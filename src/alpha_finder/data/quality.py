"""Data quality report for loaded price data.

Flags problems that create fake alpha: missing tickers (survivorship), gaps,
implausible returns (bad splits or adjustments), and non-positive prices.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable

import pandas as pd

EXTREME_RETURN = 0.40  # |daily adj_close return| above this is suspicious
MAX_GAP_DAYS = 7  # longest tolerated calendar gap between rows
MAX_MISSING_DAYS = 5  # trading days missing inside a ticker's own history
LATE_START_DAYS = 7  # first row later than the requested start by this much


@dataclass
class QualityReport:
    per_ticker: pd.DataFrame
    requested: int
    missing: list[str]

    @property
    def flagged(self) -> pd.DataFrame:
        return self.per_ticker[self.per_ticker["flags"] != ""]

    def summary(self) -> str:
        loaded = len(self.per_ticker)
        lines = [
            f"Requested {self.requested}, loaded {loaded}, "
            f"missing {len(self.missing)}, flagged {len(self.flagged)}",
        ]
        if self.missing:
            lines.append("Missing tickers: " + ", ".join(sorted(self.missing)))
        if len(self.flagged):
            cols = ["first_date", "last_date", "rows", "max_abs_return", "flags"]
            lines.append("Flagged:")
            lines.append(self.flagged[cols].to_string())
        return "\n".join(lines)


def _trading_calendar(closes: pd.DataFrame) -> pd.DatetimeIndex:
    """Dates on which at least half of the then-listed tickers have data.

    Using the union of all dates would let a single ticker's bogus row add a
    fake trading day, so we require majority presence among live tickers.
    """
    present = closes.notna()
    alive = present.cummax() & present[::-1].cummax()[::-1]
    ratio = present.sum(axis=1) / alive.sum(axis=1).clip(lower=1)
    return closes.index[ratio >= 0.5]


def quality_report(
    prices: dict[str, pd.DataFrame],
    requested: Iterable[str] | None = None,
    start: str | date | None = None,
    *,
    extreme_return: float = EXTREME_RETURN,
) -> QualityReport:
    """Build a per-ticker report. `requested` enables missing-ticker detection."""
    requested_list = list(dict.fromkeys(requested)) if requested is not None else list(prices)
    missing = [t for t in requested_list if t not in prices]

    if not prices:
        empty = pd.DataFrame(
            columns=[
                "first_date", "last_date", "rows", "missing_days", "max_gap_days",
                "max_abs_return", "n_extreme", "n_nonpositive", "n_zero_volume", "flags",
            ]
        )
        return QualityReport(empty, len(requested_list), missing)

    closes = pd.DataFrame({t: df["adj_close"] for t, df in prices.items()}).sort_index()
    calendar = _trading_calendar(closes)
    start_ts = pd.Timestamp(start) if start is not None else None

    rows = []
    for ticker, df in prices.items():
        idx = df.index
        rets = df["adj_close"].pct_change().dropna()
        own_days = calendar[(calendar >= idx.min()) & (calendar <= idx.max())]
        missing_days = len(own_days.difference(idx))
        gaps = idx.to_series().diff().dt.days.dropna()
        max_gap = int(gaps.max()) if len(gaps) else 0
        max_ret = float(rets.abs().max()) if len(rets) else 0.0
        n_extreme = int((rets.abs() > extreme_return).sum())
        n_nonpos = int((df[["open", "high", "low", "close", "adj_close"]] <= 0).any(axis=1).sum())
        n_zero_vol = int((df["volume"] == 0).sum())

        flags = []
        if n_extreme:
            flags.append(f"extreme_return x{n_extreme}")
        if missing_days > MAX_MISSING_DAYS:
            flags.append(f"missing_days {missing_days}")
        if max_gap > MAX_GAP_DAYS:
            flags.append(f"gap {max_gap}d")
        if n_nonpos:
            flags.append(f"nonpositive x{n_nonpos}")
        if start_ts is not None and (idx.min() - start_ts).days > LATE_START_DAYS:
            flags.append("late_start")

        rows.append(
            {
                "ticker": ticker,
                "first_date": idx.min().date(),
                "last_date": idx.max().date(),
                "rows": len(df),
                "missing_days": missing_days,
                "max_gap_days": max_gap,
                "max_abs_return": round(max_ret, 4),
                "n_extreme": n_extreme,
                "n_nonpositive": n_nonpos,
                "n_zero_volume": n_zero_vol,
                "flags": "; ".join(flags),
            }
        )

    per_ticker = pd.DataFrame(rows).set_index("ticker").sort_index()
    return QualityReport(per_ticker, len(requested_list), missing)
