#!/usr/bin/env python3
"""
EDGE Trading Bot — Clean Version
Signals: RSI + 200 EMA + ADX (3 clean conditions)
Scan: Every 60 minutes
Risk: 1% per trade, 3% daily limit, max 3 positions
Exits: ATR 1.5x stop, ATR 3x target, 3% trailing stop
"""

import json, time, math, schedule, urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from coinbase.rest import RESTClient

CONFIG = {
    "paper_trade":           True,
    "starting_capital":      1500.0,
    "max_risk_per_trade":    0.01,
    "max_daily_loss_pct":    0.03,
    "max_open_trades":       3,
    "pairs":                 ["BTC-USD","ETH-USD","SOL-USD","LINK-USD","GRT-USD","AVAX-USD","UNI-USD"],
    "rsi_oversold":          35,
    "rsi_overbought":        65,
    "adx_threshold":         25,
    "atr_sl_mult":           1.5,
    "atr_tp_mult":           3.0,
    "trailing_stop_pct":     0.03,
    "scan_interval_minutes": 60,
    "candle_granularity":    "ONE_HOUR",
    "candle_count":          220,
    "api_key_file":          "cdp_api_key.json",
}

LOG_FILE    = Path(__file__).parent / "bot_log.txt"
STATE_FILE  = Path(__file__).parent / "state.json"
TRADES_FILE = Path(__file__).parent / "trade_explanations.json"

def now_str(): return datetime.now().strftime("%B %d, %Y  %I:%M:%S %p")
def time_str(): return datetime.now().strftime("%I:%M %p")
def date_str(): return datetime.now().date().isoformat()

def log(msg):
    line = f"[{now_str()}]  {msg}"; print(line)
    open(LOG_FILE, "a").write(line + "\n")

def div(c="─"): log(c * 60)
def sec(t): div("═"); log(f"  {t}"); div("═")

def load_state():
    if STATE_FILE.exists():
        s = json.load(open(STATE_FILE))
        s.setdefault("performance", {"total_trades":0,"wins":0,"losses":0,"total_pnl":0.0,"max_drawdown":0.0,"peak_capital":CONFIG["starting_capital"]})
        return s
    return {
        "capital":     CONFIG["starting_capital"],
        "open_trades": {},
        "trade_history": [],
        "daily_pnl":   0.0,
        "total_pnl":   0.0,
        "last_reset":  date_str(),
        "stats":       {"wins":0,"losses":0,"total_trades":0},
        "performance": {"total_trades":0,"wins":0,"losses":0,"total_pnl":0.0,"max_drawdown":0.0,"peak_capital":CONFIG["starting_capital"]},
        "last_fg":     50,
        "last_fg_label": "Neutral",
        "last_dominance": 50,
        "last_funding": 0.0,
    }

def save_state(s): json.dump(s, open(STATE_FILE,"w"), indent=2, default=str)

def save_explanation(exp):
    exps = []
    if TRADES_FILE.exists():
        try: exps = json.load(open(TRADES_FILE))
        except: exps = []
    exps.insert(0, exp); exps = exps[:50]
    json.dump(exps, open(TRADES_FILE,"w"), indent=2, default=str)

def load_client():
    import os
    ak = os.environ.get("API_KEY_NAME")
    ap = os.environ.get("API_KEY_PRIVATE")
    if ak and ap: return RESTClient(api_key=ak, api_secret=ap)
    k = json.load(open(Path(__file__).parent / CONFIG["api_key_file"]))
    return RESTClient(api_key=k["name"], api_secret=k["privateKey"])

def fetch_url(url, timeout=5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read())
    except: return None

def get_fear_greed():
    d = fetch_url("https://api.alternative.me/fng/")
    if d: return int(d["data"][0]["value"]), d["data"][0]["value_classification"]
    return 50, "Neutral"

def get_btc_dominance():
    d = fetch_url("https://api.coingecko.com/api/v3/global")
    if d: return float(d["data"]["market_cap_percentage"]["btc"])
    return 50.0

def get_candles(client, pair):
    try:
        gm = {"ONE_HOUR": 3600}
        end = int(time.time())
        start = end - gm["ONE_HOUR"] * CONFIG["candle_count"]
        r = client.get_candles(product_id=pair, start=str(start), end=str(end),
                                granularity=CONFIG["candle_granularity"])
        return sorted(r.candles if hasattr(r,"candles") else [], key=lambda c: int(c.start))
    except Exception as e:
        log(f"  No data for {pair}: {e}"); return []

def to_lists(candles):
    return ([float(c.close)  for c in candles],
            [float(c.high)   for c in candles],
            [float(c.low)    for c in candles],
            [float(c.volume) for c in candles])

def get_price(client, pair):
    try:
        r = client.get_best_bid_ask(product_ids=[pair])
        for p in r.pricebooks:
            if p.product_id == pair:
                b = float(p.bids[0].price) if p.bids else 0
                a = float(p.asks[0].price) if p.asks else 0
                return (b+a)/2
    except: pass
    return 0.0

def ema(arr, n):
    if len(arr) < n: return arr[-1] if arr else 0.0
    k = 2/(n+1); e = sum(arr[:n])/n
    for v in arr[n:]: e = v*k + e*(1-k)
    return e

def calc_rsi(closes, n=14):
    if len(closes) < n+1: return 50.0
    g = [abs(closes[i]-closes[i-1]) for i in range(-n,0) if closes[i]>closes[i-1]]
    l = [abs(closes[i]-closes[i-1]) for i in range(-n,0) if closes[i]<=closes[i-1]]
    ag = sum(g)/n if g else 0; al = sum(l)/n if l else 1e-9
    return 100-(100/(1+ag/al))

def calc_adx(highs, lows, closes, n=14):
    if len(closes) < n*2: return 20.0
    try:
        trs, pdms, ndms = [], [], []
        for i in range(1, len(closes)):
            h,l,pc = highs[i],lows[i],closes[i-1]
            trs.append(max(h-l,abs(h-pc),abs(l-pc)))
            pdms.append(max(h-highs[i-1],0) if h-highs[i-1]>lows[i-1]-l else 0)
            ndms.append(max(lows[i-1]-l,0) if lows[i-1]-l>h-highs[i-1] else 0)
        atr = sum(trs[-n:])/n
        if atr == 0: return 20.0
        pdi = (sum(pdms[-n:])/n)/atr*100
        ndi = (sum(ndms[-n:])/n)/atr*100
        return abs(pdi-ndi)/(pdi+ndi)*100 if (pdi+ndi)>0 else 0
    except: return 20.0

def calc_atr(highs, lows, closes, n=14):
    if len(closes) < n+1: return closes[-1]*0.02
    trs = [max(highs[i]-lows[i],abs(highs[i]-closes[i-1]),abs(lows[i]-closes[i-1]))
           for i in range(-n,0)]
    return sum(trs)/n

def analyze(pair, closes, highs, lows):
    if len(closes) < 210:
        return {"direction":"HOLD","confidence":0,"reason":"Not enough data","indicators":{}}

    px    = closes[-1]
    rv    = calc_rsi(closes)
    e200  = ema(closes, 200)
    adxv  = calc_adx(highs, lows, closes)
    atrv  = calc_atr(highs, lows, closes)
    above = px > e200 * 1.001
    below = px < e200 * 0.999
    trending = adxv >= CONFIG["adx_threshold"]

    ind = {
        "rsi":    rv,
        "ema200": e200,
        "adx":    adxv,
        "atr":    atrv,
        "price":  px,
        "above_ema": above,
    }

    # BUY: RSI oversold + price above 200 EMA + ADX trending
    if rv < CONFIG["rsi_oversold"] and above and trending:
        sl = px - atrv * CONFIG["atr_sl_mult"]
        tp = px + atrv * CONFIG["atr_tp_mult"]
        reason = (f"RSI {rv:.0f} oversold + price above 200 EMA (${e200:,.4f}) "
                  f"+ ADX {adxv:.0f} confirms trend. "
                  f"Stop ${sl:,.4f} | Target ${tp:,.4f}")
        return {"direction":"BUY","confidence":85,"reason":reason,"indicators":ind,
                "sl":sl,"tp":tp,"atr":atrv}

    # SELL signal: RSI overbought + price below 200 EMA + ADX trending
    if rv > CONFIG["rsi_overbought"] and below and trending:
        reason = (f"RSI {rv:.0f} overbought + price below 200 EMA (${e200:,.4f}) "
                  f"+ ADX {adxv:.0f} confirms trend.")
        return {"direction":"SELL","confidence":85,"reason":reason,"indicators":ind}

    # Why not trading
    reasons = []
    if rv >= CONFIG["rsi_oversold"] and rv <= CONFIG["rsi_overbought"]:
        reasons.append(f"RSI {rv:.0f} neutral (need <{CONFIG['rsi_oversold']} or >{CONFIG['rsi_overbought']})")
    if not trending:
        reasons.append(f"ADX {adxv:.0f} — market ranging (need >{CONFIG['adx_threshold']})")
    if rv < CONFIG["rsi_oversold"] and not above:
        reasons.append(f"RSI oversold but price below 200 EMA — no uptrend")
    if rv > CONFIG["rsi_overbought"] and not below:
        reasons.append(f"RSI overbought but price above 200 EMA — no downtrend")

    return {"direction":"HOLD","confidence":0,
            "reason":"Waiting: " + " | ".join(reasons) if reasons else "No signal",
            "indicators":ind}

def pos_size(state, atr, price):
    capital   = state["capital"]
    risk_usd  = capital * CONFIG["max_risk_per_trade"]
    stop_dist = atr * CONFIG["atr_sl_mult"]
    if stop_dist > 0 and price > 0:
        units = risk_usd / stop_dist
        usd   = units * price
    else:
        usd = risk_usd * 4
    return min(round(usd, 2), capital * 0.10)

def place_order(client, pair, side, usd, price, state, reason="", atr=0):
    mode = "PAPER TRADE" if CONFIG["paper_trade"] else "LIVE"
    if side == "BUY":
        sl = price - atr * CONFIG["atr_sl_mult"]
        tp = price + atr * CONFIG["atr_tp_mult"]
        log(f"  💰 {mode} — Buying ${usd:.2f} of {pair} @ ${price:,.4f}")
        log(f"     Stop: ${sl:,.4f} | Target: ${tp:,.4f}")
        log(f"  📝 {reason}")
    else:
        entry = state["open_trades"].get(pair,{}).get("entry_price", price)
        pct   = (price-entry)/entry*100
        log(f"  💸 {mode} — Selling {pair} @ ${price:,.4f} ({'gained' if pct>0 else 'lost'} {abs(pct):.1f}%)")

    if not CONFIG["paper_trade"]:
        try:
            import uuid; cid = str(uuid.uuid4())
            if side == "BUY":
                client.market_order_buy(client_order_id=cid, product_id=pair, quote_size=str(usd))
            else:
                client.market_order_sell(client_order_id=cid, product_id=pair,
                                          base_size=str(round(usd/price,8)))
        except Exception as e:
            log(f"  ❌ {e}"); return False

    state["trade_history"].append({
        "time": now_str(), "pair": pair, "side": side,
        "usd": usd, "price": price, "paper": CONFIG["paper_trade"],
        "explanation": reason
    })
    state["stats"]["total_trades"] += 1
    state["performance"]["total_trades"] += 1

    if side == "BUY":
        sl = price - atr * CONFIG["atr_sl_mult"]
        tp = price + atr * CONFIG["atr_tp_mult"]
        state["open_trades"][pair] = {
            "entry_price":   price,
            "usd_invested":  usd,
            "entry_time":    now_str(),
            "highest_price": price,
            "atr":           atr,
            "stop_loss":     sl,
            "take_profit":   tp,
            "explanation":   reason,
        }
        state["capital"] -= usd
        save_explanation({
            "time": now_str(), "pair": pair, "side": "BUY",
            "price": price, "usd": usd, "explanation": reason,
            "stop_loss": sl, "take_profit": tp
        })

    elif pair in state["open_trades"]:
        e   = state["open_trades"].pop(pair)
        pnl = (price-e["entry_price"])/e["entry_price"] * e["usd_invested"]
        state["capital"]   += e["usd_invested"] + pnl
        state["daily_pnl"] += pnl
        state["total_pnl"] += pnl
        state["performance"]["total_pnl"] += pnl
        if pnl > 0:
            state["stats"]["wins"]       += 1
            state["performance"]["wins"] += 1
            log(f"  🏆 Profit: ${pnl:+.2f}")
        else:
            state["stats"]["losses"]       += 1
            state["performance"]["losses"] += 1
            log(f"  📉 Loss: ${pnl:+.2f}")
        peak = state["performance"].get("peak_capital", CONFIG["starting_capital"])
        if state["capital"] > peak:
            state["performance"]["peak_capital"] = state["capital"]
        else:
            dd = (peak-state["capital"])/peak*100
            if dd > state["performance"].get("max_drawdown",0):
                state["performance"]["max_drawdown"] = dd
        save_explanation({
            "time": now_str(), "pair": pair, "side": "SELL",
            "price": price, "pnl": pnl,
            "explanation": f"Exited {'profit' if pnl>0 else 'loss'} ${abs(pnl):.2f}"
        })
    return True

def risk_ok(state):
    today = date_str()
    if state.get("last_reset") != today:
        state["daily_pnl"] = 0.0
        state["last_reset"] = today
        log("  🔄 New day — daily P/L reset")
    if state["daily_pnl"] < -CONFIG["starting_capital"]*CONFIG["max_daily_loss_pct"]:
        log("  🛑 Daily loss limit hit — pausing until tomorrow")
        return False
    if len(state["open_trades"]) >= CONFIG["max_open_trades"]:
        log(f"  ⏸️  Max {CONFIG['max_open_trades']} positions open")
        return False
    return True

def check_exits(client, state):
    if not state["open_trades"]: return
    log("  Checking open positions...")
    for pair, pos in list(state["open_trades"].items()):
        px = get_price(client, pair)
        if px == 0: continue
        if px > pos.get("highest_price", px):
            pos["highest_price"] = px
            state["open_trades"][pair] = pos
        entry   = pos["entry_price"]
        highest = pos.get("highest_price", px)
        atr     = pos.get("atr", px*0.02)
        sl      = pos.get("stop_loss",  entry - atr*CONFIG["atr_sl_mult"])
        tp      = pos.get("take_profit", entry + atr*CONFIG["atr_tp_mult"])
        trail   = highest * (1 - CONFIG["trailing_stop_pct"])
        ch      = (px-entry)/entry*100
        log(f"  📊 {pair}: ${px:,.4f} | {ch:+.1f}% | SL ${sl:,.4f} | TP ${tp:,.4f}")
        if px <= sl:
            log(f"  🛑 STOP LOSS hit on {pair}")
            place_order(client, pair, "SELL", pos["usd_invested"], px, state, atr=atr)
        elif px >= tp:
            log(f"  🎯 TAKE PROFIT hit on {pair}")
            place_order(client, pair, "SELL", pos["usd_invested"], px, state, atr=atr)
        elif px <= trail and ch > 2:
            log(f"  📉 TRAILING STOP on {pair}")
            place_order(client, pair, "SELL", pos["usd_invested"], px, state, atr=atr)

def scan(client, state):
    sec(f"SCAN — {time_str()}")

    fg, fgl   = get_fear_greed()
    dominance = get_btc_dominance()
    state["last_fg"]        = fg
    state["last_fg_label"]  = fgl
    state["last_dominance"] = dominance

    log(f"  Fear & Greed: {fg}/100 — {fgl}")
    log(f"  BTC Dominance: {dominance:.1f}%")
    log("")

    check_exits(client, state)
    if not risk_ok(state):
        save_state(state); return

    for pair in CONFIG["pairs"]:
        coin = pair.split("-")[0]
        div(); log(f"  {coin}"); div()
        candles = get_candles(client, pair)
        if not candles: log("  No data"); continue
        closes, highs, lows, volumes = to_lists(candles)
        signal = analyze(pair, closes, highs, lows)
        ind    = signal["indicators"]
        px     = ind.get("price", 0)
        rv     = ind.get("rsi", 50)
        e200   = ind.get("ema200", 0)
        adxv   = ind.get("adx", 0)
        atrv   = ind.get("atr", 0)

        log(f"  Price:   ${px:,.4f}")
        log(f"  RSI:     {rv:.1f} {'— OVERSOLD ✅' if rv<35 else '— OVERBOUGHT ✅' if rv>65 else '— neutral'}")
        log(f"  200 EMA: ${e200:,.4f} — price is {'ABOVE ✅' if ind.get('above_ema') else 'BELOW'}")
        log(f"  ADX:     {adxv:.1f} {'— trending ✅' if adxv>=25 else '— ranging ⚠️'}")
        log(f"  → {signal['reason']}")
        log("")

        if signal["direction"] == "BUY" and pair not in state["open_trades"]:
            usd = pos_size(state, atrv, px)
            if usd >= 10:
                place_order(client, pair, "BUY", usd, px, state, signal["reason"], atrv)
            else:
                log(f"  Position too small (${usd:.2f}) — skipping")

        elif signal["direction"] == "SELL" and pair in state["open_trades"]:
            place_order(client, pair, "SELL",
                       state["open_trades"][pair]["usd_invested"], px, state,
                       signal["reason"], atrv)

    st  = state["stats"]
    t   = st["total_trades"]
    wr  = (st["wins"]/t*100) if t else 0
    gr  = (state["capital"]-CONFIG["starting_capital"])/CONFIG["starting_capital"]*100
    sec("PORTFOLIO")
    log(f"  Capital:  ${state['capital']:,.2f} ({'▲' if gr>=0 else '▼'}{abs(gr):.1f}%)")
    log(f"  P/L:      ${state['total_pnl']:+,.2f}  |  Today: ${state['daily_pnl']:+,.2f}")
    log(f"  Trades:   {t}  |  W/L: {st['wins']}/{st['losses']}  |  WR: {wr:.1f}%")
    log(f"  F&G:      {fg}/100 — {fgl}")
    if state["open_trades"]:
        for p, pos in state["open_trades"].items():
            log(f"  • {p} ${pos['usd_invested']:.2f} @ ${pos['entry_price']:,.4f} | SL ${pos.get('stop_loss',0):,.4f} | TP ${pos.get('take_profit',0):,.4f}")
    else:
        log("  Holding: Cash — waiting for clean signal")
    div("═")

    nxt = (datetime.now()+timedelta(minutes=CONFIG["scan_interval_minutes"])).strftime("%I:%M %p")
    log(f"  ✅ Next scan at {nxt}.\n")
    save_state(state)

def main():
    sec("EDGE BOT — CLEAN VERSION")
    log(f"  Mode:    {'PAPER TRADE' if CONFIG['paper_trade'] else '⚡ LIVE'}")
    log(f"  Capital: ${CONFIG['starting_capital']:,.2f}")
    log(f"  Signals: RSI + 200 EMA + ADX  (all 3 must agree)")
    log(f"  Scan:    Every {CONFIG['scan_interval_minutes']} minutes")
    log(f"  Risk:    {CONFIG['max_risk_per_trade']*100:.0f}% per trade | {CONFIG['max_daily_loss_pct']*100:.0f}% daily limit")
    log(f"  Exits:   ATR x{CONFIG['atr_sl_mult']} stop | ATR x{CONFIG['atr_tp_mult']} target | {CONFIG['trailing_stop_pct']*100:.0f}% trail")
    div("═"); log("")
    client = load_client(); state = load_state()
    log("  ✅ Connected | Running first scan...\n")
    scan(client, state)
    schedule.every(CONFIG["scan_interval_minutes"]).minutes.do(scan, client, state)
    nxt = (datetime.now()+timedelta(minutes=CONFIG["scan_interval_minutes"])).strftime("%I:%M %p")
    log(f"  Live. Next scan {nxt}. Ctrl+C to stop.\n")
    while True: schedule.run_pending(); time.sleep(30)

if __name__ == "__main__": main()
