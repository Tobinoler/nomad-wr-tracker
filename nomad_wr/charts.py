"""Plotly figures rendered to self-contained HTML fragments.

Plotly's JS is copied out of the installed ``plotly`` package into ``www/`` at
startup and loaded once in the page head, so the charts work on a gym Wi-Fi
network with no internet connection — no CDN, and no extra Python dependency
beyond plotly itself.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.graph_objects as go

from . import logic
from .config import TIERS, WWW_DIR

log = logging.getLogger("nomad_wr.charts")

# Chrome is monochrome to match the newsprint theme; the only colour left in a
# chart is the one carrying meaning — PR markers and benchmark tiers.
INK = "#0A0A0A"
INK_SOFT = "#3A3A3A"
GRID = "#E8E8E8"
MUTED = "#6B6B6B"
GOLD = "#B8860B"

TIER_COLORS = {
    "Elite": "#B8860B",
    "Advanced": "#1F6FB2",
    "Average": "#8A8A8A",
    "Below Average": "#B3261E",
}

FONT_FAMILY = (
    '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif'
)

PLOT_CONFIG: dict[str, Any] = {
    # No modebar and no drag: on a tablet, a draggable plot steals page scroll.
    "displayModeBar": False,
    "responsive": True,
    "scrollZoom": False,
    "doubleClick": False,
    "staticPlot": False,
}


def ensure_plotly_asset() -> bool:
    """Copy plotly.min.js into www/ if it isn't there yet. Returns True on success."""
    target = WWW_DIR / "plotly.min.js"
    if target.exists() and target.stat().st_size > 100_000:
        return True
    try:
        import plotly

        source = Path(plotly.__file__).parent / "package_data" / "plotly.min.js"
        WWW_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        log.info("Copied plotly.min.js into %s", WWW_DIR)
        return True
    except Exception:
        log.exception("Could not stage plotly.min.js — charts may not render offline")
        return False


def _base_layout(fig: go.Figure, height: int) -> None:
    fig.update_layout(
        height=height,
        template="plotly_white",
        font=dict(family=FONT_FAMILY, size=13, color=INK),
        margin=dict(l=8, r=14, t=18, b=8),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#FFFFFF",
        dragmode=False,
        showlegend=False,
        hoverlabel=dict(font_size=14, bgcolor=INK, font_color="#FFFFFF"),
    )
    fig.update_xaxes(
        gridcolor=GRID, linecolor=GRID, zeroline=False, ticks="outside",
        tickcolor=GRID, tickfont=dict(size=12, color=MUTED), automargin=True,
    )
    fig.update_yaxes(
        gridcolor=GRID, linecolor=GRID, zeroline=False,
        tickfont=dict(size=12, color=MUTED), automargin=True,
    )


def _to_html(fig: go.Figure) -> str:
    return fig.to_html(
        full_html=False,
        include_plotlyjs=False,  # loaded once from www/plotly.min.js
        config=PLOT_CONFIG,
        default_width="100%",
    )


def trend_chart(df: pd.DataFrame, metric: dict[str, Any], height: int = 260) -> str:
    """Date-axis line chart of one athlete's history for one metric, PRs starred."""
    plot_df = df[df["ts"].notna()].sort_values("ts")
    if plot_df.empty:
        return ""

    unit = str(metric.get("unit") or "")
    hib = bool(metric.get("higher_is_better", True))
    marked = logic.mark_prs(plot_df, hib)
    best = logic.best_value(marked["value"], hib)

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=marked["ts"],
            y=marked["value"],
            mode="lines+markers",
            line=dict(color=INK, width=2.5, shape="linear"),
            marker=dict(size=8, color=INK, line=dict(color="#FFFFFF", width=2)),
            hovertemplate="%{x|%b %d, %Y}<br><b>%{y}</b> " + unit + "<extra></extra>",
            name=str(metric.get("name", "")),
        )
    )

    prs = marked[marked["is_pr"]]
    if not prs.empty:
        fig.add_trace(
            go.Scatter(
                x=prs["ts"],
                y=prs["value"],
                mode="markers",
                marker=dict(size=15, color=GOLD, symbol="star", line=dict(color="#8A6410", width=1)),
                hovertemplate="PR · %{x|%b %d, %Y}<br><b>%{y}</b> " + unit + "<extra></extra>",
                name="PR",
            )
        )

    if best is not None and len(marked) > 1:
        fig.add_hline(
            y=best,
            line=dict(color=GOLD, width=1.5, dash="dot"),
            annotation_text=f"PR {logic.fmt_value(best, unit)}",
            annotation_position="top left",
            annotation_font=dict(color="#8A6410", size=12),
        )

    _base_layout(fig, height)
    fig.update_yaxes(title=None, ticksuffix=f" {unit}" if unit else None)
    fig.update_xaxes(title=None, type="date", tickformat="%b %d")
    if len(marked) == 1:  # a single point needs breathing room on both axes
        only = pd.Timestamp(marked["ts"].iloc[0])
        fig.update_xaxes(range=[only - pd.Timedelta(days=7), only + pd.Timedelta(days=7)])
    return _to_html(fig)


def leaderboard_chart(board: pd.DataFrame, metric: dict[str, Any], top_n: int = 15) -> str:
    """Ranked chart, best at the top, coloured by tier when benchmarks exist.

    Higher-is-better metrics get bars, where a longer bar honestly means better.
    Lower-is-better metrics (a timed sprint) get dots instead: a bar chart would
    draw the slowest athlete the longest, which reads backwards at a glance.
    """
    if board.empty:
        return ""
    unit = str(metric.get("unit") or "")
    higher_is_better = bool(metric.get("higher_is_better", True))
    top = board.head(top_n).iloc[::-1]  # plotly draws the first row at the bottom
    has_tiers = top["tier"].notna().any()
    colors = [TIER_COLORS.get(t, INK_SOFT) if has_tiers else INK for t in top["tier"]]
    names = [f"{n}  " for n in top["name"]]
    labels = [logic.fmt_value(v, unit) for v in top["best"]]
    hover = "<b>%{y}</b><br>%{x} " + unit + "<extra></extra>"

    if higher_is_better:
        fig = go.Figure(
            go.Bar(
                x=top["best"],
                y=names,
                orientation="h",
                marker=dict(color=colors, line=dict(width=0)),
                text=labels,
                textposition="outside",
                textfont=dict(size=13, color=INK),
                cliponaxis=False,
                hovertemplate=hover,
            )
        )
    else:
        fig = go.Figure(
            go.Scatter(
                x=top["best"],
                y=names,
                mode="markers+text",
                marker=dict(size=17, color=colors, line=dict(color="#FFFFFF", width=2)),
                text=labels,
                textposition="middle right",
                textfont=dict(size=13, color=INK),
                cliponaxis=False,
                hovertemplate=hover,
            )
        )

    _base_layout(fig, height=max(200, 34 * len(top) + 70))
    fig.update_xaxes(
        title=dict(
            text=f"{unit} · {'higher' if higher_is_better else 'lower'} is better".strip(" ·"),
            font=dict(size=11, color=MUTED),
        ),
        showgrid=True,
        rangemode="tozero" if higher_is_better else "normal",
    )
    fig.update_yaxes(title=None, showgrid=False, tickfont=dict(size=13, color=INK))
    fig.update_layout(margin=dict(l=8, r=56, t=10, b=34), bargap=0.32)
    return _to_html(fig)


def tier_legend_items() -> list[tuple[str, str]]:
    return [(tier, TIER_COLORS[tier]) for tier in TIERS]
