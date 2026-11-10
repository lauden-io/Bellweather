"""Scoring for the Bellweather benchmark (docs/benchmark.md).

Truth: the scoring station's reports recorded by runner.py, per full hour
the report nearest to it within 20 minutes. Forecasts: per lead band, the
latest capture whose lead to the target hour falls in the band (captures are
4 hours apart and bands 4 hours wide, so there is exactly one). Pairing: an
hour counts only if every compared provider has a value for it.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

NEAR_HOUR = timedelta(minutes=20)
LEADS = {"next hours": (1, 5), "day 1": (24, 28), "day 2": (48, 52), "day 3": (72, 76)}
DAILY_LEAD_MIN = timedelta(hours=6)  # daily max/min: the latest capture at least 6 h before the local day starts
VARS = ("t", "d", "w", "p")
WET_MM = 0.1
BLOCK = 3  # hours: wet scores are on 3-hour blocks (00, 03, ... UTC), the step OpenWeather gives
_WET = re.compile(r"(DZ|RA|SN|SG|PL|GR|GS|UP)")
PROVIDERS = ["bellweather", "control", "met_norway", "openweather", "open_meteo", "dwd_mosmix", "ecmwf_ifs"]
DWD_MAX_KM = 25.0


def parse_time(text: str) -> datetime:
    return datetime.fromisoformat(str(text).replace("Z", "+00:00")).astimezone(timezone.utc)


def full_hour(t: datetime) -> datetime:
    return (t + timedelta(minutes=30)).replace(minute=0, second=0, microsecond=0)


# --- truth --------------------------------------------------------------------------

def _esoh_kind(name: str) -> tuple[str, int] | None:
    """(variable, preference) for an E-SOH series name; lower preference wins."""
    base = name.split(":")[0]
    method = name.split(":")[-2] if name.count(":") >= 3 else ""
    period = name.split(":")[-1]
    if base in ("air_temperature", "dew_point_temperature"):
        var = "t" if base == "air_temperature" else "d"
        order = {("point", "PT0S"): 0, ("mean", "PT1M"): 1, ("mean", "PT10M"): 2}
        rank = order.get((method, period))
        return (var, rank) if rank is not None else None
    if base == "wind_speed":
        order = {("mean", "PT10M"): 0, ("mean", "PT2M"): 1, ("mean", "PT1M"): 2, ("point", "PT0S"): 3}
        rank = order.get((method, period))
        return ("w", rank) if rank is not None else None
    if base == "air_pressure_at_mean_sea_level":
        return ("p", 0 if period == "PT0S" else 1)
    return None


def _esoh_value(var: str, v: float) -> float | None:
    if var in ("t", "d"):
        v = v - 273.15 if v > 150 else v
        return v if -90 <= v <= 60 else None
    if var == "w":
        v = v * 3.6  # m/s
        return v if 0 <= v <= 400 else None
    if var == "p":
        v = v / 100.0 if v > 2000 else v
        return v if 850 <= v <= 1090 else None
    return None


def truth(con: sqlite3.Connection, station: str, kind: str) -> tuple[dict[datetime, dict[str, float]], dict[datetime, bool]]:
    """Hourly values {hour: {t, d, w, p}} and wet hours {hour starting at key: bool}."""
    best: dict[tuple[datetime, str], tuple[float, float]] = {}  # (hour, var) -> (gap, value)
    wet: dict[datetime, bool] = {}
    rows = con.execute("SELECT time, var, value FROM observation WHERE station = ?", (station,)).fetchall()
    if kind == "metar":
        by_time: dict[datetime, dict[str, object]] = defaultdict(dict)
        for t, var, value in rows:
            by_time[parse_time(t)][var] = value
        for t, values in by_time.items():
            h, gap = full_hour(t), abs((t - full_hour(t)).total_seconds())
            pressure = values.get("slp") if isinstance(values.get("slp"), (int, float)) else values.get("qnh")
            for var, v in (("t", values.get("t")), ("d", values.get("d")), ("w", values.get("w")), ("p", pressure)):
                if isinstance(v, (int, float)) and gap <= NEAR_HOUR.total_seconds() and gap < best.get((h, var), (1e9, 0))[0]:
                    if var == "p" and not 850 <= v <= 1090:
                        continue
                    best[(h, var)] = (gap, float(v))
            wx = values.get("wx")
            if isinstance(wx, str):
                try:
                    wx = json.loads(wx)
                except ValueError:
                    pass
                tokens = str(wx).split()
                said_wet = any(_WET.search(tok) and not tok.startswith("VC") for tok in tokens)
                slot = t.replace(minute=0, second=0, microsecond=0)
                wet[slot] = wet.get(slot, False) or said_wet
    else:
        ranked: dict[tuple[datetime, str], tuple[int, float, float]] = {}
        for t, name, value in rows:
            if not isinstance(value, (int, float)):
                continue
            when = parse_time(t)
            if name.startswith("precipitation_amount:") and name.endswith(":sum:PT1H"):
                start = when.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)  # the sum of the hour that ends at the stamp
                wet[start] = wet.get(start, False) or value >= WET_MM
                continue
            kind_ = _esoh_kind(name)
            if kind_ is None:
                continue
            var, rank = kind_
            v = _esoh_value(var, float(value))
            h, gap = full_hour(when), abs((when - full_hour(when)).total_seconds())
            if v is None or gap > NEAR_HOUR.total_seconds():
                continue
            key = (h, var)
            if key not in ranked or (rank, gap) < ranked[key][:2]:
                ranked[key] = (rank, gap, v)
        best = {k: (r[1], r[2]) for k, r in ranked.items()}
    hourly: dict[datetime, dict[str, float]] = defaultdict(dict)
    for (h, var), (_, v) in best.items():
        hourly[h][var] = v
    return dict(hourly), wet


# --- forecasts --------------------------------------------------------------------------

def forecasts(con: sqlite3.Connection, point: str) -> dict[tuple[str, str, datetime], list[tuple[datetime, float]]]:
    """(provider, var, target) -> [(capture, value)], sorted by capture."""
    out: dict[tuple[str, str, datetime], list[tuple[datetime, float]]] = defaultdict(list)
    for capture, provider, target, var, value in con.execute(
            "SELECT capture, provider, target, var, value FROM forecast WHERE point = ?", (point,)):
        out[(provider, var, parse_time(target))].append((parse_time(capture), float(value)))
    for rows in out.values():
        rows.sort()
    return out


def at_lead(rows: list[tuple[datetime, float]], target: datetime, lo: float, hi: float) -> float | None:
    """The latest capture whose lead to `target` lies in [lo, hi) hours."""
    chosen = None
    for capture, value in rows:
        lead = (target - capture).total_seconds() / 3600
        if lo <= lead < hi:
            chosen = value
    return chosen


# --- scores -----------------------------------------------------------------------------

@dataclass
class Errors:
    abs_err: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))

    def add(self, provider: str, err: float) -> None:
        self.abs_err[provider].append(abs(err))

    def mae(self, provider: str) -> float | None:
        v = self.abs_err.get(provider)
        return sum(v) / len(v) if v else None


@dataclass
class Result:
    providers: list[str]
    hours: dict[str, int] = field(default_factory=dict)  # per variable, paired hours
    mae: dict[str, dict[str, Errors]] = field(default_factory=lambda: defaultdict(lambda: defaultdict(Errors)))  # lead -> var
    csi: dict[str, list[int]] = field(default_factory=lambda: defaultdict(lambda: [0, 0, 0]))  # provider -> hits, misses, false alarms
    brier: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    blocks: int = 0
    wet_blocks: int = 0
    daily_max: Errors = field(default_factory=Errors)
    daily_min: Errors = field(default_factory=Errors)
    days: int = 0
    by_group: dict[str, Errors] = field(default_factory=lambda: defaultdict(Errors))  # group -> temperature day 1


def eligible(point: dict, provider: str, present: set[str]) -> bool:
    if provider not in present:
        return False
    if provider == "dwd_mosmix":
        return (point.get("mosmix") or {}).get("distance_km", 1e9) <= DWD_MAX_KM
    return True


def score(con: sqlite3.Connection, points: list[dict], start: datetime, end: datetime, providers: list[str],
          group_of=lambda p: p["continent"]) -> Result:
    """Scores for target hours in [start, end). `providers` are compared; a
    point counts only if all of them are eligible there."""
    res = Result(providers=providers)
    hour_counts: dict[str, int] = defaultdict(int)
    for point in points:
        fc = forecasts(con, point["id"])
        present = {k[0] for k in fc}
        if not all(eligible(point, p, present) for p in providers):
            continue
        temps_by_capture: dict[str, dict[datetime, dict[datetime, float]]] = {p: defaultdict(dict) for p in providers}
        for (pp, var, h), rows in fc.items():
            if var == "t" and pp in temps_by_capture:
                for c, v in rows:
                    temps_by_capture[pp][c][h] = v
        hourly, wet = truth(con, point["truth"]["id"], point["truth"]["kind"])
        # hourly values
        for lead, (lo, hi) in LEADS.items():
            for h in sorted(hourly):
                if not start <= h < end:
                    continue
                for var in VARS:
                    o = hourly[h].get(var)
                    if o is None:
                        continue
                    values = {p: at_lead(fc.get((p, var, h), []), h, lo, hi) for p in providers}
                    if any(v is None for v in values.values()):
                        continue
                    for p, v in values.items():
                        res.mae[lead][var].add(p, v - o)
                    if lead == "day 1":
                        hour_counts[var] += 1
                        if var == "t":
                            for p, v in values.items():
                                res.by_group[group_of(point)].add(p, v - o)
        # wet 3-hour blocks, day 1
        lo, hi = LEADS["day 1"]
        t = start.replace(hour=(start.hour // BLOCK) * BLOCK, minute=0, second=0, microsecond=0)
        while t < end:
            hours = [t + timedelta(hours=i) for i in range(BLOCK)]
            seen = [wet[h] for h in hours if h in wet]
            if len(seen) >= 2:
                observed = any(seen)
                said: dict[str, bool] = {}
                probs: dict[str, float | None] = {}
                for p in providers:
                    amounts = [at_lead(fc.get((p, "r", h), []), h, lo, hi) for h in hours]
                    r3 = at_lead(fc.get((p, "r3", t), []), t, lo, hi)
                    if all(a is not None for a in amounts):
                        said[p] = sum(amounts) >= WET_MM
                    elif r3 is not None:
                        said[p] = r3 >= WET_MM
                    pops = [at_lead(fc.get((p, "pop", h), []), h, lo, hi) for h in hours]
                    pops = [x for x in pops if x is not None]
                    probs[p] = max(pops) / 100.0 if pops else None
                if len(said) == len(providers):
                    res.blocks += 1
                    res.wet_blocks += observed
                    for p in providers:
                        c = res.csi[p]
                        if said[p] and observed:
                            c[0] += 1
                        elif observed:
                            c[1] += 1
                        elif said[p]:
                            c[2] += 1
                        if probs[p] is not None:
                            res.brier[p].append((probs[p] - float(observed)) ** 2)
            t += timedelta(hours=BLOCK)
        # daily max/min on local mean time days
        offset = timedelta(hours=round(point["longitude"] / 15))
        day = (start + offset).date()
        while datetime(day.year, day.month, day.day, tzinfo=timezone.utc) - offset < end:
            day_start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) - offset
            day_end = day_start + timedelta(days=1)
            if day_start >= start and day_end <= end:
                obs = [hourly[h]["t"] for h in hourly if day_start <= h < day_end and "t" in hourly[h]]
                if len(obs) >= 20:
                    # the latest capture that every provider answered for this day, so all
                    # are judged at the same lead even when one of them failed later
                    common = None
                    for p in providers:
                        ok = {c for c, vals in temps_by_capture[p].items() if c <= day_start - DAILY_LEAD_MIN
                              and sum(1 for h in vals if day_start <= h < day_end) >= 7}  # hourly gives 24, 3-hourly 8
                        common = ok if common is None else common & ok
                    highs, lows = {}, {}
                    if common:
                        cap = max(common)
                        for p in providers:
                            vals = [v for h, v in temps_by_capture[p][cap].items() if day_start <= h < day_end]
                            highs[p], lows[p] = max(vals), min(vals)
                    if highs and len(highs) == len(providers):
                        res.days += 1
                        for p in providers:
                            res.daily_max.add(p, highs[p] - max(obs))
                            res.daily_min.add(p, lows[p] - min(obs))
            day += timedelta(days=1)
    res.hours = dict(hour_counts)
    return res


def csi(c: list[int]) -> float | None:
    return c[0] / sum(c) if sum(c) else None


def round_or_none(v: float | None, digits: int = 2) -> float | None:
    return None if v is None or not math.isfinite(v) else round(v, digits)


_ = date
