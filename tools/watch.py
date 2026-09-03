#!/usr/bin/env python3
"""
watch.py -- a live view of what the agent is deciding, right now.

It OBSERVES. It never trades, never starts an agent, never touches an account.
It re-reads the journals the running agent is writing and renders them, so you
can watch a session -- or film one -- without a second agent fighting the first
for the same account.

    python3 tools/watch.py

Every line it shows was written by the agent or the risk kernel as it happened.
Nothing here is reconstructed or prettified after the fact.
"""
import json, os, sys, time
from datetime import datetime, timedelta, timezone

# The books are per-account. Reading the unscoped journal here showed a stale
# session from a previous day while the header claimed LIVE -- see books.py.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import books
AGENT = books.journal()
RISK = books.risk_decisions()
HALT = books.HALT

C = dict(r="\033[0m", b="\033[1m", dim="\033[2m",
         red="\033[38;5;203m", grn="\033[38;5;114m", yel="\033[38;5;179m",
         blu="\033[38;5;110m", mag="\033[38;5;176m", gry="\033[38;5;245m")
if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
    C = {k: "" for k in C}

# how each journal event should read on screen, and in what colour
EVENTS = {
    "start":            (C["blu"], "session started"),
    "session_open":     (C["blu"], "new session"),
    "anchor":           (C["gry"], "peak equity anchored at strategy start"),
    "market_closed":    (C["gry"], "market closed"),
    "outside_window":   (C["gry"], "outside the opening window"),
    "stand_down":       (C["yel"], "STANDING DOWN"),
    "halted":           (C["red"], "HALTED by kill switch"),
    "at_capacity":      (C["yel"], "at capacity"),
    "no_structure":     (C["yel"], "no tradeable structure"),
    "proposal":         (C["mag"], "proposal"),
    "opened":           (C["grn"], "OPENED"),
    "closed":           (C["grn"], "CLOSED at target"),
    "holding":          (C["gry"], "holding"),
    "flatten":          (C["red"], "FLATTENING"),
    "assignment_risk":  (C["red"], "assignment risk"),
    "structure_gone":   (C["yel"], "structure gone from broker"),
    "cycle_error":      (C["red"], "cycle error"),
    "close_failed":     (C["red"], "close failed"),
    "session_end":      (C["blu"], "session ended"),
    "stop":             (C["blu"], "stopped"),
}


def tail(path, n):
    if not os.path.exists(path):
        return []
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
    return out[-n:]


def et():
    return datetime.now(timezone.utc) - timedelta(hours=4)


def describe(r):
    ev = r.get("event", "?")
    col, label = EVENTS.get(ev, (C["gry"], ev))
    extra = ""
    if ev == "proposal":
        extra = "%s  credit $%.2f  gate %s" % (
            r.get("short", "?"), r.get("credit", 0) or 0,
            r.get("gate") or r.get("decision", ""))
        if r.get("decision") == "REJECT":
            col, label = C["red"], "REFUSED"
    elif ev == "opened":
        extra = "%d contract(s)  credit $%.2f" % (r.get("contracts", 0) or 0,
                                                  r.get("credit", 0) or 0)
    elif ev == "closed":
        extra = "kept %.0f%% of the credit" % ((r.get("captured_frac", 0) or 0) * 100)
    elif ev == "holding":
        extra = "%.0f%% of target" % ((r.get("captured_frac", 0) or 0) * 100)
    elif ev == "stand_down":
        extra = str(r.get("reason", ""))[:52]
    elif ev == "no_structure":
        extra = str(r.get("reason", ""))[:52]
    elif ev == "halted":
        extra = "opening stopped; managing still allowed"
    elif ev == "cycle_error":
        extra = str(r.get("error", ""))[:52]
    return col, label, extra


def since_last_start(rows):
    """Only the current session.

    The journal is append-only and keeps every run ever made, including dry-run
    rehearsals. A monitor that mixed those into today's session would be showing
    something that never happened today, so it starts from the most recent
    'start' record. Pass --all to see the whole history.
    """
    for i in range(len(rows) - 1, -1, -1):
        if rows[i].get("event") == "start":
            return rows[i:]
    return rows


def main():
    n = 14
    every = 2.0
    show_all = "--all" in sys.argv
    for i, a in enumerate(sys.argv):
        if a == "--lines" and i + 1 < len(sys.argv):
            n = int(sys.argv[i + 1])
    try:
        while True:
            agent = tail(AGENT, 800)
            if not show_all:
                agent = since_last_start(agent)
            risk = tail(RISK, 60)
            if not show_all and agent:
                t0 = agent[0].get("ts", "")
                risk = [r for r in risk if (r.get("ts") or "") >= t0]
            refusals = [r for r in risk if r.get("decision") == "REJECT"]
            opens = [r for r in agent if r.get("event") == "opened"]
            closes = [r for r in agent if r.get("event") == "closed"]

            halted = os.path.exists(HALT)
            last = agent[-1] if agent else {}
            live = last.get("dry_run") is False

            sys.stdout.write("\033[2J\033[H")
            bar = "=" * 74
            print("%s%s%s" % (C["b"], bar, C["r"]))
            print("%s  MIDPOINT AGENT  %s%s  %s ET%s"
                  % (C["b"], C["r"], C["dim"], et().strftime("%H:%M:%S"), C["r"]))
            print("%s%s%s" % (C["b"], bar, C["r"]))

            state = ("%sHALTED%s" % (C["red"] + C["b"], C["r"]) if halted else
                     ("%sLIVE%s" % (C["grn"], C["r"]) if live else
                      "%sdry run%s" % (C["gry"], C["r"])))
            print("  mode %-22s cycles %-6d opened %-4d closed %-4d refused %d"
                  % (state, len(agent), len(opens), len(closes), len(refusals)))
            if not show_all:
                print("  %sthis session only -- run with --all for the full history%s"
                      % (C["dim"], C["r"]))
            if halted:
                print("  %sthe kill switch is pulled: the agent will not open anything%s"
                      % (C["red"], C["r"]))
            print("%s%s%s" % (C["dim"], "-" * 74, C["r"]))

            for r in agent[-n:]:
                col, label, extra = describe(r)
                print("  %s%-8s%s %s%-26s%s %s%s"
                      % (C["dim"], r.get("et", "")[:8], C["r"], col, label, C["r"],
                         C["gry"], extra + C["r"]))

            if refusals:
                print("%s%s%s" % (C["dim"], "-" * 74, C["r"]))
                print("  %slast refusals, each priceable afterwards%s" % (C["dim"], C["r"]))
                for r in refusals[-3:]:
                    why = (r.get("reasons") or [{}])[0].get("reason", "")[:44]
                    print("    %s%-24s%s %s%s%s"
                          % (C["red"], r.get("gate", "?")[:24], C["r"], C["gry"], why, C["r"]))

            print("%s%s%s" % (C["dim"], "-" * 74, C["r"]))
            print("  %sobserving only -- this window never places an order."
                  "  Ctrl-C to stop.%s" % (C["dim"], C["r"]))
            sys.stdout.flush()
            time.sleep(every)
    except KeyboardInterrupt:
        print("\nstopped watching")
    return 0


if __name__ == "__main__":
    sys.exit(main())
