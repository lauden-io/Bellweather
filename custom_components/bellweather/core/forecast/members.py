"""Forecast members: what each model says for every coming hour.

Open-Meteo serves many national and global models from one keyless API,
in one request. Its terms allow personal home automation for free under
10,000 calls a day; the data is CC BY 4.0.

Time convention used everywhere in Bellweather's forecast: an hour is the
hour that *starts* at its timestamp. Instant values (temperature, wind,
cloud) are taken at that moment; amounts (precipitation, snowfall) are the
sum over that hour. Open-Meteo gives an amount at T as the sum of the hour
*before* T, so its amounts are shifted back by one hour when read.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from ..privacy import Precision, shared_point
from ..model import Location
from ..safety import MAX_JSON_BYTES, finite
from ..sources.base import Http

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
PREVIOUS_RUNS_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
ATTRIBUTION = "Weather data by Open-Meteo.com (CC BY 4.0)"

# The members I measured (docs/forecast.md). "seamless" joins a model's
# regional high-resolution grid with its global one.
DEFAULT_MODELS = [
    "ecmwf_ifs025",
    "ecmwf_aifs025_single",
    "icon_seamless",
    "gfs_seamless",
    "meteofrance_seamless",
    "ukmo_seamless",
    "knmi_seamless",
    "dmi_seamless",
    "metno_seamless",
    "gem_seamless",
    "jma_seamless",
]

HOURLY = [
    "temperature_2m", "relative_humidity_2m", "dew_point_2m", "pressure_msl", "cloud_cover",
    "wind_speed_10m", "wind_gusts_10m", "wind_direction_10m", "precipitation", "snowfall",
    "weather_code", "visibility", "uv_index",
]
AMOUNTS = ("precipitation", "snowfall")
SNOW_CM_PER_MM = 0.7  # Open-Meteo: 7 cm of snow is 10 mm of water


@dataclass
class Hour:
    """One member's values for the hour that starts at the key time."""

    temperature: float | None = None  # degrees C
    humidity: float | None = None  # %
    dew_point: float | None = None  # degrees C
    pressure: float | None = None  # hPa, sea level
    cloud_cover: float | None = None  # %
    wind_speed: float | None = None  # km/h
    wind_gust: float | None = None  # km/h
    wind_direction: float | None = None  # degrees, from
    precipitation: float | None = None  # mm in this hour
    snow_water: float | None = None  # mm of water that falls as snow in this hour
    weather_code: int | None = None  # WMO code
    visibility: float | None = None  # km
    uv_index: float | None = None  # WHO UV index; only a few models give it


Member = dict[datetime, Hour]


@dataclass
class MemberSet:
    """All members of one forecast run."""

    members: dict[str, Member] = field(default_factory=dict)
    fetched: datetime | None = None
    attribution: dict[str, str] = field(default_factory=dict)


def _utc(text: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(text)[:25]).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


# Physical limits per variable; anything outside is dropped as broken data.
LIMITS = {
    "temperature_2m": (-90, 60), "relative_humidity_2m": (0, 100), "dew_point_2m": (-90, 40),
    "pressure_msl": (850, 1090), "cloud_cover": (0, 100), "wind_speed_10m": (0, 400), "wind_gusts_10m": (0, 500),
    "wind_direction_10m": (0, 360), "precipitation": (0, 500), "snowfall": (0, 500), "weather_code": (0, 99),
    "visibility": (0, 1_000_000), "uv_index": (0, 25),
}


def _value(col: list, i: int, var: str) -> float | None:
    if not 0 <= i < len(col):
        return None
    number = finite(col[i])
    low, high = LIMITS.get(var, (float("-inf"), float("inf")))
    return number if number is not None and low <= number <= high else None


def parse_forecast(data: dict[str, Any], models: list[str]) -> dict[str, Member]:
    hourly = data.get("hourly") if isinstance(data, dict) and isinstance(data.get("hourly"), dict) else {}
    times = [_utc(t) for t in (hourly.get("time") or [])[: 24 * 20]]
    out: dict[str, Member] = {}
    for model in models:
        def series(var: str) -> list:
            # One model: plain names; several: names end in _<model>.
            return hourly.get(f"{var}_{model}") or (hourly.get(var) if len(models) == 1 else None) or []

        cols = {var: (series(var) if isinstance(series(var), list) else []) for var in HOURLY}
        temps = cols["temperature_2m"][: len(times) + 1]
        if not any(_value(temps, i, "temperature_2m") is not None for i in range(len(temps))):
            continue  # the model has no data for this point (outside its area, or switched off)
        member: Member = {}
        for i, t in enumerate(times):
            if t is None:
                continue

            def at(var: str, j: int = i):
                return _value(cols[var], j, var)

            snow_cm = at("snowfall", i + 1)
            vis = at("visibility")
            code = at("weather_code")
            member[t] = Hour(
                temperature=at("temperature_2m"), humidity=at("relative_humidity_2m"), dew_point=at("dew_point_2m"),
                pressure=at("pressure_msl"), cloud_cover=at("cloud_cover"), wind_speed=at("wind_speed_10m"),
                wind_gust=at("wind_gusts_10m"), wind_direction=at("wind_direction_10m"),
                precipitation=at("precipitation", i + 1),  # the sum given at T+1h belongs to the hour starting at T
                snow_water=None if snow_cm is None else snow_cm / SNOW_CM_PER_MM,
                weather_code=None if code is None else int(code),
                visibility=None if vis is None else vis / 1000.0,
                uv_index=at("uv_index"),
            )
        out[model] = member
    return out


async def fetch_forecast(
    http: Http, location: Location, precision: Precision, models: list[str], days: int = 10
) -> dict[str, Member]:
    lat, lon = shared_point(location, precision)
    data = await http.get_json(FORECAST_URL, {
        "latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}", "hourly": ",".join(HOURLY),
        "models": ",".join(models), "forecast_days": str(days), "timezone": "GMT",
    }, limit=MAX_JSON_BYTES)
    return drop_copies(await asyncio.to_thread(parse_forecast, data or {}, models))


def _signature(h: Hour) -> tuple | None:
    values = (h.temperature, h.wind_speed, h.precipitation, h.cloud_cover, h.humidity, h.pressure)
    return values if sum(v is not None for v in values) >= 3 else None


def drop_copies(members: dict[str, Member]) -> dict[str, Member]:
    """Outside their own area the regional "seamless" members are filled
    with another model's values (at Nairobi, 2026-11-10, knmi, dmi and metno
    all returned ECMWF IFS exactly). Counted three times, that model would
    outweigh the rest. So for every hour, a member whose values equal an
    earlier member's in every field is left out for that hour."""
    seen: dict[datetime, set[tuple]] = {}
    out: dict[str, Member] = {}
    for name, member in members.items():
        kept: Member = {}
        for t, hour in member.items():
            sig = _signature(hour)
            if sig is not None and sig in seen.setdefault(t, set()):
                continue
            if sig is not None:
                seen[t].add(sig)
            kept[t] = hour
        if kept:
            out[name] = kept
    return out


# --- what the models said one day earlier (for the verification) -----------

PREVIOUS = {
    "t": "temperature_2m_previous_day1", "w": "wind_speed_10m_previous_day1", "p": "precipitation_previous_day1",
    "d": "dew_point_2m_previous_day1", "pr": "pressure_msl_previous_day1",
}
# The instant values of the day-1 run, with their physical limits' names.
PREVIOUS_INSTANT = {"t": "temperature_2m", "w": "wind_speed_10m", "d": "dew_point_2m", "pr": "pressure_msl"}


def parse_previous(data: dict[str, Any], models: list[str]) -> dict[str, dict[datetime, dict[str, float]]]:
    """member -> hour -> {"t", "w", "d", "pr", "p"}: the run from one day
    earlier (temperature, wind, dew point, sea-level pressure, precipitation).
    "p" is the amount of the hour that starts at the key (shifted like above)."""
    hourly = data.get("hourly") if isinstance(data, dict) and isinstance(data.get("hourly"), dict) else {}
    times = [_utc(t) for t in (hourly.get("time") or [])[: 24 * 120]]
    out: dict[str, dict[datetime, dict[str, float]]] = {}
    for model in models:
        cols = {k: hourly.get(f"{v}_{model}") or (hourly.get(v) if len(models) == 1 else None) or []
                for k, v in PREVIOUS.items()}
        cols = {k: (v if isinstance(v, list) else []) for k, v in cols.items()}
        member: dict[datetime, dict[str, float]] = {}
        for i, t in enumerate(times):
            if t is None:
                continue
            row: dict[str, float] = {}
            for key, var in PREVIOUS_INSTANT.items():
                value = _value(cols[key], i, var)
                if value is not None:
                    row[key] = value
            amount = _value(cols["p"], i + 1, "precipitation")
            if amount is not None:
                row["p"] = amount
            if row:
                member[t] = row
        if member:
            out[model] = member
    return out


async def fetch_previous(
    http: Http, latitude: float, longitude: float, models: list[str], start: datetime, end: datetime
) -> dict[str, dict[datetime, dict[str, float]]]:
    """The day-1 runs between two dates, for any point. Used at the shared
    point, and at the public position of an airport for the first 60 days."""
    data = await http.get_json(PREVIOUS_RUNS_URL, {
        "latitude": f"{latitude:.4f}", "longitude": f"{longitude:.4f}",
        "hourly": ",".join(PREVIOUS.values()), "models": ",".join(models),
        "start_date": start.date().isoformat(), "end_date": (end + timedelta(days=1)).date().isoformat(),
        "timezone": "GMT",
    }, limit=MAX_JSON_BYTES)
    return await asyncio.to_thread(parse_previous, data or {}, models)
