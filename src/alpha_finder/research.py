"""Shared setup for research runs: market data, ranks, coverage, run logging."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from functools import cached_property
from pathlib import Path

import pandas as pd

from alpha_finder.backtest.engine import BacktestConfig, Panels, Result, build_panels, run_backtest
from alpha_finder.backtest.metrics import summarize
from alpha_finder.backtest.tax import TaxConfig
from alpha_finder.config import DEFAULT_START, PROJECT_ROOT
from alpha_finder.data.prices import load_prices, to_panel
from alpha_finder.signals.momentum import month_end_dates, momentum_scores, rank_universe
from alpha_finder.universe.sp500 import SP500History

REPORT_DIR = PROJECT_ROOT / "reports"
RUN_LOG = REPORT_DIR / "run_log.jsonl"
DATA_END = "2026-09-23"
DEV_END = pd.Timestamp("2019-12-31")  # holdout starts with the signal dated this day (fills 2020-01-02)
HOLDOUT_SIGNAL_START = pd.Timestamp("2019-12-31")

TAX = TaxConfig(short_term=0.38, long_term=0.23, dividend=0.23)


@dataclass
class Market:
    prices: dict[str, pd.DataFrame]
    history: SP500History
    calendar: pd.DatetimeIndex
    panels: Panels
    adj: pd.DataFrame
    month_ends: pd.DatetimeIndex
    sectors: dict[str, str]
    _ranks: dict = field(default_factory=dict)

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

    def ranks(self, lookback: int = 12, skip: int = 1) -> dict[pd.Timestamp, pd.Series]:
        key = (lookback, skip)
        if key not in self._ranks:
            scores = momentum_scores(self.adj, self.month_ends, lookback, skip)
            have_price = self.adj.ffill(limit=5).reindex(self.month_ends).notna()
            self._ranks[key] = rank_universe(
                scores,
                members=lambda d: self.history.members(d) - {"QQQ"},
                tradable=lambda d: set(have_price.columns[have_price.loc[d].to_numpy()]),
            )
        return self._ranks[key]

    def coverage(self) -> pd.DataFrame:
        """Per month-end: index members, members with any price, members that got a rank."""
        have_price = self.adj.ffill(limit=5).reindex(self.month_ends).notna()
        ranks = self.ranks()
        rows = []
        for d in self.month_ends:
            members = self.history.members(d)
            priced = {t for t in members if t in have_price.columns and have_price.loc[d, t]}
            rows.append({"date": d, "members": len(members), "with_price": len(priced),
                         "ranked": len(ranks.get(d, []))})
        return pd.DataFrame(rows).set_index("date")


def variants(tax: TaxConfig = TAX) -> dict[str, BacktestConfig]:
    return {
        "A": BacktestConfig("A: top-25 replaced monthly, tax-blind", sell_rank=None, equalize=True,
                            tax_gate=False, tax=tax),
        "B": BacktestConfig("B: rank buffer", sell_rank=60, tax_gate=False, tax=tax),
        "C": BacktestConfig("C: buffer + tax gate", sell_rank=60, tax_gate=True, tax=tax),
    }


def benchmark_config(tax: TaxConfig = TAX) -> BacktestConfig:
    return BacktestConfig("QQQ buy and hold", mode="buy_hold", n_holdings=1, tax=tax)


def run_pair(market: Market, cfg: BacktestConfig, ranks, start: pd.Timestamp | None,
             end: pd.Timestamp | None) -> tuple[Result, Result]:
    """Same strategy run once with taxes and once without (identical rules)."""
    after = run_backtest(cfg, market.panels, ranks, market.sectors, end=end, start=start)
    pre = run_backtest(replace(cfg, tax=TaxConfig.none()), market.panels, ranks, market.sectors,
                       end=end, start=start)
    return after, pre


def log_run(stage: str, name: str, cfg: BacktestConfig, start, end, row: dict, extra: dict | None = None) -> None:
    """Append one trial to the run log, so we can count how many things we tried."""
    REPORT_DIR.mkdir(exist_ok=True)
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
    with RUN_LOG.open("a") as f:
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
