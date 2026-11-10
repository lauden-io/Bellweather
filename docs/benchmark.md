# Benchmark plan: how good is the forecast, in numbers

Status: written on 2026-11-10 **before any result exists**, so the results
cannot shape it. Recording started on **11 November 2026 at 00:00 Central
European Time** (10 November, 23:00 UTC). Every later change is listed at the end
with its date and reason.

## What "more accurate" means

A forecast is more accurate when it is closer to what really happened. What
really happened is not our opinion or a provider's: it is what an official,
calibrated weather station measured.

An example with made-up numbers:

1. On Monday every provider says what the temperature will be on Tuesday at
   14:00. One says 12.3 °C, another 10.9 °C.
2. On Tuesday at 14:00 the station measures 11.5 °C.
3. The first was 0.8 °C off, the second 0.6 °C.
4. This is done for every hour, every place and every provider, and the
   sizes of the misses are averaged: the **mean absolute error**. "0.88 °C"
   means that the forecast for the next day was, on average, 0.88 °C away
   from what was measured. Lower is better.

For rain the question is whether it was right: a wet hour it called, a wet
hour it missed, a wet hour it called that stayed dry. The **critical success
index** (hits / (hits + misses + false alarms)) puts that in one number; 1
is perfect.

Why this is fair:

- Every forecast is made before the weather happens, so nobody can see the
  answer.
- Everyone is scored on the same hours with the same measurements. An hour
  missing from any provider is left out for all.
- Bellweather's correction learns only from days before the one being
  scored, never from the future, and the scoring station is hidden from it.
- The measurements come from public archives and services, and the script
  and data are published: anyone can recompute every number.

What it cannot tell:

- The station is not at the address. The gap is listed for every place, and
  it is the same for every provider.
- Measurements are not perfect either: an official thermometer is good to a
  few tenths of a degree (WMO guidance), small next to forecast errors of
  about a degree, and the same for everyone.
- Little data can mislead. A week is about 4,000 scored hours, and a week can
  go the other way from a month; that is why there are reports after two
  weeks, a month, a quarter, half a year and a year.
- Only what is measured can be judged. Where nothing measures a value (UV,
  in most places), nothing is claimed.

The aim is to show what is true, not to claim anything. Every number will
be published, including the ones where Bellweather loses. Bellweather is a
free, open-source, non-commercial project, and so is this benchmark. Should
it ever be used commercially, the results of providers whose terms allow
only non-commercial use are taken down.

## Where: random addresses, like real users

People do not live at weather stations. So the forecasts are asked for
**random addresses in populated places**, the way anyone would use them,
and every provider gets exactly the same coordinates.

- **Choosing the addresses:** cities are drawn at random from GeoNames
  (towns of 15,000 people or more, CC BY 4.0), each with a chance in
  proportion to its population; the address is a random point up to 3 km
  from the town centre. The random seed is published in this document before the
  first capture, so anyone can redraw the same list.
- **Truth:** a point is kept only if an official station that reports air
  temperature lies within 5 km and within 50 m of height (heights of both
  from the same elevation model), and it reported at least 20 of the last
  24 hours on the day of the draw. The stations are those of E-SOH, the
  European official network, and of the worldwide airport reports (METAR,
  aviationweather.gov). NOAA's Integrated Surface Database was the first
  choice, but on 2026-11-10 its public archive had no data for 2026, so it
  could not serve. E-SOH keeps only recent days, so the add-on records every
  scoring station's reports itself, every hour; airport reports are also
  archived by the Iowa Environmental Mesonet. The station's distance and
  height difference are listed for every point. It is the nearest real
  measurement, not the address itself; that gap is the same for every
  provider.
- **How many:** 4 addresses per continent on six continents, 24 in all,
  the first 4 accepted draws per continent. Seed **20261111**; the script
  is `benchmark/select_points.py`, the list `benchmark/points.json`. In
  Europe all four scoring stations are E-SOH stations; elsewhere all are
  airports, because outside Europe the airport reports are the official
  hourly stations this method can reach.
- **Nobody sees the answer:** at each address the scoring station is never
  used by Bellweather, neither as a current value nor as its airport
  archive, so it cannot learn from it.
- **How Bellweather runs:** exactly as in Home Assistant at the exact
  address (precision "exact"), with the airport archive on, using the
  nearest airport within 60 km other than the scoring station. Where there
  is none (9 of the 24 addresses), or the archive has no reports for that
  airport (3 more, found at the first capture), it has no correction yet and equals its
  control, as a new installation without the archive does. Like a Home
  Assistant installation, it keeps its own log of the measured values it
  chooses near the address (other official stations and airports, never the
  scoring station) from the start, and corrects with it once 20 days are
  logged.

## Who is compared

| Provider | Data and licence | Can its forecasts be kept for scoring? |
|---|---|---|
| Bellweather | its own blend, run at each address exactly as in Home Assistant | yes, our own output |
| Bellweather without correction | the plain mean of the same 11 models | yes |
| ECMWF IFS | via Open-Meteo, CC BY 4.0, non-commercial free plan | yes |
| Open-Meteo "best match" | CC BY 4.0, non-commercial free plan | yes |
| MET Norway (Yr) | Locationforecast, CC BY 4.0 / NLOD | yes |
| OpenWeather | 5-day/3-hour forecast (free plan); ODbL. It gives humidity, not dew point: its dew point is computed by the benchmark from its temperature and humidity | yes: no caching limit or benchmark clause found (terms of sale, pricing page) |
| DWD MOSMIX | Deutscher Wetterdienst open data, CC BY 4.0 | yes; it forecasts for about 5,400 fixed points, so an address gets the nearest one, as with DWD-based apps. It is scored only where that point is within 25 km (18 of the 24 addresses, fixed before the start); elsewhere DWD has no point near enough for a fair comparison (up to 218 km) and its line shows "–" |

"Bellweather without correction" costs no extra request: it is the same
models' plain mean, before Bellweather removes each model's local error
and weights them. Bellweather's main claim is that this correction makes
the forecast better; this line shows, in numbers and per place, how much
it does, and where it does not. In the report it stands next to
Bellweather, marked as its control.

Checked on 2026-11-10 and left out:

- **Pirate Weather:** its free plan is "for personal use", which a public
  benchmark is not, so it is left out.

- **Google Weather API:** hourly forecasts may be kept for 1 hour, daily
  forecasts for 24 hours (Maps Service Specific Terms 21.2). Forecasts for
  tomorrow and later cannot be kept until they can be scored, so no
  long-term statistics are possible.
- **Apple WeatherKit:** data may not be cached or stored except temporarily
  for performance, nor used in a derived database (Developer Program
  License Agreement, Attachment 8).
- **Tomorrow.io:** its terms forbid publishing benchmark results and use by
  competitors (Subscription Terms 8.1, preamble).
- **AccuWeather:** its terms forbid using the data to "rate, rank, review or
  otherwise evaluate" AccuWeather, and forbid archives (Terms of Use).
- **Met Office DataHub:** the products may not be used to promote one's own
  products (T&Cs 3.6); a benchmark published by the maker of a forecast is
  close to that.
- **Foreca:** the free plan is non-commercial, but the licence covers only
  the uses its service description names, which do not include keeping
  forecasts for later scoring.

## What is scored

- **Values:** temperature, dew point, wind speed, sea-level pressure,
  "wet hour" (any precipitation reported), and the day's highest and
  lowest temperature.
- **Lead times:** 0–6 hours, day 1 (24–47 hours ahead), day 2, day 3.
- **Pairing:** an hour counts only if every compared provider has a value
  for it, so all are scored on exactly the same hours. The number of hours
  is always shown.
- **Measures:** mean absolute error; for wet hours the critical success
  index and, where a provider gives a probability, the Brier score.
- **Capture:** every 4 hours, at 00, 04, 08, 12, 16 and 20 Central European
  Time (23, 03, 07, 11, 15 and 19 UTC, fixed in UTC all year), everyone
  asked at the same moment for the same address.

## When it is published

After 1 week, 2 weeks, 30 days, 3 months, 6 months and 1 year: on 18 and
25 November and 11 December 2026, and 11 February, 11 May and 11 November
2027. Each report is a fixed set of tables, generated by the same script,
in `docs/benchmark/`, and opens with the explanation above. One week says
little; the report says so.

## How it runs

A small Home Assistant add-on on a home server, running all year
(`benchmark/addon`, code in `benchmark/app`: `runner.py` records, `score.py`
scores, `report.py` writes the reports). It creates no entities, keeps its
data in the add-on's own storage, and keeps API keys only in the add-on's
settings, never in this repository. Each report is written automatically
at 05:00 UTC the morning after its period ends. The
script, the address list, the seed and the raw data of every provider whose
licence allows it are published with each report, so anyone can repeat the
test.

Calls: 24 addresses × 6 captures a day is about 4,300 calls a month per
provider, within every free plan above.

**Gaps.** A home server can stop: a power cut, a restart. Nothing missed is
made up from later data, and nothing is left out quietly:

- A capture missed while the host was off is never made later, because a
  later capture would see newer forecasts. Every provider misses the same
  captures, so the comparison stays fair, on fewer hours. Each report says
  how many captures were made and lists the missed ones.
- Station reports are fetched again automatically. Every successful
  request is remembered, and the next one starts where the covered time
  ends, as far back as the service keeps reports: aviationweather.gov 48
  hours, E-SOH about 24 hours (both checked on 11 November 2026). A gap
  shorter than that leaves no hole in the measurements; a failed request is
  made up the same way.
- Bellweather's own log of local measurements is not filled in afterwards:
  a Home Assistant installation that was off would not have it either.

## Changes

- 2026-11-10: plan written (airports, Google partly).
- 2026-11-10: terms of nine more providers checked; OpenWeather, DWD MOSMIX
  and Pirate Weather added; Tomorrow.io, AccuWeather, Met Office and Foreca
  left out.
- 2026-11-10: random addresses in populated places
  instead of airports (users live at addresses, not at stations); Google
  dropped (no long-term statistics within its caching limits); the project
  is non-commercial.
- 2026-11-10: Pirate Weather, Foreca, Met Office,
  Tomorrow.io and AccuWeather are left out.
- 2026-11-10: explanation of "more accurate" added; a 2-week report added;
  scoring stations from E-SOH and airport reports instead of NOAA ISD (no
  2026 data in its archive); seed and address list fixed; Bellweather's
  setup described. Recording starts 2026-11-11 00:00 Central European Time.
- 2026-11-10, before the start: scoring and reports built (`score.py`,
  `report.py`); Bellweather's own log of local measurements runs from the
  start; DWD is scored only within 25 km of its point.
- 2026-11-11: a power cut stopped the host from about 07:35 to 16:42 UTC;
  the captures at 11:00 and 15:00 UTC were missed. Since add-on version
  0.2.3, station reports missed during a gap are fetched again automatically
  (this gap included), each report lists missed captures, and the daily high
  and low use the latest capture that every provider answered, so all are
  judged at the same lead.
