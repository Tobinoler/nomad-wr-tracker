"""Group — one screen showing what a whole lifting group has been hitting.

Built for the coach standing between the racks: assign up to eight athletes to
slots, switch on the lifts being trained today, and every athlete's last value
and PR is on screen at once. The question it answers is "what should this kid
have on the bar right now?", so the *last* number leads and the PR sits beside
it, with the recent history underneath for context.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
from htmltools import TagChild, TagList
from shiny import module, reactive, render, ui

from .. import data, logic
from ..ui_helpers import page_heading

SLOTS = 8
HISTORY_ROWS = 5
EMPTY_SLOT = ""


def _slot_choices(athletes: pd.DataFrame) -> dict[str, str]:
    """Names only — the box is narrow, and its sub-line already carries the rest."""
    active = athletes[athletes["active"]]
    return {EMPTY_SLOT: "— Empty —", **{r["athlete_id"]: str(r["name"]) for _, r in active.iterrows()}}


def _metric_choices(metrics: pd.DataFrame) -> dict[str, str]:
    return {r["metric_id"]: str(r["name"]) for _, r in metrics.iterrows()}


@module.ui
def group_ui(athletes: pd.DataFrame, metrics: pd.DataFrame) -> TagList:
    metric_options = _metric_choices(metrics)
    first_metric = [next(iter(metric_options))] if metric_options else []
    slot_options = _slot_choices(athletes)

    return TagList(
        page_heading("Group", "Who's lifting together, and what they've been hitting."),
        ui.tags.div(
            {"class": "panel"},
            # Controls live in the header rather than under the chips: every row
            # saved here is a row of athlete boxes that fits on the same screen.
            ui.tags.div(
                {"class": "panel-head"},
                ui.tags.h2("Lifts to show"),
                ui.tags.div(
                    {"class": "btn-row group-actions"},
                    ui.input_action_button("all_metrics", "All", class_="btn-admin btn-quiet"),
                    ui.input_action_button("no_metrics", "None", class_="btn-admin btn-quiet"),
                    ui.input_action_button(
                        "clear_slots", "Clear group", class_="btn-admin btn-quiet"
                    ),
                ),
            ),
            ui.tags.div(
                {"class": "panel-body"},
                ui.tags.div(
                    {"class": "metric-toggles"},
                    ui.input_checkbox_group(
                        "metrics",
                        None,
                        choices=metric_options,
                        selected=first_metric,
                        inline=True,
                    ),
                ),
            ),
        ),
        ui.tags.div(
            {"class": "group-grid"},
            *[
                ui.tags.div(
                    {"class": "group-slot"},
                    ui.tags.div(
                        {"class": "group-slot-head"},
                        ui.tags.span(str(i + 1), class_="slot-num"),
                        ui.input_selectize(
                            f"slot_{i}",
                            None,
                            choices=slot_options,
                            selected=EMPTY_SLOT,
                            width="100%",
                            options={"placeholder": "— Empty —"},
                        ),
                    ),
                    ui.output_ui(f"slot_body_{i}"),
                )
                for i in range(SLOTS)
            ],
        ),
    )


@module.server
def group_server(input, output, session) -> None:
    @reactive.effect
    def _sync_slots() -> None:
        choices = _slot_choices(data.athletes())
        for i in range(SLOTS):
            with reactive.isolate():
                current = input[f"slot_{i}"]() or EMPTY_SLOT
            ui.update_selectize(
                f"slot_{i}",
                choices=choices,
                selected=current if current in choices else EMPTY_SLOT,
            )

    @reactive.effect
    def _sync_metrics() -> None:
        choices = _metric_choices(data.metrics())
        with reactive.isolate():
            current = [m for m in (input.metrics() or []) if m in choices]
        if not current and choices:
            current = [next(iter(choices))]
        # inline must be repeated: sending choices re-renders the whole group.
        ui.update_checkbox_group("metrics", choices=choices, selected=current, inline=True)

    @reactive.effect
    @reactive.event(input.all_metrics)
    def _all_metrics() -> None:
        ui.update_checkbox_group("metrics", selected=list(_metric_choices(data.metrics())))

    @reactive.effect
    @reactive.event(input.no_metrics)
    def _no_metrics() -> None:
        ui.update_checkbox_group("metrics", selected=[])

    @reactive.effect
    @reactive.event(input.clear_slots)
    def _clear_slots() -> None:
        for i in range(SLOTS):
            ui.update_selectize(f"slot_{i}", selected=EMPTY_SLOT)

    @reactive.calc
    def chosen_metrics() -> list[dict[str, Any]]:
        """Selected metrics, in catalogue order rather than click order."""
        selected = set(input.metrics() or [])
        return [dict(r) for _, r in data.metrics().iterrows() if r["metric_id"] in selected]

    @reactive.calc
    def slot_ids() -> list[str]:
        return [input[f"slot_{i}"]() or EMPTY_SLOT for i in range(SLOTS)]

    @reactive.calc
    def group_entries() -> pd.DataFrame:
        """Every entry relevant to this screen, filtered once rather than 8 x N times."""
        athlete_ids = {a for a in slot_ids() if a}
        metric_ids = {m["metric_id"] for m in chosen_metrics()}
        entries = data.entries()
        if not athlete_ids or not metric_ids or entries.empty:
            return entries.iloc[0:0]
        return entries[
            entries["athlete_id"].isin(athlete_ids) & entries["metric_id"].isin(metric_ids)
        ]

    def _register_slot(index: int) -> None:
        @output(id=f"slot_body_{index}")
        @render.ui
        def _slot_body() -> TagChild:
            athlete = logic.athlete_map(data.athletes()).get(slot_ids()[index] or "")
            if athlete is None:
                return ui.tags.div(
                    {"class": "group-slot-empty"},
                    "Pick an athlete for this spot.",
                )
            metrics = chosen_metrics()
            if not metrics:
                return ui.tags.div(
                    {"class": "group-slot-empty"}, "Switch on a lift to see their numbers."
                )
            entries = group_entries()
            sub = _slot_sub(athlete)
            return TagList(
                ui.tags.div({"class": "group-slot-sub"}, sub) if sub else None,
                *[_metric_block(entries, athlete["athlete_id"], metric) for metric in metrics],
            )

    for _slot_index in range(SLOTS):
        _register_slot(_slot_index)


def _slot_sub(athlete: dict[str, Any]) -> str:
    """'RHP / OF · '27' — the picker shows the name, this carries the rest."""
    bits = [str(athlete.get("position") or "").strip()]
    grad = athlete.get("grad_year")
    if grad is not None and not pd.isna(grad):
        bits.append(f"'{int(grad) % 100:02d}")
    return " · ".join(b for b in bits if b)


def _metric_block(entries: pd.DataFrame, athlete_id: str, metric: dict[str, Any]) -> TagChild:
    """One lift inside one athlete's box: last, PR, then the recent numbers."""
    unit = str(metric.get("unit") or "")
    ref = logic.reference_for(
        entries, athlete_id, metric["metric_id"], bool(metric["higher_is_better"])
    )

    header = ui.tags.div(
        {"class": "gm-head"},
        ui.tags.span(str(metric["name"]), class_="gm-name"),
        ui.tags.span(unit, class_="gm-unit") if unit else None,
    )

    if ref["count"] == 0:
        return ui.tags.div(
            {"class": "gm"},
            header,
            ui.tags.div("Nothing logged yet", class_="gm-none"),
        )

    recent = ref["recent"].head(HISTORY_ROWS)
    rows = [
        ui.tags.div(
            {"class": "gm-row"},
            ui.tags.span(logic.fmt_date(row["ts"]), class_="gm-date"),
            ui.tags.span(logic.fmt_value(row["value"], unit), class_="gm-num"),
        )
        for _, row in recent.iterrows()
    ]
    more = ref["count"] - len(recent)

    return ui.tags.div(
        {"class": "gm"},
        header,
        ui.tags.div(
            {"class": "gm-stats"},
            ui.tags.div(
                {"class": "gm-stat"},
                ui.tags.span("LAST", class_="gm-k"),
                ui.tags.span(logic.fmt_value(ref["last_value"], unit), class_="gm-v"),
                ui.tags.span(logic.fmt_date(ref["last_ts"]), class_="gm-d"),
            ),
            ui.tags.div(
                {"class": "gm-stat gm-stat-pr"},
                ui.tags.span("PR", class_="gm-k"),
                ui.tags.span(logic.fmt_value(ref["pr_value"], unit), class_="gm-v"),
                ui.tags.span(logic.fmt_date(ref["pr_ts"]), class_="gm-d"),
            ),
        ),
        ui.tags.div({"class": "gm-hist"}, *rows),
        ui.tags.div(f"+{more} older", class_="gm-more") if more > 0 else None,
    )
