#!/usr/bin/env python3
"""
agent.py -- the autonomous loop. Ties signal, structure, risk kernel and executor
            together, and is deliberately the least clever component in the system.

WHAT IT DOES EACH CYCLE
-----------------------
    reconcile against the broker  ->  read the regime  ->  build a structure
      ->  submit it to the risk kernel  ->  execute only if the kernel allows
      ->  manage what is open  ->  journal everything, including the refusals

WHAT IT DOES NOT DO
-------------------
It does not forecast, size, or price anything. Sizing belongs to the kernel, pricing
to the structure builder, and the decision to trade at all to the regime gate. The
agent's only job is sequencing and bookkeeping -- which is exactly the job that goes
wrong at 3pm on a Friday when nobody is watching.

THREE PROPERTIES THAT MATTER MORE THAN THE STRATEGY
--------------------------------------------------
1. RECONCILE FIRST, ALWAYS. Local state is a cache; the broker is the truth. Every
   cycle begins by asking Alpaca what actually exists.
2. PEAK EQUITY ANCHORS AT STRATEGY START, not account inception. Conflating research
   spending with trading drawdown would halt the agent before it ever traded -- we hit
   exactly that in testing.
3. FLAT BY THE CUTOFF, unconditionally. Not "usually", not "if the position is
   losing". The clock gate outranks every other consideration including an open profit.

Honest note on what is being traded: a defined-risk credit vertical is fairly priced
under the risk-neutral measure. Whatever edge exists comes from the variance risk
premium -- implied exceeding realised -- and from not paying more than necessary to
get in and out. The agent does not claim edge; it claims discipline, and it records
the required win rate of every trade so the claim can be checked.
"""

from __future__ import annotations

import argparse, json, os, sys, time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import alpaca_io as aio
import signal as sig
from structure import build_vertical
from risk_kernel import RiskKernel, RiskConfig, AccountState, REJECT, SHRINK
from executor import Executor, VerticalSpread, ExecError

STATE_PATH = os.path.expanduser("~/midpoint/docs/agent_state.json")
JOURNAL = os.path.expanduser("~/midpoint/docs/agent.jsonl")


@dataclass
class AgentConfig:
    underlying: str = "SPY"
    min_dte: int = 0
    max_dte: int = 2
    kind: str = "put"
    target_short_delta: float = 0.20
    width_strikes: float = 1.0
    contracts: int = 1
    cycle_seconds: float = 60.0
    profit_target_frac: float = 0.50      # close at half the credit captured
    max_concurrent: int = 3
    open_window_et: tuple = ("10:00", "14:00")
    flatten_by_et: str = "15:15"


def _et_now():
    return datetime.now(timezone.utc) - timedelta(hours=4)


def _mins(s):
    h, m = s.split(":"); return int(h) * 60 + int(m)


class Agent:
    def __init__(self, cfg: AgentConfig, risk: RiskConfig,
                 dry_run: bool = True, state_path: str = STATE_PATH):
        self.cfg, self.dry_run = cfg, dry_run
        self.kernel = RiskKernel(risk)
        self.exec = Executor(dry_run=dry_run)
        self.state_path = os.path.expanduser(state_path)
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        self.s = self._load_state()

    # ------------------------------------------------------------------- state

    def _load_state(self):
        if os.path.exists(self.state_path):
            try: return json.load(open(self.state_path))
            except Exception: pass
        return {"strategy_start_equity": None, "peak_equity": None,
                "day_start_equity": None, "session_date": None,
                "orders_this_session": 0, "consecutive_rejects": 0,
                "recent_orders": [], "open_structures": [], "seq": 0}

    def _save_state(self):
        json.dump(self.s, open(self.state_path, "w"), indent=2)

    def _journal(self, event, **kw):
        rec = {"ts": datetime.now(timezone.utc).isoformat(),
               "et": _et_now().strftime("%H:%M:%S"), "event": event,
               "dry_run": self.dry_run}
        rec.update(kw)
        with open(JOURNAL, "a", buffering=1) as f:
            f.write(json.dumps(rec, default=str) + "\n")
        return rec


    # -------------------------------------------------------------- management

    def _price_to_close(self, st):
        """Cost to buy back a credit vertical, priced conservatively: pay the ask on
        the leg we are short, receive the bid on the leg we are long."""
        syms = "%s,%s" % (st["short"], st["long"])
        snaps = aio.req("GET", "%s/v1beta1/options/snapshots" % aio.DATA,
                        params={"symbols": syms, "feed": "indicative"}).get("snapshots") or {}
        sq = (snaps.get(st["short"]) or {}).get("latestQuote") or {}
        lq = (snaps.get(st["long"]) or {}).get("latestQuote") or {}
        sa, lb = float(sq.get("ap") or 0), float(lq.get("bp") or 0)
        if sa <= 0:
            return None
        return round(sa - lb, 4)

    def assignment_risk(self, st):
        """Flag a short leg whose extrinsic value has gone.

        Exchange-traded stock and ETF options are American: the holder of the long
        side may exercise at any time. Hull (ch. 10) shows why that is rarely
        rational while extrinsic value remains -- exercising throws that value away
        -- and why it becomes rational for a put once the option is deep enough in
        the money that the interest on the strike outweighs what is left. The
        practical trigger desks watch is the same quantity: extrinsic value near
        zero on an in-the-money short.

        This does not change what the agent does. The structure is defined-risk, the
        long leg still caps the loss, and assignment on a spread converts it into
        stock plus a hedge rather than an open-ended position. It is journalled
        because an agent that cannot see a risk cannot honestly claim to manage it,
        and because Alpaca's paper engine is a simulation: whether it models early
        assignment at all is a property of the venue, not of the market, and we
        would rather record the exposure than assume it away.
        """
        k = float(st.get("short_strike") or 0)
        if not k:
            return None                     # pre-upgrade record: nothing to measure
        try:
            snaps = aio.req("GET", "%s/v1beta1/options/snapshots" % aio.DATA,
                            params={"symbols": st["short"],
                                    "feed": "indicative"}).get("snapshots") or {}
            q = (snaps.get(st["short"]) or {}).get("latestQuote") or {}
            bid, ask = float(q.get("bp") or 0), float(q.get("ap") or 0)
            if bid <= 0 or ask <= 0:
                return None
            spot = float(aio.req("GET", "%s/v2/stocks/%s/trades/latest"
                                 % (aio.DATA, self.cfg.underlying),
                                 params={"feed": "iex"})["trade"]["p"])
            kind = st.get("kind", self.cfg.kind)
            intrinsic = max(0.0, k - spot) if kind == "put" else max(0.0, spot - k)
            mark = (bid + ask) / 2.0
            extrinsic = mark - intrinsic
            itm = intrinsic > 0
            if itm and extrinsic <= 0.05:
                return {"short": st["short"], "spot": round(spot, 2), "strike": k,
                        "intrinsic": round(intrinsic, 3),
                        "extrinsic": round(extrinsic, 3),
                        "note": ("short leg is in the money with %.3f extrinsic left; "
                                 "early assignment becomes rational for the holder "
                                 "around here" % extrinsic)}
        except Exception:
            return None
        return None

    def manage_positions(self, broker_positions):
        """Reconcile recorded structures against the broker, then close anything that
        has reached its profit target.

        State is a cache and drifts -- a structure can be closed by the flatten path,
        by assignment, or by a human. Anything not backed by a real position is dropped
        rather than trusted, because acting on a stale record is how an agent sells
        something it does not own.
        """
        held = {p["symbol"] for p in broker_positions}
        alive, closed = [], 0
        for st in self.s["open_structures"]:
            if not self.dry_run and st["short"] not in held:
                self._journal("structure_gone", short=st["short"],
                              note="no matching broker position; dropped from state")
                continue
            debit = self._price_to_close(st)
            risk = self.assignment_risk(st)
            if risk:
                self._journal("assignment_risk", **risk)
            if debit is None:
                alive.append(st); continue
            captured = st["credit"] - debit
            frac = captured / st["credit"] if st["credit"] else 0
            if frac >= self.cfg.profit_target_frac:
                self.s["seq"] += 1
                spread = VerticalSpread(short_symbol=st["short"], long_symbol=st["long"],
                                        width=st["width"], underlying=self.cfg.underlying)
                try:
                    self.exec.close_vertical(spread, st["contracts"], debit,
                                             seq=self.s["seq"])
                    self._journal("closed", short=st["short"], credit=st["credit"],
                                  debit=debit, captured=round(captured, 4),
                                  captured_frac=round(frac, 3),
                                  target=self.cfg.profit_target_frac)
                    closed += 1
                    continue
                except ExecError as e:
                    self._journal("close_failed", short=st["short"], error=str(e))
            else:
                self._journal("holding", short=st["short"], debit=debit,
                              captured_frac=round(frac, 3),
                              target=self.cfg.profit_target_frac)
            alive.append(st)
        self.s["open_structures"] = alive
        self._save_state()
        return closed

    # ------------------------------------------------------------------- cycle

    def reconcile(self):
        """The broker is the source of truth. Local state is only a cache."""
        st = self.exec.reconcile()
        eq = st["equity"]
        if not self.s.get("account_number"):
            try:
                self.s["account_number"] = self.exec._run(["account","get"])["account_number"]
            except Exception:
                self.s["account_number"] = "?"
        self._account_number = (st.get("account") or {}).get("account_number") \
            if isinstance(st.get("account"), dict) else self.s.get("account_number", "?")
        today = _et_now().date().isoformat()
        if self.s["strategy_start_equity"] is None:
            # anchor here, NOT at account inception -- research spend is not drawdown
            self.s["strategy_start_equity"] = eq
            self.s["peak_equity"] = eq
            self._journal("anchor", equity=eq,
                          note="peak anchored at strategy start, not account inception")
        if self.s["session_date"] != today:
            self.s.update({"session_date": today, "day_start_equity": eq,
                           "orders_this_session": 0, "consecutive_rejects": 0,
                           "recent_orders": []})
            self._journal("session_open", date=today, equity=eq)
        self.s["peak_equity"] = max(self.s["peak_equity"] or eq, eq)
        self._save_state()
        return st

    def account_state(self, equity, open_risk, account_number="?"):
        return AccountState(
            account_number=account_number,
            equity=equity, peak_equity=self.s["peak_equity"],
            day_start_equity=self.s["day_start_equity"], open_defined_risk=open_risk,
            now_et=_et_now(),
            recent_orders=[tuple(x) for x in self.s["recent_orders"]],
            orders_this_session=self.s["orders_this_session"],
            consecutive_rejects=self.s["consecutive_rejects"])

    def run_cycle(self):
        now = _et_now(); mins = now.hour * 60 + now.minute

        # Nothing below this is meaningful when the exchange is shut. Without it the
        # flatten gate fires all night, because 23:30 is trivially "past 15:15".
        clock = aio.req("GET", "%s/v2/clock" % aio.TRADING)
        if not clock.get("is_open"):
            self._journal("market_closed", next_open=clock.get("next_open"))
            return "market_closed"

        st = self.reconcile()
        equity, positions = st["equity"], st["positions"]

        if mins >= _mins(self.cfg.flatten_by_et):
            if positions or st["open_orders"]:
                self._journal("flatten", reason="past %s ET" % self.cfg.flatten_by_et,
                              n_positions=len(positions))
                self.exec.flatten_all()
            if self.s["open_structures"]:
                self.s["open_structures"] = []      # we just closed them; do not
                self._save_state()                  # report them missing next cycle
            return "flattened"

        # Manage what is open BEFORE anything else can return early. Standing down
        # is a decision not to OPEN; it is not a decision to stop looking after
        # what is already on. An earlier version returned here on a bad regime and
        # would have held a position straight through its profit target.
        if self.s["open_structures"]:
            n = self.manage_positions(positions)
            if n:
                st = self.exec.reconcile(); positions = st["positions"]

        regime = sig.read_regime()
        if not regime.short_premium_ok:
            self._journal("stand_down", reason=regime.explain(), ratio=regime.ratio)
            return "stand_down"

        # Count STRUCTURES, not legs. A vertical is two positions, so counting legs
        # made a limit of three concurrent spreads bind after the second one.
        n_open = len(self.s["open_structures"])
        if n_open >= self.cfg.max_concurrent:
            self._journal("at_capacity", n_structures=n_open, n_legs=len(positions))
            return "at_capacity"

        lo, hi = self.cfg.open_window_et
        if not (_mins(lo) <= mins < _mins(hi)):
            self._journal("outside_window", et=now.strftime("%H:%M"), window=self.cfg.open_window_et)
            return "outside_window"

        built = build_vertical(underlying=self.cfg.underlying, min_dte=self.cfg.min_dte,
                               max_dte=self.cfg.max_dte, kind=self.cfg.kind,
                               target_short_delta=self.cfg.target_short_delta,
                               width_strikes=self.cfg.width_strikes,
                               max_quoted_width=self.kernel.cfg.max_quoted_width,
                               contracts=self.cfg.contracts)
        if not built["ok"]:
            # a structure-stage refusal is still a refusal: journal it into the same
            # ledger the kernel writes, with the quotes attached, so that
            # opportunity_cost.py can price it alongside every other veto
            if built.get("snapshot"):
                self.kernel.log_external_refusal(
                    gate=built.get("gate", "structure"), reason=built["reason"],
                    snapshot=built["snapshot"], equity=equity,
                    underlying=self.cfg.underlying)
            self._journal("no_structure", reason=built["reason"],
                          gate=built.get("gate"))
            return "no_structure"

        p = built["proposal"]
        ml, cr = built["max_loss_per_contract"], built["credit"] * 100
        required_win = ml / (ml + cr) if (ml + cr) else 1.0

        open_risk = sum(x.get("max_loss", 0) for x in self.s["open_structures"])
        acct = self.account_state(equity, open_risk,
                                  account_number=self.s.get("account_number", "?"))
        d = self.kernel.evaluate(p, acct)

        self._journal("proposal", short=built["short"].symbol, long=built["long"].symbol,
                      expiry=built["expiry"], dte=built["dte"],
                      short_delta=built["short_delta"], credit=built["credit"],
                      max_loss_per_contract=ml, quoted_width=p.quoted_width,
                      required_win_rate=round(required_win, 4),
                      decision=d.action, contracts=d.contracts, gate=d.gate,
                      reasons=d.reasons)

        if d.action == REJECT:
            self.s["consecutive_rejects"] += 1; self._save_state()
            return "rejected:%s" % d.gate
        self.s["consecutive_rejects"] = 0

        spread = VerticalSpread(short_symbol=built["short"].symbol,
                                long_symbol=built["long"].symbol,
                                width=built["width"], underlying=self.cfg.underlying,
                                strategy=p.strategy)
        self.s["seq"] += 1
        try:
            r = self.exec.submit_vertical(spread, d.contracts, p.limit_price, d,
                                          seq=self.s["seq"], opening=True)
        except ExecError as e:
            self._journal("submit_failed", error=str(e)); self._save_state()
            return "submit_failed"

        self.s["orders_this_session"] += 1
        self.s["recent_orders"].append([time.time(), p.fingerprint])
        self.s["recent_orders"] = self.s["recent_orders"][-50:]
        if not self.dry_run:
            self.s["open_structures"].append(
                {"short": spread.short_symbol, "long": spread.long_symbol,
                 "width": spread.width, "credit": built["credit"],
                 "contracts": d.contracts, "max_loss": ml * d.contracts,
                 "short_strike": built["short"].strike,
                 "long_strike": built["long"].strike,
                 "kind": self.cfg.kind, "expiry": built["expiry"],
                 "coid": r["client_order_id"], "opened_et": now.isoformat()})
        self._save_state()
        self._journal("opened", coid=r["client_order_id"], contracts=d.contracts,
                      credit=built["credit"], required_win_rate=round(required_win, 4))
        return "opened"

    def run(self, cycles: int = None):
        self._journal("start", dry_run=self.dry_run, config=asdict(self.cfg))
        n = 0
        try:
            while cycles is None or n < cycles:
                try:
                    out = self.run_cycle()
                    print("  [%s] cycle %d -> %s" % (_et_now().strftime("%H:%M:%S"), n + 1, out))
                except Exception as e:
                    self._journal("cycle_error", error=repr(e))
                    print("  cycle error: %r" % e)
                n += 1
                if cycles is None or n < cycles:
                    time.sleep(self.cfg.cycle_seconds)
        except KeyboardInterrupt:
            print("\ninterrupted")
        finally:
            self._journal("stop", cycles=n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="actually place orders")
    ap.add_argument("--cycles", type=int, default=1)
    ap.add_argument("--cycle-seconds", type=float, default=60.0)
    ap.add_argument("--contracts", type=int, default=1)
    a = ap.parse_args()

    risk = RiskConfig(**json.load(open(os.path.expanduser("~/midpoint/config/risk.json"))))
    cfg = AgentConfig(cycle_seconds=a.cycle_seconds, contracts=a.contracts)
    agent = Agent(cfg, risk, dry_run=not a.live)
    print("Midpoint agent | %s | %s" % ("LIVE" if a.live else "DRY RUN",
                                        _et_now().strftime("%Y-%m-%d %H:%M ET")))
    agent.run(cycles=a.cycles)


if __name__ == "__main__":
    main()
