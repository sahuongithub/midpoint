# Measurement results

Raw output from the experiments described in the top-level README. Every number
quoted there can be recomputed from these files.

| File | What it is |
|---|---|
| `fill_oracle_result.json` | Six simultaneous buy limits spanning $1.00 on one SPY 0DTE contract. Fills span $0.01 — establishing that the paper engine fills at the true NBBO regardless of limit price, which is what makes ground truth free. |
| `feed_audit.json` / `.csv` | 40 contracts, true NBBO recovered by buy-then-sell. Indicative midpoint approximately unbiased; spread error flips sign with liquidity. |
| `liquidity_gate.json` | 17 SPY contracts, stratified by moneyness and expiry. True cost per contract ranges −$1 to $347. |
| `liquidity_gate_run2.json` / `.csv` | Replication: 48 probed contracts across SPY, QQQ and AAPL, with a per-contract cost cap. Out-of-sample test of the $0.20 gate. |
| `paired_arm.json` | Ten paired trials testing execution timing. Null result: +$0.15/contract, t = +0.31. |

The high-frequency capture behind the fair-value study (240 samples × 8 contracts) is
excluded for size; the analysis that consumes it is in `tools/fv_analyze.py`.

Collected on live markets, 31 August 2026, against an Alpaca paper account used
exclusively for experiments and never submitted for judging.
