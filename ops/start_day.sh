#!/bin/zsh
# Start everything for one trading day, in one command.
#
#   ./ops/start_day.sh                  BOTH accounts: judged first, then research
#   ./ops/start_day.sh --competition    only the account the contest is judged on
#   ./ops/start_day.sh --research       only the research account
#
# Two accounts, deliberately kept apart. Research probes buy and sell purely to
# measure the venue, and a judged account carrying that traffic would have an
# equity curve nobody could read. So the agent's judged record runs on one
# account and every probe on the other, and the risk kernel is told which one
# this session may touch -- anything else is refused whatever its number.
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

MODE=""
for a in "$@"; do
  [ "$a" = "--competition" ] && MODE="competition"
  [ "$a" = "--research" ] && MODE="research"
done

# With no account named, do both -- judged first, so that if anything goes wrong
# it goes wrong on the run that matters least. Each is a separate invocation with
# its own guards, so one failing cannot stop the other.
if [ -z "$MODE" ]; then
  RC=0
  echo "  starting both accounts"
  echo ""
  echo "  --- judged account ---"
  "$0" --competition || RC=1
  echo ""
  echo "  --- research account ---"
  "$0" --research || RC=1
  exit $RC
fi

if [ "$MODE" = "competition" ]; then
  ENVFILE="$HOME/.config/midpoint/competition.env"
else
  ENVFILE="$HOME/.config/midpoint/lab.env"
fi

if [ ! -f "$ENVFILE" ]; then
  echo "  ! no credentials at $ENVFILE"
  if [ "$MODE" = "competition" ]; then
    echo "    See ops/SETUP-COMPETITION.md for how to create it."
  fi
  exit 1
fi
set -a; . "$ENVFILE"; set +a
echo "  mode:    $MODE   ($(basename $ENVFILE))"

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
EXPECT="${EXPECT_ACCOUNT:-}"
if [ -z "$EXPECT" ]; then
  EXPECT=$(/usr/bin/python3 -c "import os,sys; sys.path.insert(0,os.path.expanduser('~/midpoint/tools')); import alpaca_io as aio; print(aio.account()['account_number'])")
fi
echo "  account: $EXPECT"
if ! /usr/bin/python3 tools/preflight.py --expect "$EXPECT" \
      > "$LOG/preflight.$STAMP.log" 2>&1; then
  echo "  ! PRE-FLIGHT FAILED -- nothing started. Details:"
  tail -20 "$LOG/preflight.$STAMP.log" | sed 's/^/      /'
  exit 1
fi
echo "  pre-flight clear"

# --- 3. the agent --------------------------------------------------------
echo ""
# "is an agent running" is the wrong question when there are two accounts: both
# sessions have identical command lines and differ only by environment. Ask
# instead whether an agent is running FOR THIS ACCOUNT, tracked by a pid file.
PIDFILE="$ROOT/ops/run/agent.$EXPECT.pid"
mkdir -p "$ROOT/ops/run"
RUNNING="no"
if [ -f "$PIDFILE" ]; then
  OLDPID=$(cat "$PIDFILE" 2>/dev/null)
  if [ -n "$OLDPID" ] && kill -0 "$OLDPID" 2>/dev/null; then
    RUNNING="yes"
  else
    rm -f "$PIDFILE"      # stale: the process is gone
  fi
fi

if [ "$RUNNING" = "yes" ]; then
  echo "  agent already running for $EXPECT -- leaving it alone"
else
  MIDPOINT_ALLOWED_ACCOUNT="$EXPECT" MIDPOINT_ENV="$ENVFILE" \
    nohup ./ops/run_session.sh "$ET_DATE" --live --cycle-seconds 60 --until-et 16:00 \
    > "$LOG/session.$STAMP.log" 2>&1 &
  # run_session.sh writes the pid file itself, naming the python process rather
  # than this wrapper, so a later stop actually stops the agent
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
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "  agent        RUNNING for $EXPECT"
else
  echo "  agent        FAILED TO START -- see $LOG"
fi
pgrep -f "tools/spread_curve.py" > /dev/null \
  && echo "  spread curve RUNNING" || echo "  spread curve not running"
echo "  ----------------------------------------------------------------"
echo ""
echo "  Watch it:      python3 tools/watch.py"
echo "  Stop it all:   touch ~/midpoint/HALT"
echo "  Film between:  10:00 and 14:00 ET  (19:30-23:30 IST)"
if [ "$MODE" = "competition" ]; then
  echo ""
  echo "  This is the judged account. Research probes never run against it."
fi
