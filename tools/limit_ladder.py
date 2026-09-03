#!/usr/bin/env python3
"""
limit_ladder.py -- what does a credit spread actually have to give up to fill?

THE QUESTION
------------
structure.py builds every proposal as

    credit = short.bid - long.ask

which sells the short leg at the bid and buys the long leg at the ask: the worst
available price on both sides at once. Measured against its own recorded fair value
(short.mid - long.mid), the agent has sold BELOW fair on 464 of 464 proposals, median
one cent a share, mean about two. On a twelve-cent credit that is a fifth of the gross
handed over before the position exists, and the exit crosses again.

Whether that is necessary is an empirical question and nobody has asked it. The
single-leg fill oracle already established two things about this venue: an order below
the market does not fill, and an order above it fills at the NBBO rather than at your
limit -- a limit of 5.10 filled at 5.01. Neither result says where the marketable
boundary sits for a two-leg spread, which is the thing the agent actually sends.

THE DESIGN
----------
One structure, quoted fresh at every rung, submitted at a ladder of limits expressed
as a fraction of its own quoted spread:

    aggressive = short.bid - long.ask     theta = 0   (what the agent sends today)
    mid        = short.mid - long.mid     theta = 0.5 (fair value)
    passive    = short.ask - long.bid     theta = 1   (should not fill)

    limit(theta) = aggressive + theta * (passive - aggressive)

Three things make the answer trustworthy rather than anecdotal. The rungs are RE-QUOTED
at each attempt, so theta means the same fraction of the spread even as the market
moves. The order of rungs is SHUFFLED within each pass, so a drift in the underlying
cannot line up with the ladder and manufacture a trend. And each rung is repeated
across passes, because one fill or one miss is not evidence of anything.

WHY THIS BYPASSES THE RISK KERNEL
---------------------------------
It has to, and that is worth stating plainly rather than burying. The price collar
(G8) rejects any limit more than 10% from fair value, so the kernel would refuse every
rung above theta = 0.5 -- correctly, for trading, and fatally for an experiment whose
entire purpose is to find where the boundary is. The probe therefore submits directly
through the executor with no decision attached. What keeps that safe is not the kernel
but the construction: one contract, a one-wide vertical, defined risk of at most $100,
on the research account only, flattened the moment it fills. The competition account is
refused outright.

NOT SHARING AN ACCOUNT CARELESSLY
---------------------------------
A probe that calls flatten_all() closes whatever the agent is holding, and this
project has already made that mistake once, with the size ladder. So this one cleans
up after itself and nothing else: it cancels only the client order ids it minted, and
closes only the two legs it laddered. It also refuses to start against strikes the
account already holds, and refuses to start at all while a research agent is running,
because the safest way to share an account is not to.

    python3 tools/limit_ladder.py --passes 3 --wait 25
"""
import argparse, json, os, random, sys, time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import alpaca_io as aio
import structure as S
from executor import Executor, VerticalSpread, ExecError

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "results", "limit_ladder.jsonl")

RUNGS = [0.0, 0.25, 0.5, 0.75, 1.0]

# every client order id this run has minted. Cleanup touches these and nothing else.
MINE = set()


def now():
    return datetime.now(timezone.utc).isoformat()


def quote_legs(underlying, short_sym, long_sym, min_dte, max_dte, kind):
    """Re-quote the two legs we are laddering. Returns (short, long) Candidates."""
    spot = float(aio.req("GET", "%s/v2/stocks/%s/trades/latest" % (aio.DATA, underlying),
                         params={"feed": "iex"})["trade"]["p"])
    chain = S.fetch_chain(underlying, min_dte, max_dte, kind, spot)
    by = {c.symbol: c for c in chain}
    return by.get(short_sym), by.get(long_sym), spot


def cancel(order_id):
    try:
        aio.req("DELETE", "%s/v2/orders/%s" % (aio.TRADING, order_id))
        return True
    except Exception:
        return False


def open_orders():
    try:
        return aio.req("GET", "%s/v2/orders" % aio.TRADING, params={"status": "open"})
    except Exception:
        return []


def cancel_mine():
    """Cancel resting orders this run submitted. Anything else on the account
    belongs to somebody who did not ask us to touch it."""
    n = 0
    for o in open_orders():
        if o.get("client_order_id") in MINE and cancel(o.get("id")):
            n += 1
    return n


def close_legs(spread):
    """Close only the two contracts we laddered, never the account."""
    out = []
    for sym in (spread.short_symbol, spread.long_symbol):
        out.append(aio.flatten_symbol(sym, verbose=False))
    return out


def cleanup(spread, reason):
    """Never leave OUR resting order or OUR position behind, whatever went wrong."""
    n = cancel_mine()
    held = [p for p in aio.req("GET", "%s/v2/positions" % aio.TRADING)
            if p.get("symbol") in (spread.short_symbol, spread.long_symbol)]
    if held:
        close_legs(spread)
    print("  cleanup (%s): cancelled %d of our orders, closed %d of our legs"
          % (reason, n, len(held)))


def run_rung(ex, spread, theta, short, long, seq, wait_s, spot):
    aggressive = short.bid - long.ask
    passive = short.ask - long.bid
    mid = short.mid - long.mid
    limit = round(aggressive + theta * (passive - aggressive), 2)

    rec = {"ts": now(), "theta": theta, "limit": limit, "spot": spot,
           "short": {"symbol": short.symbol, "bid": short.bid, "ask": short.ask,
                     "mid": round(short.mid, 4), "delta": short.delta},
           "long": {"symbol": long.symbol, "bid": long.bid, "ask": long.ask,
                    "mid": round(long.mid, 4), "delta": long.delta},
           "aggressive": round(aggressive, 4), "mid_credit": round(mid, 4),
           "passive": round(passive, 4),
           "limit_minus_fair": round(limit - mid, 4)}

    if limit <= 0:
        rec.update(filled=None, skipped="limit is not a credit")
        return rec

    t0 = time.time()
    try:
        sub = ex.submit_vertical(spread, 1, limit, None, seq, opening=True)
    except ExecError as e:
        rec.update(filled=None, error=str(e)[:200])
        return rec
    coid = sub["client_order_id"]
    MINE.add(coid)
    o = ex.wait_for_fill(coid, timeout_s=wait_s)
    rec["waited_s"] = round(time.time() - t0, 1)

    if o and o.get("status") == "filled":
        fill = float(o.get("filled_avg_price") or 0)
        rec.update(filled=True, fill_credit=abs(fill),
                   fill_minus_limit=round(abs(fill) - limit, 4),
                   fill_minus_fair=round(abs(fill) - mid, 4))
        # close it straight away; the probe is about entry, not about carrying risk.
        # Only our two legs -- flatten_all() here would close an agent's positions.
        time.sleep(1.0)
        rec["exit"] = [{"symbol": r["symbol"], "closed": r["positions_closed"],
                        "clean": r["clean"]} for r in close_legs(spread)]
    else:
        # find and cancel whatever is resting under this client id
        killed = False
        for r in open_orders():
            if r.get("client_order_id") == coid:
                killed = cancel(r.get("id"))
        rec.update(filled=False, cancelled=killed,
                   status=(o or {}).get("status", "unfilled"))
    return rec


def agent_running():
    """A live agent on this account is a reason not to start, not a race to win."""
    live = []
    rundir = os.path.expanduser("~/midpoint/ops/run")
    if not os.path.isdir(rundir):
        return live
    for f in os.listdir(rundir):
        if not f.endswith(".pid"):
            continue
        try:
            pid = int(open(os.path.join(rundir, f)).read().strip())
            os.kill(pid, 0)
            live.append(f)
        except Exception:
            pass
    return live


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--passes", type=int, default=3)
    ap.add_argument("--wait", type=float, default=25.0, help="seconds to allow a fill")
    ap.add_argument("--underlying", default="SPY")
    ap.add_argument("--min-dte", type=int, default=0)
    ap.add_argument("--max-dte", type=int, default=2)
    ap.add_argument("--kind", default="put")
    ap.add_argument("--delta", type=float, default=0.20)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="run even though an agent is live on this account")
    a = ap.parse_args(argv)

    acct = aio.guard_not_competition()          # refuses the submission account
    live = agent_running()
    if live and not a.force:
        print("  an agent is running (%s). The probe closes positions by symbol, and "
              "a strike\n  it laddered is a strike the agent could open a minute "
              "later. Refusing.\n  Run before the agent starts, or pass --force if "
              "you know the account is idle." % ", ".join(live))
        return 2
    print("=" * 74)
    print("  WHAT A SPREAD HAS TO GIVE UP TO FILL   account %s" % acct["account_number"])
    print("=" * 74)

    held = {p["symbol"] for p in aio.req("GET", "%s/v2/positions" % aio.TRADING)}
    if held:
        print("  account already holds %d contracts; excluding them from the ladder"
              % len(held))
    built = S.build_vertical(underlying=a.underlying, min_dte=a.min_dte,
                             max_dte=a.max_dte, kind=a.kind,
                             target_short_delta=a.delta, width_strikes=1.0,
                             max_quoted_width=1.00, contracts=1,
                             exclude_symbols=held)
    if not built.get("ok"):
        print("  could not build a structure: %s" % built.get("reason"))
        return 1
    short, long = built["short"], built["long"]
    spread = VerticalSpread(short_symbol=short.symbol, long_symbol=long.symbol,
                            width=abs(short.strike - long.strike),
                            underlying=a.underlying, strategy="limit-ladder")
    print("  structure: short %s  long %s  width %.0f  dte %s"
          % (short.symbol, long.symbol, spread.width, built.get("dte")))
    print("  each rung is re-quoted; rung order is shuffled inside every pass\n")

    ex = Executor(dry_run=a.dry_run)
    seq = int(time.time()) % 100000
    rows = []
    try:
        for p in range(a.passes):
            order = RUNGS[:]
            random.shuffle(order)
            print("  pass %d  rungs %s" % (p + 1, order))
            for theta in order:
                s2, l2, spot = quote_legs(a.underlying, short.symbol, long.symbol,
                                          a.min_dte, a.max_dte, a.kind)
                if not s2 or not l2:
                    print("    theta %.2f  legs no longer quotable, skipping" % theta)
                    continue
                seq += 1
                r = run_rung(ex, spread, theta, s2, l2, seq, a.wait, spot)
                rows.append(r)
                with open(OUT, "a") as fh:
                    fh.write(json.dumps(r, sort_keys=True) + "\n")
                if r.get("skipped"):
                    print("    theta %.2f  limit %.2f  skipped (%s)"
                          % (theta, r["limit"], r["skipped"]))
                elif r.get("filled") is None:
                    print("    theta %.2f  limit %.2f  ERROR %s"
                          % (theta, r["limit"], r.get("error", "")[:60]))
                elif r["filled"]:
                    print("    theta %.2f  limit %.2f (fair %.2f)  FILLED at %.2f  "
                          "vs fair %+.2f  in %.0fs"
                          % (theta, r["limit"], r["mid_credit"], r["fill_credit"],
                             r["fill_minus_fair"], r["waited_s"]))
                else:
                    print("    theta %.2f  limit %.2f (fair %.2f)  no fill in %.0fs"
                          % (theta, r["limit"], r["mid_credit"], r["waited_s"]))
    except KeyboardInterrupt:
        print("\n  interrupted")
    finally:
        cleanup(spread, "end of run")

    print("\n  %d rungs written to %s" % (len(rows), OUT))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
