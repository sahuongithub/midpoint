#!/usr/bin/env python3
"""
audit_publish.py -- publishes the agent's decisions to the public repository as it runs.

WHY
---
Everything this project claims about its risk layer is currently something we say.
"Fourteen gates." "The refusal log is the demo." A reader has to take our word for it,
and a log only the operator can produce is exactly what external-reporting standards
warn about: self-reported evidence weakens the thing it is meant to establish.

So the decisions go somewhere we do not control the timestamps. Git history published
to GitHub is attested by a third party, and each record carries the hash of the record
before it, so a retroactive edit to any earlier entry breaks every hash after it.

WHAT THIS PROVES, AND WHAT IT DOES NOT
--------------------------------------
It proves the sequence has not been silently altered, and that entries existed at the
commit times GitHub recorded. It does NOT prove we published everything -- an operator
could always withhold. We say so rather than overclaiming: this is tamper-EVIDENT, not
tamper-proof, and the distinction is the honest one.

ISOLATION
---------
This runs as a separate process from the agent and only ever READS the journals. A
network failure, a rejected push, or a git lock cannot stall or corrupt trading. Every
failure here is caught, logged and shrugged off.
"""

from __future__ import annotations

import argparse, hashlib, json, os, subprocess, sys, time
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUDIT = os.path.join(ROOT, "audit")
CHAIN = os.path.join(AUDIT, "log.jsonl")
DIGEST = os.path.join(AUDIT, "README.md")
SOURCES = [os.path.expanduser("~/midpoint/docs/agent.jsonl"),
           os.path.expanduser("~/midpoint/docs/risk_decisions.jsonl")]

GENESIS = "0" * 64


def _et(): return datetime.now(timezone.utc) - timedelta(hours=4)


def read_source(path, kind):
    out = []
    if not os.path.exists(path):
        return out
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue                       # tolerate a torn final line
        r["_kind"] = kind
        out.append(r)
    return out


def existing_chain():
    if not os.path.exists(CHAIN):
        return [], GENESIS, set()
    rows = []
    for line in open(CHAIN):
        line = line.strip()
        if line:
            try: rows.append(json.loads(line))
            except json.JSONDecodeError: pass
    head = rows[-1]["hash"] if rows else GENESIS
    seen = {r["fingerprint"] for r in rows}
    return rows, head, seen


def fingerprint(rec):
    """Identity of a source record, so republishing cannot duplicate it."""
    return hashlib.sha256(json.dumps(rec, sort_keys=True, default=str).encode()).hexdigest()[:24]


def link(prev_hash, payload):
    body = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256((prev_hash + body).encode()).hexdigest()


def summarise(r):
    """One human-readable line per decision."""
    e = r.get("event") or r.get("decision") or "?"
    if r["_kind"] == "risk":
        gate = r.get("gate") or "—"
        return ("%s · %s · proposed %s → **%s** %s · gate `%s`"
                % (r.get("ts", "")[11:19], r.get("strategy", "?"),
                   r.get("proposed_contracts", "?"), r.get("decision", "?"),
                   r.get("contracts", ""), gate))
    bits = []
    for k in ("reason", "gate", "coid", "credit", "required_win_rate", "contracts",
              "next_open", "ratio", "captured_frac", "error"):
        if r.get(k) not in (None, ""):
            bits.append("%s=%s" % (k, r[k]))
    return "%s · `%s` %s" % (r.get("ts", "")[11:19], e, " ".join(bits[:5]))


def build_digest(rows):
    total = len(rows)
    refusals = [r for r in rows if r["payload"].get("decision") == "REJECT"]
    opens = [r for r in rows if r["payload"].get("event") == "opened"]
    closes = [r for r in rows if r["payload"].get("event") == "closed"]
    gates = {}
    for r in refusals:
        g = r["payload"].get("gate") or "?"
        gates[g] = gates.get(g, 0) + 1

    L = []
    L.append("# Decision log\n")
    L.append("Every decision this agent made, published as it ran. Refusals included — "
             "especially refusals.\n")
    L.append("| | |\n|---|---|")
    L.append("| Records | **%d** |" % total)
    L.append("| Orders opened | **%d** |" % len(opens))
    L.append("| Positions closed | **%d** |" % len(closes))
    L.append("| Trades refused by the risk kernel | **%d** |" % len(refusals))
    L.append("| Chain head | `%s` |" % (rows[-1]["hash"][:16] if rows else "—"))
    L.append("| Last updated | %s ET |\n" % _et().strftime("%Y-%m-%d %H:%M:%S"))

    if gates:
        L.append("## Which gates fired\n")
        L.append("| Gate | Refusals |\n|---|---|")
        for g, n in sorted(gates.items(), key=lambda kv: -kv[1]):
            L.append("| `%s` | %d |" % (g, n))
        L.append("")

    L.append("## How to verify this\n")
    L.append("Each record carries the SHA-256 of the record before it, so editing any "
             "earlier entry breaks every hash after it. The commit timestamps are "
             "GitHub's, not ours.\n")
    L.append("```\npython3 tools/audit_publish.py --verify\n```\n")
    L.append("This is **tamper-evident, not tamper-proof**. It shows the sequence has not "
             "been silently altered. It cannot show that nothing was withheld — no "
             "self-published log can, and claiming otherwise would be dishonest.\n")

    L.append("## Most recent 40 decisions\n")
    for r in rows[-40:][::-1]:
        L.append("- " + summarise(dict(r["payload"], _kind=r["kind"])))
    return "\n".join(L) + "\n"


def verify():
    rows, _, _ = existing_chain()
    if not rows:
        print("no chain yet"); return 0
    prev = GENESIS
    for i, r in enumerate(rows):
        want = link(prev, r["payload"])
        if want != r["hash"]:
            print("BROKEN at record %d (%s)" % (i, r.get("fingerprint")))
            return 1
        prev = r["hash"]
    print("chain intact: %d records, head %s" % (len(rows), rows[-1]["hash"][:16]))
    return 0


def git(*args):
    """Never raise. A git problem must not become a trading problem."""
    try:
        p = subprocess.run(["git"] + list(args), cwd=ROOT, capture_output=True,
                           text=True, timeout=90)
        return p.returncode == 0, (p.stdout + p.stderr).strip()
    except Exception as e:
        return False, repr(e)


def publish(push=True, quiet=False):
    os.makedirs(AUDIT, exist_ok=True)
    rows, head, seen = existing_chain()

    fresh = []
    for path, kind in ((SOURCES[0], "agent"), (SOURCES[1], "risk")):
        for rec in read_source(path, kind):
            fp = fingerprint(rec)
            if fp not in seen:
                fresh.append((rec.get("ts", ""), kind, fp, rec))
                seen.add(fp)
    fresh.sort(key=lambda x: x[0])

    if not fresh:
        if not quiet: print("nothing new to publish")
        return 0

    with open(CHAIN, "a", buffering=1) as f:
        for ts, kind, fp, rec in fresh:
            payload = {k: v for k, v in rec.items() if k != "_kind"}
            head = link(head, payload)
            row = {"seq": len(rows) + 1, "ts": ts, "kind": kind,
                   "fingerprint": fp, "payload": payload, "hash": head}
            rows.append(row)
            f.write(json.dumps(row, default=str) + "\n")
            f.flush(); os.fsync(f.fileno())

    open(DIGEST, "w").write(build_digest(rows))

    refusals = sum(1 for _, k, _, r in fresh
                   if k == "risk" and r.get("decision") == "REJECT")
    msg = ("audit: %s ET — %d decision%s published%s"
           % (_et().strftime("%H:%M"), len(fresh), "" if len(fresh) == 1 else "s",
              ", %d refused" % refusals if refusals else ""))
    ok, out = git("add", "audit/log.jsonl", "audit/README.md")
    if ok:
        ok, out = git("commit", "-q", "-m", msg)
    committed = ok
    pushed = False
    if committed and push:
        pushed, out = git("push", "-q", "origin", "main")
    if not quiet:
        state = ("pushed" if pushed else
                 ("committed, not pushed" if committed else "NOT COMMITTED (%s)" % out[:80]))
        print("published %d record%s | head %s | %s"
              % (len(fresh), "" if len(fresh) == 1 else "s", head[:16], state))
    return len(fresh)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--watch", type=float, default=0, metavar="SECONDS",
                    help="publish continuously at this interval")
    ap.add_argument("--no-push", action="store_true")
    a = ap.parse_args()
    if a.verify:
        sys.exit(verify())
    if a.watch:
        print("watching every %.0fs — ctrl-c to stop" % a.watch)
        try:
            while True:
                try: publish(push=not a.no_push, quiet=True)
                except Exception as e: print("publish error (ignored): %r" % e)
                time.sleep(a.watch)
        except KeyboardInterrupt:
            print("\nstopped"); publish(push=not a.no_push)
    else:
        publish(push=not a.no_push)


if __name__ == "__main__":
    main()
