"""Local web site with three tabs: shuffling weekly trail plans, a training log, and climbing fitness.

Usage: uv run python -m trail_trainer.app, then open http://localhost:5000
"""

import json
import random
import secrets
import tempfile
import threading
from collections import OrderedDict
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path, PureWindowsPath
from urllib.parse import urlsplit

import plotly
from flask import Flask, jsonify, redirect, request, send_from_directory, url_for

from trail_trainer.climb_blocks import CLIMB_FLOOR_GRADE_FRAC
from trail_trainer.climb_model import ClimbModel
from trail_trainer.fitness import (
    DEFAULT_CLIMB_BLOCKS_PATH,
    MIN_EFFORT_CLIMBING_S,
    MIN_MODELLED_HEART_RATE_BPM,
    MIN_RECENT_EFFORTS,
    MODEL_WINDOW,
    STANDARD_CONDITIONS,
    Effort,
    FitnessOverview,
    activity_analysis,
    explorable_activity,
    explorable_ids,
    fitness_overview,
    why_not_an_effort,
)
from trail_trainer.fitness_plots import (
    elevation_and_grade,
    fitness_trend,
    model_details,
    predicted_vs_actual,
    track_overview,
)
from trail_trainer.import_activity_files import import_activity_files
from trail_trainer.planner import (
    NoCombinations,
    NoPlanInTolerance,
    NoPlan,
    NotEnoughRunnable,
    Plan,
    PlanRequest,
    PlansFound,
    shuffle_plans,
)
from trail_trainer.swaps import SwapOption, swap_options
from trail_trainer.trails import DEFAULT_GPX_DIR, DEFAULT_TRAILS_PATH, Trail, load_trails
from trail_trainer.update_trails import update_trails
from trail_trainer.strava_api import (
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
from trail_trainer.strava_sync import sync
from trail_trainer.training_log import (
    DEFAULT_ACTIVITY_FILES_DIR,
    DEFAULT_LOG_PATH,
    Activity,
    ActivityType,
    ModelChoice,
    UnknownActivity,
    load_activities,
    set_manual,
    set_model_choice,
)
from trail_trainer.week_plans import (
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
app.config["CLIMB_BLOCKS_PATH"] = DEFAULT_CLIMB_BLOCKS_PATH
rng = random.Random()
# Held while the training log is read, changed and written, so a sync and an edit can't undo each other.
log_lock = threading.Lock()
# Held for a whole sync, so a second click while one runs is turned away rather than queued.
sync_lock = threading.Lock()
# Held for a whole trail update, so a second click while one runs is turned away rather than queued.
update_trails_lock = threading.Lock()
# Held while saved week plans are read, changed and written, so two saves can't undo each other.
week_plans_lock = threading.Lock()
# Held while stored climb blocks are brought up to date, so two page loads don't both compute them.
climb_blocks_lock = threading.Lock()
# The acceptable plans last shuffled for each of the most recent (trails, request) pairs, in their
# shuffled order, so stepping through them doesn't search again. Most recently used last.
shuffled_plans: OrderedDict[tuple[tuple[Trail, ...], PlanRequest], tuple[Plan, ...]] = OrderedDict()
MAX_REMEMBERED_SHUFFLES = 8
shuffled_plans_lock = threading.Lock()
# The plotly.js that the installed plotly writes figures for.
PLOTLY_JS_DIR = Path(plotly.__file__).parent / "package_data"
# The `state` of each approval sent to Strava and not yet returned, so the callback only accepts those.
pending_oauth_states: set[str] = set()


def trail_json(trail: Trail) -> dict:
    return {
        "name": trail.name,
        "distance_mi": trail.distance_mi,
        "vert_ft": trail.vert_ft,
        "runnable_mi": trail.runnable_mi,
    }


def trail_from_json(body) -> Trail:
    """The trail `body` describes, as trail_json writes it. Raises KeyError, TypeError or ValueError if it's invalid."""
    if not isinstance(body["name"], str):
        raise TypeError("trail name must be a string")
    return Trail(body["name"], float(body["distance_mi"]), float(body["vert_ft"]), float(body["runnable_mi"]))


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


def no_plan_json(result: NoPlan) -> dict:
    match result:
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


@app.get("/fitness")
def fitness():
    return app.send_static_file("fitness.html")


@app.get("/fitness/activities/<activity_id>")
def fitness_activity_page(activity_id: str):
    return app.send_static_file("fitness_activity.html")


@app.get("/plotly.min.js")
def plotly_js():
    return send_from_directory(PLOTLY_JS_DIR, "plotly.min.js")


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


def plan_index_or_none(value) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise TypeError("plan_index must be null or a non-negative integer")
    return value


@app.post("/api/plan")
def plan():
    """One of the plans that meet the page's settings. With `plan_index` null, the acceptable plans
    are shuffled into a new order and the first is returned; otherwise the plan at `plan_index` in
    the order last shuffled for these settings and trails. If there's no such order (the trails have
    changed, or it was forgotten), a new one is shuffled and its first plan returned. A found plan
    comes with its `plan_index` and the `num_plans` in the order."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(error="body must be an object"), 400
    try:
        plan_request = plan_request_from_json(body)
        requested_index = plan_index_or_none(body.get("plan_index"))
        trails = tuple(load_trails(app.config["TRAILS_PATH"]).complete)
        key = (trails, plan_request)
        with shuffled_plans_lock:
            plans = shuffled_plans.get(key) if requested_index is not None else None
            if plans is None:
                result = shuffle_plans(list(trails), plan_request, rng)
                if not isinstance(result, PlansFound):
                    return jsonify(no_plan_json(result))
                plans, plan_index = result.plans, 0
                shuffled_plans[key] = plans
                while len(shuffled_plans) > MAX_REMEMBERED_SHUFFLES:
                    shuffled_plans.popitem(last=False)
            else:
                if requested_index >= len(plans):
                    raise ValueError(f"plan_index {requested_index} is past the last of {len(plans)} plans")
                plan_index = requested_index
            shuffled_plans.move_to_end(key)
    except (KeyError, TypeError, ValueError) as e:
        return jsonify(error=str(e)), 400
    return jsonify(status="found", plan=plan_json(plans[plan_index]), plan_index=plan_index, num_plans=len(plans))


def swap_option_json(option: SwapOption) -> dict:
    return {
        "trail": trail_json(option.trail),
        "plan": plan_json(option.plan),
        "recommendation": None if option.recommendation is None else option.recommendation.value,
    }


@app.post("/api/plan/swap-options")
def plan_swap_options():
    """The trails that could replace one outing of a plan, given the settings it was shuffled with
    (`request`), its `trails` as shown, and the `outing_index` to replace. Each comes with the plan
    the swap makes and whether it's recommended."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(error="body must be an object"), 400
    try:
        plan_request = plan_request_from_json(body["request"])
        if not isinstance(body["trails"], list):
            raise TypeError("trails must be a list")
        plan = Plan(tuple(trail_from_json(t) for t in body["trails"]), plan_request.target_vert_ft)
        catalog = load_trails(app.config["TRAILS_PATH"]).complete
        options = swap_options(plan, int(body["outing_index"]), catalog, plan_request)
    except (KeyError, TypeError, ValueError) as e:
        return jsonify(error=str(e)), 400
    return jsonify(options=[swap_option_json(option) for option in options])


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


def uploaded_file_name(file_name: str | None) -> str:
    """The upload's own name, without any folders the browser sent with it (a Windows browser may
    send backslashes)."""
    name = PureWindowsPath(file_name or "").name
    return name if name not in ("", ".", "..") else "upload"


def types_by_file_name_from_json(text: str) -> dict[str, ActivityType]:
    """`{"file name": "type", ...}` as types, or ValueError."""
    try:
        types = json.loads(text)
    except json.JSONDecodeError:
        raise ValueError("types must be a JSON object") from None
    if not isinstance(types, dict):
        raise ValueError("types must be a JSON object")
    try:
        return {str(name): ActivityType(value) for name, value in types.items()}
    except ValueError:
        raise ValueError(f"each type must be one of {', '.join(ActivityType)}") from None


@app.post("/api/activities/files")
def add_activity_files():
    """Import the uploaded activity files (form field `files`: FIT or GPX, optionally gzipped, or
    zips of them). An optional `types` field, `{"file name": "type"}`, gives the type of files that
    don't say their sport. Responds with what was imported."""
    uploads = request.files.getlist("files")
    if not uploads:
        return jsonify(error="no files were uploaded"), 400
    try:
        types_by_file_name = types_by_file_name_from_json(request.form.get("types", "{}"))
    except ValueError as e:
        return jsonify(error=str(e)), 400
    with tempfile.TemporaryDirectory() as upload_dir:
        paths = []
        for i, upload in enumerate(uploads):
            # A folder per upload keeps each one's name, even if two share it.
            folder = Path(upload_dir) / str(i)
            folder.mkdir()
            path = folder / uploaded_file_name(upload.filename)
            upload.save(path)
            paths.append(path)
        with log_lock:
            report = import_activity_files(
                paths,
                app.config["TRAINING_LOG_PATH"],
                app.config["ACTIVITY_FILES_DIR"],
                types_by_file_name=types_by_file_name,
            )
    return jsonify(asdict(report))


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


@app.put("/api/activities/<activity_id>/model-choice")
def edit_model_choice(activity_id: str):
    """Require the activity in the climbing model, exclude it, or (`choice` null) leave it to its date."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or "choice" not in body:
        return jsonify(error="body must be an object with a `choice` field"), 400
    try:
        choice = None if body["choice"] is None else ModelChoice(body["choice"])
    except ValueError:
        return jsonify(error=f"choice must be one of {', '.join(ModelChoice)} or null"), 400
    try:
        with log_lock:
            edited = set_model_choice(activity_id, choice, app.config["TRAINING_LOG_PATH"])
    except UnknownActivity:
        return jsonify(error=f"no activity {activity_id!r}"), 404
    return jsonify(activity_json(edited))


def current_fitness() -> FitnessOverview:
    with climb_blocks_lock:
        return fitness_overview(
            app.config["TRAINING_LOG_PATH"], app.config["ACTIVITY_FILES_DIR"], app.config["CLIMB_BLOCKS_PATH"]
        )


def climb_model_json(model: ClimbModel) -> dict:
    return {
        "at_standard_ft_per_hr": model.predict_at(STANDARD_CONDITIONS),
        # At the standard grade and gait, where the model's line through heart rate and elevation is straight.
        "hr_slope_ft_per_hr_per_bpm": model.hr_slope_ft_per_hr_per_bpm(STANDARD_CONDITIONS),
        "elevation_slope_ft_per_hr_per_ft": model.elevation_slope_ft_per_hr_per_ft(STANDARD_CONDITIONS),
        "r_squared": model.r_squared,
        "num_blocks": model.num_blocks,
        "elevation_span_ft": list(model.elevation_span_ft),
    }


def effort_json(effort: Effort, overview: FitnessOverview) -> dict:
    activity = effort.activity
    score = overview.scores.get(effort.id)
    membership = overview.membership.get(effort.id)
    return {
        "id": effort.id,
        "name": activity.name,
        "type": activity.type.value,
        "start_local": activity.start_local.isoformat(),
        "climbing_s": effort.climbing_s,
        "avg_heart_rate_bpm": effort.avg_heart_rate_bpm,
        "elevation_span_ft": list(effort.elevation_span_ft),
        "model_choice": activity.model_choice.value if activity.model_choice else None,
        "membership": membership.value if membership else None,
        "at_standard_ft_per_hr": score.at_standard_ft_per_hr if score else None,
        "vs_model_frac": score.vs_model_frac if score else None,
        "outside_model_elevations_frac": score.outside_model_elevations_frac if score else None,
    }


def fitness_context_json(overview: FitnessOverview) -> dict:
    """What every fitness response carries: the standard conditions and the model."""
    return {
        "standard": {
            "heart_rate_bpm": STANDARD_CONDITIONS.heart_rate_bpm,
            "elevation_ft": STANDARD_CONDITIONS.elevation_ft,
            "grade_frac": STANDARD_CONDITIONS.grade_frac,
            "running_frac": STANDARD_CONDITIONS.running_frac,
        },
        "model": None if overview.model is None else climb_model_json(overview.model),
        "no_model_reason": overview.no_model_reason,
    }


@app.get("/api/fitness")
def fitness_summary():
    overview = current_fitness()
    return jsonify(
        **fitness_context_json(overview),
        model_window_days=MODEL_WINDOW.days,
        min_recent_efforts=MIN_RECENT_EFFORTS,
        min_effort_climbing_s=MIN_EFFORT_CLIMBING_S,
        climb_floor_grade_frac=CLIMB_FLOOR_GRADE_FRAC,
        min_modelled_heart_rate_bpm=MIN_MODELLED_HEART_RATE_BPM,
        efforts=[effort_json(e, overview) for e in overview.efforts],
        num_unreadable=len(overview.unreadable),
        trend=None if overview.model is None else fitness_trend(overview),
        model_details=None if overview.model is None else model_details(overview),
    )


@app.get("/api/fitness/explorable-ids")
def fitness_explorable_ids():
    """The ids of the activities with a page to explore, without fitting the model or scoring them."""
    with climb_blocks_lock:
        ids = explorable_ids(
            app.config["TRAINING_LOG_PATH"], app.config["ACTIVITY_FILES_DIR"], app.config["CLIMB_BLOCKS_PATH"]
        )
    return jsonify(ids)


@app.get("/api/fitness/activities/<activity_id>")
def fitness_activity(activity_id: str):
    """One activity's climbing in full, for looking at it closely. An effort also gets its scores
    and its figures against the model; any other activity only the figures of its recording."""
    activity = explorable_activity(activity_id, app.config["TRAINING_LOG_PATH"])
    if activity is None:
        return jsonify(error=f"nothing to explore for {activity_id!r}: it isn't in the log, doesn't cover "
                             "distance, or has no recording"), 404
    try:
        analysis = activity_analysis(activity_id, app.config["TRAINING_LOG_PATH"], app.config["ACTIVITY_FILES_DIR"])
    except (ValueError, OSError) as e:
        return jsonify(error=f"couldn't read the recording: {e}"), 500
    panels = [elevation_and_grade(analysis), track_overview(analysis)]
    overview = current_fitness()
    effort = overview.effort(activity_id)
    activity_fields = {
        "id": activity.id,
        "name": activity.name,
        "type": activity.type.value,
        "start_local": activity.start_local.isoformat(),
    }
    if effort is None:
        return jsonify(
            **fitness_context_json(overview),
            activity=activity_fields,
            effort=None,
            not_effort_reason=why_not_an_effort(activity),
            panels=panels,
        )
    if overview.model is not None:
        comparison = overview.comparison_model(effort)
        if comparison is not None:
            panels.append(
                predicted_vs_actual(analysis, comparison, compared_without_effort=effort.id in overview.membership)
            )
        panels.append(model_details(overview, effort))
    return jsonify(
        **fitness_context_json(overview),
        activity=activity_fields,
        effort=effort_json(effort, overview),
        not_effort_reason=None,
        panels=panels,
    )


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
