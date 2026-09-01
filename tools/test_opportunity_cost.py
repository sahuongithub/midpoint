#!/usr/bin/env python3
"""
Checks the counterfactual arithmetic in opportunity_cost.py against hand
calculations, using a synthetic journal and a pre-seeded settlement cache so the
test never touches the network. Run: python3 tools/test_opportunity_cost.py
"""
import json, os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import opportunity_cost as OC

fails = []


def ck(name, got, want, tol=1e-9):
    ok = abs(got - want) <= tol
    print("  %-58s %+10.4f vs %+10.4f  %s" % (name, got, want, "ok" if ok else "FAIL"))
    if not ok:
        fails.append(name)


print("1. put credit spread, short 760 / long 759, credit 0.30, width 1.00")
f = OC.vertical_pnl_per_share
ck("settles at 765 (above short: worthless)", f("put", 760, 759, 0.30, 765), 0.30)
ck("settles at 760 (exactly at short)", f("put", 760, 759, 0.30, 760), 0.30)
ck("settles at 759.70 (inside the spread)", f("put", 760, 759, 0.30, 759.70), 0.0)
ck("settles at 759.50 (inside, half breached)", f("put", 760, 759, 0.30, 759.5), -0.20)
ck("settles at 759 (at long: max loss)", f("put", 760, 759, 0.30, 759), -0.70)
ck("settles at 700 (far below: still max loss)", f("put", 760, 759, 0.30, 700), -0.70)
ck("breakeven is short - credit = 759.70", f("put", 760, 759, 0.30, 759.70), 0.0)

print("\n2. call credit spread, short 770 / long 771, credit 0.25, width 1.00")
ck("settles at 765 (below short: worthless)", f("call", 770, 771, 0.25, 765), 0.25)
ck("settles at 770.25 (breakeven)", f("call", 770, 771, 0.25, 770.25), 0.0)
ck("settles at 771 (at long: max loss)", f("call", 770, 771, 0.25, 771), -0.75)
ck("settles at 900 (far above: max loss)", f("call", 770, 771, 0.25, 900), -0.75)

print("\n3. max loss floor is never breached (defined risk holds)")
worst = min(f("put", 760, 755, 1.20, s) for s in [x / 4.0 for x in range(2800, 3080)])
ck("5-wide put spread, credit 1.20, floor", worst, -(5.0 - 1.20))

print("\n4. end-to-end through the journal reader")
tmp = tempfile.mkdtemp()
jpath = os.path.join(tmp, "risk_decisions.jsonl")


def rec(gate, kind, ks, kl, credit, n, expiry="2026-09-04", und="SPY", spot=765.0):
    return {"ts": "2026-09-01T14:00:00+00:00", "decision": "REJECT", "gate": gate,
            "underlying": und, "proposed_contracts": n,
            "reasons": [{"gate": gate, "reason": "test", "action": "REJECT"}],
            "snapshot": {"underlying": und, "kind": kind, "expiry": expiry,
                         "spot": spot, "credit_per_share": credit,
                         "strike_width": abs(ks - kl), "contracts_proposed": n,
                         "short": {"symbol": "X", "strike": ks, "expiry": expiry},
                         "long": {"symbol": "Y", "strike": kl, "expiry": expiry}}}


with open(jpath, "w") as fh:
    # a refusal that saved us: settles deep through both strikes -> max loss avoided
    fh.write(json.dumps(rec("G9-liquidity", "put", 760, 759, 0.30, 2)) + "\n")
    # a refusal that cost us: settles above the short -> the credit was free money
    fh.write(json.dumps(rec("G10-duplicate", "put", 740, 739, 0.20, 1)) + "\n")
    # a PASS, which must not be counted as a refusal
    p = rec("none", "put", 750, 749, 0.25, 1); p["decision"] = "PASS"
    fh.write(json.dumps(p) + "\n")

cache_path = os.path.join(tmp, "settlement_cache.json")
OC.CACHE = cache_path
OC.OUT = os.path.join(tmp, "opportunity_cost.json")
OC.JOURNAL = jpath
json.dump({"SPY|2026-09-04": 745.00}, open(cache_path, "w"))   # settles at 745

rc = OC.main(["--offline", "--journal", jpath])
out = json.load(open(OC.OUT))
print()
ck("exit code", rc, 0)
ck("evaluations counted", out["evaluations"], 3)
ck("refusals counted", out["refusals"], 2)
ck("settled", out["settled"], 2)
# hand calculation:
#  A: put 760/759 credit .30, S_T=745 -> max loss -(1.00-.30) = -0.70/share * 100 * 2 = -140
#  B: put 740/739 credit .20, S_T=745 -> above short, worthless -> +0.20 * 100 * 1 = +20
rows = {r["gate"]: r for r in out["rows"]}
ck("A avoided P&L (would have lost)", rows["G9-liquidity"]["avoided_pnl_usd"], -140.0, 1e-6)
ck("B avoided P&L (would have won)", rows["G10-duplicate"]["avoided_pnl_usd"], 20.0, 1e-6)
ck("net effect of refusing = +140 - 20", out["net_effect_of_refusing_usd"], 120.0, 1e-6)
ck("saved", out["saved_usd"], 140.0, 1e-6)
ck("cost", out["cost_usd"], 20.0, 1e-6)
ck("A flagged as not worthless", 0.0 if rows["G9-liquidity"]["expired_worthless"] else 1.0, 1.0)
ck("B flagged as worthless", 1.0 if rows["G10-duplicate"]["expired_worthless"] else 0.0, 1.0)

print("\n5. sign convention: 'net effect of refusing' is positive when gates helped")
print("   avoided_pnl_usd is the trade's P&L had it run; refusing earns its negative.")
ck("A: trade would lose 140, so refusing earns +140",
   -rows["G9-liquidity"]["avoided_pnl_usd"], 140.0, 1e-6)

print("\n%s" % ("ALL PASS" if not fails else "FAILURES: " + ", ".join(fails)))
sys.exit(1 if fails else 0)
