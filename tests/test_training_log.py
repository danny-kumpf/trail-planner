import json
from datetime import datetime

import pytest

from trail_planner.app import app
from trail_planner.training_log import ActivityType, Grade, UnknownActivity, load_activities, set_manual

RECORDED = {
    "name": "Treadmill hills",
    "type": "trail_run",
    "start_local": "2026-10-01T06:30:00",
    "moving_time_s": 3600,
    "distance_mi": 5.0,
    "vert_ft": 0,
    "running_mi": 5.0,
}


@pytest.fixture
def log_path(tmp_path):
    path = tmp_path / "training_log.json"
    path.write_text(json.dumps({"strava:1": {"recorded": RECORDED, "manual": {}}}))
    return path


def test_recorded_values_without_edits(log_path):
    [activity] = load_activities(log_path)
    assert activity.type == ActivityType.TRAIL_RUN
    assert activity.start_local == datetime(2026, 10, 1, 6, 30)
    assert activity.vert_ft == 0
    assert activity.grade is None
    assert activity.overridden_fields == frozenset()
    assert activity.avg_heart_rate_bpm is None


def test_manual_values_override_recorded_ones(log_path):
    activity = set_manual("strava:1", {"vert_ft": 1500, "grade": "B"}, log_path)
    assert activity.vert_ft == 1500
    assert activity.recorded["vert_ft"] == 0
    assert activity.grade == Grade.B
    assert activity.overridden_fields == {"vert_ft"}
    assert load_activities(log_path) == [activity]


def test_type_can_be_overridden(log_path):
    activity = set_manual("strava:1", {"type": "race"}, log_path)
    assert activity.type == ActivityType.RACE
    assert activity.recorded["type"] == "trail_run"
    assert activity.overridden_fields == {"type"}


def test_setting_manual_replaces_previous_edits(log_path):
    set_manual("strava:1", {"vert_ft": 1500, "grade": "B"}, log_path)
    activity = set_manual("strava:1", {"grade": "A"}, log_path)
    assert activity.vert_ft == 0
    assert activity.grade == Grade.A


@pytest.mark.parametrize(
    "manual",
    [
        {"grade": "E"},
        {"name": "Renamed"},
        {"type": "running"},
        {"vert_ft": -5},
        {"vert_ft": "lots"},
        {"vert_ft": True},
    ],
)
def test_rejects_what_cant_be_edited_by_hand(log_path, manual):
    with pytest.raises(ValueError):
        set_manual("strava:1", manual, log_path)
    assert json.loads(log_path.read_text())["strava:1"]["manual"] == {}


def test_unknown_activity(log_path):
    with pytest.raises(UnknownActivity):
        set_manual("strava:2", {"grade": "A"}, log_path)


def test_missing_log_is_empty(tmp_path):
    assert load_activities(tmp_path / "training_log.json") == []


@pytest.fixture
def client(log_path, monkeypatch):
    monkeypatch.setitem(app.config, "TRAINING_LOG_PATH", log_path)
    return app.test_client()


def test_activities_endpoint_and_edit_round_trip(client):
    response = client.patch(
        "/api/activities/strava:1", json={"manual": {"vert_ft": 1500, "grade": "A", "type": "race"}}
    )
    assert response.status_code == 200
    assert response.json["vert_ft"] == 1500
    assert response.json["type"] == "race"
    assert response.json["overridden_fields"] == ["type", "vert_ft"]

    [activity] = client.get("/api/activities").json
    assert activity["vert_ft"] == 1500
    assert activity["recorded"]["vert_ft"] == 0
    assert activity["grade"] == "A"
    assert activity["start_local"] == "2026-10-01T06:30:00"


def test_edit_endpoint_errors(client):
    assert client.patch("/api/activities/strava:1", json={"manual": {"grade": "Z"}}).status_code == 400
    assert client.patch("/api/activities/strava:1", json={"grade": "A"}).status_code == 400
    assert client.patch("/api/activities/strava:9", json={"manual": {}}).status_code == 404
