"""Recompute each trail's `from_gpx` stats from the GPX files, adding entries for new files.

Usage: uv run python -m trail_planner.update_trails
"""

from dataclasses import dataclass
from pathlib import Path

from trail_planner.trail_stats import compute_track_stats
from trail_planner.json_store import read_entries, write_entries
from trail_planner.trails import DEFAULT_GPX_DIR, DEFAULT_TRAILS_PATH


@dataclass(frozen=True)
class UpdateReport:
    updated: list[str]
    added: list[str]


def keys_by_lowercase(entries: dict[str, dict]) -> dict[str, str]:
    """Map each lowercased key to the key as written, rejecting keys that differ only by case."""
    by_lowercase: dict[str, str] = {}
    for key in entries:
        if key.lower() in by_lowercase:
            raise ValueError(f"Trail names differ only by case: {by_lowercase[key.lower()]!r}, {key!r}")
        by_lowercase[key.lower()] = key
    return by_lowercase


def update_trails(trails_path: Path, gpx_dir: Path) -> UpdateReport:
    entries = read_entries(trails_path)
    existing_keys = keys_by_lowercase(entries)
    updated: list[str] = []
    added: list[str] = []

    for gpx_path in sorted(gpx_dir.glob("*.gpx")):
        stats = compute_track_stats(gpx_path)
        from_gpx = {
            "distance_mi": round(stats.distance_mi, 2),
            "vert_ft": round(stats.vert_ft),
            "runnable_mi": round(stats.runnable_mi, 2),
        }
        lowercase_name = gpx_path.stem.lower()
        if lowercase_name in existing_keys:
            key = existing_keys[lowercase_name]
            entries[key]["from_gpx"] = from_gpx
            entries[key].setdefault("manual", {})
            updated.append(key)
        else:
            entries[lowercase_name] = {"from_gpx": from_gpx, "manual": {}}
            existing_keys[lowercase_name] = lowercase_name
            added.append(lowercase_name)

    write_entries(trails_path, entries)
    return UpdateReport(updated, added)


def main() -> None:
    report = update_trails(DEFAULT_TRAILS_PATH, DEFAULT_GPX_DIR)
    print(f"Updated {len(report.updated)} trail(s): {', '.join(report.updated) or '-'}")
    print(f"Added {len(report.added)} new trail(s): {', '.join(report.added) or '-'}")


if __name__ == "__main__":
    main()
