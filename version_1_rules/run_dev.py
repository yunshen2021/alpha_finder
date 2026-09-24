"""Version 1, development stage (2011-2019 only): signal check, A/B/C comparison, robustness, sanity tests.

The holdout period (2020 onward) is deliberately NOT touched here. Fill dates stop at
2019-12-31, and the finalist is picked by a rule fixed in advance (see pick_finalist).
"""
import json
import logging
import sys
from pathlib import Path
from dataclasses import replace

import pandas as pd

from alpha_finder.backtest.metrics import summarize
from alpha_finder.research import DEV_END, Market, benchmark_config, log_run, run_pair, to_jsonable
from alpha_finder.signals.evaluate import signal_diagnostics, summarize_diagnostics
from alpha_finder.signals.momentum import shuffle_ranks

# Version 1's own modules sit next to this file.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ranks import momentum_ranks  # noqa: E402
from variants import variants  # noqa: E402

RESULTS = Path(__file__).resolve().parent / "results"

logging.basicConfig(level=logging.ERROR)
pd.set_option("display.width", 220)
pd.set_option("display.max_columns", 30)


def pct(x):
    return f"{100 * x:6.2f}%"


def evaluate(market, cfg, ranks, bench_cache, key, stage, name, extra=None):
    if key not in bench_cache:
        bench_cache[key] = run_pair(market, benchmark_config(), ranks, None, DEV_END)
    bench_after, bench_pre = bench_cache[key]
    after, pre = run_pair(market, cfg, ranks, None, DEV_END)
    row = summarize(name, after, pre, bench_after, bench_pre)
    log_run(RESULTS / "run_log.jsonl", stage, name, cfg, None, DEV_END, row, extra)
    return row, after, pre


def pick_finalist(rows: dict[str, dict]) -> str:
    """Pre-registered rule: highest dev-period after-tax CAGR among A, B, C."""
    return max(rows, key=lambda k: rows[k]["after_tax_cagr"])


def main():
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "data").mkdir(exist_ok=True)
    market = Market.load()
    ranks = momentum_ranks(market, 12, 1)
    dev_dates = [d for d in ranks if d <= DEV_END]

    cov = market.coverage(ranks)
    dev_cov = cov.loc[(cov.index >= dev_dates[0]) & (cov.index <= DEV_END)]
    coverage = {
        "first_signal": str(dev_dates[0].date()),
        "avg_members": float(dev_cov.members.mean()),
        "avg_with_price": float(dev_cov.with_price.mean()),
        "avg_ranked": float(dev_cov.ranked.mean()),
        "price_coverage": float((dev_cov.with_price / dev_cov.members).mean()),
        "tickers_requested": len(market.history.all_tickers("2010-12-31", "2026-09-23")),
        "tickers_with_prices": len(market.prices) - 1,
    }
    print("COVERAGE", json.dumps(coverage, indent=1))

    diag = signal_diagnostics({d: ranks[d] for d in dev_dates}, market.adj, market.month_ends, end=DEV_END)
    diag_summary = summarize_diagnostics(diag)
    print("SIGNAL (dev)", json.dumps(diag_summary, indent=1))

    bench_cache = {}
    rows, curves = {}, {}
    for key, cfg in variants().items():
        row, after, pre = evaluate(market, cfg, ranks, bench_cache, (12, 1), "dev", cfg.name)
        rows[key] = row
        curves[key] = (after.equity, pre.equity)
        print(f"\n{cfg.name}")
        print(f"  after-tax CAGR {pct(row['after_tax_cagr'])}  QQQ {pct(row['bench_after_tax_cagr'])}  "
              f"excess {pct(row['excess_after_tax'])}   pre-tax excess {pct(row['excess_pre_tax'])}")
        print(f"  alpha(after) {pct(row['alpha_after_tax'].alpha_annual)} t={row['alpha_after_tax'].alpha_t:.2f} "
              f"beta {row['alpha_after_tax'].beta:.2f}   maxDD {pct(row['max_dd'])} vs QQQ {pct(row['bench_max_dd'])}")
        print(f"  turnover {pct(row['annual_turnover'])}  LT share of gains {pct(row['lt_share_of_gains'])}  "
              f"tax paid ${row['tax_paid']:,.0f}  holdings {row['avg_holdings']:.1f}  cash {pct(row['avg_cash_frac'])}")

    bench_after, bench_pre = bench_cache[(12, 1)]
    finalist = pick_finalist(rows)
    print("\nFINALIST (pre-registered rule: best dev after-tax CAGR):", finalist)
    (RESULTS / "finalist.json").write_text(json.dumps({"finalist": finalist, "dev_end": str(DEV_END.date())}))

    # --- robustness: one parameter at a time around variant C (logged; not used to re-tune) ---
    base = variants()["C"]
    neigh = {
        "N=20": (replace(base, n_holdings=20), (12, 1)),
        "N=30": (replace(base, n_holdings=30), (12, 1)),
        "sell_rank=40": (replace(base, sell_rank=40), (12, 1)),
        "sell_rank=50": (replace(base, sell_rank=50), (12, 1)),
        "sell_rank=75": (replace(base, sell_rank=75), (12, 1)),
        "lookback=6": (base, (6, 1)),
        "lookback=9": (base, (9, 1)),
        "skip=0": (base, (12, 0)),
    }
    neigh_rows = {}
    for label, (cfg, (lb, sk)) in neigh.items():
        r = momentum_ranks(market, lb, sk)
        row, _, _ = evaluate(market, replace(cfg, name=f"C {label}"), r, bench_cache, (lb, sk), "dev-neighborhood", f"C {label}")
        neigh_rows[label] = row
        print(f"  {label:14s} after-tax excess {pct(row['excess_after_tax'])}  pre-tax excess {pct(row['excess_pre_tax'])}  "
              f"turnover {pct(row['annual_turnover'])}")

    # --- sanity 1: shuffled scores must show no edge ---
    shuffled = []
    for seed in range(5):
        row, _, _ = evaluate(market, replace(base, name=f"C shuffled seed{seed}"), shuffle_ranks(ranks, seed),
                             bench_cache, (12, 1), "dev-sanity", f"C shuffled seed{seed}")
        shuffled.append(row)
    print("  shuffled after-tax excess:", [round(100 * r["excess_after_tax"], 2) for r in shuffled],
          " pre-tax alpha t:", [round(r["alpha_pre_tax"].alpha_t, 2) for r in shuffled])

    # --- sanity 2: one more day of delay must not collapse a monthly strategy ---
    lag_row, _, _ = evaluate(market, replace(base, name="C fill delay +1 day", fill_delay=2), ranks,
                             bench_cache, (12, 1), "dev-sanity", "C fill delay +1 day")
    print("  lag +1 day after-tax excess", pct(lag_row["excess_after_tax"]), "vs base", pct(rows["C"]["excess_after_tax"]))

    out = {
        "coverage": coverage, "signal": diag_summary, "variants": rows, "finalist": finalist,
        "neighborhood": neigh_rows, "shuffled": shuffled, "lag": lag_row,
        "bench": {"after_tax_cagr": rows["A"]["bench_after_tax_cagr"], "max_dd": rows["A"]["bench_max_dd"]},
    }
    (RESULTS / "results_dev.json").write_text(json.dumps(to_jsonable(out), indent=1, default=float))
    eq = pd.DataFrame({
        "QQQ_after_tax": bench_after.equity, "QQQ_pre_tax": bench_pre.equity,
        **{f"{k}_after_tax": v[0] for k, v in curves.items()},
        **{f"{k}_pre_tax": v[1] for k, v in curves.items()},
    })
    eq.to_csv(RESULTS / "data" / "equity_dev.csv")
    diag.to_csv(RESULTS / "data" / "signal_diagnostics_dev.csv")
    cov.to_csv(RESULTS / "data" / "coverage.csv")


if __name__ == "__main__":
    main()
