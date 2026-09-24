# data/

All data files for the project. They are shared by every version.

| Folder | What | In git? |
|---|---|---|
| `universe/` | S&P 500 membership: `sp500_current.csv` (today's list) and `sp500_changes.csv` (every add/remove, from Wikipedia, pulled 2026-09-24). Kept in git so results stay reproducible even if Wikipedia changes | Yes |
| `prices/` | One file per ticker (`AAPL.parquet`, ...) of daily Yahoo prices, plus a small `.json` of what date range each file covers. Created by `python scripts/fetch_universe_prices.py` | No (large, re-downloadable) |
| `since2000/` | Experimental price cache going back to 2000, used only for a data-coverage check | No |
| `etf_holdings/` | QQQ and VGT holdings, one row per stock per quarter (2019-2025), from the funds' SEC N-PORT filings, with the ticker and how it was matched. `id_to_ticker.csv` caches identifier lookups; `sec_company_tickers.json` is the SEC's company list used for name matching. Built by `version_3_etf_topn/fetch_holdings.py` | Yes |
| `etf_holdings_raw/` | The raw N-PORT XML filings | No (re-downloadable) |

The code that loads and checks this data is in `src/alpha_finder/data/` (the loader, the QQQ benchmark, the quality
report) and `src/alpha_finder/universe/`.

Known limit: Yahoo has no prices for companies that no longer exist, so about a quarter of former S&P 500
members are missing. See `version_1_rules/backtest_report.md`, "Limits and biases".
