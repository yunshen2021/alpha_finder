"""Figures and tables for version_3_etf_topn/report.md, built from results/ so every number is exact.

Writes results/figures/*.png and results/tables.md (all tables). The report's text refers to these.
"""
import json
from pathlib import Path

import pandas as pd

from alpha_finder.backtest.dca import wednesday_schedule
from alpha_finder.reporting.plots import topn_bars, topn_lines

HERE = Path(__file__).resolve().parent
RES = HERE / "results"
FIG = RES / "figures"
N_LIST = [5, 10, 15, 20, 25, 50, 75]
ETFS = ["QQQ", "VGT"]
YEARS = [str(y) for y in range(2020, 2026)]


def money(x: float) -> str:
    return f"${x:,.0f}"


def signed(x: float) -> str:
    return f"{x:+.1%}"


def md(df: pd.DataFrame) -> str:
    head = "| " + " | ".join(df.columns) + " |"
    sep = "|" + "|".join("---" if i == 0 else "---:" for i in range(len(df.columns))) + "|"
    body = ["| " + " | ".join(str(v) for v in r) + " |" for r in df.itertuples(index=False)]
    return "\n".join([head, sep, *body])


def by_label(rows: list[dict]) -> dict[str, dict]:
    return {r["portfolio"]: r for r in rows}


def headline_table(period: dict, end_label: str = "Value on Dec 31, 2025") -> pd.DataFrame:
    bench = by_label(period["benchmarks"])
    out = []
    for label in ("All in QQQ", "All in VGT", "Cash (kept, no interest)"):
        r = bench[label]
        out.append((label, r))
    for etf in ETFS:
        rows = by_label(period[etf])
        for n in N_LIST:
            out.append((f"{etf} top {n}", rows[f"Top {n}"]))
        out.append((f"{etf} all holdings, never sold", rows["All holdings (check)"]))
    qqq, vgt = bench["All in QQQ"]["final_value"], bench["All in VGT"]["final_value"]
    return pd.DataFrame([{
        "Portfolio": label, "Money put in": money(r["invested"]), end_label: money(r["final_value"]),
        "vs all-in QQQ": signed(r["final_value"] / qqq - 1), "vs all-in VGT": signed(r["final_value"] / vgt - 1),
        "Yearly return*": f"{r['annual_return']:.1%}",
    } for label, r in out])


def yearly_table(scenario: dict, etf: str) -> pd.DataFrame:
    """Top-N minus the ETF itself, each period computed on its own."""
    cols = YEARS + ["2024-2025", "2020-2025"]
    rows = []
    for n in N_LIST:
        rec = {"Portfolio": f"{etf} top {n}"}
        for c in cols:
            rec[c] = signed(by_label(scenario["periods"][c][etf])[f"Top {n}"]["vs_own_etf_pct"])
        rows.append(rec)
    rec = {"Portfolio": f"{etf} all holdings, never sold"}
    for c in cols:
        rec[c] = signed(by_label(scenario["periods"][c][etf])["All holdings (check)"]["vs_own_etf_pct"])
    rows.append(rec)
    own = {"Portfolio": f"{etf} itself, return that period"}
    for c in cols:
        b = by_label(scenario["periods"][c]["benchmarks"])[f"All in {etf}"]
        own[c] = f"{b['final_value'] / b['invested'] - 1:.1%}"
    rows.append(own)
    best = {"Portfolio": "Best N that period"}
    for c in cols:
        vals = {n: by_label(scenario["periods"][c][etf])[f"Top {n}"]["final_value"] for n in N_LIST}
        best[c] = f"top {max(vals, key=vals.get)}"
    rows.append(best)
    return pd.DataFrame(rows)


def time_weighted(v: pd.Series, deposits: pd.Series) -> pd.Series:
    """Daily returns with each day's deposit removed: (V_t - deposit_t) / V_(t-1) - 1."""
    return ((v - deposits) / v.shift(1) - 1).iloc[1:]


def returns_tables(R: dict, curves: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Annual returns two ways (flat plan): money-weighted (your return with weekly deposits) and
    time-weighted (the strategy's own return, as funds report it), plus calendar years."""
    c = curves[("flat", "QQQ")]
    series = {"QQQ top 10": c["Top 10"], "VGT top 5": curves[("flat", "VGT")]["Top 5"],
              "All in QQQ": c["All in QQQ"], "All in VGT": c["All in VGT"]}
    deposits = pd.Series(0.0, index=c.index)
    deposits[wednesday_schedule("2020-01-01", "2025-12-31", c.index)] = 1000.0
    twr = {k: time_weighted(v, deposits) for k, v in series.items()}
    flat = R["scenarios"]["flat"]["periods"]

    def mwr(period, key):
        where = {"QQQ top 10": ("QQQ", "Top 10"), "VGT top 5": ("VGT", "Top 5"),
                 "All in QQQ": ("benchmarks", "All in QQQ"), "All in VGT": ("benchmarks", "All in VGT")}[key]
        return by_label(flat[period][where[0]])[where[1]]["annual_return"]

    def annualized(r):
        years = ((r.index[-1] - r.index[0]).days + 1) / 365.25
        return (1 + r).prod() ** (1 / years) - 1

    summary = pd.DataFrame([{
        "Portfolio": k,
        "2020-2025, your return (money-weighted)": f"{mwr('2020-2025', k):.1%}",
        "2020-2025, strategy return (time-weighted)": f"{annualized(r):.1%}",
        "2024-2025, your return (money-weighted)": f"{mwr('2024-2025', k):.1%}",
        "2024-2025, strategy return (time-weighted)": f"{annualized(r[r.index.year >= 2024]):.1%}",
    } for k, r in twr.items()])
    years = pd.DataFrame([{"Year": str(y), **{k: signed((1 + r[r.index.year == y]).prod() - 1) for k, r in twr.items()}}
                          for y in range(2020, 2026)])
    return summary, years


def money_in_curve(index: pd.DatetimeIndex, scenario: str) -> pd.Series:
    dates = wednesday_schedule("2020-01-01", "2025-12-31", index)
    amount = (lambda d: 1000.0) if scenario == "flat" else (lambda d: 1000.0 * 1.15 ** (d.year - 2020))
    s = pd.Series({d: amount(d) for d in dates}).cumsum()
    return s.reindex(index).ffill().fillna(0.0)


def main():
    R = json.loads((RES / "results.json").read_text())
    curves = {(sc, etf): pd.read_csv(RES / f"curves_{sc}_{etf}.csv", index_col="date", parse_dates=True)
              for sc in R["scenarios"] for etf in ETFS}
    FIG.mkdir(parents=True, exist_ok=True)
    parts = []

    for sc_key, sc in R["scenarios"].items():
        per = sc["periods"]["2020-2025"]
        parts.append(f"## {sc['label']}, 2020-2025\n\n" + md(headline_table(per)))
        for etf in ETFS:
            rows = by_label(per[etf])
            bench = by_label(per["benchmarks"])[f"All in {etf}"]
            topn_bars([f"Top {n}" for n in N_LIST], [rows[f"Top {n}"]["final_value"] for n in N_LIST],
                      bench["final_value"], f"All in {etf}", str(FIG / f"{sc_key}_{etf}_final_value_by_n.png"),
                      f"{etf}: value on Dec 31, 2025, by number of top holdings bought",
                      f"{sc['label']}, 2020-2025 ({money(bench['invested'])} put in); no taxes",
                      bench["invested"])
            c = curves[(sc_key, etf)]
            series = {f"All in {etf}": c[f"All in {etf}"], "Top 5": c["Top 5"], "Top 10": c["Top 10"],
                      "Top 25": c["Top 25"]}
            idx = c[f"All in {etf}"].index
            topn_lines(series, str(FIG / f"{sc_key}_{etf}_value_over_time.png"),
                       f"{etf}: value of the weekly purchases, 2020-2025", sc["label"],
                       reference=("Money put in", money_in_curve(idx, sc_key)))

    flat = R["scenarios"]["flat"]
    for etf in ETFS:
        parts.append(f"## {etf}: top N minus {etf} itself, each period on its own (flat $1,000 a week)\n\n"
                     + md(yearly_table(flat, etf)))

    parts.append("## $1,000 every week, 2024-2025 only\n\n" + md(headline_table(flat["periods"]["2024-2025"])))
    (RES / "tables.md").write_text("\n\n".join(parts) + "\n")

    # the report: narrative in report_template.md, tables filled in from the results
    tables = {
        "TABLE_FLAT": md(headline_table(R["scenarios"]["flat"]["periods"]["2020-2025"])),
        "TABLE_GROWING": md(headline_table(R["scenarios"]["growing"]["periods"]["2020-2025"])),
        "TABLE_2024_2025": md(headline_table(flat["periods"]["2024-2025"])),
        "TABLE_YEARLY_QQQ": md(yearly_table(flat, "QQQ")),
        "TABLE_YEARLY_VGT": md(yearly_table(flat, "VGT")),
    }
    summary, years = returns_tables(R, curves)
    tables["TABLE_RETURNS"] = md(summary)
    tables["TABLE_CALENDAR"] = md(years)
    template = (HERE / "report_template.md").read_text()
    for key, table in tables.items():
        template = template.replace("{{" + key + "}}", table)
    assert "{{" not in template, "unfilled placeholder in report_template.md"
    (HERE / "report.md").write_text(template)
    print("wrote report.md")


if __name__ == "__main__":
    main()
