"""
executor.py -- the write path. The ONLY component that can move an order.

DESIGN
------
Multi-leg orders go through Alpaca's CLI as a single ATOMIC `mleg` order rather than
being legged in one side at a time. An earlier note in this project claimed the CLI
could not do multi-leg; that was wrong -- `--order-class mleg --legs '[...]'` works,
and `--side`/`--symbol` are documented as "required for all order classes except mleg".

Atomic is strictly safer than legging:
  * no leg risk -- there is no window where one side is filled and the other is not
  * Alpaca requires every short leg to be covered WITHIN the mleg order, so an
    uncovered short is structurally unrepresentable rather than merely forbidden

IDEMPOTENCY
-----------
Every order carries a deterministic client_order_id derived from
(strategy, legs, session date, sequence). A crashed and restarted agent that replays
the same intent produces the same id, and Alpaca rejects the duplicate. This is the
single control that stops a restart loop becoming a Knight Capital.

The risk kernel runs BEFORE anything here. The executor refuses to act on a rejected
decision -- it cannot be talked into a trade the kernel declined.
"""

from __future__ import annotations

import hashlib, json, os, shlex, subprocess, sys, time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from risk_kernel import Decision, REJECT

CLI = os.path.expanduser("~/midpoint/bin/alpaca")
JOURNAL = os.path.expanduser("~/midpoint/docs/executions.jsonl")


class ExecError(Exception):
    pass


@dataclass
class VerticalSpread:
    """A defined-risk two-leg structure. Width and credit fix the max loss."""
    short_symbol: str
    long_symbol: str
    width: float                 # strike distance, in dollars
    underlying: str = "SPY"
    strategy: str = "vertical"

    def legs(self, opening: bool) -> list:
        if opening:
            return [
                {"symbol": self.short_symbol, "side": "sell",
                 "ratio_qty": "1", "position_intent": "sell_to_open"},
                {"symbol": self.long_symbol, "side": "buy",
                 "ratio_qty": "1", "position_intent": "buy_to_open"},
            ]
        return [
            {"symbol": self.short_symbol, "side": "buy",
             "ratio_qty": "1", "position_intent": "buy_to_close"},
            {"symbol": self.long_symbol, "side": "sell",
             "ratio_qty": "1", "position_intent": "sell_to_close"},
        ]

    def max_loss_per_contract(self, credit: float) -> float:
        """Width minus credit received, per share. Never negative."""
        return max(0.0, self.width - abs(credit))


class Executor:
    def __init__(self, dry_run: bool = True, cli: str = CLI,
                 journal: str = JOURNAL, timeout: int = 30):
        self.dry_run, self.cli, self.timeout = dry_run, cli, timeout
        self.journal = os.path.expanduser(journal)
        os.makedirs(os.path.dirname(self.journal), exist_ok=True)
        if not os.path.exists(self.cli):
            raise ExecError("alpaca CLI not found at %s" % self.cli)

    # ------------------------------------------------------------------ plumbing

    def _run(self, args: list, retries: int = 3) -> dict:
        """Invoke the CLI. Retries only on transport failure, never on a rejection:
        a rejected order is an answer, and retrying it is how you double-fire."""
        cmd = [self.cli] + args
        last = None
        for attempt in range(retries + 1):
            try:
                p = subprocess.run(cmd, capture_output=True, text=True,
                                   timeout=self.timeout)
                out = (p.stdout or "").strip()
                if out.startswith("{") or out.startswith("["):
                    return json.loads(out)
                last = (p.returncode, out[:300], (p.stderr or "")[:300])
                if p.returncode == 0:
                    return {"raw": out}
                # a 4xx-style rejection is deterministic; do not retry it
                if "reject" in out.lower() or "insufficient" in out.lower():
                    raise ExecError("order rejected: %s" % out[:300])
            except subprocess.TimeoutExpired as e:
                last = ("timeout", str(e), "")
            if attempt < retries:
                time.sleep(0.8 * (2 ** attempt))
        raise ExecError("CLI failed after %d retries: %s" % (retries, last))

    def _log(self, event: str, **kw):
        rec = {"ts": datetime.now(timezone.utc).isoformat(), "event": event,
               "dry_run": self.dry_run}
        rec.update(kw)
        with open(self.journal, "a", buffering=1) as f:
            f.write(json.dumps(rec) + "\n")
        return rec

    @staticmethod
    def client_order_id(strategy: str, legs: list, seq: int,
                        session: str = None) -> str:
        """Deterministic. A restart that replays the same intent replays the same id,
        and Alpaca rejects the duplicate rather than filling it twice."""
        session = session or datetime.now(timezone.utc).strftime("%Y%m%d")
        payload = json.dumps({"s": strategy, "l": legs, "d": session, "n": seq},
                             sort_keys=True)
        return "mp-" + hashlib.sha256(payload.encode()).hexdigest()[:28]

    # -------------------------------------------------------------------- orders

    def submit_vertical(self, spread: VerticalSpread, qty: int, limit_price: float,
                        decision: Decision, seq: int, opening: bool = True) -> dict:
        if decision is not None and decision.action == REJECT:
            raise ExecError("risk kernel rejected this proposal (%s); executor will "
                            "not submit" % decision.gate)
        if decision is not None and qty != decision.contracts:
            raise ExecError("qty %d does not match the kernel's approved %d"
                            % (qty, decision.contracts))
        legs = spread.legs(opening=opening)
        coid = self.client_order_id(spread.strategy, legs, seq)
        args = ["order", "submit", "--order-class", "mleg", "--qty", str(qty),
                "--type", "limit", "--limit-price", "%.2f" % limit_price,
                "--time-in-force", "day", "--legs", json.dumps(legs),
                "--client-order-id", coid]
        if self.dry_run:
            args.append("--dry-run")
        self._log("submit", coid=coid, qty=qty, limit=limit_price,
                  opening=opening, legs=legs,
                  gate=(decision.gate if decision else None))
        res = self._run(args)
        self._log("submitted", coid=coid, response=res)
        return {"client_order_id": coid, "response": res}

    def wait_for_fill(self, coid: str, timeout_s: float = 60.0) -> Optional[dict]:
        if self.dry_run:
            return None
        t0 = time.time()
        while time.time() - t0 < timeout_s:
            try:
                o = self._run(["order", "get-by-client-id", coid])
            except ExecError:
                time.sleep(1.0); continue
            st = o.get("status")
            if st in ("filled", "canceled", "rejected", "expired"):
                self._log("terminal", coid=coid, status=st,
                          filled_avg_price=o.get("filled_avg_price"),
                          waited_s=round(time.time() - t0, 2))
                return o
            time.sleep(1.0)
        self._log("fill_timeout", coid=coid, waited_s=timeout_s)
        return None

    def close_vertical(self, spread: VerticalSpread, qty: int, limit_price: float,
                       seq: int) -> dict:
        """Close by submitting the mirrored mleg. Atomic, so the structure never
        becomes half-closed and therefore never becomes an uncovered short."""
        return self.submit_vertical(spread, qty, limit_price, None, seq, opening=False)

    # ------------------------------------------------------------ reconciliation

    def reconcile(self) -> dict:
        """Read authoritative state from the broker. Never trust local memory after
        a restart -- the broker is the source of truth."""
        positions = self._run(["position", "list"])
        orders = self._run(["order", "list", "--status", "open"])
        acct = self._run(["account", "get"])
        state = {"equity": float(acct.get("equity", 0)),
                 "positions": positions if isinstance(positions, list) else [],
                 "open_orders": orders if isinstance(orders, list) else []}
        self._log("reconcile", n_positions=len(state["positions"]),
                  n_open_orders=len(state["open_orders"]), equity=state["equity"])
        return state

    def flatten_all(self) -> dict:
        """Cancel orders first, then close positions. Order matters: a resting order
        can re-open a position you just closed."""
        if self.dry_run:
            return {"dry_run": True}
        try: self._run(["order", "cancel-all"])
        except ExecError: pass
        time.sleep(1.0)
        try: self._run(["position", "close-all"])
        except ExecError: pass
        time.sleep(1.5)
        st = self.reconcile()
        st["clean"] = not st["positions"] and not st["open_orders"]
        self._log("flatten_all", clean=st["clean"])
        return st
