"""App-wide reactive views of the CSV store.

Each frame is wrapped in a `reactive.poll` keyed on the file's mtime + size, so
a save from the kiosk shows up on every connected phone within a second without
anyone hitting refresh — and a coach who edits a CSV by hand gets the same
treatment.

These objects are created once at import (``session=None``) and shared by all
sessions, so 20 phones cost one stat() per file per interval, not 20.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import pandas as pd
from shiny import reactive

from . import storage
from .config import ATHLETES_CSV, BENCHMARKS_CSV, ENTRIES_CSV, METRICS_CSV

POLL_SECONDS = 0.75

# Files must exist before anything tries to stat them.
storage.ensure_data_files()


def _stamp(path: Path) -> Callable[[], tuple[int, int]]:
    """Cheap change-detector: (mtime_ns, size). Missing file reads as (0, 0)."""

    def check() -> tuple[int, int]:
        try:
            st = path.stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return (0, 0)

    return check


@reactive.poll(_stamp(ATHLETES_CSV), POLL_SECONDS, session=None)
def athletes() -> pd.DataFrame:
    return storage.load_athletes()


@reactive.poll(_stamp(METRICS_CSV), POLL_SECONDS, session=None)
def metrics() -> pd.DataFrame:
    return storage.load_metrics()


@reactive.poll(_stamp(ENTRIES_CSV), POLL_SECONDS, session=None)
def entries() -> pd.DataFrame:
    return storage.load_entries()


@reactive.poll(_stamp(BENCHMARKS_CSV), POLL_SECONDS, session=None)
def benchmarks() -> pd.DataFrame:
    return storage.load_benchmarks()
