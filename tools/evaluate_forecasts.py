"""Out-of-sample test of forecast strategies at one airport.

    python tools/evaluate_forecasts.py CACHE_DIR ICAO LAT LON [END_DATE]

- Observations: the Iowa Environmental Mesonet METAR archive, hourly.
- Forecasts: Open-Meteo Previous Runs, the run from one day earlier, 11 models.
- Rolling: each test day (days 31-90) uses only the 30 days before it.

Use public airports only. Results of 2026-11-09 are in docs/forecast.md.
"""
import csv, io, json, math, statistics, sys, urllib.parse, urllib.request, datetime as dt, re, os
UA = {"User-Agent": "bellweather-dev/0.0.1 (forecast evaluation)"}
OUT = sys.argv[1]
icao, lat, lon = sys.argv[2], float(sys.argv[3]), float(sys.argv[4])
END = dt.date.fromisoformat(sys.argv[5]) if len(sys.argv) > 5 else dt.date.today() - dt.timedelta(days=1)
START = END - dt.timedelta(days=89)
os.makedirs(OUT, exist_ok=True)
MODELS = ["ecmwf_ifs025","ecmwf_aifs025_single","icon_seamless","gfs_seamless","meteofrance_seamless","ukmo_seamless","knmi_seamless","dmi_seamless","metno_seamless","gem_seamless","jma_seamless"]
def fetch(url, params, name):
    path = os.path.join(OUT, name)
    if not os.path.exists(path):
        with urllib.request.urlopen(urllib.request.Request(url + "?" + urllib.parse.urlencode(params, doseq=True), headers=UA), timeout=180) as r:
            open(path, "wb").write(r.read())
    return open(path, "rb").read()
raw = fetch("https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py", {"station": icao, "data": ["tmpc", "sknt", "wxcodes"],
    "year1": START.year, "month1": START.month, "day1": START.day, "day2": (END + dt.timedelta(days=1)).day, "month2": (END + dt.timedelta(days=1)).month, "year2": (END + dt.timedelta(days=1)).year,
    "tz": "Etc/UTC", "format": "onlycomma", "latlon": "no", "missing": "M", "trace": "T", "report_type": [3, 4]}, f"{icao}_obs.csv").decode()
T, W, WET = {}, {}, {}
for row in csv.DictReader(io.StringIO(raw)):
    t = dt.datetime.strptime(row["valid"], "%Y-%m-%d %H:%M").replace(tzinfo=dt.UTC)
    h = (t + dt.timedelta(minutes=10)).replace(minute=0)  # :50 reports belong to the next hour
    if row["tmpc"] not in ("M", ""): T[h] = float(row["tmpc"])
    if row["sknt"] not in ("M", ""): W[h] = float(row["sknt"]) * 1.852
    wx = row["wxcodes"] if row["wxcodes"] not in ("M",) else ""
    w = any(re.search(r"(DZ|RA|SN|SG|PL|GR|GS|UP)", tok) and not tok.startswith("VC") for tok in wx.split())
    hp = t.replace(minute=0)  # precipitation: the hour the report falls in
    WET[hp] = WET.get(hp, False) or w
pr = json.loads(fetch("https://previous-runs-api.open-meteo.com/v1/forecast", {"latitude": lat, "longitude": lon,
    "hourly": "temperature_2m_previous_day1,wind_speed_10m_previous_day1,precipitation_previous_day1", "models": ",".join(MODELS),
    "start_date": START.isoformat(), "end_date": END.isoformat(), "timezone": "GMT"}, f"{icao}_fc.json"))
times = [dt.datetime.fromisoformat(t).replace(tzinfo=dt.UTC) for t in pr["hourly"]["time"]]
def F(var, m): return dict(zip(times, pr["hourly"].get(f"{var}_previous_day1_{m}") or []))
def evaluate(var, obs):
    fc = {m: F(var, m) for m in MODELS}
    ok = [m for m in MODELS if sum(v is not None for v in fc[m].values()) > len(times) * 0.9]
    hours = [t for t in times if t in obs and all(fc[m].get(t) is not None for m in ok)]
    days = sorted({t.date() for t in hours})
    test_days = days[30:]
    res = {k: [] for k in ["IFS", "best_model_30d", "equal_mean", "median", "mean_bias30", "invmse30", "invmse30_bias", "hindsight_best"]}
    per_model_test = {m: [] for m in ok}
    for d in test_days:
        train = [t for t in hours if d - dt.timedelta(days=30) <= t.date() < d]
        test = [t for t in hours if t.date() == d]
        if len(train) < 24 * 20 or not test: continue
        bias = {m: statistics.fmean(fc[m][t] - obs[t] for t in train) for m in ok}
        mse = {m: statistics.fmean((fc[m][t] - obs[t]) ** 2 for t in train) for m in ok}
        mae = {m: statistics.fmean(abs(fc[m][t] - obs[t]) for t in train) for m in ok}
        best = min(ok, key=lambda m: mae[m])
        w = {m: 1 / mse[m] for m in ok}; wb = {m: 1 / statistics.fmean((fc[m][t] - bias[m] - obs[t]) ** 2 for t in train) for m in ok}
        for t in test:
            o = obs[t]
            res["IFS"].append(abs(fc["ecmwf_ifs025"][t] - o))
            res["best_model_30d"].append(abs(fc[best][t] - o))
            res["equal_mean"].append(abs(statistics.fmean(fc[m][t] for m in ok) - o))
            res["median"].append(abs(statistics.median(fc[m][t] for m in ok) - o))
            res["mean_bias30"].append(abs(statistics.fmean(fc[m][t] - bias[m] for m in ok) - o))
            res["invmse30"].append(abs(sum(w[m] * fc[m][t] for m in ok) / sum(w.values()) - o))
            res["invmse30_bias"].append(abs(sum(wb[m] * (fc[m][t] - bias[m]) for m in ok) / sum(wb.values()) - o))
            for m in ok: per_model_test[m].append(abs(fc[m][t] - o))
    hb = min(ok, key=lambda m: statistics.fmean(per_model_test[m]))
    res["hindsight_best"] = per_model_test[hb]
    return {k: (statistics.fmean(v) if v else float("nan")) for k, v in res.items()}, hb, len(res["IFS"])
def precip():
    fc = {m: F("precipitation", m) for m in MODELS}
    ok = [m for m in MODELS if sum(v is not None for v in fc[m].values()) > len(times) * 0.9]
    # Open-Meteo precipitation at T covers the hour before T
    hours = [t for t in times if (t - dt.timedelta(hours=1)) in WET and all(fc[m].get(t) is not None for m in ok)]
    def score(pred):
        h = sum(1 for t in hours if pred(t) and WET[t - dt.timedelta(hours=1)]); mi = sum(1 for t in hours if not pred(t) and WET[t - dt.timedelta(hours=1)])
        fa = sum(1 for t in hours if pred(t) and not WET[t - dt.timedelta(hours=1)])
        return h / (h + mi + fa) if h + mi + fa else float("nan")
    out = {m: score(lambda t, m=m: fc[m][t] >= 0.1) for m in ok}
    for thr in (0.3, 0.5):
        out[f"ens>={int(thr*100)}%"] = score(lambda t, thr=thr: statistics.fmean(fc[m][t] >= 0.1 for m in ok) >= thr)
    wet = sum(WET[t - dt.timedelta(hours=1)] for t in hours)
    return out, wet, len(hours)
for var, obs, unit in (("temperature_2m", T, "C"), ("wind_speed_10m", W, "km/h")):
    r, hb, n = evaluate(var, obs)
    print(f"{icao} {var} MAE ({unit}) over {n} test hours; hindsight best = {hb}")
    print("   " + "  ".join(f"{k}={v:.2f}" for k, v in r.items()))
p, wet, n = precip()
best = sorted(p.items(), key=lambda kv: -kv[1])
print(f"{icao} precipitation CSI, {wet} wet of {n} hours: " + ", ".join(f"{k}={v:.2f}" for k, v in best[:6]) + f" ... IFS={p.get('ecmwf_ifs025', float('nan')):.2f}")
