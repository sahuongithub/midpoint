#!/usr/bin/env python3
"""
fv_analyze.py -- Does the underlying predict the option's next price?

Compares two predictors of mid(t+h):
    naive           : mid(t)                                  (random walk)
    delta-adjusted  : mid(t) + delta*dS + 0.5*gamma*dS^2      (Muravyev-Pearson)

Reports RMSE of each and the improvement. For 0DTE contracts Alpaca supplies no
greeks at all, so we back out implied vol from the mid with our own engine and
compute the greeks ourselves on an intraday clock -- which is the point of having
the engine.
"""
import json, math, os, sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pricing


def rmse(v):
    return math.sqrt(sum(x * x for x in v) / len(v)) if v else float("nan")


def hours_left(iso):
    t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    et_hour = (t.hour - 4) + t.minute / 60.0        # EDT
    return max(0.02, 16.0 - et_hour)


def main(path):
    d = json.load(open(path))
    S, tg = d["samples"], {t["osi"]: t for t in d["targets"]}
    dt = d.get("interval_s", 2.0)
    print("=" * 96)
    print("  FAIR-VALUE PREDICTION STUDY   n=%d samples   nominal interval %.1fs" % (len(S), dt))
    print("=" * 96)

    print("\n--- 1. how noisy is the free feed's mid, tick to tick? ---")
    print("  %-22s %-10s %8s %9s %9s %9s" % ("contract", "label", "n", "sd(dmid)", "med|dmid|", "mean mid"))
    for osi, t in tg.items():
        m = [s["opts"][osi]["mid"] for s in S if s["opts"][osi]["mid"]]
        if len(m) < 10: continue
        dm = [m[i+1] - m[i] for i in range(len(m)-1)]
        mu = sum(dm)/len(dm)
        sd = math.sqrt(sum((x-mu)**2 for x in dm)/len(dm))
        med = sorted(abs(x) for x in dm)[len(dm)//2]
        print("  %-22s %-10s %8d %9.4f %9.4f %9.3f"
              % (osi, t["label"], len(m), sd, med, sum(m)/len(m)))

    print("\n--- 2. does the underlying predict the option's next mid? ---")
    for H in (1, 2, 5, 10, 20):
        print("\n  horizon h = %d samples  (~%.0fs)" % (H, H * dt))
        print("  %-22s %-10s %6s %10s %10s %9s %7s"
              % ("contract", "label", "n", "RMSE naive", "RMSE d-adj", "improve", "greeks"))
        for osi, t in tg.items():
            u = t["underlying"]; is_call = t["type"] == "call"; K = t["strike"]
            rows = [s for s in S if s["opts"][osi]["mid"] and s["spot"].get(u)]
            if len(rows) < H + 12: continue
            en, ea, src = [], [], "alpaca"
            for i in range(len(rows) - H):
                a, b = rows[i], rows[i + H]
                m0, m1 = a["opts"][osi]["mid"], b["opts"][osi]["mid"]
                s0, s1 = a["spot"][u], b["spot"][u]
                dl, gm = a["opts"][osi]["delta"], a["opts"][osi]["gamma"]
                if dl is None:                      # 0DTE: build them ourselves
                    src = "OURS"
                    T = pricing.year_fraction(0, hours_left(a["iso"]))
                    iv = pricing.implied_vol(m0, s0, K, T, is_call)
                    if not iv: continue
                    g = pricing.greeks(s0, K, T, iv, is_call)
                    dl, gm = g["delta"], g["gamma"]
                en.append(m1 - m0)
                ea.append(m1 - pricing.fair_value_from_underlying(m0, s0, s1, dl, gm))
            if len(en) < 12: continue
            rn, ra = rmse(en), rmse(ea)
            print("  %-22s %-10s %6d %10.4f %10.4f %8.1f%% %7s"
                  % (osi, t["label"], len(en), rn, ra, (1 - ra/rn) * 100 if rn else 0, src))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/midpoint/docs/fv_study.json"))
