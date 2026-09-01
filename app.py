"""
Midpoint -- the execution-quality report card options traders never got.

Runs entirely on measured results committed to this repository. No credentials,
no live calls: every number shown was recorded against live markets and can be
recomputed from results/.
"""
import json, os
import pandas as pd
import streamlit as st

R = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
st.set_page_config(page_title="Midpoint", page_icon="◎", layout="wide")


@st.cache_data
def load(name):
    p = os.path.join(R, name)
    return json.load(open(p)) if os.path.exists(p) else None


gate1, gate2 = load("liquidity_gate.json"), load("liquidity_gate_run2.json")
audit, oracle, paired = load("feed_audit.json"), load("fill_oracle_result.json"), load("paired_arm.json")
probed = [r for r in ((gate1 or []) + (gate2 or [])) if r.get("true_width") is not None]

st.title("Midpoint")
st.markdown(
    "**Options traders lose more to the spread than to being wrong — and the cost of a "
    "trade is set when you pick the strike, not when you press the button.**")
st.caption(
    "Rule 605 requires execution-quality disclosure for NMS stocks. 17 CFR 242.600 defines "
    "an NMS stock as “any NMS security other than an option.” Equities got this in 2001. "
    "Options never did."
)

t1, t2, t3 = st.tabs(["1 · What will this cost you?",
                      "2 · The report card",
                      "3 · Skill or luck?"])

# ----------------------------------------------------------------- screen one
with t1:
    st.subheader("The strike you choose sets the bill")
    if probed:
        df = pd.DataFrame(probed)
        c1, c2, c3 = st.columns(3)
        c1.metric("Contracts measured", len(df))
        c2.metric("Cheapest", f"${df.cost_usd.min():,.0f}")
        c3.metric("Most expensive", f"${df.cost_usd.max():,.0f}",
                  delta=f"{df.cost_usd.max()/max(1,df.cost_usd.median()):.0f}× the median",
                  delta_color="inverse")

        st.markdown("##### The stock-replacement trap")
        st.markdown(
            "A deep in-the-money call is roughly a hundred shares of delta — traders use them "
            "as stock replacement. SPY's own equity spread was **3 cents**, so a hundred shares "
            "cost **\\$3**. The same exposure through options:")
        deep = df[df.moneyness < 0.98].nlargest(5, "cost_usd")
        if len(deep):
            show = deep[["contract", "dte_label", "cost_usd"]].copy()
            show["vs 100 shares"] = (show.cost_usd / 3.0).round(0).astype(int).astype(str) + "×"
            show.columns = ["Contract", "Expiry", "Spread cost ($)", "vs 100 shares"]
            st.dataframe(show, hide_index=True, use_container_width=True)

        st.markdown("##### Screen before you trade")
        thr = st.slider("Reject contracts quoted wider than ($)", 0.05, 1.00, 0.20, 0.05)
        p = df[df.ind_width <= thr]; f = df[df.ind_width > thr]
        a, b, c = st.columns(3)
        a.metric("Passes", len(p), f"mean ${p.cost_usd.mean():,.2f}" if len(p) else "—")
        b.metric("Rejected", len(f), f"mean ${f.cost_usd.mean():,.2f}" if len(f) else "—")
        c.metric("Traps over $50 let through", int((p.cost_usd > 50).sum()) if len(p) else 0)
        st.caption(
            "Absolute dollars, not a percentage: a one-cent spread on a three-cent option is "
            "33% in relative terms but only a dollar in real money. The $0.20 threshold was "
            "derived on 17 contracts and validated out of sample on 48 more.")
    else:
        st.info("Measurement data not found.")

# ----------------------------------------------------------------- screen two
with t2:
    st.subheader("What your fills actually cost")
    if oracle:
        st.markdown("##### Why these numbers are trustworthy")
        rows = [r for r in oracle["probes"] if r.get("fill")]
        if rows:
            o = pd.DataFrame(rows)[["limit", "fill", "delta_fill_minus_limit"]]
            o.columns = ["Limit offered ($)", "Actually paid ($)", "Difference"]
            st.dataframe(o, hide_index=True, use_container_width=True)
            st.caption(
                "Six orders sent at the same instant on the same contract. The limit varied by a "
                "dollar; the fill varied by a cent. The engine charges the true market price "
                "regardless of what you offer — which is what makes every fill an exact reading "
                "of the real quote, and ground truth free.")
    if audit:
        st.markdown("##### How wrong is the free data feed?")
        a = pd.DataFrame(audit)
        tight, mid = a[a.ind_width <= 0.10], a[(a.ind_width > 0.10) & (a.ind_width <= 0.60)]
        k1, k2, k3 = st.columns(3)
        k1.metric("Midpoint error (median)", f"${a.mid_err.median():+.3f}")
        k2.metric("Liquid contracts", f"{tight.ind_width.mean()/tight.true_width.mean():.2f}× true width")
        k3.metric("Mid-liquidity", f"{mid.ind_width.mean()/mid.true_width.mean():.2f}× true width")
        st.caption(
            "The midpoint is close to unbiased, but the **spread error flips sign with "
            "liquidity** — too tight on liquid contracts, too wide on mid-liquidity ones. "
            "It is not a correctable constant, which is why the true quote has to be measured "
            "rather than read.")
    if paired:
        s = paired["summary"]
        st.markdown("##### Does timing the trade help? We tested it. No.")
        m1, m2, m3 = st.columns(3)
        m1.metric("Cross immediately", f"${paired['summary']['mean_A']*100:+.2f}/contract")
        m2.metric("Wait for fair value", f"${paired['summary']['mean_B']*100:+.2f}/contract")
        m3.metric("Difference", f"${s['diff']*100:+.2f}", f"t = {s['t']:+.2f}", delta_color="off")
        st.caption(
            f"Paired trials on the same contract, differing only in when the order was sent "
            f"(n={s['n']}). The published effect this tests for would have needed only n=4 to "
            "detect. **Contract selection is roughly eighty times larger than timing** — which "
            "is why screen one exists and this one is a footnote.")

# --------------------------------------------------------------- screen three
with t3:
    st.subheader("Is the result real?")
    st.markdown(
        "A short-volatility book showing a beautiful number over a few days is the predicted "
        "output of the measurement error, not evidence of skill. Goetzmann, Ingersoll, Spiegel "
        "and Welch (RFS 2007) show a fund selling out-of-the-money options and holding cash has, "
        "whenever the options expire worthless, a zero standard deviation and a positive excess "
        "return — and therefore an infinite Sharpe ratio.")
    st.markdown("##### What we claim")
    st.markdown(
        "- Execution measured against ground truth, with confidence intervals\n"
        "- Risk enforced by gates mapped to SEC Rule 15c3-5, and tested\n"
        "- Every decision auditable, including every refusal\n"
        "- Findings replicated — and one claim retracted when replication killed it")
    st.markdown("##### What we do not claim")
    st.markdown(
        "- That the strategy has edge. A defined-risk credit vertical is fairly priced "
        "under the risk-neutral measure.\n"
        "- That a few sessions of P&L means anything. A strategy earning a Sharpe of 0.8 "
        "produces about **+0.1%** over five sessions against a standard deviation of about "
        "**0.9%** — a signal-to-noise ratio of roughly **0.11**.")
    st.info(
        "**Pre-registered.** Risk limits, strategy rules and three falsifiable predictions were "
        "committed before the first trade — including the prediction that P&L over the window "
        "will *not* be statistically distinguishable from zero. Commit "
        "`bac24e3e30513bf1f6acc552755466770acb31a7`.")
    st.markdown("##### The retraction, left standing on purpose")
    st.caption(
        "Quoted ask size looked like the second-best predictor of true spread at n=17 "
        "(ρ = −0.897). It did not survive replication at n=48 (ρ = −0.324). Only quoted width "
        "and option price hold up. A project about honest measurement should show its own "
        "claims being withdrawn.")

st.divider()
st.caption(
    "Paper trading only. Simulated results are hypothetical and do not represent actual trading. "
    "Options involve substantial risk and are not suitable for all investors. Nothing here is "
    "investment advice.")
