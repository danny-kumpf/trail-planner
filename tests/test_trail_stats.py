import numpy as np
import pytest

from trail_planner.trail_stats import (
    FT_PER_M,
    M_PER_MI,
    ElevationProfile,
    great_circle_steps_m,
    read_profile,
    stats_from_profile,
)


def profile_from_segments(segments: list[tuple[float, float]], spacing_m: float = 1.0) -> ElevationProfile:
    """Piecewise-linear profile from (length_m, grade_frac) segments, sampled every `spacing_m`."""
    along_m = [0.0]
    elevation_m = [2000.0]
    for length_m, grade_frac in segments:
        for _ in range(round(length_m / spacing_m)):
            along_m.append(along_m[-1] + spacing_m)
            elevation_m.append(elevation_m[-1] + grade_frac * spacing_m)
    return ElevationProfile(np.array(along_m), np.array(elevation_m))


def test_steady_climb_gain_and_nothing_runnable():
    profile = profile_from_segments([(2000, 0.20)])
    stats = stats_from_profile(profile)
    assert stats.distance_mi == pytest.approx(2000 / M_PER_MI)
    assert stats.vert_ft == pytest.approx(400 * FT_PER_M, rel=0.05)
    # Edge padding flattens the ends of the smoothed profile slightly, so allow a little runnable there.
    assert stats.runnable_mi < 0.1


def test_out_and_back_counts_only_the_climb():
    profile = profile_from_segments([(3000, 0.15), (3000, -0.15)])
    stats = stats_from_profile(profile)
    assert stats.vert_ft == pytest.approx(450 * FT_PER_M, rel=0.05)


def test_runnable_grade_window():
    # Flat and gentle descent are runnable; steep descent and moderate climb are not.
    profile = profile_from_segments([(2000, 0.0), (2000, -0.05), (2000, -0.20), (2000, 0.08)])
    stats = stats_from_profile(profile)
    assert stats.runnable_mi == pytest.approx(4000 / M_PER_MI, rel=0.05)


def test_smoothing_removes_small_wiggles():
    profile = profile_from_segments([(1000, 0.0)])
    noisy_m = profile.elevation_m + 1.0 * np.sin(np.arange(len(profile.elevation_m)) * 2 * np.pi / 20)
    stats = stats_from_profile(ElevationProfile(profile.along_track_m, noisy_m))
    assert stats.vert_ft < 3


def test_great_circle_step_along_meridian():
    steps_m = great_circle_steps_m(np.array([40.0, 40.001]), np.array([-105.0, -105.0]))
    assert steps_m[0] == pytest.approx(111.2, abs=0.1)


def test_read_profile_drops_repeated_points(tmp_path):
    gpx_path = tmp_path / "t.gpx"
    gpx_path.write_text(
        """<?xml version="1.0"?>
<gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>
<trkpt lat="40.0" lon="-105.0"><ele>1800</ele></trkpt>
<trkpt lat="40.0" lon="-105.0"><ele>1800</ele></trkpt>
<trkpt lat="40.001" lon="-105.0"><ele>1810</ele></trkpt>
</trkseg></trk></gpx>""",
        encoding="utf-8",
    )
    profile = read_profile(gpx_path)
    assert len(profile.along_track_m) == 2
    assert profile.length_m == pytest.approx(111.2, abs=0.1)
