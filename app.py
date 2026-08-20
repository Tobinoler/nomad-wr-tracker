"""Nomad WR Tracker — Shiny for Python entry point.

Run it:
    python app.py                                  # binds 0.0.0.0:8000
    shiny run --host 0.0.0.0 --port 8000 app.py    # same thing, with --reload for dev
"""

from __future__ import annotations

import logging
import socket

from shiny import App, ui
from starlette.requests import Request

from nomad_wr import charts, storage
from nomad_wr.config import APP_NAME, ORG_NAME, WWW_DIR
from nomad_wr.pages import admin, athlete_profile, group, leaderboard, quick_entry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("nomad_wr")

storage.ensure_data_files()
charts.ensure_plotly_asset()

FAVICON = (
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E"
    "%3Crect width='32' height='32' rx='3' fill='%230A0A0A'/%3E"
    "%3Ctext x='16' y='23' font-size='19' font-family='Arial,sans-serif' font-weight='bold' "
    "text-anchor='middle' fill='%23FFFFFF'%3EN%3C/text%3E%3C/svg%3E"
)


def _head() -> ui.TagChild:
    return ui.head_content(
        ui.tags.meta(
            name="viewport",
            content="width=device-width, initial-scale=1, viewport-fit=cover",
        ),
        ui.tags.meta(name="theme-color", content="#0A0A0A"),
        ui.tags.meta(name="mobile-web-app-capable", content="yes"),
        ui.tags.link(rel="icon", href=FAVICON),
        ui.tags.link(rel="stylesheet", href="assets/styles.css"),
        # Plotly's own bundle, copied out of the installed package at startup —
        # no CDN, so charts render on a gym network with no internet.
        ui.tags.script(src="assets/plotly.min.js"),
    )


def app_ui(request: Request) -> ui.Tag:
    """Built per page load, so a roster change is live on the next refresh."""
    athletes = storage.load_athletes()
    metrics = storage.load_metrics()
    return ui.page_navbar(
        ui.nav_panel("Quick Entry", quick_entry.quick_entry_ui("quick", athletes, metrics)),
        ui.nav_panel("Athlete", athlete_profile.athlete_profile_ui("profile", athletes)),
        ui.nav_panel("Leaderboard", leaderboard.leaderboard_ui("board", athletes, metrics)),
        ui.nav_panel("Group", group.group_ui("group", athletes, metrics)),
        ui.nav_panel("Admin", admin.admin_ui("admin", athletes, metrics)),
        _head(),
        title=ui.tags.span(
            ui.tags.span("NOMAD", class_="brand-mark"),
            ui.tags.span("WR", class_="brand-accent"),
            class_="brand",
        ),
        id="nav",
        window_title=f"{APP_NAME} · {ORG_NAME}",
        fillable=False,
    )


def server(input, output, session) -> None:
    quick_entry.quick_entry_server("quick")
    athlete_profile.athlete_profile_server("profile")
    leaderboard.leaderboard_server("board")
    group.group_server("group")
    admin.admin_server("admin")


app = App(app_ui, server, static_assets={"/assets": WWW_DIR})


def lan_ip() -> str:
    """Best guess at this machine's address on the gym Wi-Fi."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))  # no packets sent; just picks the route
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


if __name__ == "__main__":
    import shiny

    PORT = 8000
    log.info("%s starting", APP_NAME)
    log.info("  This machine : http://localhost:%d", PORT)
    log.info("  Phones/tablets on the same Wi-Fi: http://%s:%d", lan_ip(), PORT)
    shiny.run_app(app, host="0.0.0.0", port=PORT, launch_browser=False)
