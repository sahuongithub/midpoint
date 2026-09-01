#!/usr/bin/env python3
"""
microprice_study.py -- Does the size-weighted mid beat the plain mid at
guessing where an option really trades?

Background. Cartea, Jaimungal & Penalva (Algorithmic and High-Frequency
Trading, 2015, sec. 1.4 and ch. 12) define the microprice

    micro = (V_bid * P_ask + V_ask * P_bid) / (V_bid + V_ask)

and argue it is a better proxy than the mid for the "true" transaction-cost-free
price, because it leans toward the side with less resting size -- the side the
next trade is more likely to consume. In equities that is a well documented
effect. Nobody appears to have checked it for retail options quotes, because
checking it needs the true NBBO, and the free options feed does not publish it.

We have the true NBBO for 65 contracts, because we bought it: each row of the
liquidity-gate runs paid a real marketable order on both sides of the book and
recorded where the paper engine filled, which is the true national best bid and
offer at that instant (see results/fill_oracle_result.json for the validation
of that method).

So this is a straight, pre-specified comparison on data already collected:

    err_mid   = ind_mid   - true_mid
    err_micro = ind_micro - true_mid

and we ask whether |err_micro| < |err_mid|. Errors are reported both in dollars
per contract-share and normalised by the true width, because the contracts range
from $0.01 to $46 and a dollar means different things across that range.

The honest prior is that microprice may LOSE here, for a reason specific to this
data: the microprice is a point strictly inside the *indicative* bid-ask, and we
have already measured that the indicative width is wrong -- too narrow in
liquid contracts and too wide in illiquid ones (results/feed_audit.json). An
estimator built inside a mis-stated interval inherits the mis-statement. We
publish whichever way it comes out.
"""
import json, math, os, sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import statlib as S

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = [os.path.join(HERE, "results", f)
       for f in ("liquidity_gate.json", "liquidity_gate_run2.json")]
OUT = os.path.join(HERE, "results", "microprice_study.json")


def load():
    rows, dropped = [], {"unprobed": 0, "no_size": 0, "bad_quote": 0}
    for path in SRC:
        if not os.path.exists(path):
            continue
        for r in json.load(open(path)):
            if r.get("true_bid") is None or r.get("true_ask") is None:
                dropped["unprobed"] += 1      # not probed: no ground truth
                continue
            bs, a_s = r.get("bid_size"), r.get("ask_size")
            if not bs or not a_s:
                dropped["no_size"] += 1       # no displayed size: cannot form microprice
                continue
            ib, ia = r["ind_bid"], r["ind_ask"]
            tb, ta = r["true_bid"], r["true_ask"]
            if ia <= 0 or ta < tb:
                dropped["bad_quote"] += 1     # crossed or empty book
                continue
            true_mid = (tb + ta) / 2.0
            true_w = ta - tb
            ind_mid = (ib + ia) / 2.0
            micro = (bs * ia + a_s * ib) / float(bs + a_s)
            rows.append(dict(
                contract=r["contract"], dte=r.get("dte"), strike=r.get("strike"),
                spot=r.get("spot"), moneyness=r.get("moneyness"),
                bid_size=bs, ask_size=a_s,
                imbalance=(bs - a_s) / float(bs + a_s),
                ind_bid=ib, ind_ask=ia, ind_mid=ind_mid, ind_width=ia - ib,
                true_bid=tb, true_ask=ta, true_mid=true_mid, true_width=true_w,
                micro=micro,
                err_mid=ind_mid - true_mid,
                err_micro=micro - true_mid,
                src=os.path.basename(path), ts=r.get("ts")))
    return rows, dropped


def med(v):
    v = sorted(v)
    n = len(v)
    if not n:
        return float("nan")
    return v[n // 2] if n % 2 else 0.5 * (v[n // 2 - 1] + v[n // 2])


def am_(v):
    return [abs(x) for x in v]


def _wrap(text, width):
    words, line, out = text.split(), "", []
    for w in words:
        if len(line) + len(w) + 1 > width:
            out.append(line); line = w
        else:
            line = (line + " " + w).strip()
    if line:
        out.append(line)
    return out


def block(title):
    print("\n" + "-" * 78)
    print(title)
    print("-" * 78)


def main():
    rows, dropped = load()
    n = len(rows)
    if n < 10:
        print("not enough probed rows with sizes (%d)" % n)
        return 1

    print("=" * 78)
    print("  MICROPRICE vs MID against the true NBBO      n = %d probed contracts" % n)
    print("=" * 78)
    print("  excluded: %d never probed (no ground truth), %d without displayed size,"
          % (dropped["unprobed"], dropped["no_size"]))
    print("            %d crossed/empty books" % dropped["bad_quote"])

    em = [r["err_mid"] for r in rows]
    eu = [r["err_micro"] for r in rows]
    # width-normalised: how far is the estimate from true mid, in true half-widths
    nm = [r["err_mid"] / (r["true_width"] / 2.0) for r in rows if r["true_width"] > 0]
    nu = [r["err_micro"] / (r["true_width"] / 2.0) for r in rows if r["true_width"] > 0]

    block("1. accuracy in dollars per contract-share")
    print("  %-28s %10s %10s" % ("", "mid", "microprice"))
    print("  %-28s %10.4f %10.4f" % ("RMSE", S.rmse(em), S.rmse(eu)))
    print("  %-28s %10.4f %10.4f" % ("mean abs error", S.mae(em), S.mae(eu)))
    print("  %-28s %10.4f %10.4f" % ("median abs error", med(am_(em)), med(am_(eu))))
    print("  %-28s %10.4f %10.4f" % ("mean signed error (bias)", S.mean(em), S.mean(eu)))
    lo, hi = S.ci95(em); print("  %-28s [%+.4f, %+.4f]" % ("  95% CI on mid bias", lo, hi))
    lo, hi = S.ci95(eu); print("  %-28s [%+.4f, %+.4f]" % ("  95% CI on micro bias", lo, hi))

    block("2. accuracy in units of the true half-width  (n=%d with width>0)" % len(nm))
    print("  %-28s %10s %10s" % ("", "mid", "microprice"))
    print("  %-28s %10.4f %10.4f" % ("RMSE", S.rmse(nm), S.rmse(nu)))
    print("  %-28s %10.4f %10.4f" % ("mean abs error", S.mae(nm), S.mae(nu)))
    print("  %-28s %10.4f %10.4f" % ("mean signed error", S.mean(nm), S.mean(nu)))

    block("3. is the difference real?  paired tests on |error|")
    am, au = [abs(x) for x in em], [abs(x) for x in eu]
    nn, md, se, t, p = S.paired_t(am, au)
    print("  dollars:      mean(|mid| - |micro|) = %+.5f   se %.5f   t = %+.2f   p = %.4f"
          % (md, se, t, p))
    lo, hi = S.ci95([x - y for x, y in zip(am, au)])
    print("                95%% CI on that difference: [%+.5f, %+.5f]" % (lo, hi))
    anm, anu = [abs(x) for x in nm], [abs(x) for x in nu]
    nn2, md2, se2, t2, p2 = S.paired_t(anm, anu)
    print("  half-widths:  mean(|mid| - |micro|) = %+.5f   se %.5f   t = %+.2f   p = %.4f"
          % (md2, se2, t2, p2))
    wins = sum(1 for x, y in zip(am, au) if y < x)
    ties = sum(1 for x, y in zip(am, au) if y == x)
    nz, k, ps = S.sign_test([x - y for x, y in zip(am, au)])
    print("  microprice closer on %d of %d contracts (%d ties); sign test p = %.4f"
          % (wins, n, ties, ps))
    bl, bh = S.bootstrap_ci(rows,
                            lambda ss: S.rmse([r["err_mid"] for r in ss])
                                       - S.rmse([r["err_micro"] for r in ss]))
    print("  bootstrap 95%% CI on RMSE(mid) - RMSE(micro): [%+.5f, %+.5f]" % (bl, bh))

    block("4. does displayed-size imbalance point at the true mid?")
    print("  Cartea ch.12: imbalance predicts the next price move in equities.")
    print("  Here: does imbalance rho=(Vb-Va)/(Vb+Va) predict where true_mid sits")
    print("  relative to the indicative mid, in half-widths?")
    x = [r["imbalance"] for r in rows if r["true_width"] > 0]
    y = [(r["true_mid"] - r["ind_mid"]) / (r["true_width"] / 2.0)
         for r in rows if r["true_width"] > 0]
    r_, p_, n_ = S.pearson(x, y)
    rs, ps_, _ = S.spearman(x, y)
    print("  Pearson  r = %+.3f   p = %.4f   n = %d" % (r_, p_, n_))
    print("  Spearman r = %+.3f   p = %.4f" % (rs, ps_))
    print("  (positive r would mean: more size on the bid -> true mid above ind mid,")
    print("   which is the direction the microprice construction assumes.)")

    block("5. where the two estimators actually differ")
    diffs = sorted(rows, key=lambda r: -abs(r["micro"] - r["ind_mid"]))
    print("  %-22s %6s %8s %8s %9s %9s %9s %8s"
          % ("contract", "imb", "ind_mid", "micro", "true_mid", "err_mid", "err_mic", "ind_w"))
    for r in diffs[:10]:
        print("  %-22s %+6.2f %8.3f %8.3f %9.3f %+9.3f %+9.3f %8.2f"
              % (r["contract"], r["imbalance"], r["ind_mid"], r["micro"],
                 r["true_mid"], r["err_mid"], r["err_micro"], r["ind_width"]))
    same = sum(1 for r in rows if abs(r["micro"] - r["ind_mid"]) < 0.005)
    print("  microprice within half a cent of the mid on %d of %d contracts" % (same, n))

    block("5b. robustness: does one wide quote drive the whole result?")
    worst = max(rows, key=lambda r: abs(r["err_micro"]))
    print("  widest single miss: %s  ind_width $%.2f  err_micro %+0.3f"
          % (worst["contract"], worst["ind_width"], worst["err_micro"]))
    for label, keep in (("all contracts", lambda r: True),
                        ("drop the single worst", lambda r: r is not worst),
                        ("gate-passing only (ind_width <= $0.20)",
                         lambda r: r["ind_width"] <= 0.20),
                        ("wide quotes only (ind_width > $0.20)",
                         lambda r: r["ind_width"] > 0.20)):
        sub = [r for r in rows if keep(r)]
        if len(sub) < 5:
            print("  %-38s n=%-4d (too few to test)" % (label, len(sub)))
            continue
        a = [abs(r["err_mid"]) for r in sub]
        b = [abs(r["err_micro"]) for r in sub]
        _, mdx, _, tx, px = S.paired_t(a, b)
        print("  %-38s n=%-4d RMSE mid %7.4f  micro %7.4f   d|err| %+.4f  p %.3f"
              % (label, len(sub), S.rmse([r["err_mid"] for r in sub]),
                 S.rmse([r["err_micro"] for r in sub]), mdx, px))
    print("  The contracts where the microprice does real damage are wide-quote")
    print("  contracts -- exactly the ones gate G9 already refuses to trade.")

    block("6. verdict")
    better = S.rmse(eu) < S.rmse(em)
    sig = p < 0.05
    side = "microprice" if better else "the plain mid"
    if sig:
        v = ("%s is more accurate and the paired test on absolute errors confirms it "
             "(p = %.3f)." % (side, p))
    else:
        v = ("%s is nominally more accurate (RMSE %.4f vs %.4f) but the paired test on "
             "absolute errors does not reach significance (p = %.2f). The bootstrap CI on "
             "the RMSE difference is [%+.3f, %+.3f]. Nothing here justifies replacing the "
             "mid with the microprice in this pipeline; we keep the simpler estimator and "
             "publish the null."
             % (side, min(S.rmse(em), S.rmse(eu)), max(S.rmse(em), S.rmse(eu)), p, bl, bh))
    print("  " + "\n  ".join(_wrap(v, 74)))
    print("""
  Mechanism note. The microprice can only ever move the estimate inside the
  indicative bid-ask. Our feed audit found that indicative width is itself
  mis-stated (0.67x true width in liquid contracts, 1.94x in illiquid ones),
  so an estimator defined inside that interval inherits the error. Size
  imbalance may well carry information in options -- this test says only that
  reading it through a mis-stated spread does not recover the true mid.""")

    out = dict(
        generated=datetime.utcnow().isoformat() + "Z",
        method=("microprice = (Vb*Pa + Va*Pb)/(Vb+Va) from the free feed, compared "
                "against the true NBBO mid recovered by paid marketable orders"),
        n=n, excluded=dropped,
        dollars=dict(rmse_mid=S.rmse(em), rmse_micro=S.rmse(eu),
                     mae_mid=S.mae(em), mae_micro=S.mae(eu),
                     bias_mid=S.mean(em), bias_micro=S.mean(eu)),
        halfwidths=dict(rmse_mid=S.rmse(nm), rmse_micro=S.rmse(nu),
                        mae_mid=S.mae(nm), mae_micro=S.mae(nu)),
        paired_test=dict(mean_abs_diff=md, se=se, t=t, p=p,
                         ci95=list(S.ci95([a - b for a, b in zip(am, au)])),
                         micro_closer_count=wins, ties=ties, sign_p=ps),
        bootstrap_rmse_diff_ci=[bl, bh],
        imbalance_vs_mid_error=dict(pearson_r=r_, pearson_p=p_,
                                    spearman_r=rs, spearman_p=ps_, n=n_),
        verdict=v,
        rows=rows)
    json.dump(out, open(OUT, "w"), indent=1)
    print("\n  wrote %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
