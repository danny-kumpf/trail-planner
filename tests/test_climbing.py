from datetime import datetime, timezone

import numpy as np
import pytest

from trail_planner.climb_blocks import BLOCK_S, HR_LAG_S, MIN_CLIMB_S, ClimbBlock, climbs_in
from trail_planner.climb_model import BlockColumns, ClimbConditions, at_conditions_ft_per_hr, fit_climb_model
from trail_planner.climb_signals import ClimbSignals, climb_signals
from trail_planner.recording import Recording
from trail_planner.trail_stats import FT_PER_M

START = datetime(2026, 10, 1, 15, 0, tzinfo=timezone.utc)
M_PER_DEG_LAT = 111_195.0


def heading_north(segments: list[tuple[float, float, float]], heart_rate_bpm=None) -> Recording:
    """A recording sampled every second, through segments of (duration_s, speed_m_per_s, grade_frac)."""
    step_m = np.concatenate([np.full(int(duration_s), speed) for duration_s, speed, _ in segments])
    step_rise_m = np.concatenate([np.full(int(duration_s), speed * grade) for duration_s, speed, grade in segments])
    along_m = np.concatenate([[0.0], np.cumsum(step_m)])
    return Recording(
        start=START,
        local_start=None,
        time_s=np.arange(len(along_m), dtype=float),
        latitude_deg=40 + along_m / M_PER_DEG_LAT,
        longitude_deg=np.full(len(along_m), -105.0),
        elevation_m=1800 + np.concatenate([[0.0], np.cumsum(step_rise_m)]),
        heart_rate_bpm=heart_rate_bpm,
    )


def test_steady_climb_has_its_rate_and_grade():
    signals = climb_signals(heading_north([(600, 1.0, 0.0), (1800, 0.6, 0.15), (600, 1.0, 0.0)]))
    mid_climb = (signals.time_s > 900) & (signals.time_s < 2100)
    expected_ft_per_hr = 0.6 * 0.15 * FT_PER_M * 3600
    assert np.allclose(signals.vert_rate_ft_per_hr[mid_climb], expected_ft_per_hr, rtol=0.01)
    assert np.allclose(signals.grade_frac[mid_climb], 0.15, atol=0.005)
    assert signals.heart_rate_bpm is None
    assert signals.heart_rate_misread is None


def test_a_gps_jump_leaves_the_rate_unknown_rather_than_fast():
    recording = heading_north([(1200, 1.0, 0.0)])
    elevation_m = recording.elevation_m.copy()
    elevation_m[600:] += 60  # A minute of climbing landed in one second.
    signals = climb_signals(Recording(**{**recording.__dict__, "elevation_m": elevation_m}))
    assert signals.rate_is_implausible[600]
    assert np.isnan(signals.vert_rate_ft_per_hr[600])
    assert not signals.rate_is_implausible[:400].any()


def test_heart_rate_reading_low_until_it_locks_on_isnt_trusted():
    heart_rate_bpm = np.where(np.arange(1801) < 600, 70.0, 140.0)
    signals = climb_signals(heading_north([(1800, 1.0, 0.0)], heart_rate_bpm))
    assert signals.heart_rate_misread[:600].all()
    assert not signals.heart_rate_misread[600:].any()
    assert np.isnan(signals.heart_rate_bpm[:600]).all()
    assert (signals.heart_rate_bpm[600:] == 140).all()


def test_misreading_reaches_back_only_until_a_lower_effort_explains_the_reading():
    # An easy, flat 10 minutes at a real 100 bpm, then a steep climb read at 120 until the monitor
    # locks on to 160 fifteen minutes in.
    lock_on_s = 900
    heart_rate_bpm = np.where(np.arange(2401) < 600, 100.0, np.where(np.arange(2401) < lock_on_s, 120.0, 160.0))
    signals = climb_signals(heading_north([(600, 1.0, 0.0), (1800, 0.6, 0.3)], heart_rate_bpm))
    assert not signals.heart_rate_misread[:550].any()
    assert signals.heart_rate_misread[650:lock_on_s].all()
    assert not signals.heart_rate_misread[lock_on_s:].any()


def test_a_jump_from_setting_off_is_real():
    heart_rate_bpm = np.where(np.arange(1801) < 600, 70.0, 100.0)
    signals = climb_signals(heading_north([(600, 0.0, 0.0), (1200, 1.0, 0.0)], heart_rate_bpm))
    assert not signals.heart_rate_misread.any()


def test_a_jump_from_starting_to_climb_is_real():
    heart_rate_bpm = np.where(np.arange(1801) < 610, 100.0, 140.0)
    signals = climb_signals(heading_north([(600, 1.0, 0.0), (1200, 0.6, 0.3)], heart_rate_bpm))
    assert not signals.heart_rate_misread.any()


def test_too_little_elevation():
    recording = heading_north([(600, 1.0, 0.0)])
    with pytest.raises(ValueError):
        climb_signals(Recording(**{**recording.__dict__, "elevation_m": None}))


def signals_of(vert_rate_ft_per_hr: np.ndarray, grade_frac: float = 0.15, heart_rate_bpm=None) -> ClimbSignals:
    """Signals on a 1 s grid with the given vertical rate, at a constant grade."""
    n = len(vert_rate_ft_per_hr)
    elevation_ft = 6000 + np.cumsum(vert_rate_ft_per_hr) / 3600
    return ClimbSignals(
        time_s=np.arange(n, dtype=float),
        raw_elevation_ft=elevation_ft,
        elevation_ft=elevation_ft,
        vert_rate_ft_per_hr=np.asarray(vert_rate_ft_per_hr, dtype=float),
        rate_is_implausible=np.zeros(n, dtype=bool),
        along_track_m=np.arange(n, dtype=float),
        grade_frac=np.full(n, grade_frac),
        heart_rate_bpm=heart_rate_bpm,
        heart_rate_misread=None if heart_rate_bpm is None else np.zeros(n, dtype=bool),
    )


def test_a_stop_splits_a_climb_and_brief_climbs_arent_modelled():
    long_s, brief_s = 600, int(MIN_CLIMB_S) - 10
    vert_rate = np.concatenate([np.full(long_s, 1500.0), np.full(60, 0.0), np.full(brief_s, 1500.0)])
    analysis = climbs_in(signals_of(vert_rate))
    assert [(c.start, c.stop) for c in analysis.climbs] == [(0, long_s)]
    assert [(c.start, c.stop) for c in analysis.short_climbs] == [(long_s + 60, long_s + 60 + brief_s)]


def test_shallow_terrain_isnt_climbing():
    assert climbs_in(signals_of(np.full(600, 1500.0), grade_frac=0.05)).climbs == []


def test_blocks_cut_each_climb_and_keep_a_long_enough_remainder():
    climb_s = 10 * int(BLOCK_S) + 30
    analysis = climbs_in(signals_of(np.full(climb_s, 1500.0)))
    assert [b.duration_s for b in analysis.blocks] == [BLOCK_S] * 10 + [30.0]
    assert all(b.vert_rate_ft_per_hr == pytest.approx(1500) for b in analysis.blocks)
    assert all(b.grade_frac == pytest.approx(0.15) for b in analysis.blocks)


def test_a_block_has_the_heart_rate_its_effort_produced():
    # Heart rate rises at 990 s, so it was produced by the effort HR_LAG_S earlier: the block 945-990 s.
    rise_s = 990
    heart_rate_bpm = np.where(np.arange(1800) < rise_s, 100.0, 160.0)
    analysis = climbs_in(signals_of(np.full(1800, 1500.0), heart_rate_bpm=heart_rate_bpm))
    by_start = {b.start_s: b.heart_rate_bpm for b in analysis.blocks}
    assert by_start[rise_s - HR_LAG_S] == 160
    assert by_start[rise_s - 2 * HR_LAG_S] == 100
    # The last block's heart rate would come from after the recording ends.
    assert analysis.blocks[-1].heart_rate_bpm is None


def blocks_following(intercept, per_bpm, per_ft, per_frac, n=40, seed=0, extra_ft_per_hr=0.0) -> list[ClimbBlock]:
    rng = np.random.default_rng(seed)
    blocks = []
    for _ in range(n):
        heart_rate_bpm = rng.uniform(110, 170)
        elevation_ft = rng.uniform(5000, 13000)
        grade_frac = rng.uniform(0.1, 0.35)
        vert_rate = intercept + per_bpm * heart_rate_bpm + per_ft * elevation_ft + per_frac * grade_frac
        blocks.append(ClimbBlock(0.0, rng.uniform(20, 45), vert_rate + extra_ft_per_hr, elevation_ft, grade_frac, heart_rate_bpm))
    return blocks


def test_fit_recovers_a_straight_line_relationship():
    model = fit_climb_model(blocks_following(-500, 18.0, -0.05, 200.0))
    assert model.hr_slope_ft_per_hr_per_bpm == pytest.approx(18.0)
    assert model.elevation_slope_ft_per_hr_per_ft == pytest.approx(-0.05)
    assert model.grade_slope_ft_per_hr_per_frac == pytest.approx(200.0)
    assert model.r_squared == pytest.approx(1.0)
    assert model.num_blocks == 40


def test_blocks_moved_to_conditions_keep_their_departure_from_the_model():
    model = fit_climb_model(blocks_following(-500, 18.0, -0.05, 200.0))
    faster = BlockColumns.of(blocks_following(-500, 18.0, -0.05, 200.0, seed=1, extra_ft_per_hr=100))
    conditions = ClimbConditions(heart_rate_bpm=150, elevation_ft=8000, grade_frac=0.15)
    assert np.allclose(at_conditions_ft_per_hr(model, faster, conditions), model.predict_at(conditions) + 100)


def test_fit_needs_enough_blocks_with_heart_rate():
    blocks = blocks_following(0, 10, 0, 0, n=5)
    with pytest.raises(ValueError):
        fit_climb_model(blocks[:3])
    with pytest.raises(ValueError):
        fit_climb_model([*blocks[:4], ClimbBlock(0.0, 45.0, 1000.0, 6000.0, 0.2, None)])
