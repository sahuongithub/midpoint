#!/usr/bin/env python3
"""
fill_oracle_test.py  --  Does Alpaca's paper engine fill at YOUR limit, or at the TRUE ask?

WHY THIS MATTERS
----------------
Alpaca's paper engine fills a buy limit only once it is marketable against the real
NBBO, and the data subscription does not change fills. So every fill carries
information about the true quote. The question is how much:

  * If the fill price always equals your limit price  ->  each fill is a CENSORED
    observation ("true ask was <= my limit"). Recovering the true ask needs ladders.

  * If the fill price comes in BELOW your limit       ->  the fill price is a DIRECT
    reading of the true ask. The measurement problem is over.

DESIGN
------
Submit several buy limits on the SAME contract at the SAME moment, at increasing
padding above the indicative ask. Because they are simultaneous they all face the
same true ask, so market movement cannot confound the comparison.

The discriminator is  (fill_price - limit_price)  within each probe:
    identically 0 across all padding levels      -> fills AT limit    -> CENSORED
    fills cluster at one price regardless        -> that price is the true ask -> DIRECT

One deliberately non-marketable control probe is included. It should NOT fill.

SAFETY
------
Paper endpoint only, and refuses to run unless you pass --yes.
Use a THROWAWAY account, never the account you submit to the hackathon.
Positions opened by the test are closed at the end.

USAGE
-----
    source ~/.config/midpoint/lab.env
    python3 fill_oracle_test.py --preflight     # checks only, no orders
    python3 fill_oracle_test.py --yes           # runs the experiment (market hours)

Zero dependencies: standard library only.
"""

import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import re
import urllib.request
from datetime import datetime, timedelta, timezone


def parse_ts(v):
    """Alpaca returns >6-digit fractional seconds, which 3.9 fromisoformat rejects."""
    v = v.replace("Z", "+00:00")
    v = re.sub(r"\.(\d{1,6})\d*", lambda m: "." + m.group(1).ljust(6, "0"), v)
    return datetime.fromisoformat(v)

TRADING = "https://paper-api.alpaca.markets"
DATA = "https://data.alpaca.markets"

# Padding added to the indicative ask for each probe, in dollars.
# The negative one is a control: it must not fill.
PROBES = [-0.50, 0.01, 0.05, 0.15, 0.40, 1.00]

FILL_TIMEOUT_S = 240      # community reports fills taking 50-260s
POLL_EVERY_S = 3


# --------------------------------------------------------------------------- http

def die(msg):
    print("\nFATAL: %s" % msg, file=sys.stderr)
    sys.exit(1)


def _headers():
    k, s = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_SECRET_KEY")
    if not k or not s:
        die("ALPACA_API_KEY and ALPACA_SECRET_KEY are not set.\n"
            "       Run:  source ~/.config/midpoint/lab.env")
    return {
        "APCA-API-KEY-ID": k,
        "APCA-API-SECRET-KEY": s,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def req(method, url, params=None, body=None, quiet=False):
    if params:
        url = "%s?%s" % (url, urllib.parse.urlencode(params))
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, headers=_headers(), method=method)
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(r, timeout=30, context=ctx) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:400]
        if not quiet:
            print("  ! HTTP %s on %s %s\n    %s" % (e.code, method, url, detail),
                  file=sys.stderr)
        raise
    except urllib.error.URLError as e:
        die("network error contacting %s: %s" % (url, e))


# ---------------------------------------------------------------- market plumbing

def tick_round(price):
    """Options trade in $0.01 increments below $3.00 and $0.05 at or above."""
    if price < 3.00:
        return round(round(price / 0.01) * 0.01, 2)
    return round(round(price / 0.05) * 0.05, 2)


def get_underlying_price(symbol="SPY"):
    """IEX is free and real-time on the Basic plan."""
    j = req("GET", "%s/v2/stocks/%s/trades/latest" % (DATA, symbol),
            params={"feed": "iex"})
    return float(j["trade"]["p"])


def pick_contract(underlying, spot, min_dte=5, max_dte=21):
    """Nearest-to-the-money call in the DTE window. Liquid, greeks available, not 0DTE."""
    today = datetime.now(timezone.utc).date()
    params = {
        "underlying_symbols": underlying,
        "status": "active",
        "type": "call",
        "expiration_date_gte": str(today + timedelta(days=min_dte)),
        "expiration_date_lte": str(today + timedelta(days=max_dte)),
        "strike_price_gte": str(round(spot * 0.97, 2)),
        "strike_price_lte": str(round(spot * 1.03, 2)),
        "limit": "1000",
    }
    j = req("GET", "%s/v2/options/contracts" % TRADING, params=params)
    contracts = j.get("option_contracts") or []
    if not contracts:
        die("no option contracts returned - widen the DTE/strike window, or check "
            "that options are enabled on this paper account.")
    contracts.sort(key=lambda c: (abs(float(c["strike_price"]) - spot),
                                  c["expiration_date"]))
    return contracts[0]


def get_indicative_quote(osi):
    j = req("GET", "%s/v1beta1/options/quotes/latest" % DATA,
            params={"symbols": osi, "feed": "indicative"})
    q = (j.get("quotes") or {}).get(osi)
    if not q:
        die("no indicative quote for %s. Raw: %s" % (osi, json.dumps(j)[:300]))
    return {"bid": float(q.get("bp", 0)), "ask": float(q.get("ap", 0)), "ts": q.get("t")}


# ----------------------------------------------------------------------- ordering

def submit_probe(osi, limit_price, tag):
    body = {
        "symbol": osi,
        "qty": "1",
        "side": "buy",
        "type": "limit",
        "time_in_force": "day",
        "limit_price": "%.2f" % limit_price,
        "client_order_id": "oracle-%s-%d" % (tag, int(time.time() * 1000)),
    }
    try:
        return req("POST", "%s/v2/orders" % TRADING, body=body)
    except urllib.error.HTTPError:
        return None


def poll(order_ids, timeout_s):
    """Poll until every order is terminal or the timeout expires."""
    t0 = time.time()
    done = {}
    while time.time() - t0 < timeout_s:
        pending = [oid for oid in order_ids if oid not in done]
        if not pending:
            break
        for oid in pending:
            o = req("GET", "%s/v2/orders/%s" % (TRADING, oid), quiet=True)
            if o.get("status") in ("filled", "canceled", "rejected", "expired"):
                o["_observed_after_s"] = round(time.time() - t0, 1)
                done[oid] = o
        if len(done) < len(order_ids):
            time.sleep(POLL_EVERY_S)
    for oid in order_ids:
        if oid not in done:
            o = req("GET", "%s/v2/orders/%s" % (TRADING, oid), quiet=True)
            o["_observed_after_s"] = None
            done[oid] = o
    return done


def cleanup(osi):
    print("\n[cleanup] cancelling open orders and closing the test position...")
    try:
        req("DELETE", "%s/v2/orders" % TRADING, quiet=True)
    except Exception:
        pass
    time.sleep(2)
    enc = urllib.parse.quote(osi)
    try:
        pos = req("GET", "%s/v2/positions/%s" % (TRADING, enc), quiet=True)
        qty = abs(int(float(pos["qty"])))
        if qty:
            req("DELETE", "%s/v2/positions/%s" % (TRADING, enc), quiet=True)
            print("[cleanup] submitted close for %d contract(s) of %s" % (qty, osi))
    except urllib.error.HTTPError:
        print("[cleanup] no open position to close")
    except Exception as e:
        print("[cleanup] note: %s" % e)


# ------------------------------------------------------------------------ verdict

def verdict(rows):
    filled = [r for r in rows if r["status"] == "filled" and r["fill"] is not None]
    if len(filled) < 2:
        return ("INCONCLUSIVE",
                "Fewer than two probes filled. Re-run during regular market hours, or "
                "raise the padding if the indicative ask sits well below the true ask.")

    deltas = [round(r["fill"] - r["limit"], 4) for r in filled]
    at_limit = all(abs(d) < 0.005 for d in deltas)
    spread_of_fills = max(r["fill"] for r in filled) - min(r["fill"] for r in filled)

    if at_limit:
        return ("CENSORED",
                "Every fill landed at its own limit price. Each fill therefore only tells "
                "you 'the true ask was <= my limit'. Ground truth needs probe ladders: "
                "bracket the true ask by bisecting between the highest non-filling and the "
                "lowest filling limit.")
    if spread_of_fills <= 0.0101:   # within one $0.01 tick
        return ("DIRECT",
                "Fills clustered at a single price regardless of how far above it the limit "
                "sat. That price IS the true best ask. Every fill is a direct reading of the "
                "NBBO and the measurement problem is solved - no ladders needed.")
    return ("PARTIAL-IMPROVEMENT",
            "Fills came in below their limits but did not agree on one price. Likely genuine "
            "price improvement against a moving quote. Treat each fill as an upper bound on "
            "the true ask at its own timestamp and calibrate the residual against OPRA.")


# --------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="Alpaca paper-fill NBBO oracle test")
    ap.add_argument("--yes", action="store_true", help="actually submit orders")
    ap.add_argument("--preflight", action="store_true", help="checks only, no orders")
    ap.add_argument("--underlying", default="SPY")
    ap.add_argument("--timeout", type=int, default=FILL_TIMEOUT_S)
    ap.add_argument("--out", default=os.path.expanduser("~/midpoint/docs/fill_oracle_result.json"))
    args = ap.parse_args()

    print("=" * 74)
    print("  ALPACA PAPER-FILL ORACLE TEST")
    print("=" * 74)

    acct = req("GET", "%s/v2/account" % TRADING)
    print("\n[account]  %s   equity $%s   status %s"
          % (acct.get("account_number", "?"),
             format(float(acct.get("equity", 0)), ",.2f"),
             acct.get("status")))
    print("           ^ confirm this is your THROWAWAY account, not the competition one.")

    clock = req("GET", "%s/v2/clock" % TRADING)
    print("[clock]    market_open=%s   now=%s" % (clock.get("is_open"), clock.get("timestamp")))
    if not clock.get("is_open"):
        print("           Market is CLOSED. Limit orders would queue, not fill.")
        print("           Run during 09:30-16:00 ET  =  19:00-01:30 IST, Mon-Fri.")

    spot = get_underlying_price(args.underlying)
    print("[spot]     %s = %.2f  (IEX, free real-time)" % (args.underlying, spot))

    c = pick_contract(args.underlying, spot)
    osi = c["symbol"]
    print("[contract] %s   strike %s   expiry %s"
          % (osi, c["strike_price"], c["expiration_date"]))

    q = get_indicative_quote(osi)
    if q["ask"] <= 0:
        die("indicative ask is zero - try another contract or check data entitlement.")
    print("[quote]    indicative  bid %.2f   ask %.2f   mid %.2f   width %.2f"
          % (q["bid"], q["ask"], (q["bid"] + q["ask"]) / 2, q["ask"] - q["bid"]))

    plan = [(p, tick_round(q["ask"] + p)) for p in PROBES]
    print("\n[plan]     probes, all submitted at once against the same true ask:")
    for p, lim in plan:
        note = "   <-- control, must NOT fill" if p < 0 else ""
        print("             ask%+.2f  ->  limit %.2f%s" % (p, lim, note))

    if args.preflight or not args.yes:
        print("\nPreflight only - nothing was submitted. Re-run with --yes to run it.")
        return

    if not clock.get("is_open"):
        print("\nRefusing to submit while the market is closed. Re-run during market hours.")
        return

    print("\n[submit]   sending probes...")
    submitted = []
    for p, lim in plan:
        o = submit_probe(osi, lim, tag=("%+.2f" % p).replace(".", "p"))
        if o and o.get("id"):
            submitted.append({"pad": p, "limit": lim, "id": o["id"]})
            print("             ok        ask%+.2f  limit %.2f  id %s" % (p, lim, o["id"][:8]))
        else:
            print("             REJECTED  ask%+.2f  limit %.2f" % (p, lim))

    if not submitted:
        die("every probe was rejected - check that options trading is enabled here.")

    print("\n[poll]     waiting up to %ds for terminal states..." % args.timeout)
    results = poll([s["id"] for s in submitted], args.timeout)

    rows = []
    for s in submitted:
        o = results[s["id"]]
        fill = float(o["filled_avg_price"]) if o.get("filled_avg_price") else None
        rows.append({
            "pad": s["pad"], "limit": s["limit"], "status": o.get("status"), "fill": fill,
            "delta_fill_minus_limit": round(fill - s["limit"], 4) if fill else None,
            "delta_fill_minus_indicative_ask": round(fill - q["ask"], 4) if fill else None,
            "latency_s": (
                round((parse_ts(o["filled_at"]) - parse_ts(o["submitted_at"])).total_seconds(), 3)
                if o.get("filled_at") and o.get("submitted_at") else None),
            "observed_after_s": o.get("_observed_after_s"),
        })

    print("\n" + "-" * 74)
    print("%7s %8s %10s %8s %11s %12s %7s"
          % ("pad", "limit", "status", "fill", "fill-limit", "fill-indAsk", "fill lat"))
    print("-" * 74)
    for r in sorted(rows, key=lambda x: x["pad"]):
        f = ("%.2f" % r["fill"]) if r["fill"] else "-"
        d1 = ("%+.2f" % r["delta_fill_minus_limit"]) if r["fill"] else "-"
        d2 = ("%+.2f" % r["delta_fill_minus_indicative_ask"]) if r["fill"] else "-"
        lat = ("%.3f" % r["latency_s"]) if r["latency_s"] is not None else "-"
        print("%+7.2f %8.2f %10s %8s %11s %12s %7s"
              % (r["pad"], r["limit"], r["status"], f, d1, d2, lat))
    print("-" * 74)

    ctrl = [r for r in rows if r["pad"] < 0]
    if ctrl and ctrl[0]["status"] == "filled":
        print("\n!! CONTROL PROBE FILLED. A limit well below the indicative ask should not")
        print("   have been marketable. Either the indicative ask badly overstates the true")
        print("   ask, or the fill model differs from the docs. Investigate before building.")

    tag, explanation = verdict(rows)
    print("\n" + "=" * 74)
    print("  VERDICT: %s" % tag)
    print("=" * 74)
    print("  %s\n" % explanation)

    out = {
        "run_utc": datetime.now(timezone.utc).isoformat(),
        "account": acct.get("account_number"),
        "underlying": args.underlying, "spot": spot,
        "contract": osi, "expiry": c["expiration_date"], "strike": c["strike_price"],
        "indicative_quote": q, "probes": rows,
        "verdict": tag, "explanation": explanation,
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as fh:
        json.dump(out, fh, indent=2)
    print("  written to %s\n" % args.out)

    cleanup(osi)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\ninterrupted - check for open orders/positions on the throwaway account.")
