"""Exercises every gate, plus the kernel's core invariants. No network."""
import os, sys, tempfile, time
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from risk_kernel import (RiskKernel, RiskConfig, Proposal, Leg, AccountState,
                         PASS, SHRINK, REJECT)

J = tempfile.mktemp(suffix=".jsonl")
PASSED, FAILED = [], []

def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print("  %s  %-46s %s" % ("PASS" if cond else "FAIL", name, detail))

def kernel(**over):
    return RiskKernel(RiskConfig(**over), journal_path=J)

def good_legs():
    return [Leg("SPY_LONG", "buy", 1, True), Leg("SPY_SHORT", "sell", 1)]

def proposal(**over):
    d = dict(strategy="vertical", underlying="SPY", legs=good_legs(),
             limit_price=1.50, max_loss_per_contract=3.50, contracts=1,
             fair_value=1.50, quoted_width=0.05, dte=1, fingerprint="fp1")
    d.update(over); return Proposal(**d)

def state(**over):
    d = dict(account_number="PA3TD7HMABNH", equity=100_000.0, peak_equity=100_000.0,
             day_start_equity=100_000.0, open_defined_risk=0.0,
             now_et=datetime(2026, 9, 1, 11, 0), recent_orders=[],
             orders_this_session=0, consecutive_rejects=0)
    d.update(over); return AccountState(**d)

print("\n=== happy path ===")
d = kernel().evaluate(proposal(), state())
check("clean proposal passes", d.action == PASS and d.contracts == 1, d.action)

print("\n=== (c)(1)(i) capital thresholds ===")
d = kernel().evaluate(proposal(contracts=10), state())
check("G3 shrinks to the per-trade loss cap", d.action == SHRINK and d.contracts == 1,
      "-> %d contracts" % d.contracts)
d = kernel().evaluate(proposal(), state(equity=98_000))
check("G4 halts on the daily loss limit", d.gate == "G4-daily-loss", d.gate)
d = kernel().evaluate(proposal(), state(equity=96_000, day_start_equity=96_500,
                                        peak_equity=100_000))
check("G5 halts on drawdown from peak", d.gate == "G5-drawdown", d.gate)
d = kernel().evaluate(proposal(), state(open_defined_risk=1_950))
check("G6 caps aggregate open risk", d.gate == "G6-aggregate-risk" or d.action != PASS,
      "%s / %s" % (d.action, d.gate))

print("\n=== (c)(1)(ii) erroneous-order controls ===")
d = kernel().evaluate(proposal(contracts=500, max_loss_per_contract=0.01), state())
check("G7 fat finger caps contracts", d.contracts <= 25, "-> %d" % d.contracts)
d = kernel().evaluate(proposal(limit_price=5.00, fair_value=1.50,
                               max_loss_per_contract=0.10), state())
check("G8 price collar rejects an off-market limit", d.gate == "G8-price-collar", d.gate)
d = kernel().evaluate(proposal(quoted_width=3.00), state())
check("G9 liquidity gate rejects a wide quote", d.gate == "G9-liquidity", d.gate)
d = kernel().evaluate(proposal(quoted_width=None), state())
check("G9 rejects when no quote exists", d.gate == "G9-liquidity", d.gate)
d = kernel().evaluate(proposal(), state(recent_orders=[(time.time(), "fp1")]))
check("G10 detects a duplicate structure", d.gate == "G10-duplicate", d.gate)
d = kernel().evaluate(proposal(fingerprint="other"),
                      state(recent_orders=[(time.time()-1, "x")]*20))
check("G11 throttles on message rate", d.gate == "G11-throttle", d.gate)

print("\n=== (c)(1)(iii) compliance and platform rules ===")
d = kernel().evaluate(proposal(legs=[Leg("S", "sell", 1)]), state())
check("G2 blocks an uncovered short", d.gate == "G2-defined-risk", d.gate)
d = kernel().evaluate(proposal(legs=[Leg("S","sell",1), Leg("L","buy",1,True)]), state())
check("G2 requires the long leg sequenced first", d.gate == "G2-defined-risk", d.gate)
d = kernel().evaluate(proposal(max_loss_per_contract=0), state())
check("G2 blocks when max loss is not computable", d.gate == "G2-defined-risk", d.gate)
d = kernel().evaluate(proposal(dte=0), state(now_et=datetime(2026,9,1,14,30)))
check("G12 blocks new 0DTE after 14:00 ET", d.gate == "G12-clock", d.gate)
d = kernel().evaluate(proposal(), state(now_et=datetime(2026,9,1,15,55)))
check("G12 blocks past the hard close", d.gate == "G12-clock", d.gate)
d = kernel().evaluate(proposal(), state(account_number="PA32CGA2U1DY"))
check("G0 refuses the competition account", d.gate == "G0-account", d.gate)

print("\n=== operational / Knight lessons ===")
halt = os.path.expanduser("~/midpoint/HALT_TEST")
open(halt, "w").close()
d = kernel(kill_switch_path=halt).evaluate(proposal(), state())
check("G1 kill switch halts everything", d.gate == "G1-kill-switch", d.gate)
os.unlink(halt)
d = kernel().evaluate(proposal(), state(consecutive_rejects=30))
check("G13 escalates after repeated refusals", d.gate == "G13-escalation", d.gate)

print("\n=== invariants ===")
d = kernel().evaluate(proposal(contracts=1), state())
check("kernel never enlarges a proposal", d.contracts <= 1, "-> %d" % d.contracts)
d = kernel().evaluate(proposal(contracts=3, max_loss_per_contract=3.50), state())
check("shrink is monotone downward", d.contracts <= 3, "-> %d" % d.contracts)
d = kernel().evaluate(proposal(contracts=1, max_loss_per_contract=600.0), state())
check("rejects when even one contract breaches the cap",
      d.action == REJECT, "%s / %s" % (d.action, d.gate))
n = sum(1 for _ in open(J))
check("every decision is journalled", n >= 20, "%d records" % n)

print("\n" + "="*64)
if FAILED:
    print("  %d passed, %d FAILED" % (len(PASSED), len(FAILED)))
    print("  FAILING:", FAILED); sys.exit(1)
print("="*64)
print("  %d passed, 0 failed" % len(PASSED))
