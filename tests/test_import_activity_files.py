import gzip
import json
import shutil
import zipfile
from datetime import timedelta
from pathlib import Path

import gpxpy.gpx
import pytest

from trail_trainer.climb_blocks import ClimbBlock
from trail_trainer.fitness import climb_blocks_by_activity
from trail_trainer.import_activity_files import NotLogged, activity_type_of_file, import_activity_files
from trail_trainer.import_strava_export import import_strava_export
from trail_trainer.rebuild_training_log import rebuild_training_log
from trail_trainer.recording import FileDescription, read_recording
from trail_trainer.training_log import ActivityType, Grade, load_activities, set_manual

EXPORT_DIR = Path(__file__).resolve().parent / "data" / "strava_export"
# The export's hike, "weighted carry": a FIT file whose session says "hiking".
HIKE_FIT = EXPORT_DIR / "activities" / "21553533855.fit.gz"
HIKE_STRAVA_ID = "20397657728"
HIKE_FILE_ID = "file-20260930T184820Z"


@pytest.fixture
def paths(tmp_path):
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    return downloads, tmp_path / "training_log.json", tmp_path / "activity_files"


def downloaded_fit(downloads: Path, name: str = "Weighted_carry.fit.gz") -> Path:
    path = downloads / name
    shutil.copy(HIKE_FIT, path)
    return path


def downloaded_gpx(downloads: Path, sport: str | None, name: str | None = "Weighted carry", seconds_later: int = 0) -> Path:
    """The FIT hike written as GPX, as another app might export it."""
    recording = read_recording(HIKE_FIT)
    gpx = gpxpy.gpx.GPX()
    track = gpxpy.gpx.GPXTrack(name=name)
    track.type = sport
    segment = gpxpy.gpx.GPXTrackSegment()
    for i in range(recording.num_samples):
        segment.points.append(
            gpxpy.gpx.GPXTrackPoint(
                float(recording.latitude_deg[i]),
                float(recording.longitude_deg[i]),
                elevation=float(recording.elevation_m[i]),
                time=recording.start + timedelta(seconds=float(recording.time_s[i]) + seconds_later),
            )
        )
    track.segments.append(segment)
    gpx.tracks.append(track)
    path = downloads / "weighted_carry.gpx"
    path.write_text(gpx.to_xml(), encoding="utf-8")
    return path


def imported_export(tmp_path: Path, log_path: Path, files_dir: Path) -> None:
    zip_path = tmp_path / "export.zip"
    with zipfile.ZipFile(zip_path, "w") as export:
        for path in EXPORT_DIR.rglob("*"):
            if path.is_file():
                export.write(path, path.relative_to(EXPORT_DIR).as_posix())
    import_strava_export(zip_path, log_path, files_dir)


def test_a_fit_file_is_imported(paths):
    downloads, log_path, files_dir = paths
    report = import_activity_files([downloaded_fit(downloads)], log_path, files_dir)
    assert report.added == [f"Weighted carry ({HIKE_FILE_ID})"] and report.failed == {}
    assert (files_dir / f"{HIKE_FILE_ID}.fit.gz").exists()

    [hike] = load_activities(log_path)
    assert hike.id == HIKE_FILE_ID
    assert hike.type == ActivityType.HIKE
    # The FIT file's own local time.
    assert str(hike.start_local) == "2026-09-30 12:48:20"
    # Strava: 302 ft over 0.23 mi.
    assert 250 < hike.vert_ft < 400
    assert 0.2 < hike.distance_mi < 0.3
    assert hike.avg_heart_rate_bpm is not None

    entry = json.loads(log_path.read_text())[HIKE_FILE_ID]
    assert entry["file"]["file_name"] == "Weighted_carry.fit.gz"


def test_a_gpx_file_gives_its_track_name_and_sport(paths):
    downloads, log_path, files_dir = paths
    import_activity_files([downloaded_gpx(downloads, sport="trail_running", name="Lunch run")], log_path, files_dir)
    [run] = load_activities(log_path)
    assert run.name == "Lunch run"
    assert run.type == ActivityType.TRAIL_RUN


def test_a_file_that_doesnt_name_its_sport_needs_a_type(paths):
    downloads, log_path, files_dir = paths
    gpx_path = downloaded_gpx(downloads, sport=None)
    report = import_activity_files([gpx_path], log_path, files_dir)
    assert "--type" in report.failed["weighted_carry.gpx"]
    assert load_activities(log_path) == []

    import_activity_files([gpx_path], log_path, files_dir, ActivityType.HIKE)
    assert [a.type for a in load_activities(log_path)] == [ActivityType.HIKE]


def test_a_folder_imports_its_activity_files(paths):
    downloads, log_path, files_dir = paths
    downloaded_fit(downloads)
    (downloads / "notes.txt").write_text("not an activity")
    report = import_activity_files([downloads], log_path, files_dir)
    assert len(report.added) == 1 and report.failed == {}


def test_another_file_of_the_same_activity_updates_it_keeping_edits(paths):
    downloads, log_path, files_dir = paths
    import_activity_files([downloaded_fit(downloads)], log_path, files_dir)
    set_manual(HIKE_FILE_ID, {"grade": "B"}, log_path)

    # The GPX's clock is a couple of seconds off the FIT's, as two devices' would be.
    report = import_activity_files([downloaded_gpx(downloads, "hiking", seconds_later=2)], log_path, files_dir)
    assert report.updated == [f"Weighted carry ({HIKE_FILE_ID})"] and report.added == []
    [hike] = load_activities(log_path)
    assert hike.grade == Grade.B
    assert sorted(p.name for p in files_dir.iterdir()) == [f"{HIKE_FILE_ID}.gpx"]


def test_a_file_of_an_activity_already_from_strava_is_skipped(tmp_path, paths):
    downloads, log_path, files_dir = paths
    imported_export(tmp_path, log_path, files_dir)
    report = import_activity_files([downloaded_fit(downloads)], log_path, files_dir)
    assert report.already_from_strava == {"Weighted_carry.fit.gz": f"weighted carry ({HIKE_STRAVA_ID})"}
    assert HIKE_FILE_ID not in {a.id for a in load_activities(log_path)}


def test_the_same_activity_from_strava_replaces_the_files_entry(tmp_path, paths):
    downloads, log_path, files_dir = paths
    import_activity_files([downloaded_fit(downloads)], log_path, files_dir)
    set_manual(HIKE_FILE_ID, {"grade": "A"}, log_path)

    zip_path = tmp_path / "export.zip"
    with zipfile.ZipFile(zip_path, "w") as export:
        for path in EXPORT_DIR.rglob("*"):
            if path.is_file():
                export.write(path, path.relative_to(EXPORT_DIR).as_posix())
    report = import_strava_export(zip_path, log_path, files_dir)
    assert report.file_entries_replaced == {HIKE_FILE_ID: HIKE_STRAVA_ID}

    entries = json.loads(log_path.read_text())
    assert HIKE_FILE_ID not in entries
    assert not (files_dir / f"{HIKE_FILE_ID}.fit.gz").exists()
    # The edit made on the file's entry wins over the grade in Strava's note.
    assert entries[HIKE_STRAVA_ID]["manual"] == {"grade": "A"}


def test_rebuild_recomputes_file_entries(paths):
    downloads, log_path, files_dir = paths
    import_activity_files([downloaded_fit(downloads)], log_path, files_dir)
    entries = json.loads(log_path.read_text())
    expected = dict(entries[HIKE_FILE_ID]["recorded"])
    entries[HIKE_FILE_ID]["recorded"]["vert_ft"] = -1
    log_path.write_text(json.dumps(entries))

    assert rebuild_training_log(log_path, files_dir) == {}
    assert json.loads(log_path.read_text())[HIKE_FILE_ID]["recorded"] == expected


def test_a_files_climbs_are_analysed_from_all_of_it(paths, tmp_path):
    downloads, log_path, files_dir = paths
    import_activity_files([downloaded_fit(downloads)], log_path, files_dir)
    entries = json.loads(log_path.read_text())
    blocks, unreadable = climb_blocks_by_activity(entries, [HIKE_FILE_ID], files_dir, tmp_path / "climb_blocks.json")
    assert unreadable == {}
    assert len(blocks[HIKE_FILE_ID]) > 0 and all(isinstance(b, ClimbBlock) for b in blocks[HIKE_FILE_ID])


def test_an_unsupported_file_fails(paths):
    downloads, log_path, files_dir = paths
    tcx_path = downloads / "run.tcx"
    tcx_path.write_text("<TrainingCenterDatabase/>")
    report = import_activity_files([tcx_path], log_path, files_dir)
    assert "unsupported file type" in report.failed["run.tcx"]


def test_a_gzipped_gpx_is_read(paths):
    downloads, log_path, files_dir = paths
    gpx_path = downloaded_gpx(downloads, "hiking")
    gz_path = downloads / "weighted_carry.gpx.gz"
    gz_path.write_bytes(gzip.compress(gpx_path.read_bytes()))
    gpx_path.unlink()
    assert len(import_activity_files([gz_path], log_path, files_dir).added) == 1


@pytest.mark.parametrize(
    "sport, sub_sport, activity_type",
    [
        ("hiking", "generic", ActivityType.HIKE),
        ("walking", None, ActivityType.HIKE),
        ("running", "trail", ActivityType.TRAIL_RUN),
        ("trail_running", None, ActivityType.TRAIL_RUN),
        ("Trail Run", None, ActivityType.TRAIL_RUN),
        ("cycling", "mountain", ActivityType.BIKE),
        ("cross_country_skiing", None, ActivityType.SKI),
        ("alpine_skiing", "backcountry", ActivityType.SKI),
        ("training", "cardio_training", ActivityType.WORKOUT),
        ("9", None, None),
        (None, None, None),
    ],
)
def test_activity_type_of_file(sport, sub_sport, activity_type):
    assert activity_type_of_file(FileDescription(name=None, sport=sport, sub_sport=sub_sport)) == activity_type


@pytest.mark.parametrize("sport", ["alpine_skiing", "snowboarding"])
def test_resort_skiing_and_snowboarding_are_not_logged(sport):
    with pytest.raises(NotLogged):
        activity_type_of_file(FileDescription(name=None, sport=sport, sub_sport="generic"))
