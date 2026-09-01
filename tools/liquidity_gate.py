#!/usr/bin/env python3
"""
liquidity_gate.py -- What does a strike choice actually cost, and can we predict it
                     BEFORE trading?

WHY
---
The paired-arm experiment showed timing the cross is worth at most ~$1/contract, while
choosing a liquid strike over an illiquid one is worth ~$86. So the agent's most
valuable execution decision is WHICH contract to trade. But the gate has to run on
free data, before any order exists.

THE TENSION WE CAN SETTLE
-------------------------
Academic work (e.g. George & Longstaff JFQA 1993, and later cross-sectional studies)
finds moneyness, implied volatility and time to expiry dominate, while VOLUME AND OPEN
INTEREST have limited predictive power. Retail folklore says the opposite: screen on
open interest, avoid single-digit OI. Ground truth from the fill oracle can decide it.

METHOD
------
For each contract in a stratified grid: record every free feature, then recover the
TRUE spread by buying one and immediately selling one. Net position zero.

COST CONTROL
------------
Probing an illiquid strike is genuinely expensive (we measured one at $306 a round
trip). A hard budget stops the sweep rather than discovering the cost afterwards.

SAFETY: lab account only, market hours, --yes required, flat after every probe.
"""

import argparse, csv, json, math, os, ssl, sys, time
import urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pricing
import alpaca_io as aio

TRADING = "https://paper-api.alpaca.markets"
DATA = "https://data.alpaca.markets"
COMPETITION_ACCOUNT = "PA32CGA2U1DY"

GRID_DTE = [(0, 0, "0DTE"), (1, 3, "1-3d"), (7, 12, "7-12d"), (28, 45, "28-45d")]
GRID_MNY = [0.97, 0.99, 1.00, 1.01, 1.03, 1.05]
UNDERLYINGS = ["SPY", "QQQ", "AAPL"]

PAD, FILL_WAIT_S, PACE_S = 1.00, 20, 0.6
MAX_MID = 150.0                          # skip options that would tie up silly capital


def die(m): sys.exit("FATAL: " + m)

# HTTP, guards and cleanup all delegate to alpaca_io, which retries transient
# failures. A single SSL timeout killed an earlier run of this script.
req = aio.req
TRADING, DATA, COMPETITION_ACCOUNT = aio.TRADING, aio.DATA, aio.COMPETITION_ACCOUNT


def tick(p):
    p = max(0.01, p)
    return round(round(p / 0.01) * 0.01, 2) if p < 3.0 else round(round(p / 0.05) * 0.05, 2)

def spot(sym):
    j = req("GET", "%s/v2/stocks/%s/trades/latest" % (DATA, sym), params={"feed": "iex"})
    return float(j["trade"]["p"])

def find(u, px, lo, hi, mny, typ="call"):
    today = datetime.now(timezone.utc).date()
    tgt = px * mny
    j = req("GET", "%s/v2/options/contracts" % TRADING, params={
        "underlying_symbols": u, "status": "active", "type": typ,
        "expiration_date_gte": str(today + timedelta(days=lo)),
        "expiration_date_lte": str(today + timedelta(days=hi)),
        "strike_price_gte": str(round(tgt * 0.985, 2)),
        "strike_price_lte": str(round(tgt * 1.015, 2)), "limit": "500"})
    cs = j.get("option_contracts") or []
    if not cs: return None
    cs.sort(key=lambda c: (abs(float(c["strike_price"]) - tgt), c["expiration_date"]))
    return cs[0]

def snap(osi):
    j = req("GET", "%s/v1beta1/options/snapshots" % DATA,
            params={"symbols": osi, "feed": "indicative"})
    return (j.get("snapshots") or {}).get(osi) or {}

def probe(osi, side, ref):
    lim = tick(ref + PAD) if side == "buy" else tick(max(0.01, ref - PAD))
    body = {"symbol": osi, "qty": "1", "side": side, "type": "limit",
            "time_in_force": "day", "limit_price": "%.2f" % lim,
            "client_order_id": "liq-%s-%d" % (side[0], int(time.time() * 1e6) % 10**9)}
    try: o = req("POST", "%s/v2/orders" % TRADING, body=body)
    except urllib.error.HTTPError: return None
    t0 = time.time()
    while time.time() - t0 < FILL_WAIT_S:
        cur = req("GET", "%s/v2/orders/%s" % (TRADING, o["id"]))
        if cur.get("status") in ("filled", "canceled", "rejected", "expired"):
            return cur if cur.get("filled_avg_price") else None
        time.sleep(0.3)
    req("DELETE", "%s/v2/orders/%s" % (TRADING, o["id"]))
    return None

def flat(osi):
    try: req("DELETE", "%s/v2/positions/%s" % (TRADING, urllib.parse.quote(osi)))
    except Exception: pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--budget", type=float, default=700.0, help="hard $ stop for the run")
    ap.add_argument("--max-cell-cost", type=float, default=50.0,
                    help="skip probing a contract whose quoted width implies more than this; "
                         "it is still recorded, so coverage does not collapse")
    ap.add_argument("--tag", default="run", help="label for time-of-day comparison")
    ap.add_argument("--skip-0dte", action="store_true",
                    help="exclude same-day expiries: avoids Alpaca's 15:30 ET opening cutoff "
                         "and the distortion of expiry-minute behaviour")
    ap.add_argument("--out", default=os.path.expanduser("~/midpoint/docs/liquidity_gate"))
    a = ap.parse_args()

    acct = aio.guard_not_competition()
    num, eq0 = acct["account_number"], float(acct["equity"])
    print("[account] %s  equity $%s" % (num, format(eq0, ",.2f")))
    clock = req("GET", "%s/v2/clock" % TRADING)
    print("[clock]   open=%s" % clock.get("is_open"))

    px = {u: spot(u) for u in UNDERLYINGS}
    print("[spot]    " + "  ".join("%s %.2f" % (k, v) for k, v in px.items()))

    dte_grid = [g for g in GRID_DTE if not (a.skip_0dte and g[2] == "0DTE")]
    cells = [(u, lo, hi, lab, m) for u in UNDERLYINGS
             for (lo, hi, lab) in dte_grid for m in GRID_MNY]
    print("[plan]    %d cells (%d underlyings x %d dte x %d moneyness) | budget $%.0f | cell cap $%.0f | tag '%s'"
          % (len(cells), len(UNDERLYINGS), len(dte_grid), len(GRID_MNY), a.budget, a.max_cell_cost, a.tag))
    if not a.yes:
        print("\nPreflight only. Re-run with --yes."); return
    if not clock.get("is_open"): die("market closed.")

    rows, spent, seen = [], 0.0, set()
    journal = aio.Journal(a.out + ".jsonl")   # every row hits disk immediately
    print("[journal] %s.jsonl" % a.out)
    print("\n%-22s %-6s %-5s %6s | %7s %7s %6s %6s %5s | %8s %9s"
          % ("contract", "dte", "mny", "price", "indW", "TRUE W", "OI", "vol", "askSz", "cost $", "cum $"))
    print("-" * 116)
    for (u, lo, hi, lab, m) in cells:
        if spent >= a.budget:
            print("  BUDGET REACHED ($%.2f) - stopping." % spent); break
        c = find(u, px[u], lo, hi, m)
        if not c or c["symbol"] in seen: continue
        osi = c["symbol"]; seen.add(osi)
        sn = snap(osi)
        q = sn.get("latestQuote") or {}
        bid, ask = float(q.get("bp") or 0), float(q.get("ap") or 0)
        if bid <= 0 or ask <= 0: continue
        mid = (bid + ask) / 2
        if mid > MAX_MID: continue
        predicted = (ask - bid) * 100
        if predicted > a.max_cell_cost or predicted > (a.budget - spent):
            db0 = sn.get("dailyBar") or {}
            dte0 = (datetime.strptime(c["expiration_date"], "%Y-%m-%d").date()
                    - datetime.now(timezone.utc).date()).days
            rows.append({"contract": osi, "underlying": u, "dte": dte0, "dte_label": lab,
                         "strike": float(c["strike_price"]), "spot": px[u],
                         "moneyness": round(float(c["strike_price"]) / px[u], 4),
                         "ind_bid": bid, "ind_ask": ask, "ind_mid": round(mid, 4),
                         "ind_width": round(ask - bid, 4),
                         "true_bid": None, "true_ask": None, "true_width": None,
                         "true_mid": None, "rel_true_width": None,
                         "open_interest": int(c.get("open_interest") or 0),
                         "day_volume": int(db0.get("v") or 0), "day_trades": int(db0.get("n") or 0),
                         "ask_size": int(q.get("as") or 0), "bid_size": int(q.get("bs") or 0),
                         "iv": sn.get("impliedVolatility"),
                         "delta": (sn.get("greeks") or {}).get("delta"),
                         "cost_usd": 0.0, "probed": False, "predicted_usd": round(predicted, 2),
                         "tag": a.tag, "ts": datetime.now(timezone.utc).isoformat()})
            print("%-22s %-6s %-5.2f %6.2f | %7.3f %7s %6d %6d %5d | %8s %9.2f"
                  % (osi, lab, float(c["strike_price"]) / px[u], mid, ask - bid,
                     "SKIP", int(c.get("open_interest") or 0), int((sn.get("dailyBar") or {}).get("v") or 0),
                     int(q.get("as") or 0), "~%.0f" % predicted, spent))
            journal.write(rows[-1])
            time.sleep(PACE_S); continue

        db = sn.get("dailyBar") or {}
        g = sn.get("greeks") or {}
        dte = (datetime.strptime(c["expiration_date"], "%Y-%m-%d").date()
               - datetime.now(timezone.utc).date()).days

        b = probe(osi, "buy", ask)
        if not b: flat(osi); continue
        true_ask = float(b["filled_avg_price"])
        s_ = probe(osi, "sell", bid)
        if not s_: flat(osi); continue
        true_bid = float(s_["filled_avg_price"])
        flat(osi)

        tw = true_ask - true_bid
        cost = tw * 100; spent += cost
        r = {"contract": osi, "underlying": u, "dte": dte, "dte_label": lab,
             "strike": float(c["strike_price"]), "spot": px[u],
             "moneyness": round(float(c["strike_price"]) / px[u], 4),
             "ind_bid": bid, "ind_ask": ask, "ind_mid": round(mid, 4),
             "ind_width": round(ask - bid, 4),
             "true_bid": true_bid, "true_ask": true_ask,
             "true_width": round(tw, 4), "true_mid": round((true_bid + true_ask) / 2, 4),
             "rel_true_width": round(tw / mid, 5) if mid > 0 else None,
             "open_interest": int(c.get("open_interest") or 0),
             "day_volume": int(db.get("v") or 0), "day_trades": int(db.get("n") or 0),
             "ask_size": int(q.get("as") or 0), "bid_size": int(q.get("bs") or 0),
             "iv": sn.get("impliedVolatility"), "delta": g.get("delta"),
             "cost_usd": round(cost, 2), "probed": True,
             "predicted_usd": round((ask - bid) * 100, 2), "tag": a.tag,
             "ts": datetime.now(timezone.utc).isoformat()}
        rows.append(r); journal.write(r)
        print("%-22s %-6s %-5.2f %6.2f | %7.3f %7.3f %6d %6d %5d | %8.2f %9.2f"
              % (osi, lab, r["moneyness"], mid, r["ind_width"], tw,
                 r["open_interest"], r["day_volume"], r["ask_size"], cost, spent))
        time.sleep(PACE_S)

    probed = [r for r in rows if r.get("probed")]
    if len(probed) < 5: die("too few probed rows.")
    json.dump(rows, open(a.out + ".json", "w"), indent=2)
    with open(a.out + ".csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)

    acct = req("GET", "%s/v2/account" % TRADING)
    print("\n  rows=%d  probed=%d  skipped=%d  spent $%.2f  lab equity %s -> %s"
          % (len(rows), len(probed), len(rows) - len(probed), spent,
             format(eq0, ",.2f"), format(float(acct["equity"]), ",.2f")))
    journal.close()
    aio.flatten_all()
    print("  written to %s.json / .csv / .jsonl" % a.out)


if __name__ == "__main__":
    main()
