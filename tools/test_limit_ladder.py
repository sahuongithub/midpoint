#!/usr/bin/env python3
"""The ladder trades real money on a real account. Test it before it does."""
import json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

fails = []
def ck(what, cond, detail=""):
    print("  %-64s %s" % (what, "ok" if cond else "FAIL " + detail))
    if not cond:
        fails.append(what)

import limit_ladder as L
import limit_ladder_analyze as A

print("1. the rung arithmetic")
class C:
    def __init__(s, b, a): s.bid, s.ask = b, a
    @property
    def mid(s): return (s.bid + s.ask) / 2
short, long = C(0.40, 0.46), C(0.26, 0.32)
agg = short.bid - long.ask          # 0.40 - 0.32 = 0.08
mid = short.mid - long.mid          # 0.43 - 0.29 = 0.14
pas = short.ask - long.bid          # 0.46 - 0.26 = 0.20
def limit(t): return round(agg + t * (pas - agg), 2)
ck("theta 0 is the agent's own price, both legs crossed", limit(0.0) == 0.08, str(limit(0.0)))
ck("theta 0.5 is the mid on both legs", limit(0.5) == round(mid, 2), "%s vs %s" % (limit(0.5), mid))
ck("theta 1 is fully passive", limit(1.0) == 0.20, str(limit(1.0)))
ck("the ladder is monotone in theta",
   [limit(t) for t in L.RUNGS] == sorted(limit(t) for t in L.RUNGS))
ck("a higher theta always asks for MORE credit", limit(1.0) > limit(0.0))

print("\n2. the account guard is not optional")
import alpaca_io as aio
src = open(os.path.join(os.path.dirname(L.__file__), "limit_ladder.py")).read()
ck("main() calls guard_not_competition before anything trades",
   "guard_not_competition()" in src)
ck("the guard runs before the structure is built",
   src.index("guard_not_competition()") < src.index("S.build_vertical"))
ck("cleanup runs in a finally block", "finally:" in src and "cleanup(" in src)
ck("only ever one contract", "submit_vertical(spread, 1," in src)

print("\n2b. REGRESSION: a probe must not flatten an account it shares")
# the size ladder made exactly this mistake once: an account-wide flatten_all() in a
# research probe closes whatever the agent is holding.
ck("no account-wide flatten_all call survives in the probe",
   "aio.flatten_all(" not in src)
ck("it closes by symbol instead", "flatten_symbol(" in src)
ck("it cancels only ids it minted", "client_order_id\") in MINE" in src)
ck("it registers each id it submits", "MINE.add(coid)" in src)
ck("it refuses to start while an agent is live", "agent_running()" in src and "--force" in src)
ck("it excludes strikes the account already holds", "exclude_symbols=held" in src)

print("\n3. Wilson interval, because six attempts is not a percentage")
p, lo, hi = A.wilson(3, 6)
ck("a 3-of-6 rate centres on a half", abs(p - 0.5) < 1e-9)
ck("its interval is wide, as it should be", hi - lo > 0.5, "width %.2f" % (hi - lo))
p, lo, hi = A.wilson(0, 6)
ck("zero fills does not claim certainty of never", hi > 0.2, "hi %.2f" % hi)
p, lo, hi = A.wilson(60, 60)
ck("sixty of sixty is a narrow interval", lo > 0.9, "lo %.2f" % lo)
ck("an empty rung returns zeros rather than dividing by zero", A.wilson(0, 0) == (0.0, 0.0, 0.0))

print("\n4. the analyser ignores what is not an attempt")
tmp = tempfile.mkdtemp()
A.SRC = os.path.join(tmp, "l.jsonl")
A.OUT = os.path.join(tmp, "s.json")
with open(A.SRC, "w") as fh:
    for r in [{"theta": 0.5, "filled": True, "fill_minus_fair": -0.01,
               "limit_minus_fair": 0.0},
              {"theta": 0.5, "filled": False, "limit_minus_fair": 0.0},
              {"theta": 0.5, "filled": None, "error": "boom", "limit_minus_fair": 0.0},
              {"theta": 0.0, "filled": True, "fill_minus_fair": -0.03,
               "limit_minus_fair": -0.03}]:
        fh.write(json.dumps(r) + "\n")
A.main([])
s = json.load(open(A.OUT))
ck("the errored rung is not counted as an attempt", s["attempts"] == 3, str(s["attempts"]))
ck("the mid rung counts two attempts, not three",
   s["by_theta"]["0.50"]["attempts"] == 2, str(s["by_theta"]["0.50"]["attempts"]))

print("\n%s" % ("ALL PASS" if not fails else "FAILURES: " + ", ".join(fails)))
sys.exit(1 if fails else 0)
