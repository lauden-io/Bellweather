"""Benchmark report in GitHub Markdown (docs/benchmark.md).

    python report.py DB POINTS_JSON OUT_MD --days 7 [--end 2026-11-18T00:00]

The period ends at `--end` (default: the latest full UTC day) and covers the
given number of days. Two comparisons: all providers at the addresses where
DWD has a point within 25 km, and every provider except DWD at all addresses.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import score

LOCAL = timezone.utc
BENCHMARK_START = datetime(2026, 11, 10, 23, 0, tzinfo=timezone.utc)  # as in runner.py
CAPTURE_EVERY = 4  # hours
NAMES = {
    "bellweather": "**Bellweather**", "control": "*Bellweather without correction (control)*",
    "met_norway": "MET Norway (Yr)", "openweather": "OpenWeather", "open_meteo": "Open-Meteo",
    "dwd_mosmix": "DWD (MOSMIX)", "ecmwf_ifs": "ECMWF IFS",
}
CONTINENTS = {"EU": "Europe", "AS": "Asia", "NA": "North America", "SA": "South America", "AF": "Africa", "OC": "Oceania"}
VAR_HEAD = {"t": "Temperature", "d": "Dew point", "w": "Wind speed", "p": "Pressure"}
UNITS = {"t": "°C", "d": "°C", "w": "km/h", "p": "hPa"}
PERIOD_NAME = {7: "1 week", 14: "2 weeks", 30: "30 days", 91: "3 months", 182: "6 months", 365: "1 year"}


def fmt(v, unit="", digits=2, italic=False, bold=False):
    if v is None:
        return "–"
    text = f"{v:.{digits}f}" + (f" {unit}" if unit else "")
    if bold:
        text = f"**{text}**"
    if italic:
        text = f"*{text}*"
    return text


def best_of(values: dict[str, float | None], higher: bool = False) -> str | None:
    cands = {p: v for p, v in values.items() if v is not None and p != "control"}
    if not cands:
        return None
    return (max if higher else min)(cands, key=cands.get)


def table(rows: list[str], cols: list[tuple[str, dict[str, float | None], str, bool]], digits=2) -> list[str]:
    """cols: (heading, provider -> value, unit, higher_is_better)."""
    out = ["| | " + " | ".join(c[0] for c in cols) + " |", "|---|" + "---:|" * len(cols)]
    bests = [best_of(c[1], c[3]) for c in cols]
    for p in rows:
        cells = []
        for (head, values, unit, higher), best in zip(cols, bests):
            cells.append(fmt(values.get(p), unit, digits, italic=p == "control", bold=p == best))
        out.append(f"| {NAMES[p]} | " + " | ".join(cells) + " |")
    return out


def section(res: score.Result, title: str, places: int) -> list[str]:
    lines = [f"## {title}", ""]
    if not res.hours.get("t"):
        return lines + ["No paired hours yet.", ""]
    lines += [f"{places} addresses · {res.hours.get('t', 0):,} paired hours (temperature) · "
              f"{res.blocks:,} three-hour blocks, {res.wet_blocks:,} of them wet · {res.days} days for highs and lows", ""]
    day1 = res.mae["day 1"]
    cols = [(VAR_HEAD[v], {p: day1[v].mae(p) for p in res.providers}, UNITS[v], False) for v in score.VARS]
    cols.append(("Wet (CSI) ↑", {p: score.csi(res.csi[p]) for p in res.providers}, "", True))
    cols.append(("Rain chance (Brier)", {p: (sum(b) / len(b) if (b := res.brier.get(p)) else None) for p in res.providers}, "", False))
    cols.append(("Daily high", {p: res.daily_max.mae(p) for p in res.providers}, "°C", False))
    cols.append(("Daily low", {p: res.daily_min.mae(p) for p in res.providers}, "°C", False))
    lines += ["Forecast made the day before (day 1). Mean absolute error, lower is better, except the wet score. "
              "Best value in each column in bold; the control is not counted.", ""]
    lines += table(res.providers, cols) + [""]
    lines += ["### Temperature by lead time", ""]
    cols = [(lead, {p: res.mae[lead]["t"].mae(p) for p in res.providers}, "°C", False) for lead in score.LEADS]
    lines += table(res.providers, cols) + [""]
    lines += ["### Temperature by continent (day 1)", ""]
    groups = [g for g in CONTINENTS if g in res.by_group]
    cols = [(CONTINENTS[g], {p: res.by_group[g].mae(p) for p in res.providers}, "", False) for g in groups]
    lines += table(res.providers, cols) + ["", "In °C.", ""]
    return lines


def captures_line(start: datetime, end: datetime, recorded: set[str]) -> list[str]:
    """How many of the planned captures in the period were made, and which
    were missed. A missed capture is never made up later."""
    t = BENCHMARK_START + timedelta(hours=CAPTURE_EVERY * math.ceil(max(0.0, (start - BENCHMARK_START).total_seconds()) / 3600 / CAPTURE_EVERY))
    planned = []
    while t < end:
        planned.append(t)
        t += timedelta(hours=CAPTURE_EVERY)
    missed = [t for t in planned if score_iso(t) not in recorded]
    text = f"**Captures:** {len(planned) - len(missed)} of {len(planned)} made"
    if missed:
        text += (" (missed: " + ", ".join(f"{t.astimezone(LOCAL):%d %b %H:%M}" for t in missed) + " UTC). A missed capture is "
                 "never made up later, because a later one would see newer forecasts; every provider misses the same ones, so "
                 "the comparison stays fair, on fewer hours. Station reports missed during a gap are fetched again "
                 "automatically while the services still keep them.")
    return [text + ".", ""]


def build(db: Path, points_file: Path, days: int, end: datetime) -> str:
    con = sqlite3.connect(db)
    points = json.loads(points_file.read_text(encoding="utf-8"))["points"]
    start = end - timedelta(days=days)
    present = {r[0] for r in con.execute(
        "SELECT DISTINCT provider FROM forecast WHERE target >= ? AND target < ?", (score_iso(start), score_iso(end)))}
    recorded = {r[0] for r in con.execute("SELECT DISTINCT capture FROM forecast")}
    all_providers = [p for p in score.PROVIDERS if p in present]
    without_dwd = [p for p in all_providers if p != "dwd_mosmix"]
    near_dwd = [p for p in points if p["mosmix"]["distance_km"] <= score.DWD_MAX_KM]
    res_all = score.score(con, near_dwd, start, end, all_providers)
    res_rest = score.score(con, points, start, end, without_dwd)
    con.close()
    name = PERIOD_NAME.get(days, f"{days} days")
    first, last = start.astimezone(LOCAL), (end - timedelta(seconds=1)).astimezone(LOCAL)
    lines = [
        f"# Benchmark report: {name}", "",
        f"**Period:** {first:%d %B %Y} to {last:%d %B %Y} (UTC) · **Lead times:** next hours, day 1, 2 and 3 · "
        f"**Generated:** {datetime.now(timezone.utc).astimezone(LOCAL):%d %B %Y %H:%M}", "",
        "## How to read this", "",
        "Every number says how far a forecast was from what an official weather station then measured, averaged "
        "over every hour: lower is closer to reality. All are scored on the same hours with the same measurements; "
        "nobody saw the answer in advance. Details: [What \"more accurate\" means](../benchmark.md#what-more-accurate-means).", "",
        "Competitors are listed by how many Home Assistant installations use them (Home Assistant analytics, "
        "8 November 2026); ECMWF IFS is the weather model most providers build on, shown as a reference.", "",
    ]
    if days <= 7:
        lines += ["> [!NOTE]", "> One week is too short to judge: differences of a few hundredths are within chance.", ""]
    lines += captures_line(start, end, recorded)
    lines += section(res_all, "All providers (addresses with a DWD point within 25 km)", len(near_dwd))
    lines += section(res_rest, "All addresses, without DWD", len(points))
    lines += ["## Notes", "",
              "- **Bellweather** runs at each address as in Home Assistant, with the airport archive on (nearest airport within 60 km "
              "other than the scoring station). The **control** is the same models' plain mean.",
              "- **OpenWeather** gives 3-hourly values; when it takes part, only every third hour can be paired. Its dew point is "
              "computed from its temperature and humidity.",
              "- **Wet** compares 3-hour blocks (00, 03, … UTC): forecast wet when its amount for the block is at least 0.1 mm, "
              "observed wet when any report in the block had precipitation. CSI = hits / (hits + misses + false alarms), 1 is "
              "perfect. Brier: the highest rain probability of the block against what happened, 0 is perfect.",
              "- **Daily high and low** use local mean time days (longitude / 15 hours) and the latest forecast made at least "
              "6 hours before the day began that every compared provider answered.",
              "- Data: E-SOH and aviationweather.gov (observations); Open-Meteo.com, MET Norway, DWD (CC BY 4.0); OpenWeather (ODbL).",
              "", "Addresses, stations and the method: [docs/benchmark.md](../benchmark.md) and `benchmark/points.json`.", ""]
    return "\n".join(lines)


def score_iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("points")
    ap.add_argument("out")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--end", default=None)
    a = ap.parse_args()
    end = (datetime.fromisoformat(a.end).replace(tzinfo=timezone.utc) if a.end else
           datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0))
    Path(a.out).write_text(build(Path(a.db), Path(a.points), a.days, end), encoding="utf-8")


if __name__ == "__main__":
    main()
