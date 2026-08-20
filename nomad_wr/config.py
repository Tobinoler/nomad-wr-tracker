"""Paths, branding constants and first-run seed data for Nomad WR Tracker."""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "Nomad WR Tracker"
ORG_NAME = "Nomad Baseball"

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent
WWW_DIR = PROJECT_ROOT / "www"


def _resolve_data_dir() -> Path:
    """Data lives next to the project unless NOMAD_WR_DATA points elsewhere."""
    override = os.environ.get("NOMAD_WR_DATA", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return PROJECT_ROOT / "data"


DATA_DIR = _resolve_data_dir()
ATHLETES_CSV = DATA_DIR / "athletes.csv"
METRICS_CSV = DATA_DIR / "metrics.csv"
ENTRIES_CSV = DATA_DIR / "entries.csv"
BENCHMARKS_CSV = DATA_DIR / "benchmarks.csv"

# One lock guards the whole store. A single lock keeps ordering trivial and the
# critical sections are microseconds long (append one row / rewrite one small
# catalogue), so contention is a non-issue even with a full team logging at once.
LOCK_FILE = DATA_DIR / ".nomad_wr.lock"
LOCK_TIMEOUT = 15.0

ATHLETE_COLUMNS = ["athlete_id", "name", "grad_year", "position", "active", "created_at"]
METRIC_COLUMNS = ["metric_id", "name", "category", "unit", "higher_is_better"]
ENTRY_COLUMNS = ["entry_id", "timestamp", "athlete_id", "metric_id", "value", "notes"]
BENCHMARK_COLUMNS = ["metric_id", "elite", "advanced", "average"]

# Catalogue vocabularies offered in the Admin page (free text is still allowed).
CATEGORIES = ["Strength", "Jump", "Speed", "Power", "Other"]
CATEGORY_ORDER = {name: i for i, name in enumerate(CATEGORIES)}
UNITS = ["lbs", "kg", "in", "cm", "sec", "mph", "watts", "reps", "%"]

POSITIONS = ["RHP", "LHP", "C", "1B", "2B", "3B", "SS", "MIF", "CIF", "INF", "OF", "UTIL", "DH"]

TIERS = ["Elite", "Advanced", "Average", "Below Average"]
TIER_CLASS = {
    "Elite": "tier-elite",
    "Advanced": "tier-advanced",
    "Average": "tier-average",
    "Below Average": "tier-below",
}

# --- First-run seed data -------------------------------------------------
# Written only when the CSV does not exist yet. Edit freely: once the file is
# on disk the app never touches these lists again.

SEED_METRICS = [
    # (metric_id, name, category, unit, higher_is_better)
    ("back_squat", "Back Squat", "Strength", "lbs", True),
    ("front_squat", "Front Squat", "Strength", "lbs", True),
    ("trap_bar_deadlift", "Trap Bar Deadlift", "Strength", "lbs", True),
    ("bench_press", "Bench Press", "Strength", "lbs", True),
    ("weighted_chin_up", "Weighted Chin-Up", "Strength", "lbs", True),
    ("vertical_jump", "Vertical Jump", "Jump", "in", True),
    ("broad_jump", "Broad Jump", "Jump", "in", True),
    ("flying_10_sprint", "Flying 10 Sprint", "Speed", "sec", False),
    ("overhead_med_ball_throw", "Overhead Med Ball Throw", "Power", "mph", True),
    ("body_weight", "Body Weight", "Other", "lbs", True),
]

# Empty on purpose: the roster is built by hand on the Admin page, so a fresh
# install (or a lost athletes.csv) starts blank rather than resurrecting
# example athletes who would then show up on real leaderboards.
SEED_ATHLETES: list[tuple[str, int | None, str]] = []
