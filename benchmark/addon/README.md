# Bellweather Benchmark

Records, every 4 hours from 2026-11-11 00:00 Central European Time (2026-11-10 23:00 UTC), each provider's
forecast for the random addresses in `app/points.json`, and every hour the
reports of the stations they are scored against. Method: `docs/benchmark.md`.

Options:
- `openweather_api_key`: your OpenWeather key (free plan); empty: OpenWeather is skipped.
- `contact`: an e-mail address or URL added to the User-Agent (MET Norway asks for one).

Data stay in the add-on's own storage (`/data/benchmark.sqlite`). It creates no entities.
