# Decision log

Every decision this agent made, published as it ran. Refusals included — especially refusals.

| | |
|---|---|
| Records | **18** |
| Orders opened | **2** |
| Positions closed | **1** |
| Trades refused by the risk kernel | **2** |
| Chain head | `b1c58cfc01e94f60` |
| Last updated | 2026-09-01 01:02:20 ET |

## Which gates fired

| Gate | Refusals |
|---|---|
| `G10-duplicate` | 2 |

## How to verify this

Each record carries the SHA-256 of the record before it, so editing any earlier entry breaks every hash after it. The commit timestamps are GitHub's, not ours.

```
python3 tools/audit_publish.py --verify
```

This is **tamper-evident, not tamper-proof**. It shows the sequence has not been silently altered. It cannot show that nothing was withheld — no self-published log can, and claiming otherwise would be dishonest.

## Most recent 40 decisions

- 03:40:37 · `closed` credit=1.0 captured_frac=0.85
- 03:40:36 · `holding` captured_frac=-0.154
- 03:40:34 · `opened` coid=mp-c3e8cbf838d64ea72e252667119c credit=0.13 required_win_rate=0.87 contracts=1
- 03:40:34 · `proposal` credit=0.13 required_win_rate=0.87 contracts=1
- 03:40:34 · put-credit-vertical · proposed 1 → **PASS** 1 · gate `—`
- 03:40:27 · `session_open` 
- 03:40:27 · `anchor` 
- 03:32:22 · `proposal` gate=G10-duplicate credit=0.13 required_win_rate=0.87 contracts=0
- 03:32:22 · put-credit-vertical · proposed 1 → **REJECT** 0 · gate `G10-duplicate`
- 03:32:13 · `opened` coid=mp-c3e8cbf838d64ea72e252667119c credit=0.13 required_win_rate=0.87 contracts=1
- 03:32:13 · `proposal` credit=0.13 required_win_rate=0.87 contracts=1
- 03:32:13 · put-credit-vertical · proposed 1 → **PASS** 1 · gate `—`
- 03:32:07 · `session_open` 
- 03:32:07 · `anchor` 
- 03:31:43 · `stop` 
- 03:31:43 · `market_closed` next_open=2026-09-01T09:30:00-04:00
- 03:31:40 · `market_closed` next_open=2026-09-01T09:30:00-04:00
- 03:31:39 · `start` 
