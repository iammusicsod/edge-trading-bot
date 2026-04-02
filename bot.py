#!/usr/bin/env python3
import json,time,math,schedule,urllib.request
from datetime import datetime,timedelta
from pathlib import Path
from coinbase.rest import RESTClient

CONFIG={"paper_trade":True,"starting_capital":1500.0,"risk_per_trade":0.01,"max_daily_loss_pct":0.025,"max_open_trades":3,"pairs":["BTC-USD","ETH-USD","SOL-USD","LINK-USD","GRT-USD","AVAX-USD","UNI-USD"],"rsi_oversold":35,"adx_threshold":25,"ema_period":200,"atr_period":14,"atr_sl_mult":2.0,"atr_tp_mult":4.0,"atr_be_mult":2.0,"atr_trail_mult":2.0,"min_volume_24h":5000000,"scan_interval_minutes":60,"candle_granularity":"ONE_HOUR","candle_count":220,"api_key_file":"cdp_api_key.json"}
LOG_FILE=Path(__file__).parent/"bot_log.txt"
STATE_FILE=Path(__file__).parent/"state.json"
TRADES_FILE=Path(__file__).parent/"trade_explanations.json"

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
        s.setdefault("performance",{"total_trades":0,"wins":0,"losses":0,"breakevens":0,"total_pnl":0.0,"max_drawdown":0.0,"peak_capital":CONFIG["starting_capital"]})
        return s
    return {"capital":CONFIG["starting_capital"],"open_trades":{},"trade_history":[],"daily_pnl":0.0,"total_pnl":0.0,"last_reset":date_str(),"trade_count_today":0,"stats":{"wins":0,"losses":0,"breakevens":0,"total_trades":0},"performance":{"total_trades":0,"wins":0,"losses":0,"breakevens":0,"total_pnl":0.0,"max_drawdown":0.0,"peak_capital":CONFIG["starting_capital"]},"last_fg":50,"last_fg_label":"Neutral","last_dominance":50,"last_funding":0.0}

def save_state(s): json.dump(s,open(STATE_FILE,"w"),indent=2,default=str)
def save_explanation(exp):
    exps=[]
    if TRADES_FILE.exists():
        try: exps=json.load(open(TRADES_FILE))
        except: exps=[]
    exps.insert(0,exp);exps=exps[:50];json.dump(exps,open(TRADES_FILE,"w"),indent=2,default=str)

def load_client():
    import os
    ak=os.environ.get("API_KEY_NAME");ap=os.environ.get("API_KEY_PRIVATE")
    if ak and ap: return RESTClient(api_key=ak,api_secret=ap)
    k=json.load(open(Path(__file__).parent/CONFIG["api_key_file"]))
    return RESTClient(api_key=k["name"],api_secret=k["privateKey"])

def fetch_url(url,timeout=5):
    try:
        with urllib.request.urlopen(url,timeout=timeout) as r: return json.loads(r.read())
    except: return None

def get_fear_greed():
    d=fetch_url("https://api.alternative.me/fng/")
    if d: return int(d["data"][0]["value"]),d["data"][0]["value_classification"]
    return 50,"Neutral"

def get_btc_dominance():
    d=fetch_url("https://api.coingecko.com/api/v3/global")
    if d: return float(d["data"]["market_cap_percentage"]["btc"])
    return 50.0

def get_funding_rate():
    d=fetch_url("https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT")
    if d: return float(d.get("lastFundingRate",d.get("fundingRate",0)))*100
    return 0.0

def get_candles(client,pair):
    try:
        end=int(time.time());start=end-3600*CONFIG["candle_count"]
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
    if len(closes)<24 or len(volumes)<24: return 0.0
    return sum(closes[-24+i]*volumes[-24+i] for i in range(24))

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
        log(f"  💰 {mode} — Buying ${usd:.2f} of {coin} @ ${price:,.4f}")
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
    state["trade_history"].append({"time":now_str(),"pair":pair,"side":side,"usd":usd,"price":price,"paper":CONFIG["paper_trade"],"explanation":reason})
    state["stats"]["total_trades"]+=1;state["performance"]["total_trades"]+=1
    if side=="BUY":
        sl=price-atr*CONFIG["atr_sl_mult"];tp=price+atr*CONFIG["atr_tp_mult"];be=price+atr*CONFIG["atr_be_mult"]
        state["open_trades"][pair]={"entry_price":price,"usd_invested":usd,"entry_time":now_str(),"highest_price":price,"atr":atr,"stop_loss":sl,"take_profit":tp,"be_trigger":be,"at_breakeven":False,"explanation":reason}
        state["capital"]-=usd;state["trade_count_today"]=state.get("trade_count_today",0)+1
        save_explanation({"time":now_str(),"pair":pair,"side":"BUY","price":price,"usd":usd,"explanation":reason,"stop_loss":sl,"take_profit":tp,"be_trigger":be})
    elif pair in state["open_trades"]:
        e=state["open_trades"].pop(pair);pnl=(price-e["entry_price"])/e["entry_price"]*e["usd_invested"]
        state["capital"]+=e["usd_invested"]+pnl;state["daily_pnl"]+=pnl;state["total_pnl"]+=pnl;state["performance"]["total_pnl"]+=pnl
        is_be=abs(pnl)<e["usd_invested"]*0.005
        if pnl>0: state["stats"]["wins"]+=1;state["performance"]["wins"]+=1;log(f"  🏆 Profit: ${pnl:+.2f}")
        elif is_be: state["stats"]["breakevens"]+=1;state["performance"]["breakevens"]+=1;log(f"  ↔️  Breakeven: ${pnl:+.2f}")
        else: state["stats"]["losses"]+=1;state["performance"]["losses"]+=1;log(f"  📉 Loss: ${pnl:+.2f}")
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
    log("  Checking positions...")
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
        if px<=sl:
            log(f"  {'↔️  BREAKEVEN STOP' if at_be else '🛑 STOP LOSS'} — {pair.split('-')[0]}")
            place_order(client,pair,"SELL",pos["usd_invested"],px,state,atr=atr)
        elif px>=tp:
            log(f"  🎯 TAKE PROFIT — {pair.split('-')[0]} +{ch:.1f}%")
            place_order(client,pair,"SELL",pos["usd_invested"],px,state,atr=atr)

def scan(client,state):
    sec(f"SCAN — {time_str()}")
    fg,fgl=get_fear_greed();dominance=get_btc_dominance();funding=get_funding_rate()
    state["last_fg"]=fg;state["last_fg_label"]=fgl;state["last_dominance"]=dominance;state["last_funding"]=funding
    log(f"  Fear & Greed: {fg}/100 — {fgl} | BTC Dom: {dominance:.1f}% | Funding: {funding:.4f}%");log("")
    check_exits(client,state)
    if not risk_ok(state): save_state(state);return
    for pair in CONFIG["pairs"]:
        coin=pair.split("-")[0];div();log(f"  {coin}");div()
        if pair in state["open_trades"]: log("  Already holding — skipping");continue
        candles=get_candles(client,pair)
        if not candles: log("  No data");continue
        closes=[float(c.close) for c in candles];highs=[float(c.high) for c in candles]
        lows=[float(c.low) for c in candles];volumes=[float(c.volume) for c in candles]
        signal=analyze(pair,closes,highs,lows,volumes);ind=signal["indicators"]
        px=ind.get("price",0);rv=ind.get("rsi",50);e200=ind.get("ema200",0)
        adxv=ind.get("adx",0);atrv=ind.get("atr",0);vol24=ind.get("vol24",0)
        log(f"  Price:   ${px:,.4f}")
        log(f"  RSI:     {rv:.1f}  {'✅ oversold — signal firing' if rv<35 else '— neutral' if rv<65 else '🔴 overbought'}")
        log(f"  200 EMA: ${e200:,.4f}  {'✅ price above — uptrend' if ind.get('above_ema') else '⚠️  price below — skip'}")
        log(f"  ADX:     {adxv:.1f}  {'✅ trending — ok to trade' if ind.get('trending') else '⚠️  ranging — skip'}")
        log(f"  Vol 24h: ${vol24/1e6:.1f}M  {'✅ sufficient liquidity' if vol24>=CONFIG['min_volume_24h'] else '⚠️  too low — skip'}")
        log(f"  ATR:     ${atrv:,.6f} | SL: ${ind.get('sl',0):,.4f} | TP: ${ind.get('tp',0):,.4f}")
        log(f"  → {signal['reason']}")
        if signal["direction"]=="BUY" and px>0:
            usd=pos_size(state,atrv,px)
            if usd>=10: place_order(client,pair,"BUY",usd,px,state,signal["reason"],atrv)
            else: log(f"  Too small ${usd:.2f}")
    st=state["stats"];perf=state["performance"];t=st["total_trades"];wr=(st["wins"]/t*100) if t else 0
    gr=(state["capital"]-CONFIG["starting_capital"])/CONFIG["starting_capital"]*100
    sec(f"PORTFOLIO — {'PAPER' if CONFIG['paper_trade'] else 'LIVE'}")
    log(f"  Capital: ${state['capital']:,.2f} ({'▲' if gr>=0 else '▼'}{abs(gr):.1f}%) | P/L: ${state['total_pnl']:+,.2f} | Today: ${state['daily_pnl']:+,.2f}")
    log(f"  Trades: {t} | Wins: {st['wins']} | Losses: {st['losses']} | Breakevens: {st.get('breakevens',0)} | WR: {wr:.1f}%")
    log(f"  Drawdown: {perf.get('max_drawdown',0):.1f}%")
    if state["open_trades"]:
        for p2,pos in state["open_trades"].items():
            be_s="✅ BE active" if pos.get("at_breakeven") else f"BE at ${pos.get('be_trigger',0):,.4f}"
            log(f"  • {p2} ${pos['usd_invested']:.2f} @ ${pos['entry_price']:,.4f} | SL ${pos.get('stop_loss',0):,.4f} | TP ${pos.get('take_profit',0):,.4f} | {be_s}")
    else: log("  Holding cash — waiting for clean setup")
    div("═")
    nxt=(datetime.now()+timedelta(minutes=CONFIG["scan_interval_minutes"])).strftime("%I:%M %p")
    log(f"  ✅ Next scan at {nxt}.\n");save_state(state)

def main():
    sec("EDGE BOT v7 — FINAL CLEAN VERSION")
    log(f"  Mode:      {'PAPER TRADE' if CONFIG['paper_trade'] else '⚡ LIVE'}")
    log(f"  Entry:     RSI < {CONFIG['rsi_oversold']} + Price > 200 EMA + ADX > {CONFIG['adx_threshold']}")
    log(f"  Volume:    Min ${CONFIG['min_volume_24h']/1e6:.0f}M 24h | Longs only")
    log(f"  Stop:      {CONFIG['atr_sl_mult']}x ATR | Target: {CONFIG['atr_tp_mult']}x ATR | Risk: {CONFIG['risk_per_trade']*100:.0f}%/trade")
    log(f"  Breakeven: +{CONFIG['atr_be_mult']}x ATR → stop moves to entry")
    log(f"  Trailing:  {CONFIG['atr_trail_mult']}x ATR from highest after breakeven")
    div("═");log("")
    client=load_client();state=load_state()
    log("  ✅ Connected | Running first scan...\n")
    scan(client,state)
    schedule.every(CONFIG["scan_interval_minutes"]).minutes.do(scan,client,state)
    nxt=(datetime.now()+timedelta(minutes=CONFIG["scan_interval_minutes"])).strftime("%I:%M %p")
    log(f"  Live. Next scan {nxt}. Ctrl+C to stop.\n")
    while True: schedule.run_pending();time.sleep(30)

if __name__=="__main__": main()
