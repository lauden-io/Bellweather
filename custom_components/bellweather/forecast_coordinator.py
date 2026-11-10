"""The hourly forecast and the daily verification, apart from the 5-minute
"now" update so that a slow or failing forecast never holds the
observations back."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.const import UnitOfPrecipitationDepth, UnitOfPressure, UnitOfSpeed, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import TimestampDataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import (
    DistanceConverter,
    PressureConverter,
    SpeedConverter,
    TemperatureConverter,
)

from .const import CONF_FORECAST_MEMBERS, CONF_SOURCES, CONF_USE_ARCHIVE, DOMAIN, FORECAST_INTERVAL, VERIFY_INTERVAL
from .coordinator import BellweatherConfigEntry, BellweatherCoordinator
from .core.forecast.engine import ForecastEngine, verification_key
from .core.forecast.members import LIMITS, Hour, Member
from .core.safety import finite, safe_error
from .core.sources import DEFAULT_SOURCES

_LOGGER = logging.getLogger(__name__)
STORAGE_VERSION = 1
SAVE_DELAY = 300  # seconds; the store is written at most every 5 minutes
MEMBER_DEADLINE = 30  # seconds for the members' forecast action
RETRY = timedelta(hours=1)  # after a failed verification, or one without the airport archive

# A weather entity's condition -> a WMO code with the same meaning, for the blend's type rules.
CONDITION_CODES = {"lightning-rainy": 95, "lightning": 95, "fog": 45, "hail": 96}
# Integrations whose hourly precipitation at T is the sum of the hour before T.
PRECEDING_HOUR_PLATFORMS = {"open_meteo"}


class ForecastCoordinator(TimestampDataUpdateCoordinator[None]):
    config_entry: BellweatherConfigEntry

    def __init__(self, hass: HomeAssistant, entry: BellweatherConfigEntry, now_coordinator: BellweatherCoordinator) -> None:
        super().__init__(
            hass, _LOGGER, config_entry=entry, name=f"{DOMAIN} forecast {now_coordinator.location_name}",
            update_interval=FORECAST_INTERVAL,
        )
        self.now = now_coordinator
        settings = now_coordinator.settings
        self.engine = ForecastEngine(use_open_meteo="open_meteo" in settings.get(CONF_SOURCES, DEFAULT_SOURCES))
        self.member_entities: list[str] = [str(e) for e in settings.get(CONF_FORECAST_MEMBERS) or []][:10]
        self.use_archive = bool(settings.get(CONF_USE_ARCHIVE, False))
        # One history per location, kept as long as the location exists.
        self.store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{now_coordinator.subentry_id}")
        self.verify_error: str | None = None
        self.archive_error: str | None = None
        self.retry_at: datetime | None = None
        # A stored verification is reused only for the same point, precision,
        # models and archive setting.
        self.engine.key = verification_key(
            now_coordinator.location, now_coordinator.precision, self.engine.models, self.use_archive
        )
        now_coordinator.engine = self.engine

    async def async_load(self) -> None:
        try:
            self.engine.load(await self.store.async_load(), dt_util.utcnow())
        except Exception as err:  # noqa: BLE001 - any damage: start fresh rather than fail
            # A damaged store must not stop the integration: start fresh.
            _LOGGER.warning("forecast history could not be read, starting fresh: %s", safe_error(err))

    async def _async_update_data(self) -> None:
        now = dt_util.utcnow()
        loc, precision = self.now.location, self.now.precision
        verified = self.engine.verification.updated
        due = verified is None or now - verified >= VERIFY_INTERVAL
        if self.retry_at is not None:
            due = now >= self.retry_at
        if due:
            airport = self.now.airports[0] if self.use_archive and self.now.airports else None
            try:
                archive_error = await self.engine.verify(self.now.http, loc, precision, now, airport)
            except Exception as err:  # noqa: BLE001 - the forecast still works with the old corrections
                if self.verify_error is None:
                    _LOGGER.warning("forecast verification failed: %s", safe_error(err))
                self.verify_error = safe_error(err)
                self.retry_at = now + RETRY  # not at every update
            else:
                self.verify_error = None
                if archive_error is not None:
                    if self.archive_error is None:
                        _LOGGER.warning(
                            "airport archive failed (%s); verified without it, trying again in an hour", archive_error
                        )
                    self.archive_error = archive_error
                    self.retry_at = now + RETRY
                else:
                    self.archive_error = None
                    self.retry_at = None
        entity_members = await self._entity_members()
        try:
            await self.engine.forecast(
                self.now.http, loc, precision, now, dt_util.get_default_time_zone(), entity_members
            )
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(f"forecast failed: {safe_error(err)}") from None
        if not self.engine.hours:
            raise UpdateFailed("no forecast member answered")
        self.store.async_delay_save(self.engine.dump, SAVE_DELAY)

    async def _entity_members(self) -> dict[str, Member]:
        """The hourly forecasts of the weather entities picked as members,
        converted to Bellweather's units and hour convention. Read-only: the
        `weather.get_forecasts` action only reads."""
        registry = er.async_get(self.hass)
        ids = []
        for entity_id in self.member_entities:
            entry = registry.async_get(entity_id)
            if entry is not None and entry.platform == DOMAIN:
                continue  # never our own entity: it would feed on itself
            if self.hass.states.get(entity_id) is not None:
                ids.append(entity_id)
        if not ids:
            return {}
        try:
            async with asyncio.timeout(MEMBER_DEADLINE):
                response = await self.hass.services.async_call(
                    "weather", "get_forecasts", {"entity_id": ids, "type": "hourly"}, blocking=True, return_response=True
                )
        except (HomeAssistantError, TimeoutError) as err:
            _LOGGER.debug("weather entity forecasts unavailable: %s", safe_error(err))
            return {}
        out: dict[str, Member] = {}
        for entity_id in ids:
            items = ((response or {}).get(entity_id) or {}).get("forecast")
            state = self.hass.states.get(entity_id)
            entry = registry.async_get(entity_id)
            if isinstance(items, list) and state is not None:
                shift = entry is not None and entry.platform in PRECEDING_HOUR_PLATFORMS
                try:
                    out[entity_id] = convert_member(items[:240], state.attributes, shift)
                except Exception as err:  # noqa: BLE001 - one broken member must not stop the forecast
                    _LOGGER.debug("member %s skipped: %s", entity_id, safe_error(err))
        return out

    async def async_remove(self) -> None:
        await self.store.async_remove()


def convert_member(items: list[Any], attrs: Any, preceding_hour: bool) -> Member:
    """A weather entity's hourly forecast as a blend member."""
    def unit(name: str) -> str | None:
        value = attrs.get(name)
        return str(value) if value else None

    def conv(value: Any, converter, src: str | None, dst: str, var: str) -> float | None:
        number = finite(value)
        if number is None:
            return None
        if src is not None and src != dst:
            try:
                number = finite(converter.convert(number, src, dst))
            except (HomeAssistantError, ValueError, OverflowError):
                return None
        return limited(number, var)

    def limited(number: float | None, var: str) -> float | None:
        low, high = LIMITS[var]
        return number if number is not None and low <= number <= high else None

    t_unit, w_unit = unit("temperature_unit"), unit("wind_speed_unit")
    p_unit, pr_unit = unit("precipitation_unit"), unit("pressure_unit")
    rows: list[tuple[datetime, Hour]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            when = dt_util.parse_datetime(str(item.get("datetime"))[:40])
            if when is None or when.tzinfo is None or not 2000 <= when.year <= 2100:
                continue
            when = when.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
        except (ValueError, OverflowError):
            continue
        amount = conv(item.get("precipitation"), DistanceConverter, p_unit, UnitOfPrecipitationDepth.MILLIMETERS, "precipitation")
        cond = str(item.get("condition") or "")
        snow = amount if cond == "snowy" else (amount / 2 if amount is not None and cond == "snowy-rainy" else 0.0)
        rows.append((when, Hour(
            temperature=conv(item.get("temperature"), TemperatureConverter, t_unit, UnitOfTemperature.CELSIUS, "temperature_2m"),
            humidity=limited(finite(item.get("humidity")), "relative_humidity_2m"),
            dew_point=conv(item.get("dew_point"), TemperatureConverter, t_unit, UnitOfTemperature.CELSIUS, "dew_point_2m"),
            pressure=conv(item.get("pressure"), PressureConverter, pr_unit, UnitOfPressure.HPA, "pressure_msl"),
            cloud_cover=limited(finite(item.get("cloud_coverage")), "cloud_cover"),
            wind_speed=conv(item.get("wind_speed"), SpeedConverter, w_unit, UnitOfSpeed.KILOMETERS_PER_HOUR, "wind_speed_10m"),
            wind_gust=conv(item.get("wind_gust_speed"), SpeedConverter, w_unit, UnitOfSpeed.KILOMETERS_PER_HOUR, "wind_gusts_10m"),
            wind_direction=limited(finite(item.get("wind_bearing")), "wind_direction_10m"),
            precipitation=amount, snow_water=snow, weather_code=CONDITION_CODES.get(cond),
            visibility=None, uv_index=limited(finite(item.get("uv_index")), "uv_index"),
        )))
    member: Member = dict(rows)
    if preceding_hour:
        # Instant values stay at T; the amount given at T belongs to the hour before.
        amounts = {when: (hour.precipitation, hour.snow_water) for when, hour in rows}
        for when, hour in member.items():
            hour.precipitation, hour.snow_water = amounts.get(when + timedelta(hours=1), (None, None))
    return member
