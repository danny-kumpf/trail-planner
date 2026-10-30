"""Plans saved for particular weeks, in week_plans.json, so a week planned once can be reopened later.

Each entry is keyed by the week's first day (YYYY-MM-DD) and holds:
- `request`: the settings the plan was shuffled with, so the planning page can be put back as it was.
- `trails`: the plan's trails with their stats when it was saved, so later trail updates don't change it.
"""

from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

from trail_planner.json_store import read_entries, write_entries
from trail_planner.planner import Plan, PlanRequest
from trail_planner.trails import PROJECT_ROOT, Trail

DEFAULT_WEEK_PLANS_PATH = PROJECT_ROOT / "week_plans.json"


@dataclass(frozen=True)
class WeekPlan:
    week_start: date
    request: PlanRequest
    plan: Plan


class WeekAlreadyPlanned(Exception):
    """A plan is already saved for the week, and replacing it wasn't asked for."""


def request_entry(request: PlanRequest) -> dict:
    return {
        **asdict(request),
        "required_names": sorted(request.required_names),
        "excluded_names": sorted(request.excluded_names),
    }


def request_from_entry(entry: dict) -> PlanRequest:
    return PlanRequest(
        **{
            **entry,
            "required_names": frozenset(entry["required_names"]),
            "excluded_names": frozenset(entry["excluded_names"]),
        }
    )


def week_plan_from_entry(week_start: date, entry: dict) -> WeekPlan:
    request = request_from_entry(entry["request"])
    trails = tuple(Trail(**trail) for trail in entry["trails"])
    return WeekPlan(week_start, request, Plan(trails, request.target_vert_ft))


def read_week_plans(path: Path) -> dict[str, dict]:
    """The saved plans' entries; a file that hasn't been created yet holds none."""
    return read_entries(path) if path.exists() else {}


def planned_week_starts(path: Path = DEFAULT_WEEK_PLANS_PATH) -> list[date]:
    """The first day of every week with a saved plan, oldest first."""
    return sorted(date.fromisoformat(key) for key in read_week_plans(path))


def load_week_plan(week_start: date, path: Path = DEFAULT_WEEK_PLANS_PATH) -> WeekPlan | None:
    entry = read_week_plans(path).get(week_start.isoformat())
    return None if entry is None else week_plan_from_entry(week_start, entry)


def save_week_plan(week_plan: WeekPlan, replace_existing: bool, path: Path = DEFAULT_WEEK_PLANS_PATH) -> None:
    """Save the plan for its week. Raises WeekAlreadyPlanned if the week has one and `replace_existing` is false."""
    entries = read_week_plans(path)
    key = week_plan.week_start.isoformat()
    if key in entries and not replace_existing:
        raise WeekAlreadyPlanned(key)
    entries[key] = {
        "request": request_entry(week_plan.request),
        "trails": [asdict(trail) for trail in week_plan.plan.trails],
    }
    write_entries(path, dict(sorted(entries.items())))
