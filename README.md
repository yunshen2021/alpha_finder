# alpha_finder

Research project: find a stock-selection method that beats buy-and-hold **QQQ after tax**, for a taxable
account. Every version is judged the same way (same data, same tax assumptions, same success criteria),
so results are comparable.

## Where things stand

| Version | Folder | Status | Result |
|---|---|---|---|
| 1 | [`version_1_rules/`](version_1_rules/) | **Done** | 12-1 momentum rules on the S&P 500 does **not** beat QQQ: 7.6% vs 14.6% a year after tax (2011-2019) |
| 2 | [`version_2_ml/`](version_2_ml/) | **On hold** | Machine learning with many features. Plan and open decisions only, no code yet |

## Folder map

```
alpha_finder/
├── README.md                  <- you are here
├── version_1_rules/           <- one folder per version: everything about it lives inside
│   ├── README.md                 what it is, result at a glance, how to run
│   ├── backtest_report.md        the full write-up (read this first)
│   ├── ranks.py, variants.py     this version's own logic
│   ├── run_dev.py, make_dev_assets.py   scripts that produce the results
│   └── results/                  figures, CSVs, JSON, run_log.jsonl (every trial)
├── version_2_ml/              <- placeholder for the ML version (on hold)
├── data/                      <- all data files, shared by every version
│   ├── universe/                 S&P 500 membership snapshots (in git)
│   └── prices/                   downloaded Yahoo prices (local only, not in git)
├── docs/
│   └── strategy_rules.md         project-wide rules: goal, tax assumptions, success criteria,
│                                 validation discipline, failure modes
├── src/alpha_finder/          <- shared toolbox used by every version (see below)
├── scripts/                   <- shared utilities (download data)
├── tests/                     <- 42 automated tests
└── examples/                  <- empty placeholder from the original scaffold
```

Shared toolbox in `src/alpha_finder/` (nothing version-specific belongs here):

| Folder | Purpose |
|---|---|
| `data/` | Price loader with caching, QQQ benchmark, data quality report (code only; the data files are in `/data`) |
| `universe/` | Point-in-time S&P 500 membership rebuilt from the snapshots |
| `signals/` | Building blocks: month-end dates, momentum score, ranking, signal-quality (IC) diagnostics |
| `backtest/` | **Tax-lot engine** (wash sales, loss carryforward), performance statistics |
| `reporting/` | Chart code |
| `research.py` | Shared setup: market data, the dev/holdout split, tax rates, run logging |

## The rules every version follows

1. **Same yardstick.** A version is compared with after-tax buy-and-hold QQQ, using the success criteria in
   [`docs/strategy_rules.md`](docs/strategy_rules.md), fixed before results are seen.
2. **Development vs holdout.** 2011-2019 is for development. **2020 onward is unspent**: it is a one-time test
   for a candidate that already passed development.
3. **Every trial is logged** in that version's `results/run_log.jsonl`, so we know how many things were tried.
4. **A version only has to produce one thing**: a monthly table of stock ranks. The shared engine does the
   trading, taxes and benchmark comparison, which is what keeps versions comparable.

## Run

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt && pip install -e .
python -m pytest                              # tests
python scripts/fetch_universe_prices.py       # download prices into data/prices/ (about 1 minute)
python version_1_rules/run_dev.py             # rebuild version 1 results
python version_1_rules/make_dev_assets.py     # rebuild version 1 figures
```

## Adding a new version

1. Create `version_3_<name>/` next to the others.
2. Add a `README.md` (what it is, status), your code that builds the monthly rank table, and a `results/` folder.
3. Reuse the shared toolbox for everything else. Copy the pattern in `version_1_rules/run_dev.py`.
4. Write `backtest_report.md` in the same format, and add a row to the status table above.
