# Trail Trainer: notes for working on the code

## Where documentation goes

- `README.md` is for a new user: what the app does and how to get it running with their own trails and Strava data. Keep implementation details out of it.
- `docs/trail_planning.md`, `docs/training_log.md`, `docs/fitness.md`: one per tab. What the tab's numbers mean and how they're computed, written independently of the code. Don't document things the page makes obvious (swapping an outing, saving a plan).
- `docs/fitness_model.md`: the climbing model itself.
- This file: code layout, data file formats, and what to do after changing a method.

When a change alters how something is computed, update the matching doc in `docs/`.

## Commands

```
uv sync                                      # install
uv run python -m trail_trainer.app           # run at http://localhost:5000
uv run pytest                                # tests
uv run python -m trail_trainer.update_trails # recompute trail stats from gpx/
uv run python -m trail_trainer.import_strava_export <export.zip>
uv run python -m trail_trainer.import_activity_files [--type TYPE] <file or folder>...
uv run python -m trail_trainer.rebuild_training_log
uv run python -m trail_trainer.readme_screenshots
```

## Project layout

| Path | Purpose |
|---|---|
| `available_trails.json` | Trail catalog: GPX-derived stats plus manual overrides |
| `gpx/` | Route GPX files, one per trail |
| `trail_trainer/trail_stats.py` | GPX → distance, vert, runnable miles (smoothing and grade settings live here) |
| `trail_trainer/update_trails.py` | Script that writes GPX stats into the catalog |
| `trail_trainer/trails.py` | Loads the catalog, applying manual overrides |
| `trail_trainer/planner.py` | Shuffle algorithm |
| `trail_trainer/swaps.py` | Swap options for one outing of a plan |
| `week_plans.json` | Plans saved for each week, with the settings they were shuffled with |
| `trail_trainer/week_plans.py` | Saves and loads the plans for each week |
| `trail_trainer/effort.py` | Recording → running and hiking miles (by cadence, or by pace alone), heart rate |
| `training_log.json` | Training log: each activity's source, our numbers and the user's edits |
| `activity_files/` | Each activity's recording (FIT or GPX from the export or a file import, or streams from a sync), named by entry id |
| `trail_trainer/recording.py` | Reads FIT and GPX files and Strava's streams into one form, and what a file says about its activity (name, sport); moving time |
| `trail_trainer/activity_stats.py` | Source block + recording → the numbers the training log shows |
| `trail_trainer/training_log.py` | Loads the training log, applying overrides; validates edits |
| `trail_trainer/import_strava_export.py` | Script that imports a Strava account export into the training log |
| `trail_trainer/import_activity_files.py` | Imports individual FIT/GPX files (or zips of them) from any source into the training log; a script, and behind the Training Log's **Add activity files** button (`POST /api/activities/files`) |
| `trail_trainer/rebuild_training_log.py` | Script that recomputes the training log from the stored recordings |
| `trail_trainer/strava_api.py` | Strava's API: approving access, refreshing tokens, reading activities |
| `trail_trainer/strava_sync.py` | Brings the training log up to date with Strava |
| `strava_app.json`, `strava_tokens.json` | The user's Strava API application, and the access it was given (private, git-ignored) |
| `trail_trainer/climb_signals.py` | Recording → vertical rate, grade, heart rate and gait on an even time grid |
| `trail_trainer/climb_blocks.py` | Sustained climbs, cut into the 45 s blocks the model is fit on (heart rate lag lives here) |
| `trail_trainer/climb_model.py` | The climbing model: power from heart rate and elevation, turned into a climbing rate by Minetti's cost of grade and gait |
| `trail_trainer/fitness.py` | Which efforts the model uses, each effort's scores, and the stored climb blocks |
| `trail_trainer/fitness_plots.py` | The Fitness tab's figures |
| `climb_blocks.json` | Each activity's climb blocks, cached so they needn't be recomputed |
| `trail_trainer/json_store.py` | Reading and writing the JSON data files |
| `trail_trainer/app.py` | Flask server for the pages and API |
| `trail_trainer/static/site.css` | Styles shared by every tab, including the tab bar |
| `trail_trainer/static/calendar.js` | Calendar days and weeks, and the week start day, shared by both tabs |
| `trail_trainer/static/trail_planning.html` | The Trail Planning tab |
| `trail_trainer/static/training_log.html` | The Training Log tab |
| `trail_trainer/static/fitness.html`, `fitness_activity.html` | The Fitness tab, and an activity's Explore page |
| `trail_trainer/static/fitness.js` | Drawing figures and formatting, shared by the Fitness pages |
| `trail_trainer/readme_screenshots.py` | Script that renders the screenshots in `docs/screenshots/` from the running site |
| `docs/` | User-facing docs for each tab and the fitness model, and the screenshots |
| `.githooks/pre-commit` | Re-renders the screenshots when a commit changes the site |
| `tests/` | Unit tests |

The user's data files (`training_log.json`, `week_plans.json`, `activity_files/`, `recorded_activities/`, `climb_blocks.json`, the Strava credentials) are git-ignored. `gpx/` and `available_trails.json` are tracked and hold the author's trails as an example.

## Data formats

### `available_trails.json`

```json
"green_from_ncar": {
    "from_gpx": {"distance_mi": 11.42, "vert_ft": 2468, "runnable_mi": 5.65},
    "manual": {}
}
```

- `from_gpx` is rewritten every time `update_trails` runs. It recalculates every GPX file; a file matching an existing entry (ignoring case) updates it, and a new file gets a new entry named after the file in lowercase.
- `manual` is never touched by the script, and its values take priority.
- A trail can be planned once it has all three fields, from either block.

### `week_plans.json`

Saved plans, keyed by the week's first day, each with the settings it was shuffled with (including required and excluded trails). Each saved trail keeps the stats it had when saved, so updating trails later doesn't change a saved plan.

### `training_log.json`

Each entry has exactly one source block, `strava` or `file` (see `Source` in `training_log.py`), plus `recording_file`, `recorded` and `manual`. An activity from Strava:

```json
"20455753192": {
    "strava": {"name": "Mallory Cave with Jess", "type": "Hike", "start_utc": "2026-10-04T21:25:55+00:00",
               "start_local": null, "moving_time_s": 5807.0, "elapsed_time_s": 6580.0, "distance_m": 5127.2,
               "elevation_gain_m": 318.8, "avg_heart_rate_bpm": 109.0, "max_heart_rate_bpm": 138.0,
               "workout_type": null, "private_note": null, "description": null},
    "recording_file": "20455753192.fit.gz",
    "recorded": {"name": "Mallory Cave with Jess", "type": "hike", "start_local": "2026-10-04T15:25:55",
                 "moving_time_s": 5921, "distance_mi": 3.23, "vert_ft": 942, "running_mi": 0.02,
                 "avg_heart_rate_bpm": 107, "max_heart_rate_bpm": 138},
    "manual": {"grade": "B", "model_choice": "exclude"}
}
```

- `strava` is Strava's own summary, as the export or a sync gives it. Its numbers aren't shown; they're kept for reference and for comparing with ours. `workout_type` is how Strava marks a race. The export doesn't give it, so it's `null` until the first sync.
- `recording_file` is the activity's recording in `activity_files/`: a FIT or GPX file from the export or a file import, or `<id>.streams.json.gz` from a sync. It is `null` for an activity entered by hand on Strava.
- `file` replaces `strava` for an activity imported from a file. Its entry is keyed by `file-` and its UTC start, e.g. `file-20261004T212555Z`:

  ```json
  "file": {"name": "Mallory Cave", "type": "hike", "start_utc": "2026-10-04T21:25:55+00:00",
           "file_name": "Mallory_Cave.fit"}
  ```

  `type` is already the log's type (from `--type` or the file's sport), and the whole recording is used, with no crop.
- `recorded` is what the page shows. It is computed from `strava` and the recording file, and rewritten whenever they change.
- `manual` holds the user's edits. It's written only when they save on the page, and never touched by imports or syncs. Its `model_choice` is the activity's Require (`"require"`) or Exclude (`"exclude"`) on the Fitness tab.

### One activity from several sources

Entries starting within `SAME_ACTIVITY_MAX_START_DIFFERENCE` (60 s) of each other are the same activity. A file import skips an activity already from Strava and updates one already from a file. An export import or sync that adds a Strava activity replaces a file entry for it, moving its `manual` over (`replace_file_entries`).

### Sync details

- Listing summaries takes one request per 200 activities; each new activity takes two more (details for its private note, and its streams). Strava allows 100 reads per 15 minutes; a sync that hits the limit keeps what it added and reports how many are left.
- Streams are stored in `activity_files/`, so a rebuild never downloads them again.
- Sync refreshes existing entries' `strava` block and recomputes `recorded` only for entries whose summary changed.

## After changing a method

- **Trail stats** (`trail_stats.py`): run `update_trails`, and `rebuild_training_log`, since activity vert uses the same method.
- **Activity numbers** (`activity_stats.py`, `effort.py`, `recording.py`): run `rebuild_training_log` to recompute every activity, from either source, from the stored files. It keeps `manual` edits.
- **Climbs or climb blocks** (`climb_signals.py`, `climb_blocks.py`): increase `CLIMB_ANALYSIS_VERSION` in `climb_blocks.py` so cached blocks are recomputed. Each `climb_blocks.json` entry records the recording, Strava's crop (for an activity from Strava) and the analysis version it was computed from, and is redone when any of them changes. Deleting the file has the same effect.
- **Heart rate monitor**: `HR_LAG_S` in `climb_blocks.py` is a typical lag, not a measured one; revisit it if the monitor changes.

## Screenshots

The screenshots in `docs/screenshots/` are rendered from the running site by a headless browser, using this checkout's data. Setup, once per clone:

```
uv run playwright install chromium
git config core.hooksPath .githooks
```

From then on, any commit that changes something in `trail_trainer/` re-renders them and adds them to the commit (about 10 seconds). Skip with `git commit --no-verify`, or render without committing with `uv run python -m trail_trainer.readme_screenshots`.
