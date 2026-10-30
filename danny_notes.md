## Modeling

Ok, next thing I want for this project is to see my fitness progress. What will that look like?

I like the model we created in ../pace-predictor. It fits (I think?) a linear
model to altitude, grade, and heart rate. And it predicts vertical rate. That
feels like a decent starting point, although I'd like to extend it to downhill and 
flat running to.

Is there a better thing to predict than vertical rate? Can we use the power model
from that other repo? what about for downhills?

I guess for now, I'm fine to just use the other repo's model and only check 
my fitness on the uphills. Maybe later we'll think about a downhill model.

Seems like for running downhill, we can't fit a linear model; we have to fit 
some kind of paramaterized curve that starts out with less grade meaning faster
running, and ends with the opposite.

Anyway. What do I want the UI to look like?
- Primarily I want to ask "how was this effort compared to my recent efforts"?
- I like the general "explore" page from the other repo. It's fun to just be able 
  to see those stats for a particular effort. Mostly I'm interested in the 
  vert/hr plots for the steep climbs
- I think I want it to automatically maintain a model from the last ~month of efforts
- Plus a table of all efforts with a check/X column like we have on the other 
  page, but this one lets me require/exclude an effort from being in the model
- 
