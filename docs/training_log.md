# Training Log

The Training Log tab shows every recorded activity on a calendar. For getting your activities in, see the [README](../README.md#4-bring-in-your-activities).

## Using it

Each activity is a circle coloured by type (Hike, Trail Run, Bike, Race, Ski, Workout), and the circle's area shows its moving time. Each row's left column totals the vert, time, distance and running of the activities in it.

- **Weekly** (the default): one row per week, one column per day. Click a day's heading to start weeks on that day; Saturday is the default, and your choice is remembered in the browser. Under each circle are the activity's name, vert, distance and running. A grade, if you gave one, is a badge on the circle's edge.
- **Monthly**: one row per month. The month is cut into its weeks at the week's start day, so the first and last columns may be partial weeks and the row totals are exact calendar months.
- **Yearly**: one row per year, one column per month.

The type filter limits both the circles and the totals. The sidebar jumps to a year or month, and lists races under their month.

**Editing an activity.** Click a circle to give the activity a grade (A–F) or override its type, time, distance, vert or running. Changing the type is how a run becomes a Race. Overriding vert is how a treadmill or stairwell session gets its climbing. Overridden numbers are underlined with a dotted line on the calendar. Edits are never overwritten by imports or syncs.

**Grades from Strava.** When an activity first enters the log, a line like `Grade: B` in its Strava private note becomes its grade. This happens only once, so a grade you clear later doesn't come back.

## Activities from more than one place

An activity can come in from an activity file, Strava's account export, or a sync. Each is recognized by when it started, so two copies starting within a minute of each other are the same activity:

- A file of an activity that's already in the log from Strava is skipped.
- Another file of an activity imported from a file (say its FIT after its GPX) updates it.
- An activity from Strava that was already in the log from a file replaces the file's entry, and your edits on it move over.

## What syncing does

Each **Sync with Strava**:

- refreshes activities already in the log with Strava's latest summary, picking up renames, crops, runs marked as races, and local start times. The first sync after an export import recomputes every activity, which takes about a minute;
- adds new activities, downloading each one's recorded streams (time, position, altitude, distance, cadence, heart rate);
- leaves out resort skiing and snowboarding (Strava's Alpine Ski and Snowboard). An activity changed to one of those on Strava is removed from the log;
- lists activities from Strava that are no longer on it under the calendar, without removing them, so their edits aren't lost to a mistake. Activities imported from files aren't listed;
- replaces activities imported from files with Strava's copy, as above.

## How each activity's numbers are computed

- **Cropping.** The export holds each activity's original, uncropped recording, which may include the drive home. Every recording from Strava is first cut to the start and elapsed time Strava reports. An imported file is used whole, so crop it before downloading it (Strava and Garmin Connect can both crop an activity) if it holds more than the activity.
- **Distance** is summed along the GPS positions, the same way as for trails. A recording without GPS (e.g. a treadmill run) uses the watch's own distance, and an activity without a recording uses Strava's.
- **Vert** is computed the same way as for trails (see [Trail Planning](trail_planning.md#how-trail-stats-are-computed)), so log totals are comparable to planning targets. It is 0 for a recording without elevation.
- **Moving time** counts the time spent faster than 0.2 m/s between samples. An activity that doesn't travel at all, e.g. a gym workout, counts all of its time.
  - For hikes, rides and watch-recorded runs, this matches Strava's moving time to within 1% at the median.
  - Phone-recorded runs on steep trails come out higher, because Strava counts slow climbing in a run as stopped. For example, Strava gives 9 of 66 min on the Manitou Incline.
- **Start time.** Strava's local start time, from a sync. Otherwise, the watch's local time from FIT files. GPX files don't carry one, so they use this computer's time zone.
- **Type.** For an imported file, the sport it records (or the `--type` it was imported with), mapped the same way. For Strava: Hike and Walk become Hike; Run and Trail Run become Trail Run, or Race if Strava marks it as a race; Ride becomes Bike; Backcountry Ski and Nordic Ski become Ski; anything else becomes Workout. Resort skiing and snowboarding aren't training, so they're left out of the log.
- **Running** is computed only for Hike, Trail Run and Race. Bike, Ski and Workout get 0, because a bike's pedalling cadence would look like running.
  - Recordings with cadence on at least 90% of samples are judged by cadence and pace together:
    1. Each stretch between samples has running cadence if its cadence is at least 155 steps/min. Recordings store cadence per foot, so their values are doubled first. A cadence dropout counts as non-running cadence.
    2. An unbroken stretch of running cadence counts as running only if its average pace is faster than 20 min/mi. Hiking can briefly show running cadence, but at 25–60 min/mi; short runs on the sample recordings were 12–15 min/mi. Pace is averaged over the whole stretch because GPS speed over a few seconds is noisy.
    3. A change of gait that lasts under 10 s takes the gait around it, so a stray step or two on a hike, a stumble on a run, or a brief cadence dropout don't count as a switch.
  - Recordings without cadence (e.g. from a phone) are judged by pace alone. A stretch is running if it's faster than 15 min/mi, and changes of gait under 10 s take the gait around them. On recordings that do have cadence, this came within 0.4 mi of the cadence method.
  - An activity without a recording counts as all running if its average pace is faster than 15 min/mi.
  - Override an activity's running if you disagree.
- **Heart rate** is stored but not shown on the Training Log. The Fitness tab uses the recording's heart rate.
