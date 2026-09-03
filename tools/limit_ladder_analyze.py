#!/usr/bin/env python3
"""
limit_ladder_analyze.py -- read the ladder and answer the question it was built for.

The question is not "did some order fill". It is: how far above the price the agent
currently pays can a spread be quoted and still fill, and what does the venue actually
charge when it does. Two numbers matter.

  FILL RATE BY RUNG      whether a mid-priced spread fills at all, and how often.
  FILL MINUS FAIR        what the fill was worth against the mid at the moment of
                         submission -- the money given away, per share.

A fill rate that holds up at theta = 0.5 means the agent has been paying the full
quoted spread for nothing, on every trade it has ever made.
"""
import json, os, sys, collections, statistics

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import statlib

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(HERE, "results", "limit_ladder.jsonl")
OUT = os.path.join(HERE, "results", "limit_ladder_summary.json")


def wilson(k, n, z=1.96):
    """A fill rate from six attempts needs an interval, not a percentage."""
    if n == 0:
        return (0.0, 0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return (p, max(0.0, centre - half), min(1.0, centre + half))


def main(argv):
    if not os.path.exists(SRC):
        print("no ladder data at %s -- run tools/limit_ladder.py first" % SRC)
        return 1
    rows = []
    for line in open(SRC):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("filled") is None:
            continue                     # errors and skips are not attempts
        rows.append(r)
    if not rows:
        print("no completed rungs in %s" % SRC)
        return 1

    by = collections.defaultdict(list)
    for r in rows:
        by[r["theta"]].append(r)

    print("=" * 78)
    print("  WHAT A SPREAD HAS TO GIVE UP TO FILL      %d attempts" % len(rows))
    print("=" * 78)
    print("  theta 0 is what the agent sends today (short bid, long ask).")
    print("  theta 0.5 is the mid on both legs. theta 1 is fully passive.\n")
    print("  %-7s %-10s %-22s %-16s %s"
          % ("theta", "attempts", "filled", "fill rate 95% CI", "median fill vs fair"))

    summary = {}
    for theta in sorted(by):
        rs = by[theta]
        n = len(rs)
        fills = [r for r in rs if r["filled"]]
        p, lo, hi = wilson(len(fills), n)
        deltas = sorted(r["fill_minus_fair"] for r in fills if "fill_minus_fair" in r)
        med = deltas[len(deltas) // 2] if deltas else None
        print("  %-7.2f %-10d %-22s [%.2f, %.2f]%s   %s"
              % (theta, n, "%d  (%.0f%%)" % (len(fills), 100 * p), lo, hi,
                 " " * 4, ("%+0.3f" % med) if med is not None else "--"))
        summary["%.2f" % theta] = {
            "attempts": n, "fills": len(fills), "fill_rate": round(p, 4),
            "ci95": [round(lo, 4), round(hi, 4)],
            "median_fill_minus_fair": med,
            "median_limit_minus_fair": statistics.median(
                [r["limit_minus_fair"] for r in rs]) if rs else None,
        }

    agg = summary.get("0.00")
    mid = summary.get("0.50")
    print()
    if agg and mid and agg["attempts"] and mid["attempts"]:
        if mid["fills"] == 0:
            verdict = ("A mid-priced spread never filled. Crossing the full quoted "
                       "spread is what this venue requires, and the agent's pricing "
                       "is not a defect -- it is the price of admission.")
        elif mid["fill_rate"] >= 0.5:
            saved = None
            if mid["median_fill_minus_fair"] is not None and \
               agg["median_fill_minus_fair"] is not None:
                saved = mid["median_fill_minus_fair"] - agg["median_fill_minus_fair"]
            verdict = ("A mid-priced spread filled %d of %d times. The agent has been "
                       "paying the full quoted spread when roughly half of it would "
                       "have done%s."
                       % (mid["fills"], mid["attempts"],
                          "" if saved is None else
                          ", worth %+0.3f a share on the median fill" % saved))
        else:
            verdict = ("A mid-priced spread filled %d of %d times. There is something "
                       "here, but the rate is too low to price into the agent without "
                       "measuring the cost of the misses."
                       % (mid["fills"], mid["attempts"]))
        print("  " + "\n  ".join(_wrap(verdict, 74)))

    json.dump({"attempts": len(rows), "by_theta": summary}, open(OUT, "w"), indent=1)
    print("\n  wrote %s" % OUT)
    return 0


def _wrap(s, n):
    out, line = [], ""
    for w in s.split():
        if len(line) + len(w) + 1 > n:
            out.append(line); line = w
        else:
            line = (line + " " + w).strip()
    if line:
        out.append(line)
    return out


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
