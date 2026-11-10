"""Yes/no sensors: rain now, thunder, fog, snow on the ground."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import BellweatherConfigEntry, BellweatherCoordinator
from .core.model import Field
from .entity import BellweatherEntity, BellweatherFieldEntity

BINARY_SENSORS: dict[Field, BinarySensorDeviceClass | None] = {
    Field.RAIN_NOW: BinarySensorDeviceClass.MOISTURE,
    Field.THUNDER: None,
    Field.FOG: None,
    Field.SNOW_ON_GROUND: None,
}


async def async_setup_entry(
    hass: HomeAssistant, entry: BellweatherConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    for coordinator in entry.runtime_data.locations.values():
        entities: list[BinarySensorEntity] = [BellweatherBinarySensor(coordinator, field) for field in BINARY_SENSORS]
        entities.append(PrecipitationExpected(coordinator))
        async_add_entities(entities, config_subentry_id=coordinator.subentry_id)


class BellweatherBinarySensor(BellweatherFieldEntity, BinarySensorEntity):
    def __init__(self, coordinator: BellweatherCoordinator, field: Field) -> None:
        super().__init__(coordinator, field)
        self._attr_device_class = BINARY_SENSORS[field]

    @property
    def is_on(self) -> bool | None:
        choice = self.choice
        return bool(choice.reading.value) if choice else None


class PrecipitationExpected(BellweatherEntity, BinarySensorEntity):
    """On when the nowcast expects precipitation within the next hour."""

    _attr_device_class = BinarySensorDeviceClass.MOISTURE

    def __init__(self, coordinator: BellweatherCoordinator) -> None:
        super().__init__(coordinator, "precip_expected")

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.nowcast is not None and bool(self.coordinator.nowcast.steps)

    @property
    def is_on(self) -> bool | None:
        cast = self.coordinator.nowcast
        return None if cast is None else cast.expected_within(60)

