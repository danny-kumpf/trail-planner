"""Choosing a week of trails whose total vert lands near a target.

Shuffling rolls the number of outings first, so every count in the allowed range is equally
likely regardless of how many combinations each count has, then picks uniformly at random
among that count's acceptable plans.
"""

import itertools
import math
import random
from collections.abc import Iterator
from dataclasses import dataclass

from trail_planner.trails import Trail

# When nothing fits, recommend a tolerance that would give about this many plans to shuffle among.
RECOMMENDED_NUM_OPTIONS = 10
TOLERANCE_STEP_FRAC = 0.005


@dataclass(frozen=True)
class PlanRequest:
    target_vert_ft: float
    target_running_mi: float
    min_outings: int
    max_outings: int
    vert_tolerance_frac: float
    allow_repeats: bool

    def __post_init__(self) -> None:
        if self.target_vert_ft <= 0:
            raise ValueError("Target vert must be positive")
        if self.target_running_mi < 0:
            raise ValueError("Target running miles can't be negative")
        if not 1 <= self.min_outings <= self.max_outings:
            raise ValueError("Outings range must satisfy 1 <= min <= max")
        if self.vert_tolerance_frac < 0:
            raise ValueError("Vert tolerance can't be negative")


@dataclass(frozen=True)
class Plan:
    trails: tuple[Trail, ...]
    target_vert_ft: float

    @property
    def total_vert_ft(self) -> float:
        return sum(t.vert_ft for t in self.trails)

    @property
    def total_distance_mi(self) -> float:
        return sum(t.distance_mi for t in self.trails)

    @property
    def total_runnable_mi(self) -> float:
        return sum(t.runnable_mi for t in self.trails)

    @property
    def vert_error_frac(self) -> float:
        """Signed: positive means the plan overshoots the target."""
        return (self.total_vert_ft - self.target_vert_ft) / self.target_vert_ft


@dataclass(frozen=True)
class PlanFound:
    plan: Plan


@dataclass(frozen=True)
class ToleranceRecommendation:
    tolerance_frac: float
    num_plans: int


@dataclass(frozen=True)
class NoPlanInTolerance:
    """Some plans have enough runnable miles, but none land within the vert tolerance."""

    closest: Plan
    recommendation: ToleranceRecommendation


@dataclass(frozen=True)
class NotEnoughRunnable:
    """No plan in the outings range has enough runnable miles for the running target."""

    max_runnable_mi: float


@dataclass(frozen=True)
class NoCombinations:
    """There are too few trails to fill any outing count in the range."""


PlanResult = PlanFound | NoPlanInTolerance | NotEnoughRunnable | NoCombinations


def plans_with_outings(trails: list[Trail], num_outings: int, request: PlanRequest) -> Iterator[Plan]:
    choose = itertools.combinations_with_replacement if request.allow_repeats else itertools.combinations
    for combo in choose(trails, num_outings):
        yield Plan(combo, request.target_vert_ft)


def has_enough_runnable(plan: Plan, request: PlanRequest) -> bool:
    return plan.total_runnable_mi >= request.target_running_mi


def is_acceptable(plan: Plan, request: PlanRequest) -> bool:
    return abs(plan.vert_error_frac) <= request.vert_tolerance_frac and has_enough_runnable(plan, request)


def shuffle_plan(trails: list[Trail], request: PlanRequest, rng: random.Random) -> PlanResult:
    outing_counts = list(range(request.min_outings, request.max_outings + 1))
    rng.shuffle(outing_counts)
    for num_outings in outing_counts:
        acceptable = [p for p in plans_with_outings(trails, num_outings, request) if is_acceptable(p, request)]
        if acceptable:
            return PlanFound(rng.choice(acceptable))
    return explain_no_plan(trails, request)


def explain_no_plan(trails: list[Trail], request: PlanRequest) -> PlanResult:
    all_plans = [
        plan
        for num_outings in range(request.min_outings, request.max_outings + 1)
        for plan in plans_with_outings(trails, num_outings, request)
    ]
    if not all_plans:
        return NoCombinations()
    runnable_plans = [p for p in all_plans if has_enough_runnable(p, request)]
    if not runnable_plans:
        return NotEnoughRunnable(max(p.total_runnable_mi for p in all_plans))
    return NoPlanInTolerance(
        closest=min(runnable_plans, key=lambda p: abs(p.vert_error_frac)),
        recommendation=recommend_tolerance(runnable_plans),
    )


def recommend_tolerance(plans: list[Plan]) -> ToleranceRecommendation:
    """The smallest tolerance, in whole steps of TOLERANCE_STEP_FRAC, that admits
    RECOMMENDED_NUM_OPTIONS of `plans` (or all of them, if there are fewer)."""
    errors_frac = sorted(abs(p.vert_error_frac) for p in plans)
    needed_error_frac = errors_frac[min(RECOMMENDED_NUM_OPTIONS, len(errors_frac)) - 1]
    # Rounding before ceil keeps float noise (e.g. 0.03 / 0.005 = 6.000000000000001) from adding a step.
    num_steps = math.ceil(round(needed_error_frac / TOLERANCE_STEP_FRAC, 9))
    tolerance_frac = num_steps * TOLERANCE_STEP_FRAC
    return ToleranceRecommendation(
        tolerance_frac=tolerance_frac,
        num_plans=sum(error_frac <= tolerance_frac for error_frac in errors_frac),
    )
