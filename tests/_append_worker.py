"""Child process used by the concurrency test: hammer entries.csv with appends.

Usage: python _append_worker.py <data_dir> <athlete_id> <count>
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

data_dir, athlete_id, count = sys.argv[1], sys.argv[2], int(sys.argv[3])
os.environ["NOMAD_WR_DATA"] = data_dir
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nomad_wr import storage  # noqa: E402  (import after the env var is set)

for i in range(count):
    storage.append_entry(
        athlete_id=athlete_id,
        metric_id="back_squat",
        value=200 + i,
        notes=f"set {i}, felt \"fine\"; comma, quote and ; all in one",
    )
