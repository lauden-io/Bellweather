"""Observations to score the forecast with.

Two kinds, both reduced to the same hourly record {"t", "w", "d", "pr",
"wet"} (temperature, wind, dew point, sea-level pressure, precipitation):

- **Bellweather's own values at the home** (`ObservationLog`): from each
  update, the measured temperature, wind, dew point and pressure near the
  full hour, and whether any measured source saw precipitation during the
  hour. Values that came from a model are never logged: scoring models
  against a model would only measure agreement. Each value says whether
  your own sensor measured it or a station or airport nearby: the
  correction removes a model's error in full where the home itself was
  measured, and for temperature, dew point and wind only a share of it
  where it was measured elsewhere (skill.ELSEWHERE_SHARE). Your own
  temperature, humidity and wind sensors are logged only when you say they
  are placed like a weather station's: a sensor by a warm wall would teach
  the forecast the wall's warmth. Otherwise the best measured value that is
  not your own is logged instead (`choices_for_log`). Your own barometer
  always counts: where it hangs does not change the pressure.
- **An airport's archive** (optional, off by default): the Iowa
  Environmental Mesonet keeps the METAR history of airports worldwide.
  Turned on in the options, it gives the first 60 days at once, so the
  correction can start on day one instead of after about 20 days. The
  service learns the airport's code, which says roughly where the home is.

The archive refuses requests that come too close together from one address
("Too many requests from your IP address, slow down.", HTTP 429): measured
on 2026-11-10, requests 1 second apart were refused, 5 seconds apart were
not. So every location's archive request goes through one gate: one at a
time, at least 10 seconds apart.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from ..derive import sea_level_pressure
from ..fusion import Choice
from ..model import Field, Kind, Reading
from ..safety import CAP_ARCHIVE_CSV, clean_text, csv_rows, finite, valid_id
from ..sources.base import Http

IEM_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
IEM_INTERVAL = 10.0  # seconds between two archive requests from this installation
MEASURED = {Kind.LOCAL, Kind.RADAR, Kind.STATION, Kind.AIRPORT}
NEAR_HOUR = timedelta(minutes=10)
# Airports report on their own schedule: most at :50 or :00, some at :15 and
# :45 (checked on 2026-11-10), some at :20 and :50. For each hour
# the archive's report nearest to the full hour counts, up to 20 minutes away.
ARCHIVE_NEAR_HOUR = timedelta(minutes=20)
KEEP_DAYS = 70
LOG_VERSION = 2  # 2: each value says whether your own sensor measured it (2026-11-16)
GATED = {"t", "d", "w"}  # values your own sensors give only when placed like a station's
_WET = re.compile(r"(DZ|RA|SN|SG|PL|GR|GS|UP)")

Observations = dict[datetime, dict[str, float | bool]]


def _hour(t: datetime) -> datetime:
    return t.replace(minute=0, second=0, microsecond=0)


class ObservationLog:
    """Hourly measured values at the home, kept for 70 days."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self.hours: Observations = {}
        # A log from before version 2 does not say where its values were
        # measured; see drop_legacy.
        self.legacy = bool((data or {}).get("hours")) and (data or {}).get("version") != LOG_VERSION
        for key, row in ((data or {}).get("hours") or {}).items():
            try:
                t = datetime.fromisoformat(str(key)[:25])
            except ValueError:
                continue
            if t.tzinfo is None or not isinstance(row, dict):
                continue
            clean: dict[str, float | bool] = {}
            for k in ("t", "w", "d", "pr"):
                v = finite(row.get(k))
                if v is not None:
                    clean[k] = v
            if isinstance(row.get("wet"), bool):
                clean["wet"] = row["wet"]
            for k in ("t", "w", "d", "pr"):
                if k in clean and row.get(f"{k}_own") is True:
                    clean[f"{k}_own"] = True
            if clean:
                self.hours[t] = clean

    def add(self, choices: dict[Field, Choice], now: datetime, own_sited: bool = False) -> None:
        """Record what was measured. Instant values count for the full hour
        they are within 10 minutes of; precipitation for the hour it fell in.
        `own_sited`: your own sensors are placed like a weather station's;
        otherwise their values are not logged for the forecast."""
        for field, key in (
            (Field.TEMPERATURE, "t"), (Field.WIND_SPEED, "w"), (Field.DEW_POINT, "d"), (Field.PRESSURE, "pr"),
        ):
            choice = choices.get(field)
            if choice is None or choice.reading.kind not in MEASURED:
                continue
            own = choice.reading.kind is Kind.LOCAL
            if own and not own_sited and key in GATED:
                continue
            observed = choice.reading.observed
            nearest = _hour(observed + timedelta(minutes=30))
            if abs(observed - nearest) <= NEAR_HOUR:
                slot = self.hours.setdefault(nearest, {})
                slot[key] = float(choice.reading.value)
                if own:
                    slot[f"{key}_own"] = True
                else:
                    slot.pop(f"{key}_own", None)
        rain = choices.get(Field.RAIN_NOW)
        if rain is not None and rain.reading.kind in MEASURED:
            slot = self.hours.setdefault(_hour(rain.reading.observed), {})
            slot["wet"] = bool(slot.get("wet")) or bool(rain.reading.value)
        cutoff = now - timedelta(days=KEEP_DAYS)
        self.hours = {t: v for t, v in self.hours.items() if t >= cutoff}

    def drop_legacy(self, keys: set[str]) -> None:
        """Forget the given values of hours logged before version 2, when
        they may have come from your own sensors that are not placed like a
        station's: such a log does not say which hours were yours."""
        if self.legacy and keys:
            for row in self.hours.values():
                for k in keys:
                    row.pop(k, None)
                    row.pop(f"{k}_own", None)
            self.hours = {t: row for t, row in self.hours.items() if row}
        self.legacy = False

    def as_dict(self) -> dict[str, Any]:
        return {"version": LOG_VERSION, "hours": {t.isoformat(): v for t, v in sorted(self.hours.items())}}


def choices_for_log(
    readings: list[Reading], result: dict[Field, Choice], own_sited: bool,
    refuse: Callable[[list[Reading]], dict[Field, Choice]],
) -> dict[Field, Choice]:
    """What the log records from one update. With your own sensors placed
    like a station's, or with none, it is what was shown. Otherwise the
    fusion runs again without them (`refuse`), so temperature, dew point and
    wind come from the best station or airport, as if your sensors were not
    there; pressure and precipitation stay as shown."""
    if own_sited or not any(r.kind is Kind.LOCAL for r in readings):
        return result
    again = dict(refuse([r for r in readings if r.kind is not Kind.LOCAL]))
    for field in (Field.PRESSURE, Field.RAIN_NOW):
        if field in result:
            again[field] = result[field]
        else:
            again.pop(field, None)
    return again


def parse_iem(text: str) -> Observations:
    """IEM `onlycomma` CSV with station,valid,elevation,tmpc,sknt,dwpc,mslp,alti,wxcodes."""
    out: Observations = {}
    gaps: dict[tuple[datetime, str], timedelta] = {}  # how far the kept value is from its full hour
    rows = []
    temps: dict[datetime, tuple[timedelta, float]] = {}  # full hour -> the temperature nearest to it
    for row in csv_rows(text[:CAP_ARCHIVE_CSV], ["valid", "elevation", "tmpc", "sknt", "dwpc", "mslp", "alti", "wxcodes"], 24 * 70 * 4):
        try:
            t = datetime.strptime(str(row.get("valid"))[:16], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        rows.append((t, row))
        temp = finite(row.get("tmpc"))
        nearest = _hour(t + timedelta(minutes=30))
        if temp is not None and abs(t - nearest) <= ARCHIVE_NEAR_HOUR and abs(t - nearest) < temps.get(nearest, (timedelta.max, 0.0))[0]:
            temps[nearest] = (abs(t - nearest), temp)
    for t, row in rows:
        nearest = _hour(t + timedelta(minutes=30))
        gap = abs(t - nearest)
        if gap <= ARCHIVE_NEAR_HOUR:
            temp = finite(row.get("tmpc"))
            knots = finite(row.get("sknt"))
            dew = finite(row.get("dwpc"))
            # Sea-level pressure from the remarks where an airport gives it
            # (mostly North America); otherwise from QNH, the altimeter
            # setting, converted with the airport's height and temperature
            # (derive.sea_level_pressure). QNH as it is would differ by
            # several hPa at high or cold airports.
            slp = finite(row.get("mslp"))
            alti = finite(row.get("alti"))
            elevation = finite(row.get("elevation"))
            pressure = slp
            if pressure is None and alti is not None and elevation is not None and temp is not None:
                earlier = temps.get(nearest - timedelta(hours=12))
                pressure = sea_level_pressure(alti * 33.8639, elevation, temp, earlier[1] if earlier else None)
            for key, value in (
                ("t", temp if temp is not None and -90 <= temp <= 60 else None),
                ("w", round(knots * 1.852, 1) if knots is not None and 0 <= knots <= 250 else None),
                ("d", dew if dew is not None and -90 <= dew <= 40 else None),
                ("pr", round(pressure, 1) if pressure is not None and 850 <= pressure <= 1090 else None),
            ):
                if value is not None and gap < gaps.get((nearest, key), timedelta.max):
                    out.setdefault(nearest, {})[key] = value
                    gaps[(nearest, key)] = gap
        codes = clean_text(row.get("wxcodes"), 64)
        wet = any(_WET.search(tok) and not tok.startswith("VC") for tok in codes.split() if codes != "M")
        slot = out.setdefault(_hour(t), {})
        slot["wet"] = bool(slot.get("wet")) or wet
    return out


class Gate:
    """One request at a time, at least `interval` seconds apart."""

    def __init__(
        self, interval: float, clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.interval = interval
        self._clock = clock
        self._sleep = sleep
        self._lock: asyncio.Lock | None = None
        self._last: float | None = None

    async def run(self, call: Callable[[], Awaitable]):
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if self._last is not None:
                wait = self._last + self.interval - self._clock()
                if wait > 0:
                    await self._sleep(wait)
            try:
                return await call()
            finally:
                self._last = self._clock()


IEM_GATE = Gate(IEM_INTERVAL)


async def fetch_iem(http: Http, station: str, start: datetime, end: datetime, gate: Gate | None = None) -> Observations:
    sid = valid_id(station)
    if sid is None:
        return {}
    last = end + timedelta(days=1)
    gate = gate or IEM_GATE
    text = await gate.run(lambda: http.get_text(IEM_URL, {
        "station": sid, "data": "tmpc,sknt,dwpc,mslp,alti,wxcodes", "year1": str(start.year), "month1": str(start.month), "day1": str(start.day),
        "year2": str(last.year), "month2": str(last.month), "day2": str(last.day), "tz": "Etc/UTC",
        "format": "onlycomma", "latlon": "no", "elev": "yes", "missing": "M", "trace": "T", "report_type": "3,4",
    }, limit=CAP_ARCHIVE_CSV))
    return parse_iem(text)
