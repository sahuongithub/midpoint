"""
pricing.py -- Black-Scholes-Merton, implied volatility, and greeks.

Exists because Alpaca returns no greeks at all for 0DTE contracts (a Black-Scholes
limitation, not a bug: time-to-expiry sits in the denominator of d1, so the greeks
are indeterminate at T=0). Any expiration-day behaviour needs its own engine on an
intraday clock.

Time convention: T is in years, measured on an INTRADAY clock, so a contract with
three hours left on expiry day is T = 3 / 6.5 / 252, not zero.
"""

import math

TRADING_DAYS = 252.0
SESSION_HOURS = 6.5
MIN_T = 1e-6            # floor so d1/d2 stay finite in the last seconds


def year_fraction(days_to_expiry, hours_left_today=None):
    """Days to expiry as a year fraction on a trading-day clock."""
    if hours_left_today is not None and days_to_expiry <= 0:
        return max(MIN_T, hours_left_today / SESSION_HOURS / TRADING_DAYS)
    t = days_to_expiry / TRADING_DAYS
    if hours_left_today is not None:
        t = (days_to_expiry - 1 + hours_left_today / SESSION_HOURS) / TRADING_DAYS
    return max(MIN_T, t)


def _nd(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _Nd(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def d1d2(S, K, T, sigma, r=0.0, q=0.0):
    if S <= 0 or K <= 0 or T <= 0 or sigma <= 0:
        return None, None
    v = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / v
    return d1, d1 - v


def price(S, K, T, sigma, is_call, r=0.0, q=0.0):
    """BSM price. Falls back to intrinsic when the model degenerates."""
    intrinsic = max(0.0, (S - K) if is_call else (K - S))
    d1, d2 = d1d2(S, K, T, sigma, r, q)
    if d1 is None:
        return intrinsic
    df_r, df_q = math.exp(-r * T), math.exp(-q * T)
    if is_call:
        return S * df_q * _Nd(d1) - K * df_r * _Nd(d2)
    return K * df_r * _Nd(-d2) - S * df_q * _Nd(-d1)


def greeks(S, K, T, sigma, is_call, r=0.0, q=0.0):
    """delta, gamma, vega (per 1 vol point), theta (per calendar day)."""
    d1, d2 = d1d2(S, K, T, sigma, r, q)
    if d1 is None:
        return {"delta": 1.0 if (is_call and S > K) else (-1.0 if (not is_call and S < K) else 0.0),
                "gamma": 0.0, "vega": 0.0, "theta": 0.0}
    df_r, df_q = math.exp(-r * T), math.exp(-q * T)
    sqrtT = math.sqrt(T)
    delta = df_q * (_Nd(d1) if is_call else _Nd(d1) - 1.0)
    gamma = df_q * _nd(d1) / (S * sigma * sqrtT)
    vega = S * df_q * _nd(d1) * sqrtT / 100.0
    common = -(S * df_q * _nd(d1) * sigma) / (2.0 * sqrtT)
    if is_call:
        theta = (common - r * K * df_r * _Nd(d2) + q * S * df_q * _Nd(d1)) / 365.0
    else:
        theta = (common + r * K * df_r * _Nd(-d2) - q * S * df_q * _Nd(-d1)) / 365.0
    return {"delta": delta, "gamma": gamma, "vega": vega, "theta": theta}


def implied_vol(target, S, K, T, is_call, r=0.0, q=0.0, lo=1e-4, hi=8.0, tol=1e-7):
    """Bisection on price. Robust where Newton diverges near expiry and deep OTM."""
    intrinsic = max(0.0, (S - K) if is_call else (K - S))
    if target <= intrinsic + 1e-10 or T <= MIN_T:
        return None
    if price(S, K, T, hi, is_call, r, q) < target:
        return None
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        if price(S, K, T, mid, is_call, r, q) < target:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    return 0.5 * (lo + hi)


def fair_value_from_underlying(anchor_price, anchor_S, new_S, delta, gamma):
    """
    Muravyev-Pearson mechanism: option fair value is predictable at high frequency
    from the underlying's move, holding implied volatility fixed. Second-order
    Taylor expansion around the anchor.
    """
    ds = new_S - anchor_S
    return anchor_price + delta * ds + 0.5 * gamma * ds * ds
