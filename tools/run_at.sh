#!/bin/bash
# Wait until a target ET time, then run the near-close sweep.
# Heartbeats to stdout so a stalled or killed waiter is visible, and refuses to
# start anything within 5 minutes of the close.
TARGET=$((15*60+25))     # 15:25 ET
ABORT=$((15*60+50))
source ~/.config/midpoint/lab.env
et_min () { python3 -c "
from datetime import datetime,timezone,timedelta
et=datetime.now(timezone.utc)-timedelta(hours=4)
print(et.hour*60+et.minute)"; }
while true; do
  NOW=$(et_min)
  if [ "$NOW" -ge "$ABORT" ]; then echo "ABORT: $NOW >= $ABORT, too close to the bell"; exit 1; fi
  if [ "$NOW" -ge "$TARGET" ]; then break; fi
  echo "waiting... ET $((NOW/60)):$(printf %02d $((NOW%60)))  ($((TARGET-NOW)) min to go)"
  sleep 120
done
echo "=== firing near-close sweep ==="
python3 ~/midpoint/tools/liquidity_gate.py --yes --tag near-close --skip-0dte \
  --budget 500 --max-cell-cost 50 --out ~/midpoint/docs/liquidity_gate_run3
echo "=== sweep finished; verifying flat ==="
python3 - <<'PY'
import json,os,ssl,urllib.request,urllib.parse
H={"APCA-API-KEY-ID":os.environ["ALPACA_API_KEY"],"APCA-API-SECRET-KEY":os.environ["ALPACA_SECRET_KEY"]}
def rq(m,u):
    return json.loads(urllib.request.urlopen(urllib.request.Request(u,headers=H,method=m),
           timeout=20,context=ssl.create_default_context()).read() or b"{}")
pos=rq("GET","https://paper-api.alpaca.markets/v2/positions")
for p in pos:
    rq("DELETE","https://paper-api.alpaca.markets/v2/positions/"+urllib.parse.quote(p["symbol"]))
    print("flattened",p["symbol"])
print("final open positions:",len(rq("GET","https://paper-api.alpaca.markets/v2/positions")))
PY
