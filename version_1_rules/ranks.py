"""Version 1's signal: 12-1 momentum, turned into a monthly rank table the engine can trade."""
import pandas as pd

from alpha_finder.research import Market
from alpha_finder.signals.momentum import momentum_scores, rank_universe


def momentum_ranks(market: Market, lookback: int = 12, skip: int = 1) -> dict[pd.Timestamp, pd.Series]:
    """{month-end date: Series(rank by ticker)}, rank 1 = strongest momentum among index members."""
    cache = market.__dict__.setdefault("_momentum_ranks", {})
    key = (lookback, skip)
    if key not in cache:
        scores = momentum_scores(market.adj, market.month_ends, lookback, skip)
        have_price = market.adj.ffill(limit=5).reindex(market.month_ends).notna()
        cache[key] = rank_universe(
            scores,
            members=lambda d: market.history.members(d) - {"QQQ"},
            tradable=lambda d: set(have_price.columns[have_price.loc[d].to_numpy()]),
        )
    return cache[key]
