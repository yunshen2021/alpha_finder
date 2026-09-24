import numpy as np
import pandas as pd
import pytest

from alpha_finder.backtest.engine import Account, BacktestConfig, build_panels, run_backtest
from alpha_finder.backtest.tax import Lot, TaxConfig, TaxLedger
from alpha_finder.signals.momentum import momentum_scores, month_end_dates, rank_universe

ST, LT = 0.38, 0.23


def make_prices(paths: dict[str, np.ndarray], start="2020-01-01", opens=None, divs=None):
    """Synthetic daily frames. `opens` defaults to the close; `divs` is {ticker: {index: amount}}."""
    n = len(next(iter(paths.values())))
    idx = pd.bdate_range(start, periods=n)
    out = {}
    for t, close in paths.items():
        open_ = close if opens is None or t not in opens else opens[t]
        d = np.zeros(n)
        for j, amt in (divs or {}).get(t, {}).items():
            d[j] = amt
        out[t] = pd.DataFrame(
            {"open": open_, "high": close, "low": close, "close": close, "adj_close": close,
             "volume": 1e6, "dividends": d, "splits": 0.0}, index=idx)
    return out, idx


def cfg(**kw):
    base = dict(name="t", cost_bps=0.0, tax=TaxConfig(ST, LT, LT), n_holdings=2, sell_rank=2,
                max_sector_frac=1.0, position_cap=1.0)
    base.update(kw)
    return BacktestConfig(**base)


# --- tax ledger ---------------------------------------------------------

def test_short_term_loss_offsets_long_term_gain():
    led = TaxLedger(TaxConfig(ST, LT, LT))
    led.realize(2021, 100.0, long_term=True)
    led.realize(2021, -40.0, long_term=False)
    assert led.tax_for_year(2021) == pytest.approx(60 * LT)


def test_loss_carries_forward_with_character():
    led = TaxLedger(TaxConfig(ST, LT, LT))
    led.realize(2021, -100.0, long_term=False)
    assert led.tax_for_year(2021) == 0.0
    led.realize(2022, 250.0, long_term=False)
    assert led.tax_for_year(2022) == pytest.approx(150 * ST)


def test_dividends_taxed_at_dividend_rate():
    led = TaxLedger(TaxConfig(ST, LT, 0.15))
    led.dividend(2021, 1000.0)
    assert led.tax_for_year(2021) == pytest.approx(150.0)


def test_long_term_needs_more_than_a_year():
    lot = Lot("A", 1, 100, pd.Timestamp("2020-01-02"))
    assert not lot.is_long_term(pd.Timestamp("2021-01-01"))  # 365 days
    assert lot.is_long_term(pd.Timestamp("2021-01-02"))  # 366 days


# --- buy and hold benchmark ---------------------------------------------

def bench_run(prices, idx, **kw):
    scores = pd.Series({"QQQ": 1.0})
    ranks = {idx[0]: scores}
    panels = build_panels(prices, idx)
    return run_backtest(cfg(mode="buy_hold", **kw), panels, ranks, {})


def test_buy_hold_long_term_liquidation_tax():
    close = np.r_[np.full(300, 100.0), np.full(300, 200.0)]  # doubles well after a year
    prices, idx = make_prices({"QQQ": close})
    res = bench_run(prices, idx)
    assert res.final_value == pytest.approx(2_000_000)
    assert res.final_after_tax == pytest.approx(2_000_000 - LT * 1_000_000)


def test_buy_hold_short_term_when_sold_inside_a_year():
    close = np.r_[np.full(100, 100.0), np.full(100, 200.0)]
    prices, idx = make_prices({"QQQ": close})
    res = bench_run(prices, idx)
    assert res.final_after_tax == pytest.approx(2_000_000 - ST * 1_000_000)


def test_costs_reduce_basis_and_proceeds():
    prices, idx = make_prices({"QQQ": np.full(50, 100.0)})
    res = bench_run(prices, idx, cost_bps=10.0, tax=TaxConfig.none())
    # 10 bps paid to buy, and 10 bps to liquidate
    assert res.final_after_tax == pytest.approx(1_000_000 / 1.001 * 0.999, rel=1e-6)


def test_dividend_tax_paid_from_dividends_each_year():
    n = 520
    close = np.full(n, 100.0)
    divs = {"QQQ": {j: 1.0 for j in range(20, n, 63)}}  # quarterly $1/share
    prices, idx = make_prices({"QQQ": close}, divs=divs)
    res = bench_run(prices, idx)
    shares = 1_000_000 / 100
    year1 = sum(1 for j in range(20, n, 63) if idx[j].year == idx[0].year) * shares
    assert res.ledger.dividends[idx[0].year] == pytest.approx(year1)
    assert res.tax_paid >= year1 * LT - 1e-6


# --- rank strategy execution --------------------------------------------

def test_signal_is_filled_at_next_open_not_same_close():
    n = 80
    close = {t: np.full(n, 100.0) for t in "ABC"}
    opens = {"A": np.full(n, 50.0), "B": np.full(n, 60.0), "C": np.full(n, 70.0)}
    prices, idx = make_prices(close, opens=opens)
    ranks = {idx[10]: pd.Series({"A": 1.0, "B": 2.0, "C": 3.0})}
    res = run_backtest(cfg(), build_panels(prices, idx), ranks, {})
    buys = res.trades[res.trades.side == "buy"]
    assert set(buys.ticker) == {"A", "B"}
    assert (buys.date == idx[11]).all()  # next trading day, never the signal day
    px = dict(zip(buys.ticker, buys.price))
    assert px == {"A": 50.0, "B": 60.0}  # opens, not closes


def test_buffer_keeps_incumbent_and_replaces_only_failures():
    n = 120
    close = {t: np.full(n, 100.0) for t in "ABCD"}
    prices, idx = make_prices(close)
    ranks = {
        idx[10]: pd.Series({"A": 1.0, "B": 2.0, "C": 3.0, "D": 4.0}),
        idx[40]: pd.Series({"C": 1.0, "A": 3.0, "B": 2.0, "D": 4.0}),  # A slips to 3
        idx[70]: pd.Series({"C": 1.0, "D": 2.0, "B": 3.0, "A": 4.0}),  # B slips to 3
    }
    res = run_backtest(cfg(sell_rank=3, n_holdings=2), build_panels(prices, idx), ranks, {})
    sells = res.trades[res.trades.side == "sell"]
    assert len(sells) == 1 and sells.iloc[0].ticker == "A"  # only A fell past rank 3 (to 4)


def test_forced_sell_when_not_in_universe():
    n = 80
    close = {t: np.full(n, 100.0) for t in "ABC"}
    prices, idx = make_prices(close)
    ranks = {
        idx[10]: pd.Series({"A": 1.0, "B": 2.0, "C": 3.0}),
        idx[40]: pd.Series({"B": 1.0, "C": 2.0}),  # A left the index
    }
    res = run_backtest(cfg(sell_rank=10), build_panels(prices, idx), ranks, {})
    forced = res.trades[(res.trades.side == "sell") & (res.trades.term == "forced")]
    assert list(forced.ticker) == ["A"]


def test_delisted_holding_is_sold_at_last_close():
    n = 80
    a = np.full(n, 100.0)
    a[40:] = np.nan
    close = {"A": a, "B": np.full(n, 100.0)}
    prices, idx = make_prices({"A": np.full(n, 100.0), "B": np.full(n, 100.0)})
    prices["A"] = prices["A"].iloc[:40]
    prices["A"].loc[prices["A"].index[-1], ["open", "close", "adj_close"]] = 30.0  # collapsed
    ranks = {idx[10]: pd.Series({"A": 1.0, "B": 2.0})}
    res = run_backtest(cfg(sell_rank=10), build_panels(prices, idx), ranks, {})
    delisted = res.trades[res.trades.term == "delisted"]
    assert list(delisted.ticker) == ["A"]
    assert delisted.iloc[0].price == 30.0
    assert delisted.iloc[0].gain < 0


def test_position_cap_trims_winner():
    n = 120
    a = np.r_[np.full(30, 100.0), np.full(90, 400.0)]
    close = {"A": a, "B": np.full(n, 100.0), "C": np.full(n, 100.0), "D": np.full(n, 100.0),
             "E": np.full(n, 100.0), "F": np.full(n, 100.0), "G": np.full(n, 100.0),
             "H": np.full(n, 100.0), "I": np.full(n, 100.0), "J": np.full(n, 100.0)}
    prices, idx = make_prices(close)
    r = {t: float(i + 1) for i, t in enumerate(close)}
    ranks = {idx[5]: pd.Series(r), idx[60]: pd.Series(r)}
    res = run_backtest(cfg(n_holdings=10, sell_rank=20, position_cap=0.10),
                       build_panels(prices, idx), ranks, {})
    trims = res.trades[res.trades.term == "trim"]
    assert list(trims.ticker) == ["A"]


# --- wash sale ----------------------------------------------------------

def test_wash_sale_defers_loss_into_new_lot():
    prices, idx = make_prices({"A": np.full(80, 100.0)})
    acct = Account(cfg(), build_panels(prices, idx))
    acct.buy(0, "A", 10_000, 100.0)  # 100 shares
    acct.sell(10, "A", 100, 80.0)  # realize -$2,000
    assert acct.ledger.st_gains[idx[10].year] == pytest.approx(-2000)
    acct.buy(20, "A", 8_200, 82.0)  # 100 shares, 10 days later
    assert acct.ledger.st_gains[idx[10].year] == pytest.approx(0)  # loss reversed
    lot = acct.lots["A"][0]
    assert lot.basis == pytest.approx(8_200 + 2_000)  # loss moved into basis
    assert lot.acquired == idx[20] - (idx[10] - idx[0])  # holding period tacked


def test_no_wash_sale_after_31_days():
    prices, idx = make_prices({"A": np.full(120, 100.0)})
    acct = Account(cfg(), build_panels(prices, idx))
    acct.buy(0, "A", 10_000, 100.0)
    acct.sell(10, "A", 100, 80.0)
    acct.buy(60, "A", 8_200, 82.0)  # well outside 30 days
    assert acct.ledger.st_gains[idx[10].year] == pytest.approx(-2000)
    assert acct.lots["A"][0].basis == pytest.approx(8_200)


def test_tax_gate_defers_gain_near_one_year_mark():
    n = 420
    a = np.r_[np.full(40, 100.0), np.full(n - 40, 150.0)]  # bought at 100 on day ~1, up 50%
    close = {"A": a, "B": np.full(n, 100.0), "C": np.full(n, 100.0)}
    prices, idx = make_prices(close)
    day_buy = 1
    sell_signal = int(np.searchsorted(idx, idx[day_buy] + pd.Timedelta(days=330)))  # 35 days short
    ranks = {idx[0]: pd.Series({"A": 1.0, "B": 2.0, "C": 3.0}),
             idx[sell_signal]: pd.Series({"B": 1.0, "C": 2.0, "A": 3.0})}
    plain = run_backtest(cfg(sell_rank=2, tax_gate=False), build_panels(prices, idx), ranks, {})
    gated = run_backtest(cfg(sell_rank=2, tax_gate=True), build_panels(prices, idx), ranks, {})
    assert (plain.trades.term == "rank").sum() == 1  # sold immediately
    assert (gated.trades.term == "rank").sum() == 0  # held past the signal


# --- signal hygiene -----------------------------------------------------

def test_momentum_scores_use_no_future_data():
    rng = np.random.default_rng(1)
    idx = pd.bdate_range("2018-01-01", periods=900)
    px = pd.DataFrame(100 * np.cumprod(1 + rng.normal(0, 0.01, (900, 5)), axis=0), index=idx,
                      columns=list("ABCDE"))
    dates = month_end_dates(idx)
    full = momentum_scores(px, dates)
    cut = idx[600]
    trunc = momentum_scores(px.loc[:cut], month_end_dates(px.loc[:cut].index))
    common = trunc.index[:-1]  # last truncated date is not a true month end
    pd.testing.assert_frame_equal(full.loc[common], trunc.loc[common])


def test_momentum_skips_latest_month():
    idx = pd.bdate_range("2019-01-01", periods=400)
    px = pd.DataFrame({"A": np.linspace(100, 200, 400)}, index=idx)
    dates = month_end_dates(idx)
    s = momentum_scores(px, dates)
    d = dates[14]
    month = px["A"].reindex(dates)
    assert s.loc[d, "A"] == pytest.approx(month.iloc[13] / month.iloc[2] - 1)


def test_rank_universe_respects_membership():
    dates = pd.DatetimeIndex(["2020-01-31"])
    scores = pd.DataFrame({"A": [0.3], "B": [0.5], "C": [0.1]}, index=dates)
    ranks = rank_universe(scores, lambda d: {"A", "C"})
    assert ranks[dates[0]].to_dict() == {"A": 1.0, "C": 2.0}


# --- development / holdout separation -----------------------------------

def _random_market(n_days=700, n_tickers=60, seed=3, tweak_after=None):
    """Random prices; optionally scramble everything after index `tweak_after`."""
    rng = np.random.default_rng(seed)
    paths = {f"T{i:02d}": 100 * np.cumprod(1 + rng.normal(0.0004, 0.015, n_days))
             for i in range(n_tickers)}
    if tweak_after is not None:
        for t in paths:
            paths[t] = paths[t].copy()
            paths[t][tweak_after + 1:] *= rng.uniform(0.2, 5.0)  # wildly different future
    return make_prices(paths)


def test_signal_diagnostics_ignore_data_after_end():
    from alpha_finder.signals.evaluate import signal_diagnostics

    cut = 600
    out = []
    for tweak in (None, cut):
        prices, idx = _random_market(tweak_after=tweak)
        adj = pd.DataFrame({t: df["adj_close"] for t, df in prices.items()})
        dates = month_end_dates(idx)
        ranks = rank_universe(momentum_scores(adj, dates), lambda d: set(adj.columns))
        out.append(signal_diagnostics(ranks, adj, dates, end=idx[cut]))
    pd.testing.assert_frame_equal(out[0], out[1])
    assert out[0].index.max() < month_end_dates(idx)[month_end_dates(idx) <= idx[cut]][-1]


def test_backtest_ignores_prices_after_end():
    cut = 600
    results = []
    for tweak in (None, cut):
        prices, idx = _random_market(tweak_after=tweak)
        adj = pd.DataFrame({t: df["adj_close"] for t, df in prices.items()})
        dates = month_end_dates(idx)
        ranks = rank_universe(momentum_scores(adj, dates), lambda d: set(adj.columns))
        panels = build_panels(prices, idx)
        res = run_backtest(cfg(n_holdings=10, sell_rank=20, position_cap=0.2), panels, ranks, {},
                           end=idx[cut])
        bench = run_backtest(cfg(mode="buy_hold", n_holdings=1, benchmark_ticker="T00"), panels, ranks, {},
                             end=idx[cut])
        results.append((res, bench))
    for a, b in zip(*results):
        assert a.final_after_tax == pytest.approx(b.final_after_tax)
        pd.testing.assert_series_equal(a.equity, b.equity)
