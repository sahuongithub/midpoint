#!/bin/zsh
# The live session: pre-flight first, and do not start if it objects.
set -u
ROOT="$HOME/midpoint"
LOG="$ROOT/ops/logs"; mkdir -p "$LOG"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
ONDATE="${1:-any}"; shift 2>/dev/null || true
EXPECT="${EXPECT_ACCOUNT:-${MIDPOINT_ALLOWED_ACCOUNT:-PA3TD7HMABNH}}"

ENVFILE="${MIDPOINT_ENV:-$HOME/.config/midpoint/lab.env}"
if [ -f "$ENVFILE" ]; then
  set -a; . "$ENVFILE"; set +a
fi

TODAY_ET="$(TZ=America/New_York date +%Y-%m-%d)"
if [ "$ONDATE" != "any" ] && [ "$ONDATE" != "$TODAY_ET" ]; then
  echo "[$STAMP] session scheduled for $ONDATE, today is $TODAY_ET: skipping" >> "$LOG/cron.log"
  exit 0
fi
if [ -f "$ROOT/HALT" ]; then
  echo "[$STAMP] HALT present, session not started" >> "$LOG/cron.log"
  exit 0
fi

echo "[$STAMP] pre-flight" >> "$LOG/cron.log"
if ! /usr/bin/python3 "$ROOT/tools/preflight.py" --expect "$EXPECT" \
      > "$LOG/preflight.$STAMP.log" 2>&1; then
  echo "[$STAMP] PRE-FLIGHT FAILED, session not started" >> "$LOG/cron.log"
  tail -6 "$LOG/preflight.$STAMP.log" >> "$LOG/cron.log"
  exit 1
fi

echo "[$STAMP] starting live session: $*" >> "$LOG/cron.log"
/usr/bin/python3 "$ROOT/tools/agent.py" "$@" >> "$LOG/session.$STAMP.log" 2>&1 &
AGENT_PID=$!
# Record the PYTHON pid, not this shell's. A pid file naming the wrapper is worse
# than none: killing it leaves the agent running while the file disappears, and
# the next start sees a free slot and launches a second agent onto the same
# account -- two sessions sharing one state file on the account that matters.
mkdir -p "$ROOT/ops/run"
[ -n "${EXPECT:-}" ] && echo "$AGENT_PID" > "$ROOT/ops/run/agent.$EXPECT.pid"
wait $AGENT_PID
RC=$?
export SESSION_RC="$RC"
echo "[$STAMP] session exited $RC" >> "$LOG/cron.log"

# Whatever happened, do not leave the account holding something overnight -- and
# write down what that cost. The exit is a trade like any other: it crosses the
# spread, it moves the equity curve, and a project whose subject is the true cost of
# execution cannot be the one place where a fill goes unrecorded. Before today this
# block flattened silently, the agent's journal showed only "structure_gone", and the
# P&L attribution had no way to tell a supervised exit from a position that expired.
echo "[$STAMP] verifying flat" >> "$LOG/cron.log"
/usr/bin/python3 - <<'PY' >> "$LOG/cron.log" 2>&1
import os, sys, json, datetime
sys.path.insert(0, os.path.expanduser("~/midpoint/tools"))
import alpaca_io as aio

acct = os.environ.get("MIDPOINT_ALLOWED_ACCOUNT")
stem = "agent.%s" % acct if acct else "agent"
JOURNAL = os.path.expanduser("~/midpoint/docs/%s.jsonl" % stem)

def journal(event, **kw):
    now = datetime.datetime.now(datetime.timezone.utc)
    rec = {"ts": now.isoformat(), "event": event, "by": "run_session.sh"}
    rec.update(kw)
    with open(JOURNAL, "a") as fh:
        fh.write(json.dumps(rec, sort_keys=True) + "\n")

pos = aio.req("GET", "%s/v2/positions" % aio.TRADING)
orders = aio.req("GET", "%s/v2/orders" % aio.TRADING, params={"status": "open"})
print("  after session: %d positions, %d resting orders" % (len(pos), len(orders)))
if not (pos or orders):
    journal("session_flat", positions=0, resting=0)
else:
    print("  not flat -- flattening")
    held = [{"symbol": p["symbol"], "qty": p["qty"],
             "unrealized": float(p.get("unrealized_pl") or 0.0)} for p in pos]
    journal("session_flatten_begin", positions=held, resting=len(orders),
            reason="session exited (rc=%s) while not flat" % os.environ.get("SESSION_RC", "?"))
    result = aio.flatten_all(verbose=False)
    print("  ", result)
    # read back what the flatten actually paid, rather than what it intended to
    fills = []
    try:
        recent = aio.req("GET", "%s/v2/orders" % aio.TRADING,
                         params={"status": "closed", "limit": 50, "direction": "desc"})
        wanted = {h["symbol"] for h in held}
        for o in recent:
            if o.get("symbol") in wanted and o.get("filled_qty") not in (None, "0"):
                fills.append({"symbol": o["symbol"], "side": o["side"],
                              "qty": o["filled_qty"], "price": o.get("filled_avg_price"),
                              "at": o.get("filled_at")})
                wanted.discard(o["symbol"])
            if not wanted:
                break
    except Exception as e:          # never let bookkeeping block the flatten
        journal("session_flatten_unrecorded", error=str(e))
    journal("session_flatten_done", fills=fills, result=str(result))
PY
exit $RC
