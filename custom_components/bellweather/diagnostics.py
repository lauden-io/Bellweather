"""Diagnostics download for bug reports.

Locations are numbered, not named, and their places are removed. Station
names and distances are removed too: three distances to known stations
would give a location away. Distances are shown as a band instead. For
values measured by a station, an airport or your own sensor, the value and
its exact time are left out as well: matched against public archives they
would name the station.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import CONF_ELEVATION, CONF_EXCLUDED_STATIONS, CONF_LOCATION, VERSION
from .coordinator import BellweatherConfigEntry, BellweatherCoordinator

TO_REDACT = {CONF_LOCATION, "latitude", "longitude", CONF_ELEVATION, CONF_EXCLUDED_STATIONS}
BANDS = ((0.5, "at the point"), (5, "< 5 km"), (15, "5-15 km"), (30, "15-30 km"), (50, "30-50 km"))


def _band(distance: float | None) -> str | None:
    if distance is None:
        return None
    for limit, name in BANDS:
        if distance < limit:
            return name
    return ">= 50 km"


def _location(coordinator: BellweatherCoordinator) -> dict[str, Any]:
    values: dict[str, Any] = {}
    now = dt_util.utcnow()
    for field, choice in (coordinator.data or {}).items():
        r = choice.reading
        identifying = r.kind.value in ("station", "airport", "local")
        values[field.value] = {
            "value": None if identifying else r.value,
            "source": r.source if r.kind.value != "local" else "local",
            "source_kind": r.kind.value,
            "distance": _band(r.distance_km),
            "age_minutes": max(0, int((now - r.observed).total_seconds() // 900) * 15),
            "candidate_count": choice.candidates,
        }
    return {
        "settings": async_redact_data(dict(coordinator.subentry.data), TO_REDACT),
        "elevation_known": coordinator.location.elevation is not None,
        "sources": coordinator.source_status(),
        "values": values,
    }


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: BellweatherConfigEntry) -> dict[str, Any]:
    data = entry.runtime_data
    return {
        "version": VERSION,
        "options": async_redact_data(dict(entry.options), TO_REDACT),
        "cache": data.http.stats(),
        "locations": {f"location {i}": _location(c) for i, c in enumerate(data.locations.values(), start=1)},
    }
