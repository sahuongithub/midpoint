#!/usr/bin/env python3
"""
build_site.py -- assembles the public explorable from measured results.

The page is a SINGLE self-contained HTML file with its data embedded inline: no
fetch, no CORS, no backend, no build step at view time. It cannot fail on camera
because there is nothing to fail. Regenerate it by re-running this script whenever
new measurements land -- the page is never hand-edited.

    python3 tools/build_site.py   ->   docs/index.html
"""
import json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE, RESULTS, OUT = (os.path.join(ROOT, "site"), os.path.join(ROOT, "results"),
                      os.path.join(ROOT, "docs"))


def load(name):
    p = os.path.join(RESULTS, name)
    return json.load(open(p)) if os.path.exists(p) else None


def build_data():
    g1, g2 = load("liquidity_gate.json") or [], load("liquidity_gate_run2.json") or []
    contracts = []
    for r in g1 + g2:
        if r.get("true_width") is None:
            continue                      # skipped by the cost cap: no ground truth
        contracts.append({
            "c": r["contract"], "u": r["underlying"], "dte": r["dte_label"],
            "mny": round(r["moneyness"], 4), "indW": round(r["ind_width"], 4),
            "trueW": round(r["true_width"], 4), "cost": round(r["cost_usd"], 2),
            "mid": round(r["ind_mid"], 2), "oi": r.get("open_interest", 0),
        })
    oracle = [{"limit": p["limit"], "fill": p["fill"]}
              for p in (load("fill_oracle_result.json") or {}).get("probes", [])
              if p.get("fill")]
    pa = load("paired_arm.json") or {}
    paired = [{"a": t["A"]["cost"], "b": t["B"]["cost"],
               "diff": round(t["B"]["cost"] - t["A"]["cost"], 4)}
              for t in pa.get("trials", [])]
    # results that the appendix reports as numbers rather than as interactions.
    # Embedded so the page never quotes a figure that is not in the repository.
    mp = load("microprice_study.json") or {}
    micro = {"n": mp.get("n"),
             "rmse_mid": (mp.get("dollars") or {}).get("rmse_mid"),
             "rmse_micro": (mp.get("dollars") or {}).get("rmse_micro"),
             "p": (mp.get("paired_test") or {}).get("p"),
             "verdict": mp.get("verdict")} if mp else {}
    oc = load("opportunity_cost.json") or {}
    refusals = {"evaluations": oc.get("evaluations"), "refusals": oc.get("refusals"),
                "settled": oc.get("settled"), "pending": oc.get("pending"),
                "net": oc.get("net_effect_of_refusing_usd"),
                "saved": oc.get("saved_usd"), "cost": oc.get("cost_usd"),
                "by_gate": oc.get("by_gate", {})} if oc else {}
    sc = load("spread_curve_summary.json") or {}
    sl = load("size_ladder_summary.json") or {}
    ladder = {"rungs": sl.get("rungs"),
              "round_trips": sl.get("round_trips_per_share"),
              "all_free": sl.get("all_round_trips_free"),
              "max_multiple": sl.get("max_multiple_of_displayed"),
              "verdict": sl.get("verdict")} if sl else {}
    at = load("pnl_attribution.json") or {}
    attrib = {"agent": (at.get("agent") or {}).get("net_cash"),
              "research": (at.get("research") or {}).get("net_cash"),
              "broker": at.get("broker_day_pnl")} if at else {}
    return {"contracts": contracts, "oracle": oracle, "paired": paired,
            "summary": pa.get("summary", {}),
            "micro": micro, "refusals": refusals, "spread_curve": sc,
            "ladder": ladder, "attribution": attrib}


def main():
    data = build_data()
    if len(data["contracts"]) < 20:
        sys.exit("refusing to build: only %d measured contracts found"
                 % len(data["contracts"]))
    tpl = open(os.path.join(SITE, "template.html")).read()
    body = open(os.path.join(SITE, "body.html")).read()
    js = open(os.path.join(SITE, "app.js")).read()

    html = (tpl.replace("__BODY__", body)
               .replace("__DATA__", json.dumps(data, separators=(",", ":")))
               .replace("__SCRIPT__", js))

    os.makedirs(OUT, exist_ok=True)
    open(os.path.join(OUT, "index.html"), "w").write(html)
    open(os.path.join(OUT, ".nojekyll"), "w").close()   # or Pages mangles the file

    kb = len(html) / 1024
    print("built docs/index.html  %.0f KB" % kb)
    print("  %d contracts with ground truth" % len(data["contracts"]))
    print("  %d oracle probes, %d paired trials" % (len(data["oracle"]), len(data["paired"])))
    if data.get("micro", {}).get("n"):
        print("  microprice study: n=%d, mid %.4f vs micro %.4f"
              % (data["micro"]["n"], data["micro"]["rmse_mid"], data["micro"]["rmse_micro"]))
    r = data.get("refusals") or {}
    if r.get("refusals"):
        print("  refusal ledger: %d refusals, %d settled, net $%s"
              % (r["refusals"], r.get("settled") or 0, r.get("net")))
    if data.get("spread_curve", {}).get("samples"):
        print("  spread curve: %d samples" % data["spread_curve"]["samples"])
    if data.get("ladder", {}).get("rungs"):
        print("  size ladder: %d rungs, max %.2fx displayed depth"
              % (data["ladder"]["rungs"], data["ladder"]["max_multiple"] or 0))
    a = data.get("attribution") or {}
    if a.get("agent") is not None:
        print("  attribution: agent $%+.2f, research $%+.2f" % (a["agent"], a["research"]))
    costs = [c["cost"] for c in data["contracts"]]
    print("  cost range $%.0f to $%.0f" % (min(costs), max(costs)))
    if kb > 900:
        print("  ! page is large; consider trimming embedded data")


if __name__ == "__main__":
    main()
