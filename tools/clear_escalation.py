#!/usr/bin/env python3
"""
clear_escalation.py -- release the escalation halt, and say why in the record.

The escalation gate halts the agent after a run of consecutive refusals, on the
reasoning that an agent being refused over and over is more likely broken than
unlucky. That is the Knight Capital lesson: their systems sent 97 warnings before
the open and nobody acted, so this one stops rather than warns.

Which means clearing it is a human decision about a fault that has been fixed,
not a routine reset -- and a fault that was diagnosed and repaired should leave a
record saying so. This writes that record into the same journal the agent writes,
so the audit chain carries the reason alongside the refusals it explains.

    python3 tools/clear_escalation.py --account PA32CGA2U1DY --reason "..."
"""
import json, os, sys
from datetime import datetime, timedelta, timezone


def main(argv):
    account, reason = None, None
    for i, a in enumerate(argv):
        if a == "--account" and i + 1 < len(argv):
            account = argv[i + 1]
        if a == "--reason" and i + 1 < len(argv):
            reason = argv[i + 1]
    if not account or not reason:
        print(__doc__)
        return 2

    state = os.path.expanduser("~/midpoint/docs/agent_state.%s.json" % account)
    journal = os.path.expanduser("~/midpoint/docs/agent.%s.jsonl" % account)
    if not os.path.exists(state):
        print("no state file for %s" % account)
        return 1

    s = json.load(open(state))
    was = s.get("consecutive_rejects", 0)
    if was == 0:
        print("nothing to clear: the streak is already zero")
        return 0

    s["consecutive_rejects"] = 0
    json.dump(s, open(state, "w"), indent=2)

    et = datetime.now(timezone.utc) - timedelta(hours=4)
    rec = {"ts": datetime.now(timezone.utc).isoformat(),
           "et": et.strftime("%H:%M:%S"),
           "event": "escalation_cleared",
           "dry_run": False,
           "account": account,
           "streak_cleared": was,
           "reason": reason,
           "note": ("a human released the escalation halt after diagnosing the "
                    "fault behind the refusals")}
    with open(journal, "a", buffering=1) as f:
        f.write(json.dumps(rec) + "\n")

    print("cleared a streak of %d refusals on %s" % (was, account))
    print("reason recorded in the journal: %s" % reason)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
