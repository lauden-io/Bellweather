"""Search for the location step: a place or a street address to a point.

Nothing is sent unless the user types into the search field, and then only
that text:

- a place name (a town, village or postcode) goes to Open-Meteo's place
  search (GeoNames data), which Bellweather already uses for the forecast;
- text with a house number or a comma, or a place name Open-Meteo does not
  find, goes to the address search: OpenStreetMap's Nominatim by default, or
  another Nominatim-compatible server set in the options (empty: off).

Nominatim's usage policy (https://operations.osmfoundation.org/policies/nominatim/)
is followed: only searches the user starts, never automatic or as-you-type;
at most one request per second from this installation; answers are cached;
the application is named in the User-Agent; results are shown with the
OpenStreetMap attribution; and the server can be switched or turned off in
the options without an update. A street address is found only where
OpenStreetMap has it: otherwise the result is the street, and the pin is then
moved on the map to the exact spot.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from .safety import CAP_SMALL_JSON, finite
from .sources.base import Http

URL = "https://geocoding-api.open-meteo.com/v1/search"
ADDRESS_SERVER = "https://nominatim.openstreetmap.org"
OSM = "osm"
OPEN_METEO = "open_meteo"
MAX_QUERY = 100
MAX_RESULTS = 8
MAX_LABEL = 110
MAX_SERVER = 200
ADDRESS_INTERVAL = 1.0  # seconds between two address searches (Nominatim: at most 1 per second)
_LANGUAGE = re.compile(r"^[a-z]{2}$")
_DIGIT = re.compile(r"\d")
_LETTER = re.compile(r"[^\W\d_]")


@dataclass(frozen=True)
class Place:
    name: str
    region: str  # Open-Meteo: first administrative level and country; OSM: the rest of the address
    latitude: float
    longitude: float
    elevation: float | None
    source: str = OPEN_METEO

    @property
    def label(self) -> str:
        height = f" · {self.elevation:.0f} m" if self.elevation is not None else ""
        text = f"{self.name}, {self.region}" if self.region else self.name
        return f"{text[:MAX_LABEL]}{height}"


def clean_query(text: str) -> str:
    """Trimmed, single-spaced, at most 100 characters, no control characters."""
    text = "".join(ch for ch in str(text) if ch.isprintable())
    return " ".join(text.split())[:MAX_QUERY]


def _text(value: Any, limit: int = 80) -> str:
    return clean_query(value)[:limit] if isinstance(value, str) else ""


def _language(language: Any) -> str:
    code = language.split("-")[0].lower() if isinstance(language, str) else "en"
    return code if _LANGUAGE.match(code) else "en"


def looks_like_address(text: str) -> bool:
    """A house number next to a name ("Main Street 5", "75001 Paris") or a
    comma ("Rue de Rivoli, Paris"): the address search answers these."""
    return bool(_DIGIT.search(text) and _LETTER.search(text)) or "," in text


def address_server(value: Any) -> str | None:
    """A usable Nominatim-compatible server: https, a host, optionally a path;
    no user name, password, query or fragment. None: off or not usable."""
    if not isinstance(value, str):
        return None
    text = value.strip().rstrip("/")
    if not text or len(text) > MAX_SERVER or any(not ch.isprintable() or ch.isspace() for ch in text):
        return None
    try:
        parts = urlsplit(text)
        port = parts.port  # raises for a malformed port
    except ValueError:
        return None
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        return None
    _ = port
    return text


def parse(data: Any) -> list[Place]:
    """Open-Meteo's place search."""
    results = data.get("results") if isinstance(data, dict) else None
    out: list[Place] = []
    for item in results if isinstance(results, list) else []:
        if not isinstance(item, dict):
            continue
        lat, lon = finite(item.get("latitude")), finite(item.get("longitude"))
        name = _text(item.get("name"))
        if lat is None or lon is None or not name or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        elevation = finite(item.get("elevation"))
        if elevation is not None and not -500 <= elevation <= 9000:
            elevation = None
        region = ", ".join(x for x in (_text(item.get("admin1")), _text(item.get("country"))) if x)
        out.append(Place(name, region, lat, lon, elevation))
        if len(out) >= MAX_RESULTS:
            break
    return out


def parse_addresses(data: Any) -> list[Place]:
    """Nominatim's `jsonv2` answer: a list of results with lat, lon and
    display_name (strings). Heights are not part of it."""
    out: list[Place] = []
    seen: set[str] = set()
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        lat, lon = finite(item.get("lat")), finite(item.get("lon"))
        full = _text(item.get("display_name"), 200)
        if lat is None or lon is None or not full or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        parts = [p for p in full.split(", ") if p]
        if len(parts) > 1 and parts[0].isdigit():  # "10, Main Street, ...": keep the number with the street
            parts = [f"{parts[1]} {parts[0]}", *parts[2:]]
        head, rest = parts[0][:MAX_LABEL], ""
        for part in parts[1:]:  # whole parts only, never cut in the middle of a word
            candidate = f"{rest}, {part}" if rest else part
            if len(head) + 2 + len(candidate) > MAX_LABEL:
                break
            rest = candidate
        place = Place(head, rest, lat, lon, None, OSM)
        if place.label in seen:  # one street mapped as several ways comes back several times
            continue
        seen.add(place.label)
        out.append(place)
        if len(out) >= MAX_RESULTS:
            break
    return out


async def search_places(http: Http, text: str, language: str = "en") -> list[Place]:
    query = clean_query(text)
    if len(query) < 2:
        return []
    params = {"name": query, "count": str(MAX_RESULTS), "format": "json", "language": _language(language)}
    return parse(await http.get_json(URL, params, limit=CAP_SMALL_JSON))


class AddressSearch:
    """Nominatim-compatible search, one request at a time, at least a second
    apart. One instance per Home Assistant process (`ADDRESSES`)."""

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._clock = clock
        self._sleep = sleep
        self._lock: asyncio.Lock | None = None
        self._last: float | None = None

    async def search(self, http: Http, server: str, text: str, language: str = "en") -> list[Place]:
        base = address_server(server)
        query = clean_query(text)
        if base is None or len(query) < 3:
            return []
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if self._last is not None:
                wait = self._last + ADDRESS_INTERVAL - self._clock()
                if wait > 0:
                    await self._sleep(wait)
            try:
                data = await http.get_json(f"{base}/search", {
                    "q": query, "format": "jsonv2", "limit": str(MAX_RESULTS),
                    "accept-language": _language(language), "addressdetails": "0",
                }, limit=CAP_SMALL_JSON)
            finally:
                self._last = self._clock()
        return parse_addresses(data)


ADDRESSES = AddressSearch()


async def find(
    http: Http, text: str, language: str = "en", server: str | None = ADDRESS_SERVER,
    addresses: AddressSearch | None = None,
) -> list[Place]:
    """Place names to Open-Meteo first; addresses, and names Open-Meteo does
    not know, to the address search when one is set."""
    addresses = addresses or ADDRESSES
    query = clean_query(text)
    base = address_server(server)
    if base is not None and looks_like_address(query):
        return await addresses.search(http, base, query, language)
    places = await search_places(http, query, language)
    if places or base is None:
        return places
    return await addresses.search(http, base, query, language)
