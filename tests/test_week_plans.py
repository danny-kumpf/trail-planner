import json
from datetime import date

import pytest

from trail_planner import app as app_module
from trail_planner.planner import Plan, PlanRequest
from trail_planner.trails import Trail
from trail_planner.week_plans import (
    WeekAlreadyPlanned,
    WeekPlan,
    load_week_plan,
    planned_week_starts,
    save_week_plan,
)

WEEK_START = date(2026, 10, 17)
REQUEST = PlanRequest(
    target_vert_ft=4000,
    target_running_mi=2,
    min_outings=1,
    max_outings=4,
    vert_tolerance_frac=0.03,
    allow_repeats=True,
    required_names=frozenset({"a"}),
    excluded_names=frozenset({"c", "b"}),
)
A = Trail("a", distance_mi=5, vert_ft=2000, runnable_mi=1)
D = Trail("d", distance_mi=4, vert_ft=1000, runnable_mi=1.5)


def week_plan(week_start=WEEK_START, trails=(A, D, D)) -> WeekPlan:
    return WeekPlan(week_start, REQUEST, Plan(trails, REQUEST.target_vert_ft))


def test_saved_plan_loads_back(tmp_path):
    path = tmp_path / "week_plans.json"
    save_week_plan(week_plan(), replace_existing=False, path=path)
    assert load_week_plan(WEEK_START, path) == week_plan()


def test_unplanned_week_has_no_plan(tmp_path):
    path = tmp_path / "week_plans.json"
    assert load_week_plan(WEEK_START, path) is None
    save_week_plan(week_plan(), replace_existing=False, path=path)
    assert load_week_plan(date(2026, 10, 24), path) is None


def test_planned_week_starts_are_oldest_first(tmp_path):
    path = tmp_path / "week_plans.json"
    assert planned_week_starts(path) == []
    save_week_plan(week_plan(date(2026, 10, 17)), replace_existing=False, path=path)
    save_week_plan(week_plan(date(2026, 10, 3)), replace_existing=False, path=path)
    assert planned_week_starts(path) == [date(2026, 10, 3), date(2026, 10, 17)]


def test_saving_over_a_plan_needs_replace_existing(tmp_path):
    path = tmp_path / "week_plans.json"
    save_week_plan(week_plan(), replace_existing=False, path=path)
    with pytest.raises(WeekAlreadyPlanned):
        save_week_plan(week_plan(trails=(A, A)), replace_existing=False, path=path)
    assert load_week_plan(WEEK_START, path) == week_plan()
    save_week_plan(week_plan(trails=(A, A)), replace_existing=True, path=path)
    assert load_week_plan(WEEK_START, path) == week_plan(trails=(A, A))


# ---- The API ----

CATALOG = {
    "a": {"from_gpx": {"distance_mi": 5, "vert_ft": 2000, "runnable_mi": 1}, "manual": {}},
    "d": {"from_gpx": {"distance_mi": 4, "vert_ft": 1000, "runnable_mi": 1.5}, "manual": {}},
}
SETTINGS = {
    "target_vert_ft": 4000,
    "target_running_mi": 2,
    "min_outings": 1,
    "max_outings": 4,
    "vert_tolerance_pct": 3,
    "allow_repeats": True,
    "required_trails": ["a"],
    "excluded_trails": [],
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    trails_path = tmp_path / "available_trails.json"
    trails_path.write_text(json.dumps(CATALOG))
    monkeypatch.setitem(app_module.app.config, "TRAILS_PATH", trails_path)
    monkeypatch.setitem(app_module.app.config, "WEEK_PLANS_PATH", tmp_path / "week_plans.json")
    return app_module.app.test_client()


def put_plan(client, trail_names, replace_existing=False, week_start="2026-10-17"):
    return client.put(
        f"/api/week-plans/{week_start}",
        json={"request": SETTINGS, "trail_names": trail_names, "replace_existing": replace_existing},
    )


def test_saved_plan_comes_back_with_its_settings(client):
    assert put_plan(client, ["a", "d", "d"]).status_code == 200
    saved = client.get("/api/week-plans/2026-10-17").get_json()
    assert saved["request"] == SETTINGS
    assert [t["name"] for t in saved["plan"]["trails"]] == ["a", "d", "d"]
    assert saved["plan"]["total_vert_ft"] == 4000
    assert client.get("/api/week-plans").get_json() == ["2026-10-17"]


def test_saving_over_a_plan_is_refused_unless_replacing(client):
    put_plan(client, ["a", "d", "d"])
    assert put_plan(client, ["a", "a"]).status_code == 409
    assert put_plan(client, ["a", "a"], replace_existing=True).status_code == 200
    saved = client.get("/api/week-plans/2026-10-17").get_json()
    assert [t["name"] for t in saved["plan"]["trails"]] == ["a", "a"]


def test_unplanned_week_is_not_found(client):
    assert client.get("/api/week-plans/2026-10-17").status_code == 404


@pytest.mark.parametrize(
    "response",
    [
        lambda client: put_plan(client, ["a", "nope"]),
        lambda client: put_plan(client, "a"),
        lambda client: put_plan(client, ["a"], week_start="not-a-date"),
        lambda client: client.get("/api/week-plans/not-a-date"),
    ],
)
def test_bad_requests_rejected(client, response):
    assert response(client).status_code == 400
