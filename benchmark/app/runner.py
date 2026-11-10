"""Bellweather benchmark: captures every provider's forecast for the random
addresses in points.json every 4 hours, and records the scoring stations'
reports every hour. Scoring and reports are separate (score.py, report.py).

Method: docs/benchmark.md. Runs as a Home Assistant add-on; everything is
kept in /data. Times are UTC throughout.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import math
import os
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree

APP = Path(__file__).resolve().parent
sys.path.insert(0, str(APP))

from core.derive import dew_point_from_humidity  # noqa: E402
from core.forecast.blend import blend_hours  # noqa: E402
from core.forecast.engine import Airport, ForecastEngine, verification_key  # noqa: E402
from core.forecast.members import DEFAULT_MODELS, fetch_forecast  # noqa: E402
from core.forecast.skill import Corrections  # noqa: E402
from core.fusion import Settings, fuse  # noqa: E402
from core.model import Location  # noqa: E402
from core.privacy import Precision  # noqa: E402
from core.sources import make_sources  # noqa: E402
from core.safety import MAX_IMAGE_BYTES, MAX_JSON_BYTES, MAX_TEXT_BYTES, HttpStatusError, UnsafeResponse, check_url  # noqa: E402

VERSION = "0.2.3"
DATA = Path(os.environ.get("BENCH_DATA") or ("/data" if Path("/data").exists() else str(APP / "data")))
START = datetime(2026, 11, 10, 23, 0, tzinfo=timezone.utc)  # 2026-11-11 00:00 Central European Time
CAPTURE_HOURS = {23, 3, 7, 11, 15, 19}  # every 4 hours from the start
OBS_MINUTE = 15  # the scoring stations' reports are recorded every hour at :15
HORIZON = timedelta(hours=96)
SHARE = Path("/share/bellweather_benchmark") if Path("/share").exists() else DATA / "share"
REPORTS = [7, 14, 30, 91, 182, 365]  # days after the start
REPORT_DELAY = timedelta(hours=6)  # the last day's reports are in by then
HOME_LOG_SINCE = START  # Bellweather's own local log runs from the start, as in Home Assistant
VERIFY_EVERY = timedelta(hours=24)
# How far back each request for station reports reaches. Normally a few hours,
# for reports that arrive late. After a gap (the host was off, or a request
# failed) from where the gap began, as far back as the service keeps reports
# (both checked on 2026-11-11: aviationweather.gov answered 48 hours back,
# E-SOH about 24 hours).
METAR_RECENT, METAR_KEEPS = timedelta(hours=3), timedelta(hours=48)
ESOH_RECENT, ESOH_KEEPS = timedelta(hours=6), timedelta(hours=24)
COVERED_SLACK = timedelta(minutes=5)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
log = logging.getLogger("benchmark")


def options() -> dict:
    try:
        return json.loads((DATA / "options.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def user_agent() -> str:
    contact = str(options().get("contact") or "").strip()[:100]
    base = f"bellweather-benchmark/{VERSION} (open-source forecast benchmark"
    return f"{base}; {contact})" if contact else f"{base})"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect())


class Http:
    """The core's Http calls over urllib: https only, no redirects, size caps."""

    def _get(self, url, params, limit, allow_404):
        check_url(url)
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        try:
            with _OPENER.open(urllib.request.Request(url, headers={"User-Agent": user_agent()}), timeout=60) as resp:
                data = resp.read(limit + 1)
        except urllib.error.HTTPError as err:
            if err.code == 404 and allow_404:
                return None
            raise HttpStatusError(err.code) from None
        if len(data) > limit:
            raise UnsafeResponse("response too large")
        return data

    async def get_bytes(self, url, params=None, limit=MAX_IMAGE_BYTES):
        return await asyncio.to_thread(self._get, url, params, limit, False) or b""

    async def get_text(self, url, params=None, limit=MAX_TEXT_BYTES):
        return (await asyncio.to_thread(self._get, url, params, limit, False) or b"").decode("utf-8", "replace")

    async def get_json(self, url, params=None, limit=MAX_JSON_BYTES):
        data = await asyncio.to_thread(self._get, url, params, limit, True)
        if data is None or not data.strip():  # 404, or an empty answer (aviationweather.gov: no reports)
            return None
        return json.loads(data)


HTTP = Http()


# --- storage ---------------------------------------------------------------------

SCHEMA = """
    CREATE TABLE IF NOT EXISTS forecast (
        capture TEXT, point TEXT, provider TEXT, target TEXT, var TEXT, value REAL,
        PRIMARY KEY (capture, point, provider, target, var));
    CREATE TABLE IF NOT EXISTS observation (
        station TEXT, time TEXT, var TEXT, value REAL, PRIMARY KEY (station, time, var));
    CREATE TABLE IF NOT EXISTS run (time TEXT, kind TEXT, point TEXT, provider TEXT, status TEXT);
    CREATE TABLE IF NOT EXISTS fetched (source TEXT, start TEXT, until TEXT);
"""


def db(path: Path | None = None) -> sqlite3.Connection:
    con = sqlite3.connect(path or DATA / "benchmark.sqlite")
    con.executescript(SCHEMA)
    return con


def iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def save_forecast(con, capture: datetime, point: str, provider: str, rows: list[tuple[datetime, str, float]]) -> int:
    limit = capture + HORIZON
    clean = [(iso(capture), point, provider, iso(t), var, float(v)) for t, var, v in rows
             if v is not None and math.isfinite(float(v)) and capture - timedelta(hours=1) <= t <= limit]
    con.executemany("INSERT OR REPLACE INTO forecast VALUES (?,?,?,?,?,?)", clean)
    return len(clean)


# --- providers -------------------------------------------------------------------

HOURLY = "temperature_2m,dew_point_2m,wind_speed_10m,pressure_msl,precipitation,precipitation_probability"


def hour_rows(hours, prefix=""):
    out = []
    for h in hours:
        t = h.time
        for var, v in (("t", h.temperature), ("d", h.dew_point), ("w", h.wind_speed), ("p", h.pressure),
                       ("r", h.precipitation), ("pop", getattr(h, "precipitation_probability", None))):
            if v is not None:
                out.append((t, var, v))
    return out


async def bellweather_and_models(point, engine: ForecastEngine, now: datetime):
    """Bellweather, its uncorrected control and ECMWF IFS, from one request."""
    loc = Location(point["latitude"], point["longitude"], point.get("elevation_m"))
    members = await fetch_forecast(HTTP, loc, Precision.EXACT, DEFAULT_MODELS)
    start = now.replace(minute=0, second=0, microsecond=0)
    corrected = blend_hours(members, engine.verification.corrections, loc, start)
    control = blend_hours(members, Corrections(), loc, start)
    ecmwf = []
    for t, h in sorted((members.get("ecmwf_ifs025") or {}).items()):
        if t >= start:
            for var, v in (("t", h.temperature), ("d", h.dew_point), ("w", h.wind_speed), ("p", h.pressure), ("r", h.precipitation)):
                if v is not None:
                    ecmwf.append((t, var, v))
    return {"bellweather": hour_rows(corrected), "control": hour_rows(control), "ecmwf_ifs": ecmwf}


async def open_meteo(point, now):
    data = await HTTP.get_json("https://api.open-meteo.com/v1/forecast", {
        "latitude": f"{point['latitude']:.4f}", "longitude": f"{point['longitude']:.4f}", "hourly": HOURLY,
        "forecast_days": "5", "timezone": "GMT", "wind_speed_unit": "kmh"})
    hourly = (data or {}).get("hourly") or {}
    times = [datetime.fromisoformat(s).replace(tzinfo=timezone.utc) for s in hourly.get("time") or []]
    rows = []
    for key, var in (("temperature_2m", "t"), ("dew_point_2m", "d"), ("wind_speed_10m", "w"), ("pressure_msl", "p"),
                     ("precipitation_probability", "pop")):
        for t, v in zip(times, hourly.get(key) or []):
            if v is not None:
                rows.append((t, var, v))
    # The amount given at T fell in the hour before T: stored for the hour that starts at T - 1 h.
    for t, v in zip(times, hourly.get("precipitation") or []):
        if v is not None:
            rows.append((t - timedelta(hours=1), "r", v))
    return rows


async def met_norway(point, now):
    params = {"lat": f"{point['latitude']:.4f}", "lon": f"{point['longitude']:.4f}"}
    if point.get("elevation_m") is not None:
        params["altitude"] = str(int(point["elevation_m"]))
    data = await HTTP.get_json("https://api.met.no/weatherapi/locationforecast/2.0/complete", params)
    rows = []
    for item in ((data or {}).get("properties") or {}).get("timeseries") or []:
        t = datetime.fromisoformat(item["time"].replace("Z", "+00:00"))
        inst = ((item.get("data") or {}).get("instant") or {}).get("details") or {}
        for key, var, f in (("air_temperature", "t", 1), ("dew_point_temperature", "d", 1), ("wind_speed", "w", 3.6),
                            ("air_pressure_at_sea_level", "p", 1)):
            if inst.get(key) is not None:
                rows.append((t, var, inst[key] * f))
        nxt = ((item.get("data") or {}).get("next_1_hours") or {}).get("details") or {}
        if nxt.get("precipitation_amount") is not None:
            rows.append((t, "r", nxt["precipitation_amount"]))
        if nxt.get("probability_of_precipitation") is not None:
            rows.append((t, "pop", nxt["probability_of_precipitation"]))
    return rows


async def openweather(point, now):
    key = str(options().get("openweather_api_key") or "").strip()
    if not key:
        return None  # not configured yet
    data = await HTTP.get_json("https://api.openweathermap.org/data/2.5/forecast", {
        "lat": f"{point['latitude']:.4f}", "lon": f"{point['longitude']:.4f}", "units": "metric", "appid": key})
    rows = []
    for item in (data or {}).get("list") or []:
        t = datetime.fromtimestamp(int(item["dt"]), tz=timezone.utc)
        main, wind = item.get("main") or {}, item.get("wind") or {}
        temp, hum = main.get("temp"), main.get("humidity")
        if temp is not None:
            rows.append((t, "t", temp))
        if temp is not None and hum:
            rows.append((t, "d", dew_point_from_humidity(temp, hum)))  # derived: the API gives humidity, not dew point
        if main.get("sea_level") is not None:
            rows.append((t, "p", main["sea_level"]))
        if wind.get("speed") is not None:
            rows.append((t, "w", wind["speed"] * 3.6))
        rain = (item.get("rain") or {}).get("3h")
        snow = (item.get("snow") or {}).get("3h")
        if rain is not None or snow is not None:
            rows.append((t - timedelta(hours=3), "r3", (rain or 0) + (snow or 0)))  # the 3 hours before T
        elif item.get("dt"):
            rows.append((t - timedelta(hours=3), "r3", 0.0))
        if item.get("pop") is not None:
            rows.append((t, "pop", item["pop"] * 100))
    return rows


async def dwd_mosmix(point, now):
    sid = point["mosmix"]["id"]
    blob = await HTTP.get_bytes(
        f"https://opendata.dwd.de/weather/local_forecasts/mos/MOSMIX_L/single_stations/{sid}/kml/MOSMIX_L_LATEST_{sid}.kmz",
        limit=5_000_000)
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        xml = zf.read(zf.namelist()[0])
    if len(xml) > 50_000_000:
        raise UnsafeResponse("MOSMIX file too large")
    root = ElementTree.fromstring(xml)
    ns = {"dwd": "https://opendata.dwd.de/weather/lib/pointforecast_dwd_extension_V1_0.xsd",
          "kml": "http://www.opengis.net/kml/2.2"}
    steps = [datetime.fromisoformat(s.text.replace("Z", "+00:00")) for s in root.iter(f"{{{ns['dwd']}}}TimeStep")]
    rows = []
    convert = {"TTT": ("t", lambda v: v - 273.15), "Td": ("d", lambda v: v - 273.15), "FF": ("w", lambda v: v * 3.6),
               "PPPP": ("p", lambda v: v / 100.0), "RR1c": ("r", lambda v: v), "wwP": ("pww", lambda v: v)}
    for fc in root.iter(f"{{{ns['dwd']}}}Forecast"):
        name = fc.get(f"{{{ns['dwd']}}}elementName")
        if name not in convert:
            continue
        var, f = convert[name]
        text = fc.find("dwd:value", ns)
        values = (text.text or "").split() if text is not None else []
        for t, raw in zip(steps, values):
            if raw == "-":
                continue
            try:
                v = f(float(raw))
            except ValueError:
                continue
            # RR1c is the hour before the time step.
            rows.append((t - timedelta(hours=1) if var == "r" else t, var, v))
    return rows


# --- Bellweather's state per address ------------------------------------------------

def engine_for(point) -> ForecastEngine:
    engine = ForecastEngine()
    loc = Location(point["latitude"], point["longitude"], point.get("elevation_m"))
    engine.key = verification_key(loc, Precision.EXACT, engine.models, point.get("archive_airport") is not None)
    path = DATA / "engine" / f"{point['id']}.json"
    try:
        engine.load(json.loads(path.read_text(encoding="utf-8")), datetime.now(timezone.utc))
    except (OSError, ValueError):
        pass
    return engine


def save_engine(point, engine: ForecastEngine) -> None:
    path = DATA / "engine" / f"{point['id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(engine.dump()), encoding="utf-8")


async def verify(point, engine: ForecastEngine, now: datetime) -> None:
    updated = engine.verification.updated
    if updated is not None and now - updated < VERIFY_EVERY:
        return
    a = point.get("archive_airport")
    airport = Airport(a["icao"], a["latitude"], a["longitude"]) if a else None
    loc = Location(point["latitude"], point["longitude"], point.get("elevation_m"))
    error = await engine.verify(HTTP, loc, Precision.EXACT, now, airport)
    log.info("verified %s: days=%s source=%s archive_error=%s", point["id"], engine.verification.corrections.days,
             engine.verification.corrections.source, error)


# --- capture ------------------------------------------------------------------------

PROVIDERS = {"open_meteo": open_meteo, "met_norway": met_norway, "openweather": openweather, "dwd_mosmix": dwd_mosmix}


async def capture(con, points, slot: datetime) -> None:
    log.info("capture %s: %d addresses", iso(slot), len(points))
    for point in points:
        engine = engine_for(point)
        try:
            await verify(point, engine, slot)
        except Exception as err:  # noqa: BLE001 - Bellweather still runs, uncorrected
            log.warning("verification %s failed: %s", point["id"], type(err).__name__)
        try:
            for provider, rows in (await bellweather_and_models(point, engine, slot)).items():
                n = save_forecast(con, slot, point["id"], provider, rows)
                con.execute("INSERT INTO run VALUES (?,?,?,?,?)", (iso(slot), "forecast", point["id"], provider, f"ok {n}"))
            save_engine(point, engine)
        except Exception as err:  # noqa: BLE001
            log.warning("bellweather %s failed: %s", point["id"], type(err).__name__)
            con.execute("INSERT INTO run VALUES (?,?,?,?,?)", (iso(slot), "forecast", point["id"], "bellweather", type(err).__name__))
        for name, fetch in PROVIDERS.items():
            try:
                rows = await fetch(point, slot)
                status = "not configured" if rows is None else f"ok {save_forecast(con, slot, point['id'], name, rows)}"
            except Exception as err:  # noqa: BLE001 - one provider failing must not stop the others
                status = type(err).__name__ + (f" {err.status}" if isinstance(err, HttpStatusError) else "")
                log.warning("%s %s failed: %s", name, point["id"], status)
            con.execute("INSERT INTO run VALUES (?,?,?,?,?)", (iso(slot), "forecast", point["id"], name, status))
            await asyncio.sleep(1)
        con.commit()
    log.info("capture %s done", iso(slot))


# --- the scoring stations' reports -----------------------------------------------------

def _utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def fetch_from(con, source: str, now: datetime, recent: timedelta, keeps: timedelta) -> datetime:
    """Where this hour's request for `source` starts. Every successful
    request stores the span it asked for; the first moment within reach that
    no request covered is where the reports are missing. Normally that is
    now, and the request reaches `recent` back; after an outage or failed
    requests it reaches back to the gap, at most `keeps` (what the service
    still has)."""
    floor = max(now - keeps, START - timedelta(hours=1))
    covered = floor
    for start, until in sorted((_utc(a), _utc(b)) for a, b in con.execute(
            "SELECT start, until FROM fetched WHERE source = ? AND until > ?", (source, iso(floor)))):
        if start > covered + COVERED_SLACK:
            break
        covered = max(covered, until)
    return min(now - recent, max(floor, covered - timedelta(minutes=30)))


def fetched(con, source: str, start: datetime, now: datetime) -> None:
    con.execute("INSERT INTO fetched VALUES (?,?,?)", (source, iso(start), iso(now)))


def _catching_up(name: str, start: datetime, now: datetime, recent: timedelta) -> None:
    if now - start > recent + timedelta(minutes=1):
        log.info("%s: filling the gap from %s", name, iso(start))


async def record_observations(con, points, now: datetime) -> None:
    metar = sorted({p["truth"]["id"] for p in points if p["truth"]["kind"] == "metar"})
    n = 0
    data = None
    con.execute("DELETE FROM fetched WHERE until < ?", (iso(now - 3 * METAR_KEEPS),))
    start = fetch_from(con, "metar", now, METAR_RECENT, METAR_KEEPS)
    hours = min(max(math.ceil((now - start).total_seconds() / 3600), 3), int(METAR_KEEPS.total_seconds() // 3600))
    _catching_up("METAR reports", start, now, METAR_RECENT)
    for attempt in range(2 if metar else 0):
        try:
            data = await HTTP.get_json("https://aviationweather.gov/api/data/metar", {"ids": ",".join(metar), "hours": str(hours), "format": "json"})
            fetched(con, "metar", now - timedelta(hours=hours), now)
            break
        except Exception as err:  # noqa: BLE001
            log.warning("METAR reports failed (try %d): %s", attempt + 1, type(err).__name__)
            await asyncio.sleep(60)
    try:
        for m in data or []:
            sid, t = m.get("icaoId"), m.get("reportTime") or m.get("obsTime")
            if not sid or not t:
                continue
            when = datetime.fromisoformat(str(t).replace("Z", "+00:00")) if isinstance(t, str) else datetime.fromtimestamp(int(t), tz=timezone.utc)
            for key, var, f in (("temp", "t", 1), ("dewp", "d", 1), ("wspd", "w", 1.852), ("slp", "slp", 1), ("altim", "qnh", 1)):
                v = m.get(key)
                if isinstance(v, (int, float)):
                    con.execute("INSERT OR REPLACE INTO observation VALUES (?,?,?,?)", (sid, iso(when), var, v * f))
                    n += 1
            wx = str(m.get("wxString") or "")
            con.execute("INSERT OR REPLACE INTO observation VALUES (?,?,?,?)", (sid, iso(when), "wx", json.dumps(wx)))
    except Exception as err:  # noqa: BLE001
        log.warning("METAR reports failed: %s", type(err).__name__)
    for p in points:
        if p["truth"]["kind"] != "esoh":
            continue
        st = p["truth"]
        d = 0.01
        lat, lon = st["latitude"], st["longitude"]
        poly = (f"POLYGON(({lon-d:.4f} {lat-d:.4f},{lon+d:.4f} {lat-d:.4f},{lon+d:.4f} {lat+d:.4f},"
                f"{lon-d:.4f} {lat+d:.4f},{lon-d:.4f} {lat-d:.4f}))")
        source = f"esoh:{st['id']}"
        start = fetch_from(con, source, now, ESOH_RECENT, ESOH_KEEPS)  # 3 h missed recent data of some feeds
        _catching_up(f"E-SOH reports {st['id']}", start, now, ESOH_RECENT)
        try:
            data = await HTTP.get_json("https://observations.meteogate.eu/collections/observations/area", {
                "coords": poly, "datetime": iso(start) + "/.."})
            fetched(con, source, start, now)
            for cov in (data or {}).get("coverages") or []:
                if cov.get("metocean:wigosId") != st["id"]:
                    continue
                times = ((cov.get("domain") or {}).get("axes") or {}).get("t", {}).get("values") or []
                for name, rng in (cov.get("ranges") or {}).items():
                    for t, v in zip(times, rng.get("values") or []):
                        if isinstance(v, (int, float)):
                            con.execute("INSERT OR REPLACE INTO observation VALUES (?,?,?,?)", (st["id"], str(t), name, v))
                            n += 1
        except Exception as err:  # noqa: BLE001
            log.warning("E-SOH reports %s failed: %s", st["id"], type(err).__name__)
        await asyncio.sleep(1)
    con.commit()
    log.info("observations recorded: %d values", n)


# --- Bellweather's own log of local measurements -------------------------------------

async def home_log(points, now: datetime) -> None:
    """What a Home Assistant installation logs every update: the measured
    values Bellweather chooses near the address (official stations and
    airports), never the scoring station. Used for its correction once 20
    days are logged, exactly as in Home Assistant."""
    if now < HOME_LOG_SINCE:
        return
    sources = make_sources(["esoh", "metar"], HTTP)
    for point in points:
        loc = Location(point["latitude"], point["longitude"], point.get("elevation_m"))
        readings = []
        for source in sources:
            try:
                readings += await source.fetch(HTTP, loc, Precision.EXACT, now)
            except Exception as err:  # noqa: BLE001
                log.debug("home log %s %s: %s", point["id"], source.key, type(err).__name__)
        choices = fuse(readings, loc, now, Settings(excluded_stations=frozenset({point["truth"]["id"]})))
        engine = engine_for(point)
        engine.log.add(choices, now)
        save_engine(point, engine)
        await asyncio.sleep(1)
    log.info("home log updated for %d addresses", len(points))


# --- reports ----------------------------------------------------------------------------

def write_reports(now: datetime) -> None:
    """The fixed reports once their period is over, and a daily status report."""
    import report

    out = SHARE / "reports"
    out.mkdir(parents=True, exist_ok=True)
    for days in REPORTS:
        end = START + timedelta(days=days)
        path = out / f"{(end - timedelta(seconds=1)).astimezone(report.LOCAL):%Y-%m-%d}-{report.PERIOD_NAME[days].replace(' ', '-')}.md"
        if now >= end + REPORT_DELAY and not path.exists():
            path.write_text(report.build(DATA / "benchmark.sqlite", APP / "points.json", days, end), encoding="utf-8")
            log.info("report written: %s", path.name)
    days = (now - START).days
    if days >= 1:
        end = START + timedelta(days=days)
        (out / "status.md").write_text(report.build(DATA / "benchmark.sqlite", APP / "points.json", days, end), encoding="utf-8")


# --- schedule --------------------------------------------------------------------------

async def self_check(points) -> None:
    """One request to every provider for the first address; nothing is stored."""
    p = points[0]
    checks = {
        "open_meteo": open_meteo(p, None), "met_norway": met_norway(p, None), "dwd_mosmix": dwd_mosmix(p, None),
        "openweather": openweather(p, None),
        "metar": HTTP.get_json("https://aviationweather.gov/api/data/metar", {"ids": "KNYC", "hours": "1", "format": "json"}),
        "previous_runs": HTTP.get_json("https://previous-runs-api.open-meteo.com/v1/forecast", {
            "latitude": "40.7", "longitude": "-74.0", "hourly": "temperature_2m_previous_day1", "past_days": "1", "forecast_days": "0"}),
    }
    for name, call in checks.items():
        try:
            result = await call
            log.info("self-check %s: %s", name, "not configured" if result is None and name == "openweather"
                     else f"ok ({len(result)} items)" if isinstance(result, list) else "ok")
        except Exception as err:  # noqa: BLE001
            log.warning("self-check %s: %s", name, type(err).__name__ + (f" {err.status}" if isinstance(err, HttpStatusError) else ""))


async def main() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    points = json.loads((APP / "points.json").read_text(encoding="utf-8"))["points"]
    con = db()
    log.info("Bellweather benchmark %s: %d addresses, start %s", VERSION, len(points), iso(START))
    await self_check(points)
    done_capture: set[str] = {r[0] for r in con.execute("SELECT DISTINCT capture FROM forecast")}
    done_obs: set[str] = set()
    while True:
        now = datetime.now(timezone.utc)
        if now >= START:
            slot = now.replace(minute=0, second=0, microsecond=0)
            if slot.hour in CAPTURE_HOURS and iso(slot) not in done_capture and now - slot < timedelta(hours=1):
                done_capture.add(iso(slot))
                await capture(con, points, slot)
            obs_slot = iso(slot)
            if now.minute >= OBS_MINUTE and obs_slot not in done_obs:
                done_obs.add(obs_slot)
                await record_observations(con, points, now)
                await home_log(points, now)
                if slot.hour == 5:
                    try:
                        await asyncio.to_thread(write_reports, now)
                    except Exception as err:  # noqa: BLE001
                        log.warning("reports failed: %s", type(err).__name__)
        await asyncio.sleep(30)


if __name__ == "__main__":
    asyncio.run(main())
