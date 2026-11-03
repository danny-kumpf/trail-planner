import gzip
import json
import shutil
from datetime import datetime, timedelta

import pytest

from test_climbing import blocks_following
from trail_planner import fitness
from trail_planner.app import app
from trail_planner.fitness_plots import model_coverage
from trail_planner.fitness import (
    MIN_RECENT_EFFORTS,
    MODEL_WINDOW,
    Effort,
    FitnessOverview,
    Membership,
    climb_blocks_by_activity,
    comparison_model,
    model_membership,
    vs_model_frac,
)
from trail_planner.training_log import ModelChoice, activity_from_entry, load_activities, set_manual, set_model_choice

FIT_HIKE = "21553533855.fit.gz"
LATEST = datetime(2026, 10, 28, 9, 0)


def effort(activity_id: str, days_before_latest: float, choice: ModelChoice | None = None, blocks=()) -> Effort:
    entry = {
        "recorded": {
            "name": f"Hike {activity_id}",
            "type": "hike",
            "start_local": (LATEST - timedelta(days=days_before_latest)).isoformat(),
            "moving_time_s": 3600,
            "distance_mi": 4.0,
            "vert_ft": 2000,
            "running_mi": 0.0,
            "avg_heart_rate_bpm": 140,
        },
        "manual": {} if choice is None else {"model_choice": choice.value},
    }
    return Effort(activity_from_entry(activity_id, entry), list(blocks))


WINDOW_DAYS = MODEL_WINDOW.days


def test_model_is_fit_from_the_window_before_the_latest_effort():
    days = [0, 1, 2, 3, 4, 5, WINDOW_DAYS - 1, WINDOW_DAYS + 1, 2 * WINDOW_DAYS]
    efforts = [effort(str(i), d) for i, d in enumerate(days)]
    assert set(model_membership(efforts)) == {"0", "1", "2", "3", "4", "5", "6"}


def test_model_is_fit_from_the_latest_few_if_the_window_holds_fewer():
    days = [0] + [WINDOW_DAYS + 10 * k for k in range(1, 7)]
    efforts = [effort(str(i), d) for i, d in enumerate(days)]
    assert set(model_membership(efforts)) == {str(i) for i in range(MIN_RECENT_EFFORTS)}


def test_excluded_efforts_are_left_out_and_dont_set_the_window():
    # The latest effort left in is `anchor` days back, and the window runs back from it.
    anchor = WINDOW_DAYS + 10
    days = [anchor + 5 * k for k in range(6)] + [anchor + WINDOW_DAYS + 20]
    efforts = [effort("latest", 0, ModelChoice.EXCLUDE)] + [effort(str(i), d) for i, d in enumerate(days)]
    membership = model_membership(efforts)
    assert "latest" not in membership
    assert set(membership) == {"0", "1", "2", "3", "4", "5"}


def test_required_efforts_are_in_whatever_their_date():
    efforts = [effort(str(i), days) for i, days in enumerate(range(0, 25, 5))] + [
        effort("old", 400, ModelChoice.REQUIRE)
    ]
    membership = model_membership(efforts)
    assert membership["old"] == Membership.REQUIRED
    assert membership["0"] == Membership.RECENT


def test_an_effort_in_the_model_is_compared_with_the_model_fit_without_it():
    usual = effort("usual", 1, blocks=blocks_following(-2.0, 0.06, -0.0002))
    faster = effort("faster", 0, blocks=blocks_following(-2.0, 0.06, -0.0002, seed=1, extra_w_per_kg=0.6))
    efforts = [faster, usual]
    membership = model_membership(efforts)
    full = fitness.fit_climb_model(fitness.model_blocks(efforts, membership))
    without_faster = comparison_model(faster, efforts, membership, full)
    # Fit on `usual` alone, the model has no trace of the faster effort's extra 0.6 W/kg.
    assert without_faster.predict_w_per_kg(150, 8000) == pytest.approx(-2.0 + 0.06 * 150 - 0.0002 * 8000)
    assert vs_model_frac(faster, without_faster) > vs_model_frac(faster, full) > 0


def heatmap_minutes(panel: dict) -> float:
    [heatmap] = [trace for trace in panel["figure"]["light"]["data"] if trace["type"] == "heatmap"]
    return sum(minutes for row in heatmap["z"] for minutes in row if minutes is not None)


def test_coverage_counts_the_climbing_the_model_is_fit_on():
    usual = effort("usual", 1, blocks=blocks_following(-2.0, 0.06, -0.0002))
    faster = effort("faster", 0, blocks=blocks_following(-2.0, 0.06, -0.0002, seed=1, extra_w_per_kg=0.6))
    efforts = [faster, usual]
    membership = model_membership(efforts)
    model = fitness.fit_climb_model(fitness.model_blocks(efforts, membership))
    overview = FitnessOverview(efforts, membership, model, None, {}, {})

    assert heatmap_minutes(model_coverage(overview)) == pytest.approx((usual.climbing_s + faster.climbing_s) / 60)
    # An effort's page shows the model it's compared with, which leaves it out, and its blocks on top.
    explored = model_coverage(overview, faster)
    assert heatmap_minutes(explored) == pytest.approx(usual.climbing_s / 60)
    assert "this effort's climb blocks" in [trace.get("name") for trace in explored["figure"]["light"]["data"]]


@pytest.fixture
def activity_files_dir(tmp_path):
    files_dir = tmp_path / "activity_files"
    files_dir.mkdir()
    shutil.copy(f"tests/data/strava_export/activities/{FIT_HIKE}", files_dir / FIT_HIKE)
    return files_dir


def fit_hike_entry(elapsed_time_s: float = 1886.0) -> dict:
    return {
        "strava": {"start_utc": "2026-09-30T18:48:20+00:00", "elapsed_time_s": elapsed_time_s},
        "recording_file": FIT_HIKE,
        "recorded": {
            "name": "weighted carry",
            "type": "hike",
            "start_local": "2026-09-30T12:48:20",
            "moving_time_s": 1800,
            "distance_mi": 1.0,
            "vert_ft": 337,
            "running_mi": 0.0,
            "avg_heart_rate_bpm": 128,
        },
        "manual": {},
    }


def test_climb_blocks_are_stored_and_recomputed_only_when_their_inputs_change(activity_files_dir, tmp_path, monkeypatch):
    store_path = tmp_path / "climb_blocks.json"
    analysed = []
    analyse = fitness.analyse_climbs
    monkeypatch.setattr(fitness, "analyse_climbs", lambda recording: analysed.append(1) or analyse(recording))

    entries = {"1": fit_hike_entry(), "gone": fit_hike_entry()}
    blocks, unreadable = climb_blocks_by_activity(entries, ["1", "gone"], activity_files_dir, store_path)
    assert len(analysed) == 2 and len(blocks["1"]) > 0 and unreadable == {}

    del entries["gone"]
    assert climb_blocks_by_activity(entries, ["1"], activity_files_dir, store_path)[0] == {"1": blocks["1"]}
    assert len(analysed) == 2
    assert set(json.loads(store_path.read_text())) == {"1"}

    # Cropped on Strava: the blocks come from less of the recording.
    entries["1"] = fit_hike_entry(elapsed_time_s=900.0)
    cropped, _ = climb_blocks_by_activity(entries, ["1"], activity_files_dir, store_path)
    assert len(analysed) == 3 and len(cropped["1"]) < len(blocks["1"])


def test_an_unreadable_recording_is_reported(activity_files_dir, tmp_path):
    (activity_files_dir / "broken.fit.gz").write_bytes(gzip.compress(b"not a fit file"))
    entries = {"1": {**fit_hike_entry(), "recording_file": "broken.fit.gz"}}
    blocks, unreadable = climb_blocks_by_activity(entries, ["1"], activity_files_dir, tmp_path / "climb_blocks.json")
    assert blocks == {} and "1" in unreadable


@pytest.fixture
def log_path(tmp_path):
    path = tmp_path / "training_log.json"
    path.write_text(json.dumps({"1": fit_hike_entry()}))
    return path


@pytest.fixture
def client(log_path, activity_files_dir, tmp_path, monkeypatch):
    monkeypatch.setitem(app.config, "TRAINING_LOG_PATH", log_path)
    monkeypatch.setitem(app.config, "ACTIVITY_FILES_DIR", activity_files_dir)
    monkeypatch.setitem(app.config, "CLIMB_BLOCKS_PATH", tmp_path / "climb_blocks.json")
    return app.test_client()


def test_fitness_endpoints(client):
    summary = client.get("/api/fitness").json
    assert summary["model"]["num_blocks"] > 0
    [only] = summary["efforts"]
    assert only["membership"] == "recent"
    # Not checked for sense: from one effort at ~6,000 ft, standard conditions are far extrapolation.
    assert isinstance(only["at_standard_ft_per_hr"], float)
    # Nothing is left to fit a model from without it.
    assert only["vs_model_frac"] is None
    assert summary["trend"]["figure"]["light"]["data"]

    detail = client.get("/api/fitness/efforts/1").json
    assert [p["title"] for p in detail["panels"]] == [
        "Elevation and grade", "Climbing", "Vertical rate vs heart rate", "Vertical rate vs elevation"
    ]
    assert client.get("/api/fitness/efforts/2").status_code == 404


def test_excluding_the_only_effort_leaves_no_model(client):
    response = client.put("/api/activities/1/model-choice", json={"choice": "exclude"})
    assert response.status_code == 200
    summary = client.get("/api/fitness").json
    assert summary["model"] is None and summary["no_model_reason"]
    assert summary["efforts"][0]["model_choice"] == "exclude"

    assert client.put("/api/activities/1/model-choice", json={"choice": None}).status_code == 200
    assert client.get("/api/fitness").json["model"] is not None


def test_model_choice_endpoint_errors(client):
    assert client.put("/api/activities/1/model-choice", json={"choice": "maybe"}).status_code == 400
    assert client.put("/api/activities/1/model-choice", json={}).status_code == 400
    assert client.put("/api/activities/9/model-choice", json={"choice": "require"}).status_code == 404


def test_editing_an_activity_keeps_its_model_choice(log_path):
    set_model_choice("1", ModelChoice.REQUIRE, log_path)
    activity = set_manual("1", {"grade": "A"}, log_path)
    assert activity.model_choice == ModelChoice.REQUIRE
    set_model_choice("1", None, log_path)
    [activity] = load_activities(log_path)
    assert activity.model_choice is None
    assert activity.grade.value == "A"
