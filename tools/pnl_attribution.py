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

So this attributes every fill to the thing that caused it.

The key is the ORDER, not the contract. An earlier version matched on symbol, using
the agent's journal as the list of contracts it had touched -- and that cannot work,
because both tiers trade the same strikes. On 2 September the agent and the venue
probes both traded the SPY 762/763 puts, and a symbol match had no way to tell them
apart. Every order the agent sends carries an mp- client id, so the order is what
identifies the cause; the supervisor's flatten is recognised from its own journal
record, and belongs to the agent because it closes an agent position.

    python3 tools/pnl_attribution.py
"""
import json, os, sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import alpaca_io as aio

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def _journal_path():
    """The books are per-account, and so is this. Reading the unscoped journal while
    attributing the judged account reported the agent as having traded nothing."""
    acct = os.environ.get("MIDPOINT_ALLOWED_ACCOUNT")
    stem = "agent.%s" % acct if acct else "agent"
    return os.path.expanduser("~/midpoint/docs/%s.jsonl" % stem)


AGENT_JOURNAL = _journal_path()
def _out_path():
    """Per-account, like the books. A single shared file meant whichever account ran
    last silently overwrote the other's attribution."""
    acct = os.environ.get("MIDPOINT_ALLOWED_ACCOUNT")
    stem = "pnl_attribution.%s" % acct if acct else "pnl_attribution"
    return os.path.join(HERE, "results", "%s.json" % stem)


OUT = _out_path()


def et_today():
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date().isoformat()


def journal_records(day):
    for line in open(AGENT_JOURNAL) if os.path.exists(AGENT_JOURNAL) else []:
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("dry_run") is True:
            continue                       # rehearsals never touched the account
        yield r


def supervisor_exit_fills(day):
    """The fills run_session.sh made flattening the account on the way out.

    They close agent positions, so they are agent trades -- but they are sent as bare
    single legs after the agent is gone, with no mp- id to recognise them by. The
    journal record the flatten writes is what identifies them."""
    keys = set()
    for r in journal_records(day):
        if r.get("event") != "session_flatten_done":
            continue
        for f in (r.get("fills") or []):
            at = (f.get("at") or "")
            if at.startswith(day) or not at:
                keys.add((f.get("symbol"), f.get("side"), str(f.get("price"))))
    return keys


def agent_order_ids(day):
    """Order ids whose client id the agent minted. Everything it sends is mp-."""
    ids = set()
    try:
        orders = aio.req("GET", "%s/v2/orders" % aio.TRADING,
                         params={"status": "all", "after": day + "T00:00:00Z",
                                 "limit": "500", "nested": "true"})
    except Exception:
        return ids

    def walk(o, mine):
        mine = mine or (o.get("client_order_id") or "").startswith("mp-")
        if mine:
            ids.add(o.get("id"))
        for leg in (o.get("legs") or []):
            walk(leg, mine)            # a leg inherits the parent's authorship

    for o in orders:
        walk(o, False)
    return ids


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

    mine = agent_order_ids(day)
    exits = supervisor_exit_fills(day)

    def is_agent(f):
        if f.get("order_id") in mine:
            return True
        return (f.get("symbol"), f.get("side"), str(f.get("price"))) in exits

    by_agent = [f for f in fills if is_agent(f)]
    by_research = [f for f in fills if not is_agent(f)]

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
           "note": ("agent fills are those whose order carries the agent's mp- client id, "
                    "plus the supervisor's flatten fills, which close agent positions; "
                    "everything else on the account is a research probe and is reported "
                    "separately")}
    json.dump(out, open(OUT, "w"), indent=1)
    print("\n  wrote %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
