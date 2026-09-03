#!/usr/bin/env python3
"""
books.py -- one answer to the question "where are this account's books?"

Every file that records what an account did is named after that account: its
journal, its risk decisions, its state, its attribution. Two sessions on two
accounts sharing one file would overwrite each other's positions and anchor each
other's risk limits to the wrong equity.

This module exists because that rule was re-implemented separately in four places
and got it wrong in three of them. agent.py had its own copy and was correct.
pnl_attribution.py hardcoded the unscoped path, read an empty file on the judged
account, and reported the agent as having traded nothing all day. watch.py
hardcoded it too, and showed a stale session from the previous day while claiming
to be live -- which would have gone into a recorded demo. The limit ladder made the
matching mistake about flatten_all.

A rule duplicated is a rule that will disagree with itself. There is now one
definition, and everything asks it.
"""
import os

ROOT = os.path.expanduser("~/midpoint")
DOCS = os.path.join(ROOT, "docs")
RESULTS = os.path.join(ROOT, "results")

# The account these books belong to, or None when nothing is pinned. Callers should
# not read the environment themselves; that is how the copies drifted apart.
def account():
    return os.environ.get("MIDPOINT_ALLOWED_ACCOUNT") or None


def scoped(name, ext=".jsonl", where=None):
    """`agent` -> ~/midpoint/docs/agent.PA32CGA2U1DY.jsonl, or agent.jsonl unpinned."""
    acct = account()
    stem = "%s.%s" % (name, acct) if acct else name
    return os.path.join(where or DOCS, stem + ext)


def journal():
    """What the agent did: proposals, fills, refusals, corrections."""
    return scoped("agent", ".jsonl")


def risk_decisions():
    """What the risk kernel decided, with the market snapshot behind each call."""
    return scoped("risk_decisions", ".jsonl")


def state():
    """Open structures, the equity peak, the day's starting equity."""
    return scoped("agent_state", ".json")


def attribution():
    return scoped("pnl_attribution", ".json", where=RESULTS)


HALT = os.path.join(ROOT, "HALT")
