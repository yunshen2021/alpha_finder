"""Version 3: does buying only the top N holdings of QQQ or VGT beat buying the ETF itself?

Pure calculation, no taxes. Every Wednesday from 2020-01-01 to 2025-12-31 a fixed amount is invested
and nothing is ever sold. For "top N", that week's money is split across the fund's N largest holdings
in proportion to their weights. Benchmarks: the same money put entirely into QQQ, entirely into VGT,
or kept as cash. Two contribution scenarios: $1,000 every week, and $1,000 a week in 2020 rising 15%
each year.

Rules (see version_3_etf_topn/README.md for the reasoning):
- Purchase at Wednesday's close (next trading day if the market is closed).
- Holdings and weights: the fund's latest quarterly SEC filing on or before the purchase day, with
  each weight moved by the stock's price change up to the previous day's close (what the fund's
  daily holdings page would show that morning).
- Dividends are reinvested in the stock that paid them (Yahoo adjusted prices), for the ETFs too.
- Spin-offs: the new company's shares are kept. Buyouts: cash sits uninvested; acquirer shares are kept.

Run fetch_holdings.py first.
"""
import argparse
import json
import logging
import sys
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from alpha_finder.backtest.dca import (
    Acquisition, Spinoff, WeightPath, actual_prices, drift_prices, simulate, wednesday_schedule,
)
from alpha_finder.config import DATA_DIR
from alpha_finder.data.prices import load_prices, to_panel
from alpha_finder.data.proxy_prices import estimate_prices, leave_one_out_errors, to_trading_days

logging.basicConfig(level=logging.ERROR)
pd.set_option("display.width", 200)

HERE = Path(__file__).resolve().parent
START, END = "2020-01-01", "2025-12-31"
PRICE_START = "2019-06-01"
PRICE_END = "2026-09-23"  # load past END so later splits/spin-offs can be undone when rebuilding traded prices
AMOUNT = 1000.0
N_LIST = [5, 10, 15, 20, 25, 50, 75]
ETFS = ["QQQ", "VGT"]
SCENARIOS = {
    "flat": ("$1,000 every week", lambda d: AMOUNT),
    "growing": ("$1,000 a week in 2020, 15% more each year", lambda d: AMOUNT * 1.15 ** (d.year - 2020)),
}
PERIODS = {"2020-2025": ("2020-01-01", "2025-12-31"), "2024-2025": ("2024-01-01", "2025-12-31"),
           **{str(y): (f"{y}-01-01", f"{y}-12-31") for y in range(2020, 2026)}}
PRICE_TOLERANCE = 0.02  # a Yahoo price more than 2% away from the fund's own valuation is flagged

# Corporate actions affecting stocks these portfolios can buy. Each is verified against prices below.
T = pd.Timestamp
SPINOFFS = [
    Spinoff("DELL", "VMW", T("2021-11-02"), 0.440626),  # Dell -> its VMware stake (ex-date)
    Spinoff("IBM", "KD", T("2021-11-04"), 1 / 5),        # IBM -> Kyndryl
    Spinoff("EXC", "CEG", T("2022-02-02"), 1 / 3),       # Exelon -> Constellation Energy
    Spinoff("FLEX", "NXT", T("2024-01-03"), 0.1713),     # Flex -> its Nextracker stake
    Spinoff("ILMN", "GRAL", T("2024-06-25"), 1 / 6),     # Illumina -> GRAIL
    Spinoff("WDC", "SNDK", T("2025-02-24"), 1 / 3),      # Western Digital -> Sandisk
    Spinoff("HON", "SOLS", T("2025-10-30"), 1 / 4),      # Honeywell -> Solstice
]
ACQUISITIONS = [
    Acquisition("ALXN", T("2021-07-21"), T("2021-07-20"), cash=60.00, acquirer="AZN", ratio=2.1243),  # AstraZeneca
    Acquisition("WORK", T("2021-07-21"), T("2021-07-20"), cash=26.79, acquirer="CRM", ratio=0.0776),  # Salesforce
    Acquisition("MXIM", T("2021-08-26"), T("2021-08-25"), cash=0.0, acquirer="ADI", ratio=0.630),     # Analog Devices
    Acquisition("XLNX", T("2022-02-14"), T("2022-02-11"), cash=0.0, acquirer="AMD", ratio=1.7234),    # AMD
    Acquisition("NUAN", T("2022-03-04"), T("2022-03-03"), cash=56.00),                                # Microsoft
    Acquisition("CERN", T("2022-06-08"), T("2022-06-07"), cash=95.00),                                # Oracle
    Acquisition("CTXS", T("2022-09-30"), T("2022-09-29"), cash=104.00),                               # Vista/Elliott
    Acquisition("ZEN", T("2022-11-22"), T("2022-11-21"), cash=77.50),                                 # H&F/Permira
    Acquisition("COUP", T("2023-02-28"), T("2023-02-27"), cash=81.00),                                # Thoma Bravo
    Acquisition("ATVI", T("2023-10-13"), T("2023-10-12"), cash=95.00),                                # Microsoft
    # VMware holders chose $142.50 cash or 0.252 Broadcom shares, prorated to about half each: use the average
    Acquisition("VMW", T("2023-11-22"), T("2023-11-21"), cash=71.25, acquirer="AVGO", ratio=0.126),   # Broadcom
    Acquisition("SGEN", T("2023-12-14"), T("2023-12-13"), cash=229.00),                               # Pfizer
    Acquisition("SPLK", T("2024-03-18"), T("2024-03-15"), cash=157.00),                               # Cisco
    Acquisition("JNPR", T("2025-07-02"), T("2025-07-01"), cash=40.00),                                # HPE
    Acquisition("ANSS", T("2025-07-17"), T("2025-07-16"), cash=197.00, acquirer="SNPS", ratio=0.345), # Synopsys
    # Walgreens: $11.45 cash plus a right to up to $3.00 more from asset sales; the right is counted as zero
    Acquisition("WBA", T("2025-08-28"), T("2025-08-27"), cash=11.45),                                 # Sycamore
]
# Stocks Yahoo no longer carries but that rank high enough to be bought: prices are estimated from the
# funds' own quarter-end valuations, the ETF's daily moves, and the deal terms (see data/proxy_prices.py).
_DEALS = {a.target: a for a in ACQUISITIONS}


def _spec(t, reference, announced, dividends=()):
    a = _DEALS.get(t)
    return dict(reference=reference, announced=announced, dividends=list(dividends),
                cash=a.cash if a else 0.0, acquirer=a.acquirer if a else None, ratio=a.ratio if a else 0.0,
                last_trade=str(a.last_trade.date()) if a else None)


ESTIMATED = {
    "ALXN": _spec("ALXN", "QQQ", "2020-12-12"),
    "WORK": _spec("WORK", "VGT", "2020-12-01"),
    # Walgreens paid a large dividend (4-8% a year) until 2025; approximate quarterly ex-dates
    "WBA": _spec("WBA", "QQQ", "2025-03-06", dividends=[
        ("2020-02-14", 0.4575), ("2020-05-15", 0.4575), ("2020-08-14", 0.4675), ("2020-11-13", 0.4675),
        ("2021-02-12", 0.4675), ("2021-05-14", 0.4675), ("2021-08-13", 0.4775), ("2021-11-12", 0.4775),
        ("2022-02-11", 0.4775), ("2022-05-13", 0.4775), ("2022-08-12", 0.48), ("2022-11-10", 0.48),
        ("2023-02-10", 0.48), ("2023-05-12", 0.48), ("2023-08-11", 0.48), ("2023-11-10", 0.48),
        ("2024-02-16", 0.25), ("2024-05-17", 0.25), ("2024-08-16", 0.25), ("2024-11-15", 0.25),
        ("2025-02-14", 0.25)]),
    "MXIM": _spec("MXIM", "VGT", "2020-07-13"),
    "XLNX": _spec("XLNX", "VGT", "2020-10-27"),
    "NUAN": _spec("NUAN", "VGT", "2021-04-12"),
    "CERN": _spec("CERN", "QQQ", "2021-12-20"),
    "CTXS": _spec("CTXS", "VGT", "2022-01-31"),
    "ATVI": _spec("ATVI", "QQQ", "2022-01-18"),
    "ZEN": _spec("ZEN", "VGT", "2022-06-24"),
    "COUP": _spec("COUP", "VGT", "2022-12-12"),
    "VMW": _spec("VMW", "VGT", "2022-05-26", dividends=[("2021-10-28", 11.50)]),  # special dividend
    "SGEN": _spec("SGEN", "QQQ", "2023-03-13"),
    "SPLK": _spec("SPLK", "VGT", "2023-09-21"),
    "JNPR": _spec("JNPR", "VGT", "2024-01-09"),
    "ANSS": _spec("ANSS", "VGT", "2024-01-16"),
    # Electronic Arts: still trading at the end of 2025 (its buyout closed in 2026), only Yahoo lacks it
    "EA": dict(reference="QQQ", announced="2025-09-29", cash=210.00, acquirer=None, ratio=0.0, last_trade=None,
               dividends=[]),
}
EXTRA = ["SNPS", "SNDK", "SOLS", "GRAL", "KD", "CEG", "AMD", "ADI", "AVGO", "AZN", "DELL", "IBM", "EXC", "NXT", "CRM",
         "FLEX"]
# Index changes between two filings. From these dates the next filing (drifted back) is used.
INDEX_EVENTS = {
    "QQQ": ["2023-07-24"],  # Nasdaq-100 special rebalance (cut the 7 largest weights), before the open
    "VGT": ["2023-06-01"],  # MSCI applied the 2023 GICS change: payments companies left the tech sector
}


def money(x: float) -> str:
    return f"${x:,.0f}"


def load_snapshot_rows(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype={"symbol": str, "cusip": str}).fillna({"symbol": ""})
    df["snapshot_date"] = pd.to_datetime(df["snapshot_date"])
    df["rank"] = df.groupby("snapshot_date")["pct_val"].rank(ascending=False, method="first")
    return df


def snapshots_from(rows: pd.DataFrame) -> dict[pd.Timestamp, pd.Series]:
    mapped = rows[rows.symbol != ""]
    return {d: g.groupby("symbol")["pct_val"].sum() for d, g in mapped.groupby("snapshot_date")}


def is_regular_split(r: float) -> bool:
    """True for ordinary splits (2-for-1, 3-for-2, 10-for-1, 1-for-10...); False for spin-off factors."""
    for p in range(1, 51):
        for q in range(1, 11):
            if abs(r - p / q) < 1e-4:
                return True
    return False


# --- data ------------------------------------------------------------------------------------

def build_market(rows: dict[str, pd.DataFrame]):
    symbols = sorted({s for df in rows.values() for s in df.symbol if s} | set(ETFS) | set(EXTRA))
    prices = load_prices(symbols, PRICE_START, PRICE_END)
    full = prices["QQQ"].index
    calendar = full[(full >= pd.Timestamp(PRICE_START)) & (full <= pd.Timestamp(END))]
    spin_dates: dict[str, list] = {}
    for sp in SPINOFFS:
        spin_dates.setdefault(sp.parent, []).append(sp.date)
    cols = {"adj": {}, "actual": {}, "drift": {}, "splits": {}}
    for t, df in prices.items():
        df = df.reindex(full)
        splits_t = df["splits"].fillna(0.0)
        cols["adj"][t] = df["adj_close"]
        cols["actual"][t] = actual_prices(df["close"], splits_t)  # uses splits up to PRICE_END
        cols["drift"][t] = drift_prices(df["close"], splits_t, spin_dates.get(t, []))
        cols["splits"][t] = splits_t
    adj, actual, drift, splits = (pd.DataFrame(cols[k]).reindex(calendar) for k in ("adj", "actual", "drift", "splits"))

    estimates = {}
    for t, spec in ESTIMATED.items():
        pts = pd.concat([df.loc[df.symbol == t].set_index("snapshot_date")["price"] for df in rows.values()]).dropna()
        value = pd.Series(spec["cash"], index=calendar)
        if spec.get("acquirer"):
            value = value + spec["ratio"] * actual[spec["acquirer"]]
        kw = dict(deal_value=value, announced=pd.Timestamp(spec["announced"]) if spec.get("announced") else None,
                  last_trade=pd.Timestamp(spec["last_trade"]) if spec.get("last_trade") else None,
                  reference=actual[spec["reference"]])
        est = estimate_prices(pts, calendar, **kw)
        adj_est = est.copy()
        for ex, amount in spec.get("dividends", []):  # large one-off dividends, reinvested like Yahoo does
            ex = pd.Timestamp(ex)
            before = calendar[calendar < ex]
            adj_est.loc[before] *= 1 - amount / est.loc[before[-1]]
        actual[t], drift[t], adj[t] = est, est, adj_est
        estimates[t] = {"anchors": to_trading_days(pts, calendar), "loo": leave_one_out_errors(pts, calendar, **kw)}
    missing = [s for s in symbols if s not in adj.columns or adj[s].isna().all()]
    return calendar, adj, actual, drift, splits, estimates, missing


# --- audits --------------------------------------------------------------------------------------

def price_audit(rows: dict[str, pd.DataFrame], actual: pd.DataFrame, calendar) -> pd.DataFrame:
    """Compare each holding's price in the fund's filing with Yahoo's traded price that day."""
    out = []
    for etf, df in rows.items():
        for _, r in df[(df.symbol != "") & df.price.notna()].iterrows():
            if r.symbol in ESTIMATED or r.symbol not in actual.columns:
                continue
            day = calendar[calendar.searchsorted(r.snapshot_date, side="right") - 1]
            y = actual.at[day, r.symbol]
            out.append({"etf": etf, "snapshot": r.snapshot_date.date(), "symbol": r.symbol, "name": r["name"],
                        "rank": int(r["rank"]), "fund_price": r.price, "yahoo_price": y,
                        "ratio": r.price / y if y and np.isfinite(y) else np.nan})
    return pd.DataFrame(out)


def listing_audit(rows, adj, last_day, max_rank=75) -> list[str]:
    """A stock that can be bought must have prices, and must either trade through the end or have a
    listed buyout. Checked for every holding ranked within `max_rank` in a filing that is used."""
    problems = []
    buyouts = {a.target for a in ACQUISITIONS}
    tradable = {s for df in rows.values() for s in df.loc[df["rank"] <= max_rank, "symbol"] if s}
    for t in sorted(tradable):
        if t not in adj.columns or adj[t].isna().all():
            problems.append(f"{t}: no prices at all")
        elif not np.isfinite(adj.at[last_day, t]) and t not in buyouts:
            last = adj[t].loc[:last_day].last_valid_index()
            problems.append(f"{t}: stopped trading after {last.date()} with no listed buyout")
    return problems


def held_split_audit(results: dict, splits: pd.DataFrame, end) -> list[str]:
    """Every odd "split" Yahoo shows for a stock while it was actually held must be a listed spin-off."""
    spins = {(sp.parent, sp.date) for sp in SPINOFFS}
    first_bought: dict[str, pd.Timestamp] = {}
    for res in results.values():
        for t in res.purchases.columns:
            bought = res.purchases.index[res.purchases[t] > 0]
            if len(bought):
                first_bought[t] = min(first_bought.get(t, bought[0]), bought[0])
    problems = []
    for t, first in sorted(first_bought.items()):
        if t not in splits.columns:
            continue
        ev = splits.loc[(splits.index > first) & (splits.index <= end), t]
        for d, r in ev[ev > 0].items():
            if not is_regular_split(r) and (t, d) not in spins:
                problems.append(f"{t}: odd split factor {r:.4f} on {d.date()} while held (a spin-off?)")
    return problems


def verify_spinoffs(actual: pd.DataFrame, splits: pd.DataFrame, calendar) -> list[dict]:
    """The value Yahoo removed from the parent should equal the child shares handed out."""
    out = []
    for s in SPINOFFS:
        prev = calendar[calendar.get_loc(s.date) - 1]
        factor = splits.at[s.date, s.parent]
        removed = 1 - 1 / factor if factor else float("nan")
        handed_out = s.ratio * actual.at[s.date, s.child] / actual.at[prev, s.parent]
        out.append({"parent": s.parent, "child": s.child, "date": str(s.date.date()), "ratio": s.ratio,
                    "yahoo_factor": factor, "value_removed_pct": removed, "child_value_pct": handed_out})
    return out


# --- simulation -----------------------------------------------------------------------------------

def drift_accuracy(path: WeightPath, calendar) -> pd.DataFrame:
    """How well do price-drifted weights predict the next filing? Large gaps reveal index changes."""
    snaps = sorted(path.snapshots)
    out = []
    for a, b in zip(snaps[:-1], snaps[1:]):
        day = calendar[calendar.searchsorted(b, side="right") - 1]
        pred = path.at(day, snapshot=a)
        act = path.snapshots[b][path.snapshots[b].index.isin(path.close.columns)]
        act = act / act.sum()
        both = pred.index.union(act.index)
        p, q = pred.reindex(both).fillna(0.0), act.reindex(both).fillna(0.0)
        top = lambda w, n: set(w.sort_values(ascending=False).index[:n])
        out.append({"from": a.date(), "to": b.date(), "weight_gap": 0.5 * float((p - q).abs().sum()),
                    "top5_same": len(top(p, 5) & top(q, 5)), "top25_same": len(top(p, 25) & top(q, 25)),
                    "top5_weight_gap": 0.5 * float((p - q).reindex(sorted(top(q, 5))).abs().sum())})
    return pd.DataFrame(out)


def make_weights(path: WeightPath, calendar):
    last_trade = {a.target: a.last_trade for a in ACQUISITIONS}
    prev_day = dict(zip(calendar[1:], calendar[:-1]))

    @lru_cache(maxsize=None)
    def weights_at(d: pd.Timestamp) -> pd.Series:
        w = path.at(prev_day[d], snapshot=path.snapshot_date_for(d))
        w = w.drop([t for t in w.index if t in last_trade and d > last_trade[t]])
        return w / w.sum()

    return weights_at


def run_etf(adj, actual, weights_at, dates, end, amount) -> dict:
    """Top-N portfolios of one ETF plus the all-holdings replica (a sanity check)."""
    actions = SPINOFFS + ACQUISITIONS
    out = {"ALL": simulate(adj, weights_at, dates, None, amount, end, actual=actual, actions=actions, strict=False)}
    for n in N_LIST:
        out[n] = simulate(adj, weights_at, dates, n, amount, end, actual=actual, actions=actions)
    return out


def run_benchmarks(adj, dates, end, amount) -> dict:
    return {etf: simulate(adj[[etf]], lambda d, e=etf: pd.Series({e: 1.0}), dates, None, amount, end)
            for etf in ETFS}


def row(label: str, r, bench: dict, own: str | None) -> dict:
    invested = r.invested
    out = {"portfolio": label, "invested": invested, "final_value": r.final_value, "gain": r.gain,
           "annual_return": r.irr(), "cash_from_buyouts": getattr(r, "cash", 0.0)}
    for etf, b in bench.items():
        out[f"vs_{etf}_pct"] = r.final_value / b.final_value - 1
    out["vs_own_etf_pct"] = out[f"vs_{own}_pct"] if own else np.nan
    return out


def cash_row(dates, amount) -> dict:
    total = float(sum(amount(d) for d in dates))
    return {"portfolio": "Cash (kept, no interest)", "invested": total, "final_value": total, "gain": 0.0,
            "annual_return": 0.0, "cash_from_buyouts": 0.0}


def estimated_exposure(res, estimates, adj, actual, end) -> dict:
    """Dollars put into stocks with estimated prices, and a bound on how wrong that could make the total.

    If every estimated purchase price is off by up to e, the shares bought are off by up to about e,
    so the position's end value is off by up to about e x (its end value). e is the larger of 5% and the
    worst error seen when re-estimating a real price anchor without itself (a pessimistic measure: without
    the anchor, the gap to the nearest real price is twice as long as in the actual estimate).
    """
    out = {}
    for t, info in estimates.items():
        if t not in res.purchases.columns or res.purchases[t].sum() == 0:
            continue
        bought = res.purchases[t]
        bought = bought[bought > 0]
        shares = float((bought / adj.loc[bought.index, t]).sum())
        spec = ESTIMATED[t]
        if spec["last_trade"] and pd.Timestamp(spec["last_trade"]) < end:  # bought out: deal terms
            per_share = spec["cash"] + spec.get("ratio", 0.0) * (actual.at[end, spec["acquirer"]] if spec.get("acquirer") else 0.0)
        else:
            per_share = adj.at[end, t]
        worst = float(info["loo"].abs().max()) if len(info["loo"]) else 0.10
        eps = max(0.05, worst)
        value = shares * per_share
        out[t] = {"dollars_bought": float(bought.sum()), "end_value": value, "error_rate_used": eps,
                  "max_error_dollars": eps * value, "anchors": int(len(info["anchors"])), "worst_leave_one_out": worst}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-dir", type=Path, default=HERE / "results")
    args = ap.parse_args()
    res_dir = args.results_dir
    (res_dir / "figures").mkdir(parents=True, exist_ok=True)

    rows = {}
    for etf in ETFS:
        p = DATA_DIR / "etf_holdings" / f"{etf}_snapshots.csv"
        if not p.exists():
            sys.exit(f"missing {p}. Run version_3_etf_topn/fetch_holdings.py first.")
        rows[etf] = load_snapshot_rows(p)

    calendar, adj, actual, drift, splits, estimates, missing = build_market(rows)
    all_dates = wednesday_schedule(START, END, calendar)
    end = calendar[calendar <= pd.Timestamp(END)][-1]
    print(f"{len(all_dates)} purchases, {all_dates[0].date()} to {all_dates[-1].date()}, valued {end.date()}", flush=True)

    # ---- audits (only on filings the calculation actually uses)
    for etf in ETFS:
        df = rows[etf]
        first_used = df.loc[df.snapshot_date <= all_dates[0], "snapshot_date"].max()
        rows[etf] = df[df.snapshot_date >= first_used].copy()
    audit = price_audit(rows, actual, calendar)
    audit.to_csv(res_dir / "price_audit.csv", index=False)
    bad = audit[(audit.ratio - 1).abs() > PRICE_TOLERANCE]
    print(f"price audit: {len(audit)} checks, {len(bad)} outside {PRICE_TOLERANCE:.0%} "
          f"({len(bad[bad['rank'] <= 75])} of them ranked in the top 75)")
    if len(bad):
        print(bad.sort_values("rank").head(25).to_string(index=False))
    wrong = audit[(audit.ratio - 1).abs() > 0.15]  # a different security behind the ticker: unmap it
    for _, r in wrong.iterrows():
        df = rows[r.etf]
        df.loc[(df.snapshot_date == pd.Timestamp(r.snapshot)) & (df.symbol == r.symbol), "symbol"] = ""
    if len(wrong):
        print(f"  unmapped {len(wrong)} holding-quarters whose ticker now belongs to another company: "
              f"{sorted(set(wrong.name))}")
    problems = listing_audit(rows, adj, end)
    for etf, df in rows.items():
        priced = df.symbol.map(lambda t: t in adj.columns and bool(adj[t].notna().any()))
        for _, r in df[~priced & (df["rank"] <= 90)].sort_values("rank").iterrows():
            msg = f"{etf} {r.snapshot_date.date()}: '{r['name']}' ranked #{int(r['rank'])} has no price"
            if r["rank"] <= 75:
                problems.append(msg)
            else:
                print("  note (outside the top 75):", msg)
    spin_check = verify_spinoffs(actual, splits, calendar)
    for sc in spin_check:
        print(f"  spin-off {sc['parent']}->{sc['child']} {sc['date']}: Yahoo removed {sc['value_removed_pct']:.1%} "
              f"of the parent's price; child shares handed out were worth {sc['child_value_pct']:.1%}")
    for t, info in estimates.items():
        loo = info["loo"]
        worst = f"{loo.abs().max():.1%}" if len(loo) else "n/a"
        med = f"{loo.abs().median():.1%}" if len(loo) else "n/a"
        print(f"  estimated {t}: {len(info['anchors'])} real price anchors, leave-one-out error median {med}, worst {worst}")
    if problems:
        print(f"\nStopping: {len(problems)} problems must be handled before results mean anything:")
        for p in problems:
            print("  ", p)
        (res_dir / "audit_problems.txt").write_text("\n".join(problems))
        return

    # ---- simulations
    weights = {}
    paths = {}
    for etf in ETFS:
        paths[etf] = WeightPath(snapshots_from(rows[etf]), drift.ffill(limit=5), events=INDEX_EVENTS[etf])
        weights[etf] = make_weights(paths[etf], calendar)
        acc = drift_accuracy(paths[etf], calendar)
        acc.to_csv(res_dir / f"{etf}_drift_accuracy.csv", index=False)
        print(f"\n{etf}: drifted weights vs the next filing")
        print(acc.to_string(index=False, float_format=lambda x: f"{x:.3f}"))

    main_amount = SCENARIOS["flat"][1]
    main_runs = {}
    for etf in ETFS:
        for k, r in run_etf(adj, actual, weights[etf], all_dates, end, main_amount).items():
            main_runs[(etf, k)] = r
    held_problems = held_split_audit({k: v for k, v in main_runs.items() if k[1] != "ALL"}, splits, end)
    print("held-period corporate-action audit:", "clean" if not held_problems else f"{len(held_problems)} problems")
    if held_problems:
        for p in held_problems:
            print("  ", p)
        (res_dir / "audit_problems.txt").write_text("\n".join(held_problems))
        return

    results = {"schedule": {"purchases": len(all_dates), "first": str(all_dates[0].date()),
                            "last": str(all_dates[-1].date()), "valued_on": str(end.date())},
               "audit": {"price_checks": int(len(audit)), "price_flags": int(len(bad)),
                         "unmapped_wrong_ticker": sorted(set(wrong.name)),
                         "price_flags_top75": int(len(bad[bad["rank"] <= 75])), "spinoffs": spin_check,
                         "acquisitions": [dict(target=a.target, date=str(a.date.date()), cash=a.cash,
                                               acquirer=a.acquirer, ratio=a.ratio) for a in ACQUISITIONS],
                         "estimated": {t: {"anchors": int(len(i["anchors"])),
                                           "worst_leave_one_out": float(i["loo"].abs().max()) if len(i["loo"]) else None}
                                       for t, i in estimates.items()},
                         "symbols_without_prices": missing},
               "scenarios": {}}
    curves = {}
    for sc_key, (sc_label, amount) in SCENARIOS.items():
        sc_out = {"label": sc_label, "periods": {}}
        for per_key, (p0, p1) in PERIODS.items():
            dates = [d for d in all_dates if pd.Timestamp(p0) <= d <= pd.Timestamp(p1)]
            per_end = calendar[calendar <= pd.Timestamp(p1)][-1]
            bench = run_benchmarks(adj, dates, per_end, amount)
            per_out = {"benchmarks": [row(f"All in {e}", b, bench, e) for e, b in bench.items()] + [cash_row(dates, amount)]}
            for etf in ETFS:
                res = run_etf(adj, actual, weights[etf], dates, per_end, amount)
                per_out[etf] = [row(f"Top {k}" if k != "ALL" else "All holdings (check)", r, bench, etf)
                                for k, r in res.items()]
                if per_key == "2020-2025":
                    per_out[f"{etf}_estimated_exposure"] = {str(k): estimated_exposure(r, estimates, adj, actual, per_end)
                                                            for k, r in res.items()}
                    per_out[f"{etf}_replica_cashed_out"] = [(t, str(d.date()), px) for t, d, px in res["ALL"].cashed_out]
                    curves[(sc_key, etf)] = {**{f"All in {e}": b.value_curve for e, b in bench.items()},
                                             **{f"Top {n}": res[n].value_curve for n in N_LIST}}
            sc_out["periods"][per_key] = per_out
            print(f"{sc_key:8s} {per_key:9s} done", flush=True)
        results["scenarios"][sc_key] = sc_out

    # ---- what the top-N lists looked like (first and last purchase)
    lists = []
    for etf in ETFS:
        for when, d in (("first purchase", all_dates[0]), ("last purchase", all_dates[-1])):
            w = weights[etf](d).sort_index().sort_values(ascending=False, kind="stable")
            for n in N_LIST:
                top = w.iloc[:n]
                lists += [{"etf": etf, "top_n": n, "when": when, "date": d.date(), "rank": i + 1, "ticker": t,
                           "share_of_each_1000": 1000 * v / top.sum()} for i, (t, v) in enumerate(top.items())]
    pd.DataFrame(lists).to_csv(res_dir / "top_n_lists.csv", index=False)
    results["dropped_weight_by_snapshot"] = {etf: {str(k.date()): v for k, v in paths[etf].dropped_weight.items()}
                                             for etf in ETFS}
    (res_dir / "results.json").write_text(json.dumps(results, indent=1, default=float))
    for (sc_key, etf), series in curves.items():  # daily values behind the charts
        pd.DataFrame(series).to_csv(res_dir / f"curves_{sc_key}_{etf}.csv", index_label="date")
    print_summary(results)


def print_summary(results):
    for sc_key, sc in results["scenarios"].items():
        per = sc["periods"]["2020-2025"]
        print(f"\n=== {sc['label']}, 2020-2025 ===")
        print(pd.DataFrame(per["benchmarks"])[["portfolio", "invested", "final_value", "annual_return"]]
              .to_string(index=False, float_format=lambda x: f"{x:,.3f}" if abs(x) < 5 else f"{x:,.0f}"))
        for etf in ETFS:
            print(f"-- {etf}")
            print(pd.DataFrame(per[etf])[["portfolio", "final_value", "vs_own_etf_pct", "vs_QQQ_pct", "vs_VGT_pct",
                                          "annual_return", "cash_from_buyouts"]]
                  .to_string(index=False, float_format=lambda x: f"{x:,.4f}" if abs(x) < 5 else f"{x:,.0f}"))


if __name__ == "__main__":
    main()
