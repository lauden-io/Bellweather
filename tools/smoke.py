"""Run every source, the nowcast and the forecast against the live services
for one point, without Home Assistant, and print the result.

    python tools/smoke.py LAT LON [ELEVATION_M] [PRECISION] [--forecast] [--archive ICAO]

The point is sent out exactly as the chosen precision allows, the same way
the integration does it. Use a public test point, never a home.

The HTTP client follows the same rules as the integration (core/safety.py):
https only, no redirects, a size cap per response, errors without URLs.
"""

from __future__ import annotations

import asyncio
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "bellweather"))

from core.forecast.engine import Airport, ForecastEngine  # noqa: E402
from core.forecast.met_nowcast import MetNowcast  # noqa: E402
from core.fusion import fuse  # noqa: E402
from core.model import Field, Kind, Location  # noqa: E402
from core.privacy import Precision  # noqa: E402
from core.safety import (  # noqa: E402
    MAX_IMAGE_BYTES, MAX_JSON_BYTES, MAX_TEXT_BYTES, HttpStatusError, UnsafeResponse, check_url, safe_error,
)
from core.sources import DEFAULT_SOURCES, make_sources  # noqa: E402

UA = {"User-Agent": "bellweather-smoke/0.0.1 (development test)"}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        raise HttpStatusError(code)


_OPENER = urllib.request.build_opener(_NoRedirect)


class UrllibHttp:
    def _get(self, url: str, params: dict[str, str] | None, limit: int, allow_404: bool) -> bytes | None:
        check_url(url)
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        try:
            with _OPENER.open(urllib.request.Request(url, headers=UA), timeout=60) as resp:
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
        return None if data is None else json.loads(data)


async def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = sys.argv[1:]
    lat, lon = float(args[0]), float(args[1])
    elevation = float(args[2]) if len(args) > 2 else None
    precision = Precision(args[3]) if len(args) > 3 else Precision.KM_10
    archive = flags[flags.index("--archive") + 1] if "--archive" in flags else None
    location = Location(lat, lon, elevation)
    now = datetime.now(timezone.utc)
    http = UrllibHttp()
    sources = make_sources(DEFAULT_SOURCES, http)
    results = await asyncio.gather(*(s.fetch(http, location, precision, now) for s in sources), return_exceptions=True)
    readings = []
    for s, res in zip(sources, results, strict=True):
        if isinstance(res, BaseException):
            print(f"{s.key:11} ERROR {safe_error(res)}")
            continue
        print(f"{s.key:11} {len(res)} readings")
        readings.extend(res)
    print()
    fused = fuse(readings, location, now)
    for field, choice in fused.items():
        rd = choice.reading
        age = int((now - rd.observed).total_seconds() // 60)
        where = f"{rd.station or '-'}, {rd.distance_km} km" if rd.distance_km is not None else (rd.station or "-")
        value = str(rd.value)[:25]
        print(f"{field.value:20} {value:25} {rd.kind.value:9} {rd.source:11} {age:5} min  {where}  ({choice.candidates})")

    radar = next((s for s in sources if s.key == "rainviewer"), None)
    if radar is not None:
        print()
        try:
            cast = await MetNowcast().fetch(http, location, precision, now)
            if cast is None:
                cast = await radar.nowcast(http, location, now)
        except Exception as err:  # noqa: BLE001
            print(f"nowcast ERROR {safe_error(err)}")
            cast = None
        if cast is None:
            print("nowcast: none (no radar coverage or too few frames)")
        else:
            wet = bool(fused.get(Field.RAIN_NOW) and fused[Field.RAIN_NOW].reading.value)
            print(f"nowcast {cast.method}: motion {cast.motion_kmh} km/h towards {cast.motion_towards}, horizon {cast.horizon} min")
            print("   " + "  ".join(f"+{s.minutes}:{s.rate}mm/h,{int(s.probability * 100)}%" for s in cast.steps))
            print(f"   next hour {cast.next_hour_mm()} mm, starts in {cast.starts_in(wet)}, stops in {cast.stops_in(wet)}")

    if "--forecast" in flags:
        print()
        engine = ForecastEngine()
        airport = None
        if archive:
            ap = next((r for r in readings if r.kind is Kind.AIRPORT and r.station_id == archive), None)
            if ap is None or ap.latitude is None:
                print(f"archive airport {archive} not among the nearby airports")
            else:
                airport = Airport(archive, ap.latitude, ap.longitude, ap.distance_km)
        await engine.verify(http, location, precision, now, airport)
        v = engine.verification
        print(f"verification: corrections active t={bool(v.corrections.temperature)} w={bool(v.corrections.wind)}, "
              f"days {v.corrections.days}, source {v.corrections.source}, archive {v.airport}")
        for name, sc in (("temperature", v.temperature), ("wind", v.wind)):
            if sc and sc.blend_mae is not None:
                best = min(sc.member_mae.items(), key=lambda kv: kv[1])
                print(f"   {name}: blend MAE {sc.blend_mae:.2f}, plain mean {sc.plain_mean_mae:.2f}, best member {best[0]} {best[1]:.2f}, "
                      f"ECMWF IFS {sc.member_mae.get('ecmwf_ifs025', float('nan')):.2f} ({sc.hours} h, {sc.days} days)")
        if v.wet and v.wet.hours:
            print(f"   wet hours: blend CSI {v.wet.csi.get('blend', float('nan')):.2f}, ECMWF IFS {v.wet.csi.get('ecmwf_ifs025', float('nan')):.2f}, "
                  f"{v.wet.wet_hours} wet of {v.wet.hours} h")
        await engine.forecast(http, location, precision, now, timezone.utc)
        print(f"forecast: {len(engine.members_used)} members, {len(engine.hours)} hours, {len(engine.days)} days")
        for h in engine.hours[:6]:
            print(f"   {h.time:%d %H:%M} {h.condition:15} {h.temperature:5}°C wind {h.wind_speed} gust {h.wind_gust} from {h.wind_bearing} "
                  f"precip {h.precipitation} mm ({h.precipitation_probability}%) cloud {h.cloud_cover}%")
        for d in engine.days[:7]:
            print(f"   {d.day} {d.condition:15} {d.templow}..{d.temperature}°C precip {d.precipitation} mm ({d.precipitation_probability}%) "
                  f"gust {d.wind_gust} members {d.members}")


if __name__ == "__main__":
    asyncio.run(main())
