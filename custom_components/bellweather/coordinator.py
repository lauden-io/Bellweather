"""Fetches all enabled sources, adds local sensors and the weather entity,
and fuses them into one value per field."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from aiohttp import ClientSession, ClientTimeout

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfLength,
    UnitOfPressure,
    UnitOfSpeed,
    UnitOfTemperature,
    UnitOfVolumetricFlux,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.util.json import json_loads
from homeassistant.util.unit_conversion import (
    DistanceConverter,
    PressureConverter,
    SpeedConverter,
    TemperatureConverter,
)

from .const import (
    CONF_ELEVATION,
    CONF_EXCLUDED_STATIONS,
    CONF_LIMITS,
    CONF_LOCAL_HUMIDITY,
    CONF_LOCAL_PRESSURE,
    CONF_LOCAL_RAIN_RATE,
    CONF_LOCAL_RAIN_STATE,
    CONF_LOCAL_SNOW_DEPTH,
    CONF_LOCAL_TEMPERATURE,
    CONF_LOCAL_VISIBILITY,
    CONF_LOCAL_WIND_DIRECTION,
    CONF_LOCAL_WIND_GUST,
    CONF_LOCAL_WIND_SPEED,
    CONF_LOCATION,
    CONF_PRECISION,
    CONF_SOURCES,
    CONF_WEATHER_ENTITY,
    DOMAIN,
    MET_NOWCAST,
    UPDATE_INTERVAL,
    USER_AGENT,
)
from .core.derive import dew_point_from_humidity
from .core.forecast.engine import Airport, ForecastEngine
from .core.forecast.met_nowcast import MetNowcast
from .core.nowcast import Nowcast
from .core.fusion import DEFAULT_LIMITS, Choice, Group, GroupLimits, Settings, fuse
from .core.model import Field, Kind, Location, PrecipType, Reading, intensity_class, plausible
from .core.privacy import DEFAULT_PRECISION, Precision
from .core.safety import (
    MAX_IMAGE_BYTES,
    MAX_JSON_BYTES,
    MAX_TEXT_BYTES,
    HttpStatusError,
    UnsafeResponse,
    check_url,
    finite,
    read_capped,
    safe_error,
    valid_id,
)
from .core.sources import DEFAULT_SOURCES, Source, make_sources
from .core.sources.esoh import StationHeights

_LOGGER = logging.getLogger(__name__)
TIMEOUT = ClientTimeout(total=30)



@dataclass
class BellweatherData:
    """Runtime data of the one Bellweather entry: the shared HTTP layer and
    one coordinator per location (config subentry)."""

    session: ClientSession
    http: Any  # core.cache.CachingHttp around HaHttp, shared by every location
    locations: dict[str, BellweatherCoordinator]
    heights: StationHeights = field(default_factory=StationHeights)  # shared, kept across restarts


type BellweatherConfigEntry = ConfigEntry[BellweatherData]


class HaHttp:
    """The `core.sources.base.Http` calls, with the rules of `core.safety`:
    https only, no redirects, a hard size cap per response (counted while
    reading, after decompression), errors without the URL.

    It uses its own session without a cookie jar, so no service can set a
    cookie and recognise this installation across IP address changes."""

    def __init__(self, session: ClientSession) -> None:
        self._session = session

    async def _read(self, url: str, params: dict[str, str] | None, limit: int, allow_404: bool) -> bytes | None:
        check_url(url)
        async with self._session.get(
            url, params=params, timeout=TIMEOUT, allow_redirects=False, headers={"User-Agent": USER_AGENT}
        ) as resp:
            if resp.status == 404 and allow_404:
                return None
            if resp.status != 200:
                raise HttpStatusError(resp.status, finite(resp.headers.get("Retry-After")))
            if (resp.content_length or 0) > limit:
                raise UnsafeResponse("response too large")
            return await read_capped(resp.content.iter_chunked(65536), limit)

    async def get_json(self, url: str, params: dict[str, str] | None = None, limit: int = MAX_JSON_BYTES) -> Any:
        data = await self._read(url, params, limit, allow_404=True)
        return None if data is None else json_loads(data)

    async def get_bytes(self, url: str, params: dict[str, str] | None = None, limit: int = MAX_IMAGE_BYTES) -> bytes:
        return await self._read(url, params, limit, allow_404=False) or b""

    async def get_text(self, url: str, params: dict[str, str] | None = None, limit: int = MAX_TEXT_BYTES) -> str:
        data = await self._read(url, params, limit, allow_404=False) or b""
        return data.decode("utf-8", errors="replace")


# Weather entity condition -> what it says about rain, type and thunder.
CONDITIONS: dict[str, tuple[bool, PrecipType, bool]] = {
    "rainy": (True, PrecipType.RAIN, False),
    "pouring": (True, PrecipType.RAIN, False),
    "lightning-rainy": (True, PrecipType.RAIN, True),
    "lightning": (False, PrecipType.NONE, True),
    "snowy": (True, PrecipType.SNOW, False),
    "snowy-rainy": (True, PrecipType.SLEET, False),
    "hail": (True, PrecipType.HAIL, False),
}


@dataclass
class SourceStatus:
    """What the diagnostics show per source, and when to ask it again."""

    last_success: datetime | None = None
    last_error: str | None = None
    last_error_at: datetime | None = None
    readings: int = 0
    failures: int = 0
    next_try: datetime | None = None


# After a failure a source rests 5, 10, 20, 40, then 60 minutes, or as long
# as its Retry-After header asks (at most 6 hours), so a service that is down
# or rate-limiting is not hammered by every installation.
BACKOFF_MINUTES = (5, 10, 20, 40, 60)
MAX_RETRY_AFTER = timedelta(hours=6)
# The longest one source (all its requests together) may take per update.
SOURCE_DEADLINE = timedelta(seconds=90)
MAX_STATIONS = 200


def limits_from_options(raw: dict[str, Any] | None) -> dict[Group, GroupLimits]:
    """The collapsed "limits" section, flat keys like `wind_station_km`."""
    raw = raw or {}
    out: dict[Group, GroupLimits] = {}
    for group, default in DEFAULT_LIMITS.items():
        height = raw.get(f"{group.value}_height_m", default.height_m)
        out[group] = GroupLimits(
            station_km=float(raw.get(f"{group.value}_station_km", default.station_km)),
            airport_km=float(raw.get(f"{group.value}_airport_km", default.airport_km)),
            height_m=None if default.height_m is None or height in (None, "") else float(height),
        )
    return out


class BellweatherCoordinator(DataUpdateCoordinator[dict[Field, Choice]]):
    config_entry: BellweatherConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: BellweatherConfigEntry,
        subentry: ConfigSubentry,
        http: Any,
        heights: StationHeights | None = None,
    ) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=f"{DOMAIN} {subentry.title}", update_interval=UPDATE_INTERVAL)
        self.subentry = subentry
        self.subentry_id = subentry.subentry_id
        self.location_name = subentry.title
        self.http = http
        self.heights = heights if heights is not None else StationHeights()
        self.sources: list[Source] = []
        self.status: dict[str, SourceStatus] = {}
        self.stations: dict[str, str] = {}  # id -> label, for the options
        self.last_precip: Choice | None = None
        self.nowcast: Nowcast | None = None
        self.nowcast_error: str | None = None
        self.airports: list[Airport] = []
        self.engine: ForecastEngine | None = None  # set by the forecast coordinator; gets the measured values
        self.forecast: Any = None  # the ForecastCoordinator, set in __init__.py
        self._met = MetNowcast()
        self.reload_settings()

    def reload_settings(self) -> None:
        # Shared settings from the entry, then this location's own.
        settings = {**self.config_entry.data, **self.config_entry.options, **self.subentry.data}
        loc = settings.get(CONF_LOCATION) or {}
        lat = float(loc.get("latitude", self.hass.config.latitude))
        lon = float(loc.get("longitude", self.hass.config.longitude))
        elevation = finite(settings.get(CONF_ELEVATION))
        at_home = (round(lat, 3), round(lon, 3)) == (round(self.hass.config.latitude, 3), round(self.hass.config.longitude, 3))
        if elevation is None and at_home and self.hass.config.elevation is not None:
            elevation = float(self.hass.config.elevation)
        self.location = Location(latitude=lat, longitude=lon, elevation=elevation)
        self.precision = Precision(settings.get(CONF_PRECISION, DEFAULT_PRECISION))
        limits = limits_from_options(settings.get(CONF_LIMITS))
        excluded = {valid_id(sid) for sid in settings.get(CONF_EXCLUDED_STATIONS) or []}
        self.fusion_settings = Settings(limits=limits, excluded_stations=frozenset(sid for sid in excluded if sid))
        keys = list(settings.get(CONF_SOURCES, DEFAULT_SOURCES))
        self.sources = make_sources(keys, self.http, limits, self.heights)
        self.use_met_nowcast = MET_NOWCAST in keys
        self.limits = limits
        self.status = {s.key: SourceStatus() for s in self.sources}
        self.settings = settings

    def restore_last_precip(self, when: datetime) -> None:
        """Called by the "last precipitation" sensor with its state from
        before the restart."""
        restored = Choice(
            Reading(field=Field.LAST_PRECIP, value=when.isoformat(), kind=Kind.DERIVED, source="restored", observed=when),
            1,
        )
        if self.last_precip is None or datetime.fromisoformat(str(self.last_precip.reading.value)) < when:
            self.last_precip = restored
            if self.data is not None:
                self.data[Field.LAST_PRECIP] = restored

    async def _async_update_data(self) -> dict[Field, Choice]:
        now = dt_util.utcnow()
        due = [s for s in self.sources if not (st := self.status.get(s.key)) or not st.next_try or st.next_try <= now]
        results = await asyncio.gather(
            *(self._fetch_with_deadline(s, now) for s in due),
            return_exceptions=True,
        )
        readings: list[Reading] = []
        for source, result in zip(due, results, strict=True):
            status = self.status.setdefault(source.key, SourceStatus())
            if isinstance(result, BaseException):
                if isinstance(result, asyncio.CancelledError):
                    raise result
                if status.last_error is None:
                    # Never the exception text: it can hold the URL with the shared point.
                    _LOGGER.warning("%s failed: %s", source.key, safe_error(result))
                status.last_error, status.last_error_at = safe_error(result), now
                status.failures += 1
                rest = timedelta(minutes=BACKOFF_MINUTES[min(status.failures, len(BACKOFF_MINUTES)) - 1])
                retry_after = finite(getattr(result, "retry_after", None))
                if retry_after and retry_after > 0:
                    seconds = min(retry_after, MAX_RETRY_AFTER.total_seconds())
                    rest = max(rest, timedelta(seconds=seconds))
                status.next_try = now + rest
                continue
            if status.last_error is not None:
                _LOGGER.info("%s working again", source.key)
            status.last_success, status.last_error, status.readings = now, None, len(result)
            status.failures, status.next_try = 0, None
            readings.extend(result)
        readings.extend(self._local_readings())
        readings.extend(self._weather_entity_readings())
        if not readings and self.sources:
            raise UpdateFailed("no source returned data")
        self._remember_stations(readings)
        result = fuse(readings, self.location, now, self.fusion_settings, self.last_precip)
        self.last_precip = result.get(Field.LAST_PRECIP, self.last_precip)
        if self.engine is not None:
            self.engine.log.add(result, now)
        await self._update_nowcast(now)
        return result

    async def _fetch_with_deadline(self, source: Source, now: datetime) -> list[Reading]:
        """No source may hold the update longer than SOURCE_DEADLINE."""
        async with asyncio.timeout(SOURCE_DEADLINE.total_seconds()):
            return await source.fetch(self.http, self.location, self.precision, now)

    async def _update_nowcast(self, now: datetime) -> None:
        """The official nowcast where there is one, else the own radar
        extrapolation. A failure in this step never fails the update."""
        cast: Nowcast | None = None
        try:
            async with asyncio.timeout(SOURCE_DEADLINE.total_seconds()):
                if self.use_met_nowcast:
                    cast = await self._met.fetch(self.http, self.location, self.precision, now)
                radar = next((s for s in self.sources if s.key == "rainviewer"), None)
                if cast is None and radar is not None:
                    cast = await radar.nowcast(self.http, self.location, now)
            self.nowcast_error = None
        except Exception as err:  # noqa: BLE001 - logged without details that could hold the location
            if self.nowcast_error is None:
                _LOGGER.warning("nowcast failed: %s", safe_error(err))
            self.nowcast_error = safe_error(err)
        self.nowcast = cast

    def _remember_stations(self, readings: list[Reading]) -> None:
        airports: dict[str, Airport] = {}
        temp_limits = self.limits[Group.TEMPERATURE]
        nearby: dict[str, tuple[float, str]] = {}
        for r in readings:
            if r.station_id and r.kind in (Kind.STATION, Kind.AIRPORT):
                nearby[r.station_id] = (r.distance_km or 0.0, f"{r.station} – {r.distance_km} km")
            # Airports close enough, and close enough in height, to stand in for
            # the home when scoring the forecast against their archive.
            if (
                r.kind is Kind.AIRPORT and r.station_id and r.latitude is not None and r.longitude is not None
                and (r.distance_km or 0) <= temp_limits.airport_km
                and (temp_limits.height_m is None or r.elevation is None or self.location.elevation is None
                     or abs(r.elevation - self.location.elevation) <= temp_limits.height_m)
            ):
                airports[r.station_id] = Airport(r.station_id, r.latitude, r.longitude, r.distance_km)
        if airports:
            self.airports = sorted(airports.values(), key=lambda a: a.distance_km or 0.0)[:5]
        # Rebuilt every update and capped: a service cannot grow this list.
        closest = sorted(nearby.items(), key=lambda kv: kv[1][0])[:MAX_STATIONS]
        self.stations = {sid: label for sid, (_, label) in closest}

    # --- local sensors -------------------------------------------------------

    def _state(self, key: str) -> State | None:
        entity_id = self.settings.get(key)
        if not entity_id:
            return None
        entry = er.async_get(self.hass).async_get(entity_id)
        if entry is not None and entry.platform == DOMAIN:
            return None  # our own entity would feed on itself and freeze
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return None
        return state

    def _local(self, field: Field, value: float | bool | str, state: State) -> Reading:
        return Reading(
            field=field, value=value, kind=Kind.LOCAL, source="local",
            observed=getattr(state, "last_reported", None) or state.last_updated,
            station=state.entity_id, distance_km=0.0,
        )

    def _converted(self, key: str, converter, unit: str, field: Field | None = None) -> tuple[float, State] | None:
        state = self._state(key)
        if state is None:
            return None
        value = finite(state.state)
        if value is None:
            return None
        try:
            src = state.attributes.get("unit_of_measurement")
            if converter is not None and src and src != unit:
                value = finite(converter.convert(value, src, unit))
        except (ValueError, HomeAssistantError, OverflowError):
            return None
        if value is None or (field is not None and not plausible(field, value)):
            return None
        return value, state

    def _local_readings(self) -> list[Reading]:
        out: list[Reading] = []
        rate = self._converted(CONF_LOCAL_RAIN_RATE, SpeedConverter, UnitOfVolumetricFlux.MILLIMETERS_PER_HOUR, Field.INTENSITY)
        if rate:
            out.append(self._local(Field.INTENSITY, round(rate[0], 2), rate[1]))
            out.append(self._local(Field.INTENSITY_CLASS, intensity_class(rate[0]).value, rate[1]))
            out.append(self._local(Field.RAIN_NOW, rate[0] >= 0.1, rate[1]))
        wet = self._state(CONF_LOCAL_RAIN_STATE)
        if wet is not None:
            # A rain-state sensor answers "wet now" faster than a gauge: it wins.
            out = [r for r in out if r.field is not Field.RAIN_NOW]
            out.append(self._local(Field.RAIN_NOW, wet.state == "on", wet))
        for key, field in (
            (CONF_LOCAL_WIND_SPEED, Field.WIND_SPEED),
            (CONF_LOCAL_WIND_GUST, Field.WIND_GUST),
        ):
            hit = self._converted(key, SpeedConverter, UnitOfSpeed.KILOMETERS_PER_HOUR, field)
            if hit:
                out.append(self._local(field, round(hit[0], 1), hit[1]))
        direction = self._converted(CONF_LOCAL_WIND_DIRECTION, None, "°", Field.WIND_DIRECTION)
        if direction:
            out.append(self._local(Field.WIND_DIRECTION, direction[0], direction[1]))
        vis = self._converted(CONF_LOCAL_VISIBILITY, DistanceConverter, UnitOfLength.KILOMETERS, Field.VISIBILITY)
        if vis:
            out.append(self._local(Field.VISIBILITY, round(vis[0], 2), vis[1]))
            out.append(self._local(Field.FOG, vis[0] < 1.0, vis[1]))
        snow = self._converted(CONF_LOCAL_SNOW_DEPTH, DistanceConverter, UnitOfLength.CENTIMETERS, Field.SNOW_DEPTH)
        if snow:
            out.append(self._local(Field.SNOW_DEPTH, round(snow[0], 1), snow[1]))
        temp = self._converted(CONF_LOCAL_TEMPERATURE, TemperatureConverter, UnitOfTemperature.CELSIUS, Field.TEMPERATURE)
        if temp:
            out.append(self._local(Field.TEMPERATURE, round(temp[0], 1), temp[1]))
        hum = self._converted(CONF_LOCAL_HUMIDITY, None, PERCENTAGE, Field.HUMIDITY)
        if hum:
            out.append(self._local(Field.HUMIDITY, round(hum[0], 1), hum[1]))
            if temp:
                dew = dew_point_from_humidity(temp[0], hum[0])
                out.append(self._local(Field.DEW_POINT, round(dew, 1), temp[1]))
        pressure = self._converted(CONF_LOCAL_PRESSURE, PressureConverter, UnitOfPressure.HPA, Field.PRESSURE)
        if pressure:
            out.append(self._local(Field.PRESSURE, round(pressure[0], 1), pressure[1]))
        return out

    # --- weather entity (model, last link) -----------------------------------

    def _weather_entity_readings(self) -> list[Reading]:
        state = self._state(CONF_WEATHER_ENTITY)
        if state is None:
            return []
        attrs = state.attributes
        observed = getattr(state, "last_reported", None) or state.last_updated
        common = dict(kind=Kind.MODEL, source=state.entity_id, observed=observed, attribution=attrs.get("attribution"))
        out: list[Reading] = []
        rain, ptype, thunder = CONDITIONS.get(state.state, (False, PrecipType.NONE, False))
        out.append(Reading(field=Field.RAIN_NOW, value=rain, **common))
        out.append(Reading(field=Field.PRECIP_TYPE, value=ptype.value, **common))
        out.append(Reading(field=Field.THUNDER, value=thunder, **common))
        out.append(Reading(field=Field.FOG, value=state.state == "fog", **common))

        def convert(attr: str, unit_attr: str | None, converter, unit: str, field: Field, digits: int = 1) -> None:
            value = finite(attrs.get(attr))
            if value is None:
                return
            try:
                src = attrs.get(unit_attr) if unit_attr else None
                if converter is not None:
                    if not src:
                        return
                    value = finite(converter.convert(value, src, unit))
            except (ValueError, HomeAssistantError, OverflowError):
                return
            if value is not None and plausible(field, value):
                out.append(Reading(field=field, value=round(value, digits), **common))

        convert("wind_speed", "wind_speed_unit", SpeedConverter, UnitOfSpeed.KILOMETERS_PER_HOUR, Field.WIND_SPEED)
        convert("wind_gust_speed", "wind_speed_unit", SpeedConverter, UnitOfSpeed.KILOMETERS_PER_HOUR, Field.WIND_GUST)
        convert("visibility", "visibility_unit", DistanceConverter, UnitOfLength.KILOMETERS, Field.VISIBILITY, 2)
        convert("temperature", "temperature_unit", TemperatureConverter, UnitOfTemperature.CELSIUS, Field.TEMPERATURE)
        convert("dew_point", "temperature_unit", TemperatureConverter, UnitOfTemperature.CELSIUS, Field.DEW_POINT)
        convert("pressure", "pressure_unit", PressureConverter, UnitOfPressure.HPA, Field.PRESSURE)
        convert("humidity", None, None, PERCENTAGE, Field.HUMIDITY)
        convert("cloud_coverage", None, None, PERCENTAGE, Field.CLOUD_COVER)
        bearing = finite(attrs.get("wind_bearing"))
        if bearing is not None and plausible(Field.WIND_DIRECTION, bearing):
            out.append(Reading(field=Field.WIND_DIRECTION, value=bearing, **common))
        return out

    # --- diagnostics ---------------------------------------------------------

    def source_status(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for key, st in self.status.items():
            out[key] = {
                "last_success": st.last_success.isoformat() if st.last_success else None,
                "last_error": st.last_error,
                "last_error_at": st.last_error_at.isoformat() if st.last_error_at else None,
                "readings": st.readings,
                "next_try": st.next_try.isoformat() if st.next_try else None,
            }
        for source in self.sources:
            covered = getattr(source, "covered", None)
            if covered is not None:
                out[source.key]["radar_coverage"] = covered
        return out
