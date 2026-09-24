"""Tax lots and the annual capital-gains ledger.

Simplifications (all listed in the backtest report):
- Federal, state and NIIT are collapsed into one short-term and one long-term
  rate. Dividends are all treated as qualified (long-term rate).
- Long-term means held for more than 365 calendar days.
- The $3,000 ordinary-income loss deduction is ignored; net losses carry
  forward with their short/long character.
- Wash sales: a loss is disallowed when the same ticker is bought within 30
  calendar days AFTER the sale, and moves into the basis of the new lot with the
  old holding period tacked on. Purchases in the 30 days BEFORE a loss sale are
  not modelled (the strategy adds to positions rarely, so this is small).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

LONG_TERM_DAYS = 365
WASH_DAYS = 30


@dataclass(frozen=True)
class TaxConfig:
    short_term: float = 0.38  # 35% federal + 3% MA
    long_term: float = 0.23  # 20% federal + 3% MA
    dividend: float = 0.23  # qualified dividends at the long-term rate

    @classmethod
    def none(cls) -> "TaxConfig":
        return cls(0.0, 0.0, 0.0)


@dataclass
class Lot:
    ticker: str
    shares: float
    basis: float  # total dollars, including any disallowed wash-sale loss
    acquired: pd.Timestamp  # holding-period start (tacked on wash sales)

    @property
    def basis_per_share(self) -> float:
        return self.basis / self.shares

    def is_long_term(self, on: pd.Timestamp) -> bool:
        return (on - self.acquired).days > LONG_TERM_DAYS

    def days_to_long_term(self, on: pd.Timestamp) -> int:
        return max(0, LONG_TERM_DAYS + 1 - (on - self.acquired).days)


@dataclass
class PendingLoss:
    """A realized loss that a repurchase within the window would disallow."""

    sale_date: pd.Timestamp
    shares_left: float
    loss_per_share: float  # positive number
    holding_days: int
    long_term: bool


@dataclass
class TaxLedger:
    config: TaxConfig
    st_gains: dict[int, float] = field(default_factory=dict)
    lt_gains: dict[int, float] = field(default_factory=dict)
    dividends: dict[int, float] = field(default_factory=dict)
    st_carry: float = 0.0  # negative or zero
    lt_carry: float = 0.0
    total_paid: float = 0.0
    realized_st_total: float = 0.0
    realized_lt_total: float = 0.0

    def realize(self, year: int, gain: float, long_term: bool) -> None:
        book = self.lt_gains if long_term else self.st_gains
        book[year] = book.get(year, 0.0) + gain
        if long_term:
            self.realized_lt_total += gain
        else:
            self.realized_st_total += gain

    def dividend(self, year: int, amount: float) -> None:
        self.dividends[year] = self.dividends.get(year, 0.0) + amount

    def tax_for_year(self, year: int, extra_st: float = 0.0, extra_lt: float = 0.0,
                     commit: bool = True) -> float:
        """Tax owed for `year`. extra_* are hypothetical gains (used to price a full
        liquidation). commit=False leaves the loss carryforwards untouched."""
        st = self.st_gains.get(year, 0.0) + extra_st + self.st_carry
        lt = self.lt_gains.get(year, 0.0) + extra_lt + self.lt_carry
        if st < 0 < lt:  # short-term losses offset long-term gains, and vice versa
            offset = min(-st, lt)
            st, lt = st + offset, lt - offset
        elif lt < 0 < st:
            offset = min(-lt, st)
            st, lt = st - offset, lt + offset
        tax = max(st, 0.0) * self.config.short_term + max(lt, 0.0) * self.config.long_term
        tax += self.dividends.get(year, 0.0) * self.config.dividend
        if commit:
            self.st_carry, self.lt_carry = min(st, 0.0), min(lt, 0.0)
        return tax
