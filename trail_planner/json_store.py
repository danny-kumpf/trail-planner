"""Reading and writing the JSON files that hold entries keyed by name or id."""

import json
from pathlib import Path


def read_entries(path: Path) -> dict[str, dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_entries(path: Path, entries: dict[str, dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=4)
        f.write("\n")
