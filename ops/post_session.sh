#!/bin/zsh
# Runs after the close: settle the day's refusals, analyse the spread curve,
# rebuild the page, publish the audit chain.
#
# It refuses to publish anything it cannot verify first. A pipeline that pushes
# unattended has to be more careful than one a human watches, not less.
set -u
ROOT="$HOME/midpoint"
cd "$ROOT" || exit 1
LOG="$ROOT/ops/logs"; mkdir -p "$LOG"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
say() { echo "[$STAMP] $*" | tee -a "$LOG/post_session.log"; }

if [ -f "$HOME/.config/midpoint/lab.env" ]; then
  set -a; . "$HOME/.config/midpoint/lab.env"; set +a
fi
[ -f "$ROOT/HALT" ] && { say "HALT present, skipping post-session"; exit 0; }

say "settling refusals against today's closes"
/usr/bin/python3 tools/opportunity_cost.py >> "$LOG/post_session.log" 2>&1

say "attributing profit and loss by cause"
/usr/bin/python3 tools/pnl_attribution.py >> "$LOG/post_session.log" 2>&1

say "analysing the size ladder"
/usr/bin/python3 tools/size_ladder_analyze.py >> "$LOG/post_session.log" 2>&1

say "analysing the intraday spread curve"
/usr/bin/python3 tools/spread_curve_analyze.py >> "$LOG/post_session.log" 2>&1

say "running the test suite before anything is published"
if ! ./tools/run_tests.sh >> "$LOG/post_session.log" 2>&1; then
  say "TESTS FAILED -- refusing to rebuild or publish"
  exit 1
fi

say "rebuilding the page"
if ! /usr/bin/python3 tools/build_site.py >> "$LOG/post_session.log" 2>&1; then
  say "build failed -- nothing published"
  exit 1
fi

# a built page that does not parse is worse than yesterday's page
if ! /usr/bin/python3 - <<'PY' >> "$LOG/post_session.log" 2>&1
import re, sys
h = open("docs/index.html").read()
bad = []
for t in ("section", "div", "table", "p"):
    o = len(re.findall(r"<%s[ >]" % t, h)); c = h.count("</%s>" % t)
    if o != c:
        bad.append("%s %d/%d" % (t, o, c))
if len(h) < 40000:
    bad.append("page suspiciously small: %d bytes" % len(h))
if "__DATA__" in h or "__BODY__" in h:
    bad.append("template placeholder left unreplaced")
print("validation:", "; ".join(bad) if bad else "clean")
sys.exit(1 if bad else 0)
PY
then
  say "built page failed validation -- nothing published"
  exit 1
fi

say "publishing the audit chain and the rebuilt page"
/usr/bin/python3 tools/audit_publish.py >> "$LOG/post_session.log" 2>&1

if [ -n "$(git status --porcelain docs/index.html results/ audit/ 2>/dev/null)" ]; then
  git add docs/index.html results/ audit/ 2>/dev/null
  git commit -q -m "Session results: refusals settled, page rebuilt" \
    && git push -q origin HEAD && say "published" || say "commit/push failed"
else
  say "nothing changed; nothing published"
fi
say "post-session complete"
