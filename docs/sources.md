# Sources and how a value is chosen

A weather forecast answers "what will the weather probably be". The "now"
side of Bellweather answers **"what is the weather doing at the location I set, right now"**. For
each value it uses the most direct measurement available, and it says which
one it used. The forecast and the radar nowcast are described in
[forecast.md](forecast.md).

## The chain

For every value Bellweather walks a fixed list of source kinds, most direct
first. The first kind with a usable reading wins. Within a kind, the nearest
reading wins; between equally near ones, a weather entity you picked wins
over the built-in model; then the newest.

A reading is usable when all of these hold:

- it is fresh enough;
- it is close enough;
- for stations, it is not too far above or below the home. A mountain-top
  station says little about the wind or the temperature in the valley;
- its station is not on your list of stations to ignore.

| Value | 1st | 2nd | 3rd | 4th | 5th |
|---|---|---|---|---|---|
| Precipitation now | your own sensor | radar at the exact point | official station ≤ 15 km | airport ≤ 30 km | model |
| Precipitation type | your own sensor | airport (the only global type code) | radar (rain or snow) | model | – |
| Intensity (mm/h, class) | your own sensor | radar | official station (10-min sum) | airport (light / moderate / heavy) | model |
| Precipitation, last hour | your own sensor | radar (six 10-min frames) | official station ≤ 15 km (hourly sum) | – | – |
| Precipitation, last 24 h | – | official station ≤ 15 km (24-h sum) | model | – | – |
| Last precipitation | now, if it is wet now | radar history (2 h) | official station hourly sum > 0 | the time remembered before | – |
| Thunder | your own sensor | airport | model | – | – |
| Wind, gust, direction | your own sensors | official station ≤ 40 km, ≤ 400 m height difference | airport ≤ 50 km, ≤ 400 m | model | – |
| Temperature, dew point, humidity | your own sensors | official station ≤ 25 km, ≤ 150 m height difference | airport ≤ 40 km, ≤ 150 m | model | – |
| Pressure (sea level) | your own sensor | official station ≤ 100 km | airport ≤ 100 km (sea-level pressure, else QNH) | model | – |
| Visibility, fog | your own sensor | official station ≤ 30 km | airport ≤ 40 km | model | – |
| Cloud cover | model | airport ≤ 40 km | official station | – | – |
| Snow on the ground | observed depth: own sensor, station or airport ≤ 30 km, ≤ 300 m, ≤ 24 h old | satellite snow map (IMS, Northern Hemisphere) | model snow depth (≥ 2 cm counts) | – | – |

"Model" means the weather entity you picked in the options, then the
built-in Open-Meteo values (ECMWF IFS). You can change every distance and
height limit in the options, under "Distance and height limits".

Two values are computed from the others:

- **Feels like:** the Australian Bureau of Meteorology's apparent
  temperature (Steadman 1994), from temperature, humidity and wind, without
  sunshine.
- **Weather now:** one of Home Assistant's condition strings. Thunder comes
  first, then hail, then precipitation by type, fog, wind of at least
  41 km/h (Beaufort 6), and finally the cloud cover with the sun's height:
  below 31 % is sunny or clear night, from 75 % cloudy, partly cloudy in
  between. Its `based_on` attribute says which source gave each input.

Rules that hold across the chain:

- **"Precipitation now" decides.** If it says dry, the type is "none" and
  the class is "dry". If it says wet and the chosen type source says
  "none", the next source that names a type is used. For example, radar sees
  rain while the airport 15 km away reports nothing.
- **No radar is not "dry".** Where the radar coverage map shows no radar,
  the radar gives no answer at all, and the next source decides.
- **Snow on the ground is never derived from snowfall.** Snow can fall
  without settling, and a landscape can stay white for weeks without any
  snowfall.
- **No gust group in a METAR** means no significant gust. The gust then
  equals the mean wind.
- **Temperature is not corrected for height.** Inversions, common in
  winter valleys, make a fixed lapse rate wrong, so only stations close in
  height count.
- **Missing-value markers** such as -32767 or -999 are dropped, as are
  values outside physical limits.

Every distance, age and height limit above is a starting value, to be tuned
with real data.

## Every value shows its origin

Each entity carries these attributes:

- `source`: the service, e.g. `rainviewer`, `esoh`, `metar`;
- `source_kind`: local, radar, station, airport, satellite, model or derived;
- `station` and `station_id`;
- `distance_km`;
- `observed`: when the value was measured;
- `candidate_count`: how many usable readings of that kind there were.

Wind direction also has `cardinal` (N, NNE, ...). The entity's attribution
names the data owner.

The **Sources working** diagnostic sensor counts the sources that answered
on the last update. Its attributes show, per source, the last success, the
last error and, for the radar, whether the location has radar coverage.

## Sources in this version

| Source | Kind | Coverage | Cadence | Access | Licence / terms |
|---|---|---|---|---|---|
| RainViewer | radar | 150+ countries, where radar exists | 10 min | no key | personal, educational and small-scale community use; "Weather data by RainViewer" |
| EUMETNET E-SOH (MeteoGate) | official stations | Europe, about 4,000 stations from many national services | 10 min; values seen 10–40 min after measurement | no key | CC BY 4.0 |
| NOAA Aviation Weather Center | airports (METAR) | worldwide, about 7,700 airports | 30–60 min | no key; 100 requests/min | NOAA public data |
| Open-Meteo (ECMWF IFS) | model values, snow depth, 24-h precipitation; station heights (elevation API) | worldwide | hourly (current values every 15 min, asked once per quarter hour) | no key | free for non-commercial use |
| NOAA/USNIC IMS (NSIDC G02156) | satellite snow map | Northern Hemisphere, 4 km | daily, valid 00 UTC, posted about 14 h later | no key | cite the data set; NSIDC says its servers are not for operational use |
| Open-Meteo forecast and Previous Runs | 11 forecast models, and what they said one day earlier | worldwide | hourly forecast; verification daily | no key | free for personal home automation under 10,000 calls a day; CC BY 4.0 |
| MET Norway Nowcast 2.0 | official radar nowcast | Nordic countries | 5 min, 2 h ahead | no key; identifying User-Agent | CC BY 4.0 |
| Iowa Environmental Mesonet | airport report archive (optional, off by default) | worldwide airports | once a day | no key | public archive |

## Planned sources

**Satellite snow map, Southern Hemisphere:** VIIRS snow cover (NASA GIBS);
clouds leave gaps.

**National official stations, no key** (denser or with more values than
E-SOH where they exist):
- **Europe:**
  - Germany (DWD), Switzerland, Austria, Finland, Denmark, Belgium, Poland, Slovenia, Hungary, Iceland, Cyprus;
  - these also give snow depth: Austria, Switzerland, Finland, Czechia, Slovakia, Sweden, and Germany hourly.
- **Elsewhere:**
  - USA (NWS);
  - Canada (ECCC; includes snow depth);
  - Japan (JMA AMeDAS, includes snow depth);
  - Hong Kong, Singapore.

**National official stations with a free personal key:**
- Netherlands, France, Spain, United Kingdom, Norway;
- Taiwan, South Korea, Israel, Chile.

**Global station stream:** WMO WIS2. It is open worldwide, but it needs
MQTT and BUFR decoding.

**Road weather**, an optional national add-on for snow and ice on the road:
- Finland (Digitraffic), Sweden;
- Germany's road stations;
- the US (Iowa Environmental Mesonet mirror);
- Ontario and Québec.

**Not usable** because of their terms or because data is not open: Australia
(Bureau of Meteorology), New Zealand, India, Saudi Arabia, South Africa.
