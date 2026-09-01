#!/bin/zsh
# Start both accounts for the day: the judged session and the research session.
# Each is guarded independently, so one failing never stops the other.
ROOT="$HOME/midpoint"
cd "$ROOT" || exit 1
echo "### judged account ###"
./ops/start_day.sh --competition
echo ""
echo "### research account ###"
./ops/start_day.sh
