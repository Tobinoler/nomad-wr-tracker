"""Quick Entry — the page an athlete taps thirty seconds after a set.

Everything here is tuned for one thing: athlete -> lift -> number -> Save, with
the athlete's last value and PR visible while they type.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd
from htmltools import TagChild, TagList
from shiny import module, reactive, render, ui

from .. import data, logic, storage
from ..ui_helpers import alert, big_number_input, data_table, empty_state, page_heading, stat_tile

log = logging.getLogger("nomad_wr.quick_entry")

ATHLETE_PLACEHOLDER = "— Choose athlete —"
METRIC_PLACEHOLDER = "— Choose lift / metric —"

# Spinner granularity per unit; typing any number is still allowed.
STEP_BY_UNIT = {"sec": 0.01, "mph": 0.1, "%": 0.1, "in": 0.5, "cm": 0.5, "kg": 0.5, "lbs": 5.0}


def _parse_grad(text: Any) -> int | None:
    """Grad year from the self-signup box. Blank is fine; nonsense is not."""
    text = str(text or "").strip()
    if not text:
        return None
    try:
        year = int(float(text))
    except ValueError:
        raise storage.StorageError("Grad year should be a number like 2027.") from None
    if not 1900 <= year <= 2100:
        raise storage.StorageError("That grad year doesn't look right.")
    return year


@module.ui
def quick_entry_ui(athletes: pd.DataFrame, metrics: pd.DataFrame) -> TagList:
    return TagList(
        page_heading("Quick Entry", "Log it right after the set."),
        ui.tags.div(
            {"class": "panel panel-flush"},
            ui.tags.div(
                {"class": "panel-body"},
                ui.input_selectize(
                    "athlete",
                    "Athlete",
                    choices={"": ATHLETE_PLACEHOLDER, **logic.athlete_choices(athletes)},
                    selected="",
                    width="100%",
                    options={"placeholder": ATHLETE_PLACEHOLDER},
                ),
                ui.output_ui("add_me"),
                ui.output_ui("add_me_msg"),
                ui.input_select(
                    "metric",
                    "Lift / Metric",
                    choices={"": METRIC_PLACEHOLDER, **logic.metric_choices(metrics)},
                    selected="",
                    width="100%",
                ),
            ),
        ),
        ui.output_ui("reference"),
        ui.tags.div(
            {"class": "panel panel-flush"},
            ui.tags.div(
                {"class": "panel-body"},
                ui.output_ui("entry_form"),
                ui.input_action_button("save", "SAVE ENTRY", class_="btn-save", width="100%"),
            ),
        ),
        ui.output_ui("confirmation"),
        ui.output_ui("recent"),
    )


@module.server
def quick_entry_server(input, output, session) -> None:
    result: reactive.Value[dict[str, Any] | None] = reactive.value(None)
    reset_token: reactive.Value[int] = reactive.value(0)
    show_add: reactive.Value[bool] = reactive.value(False)
    add_msg: reactive.Value[tuple[str, str] | None] = reactive.value(None)

    # -- keep the two pickers in sync with the catalogue files ---------------

    @reactive.effect
    def _sync_athletes() -> None:
        choices = {"": ATHLETE_PLACEHOLDER, **logic.athlete_choices(data.athletes())}
        with reactive.isolate():
            current = input.athlete() or ""
        ui.update_selectize(
            "athlete", choices=choices, selected=current if current in choices else ""
        )

    @reactive.effect
    def _sync_metrics() -> None:
        choices = {"": METRIC_PLACEHOLDER, **logic.metric_choices(data.metrics())}
        with reactive.isolate():
            current = input.metric() or ""
        valid = {mid for group in choices.values() for mid in (group if isinstance(group, dict) else {})}
        ui.update_select("metric", choices=choices, selected=current if current in valid else "")

    # -- self-signup ---------------------------------------------------------
    # Athletes can put themselves on the roster from here, so nobody needs to be
    # sent to the Admin page — where the same tap could edit the metric
    # catalogue and change PR detection for the whole team.

    @reactive.effect
    @reactive.event(input.open_add)
    def _open_add() -> None:
        add_msg.set(None)
        show_add.set(True)

    @reactive.effect
    @reactive.event(input.cancel_add)
    def _cancel_add() -> None:
        add_msg.set(None)
        show_add.set(False)

    @reactive.effect
    @reactive.event(input.add_submit)
    def _add_me() -> None:
        try:
            athlete_id, created = storage.add_athlete_if_new(
                name=input.add_name(),
                grad_year=_parse_grad(input.add_grad()),
                position=logic.join_position(input.add_pos1() or "", input.add_pos2() or ""),
            )
        except storage.StorageError as exc:
            add_msg.set(("error", str(exc)))
            return
        except Exception as exc:  # a failed signup must never take down the kiosk
            log.exception("Self-signup failed")
            add_msg.set(("error", f"Could not add you: {exc}"))
            return

        # Fresh from disk, not the polled frame: the poll can be up to a second
        # behind the write, and the browser can't select an option it lacks.
        roster = storage.load_athletes()
        name = str(logic.athlete_map(roster).get(athlete_id, {}).get("name", "You"))
        ui.update_selectize(
            "athlete",
            choices={"": ATHLETE_PLACEHOLDER, **logic.athlete_choices(roster)},
            selected=athlete_id,
        )
        show_add.set(False)
        add_msg.set(
            ("success", f"You're on the roster, {name} — and selected below. Log away.")
            if created
            else ("info", f"{name} was already on the roster — selected below.")
        )

    @render.ui
    def add_me() -> TagChild:
        expanded = show_add()
        # Isolated: another athlete signing up mid-keystroke must not re-render
        # this form and wipe what's been typed into it.
        with reactive.isolate():
            roster = data.athletes()
        roster_empty = roster.empty
        expanded = expanded or roster_empty

        if not expanded:
            return ui.tags.div(
                {"class": "add-me-row"},
                ui.tags.span("Not on the list?", class_="add-me-prompt"),
                ui.input_action_button("open_add", "+ Add me", class_="btn-addme"),
            )

        positions = logic.position_choices(roster)
        return ui.tags.div(
            {"class": "add-me-form"},
            ui.tags.div(
                "No one on the roster yet — add yourself"
                if roster_empty
                else "Add yourself to the roster",
                class_="add-me-title",
            ),
            ui.input_text("add_name", "Your name", placeholder="First Last", width="100%"),
            ui.tags.div(
                {"class": "filter-row"},
                ui.input_text("add_grad", "Grad year", placeholder="2027"),
                ui.input_select("add_pos1", "Position", choices=positions),
                ui.input_select("add_pos2", "Second position", choices=positions),
            ),
            ui.tags.div(
                {"class": "btn-row"},
                ui.input_action_button("add_submit", "Add me", class_="btn-admin"),
                None
                if roster_empty
                else ui.input_action_button("cancel_add", "Cancel", class_="btn-admin btn-quiet"),
            ),
        )

    @render.ui
    def add_me_msg() -> TagChild:
        msg = add_msg()
        return None if msg is None else alert(msg[1], tone=msg[0])

    # -- current selections --------------------------------------------------

    @reactive.calc
    def current_metric() -> dict[str, Any] | None:
        return logic.metric_map(data.metrics()).get(input.metric() or "")

    @reactive.calc
    def current_athlete() -> dict[str, Any] | None:
        return logic.athlete_map(data.athletes()).get(input.athlete() or "")

    @reactive.calc
    def reference_data() -> dict[str, Any] | None:
        """Last value + PR for the selected pair, or None until both are chosen."""
        metric, athlete = current_metric(), current_athlete()
        if metric is None or athlete is None:
            return None
        return logic.reference_for(
            data.entries(),
            athlete["athlete_id"],
            metric["metric_id"],
            bool(metric["higher_is_better"]),
        )

    # -- reference tiles -----------------------------------------------------

    @render.ui
    def reference() -> TagChild:
        metric, athlete = current_metric(), current_athlete()
        ref = reference_data()
        if metric is None or athlete is None or ref is None:
            return ui.tags.div(
                {"class": "panel panel-flush"},
                ui.tags.div(
                    {"class": "panel-body"},
                    empty_state(
                        "Pick an athlete and a lift",
                        "Their last result and current PR will show up here.",
                    ),
                ),
            )
        unit = str(metric.get("unit") or "")
        direction = "higher is better" if metric["higher_is_better"] else "lower is better"
        if ref["count"] == 0:
            body = empty_state(
                f"No {metric['name']} on file for {athlete['name']}",
                f"This will be their first entry · {direction}.",
            )
        else:
            body = ui.tags.div(
                {"class": "tile-row"},
                stat_tile(
                    "MOST RECENT",
                    logic.fmt_with_unit(ref["last_value"], unit),
                    logic.fmt_day(ref["last_ts"]),
                ),
                stat_tile(
                    "CURRENT PR",
                    logic.fmt_with_unit(ref["pr_value"], unit),
                    logic.fmt_day(ref["pr_ts"]),
                    tone="tile-pr",
                ),
            )
        return ui.tags.div(
            {"class": "panel panel-flush"},
            ui.tags.div(
                {"class": "panel-head panel-head-tight"},
                ui.tags.h2(f"{athlete['name']} · {metric['name']}"),
                ui.tags.span(direction, class_="panel-note"),
            ),
            ui.tags.div({"class": "panel-body"}, body),
        )

    # -- the number itself ---------------------------------------------------

    @render.ui
    def entry_form() -> TagChild:
        # Re-rendering clears the box: after a save, and whenever the athlete or
        # lift changes, so a leftover number can never be attributed to the
        # wrong person or the wrong lift.
        reset_token()
        input.athlete()
        metric = current_metric()
        unit = str(metric.get("unit") or "") if metric else ""
        label = f"{metric['name']} ({unit})" if metric and unit else "Value"
        return TagList(
            big_number_input("value", label, step=STEP_BY_UNIT.get(unit.lower(), 1.0), unit=unit),
            ui.input_text(
                "notes",
                "Notes (optional)",
                placeholder="reps, RPE, how it felt…",
                width="100%",
            ),
        )

    # -- save ---------------------------------------------------------------

    @reactive.effect
    @reactive.event(input.save)
    def _save() -> None:
        athlete, metric = current_athlete(), current_metric()
        if athlete is None:
            result.set({"status": "error", "message": "Choose an athlete first."})
            return
        if metric is None:
            result.set({"status": "error", "message": "Choose a lift or metric first."})
            return
        raw = input.value()
        if raw is None or (isinstance(raw, float) and pd.isna(raw)):
            result.set({"status": "error", "message": "Enter a number before saving."})
            return
        value = float(raw)
        if value <= 0:
            result.set({"status": "error", "message": "Enter a value greater than zero."})
            return

        try:
            saved = storage.record_entry(
                athlete_id=athlete["athlete_id"],
                metric_id=metric["metric_id"],
                value=value,
                higher_is_better=bool(metric["higher_is_better"]),
                notes=input.notes() or "",
            )
        except storage.StorageError as exc:
            log.warning("Save refused: %s", exc)
            result.set({"status": "error", "message": str(exc)})
            return
        except Exception as exc:  # never let a save crash the kiosk
            log.exception("Unexpected save failure")
            result.set({"status": "error", "message": f"Could not save: {exc}"})
            return

        result.set(
            {
                "status": "pr" if saved["is_pr"] else ("first" if saved["prior_count"] == 0 else "ok"),
                "athlete": athlete,
                "metric": metric,
                "value": value,
                "prior_best": saved["prior_best"],
                "suspicious": saved["suspicious"],
                "timestamp": saved["row"]["timestamp"],
            }
        )
        reset_token.set(reset_token() + 1)

    @render.ui
    def confirmation() -> TagChild:
        state = result()
        if not state:
            return None
        if state["status"] == "error":
            return alert(state["message"], tone="error", title="Not saved")

        metric, athlete = state["metric"], state["athlete"]
        unit = str(metric.get("unit") or "")
        shown = logic.fmt_with_unit(state["value"], unit)
        warning = (
            ui.tags.div(
                "⚠ That's far off their usual numbers — saved anyway, but double-check it.",
                class_="alert-warn-line",
            )
            if state["suspicious"]
            else None
        )

        if state["status"] == "pr":
            gain = state["value"] - state["prior_best"]
            return ui.tags.div(
                {"class": "pr-banner"},
                ui.tags.div("🏆  NEW PR", class_="pr-kicker"),
                ui.tags.div(shown, class_="pr-value"),
                ui.tags.div(
                    f"{athlete['name']} · {metric['name']} · "
                    f"beat {logic.fmt_with_unit(state['prior_best'], unit)} "
                    f"by {logic.fmt_value(abs(gain), unit)} {unit}".strip(),
                    class_="pr-sub",
                ),
                warning,
            )
        if state["status"] == "first":
            return alert(
                f"{shown} logged for {athlete['name']} — first {metric['name']} on record.",
                tone="info",
                title="Saved",
            )
        return alert(
            TagList(
                ui.tags.span(shown, class_="saved-value"),
                f" · {athlete['name']} · {metric['name']}",
                warning,
            ),
            tone="success",
            title="Saved",
        )

    @render.ui
    def recent() -> TagChild:
        metric = current_metric()
        ref = reference_data()
        if metric is None or ref is None or ref["count"] == 0:
            return None
        unit = str(metric.get("unit") or "")
        rows = [
            [
                logic.fmt_datetime(row["ts"]),
                ui.tags.b(logic.fmt_value(row["value"], unit)),
                row["notes"] or "—",
            ]
            for _, row in ref["recent"].iterrows()
        ]
        return ui.tags.div(
            {"class": "panel panel-flush"},
            ui.tags.div(
                {"class": "panel-head panel-head-tight"},
                ui.tags.h2("Last few"),
                ui.tags.span(f"{ref['count']} total", class_="panel-note"),
            ),
            ui.tags.div(
                {"class": "panel-body"},
                data_table(
                    ["When", unit or "Value", "Notes"],
                    rows,
                    align=["left", "right", "left"],
                    max_height="14rem",
                ),
            ),
        )
