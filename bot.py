#!/usr/bin/env python3
"""EDGE Trading Bot v5 — RSI+MACD+OBV+200EMA, ATR exits, BTC filter"""
import json,time,math,schedule,urllib.request
from datetime import datetime,timedelta
from pathlib import Path
from coinbase.rest import RESTClient

CONFIG={"paper_trade":True,"starting_capital":1500.0,"max_risk_per_trade":0.01,"max_daily_loss_pct":0.025,"max_open_trades":3,"min_signals":3,"min_confidence":72,"pairs":["BTC-USD","ETH-USD","SOL-USD","LINK-USD","GRT-USD","AVAX-USD","UNI-USD"],"adx_threshold":25,"ema_period":200,"atr_period":14,"atr_sl_mult":1.5,"atr_tp_mult":3.0,"trailing_stop_pct":0.03,"scan_interval_minutes":15,"candle_granularity":"ONE_HOUR","candle_count":220,"api_key_file":"cdp_api_key.json","session_filter":False,"session_start_hour":8,"session_end_hour":20,"adx_period":14,"atr_period":14}
LOG_FILE=Path(__file__).parent/"bot_log.txt"
STATE_FILE=Path(__file__).parent/"state.json"
TRADES_FILE=Path(__file__).parent/"trade_explanations.json"
CONFIG_FILE=Path(__file__).parent/"config_override.json"

def now_str(): return datetime.now().strftime("%B %d, %Y  %I:%M:%S %p")
def time_str(): return datetime.now().strftime("%I:%M %p")
def date_str(): return datetime.now().date().isoformat()
def log(msg):
    line=f"[{now_str()}]  {msg}";print(line)
    open(LOG_FILE,"a").write(line+"\n")
def div(c="─"): log(c*60)
def sec(t): div("═");log(f"  {t}");div("═")

def load_config_overrides():
    if CONFIG_FILE.exists():
        try: CONFIG.update(json.load(open(CONFIG_FILE)))
        except: pass

def load_state():
    if STATE_FILE.exists():
        s=json.load(open(STATE_FILE))
        s.setdefault("trade_count_today",0)
        s.setdefault("performance",{"total_trades":0,"wins":0,"losses":0,"total_pnl":0.0,"max_drawdown":0.0,"peak_capital":CONFIG["starting_capital"]})
        s.setdefault("atr_stops",{})
        return s
    return {"capital":CONFIG["starting_capital"],"open_trades":{},"trade_history":[],"daily_pnl":0.0,"total_pnl":0.0,"last_reset":date_str(),"trade_count_today":0,"stats":{"wins":0,"losses":0,"total_trades":0},"performance":{"total_trades":0,"wins":0,"losses":0,"total_pnl":0.0,"max_drawdown":0.0,"peak_capital":CONFIG["starting_capital"]},"last_fg":50,"last_fg_label":"Neutral","last_dominance":50,"last_funding":0.0,"atr_stops":{}}

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

def get_funding_rate():
    d=fetch_url("https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT")
    if d: return float(d.get("lastFundingRate",d.get("fundingRate",0)))*100
    return 0.0

def get_btc_dominance():
    d=fetch_url("https://api.coingecko.com/api/v3/global")
    if d: return float(d["data"]["market_cap_percentage"]["btc"])
    return 50.0

def get_exchange_flow():
    d=fetch_url("https://api.binance.com/api/v3/depth?symbol=BTCUSDT&limit=20")
    if not d: return 0,"No data"
    bids=sum(float(b[1]) for b in d["bids"]);asks=sum(float(a[1]) for a in d["asks"])
    ratio=bids/asks if asks>0 else 1.0
    if ratio>1.3: return 1,f"Buy pressure ({ratio:.2f})"
    elif ratio<0.7: return -1,f"Sell pressure ({ratio:.2f})"
    return 0,f"Balanced ({ratio:.2f})"

def fetch_alpha():
    fg,fgl=get_fear_greed();funding=get_funding_rate();dom=get_btc_dominance();flow,fdesc=get_exchange_flow()
    return{"fg":fg,"fg_label":fgl,"funding":funding,"dominance":dom,"exchange_flow":flow,"exchange_flow_desc":fdesc}

def get_candles(client,pair,granularity=None,count=None):
    try:
        gs=granularity or CONFIG["candle_granularity"];cnt=count or CONFIG["candle_count"]
        gm={"ONE_MINUTE":60,"FIVE_MINUTE":300,"ONE_HOUR":3600,"ONE_DAY":86400}
        end=int(time.time());start=end-gm.get(gs,3600)*cnt
        r=client.get_candles(product_id=pair,start=str(start),end=str(end),granularity=gs)
        return sorted(r.candles if hasattr(r,"candles") else [],key=lambda c:int(c.start))
    except Exception as e: log(f"  No candles {pair}: {e}");return []

def to_lists(c): return [float(x.close) for x in c],[float(x.high) for x in c],[float(x.low) for x in c],[float(x.volume) for x in c]

def get_price(client,pair):
    try:
        r=client.get_best_bid_ask(product_ids=[pair])
        for p in r.pricebooks:
            if p.product_id==pair:
                b=float(p.bids[0].price) if p.bids else 0;a=float(p.asks[0].price) if p.asks else 0;return(b+a)/2
    except: pass
    return 0.0

def ema(d,n):
    if len(d)<n: return d[-1] if d else 0.0
    k=2/(n+1);e=sum(d[:n])/n
    for v in d[n:]: e=v*k+e*(1-k)
    return e

def rsi(closes,n=14):
    if len(closes)<n+1: return 50.0
    g=[abs(closes[i]-closes[i-1]) for i in range(-n,0) if closes[i]>closes[i-1]]
    l=[abs(closes[i]-closes[i-1]) for i in range(-n,0) if closes[i]<=closes[i-1]]
    ag=sum(g)/n if g else 0;al=sum(l)/n if l else 1e-9;return 100-(100/(1+ag/al))

def macd_signal(closes,fast=12,slow=26,sig=9):
    if len(closes)<slow+sig: return "neutral"
    ef=ema(closes,fast);es=ema(closes,slow);ml=ef-es
    mv=[ema(closes[:i],fast)-ema(closes[:i],slow) for i in range(slow,len(closes)+1)]
    sl=ema(mv,sig) if len(mv)>=sig else ml;hist=ml-sl
    if ml>sl and hist>0: return "bullish"
    elif ml<sl and hist<0: return "bearish"
    return "neutral"

def obv_signal(closes,volumes):
    if len(closes)<2: return "neutral"
    half=len(closes)//2
    o1=sum(volumes[i] if closes[i]>closes[i-1] else -volumes[i] if closes[i]<closes[i-1] else 0 for i in range(1,half))
    o2=sum(volumes[i] if closes[i]>closes[i-1] else -volumes[i] if closes[i]<closes[i-1] else 0 for i in range(half,len(closes)))
    if o2>o1*1.1: return "bullish"
    elif o2<o1*0.9: return "bearish"
    return "neutral"

def ema200_signal(closes):
    if len(closes)<200: return "unknown",0.0
    e200=ema(closes,200);px=closes[-1]
    if px>e200*1.001: return "above",e200
    elif px<e200*0.999: return "below",e200
    return "at",e200

def calc_adx(highs,lows,closes,n=14):
    if len(closes)<n*2: return 20.0
    try:
        trs,pdms,ndms=[],[],[]
        for i in range(1,len(closes)):
            h,l,pc=highs[i],lows[i],closes[i-1];tr=max(h-l,abs(h-pc),abs(l-pc))
            pdm=max(h-highs[i-1],0) if h-highs[i-1]>lows[i-1]-l else 0
            ndm=max(lows[i-1]-l,0) if lows[i-1]-l>h-highs[i-1] else 0
            trs.append(tr);pdms.append(pdm);ndms.append(ndm)
        atr=sum(trs[-n:])/n
        if atr==0: return 20.0
        pdi=(sum(pdms[-n:])/n)/atr*100;ndi=(sum(ndms[-n:])/n)/atr*100
        return abs(pdi-ndi)/(pdi+ndi)*100 if(pdi+ndi)>0 else 0
    except: return 20.0

def calc_atr(highs,lows,closes,n=14):
    if len(closes)<n+1: return closes[-1]*0.02 if closes else 1.0
    trs=[max(highs[i]-lows[i],abs(highs[i]-closes[i-1]),abs(lows[i]-closes[i-1])) for i in range(-n,0)]
    return sum(trs)/n

def btc_above_200ema(client):
    try:
        c=get_candles(client,"BTC-USD","ONE_HOUR",220)
        if not c: return True
        closes=[float(x.close) for x in c];sig,e200=ema200_signal(closes);return sig=="above"
    except: return True

def analyze(pair,closes,highs,lows,volumes,client,alpha,state):
    if len(closes)<50: return{"direction":"HOLD","confidence":0,"signals":[],"blocked_by":[],"indicators":{},"explanation":""}
    rv=rsi(closes);mt=macd_signal(closes);obv_t=obv_signal(closes,volumes);ema_sig,e200=ema200_signal(closes)
    px=closes[-1];adx_val=calc_adx(highs,lows,closes,CONFIG["adx_period"]);atr_val=calc_atr(highs,lows,closes,CONFIG["atr_period"])
    adx_ok=adx_val>=CONFIG["adx_threshold"]
    bs,ss=[],[]
    if rv<35: bs.append(f"RSI {rv:.0f} oversold")
    elif rv>65: ss.append(f"RSI {rv:.0f} overbought")
    if mt=="bullish": bs.append("MACD bullish crossover")
    elif mt=="bearish": ss.append("MACD bearish crossover")
    if obv_t=="bullish": bs.append("OBV rising — buying volume")
    elif obv_t=="bearish": ss.append("OBV falling — selling volume")
    if ema_sig=="above": bs.append(f"Price above 200 EMA (${e200:,.2f})")
    elif ema_sig=="below": ss.append(f"Price below 200 EMA (${e200:,.2f})")
    nb=len(bs);ns=len(ss);blocked=[];direction="HOLD";signals=[];explanation=""
    if nb>=CONFIG["min_signals"] and nb>ns:
        if not adx_ok: blocked.append(f"ADX {adx_val:.0f} < {CONFIG['adx_threshold']} — ranging market")
        elif pair!="BTC-USD" and not btc_above_200ema(client): blocked.append("BTC below 200 EMA — no altcoin longs")
        else:
            direction="BUY";signals=bs
            sl=px-atr_val*CONFIG["atr_sl_mult"];tp=px+atr_val*CONFIG["atr_tp_mult"]
            explanation=f"Entered LONG {pair.split('-')[0]}: {', '.join(bs[:3])}. ADX {adx_val:.0f}. SL ${sl:,.2f} TP ${tp:,.2f}."
    elif ns>=CONFIG["min_signals"] and ns>nb:
        if not adx_ok: blocked.append(f"ADX {adx_val:.0f} < {CONFIG['adx_threshold']} — ranging market")
        else: direction="SELL";signals=ss;explanation=f"Exiting {pair.split('-')[0]}: {', '.join(ss[:3])}."
    conf=min(100,40+nb*15) if direction=="BUY" else min(100,40+ns*15) if direction=="SELL" else 0
    sl=round(px-atr_val*CONFIG["atr_sl_mult"],4) if direction=="BUY" else 0
    tp=round(px+atr_val*CONFIG["atr_tp_mult"],4) if direction=="BUY" else 0
    ind={"rsi":rv,"macd_trend":mt,"obv":obv_t,"ema200":e200,"ema_sig":ema_sig,"adx":adx_val,"atr":atr_val,"price":px,"buy_ct":nb,"sell_ct":ns,"sl":sl,"tp":tp}
    return{"direction":direction,"confidence":conf,"signals":signals,"blocked_by":blocked,"indicators":ind,"explanation":explanation}

def pos_size(state,confidence,atr,price):
    capital=state["capital"];base=capital*CONFIG["max_risk_per_trade"]
    stop_dist=atr*CONFIG["atr_sl_mult"]
    usd=(base/stop_dist)*price if stop_dist>0 and price>0 else base*4
    mult=1.3 if confidence>=85 else 1.0 if confidence>=72 else 0.8
    return min(round(usd*mult,2),capital*0.06)

def place_order(client,pair,side,usd,price,state,explanation="",atr=0):
    mode="PAPER TRADE" if CONFIG["paper_trade"] else "LIVE TRADE"
    if side=="BUY":
        sl=price-atr*CONFIG["atr_sl_mult"];tp=price+atr*CONFIG["atr_tp_mult"]
        log(f"  💰 {mode} — Buying ${usd:.2f} of {pair} @ ${price:,.2f}")
        log(f"     SL: ${sl:,.2f} | TP: ${tp:,.2f} | ATR: ${atr:,.4f}")
        if explanation: log(f"  📝 {explanation}")
    else:
        entry=state["open_trades"].get(pair,{}).get("entry_price",price);pct=(price-entry)/entry*100
        log(f"  💸 {mode} — Selling {pair} @ ${price:,.2f} ({'gained' if pct>0 else 'lost'} {abs(pct):.1f}%)")
    if not CONFIG["paper_trade"]:
        try:
            import uuid;cid=str(uuid.uuid4())
            if side=="BUY": r=client.market_order_buy(client_order_id=cid,product_id=pair,quote_size=str(usd))
            else: r=client.market_order_sell(client_order_id=cid,product_id=pair,base_size=str(round(usd/price,8)))
            log("  ✅ Confirmed!" if getattr(r,"success",True) else "  ⚠️  Check Coinbase")
        except Exception as e: log(f"  ❌ {e}");return False
    state["trade_history"].append({"time":now_str(),"pair":pair,"side":side,"usd":usd,"price":price,"paper":CONFIG["paper_trade"],"explanation":explanation})
    state["stats"]["total_trades"]+=1;state["performance"]["total_trades"]+=1
    if side=="BUY":
        sl=price-atr*CONFIG["atr_sl_mult"];tp=price+atr*CONFIG["atr_tp_mult"]
        state["open_trades"][pair]={"entry_price":price,"usd_invested":usd,"entry_time":now_str(),"highest_price":price,"atr":atr,"stop_loss":sl,"take_profit":tp,"explanation":explanation}
        state["capital"]-=usd;state["trade_count_today"]=state.get("trade_count_today",0)+1
        if explanation: save_explanation({"time":now_str(),"pair":pair,"side":"BUY","price":price,"usd":usd,"explanation":explanation,"stop_loss":sl,"take_profit":tp})
    elif pair in state["open_trades"]:
        e=state["open_trades"].pop(pair);pnl=(price-e["entry_price"])/e["entry_price"]*e["usd_invested"]
        state["capital"]+=e["usd_invested"]+pnl;state["daily_pnl"]+=pnl;state["total_pnl"]+=pnl;state["performance"]["total_pnl"]+=pnl
        if pnl>0: state["stats"]["wins"]+=1;state["performance"]["wins"]+=1;log(f"  🏆 Profit: ${pnl:+.2f}")
        else: state["stats"]["losses"]+=1;state["performance"]["losses"]+=1;log(f"  📉 Loss: ${pnl:+.2f}")
        peak=state["performance"].get("peak_capital",CONFIG["starting_capital"])
        if state["capital"]>peak: state["performance"]["peak_capital"]=state["capital"]
        else:
            dd=(peak-state["capital"])/peak*100
            if dd>state["performance"].get("max_drawdown",0): state["performance"]["max_drawdown"]=dd
        save_explanation({"time":now_str(),"pair":pair,"side":"SELL","price":price,"pnl":pnl,"explanation":f"Exited {'profit' if pnl>0 else 'loss'} ${abs(pnl):.2f}"})
    return True

def session_ok():
    if not CONFIG.get("session_filter",False): return True,"Session filter OFF"
    h=datetime.now().hour
    if CONFIG["session_start_hour"]<=h<CONFIG["session_end_hour"]: return True,f"In session"
    return False,f"Outside session ({h}:00 CT)"

def risk_ok(state):
    today=date_str()
    if state.get("last_reset")!=today:
        state["daily_pnl"]=0.0;state["last_reset"]=today;state["trade_count_today"]=0;log("  🔄 Daily reset")
    if state["daily_pnl"]<-CONFIG["starting_capital"]*CONFIG["max_daily_loss_pct"]:
        log("  🛑 Daily loss limit");return False
    if len(state["open_trades"])>=CONFIG["max_open_trades"]:
        log(f"  ⏸️  Max {CONFIG['max_open_trades']} positions");return False
    ok,msg=session_ok()
    if not ok: log(f"  ⏰ {msg}");return False
    return True

def check_exits(client,state):
    if not state["open_trades"]: return
    log("  Checking positions...")
    for pair,pos in list(state["open_trades"].items()):
        px=get_price(client,pair)
        if px==0: continue
        if px>pos.get("highest_price",px): pos["highest_price"]=px;state["open_trades"][pair]=pos
        entry=pos["entry_price"];highest=pos.get("highest_price",px);atr=pos.get("atr",px*0.02)
        sl=pos.get("stop_loss",entry-atr*CONFIG["atr_sl_mult"])
        tp=pos.get("take_profit",entry+atr*CONFIG["atr_tp_mult"])
        trail=highest*(1-CONFIG["trailing_stop_pct"]);ch=(px-entry)/entry*100
        log(f"  📊 {pair}: ${px:,.2f} | {ch:+.1f}% | SL:${sl:,.2f} | TP:${tp:,.2f}")
        if px<=sl: log(f"  🛑 ATR STOP LOSS {pair}");place_order(client,pair,"SELL",pos["usd_invested"],px,state,atr=atr)
        elif px>=tp: log(f"  🎯 ATR TAKE PROFIT {pair}");place_order(client,pair,"SELL",pos["usd_invested"],px,state,atr=atr)
        elif px<=trail and ch>2: log(f"  📉 TRAILING STOP {pair}");place_order(client,pair,"SELL",pos["usd_invested"],px,state,atr=atr)

def explain_analysis(pair,signal):
    ind=signal["indicators"];px=ind.get("price",0);rv=ind.get("rsi",50);mt=ind.get("macd_trend","neutral")
    obv=ind.get("obv","neutral");es=ind.get("ema_sig","unknown");e200=ind.get("ema200",0)
    adx=ind.get("adx",0);atr=ind.get("atr",0);nb=ind.get("buy_ct",0);ns=ind.get("sell_ct",0)
    sl=ind.get("sl",0);tp=ind.get("tp",0)
    log(f"  {pair.split('-')[0]} @ ${px:,.2f}")
    log(f"  RSI: {rv:.0f} {'oversold ✅' if rv<35 else 'overbought ✅' if rv>65 else 'neutral'}")
    log(f"  MACD: {mt} {'✅' if mt!='neutral' else ''}")
    log(f"  OBV: {obv} {'✅' if obv!='neutral' else ''}")
    log(f"  200 EMA: ${e200:,.2f} — {es} {'✅' if es in ['above','below'] else ''}")
    log(f"  ADX: {adx:.0f} {'✅ trending' if adx>=25 else '⚠️ ranging — will skip'}")
    log(f"  Signals: {nb} buy / {ns} sell (need 3+)")
    if sl and tp: log(f"  ATR exits: SL ${sl:,.2f} | TP ${tp:,.2f}")
    for b in signal["blocked_by"]: log(f"  ⛔ {b}")
    d=signal["direction"];c=signal["confidence"]
    if d=="HOLD" and not signal["blocked_by"]: log(f"  → Waiting: {nb}B/{ns}S — need 3+")
    elif d=="BUY": log(f"  → BUY ✅ {c}% | {signal['explanation']}")
    elif d=="SELL": log(f"  → SELL 🔴 {c}%")

def report(state,alpha):
    st=state["stats"];perf=state["performance"];t=st["total_trades"];wr=(st["wins"]/t*100) if t else 0
    gr=(state["capital"]-CONFIG["starting_capital"])/CONFIG["starting_capital"]*100
    sec(f"PORTFOLIO — {'PAPER TRADE' if CONFIG['paper_trade'] else 'LIVE'}")
    log(f"  Capital: ${state['capital']:,.2f} ({'▲' if gr>=0 else '▼'}{abs(gr):.1f}%) | P/L: ${state['total_pnl']:+,.2f} | Today: ${state['daily_pnl']:+,.2f}")
    log(f"  Trades: {t} | W/L: {st['wins']}/{st['losses']} | WR: {wr:.1f}% | Drawdown: {perf.get('max_drawdown',0):.1f}%")
    log(f"  F&G: {alpha.get('fg',50)}/100 {alpha.get('fg_label','—')} | DOM: {alpha.get('dominance',50):.1f}% | Fund: {alpha.get('funding',0):.4f}%")
    log(f"  Session filter: {'ON' if CONFIG.get('session_filter') else 'OFF (24/7)'}")
    if state["open_trades"]:
        for pair,pos in state["open_trades"].items(): log(f"  • {pair} ${pos['usd_invested']:.2f} @ ${pos['entry_price']:,.2f} | SL ${pos.get('stop_loss',0):,.2f} | TP ${pos.get('take_profit',0):,.2f}")
    else: log("  Holding: Cash — waiting for signal")
    div("═")

def scan(client,state):
    load_config_overrides();sec(f"SCAN — {time_str()}")
    alpha=fetch_alpha();fg=alpha["fg"];fgl=alpha["fg_label"]
    state["last_fg"]=fg;state["last_fg_label"]=fgl;state["last_dominance"]=alpha["dominance"];state["last_funding"]=alpha["funding"]
    log(f"  F&G: {fg}/100 {fgl} | DOM: {alpha['dominance']:.1f}% | Fund: {alpha['funding']:.4f}%");log("")
    is_ef=fg<=25;is_eg=fg>=75
    if is_ef: log("  ✅ EXTREME FEAR — favorable conditions")
    if is_eg: log("  ⚠️  EXTREME GREED — being cautious")
    check_exits(client,state)
    if not risk_ok(state): save_state(state);return
    for pair in CONFIG["pairs"]:
        div();log(f"  {pair.split('-')[0]}");div()
        candles=get_candles(client,pair)
        if not candles: log("  No data");continue
        closes,highs,lows,volumes=to_lists(candles)
        signal=analyze(pair,closes,highs,lows,volumes,client,alpha,state)
        explain_analysis(pair,signal)
        px=signal["indicators"].get("price",0);atr=signal["indicators"].get("atr",0)
        if px==0: continue
        if signal["direction"]=="BUY" and pair not in state["open_trades"]:
            if is_eg: log("  ⏸️  Skip — extreme greed")
            elif signal["confidence"]<CONFIG["min_confidence"]: log(f"  ⏸️  Confidence {signal['confidence']}% < {CONFIG['min_confidence']}%")
            else:
                conf=signal["confidence"]
                if is_ef: conf=min(100,conf+10);log(f"  ⚡ Fear boost → {conf}%")
                usd=pos_size(state,conf,atr,px)
                if usd>=10: place_order(client,pair,"BUY",usd,px,state,signal["explanation"],atr)
                else: log(f"  Too small ${usd:.2f}")
        elif signal["direction"]=="SELL" and pair in state["open_trades"]:
            place_order(client,pair,"SELL",state["open_trades"][pair]["usd_invested"],px,state,signal["explanation"],atr)
    report(state,alpha)
    nxt=(datetime.now()+timedelta(minutes=CONFIG["scan_interval_minutes"])).strftime("%I:%M %p")
    log(f"  ✅ Next scan at {nxt}.\n");save_state(state)

def main():
    sec("EDGE BOT v5")
    log(f"  Mode: {'PAPER TRADE' if CONFIG['paper_trade'] else '⚡ LIVE'}")
    log(f"  Signals: RSI + MACD + OBV + 200 EMA (need 3 of 4)")
    log(f"  Filters: ADX >= {CONFIG['adx_threshold']} | BTC 200 EMA | Session: {'ON' if CONFIG.get('session_filter') else 'OFF'}")
    log(f"  Exits: ATR x{CONFIG['atr_sl_mult']} SL | ATR x{CONFIG['atr_tp_mult']} TP | {CONFIG['trailing_stop_pct']*100:.0f}% trail")
    log(f"  Risk: {CONFIG['max_risk_per_trade']*100:.0f}%/trade | Daily limit: {CONFIG['max_daily_loss_pct']*100:.1f}% | Max {CONFIG['max_open_trades']} positions")
    div("═");log("")
    client=load_client();state=load_state()
    log("  ✅ Connected | Running first scan...\n")
    scan(client,state)
    schedule.every(CONFIG["scan_interval_minutes"]).minutes.do(scan,client,state)
    nxt=(datetime.now()+timedelta(minutes=CONFIG["scan_interval_minutes"])).strftime("%I:%M %p")
    log(f"  Live. Next scan {nxt}. Ctrl+C to stop.\n")
    while True: schedule.run_pending();time.sleep(30)

if __name__=="__main__": main()
