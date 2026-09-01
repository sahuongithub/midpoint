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

PERFORMANCE TIERS
-----------------
GIPS is explicit that theoretical performance -- "model, backtested, hypothetical,
simulated, indicative, ex ante, and forward-looking" -- must be clearly labelled and
must NOT be linked with actual performance. The SEC Marketing Rule adds that hypothetical
results require "sufficient information to enable the audience to understand the criteria
used and the assumptions made".

Applied honestly, that catches more than our dry runs. PAPER TRADING IS ALSO A SIMULATION.
Under GIPS every number this project produces is theoretical -- so the log separates:

    simulated  dry run; no order ever left the machine
    paper      a real order, filled against the real NBBO, with simulated money
    live       real money -- we never do this

and never blends them into one figure. The tier is DERIVED from each record rather than
stored inside the hashed payload, so anyone can recompute it and nobody can quietly
relabel a dry run as a trade.

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


MODES = ("simulated", "paper", "live")


def mode_of(payload, kind=None):
    """Derived, never stored in the hashed payload -- so it is independently checkable.

    Risk-kernel records are EVALUATIONS, not trades. A refusal is the absence of a
    trade, so it belongs to no performance tier at all; filing it under one would
    imply an order that never existed.
    """
    if kind == "risk" or "proposed_contracts" in payload:
        return "evaluation"
    d = payload.get("dry_run")
    if d is True:
        return "simulated"
    if d is False:
        return "paper"          # a real order against the real NBBO, with simulated money
    return "unclassified"


def prov(payload, kind):
    """W3C PROV shape: who acted, what they did, and on what."""
    if kind == "risk":
        return {"agent": "risk_kernel",
                "activity": payload.get("decision", "evaluate"),
                "entity": payload.get("strategy", "proposal")}
    return {"agent": "agent",
            "activity": payload.get("event", "?"),
            "entity": payload.get("coid") or payload.get("short") or payload.get("underlying", "-")}


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
    """Counts are reported PER TIER and never blended -- GIPS forbids linking theoretical
    performance with actual, and the same discipline applies within theoretical too."""
    by = {m: [] for m in list(MODES) + ["unclassified"]}
    for r in rows:
        by.setdefault(r.get("mode") or mode_of(r["payload"]), []).append(r)

    def count(bucket, pred):
        return sum(1 for r in bucket if pred(r["payload"]))

    L = []
    L.append("# Decision log\n")
    L.append("Every decision this agent made, published as it ran. Refusals included — "
             "especially refusals.\n")
    L.append("> **All performance shown here is theoretical.** Paper trading is a "
             "simulation: orders are filled against real quotes, but with simulated money "
             "and without ever reaching an exchange. GIPS requires theoretical performance "
             "to be labelled as such and never linked with actual performance, so the tiers "
             "below are reported separately and are never combined into a single figure. "
             "**No real money has been traded at any point.**\n")

    L.append("## Activity by tier\n")
    L.append("| Tier | What it means | Records | Orders opened | Positions closed | Refused |")
    L.append("|---|---|---|---|---|---|")
    labels = {
        "simulated": "dry run — no order left the machine",
        "paper": "real order, real quote, simulated money",
        "live": "real money — never used",
        "evaluation": "risk-kernel decision; no order implied",
        "unclassified": "tier could not be derived",
    }
    for m in ["paper", "simulated", "live", "evaluation", "unclassified"]:
        b = by.get(m) or []
        if not b and m in ("live", "unclassified"):
            continue
        opened = count(b, lambda p: p.get("event") == "opened")
        closed = count(b, lambda p: p.get("event") == "closed")
        # refusals are counted from the kernel's own records only; the agent journals
        # the same decision and counting both would double every one
        refused = count(b, lambda p: p.get("decision") == "REJECT") if m == "evaluation" else 0
        L.append("| **%s** | %s | %d | %s | %s | %s |" % (
            m, labels[m], len(b),
            opened if m in ("paper", "simulated", "live") else "—",
            closed if m in ("paper", "simulated", "live") else "—",
            refused if m == "evaluation" else "—"))
    L.append("")
    L.append("Refusals are counted from the risk kernel's own records only. The agent "
             "journals the same decision from its side, and counting both would double "
             "every one.\n")

    gates = {}
    for r in rows:
        if r["kind"] == "risk" and r["payload"].get("decision") == "REJECT":
            g = r["payload"].get("gate") or "?"
            gates[g] = gates.get(g, 0) + 1
    if gates:
        L.append("## Which gates refused a trade\n")
        L.append("| Gate | Refusals |\n|---|---|")
        for g, n in sorted(gates.items(), key=lambda kv: -kv[1]):
            L.append("| `%s` | %d |" % (g, n))
        L.append("")

    L.append("## Criteria and assumptions\n")
    L.append("The SEC Marketing Rule requires that hypothetical results carry enough "
             "detail for a reader to understand how they were produced. Ours:\n")
    L.append("- **Strategy** — defined-risk vertical spreads on SPY, 0–2 days to expiry, "
             "entered only while the volatility term structure is in contango, closed at "
             "50% of credit captured or by 15:15 ET, flat overnight without exception.")
    L.append("- **Sizing** — 0.25% of equity at risk per trade, 1.5% aggregate, halting at "
             "a 2.5% drawdown from the strategy's starting equity. Frozen in "
             "`config/risk.json` before the first trade.")
    L.append("- **Fills** — simulated by Alpaca against the real national best bid and "
             "offer. Order size is not checked against available quantity, so fills may be "
             "more favourable than a live market would allow.")
    L.append("- **Costs** — no commissions or fees are modelled; Alpaca does not charge "
             "them on paper accounts. The bid-ask spread *is* paid and is measured.")
    L.append("- **What this cannot show** — slippage under stress, partial-fill behaviour "
             "at size, assignment mechanics, or any effect of the orders on the market.\n")

    L.append("## How to verify this\n")
    L.append("Each record carries the SHA-256 of the record before it, so editing any "
             "earlier entry breaks every hash after it. The commit timestamps are "
             "GitHub's, not ours. The tier is derived from each record rather than stored "
             "in the hashed payload, so it can be recomputed independently.\n")
    L.append("```\ngit clone https://github.com/sahuongithub/midpoint\n"
             "python3 tools/audit_publish.py --verify\n```\n")
    L.append("This is **tamper-evident, not tamper-proof**. It shows the sequence has not "
             "been silently altered. It cannot show that nothing was withheld — no "
             "self-published log can, and claiming otherwise would be dishonest.\n")
    L.append("| | |\n|---|---|")
    L.append("| Total records | %d |" % len(rows))
    L.append("| Chain head | `%s` |" % (rows[-1]["hash"][:16] if rows else "—"))
    L.append("| Last updated | %s ET |\n" % _et().strftime("%Y-%m-%d %H:%M:%S"))

    L.append("## Most recent 40 decisions\n")
    for r in rows[-40:][::-1]:
        m = r.get("mode") or mode_of(r["payload"], r["kind"])
        L.append("- `[%s]` %s" % (m, summarise(dict(r["payload"], _kind=r["kind"]))))
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
                   "fingerprint": fp, "payload": payload, "hash": head,
                   "mode": mode_of(payload, kind), "prov": prov(payload, kind)}
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
