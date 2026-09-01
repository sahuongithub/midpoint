#!/usr/bin/env python3
"""
feed_audit_sweep.py  --  How wrong is Alpaca's free indicative options feed?

METHOD
------
The oracle test established that Alpaca's paper engine fills a marketable order at
the TRUE NBBO, independent of your limit price. That works on both sides:

    aggressive BUY  limit (well above ask)  ->  fill price = TRUE BEST ASK
    aggressive SELL limit (well below bid)  ->  fill price = TRUE BEST BID

So for each contract we read the indicative quote, buy one, immediately sell one,
and recover the complete true NBBO. Net position is zero; the only cost is crossing
the real spread once, in simulated money.

We then measure how far the free feed sits from reality, sliced by underlying,
days-to-expiry, moneyness and option type.

SAFETY
------
Refuses to run on the competition account. Requires --yes. Cancels and force-closes
anything left open. One contract at a time.

USAGE
-----
    source ~/.config/midpoint/lab.env
    python3 feed_audit_sweep.py --preflight
    python3 feed_audit_sweep.py --yes
"""

import argparse, csv, json, os, re, ssl, sys, time
import urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

TRADING = "https://paper-api.alpaca.markets"
DATA = "https://data.alpaca.markets"

COMPETITION_ACCOUNT = "PA32CGA2U1DY"      # never trade here

UNDERLYINGS = ["SPY", "QQQ", "AAPL"]
DTE_BUCKETS = [(2, 9), (10, 35), (36, 90)]
MONEYNESS = [0.98, 1.00, 1.02]
TYPES = ["call", "put"]

PAD = 1.00          # how far through the quote to push, in dollars
FILL_WAIT_S = 20    # marketable fills land in <1s; this is a generous ceiling
PACE_S = 0.7        # stay under the 200 req/min free-tier budget


def die(m):
    print("\nFATAL: %s" % m, file=sys.stderr); sys.exit(1)


def _h():
    k, s = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_SECRET_KEY")
    if not k or not s:
        die("keys not set - run: source ~/.config/midpoint/lab.env")
    return {"APCA-API-KEY-ID": k, "APCA-API-SECRET-KEY": s,
            "Content-Type": "application/json", "Accept": "application/json"}


def req(method, url, params=None, body=None, quiet=False):
    if params:
        url = "%s?%s" % (url, urllib.parse.urlencode(params))
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, headers=_h(), method=method)
    try:
        with urllib.request.urlopen(r, timeout=30, context=ssl.create_default_context()) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        if not quiet:
            print("  ! HTTP %s %s: %s" % (e.code, url.split("?")[0], e.read().decode()[:200]),
                  file=sys.stderr)
        raise


def parse_ts(v):
    v = v.replace("Z", "+00:00")
    v = re.sub(r"\.(\d{1,6})\d*", lambda m: "." + m.group(1).ljust(6, "0"), v)
    return datetime.fromisoformat(v)


def tick(p):
    p = max(0.01, p)
    return round(round(p / 0.01) * 0.01, 2) if p < 3.0 else round(round(p / 0.05) * 0.05, 2)


def spot(sym):
    j = req("GET", "%s/v2/stocks/%s/trades/latest" % (DATA, sym), params={"feed": "iex"})
    return float(j["trade"]["p"])


def contracts_for(sym, px, lo_dte, hi_dte, mny, typ):
    today = datetime.now(timezone.utc).date()
    target = px * mny
    j = req("GET", "%s/v2/options/contracts" % TRADING, params={
        "underlying_symbols": sym, "status": "active", "type": typ,
        "expiration_date_gte": str(today + timedelta(days=lo_dte)),
        "expiration_date_lte": str(today + timedelta(days=hi_dte)),
        "strike_price_gte": str(round(target * 0.97, 2)),
        "strike_price_lte": str(round(target * 1.03, 2)),
        "limit": "500"})
    cs = j.get("option_contracts") or []
    if not cs:
        return None
    cs.sort(key=lambda c: (abs(float(c["strike_price"]) - target), c["expiration_date"]))
    return cs[0]


def quote(osi):
    j = req("GET", "%s/v1beta1/options/quotes/latest" % DATA,
            params={"symbols": osi, "feed": "indicative"}, quiet=True)
    q = (j.get("quotes") or {}).get(osi)
    if not q:
        return None
    return {"bid": float(q.get("bp") or 0), "ask": float(q.get("ap") or 0)}


def market_probe(osi, side, limit_price, tag):
    body = {"symbol": osi, "qty": "1", "side": side, "type": "limit",
            "time_in_force": "day", "limit_price": "%.2f" % limit_price,
            "client_order_id": "audit-%s-%d" % (tag, int(time.time() * 1e6) % 10**9)}
    try:
        o = req("POST", "%s/v2/orders" % TRADING, body=body, quiet=True)
    except urllib.error.HTTPError:
        return None
    oid = o["id"]
    t0 = time.time()
    while time.time() - t0 < FILL_WAIT_S:
        cur = req("GET", "%s/v2/orders/%s" % (TRADING, oid), quiet=True)
        if cur.get("status") in ("filled", "canceled", "rejected", "expired"):
            return cur
        time.sleep(0.4)
    req("DELETE", "%s/v2/orders/%s" % (TRADING, oid), quiet=True)
    return None


def force_flat(osi):
    enc = urllib.parse.quote(osi)
    try:
        req("DELETE", "%s/v2/positions/%s" % (TRADING, enc), quiet=True)
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--preflight", action="store_true")
    ap.add_argument("--max", type=int, default=40)
    ap.add_argument("--out", default=os.path.expanduser("~/midpoint/docs/feed_audit"))
    a = ap.parse_args()

    acct = req("GET", "%s/v2/account" % TRADING)
    num = acct.get("account_number")
    print("[account] %s   equity $%s" % (num, format(float(acct["equity"]), ",.2f")))
    if num == COMPETITION_ACCOUNT:
        die("this is the COMPETITION account (%s). Refusing." % COMPETITION_ACCOUNT)

    clock = req("GET", "%s/v2/clock" % TRADING)
    print("[clock]   open=%s  %s" % (clock.get("is_open"), clock.get("timestamp")))

    spots = {}
    for u in UNDERLYINGS:
        try:
            spots[u] = spot(u)
            print("[spot]    %-5s %.2f" % (u, spots[u]))
        except Exception as e:
            print("[spot]    %-5s unavailable (%s)" % (u, e))

    grid = []
    for u in UNDERLYINGS:
        if u not in spots:
            continue
        for lo, hi in DTE_BUCKETS:
            for m in MONEYNESS:
                for t in TYPES:
                    grid.append((u, lo, hi, m, t))
    grid = grid[:a.max]
    print("\n[plan]    %d cells: %d underlyings x %d dte x %d moneyness x %d types"
          % (len(grid), len(spots), len(DTE_BUCKETS), len(MONEYNESS), len(TYPES)))

    if a.preflight or not a.yes:
        print("\nPreflight only - nothing submitted. Re-run with --yes.")
        return
    if not clock.get("is_open"):
        die("market closed - marketable probes will not fill.")

    rows, seen = [], set()
    print("\n%-22s %-4s %5s %6s | %6s %6s | %6s %6s | %7s %7s %7s"
          % ("contract", "typ", "dte", "mny", "iBid", "iAsk", "tBid", "tAsk",
             "askErr", "bidErr", "midErr"))
    print("-" * 108)

    for (u, lo, hi, m, t) in grid:
        try:
            c = contracts_for(u, spots[u], lo, hi, m, t)
        except Exception:
            c = None
        if not c or c["symbol"] in seen:
            continue
        osi = c["symbol"]; seen.add(osi)
        q = quote(osi)
        if not q or q["ask"] <= 0 or q["bid"] <= 0:
            print("%-22s  no indicative quote - skipped" % osi)
            time.sleep(PACE_S); continue

        dte = (datetime.strptime(c["expiration_date"], "%Y-%m-%d").date()
               - datetime.now(timezone.utc).date()).days

        buy = market_probe(osi, "buy", tick(q["ask"] + PAD), "b")
        if not buy or not buy.get("filled_avg_price"):
            print("%-22s  buy did not fill - skipped" % osi)
            force_flat(osi); time.sleep(PACE_S); continue
        true_ask = float(buy["filled_avg_price"])

        sell = market_probe(osi, "sell", tick(max(0.01, q["bid"] - PAD)), "s")
        if not sell or not sell.get("filled_avg_price"):
            print("%-22s  sell did not fill - force closing" % osi)
            force_flat(osi); time.sleep(PACE_S); continue
        true_bid = float(sell["filled_avg_price"])

        r = {
            "contract": osi, "underlying": u, "type": t, "dte": dte,
            "strike": float(c["strike_price"]), "spot": spots[u],
            "moneyness": round(float(c["strike_price"]) / spots[u], 4),
            "ind_bid": q["bid"], "ind_ask": q["ask"],
            "ind_mid": round((q["bid"] + q["ask"]) / 2, 4),
            "ind_width": round(q["ask"] - q["bid"], 4),
            "true_bid": true_bid, "true_ask": true_ask,
            "true_mid": round((true_bid + true_ask) / 2, 4),
            "true_width": round(true_ask - true_bid, 4),
            "ask_err": round(true_ask - q["ask"], 4),
            "bid_err": round(true_bid - q["bid"], 4),
            "mid_err": round((true_bid + true_ask) / 2 - (q["bid"] + q["ask"]) / 2, 4),
            "width_err": round((true_ask - true_bid) - (q["ask"] - q["bid"]), 4),
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        rows.append(r)
        print("%-22s %-4s %5d %6.3f | %6.2f %6.2f | %6.2f %6.2f | %+7.3f %+7.3f %+7.3f"
              % (osi, t, dte, r["moneyness"], q["bid"], q["ask"],
                 true_bid, true_ask, r["ask_err"], r["bid_err"], r["mid_err"]))
        time.sleep(PACE_S)

    if not rows:
        die("no usable rows collected.")

    with open(a.out + ".json", "w") as f:
        json.dump(rows, f, indent=2)
    with open(a.out + ".csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)

    def stats(vals):
        vals = sorted(vals); n = len(vals)
        return (sum(vals) / n, vals[n // 2], min(vals), max(vals), n)

    print("\n" + "=" * 108)
    print("  FEED AUDIT SUMMARY   n = %d contracts" % len(rows))
    print("=" * 108)
    for name, key in (("ask error (true-indicative)", "ask_err"),
                      ("bid error (true-indicative)", "bid_err"),
                      ("mid error (true-indicative)", "mid_err"),
                      ("width error (true-indicative)", "width_err")):
        mean, med, lo_, hi_, n = stats([r[key] for r in rows])
        print("  %-30s mean %+.4f   median %+.4f   range %+.3f .. %+.3f"
              % (name, mean, med, lo_, hi_))

    iw = [r["ind_width"] for r in rows]; tw = [r["true_width"] for r in rows]
    print("\n  indicative width  mean %.4f   median %.4f" % (sum(iw) / len(iw), sorted(iw)[len(iw)//2]))
    print("  TRUE width        mean %.4f   median %.4f" % (sum(tw) / len(tw), sorted(tw)[len(tw)//2]))
    print("  => indicative is %.2fx the true width on average" % ((sum(iw)/len(iw)) / max(1e-9, sum(tw)/len(tw))))

    print("\n  by underlying:")
    for u in sorted(set(r["underlying"] for r in rows)):
        sub = [r for r in rows if r["underlying"] == u]
        print("    %-5s n=%2d  mid err mean %+.4f   ind width %.3f vs true %.3f"
              % (u, len(sub), sum(r["mid_err"] for r in sub)/len(sub),
                 sum(r["ind_width"] for r in sub)/len(sub),
                 sum(r["true_width"] for r in sub)/len(sub)))

    print("\n  by dte bucket:")
    for lo, hi in DTE_BUCKETS:
        sub = [r for r in rows if lo <= r["dte"] <= hi]
        if sub:
            print("    %2d-%2dd n=%2d  mid err mean %+.4f   ind width %.3f vs true %.3f"
                  % (lo, hi, len(sub), sum(r["mid_err"] for r in sub)/len(sub),
                     sum(r["ind_width"] for r in sub)/len(sub),
                     sum(r["true_width"] for r in sub)/len(sub)))

    print("\n  written to %s.json / .csv\n" % a.out)

    pos = req("GET", "%s/v2/positions" % TRADING)
    print("  open positions after sweep: %d %s" % (len(pos), [p["symbol"] for p in pos]))
    for p in pos:
        force_flat(p["symbol"])


if __name__ == "__main__":
    main()
