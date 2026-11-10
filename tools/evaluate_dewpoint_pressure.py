"""Would correcting dew point and sea-level pressure the way temperature is
corrected (30-day bias removed, 1/variance weights, only with >= 20 days)
beat the plain mean Bellweather uses for them now? Out of sample, day-1 run,
public airports, 90 days; the method of tools/evaluate_forecasts.py.

    python tools/evaluate_dewpoint_pressure.py CACHE_DIR

Public airports only. Results of 2026-11-10 are in docs/forecast.md.
"""
import csv, io, json, os, statistics, sys, time, urllib.parse, urllib.request
import datetime as dt

UA = {"User-Agent": "bellweather-dev/0.0.1 (forecast evaluation)"}
OUT = sys.argv[1]
os.makedirs(OUT, exist_ok=True)
END = dt.date(2026, 11, 9)
START = END - dt.timedelta(days=89)
MODELS = ["ecmwf_ifs025", "ecmwf_aifs025_single", "icon_seamless", "gfs_seamless", "meteofrance_seamless", "ukmo_seamless",
          "knmi_seamless", "dmi_seamless", "metno_seamless", "gem_seamless", "jma_seamless"]
AIRPORTS = [  # public airports: the 6 of the first test, plus 4 outside Europe
    ("LFPB", 48.9694, 2.4414), ("ENGM", 60.1939, 11.1004), ("LOWW", 48.1103, 16.5697), ("KORD", 41.9786, -87.9048),
    ("EGLL", 51.4775, -0.4614), ("LEMD", 40.4719, -3.5626), ("YSSY", -33.9461, 151.1772), ("WSSS", 1.3502, 103.9940),
    ("RJTT", 35.5523, 139.7798), ("OMDB", 25.2528, 55.3644),
]
_last_iem = [0.0]


def fetch(url, params, name, iem=False):
    path = os.path.join(OUT, name)
    if not os.path.exists(path):
        if iem:
            wait = _last_iem[0] + 12 - time.monotonic()
            if wait > 0:
                time.sleep(wait)
        with urllib.request.urlopen(urllib.request.Request(url + "?" + urllib.parse.urlencode(params, doseq=True), headers=UA), timeout=180) as r:
            data = r.read()
        if iem:
            _last_iem[0] = time.monotonic()
        open(path, "wb").write(data)
    return open(path, "rb").read()


def run(icao, lat, lon):
    last = END + dt.timedelta(days=1)
    raw = fetch("https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py", {
        "station": icao, "data": ["dwpc", "alti", "mslp"], "year1": START.year, "month1": START.month, "day1": START.day,
        "year2": last.year, "month2": last.month, "day2": last.day, "tz": "Etc/UTC", "format": "onlycomma", "latlon": "no",
        "missing": "M", "report_type": [3, 4]}, f"{icao}_dp.csv", iem=True).decode()
    D, P, gapd, gapp = {}, {}, {}, {}
    for row in csv.DictReader(io.StringIO(raw)):
        t = dt.datetime.strptime(row["valid"], "%Y-%m-%d %H:%M").replace(tzinfo=dt.UTC)
        h = (t + dt.timedelta(minutes=30)).replace(minute=0)
        gap = abs((t - h).total_seconds())
        if gap > 1200:
            continue
        if row["dwpc"] not in ("M", "") and gap < gapd.get(h, 1e9):
            D[h], gapd[h] = float(row["dwpc"]), gap
        pressure = None
        if row.get("mslp") not in (None, "M", ""):
            pressure = float(row["mslp"])
        elif row.get("alti") not in (None, "M", ""):
            pressure = float(row["alti"]) * 33.8639  # QNH, inches of mercury to hPa
        if pressure is not None and 850 <= pressure <= 1090 and gap < gapp.get(h, 1e9):
            P[h], gapp[h] = pressure, gap
    fc_raw = json.loads(fetch("https://previous-runs-api.open-meteo.com/v1/forecast", {
        "latitude": lat, "longitude": lon, "hourly": "dew_point_2m_previous_day1,pressure_msl_previous_day1",
        "models": ",".join(MODELS), "start_date": START.isoformat(), "end_date": END.isoformat(), "timezone": "GMT"}, f"{icao}_dpfc.json"))
    times = [dt.datetime.fromisoformat(t).replace(tzinfo=dt.UTC) for t in fc_raw["hourly"]["time"]]

    def F(var, m):
        return dict(zip(times, fc_raw["hourly"].get(f"{var}_previous_day1_{m}") or []))

    out = {}
    for var, obs in (("dew_point_2m", D), ("pressure_msl", P)):
        fc = {m: F(var, m) for m in MODELS}
        ok = [m for m in MODELS if sum(v is not None for v in fc[m].values()) > len(times) * 0.9]
        hours = [t for t in times if t in obs and all(fc[m].get(t) is not None for m in ok)]
        days = sorted({t.date() for t in hours})
        res = {"IFS": [], "equal_mean": [], "bias_weights": []}
        for d in days[30:]:
            train = [t for t in hours if d - dt.timedelta(days=30) <= t.date() < d]
            test = [t for t in hours if t.date() == d]
            if len({t.date() for t in train}) < 20 or not test or "ecmwf_ifs025" not in ok:
                continue
            bias = {m: statistics.fmean(fc[m][t] - obs[t] for t in train) for m in ok}
            var_ = {m: max(statistics.fmean((fc[m][t] - bias[m] - obs[t]) ** 2 for t in train), 1e-6) for m in ok}
            for t in test:
                o = obs[t]
                res["IFS"].append(abs(fc["ecmwf_ifs025"][t] - o))
                res["equal_mean"].append(abs(statistics.fmean(fc[m][t] for m in ok) - o))
                res["bias_weights"].append(abs(sum((fc[m][t] - bias[m]) / var_[m] for m in ok) / sum(1 / var_[m] for m in ok) - o))
        out[var] = {k: (round(statistics.fmean(v), 2) if v else None) for k, v in res.items()} | {"hours": len(res["IFS"]), "models": len(ok)}
    return out


results = {}
for icao, lat, lon in AIRPORTS:
    try:
        results[icao] = run(icao, lat, lon)
    except Exception as err:  # noqa: BLE001
        results[icao] = {"error": f"{type(err).__name__}: {err}"[:120]}
    print(icao, json.dumps(results[icao]), flush=True)
json.dump(results, open(os.path.join(OUT, "results.json"), "w"), indent=1)
