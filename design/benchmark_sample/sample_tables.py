"""Numbers for a SAMPLE benchmark report, from data already downloaded for
the earlier tests (6 public airports), plus Open-Meteo's default forecast
("best match") for the same days. Day-1 run, paired hours only.

Proxies (the real benchmark asks the products themselves):
- MET Norway: its model as served by Open-Meteo (metno_seamless);
- DWD: its ICON model as served by Open-Meteo (icon_seamless), not MOSMIX;
- OpenWeather: no past forecasts exist, so no numbers.
"""
import csv, io, json, os, re, statistics, time, urllib.parse, urllib.request
import datetime as dt

ROOT = os.path.dirname(os.path.abspath(__file__))  # expects dl_eval/ and dl_dewpress/ caches from tools/evaluate_*.py
EVAL, DP, BM = (os.path.join(ROOT, d) for d in ("dl_eval", "dl_dewpress", "dl_bestmatch"))
os.makedirs(BM, exist_ok=True)
UA = {"User-Agent": "bellweather-dev/0.0.1 (benchmark sample)"}
AIRPORTS = [("LFPB", "Paris", 48.9694, 2.4414), ("ENGM", "Oslo", 60.1939, 11.1004), ("LOWW", "Vienna", 48.1103, 16.5697),
            ("KORD", "Chicago", 41.9786, -87.9048), ("EGLL", "London", 51.4775, -0.4614), ("LEMD", "Madrid", 40.4719, -3.5626)]
MODELS = ["ecmwf_ifs025", "ecmwf_aifs025_single", "icon_seamless", "gfs_seamless", "meteofrance_seamless", "ukmo_seamless",
          "knmi_seamless", "dmi_seamless", "metno_seamless", "gem_seamless", "jma_seamless"]
END = dt.date(2026, 11, 8)
WINDOWS = {"7 days": (END - dt.timedelta(days=6), END), "30 days": (END - dt.timedelta(days=29), END)}
VARS = {"t": "temperature_2m", "w": "wind_speed_10m", "d": "dew_point_2m", "pr": "pressure_msl", "p": "precipitation"}


def utc(s):
    return dt.datetime.fromisoformat(s).replace(tzinfo=dt.UTC)


def nearest_hour(t):
    return (t + dt.timedelta(minutes=30)).replace(minute=0)


def load_obs(icao):
    obs = {k: {} for k in ("t", "w", "d", "pr")}
    wet = {}
    for row in csv.DictReader(io.StringIO(open(os.path.join(EVAL, f"{icao}_obs.csv"), encoding="utf-8").read())):
        t = dt.datetime.strptime(row["valid"], "%Y-%m-%d %H:%M").replace(tzinfo=dt.UTC)
        h = nearest_hour(t)
        if abs((t - h).total_seconds()) <= 1200:
            if row["tmpc"] not in ("M", ""):
                obs["t"][h] = float(row["tmpc"])
            if row["sknt"] not in ("M", ""):
                obs["w"][h] = float(row["sknt"]) * 1.852
        wx = row["wxcodes"] if row["wxcodes"] != "M" else ""
        w = any(re.search(r"(DZ|RA|SN|SG|PL|GR|GS|UP)", tok) and not tok.startswith("VC") for tok in wx.split())
        hp = t.replace(minute=0)
        wet[hp] = wet.get(hp, False) or w
    for row in csv.DictReader(io.StringIO(open(os.path.join(DP, f"{icao}_dp.csv"), encoding="utf-8").read())):
        t = dt.datetime.strptime(row["valid"], "%Y-%m-%d %H:%M").replace(tzinfo=dt.UTC)
        h = nearest_hour(t)
        if abs((t - h).total_seconds()) > 1200:
            continue
        if row["dwpc"] not in ("M", ""):
            obs["d"].setdefault(h, float(row["dwpc"]))
        p = float(row["mslp"]) if row.get("mslp") not in (None, "M", "") else (
            float(row["alti"]) * 33.8639 if row.get("alti") not in (None, "M", "") else None)
        if p is not None and 850 <= p <= 1090:
            obs["pr"].setdefault(h, p)
    return obs, wet


def load_models(icao):
    fc = {k: {m: {} for m in MODELS} for k in VARS}
    for name, keys in ((f"{icao}_fc.json", ("t", "w", "p")), (f"{icao}_dpfc.json", ("d", "pr"))):
        data = json.load(open(os.path.join(EVAL if name.endswith("_fc.json") else DP, name), encoding="utf-8"))["hourly"]
        times = [utc(s) for s in data["time"]]
        for k in keys:
            for m in MODELS:
                for t, v in zip(times, data.get(f"{VARS[k]}_previous_day1_{m}") or []):
                    if v is not None:
                        fc[k][m][t] = v
    return fc


def load_best_match(icao, lat, lon):
    path = os.path.join(BM, f"{icao}.json")
    if not os.path.exists(path):
        start = WINDOWS["30 days"][0]
        params = {"latitude": lat, "longitude": lon, "hourly": ",".join(f"{v}_previous_day1" for v in VARS.values()),
                  "start_date": start.isoformat(), "end_date": (END + dt.timedelta(days=1)).isoformat(), "timezone": "GMT"}
        with urllib.request.urlopen(urllib.request.Request(
                "https://previous-runs-api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params), headers=UA), timeout=120) as r:
            open(path, "wb").write(r.read())
        time.sleep(1)
    data = json.load(open(path, encoding="utf-8"))["hourly"]
    times = [utc(s) for s in data["time"]]
    return {k: {t: v for t, v in zip(times, data.get(f"{VARS[k]}_previous_day1") or []) if v is not None} for k in VARS}


def bellweather(fc, obs, k, t, ok):
    """Bias removed and 1/variance weights from the 30 days before t's day (>= 20 days of data)."""
    day = t.date()
    train = [h for h in obs[k] if day - dt.timedelta(days=30) <= h.date() < day and all(h in fc[k][m] for m in ok)]
    if len({h.date() for h in train}) < 20:
        return statistics.fmean(fc[k][m][t] for m in ok)
    bias = {m: statistics.fmean(fc[k][m][h] - obs[k][h] for h in train) for m in ok}
    var = {m: max(statistics.fmean((fc[k][m][h] - bias[m] - obs[k][h]) ** 2 for h in train), 1e-6) for m in ok}
    return sum((fc[k][m][t] - bias[m]) / var[m] for m in ok) / sum(1 / var[m] for m in ok)


PROVIDERS = ["Bellweather", "control", "MET Norway", "OpenWeather", "Open-Meteo", "DWD", "ECMWF IFS"]
results = {}
for wname, (a, b) in WINDOWS.items():
    errs = {k: {p: [] for p in PROVIDERS} for k in ("t", "w", "d", "pr")}
    per_airport = {}
    csi_counts = {p: [0, 0, 0] for p in PROVIDERS}
    for icao, city, lat, lon in AIRPORTS:
        obs, wet = load_obs(icao)
        fc = load_models(icao)
        bm = load_best_match(icao, lat, lon)
        cache: dict = {}
        per_airport[city] = {p: [] for p in PROVIDERS}
        for k in ("t", "w", "d", "pr"):
            ok = [m for m in MODELS if len(fc[k][m]) > 0.9 * len(fc["t"]["ecmwf_ifs025"] or [1])]
            for t in sorted(obs[k]):
                if not a <= t.date() <= b or t not in bm[k] or not all(t in fc[k][m] for m in ok) or "ecmwf_ifs025" not in ok:
                    continue
                o = obs[k][t]
                values = {
                    "Bellweather": bellweather(fc, obs, k, t, ok), "control": statistics.fmean(fc[k][m][t] for m in ok),
                    "MET Norway": fc[k]["metno_seamless"].get(t), "Open-Meteo": bm[k][t], "DWD": fc[k]["icon_seamless"].get(t),
                    "ECMWF IFS": fc[k]["ecmwf_ifs025"][t],
                }
                if any(v is None for v in values.values()):
                    continue
                for p, v in values.items():
                    errs[k][p].append(abs(v - o))
                    if k == "t":
                        per_airport[city][p].append(abs(v - o))
        # wet hours: Open-Meteo's amount at T is the hour before T
        okp = [m for m in MODELS if fc["p"][m]]
        for t in sorted(bm["p"]):
            hour = t - dt.timedelta(hours=1)
            if not a <= hour.date() <= b or hour not in wet or not all(t in fc["p"][m] for m in okp):
                continue
            share = sum(fc["p"][m][t] >= 0.1 for m in okp) / len(okp)
            said = {"Bellweather": share >= 0.5, "control": share >= 0.5,
                    "MET Norway": fc["p"]["metno_seamless"].get(t, 0) >= 0.1, "Open-Meteo": bm["p"][t] >= 0.1,
                    "DWD": fc["p"]["icon_seamless"].get(t, 0) >= 0.1, "ECMWF IFS": fc["p"]["ecmwf_ifs025"].get(t, 0) >= 0.1}
            for p, s in said.items():
                c = csi_counts[p]
                if s and wet[hour]:
                    c[0] += 1
                elif wet[hour]:
                    c[1] += 1
                elif s:
                    c[2] += 1
    results[wname] = {
        "hours": {k: len(errs[k]["Bellweather"]) for k in errs},
        "mae": {k: {p: (round(statistics.fmean(v), 2) if v else None) for p, v in errs[k].items()} for k in errs},
        "csi": {p: (round(c[0] / sum(c), 2) if sum(c) else None) for p, c in csi_counts.items()},
        "wet_hours": csi_counts["ECMWF IFS"][0] + csi_counts["ECMWF IFS"][1],
        "temperature_by_place": {city: {p: (round(statistics.fmean(v), 2) if v else None) for p, v in d.items()}
                                 for city, d in per_airport.items()},
    }
print(json.dumps(results, indent=1))
json.dump(results, open(os.path.join(ROOT, "sample_tables.json"), "w"), indent=1)
