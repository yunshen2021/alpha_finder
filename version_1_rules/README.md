# Version 1: rules-based momentum

**Status: done.** Result: does **not** beat QQQ.

Buy the 25 S&P 500 stocks with the strongest 12-1 month momentum, rebalanced monthly, with tax-aware selling
rules. Tested on 2011-2019 only; the 2020-onward holdout is unspent.

## Result at a glance (development period, Feb 2011 to Dec 2019, $1M start)

| | QQQ buy and hold | Best variant (B) |
|---|---|---|
| Annual return after tax | **14.6%** | 7.6% |
| $1M becomes (after tax) | **$3.37M** | $1.93M |
| Worst drawdown (pre-tax) | **-22.7%** | -34.3% |

Momentum showed no predictive power in this sample (rank correlation 0.001). Most of the gap to QQQ comes from the
universe: the S&P 500 itself trailed QQQ by about 4 points a year.

## Read in this order

1. [`backtest_report.md`](backtest_report.md): the full write-up with tables, charts, limits and next steps.
2. [`../docs/strategy_rules.md`](../docs/strategy_rules.md): the rules and success criteria all versions share.

## Files

| File | What it does |
|---|---|
| `ranks.py` | The signal: 12-1 momentum turned into a monthly rank of index members |
| `variants.py` | The three portfolio variants: **A** replace monthly, **B** rank buffer, **C** buffer + tax gate |
| `run_dev.py` | Runs everything (variants, robustness checks, sanity tests) and writes `results/` |
| `make_dev_assets.py` | Builds the reference numbers and the three charts |
| `results/figures/` | Charts used in the report |
| `results/data/` | Daily equity curves, signal diagnostics, coverage table (CSV) |
| `results/*.json` | All computed numbers behind the report |
| `results/run_log.jsonl` | Every trial that was run (17), so the number of things tried is on record |

## Run

```bash
python scripts/fetch_universe_prices.py     # once, from the project root
python version_1_rules/run_dev.py           # about 3 minutes
python version_1_rules/make_dev_assets.py
```

Everything else (tax engine, data loading, statistics) is in the shared toolbox, `src/alpha_finder/`.
