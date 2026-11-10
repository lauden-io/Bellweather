# Benchmark report: 7 days

> [!WARNING]
> **Sample, not a result.** Built from data downloaded for earlier tests, to
> show what a report will look like. It uses 6 public airports instead of
> random addresses, and stand-ins for two providers (see the notes). The
> real benchmark has not started yet; its method is in
> [docs/benchmark.md](../../docs/benchmark.md).

**Period:** 2 to 8 November 2026 · **Places:** 6 · **Lead time:** day 1
(the forecast made the day before) · **Hours scored:** 1,006, the same
hours for everyone · **Wet hours:** 87

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
| **Bellweather** | 1.08 °C | **0.91 °C** | **2.70 km/h** | **0.53 hPa** | 0.16 |
| *Bellweather without correction (control)* | *1.01 °C* | *0.96 °C* | *2.68 km/h* | *0.81 hPa* | *0.16* |
| MET Norway (Yr)¹ | 1.15 °C | 1.02 °C | 3.22 km/h | 0.93 hPa | 0.21 |
| OpenWeather² | – | – | – | – | – |
| Open-Meteo | 1.11 °C | 1.04 °C | 2.89 km/h | 1.08 hPa | 0.12 |
| DWD³ | **0.97 °C** | 1.01 °C | 3.10 km/h | 1.11 hPa | 0.14 |
| ECMWF IFS | 1.26 °C | 1.18 °C | 3.13 km/h | 0.78 hPa | **0.22** |

Competitors are listed by how many Home Assistant installations use them:
MET Norway 479,240, OpenWeather 25,327, Open-Meteo 19,871, DWD 10,560
(Home Assistant analytics, 8 November 2026). ECMWF IFS is a weather model
used by most providers; it is shown as a reference.

## Temperature by place

| | Paris | Oslo | Vienna | Chicago | London | Madrid |
|---|---:|---:|---:|---:|---:|---:|
| **Bellweather** | 1.23 | 1.51 | **0.86** | 1.19 | **0.66** | 1.02 |
| *control* | *1.21* | *1.45* | *0.86* | *0.83* | *0.63* | *1.05* |
| MET Norway (Yr)¹ | 1.22 | 1.82 | 1.05 | 1.01 | 0.68 | 1.11 |
| OpenWeather² | – | – | – | – | – | – |
| Open-Meteo | **1.03** | 1.82 | 1.18 | 1.05 | 0.76 | **0.82** |
| DWD³ | 1.04 | **1.01** | 1.10 | 1.07 | 0.78 | 0.83 |
| ECMWF IFS | 1.21 | 2.50 | 1.02 | **0.97** | 0.72 | 1.13 |

Mean absolute error in °C. Bold: best of the providers; the control is not
counted.

## Reading this week

- Bellweather had the lowest error for dew point, wind and pressure, and
  not for temperature or wet hours.
- Its correction made temperature worse this week: the control, the same
  models without correction, was better at four of six places, most at
  Chicago (0.83 against 1.19 °C). The correction learns from the 30 days
  before; a week whose weather differs from them can go either way.
- Only 87 wet hours: too few for a firm wet-hour score.
- One week is about 1,000 hours. Differences of a few hundredths of a
  degree are within chance. The 30-day report says more.

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
