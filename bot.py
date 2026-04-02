#!/usr/bin/env python3
import json,time,math,schedule,urllib.request
from datetime import datetime,timedelta
from pathlib import Path
from coinbase.rest import RESTClient

CONFIG={"paper_trade":True,"starting_capital":1500.0,"risk_per_trade":0.01,"max_daily_loss_pct":0.025,"max_open_trades":3,"pairs":["BTC-USD","ETH-USD","SOL-USD","LINK-USD","XRP-USD","AVAX-USD","ADA-USD"],"rsi_oversold":35,"adx_threshold":25,"ema_period":200,"atr_period":14,"atr_sl_mult":2.0,"atr_tp_mult":4.0,"atr_be_mult":2.0,"atr_trail_mult":1.5,"min_volume_24h":5000000,"scan_interval_minutes":60,"candle_granularity":"ONE_HOUR","candle_count":220,"api_key_file":"cdp_api_key.json"}
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


def generate_market_summary(state, scan_results, fg, fgl, dominance):
    """Call Claude API to generate plain English market summary"""
    import urllib.request, json

    fg_val = state.get("last_fg", fg)
    cap = state.get("capital", 1500)
    open_trades = state.get("open_trades", {})
    st = state.get("stats", {})

    # Build context for Claude
    coin_lines = []
    closest = []
    waiting = []
    for pair, data in scan_results.items():
        coin = pair.split("-")[0]
        rsi = data.get("rsi", 50)
        above_ema = data.get("above_ema", False)
        trending = data.get("adx", 0) >= 25
        oversold = rsi < 35
        sig = data.get("signal", "HOLD")
        conditions_met = sum([oversold, above_ema, trending])
        if sig == "BUY":
            closest.append(f"{coin} — ALL 3 SIGNALS FIRING — BUY triggered")
        elif conditions_met == 2:
            missing = []
            if not oversold: missing.append(f"RSI {rsi:.0f} needs to drop to 35")
            if not above_ema: missing.append("price needs to rise above 200 EMA")
            if not trending: missing.append(f"ADX {data.get('adx',0):.0f} needs to reach 25")
            closest.append(f"{coin} — 2 of 3 conditions met, missing: {', '.join(missing)}")
        elif conditions_met == 1:
            waiting.append(f"{coin} — only 1 condition met, RSI {rsi:.0f}, {'above' if above_ema else 'below'} 200 EMA, ADX {data.get('adx',0):.0f}")
        else:
            waiting.append(f"{coin} — no conditions met, RSI {rsi:.0f}, {'above' if above_ema else 'below'} 200 EMA, ADX {data.get('adx',0):.0f}")

    open_summary = ""
    if open_trades:
        for pair, pos in open_trades.items():
            coin = pair.split("-")[0]
            open_summary = f"Currently holding {coin} at ${pos.get('entry_price', 0):.4f}."
    else:
        open_summary = "No open positions — holding cash."

    prompt = f"""You are a sharp, direct trading analyst giving a quick market update for a crypto bot. Write exactly like this example — conversational, clear, no jargon, like explaining to a smart friend:

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
        payload = json.dumps({
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 300,
            "messages": [{"role": "user", "content": prompt}]
        }).encode()

        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={
                "content-type": "application/json",
                "anthropic-version": "2023-06-01"
            }
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = json.loads(r.read())
            summary = resp["content"][0]["text"].strip()
            # Save to file
            import pathlib
            summary_file = pathlib.Path(__file__).parent / "market_summary.json"
            json.dump({"summary": summary, "time": now_str()}, open(summary_file, "w"))
            log(f"  📝 Market summary updated")
            return summary
    except Exception as e:
        return None


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
    _scan_signals = {}
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
        _scan_signals[pair]=ind
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
    # Generate market summary
    scan_results_for_summary = {}
    for pair in CONFIG["pairs"]:
        if pair in state.get("open_trades", {}):
            continue
        try:
            candles = get_candles(client, pair)
            if candles and len(candles) >= 210:
                closes = [float(c.close) for c in candles]
                highs = [float(c.high) for c in candles]
                lows = [float(c.low) for c in candles]
                volumes = [float(c.volume) for c in candles]
                sig = analyze(pair, closes, highs, lows, volumes)
                scan_results_for_summary[pair] = {
                    "rsi": sig["indicators"].get("rsi", 50),
                    "above_ema": sig["indicators"].get("above_ema", False),
                    "adx": sig["indicators"].get("adx", 0),
                    "signal": sig["direction"]
                }
        except: pass
    generate_market_summary(state, scan_results_for_summary, state.get("last_fg", 50), state.get("last_fg_label", "Neutral"), state.get("last_dominance", 50))


    # ── SHADOW SHORT LOGGER ─────────────────────────────────────────────────
    # Tracks virtual short signals without placing any real trades
    # Mirror of long logic: RSI > 65, Price < 200 EMA, ADX > 25
    try:
        import csv
        shadow_file = Path(__file__).parent / "shadow_shorts.csv"
        shadow_state_file = Path(__file__).parent / "shadow_state.json"

        # Load existing shadow positions
        shadow_state = {}
        if shadow_state_file.exists():
            try: shadow_state = json.load(open(shadow_state_file))
            except: shadow_state = {}

        # Check open shadow positions for TP/SL hits
        for s_pair, s_pos in list(shadow_state.items()):
            if s_pos.get("outcome") != "OPEN": continue
            s_px = _scan_signals.get(s_pair, {}).get("price", 0)
            if s_px == 0: continue
            s_entry = s_pos["entry_price"]
            s_sl = s_pos["stop_price"]
            s_tp = s_pos["target_price"]
            s_atr = s_pos["atr"]
            s_highest_drop = s_pos.get("lowest_price", s_entry)

            # Update lowest price
            if s_px < s_highest_drop:
                s_pos["lowest_price"] = s_px
                shadow_state[s_pair] = s_pos

            # Breakeven check
            be_trigger = s_entry - s_atr * 2.0
            if not s_pos.get("at_breakeven") and s_px <= be_trigger:
                s_pos["at_breakeven"] = True
                s_pos["stop_price"] = s_entry
                shadow_state[s_pair] = s_pos
                log(f"  📊 SHADOW {s_pair.split('-')[0]}: Breakeven triggered at ${s_px:,.4f}")

            # Trailing stop after breakeven
            if s_pos.get("at_breakeven"):
                trail = s_pos.get("lowest_price", s_px) + s_atr * 1.5
                if trail < s_pos["stop_price"]:
                    s_pos["stop_price"] = trail
                    shadow_state[s_pair] = s_pos

            # Check exits
            outcome = None
            if s_px >= s_pos["stop_price"]:
                outcome = "STOP LOSS"
            elif s_px <= s_tp:
                outcome = "TAKE PROFIT"

            if outcome:
                pnl_pct = (s_entry - s_px) / s_entry * 100
                s_pos["outcome"] = outcome
                s_pos["exit_price"] = s_px
                s_pos["exit_time"] = now_str()
                s_pos["pnl_pct"] = round(pnl_pct, 2)
                shadow_state[s_pair] = s_pos
                log(f"  📊 SHADOW SHORT {s_pair.split('-')[0]}: {outcome} @ ${s_px:,.4f} | P&L: {pnl_pct:+.2f}%")

                # Write to CSV
                file_exists = shadow_file.exists()
                with open(shadow_file, "a", newline="") as f:
                    writer = csv.writer(f)
                    if not file_exists:
                        writer.writerow(["Timestamp","Pair","Virtual Entry","Stop Price","Target Price","Exit Price","Exit Time","Outcome","Simulated P&L (%)","Notes"])
                    writer.writerow([
                        s_pos["entry_time"], s_pair,
                        s_pos["entry_price"], s_pos["original_stop"],
                        s_pos["target_price"], s_px, now_str(),
                        outcome, round(pnl_pct, 2),
                        f"ADX {s_pos.get('adx',0):.0f} at entry"
                    ])

        # Check for new shadow short signals
        for pair in CONFIG["pairs"]:
            if pair not in _scan_signals: continue
            if pair in shadow_state and shadow_state[pair].get("outcome") == "OPEN": continue
            s = _scan_signals[pair]
            px = s.get("price", 0)
            rsi = s.get("rsi", 50)
            above_ema = s.get("above_ema", False)
            adx = s.get("adx", 0)
            atr = s.get("atr", 0)
            if px == 0 or atr == 0: continue

            # Short signal: RSI overbought, price BELOW 200 EMA, ADX trending
            overbought = rsi > 65
            below_ema = not above_ema
            trending = adx >= CONFIG["adx_threshold"]

            if overbought and below_ema and trending:
                sl = round(px + atr * 2.0, 6)
                tp = round(px - atr * 4.0, 6)
                coin = pair.split("-")[0]
                log(f"  📊 SHADOW SHORT signal — {coin} @ ${px:,.4f} | RSI {rsi:.0f} | ADX {adx:.0f}")
                log(f"     Virtual SL: ${sl:,.6f} | Virtual TP: ${tp:,.6f}")
                shadow_state[pair] = {
                    "entry_price": px, "entry_time": now_str(),
                    "stop_price": sl, "original_stop": sl,
                    "target_price": tp, "atr": atr,
                    "lowest_price": px, "at_breakeven": False,
                    "outcome": "OPEN", "rsi": rsi, "adx": adx
                }
                # Write entry to CSV
                file_exists = shadow_file.exists()
                with open(shadow_file, "a", newline="") as f:
                    writer = csv.writer(f)
                    if not file_exists:
                        writer.writerow(["Timestamp","Pair","Virtual Entry","Stop Price","Target Price","Exit Price","Exit Time","Outcome","Simulated P&L (%)","Notes"])
                    writer.writerow([
                        now_str(), pair, px, sl, tp, "", "", "OPEN", "",
                        f"RSI {rsi:.0f} overbought | ADX {adx:.0f} trending"
                    ])

        json.dump(shadow_state, open(shadow_state_file, "w"), indent=2, default=str)
    except Exception as se:
        log(f"  Shadow logger error: {se}")


    # ── SHADOW LONG LOGGER — DOGE & DOT AUDIT ──────────────────────────────
    # Tracks virtual long signals on DOGE and DOT without placing real trades
    # Same logic as live bot — RSI < 35, Price > 200 EMA, ADX > 25
    try:
        SHADOW_LONG_PAIRS = ["DOGE-USD", "DOT-USD"]
        shadow_long_file = Path(__file__).parent / "shadow_longs.csv"
        shadow_long_state_file = Path(__file__).parent / "shadow_long_state.json"

        shadow_long_state = {}
        if shadow_long_state_file.exists():
            try: shadow_long_state = json.load(open(shadow_long_state_file))
            except: shadow_long_state = {}

        # Fetch candles and analyze for shadow long pairs
        for pair in SHADOW_LONG_PAIRS:
            coin = pair.split("-")[0]
            try:
                candles = get_candles(client, pair)
                if not candles or len(candles) < 210:
                    continue
                closes  = [float(c.close)  for c in candles]
                highs   = [float(c.high)   for c in candles]
                lows    = [float(c.low)    for c in candles]
                volumes = [float(c.volume) for c in candles]

                px    = closes[-1]
                rv    = calc_rsi(closes)
                e200  = calc_ema(closes, CONFIG["ema_period"])
                adxv  = calc_adx(highs, lows, closes)
                atrv  = calc_atr(highs, lows, closes)
                vol24 = calc_volume_24h(closes, volumes)
                above = px > e200 * 1.001
                trending = adxv >= CONFIG["adx_threshold"]
                oversold = rv < CONFIG["rsi_oversold"]
                vol_ok = vol24 >= CONFIG["min_volume_24h"]

                # Check open shadow long positions
                if pair in shadow_long_state and shadow_long_state[pair].get("outcome") == "OPEN":
                    pos = shadow_long_state[pair]
                    s_entry  = pos["entry_price"]
                    s_sl     = pos["stop_price"]
                    s_tp     = pos["target_price"]
                    s_atr    = pos["atr"]
                    s_high   = pos.get("highest_price", px)

                    if px > s_high:
                        pos["highest_price"] = px
                        shadow_long_state[pair] = pos

                    # Breakeven
                    be_trigger = s_entry + s_atr * 2.0
                    if not pos.get("at_breakeven") and px >= be_trigger:
                        pos["at_breakeven"] = True
                        pos["stop_price"] = s_entry
                        shadow_long_state[pair] = pos
                        log(f"  📊 SHADOW LONG {coin}: Breakeven triggered at ${px:,.4f}")

                    # Trailing after breakeven
                    if pos.get("at_breakeven"):
                        trail = pos.get("highest_price", px) - s_atr * 1.5
                        if trail > pos["stop_price"]:
                            pos["stop_price"] = trail
                            shadow_long_state[pair] = pos

                    # Check exits
                    outcome = None
                    if px <= pos["stop_price"]:
                        outcome = "STOP LOSS"
                    elif px >= s_tp:
                        outcome = "TAKE PROFIT"

                    if outcome:
                        pnl_pct = (px - s_entry) / s_entry * 100
                        pos["outcome"] = outcome
                        pos["exit_price"] = px
                        pos["exit_time"] = now_str()
                        pos["pnl_pct"] = round(pnl_pct, 2)
                        shadow_long_state[pair] = pos
                        log(f"  📊 SHADOW LONG {coin}: {outcome} @ ${px:,.4f} | P&L: {pnl_pct:+.2f}%")

                        import csv
                        file_exists = shadow_long_file.exists()
                        with open(shadow_long_file, "a", newline="") as f:
                            writer = csv.writer(f)
                            if not file_exists:
                                writer.writerow(["Timestamp","Pair","Virtual Entry","Stop Price","Target Price","Exit Price","Exit Time","Outcome","Simulated P&L (%)","Notes"])
                            writer.writerow([
                                pos["entry_time"], pair,
                                pos["entry_price"], pos["original_stop"],
                                pos["target_price"], px, now_str(),
                                outcome, round(pnl_pct, 2),
                                f"RSI {pos.get('rsi',0):.0f} | ADX {pos.get('adx',0):.0f} at entry"
                            ])

                # Check for new shadow long signal
                elif oversold and above and trending and vol_ok:
                    sl = round(px - atrv * CONFIG["atr_sl_mult"], 6)
                    tp = round(px + atrv * CONFIG["atr_tp_mult"], 6)
                    log(f"  📊 SHADOW LONG signal — {coin} @ ${px:,.4f} | RSI {rv:.0f} | ADX {adxv:.0f}")
                    log(f"     Virtual SL: ${sl:,.6f} | Virtual TP: ${tp:,.6f}")
                    shadow_long_state[pair] = {
                        "entry_price": px, "entry_time": now_str(),
                        "stop_price": sl, "original_stop": sl,
                        "target_price": tp, "atr": atrv,
                        "highest_price": px, "at_breakeven": False,
                        "outcome": "OPEN", "rsi": rv, "adx": adxv
                    }
                    import csv
                    file_exists = shadow_long_file.exists()
                    with open(shadow_long_file, "a", newline="") as f:
                        writer = csv.writer(f)
                        if not file_exists:
                            writer.writerow(["Timestamp","Pair","Virtual Entry","Stop Price","Target Price","Exit Price","Exit Time","Outcome","Simulated P&L (%)","Notes"])
                        writer.writerow([
                            now_str(), pair, px, sl, tp, "", "", "OPEN", "",
                            f"RSI {rv:.0f} oversold | ADX {adxv:.0f} trending | Vol ${vol24/1e6:.1f}M"
                        ])
                else:
                    missing = []
                    if not oversold:  missing.append(f"RSI {rv:.0f}")
                    if not above:     missing.append("below 200 EMA")
                    if not trending:  missing.append(f"ADX {adxv:.0f}")
                    if not vol_ok:    missing.append(f"Vol ${vol24/1e6:.1f}M low")
                    log(f"  📊 SHADOW {coin}: Waiting — {' | '.join(missing)}")

            except Exception as pe:
                log(f"  Shadow long {coin} error: {pe}")

        json.dump(shadow_long_state, open(shadow_long_state_file, "w"), indent=2, default=str)
    except Exception as sle:
        log(f"  Shadow long logger error: {sle}")

    # Generate plain English market summary
    try:
        all_below_ema = True
        closest = []
        for pair in CONFIG["pairs"]:
            if pair not in _scan_signals:
                continue
            s = _scan_signals[pair]
            oversold  = s.get("rsi", 50) < CONFIG["rsi_oversold"]
            above_ema = s.get("above_ema", False)
            trending  = s.get("adx", 0) >= CONFIG["adx_threshold"]
            vol_ok    = s.get("vol_ok", True)
            if above_ema:
                all_below_ema = False
            met = sum([oversold, above_ema, trending, vol_ok])
            missing = []
            if not oversold:  missing.append(f"RSI {s.get('rsi',50):.0f} not oversold yet (need < {CONFIG['rsi_oversold']})")
            if not above_ema: missing.append(f"price below 200 EMA")
            if not trending:  missing.append(f"ADX {s.get('adx',0):.0f} ranging (need > {CONFIG['adx_threshold']})")
            coin = pair.split("-")[0]
            all_3 = oversold and above_ema and trending and vol_ok
            closest.append((coin, met, missing, all_3))

        closest.sort(key=lambda x: -x[1])
        fg  = state.get("last_fg", 50)
        fgl = state.get("last_fg_label", "Neutral")
        lines = [f"Market sentiment: Fear & Greed {fg}/100 — {fgl}."]

        ready = [c for c in closest if c[3]]
        if ready:
            for c in ready:
                lines.append(f"{c[0]} has all 3 signals firing — RSI oversold, price above 200 EMA, ADX trending. Bot will enter on next scan if conditions hold.")
        elif all_below_ema:
            lines.append("Every coin is below its 200 EMA right now — the entire market is in a downtrend. RSI and ADX may look good but the bot correctly stays in cash until price recovers above the 200 EMA.")
            two_of_three = [c for c in closest if c[1] >= 3]
            if two_of_three:
                top = two_of_three[0]
                lines.append(f"Closest to a signal: {top[0]} — only missing {top[2][0] if top[2] else 'one condition'}.")
        else:
            two = [c for c in closest if c[1] >= 3 and not c[3]]
            if two:
                top = two[0]
                lines.append(f"{top[0]} is the closest with {top[1]-1} of 3 signals met. Still waiting on: {', '.join(top[2])}.")
            others = [c[0] for c in closest[1:3] if not c[3]]
            if others:
                lines.append(f"Also watching: {', '.join(others)}.")

        if not ready:
            lines.append("No trades taken this scan. Bot is being patient — all 3 signals must agree before any entry.")

        import json as _json
        sf = Path(__file__).parent / "summary.json"
        _json.dump({"time": now_str(), "summary": " ".join(lines), "signals": _scan_signals}, open(sf, "w"), indent=2, default=str)
    except Exception as se:
        log(f"  Summary error: {se}")

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
