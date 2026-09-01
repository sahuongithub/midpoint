# Pre-registration

Everything below is fixed **before** the first order is placed on the competition
account. The commit that introduces this file is the reference; its hash appears in
the submission write-up.

The point is falsifiability. Option-strategy results are notoriously easy to flatter
after the fact — Duarte et al. (RFS 2026) show that ex-post filtering inflates option
Sharpe ratios from around 0.5 to over 5, and Look-Ahead-Bench finds in-sample gains of
+20.7% becoming −1.0% out of sample. A rule set that can be adjusted after seeing
results is not evidence of anything.

## Accounts

| Role | Account | Rule |
|---|---|---|
| Competition | `PA32CGA2U1DY` | strategy only; created 31 Aug 2026; $100,000; zero trades before the window |
| Laboratory | `PA3TD7HMABNH` | all experiments; never submitted for judging |

`peak_equity` for drawdown purposes anchors at **strategy start**, not account
inception. Research expenditure is not trading drawdown.

## Strategy

Defined-risk vertical spreads on SPY, 0–2 days to expiry, submitted as a single
atomic `mleg` order. Opened between 10:00 and 14:00 ET, closed by 15:15 ET,
**flat overnight without exception**.

Entry is gated on the volatility term-structure slope (VIX9D / VIX / VIX3M). Johnson
(JFQA) finds the level of implied volatility insignificant at every maturity while the
slope is significant at 1% at every maturity, with the variance premium changing sign
in the bottom slope quintile. IV rank is not used: it has no supporting literature.

## Risk limits

Frozen in `config/risk.json`. Summary:

| Control | Limit |
|---|---|
| Per-trade maximum loss | 0.25% of equity |
| Daily loss limit | 1.0% |
| Drawdown halt (flatten and stop) | 2.5% from strategy-start peak |
| Aggregate open defined risk | 1.5% |
| Liquidity gate — reject quoted width above | $0.20 |
| Fat finger — contracts / notional per order | 10 / $15,000 |
| Price collar around fair value | 10% |

These are deliberately tighter than a standalone trading account would use. Over a
window of four to seven sessions, P&L is dominated by noise — a strategy earning a
Sharpe of 0.8 produces roughly +0.1% against a five-session standard deviation of
about 0.9%. The upside is therefore capped by arithmetic, while a visible drawdown
would undercut the submission's central claim. When the payoff is asymmetric, size for
the downside. A $1-wide SPY 0–2DTE vertical also reaches maximum loss on roughly a
0.3% move in the underlying, which is an ordinary intraday event rather than a rare one.

## What we predict, and what would falsify it

1. **Execution timing does not pay on this platform.** Measured 31 Aug: paired-arm
   difference +$0.15 per contract, t = +0.31, 95% CI [−$0.82, +$1.12], which excludes
   the roughly $2.50 effect reported by Muravyev & Pearson. *Falsified if* a larger
   replication finds a significant negative difference.
2. **Contract selection dominates execution cost.** Liquid versus illiquid true
   half-spread measured at $2.80 against $88.79. *Falsified if* the $0.20 gate fails
   to separate cheap from expensive contracts out of sample.
3. **P&L over the window will not be statistically distinguishable from zero.** We
   expect roughly −4% to +6%. *Falsified if* the result is significant at n < 100
   trades — which would itself be evidence of a measurement error, not of skill.

## Standing rules

- Any option-strategy Sharpe above 2 is treated as a look-ahead-bias diagnostic, not
  an edge — including our own.
- Results are reported with confidence intervals, including when unflattering.
- Claims that fail replication are retracted in place rather than quietly removed.
  One already has been: quoted ask size looked like a strong predictor of true spread
  at n=17 (ρ = −0.897) and did not survive n=48 (ρ = −0.324).
