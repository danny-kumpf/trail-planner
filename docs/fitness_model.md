# The fitness model

The [Fitness tab](fitness.md) predicts how fast you climb from your heart rate and the elevation you're at. This describes how that prediction is made.

## Power, not climbing rate

On sustained climbs, the energy you spend per second rises steadily with heart rate. So the model works in metabolic power (W/kg) rather than directly in climbing rate.

Each 45 s block of climbing gets a power: its speed times what a metre of its terrain costs. That cost depends on grade and gait, and comes from Minetti et al. (2002), who measured it on a treadmill from −45% to +45% grade, both walking and running. A block's gait is the share of it that the Training Log's running-vs-hiking judgement calls running (see [Training Log](training_log.md#how-each-activitys-numbers-are-computed)).

The model is a straight-line fit of power against heart rate and elevation, over every block in its efforts, with each block weighted by how long it lasted. Every prediction is turned back into a climbing rate for the block's grade and gait, so power never appears on the page.

**Why power.** On the efforts this was developed on, climbing rate rises from 10% to about 20% grade and then levels off, which a straight line in grade can't follow. Minetti's walking cost follows it closely. Compared by leaving each effort out of the fit and predicting it, the power model predicts better than a model of climbing rate directly. Adding grade as a further term barely changes its predictions, so it isn't one. Above about 25% grade, the model expects about 5% more climbing than these efforts show, and the gap isn't clearly more than noise.

## What counts as climbing

- **Climbing** means terrain steeper than 5% that's being climbed at 100 ft/hr or more; slower than that is stopped, not climbing slowly. Down to 5% grade, Minetti's cost predicts climbing as well as it does on steeper terrain. Below 3% there's too little climbing, and too much noise in its grade, to tell.
- **A climb** has to last at least 90 s, so heart rate has time to catch up with the effort.
- **Heart rate of 120 bpm or more.** Climbing below that is too easy to be worth modelling, and the model's straight line in heart rate predicts it 20–40% too slow, so it's left out of both the model and the scores.
- **Grade** comes from elevation against distance only, smoothed over 200 m, so stopping on a slope doesn't change it.
- **GPS faults.** A stretch where the recording claims more than 6,000 ft/hr up or 8,000 ft/hr down is a GPS fault, not fast climbing, so its rate is treated as unknown.
- **Heart rate dropouts.** If heart rate starts out reading far too low and then jumps up, as some monitors do, it isn't used until it settles.

## Heart rate lag

Heart rate trails the effort that produced it, so each block is paired with the heart rate 45 s later. That's a typical lag, not a measured one: correlating heart rate with climbing rate never gave a convincing lag on the recordings this was developed on. A different heart rate monitor may lag differently.

## Limits

- The elevation term only holds for elevations the model was fit on.
- The model covers climbing only. Running on flat ground and downhill are limited by different things and aren't modelled yet.
