"""Data types shared by the sources and the fusion step.

Nothing in `core` imports Home Assistant, so it can be tested on its own and
later moved into a separate library.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class Field(StrEnum):
    """One value the integration reports. Units are fixed inside core."""

    RAIN_NOW = "rain_now"  # bool: any precipitation falling now
    PRECIP_TYPE = "precip_type"  # PrecipType
    INTENSITY = "intensity"  # mm/h
    INTENSITY_CLASS = "intensity_class"  # IntensityClass
    THUNDER = "thunder"  # bool
    WIND_SPEED = "wind_speed"  # km/h, mean
    WIND_GUST = "wind_gust"  # km/h
    WIND_DIRECTION = "wind_direction"  # degrees the wind comes from
    VISIBILITY = "visibility"  # km
    FOG = "fog"  # bool
    CLOUD_COVER = "cloud_cover"  # %
    SNOW_DEPTH = "snow_depth"  # cm on the ground
    SNOW_ON_GROUND = "snow_on_ground"  # bool
    TEMPERATURE = "temperature"  # degrees C, 2 m
    DEW_POINT = "dew_point"  # degrees C
    HUMIDITY = "humidity"  # % relative humidity
    PRESSURE = "pressure"  # hPa, reduced to sea level
    APPARENT_TEMPERATURE = "apparent_temperature"  # degrees C, derived
    CONDITION = "condition"  # Home Assistant condition string, derived
    PRECIP_LAST_HOUR = "precip_last_hour"  # mm in the last 60 minutes
    PRECIP_LAST_24H = "precip_last_24h"  # mm in the last 24 hours
    LAST_PRECIP = "last_precip"  # ISO 8601 UTC time precipitation was last seen


class PrecipType(StrEnum):
    NONE = "none"
    DRIZZLE = "drizzle"
    RAIN = "rain"
    FREEZING_RAIN = "freezing_rain"
    SLEET = "sleet"
    SNOW = "snow"
    HAIL = "hail"


class IntensityClass(StrEnum):
    DRY = "dry"
    DRIZZLE = "drizzle"
    LIGHT = "light"
    MODERATE = "moderate"
    HEAVY = "heavy"
    EXTREME = "extreme"


class Kind(StrEnum):
    """Where a reading comes from. Fusion chains are written in these terms."""

    LOCAL = "local"  # the user's own sensors
    RADAR = "radar"  # radar at the exact point
    STATION = "station"  # official weather station
    AIRPORT = "airport"  # METAR
    SATELLITE = "satellite"
    MODEL = "model"  # forecast model or a weather entity
    DERIVED = "derived"  # computed by Bellweather from other chosen values


@dataclass(frozen=True)
class Location:
    latitude: float
    longitude: float
    elevation: float | None = None  # metres


@dataclass(frozen=True)
class Reading:
    """One value from one source, with what is needed to judge it."""

    field: Field
    value: float | bool | str
    kind: Kind
    source: str  # source key, e.g. "metar"
    observed: datetime  # UTC, when it was measured or valid
    station: str | None = None  # display name
    distance_km: float | None = None  # from the exact location, computed locally
    elevation: float | None = None  # station elevation, metres
    attribution: str | None = None
    station_id: str | None = None  # stable id, used to exclude a station
    priority: int = 0  # tie-break inside a kind: lower wins (the user's own weather entity before a default model)
    latitude: float | None = None  # station position, for the forecast's verification
    longitude: float | None = None


# Values outside these ranges are missing-value markers (e.g. -32767, -999)
# or sensor faults, never weather.
PLAUSIBLE: dict[Field, tuple[float, float]] = {
    Field.INTENSITY: (0.0, 500.0),
    Field.WIND_SPEED: (0.0, 400.0),
    Field.WIND_GUST: (0.0, 500.0),
    Field.WIND_DIRECTION: (0.0, 360.0),
    Field.VISIBILITY: (0.0, 200.0),
    Field.CLOUD_COVER: (0.0, 100.0),
    Field.SNOW_DEPTH: (0.0, 2000.0),
    Field.TEMPERATURE: (-90.0, 60.0),
    Field.DEW_POINT: (-90.0, 40.0),
    Field.HUMIDITY: (0.0, 100.0),
    Field.PRESSURE: (850.0, 1090.0),
    Field.PRECIP_LAST_HOUR: (0.0, 500.0),
    Field.PRECIP_LAST_24H: (0.0, 2000.0),
}


def plausible(field: Field, value: float) -> bool:
    low, high = PLAUSIBLE.get(field, (float("-inf"), float("inf")))
    return low <= value <= high


def intensity_class(mm_per_hour: float, precip_type: PrecipType | None = None) -> IntensityClass:
    """Class of a precipitation rate.

    Thresholds: 0.1 mm/h for "wet" and 0.5 mm/h for drizzle are design
    choices; 2.5 / 7.6 mm/h follow the US Federal Meteorological Handbook
    No. 1 (light / moderate / heavy rain); 50 mm/h "violent" is from
    Canada's MANOBS as quoted by a secondary source.
    """
    if mm_per_hour < 0.1:
        return IntensityClass.DRY
    if mm_per_hour < 0.5 and precip_type not in (PrecipType.SNOW, PrecipType.HAIL):
        return IntensityClass.DRIZZLE
    if mm_per_hour <= 2.5:
        return IntensityClass.LIGHT
    if mm_per_hour <= 7.6:
        return IntensityClass.MODERATE
    if mm_per_hour <= 50:
        return IntensityClass.HEAVY
    return IntensityClass.EXTREME
