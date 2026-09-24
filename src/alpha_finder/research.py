"""Shared setup used by every version: market data, the dev/holdout split, paired runs, run logging.

Anything specific to one version (its signal, its portfolio variants, its scripts) lives in that
version's own folder (version_1_rules/, version_2_ml/, ...), not here.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from alpha_finder.backtest.engine import BacktestConfig, Panels, Result, build_panels, run_backtest
from alpha_finder.backtest.tax import TaxConfig
from alpha_finder.config import DEFAULT_START
from alpha_finder.data.prices import load_prices, to_panel
from alpha_finder.signals.momentum import month_end_dates
from alpha_finder.universe.sp500 import SP500History

DATA_END = "2026-09-23"
DEV_END = pd.Timestamp("2019-12-31")  # holdout starts with the signal dated this day (fills 2020-01-02)
HOLDOUT_SIGNAL_START = pd.Timestamp("2019-12-31")

# One set of tax assumptions for every version, so results are comparable.
TAX = TaxConfig(short_term=0.38, long_term=0.23, dividend=0.23)


@dataclass
class Market:
    """Everything a model needs to read: prices, index membership, calendar."""

    prices: dict[str, pd.DataFrame]
    history: SP500History
    calendar: pd.DatetimeIndex
    panels: Panels
    adj: pd.DataFrame
    month_ends: pd.DatetimeIndex
    sectors: dict[str, str]

    @classmethod
    def load(cls, end: str = DATA_END) -> "Market":
        history = SP500History.load()
        tickers = sorted(history.all_tickers("2010-12-31", end)) + ["QQQ"]
        prices = load_prices(tickers, DEFAULT_START, end)
        calendar = prices["QQQ"].index
        panels = build_panels(prices, calendar)
        adj = to_panel(prices, "adj_close").reindex(calendar)
        sectors = dict(zip(history.current["ticker"], history.current["sector"]))
        return cls(prices, history, calendar, panels, adj, month_end_dates(calendar), sectors)

    def coverage(self, ranks: dict[pd.Timestamp, pd.Series] | None = None) -> pd.DataFrame:
        """Per month-end: index members, members with any price, and (if ranks given) members ranked."""
        have_price = self.adj.ffill(limit=5).reindex(self.month_ends).notna()
        ranks = ranks or {}
        rows = []
        for d in self.month_ends:
            members = self.history.members(d)
            priced = {t for t in members if t in have_price.columns and have_price.loc[d, t]}
            rows.append({"date": d, "members": len(members), "with_price": len(priced),
                         "ranked": len(ranks.get(d, []))})
        return pd.DataFrame(rows).set_index("date")


def benchmark_config(tax: TaxConfig = TAX) -> BacktestConfig:
    return BacktestConfig("QQQ buy and hold", mode="buy_hold", n_holdings=1, tax=tax)


def run_pair(market: Market, cfg: BacktestConfig, ranks, start: pd.Timestamp | None,
             end: pd.Timestamp | None) -> tuple[Result, Result]:
    """Same strategy run once with taxes and once without (identical rules)."""
    after = run_backtest(cfg, market.panels, ranks, market.sectors, end=end, start=start)
    pre = run_backtest(replace(cfg, tax=TaxConfig.none()), market.panels, ranks, market.sectors,
                       end=end, start=start)
    return after, pre


def log_run(log_path: Path, stage: str, name: str, cfg: BacktestConfig, start, end, row: dict,
            extra: dict | None = None) -> None:
    """Append one trial to a version's run log, so we can count how many things we tried.

    Each version keeps its own log (version_x/results/run_log.jsonl). Logging the same trial
    twice is a no-op, so re-running a script never inflates the trial count.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    params = asdict(cfg)
    params.pop("tax", None)
    entry = {
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stage": stage, "name": name, "params": params,
        "start": str(start) if start is not None else None,
        "end": str(end) if end is not None else None,
        "after_tax_cagr": row["after_tax_cagr"], "bench_after_tax_cagr": row["bench_after_tax_cagr"],
        "excess_after_tax": row["excess_after_tax"], "extra": extra or {},
    }
    key = (stage, name, json.dumps(params, sort_keys=True, default=float), entry["start"], entry["end"])
    if log_path.exists():
        for line in log_path.read_text().splitlines():
            old = json.loads(line)
            if (old["stage"], old["name"], json.dumps(old["params"], sort_keys=True, default=float),
                    old["start"], old["end"]) == key:
                return
    with log_path.open("a") as f:
        f.write(json.dumps(entry, default=float) + "\n")


def to_jsonable(obj):
    if hasattr(obj, "__dataclass_fields__"):
        return {k: to_jsonable(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    if hasattr(obj, "item"):
        return obj.item()
    return obj
