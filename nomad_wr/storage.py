"""Every read and write of the flat-file store lives here.

Design rules that the rest of the app relies on:

* **Reads never raise.** A truncated or malformed row is skipped and logged, so
  one bad line in ``entries.csv`` can never take the weight-room kiosk down.
* **Entries are append-only.** Recording a lift appends exactly one row; the
  file is never rewritten to add data, so a crash mid-write can at worst lose
  the row being written, never the 4,000 rows already on disk.
* **Every write happens under a cross-process file lock** (``filelock``), so two
  phones hitting Save in the same millisecond queue up instead of interleaving.
* **Catalogue writes are atomic.** ``athletes.csv`` / ``metrics.csv`` /
  ``benchmarks.csv`` are re-read inside the lock, edited, written to a temp file
  in the same directory, ``fsync``-ed, then ``os.replace``-d over the original.
  A reader either sees the whole old file or the whole new one.
"""

from __future__ import annotations

import csv
import logging
import math
import os
import re
import tempfile
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

import pandas as pd
from filelock import FileLock, Timeout

from .config import (
    ATHLETE_COLUMNS,
    ATHLETES_CSV,
    BENCHMARK_COLUMNS,
    BENCHMARKS_CSV,
    DATA_DIR,
    ENTRIES_CSV,
    ENTRY_COLUMNS,
    LOCK_FILE,
    LOCK_TIMEOUT,
    METRIC_COLUMNS,
    METRICS_CSV,
    SEED_ATHLETES,
    SEED_METRICS,
)

log = logging.getLogger("nomad_wr.storage")

MAX_NOTE_CHARS = 300


class StorageError(RuntimeError):
    """Raised for write failures the UI should surface to the user."""


# --------------------------------------------------------------------------
# Locking
# --------------------------------------------------------------------------

_lock_obj: FileLock | None = None
_lock_init = threading.Lock()


def data_lock() -> FileLock:
    """The single process-wide lock instance guarding the data directory.

    Reusing one ``FileLock`` object (rather than constructing a new one per
    call) keeps it re-entrant within a thread, so nested helpers can't deadlock
    against themselves while still blocking other threads and other processes.
    """
    global _lock_obj
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if _lock_obj is None:
        with _lock_init:
            if _lock_obj is None:
                _lock_obj = FileLock(str(LOCK_FILE), timeout=LOCK_TIMEOUT)
    return _lock_obj


def _busy(exc: Timeout) -> StorageError:
    return StorageError(
        "The data files are busy right now — another device is mid-save. "
        "Wait a moment and try again."
    )


# --------------------------------------------------------------------------
# Small value helpers
# --------------------------------------------------------------------------


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def now_iso() -> str:
    """ISO 8601, local wall-clock, second precision (e.g. 2026-08-19T15:04:22)."""
    return datetime.now().isoformat(timespec="seconds")


def slugify(text: str, fallback: str = "metric") -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", str(text).strip().lower()).strip("_")
    return slug or fallback


def fmt_number(value: float | int | None) -> str:
    """Human-friendly number for CSV storage: 315 not 315.0, 1.52 kept as is."""
    if value is None:
        return ""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(f):
        return ""
    if f == int(f) and abs(f) < 1e15:
        return str(int(f))
    return f"{f:.4f}".rstrip("0").rstrip(".")


_TRUE = {"true", "t", "yes", "y", "1"}
_FALSE = {"false", "f", "no", "n", "0"}


def to_bool(value: Any, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    return default


def clean_name(value: Any) -> str:
    """Collapse whitespace to a single space.

    None and NaN become "" rather than the string "None", so they fail the
    empty-name check instead of putting an athlete called None on the roster.
    """
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return " ".join(str(value).split())


def clean_note(note: Any) -> str:
    text = "" if note is None else str(note)
    text = " ".join(text.split())  # collapse newlines/tabs so a row stays a row
    return text[:MAX_NOTE_CHARS]


# --------------------------------------------------------------------------
# Low-level file IO (callers must already hold the lock for the write helpers)
# --------------------------------------------------------------------------


def _empty_frame(columns: Sequence[str]) -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype="object") for c in columns})


def _read_raw(path: Path, columns: Sequence[str]) -> pd.DataFrame:
    """Read a CSV as plain strings, tolerating anything short of an unreadable file."""
    try:
        if not path.exists() or path.stat().st_size == 0:
            return _empty_frame(columns)
        df = pd.read_csv(
            path,
            dtype=str,
            keep_default_na=False,
            na_values=[],
            on_bad_lines="skip",  # a half-written row is dropped, not fatal
            engine="python",
        )
    except Exception:  # unreadable file, wrong encoding, locked by Excel, ...
        log.exception("Could not read %s — treating it as empty", path.name)
        return _empty_frame(columns)

    df.columns = [str(c).strip() for c in df.columns]
    missing = [c for c in columns if c not in df.columns]
    if missing:
        log.warning("%s is missing column(s) %s — filling blanks", path.name, missing)
        for col in missing:
            df[col] = ""
    extra = [c for c in df.columns if c not in columns]
    if extra:
        log.info("%s has extra column(s) %s — ignored", path.name, extra)
    return df[list(columns)].map(lambda v: "" if v is None else str(v).strip())


def _write_rows_atomic(path: Path, columns: Sequence[str], rows: Iterable[Sequence[Any]]) -> None:
    """Replace `path` with `columns` + `rows` via temp file + atomic rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh, lineterminator="\n")
            writer.writerow(columns)
            writer.writerows(rows)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)  # atomic on Windows and POSIX alike
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _ensure_header(path: Path, columns: Sequence[str]) -> None:
    if not path.exists() or path.stat().st_size == 0:
        _write_rows_atomic(path, columns, [])


def _append_row(path: Path, columns: Sequence[str], values: Sequence[Any]) -> None:
    """Append exactly one row. Caller holds the lock."""
    _ensure_header(path, columns)
    needs_newline = False
    size = path.stat().st_size
    if size:
        with open(path, "rb") as fh:  # guard against a previously truncated write
            fh.seek(-1, os.SEEK_END)
            needs_newline = fh.read(1) not in (b"\n", b"\r")
    with open(path, "a", newline="", encoding="utf-8") as fh:
        if needs_newline:
            fh.write("\n")
        csv.writer(fh, lineterminator="\n").writerow(values)
        fh.flush()
        os.fsync(fh.fileno())


# --------------------------------------------------------------------------
# Startup / seeding
# --------------------------------------------------------------------------


def ensure_data_files() -> None:
    """Create /data and seed any CSV that does not exist yet. Idempotent."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with data_lock():
            if not METRICS_CSV.exists():
                log.info("Seeding %s", METRICS_CSV)
                _write_rows_atomic(
                    METRICS_CSV,
                    METRIC_COLUMNS,
                    [
                        (mid, name, cat, unit, "true" if hib else "false")
                        for mid, name, cat, unit, hib in SEED_METRICS
                    ],
                )
            if not ATHLETES_CSV.exists():
                log.info("Seeding %s", ATHLETES_CSV)
                stamp = now_iso()
                _write_rows_atomic(
                    ATHLETES_CSV,
                    ATHLETE_COLUMNS,
                    [
                        (new_id("ath"), name, grad, pos, "true", stamp)
                        for name, grad, pos in SEED_ATHLETES
                    ],
                )
            # Entries start empty: this is the real log, never fabricated.
            _ensure_header(ENTRIES_CSV, ENTRY_COLUMNS)
            # Benchmarks start empty too: without real cutoffs the app shows
            # plain rankings rather than invented tiers.
            _ensure_header(BENCHMARKS_CSV, BENCHMARK_COLUMNS)
    except Timeout as exc:
        raise _busy(exc) from exc


# --------------------------------------------------------------------------
# Loaders — each returns a clean, typed DataFrame and never raises
# --------------------------------------------------------------------------


def load_athletes() -> pd.DataFrame:
    """Columns: athlete_id, name, grad_year (Int64), position, active (bool), created_at."""
    df = _read_raw(ATHLETES_CSV, ATHLETE_COLUMNS)
    if df.empty:
        out = _empty_frame(ATHLETE_COLUMNS)
        out["grad_year"] = pd.Series(dtype="Int64")
        out["active"] = pd.Series(dtype="bool")
        return out

    bad = (df["athlete_id"] == "") | (df["name"] == "")
    if bad.any():
        log.warning("Skipped %d athlete row(s) with no id or name", int(bad.sum()))
    df = df[~bad].copy()

    df["grad_year"] = pd.to_numeric(df["grad_year"], errors="coerce").astype("Int64")
    df["active"] = df["active"].map(lambda v: to_bool(v, default=True))
    dupes = df["athlete_id"].duplicated(keep="last")
    if dupes.any():
        log.warning("Dropped %d duplicate athlete_id row(s)", int(dupes.sum()))
    df = df[~dupes]
    return df.sort_values("name", key=lambda s: s.str.lower()).reset_index(drop=True)


def load_metrics() -> pd.DataFrame:
    """Columns: metric_id, name, category, unit, higher_is_better (bool)."""
    df = _read_raw(METRICS_CSV, METRIC_COLUMNS)
    if df.empty:
        out = _empty_frame(METRIC_COLUMNS)
        out["higher_is_better"] = pd.Series(dtype="bool")
        return out

    bad = (df["metric_id"] == "") | (df["name"] == "")
    if bad.any():
        log.warning("Skipped %d metric row(s) with no id or name", int(bad.sum()))
    df = df[~bad].copy()

    unparsed = ~df["higher_is_better"].str.strip().str.lower().isin(_TRUE | _FALSE)
    if unparsed.any():
        log.warning(
            "metrics.csv: %d row(s) have an unreadable higher_is_better value; "
            "defaulting to true. Fix this in Admin — it drives PR and tier logic.",
            int(unparsed.sum()),
        )
    df["higher_is_better"] = df["higher_is_better"].map(lambda v: to_bool(v, default=True))
    df["category"] = df["category"].replace("", "Other")
    dupes = df["metric_id"].duplicated(keep="last")
    if dupes.any():
        log.warning("Dropped %d duplicate metric_id row(s)", int(dupes.sum()))
    return df[~dupes].reset_index(drop=True)


def _parse_timestamps(series: pd.Series) -> pd.Series:
    """Parse ISO timestamps leniently; unreadable ones become NaT, never an error."""
    for kwargs in ({"format": "mixed"}, {}, {"format": "mixed", "utc": True}, {"utc": True}):
        try:
            parsed = pd.to_datetime(series, errors="coerce", **kwargs)
        except Exception:
            continue
        if getattr(parsed.dtype, "tz", None) is not None:
            parsed = parsed.dt.tz_localize(None)
        return parsed
    return pd.Series(pd.NaT, index=series.index)


def load_entries() -> pd.DataFrame:
    """Columns: entry_id, timestamp, athlete_id, metric_id, value (float), notes, ts (datetime)."""
    df = _read_raw(ENTRIES_CSV, ENTRY_COLUMNS)
    if df.empty:
        out = _empty_frame(ENTRY_COLUMNS)
        out["value"] = pd.Series(dtype="float64")
        out["ts"] = pd.Series(dtype="datetime64[ns]")
        return out

    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    bad = df["value"].isna() | (df["athlete_id"] == "") | (df["metric_id"] == "")
    if bad.any():
        log.warning("Skipped %d entry row(s) with no value / athlete / metric", int(bad.sum()))
    df = df[~bad].copy()

    df["ts"] = _parse_timestamps(df["timestamp"])
    undated = df["ts"].isna()
    if undated.any():
        # Keep the number — losing a logged lift is worse than a missing date.
        log.warning("%d entry row(s) have an unreadable timestamp", int(undated.sum()))
    return df.sort_values("ts", na_position="first").reset_index(drop=True)


def load_benchmarks() -> pd.DataFrame:
    """Columns: metric_id, elite, advanced, average (all float, may be NaN)."""
    df = _read_raw(BENCHMARKS_CSV, BENCHMARK_COLUMNS)
    if df.empty:
        out = _empty_frame(BENCHMARK_COLUMNS)
        for col in ("elite", "advanced", "average"):
            out[col] = pd.Series(dtype="float64")
        return out

    for col in ("elite", "advanced", "average"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    keep = (df["metric_id"] != "") & df[["elite", "advanced", "average"]].notna().any(axis=1)
    return df[keep].drop_duplicates("metric_id", keep="last").reset_index(drop=True)


# --------------------------------------------------------------------------
# Writers
# --------------------------------------------------------------------------


def append_entry(
    athlete_id: str,
    metric_id: str,
    value: float,
    notes: str = "",
    timestamp: str | None = None,
) -> dict[str, str]:
    """Append one entry row. Returns the row as written."""
    if not athlete_id or not metric_id:
        raise StorageError("Pick an athlete and a metric before saving.")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise StorageError("That value isn't a number.") from exc
    if not math.isfinite(numeric):
        raise StorageError("That value isn't a number.")

    row = {
        "entry_id": new_id("ent"),
        "timestamp": timestamp or now_iso(),
        "athlete_id": str(athlete_id),
        "metric_id": str(metric_id),
        "value": fmt_number(numeric),
        "notes": clean_note(notes),
    }
    try:
        with data_lock():
            _append_row(ENTRIES_CSV, ENTRY_COLUMNS, [row[c] for c in ENTRY_COLUMNS])
    except Timeout as exc:
        raise _busy(exc) from exc
    except OSError as exc:
        raise StorageError(f"Could not write the entry: {exc}") from exc
    log.info("Entry %s: athlete=%s metric=%s value=%s", row["entry_id"], athlete_id, metric_id, row["value"])
    return row


def reset_entries() -> int:
    """Delete every entry, leaving just the header. Returns rows removed.

    Destructive and deliberately not reachable from the UI — it exists for
    `seed_demo.py --reset`, to clear demo data before real logging starts.
    """
    try:
        with data_lock():
            removed = len(load_entries())
            _write_rows_atomic(ENTRIES_CSV, ENTRY_COLUMNS, [])
            log.warning("entries.csv reset — %d rows removed", removed)
            return removed
    except Timeout as exc:
        raise _busy(exc) from exc


def _looks_off(value: float, prior_best: float | None) -> bool:
    """Flag a probable typo (a 3350 lb squat) without ever refusing the entry."""
    if prior_best is None or prior_best <= 0:
        return False
    ratio = value / prior_best
    return ratio > 3 or ratio < 1 / 3


def record_entry(
    athlete_id: str,
    metric_id: str,
    value: float,
    higher_is_better: bool,
    notes: str = "",
) -> dict[str, Any]:
    """Append one entry and report how it compares to what was already on file.

    The history read and the append happen inside a single lock acquisition, so
    the "is this a PR?" verdict cannot be raced by a save from another device.
    """
    from . import logic  # imported here purely to keep the dependency one-way

    numeric = float(value)
    try:
        with data_lock():
            existing = load_entries()
            mine = existing[
                (existing["athlete_id"] == athlete_id) & (existing["metric_id"] == metric_id)
            ]
            prior_best = None if mine.empty else logic.best_value(mine["value"], higher_is_better)
            row = append_entry(athlete_id, metric_id, numeric, notes)
    except Timeout as exc:
        raise _busy(exc) from exc

    return {
        "row": row,
        "prior_best": prior_best,
        "prior_count": int(len(mine)),
        "is_pr": logic.is_better(numeric, prior_best, higher_is_better),
        "suspicious": _looks_off(numeric, prior_best),
    }


def _athlete_rows(df: pd.DataFrame) -> list[list[str]]:
    return [
        [
            r["athlete_id"],
            r["name"],
            "" if pd.isna(r["grad_year"]) else str(int(r["grad_year"])),
            r["position"],
            "true" if r["active"] else "false",
            r["created_at"] or now_iso(),
        ]
        for _, r in df.iterrows()
    ]


def _metric_rows(df: pd.DataFrame) -> list[list[str]]:
    return [
        [
            r["metric_id"],
            r["name"],
            r["category"] or "Other",
            r["unit"],
            "true" if r["higher_is_better"] else "false",
        ]
        for _, r in df.iterrows()
    ]


def _benchmark_rows(df: pd.DataFrame) -> list[list[str]]:
    return [
        [r["metric_id"], fmt_number(r["elite"]), fmt_number(r["advanced"]), fmt_number(r["average"])]
        for _, r in df.iterrows()
    ]


def save_athlete(
    name: str,
    grad_year: int | None,
    position: str,
    active: bool,
    athlete_id: str | None = None,
) -> str:
    """Create or update one athlete. Returns the athlete_id."""
    name = clean_name(name)
    if not name:
        raise StorageError("An athlete needs a name.")
    try:
        with data_lock():
            df = load_athletes()  # re-read inside the lock: no lost updates
            aid = athlete_id or new_id("ath")
            record = {
                "athlete_id": aid,
                "name": name,
                "grad_year": pd.NA if grad_year in (None, "") else int(grad_year),
                "position": str(position or "").strip(),
                "active": bool(active),
                "created_at": now_iso(),
            }
            if athlete_id and (df["athlete_id"] == athlete_id).any():
                idx = df.index[df["athlete_id"] == athlete_id][0]
                record["created_at"] = df.at[idx, "created_at"] or record["created_at"]
                for key, val in record.items():
                    df.at[idx, key] = val
            else:
                df = pd.concat([df, pd.DataFrame([record])], ignore_index=True)
            _write_rows_atomic(ATHLETES_CSV, ATHLETE_COLUMNS, _athlete_rows(df))
            return aid
    except Timeout as exc:
        raise _busy(exc) from exc
    except OSError as exc:
        raise StorageError(f"Could not save the athlete: {exc}") from exc


def add_athlete_if_new(
    name: str,
    grad_year: int | None = None,
    position: str = "",
) -> tuple[str, bool]:
    """Self-signup: create an athlete unless that name is already on the roster.

    Returns ``(athlete_id, created)`` — when ``created`` is False the id belongs
    to the athlete who was already there, so the caller can just select them.

    The name check and the write share one lock acquisition. Two athletes on two
    phones tapping "Add me" in the same second therefore can't both create a row,
    and an athlete who taps twice gets themselves back rather than a twin.
    """
    name = clean_name(name)
    if not name:
        raise StorageError("Enter a name first.")
    if len(name) > 80:
        raise StorageError("That name is too long.")

    try:
        with data_lock():
            roster = load_athletes()
            if not roster.empty:
                existing = roster[roster["name"].str.casefold() == name.casefold()]
                if not existing.empty:
                    return str(existing.iloc[0]["athlete_id"]), False
            return save_athlete(name, grad_year, position, True), True
    except Timeout as exc:
        raise _busy(exc) from exc


def set_athlete_active(athlete_id: str, active: bool) -> None:
    try:
        with data_lock():
            df = load_athletes()
            if not (df["athlete_id"] == athlete_id).any():
                raise StorageError("That athlete is no longer in the roster file.")
            df.loc[df["athlete_id"] == athlete_id, "active"] = bool(active)
            _write_rows_atomic(ATHLETES_CSV, ATHLETE_COLUMNS, _athlete_rows(df))
    except Timeout as exc:
        raise _busy(exc) from exc


def save_metric(
    name: str,
    category: str,
    unit: str,
    higher_is_better: bool,
    metric_id: str | None = None,
) -> str:
    """Create or update one metric. Returns the metric_id."""
    name = clean_name(name)
    if not name:
        raise StorageError("A metric needs a name.")
    try:
        with data_lock():
            df = load_metrics()
            if metric_id and (df["metric_id"] == metric_id).any():
                idx = df.index[df["metric_id"] == metric_id][0]
                df.at[idx, "name"] = name
                df.at[idx, "category"] = category or "Other"
                df.at[idx, "unit"] = unit or ""
                df.at[idx, "higher_is_better"] = bool(higher_is_better)
                mid = metric_id
            else:
                mid = _unique_metric_id(slugify(name), set(df["metric_id"]))
                df = pd.concat(
                    [
                        df,
                        pd.DataFrame(
                            [
                                {
                                    "metric_id": mid,
                                    "name": name,
                                    "category": category or "Other",
                                    "unit": unit or "",
                                    "higher_is_better": bool(higher_is_better),
                                }
                            ]
                        ),
                    ],
                    ignore_index=True,
                )
            _write_rows_atomic(METRICS_CSV, METRIC_COLUMNS, _metric_rows(df))
            return mid
    except Timeout as exc:
        raise _busy(exc) from exc
    except OSError as exc:
        raise StorageError(f"Could not save the metric: {exc}") from exc


def _unique_metric_id(base: str, taken: set[str]) -> str:
    if base not in taken:
        return base
    for n in range(2, 100):
        candidate = f"{base}_{n}"
        if candidate not in taken:
            return candidate
    return new_id("metric")


def save_benchmark(
    metric_id: str,
    elite: float | None,
    advanced: float | None,
    average: float | None,
) -> None:
    """Set (or clear, when all three are None) the tier cutoffs for one metric."""
    if not metric_id:
        raise StorageError("Pick a metric first.")
    try:
        with data_lock():
            df = load_benchmarks()
            df = df[df["metric_id"] != metric_id]
            if any(v is not None and str(v) != "" for v in (elite, advanced, average)):
                df = pd.concat(
                    [
                        df,
                        pd.DataFrame(
                            [
                                {
                                    "metric_id": metric_id,
                                    "elite": _opt_float(elite),
                                    "advanced": _opt_float(advanced),
                                    "average": _opt_float(average),
                                }
                            ]
                        ),
                    ],
                    ignore_index=True,
                )
            _write_rows_atomic(BENCHMARKS_CSV, BENCHMARK_COLUMNS, _benchmark_rows(df))
    except Timeout as exc:
        raise _busy(exc) from exc
    except OSError as exc:
        raise StorageError(f"Could not save the benchmark: {exc}") from exc


def _opt_float(value: Any) -> float:
    if value is None or value == "":
        return float("nan")
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")
