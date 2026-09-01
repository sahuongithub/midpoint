#!/bin/zsh
# Start everything for one trading day, in one command.
#
#   ./ops/start_day.sh
#
# Safe to run twice: it refuses to start a second agent if one is already running.
# Everything it starts is detached, so closing the terminal does not kill it.
set -u
ROOT="$HOME/midpoint"
cd "$ROOT" || exit 1
LOG="$ROOT/ops/logs"; mkdir -p "$LOG"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
ET_DATE="$(TZ=America/New_York date +%Y-%m-%d)"
ET_TIME="$(TZ=America/New_York date +%H:%M)"

echo "======================================================================"
echo "  MIDPOINT -- starting the day    $ET_DATE  $ET_TIME ET"
echo "======================================================================"

if [ -f "$HOME/.config/midpoint/lab.env" ]; then
  set -a; . "$HOME/.config/midpoint/lab.env"; set +a
else
  echo "  ! no credentials at ~/.config/midpoint/lab.env"; exit 1
fi

if [ -f "$ROOT/HALT" ]; then
  echo "  ! the kill switch is pulled. Remove it first:  rm ~/midpoint/HALT"
  exit 1
fi

# --- 1. is the market open at all today? ---------------------------------
OPEN=$(/usr/bin/python3 - <<'PY'
import os, sys
sys.path.insert(0, os.path.expanduser("~/midpoint/tools"))
import alpaca_io as aio
c = aio.req("GET", "%s/v2/clock" % aio.TRADING)
print("open" if c.get("is_open") else "closed:%s" % c.get("next_open", "?"))
PY
)
case "$OPEN" in
  open) echo "  market is OPEN" ;;
  *)    echo "  market is closed. Next open: ${OPEN#closed:}"
        echo "  Start this again once it opens -- nothing has been launched."
        exit 0 ;;
esac

# --- 2. pre-flight -------------------------------------------------------
echo ""
echo "  running pre-flight..."
if ! /usr/bin/python3 tools/preflight.py --expect "${EXPECT_ACCOUNT:-PA3TD7HMABNH}" \
      > "$LOG/preflight.$STAMP.log" 2>&1; then
  echo "  ! PRE-FLIGHT FAILED -- nothing started. Details:"
  tail -20 "$LOG/preflight.$STAMP.log" | sed 's/^/      /'
  exit 1
fi
echo "  pre-flight clear"

# --- 3. the agent --------------------------------------------------------
echo ""
if pgrep -f "tools/agent.py --live" > /dev/null; then
  echo "  agent already running -- leaving it alone"
else
  nohup ./ops/run_session.sh "$ET_DATE" --live --cycle-seconds 60 --until-et 16:00 \
    > "$LOG/session.$STAMP.log" 2>&1 &
  echo "  agent started        -> trades until 16:00 ET, flattens at 15:15"
fi

# --- 4. the free quote poller -------------------------------------------
if pgrep -f "tools/spread_curve.py" > /dev/null; then
  echo "  spread curve already running"
else
  nohup ./ops/run_experiment.sh spread_curve "$ET_DATE" --interval 60 --until 16:05 \
    > "$LOG/spread.$STAMP.log" 2>&1 &
  echo "  spread curve started -> samples quotes every minute, costs nothing"
fi

sleep 8
echo ""
echo "  ----------------------------------------------------------------"
pgrep -f "tools/agent.py --live" > /dev/null \
  && echo "  agent        RUNNING" || echo "  agent        FAILED TO START -- see $LOG"
pgrep -f "tools/spread_curve.py" > /dev/null \
  && echo "  spread curve RUNNING" || echo "  spread curve not running"
echo "  ----------------------------------------------------------------"
echo ""
echo "  Watch it:      python3 tools/watch.py"
echo "  Stop it all:   touch ~/midpoint/HALT"
echo "  Film between:  10:00 and 14:00 ET  (19:30-23:30 IST)"
