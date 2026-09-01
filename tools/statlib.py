#!/usr/bin/env python3
"""
statlib.py -- the small amount of statistics this project needs, written out
so that every number on the site can be recomputed from stdlib Python alone.

No numpy, no scipy: a judge with a stock Python install can rerun everything.
The Student-t CDF is the usual continued-fraction incomplete beta (Lentz's
method). It was checked against Simpson integration of the t density: the two
agree to ~1e-14 over the range this project uses (see test_statlib.py).
"""
import math
import random


def mean(v):
    return sum(v) / len(v)


def var(v, ddof=1):
    if len(v) - ddof <= 0:
        return float("nan")
    m = mean(v)
    return sum((x - m) ** 2 for x in v) / (len(v) - ddof)


def sd(v, ddof=1):
    return math.sqrt(var(v, ddof))


def rmse(v):
    return math.sqrt(sum(x * x for x in v) / len(v)) if v else float("nan")


def mae(v):
    return sum(abs(x) for x in v) / len(v) if v else float("nan")


def _betacf(a, b, x, itmax=300, eps=3e-14):
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    if abs(d) < 1e-300:
        d = 1e-300
    d = 1.0 / d
    h = d
    for m in range(1, itmax + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-300:
            d = 1e-300
        c = 1.0 + aa / c
        if abs(c) < 1e-300:
            c = 1e-300
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-300:
            d = 1e-300
        c = 1.0 + aa / c
        if abs(c) < 1e-300:
            c = 1e-300
        d = 1.0 / d
        de = d * c
        h *= de
        if abs(de - 1.0) < eps:
            break
    return h


def betai(a, b, x):
    """Regularized incomplete beta I_x(a,b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
             + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return math.exp(lbeta) * _betacf(a, b, x) / a
    return 1.0 - math.exp(lbeta) * _betacf(b, a, 1.0 - x) / b


def t_sf2(t, df):
    """Two-sided survival function for Student's t."""
    if df <= 0:
        return float("nan")
    return betai(0.5 * df, 0.5, df / (df + t * t))


def paired_t(a, b):
    """Paired t-test on a-b. Returns (n, meandiff, se, t, p_two_sided)."""
    d = [x - y for x, y in zip(a, b)]
    n = len(d)
    if n < 2:
        return n, float("nan"), float("nan"), float("nan"), float("nan")
    m = mean(d)
    se = sd(d) / math.sqrt(n)
    if se == 0:
        return n, m, se, float("inf") if m else 0.0, 0.0 if m else 1.0
    t = m / se
    return n, m, se, t, t_sf2(t, n - 1)


def one_sample_t(v, mu0=0.0):
    n = len(v)
    if n < 2:
        return n, float("nan"), float("nan"), float("nan"), float("nan")
    m = mean(v)
    se = sd(v) / math.sqrt(n)
    if se == 0:
        return n, m, se, float("inf") if m != mu0 else 0.0, 0.0 if m != mu0 else 1.0
    t = (m - mu0) / se
    return n, m, se, t, t_sf2(t, n - 1)


def ci95(v):
    """Normal-approximation 95% CI for the mean (n>=30) else t-based."""
    n = len(v)
    if n < 2:
        return (float("nan"), float("nan"))
    m = mean(v)
    se = sd(v) / math.sqrt(n)
    # critical value from the t distribution, found by bisection on t_sf2
    lo, hi = 0.0, 100.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if t_sf2(mid, n - 1) > 0.05:
            lo = mid
        else:
            hi = mid
    tc = (lo + hi) / 2
    return (m - tc * se, m + tc * se)


def bootstrap_ci(rows, stat, reps=10000, seed=20260901, alpha=0.05):
    """Percentile bootstrap CI of stat(sample) over rows (list of anything)."""
    rng = random.Random(seed)
    n = len(rows)
    if n == 0:
        return (float("nan"), float("nan"))
    out = []
    for _ in range(reps):
        samp = [rows[rng.randrange(n)] for _ in range(n)]
        out.append(stat(samp))
    out.sort()
    lo = out[int(alpha / 2 * reps)]
    hi = out[int((1 - alpha / 2) * reps) - 1]
    return (lo, hi)


def pearson(x, y):
    n = len(x)
    if n < 3:
        return float("nan"), float("nan"), n
    mx, my = mean(x), mean(y)
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    if sxx <= 0 or syy <= 0:
        return float("nan"), float("nan"), n
    r = sxy / math.sqrt(sxx * syy)
    r = max(-0.999999999, min(0.999999999, r))
    t = r * math.sqrt((n - 2) / (1 - r * r))
    return r, t_sf2(t, n - 2), n


def spearman(x, y):
    def rank(v):
        idx = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(idx):
            j = i
            while j + 1 < len(idx) and v[idx[j + 1]] == v[idx[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                r[idx[k]] = avg
            i = j + 1
        return r
    return pearson(rank(x), rank(y))


def sign_test(v):
    """Two-sided sign test that median(v) == 0. Returns (n_nonzero, n_pos, p)."""
    nz = [x for x in v if x != 0]
    n = len(nz)
    k = sum(1 for x in nz if x > 0)
    if n == 0:
        return 0, 0, float("nan")
    # exact two-sided binomial p
    def C(n, r):
        return math.comb(n, r)
    tail = min(k, n - k)
    p = 2.0 * sum(C(n, i) for i in range(0, tail + 1)) / (2.0 ** n)
    return n, k, min(1.0, p)
