"""Pick one value per field from all readings.

Each field has a chain of source kinds, most trusted first. The first kind
that has a usable reading wins; inside a kind the nearest reading wins, then
the one with the lower priority number, then the newest. A reading is usable
when it is fresh enough, close enough and, for stations, not too far above
or below the home.

Every limit below is a starting value, to be tuned with real data. The user
can change the distance and height limits per group of fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from .condition import condition
from .derive import apparent_temperature, cardinal, solar_elevation
from .model import Field, IntensityClass, Kind, Location, PrecipType, Reading
from .safety import not_future

L, R, S, A, SAT, M = Kind.LOCAL, Kind.RADAR, Kind.STATION, Kind.AIRPORT, Kind.SATELLITE, Kind.MODEL

CHAINS: dict[Field, list[Kind]] = {
    Field.RAIN_NOW: [L, R, S, A, M],
    Field.PRECIP_TYPE: [L, A, R, M],
    Field.INTENSITY: [L, R, S, M],
    Field.INTENSITY_CLASS: [L, R, S, A, M],
    Field.THUNDER: [L, A, M],
    Field.WIND_SPEED: [L, S, A, M],
    Field.WIND_GUST: [L, S, A, M],
    Field.WIND_DIRECTION: [L, S, A, M],
    Field.VISIBILITY: [L, S, A, M],
    Field.FOG: [L, S, A, M],
    # The model covers the home itself; an airport's cloud sensor may be far
    # away and sees only the lower sky.
    Field.CLOUD_COVER: [L, M, A, S],
    Field.SNOW_DEPTH: [L, S, A, M],
    Field.TEMPERATURE: [L, S, A, M],
    Field.DEW_POINT: [L, S, A, M],
    Field.HUMIDITY: [L, S, A, M],
    Field.PRESSURE: [L, S, A, M],
    Field.PRECIP_LAST_HOUR: [L, R, S],
    Field.PRECIP_LAST_24H: [L, S, M],
}


class Group(StrEnum):
    """Fields that share distance and height limits."""

    PRECIPITATION = "precipitation"
    WIND = "wind"
    SIGHT = "sight"  # visibility, fog, cloud
    TEMPERATURE = "temperature"  # temperature, dew point, humidity
    PRESSURE = "pressure"
    SNOW = "snow"


GROUP_OF: dict[Field, Group] = {
    Field.RAIN_NOW: Group.PRECIPITATION,
    Field.PRECIP_TYPE: Group.PRECIPITATION,
    Field.INTENSITY: Group.PRECIPITATION,
    Field.INTENSITY_CLASS: Group.PRECIPITATION,
    Field.THUNDER: Group.PRECIPITATION,
    Field.PRECIP_LAST_HOUR: Group.PRECIPITATION,
    Field.PRECIP_LAST_24H: Group.PRECIPITATION,
    Field.LAST_PRECIP: Group.PRECIPITATION,
    Field.WIND_SPEED: Group.WIND,
    Field.WIND_GUST: Group.WIND,
    Field.WIND_DIRECTION: Group.WIND,
    Field.VISIBILITY: Group.SIGHT,
    Field.FOG: Group.SIGHT,
    Field.CLOUD_COVER: Group.SIGHT,
    Field.TEMPERATURE: Group.TEMPERATURE,
    Field.DEW_POINT: Group.TEMPERATURE,
    Field.HUMIDITY: Group.TEMPERATURE,
    Field.PRESSURE: Group.PRESSURE,
    Field.SNOW_DEPTH: Group.SNOW,
    Field.SNOW_ON_GROUND: Group.SNOW,
}


@dataclass(frozen=True)
class GroupLimits:
    station_km: float
    airport_km: float
    height_m: float | None  # None: height is not checked


DEFAULT_LIMITS: dict[Group, GroupLimits] = {
    Group.PRECIPITATION: GroupLimits(15, 30, None),
    Group.WIND: GroupLimits(40, 50, 400),
    Group.SIGHT: GroupLimits(30, 40, None),
    # Temperature changes with height, and inversions make a fixed lapse
    # rate unreliable, so no correction: only stations close in height count.
    Group.TEMPERATURE: GroupLimits(25, 40, 150),
    # Sea-level pressure is comparable over long distances and heights.
    Group.PRESSURE: GroupLimits(100, 100, None),
    Group.SNOW: GroupLimits(30, 30, 300),
}


@dataclass(frozen=True)
class Limit:
    max_age: timedelta
    max_distance_km: float | None = None
    max_height_diff_m: float | None = None


def limit_for(field: Field, kind: Kind, limits: dict[Group, GroupLimits] | None = None) -> Limit:
    if kind is Kind.LOCAL:
        return Limit(timedelta(minutes=30))
    if kind is Kind.RADAR:
        return Limit(timedelta(minutes=25))
    if kind is Kind.SATELLITE:
        # A daily map is valid for 00 UTC and appears about 14 h later, so
        # the newest one can be up to about 38 h old.
        return Limit(timedelta(hours=48))
    if kind is Kind.MODEL:
        return Limit(timedelta(hours=3))
    group = GROUP_OF.get(field, Group.SIGHT)
    lim = (limits or DEFAULT_LIMITS).get(group, DEFAULT_LIMITS[group])
    if group is Group.SNOW:
        age = timedelta(hours=24)
    elif field in (Field.PRECIP_LAST_HOUR, Field.PRECIP_LAST_24H):
        age = timedelta(minutes=90)  # hourly sums arrive after the hour
    elif kind is Kind.STATION:
        age = timedelta(minutes=45)
    else:
        age = timedelta(minutes=90)  # airports report every 30-60 minutes
    distance = lim.station_km if kind is Kind.STATION else lim.airport_km
    return Limit(age, distance, lim.height_m)


@dataclass(frozen=True)
class Settings:
    """What the user can change about the choice."""

    limits: dict[Group, GroupLimits] = dc_field(default_factory=lambda: dict(DEFAULT_LIMITS))
    excluded_stations: frozenset[str] = frozenset()


def usable(reading: Reading, location: Location, now: datetime, settings: Settings | None = None) -> bool:
    settings = settings or Settings()
    if reading.station_id and reading.station_id in settings.excluded_stations:
        return False
    lim = limit_for(reading.field, reading.kind, settings.limits)
    if now - reading.observed > lim.max_age or not not_future(reading.observed, now):
        return False
    if lim.max_distance_km is not None and (reading.distance_km or 0) > lim.max_distance_km:
        return False
    if (
        lim.max_height_diff_m is not None
        and reading.elevation is not None
        and location.elevation is not None
        and abs(reading.elevation - location.elevation) > lim.max_height_diff_m
    ):
        return False
    return True


@dataclass(frozen=True)
class Choice:
    reading: Reading
    candidates: int  # usable readings of the winning kind
    details: dict[str, Any] | None = None  # extra attributes, e.g. what a derived value is based on


def pick(
    field: Field,
    chain: list[Kind],
    readings: list[Reading],
    location: Location,
    now: datetime,
    accept=lambda r: True,
    settings: Settings | None = None,
) -> Choice | None:
    for kind in chain:
        found = [
            r for r in readings
            if r.field is field and r.kind is kind and accept(r) and usable(r, location, now, settings)
        ]
        if found:
            found.sort(key=lambda r: (r.distance_km or 0.0, r.priority, -r.observed.timestamp()))
            return Choice(found[0], len(found))
    return None


SNOW_MODEL_MIN_CM = 2.0  # model depth that counts as "snowy" (design choice)
SNOW_OBSERVED_MIN_CM = 0.5


def snow_on_ground(
    readings: list[Reading], location: Location, now: datetime, settings: Settings | None = None
) -> Choice | None:
    """Snowy landscape. Observed depth first, then the satellite map, then
    the model. Never derived from snowfall alone."""
    observed = pick(Field.SNOW_DEPTH, [L, S, A], readings, location, now, settings=settings)
    if observed:
        r = observed.reading
        return Choice(_as(r, Field.SNOW_ON_GROUND, float(r.value) >= SNOW_OBSERVED_MIN_CM), observed.candidates)
    satellite = pick(Field.SNOW_ON_GROUND, [SAT], readings, location, now, settings=settings)
    if satellite:
        return satellite
    model = pick(Field.SNOW_DEPTH, [M], readings, location, now, settings=settings)
    if model:
        r = model.reading
        return Choice(_as(r, Field.SNOW_ON_GROUND, float(r.value) >= SNOW_MODEL_MIN_CM), model.candidates)
    return None


def _as(r: Reading, field: Field, value: bool | str | float) -> Reading:
    return replace(r, field=field, value=value)


def _derived(r: Reading, field: Field, value: bool | str) -> Choice:
    return Choice(_as(r, field, value), 1)


def _make_consistent(
    result: dict[Field, Choice], readings: list[Reading], location: Location, now: datetime, settings: Settings
) -> None:
    """"Rain now" decides; type and class must not contradict it. Radar can
    say "wet" while the airport 15 km away says "no weather": then the type
    comes from the next source that names one."""
    rain = result.get(Field.RAIN_NOW)
    if rain is None:
        return
    if not rain.reading.value:
        result[Field.PRECIP_TYPE] = _derived(rain.reading, Field.PRECIP_TYPE, PrecipType.NONE.value)
        result[Field.INTENSITY_CLASS] = _derived(rain.reading, Field.INTENSITY_CLASS, IntensityClass.DRY.value)
        return
    for field, empty in ((Field.PRECIP_TYPE, PrecipType.NONE.value), (Field.INTENSITY_CLASS, IntensityClass.DRY.value)):
        current = result.get(field)
        if current is None or current.reading.value == empty:
            better = pick(
                field, CHAINS[field], readings, location, now,
                accept=lambda r, e=empty: r.value != e, settings=settings,
            )
            if better:
                result[field] = better
            else:
                result.pop(field, None)


def _derived_reading(field: Field, value: float | str, inputs: list[Choice]) -> Reading:
    """A value Bellweather computes from other values. Its age is the age of its oldest input."""
    return Reading(
        field=field, value=value, kind=Kind.DERIVED, source="derived",
        observed=min(c.reading.observed for c in inputs),
    )


def _based_on(choices: dict[str, Choice]) -> dict[str, str]:
    return {name: f"{c.reading.kind.value}:{c.reading.source}" for name, c in choices.items()}


def _add_derived(result: dict[Field, Choice], location: Location, now: datetime) -> None:
    temp, hum, wind = result.get(Field.TEMPERATURE), result.get(Field.HUMIDITY), result.get(Field.WIND_SPEED)
    if temp and hum:
        inputs = {"temperature": temp, "humidity": hum}
        speed = 0.0
        if wind:
            inputs["wind_speed"] = wind
            speed = float(wind.reading.value)
        value = round(apparent_temperature(float(temp.reading.value), float(hum.reading.value), speed), 1)
        result[Field.APPARENT_TEMPERATURE] = Choice(
            _derived_reading(Field.APPARENT_TEMPERATURE, value, list(inputs.values())), 1, {"based_on": _based_on(inputs)}
        )

    direction = result.get(Field.WIND_DIRECTION)
    if direction:
        result[Field.WIND_DIRECTION] = replace(
            direction, details={**(direction.details or {}), "cardinal": cardinal(float(direction.reading.value))}
        )

    parts = {
        name: result[f] for name, f in (
            ("precipitation", Field.RAIN_NOW), ("precip_type", Field.PRECIP_TYPE),
            ("intensity_class", Field.INTENSITY_CLASS), ("thunder", Field.THUNDER), ("fog", Field.FOG),
            ("wind_speed", Field.WIND_SPEED), ("cloud_cover", Field.CLOUD_COVER),
        ) if f in result
    }

    def val(name: str):
        return parts[name].reading.value if name in parts else None

    elevation = solar_elevation(location.latitude, location.longitude, now)
    cond = condition(
        precipitating=val("precipitation"), precip_type=val("precip_type"), intensity_class=val("intensity_class"),
        thunder=val("thunder"), fog=val("fog"),
        wind_kmh=None if val("wind_speed") is None else float(val("wind_speed")),
        cloud_pct=None if val("cloud_cover") is None else float(val("cloud_cover")),
        sun_elevation=elevation,
    )
    if cond is not None and parts:
        result[Field.CONDITION] = Choice(
            _derived_reading(Field.CONDITION, cond.value, list(parts.values())), 1,
            {"based_on": _based_on(parts), "sun_elevation": round(elevation, 1)},
        )


LAST_PRECIP_CHAIN = [L, R, S]


def last_precipitation(
    result: dict[Field, Choice],
    readings: list[Reading],
    location: Location,
    now: datetime,
    previous: Choice | None,
    settings: Settings,
) -> Choice | None:
    """When precipitation was last seen: now if it is wet now, otherwise
    the newest wet moment the radar history or an hourly station sum shows,
    or what was remembered before. The newest of these wins."""
    options: list[Choice] = []
    rain = result.get(Field.RAIN_NOW)
    if rain and rain.reading.value:
        options.append(Choice(_as(rain.reading, Field.LAST_PRECIP, rain.reading.observed.isoformat()), rain.candidates))
    for kind in LAST_PRECIP_CHAIN:
        found = [
            r for r in readings
            if r.field is Field.LAST_PRECIP and r.kind is kind and _close_enough(r, settings)
            and (r.station_id is None or r.station_id not in settings.excluded_stations)
        ]
        if found:
            found.sort(key=lambda r: _when(r.value), reverse=True)
            options.append(Choice(found[0], len(found)))
            break
    if previous is not None:
        options.append(previous)
    if not options:
        return None
    return max(options, key=lambda c: _when(c.reading.value))


def _when(value) -> datetime:
    return datetime.fromisoformat(str(value))


def _close_enough(r: Reading, settings: Settings) -> bool:
    lim = limit_for(Field.LAST_PRECIP, r.kind, settings.limits)
    return lim.max_distance_km is None or (r.distance_km or 0) <= lim.max_distance_km


def fuse(
    readings: list[Reading],
    location: Location,
    now: datetime,
    settings: Settings | None = None,
    previous_last_precip: Choice | None = None,
) -> dict[Field, Choice]:
    settings = settings or Settings()
    result: dict[Field, Choice] = {}
    for field, chain in CHAINS.items():
        choice = pick(field, chain, readings, location, now, settings=settings)
        if choice:
            result[field] = choice
    _make_consistent(result, readings, location, now, settings)
    snow = snow_on_ground(readings, location, now, settings)
    if snow:
        result[Field.SNOW_ON_GROUND] = snow
    _add_derived(result, location, now)
    last = last_precipitation(result, readings, location, now, previous_last_precip, settings)
    if last:
        result[Field.LAST_PRECIP] = last
    return result
