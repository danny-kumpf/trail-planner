"""Climbing rate from heart rate, elevation, grade and gait, by way of the metabolic power it takes.

The model is a straight-line fit of metabolic power (W/kg) against heart rate and elevation, over
climb blocks. A block's power is its speed times what its terrain costs per metre at its grade and
gait: Minetti et al. (2002), "Energy cost of walking and running at extreme uphill and downhill
slopes", J Appl Physiol 93:1039-46, measured on a treadmill from -45% to +45% grade. Grade and gait
enter only through that cost. Fit as a further term, grade barely changed predictions for efforts
left out of the fit, so it isn't one. Power isn't shown anywhere: every prediction is turned back
into a climbing rate.

Each block counts for the time it lasted rather than as one row: the model is about how fast
terrain gets climbed, so a block should weigh what it represents.
"""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from trail_planner.climb_blocks import ClimbBlock
from trail_planner.trail_stats import FT_PER_M

# One block per coefficient is the least a fit can be solved from.
MIN_FIT_BLOCKS = 3
S_PER_HR = 3600.0


def walking_cost_j_per_kg_per_m(grade_frac) -> np.ndarray:
    """Minetti's metabolic cost of walking a metre at a gradient."""
    i = np.asarray(grade_frac, dtype=float)
    return 280.5 * i**5 - 58.7 * i**4 - 76.8 * i**3 + 51.9 * i**2 + 19.6 * i + 2.5


def running_cost_j_per_kg_per_m(grade_frac) -> np.ndarray:
    """Minetti's metabolic cost of running a metre at a gradient."""
    i = np.asarray(grade_frac, dtype=float)
    return 155.4 * i**5 - 30.4 * i**4 - 43.3 * i**3 + 46.3 * i**2 + 19.5 * i + 3.6


def cost_j_per_kg_per_m(grade_frac, running_frac) -> np.ndarray:
    """The cost of a stretch that was `running_frac` run and the rest walked, weighting each gait's
    cost by its share of the time."""
    running_frac = np.asarray(running_frac, dtype=float)
    return running_frac * running_cost_j_per_kg_per_m(grade_frac) + (1 - running_frac) * walking_cost_j_per_kg_per_m(
        grade_frac
    )


def ft_per_hr_per_w_per_kg(grade_frac, running_frac) -> np.ndarray:
    """The climbing rate each W/kg buys on terrain of this grade, at this gait."""
    return np.asarray(grade_frac, dtype=float) * FT_PER_M * S_PER_HR / cost_j_per_kg_per_m(grade_frac, running_frac)


@dataclass(frozen=True)
class ClimbConditions:
    heart_rate_bpm: float
    elevation_ft: float
    grade_frac: float
    running_frac: float


@dataclass(frozen=True)
class BlockColumns:
    """Blocks with heart rate, as one array per field."""

    duration_s: np.ndarray
    vert_rate_ft_per_hr: np.ndarray
    heart_rate_bpm: np.ndarray
    elevation_ft: np.ndarray
    grade_frac: np.ndarray
    running_frac: np.ndarray

    @classmethod
    def of(cls, blocks: Sequence[ClimbBlock]) -> "BlockColumns":
        if any(b.heart_rate_bpm is None for b in blocks):
            raise ValueError("every block needs a heart rate")
        return cls(
            duration_s=np.array([b.duration_s for b in blocks], dtype=float),
            vert_rate_ft_per_hr=np.array([b.vert_rate_ft_per_hr for b in blocks], dtype=float),
            heart_rate_bpm=np.array([b.heart_rate_bpm for b in blocks], dtype=float),
            elevation_ft=np.array([b.elevation_ft for b in blocks], dtype=float),
            grade_frac=np.array([b.grade_frac for b in blocks], dtype=float),
            running_frac=np.array([b.running_frac for b in blocks], dtype=float),
        )

    @property
    def power_w_per_kg(self) -> np.ndarray:
        return self.vert_rate_ft_per_hr / ft_per_hr_per_w_per_kg(self.grade_frac, self.running_frac)


@dataclass(frozen=True)
class ClimbModel:
    intercept_w_per_kg: float
    hr_slope_w_per_kg_per_bpm: float
    elevation_slope_w_per_kg_per_ft: float
    # Of climbing rate, which is what the model is for, though it's fit to power.
    r_squared: float
    num_blocks: int
    # The elevations the fit saw. Outside them, the elevation term is extrapolating.
    elevation_span_ft: tuple[float, float]

    def predict_w_per_kg(self, heart_rate_bpm, elevation_ft) -> np.ndarray:
        return (
            self.intercept_w_per_kg
            + self.hr_slope_w_per_kg_per_bpm * np.asarray(heart_rate_bpm, dtype=float)
            + self.elevation_slope_w_per_kg_per_ft * np.asarray(elevation_ft, dtype=float)
        )

    def predict_ft_per_hr(self, heart_rate_bpm, elevation_ft, grade_frac, running_frac) -> np.ndarray:
        return self.predict_w_per_kg(heart_rate_bpm, elevation_ft) * ft_per_hr_per_w_per_kg(grade_frac, running_frac)

    def predict_at(self, conditions: ClimbConditions) -> float:
        return float(
            self.predict_ft_per_hr(
                conditions.heart_rate_bpm, conditions.elevation_ft, conditions.grade_frac, conditions.running_frac
            )
        )

    def predict_blocks_ft_per_hr(self, blocks: BlockColumns) -> np.ndarray:
        return self.predict_ft_per_hr(blocks.heart_rate_bpm, blocks.elevation_ft, blocks.grade_frac, blocks.running_frac)

    def hr_slope_ft_per_hr_per_bpm(self, conditions: ClimbConditions) -> float:
        """How much faster each extra bpm climbs at `conditions`' grade and gait."""
        return self.hr_slope_w_per_kg_per_bpm * float(ft_per_hr_per_w_per_kg(conditions.grade_frac, conditions.running_frac))

    def elevation_slope_ft_per_hr_per_ft(self, conditions: ClimbConditions) -> float:
        """How much faster each extra foot of elevation climbs at `conditions`' grade and gait."""
        return self.elevation_slope_w_per_kg_per_ft * float(
            ft_per_hr_per_w_per_kg(conditions.grade_frac, conditions.running_frac)
        )


def fit_climb_model(blocks: Sequence[ClimbBlock]) -> ClimbModel:
    """Weighted least squares over blocks that all have a heart rate. Raises ValueError if there
    are too few."""
    if len(blocks) < MIN_FIT_BLOCKS:
        raise ValueError(f"need at least {MIN_FIT_BLOCKS} climb blocks to fit, found {len(blocks)}")
    columns = BlockColumns.of(blocks)
    design = np.column_stack([np.ones(len(blocks)), columns.heart_rate_bpm, columns.elevation_ft])
    root_weights = np.sqrt(columns.duration_s)
    coefficients, *_ = np.linalg.lstsq(design * root_weights[:, None], columns.power_w_per_kg * root_weights, rcond=None)
    predicted_ft_per_hr = design @ coefficients * ft_per_hr_per_w_per_kg(columns.grade_frac, columns.running_frac)
    residual = columns.vert_rate_ft_per_hr - predicted_ft_per_hr
    mean_ft_per_hr = np.average(columns.vert_rate_ft_per_hr, weights=columns.duration_s)
    total_variation = float((columns.duration_s * (columns.vert_rate_ft_per_hr - mean_ft_per_hr) ** 2).sum())
    return ClimbModel(
        intercept_w_per_kg=float(coefficients[0]),
        hr_slope_w_per_kg_per_bpm=float(coefficients[1]),
        elevation_slope_w_per_kg_per_ft=float(coefficients[2]),
        r_squared=(
            1 - float((columns.duration_s * residual**2).sum()) / total_variation if total_variation > 0 else float("nan")
        ),
        num_blocks=len(blocks),
        elevation_span_ft=(float(columns.elevation_ft.min()), float(columns.elevation_ft.max())),
    )


def at_conditions_ft_per_hr(model: ClimbModel, blocks: BlockColumns, conditions: ClimbConditions) -> np.ndarray:
    """Each block's climbing rate moved to `conditions` through the model: the power the model
    predicts at `conditions`, plus how far the block's own power departed from the model's
    prediction for it, spent on `conditions`' grade and gait. Their time-weighted mean is the
    model's prediction at `conditions` plus the blocks' mean departure from the model."""
    departure_w_per_kg = blocks.power_w_per_kg - model.predict_w_per_kg(blocks.heart_rate_bpm, blocks.elevation_ft)
    at_conditions_w_per_kg = model.predict_w_per_kg(conditions.heart_rate_bpm, conditions.elevation_ft) + departure_w_per_kg
    return at_conditions_w_per_kg * ft_per_hr_per_w_per_kg(conditions.grade_frac, conditions.running_frac)


def time_weighted_mean(values: np.ndarray, blocks: BlockColumns) -> float:
    return float(np.average(values, weights=blocks.duration_s))
