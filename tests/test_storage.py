"""Persistence and rules tests. Run directly (`python tests/test_storage.py`) or with pytest.

Everything runs against a throwaway data directory, never the real /data.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP_DATA = Path(tempfile.mkdtemp(prefix="nomad_wr_test_"))
os.environ["NOMAD_WR_DATA"] = str(TMP_DATA)
sys.path.insert(0, str(ROOT))

from nomad_wr import logic, storage  # noqa: E402
from nomad_wr.config import ATHLETES_CSV, BENCHMARKS_CSV, ENTRIES_CSV, METRICS_CSV  # noqa: E402
from nomad_wr.pages.admin import _join_position, _split_position  # noqa: E402

storage.ensure_data_files()


def _an_athlete() -> str:
    """An athlete_id to hang entries off — the roster ships empty, so make one."""
    roster = storage.load_athletes()
    if roster.empty:
        return storage.save_athlete("Test Athlete One", 2028, "RHP / OF", True)
    return str(roster.iloc[0]["athlete_id"])


def test_seeding_creates_all_four_files() -> None:
    storage.ensure_data_files()
    for path in (ATHLETES_CSV, METRICS_CSV, ENTRIES_CSV, BENCHMARKS_CSV):
        assert path.exists(), f"{path.name} was not created"

    metrics = storage.load_metrics()
    assert len(metrics) >= 10, "expected seeded metrics"
    assert storage.load_athletes().empty, "roster must start empty — never invent athletes"
    assert storage.load_entries().empty, "entries must start empty — the log is never faked"
    assert storage.load_benchmarks().empty, "benchmarks must start empty — never invent cutoffs"

    sprint = metrics[metrics["metric_id"] == "flying_10_sprint"].iloc[0]
    assert not sprint["higher_is_better"], "a timed sprint must be lower-is-better"
    squat = metrics[metrics["metric_id"] == "back_squat"].iloc[0]
    assert bool(squat["higher_is_better"]) is True


def test_seeding_is_idempotent() -> None:
    before = ATHLETES_CSV.read_text(encoding="utf-8")
    storage.ensure_data_files()
    assert ATHLETES_CSV.read_text(encoding="utf-8") == before, "re-seeding overwrote a catalogue"


def test_append_roundtrip_survives_nasty_notes() -> None:
    athlete_id = _an_athlete()
    note = 'felt easy, 3x5 @ "RPE 7"\nsecond line\twith tab'
    row = storage.append_entry(athlete_id, "back_squat", 315, note)

    entries = storage.load_entries()
    saved = entries[entries["entry_id"] == row["entry_id"]].iloc[0]
    assert float(saved["value"]) == 315.0
    assert saved["athlete_id"] == athlete_id
    assert "\n" not in saved["notes"] and "RPE 7" in saved["notes"]
    assert len(ENTRIES_CSV.read_text(encoding="utf-8").splitlines()) == 2  # header + 1


def test_values_keep_precision_and_shed_noise() -> None:
    athlete_id = _an_athlete()
    storage.append_entry(athlete_id, "flying_10_sprint", 1.53)
    storage.append_entry(athlete_id, "vertical_jump", 32.0)
    entries = storage.load_entries()
    assert float(entries[entries["metric_id"] == "flying_10_sprint"].iloc[0]["value"]) == 1.53
    assert entries[entries["metric_id"] == "vertical_jump"].iloc[0]["value"] == 32.0
    assert ",32," in ENTRIES_CSV.read_text(encoding="utf-8"), "32.0 should be stored as 32"


def test_malformed_rows_are_skipped_not_fatal() -> None:
    good_before = len(storage.load_entries())
    with open(ENTRIES_CSV, "a", encoding="utf-8", newline="") as fh:
        fh.write("this,row,is,broken\n")  # too few columns
        fh.write("ent_x,2026-01-02T10:00:00,ath_1,back_squat,not-a-number,bad value\n")
        fh.write("ent_y,not-a-date,ath_1,back_squat,405,unreadable timestamp\n")

    entries = storage.load_entries()  # must not raise
    assert len(entries) == good_before + 1, "only the unparseable-value rows should drop"
    kept = entries[entries["entry_id"] == "ent_y"].iloc[0]
    assert kept["value"] == 405.0, "a bad timestamp must never cost us the number"

    # A file left without its final newline (power cut mid-write) must not
    # swallow the next append by gluing two rows together.
    with open(ENTRIES_CSV, "a", encoding="utf-8", newline="") as fh:
        fh.write("ent_z,2026-01-03T10:00:00,ath_1,back_squat,410,no trailing newline")
    athlete_id = _an_athlete()
    storage.append_entry(athlete_id, "back_squat", 320)
    text = ENTRIES_CSV.read_text(encoding="utf-8")
    assert "no trailing newline\n" in text, "the appender must close the dangling line first"
    assert len(storage.load_entries()) == good_before + 3


def test_catalogue_edits_are_atomic_and_readable() -> None:
    athlete_id = storage.save_athlete("Test Athlete", 2029, "1B / OF", True)
    roster = storage.load_athletes()
    saved = roster[roster["athlete_id"] == athlete_id].iloc[0]
    assert saved["name"] == "Test Athlete" and int(saved["grad_year"]) == 2029
    assert saved["position"] == "1B / OF", "combined positions must survive the write"

    storage.save_athlete("Test Athlete", 2029, "1B / OF", False, athlete_id=athlete_id)
    roster = storage.load_athletes()
    assert not bool(roster[roster["athlete_id"] == athlete_id].iloc[0]["active"])
    assert len(roster[roster["athlete_id"] == athlete_id]) == 1, "edit must not duplicate the row"

    metric_id = storage.save_metric("Split Squat ISO", "Strength", "sec", False)
    assert metric_id == "split_squat_iso"
    metrics = storage.load_metrics()
    assert not bool(metrics[metrics["metric_id"] == metric_id].iloc[0]["higher_is_better"])

    # No temp files left behind by the rename dance.
    assert not list(TMP_DATA.glob("*.tmp")), "atomic write leaked a temp file"


def test_benchmarks_round_trip_and_clear() -> None:
    storage.save_benchmark("back_squat", 405, 315, 225)
    cuts = logic.benchmark_map(storage.load_benchmarks())["back_squat"]
    assert cuts == {"elite": 405.0, "advanced": 315.0, "average": 225.0}

    storage.save_benchmark("vertical_jump", 32, None, 24)  # partial tiers are allowed
    partial = logic.benchmark_map(storage.load_benchmarks())["vertical_jump"]
    assert "advanced" not in partial

    storage.save_benchmark("back_squat", None, None, None)
    assert "back_squat" not in logic.benchmark_map(storage.load_benchmarks())


def test_position_pairs_round_trip() -> None:
    assert _join_position("RHP", "OF") == "RHP / OF"
    assert _join_position("SS", "") == "SS"
    assert _join_position("", "") == ""
    assert _split_position("RHP / OF") == ("RHP", "OF")
    assert _split_position("SS") == ("SS", "")
    assert _split_position("") == ("", "")
    # A hand-typed three-way splits once, so nothing is dropped on a re-save.
    assert _split_position("RHP / 1B / OF") == ("RHP", "1B / OF")
    for original in ("RHP / OF", "SS", "", "CIF / 3B"):
        assert _join_position(*_split_position(original)) == original


def test_pr_direction_respects_higher_is_better() -> None:
    assert logic.is_better(320, 315, True) is True
    assert logic.is_better(315, 315, True) is False, "a tie is not a PR"
    assert logic.is_better(310, 315, True) is False
    assert logic.is_better(1.49, 1.53, False) is True, "faster sprint is a PR"
    assert logic.is_better(1.60, 1.53, False) is False
    assert logic.is_better(225, None, True) is False, "first entry has nothing to beat"


def test_record_entry_reports_pr_and_typos() -> None:
    athlete_id = storage.save_athlete("PR Tester", 2028, "OF", True)
    first = storage.record_entry(athlete_id, "bench_press", 185, True)
    assert first["is_pr"] is False and first["prior_count"] == 0

    second = storage.record_entry(athlete_id, "bench_press", 205, True)
    assert second["is_pr"] is True and second["prior_best"] == 185.0

    third = storage.record_entry(athlete_id, "bench_press", 195, True)
    assert third["is_pr"] is False and third["prior_best"] == 205.0

    fat_finger = storage.record_entry(athlete_id, "bench_press", 2050, True)
    assert fat_finger["suspicious"] is True, "10x the PR should be flagged"
    assert fat_finger["is_pr"] is True, "...but still saved and still a PR"

    sprint_1 = storage.record_entry(athlete_id, "flying_10_sprint", 1.55, False)
    sprint_2 = storage.record_entry(athlete_id, "flying_10_sprint", 1.49, False)
    assert sprint_1["is_pr"] is False and sprint_2["is_pr"] is True


def test_tier_logic_both_directions() -> None:
    high = {"elite": 405.0, "advanced": 315.0, "average": 225.0}
    assert logic.tier_of(420, high, True) == "Elite"
    assert logic.tier_of(405, high, True) == "Elite", "cutoffs are inclusive"
    assert logic.tier_of(330, high, True) == "Advanced"
    assert logic.tier_of(230, high, True) == "Average"
    assert logic.tier_of(185, high, True) == "Below Average"

    low = {"elite": 1.45, "advanced": 1.55, "average": 1.65}
    assert logic.tier_of(1.42, low, False) == "Elite"
    assert logic.tier_of(1.50, low, False) == "Advanced"
    assert logic.tier_of(1.80, low, False) == "Below Average"

    assert logic.tier_of(300, None, True) is None, "no cutoffs means no invented tier"
    assert logic.tier_of(300, {}, True) is None
    assert logic.cutoffs_are_ordered(high, True) is True
    assert logic.cutoffs_are_ordered(high, False) is False, "wrong direction should warn"
    assert logic.cutoffs_are_ordered(low, False) is True


def test_leaderboard_orders_by_direction() -> None:
    entries = storage.load_entries()
    athletes = storage.load_athletes()
    metrics = storage.load_metrics()

    squat = dict(metrics[metrics["metric_id"] == "back_squat"].iloc[0])
    board = logic.leaderboard(entries, athletes, squat)
    if not board.empty:
        assert list(board["best"]) == sorted(board["best"], reverse=True)

    sprint = dict(metrics[metrics["metric_id"] == "flying_10_sprint"].iloc[0])
    sprint_board = logic.leaderboard(entries, athletes, sprint)
    if not sprint_board.empty:
        assert list(sprint_board["best"]) == sorted(sprint_board["best"])


def test_concurrent_appends_from_four_processes_lose_nothing() -> None:
    """The one that matters: two phones hitting Save at the same instant."""
    workers, per_worker = 4, 30
    before = len(storage.load_entries())
    # Raw line count too: the earlier test deliberately left junk lines behind,
    # so only the delta is meaningful.
    raw_before = len(ENTRIES_CSV.read_text(encoding="utf-8").splitlines())

    procs = [
        subprocess.Popen(
            [sys.executable, str(Path(__file__).parent / "_append_worker.py"),
             str(TMP_DATA), f"ath_conc_{i}", str(per_worker)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for i in range(workers)
    ]
    for proc in procs:
        _, err = proc.communicate(timeout=180)
        assert proc.returncode == 0, f"worker failed: {err.decode(errors='replace')[-800:]}"

    entries = storage.load_entries()
    expected = before + workers * per_worker
    assert len(entries) == expected, f"expected {expected} rows, found {len(entries)}"
    assert entries["entry_id"].nunique() == len(entries), "duplicate entry ids"

    raw_after = len(ENTRIES_CSV.read_text(encoding="utf-8").splitlines())
    assert raw_after - raw_before == workers * per_worker, (
        "lines on disk disagree with the appends made — a write interleaved"
    )
    for athlete in (f"ath_conc_{i}" for i in range(workers)):
        assert len(entries[entries["athlete_id"] == athlete]) == per_worker


def _run_all() -> int:
    # Definition order, the same order pytest uses — several tests build on the
    # state the previous one left on disk.
    tests = sorted(
        ((name, fn) for name, fn in globals().items() if name.startswith("test_")),
        key=lambda pair: pair[1].__code__.co_firstlineno,
    )
    failures = 0
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"  FAIL  {name}\n        {type(exc).__name__}: {exc}")
        else:
            print(f"  ok    {name}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed   (data dir: {TMP_DATA})")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
