#!/usr/bin/env python3
import json,time,math,schedule,urllib.request,os
from datetime import datetime,timedelta
from pathlib import Path
from coinbase.rest import RESTClient
import base64
import urllib.request as _urllib_req

CONFIG={"paper_trade":True,"taker_fee":0.006,"max_spread_pct":0.002,"starting_capital":1500.0,"risk_per_trade":0.01,"max_daily_loss_pct":0.025,"max_open_trades":3,"pairs":["BTC-USD","ETH-USD","SOL-USD","LINK-USD","XRP-USD","AVAX-USD","ADA-USD"],"rsi_oversold":35,"rsi_oversold_reset":30,"adx_threshold":25,"ema_period":200,"atr_period":14,"atr_sl_mult":2.0,"atr_tp_mult":4.0,"atr_be_mult":2.0,"atr_trail_mult":1.5,"min_volume_24h":5000000,"scan_interval_minutes":240,"candle_granularity":"FOUR_HOUR","candle_count":220,"api_key_file":"cdp_api_key.json","stop_cooldown_hours":4,"market_crash_rsi":30}

LOG_FILE=Path(__file__).parent/"bot_log.txt"
STATE_FILE=Path(__file__).parent/"state.json"
TRADES_FILE=Path(__file__).parent/"trade_explanations.json"

_stop_cooldowns={}

def now_str(): return datetime.now().strftime("%B %d, %Y  %I:%M:%S %p")
def time_str(): return datetime.now().strftime("%I:%M %p")
def date_str(): return datetime.now().date().isoformat()
def log(msg):
    line=f"[{now_str()}]  {msg}";print(line)
    open(LOG_FILE,"a").write(line+"\n")
def div(c="─"): log(c*60)
def sec(t): div("═");log(f"  {t}");div("═")

def load_state():
    if STATE_FILE.exists():
        s=json.load(open(STATE_FILE))
        s.setdefault("trade_count_today",0)
        s.setdefault("last_rsi",{})
        s.setdefault("short_capital",s.get("capital",CONFIG["starting_capital"]))
        s.setdefault("short_open_trades",{})
        s.setdefault("short_stats",{"wins":0,"losses":0,"breakevens":0,"total_trades":0,"total_pnl":0.0})
        s.setdefault("performance",{"total_trades":0,"wins":0,"losses":0,"breakevens":0,"total_pnl":0.0,"max_drawdown":0.0,"peak_capital":CONFIG["starting_capital"]})
        return s
    cap=CONFIG["starting_capital"]
    return {"capital":cap,"open_trades":{},"trade_history":[],"daily_pnl":0.0,"total_pnl":0.0,"last_reset":date_str(),"trade_count_today":0,"last_rsi":{},"short_capital":cap,"short_open_trades":{},"short_stats":{"wins":0,"losses":0,"breakevens":0,"total_trades":0,"total_pnl":0.0},"stats":{"wins":0,"losses":0,"breakevens":0,"total_trades":0},"performance":{"total_trades":0,"wins":0,"losses":0,"breakevens":0,"total_pnl":0.0,"max_drawdown":0.0,"peak_capital":cap},"last_fg":50,"last_fg_label":"Neutral","last_dominance":50,"last_funding":0.0}

def save_state(s): json.dump(s,open(STATE_FILE,"w"),indent=2,default=str)
def save_explanation(exp):
    exps=[]
    if TRADES_FILE.exists():
        try: exps=json.load(open(TRADES_FILE))
        except: exps=[]
    exps.insert(0,exp);exps=exps[:50];json.dump(exps,open(TRADES_FILE,"w"),indent=2,default=str)

def load_client():
    ak=os.environ.get("API_KEY_NAME");ap=os.environ.get("API_KEY_PRIVATE")
    if ak and ap: return RESTClient(api_key=ak,api_secret=ap)
    k=json.load(open(Path(__file__).parent/CONFIG["api_key_file"]))
    return RESTClient(api_key=k["name"],api_secret=k["privateKey"])

def fetch_url(url,timeout=5):
    try:
        with urllib.request.urlopen(url,timeout=timeout) as r: return json.loads(r.read())
    except: return None

def get_fear_greed():
    try:
        req=urllib.request.Request(
            "https://api.alternative.me/fng/?limit=1&format=json",
            headers={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        )
        with urllib.request.urlopen(req,timeout=5) as r:
            d=json.loads(r.read())
            score=int(d["data"][0]["value"])
            label=d["data"][0]["value_classification"]
            return score,label
    except Exception as e:
        log(f"  ⚠️  Fear & Greed API failed: {e}")
    return 50,"Neutral"

def get_btc_dominance():
    d=fetch_url("https://api.coingecko.com/api/v3/global")
    if d: return float(d["data"]["market_cap_percentage"]["btc"])
    return 50.0

def get_funding_rate():
    d=fetch_url("https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT")
    if d: return float(d.get("lastFundingRate",d.get("fundingRate",0)))*100
    return 0.0

def is_in_cooldown(pair):
    if pair not in _stop_cooldowns: return False,0
    elapsed=time.time()-_stop_cooldowns[pair]
    cooldown_seconds=CONFIG["stop_cooldown_hours"]*3600
    if elapsed<cooldown_seconds:
        remaining_minutes=int((cooldown_seconds-elapsed)/60)
        return True,remaining_minutes
    return False,0

def set_cooldown(pair):
    _stop_cooldowns[pair]=time.time()
    log(f"  ⏳ {pair.split('-')[0]} — 4-hour cooldown started after stop loss")

def check_market_crash(scan_signals,state):
    btc_rsi=scan_signals.get("BTC-USD",{}).get("rsi",50)
    sol_rsi=scan_signals.get("SOL-USD",{}).get("rsi",50)
    crash_threshold=CONFIG["market_crash_rsi"]
    fg=state.get("last_fg",50)
    if btc_rsi<crash_threshold and sol_rsi<crash_threshold:
        log(f"  🚨 REJECT_MARKET_CRASH — BTC RSI {btc_rsi:.0f} + SOL RSI {sol_rsi:.0f} both below {crash_threshold}")
        log(f"  🚨 Systemic crash detected — blocking all new entries this scan")
        state["market_crash_active"]=True
        state["market_crash_detail"]=f"GLOBAL FUSE BLOWN — BTC RSI {btc_rsi:.0f} + SOL RSI {sol_rsi:.0f} both below {crash_threshold}. All entries blocked."
        return True
    elif fg<20:
        log(f"  🚨 REJECT_EXTREME_FEAR — Fear & Greed {fg}/100 — market in panic. Bot in cash until F&G recovers above 20.")
        state["market_crash_active"]=True
        state["market_crash_detail"]=f"EXTREME FEAR LOCKDOWN — Fear & Greed {fg}/100. No entries until sentiment recovers above 20."
        return True
    else:
        state["market_crash_active"]=False
        state["market_crash_detail"]=""
        return False

def rsi_crossover_confirmed(pair,current_rsi,state):
    last_rsi=state.get("last_rsi",{}).get(pair,50)
    reset_level=CONFIG["rsi_oversold_reset"]
    entry_level=CONFIG["rsi_oversold"]
    crossover=last_rsi<reset_level and current_rsi>=entry_level
    if crossover:
        log(f"  ✅ RSI CROSSOVER confirmed — was {last_rsi:.1f} last scan, now {current_rsi:.1f} — bounce confirmed")
    elif current_rsi<entry_level:
        log(f"  ⏳ RSI {current_rsi:.1f} oversold but no crossover yet — last scan was {last_rsi:.1f} (need < {reset_level} then cross > {entry_level})")
    return crossover

def pos_size_short(state,atr,price):
    capital=state.get("short_capital",CONFIG["starting_capital"])
    risk=capital*CONFIG["risk_per_trade"]
    stop=atr*CONFIG["atr_sl_mult"]
    if stop<=0 or price<=0: return round(risk*4,2)
    return round(min((risk/stop)*price,capital*0.15),2)

def place_short(pair,price,state,reason="",atr=0):
    coin=pair.split("-")[0]
    usd=pos_size_short(state,atr,price)
    if usd<10:
        log(f"  📉 SHORT too small ${usd:.2f} — skip")
        return
    sl=round(price+atr*CONFIG["atr_sl_mult"],6)
    tp=round(price-atr*CONFIG["atr_tp_mult"],6)
    be=round(price-atr*CONFIG["atr_be_mult"],6)
    entry_fee=usd*CONFIG["taker_fee"]
    state["short_capital"]-=entry_fee
    log(f"  📉 PAPER SHORT — Shorting ${usd:.2f} of {coin} @ ${price:,.4f}")
    log(f"  💸 Entry fee: ${entry_fee:.2f} (0.6%)")
    log(f"     Stop: ${sl:,.6f} | Target: ${tp:,.6f} | BE triggers at: ${be:,.6f}")
    log(f"     Why: {reason}")
    state["short_open_trades"][pair]={"entry_price":price,"usd_invested":usd,"entry_time":now_str(),"lowest_price":price,"atr":atr,"stop_loss":sl,"take_profit":tp,"be_trigger":be,"at_breakeven":False,"explanation":reason,"type":"SHORT"}
    state["short_stats"]["total_trades"]+=1
    state["trade_history"].append({"time":now_str(),"pair":pair,"side":"SHORT","usd":usd,"price":price,"paper":True,"explanation":reason,"type":"SHORT"})
    save_explanation({"time":now_str(),"pair":pair,"side":"SHORT","price":price,"usd":usd,"explanation":reason,"stop_loss":sl,"take_profit":tp,"be_trigger":be})

def close_short(pair,price,state,reason=""):
    if pair not in state.get("short_open_trades",{}): return
    pos=state["short_open_trades"].pop(pair)
    coin=pair.split("-")[0]
    entry=pos["entry_price"];usd=pos["usd_invested"]
    pnl_pct=(entry-price)/entry
    proceeds=usd+(pnl_pct*usd)
    exit_fee=proceeds*CONFIG["taker_fee"]
    net_proceeds=proceeds-exit_fee
    pnl=net_proceeds-usd
    state["short_capital"]+=net_proceeds
    is_be=abs(pnl)<usd*0.005
    log(f"  📉 PAPER SHORT CLOSE — {coin} @ ${price:,.4f} | P&L: ${pnl:+.2f} ({pnl_pct*100:+.2f}%)")
    log(f"  💸 Exit fee: ${exit_fee:.2f} (0.6%)")
    if pnl>0:
        state["short_stats"]["wins"]+=1;state["short_stats"]["total_pnl"]+=pnl
        log(f"  🏆 SHORT PROFIT: ${pnl:+.2f}")
    elif is_be:
        state["short_stats"]["breakevens"]+=1
        log(f"  ↔️  SHORT BREAKEVEN: ${pnl:+.2f}")
    else:
        state["short_stats"]["losses"]+=1;state["short_stats"]["total_pnl"]+=pnl
        log(f"  📉 SHORT LOSS: ${pnl:+.2f}")
    state["trade_history"].append({"time":now_str(),"pair":pair,"side":"SHORT_CLOSE","usd":usd,"price":price,"paper":True,"explanation":reason,"type":"SHORT","pnl":round(pnl,2)})
    save_explanation({"time":now_str(),"pair":pair,"side":"SHORT_CLOSE","price":price,"pnl":pnl,"explanation":f"Short closed {reason}. Entry ${entry:,.4f} → Exit ${price:,.4f}. P&L ${pnl:+.2f}"})

def check_short_exits(client,state):
    shorts=state.get("short_open_trades",{})
    if not shorts: return
    log("  Checking short positions...")
    for pair,pos in list(shorts.items()):
        px=get_price(client,pair)
        if px==0: continue
        entry=pos["entry_price"];atr=pos.get("atr",entry*0.02)
        sl=pos.get("stop_loss",entry+atr*CONFIG["atr_sl_mult"])
        tp=pos.get("take_profit",entry-atr*CONFIG["atr_tp_mult"])
        be_trig=pos.get("be_trigger",entry-atr*CONFIG["atr_be_mult"])
        at_be=pos.get("at_breakeven",False)
        ch=(entry-px)/entry*100
        if px<pos.get("lowest_price",px):
            pos["lowest_price"]=px;state["short_open_trades"][pair]=pos
        lowest=pos.get("lowest_price",px)
        if not at_be and px<=be_trig:
            pos["stop_loss"]=entry;pos["at_breakeven"]=True
            state["short_open_trades"][pair]=pos;sl=entry
            log(f"  ↔️  SHORT BREAKEVEN — {pair.split('-')[0]} hit -2x ATR. Stop → entry ${entry:,.6f}")
        if at_be:
            trail=lowest+atr*CONFIG["atr_trail_mult"]
            if trail<pos.get("stop_loss",sl):
                pos["stop_loss"]=trail;state["short_open_trades"][pair]=pos;sl=trail
        be_str="✅ BE active" if at_be else f"BE at ${be_trig:,.4f}"
        log(f"  📉 SHORT {pair.split('-')[0]}: ${px:,.4f} | {ch:+.1f}% favour | SL ${sl:,.4f} | TP ${tp:,.4f} | {be_str}")
        if px>=sl:
            log(f"  🛑 SHORT STOP LOSS — {pair.split('-')[0]}")
            close_short(pair,px,state,reason="SHORT_STOP_LOSS")
        elif px<=tp:
            log(f"  🎯 SHORT TAKE PROFIT — {pair.split('-')[0]} +{ch:.1f}%")
            close_short(pair,px,state,reason="SHORT_TAKE_PROFIT")

def generate_market_summary(state,scan_results,fg,fgl,dominance):
    api_key=os.environ.get("ANTHROPIC_API_KEY","")
    if not api_key:
        log("  ⚠️  No ANTHROPIC_API_KEY — skipping AI summary")
        return None
    fg_val=state.get("last_fg",fg)
    cap=state.get("capital",1500)
    open_trades=state.get("open_trades",{})
    closest=[];waiting=[]
    for pair,data in scan_results.items():
        coin=pair.split("-")[0]
        rsi=data.get("rsi",50);above_ema=data.get("above_ema",False)
        trending=data.get("adx",0)>=25;oversold=rsi<35
        sig=data.get("signal","HOLD");conditions_met=sum([oversold,above_ema,trending])
        if sig=="BUY":
            closest.append(f"{coin} — ALL 3 SIGNALS FIRING — BUY triggered")
        elif conditions_met==2:
            missing=[]
            if not oversold: missing.append(f"RSI {rsi:.0f} needs to drop to 35")
            if not above_ema: missing.append("price needs to rise above 200 EMA")
            if not trending: missing.append(f"ADX {data.get('adx',0):.0f} needs to reach 25")
            closest.append(f"{coin} — 2 of 3 conditions met, missing: {', '.join(missing)}")
        elif conditions_met==1:
            waiting.append(f"{coin} — only 1 condition met, RSI {rsi:.0f}, {'above' if above_ema else 'below'} 200 EMA, ADX {data.get('adx',0):.0f}")
        else:
            waiting.append(f"{coin} — no conditions met, RSI {rsi:.0f}, {'above' if above_ema else 'below'} 200 EMA, ADX {data.get('adx',0):.0f}")
    if open_trades: open_summary=f"Currently holding {len(open_trades)} long position(s)."
    else: open_summary="No open long positions — holding cash."
    short_open=state.get("short_open_trades",{})
    if short_open: open_summary+=f" {len(short_open)} paper short(s) active."
    prompt=f"""You are a sharp, direct trading analyst giving a quick market update for a crypto bot. Write exactly like this example — conversational, clear, no jargon, like explaining to a smart friend:
"Right now the market is in a weird spot — Extreme Fear (12/100), BTC hovering right around its 200 EMA, and most coins showing neutral RSI in the 40-50 range. None of them are oversold enough to trigger RSI below 35 while also being above their 200 EMA with ADX trending. The bot is doing exactly what it should — being patient and waiting for a clean setup. ETH is the closest — it has the 200 EMA and ADX conditions met. It just needs RSI to drop to 35."
Now write a similar update using this current data:
Fear & Greed: {fg_val}/100 — {fgl}
BTC Dominance: {dominance:.1f}%
Capital: ${cap:.2f}
{open_summary}
Coin status:
{chr(10).join(closest) if closest else "No coins close to triggering"}
{chr(10).join(waiting)}
Rules:
- 2-4 sentences max
- Mention Fear & Greed naturally
- Call out the closest coin by name and exactly what it needs
- End with what to watch for
- Sound like a real person, not a robot
- No bullet points, just flowing text
"""
    try:
        payload=json.dumps({"model":"claude-sonnet-4-20250514","max_tokens":300,"messages":[{"role":"user","content":prompt}]}).encode()
        req=urllib.request.Request("https://api.anthropic.com/v1/messages",data=payload,headers={"content-type":"application/json","anthropic-version":"2023-06-01","x-api-key":api_key})
        with urllib.request.urlopen(req,timeout=15) as r:
            resp=json.loads(r.read())
            summary=resp["content"][0]["text"].strip()
            json.dump({"summary":summary,"time":now_str()},open(Path(__file__).parent/"market_summary.json","w"))
            log(f"  📝 Market summary updated")
            return summary
    except Exception as e:
        log(f"  ⚠️  Market summary API error: {e}")
        return None

def get_candles(client,pair):
    try:
        end=int(time.time());start=end-14400*CONFIG["candle_count"]
        r=client.get_candles(product_id=pair,start=str(start),end=str(end),granularity=CONFIG["candle_granularity"])
        return sorted(r.candles if hasattr(r,"candles") else [],key=lambda c:int(c.start))
    except Exception as e: log(f"  No candles {pair}: {e}");return []

def get_price(client,pair):
    try:
        r=client.get_best_bid_ask(product_ids=[pair])
        for p in r.pricebooks:
            if p.product_id==pair:
                b=float(p.bids[0].price) if p.bids else 0;a=float(p.asks[0].price) if p.asks else 0;return(b+a)/2
    except: pass
    return 0.0

def calc_ema(prices,n):
    if len(prices)<n: return prices[-1] if prices else 0.0
    k=2/(n+1);e=sum(prices[:n])/n
    for v in prices[n:]: e=v*k+e*(1-k)
    return e

def calc_rsi(closes,n=14):
    if len(closes)<n+1: return 50.0
    g=[abs(closes[i]-closes[i-1]) for i in range(-n,0) if closes[i]>closes[i-1]]
    l=[abs(closes[i]-closes[i-1]) for i in range(-n,0) if closes[i]<=closes[i-1]]
    ag=sum(g)/n if g else 0;al=sum(l)/n if l else 1e-9
    return 100-(100/(1+ag/al))

def calc_adx(highs,lows,closes,n=14):
    if len(closes)<n*2: return 20.0
    try:
        trs,pdms,ndms=[],[],[]
        for i in range(1,len(closes)):
            h,l,pc=highs[i],lows[i],closes[i-1]
            trs.append(max(h-l,abs(h-pc),abs(l-pc)))
            pdms.append(max(h-highs[i-1],0) if(h-highs[i-1])>(lows[i-1]-l) else 0)
            ndms.append(max(lows[i-1]-l,0) if(lows[i-1]-l)>(h-highs[i-1]) else 0)
        atr=sum(trs[-n:])/n
        if atr==0: return 20.0
        pdi=(sum(pdms[-n:])/n)/atr*100;ndi=(sum(ndms[-n:])/n)/atr*100
        return abs(pdi-ndi)/(pdi+ndi)*100 if(pdi+ndi)>0 else 0
    except: return 20.0

def calc_atr(highs,lows,closes,n=14):
    if len(closes)<n+1: return closes[-1]*0.02 if closes else 1.0
    trs=[max(highs[i]-lows[i],abs(highs[i]-closes[i-1]),abs(lows[i]-closes[i-1])) for i in range(-n,0)]
    return sum(trs)/n

def calc_volume_24h(closes,volumes):
    if len(closes)<6 or len(volumes)<6: return 0.0
    return sum(closes[-6+i]*volumes[-6+i] for i in range(6))

def analyze(pair,closes,highs,lows,volumes):
    if len(closes)<210: return{"direction":"HOLD","reason":"Not enough data","indicators":{}}
    px=closes[-1];rv=calc_rsi(closes);e200=calc_ema(closes,CONFIG["ema_period"])
    adxv=calc_adx(highs,lows,closes);atrv=calc_atr(highs,lows,closes)
    vol24=calc_volume_24h(closes,volumes)
    above=px>e200*1.001;trending=adxv>=CONFIG["adx_threshold"]
    oversold=rv<CONFIG["rsi_oversold"];vol_ok=vol24>=CONFIG["min_volume_24h"]
    sl=round(px-atrv*CONFIG["atr_sl_mult"],6);tp=round(px+atrv*CONFIG["atr_tp_mult"],6)
    be=round(px+atrv*CONFIG["atr_be_mult"],6)
    ind={"price":px,"rsi":rv,"ema200":e200,"adx":adxv,"atr":atrv,"vol24":vol24,"above_ema":above,"trending":trending,"oversold":oversold,"vol_ok":vol_ok,"sl":sl,"tp":tp,"be_trigger":be}
    if oversold and above and trending and vol_ok:
        reason=f"RSI {rv:.0f} oversold — bounce likely. Price ${px:,.4f} above 200 EMA ${e200:,.4f} — uptrend intact. ADX {adxv:.0f} — trending. Vol ${vol24/1e6:.1f}M. SL ${sl:,.6f} | TP ${tp:,.6f} | BE at ${be:,.6f}."
        return{"direction":"BUY","reason":reason,"indicators":ind}
    missing=[]
    if not oversold: missing.append(f"RSI {rv:.0f} not oversold (need < {CONFIG['rsi_oversold']})")
    if not above: missing.append(f"Price below 200 EMA ${e200:,.4f}")
    if not trending: missing.append(f"ADX {adxv:.0f} ranging (need > {CONFIG['adx_threshold']})")
    if not vol_ok: missing.append(f"Vol ${vol24/1e6:.1f}M too low (need > $5M)")
    return{"direction":"HOLD","reason":"Waiting: "+" | ".join(missing),"indicators":ind}

def pos_size(state,atr,price):
    capital=state["capital"];risk=capital*CONFIG["risk_per_trade"]
    stop=atr*CONFIG["atr_sl_mult"]
    if stop<=0 or price<=0: return round(risk*4,2)
    return round(min((risk/stop)*price,capital*0.15),2)

def place_order(client,pair,side,usd,price,state,reason="",atr=0):
    mode="PAPER TRADE" if CONFIG["paper_trade"] else "LIVE TRADE"
    coin=pair.split("-")[0]
    if side=="BUY":
        sl=price-atr*CONFIG["atr_sl_mult"];tp=price+atr*CONFIG["atr_tp_mult"];be=price+atr*CONFIG["atr_be_mult"]
        entry_fee=usd*CONFIG["taker_fee"]
        state["capital"]-=entry_fee
        log(f"  💰 {mode} — Buying ${usd:.2f} of {coin} @ ${price:,.4f}")
        log(f"  💸 Entry fee: ${entry_fee:.2f} (0.6%)")
        log(f"     Stop: ${sl:,.6f} | Target: ${tp:,.6f} | BE triggers at: ${be:,.6f}")
        log(f"     Why: {reason}")
    else:
        if pair in state["open_trades"]:
            entry=state["open_trades"][pair].get("entry_price",price);pct=(price-entry)/entry*100
            log(f"  💸 {mode} — Selling {coin} @ ${price:,.4f} ({'gained' if pct>0 else 'lost'} {abs(pct):.2f}%)")
    if not CONFIG["paper_trade"]:
        try:
            import uuid;cid=str(uuid.uuid4())
            if side=="BUY": r=client.market_order_buy(client_order_id=cid,product_id=pair,quote_size=str(usd))
            else: r=client.market_order_sell(client_order_id=cid,product_id=pair,base_size=str(round(usd/price,8)))
            log("  ✅ Confirmed!" if getattr(r,"success",True) else "  ⚠️  Check Coinbase")
        except Exception as e: log(f"  ❌ {e}");return False
    state["trade_history"].append({"time":now_str(),"pair":pair,"side":side,"usd":usd,"price":price,"paper":CONFIG["paper_trade"],"explanation":reason,"type":"LONG"})
    state["stats"]["total_trades"]+=1;state["performance"]["total_trades"]+=1
    if side=="BUY":
        sl=price-atr*CONFIG["atr_sl_mult"];tp=price+atr*CONFIG["atr_tp_mult"];be=price+atr*CONFIG["atr_be_mult"]
        state["open_trades"][pair]={"entry_price":price,"usd_invested":usd,"entry_time":now_str(),"highest_price":price,"atr":atr,"stop_loss":sl,"take_profit":tp,"be_trigger":be,"at_breakeven":False,"explanation":reason,"type":"LONG"}
        state["capital"]-=usd;state["trade_count_today"]=state.get("trade_count_today",0)+1
        save_explanation({"time":now_str(),"pair":pair,"side":"BUY","price":price,"usd":usd,"explanation":reason,"stop_loss":sl,"take_profit":tp,"be_trigger":be})
    elif pair in state["open_trades"]:
        e=state["open_trades"].pop(pair);proceeds=e["usd_invested"]+(price-e["entry_price"])/e["entry_price"]*e["usd_invested"];exit_fee=proceeds*CONFIG["taker_fee"];net_proceeds=proceeds-exit_fee;pnl=net_proceeds-e["usd_invested"]
        state["capital"]+=net_proceeds;log(f"  💸 Exit fee: ${exit_fee:.2f} (0.6%)");state["daily_pnl"]+=pnl;state["total_pnl"]+=pnl;state["performance"]["total_pnl"]+=pnl
        is_be=abs(pnl)<e["usd_invested"]*0.005
        if pnl>0: state["stats"]["wins"]+=1;state["performance"]["wins"]+=1;log(f"  🏆 Profit: ${pnl:+.2f}")
        elif is_be: state["stats"]["breakevens"]+=1;state["performance"]["breakevens"]+=1;log(f"  ↔️  Breakeven: ${pnl:+.2f}")
        else: state["stats"]["losses"]+=1;state["performance"]["losses"]+=1;log(f"  📉 Loss: ${pnl:+.2f}")
        if "STOP" in reason or "WEBSOCKET_STOP" in reason: set_cooldown(pair)
        peak=state["performance"].get("peak_capital",CONFIG["starting_capital"])
        if state["capital"]>peak: state["performance"]["peak_capital"]=state["capital"]
        else:
            dd=(peak-state["capital"])/peak*100
            if dd>state["performance"].get("max_drawdown",0): state["performance"]["max_drawdown"]=dd
        save_explanation({"time":now_str(),"pair":pair,"side":"SELL","price":price,"pnl":pnl,"explanation":f"Exited {'profit' if pnl>0 else 'breakeven' if is_be else 'loss'} ${abs(pnl):.2f}. Entry ${e['entry_price']:,.4f} → Exit ${price:,.4f}."})
    return True

def risk_ok(state):
    today=date_str()
    if state.get("last_reset")!=today:
        state["daily_pnl"]=0.0;state["last_reset"]=today;state["trade_count_today"]=0;log("  🔄 Daily reset")
    if state["daily_pnl"]<-CONFIG["starting_capital"]*CONFIG["max_daily_loss_pct"]:
        log("  🛑 Daily loss limit");return False
    if len(state["open_trades"])>=CONFIG["max_open_trades"]:
        log(f"  ⏸️  Max {CONFIG['max_open_trades']} positions");return False
    return True

def check_exits(client,state):
    if not state["open_trades"]: return
    log("  ── HOURLY EXIT CHECK ──────────────────────────────────────")
    for pair,pos in list(state["open_trades"].items()):
        px=get_price(client,pair)
        if px==0: continue
        entry=pos["entry_price"];atr=pos.get("atr",entry*0.02)
        sl=pos.get("stop_loss",entry-atr*CONFIG["atr_sl_mult"])
        tp=pos.get("take_profit",entry+atr*CONFIG["atr_tp_mult"])
        be_trig=pos.get("be_trigger",entry+atr*CONFIG["atr_be_mult"])
        at_be=pos.get("at_breakeven",False);ch=(px-entry)/entry*100
        if px>pos.get("highest_price",px): pos["highest_price"]=px;state["open_trades"][pair]=pos
        highest=pos.get("highest_price",px)
        if not at_be and px>=be_trig:
            pos["stop_loss"]=entry;pos["at_breakeven"]=True;state["open_trades"][pair]=pos;sl=entry
            log(f"  ↔️  BREAKEVEN — {pair.split('-')[0]} hit +2x ATR. Stop → entry ${entry:,.6f}")
        if at_be:
            trail=highest-atr*CONFIG["atr_trail_mult"]
            if trail>pos.get("stop_loss",sl): pos["stop_loss"]=trail;state["open_trades"][pair]=pos;sl=trail
        be_str="✅ BE active" if at_be else f"BE at ${be_trig:,.4f}"
        log(f"  📊 {pair.split('-')[0]}: ${px:,.4f} | {ch:+.1f}% | SL ${sl:,.4f} | TP ${tp:,.4f} | {be_str}")
        try:
            entry_dt=datetime.strptime(pos.get("entry_time",""),"%B %d, %Y  %I:%M:%S %p")
            hours_open=(datetime.now()-entry_dt).total_seconds()/3600
            movement_atr=abs(px-entry)/atr if atr>0 else 0
            if hours_open>=24 and not at_be:
                log(f"  ⚠️  STALE TRADE WARNING — {pair.split('-')[0]} open {hours_open:.1f}h | {movement_atr:.2f}x ATR movement | no breakeven yet")
            elif hours_open>=16 and not at_be:
                log(f"  🕐 TRADE WATCH — {pair.split('-')[0]} open {hours_open:.1f}h | {movement_atr:.2f}x ATR movement | approaching stale zone")
        except:
            pass
        if px<=sl:
            log(f"  {'↔️  BREAKEVEN STOP' if at_be else '🛑 STOP LOSS'} — {pair.split('-')[0]}")
            place_order(client,pair,"SELL",pos["usd_invested"],px,state,reason="STOP_LOSS — hourly check",atr=atr)
        elif px>=tp:
            log(f"  🎯 TAKE PROFIT — {pair.split('-')[0]} +{ch:.1f}%")
            place_order(client,pair,"SELL",pos["usd_invested"],px,state,atr=atr)
    save_state(state)

def hourly_management(client,state):
    log(f"  ⏱️  HOURLY MANAGEMENT — {time_str()}")
    check_exits(client,state)
    check_short_exits(client,state)

def scan(client,state):
    _scan_signals={}
    sec(f"4H SCAN — {time_str()}")
    fg,fgl=get_fear_greed();dominance=get_btc_dominance();funding=get_funding_rate()
    state["last_fg"]=fg;state["last_fg_label"]=fgl;state["last_dominance"]=dominance;state["last_funding"]=funding
    log(f"  Fear & Greed: {fg}/100 — {fgl} | BTC Dom: {dominance:.1f}% | Funding: {funding:.4f}%");log("")
    check_exits(client,state)
    check_short_exits(client,state)
    try:
        sf=Path(__file__).parent/"summary.json"
        if sf.exists():
            sd=json.loads(sf.read_bytes().decode('utf-8',errors='replace'))
            state["ai_summary"]=sd.get("summary","")
            state["ai_summary_time"]=sd.get("time","")
            state["ai_signals"]=sd.get("signals",{})
    except: pass
    if not risk_ok(state): save_state(state);return

    for pair in CONFIG["pairs"]:
        if pair in state["open_trades"]: continue
        candles=get_candles(client,pair)
        if not candles: continue
        closes=[float(c.close) for c in candles];highs=[float(c.high) for c in candles]
        lows=[float(c.low) for c in candles];volumes=[float(c.volume) for c in candles]
        signal=analyze(pair,closes,highs,lows,volumes)
        _scan_signals[pair]=signal["indicators"]

    market_crash=check_market_crash(_scan_signals,state)

    for pair in CONFIG["pairs"]:
        coin=pair.split("-")[0];div();log(f"  {coin}");div()
        if pair in state["open_trades"]: log("  Already holding long — skipping");continue
        if pair not in _scan_signals: log("  No data");continue
        ind=_scan_signals[pair]
        px=ind.get("price",0);rv=ind.get("rsi",50);e200=ind.get("ema200",0)
        adxv=ind.get("adx",0);atrv=ind.get("atr",0);vol24=ind.get("vol24",0)
        try:
            bbo=client.get_best_bid_ask(product_ids=[pair])
            bids=bbo.pricebooks[0].bids;asks=bbo.pricebooks[0].asks
            if bids and asks:
                bid=float(bids[0].price);ask=float(asks[0].price);mid=(bid+ask)/2
                spread_pct=(ask-bid)/mid
                if spread_pct>CONFIG["max_spread_pct"]:
                    log(f"  ⚠️  Spread {spread_pct*100:.3f}% — too wide, skip");continue
        except: pass
        log(f"  Price:   ${px:,.4f}")
        log(f"  RSI:     {rv:.1f}  {'✅ oversold — signal firing' if rv<35 else '— neutral' if rv<65 else '🔴 overbought'}")
        log(f"  200 EMA: ${e200:,.4f}  {'✅ price above — uptrend' if ind.get('above_ema') else '⚠️  price below — skip'}")
        log(f"  ADX:     {adxv:.1f}  {'✅ trending' if ind.get('trending') else '⚠️  ranging — skip'}")
        log(f"  Vol 24h: ${vol24/1e6:.1f}M  {'✅ sufficient' if vol24>=CONFIG['min_volume_24h'] else '⚠️  too low — skip'}")
        log(f"  ATR:     ${atrv:,.6f} | SL: ${ind.get('sl',0):,.4f} | TP: ${ind.get('tp',0):,.4f}")
        oversold=ind.get("oversold",False);above=ind.get("above_ema",False)
        trending=ind.get("trending",False);vol_ok=ind.get("vol_ok",True)
        if oversold and above and trending and vol_ok and px>0:
            if market_crash: log(f"  🚫 REJECT_MARKET_CRASH — skipping {coin} entry");continue
            in_cooldown,remaining=is_in_cooldown(pair)
            if in_cooldown: log(f"  ⏳ COOLDOWN — {coin} blocked for {remaining} more minutes");continue
            if not rsi_crossover_confirmed(pair,rv,state): log(f"  ⏳ RSI CROSSOVER PENDING");continue
            reason=f"RSI {rv:.0f} oversold crossover on 4H — bounce confirmed. Price ${px:,.4f} above 200 EMA ${e200:,.4f}. ADX {adxv:.0f} trending. Vol ${vol24/1e6:.1f}M. SL ${ind.get('sl',0):,.6f} | TP ${ind.get('tp',0):,.6f} | BE at ${ind.get('be_trigger',0):,.6f}."
            usd=pos_size(state,atrv,px)
            if usd>=10: place_order(client,pair,"BUY",usd,px,state,reason,atrv)
            else: log(f"  Too small ${usd:.2f}")
        else:
            missing=[]
            if not oversold: missing.append(f"RSI {rv:.0f} not oversold")
            if not above: missing.append(f"Price below 200 EMA")
            if not trending: missing.append(f"ADX {adxv:.0f} ranging")
            if not vol_ok: missing.append(f"Vol too low")
            log(f"  → Waiting: {' | '.join(missing)}")

    SHORT_PAIRS=list(CONFIG["pairs"])+["DOGE-USD","DOT-USD","SUI-USD","LTC-USD","TAO-USD","FET-USD"]
    for pair in SHORT_PAIRS:
        if pair not in _scan_signals:
            try:
                candles=get_candles(client,pair)
                if not candles or len(candles)<210: continue
                closes=[float(c.close) for c in candles];highs=[float(c.high) for c in candles]
                lows=[float(c.low) for c in candles];volumes=[float(c.volume) for c in candles]
                px2=closes[-1];rv2=calc_rsi(closes);e200_2=calc_ema(closes,CONFIG["ema_period"])
                adxv2=calc_adx(highs,lows,closes);atrv2=calc_atr(highs,lows,closes)
                vol24_2=calc_volume_24h(closes,volumes)
                above2=px2>e200_2*1.001;trending2=adxv2>=CONFIG["adx_threshold"]
                vol_ok2=vol24_2>=CONFIG["min_volume_24h"]
                _scan_signals[pair]={"price":px2,"rsi":rv2,"ema200":e200_2,"adx":adxv2,"atr":atrv2,"vol24":vol24_2,"above_ema":above2,"trending":trending2,"oversold":rv2<CONFIG["rsi_oversold"],"vol_ok":vol_ok2}
            except: continue
        if pair not in _scan_signals: continue
        s=_scan_signals[pair]
        px=s.get("price",0);rsi=s.get("rsi",50);above_ema=s.get("above_ema",False)
        adx=s.get("adx",0);atr=s.get("atr",0)
        if px==0 or atr==0: continue
        coin=pair.split("-")[0]
        last_rsi_val=state.get("last_rsi",{}).get(pair,50)
        overbought_reset=last_rsi_val>70
        overbought_cross=rsi<=65
        short_signal=overbought_reset and overbought_cross and (not above_ema) and adx>=CONFIG["adx_threshold"]
        if pair in state.get("short_open_trades",{}):
            log(f"  📉 SHORT {coin}: already open — skipping")
        elif short_signal:
            reason=f"RSI hook {last_rsi_val:.0f}→{rsi:.0f} on 4H — overbought exhausted. Price ${px:,.4f} below 200 EMA — downtrend. ADX {adx:.0f} trending. SL ${round(px+atr*2,4)} | TP ${round(px-atr*4,4)}."
            log(f"  📉 SHORT SIGNAL — {coin} @ ${px:,.4f} | RSI {last_rsi_val:.0f}→{rsi:.0f} | ADX {adx:.0f}")
            place_short(pair,px,state,reason=reason,atr=atr)
        else:
            missing_s=[]
            if not overbought_reset: missing_s.append(f"RSI {rsi:.0f} needs >70 first (was {last_rsi_val:.0f})")
            elif not overbought_cross: missing_s.append(f"RSI {rsi:.0f} needs to cross <65")
            if above_ema: missing_s.append("above 200 EMA")
            if adx<CONFIG["adx_threshold"]: missing_s.append(f"ADX {adx:.0f} weak")
            log(f"  📉 SHORT {coin}: Waiting — {' | '.join(missing_s)}")

    if "last_rsi" not in state: state["last_rsi"]={}
    for pair,ind in _scan_signals.items():
        state["last_rsi"][pair]=ind.get("rsi",50)

    st=state["stats"];perf=state["performance"];t=st["total_trades"];wr=(st["wins"]/t*100) if t else 0
    gr=(state["capital"]-CONFIG["starting_capital"])/CONFIG["starting_capital"]*100
    short_cap=state.get("short_capital",CONFIG["starting_capital"])
    short_gr=(short_cap-CONFIG["starting_capital"])/CONFIG["starting_capital"]*100
    ss=state.get("short_stats",{})
    sec(f"PORTFOLIO — {'PAPER' if CONFIG['paper_trade'] else 'LIVE'}")
    log(f"  LONG Capital: ${state['capital']:,.2f} ({'▲' if gr>=0 else '▼'}{abs(gr):.1f}%) | P/L: ${state['total_pnl']:+,.2f}")
    log(f"  SHORT Capital: ${short_cap:,.2f} ({'▲' if short_gr>=0 else '▼'}{abs(short_gr):.1f}%) | Shorts: {ss.get('total_trades',0)} | W:{ss.get('wins',0)} L:{ss.get('losses',0)}")
    log(f"  Long Trades: {t} | Wins: {st['wins']} | Losses: {st['losses']} | WR: {wr:.1f}%")
    log(f"  Drawdown: {perf.get('max_drawdown',0):.1f}%")
    if state["open_trades"]:
        for p2,pos in state["open_trades"].items():
            be_s="✅ BE active" if pos.get("at_breakeven") else f"BE at ${pos.get('be_trigger',0):,.4f}"
            log(f"  • LONG {p2} ${pos['usd_invested']:.2f} @ ${pos['entry_price']:,.4f} | SL ${pos.get('stop_loss',0):,.4f} | TP ${pos.get('take_profit',0):,.4f} | {be_s}")
    if state.get("short_open_trades"):
        for p2,pos in state["short_open_trades"].items():
            be_s="✅ BE active" if pos.get("at_breakeven") else f"BE at ${pos.get('be_trigger',0):,.4f}"
            log(f"  • SHORT {p2} ${pos['usd_invested']:.2f} @ ${pos['entry_price']:,.4f} | SL ${pos.get('stop_loss',0):,.4f} | TP ${pos.get('take_profit',0):,.4f} | {be_s}")
    if not state["open_trades"] and not state.get("short_open_trades"):
        log("  Holding cash — waiting for clean 4H setups")
    div("═")

    scan_results_for_summary={}
    for pair in CONFIG["pairs"]:
        if pair in state.get("open_trades",{}): continue
        if pair in _scan_signals:
            s=_scan_signals[pair]
            scan_results_for_summary[pair]={"rsi":s.get("rsi",50),"above_ema":s.get("above_ema",False),"adx":s.get("adx",0),"signal":"BUY" if (s.get("oversold") and s.get("above_ema") and s.get("trending") and s.get("vol_ok")) else "HOLD"}

    try:
        import csv
        audit_file=Path(__file__).parent/"strategy_audit.csv"
        rejected_file=Path(__file__).parent/"rejected_signals.csv"
        equity_file=Path(__file__).parent/"equity_curve.csv"
        equity_exists=equity_file.exists()
        with open(equity_file,"a",newline="") as f:
            writer=csv.writer(f)
            if not equity_exists:
                writer.writerow(["Timestamp","Date","Long_Capital","Short_Capital","Total_PnL","Daily_PnL","Open_Longs","Open_Shorts","Win_Rate","Total_Trades","Wins","Losses","Max_Drawdown"])
            st2=state.get("stats",{});t2=st2.get("total_trades",0)
            wr2=round(st2.get("wins",0)/t2*100,1) if t2 else 0
            writer.writerow([now_str(),date_str(),round(state.get("capital",1500),2),round(state.get("short_capital",1500),2),round(state.get("total_pnl",0),2),round(state.get("daily_pnl",0),2),len(state.get("open_trades",{})),len(state.get("short_open_trades",{})),wr2,t2,st2.get("wins",0),st2.get("losses",0),round(state.get("performance",{}).get("max_drawdown",0),2)])
        rejected_exists=rejected_file.exists()
        for pair,s in _scan_signals.items():
            px=s.get("price",0);rv=s.get("rsi",50);adxv=s.get("adx",0)
            above=s.get("above_ema",False);atrv=s.get("atr",0);e200=s.get("ema200",0)
            oversold=rv<CONFIG["rsi_oversold"];vol_ok=s.get("vol_ok",True);trending=adxv>=CONFIG["adx_threshold"]
            if oversold and above and not trending and vol_ok and px>0:
                with open(rejected_file,"a",newline="") as f:
                    writer=csv.writer(f)
                    if not rejected_exists:
                        writer.writerow(["Timestamp","Pair","RSI","ADX","ADX_Threshold","Price","EMA200","ATR","Reason","Potential_SL","Potential_TP"])
                        rejected_exists=True
                    writer.writerow([now_str(),pair,round(rv,1),round(adxv,1),CONFIG["adx_threshold"],px,round(e200,4),round(atrv,6),f"ADX {adxv:.0f} below threshold",round(px-atrv*CONFIG["atr_sl_mult"],6),round(px+atrv*CONFIG["atr_tp_mult"],6)])
        audit_exists=audit_file.exists()
        history=state.get("trade_history",[])
        if len(history)>=2:
            last=history[-1]
            if last.get("side")=="SELL":
                for i in range(len(history)-2,-1,-1):
                    prev=history[i]
                    if prev.get("pair")==last.get("pair") and prev.get("side")=="BUY":
                        entry_px=float(prev.get("price",0));exit_px=float(last.get("price",0))
                        usd=float(prev.get("usd",0))
                        pnl=(exit_px-entry_px)/entry_px*usd if entry_px>0 else 0
                        pnl_pct=(exit_px-entry_px)/entry_px*100 if entry_px>0 else 0
                        already_logged=False
                        if audit_file.exists():
                            with open(audit_file,"r") as rf:
                                if last.get("time","") in rf.read(): already_logged=True
                        if not already_logged:
                            with open(audit_file,"a",newline="") as f:
                                writer=csv.writer(f)
                                if not audit_exists:
                                    writer.writerow(["Trade_ID","Type","Pair","Entry_Time","Exit_Time","Entry_Price","Exit_Price","Stop_Loss","Take_Profit","Position_Size_USD","PnL_USD","PnL_Pct","Outcome","Entry_Reason","MFE_Pct","MAE_Pct"])
                                    audit_exists=True
                                exps=[]
                                if TRADES_FILE.exists():
                                    try: exps=json.load(open(TRADES_FILE))
                                    except: pass
                                exp_data=next((e for e in exps if e.get("pair")==prev.get("pair") and e.get("side")=="BUY"),{})
                                if pnl>0: outcome="TAKE PROFIT"
                                elif abs(pnl)<usd*0.005: outcome="BREAKEVEN"
                                else: outcome="STOP LOSS"
                                import hashlib
                                trade_id=hashlib.md5(f"{prev.get('pair')}{prev.get('time')}".encode()).hexdigest()[:8].upper()
                                writer.writerow([trade_id,"LONG",prev.get("pair",""),prev.get("time",""),last.get("time",""),entry_px,exit_px,exp_data.get("stop_loss",""),exp_data.get("take_profit",""),usd,round(pnl,2),round(pnl_pct,2),outcome,prev.get("explanation",""),"",""])
                        break
        perf_file=Path(__file__).parent/"symbol_performance.csv"
        coin_stats={}
        if audit_file.exists():
            with open(audit_file,"r",newline="") as f:
                reader=csv.DictReader(f)
                for row in reader:
                    pair=row.get("Pair","")
                    if pair not in coin_stats: coin_stats[pair]={"trades":0,"wins":0,"losses":0,"total_pnl":0.0}
                    coin_stats[pair]["trades"]+=1
                    pnl_val=float(row.get("PnL_USD",0) or 0)
                    coin_stats[pair]["total_pnl"]+=pnl_val
                    if row.get("Outcome")=="TAKE PROFIT": coin_stats[pair]["wins"]+=1
                    elif row.get("Outcome")=="STOP LOSS": coin_stats[pair]["losses"]+=1
        if coin_stats:
            with open(perf_file,"w",newline="") as f:
                writer=csv.writer(f)
                writer.writerow(["Pair","Total_Trades","Wins","Losses","Win_Rate_Pct","Total_PnL_USD","Avg_PnL_Per_Trade"])
                for pair,cs in coin_stats.items():
                    wr3=round(cs["wins"]/cs["trades"]*100,1) if cs["trades"] else 0
                    avg=round(cs["total_pnl"]/cs["trades"],2) if cs["trades"] else 0
                    writer.writerow([pair,cs["trades"],cs["wins"],cs["losses"],wr3,round(cs["total_pnl"],2),avg])
    except Exception as ae:
        log(f"  Audit logger error: {ae}")

    try:
        import csv
        SHADOW_LONG_PAIRS=["DOGE-USD","DOT-USD","SUI-USD","LTC-USD","TAO-USD","FET-USD"]
        shadow_long_file=Path(__file__).parent/"shadow_longs.csv"
        shadow_long_state_file=Path(__file__).parent/"shadow_long_state.json"
        shadow_long_state={}
        if shadow_long_state_file.exists():
            try: shadow_long_state=json.load(open(shadow_long_state_file))
            except: shadow_long_state={}
        for pair in SHADOW_LONG_PAIRS:
            coin=pair.split("-")[0]
            try:
                if pair not in _scan_signals:
                    candles=get_candles(client,pair)
                    if not candles or len(candles)<210: continue
                    closes=[float(c.close) for c in candles];highs=[float(c.high) for c in candles]
                    lows=[float(c.low) for c in candles];volumes=[float(c.volume) for c in candles]
                    px=closes[-1];rv=calc_rsi(closes);e200=calc_ema(closes,CONFIG["ema_period"])
                    adxv=calc_adx(highs,lows,closes);atrv=calc_atr(highs,lows,closes)
                    vol24=calc_volume_24h(closes,volumes)
                    above=px>e200*1.001;trending=adxv>=CONFIG["adx_threshold"]
                    oversold=rv<CONFIG["rsi_oversold"];vol_ok=vol24>=CONFIG["min_volume_24h"]
                    _scan_signals[pair]={"price":px,"rsi":rv,"ema200":e200,"adx":adxv,"atr":atrv,"vol24":vol24,"above_ema":above,"trending":trending,"oversold":oversold,"vol_ok":vol_ok}
                s=_scan_signals[pair]
                px=s["price"];rv=s["rsi"];e200=s["ema200"];adxv=s["adx"];atrv=s["atr"];vol24=s["vol24"]
                above=s["above_ema"];trending=s["trending"];oversold=s["oversold"];vol_ok=s["vol_ok"]
                if pair in shadow_long_state and shadow_long_state[pair].get("outcome")=="OPEN":
                    pos=shadow_long_state[pair];s_entry=pos["entry_price"];s_atr=pos["atr"]
                    if px>pos.get("highest_price",px): pos["highest_price"]=px;shadow_long_state[pair]=pos
                    be_trigger=s_entry+s_atr*2.0
                    if not pos.get("at_breakeven") and px>=be_trigger:
                        pos["at_breakeven"]=True;pos["stop_price"]=s_entry;shadow_long_state[pair]=pos
                        log(f"  📊 SHADOW LONG {coin}: Breakeven triggered at ${px:,.4f}")
                    if pos.get("at_breakeven"):
                        trail=pos.get("highest_price",px)-s_atr*1.5
                        if trail>pos["stop_price"]: pos["stop_price"]=trail;shadow_long_state[pair]=pos
                    outcome=None
                    if px<=pos["stop_price"]: outcome="STOP LOSS"
                    elif px>=pos["target_price"]: outcome="TAKE PROFIT"
                    if outcome:
                        pnl_pct=(px-s_entry)/s_entry*100
                        pos["outcome"]=outcome;pos["exit_price"]=px;pos["exit_time"]=now_str();pos["pnl_pct"]=round(pnl_pct,2)
                        shadow_long_state[pair]=pos
                        log(f"  📊 SHADOW LONG {coin}: {outcome} @ ${px:,.4f} | P&L: {pnl_pct:+.2f}%")
                        file_exists=shadow_long_file.exists()
                        with open(shadow_long_file,"a",newline="") as f:
                            writer=csv.writer(f)
                            if not file_exists: writer.writerow(["Timestamp","Pair","Virtual Entry","Stop Price","Target Price","Exit Price","Exit Time","Outcome","Simulated P&L (%)","Notes"])
                            writer.writerow([pos["entry_time"],pair,pos["entry_price"],pos["original_stop"],pos["target_price"],px,now_str(),outcome,round(pnl_pct,2),f"RSI {pos.get('rsi',0):.0f} | ADX {pos.get('adx',0):.0f}"])
                elif oversold and above and trending and vol_ok:
                    sl=round(px-atrv*CONFIG["atr_sl_mult"],6);tp=round(px+atrv*CONFIG["atr_tp_mult"],6)
                    log(f"  📊 SHADOW LONG signal — {coin} @ ${px:,.4f} | RSI {rv:.0f} | ADX {adxv:.0f}")
                    shadow_long_state[pair]={"entry_price":px,"entry_time":now_str(),"stop_price":sl,"original_stop":sl,"target_price":tp,"atr":atrv,"highest_price":px,"at_breakeven":False,"outcome":"OPEN","rsi":rv,"adx":adxv}
                    file_exists=shadow_long_file.exists()
                    with open(shadow_long_file,"a",newline="") as f:
                        writer=csv.writer(f)
                        if not file_exists: writer.writerow(["Timestamp","Pair","Virtual Entry","Stop Price","Target Price","Exit Price","Exit Time","Outcome","Simulated P&L (%)","Notes"])
                        writer.writerow([now_str(),pair,px,sl,tp,"","","OPEN","",f"RSI {rv:.0f} | ADX {adxv:.0f} | Vol ${vol24/1e6:.1f}M"])
                else:
                    missing=[]
                    if not oversold: missing.append(f"RSI {rv:.0f}")
                    if not above: missing.append("below 200 EMA")
                    if not trending: missing.append(f"ADX {adxv:.0f}")
                    if not vol_ok: missing.append(f"Vol ${vol24/1e6:.1f}M low")
                    log(f"  📊 SHADOW {coin}: Waiting — {' | '.join(missing)}")
            except Exception as pe: log(f"  Shadow long {coin} error: {pe}")
        json.dump(shadow_long_state,open(shadow_long_state_file,"w"),indent=2,default=str)
    except Exception as sle: log(f"  Shadow long logger error: {sle}")

    try:
        ai_summary=generate_market_summary(state,scan_results_for_summary,fg,fgl,dominance)
    except Exception as e:
        ai_summary=None;log(f"  AI summary error: {e}")
    try:
        all_below_ema=True;closest=[]
        for pair in CONFIG["pairs"]:
            if pair not in _scan_signals: continue
            s=_scan_signals[pair]
            oversold=s.get("rsi",50)<CONFIG["rsi_oversold"];above_ema=s.get("above_ema",False)
            trending=s.get("adx",0)>=CONFIG["adx_threshold"];vol_ok=s.get("vol_ok",True)
            if above_ema: all_below_ema=False
            met=sum([oversold,above_ema,trending,vol_ok]);missing=[]
            if not oversold: missing.append(f"RSI {s.get('rsi',50):.0f} not oversold yet")
            if not above_ema: missing.append("price below 200 EMA")
            if not trending: missing.append(f"ADX {s.get('adx',0):.0f} ranging")
            coin=pair.split("-")[0];all_3=oversold and above_ema and trending and vol_ok
            closest.append((coin,met,missing,all_3))
        closest.sort(key=lambda x:-x[1])
        lines=[f"Market sentiment: Fear & Greed {fg}/100 — {fgl}."]
        ready=[c for c in closest if c[3]]
        if ready:
            for c in ready: lines.append(f"{c[0]} has all 3 signals firing on 4H chart — bot will enter on next scan if conditions hold.")
        elif all_below_ema:
            lines.append("Every coin is below its 4H 200 EMA — entire market in downtrend. Bot correctly stays in cash.")
            two_of_three=[c for c in closest if c[1]>=3]
            if two_of_three:
                top=two_of_three[0];lines.append(f"Closest: {top[0]} — missing {top[2][0] if top[2] else 'one condition'}.")
        else:
            two=[c for c in closest if c[1]>=3 and not c[3]]
            if two:
                top=two[0];lines.append(f"{top[0]} is closest with {top[1]-1} of 3 signals met. Waiting on: {', '.join(top[2])}.")
            others=[c[0] for c in closest[1:3] if not c[3]]
            if others: lines.append(f"Also watching: {', '.join(others)}.")
        if not ready: lines.append("No trades taken this scan. Bot is being patient.")
        final_summary=ai_summary if ai_summary else " ".join(lines)
        import json as _json
        sf=Path(__file__).parent/"summary.json"
        _json.dump({"time":now_str(),"summary":final_summary,"signals":_scan_signals},open(sf,"w",encoding="utf-8"),indent=2,default=str,ensure_ascii=True)
        log(f"  ✅ Summary written to summary.json")
    except Exception as se: log(f"  Summary error: {se}")

    try:
        sf=Path(__file__).parent/"summary.json"
        if sf.exists():
            sd=json.loads(sf.read_bytes().decode('utf-8',errors='replace'))
            state["ai_summary"]=sd.get("summary","")
            state["ai_summary_time"]=sd.get("time","")
            state["ai_signals"]=sd.get("signals",{})
    except: pass

    nxt=(datetime.now()+timedelta(minutes=CONFIG["scan_interval_minutes"])).strftime("%I:%M %p")
    log(f"  ✅ Next 4H scan at {nxt}.\n")
    save_state(state)


import threading
import websocket
import json as _ws_json

_ws_state_ref=None
_ws_client_ref=None
_ws_running=True
_last_tick_time={}

def ws_on_message(ws,message):
    global _ws_state_ref,_ws_client_ref
    try:
        data=_ws_json.loads(message)
        events=data.get("events",[])
        for event in events:
            for ticker in event.get("tickers",[]):
                pair=ticker.get("product_id","");price_str=ticker.get("price","")
                if not pair or not price_str: continue
                px=float(price_str);_last_tick_time[pair]=time.time()
                if _ws_state_ref is None: continue
                open_trades=_ws_state_ref.get("open_trades",{})
                if pair in open_trades:
                    pos=open_trades[pair];sl=pos.get("stop_loss",0)
                    if sl>0 and px<=sl:
                        log(f"  🚨 WEBSOCKET STOP — {pair.split('-')[0]} price ${px:,.4f} breached stop ${sl:,.4f}")
                        place_order(_ws_client_ref,pair,"SELL",pos["usd_invested"],px,_ws_state_ref,reason="WEBSOCKET_STOP — real-time circuit breaker",atr=pos.get("atr",0))
                        set_cooldown(pair);save_state(_ws_state_ref)
                        log(f"  ✅ WEBSOCKET_STOP logged and position closed")
                short_trades=_ws_state_ref.get("short_open_trades",{})
                if pair in short_trades:
                    pos=short_trades[pair];sl=pos.get("stop_loss",0)
                    if sl>0 and px>=sl:
                        log(f"  🚨 SHORT WEBSOCKET STOP — {pair.split('-')[0]} price ${px:,.4f} breached stop ${sl:,.4f}")
                        close_short(pair,px,_ws_state_ref,reason="SHORT_WEBSOCKET_STOP")
                        save_state(_ws_state_ref)
                        log(f"  ✅ SHORT WEBSOCKET_STOP logged and position closed")
    except Exception as e: log(f"  WebSocket message error: {e}")

def ws_on_error(ws,error): log(f"  ⚠️  WebSocket error: {error}")
def ws_on_close(ws,close_status_code,close_msg): log(f"  ⚠️  WebSocket closed — will reconnect")

def ws_on_open(ws):
    pairs=CONFIG["pairs"]
    subscribe_msg=_ws_json.dumps({"type":"subscribe","product_ids":pairs,"channel":"ticker"})
    ws.send(subscribe_msg)
    log(f"  ✅ WebSocket Risk Desk online — watching {len(pairs)} pairs in real time")

def run_websocket_risk_desk(state,client):
    global _ws_state_ref,_ws_client_ref,_ws_running
    _ws_state_ref=state;_ws_client_ref=client;backoff=1
    while _ws_running:
        try:
            log("  🔌 WebSocket Risk Desk connecting...")
            ws=websocket.WebSocketApp("wss://advanced-trade-ws.coinbase.com",on_open=ws_on_open,on_message=ws_on_message,on_error=ws_on_error,on_close=ws_on_close)
            ws.run_forever(ping_interval=30,ping_timeout=10)
            backoff=min(backoff*2,60);log(f"  🔄 WebSocket reconnecting in {backoff}s...");time.sleep(backoff)
        except Exception as e:
            log(f"  WebSocket thread error: {e}");time.sleep(backoff);backoff=min(backoff*2,60)

def start_risk_desk(state,client):
    t=threading.Thread(target=run_websocket_risk_desk,args=(state,client),daemon=True)
    t.start();log("  ✅ WebSocket Risk Desk thread launched");return t

def main():
    sec("EDGE BOT v8 — 4H SWING TRADER")
    log(f"  Mode:      {'PAPER TRADE' if CONFIG['paper_trade'] else '⚡ LIVE'}")
    log(f"  Timeframe: 4-HOUR CANDLES — Swing Trading")
    log(f"  Entry:     RSI < {CONFIG['rsi_oversold']} (crossover from < {CONFIG['rsi_oversold_reset']}) + Price > 200 EMA + ADX > {CONFIG['adx_threshold']}")
    log(f"  Shorts:    RSI hook >70→<65 + below 200 EMA + ADX > {CONFIG['adx_threshold']} — LIVE ON PAPER")
    log(f"  Stop:      {CONFIG['atr_sl_mult']}x ATR | Target: {CONFIG['atr_tp_mult']}x ATR | Risk: {CONFIG['risk_per_trade']*100:.0f}%/trade")
    log(f"  Scans:     Full 4H scan every 240min | Exit management every 60min")
    log(f"  Cooldown:  {CONFIG['stop_cooldown_hours']}h after any stop loss exit")
    log(f"  Crash Gate: Block entries if BTC+SOL RSI both < {CONFIG['market_crash_rsi']}")
    log(f"  Fear Gate:  Block entries if Fear & Greed < 20")
    log(f"  Stale Monitor: Warning at 16h, Alert at 24h — observation only")
    div("═");log("")
    client=load_client();state=load_state()
    try:
        token=os.environ.get("GITHUB_TOKEN","");repo=os.environ.get("GITHUB_REPO","")
        if token and repo:
            files_to_pull=["state.json","shadow_long_state.json","shadow_longs.csv","equity_curve.csv","strategy_audit.csv","rejected_signals.csv","symbol_performance.csv","trade_explanations.json"]
            headers={"Authorization":f"token {token}","User-Agent":"EDGE-Bot-v8"}
            base_url=f"https://api.github.com/repos/{repo}/contents/"
            for filename in files_to_pull:
                try:
                    req=urllib.request.Request(base_url+filename,headers=headers)
                    with urllib.request.urlopen(req,timeout=10) as r:
                        data=json.loads(r.read());content=base64.b64decode(data["content"])
                        (Path(__file__).parent/filename).write_bytes(content)
                except: pass
            log("  ✅ Startup data restored from GitHub")
    except Exception as e: log(f"  Startup restore error: {e}")
    state=load_state()
    start_risk_desk(state,client)
    log("  ✅ Connected | Running first 4H scan...\n")
    scan(client,state)
    schedule.every(CONFIG["scan_interval_minutes"]).minutes.do(scan,client,state)
    schedule.every(60).minutes.do(hourly_management,client,state)
    nxt=(datetime.now()+timedelta(minutes=CONFIG["scan_interval_minutes"])).strftime("%I:%M %p")
    log(f"  Live. Next 4H scan {nxt}. Exit checks every 60min. Ctrl+C to stop.\n")
    while True:
        schedule.run_pending();time.sleep(30)

if __name__=="__main__": main()
