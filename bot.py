#!/usr/bin/env python3
import json,time,math,schedule,urllib.request
from datetime import datetime,timedelta
from pathlib import Path
from coinbase.rest import RESTClient

CONFIG={"paper_trade":True,"starting_capital":1500.0,"max_risk_per_trade":0.01,"max_daily_loss_pct":0.03,"max_open_trades":4,"min_confidence":72,"min_technical_signals":3,"min_smart_money":1,"pairs":["BTC-USD","ETH-USD","SOL-USD","LINK-USD","GRT-USD","AVAX-USD","UNI-USD"],"rsi_oversold":35,"rsi_overbought":65,"bb_std":2.0,"momentum_periods":14,"adx_period":14,"adx_threshold":25,"scan_interval_minutes":15,"candle_granularity":"ONE_HOUR","candle_count":100,"api_key_file":"cdp_api_key.json","trailing_stop_pct":0.03,"take_profit_pct":0.06,"stop_loss_pct":0.04}
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
        s.setdefault("trailing_highs",{});s.setdefault("trade_count_today",0)
        s.setdefault("last_trade_date",date_str())
        s.setdefault("performance",{"total_trades":0,"wins":0,"losses":0,"total_pnl":0.0,"max_drawdown":0.0,"peak_capital":CONFIG["starting_capital"]})
        return s
    return {"capital":CONFIG["starting_capital"],"open_trades":{},"trade_history":[],"daily_pnl":0.0,"total_pnl":0.0,"last_reset":date_str(),"last_trade_date":date_str(),"trade_count_today":0,"trailing_highs":{},"stats":{"wins":0,"losses":0,"total_trades":0},"performance":{"total_trades":0,"wins":0,"losses":0,"total_pnl":0.0,"max_drawdown":0.0,"peak_capital":CONFIG["starting_capital"]},"last_fg":50,"last_fg_label":"Neutral","last_dominance":50,"last_funding":0.0,"last_explanation":None}
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
    if ratio>1.3: return 1,f"Strong buy pressure ({ratio:.2f})"
    elif ratio<0.7: return -1,f"Strong sell pressure ({ratio:.2f})"
    return 0,f"Balanced ({ratio:.2f})"
def fetch_alpha():
    fg,fgl=get_fear_greed();funding=get_funding_rate();dominance=get_btc_dominance()
    flow,fdesc=get_exchange_flow()
    return{"fg":fg,"fg_label":fgl,"funding":funding,"dominance":dominance,"exchange_flow":flow,"exchange_flow_desc":fdesc}
def get_candles(client,pair,granularity=None,count=None):
    try:
        gs=granularity or CONFIG["candle_granularity"];cnt=count or CONFIG["candle_count"]
        gm={"ONE_MINUTE":60,"FIVE_MINUTE":300,"ONE_HOUR":3600,"ONE_DAY":86400}
        end=int(time.time());start=end-gm.get(gs,3600)*cnt
        r=client.get_candles(product_id=pair,start=str(start),end=str(end),granularity=gs)
        return sorted(r.candles if hasattr(r,"candles") else [],key=lambda c:int(c.start))
    except Exception as e: log(f"  No data {pair}: {e}");return []
def to_lists(c): return [float(x.close) for x in c],[float(x.high) for x in c],[float(x.low) for x in c],[float(x.volume) for x in c]
def get_price(client,pair):
    try:
        r=client.get_best_bid_ask(product_ids=[pair])
        for p in r.pricebooks:
            if p.product_id==pair:
                b=float(p.bids[0].price) if p.bids else 0;a=float(p.asks[0].price) if p.asks else 0;return(b+a)/2
    except: pass
    return 0.0
def sma(d,n): return sum(d[-n:])/n if len(d)>=n else 0.0
def ema(d,n):
    if len(d)<n: return d[-1] if d else 0.0
    k=2/(n+1);e=sum(d[:n])/n
    for v in d[n:]: e=v*k+e*(1-k)
    return e
def sdv(d,n):
    if len(d)<n: return 0.0
    s=d[-n:];m=sum(s)/n;return math.sqrt(sum((x-m)**2 for x in s)/n)
def rsi(closes,n=14):
    if len(closes)<n+1: return 50.0
    g=[abs(closes[i]-closes[i-1]) for i in range(-n,0) if closes[i]>closes[i-1]]
    l=[abs(closes[i]-closes[i-1]) for i in range(-n,0) if closes[i]<=closes[i-1]]
    ag=sum(g)/n if g else 0;al=sum(l)/n if l else 1e-9;return 100-(100/(1+ag/al))
def bb(closes,n=20,s=2.0): m=sma(closes,n);sd=sdv(closes,n);return m-s*sd,m,m+s*sd
def momentum_score(closes,n=14):
    if len(closes)<n+1: return 0.0
    p=closes[-(n+1)];return(closes[-1]-p)/p if p else 0.0
def obv_signal(closes,volumes):
    if len(closes)<2: return "neutral"
    half=len(closes)//2
    o1=sum(volumes[i] if closes[i]>closes[i-1] else -volumes[i] if closes[i]<closes[i-1] else 0 for i in range(1,half))
    o2=sum(volumes[i] if closes[i]>closes[i-1] else -volumes[i] if closes[i]<closes[i-1] else 0 for i in range(half,len(closes)))
    if o2>o1*1.1: return "bullish"
    elif o2<o1*0.9: return "bearish"
    return "neutral"
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
def volatility_regime(closes,n=20):
    if len(closes)<n+1: return "medium"
    rets=[(closes[i]-closes[i-1])/closes[i-1] for i in range(-n,0)]
    v=math.sqrt(sum(r**2 for r in rets)/n)
    return "low" if v<0.01 else "high" if v>0.03 else "medium"
def higher_tf_trend(client,pair):
    candles=get_candles(client,pair,"ONE_HOUR",50)
    if not candles: return "unknown"
    closes=[float(c.close) for c in candles]
    if len(closes)<20: return "unknown"
    e20=ema(closes,20);e50=ema(closes,min(50,len(closes)));px=closes[-1]
    if px>e20>e50: return "uptrend"
    elif px<e20<e50: return "downtrend"
    return "sideways"
def btc_ok(client,pair,direction):
    if pair=="BTC-USD": return True,"BTC itself"
    try:
        c=get_candles(client,"BTC-USD","ONE_HOUR",5)
        if not c: return True,"No BTC data"
        cl=[float(x.close) for x in c];mv=(cl[-1]-cl[0])/cl[0]*100
        if direction=="BUY" and mv<-2: return False,f"BTC dropping {mv:.1f}%"
        elif direction=="SELL" and mv>2: return False,f"BTC rising {mv:.1f}%"
        return True,f"BTC {mv:+.1f}% OK"
    except: return True,"BTC check failed"
def ob_imbalance(client,pair):
    try:
        r=client.get_best_bid_ask(product_ids=[pair])
        for p in r.pricebooks:
            if p.product_id==pair:
                if not p.bids or not p.asks: return 0,"No data"
                bid=float(p.bids[0].price);ask=float(p.asks[0].price);sp=(ask-bid)/bid*100
                if sp>0.5: return -1,f"Wide spread {sp:.3f}%"
                elif sp<0.1: return 1,f"Tight spread {sp:.3f}%"
                return 0,f"Normal spread {sp:.3f}%"
    except: pass
    return 0,"No data"
def analyze(pair,closes,highs,lows,volumes,client,alpha):
    if len(closes)<30: return{"direction":"HOLD","confidence":0,"confluence":[],"smart_money":[],"blocked_by":[],"indicators":{},"explanation":"","tech_buy_ct":0,"tech_sell_ct":0,"smart_buy_ct":0,"smart_sell_ct":0}
    rv=rsi(closes);lo,mid,hi=bb(closes,20,CONFIG["bb_std"]);mv=momentum_score(closes,CONFIG["momentum_periods"])
    obv_t=obv_signal(closes,volumes);adxv=calc_adx(highs,lows,closes,CONFIG["adx_period"])
    rg=volatility_regime(closes);px=closes[-1];dev=(px-mid)/mid if mid else 0
    tb,ts=[],[]
    if rv<CONFIG["rsi_oversold"]: tb.append(("RSI_OVERSOLD",f"RSI {rv:.0f} oversold"))
    elif rv>CONFIG["rsi_overbought"]: ts.append(("RSI_OVERBOUGHT",f"RSI {rv:.0f} overbought"))
    if px<lo and dev<-0.02: tb.append(("BB_LOWER","Price below lower Bollinger Band"))
    elif px>hi and dev>0.02: ts.append(("BB_UPPER","Price above upper Bollinger Band"))
    if mv>0.02: tb.append(("MOMENTUM_BULL",f"Bullish momentum +{mv*100:.1f}%"))
    elif mv<-0.02: ts.append(("MOMENTUM_BEAR",f"Bearish momentum {mv*100:.1f}%"))
    if obv_t=="bullish": tb.append(("OBV_BULL","OBV shows strong buying volume"))
    elif obv_t=="bearish": ts.append(("OBV_BEAR","OBV shows strong selling volume"))
    if rg=="low" and mv>0.01: tb.append(("VOL_REGIME","Low volatility + positive momentum"))
    elif rg=="high" and mv<-0.01: ts.append(("VOL_REGIME","High volatility + negative momentum"))
    nb=len(tb);ns=len(ts)
    smb,sms=[],[]
    flow=alpha.get("exchange_flow",0);fdesc=alpha.get("exchange_flow_desc","")
    if flow>0: smb.append(("FLOW",fdesc))
    elif flow<0: sms.append(("FLOW",fdesc))
    obs,obdesc=ob_imbalance(client,pair)
    if obs>0: smb.append(("OB",obdesc))
    elif obs<0: sms.append(("OB",obdesc))
    funding=alpha.get("funding",0)
    if funding<-0.03: smb.append(("FUND",f"Negative funding {funding:.4f}%"))
    elif funding>0.03: sms.append(("FUND",f"Positive funding {funding:.4f}%"))
    smbn=len(smb);smsn=len(sms)
    adx_ok=adxv>=CONFIG["adx_threshold"];size_mult=0.5 if not adx_ok else 1.0
    htf=higher_tf_trend(client,pair)
    blocked=[];direction="HOLD";confluence=[];smart_money=[];explanation=""
    if nb>=CONFIG["min_technical_signals"] and smbn>=CONFIG["min_smart_money"] and nb>ns:
        bok,bdesc=btc_ok(client,pair,"BUY")
        if not bok: blocked.append(f"BTC: {bdesc}")
        elif htf=="downtrend": blocked.append("1H downtrend — skip buy")
        else:
            direction="BUY";confluence=[t[1] for t in tb];smart_money=[s[1] for s in smb]
            explanation=f"Entered LONG: {', '.join(confluence[:3])}. Smart money: {', '.join(smart_money[:2])}. 1H: {htf}. ADX: {adxv:.0f}."
    elif ns>=CONFIG["min_technical_signals"] and smsn>=CONFIG["min_smart_money"] and ns>nb:
        bok,bdesc=btc_ok(client,pair,"SELL")
        if not bok: blocked.append(f"BTC: {bdesc}")
        elif htf=="uptrend": blocked.append("1H uptrend — skip sell")
        else:
            direction="SELL";confluence=[t[1] for t in ts];smart_money=[s[1] for s in sms]
            explanation=f"Entered SHORT: {', '.join(confluence[:3])}. Smart money: {', '.join(smart_money[:2])}. 1H: {htf}. ADX: {adxv:.0f}."
    total=(nb+smbn) if direction=="BUY" else(ns+smsn)
    confidence=min(100,40+total*10) if direction!="HOLD" else 0
    ind={"rsi":rv,"bb_lower":lo,"bb_mid":mid,"bb_upper":hi,"momentum":mv,"obv_trend":obv_t,"adx":adxv,"regime":rg,"price":px,"htf":htf,"size_mult":size_mult}
    return{"direction":direction,"confidence":confidence,"confluence":confluence,"smart_money":smart_money,"blocked_by":blocked,"indicators":ind,"explanation":explanation,"tech_buy_ct":nb,"tech_sell_ct":ns,"smart_buy_ct":smbn,"smart_sell_ct":smsn}
def pos_size(state,confidence,signal,px):
    capital=state["capital"];base=capital*CONFIG["max_risk_per_trade"]
    mult=1.5 if confidence>=90 else 1.2 if confidence>=80 else 1.0 if confidence>=72 else 0.7
    adx_mult=signal["indicators"].get("size_mult",1.0)
    return min(round(base*mult*adx_mult,2),capital*0.05)
def place_order(client,pair,side,usd,price,state,explanation=""):
    mode="PAPER TRADE" if CONFIG["paper_trade"] else "LIVE TRADE"
    if side=="BUY":
        log(f"  💰 {mode} — Buying ${usd:.2f} of {pair} at ${price:,.2f}  ({usd/state['capital']*100:.1f}% of capital)")
        if explanation: log(f"  📝 {explanation}")
    else:
        entry=state["open_trades"].get(pair,{}).get("entry_price",price);pct=(price-entry)/entry*100
        log(f"  💸 {mode} — Selling {pair} at ${price:,.2f} ({'gained' if pct>0 else 'lost'} {abs(pct):.1f}%)")
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
        state["open_trades"][pair]={"entry_price":price,"usd_invested":usd,"entry_time":now_str(),"highest_price":price,"explanation":explanation}
        state["capital"]-=usd;state["trade_count_today"]=state.get("trade_count_today",0)+1
        if explanation: save_explanation({"time":now_str(),"pair":pair,"side":"BUY","price":price,"usd":usd,"explanation":explanation})
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
def risk_ok(state):
    today=date_str()
    if state.get("last_reset")!=today:
        state["daily_pnl"]=0.0;state["last_reset"]=today;state["trade_count_today"]=0;log("  🔄 Daily reset")
    if state["daily_pnl"]<-CONFIG["starting_capital"]*CONFIG["max_daily_loss_pct"]: log("  🛑 Daily loss limit");return False
    if len(state["open_trades"])>=CONFIG["max_open_trades"]: log("  ⏸️  Max trades");return False
    return True
def check_exits(client,state):
    if not state["open_trades"]: return
    log("  Checking positions...")
    for pair,pos in list(state["open_trades"].items()):
        px=get_price(client,pair)
        if px==0: continue
        if px>pos.get("highest_price",px): pos["highest_price"]=px;state["open_trades"][pair]=pos
        entry=pos["entry_price"];highest=pos.get("highest_price",px)
        ch=(px-entry)/entry;trail=(px-highest)/highest if highest>0 else 0
        if ch<=-CONFIG["stop_loss_pct"]: log(f"  🛑 STOP LOSS {pair} {ch*100:.1f}%");place_order(client,pair,"SELL",pos["usd_invested"],px,state)
        elif ch>=0.02 and trail<=-CONFIG["trailing_stop_pct"]: log(f"  📉 TRAIL STOP {pair}");place_order(client,pair,"SELL",pos["usd_invested"],px,state)
        elif ch>=CONFIG["take_profit_pct"]: log(f"  🎯 TAKE PROFIT {pair} +{ch*100:.1f}%");place_order(client,pair,"SELL",pos["usd_invested"],px,state)
        else: log(f"  📊 {pair}: {ch*100:+.1f}% | High: ${highest:,.2f} | Trail: {trail*100:+.1f}%")
def explain_analysis(pair,signal):
    ind=signal["indicators"];px=ind.get("price",0);rv=ind.get("rsi",50);mv=ind.get("momentum",0)*100
    obv_t=ind.get("obv_trend","neutral");adxv=ind.get("adx",0);htf=ind.get("htf","unknown")
    log(f"  {pair.split('-')[0]} @ ${px:,.2f}")
    log(f"  RSI {rv:.0f} {'OVERSOLD ✅' if rv<35 else 'OVERBOUGHT ✅' if rv>65 else 'Neutral'} | Mom {'BULL ✅' if mv>2 else 'BEAR ✅' if mv<-2 else 'flat'} {mv:+.1f}%")
    log(f"  OBV {'BULL ✅' if obv_t=='bullish' else 'BEAR ✅' if obv_t=='bearish' else 'neutral'} | ADX {adxv:.0f} {'✅' if adxv>=25 else '⚠️ range'} | 1H: {htf}")
    log(f"  Tech: {signal['tech_buy_ct']}B/{signal['tech_sell_ct']}S | Smart $: {signal['smart_buy_ct']}B/{signal['smart_sell_ct']}S")
    for b in signal["blocked_by"]: log(f"  ⛔ {b}")
    d=signal["direction"];c=signal["confidence"]
    if d=="HOLD" and not signal["blocked_by"]: log("  → Waiting: need 3+ tech AND 1+ smart money")
    elif d=="BUY": log(f"  → BUY ✅ {c}% | {signal['explanation']}")
    elif d=="SELL": log(f"  → SELL 🔴 {c}% | {signal['explanation']}")
def report(state,alpha):
    st=state["stats"];perf=state["performance"];t=st["total_trades"];wr=(st["wins"]/t*100) if t else 0
    gr=(state["capital"]-CONFIG["starting_capital"])/CONFIG["starting_capital"]*100
    sec(f"PORTFOLIO — {'PAPER TRADE' if CONFIG['paper_trade'] else 'LIVE'}")
    log(f"  Capital: ${state['capital']:,.2f} ({'▲' if gr>=0 else '▼'}{abs(gr):.1f}%) | P/L: ${state['total_pnl']:+,.2f} | Today: ${state['daily_pnl']:+,.2f}")
    log(f"  Trades: {t} | Wins: {st['wins']} | Losses: {st['losses']} | WR: {wr:.1f}% | Drawdown: {perf.get('max_drawdown',0):.1f}%")
    log(f"  F&G: {alpha.get('fg',50)}/100 {alpha.get('fg_label','—')} | DOM: {alpha.get('dominance',50):.1f}% | Fund: {alpha.get('funding',0):.4f}%")
    if state["open_trades"]:
        for pair,pos in state["open_trades"].items(): log(f"  • {pair} ${pos['usd_invested']:.2f} @ ${pos['entry_price']:,.2f}")
    else: log("  Holding: Nothing — waiting for signal")
    div("═")
def scan(client,state):
    sec(f"SCAN — {time_str()}")
    alpha=fetch_alpha()
    fg=alpha["fg"];fgl=alpha["fg_label"]
    state["last_fg"]=fg;state["last_fg_label"]=fgl;state["last_dominance"]=alpha["dominance"];state["last_funding"]=alpha["funding"]
    log(f"  F&G: {fg}/100 {fgl} | DOM: {alpha['dominance']:.1f}% | Fund: {alpha['funding']:.4f}% | Flow: {alpha.get('exchange_flow_desc','—')}")
    is_ef=fg<=25;is_eg=fg>=75;is_hd=alpha["dominance"]>58
    if is_ef: log("  ✅ EXTREME FEAR — boosting BUY confidence")
    if is_eg: log("  ⚠️  EXTREME GREED — skipping BUYs")
    if is_hd: log("  ⚠️  HIGH BTC DOM — altcoins under pressure")
    check_exits(client,state)
    if not risk_ok(state): save_state(state);return
    for pair in CONFIG["pairs"]:
        div();log(f"  {pair.split('-')[0]}");div()
        candles=get_candles(client,pair)
        if not candles: log("  No data");continue
        closes,highs,lows,volumes=to_lists(candles)
        signal=analyze(pair,closes,highs,lows,volumes,client,alpha)
        explain_analysis(pair,signal)
        px=signal["indicators"].get("price",0)
        if px==0: continue
        if signal["direction"]=="BUY" and pair not in state["open_trades"]:
            if is_eg: log("  ⏸️  Skip — extreme greed")
            elif is_hd and pair not in ["BTC-USD","ETH-USD"]: log(f"  ⏸️  Skip — BTC dom high")
            elif signal["confidence"]<CONFIG["min_confidence"]: log(f"  ⏸️  Confidence {signal['confidence']}% < {CONFIG['min_confidence']}%")
            else:
                conf=signal["confidence"]
                if is_ef: conf=min(100,conf+15);log(f"  ⚡ Boosted to {conf}%")
                usd=pos_size(state,conf,signal,px)
                if usd>=10: place_order(client,pair,"BUY",usd,px,state,signal["explanation"])
                else: log(f"  Too small ${usd:.2f}")
        elif signal["direction"]=="SELL" and pair in state["open_trades"]:
            place_order(client,pair,"SELL",state["open_trades"][pair]["usd_invested"],px,state,signal["explanation"])
    report(state,alpha)
    nxt=(datetime.now()+timedelta(minutes=CONFIG["scan_interval_minutes"])).strftime("%I:%M %p")
    log(f"  ✅ Next scan at {nxt}.\n")
    save_state(state)
def main():
    sec("EDGE BOT v4")
    log(f"  Mode: {'PAPER (60-90 day test)' if CONFIG['paper_trade'] else '⚡ LIVE'}")
    log(f"  Capital: ${CONFIG['starting_capital']:,.2f} | Pairs: {', '.join(CONFIG['pairs'])}")
    log(f"  Entry: 3+ tech + 1+ smart money | Min conf: {CONFIG['min_confidence']}%")
    log(f"  Risk: {CONFIG['max_risk_per_trade']*100:.0f}%/trade | Daily stop: {CONFIG['max_daily_loss_pct']*100:.0f}%")
    div("═");log("")
    client=load_client();state=load_state()
    log("  ✅ Connected to Coinbase | Running first scan...\n")
    scan(client,state)
    schedule.every(CONFIG["scan_interval_minutes"]).minutes.do(scan,client,state)
    nxt=(datetime.now()+timedelta(minutes=CONFIG["scan_interval_minutes"])).strftime("%I:%M %p")
    log(f"  Live. Next scan {nxt}. Ctrl+C to stop.\n")
    while True: schedule.run_pending();time.sleep(30)
if __name__=="__main__": main()
