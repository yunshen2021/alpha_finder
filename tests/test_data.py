from datetime import date

import numpy as np
import pandas as pd
import pytest

from alpha_finder.data.benchmark import load_benchmark
from alpha_finder.data.prices import COLUMNS, load_prices, to_panel
from alpha_finder.data.quality import quality_report


def make_frame(start="2020-01-01", end="2020-12-31", price=100.0, seed=0) -> pd.DataFrame:
    idx = pd.bdate_range(start, end)
    rng = np.random.default_rng(seed)
    close = price * np.cumprod(1 + rng.normal(0, 0.01, len(idx)))
    df = pd.DataFrame(
        {
            "open": close, "high": close * 1.01, "low": close * 0.99,
            "close": close, "adj_close": close, "volume": 1_000_000.0,
            "dividends": 0.0, "splits": 0.0,
        },
        index=idx,
    )
    df.index.name = "date"
    return df


class FakeFetcher:
    """Returns synthetic data for known tickers and records every call."""

    def __init__(self, known):
        self.known = set(known)
        self.calls = []

    def __call__(self, tickers, start, end):
        self.calls.append((list(tickers), start, end))
        return {
            t: make_frame(start.isoformat(), end.isoformat(), seed=i)
            for i, t in enumerate(tickers)
            if t in self.known
        }


def test_load_returns_expected_columns_and_range(tmp_path):
    f = FakeFetcher(["AAA"])
    out = load_prices(["AAA"], "2020-01-01", "2020-12-31", cache_dir=tmp_path, fetcher=f)
    assert list(out["AAA"].columns) == COLUMNS
    assert out["AAA"].index.min() >= pd.Timestamp("2020-01-01")
    assert out["AAA"].index.max() <= pd.Timestamp("2020-12-31")


def test_second_load_hits_cache(tmp_path):
    f = FakeFetcher(["AAA"])
    first = load_prices(["AAA"], "2020-01-01", "2020-12-31", cache_dir=tmp_path, fetcher=f)
    second = load_prices(["AAA"], "2020-01-01", "2020-12-31", cache_dir=tmp_path, fetcher=f)
    assert len(f.calls) == 1
    pd.testing.assert_frame_equal(first["AAA"], second["AAA"], check_freq=False)


def test_subrange_served_from_cache(tmp_path):
    f = FakeFetcher(["AAA"])
    load_prices(["AAA"], "2020-01-01", "2020-12-31", cache_dir=tmp_path, fetcher=f)
    sub = load_prices(["AAA"], "2020-03-01", "2020-06-30", cache_dir=tmp_path, fetcher=f)
    assert len(f.calls) == 1
    assert sub["AAA"].index.min() >= pd.Timestamp("2020-03-01")
    assert sub["AAA"].index.max() <= pd.Timestamp("2020-06-30")


def test_wider_range_refetches(tmp_path):
    f = FakeFetcher(["AAA"])
    load_prices(["AAA"], "2020-03-01", "2020-06-30", cache_dir=tmp_path, fetcher=f)
    load_prices(["AAA"], "2020-01-01", "2020-12-31", cache_dir=tmp_path, fetcher=f)
    assert len(f.calls) == 2


def test_refresh_forces_fetch(tmp_path):
    f = FakeFetcher(["AAA"])
    load_prices(["AAA"], "2020-01-01", "2020-12-31", cache_dir=tmp_path, fetcher=f)
    load_prices(["AAA"], "2020-01-01", "2020-12-31", cache_dir=tmp_path, fetcher=f, refresh=True)
    assert len(f.calls) == 2


def test_only_uncached_tickers_are_fetched(tmp_path):
    f = FakeFetcher(["AAA", "BBB"])
    load_prices(["AAA"], "2020-01-01", "2020-12-31", cache_dir=tmp_path, fetcher=f)
    out = load_prices(["AAA", "BBB"], "2020-01-01", "2020-12-31", cache_dir=tmp_path, fetcher=f)
    assert f.calls[1][0] == ["BBB"]
    assert list(out) == ["AAA", "BBB"]


def test_missing_ticker_omitted_not_cached(tmp_path):
    f = FakeFetcher(["AAA"])
    out = load_prices(["AAA", "ZZZ"], "2020-01-01", "2020-12-31", cache_dir=tmp_path, fetcher=f)
    assert list(out) == ["AAA"]
    assert not (tmp_path / "prices" / "ZZZ.parquet").exists()


def test_corrupt_cache_is_refetched(tmp_path):
    f = FakeFetcher(["AAA"])
    load_prices(["AAA"], "2020-01-01", "2020-12-31", cache_dir=tmp_path, fetcher=f)
    (tmp_path / "prices" / "AAA.parquet").write_bytes(b"not parquet")
    out = load_prices(["AAA"], "2020-01-01", "2020-12-31", cache_dir=tmp_path, fetcher=f)
    assert len(f.calls) == 2
    assert not out["AAA"].empty


def test_end_defaults_to_today(tmp_path):
    f = FakeFetcher(["AAA"])
    load_prices(["AAA"], "2020-01-01", cache_dir=tmp_path, fetcher=f)
    assert f.calls[0][2] == date.today()


def test_to_panel_is_wide(tmp_path):
    f = FakeFetcher(["AAA", "BBB"])
    prices = load_prices(["AAA", "BBB"], "2020-01-01", "2020-12-31", cache_dir=tmp_path, fetcher=f)
    panel = to_panel(prices)
    assert list(panel.columns) == ["AAA", "BBB"]
    assert panel.index.is_monotonic_increasing


def test_benchmark_adds_returns(tmp_path):
    f = FakeFetcher(["QQQ"])
    bench = load_benchmark("2020-01-01", "2020-12-31", cache_dir=tmp_path, fetcher=f)
    assert bench["return"].iloc[0] != bench["return"].iloc[0]  # first is NaN
    expected = bench["adj_close"].pct_change().iloc[1:]
    pd.testing.assert_series_equal(bench["return"].iloc[1:], expected, check_names=False)


def test_benchmark_missing_raises(tmp_path):
    with pytest.raises(RuntimeError):
        load_benchmark("2020-01-01", "2020-12-31", cache_dir=tmp_path, fetcher=FakeFetcher([]))


def test_quality_clean_data_has_no_flags():
    prices = {"AAA": make_frame(seed=1), "BBB": make_frame(seed=2)}
    report = quality_report(prices, ["AAA", "BBB"], start="2020-01-01")
    assert report.missing == []
    assert report.flagged.empty


def test_quality_reports_missing_tickers():
    report = quality_report({"AAA": make_frame()}, ["AAA", "ZZZ"])
    assert report.missing == ["ZZZ"]
    assert "ZZZ" in report.summary()


def test_quality_flags_extreme_return():
    bad = make_frame(seed=1)
    bad.iloc[100:, bad.columns.get_indexer(["adj_close"])] *= 0.4  # 60% one-day drop
    report = quality_report({"AAA": make_frame(seed=2), "BAD": bad}, start="2020-01-01")
    assert "extreme_return" in report.per_ticker.loc["BAD", "flags"]
    assert report.per_ticker.loc["AAA", "flags"] == ""


def test_quality_flags_gap_and_missing_days():
    gappy = make_frame(seed=1).drop(make_frame().index[50:70])
    report = quality_report({"AAA": make_frame(seed=2), "GAP": gappy, "CCC": make_frame(seed=3)})
    flags = report.per_ticker.loc["GAP", "flags"]
    assert "gap" in flags and "missing_days" in flags


def test_quality_late_start_flag():
    late = make_frame(start="2020-06-01", seed=1)
    report = quality_report({"AAA": make_frame(seed=2), "LATE": late}, start="2020-01-01")
    assert "late_start" in report.per_ticker.loc["LATE", "flags"]
    assert "late_start" not in report.per_ticker.loc["AAA", "flags"]


def test_quality_late_start_not_counted_as_gap():
    late = make_frame(start="2020-06-01", seed=1)
    report = quality_report({"AAA": make_frame(seed=2), "LATE": late})
    assert report.per_ticker.loc["LATE", "missing_days"] == 0


def test_quality_flags_nonpositive_price():
    bad = make_frame(seed=1)
    bad.iloc[10, bad.columns.get_loc("close")] = 0.0
    report = quality_report({"BAD": bad})
    assert "nonpositive" in report.per_ticker.loc["BAD", "flags"]


def test_quality_empty_input():
    report = quality_report({}, ["AAA"])
    assert report.missing == ["AAA"]
    assert report.per_ticker.empty
