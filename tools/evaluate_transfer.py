"""Does a correction learned at an airport help at another place nearby?

Bellweather learns each model's error where it has measurements: your own
sensor, a station or an airport near the home, or the airport archive. This
measures how much of an error learned somewhere else carries over to a
place a few kilometres away. Pairs: the benchmark's addresses whose scoring
station is an airport and which have an archive airport (benchmark/points.json).
For each pair, 120 days:

- the models' day-1 values at the address and at the archive airport
  (Open-Meteo Previous Runs), and both stations' reports (IEM archive);
- each day of the last 90 is scored at the scoring station with stats
  learned from the 30 days before it, as the live blend does (>= 20 days);
- variants: the plain mean; learned at the archive airport with its bias
  removed in full, three quarters, half, a quarter or not at all (the
  weights kept); learned at the scoring station itself (what a sensor at
  the home, placed like a weather station's, would give).

    python tools/evaluate_transfer.py t|w|d|pr CACHE_DIR

Public data only. Results of 2026-11-16 are in docs/forecast.md.
"""

import json
import statistics
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("evaluate_transfer_cache")
CACHE.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT / "custom_components" / "bellweather"))

from const import USER_AGENT  # noqa: E402
from core.forecast import skill  # noqa: E402
from core.forecast.history import IEM_URL, parse_iem  # noqa: E402
from core.forecast.members import DEFAULT_MODELS, PREVIOUS_RUNS_URL  # noqa: E402

VAR = sys.argv[1] if len(sys.argv) > 1 else "t"
START = datetime(2026, 7, 18, tzinfo=timezone.utc)
END = datetime(2026, 11, 16, tzinfo=timezone.utc)
SCORE_FROM = END - timedelta(days=90)
COLUMN = {"t": "temperature_2m_previous_day1", "w": "wind_speed_10m_previous_day1",
          "d": "dew_point_2m_previous_day1", "pr": "pressure_msl_previous_day1"}

points = json.loads((ROOT / "benchmark" / "points.json").read_text(encoding="utf-8"))["points"]
PAIRS = [p for p in points if p["truth"]["kind"] == "metar" and p.get("archive_airport")]


def get(url, params, name):
    path = CACHE / name
    if path.exists():
        return path.read_text(encoding="utf-8")
    req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params), headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as r:
        text = r.read().decode("utf-8")
        print(f"  fetched {name} cost={r.headers.get('X-RateLimit-Cost')}", file=sys.stderr)
    path.write_text(text, encoding="utf-8")
    return text


def previous(lat, lon, tag):
    base = {"latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}", "models": ",".join(DEFAULT_MODELS),
            "start_date": START.date().isoformat(), "end_date": (END - timedelta(days=1)).date().isoformat(), "timezone": "GMT"}
    if VAR == "t":
        text = get(PREVIOUS_RUNS_URL, {**base, "hourly": COLUMN["t"]}, f"prev_{tag}.json")
    else:
        text = get(PREVIOUS_RUNS_URL, {**base, "hourly": ",".join(COLUMN[v] for v in ("w", "d", "pr"))}, f"prev3_{tag}.json")
    hourly = json.loads(text)["hourly"]
    times = [datetime.fromisoformat(t).replace(tzinfo=timezone.utc) for t in hourly["time"]]
    out = {}
    for m in DEFAULT_MODELS:
        col = hourly.get(f"{COLUMN[VAR]}_{m}") or []
        rows = {t: {VAR: v} for t, v in zip(times, col) if v is not None}
        if rows:
            out[m] = rows
    return out


def iem(station):
    name = f"iem2_{station}.csv"  # with the station height, for the QNH conversion
    if not (CACHE / name).exists():
        time.sleep(10)
    text = get(IEM_URL, {"station": station, "data": "tmpc,sknt,dwpc,mslp,alti,wxcodes", "year1": START.year, "month1": START.month,
                         "day1": START.day, "year2": END.year, "month2": END.month, "day2": END.day, "tz": "Etc/UTC",
                         "format": "onlycomma", "latlon": "no", "elev": "yes", "missing": "M", "trace": "T", "report_type": "3,4"}, name)
    # parse_iem reads at most 6,720 rows (70 days at 4 reports an hour): parse in
    # overlapping chunks, and from each later chunk keep only the hours after its
    # first 200 rows, so every hour has its 12 hours of temperature behind it.
    lines = text.splitlines()
    header, body = lines[0], lines[1:]
    out = {}
    for i in range(0, len(body), 4800):
        chunk = body[i:i + 5000]
        if not chunk or (i > 0 and len(chunk) <= 200):
            break  # nothing new: the previous chunk already holds these rows
        floor = None
        if i > 0 and len(chunk) > 200:
            floor = datetime.strptime(chunk[200].split(",")[1][:16], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        for t, row in parse_iem("\n".join([header, *chunk])).items():
            if floor is not None and t < floor:
                continue
            if isinstance(row.get(VAR), (int, float)) and not isinstance(row.get(VAR), bool):
                out[t] = {VAR: row[VAR]}
    return out


def blend(values, stats, shrink=1.0):
    if not stats:
        return statistics.fmean(values.values())
    st = {m: skill.MemberStats(bias=s.bias * shrink, mse=s.mse - s.bias ** 2 + (s.bias * shrink) ** 2, mae=s.mae, hours=s.hours)
          for m, s in stats.items()}
    return skill.combine(values, st)


VARIANTS = {"plain": None, "air_x0": ("air", 0.0), "air_x.25": ("air", 0.25), "air_x.5": ("air", 0.5), "air_x.75": ("air", 0.75),
            "air_x1": ("air", 1.0), "own_x1": ("own", 1.0), "own_x.5": ("own", 0.5)}


def run_pair(p):
    a = p["archive_airport"]
    prev_home = previous(p["latitude"], p["longitude"], p["id"])
    prev_air = previous(a["latitude"], a["longitude"], f"{p['id']}_air")
    obs_truth = iem(p["truth"]["id"])
    obs_air = iem(a["icao"])
    err = defaultdict(list)
    active_any = False
    day = SCORE_FROM
    while day < END:
        nxt = day + timedelta(days=1)
        s_air, n_air = skill.member_stats(obs_air, prev_air, VAR, day)
        s_own, n_own = skill.member_stats(obs_truth, prev_home, VAR, day)
        stats = {"air": s_air if n_air >= skill.MIN_DAYS else {}, "own": s_own if n_own >= skill.MIN_DAYS else {}}
        active_any |= bool(stats["air"])
        for t, o in obs_truth.items():
            if not (day <= t < nxt):
                continue
            values = {m: rows[t][VAR] for m, rows in prev_home.items() if t in rows}
            if len(values) < len(prev_home) // 2 + 1:
                continue
            for name, how in VARIANTS.items():
                v = statistics.fmean(values.values()) if how is None else blend(values, stats[how[0]], how[1])
                err[name].append(abs(v - o[VAR]))
                if stats["air"]:
                    err["A:" + name].append(abs(v - o[VAR]))
        day = nxt
    return err, active_any


def main():
    total = defaultdict(list)
    names = list(VARIANTS)
    print(f"var={VAR}, day-1, MAE; 'A' = only hours with the airport's stats in force")
    print(f"{'pt':4} {'truth':5} {'air':5} {'km':>4} {'n':>5} " + " ".join(f"{v:>8}" for v in names))
    helped = hurt = 0
    for p in PAIRS:
        try:
            err, active = run_pair(p)
        except Exception as e:  # noqa: BLE001
            print(f"{p['id']}: failed {type(e).__name__} {e}")
            continue
        if len(err["plain"]) < 100:
            print(f"{p['id']}: only {len(err['plain'])} scored hours, skipped")
            continue
        for k, v in err.items():
            total[k] += v
        if err["A:plain"]:
            d = statistics.fmean(err["A:air_x1"]) - statistics.fmean(err["A:plain"])
            helped += d < 0
            hurt += d > 0
        print(f"{p['id']:4} {p['truth']['id']:5} {p['archive_airport']['icao']:5} {p['archive_airport']['distance_km']:4.0f} {len(err['plain']):5d} "
              + " ".join(f"{statistics.fmean(err[v]):8.3f}" for v in names) + ("" if active else "  (airport stats never in force)"))
    print(f"{'all':4} {'':5} {'':5} {'':4} {len(total['plain']):5d} " + " ".join(f"{statistics.fmean(total[v]):8.3f}" for v in names))
    if total["A:plain"]:
        print(f"{'A':4} {'':5} {'':5} {'':4} {len(total['A:plain']):5d} " + " ".join(f"{statistics.fmean(total['A:' + v]):8.3f}" for v in names))
        print(f"airport full correction vs plain, per pair: helped {helped}, hurt {hurt}")


main()
