import random

import pytest

from trail_planner.planner import (
    NoCombinations,
    NoPlanInTolerance,
    NotEnoughRunnable,
    Plan,
    PlanFound,
    PlanRequest,
    shuffle_plan,
)
from trail_planner.trails import Trail

TRAILS = [
    Trail("a", distance_mi=4, vert_ft=1000, runnable_mi=1.0),
    Trail("b", distance_mi=5, vert_ft=1500, runnable_mi=2.0),
    Trail("c", distance_mi=7, vert_ft=2500, runnable_mi=3.0),
    Trail("d", distance_mi=3, vert_ft=1200, runnable_mi=0.5),
]


def request(**overrides) -> PlanRequest:
    fields = dict(
        target_vert_ft=4000,
        target_running_mi=0,
        min_outings=1,
        max_outings=4,
        vert_tolerance_frac=0.03,
        allow_repeats=False,
    )
    return PlanRequest(**(fields | overrides))


def test_found_plan_is_within_tolerance_and_has_enough_runnable():
    req = request(target_running_mi=3)
    for seed in range(50):
        result = shuffle_plan(TRAILS, req, random.Random(seed))
        assert isinstance(result, PlanFound)
        assert abs(result.plan.vert_error_frac) <= 0.03
        assert result.plan.total_runnable_mi >= 3


def test_shuffle_covers_every_acceptable_plan():
    # Within 10% of 4000 ft (3600-4400): {b,c}=4000, {a,b,d}=3700, {c,d}=3700.
    req = request(vert_tolerance_frac=0.10)
    seen = {
        tuple(t.name for t in shuffle_plan(TRAILS, req, random.Random(seed)).plan.trails) for seed in range(200)
    }
    assert seen == {("b", "c"), ("a", "b", "d"), ("c", "d")}


def test_outing_count_is_rolled_before_the_plan():
    # With repeats, count 1 has one acceptable plan and count 4 has one; each count should be
    # chosen about equally often even though other counts have none.
    trails = [Trail("x", 1, vert_ft=1000, runnable_mi=0), Trail("y", 1, vert_ft=4000, runnable_mi=0)]
    req = request(allow_repeats=True, vert_tolerance_frac=0.0)
    counts = [len(shuffle_plan(trails, req, random.Random(seed)).plan.trails) for seed in range(400)]
    assert set(counts) == {1, 4}
    assert 150 < counts.count(1) < 250


def test_repeats_only_when_allowed():
    trails = [Trail("x", 1, vert_ft=1000, runnable_mi=0)]
    assert isinstance(shuffle_plan(trails, request(target_vert_ft=3000), random.Random(0)), NoPlanInTolerance)
    result = shuffle_plan(trails, request(target_vert_ft=3000, allow_repeats=True), random.Random(0))
    assert isinstance(result, PlanFound)
    assert len(result.plan.trails) == 3


def test_no_plan_in_tolerance_reports_closest():
    result = shuffle_plan(TRAILS, request(target_vert_ft=10000), random.Random(0))
    assert isinstance(result, NoPlanInTolerance)
    assert result.closest.total_vert_ft == 6200


def test_recommended_tolerance_admits_ten_plans():
    # Single outings missing a 10,000 ft target by 1%, 2%, ..., 12%.
    trails = [Trail(str(k), 1, vert_ft=10000 + 100 * k, runnable_mi=0) for k in range(1, 13)]
    req = request(target_vert_ft=10000, max_outings=1, vert_tolerance_frac=0.005)
    result = shuffle_plan(trails, req, random.Random(0))
    assert isinstance(result, NoPlanInTolerance)
    assert result.recommendation.tolerance_frac == pytest.approx(0.10)
    assert result.recommendation.num_plans == 10


def test_recommended_tolerance_rounds_up_to_half_percent():
    # Single outings missing a 10,000 ft target by 1.2%, 2.4%, ..., 14.4%; the 10th misses by 12%.
    trails = [Trail(str(k), 1, vert_ft=10000 + 120 * k, runnable_mi=0) for k in range(1, 13)]
    result = shuffle_plan(trails, request(target_vert_ft=10000, max_outings=1, vert_tolerance_frac=0.005), random.Random(0))
    assert result.recommendation.tolerance_frac == pytest.approx(0.12)
    trails = [Trail(str(k), 1, vert_ft=10000 + 121 * k, runnable_mi=0) for k in range(1, 13)]
    result = shuffle_plan(trails, request(target_vert_ft=10000, max_outings=1, vert_tolerance_frac=0.005), random.Random(0))
    assert result.recommendation.tolerance_frac == pytest.approx(0.125)


def test_recommended_tolerance_with_fewer_than_ten_plans_admits_all():
    # Four single-trail plans; the worst (1000 ft) misses 10,000 ft by 90%.
    result = shuffle_plan(TRAILS, request(target_vert_ft=10000, max_outings=1), random.Random(0))
    assert result.recommendation.tolerance_frac == pytest.approx(0.90)
    assert result.recommendation.num_plans == 4


def test_closest_plan_meets_running_target():
    result = shuffle_plan(TRAILS, request(target_vert_ft=1100, target_running_mi=2), random.Random(0))
    assert isinstance(result, NoPlanInTolerance)
    assert result.closest.total_runnable_mi >= 2
    assert result.closest.total_vert_ft == 1500


def test_not_enough_runnable():
    result = shuffle_plan(TRAILS, request(target_running_mi=100), random.Random(0))
    assert result == NotEnoughRunnable(max_runnable_mi=6.5)


def test_no_combinations():
    result = shuffle_plan(TRAILS, request(min_outings=5, max_outings=6), random.Random(0))
    assert isinstance(result, NoCombinations)


def plan_names(plan: Plan) -> tuple[str, ...]:
    return tuple(sorted(t.name for t in plan.trails))


def test_required_trail_is_in_every_plan():
    # Within 10% of 4000 ft: {b,c}, {a,b,d}, {c,d}; only the first two include b.
    req = request(vert_tolerance_frac=0.10, required_names=frozenset({"b"}))
    seen = {plan_names(shuffle_plan(TRAILS, req, random.Random(seed)).plan) for seed in range(200)}
    assert seen == {("b", "c"), ("a", "b", "d")}


def test_excluded_trail_is_in_no_plan():
    req = request(vert_tolerance_frac=0.10, excluded_names=frozenset({"b"}))
    seen = {plan_names(shuffle_plan(TRAILS, req, random.Random(seed)).plan) for seed in range(200)}
    assert seen == {("c", "d")}


def test_required_trail_can_repeat_when_repeats_allowed():
    trails = [Trail("x", 1, vert_ft=1000, runnable_mi=0), Trail("y", 1, vert_ft=2000, runnable_mi=0)]
    req = request(target_vert_ft=3000, max_outings=3, vert_tolerance_frac=0.0, allow_repeats=True,
                  required_names=frozenset({"x"}))
    seen = {plan_names(shuffle_plan(trails, req, random.Random(seed)).plan) for seed in range(200)}
    assert seen == {("x", "y"), ("x", "x", "x")}


def test_required_trail_isnt_repeated_without_repeats():
    trails = [Trail("x", 1, vert_ft=1000, runnable_mi=0)]
    req = request(target_vert_ft=2000, required_names=frozenset({"x"}))
    assert isinstance(shuffle_plan(trails, req, random.Random(0)), NoPlanInTolerance)


def test_closest_plan_respects_required_and_excluded():
    req = request(target_vert_ft=10000, required_names=frozenset({"a"}), excluded_names=frozenset({"c"}))
    result = shuffle_plan(TRAILS, req, random.Random(0))
    assert isinstance(result, NoPlanInTolerance)
    assert plan_names(result.closest) == ("a", "b", "d")


def test_excluding_too_many_leaves_no_combinations():
    req = request(min_outings=3, max_outings=4, excluded_names=frozenset({"a", "b"}))
    assert isinstance(shuffle_plan(TRAILS, req, random.Random(0)), NoCombinations)


def test_unknown_required_trail_rejected():
    with pytest.raises(ValueError):
        shuffle_plan(TRAILS, request(required_names=frozenset({"nope"})), random.Random(0))


@pytest.mark.parametrize(
    "overrides",
    [dict(target_vert_ft=0), dict(target_running_mi=-1), dict(min_outings=0), dict(min_outings=3, max_outings=2),
     dict(vert_tolerance_frac=-0.01),
     dict(required_names=frozenset({"a"}), excluded_names=frozenset({"a"})),
     dict(max_outings=1, required_names=frozenset({"a", "b"}))],
)
def test_invalid_requests_rejected(overrides):
    with pytest.raises(ValueError):
        request(**overrides)
