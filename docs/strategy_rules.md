# Alpha Finder: Strategy Rules and Research Plan

Status: **draft for review**. Nothing here has been backtested yet. Every threshold is a
starting hypothesis that the backtest is allowed to reject. Update this file when a rule
changes, and note why.

## 1. Goal

Build a research pipeline that answers one question:

> Does this stock-selection strategy beat buy-and-hold QQQ **after tax, after costs,
> out of sample**?

It is a research and decision tool for a real taxable account, not a ticker generator.

## 2. Constraints and assumptions

| Item | Value | Notes |
|---|---|---|
| Account | Taxable brokerage (IRA later) | Accounts are a parameter: `taxable` or `tax_free` |
| Rebalance | Monthly decision, few actual trades | Weekly only if monthly works, and probably only in the IRA |
| Period | 2010 to now | Dev: 2010-2019. Holdout: 2020-now, untouched until finalists |
| Direction | Long-only | Shorting produces short-term gains and adds borrow cost |
| Benchmark | QQQ, total return | After-tax buy-and-hold is the headline comparison |
| Account size | **$1,000,000** | ~$40k per position at N=25. Whole shares are fine, and S&P 500 liquidity is not a constraint at this size. Backtest starts from $1M |

### Tax parameters (config values, not constants)

| Item | Federal | MA | NIIT | All-in |
|---|---|---|---|---|
| Short-term gains | 35% | 3% | 0% (config) | **38%** |
| Long-term gains / qualified dividends | 20% | 3% | 0% (config) | **23%** |

NIIT (3.8%) is a separate config value, currently set to 0 per the account holder's stated
rates. If it applies, the all-in rates become 41.8% / 26.8% and the hurdle below gets harder.

To verify: whether NIIT applies, whether 3% is the actual MA rate on investment gains, and
whether 20% is the right long-term federal rate.

### Why tax dominates the design

With QQQ at ~18% pre-tax, held 15 years and sold once at 23%, after-tax QQQ is
**~16.2% a year**. To match that, a strategy needs roughly:

| Gains realized as | Pre-tax return needed | Alpha needed over QQQ |
|---|---|---|
| All short-term | ~26% | ~8 points a year |
| All long-term, realized yearly | ~21% | ~3 points a year |

(Rough estimate; ignores loss offsets.) So the design must push gains into long-term
treatment, keep turnover low, and harvest losses.

## 3. Success criteria (agreed and fixed before seeing results)

Primary objective: **higher after-tax account value than buy-and-hold QQQ.** QQQ is the
default; the strategy has to earn its complexity.

The strategy is worth trading only if **all four** hold, measured net of costs and tax on
total-return series:

1. **Margin:** after-tax excess return vs after-tax buy-and-hold QQQ of at least
   **~2 points a year**. Smaller edges are inside the noise for ~15 years of data.
2. **Alpha, not beta:** regressing strategy returns on QQQ gives a positive intercept whose
   confidence interval does not sit on zero.
3. **Drawdown:** max drawdown no worse than QQQ's, ideally a bit better (QQQ: roughly -28%
   in early 2020 and -35% in 2022).
4. **Out of sample:** the advantage appears in the 2020-now holdout and is not concentrated
   in a single sub-period.

Reading the outcomes:

- Meets 1 only: a warning (likely hidden beta or noise), not a win.
- Meets 2-4 but not 1: the case is risk reduction; consider the QQQ core plus satellite mix.
- Fails 1 and 2: buy QQQ.

Also reported: information ratio, tracking error, turnover, share of gains realized
long-term, and pre-tax results as secondary numbers. Practical note: even good strategies
trail QQQ for stretches; longest underperformance period is reported so we know what holding
it would feel like.

Fallback outcome: if stock selection does not clear the bar, test a **QQQ core plus
strategy satellite** mix instead of a full replacement.

## 4. Approach: rules-based, not ML

Version one uses explicit, published, slow rules. No trained model. Reasons: every trade is
explainable; few parameters means less room to overfit; the factors have decades of evidence;
and ~180 monthly observations is too little to train a model reliably.

Later, only if the rules baseline works and we understand it, and only if the result survives
the holdout: data-estimated tax-gate trade-offs, out-of-sample factor weighting, or a
gradient-boosted model compared against the rules baseline.

## 5. Universe

S&P 500 with **point-in-time membership** reconstructed from historical index changes
(e.g. Wikipedia's change log), including delisted names where possible. Plus a basic
liquidity filter.

Known limitation: yfinance drops delisted tickers. We measure and report how many are
missing instead of hiding it. If reconstruction proves impractical, fall back to current
members and label all results as optimistic.

## 6. Stock selection rules

Each factor becomes a cross-sectional percentile rank per date. Composite = equal-weight
average of ranks (no fitted weights).

| # | Rule | Source of idea | Parameters to test |
|---|---|---|---|
| S1 | 12-1 momentum: return over the past 12 months, skipping the latest month | Jegadeesh & Titman (1993); MSCI Momentum-style indexes | Lookback 6/9/12, skip 0/1 |
| S2 | Quality: gross profitability, ROIC, low leverage (phase 2) | Novy-Marx (2013); AQR quality work | Which measures; does adding it help |
| S3 | Low-volatility filter (a risk control, not a return driver) | Frazzini & Pedersen; min-vol products | Does it cut drawdown without costing too much return |

Value is **not** included to start (weak over 2010-now). Adding it later must be justified by
evidence, not by "it's a classic".

Phase 1 = momentum only (reliable price data). Phase 2 adds quality once filing-date-correct
fundamentals are available (SEC EDGAR likely).

## 7. Portfolio rules

| # | Rule | Starting value | Tested range | Notes |
|---|---|---|---|---|
| P1 | Holdings: top-N by composite | N = 25 | 20 / 25 / 30 | My simplification; institutions hold far more |
| P2 | Weighting | Roughly equal | Equal vs inverse-vol | |
| P3 | Position cap | 6-8% at purchase | 5-10% | |
| P4 | Sector cap | 30% | 25 / 30 / 35% | Standard risk control |

## 8. Buy rules

- Ranks computed at month-end close.
- Orders fill at the **next open**, never the same close used to build the signal.
- New buys only from names in the top N that we don't already hold, funded by sells,
  new cash and dividends.

## 9. Sell rules (applied in this order)

| # | Rule | Starting value | Tested range |
|---|---|---|---|
| X1 | **Forced sell**: acquired, delisted, or removed from universe | n/a | n/a |
| X2 | **Rank buffer**: buy at rank <= 25, hold until rank > 60 | sell at 60 | 40 / 50 / 60 / 75 (idea from MSCI/FTSE Russell buffer zones; the numbers are mine) |
| X3a | Fails buffer and **at a loss**: sell and harvest; no rebuy for 31 days | on | on / off |
| X3b | Fails buffer and **long-term gain**: sell normally | on | n/a |
| X3c | Fails buffer and **short-term gain near the 1-year mark**: hold until long-term unless rank is very weak | window ~60 days; override at rank > 150 | window 30 / 60 / 90; override 100 / 150 / 200 |
| X4 | **No trimming for weight drift** unless above a hard cap | cap 10% | trim-to-target as a comparison |
| X5 | **No price stop-losses** by default | off | one stop rule as an optional test |

Principle behind X3: sell only if the expected return lost by holding a weaker stock exceeds
the extra tax from selling now instead of later. Version one uses the thresholds above.
Later the expected-return side can be estimated from historical spreads between rank buckets.

## 10. Tax and cost modeling

- Lot-level tracking with purchase dates; each sale classified short-term or long-term.
- Gains and losses netted per year (short-term against short-term first), taxed at the
  configured rates (38% short-term, 23% long-term). Dividends taxed as they are paid
  (treated as qualified).
- Wash sales: 31-day rule modeled for harvested losses. If it proves too complex initially,
  flag it explicitly instead of assuming it away.
- Transaction costs: ~5-10 bps per trade to start (configurable).
- **Fair QQQ comparison**: same tax basis for both. Tax paid annually from the portfolio for
  both, and the terminal liquidation tax computed for both. Buy-and-hold QQQ realizes gains
  only at the end.

## 11. Validation plan

Three versions compared after tax, on the 2010-2019 development period only:

- **A**: top-N fully replaced monthly, tax-blind
- **B**: with rank buffer (X2)
- **C**: buffer plus tax gate (X2 + X3)

If C does not clearly beat B, and B does not clearly beat A after tax, the tax logic is not
adding value and we simplify.

Then:

1. **Neighborhood checks** on every parameter. A real effect should degrade smoothly; if 12-1
   works but 11-1 and 13-1 don't, treat it as noise.
2. **Sub-period analysis**, not just the aggregate (2010-2013, 2014-2019, 2020-2021, 2022, 2023+).
3. **One-shot holdout** on 2020-now for the finalists. After that period is used, it is spent.
   Retuning afterwards means it is no longer a test.
4. **Run log**: every backtest recorded with parameters, data snapshot and results, so we can
   count trials and reproduce numbers. Consider a deflated Sharpe ratio for the multiple-testing
   correction.
5. **Paper trade** for a few months before real money. Expect live results to be worse than the
   backtest.

## 12. Automatic sanity checks

- **Shuffled signal**: randomizing scores should show zero alpha. If not, the pipeline has a bug.
- **Lag test**: adding a one-day delay should not collapse a monthly strategy.
- **Shifted lag enforced centrally**: signal authors cannot accidentally use same-day data.
- **Audit any result that looks too good** (for example Sharpe > 1.5) before believing it.
- **Data quality report**: missing tickers, daily returns > ~40%, gaps, coverage by date, and a
  spot check of a few total-return series against a second source.

## 13. Known failure modes to watch for

- Survivorship bias, bad split/dividend adjustment, ticker changes
- Fundamentals used before the filing date; restated figures
- Signal and fill on the same close; full-sample normalization
- Overfitting, multiple testing, parameter sensitivity, single-regime results
- Hidden beta (winning only because of higher exposure to tech)
- Concentration in one sector or factor
- Pre-tax strategy compared with after-tax QQQ, or price return with total return
- Tax logic that creates its own bias (e.g. holding losers too long)
- Changing rules after seeing the holdout; trusting one impressive number
- Unreproducible runs (no fixed data snapshot)

## 14. Build order

1. Data layer: yfinance with parquet caching in `data_cache/`, data quality report, QQQ benchmark.
2. Tax-lot accounting and the after-tax comparison against buy-and-hold QQQ.
3. Universe reconstruction (point-in-time S&P 500).
4. Signals: momentum first, then low-vol, then quality.
5. Tax-aware portfolio construction and the A/B/C comparison.
6. Validation: neighborhood checks, sub-periods, holdout, run log.

## 15. Open questions

- Confirm the rates: 35% / 20% federal plus 3% MA, i.e. 38% / 23%. Does NIIT (3.8%) apply on
  top? Is 3% the correct MA rate on investment gains?
- Any rule above to drop, change or add?
- Long-only top-25 acceptable, or a different holding count?

## 16. Deliberately not doing (yet)

- ML models, weekly rebalancing, shorting, value factor, price stop-losses
- Optimizer-based portfolio construction
- Intraday data or execution modeling beyond a simple cost assumption

## 17. Implementation status (2026-09-24)

Version 1 is built and evaluated on the development period only. Results and the full write-up are
in [`reports/backtest_report.md`](../reports/backtest_report.md). Verdict: **it does not beat QQQ**
(fails success criteria 1, 2 and 3), so per section 3 the reading is "buy QQQ" for this version.

What was built: cached price loader, QQQ benchmark, data quality report, point-in-time S&P 500
universe, 12-1 momentum, lot-level tax engine with wash sales, variants A/B/C, alpha/beta and
drawdown statistics, IC diagnostics, run log, shuffled-signal and lag sanity tests (42 tests).

Deviations from this plan, and why:

- **Dev window is Feb 2011 to Dec 2019**, not 2010: the 12-month lookback needs a year of prices first.
- **Momentum only.** Quality (S2) needs filing-date-correct fundamentals and is not built. Low-vol (S3) was not tested.
- **Sector caps use today's GICS sectors**; names that left the index have no label and are uncapped.
- **Holdout (2020 onward) is unspent on purpose**: the finalist failed the dev bar by ~9 points, so it
  does not qualify, and the holdout is kept clean for a candidate that does.
- **Finalist rule** (fixed in advance): best dev-period after-tax return among A, B, C. B was selected, though
  A, B and C are within noise of each other.
- **Tax gate (X3c) showed no benefit**; per section 11 it is dropped from further work unless a new signal changes that.
- **Coverage gap**: Yahoo has no prices for many former members (69% of index members priced in 2011,
  99.5% today), so survivorship bias is reduced but not removed.

Open items: confirm NIIT applicability and the MA rate on investment gains (config `TAX` in
`src/alpha_finder/research.py`); choose the next direction (see the report's "What I would do next").
