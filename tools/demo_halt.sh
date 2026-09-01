#!/bin/zsh
# Pull the kill switch, hold it for a few seconds so the effect is visible in the
# watch window, then release it -- and verify it is gone.
#
# A demo that leaves the halt file behind would silently stop the real session and
# everything scheduled after it, so this releases automatically and says so. Nothing
# here is simulated: the file is the same file the agent checks every cycle.
set -u
HOLD="${1:-90}"
HALT="$HOME/midpoint/HALT"

cleanup(){ rm -f "$HALT"; echo ""; echo "  kill switch RELEASED -- the agent may open again"; }
trap cleanup INT TERM EXIT

echo "  pulling the kill switch: $HALT"
date -u "+  %H:%M:%SZ  file created" | sed 's/^/ /'
: > "$HALT"
echo "  the agent's next cycle will refuse to open anything."
echo "  holding for ${HOLD}s, then releasing automatically."
i=0
while [ $i -lt "$HOLD" ]; do
  sleep 1; i=$((i+1))
  if [ $((i % 15)) -eq 0 ]; then echo "    held ${i}s of ${HOLD}s"; fi
done
