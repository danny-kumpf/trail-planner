"""Climbing fitness: a climbing model kept up to date from recent efforts, and how each effort compares.

An effort is an on-foot activity with at least MIN_EFFORT_CLIMBING_S of climbing the model covers:
climbing with a heart rate of at least MIN_MODELLED_HEART_RATE_BPM. Each effort is scored two ways:
- At standard conditions: its climbing moved through the model to STANDARD_CONDITIONS, so efforts
  on different terrain, at different heart rates and gaits, read in the same ft/hr.
- Against the model: how much more it climbed than the model predicted for its own heart rate,
  elevation, grade and gait. An effort the model is fit from is compared with the model fit
  without it, or it would partly be compared with itself.

Climb blocks take about half a second per recording to compute, so they're stored in
climb_blocks.json along with what they were computed from, and recomputed only when that changes.
"""

import json
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from pathlib import Path

from trail_planner.activity_stats import kept_recording
from trail_planner.climb_blocks import CLIMB_ANALYSIS_VERSION, ClimbAnalysis, ClimbBlock, analyse_climbs
from trail_planner.climb_model import (
    BlockColumns,
    ClimbConditions,
    ClimbModel,
    at_conditions_ft_per_hr,
    fit_climb_model,
    time_weighted_mean,
)
from trail_planner.training_log import Activity, ModelChoice, activity_from_entry, read_log
from trail_planner.trails import PROJECT_ROOT

DEFAULT_CLIMB_BLOCKS_PATH = PROJECT_ROOT / "climb_blocks.json"
# The model is fit from the efforts in this long before the latest one...
MODEL_WINDOW = timedelta(days=60)
# ...or from this many of the latest efforts, if the window holds fewer.
MIN_RECENT_EFFORTS = 5
# Less climbing than this says too little about an effort to score it.
MIN_EFFORT_CLIMBING_S = 600.0
# Climbing at a lower heart rate is too easy to be worth modelling, and noisy: there, the model's
# straight line in heart rate predicted 20-40% too little climbing. It's left out of the model and
# the scores.
MIN_MODELLED_HEART_RATE_BPM = 120.0
STANDARD_CONDITIONS = ClimbConditions(heart_rate_bpm=150.0, elevation_ft=8000.0, grade_frac=0.15, running_frac=0.0)
BLOCK_FIELDS = (
    "start_s",
    "duration_s",
    "vert_rate_ft_per_hr",
    "elevation_ft",
    "grade_frac",
    "heart_rate_bpm",
    "running_frac",
)


@dataclass(frozen=True)
class Effort:
    activity: Activity
    # Only the blocks the model covers.
    blocks: list[ClimbBlock]

    @property
    def id(self) -> str:
        return self.activity.id

    @property
    def columns(self) -> BlockColumns:
        return BlockColumns.of(self.blocks)

    @property
    def climbing_s(self) -> float:
        return sum(b.duration_s for b in self.blocks)

    @property
    def avg_heart_rate_bpm(self) -> float:
        return time_weighted_mean(self.columns.heart_rate_bpm, self.columns)

    @property
    def elevation_span_ft(self) -> tuple[float, float]:
        return min(b.elevation_ft for b in self.blocks), max(b.elevation_ft for b in self.blocks)


class Membership(StrEnum):
    """Why an effort is one the model is fit from."""

    REQUIRED = "required"
    RECENT = "recent"


@dataclass(frozen=True)
class EffortScore:
    # Its climbing moved to STANDARD_CONDITIONS through the model.
    at_standard_ft_per_hr: float
    # How much more it climbed than the comparison model predicted for the same time; None if
    # there are too few other efforts to fit one without it.
    vs_model_frac: float | None
    # The share of its climbing at elevations the comparison model wasn't fit on.
    outside_model_elevations_frac: float | None


@dataclass(frozen=True)
class FitnessOverview:
    # Newest first.
    efforts: list[Effort]
    membership: dict[str, Membership]
    model: ClimbModel | None
    # Why there's no model, when there isn't one.
    no_model_reason: str | None
    scores: dict[str, EffortScore]
    # Activities whose recordings couldn't be analysed, with why.
    unreadable: dict[str, str]

    def effort(self, activity_id: str) -> Effort | None:
        return next((e for e in self.efforts if e.id == activity_id), None)

    def comparison_model(self, effort: Effort) -> ClimbModel | None:
        return comparison_model(effort, self.efforts, self.membership, self.model)


def modelled_blocks(blocks: list[ClimbBlock]) -> list[ClimbBlock]:
    """The blocks the model covers: those with a heart rate of at least MIN_MODELLED_HEART_RATE_BPM."""
    return [b for b in blocks if b.heart_rate_bpm is not None and b.heart_rate_bpm >= MIN_MODELLED_HEART_RATE_BPM]


# ---- Stored climb blocks ----


def could_be_effort(activity: Activity, entry: dict) -> bool:
    """On foot, with a recording that has heart rate."""
    return (
        activity.type.is_on_foot
        and activity.avg_heart_rate_bpm is not None
        and bool(entry.get("recording_file"))
        and "strava" in entry
    )


def analysis_inputs(entry: dict) -> dict:
    """What an activity's climb blocks are computed from. Stored blocks are reused while it matches."""
    return {
        "version": CLIMB_ANALYSIS_VERSION,
        "recording_file": entry["recording_file"],
        "start_utc": entry["strava"]["start_utc"],
        "elapsed_time_s": entry["strava"]["elapsed_time_s"],
    }


def stored_analysis(entry: dict, activity_files_dir: Path) -> dict:
    inputs = analysis_inputs(entry)
    try:
        blocks = analyse_climbs(kept_recording(entry, activity_files_dir)).blocks
    except (ValueError, OSError) as e:
        return {"inputs": inputs, "error": str(e)}
    return {"inputs": inputs, "blocks": [[getattr(b, field) for field in BLOCK_FIELDS] for b in blocks]}


def climb_blocks_by_activity(
    entries: dict[str, dict], activity_ids: list[str], activity_files_dir: Path, store_path: Path
) -> tuple[dict[str, list[ClimbBlock]], dict[str, str]]:
    """Each activity's climb blocks, computed only if they aren't stored from the same inputs; and
    the activities whose recordings couldn't be analysed, with why."""
    stored = json.loads(store_path.read_text(encoding="utf-8")) if store_path.exists() else {}
    changed = False
    for activity_id in activity_ids:
        if stored.get(activity_id, {}).get("inputs") != analysis_inputs(entries[activity_id]):
            stored[activity_id] = stored_analysis(entries[activity_id], activity_files_dir)
            changed = True
    if gone := set(stored) - set(entries):
        for activity_id in gone:
            del stored[activity_id]
        changed = True
    if changed:
        store_path.write_text(json.dumps(stored), encoding="utf-8")

    blocks: dict[str, list[ClimbBlock]] = {}
    unreadable: dict[str, str] = {}
    for activity_id in activity_ids:
        if "error" in stored[activity_id]:
            unreadable[activity_id] = stored[activity_id]["error"]
        else:
            blocks[activity_id] = [ClimbBlock(**dict(zip(BLOCK_FIELDS, row))) for row in stored[activity_id]["blocks"]]
    return blocks, unreadable


# ---- The model and the scores ----


def model_membership(efforts: list[Effort]) -> dict[str, Membership]:
    """The efforts the model is fit from: the required ones, and the latest ones not excluded,
    either within MODEL_WINDOW of the latest or the latest MIN_RECENT_EFFORTS, whichever is more."""
    candidates = sorted(
        (e for e in efforts if e.activity.model_choice != ModelChoice.EXCLUDE),
        key=lambda e: e.activity.start_local,
        reverse=True,
    )
    membership: dict[str, Membership] = {}
    if candidates:
        window_start = candidates[0].activity.start_local - MODEL_WINDOW
        for i, effort in enumerate(candidates):
            if i < MIN_RECENT_EFFORTS or effort.activity.start_local >= window_start:
                membership[effort.id] = Membership.RECENT
    for effort in efforts:
        if effort.activity.model_choice == ModelChoice.REQUIRE:
            membership[effort.id] = Membership.REQUIRED
    return membership


def model_blocks(
    efforts: list[Effort], membership: dict[str, Membership], leaving_out: str | None = None
) -> list[ClimbBlock]:
    return [b for e in efforts if e.id in membership and e.id != leaving_out for b in e.blocks]


def comparison_model(
    effort: Effort, efforts: list[Effort], membership: dict[str, Membership], model: ClimbModel | None
) -> ClimbModel | None:
    """The model to compare an effort with: fit without it, if it's one the model is fit from. None
    if there's no model, or too little left to fit one without it."""
    if model is None or effort.id not in membership:
        return model
    try:
        return fit_climb_model(model_blocks(efforts, membership, leaving_out=effort.id))
    except ValueError:
        return None


def outside_elevations_frac(effort: Effort, model: ClimbModel) -> float:
    low_ft, high_ft = model.elevation_span_ft
    columns = effort.columns
    outside = (columns.elevation_ft < low_ft) | (columns.elevation_ft > high_ft)
    return float(columns.duration_s[outside].sum() / columns.duration_s.sum())


def vs_model_frac(effort: Effort, model: ClimbModel) -> float | None:
    """Vert climbed over vert predicted, less one; None where the model predicts no climbing."""
    columns = effort.columns
    predicted_ft = float((model.predict_blocks_ft_per_hr(columns) * columns.duration_s).sum())
    if predicted_ft <= 0:
        return None
    return float((columns.vert_rate_ft_per_hr * columns.duration_s).sum()) / predicted_ft - 1


def effort_score(effort: Effort, model: ClimbModel, comparison: ClimbModel | None) -> EffortScore:
    columns = effort.columns
    return EffortScore(
        at_standard_ft_per_hr=time_weighted_mean(at_conditions_ft_per_hr(model, columns, STANDARD_CONDITIONS), columns),
        vs_model_frac=None if comparison is None else vs_model_frac(effort, comparison),
        outside_model_elevations_frac=None if comparison is None else outside_elevations_frac(effort, comparison),
    )


def log_efforts(log_path: Path, activity_files_dir: Path, store_path: Path) -> tuple[list[Effort], dict[str, str]]:
    """Every effort in the training log, newest first; and the activities whose recordings
    couldn't be analysed, with why."""
    entries = read_log(log_path)
    candidates = [
        activity
        for activity_id, entry in entries.items()
        if could_be_effort(activity := activity_from_entry(activity_id, entry), entry)
    ]
    blocks, unreadable = climb_blocks_by_activity(entries, [a.id for a in candidates], activity_files_dir, store_path)
    efforts = sorted(
        (
            effort
            for activity in candidates
            if activity.id in blocks
            and (effort := Effort(activity, modelled_blocks(blocks[activity.id]))).climbing_s
            >= MIN_EFFORT_CLIMBING_S
        ),
        key=lambda e: e.activity.start_local,
        reverse=True,
    )
    return efforts, unreadable


def fitness_overview(log_path: Path, activity_files_dir: Path, store_path: Path) -> FitnessOverview:
    efforts, unreadable = log_efforts(log_path, activity_files_dir, store_path)
    membership = model_membership(efforts)
    try:
        model = fit_climb_model(model_blocks(efforts, membership))
    except ValueError as e:
        return FitnessOverview(efforts, membership, None, str(e), {}, unreadable)
    scores = {e.id: effort_score(e, model, comparison_model(e, efforts, membership, model)) for e in efforts}
    return FitnessOverview(efforts, membership, model, None, scores, unreadable)


def effort_analysis(activity_id: str, log_path: Path, activity_files_dir: Path) -> ClimbAnalysis:
    """An activity's climbing in full, from its recording, for looking at it closely."""
    entry = read_log(log_path)[activity_id]
    return analyse_climbs(kept_recording(entry, activity_files_dir))
