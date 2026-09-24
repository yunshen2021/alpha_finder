"""Today's top N holdings of QQQ and VGT, and how a weekly amount would be split between them.

Uses each fund's latest SEC filing, with every weight moved by its stock's price up to the last close
(the same method the backtest uses between filings). Index changes after the filing date are not
captured; for the largest holdings that rarely matters.

    export SEC_USER_AGENT="Your Name your.email@example.com"
    python version_3_etf_topn/current_top.py                  # QQQ top 10 and VGT top 5, $1,000
    python version_3_etf_topn/current_top.py --qqq 25 --vgt 10 --amount 2011.36
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_holdings import ETFS, MANUAL_TICKERS, OUT, RAW, SEC_LIST  # noqa: E402

from alpha_finder.backtest.dca import drift_prices  # noqa: E402
from alpha_finder.data.nport import (  # noqa: E402
    download_filing, equity_holdings, list_nport_filings, map_to_tickers, match_by_name, parse_nport, read_header,
)
from alpha_finder.data.prices import load_prices  # noqa: E402


def is_regular_split(r: float) -> bool:
    return any(abs(r - p / q) < 1e-4 for p in range(1, 51) for q in range(1, 11))


def latest_filing(etf: str) -> Path:
    spec = ETFS[etf]
    since = (pd.Timestamp.today() - pd.DateOffset(months=9)).date().isoformat()
    filings = list_nport_filings(spec["cik"], since, date.today().isoformat())
    folder = RAW / etf
    index_path = folder / "_series_index.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {}
    for _, f in filings.sort_values("report_date", ascending=False).iterrows():
        if spec["series_contains"] is not None:
            if f.accession not in index:
                index[f.accession] = read_header(spec["cik"], f.accession)["series_name"]
                index_path.write_text(json.dumps(index, indent=1))
            if spec["series_contains"] not in index[f.accession].lower():
                continue
        return download_filing(spec["cik"], f.accession, folder / f"{f.report_date}.xml")
    sys.exit(f"no recent filing found for {etf}")


def current_weights(etf: str, asof: date) -> tuple[pd.Series, pd.Timestamp, pd.Timestamp]:
    meta, df = parse_nport(latest_filing(etf).read_bytes())
    snap = pd.Timestamp(meta["report_date"])
    eq = equity_holdings(df)
    eq.insert(0, "snapshot_date", meta["report_date"])
    eq = map_to_tickers(eq, OUT / "id_to_ticker.csv")
    titles = {v["title"]: v["ticker"] for v in json.loads(SEC_LIST.read_text()).values()} if SEC_LIST.exists() else {}
    eq = match_by_name(eq, titles, MANUAL_TICKERS)
    w = eq[eq.symbol != ""].groupby("symbol")["pct_val"].sum()
    unmapped = eq.loc[eq.symbol == "", "pct_val"].sum()
    prices = load_prices(list(w.index), (snap - pd.Timedelta(days=10)).date(), asof, refresh=True)
    last = max(df.index.max() for df in prices.values())
    moved = {}
    for t, px in prices.items():
        odd = [d for d, r in px["splits"].items() if r > 0 and not is_regular_split(r)]  # spin-offs: undo
        p = drift_prices(px["close"], px["splits"].fillna(0.0), odd).ffill()
        base = p.loc[:snap].dropna()
        if len(base) and pd.notna(p.iloc[-1]):
            moved[t] = w[t] * p.iloc[-1] / base.iloc[-1]
    moved = pd.Series(moved)
    print(f"{etf}: filing as of {snap.date()}, prices to {last.date()}; "
          f"{len(moved)} of {len(w)} holdings priced; {unmapped:.2f}% of the fund's weight had no ticker")
    return (moved / moved.sum()).sort_values(ascending=False), snap, last


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--qqq", type=int, default=10)
    ap.add_argument("--vgt", type=int, default=5)
    ap.add_argument("--amount", type=float, default=1000.0)
    ap.add_argument("--asof", type=date.fromisoformat, default=date.today() - pd.Timedelta(days=1),
                    help="use closing prices up to this date (default: yesterday, a completed trading day)")
    args = ap.parse_args()
    for etf, n in (("QQQ", args.qqq), ("VGT", args.vgt)):
        w, snap, last = current_weights(etf, args.asof)
        top = w.iloc[:n]
        table = pd.DataFrame({"weight in fund": top.map(lambda x: f"{x:.2%}"),
                              f"share of ${args.amount:,.0f}": (args.amount * top / top.sum()).map(lambda x: f"${x:,.2f}")})
        table.index = [f"{i + 1}. {t}" for i, t in enumerate(top.index)]
        print(f"\n{etf} top {n} (together {top.sum():.1%} of the fund); next: "
              + ", ".join(f"{t} {v:.2%}" for t, v in w.iloc[n:n + 3].items()))
        print(table.to_string())
        print()


if __name__ == "__main__":
    main()
