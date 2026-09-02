#!/usr/bin/env python3
"""Attribution has to survive the case that broke it: both tiers on one strike."""
import json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

fails = []
def ck(what, cond, detail=""):
    print("  %-62s %s" % (what, "ok" if cond else "FAIL " + detail))
    if not cond:
        fails.append(what)

tmp = tempfile.mkdtemp()
os.environ["MIDPOINT_ALLOWED_ACCOUNT"] = "PATEST00001"
home = os.path.expanduser("~/midpoint/docs")
jpath = os.path.join(home, "agent.PATEST00001.jsonl")

DAY = "2026-09-02"
with open(jpath, "w") as fh:
    fh.write(json.dumps({"ts": DAY + "T16:42:37+00:00", "event": "session_flatten_done",
        "fills": [{"symbol": "SPY260902P00762000", "side": "sell", "qty": "1",
                   "price": "0.22", "at": DAY + "T16:42:36Z"},
                  {"symbol": "SPY260902P00763000", "side": "buy", "qty": "1",
                   "price": "0.37", "at": DAY + "T16:42:37Z"}]}) + "\n")

import pnl_attribution as P
P.AGENT_JOURNAL = jpath
P.OUT = os.path.join(tmp, "out.json")

print("1. the supervisor's flatten counts as the agent's own exit")
exits = P.supervisor_exit_fills(DAY)
ck("both flatten legs are recognised", len(exits) == 2, str(exits))
ck("a leg is keyed on symbol, side and price",
   ("SPY260902P00762000", "sell", "0.22") in exits)
ck("the same contract on the other side is NOT the same fill",
   ("SPY260902P00762000", "buy", "0.22") not in exits)

print("\n2. REGRESSION: one strike, two tiers")
# the defect: the agent and a research probe both traded SPY 762. Matching on symbol
# put every fill in one bucket; matching on the order that caused it does not.
mine = {"order-agent-1"}
def is_agent(f):
    if f.get("order_id") in mine:
        return True
    return (f.get("symbol"), f.get("side"), str(f.get("price"))) in exits

agent_fill    = {"order_id": "order-agent-1", "symbol": "SPY260902P00762000",
                 "side": "sell", "price": "0.30"}
probe_fill    = {"order_id": "order-probe-9", "symbol": "SPY260902P00762000",
                 "side": "sell", "price": "0.31"}
flatten_fill  = {"order_id": "order-super-4", "symbol": "SPY260902P00762000",
                 "side": "sell", "price": "0.22"}
ck("the agent's own order is the agent's", is_agent(agent_fill))
ck("a probe on the SAME contract is not the agent's", not is_agent(probe_fill))
ck("the supervisor's exit is the agent's", is_agent(flatten_fill))

print("\n3. cash direction")
ck("selling brings cash in",
   P.cash([{"qty": "1", "price": "0.30", "side": "sell"}]) == 30.0)
ck("buying takes cash out",
   P.cash([{"qty": "1", "price": "0.30", "side": "buy"}]) == -30.0)
ck("a round trip nets the difference",
   round(P.cash([{"qty": "1", "price": "0.30", "side": "sell"},
                 {"qty": "1", "price": "0.22", "side": "buy"}]), 2) == 8.0)

print("\n4. the books are per-account")
os.environ["MIDPOINT_ALLOWED_ACCOUNT"] = "PAAAA"
ck("the journal path names the account", P._journal_path().endswith("agent.PAAAA.jsonl"))
ck("the output path names the account", P._out_path().endswith("pnl_attribution.PAAAA.json"))
os.environ.pop("MIDPOINT_ALLOWED_ACCOUNT")
ck("with no account pinned it falls back to the unscoped name",
   P._journal_path().endswith("agent.jsonl"))

os.remove(jpath)
print("\n%s" % ("ALL PASS" if not fails else "FAILURES: " + ", ".join(fails)))
sys.exit(1 if fails else 0)
