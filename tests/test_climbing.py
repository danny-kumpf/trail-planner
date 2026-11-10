from datetime import datetime, timezone

import numpy as np
import pytest

from trail_trainer.climb_blocks import BLOCK_S, CLIMB_FLOOR_GRADE_FRAC, HR_LAG_S, MIN_CLIMB_S, ClimbBlock, climbs_in
from trail_trainer.climb_model import (
    BlockColumns,
    ClimbConditions,
    at_conditions_ft_per_hr,
    fit_climb_model,
    ft_per_hr_per_w_per_kg,
)
from trail_trainer.climb_signals import ClimbSignals, climb_signals
from trail_trainer.recording import Recording
from trail_trainer.trail_stats import FT_PER_M

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


def signals_of(
    vert_rate_ft_per_hr: np.ndarray, grade_frac: float = 0.15, heart_rate_bpm=None, is_running=None
) -> ClimbSignals:
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
        is_running=np.zeros(n, dtype=bool) if is_running is None else is_running,
    )


def test_a_stop_splits_a_climb_and_brief_climbs_arent_modelled():
    long_s, brief_s = 600, int(MIN_CLIMB_S) - 10
    vert_rate = np.concatenate([np.full(long_s, 1500.0), np.full(60, 0.0), np.full(brief_s, 1500.0)])
    analysis = climbs_in(signals_of(vert_rate))
    assert [(c.start, c.stop) for c in analysis.climbs] == [(0, long_s)]
    assert [(c.start, c.stop) for c in analysis.short_climbs] == [(long_s + 60, long_s + 60 + brief_s)]


def test_shallow_terrain_isnt_climbing():
    assert climbs_in(signals_of(np.full(600, 1500.0), grade_frac=CLIMB_FLOOR_GRADE_FRAC - 0.01)).climbs == []


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


def test_a_block_has_the_share_of_it_that_was_run():
    climb_s, run_s = 10 * int(BLOCK_S), int(BLOCK_S) // 3
    is_running = np.arange(climb_s) >= climb_s - run_s
    analysis = climbs_in(signals_of(np.full(climb_s, 1500.0), is_running=is_running))
    assert analysis.blocks[0].running_frac == 0
    assert analysis.blocks[-1].running_frac == pytest.approx(run_s / analysis.blocks[-1].duration_s)


def test_running_is_judged_as_the_training_log_judges_it():
    # Without cadence, pace alone decides: 1 m/s is a hike, 3 m/s a run.
    signals = climb_signals(heading_north([(600, 1.0, 0.15), (600, 3.0, 0.15)]))
    assert not signals.is_running[100:500].any()
    assert signals.is_running[700:1100].all()


def blocks_following(intercept_w_per_kg, per_bpm, per_ft, n=40, seed=0, extra_w_per_kg=0.0) -> list[ClimbBlock]:
    """Blocks at a mix of grades and gaits whose power is a straight line in heart rate and elevation."""
    rng = np.random.default_rng(seed)
    blocks = []
    for _ in range(n):
        heart_rate_bpm = rng.uniform(110, 170)
        elevation_ft = rng.uniform(5000, 13000)
        grade_frac = rng.uniform(0.1, 0.35)
        running_frac = rng.choice([0.0, 0.0, 0.5, 1.0])
        power_w_per_kg = intercept_w_per_kg + per_bpm * heart_rate_bpm + per_ft * elevation_ft + extra_w_per_kg
        blocks.append(
            ClimbBlock(
                start_s=0.0,
                duration_s=rng.uniform(20, 45),
                vert_rate_ft_per_hr=power_w_per_kg * float(ft_per_hr_per_w_per_kg(grade_frac, running_frac)),
                elevation_ft=elevation_ft,
                grade_frac=grade_frac,
                heart_rate_bpm=heart_rate_bpm,
                running_frac=running_frac,
            )
        )
    return blocks


def test_fit_recovers_a_straight_line_relationship_in_power():
    model = fit_climb_model(blocks_following(-2.0, 0.06, -0.0002))
    assert model.intercept_w_per_kg == pytest.approx(-2.0)
    assert model.hr_slope_w_per_kg_per_bpm == pytest.approx(0.06)
    assert model.elevation_slope_w_per_kg_per_ft == pytest.approx(-0.0002)
    assert model.r_squared == pytest.approx(1.0)
    assert model.num_blocks == 40


def test_climbing_rate_follows_the_cost_of_the_grade_and_gait():
    model = fit_climb_model(blocks_following(-2.0, 0.06, -0.0002))
    hiking_ft_per_hr = model.predict_ft_per_hr(150, 8000, [0.10, 0.20], 0.0)
    # The same power climbs faster on steeper ground, and slower running than hiking.
    assert hiking_ft_per_hr[1] > hiking_ft_per_hr[0]
    assert model.predict_ft_per_hr(150, 8000, 0.20, 1.0) < hiking_ft_per_hr[1]


def test_the_slopes_are_the_models_line_at_the_given_grade_and_gait():
    model = fit_climb_model(blocks_following(-2.0, 0.06, -0.0002))
    conditions = ClimbConditions(heart_rate_bpm=150, elevation_ft=8000, grade_frac=0.15, running_frac=0.0)
    one_bpm_more = ClimbConditions(heart_rate_bpm=151, elevation_ft=8000, grade_frac=0.15, running_frac=0.0)
    one_ft_higher = ClimbConditions(heart_rate_bpm=150, elevation_ft=8001, grade_frac=0.15, running_frac=0.0)
    assert model.hr_slope_ft_per_hr_per_bpm(conditions) == pytest.approx(
        model.predict_at(one_bpm_more) - model.predict_at(conditions)
    )
    assert model.elevation_slope_ft_per_hr_per_ft(conditions) == pytest.approx(
        model.predict_at(one_ft_higher) - model.predict_at(conditions)
    )


def test_blocks_moved_to_conditions_keep_their_departure_from_the_model():
    model = fit_climb_model(blocks_following(-2.0, 0.06, -0.0002))
    faster = BlockColumns.of(blocks_following(-2.0, 0.06, -0.0002, seed=1, extra_w_per_kg=0.3))
    conditions = ClimbConditions(heart_rate_bpm=150, elevation_ft=8000, grade_frac=0.15, running_frac=0.0)
    extra_ft_per_hr = 0.3 * float(ft_per_hr_per_w_per_kg(conditions.grade_frac, conditions.running_frac))
    assert np.allclose(at_conditions_ft_per_hr(model, faster, conditions), model.predict_at(conditions) + extra_ft_per_hr)


def test_fit_needs_enough_blocks_with_heart_rate():
    blocks = blocks_following(0.0, 0.05, 0.0, n=5)
    with pytest.raises(ValueError):
        fit_climb_model(blocks[:2])
    no_heart_rate = ClimbBlock(0.0, 45.0, 1000.0, 6000.0, 0.2, heart_rate_bpm=None, running_frac=0.0)
    with pytest.raises(ValueError):
        fit_climb_model([*blocks[:3], no_heart_rate])
