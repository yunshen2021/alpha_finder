"""QQQ benchmark series."""
from __future__ import annotations

from datetime import date

import pandas as pd

from alpha_finder.config import BENCHMARK_TICKER, DEFAULT_START
from alpha_finder.data.prices import load_prices


def load_benchmark(
    start: str | date = DEFAULT_START,
    end: str | date | None = None,
    ticker: str = BENCHMARK_TICKER,
    **kwargs,
) -> pd.DataFrame:
    """Benchmark prices plus a `return` column of daily total returns.

    Total return comes from adj_close (dividends reinvested). The raw `close`
    and `dividends` columns are kept so the tax model can treat dividends as
    cash events on the same basis as the strategy.
    """
    prices = load_prices([ticker], start, end, **kwargs)
    if ticker not in prices:
        raise RuntimeError(f"no price data for benchmark {ticker}")
    df = prices[ticker].copy()
    df["return"] = df["adj_close"].pct_change()
    return df
