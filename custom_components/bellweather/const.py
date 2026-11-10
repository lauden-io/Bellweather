"""Constants for the Home Assistant side."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "bellweather"
NAME = "Bellweather"
VERSION = "0.1.0"
PROJECT_URL = "https://github.com/lauden-io/Bellweather"
# MET Norway requires contact details in the User-Agent: an e-mail address, a
# website, or a public repository where issues can be opened. The project's
# repository is that contact; nothing about the installation is sent.
USER_AGENT = f"bellweather/{VERSION} (Home Assistant custom integration; {PROJECT_URL})"
UPDATE_INTERVAL = timedelta(minutes=5)

CONF_LOCATION = "location"
CONF_ELEVATION = "elevation"  # metres; per location
CONF_NAME = "name"  # the location's name: the device and the start of its entity ids
CONF_SEARCH = "search"  # optional place search in the location step (only the typed text is sent)
CONF_PLACE = "place"  # the chosen search result
CONF_ADDRESS_SERVER = "address_server"  # Nominatim-compatible server for street addresses; empty: off
MAX_NAME = 60
SUBENTRY_LOCATION = "location"  # config subentry type: one per location
CONF_PRECISION = "precision"
CONF_SOURCES = "sources"
CONF_WEATHER_ENTITY = "weather_entity"
CONF_EXCLUDED_STATIONS = "excluded_stations"
CONF_LIMITS = "limits"  # collapsed section with per-group distance and height limits

# Optional local sensors that win over every public source.
CONF_LOCAL_RAIN_RATE = "local_rain_rate"
CONF_LOCAL_RAIN_STATE = "local_rain_state"
CONF_LOCAL_WIND_SPEED = "local_wind_speed"
CONF_LOCAL_WIND_GUST = "local_wind_gust"
CONF_LOCAL_WIND_DIRECTION = "local_wind_direction"
CONF_LOCAL_VISIBILITY = "local_visibility"
CONF_LOCAL_SNOW_DEPTH = "local_snow_depth"
CONF_LOCAL_TEMPERATURE = "local_temperature"
CONF_LOCAL_HUMIDITY = "local_humidity"
CONF_LOCAL_PRESSURE = "local_pressure"

# Forecast
CONF_FORECAST_MEMBERS = "forecast_members"  # the user's weather entities that join the blend
CONF_USE_ARCHIVE = "use_airport_archive"  # off by default: one more service learns the nearest airport
FORECAST_INTERVAL = timedelta(hours=1)
VERIFY_INTERVAL = timedelta(hours=24)
MET_NOWCAST = "met_nowcast"  # a "source" key: MET Norway's nowcast, Nordic countries only
