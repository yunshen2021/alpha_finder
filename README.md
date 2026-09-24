# alpha_finder

Research toolkit for testing equity signals **after tax against QQQ**, for a taxable account.
The question it answers: *does this strategy beat buy-and-hold QQQ after costs and tax, out of sample?*

**Current status:** version 1 (12-1 momentum on the S&P 500) does **not** beat QQQ.
Read [`reports/backtest_report.md`](reports/backtest_report.md) for results and next steps, and
[`docs/strategy_rules.md`](docs/strategy_rules.md) for the rules, tax assumptions and success criteria.

## Layout

| Path | Purpose |
|---|---|
| `src/alpha_finder/data/` | Cached Yahoo price loader, QQQ benchmark, data quality report |
| `src/alpha_finder/universe/` | Point-in-time S&P 500 membership rebuilt from Wikipedia snapshots |
| `src/alpha_finder/signals/` | 12-1 momentum, ranking, IC diagnostics |
| `src/alpha_finder/backtest/` | Lot-level tax ledger (wash sales, loss carryforward), engine, metrics |
| `src/alpha_finder/research.py` | Shared setup, variants A/B/C, run log |
| `scripts/` | `fetch_universe_prices.py`, `run_dev.py`, `make_dev_assets.py` |
| `reports/` | Report, figures, results JSON, run log of every trial |
| `snapshots/` | Index membership snapshot (keeps results reproducible) |

## Run

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt && pip install -e .
python -m pytest
python scripts/fetch_universe_prices.py
python scripts/run_dev.py
python scripts/make_dev_assets.py
```

Research discipline: 2011-2019 is the development period; 2020 onward is a held-out test that is
run once, for a candidate that already passed development. Every trial is logged in `reports/run_log.jsonl`.
