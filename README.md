<h1 align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="branding/logo-motto-dark.svg">
    <img src="branding/logo-motto.svg" alt="Bellweather: The weather here and now, measured." width="460">
  </picture>
</h1>

<p align="center"><b>And the best forecast for your home, scored against what was measured.</b></p>

A bellwether is the sheep with the bell that the flock follows: the one
that shows where things are heading. Bellweather does that for the weather
at your home, in Home Assistant.

It is for everyone: any Home Assistant installation, anywhere in the world,
with no account, no API key and no extra hardware.

Version **0.1.0** (what is new: [CHANGELOG.md](CHANGELOG.md)).

## Why I built it

Bellweather is the perfect companion for
[PanelOS](https://github.com/lauden-io/PanelOS), the open firmware for
smart home wall panels. A wall panel shows the weather at a glance, so that
glance has to be right: is it raining at the door now, how cold is it
really, will it rain in the next hour. No weather source in Home Assistant
could answer that reliably, so I built one.

It needs no panel. Every answer it gives a panel, it gives a dashboard, an
automation or a notification just the same, in any Home Assistant
installation.

Home Assistant's weather entities come from forecast models.

- Their "current" values are model output: an estimate of what the weather
  is probably doing.
- They have no "is it raining right now" value at all.
- Their forecast is one model's opinion, however well or badly that model
  does at your place.

I wanted the opposite: the weather as it **is**, from the most direct
measurement available, and a forecast that **proves** it is good where I
live.

## What you get

### The weather now, measured

For every value Bellweather picks the most direct source available, and it
tells you which one it picked:

1. your own sensors;
2. radar right above your home;
3. the nearest official weather station;
4. the nearest airport;
5. a model, only when nothing measured is close and fresh enough.

| Entity | What it is |
|---|---|
| Weather now | sunny, rainy, snowy, fog, … from the observations and the sun's height |
| Precipitation now | on or off |
| Precipitation type and class | none, drizzle, rain, freezing rain, sleet, snow, hail; dry … extreme |
| Precipitation intensity | mm/h |
| Precipitation in the last hour and the last 24 hours | mm |
| Last precipitation | when it last rained or snowed ("dry since") |
| Temperature, feels like, dew point, humidity, pressure | °C, %, hPa |
| Wind speed, gust, direction | km/h, degrees and compass point |
| Visibility, fog, cloud cover | km, on or off, % |
| Thunder | on or off |
| Snow depth and snow on the ground | cm, on or off. Snow on the ground is never guessed from snowfall: it comes from measured depth, a satellite snow map or the model, in that order |

Every value shows where it came from in its attributes: the service, the
kind of source, the station, how far away it is, how old the value is, and
how many candidates there were. You never have to wonder why it says what
it says.

### The next two hours, from radar

- **Precipitation next hour** (mm).
- **Precipitation starts in** and **stops in** (minutes).
- **Precipitation within an hour** (on or off).

Bellweather moves the latest radar frames forward to see what is coming:
a radar nowcast, the method weather services use for the first hours. Where
a national weather service publishes its own nowcast, that is used instead.
And it scores itself: every prediction is checked against the radar a few
minutes later.

### The forecast: eleven models, corrected for your place

Each location's weather entity (`weather.home` for the first one) combines
the measured current conditions with an hourly and daily forecast blended
from 11 weather models:

- ECMWF IFS and AIFS;
- DWD ICON, NOAA GFS, Météo-France, UK Met Office;
- KNMI, DMI, MET Norway, Environment Canada and JMA.

Every day it checks how each model has done at your place. It removes the
errors each one keeps making there and gives more weight to the steady
ones.

I tested the method at six airports over 90 days, with no peeking (each
day scored only with what was known before it):

- **Temperature:** the blend beat every single model, ECMWF included, at
  all six places, and was 7–31 % more accurate than ECMWF.
- **Wind:** 11–28 % more accurate than ECMWF.
- **"Will it rain this hour?":** a better hit rate than any single model at
  four of six places.

The integration keeps that test running at your place. Diagnostic sensors
show the blend's error over the last 30 days next to every model's. You can
see for yourself whether it works where you live.

All the numbers, the method, and why it is built this way:
[docs/forecast.md](docs/forecast.md). A public benchmark at 24 random
addresses on six continents runs from November 2026, against MET Norway,
OpenWeather, Open-Meteo, DWD and ECMWF, with reports after a week, two
weeks, a month, a quarter, half a year and a year:
[docs/benchmark.md](docs/benchmark.md).

## Private and secure by design

- **Your location stays home** wherever accuracy depends on it.
  - Stations and airports are asked for an area, and the nearest one is
    picked inside Home Assistant.
  - The radar is asked for a 200–300 km map tile, and your pixel is read
    inside Home Assistant.
  - The satellite snow map is downloaded whole.
  - Only the model and forecast services get a point, rounded to about
    10 km by default. You can set it anywhere from exact to 25 km, and each
    option says what it costs.
- **No way in.** Bellweather opens no port, no web endpoint, no webhook. It
  only reads public weather data over HTTPS, from a fixed list of
  addresses, and never follows a redirect.
- **Hostile input, handled.** Every response is size-capped, decompression
  is capped, and numbers, times and texts are checked, so a broken or
  hijacked service cannot crash Home Assistant or eat its memory.
- **No third-party code.** Pure Python, no extra packages, no keys,
  no accounts.
- **Nothing leaks into logs or diagnostics.** Errors never contain the
  request URL, and diagnostics show distances only as bands.

Details: [docs/privacy.md](docs/privacy.md) and
[docs/security.md](docs/security.md).

## Setup

### Install

**With HACS** (recommended, updates arrive like any other):

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=lauden-io&repository=Bellweather&category=integration)

1. Click the button, or in HACS open the menu (⋮, top right) →
   **Custom repositories**, paste `https://github.com/lauden-io/Bellweather`,
   choose the type **Integration** and add it.
2. Find **Bellweather** in HACS and download it.
3. Restart Home Assistant.

**By hand:** copy `custom_components/bellweather` into Home Assistant's
`custom_components` folder, then restart Home Assistant.

### First setup

1. Go to Settings → Devices & services → Add integration → **Bellweather**.
2. Choose two things that apply to every location:
   - **Location shared with weather services:** about 10 km by default.
   - **Sources:** all on by default. Each one you turn off receives
     nothing.
3. Then the first location, called **Home** unless you rename it:
   - **Search** (optional): a town, a postcode or a street address. Only
     what you type is sent: a place name to Open-Meteo's place search, an
     address to OpenStreetMap's Nominatim (you can switch it to another
     server or turn it off). A house is found where OpenStreetMap has its
     number; otherwise you get the street and move the pin.
   - **The map:** your home by default. Drag the pin to the exact spot.
     Nothing is sent while you use the map.
   - **Height above sea level**, filled in for you; check it in hills.

### More places

Every place is a location of its own: a holiday home, the office, the
grandparents. **Add location** on the integration page adds one with the
same three steps. Each location is a device with its own entities, named
after it: `weather.home`, `sensor.lake_house_temperature`.

**A new location always gets `weather.<its name>`**, so the first one is
`weather.home`, the id dashboards, cards and automations usually point to.
If another integration's entity already has that id when you create the
location (Open-Meteo's, for example, is also called `weather.home`),
Bellweather renames that entity to `weather.<integration>_<name>`, for
example `weather.open_meteo_home`, and takes the id. Home Assistant moves
the renamed entity's history and statistics with it, and it keeps working.
Everything that used `weather.home` reads Bellweather from then on. The log
says what was renamed. If the id cannot be taken (another Bellweather
location has the same name, or Home Assistant cannot rename the other
entity), the location gets `weather.bellweather`, or
`weather.bellweather_<name>`, never a numbered `weather.home_2`. Entities
that already exist are never renamed: to
change one later, rename it in its settings.

Locations near each other share what they can: the radar tiles, the radar
index and the daily snow map are fetched once for all of them. A second
location nearby adds about 2–3 MB a day instead of about 23 MB (measured for
two points 3.6 km apart).

### Settings

Both are menus: open a page, change what you like, and its **Back to the
menu** button keeps it and brings you back. Nothing is saved until **Save
and close**; the X closes without saving.

- **The cog on Bellweather**, for every location: location privacy, sources,
  the **address search** (OpenStreetMap's Nominatim by default; another
  Nominatim-compatible server, or empty for off), and the **airport
  archive** (off by default; on, the forecast is corrected from day one
  instead of after about 20 days, and one more service learns your nearest
  airport).
- **The cog on a location's row**, for that location only:
  - **Name and place**: rename, search, move the pin.
  - **Your own sensors**: rain rate, rain detector, wind, gust, direction,
    visibility, snow depth, temperature, humidity, pressure. They beat every
    public source. Also a weather entity as the last fallback.
  - **Forecast members**: your own weather entities join the blend and are
    scored like the built-in models.
  - **Stations and limits**: stations to ignore, and the distance and height
    limits, one section per group. If nothing is within a limit, the next
    source in line answers: the nearest airport, then the weather model.
    The entity's `source` attribute always says which one it was.

It needs Home Assistant 2025.4 or newer.

## Credit badge

Showing Bellweather data on a site or in a README? This badge says where it
comes from. Set the height you need; the width follows. Copy the two SVG
files from [`branding/`](branding/) to your own site and point the code
there: then no third party sees your visitors.

```html
<a href="https://github.com/lauden-io/Bellweather">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="credit-dark.svg">
    <img src="credit-light.svg" alt="Weather data fused by Bellweather" height="28">
  </picture>
</a>
```

Everything else for using the name and logo, the colours for screen and
print included, is in the
[brand guidelines](branding/Bellweather-Brand-Guidelines.pdf) and the
[brand kit](branding/Bellweather-Brand-Kit.zip).

## How a value is chosen

The chains, limits and rules for every value are in
[docs/sources.md](docs/sources.md).

## Data and thanks

The data belongs to its owners. Each value's attribution is on its entity.

- RainViewer.
- EUMETNET E-SOH and the national weather services (CC BY 4.0).
- NOAA Aviation Weather Center.
- Open-Meteo (CC BY 4.0) and the weather services whose models it serves:
  ECMWF, DWD, NOAA, Météo-France, UK Met Office, KNMI, DMI, MET Norway, ECCC
  and JMA.
- U.S. National Ice Center and NSIDC (IMS snow map).
- MET Norway (nowcast, CC BY 4.0).
- Iowa Environmental Mesonet (airport archive, optional).

## Licence

Bellweather is free software under the GNU Affero General Public License,
version 3 or (at your option) any later version: [LICENSE](LICENSE),
[NOTICE](NOTICE). Use it, change it and share it. If you share a changed
version, or run one as a service for others, share its source too.

## Development

- `python -m unittest discover -s tests` runs the tests of the Home
  Assistant-free core, the safety rules included.
- `python tools/smoke.py LAT LON [ELEVATION] [PRECISION] [--forecast]
  [--archive ICAO]` runs every source, the nowcast and the forecast live
  for one public test point.
- `python tools/evaluate_forecasts.py CACHE_DIR ICAO LAT LON [END_DATE]`
  repeats the 90-day forecast test for any public airport.

Security reports: [SECURITY.md](SECURITY.md).
