# Build in Public — 5 posts

Post these to X, then paste the 5 links into the submission form. Tag **@lablabai** and
**@AlpacaHQ** in every one. Post them a few minutes apart rather than all at once.

Links to use:
- App — https://sahuongithub.github.io/midpoint/
- Repo — https://github.com/sahuongithub/midpoint
- Write-up — https://github.com/sahuongithub/midpoint/blob/main/WRITEUP.md

---

## Post 1 — the finding (lead with this one)

> Two real SPY puts, quoted minutes apart.
>
> One costs $1 to get in and out of.
> The other costs $306.
>
> Nothing on your broker screen tells you which one you just clicked.
>
> So I built the receipt nobody sends you 👇
> https://sahuongithub.github.io/midpoint/
>
> @lablabai @AlpacaHQ

---

## Post 2 — the legal gap

> Why is there no options execution data?
>
> Rule 605 makes brokers publish execution quality — for "NMS stocks".
>
> 17 CFR 242.600 defines that as "any NMS security other than an option."
>
> One word. Every option, exempt.
>
> Built on @AlpacaHQ paper API for @lablabai

---

## Post 3 — the original idea

> Every trading system measures the trades it MADE.
>
> Mine also prices the trades it REFUSED — by settling each one at expiry against what
> actually happened.
>
> 138 refusals priced:
> · 49 would have lost money
> · saying no was worth +$2,491
>
> @lablabai @AlpacaHQ

---

## Post 4 — a setback, honestly (this is what "build in public" actually asks for)

> Debugging story.
>
> My live monitor said "LIVE" while showing yesterday's session. It was reading the
> wrong file.
>
> Found it 20 min before filming a demo where I'd have pointed at it and said "this is
> happening right now."
>
> Run it. Never trust it.
>
> @lablabai @AlpacaHQ

---

## Post 5 — the honest P&L

> My agent is up $13 on $100k. I'm not going to dress that up.
>
> Over 5 sessions a skilled strategy and a lucky one are statistically identical —
> establishing top-quartile skill takes ~16 years of returns.
>
> So I measured the thing 5 days CAN prove: cost.
>
> @lablabai @AlpacaHQ

---

## LinkedIn version (optional — one longer post)

> **Retail options traders lose more to the spread than to being wrong.**
>
> Bryzgalova, Pavlova and Sikorskaya (Journal of Finance, 2023) put aggregate retail
> options losses at $2.10bn — against $6.4bn of trading costs over the same period.
> Roughly three times more lost to the toll than to being wrong.
>
> If you trade shares, SEC Rule 605 forces your broker to publish a receipt for this.
> If you trade options, it doesn't — because 17 CFR 242.600 defines an NMS stock as
> "any NMS security other than an option."
>
> So for the Alpaca AI Trading Agents Hackathon I built Midpoint: an execution-quality
> report card for options traders, measured on 65 real contracts by paying the cost
> myself on a live Alpaca paper account, plus an autonomous agent that trades under
> the discipline those measurements imply.
>
> The part I'm proudest of isn't the agent's P&L — it's that the agent prices the
> trades it REFUSED. Every refusal is journalled with the market snapshot behind it,
> then settled at expiry against what actually happened. Across 138 refusals, saying
> no was worth +$2,491. Conventional transaction-cost analysis structurally can't do
> this: it's built from executed orders and is blind to the trades never made.
>
> Two findings on the site came out null, and one result I published and then withdrew
> after finding my own error. They're there for the same reason the refusals are
> priced: a number is only worth something if the method that produced it would also
> have reported a negative.
>
> App: https://sahuongithub.github.io/midpoint/
> Code: https://github.com/sahuongithub/midpoint
>
> #AlpacaHackathon @lablab.ai @Alpaca
