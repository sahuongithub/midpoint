#!/usr/bin/env python3
"""
paired_arm.py -- Does timing the cross beat crossing immediately?

THE EXPERIMENT
--------------
Each trial trades the SAME contract twice, moments apart, differing only in WHEN
the order is sent. Paired design, so contract and time-of-day effects cancel.

    Arm A (control)   : cross immediately at the decision time.
    Arm B (treatment) : watch the underlying; cross when the quote has gone stale
                        in our favour, or at a deadline if it never does.

THE METRIC, AND THE TRAP IT AVOIDS
----------------------------------
Waiting for the underlying to fall gets you a cheaper option -- but the option is
genuinely worth less, so that is not an execution gain. Scoring against the entry
price would reward the agent for the market moving, not for executing well.

So each arm is charged against fair value AT ITS OWN FILL INSTANT:

    cost = fill_price - FV(fill_time),  FV(t) = mid0 + delta*dS + 0.5*gamma*dS^2

That is the effective half-spread paid, neutral to market direction. Lower is better.
The fair-value model behind it was validated separately at t=24.1, R^2=0.726 (40s).

WHY THE TREATMENT SHOULD WORK
-----------------------------
Muravyev & Pearson: quoted option prices lag the underlying. When the underlying
jumps in our favour the standing ask is momentarily stale -- cheap against the new
fair value. Arm B waits for exactly that. The trigger uses ONLY free real-time
equity data, never the option quote.

INTENTION TO TREAT
------------------
Arm B always crosses by the deadline, so every assigned trial produces a fill.
There is no censoring and no survivorship in the comparison.

SAFETY: lab account only, market hours only, --yes required, flat after every trial.
"""

import argparse, json, math, os, random, ssl, sys, time
import urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pricing

TRADING = "https://paper-api.alpaca.markets"
DATA = "https://data.alpaca.markets"
COMPETITION_ACCOUNT = "PA32CGA2U1DY"
PAD = 1.00          # aggressive limit: we always get the true touch anyway
FILL_WAIT_S = 20


def die(m): sys.exit("FATAL: " + m)


def _h():
    k, s = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_SECRET_KEY")
    if not k or not s: die("keys not set - source ~/.config/midpoint/lab.env")
    return {"APCA-API-KEY-ID": k, "APCA-API-SECRET-KEY": s,
            "Content-Type": "application/json", "Accept": "application/json"}


def req(method, url, params=None, body=None, quiet=False):
    if params: url = "%s?%s" % (url, urllib.parse.urlencode(params))
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, headers=_h(), method=method)
    try:
        with urllib.request.urlopen(r, timeout=20, context=ssl.create_default_context()) as x:
            return json.loads(x.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        if not quiet:
            print("  ! HTTP %s %s" % (e.code, e.read().decode()[:160]), file=sys.stderr)
        raise


def tick(p):
    p = max(0.01, p)
    return round(round(p / 0.01) * 0.01, 2) if p < 3.0 else round(round(p / 0.05) * 0.05, 2)


def spot(sym):
    j = req("GET", "%s/v2/stocks/%s/trades/latest" % (DATA, sym), params={"feed": "iex"})
    return float(j["trade"]["p"])


def snap(osi):
    j = req("GET", "%s/v1beta1/options/snapshots" % DATA,
            params={"symbols": osi, "feed": "indicative"}, quiet=True)
    return (j.get("snapshots") or {}).get(osi) or {}


def cross(osi, side, ref, tag):
    """Aggressive limit. The engine fills at the true touch regardless of our limit."""
    lim = tick(ref + PAD) if side == "buy" else tick(max(0.01, ref - PAD))
    body = {"symbol": osi, "qty": "1", "side": side, "type": "limit",
            "time_in_force": "day", "limit_price": "%.2f" % lim,
            "client_order_id": "arm-%s-%d" % (tag, int(time.time() * 1e6) % 10**9)}
    try:
        o = req("POST", "%s/v2/orders" % TRADING, body=body, quiet=True)
    except urllib.error.HTTPError:
        return None
    t0 = time.time()
    while time.time() - t0 < FILL_WAIT_S:
        cur = req("GET", "%s/v2/orders/%s" % (TRADING, o["id"]), quiet=True)
        if cur.get("status") in ("filled", "canceled", "rejected", "expired"):
            return cur if cur.get("filled_avg_price") else None
        time.sleep(0.3)
    req("DELETE", "%s/v2/orders/%s" % (TRADING, o["id"]), quiet=True)
    return None


def flat(osi):
    try: req("DELETE", "%s/v2/positions/%s" % (TRADING, urllib.parse.quote(osi)), quiet=True)
    except Exception: pass


def pick_contract(u, px, dte_lo, dte_hi):
    today = datetime.now(timezone.utc).date()
    j = req("GET", "%s/v2/options/contracts" % TRADING, params={
        "underlying_symbols": u, "status": "active", "type": "call",
        "expiration_date_gte": str(today + timedelta(days=dte_lo)),
        "expiration_date_lte": str(today + timedelta(days=dte_hi)),
        "strike_price_gte": str(round(px * 0.995, 2)),
        "strike_price_lte": str(round(px * 1.005, 2)), "limit": "300"})
    cs = j.get("option_contracts") or []
    if not cs: return None
    cs.sort(key=lambda c: (abs(float(c["strike_price"]) - px), c["expiration_date"]))
    return cs[0]


def anchor_greeks(sn, S, K, T, is_call, mid):
    g = sn.get("greeks") or {}
    if g.get("delta") is not None:
        return g["delta"], g.get("gamma") or 0.0, "alpaca"
    iv = pricing.implied_vol(mid, S, K, T, is_call)
    if not iv: return None, None, "failed"
    gg = pricing.greeks(S, K, T, iv, is_call)
    return gg["delta"], gg["gamma"], "ours"


def hours_left():
    n = datetime.now(timezone.utc); et = n - timedelta(hours=4)
    return max(0.02, 16.0 - (et.hour + et.minute / 60.0))


def run_trial(u, osi, K, is_call, dte, deadline, thresh, poll):
    """Returns a dict for both arms, or None if the trial could not complete."""
    S0 = spot(u)
    sn = snap(osi)
    q = sn.get("latestQuote") or {}
    bid, ask = float(q.get("bp") or 0), float(q.get("ap") or 0)
    if bid <= 0 or ask <= 0: return None
    mid0 = (bid + ask) / 2
    T = pricing.year_fraction(dte, hours_left())
    dl, gm, src = anchor_greeks(sn, S0, K, T, is_call, mid0)
    if dl is None: return None

    def fv(S_now): return pricing.fair_value_from_underlying(mid0, S0, S_now, dl, gm)

    # ---- Arm A: cross now
    a = cross(osi, "buy", ask, "A")
    if not a: return None
    Sa = spot(u)
    fill_a = float(a["filled_avg_price"])
    flat(osi)

    # ---- Arm B: wait for the quote to go stale in our favour
    t0 = time.time(); trigger = "deadline"; Sb = S0
    while time.time() - t0 < deadline:
        time.sleep(poll)
        Sb = spot(u)
        if dl * (Sb - S0) > thresh:      # underlying moved our way; standing ask is stale
            trigger = "favourable"
            break
    b = cross(osi, "buy", ask, "B")
    if not b:
        flat(osi); return None
    Sb = spot(u)
    fill_b = float(b["filled_avg_price"])
    flat(osi)

    return {
        "osi": osi, "underlying": u, "dte": dte, "greeks_src": src,
        "S0": S0, "mid0": round(mid0, 4), "ind_bid": bid, "ind_ask": ask,
        "delta": round(dl, 4), "gamma": round(gm, 6),
        "A": {"fill": fill_a, "S": Sa, "fv": round(fv(Sa), 4),
              "cost": round(fill_a - fv(Sa), 4), "wait_s": 0.0},
        "B": {"fill": fill_b, "S": Sb, "fv": round(fv(Sb), 4),
              "cost": round(fill_b - fv(Sb), 4),
              "wait_s": round(time.time() - t0, 1), "trigger": trigger},
        "ts": datetime.now(timezone.utc).isoformat(),
    }


def paired_t(d):
    n = len(d)
    if n < 2: return float("nan"), float("nan")
    m = sum(d) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in d) / (n - 1))
    return m, (m / (sd / math.sqrt(n)) if sd > 0 else float("nan"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--trials", type=int, default=12)
    ap.add_argument("--underlying", default="SPY")
    ap.add_argument("--dte-lo", type=int, default=0)
    ap.add_argument("--dte-hi", type=int, default=2)
    ap.add_argument("--deadline", type=float, default=45.0)
    ap.add_argument("--threshold", type=float, default=0.02)
    ap.add_argument("--poll", type=float, default=1.5)
    ap.add_argument("--out", default=os.path.expanduser("~/midpoint/docs/paired_arm.json"))
    a = ap.parse_args()

    acct = req("GET", "%s/v2/account" % TRADING)
    num, eq0 = acct["account_number"], float(acct["equity"])
    print("[account] %s  equity $%s" % (num, format(eq0, ",.2f")))
    if num == COMPETITION_ACCOUNT: die("competition account - refusing.")
    clock = req("GET", "%s/v2/clock" % TRADING)
    print("[clock]   open=%s" % clock.get("is_open"))

    px = spot(a.underlying)
    c = pick_contract(a.underlying, px, a.dte_lo, a.dte_hi)
    if not c: die("no contract found")
    osi, K = c["symbol"], float(c["strike_price"])
    dte = (datetime.strptime(c["expiration_date"], "%Y-%m-%d").date()
           - datetime.now(timezone.utc).date()).days
    print("[target]  %s  %s %.2f  strike %.0f  exp %s (dte %d)"
          % (osi, a.underlying, px, K, c["expiration_date"], dte))
    print("[design]  %d paired trials | deadline %.0fs | trigger delta*dS > $%.2f | poll %.1fs"
          % (a.trials, a.deadline, a.threshold, a.poll))

    if not a.yes:
        print("\nPreflight only. Re-run with --yes."); return
    if not clock.get("is_open"): die("market closed.")

    rows = []
    print("\n  %3s %8s %8s %8s %8s %8s %7s %11s" %
          ("#", "A fill", "A cost", "B fill", "B cost", "B-A", "wait", "trigger"))
    print("  " + "-" * 72)
    for i in range(a.trials):
        r = run_trial(a.underlying, osi, K, c["type"] == "call", dte,
                      a.deadline, a.threshold, a.poll)
        if not r:
            print("  %3d  trial failed" % (i + 1)); continue
        rows.append(r)
        d = r["B"]["cost"] - r["A"]["cost"]
        print("  %3d %8.2f %8.4f %8.2f %8.4f %+8.4f %6.0fs %11s"
              % (i + 1, r["A"]["fill"], r["A"]["cost"], r["B"]["fill"], r["B"]["cost"],
                 d, r["B"]["wait_s"], r["B"]["trigger"]))

    if len(rows) < 2: die("not enough completed trials.")
    ca = [r["A"]["cost"] for r in rows]; cb = [r["B"]["cost"] for r in rows]
    diff = [b - a_ for a_, b in zip(ca, cb)]
    ma, _ = paired_t(ca); mb, _ = paired_t(cb); md, t = paired_t(diff)

    print("\n" + "=" * 74)
    print("  PAIRED-ARM RESULT   n = %d" % len(rows))
    print("=" * 74)
    print("  Arm A (cross now)      mean cost vs fair value : %+.4f  ($%+.2f / contract)" % (ma, ma * 100))
    print("  Arm B (timed)          mean cost vs fair value : %+.4f  ($%+.2f / contract)" % (mb, mb * 100))
    print("  Difference (B - A)                             : %+.4f  ($%+.2f / contract)" % (md, md * 100))
    print("  paired t-statistic                             : %+.2f" % t)
    print("  %s" % ("B is CHEAPER (timing helps)" if md < 0 else "B is DEARER (timing did not help)"))
    fav = sum(1 for r in rows if r["B"]["trigger"] == "favourable")
    print("\n  triggered favourably: %d/%d   mean wait %.0fs"
          % (fav, len(rows), sum(r["B"]["wait_s"] for r in rows) / len(rows)))

    acct = req("GET", "%s/v2/account" % TRADING)
    print("  lab equity %s -> %s  (experiment cost $%.2f)"
          % (format(eq0, ",.2f"), format(float(acct["equity"]), ",.2f"),
             eq0 - float(acct["equity"])))
    pos = req("GET", "%s/v2/positions" % TRADING)
    print("  open positions after run: %d" % len(pos))
    for p in pos: flat(p["symbol"])

    json.dump({"config": vars(a), "trials": rows,
               "summary": {"n": len(rows), "mean_A": ma, "mean_B": mb,
                           "diff": md, "t": t, "favourable": fav}},
              open(a.out, "w"), indent=2)
    print("  written to %s\n" % a.out)


if __name__ == "__main__":
    main()
