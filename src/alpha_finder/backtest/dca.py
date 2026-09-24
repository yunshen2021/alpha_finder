"""Dollar-cost averaging arithmetic: buy a fixed amount on a schedule, never sell, value at the end.

Pure calculation, no taxes. Used to compare "buy the whole ETF" with "buy only its top N holdings,
split in proportion to their fund weights".

Conventions (all fixed here so results are reproducible):
- Purchases happen on the close of every Wednesday. If the market is closed that Wednesday, the next
  trading day is used.
- Prices are Yahoo `adj_close` (split- and dividend-adjusted), so dividends are treated as reinvested.
  Fund weights drift with the unadjusted `close` (that is what market-cap weights do).
- Fractional shares are allowed. Nothing is ever sold.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.optimize import brentq


def wednesday_schedule(start, end, trading_days) -> list[pd.Timestamp]:
    """Every Wednesday in [start, end], moved to the next trading day when the market is closed."""
    days = pd.DatetimeIndex(trading_days)
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    out = []
    for wed in pd.date_range(start, end, freq="W-WED"):
        pos = days.searchsorted(wed)  # first trading day on or after that Wednesday
        if pos >= len(days):
            break
        if days[pos] <= end:
            out.append(days[pos])
    return out


def allocate(weights: pd.Series, n: int | None, amount: float) -> pd.Series:
    """Dollars per ticker: the top `n` by weight, split in proportion to their weights.

    Ties are broken alphabetically so the choice is deterministic. n=None means every holding.
    """
    w = weights[weights > 0]
    w = w.sort_index().sort_values(ascending=False, kind="stable")
    if n is not None:
        w = w.iloc[:n]
    return amount * w / w.sum()


@dataclass(frozen=True)
class Spinoff:
    """Holders of `parent` receive `ratio` shares of `child` per share, on the ex-date `date`."""

    parent: str
    child: str
    date: pd.Timestamp
    ratio: float


@dataclass(frozen=True)
class Acquisition:
    """`target` is bought out on `date`: holders get `cash` per share plus `ratio` shares of `acquirer`.

    `last_trade` is the target's last trading day. The cash then sits uninvested (buy-only rule).
    """

    target: str
    date: pd.Timestamp
    last_trade: pd.Timestamp
    cash: float
    acquirer: str | None = None
    ratio: float = 0.0


def actual_prices(close: pd.Series, splits: pd.Series) -> pd.Series:
    """Undo Yahoo's split adjustment: the price that actually traded on each day.

    Yahoo divides every close before a split by the split ratio; it also books spin-offs as
    "splits" with odd ratios. Multiplying back by all later ratios recovers the traded price.
    """
    ratios = splits.reindex(close.index).fillna(0.0).replace(0.0, 1.0)
    later = ratios[::-1].cumprod()[::-1].shift(-1).fillna(1.0)  # product over dates strictly after t
    return close * later


def drift_prices(close: pd.Series, splits: pd.Series, spin_dates: list[pd.Timestamp]) -> pd.Series:
    """Split-adjusted close with spin-offs NOT folded in: the right series for market-cap drift.

    After a spin-off the parent is genuinely smaller (the child is a separate company), so its
    weight should drop; Yahoo's close hides that by treating the spin as a split.
    """
    ratios = splits.reindex(close.index).fillna(0.0).replace(0.0, 1.0)
    spin_only = pd.Series(1.0, index=close.index)
    for d in spin_dates:
        if d in spin_only.index:
            spin_only[d] = ratios[d]
    later = spin_only[::-1].cumprod()[::-1].shift(-1).fillna(1.0)
    return close * later


@dataclass
class WeightPath:
    """A fund's weights over time from periodic holdings snapshots.

    On any date the latest snapshot on or before it is used, and each weight is scaled by that
    stock's price change since the snapshot (buy-and-hold drift), then renormalized. Stocks with
    no price on the date are dropped and the rest renormalized; `dropped_weight` records how much
    weight that removed at each snapshot, so it is visible instead of silent.
    """

    snapshots: dict[pd.Timestamp, pd.Series]
    close: pd.DataFrame  # unadjusted (split-adjusted) closes, date x ticker
    dropped_weight: dict[pd.Timestamp, float] = field(default_factory=dict)
    # Index changes that happened between two filings (a sector reclassification, a special
    # rebalance). On and after such a date, the next filing is used, drifted back in time, because
    # it already reflects the change while the earlier filing does not.
    events: list = field(default_factory=list)

    def __post_init__(self):
        self.snapshots = {pd.Timestamp(k): v / v.sum() for k, v in sorted(self.snapshots.items())}
        self.events = [pd.Timestamp(e) for e in self.events]

    def snapshot_date_for(self, date: pd.Timestamp) -> pd.Timestamp:
        date = pd.Timestamp(date)
        eligible = [s for s in self.snapshots if s <= date]
        if not eligible:
            raise ValueError(f"no holdings snapshot on or before {date.date()}")
        prev = eligible[-1]
        if any(prev < e <= date for e in self.events):
            later = [s for s in self.snapshots if s > date]
            if later:
                return later[0]
        return prev

    def at(self, date, snapshot=None) -> pd.Series:
        """Weights on `date`, drifted from `snapshot` (default: the latest one on or before `date`)."""
        date = pd.Timestamp(date)
        snap = pd.Timestamp(snapshot) if snapshot is not None else self.snapshot_date_for(date)
        base = self.snapshots[snap]
        px_pos = self.close.index.searchsorted(snap, side="right") - 1  # last trading day on/before snapshot
        px_snap = self.close.iloc[px_pos]
        px_now = self.close.loc[date]
        cols = [t for t in base.index if t in self.close.columns]
        ok = [t for t in cols if pd.notna(px_snap[t]) and pd.notna(px_now[t]) and px_snap[t] > 0]
        self.dropped_weight[snap] = float(1.0 - base[ok].sum())
        drifted = base[ok] * (px_now[ok] / px_snap[ok])
        return drifted / drifted.sum()


@dataclass
class DcaResult:
    invested: float
    final_value: float
    n_purchases: int
    shares: pd.Series  # final shares per ticker
    value_curve: pd.Series  # portfolio value on every trading day from the first purchase
    purchases: pd.DataFrame  # dollars bought per purchase date x ticker
    dates: list[pd.Timestamp]
    end: pd.Timestamp
    cash: float = 0.0  # uninvested cash from cash buyouts
    cashed_out: list = field(default_factory=list)  # (ticker, date, price) for unexplained delistings

    @property
    def gain(self) -> float:
        return self.final_value - self.invested

    @property
    def multiple(self) -> float:
        return self.final_value / self.invested

    def irr(self, amount: float | None = None) -> float:
        """Annualized money-weighted return of the weekly contributions."""
        return money_weighted_return(self.dates, self.purchases.sum(axis=1).to_numpy(), self.final_value, self.end)


def money_weighted_return(dates, amounts, final_value: float, end) -> float:
    """Annual rate r with sum(amount_i * (1+r)^(years from date_i to end)) = final_value."""
    end = pd.Timestamp(end)
    years = np.array([(end - pd.Timestamp(d)).days / 365.25 for d in dates])
    amounts = np.asarray(amounts, dtype=float)

    def f(r):
        return float(np.sum(amounts * (1 + r) ** years) - final_value)

    return float(brentq(f, -0.95, 20.0))


def simulate(adj_close: pd.DataFrame, weights_at, dates: list[pd.Timestamp], n: int | None,
             amount: float, end, actual: pd.DataFrame | None = None,
             actions: list | tuple = (), strict: bool = True) -> DcaResult:
    """Buy `amount` on each date across the top `n` holdings; value everything on `end`.

    `weights_at(date)` returns the fund's weights on that date (a Series by ticker).
    `actual` holds traded (unadjusted) prices, needed only when `actions` are given.
    Corporate actions are applied at the start of their day, before that day's purchase.

    strict=True: a held stock that stops trading without a matching corporate action is an error
    (so every such event is looked at). strict=False: it is turned into cash at its last price and
    listed in `cashed_out` (used only for the all-holdings sanity check).
    """
    end = pd.Timestamp(end)
    px = adj_close.ffill(limit=5)
    days = px.loc[dates[0]:end].index
    buy_on = set(dates)
    events: dict[pd.Timestamp, list] = {}
    for a in actions:
        events.setdefault(pd.Timestamp(a.date), []).append(a)
    gone = {a.target: pd.Timestamp(a.last_trade) for a in actions if isinstance(a, Acquisition)}

    units: dict[str, float] = {}
    cash = 0.0
    cashed_out: list[tuple] = []
    buys: dict[pd.Timestamp, pd.Series] = {}
    values = {}
    prev = None
    for d in days:
        for a in events.get(d, []):
            cash += _apply(a, units, px, actual, d, prev)
        if d in buy_on:
            dollars = allocate(weights_at(d), n, amount(d) if callable(amount) else amount)
            late = [t for t in dollars.index if t in gone and d > gone[t]]
            if late:
                raise ValueError(f"{d.date()}: cannot buy {late} after their last trading day")
            price = px.loc[d, dollars.index]
            if price.isna().any():
                raise ValueError(f"no price on {d.date()} for {list(price.index[price.isna()])}")
            for t, v in dollars.items():
                units[t] = units.get(t, 0.0) + v / price[t]
            buys[d] = dollars
        held = [t for t, u in units.items() if u > 0]
        mark = px.loc[d, held]
        if mark.isna().any():
            missing = list(mark.index[mark.isna()])
            if strict:
                raise ValueError(f"no price on {d.date()} to value {missing}")
            for t in missing:  # stopped trading: cash out at the last traded price
                last_px = adj_close[t].loc[:d].dropna().iloc[-1]
                cash += units.pop(t) * last_px
                cashed_out.append((t, d, last_px))
            held = [t for t in held if t not in missing]
            mark = px.loc[d, held]
        values[d] = cash + float((mark * pd.Series(units, dtype=float)[held]).sum())
        prev = d

    purchases = pd.DataFrame(buys).T.fillna(0.0).sort_index()
    last = days[-1]
    shares = pd.Series(units, dtype=float)
    return DcaResult(
        invested=float(purchases.to_numpy().sum()), final_value=float(values[last]),
        n_purchases=len(buys), shares=shares, value_curve=pd.Series(values),
        purchases=purchases, dates=list(buys), end=last, cash=cash, cashed_out=cashed_out,
    )


def _apply(action, units: dict[str, float], px: pd.DataFrame, actual: pd.DataFrame | None,
           day: pd.Timestamp, prev: pd.Timestamp | None) -> float:
    """Apply one corporate action to `units` in place. Returns cash received."""
    if actual is None:
        raise ValueError("corporate actions need actual (unadjusted) prices")
    if isinstance(action, Spinoff):
        u = units.get(action.parent, 0.0)
        if u <= 0 or prev is None:
            return 0.0
        value_before = u * px.at[prev, action.parent]                 # dollars, correct before the spin
        shares = value_before / actual.at[prev, action.parent]         # effective shares held
        units[action.parent] = shares * actual.at[day, action.parent] / px.at[day, action.parent]
        child_value = shares * action.ratio * actual.at[day, action.child]
        units[action.child] = units.get(action.child, 0.0) + child_value / px.at[day, action.child]
        return 0.0
    if isinstance(action, Acquisition):
        u = units.pop(action.target, 0.0)
        if u <= 0:
            return 0.0
        last = pd.Timestamp(action.last_trade)
        shares = u * px.at[last, action.target] / actual.at[last, action.target]
        if action.acquirer:
            got = shares * action.ratio * actual.at[day, action.acquirer]
            units[action.acquirer] = units.get(action.acquirer, 0.0) + got / px.at[day, action.acquirer]
        return shares * action.cash
    raise TypeError(f"unknown corporate action {action!r}")
