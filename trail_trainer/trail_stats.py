"""Distance, vert, and runnable miles for a GPX route."""

from dataclasses import dataclass
from pathlib import Path

import gpxpy
import numpy as np

EARTH_RADIUS_M = 6_371_000.0
M_PER_MI = 1609.344
FT_PER_M = 3.28084

# Calibrated against AllTrails on Green from NCAR (2,474 ft listed; this gives 2,468 ft).
DEFAULT_GRID_SPACING_M = 10.0
DEFAULT_SMOOTHING_WINDOW_M = 150.0
DEFAULT_MIN_RUNNABLE_GRADE_FRAC = -0.10
DEFAULT_MAX_RUNNABLE_GRADE_FRAC = 0.05


@dataclass(frozen=True)
class TrackStats:
    distance_mi: float
    vert_ft: float
    runnable_mi: float


@dataclass(frozen=True)
class ElevationProfile:
    """Elevation as a function of horizontal distance along a track."""

    along_track_m: np.ndarray
    elevation_m: np.ndarray

    @property
    def length_m(self) -> float:
        return float(self.along_track_m[-1])


def great_circle_steps_m(latitude_deg: np.ndarray, longitude_deg: np.ndarray) -> np.ndarray:
    """Haversine distance between each pair of consecutive points."""
    lat_rad = np.radians(latitude_deg)
    lon_rad = np.radians(longitude_deg)
    half_chord_sq = (
        np.sin(np.diff(lat_rad) / 2) ** 2
        + np.cos(lat_rad[:-1]) * np.cos(lat_rad[1:]) * np.sin(np.diff(lon_rad) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * np.arcsin(np.sqrt(half_chord_sq))


def profile_from_gpx(gpx: gpxpy.gpx.GPX) -> ElevationProfile:
    """Every point of every track, joined in order into one profile."""
    points = [p for track in gpx.tracks for segment in track.segments for p in segment.points]
    if len(points) < 2:
        raise ValueError(f"need at least 2 track points, found {len(points)}")
    if any(p.elevation is None for p in points):
        raise ValueError("some track points have no elevation")

    steps_m = great_circle_steps_m(
        np.array([p.latitude for p in points]), np.array([p.longitude for p in points])
    )
    return profile_from_along_track(
        np.concatenate([[0.0], np.cumsum(steps_m)]), np.array([p.elevation for p in points])
    )


def profile_from_along_track(along_track_m: np.ndarray, elevation_m: np.ndarray) -> ElevationProfile:
    """The profile through the points that have an elevation (NaN where they don't)."""
    has_elevation = ~np.isnan(elevation_m)
    along_track_m = along_track_m[has_elevation]
    elevation_m = elevation_m[has_elevation]
    if len(along_track_m) < 2:
        raise ValueError(f"need at least 2 points with elevation, found {len(along_track_m)}")
    # Points that add no distance would make the profile non-increasing in distance.
    moves = np.concatenate([[True], np.diff(along_track_m) > 0])
    return ElevationProfile(along_track_m[moves], elevation_m[moves])


def read_profile(gpx_path: Path) -> ElevationProfile:
    with open(gpx_path, encoding="utf-8") as f:
        gpx = gpxpy.parse(f)
    try:
        return profile_from_gpx(gpx)
    except ValueError as e:
        raise ValueError(f"{gpx_path}: {e}") from e


def smoothed_on_grid(
    profile: ElevationProfile, grid_spacing_m: float, smoothing_window_m: float
) -> ElevationProfile:
    """Resample at even spacing, then apply a centered moving average to remove elevation noise.

    The grid covers only whole pieces of `grid_spacing_m`; the final partial piece is dropped.
    """
    num_pieces = int(profile.length_m // grid_spacing_m)
    grid_m = np.arange(num_pieces + 1) * grid_spacing_m
    elevation_m = np.interp(grid_m, profile.along_track_m, profile.elevation_m)

    window_len = max(1, round(smoothing_window_m / grid_spacing_m))
    padded_m = np.pad(elevation_m, (window_len // 2, window_len - 1 - window_len // 2), mode="edge")
    smoothed_m = np.convolve(padded_m, np.ones(window_len) / window_len, mode="valid")
    return ElevationProfile(grid_m, smoothed_m)


def stats_from_profile(
    profile: ElevationProfile,
    grid_spacing_m: float = DEFAULT_GRID_SPACING_M,
    smoothing_window_m: float = DEFAULT_SMOOTHING_WINDOW_M,
    min_runnable_grade_frac: float = DEFAULT_MIN_RUNNABLE_GRADE_FRAC,
    max_runnable_grade_frac: float = DEFAULT_MAX_RUNNABLE_GRADE_FRAC,
) -> TrackStats:
    smoothed = smoothed_on_grid(profile, grid_spacing_m, smoothing_window_m)
    rises_m = np.diff(smoothed.elevation_m)
    grades_frac = rises_m / grid_spacing_m
    is_runnable = (grades_frac >= min_runnable_grade_frac) & (grades_frac <= max_runnable_grade_frac)
    return TrackStats(
        distance_mi=profile.length_m / M_PER_MI,
        vert_ft=float(rises_m[rises_m > 0].sum()) * FT_PER_M,
        runnable_mi=float(is_runnable.sum()) * grid_spacing_m / M_PER_MI,
    )


def compute_track_stats(
    gpx_path: Path,
    grid_spacing_m: float = DEFAULT_GRID_SPACING_M,
    smoothing_window_m: float = DEFAULT_SMOOTHING_WINDOW_M,
    min_runnable_grade_frac: float = DEFAULT_MIN_RUNNABLE_GRADE_FRAC,
    max_runnable_grade_frac: float = DEFAULT_MAX_RUNNABLE_GRADE_FRAC,
) -> TrackStats:
    return stats_from_profile(
        read_profile(gpx_path),
        grid_spacing_m,
        smoothing_window_m,
        min_runnable_grade_frac,
        max_runnable_grade_frac,
    )
