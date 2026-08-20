"""Athlete Profile — one athlete, every metric they've logged, over time."""

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
    page_heading,
    stat_tile,
    tile_row,
)

ALL_METRICS = "__all__"


def _profile_choices(athletes: pd.DataFrame) -> dict[str, str]:
    """Everyone, inactive included — a coach still wants last year's numbers."""
    choices: dict[str, str] = {"": "— Choose athlete —"}
    for _, row in athletes.iterrows():
        label = logic.athlete_label(dict(row))
        choices[row["athlete_id"]] = label if row["active"] else f"{label} · inactive"
    return choices


@module.ui
def athlete_profile_ui(athletes: pd.DataFrame) -> TagList:
    return TagList(
        page_heading("Athlete Profile", "Every number, every date."),
        ui.tags.div(
            {"class": "panel panel-flush"},
            ui.tags.div(
                {"class": "panel-body"},
                ui.input_selectize(
                    "athlete",
                    "Athlete",
                    choices=_profile_choices(athletes),
                    selected="",
                    width="100%",
                    options={"placeholder": "— Choose athlete —"},
                ),
                ui.input_select(
                    "metric",
                    "Show",
                    choices={ALL_METRICS: "All metrics"},
                    width="100%",
                ),
            ),
        ),
        ui.output_ui("summary"),
        ui.output_ui("history"),
    )


@module.server
def athlete_profile_server(input, output, session) -> None:
    @reactive.effect
    def _sync_athletes() -> None:
        choices = _profile_choices(data.athletes())
        with reactive.isolate():
            current = input.athlete() or ""
        ui.update_selectize(
            "athlete", choices=choices, selected=current if current in choices else ""
        )

    @reactive.calc
    def athlete() -> dict[str, Any] | None:
        return logic.athlete_map(data.athletes()).get(input.athlete() or "")

    @reactive.calc
    def summary_table() -> pd.DataFrame:
        person = athlete()
        if person is None:
            return pd.DataFrame()
        return logic.athlete_metric_summary(data.entries(), data.metrics(), person["athlete_id"])

    @reactive.effect
    def _sync_metric_filter() -> None:
        summary = summary_table()
        choices = {ALL_METRICS: "All metrics"}
        choices.update({r["metric_id"]: r["metric_name"] for _, r in summary.iterrows()})
        with reactive.isolate():
            current = input.metric() or ALL_METRICS
        ui.update_select(
            "metric", choices=choices, selected=current if current in choices else ALL_METRICS
        )

    @render.ui
    def summary() -> TagChild:
        person = athlete()
        if person is None:
            return ui.tags.div(
                {"class": "panel panel-flush"},
                ui.tags.div(
                    {"class": "panel-body"},
                    empty_state("Choose an athlete", "Their full history shows up here."),
                ),
            )
        summary_df = summary_table()
        if summary_df.empty:
            return ui.tags.div(
                {"class": "panel panel-flush"},
                ui.tags.div(
                    {"class": "panel-body"},
                    empty_state(
                        f"Nothing logged yet for {person['name']}",
                        "Head to Quick Entry to record their first lift.",
                    ),
                ),
            )

        last_ts = summary_df["latest_ts"].max()
        rows = []
        classes = []
        for _, r in summary_df.iterrows():
            unit = str(r["unit"] or "")
            rows.append(
                [
                    ui.tags.b(r["metric_name"]),
                    logic.fmt_with_unit(r["pr_value"], unit),
                    logic.fmt_with_unit(r["latest_value"], unit),
                    logic.fmt_delta(r["change"], unit),
                    int(r["entries"]),
                    logic.fmt_date(r["latest_ts"]),
                ]
            )
            classes.append("" if r["known_metric"] else "row-orphan")

        return TagList(
            ui.tags.div(
                {"class": "panel panel-flush"},
                ui.tags.div(
                    {"class": "panel-head panel-head-tight"},
                    ui.tags.h2(person["name"]),
                    ui.tags.span(
                        " · ".join(
                            b
                            for b in [
                                str(person.get("position") or ""),
                                ""
                                if pd.isna(person.get("grad_year"))
                                else f"Class of {int(person['grad_year'])}",
                                "" if person["active"] else "INACTIVE",
                            ]
                            if b
                        ),
                        class_="panel-note",
                    ),
                ),
                ui.tags.div(
                    {"class": "panel-body"},
                    tile_row(
                        stat_tile("ENTRIES", f"{int(summary_df['entries'].sum())}"),
                        stat_tile("METRICS", f"{len(summary_df)}"),
                        stat_tile("LAST LOGGED", logic.fmt_date(last_ts), logic.time_ago(last_ts)),
                    ),
                ),
            ),
            ui.tags.div(
                {"class": "panel"},
                ui.tags.div({"class": "panel-head"}, ui.tags.h2("All metrics")),
                ui.tags.div(
                    {"class": "panel-body"},
                    data_table(
                        ["Metric", "PR", "Latest", "Change", "#", "Last"],
                        rows,
                        row_classes=classes,
                        align=["left", "right", "right", "right", "right", "right"],
                        max_height="26rem",
                    ),
                ),
            ),
        )

    @render.ui
    def history() -> TagChild:
        person = athlete()
        summary_df = summary_table()
        if person is None or summary_df.empty:
            return None
        selected = input.metric() or ALL_METRICS
        if selected != ALL_METRICS:
            summary_df = summary_df[summary_df["metric_id"] == selected]

        entries = data.entries()
        mine = entries[entries["athlete_id"] == person["athlete_id"]]
        mmap = logic.metric_map(data.metrics())

        blocks = []
        for _, row in summary_df.iterrows():
            metric = mmap.get(
                row["metric_id"],
                {
                    "metric_id": row["metric_id"],
                    "name": row["metric_name"],
                    "unit": row["unit"],
                    "higher_is_better": row["higher_is_better"],
                    "category": row["category"],
                },
            )
            blocks.append(_metric_block(mine, metric))
        return TagList(*blocks)


def _metric_block(mine: pd.DataFrame, metric: dict[str, Any]) -> TagChild:
    """One card: headline numbers, trend chart, and the full entry log."""
    unit = str(metric.get("unit") or "")
    hib = bool(metric.get("higher_is_better", True))
    group = mine[mine["metric_id"] == metric["metric_id"]].sort_values("ts", na_position="first")
    if group.empty:
        return None

    marked = logic.mark_prs(group, hib)
    best = logic.best_row(marked, hib)
    best_id = None if best is None else best["entry_id"]
    latest = marked.iloc[-1]

    rows, classes = [], []
    for _, r in marked.iloc[::-1].iterrows():
        badge: TagChild = ""
        if r["entry_id"] == best_id:
            badge = ui.tags.span("PR", class_="badge-pr")
        elif r["is_pr"]:
            badge = ui.tags.span("best then", class_="badge-then")
        elif r["is_first"]:
            badge = ui.tags.span("first", class_="badge-then")
        rows.append(
            [
                logic.fmt_datetime(r["ts"]),
                ui.tags.b(logic.fmt_value(r["value"], unit)),
                badge,
                r["notes"] or "—",
            ]
        )
        classes.append("row-best" if r["entry_id"] == best_id else "")

    return ui.tags.div(
        {"class": "panel"},
        ui.tags.div(
            {"class": "panel-head"},
            ui.tags.h2(metric["name"]),
            ui.tags.span(
                f"{metric.get('category', '')} · {'higher' if hib else 'lower'} is better",
                class_="panel-note",
            ),
        ),
        ui.tags.div(
            {"class": "panel-body"},
            tile_row(
                stat_tile(
                    "PR",
                    logic.fmt_with_unit(None if best is None else float(best["value"]), unit),
                    "—" if best is None else logic.fmt_day(best["ts"]),
                    tone="tile-pr",
                ),
                stat_tile(
                    "LATEST",
                    logic.fmt_with_unit(float(latest["value"]), unit),
                    logic.fmt_day(latest["ts"]),
                ),
                stat_tile("ENTRIES", f"{len(marked)}"),
            ),
            chart_block(charts.trend_chart(marked, metric)),
            data_table(
                ["When", unit or "Value", "", "Notes"],
                rows,
                row_classes=classes,
                align=["left", "right", "center", "left"],
                max_height="18rem",
            ),
        ),
    )
