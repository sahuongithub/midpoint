#!/usr/bin/env python3
"""
Tests the agent's cycle sequencing with a fake broker, so the ordering rules can
be checked without a market, an account or a network.

Sequencing is where an autonomous loop actually fails. The strategy can be sound
and the risk gates perfect, and the thing still misbehaves because a check ran in
the wrong order at 3pm. Three of the cases below are regressions: each is a bug
this file was written to catch after finding it by reading.

Run: python3 tools/test_agent_cycle.py
"""
import json, os, sys, tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent as A

fails = []


def ck(name, cond, detail=""):
    print("  %-60s %s%s" % (name, "ok" if cond else "FAIL",
                            ("  [%s]" % detail) if (detail and not cond) else ""))
    if not cond:
        fails.append(name)


class FakeExec:
    """Stands in for the executor: records what it was told to do."""
    def __init__(self, positions=(), equity=100_000.0):
        self.positions = list(positions)
        self.equity = equity
        self.flattened = 0
        self.closed = []

    def reconcile(self):
        return {"equity": self.equity, "positions": list(self.positions),
                "open_orders": [], "account": {"account_number": "PA_TEST"}}

    def flatten_all(self):
        self.flattened += 1
        self.positions = []
        return {"clean": True}

    def close_vertical(self, spread, n, debit, seq=None):
        self.closed.append((spread.short_symbol, n, debit))
        return {"client_order_id": "close-%s" % seq}

    def _run(self, args):
        return {"account_number": "PA_TEST"}


class Regime:
    def __init__(self, ok, ratio=0.85):
        self.short_premium_ok, self.ratio = ok, ratio

    def explain(self):
        return "backwardation" if not self.short_premium_ok else "contango"


def build_agent(tmp, positions=(), structures=(), et_hour=11, et_min=0,
                open_market=True, regime_ok=True, close_price=None):
    cfg = A.AgentConfig(max_concurrent=3)
    a = A.Agent.__new__(A.Agent)
    a.cfg, a.dry_run = cfg, True
    a.kernel = A.RiskKernel(A.RiskConfig())
    a.exec = FakeExec(positions=positions)
    a.state_path = os.path.join(tmp, "state.json")
    a.s = {"strategy_start_equity": 100_000.0, "peak_equity": 100_000.0,
           "day_start_equity": 100_000.0,
           "session_date": (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat(),
           "orders_this_session": 0, "consecutive_rejects": 0, "recent_orders": [],
           "open_structures": [dict(x) for x in structures], "seq": 0,
           "account_number": "PA_TEST"}

    A.aio.req = lambda m, u, **k: ({"is_open": open_market, "next_open": "later"}
                                   if "clock" in u else {})
    A.sig.read_regime = lambda: Regime(regime_ok)
    A._et_now = lambda: datetime(2026, 9, 1, et_hour, et_min)
    a._price_to_close = lambda st: close_price
    a.assignment_risk = lambda st: None
    a._journal = lambda ev, **kw: a.events.append((ev, kw))
    a.events = []
    # structure building needs a market; stub it so the cycle ends after the checks
    # this file is about. Its own behaviour is covered by test_opportunity_cost.
    A.build_vertical = lambda **kw: {"ok": False, "reason": "stubbed in test",
                                     "gate": None}
    return a


tmp = tempfile.mkdtemp()
S1 = {"short": "SPY260901P00760000", "long": "SPY260901P00759000", "width": 1.0,
      "credit": 0.20, "contracts": 1, "max_loss": 80.0, "short_strike": 760.0,
      "long_strike": 759.0, "kind": "put", "expiry": "2026-09-01"}
S2 = dict(S1, short="SPY260901P00755000", long="SPY260901P00754000")
LEGS = [{"symbol": S1["short"]}, {"symbol": S1["long"]}]

print("1. the exchange being shut outranks everything")
a = build_agent(tmp, open_market=False, et_hour=23)
ck("returns market_closed and does not flatten", a.run_cycle() == "market_closed" and a.exec.flattened == 0)

print("\n2. the flatten cutoff outranks an open profit  (stated doctrine)")
a = build_agent(tmp, positions=LEGS, structures=[S1], et_hour=15, et_min=20)
r = a.run_cycle()
ck("flattens after the cutoff", r == "flattened" and a.exec.flattened == 1)
ck("clears its structure list rather than reporting them missing later",
   a.s["open_structures"] == [], str(a.s["open_structures"]))

print("\n3. REGRESSION: standing down must still manage what is open")
# regime is bad AND the open spread has reached its profit target
a = build_agent(tmp, positions=LEGS, structures=[S1], regime_ok=False,
                close_price=0.10)          # credit 0.20, debit 0.10 -> 50% captured
r = a.run_cycle()
ck("still returns stand_down", r == "stand_down", r)
ck("but closed the position that hit its target first",
   len(a.exec.closed) == 1, str(a.exec.closed))
ck("journalled the close before the stand-down",
   [e for e, _ in a.events].index("closed") < [e for e, _ in a.events].index("stand_down"),
   str([e for e, _ in a.events]))

print("\n4. REGRESSION: capacity counts structures, not legs")
# two open verticals = four option positions, with a limit of three CONCURRENT SPREADS
a = build_agent(tmp, positions=LEGS * 2, structures=[S1, S2], close_price=0.19)
r = a.run_cycle()
ck("two spreads (four legs) is not 'at capacity' when the limit is three",
   r != "at_capacity", r)
a = build_agent(tmp, positions=LEGS * 3, structures=[S1, S2, dict(S1, short="X", long="Y")],
                close_price=0.19)
ck("three spreads IS at capacity", a.run_cycle() == "at_capacity")

print("\n4b. the kill switch stops opening but never traps an open position")
import os as _os
halt = _os.path.join(tmp, "HALT")
a = build_agent(tmp, positions=LEGS, structures=[S1], close_price=0.05)  # 75% captured
a.kernel.cfg.kill_switch_path = halt
open(halt, "w").write("stop")
r = a.run_cycle()
ck("returns halted", r == "halted", r)
ck("but still closed the position that hit its target",
   len(a.exec.closed) == 1, str(a.exec.closed))
ck("journalled the halt", any(e == "halted" for e, _ in a.events))
a2 = build_agent(tmp, structures=[], close_price=None)
a2.kernel.cfg.kill_switch_path = halt
ck("with nothing open it simply halts", a2.run_cycle() == "halted")
_os.remove(halt)

print("\n5. a position short of its target is held, not closed")
a = build_agent(tmp, positions=LEGS, structures=[S1], close_price=0.15)   # 25% captured
a.run_cycle()
ck("nothing closed at 25% of the credit", a.exec.closed == [])
ck("journalled that it is holding", any(e == "holding" for e, _ in a.events))

print("\n6. outside the opening window it manages but does not open")
a = build_agent(tmp, positions=LEGS, structures=[S1], et_hour=14, et_min=30,
                close_price=0.05)          # 75% captured -> should close
r = a.run_cycle()
ck("returns outside_window", r == "outside_window", r)
ck("still closed the position that hit its target", len(a.exec.closed) == 1)

print("\n%s" % ("ALL PASS" if not fails else "FAILURES: " + ", ".join(fails)))
sys.exit(1 if fails else 0)
