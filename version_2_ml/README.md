# Version 2: machine learning

**Status: on hold.** Nothing is built here yet. This page records what we discussed so the plan is not lost.

## Idea

Learn which stocks to buy from many features instead of one hand-written rule. Features you listed: volume,
industry, bond yields, the Fed interest rate, dollar strength, gold, and more.

## Design notes from the discussion (not a final spec)

- **Macro features work through interactions.** Yields, the dollar and gold are the same for every stock on a given
  date, so they cannot rank stocks on their own. They help only when combined with each stock's sensitivity to them
  (for example, rate-sensitive sectors when yields rise).
- **Add features in groups and keep a group only if it helps out of sample:** (1) price and volume, (2) industry,
  (3) stock sensitivities to yield/dollar/gold/oil/VIX, (4) macro state interactions, (5) fundamentals later.
- **Use changes and relative measures, not raw levels** (a Fed rate of 0% identifies the era and lets a model memorize it).
- **Target:** relative return over 6 to 12 months, to keep holdings slow and tax-friendly.
- **Models:** ridge first, then gradient boosting. Sequence models (LSTM, transformers) only if the simpler models
  show real signal.
- **Validation:** blocked cross-validation with gaps (leave-two-years-out) for tuning; walk-forward (train on the past,
  test on the next 1-2 years) for the honest result; a clean holdout that is never used for tuning.
- **Sanity tests:** shuffled labels must show no edge; no data after the test date may influence any result.
- **Same yardstick as version 1:** the model outputs a monthly rank table; the shared engine handles trading, tax and
  the QQQ comparison, judged by the success criteria in `../docs/strategy_rules.md`.

## Decisions still open

| Question | Options |
|---|---|
| Data | Free Yahoo data from 2011 (coverage 69% rising to 99%) **or** a paid source that includes delisted stocks and point-in-time fundamentals (Sharadar, Norgate; price unverified), which would allow 2000+ |
| Holdout | Keep 2020+ untouched **or** develop through 2022 and hold out 2023+ (more market regimes in development) |
| Exact Fed funds rate | Free FRED API key, or use the 3-month T-bill yield from Yahoo as a proxy |

## Expected layout once work starts

```
version_2_ml/
├── README.md
├── backtest_report.md
├── features.py       feature builders (one function per feature group)
├── validation.py     blocked cross-validation and walk-forward
├── ridge/            one folder per model family
├── gbm/
└── results/
```

Honest expectation: better than a price-only model, but still a long shot. The short history, a single macro regime,
a large-cap universe and the after-tax hurdle vs QQQ remain.
