#!/usr/bin/env python3
"""Checks the assignment-risk arithmetic without touching the network."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent as A

fails = []


def ck(name, cond):
    print("  %-62s %s" % (name, "ok" if cond else "FAIL"))
    if not cond:
        fails.append(name)


def make(kind="put"):
    a = A.Agent.__new__(A.Agent)
    a.cfg = A.AgentConfig(kind=kind)
    return a


def patched(a, bid, ask, spot):
    """Swap the two network calls for fixtures."""
    import alpaca_io as aio
    def fake_req(method, url, params=None, body=None, **kw):
        if "snapshots" in url:
            sym = params["symbols"]
            return {"snapshots": {sym: {"latestQuote": {"bp": bid, "ap": ask}}}}
        return {"trade": {"p": spot}}
    A.aio.req, old = fake_req, A.aio.req
    return old


st = {"short": "SPY260901P00760000", "long": "SPY260901P00759000",
      "short_strike": 760.0, "long_strike": 759.0, "kind": "put"}

print("put spread, short strike 760")
a = make("put")
old = patched(a, 0.10, 0.12, 765.0)          # out of the money: all extrinsic
ck("OTM short is not flagged", a.assignment_risk(st) is None)

patched(a, 5.02, 5.06, 755.0)                # ITM by 5.00, mark 5.04 -> 0.04 extrinsic
r = a.assignment_risk(st)
ck("deep ITM with 0.04 extrinsic IS flagged", r is not None)
ck("  intrinsic computed as 5.00", r and abs(r["intrinsic"] - 5.0) < 1e-9)
ck("  extrinsic computed as 0.04", r and abs(r["extrinsic"] - 0.04) < 1e-9)

patched(a, 5.30, 5.40, 755.0)                # ITM by 5.00 but 0.35 extrinsic left
ck("ITM with 0.35 extrinsic is NOT flagged", a.assignment_risk(st) is None)

patched(a, 0.0, 0.0, 755.0)
ck("missing quote returns None rather than guessing", a.assignment_risk(st) is None)

print("\ncall spread, short strike 760")
b = make("call")
stc = dict(st, kind="call")
patched(b, 5.01, 5.05, 765.0)                # ITM call by 5.00, 0.03 extrinsic
r = b.assignment_risk(stc)
ck("ITM short call with 0.03 extrinsic IS flagged", r is not None)
patched(b, 0.20, 0.24, 755.0)
ck("OTM short call is not flagged", b.assignment_risk(stc) is None)

print("\nrecords written before this upgrade")
ck("record without a strike is skipped, not crashed",
   a.assignment_risk({"short": "X", "long": "Y"}) is None)

A.aio.req = old
print("\n%s" % ("ALL PASS" if not fails else "FAILURES: " + ", ".join(fails)))
sys.exit(1 if fails else 0)
