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
from nomad_wr.logic import join_position, split_position  # noqa: E402

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


def test_removing_a_metric_keeps_the_entries_and_reattaches_them() -> None:
    athlete_id = _an_athlete()
    metric_id = storage.save_metric("Sled Push", "Speed", "sec", False)
    storage.append_entry(athlete_id, metric_id, 6.4)
    storage.append_entry(athlete_id, metric_id, 6.1)
    storage.save_benchmark(metric_id, 5.5, 6.0, 6.5)
    entries_before = len(storage.load_entries())

    assert storage.count_entries_for_metric(metric_id) == 2
    removed = storage.delete_metric(metric_id)
    assert removed == {"name": "Sled Push", "orphaned": 2, "had_benchmark": True}

    assert metric_id not in set(storage.load_metrics()["metric_id"]), "metric should be gone"
    assert metric_id not in logic.benchmark_map(storage.load_benchmarks()), "cutoffs go with it"
    # The append-only log is untouched: nothing an athlete recorded is destroyed.
    assert len(storage.load_entries()) == entries_before
    assert storage.count_entries_for_metric(metric_id) == 2

    # Re-adding under the same name regenerates the same slug, so the orphaned
    # history reattaches rather than being stranded forever.
    readded = storage.save_metric("Sled Push", "Speed", "sec", False)
    assert readded == metric_id
    summary = logic.athlete_metric_summary(
        storage.load_entries(), storage.load_metrics(), athlete_id
    )
    row = summary[summary["metric_id"] == metric_id].iloc[0]
    assert bool(row["known_metric"]) is True and int(row["entries"]) == 2
    assert row["pr_value"] == 6.1, "lower-is-better PR survived the round trip"


def test_removing_an_unused_metric_is_clean() -> None:
    metric_id = storage.save_metric("Sled Drag", "Speed", "sec", False)
    assert storage.count_entries_for_metric(metric_id) == 0
    removed = storage.delete_metric(metric_id)
    assert removed == {"name": "Sled Drag", "orphaned": 0, "had_benchmark": False}
    assert metric_id not in set(storage.load_metrics()["metric_id"])

    for bad in ("", "no_such_metric"):
        try:
            storage.delete_metric(bad)
        except storage.StorageError:
            pass
        else:
            raise AssertionError(f"delete_metric({bad!r}) should have been refused")


def test_orphaned_entries_stay_visible_on_the_profile() -> None:
    athlete_id = _an_athlete()
    metric_id = storage.save_metric("Ghost Lift", "Other", "lbs", True)
    storage.append_entry(athlete_id, metric_id, 100)
    storage.delete_metric(metric_id)

    summary = logic.athlete_metric_summary(
        storage.load_entries(), storage.load_metrics(), athlete_id
    )
    row = summary[summary["metric_id"] == metric_id].iloc[0]
    assert bool(row["known_metric"]) is False
    assert metric_id in row["metric_name"], "the coach should still see which metric it was"
    assert int(row["entries"]) == 1


def test_position_pairs_round_trip() -> None:
    assert join_position("RHP", "OF") == "RHP / OF"
    assert join_position("SS", "") == "SS"
    assert join_position("", "") == ""
    assert split_position("RHP / OF") == ("RHP", "OF")
    assert split_position("SS") == ("SS", "")
    assert split_position("") == ("", "")
    # A hand-typed three-way splits once, so nothing is dropped on a re-save.
    assert split_position("RHP / 1B / OF") == ("RHP", "1B / OF")
    for original in ("RHP / OF", "SS", "", "CIF / 3B"):
        assert join_position(*split_position(original)) == original


def test_self_signup_never_creates_a_twin() -> None:
    first_id, created = storage.add_athlete_if_new("Linkyn Fuller", 2027, "RHP / OF")
    assert created is True

    # The same athlete tapping "Add me" again gets themselves back, not a twin.
    again_id, created_again = storage.add_athlete_if_new("Linkyn Fuller", 2027, "RHP / OF")
    assert (again_id, created_again) == (first_id, False)

    # Match on the name as typed, whatever the case or the spacing.
    sloppy_id, sloppy_created = storage.add_athlete_if_new("  linkyn   FULLER ")
    assert (sloppy_id, sloppy_created) == (first_id, False)

    roster = storage.load_athletes()
    assert len(roster[roster["name"] == "Linkyn Fuller"]) == 1
    saved = roster[roster["athlete_id"] == first_id].iloc[0]
    assert saved["position"] == "RHP / OF" and bool(saved["active"]) is True

    # A different athlete with a different name still gets in.
    other_id, other_created = storage.add_athlete_if_new("Blake Lyle", 2029, "LHP")
    assert other_created is True and other_id != first_id

    # None must not become a row named "None" — str(None) is a truthy string.
    savers = (
        ("add_athlete_if_new", lambda v: storage.add_athlete_if_new(v)),
        ("save_athlete", lambda v: storage.save_athlete(v, None, "", True)),
        ("save_metric", lambda v: storage.save_metric(v, "Strength", "lbs", True)),
    )
    for blank in ("", "   ", None, float("nan")):
        for label, save in savers:
            try:
                save(blank)
            except storage.StorageError:
                pass
            else:
                raise AssertionError(f"{label} accepted a blank name ({blank!r})")


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
