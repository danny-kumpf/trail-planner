"""Bring the training log up to date with Strava.

Every sync lists every activity's summary, which takes one request per 200 activities. Then:
- An activity already in the log gets Strava's latest summary, picking up e.g. a rename, a crop,
  a race flag or the local start time the export lacked. If that changed anything, its `recorded`
  block is recomputed from its stored recording.
- A new activity's details (for its private note) and streams (its recorded channels) are
  downloaded. The streams are kept in activity_files/ as <id>.streams.json.gz, so a rebuild never
  needs to download them again.

Resort skiing and snowboarding are left out, and an activity changed to one of those on Strava
is removed. Activities in the log that are no longer on Strava are reported, not removed, so their
edits aren't lost to a mistake.

Strava allows 100 reads per 15 minutes, and each new activity takes two. A sync that reaches the
limit keeps what it has done, and the next sync adds the rest.
"""

import gzip
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from trail_planner.activity_stats import is_logged, recorded_for_entry
from trail_planner.json_store import write_entries
from trail_planner.strava_api import NotAuthorized, NotFound, RateLimited, StravaClient, StravaError
from trail_planner.training_log import new_entry, read_log, remove_entry


@dataclass
class SyncReport:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    # New activities left for the next sync because Strava's rate limit was reached.
    not_yet_added: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    grades_copied: dict[str, str] = field(default_factory=dict)
    # Activities changed on Strava to a type the log doesn't keep, e.g. resort snowboarding.
    removed: list[str] = field(default_factory=list)
    not_on_strava: list[str] = field(default_factory=list)


def label(name: str, activity_id: str) -> str:
    return f"{name} ({activity_id})"


def strava_summary(activity: dict) -> dict:
    """The `strava` block's fields that an activity's summary in Strava's list gives."""
    return {
        "name": activity["name"],
        "type": activity["sport_type"],
        "start_utc": datetime.fromisoformat(activity["start_date"]).isoformat(),
        # Wall-clock time where the activity was done, though Strava writes it with a "Z".
        "start_local": datetime.fromisoformat(activity["start_date_local"]).replace(tzinfo=None).isoformat(),
        "moving_time_s": float(activity["moving_time"]),
        "elapsed_time_s": float(activity["elapsed_time"]),
        "distance_m": float(activity["distance"]),
        "elevation_gain_m": activity.get("total_elevation_gain"),
        # Absent if no heart rate was recorded.
        "avg_heart_rate_bpm": activity.get("average_heartrate"),
        "max_heart_rate_bpm": activity.get("max_heartrate"),
        "workout_type": activity.get("workout_type"),
    }


def refresh_entry(entry: dict, summary: dict, activity_files_dir: Path) -> bool:
    """Give the entry Strava's latest summary, keeping the fields only details give (the notes).
    Returns whether anything changed."""
    strava = {**entry["strava"], **summary}
    if strava == entry["strava"]:
        return False
    entry["recorded"] = recorded_for_entry({**entry, "strava": strava}, activity_files_dir)
    entry["strava"] = strava
    return True


def save_streams(client: StravaClient, activity_id: str, start_utc: str, activity_files_dir: Path) -> str | None:
    """Download the activity's streams into activity_files/, returning the file's name; None if it
    has none."""
    try:
        streams = client.activity_streams(activity_id)
    except NotFound:
        return None
    file_name = f"{activity_id}.streams.json.gz"
    stored = {"start_utc": start_utc, "streams": streams}
    (activity_files_dir / file_name).write_bytes(gzip.compress(json.dumps(stored).encode("utf-8")))
    return file_name


def downloaded_entry(client: StravaClient, activity: dict, activity_files_dir: Path) -> dict:
    """A new activity's entry, from its summary, details and streams."""
    activity_id = str(activity["id"])
    details = client.activity_details(activity_id)
    strava = {
        **strava_summary(activity),
        "private_note": details.get("private_note"),
        "description": details.get("description"),
    }
    # An activity entered by hand has no streams, so don't spend a request asking.
    recording_file = (
        None if activity["manual"] else save_streams(client, activity_id, strava["start_utc"], activity_files_dir)
    )
    recorded = recorded_for_entry({"strava": strava, "recording_file": recording_file}, activity_files_dir)
    return new_entry(strava, recording_file, recorded)


def sync(client: StravaClient, log_path: Path, activity_files_dir: Path) -> SyncReport:
    report = SyncReport()
    entries = read_log(log_path)
    activity_files_dir.mkdir(parents=True, exist_ok=True)
    summaries = client.list_activities()

    on_strava = {str(a["id"]) for a in summaries}
    report.not_on_strava = [
        label(entry["recorded"]["name"], activity_id) for activity_id, entry in entries.items() if activity_id not in on_strava
    ]

    try:
        for activity in summaries:
            activity_id = str(activity["id"])
            if activity_id in entries and not is_logged(activity["sport_type"]):
                remove_entry(entries, activity_id, activity_files_dir)
                report.removed.append(label(activity["name"], activity_id))
        logged = [a for a in summaries if is_logged(a["sport_type"])]

        for activity in logged:
            activity_id = str(activity["id"])
            if activity_id not in entries:
                continue
            try:
                if refresh_entry(entries[activity_id], strava_summary(activity), activity_files_dir):
                    report.updated.append(label(activity["name"], activity_id))
            except (ValueError, OSError) as e:
                report.failed[label(activity["name"], activity_id)] = str(e)

        # Newest first, so if the rate limit is reached it's the oldest that wait.
        new_activities = [a for a in logged if str(a["id"]) not in entries]
        for i, activity in enumerate(new_activities):
            activity_id = str(activity["id"])
            try:
                entries[activity_id] = downloaded_entry(client, activity, activity_files_dir)
            except RateLimited:
                report.not_yet_added = [label(a["name"], str(a["id"])) for a in new_activities[i:]]
                break
            except NotAuthorized:
                raise
            except (StravaError, ValueError, OSError) as e:
                report.failed[label(activity["name"], activity_id)] = str(e)
                continue
            report.added.append(label(activity["name"], activity_id))
            if "grade" in entries[activity_id]["manual"]:
                report.grades_copied[label(activity["name"], activity_id)] = entries[activity_id]["manual"]["grade"]
    finally:
        # Keep whatever was done, even if Strava stopped answering partway through.
        write_entries(log_path, entries)
    return report
