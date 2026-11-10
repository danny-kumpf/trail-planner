"""Import activity files (FIT or GPX, optionally gzipped) into the training log, from any source:
a watch or phone app's download, or Strava's export of a single activity.

Usage: uv run python -m trail_trainer.import_activity_files [--type TYPE] PATH [PATH ...]

Each PATH is a file, or a folder whose activity files are all imported. Each file's recording is
copied into activity_files/, and its entry in training_log.json gets a `file` block:
- `name`: the GPX track's name, or else the file's name.
- `type`: --type if given, else what the file says its sport is. A file that doesn't say needs
  --type.
- `start_utc`: when the recording starts.
- `file_name`: the name of the file it was imported from.

An activity is identified by when it started, so importing a file again, or another file of the
same activity (e.g. its FIT after its GPX), updates its entry and keeps its edits. A file of an
activity already in the log from Strava is skipped. An activity later imported or synced from
Strava replaces the file's entry, taking its edits.
"""

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from trail_trainer.activity_stats import recorded_from_file
from trail_trainer.json_store import write_entries
from trail_trainer.recording import FileDescription, file_format, read_description, read_recording
from trail_trainer.training_log import (
    DEFAULT_ACTIVITY_FILES_DIR,
    DEFAULT_LOG_PATH,
    ActivityType,
    Source,
    new_file_entry,
    read_log,
    same_activity_ids,
    source_of,
)

FILE_ENTRY_ID_PREFIX = "file-"

# Sports as activity files name them, lowercased with spaces, underscores and hyphens removed:
# FIT's sports (e.g. "cross_country_skiing") and the words GPX tracks use (e.g. Strava's
# "trail_running"). A sport not listed is a Workout, as for Strava's types.
ACTIVITY_TYPES_BY_FILE_SPORT = {
    "hiking": ActivityType.HIKE,
    "hike": ActivityType.HIKE,
    "walking": ActivityType.HIKE,
    "walk": ActivityType.HIKE,
    "snowshoeing": ActivityType.HIKE,
    "snowshoe": ActivityType.HIKE,
    "mountaineering": ActivityType.HIKE,
    "running": ActivityType.TRAIL_RUN,
    "run": ActivityType.TRAIL_RUN,
    "trailrunning": ActivityType.TRAIL_RUN,
    "trailrun": ActivityType.TRAIL_RUN,
    "cycling": ActivityType.BIKE,
    "biking": ActivityType.BIKE,
    "ride": ActivityType.BIKE,
    "mountainbiking": ActivityType.BIKE,
    "ebiking": ActivityType.BIKE,
    "crosscountryskiing": ActivityType.SKI,
    "nordicskiing": ActivityType.SKI,
    "nordicski": ActivityType.SKI,
    "backcountryskiing": ActivityType.SKI,
    "backcountryski": ActivityType.SKI,
}
# Resort skiing and snowboarding, which aren't training and are left out of the log.
UNLOGGED_FILE_SPORTS = {"alpineskiing", "alpineski", "snowboarding", "snowboard"}
# FIT records backcountry skiing as alpine skiing with this sub-sport.
BACKCOUNTRY_SUB_SPORT = "backcountry"


class NotLogged(Exception):
    """The activity is of a sport the training log doesn't keep."""


def normalized_sport(sport: str) -> str:
    return sport.lower().replace(" ", "").replace("_", "").replace("-", "")


def activity_type_of_file(description: FileDescription) -> ActivityType | None:
    """The log's type for the sport the file names; None if it doesn't name one we can read."""
    if description.sport is None or description.sport.isdigit():
        # Some GPX files give the sport as a number from an app's own list.
        return None
    sport = normalized_sport(description.sport)
    if sport in UNLOGGED_FILE_SPORTS:
        if description.sub_sport is not None and normalized_sport(description.sub_sport) == BACKCOUNTRY_SUB_SPORT:
            return ActivityType.SKI
        raise NotLogged(f"{description.sport} activities aren't kept in the training log")
    return ACTIVITY_TYPES_BY_FILE_SPORT.get(sport, ActivityType.WORKOUT)


def name_from_file_name(file_name: str) -> str:
    """E.g. "Green_from_NCAR.fit.gz" -> "Green from NCAR"."""
    return file_name.split(".", 1)[0].replace("_", " ")


def file_entry_id(start: datetime) -> str:
    return f"{FILE_ENTRY_ID_PREFIX}{start.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}"


def stored_file_name(activity_id: str, file_name: str) -> str:
    """The recording's name in activity_files/: the entry's id with the file's extensions, e.g.
    "file-20261004T212555Z.fit.gz"."""
    return f"{activity_id}.{file_name.split('.', 1)[1].lower()}"


def activity_files_in(paths: list[Path]) -> list[Path]:
    """The files named, and the activity files in the folders named."""

    def is_activity_file(path: Path) -> bool:
        try:
            file_format(path.name)
        except ValueError:
            return False
        return path.is_file()

    files: list[Path] = []
    for path in paths:
        files.extend(sorted(filter(is_activity_file, path.iterdir())) if path.is_dir() else [path])
    return files


@dataclass
class FileImportReport:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    # File name -> the entry from Strava that already records its activity.
    already_from_strava: dict[str, str] = field(default_factory=dict)
    # Files of a sport the log doesn't keep, e.g. resort snowboarding.
    left_out: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)


def import_activity_file(
    path: Path,
    entries: dict[str, dict],
    activity_files_dir: Path,
    activity_type: ActivityType | None,
    report: FileImportReport,
) -> None:
    """Add the file's activity to `entries`, or update its entry; `activity_type` overrides what
    the file says its sport is."""
    recording = read_recording(path)
    description = read_description(path)
    activity_type = activity_type or activity_type_of_file(description)
    if activity_type is None:
        raise ValueError("the file doesn't say what sport it is; import it with --type")
    start = recording.start.astimezone(timezone.utc)

    same_activity = same_activity_ids(entries, start)
    from_strava = [i for i in same_activity if source_of(entries[i]) == Source.STRAVA]
    if from_strava:
        report.already_from_strava[path.name] = f"{entries[from_strava[0]]['recorded']['name']} ({from_strava[0]})"
        return
    activity_id = same_activity[0] if same_activity else file_entry_id(start)

    file_block = {
        "name": description.name or name_from_file_name(path.name),
        "type": activity_type.value,
        "start_utc": start.isoformat(),
        "file_name": path.name,
    }
    recorded = recorded_from_file(file_block, recording)
    recording_file = stored_file_name(activity_id, path.name)
    (activity_files_dir / recording_file).write_bytes(path.read_bytes())

    label = f"{file_block['name']} ({activity_id})"
    if activity_id in entries:
        previous_file = entries[activity_id]["recording_file"]
        if previous_file != recording_file:
            (activity_files_dir / previous_file).unlink(missing_ok=True)
        entries[activity_id].update(file=file_block, recording_file=recording_file, recorded=recorded)
        report.updated.append(label)
    else:
        entries[activity_id] = new_file_entry(file_block, recording_file, recorded)
        report.added.append(label)


def import_activity_files(
    paths: list[Path], log_path: Path, activity_files_dir: Path, activity_type: ActivityType | None = None
) -> FileImportReport:
    report = FileImportReport()
    entries = read_log(log_path)
    activity_files_dir.mkdir(parents=True, exist_ok=True)
    for path in activity_files_in(paths):
        try:
            import_activity_file(path, entries, activity_files_dir, activity_type, report)
        except NotLogged:
            report.left_out.append(path.name)
        except (ValueError, OSError) as e:
            report.failed[path.name] = str(e)
    write_entries(log_path, entries)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="uv run python -m trail_trainer.import_activity_files",
        description="Import FIT or GPX activity files (optionally gzipped) into the training log.",
    )
    parser.add_argument("paths", nargs="+", type=Path, metavar="PATH", help="an activity file, or a folder of them")
    parser.add_argument(
        "--type",
        choices=[t.value for t in ActivityType],
        help="the activities' type, instead of the sport each file names",
    )
    args = parser.parse_args()
    activity_type = ActivityType(args.type) if args.type else None
    report = import_activity_files(args.paths, DEFAULT_LOG_PATH, DEFAULT_ACTIVITY_FILES_DIR, activity_type)

    print(f"Added {len(report.added)} activities and updated {len(report.updated)}.")
    for label in report.added:
        print(f"Added {label}")
    for label in report.updated:
        print(f"Updated {label}")
    for file_name, strava_label in report.already_from_strava.items():
        print(f"Skipped {file_name}: already in the log from Strava as {strava_label}")
    if report.left_out:
        print(f"Left out as resort skiing or snowboarding: {', '.join(report.left_out)}")
    for file_name, error in report.failed.items():
        print(f"Failed {file_name}: {error}")


if __name__ == "__main__":
    main()
