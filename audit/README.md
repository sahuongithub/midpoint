# Decision log

Every decision this agent made, published as it ran. Refusals included — especially refusals.

> **All performance shown here is theoretical.** Paper trading is a simulation: orders are filled against real quotes, but with simulated money and without ever reaching an exchange. GIPS requires theoretical performance to be labelled as such and never linked with actual performance, so the tiers below are reported separately and are never combined into a single figure. **No real money has been traded at any point.**

## Activity by tier

| Tier | What it means | Records | Orders opened | Positions closed | Refused |
|---|---|---|---|---|---|
| **paper** | real order, real quote, simulated money | 0 | 0 | 0 | — |
| **simulated** | dry run — no order left the machine | 21 | 2 | 1 | — |
| **evaluation** | risk-kernel decision; no order implied | 3 | — | — | 1 |

Refusals are counted from the risk kernel's own records only. The agent journals the same decision from its side, and counting both would double every one.

## Which gates refused a trade

| Gate | Refusals |
|---|---|
| `G10-duplicate` | 1 |

## Criteria and assumptions

The SEC Marketing Rule requires that hypothetical results carry enough detail for a reader to understand how they were produced. Ours:

- **Strategy** — defined-risk vertical spreads on SPY, 0–2 days to expiry, entered only while the volatility term structure is in contango, closed at 50% of credit captured or by 15:15 ET, flat overnight without exception.
- **Sizing** — 0.25% of equity at risk per trade, 1.5% aggregate, halting at a 2.5% drawdown from the strategy's starting equity. Frozen in `config/risk.json` before the first trade.
- **Fills** — simulated by Alpaca against the real national best bid and offer. Order size is not checked against available quantity, so fills may be more favourable than a live market would allow.
- **Costs** — no commissions or fees are modelled; Alpaca does not charge them on paper accounts. The bid-ask spread *is* paid and is measured.
- **What this cannot show** — slippage under stress, partial-fill behaviour at size, assignment mechanics, or any effect of the orders on the market.

## How to verify this

Each record carries the SHA-256 of the record before it, so editing any earlier entry breaks every hash after it. The commit timestamps are GitHub's, not ours. The tier is derived from each record rather than stored in the hashed payload, so it can be recomputed independently.

```
git clone https://github.com/sahuongithub/midpoint
python3 tools/audit_publish.py --verify
```

This is **tamper-evident, not tamper-proof**. It shows the sequence has not been silently altered. It cannot show that nothing was withheld — no self-published log can, and claiming otherwise would be dishonest.

| | |
|---|---|
| Total records | 24 |
| Chain head | `0c74705cc1bb35ec` |
| Last updated | 2026-09-01 04:37:33 ET |

## Most recent 40 decisions

- `[simulated]` 08:37:22 · `stop` 
- `[simulated]` 08:37:22 · `market_closed` next_open=2026-09-01T09:30:00-04:00
- `[simulated]` 08:37:21 · `start` 
- `[simulated]` 08:36:10 · `stop` 
- `[simulated]` 08:36:10 · `market_closed` next_open=2026-09-01T09:30:00-04:00
- `[simulated]` 08:36:09 · `start` 
- `[simulated]` 03:40:37 · `closed` credit=1.0 captured_frac=0.85
- `[simulated]` 03:40:36 · `holding` captured_frac=-0.154
- `[simulated]` 03:40:34 · `opened` coid=mp-c3e8cbf838d64ea72e252667119c credit=0.13 required_win_rate=0.87 contracts=1
- `[simulated]` 03:40:34 · `proposal` credit=0.13 required_win_rate=0.87 contracts=1
- `[evaluation]` 03:40:34 · put-credit-vertical · proposed 1 → **PASS** 1 · gate `—`
- `[simulated]` 03:40:27 · `session_open` 
- `[simulated]` 03:40:27 · `anchor` 
- `[simulated]` 03:32:22 · `proposal` gate=G10-duplicate credit=0.13 required_win_rate=0.87 contracts=0
- `[evaluation]` 03:32:22 · put-credit-vertical · proposed 1 → **REJECT** 0 · gate `G10-duplicate`
- `[simulated]` 03:32:13 · `opened` coid=mp-c3e8cbf838d64ea72e252667119c credit=0.13 required_win_rate=0.87 contracts=1
- `[simulated]` 03:32:13 · `proposal` credit=0.13 required_win_rate=0.87 contracts=1
- `[evaluation]` 03:32:13 · put-credit-vertical · proposed 1 → **PASS** 1 · gate `—`
- `[simulated]` 03:32:07 · `session_open` 
- `[simulated]` 03:32:07 · `anchor` 
- `[simulated]` 03:31:43 · `stop` 
- `[simulated]` 03:31:43 · `market_closed` next_open=2026-09-01T09:30:00-04:00
- `[simulated]` 03:31:40 · `market_closed` next_open=2026-09-01T09:30:00-04:00
- `[simulated]` 03:31:39 · `start` 
