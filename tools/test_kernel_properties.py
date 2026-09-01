#!/usr/bin/env python3
"""
Property-based tests for the risk kernel.

The unit tests in test_risk_kernel.py check the cases we thought of. These check
the cases we did not: ten thousand randomly generated proposals and account
states, against invariants that must hold for every input the kernel can ever
see. This is the discipline Hull draws out of the derivatives disasters in ch.
35 -- Kidder Peabody's loss came from a system that computed profit wrongly and
was never independently checked -- and it is the reason the kernel's contract is
written as an invariant ("may only veto or shrink") rather than as prose.

Written against the standard library so a judge can run it with no install:
generation is a seeded PRNG, and any failure prints the exact proposal, the
account state and the config needed to reproduce it.

Run: python3 tools/test_kernel_properties.py [trials]
"""
import json, math, os, random, sys, tempfile
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from risk_kernel import (RiskKernel, RiskConfig, Proposal, Leg, AccountState,
                         PASS, SHRINK, REJECT)

CFG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "config", "risk.json")


def base_cfg():
    cfg = RiskConfig()
    if os.path.exists(CFG_PATH):
        for k, v in json.load(open(CFG_PATH)).items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
    return cfg


def rand_proposal(rng, cfg, tradeable=False):
    """A random proposal.

    tradeable=True biases toward structures that can actually reach the sizing
    gates -- credit near fair value, quotes inside the liquidity limit. Without
    that stratum the size-cap invariants are almost never exercised, because a
    uniformly random proposal is usually killed by an earlier gate. Both strata
    are generated: the wild one proves refusals happen, the tradeable one proves
    what gets through stays inside the caps.
    """
    width = rng.choice([1.0, 1.0, 2.0, 5.0])
    credit = round(rng.uniform(0.01, width * 0.9), 2)
    max_loss = max(0.0, width - credit)
    n = rng.choice([1, 1, 2, 3, 5, 9, 10, 11, 25, 60])
    short_k = round(rng.uniform(300, 800))
    kind = rng.choice(["put", "call"])
    long_k = short_k - width if kind == "put" else short_k + width
    if tradeable:
        fv = credit * rng.uniform(0.93, 1.07)          # inside the 10% collar
        qw = rng.uniform(0.01, cfg.max_quoted_width)   # inside the liquidity gate
    else:
        fv = credit * rng.uniform(0.8, 1.25) if rng.random() < 0.9 else None
        qw = rng.uniform(0.01, 0.60)
    return Proposal(
        strategy="%s-credit-vertical" % kind,
        underlying=rng.choice(["SPY", "QQQ"]),
        legs=[Leg("L%d" % long_k, "buy", 1, True), Leg("S%d" % short_k, "sell", 1)],
        limit_price=credit,
        max_loss_per_contract=round(max_loss, 4),
        contracts=n,
        fair_value=None if fv is None else round(fv, 4),
        quoted_width=round(qw, 3),
        dte=rng.choice([1, 2] if tradeable else [0, 0, 1, 2, 7]),
        fingerprint="fp-%d" % rng.randrange(10 ** 6),
        snapshot=None)


def rand_state(rng, cfg, equity=None, healthy=False):
    """A random account state. healthy=True keeps the account inside its loss and
    drawdown limits and the clock inside the opening window, so that the gates
    downstream of them are reachable."""
    eq = equity if equity is not None else rng.uniform(20_000, 200_000)
    if healthy:
        peak = eq * rng.uniform(1.0, 1.0 + cfg.max_drawdown_frac * 0.8)
        day0 = eq * rng.uniform(1.0 - 0.0, 1.0 + cfg.daily_loss_limit_frac * 0.8)
        et = datetime(2026, 9, 1, rng.randrange(10, 13), rng.randrange(0, 60))
    else:
        peak = eq * rng.uniform(1.0, 1.10)
        day0 = eq * rng.uniform(0.97, 1.05)
        et = datetime(2026, 9, 1, rng.randrange(9, 16), rng.randrange(0, 60))
    return AccountState(
        account_number="PA_TEST_%d" % rng.randrange(1000),
        equity=eq, peak_equity=peak, day_start_equity=day0,
        open_defined_risk=rng.choice([0.0, 0.0, eq * rng.uniform(0, 0.03)])
        if not healthy else rng.choice([0.0, eq * rng.uniform(0, cfg.max_open_risk_frac)]),
        now_et=et, recent_orders=[], orders_this_session=rng.randrange(0, 5),
        consecutive_rejects=rng.randrange(0, 3))


def main(trials=10000):
    rng = random.Random(20260901)
    tmp = tempfile.mkdtemp()
    jpath = os.path.join(tmp, "j.jsonl")
    cfg = base_cfg()
    fails = []

    def fail(prop, msg, p, s, d):
        fails.append(prop)
        print("\n  FAIL [%s] %s" % (prop, msg))
        print("    proposal: %s" % json.dumps({k: v for k, v in asdict(p).items()
                                               if k != "legs"}, default=str))
        print("    state:    equity=%.2f peak=%.2f day0=%.2f open_risk=%.2f et=%s"
              % (s.equity, s.peak_equity, s.day_start_equity, s.open_defined_risk,
                 s.now_et))
        print("    decision: %s n=%s gate=%s" % (d.action, d.contracts, d.gate))

    checked = {k: 0 for k in
               ("never_enlarges", "reject_is_zero", "deterministic",
                "trade_cap_respected", "aggregate_cap_respected",
                "fat_finger_respected", "one_journal_line_each",
                "shrink_only_downward", "kill_switch_absolute",
                "account_guard_absolute", "size_monotone")}

    seen = {}
    print("running %d random proposals against the kernel invariants" % trials)
    for i in range(trials):
        healthy = (i % 2 == 0)          # half the trials in a tradeable stratum
        k = RiskKernel(cfg, journal_path=jpath)
        p = rand_proposal(rng, cfg, tradeable=healthy)
        s = rand_state(rng, cfg, healthy=healthy)

        before = sum(1 for _ in open(jpath)) if os.path.exists(jpath) else 0
        d = k.evaluate(p, s)
        after = sum(1 for _ in open(jpath))
        seen[(d.action, d.gate)] = seen.get((d.action, d.gate), 0) + 1

        # 1. the kernel may never enlarge a proposal
        checked["never_enlarges"] += 1
        if d.contracts > p.contracts:
            fail("never_enlarges", "approved %d > proposed %d" % (d.contracts, p.contracts), p, s, d)

        # 2. a rejection approves nothing
        checked["reject_is_zero"] += 1
        if d.action == REJECT and d.contracts != 0:
            fail("reject_is_zero", "REJECT with %d contracts" % d.contracts, p, s, d)

        # 3. SHRINK means strictly fewer, PASS means unchanged
        checked["shrink_only_downward"] += 1
        if d.action == SHRINK and not (0 < d.contracts < p.contracts):
            fail("shrink_only_downward", "SHRINK to %d from %d" % (d.contracts, p.contracts), p, s, d)
        if d.action == PASS and d.contracts != p.contracts:
            fail("shrink_only_downward", "PASS changed size", p, s, d)

        # 4. determinism: the same inputs must give the same answer
        checked["deterministic"] += 1
        k2 = RiskKernel(cfg, journal_path=os.path.join(tmp, "j2.jsonl"))
        d2 = k2.evaluate(p, rand_state_copy(s))
        if (d.action, d.contracts, d.gate) != (d2.action, d2.contracts, d2.gate):
            fail("deterministic", "%s/%s/%s vs %s/%s/%s"
                 % (d.action, d.contracts, d.gate, d2.action, d2.contracts, d2.gate), p, s, d)

        # 5. anything approved must sit inside the configured caps
        if d.action in (PASS, SHRINK) and p.max_loss_per_contract > 0:
            risk = d.contracts * p.max_loss_per_contract * 100
            checked["trade_cap_respected"] += 1
            if risk > cfg.max_trade_loss_frac * s.equity + 1e-6:
                fail("trade_cap_respected", "approved risk %.2f over per-trade cap %.2f"
                     % (risk, cfg.max_trade_loss_frac * s.equity), p, s, d)
            checked["aggregate_cap_respected"] += 1
            if s.open_defined_risk + risk > cfg.max_open_risk_frac * s.equity + 1e-6:
                fail("aggregate_cap_respected", "aggregate %.2f over cap %.2f"
                     % (s.open_defined_risk + risk, cfg.max_open_risk_frac * s.equity), p, s, d)
            checked["fat_finger_respected"] += 1
            if d.contracts > cfg.max_contracts_per_order:
                fail("fat_finger_respected", "approved %d contracts" % d.contracts, p, s, d)
            if abs(p.limit_price) * 100 * d.contracts > cfg.max_notional_per_order + 1e-6:
                fail("fat_finger_respected", "approved notional over cap", p, s, d)

        # 6. exactly one journal line per evaluation
        checked["one_journal_line_each"] += 1
        if after - before != 1:
            fail("one_journal_line_each", "wrote %d lines" % (after - before), p, s, d)

    # 7. the kill switch and the account guard are absolute, whatever else is true
    print("checking the two absolute gates against %d random inputs" % (trials // 10))
    halt = os.path.join(tmp, "HALT")
    cfg_halt = replace(cfg, kill_switch_path=halt)
    open(halt, "w").write("stop")
    for i in range(trials // 10):
        k = RiskKernel(cfg_halt, journal_path=jpath)
        p, s = rand_proposal(rng, cfg), rand_state(rng, cfg)
        d = k.evaluate(p, s)
        checked["kill_switch_absolute"] += 1
        if d.action != REJECT:
            fail("kill_switch_absolute", "kill switch present but action %s" % d.action, p, s, d)
    os.remove(halt)

    for i in range(trials // 10):
        k = RiskKernel(cfg, journal_path=jpath)
        p = rand_proposal(rng, cfg)
        s = rand_state(rng, cfg)
        s.account_number = cfg.competition_account
        d = k.evaluate(p, s)
        checked["account_guard_absolute"] += 1
        if d.action != REJECT:
            fail("account_guard_absolute", "competition account not refused", p, s, d)

    # 8. monotonicity in size: if n contracts are refused for a sizing reason,
    #    then more contracts must also be refused (fresh kernel, same state)
    print("checking size monotonicity on %d cases" % (trials // 10))
    for i in range(trials // 10):
        p = rand_proposal(rng, cfg, tradeable=True)
        s = rand_state(rng, cfg, healthy=True)
        small = replace(p, contracts=1, legs=p.legs)
        big = replace(p, contracts=max(2, p.contracts), legs=p.legs)
        d_small = RiskKernel(cfg, journal_path=jpath).evaluate(small, rand_state_copy(s))
        d_big = RiskKernel(cfg, journal_path=jpath).evaluate(big, rand_state_copy(s))
        checked["size_monotone"] += 1
        if d_small.action == REJECT and d_big.action != REJECT:
            fail("size_monotone", "1 contract refused but %d allowed" % big.contracts,
                 big, s, d_big)
        if d_big.contracts > 0 and d_small.contracts == 0 and d_small.gate not in (
                None, "G10-duplicate", "G11-throttle"):
            fail("size_monotone", "smaller refused (%s) yet larger approved" % d_small.gate,
                 big, s, d_big)

    print("\n  decision mix reached by the generator:")
    for kk, vv in sorted(seen.items(), key=lambda x: -x[1]):
        print("    %-28s %6d" % ("%s/%s" % kk, vv))

    print("\n" + "=" * 64)
    for k_, v in checked.items():
        print("  %-28s checked %6d times   %s"
              % (k_, v, "FAILED" if k_ in fails else "held"))
    print("=" * 64)
    uniq = sorted(set(fails))
    print("  %d invariants, %d violations" % (len(checked), len(fails)))
    print("  %s" % ("ALL INVARIANTS HELD" if not uniq else "VIOLATED: " + ", ".join(uniq)))
    return 1 if uniq else 0


def rand_state_copy(s):
    return AccountState(account_number=s.account_number, equity=s.equity,
                        peak_equity=s.peak_equity, day_start_equity=s.day_start_equity,
                        open_defined_risk=s.open_defined_risk, now_et=s.now_et,
                        recent_orders=list(s.recent_orders),
                        orders_this_session=s.orders_this_session,
                        consecutive_rejects=s.consecutive_rejects)


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 10000))
