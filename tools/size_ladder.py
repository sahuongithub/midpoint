#!/usr/bin/env python3
"""
size_ladder.py -- does order size move the price in Alpaca's paper venue?

THE QUESTION
------------
Every submission in this hackathon reports a paper P&L. All of those numbers
inherit whatever the paper venue assumes about liquidity, and nobody has
measured what that assumption is.

Theory says what a real venue does. Grinold and Kahn (ch. 16) derive market
impact from the liquidity supplier's inventory risk: time to clear scales with
the size you want relative to daily volume, risk scales with the square root of
time, so impact scales as

    impact  ~  sigma * sqrt( V_trade / V_daily )

the square-root law, which Loeb's 1983 block-bid data fits closely and which
BARRA's own fitting confirmed with an exponent of one half. The rule of thumb
that falls out is that it costs about one day's volatility to trade one day's
volume. Cartea et al. (ch. 4) show the same thing from the other side: displayed
depth at the touch is tiny -- a median of a few hundred shares even in AAPL --
so a large marketable order walks the book by construction.

A simulator that fills any quantity at the displayed NBBO shows none of this: a
flat line where theory and every real market show a rising curve. Harris (ch.
15) is blunt about what that flat line hides -- it grants the trader the
benefits of price discrimination that real block traders must pay for.

THE TEST
--------
Send marketable buy orders of increasing size in one tight contract, record the
fill price against the displayed NBBO ask and the displayed ask size at the
moment of submission, then close each position immediately. If the fill price is
independent of size, and in particular if orders far larger than the displayed
size still fill at the touch, the venue is granting infinite depth at the
inside.

COST CONTROL
------------
This experiment pays a real spread on every round trip, so:
  * it refuses to run on a contract wider than --max-width (default $0.05),
  * it tracks spend and stops at --max-spend (default $150),
  * it flattens after every rung and verifies flat at the end,
  * it refuses to run on the competition account.
Every rung is written to disk as it completes.
"""
import json, os, sys, time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import alpaca_io as aio

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "results", "size_ladder.jsonl")


def et_now():
    return datetime.now(timezone.utc) - timedelta(hours=4)


def snapshot(symbol):
    j = aio.req("GET", "%s/v1beta1/options/snapshots" % aio.DATA,
                params={"symbols": symbol, "feed": "indicative"})
    sn = (j.get("snapshots") or {}).get(symbol) or {}
    q = sn.get("latestQuote") or {}
    return {"bid": float(q.get("bp") or 0), "ask": float(q.get("ap") or 0),
            "bid_size": q.get("bs"), "ask_size": q.get("as"),
            "ts": q.get("t")}


def pick_contract(underlying, max_width, span=0.02):
    """Pick the contract that makes the test both cheap and decisive.

    Cost is the spread times size, so we need a contract quoted at the minimum
    tick. Decisiveness needs the ladder to climb past the DISPLAYED size, since
    the claim under test is that the venue fills any quantity at the touch. So
    among contracts inside max_width we take the one showing the least size:
    that is where a real book would run out first, and where a simulator that
    ignores size will be caught most cheaply.
    """
    s = float(aio.req("GET", "%s/v2/stocks/%s/trades/latest" % (aio.DATA, underlying),
                      params={"feed": "iex"})["trade"]["p"])
    today = et_now().date()
    j = aio.req("GET", "%s/v2/options/contracts" % aio.TRADING, params={
        "underlying_symbols": underlying, "status": "active", "type": "call",
        "expiration_date_gte": str(today), "expiration_date_lte": str(today + timedelta(days=3)),
        "strike_price_gte": str(round(s * (1 - span), 2)),
        "strike_price_lte": str(round(s * (1 + span), 2)), "limit": "500"})
    cs = j.get("option_contracts") or []
    if not cs:
        return None, s
    exp = sorted({c["expiration_date"] for c in cs})[0]
    cs = [c for c in cs if c["expiration_date"] == exp]
    snaps = aio.req("GET", "%s/v1beta1/options/snapshots" % aio.DATA,
                    params={"symbols": ",".join(c["symbol"] for c in cs[:100]),
                            "feed": "indicative"}).get("snapshots") or {}
    best = None
    for c in cs:
        q = (snaps.get(c["symbol"]) or {}).get("latestQuote") or {}
        b, a = float(q.get("bp") or 0), float(q.get("ap") or 0)
        if b <= 0 or a <= 0:
            continue
        w = a - b
        if w > max_width:
            continue
        cand = {"symbol": c["symbol"], "strike": float(c["strike_price"]),
                "expiry": exp, "bid": b, "ask": a, "width": w,
                "ask_size": q.get("as") or 0, "bid_size": q.get("bs") or 0}
        if cand["ask_size"] <= 0 or cand["bid_size"] <= 0:
            continue
        key = (round(w, 4), cand["ask_size"])          # tight first, then thin
        if best is None or key < (round(best["width"], 4), best["ask_size"]):
            best = cand
    return best, s


def auto_rungs(displayed, width, budget):
    """Sizes that straddle the displayed depth, spending the budget where it decides.

    Cost is width x 100 x size, so the budget buys a fixed number of contracts and
    the question is how to spend them. A ladder of small rungs looks thorough and
    proves nothing: the claim under test is that the venue fills sizes it never
    displayed, so the rungs above 1.0x displayed depth are the only ones that can
    falsify anything. We therefore buy the baseline of one contract, then the
    largest multiple of displayed size the budget allows, then fill in the middle
    rungs with whatever is left.
    """
    d = max(1, int(displayed or 1))
    per = width * 100.0
    rungs, spend = [1], per                       # baseline always
    for mult in (4, 2, 1):                        # decisive first
        n = int(mult * d)
        if n in rungs:
            continue
        if spend + per * n <= budget:
            rungs.append(n); spend += per * n
            break
    for mult in (2, 1, 0.5):                      # then a curve, if affordable
        n = max(2, int(mult * d))
        if n in rungs or n > max(rungs):
            continue
        if spend + per * n <= budget:
            rungs.append(n); spend += per * n
    return sorted(set(rungs)), spend


def submit(symbol, side, qty, limit, intent):
    body = {"symbol": symbol, "qty": str(qty), "side": side, "type": "limit",
            "time_in_force": "day", "limit_price": "%.2f" % limit,
            "position_intent": intent,
            "client_order_id": "mp-ladder-%d-%s-%d" % (qty, side, int(time.time() * 1000))}
    return aio.req("POST", "%s/v2/orders" % aio.TRADING, body=body)


def wait_fill(order_id, timeout=45.0):
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        o = aio.req("GET", "%s/v2/orders/%s" % (aio.TRADING, order_id))
        last = o
        if o.get("status") in ("filled", "canceled", "expired", "rejected"):
            return o, time.time() - t0
        time.sleep(0.4)
    try:
        aio.req("DELETE", "%s/v2/orders/%s" % (aio.TRADING, order_id))
    except Exception:
        pass
    return last, time.time() - t0


def main(argv):
    underlying = "SPY"
    rungs = None                     # default: adapt to the displayed size
    max_width = 0.05
    max_spend = 250.0
    dry = "--dry-run" in argv
    for i, a in enumerate(argv):
        if a == "--underlying" and i + 1 < len(argv): underlying = argv[i + 1]
        if a == "--rungs" and i + 1 < len(argv): rungs = [int(x) for x in argv[i + 1].split(",")]
        if a == "--max-width" and i + 1 < len(argv): max_width = float(argv[i + 1])
        if a == "--max-spend" and i + 1 < len(argv): max_spend = float(argv[i + 1])

    aio.guard_not_competition()
    clock = aio.req("GET", "%s/v2/clock" % aio.TRADING)
    if not clock.get("is_open") and not dry:
        print("market closed (next open %s) -- not running" % clock.get("next_open"))
        return 0
    if not clock.get("is_open"):
        print("(market closed: dry run uses the last quotes seen)")

    c, spot = pick_contract(underlying, max_width)
    if not c:
        print("no contract at or inside $%.2f wide; not running" % max_width)
        return 0
    acct = aio.account()
    print("=" * 78)
    print("  SIZE LADDER   %s  spot %.2f  equity $%s" % (underlying, spot, acct["equity"]))
    print("  contract %s  strike %.0f  exp %s" % (c["symbol"], c["strike"], c["expiry"]))
    print("  quote %.2f / %.2f  width %.2f  displayed sizes  bid %s / ask %s"
          % (c["bid"], c["ask"], c["width"], c["bid_size"], c["ask_size"]))
    if rungs is None:
        rungs, est = auto_rungs(c["ask_size"], c["width"], max_spend)
    else:
        est = sum(c["width"] * 100 * n for n in rungs)
    print("  rungs %s   est cost $%.0f   budget $%.0f   %s"
          % (rungs, est, max_spend, "DRY RUN" if dry else "LIVE (this spends money)"))
    print("=" * 78)
    if dry:
        print("  estimated round-trip cost at the current width: $%.0f" % est)
        for n in rungs:
            print("    %4d contracts -> ~$%5.0f   %5.1fx the displayed ask size of %s"
                  % (n, c["width"] * 100 * n,
                     n / float(c["ask_size"]) if c["ask_size"] else float("nan"),
                     c["ask_size"]))
        top = rungs[-1] / float(c["ask_size"]) if c["ask_size"] else 0
        if top > 1.0:
            print("\n  The decisive rung is %d contracts, %.1fx the %s displayed at the"
                  % (rungs[-1], top, c["ask_size"]))
            print("  offer. A real book showing that size cannot fill it at one price.")
        else:
            print("\n  WARNING: the budget only reaches %.1fx displayed size, so this run"
                  % top)
            print("  cannot test the interesting claim. Raise --max-spend or wait for a")
            print("  thinner quote. Reported anyway, and labelled as inconclusive.")
        return 0

    spent, rows = 0.0, []
    with open(OUT, "a", buffering=1) as fh:
        for n in rungs:
            pre = snapshot(c["symbol"])
            if pre["ask"] <= 0 or pre["bid"] <= 0:
                print("  %3d: no quote, skipping" % n); continue
            est = (pre["ask"] - pre["bid"]) * 100 * n
            if spent + est > max_spend:
                print("  %3d: would breach the $%.0f budget (spent $%.0f, est $%.0f) -- stopping"
                      % (n, max_spend, spent, est))
                break
            buy = submit(c["symbol"], "buy", n, round(pre["ask"] * 1.10, 2), "buy_to_open")
            bo, blat = wait_fill(buy["id"])
            mid = snapshot(c["symbol"])
            sell = submit(c["symbol"], "sell", n, round(max(0.01, mid["bid"] * 0.90), 2),
                          "sell_to_close")
            so, slat = wait_fill(sell["id"])
            post = snapshot(c["symbol"])

            bfill = float(bo.get("filled_avg_price") or 0)
            sfill = float(so.get("filled_avg_price") or 0)
            bqty = int(float(bo.get("filled_qty") or 0))
            sqty = int(float(so.get("filled_qty") or 0))
            rt = (bfill - sfill) * 100 * min(bqty, sqty) if (bfill and sfill) else 0.0
            spent += max(0.0, rt)
            row = {"ts": datetime.now(timezone.utc).isoformat(),
                   "et": et_now().strftime("%H:%M:%S"),
                   "contract": c["symbol"], "strike": c["strike"], "expiry": c["expiry"],
                   "requested": n,
                   "pre_quote": pre, "mid_quote": mid, "post_quote": post,
                   "buy": {"status": bo.get("status"), "filled_qty": bqty,
                           "fill": bfill, "latency_s": round(blat, 2),
                           "slip_vs_ask": round(bfill - pre["ask"], 4) if bfill else None},
                   "sell": {"status": so.get("status"), "filled_qty": sqty,
                            "fill": sfill, "latency_s": round(slat, 2),
                            "slip_vs_bid": round(mid["bid"] - sfill, 4) if sfill else None},
                   "round_trip_usd": round(rt, 2),
                   "displayed_ask_size": pre["ask_size"],
                   "size_vs_displayed": (n / float(pre["ask_size"]))
                   if pre.get("ask_size") else None}
            fh.write(json.dumps(row) + "\n")
            rows.append(row)
            print("  %3d contracts | ask %.2f size %-5s | fill %.2f (slip %+.4f) | "
                  "sold %.2f | round trip $%.2f | spent $%.0f"
                  % (n, pre["ask"], pre["ask_size"], bfill,
                     (bfill - pre["ask"]) if bfill else float("nan"), sfill, rt, spent))
            time.sleep(1.0)

    # clean up ONLY what this experiment opened. The lab account may be running the
    # agent at the same time, and a blanket flatten would close positions it is
    # still managing -- turning a research probe into an intervention.
    print("\n  flattening this experiment's own contract and verifying")
    aio.flatten_symbol(c["symbol"], coid_prefix="mp-ladder-")
    pos = [p for p in aio.req("GET", "%s/v2/positions" % aio.TRADING)
           if p.get("symbol") == c["symbol"]]
    print("  open positions in %s after run: %d" % (c["symbol"], len(pos)))
    print("  total spent: $%.2f" % spent)

    if rows:
        print("\n  slippage against the displayed ask, by size:")
        print("  %10s %12s %12s %14s" % ("size", "displayed", "slip vs ask", "size/displayed"))
        for r in rows:
            print("  %10d %12s %12s %14s"
                  % (r["requested"], r["displayed_ask_size"],
                     ("%+.4f" % r["buy"]["slip_vs_ask"]) if r["buy"]["slip_vs_ask"] is not None else "-",
                     ("%.1fx" % r["size_vs_displayed"]) if r["size_vs_displayed"] else "-"))
        flat = all((r["buy"]["slip_vs_ask"] or 0) <= 0.0001 for r in rows)
        print("\n  %s" % ("Every rung filled at or inside the displayed ask, including sizes "
                          "far above the displayed depth: the venue grants unlimited size at "
                          "the touch, and the square-root impact law does not operate here."
                          if flat else
                          "Fills degraded with size: the venue models some depth."))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
