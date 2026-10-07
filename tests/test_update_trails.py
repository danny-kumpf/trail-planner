import json
import shutil
from pathlib import Path

import pytest

from trail_planner.trails import load_trails
from trail_planner.update_trails import update_trails

SAMPLE_GPX = Path(__file__).resolve().parent.parent / "gpx" / "Green_from_NCAR.gpx"


@pytest.fixture
def workspace(tmp_path):
    gpx_dir = tmp_path / "gpx"
    gpx_dir.mkdir()
    shutil.copy(SAMPLE_GPX, gpx_dir / "Green_from_NCAR.gpx")
    shutil.copy(SAMPLE_GPX, gpx_dir / "New_Trail.gpx")
    trails_path = tmp_path / "trails.json"
    trails_path.write_text(
        json.dumps(
            {
                "green_from_ncar": {"from_gpx": {}, "manual": {"runnable_mi": 1.0}},
                "no_gpx_yet": {"from_gpx": {}, "manual": {"vert_ft": 900}},
            }
        )
    )
    return trails_path, gpx_dir


def test_matches_case_insensitively_and_adds_new_entries(workspace):
    trails_path, gpx_dir = workspace
    report = update_trails(trails_path, gpx_dir)
    assert report.updated == ["green_from_ncar"]
    assert report.added == ["new_trail"]

    entries = json.loads(trails_path.read_text())
    assert entries["green_from_ncar"]["from_gpx"]["vert_ft"] == pytest.approx(2470, abs=30)
    assert entries["green_from_ncar"]["manual"] == {"runnable_mi": 1.0}
    assert entries["new_trail"]["manual"] == {}


def test_manual_values_override_gpx_values(workspace):
    trails_path, gpx_dir = workspace
    update_trails(trails_path, gpx_dir)
    catalog = load_trails(trails_path)
    green = next(t for t in catalog.complete if t.name == "green_from_ncar")
    assert green.runnable_mi == 1.0
    assert catalog.incomplete_names == ["no_gpx_yet"]


def test_keys_differing_only_by_case_rejected(tmp_path):
    trails_path = tmp_path / "trails.json"
    trails_path.write_text(json.dumps({"Bear": {}, "bear": {}}))
    with pytest.raises(ValueError):
        update_trails(trails_path, tmp_path)
