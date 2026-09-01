# A guided tour, for anyone with five minutes

Everything below runs on a stock Python install. No packages, no account, no
network — except where a command is explicitly marked as touching the market.

    ./tools/run_tests.sh

That single command runs the unit tests, ten thousand randomised cases against
the risk kernel's invariants, the arithmetic behind the refusal ledger, and
recomputes the published analyses from the recorded data.

---

## 1. The claim, in one number

A retail options trader lost **$347** in hidden cost on one contract. The same
trade, one strike away, cost **$7**. Nobody is required to show them either
figure: Rule 605 mandates execution-quality disclosure for *NMS stocks*, and
17 CFR 242.600 defines an NMS stock as "any NMS security other than an option."

The live walkthrough is at **[sahuongithub.github.io/midpoint](https://sahuongithub.github.io/midpoint/)**.
Everything on it was measured, and every figure can be recomputed from `results/`.

## 2. Why the numbers can be trusted

The free options feed does not publish the true national best bid and offer, so
the cost of a trade is normally unknowable to a retail trader. We found a way to
read it for nothing.

Alpaca's paper engine fills a marketable order at the true NBBO **regardless of
the limit price you send**. Six orders at one instant, limits spanning $1.00,
filled within $0.01 of each other — so every fill is an exact reading of the real
market, and the price of a contract can be measured rather than guessed.

    cat results/fill_oracle_result.json

That method is what makes the rest of the project possible, and it is the part
we have not seen anyone else do.

## 3. The thing we do that nobody else does

Every trading system logs the trades its risk layer stopped. Almost none price
them. The gap is structural — transaction-cost analysis is built from executed
orders, so it can only see trades that happened. Grinold and Kahn call the rest
censored data; Wagner found the cost of trades never made often dominates every
cost that *is* measured.

So each veto here carries the quotes, strikes and credit it refused, and is
settled afterwards against where the underlying actually closed. For a
defined-risk spread held to expiry that needs no model at all:

    P&L per share = credit - max(0, K_short - S_T) + max(0, K_long - S_T)

Run it:

    python3 tools/opportunity_cost.py --offline

Refusals that would have lost money are money the gates saved. Refusals that
would have made money are what the gates cost, reported with equal prominence.
The arithmetic is checked against hand calculations in
`tools/test_opportunity_cost.py`.

## 4. Two findings we published against our own interest

**Execution timing does not pay.** We built the harness to prove that waiting for
a better moment reduces cost. It does not: ten paired trials, +$0.15/contract,
t = +0.31, CI [−$0.82, +$1.12] — a window that excludes the published effect we
were testing for. Contract *selection* turned out to be worth roughly eighty
times more than timing, and that is what the product now leads with.

**The size-weighted mid does not beat the plain mid.** Standard practice in
equity microstructure, never checked on retail options quotes because checking
needs the true NBBO. We had 64 contracts where we had bought it. It loses
(RMSE $0.229 vs $0.141, paired p = 0.30), and on contracts our gate would trade
the two are indistinguishable.

    python3 tools/microprice_study.py

We also published a finding and withdrew it: quoted ask size looked like a strong
predictor of true spread at n=17 and died at n=48. The retraction is still on the
page.

## 5. What the risk layer actually guarantees

Fourteen pre-trade gates mapped clause by clause to **SEC Rule 15c3-5**, and a
second lineage in the ways derivatives desks have actually destroyed themselves.
The kernel may only veto or shrink a proposal — never enlarge it, never invent
one, never change its direction.

That is not a promise in a README. It is an invariant, checked against ten
thousand randomly generated proposals and account states:

    python3 tools/test_kernel_properties.py 10000

Eleven invariants, zero violations, and the run prints the decision mix it
reached so coverage is visible rather than asserted.

## 6. What we refuse to claim

That five sessions of P&L means anything. Skill expressed is `IR ≈ IC·√BR`; five
sessions of one strategy on one underlying is a breadth of about five, and the
standard error of an information ratio is `1/√years` — sixteen years to establish
merely top-quartile skill. A short window cannot show edge, so we put the breadth
where a week can carry it: into measurement.

That prediction was pre-registered and hashed before the first trade
(`PRE-REGISTRATION.md`, commit `bac24e3`), not written afterwards.

And every result is labelled the way the standards require. Paper trading is
itself a simulation, so **every figure this project produces is theoretical**,
and dry runs, paper orders and live money are reported separately and never
blended.

---

## Reproducing the live parts

These touch the market and require credentials.

    python3 tools/preflight.py           # 17 checks; refuses an unsafe start
    python3 tools/agent.py --cycles 1    # one dry cycle, places nothing
    python3 tools/size_ladder.py --dry-run   # what the size test would cost

`tools/audit_publish.py --verify` re-hashes the published decision chain; editing
any earlier record breaks every hash after it.
