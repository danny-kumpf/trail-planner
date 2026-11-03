"""A recording resampled onto an even time grid, with the vertical rate, grade and heart rate that
climbs are measured from.

Samples arrive irregularly, and a rate from unevenly spaced samples is silently wrong, so every
channel is resampled onto one grid first. That also makes a smoothing window a real duration.
"""

from dataclasses import dataclass

import numpy as np
from scipy.signal import savgol_filter

from trail_planner.recording import DEFAULT_MIN_MOVING_SPEED_M_PER_S, Recording
from trail_planner.trail_stats import FT_PER_M, profile_from_along_track

RESAMPLE_INTERVAL_S = 1.0
# Elevation against time is smoothed over this long before it's differentiated for vertical rate.
TIME_SMOOTHING_WINDOW_S = 61.0
# Grade comes from elevation against distance alone, so stopping on a slope doesn't change it. It's
# smoothed over a wide window on purpose: whether a stretch is a climb is a question about hundreds
# of metres of terrain, and narrower windows let grade flicker across the climb floor and shatter
# sustained climbs into pieces too short to keep.
GRADE_GRID_SPACING_M = 5.0
GRADE_SMOOTHING_WINDOW_M = 200.0
SAVGOL_POLYORDER = 2
# Faster than this is a recording fault, not fast climbing: the best uphill runners in the world
# sustain about 6,000 ft/hr. A GPS that loses its fix in a couloir and finds it a minute later lands
# that minute of climbing in a few seconds.
MAX_CLIMB_FT_PER_HR = 6000.0
# The same going down, set higher because descending really is faster.
MAX_DESCENT_FT_PER_HR = 8000.0
# A heart rate monitor can read far too low for a while, then lock on: the reading jumps at least
# LOCK_ON_RISE_BPM within LOCK_ON_WITHIN_S and stays up over the LOCK_ON_HOLD_S after. A heart only
# rises that fast when effort suddenly increases, such as setting off from a stop or reaching a
# steeper pitch, so a jump is a lock-on only if the recording shows effort holding steady across it.
LOCK_ON_RISE_BPM = 20.0
LOCK_ON_WITHIN_S = 5.0
LOCK_ON_HOLD_S = 60.0
# Effort across a jump is the climbing rate over the EFFORT_ONSET_S either side of it, compared with
# the EFFORT_BEFORE_S before that: a heart answers a sudden increase in effort within about that long.
EFFORT_ONSET_S = 30.0
EFFORT_BEFORE_S = 120.0
# Climbing rates closer than this are the same effort.
SAME_EFFORT_FT_PER_HR = 500.0
# Moving for less of the time before a jump than this is setting off from a stop.
MIN_MOVING_FRAC = 0.5
# Before a lock-on, the monitor was misreading for as long as it read lower than where it landed by
# more than this, plus whatever easing off in effort could explain. Heart rate is taken to fall by
# at most 1 bpm for each EASING_OFF_PER_BPM_FT_PER_HR less climbing rate, a generous bound: on these
# recordings it falls 1 bpm per 40-70 ft/hr. Effort here is averaged over EFFORT_BEFORE_S, so a
# brief flat stretch isn't easing off.
MISREAD_MARGIN_BPM = 15.0
EASING_OFF_PER_BPM_FT_PER_HR = 40.0


@dataclass(frozen=True)
class ClimbSignals:
    """A recording on an even time grid, timed from the recording's start."""

    time_s: np.ndarray
    raw_elevation_ft: np.ndarray
    elevation_ft: np.ndarray
    # NaN where `rate_is_implausible`: the recording lost when the climbing happened there, so the
    # rate is unknown rather than fast.
    vert_rate_ft_per_hr: np.ndarray
    rate_is_implausible: np.ndarray
    along_track_m: np.ndarray
    grade_frac: np.ndarray
    # None if no heart rate was recorded; NaN where it dropped out or was misread.
    heart_rate_bpm: np.ndarray | None
    # Where the monitor was reading low before it locked on; None if no heart rate was recorded.
    heart_rate_misread: np.ndarray | None

    @property
    def sample_interval_s(self) -> float:
        return float(self.time_s[1] - self.time_s[0])


def true_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Half-open [start, stop) index ranges of consecutive True values."""
    padded = np.concatenate(([False], mask.astype(bool), [False]))
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    return [(int(start), int(stop)) for start, stop in zip(edges[0::2], edges[1::2])]


def smoothed(values: np.ndarray, step: float, window: float) -> np.ndarray:
    """`values` sampled every `step`, low-passed over `window` in the same units."""
    min_length = SAVGOL_POLYORDER + 1
    if len(values) <= min_length:
        return values.copy()
    # Savitzky-Golay needs an odd window, no longer than the data.
    length = max(round(window / step), min_length) | 1
    if length > len(values):
        length = len(values) if len(values) % 2 else len(values) - 1
    return savgol_filter(values, max(length, min_length), SAVGOL_POLYORDER)


def implausible_rate_mask(vert_rate_ft_per_hr: np.ndarray, sample_interval_s: float) -> np.ndarray:
    """Samples whose vertical rate can't be believed, widened to every rate their elevation fed.

    A bad elevation sample corrupts every rate whose smoothing window holds it, so a span past the
    bounds is grown by half that window each way.
    """
    impossible = (vert_rate_ft_per_hr > MAX_CLIMB_FT_PER_HR) | (vert_rate_ft_per_hr < -MAX_DESCENT_FT_PER_HR)
    if not impossible.any():
        return impossible
    reach = round(TIME_SMOOTHING_WINDOW_S / 2 / sample_interval_s)
    return np.convolve(impossible.astype(int), np.ones(2 * reach + 1, dtype=int), mode="same") > 0


@dataclass(frozen=True)
class HeartRateJump:
    """A sudden rise in heart rate that held, as indices into the samples it was found in."""

    # The last sample before the rise.
    rise_start: int
    # The highest sample within LOCK_ON_WITHIN_S of `rise_start`.
    landed: int
    # The median over the LOCK_ON_HOLD_S from `landed`.
    held_bpm: float


def heart_rate_jumps(time_s: np.ndarray, heart_rate_bpm: np.ndarray) -> list[HeartRateJump]:
    """Rises of at least LOCK_ON_RISE_BPM within LOCK_ON_WITHIN_S that hold there, in samples that
    all have a heart rate."""
    window_end = np.searchsorted(time_s, time_s + LOCK_ON_WITHIN_S, side="right")
    jumps = []
    i = 0
    while i < len(time_s) - 1:
        if window_end[i] > i + 1:
            top = i + 1 + int(np.argmax(heart_rate_bpm[i + 1 : window_end[i]]))
            if heart_rate_bpm[top] - heart_rate_bpm[i] >= LOCK_ON_RISE_BPM:
                hold_end = np.searchsorted(time_s, time_s[top] + LOCK_ON_HOLD_S, side="right")
                held_bpm = float(np.median(heart_rate_bpm[top:hold_end]))
                if held_bpm - heart_rate_bpm[i] >= LOCK_ON_RISE_BPM:
                    jumps.append(HeartRateJump(rise_start=i, landed=top, held_bpm=held_bpm))
                    i = top
                    continue
        i += 1
    return jumps


def mean_known(values: np.ndarray) -> float | None:
    known = values[~np.isnan(values)]
    return float(known.mean()) if known.size else None


def moving_average_of_known(values: np.ndarray, window_len: int) -> np.ndarray:
    """Each value averaged with the known ones in a centred window; NaN where none are known."""
    known = ~np.isnan(values)
    window = np.ones(window_len)
    totals = np.convolve(np.where(known, values, 0.0), window, mode="same")
    counts = np.convolve(known.astype(float), window, mode="same")
    return np.divide(totals, counts, out=np.full(len(values), np.nan), where=counts > 0)


def steady_effort_ft_per_hr(
    jump_s: float, grid_s: np.ndarray, vert_rate_ft_per_hr: np.ndarray, is_moving: np.ndarray
) -> float | None:
    """The climbing rate that effort held steady at across a heart rate jump at `jump_s`. None
    unless the recording shows it holding steady: already moving, and climbing no faster across
    the jump than before it."""
    before = (grid_s >= jump_s - EFFORT_ONSET_S - EFFORT_BEFORE_S) & (grid_s < jump_s - EFFORT_ONSET_S)
    across = (grid_s >= jump_s - EFFORT_ONSET_S) & (grid_s < jump_s + EFFORT_ONSET_S)
    rate_before = mean_known(vert_rate_ft_per_hr[before])
    rate_across = mean_known(vert_rate_ft_per_hr[across])
    if rate_before is None or rate_across is None or is_moving[before].mean() < MIN_MOVING_FRAC:
        return None
    return rate_before if rate_across - rate_before < SAME_EFFORT_FT_PER_HR else None


def misreading_start(
    heart_rate_bpm: np.ndarray, effort_ft_per_hr: np.ndarray, jump: HeartRateJump, steady_ft_per_hr: float
) -> int:
    """The first sample of the misreading that `jump` ended: the run of samples before it reading
    further below where it held than easing off from the steady effort explains."""
    earlier = slice(0, jump.rise_start)
    eased_off_ft_per_hr = np.maximum(steady_ft_per_hr - effort_ft_per_hr[earlier], 0.0)
    explained_bpm = MISREAD_MARGIN_BPM + eased_off_ft_per_hr / EASING_OFF_PER_BPM_FT_PER_HR
    misreading = heart_rate_bpm[earlier] < jump.held_bpm - explained_bpm
    breaks = np.flatnonzero(~misreading)
    return int(breaks[-1]) + 1 if breaks.size else 0


def misread_heart_rate(
    time_s: np.ndarray,
    heart_rate_bpm: np.ndarray,
    grid_s: np.ndarray,
    vert_rate_ft_per_hr: np.ndarray,
    is_moving: np.ndarray,
) -> np.ndarray:
    """Which samples were recorded while the monitor read low, before it locked on.

    `vert_rate_ft_per_hr` and `is_moving` are on `grid_s`; the rate is NaN where it's unknown.
    """
    recorded = np.flatnonzero(~np.isnan(heart_rate_bpm))
    recorded_s, recorded_bpm = time_s[recorded], heart_rate_bpm[recorded]
    effort_window_len = round(EFFORT_BEFORE_S / (grid_s[1] - grid_s[0])) | 1
    effort_ft_per_hr = moving_average_of_known(vert_rate_ft_per_hr, effort_window_len)
    effort_at_recorded_ft_per_hr = np.interp(recorded_s, grid_s, effort_ft_per_hr)
    misread = np.zeros(len(time_s), dtype=bool)
    for jump in heart_rate_jumps(recorded_s, recorded_bpm):
        steady = steady_effort_ft_per_hr(float(recorded_s[jump.rise_start]), grid_s, vert_rate_ft_per_hr, is_moving)
        if steady is not None:
            start = misreading_start(recorded_bpm, effort_at_recorded_ft_per_hr, jump, steady)
            misread[recorded[start : jump.landed]] = True
    return misread


def interpolated_with_gaps(grid_s: np.ndarray, time_s: np.ndarray, values: np.ndarray) -> np.ndarray:
    """`values` on the grid, NaN wherever they were NaN, rather than bridged by a straight line."""
    known = ~np.isnan(values)
    if not known.any():
        return np.full(len(grid_s), np.nan)
    interpolated = np.interp(grid_s, time_s[known], values[known])
    still_known = np.interp(grid_s, time_s, known.astype(float)) >= 1.0
    return np.where(still_known, interpolated, np.nan)


def grade_by_distance(along_track_m: np.ndarray, elevation_m: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Grade along the track, as (distance grid, grade at each grid point)."""
    profile = profile_from_along_track(along_track_m, elevation_m)
    grid_m = np.arange(0.0, profile.length_m, GRADE_GRID_SPACING_M)
    if len(grid_m) <= SAVGOL_POLYORDER + 1:
        return np.array([0.0, max(profile.length_m, 1.0)]), np.zeros(2)
    elevation_on_grid_m = np.interp(grid_m, profile.along_track_m, profile.elevation_m)
    smoothed_m = smoothed(elevation_on_grid_m, GRADE_GRID_SPACING_M, GRADE_SMOOTHING_WINDOW_M)
    return grid_m, np.gradient(smoothed_m, grid_m)


def climb_signals(recording: Recording) -> ClimbSignals:
    """Raises ValueError if the recording has too little elevation to measure climbing by."""
    if recording.elevation_m is None or np.count_nonzero(~np.isnan(recording.elevation_m)) < 2:
        raise ValueError("the recording has no elevation")
    has_elevation = ~np.isnan(recording.elevation_m)
    grid_s = np.arange(recording.time_s[0], recording.time_s[-1] + RESAMPLE_INTERVAL_S / 2, RESAMPLE_INTERVAL_S)
    if len(grid_s) < 2:
        raise ValueError("the recording is too short")

    raw_elevation_ft = np.interp(grid_s, recording.time_s[has_elevation], recording.elevation_m[has_elevation]) * FT_PER_M
    elevation_ft = smoothed(raw_elevation_ft, RESAMPLE_INTERVAL_S, TIME_SMOOTHING_WINDOW_S)
    measured_vert_rate_ft_per_hr = np.gradient(elevation_ft, grid_s) * 3600
    rate_is_implausible = implausible_rate_mask(measured_vert_rate_ft_per_hr, RESAMPLE_INTERVAL_S)
    vert_rate_ft_per_hr = np.where(rate_is_implausible, np.nan, measured_vert_rate_ft_per_hr)

    along_track_m = recording.along_track_m()
    distance_grid_m, grade_on_grid_frac = grade_by_distance(along_track_m, recording.elevation_m)
    along_track_on_grid_m = np.interp(grid_s, recording.time_s, along_track_m)

    heart_rate_bpm = None
    heart_rate_misread = None
    if recording.heart_rate_bpm is not None and (~np.isnan(recording.heart_rate_bpm)).any():
        is_moving = np.gradient(along_track_on_grid_m, grid_s) > DEFAULT_MIN_MOVING_SPEED_M_PER_S
        misread = misread_heart_rate(recording.time_s, recording.heart_rate_bpm, grid_s, vert_rate_ft_per_hr, is_moving)
        believed_bpm = np.where(misread, np.nan, recording.heart_rate_bpm)
        heart_rate_bpm = interpolated_with_gaps(grid_s, recording.time_s, believed_bpm)
        heart_rate_misread = np.interp(grid_s, recording.time_s, misread.astype(float)) > 0

    return ClimbSignals(
        time_s=grid_s,
        raw_elevation_ft=raw_elevation_ft,
        elevation_ft=elevation_ft,
        vert_rate_ft_per_hr=vert_rate_ft_per_hr,
        rate_is_implausible=rate_is_implausible,
        along_track_m=along_track_on_grid_m,
        grade_frac=np.interp(along_track_on_grid_m, distance_grid_m, grade_on_grid_frac),
        heart_rate_bpm=heart_rate_bpm,
        heart_rate_misread=heart_rate_misread,
    )
