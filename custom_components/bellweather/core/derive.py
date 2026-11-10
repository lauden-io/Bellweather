"""Values computed from other values: humidity, feels-like temperature,
the sun's height and compass points. Pure functions, no I/O."""

from __future__ import annotations

import math
from datetime import datetime, timezone

# Magnus formula constants from Alduchov and Eskridge (1996), valid for
# about -40 to +50 degrees C over water.
_MAGNUS_A = 17.625
_MAGNUS_B = 243.04


def humidity_from_dew_point(temperature_c: float, dew_point_c: float) -> float:
    """Relative humidity in % from air temperature and dew point."""
    temperature_c, dew_point_c = max(temperature_c, -90.0), max(dew_point_c, -90.0)
    rh = 100.0 * math.exp(_MAGNUS_A * dew_point_c / (_MAGNUS_B + dew_point_c)) / math.exp(
        _MAGNUS_A * temperature_c / (_MAGNUS_B + temperature_c)
    )
    return max(0.0, min(100.0, rh))


def dew_point_from_humidity(temperature_c: float, humidity_pct: float) -> float:
    """Dew point in degrees C from air temperature and relative humidity."""
    humidity_pct = max(0.1, min(100.0, humidity_pct))
    temperature_c = max(temperature_c, -90.0)
    gamma = math.log(humidity_pct / 100.0) + _MAGNUS_A * temperature_c / (_MAGNUS_B + temperature_c)
    return _MAGNUS_B * gamma / (_MAGNUS_A - gamma)


def apparent_temperature(temperature_c: float, humidity_pct: float, wind_kmh: float) -> float:
    """Feels-like temperature, the Australian Bureau of Meteorology formula
    (Steadman 1994) without solar radiation:

        AT = Ta + 0.33 e - 0.70 ws - 4.00

    e is the water vapour pressure in hPa, ws the wind speed in m/s at 10 m.
    """
    temperature_c = max(min(temperature_c, 60.0), -90.0)  # physical range; keeps the formula finite
    e = humidity_pct / 100.0 * 6.105 * math.exp(17.27 * temperature_c / (237.7 + temperature_c))
    return temperature_c + 0.33 * e - 0.70 * (wind_kmh / 3.6) - 4.00


def solar_elevation(latitude: float, longitude: float, when: datetime) -> float:
    """Height of the sun's centre above the horizon in degrees, without
    refraction. NOAA's low-accuracy formulas (about 1 degree), enough to
    tell day from night."""
    when = when.astimezone(timezone.utc)
    day_of_year = when.timetuple().tm_yday
    hour = when.hour + when.minute / 60 + when.second / 3600
    gamma = 2 * math.pi / 365 * (day_of_year - 1 + (hour - 12) / 24)
    eqtime = 229.18 * (
        0.000075 + 0.001868 * math.cos(gamma) - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma) - 0.040849 * math.sin(2 * gamma)
    )
    decl = (
        0.006918 - 0.399912 * math.cos(gamma) + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma) + 0.000907 * math.sin(2 * gamma)
        - 0.002697 * math.cos(3 * gamma) + 0.00148 * math.sin(3 * gamma)
    )
    true_solar_minutes = hour * 60 + eqtime + 4 * longitude
    hour_angle = math.radians(true_solar_minutes / 4 - 180)
    lat = math.radians(latitude)
    cos_zenith = math.sin(lat) * math.sin(decl) + math.cos(lat) * math.cos(decl) * math.cos(hour_angle)
    return 90.0 - math.degrees(math.acos(max(-1.0, min(1.0, cos_zenith))))


# Sunrise and sunset are defined at -0.833 degrees (US Naval Observatory:
# refraction plus the sun's radius).
DAYLIGHT_ELEVATION = -0.833

_POINTS = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")


def cardinal(degrees: float) -> str:
    """16-point compass name of a direction (where the wind comes from)."""
    return _POINTS[int((degrees % 360) / 22.5 + 0.5) % 16]
