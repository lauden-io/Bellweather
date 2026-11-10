"""Draw the benchmark's random addresses (docs/benchmark.md).

    python -I benchmark/select_points.py DATA_DIR OUT_JSON

DATA_DIR holds the public inputs, downloaded as they were on the day of the
draw: GeoNames cities15000.txt and countryInfo.txt (CC BY 4.0), the
aviationweather.gov station cache (stations.cache.json.gz), the E-SOH
station list (collections/observations/locations) and DWD's MOSMIX station
catalogue. The seed is fixed, so the same inputs give the same list.

Rules:
- per continent, draw a town with a chance in proportion to its population,
  then a uniform random point up to 3 km from its centre;
- keep it if an official station that reports air temperature lies within
  5 km and within 50 m of height (heights of both from the same elevation
  model, Open-Meteo / Copernicus DEM, so they compare like with like);
- the station must have reported at least 20 hours of the last 24;
- 4 addresses per continent; a station is used once.
"""

from __future__ import annotations

import gzip
import json
import math
import random
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

SEED = 20261111
PER_CONTINENT = 4
CONTINENTS = ["EU", "AS", "NA", "SA", "AF", "OC"]
RADIUS_KM = 3.0
STATION_KM = 5.0
HEIGHT_M = 50.0
UA = {"User-Agent": "bellweather-benchmark/0.1 (point selection; open-source project)"}


def km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def get_json(url, params=None):
    full = url + ("?" + urllib.parse.urlencode(params) if params else "")
    with urllib.request.urlopen(urllib.request.Request(full, headers=UA), timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def load(data: Path):
    continent = {}
    for line in (data / "countryInfo.txt").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            cols = line.split("\t")
            continent[cols[0]] = cols[8]
    towns = {c: [] for c in CONTINENTS}
    for line in (data / "cities15000.txt").read_text(encoding="utf-8").splitlines():
        cols = line.split("\t")
        name, lat, lon, cc, pop = cols[1], float(cols[4]), float(cols[5]), cols[8], int(cols[14] or 0)
        cont = continent.get(cc)
        if cont in towns and pop >= 15000:
            towns[cont].append((name, cc, lat, lon, pop))
    stations = []
    for s in json.load(gzip.open(data / "stations.cache.json.gz")):
        if s.get("icaoId") and "METAR" in (s.get("siteType") or []):
            stations.append({"kind": "metar", "id": s["icaoId"], "name": s.get("site") or s["icaoId"],
                             "lat": float(s["lat"]), "lon": float(s["lon"])})
    esoh = json.loads((data / "esoh_locations.json").read_text(encoding="utf-8"))
    for f in esoh.get("features", []):
        params = f.get("properties", {}).get("parameter-name") or []
        if any(p.startswith("air_temperature:") for p in params):
            lon, lat = f["geometry"]["coordinates"][:2]
            stations.append({"kind": "esoh", "id": f["id"], "name": f["properties"].get("name") or f["id"],
                             "lat": float(lat), "lon": float(lon)})
    mosmix = []
    for line in (data / "mosmix_catalog.cfg").read_text(encoding="latin-1").splitlines()[2:]:
        parts = line.split()
        if len(parts) < 5:
            continue
        try:
            sid, la, lo = parts[0], float(parts[-3]), float(parts[-2])
        except ValueError:
            continue
        # The catalogue writes degrees.minutes (70.56 = 70° 56').
        def dm(v):
            d = int(abs(v))
            return math.copysign(d + (abs(v) - d) * 100 / 60, v)
        mosmix.append((sid, dm(la), dm(lo)))
    return towns, stations, mosmix


def reported_hours(st) -> int:
    """Hours of the last 24 with an air temperature from this station."""
    try:
        if st["kind"] == "metar":
            data = get_json("https://aviationweather.gov/api/data/metar", {"ids": st["id"], "hours": "24", "format": "json"})
            return len({str(m.get("reportTime", ""))[:13] for m in data if m.get("temp") is not None})
        d = 0.01
        lat, lon = st["lat"], st["lon"]
        poly = (f"POLYGON(({lon-d:.4f} {lat-d:.4f},{lon+d:.4f} {lat-d:.4f},{lon+d:.4f} {lat+d:.4f},"
                f"{lon-d:.4f} {lat+d:.4f},{lon-d:.4f} {lat-d:.4f}))")
        data = get_json("https://observations.meteogate.eu/collections/observations/area", {
            "coords": poly, "datetime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 86400)) + "/.."})
        hours = set()
        for cov in data.get("coverages", []):
            if cov.get("metocean:wigosId") != st["id"]:
                continue
            times = cov.get("domain", {}).get("axes", {}).get("t", {}).get("values") or []
            for name, rng in (cov.get("ranges") or {}).items():
                if name.startswith("air_temperature:"):
                    for t, v in zip(times, rng.get("values") or []):
                        if isinstance(v, (int, float)):
                            hours.add(str(t)[:13])
        return len(hours)
    except Exception:  # noqa: BLE001 - a station that does not answer is not used
        return 0


def heights(points):
    out = []
    for i in range(0, len(points), 90):
        chunk = points[i:i + 90]
        data = get_json("https://api.open-meteo.com/v1/elevation", {
            "latitude": ",".join(f"{p[0]:.4f}" for p in chunk), "longitude": ",".join(f"{p[1]:.4f}" for p in chunk)})
        out.extend(data.get("elevation") or [None] * len(chunk))
        time.sleep(1)
    return out


def main() -> None:
    data, out = Path(sys.argv[1]), Path(sys.argv[2])
    towns, stations, mosmix = load(data)
    rng = random.Random(SEED)
    chosen, used = [], set()
    for cont in CONTINENTS:
        pool = towns[cont]
        weights = [t[4] for t in pool]
        kept, tries = 0, 0
        while kept < PER_CONTINENT and tries < 4000:
            tries += 1
            name, cc, tlat, tlon, pop = rng.choices(pool, weights=weights, k=1)[0]
            r = RADIUS_KM * math.sqrt(rng.random())
            a = rng.random() * 2 * math.pi
            lat = tlat + (r * math.cos(a)) / 111.32
            lon = tlon + (r * math.sin(a)) / (111.32 * max(math.cos(math.radians(tlat)), 0.01))
            near = sorted(((km(lat, lon, s["lat"], s["lon"]), s) for s in stations
                           if abs(s["lat"] - lat) < 0.1 and (s["kind"], s["id"]) not in used), key=lambda x: x[0])
            near = [(d, s) for d, s in near if d <= STATION_KM]
            if not near:
                continue
            d, st = near[0]
            h_addr, h_st = heights([(lat, lon), (st["lat"], st["lon"])])
            if h_addr is None or h_st is None or abs(h_addr - h_st) > HEIGHT_M:
                continue
            hours = reported_hours(st)
            if hours < 20:
                continue
            mos = min(mosmix, key=lambda m: km(lat, lon, m[1], m[2]))
            used.add((st["kind"], st["id"]))
            kept += 1
            chosen.append({
                "id": f"{cont.lower()}{kept}", "continent": cont, "town": name, "country": cc, "town_population": pop,
                "latitude": round(lat, 4), "longitude": round(lon, 4), "elevation_m": round(h_addr),
                "truth": {"kind": st["kind"], "id": st["id"], "name": st["name"], "latitude": st["lat"], "longitude": st["lon"],
                          "distance_km": round(d, 2), "height_difference_m": round(h_st - h_addr), "hours_last_24": hours},
                "mosmix": {"id": mos[0], "distance_km": round(km(lat, lon, mos[1], mos[2]), 1)},
                "draw": tries,
            })
            print(cont, kept, name, cc, st["kind"], st["id"], f"{d:.1f} km", flush=True)
    out.write_text(json.dumps({"seed": SEED, "rules": __doc__, "points": chosen}, indent=1, ensure_ascii=False), encoding="utf-8")
    print("points:", len(chosen))


if __name__ == "__main__":
    main()
