"""
alpaca_io.py -- resilient HTTP and guaranteed cleanup for every tool in this project.

WHY THIS EXISTS
---------------
A near-close sweep on 31 Aug 2026 died on a single SSL handshake timeout, mid-probe.
Three defects, all of which would be fatal to an unattended agent:

  1. no retry on transient network errors -- one blip killed the run
  2. no incremental persistence      -- every probe collected was lost
  3. cleanup flattened positions but did not cancel ORDERS, leaving one live

Alpaca's own CLI already retries 429/5xx three times and honours Retry-After. Raw
urllib does nothing. This module is the equivalent, plus the cleanup the incident
showed was missing.
"""

import json, os, random, socket, ssl, sys, time
import urllib.error, urllib.parse, urllib.request

TRADING = "https://paper-api.alpaca.markets"
DATA = "https://data.alpaca.markets"
COMPETITION_ACCOUNT = "PA32CGA2U1DY"

RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_RETRIES = 4
BASE_BACKOFF = 0.8


class Fatal(Exception):
    pass


def headers():
    k, s = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_SECRET_KEY")
    if not k or not s:
        raise Fatal("ALPACA_API_KEY / ALPACA_SECRET_KEY not set "
                    "(run: source ~/.config/midpoint/lab.env)")
    return {"APCA-API-KEY-ID": k, "APCA-API-SECRET-KEY": s,
            "Content-Type": "application/json", "Accept": "application/json"}


def req(method, url, params=None, body=None, timeout=25, retries=MAX_RETRIES, quiet=True):
    """HTTP with exponential backoff and jitter on transient failures.

    Retries: connection errors, socket timeouts, and HTTP 429/5xx.
    Does NOT retry 4xx other than 429 -- those are our bug, not the network's.
    """
    if params:
        url = "%s?%s" % (url, urllib.parse.urlencode(params))
    data = json.dumps(body).encode() if body is not None else None
    last = None
    for attempt in range(retries + 1):
        try:
            r = urllib.request.Request(url, data=data, headers=headers(), method=method)
            with urllib.request.urlopen(r, timeout=timeout,
                                        context=ssl.create_default_context()) as resp:
                return json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            last = e
            if e.code not in RETRY_STATUS:
                if not quiet:
                    print("  ! HTTP %s %s" % (e.code, e.read().decode()[:180]), file=sys.stderr)
                raise
            wait = float(e.headers.get("Retry-After") or 0) or _backoff(attempt)
        except (urllib.error.URLError, socket.timeout, ssl.SSLError, OSError) as e:
            last = e
            wait = _backoff(attempt)
        if attempt == retries:
            break
        print("  ~ transient (%s), retry %d/%d in %.1fs"
              % (type(last).__name__, attempt + 1, retries, wait), file=sys.stderr)
        time.sleep(wait)
    raise Fatal("request failed after %d retries: %s %s -- %s"
                % (retries, method, url.split("?")[0], last))


def _backoff(attempt):
    return BASE_BACKOFF * (2 ** attempt) * (0.7 + 0.6 * random.random())


def account():
    return req("GET", "%s/v2/account" % TRADING)


def guard_not_competition():
    """Refuse to run anything that trades against the submission account."""
    a = account()
    if a.get("account_number") == COMPETITION_ACCOUNT:
        raise Fatal("this is the COMPETITION account (%s). Refusing to trade."
                    % COMPETITION_ACCOUNT)
    return a


def flatten_symbol(symbol, coid_prefix=None, verbose=True):
    """Close one symbol's position and cancel only orders we recognise as ours.

    flatten_all() is correct for an agent that owns the whole account, and wrong
    for anything that shares it: a research probe calling it would close positions
    an agent is still managing. Experiments that run alongside the agent clean up
    after themselves with this instead, which touches one symbol and, if given a
    client_order_id prefix, only the orders carrying it.
    """
    result = {"orders_cancelled": 0, "positions_closed": 0, "symbol": symbol}
    for o in req("GET", "%s/v2/orders" % TRADING, params={"status": "open"}):
        if o.get("symbol") != symbol:
            continue
        if coid_prefix and not (o.get("client_order_id") or "").startswith(coid_prefix):
            continue
        try:
            req("DELETE", "%s/v2/orders/%s" % (TRADING, o["id"]))
            result["orders_cancelled"] += 1
        except Exception:
            pass
    time.sleep(1.0)
    for p in req("GET", "%s/v2/positions" % TRADING):
        if p.get("symbol") != symbol:
            continue
        try:
            req("DELETE", "%s/v2/positions/%s" % (TRADING, urllib.parse.quote(p["symbol"])))
            result["positions_closed"] += 1
        except Exception:
            pass
    time.sleep(1.5)
    left = [p for p in req("GET", "%s/v2/positions" % TRADING) if p.get("symbol") == symbol]
    result["positions_left"] = len(left)
    result["clean"] = result["positions_left"] == 0
    if verbose:
        print("[flatten %s] cancelled %d, closed %d -> %s"
              % (symbol, result["orders_cancelled"], result["positions_closed"],
                 "CLEAN" if result["clean"] else "STILL HOLDING %d" % result["positions_left"]))
    return result


def flatten_all(verbose=True):
    """Cancel every open order, then close every position, then VERIFY both are zero.

    Order matters: cancelling first prevents a resting order from re-opening a
    position we just closed.
    """
    result = {"orders_cancelled": 0, "positions_closed": 0}
    for o in req("GET", "%s/v2/orders" % TRADING, params={"status": "open"}):
        try:
            req("DELETE", "%s/v2/orders/%s" % (TRADING, o["id"]))
            result["orders_cancelled"] += 1
        except Exception:
            pass
    time.sleep(1.0)
    for p in req("GET", "%s/v2/positions" % TRADING):
        try:
            req("DELETE", "%s/v2/positions/%s" % (TRADING, urllib.parse.quote(p["symbol"])))
            result["positions_closed"] += 1
        except Exception:
            pass
    time.sleep(1.5)
    result["orders_left"] = len(req("GET", "%s/v2/orders" % TRADING, params={"status": "open"}))
    result["positions_left"] = len(req("GET", "%s/v2/positions" % TRADING))
    result["clean"] = result["orders_left"] == 0 and result["positions_left"] == 0
    if verbose:
        print("[flatten] cancelled %d orders, closed %d positions -> %s"
              % (result["orders_cancelled"], result["positions_closed"],
                 "CLEAN" if result["clean"] else
                 "STILL DIRTY (%d orders, %d positions)"
                 % (result["orders_left"], result["positions_left"])))
    return result


class Journal:
    """Append-only JSONL. A crash loses at most the row in flight, never the run."""

    def __init__(self, path):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.f = open(path, "a", buffering=1)      # line buffered

    def write(self, row):
        self.f.write(json.dumps(row) + "\n")
        self.f.flush()
        os.fsync(self.f.fileno())

    def close(self):
        try: self.f.close()
        except Exception: pass

    @staticmethod
    def read(path):
        if not os.path.exists(path):
            return []
        out = []
        for line in open(path):
            line = line.strip()
            if line:
                try: out.append(json.loads(line))
                except json.JSONDecodeError: pass    # tolerate a torn final line
        return out
