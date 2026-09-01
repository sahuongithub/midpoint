#!/usr/bin/env python3
"""
spread_curve_analyze.py -- read the day's samples and test the U-shape.

Reports, for each tracked contract and for each expiry, the median quoted width
by half-hour bucket, and then the specific comparisons that matter to the
agent's schedule:

    open (09:30-10:00)  vs  our opening window (10:00-14:00)
    our opening window  vs  our flatten window (15:00-15:30)

using a Mann-Whitney style rank comparison as well as medians, because widths
are discrete, bounded below by a tick, and badly non-normal.
"""
import glob, json, math, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import statlib as S

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_GLOB = os.path.join(HERE, "results", "spread_curve.*.jsonl")
OUT = os.path.join(HERE, "results", "spread_curve_summary.json")

BUCKETS = [(570, 600, "09:30-10:00"), (600, 660, "10:00-11:00"),
           (660, 720, "11:00-12:00"), (720, 780, "12:00-13:00"),
           (780, 840, "13:00-14:00"), (840, 900, "14:00-15:00"),
           (900, 930, "15:00-15:30"), (930, 960, "15:30-16:00")]


def med(v):
    v = sorted(v); n = len(v)
    if not n: return float("nan")
    return v[n // 2] if n % 2 else 0.5 * (v[n // 2 - 1] + v[n // 2])


def mannwhitney_p(a, b):
    """Normal-approximation two-sided Mann-Whitney U (ties corrected crudely)."""
    n1, n2 = len(a), len(b)
    if n1 < 5 or n2 < 5:
        return float("nan")
    allv = [(x, 0) for x in a] + [(x, 1) for x in b]
    allv.sort()
    ranks, i = [0.0] * len(allv), 0
    while i < len(allv):
        j = i
        while j + 1 < len(allv) and allv[j + 1][0] == allv[i][0]:
            j += 1
        r = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[k] = r
        i = j + 1
    r1 = sum(r for r, (_, g) in zip(ranks, allv) if g == 0)
    u1 = r1 - n1 * (n1 + 1) / 2.0
    mu = n1 * n2 / 2.0
    sd = math.sqrt(n1 * n2 * (n1 + n2 + 1) / 12.0)
    if sd == 0:
        return float("nan")
    z = (u1 - mu) / sd
    return S.t_sf2(abs(z), 10 ** 6)


def main():
    files = sorted(glob.glob(SRC_GLOB))
    if not files:
        print("no samples yet (%s) -- run tools/spread_curve.py during a session" % SRC_GLOB)
        return 0
    src = files[-1]
    for i, a in enumerate(sys.argv):
        if a == "--file" and i + 1 < len(sys.argv):
            src = sys.argv[i + 1]
    if len(files) > 1:
        print("  %d session files present; analysing %s" % (len(files), os.path.basename(src)))
    rows = [json.loads(l) for l in open(src) if l.strip()]
    if not rows:
        print("empty sample file"); return 0

    legs = []
    for r in rows:
        for l in r["legs"]:
            legs.append({**l, "et_minutes": r["et_minutes"], "spot": r["spot"],
                         "et": r["et"]})
    print("=" * 78)
    print("  INTRADAY QUOTED WIDTH, SPY OPTIONS      %d samples, %d leg observations"
          % (len(rows), len(legs)))
    print("  span %s to %s ET" % (rows[0]["et"], rows[-1]["et"]))
    print("=" * 78)

    tags = sorted({l["tag"] for l in legs})
    exps = sorted({l["expiry"] for l in legs})
    summary = {}

    for exp in exps:
        print("\n  expiry %s" % exp)
        print("  %-16s %8s %9s %9s %9s %8s"
              % ("bucket", "n", "med width", "med rel", "med spot", "med size"))
        for lo, hi, label in BUCKETS:
            sel = [l for l in legs if l["expiry"] == exp
                   and lo <= l["et_minutes"] < hi and l["tag"] == "rolling_atm"]
            if not sel:
                continue
            sizes = [l["ask_size"] for l in sel if l.get("ask_size")]
            print("  %-16s %8d %9.4f %9.5f %9.2f %8s"
                  % (label, len(sel), med([l["width"] for l in sel]),
                     med([l["rel_width"] for l in sel if l["rel_width"]]),
                     med([l["spot"] for l in sel]),
                     "%.0f" % med(sizes) if sizes else "-"))

        win = [l["width"] for l in legs if l["expiry"] == exp
               and l["tag"] == "rolling_atm" and 600 <= l["et_minutes"] < 840]
        cls = [l["width"] for l in legs if l["expiry"] == exp
               and l["tag"] == "rolling_atm" and 900 <= l["et_minutes"] < 930]
        opn = [l["width"] for l in legs if l["expiry"] == exp
               and l["tag"] == "rolling_atm" and 570 <= l["et_minutes"] < 600]
        line = []
        if opn and win:
            line.append("open %.4f vs window %.4f (p=%.3f)"
                        % (med(opn), med(win), mannwhitney_p(opn, win)))
        if win and cls:
            line.append("window %.4f vs flatten %.4f (p=%.3f)"
                        % (med(win), med(cls), mannwhitney_p(win, cls)))
        if line:
            print("  " + " | ".join(line))
        summary[exp] = {
            "buckets": {label: {
                "n": len([l for l in legs if l["expiry"] == exp
                          and lo <= l["et_minutes"] < hi and l["tag"] == "rolling_atm"]),
                "median_width": med([l["width"] for l in legs if l["expiry"] == exp
                                     and lo <= l["et_minutes"] < hi
                                     and l["tag"] == "rolling_atm"])}
                for lo, hi, label in BUCKETS},
            "median_open": med(opn) if opn else None,
            "median_window": med(win) if win else None,
            "median_flatten": med(cls) if cls else None,
            "p_open_vs_window": mannwhitney_p(opn, win) if (opn and win) else None,
            "p_window_vs_flatten": mannwhitney_p(win, cls) if (win and cls) else None,
        }

    print("\n  pinned contracts (same contract all day, so no strike drift):")
    print("  %-24s %-12s %8s %10s %10s"
          % ("symbol", "tag", "n", "med width", "IQR"))
    for tag in tags:
        if not tag.startswith("pinned"):
            continue
        for sym in sorted({l["symbol"] for l in legs if l["tag"] == tag}):
            sel = [l["width"] for l in legs if l["symbol"] == sym]
            if len(sel) < 5:
                continue
            q = sorted(sel)
            iqr = q[int(0.75 * len(q))] - q[int(0.25 * len(q))]
            print("  %-24s %-12s %8d %10.4f %10.4f" % (sym, tag, len(sel), med(sel), iqr))

    json.dump({"source": os.path.basename(src), "samples": len(rows),
               "observations": len(legs), "by_expiry": summary},
              open(OUT, "w"), indent=1)
    print("\n  wrote %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
