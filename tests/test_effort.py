from datetime import datetime, timedelta, timezone

import gpxpy
import numpy as np
import pytest

from trail_planner.effort import (
    RecordedTrack,
    heart_rate_summary,
    ignore_brief_gait_changes,
    slow_running_as_hiking,
    recorded_track_from_recording,
    split_by_gait,
    split_by_pace,
)
from trail_planner.recording import recording_from_gpx
from trail_planner.trail_stats import M_PER_MI

START = datetime(2026, 8, 18, 16, 0, tzinfo=timezone.utc)
M_PER_DEG_LAT = 111_195.0


def gpx_text(
    cadences_strides_per_min: list[int | None],
    step_m: float = 10.0,
    step_s: int = 5,
    heart_rates_bpm: list[int | None] | None = None,
) -> str:
    """A track due north with one point per cadence, `step_m` and `step_s` apart."""
    points = []
    for i, cadence in enumerate(cadences_strides_per_min):
        heart_rate = heart_rates_bpm[i] if heart_rates_bpm else None
        fields_xml = (f"<gpxtpx:hr>{heart_rate}</gpxtpx:hr>" if heart_rate is not None else "") + (
            f"<gpxtpx:cad>{cadence}</gpxtpx:cad>" if cadence is not None else ""
        )
        cadence_xml = (
            f"<extensions><gpxtpx:TrackPointExtension>{fields_xml}</gpxtpx:TrackPointExtension></extensions>"
            if fields_xml
            else ""
        )
        time = (START + timedelta(seconds=i * step_s)).strftime("%Y-%m-%dT%H:%M:%SZ")
        points.append(
            f'<trkpt lat="{40 + i * step_m / M_PER_DEG_LAT:.7f}" lon="-105.0">'
            f"<ele>1800</ele><time>{time}</time>{cadence_xml}</trkpt>"
        )
    return f"""<?xml version="1.0"?>
<gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1"
 xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1">
<trk><trkseg>{"".join(points)}</trkseg></trk></gpx>"""


def track_from_gpx_text(text: str) -> RecordedTrack:
    return recorded_track_from_recording(recording_from_gpx(gpxpy.parse(text)))


def track(cadences_steps_per_min: list[float], step_m: float = 10.0, step_s: float = 5.0) -> RecordedTrack:
    n = len(cadences_steps_per_min)
    return RecordedTrack(
        step_m=np.full(n, step_m),
        step_duration_s=np.full(n, step_s),
        step_cadence_steps_per_min=np.array(cadences_steps_per_min, dtype=float),
    )


def test_read_recorded_track_doubles_cadence_and_takes_it_from_step_end():
    recorded = track_from_gpx_text(gpx_text([0, 55, 85]))
    np.testing.assert_allclose(recorded.step_cadence_steps_per_min, [110, 170])
    np.testing.assert_allclose(recorded.step_duration_s, [5, 5])
    np.testing.assert_allclose(recorded.step_m, [10, 10], rtol=0.01)


def test_cadence_dropouts_are_kept_as_gaps():
    recorded = track_from_gpx_text(gpx_text([55, None, 85]))
    np.testing.assert_allclose(recorded.step_cadence_steps_per_min, [np.nan, 170])


def test_judging_by_cadence_needs_it_on_nearly_every_step():
    # 1 dropout in 10 steps is 90% coverage; 2 is too many.
    one_dropout = track_from_gpx_text(gpx_text([85] * 10 + [None]))
    two_dropouts = track_from_gpx_text(gpx_text([85] * 9 + [None, None]))
    assert one_dropout.has_usable_cadence
    assert not two_dropouts.has_usable_cadence
    with pytest.raises(ValueError, match="cadence"):
        split_by_gait(two_dropouts)


def test_a_cadence_dropout_inside_a_run_is_absorbed():
    recorded = track([170] * 10 + [np.nan] + [170] * 10)
    assert split_by_gait(recorded, min_gait_stretch_s=10).hiking_mi == 0


def test_read_recorded_track_without_cadence():
    recorded = track_from_gpx_text(gpx_text([None] * 3))
    assert recorded.step_cadence_steps_per_min is None
    with pytest.raises(ValueError, match="cadence"):
        split_by_gait(recorded)


def test_heart_rate_is_time_weighted_and_may_have_gaps():
    recorded = track_from_gpx_text(gpx_text([None] * 4, heart_rates_bpm=[90, 100, None, 130]))
    np.testing.assert_allclose(recorded.step_heart_rate_bpm, [100, np.nan, 130])
    summary = heart_rate_summary(recorded)
    assert summary.avg_bpm == pytest.approx(115)
    assert summary.max_bpm == 130


def test_no_heart_rate():
    recorded = track_from_gpx_text(gpx_text([None] * 3))
    assert recorded.step_heart_rate_bpm is None
    assert heart_rate_summary(recorded) is None


def test_split_by_pace():
    # 5 s per step: 10 m is 13.4 min/mi, faster than 15; 5 m is 26.8 min/mi.
    recorded = track([0] * 40, step_m=5.0)
    recorded.step_m[10:30] = 10.0
    split = split_by_pace(recorded, running_pace_min_per_mi=15, min_gait_stretch_s=10)
    assert split.running_mi == pytest.approx(200 / M_PER_MI)
    assert split.hiking_mi == pytest.approx(100 / M_PER_MI)


def test_split_by_pace_absorbs_a_brief_fast_blip():
    # One step of GPS jitter that looks fast inside a hike.
    recorded = track([0] * 20, step_m=5.0)
    recorded.step_m[10] = 30.0
    split = split_by_pace(recorded, running_pace_min_per_mi=15, min_gait_stretch_s=10)
    assert split.running_mi == 0


def test_brief_running_inside_hike_counts_as_hiking():
    is_running = np.array([False] * 20 + [True] * 3 + [False] * 20)
    result = ignore_brief_gait_changes(is_running, track([0] * len(is_running)), min_stretch_s=60)
    assert not result.any()


def test_brief_hiking_inside_run_counts_as_running():
    is_running = np.array([True] * 20 + [False] * 3 + [True] * 20)
    result = ignore_brief_gait_changes(is_running, track([0] * len(is_running)), min_stretch_s=60)
    assert result.all()


def test_stumble_is_absorbed_before_judging_the_run_around_it():
    # Each half of the run alone is under a minute, but with the stumble absorbed the run is 85 s.
    is_running = np.array([False] * 20 + [True] * 8 + [False] + [True] * 8 + [False] * 20)
    result = ignore_brief_gait_changes(is_running, track([0] * len(is_running)), min_stretch_s=60)
    np.testing.assert_array_equal(result, [False] * 20 + [True] * 17 + [False] * 20)


def test_sustained_gaits_are_kept():
    is_running = np.array([False] * 20 + [True] * 20 + [False] * 20)
    result = ignore_brief_gait_changes(is_running, track([0] * len(is_running)), min_stretch_s=60)
    np.testing.assert_array_equal(result, is_running)


def test_slow_running_cadence_counts_as_hiking():
    # 10 m per 5 s is 13.4 min/mi; 2 m per 5 s is 67 min/mi.
    is_running = np.array([True] * 4)
    fast = slow_running_as_hiking(is_running, track([0] * 4, step_m=10), max_running_pace_min_per_mi=20)
    slow = slow_running_as_hiking(is_running, track([0] * 4, step_m=2), max_running_pace_min_per_mi=20)
    assert fast.all()
    assert not slow.any()


def test_pace_is_judged_over_the_whole_stretch():
    # One slow step inside a fast stretch doesn't make that step hiking.
    recorded = track([0] * 5)
    recorded.step_m[2] = 1.0
    result = slow_running_as_hiking(np.array([True] * 5), recorded, max_running_pace_min_per_mi=20)
    assert result.all()


def test_stationary_running_cadence_counts_as_hiking():
    result = slow_running_as_hiking(np.array([True] * 3), track([0] * 3, step_m=0), max_running_pace_min_per_mi=20)
    assert not result.any()


def test_split_by_gait_needs_running_cadence_and_pace():
    # Hike, running cadence at a hiking pace, hike, then a run.
    recorded = track([110] * 20 + [170] * 10 + [110] * 10 + [170] * 20)
    recorded.step_m[20:30] = 2.0
    split = split_by_gait(recorded, running_threshold_steps_per_min=140, max_running_pace_min_per_mi=20)
    assert split.running_mi == pytest.approx(200 / M_PER_MI)
    assert split.hiking_mi == pytest.approx(320 / M_PER_MI)


def test_split_by_gait_uses_threshold():
    split = split_by_gait(track([110] * 30 + [170] * 20), running_threshold_steps_per_min=140)
    assert split.running_mi == pytest.approx(200 / M_PER_MI)
    assert split.hiking_mi == pytest.approx(300 / M_PER_MI)
