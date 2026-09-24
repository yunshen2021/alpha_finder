"""Signal quality before any portfolio: rank IC and quantile spreads.

For each month-end, correlate the score with the following month's return
(close to close) across index members. This is cheaper and harder to game than
a P&L backtest, and it tells us whether the signal predicts anything at all.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats


def signal_diagnostics(ranks: dict[pd.Timestamp, pd.Series], adj_close: pd.DataFrame,
                       month_ends: pd.DatetimeIndex, top_n: int = 25) -> pd.DataFrame:
    """Per signal date: rank IC, top-quintile minus bottom-quintile, top-N minus universe.

    Forward return runs from the signal month-end close to the next month-end close.
    """
    px = adj_close.ffill(limit=5).reindex(month_ends)
    rows = []
    for j in range(len(month_ends) - 1):
        d, nxt = month_ends[j], month_ends[j + 1]
        if d not in ranks:
            continue
        r = ranks[d]
        fwd = (px.loc[nxt, r.index] / px.loc[d, r.index] - 1).dropna()
        r = r.loc[fwd.index]
        if len(r) < 50:
            continue
        ic, _ = stats.spearmanr(-r, fwd)
        q = pd.qcut(r.rank(method="first"), 5, labels=False)
        rows.append({
            "date": d, "n": len(r), "ic": ic,
            "q1_minus_q5": fwd[q == 0].mean() - fwd[q == 4].mean(),
            "topn_minus_universe": fwd[r <= top_n].mean() - fwd.mean(),
        })
    return pd.DataFrame(rows).set_index("date")


def summarize_diagnostics(df: pd.DataFrame) -> dict:
    n = len(df)
    ic = df["ic"]
    return {
        "months": n,
        "mean_ic": float(ic.mean()),
        "ic_t": float(ic.mean() / ic.std(ddof=1) * np.sqrt(n)) if n > 1 else float("nan"),
        "pct_positive_ic": float((ic > 0).mean()),
        "q1_minus_q5_monthly": float(df["q1_minus_q5"].mean()),
        "topn_minus_universe_monthly": float(df["topn_minus_universe"].mean()),
        "topn_minus_universe_annual": float((1 + df["topn_minus_universe"].mean()) ** 12 - 1),
    }
