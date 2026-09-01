#!/bin/zsh
# Store the judged account's API keys, without them touching the shell history
# or anything else that keeps a record.
#
#   ./ops/add_competition_keys.sh
#
# The secret is read with the terminal echo turned off, so it never appears on
# screen. Neither value is passed as an argument, so neither lands in
# ~/.zsh_history. The file is written readable only by you.
set -u
DEST="$HOME/.config/midpoint/competition.env"
EXPECT="${1:-PA32CGA2U1DY}"

echo "======================================================================"
echo "  Storing the judged account's keys"
echo "======================================================================"
echo "  Get them from alpaca.markets -> Paper Trading -> API Keys -> Generate."
echo "  Alpaca shows the secret once only, so copy both before continuing."
echo ""

if [ -f "$DEST" ]; then
  echo "  A file already exists at $DEST"
  printf "  Replace it? [y/N] "
  read REPLY
  case "$REPLY" in y|Y) ;; *) echo "  left alone."; exit 0 ;; esac
  echo ""
fi

printf "  API key ID   : "
read KEYID
printf "  Secret key   : "
stty -echo 2>/dev/null
read SECRET
stty echo 2>/dev/null
echo ""
echo ""

if [ -z "$KEYID" ] || [ -z "$SECRET" ]; then
  echo "  ! both values are required. Nothing written."
  exit 1
fi

mkdir -p "$(dirname "$DEST")"
umask 177
printf 'ALPACA_API_KEY=%s\nALPACA_SECRET_KEY=%s\n' "$KEYID" "$SECRET" > "$DEST"
chmod 600 "$DEST"
unset SECRET KEYID
echo "  written to $DEST  (readable only by you)"
echo ""

echo "  checking the keys work, and that they belong to the right account..."
ACCT=$( set -a; . "$DEST"; set +a
/usr/bin/python3 - <<'PYEOF' 2>/dev/null
import os, sys
sys.path.insert(0, os.path.expanduser("~/midpoint/tools"))
try:
    import alpaca_io as aio
    a = aio.account()
    print("%s|%s|%s" % (a["account_number"], a["equity"], a.get("status")))
except Exception as e:
    print("ERROR|%s|" % e)
PYEOF
)
NUM="${ACCT%%|*}"
REST="${ACCT#*|}"
EQ="${REST%%|*}"

if [ "$NUM" = "ERROR" ]; then
  rm -f "$DEST"          # never leave a file that failed its own check
  echo "  ! the keys did not work: $EQ"
  echo "    Nothing was saved. Check you copied both values completely and"
  echo "    that you generated them under Paper Trading, then run this again."
  exit 1
fi

echo "  keys work. Account $NUM, equity \$$EQ"
if [ "$NUM" != "$EXPECT" ]; then
  echo ""
  echo "  ! WARNING: that is NOT the account on your submission ($EXPECT)."
  echo "    You are probably signed in to a different paper account. Switch to"
  echo "    $EXPECT on alpaca.markets, generate keys there, and run this again."
  exit 1
fi

echo ""
echo "  Correct account. You are ready:"
echo "      ./ops/start_day.sh --competition"
