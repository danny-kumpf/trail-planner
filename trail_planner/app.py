"""Local web site with two tabs: shuffling weekly trail plans, and a training log.

Usage: uv run python -m trail_planner.app, then open http://localhost:5000
"""

import random
import secrets
import threading
from dataclasses import asdict
from datetime import date, datetime, timezone
from urllib.parse import urlsplit

from flask import Flask, jsonify, redirect, request, url_for

from trail_planner.planner import (
    NoCombinations,
    NoPlanInTolerance,
    NotEnoughRunnable,
    Plan,
    PlanFound,
    PlanRequest,
    PlanResult,
    shuffle_plan,
)
from trail_planner.trails import DEFAULT_GPX_DIR, DEFAULT_TRAILS_PATH, Trail, load_trails
from trail_planner.update_trails import update_trails
from trail_planner.strava_api import (
    DEFAULT_APP_PATH,
    DEFAULT_TOKENS_PATH,
    SCOPE,
    NotAuthorized,
    NotSetUp,
    StravaApp,
    StravaClient,
    StravaError,
    authorize_url,
    current_tokens,
    exchange_code,
    granted_scopes,
    load_app,
    load_tokens,
    save_tokens,
)
from trail_planner.strava_sync import sync
from trail_planner.training_log import (
    DEFAULT_ACTIVITY_FILES_DIR,
    DEFAULT_LOG_PATH,
    Activity,
    UnknownActivity,
    load_activities,
    set_manual,
)
from trail_planner.week_plans import (
    DEFAULT_WEEK_PLANS_PATH,
    WeekAlreadyPlanned,
    WeekPlan,
    load_week_plan,
    planned_week_starts,
    save_week_plan,
)

app = Flask(__name__)
app.config["TRAILS_PATH"] = DEFAULT_TRAILS_PATH
app.config["GPX_DIR"] = DEFAULT_GPX_DIR
app.config["TRAINING_LOG_PATH"] = DEFAULT_LOG_PATH
app.config["ACTIVITY_FILES_DIR"] = DEFAULT_ACTIVITY_FILES_DIR
app.config["STRAVA_APP_PATH"] = DEFAULT_APP_PATH
app.config["STRAVA_TOKENS_PATH"] = DEFAULT_TOKENS_PATH
app.config["WEEK_PLANS_PATH"] = DEFAULT_WEEK_PLANS_PATH
rng = random.Random()
# Held while the training log is read, changed and written, so a sync and an edit can't undo each other.
log_lock = threading.Lock()
# Held for a whole sync, so a second click while one runs is turned away rather than queued.
sync_lock = threading.Lock()
# Held for a whole trail update, so a second click while one runs is turned away rather than queued.
update_trails_lock = threading.Lock()
# Held while saved week plans are read, changed and written, so two saves can't undo each other.
week_plans_lock = threading.Lock()
# The `state` of each approval sent to Strava and not yet returned, so the callback only accepts those.
pending_oauth_states: set[str] = set()


def trail_json(trail: Trail) -> dict:
    return {
        "name": trail.name,
        "distance_mi": trail.distance_mi,
        "vert_ft": trail.vert_ft,
        "runnable_mi": trail.runnable_mi,
    }


def plan_json(plan: Plan) -> dict:
    return {
        "trails": [trail_json(t) for t in plan.trails],
        "total_vert_ft": plan.total_vert_ft,
        "total_distance_mi": plan.total_distance_mi,
        "total_runnable_mi": plan.total_runnable_mi,
        "vert_error_frac": plan.vert_error_frac,
    }


def activity_json(activity: Activity) -> dict:
    return {
        "id": activity.id,
        "name": activity.name,
        "type": activity.type.value,
        "start_local": activity.start_local.isoformat(),
        "moving_time_s": activity.moving_time_s,
        "distance_mi": activity.distance_mi,
        "vert_ft": activity.vert_ft,
        "running_mi": activity.running_mi,
        "grade": activity.grade.value if activity.grade else None,
        "recorded": activity.recorded,
        "overridden_fields": sorted(activity.overridden_fields),
    }


def result_json(result: PlanResult) -> dict:
    match result:
        case PlanFound(plan):
            return {"status": "found", "plan": plan_json(plan)}
        case NoPlanInTolerance(closest, recommendation):
            return {
                "status": "no_plan_in_tolerance",
                "closest": plan_json(closest),
                "recommended_tolerance_frac": recommendation.tolerance_frac,
                "num_plans_at_recommended": recommendation.num_plans,
            }
        case NotEnoughRunnable(max_runnable_mi):
            return {"status": "not_enough_runnable", "max_runnable_mi": max_runnable_mi}
        case NoCombinations():
            return {"status": "no_combinations"}


@app.get("/")
def index():
    return redirect(url_for("trail_planning"))


@app.get("/trail-planning")
def trail_planning():
    return app.send_static_file("trail_planning.html")


@app.get("/training-log")
def training_log():
    return app.send_static_file("training_log.html")


@app.get("/api/trails")
def trails():
    # Reloaded on every request so re-running update_trails shows up without a restart.
    catalog = load_trails(app.config["TRAILS_PATH"])
    return jsonify(
        complete=[trail_json(t) for t in catalog.complete],
        incomplete_names=catalog.incomplete_names,
    )


@app.post("/api/trails/update")
def trails_update():
    """Recompute each trail's GPX stats and add entries for new GPX files, as update_trails does."""
    if not update_trails_lock.acquire(blocking=False):
        return jsonify(error="An update is already running."), 409
    try:
        report = update_trails(app.config["TRAILS_PATH"], app.config["GPX_DIR"])
    except ValueError as e:
        return jsonify(error=str(e)), 500
    finally:
        update_trails_lock.release()
    return jsonify(asdict(report))


def trail_names(value) -> frozenset[str]:
    if not isinstance(value, list) or not all(isinstance(name, str) for name in value):
        raise TypeError("trail names must be a list of strings")
    return frozenset(value)


def plan_request_from_json(body) -> PlanRequest:
    """The plan request the page's settings describe. Raises KeyError, TypeError or ValueError if they're invalid."""
    return PlanRequest(
        target_vert_ft=float(body["target_vert_ft"]),
        target_running_mi=float(body["target_running_mi"]),
        min_outings=int(body["min_outings"]),
        max_outings=int(body["max_outings"]),
        vert_tolerance_frac=float(body["vert_tolerance_pct"]) / 100,
        allow_repeats=bool(body["allow_repeats"]),
        required_names=trail_names(body["required_trails"]),
        excluded_names=trail_names(body["excluded_trails"]),
    )


def plan_request_json(plan_request: PlanRequest) -> dict:
    """The page's settings for `plan_request`; the inverse of plan_request_from_json."""
    return {
        "target_vert_ft": plan_request.target_vert_ft,
        "target_running_mi": plan_request.target_running_mi,
        "min_outings": plan_request.min_outings,
        "max_outings": plan_request.max_outings,
        # Rounded so the percent typed in comes back as typed, e.g. 3 rather than 3.0000000000000004.
        "vert_tolerance_pct": round(plan_request.vert_tolerance_frac * 100, 9),
        "allow_repeats": plan_request.allow_repeats,
        "required_trails": sorted(plan_request.required_names),
        "excluded_trails": sorted(plan_request.excluded_names),
    }


@app.post("/api/plan")
def plan():
    body = request.get_json()
    try:
        plan_request = plan_request_from_json(body)
        result = shuffle_plan(load_trails(app.config["TRAILS_PATH"]).complete, plan_request, rng)
    except (KeyError, TypeError, ValueError) as e:
        return jsonify(error=str(e)), 400
    return jsonify(result_json(result))


def week_plan_json(week_plan: WeekPlan) -> dict:
    return {
        "week_start": week_plan.week_start.isoformat(),
        "request": plan_request_json(week_plan.request),
        "plan": plan_json(week_plan.plan),
    }


@app.get("/api/week-plans")
def week_plans():
    """The first day of each week with a saved plan."""
    return jsonify([week_start.isoformat() for week_start in planned_week_starts(app.config["WEEK_PLANS_PATH"])])


def week_start_or_none(text: str) -> date | None:
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


@app.get("/api/week-plans/<week_start>")
def week_plan(week_start: str):
    start = week_start_or_none(week_start)
    if start is None:
        return jsonify(error=f"{week_start!r} isn't a date"), 400
    saved = load_week_plan(start, app.config["WEEK_PLANS_PATH"])
    if saved is None:
        return jsonify(error=f"no plan saved for the week of {week_start}"), 404
    return jsonify(week_plan_json(saved))


def plan_of_trail_names(names, catalog: list[Trail], target_vert_ft: float) -> Plan:
    """The plan made of the named trails, in order, as they are in `catalog`. Raises TypeError or
    ValueError if `names` isn't a list of plannable trails."""
    if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
        raise TypeError("trail names must be a list of strings")
    by_name = {t.name: t for t in catalog}
    if unknown := sorted(set(names) - set(by_name)):
        raise ValueError(f"Trails aren't in the catalog: {', '.join(unknown)}")
    return Plan(tuple(by_name[name] for name in names), target_vert_ft)


@app.put("/api/week-plans/<week_start>")
def save_week(week_start: str):
    """Save a plan for the week starting on `week_start`, given the settings it was shuffled with
    (`request`) and its `trail_names`. If the week already has a plan, responds 409 unless
    `replace_existing` is true."""
    start = week_start_or_none(week_start)
    if start is None:
        return jsonify(error=f"{week_start!r} isn't a date"), 400
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(error="body must be an object"), 400
    try:
        plan_request = plan_request_from_json(body["request"])
        catalog = load_trails(app.config["TRAILS_PATH"]).complete
        saved = WeekPlan(start, plan_request, plan_of_trail_names(body["trail_names"], catalog, plan_request.target_vert_ft))
        with week_plans_lock:
            save_week_plan(saved, bool(body.get("replace_existing", False)), app.config["WEEK_PLANS_PATH"])
    except WeekAlreadyPlanned:
        return jsonify(error=f"a plan is already saved for the week of {week_start}"), 409
    except (KeyError, TypeError, ValueError) as e:
        return jsonify(error=str(e)), 400
    return jsonify(week_plan_json(saved))


@app.get("/api/activities")
def activities():
    return jsonify([activity_json(a) for a in load_activities(app.config["TRAINING_LOG_PATH"])])


@app.patch("/api/activities/<activity_id>")
def edit_activity(activity_id: str):
    """Replace the activity's hand edits (its grade and overrides) with the request body's `manual`."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or "manual" not in body:
        return jsonify(error="body must be an object with a `manual` field"), 400
    try:
        with log_lock:
            edited = set_manual(activity_id, body["manual"], app.config["TRAINING_LOG_PATH"])
        return jsonify(activity_json(edited))
    except UnknownActivity:
        return jsonify(error=f"no activity {activity_id!r}"), 404
    except ValueError as e:
        return jsonify(error=str(e)), 400


def strava_redirect_uri() -> str:
    """Strava redirects only to the application's callback domain, localhost, so use it even if
    this page was opened as 127.0.0.1."""
    port = urlsplit(request.host_url).port or 80
    return f"http://localhost:{port}{url_for('strava_callback')}"


def needs_approval(strava_app: StravaApp):
    state = secrets.token_urlsafe(32)
    pending_oauth_states.add(state)
    return jsonify(
        error="Approve access on Strava first.",
        authorize_url=authorize_url(strava_app, strava_redirect_uri(), state),
    ), 401


@app.post("/api/strava/sync")
def strava_sync():
    """Sync the training log with Strava. If access hasn't been approved, responds 401 with the
    `authorize_url` to send the browser to."""
    try:
        strava_app = load_app(app.config["STRAVA_APP_PATH"])
    except NotSetUp as e:
        return jsonify(error=str(e)), 503
    tokens_path = app.config["STRAVA_TOKENS_PATH"]
    tokens = load_tokens(tokens_path)
    if tokens is None:
        return needs_approval(strava_app)
    if not sync_lock.acquire(blocking=False):
        return jsonify(error="A sync is already running."), 409
    try:
        refreshed = current_tokens(strava_app, tokens, datetime.now(timezone.utc))
        if refreshed != tokens:
            save_tokens(refreshed, tokens_path)
        with log_lock:
            report = sync(
                StravaClient(refreshed.access_token), app.config["TRAINING_LOG_PATH"], app.config["ACTIVITY_FILES_DIR"]
            )
    except NotAuthorized:
        return needs_approval(strava_app)
    except StravaError as e:
        return jsonify(error=str(e)), 502
    finally:
        sync_lock.release()
    return jsonify(asdict(report))


def back_to_training_log(**params):
    return redirect(url_for("training_log", **params))


@app.get("/strava/callback")
def strava_callback():
    """Where Strava sends the browser after you approve (or decline) access. On approval, the tokens
    are saved and the training log page runs the sync it was asked for."""
    state = request.args.get("state", "")
    if state not in pending_oauth_states:
        return back_to_training_log(strava_error="Strava's reply didn't match an approval this site asked for; try again.")
    pending_oauth_states.discard(state)
    if "error" in request.args:
        return back_to_training_log(strava_error=f"Strava access wasn't approved ({request.args['error']}).")
    if SCOPE not in granted_scopes(request.args.get("scope", "")):
        return back_to_training_log(
            strava_error="Syncing needs access to all your activities, including private ones; approve that on Strava."
        )
    try:
        strava_app = load_app(app.config["STRAVA_APP_PATH"])
        save_tokens(exchange_code(strava_app, request.args["code"]), app.config["STRAVA_TOKENS_PATH"])
    except (StravaError, KeyError) as e:
        return back_to_training_log(strava_error=f"Couldn't finish approving access: {e}")
    return back_to_training_log(sync=1)


if __name__ == "__main__":
    app.run(debug=True)
