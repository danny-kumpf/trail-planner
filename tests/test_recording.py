from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from trail_planner.recording import Recording, moving_time_s, only_samples_with_position, read_recording

EXPORT_ACTIVITIES = Path(__file__).resolve().parent / "data" / "strava_export" / "activities"
FIT_HIKE = EXPORT_ACTIVITIES / "21553533855.fit.gz"
FIT_WORKOUT_WITHOUT_GPS = EXPORT_ACTIVITIES / "20878058526.fit.gz"
START = datetime(2026, 8, 18, 16, 0, tzinfo=timezone.utc)
M_PER_DEG_LAT = 111_195.0


def due_north(step_m: list[float], step_s: float = 1.0, **channels) -> Recording:
    """A recording heading north, moving `step_m[i]` in step i."""
    along_m = np.concatenate([[0.0], np.cumsum(step_m)])
    return Recording(
        start=START,
        local_start=None,
        time_s=np.arange(len(along_m)) * step_s,
        latitude_deg=40 + along_m / M_PER_DEG_LAT,
        longitude_deg=np.full(len(along_m), -105.0),
        **channels,
    )


def test_fit_hike():
    recording = read_recording(FIT_HIKE)
    assert recording.start == datetime(2026, 9, 30, 18, 48, 20, tzinfo=timezone.utc)
    # The watch's local time: Mountain Daylight Time.
    assert recording.local_start == datetime(2026, 9, 30, 12, 48, 20)
    # Semicircles converted to degrees: Boulder, CO.
    assert 39.9 < np.nanmean(recording.latitude_deg) < 40.1
    assert -105.4 < np.nanmean(recording.longitude_deg) < -105.2
    assert 1600 < np.nanmean(recording.elevation_m) < 2500
    # Cadence in strides per minute (0 while standing), including the fractional part.
    assert 50 < np.nanmax(recording.cadence_strides_per_min) < 100
    assert (recording.cadence_strides_per_min % 1 == 0.5).any()
    assert recording.heart_rate_bpm is not None


def test_fit_workout_without_gps():
    recording = read_recording(FIT_WORKOUT_WITHOUT_GPS)
    assert recording.latitude_deg is None
    assert recording.elevation_m is None
    assert recording.cadence_strides_per_min is None
    assert recording.heart_rate_bpm is not None


def test_samples_before_gps_lock_are_dropped():
    recording = due_north([10.0] * 9)
    latitude_deg = recording.latitude_deg.copy()
    latitude_deg[:2] = np.nan
    kept = only_samples_with_position(Recording(**{**recording.__dict__, "latitude_deg": latitude_deg}))
    assert kept.num_samples == 8
    assert not np.isnan(kept.latitude_deg).any()


def test_stray_positions_are_ignored():
    recording = due_north([10.0] * 9)
    latitude_deg = np.full(recording.num_samples, np.nan)
    latitude_deg[0] = 40.0
    kept = only_samples_with_position(Recording(**{**recording.__dict__, "latitude_deg": latitude_deg}))
    assert kept.latitude_deg is None
    assert kept.num_samples == 10


def test_without_positions_distance_comes_from_the_device():
    recording = Recording(
        start=START,
        local_start=None,
        time_s=np.arange(5.0),
        device_distance_m=np.array([0.0, 3.0, np.nan, 9.0, 12.0]),
    )
    np.testing.assert_allclose(recording.step_m(), [3, 0, 6, 3])


def test_moving_time_leaves_out_stops():
    # 10 s walking, 20 s standing (GPS drifting 0.1 m per s), 10 s walking.
    recording = due_north([1.2] * 10 + [0.1] * 20 + [1.2] * 10)
    assert moving_time_s(recording, min_moving_speed_m_per_s=0.2) == 20


def test_moving_time_of_an_activity_that_doesnt_travel_is_all_of_it():
    recording = Recording(start=START, local_start=None, time_s=np.arange(0.0, 600.0, 5.0))
    assert moving_time_s(recording) == 595


def test_cropping_keeps_the_part_strava_kept():
    recording = due_north([1.0] * 100)
    recording = Recording(**{**recording.__dict__, "local_start": datetime(2026, 8, 18, 10, 0)})
    cropped = recording.between(START + timedelta(seconds=10), START + timedelta(seconds=30))
    assert cropped.num_samples == 21
    assert cropped.time_s[0] == 0
    assert cropped.step_m().sum() == pytest.approx(20, rel=0.01)
    assert cropped.local_start == datetime(2026, 8, 18, 10, 0, 10)
