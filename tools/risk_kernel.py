"""
risk_kernel.py -- pre-trade risk controls for an autonomous options agent.

WHY IT LOOKS LIKE THIS
----------------------
The gates are not invented. They map to SEC Rule 15c3-5, the Market Access Rule,
which requires a broker-dealer with market access to maintain controls that:

  (c)(1)(i)   prevent orders exceeding pre-set credit or capital thresholds
  (c)(1)(ii)  prevent ERRONEOUS orders -- rejecting those that breach price or size
              parameters on an order-by-order basis or over a short period, or that
              appear duplicative
  (c)(1)(iii) ensure regulatory compliance before order entry
  (c)(2)      deliver immediate post-trade reports to surveillance

The distinction that shapes the design: 15c3-5's erroneous-order controls exist to
catch YOUR OWN SYSTEM MALFUNCTIONING, not the market moving. Retail risk management
is almost entirely about market risk. Knight Capital lost $460m in 45 minutes in 2012
to deprecated code on one of eight servers -- and the SEC charged them under this very
rule, finding their controls "not reasonably designed" because they had inventoried
existing controls rather than considered "possible malfunctions in its automated order
router." Their system also sent 97 warning emails before the open that nobody acted on.

So this kernel assumes the agent above it is the most likely thing to be broken, and
it escalates on repeated refusals rather than logging them forever in silence.

CONTRACT
--------
The kernel may only ever VETO or SHRINK a proposal. It can never enlarge one, change
its direction, or invent a trade. Every evaluation is logged with its reason.
"""

from __future__ import annotations

import json, math, os, time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Optional

PASS, SHRINK, REJECT = "PASS", "SHRINK", "REJECT"


# --------------------------------------------------------------------------- config

@dataclass
class RiskConfig:
    """Every threshold the kernel uses. Serialised into the pre-registration commit
    so the rules are frozen and hashed before the first live order."""
    # (c)(1)(i) capital thresholds
    max_trade_loss_frac: float = 0.005      # 0.5% of equity per trade
    daily_loss_limit_frac: float = 0.015    # 1.5% in a session -> stop opening
    max_drawdown_frac: float = 0.03         # 3% from peak -> flatten and halt
    max_open_risk_frac: float = 0.02        # 2% aggregate defined risk at once

    # (c)(1)(ii) erroneous-order controls
    max_contracts_per_order: int = 25       # fat finger
    max_notional_per_order: float = 25_000.0
    price_collar_frac: float = 0.10         # limit must be within 10% of fair value
    max_quoted_width: float = 0.20          # measured liquidity gate: $20/contract
    duplicate_window_s: float = 60.0        # identical structure inside this -> dup
    max_orders_per_min: int = 20            # message throttle
    max_orders_per_session: int = 400

    # (c)(1)(iii) platform and regulatory compliance
    require_defined_risk: bool = True
    require_long_leg_first: bool = True
    no_open_0dte_after_et: str = "14:00"
    flatten_expiring_by_et: str = "15:15"
    hard_close_et: str = "15:50"

    # operational (Knight lessons)
    kill_switch_path: str = "~/midpoint/HALT"
    max_consecutive_rejects: int = 25       # the "97 ignored emails" gate
    competition_account: str = "PA32CGA2U1DY"
    allowed_account: Optional[str] = None   # when set, ONLY this account may trade


@dataclass
class Leg:
    symbol: str
    side: str               # buy | sell
    qty: int
    is_long_wing: bool = False


@dataclass
class Proposal:
    strategy: str
    underlying: str
    legs: list
    limit_price: float          # net debit(+) / credit(-) per share
    max_loss_per_contract: float
    contracts: int
    fair_value: Optional[float] = None
    quoted_width: Optional[float] = None
    dte: int = 0
    fingerprint: str = ""
    snapshot: Optional[dict] = None     # market state at proposal time, so that a
                                        # refusal can be priced later (opportunity_cost.py)

    def notional(self) -> float:
        return abs(self.limit_price) * 100 * self.contracts


@dataclass
class AccountState:
    account_number: str
    equity: float
    peak_equity: float
    day_start_equity: float
    open_defined_risk: float = 0.0
    now_et: Optional[datetime] = None
    recent_orders: list = field(default_factory=list)   # (ts, fingerprint)
    orders_this_session: int = 0
    consecutive_rejects: int = 0


@dataclass
class Decision:
    action: str
    contracts: int
    reasons: list = field(default_factory=list)
    gate: Optional[str] = None

    def blocked(self) -> bool:
        return self.action == REJECT


# ----------------------------------------------------------------------- utilities

def _et_now() -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=4)


def _hhmm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


# -------------------------------------------------------------------------- kernel

class RiskKernel:
    """Deterministic. No model in the loop. Veto or shrink only."""

    def __init__(self, cfg: RiskConfig = None, journal_path: str = None):
        self.cfg = cfg or RiskConfig()
        self.journal_path = os.path.expanduser(
            journal_path or "~/midpoint/docs/risk_decisions.jsonl")
        os.makedirs(os.path.dirname(self.journal_path), exist_ok=True)

    # ---- the gates. each returns None to pass, or (gate_id, reason) to veto,
    #      or (gate_id, reason, new_contracts) to shrink.

    def g0_account(self, p, s):
        """Trade the account this session was authorised for, and no other.

        This began as a blocklist protecting the contest account from research
        spending. A blocklist is the weaker design: it stops one known mistake and
        permits every unknown one, so a session pointed at the wrong credentials by
        accident would have traded happily. When allowed_account is set the gate
        inverts into an allowlist -- the account must be the expected one, and
        anything else is refused whatever its number.
        """
        if self.cfg.allowed_account:
            if s.account_number != self.cfg.allowed_account:
                return ("G0-account",
                        "account %s is not the one this session is authorised for (%s)"
                        % (s.account_number, self.cfg.allowed_account))
            return None
        if s.account_number == self.cfg.competition_account and os.environ.get(
                "MIDPOINT_ALLOW_COMPETITION") != "yes":
            return ("G0-account", "competition account without explicit authorisation")

    def g1_kill_switch(self, p, s):
        if os.path.exists(os.path.expanduser(self.cfg.kill_switch_path)):
            return ("G1-kill-switch", "HALT file present")

    def g2_defined_risk(self, p, s):
        if not self.cfg.require_defined_risk:
            return None
        if p.max_loss_per_contract is None or p.max_loss_per_contract <= 0:
            return ("G2-defined-risk", "max loss per contract is not computable")
        shorts = [l for l in p.legs if l.side == "sell"]
        longs = [l for l in p.legs if l.side == "buy"]
        if shorts and not longs:
            return ("G2-defined-risk", "uncovered short: no long leg in the structure")
        if self.cfg.require_long_leg_first and shorts:
            if not p.legs[0].side == "buy":
                return ("G2-defined-risk",
                        "long protective leg must be sequenced first")

    def g3_trade_size(self, p, s):
        cap = self.cfg.max_trade_loss_frac * s.equity
        risk = p.max_loss_per_contract * 100 * p.contracts
        if risk <= cap:
            return None
        allowed = int(cap // (p.max_loss_per_contract * 100))
        if allowed < 1:
            return ("G3-trade-size",
                    "one contract risks $%.0f, above the $%.0f per-trade cap"
                    % (p.max_loss_per_contract * 100, cap))
        return ("G3-trade-size",
                "risk $%.0f exceeds cap $%.0f; shrunk %d -> %d contracts"
                % (risk, cap, p.contracts, allowed), allowed)

    def g4_daily_loss(self, p, s):
        loss = s.day_start_equity - s.equity
        if loss >= self.cfg.daily_loss_limit_frac * s.day_start_equity:
            return ("G4-daily-loss",
                    "session loss $%.0f at or beyond the %.1f%% limit"
                    % (loss, self.cfg.daily_loss_limit_frac * 100))

    def g5_drawdown(self, p, s):
        dd = (s.peak_equity - s.equity) / s.peak_equity if s.peak_equity else 0
        if dd >= self.cfg.max_drawdown_frac:
            return ("G5-drawdown",
                    "drawdown %.2f%% at or beyond the %.1f%% halt"
                    % (dd * 100, self.cfg.max_drawdown_frac * 100))

    def g6_aggregate_risk(self, p, s):
        add = p.max_loss_per_contract * 100 * p.contracts
        cap = self.cfg.max_open_risk_frac * s.equity
        if s.open_defined_risk + add > cap:
            room = cap - s.open_defined_risk
            if room <= 0:
                return ("G6-aggregate-risk", "no room under the aggregate risk cap")
            allowed = int(room // (p.max_loss_per_contract * 100))
            if allowed < 1:
                return ("G6-aggregate-risk", "aggregate cap leaves room for zero contracts")
            return ("G6-aggregate-risk",
                    "aggregate risk would reach $%.0f over cap $%.0f; shrunk to %d"
                    % (s.open_defined_risk + add, cap, allowed), allowed)

    def g7_fat_finger(self, p, s):
        if p.contracts > self.cfg.max_contracts_per_order:
            return ("G7-fat-finger", "%d contracts exceeds the %d per-order limit"
                    % (p.contracts, self.cfg.max_contracts_per_order),
                    self.cfg.max_contracts_per_order)
        if p.notional() > self.cfg.max_notional_per_order:
            return ("G7-fat-finger", "notional $%.0f exceeds the $%.0f per-order limit"
                    % (p.notional(), self.cfg.max_notional_per_order))

    def g8_price_collar(self, p, s):
        if p.fair_value is None or p.fair_value == 0:
            return None
        dev = abs(p.limit_price - p.fair_value) / abs(p.fair_value)
        if dev > self.cfg.price_collar_frac:
            return ("G8-price-collar",
                    "limit %.2f is %.1f%% from fair value %.2f, outside the %.0f%% collar"
                    % (p.limit_price, dev * 100, p.fair_value,
                       self.cfg.price_collar_frac * 100))

    def g9_liquidity(self, p, s):
        if p.quoted_width is None:
            return ("G9-liquidity", "no quote available to assess spread cost")
        if p.quoted_width > self.cfg.max_quoted_width:
            return ("G9-liquidity",
                    "quoted width $%.2f implies $%.0f/contract, above the $%.0f gate"
                    % (p.quoted_width, p.quoted_width * 100,
                       self.cfg.max_quoted_width * 100))

    def g10_duplicate(self, p, s):
        if not p.fingerprint:
            return None
        now = time.time()
        for ts, fp in s.recent_orders:
            if fp == p.fingerprint and now - ts < self.cfg.duplicate_window_s:
                return ("G10-duplicate",
                        "identical structure submitted %.0fs ago" % (now - ts))

    def g11_throttle(self, p, s):
        now = time.time()
        last_min = [1 for ts, _ in s.recent_orders if now - ts < 60]
        if len(last_min) >= self.cfg.max_orders_per_min:
            return ("G11-throttle", "%d orders in the last minute, at the limit"
                    % len(last_min))
        if s.orders_this_session >= self.cfg.max_orders_per_session:
            return ("G11-throttle", "session order cap reached")

    def g12_clock(self, p, s):
        now = s.now_et or _et_now()
        mins = now.hour * 60 + now.minute
        if mins >= _hhmm(self.cfg.hard_close_et):
            return ("G12-clock", "past the %s hard close" % self.cfg.hard_close_et)
        if p.dte == 0 and mins >= _hhmm(self.cfg.no_open_0dte_after_et):
            return ("G12-clock", "no new 0DTE positions after %s ET"
                    % self.cfg.no_open_0dte_after_et)

    def g13_escalation(self, p, s):
        """Knight's system sent 97 warnings before the open and nobody acted.
        Repeated refusals mean the agent above is malfunctioning, not unlucky."""
        if s.consecutive_rejects >= self.cfg.max_consecutive_rejects:
            return ("G13-escalation",
                    "%d consecutive rejections: halting rather than continuing to refuse"
                    % s.consecutive_rejects)

    GATES = ["g0_account", "g1_kill_switch", "g2_defined_risk", "g13_escalation",
             "g4_daily_loss", "g5_drawdown", "g12_clock", "g7_fat_finger",
             "g8_price_collar", "g9_liquidity", "g10_duplicate", "g11_throttle",
             "g3_trade_size", "g6_aggregate_risk"]

    def evaluate(self, p: Proposal, s: AccountState) -> Decision:
        contracts, reasons, shrunk_by = p.contracts, [], None
        for name in self.GATES:
            probe = Proposal(**{**asdict(p), "contracts": contracts,
                                "legs": p.legs})
            out = getattr(self, name)(probe, s)
            if out is None:
                continue
            if len(out) == 3:
                gate, reason, new_n = out
                reasons.append({"gate": gate, "reason": reason, "action": SHRINK})
                contracts, shrunk_by = new_n, gate
            else:
                gate, reason = out
                reasons.append({"gate": gate, "reason": reason, "action": REJECT})
                d = Decision(REJECT, 0, reasons, gate)
                self._log(p, s, d)
                return d
        if contracts < 1:
            d = Decision(REJECT, 0, reasons + [
                {"gate": "sizing", "reason": "shrunk below one contract",
                 "action": REJECT}], "sizing")
            self._log(p, s, d)
            return d
        d = Decision(SHRINK if contracts != p.contracts else PASS,
                     contracts, reasons, shrunk_by)
        self._log(p, s, d)
        return d

    def _log(self, p, s, d):
        """Every evaluation is journaled with enough market state to price the
        counterfactual afterwards.

        Standard transaction-cost analysis can only see orders that were sent.
        Grinold and Kahn call the rest censored data, and Wayne Wagner's studies
        found the opportunity cost of trades never made often dominates every cost
        that is measured. A refusal log that records only the reason cannot answer
        the one question worth asking of a risk system -- did the refusals help? --
        so we record the quotes too, and settle the answer at expiry.
        """
        rec = {"ts": datetime.now(timezone.utc).isoformat(),
               "strategy": p.strategy, "underlying": p.underlying,
               "proposed_contracts": p.contracts, "decision": d.action,
               "contracts": d.contracts, "gate": d.gate, "reasons": d.reasons,
               "equity": s.equity,
               "limit_price": p.limit_price,
               "max_loss_per_contract": p.max_loss_per_contract,
               "quoted_width": p.quoted_width, "fair_value": p.fair_value,
               "dte": p.dte, "fingerprint": p.fingerprint,
               "legs": [asdict(l) if not isinstance(l, dict) else l for l in p.legs],
               "snapshot": p.snapshot}
        with open(self.journal_path, "a", buffering=1) as f:
            f.write(json.dumps(rec) + "\n")

    def log_external_refusal(self, gate: str, reason: str, snapshot: dict,
                             equity: float = None, strategy: str = "",
                             underlying: str = "") -> None:
        """Journal a refusal raised BEFORE a Proposal exists.

        The measured liquidity gate runs inside structure building -- a spread whose
        legs are too wide is never proposed at all. Those refusals used to leave no
        trace, which would have quietly biased the refusal ledger toward the gates
        that happen to sit late in the pipeline. They are recorded here in the same
        journal, flagged with their stage.
        """
        rec = {"ts": datetime.now(timezone.utc).isoformat(),
               "strategy": strategy or (snapshot or {}).get("kind", "") + "-credit-vertical",
               "underlying": underlying or (snapshot or {}).get("underlying", ""),
               "proposed_contracts": (snapshot or {}).get("contracts_proposed", 0),
               "decision": REJECT, "contracts": 0, "gate": gate,
               "reasons": [{"gate": gate, "reason": reason, "action": REJECT}],
               "equity": equity, "stage": "pre-proposal",
               "limit_price": (snapshot or {}).get("credit_per_share"),
               "max_loss_per_contract": (snapshot or {}).get("max_loss_per_share"),
               "dte": (snapshot or {}).get("dte"),
               "snapshot": snapshot}
        with open(self.journal_path, "a", buffering=1) as f:
            f.write(json.dumps(rec) + "\n")
