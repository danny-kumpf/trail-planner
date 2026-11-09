import json
import random
from collections import OrderedDict
from dataclasses import asdict

import pytest

import trail_planner.app as app_module
from trail_planner.planner import (
    NoCombinations,
    NoPlanInTolerance,
    NotEnoughRunnable,
    Plan,
    PlanRequest,
    PlansFound,
    shuffle_plans,
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


def plan_names(plan: Plan) -> tuple[str, ...]:
    return tuple(sorted(t.name for t in plan.trails))


def shuffled_names(trails: list[Trail], req: PlanRequest, seed: int = 0) -> list[tuple[str, ...]]:
    result = shuffle_plans(trails, req, random.Random(seed))
    assert isinstance(result, PlansFound)
    return [plan_names(p) for p in result.plans]


def test_found_plans_are_within_tolerance_and_have_enough_runnable():
    result = shuffle_plans(TRAILS, request(target_running_mi=3, vert_tolerance_frac=0.10), random.Random(0))
    assert isinstance(result, PlansFound)
    assert result.plans
    for plan in result.plans:
        assert abs(plan.vert_error_frac) <= 0.10
        assert plan.total_runnable_mi >= 3


def test_shuffle_has_every_acceptable_plan_once():
    # Within 10% of 4000 ft (3600-4400): {b,c}=4000, {a,b,d}=3700, {c,d}=3700.
    names = shuffled_names(TRAILS, request(vert_tolerance_frac=0.10))
    assert sorted(names) == [("a", "b", "d"), ("b", "c"), ("c", "d")]


def test_shuffle_order_varies():
    req = request(vert_tolerance_frac=0.10)
    assert len({tuple(shuffled_names(TRAILS, req, seed)) for seed in range(50)}) > 1


def test_each_next_plans_outing_count_is_drawn_evenly():
    # One single-outing plan (solo) and ten two-outing plans (pairs of the 2000 ft trails). The
    # first plan should be the single outing about half the time, not 1 time in 11.
    trails = [Trail("solo", 1, vert_ft=4000, runnable_mi=0)]
    trails += [Trail(str(k), 1, vert_ft=2000, runnable_mi=0) for k in range(5)]
    req = request(max_outings=2, vert_tolerance_frac=0.0)
    first_counts = [len(shuffled_names(trails, req, seed)[0]) for seed in range(400)]
    assert 150 < first_counts.count(1) < 250


def test_repeats_only_when_allowed():
    trails = [Trail("x", 1, vert_ft=1000, runnable_mi=0)]
    assert isinstance(shuffle_plans(trails, request(target_vert_ft=3000), random.Random(0)), NoPlanInTolerance)
    assert shuffled_names(trails, request(target_vert_ft=3000, allow_repeats=True)) == [("x", "x", "x")]


def test_no_plan_in_tolerance_reports_closest():
    result = shuffle_plans(TRAILS, request(target_vert_ft=10000), random.Random(0))
    assert isinstance(result, NoPlanInTolerance)
    assert result.closest.total_vert_ft == 6200


def test_recommended_tolerance_admits_ten_plans():
    # Single outings missing a 10,000 ft target by 1%, 2%, ..., 12%.
    trails = [Trail(str(k), 1, vert_ft=10000 + 100 * k, runnable_mi=0) for k in range(1, 13)]
    req = request(target_vert_ft=10000, max_outings=1, vert_tolerance_frac=0.005)
    result = shuffle_plans(trails, req, random.Random(0))
    assert isinstance(result, NoPlanInTolerance)
    assert result.recommendation.tolerance_frac == pytest.approx(0.10)
    assert result.recommendation.num_plans == 10


def test_recommended_tolerance_rounds_up_to_half_percent():
    # Single outings missing a 10,000 ft target by 1.2%, 2.4%, ..., 14.4%; the 10th misses by 12%.
    trails = [Trail(str(k), 1, vert_ft=10000 + 120 * k, runnable_mi=0) for k in range(1, 13)]
    result = shuffle_plans(trails, request(target_vert_ft=10000, max_outings=1, vert_tolerance_frac=0.005), random.Random(0))
    assert result.recommendation.tolerance_frac == pytest.approx(0.12)
    trails = [Trail(str(k), 1, vert_ft=10000 + 121 * k, runnable_mi=0) for k in range(1, 13)]
    result = shuffle_plans(trails, request(target_vert_ft=10000, max_outings=1, vert_tolerance_frac=0.005), random.Random(0))
    assert result.recommendation.tolerance_frac == pytest.approx(0.125)


def test_recommended_tolerance_with_fewer_than_ten_plans_admits_all():
    # Four single-trail plans; the worst (1000 ft) misses 10,000 ft by 90%.
    result = shuffle_plans(TRAILS, request(target_vert_ft=10000, max_outings=1), random.Random(0))
    assert result.recommendation.tolerance_frac == pytest.approx(0.90)
    assert result.recommendation.num_plans == 4


def test_closest_plan_meets_running_target():
    result = shuffle_plans(TRAILS, request(target_vert_ft=1100, target_running_mi=2), random.Random(0))
    assert isinstance(result, NoPlanInTolerance)
    assert result.closest.total_runnable_mi >= 2
    assert result.closest.total_vert_ft == 1500


def test_not_enough_runnable():
    result = shuffle_plans(TRAILS, request(target_running_mi=100), random.Random(0))
    assert result == NotEnoughRunnable(max_runnable_mi=6.5)


def test_no_combinations():
    result = shuffle_plans(TRAILS, request(min_outings=5, max_outings=6), random.Random(0))
    assert isinstance(result, NoCombinations)


def test_required_trail_is_in_every_plan():
    # Within 10% of 4000 ft: {b,c}, {a,b,d}, {c,d}; only the first two include b.
    req = request(vert_tolerance_frac=0.10, required_names=frozenset({"b"}))
    assert sorted(shuffled_names(TRAILS, req)) == [("a", "b", "d"), ("b", "c")]


def test_excluded_trail_is_in_no_plan():
    req = request(vert_tolerance_frac=0.10, excluded_names=frozenset({"b"}))
    assert shuffled_names(TRAILS, req) == [("c", "d")]


def test_required_trail_can_repeat_when_repeats_allowed():
    trails = [Trail("x", 1, vert_ft=1000, runnable_mi=0), Trail("y", 1, vert_ft=2000, runnable_mi=0)]
    req = request(target_vert_ft=3000, max_outings=3, vert_tolerance_frac=0.0, allow_repeats=True,
                  required_names=frozenset({"x"}))
    assert sorted(shuffled_names(trails, req)) == [("x", "x", "x"), ("x", "y")]


def test_required_trail_isnt_repeated_without_repeats():
    trails = [Trail("x", 1, vert_ft=1000, runnable_mi=0)]
    req = request(target_vert_ft=2000, required_names=frozenset({"x"}))
    assert isinstance(shuffle_plans(trails, req, random.Random(0)), NoPlanInTolerance)


def test_closest_plan_respects_required_and_excluded():
    req = request(target_vert_ft=10000, required_names=frozenset({"a"}), excluded_names=frozenset({"c"}))
    result = shuffle_plans(TRAILS, req, random.Random(0))
    assert isinstance(result, NoPlanInTolerance)
    assert plan_names(result.closest) == ("a", "b", "d")


def test_excluding_too_many_leaves_no_combinations():
    req = request(min_outings=3, max_outings=4, excluded_names=frozenset({"a", "b"}))
    assert isinstance(shuffle_plans(TRAILS, req, random.Random(0)), NoCombinations)


def test_unknown_required_trail_rejected():
    with pytest.raises(ValueError):
        shuffle_plans(TRAILS, request(required_names=frozenset({"nope"})), random.Random(0))


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


SETTINGS = {
    "target_vert_ft": 4000,
    "target_running_mi": 0,
    "min_outings": 1,
    "max_outings": 4,
    "vert_tolerance_pct": 10,
    "allow_repeats": False,
    "required_trails": [],
    "excluded_trails": [],
}


def write_trails(path, trails: list[Trail]) -> None:
    entries = {t.name: {"from_gpx": {k: v for k, v in asdict(t).items() if k != "name"}, "manual": {}} for t in trails}
    path.write_text(json.dumps(entries))


@pytest.fixture
def trails_path(tmp_path, monkeypatch):
    path = tmp_path / "available_trails.json"
    write_trails(path, TRAILS)
    monkeypatch.setitem(app_module.app.config, "TRAILS_PATH", path)
    monkeypatch.setattr(app_module, "shuffled_plans", OrderedDict())
    return path


@pytest.fixture
def client(trails_path):
    return app_module.app.test_client()


def post_plan(client, plan_index, **settings):
    return client.post("/api/plan", json=SETTINGS | settings | {"plan_index": plan_index})


def test_plan_endpoint_steps_through_one_shuffled_order(client):
    first = post_plan(client, None).get_json()
    assert first["status"] == "found"
    assert (first["plan_index"], first["num_plans"]) == (0, 3)
    shown = [first["plan"]]
    for plan_index in (1, 2):
        result = post_plan(client, plan_index).get_json()
        assert (result["plan_index"], result["num_plans"]) == (plan_index, 3)
        shown.append(result["plan"])
    # {b,c}, {a,b,d} and {c,d}, each once.
    names = sorted(tuple(sorted(t["name"] for t in p["trails"])) for p in shown)
    assert names == [("a", "b", "d"), ("b", "c"), ("c", "d")]


def test_plan_endpoint_shuffles_anew_when_the_trails_change(client, trails_path):
    post_plan(client, None)
    write_trails(trails_path, TRAILS + [Trail("e", 1, vert_ft=4000, runnable_mi=0)])
    result = post_plan(client, 1).get_json()
    assert (result["plan_index"], result["num_plans"]) == (0, 4)


def test_plan_endpoint_keeps_orders_apart_by_settings(client):
    post_plan(client, None)
    result = post_plan(client, 1, excluded_trails=["b"]).get_json()
    assert (result["plan_index"], result["num_plans"]) == (0, 1)


def test_plan_endpoint_explains_no_plan(client):
    result = post_plan(client, None, target_running_mi=100).get_json()
    assert result == {"status": "not_enough_runnable", "max_runnable_mi": 6.5}


@pytest.mark.parametrize("plan_index", [3, -1, "1", True])
def test_bad_plan_index_rejected(client, plan_index):
    post_plan(client, None)
    assert post_plan(client, plan_index).status_code == 400
