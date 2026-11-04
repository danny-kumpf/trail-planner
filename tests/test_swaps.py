import json
from dataclasses import asdict

import pytest

from trail_planner import app as app_module
from trail_planner.planner import Plan, PlanRequest
from trail_planner.swaps import SwapRecommendation, swap_options
from trail_planner.trails import Trail


def trail(name: str, vert_ft: float, runnable_mi: float = 1) -> Trail:
    return Trail(name, distance_mi=5, vert_ft=vert_ft, runnable_mi=runnable_mi)


BASE = trail("base", 2000)
CURRENT = trail("current", 2000)
# Swapping out CURRENT, each of these lands the plan's vert this far from 4000 ft.
CATALOG = [
    BASE,  # 0%, but repeats BASE
    CURRENT,
    trail("meets_high", 2050),  # +1.25%
    trail("meets_low", 1950),  # -1.25%
    trail("near_1", 2200),  # +5%
    trail("near_2", 1700),  # -7.5%
    trail("near_3", 2400),  # +10%
    trail("near_4", 1500),  # -12.5%
    trail("dry", 2000, runnable_mi=0),  # 0%, but too little runnable
    trail("excluded", 2000),  # 0%, but excluded
]
PLAN = Plan((BASE, CURRENT), target_vert_ft=4000)


def request(**overrides) -> PlanRequest:
    fields = dict(
        target_vert_ft=4000,
        target_running_mi=2,
        min_outings=1,
        max_outings=4,
        vert_tolerance_frac=0.03,
        allow_repeats=False,
        excluded_names=frozenset({"excluded"}),
    )
    return PlanRequest(**(fields | overrides))


def recommendations(req: PlanRequest) -> dict[str, SwapRecommendation | None]:
    return {option.trail.name: option.recommendation for option in swap_options(PLAN, 1, CATALOG, req)}


def test_closest_swaps_fill_out_the_ones_that_meet_criteria():
    assert recommendations(request()) == {
        "base": None,
        "meets_high": SwapRecommendation.MEETS_CRITERIA,
        "meets_low": SwapRecommendation.MEETS_CRITERIA,
        "near_1": SwapRecommendation.CLOSEST,
        "near_2": SwapRecommendation.CLOSEST,
        "near_3": SwapRecommendation.CLOSEST,
        "near_4": None,
        "dry": None,
        "excluded": None,
    }


def test_repeat_is_recommended_when_repeats_allowed():
    recommended = recommendations(request(allow_repeats=True))
    assert recommended["base"] == SwapRecommendation.MEETS_CRITERIA
    assert [name for name, r in recommended.items() if r == SwapRecommendation.CLOSEST] == ["near_1", "near_2"]


def test_every_swap_meeting_criteria_is_recommended_even_past_five():
    recommended = recommendations(request(vert_tolerance_frac=0.2))
    assert [name for name, r in recommended.items() if r == SwapRecommendation.MEETS_CRITERIA] == [
        "meets_high", "meets_low", "near_1", "near_2", "near_3", "near_4",
    ]
    assert SwapRecommendation.CLOSEST not in recommended.values()


def test_swap_replaces_only_its_outing():
    options = {option.trail.name: option for option in swap_options(PLAN, 1, CATALOG, request())}
    assert "current" not in options
    swapped_plan = options["near_1"].plan
    assert [t.name for t in swapped_plan.trails] == ["base", "near_1"]
    assert swapped_plan.total_vert_ft == 4200
    assert swapped_plan.vert_error_frac == pytest.approx(0.05)


@pytest.mark.parametrize("outing_index", [-1, 2])
def test_swapping_a_missing_outing_rejected(outing_index):
    with pytest.raises(ValueError):
        swap_options(PLAN, outing_index, CATALOG, request())


SETTINGS = {
    "target_vert_ft": 4000,
    "target_running_mi": 2,
    "min_outings": 1,
    "max_outings": 4,
    "vert_tolerance_pct": 3,
    "allow_repeats": False,
    "required_trails": [],
    "excluded_trails": ["excluded"],
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    trails_path = tmp_path / "available_trails.json"
    entries = {t.name: {"from_gpx": {k: v for k, v in asdict(t).items() if k != "name"}, "manual": {}} for t in CATALOG}
    trails_path.write_text(json.dumps(entries))
    monkeypatch.setitem(app_module.app.config, "TRAILS_PATH", trails_path)
    return app_module.app.test_client()


def post_swap(client, outing_index, trails=(BASE, CURRENT)):
    return client.post(
        "/api/plan/swap-options",
        json={"request": SETTINGS, "trails": [asdict(t) for t in trails], "outing_index": outing_index},
    )


def test_swap_options_endpoint(client):
    response = post_swap(client, 1)
    assert response.status_code == 200
    options = {option["trail"]["name"]: option for option in response.get_json()["options"]}
    assert options["meets_high"]["recommendation"] == "meets_criteria"
    assert options["near_1"]["recommendation"] == "closest"
    assert options["dry"]["recommendation"] is None
    assert [t["name"] for t in options["near_1"]["plan"]["trails"]] == ["base", "near_1"]
    assert options["near_1"]["plan"]["vert_error_frac"] == pytest.approx(0.05)


def test_swap_options_use_the_plans_trails_as_sent(client):
    # A saved plan keeps the stats its trails had when saved, which the swapped plan should keep too.
    old_base = trail("base", 1000)
    options = {o["trail"]["name"]: o for o in post_swap(client, 1, trails=(old_base, CURRENT)).get_json()["options"]}
    assert options["meets_high"]["plan"]["total_vert_ft"] == 3050


@pytest.mark.parametrize(
    "response",
    [
        lambda client: post_swap(client, 5),
        lambda client: post_swap(client, "first"),
        lambda client: client.post("/api/plan/swap-options", json={"request": SETTINGS, "trails": "base", "outing_index": 0}),
        lambda client: client.post("/api/plan/swap-options", json=[]),
    ],
)
def test_bad_swap_requests_rejected(client, response):
    assert response(client).status_code == 400
