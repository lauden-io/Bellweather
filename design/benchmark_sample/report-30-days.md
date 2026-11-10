# Benchmark report: 30 days

> [!WARNING]
> **Sample, not a result.** Built from data downloaded for earlier tests, to
> show what a report will look like. It uses 6 public airports instead of
> random addresses, and stand-ins for two providers (see the notes). The
> real benchmark has not started yet; its method is in
> [docs/benchmark.md](../../docs/benchmark.md).

**Period:** 10 October to 8 November 2026 · **Places:** 6 · **Lead time:**
day 1 (the forecast made the day before) · **Hours scored:** 4,311, the
same hours for everyone · **Wet hours:** 541

## How to read this

Every number says how far a forecast made the day before was from what an
official weather station then measured, averaged over every hour: lower is
closer to reality. All are scored on the same hours with the same
measurements; nobody saw the answer in advance. Details: [What "more
accurate" means](../../docs/benchmark.md#what-more-accurate-means).

## Summary

Lower is better, except the wet-hour score. The best value in each column
is in bold.

| | Temperature | Dew point | Wind speed | Pressure | Wet hours (CSI) ↑ |
|---|---:|---:|---:|---:|---:|
| **Bellweather** | **0.88 °C** | **0.80 °C** | **3.13 km/h** | **0.48 hPa** | **0.49** |
| *Bellweather without correction (control)* | *0.93 °C* | *0.84 °C* | *3.39 km/h* | *0.67 hPa* | *0.49* |
| MET Norway (Yr)¹ | 1.06 °C | 1.08 °C | 4.00 km/h | 0.78 hPa | 0.40 |
| OpenWeather² | – | – | – | – | – |
| Open-Meteo | 1.07 °C | 1.01 °C | 3.50 km/h | 0.93 hPa | 0.43 |
| DWD³ | 1.01 °C | 1.01 °C | 4.02 km/h | 0.87 hPa | 0.40 |
| ECMWF IFS | 1.13 °C | 1.10 °C | 3.93 km/h | 0.64 hPa | 0.42 |

Competitors are listed by how many Home Assistant installations use them:
MET Norway 479,240, OpenWeather 25,327, Open-Meteo 19,871, DWD 10,560
(Home Assistant analytics, 8 November 2026). ECMWF IFS is a weather model
used by most providers; it is shown as a reference.

**Compared with the best other provider in each column:** temperature
13 % lower error (DWD 1.01 °C), dew point 21 % (Open-Meteo and DWD
1.01 °C), wind 11 % (Open-Meteo 3.50 km/h), pressure 25 % (ECMWF IFS
0.64 hPa), wet hours 0.06 higher (Open-Meteo 0.43).

## Temperature by place

| | Paris | Oslo | Vienna | Chicago | London | Madrid |
|---|---:|---:|---:|---:|---:|---:|
| **Bellweather** | **0.91** | **0.98** | **0.87** | **0.81** | **0.67** | 1.03 |
| *control* | *0.96* | *1.04* | *0.86* | *0.93* | *0.78* | *1.03* |
| MET Norway (Yr)¹ | 1.02 | 1.12 | 1.00 | 1.12 | 0.86 | 1.23 |
| OpenWeather² | – | – | – | – | – | – |
| Open-Meteo | 1.00 | 1.12 | 1.14 | 1.42 | 0.79 | **0.97** |
| DWD³ | 1.01 | 1.02 | 1.09 | 1.08 | 0.90 | 0.98 |
| ECMWF IFS | 1.01 | 1.50 | 0.97 | 1.19 | 0.91 | 1.20 |

Mean absolute error in °C. Bold: best of the providers; the control is not
counted.

## Reading this month

- Bellweather had the lowest error in all five columns.
- It was not best everywhere: at Madrid, Open-Meteo (0.97 °C) and DWD
  (0.98 °C) were better than Bellweather (1.03 °C) for temperature.
- The correction helped most for pressure (0.67 → 0.48 hPa) and wind
  (3.39 → 3.13 km/h); for temperature it made no real difference at
  Vienna (0.86 → 0.87 °C) and Madrid (1.03 → 1.03 °C).
- Over the same 30 days the week above went the other way for
  temperature: a single week is not enough to judge.

## Notes

1. Stand-in: MET Norway's model as served by Open-Meteo, not its
   Locationforecast product. The real benchmark asks Locationforecast.
2. OpenWeather keeps no past forecasts, so this sample has none. The real
   benchmark records them from the first day.
3. Stand-in: DWD's ICON model as served by Open-Meteo, not its MOSMIX
   product. The real benchmark asks MOSMIX.

Wet hours: CSI = hits / (hits + misses + false alarms); 1 is perfect.
Bellweather and the control share the same wet-hour rule (precipitation
is not corrected), so their scores are the same.

Data: Iowa Environmental Mesonet (airport reports), Open-Meteo (CC BY 4.0).
