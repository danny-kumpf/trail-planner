"""How far a recorded activity was run versus hiked, judged by cadence and pace."""

import math
from dataclasses import dataclass, replace
from itertools import groupby

import numpy as np

from trail_planner.recording import Recording
from trail_planner.trail_stats import M_PER_MI

# Running is ~165 steps/min and hiking ~110. 155 discriminated well on Lions_Lair while
# removing false positives from other hikes.
DEFAULT_RUNNING_THRESHOLD_STEPS_PER_MIN = 155.0
# Hiking can show running cadence for 10-20 s at a time, but at 25-60 min/mi; short runs on
# these recordings were 12-15 min/mi.
DEFAULT_MAX_RUNNING_PACE_MIN_PER_MI = 20.0
# Shorter changes of gait are a stray step or two, or a stumble while running.
DEFAULT_MIN_GAIT_STRETCH_S = 10.0
# Without cadence, pace alone decides; hiking is rarely faster than this, even downhill.
DEFAULT_PACE_ONLY_RUNNING_PACE_MIN_PER_MI = 15.0

# FIT files and the Garmin TrackPointExtension in GPX record cadence in strides (one foot) per minute.
STEPS_PER_STRIDE = 2
# Judging by cadence needs it on nearly every step; brief dropouts count as non-running cadence,
# which the minimum gait stretch then absorbs.
MIN_CADENCE_COVERAGE_FRAC = 0.9


@dataclass(frozen=True)
class RecordedTrack:
    """A recorded activity as a sequence of steps between consecutive track points.

    Each step's cadence and heart rate are the ones recorded at its end point.
    """

    step_m: np.ndarray
    step_duration_s: np.ndarray
    # None when the recording has no cadence at all; NaN on steps where it dropped out.
    step_cadence_steps_per_min: np.ndarray | None = None
    # None when the recording has no heart rate at all; NaN on steps where it dropped out.
    step_heart_rate_bpm: np.ndarray | None = None

    @property
    def has_usable_cadence(self) -> bool:
        """Whether enough steps have cadence to judge running by it."""
        if self.step_cadence_steps_per_min is None:
            return False
        coverage_frac = float((~np.isnan(self.step_cadence_steps_per_min)).mean())
        return coverage_frac >= MIN_CADENCE_COVERAGE_FRAC


@dataclass(frozen=True)
class GaitSplit:
    running_mi: float
    hiking_mi: float

    @property
    def total_mi(self) -> float:
        return self.running_mi + self.hiking_mi


@dataclass(frozen=True)
class HeartRateSummary:
    avg_bpm: float
    max_bpm: float


def recorded_track_from_recording(recording: Recording) -> RecordedTrack:
    return RecordedTrack(
        step_m=recording.step_m(),
        step_duration_s=np.diff(recording.time_s),
        step_cadence_steps_per_min=(
            None
            if recording.cadence_strides_per_min is None
            else recording.cadence_strides_per_min[1:] * STEPS_PER_STRIDE
        ),
        step_heart_rate_bpm=None if recording.heart_rate_bpm is None else recording.heart_rate_bpm[1:],
    )


def heart_rate_summary(track: RecordedTrack) -> HeartRateSummary | None:
    """Time-weighted average and maximum heart rate, or None if none was recorded."""
    if track.step_heart_rate_bpm is None:
        return None
    has_heart_rate = ~np.isnan(track.step_heart_rate_bpm)
    if not has_heart_rate.any():
        return None
    heart_rate_bpm = track.step_heart_rate_bpm[has_heart_rate]
    duration_s = track.step_duration_s[has_heart_rate]
    if duration_s.sum() == 0:
        avg_bpm = float(heart_rate_bpm.mean())
    else:
        avg_bpm = float((heart_rate_bpm * duration_s).sum() / duration_s.sum())
    return HeartRateSummary(avg_bpm=avg_bpm, max_bpm=float(heart_rate_bpm.max()))


@dataclass(frozen=True)
class GaitStretch:
    """Consecutive steps taken at one gait."""

    is_running: bool
    num_steps: int
    duration_s: float
    distance_m: float

    @property
    def pace_min_per_mi(self) -> float:
        """Infinite for a stretch that covered no distance."""
        if self.distance_m == 0:
            return math.inf
        return (self.duration_s / 60) / (self.distance_m / M_PER_MI)


def gait_stretches(is_running: np.ndarray, track: RecordedTrack) -> list[GaitStretch]:
    stretches: list[GaitStretch] = []
    for gait, step_indices in groupby(range(len(is_running)), key=lambda i: bool(is_running[i])):
        step_indices = list(step_indices)
        stretches.append(
            GaitStretch(
                is_running=gait,
                num_steps=len(step_indices),
                duration_s=float(track.step_duration_s[step_indices].sum()),
                distance_m=float(track.step_m[step_indices].sum()),
            )
        )
    return stretches


def step_gaits(stretches: list[GaitStretch]) -> np.ndarray:
    """Whether each step is running, from the stretches the steps make up."""
    return np.concatenate([np.full(s.num_steps, s.is_running) for s in stretches])


def slow_running_as_hiking(
    is_running: np.ndarray, track: RecordedTrack, max_running_pace_min_per_mi: float
) -> np.ndarray:
    """Count each stretch of running slower than `max_running_pace_min_per_mi` as hiking.

    Pace is judged over the whole stretch, as GPS speed over a single step is too noisy.
    """
    return step_gaits(
        [
            replace(s, is_running=False)
            if s.is_running and s.pace_min_per_mi > max_running_pace_min_per_mi
            else s
            for s in gait_stretches(is_running, track)
        ]
    )


def ignore_brief_gait_changes(
    is_running: np.ndarray, track: RecordedTrack, min_stretch_s: float
) -> np.ndarray:
    """Give each stretch of one gait lasting under `min_stretch_s` the gait around it.

    Shortest stretches go first, so a brief stumble inside a run is absorbed before the run's
    halves are judged, rather than each half looking brief on its own.
    """
    stretches = gait_stretches(is_running, track)
    while len(stretches) > 1:
        i = min(range(len(stretches)), key=lambda i: stretches[i].duration_s)
        if stretches[i].duration_s >= min_stretch_s:
            break
        # Its neighbours both have the other gait, so the three become one stretch of that gait.
        first, last = max(i - 1, 0), min(i + 1, len(stretches) - 1)
        merged = stretches[first : last + 1]
        stretches[first : last + 1] = [
            GaitStretch(
                is_running=not stretches[i].is_running,
                num_steps=sum(s.num_steps for s in merged),
                duration_s=sum(s.duration_s for s in merged),
                distance_m=sum(s.distance_m for s in merged),
            )
        ]
    return step_gaits(stretches)


def split_by_gait(
    track: RecordedTrack,
    running_threshold_steps_per_min: float = DEFAULT_RUNNING_THRESHOLD_STEPS_PER_MIN,
    max_running_pace_min_per_mi: float = DEFAULT_MAX_RUNNING_PACE_MIN_PER_MI,
    min_gait_stretch_s: float = DEFAULT_MIN_GAIT_STRETCH_S,
) -> GaitSplit:
    """Running is a running cadence held at a running pace; everything else is hiking."""
    if not track.has_usable_cadence:
        raise ValueError("the recording has no cadence, or too little to judge running by")
    # A step whose cadence dropped out doesn't have running cadence.
    has_running_cadence = (
        np.nan_to_num(track.step_cadence_steps_per_min, nan=0.0) >= running_threshold_steps_per_min
    )
    is_running = ignore_brief_gait_changes(
        slow_running_as_hiking(has_running_cadence, track, max_running_pace_min_per_mi),
        track,
        min_gait_stretch_s,
    )
    return gait_split(is_running, track)


def split_by_pace(
    track: RecordedTrack,
    running_pace_min_per_mi: float = DEFAULT_PACE_ONLY_RUNNING_PACE_MIN_PER_MI,
    min_gait_stretch_s: float = DEFAULT_MIN_GAIT_STRETCH_S,
) -> GaitSplit:
    """For recordings without cadence: running is any stretch faster than `running_pace_min_per_mi`.

    Cruder than `split_by_gait`, since a fast hike downhill and a slow run uphill overlap in pace.
    """
    step_pace_min_per_mi = np.divide(
        track.step_duration_s / 60,
        track.step_m / M_PER_MI,
        out=np.full(len(track.step_m), np.inf),
        where=track.step_m > 0,
    )
    is_running = ignore_brief_gait_changes(
        step_pace_min_per_mi < running_pace_min_per_mi, track, min_gait_stretch_s
    )
    return gait_split(is_running, track)


def gait_split(is_running: np.ndarray, track: RecordedTrack) -> GaitSplit:
    return GaitSplit(
        running_mi=float(track.step_m[is_running].sum()) / M_PER_MI,
        hiking_mi=float(track.step_m[~is_running].sum()) / M_PER_MI,
    )
