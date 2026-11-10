# Trail Planning

The Trail Planning tab picks a week of trails that hits a vert target and has enough runnable terrain for a running target. For adding trails, see the [README](../README.md#3-add-your-trails).

## Using it

Enter the week's vert and running targets, the range of outings (default 1–6), the vert tolerance (default ±3%), and whether a trail may repeat within the week. Click **Generate**, then **Shuffle** to step through the other plans that fit.

**Included Trails**, below the plan, lists every plannable trail. Its **Require** and **Exclude** checkboxes put a trail in every plan (one you want to run this week) or keep it out of all of them (one you just ran). **Excluded Trails** lists trails that are missing distance, vert or runnable miles, and so can't be planned yet.

**Mid-week replanning.** The Training Log's weekly view shows how much vert and running you've done so far this week. Subtract those from your weekly targets and plan the rest of the week with what remains.

The week being planned starts on the day the Training Log's weeks start on. Saved plans are filed under their week's first day, so after changing the week start day, plans saved before don't line up with the new weeks and don't show.

## How trail stats are computed

Each trail's distance, vert and runnable miles come from a GPX file of the route. The elevation profile is resampled every 10 m and smoothed with a 150 m moving average, because raw GPS and map elevation have small wiggles that inflate gain. From the smoothed profile:

- **vert** is the sum of all climbing;
- **runnable miles** is the distance on grades between −10% and +5%.

Two calibration checks against AllTrails: Green from NCAR gives 2,468 ft (AllTrails lists 2,474 ft), and Lions Lair gives 910 ft (AllTrails lists 912 ft).

The Training Log computes vert from recordings the same way, so log totals are comparable to planning targets.

## How plans are picked

Clicking **Generate** does this:

1. For each number of outings in the allowed range, find every combination of that many trails that meets both conditions:
   - total vert is within the tolerance of the target;
   - total runnable miles is at least the running target.
2. Shuffle all of them into one random order. For each next plan in the order, first pick a number of outings at random from the ones with plans left, then pick one of that count's plans.
3. Show the first plan in the order.

The button then reads **Shuffle (1/N)**, where N is how many plans meet the conditions. Each click shows the next plan in the order without searching again, and after the last one it starts over. Changing any setting turns the button back into **Generate**, which searches and shuffles anew.

The number of outings is picked before the plan so that, say, 3-outing and 6-outing weeks come up about equally often early in the order, even though there are far more 6-trail combinations. The tolerance is symmetric, so small misses should cancel out over several weeks.

If nothing fits, the page says why: either it shows the closest plan with enough runnable miles, or it reports the most runnable miles any plan has.

The planner only checks that enough runnable terrain exists. Which stretches to actually run is up to you.
