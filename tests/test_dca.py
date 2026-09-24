import numpy as np
import pandas as pd
import pytest

from alpha_finder.backtest.dca import (
    WeightPath, allocate, money_weighted_return, simulate, wednesday_schedule,
)


def calendar(start="2023-12-01", end="2026-01-31", closed=()):
    days = pd.bdate_range(start, end)
    return days.difference(pd.DatetimeIndex(closed))


# --- schedule -----------------------------------------------------------

def test_every_wednesday_2024_to_2025_is_105_purchases():
    days = calendar(closed=["2024-06-19", "2024-12-25", "2025-01-01"])
    sched = wednesday_schedule("2024-01-01", "2025-12-31", days)
    assert len(sched) == 105  # 52 Wednesdays in 2024 (from Jan 3) + 53 in 2025 (Jan 1 to Dec 31)
    assert sched[0] == pd.Timestamp("2024-01-03")
    assert sched[-1] == pd.Timestamp("2025-12-31")  # Dec 31, 2025 is itself a Wednesday


def test_closed_wednesday_moves_to_next_trading_day():
    days = calendar(closed=["2024-06-19", "2024-12-25", "2025-01-01"])
    sched = wednesday_schedule("2024-01-01", "2025-12-31", days)
    assert pd.Timestamp("2024-06-20") in sched and pd.Timestamp("2024-06-19") not in sched
    assert pd.Timestamp("2024-12-26") in sched
    assert pd.Timestamp("2025-01-02") in sched
    assert all(d.dayofweek in (2, 3) for d in sched)  # Wednesday, or Thursday after a holiday


def test_schedule_never_goes_past_end():
    days = calendar()
    sched = wednesday_schedule("2024-01-01", "2024-01-10", days)
    assert [d.date().isoformat() for d in sched] == ["2024-01-03", "2024-01-10"]
    assert wednesday_schedule("2024-01-04", "2024-01-09", days) == []


# --- allocation ---------------------------------------------------------

def test_top_n_split_in_proportion_to_weights():
    dollars = allocate(pd.Series({"A": 0.5, "B": 0.3, "C": 0.2}), n=2, amount=1000)
    assert dollars.to_dict() == pytest.approx({"A": 625.0, "B": 375.0})
    assert dollars.sum() == pytest.approx(1000)


def test_all_holdings_when_n_is_none():
    dollars = allocate(pd.Series({"A": 0.5, "B": 0.3, "C": 0.2}), n=None, amount=1000)
    assert dollars.to_dict() == pytest.approx({"A": 500.0, "B": 300.0, "C": 200.0})


def test_ties_break_alphabetically():
    dollars = allocate(pd.Series({"Z": 0.4, "B": 0.4, "A": 0.2}), n=1, amount=1000)
    assert list(dollars.index) == ["B"]


# --- hand-calculated purchases ------------------------------------------

def px_frame(prices: dict, dates):
    return pd.DataFrame(prices, index=pd.DatetimeIndex(dates))


def test_single_asset_hand_calculation():
    dates = ["2024-01-03", "2024-01-10", "2024-01-17"]
    adj = px_frame({"X": [100.0, 100.0, 200.0]}, dates)
    res = simulate(adj, lambda d: pd.Series({"X": 1.0}), [pd.Timestamp(d) for d in dates],
                   n=1, amount=1000, end="2024-01-17")
    assert res.shares["X"] == pytest.approx(10 + 10 + 5)
    assert res.invested == pytest.approx(3000)
    assert res.final_value == pytest.approx(25 * 200)  # 5,000
    assert res.value_curve.iloc[-1] == pytest.approx(res.final_value)
    assert res.purchases.to_numpy().sum() == pytest.approx(res.invested)


def test_top_n_versus_full_basket_two_assets():
    dates = ["2024-01-03", "2024-01-10"]
    adj = px_frame({"A": [100.0, 150.0], "B": [50.0, 50.0]}, dates)
    w = lambda d: pd.Series({"A": 0.75, "B": 0.25})
    ds = [pd.Timestamp(d) for d in dates]
    top1 = simulate(adj, w, ds, n=1, amount=1000, end="2024-01-10")
    full = simulate(adj, w, ds, n=None, amount=1000, end="2024-01-10")
    # top 1: all $2,000 in A: 10 shares at 100 + 6.667 at 150 = 16.667 shares -> 2,500
    assert top1.final_value == pytest.approx(2500.0)
    # full: A gets 750 then 750 (7.5 + 5 shares), B gets 250 each (5 + 5 shares)
    assert full.final_value == pytest.approx(12.5 * 150 + 10 * 50)  # 1,875 + 500 = 2,375
    assert top1.invested == full.invested == pytest.approx(2000)


def test_value_only_uses_prices_up_to_end_date():
    dates = ["2024-01-03", "2024-01-10", "2024-01-17"]
    ds = [pd.Timestamp(d) for d in dates]
    base = px_frame({"X": [100.0, 110.0, 120.0]}, dates)
    changed = px_frame({"X": [100.0, 110.0, 999.0]}, dates)
    a = simulate(base, lambda d: pd.Series({"X": 1.0}), ds[:2], n=1, amount=1000, end="2024-01-10")
    b = simulate(changed, lambda d: pd.Series({"X": 1.0}), ds[:2], n=1, amount=1000, end="2024-01-10")
    assert a.final_value == pytest.approx(b.final_value)


def test_missing_price_on_purchase_date_raises():
    dates = ["2024-01-03", "2024-01-10"]
    adj = px_frame({"X": [np.nan, 100.0], "Y": [10.0, 10.0]}, dates)  # nothing earlier to fill from
    with pytest.raises(ValueError):
        simulate(adj, lambda d: pd.Series({"X": 1.0}), [pd.Timestamp(d) for d in dates], n=1,
                 amount=1000, end="2024-01-10")


def test_short_price_gap_is_filled_from_previous_close():
    dates = ["2024-01-03", "2024-01-10"]
    adj = px_frame({"X": [100.0, np.nan]}, dates)  # one missing day: use the previous close
    res = simulate(adj, lambda d: pd.Series({"X": 1.0}), [pd.Timestamp(d) for d in dates], n=1,
                   amount=1000, end="2024-01-10")
    assert res.shares["X"] == pytest.approx(20.0)


# --- weights drift and snapshots ----------------------------------------

def test_weights_drift_with_price_changes():
    idx = pd.DatetimeIndex(["2024-01-02", "2024-01-09"])
    close = px_frame({"A": [100.0, 200.0], "B": [50.0, 50.0]}, idx)
    wp = WeightPath({pd.Timestamp("2024-01-02"): pd.Series({"A": 0.5, "B": 0.5})}, close)
    w = wp.at("2024-01-09")
    assert w["A"] == pytest.approx(2 / 3) and w["B"] == pytest.approx(1 / 3)


def test_new_snapshot_replaces_drifted_weights():
    idx = pd.DatetimeIndex(["2024-01-02", "2024-01-09", "2024-01-16"])
    close = px_frame({"A": [100.0, 200.0, 200.0], "B": [50.0, 50.0, 50.0]}, idx)
    wp = WeightPath({
        pd.Timestamp("2024-01-02"): pd.Series({"A": 0.5, "B": 0.5}),
        pd.Timestamp("2024-01-10"): pd.Series({"A": 0.6, "B": 0.4}),  # a fresh fund snapshot
    }, close)
    assert wp.at("2024-01-09")["A"] == pytest.approx(2 / 3)  # still the old snapshot, drifted
    assert wp.at("2024-01-16")["A"] == pytest.approx(0.6)  # new snapshot, no drift since it


def test_snapshot_on_a_weekend_uses_last_trading_close():
    idx = pd.DatetimeIndex(["2023-12-29", "2024-01-03"])  # Dec 31, 2023 is a Sunday
    close = px_frame({"A": [100.0, 110.0], "B": [100.0, 100.0]}, idx)
    wp = WeightPath({pd.Timestamp("2023-12-31"): pd.Series({"A": 0.5, "B": 0.5})}, close)
    w = wp.at("2024-01-03")
    assert w["A"] == pytest.approx(0.55 / (0.55 + 0.5))


def test_no_snapshot_before_first_date_is_an_error():
    idx = pd.DatetimeIndex(["2024-01-02"])
    close = px_frame({"A": [100.0]}, idx)
    wp = WeightPath({pd.Timestamp("2024-02-01"): pd.Series({"A": 1.0})}, close)
    with pytest.raises(ValueError):
        wp.at("2024-01-02")


def test_unpriced_tickers_are_dropped_and_reported():
    idx = pd.DatetimeIndex(["2024-01-02", "2024-01-09"])
    close = px_frame({"A": [100.0, 100.0], "B": [50.0, 50.0]}, idx)
    snap = pd.Timestamp("2024-01-02")
    wp = WeightPath({snap: pd.Series({"A": 0.6, "B": 0.3, "GONE": 0.1})}, close)
    w = wp.at("2024-01-09")
    assert "GONE" not in w.index
    assert w.sum() == pytest.approx(1.0)
    assert wp.dropped_weight[snap] == pytest.approx(0.1)


# --- return measure -----------------------------------------------------

def test_money_weighted_return_single_contribution():
    start, end = pd.Timestamp("2024-01-03"), pd.Timestamp("2025-01-02")  # 365 days
    r = money_weighted_return([start], [1000.0], 1100.0, end)
    assert r == pytest.approx(1.1 ** (365.25 / 365) - 1, rel=1e-6)


def test_money_weighted_return_flat_market_is_zero():
    dates = pd.date_range("2024-01-03", periods=10, freq="W-WED")
    assert money_weighted_return(dates, [1000.0] * 10, 10000.0, dates[-1]) == pytest.approx(0.0, abs=1e-9)


# --- corporate actions ----------------------------------------------------

from alpha_finder.backtest.dca import Acquisition, Spinoff, actual_prices, drift_prices


def test_actual_prices_undo_split_adjustment():
    idx = pd.DatetimeIndex(["2024-06-06", "2024-06-07", "2024-06-10"])
    close = pd.Series([120.0, 121.0, 122.0], index=idx)   # Yahoo: already divided by 10 before the split
    splits = pd.Series([0.0, 0.0, 10.0], index=idx)       # 10-for-1 on 2024-06-10
    assert list(actual_prices(close, splits)) == [1200.0, 1210.0, 122.0]


def test_drift_prices_keep_true_splits_but_undo_spinoffs():
    idx = pd.DatetimeIndex(["2025-02-21", "2025-02-24", "2025-02-25"])
    close = pd.Series([50.0, 52.0, 53.0], index=idx)       # Yahoo divided 2025-02-21 by 1.3 (a spin-off)
    splits = pd.Series([0.0, 1.3, 0.0], index=idx)
    drift = drift_prices(close, splits, [pd.Timestamp("2025-02-24")])
    assert list(drift) == pytest.approx([65.0, 52.0, 53.0])  # parent really fell from 65 to 52
    assert list(drift_prices(close, splits, [])) == [50.0, 52.0, 53.0]  # a true split: keep it adjusted


def _two_day_frames(parent_prev, parent_now, child_now, factor):
    idx = pd.DatetimeIndex(["2025-02-19", "2025-02-21", "2025-02-24", "2025-02-25"])
    actual = pd.DataFrame({"P": [parent_prev, parent_prev, parent_now, parent_now],
                           "C": [np.nan, np.nan, child_now, child_now]}, index=idx)
    adj = actual.copy()
    adj.loc[idx[:2], "P"] = parent_prev / factor              # Yahoo folds the spin into a split
    return idx, adj, actual


def test_spinoff_keeps_value_and_hands_out_child_shares():
    # Parent 70 -> 52.5 when 1 child share per 3 parent shares (child 52.5) is spun off: 70 = 52.5 + 52.5/3
    idx, adj, actual = _two_day_frames(70.0, 52.5, 52.5, factor=70.0 / 52.5)
    spin = Spinoff("P", "C", idx[2], 1 / 3)
    res = simulate(adj, lambda d: pd.Series({"P": 1.0}), [idx[0]], n=1, amount=700, end=idx[3],
                   actual=actual, actions=[spin])
    # 700 bought 10 parent shares; after the spin: 10 parent (525) + 3.333 child (175) = 700
    assert res.final_value == pytest.approx(700.0)
    assert res.shares["C"] * adj.at[idx[3], "C"] == pytest.approx(175.0)


def test_spinoff_then_child_rallies():
    idx, adj, actual = _two_day_frames(70.0, 52.5, 52.5, factor=70.0 / 52.5)
    actual.loc[idx[3], "C"] = 105.0                            # child doubles the next day
    adj.loc[idx[3], "C"] = 105.0
    spin = Spinoff("P", "C", idx[2], 1 / 3)
    res = simulate(adj, lambda d: pd.Series({"P": 1.0}), [idx[0]], n=1, amount=700, end=idx[3],
                   actual=actual, actions=[spin])
    assert res.final_value == pytest.approx(525.0 + 350.0)     # Yahoo's "split" view would say 700


def test_cash_and_stock_acquisition():
    idx = pd.DatetimeIndex(["2025-07-09", "2025-07-16", "2025-07-17", "2025-07-18"])
    actual = pd.DataFrame({"T": [360.0, 370.0, np.nan, np.nan], "A": [500.0, 500.0, 520.0, 530.0]}, index=idx)
    adj = actual.copy()
    deal = Acquisition("T", date=idx[2], last_trade=idx[1], cash=197.0, acquirer="A", ratio=0.345)
    res = simulate(adj, lambda d: pd.Series({"T": 1.0}), [idx[0]], n=1, amount=3600, end=idx[3],
                   actual=actual, actions=[deal])
    # 10 shares -> $1,970 cash + 3.45 A shares worth 530 each at the end
    assert res.cash == pytest.approx(1970.0)
    assert res.final_value == pytest.approx(1970.0 + 3.45 * 530.0)


def test_cannot_buy_after_last_trading_day():
    idx = pd.DatetimeIndex(["2024-03-13", "2024-03-15", "2024-03-18", "2024-03-20"])
    actual = pd.DataFrame({"T": [155.0, 156.0, np.nan, np.nan], "X": [10.0, 10.0, 10.0, 10.0]}, index=idx)
    deal = Acquisition("T", date=idx[2], last_trade=idx[1], cash=157.0)
    with pytest.raises(ValueError):
        simulate(actual, lambda d: pd.Series({"T": 1.0}), [idx[0], idx[3]], n=1, amount=100, end=idx[3],
                 actual=actual, actions=[deal])


# --- estimated prices for delisted stocks -------------------------------------

from alpha_finder.data.proxy_prices import estimate_prices, leave_one_out_errors, to_trading_days


def test_anchor_on_weekend_moves_to_friday():
    cal = pd.bdate_range("2023-12-25", "2024-01-05")
    moved = to_trading_days(pd.Series({pd.Timestamp("2023-12-31"): 100.0}), cal)
    assert moved.index[0] == pd.Timestamp("2023-12-29")


def test_estimate_hits_anchors_and_interpolates_in_percent_terms():
    cal = pd.bdate_range("2024-01-01", "2024-12-31")
    pts = pd.Series({cal[0]: 100.0, cal[100]: 400.0})
    est = estimate_prices(pts, cal)
    assert est[cal[0]] == pytest.approx(100.0) and est[cal[100]] == pytest.approx(400.0)
    assert est[cal[50]] == pytest.approx(200.0, rel=1e-3)  # halfway in log terms
    assert est[cal[200]] == pytest.approx(400.0)            # flat after the last anchor


def test_cash_deal_price_is_a_fraction_of_deal_value_and_stops_at_last_trade():
    cal = pd.bdate_range("2024-01-01", "2024-03-29")
    pts = pd.Series({cal[0]: 150.0, cal[40]: 155.0})
    est = estimate_prices(pts, cal, deal_value=pd.Series(157.0, index=cal), announced=pd.Timestamp("2023-09-21"),
                          last_trade=cal[50])
    assert est[cal[20]] == pytest.approx(152.5)
    assert est[cal[50]] == pytest.approx(155.0)
    assert est[cal[51:]].isna().all()


def test_stock_deal_estimate_follows_acquirer():
    cal = pd.bdate_range("2024-01-01", "2024-06-28")
    acquirer = pd.Series(np.linspace(500, 600, len(cal)), index=cal)
    value = 197 + 0.345 * acquirer
    pts = pd.Series({cal[10]: 0.9 * value[cal[10]], cal[100]: 0.9 * value[cal[100]]})
    est = estimate_prices(pts, cal, deal_value=value, announced=cal[5])
    assert est[cal[60]] == pytest.approx(0.9 * value[cal[60]])  # steady 10% spread, acquirer moving


def test_leave_one_out_is_zero_for_a_perfect_line():
    cal = pd.bdate_range("2024-01-01", "2024-12-31")
    pts = pd.Series({cal[i]: 100.0 * 1.001 ** i for i in (0, 60, 120, 180, 240)})
    assert leave_one_out_errors(pts, cal).abs().max() == pytest.approx(0.0, abs=1e-9)


def test_unexplained_delisting_is_an_error_in_strict_mode_and_cash_otherwise():
    idx = pd.DatetimeIndex(["2024-01-03", "2024-01-10", "2024-01-17"] + list(pd.bdate_range("2024-01-18", "2024-02-02")))
    adj = pd.DataFrame({"X": [100.0, 110.0] + [np.nan] * (len(idx) - 2), "Y": 10.0}, index=idx)
    w = lambda d: pd.Series({"X": 0.5, "Y": 0.5})
    with pytest.raises(ValueError):
        simulate(adj, w, [idx[0]], n=None, amount=1000, end=idx[-1])
    res = simulate(adj, w, [idx[0]], n=None, amount=1000, end=idx[-1], strict=False)
    assert res.final_value == pytest.approx(550.0 + 500.0)  # X cashed out at its last price, 110
    assert [c[0] for c in res.cashed_out] == ["X"]


def test_reference_series_shapes_path_between_anchors():
    cal = pd.bdate_range("2020-01-01", "2020-06-30")
    ref = pd.Series(100.0, index=cal)
    ref[cal[30]:cal[60]] = 70.0                                 # a crash between two anchors
    pts = pd.Series({cal[0]: 50.0, cal[100]: 50.0})             # stock = half the reference at both anchors
    est = estimate_prices(pts, cal, reference=ref)
    assert est[cal[45]] == pytest.approx(35.0)                  # follows the crash
    assert estimate_prices(pts, cal)[cal[45]] == pytest.approx(50.0)  # a straight line would miss it


def test_growing_weekly_amount():
    dates = [pd.Timestamp("2020-12-30"), pd.Timestamp("2021-01-06")]
    adj = px_frame({"X": [100.0, 100.0]}, dates)
    res = simulate(adj, lambda d: pd.Series({"X": 1.0}), dates, n=1,
                   amount=lambda d: 1000.0 * 1.15 ** (d.year - 2020), end=dates[-1])
    assert res.invested == pytest.approx(2150.0)
    assert list(res.purchases.sum(axis=1)) == pytest.approx([1000.0, 1150.0])


def test_index_event_switches_to_next_filing_drifted_back():
    idx = pd.DatetimeIndex(["2023-02-28", "2023-03-15", "2023-03-22", "2023-05-31"])
    close = px_frame({"V": [200.0, 200.0, 200.0, 220.0], "NVDA": [100.0, 110.0, 120.0, 150.0]}, idx)
    snaps = {pd.Timestamp("2023-02-28"): pd.Series({"V": 0.5, "NVDA": 0.5}),
             pd.Timestamp("2023-05-31"): pd.Series({"NVDA": 1.0})}          # V was reclassified out
    wp = WeightPath(snaps, close, events=[pd.Timestamp("2023-03-20")])
    assert wp.snapshot_date_for(pd.Timestamp("2023-03-15")) == pd.Timestamp("2023-02-28")  # before the event
    assert wp.snapshot_date_for(pd.Timestamp("2023-03-22")) == pd.Timestamp("2023-05-31")  # after it
    assert list(wp.at("2023-03-22").index) == ["NVDA"]


def test_deal_price_anchors_a_stock_that_left_the_fund():
    cal = pd.bdate_range("2023-01-02", "2025-08-29")
    pts = pd.Series({cal[0]: 30.0})                           # last real price, long before the deal
    ann, last = pd.Timestamp("2025-03-06"), pd.Timestamp("2025-08-27")
    est = estimate_prices(pts, cal, deal_value=pd.Series(11.45, index=cal), announced=ann, last_trade=last)
    assert est[cal[0]] == pytest.approx(30.0)
    assert est[pd.Timestamp("2025-03-06")] == pytest.approx(11.45)
    assert est[pd.Timestamp("2025-06-02")] == pytest.approx(11.45)
    mid = est[pd.Timestamp("2024-02-01")]
    assert 11.45 < mid < 30.0                                 # glides between the two, no flat 30 until the deal
    assert est[pd.Timestamp("2025-08-28"):].isna().all()
