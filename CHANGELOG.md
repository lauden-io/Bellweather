# Changelog

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
