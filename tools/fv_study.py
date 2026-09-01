#!/usr/bin/env python3
"""
fv_study.py -- Does option fair value actually track the underlying at high frequency
               on Alpaca's data? This gates the entire patience engine.

THE CLAIM UNDER TEST
--------------------
Muravyev & Pearson (RFS 2020): option fair value is predictable sub-minute from the
underlying's move with implied volatility held fixed. If true here, an agent can
decide WHEN to cross using only free real-time equity data. If false, the patience
engine has no basis and the thesis needs rebuilding.

PREDICTION
----------
    FV(t+h) = mid(t) + delta * dS + 0.5 * gamma * dS^2       [delta-adjusted]
    FV(t+h) = mid(t)                                          [naive / random walk]

We compare RMSE of both against the realised mid at t+h, for several horizons.

A SECOND QUESTION, MEASURED FIRST
---------------------------------
The free indicative feed's mid carries noise. If that noise is larger than the
signal we are trying to detect, no prediction test can succeed regardless of the
underlying model. So we measure the mid's own time-series jitter before anything else.

Collection is READ-ONLY. No orders. Zero cost.
"""

import argparse, json, os, ssl, sys, time
import urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pricing

TRADING = "https://paper-api.alpaca.markets"
DATA = "https://data.alpaca.markets"


def _h():
    k, s = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_SECRET_KEY")
    if not k or not s:
        sys.exit("keys not set - run: source ~/.config/midpoint/lab.env")
    return {"APCA-API-KEY-ID": k, "APCA-API-SECRET-KEY": s, "Accept": "application/json"}


def req(method, url, params=None, quiet=False):
    if params:
        url = "%s?%s" % (url, urllib.parse.urlencode(params))
    r = urllib.request.Request(url, headers=_h(), method=method)
    try:
        with urllib.request.urlopen(r, timeout=20, context=ssl.create_default_context()) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        if not quiet:
            print("  ! HTTP %s %s %s" % (e.code, url.split("?")[0], e.read().decode()[:160]),
                  file=sys.stderr)
        raise


def spots(syms):
    j = req("GET", "%s/v2/stocks/trades/latest" % DATA,
            params={"symbols": ",".join(syms), "feed": "iex"})
    return {k: float(v["p"]) for k, v in (j.get("trades") or {}).items()}


def snapshots(osis):
    """One call for every contract: quote + greeks + IV where available."""
    j = req("GET", "%s/v1beta1/options/snapshots" % DATA,
            params={"symbols": ",".join(osis), "feed": "indicative"}, quiet=True)
    return j.get("snapshots") or {}


def pick(underlying, px, min_dte, max_dte, mny, typ="call"):
    today = datetime.now(timezone.utc).date()
    j = req("GET", "%s/v2/options/contracts" % TRADING, params={
        "underlying_symbols": underlying, "status": "active", "type": typ,
        "expiration_date_gte": str(today + timedelta(days=min_dte)),
        "expiration_date_lte": str(today + timedelta(days=max_dte)),
        "strike_price_gte": str(round(px * mny * 0.985, 2)),
        "strike_price_lte": str(round(px * mny * 1.015, 2)),
        "limit": "500"})
    cs = j.get("option_contracts") or []
    if not cs:
        return None
    cs.sort(key=lambda c: (abs(float(c["strike_price"]) - px * mny), c["expiration_date"]))
    return cs[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=8.0)
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--out", default=os.path.expanduser("~/midpoint/docs/fv_study.json"))
    a = ap.parse_args()

    clock = req("GET", "%s/v2/clock" % TRADING)
    print("[clock] open=%s  %s" % (clock.get("is_open"), clock.get("timestamp")))
    if not clock.get("is_open"):
        sys.exit("market closed - quotes will not move; run during RTH.")

    unds = ["SPY", "QQQ"]
    px = spots(unds)
    print("[spot]  " + "  ".join("%s %.2f" % (k, v) for k, v in px.items()))

    targets = []
    for u in unds:
        for (lo, hi, mny, label) in [(0, 0, 1.00, "0DTE-ATM"),
                                     (1, 4, 1.00, "short-ATM"),
                                     (7, 16, 1.00, "mid-ATM"),
                                     (7, 16, 1.02, "mid-OTM")]:
            c = pick(u, px[u], lo, hi, mny)
            if c:
                targets.append({"osi": c["symbol"], "underlying": u, "label": label,
                                "strike": float(c["strike_price"]),
                                "expiry": c["expiration_date"], "type": c["type"]})
    seen, uniq = set(), []
    for t in targets:
        if t["osi"] not in seen:
            seen.add(t["osi"]); uniq.append(t)
    targets = uniq
    print("[targets] %d contracts" % len(targets))
    for t in targets:
        print("    %-22s %-10s %s strike %.0f exp %s" %
              (t["osi"], t["label"], t["underlying"], t["strike"], t["expiry"]))

    osis = [t["osi"] for t in targets]
    n = int(a.minutes * 60 / a.interval)
    print("\n[collect] %d samples every %.1fs (~%.1f min). Read-only, no orders.\n"
          % (n, a.interval, a.minutes))

    samples = []
    t0 = time.time()
    for i in range(n):
        try:
            s_px = spots(unds)
            snaps = snapshots(osis)
        except Exception as e:
            print("  sample %d failed: %s" % (i, e)); time.sleep(a.interval); continue
        row = {"t": time.time() - t0, "iso": datetime.now(timezone.utc).isoformat(),
               "spot": s_px, "opts": {}}
        for osi in osis:
            sn = snaps.get(osi) or {}
            q = sn.get("latestQuote") or {}
            g = sn.get("greeks") or {}
            bid, ask = float(q.get("bp") or 0), float(q.get("ap") or 0)
            row["opts"][osi] = {
                "bid": bid, "ask": ask,
                "mid": round((bid + ask) / 2, 4) if bid > 0 and ask > 0 else None,
                "iv": sn.get("impliedVolatility"),
                "delta": g.get("delta"), "gamma": g.get("gamma"),
            }
        samples.append(row)
        if i % 30 == 0:
            m = row["opts"][osis[0]]
            print("  %3d/%d  t=%5.0fs  %s=%.2f  %s mid=%s iv=%s delta=%s"
                  % (i, n, row["t"], unds[0], s_px.get(unds[0], 0), osis[0][:18],
                     m["mid"], (("%.3f" % m["iv"]) if m["iv"] else "None"),
                     (("%.3f" % m["delta"]) if m["delta"] else "None")))
        # pace against the wall clock so API round-trip time does not inflate the interval
        next_due = t0 + (i + 1) * a.interval
        time.sleep(max(0.0, next_due - time.time()))
    out = {"collected_utc": datetime.now(timezone.utc).isoformat(),
           "interval_s": a.interval, "targets": targets, "samples": samples}
    with open(a.out, "w") as f:
        json.dump(out, f)
    print("\n[saved] %d samples -> %s" % (len(samples), a.out))


if __name__ == "__main__":
    main()
