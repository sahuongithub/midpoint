#!/usr/bin/env python3
"""
spread_curve.py -- what does an option's quoted spread do over a trading day?

WHY
---
Cartea, Jaimungal and Penalva (ch. 4) show the equity pattern from NASDAQ data:
quoted spreads are widest at the open, fall through the morning, sit on a
plateau, and are NARROWEST in the last half hour, while depth is highest at the
close and temporary price impact is lowest there. That is measured on equities.

Our agent's schedule already assumes something like it -- it opens positions
between 10:00 and 14:00 and flattens at 15:15 -- but that schedule was chosen
from reasoning about pin risk and 0DTE decay, not from measured option spreads.
This tool checks the assumption on the instrument we actually trade. If the
U-shape holds for SPY options, the flatten window sits in the cheapest liquidity
of the day and we can say so with our own numbers. If it does not, we would
rather find out and say that.

METHOD
------
Poll the free indicative feed once a minute for a full session and record the
quoted width of:
  * a PINNED set of contracts, chosen once at the start and never changed, so
    the day's comparison is within-contract and cannot be confounded by strike
    drift, and
  * the ROLLING at-the-money contract, which is what a trader actually faces.
Both 0DTE and the next expiry, because their decay profiles differ.

Costs nothing: quotes are free, no orders are sent. Every sample is appended to
disk as it is taken, so an interrupted session still leaves usable data -- the
lesson from losing a $53 sweep to one SSL timeout.
"""
import json, os, sys, time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import alpaca_io as aio

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def out_path():
    """One file per trading date: mixing sessions into one file would blend
    different regimes into the same time-of-day bucket."""
    return os.path.join(HERE, "results",
                        "spread_curve.%s.jsonl" % et_now().date().isoformat())


def et_now():
    return datetime.now(timezone.utc) - timedelta(hours=4)


def spot(sym):
    return float(aio.req("GET", "%s/v2/stocks/%s/trades/latest" % (aio.DATA, sym),
                         params={"feed": "iex"})["trade"]["p"])


def chain(underlying, span=0.03, max_expiries=2):
    """Contracts near the money for the next `max_expiries` expiries."""
    today = et_now().date()
    s = spot(underlying)
    j = aio.req("GET", "%s/v2/options/contracts" % aio.TRADING, params={
        "underlying_symbols": underlying, "status": "active",
        "expiration_date_gte": str(today),
        "expiration_date_lte": str(today + timedelta(days=7)),
        "strike_price_gte": str(round(s * (1 - span), 2)),
        "strike_price_lte": str(round(s * (1 + span), 2)),
        "limit": "500"})
    cs = j.get("option_contracts") or []
    exps = sorted({c["expiration_date"] for c in cs})[:max_expiries]
    return s, [c for c in cs if c["expiration_date"] in exps], exps


def quotes(symbols):
    out = {}
    for i in range(0, len(symbols), 100):
        out.update(aio.req("GET", "%s/v1beta1/options/snapshots" % aio.DATA,
                           params={"symbols": ",".join(symbols[i:i + 100]),
                                   "feed": "indicative"}).get("snapshots") or {})
    return out


def pick_pinned(cs, s, exps):
    """One ATM and one ~1% OTM put per expiry, fixed for the whole session."""
    pinned = []
    for e in exps:
        puts = [c for c in cs if c["expiration_date"] == e and c["type"] == "put"]
        if not puts:
            continue
        atm = min(puts, key=lambda c: abs(float(c["strike_price"]) - s))
        otm = min(puts, key=lambda c: abs(float(c["strike_price"]) - s * 0.99))
        for c, tag in ((atm, "pinned_atm"), (otm, "pinned_otm1pct")):
            pinned.append({"symbol": c["symbol"], "tag": tag, "expiry": e,
                           "strike": float(c["strike_price"]), "type": c["type"]})
    return pinned


def main(argv):
    underlying = "SPY"
    interval = 60.0
    stop_et = "16:05"
    for i, a in enumerate(argv):
        if a == "--underlying" and i + 1 < len(argv): underlying = argv[i + 1]
        if a == "--interval" and i + 1 < len(argv): interval = float(argv[i + 1])
        if a == "--until" and i + 1 < len(argv): stop_et = argv[i + 1]
    hh, mm = (int(x) for x in stop_et.split(":"))

    s0, cs, exps = chain(underlying)
    pinned = pick_pinned(cs, s0, exps)
    if not pinned:
        print("no contracts found"); return 1
    print("spread curve: %s  spot %.2f  expiries %s" % (underlying, s0, exps))
    for p in pinned:
        print("  pinned %-22s %-14s strike %.0f  exp %s"
              % (p["symbol"], p["tag"], p["strike"], p["expiry"]))
    out = out_path()
    print("  sampling every %.0fs until %s ET -> %s" % (interval, stop_et, out))

    by_exp_puts = {}
    for e in exps:
        by_exp_puts[e] = sorted([c for c in cs if c["expiration_date"] == e
                                 and c["type"] == "put"],
                                key=lambda c: float(c["strike_price"]))

    n = 0
    with open(out, "a", buffering=1) as fh:
        while True:
            et = et_now()
            if (et.hour, et.minute) >= (hh, mm):
                break
            try:
                s = spot(underlying)
                # rolling ATM per expiry, plus the pinned set
                watch = list(pinned)
                for e, puts in by_exp_puts.items():
                    if not puts:
                        continue
                    c = min(puts, key=lambda c: abs(float(c["strike_price"]) - s))
                    watch.append({"symbol": c["symbol"], "tag": "rolling_atm",
                                  "expiry": e, "strike": float(c["strike_price"]),
                                  "type": "put"})
                q = quotes([w["symbol"] for w in watch])
                row = {"ts": datetime.now(timezone.utc).isoformat(),
                       "et": et.strftime("%H:%M:%S"),
                       "et_minutes": et.hour * 60 + et.minute,
                       "underlying": underlying, "spot": s, "legs": []}
                for w in watch:
                    sn = q.get(w["symbol"]) or {}
                    qq = sn.get("latestQuote") or {}
                    bid, ask = float(qq.get("bp") or 0), float(qq.get("ap") or 0)
                    if bid <= 0 or ask <= 0:
                        continue
                    row["legs"].append({
                        **w, "bid": bid, "ask": ask, "mid": (bid + ask) / 2,
                        "width": round(ask - bid, 4),
                        "rel_width": round((ask - bid) / ((bid + ask) / 2), 6)
                        if (bid + ask) else None,
                        "bid_size": qq.get("bs"), "ask_size": qq.get("as"),
                        "moneyness": round(w["strike"] / s, 5)})
                if row["legs"]:
                    fh.write(json.dumps(row) + "\n")
                    n += 1
                    if n % 10 == 1:
                        ws = ", ".join("%s %.2f" % (l["tag"][:11], l["width"])
                                       for l in row["legs"][:4])
                        print("  %s  spot %.2f  %s" % (row["et"], s, ws))
            except Exception as e:
                sys.stderr.write("  sample failed at %s: %s\n" % (et.strftime("%H:%M:%S"), e))
            time.sleep(interval)

    print("collected %d samples -> %s" % (n, out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
