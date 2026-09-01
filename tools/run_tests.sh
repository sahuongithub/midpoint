#!/bin/zsh
# Everything a reader needs to check the claims, in one command, with no install:
#   ./tools/run_tests.sh
# Only the standard library is used. Nothing here touches the network or an account.
set -u
cd "$(dirname "$0")/.."
FAIL=0
run() {
  printf "  %-34s" "$1"
  if OUT=$(/usr/bin/python3 "tools/$2" ${3:-} 2>&1); then
    printf "%s\n" "$(printf "%s" "$OUT" | tail -1 | sed 's/^ *//')"
  else
    printf "FAILED\n"; printf "%s\n" "$OUT" | tail -12 | sed 's/^/      /'
    FAIL=1
  fi
}
echo "======================================================================"
echo "  midpoint -- test suite"
echo "======================================================================"
run "statistics module"          test_statlib.py
run "risk kernel, unit"          test_risk_kernel.py
run "risk kernel, 10k random"    test_kernel_properties.py 10000
run "refusal pricing"            test_opportunity_cost.py
run "assignment-risk monitor"    test_assignment_risk.py
echo "----------------------------------------------------------------------"
echo "  analyses recomputed from the recorded data (no network):"
run "microprice vs mid"          microprice_study.py
run "refusal ledger"             opportunity_cost.py --offline
echo "======================================================================"
[ $FAIL -eq 0 ] && echo "  all green" || echo "  FAILURES ABOVE"
exit $FAIL
