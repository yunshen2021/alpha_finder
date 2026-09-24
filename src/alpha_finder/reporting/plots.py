"""Report figures. Colors are the validated categorical slots from the dataviz palette
(QQQ, A, B, C keep the same color in every chart); text uses ink tokens, never series colors."""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
matplotlib.rcParams["text.parse_math"] = False  # "$1,000 ... $458,206" is money, not a math formula
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.ticker as mt  # noqa: E402
import pandas as pd  # noqa: E402

SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e1"
NEUTRAL = "#a3a29c"
COLORS = {"QQQ": "#2a78d6", "A": "#eb6834", "B": "#1baf7a", "C": "#eda100"}
LABELS = {
    "QQQ": "QQQ buy and hold",
    "A": "A: top-25 replaced monthly",
    "B": "B: rank buffer",
    "C": "C: buffer + tax gate",
}


def _style(ax):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=9, length=0)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def growth_chart(curves: dict[str, pd.Series], path: str, title: str, subtitle: str) -> None:
    fig, ax = plt.subplots(figsize=(9, 4.8), dpi=150, facecolor=SURFACE)
    _style(ax)
    ends = []
    for key, s in curves.items():
        ax.plot(s.index, s / 1e6, color=COLORS[key], linewidth=1.6, solid_capstyle="round",
                label=LABELS[key])
        ax.plot(s.index[-1], s.iloc[-1] / 1e6, "o", color=COLORS[key], markersize=6,
                markeredgecolor=SURFACE, markeredgewidth=1.5)
        ends.append((key, s.iloc[-1] / 1e6))
    # direct labels at line ends, spread vertically (in data units) so they never collide
    ends.sort(key=lambda kv: kv[1])
    ymax = max(y for _, y in ends)
    gap = 0.045 * ymax
    placed: list[float] = []
    for _, y in ends:
        placed.append(max(y, placed[-1] + gap) if placed else y)
    x_end = max(s.index[-1] for s in curves.values())
    for (key, y), y_lab in zip(ends, placed):
        ax.text(x_end + pd.Timedelta(days=45), y_lab, f"{key}  ${y:.2f}M", color=INK, fontsize=9,
                va="center", ha="left")
    ax.yaxis.set_major_formatter(mt.FuncFormatter(lambda v, _: f"${v:.1f}M"))
    ax.set_xlim(right=x_end + pd.Timedelta(days=330))
    ax.legend(loc="upper left", frameon=False, fontsize=9, labelcolor=INK2)
    fig.text(0.075, 0.955, title, color=INK, fontsize=12, fontweight="bold", ha="left")
    fig.text(0.075, 0.915, subtitle, color=INK2, fontsize=9, ha="left")
    fig.subplots_adjust(top=0.86, left=0.075, right=0.97, bottom=0.08)
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)


def drawdown_chart(curves: dict[str, pd.Series], path: str, title: str, subtitle: str) -> None:
    fig, ax = plt.subplots(figsize=(9, 3.8), dpi=150, facecolor=SURFACE)
    _style(ax)
    for key, s in curves.items():
        dd = s / s.cummax() - 1
        ax.plot(dd.index, dd * 100, color=COLORS[key], linewidth=1.6, label=LABELS[key])
        worst = dd.idxmin()
        ax.plot(worst, dd.min() * 100, "o", color=COLORS[key], markersize=6,
                markeredgecolor=SURFACE, markeredgewidth=1.5)
        ax.annotate(f"{dd.min() * 100:.0f}%", (worst, dd.min() * 100), xytext=(8, -2),
                    textcoords="offset points", color=INK, fontsize=9, va="center")
    ax.yaxis.set_major_formatter(mt.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    ax.legend(loc="lower left", frameon=False, fontsize=9, labelcolor=INK2)
    fig.text(0.075, 0.94, title, color=INK, fontsize=12, fontweight="bold", ha="left")
    fig.text(0.075, 0.89, subtitle, color=INK2, fontsize=9, ha="left")
    fig.subplots_adjust(top=0.82, left=0.075, right=0.97, bottom=0.08)
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)


def ladder_chart(rows: list[tuple[str, float, str]], path: str, title: str, subtitle: str) -> None:
    """Horizontal bars, one per rung. rows = (label, value in %, color key or 'ref')."""
    fig, ax = plt.subplots(figsize=(9, 4.2), dpi=150, facecolor=SURFACE)
    _style(ax)
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ys = list(range(len(rows)))[::-1]
    for y, (label, value, key) in zip(ys, rows):
        ax.barh(y, value, height=0.5, color=COLORS.get(key, NEUTRAL))
        ax.text(value + 0.25, y, f"{value:.1f}%", va="center", color=INK, fontsize=9)
    ax.set_yticks(ys)
    ax.set_yticklabels([r[0] for r in rows], color=INK, fontsize=9)
    ax.xaxis.set_major_locator(mt.MultipleLocator(5))
    ax.xaxis.set_major_formatter(mt.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    ax.set_xlim(0, max(r[1] for r in rows) * 1.15)
    fig.text(0.02, 0.945, title, color=INK, fontsize=12, fontweight="bold", ha="left")
    fig.text(0.02, 0.895, subtitle, color=INK2, fontsize=9, ha="left")
    fig.subplots_adjust(top=0.84, left=0.38, right=0.96, bottom=0.08)
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)


# --- version 3: top-N of an ETF versus the ETF itself ---------------------------------------

def topn_bars(labels: list[str], values: list[float], etf_value: float, etf_label: str, path: str,
              title: str, subtitle: str, invested: float, cash_label: str = "Money put in") -> None:
    """One bar per portfolio (top N, in order), a reference line at the ETF's value.

    Every bar is labeled with its value and its difference from the ETF, so nothing has to be
    read off the axis.
    """
    fig, ax = plt.subplots(figsize=(9, 4.6), dpi=150, facecolor=SURFACE)
    _style(ax)
    xs = list(range(len(labels)))
    ax.bar(xs, [v / 1e3 for v in values], width=0.5, color=COLORS["QQQ"])
    ax.axhline(etf_value / 1e3, color=COLORS["A"], linewidth=1.6, zorder=3)
    ax.axhline(invested / 1e3, color=NEUTRAL, linewidth=1.2, zorder=3)
    ax.set_xlim(-0.6, len(labels) + 0.9)  # free space on the right for the reference-line labels
    ax.text(len(labels) - 0.35, etf_value / 1e3, f"{etf_label}\n${etf_value / 1e3:,.1f}k", color=INK, fontsize=9,
            va="center", ha="left", linespacing=1.3)
    ax.text(len(labels) - 0.35, invested / 1e3, f"{cash_label}\n${invested / 1e3:,.1f}k", color=INK2, fontsize=9,
            va="center", ha="left", linespacing=1.3)
    top = max(values + [etf_value]) / 1e3
    for x, v in zip(xs, values):
        diff = v - etf_value
        ax.text(x, v / 1e3 + top * 0.012, f"${v / 1e3:,.1f}k\n{diff / etf_value:+.1%}", ha="center", va="bottom",
                color=INK, fontsize=8.5, linespacing=1.3)
    ax.set_xticks(xs)
    ax.set_xticklabels(labels, color=INK, fontsize=9)
    ax.set_ylim(0, top * 1.16)
    ax.yaxis.set_major_formatter(mt.FuncFormatter(lambda v, _: f"${v:,.0f}k"))
    fig.text(0.075, 0.945, title, color=INK, fontsize=12, fontweight="bold", ha="left")
    fig.text(0.075, 0.895, subtitle, color=INK2, fontsize=9, ha="left")
    fig.subplots_adjust(top=0.84, left=0.09, right=0.97, bottom=0.09)
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)


def topn_lines(curves: dict[str, pd.Series], path: str, title: str, subtitle: str,
               reference: tuple[str, pd.Series] | None = None) -> None:
    """Portfolio value over time. First curve is the ETF itself (blue); the others use the next slots.
    `reference` (label, series) is drawn in neutral gray, e.g. the money put in so far."""
    order = ["QQQ", "A", "B", "C"]
    fig, ax = plt.subplots(figsize=(9, 4.6), dpi=150, facecolor=SURFACE)
    _style(ax)
    ends = []
    if reference is not None:
        ref_label, ref = reference
        ax.plot(ref.index, ref / 1e3, color=NEUTRAL, linewidth=1.4, label=ref_label)
        ends.append((ref_label, ref.iloc[-1] / 1e3, ref.index[-1]))
    for key, (label, s) in zip(order, curves.items()):
        ax.plot(s.index, s / 1e3, color=COLORS[key], linewidth=1.6, solid_capstyle="round", label=label)
        ax.plot(s.index[-1], s.iloc[-1] / 1e3, "o", color=COLORS[key], markersize=6,
                markeredgecolor=SURFACE, markeredgewidth=1.5)
        ends.append((label, s.iloc[-1] / 1e3, s.index[-1]))
    ends.sort(key=lambda t: t[1])
    gap = 0.05 * max(e[1] for e in ends)
    placed: list[float] = []
    for _, y, _ in ends:
        placed.append(max(y, placed[-1] + gap) if placed else y)
    x_end = max(e[2] for e in ends)
    starts = [s.index[0] for s in curves.values()]
    span = x_end - min(starts)
    for (label, y, _), y_lab in zip(ends, placed):
        ax.text(x_end + span * 0.012, y_lab, f"{label}  ${y:,.0f}k", color=INK, fontsize=8.5, va="center", ha="left")
    ax.yaxis.set_major_formatter(mt.FuncFormatter(lambda v, _: f"${v:,.0f}k"))
    ax.set_xlim(right=x_end + span * 0.20)
    years = [pd.Timestamp(f"{y}-01-01") for y in range(min(starts).year, x_end.year + 2)
             if pd.Timestamp(f"{y}-01-01") <= x_end + pd.Timedelta(days=1)]
    ax.set_xticks(years)
    ax.set_xticklabels([str(y.year) for y in years])
    ax.legend(loc="upper left", frameon=False, fontsize=9, labelcolor=INK2)
    fig.text(0.075, 0.945, title, color=INK, fontsize=12, fontweight="bold", ha="left")
    fig.text(0.075, 0.895, subtitle, color=INK2, fontsize=9, ha="left")
    fig.subplots_adjust(top=0.84, left=0.09, right=0.97, bottom=0.09)
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)
