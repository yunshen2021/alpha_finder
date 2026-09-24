"""Report figures. Colors are the validated categorical slots from the dataviz palette
(QQQ, A, B, C keep the same color in every chart); text uses ink tokens, never series colors."""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
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
