"""One forecast from many members.

Per hour:

- temperature, wind speed, dew point and sea-level pressure:
  bias-corrected, error-weighted mean (`skill.combine`), or the plain mean
  until 20 days of verification exist;
- humidity, cloud, gusts: plain mean (not verified, so not corrected);
- wind direction: mean of the wind vectors, so 350 and 10 degrees give 0,
  not 180;
- precipitation: the hour is wet when at least half of the members say at
  least 0.1 mm; the amount is the members' mean; the probability is the
  share of members that say at least 0.1 mm. This share is not a calibrated
  probability, and the docs say so;
- type: snow when at least 80 % of the mean amount falls as snow, sleet from
  20 %, rain below. Freezing rain when at least 30 % of the members give a
  freezing-rain code. Thunder when at least 30 % give a thunderstorm code.
  These cut points are my choice;
- condition: the same rules as "Weather now", with the sun's height at
  that hour;
- UV index: the mean of the members that compute their own. Only a few
  models give one, and some members pass on another model's UV unchanged
  (in my test KNMI and MET Norway gave exactly GFS's values everywhere):
  a member whose UV series is an exact copy of an earlier member's is
  counted once. Nothing measures UV near most homes, so it is not
  verified, like dew point and pressure.

Per day (local days): highest and lowest temperature, the sum of the
blended amounts, the share of members whose day total reaches 1 mm (the
usual "rain day" threshold), the strongest wind and gust, the mean
direction and cloud, the mean humidity, dew point and pressure, the
highest feels-like temperature and UV index, and the most significant
condition of the day.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo

from ..condition import Condition, condition
from ..derive import DAYLIGHT_ELEVATION, apparent_temperature, solar_elevation
from ..model import IntensityClass, Location, PrecipType, intensity_class
from .members import Hour, Member
from .skill import WET_SHARE, Corrections, combine, wet_share

SNOW_SHARE = 0.8
SLEET_SHARE = 0.2
CODE_SHARE = 0.3
FREEZING_CODES = {56, 57, 66, 67}
THUNDER_CODES = {95, 96, 99}
FOG_CODES = {45, 48}
RAIN_DAY_MM = 1.0
MIN_MEMBERS = 3

WET_CONDITIONS = {
    Condition.LIGHTNING_RAINY, Condition.HAIL, Condition.SNOWY, Condition.SNOWY_RAINY, Condition.POURING, Condition.RAINY,
}

# How significant a condition is when one has to stand for a whole day.
RANK = {
    Condition.LIGHTNING_RAINY: 9, Condition.LIGHTNING: 8, Condition.HAIL: 8, Condition.SNOWY: 7,
    Condition.SNOWY_RAINY: 7, Condition.POURING: 6, Condition.RAINY: 5, Condition.FOG: 4,
    Condition.WINDY_VARIANT: 3, Condition.WINDY: 3, Condition.CLOUDY: 2, Condition.PARTLYCLOUDY: 1,
    Condition.SUNNY: 0, Condition.CLEAR_NIGHT: 0, Condition.EXCEPTIONAL: 9,
}


@dataclass
class ForecastHour:
    time: datetime
    members: int
    condition: str | None
    temperature: float | None
    apparent_temperature: float | None
    dew_point: float | None
    humidity: float | None
    pressure: float | None
    wind_speed: float | None
    wind_gust: float | None
    wind_bearing: float | None
    cloud_cover: float | None
    precipitation: float | None
    precipitation_probability: int | None
    precip_type: str
    is_daytime: bool
    uv_index: float | None = None


@dataclass
class ForecastDay:
    day: date
    start: datetime  # local midnight
    members: int
    condition: str | None
    temperature: float | None
    templow: float | None
    precipitation: float | None
    precipitation_probability: int | None
    wind_speed: float | None
    wind_gust: float | None
    wind_bearing: float | None
    cloud_cover: float | None
    humidity: float | None
    apparent_temperature: float | None = None  # the day's highest
    dew_point: float | None = None  # mean
    pressure: float | None = None  # mean, sea level
    uv_index: float | None = None  # the day's highest


def own_uv(members: dict[str, Member]) -> set[str]:
    """Members with a UV series of their own: a series that is an exact copy
    of an earlier member's (same value at every shared hour, at least 12 of
    them) is a pass-through and counted once."""
    kept: dict[str, dict[datetime, float]] = {}
    for name in sorted(members):
        series = {t: h.uv_index for t, h in members[name].items() if h.uv_index is not None}
        if not series:
            continue
        copy = False
        for other in kept.values():
            shared = series.keys() & other.keys()
            if len(shared) >= 12 and all(series[t] == other[t] for t in shared):
                copy = True
                break
        if not copy:
            kept[name] = series
    return set(kept)


def _mean(values: list[float | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return statistics.fmean(vals) if vals else None


def _vector_mean(directions: list[float | None], speeds: list[float | None]) -> float | None:
    u = v = 0.0
    n = 0
    for d, s in zip(directions, speeds, strict=False):
        if d is None:
            continue
        weight = s if s else 1.0
        u += weight * math.sin(math.radians(d))
        v += weight * math.cos(math.radians(d))
        n += 1
    if not n or (abs(u) < 1e-9 and abs(v) < 1e-9):
        return None
    return math.degrees(math.atan2(u, v)) % 360


def _share(codes: list[int | None], wanted: set[int]) -> float:
    known = [c for c in codes if c is not None]
    return sum(1 for c in known if c in wanted) / len(known) if known else 0.0


def _r(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value, digits)


def blend_hour(
    t: datetime, hours: dict[str, Hour], corr: Corrections, location: Location, uv_from: set[str] | None = None
) -> ForecastHour:
    def values(attr: str) -> dict[str, float]:
        return {m: getattr(h, attr) for m, h in hours.items() if getattr(h, attr) is not None}

    temp = combine(values("temperature"), corr.temperature)
    wind = combine(values("wind_speed"), corr.wind)
    if wind is not None:
        wind = max(wind, 0.0)
    gust = _mean(list(values("wind_gust").values()))
    if gust is not None and wind is not None:
        gust = max(gust, wind)
    humidity = _mean(list(values("humidity").values()))
    dew = combine(values("dew_point"), corr.dew_point)
    cloud = _mean(list(values("cloud_cover").values()))
    amounts = values("precipitation")
    share = wet_share(amounts)
    amount = _mean(list(amounts.values()))
    snow = _mean(list(values("snow_water").values()))
    codes = [h.weather_code for h in hours.values()]
    wet = share is not None and share >= WET_SHARE

    ptype = PrecipType.NONE
    if wet:
        snow_part = (snow or 0.0) / amount if amount else 0.0
        if _share(codes, FREEZING_CODES) >= CODE_SHARE:
            ptype = PrecipType.FREEZING_RAIN
        elif snow_part >= SNOW_SHARE:
            ptype = PrecipType.SNOW
        elif snow_part >= SLEET_SHARE:
            ptype = PrecipType.SLEET
        else:
            ptype = PrecipType.RAIN
    visibility = _mean(list(values("visibility").values()))
    fog = (visibility is not None and visibility < 1.0) or _share(codes, FOG_CODES) >= WET_SHARE
    elevation = solar_elevation(location.latitude, location.longitude, t + timedelta(minutes=30))
    rate = amount if amount is not None else 0.0
    cond = condition(
        precipitating=wet, precip_type=ptype.value,
        intensity_class=intensity_class(rate, ptype).value if wet else IntensityClass.DRY.value,
        thunder=_share(codes, THUNDER_CODES) >= CODE_SHARE, fog=fog,
        wind_kmh=wind, cloud_pct=cloud, sun_elevation=elevation,
    )
    uv = _mean([h.uv_index for m, h in hours.items() if uv_from is None or m in uv_from])
    feels = None
    if temp is not None and humidity is not None:
        feels = apparent_temperature(temp, humidity, wind or 0.0)
    return ForecastHour(
        time=t, members=len(hours), condition=None if cond is None else cond.value,
        temperature=_r(temp), apparent_temperature=_r(feels), dew_point=_r(dew), humidity=_r(humidity, 0),
        pressure=_r(combine(values("pressure"), corr.pressure)), wind_speed=_r(wind), wind_gust=_r(gust),
        wind_bearing=_r(_vector_mean([h.wind_direction for h in hours.values()], [h.wind_speed for h in hours.values()]), 0),
        cloud_cover=_r(cloud, 0), precipitation=_r(amount, 2),
        precipitation_probability=None if share is None else round(share * 100),
        precip_type=ptype.value, is_daytime=elevation > DAYLIGHT_ELEVATION,
        uv_index=_r(uv),
    )


def blend_hours(members: dict[str, Member], corr: Corrections, location: Location, start: datetime) -> list[ForecastHour]:
    """Hours from `start` on, while at least three members have data."""
    times = sorted({t for m in members.values() for t in m if t >= start})
    uv_from = own_uv(members)
    out: list[ForecastHour] = []
    for t in times:
        hours = {name: m[t] for name, m in members.items() if t in m and m[t].temperature is not None}
        if len(hours) < MIN_MEMBERS:
            continue
        out.append(blend_hour(t, hours, corr, location, uv_from))
    return out


def blend_days(hours: list[ForecastHour], members: dict[str, Member], zone: tzinfo, today: date) -> list[ForecastDay]:
    by_day: dict[date, list[ForecastHour]] = {}
    for h in hours:
        by_day.setdefault(h.time.astimezone(zone).date(), []).append(h)
    out: list[ForecastDay] = []
    for day, items in sorted(by_day.items()):
        if day < today or len(items) < 20:  # a day needs most of its hours
            continue
        start = datetime(day.year, day.month, day.day, tzinfo=zone)
        stop = start + timedelta(days=1)
        totals = []
        for member in members.values():
            amounts = [h.precipitation for t, h in member.items() if start <= t < stop and h.precipitation is not None]
            if len(amounts) >= 20:
                totals.append(sum(amounts))
        temps = [h.temperature for h in items if h.temperature is not None]
        precip = [h.precipitation for h in items if h.precipitation is not None]
        daytime = [h for h in items if h.is_daytime] or items
        probability = round(100 * sum(1 for x in totals if x >= RAIN_DAY_MM) / len(totals)) if totals else None
        significant = [Condition(h.condition) for h in items if h.condition]
        if probability is not None and probability < 50:
            # A day is shown wet only when most members make it a rain day.
            significant = [c for c in significant if c not in WET_CONDITIONS] or [Condition.CLOUDY]
        cond = max(significant, key=lambda c: RANK[c]) if significant else None
        if cond in (Condition.CLEAR_NIGHT,):
            cond = Condition.SUNNY
        if cond in (Condition.SUNNY, Condition.PARTLYCLOUDY, Condition.CLOUDY):
            cloud = _mean([h.cloud_cover for h in daytime])
            if cloud is not None:
                cond = Condition.CLOUDY if cloud >= 75 else Condition.PARTLYCLOUDY if cloud >= 31 else Condition.SUNNY
        out.append(ForecastDay(
            day=day, start=start, members=min(h.members for h in items), condition=None if cond is None else cond.value,
            temperature=max(temps) if temps else None, templow=min(temps) if temps else None,
            precipitation=round(sum(precip), 1) if precip else None,
            precipitation_probability=probability,
            wind_speed=max((h.wind_speed for h in items if h.wind_speed is not None), default=None),
            wind_gust=max((h.wind_gust for h in items if h.wind_gust is not None), default=None),
            wind_bearing=_r(_vector_mean([h.wind_bearing for h in items], [h.wind_speed for h in items]), 0),
            cloud_cover=_r(_mean([h.cloud_cover for h in daytime]), 0),
            humidity=_r(_mean([h.humidity for h in items]), 0),
            apparent_temperature=max((h.apparent_temperature for h in items if h.apparent_temperature is not None), default=None),
            dew_point=_r(_mean([h.dew_point for h in items])),
            pressure=_r(_mean([h.pressure for h in items])),
            uv_index=max((h.uv_index for h in items if h.uv_index is not None), default=None),
        ))
    return out
