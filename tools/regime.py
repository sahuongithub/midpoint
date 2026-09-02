"""
regime.py -- the regime gate. Decides whether short-premium conditions are on.

THE EVIDENCE
------------
Johnson, "Risk Premia and the VIX Term Structure" (JFQA 52(6), 2017, 2461-2490):
the SHAPE of the VIX term structure carries information about the PRICE OF VARIANCE
RISK rather than expected changes in VIX -- a rejection of the expectations hypothesis.
The second principal component, which he calls SLOPE, "summarizes nearly all this
information, predicting the excess returns of synthetic S&P 500 variance swaps, VIX
futures, and S&P 500 straddles for all maturities and to the exclusion of the rest of
the term structure." The LEVEL of implied volatility carries no such information,
which is why IV rank is not used anywhere in this project.

WHY THIS FILE IS NOT CALLED signal.py
------------------------------------
It was, and that was a latent fault. Python resolves a script's own directory before
the standard library, so a module named signal.py here became THE signal module for
the whole process -- including for stdlib machinery that quietly depends on it.
subprocess reaches for signal.SIGKILL when it terminates a child, found this file
instead, and the agent lost a cycle to
AttributeError: module 'signal' has no attribute 'SIGKILL'.
The regime gate had nothing to do with it. Nothing in this project may take the name
of a standard-library module.

HONEST LIMITATION
-----------------
Johnson's SLOPE is the second principal component of the whole term structure. We use
a TWO-POINT PROXY: the VIX / VIX3M ratio. PC2 of a term structure is almost always a
long-minus-short contrast, so the proxy is well motivated, but it is a proxy and is
labelled as one. We do not claim to have implemented PC2.

WHY VIX3M AND NOT VIX9D
-----------------------
The 9-day index is documented as too noisy for regime detection; the 3-month index is
the stable anchor. Contango holds roughly 85% of the time, and VIX closes above VIX3M
on about 8% of trading days -- the rarity is what makes the signal informative.

DATA
----
Alpaca serves no index data at all, so this comes from outside: Yahoo primary, Cboe
fallback. Two independent sources, because a signal with one source is a single point
of failure that halts the agent.
"""

from __future__ import annotations

import json, ssl, urllib.request
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional

CONTANGO, BACKWARDATION = "contango", "backwardation"
_CTX = ssl.create_default_context()
_UA = {"User-Agent": "Mozilla/5.0"}


@dataclass
class Regime:
    vix: float
    vix3m: float
    vix9d: Optional[float]
    ratio: float                 # VIX / VIX3M
    regime: str
    short_premium_ok: bool
    source: str
    asof: str

    def explain(self) -> str:
        return ("VIX %.2f / VIX3M %.2f = %.3f -> %s. Short premium %s."
                % (self.vix, self.vix3m, self.ratio, self.regime.upper(),
                   "PERMITTED" if self.short_premium_ok else "STOOD DOWN"))


def _get(url, timeout=15):
    return urllib.request.urlopen(
        urllib.request.Request(url, headers=_UA), timeout=timeout, context=_CTX).read().decode()


def _yahoo(sym: str) -> float:
    u = "https://query1.finance.yahoo.com/v8/finance/chart/%s?range=1d&interval=1d" % sym
    m = json.loads(_get(u))["chart"]["result"][0]["meta"]
    px = m.get("regularMarketPrice")
    if px is None:
        raise ValueError("no price for %s" % sym)
    return float(px)


def _cboe(sym: str) -> float:
    u = "https://cdn.cboe.com/api/global/delayed_quotes/quotes/_%s.json" % sym
    return float(json.loads(_get(u))["data"]["current_price"])


def read_regime(backwardation_threshold: float = 1.0) -> Regime:
    """Ratio at or above the threshold means backwardation: stand down.

    Threshold is 1.0 by construction rather than fitted -- it is the point where the
    curve inverts, not a parameter we tuned. Anything fitted would need to be
    pre-registered; this does not.
    """
    vix = vix3m = vix9d = None
    source = "yahoo"
    try:
        vix, vix3m = _yahoo("^VIX"), _yahoo("^VIX3M")
        try: vix9d = _yahoo("^VIX9D")
        except Exception: pass
    except Exception:
        source = "cboe"
        vix, vix3m = _cboe("VIX"), _cboe("VIX3M")
    if not vix or not vix3m:
        raise RuntimeError("could not read the VIX term structure from any source")

    ratio = vix / vix3m
    inverted = ratio >= backwardation_threshold
    return Regime(vix=round(vix, 2), vix3m=round(vix3m, 2),
                  vix9d=round(vix9d, 2) if vix9d else None,
                  ratio=round(ratio, 4),
                  regime=BACKWARDATION if inverted else CONTANGO,
                  short_premium_ok=not inverted,
                  source=source,
                  asof=datetime.now(timezone.utc).isoformat())


if __name__ == "__main__":
    r = read_regime()
    print(json.dumps(asdict(r), indent=2))
    print("\n" + r.explain())
