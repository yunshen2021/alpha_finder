"""Estimated daily prices for stocks Yahoo no longer carries (acquired or taken private).

The anchor points are real: each fund's N-PORT filing states the value and share count of every
holding, so value / shares is the price the fund used on that quarter-end. Between anchors:

- before a buyout is announced, the stock's price relative to a reference series (its ETF) is
  interpolated in log terms, and multiplied by the reference's daily price. Market-wide moves
  between anchors (such as the March 2020 crash) are then captured instead of drawn as a line;
- after it is announced, the stock trades at a fairly steady fraction of the deal's value
  (cash plus any acquirer shares), so that fraction is interpolated instead and multiplied by the
  deal's daily value. For a cash deal this is close to flat; for a stock deal it follows the
  acquirer's price.

`leave_one_out_errors` measures how good this is: drop each real anchor in turn, estimate it from
the others, and compare.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def to_trading_days(points: pd.Series, calendar: pd.DatetimeIndex) -> pd.Series:
    """Move each dated price to the last trading day on or before it (quarter-ends fall on weekends)."""
    pos = calendar.searchsorted(pd.DatetimeIndex(points.index), side="right") - 1
    moved = pd.Series(points.to_numpy(), index=calendar[pos])
    return moved.groupby(level=0).mean().sort_index()


def estimate_prices(points: pd.Series, calendar: pd.DatetimeIndex, *, deal_value: pd.Series | None = None,
                    announced: pd.Timestamp | None = None, last_trade: pd.Timestamp | None = None,
                    reference: pd.Series | None = None) -> pd.Series:
    """Daily price estimate on `calendar` (NaN after `last_trade`).

    `reference` (optional, e.g. the ETF's own price) shapes the path between anchors before any deal.
    """
    points = to_trading_days(points.dropna(), calendar)
    days = calendar if last_trade is None else calendar[calendar <= pd.Timestamp(last_trade)]
    out = pd.Series(np.nan, index=calendar)
    cut = pd.Timestamp(announced) if announced is not None else None

    pre_days = days if cut is None else days[days < cut]
    pre = points if cut is None else points[points.index < cut]
    if cut is not None and deal_value is not None and not (points.index >= cut).any():
        # No real price after the deal was announced (the stock had left the fund): the deal price is the
        # best anchor. It ends the pre-deal path and is used from the announcement on.
        on = days[days >= cut]
        if len(on):
            value = deal_value.reindex(calendar).ffill()
            points = pd.concat([points, pd.Series({on[0]: float(value[on[0]])})]).sort_index()
            pre = pd.concat([pre, pd.Series({on[0]: float(value[on[0]])})]).sort_index()
    if len(pre_days):
        base = pre if len(pre) else points.iloc[:1]
        if reference is not None:
            ref = reference.reindex(calendar).ffill()
            rel = np.log(base.to_numpy() / ref.reindex(base.index).to_numpy())
            out.loc[pre_days] = np.exp(np.interp(pre_days.asi8, base.index.asi8, rel)) * ref.loc[pre_days]
        else:
            out.loc[pre_days] = np.exp(np.interp(pre_days.asi8, base.index.asi8, np.log(base.to_numpy())))

    if cut is not None:
        post_days = days[days >= cut]
        post = points[points.index >= cut]
        if len(post_days) and len(post):
            value = deal_value.reindex(calendar).ffill()
            frac = post / value.reindex(post.index)
            out.loc[post_days] = np.interp(post_days.asi8, frac.index.asi8, frac.to_numpy()) * value.loc[post_days]
    return out


def leave_one_out_errors(points: pd.Series, calendar: pd.DatetimeIndex, **kwargs) -> pd.Series:
    """For each anchor that has neighbours on both sides, the % error of estimating it without itself."""
    pts = to_trading_days(points.dropna(), calendar)
    errs = {}
    for d in pts.index[1:-1]:
        est = estimate_prices(pts.drop(d), calendar, **kwargs).get(d)
        if est is not None and np.isfinite(est):
            errs[d] = est / pts[d] - 1
    return pd.Series(errs, dtype=float)
