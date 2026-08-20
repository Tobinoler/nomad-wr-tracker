"""Admin — roster, metric catalogue and benchmark cutoffs. Coach-facing, plain."""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd
from htmltools import TagChild, TagList
from shiny import module, reactive, render, ui

from .. import data, logic, storage
from ..config import CATEGORIES, DATA_DIR, UNITS
from ..ui_helpers import alert, data_table, empty_state, page_heading, pill

log = logging.getLogger("nomad_wr.admin")

NEW = ""


def _athlete_pick_choices(athletes: pd.DataFrame) -> dict[str, str]:
    choices = {NEW: "+ New athlete"}
    for _, r in athletes.iterrows():
        choices[r["athlete_id"]] = r["name"] if r["active"] else f"{r['name']} (inactive)"
    return choices


def _metric_pick_choices(metrics: pd.DataFrame) -> dict[str, str]:
    return {NEW: "+ New metric", **{r["metric_id"]: r["name"] for _, r in metrics.iterrows()}}


def _parse_optional_float(text: Any) -> float | None:
    text = str(text or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        raise storage.StorageError(f"“{text}” isn't a number.") from None


def _parse_optional_int(text: Any) -> int | None:
    value = _parse_optional_float(text)
    return None if value is None else int(value)


@module.ui
def admin_ui(athletes: pd.DataFrame, metrics: pd.DataFrame) -> TagList:
    return TagList(
        page_heading("Admin", "Roster, metrics and benchmark cutoffs."),
        # ---- roster -------------------------------------------------------
        ui.tags.div(
            {"class": "panel"},
            ui.tags.div({"class": "panel-head"}, ui.tags.h2("Athletes")),
            ui.tags.div(
                {"class": "panel-body"},
                ui.input_select(
                    "athlete_pick",
                    "Add or edit",
                    choices=_athlete_pick_choices(athletes),
                    width="100%",
                ),
                ui.input_text("athlete_name", "Name", placeholder="First Last", width="100%"),
                ui.tags.div(
                    {"class": "filter-row"},
                    ui.input_text("athlete_grad", "Grad year", placeholder="2027"),
                    ui.input_select(
                        "athlete_pos1", "Position", choices=logic.position_choices(athletes)
                    ),
                    ui.input_select(
                        "athlete_pos2", "Second position", choices=logic.position_choices(athletes)
                    ),
                    ui.input_switch("athlete_active", "Active", value=True),
                ),
                ui.input_action_button("save_athlete", "Save athlete", class_="btn-admin"),
                ui.output_ui("athlete_feedback"),
                ui.output_ui("athlete_table"),
            ),
        ),
        # ---- metrics ------------------------------------------------------
        ui.tags.div(
            {"class": "panel"},
            ui.tags.div({"class": "panel-head"}, ui.tags.h2("Metrics")),
            ui.tags.div(
                {"class": "panel-body"},
                ui.input_select(
                    "metric_pick", "Add or edit", choices=_metric_pick_choices(metrics), width="100%"
                ),
                ui.input_text("metric_name", "Name", placeholder="Back Squat", width="100%"),
                ui.tags.div(
                    {"class": "filter-row"},
                    ui.input_select(
                        "metric_category", "Category", choices={c: c for c in CATEGORIES}
                    ),
                    ui.input_select("metric_unit", "Unit", choices={u: u for u in UNITS}),
                ),
                ui.input_switch("metric_hib", "Higher is better", value=True),
                ui.tags.div(
                    "Turn this off for timed metrics like a sprint, where a lower number wins. "
                    "It drives PR detection and tier colouring.",
                    class_="field-hint",
                ),
                ui.tags.div(
                    {"class": "btn-row"},
                    ui.input_action_button("save_metric", "Save metric", class_="btn-admin"),
                    ui.input_action_button(
                        "remove_metric", "Remove metric", class_="btn-admin btn-danger-soft"
                    ),
                ),
                ui.output_ui("remove_metric_confirm"),
                ui.output_ui("metric_feedback"),
                ui.output_ui("metric_table"),
            ),
        ),
        # ---- benchmarks ---------------------------------------------------
        ui.tags.div(
            {"class": "panel"},
            ui.tags.div({"class": "panel-head"}, ui.tags.h2("Benchmark cutoffs")),
            ui.tags.div(
                {"class": "panel-body"},
                ui.tags.div(
                    "Optional. Set the minimum value for each tier — leave a box blank to skip "
                    "that tier. Metrics with no cutoffs simply show a raw ranking.",
                    class_="field-hint",
                ),
                ui.input_select(
                    "bench_metric",
                    "Metric",
                    choices={r["metric_id"]: r["name"] for _, r in metrics.iterrows()},
                    width="100%",
                ),
                ui.tags.div(
                    {"class": "filter-row"},
                    ui.input_text("bench_elite", "Elite", placeholder="—"),
                    ui.input_text("bench_advanced", "Advanced", placeholder="—"),
                    ui.input_text("bench_average", "Average", placeholder="—"),
                ),
                ui.output_ui("bench_hint"),
                ui.tags.div(
                    {"class": "btn-row"},
                    ui.input_action_button("save_bench", "Save cutoffs", class_="btn-admin"),
                    ui.input_action_button("clear_bench", "Clear", class_="btn-admin btn-quiet"),
                ),
                ui.output_ui("bench_feedback"),
                ui.output_ui("bench_table"),
            ),
        ),
        ui.output_ui("data_files"),
    )


@module.server
def admin_server(input, output, session) -> None:
    athlete_msg: reactive.Value[tuple[str, str] | None] = reactive.value(None)
    metric_msg: reactive.Value[tuple[str, str] | None] = reactive.value(None)
    bench_msg: reactive.Value[tuple[str, str] | None] = reactive.value(None)
    pending_remove: reactive.Value[str | None] = reactive.value(None)

    # ---- roster -----------------------------------------------------------

    @reactive.effect
    def _sync_athlete_pick() -> None:
        athletes = data.athletes()
        choices = _athlete_pick_choices(athletes)
        with reactive.isolate():
            current = input.athlete_pick() or NEW
        ui.update_select(
            "athlete_pick", choices=choices, selected=current if current in choices else NEW
        )
        positions = logic.position_choices(athletes)
        with reactive.isolate():
            ui.update_select("athlete_pos1", choices=positions, selected=input.athlete_pos1())
            ui.update_select("athlete_pos2", choices=positions, selected=input.athlete_pos2())

    @reactive.effect
    @reactive.event(input.athlete_pick)
    def _load_athlete() -> None:
        # Straight from disk, not from the polled frame: right after a save the
        # poll can still be up to a second behind, and loading a blank form over
        # a just-created athlete is how you end up saving an empty name.
        roster = storage.load_athletes()
        athlete = logic.athlete_map(roster).get(input.athlete_pick() or "")
        if athlete is None:
            ui.update_text("athlete_name", value="")
            ui.update_text("athlete_grad", value="")
            ui.update_select("athlete_pos1", selected="")
            ui.update_select("athlete_pos2", selected="")
            ui.update_switch("athlete_active", value=True)
            return
        grad = athlete.get("grad_year")
        primary, secondary = logic.split_position(athlete.get("position") or "")
        positions = logic.position_choices(roster, extra=(primary, secondary))
        ui.update_text("athlete_name", value=str(athlete["name"]))
        ui.update_text("athlete_grad", value="" if pd.isna(grad) else str(int(grad)))
        ui.update_select("athlete_pos1", choices=positions, selected=primary)
        ui.update_select("athlete_pos2", choices=positions, selected=secondary)
        ui.update_switch("athlete_active", value=bool(athlete["active"]))

    @reactive.effect
    @reactive.event(input.save_athlete)
    def _save_athlete() -> None:
        try:
            grad = _parse_optional_int(input.athlete_grad())
            athlete_id = storage.save_athlete(
                name=input.athlete_name(),
                grad_year=grad,
                position=logic.join_position(input.athlete_pos1() or "", input.athlete_pos2() or ""),
                active=bool(input.athlete_active()),
                athlete_id=input.athlete_pick() or None,
            )
        except storage.StorageError as exc:
            athlete_msg.set(("error", str(exc)))
            return
        except Exception as exc:
            log.exception("Athlete save failed")
            athlete_msg.set(("error", f"Could not save: {exc}"))
            return
        verb = "Updated" if input.athlete_pick() else "Added"
        athlete_msg.set(("success", f"{verb} {input.athlete_name().strip()}."))
        # Send the refreshed choice list along with the selection: the browser
        # can't select an option it doesn't have yet, and leaving the form on
        # "+ New athlete" with the name still filled invites a duplicate row.
        ui.update_select(
            "athlete_pick",
            choices=_athlete_pick_choices(storage.load_athletes()),
            selected=athlete_id,
        )

    @render.ui
    def athlete_feedback() -> TagChild:
        msg = athlete_msg()
        return None if msg is None else alert(msg[1], tone=msg[0])

    @render.ui
    def athlete_table() -> TagChild:
        athletes = data.athletes()
        entries = data.entries()
        counts = entries["athlete_id"].value_counts() if not entries.empty else pd.Series(dtype=int)
        rows = [
            [
                ui.tags.b(r["name"]),
                "—" if pd.isna(r["grad_year"]) else str(int(r["grad_year"])),
                r["position"] or "—",
                str(int(counts.get(r["athlete_id"], 0))),
                pill("active", "pill-on") if r["active"] else pill("inactive", "pill-off"),
            ]
            for _, r in athletes.iterrows()
        ]
        return data_table(
            ["Name", "Class", "Pos", "Entries", ""],
            rows,
            align=["left", "right", "left", "right", "right"],
            max_height="22rem",
            empty_message="No athletes yet.",
        )

    # ---- metrics ----------------------------------------------------------

    @reactive.effect
    def _sync_metric_pick() -> None:
        metrics = data.metrics()
        choices = _metric_pick_choices(metrics)
        with reactive.isolate():
            current = input.metric_pick() or NEW
        ui.update_select(
            "metric_pick", choices=choices, selected=current if current in choices else NEW
        )
        bench_choices = {r["metric_id"]: r["name"] for _, r in metrics.iterrows()}
        with reactive.isolate():
            bench_current = input.bench_metric() or ""
        ui.update_select(
            "bench_metric",
            choices=bench_choices,
            selected=bench_current
            if bench_current in bench_choices
            else next(iter(bench_choices), None),
        )

    @reactive.effect
    @reactive.event(input.metric_pick)
    def _load_metric() -> None:
        # Switching the selection abandons any half-finished removal, so the
        # confirm box can never end up describing a different metric.
        pending_remove.set(None)
        metric = logic.metric_map(storage.load_metrics()).get(input.metric_pick() or "")
        if metric is None:
            ui.update_text("metric_name", value="")
            ui.update_select("metric_category", selected="Strength")
            ui.update_select("metric_unit", selected="lbs")
            ui.update_switch("metric_hib", value=True)
            return
        ui.update_text("metric_name", value=str(metric["name"]))
        ui.update_select("metric_category", selected=str(metric["category"] or "Other"))
        ui.update_select("metric_unit", selected=str(metric["unit"] or ""))
        ui.update_switch("metric_hib", value=bool(metric["higher_is_better"]))

    @reactive.effect
    @reactive.event(input.save_metric)
    def _save_metric() -> None:
        try:
            metric_id = storage.save_metric(
                name=input.metric_name(),
                category=input.metric_category(),
                unit=input.metric_unit(),
                higher_is_better=bool(input.metric_hib()),
                metric_id=input.metric_pick() or None,
            )
        except storage.StorageError as exc:
            metric_msg.set(("error", str(exc)))
            return
        except Exception as exc:
            log.exception("Metric save failed")
            metric_msg.set(("error", f"Could not save: {exc}"))
            return
        verb = "Updated" if input.metric_pick() else "Added"
        metric_msg.set(("success", f"{verb} {input.metric_name().strip()}."))
        ui.update_select(
            "metric_pick",
            choices=_metric_pick_choices(storage.load_metrics()),
            selected=metric_id,
        )

    # ---- removing a metric ------------------------------------------------
    # A metric nothing has been logged against goes straight away; one with
    # history asks first and says exactly what happens to that history.

    @reactive.effect
    @reactive.event(input.remove_metric)
    def _remove_metric() -> None:
        metric = logic.metric_map(storage.load_metrics()).get(input.metric_pick() or "")
        if metric is None:
            pending_remove.set(None)
            metric_msg.set(("error", "Pick a metric from the list first."))
            return
        if storage.count_entries_for_metric(metric["metric_id"]) > 0:
            metric_msg.set(None)
            pending_remove.set(metric["metric_id"])
            return
        _do_remove(metric["metric_id"])

    @reactive.effect
    @reactive.event(input.confirm_remove)
    def _confirm_remove() -> None:
        metric_id = pending_remove()
        if metric_id:
            _do_remove(metric_id)

    @reactive.effect
    @reactive.event(input.cancel_remove)
    def _cancel_remove() -> None:
        pending_remove.set(None)

    def _do_remove(metric_id: str) -> None:
        try:
            removed = storage.delete_metric(metric_id)
        except storage.StorageError as exc:
            pending_remove.set(None)
            metric_msg.set(("error", str(exc)))
            return
        except Exception as exc:
            log.exception("Metric removal failed")
            pending_remove.set(None)
            metric_msg.set(("error", f"Could not remove it: {exc}"))
            return

        pending_remove.set(None)
        ui.update_select(
            "metric_pick", choices=_metric_pick_choices(storage.load_metrics()), selected=NEW
        )
        note = f"Removed {removed['name']}."
        if removed["orphaned"]:
            plural = "entry" if removed["orphaned"] == 1 else "entries"
            note += (
                f" {removed['orphaned']} logged {plural} stayed in entries.csv — add a metric"
                f" named “{removed['name']}” again to reattach them."
            )
        if removed["had_benchmark"]:
            note += " Its benchmark cutoffs went with it."
        metric_msg.set(("info" if removed["orphaned"] else "success", note))

    @render.ui
    def remove_metric_confirm() -> TagChild:
        metric_id = pending_remove()
        if not metric_id:
            return None
        metric = logic.metric_map(data.metrics()).get(metric_id)
        if metric is None:
            return None
        count = storage.count_entries_for_metric(metric_id)
        plural = "entry" if count == 1 else "entries"
        return ui.tags.div(
            {"class": "confirm-box"},
            ui.tags.div(f"Remove {metric['name']}?", class_="confirm-title"),
            ui.tags.div(
                f"{count} {plural} already logged against it. Those rows stay in "
                f"entries.csv — nothing an athlete recorded gets deleted — but they drop off "
                f"the leaderboards and show as a missing metric on athlete profiles. "
                f"Adding “{metric['name']}” back later reattaches them.",
                class_="confirm-body",
            ),
            ui.tags.div(
                {"class": "btn-row"},
                ui.input_action_button(
                    "confirm_remove", f"Yes, remove {metric['name']}", class_="btn-admin btn-danger"
                ),
                ui.input_action_button("cancel_remove", "Keep it", class_="btn-admin btn-quiet"),
            ),
        )

    @render.ui
    def metric_feedback() -> TagChild:
        msg = metric_msg()
        return None if msg is None else alert(msg[1], tone=msg[0])

    @render.ui
    def metric_table() -> TagChild:
        metrics = data.metrics()
        entries = data.entries()
        counts = entries["metric_id"].value_counts() if not entries.empty else pd.Series(dtype=int)
        rows = [
            [
                ui.tags.b(r["name"]),
                r["category"],
                r["unit"] or "—",
                "higher" if r["higher_is_better"] else "lower",
                str(int(counts.get(r["metric_id"], 0))),
            ]
            for _, r in metrics.iterrows()
        ]
        return data_table(
            ["Metric", "Category", "Unit", "Better", "Entries"],
            rows,
            align=["left", "left", "left", "left", "right"],
            max_height="22rem",
            empty_message="No metrics yet.",
        )

    # ---- benchmarks -------------------------------------------------------

    @reactive.calc
    def bench_metric() -> dict[str, Any] | None:
        return logic.metric_map(data.metrics()).get(input.bench_metric() or "")

    @reactive.effect
    @reactive.event(input.bench_metric)
    def _load_bench() -> None:
        metric = bench_metric()
        cuts = {} if metric is None else logic.benchmark_map(storage.load_benchmarks()).get(
            metric["metric_id"], {}
        )
        for key, input_id in (
            ("elite", "bench_elite"),
            ("advanced", "bench_advanced"),
            ("average", "bench_average"),
        ):
            value = cuts.get(key)
            ui.update_text(input_id, value="" if value is None else storage.fmt_number(value))

    @render.ui
    def bench_hint() -> TagChild:
        metric = bench_metric()
        if metric is None:
            return None
        unit = str(metric.get("unit") or "")
        if metric["higher_is_better"]:
            text = f"Higher is better — Elite should be the biggest number ({unit})."
        else:
            text = f"Lower is better — Elite should be the smallest number ({unit})."
        return ui.tags.div(text, class_="field-hint")

    @reactive.effect
    @reactive.event(input.save_bench)
    def _save_bench() -> None:
        metric = bench_metric()
        if metric is None:
            bench_msg.set(("error", "Pick a metric first."))
            return
        try:
            cuts = {
                "elite": _parse_optional_float(input.bench_elite()),
                "advanced": _parse_optional_float(input.bench_advanced()),
                "average": _parse_optional_float(input.bench_average()),
            }
            storage.save_benchmark(metric["metric_id"], **cuts)
        except storage.StorageError as exc:
            bench_msg.set(("error", str(exc)))
            return
        except Exception as exc:
            log.exception("Benchmark save failed")
            bench_msg.set(("error", f"Could not save: {exc}"))
            return

        present = {k: v for k, v in cuts.items() if v is not None}
        if not present:
            bench_msg.set(("info", f"Cleared the cutoffs for {metric['name']}."))
        elif not logic.cutoffs_are_ordered(present, bool(metric["higher_is_better"])):
            direction = "descending" if metric["higher_is_better"] else "ascending"
            bench_msg.set(
                (
                    "warning",
                    f"Saved — but check the order. For {metric['name']} the cutoffs should run "
                    f"{direction} from Elite to Average.",
                )
            )
        else:
            bench_msg.set(("success", f"Saved cutoffs for {metric['name']}."))

    @reactive.effect
    @reactive.event(input.clear_bench)
    def _clear_bench() -> None:
        metric = bench_metric()
        if metric is None:
            return
        try:
            storage.save_benchmark(metric["metric_id"], None, None, None)
        except storage.StorageError as exc:
            bench_msg.set(("error", str(exc)))
            return
        for input_id in ("bench_elite", "bench_advanced", "bench_average"):
            ui.update_text(input_id, value="")
        bench_msg.set(("info", f"Cleared the cutoffs for {metric['name']}."))

    @render.ui
    def bench_feedback() -> TagChild:
        msg = bench_msg()
        return None if msg is None else alert(msg[1], tone=msg[0])

    @render.ui
    def bench_table() -> TagChild:
        benchmarks = data.benchmarks()
        mmap = logic.metric_map(data.metrics())
        if benchmarks.empty:
            return empty_state(
                "No benchmarks set",
                "Leaderboards will show plain rankings until you add cutoffs.",
            )
        rows = []
        for _, r in benchmarks.iterrows():
            metric = mmap.get(r["metric_id"])
            unit = str(metric.get("unit") or "") if metric else ""
            rows.append(
                [
                    ui.tags.b(metric["name"] if metric else r["metric_id"]),
                    logic.fmt_value(r["elite"], unit),
                    logic.fmt_value(r["advanced"], unit),
                    logic.fmt_value(r["average"], unit),
                ]
            )
        return data_table(
            ["Metric", "Elite", "Advanced", "Average"],
            rows,
            align=["left", "right", "right", "right"],
            max_height="18rem",
        )

    # ---- where the data lives --------------------------------------------

    @render.ui
    def data_files() -> TagChild:
        return ui.tags.div(
            {"class": "panel"},
            ui.tags.div({"class": "panel-head"}, ui.tags.h2("Data files")),
            ui.tags.div(
                {"class": "panel-body"},
                ui.tags.div(str(DATA_DIR), class_="mono-path"),
                data_table(
                    ["File", "Rows"],
                    [
                        ["athletes.csv", str(len(data.athletes()))],
                        ["metrics.csv", str(len(data.metrics()))],
                        ["entries.csv", str(len(data.entries()))],
                        ["benchmarks.csv", str(len(data.benchmarks()))],
                    ],
                    align=["left", "right"],
                    scroll=False,
                ),
                ui.tags.div(
                    "Entries are append-only. To correct a bad number, stop the app and edit "
                    "entries.csv directly, or log the right value again — the leaderboard uses "
                    "each athlete's best.",
                    class_="field-hint",
                ),
            ),
        )
