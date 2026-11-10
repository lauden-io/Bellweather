# The forecast: many models, scored against your own weather

Most weather integrations hand you one model's opinion and call it the
forecast. Bellweather asks eleven models, checks every day how each of them
has done **at your place**, removes the errors each one keeps making there,
and gives you the blend. Then it shows you its own score, every day, next to
the score of every model it used.

I did not build it this way because it sounds clever. I built it this way
because I measured it, and because the meteorology literature says the same
thing for decades.

## In one table

| What | How | Why |
|---|---|---|
| Forecast | 11 models from Open-Meteo, blended per hour | A blend beat every single model, ECMWF included, at all 6 airports I tested |
| Correction | Each model's bias over the last 30 days is removed, and models are weighted by 1 / error variance | It cut temperature error by 7–31 % and wind error by 11–28 % against ECMWF alone |
| Precipitation | An hour is wet when at least half of the models say ≥ 0.1 mm | That rule had the best hit rate at 4 of 6 airports |
| Safety net | No correction until 20 of the last 30 days have data | With 3–4 days, correcting made things worse in my test |
| Next two hours | Radar nowcast: the latest radar frames moved forward, or an official nowcast where one exists | Radar extrapolation beats models in the first one to a few hours |
| Honesty | Diagnostic sensors with the blend's and each model's error over the last 30 days, out of sample | So you can see how good it is at your location, not somewhere on average |

## What I measured

I wanted to know three things before writing a line of the forecast:

1. Does a blend of models really beat the best single model at one point?
2. Does correcting each model's local bias help, and how much history does
   that need?
3. Is "pick the model that did best lately" a good idea?

### Method

- **Places:** six airports, chosen because their reports are public and long:
  Paris-Le Bourget (LFPB), Oslo (ENGM), Vienna (LOWW), Chicago O'Hare (KORD),
  London Heathrow (EGLL), Madrid (LEMD).
- **Truth:** the hourly airport reports (METAR) from the Iowa Environmental
  Mesonet archive.
  - Temperature, wind speed.
  - A wet hour: any precipitation code in the hour.
- **Forecasts:** what each model said **one day earlier**, from Open-Meteo's
  Previous Runs archive, for 11 models:
  - ECMWF IFS and ECMWF AIFS;
  - ICON, GFS, Météo-France, UKMO, KNMI, DMI, MET Norway, GEM, JMA.
- **Period:** 90 days, 11 August to 8 November 2026.
- **No peeking:** every test day was scored with corrections learned only
  from the 30 days before it. This is exactly what the integration does
  live. About 1,430 scored hours per airport.

The script is in the repository: `tools/evaluate_forecasts.py`. Anyone can
run it for any public airport and check my numbers.

### Temperature: mean absolute error, °C

| Airport | ECMWF IFS | best model of the last 30 days | plain mean of 11 | **Bellweather blend** | best single model, known only afterwards |
|---|---|---|---|---|---|
| Paris | 1.09 | 1.09 | 1.04 | **0.99** | 1.09 |
| Oslo | 1.25 | 0.99 | 0.92 | **0.86** | 0.95 |
| Vienna | 1.00 | 1.04 | **0.92** | 0.93 | 1.00 |
| Chicago | 1.23 | 1.08 | 0.97 | **0.86** | 1.08 |
| London | 0.94 | 0.88 | 0.81 | **0.67** | 0.85 |
| Madrid | 1.20 | 1.24 | 1.06 | **1.06** | 1.15 |

The last column is the model that turned out best at that airport over the
whole period. Nobody can know it in advance, and the blend still beats it
everywhere.

### Wind speed: mean absolute error, km/h

| Airport | ECMWF IFS | best of last 30 days | plain mean | **Bellweather blend** |
|---|---|---|---|---|
| Paris | 3.14 | 2.84 | 2.83 | **2.79** |
| Oslo | 3.99 | 3.23 | 3.30 | **3.01** |
| Vienna | 4.56 | 3.63 | 3.94 | **3.31** |
| Chicago | 4.63 | 4.17 | 4.11 | **3.63** |
| London | 5.03 | 3.92 | 4.18 | **3.64** |
| Madrid | 3.19 | 3.02 | 2.86 | **2.88** |

### Will it rain in this hour? Hit rate (CSI)

CSI counts hits divided by hits + misses + false alarms; 1 is perfect.

| Airport | wet hours | ECMWF IFS | best single model | **half of the models wet** |
|---|---|---|---|---|
| Paris | 120 of 2147 | 0.23 | 0.29 | **0.33** |
| Oslo | 448 of 2159 | 0.49 | 0.53 | **0.57** |
| Vienna | 161 of 2159 | 0.33 | 0.34 | **0.39** |
| Chicago | 179 of 2149 | 0.25 | 0.33 | **0.33** |
| London | 295 of 2159 | 0.35 | 0.37 | **0.42** |
| Madrid | 65 of 2159 | 0.17 | **0.23** | 0.21 |

### Dew point and pressure: mean absolute error

Measured on 2026-11-10 with the same method, at 10 public airports on four
continents: the six above, plus Sydney, Singapore, Tokyo Haneda and Dubai.
Day-1 run, 90 days to 9 November 2026, each test day scored only with the
30 days before it. Script: `tools/evaluate_dewpoint_pressure.py`.

| Airport | Dew point, plain mean | Dew point, corrected | Pressure, plain mean | Pressure, corrected |
|---|---|---|---|---|
| Paris | 0.81 °C | **0.73 °C** | 0.47 hPa | **0.38 hPa** |
| Oslo | 0.78 | **0.75** | 0.93 | **0.46** |
| Vienna | 0.76 | **0.68** | 0.54 | **0.41** |
| Chicago | 0.95 | **0.93** | **0.39** | 0.40 |
| London | 0.85 | **0.72** | 0.49 | **0.39** |
| Madrid | 1.05 | **1.03** | 1.11 | **0.77** |
| Sydney | 1.33 | **1.32** | 0.41 | **0.39** |
| Singapore | 0.72 | **0.66** | 0.45 | **0.38** |
| Tokyo | 1.10 | **0.94** | 0.67 | **0.47** |
| Dubai | **1.54** | 1.57 | 0.55 | **0.32** |

The correction lowered the dew point error at 9 of 10 airports (by 1–15 %)
and the pressure error at 9 of 10 (by 5–51 %); it was slightly worse at
Dubai for dew point and at Chicago for pressure. Part of the pressure gain
is a steady difference the correction learns: outside North America the
airports give QNH, which differs a little from sea-level pressure, more so
high up (Madrid, 610 m). At home Bellweather scores against what it
measures there, so it learns that home's own difference the same way.

### What it taught me

- **The blend wins.** At all six places it beat every single model for
  temperature, including the one that later turned out best.
- **Local correction is worth it.** Removing each model's own bias at your
  place cut the error clearly at four of six airports. At the other two it
  was even with the plain mean.
- **Chasing "the best model lately" is a trap.** It was worse than ECMWF at
  Vienna and Madrid. Models take turns; a month of luck is not skill.
- **Not enough history is worse than none.** In a separate check on one
  week, correcting from 3–4 days made the forecast worse. Hence the rule:
  nothing is corrected before 20 of the last 30 days have data.

### What the literature says

My test is small. Larger studies found:

- **Consensus beats its members.** The Australian Bureau of Meteorology's
  bias-corrected, error-weighted consensus had about 40 % lower temperature
  error than its component forecasts at days 0–2. Weighting by error beat a
  plain average by about 5 % (Woodcock and Engel 2005,
  doi:10.1175/WAF-831.1).
- **Consensus beats human forecasters.** In the US, consensus statistical
  forecasts matched or beat them, and performance weighting did better still
  (Baars and Mass 2005, doi:10.1175/WAF896.1).
- **The US blend beats its parts.** NOAA's National Blend of Models
  outperforms its components; its day-4 forecast is as good as the
  bias-corrected GFS at day 2 (Huntemann et al. 2017, AMS,
  https://www.weather.gov/media/mdl/AMS2017-NBMVerification.pdf).
- **Training length.** Running-mean bias removal needs about a week of
  history (Stensrud and Yussouf 2003). Temperature corrections settle in
  about 14 days, precipitation needs about 40 (McCollor and Stull 2008,
  doi:10.1175/2007WAF2006107.1). My 20-of-30-days rule sits on the safe
  side of that.
- **Not every blend helps.** In one study a blend of nine global models did
  not beat ECMWF alone, while the four best did (Hagedorn et al. 2012,
  doi:10.1002/qj.1895). That is why Bellweather weights members by their
  local error instead of trusting all of them equally, and why it shows you
  the score.

## How the forecast is made, step by step

### 1. Members

Every hour Bellweather asks Open-Meteo for the next 10 days of 11 models in
one request:

- ECMWF IFS 0.25° and ECMWF AIFS;
- the "seamless" series of DWD ICON, NOAA GFS, Météo-France, UK Met Office,
  KNMI, DMI, MET Norway, Environment Canada GEM and JMA.

A "seamless" series joins a service's high-resolution regional model
(for example ICON-D2 at 2 km or AROME at 1.3 km) with its global model, so
you get the sharpest model available for your area, without configuration.

Optionally your own weather entities join as members, for example a paid
provider you already use. They are recorded and scored exactly like the
built-in models.

Open-Meteo's terms allow personal home automation for free under 10,000
calls a day (https://open-meteo.com/en/terms). Bellweather uses far less:
one current request every 5 minutes, one forecast an hour, one verification
a day.

### 2. Verification, once a day

- Open-Meteo keeps what each model forecast one day earlier (Previous Runs,
  archived since January 2024 for most models). Bellweather fetches the
  last 65 days of that for your point.
- It compares those forecasts with what was measured at your place:
  Bellweather's own values, logged every hour.
- **Values that came from a model are never used as truth.** Scoring
  models against a model would only measure agreement.
- Optional: the airport's archive (see below) fills the first 60 days.

### 3. Corrections

For temperature and wind speed, per member, over the last 30 days:

- **bias**: the average error. A model that is always 1.5 °C too warm in
  your valley gets 1.5 °C taken off;
- **weight**: 1 / the variance of the remaining error. A model that misses
  by a steady amount is easy to correct; one that scatters wildly counts
  less.

Nothing is corrected until at least 20 of those 30 days have data. Until
then every member counts the same, which already beat every single model
in my test.

Since 2026-11-10 dew point and sea-level pressure are corrected the same
way; the measurement is in the table above. Humidity, cloud and gusts are
averaged without correction, because I have not verified corrections for
them. Wind
direction is the mean of the wind vectors, so 350° and 10° give 0°, not
180°.

### 4. Precipitation

- **Wet hour:** at least half of the members forecast at least 0.1 mm.
- **Amount:** the members' mean.
- **"Probability":** the share of members that forecast at least 0.1 mm.
  It is honest about what it is: agreement between models, not a
  calibrated probability.
- **Type:**
  - snow when at least 80 % of the amount falls as snow;
  - sleet from 20 %;
  - freezing rain or thunder when at least 30 % of the members say so.
  These cut points are my choice and will be tuned with data.

### 5. Days

Days are local days:

- highest and lowest temperature;
- the sum of the hourly amounts;
- the share of members whose day reaches 1 mm (the usual "rain day");
- the strongest wind and gust;
- the mean humidity, dew point and pressure;
- the highest feels-like temperature and UV index;
- the most significant condition. A day only shows rain when most members
  make it a rain day.

### UV index

Only a few models compute a UV index: in my check on 2026-11-10 (Paris and
Nairobi) GFS and the UK Met Office did, and KNMI and MET Norway passed on
GFS's values unchanged. Bellweather takes the mean of the members with a
UV series of their own and counts a pass-through copy once, so GFS does
not weigh three times. Nothing measures UV near most homes, so, like dew
point and pressure, it is the models' value and not verified. The current
UV index on the weather entity is the blend's value for this hour.

### 6. Timing

Every hour in Bellweather's forecast is the hour that **starts** at its
time stamp. Providers disagree on this: Open-Meteo gives an hour's
precipitation at the end of the hour, met.no at the start. Bellweather
converts everything to one convention, so the members line up.

### 7. Stored, not fetched again

The verification is kept with the location's history. A restart, a reload
or a saved setting uses it instead of fetching 65 days of every model's
history again, as long as it is younger than a day and was made for the
same point, precision, models and archive setting (only a hash of those is
stored). Locations also start their first forecast 15 seconds apart, so
their requests do not reach the services all at once.

## The airport archive (off by default)

Without it, the correction starts once Bellweather has logged about 20 days
of your own measurements. With it, the Iowa Environmental Mesonet sends the
nearest suitable airport's last 60 days of reports, and the forecast is
corrected from the first day.

The archive refuses requests that come too close together from one
address: on 2026-11-10 requests one second apart got "Too many requests
from your IP address, slow down." (HTTP 429), five seconds apart did not.
So all locations' archive requests go through one gate, one at a time and
at least 10 seconds apart.

Airports report on their own schedule: most at :50 or :00, but some report
at :15 and :45, and others at :20 and :50. For each hour the report
nearest to the full hour counts, up to 20 minutes away. Before this rule,
an airport reporting at :15 and :45 gave no usable hours at all. If the archive still fails, the verification
runs with your own measurements; a verification made with the archive in
the last three days is kept instead, and the archive is asked again an
hour later.

- **What it costs:** one more service learns that airport's code, which
  says roughly where you live. That is no more than the airport query
  already says, but it goes to one more party.
- **What it buys:** in my test, correction against plain averaging improved
  temperature error by −1 % to +17 %, depending on the place.
- **Which airport:** the nearest one within the temperature limits (default
  40 km and 150 m height difference), so that its weather is a fair stand-in
  for yours.

Its days give way to your own measurements as they accumulate.

## The next two hours: radar nowcast

Models are poor at telling you whether the shower will reach you in 20
minutes. Radar is good at it. Since 1 January 2026 RainViewer's public API
no longer provides future radar frames (its transition FAQ,
https://rainviewer.com/api/transition-faq.html), so Bellweather makes its
own:

1. It reads the radar around your point, about 80–100 km in each direction,
   in the newest frame and the frame 20 minutes before.
2. It finds how the rain moved, block by block around you, coarse to fine.
   The median of the block movements is the motion.
3. For every 10 minutes up to two hours it looks upstream: the rain that
   reaches you in 40 minutes is now 40 minutes' travel away.
4. Uncertainty grows with time, so the look-up area grows with time too.
   The share of wet pixels in it is the probability.
5. It stops when upstream leaves the radar area it has seen. No guessing.

**Why this method:**

- **Reach:** in the literature, radar extrapolation beats numerical models
  up to roughly two to five hours.
  - The pysteps paper summarises the literature as the first 2–5 h
    (Pulkkinen et al. 2019,
    https://gmd.copernicus.org/articles/12/4185/2019/).
  - Newer studies with fast-updating models put the crossover at 1–2.5 h:
    Imhoff et al. 2024 for the Netherlands, Blahak et al. 2024 for DWD.
- **Block matching:** it is only slightly worse than full optical flow in
  the first two hours, and clearly worse only from three hours on (Woo and
  Wong 2017, Hong Kong Observatory,
  https://www.hko.gov.hk/publica/reprint/r1258.pdf). Two hours is where
  Bellweather stops.
- **Not one vector for the whole map:** a single global motion vector loses
  to local methods (Nielsen et al. 2026). That is why the motion comes from
  blocks around your point.

**Measured on the evening of 9 November 2026, live RainViewer data:**

| City | 10 min | 30 min | 60 min |
|---|---|---|---|
| Paris (rain moving at about 37 km/h) | 0.91 (no change: 0.81) | 0.81 (0.66) | 0.70 (0.61) |
| Tokyo (slow rain) | 0.94 (0.93) | 0.91 (0.90) | 0.86 (0.86) |

The numbers are the hit rate (CSI) over the radar tile; "no change" means
assuming the rain stays where it is. Two cases are not a study, which is
why the nowcast scores itself.

**It scores itself, live:**

- every prediction is compared with the radar 10 to 60 minutes later;
- "no change" is scored next to it;
- the "Nowcast hit rate" diagnostic sensor shows both for the last 7 days.

**Where an official nowcast exists, it wins.** MET Norway's nowcast covers
the Nordic countries at 1 km and 5 minutes
(https://api.met.no/weatherapi/nowcast/2.0/documentation). Bellweather
asks it only for points in that area and uses it instead of its own.

Its known limit, shared by every extrapolation: rain that forms or dies on
the way is not foreseen. Summer storms that pop up overhead remain hard for
everyone.

## What you see in Home Assistant

- **Each location's weather entity (`weather.home` for the first):**
  - its current values are the measured ones; the UV index is the blend's
    value for this hour;
  - its hourly forecast covers 7 days and its daily forecast about 9 days;
  - its attributes say how many members answered, whether the correction is
    active, and from how many days and which source.
- **Nowcast sensors:**
  - "Precipitation next hour" (mm), with the motion and every 10-minute step
    as attributes;
  - "Precipitation starts in" and "Precipitation stops in" (minutes; unknown
    when nothing changes within the horizon);
  - "Precipitation within an hour" (on or off).
- **Diagnostic sensors (the scorecard):**
  - "Forecast error, temperature" (°C) and "Forecast error, wind" (km/h);
  - "Forecast hit rate, precipitation" (%).

  Each shows the blend's error over the last 30 days, scored out of sample,
  with every member's figure in the attributes. So you can see, for your
  place, how much the blend gains over each model.
  - "Nowcast hit rate" (%): the radar nowcast 30 minutes ahead, next to
    "no change".

## Limits, plainly

- My verification covers 6 places, one season (late summer to autumn), the
  day-1 run, and temperature, wind and wet hours. The integration's own
  scorecard extends that to your place and every season, but it starts
  empty.
- The correction learned for day 1 is applied to all lead times; I have not
  verified it beyond day 1.
- The probability is model agreement, not calibrated.
- Rounding the location (default 10 km) means the forecast answers for a
  point up to 7 km away. Near hills or coasts, choose 1–5 km if that matters
  more to you than privacy. [docs/privacy.md](privacy.md) has the trade-off.
- Some models stop or change. HRRR ends in November 2026, and ICON's regular
  grids change after 30 November 2026. The seamless series and the
  out-of-sample scorecard absorb such changes, and members that stop
  answering simply drop out.
