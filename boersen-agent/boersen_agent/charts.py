"""Static charts (PNG) for the PDF reports.

Colours: the validated default palette (dataviz reference). Three categorical slots only (the
all-pairs-safe set), status colours reserved for warnings and always paired with a text label, one
y-axis per chart, recessive grid, direct labels where colour would otherwise carry meaning alone.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

from . import rules as R  # noqa: E402
from .names import nice_name  # noqa: E402

SURFACE, INK, INK2, MUTED = "#fcfcfb", "#0b0b0b", "#52514e", "#898781"
GRID, AXIS = "#e1e0d9", "#c3c2b7"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a"]          # blue, orange, aqua (validated all-pairs)
GOOD, WARNING, SERIOUS, CRITICAL = "#0ca30c", "#fab219", "#ec835a", "#d03b3b"
DPI = 200

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 8.5, "axes.edgecolor": AXIS, "axes.labelcolor": INK2,
    "xtick.color": MUTED, "ytick.color": MUTED, "axes.facecolor": SURFACE, "figure.facecolor": SURFACE,
    "savefig.facecolor": SURFACE, "axes.titlesize": 9, "axes.titlecolor": INK, "legend.frameon": False,
})


def _axes(ax, grid_axis="y"):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.spines["left"].set_color(AXIS)
    ax.spines["bottom"].set_color(AXIS)
    ax.grid(axis=grid_axis, color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    ax.tick_params(length=0)


def _eur(v):
    a = abs(v)
    if a >= 1_000_000:
        return f"{v / 1_000_000:,.2f}M"
    if a >= 10_000:
        return f"{v / 1000:,.0f}k"
    if a >= 1000:
        return f"{v / 1000:,.1f}k"
    return f"{v:,.0f}"


def _save(fig, path: Path) -> Path:
    fig.tight_layout()
    fig.savefig(path, dpi=DPI)
    plt.close(fig)
    return path


def _short(name: str, n: int = 28) -> str:
    name = nice_name(name)
    return name if len(name) <= n else name[: n - 1] + "…"


# ------------------------------------------------------------------ daily
def allocation(rows: list[dict], path: Path) -> Path | None:
    """Position values in EUR, grouped by asset class (stock / fund / cash-like)."""
    data = [r for r in rows if r.get("value_eur")]
    if not data:
        return None
    group = {"stock": 0, "fund": 1, "bond": 2, "cash": 2}
    names = {0: "Stocks", 1: "Funds / ETFs", 2: "Cash-like (cash, bonds)"}
    data.sort(key=lambda r: r["value_eur"])
    fig, ax = plt.subplots(figsize=(7.2, 0.32 * len(data) + 0.9))
    y = range(len(data))
    cols = [SERIES[group.get(r.get("asset") or ("cash" if r.get("cash") else "stock"), 0)] for r in data]
    ax.barh(list(y), [r["value_eur"] for r in data], height=0.68, color=cols)
    ax.set_yticks(list(y), [_short(r["name"]) for r in data], color=INK2)
    top = max(r["value_eur"] for r in data)
    for i, r in enumerate(data):
        ax.text(r["value_eur"] + top * 0.01, i, _eur(r["value_eur"]) + " €", va="center", color=INK2, fontsize=7.5)
    ax.set_xlim(0, top * 1.15)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: _eur(v)))
    _axes(ax, "x")
    used = sorted({group.get(r.get("asset") or ("cash" if r.get("cash") else "stock"), 0) for r in data})
    ax.legend(handles=[Patch(facecolor=SERIES[g], label=names[g]) for g in used], loc="lower right", fontsize=7.5)
    return _save(fig, path)


def stop_distance(items: list[dict], path: Path, near_pct: float) -> Path | None:
    """Distance of each position's price to its 9-month stop, in %. Status colour + text label below
    the warning threshold."""
    data = [i for i in items if i.get("dist") is not None]
    if not data:
        return None
    data.sort(key=lambda i: i["dist"], reverse=True)
    fig, ax = plt.subplots(figsize=(7.2, 0.32 * len(data) + 0.9))
    for k, i in enumerate(data):
        d = i["dist"]
        broke = d < 0 or i.get("breach")
        col = CRITICAL if broke else SERIOUS if d < near_pct else SERIES[0]
        ax.barh(k, d, height=0.68, color=col)
        label = f"{d:+.1f}%" + ("  ✕ broke the 9-month low" if broke else "  ⚠ near stop" if d < near_pct else "")
        ax.text(d + (0.6 if d >= 0 else -0.6), k, label, va="center", ha="left" if d >= 0 else "right",
                color=INK2, fontsize=7.5)
    ax.axvline(near_pct, color=MUTED, linewidth=0.8, linestyle=(0, (3, 3)))
    ax.text(near_pct, len(data) - 0.4, f" {near_pct:g}% watch line", color=MUTED, fontsize=7, va="bottom")
    ax.axvline(0, color=AXIS, linewidth=0.8)
    ax.set_yticks(range(len(data)), [_short(i["name"]) for i in data], color=INK2)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}%"))
    lo = min(0, min(i["dist"] for i in data)) - 5
    ax.set_xlim(lo, max(i["dist"] for i in data) * 1.25 + 5)
    _axes(ax, "x")
    return _save(fig, path)


def price_vs_stop(panels: list[dict], path: Path, weeks: int = 39) -> Path | None:
    """Small multiples: weekly closes (1 year) with the rolling 9-month stop below them."""
    panels = [p for p in panels if len(p["weekly"]) > weeks + 4][:6]
    if not panels:
        return None
    cols = 3 if len(panels) > 4 else 2 if len(panels) > 1 else 1
    rows_n = (len(panels) + cols - 1) // cols
    fig, axes = plt.subplots(rows_n, cols, figsize=(7.2, 1.55 * rows_n + 0.2), squeeze=False)
    for ax, p in zip([a for r in axes for a in r], panels):
        wk = p["weekly"]
        closes = [c for _, c in wk]
        stop = [min(closes[i - weeks:i]) if i >= weeks else None for i in range(len(closes))]
        start = max(0, len(wk) - 52)
        dates = [R.d(d) for d, _ in wk][start:]
        ax.plot(dates, closes[start:], color=SERIES[0], linewidth=1.6)
        ax.step(dates, stop[start:], where="post", color=MUTED, linewidth=1.0, linestyle=(0, (3, 2)))
        if stop[-1] is not None:
            ax.text(dates[-1], stop[-1], f" stop {stop[-1]:,.2f}", color=MUTED, fontsize=7, va="top")
        ax.text(dates[-1], closes[-1], f" {closes[-1]:,.2f}", color=INK2, fontsize=7, va="bottom")
        ax.set_title(_short(p["name"], 26 if cols == 3 else 40), loc="left")
        ax.xaxis.set_major_locator(mdates.MonthLocator(bymonth=[1, 7] if cols == 3 else [1, 4, 7, 10]))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %y"))
        ax.margins(x=0.12)
        _axes(ax)
    for ax in [a for r in axes for a in r][len(panels):]:
        ax.axis("off")
    return _save(fig, path)


def value_history(snapshots: list[tuple[str, float]], path: Path) -> Path | None:
    pts = [(R.d(d), v) for d, v in snapshots if v]
    if len(pts) < 2:
        return None
    fig, ax = plt.subplots(figsize=(7.2, 2.2))
    ax.plot([p[0] for p in pts], [p[1] for p in pts], color=SERIES[0], linewidth=1.6)
    ax.text(pts[-1][0], pts[-1][1], f" {_eur(pts[-1][1])} €", color=INK2, fontsize=7.5, va="center")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: _eur(v)))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    ax.margins(x=0.1)
    _axes(ax)
    return _save(fig, path)


# ------------------------------------------------------------------ issue report
def gesamt_history(issues: list[dict], path: Path) -> Path | None:
    """Positive votes of the Gesamtsystem per issue (0-3), labelled with the score."""
    data = [i for i in issues if i.get("score")]
    if len(data) < 2:
        return None
    fig, ax = plt.subplots(figsize=(7.2, 2.3))
    x = list(range(len(data)))
    for k, i in enumerate(data):
        pos, neg = i["score"]
        ax.bar(k, pos, width=0.7, color=GOOD if pos > neg else CRITICAL)
        ax.text(k, pos + 0.05, f"{pos}:{neg}", ha="center", va="bottom", color=INK2, fontsize=6.5)
    ax.set_xticks(x, [R.d(i["date"]).strftime("%d.%m.") for i in data], rotation=90, fontsize=6.5)
    ax.set_yticks([0, 1, 2, 3])
    ax.set_ylim(0, 3.5)
    ax.set_ylabel("positive votes")
    ax.axhline(1.5, color=MUTED, linewidth=0.8, linestyle=(0, (3, 3)))
    ax.set_xlim(-0.6, len(data) + 1.6)
    ax.text(len(data) - 0.3, 1.5, "positive\nabove", color=MUTED, fontsize=6.5, ha="left", va="center")
    _axes(ax)
    return _save(fig, path)


def zins_history(rows: list[tuple[str, float | None, float | None]], path: Path) -> Path | None:
    """Yield curve per issue: value printed in the newsletter vs. computed by the agent."""
    pub = [(R.d(d), p) for d, p, _ in rows if p is not None]
    comp = [(R.d(d), c) for d, _, c in rows if c is not None]
    if len(pub) < 2 and len(comp) < 2:
        return None
    fig, ax = plt.subplots(figsize=(7.2, 2.3))
    for pts, col, label in ((pub, SERIES[0], "newsletter"), (comp, SERIES[1], "computed")):
        if len(pts) >= 2:
            ax.plot([p[0] for p in pts], [p[1] for p in pts], color=col, linewidth=1.6, label=label)
            ax.text(pts[-1][0], pts[-1][1], f" {label} {pts[-1][1]:.2f}", color=INK2, fontsize=7, va="center")
    ax.axhline(0, color=CRITICAL, linewidth=0.8)
    ax.text(1.0, 0.0, "warning below 0 ", color=CRITICAL, fontsize=6.5, va="bottom", ha="right",
            transform=ax.get_yaxis_transform())
    top = max([v for _, v in pub + comp] + [0.1])
    ax.set_ylim(min(0, min(v for _, v in pub + comp)) - 0.05, top * 1.45)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %y"))
    ax.margins(x=0.18)
    ax.legend(loc="upper left", fontsize=7, ncol=2)
    _axes(ax)
    return _save(fig, path)


def rs_kuv(universe: list[dict], highlights: dict[str, list[dict]], top_n: int, kuv_max: float,
           path: Path) -> Path | None:
    """The ~700-stock list: relative strength vs. price/sales (log), with three highlighted groups."""
    pts = [u for u in universe if u.get("rs") and u.get("kuv")]
    if len(pts) < 20:
        return None
    fig, ax = plt.subplots(figsize=(7.2, 4.0))
    ax.set_yscale("log")
    ax.scatter([u["rs"] for u in pts], [u["kuv"] for u in pts], s=6, color=AXIS, linewidths=0, label="stock list")
    labelled = []
    for (label, items), col in zip(highlights.items(), SERIES):
        it = [i for i in items if i.get("rs") and i.get("kuv")]
        if not it:
            continue
        ax.scatter([i["rs"] for i in it], [i["kuv"] for i in it], s=26, color=col, edgecolors=SURFACE,
                   linewidths=0.8, label=label, zorder=3)
        labelled += it[:12]
    # Greedy label placement: try four offsets, keep the first that does not overlap a placed label.
    fig.canvas.draw()
    placed: list[tuple[float, float, float, float]] = []
    for i in sorted(labelled, key=lambda i: -i["rs"]):
        text = _short(i["name"], 18)
        px, py = ax.transData.transform((i["rs"], i["kuv"]))      # display pixels at fig.dpi
        pt = fig.dpi / 72                                          # points -> pixels
        w, h = 0.58 * 6.5 * len(text) * pt, 6.5 * 1.25 * pt          # approx. text box at 6.5 pt
        for dx, dy, ha in ((4, 2, "left"), (4, -9, "left"), (-4, 2, "right"), (-4, -9, "right")):
            sx, sy = dx * pt, dy * pt
            x0 = px + sx if ha == "left" else px + sx - w
            box_ = (x0, py + sy, x0 + w, py + sy + h)
            if not any(box_[0] < b[2] and b[0] < box_[2] and box_[1] < b[3] and b[1] < box_[3] for b in placed):
                break
        placed.append(box_)
        ax.annotate(text, (i["rs"], i["kuv"]), xytext=(dx, dy), textcoords="offset points", ha=ha,
                    fontsize=6.5, color=INK2)
    ranked = sorted((u["rs"] for u in universe if u.get("rs")), reverse=True)
    if len(ranked) >= top_n:
        cut = ranked[top_n - 1]
        ax.axvline(cut, color=MUTED, linewidth=0.8, linestyle=(0, (3, 3)))
        ax.text(cut, 0.97, f" top {top_n} by RS →", color=MUTED, fontsize=6.5, va="top",
                transform=ax.get_xaxis_transform())
    ax.axhline(kuv_max, color=MUTED, linewidth=0.8, linestyle=(0, (3, 3)))
    ax.text(0.995, kuv_max, f"KUV {kuv_max:g} (normal ≤) ", color=MUTED, fontsize=6.5, va="bottom", ha="right",
            transform=ax.get_yaxis_transform())
    ax.set_yscale("log")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
    ax.set_xlabel("relative strength (price ÷ 15-month median × 100)")
    ax.set_ylabel("KUV (price / sales, log)")
    ax.legend(loc="upper right", fontsize=7)
    _axes(ax, "both")
    return _save(fig, path)
