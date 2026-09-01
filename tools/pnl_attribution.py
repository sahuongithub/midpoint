#!/usr/bin/env python3
"""
pnl_attribution.py -- split the account's profit and loss by what caused it.

One paper account has carried two very different activities: an agent trading a
strategy, and research probes that deliberately buy and sell to measure how the
venue behaves. The broker adds them together. Reporting that sum as trading
performance would be the exact blending our own reporting rules forbid -- GIPS
holds that theoretical results must be labelled and never linked with actual
performance, and a research probe that happens to end up ahead is not a trading
result by any reading.

So this attributes every fill to the thing that caused it, using the agent's own
journal as the record of which contracts it traded. Anything else is research.

    python3 tools/pnl_attribution.py
"""
import json, os, sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import alpaca_io as aio

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENT_JOURNAL = os.path.expanduser("~/midpoint/docs/agent.jsonl")
OUT = os.path.join(HERE, "results", "pnl_attribution.json")


def et_today():
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def agent_symbols():
    """Every contract the agent itself recorded opening or closing."""
    syms = set()
    if not os.path.exists(AGENT_JOURNAL):
        return syms
    for line in open(AGENT_JOURNAL):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("dry_run") is not False:
            continue                       # rehearsals never touched the account
        for k in ("short", "long"):
            if r.get(k):
                syms.add(r[k])
        for st in (r.get("open_structures") or []):
            for k in ("short", "long"):
                if st.get(k):
                    syms.add(st[k])
    return syms


def cash(fills):
    """Net cash across fills: selling brings money in, buying takes it out."""
    total = 0.0
    for f in fills:
        q, px = float(f["qty"]), float(f["price"])
        total += q * px * 100 * (1 if f["side"].startswith("sell") else -1)
    return total


def main(argv):
    day = et_today()
    for i, a in enumerate(argv):
        if a == "--date" and i + 1 < len(argv):
            day = argv[i + 1]

    acts = aio.req("GET", "%s/v2/account/activities" % aio.TRADING,
                   params={"page_size": "100"})
    fills = [a for a in acts if a.get("activity_type") == "FILL"
             and (a.get("transaction_time") or "").startswith(day)]
    if not fills:
        print("no fills on %s" % day)
        return 0

    mine = agent_symbols()
    by_agent = [f for f in fills if f.get("symbol") in mine]
    by_research = [f for f in fills if f.get("symbol") not in mine]

    acct = aio.account()
    eq = float(acct["equity"])
    le = float(acct.get("last_equity") or eq)

    print("=" * 72)
    print("  WHAT THE ACCOUNT'S PROFIT AND LOSS IS ACTUALLY MADE OF   %s" % day)
    print("=" * 72)
    print("  account %s   equity $%.2f" % (acct["account_number"], eq))
    print()
    print("  %-34s %8s %14s" % ("", "fills", "net cash"))
    print("  %-34s %8d %+14.2f" % ("the agent trading its strategy", len(by_agent),
                                   cash(by_agent)))
    print("  %-34s %8d %+14.2f" % ("research probes measuring the venue",
                                   len(by_research), cash(by_research)))
    print("  %-34s %8d %+14.2f" % ("what the broker screen shows", len(fills),
                                   cash(fills)))
    print()
    print("  broker's own day figure: $%+.2f" % (eq - le))

    if by_research:
        syms = sorted({f["symbol"] for f in by_research})
        print()
        print("  research contracts excluded from trading performance:")
        for s in syms:
            sub = [f for f in by_research if f["symbol"] == s]
            print("    %-26s %2d fills  $%+.2f" % (s, len(sub), cash(sub)))
        print()
        print("  These were bought and sold deliberately to measure the venue, not to")
        print("  express a view. They belong in the research tier and are never")
        print("  reported as trading performance, whichever way they happen to land.")

    out = {"date": day, "account": acct["account_number"], "equity": eq,
           "broker_day_pnl": round(eq - le, 2),
           "agent": {"fills": len(by_agent), "net_cash": round(cash(by_agent), 2)},
           "research": {"fills": len(by_research), "net_cash": round(cash(by_research), 2),
                        "contracts": sorted({f["symbol"] for f in by_research})},
           "note": ("agent contracts are those the agent journalled; everything else on "
                    "the account is a research probe and is reported separately")}
    json.dump(out, open(OUT, "w"), indent=1)
    print("\n  wrote %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
