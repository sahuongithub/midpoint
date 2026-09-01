"""
structure.py -- turns a regime signal into a concrete, priced, defined-risk proposal.

STRIKE SELECTION, AND WHAT IT CAN AND CANNOT DO
----------------------------------------------
Backtests across delta targets find that higher short-strike delta "moves equity vol
rather than alpha" -- 10, 30 and 45 delta produce similar risk-adjusted results at
different volatility levels. The options market prices this correctly: the credit
received is calibrated to the probability of loss.

So delta is a RISK-SHAPE knob, not an alpha knob, and we treat it as one. The default
short delta of 0.20 is chosen for two reasons that are ours rather than folklore:
it sits where our own measurements found spreads tightest, and it keeps max loss small
enough that the 0.25% per-trade cap permits a meaningful number of contracts.

DIRECTION
---------
Put credit spread by default. We have no directional edge and do not claim one; the
put side is chosen because equity drift is mildly upward, which is a structural
tailwind rather than a forecast. Direction is a parameter, not a conviction.

THE LIQUIDITY GATE IS ENFORCED HERE, NOT JUST REPORTED
------------------------------------------------------
Both legs must pass the $0.20 quoted-width test before a proposal is even constructed.
That threshold is not chosen -- it was measured, on 65 contracts, and validated out of
sample. It is the single highest-value execution decision available: contract selection
was worth ~80x more than execution timing in our own experiment.
"""

from __future__ import annotations

import json, os, sys, urllib.parse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import alpaca_io as aio
import pricing
from risk_kernel import Proposal, Leg


@dataclass
class Candidate:
    symbol: str
    strike: float
    expiry: str
    dte: int
    kind: str            # call | put
    bid: float
    ask: float
    delta: Optional[float]
    greeks_source: str

    @property
    def mid(self): return (self.bid + self.ask) / 2

    @property
    def width(self): return self.ask - self.bid


def _et_date():
    """The effective TRADING date, taken from the exchange clock rather than wall time.

    Two ways to get this wrong, both of which we hit:
      * computing on the UTC date mislabels a next-session expiry as 0DTE overnight
      * computing on the ET date AFTER the close selects contracts that already expired
    The clock endpoint reports whether the market is open and when it next opens, which
    settles both. This matters because the risk kernel treats 0DTE specially.
    """
    try:
        c = aio.req("GET", "%s/v2/clock" % aio.TRADING)
        if c.get("is_open"):
            return datetime.fromisoformat(c["timestamp"][:19]).date()
        return datetime.fromisoformat(c["next_open"][:19]).date()
    except Exception:
        return (datetime.now(timezone.utc) - timedelta(hours=4)).date()


def _hours_left_et() -> float:
    et = datetime.now(timezone.utc) - timedelta(hours=4)
    return max(0.02, 16.0 - (et.hour + et.minute / 60.0))


def fetch_chain(underlying: str, min_dte: int, max_dte: int, kind: str,
                spot: float, span: float = 0.10) -> list:
    """Contracts within +/- span of spot, with quotes and greeks attached."""
    today = _et_date()
    j = aio.req("GET", "%s/v2/options/contracts" % aio.TRADING, params={
        "underlying_symbols": underlying, "status": "active", "type": kind,
        "expiration_date_gte": str(today + timedelta(days=min_dte)),
        "expiration_date_lte": str(today + timedelta(days=max_dte)),
        "strike_price_gte": str(round(spot * (1 - span), 2)),
        "strike_price_lte": str(round(spot * (1 + span), 2)),
        "limit": "500"})
    contracts = j.get("option_contracts") or []
    if not contracts:
        return []
    # nearest expiry only -- mixing expiries in one spread is a calendar, not a vertical
    contracts.sort(key=lambda c: c["expiration_date"])
    expiry = contracts[0]["expiration_date"]
    contracts = [c for c in contracts if c["expiration_date"] == expiry]

    out, syms = [], [c["symbol"] for c in contracts]
    snaps = {}
    for i in range(0, len(syms), 100):                       # endpoint caps at 100
        snaps.update(aio.req("GET", "%s/v1beta1/options/snapshots" % aio.DATA,
                             params={"symbols": ",".join(syms[i:i+100]),
                                     "feed": "indicative"}).get("snapshots") or {})
    dte = (datetime.strptime(expiry, "%Y-%m-%d").date() - today).days
    for c in contracts:
        sn = snaps.get(c["symbol"]) or {}
        q = sn.get("latestQuote") or {}
        bid, ask = float(q.get("bp") or 0), float(q.get("ap") or 0)
        if bid <= 0 or ask <= 0:
            continue
        g = sn.get("greeks") or {}
        delta, src = g.get("delta"), "alpaca"
        if delta is None:                                     # 0DTE: Alpaca gives none
            T = pricing.year_fraction(dte, _hours_left_et())
            iv = pricing.implied_vol((bid + ask) / 2, spot, float(c["strike_price"]),
                                     T, kind == "call")
            if iv:
                delta = pricing.greeks(spot, float(c["strike_price"]), T, iv,
                                       kind == "call")["delta"]
                src = "ours"
            else:
                src = "unavailable"
        out.append(Candidate(c["symbol"], float(c["strike_price"]), expiry, dte,
                             kind, bid, ask, delta, src))
    return out


def build_vertical(underlying: str = "SPY", min_dte: int = 0, max_dte: int = 2,
                   kind: str = "put", target_short_delta: float = 0.20,
                   width_strikes: float = 1.0, max_quoted_width: float = 0.20,
                   contracts: int = 1) -> dict:
    """Returns {'ok': bool, 'reason': str, 'proposal': Proposal, ...}."""
    spot = float(aio.req("GET", "%s/v2/stocks/%s/trades/latest" % (aio.DATA, underlying),
                         params={"feed": "iex"})["trade"]["p"])
    chain = fetch_chain(underlying, min_dte, max_dte, kind, spot)
    if not chain:
        return {"ok": False, "reason": "no quotable contracts in the window"}

    usable = [c for c in chain if c.delta is not None]
    if not usable:
        return {"ok": False, "reason": "no contract had a usable delta"}

    # short strike nearest the target delta, on the OTM side
    short = min(usable, key=lambda c: abs(abs(c.delta) - target_short_delta))
    # long strike is further OTM by the requested width
    want = short.strike - width_strikes if kind == "put" else short.strike + width_strikes
    side = [c for c in chain if (c.strike < short.strike if kind == "put"
                                 else c.strike > short.strike)]
    if not side:
        return {"ok": False, "reason": "no protective strike available beyond the short"}
    long = min(side, key=lambda c: abs(c.strike - want))

    # LIQUIDITY GATE -- measured, enforced before the proposal exists
    for leg, label in ((short, "short"), (long, "long")):
        if leg.width > max_quoted_width:
            return {"ok": False,
                    "reason": "liquidity gate: %s leg %s quoted $%.2f wide "
                              "(~$%.0f/contract), above the $%.2f limit"
                              % (label, leg.symbol, leg.width, leg.width * 100,
                                 max_quoted_width)}

    # conservative credit: sell the short at its BID, buy the long at its ASK
    credit = short.bid - long.ask
    width = abs(short.strike - long.strike)
    max_loss = max(0.0, width - credit)
    if credit <= 0:
        return {"ok": False, "reason": "structure is a net debit (%.2f); no credit to sell"
                % credit}

    proposal = Proposal(
        strategy="%s-credit-vertical" % kind,
        underlying=underlying,
        legs=[Leg(long.symbol, "buy", 1, True), Leg(short.symbol, "sell", 1)],
        limit_price=round(credit, 2),
        max_loss_per_contract=round(max_loss, 4),
        contracts=contracts,
        fair_value=round((short.mid - long.mid), 4),
        quoted_width=round(max(short.width, long.width), 4),
        dte=short.dte,
        fingerprint="%s|%s|%s" % (short.symbol, long.symbol, short.expiry),
    )
    return {"ok": True, "reason": "built", "proposal": proposal,
            "spot": spot, "expiry": short.expiry, "dte": short.dte,
            "short": short, "long": long,
            "credit": round(credit, 2), "width": width,
            "max_loss_per_contract": round(max_loss * 100, 2),
            "short_delta": round(short.delta, 4), "greeks_source": short.greeks_source}


if __name__ == "__main__":
    r = build_vertical()
    if not r["ok"]:
        print("NOT BUILT:", r["reason"]); sys.exit(0)
    p = r["proposal"]
    print("spot %.2f  expiry %s (dte %d)" % (r["spot"], r["expiry"], r["dte"]))
    print("  SELL %s  strike %.0f  delta %.3f  (%s greeks)  bid %.2f ask %.2f"
          % (r["short"].symbol, r["short"].strike, r["short_delta"],
             r["greeks_source"], r["short"].bid, r["short"].ask))
    print("  BUY  %s  strike %.0f                          bid %.2f ask %.2f"
          % (r["long"].symbol, r["long"].strike, r["long"].bid, r["long"].ask))
    print("  credit $%.2f  width $%.2f  max loss $%.0f/contract  worst quoted width $%.2f"
          % (r["credit"], r["width"], r["max_loss_per_contract"], p.quoted_width))
    be = r["max_loss_per_contract"] / (r["max_loss_per_contract"] + r["credit"] * 100)
    pop = 1 - abs(r["short_delta"])
    print("  break-even win rate %.1f%%   short-strike POP proxy %.1f%%   edge %+.1f pts"
          % (be * 100, pop * 100, (pop - be) * 100))
    print("  ^ the credit is calibrated to the probability. Strike choice sets the risk"
          "\n    shape, not the edge -- which is why the regime gate and execution cost"
          "\n    are where this strategy actually lives.")
