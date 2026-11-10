"""Recompute every activity's `recorded` block from its source block and recording file.

Usage: uv run python -m trail_trainer.rebuild_training_log

Run it after changing how vert, running or moving time are computed. Hand edits are untouched.
"""

from pathlib import Path

from trail_trainer.activity_stats import recorded_for_entry
from trail_trainer.json_store import write_entries
from trail_trainer.training_log import DEFAULT_ACTIVITY_FILES_DIR, DEFAULT_LOG_PATH, read_log


def rebuild_training_log(log_path: Path, activity_files_dir: Path) -> dict[str, str]:
    """Rebuild every entry; returns the ones that failed, with why."""
    entries = read_log(log_path)
    failed: dict[str, str] = {}
    for activity_id, entry in entries.items():
        try:
            entry["recorded"] = recorded_for_entry(entry, activity_files_dir)
        except (ValueError, OSError) as e:
            failed[activity_id] = str(e)
    write_entries(log_path, entries)
    return failed


def main() -> None:
    failed = rebuild_training_log(DEFAULT_LOG_PATH, DEFAULT_ACTIVITY_FILES_DIR)
    print(f"Rebuilt the training log; {len(failed)} failed.")
    for activity_id, error in failed.items():
        print(f"Failed {activity_id}: {error}")


if __name__ == "__main__":
    main()
