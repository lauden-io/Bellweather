"""The weather entity of each location: observed now, blended forecast.

The current values are the observed ones (the same as the sensors), the
condition is "Weather now". The UV index is the blend's value for the
current hour (no source measures it). The hourly and daily forecasts are
the blend.

Entity id: a new location's weather entity is always weather.<location
name>, so the first one is weather.home, the id dashboards and automations
usually use. If another integration's entity already has that id when the
location is created, that entity is renamed to weather.<its integration>_
<name> (Home Assistant moves its history with it) and Bellweather takes the
id. Where that is not possible (the id belongs to another Bellweather
location of the same name, or to an entity Home Assistant cannot rename),
the entity becomes weather.bellweather, or weather.bellweather_<name>, never
a numbered weather.<name>_2. Entities that already exist are never renamed.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.components.weather import (
    CoordinatorWeatherEntity,
    Forecast,
    WeatherEntityFeature,
)
from homeassistant.const import UnitOfLength, UnitOfPrecipitationDepth, UnitOfPressure, UnitOfSpeed, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import slugify
from homeassistant.helpers.update_coordinator import TimestampDataUpdateCoordinator

from .const import DOMAIN
from .entity import location_device
from .coordinator import BellweatherConfigEntry, BellweatherCoordinator
from .core.forecast.members import ATTRIBUTION as MODELS_ATTRIBUTION
from .core.model import Field
from .forecast_coordinator import ForecastCoordinator

_LOGGER = logging.getLogger(__name__)

# A forecast stays on show this long after the last successful update.
FORECAST_VALID = timedelta(hours=3)
RENAME_WAIT = 5.0  # seconds for a renamed entity to leave its old id


async def async_setup_entry(
    hass: HomeAssistant, entry: BellweatherConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    for coordinator in entry.runtime_data.locations.values():
        entity = BellweatherWeather(coordinator, coordinator.forecast)
        await claim_entity_id(hass, entity)
        async_add_entities([entity], config_subentry_id=coordinator.subentry_id)


async def claim_entity_id(hass: HomeAssistant, entity: BellweatherWeather) -> None:
    """Give a new location's weather entity weather.<location name>, renaming
    another integration's entity that holds it. Does nothing for an entity
    that is already registered: an existing id is never changed."""
    registry = er.async_get(hass)
    if registry.async_get_entity_id("weather", DOMAIN, entity.unique_id or "") is not None:
        return
    object_id = slugify(entity.coordinator.location_name or "")
    if not object_id:
        return
    preferred = f"weather.{object_id}"
    other = registry.async_get(preferred)
    if other is None:
        if hass.states.get(preferred) is not None:  # an entity without a registry entry: cannot be renamed
            _fallback_id(hass, registry, entity, object_id)
        return
    if other.platform == DOMAIN:  # another Bellweather location with the same name
        _fallback_id(hass, registry, entity, object_id)
        return
    new_id = registry.async_get_available_entity_id("weather", f"{other.platform}_{object_id}")
    try:
        registry.async_update_entity(other.entity_id, new_entity_id=new_id)
    except ValueError as err:
        _LOGGER.warning("Could not rename %s: %s", preferred, err)
        _fallback_id(hass, registry, entity, object_id)
        return
    _LOGGER.warning(
        "%s belonged to the %s integration; it is now %s, so that this Bellweather location takes %s",
        preferred, other.platform, new_id, preferred,
    )
    # The id itself comes from the device name ("Home" gives weather.home)
    # once the old state has left it.
    deadline = asyncio.get_running_loop().time() + RENAME_WAIT
    while hass.states.get(preferred) is not None and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.1)


def _fallback_id(hass: HomeAssistant, registry: er.EntityRegistry, entity: BellweatherWeather, object_id: str) -> None:
    """When weather.<name> cannot be taken: weather.bellweather, or
    weather.bellweather_<name>, never a numbered weather.<name>_2."""
    for candidate in ("weather.bellweather", f"weather.bellweather_{object_id}"):
        if registry.async_get(candidate) is None and hass.states.get(candidate) is None:
            entity.entity_id = candidate
            return


class BellweatherWeather(
    CoordinatorWeatherEntity[
        BellweatherCoordinator, ForecastCoordinator, ForecastCoordinator, TimestampDataUpdateCoordinator[None]
    ]
):
    _attr_has_entity_name = True
    _attr_name = None  # the device name: the location's name
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_native_pressure_unit = UnitOfPressure.HPA
    _attr_native_wind_speed_unit = UnitOfSpeed.KILOMETERS_PER_HOUR
    _attr_native_visibility_unit = UnitOfLength.KILOMETERS
    _attr_native_precipitation_unit = UnitOfPrecipitationDepth.MILLIMETERS
    _attr_supported_features = WeatherEntityFeature.FORECAST_HOURLY | WeatherEntityFeature.FORECAST_DAILY

    def __init__(self, coordinator: BellweatherCoordinator, forecast: ForecastCoordinator) -> None:
        super().__init__(
            coordinator, hourly_coordinator=forecast, daily_coordinator=forecast,
            hourly_forecast_valid=FORECAST_VALID, daily_forecast_valid=FORECAST_VALID,
        )
        self.forecast_coordinator = forecast
        self._attr_unique_id = f"{coordinator.subentry_id}_weather"
        self._attr_device_info = location_device(coordinator)

    def _value(self, field: Field) -> Any:
        choice = (self.coordinator.data or {}).get(field)
        return None if choice is None else choice.reading.value

    @property
    def condition(self) -> str | None:
        return self._value(Field.CONDITION)

    @property
    def native_temperature(self) -> float | None:
        return self._value(Field.TEMPERATURE)

    @property
    def native_apparent_temperature(self) -> float | None:
        return self._value(Field.APPARENT_TEMPERATURE)

    @property
    def native_dew_point(self) -> float | None:
        return self._value(Field.DEW_POINT)

    @property
    def uv_index(self) -> float | None:
        """The blend's UV index for the current hour."""
        hours = self.forecast_coordinator.engine.hours
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        for h in hours[:2]:
            if h.time == now:
                return h.uv_index
        return None

    @property
    def humidity(self) -> float | None:
        return self._value(Field.HUMIDITY)

    @property
    def native_pressure(self) -> float | None:
        return self._value(Field.PRESSURE)

    @property
    def native_wind_speed(self) -> float | None:
        return self._value(Field.WIND_SPEED)

    @property
    def native_wind_gust_speed(self) -> float | None:
        return self._value(Field.WIND_GUST)

    @property
    def wind_bearing(self) -> float | None:
        return self._value(Field.WIND_DIRECTION)

    @property
    def native_visibility(self) -> float | None:
        return self._value(Field.VISIBILITY)

    @property
    def cloud_coverage(self) -> float | None:
        return self._value(Field.CLOUD_COVER)

    @property
    def attribution(self) -> str:
        return f"Observations: see each sensor. Forecast: {MODELS_ATTRIBUTION}"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        engine = self.forecast_coordinator.engine
        corr = engine.verification.corrections
        return {
            "forecast_members": engine.members_used,
            "forecast_corrected": bool(corr.temperature or corr.wind or corr.dew_point or corr.pressure),
            "verification_days": corr.days,
            "verification_source": corr.source,
            "forecast_updated": engine.fetched.isoformat() if engine.fetched else None,
        }

    @callback
    def _async_forecast_hourly(self) -> list[Forecast] | None:
        hours = self.forecast_coordinator.engine.hours
        if not hours:
            return None
        return [
            Forecast(
                datetime=h.time.isoformat(), condition=h.condition, native_temperature=h.temperature,
                native_apparent_temperature=h.apparent_temperature, native_dew_point=h.dew_point,
                humidity=h.humidity, native_pressure=h.pressure, native_wind_speed=h.wind_speed,
                native_wind_gust_speed=h.wind_gust, wind_bearing=h.wind_bearing,
                cloud_coverage=None if h.cloud_cover is None else round(h.cloud_cover),
                native_precipitation=h.precipitation, precipitation_probability=h.precipitation_probability,
                is_daytime=h.is_daytime, uv_index=h.uv_index,
            )
            for h in hours[:168]
        ]

    @callback
    def _async_forecast_daily(self) -> list[Forecast] | None:
        days = self.forecast_coordinator.engine.days
        if not days:
            return None
        return [
            Forecast(
                datetime=d.start.isoformat(), condition=d.condition, native_temperature=d.temperature,
                native_templow=d.templow, native_precipitation=d.precipitation,
                precipitation_probability=d.precipitation_probability, native_wind_speed=d.wind_speed,
                native_wind_gust_speed=d.wind_gust, wind_bearing=d.wind_bearing,
                cloud_coverage=None if d.cloud_cover is None else round(d.cloud_cover), humidity=d.humidity,
                native_apparent_temperature=d.apparent_temperature, native_dew_point=d.dew_point,
                native_pressure=d.pressure, uv_index=d.uv_index,
            )
            for d in days
        ]
