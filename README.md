# Trail Planner

A weekly training planner for trail running and hiking. You give it the vert and running miles you want this week, and it picks a set of trails that hits the vert within a small tolerance and has enough runnable terrain for the running target.

Why: when every workout is a trail outing of at least an hour, you can't raise the weekly load by adding 10 minutes to a run. But trails differ in vert, so combinations of them give fine-grained control over the weekly total. This tool searches those combinations.

It plans single weeks only. Long-term progression (e.g. +10% vert per week) is up to you.

## How it works

**Trail stats.** Each trail's distance, vert and runnable miles come from a GPX file of the route. The elevation profile is resampled every 10 m and smoothed with a 150 m moving average, because raw GPS/map elevation has small wiggles that inflate gain. From the smoothed profile:

- **vert** is the sum of all climbing;
- **runnable miles** is the distance on grades between −10% and +5%.

Two calibration checks against AllTrails: Green from NCAR gives 2,468 ft (AllTrails lists 2,474 ft), and Lions Lair gives 910 ft (AllTrails lists 912 ft).

**Planning.** Each click of **Shuffle** does this:

1. Pick a random number of outings from the allowed range.
2. Find every combination of that many trails that meets both conditions:
   - total vert is within the tolerance of the target (default ±3%);
   - total runnable miles is at least the running target.
3. Return one of them at random. If none exist, try another number of outings.

The number of outings is picked first so that, say, 3-outing and 6-outing weeks are equally likely, even though there are far more 6-trail combinations. The tolerance is symmetric, so small misses should cancel out over several weeks.

If nothing fits, the page tells you why. Either it shows the closest plan with enough runnable miles, or it reports the most runnable miles any plan has.

The tool only checks that enough runnable terrain exists. Which descents to actually run is up to you.

**Mid-week replanning.** The [Training Log](#training-log) tab shows how much vert and running you've done so far this week. Subtract those from your weekly targets, and enter the remaining vert and running as the new targets.

## Setup

Requires [uv](https://docs.astral.sh/uv/) and Python 3.13.

```
uv sync
```

## Usage

### Start the planner

```
uv run python -m trail_planner.app
```

Open http://localhost:5000. The site has two tabs at the top: **Trail Planning** (described here) and **Training Log** (see [Training log](#training-log)). On Trail Planning, enter your targets, set the outings range (default 1–6), the tolerance, and whether a trail may repeat within the week. Then click **Shuffle** until you get a plan you like. Below the plan, **Included Trails** expands to a table of every plannable trail with its vert, distance and runnable miles. Its **Require** and **Exclude** checkboxes put a trail in every plan (say, one you want to run this week) or keep it out of all of them (one you just ran); by default neither is checked. Your settings and these choices are kept for each week while the browser tab is open, so going to the Training Log and back doesn't reset them. Under that, **Excluded Trails** lists the ones that don't have all three yet.

### Save a plan for the week

Under a plan, **Save plan for this week** keeps it in `week_plans.json` with the settings it was shuffled with, including the required and excluded trails. Coming back to the tab (after a restart, say) shows the week's saved plan and puts those settings back, unless you've changed them since in the same browser tab. If the week already has a saved plan, you're asked before it's overwritten. Each saved trail keeps the stats it had when saved, so updating trails later doesn't change a saved plan.

The planning tab plans the current week, starting on the day weeks start on in the Training Log. On the Training Log's weekly view, the current week has a **Plan week** button, or **See plan** once it has one. Any earlier week with a saved plan has **See plan** too, which opens the planning tab for that week. Plans are filed under the week's first day, so after changing the day weeks start on, plans saved before don't line up with the new weeks and don't show.

### Add or update trails

1. Export the route as a GPX file (Strava, AllTrails, etc.) into `gpx/`.
2. Name the file after the trail, e.g. `Green_from_NCAR.gpx`. Case doesn't matter.
3. Run:

   ```
   uv run python -m trail_planner.update_trails
   ```

The script recalculates every GPX file each time it runs. A file that matches an existing entry in `available_trails.json` (ignoring case) updates that entry. A file with no matching entry gets a new one, named after the file in lowercase. The planner reads the JSON on every request, so there's no need to restart it.

### Manual overrides

Each trail in `available_trails.json` has two blocks:

```json
"green_from_ncar": {
    "from_gpx": {"distance_mi": 11.42, "vert_ft": 2468, "runnable_mi": 5.65},
    "manual": {}
}
```

- `from_gpx` is rewritten every time `update_trails` runs, so don't edit it.
- `manual` is never touched by the script. A value you put there takes priority over the GPX value.

Use `manual` when you know better than the GPX. One case is lowering `runnable_mi` on a trail whose gentle grades are too rocky to run. Another is entering estimates for a trail you have no GPX file for yet.

A trail can be planned once it has all three fields, from either block.

### Training log

The **Training Log** tab shows every recorded activity on a calendar, modelled on Strava's training log. Each activity is a circle coloured by type (Hike, Trail Run, Bike, Race, Ski, Workout), and the circle's area shows its moving time. Each row's left column totals the vert, time, distance and running of the activities in it.

- **Weekly** (the default): one row per week, one column per day. Click a day's heading to start weeks on that day. Saturday is the default, and your choice is remembered in the browser. Under each circle are the activity's name, vert, distance and running, and its time is inside the circle when it fits. A grade, if you gave one, is a badge on the circle's edge.
- **Monthly**: one row per month. The month is cut into its weeks at the week's start day, so the first and last columns may be partial weeks and the row totals are exact calendar months.
- **Yearly**: one row per year, one column per month.

In monthly and yearly views, hover over a circle to see its numbers. The type filter limits both the circles and the totals. The sidebar jumps to a year or month, and lists races under their month.

Click any circle to edit the activity. You can give it a grade (A, B, C, D or F) and override its type, time, distance, vert or running. Changing the type is how a run becomes a Race. Overriding vert is how a treadmill or stairwell session gets its climbing. Recorded values show greyed as placeholders, and **Reset** goes back to one. Overridden numbers are underlined with a dotted line on the calendar.

**Where activities come from.** Your whole history comes in once from a Strava account export. After that, **Sync with Strava** keeps the log up to date.

To import the export: on strava.com, go to Settings → My Account → Download or delete your account. Then run:

```
uv run python -m trail_planner.import_strava_export recorded_activities/export_<id>.zip
```

The import copies each activity's original recording (FIT or GPX) into `activity_files/`, named by Strava activity id. It then adds the activity to `training_log.json`. Once imported, the zip isn't needed. Running the import again, e.g. with a newer export, updates every activity and keeps your edits.

To set up syncing, once:

1. Create an API application at https://www.strava.com/settings/api. Set its **Authorization Callback Domain** to `localhost`.
2. Put its id and secret in `strava_app.json` in the project folder:

   ```json
   {"client_id": "12345", "client_secret": "..."}
   ```

3. Click **Sync with Strava**. The first time, it sends you to Strava to approve access to all your activities, including private ones. Strava then sends you back and the sync runs. The tokens Strava gives are kept in `strava_tokens.json` and refreshed when they expire, so later syncs need no approval. Keep both files to yourself.

Each sync does the following:

- **Lists every activity's summary.** This takes one request per 200 activities.
- **Refreshes activities already in the log** with Strava's latest summary. This picks up a rename, a crop, a run marked as a race on Strava (which makes it a Race here), and the local start time the export lacks. If anything changed, the activity's numbers are recomputed from its stored recording. The first sync after an export import recomputes every activity, which takes about a minute. Later syncs are quick.
- **Adds new activities.** It downloads each one's details (for its private note) and its streams (the recorded time, position, altitude, distance, cadence and heart rate). The streams are kept in `activity_files/` as `<id>.streams.json.gz`, so a rebuild never downloads them again.
- **Leaves out resort skiing and snowboarding** (Strava's Alpine Ski and Snowboard). An activity changed to one of those on Strava is removed from the log, along with its recording and edits. The import does the same.
- **Reports activities that are no longer on Strava** under the calendar. It doesn't remove them, so their edits aren't lost to a mistake.

Strava allows 100 reads every 15 minutes, and each new activity takes two. If a sync reaches the limit, it keeps what it added and says how many are left. Sync again in 15 minutes for the rest.

Each activity in `training_log.json` has these blocks:

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
    "manual": {"grade": "B"}
}
```

- `strava` is Strava's own summary, as the export or a sync gives it. Its numbers aren't shown; they're kept for reference and for comparing with ours. `workout_type` is how Strava marks a race. The export doesn't give it, so it's `null` until the first sync.
- `recording_file` is the activity's recording in `activity_files/`: a FIT or GPX file from the export, or streams from a sync. It is `null` for an activity entered by hand on Strava.
- `recorded` is what the page shows. It is computed from `strava` and the recording file, and rewritten whenever they change.
- `manual` holds your edits. It's written only when you save on the page, and never touched by imports or syncs.

When an activity first enters the log, by import or sync, a "Grade: X" line in its Strava private note becomes its grade. That happens only once, so later imports and syncs won't bring back a grade you cleared.

How each value in `recorded` is computed:

- **Cropping.** The export holds each activity's original, uncropped recording, which may include the drive home. So every recording is first cut to the start and elapsed time Strava reports.
- **Distance** is summed along the GPS positions, the same way as for trails. A recording without GPS (e.g. a treadmill run) uses the watch's own distance, and an activity without a recording uses Strava's.
- **Vert** is computed the same way as for trails, so log totals are comparable to planner targets. It is 0 for a recording without elevation.
- **Moving time** counts the time spent faster than 0.2 m/s between samples. An activity that doesn't travel at all, e.g. a gym workout, counts all of its time.
  - For hikes, rides and watch-recorded runs, this matches Strava's moving time to within 1% at the median.
  - Phone-recorded runs on steep trails come out higher, because Strava counts slow climbing in a run as stopped. For example, Strava gives 9 of 66 min on the Manitou Incline.
- **Start time.** Strava's local start time, from a sync. Until the first sync, activities from the export use the watch's local time from FIT files. GPX files don't carry one, so they use this computer's time zone.
- **Type.** Strava's type is mapped to the log's type: Hike and Walk become Hike; Run and Trail Run become Trail Run, or Race if Strava marks it as a race; Ride becomes Bike; Backcountry Ski and Nordic Ski become Ski; anything else becomes Workout. Resort skiing and snowboarding (Strava's Alpine Ski and Snowboard) aren't training, so they're left out of the log altogether.
- **Heart rate** is stored for later use but not shown yet.
- **Running** is computed only for Hike, Trail Run and Race. Bike, Ski and Workout get 0, because a bike's pedalling cadence would look like running.
  - Recordings with cadence on at least 90% of samples are judged by cadence and pace together:
    1. Each stretch between samples has running cadence if the cadence recorded there is at least 155 steps/min. Recordings store cadence per foot, so their values are doubled first. A cadence dropout counts as non-running cadence.
    2. An unbroken stretch of running cadence counts as running only if its average pace is faster than 20 min/mi. Hiking can briefly show running cadence, but at 25–60 min/mi; short runs on the sample recordings were 12–15 min/mi. Pace is averaged over the whole stretch because GPS speed over a few seconds is noisy.
    3. A change of gait that lasts under 10 s takes the gait around it. That way a stray step or two on a hike, a stumble on a run, or a brief cadence dropout don't count as a switch.
  - Recordings without cadence (e.g. from a phone) are judged by pace alone. A stretch is running if it's faster than 15 min/mi, and changes of gait under 10 s take the gait around them. On recordings that do have cadence, this came within 0.4 mi of the cadence method.
  - An activity without a recording counts as all running if its average pace is faster than 15 min/mi.
  - Override an activity's running if you disagree.

After changing any of these methods, recompute every activity from the stored files. This keeps your edits:

```
uv run python -m trail_planner.rebuild_training_log
```

### Run the tests

```
uv run pytest
```

## Project layout

| Path | Purpose |
|---|---|
| `available_trails.json` | Trail catalog: GPX-derived stats plus manual overrides |
| `gpx/` | Route GPX files, one per trail |
| `trail_planner/trail_stats.py` | GPX → distance, vert, runnable miles (smoothing and grade settings live here) |
| `trail_planner/update_trails.py` | Script that writes GPX stats into the catalog |
| `trail_planner/trails.py` | Loads the catalog, applying manual overrides |
| `trail_planner/planner.py` | Shuffle algorithm |
| `week_plans.json` | Plans saved for each week, with the settings they were shuffled with |
| `trail_planner/week_plans.py` | Saves and loads the plans for each week |
| `trail_planner/effort.py` | Recording → running and hiking miles (by cadence, or by pace alone), heart rate |
| `training_log.json` | Training log: Strava's summary, our numbers and your edits for every activity |
| `activity_files/` | Each activity's recording (FIT or GPX from the export, or streams from a sync), named by Strava activity id |
| `trail_planner/recording.py` | Reads FIT and GPX files and Strava's streams into one form; moving time |
| `trail_planner/activity_stats.py` | Strava summary + recording → the numbers the training log shows |
| `trail_planner/training_log.py` | Loads the training log, applying overrides; validates edits |
| `trail_planner/import_strava_export.py` | Script that imports a Strava account export into the training log |
| `trail_planner/rebuild_training_log.py` | Script that recomputes the training log from the stored recordings |
| `trail_planner/strava_api.py` | Strava's API: approving access, refreshing tokens, reading activities |
| `trail_planner/strava_sync.py` | Brings the training log up to date with Strava |
| `strava_app.json`, `strava_tokens.json` | Your Strava API application, and the access it was given (keep private) |
| `trail_planner/json_store.py` | Reading and writing the JSON data files |
| `trail_planner/app.py` | Flask server for the pages and API |
| `trail_planner/static/site.css` | Styles shared by both tabs, including the tab bar |
| `trail_planner/static/calendar.js` | Calendar days and weeks, and the week start day, shared by both tabs |
| `trail_planner/static/trail_planning.html` | The Trail Planning tab |
| `trail_planner/static/training_log.html` | The Training Log tab |
| `tests/` | Unit tests |

## Not yet supported

- Planning by time instead of vert. Time depends on how much of each trail you run, so it would need a pace model, e.g. one fitted from Strava history.
- Automatic link-ups. A combined route like `bear_and_green_from_ncar` needs its own entry and GPX file.
- Removing activities deleted on Strava. A sync lists them, and you can delete their entries from `training_log.json` by hand.
