"""Bellweather: the weather here and now, measured; and the best forecast
for your home, scored against what was measured.

One entry holds every location. Each location (a config subentry) gets its
own coordinators and its own device; all of them share one HTTP layer with
a short-lived cache, so locations near each other ask for the radar tiles,
the radar index and the daily snow map once, not once each.
"""

from __future__ import annotations

import asyncio

from aiohttp import DummyCookieJar

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.storage import Store

from .const import DOMAIN, SUBENTRY_LOCATION
from .coordinator import BellweatherConfigEntry, BellweatherCoordinator, BellweatherData, HaHttp
from .core.cache import CachingHttp
from .forecast_coordinator import STORAGE_VERSION, ForecastCoordinator

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR, Platform.WEATHER]
FIRST_FORECAST_STAGGER = 15.0  # seconds between the locations' first forecasts


async def async_setup_entry(hass: HomeAssistant, entry: BellweatherConfigEntry) -> bool:
    # Its own session without a cookie jar: no service can set a cookie and
    # recognise this installation across IP address changes.
    session = async_create_clientsession(hass, cookie_jar=DummyCookieJar())
    data = BellweatherData(session=session, http=CachingHttp(HaHttp(session)), locations={})
    for sub in entry.subentries.values():
        if sub.subentry_type != SUBENTRY_LOCATION:
            continue
        coordinator = BellweatherCoordinator(hass, entry, sub, data.http)
        forecast = ForecastCoordinator(hass, entry, coordinator)
        coordinator.forecast = forecast
        await forecast.async_load()
        data.locations[sub.subentry_id] = coordinator
    entry.runtime_data = data
    # Created during setup, the session is detached by Home Assistant when the
    # entry unloads (or setup fails); it must not be closed by hand.
    await asyncio.gather(*(c.async_config_entry_first_refresh() for c in data.locations.values()))
    entry.async_on_unload(entry.add_update_listener(_entry_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # The forecast must not hold the observations back: it starts in the
    # background, and a failure only leaves it empty until the next hourly try.
    # Locations start a little apart, so their requests do not arrive at the
    # services all at once.
    for i, coordinator in enumerate(data.locations.values()):
        entry.async_create_background_task(
            hass, _first_forecast(coordinator, i * FIRST_FORECAST_STAGGER), f"{DOMAIN} {coordinator.subentry_id} first forecast"
        )
    return True


async def _first_forecast(coordinator: BellweatherCoordinator, delay: float) -> None:
    if delay:
        await asyncio.sleep(delay)
    await coordinator.forecast.async_refresh()


async def async_unload_entry(hass: HomeAssistant, entry: BellweatherConfigEntry) -> bool:
    data = entry.runtime_data
    for sid, coordinator in data.locations.items():
        # A location that was just removed keeps no history.
        if sid in entry.subentries and coordinator.forecast is not None:
            await coordinator.forecast.store.async_save(coordinator.forecast.engine.dump())
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: BellweatherConfigEntry) -> None:
    """Delete every location's stored forecast history with the entry."""
    for sid in entry.subentries:
        await Store(hass, STORAGE_VERSION, f"{DOMAIN}.{sid}").async_remove()


async def _entry_updated(hass: HomeAssistant, entry: BellweatherConfigEntry) -> None:
    """Settings saved, or a location added, changed or removed: forget the
    history of removed locations, then reload."""
    data = getattr(entry, "runtime_data", None)
    if data is not None:
        for sid in set(data.locations) - set(entry.subentries):
            await Store(hass, STORAGE_VERSION, f"{DOMAIN}.{sid}").async_remove()
    await hass.config_entries.async_reload(entry.entry_id)
