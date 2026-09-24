"""12-1 momentum and cross-sectional ranking.

Everything is computed at month-end closes. The score at month-end t is the
total return from t-12 months to t-1 month, skipping the latest month (which
tends to reverse). Orders based on it are filled at the NEXT trading day's
open, enforced by the backtest engine, never on the same close.
"""
from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd


def month_end_dates(calendar: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Last trading day of each month."""
    s = pd.Series(calendar, index=calendar)
    return pd.DatetimeIndex(s.groupby([calendar.year, calendar.month]).max().values)


def momentum_scores(adj_close: pd.DataFrame, rebalance_dates: pd.DatetimeIndex,
                    lookback: int = 12, skip: int = 1) -> pd.DataFrame:
    """Rebalance-date x ticker scores. NaN where history is missing."""
    px = adj_close.ffill(limit=5).reindex(rebalance_dates)
    return px.shift(skip) / px.shift(lookback) - 1.0


def rank_universe(
    scores: pd.DataFrame,
    members: Callable[[pd.Timestamp], set[str]],
    tradable: Callable[[pd.Timestamp], set[str]] | None = None,
) -> dict[pd.Timestamp, pd.Series]:
    """Per date, rank 1 = best score among index members that have a score.

    `tradable`, if given, restricts to tickers with a price on that date.
    """
    ranks: dict[pd.Timestamp, pd.Series] = {}
    for date, row in scores.iterrows():
        eligible = members(date)
        if tradable is not None:
            eligible &= tradable(date)
        s = row[row.index.isin(eligible)].dropna()
        if s.empty:
            continue
        ranks[date] = s.rank(ascending=False, method="first")
    return ranks


def shuffle_ranks(ranks: dict[pd.Timestamp, pd.Series], seed: int) -> dict[pd.Timestamp, pd.Series]:
    """Random ranks over the same eligible names (a sanity check: no alpha expected)."""
    rng = np.random.default_rng(seed)
    out = {}
    for date, r in ranks.items():
        perm = rng.permutation(len(r)) + 1
        out[date] = pd.Series(perm.astype(float), index=r.index)
    return out
