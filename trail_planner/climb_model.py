"""Climbing rate as a straight-line function of heart rate, elevation and grade, fit over climb blocks.

Each block counts for the time it lasted rather than as one row: the model is about how fast
terrain gets climbed, so a block should weigh what it represents.
"""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from trail_planner.climb_blocks import ClimbBlock

# One block per coefficient is the least a fit can be solved from.
MIN_FIT_BLOCKS = 4


@dataclass(frozen=True)
class ClimbConditions:
    heart_rate_bpm: float
    elevation_ft: float
    grade_frac: float


@dataclass(frozen=True)
class BlockColumns:
    """Blocks with heart rate, as one array per field."""

    duration_s: np.ndarray
    vert_rate_ft_per_hr: np.ndarray
    heart_rate_bpm: np.ndarray
    elevation_ft: np.ndarray
    grade_frac: np.ndarray

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
        )


@dataclass(frozen=True)
class ClimbModel:
    intercept_ft_per_hr: float
    hr_slope_ft_per_hr_per_bpm: float
    elevation_slope_ft_per_hr_per_ft: float
    grade_slope_ft_per_hr_per_frac: float
    r_squared: float
    num_blocks: int
    # The elevations the fit saw. Outside them, the elevation term is extrapolating.
    elevation_span_ft: tuple[float, float]

    def predict_ft_per_hr(self, heart_rate_bpm, elevation_ft, grade_frac) -> np.ndarray:
        return (
            self.intercept_ft_per_hr
            + self.hr_slope_ft_per_hr_per_bpm * np.asarray(heart_rate_bpm, dtype=float)
            + self.elevation_slope_ft_per_hr_per_ft * np.asarray(elevation_ft, dtype=float)
            + self.grade_slope_ft_per_hr_per_frac * np.asarray(grade_frac, dtype=float)
        )

    def predict_at(self, conditions: ClimbConditions) -> float:
        return float(self.predict_ft_per_hr(conditions.heart_rate_bpm, conditions.elevation_ft, conditions.grade_frac))

    def predict_blocks_ft_per_hr(self, blocks: BlockColumns) -> np.ndarray:
        return self.predict_ft_per_hr(blocks.heart_rate_bpm, blocks.elevation_ft, blocks.grade_frac)


def fit_climb_model(blocks: Sequence[ClimbBlock]) -> ClimbModel:
    """Weighted least squares over blocks that all have a heart rate. Raises ValueError if there
    are too few."""
    if len(blocks) < MIN_FIT_BLOCKS:
        raise ValueError(f"need at least {MIN_FIT_BLOCKS} climb blocks to fit, found {len(blocks)}")
    columns = BlockColumns.of(blocks)
    design = np.column_stack(
        [np.ones(len(blocks)), columns.heart_rate_bpm, columns.elevation_ft, columns.grade_frac]
    )
    root_weights = np.sqrt(columns.duration_s)
    coefficients, *_ = np.linalg.lstsq(
        design * root_weights[:, None], columns.vert_rate_ft_per_hr * root_weights, rcond=None
    )
    residual = columns.vert_rate_ft_per_hr - design @ coefficients
    mean_ft_per_hr = np.average(columns.vert_rate_ft_per_hr, weights=columns.duration_s)
    total_variation = float((columns.duration_s * (columns.vert_rate_ft_per_hr - mean_ft_per_hr) ** 2).sum())
    return ClimbModel(
        intercept_ft_per_hr=float(coefficients[0]),
        hr_slope_ft_per_hr_per_bpm=float(coefficients[1]),
        elevation_slope_ft_per_hr_per_ft=float(coefficients[2]),
        grade_slope_ft_per_hr_per_frac=float(coefficients[3]),
        r_squared=(
            1 - float((columns.duration_s * residual**2).sum()) / total_variation if total_variation > 0 else float("nan")
        ),
        num_blocks=len(blocks),
        elevation_span_ft=(float(columns.elevation_ft.min()), float(columns.elevation_ft.max())),
    )


def at_conditions_ft_per_hr(model: ClimbModel, blocks: BlockColumns, conditions: ClimbConditions) -> np.ndarray:
    """Each block's climbing rate moved to `conditions` along the model's slopes: what the block's
    effort would have climbed there. Their time-weighted mean is the model's prediction at
    `conditions` plus the blocks' mean departure from the model."""
    return model.predict_at(conditions) + blocks.vert_rate_ft_per_hr - model.predict_blocks_ft_per_hr(blocks)


def time_weighted_mean(values: np.ndarray, blocks: BlockColumns) -> float:
    return float(np.average(values, weights=blocks.duration_s))
