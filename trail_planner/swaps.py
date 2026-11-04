"""Swapping one outing of a plan for another trail, and which swaps to recommend.

A swap is recommended if the plan it makes is still acceptable: within the vert tolerance and with
enough runnable miles. When fewer than NUM_RECOMMENDED_SWAPS are, the ones landing closest to the
target vert while keeping enough runnable miles are recommended as well, up to that many in all.

Any trail can be swapped in, since the person swapping chose it. But one that the plan's settings
exclude, or that repeats another outing when repeats aren't allowed, is never recommended.
"""

from dataclasses import dataclass
from enum import Enum

from trail_planner.planner import Plan, PlanRequest, has_enough_runnable, is_acceptable
from trail_planner.trails import Trail

NUM_RECOMMENDED_SWAPS = 5


class SwapRecommendation(Enum):
    MEETS_CRITERIA = "meets_criteria"
    CLOSEST = "closest"


@dataclass(frozen=True)
class SwapOption:
    trail: Trail
    # The plan with `trail` in place of the swapped-out outing.
    plan: Plan
    recommendation: SwapRecommendation | None


def swapped(plan: Plan, outing_index: int, trail: Trail) -> Plan:
    trails = list(plan.trails)
    trails[outing_index] = trail
    return Plan(tuple(trails), plan.target_vert_ft)


def keeps_trail_rules(plan: Plan, outing_index: int, trail: Trail, request: PlanRequest) -> bool:
    """Whether swapping `trail` in keeps the plan free of excluded trails, and of repeats unless they're allowed."""
    other_names = {t.name for i, t in enumerate(plan.trails) if i != outing_index}
    return trail.name not in request.excluded_names and (request.allow_repeats or trail.name not in other_names)


def recommendations(plans_by_name: dict[str, Plan], request: PlanRequest) -> dict[str, SwapRecommendation]:
    """Which of the swaps to recommend, given the plan each makes by the name of the trail it swaps in.
    Swaps left out aren't recommended."""
    meeting = [name for name, plan in plans_by_name.items() if is_acceptable(plan, request)]
    near_misses = sorted(
        (name for name, plan in plans_by_name.items() if name not in meeting and has_enough_runnable(plan, request)),
        key=lambda name: abs(plans_by_name[name].vert_error_frac),
    )
    closest = near_misses[: max(0, NUM_RECOMMENDED_SWAPS - len(meeting))]
    return {name: SwapRecommendation.MEETS_CRITERIA for name in meeting} | {
        name: SwapRecommendation.CLOSEST for name in closest
    }


def swap_options(plan: Plan, outing_index: int, catalog: list[Trail], request: PlanRequest) -> list[SwapOption]:
    """Each trail in `catalog` as a replacement for the plan's outing at `outing_index`, in catalog
    order, leaving out the trail already there. Raises ValueError if the plan has no such outing."""
    if not 0 <= outing_index < len(plan.trails):
        raise ValueError(f"The plan has no outing {outing_index}")
    swapped_out_name = plan.trails[outing_index].name
    candidates = [t for t in catalog if t.name != swapped_out_name]
    plans_by_name = {t.name: swapped(plan, outing_index, t) for t in candidates}
    recommended = recommendations(
        {t.name: plans_by_name[t.name] for t in candidates if keeps_trail_rules(plan, outing_index, t, request)},
        request,
    )
    return [SwapOption(t, plans_by_name[t.name], recommended.get(t.name)) for t in candidates]
