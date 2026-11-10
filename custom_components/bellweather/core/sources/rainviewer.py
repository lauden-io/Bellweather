"""Radar at the exact point: RainViewer's public radar mosaic.

The only free, keyless, multi-country radar (the API page names 1200+ radars
in 150+ countries). Since 2026-01-01 the public API is for personal,
educational and small-scale community use, keeps past frames only (10-minute
steps, about 2 hours), one colour scheme (Universal Blue) and zoom 7 at most.

Only the zoom-7 tile that holds the point is downloaded; the pixel is read
inside Home Assistant, so the service never learns the exact location. In the colour table the
rain colours and the snow colours do not overlap, so one pixel gives both
the phase and the reflectivity (dBZ).

A transparent pixel means "no echo", but it would also mean "no radar
coverage at this point". The coverage map tells them apart: there, a transparent pixel is
covered by radar and an opaque one is not. Outside coverage this source
returns nothing, so the next source in the chain answers.

Past frames never change, so each frame's pixel is read once and kept. The
last hour's amount is the sum of the six newest 10-minute frames, each
taken as a constant rate for its 10 minutes.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import asyncio

from .. import nowcast as nc
from .. import png
from ..geo import tile_for
from ..model import Field, Kind, Location, PrecipType, Reading, intensity_class
from ..privacy import Precision
from ..safety import CAP_COLOURS, CAP_SMALL_JSON, csv_header, csv_rows, finite, not_future
from .base import Http, Source

MAPS_URL = "https://api.rainviewer.com/public/weather-maps.json"
COLOURS_URL = "https://www.rainviewer.com/files/rainviewer_api_colors_table.csv"
ZOOM = 7
SCHEME = 2  # Universal Blue, the only scheme left
SCHEME_COLUMN = "Universal Blue"
OPTIONS = "0_1"  # no smoothing (keeps the colours exact), snow colours on
COLOUR_TABLE_MAX_AGE = timedelta(days=1)
COVERAGE_MAX_AGE = timedelta(days=1)
FRAME_MINUTES = 10
WET_MM_PER_HOUR = 0.1
# The index names the tile host and paths. They are checked, so a tampered
# index cannot send requests anywhere else.
ALLOWED_HOST = "https://tilecache.rainviewer.com"
MAX_NEW_FRAMES = 13  # new past frames read per update (about 2 hours)
_PATH = re.compile(r"^/v2/radar/[0-9a-f]{6,32}$")


def rain_rate(dbz: float) -> float:
    """mm/h from reflectivity with the classic Marshall-Palmer relation
    Z = 200 R^1.6. To be tuned against stations (snow needs its own)."""
    return ((10 ** (dbz / 10)) / 200) ** (1 / 1.6)


def parse_colour_table(text: str) -> dict[tuple[int, int, int, int], tuple[float, bool]]:
    """RGBA -> (dBZ, is_snow). The first half of the rows is rain, the
    second half snow, both from -32 to 95 dBZ."""
    first = (csv_header(text) or [""])[0]
    rows = csv_rows(text[:CAP_COLOURS], [first, SCHEME_COLUMN], 1000)
    half = len(rows) // 2
    table: dict[tuple[int, int, int, int], tuple[float, bool]] = {}
    for i, row in enumerate(rows):
        hexa = (row.get(SCHEME_COLUMN) or "").lstrip("#")
        if len(hexa) != 8 or any(c not in "0123456789abcdefABCDEF" for c in hexa):
            continue
        rgba = tuple(int(hexa[j : j + 2], 16) for j in (0, 2, 4, 6))
        if rgba[3] == 0:
            continue
        dbz = finite(row.get(first))
        if dbz is None or not -40 <= dbz <= 100:
            continue
        table.setdefault(rgba, (dbz, i >= half))
    return table


def covered(rgba: tuple[int, int, int, int]) -> bool:
    """On the coverage map, transparent means covered by radar."""
    return rgba[3] == 0


@dataclass(frozen=True)
class Frame:
    time: datetime
    rate: float  # mm/h
    snow: bool


def last_hour_mm(frames: list[Frame], newest: datetime) -> float | None:
    """mm in the hour that ends with the newest frame; None when a frame of
    that hour is missing."""
    wanted = [newest - timedelta(minutes=FRAME_MINUTES * i) for i in range(60 // FRAME_MINUTES)]
    by_time = {f.time: f for f in frames}
    if any(t not in by_time for t in wanted):
        return None
    return round(sum(by_time[t].rate for t in wanted) * FRAME_MINUTES / 60, 2)


def valid_frames(maps: object, now: datetime) -> list[tuple[datetime, str]]:
    """(time, path) of the listed past frames that pass the checks: the
    expected host, a path of the known shape, a time that is not in the
    future. At most 24 frames."""
    if not isinstance(maps, dict) or maps.get("host") != ALLOWED_HOST:
        return []
    radar = maps.get("radar")
    past = radar.get("past") if isinstance(radar, dict) else None
    out: list[tuple[datetime, str]] = []
    for item in (past if isinstance(past, list) else [])[-24:]:
        if not isinstance(item, dict):
            continue
        stamp, path = finite(item.get("time")), item.get("path")
        if stamp is None or not isinstance(path, str) or not _PATH.match(path):
            continue
        try:
            when = datetime.fromtimestamp(stamp, timezone.utc)
        except (OverflowError, OSError, ValueError):
            continue
        if not_future(when, now) and now - when < timedelta(hours=6):
            out.append((when, path))
    return out


class RainViewerSource(Source):
    key = "rainviewer"
    kind = Kind.RADAR
    attribution = "Weather data by RainViewer"
    sends = "the zoom-7 map tile that holds the point (a few hundred km wide), whatever the precision"

    def __init__(self) -> None:
        self._colours: dict[tuple[int, int, int, int], tuple[float, bool]] = {}
        self._colours_at: datetime | None = None
        self._coverage: bool | None = None
        self._coverage_at: datetime | None = None
        self._frames: dict[datetime, Frame] = {}
        self.covered: bool | None = None  # for diagnostics
        self._index: list[tuple[datetime, str]] = []
        self._grids: dict[tuple[str, int, int], nc.Grid] = {}
        self._pending: dict[tuple[datetime, int], tuple[bool, bool]] = {}
        self.checks: list[nc.CheckEvent] = []
        self.last_nowcast: nc.Nowcast | None = None
        self._nowcast_key: tuple | None = None  # the frames the last nowcast was made from

    async def _colour_table(self, http: Http, now: datetime) -> dict:
        if not self._colours or not self._colours_at or now - self._colours_at > COLOUR_TABLE_MAX_AGE:
            try:
                text = await http.get_text(COLOURS_URL, limit=CAP_COLOURS)
                table = await asyncio.to_thread(parse_colour_table, text)
            except Exception:  # keep the old table; frames with an echo then give no answer if there is none
                self._colours_at = now - COLOUR_TABLE_MAX_AGE + timedelta(hours=1)  # try again in an hour
                return self._colours
            if table:
                self._colours = table
            self._colours_at = now
        return self._colours

    async def _is_covered(self, http: Http, host: str, location: Location, now: datetime) -> bool:
        if self._coverage is None or not self._coverage_at or now - self._coverage_at > COVERAGE_MAX_AGE:
            x, y, px, py = tile_for(location.latitude, location.longitude, ZOOM)
            tile = await http.get_bytes(f"{host}/v2/coverage/0/256/{ZOOM}/{x}/{y}/0/0_0.png")
            self._coverage = covered(await asyncio.to_thread(png.pixel, tile, px, py, (256, 256)))
            self._coverage_at = now
        return self._coverage

    async def _frame(self, http: Http, host: str, path: str, when: datetime, location: Location, now: datetime) -> Frame | None:
        if when in self._frames:
            return self._frames[when]
        x, y, px, py = tile_for(location.latitude, location.longitude, ZOOM)
        tile = await http.get_bytes(f"{host}{path}/256/{ZOOM}/{x}/{y}/{SCHEME}/{OPTIONS}.png")
        rgba = await asyncio.to_thread(png.pixel, tile, px, py, (256, 256))  # off the event loop
        if rgba[3] == 0:
            frame = Frame(when, 0.0, False)
        else:
            hit = (await self._colour_table(http, now)).get(rgba)
            if hit is None:
                return None  # a colour outside the table: do not guess
            frame = Frame(when, round(rain_rate(hit[0]), 2), hit[1])
        self._frames[when] = frame
        return frame

    async def fetch(self, http: Http, location: Location, precision: Precision, now: datetime) -> list[Reading]:
        maps = await http.get_json(MAPS_URL, limit=CAP_SMALL_JSON)
        past = valid_frames(maps, now)
        if not past:
            return []
        host = ALLOWED_HOST
        # Fetched before any tile, whatever the pixel shows: when the table is
        # asked for must not hint at what the radar sees at the home.
        await self._colour_table(http, now)
        self.covered = await self._is_covered(http, host, location, now)
        if not self.covered:
            return []
        frames: list[Frame] = []
        new = 0
        for when, path in past:
            if when not in self._frames:
                new += 1
                if new > MAX_NEW_FRAMES:
                    continue  # the rest next time
            frame = await self._frame(http, host, path, when, location, now)
            if frame:
                frames.append(frame)
        self._index = past
        listed = {when for when, _ in past}
        self._frames = {t: f for t, f in self._frames.items() if t in listed}
        if not frames:
            return []
        frames.sort(key=lambda f: f.time)
        newest = frames[-1]
        common = dict(kind=Kind.RADAR, source=self.key, distance_km=0.0, attribution=self.attribution)
        wet = newest.rate >= WET_MM_PER_HOUR
        ptype = (PrecipType.SNOW if newest.snow else PrecipType.RAIN) if wet else PrecipType.NONE
        out = [
            Reading(field=Field.RAIN_NOW, value=wet, observed=newest.time, **common),
            Reading(field=Field.INTENSITY, value=newest.rate, observed=newest.time, **common),
            Reading(field=Field.INTENSITY_CLASS, value=intensity_class(newest.rate, ptype).value, observed=newest.time, **common),
            Reading(field=Field.PRECIP_TYPE, value=ptype.value, observed=newest.time, **common),
        ]
        hour = last_hour_mm(frames, newest.time)
        if hour is not None:
            out.append(Reading(field=Field.PRECIP_LAST_HOUR, value=hour, observed=newest.time, **common))
        wet_frames = [f for f in frames if f.rate >= WET_MM_PER_HOUR]
        self._resolve_checks(now)
        if wet_frames:
            t = wet_frames[-1].time
            out.append(Reading(field=Field.LAST_PRECIP, value=t.isoformat(), observed=t, **common))
        return out

    # --- nowcast -----------------------------------------------------------

    async def _grid(self, http: Http, path: str, x: int, y: int, now: datetime) -> nc.Grid:
        key = (path, x, y)
        if key not in self._grids:
            tile = await http.get_bytes(f"{ALLOWED_HOST}{path}/256/{ZOOM}/{x}/{y}/{SCHEME}/{OPTIONS}.png")
            colours = await self._colour_table(http, now)
            self._grids[key] = await asyncio.to_thread(nc.rate_grid, tile, colours, rain_rate)
        return self._grids[key]

    async def nowcast(self, http: Http, location: Location, now: datetime) -> nc.Nowcast | None:
        """Extrapolate the newest frame. Needs a covered location and two
        frames 20 (or 10) minutes apart. Same zoom-7 tiles as the point
        reading, plus their neighbours: still no exact location leaves."""
        if not self.covered or len(self._index) < 2:
            return None
        newest_time, newest_path = self._index[-1]
        older = [(t, p) for t, p in self._index if newest_time - t in (timedelta(minutes=20), timedelta(minutes=10))]
        if not older:
            return None
        older_time, older_path = min(older, key=lambda tp: tp[0])  # prefer the 20-minute gap
        # New frames come every 10 minutes and updates every 5: between two
        # frames the same input gives the same answer, so it is not computed
        # (or self-checked) twice.
        key = (newest_path, older_path, location.latitude, location.longitude)
        if key == self._nowcast_key and self.last_nowcast is not None:
            return self.last_nowcast
        radius = nc.WINDOW_PX + nc.SEARCH_PX
        cx, cy = nc.global_pixel(location)
        # Always the same 3x3 block around the home's tile: which neighbours
        # are asked for must not reveal where in the tile the home lies.
        tiles = nc.block_3x3(cx, cy)
        grids_new = {xy: await self._grid(http, newest_path, *xy, now) for xy in tiles}
        grids_old = {xy: await self._grid(http, older_path, *xy, now) for xy in tiles}
        # Keep the last three frames: the newest becomes "older" in 10-20
        # minutes, and its tiles need not be fetched again.
        keep = {path for _, path in self._index[-3:]}
        self._grids = {k: v for k, v in self._grids.items() if k[0] in keep}
        curr = nc.window(grids_new, cx, cy, radius)
        prev = nc.window(grids_old, cx, cy, radius)
        if curr is None or prev is None:
            return None
        gap = int((newest_time - older_time).total_seconds() // 60)
        result = await asyncio.to_thread(nc.nowcast_from_grids, prev, curr, gap, newest_time, location.latitude)
        self._record(result)
        self.last_nowcast = result
        self._nowcast_key = key
        return result

    def _record(self, result: nc.Nowcast) -> None:
        """Remember what was predicted, to compare once those frames exist."""
        point = next((f for f in self._frames.values() if f.time == result.issued), None)
        if point is None:
            return
        wet_now = point.rate >= WET_MM_PER_HOUR
        for step in result.steps:
            if step.minutes <= 60:
                self._pending.setdefault((result.issued, step.minutes), (step.probability >= nc.LIKELY, wet_now))

    def _resolve_checks(self, now: datetime) -> None:
        by_time = {f.time: f for f in self._frames.values()}
        for (issued, minutes), (predicted, persistence) in list(self._pending.items()):
            target = issued + timedelta(minutes=minutes)
            frame = by_time.get(target)
            if frame is not None:
                self.checks.append(nc.CheckEvent(issued, minutes, predicted, persistence, frame.rate >= WET_MM_PER_HOUR))
                del self._pending[(issued, minutes)]
            elif now - target > timedelta(hours=3):
                del self._pending[(issued, minutes)]
        cutoff = now - timedelta(days=7)
        self.checks = [e for e in self.checks if e.issued >= cutoff][-10000:]

