"""What the home location turns into before it leaves Home Assistant.

The user picks a precision. The exact location never leaves Home Assistant
unless the precision is "exact":

- point services (a forecast model) receive the shared point: the centre of
  a grid cell of the chosen size;
- area services (station and airport lists) receive a box centred on the
  shared point, made larger by the cell size so it still covers the search
  radius around the real location; the nearest station is then picked inside Home Assistant,
  from the exact location;
- radar receives only the zoom-7 map tile that holds the point (a few hundred
  km wide), whatever the precision; the pixel is read inside Home Assistant;
- the satellite snow map is downloaded whole, so it receives nothing.
"""

from __future__ import annotations

import math
from enum import StrEnum

from .geo import Box, box_around, snap_to_grid
from .model import Location


class Precision(StrEnum):
    EXACT = "exact"
    KM_1 = "1"
    KM_5 = "5"
    KM_10 = "10"
    KM_25 = "25"


DEFAULT_PRECISION = Precision.KM_10  # 10 km by default, adjustable both ways


def step_km(precision: Precision) -> float:
    return 0.0 if precision is Precision.EXACT else float(precision.value)


def shared_point(location: Location, precision: Precision) -> tuple[float, float]:
    """The point a point service receives."""
    return snap_to_grid(location.latitude, location.longitude, step_km(precision))


def query_box(location: Location, precision: Precision, radius_km: float) -> Box:
    """The box an area service receives.

    The real location lies at most half a cell diagonal from the shared
    point, so growing the radius by that much keeps the whole search circle
    around the real location inside the box.
    """
    lat, lon = shared_point(location, precision)
    return box_around(lat, lon, radius_km + step_km(precision) * math.sqrt(2) / 2)
