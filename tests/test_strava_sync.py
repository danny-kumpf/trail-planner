import gzip
import io
import json
import urllib.error
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import numpy as np
import pytest

from trail_planner import app as app_module
from trail_planner.activity_stats import recorded_block
from trail_planner.import_strava_export import import_strava_export
from trail_planner.recording import Recording, read_recording, recording_from_streams
from trail_planner.strava_api import (
    MAX_ACTIVITIES_PER_PAGE,
    NotAuthorized,
    NotFound,
    RateLimited,
    StravaApp,
    StravaClient,
    Tokens,
    current_tokens,
    load_tokens,
    save_tokens,
)
from trail_planner.strava_sync import sync
from trail_planner.training_log import ActivityType, Grade, load_activities, set_manual

EXPORT_DIR = Path(__file__).resolve().parent / "data" / "strava_export"
HIKE_FIT = EXPORT_DIR / "activities" / "21553533855.fit.gz"
HIKE_ID = "20397657728"  # "weighted carry", the export's hike
NO_FILE_ID = "14456102085"  # "First paced mile!", a run entered by hand
WORKOUT_ID = "19746459323"


def streams_from(recording: Recording) -> dict:
    """What Strava's streams endpoint gives for a recording, keyed by channel."""

    def stream(values: np.ndarray) -> dict:
        return {"data": [None if np.isnan(v) else float(v) for v in values]}

    streams = {
        "time": {"data": recording.time_s.tolist()},
        "latlng": {"data": [[lat, lon] for lat, lon in zip(recording.latitude_deg, recording.longitude_deg)]},
    }
    for key, values in [
        ("altitude", recording.elevation_m),
        ("distance", recording.device_distance_m),
        ("cadence", recording.cadence_strides_per_min),
        ("heartrate", recording.heart_rate_bpm),
    ]:
        if values is not None:
            streams[key] = stream(values)
    return streams


def summary(activity_id: str, recording: Recording | None = None, start: datetime | None = None, **fields) -> dict:
    """An activity as Strava's list gives it; by default a 10 min hike, at 9 am on Oct 5 unless
    the recording or `start` says when."""
    start = start or (recording.start if recording else datetime(2026, 10, 5, 15, tzinfo=timezone.utc))
    return {
        "id": int(activity_id),
        "name": "Morning hike",
        "sport_type": "Hike",
        "start_date": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "start_date_local": (start - timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "moving_time": 600,
        "elapsed_time": round(recording.elapsed_s) if recording else 600,
        "distance": 1000.0,
        "total_elevation_gain": 50.0,
        "workout_type": None,
        "manual": False,
        **fields,
    }


class FakeStrava:
    """Strava's API, answering from the activities it's given."""

    def __init__(self, summaries, details=None, streams=None, reads_before_rate_limit=None):
        self.summaries = summaries
        self.details = details or {}
        self.streams = streams or {}
        self.reads_before_rate_limit = reads_before_rate_limit
        self.stream_requests: list[str] = []

    def read(self):
        if self.reads_before_rate_limit == 0:
            raise RateLimited("rate limited")
        if self.reads_before_rate_limit is not None:
            self.reads_before_rate_limit -= 1

    def list_activities(self):
        return self.summaries

    def activity_details(self, activity_id):
        self.read()
        return {"private_note": None, "description": None, **self.details.get(activity_id, {})}

    def activity_streams(self, activity_id):
        self.read()
        self.stream_requests.append(activity_id)
        if activity_id not in self.streams:
            raise NotFound(activity_id)
        return self.streams[activity_id]


@pytest.fixture
def hike_recording():
    return read_recording(HIKE_FIT)


@pytest.fixture
def paths(tmp_path):
    return tmp_path / "training_log.json", tmp_path / "activity_files"


def imported_export(tmp_path, log_path, files_dir):
    zip_path = tmp_path / "export.zip"
    with zipfile.ZipFile(zip_path, "w") as export:
        for path in EXPORT_DIR.rglob("*"):
            if path.is_file():
                export.write(path, path.relative_to(EXPORT_DIR).as_posix())
    import_strava_export(zip_path, log_path, files_dir)


def test_streams_give_the_same_numbers_as_the_recording(hike_recording):
    strava = {
        "name": "weighted carry", "type": "Hike", "start_utc": hike_recording.start.isoformat(),
        "start_local": "2026-09-30T12:48:20", "moving_time_s": 600.0, "elapsed_time_s": hike_recording.elapsed_s,
        "distance_m": 0.0, "elevation_gain_m": None, "avg_heart_rate_bpm": None, "max_heart_rate_bpm": None,
        "workout_type": None,
    }
    from_streams = recording_from_streams({"start_utc": strava["start_utc"], "streams": streams_from(hike_recording)})
    assert from_streams.cadence_strides_per_min is not None
    assert recorded_block(strava, from_streams) == recorded_block(strava, hike_recording)


def test_new_activity_is_downloaded(paths, hike_recording):
    log_path, files_dir = paths
    strava = FakeStrava(
        [summary("101", hike_recording, name="Green Mountain")],
        details={"101": {"private_note": "Grade: A\nstrong climb"}},
        streams={"101": streams_from(hike_recording)},
    )
    report = sync(strava, log_path, files_dir)
    assert report.added == ["Green Mountain (101)"]
    assert report.grades_copied == {"Green Mountain (101)": "A"}

    [activity] = load_activities(log_path)
    assert activity.name == "Green Mountain"
    assert activity.grade == Grade.A
    # Strava's local start, not this computer's time zone.
    assert activity.start_local == (hike_recording.start - timedelta(hours=6)).replace(tzinfo=None, microsecond=0)
    assert 250 < activity.vert_ft < 400

    stored = json.loads(gzip.decompress((files_dir / "101.streams.json.gz").read_bytes()))
    assert stored["streams"] == streams_from(hike_recording)
    assert json.loads(log_path.read_text())["101"]["strava"]["private_note"] == "Grade: A\nstrong climb"


def test_activity_entered_by_hand_comes_from_its_summary(paths):
    log_path, files_dir = paths
    strava = FakeStrava([summary("102", sport_type="Run", manual=True, distance=1609.344, moving_time=480)])
    sync(strava, log_path, files_dir)
    assert strava.stream_requests == []
    [activity] = load_activities(log_path)
    assert activity.type == ActivityType.TRAIL_RUN
    assert activity.distance_mi == pytest.approx(1.0)
    assert activity.running_mi == pytest.approx(1.0)


def test_resort_skiing_and_snowboarding_are_left_out(paths, hike_recording):
    log_path, files_dir = paths
    strava = FakeStrava(
        [
            summary("301", hike_recording, name="Eldora", sport_type="Snowboard"),
            summary("302", hike_recording, name="Winter Park", sport_type="AlpineSki"),
            summary("303", hike_recording, name="Skin up Jones Pass", sport_type="BackcountrySki"),
        ],
        streams={"303": streams_from(hike_recording)},
    )
    report = sync(strava, log_path, files_dir)
    assert report.added == ["Skin up Jones Pass (303)"]
    assert strava.stream_requests == ["303"]
    assert [(a.id, a.type) for a in load_activities(log_path)] == [("303", ActivityType.SKI)]


def test_an_activity_changed_to_snowboarding_is_removed(paths, hike_recording):
    log_path, files_dir = paths
    strava = FakeStrava([summary("401", hike_recording, name="Eldora")], streams={"401": streams_from(hike_recording)})
    sync(strava, log_path, files_dir)
    assert (files_dir / "401.streams.json.gz").exists()

    strava.summaries = [summary("401", hike_recording, name="Eldora", sport_type="Snowboard")]
    report = sync(strava, log_path, files_dir)
    assert report.removed == ["Eldora (401)"]
    assert load_activities(log_path) == []
    assert not (files_dir / "401.streams.json.gz").exists()


def test_activities_from_the_export_are_refreshed_keeping_edits(tmp_path, paths, hike_recording):
    log_path, files_dir = paths
    imported_export(tmp_path, log_path, files_dir)
    set_manual(HIKE_ID, {"vert_ft": 1000}, log_path)
    exported = json.loads(log_path.read_text())

    def exported_start(activity_id: str) -> datetime:
        return datetime.fromisoformat(exported[activity_id]["strava"]["start_utc"])

    summaries = [
        summary(HIKE_ID, hike_recording, name="Weighted carry up Flagstaff", sport_type="Run", workout_type=1),
        summary(NO_FILE_ID, start=exported_start(NO_FILE_ID), name="First paced mile!", sport_type="Run",
                manual=True, distance=1609.344, moving_time=480),
        summary(WORKOUT_ID, start=exported_start(WORKOUT_ID), name="Stairwell weighted carry", sport_type="Workout",
                elapsed_time=round(exported[WORKOUT_ID]["strava"]["elapsed_time_s"])),
    ]
    report = sync(FakeStrava(summaries), log_path, files_dir)
    assert report.added == []
    assert len(report.updated) == 3
    assert report.failed == {}

    hike = {a.id: a for a in load_activities(log_path)}[HIKE_ID]
    assert hike.name == "Weighted carry up Flagstaff"
    # Marked as a race on Strava.
    assert hike.type == ActivityType.RACE
    assert hike.vert_ft == 1000
    # The export's private note is kept; the list doesn't give notes.
    entry = json.loads(log_path.read_text())[HIKE_ID]
    assert entry["strava"]["private_note"].startswith("Grade: D")
    assert entry["recording_file"] == f"{HIKE_ID}.fit.gz"

    # Nothing changed on Strava since, so nothing is recomputed.
    assert sync(FakeStrava(summaries), log_path, files_dir).updated == []


def test_reaching_the_rate_limit_keeps_what_was_added(paths, hike_recording):
    log_path, files_dir = paths
    strava = FakeStrava(
        [summary("201", hike_recording, name="Newest"), summary("200", hike_recording, name="Older")],
        streams={"201": streams_from(hike_recording), "200": streams_from(hike_recording)},
        reads_before_rate_limit=3,
    )
    report = sync(strava, log_path, files_dir)
    assert report.added == ["Newest (201)"]
    assert report.not_yet_added == ["Older (200)"]
    assert [a.id for a in load_activities(log_path)] == ["201"]

    strava.reads_before_rate_limit = None
    assert sync(strava, log_path, files_dir).added == ["Older (200)"]


def test_activities_no_longer_on_strava_are_kept(tmp_path, paths):
    log_path, files_dir = paths
    imported_export(tmp_path, log_path, files_dir)
    report = sync(FakeStrava([]), log_path, files_dir)
    assert sorted(report.not_on_strava) == sorted(
        ["weighted carry (20397657728)", "Stairwell weighted carry (19746459323)", "First paced mile! (14456102085)"]
    )
    assert len(load_activities(log_path)) == 3


# ---- Strava's API ----


class FakeResponse:
    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.body


def http_error(code: int):
    return urllib.error.HTTPError("https://www.strava.com/api/v3/x", code, "error", {}, io.BytesIO(b"{}"))


def test_activities_are_listed_page_by_page():
    pages = {1: [{"id": i} for i in range(MAX_ACTIVITIES_PER_PAGE)], 2: [{"id": -1}]}
    requested_pages = []

    def open_url(request, timeout):
        page = int(parse_qs(urlsplit(request.full_url).query)["page"][0])
        requested_pages.append(page)
        assert request.headers["Authorization"] == "Bearer token"
        return FakeResponse(pages[page])

    activities = StravaClient("token", open_url).list_activities()
    assert len(activities) == MAX_ACTIVITIES_PER_PAGE + 1
    assert requested_pages == [1, 2]


@pytest.mark.parametrize("code, error", [(401, NotAuthorized), (404, NotFound), (429, RateLimited)])
def test_http_errors(code, error):
    def open_url(request, timeout):
        raise http_error(code)

    with pytest.raises(error):
        StravaClient("token", open_url).activity_details("1")


def test_tokens_are_refreshed_only_when_expiring():
    app = StravaApp(client_id="1", client_secret="secret")
    now = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
    sent = []

    def open_url(request, timeout):
        sent.append(parse_qs(request.data.decode()))
        return FakeResponse({"access_token": "new", "refresh_token": "new refresh", "expires_at": 1_800_000_000})

    fresh = Tokens("old", "refresh", now + timedelta(hours=5))
    assert current_tokens(app, fresh, now, open_url) is fresh
    assert sent == []

    expiring = Tokens("old", "refresh", now + timedelta(minutes=1))
    refreshed = current_tokens(app, expiring, now, open_url)
    assert refreshed.access_token == "new"
    assert refreshed.refresh_token == "new refresh"
    assert sent[0]["grant_type"] == ["refresh_token"]
    assert sent[0]["refresh_token"] == ["refresh"]


def test_tokens_round_trip(tmp_path):
    tokens = Tokens("access", "refresh", datetime(2026, 10, 5, 18, tzinfo=timezone.utc))
    save_tokens(tokens, tmp_path / "tokens.json")
    assert load_tokens(tmp_path / "tokens.json") == tokens
    assert load_tokens(tmp_path / "missing.json") is None


# ---- The site ----


@pytest.fixture
def site(tmp_path, monkeypatch):
    monkeypatch.setitem(app_module.app.config, "TRAINING_LOG_PATH", tmp_path / "training_log.json")
    monkeypatch.setitem(app_module.app.config, "ACTIVITY_FILES_DIR", tmp_path / "activity_files")
    monkeypatch.setitem(app_module.app.config, "STRAVA_APP_PATH", tmp_path / "strava_app.json")
    monkeypatch.setitem(app_module.app.config, "STRAVA_TOKENS_PATH", tmp_path / "strava_tokens.json")
    return app_module.app.test_client(), tmp_path


def set_up_strava_app(tmp_path):
    (tmp_path / "strava_app.json").write_text(json.dumps({"client_id": "4242", "client_secret": "secret"}))


def test_sync_without_a_strava_app_says_how_to_set_one_up(site):
    client, _ = site
    response = client.post("/api/strava/sync")
    assert response.status_code == 503
    assert "strava_app.json" in response.get_json()["error"]


def test_approving_access_then_syncing(site, monkeypatch):
    client, tmp_path = site
    set_up_strava_app(tmp_path)

    response = client.post("http://127.0.0.1:5000/api/strava/sync")
    assert response.status_code == 401
    query = parse_qs(urlsplit(response.get_json()["authorize_url"]).query)
    assert query["client_id"] == ["4242"]
    assert query["redirect_uri"] == ["http://localhost:5000/strava/callback"]
    assert query["scope"] == ["activity:read_all"]

    expires_at = datetime.now(timezone.utc) + timedelta(hours=6)
    monkeypatch.setattr(app_module, "exchange_code", lambda app, code: Tokens(f"access for {code}", "refresh", expires_at))
    callback = client.get(
        "/strava/callback", query_string={"state": query["state"][0], "code": "abc", "scope": "read,activity:read_all"}
    )
    assert callback.status_code == 302
    assert callback.headers["Location"].endswith("/training-log?sync=1")
    assert load_tokens(tmp_path / "strava_tokens.json").access_token == "access for abc"

    clients = []
    monkeypatch.setattr(
        app_module, "StravaClient", lambda token: clients.append(token) or FakeStrava([summary("7", manual=True)])
    )
    response = client.post("/api/strava/sync")
    assert response.status_code == 200
    assert response.get_json()["added"] == ["Morning hike (7)"]
    assert clients == ["access for abc"]
    assert [a["id"] for a in client.get("/api/activities").get_json()] == ["7"]


@pytest.mark.parametrize(
    "args",
    [
        {"state": "not one we sent", "code": "abc", "scope": "read,activity:read_all"},
        {"code": "abc", "scope": "read,activity:read"},
        {"error": "access_denied"},
    ],
)
def test_callback_refuses_what_it_cant_use(site, args):
    client, tmp_path = site
    set_up_strava_app(tmp_path)
    state = parse_qs(urlsplit(client.post("/api/strava/sync").get_json()["authorize_url"]).query)["state"][0]
    response = client.get("/strava/callback", query_string={"state": state, **args})
    assert "strava_error=" in response.headers["Location"]
    assert not (tmp_path / "strava_tokens.json").exists()
