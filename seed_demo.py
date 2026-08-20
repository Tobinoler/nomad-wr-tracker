"""Populate the app with believable demo history so it's worth looking at.

    python seed_demo.py                    # ~4 months of entries for the seeded athletes
    python seed_demo.py --with-benchmarks  # also write PLACEHOLDER tier cutoffs
    python seed_demo.py --reset            # wipe entries.csv (and cutoffs) back to headers

Run `--reset` before the first real weight-room session so no fake numbers end
up in a real athlete's history.
"""

from __future__ import annotations

import argparse
import random
from datetime import datetime, timedelta

from nomad_wr import storage
from nomad_wr.config import DATA_DIR

# metric_id -> (starting value, gain per week, session-to-session noise)
PROGRESSIONS = {
    "back_squat": (255, 2.6, 12),
    "front_squat": (195, 1.9, 10),
    "trap_bar_deadlift": (315, 3.1, 15),
    "bench_press": (175, 1.5, 8),
    "weighted_chin_up": (25, 0.7, 5),
    "vertical_jump": (26.5, 0.09, 1.1),
    "broad_jump": (94, 0.22, 3.0),
    "flying_10_sprint": (1.63, -0.0035, 0.03),
    "overhead_med_ball_throw": (52, 0.14, 1.6),
    "body_weight": (178, 0.35, 1.4),
}

# Rough per-athlete strength scaling, so the leaderboard isn't a flat line.
SCALE = {0: 1.12, 1: 1.0, 2: 0.93, 3: 0.86}

NOTES = [
    "", "", "", "3x5", "5x3", "felt easy", "belt on", "RPE 8", "tough set",
    "clean rep", "post-throwing", "AM session", "PR attempt",
]

# Placeholder cutoffs only — replace with Nomad's own standards in Admin.
DEMO_BENCHMARKS = {
    "back_squat": (405, 315, 245),
    "trap_bar_deadlift": (495, 405, 315),
    "vertical_jump": (32, 28, 24),
    "flying_10_sprint": (1.42, 1.52, 1.62),  # lower is better
}

WEEKS = 16
SESSIONS_PER_WEEK = 2


def seed_entries(rng: random.Random) -> int:
    athletes = storage.load_athletes()
    metrics = storage.load_metrics()
    if athletes.empty:
        raise SystemExit("No athletes on file — add some on the Admin page first.")
    if metrics.empty:
        raise SystemExit("No metrics on file — start the app once to seed the catalogue.")

    valid = set(metrics["metric_id"])
    lifts = [m for m in PROGRESSIONS if m in valid and m != "body_weight"]
    start = datetime.now().replace(hour=16, minute=0, second=0, microsecond=0) - timedelta(
        weeks=WEEKS
    )

    written = 0
    for position, (_, athlete) in enumerate(athletes.head(4).iterrows()):
        scale = SCALE.get(position, 1.0)
        for week in range(WEEKS):
            for session in range(SESSIONS_PER_WEEK):
                day = start + timedelta(weeks=week, days=session * 3, minutes=rng.randint(0, 90))
                if day > datetime.now():
                    continue
                todays = rng.sample(lifts, k=rng.randint(2, 4))
                if session == 0 and "body_weight" in valid:
                    todays.append("body_weight")
                for metric_id in todays:
                    base, gain, noise = PROGRESSIONS[metric_id]
                    scaled_base = base * scale if metric_id != "flying_10_sprint" else base / scale
                    value = scaled_base + gain * week + rng.gauss(0, noise)
                    if metric_id == "flying_10_sprint":
                        value = max(1.28, round(value, 2))
                    elif metric_id in ("vertical_jump", "broad_jump", "overhead_med_ball_throw"):
                        value = round(value, 1)
                    else:
                        value = round(value / 5) * 5  # plates come in fives
                    storage.append_entry(
                        athlete_id=athlete["athlete_id"],
                        metric_id=metric_id,
                        value=value,
                        notes=rng.choice(NOTES),
                        timestamp=day.isoformat(timespec="seconds"),
                    )
                    written += 1
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="delete all entries and cutoffs")
    parser.add_argument(
        "--with-benchmarks", action="store_true", help="also write placeholder tier cutoffs"
    )
    args = parser.parse_args()

    storage.ensure_data_files()

    if args.reset:
        removed = len(storage.load_entries())
        storage.reset_entries()
        for metric_id in list(DEMO_BENCHMARKS):
            storage.save_benchmark(metric_id, None, None, None)
        print(f"Cleared {removed} entries and the demo cutoffs in {DATA_DIR}")
        return

    rng = random.Random(7)  # same demo data every time
    written = seed_entries(rng)
    print(f"Wrote {written} demo entries to {DATA_DIR}")

    if args.with_benchmarks:
        for metric_id, (elite, advanced, average) in DEMO_BENCHMARKS.items():
            storage.save_benchmark(metric_id, elite, advanced, average)
        print(
            f"Wrote PLACEHOLDER cutoffs for {len(DEMO_BENCHMARKS)} metrics — "
            "replace them with Nomad's own standards on the Admin page."
        )
    print("Run `python seed_demo.py --reset` before logging real sessions.")


if __name__ == "__main__":
    main()
