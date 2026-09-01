#!/usr/bin/env python3
"""
preflight.py -- refuse to start a session into a state we have not checked.

An unattended run has no one to notice that the credentials point at the wrong
account, or that yesterday's position is still open, or that a halt file was left
behind. Each of those turns a session into a mess that is expensive to unpick
afterwards, and every one of them is cheap to detect beforehand.

Exit code 0 means safe to run. Anything else means do not start.

    python3 tools/preflight.py             # check the lab account
    python3 tools/preflight.py --expect PA3TD7HMABNH
"""
import json, os, subprocess, sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import alpaca_io as aio

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(ROOT, "bin", "alpaca")
STATE = os.path.expanduser("~/midpoint/docs/agent_state.json")
HALT = os.path.expanduser("~/midpoint/HALT")
COMPETITION = "PA32CGA2U1DY"

FAIL, WARN = [], []


def check(name, ok, detail="", fatal=True):
    mark = "ok  " if ok else ("FAIL" if fatal else "warn")
    print("  [%s] %-46s %s" % (mark, name, detail))
    if not ok:
        (FAIL if fatal else WARN).append(name)
    return ok


def main(argv):
    expect = None
    for i, a in enumerate(argv):
        if a == "--expect" and i + 1 < len(argv):
            expect = argv[i + 1]

    print("=" * 74)
    print("  PRE-FLIGHT  %s" % datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    print("=" * 74)

    # 1. operator stop
    check("no halt file left behind", not os.path.exists(HALT),
          HALT if os.path.exists(HALT) else "")

    # 2. credentials and the write path
    have_keys = bool(os.environ.get("ALPACA_API_KEY") and os.environ.get("ALPACA_SECRET_KEY"))
    check("credentials in the environment", have_keys)
    check("CLI binary present", os.path.exists(CLI), CLI)

    acct = {}
    if have_keys and os.path.exists(CLI):
        try:
            p = subprocess.run([CLI, "account", "get"], capture_output=True,
                               text=True, timeout=25)
            acct = json.loads(p.stdout) if p.stdout.strip().startswith("{") else {}
        except Exception as e:
            print("      CLI error: %r" % e)
    check("CLI authenticates", bool(acct.get("account_number")),
          acct.get("account_number", ""))

    # 3. the right account, and never the one reserved for the contest
    num = acct.get("account_number", "")
    check("not the competition account", num != COMPETITION, num)
    if expect:
        check("account is the one expected", num == expect, "%s vs %s" % (num, expect))

    # 4. the account can actually do what the strategy needs
    lvl = acct.get("options_trading_level") or acct.get("options_approved_level")
    check("options level allows spreads", (lvl or 0) >= 2, "level %s" % lvl)
    check("account is active", acct.get("status") == "ACTIVE", str(acct.get("status")))
    check("not blocked", not (acct.get("trading_blocked") or acct.get("account_blocked")))
    try:
        eq = float(acct.get("equity") or 0)
        bp = float(acct.get("buying_power") or 0)
    except ValueError:
        eq = bp = 0.0
    check("equity is readable and positive", eq > 0, "$%.2f" % eq)
    check("buying power covers a defined-risk spread", bp > 1000, "$%.2f" % bp, fatal=False)

    # 5. the data path, separately from the write path
    clock = {}
    try:
        clock = aio.req("GET", "%s/v2/clock" % aio.TRADING)
    except Exception as e:
        print("      clock error: %r" % e)
    check("market clock reachable", bool(clock),
          ("open" if clock.get("is_open") else "closed, next open %s"
           % clock.get("next_open", "?")) if clock else "")

    # 6. start flat, or know why not
    pos, orders = [], []
    try:
        pos = aio.req("GET", "%s/v2/positions" % aio.TRADING)
        orders = aio.req("GET", "%s/v2/orders" % aio.TRADING, params={"status": "open"})
    except Exception as e:
        print("      position lookup error: %r" % e)
    check("no positions carried in", len(pos) == 0,
          "%d open" % len(pos), fatal=False)
    check("no resting orders carried in", len(orders) == 0,
          "%d open" % len(orders), fatal=False)

    # 7. local state agrees with the broker
    st = {}
    if os.path.exists(STATE):
        try:
            st = json.load(open(STATE))
        except Exception:
            pass
    recorded = st.get("open_structures", [])
    held = {p.get("symbol") for p in pos}
    orphan = [s for s in recorded if s.get("short") not in held]
    check("no structures recorded that the broker does not hold",
          not orphan, "%d orphaned" % len(orphan), fatal=False)

    # 8. the risk rules load and construct
    try:
        from risk_kernel import RiskKernel, RiskConfig
        cfg = RiskConfig(**json.load(open(os.path.join(ROOT, "config", "risk.json"))))
        RiskKernel(cfg)
        ok_cfg = True
        detail = ("per-trade %.2f%%, daily %.1f%%, drawdown %.1f%%, width $%.2f"
                  % (cfg.max_trade_loss_frac * 100, cfg.daily_loss_limit_frac * 100,
                     cfg.max_drawdown_frac * 100, cfg.max_quoted_width))
    except Exception as e:
        ok_cfg, detail = False, repr(e)
    check("risk config loads and the kernel builds", ok_cfg, detail)

    # 9. journals are writable, since a session that cannot record is not auditable
    writable = True
    for path in (STATE, os.path.expanduser("~/midpoint/docs/agent.jsonl"),
                 os.path.expanduser("~/midpoint/docs/risk_decisions.jsonl")):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a"):
                pass
        except Exception:
            writable = False
    check("journals are writable", writable)

    print("=" * 74)
    if FAIL:
        print("  NOT SAFE TO START -- %d blocking: %s" % (len(FAIL), ", ".join(FAIL)))
    else:
        print("  clear to start%s" % (" (%d warnings: %s)" % (len(WARN), ", ".join(WARN))
                                      if WARN else ""))
    print("=" * 74)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
