"""The training log: recorded activities, each with optional hand edits, in training_log.json.

Each entry is keyed by Strava activity id and has these blocks:
- `strava`: Strava's summary of the activity, rewritten by imports and syncs.
- `recording_file`: the activity's recording in activity_files/, or null if it has none.
- `recorded`: what we compute from those two (see activity_stats), rewritten with them.
- `manual`: hand edits, written when edits are saved from the page. It holds the grade and
  overrides of the overridable fields. A field's value is the manual one if set, else the
  recorded one. It also holds `model_choice`, set on the Fitness tab, which puts the activity in
  the climbing model or keeps it out.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from trail_trainer.json_store import read_entries, write_entries
from trail_trainer.trails import PROJECT_ROOT

DEFAULT_LOG_PATH = PROJECT_ROOT / "training_log.json"
DEFAULT_ACTIVITY_FILES_DIR = PROJECT_ROOT / "activity_files"


class ActivityType(StrEnum):
    HIKE = "hike"
    TRAIL_RUN = "trail_run"
    BIKE = "bike"
    RACE = "race"
    SKI = "ski"
    WORKOUT = "workout"

    @property
    def is_on_foot(self) -> bool:
        """Whether part of it may have been run, as opposed to ridden, skied or lifted."""
        return self in (ActivityType.HIKE, ActivityType.TRAIL_RUN, ActivityType.RACE)

    @property
    def covers_distance(self) -> bool:
        """Whether it goes somewhere, so its recording is a track over terrain, as opposed to e.g.
        yoga or climbing, which only take time."""
        return self != ActivityType.WORKOUT


class Grade(StrEnum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    F = "F"


class ModelChoice(StrEnum):
    """Whether an activity is put in the climbing model whatever its date, or kept out of it."""

    REQUIRE = "require"
    EXCLUDE = "exclude"


NOTE_GRADE_PATTERN =re.compile(r"^\s*Grade:\s*([A-Za-z])\b", re.MULTILINE)

# The recorded numbers a hand edit may replace, e.g. vert for a treadmill session.
OVERRIDABLE_NUMBER_FIELDS = ("moving_time_s", "distance_mi", "vert_ft", "running_mi")
# The type can be replaced too, e.g. to mark a run as a race.
OVERRIDABLE_FIELDS = ("type", *OVERRIDABLE_NUMBER_FIELDS)


@dataclass(frozen=True)
class Activity:
    id: str
    name: str
    type: ActivityType
    # Wall-clock time where the activity was done, so its date is the local day.
    start_local: datetime
    moving_time_s: float
    distance_mi: float
    vert_ft: float
    running_mi: float
    avg_heart_rate_bpm: float | None
    max_heart_rate_bpm: float | None
    grade: Grade | None
    # The recorded values of the overridable fields, whether or not they're overridden.
    recorded: dict[str, float | str]
    overridden_fields: frozenset[str]
    model_choice: ModelChoice | None


class UnknownActivity(KeyError):
    pass


def activity_from_entry(activity_id: str, entry: dict) -> Activity:
    recorded = entry["recorded"]
    manual = entry.get("manual", {})
    overrides = {field: manual[field] for field in OVERRIDABLE_FIELDS if field in manual}
    fields = {**recorded, **overrides}
    return Activity(
        id=activity_id,
        name=recorded["name"],
        type=ActivityType(fields["type"]),
        start_local=datetime.fromisoformat(recorded["start_local"]),
        moving_time_s=float(fields["moving_time_s"]),
        distance_mi=float(fields["distance_mi"]),
        vert_ft=float(fields["vert_ft"]),
        running_mi=float(fields["running_mi"]),
        avg_heart_rate_bpm=recorded.get("avg_heart_rate_bpm"),
        max_heart_rate_bpm=recorded.get("max_heart_rate_bpm"),
        grade=Grade(manual["grade"]) if "grade" in manual else None,
        recorded={"type": recorded["type"], **{field: float(recorded[field]) for field in OVERRIDABLE_NUMBER_FIELDS}},
        overridden_fields=frozenset(overrides),
        model_choice=ModelChoice(manual["model_choice"]) if "model_choice" in manual else None,
    )


def grade_in_note(note: str | None) -> Grade | None:
    """The grade from a "Grade: X" line in a private note, if it has one."""
    match = NOTE_GRADE_PATTERN.search(note or "")
    if match is None:
        return None
    try:
        return Grade(match.group(1).upper())
    except ValueError:
        return None


def new_entry(strava: dict, recording_file: str | None, recorded: dict) -> dict:
    """The entry for an activity entering the log. A "Grade: X" line in its Strava private note
    becomes its grade now and never again, so a grade cleared on the page stays cleared."""
    grade = grade_in_note(strava["private_note"])
    return {
        "strava": strava,
        "recording_file": recording_file,
        "recorded": recorded,
        "manual": {} if grade is None else {"grade": grade.value},
    }


def remove_entry(entries: dict[str, dict], activity_id: str, activity_files_dir: Path) -> None:
    """Take an activity out of the log, along with its recording file and hand edits."""
    entry = entries.pop(activity_id)
    if entry["recording_file"] is not None:
        (activity_files_dir / entry["recording_file"]).unlink(missing_ok=True)


def read_log(path: Path) -> dict[str, dict]:
    """The log's entries; a log that hasn't been created yet is empty."""
    return read_entries(path) if path.exists() else {}


def load_activities(path: Path = DEFAULT_LOG_PATH) -> list[Activity]:
    """Every activity, oldest first."""
    activities = [activity_from_entry(activity_id, entry) for activity_id, entry in read_log(path).items()]
    return sorted(activities, key=lambda a: a.start_local)


def validated_manual(manual: dict) -> dict:
    """`manual` as it will be stored, or ValueError if it holds anything that can't be edited by hand."""
    if not isinstance(manual, dict):
        raise ValueError("manual must be an object")
    unknown = set(manual) - {"grade", *OVERRIDABLE_FIELDS}
    if unknown:
        raise ValueError(f"can't set by hand: {', '.join(sorted(unknown))}")
    validated: dict = {}
    if "grade" in manual:
        try:
            validated["grade"] = Grade(manual["grade"]).value
        except ValueError:
            raise ValueError(f"grade must be one of {', '.join(Grade)}, not {manual['grade']!r}") from None
    if "type" in manual:
        try:
            validated["type"] = ActivityType(manual["type"]).value
        except ValueError:
            raise ValueError(f"type must be one of {', '.join(ActivityType)}, not {manual['type']!r}") from None
    for field in OVERRIDABLE_NUMBER_FIELDS:
        if field not in manual:
            continue
        value = manual[field]
        # bool is an int, but true/false is never meant as a number.
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise ValueError(f"{field} must be a number of at least 0, not {value!r}")
        validated[field] = value
    return validated


def set_manual(activity_id: str, manual: dict, path: Path = DEFAULT_LOG_PATH) -> Activity:
    """Replace an activity's grade and overrides, returning the activity with them applied. Its
    model choice is kept, as it's set separately."""
    entries = read_log(path)
    if activity_id not in entries:
        raise UnknownActivity(activity_id)
    previous = entries[activity_id].get("manual", {})
    kept = {"model_choice": previous["model_choice"]} if "model_choice" in previous else {}
    entries[activity_id]["manual"] = {**validated_manual(manual), **kept}
    write_entries(path, entries)
    return activity_from_entry(activity_id, entries[activity_id])


def set_model_choice(activity_id: str, choice: ModelChoice | None, path: Path = DEFAULT_LOG_PATH) -> Activity:
    """Require the activity in the climbing model, exclude it, or (None) leave it to its date."""
    entries = read_log(path)
    if activity_id not in entries:
        raise UnknownActivity(activity_id)
    manual = entries[activity_id].setdefault("manual", {})
    if choice is None:
        manual.pop("model_choice", None)
    else:
        manual["model_choice"] = choice.value
    write_entries(path, entries)
    return activity_from_entry(activity_id, entries[activity_id])
