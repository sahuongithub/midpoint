# Decision log

Every decision this agent made, published as it ran. Refusals included — especially refusals.

> **All performance shown here is theoretical.** Paper trading is a simulation: orders are filled against real quotes, but with simulated money and without ever reaching an exchange. GIPS requires theoretical performance to be labelled as such and never linked with actual performance, so the tiers below are reported separately and are never combined into a single figure. **No real money has been traded at any point.**

## Activity by tier

| Tier | What it means | Records | Orders opened | Positions closed | Refused |
|---|---|---|---|---|---|
| **paper** | real order, real quote, simulated money | 284 | 5 | 5 | — |
| **simulated** | dry run — no order left the machine | 24 | 2 | 1 | — |
| **evaluation** | risk-kernel decision; no order implied | 160 | — | — | 139 |

Refusals are counted from the risk kernel's own records only. The agent journals the same decision from its side, and counting both would double every one.

## Which gates refused a trade

| Gate | Refusals |
|---|---|
| `G13-escalation` | 57 |
| `G8-price-collar` | 55 |
| `G3-trade-size` | 22 |
| `structure-no-credit` | 4 |
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
| Total records | 468 |
| Chain head | `51ef406ee3e203e3` |
| Last updated | 2026-09-01 16:12:33 ET |

## Most recent 40 decisions

- `[paper]` 20:00:59 · `stop` 
- `[paper]` 20:00:59 · `session_end` reason=reached 16:00 ET
- `[paper]` 19:16:19 · `flatten` reason=past 15:15 ET
- `[paper]` 19:15:11 · `outside_window` 
- `[paper]` 19:13:47 · `outside_window` 
- `[paper]` 19:12:22 · `outside_window` 
- `[paper]` 19:10:58 · `outside_window` 
- `[paper]` 19:09:47 · `outside_window` 
- `[paper]` 19:08:29 · `outside_window` 
- `[paper]` 19:06:55 · `outside_window` 
- `[paper]` 19:05:30 · `outside_window` 
- `[paper]` 19:03:26 · `outside_window` 
- `[paper]` 19:00:46 · `outside_window` 
- `[paper]` 18:59:22 · `outside_window` 
- `[paper]` 18:57:57 · `outside_window` 
- `[paper]` 18:56:50 · `outside_window` 
- `[paper]` 18:53:53 · `outside_window` 
- `[paper]` 18:52:29 · `outside_window` 
- `[paper]` 18:51:07 · `outside_window` 
- `[paper]` 18:49:43 · `outside_window` 
- `[paper]` 18:48:29 · `outside_window` 
- `[paper]` 18:47:20 · `outside_window` 
- `[paper]` 18:45:00 · `outside_window` 
- `[paper]` 18:43:29 · `outside_window` 
- `[paper]` 18:42:05 · `outside_window` 
- `[paper]` 18:40:57 · `outside_window` 
- `[paper]` 18:35:29 · `outside_window` 
- `[paper]` 18:31:08 · `outside_window` 
- `[paper]` 18:29:33 · `outside_window` 
- `[paper]` 18:28:08 · `outside_window` 
- `[paper]` 18:27:04 · `outside_window` 
- `[paper]` 18:21:17 · `outside_window` 
- `[paper]` 18:19:25 · `outside_window` 
- `[paper]` 18:17:50 · `outside_window` 
- `[paper]` 18:16:23 · `outside_window` 
- `[paper]` 18:14:58 · `outside_window` 
- `[paper]` 18:13:34 · `outside_window` 
- `[paper]` 18:12:27 · `outside_window` 
- `[paper]` 18:11:21 · `outside_window` 
- `[paper]` 18:09:57 · `outside_window` 
