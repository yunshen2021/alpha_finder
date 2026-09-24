# Version 3: buy the top N holdings of QQQ or VGT instead of the ETF?

**Status: done.** Read [`report.md`](report.md) for the answer.

Every Wednesday from 2020 to 2025, put money into either the ETF itself or only its N largest holdings
(split in proportion to their fund weights), and never sell. Which ends 2025 with more money? N = 5, 10,
15, 20, 25, 50, 75, for QQQ and VGT. Benchmarks: all in QQQ, all in VGT, or kept as cash. Two contribution
plans: $1,000 every week, and $1,000 a week in 2020 rising 15% a year. Pure calculation, no taxes.

## Files

| File | What it does |
|---|---|
| `fetch_holdings.py` | Downloads each fund's quarterly holdings from SEC N-PORT filings (2019-2025) and maps them to tickers |
| `run.py` | Runs the audits, then every portfolio, scenario and period; writes `results/` |
| `make_report.py` | Builds the charts and tables used in the report from `results/` |
| `current_top.py` | Today's top N of QQQ and VGT and how a weekly amount splits between them (latest SEC filing, weights moved by price to the last close) |
| `report.md` | The write-up |
| `results/results.json` | Every computed number |
| `results/tables.md` | All result tables |
| `results/price_audit.csv` | Each holding's Yahoo price checked against the fund's own filing |
| `results/*_drift_accuracy.csv` | How well price-moved weights predict the next filing |
| `results/top_n_lists.csv` | The top-N stocks and dollar split at the first and last purchase |
| `results/figures/` | Charts |

Shared code lives in `src/alpha_finder/`: the weekly-purchase engine with spin-offs and buyouts
(`backtest/dca.py`), the SEC filing reader (`data/nport.py`) and estimated prices for delisted stocks
(`data/proxy_prices.py`).

## Run

```bash
export SEC_USER_AGENT="Your Name your.email@example.com"   # the SEC's rule for automated downloads
python version_3_etf_topn/fetch_holdings.py                # about 10 minutes the first time
python version_3_etf_topn/run.py                           # audits, then all runs
python version_3_etf_topn/make_report.py                   # charts and tables
python version_3_etf_topn/current_top.py --qqq 10 --vgt 5 --amount 1000   # this week's lists
```
