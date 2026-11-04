"""Sustained climbs in a recording, cut into the blocks the climbing model is fit on.

Single samples are too noisy to model; the relationship between heart rate and climbing rate only
emerges once they're averaged over about as long as heart rate takes to catch up with effort.
"""

from dataclasses import dataclass

import numpy as np

from trail_planner.climb_signals import ClimbSignals, climb_signals, true_runs
from trail_planner.recording import Recording

# How far heart rate trails the effort that produced it. Not a measured constant: correlating heart
# rate against climbing rate never gave a lag credible enough to use on these recordings, so this
# is a typical value. A different heart rate monitor may lag differently; revisit it after a change.
HR_LAG_S = 45.0
# Climbing is terrain at least this steep...
CLIMB_FLOOR_GRADE_FRAC = 0.05
# ...being climbed at least this fast. Slower is stopped (fiddling with a pack, refilling water),
# not climbing slowly. Measured as height gained, as horizontal speed falls as terrain steepens.
MIN_CLIMBING_FT_PER_HR = 100.0
# Heart rate can't have caught up with the effort over a shorter climb.
MIN_CLIMB_S = 2 * HR_LAG_S
BLOCK_S = HR_LAG_S
# The end of a climb rarely fills a whole block. A shorter remainder is kept if it's at least this
# long, since the model weights each block by its duration.
MIN_PARTIAL_BLOCK_S = BLOCK_S / 2
# Increase after changing how blocks come out of a recording, here or in climb_signals, so that
# blocks stored before the change are recomputed.
CLIMB_ANALYSIS_VERSION = 4


@dataclass(frozen=True)
class ClimbRun:
    """A stretch of consecutive climbing samples, as the half-open index range [start, stop)."""

    start: int
    stop: int
    duration_s: float


@dataclass(frozen=True)
class ClimbBlock:
    """A stretch of steady climbing, averaged: one observation for the model."""

    start_s: float
    duration_s: float
    vert_rate_ft_per_hr: float
    elevation_ft: float
    grade_frac: float
    # Heart rate shifted back by the lag, so it's the one the block's effort produced. None if any
    # of it is missing.
    heart_rate_bpm: float | None
    # The share of the block that was run rather than hiked.
    running_frac: float


@dataclass(frozen=True)
class ClimbAnalysis:
    signals: ClimbSignals
    climbs: list[ClimbRun]
    # Climbing too brief to reach a steady heart rate, kept so it can be shown rather than vanish.
    short_climbs: list[ClimbRun]
    blocks: list[ClimbBlock]


def is_climbing(signals: ClimbSignals) -> np.ndarray:
    """Steep enough to be a climb, and actually being climbed. An unknown rate isn't climbing."""
    return (signals.grade_frac >= CLIMB_FLOOR_GRADE_FRAC) & (signals.vert_rate_ft_per_hr >= MIN_CLIMBING_FT_PER_HR)


def climb_runs(signals: ClimbSignals) -> list[ClimbRun]:
    return [
        ClimbRun(start, stop, float(signals.time_s[stop - 1] - signals.time_s[start]))
        for start, stop in true_runs(is_climbing(signals))
    ]


def heart_rate_after_lag(signals: ClimbSignals) -> np.ndarray | None:
    """Each sample's heart rate HR_LAG_S later, so it lines up with the effort that produced it."""
    if signals.heart_rate_bpm is None:
        return None
    shift = round(HR_LAG_S / signals.sample_interval_s)
    return np.concatenate([signals.heart_rate_bpm[shift:], np.full(shift, np.nan)])


def blocks_of(signals: ClimbSignals, climbs: list[ClimbRun]) -> list[ClimbBlock]:
    """Each climb cut into blocks, never spanning two climbs."""
    block_len = round(BLOCK_S / signals.sample_interval_s)
    min_len = round(MIN_PARTIAL_BLOCK_S / signals.sample_interval_s)
    heart_rate_bpm = heart_rate_after_lag(signals)
    blocks = []
    for climb in climbs:
        for start in range(climb.start, climb.stop, block_len):
            block = slice(start, min(start + block_len, climb.stop))
            if block.stop - block.start < min_len:
                continue
            block_heart_rate_bpm = None if heart_rate_bpm is None else heart_rate_bpm[block]
            blocks.append(
                ClimbBlock(
                    start_s=float(signals.time_s[start]),
                    duration_s=(block.stop - block.start) * signals.sample_interval_s,
                    vert_rate_ft_per_hr=float(np.mean(signals.vert_rate_ft_per_hr[block])),
                    elevation_ft=float(np.mean(signals.elevation_ft[block])),
                    grade_frac=float(np.median(signals.grade_frac[block])),
                    heart_rate_bpm=(
                        float(np.mean(block_heart_rate_bpm))
                        if block_heart_rate_bpm is not None and not np.isnan(block_heart_rate_bpm).any()
                        else None
                    ),
                    running_frac=float(np.mean(signals.is_running[block])),
                )
            )
    return blocks


def analyse_climbs(recording: Recording) -> ClimbAnalysis:
    """Raises ValueError if the recording has too little elevation to measure climbing by."""
    return climbs_in(climb_signals(recording))


def climbs_in(signals: ClimbSignals) -> ClimbAnalysis:
    runs = climb_runs(signals)
    climbs = [run for run in runs if run.duration_s >= MIN_CLIMB_S]
    return ClimbAnalysis(
        signals=signals,
        climbs=climbs,
        short_climbs=[run for run in runs if run.duration_s < MIN_CLIMB_S],
        blocks=blocks_of(signals, climbs),
    )
