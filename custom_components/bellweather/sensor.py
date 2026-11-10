"""Value sensors, plus one diagnostic sensor for the sources."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import RestoreSensor, SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import (
    DEGREE,
    PERCENTAGE,
    EntityCategory,
    UnitOfTime,
    UnitOfLength,
    UnitOfPrecipitationDepth,
    UnitOfPressure,
    UnitOfSpeed,
    UnitOfTemperature,
    UnitOfVolumetricFlux,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .coordinator import BellweatherConfigEntry, BellweatherCoordinator
from .core import nowcast as nc
from .core.condition import Condition
from .core.model import Field, IntensityClass, PrecipType
from .entity import BellweatherEntity, BellweatherFieldEntity, location_device
from .forecast_coordinator import ForecastCoordinator

M = SensorStateClass.MEASUREMENT
# field -> (device class, unit, state class, enum options)
SENSORS: dict[Field, tuple[SensorDeviceClass | None, str | None, SensorStateClass | None, list[str] | None]] = {
    Field.CONDITION: (SensorDeviceClass.ENUM, None, None, [c.value for c in Condition]),
    Field.TEMPERATURE: (SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS, M, None),
    Field.APPARENT_TEMPERATURE: (SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS, M, None),
    Field.DEW_POINT: (SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS, M, None),
    Field.HUMIDITY: (SensorDeviceClass.HUMIDITY, PERCENTAGE, M, None),
    Field.PRESSURE: (SensorDeviceClass.ATMOSPHERIC_PRESSURE, UnitOfPressure.HPA, M, None),
    Field.INTENSITY: (SensorDeviceClass.PRECIPITATION_INTENSITY, UnitOfVolumetricFlux.MILLIMETERS_PER_HOUR, M, None),
    Field.INTENSITY_CLASS: (SensorDeviceClass.ENUM, None, None, [c.value for c in IntensityClass]),
    Field.PRECIP_TYPE: (SensorDeviceClass.ENUM, None, None, [t.value for t in PrecipType]),
    Field.PRECIP_LAST_HOUR: (SensorDeviceClass.PRECIPITATION, UnitOfPrecipitationDepth.MILLIMETERS, M, None),
    Field.PRECIP_LAST_24H: (SensorDeviceClass.PRECIPITATION, UnitOfPrecipitationDepth.MILLIMETERS, M, None),
    Field.WIND_SPEED: (SensorDeviceClass.WIND_SPEED, UnitOfSpeed.KILOMETERS_PER_HOUR, M, None),
    Field.WIND_GUST: (SensorDeviceClass.WIND_SPEED, UnitOfSpeed.KILOMETERS_PER_HOUR, M, None),
    Field.WIND_DIRECTION: (SensorDeviceClass.WIND_DIRECTION, DEGREE, SensorStateClass.MEASUREMENT_ANGLE, None),
    Field.VISIBILITY: (SensorDeviceClass.DISTANCE, UnitOfLength.KILOMETERS, M, None),
    Field.CLOUD_COVER: (None, PERCENTAGE, M, None),
    Field.SNOW_DEPTH: (SensorDeviceClass.DISTANCE, UnitOfLength.CENTIMETERS, M, None),
}


async def async_setup_entry(
    hass: HomeAssistant, entry: BellweatherConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    for coordinator in entry.runtime_data.locations.values():
        entities: list[SensorEntity] = [BellweatherSensor(coordinator, field) for field in SENSORS]
        entities.append(LastPrecipitationSensor(coordinator))
        entities.append(SourceStatusSensor(coordinator))
        entities += [NowcastSensor(coordinator, kind) for kind in NOWCAST_SENSORS]
        entities.append(NowcastCheckSensor(coordinator))
        entities += [ForecastSkillSensor(coordinator.forecast, kind) for kind in SKILL_SENSORS]
        async_add_entities(entities, config_subentry_id=coordinator.subentry_id)


class BellweatherSensor(BellweatherFieldEntity, SensorEntity):
    def __init__(self, coordinator: BellweatherCoordinator, field: Field) -> None:
        super().__init__(coordinator, field)
        device_class, unit, state_class, options = SENSORS[field]
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = unit
        self._attr_state_class = state_class
        self._attr_options = options

    @property
    def native_value(self):
        choice = self.choice
        return choice.reading.value if choice else None


class LastPrecipitationSensor(BellweatherFieldEntity, RestoreSensor):
    """When precipitation was last seen ("dry since"). Survives restarts."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: BellweatherCoordinator) -> None:
        super().__init__(coordinator, Field.LAST_PRECIP)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state not in ("unknown", "unavailable"):
            when = dt_util.parse_datetime(last.state)
            if when is not None:
                self.coordinator.restore_last_precip(when)

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success and self.choice is not None

    @property
    def native_value(self) -> datetime | None:
        choice = self.choice
        return dt_util.parse_datetime(str(choice.reading.value)) if choice else None


class SourceStatusSensor(BellweatherEntity, SensorEntity):
    """How many of the enabled sources answered on the last update; the
    attributes say when each one last worked and why it failed."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: BellweatherCoordinator) -> None:
        super().__init__(coordinator, "sources_working")

    @property
    def native_value(self) -> int:
        return sum(1 for st in self.coordinator.status.values() if st.last_error is None and st.last_success)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"enabled": len(self.coordinator.sources), "sources": self.coordinator.source_status()}


# --- nowcast -----------------------------------------------------------------

NOWCAST_SENSORS = {
    "precip_next_hour": (SensorDeviceClass.PRECIPITATION, UnitOfPrecipitationDepth.MILLIMETERS),
    "precip_starts_in": (SensorDeviceClass.DURATION, UnitOfTime.MINUTES),
    "precip_stops_in": (SensorDeviceClass.DURATION, UnitOfTime.MINUTES),
}


def wet_now(coordinator: BellweatherCoordinator) -> bool:
    choice = (coordinator.data or {}).get(Field.RAIN_NOW)
    return bool(choice and choice.reading.value)


class NowcastSensor(BellweatherEntity, SensorEntity):
    """Precipitation in the next hour, and when it starts or stops (radar
    nowcast, up to two hours). Unknown means: no change within the horizon,
    or no nowcast for this location."""

    def __init__(self, coordinator: BellweatherCoordinator, kind: str) -> None:
        super().__init__(coordinator, kind)
        self.kind = kind
        self._attr_device_class, self._attr_native_unit_of_measurement = NOWCAST_SENSORS[kind]
        if kind == "precip_next_hour":
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.nowcast is not None

    @property
    def native_value(self) -> float | int | None:
        cast = self.coordinator.nowcast
        if cast is None:
            return None
        if self.kind == "precip_next_hour":
            return cast.next_hour_mm()
        if self.kind == "precip_starts_in":
            return cast.starts_in(wet_now(self.coordinator))
        return cast.stops_in(wet_now(self.coordinator))

    @property
    def attribution(self) -> str | None:
        cast = self.coordinator.nowcast
        return cast.attribution if cast else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        cast = self.coordinator.nowcast
        if cast is None:
            return None
        attrs: dict[str, Any] = {
            "method": cast.method, "issued": cast.issued.isoformat(), "horizon_minutes": cast.horizon,
        }
        if self.kind == "precip_next_hour":
            attrs["motion_kmh"] = cast.motion_kmh
            attrs["motion_towards"] = cast.motion_towards
            attrs["steps"] = [{"minutes": st.minutes, "rate": st.rate, "probability": st.probability} for st in cast.steps]
        return attrs


class NowcastCheckSensor(BellweatherEntity, SensorEntity):
    """How often the radar nowcast was right in the last 7 days: the hit
    rate (CSI, %) 30 minutes ahead, next to "no change"."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: BellweatherCoordinator) -> None:
        super().__init__(coordinator, "nowcast_hit_rate")

    def _check(self) -> dict[int, dict[str, float | int]]:
        radar = next((x for x in self.coordinator.sources if x.key == "rainviewer"), None)
        events = getattr(radar, "checks", []) if radar else []
        return nc.check(events, dt_util.utcnow())

    @property
    def native_value(self) -> float | None:
        value = self._check().get(30, {}).get("nowcast")
        return None if value is None or value != value else round(float(value) * 100)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"by_lead_minutes": self._check(), "days": 7}


# --- forecast skill ------------------------------------------------------------

SKILL_SENSORS = {
    "forecast_temperature_error": UnitOfTemperature.CELSIUS,
    "forecast_wind_error": UnitOfSpeed.KILOMETERS_PER_HOUR,
    "forecast_precip_hit_rate": PERCENTAGE,
}


class ForecastSkillSensor(CoordinatorEntity[ForecastCoordinator], SensorEntity):
    """The blend's own track record over the last 30 days, scored out of
    sample (each day with corrections learned only from the days before),
    with every member's figure next to it."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: ForecastCoordinator, kind: str) -> None:
        super().__init__(coordinator)
        self.kind = kind
        self._attr_translation_key = kind
        self._attr_native_unit_of_measurement = SKILL_SENSORS[kind]
        self._attr_suggested_display_precision = 0 if kind == "forecast_precip_hit_rate" else 2
        self._attr_unique_id = f"{coordinator.now.subentry_id}_{kind}"
        self._attr_device_info = location_device(coordinator.now)

    @property
    def available(self) -> bool:
        return self.coordinator.engine.verification.updated is not None

    @property
    def native_value(self) -> float | None:
        v = self.coordinator.engine.verification
        if self.kind == "forecast_precip_hit_rate":
            csi = v.wet.csi.get("blend") if v.wet else None
            return None if csi is None or csi != csi else round(csi * 100)
        score = v.temperature if self.kind == "forecast_temperature_error" else v.wind
        return None if score is None or score.blend_mae is None else round(score.blend_mae, 2)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        v = self.coordinator.engine.verification
        corr = v.corrections
        attrs: dict[str, Any] = {
            "corrected": bool(corr.temperature or corr.wind or corr.dew_point or corr.pressure), "verification_days": corr.days,
            "verification_source": corr.source, "airport_archive": v.airport,
            "updated": v.updated.isoformat() if v.updated else None,
        }
        if self.kind == "forecast_precip_hit_rate":
            if v.wet:
                attrs.update(members={k: round(x * 100) for k, x in v.wet.csi.items() if x == x},
                             wet_hours=v.wet.wet_hours, hours=v.wet.hours)
        else:
            score = v.temperature if self.kind == "forecast_temperature_error" else v.wind
            if score:
                attrs.update(
                    plain_mean=None if score.plain_mean_mae is None else round(score.plain_mean_mae, 2),
                    members={k: round(x, 2) for k, x in sorted(score.member_mae.items(), key=lambda kv: kv[1])},
                    hours=score.hours, days=score.days,
                )
        return attrs

