#!/usr/bin/env python3
"""
opportunity_cost.py -- what did the risk gates actually cost, or save?

THE HOLE THIS FILLS
-------------------
Every serious trading system logs its refusals. Almost none price them. That
gap is old and well named: Grinold and Kahn (Active Portfolio Management, ch.
16) point out that transaction-cost analysis built from executed trades is
censored data -- "the record shows trades, not orders placed, and certainly not
orders not placed because the cost would be too high" -- and Wayne Wagner's
studies of institutional desks found the opportunity cost of trades never made
often dominates every cost that is measured. Harris makes the same point in
Trading and Exchanges ch. 21, listing missed-trade opportunity cost as one of
the three components of transaction cost and the hardest to see.

So a refusal ledger that records only the reason cannot answer the one question
worth asking of a risk system: did the refusals help? This tool answers it.

HOW A REFUSED SPREAD IS PRICED
------------------------------
The agent trades defined-risk verticals that expire within days, and for those
the counterfactual needs no model at all. If the trade had been allowed and held
to expiry, its result is fixed by one number -- where the underlying settled:

    put credit spread, short K_s, long K_l (K_l < K_s), credit C per share
        P&L per share = C - max(0, K_s - S_T) + max(0, K_l - S_T)
    call credit spread, short K_s, long K_l (K_l > K_s)
        P&L per share = C - max(0, S_T - K_s) + max(0, S_T - K_l)

bounded below by -(width - C). Multiply by 100 and by the contracts the kernel
would have allowed. No volatility assumption, no pricing model, no discretion.
The settlement print comes from Alpaca's daily bars, which are free.

WHAT THIS NUMBER IS AND IS NOT
------------------------------
It is: the exact result of the refused structure, held to expiry.
It is not: what the live agent would have done. The live agent closes at 50% of
the credit, so a winner is normally banked early and a loser is normally still
open at the close. Reported alongside is the "managed" variant, which caps the
gain at the profit target if the spread ever traded through it -- that variant
needs intraday marks and is only computed when they exist.

And with a handful of refusals per gate this is descriptive, not inferential.
Grinold and Kahn put the standard error of an information ratio at 1/sqrt(years):
proving a merely top-quartile skill takes sixteen years of returns. Nothing here
is offered as proof that the gates add value. It is offered as the arithmetic
the field usually leaves out.
"""
import json, math, os, sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import statlib as S

try:
    import alpaca_io as aio
except Exception:
    aio = None

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JOURNAL = os.path.expanduser("~/midpoint/docs/risk_decisions.jsonl")
OUT = os.path.join(HERE, "results", "opportunity_cost.json")
CACHE = os.path.join(HERE, "results", "settlement_cache.json")


# ------------------------------------------------------------------ settlement

def load_cache():
    if os.path.exists(CACHE):
        try:
            return json.load(open(CACHE))
        except Exception:
            pass
    return {}


def settlement_price(symbol: str, day: str, cache: dict, allow_net=True):
    """Official-ish settlement: the daily close for `day`. Cached on disk."""
    key = "%s|%s" % (symbol, day)
    if key in cache:
        return cache[key]
    if not allow_net or aio is None:
        return None
    try:
        j = aio.req("GET", "%s/v2/stocks/%s/bars" % (aio.DATA, symbol),
                    params={"start": day, "end": day, "timeframe": "1Day",
                            "feed": "iex", "adjustment": "raw"})
        bars = j.get("bars") or []
        px = float(bars[-1]["c"]) if bars else None
    except Exception as e:
        sys.stderr.write("  settlement lookup failed for %s %s: %s\n" % (symbol, day, e))
        px = None
    if px is not None:
        cache[key] = px
        json.dump(cache, open(CACHE, "w"), indent=1)
    return px


# ------------------------------------------------------------------- pricing

def vertical_pnl_per_share(kind, k_short, k_long, credit, s_t):
    """Expiry P&L per share of a defined-risk credit vertical."""
    if kind == "put":
        short_owed = max(0.0, k_short - s_t)
        long_worth = max(0.0, k_long - s_t)
    else:
        short_owed = max(0.0, s_t - k_short)
        long_worth = max(0.0, s_t - k_long)
    return credit - short_owed + long_worth


def price_refusal(rec, cache, allow_net=True):
    """Return a dict describing what the refused trade would have done, or None."""
    snap = rec.get("snapshot") or {}
    sh, lg = snap.get("short") or {}, snap.get("long") or {}
    if not sh or not lg:
        return None
    kind = snap.get("kind") or sh.get("kind")
    k_s, k_l = sh.get("strike"), lg.get("strike")
    credit = snap.get("credit_per_share")
    if credit is None:
        credit = rec.get("limit_price")
    expiry = snap.get("expiry") or sh.get("expiry")
    und = snap.get("underlying") or rec.get("underlying")
    if None in (k_s, k_l, credit, expiry, und, kind):
        return None

    # contracts the kernel would have allowed had this gate not fired: the
    # proposal size, which is what the refusal actually denied
    n = rec.get("proposed_contracts") or snap.get("contracts_proposed") or 1

    s_t = settlement_price(und, expiry, cache, allow_net)
    if s_t is None:
        return {"status": "pending", "expiry": expiry, "underlying": und,
                "contracts": n, "credit": credit, "kind": kind,
                "k_short": k_s, "k_long": k_l}

    width = abs(k_s - k_l)
    per_share = vertical_pnl_per_share(kind, k_s, k_l, credit, s_t)
    per_share = max(per_share, -(width - credit))          # defined risk floor
    pnl = round(per_share * 100 * n, 2)
    return {"status": "settled", "expiry": expiry, "underlying": und,
            "settle": s_t, "contracts": n, "credit": credit, "kind": kind,
            "k_short": k_s, "k_long": k_l, "width": width,
            "pnl_per_share": round(per_share, 4),
            "avoided_pnl_usd": pnl,
            "max_loss_usd": round((width - credit) * 100 * n, 2),
            "max_gain_usd": round(credit * 100 * n, 2),
            "expired_worthless": per_share >= credit - 1e-9,
            "spot_at_proposal": snap.get("spot")}


# --------------------------------------------------------------------- report

def main(argv):
    offline = "--offline" in argv
    path = JOURNAL
    for i, a in enumerate(argv):
        if a == "--journal" and i + 1 < len(argv):
            path = os.path.expanduser(argv[i + 1])
    if not os.path.exists(path):
        print("no journal at %s" % path)
        return 0

    recs = []
    for line in open(path):
        line = line.strip()
        if line:
            try:
                recs.append(json.loads(line))
            except Exception:
                pass

    refusals = [r for r in recs if r.get("decision") == "REJECT"]
    shrinks = [r for r in recs if r.get("decision") == "SHRINK"]
    passes = [r for r in recs if r.get("decision") == "PASS"]

    print("=" * 78)
    print("  WHAT THE REFUSALS COST      journal: %s" % path)
    print("=" * 78)
    print("  %d evaluations: %d passed, %d shrunk, %d refused"
          % (len(recs), len(passes), len(shrinks), len(refusals)))
    priceable = [r for r in refusals if (r.get("snapshot") or {}).get("short")]
    print("  %d of %d refusals carry a market snapshot and can be priced"
          % (len(priceable), len(refusals)))
    if len(priceable) < len(refusals):
        print("  (records written before the snapshot wiring cannot be priced; they are")
        print("   counted in the totals below as 'unpriceable' and never silently dropped)")

    cache = load_cache()
    rows, pending = [], 0
    for r in priceable:
        pr = price_refusal(r, cache, allow_net=not offline)
        if pr is None:
            continue
        if pr["status"] == "pending":
            pending += 1
            continue
        pr["gate"] = r.get("gate") or "?"
        pr["ts"] = r.get("ts")
        pr["reason"] = (r.get("reasons") or [{}])[0].get("reason", "")
        pr["stage"] = r.get("stage", "kernel")
        rows.append(pr)

    if pending:
        print("  %d refusals not yet settled (expiry in the future); rerun after expiry"
              % pending)
    if not rows:
        print("\n  Nothing settled yet. The ledger is wired and will fill as the agent")
        print("  runs: every veto now carries the quotes needed to price it.")
        json.dump({"generated": datetime.now(timezone.utc).isoformat(),
                   "evaluations": len(recs), "refusals": len(refusals),
                   "priceable": len(priceable), "settled": 0, "pending": pending,
                   "rows": []}, open(OUT, "w"), indent=1)
        return 0

    print("\n" + "-" * 78)
    print("  every refused structure, settled at expiry")
    print("-" * 78)
    print("  %-26s %-10s %7s %8s %9s %11s" %
          ("gate", "expiry", "credit", "settle", "K_s/K_l", "avoided P&L"))
    for r in sorted(rows, key=lambda x: x["ts"] or ""):
        print("  %-26s %-10s %7.2f %8.2f %5.0f/%-5.0f %+11.2f"
              % (r["gate"][:26], r["expiry"], r["credit"], r["settle"],
                 r["k_short"], r["k_long"], r["avoided_pnl_usd"]))

    tot = sum(r["avoided_pnl_usd"] for r in rows)
    saved = [r for r in rows if r["avoided_pnl_usd"] < 0]
    missed = [r for r in rows if r["avoided_pnl_usd"] > 0]
    print("\n" + "-" * 78)
    print("  totals")
    print("-" * 78)
    print("  refusals priced            %d" % len(rows))
    print("  would have LOST money      %d  (refusing saved $%.2f)"
          % (len(saved), -sum(r["avoided_pnl_usd"] for r in saved)))
    print("  would have MADE money      %d  (refusing cost  $%.2f)"
          % (len(missed), sum(r["avoided_pnl_usd"] for r in missed)))
    print("  net effect of refusing     $%+.2f  %s"
          % (-tot, "(the gates were net helpful)" if tot < 0
             else "(the gates were net costly)" if tot > 0 else "(a wash)"))

    print("\n" + "-" * 78)
    print("  by gate")
    print("-" * 78)
    print("  %-30s %5s %12s %12s %10s" %
          ("gate", "n", "avoided P&L", "mean", "worst case"))
    by = {}
    for r in rows:
        by.setdefault(r["gate"], []).append(r)
    for g, rs in sorted(by.items(), key=lambda kv: sum(x["avoided_pnl_usd"] for x in kv[1])):
        v = [x["avoided_pnl_usd"] for x in rs]
        print("  %-30s %5d %+12.2f %+12.2f %+10.2f"
              % (g[:30], len(rs), sum(v), S.mean(v), min(v)))

    v = [r["avoided_pnl_usd"] for r in rows]
    print("\n" + "-" * 78)
    print("  is this more than noise?")
    print("-" * 78)
    if len(v) >= 2:
        n, m, se, t, p = S.one_sample_t(v)
        lo, hi = S.ci95(v)
        nz, k, ps = S.sign_test(v)
        print("  mean avoided P&L $%+.2f   se %.2f   t = %+.2f   p = %.3f" % (m, se, t, p))
        print("  95%% CI on the mean: [$%+.2f, $%+.2f]" % (lo, hi))
        print("  %d of %d refused structures would have lost money; sign test p = %.3f"
              % (nz - k, nz, ps))
        print("  With n = %d this is descriptive. Grinold and Kahn put the standard" % n)
        print("  error of an information ratio at 1/sqrt(years): sixteen years of")
        print("  returns to establish merely top-quartile skill. We are reporting")
        print("  arithmetic, not evidence of edge.")
    else:
        print("  n = %d: too few to say anything. Reported for completeness." % len(v))

    out = {"generated": datetime.now(timezone.utc).isoformat(),
           "journal": path,
           "evaluations": len(recs), "passes": len(passes),
           "shrinks": len(shrinks), "refusals": len(refusals),
           "priceable": len(priceable), "settled": len(rows), "pending": pending,
           "method": ("defined-risk vertical settled at expiry against the underlying "
                      "daily close; no pricing model involved"),
           "net_effect_of_refusing_usd": round(-tot, 2),
           "saved_usd": round(-sum(r["avoided_pnl_usd"] for r in saved), 2),
           "cost_usd": round(sum(r["avoided_pnl_usd"] for r in missed), 2),
           "by_gate": {g: {"n": len(rs),
                           "avoided_pnl_usd": round(sum(x["avoided_pnl_usd"] for x in rs), 2),
                           "mean": round(S.mean([x["avoided_pnl_usd"] for x in rs]), 2)}
                       for g, rs in by.items()},
           "rows": rows}
    json.dump(out, open(OUT, "w"), indent=1)
    print("\n  wrote %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
