"""Model values from Open-Meteo, ECMWF IFS: the last link of every chain.

Model output, not an observation. It answers when nothing measured is
close and fresh enough, and it is the only global source of snow depth
and the last 24 hours' precipitation where no station reports them.

ECMWF IFS is asked for by name. For snow depth `best_match` returned 0
where IFS had 14 cm in a spot check, and models differ a lot (IFS 0.14 m
against GFS 0.41 m at the same point). Free for non-commercial use.

The model grid is about 25 km, so the shared (rounded) point loses almost
nothing. A weather entity the user picks in the options wins over these
values: it is the user's own choice of model.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..model import Field, Kind, Location, Reading, plausible
from ..privacy import Precision, shared_point
from ..safety import CAP_SMALL_JSON, finite, not_future
from .base import Http, Source

URL = "https://api.open-meteo.com/v1/forecast"
ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"
MODEL = "ecmwf_ifs025"
ATTRIBUTION = "Weather data by Open-Meteo.com (ECMWF IFS)"

# Open-Meteo `current` variable -> (field, factor into core units)
CURRENT: dict[str, tuple[Field, float]] = {
    "temperature_2m": (Field.TEMPERATURE, 1.0),
    "relative_humidity_2m": (Field.HUMIDITY, 1.0),
    "dew_point_2m": (Field.DEW_POINT, 1.0),
    "pressure_msl": (Field.PRESSURE, 1.0),
    "cloud_cover": (Field.CLOUD_COVER, 1.0),
    "wind_speed_10m": (Field.WIND_SPEED, 1.0),  # km/h by default
    "wind_gusts_10m": (Field.WIND_GUST, 1.0),
    "wind_direction_10m": (Field.WIND_DIRECTION, 1.0),
    "snow_depth": (Field.SNOW_DEPTH, 100.0),  # m -> cm
}


def _time(text: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(text)[:25]).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def parse(data: dict, now: datetime) -> list[Reading]:
    common = dict(kind=Kind.MODEL, source="open_meteo", attribution=ATTRIBUTION, priority=1)
    out: list[Reading] = []
    if not isinstance(data, dict):
        return out
    current = data.get("current") if isinstance(data.get("current"), dict) else {}
    observed = _time(current.get("time"))
    if observed is not None and not_future(observed, now):
        for var, (field, factor) in CURRENT.items():
            value = finite(current.get(var))
            if value is None:
                continue
            value = round(value * factor, 1)
            if plausible(field, value):
                out.append(Reading(field=field, value=value, observed=observed, **common))
    hourly = data.get("hourly") if isinstance(data.get("hourly"), dict) else {}
    times, amounts = hourly.get("time") or [], hourly.get("precipitation") or []
    # An hourly value at T is the sum of the hour before T.
    done = []
    for t, a in zip(times, amounts, strict=False):
        when, amount = _time(t), finite(a)
        if when is not None and amount is not None and 0 <= amount <= 500 and when <= now:
            done.append((when, amount))
    done = done[-24:]
    if len(done) == 24:
        out.append(Reading(
            field=Field.PRECIP_LAST_24H, value=round(sum(a for _, a in done), 1), observed=done[-1][0], **common
        ))
    return out


def quarter_hour(now: datetime) -> datetime:
    """The quarter hour `now` falls in."""
    return now.replace(minute=now.minute - now.minute % 15, second=0, microsecond=0)


class OpenMeteoSource(Source):
    """Open-Meteo's current values change every 15 minutes, so they are asked
    once per quarter hour, at the first update after it begins; the updates in
    between reuse that answer. Open-Meteo counts every request against a daily
    limit per IP address, shared by everything in the home that uses it."""

    key = "open_meteo"
    kind = Kind.MODEL
    attribution = ATTRIBUTION
    sends = "the shared point"

    def __init__(self) -> None:
        self._asked: tuple[datetime, tuple[float, float]] | None = None
        self._readings: list[Reading] = []

    async def fetch(self, http: Http, location: Location, precision: Precision, now: datetime) -> list[Reading]:
        lat, lon = shared_point(location, precision)
        asked = (quarter_hour(now), (lat, lon))
        if self._asked == asked:
            return list(self._readings)
        data = await http.get_json(URL, {
            "latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}",
            "current": ",".join(CURRENT),
            "hourly": "precipitation", "past_hours": "24", "forecast_hours": "1",
            "models": MODEL, "timezone": "GMT",
        }, limit=CAP_SMALL_JSON)
        readings = parse(data or {}, now)
        self._asked, self._readings = asked, readings
        return list(readings)


async def elevations(http: Http, points: list[tuple[float, float]]) -> list[float | None]:
    """Ground height (Copernicus DEM, 90 m) of up to 100 public points,
    used for station heights. It never receives the home location."""
    if not points:
        return []
    data = await http.get_json(ELEVATION_URL, {
        "latitude": ",".join(f"{p[0]:.4f}" for p in points),
        "longitude": ",".join(f"{p[1]:.4f}" for p in points),
    }, limit=CAP_SMALL_JSON)
    values = data.get("elevation") if isinstance(data, dict) else None
    values = values if isinstance(values, list) else []
    return ([finite(v) for v in values] + [None] * len(points))[: len(points)]
