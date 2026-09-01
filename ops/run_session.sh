#!/bin/zsh
# The live session: pre-flight first, and do not start if it objects.
set -u
ROOT="$HOME/midpoint"
LOG="$ROOT/ops/logs"; mkdir -p "$LOG"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
ONDATE="${1:-any}"; shift 2>/dev/null || true
EXPECT="${EXPECT_ACCOUNT:-PA3TD7HMABNH}"

if [ -f "$HOME/.config/midpoint/lab.env" ]; then
  set -a; . "$HOME/.config/midpoint/lab.env"; set +a
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
/usr/bin/python3 "$ROOT/tools/agent.py" "$@" >> "$LOG/session.$STAMP.log" 2>&1
RC=$?
echo "[$STAMP] session exited $RC" >> "$LOG/cron.log"

# whatever happened, do not leave the account holding something overnight
echo "[$STAMP] verifying flat" >> "$LOG/cron.log"
/usr/bin/python3 - <<'PY' >> "$LOG/cron.log" 2>&1
import os, sys
sys.path.insert(0, os.path.expanduser("~/midpoint/tools"))
import alpaca_io as aio
pos = aio.req("GET", "%s/v2/positions" % aio.TRADING)
orders = aio.req("GET", "%s/v2/orders" % aio.TRADING, params={"status": "open"})
print("  after session: %d positions, %d resting orders" % (len(pos), len(orders)))
if pos or orders:
    print("  not flat -- flattening")
    print("  ", aio.flatten_all(verbose=False))
PY
exit $RC
