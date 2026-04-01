#!/usr/bin/env python3
import json,time,math,schedule
from datetime import datetime,timedelta
from pathlib import Path
from coinbase.rest import RESTClient

CONFIG={"paper_trade":True,"starting_capital":1500.0,"max_position_pct":0.03,"max_daily_loss_pct":0.05,"max_open_trades":5,"pairs":["BTC-USD","ETH-USD","SOL-USD","LINK-USD","GRT-USD", "AVAX-USD", "UNI-USD"],"rsi_oversold":32,"rsi_overbought":68,"bb_std":2.0,"momentum_periods":14,"mean_rev_threshold":0.025,"min_confluence":2,"scan_interval_minutes":15,"candle_granularity":"ONE_HOUR","candle_count":100,"api_key_file":"cdp_api_key.json"}
STOP_LOSS_PCT=0.04;TAKE_PROFIT_PCT=0.06
LOG_FILE=Path(__file__).parent/"bot_log.txt"
STATE_FILE=Path(__file__).parent/"state.json"

def now_str(): return datetime.now().strftime("%B %d, %Y  %I:%M:%S %p")
def time_str(): return datetime.now().strftime("%I:%M %p")
def log(msg):
    line=f"[{now_str()}]  {msg}";print(line)
    open(LOG_FILE,"a").write(line+"\n")
def div(c="─"): log(c*60)
def sec(t): div("═");log(f"  {t}");div("═")

def load_state():
    if STATE_FILE.exists():
        return json.load(open(STATE_FILE))
    return {"capital":CONFIG["starting_capital"],"open_trades":{},"trade_history":[],"daily_pnl":0.0,"total_pnl":0.0,"last_reset":datetime.now().date().isoformat(),"stats":{"wins":0,"losses":0,"total_trades":0}}
def save_state(s): json.dump(s,open(STATE_FILE,"w"),indent=2,default=str)

import urllib.request

def get_fear_greed():
    try:
        with urllib.request.urlopen('https://api.alternative.me/fng/', timeout=5) as r:
            import json
            d = json.loads(r.read())
            val = int(d['data'][0]['value'])
            label = d['data'][0]['value_classification']
            return val, label
    except:
        return 50, 'Neutral'

def get_funding_rate():
    try:
        with urllib.request.urlopen('https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT', timeout=5) as r:
            import json
            d = json.loads(r.read())
            rate = float(d.get('lastFundingRate', d.get('fundingRate', 0))) * 100
            return rate
    except:
        return 0.0

def get_btc_dominance():
    try:
        with urllib.request.urlopen('https://api.coingecko.com/api/v3/global', timeout=5) as r:
            import json
            d = json.loads(r.read())
            return float(d['data']['market_cap_percentage']['btc'])
    except:
        return 50.0

def get_alpha_data():
    fg, fg_label = get_fear_greed()
    funding = get_funding_rate()
    dominance = get_btc_dominance()
    return {
        'fear_greed': fg,
        'fear_greed_label': fg_label,
        'funding_rate': funding,
        'btc_dominance': dominance
    }

def load_client():
    k=json.load(open(Path(__file__).parent/CONFIG["api_key_file"]))
    return RESTClient(api_key=k["name"],api_secret=k["privateKey"])

def get_candles(client,pair):
    try:
        g={"ONE_MINUTE":60,"FIVE_MINUTE":300,"ONE_HOUR":3600,"ONE_DAY":86400}
        end=int(time.time());start=end-g.get(CONFIG["candle_granularity"],3600)*CONFIG["candle_count"]
        r=client.get_candles(product_id=pair,start=str(start),end=str(end),granularity=CONFIG["candle_granularity"])
        return sorted(r.candles if hasattr(r,"candles") else [],key=lambda c:int(c.start))
    except Exception as e: log(f"  Could not get data for {pair}: {e}");return []

def lists(candles): return [float(c.close) for c in candles],[float(c.high) for c in candles],[float(c.low) for c in candles],[float(c.volume) for c in candles]
def get_price(client,pair):
    try:
        r=client.get_best_bid_ask(product_ids=[pair])
        for p in r.pricebooks:
            if p.product_id==pair:
                b=float(p.bids[0].price) if p.bids else 0;a=float(p.asks[0].price) if p.asks else 0;return (b+a)/2
    except: pass
    return 0.0

def sma(d,n): return sum(d[-n:])/n if len(d)>=n else 0.0
def sdv(d,n):
    if len(d)<n: return 0.0
    s=d[-n:];m=sum(s)/n;return math.sqrt(sum((x-m)**2 for x in s)/n)
def rsi(c,n=14):
    if len(c)<n+1: return 50.0
    g=[abs(c[i]-c[i-1]) for i in range(-n,0) if c[i]>c[i-1]];l=[abs(c[i]-c[i-1]) for i in range(-n,0) if c[i]<=c[i-1]]
    ag=sum(g)/n if g else 0;al=sum(l)/n if l else 1e-9;return 100-(100/(1+ag/al))
def bb(c,n=20,s=2.0): m=sma(c,n);sd=sdv(c,n);return m-s*sd,m,m+s*sd
def mom(c,n=14): return (c[-1]-c[-n-1])/c[-n-1] if len(c)>n and c[-n-1] else 0.0
def vsurge(v,n=20): avg=sum(v[-n-1:-1])/n if len(v)>n else 1;return v[-1]/avg if avg else 1.0
def regime(c,n=20):
    if len(c)<n+1: return "medium"
    r=[(c[i]-c[i-1])/c[i-1] for i in range(-n,0)];v=math.sqrt(sum(x**2 for x in r)/n)
    return "low" if v<0.01 else "high" if v>0.03 else "medium"

def analyze(pair,closes,highs,lows,volumes):
    if len(closes)<30: return {"direction":"HOLD","confidence":0,"confluence":[],"indicators":{}}
    rv=rsi(closes);lo,mid,hi=bb(closes,20,CONFIG["bb_std"]);mv=mom(closes,CONFIG["momentum_periods"])
    vv=vsurge(volumes);rg=regime(closes);px=closes[-1];dev=(px-mid)/mid if mid else 0
    buys=[];sells=[]
    if rv<CONFIG["rsi_oversold"]: buys.append("RSI_OVERSOLD")
    elif rv>CONFIG["rsi_overbought"]: sells.append("RSI_OVERBOUGHT")
    if px<lo and dev<-CONFIG["mean_rev_threshold"]: buys.append("BB_LOWER_TOUCH")
    elif px>hi and dev>CONFIG["mean_rev_threshold"]: sells.append("BB_UPPER_TOUCH")
    if mv>0.02: buys.append("MOMENTUM_BULL")
    elif mv<-0.02: sells.append("MOMENTUM_BEAR")
    if vv>1.5:
        if buys: buys.append("VOLUME_SURGE_CONFIRM")
        if sells: sells.append("VOLUME_SURGE_CONFIRM")
    if rg=="high": buys=[s for s in buys if "BB" in s or "RSI" in s];sells=[s for s in sells if "BB" in s or "RSI" in s]
    nb=len(set(buys));ns=len(set(sells))
    ind={"rsi":rv,"bb_lower":lo,"bb_mid":mid,"bb_upper":hi,"momentum":mv,"vol_surge":vv,"regime":rg,"price":px}
    if nb>=CONFIG["min_confluence"] and nb>ns: return {"direction":"BUY","confidence":min(100,40+nb*20),"confluence":list(set(buys)),"indicators":ind}
    if ns>=CONFIG["min_confluence"] and ns>nb: return {"direction":"SELL","confidence":min(100,40+ns*20),"confluence":list(set(sells)),"indicators":ind}
    return {"direction":"HOLD","confidence":0,"confluence":[],"indicators":ind}

NAMES={"RSI_OVERSOLD":"RSI showing oversold — coin is beaten down, possible bounce","RSI_OVERBOUGHT":"RSI showing overbought — may be due for a pullback","BB_LOWER_TOUCH":"Price hit the lower Bollinger Band — statistically cheap","BB_UPPER_TOUCH":"Price hit the upper Bollinger Band — statistically expensive","MOMENTUM_BULL":"Strong bullish momentum building","MOMENTUM_BEAR":"Strong bearish momentum building","VOLUME_SURGE_CONFIRM":"High trading volume confirming the move"}

def explain(signal,ind):
    px=ind.get("price",0);rv=ind.get("rsi",50);mv=ind.get("momentum",0)*100
    lo=ind.get("bb_lower",0);hi=ind.get("bb_upper",0);vv=ind.get("vol_surge",1);rg=ind.get("regime","medium")
    log(f"  Current price: ${px:,.2f}")
    log("")
    if rv<32: log(f"  1. RSI is {rv:.0f} — OVERSOLD. This coin looks beaten down. Potential bounce coming.")
    elif rv>68: log(f"  1. RSI is {rv:.0f} — OVERBOUGHT. This coin may be due for a pullback.")
    else: log(f"  1. RSI is {rv:.0f} — Neutral territory. No extreme reading right now.")
    if mv>2: log(f"  2. Momentum is BULLISH — price has climbed {mv:.1f}% recently")
    elif mv<-2: log(f"  2. Momentum is BEARISH — price has dropped {abs(mv):.1f}% recently")
    else: log(f"  2. Momentum is FLAT — price hasn't moved much recently ({mv:.1f}%)")
    if px<lo: log(f"  3. Price is BELOW the lower Bollinger Band — statistically cheap right now")
    elif px>hi: log(f"  3. Price is ABOVE the upper Bollinger Band — statistically expensive right now")
    else: log(f"  3. Price is inside the normal Bollinger Band range (${lo:,.0f} – ${hi:,.0f})")
    if vv>1.5: log(f"  4. Volume is {vv:.1f}x above average — strong interest in this move")
    else: log(f"  4. Volume is normal ({vv:.1f}x average) — nothing unusual")
    if rg=="low": log(f"  5. Market is CALM — low volatility, momentum strategies are best")
    elif rg=="high": log(f"  5. Market is CHOPPY — high volatility, only taking safer trades")
    else: log(f"  5. Market volatility is MEDIUM — all strategies active")
    log("")
    d=signal["direction"];c=signal["confidence"];cf=signal["confluence"]
    if d=="HOLD": log("  DECISION: Sitting this one out. Not enough signals agree right now.")
    elif d=="BUY":
        log(f"  DECISION: BUYING ✅  ({c}% confidence)")
        for s in cf: log(f"     • {NAMES.get(s,s)}")
    else:
        log(f"  DECISION: SELLING 🔴  ({c}% confidence)")
        for s in cf: log(f"     • {NAMES.get(s,s)}")

def pos_size(state,conf): return round(state["capital"]*min((conf/100)*CONFIG["max_position_pct"],CONFIG["max_position_pct"]),2)

def place_order(client,pair,side,usd,price,state):
    mode="PAPER TRADE" if CONFIG["paper_trade"] else "LIVE TRADE"
    if side=="BUY":
        log(f"  💰 {mode} — Buying ${usd:.2f} of {pair} at ${price:,.2f}")
        log(f"     Using {usd/state['capital']*100:.1f}% of your capital")
    else:
        entry=state["open_trades"].get(pair,{}).get("entry_price",price)
        pct=(price-entry)/entry*100
        log(f"  💸 {mode} — Selling {pair} at ${price:,.2f}")
        log(f"     Entry was ${entry:,.2f} — {'gained' if pct>0 else 'lost'} {abs(pct):.1f}%")
    if not CONFIG["paper_trade"]:
        try:
            import uuid;cid=str(uuid.uuid4())
            r=client.market_order_buy(client_order_id=cid,product_id=pair,quote_size=str(usd)) if side=="BUY" else client.market_order_sell(client_order_id=cid,product_id=pair,base_size=str(round(usd/price,8)))
            log("  ✅ Order confirmed on Coinbase!" if getattr(r,"success",True) else "  ⚠️  Check Coinbase app")
        except Exception as e: log(f"  ❌ Order error: {e}");return False
    state["trade_history"].append({"time":now_str(),"pair":pair,"side":side,"usd":usd,"price":price})
    state["stats"]["total_trades"]+=1
    if side=="BUY":
        state["open_trades"][pair]={"entry_price":price,"usd_invested":usd,"entry_time":now_str()}
        state["capital"]-=usd
    elif pair in state["open_trades"]:
        e=state["open_trades"].pop(pair);pnl=(price-e["entry_price"])/e["entry_price"]*e["usd_invested"]
        state["capital"]+=e["usd_invested"]+pnl;state["daily_pnl"]+=pnl;state["total_pnl"]+=pnl
        if pnl>0: state["stats"]["wins"]+=1;log(f"  🏆 Winning trade! Profit: ${pnl:+.2f}")
        else: state["stats"]["losses"]+=1;log(f"  📉 Losing trade. Loss: ${pnl:+.2f}")
    return True

def risk_ok(state):
    today=datetime.now().date().isoformat()
    if state["last_reset"]!=today: state["daily_pnl"]=0.0;state["last_reset"]=today;log("  🔄 New day — daily P/L counter reset")
    if state["daily_pnl"]<-CONFIG["starting_capital"]*CONFIG["max_daily_loss_pct"]:
        log("  🛑 Daily loss limit hit — pausing until tomorrow to protect your capital");return False
    if len(state["open_trades"])>=CONFIG["max_open_trades"]:
        log("  ⏸️  Max open trades reached — waiting before opening more");return False
    return True

def check_exits(client,state):
    if not state["open_trades"]: return
    log("  Checking open positions...")
    for pair,pos in list(state["open_trades"].items()):
        px=get_price(client,pair)
        if px==0: continue
        ch=(px-pos["entry_price"])/pos["entry_price"]
        if ch<=-STOP_LOSS_PCT: log(f"  🛑 STOP LOSS — {pair} down {abs(ch*100):.1f}% — selling to protect capital");place_order(client,pair,"SELL",pos["usd_invested"],px,state)
        elif ch>=TAKE_PROFIT_PCT: log(f"  🎯 TAKE PROFIT — {pair} up {ch*100:.1f}% — locking in gains");place_order(client,pair,"SELL",pos["usd_invested"],px,state)
        else: log(f"  📊 {pair}: {'+' if ch>0 else ''}{ch*100:.1f}% from entry (${pos['entry_price']:,.2f} → ${px:,.2f})")

def report(state):
    st=state["stats"];t=st["total_trades"];wr=st["wins"]/t*100 if t else 0
    g=(state["capital"]-CONFIG["starting_capital"])/CONFIG["starting_capital"]*100
    mode="PRACTICE (Paper Trade)" if CONFIG["paper_trade"] else "LIVE TRADING"
    sec(f"PORTFOLIO SUMMARY  [{mode}]")
    log(f"  Time:              {now_str()}")
    div()
    log(f"  Starting capital:  ${CONFIG['starting_capital']:>10,.2f}")
    log(f"  Current capital:   ${state['capital']:>10,.2f}  ({'▲' if g>=0 else '▼'} {abs(g):.1f}% overall)")
    log(f"  Total profit/loss: ${state['total_pnl']:>+10,.2f}")
    log(f"  Today's P/L:       ${state['daily_pnl']:>+10,.2f}")
    div()
    log(f"  Total trades:      {t}")
    log(f"  Winning trades:    {st['wins']}")
    log(f"  Losing trades:     {st['losses']}")
    log(f"  Win rate:          {wr:.1f}%")
    div()
    if state["open_trades"]:
        log("  Currently holding:")
        for pair,pos in state["open_trades"].items(): log(f"    • {pair} — ${pos['usd_invested']:.2f} at ${pos['entry_price']:,.2f} (opened {pos['entry_time']})")
    else: log("  Currently holding: Nothing — all cash, waiting for the right signal")
    div("═")

def scan(client,state):
    sec(f"NEW SCAN — {time_str()}")
    log("  Bot waking up to check the markets...");log("")
    check_exits(client,state)
    if not risk_ok(state): save_state(state);return
    for pair in CONFIG["pairs"]:
        coin=pair.split("-")[0];div();log(f"  Analyzing {coin}...");div()
        candles=get_candles(client,pair)
        if not candles: log(f"  No data for {coin} right now — skipping");continue
        closes,highs,lows,volumes=lists(candles)
        signal=analyze(pair,closes,highs,lows,volumes)
        explain(signal,signal["indicators"])
        px=signal["indicators"].get("price",0)
        if px==0: continue
        if signal["direction"]=="BUY" and pair not in state["open_trades"]:
            
            
            
            if False:
                log(f"  ⏸️  Skipping BUY on {pair} — market in extreme greed")
            # Alpha filter: skip altcoin buys during high BTC dominance
            elif high_dominance and pair not in ["BTC-USD","ETH-USD"]:
                log(f"  ⏸️  Skipping altcoin BUY on {pair} — BTC dominance too high")
            else:
                # Boost confidence during extreme fear
                conf = signal["confidence"]
                if extreme_fear:
                    conf = min(100, conf + 15)
                    log(f"  ⚡ Confidence boosted to {conf}% due to extreme fear")
                usd=pos_size(state,conf)
                if usd>=10: place_order(client,pair,"BUY",usd,px,state)
        elif signal["direction"]=="SELL" and pair in state["open_trades"]:
            place_order(client,pair,"SELL",state["open_trades"][pair]["usd_invested"],px,state)
    report(state)
    nxt=(datetime.now()+timedelta(minutes=CONFIG["scan_interval_minutes"])).strftime("%I:%M %p")
    log(f"  ✅ Scan complete. Next scan at {nxt}. You can leave this running safely.\n")
    save_state(state)

def main():
    sec("EDGE TRADING BOT — STARTING UP")
    log(f"  Mode:     {'PRACTICE — no real money at risk' if CONFIG['paper_trade'] else '⚡ LIVE TRADING'}")
    log(f"  Capital:  ${CONFIG['starting_capital']:,.2f}")
    log(f"  Pairs:    {', '.join(CONFIG['pairs'])}")
    log(f"  Scans:    Every {CONFIG['scan_interval_minutes']} minutes, 24/7")
    log(f"  Stops:    Exits if trade drops {STOP_LOSS_PCT*100:.0f}% or gains {TAKE_PROFIT_PCT*100:.0f}%")
    log(f"  Safety:   Pauses if down {CONFIG['max_daily_loss_pct']*100:.0f}% in one day")
    div("═");log("")
    log("  Connecting to your Coinbase account...")
    client=load_client();state=load_state()
    log("  ✅ Connected to Coinbase successfully")
    log("  ✅ Portfolio loaded")
    log("  ✅ Running first scan now...\n")
    scan(client,state)
    schedule.every(CONFIG["scan_interval_minutes"]).minutes.do(scan,client,state)
    nxt=(datetime.now()+timedelta(minutes=CONFIG["scan_interval_minutes"])).strftime("%I:%M %p")
    log(f"  Bot is live. Next scan at {nxt}. Press Ctrl+C to stop.\n")
    while True: schedule.run_pending();time.sleep(30)

if __name__=="__main__": main()
