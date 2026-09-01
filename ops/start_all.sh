#!/bin/zsh
# Kept because the scheduler points here. Starting both accounts is now the
# default behaviour of start_day.sh, so this is a one-line forward -- editing the
# crontab on this machine hangs unpredictably, and a schedule pointing at a file
# that no longer exists fails silently, which is the worst way for it to fail.
exec "$HOME/midpoint/ops/start_day.sh" "$@"
