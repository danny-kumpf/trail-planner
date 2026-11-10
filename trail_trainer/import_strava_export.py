"""Import a Strava account export ("Download or delete your account") into the training log.

Usage: uv run python -m trail_trainer.import_strava_export recorded_activities/export_<id>.zip

Each activity's recording is copied into activity_files/, and its entry in training_log.json gets
Strava's summary (`strava`) and our numbers (`recorded`). Hand edits (`manual`) are kept, so the
import can be run again, e.g. with a newer export. Resort skiing and snowboarding are left out.

Two things happen only when an activity first enters the log:
- A "Grade: X" line in its Strava private note becomes its grade.
- If it was already in the log from an imported activity file, that entry's edits move to it and
  that entry is removed.
"""

import csv
import io
import sys
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from trail_trainer.activity_stats import is_logged, recorded_from_strava
from trail_trainer.json_store import write_entries
from trail_trainer.recording import read_recording
from trail_trainer.training_log import (
    DEFAULT_ACTIVITY_FILES_DIR,
    DEFAULT_LOG_PATH,
    new_strava_entry,
    read_log,
    remove_entry,
    replace_file_entries,
)

# How activities.csv writes "Activity Date", which is in UTC.
EXPORT_DATE_FORMAT = "%b %d, %Y, %I:%M:%S %p"


@dataclass
class ExportImportReport:
    imported: list[str] = field(default_factory=list)
    # Activities of a type the log doesn't keep, e.g. resort snowboarding; removed if already in it.
    left_out: list[str] = field(default_factory=list)
    # Activities with no recording file, imported from Strava's summary alone.
    without_recording: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    grades_copied: dict[str, str] = field(default_factory=dict)
    # Entry id of an imported activity file -> the Strava activity id that replaced it.
    file_entries_replaced: dict[str, str] = field(default_factory=dict)


def read_activity_rows(export: zipfile.ZipFile) -> list[dict[str, str]]:
    """activities.csv as one dict per row.

    Strava repeats some columns, first in display units (e.g. Distance in km) and later in SI units
    (Distance in m); keying by name keeps the last of each, the SI one.
    """
    reader = csv.reader(io.TextIOWrapper(export.open("activities.csv"), encoding="utf-8"))
    header = next(reader)
    return [dict(zip(header, row + [""] * (len(header) - len(row)))) for row in reader]


def optional_float(text: str) -> float | None:
    return float(text) if text.strip() else None


def strava_summary(row: dict[str, str]) -> dict:
    start_utc = datetime.strptime(row["Activity Date"], EXPORT_DATE_FORMAT).replace(tzinfo=timezone.utc)
    return {
        "name": row["Activity Name"],
        "type": row["Activity Type"],
        "start_utc": start_utc.isoformat(),
        # The export doesn't give local times; a sync fills this in from Strava's API.
        "start_local": None,
        "moving_time_s": optional_float(row["Moving Time"]),
        "elapsed_time_s": optional_float(row["Elapsed Time"]),
        "distance_m": optional_float(row["Distance"]),
        "elevation_gain_m": optional_float(row["Elevation Gain"]),
        "avg_heart_rate_bpm": optional_float(row["Average Heart Rate"]),
        "max_heart_rate_bpm": optional_float(row["Max Heart Rate"]),
        # Says whether a run was a race. The export doesn't give it; a sync fills it in.
        "workout_type": None,
        "private_note": row["Activity Private Note"] or None,
        "description": row["Activity Description"] or None,
    }


def recording_file_name(activity_id: str, export_file_name: str) -> str:
    """The activity's file name in activity_files/: its id with the export file's extensions,
    e.g. "12345.fit.gz"."""
    extensions = Path(export_file_name).name.split(".", 1)[1]
    return f"{activity_id}.{extensions}"


def import_strava_export(zip_path: Path, log_path: Path, activity_files_dir: Path) -> ExportImportReport:
    report = ExportImportReport()
    entries = read_log(log_path)
    activity_files_dir.mkdir(parents=True, exist_ok=True)
    new_ids: list[str] = []

    with zipfile.ZipFile(zip_path) as export:
        for row in read_activity_rows(export):
            activity_id = row["Activity ID"]
            label = f"{row['Activity Name']} ({activity_id})"
            strava = strava_summary(row)
            if not is_logged(strava["type"]):
                report.left_out.append(label)
                if activity_id in entries:
                    remove_entry(entries, activity_id, activity_files_dir)
                continue
            file_name = recording_file_name(activity_id, row["Filename"]) if row["Filename"] else None
            try:
                if file_name is not None:
                    (activity_files_dir / file_name).write_bytes(export.read(row["Filename"]))
                    recorded = recorded_from_strava(strava, read_recording(activity_files_dir / file_name))
                else:
                    recorded = recorded_from_strava(strava, None)
                    report.without_recording.append(label)
            except (ValueError, KeyError) as e:
                report.failed[label] = str(e)
                continue

            report.imported.append(activity_id)
            if activity_id in entries:
                entries[activity_id].update(strava=strava, recording_file=file_name, recorded=recorded)
            else:
                entries[activity_id] = new_strava_entry(strava, file_name, recorded)
                new_ids.append(activity_id)
                if "grade" in entries[activity_id]["manual"]:
                    report.grades_copied[label] = entries[activity_id]["manual"]["grade"]

    report.file_entries_replaced = replace_file_entries(entries, new_ids, activity_files_dir)
    write_entries(log_path, entries)
    return report


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    report = import_strava_export(Path(sys.argv[1]), DEFAULT_LOG_PATH, DEFAULT_ACTIVITY_FILES_DIR)
    print(f"Imported {len(report.imported)} activities.")
    if report.left_out:
        print(f"Left out as resort skiing or snowboarding: {', '.join(report.left_out)}")
    if report.without_recording:
        print(f"Without a recording, so from Strava's summary: {', '.join(report.without_recording)}")
    for label, grade in report.grades_copied.items():
        print(f"Grade {grade} from the private note of {label}")
    for file_id, activity_id in report.file_entries_replaced.items():
        print(f"Replaced {file_id}, imported from a file, with the same activity from Strava ({activity_id})")
    for label, error in report.failed.items():
        print(f"Failed {label}: {error}")


if __name__ == "__main__":
    main()
