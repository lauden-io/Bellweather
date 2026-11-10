"""All sources, by key, and how to build them from the settings."""

from __future__ import annotations

from ..fusion import DEFAULT_LIMITS, Group, GroupLimits
from .base import Http, Source
from .esoh import EsohSource
from .ims import ImsSnowSource
from .metar import MetarSource
from .open_meteo import OpenMeteoSource, elevations
from .rainviewer import RainViewerSource

SOURCES: dict[str, type[Source]] = {
    RainViewerSource.key: RainViewerSource,
    EsohSource.key: EsohSource,
    MetarSource.key: MetarSource,
    OpenMeteoSource.key: OpenMeteoSource,
    ImsSnowSource.key: ImsSnowSource,
}

# Keys the user can switch off. "met_nowcast" is not a reading source: it is
# MET Norway's nowcast, asked only for points in the Nordic area.
EXTRA_KEYS = ["met_nowcast"]
DEFAULT_SOURCES = ["rainviewer", "esoh", "metar", "open_meteo", "ims", "met_nowcast"]


def make_sources(keys: list[str], http: Http, limits: dict[Group, GroupLimits] | None = None) -> list[Source]:
    """The enabled sources. Station and airport boxes grow with the largest
    distance limit (pressure excepted, which reaches far but needs only one
    station). Station heights are looked up only when Open-Meteo is enabled,
    because that lookup goes to Open-Meteo."""
    limits = limits or DEFAULT_LIMITS
    others = [lim for group, lim in limits.items() if group is not Group.PRESSURE]
    station_km = max(lim.station_km for lim in others)
    airport_km = max(lim.airport_km for lim in others)
    out: list[Source] = []
    for key in keys:
        if key == EsohSource.key:
            lookup = (lambda points: elevations(http, points)) if OpenMeteoSource.key in keys else None
            out.append(EsohSource(elevation_lookup=lookup, radius_km=station_km))
        elif key == MetarSource.key:
            out.append(MetarSource(radius_km=airport_km))
        elif key in SOURCES:
            out.append(SOURCES[key]())
    return out


__all__ = ["DEFAULT_SOURCES", "EXTRA_KEYS", "SOURCES", "Http", "Source", "make_sources"]
