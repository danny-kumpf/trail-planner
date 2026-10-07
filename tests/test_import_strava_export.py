import json
import zipfile
from pathlib import Path

import pytest

from trail_planner.import_strava_export import import_strava_export
from trail_planner.rebuild_training_log import rebuild_training_log
from trail_planner.training_log import ActivityType, Grade, grade_in_note, load_activities, set_manual

EXPORT_DIR = Path(__file__).resolve().parent / "data" / "strava_export"
HIKE_ID = "20397657728"  # "weighted carry": FIT with GPS, cadence, and "Grade: D" in its note
WORKOUT_ID = "19746459323"  # "Stairwell weighted carry": FIT without GPS or elevation
NO_FILE_ID = "14456102085"  # "First paced mile!": no recording file


@pytest.fixture
def workspace(tmp_path):
    """A zip like Strava's export, holding three activities, and where to import it."""
    zip_path = tmp_path / "export.zip"
    with zipfile.ZipFile(zip_path, "w") as export:
        for path in EXPORT_DIR.rglob("*"):
            if path.is_file():
                export.write(path, path.relative_to(EXPORT_DIR).as_posix())
    return zip_path, tmp_path / "training_log.json", tmp_path / "activity_files"


def test_imports_every_activity(workspace):
    zip_path, log_path, files_dir = workspace
    report = import_strava_export(zip_path, log_path, files_dir)
    assert sorted(report.imported) == sorted([HIKE_ID, WORKOUT_ID, NO_FILE_ID])
    assert report.failed == {}
    assert sorted(p.name for p in files_dir.iterdir()) == [f"{WORKOUT_ID}.fit.gz", f"{HIKE_ID}.fit.gz"]

    by_id = {a.id: a for a in load_activities(log_path)}
    hike = by_id[HIKE_ID]
    assert hike.type == ActivityType.HIKE
    assert str(hike.start_local) == "2026-09-30 12:48:20"
    # Strava: 302 ft over 0.23 mi.
    assert 250 < hike.vert_ft < 400
    assert 0.2 < hike.distance_mi < 0.3

    workout = by_id[WORKOUT_ID]
    assert workout.type == ActivityType.WORKOUT
    assert workout.vert_ft == 0
    assert workout.moving_time_s == 2048
    assert workout.avg_heart_rate_bpm is not None

    mile = by_id[NO_FILE_ID]
    assert mile.type == ActivityType.TRAIL_RUN
    assert mile.distance_mi == pytest.approx(1.0)
    assert mile.running_mi == pytest.approx(1.0)


def test_grade_in_private_note_is_copied_once(workspace):
    zip_path, log_path, files_dir = workspace
    report = import_strava_export(zip_path, log_path, files_dir)
    assert list(report.grades_copied.values()) == ["D"]
    assert {a.id: a.grade for a in load_activities(log_path)}[HIKE_ID] == Grade.D

    # Cleared on the page, it stays cleared when the export is imported again.
    set_manual(HIKE_ID, {}, log_path)
    import_strava_export(zip_path, log_path, files_dir)
    assert {a.id: a.grade for a in load_activities(log_path)}[HIKE_ID] is None


def test_reimporting_keeps_hand_edits(workspace):
    zip_path, log_path, files_dir = workspace
    import_strava_export(zip_path, log_path, files_dir)
    set_manual(WORKOUT_ID, {"vert_ft": 1200, "type": "hike"}, log_path)
    import_strava_export(zip_path, log_path, files_dir)
    workout = {a.id: a for a in load_activities(log_path)}[WORKOUT_ID]
    assert workout.vert_ft == 1200
    assert workout.type == ActivityType.HIKE


def test_test_entries_are_replaced_keeping_their_edits(workspace):
    zip_path, log_path, files_dir = workspace
    test_entry = {
        "recorded": {"name": "weighted carry", "type": "hike", "start_local": "2026-09-30T12:48:20",
                     "moving_time_s": 1, "distance_mi": 1, "vert_ft": 1, "running_mi": 0},
        "manual": {"grade": "A"},
    }
    log_path.write_text(json.dumps({"gpx:green_from_gregory": test_entry}))
    report = import_strava_export(zip_path, log_path, files_dir)
    assert report.test_entries_replaced == {"gpx:green_from_gregory": HIKE_ID}

    entries = json.loads(log_path.read_text())
    assert "gpx:green_from_gregory" not in entries
    # The edit made on the test entry wins over the grade in Strava's note.
    assert entries[HIKE_ID]["manual"] == {"grade": "A"}


def test_rebuild_recomputes_from_stored_files(workspace):
    zip_path, log_path, files_dir = workspace
    import_strava_export(zip_path, log_path, files_dir)
    expected = {activity_id: entry["recorded"] for activity_id, entry in json.loads(log_path.read_text()).items()}
    entries = json.loads(log_path.read_text())
    for entry in entries.values():
        entry["recorded"]["vert_ft"] = -1
    log_path.write_text(json.dumps(entries))

    assert rebuild_training_log(log_path, files_dir) == {}
    rebuilt = json.loads(log_path.read_text())
    assert {activity_id: entry["recorded"] for activity_id, entry in rebuilt.items()} == expected


@pytest.mark.parametrize(
    "note, grade",
    [
        ("Grade: A", Grade.A),
        ("Grade: b\nfelt slow", Grade.B),
        ("Tough one.\n\nGrade: F", Grade.F),
        ("Grade: E", None),
        ("great grade on the climb", None),
        (None, None),
    ],
)
def test_grade_in_note(note, grade):
    assert grade_in_note(note) == grade


def test_resort_snowboarding_is_left_out(workspace, tmp_path):
    zip_path, log_path, files_dir = workspace
    import_strava_export(zip_path, log_path, files_dir)
    assert (files_dir / f"{WORKOUT_ID}.fit.gz").exists()

    # A newer export, where the workout was changed to snowboarding on Strava.
    csv_text = (EXPORT_DIR / "activities.csv").read_text(encoding="utf-8")
    newer_zip_path = tmp_path / "newer_export.zip"
    with zipfile.ZipFile(zip_path) as export, zipfile.ZipFile(newer_zip_path, "w") as newer:
        for name in [n for n in export.namelist() if n != "activities.csv"]:
            newer.writestr(name, export.read(name))
        newer.writestr("activities.csv", csv_text.replace("Stairwell weighted carry,Workout", "Stairwell weighted carry,Snowboard"))
    report = import_strava_export(newer_zip_path, log_path, files_dir)
    assert report.left_out == [f"Stairwell weighted carry ({WORKOUT_ID})"]
    assert WORKOUT_ID not in {a.id for a in load_activities(log_path)}
    assert not (files_dir / f"{WORKOUT_ID}.fit.gz").exists()
