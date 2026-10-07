"""Loading the trail catalog from available_trails.json.

Each entry has a `from_gpx` block (written by update_trails) and a `manual` block (hand
overrides, never written by code). A field's value is the manual one if set, else the GPX one.
"""

from dataclasses import dataclass
from pathlib import Path

from trail_planner.json_store import read_entries

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TRAILS_PATH = PROJECT_ROOT / "available_trails.json"
DEFAULT_GPX_DIR = PROJECT_ROOT / "gpx"

TRAIL_FIELDS = ("distance_mi", "vert_ft", "runnable_mi")


@dataclass(frozen=True)
class Trail:
    name: str
    distance_mi: float
    vert_ft: float
    runnable_mi: float


@dataclass(frozen=True)
class TrailCatalog:
    complete: list[Trail]
    incomplete_names: list[str]


def effective_fields(entry: dict) -> dict[str, float]:
    """The fields that have a value, taking manual overrides over GPX-derived values."""
    return {**entry.get("from_gpx", {}), **entry.get("manual", {})}


def load_trails(path: Path = DEFAULT_TRAILS_PATH) -> TrailCatalog:
    complete: list[Trail] = []
    incomplete_names: list[str] = []
    for name, entry in read_entries(path).items():
        fields = effective_fields(entry)
        if all(field in fields for field in TRAIL_FIELDS):
            complete.append(Trail(name, *(float(fields[field]) for field in TRAIL_FIELDS)))
        else:
            incomplete_names.append(name)
    return TrailCatalog(complete, incomplete_names)
