#!/bin/zsh
# Wrapper used by cron. Sources lab credentials, runs one experiment, logs it.
# Every experiment is itself guarded: they refuse to run when the market is
# closed, they refuse the competition account, and they cap their own spend.
set -u
NAME="${1:?usage: run_experiment.sh <name> <YYYY-MM-DD|any> [args...]}"
ONDATE="${2:?second argument is the ET date to run on, or 'any'}"
shift 2

# cron entries outlive their purpose; this makes a stale entry harmless
TODAY_ET="$(TZ=America/New_York date +%Y-%m-%d)"
if [ "$ONDATE" != "any" ] && [ "$ONDATE" != "$TODAY_ET" ]; then
  mkdir -p "$HOME/midpoint/ops/logs"
  echo "[$(date -u +%Y%m%dT%H%M%SZ)] $NAME scheduled for $ONDATE, today is $TODAY_ET: skipping" \
    >> "$HOME/midpoint/ops/logs/cron.log"
  exit 0
fi
ROOT="$HOME/midpoint"
LOG="$ROOT/ops/logs"
mkdir -p "$LOG"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

if [ -f "$HOME/.config/midpoint/lab.env" ]; then
  set -a; . "$HOME/.config/midpoint/lab.env"; set +a
fi

# a global stop: touching this file halts every scheduled experiment
if [ -f "$ROOT/HALT" ]; then
  echo "[$STAMP] HALT present, skipping $NAME" >> "$LOG/cron.log"
  exit 0
fi

echo "[$STAMP] starting $NAME $*" >> "$LOG/cron.log"
/usr/bin/python3 "$ROOT/tools/$NAME.py" "$@" >> "$LOG/$NAME.$STAMP.log" 2>&1
RC=$?
echo "[$STAMP] $NAME exited $RC" >> "$LOG/cron.log"
exit $RC
