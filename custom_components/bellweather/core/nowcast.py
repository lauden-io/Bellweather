"""Precipitation for the next two hours at the point: a radar nowcast.

RainViewer's public API stopped serving future frames on 2026-01-01, so
Bellweather moves the past frames forward itself, the way weather services
do it for the first hours (radar extrapolation):

1. Read the radar around the point (about 80-100 km each way) in the newest
   frame and in the frame 20 minutes before it.
2. Find how the rain moved: block matching, coarse to fine, on blocks
   around the point; the median of the block vectors is the motion. Block
   matching is only slightly worse than full optical flow in the first two
   hours in the literature (docs/forecast.md).
3. For each step of 10 minutes, look upstream: the rain that will be at the
   point after L minutes is now at point - motion x L. Uncertainty grows
   with time, so the look covers a neighbourhood that grows with L; the
   share of wet pixels in it is the probability.
4. Stop where upstream leaves the radar window: no guessing beyond what was
   seen.

Rain that forms or dies on the way is not foreseen; that is the known limit
of every extrapolation, and why it loses to models after one to a few
hours. The nowcast checks itself: each step is later compared with the
radar's actual value at the point, next to "no change" (persistence).
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from . import png
from .geo import KM_PER_DEGREE_LAT
from .model import Location
from .safety import MAX_IMAGE_BYTES

ZOOM = 7
TILE = 256
WINDOW_PX = 96  # radius around the point, in zoom-7 pixels
SEARCH_PX = 24  # largest motion between the two frames (20 min): about 60-90 km/h at mid latitudes
BLOCK_PX = 24
STEP_MINUTES = 10
HORIZON_MINUTES = 120
WET = 0.1  # mm/h
LIKELY = 0.5  # share of wet pixels that counts as "precipitation expected"


@dataclass(frozen=True)
class Step:
    minutes: int  # after the issue time
    rate: float  # mm/h, mean over the neighbourhood
    probability: float  # 0..1


@dataclass
class Nowcast:
    issued: datetime
    method: str  # "radar_extrapolation" or an official service key
    steps: list[Step] = field(default_factory=list)
    motion_kmh: float | None = None
    motion_towards: float | None = None  # degrees the rain moves towards
    attribution: str | None = None

    def next_hour_mm(self) -> float | None:
        steps = [s for s in self.steps if 0 < s.minutes <= 60]
        if not steps or steps[-1].minutes < 60:
            return None
        return round(sum(s.rate for s in steps) * STEP_MINUTES / 60, 2)

    def starts_in(self, wet_now: bool) -> int | None:
        if wet_now:
            return None
        return next((s.minutes for s in self.steps if s.probability >= LIKELY), None)

    def stops_in(self, wet_now: bool) -> int | None:
        if not wet_now:
            return None
        return next((s.minutes for s in self.steps if s.probability < LIKELY), None)

    def expected_within(self, minutes: int = 60) -> bool | None:
        steps = [s for s in self.steps if s.minutes <= minutes]
        if not steps:
            return None
        return any(s.probability >= LIKELY for s in steps)

    @property
    def horizon(self) -> int:
        return self.steps[-1].minutes if self.steps else 0


Grid = list[list[float]]


def global_pixel(location: Location) -> tuple[float, float]:
    """(x, y) of the point in zoom-7 world pixels."""
    n = 2**ZOOM * TILE
    lat = max(min(location.latitude, 85.0511), -85.0511)
    x = (location.longitude + 180.0) / 360.0 * n
    y = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n
    return x, y


def tiles_for_window(cx: float, cy: float, radius: int) -> list[tuple[int, int]]:
    n = 2**ZOOM
    xs = range(int((cx - radius) // TILE), int((cx + radius) // TILE) + 1)
    ys = range(max(int((cy - radius) // TILE), 0), min(int((cy + radius) // TILE), n - 1) + 1)
    return [(x % n, y) for y in ys for x in xs]


def block_3x3(cx: float, cy: float) -> list[tuple[int, int]]:
    """The home's tile and its eight neighbours, independent of where in
    the tile the home lies."""
    n = 2**ZOOM
    tx, ty = int(cx // TILE), int(cy // TILE)
    return [((tx + dx) % n, ty + dy) for dy in (-1, 0, 1) for dx in (-1, 0, 1) if 0 <= ty + dy < n]


def rate_grid(tile: bytes, colours: dict[tuple[int, int, int, int], tuple[float, bool]], rate) -> Grid:
    _, _, rows = png.image(tile, (TILE, TILE))
    out: Grid = []
    for row in rows:
        line = []
        for rgba in row:
            if rgba[3] == 0:
                line.append(0.0)
            else:
                hit = colours.get(rgba)
                line.append(rate(hit[0]) if hit else 0.0)
        out.append(line)
    return out


def window(tiles: dict[tuple[int, int], Grid], cx: float, cy: float, radius: int) -> Grid | None:
    """The square of side 2*radius+1 centred on the point; None if a tile is missing."""
    n = 2**ZOOM
    x0, y0 = int(cx) - radius, int(cy) - radius
    out: Grid = []
    for gy in range(y0, y0 + 2 * radius + 1):
        line = []
        for gx in range(x0, x0 + 2 * radius + 1):
            key = ((gx // TILE) % n, gy // TILE)
            grid = tiles.get(key)
            if grid is None:
                return None
            line.append(grid[gy % TILE][gx % TILE])
        out.append(line)
    return out


def _downsample(grid: Grid, factor: int) -> Grid:
    size = len(grid) // factor
    return [
        [statistics.fmean(grid[y * factor + j][x * factor + i] for j in range(factor) for i in range(factor))
         for x in range(size)]
        for y in range(size)
    ]


def _cost(prev: Grid, curr: Grid, y0: int, x0: int, size: int, dy: int, dx: int) -> float | None:
    """Mean absolute difference between the current block and the previous
    frame shifted by (dy, dx): rain at prev[y-dy][x-dx] moved to curr[y][x]."""
    total = 0.0
    count = 0
    h = len(prev)
    for y in range(y0, y0 + size):
        py = y - dy
        if not 0 <= py < h:
            return None
        rp, rc = prev[py], curr[y]
        for x in range(x0, x0 + size):
            px = x - dx
            if not 0 <= px < h:
                return None
            total += abs(rc[x] - rp[px])
            count += 1
    return total / count if count else None


def _best_shift(prev: Grid, curr: Grid, y0: int, x0: int, size: int, centre: tuple[int, int], search: int) -> tuple[int, int] | None:
    best, best_cost = None, math.inf
    for dy in range(centre[0] - search, centre[0] + search + 1):
        for dx in range(centre[1] - search, centre[1] + search + 1):
            cost = _cost(prev, curr, y0, x0, size, dy, dx)
            if cost is not None and cost < best_cost:
                best, best_cost = (dy, dx), cost
    return best


def motion(prev: Grid, curr: Grid) -> tuple[float, float] | None:
    """(dy, dx) in pixels between the two frames: median over blocks with
    rain, found coarse (1/4 resolution) then refined (1/2, then full)."""
    logp = [[math.log1p(v) for v in row] for row in prev]
    logc = [[math.log1p(v) for v in row] for row in curr]
    pyramid = {1: (logp, logc), 2: (_downsample(logp, 2), _downsample(logc, 2)), 4: (_downsample(logp, 4), _downsample(logc, 4))}
    size = len(curr)
    vectors: list[tuple[float, float]] = []
    margin = SEARCH_PX
    for by in range(margin, size - margin - BLOCK_PX + 1, BLOCK_PX):
        for bx in range(margin, size - margin - BLOCK_PX + 1, BLOCK_PX):
            wet = sum(1 for y in range(by, by + BLOCK_PX) for x in range(bx, bx + BLOCK_PX) if curr[y][x] >= WET)
            if wet < BLOCK_PX * BLOCK_PX * 0.1:
                continue  # too little rain to track
            guess = (0, 0)
            ok = True
            for factor, search in ((4, SEARCH_PX // 4), (2, 2), (1, 2)):
                p_f, c_f = pyramid[factor]
                centre = (0, 0) if factor == 4 else (guess[0] * 2, guess[1] * 2)
                found = _best_shift(p_f, c_f, by // factor, bx // factor, BLOCK_PX // factor, centre, search)
                if found is None:
                    ok = False
                    break
                guess = found
            if ok:
                vectors.append((float(guess[0]), float(guess[1])))
    if not vectors:
        return None
    return statistics.median(v[0] for v in vectors), statistics.median(v[1] for v in vectors)


def extrapolate(curr: Grid, shift_per_step: tuple[float, float], radius: int) -> list[Step]:
    """Steps of 10 minutes until upstream leaves the window."""
    centre = radius
    steps: list[Step] = []
    for k in range(1, HORIZON_MINUTES // STEP_MINUTES + 1):
        sy = centre - shift_per_step[0] * k
        sx = centre - shift_per_step[1] * k
        hood = 2 + k  # neighbourhood radius in pixels grows with lead time
        if not (hood <= sy <= len(curr) - 1 - hood and hood <= sx <= len(curr) - 1 - hood):
            break
        cells = [
            curr[y][x]
            for y in range(int(round(sy)) - hood, int(round(sy)) + hood + 1)
            for x in range(int(round(sx)) - hood, int(round(sx)) + hood + 1)
            if (y - sy) ** 2 + (x - sx) ** 2 <= hood * hood
        ]
        if not cells:
            break
        steps.append(Step(
            minutes=k * STEP_MINUTES, rate=round(statistics.fmean(cells), 2),
            probability=round(sum(1 for v in cells if v >= WET) / len(cells), 2),
        ))
    return steps


def km_per_pixel(latitude: float) -> float:
    return 40075.016686 * math.cos(math.radians(latitude)) / (2**ZOOM * TILE)


def nowcast_from_grids(prev: Grid, curr: Grid, gap_minutes: int, issued: datetime, latitude: float) -> Nowcast:
    found = motion(prev, curr)
    if found is None:
        # No trackable rain near the point: persistence of what is there.
        shift = (0.0, 0.0)
        speed = direction = None
    else:
        shift = (found[0] * STEP_MINUTES / gap_minutes, found[1] * STEP_MINUTES / gap_minutes)
        kmh = math.hypot(*shift) * km_per_pixel(latitude) * 60 / STEP_MINUTES
        speed = round(kmh, 1)
        direction = round(math.degrees(math.atan2(shift[1], -shift[0])) % 360) if kmh > 0 else None
    return Nowcast(
        issued=issued, method="radar_extrapolation", steps=extrapolate(curr, shift, WINDOW_PX + SEARCH_PX),
        motion_kmh=speed, motion_towards=direction, attribution="Weather data by RainViewer",
    )


# --- self-check -------------------------------------------------------------

@dataclass
class CheckEvent:
    issued: datetime
    minutes: int
    predicted: bool
    persistence: bool
    observed: bool


def check(events: list[CheckEvent], now: datetime, days: int = 7) -> dict[int, dict[str, float | int]]:
    """CSI per lead time for the nowcast and for "no change", last `days` days."""
    out: dict[int, dict[str, float | int]] = {}
    for minutes in sorted({e.minutes for e in events}):
        rows = [e for e in events if e.minutes == minutes and now - e.issued <= timedelta(days=days)]
        result: dict[str, float | int] = {"cases": len(rows), "wet": sum(e.observed for e in rows)}
        for name, attr in (("nowcast", "predicted"), ("persistence", "persistence")):
            hits = sum(1 for e in rows if getattr(e, attr) and e.observed)
            miss = sum(1 for e in rows if not getattr(e, attr) and e.observed)
            false = sum(1 for e in rows if getattr(e, attr) and not e.observed)
            result[name] = round(hits / (hits + miss + false), 2) if hits + miss + false else math.nan
        out[minutes] = result
    return out


_ = (KM_PER_DEGREE_LAT, MAX_IMAGE_BYTES)
