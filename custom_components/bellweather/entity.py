"""Shared entity base: every value tells where it came from."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, VERSION
from .coordinator import BellweatherCoordinator
from .core.fusion import Choice
from .core.model import Field


class BellweatherEntity(CoordinatorEntity[BellweatherCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: BellweatherCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_translation_key = key
        self._attr_unique_id = f"{coordinator.subentry_id}_{key}"
        self._attr_device_info = location_device(coordinator)


def location_device(coordinator: BellweatherCoordinator) -> DeviceInfo:
    """One device per location, named after it: its entities are then
    called, for example, weather.home and sensor.home_temperature."""
    return DeviceInfo(
        identifiers={(DOMAIN, coordinator.subentry_id)},
        name=coordinator.location_name,
        manufacturer="Bellweather",
        model="Location",
        entry_type=DeviceEntryType.SERVICE,
        sw_version=VERSION,
    )


class BellweatherFieldEntity(BellweatherEntity):
    """An entity that shows one fused field."""

    def __init__(self, coordinator: BellweatherCoordinator, field: Field) -> None:
        super().__init__(coordinator, field.value)
        self.field = field

    @property
    def choice(self) -> Choice | None:
        return (self.coordinator.data or {}).get(self.field)

    @property
    def available(self) -> bool:
        return super().available and self.choice is not None

    @property
    def attribution(self) -> str | None:
        choice = self.choice
        return choice.reading.attribution if choice else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        choice = self.choice
        if choice is None:
            return None
        r = choice.reading
        return {
            "source": r.source,
            "source_kind": r.kind.value,
            "station": r.station,
            "station_id": r.station_id,
            "distance_km": r.distance_km,
            "observed": r.observed.isoformat(),
            "candidate_count": choice.candidates,
            **(choice.details or {}),
        }
