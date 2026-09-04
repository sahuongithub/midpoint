# Midpoint

### An execution-quality report card for options traders, and an autonomous agent that trades to earn a good one.

**Alpaca paper trading account — `PA32CGA2U1DY`**
Live app <https://sahuongithub.github.io/midpoint/> · Repo <https://github.com/sahuongithub/midpoint>

---

## The finding this is built on

Two real SPY put contracts, quoted seconds apart on the same afternoon, measured by placing
real orders and reading the fills:

| Contract | Bid | Ask | Cost to get in and out |
|---|---|---|---|
| `SPY260902P00751000` | 0.14 | 0.15 | **$1** |
| `SPY260910P00781000` | 13.95 | 17.01 | **$306** |

Nothing on a broker screen tells you which one you just clicked. If these were shares, the law
would force your broker to publish a receipt for it — but **17 CFR 242.600 defines an NMS stock as
*"any NMS security other than an option"***, which puts every option outside Rule 605. That single
clause is why no options execution-quality data exists to compare yourself against.

The money is not incidental. Bryzgalova, Pavlova and Sikorskaya (*Journal of Finance*, 2023) put
aggregate retail options **losses at $2.10bn** — against **$6.4bn of trading costs** in the same
window. Traders lose roughly three times more to the toll than to being wrong. Nobody sends them
the bill. **Midpoint is that bill**, built from 65 contracts we measured by paying the cost
ourselves, plus an agent that trades under the discipline the measurements imply.

---

## What is actually new here

1. **An options execution report card, because the law does not require one.** Not modelled —
   measured, on a live account, and turned into a tool anyone can use with two numbers off their
   own broker screen.
2. **The agent prices the trades it *refused*.** Every trading system on earth measures the trades
   it made. This one journals the full market snapshot behind every refusal, then settles each
   refused structure at expiry against the actual close. **138 refusals priced: 49 would have lost
   money (refusing saved $3,172), 88 would have made money (refusing cost $681) — net +$2,491.**
   Conventional TCA structurally cannot do this: it is built from executed orders and is blind to
   the trades never made. That is the censoring problem Grinold & Kahn describe and Wagner argues
   often dominates, and closing it is the original contribution here.
3. **We audited the venue our own results depend on.** A size ladder to 196 contracts — 1.34× the
   displayed size — filled at $1.27 against a $1.30 ask. So we state plainly that paper P&L is
   earned where liquidity is free, and we do not pretend it would survive real market impact.

---

## 1. Decision logic

One loop, roughly every 70 seconds. **Every stage can only ever say *no*.**

**Regime.** `tools/regime.py` reads the VIX term structure (Yahoo, falling back to CBOE) and takes
the ratio VIX / VIX3M. Contango permits short premium; backwardation stands the agent down
entirely. This follows Johnson (*JFQA*, 2017): the **slope** of the volatility term structure
carries the variance-risk-premium information, and the **level** carries none — which is why IV
rank appears nowhere in this project. We use a two-point proxy for his second principal component
and label it as a proxy rather than claiming we implemented PC2.

**Structure.** `tools/structure.py` pulls the SPY chain within ±10% of spot, takes the short strike
nearest 0.20 delta on the out-of-the-money side, and pairs it one strike further out so the loss is
capped by construction. Strikes already held are excluded *before* selection — Alpaca infers
position intent from existing holdings, so a "buy to open" on a contract you are short is rejected
outright.

**Management.** A structure is closed once it has captured 50% of the credit. 0DTE positions are
flattened at 15:15 ET regardless, because pin risk near expiry is not something to hold on hope
(Hull, Business Snapshot 18.1). Nothing is ever carried overnight.

**There is no predictive model, and that is the point.** The agent claims no forecasting edge. The
regime signal can withhold permission; it can never size a bet. We would rather show a judge
something we can prove than a prediction we cannot.

---

## 2. Risk gates

Fourteen gates in `tools/risk_kernel.py` sit between the strategy and the broker. Every proposal
passes all of them in fixed order; any one can veto, three can shrink an order instead of killing
it. The kernel is **pure** — no network, no clock of its own — which is what makes it exhaustively
testable.

| # | Gate | Refuses |
|---|---|---|
| 1 | `G0-account` | Any account this session was not explicitly authorised for |
| 2 | `G1-kill-switch` | Everything, while the `HALT` file exists |
| 3 | `G2-defined-risk` | Any structure without a long leg capping the loss |
| 4 | `G13-escalation` | Continuing after 25 consecutive **malfunction-class** rejections |
| 5 | `G4-daily-loss` | Opening once the session is down 1% of starting equity |
| 6 | `G5-drawdown` | Opening once equity is 2.5% below its peak |
| 7 | `G12-clock` | 0DTE after 14:00 ET; anything after 15:50 ET |
| 8 | `G7-fat-finger` | More than 10 contracts, or notional above $15,000 |
| 9 | `G8-price-collar` | A limit more than 10% from the structure's fair value |
| 10 | `G9-liquidity` | Quotes wider than $0.20, or with no displayed size behind them |
| 11 | `G10-duplicate` | The same structure fingerprint inside 60 seconds |
| 12 | `G11-throttle` | More than 20 orders a minute |
| 13 | `G3-trade-size` | Risk above 0.25% of equity — shrinks, or refuses |
| 14 | `G6-aggregate-risk` | Total open defined risk above 1.5% of equity |

**These are not invented.** They map onto **SEC Rule 15c3-5**, the market-access rule: G3/G6/G7 are
the credit and capital thresholds of (c)(1)(i); G8/G9/G12 the erroneous-order controls of
(c)(1)(ii); G0/G1/G13 the regulatory-access controls of (c)(2). G13 encodes Knight Capital
directly — their system sent **97 warnings** before the open and nobody acted; $460m in 45 minutes.

**G13 separates malfunction from market, and that distinction was earned.** An earlier version
counted every refusal toward the halt, and duly halted a correctly-behaving agent on a quiet day —
exactly backwards, since the quieter the market the faster it would stop. Now, refusals caused by
the agent proposing something it should not (wrong account, undefined risk, absurd size,
duplicates) escalate; refusals caused by an unattractive market (price collar, liquidity, size
caps) reset the counter.

In live running the gates have judged **651 proposals and refused 623 of them** — a 96% refusal
rate. That ratio *is* the product.

---

## 3. Alpaca infrastructure

Everything runs against the Alpaca paper API. **No backtest appears anywhere in this project.**
Every number came from a live account placing real orders.

| Endpoint | Used for |
|---|---|
| `/v2/options/contracts` | Chain discovery within ±10% of spot |
| `/v1beta1/options/snapshots`, `/quotes/latest` | Per-leg bid, ask, size, greeks |
| `/v2/stocks/{sym}/trades/latest`, `/bars` | Underlying spot and history |
| `/v2/orders` with `--order-class mleg` | **Atomic** two-leg entry and exit |
| `/v2/positions`, `/v2/positions/{sym}` | Reconciliation and targeted flatten |
| `/v2/account`, `/v2/account/activities` | Equity, fills, P&L attribution |
| `/v2/clock` | Session state — never inferred from the local clock |

**Multi-leg orders are atomic.** Both legs go as a single `mleg` order, so a vertical can never be
half-filled and therefore never becomes an uncovered short. Alpaca requires the short leg to be
covered *within* the order, which the leg construction guarantees.

**The paper engine is used as a measurement instrument, not just a sandbox.** Six probe orders with
limits spanning $1.00 produced fills spanning $0.01 — the venue fills marketable orders at the NBBO
regardless of limit. That makes it a free ground-truth oracle, and it is what let us audit the free
feed's own quoted widths against the truth and publish the result.

**Two accounts, never blended.** `PA32CGA2U1DY` is judged; `PA3TD7HMABNH` carries research probes.
State, journals and attribution are per-account, and G0 refuses any account a session was not
authorised for. `tools/pnl_attribution.py` splits every fill by the **order that caused it**, so a
research probe that happens to land profitably is never reported as trading performance. GIPS holds
that theoretical results must be labelled and never linked with actual performance — and paper
trading is itself a simulation.

**Operations.** Sessions run under `ops/run_session.sh`: a 17-check pre-flight, the agent's PID
recorded, and — whatever happens — the account verified flat on exit, with the fills of any flatten
journalled. The kill switch is a file checked every cycle: present, the agent stops *opening* but
keeps *managing* what it holds, because abandoning an open position is not safety. Every decision,
fill, refusal and correction is appended to a tamper-evident journal; mistakes are fixed by
appending a correction, never by editing history.

---

## 4. P&L — the number, and why it is small

**`PA32CGA2U1DY`: $100,013.02, up $13.02 since inception. Five trades opened across two sessions,
every one closed at the agent's own profit target. No losing round trip.**

That is a small number and we will not dress it up. Here is the arithmetic behind the choice:

> Over a handful of sessions, a genuinely skilled strategy and a lucky one produce numbers you
> cannot tell apart. Grinold & Kahn put the standard error of an information ratio at **1/√years**
> — establishing merely *top-quartile* skill takes about **sixteen years** of returns. A strategy
> with a Sharpe of 0.8 produces about **+0.1%** over five sessions against a standard deviation
> near **0.9%**: a signal-to-noise ratio of roughly **0.11**.

So a large five-day P&L is not evidence of skill. It is evidence of variance, and the entrant who
posts one cannot distinguish their own edge from their own luck.

**We optimised the thing five sessions genuinely can measure — cost — and report profit honestly.**
Every dollar of it is attributed by the order that caused it, split from research activity, with
every fill reconciled against the broker. On 3 September the account's books and the broker's
differed by $0.30; we traced it to twelve option legs at $0.025 each and **recorded the residual as
a residual** rather than absorbing it.

---

## 5. Evidence it works

| | |
|---|---|
| Live risk decisions journalled | **651**, of which **623 refused** |
| Refusals priced at expiry | **138** — net **+$2,491** for saying no |
| Contracts measured for the report card | **65**, by paying the cost |
| Quote observations recorded | **2,097** |
| Test files / kernel unit tests | **8** / **23** |
| Invariants checked against random states | **12 × 10,000** |
| Published data files, all recomputable | **19** |
| Python | **6,325** lines across **37** modules |

The risk kernel is pure, so its 12 invariants are checked against ten thousand randomly generated
account states on every run — including stratified generation so the cap invariants actually fire
rather than passing vacuously. Every published figure is regenerated from recorded data by
`./tools/run_tests.sh`, offline.

---

## What we refuse to claim

That this beats the market. That five sessions of P&L means anything. That our regime signal is
Johnson's PC2 rather than a two-point stand-in for it. That paper fills would survive real market
impact.

The site publishes **two findings that came out null** — the size-weighted microprice does not beat
the plain mid on this data (paired *p* = 0.30), and timing entries does not help below about 20
seconds — and **one result we published and then withdrew** after finding our own error.

Those are on the page for the same reason the refusals are priced: **a number is only worth
something if the method that produced it would also have reported a negative.**
