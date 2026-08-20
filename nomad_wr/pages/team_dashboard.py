"""Team Dashboard — leaderboards per metric, with optional benchmark tiers."""

from __future__ import annotations

from typing import Any

import pandas as pd
from htmltools import TagChild, TagList
from shiny import module, reactive, render, ui

from .. import charts, data, logic
from ..ui_helpers import (
    chart_block,
    data_table,
    empty_state,
    legend,
    page_heading,
    stat_tile,
    tier_badge,
    tile_row,
)


def _first_metric_id(metrics: pd.DataFrame) -> str:
    return "" if metrics.empty else str(metrics.iloc[0]["metric_id"])


def _grad_choices(athletes: pd.DataFrame) -> dict[str, str]:
    years = sorted({int(y) for y in athletes["grad_year"].dropna().tolist()})
    return {"All": "All classes", **{str(y): f"Class of {y}" for y in years}}


@module.ui
def team_dashboard_ui(athletes: pd.DataFrame, metrics: pd.DataFrame) -> TagList:
    return TagList(
        page_heading("Team Dashboard", "Who's moving the needle."),
        ui.tags.div(
            {"class": "panel panel-flush"},
            ui.tags.div(
                {"class": "panel-body"},
                ui.input_select(
                    "metric",
                    "Metric",
                    choices=logic.metric_choices(metrics),
                    selected=_first_metric_id(metrics),
                    width="100%",
                ),
                ui.tags.div(
                    {"class": "filter-row"},
                    ui.input_select(
                        "grad", "Class", choices=_grad_choices(athletes), selected="All"
                    ),
                    ui.input_switch("include_inactive", "Include inactive", value=False),
                ),
            ),
        ),
        ui.output_ui("board"),
    )


@module.server
def team_dashboard_server(input, output, session) -> None:
    @reactive.effect
    def _sync_metrics() -> None:
        metrics = data.metrics()
        choices = logic.metric_choices(metrics)
        valid = {mid for group in choices.values() for mid in group}
        with reactive.isolate():
            current = input.metric() or ""
        ui.update_select(
            "metric",
            choices=choices,
            selected=current if current in valid else _first_metric_id(metrics),
        )

    @reactive.effect
    def _sync_grad() -> None:
        choices = _grad_choices(data.athletes())
        with reactive.isolate():
            current = input.grad() or "All"
        ui.update_select("grad", choices=choices, selected=current if current in choices else "All")

    @reactive.calc
    def metric() -> dict[str, Any] | None:
        return logic.metric_map(data.metrics()).get(input.metric() or "")

    @reactive.calc
    def cutoffs() -> dict[str, float] | None:
        current = metric()
        if current is None:
            return None
        return logic.benchmark_map(data.benchmarks()).get(current["metric_id"])

    @reactive.calc
    def board_df() -> pd.DataFrame:
        current = metric()
        if current is None:
            return pd.DataFrame()
        return logic.leaderboard(
            data.entries(),
            data.athletes(),
            current,
            benchmarks=cutoffs(),
            only_active=not input.include_inactive(),
            grad_year=input.grad(),
        )

    @render.ui
    def board() -> TagChild:
        current = metric()
        if current is None:
            return _wrap(empty_state("No metrics yet", "Add one in Admin to start ranking."))

        board = board_df()
        unit = str(current.get("unit") or "")
        if board.empty:
            return _wrap(
                empty_state(
                    f"No {current['name']} entries for this filter",
                    "Log some in Quick Entry, or widen the class filter.",
                )
            )

        tiers = cutoffs()
        best = board.iloc[0]
        median = float(board["best"].median())

        rows, classes = [], []
        for _, r in board.iterrows():
            sub_bits = [str(r["position"] or "")]
            if not pd.isna(r["grad_year"]):
                sub_bits.append(f"'{int(r['grad_year']) % 100:02d}")
            rows.append(
                [
                    str(int(r["rank"])),
                    ui.tags.div(
                        ui.tags.b(r["name"]),
                        ui.tags.div(" · ".join(b for b in sub_bits if b), class_="cell-sub"),
                    ),
                    ui.tags.div(
                        ui.tags.b(logic.fmt_value(r["best"], unit)),
                        ui.tags.div(logic.fmt_date(r["best_ts"]), class_="cell-sub"),
                    ),
                    tier_badge(r["tier"]),
                ]
            )
            classes.append("row-top" if r["rank"] == 1 else "")

        tier_note: TagChild = (
            legend(charts.tier_legend_items())
            if tiers
            else ui.tags.div(
                "No benchmark cutoffs set for this metric — showing raw ranking. "
                "Add cutoffs in Admin to turn on tiers.",
                class_="panel-note-block",
            )
        )
        order_note = "higher is better" if current["higher_is_better"] else "lower is better"

        return TagList(
            ui.tags.div(
                {"class": "panel panel-flush"},
                ui.tags.div(
                    {"class": "panel-head panel-head-tight"},
                    ui.tags.h2(current["name"]),
                    ui.tags.span(order_note, class_="panel-note"),
                ),
                ui.tags.div(
                    {"class": "panel-body"},
                    tile_row(
                        stat_tile("RANKED", f"{len(board)}", "athletes"),
                        stat_tile(
                            "TEAM BEST",
                            logic.fmt_with_unit(float(best["best"]), unit),
                            str(best["name"]),
                            tone="tile-pr",
                        ),
                        stat_tile("MEDIAN", logic.fmt_with_unit(median, unit)),
                    ),
                ),
            ),
            ui.tags.div(
                {"class": "panel"},
                ui.tags.div({"class": "panel-head"}, ui.tags.h2("Leaderboard")),
                ui.tags.div(
                    {"class": "panel-body"},
                    chart_block(charts.leaderboard_chart(board, current), fallback=""),
                    data_table(
                        ["#", "Athlete", f"Best ({unit})" if unit else "Best", "Tier"],
                        rows,
                        row_classes=classes,
                        align=["right", "left", "right", "center"],
                        max_height="30rem",
                    ),
                    tier_note,
                ),
            ),
        )


def _wrap(child: TagChild) -> TagChild:
    return ui.tags.div(
        {"class": "panel panel-flush"}, ui.tags.div({"class": "panel-body"}, child)
    )
