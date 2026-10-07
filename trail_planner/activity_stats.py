"""The `recorded` block of a training-log entry: what we compute from Strava's summary of an
activity and its recording file.
"""

from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from trail_planner.effort import (
    DEFAULT_PACE_ONLY_RUNNING_PACE_MIN_PER_MI,
    heart_rate_summary,
    recorded_track_from_recording,
    split_by_gait,
    split_by_pace,
)
from trail_planner.recording import Recording, moving_time_s, read_recording
from trail_planner.trail_stats import FT_PER_M, M_PER_MI, profile_from_along_track, stats_from_profile
from trail_planner.training_log import ActivityType

# Strava's activity types, with spaces removed and lowercased: the export writes "Backcountry
# Ski" where the API writes "BackcountrySki". Anything else is a Workout.
ACTIVITY_TYPES_BY_STRAVA_TYPE = {
    "hike": ActivityType.HIKE,
    "walk": ActivityType.HIKE,
    "snowshoe": ActivityType.HIKE,
    "run": ActivityType.TRAIL_RUN,
    "trailrun": ActivityType.TRAIL_RUN,
    "virtualrun": ActivityType.TRAIL_RUN,
    "ride": ActivityType.BIKE,
    "mountainbikeride": ActivityType.BIKE,
    "gravelride": ActivityType.BIKE,
    "ebikeride": ActivityType.BIKE,
    "emountainbikeride": ActivityType.BIKE,
    "virtualride": ActivityType.BIKE,
    "backcountryski": ActivityType.SKI,
    "nordicski": ActivityType.SKI,
}
# Resort skiing and snowboarding, which aren't training and are left out of the log.
UNLOGGED_STRAVA_TYPES = {"alpineski", "snowboard"}


# Strava's `workout_type` for a run marked as a race. (Rides use other numbers.)
STRAVA_RACE_RUN_WORKOUT_TYPE = 1


def normalized_strava_type(strava_type: str) -> str:
    return strava_type.replace(" ", "").lower()


def is_logged(strava_type: str) -> bool:
    """Whether activities of this Strava type belong in the training log."""
    return normalized_strava_type(strava_type) not in UNLOGGED_STRAVA_TYPES


def activity_type_for(strava_type: str, workout_type: int | None) -> ActivityType:
    """The log's type for Strava's type, and its workout type if known (the export doesn't give it)."""
    if not is_logged(strava_type):
        raise ValueError(f"{strava_type} activities aren't kept in the training log")
    activity_type = ACTIVITY_TYPES_BY_STRAVA_TYPE.get(normalized_strava_type(strava_type), ActivityType.WORKOUT)
    if activity_type == ActivityType.TRAIL_RUN and workout_type == STRAVA_RACE_RUN_WORKOUT_TYPE:
        return ActivityType.RACE
    return activity_type


def local_start(strava: dict, recording: Recording | None) -> datetime:
    """Strava's local start if it gave one, else the recording's, else the UTC start in this
    computer's time zone (right unless the activity was recorded while travelling)."""
    if strava.get("start_local"):
        return datetime.fromisoformat(strava["start_local"])
    if recording is not None and recording.local_start is not None:
        return recording.local_start
    return datetime.fromisoformat(strava["start_utc"]).astimezone().replace(tzinfo=None)


def vert_ft(recording: Recording) -> float:
    """Smoothed vert, as for trails; 0 for a recording without elevation."""
    if recording.elevation_m is None or np.count_nonzero(~np.isnan(recording.elevation_m)) < 2:
        return 0.0
    return stats_from_profile(profile_from_along_track(recording.along_track_m(), recording.elevation_m)).vert_ft


def running_mi(activity_type: ActivityType, recording: Recording) -> float:
    if not activity_type.is_on_foot:
        return 0.0
    track = recorded_track_from_recording(recording)
    split = split_by_gait(track) if track.has_usable_cadence else split_by_pace(track)
    return split.running_mi


def running_mi_from_summary(activity_type: ActivityType, distance_mi: float, moving_time_s: float) -> float:
    """Without a recording, all of it was run if its average pace was a running pace."""
    if not activity_type.is_on_foot or distance_mi == 0:
        return 0.0
    pace_min_per_mi = (moving_time_s / 60) / distance_mi
    return distance_mi if pace_min_per_mi < DEFAULT_PACE_ONLY_RUNNING_PACE_MIN_PER_MI else 0.0


def as_kept_on_strava(strava: dict, recording: Recording) -> Recording:
    """The part of the recording Strava kept. Cropping an activity on Strava changes its start and
    elapsed time, but the export and the original file still hold the whole recording."""
    start = datetime.fromisoformat(strava["start_utc"])
    return recording.between(start, start + timedelta(seconds=strava["elapsed_time_s"]))


def recorded_block(strava: dict, recording: Recording | None) -> dict:
    activity_type = activity_type_for(strava["type"], strava["workout_type"])
    if recording is not None:
        recording = as_kept_on_strava(strava, recording)
        distance_mi = float(recording.step_m().sum()) / M_PER_MI
        time_s = moving_time_s(recording)
        vert = vert_ft(recording)
        run_mi = running_mi(activity_type, recording)
        heart_rate = heart_rate_summary(recorded_track_from_recording(recording))
        avg_heart_rate_bpm = heart_rate.avg_bpm if heart_rate else None
        max_heart_rate_bpm = heart_rate.max_bpm if heart_rate else None
    else:
        distance_mi = strava["distance_m"] / M_PER_MI
        time_s = strava["moving_time_s"]
        vert = (strava["elevation_gain_m"] or 0.0) * FT_PER_M
        run_mi = running_mi_from_summary(activity_type, distance_mi, time_s)
        avg_heart_rate_bpm = strava["avg_heart_rate_bpm"]
        max_heart_rate_bpm = strava["max_heart_rate_bpm"]

    recorded = {
        "name": strava["name"],
        "type": activity_type.value,
        "start_local": local_start(strava, recording).isoformat(),
        "moving_time_s": round(time_s),
        "distance_mi": round(distance_mi, 2),
        "vert_ft": round(vert),
        "running_mi": round(run_mi, 2),
    }
    if avg_heart_rate_bpm is not None:
        recorded["avg_heart_rate_bpm"] = round(avg_heart_rate_bpm)
    if max_heart_rate_bpm is not None:
        recorded["max_heart_rate_bpm"] = round(max_heart_rate_bpm)
    return recorded


def recorded_for_entry(entry: dict, activity_files_dir: Path) -> dict:
    """The `recorded` block for a log entry, from its `strava` block and recording file."""
    recording_file = entry.get("recording_file")
    recording = read_recording(activity_files_dir / recording_file) if recording_file else None
    return recorded_block(entry["strava"], recording)
