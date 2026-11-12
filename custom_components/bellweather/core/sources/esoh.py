"""Official station observations for Europe: EUMETNET E-SOH via MeteoGate.

One OGC API-EDR endpoint for many national weather services, anonymous,
CC BY 4.0, 10-minute values, kept for about one day. Parameter names
follow `<cf standard name>:<level>:<method>:<period>`; the level and method
differ between countries (e.g. `precipitation_amount:2.0:point:PT10M` and
`precipitation_amount:0.0:sum:PT10M`), so only the name and the period are
matched. Units come with each parameter.

Station names come from the `locations` endpoint, once a day. E-SOH has no
station heights; when a height lookup is wired in (Open-Meteo's elevation
API, which receives only the public station coordinates), the height
limits apply to E-SOH stations too. Without it they are skipped.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from ..geo import distance_km
from ..model import Field, Kind, Location, Reading, intensity_class, plausible
from ..privacy import Precision, query_box
from ..safety import CAP_ESOH, CAP_SMALL_JSON, clean_text, finite, not_future, valid_id, valid_position
from .base import Http, Source

BASE = "https://observations.meteogate.eu/collections/observations"
URL = f"{BASE}/area"
LOCATIONS_URL = f"{BASE}/locations"
SEARCH_RADIUS_KM = 40.0  # the largest default station limit (wind); grows with the user's limits, up to MAX_RADIUS_KM
MAX_RADIUS_KM = 100.0
LOOKBACK = timedelta(minutes=70)
NAMES_MAX_AGE = timedelta(days=1)

# field -> list of (standard name, period, factor that turns the value into the field)
WANTED: dict[Field, list[tuple[str, str, float]]] = {
    Field.INTENSITY: [("precipitation_amount", "PT10M", 6.0), ("precipitation_amount", "PT1H", 1.0)],
    Field.WIND_SPEED: [("wind_speed", "PT10M", 1.0), ("wind_speed", "PT0S", 1.0)],
    Field.WIND_GUST: [("wind_speed_of_gust", "PT10M", 1.0), ("wind_speed_of_gust", "PT1H", 1.0)],
    Field.WIND_DIRECTION: [("wind_from_direction", "PT10M", 1.0), ("wind_from_direction", "PT0S", 1.0)],
    Field.VISIBILITY: [("visibility_in_air", "PT0S", 1.0), ("visibility_in_air", "PT10M", 1.0)],
    Field.CLOUD_COVER: [("cloud_area_fraction", "PT0S", 1.0)],
    Field.SNOW_DEPTH: [("surface_snow_thickness", "PT0S", 1.0)],
    Field.TEMPERATURE: [("air_temperature", "PT0S", 1.0), ("air_temperature", "PT10M", 1.0)],
    Field.DEW_POINT: [("dew_point_temperature", "PT0S", 1.0), ("dew_point_temperature", "PT10M", 1.0)],
    Field.HUMIDITY: [("relative_humidity", "PT0S", 1.0), ("relative_humidity", "PT10M", 1.0)],
    Field.PRESSURE: [("air_pressure_at_mean_sea_level", "PT0S", 1.0), ("air_pressure_at_mean_sea_level", "PT10M", 1.0)],
    Field.PRECIP_LAST_HOUR: [("precipitation_amount", "PT1H", 1.0)],
    Field.PRECIP_LAST_24H: [("precipitation_amount", "PT24H", 1.0)],
}

# QUDT unit ids (the `type` of a unit symbol) -> the short names Bellweather uses.
_QUDT = {
    "DEG_C": "degC", "K": "K", "HectoPA": "hPa", "PA": "Pa", "PERCENT": "%", "KiloGM-PER-M2": "mm",
    "MilliM": "mm", "M-PER-SEC": "m/s", "KiloM-PER-HR": "km/h", "M": "m", "KiloM": "km", "CentiM": "cm",
    "DEG": "deg", "OKTA": "okta",
}
_LABELS = {"cel": "degC", "°c": "degC", "k": "K", "hpa": "hPa", "pa": "Pa", "%": "%", "percent": "%",
           "kg/m2": "mm", "kg m-2": "mm", "mm": "mm", "m/s": "m/s", "m s-1": "m/s", "km/h": "km/h",
           "m": "m", "km": "km", "cm": "cm", "deg": "deg", "degree": "deg", "okta": "okta", "oktas": "okta"}


def _unit(meta: dict[str, Any]) -> str:
    """A short unit name. The QUDT id is the most reliable part: some symbol
    texts arrive with broken encoding (e.g. "Â°C")."""
    unit = meta.get("unit") or {}
    symbol = unit.get("symbol")
    if isinstance(symbol, dict):
        qudt = str(symbol.get("type") or "").rsplit("/", 1)[-1]
        if qudt in _QUDT:
            return _QUDT[qudt]
        symbol = symbol.get("value")
    label = unit.get("label")
    if isinstance(label, dict):
        label = label.get("en") or next(iter(label.values()), None)
    for text in (label, symbol):
        if text and str(text).replace("²", "2").lower() in _LABELS:
            return _LABELS[str(text).replace("²", "2").lower()]
    return str(label or symbol or "")


def _convert(field: Field, value: float, unit: str) -> float | None:
    """Into the units core uses (km/h, km, %, cm, mm, degrees C, hPa)."""
    if field in (Field.WIND_SPEED, Field.WIND_GUST):
        return value * 3.6 if unit == "m/s" else (value if unit == "km/h" else None)
    if field is Field.VISIBILITY:
        return value / 1000 if unit == "m" else (value if unit == "km" else None)
    if field is Field.CLOUD_COVER:
        return value * 12.5 if unit == "okta" else (value if unit == "%" else None)
    if field is Field.SNOW_DEPTH:
        return {"m": value * 100, "cm": value, "mm": value / 10}.get(unit)
    if field in (Field.INTENSITY, Field.PRECIP_LAST_HOUR, Field.PRECIP_LAST_24H):
        return value if unit == "mm" else None  # kg/m2 of water is 1 mm
    if field in (Field.TEMPERATURE, Field.DEW_POINT):
        return value - 273.15 if unit == "K" else (value if unit == "degC" else None)
    if field is Field.HUMIDITY:
        return value if unit == "%" else None
    if field is Field.PRESSURE:
        return value / 100 if unit == "Pa" else (value if unit == "hPa" else None)
    if field is Field.WIND_DIRECTION:
        return value
    return None


MAX_COVERAGES = 500
MAX_RANGES = 100
MAX_TIMES = 500


def parse(
    data: dict[str, Any],
    location: Location,
    attribution: str,
    names: dict[str, str] | None = None,
    elevations: dict[str, float] | None = None,
    box=None,
) -> list[Reading]:
    """Readings from an E-SOH area answer. Each coverage is parsed on its
    own: a broken one is skipped, the rest still count."""
    readings: list[Reading] = []
    if not isinstance(data, dict):
        return readings
    top_params = data.get("parameters") if isinstance(data.get("parameters"), dict) else {}
    now = datetime.now(timezone.utc)
    coverages = data.get("coverages") if isinstance(data.get("coverages"), list) else []
    used = 0
    # Only stations inside the box count towards the cap, so far-away
    # entries cannot push the nearest station out; the scan itself is bounded too.
    for cov in coverages[: MAX_COVERAGES * 20]:
        try:
            found = _parse_coverage(cov, top_params, location, attribution, names, elevations, box, now)
        except (TypeError, ValueError, AttributeError, KeyError, OverflowError):
            continue
        if found:
            readings.extend(found)
            used += 1
            if used >= MAX_COVERAGES:
                break
    return readings


def _parse_coverage(cov, top_params, location, attribution, names, elevations, box, now) -> list[Reading]:
    readings: list[Reading] = []
    if True:
        if not isinstance(cov, dict):
            return readings
        axes = cov.get("domain", {}).get("axes", {})
        xs, ys, ts = (axes.get(k, {}).get("values") or [] for k in ("x", "y", "t"))
        if not xs or not ys or not ts:
            return readings
        lat, lon = finite(ys[0]), finite(xs[0])
        if not valid_position(lat, lon) or (box is not None and not box.contains(lat, lon)):
            return readings
        own_params = cov.get("parameters") if isinstance(cov.get("parameters"), dict) else {}

        def meta(name: str) -> dict:
            found = own_params.get(name) or top_params.get(name) or {}
            return found if isinstance(found, dict) else {}

        ranges = cov.get("ranges") if isinstance(cov.get("ranges"), dict) else {}
        ts = ts[:MAX_TIMES]
        by_name: dict[tuple[str, str], tuple[str, list]] = {}
        for name, rng in list(ranges.items())[:MAX_RANGES]:
            if not isinstance(name, str) or not isinstance(rng, dict):
                continue
            parts = name.split(":")
            by_name.setdefault((parts[0], parts[-1]), (name, rng.get("values") or []))
        station_id = valid_id(cov.get("metocean:wigosId"))
        name = (names or {}).get(station_id or "")
        dist = round(distance_km(location.latitude, location.longitude, lat, lon), 1)

        def latest(values: list) -> tuple[float, str] | None:
            for i in range(min(len(values), len(ts)) - 1, -1, -1):
                number = finite(values[i])
                if number is not None:
                    return number, ts[i]
            return None

        for field, options in WANTED.items():
            for std, period, factor in options:
                hit = by_name.get((std, period))
                if not hit:
                    continue
                found = latest(hit[1])
                if not found:
                    continue
                value = _convert(field, found[0], _unit(meta(hit[0])))
                if value is None or not plausible(field, value * factor):
                    continue
                value = round(value * factor, 2)
                try:
                    observed = datetime.fromisoformat(str(found[1])[:32].replace("Z", "+00:00"))
                except ValueError:
                    break
                if observed.tzinfo is None or not not_future(observed, now):
                    break
                common = dict(
                    kind=Kind.STATION, source="esoh", observed=observed,
                    station=f"{name} ({station_id})" if name else station_id, station_id=station_id,
                    distance_km=dist, elevation=(elevations or {}).get(station_id or ""), attribution=attribution,
                    latitude=lat, longitude=lon,
                )
                readings.append(Reading(field=field, value=value, **common))
                if field is Field.INTENSITY:
                    readings.append(Reading(field=Field.RAIN_NOW, value=value > 0, **common))
                    readings.append(Reading(field=Field.INTENSITY_CLASS, value=intensity_class(value).value, **common))
                if field is Field.VISIBILITY:
                    readings.append(Reading(field=Field.FOG, value=value < 1.0, **common))
                if field is Field.PRECIP_LAST_HOUR and value > 0:
                    # Wet some time in the hour that ended at `observed`.
                    readings.append(Reading(field=Field.LAST_PRECIP, value=observed.isoformat(), **common))
                break
    return readings


MAX_LOCATIONS = 200


def parse_locations(data: dict[str, Any], box=None) -> dict[str, tuple[str, float, float]]:
    """id -> (name, latitude, longitude), only stations inside the box, at
    most MAX_LOCATIONS."""
    out: dict[str, tuple[str, float, float]] = {}
    features = data.get("features") if isinstance(data, dict) and isinstance(data.get("features"), list) else []
    for feat in features[: MAX_LOCATIONS * 10]:
        if len(out) >= MAX_LOCATIONS:
            break
        if not isinstance(feat, dict):
            continue
        sid = valid_id(feat.get("id"))
        coords = (feat.get("geometry") or {}).get("coordinates") or []
        name = clean_text((feat.get("properties") or {}).get("name"))
        lat, lon = (finite(coords[1]), finite(coords[0])) if isinstance(coords, list) and len(coords) >= 2 else (None, None)
        if sid and valid_position(lat, lon) and (box is None or box.contains(lat, lon)):
            name = name.replace("_", " ")
            out[str(sid)] = (name.title() if name.isupper() else name, lat, lon)
    return out


ElevationLookup = Callable[[list[tuple[float, float]]], Awaitable[list[float | None]]]


class StationHeights:
    """Ground heights of stations, shared by every location and kept across
    restarts. A station's height does not change, and Open-Meteo counts every
    point of a height request as one call, so each station is asked once. The
    key holds the station's coordinates too: a station that moves is asked
    again. Stations without a height are remembered until the next restart
    only, so a gap in the elevation data is tried again one day."""

    MAX = 5000  # far more than any box holds; a damaged store cannot grow without end

    def __init__(self) -> None:
        self._heights: dict[str, float] = {}
        self.missing: set[str] = set()
        self.changed = False

    @staticmethod
    def key(sid: str, point: tuple[float, float]) -> str:
        return f"{sid}@{point[0]:.4f},{point[1]:.4f}"

    def get(self, key: str) -> float | None:
        return self._heights.get(key)

    def put(self, key: str, height: float) -> None:
        if len(self._heights) < self.MAX and self._heights.get(key) != height:
            self._heights[key] = height
            self.changed = True

    def __contains__(self, key: str) -> bool:
        return key in self._heights or key in self.missing

    def dump(self) -> dict[str, Any]:
        return {"heights": dict(self._heights)}

    def load(self, data: Any) -> None:
        """Only well-formed entries: a text key of sane length and a height
        from -500 to 9000 m."""
        heights = data.get("heights") if isinstance(data, dict) else None
        for key, value in (heights.items() if isinstance(heights, dict) else []):
            h = finite(value)
            if isinstance(key, str) and 0 < len(key) <= 100 and h is not None and -500 <= h <= 9000:
                if len(self._heights) >= self.MAX:
                    break
                self._heights[key] = h


class EsohSource(Source):
    key = "esoh"
    kind = Kind.STATION
    attribution = "EUMETNET E-SOH (MeteoGate), CC BY 4.0"
    sends = "a box around the shared point (search radius 40 km by default, plus the rounding)"

    def __init__(
        self,
        elevation_lookup: ElevationLookup | None = None,
        radius_km: float = SEARCH_RADIUS_KM,
        heights: StationHeights | None = None,
    ) -> None:
        self.radius_km = min(max(radius_km, 1.0), MAX_RADIUS_KM)
        self._names: dict[str, str] = {}
        self._coords: dict[str, tuple[float, float]] = {}
        self._names_at: datetime | None = None
        self._heights = heights if heights is not None else StationHeights()
        self._elevation_failed: datetime | None = None
        self._elevation_lookup = elevation_lookup

    async def _refresh_names(self, http: Http, location: Location, precision: Precision, now: datetime) -> None:
        if self._names_at and now - self._names_at < NAMES_MAX_AGE:
            return
        b = query_box(location, precision, self.radius_km)
        data = await http.get_json(LOCATIONS_URL, {"bbox": f"{b.west:.3f},{b.south:.3f},{b.east:.3f},{b.north:.3f}"}, limit=CAP_ESOH)
        found = parse_locations(data or {}, b)
        self._names = {sid: v[0] for sid, v in found.items()}
        self._coords = {sid: (v[1], v[2]) for sid, v in found.items()}
        self._names_at = now

    async def _refresh_elevations(self, now: datetime) -> None:
        """At most one batch of 100 stations per update; a failed batch is not
        retried for a day, so a broken answer cannot multiply requests."""
        if self._elevation_lookup is None or (self._elevation_failed and now - self._elevation_failed < timedelta(days=1)):
            return
        missing = [sid for sid, point in self._coords.items() if StationHeights.key(sid, point) not in self._heights]
        if missing:
            batch = missing[:100]  # the elevation API takes up to 100 points
            try:
                heights = await self._elevation_lookup([self._coords[sid] for sid in batch])
            except Exception:
                self._elevation_failed = now
                raise
            for sid, h in zip(batch, heights, strict=False):
                key, h = StationHeights.key(sid, self._coords[sid]), finite(h)
                if h is not None and -500 <= h <= 9000:
                    self._heights.put(key, h)
                else:
                    self._heights.missing.add(key)

    def _elevations(self) -> dict[str, float]:
        out = {}
        for sid, point in self._coords.items():
            h = self._heights.get(StationHeights.key(sid, point))
            if h is not None:
                out[sid] = h
        return out

    async def fetch(self, http: Http, location: Location, precision: Precision, now: datetime) -> list[Reading]:
        try:
            await self._refresh_names(http, location, precision, now)
            await self._refresh_elevations(now)
        except Exception:  # names and heights are extras; the values still count
            pass
        b = query_box(location, precision, self.radius_km)
        polygon = (
            f"POLYGON(({b.west:.3f} {b.south:.3f},{b.east:.3f} {b.south:.3f},"
            f"{b.east:.3f} {b.north:.3f},{b.west:.3f} {b.north:.3f},{b.west:.3f} {b.south:.3f}))"
        )
        since = (now - LOOKBACK).strftime("%Y-%m-%dT%H:%M:%SZ")
        data = await http.get_json(URL, {"coords": polygon, "datetime": f"{since}/.."}, limit=CAP_ESOH)
        # A large box can hold many stations: parse off the event loop.
        return await asyncio.to_thread(
            parse, data or {}, location, self.attribution, dict(self._names), self._elevations(), b
        )

    def stations(self) -> dict[str, str]:
        """Known nearby stations, id -> name, for the options."""
        return dict(self._names)
