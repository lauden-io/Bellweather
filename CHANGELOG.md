# Changelog

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
