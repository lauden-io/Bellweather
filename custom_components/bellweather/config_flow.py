"""Setup and settings.

One Bellweather entry holds any number of locations. The entry's own
options are the decisions for the whole home: how precisely services learn
a location, which services are asked, the airport archive. Each location is
a config subentry ("Add location") with its own name, place, height, own
sensors, forecast members, ignored stations and limits; it becomes its own
device. A location's place is found with an optional search (only the
typed text is sent: place names to Open-Meteo's place search, addresses to
the address search, OpenStreetMap's Nominatim unless changed) and
fine-tuned on Home Assistant's own map.

Settings are menus. Each page's button goes back to the menu and keeps what
was entered; nothing is saved before "Save and close".
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol  # Home Assistant 2026.10+ maps this to probatio
from aiohttp import DummyCookieJar

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryData,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.const import UnitOfPressure
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.data_entry_flow import section
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.util.unit_conversion import PressureConverter
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    LocationSelector,
    LocationSelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_ADDRESS_SERVER,
    CONF_ELEVATION,
    CONF_EXCLUDED_STATIONS,
    CONF_FORECAST_MEMBERS,
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
    CONF_NAME,
    CONF_PLACE,
    CONF_PRECISION,
    CONF_SEARCH,
    CONF_SOURCES,
    CONF_USE_ARCHIVE,
    CONF_WEATHER_ENTITY,
    DOMAIN,
    MAX_NAME,
    NAME,
    SUBENTRY_LOCATION,
)
from .core.fusion import DEFAULT_LIMITS
from .core.geocode import ADDRESS_SERVER, OSM, Place, address_server, clean_query, find
from .core.model import Location
from .core.privacy import DEFAULT_PRECISION, Precision, shared_point
from .core.safety import finite
from .core.sources import DEFAULT_SOURCES, EXTRA_KEYS, SOURCES
from .core.sources.open_meteo import elevations

PRECISION_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=[p.value for p in Precision],
        translation_key=CONF_PRECISION,
        mode=SelectSelectorMode.LIST,
    )
)
SOURCES_SELECTOR = SelectSelector(
    SelectSelectorConfig(options=[*SOURCES, *EXTRA_KEYS], translation_key=CONF_SOURCES, multiple=True)
)
MAP = LocationSelector(LocationSelectorConfig(radius=False))
HEIGHT = NumberSelector(NumberSelectorConfig(min=-500, max=9000, step=1, unit_of_measurement="m", mode=NumberSelectorMode.BOX))
KM = NumberSelector(NumberSelectorConfig(min=1, max=100, step=1, unit_of_measurement="km", mode=NumberSelectorMode.BOX))
METRES = NumberSelector(NumberSelectorConfig(min=0, max=3000, step=10, unit_of_measurement="m", mode=NumberSelectorMode.BOX))


def _sensor(device_class: str | list[str], domain: str = "sensor") -> tuple[str | list[str], str]:
    return device_class, domain


LOCAL_SENSORS: dict[str, tuple[str | list[str], str]] = {
    CONF_LOCAL_TEMPERATURE: _sensor("temperature"),
    CONF_LOCAL_HUMIDITY: _sensor("humidity"),
    CONF_LOCAL_PRESSURE: _sensor(["atmospheric_pressure", "pressure"]),
    CONF_LOCAL_RAIN_RATE: _sensor("precipitation_intensity"),
    # Rain detectors are usually moisture binary sensors; leak sensors share
    # that class, so the user picks the right one by name.
    CONF_LOCAL_RAIN_STATE: _sensor("moisture", "binary_sensor"),
    CONF_LOCAL_WIND_SPEED: _sensor("wind_speed"),
    CONF_LOCAL_WIND_GUST: _sensor("wind_speed"),
    CONF_LOCAL_WIND_DIRECTION: _sensor("wind_direction"),
    CONF_LOCAL_VISIBILITY: _sensor("distance"),
    CONF_LOCAL_SNOW_DEPTH: _sensor("distance"),
}


def not_barometric(hass: Any) -> list[str]:
    """Sensors of Home Assistant's generic "pressure" class that are not air
    pressure: blood pressure from a phone's health data, tyres, water. Only
    the generic class is checked ("atmospheric_pressure" always stays); a
    sensor stays when its value is a plausible air pressure (300-1100 hPa).
    One that has no value right now is left out; it can be picked later."""
    out: list[str] = []
    for state in hass.states.async_all("sensor"):
        if state.attributes.get("device_class") != "pressure":
            continue
        value, unit = finite(state.state), state.attributes.get("unit_of_measurement")
        hpa = None
        if value is not None and unit:
            try:
                hpa = finite(PressureConverter.convert(value, unit, UnitOfPressure.HPA))
            except (HomeAssistantError, ValueError, OverflowError):
                hpa = None
        if hpa is None or not 300 <= hpa <= 1100:
            out.append(state.entity_id)
    return out


def _rounded(point: Any) -> tuple[float, float] | None:
    try:
        return round(float(point["latitude"]), 3), round(float(point["longitude"]), 3)
    except (KeyError, TypeError, ValueError):
        return None


def limit_sections(current: dict[str, Any]) -> dict[Any, Any]:
    """One section per group; precipitation open, the rest folded."""
    fields: dict[Any, Any] = {}
    for i, (group, default) in enumerate(DEFAULT_LIMITS.items()):
        inner: dict[Any, Any] = {}
        for suffix, value, selector in (
            ("station_km", default.station_km, KM),
            ("airport_km", default.airport_km, KM),
            ("height_m", default.height_m, METRES),
        ):
            if value is None:
                continue  # this group does not check height
            inner[vol.Optional(suffix, default=current.get(f"{group.value}_{suffix}", value))] = selector
        fields[vol.Required(f"limits_{group.value}")] = section(vol.Schema(inner), {"collapsed": i > 0})
    return fields


def flat_limits(user_input: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for group in DEFAULT_LIMITS:
        for suffix, value in (user_input.get(f"limits_{group.value}") or {}).items():
            out[f"{group.value}_{suffix}"] = value
    return out


class _PlaceSteps:
    """Name, optional place search, map; shared by the first setup, "Add
    location" and a location's Reconfigure."""

    hass: Any

    def _init_place(self) -> None:
        self._place_name = ""
        self._place_point: dict[str, float] = {"latitude": self.hass.config.latitude, "longitude": self.hass.config.longitude}
        self._place_elevation: float | None = None
        self._places: list[Place] = []

    # The subclasses say how precise the shared point is, which points are
    # taken, and what happens when the place is chosen.
    def _precision(self) -> Precision:
        raise NotImplementedError

    def _address_server(self) -> str | None:
        return ADDRESS_SERVER

    def _editing(self) -> bool:
        """True while a location's settings are changed (Reconfigure): the
        place steps then lead back to its menu, not to the end."""
        return False

    def _taken(self) -> set[tuple[float, float]]:
        raise NotImplementedError

    async def _place_finished(self) -> Any:
        raise NotImplementedError

    def _location_data(self) -> dict[str, Any]:
        return {CONF_LOCATION: dict(self._place_point), CONF_ELEVATION: self._place_elevation}

    async def _with_http(self, call):
        """The running entry's HTTP layer when there is one; otherwise a
        session of its own without cookies, detached again afterwards (Home
        Assistant owns the connection pool, so it is detached, not closed)."""
        from .coordinator import HaHttp

        runtime = getattr(self, "_runtime", lambda: None)()
        if runtime is not None:
            return await call(runtime.http)
        session = async_create_clientsession(self.hass, auto_cleanup=False, cookie_jar=DummyCookieJar())
        try:
            return await call(HaHttp(session))
        finally:
            session.detach()

    async def async_step_location(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            self._place_name = " ".join(str(user_input.get(CONF_NAME, "")).split())[:MAX_NAME]
            before = _rounded(self._place_point)
            if _rounded(user_input.get(CONF_LOCATION)) is not None:
                self._place_point = {k: float(user_input[CONF_LOCATION][k]) for k in ("latitude", "longitude")}
            moved = _rounded(self._place_point) != before
            query = clean_query(user_input.get(CONF_SEARCH) or "")
            if not self._place_name:
                errors[CONF_NAME] = "name_required"
            elif query:
                language = self.hass.config.language or "en"
                try:
                    server = self._address_server()
                    self._places = await self._with_http(lambda http: find(http, query, language, server))
                except Exception:  # noqa: BLE001 - shown as a form error; the details could hold the query
                    errors[CONF_SEARCH] = "search_failed"
                else:
                    if self._places:
                        return await self.async_step_pick()
                    errors[CONF_SEARCH] = "no_match"
            else:
                if moved:
                    self._place_elevation = None  # looked up for the new pin in the next step
                return await self.async_step_place()
        schema = vol.Schema({
            vol.Required(CONF_NAME, description={"suggested_value": self._place_name}): TextSelector(),
            vol.Optional(CONF_SEARCH): TextSelector(),
            vol.Required(CONF_LOCATION, default=self._place_point): MAP,
        })
        return self.async_show_form(step_id="location", data_schema=schema, errors=errors, last_step=False)

    async def async_step_pick(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            choice = str(user_input.get(CONF_PLACE, "none"))
            if choice.isdigit() and int(choice) < len(self._places):
                place = self._places[int(choice)]
                self._place_point = {"latitude": place.latitude, "longitude": place.longitude}
                self._place_elevation = place.elevation
                return await self.async_step_place()
            return await self.async_step_location()
        options = [SelectOptionDict(value=str(i), label=p.label) for i, p in enumerate(self._places)]
        options.append(SelectOptionDict(value="none", label="None of these"))
        schema = vol.Schema({
            vol.Required(CONF_PLACE, default="0"): SelectSelector(
                SelectSelectorConfig(options=options, mode=SelectSelectorMode.LIST, translation_key=CONF_PLACE)
            ),
        })
        osm = any(p.source == OSM for p in self._places)
        return self.async_show_form(
            step_id="pick", data_schema=schema, last_step=False,
            description_placeholders={"attribution": "Address data © OpenStreetMap contributors (ODbL), via Nominatim."
                                      if osm else "Places from GeoNames, via Open-Meteo."},
        )

    async def async_step_place(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            picked = _rounded(user_input.get(CONF_LOCATION))
            if picked is None:
                errors["base"] = "invalid_location"
            elif picked in self._taken():
                errors["base"] = "already_configured"
            else:
                self._place_point = {k: float(user_input[CONF_LOCATION][k]) for k in ("latitude", "longitude")}
                height = finite(user_input.get(CONF_ELEVATION))
                self._place_elevation = height if height is not None and -500 <= height <= 9000 else None
                return await self._place_finished()
        if self._place_elevation is None:
            self._place_elevation = await self._lookup_elevation()
        schema = vol.Schema({
            vol.Required(CONF_LOCATION, default=self._place_point): MAP,
            vol.Optional(CONF_ELEVATION, description={"suggested_value": self._place_elevation}): HEIGHT,
        })
        return self.async_show_form(
            step_id="place", data_schema=schema, errors=errors, description_placeholders={"name": self._place_name},
            last_step=not self._editing(),
        )

    async def _lookup_elevation(self) -> float | None:
        """Home Assistant's own height for its home; otherwise the elevation
        service's height for the shared, rounded point (never the pin)."""
        home = _rounded({"latitude": self.hass.config.latitude, "longitude": self.hass.config.longitude})
        if _rounded(self._place_point) == home and self.hass.config.elevation is not None:
            return float(self.hass.config.elevation)
        lat, lon = shared_point(Location(self._place_point["latitude"], self._place_point["longitude"]), self._precision())
        try:
            values = await self._with_http(lambda http: elevations(http, [(lat, lon)]))
        except Exception:  # noqa: BLE001 - the user can type the height
            return None
        value = values[0] if values else None
        return None if value is None else round(value)


class BellweatherConfigFlow(_PlaceSteps, ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._shared: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        self._init_place_once()
        if user_input is not None:
            self._shared = {
                CONF_PRECISION: user_input[CONF_PRECISION],
                CONF_SOURCES: list(user_input[CONF_SOURCES]),
                CONF_USE_ARCHIVE: False,
            }
            self._place_name = "Home"
            return await self.async_step_location()
        schema = vol.Schema({
            vol.Required(CONF_PRECISION, default=DEFAULT_PRECISION.value): PRECISION_SELECTOR,
            vol.Required(CONF_SOURCES, default=DEFAULT_SOURCES): SOURCES_SELECTOR,
        })
        return self.async_show_form(step_id="user", data_schema=schema, last_step=False)

    def _init_place_once(self) -> None:
        if not hasattr(self, "_place_point"):
            self._init_place()

    def _precision(self) -> Precision:
        return Precision(self._shared.get(CONF_PRECISION, DEFAULT_PRECISION.value))

    def _taken(self) -> set[tuple[float, float]]:
        return set()

    async def _place_finished(self) -> ConfigFlowResult:
        return self.async_create_entry(
            title=NAME,
            data={},
            options=self._shared,
            subentries=[
                ConfigSubentryData(
                    data=self._location_data(), subentry_type=SUBENTRY_LOCATION, title=self._place_name, unique_id=None
                )
            ],
        )

    @classmethod
    @callback
    def async_get_supported_subentry_types(cls, config_entry: ConfigEntry) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_LOCATION: LocationSubentryFlow}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return BellweatherOptionsFlow()


class LocationSubentryFlow(_PlaceSteps, ConfigSubentryFlow):
    """Add a location, or change one (Reconfigure on its row)."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._title = ""
        self._loaded = False

    def _precision(self) -> Precision:
        entry = self._get_entry()
        return Precision({**entry.data, **entry.options}.get(CONF_PRECISION, DEFAULT_PRECISION.value))

    def _runtime(self):
        return getattr(self._get_entry(), "runtime_data", None)

    def _address_server(self) -> str | None:
        options = {**self._get_entry().data, **self._get_entry().options}
        return address_server(options.get(CONF_ADDRESS_SERVER, ADDRESS_SERVER))

    def _editing(self) -> bool:
        return self.source == "reconfigure"

    def _taken(self) -> set[tuple[float, float]]:
        mine = self.context.get("subentry_id") if self.source == "reconfigure" else None
        out: set[tuple[float, float]] = set()
        for sid, sub in self._get_entry().subentries.items():
            if sid != mine and sub.subentry_type == SUBENTRY_LOCATION:
                point = _rounded(sub.data.get(CONF_LOCATION))
                if point is not None:
                    out.add(point)
        return out

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        self._init_place()
        return await self.async_step_location()

    async def _place_finished(self) -> SubentryFlowResult:
        if self.source == "reconfigure":
            self._data.update(self._location_data())
            self._title = self._place_name
            return await self.async_step_reconfigure()
        return self.async_create_entry(title=self._place_name, data=self._location_data())

    # --- Reconfigure: a menu of this location's settings ---------------------

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        if not self._loaded:
            sub = self._get_reconfigure_subentry()
            self._data, self._title, self._loaded = dict(sub.data), sub.title, True
            self._init_place()
            self._place_name = sub.title
            if _rounded(sub.data.get(CONF_LOCATION)) is not None:
                self._place_point = {k: float(sub.data[CONF_LOCATION][k]) for k in ("latitude", "longitude")}
            self._place_elevation = finite(sub.data.get(CONF_ELEVATION))
        return self.async_show_menu(
            step_id="reconfigure",
            menu_options=["location", "sensors", "members", "stations", "save"],
            description_placeholders={"name": self._title},
        )

    def _own_entities(self) -> list[str]:
        return [e.entity_id for e in er.async_entries_for_config_entry(er.async_get(self.hass), self._entry_id)]

    def _keep(self, keys: list[str], user_input: dict[str, Any]) -> None:
        """Fields left empty are removed from the location."""
        for key in keys:
            value = user_input.get(key)
            if value in (None, "", []):
                self._data.pop(key, None)
            else:
                self._data[key] = value

    async def async_step_sensors(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        keys = [*LOCAL_SENSORS, CONF_WEATHER_ENTITY]
        if user_input is not None:
            self._keep(keys, user_input)
            return await self.async_step_reconfigure()
        own = self._own_entities()
        not_air = not_barometric(self.hass)
        fields: dict[Any, Any] = {}
        for key, (device_class, domain) in LOCAL_SENSORS.items():
            excluded = [*own, *not_air] if key == CONF_LOCAL_PRESSURE else own
            selector = EntitySelector(EntitySelectorConfig(domain=domain, device_class=device_class, exclude_entities=excluded))
            fields[vol.Optional(key, description={"suggested_value": self._data.get(key)})] = selector
        fields[vol.Optional(CONF_WEATHER_ENTITY, description={"suggested_value": self._data.get(CONF_WEATHER_ENTITY)})] = (
            EntitySelector(EntitySelectorConfig(domain="weather", exclude_entities=own))
        )
        return self.async_show_form(step_id="sensors", data_schema=vol.Schema(fields), description_placeholders={"name": self._title})

    async def async_step_members(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        if user_input is not None:
            self._keep([CONF_FORECAST_MEMBERS], user_input)
            return await self.async_step_reconfigure()
        schema = vol.Schema({
            vol.Optional(CONF_FORECAST_MEMBERS, description={"suggested_value": self._data.get(CONF_FORECAST_MEMBERS)}): (
                EntitySelector(EntitySelectorConfig(domain="weather", multiple=True, exclude_entities=self._own_entities()))
            ),
        })
        return self.async_show_form(step_id="members", data_schema=schema, description_placeholders={"name": self._title})

    async def async_step_stations(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        if user_input is not None:
            self._data[CONF_EXCLUDED_STATIONS] = [str(s) for s in user_input.get(CONF_EXCLUDED_STATIONS) or []][:100]
            self._data[CONF_LIMITS] = flat_limits(user_input)
            return await self.async_step_reconfigure()
        excluded = list(self._data.get(CONF_EXCLUDED_STATIONS) or [])
        known: dict[str, str] = {}
        runtime = getattr(self._get_entry(), "runtime_data", None)
        location = runtime.locations.get(self.context.get("subentry_id", "")) if runtime is not None else None
        if location is not None:
            known.update(location.stations)
        for sid in excluded:
            known.setdefault(sid, sid)
        options = [SelectOptionDict(value=sid, label=label) for sid, label in sorted(known.items(), key=lambda kv: kv[1])]
        fields: dict[Any, Any] = {
            vol.Optional(CONF_EXCLUDED_STATIONS, default=excluded): SelectSelector(
                SelectSelectorConfig(options=options, multiple=True, custom_value=True, mode=SelectSelectorMode.DROPDOWN)
            ),
        }
        fields.update(limit_sections(self._data.get(CONF_LIMITS) or {}))
        return self.async_show_form(step_id="stations", data_schema=vol.Schema(fields), description_placeholders={"name": self._title})

    async def async_step_save(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return self.async_update_and_abort(
            self._get_entry(), self._get_reconfigure_subentry(), title=self._title, data=self._data
        )


class BellweatherOptionsFlow(OptionsFlow):
    """The shared settings, as a menu; nothing is saved before "Save"."""

    def __init__(self) -> None:
        self._options: dict[str, Any] | None = None

    def _current(self) -> dict[str, Any]:
        if self._options is None:
            self._options = {
                CONF_PRECISION: DEFAULT_PRECISION.value,
                CONF_SOURCES: list(DEFAULT_SOURCES),
                CONF_USE_ARCHIVE: False,
                CONF_ADDRESS_SERVER: ADDRESS_SERVER,
                **self.config_entry.data,
                **self.config_entry.options,
            }
        return self._options

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        self._current()
        return self.async_show_menu(step_id="init", menu_options=["privacy", "sources", "search", "archive", "save"])

    async def async_step_search(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            text = str(user_input.get(CONF_ADDRESS_SERVER) or "").strip()
            server = address_server(text)
            if text and server is None:
                errors[CONF_ADDRESS_SERVER] = "invalid_server"
            else:
                self._current()[CONF_ADDRESS_SERVER] = server or ""
                return await self.async_step_init()
        current = self._current().get(CONF_ADDRESS_SERVER, ADDRESS_SERVER)
        schema = vol.Schema({
            vol.Optional(CONF_ADDRESS_SERVER, description={"suggested_value": current}): TextSelector(
                TextSelectorConfig(type=TextSelectorType.URL)
            ),
        })
        return self.async_show_form(step_id="search", data_schema=schema, errors=errors)

    async def async_step_privacy(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._current()[CONF_PRECISION] = user_input[CONF_PRECISION]
            return await self.async_step_init()
        schema = vol.Schema({vol.Required(CONF_PRECISION, default=self._current()[CONF_PRECISION]): PRECISION_SELECTOR})
        return self.async_show_form(step_id="privacy", data_schema=schema)

    async def async_step_sources(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._current()[CONF_SOURCES] = list(user_input[CONF_SOURCES])
            return await self.async_step_init()
        schema = vol.Schema({vol.Required(CONF_SOURCES, default=self._current()[CONF_SOURCES]): SOURCES_SELECTOR})
        return self.async_show_form(step_id="sources", data_schema=schema)

    async def async_step_archive(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._current()[CONF_USE_ARCHIVE] = bool(user_input[CONF_USE_ARCHIVE])
            return await self.async_step_init()
        schema = vol.Schema({vol.Required(CONF_USE_ARCHIVE, default=bool(self._current()[CONF_USE_ARCHIVE])): BooleanSelector()})
        return self.async_show_form(step_id="archive", data_schema=schema)

    async def async_step_save(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_create_entry(data=self._current())

