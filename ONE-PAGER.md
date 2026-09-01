# Midpoint

**An execution-quality report card for options traders, and an autonomous agent that trades to earn a good one.**

Walkthrough → **sahuongithub.github.io/midpoint** · Code → **github.com/sahuongithub/midpoint**
Paper account → **PA32CGA2U1DY** · Rules pre-registered before the first trade → commit **`bac24e3`**

---

### The gap

Retail options traders lost **$2.10bn** between Nov 2019 and Jun 2021. Their *trading costs* over the same window were **$6.4bn**. Bryzgalova, Pavlova and Sikorskaya (*Journal of Finance*, 2023): *"the bulk of the losses comes from the indirect costs of trading."*

Nobody is required to show them that bill. Rule 605 mandates execution-quality disclosure for *NMS stocks*; 17 CFR 242.600 defines an NMS stock as **"any NMS security other than an option."** Equities got this in 2001. Options never did.

Building the receipt meant first solving measurement. Alpaca's paper engine fills a marketable order at the true NBBO regardless of your limit — six orders at one instant, limits spanning **$1.00**, fills within **$0.01**. Every fill is an exact reading of the real market, so ground truth costs nothing.

**What that measured, live:** across 65 contracts the true cost of a contract ran from **−$1 to $347**, and a deep in-the-money call — a hundred shares of delta — cost **116×** more in spread than buying the shares. Patient execution, the received wisdom, **doesn't pay here**: ten paired trials, **+$0.15/contract, t = +0.31**, CI **[−$0.82, +$1.12]**, excluding the effect we tested for — one that needed four pairs to detect.

**Contract selection is worth roughly eighty times more than execution timing.** That came from refuting our own hypothesis, and it is what the product now leads with.

### AI logic

The language model **cannot place a trade** — a capability guarantee, not an instruction. It connects to Alpaca's MCP server with the trading toolset never loaded, and we measured that boundary rather than asserting it: **72 tools unrestricted, 13 order-capable; 31 restricted, zero order-capable.**

Its job is what language is uniquely good for — reading news for binary catalysts — and it emits labels and verbatim quotes, **never numbers**. Every number acted on comes from deterministic Python: model-free implied variance, Black-Scholes greeks on an intraday clock, the term-structure gate. LLMs are unreliable at multivariate arithmetic, and the 2026 audit literature is full of agents that underperform buy-and-hold once frictions are counted.

The signal is Johnson's (JFQA 2017): the **shape** of the volatility term structure predicts straddle excess returns while the **level** predicts nothing — so IV rank appears nowhere here. It can only withhold permission, never size a bet.

### Risk gates

Fourteen pre-trade gates, **23 unit tests plus 10,000 randomised cases, no network required**, mapped clause by clause to **SEC Rule 15c3-5**: capital thresholds, erroneous orders by price/size/duplication/rate, pre-entry compliance, post-trade surveillance.

The clause that shaped the design is (c)(1)(ii). **Its erroneous-order controls exist to catch your own system malfunctioning, not the market moving.** Retail risk management is almost entirely market risk; professional pre-trade risk assumes your own code is the likeliest thing to be broken. Knight Capital lost $460m in 45 minutes to deprecated code on one of eight servers, was charged under this rule, and had fired 97 pre-open warnings nobody acted on. **So one gate has no market-risk purpose at all: after enough consecutive refusals the kernel halts, because an agent refused repeatedly is broken rather than unlucky.**

Two gates enforce our own measurements: a **$0.20 liquidity gate** — derived on 17 contracts, validated unchanged on 48 more, letting through **zero** traps above $50 in either — and a price collar against the validated fair-value model. The kernel may only veto or shrink, never enlarge: an invariant held across **10,000 randomised cases**. Every decision is journalled with its reason *and the quotes it refused*, then settled against where the underlying actually closed. Transaction-cost analysis built from executed orders is blind to this by construction — Grinold and Kahn call it censored data, and the cost of trades never made often dominates the costs that are measured. **We don't just log refusals. We price them.**

### Alpaca infrastructure implementation

**MCP is the read path and the security boundary; the CLI is the write path.** Spreads go in as one atomic `mleg` order, so no window exists with one leg filled and the other not — and since Alpaca requires every short leg covered within the order, an uncovered short is structurally unrepresentable. A deterministic `client_order_id` derived from intent means a restarted agent replays the same identifier and Alpaca rejects the duplicate.

Two pieces of quant infrastructure exist because the platform forces them: Alpaca serves **no index data**, so the term structure comes from the SPY chain, and returns **no greeks for same-day expiries**, so expiry day needs its own engine on an intraday clock. Our strongest fair-value result (**slope 0.89, t = 24.1, R² = 0.726** at 40s) came from exactly the contracts where the platform supplies nothing.

### What we do not claim

**That the strategy has an edge.** A defined-risk credit spread is fairly priced under the risk-neutral measure; whatever edge exists lives in the variance risk premium, and a short window cannot demonstrate it. The arithmetic isn't close: skill expressed is `IR ≈ IC·√BR`, five sessions give a breadth of about five, and `SE(IR) ≈ 1/√years` means **sixteen years to establish top-quartile skill.** So we put the breadth where a week can carry it: into measurement.

**That a few sessions of P&L means anything.** A Sharpe of 0.8 gives about **+0.1%** over five sessions against a standard deviation near **0.9%** — signal-to-noise ≈ **0.11**, pre-registered rather than discovered afterwards.

**And we label results the way the standards require.** GIPS holds that theoretical performance — model, backtested, hypothetical, **simulated** — must be labelled as such and never linked with actual performance. Applied honestly that catches more than our dry runs: **paper trading is itself a simulation**, so every figure here is theoretical. The decision log separates dry runs, paper orders and live money, and never blends them. No real money has been traded.

We also published a finding and withdrew it: quoted ask size looked like the second-best predictor of true spread at n=17 (ρ = −0.897) and died at n=48 (ρ = −0.324). A second null followed: the size-weighted mid does not beat the plain mid against true NBBO (n=64, p = 0.30). **Both are still on the page.**

*Paper trading only. Simulated results are hypothetical. Options involve substantial risk and are not suitable for all investors.*
