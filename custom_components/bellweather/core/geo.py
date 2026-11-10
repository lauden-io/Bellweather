"""Distances, boxes, grid snapping and map tiles."""

from __future__ import annotations

import math
from dataclasses import dataclass

EARTH_RADIUS_KM = 6371.0088
KM_PER_DEGREE_LAT = 111.32


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance (haversine)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


@dataclass(frozen=True)
class Box:
    south: float
    west: float
    north: float
    east: float

    def contains(self, lat: float, lon: float) -> bool:
        return self.south <= lat <= self.north and self.west <= lon <= self.east


def box_around(lat: float, lon: float, radius_km: float) -> Box:
    """A lat/lon box that contains every point within radius_km."""
    dlat = radius_km / KM_PER_DEGREE_LAT
    coslat = max(math.cos(math.radians(min(abs(lat) + dlat, 89.9))), 0.01)
    dlon = radius_km / (KM_PER_DEGREE_LAT * coslat)
    return Box(
        south=max(lat - dlat, -90.0),
        west=max(lon - dlon, -180.0),
        north=min(lat + dlat, 90.0),
        east=min(lon + dlon, 180.0),
    )


def snap_to_grid(lat: float, lon: float, step_km: float) -> tuple[float, float]:
    """Centre of the grid cell (about step_km wide) that holds the point.

    The same input always gives the same output, so the shared point does
    not wander between requests.
    """
    if step_km <= 0:
        return lat, lon
    dlat = step_km / KM_PER_DEGREE_LAT
    cell_lat = (math.floor(lat / dlat) + 0.5) * dlat
    coslat = max(math.cos(math.radians(cell_lat)), 0.01)
    dlon = step_km / (KM_PER_DEGREE_LAT * coslat)
    cell_lon = (math.floor(lon / dlon) + 0.5) * dlon
    return round(cell_lat, 5), round(cell_lon, 5)


def tile_for(lat: float, lon: float, zoom: int, size: int = 256) -> tuple[int, int, int, int]:
    """Web Mercator tile (x, y) that holds the point, and the pixel inside it."""
    n = 2**zoom
    lat = max(min(lat, 85.0511), -85.0511)
    xf = (lon + 180.0) / 360.0 * n
    yf = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n
    x, y = min(int(xf), n - 1), min(int(yf), n - 1)
    px = min(int((xf - x) * size), size - 1)
    py = min(int((yf - y) * size), size - 1)
    return x, y, px, py
