"""Performance statistics: after-tax growth, alpha/beta vs QQQ, drawdown, sub-periods."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import statsmodels.api as sm

from alpha_finder.backtest.engine import Result


def monthly_returns(equity: pd.Series) -> pd.Series:
    return equity.resample("ME").last().pct_change().dropna()


def max_drawdown(equity: pd.Series) -> float:
    return float((equity / equity.cummax() - 1).min())


def longest_underwater_days(relative: pd.Series) -> int:
    """Longest stretch (calendar days) that a relative-wealth series stays below its peak."""
    peak = relative.cummax()
    below = relative < peak
    longest = run_start = 0
    start_date = None
    for date, flag in below.items():
        if flag and start_date is None:
            start_date = date
        elif not flag and start_date is not None:
            longest = max(longest, (date - start_date).days)
            start_date = None
    if start_date is not None:
        longest = max(longest, (relative.index[-1] - start_date).days)
    return longest


@dataclass
class AlphaStats:
    alpha_annual: float
    alpha_t: float
    alpha_ci_low: float
    alpha_ci_high: float
    beta: float
    information_ratio: float
    tracking_error: float


def alpha_beta(strategy: pd.Series, benchmark: pd.Series) -> AlphaStats:
    """Regress monthly strategy returns on monthly QQQ returns (Newey-West errors)."""
    df = pd.concat([strategy, benchmark], axis=1, keys=["s", "b"]).dropna()
    X = sm.add_constant(df["b"])
    fit = sm.OLS(df["s"], X).fit(cov_type="HAC", cov_kwds={"maxlags": 3})
    a, beta = fit.params["const"], fit.params["b"]
    lo, hi = fit.conf_int().loc["const"]
    excess = df["s"] - df["b"]
    te = float(excess.std() * np.sqrt(12))
    ir = float(excess.mean() * 12 / te) if te > 0 else float("nan")
    return AlphaStats(
        alpha_annual=float((1 + a) ** 12 - 1), alpha_t=float(fit.tvalues["const"]),
        alpha_ci_low=float((1 + lo) ** 12 - 1), alpha_ci_high=float((1 + hi) ** 12 - 1),
        beta=float(beta), information_ratio=ir, tracking_error=te,
    )


def period_cagr(equity: pd.Series, start: str, end: str) -> float:
    seg = equity.loc[start:end]
    if len(seg) < 2:
        return float("nan")
    years = (seg.index[-1] - seg.index[0]).days / 365.25
    return float((seg.iloc[-1] / seg.iloc[0]) ** (1 / years) - 1)


SUB_PERIODS = [
    ("2011-2013", "2011-01-01", "2013-12-31"),
    ("2014-2019", "2014-01-01", "2019-12-31"),
    ("2020-2021", "2020-01-01", "2021-12-31"),
    ("2022", "2022-01-01", "2022-12-31"),
    ("2023+", "2023-01-01", "2026-12-31"),
]


def summarize(name: str, after_tax: Result, pre_tax: Result, bench_after: Result,
              bench_pre: Result) -> dict:
    """One row of headline statistics, strategy vs benchmark, after tax and pre-tax."""
    s_ret, b_ret = monthly_returns(after_tax.equity), monthly_returns(bench_after.equity)
    ab_after = alpha_beta(s_ret, b_ret)
    ab_pre = alpha_beta(monthly_returns(pre_tax.equity), monthly_returns(bench_pre.equity))
    rel = after_tax.equity / bench_after.equity
    subs = {
        label: period_cagr(after_tax.equity, a, b) - period_cagr(bench_after.equity, a, b)
        for label, a, b in SUB_PERIODS
    }
    led = after_tax.ledger
    realized = led.realized_st_total + led.realized_lt_total
    gains_lt_share = (led.realized_lt_total / realized) if realized > 0 else float("nan")
    hl = after_tax.holdings_log
    return {
        "name": name,
        "after_tax_cagr": after_tax.cagr_after_tax,
        "bench_after_tax_cagr": bench_after.cagr_after_tax,
        "excess_after_tax": after_tax.cagr_after_tax - bench_after.cagr_after_tax,
        "pre_tax_cagr": pre_tax.cagr_after_tax,  # zero tax: "after tax" equals pre-tax
        "bench_pre_tax_cagr": bench_pre.cagr_after_tax,
        "excess_pre_tax": pre_tax.cagr_after_tax - bench_pre.cagr_after_tax,
        "final_value_after_tax": after_tax.final_after_tax,
        "bench_final_after_tax": bench_after.final_after_tax,
        "max_dd": max_drawdown(pre_tax.equity),
        "bench_max_dd": max_drawdown(bench_pre.equity),
        "alpha_after_tax": ab_after,
        "alpha_pre_tax": ab_pre,
        "annual_turnover": after_tax.annual_turnover,
        "tax_paid": after_tax.tax_paid,
        "liquidation_tax": after_tax.liquidation_tax,
        "lt_share_of_gains": gains_lt_share,
        "avg_holdings": float(hl["n_holdings"].mean()) if len(hl) else float("nan"),
        "avg_cash_frac": float(hl["cash_frac"].mean()) if len(hl) else float("nan"),
        "longest_underwater_days": longest_underwater_days(rel),
        "sub_period_excess": subs,
        "disallowed_wash_loss": after_tax.disallowed_loss,
        "years": after_tax.years,
    }
