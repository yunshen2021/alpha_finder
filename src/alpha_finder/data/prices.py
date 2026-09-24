"""Daily price loading with a per-ticker parquet cache.

Each ticker is stored as data_cache/prices/<TICKER>.parquet plus a small JSON
sidecar recording the date range that was requested. A cache hit requires the
cached request to cover the new one, so results are deterministic for a given
(start, end) and old runs can be reproduced by passing an explicit end date.

Columns: open, high, low, close (split-adjusted only), adj_close (split and
dividend adjusted), volume, dividends (cash per share), splits (ratio).
"""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Iterable

import pandas as pd

from alpha_finder.config import CACHE_DIR, DEFAULT_START

log = logging.getLogger(__name__)

COLUMNS = ["open", "high", "low", "close", "adj_close", "volume", "dividends", "splits"]

# fetcher(tickers, start, end) -> {ticker: DataFrame}; end is inclusive.
Fetcher = Callable[[list[str], date, date], dict[str, pd.DataFrame]]

_YAHOO_RENAME = {
    "Open": "open",
    "High": "high",
    "Low": "low",
    "Close": "close",
    "Adj Close": "adj_close",
    "Volume": "volume",
    "Dividends": "dividends",
    "Stock Splits": "splits",
}


def yahoo_symbol(ticker: str) -> str:
    """Yahoo uses dashes for share classes, e.g. BRK.B -> BRK-B."""
    return ticker.replace(".", "-")


def yfinance_fetch(tickers: list[str], start: date, end: date) -> dict[str, pd.DataFrame]:
    import yfinance as yf

    symbols = {t: yahoo_symbol(t) for t in tickers}
    raw = yf.download(
        list(symbols.values()),
        start=start.isoformat(),
        end=(end + timedelta(days=1)).isoformat(),  # yfinance end is exclusive
        auto_adjust=False,
        actions=True,
        group_by="ticker",
        progress=False,
        threads=True,
    )
    out: dict[str, pd.DataFrame] = {}
    if raw is None or raw.empty:
        return out
    for ticker, symbol in symbols.items():
        if isinstance(raw.columns, pd.MultiIndex):
            if symbol not in raw.columns.get_level_values(0):
                continue
            frame = raw[symbol]
        else:  # single ticker, flat columns
            frame = raw
        df = _normalize(frame)
        if not df.empty:
            out[ticker] = df
    return out


def _normalize(frame: pd.DataFrame) -> pd.DataFrame:
    df = frame.rename(columns=_YAHOO_RENAME)
    for col in ("dividends", "splits"):
        if col not in df:
            df[col] = 0.0
    df = df[COLUMNS].copy()
    df = df.dropna(subset=["close"])
    df[["dividends", "splits"]] = df[["dividends", "splits"]].fillna(0.0)
    idx = pd.DatetimeIndex(df.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    df.index = idx.normalize()
    df.index.name = "date"
    return df.sort_index()


def _as_date(value: str | date | pd.Timestamp) -> date:
    return pd.Timestamp(value).date()


def _cache_files(ticker: str, cache_dir: Path) -> tuple[Path, Path]:
    base = cache_dir / "prices"
    return base / f"{ticker}.parquet", base / f"{ticker}.json"


def _slice(df: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    return df.loc[pd.Timestamp(start) : pd.Timestamp(end)]


def _read_cache(ticker: str, start: date, end: date, cache_dir: Path) -> pd.DataFrame | None:
    data_path, meta_path = _cache_files(ticker, cache_dir)
    if not (data_path.exists() and meta_path.exists()):
        return None
    try:
        meta = json.loads(meta_path.read_text())
        if date.fromisoformat(meta["start"]) > start or date.fromisoformat(meta["end"]) < end:
            return None
        return _slice(pd.read_parquet(data_path), start, end)
    except Exception:  # corrupt or unreadable cache: refetch
        log.warning("ignoring unreadable cache for %s", ticker)
        return None


def _write_cache(ticker: str, df: pd.DataFrame, start: date, end: date, cache_dir: Path) -> None:
    data_path, meta_path = _cache_files(ticker, cache_dir)
    data_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = data_path.with_suffix(".parquet.tmp")
    df.to_parquet(tmp)
    tmp.replace(data_path)
    meta_path.write_text(
        json.dumps({"start": start.isoformat(), "end": end.isoformat(), "rows": len(df)})
    )


def load_prices(
    tickers: Iterable[str],
    start: str | date = DEFAULT_START,
    end: str | date | None = None,
    *,
    cache_dir: Path = CACHE_DIR,
    refresh: bool = False,
    fetcher: Fetcher = yfinance_fetch,
) -> dict[str, pd.DataFrame]:
    """Load daily prices per ticker, using the cache where it covers the range.

    Tickers with no data are logged and omitted from the result; compare the
    returned keys with the request (see data.quality) to see what is missing.
    end defaults to today.
    """
    start_d = _as_date(start)
    end_d = _as_date(end) if end is not None else date.today()
    names = list(dict.fromkeys(tickers))

    result: dict[str, pd.DataFrame] = {}
    to_fetch: list[str] = []
    for t in names:
        cached = None if refresh else _read_cache(t, start_d, end_d, cache_dir)
        if cached is None:
            to_fetch.append(t)
        else:
            result[t] = cached

    if to_fetch:
        fetched = fetcher(to_fetch, start_d, end_d)
        for t in to_fetch:
            df = fetched.get(t)
            if df is None or df.empty:
                log.warning("no data returned for %s", t)
                continue
            _write_cache(t, df, start_d, end_d, cache_dir)
            result[t] = _slice(df, start_d, end_d)

    return {t: result[t] for t in names if t in result}


def to_panel(prices: dict[str, pd.DataFrame], field: str = "adj_close") -> pd.DataFrame:
    """Wide date x ticker frame for one field."""
    return pd.DataFrame({t: df[field] for t, df in prices.items()}).sort_index()
