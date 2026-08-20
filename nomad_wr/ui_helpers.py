"""Small presentational building blocks shared by the four pages."""

from __future__ import annotations

from typing import Iterable, Sequence

from htmltools import Tag, TagChild
from shiny import ui

from .config import TIER_CLASS


def page_heading(title: str, subtitle: str | None = None) -> Tag:
    return ui.tags.div(
        {"class": "page-heading"},
        ui.tags.h1(title),
        ui.tags.p(subtitle, class_="page-sub") if subtitle else None,
    )


def stat_tile(label: str, value: TagChild, sub: TagChild | None = None, tone: str = "") -> Tag:
    return ui.tags.div(
        {"class": f"stat-tile {tone}".strip()},
        ui.tags.div(label, class_="stat-label"),
        ui.tags.div(value, class_="stat-value"),
        ui.tags.div(sub, class_="stat-sub") if sub is not None else None,
    )


def tier_badge(tier: str | None) -> TagChild:
    if not tier:
        return ""
    return ui.tags.span(tier, class_=f"badge-tier {TIER_CLASS.get(tier, '')}")


def pill(text: str, tone: str = "") -> Tag:
    return ui.tags.span(text, class_=f"pill {tone}".strip())


def empty_state(title: str, message: str = "") -> Tag:
    return ui.tags.div(
        {"class": "empty-state"},
        ui.tags.div(title, class_="empty-title"),
        ui.tags.div(message, class_="empty-msg") if message else None,
    )


def alert(message: TagChild, tone: str = "info", title: str | None = None) -> Tag:
    return ui.tags.div(
        {"class": f"nomad-alert tone-{tone}"},
        ui.tags.div(title, class_="alert-title") if title else None,
        ui.tags.div(message, class_="alert-body"),
    )


def data_table(
    headers: Sequence[str],
    rows: Iterable[Sequence[TagChild]],
    row_classes: Iterable[str] | None = None,
    align: Sequence[str] | None = None,
    scroll: bool = True,
    max_height: str = "22rem",
    empty_message: str = "Nothing logged yet.",
) -> Tag:
    """A compact, mobile-friendly HTML table. Cell content is escaped by htmltools."""
    body_rows = list(rows)
    classes = list(row_classes) if row_classes is not None else ["" for _ in body_rows]
    aligns = list(align) if align else ["left"] * len(headers)

    if not body_rows:
        return empty_state(empty_message)

    table = ui.tags.table(
        {"class": "nomad-table"},
        ui.tags.thead(
            ui.tags.tr(
                *[
                    ui.tags.th(h, class_=f"ta-{aligns[i] if i < len(aligns) else 'left'}")
                    for i, h in enumerate(headers)
                ]
            )
        ),
        ui.tags.tbody(
            *[
                ui.tags.tr(
                    {"class": cls},
                    *[
                        ui.tags.td(cell, class_=f"ta-{aligns[i] if i < len(aligns) else 'left'}")
                        for i, cell in enumerate(row)
                    ],
                )
                for row, cls in zip(body_rows, classes)
            ]
        ),
    )
    if not scroll:
        return table
    return ui.tags.div({"class": "table-scroll", "style": f"max-height:{max_height}"}, table)


def chart_block(html: str, fallback: str = "Not enough dated entries to chart yet.") -> TagChild:
    if not html:
        return ui.tags.div(fallback, class_="chart-fallback")
    return ui.tags.div({"class": "chart-block"}, ui.HTML(html))


def field(label: str, control: TagChild, hint: str | None = None) -> Tag:
    return ui.tags.div(
        {"class": "field"},
        ui.tags.label(label, class_="field-label"),
        control,
        ui.tags.div(hint, class_="field-hint") if hint else None,
    )


def section(title: str, *children: TagChild, actions: TagChild | None = None) -> Tag:
    return ui.tags.section(
        {"class": "panel"},
        ui.tags.div(
            {"class": "panel-head"},
            ui.tags.h2(title),
            ui.tags.div(actions, class_="panel-actions") if actions is not None else None,
        ),
        ui.tags.div({"class": "panel-body"}, *children),
    )


def tile_row(*tiles: TagChild) -> Tag:
    return ui.tags.div({"class": "tile-row"}, *tiles)


def legend(items: Iterable[tuple[str, str]]) -> Tag:
    return ui.tags.div(
        {"class": "tier-legend"},
        *[
            ui.tags.span(
                ui.tags.i(style=f"background:{color}"),
                label,
                class_="tier-legend-item",
            )
            for label, color in items
        ],
    )


def big_number_input(id_: str, label: str, step: float = 1.0, unit: str = "") -> Tag:
    """Numeric input tuned for touch — `type=number` gives phones a numeric keypad.

    Sizing lives in styles.css (`.big-input input`) rather than in tag surgery so
    this keeps working if Shiny changes the markup it emits.
    """
    return ui.tags.div(
        {"class": "big-input"},
        ui.tags.label(label, class_="field-label", **{"for": id_}),
        ui.tags.div(
            {"class": "big-input-wrap"},
            ui.input_numeric(id_, None, value=None, step=step, width="100%"),
            ui.tags.span(unit, class_="big-input-unit") if unit else None,
        ),
    )
