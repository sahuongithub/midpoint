# Midpoint

An execution-quality report card for options traders, and an autonomous agent that
trades to earn a good one.

## The problem

Retail options traders lose more to the spread than to being wrong. Bryzgalova,
Pavlova and Sikorskaya (*Journal of Finance*, 2023) put aggregate retail options
losses at $2.10bn over Nov 2019 – Jun 2021 — against **$6.4bn of trading costs** over
the same period. Their words: "the bulk of the losses comes from the indirect costs of
trading."

And nobody is required to show those costs. Rule 605 mandates execution-quality
disclosure — effective spread, price improvement, E/Q — for *NMS stocks*, and
17 CFR 242.600 defines an NMS stock as "any NMS security other than an option."
Equities got execution-quality disclosure in 2001. Options never did.

## See it

**[The explorable walkthrough →](https://sahuongithub.github.io/midpoint/)** — what a strike actually
costs, why the numbers are trustworthy, and the working behind them. Seven interactions over real
measured data; plain English on the surface, the full statistics in the appendix.

**[The agent's decision log →](audit/)** — every decision it made, published as it ran,
refusals included. Hash-chained, so editing any earlier entry breaks every hash after it.

## What this is

A measurement method, four findings, and an agent that acts on them.

**The method.** Alpaca's paper engine fills a marketable order at the true NBBO
independent of your limit price — verified here with limits spanning $1.00 producing
fills spanning $0.01. Every fill is therefore an exact reading of the real market,
which makes ground truth free.

**The findings**, all measured on live markets:

| | Result |
|---|---|
| Free feed accuracy | Midpoint approximately unbiased; **spread error flips sign with liquidity** — 0.67× true width on liquid contracts, 1.94× on mid-liquidity |
| Fair value from the underlying | Slope 0.89, t = 24.1, R² = 0.726 at a 40-second horizon on SPY 0DTE — and the signal only clears the feed's noise floor at ~20 seconds |
| Execution timing | **Null.** +$0.15/contract, t = +0.31, CI [−$0.82, +$1.12] — excluding the published ~$2.50 effect |
| Contract selection | **~80× larger than timing.** Deep-ITM calls cost 102–116× more in spread than buying the equivalent shares outright |

**The agent.** Defined-risk SPY verticals, atomic multi-leg, flat overnight, behind a
pre-trade risk kernel whose gates map to SEC Rule 15c3-5.

## Layout

    tools/pricing.py        Black-Scholes, implied vol, greeks on an intraday clock
                            (Alpaca returns none at 0DTE)
    tools/alpaca_io.py      resilient HTTP, append-only journal, verified flatten
    tools/risk_kernel.py    14 pre-trade gates, 23 tests
    tools/executor.py       the only component that can move an order
    tools/*_study.py        the experiments behind the findings above
    config/risk.json        frozen risk limits
    PRE-REGISTRATION.md     rules fixed before the first trade
    site/                   source for the public explorable
    tools/build_site.py     regenerates docs/index.html from results/
    app.py                  Streamlit operator view (live data during a session)

## Running the tests

    python3 tools/test_risk_kernel.py

No network required.

## Disclaimer

Paper trading only. Simulated results are hypothetical and do not represent actual
trading. Nothing here is investment advice.
