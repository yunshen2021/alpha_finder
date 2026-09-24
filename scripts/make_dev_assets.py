"""Reference numbers and figures for the dev-period report (no new trials are logged:
A/B/C are the same deterministic runs already in run_log.jsonl, re-run to extract details)."""
import json
import logging

import numpy as np
import pandas as pd

from alpha_finder.backtest.metrics import max_drawdown, monthly_returns
from alpha_finder.data.prices import load_prices
from alpha_finder.reporting.plots import drawdown_chart, growth_chart, ladder_chart
from alpha_finder.research import (
    DEV_END, REPORT_DIR, Market, benchmark_config, run_pair, to_jsonable, variants,
)

logging.basicConfig(level=logging.ERROR)


def cagr_monthly(s):
    return float((1 + s).prod() ** (12 / len(s)) - 1)


def main():
    m = Market.load()
    ranks = m.ranks(12, 1)
    bench_after, bench_pre = run_pair(m, benchmark_config(), ranks, None, DEV_END)
    runs = {k: run_pair(m, cfg, ranks, None, DEV_END) for k, cfg in variants().items()}

    # frictionless reference series over the same months
    px = m.adj.ffill(limit=5).reindex(m.month_ends)
    ew, top25 = {}, {}
    for j in range(len(m.month_ends) - 1):
        d, nxt = m.month_ends[j], m.month_ends[j + 1]
        if d not in ranks or d > DEV_END:
            continue
        r = ranks[d]
        fwd = (px.loc[nxt, r.index] / px.loc[d, r.index] - 1).dropna()
        r = r.loc[fwd.index]
        ew[nxt], top25[nxt] = fwd.mean(), fwd[r <= 25].mean()
    ew, top25 = pd.Series(ew), pd.Series(top25)
    spy = load_prices(["SPY"], "2010-01-01", "2026-09-23")["SPY"]["adj_close"]
    spy_m = spy.reindex(m.calendar).ffill().reindex(m.month_ends).pct_change().reindex(ew.index)

    ref = {
        "qqq_pre_tax_engine": bench_pre.cagr_after_tax,
        "qqq_after_tax": bench_after.cagr_after_tax,
        "spy_pre_tax": cagr_monthly(spy_m),
        "ew_universe_survivor_only": cagr_monthly(ew),
        "top25_frictionless": cagr_monthly(top25),
        "months": len(ew),
        "window": [str(ew.index[0].date()), str(DEV_END.date())],
    }
    for k, (after, pre) in runs.items():
        led = after.ledger
        ref[k] = {
            "pre_tax_cagr": pre.cagr_after_tax, "after_tax_cagr": after.cagr_after_tax,
            "final_after_tax": after.final_after_tax, "tax_paid_to_date": after.tax_paid,
            "liquidation_tax": after.liquidation_tax,
            "realized_st_net": led.realized_st_total, "realized_lt_net": led.realized_lt_total,
            "dividends_taxed": sum(led.dividends.values()),
            "buy_volume": after.buy_volume, "sell_volume": after.sell_volume,
            "max_dd_pre_tax": max_drawdown(pre.equity), "n_trades": int(len(after.trades)),
            "disallowed_wash_loss": after.disallowed_loss,
        }
    ref["qqq"] = {"final_after_tax": bench_after.final_after_tax, "max_dd_pre_tax": max_drawdown(bench_pre.equity),
                  "liquidation_tax": bench_after.liquidation_tax, "tax_paid_to_date": bench_after.tax_paid}
    (REPORT_DIR / "results_dev_reference.json").write_text(json.dumps(to_jsonable(ref), indent=1, default=float))
    print(json.dumps(to_jsonable(ref), indent=1, default=float))

    fig = REPORT_DIR / "figures"
    curves = {"QQQ": bench_after.equity, **{k: runs[k][0].equity for k in "ABC"}}
    growth_chart(curves, str(fig / "growth_dev.png"),
                 "Growth of $1M, development period (Feb 2011 to Dec 2019)",
                 "Account value after each year's tax has been paid; unrealized gains not yet taxed")
    drawdown_chart({"QQQ": bench_pre.equity, "B": runs["B"][1].equity}, str(fig / "drawdown_dev.png"),
                   "Drawdowns, development period (pre-tax)",
                   "Decline from the previous peak in account value")
    b = ref["B"]
    ladder_chart([
        ("QQQ, pre-tax", ref["qqq_pre_tax_engine"] * 100, "QQQ"),
        ("S&P 500 index (SPY), pre-tax", ref["spy_pre_tax"] * 100, "ref"),
        ("Equal-weight S&P universe*, pre-tax", ref["ew_universe_survivor_only"] * 100, "ref"),
        ("Top-25 by momentum, no frictions", ref["top25_frictionless"] * 100, "ref"),
        ("Variant B, pre-tax (costs, rules)", b["pre_tax_cagr"] * 100, "B"),
        ("QQQ, after tax", ref["qqq_after_tax"] * 100, "QQQ"),
        ("Variant B, after tax", b["after_tax_cagr"] * 100, "B"),
    ], str(fig / "cagr_ladder_dev.png"), "Where the gap to QQQ comes from (annual return, Feb 2011 to Dec 2019)",
       "*Survivor-only: index members with no Yahoo price history are missing")


if __name__ == "__main__":
    main()
