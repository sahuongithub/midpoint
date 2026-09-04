# Midpoint — one-page write-up

**Alpaca paper trading account: `PA32CGA2U1DY`**
Live application: <https://sahuongithub.github.io/midpoint/> · Repository: <https://github.com/sahuongithub/midpoint>

Retail options traders lose more to the spread than to being wrong. Bryzgalova, Pavlova and
Sikorskaya (*Journal of Finance*, 2023) put aggregate retail options losses at **$2.10bn** against
**$6.4bn of trading costs** over the same window. Equity trades come with a legally required
execution receipt; options do not, because 17 CFR 242.600 defines an NMS stock as *"any NMS
security other than an option"*, which puts every option outside Rule 605. Midpoint is that
missing receipt, plus an agent that trades to earn a good one.

---

## 1. Decision logic

The agent runs one loop, roughly every 70 seconds, and every stage can only ever say *no*.

**Regime.** `tools/regime.py` reads the VIX term structure from Yahoo, falling back to CBOE, and
computes the ratio VIX / VIX3M. Contango (ratio < 1.0) permits short premium; backwardation
stands the agent down entirely. This follows Johnson (*JFQA*, 2017), who found the **slope** of
the volatility term structure — not its level — carries the information about the variance risk
premium. We use a documented two-point proxy for his second principal component and say so; IV
rank appears nowhere in this project because level carries no such information.

**Structure.** `tools/structure.py` pulls the SPY option chain within ±10% of spot, selects the
short strike nearest 0.20 delta on the out-of-the-money side, and pairs it one strike further out
to cap the loss. Strikes already held are excluded before selection, because Alpaca infers
position intent from existing holdings and rejects a "buy to open" on a contract you are short.

**Management.** An open structure is closed when it has captured 50% of the credit received.
0DTE positions are flattened at 15:15 ET regardless — pin risk near expiry is not a position to
hold on hope (Hull, Business Snapshot 18.1). Nothing is carried overnight.

**There is no predictive model, and that is deliberate.** The agent claims no forecasting edge.
The regime signal can only withhold permission; it can never size a bet. Over five sessions a
skilled strategy and a lucky one are statistically indistinguishable — Grinold & Kahn put the
standard error of an information ratio at 1/√years, so establishing merely top-quartile skill
takes about **sixteen years** of returns. We therefore optimised the thing five sessions *can*
measure — execution cost — and report profit honestly rather than claiming it as evidence.

---

## 2. Risk gates

Fourteen gates in `tools/risk_kernel.py` sit between the strategy and the broker. Every proposal
passes through all of them in a fixed order; any single gate can veto, and three can shrink an
order instead of killing it. The kernel is pure — no network, no clock of its own — so all 14 are
covered by unit tests plus 12 invariants checked against 10,000 randomly generated states.

| # | Gate | What it refuses |
|---|---|---|
| 1 | `G0-account` | Any account this session was not explicitly authorised for |
| 2 | `G1-kill-switch` | Everything, while the `HALT` file exists |
| 3 | `G2-defined-risk` | Any structure without a long leg capping the loss |
| 4 | `G13-escalation` | Continuing after 25 consecutive **malfunction-class** rejections |
| 5 | `G4-daily-loss` | Opening once the session is down 1% of starting equity |
| 6 | `G5-drawdown` | Opening once equity is 2.5% below its peak |
| 7 | `G12-clock` | Opening 0DTE after 14:00 ET; anything after 15:50 ET |
| 8 | `G7-fat-finger` | More than 10 contracts, or notional above $15,000 |
| 9 | `G8-price-collar` | A limit more than 10% away from the structure's fair value |
| 10 | `G9-liquidity` | Quotes wider than $0.20, or without displayed size behind them |
| 11 | `G10-duplicate` | The same structure fingerprint inside 60 seconds |
| 12 | `G11-throttle` | More than 20 orders a minute |
| 13 | `G3-trade-size` | Risk above 0.25% of equity — shrinks the order, or refuses |
| 14 | `G6-aggregate-risk` | Total open defined risk above 1.5% of equity |

These map onto **SEC Rule 15c3-5**, the market-access rule: G3/G6/G7 are the credit and capital
thresholds of (c)(1)(i), G8/G9/G12 the erroneous-order controls of (c)(1)(ii), and G0/G1/G13 the
regulatory-access controls of (c)(2). G13 encodes the Knight Capital lesson directly — their
system sent 97 warnings before the open and nobody acted, losing $460m in 45 minutes.

**G13 distinguishes malfunction from market.** Refusals caused by the agent proposing something
it should not — wrong account, undefined risk, absurd size, duplicates — count toward the halt.
Refusals caused by an unattractive market — price collar, liquidity, size caps — reset the
counter. An earlier version counted both and duly halted a correctly-behaving agent on a quiet
day, which is exactly backwards: the quieter the market, the faster it would have stopped.

**The refusals are priced.** Every refusal is journalled with the full market snapshot behind it,
so `tools/opportunity_cost.py` can later settle each refused structure at expiry against the
actual close and compute what saying no was worth. Across **138 priced refusals: 49 would have
lost money (refusing saved $3,172), 88 would have made money (refusing cost $681), net +$2,491.**
Conventional TCA cannot do this — it is built from executed orders and is blind to trades never
made, the censoring problem Grinold & Kahn describe and Wagner argues often dominates.

---

## 3. Alpaca infrastructure

Everything runs against the Alpaca paper trading API. No backtest appears anywhere in this
project; every number came from a live account placing real orders.

| Endpoint | Used for |
|---|---|
| `/v2/options/contracts` | Chain discovery within ±10% of spot |
| `/v1beta1/options/snapshots`, `/quotes/latest` | Per-leg bid, ask, size, greeks |
| `/v2/stocks/{sym}/trades/latest`, `/bars` | Underlying spot and history |
| `/v2/orders` (`mleg`) | Atomic two-leg entry and exit |
| `/v2/positions`, `/v2/positions/{sym}` | Reconciliation and targeted flatten |
| `/v2/account`, `/v2/account/activities` | Equity, fills, P&L attribution |
| `/v2/clock` | Session state; never inferred from the local clock |

**Multi-leg orders are atomic.** Both legs go as a single `--order-class mleg` order, so a
vertical can never become half-filled and therefore never becomes an uncovered short. Alpaca
requires the short leg to be covered *within* the order, which the leg construction guarantees.

**The paper engine is used as a measurement instrument, not just a sandbox.** Probing six orders
with limits spanning $1.00 produced fills spanning $0.01 — the venue fills marketable orders at
the NBBO regardless of limit. That makes it a free ground-truth oracle, which is what let us
audit the free feed's own quoted widths and publish the result. A size ladder up to 196 contracts
(1.34× displayed size) filled at $1.27 against a $1.30 ask, so we state plainly that paper P&L is
earned where liquidity is free, and we do not claim it would survive real impact.

**Two accounts, never blended.** `PA32CGA2U1DY` is judged; `PA3TD7HMABNH` carries the research
probes. State, journals and attribution are all per-account, and the G0 gate refuses any account
a session was not authorised for. `tools/pnl_attribution.py` splits every fill by the order that
caused it, so a research probe that happens to land profitably is never reported as trading
performance — GIPS holds that theoretical results must be labelled and never linked with actual
performance, and paper trading is itself a simulation.

**Operations.** Sessions run under `ops/run_session.sh`, which runs a 17-check pre-flight, records
the agent's PID, and — whatever happens — verifies the account is flat on exit, journalling the
fills of any flatten it performs. The kill switch is a file the agent checks every cycle: present,
it stops opening but keeps managing what it already holds, because abandoning an open position is
not safety. Every decision, fill, refusal and correction is appended to a tamper-evident journal;
mistakes are corrected by appending a correction record, never by editing history.

---

## What we refuse to claim

That this beats the market. That five sessions of P&L means anything. That the regime proxy is
Johnson's PC2 rather than a two-point stand-in for it. That paper fills would survive real market
impact. The site publishes two findings that came out **null** — the microprice does not beat the
plain mid on this data (p = 0.30), and timing entries does not help below ~20 seconds — and one
result we **published and then withdrew** after finding our own error. Those are on the page for
the same reason the refusals are priced: a number is only worth something if the method that
produced it would also have reported a negative.
