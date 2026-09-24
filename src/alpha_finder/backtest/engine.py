"""Tax-aware, lot-level backtest engine.

Timeline per trading day: dividends -> forced sells for names whose data ended
-> rebalance at the open (only on the first trading day after a signal date) ->
mark to market at the close -> pay the year's tax on the last trading day of
the year, out of the portfolio.

Signals arrive as {signal_date: Series(rank by ticker)}. A signal computed on
the close of date t is executed on the open of the next trading day; nothing
in the engine can see later data than the signal carries.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import timedelta

import numpy as np
import pandas as pd

from alpha_finder.backtest.tax import (
    WASH_DAYS, Lot, PendingLoss, TaxConfig, TaxLedger,
)

EPS = 1e-9


@dataclass(frozen=True)
class BacktestConfig:
    name: str
    mode: str = "rank"  # "rank" or "buy_hold"
    n_holdings: int = 25
    sell_rank: int | None = 60  # hold incumbents until rank exceeds this; None -> n_holdings
    equalize: bool = False  # trim/top-up every holding back to equal weight (naive variant)
    tax_gate: bool = False  # defer sells that would turn near-long-term gains short; wash block
    lt_window_days: int = 60
    override_rank: int = 150
    position_cap: float = 0.10
    max_sector_frac: float = 0.30
    cost_bps: float = 10.0
    initial_cash: float = 1_000_000.0
    tax: TaxConfig = field(default_factory=TaxConfig)
    benchmark_ticker: str = "QQQ"
    fill_delay: int = 1  # trading days between signal close and fill open


@dataclass
class Panels:
    dates: pd.DatetimeIndex
    tickers: list[str]
    open: np.ndarray
    close: np.ndarray  # last-known close, forward filled (for valuation)
    dividends: np.ndarray
    last_valid: np.ndarray  # calendar index of last real close per ticker
    index: dict[str, int]


def build_panels(prices: dict[str, pd.DataFrame], calendar: pd.DatetimeIndex) -> Panels:
    tickers = sorted(prices)
    T, K = len(calendar), len(tickers)
    o = np.full((T, K), np.nan)
    c = np.full((T, K), np.nan)
    d = np.zeros((T, K))
    for k, t in enumerate(tickers):
        df = prices[t].reindex(calendar)
        o[:, k] = df["open"].to_numpy()
        c[:, k] = df["close"].to_numpy()
        d[:, k] = df["dividends"].fillna(0.0).to_numpy()
    valid = ~np.isnan(c)
    last_valid = np.where(valid.any(axis=0), T - 1 - np.argmax(valid[::-1], axis=0), -1)
    c_ff = pd.DataFrame(c).ffill().to_numpy()
    return Panels(calendar, tickers, o, c_ff, d, last_valid, {t: k for k, t in enumerate(tickers)})


@dataclass
class Result:
    config: BacktestConfig
    equity: pd.Series  # daily value after taxes paid
    trades: pd.DataFrame
    holdings_log: pd.DataFrame  # per fill date: n holdings, cash share
    ledger: TaxLedger
    tax_paid: float
    final_value: float  # portfolio value at the end, before liquidation tax
    liquidation_tax: float
    final_after_tax: float  # value if everything were sold at the end
    buy_volume: float
    sell_volume: float
    disallowed_loss: float
    years: float

    @property
    def cagr_after_tax(self) -> float:
        return (self.final_after_tax / self.config.initial_cash) ** (1 / self.years) - 1

    @property
    def annual_turnover(self) -> float:
        avg = float(self.equity.mean())
        return self.sell_volume / avg / self.years


class Account:
    def __init__(self, cfg: BacktestConfig, panels: Panels):
        self.cfg, self.p = cfg, panels
        self.cash = cfg.initial_cash
        self.lots: dict[str, list[Lot]] = {}
        self.ledger = TaxLedger(cfg.tax)
        self.pending: dict[str, list[PendingLoss]] = {}
        self.last_loss_sale: dict[str, pd.Timestamp] = {}
        self.cost = cfg.cost_bps / 1e4
        self.trades: list[dict] = []
        self.buy_volume = self.sell_volume = self.disallowed = 0.0

    # --- helpers -------------------------------------------------------
    def shares(self, ticker: str) -> float:
        return sum(l.shares for l in self.lots.get(ticker, []))

    def position_value(self, ticker: str, price: float) -> float:
        return self.shares(ticker) * price

    def value_at(self, i: int, use_open: bool = False) -> float:
        total = self.cash
        for t in self.lots:
            k = self.p.index[t]
            px = self.p.open[i, k] if use_open else np.nan
            if np.isnan(px):
                px = self.p.close[i, k]
            total += self.shares(t) * px
        return total

    # --- trading -------------------------------------------------------
    def buy(self, i: int, ticker: str, dollars: float, price: float) -> None:
        day = self.p.dates[i]
        dollars = min(dollars, self.cash / (1 + self.cost))
        if dollars <= 1.0:
            return
        fee = dollars * self.cost
        shares = dollars / price
        basis_per_share = (dollars + fee) / shares
        self.cash -= dollars + fee
        self.buy_volume += dollars

        left = shares
        book = self.lots.setdefault(ticker, [])
        pend = [pl for pl in self.pending.get(ticker, [])
                if (day - pl.sale_date).days <= WASH_DAYS and pl.shares_left > EPS]
        self.pending[ticker] = pend
        for pl in pend:  # wash sale: the loss moves into the new lot's basis
            m = min(left, pl.shares_left)
            if m <= EPS:
                continue
            disallowed = m * pl.loss_per_share
            book.append(Lot(ticker, m, m * basis_per_share + disallowed,
                            day - timedelta(days=pl.holding_days)))
            self.ledger.realize(day.year, disallowed, pl.long_term)  # reverse the loss
            self.disallowed += disallowed
            pl.shares_left -= m
            left -= m
        if left > EPS:
            book.append(Lot(ticker, left, left * basis_per_share, day))
        self.trades.append({"date": day, "ticker": ticker, "side": "buy",
                            "shares": shares, "price": price, "gain": 0.0, "term": ""})

    def sell(self, i: int, ticker: str, shares: float, price: float, *, tax_year: int | None = None,
             fee: bool = True, reason: str = "") -> None:
        day = self.p.dates[i]
        year = tax_year or day.year
        lots = self.lots.get(ticker, [])
        if not lots:
            return
        net_px = price * (1 - self.cost) if fee else price
        rates = self.cfg.tax

        def tax_per_share(l: Lot) -> float:  # sell the cheapest-to-tax lots first
            rate = rates.long_term if l.is_long_term(day) else rates.short_term
            return (net_px - l.basis_per_share) * rate

        remaining = min(shares, sum(l.shares for l in lots))
        total_gain = 0.0
        for lot in sorted(lots, key=tax_per_share):
            if remaining <= EPS:
                break
            take = min(lot.shares, remaining)
            frac = take / lot.shares
            basis_used = lot.basis * frac
            proceeds = take * net_px
            gain = proceeds - basis_used
            long_term = lot.is_long_term(day)
            self.ledger.realize(year, gain, long_term)
            if gain < 0:
                self.pending.setdefault(ticker, []).append(
                    PendingLoss(day, take, -gain / take, (day - lot.acquired).days, long_term))
                self.last_loss_sale[ticker] = day
            lot.shares -= take
            lot.basis -= basis_used
            remaining -= take
            self.cash += proceeds
            self.sell_volume += take * price
            total_gain += gain
        self.lots[ticker] = [l for l in lots if l.shares > EPS]
        if not self.lots[ticker]:
            del self.lots[ticker]
        self.trades.append({"date": day, "ticker": ticker, "side": "sell", "shares": shares,
                            "price": price, "gain": total_gain, "term": reason})

    def raise_cash(self, i: int, amount: float, tax_year: int) -> None:
        """Sell every position pro rata to fund a tax bill."""
        total = self.value_at(i) - self.cash
        if total <= 0 or amount <= 0:
            return
        frac = min(1.0, amount / (total * (1 - self.cost)))
        for t in list(self.lots):
            px = self.p.close[i, self.p.index[t]]
            self.sell(i, t, self.shares(t) * frac, px, tax_year=tax_year, reason="tax")

    # --- decisions -----------------------------------------------------
    def _near_long_term_gain(self, ticker: str, day: pd.Timestamp, price: float) -> bool:
        lots = self.lots[ticker]
        if sum(l.shares * (price - l.basis_per_share) for l in lots) <= 0:
            return False
        return any(
            (not l.is_long_term(day)) and l.days_to_long_term(day) <= self.cfg.lt_window_days
            and price > l.basis_per_share for l in lots
        )

    def rebalance(self, i: int, ranks: pd.Series, sectors: dict[str, str]) -> None:
        cfg, p = self.cfg, self.p
        day = p.dates[i]
        opens = p.open[i]
        n = cfg.n_holdings
        keep_rank = cfg.sell_rank if cfg.sell_rank is not None else n

        # 1. sells
        for t in list(self.lots):
            k = p.index[t]
            px = opens[k]
            if np.isnan(px) or px <= 0:
                continue
            r = ranks.get(t)
            if r is None:
                self.sell(i, t, self.shares(t), px, reason="forced")
            elif r > keep_rank:
                if cfg.tax_gate and r <= cfg.override_rank and self._near_long_term_gain(t, day, px):
                    continue
                self.sell(i, t, self.shares(t), px, reason="rank")

        equity = self.value_at(i, use_open=True)
        target = equity / n

        # 2. trims: equal-weight everything (naive) or only enforce the position cap
        for t in list(self.lots):
            px = opens[p.index[t]]
            if np.isnan(px) or px <= 0:
                continue
            value = self.shares(t) * px
            ceiling = target if cfg.equalize else cfg.position_cap * equity
            if value > ceiling * (1 + 1e-6):
                self.sell(i, t, (value - (target if cfg.equalize else ceiling)) / px, px, reason="trim")

        # 3. buys, best rank first
        counts: dict[str, int] = {}
        for t in self.lots:
            counts[sectors.get(t, "Unknown")] = counts.get(sectors.get(t, "Unknown"), 0) + 1
        max_per_sector = max(1, int(cfg.max_sector_frac * n))
        ordered = ranks.sort_values()
        if cfg.equalize:
            wanted = ordered.index[:n]
        else:
            wanted = ordered.index
        slots = n - len(self.lots)
        for t in wanted:
            if not cfg.equalize and slots <= 0:
                break
            k = p.index.get(t)
            if k is None:
                continue
            px = opens[k]
            if np.isnan(px) or px <= 0:
                continue
            held = t in self.lots
            if cfg.equalize:
                need = target - self.position_value(t, px)
                if need < 0.25 * target:
                    continue
            else:
                if held:
                    continue
                need = target
                if cfg.tax_gate and t in self.last_loss_sale and \
                        (day - self.last_loss_sale[t]).days <= WASH_DAYS + 1:
                    continue
            sector = sectors.get(t, "Unknown")
            if not held and sector != "Unknown" and counts.get(sector, 0) >= max_per_sector:
                continue
            if self.cash < 0.25 * need:
                break
            self.buy(i, t, need, px)
            if not held and t in self.lots:
                counts[sector] = counts.get(sector, 0) + 1
                slots -= 1

    def buy_and_hold(self, i: int) -> None:
        """Benchmark: invest everything at the start, then reinvest leftover cash each January."""
        k = self.p.index[self.cfg.benchmark_ticker]
        px = self.p.open[i, k]
        if np.isnan(px) or px <= 0:
            return
        day = self.p.dates[i]
        if not self.lots or day.month == 1:
            self.buy(i, self.cfg.benchmark_ticker, self.cash, px)

    def liquidate_delisted(self, i: int) -> None:
        for t in list(self.lots):
            k = self.p.index[t]
            if self.p.last_valid[k] < i:
                self.sell(i, t, self.shares(t), self.p.close[self.p.last_valid[k], k],
                          fee=False, reason="delisted")


def run_backtest(cfg: BacktestConfig, panels: Panels, ranks: dict[pd.Timestamp, pd.Series],
                 sectors: dict[str, str], end: pd.Timestamp | None = None,
                 start: pd.Timestamp | None = None) -> Result:
    """Run one backtest. `start`/`end` bound the fill dates; both default to the data."""
    acct = Account(cfg, panels)
    dates = panels.dates
    # Both modes derive the first fill from the same signal dates, so the strategy and
    # the benchmark always start on the same day.
    signal_dates = sorted(d for d in ranks if start is None or d >= start)
    pos = {d: dates.get_loc(d) for d in signal_dates}
    fills = {min(pos[d] + cfg.fill_delay, len(dates) - 1): ranks[d] for d in signal_dates}
    first_fill = min(fills)
    if cfg.mode == "buy_hold":
        fills = {first_fill: pd.Series(dtype=float)}
        for i in range(first_fill, len(dates)):
            if dates[i].month == 1 and dates[i - 1].month != 1:
                fills[i] = pd.Series(dtype=float)
    last = len(dates) - 1 if end is None else int(dates.searchsorted(end, side="right")) - 1

    equity: dict[pd.Timestamp, float] = {}
    hold_log = []
    tax_paid = 0.0
    for i in range(first_fill, last + 1):
        day = dates[i]
        # dividends go to whoever held the shares before today's trades
        for t in list(acct.lots):
            dv = panels.dividends[i, panels.index[t]]
            if dv > 0:
                amount = dv * acct.shares(t)
                acct.cash += amount
                acct.ledger.dividend(day.year, amount)
        acct.liquidate_delisted(i)
        if i in fills:
            if cfg.mode == "buy_hold":
                acct.buy_and_hold(i)
            else:
                acct.rebalance(i, fills[i], sectors)
            v = acct.value_at(i, use_open=True)
            hold_log.append({"date": day, "n_holdings": len(acct.lots),
                             "cash_frac": acct.cash / v if v else 0.0})
        value = acct.value_at(i)
        if i == last or dates[i + 1].year != day.year:  # year-end: settle the year's tax
            if i != last:
                due = acct.ledger.tax_for_year(day.year)
                if due > acct.cash:
                    acct.raise_cash(i, due - acct.cash, day.year + 1)
                acct.cash -= due
                tax_paid += due
                value = acct.value_at(i)
        equity[day] = value

    final_day = dates[last]
    st_unreal = lt_unreal = 0.0
    for t, lots in acct.lots.items():
        px = panels.close[last, panels.index[t]] * (1 - acct.cost)
        for l in lots:
            g = l.shares * px - l.basis
            if l.is_long_term(final_day):
                lt_unreal += g
            else:
                st_unreal += g
    liq_tax = acct.ledger.tax_for_year(final_day.year, st_unreal, lt_unreal, commit=False)
    final_value = value
    liquid_value = acct.cash + sum(
        acct.shares(t) * panels.close[last, panels.index[t]] * (1 - acct.cost) for t in acct.lots)
    years = (final_day - dates[first_fill]).days / 365.25
    trades = pd.DataFrame(acct.trades)
    return Result(
        config=cfg, equity=pd.Series(equity), trades=trades,
        holdings_log=pd.DataFrame(hold_log), ledger=acct.ledger, tax_paid=tax_paid,
        final_value=final_value, liquidation_tax=liq_tax,
        final_after_tax=liquid_value - liq_tax, buy_volume=acct.buy_volume,
        sell_volume=acct.sell_volume, disallowed_loss=acct.disallowed, years=years,
    )
