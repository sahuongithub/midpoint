#!/usr/bin/env python3
"""Checks statlib against independent numerical methods. Run: python3 tools/test_statlib.py"""
import math, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import statlib as S

fails = []
def ck(name, got, want, tol):
    ok = abs(got - want) <= tol
    print("  %-46s %14.10f  vs %14.10f  %s" % (name, got, want, "ok" if ok else "FAIL"))
    if not ok: fails.append(name)

def tpdf(x, df):
    return (math.exp(math.lgamma((df+1)/2) - math.lgamma(df/2))
            / math.sqrt(df*math.pi) * (1 + x*x/df) ** (-(df+1)/2))

def tsf2_numeric(t, df, N=200000, hi=80.0):
    a, b = t, hi; h = (b-a)/N; tot = tpdf(a, df) + tpdf(b, df)
    for i in range(1, N):
        tot += (4 if i % 2 else 2) * tpdf(a + i*h, df)
    return 2 * tot * h / 3

print("Student-t two-sided tail vs Simpson integration of the density:")
for t, df in [(2.0, 60), (1.96, 1000), (2.5, 9), (0.31, 9), (3.0, 24), (0.0, 5)]:
    ck("t_sf2(%.2f, %d)" % (t, df), S.t_sf2(t, df), tsf2_numeric(t, df) if t > 0 else 1.0, 1e-9)

print("\nincomplete beta at analytic points:")
ck("betai(0.5,0.5,0.5)", S.betai(0.5, 0.5, 0.5), 0.5, 1e-12)
ck("betai(1,1,0.3)", S.betai(1, 1, 0.3), 0.3, 1e-12)
ck("betai(2,3,0.5)", S.betai(2, 3, 0.5), 0.6875, 1e-12)

print("\nmoments and correlations:")
ck("mean", S.mean([1, 2, 3, 4]), 2.5, 1e-12)
ck("sd (ddof=1)", S.sd([2, 4, 4, 4, 5, 5, 7, 9]), 2.13808993, 1e-8)
ck("pearson exact +1", S.pearson([1, 2, 3, 4], [2, 4, 6, 8])[0], 1.0, 1e-6)
ck("pearson exact -1", S.pearson([1, 2, 3, 4], [-2, -4, -6, -8])[0], -1.0, 1e-6)
ck("spearman monotone", S.spearman([1, 2, 3, 4], [1, 10, 100, 1000])[0], 1.0, 1e-6)

print("\npaired t against hand calculation:")
a = [5.1, 4.9, 6.2, 5.8, 5.0]; b = [4.8, 4.7, 5.9, 5.6, 4.9]
n, m, se, t, p = S.paired_t(a, b)
d = [x - y for x, y in zip(a, b)]
ck("paired mean diff", m, sum(d)/len(d), 1e-12)
ck("paired t", t, (sum(d)/len(d))/(S.sd(d)/math.sqrt(len(d))), 1e-12)

print("\nsign test (exact binomial):")
n_, k, p = S.sign_test([1]*9 + [-1])
ck("sign p (9 of 10)", p, 2*(math.comb(10,0)+math.comb(10,1))/2**10, 1e-12)

print("\n%s" % ("ALL PASS" if not fails else "FAILURES: " + ", ".join(fails)))
sys.exit(1 if fails else 0)
