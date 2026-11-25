# Changelog

## 0.1.3 (2026-11-25)

- **No more snow that never reaches the ground.** The radar's snow was
  converted to an amount with the formula for rain, which makes a faint
  snow echo look like light snow. On 25 November 2026 a weak band (about
  10 dBZ) showed as 0.15 mm/h of snow for hours, while the four nearest
  official rain gauges measured nothing. Snow now has its own
  formula (Gunn and Marshall, 1958): that echo is 0.07 mm/h of water, below
  what counts as precipitation, and the weather stays cloudy. The radar
  nowcast uses it too.
- A radar echo below 0.1 mm/h is no longer precipitation anywhere: it no
  longer rounds up to 0.1 mm/h, and it adds nothing to the last hour's
  amount.
- **The weather entity says whether there is snow on the ground**
  (`snow_on_ground`, `snow_depth`), apart from whether snow is falling, so
  a wall panel can show falling snow over bare ground, and a white
  landscape after the snow has stopped.

## 0.1.2 (2026-11-16)

- **Corrections learned elsewhere count only in part.** Bellweather learns
  each model's error where it has measurements, often an airport or a
  station some kilometres from the home. Measured at 9 pairs of places over
  90 days, removing that whole error helped temperature by only 1 % and
  made wind 6 % worse, because an airport's weather is partly its own (an
  open field, the sea next to it). Now hours measured elsewhere remove half
  of a model's error for temperature and dew point, a quarter for wind, and
  all of it for pressure, which is the same over tens of kilometres
  (32 % better). Measured at the home itself the correction is far better
  (8 to 15 %), so your own sensors still remove it in full.
- **Your own sensors teach the forecast only when you say so.** A new
  switch in "Your own sensors": the sensors are placed like a weather
  station's (shaded, ventilated, 1.5 to 2 m above open ground, away from
  walls). Off by default: a sensor by a warm wall would teach the forecast
  the wall's warmth. Off, your sensors still show the weather now, and the
  forecast learns from the best station or airport instead; your own
  barometer always counts. Values logged before this version that may
  have come from such a sensor are forgotten once, as the log did not say
  which hours were yours.
- Your own values and the airport archive are joined per value, not per
  hour, so an hour with only your wind still takes the archive's
  temperature.
- **Pressure from airports that report only QNH** is now converted to
  sea-level pressure with the airport's height and its temperature now and
  12 hours earlier. QNH as it is differed from the models' sea-level
  pressure by 6.6 hPa on average at Medellín (1,491 m), and the correction
  learned that difference instead of the models' error. Without a known
  height the pressure is left out rather than taken as it is.
- The stored corrections are rebuilt once after the update.
- **Benchmark:** pressure is scored as sea-level pressure everywhere, from
  the first capture made with the conversion (docs/benchmark.md, Changes).
  The script of the new measurement is `tools/evaluate_transfer.py`.

## 0.1.1 (2026-11-12)

- **Fewer calls to Open-Meteo.** Open-Meteo counts every point of a request
  as one call, against a daily limit per IP address. Its current values are
  now asked once per quarter hour, as often as they change, so 96 calls a day
  per location instead of 288. Station heights are asked once per station,
  shared by every location and kept across restarts in
  `.storage/bellweather.station_heights` (see docs/privacy.md); before, every
  restart asked them again.
- **Address search setting:** the default server's address is now filled in
  by the integration, as Home Assistant's checks require. The text reads the
  same.
- **Brand kit:** the two misuse examples (a ground too close to cobalt, a
  busy pattern) are redrawn so they show the problem.

## 0.1.0 (2026-11-11)

The first published version.

- **Weather now, measured:** every value from the most direct measurement
  available: your own sensors, radar above the home, the nearest official
  station, the nearest airport, and a weather model only when nothing
  measured is close and fresh enough. Every value says where it came from.
- **Precipitation now and in the next two hours** from radar, with a radar
  nowcast that scores itself; MET Norway's nowcast in the Nordic countries.
- **Forecast from eleven models**, each corrected for its own steady error
  at the location (temperature, wind, dew point and pressure) and weighted
  by its record; hourly for 7 days, daily for about 9, with UV index,
  feels-like, dew point and pressure. Diagnostic sensors show its error
  over the last 30 days next to every model's.
- **Several locations** in one entry, each a device of its own, with a place
  search (towns and street addresses) and the map.
- **Private and secure by default:** points rounded to 10 km for services
  that need one, stations and radar asked for areas, no cookies, HTTPS
  only, no redirects, every answer checked. See docs/privacy.md and
  docs/security.md.
- **Public benchmark** add-on and method (docs/benchmark.md).
- **Install with HACS** as a custom repository, or by hand.
- **Free software** under the GNU AGPL, version 3 or any later version.
