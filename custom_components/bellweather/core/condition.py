"""The weather now as one of Home Assistant's standard condition strings,
derived from the observed values.

Order of precedence, most significant first: thunder, hail, precipitation,
fog, strong wind, then cloud and the sun. Thresholds:

- fog: visibility below 1 km (WMO International Cloud Atlas);
- windy: mean wind of at least 41 km/h, Beaufort 6 "strong breeze"
  (22 kt) and above;
- pouring: the "heavy" class, above 7.6 mm/h (US Federal Meteorological
  Handbook No. 1);
- cloud: below 31 % is clear or a few clouds, from 75 % (a METAR "broken"
  layer) it is cloudy, in between partly cloudy. The cut points between
  okta classes are my choice;
- night: the sun's centre below -0.833 degrees.
"""

from __future__ import annotations

from enum import StrEnum

from .derive import DAYLIGHT_ELEVATION
from .model import IntensityClass, PrecipType

WINDY_KMH = 41.0
CLEAR_BELOW_PCT = 31.0
CLOUDY_FROM_PCT = 75.0


class Condition(StrEnum):
    """Home Assistant's weather conditions (homeassistant.components.weather)."""

    CLEAR_NIGHT = "clear-night"
    CLOUDY = "cloudy"
    EXCEPTIONAL = "exceptional"
    FOG = "fog"
    HAIL = "hail"
    LIGHTNING = "lightning"
    LIGHTNING_RAINY = "lightning-rainy"
    PARTLYCLOUDY = "partlycloudy"
    POURING = "pouring"
    RAINY = "rainy"
    SNOWY = "snowy"
    SNOWY_RAINY = "snowy-rainy"
    SUNNY = "sunny"
    WINDY = "windy"
    WINDY_VARIANT = "windy-variant"


def condition(
    *,
    precipitating: bool | None,
    precip_type: str | None,
    intensity_class: str | None,
    thunder: bool | None,
    fog: bool | None,
    wind_kmh: float | None,
    cloud_pct: float | None,
    sun_elevation: float,
) -> Condition | None:
    """None when there is not enough to say anything."""
    wet = bool(precipitating)
    if thunder:
        return Condition.LIGHTNING_RAINY if wet else Condition.LIGHTNING
    if wet:
        if precip_type == PrecipType.HAIL:
            return Condition.HAIL
        if precip_type == PrecipType.SNOW:
            return Condition.SNOWY
        if precip_type in (PrecipType.SLEET, PrecipType.FREEZING_RAIN):
            return Condition.SNOWY_RAINY
        if intensity_class in (IntensityClass.HEAVY, IntensityClass.EXTREME):
            return Condition.POURING
        return Condition.RAINY
    if fog:
        return Condition.FOG
    if wind_kmh is not None and wind_kmh >= WINDY_KMH:
        clear = cloud_pct is not None and cloud_pct < CLEAR_BELOW_PCT
        return Condition.WINDY if clear else Condition.WINDY_VARIANT
    if cloud_pct is None:
        return None  # dry, but sunny or overcast is unknown
    if cloud_pct >= CLOUDY_FROM_PCT:
        return Condition.CLOUDY
    if cloud_pct >= CLEAR_BELOW_PCT:
        return Condition.PARTLYCLOUDY
    return Condition.SUNNY if sun_elevation > DAYLIGHT_ELEVATION else Condition.CLEAR_NIGHT
