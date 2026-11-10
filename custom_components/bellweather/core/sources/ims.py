"""Daily satellite snow map: NOAA/USNIC IMS, Northern Hemisphere, 4 km.

IMS (Interactive Multisensor Snow and Ice Mapping System) is an analyst's
daily map of snow on land and ice on water, drawn from satellite images and
other data. NSIDC mirrors it as dataset G02156.

- The whole daily 4 km file is downloaded (about 450 KB), so the service
  receives no location at all. A file is valid for 00 UTC of its day and
  appears about 14 hours later.
- The cell of the exact location is read inside Home Assistant. Grid: polar stereographic
  on WGS 84, true scale at 60 N, central meridian 80 W, 6144 x 6144 cells
  of 4 km, as defined in NSIDC's `Ims4km.gpd`. The text file starts with
  the bottom row.
- A water cell on a coast is answered by the land cells around it.
- Southern Hemisphere: no data.

NSIDC asks for citation and says the servers are not for operational use.
"""

from __future__ import annotations

import asyncio
import gzip
import io
import math
import re
from datetime import datetime, timedelta, timezone

from ..model import Field, Kind, Location, Reading
from ..safety import CAP_INDEX, MAX_ARCHIVE_BYTES, MAX_IMS_TEXT_BYTES, MAX_LINE_CHARS, UnsafeResponse
from ..privacy import Precision
from .base import Http, Source

INDEX_URL = "https://noaadata.apps.nsidc.org/NOAA/G02156/4km/{year}/"
SIZE = 6144
CELL_M = 4000.0
ORIGIN = 12288000.0  # metres from the pole to the outer edge of the grid
_A = 6378137.0
_E2 = 1 - (6356752.314245 / _A) ** 2
_E = math.sqrt(_E2)
_LAT_TS = math.radians(60.0)
_LON0 = math.radians(-80.0)

WATER, LAND, ICE, SNOW = "1", "2", "3", "4"
# A new file appears once a day and the index is about 43 KB (measured
# 2026-11-10), so it is read every 3 hours: a snow map up to 3 hours late
# costs nothing, an hourly check would cost about 1 MB a day.
CHECK_EVERY = timedelta(hours=3)
_FILE = re.compile(r'href="(ims(\d{4})(\d{3})(?:_\d{2}UTC)?_4km_v[\d.]+\.asc\.gz)"')


def _t(phi: float) -> float:
    s = _E * math.sin(phi)
    return math.tan(math.pi / 4 - phi / 2) / ((1 - s) / (1 + s)) ** (_E / 2)


def _m(phi: float) -> float:
    return math.cos(phi) / math.sqrt(1 - _E2 * math.sin(phi) ** 2)


_K = _A * _m(_LAT_TS) / _t(_LAT_TS)


def cell(latitude: float, longitude: float) -> tuple[int, int] | None:
    """(row from the top, column) of the 4 km cell that holds the point, or
    None outside the grid."""
    if not 0.0 <= latitude <= 90.0:
        return None
    rho = _K * _t(math.radians(latitude))
    d = math.radians(longitude) - _LON0
    x, y = rho * math.sin(d), -rho * math.cos(d)
    row, col = int((ORIGIN - y) // CELL_M), int((x + ORIGIN) // CELL_M)
    return (row, col) if 0 <= row < SIZE and 0 <= col < SIZE else None


def newest_file(index_html: str) -> tuple[str, datetime] | None:
    """The newest daily file listed in an NSIDC directory page."""
    best: tuple[str, datetime] | None = None
    for name, year, doy in _FILE.findall((index_html or "")[:CAP_INDEX]):
        try:
            if not 2000 <= int(year) <= 2100 or not 1 <= int(doy) <= 366:
                continue
            valid = datetime(int(year), 1, 1, tzinfo=timezone.utc) + timedelta(days=int(doy) - 1)
        except (ValueError, OverflowError):
            continue
        if best is None or valid > best[1]:
            best = (name, valid)
    return best


def read_cells(gz: bytes, row: int, col: int, radius: int = 1) -> list[list[str]]:
    """The values around (row, col), streaming the file so the 38 MB of text
    is never held at once. Rows are counted from the top; the file starts
    with the bottom row. Total output and line length are capped, so a
    crafted file cannot use unbounded memory."""
    wanted = {SIZE - 1 - r: r for r in range(max(row - radius, 0), min(row + radius, SIZE - 1) + 1)}
    found: dict[int, str] = {}
    data_row = -1
    total = 0
    pending = b""
    with gzip.GzipFile(fileobj=io.BytesIO(gz)) as stream:
        while len(found) < len(wanted):
            chunk = stream.read(1 << 20)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_IMS_TEXT_BYTES:
                raise UnsafeResponse("snow map larger than expected")
            lines = (pending + chunk).split(b"\n")
            pending = lines.pop()
            if len(pending) > MAX_LINE_CHARS:
                raise UnsafeResponse("line longer than expected")
            for raw in lines:
                line = raw.strip()
                if len(line) != SIZE or not line.isdigit():
                    if data_row < 0:
                        continue  # header
                    return [list(found[r]) for r in sorted(found)]
                data_row += 1
                if data_row in wanted:
                    lo, hi = max(col - radius, 0), min(col + radius, SIZE - 1)
                    found[wanted[data_row]] = line[lo : hi + 1].decode("ascii")
                    if len(found) == len(wanted):
                        break
    return [list(found[r]) for r in sorted(found)]


def snow_from_cells(cells: list[list[str]]) -> bool | None:
    """Snow on land at the centre cell; for a water cell, the land cells
    around it decide (snow if at least half of them are snowy)."""
    if not cells:
        return None
    centre = cells[len(cells) // 2][len(cells[0]) // 2]
    if centre in (LAND, SNOW):
        return centre == SNOW
    land = [v for row in cells for v in row if v in (LAND, SNOW)]
    if not land:
        return None
    return sum(v == SNOW for v in land) * 2 >= len(land)


class ImsSnowSource(Source):
    key = "ims"
    kind = Kind.SATELLITE
    attribution = "U.S. National Ice Center IMS daily snow and ice analysis (NSIDC G02156)"
    sends = "nothing: the whole daily map is downloaded"

    def __init__(self) -> None:
        self._file: str | None = None
        self._result: Reading | None = None
        self._checked_at: datetime | None = None

    async def fetch(self, http: Http, location: Location, precision: Precision, now: datetime) -> list[Reading]:
        position = cell(location.latitude, location.longitude)
        if position is None:
            return []  # outside the map: never download it
        if self._checked_at and now - self._checked_at < CHECK_EVERY:
            return [self._result] if self._result else []
        self._checked_at = now
        latest = None
        for year in (now.year, now.year - 1):
            latest = newest_file(await http.get_text(INDEX_URL.format(year=year), limit=CAP_INDEX))
            if latest:
                break
        if not latest:
            return [self._result] if self._result else []
        name, valid = latest
        if name != self._file:
            year = valid.year
            gz = await http.get_bytes(INDEX_URL.format(year=year) + name, limit=MAX_ARCHIVE_BYTES)
            cells = await asyncio.to_thread(read_cells, gz, *position)
            snow = snow_from_cells(cells)
            self._file = name
            self._result = None if snow is None else Reading(
                field=Field.SNOW_ON_GROUND, value=snow, kind=Kind.SATELLITE, source=self.key,
                observed=valid, station="IMS 4 km", distance_km=0.0, attribution=self.attribution,
            )
        return [self._result] if self._result else []
