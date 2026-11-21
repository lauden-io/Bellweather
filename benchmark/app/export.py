"""Raw data of the Bellweather benchmark, published with each report
(docs/benchmark.md, "How it runs").

    python export.py export DB POINTS_JSON OUT_DIR --since 2026-11-10T23:00 --until 2026-11-17T23:00
    python export.py load NEW_DB EXPORT_DIR [EXPORT_DIR ...]

An export holds, each file under the licence of the data in it:

- the forecasts of every capture made in [since, until). A capture is never
  changed or made up later, so the exports of successive reports add up to
  every capture;
- every scoring station report and every capture's outcome recorded up to
  the moment of the export (or `--recorded-until`). Station reports can
  arrive, or be fetched again, hours after their time, so each export holds
  all of them from the start.

`load` rebuilds a database from one or more exports, oldest first, after
checking every file against its export's MANIFEST.json; report.py then
scores it exactly as the add-on did.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

REPOSITORY = "https://github.com/lauden-io/Bellweather"
SCHEMA = """
    CREATE TABLE IF NOT EXISTS forecast (
        capture TEXT, point TEXT, provider TEXT, target TEXT, var TEXT, value REAL,
        PRIMARY KEY (capture, point, provider, target, var));
    CREATE TABLE IF NOT EXISTS observation (
        station TEXT, time TEXT, var TEXT, value REAL, PRIMARY KEY (station, time, var));
    CREATE TABLE IF NOT EXISTS run (time TEXT, kind TEXT, point TEXT, provider TEXT, status TEXT);
    CREATE TABLE IF NOT EXISTS fetched (source TEXT, start TEXT, until TEXT);
"""
COLUMNS = {
    "forecast": ("capture", "point", "provider", "target", "var", "value"),
    "observation": ("station", "time", "var", "value"),
    "run": ("time", "kind", "point", "provider", "status"),
}
CC_BY = "CC-BY-4.0"
ODBL = "ODbL-1.0"
NOAA = "public domain in the United States (NOAA)"
OPENWEATHER = "openweather"
ATTRIBUTION = """# Sources and licences

Raw data of the Bellweather benchmark
(https://github.com/lauden-io/Bellweather/blob/main/docs/benchmark.md). Each
file keeps the licence of the data in it; MANIFEST.json lists every file
with its licence, row count and SHA-256.

| File | What it holds | Licence |
|---|---|---|
| `forecasts.csv.gz` | every capture's forecasts from Bellweather, its control, ECMWF IFS, Open-Meteo, MET Norway and DWD | CC BY 4.0 |
| `forecasts-openweather.csv.gz` | every capture's OpenWeather forecasts | ODbL 1.0 |
| `observations-esoh.csv.gz` | the European scoring stations' reports | CC BY 4.0 |
| `observations-metar.csv.gz` | the airport scoring stations' reports (METAR) | public domain in the United States |
| `captures.csv.gz` | the outcome of every request at every capture | CC BY 4.0 |

## Attribution

- **Bellweather and its control:** computed by Bellweather from the weather
  model forecasts served by Open-Meteo.com (CC BY 4.0): ECMWF, DWD, NOAA,
  Météo-France, UK Met Office, KNMI, DMI, MET Norway, ECCC and JMA. Changed:
  blended; Bellweather's also corrected for the address.
- **ECMWF IFS** and **Open-Meteo**: weather data by Open-Meteo.com, CC BY
  4.0. In these and in the two above, precipitation is stored for the hour
  it fell in (Open-Meteo gives it at the hour's end).
- **MET Norway:** based on data from MET Norway (Locationforecast 2.0), CC BY
  4.0. Wind converted from m/s to km/h.
- **DWD:** based on data from Deutscher Wetterdienst (MOSMIX), CC BY 4.0,
  units converted (K to °C, m/s to km/h, Pa to hPa) and hourly precipitation
  stored for the hour it fell in.
- **OpenWeather:** weather data © OpenWeather (https://openweathermap.org/),
  5 day / 3 hour forecast, under the Open Database License 1.0. Changed: dew
  point computed from temperature and humidity, wind converted to km/h, rain
  and snow added up per 3 hours, probability given in per cent. The file is a
  database offered under the ODbL; whoever shares a database made from it
  shares that under the ODbL too.
- **European stations:** station reports of the national weather services
  from EUMETNET's E-SOH (MeteoGate observations collection,
  https://observations.meteogate.eu/), CC BY 4.0. Values as served.
- **Airports:** METAR reports as served by NOAA's Aviation Weather Center
  (https://aviationweather.gov/). NOAA's material is in the public domain
  (https://www.weather.gov/disclaimer); the reports come from the airports'
  operators and national services. This is not official NOAA material, and
  no endorsement is implied. Wind converted from knots to km/h.
- **Captures and the benchmark itself:** Bellweather benchmark, CC BY 4.0.

Licences: CC BY 4.0, https://creativecommons.org/licenses/by/4.0/ · ODbL 1.0,
https://opendatacommons.org/licenses/odbl/1-0/

## Columns

- **Forecasts:** `capture` (UTC, when every provider was asked), `point`
  (the address in `benchmark/points.json`), `provider`, `target` (UTC),
  `var`, `value`. Variables: `t` temperature °C, `d` dew point °C, `w` wind
  speed km/h, `p` sea-level pressure hPa, `r` precipitation in mm in the hour
  that starts at `target`, `r3` the same for 3 hours, `pop` chance of
  precipitation in per cent, `pww` DWD's MOSMIX element wwP as served (not
  scored).
- **Observations:** `station`, `time` (UTC), `var`, `value`. Airports: `t`
  and `d` °C, `w` km/h, `slp` sea-level pressure and `qnh` altimeter setting
  in hPa, `elev` the station height in m, `wx` the present weather codes (a
  JSON string). European stations: the E-SOH series names and values as
  served, in SI units.
- **Captures:** `time` (the capture, UTC), `kind` (`forecast`: one request;
  `method`: the method that capture used), `point`, `provider`, `status`
  (`ok` and the rows stored, `not configured`, or the error's type).
"""


def iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _write(path: Path, columns: tuple[str, ...], rows) -> int:
    """Gzipped CSV with a header row. The same rows give the same bytes: no
    time or name in the gzip header, rows in a fixed order, and floats
    written in their shortest exact form."""
    n = 0
    with open(path, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=6) as gz:
        text = io.TextIOWrapper(gz, encoding="utf-8", newline="")
        writer = csv.writer(text, lineterminator="\n")
        writer.writerow(columns)
        for row in rows:
            writer.writerow(row)
            n += 1
        text.flush()
        text.detach()
    return n


def export(db: Path, points: list[dict], out: Path, since: datetime, until: datetime,
           recorded_until: datetime | None = None, name: str | None = None, version: str | None = None,
           commit: str | None = None, clean: bool | None = None, note: str | None = None) -> dict:
    """Write the export for captures in [since, until) to `out`; returns the manifest."""
    now = datetime.now(timezone.utc)
    stations = {kind: sorted({p["truth"]["id"] for p in points if p["truth"]["kind"] == kind}) for kind in ("esoh", "metar")}
    upto = iso(recorded_until) if recorded_until else None
    work = out.parent / (out.name + ".partial")
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    con = sqlite3.connect(f"{Path(db).resolve().as_uri()}?mode=ro", uri=True)
    files = []
    try:
        con.execute("BEGIN")  # one snapshot for every table
        fc = ("SELECT capture, point, provider, target, var, value FROM forecast WHERE capture >= ? AND capture < ? "
              "AND provider {} ? ORDER BY capture, point, provider, target, var")

        def observations(ids):
            marks = ",".join("?" * len(ids))
            sql = f"SELECT station, time, var, value FROM observation WHERE station IN ({marks})"
            args = list(ids)
            if upto:
                sql += " AND time <= ?"
                args.append(upto)
            return con.execute(sql + " ORDER BY station, time, var", args) if ids else iter(())

        runs = "SELECT time, kind, point, provider, status FROM run" + (" WHERE time <= ?" if upto else "")
        plan = [
            ("forecasts.csv.gz", "forecast", con.execute(fc.format("!="), (iso(since), iso(until), OPENWEATHER)), CC_BY,
             "forecasts of Bellweather, its control, ECMWF IFS, Open-Meteo, MET Norway and DWD"),
            ("forecasts-openweather.csv.gz", "forecast", con.execute(fc.format("="), (iso(since), iso(until), OPENWEATHER)),
             ODBL, "forecasts of OpenWeather"),
            ("observations-esoh.csv.gz", "observation", observations(stations["esoh"]), CC_BY,
             "reports of the European scoring stations (E-SOH)"),
            ("observations-metar.csv.gz", "observation", observations(stations["metar"]), NOAA,
             "reports of the airport scoring stations (METAR, aviationweather.gov)"),
            ("captures.csv.gz", "run", con.execute(runs + " ORDER BY time, kind, point, provider, status",
                                                   (upto,) if upto else ()), CC_BY,
             "the outcome of every request at every capture"),
        ]
        for filename, table, rows, licence, contents in plan:
            n = _write(work / filename, COLUMNS[table], rows)
            files.append({"name": filename, "table": table, "rows": n, "bytes": (work / filename).stat().st_size,
                          "sha256": sha256(work / filename), "licence": licence, "contents": contents})
    finally:
        con.close()
    manifest = {
        "name": name or out.name,
        "benchmark": f"{REPOSITORY}/blob/main/docs/benchmark.md",
        "forecasts": {"captures_from": iso(since), "captures_before": iso(until)},
        "recorded_until": upto or iso(now),
        "exported": iso(now),
        "code": {"repository": REPOSITORY, "commit": commit, "clean": clean, "addon_version": version},
        "files": files,
    }
    if note:
        manifest["note"] = note
    (work / "MANIFEST.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (work / "ATTRIBUTION.md").write_text(ATTRIBUTION, encoding="utf-8")
    if out.exists():
        shutil.rmtree(out)
    work.rename(out)
    return manifest


def _value(text: str) -> float | str | None:
    if text == "":
        return None
    try:
        return float(text)
    except ValueError:
        return text  # an airport's weather codes


def load(db: Path, exports: list[Path]) -> dict[str, int]:
    """Rebuild a database from exports, oldest first; later station reports
    replace earlier ones. Every file must match its export's MANIFEST.json."""
    con = sqlite3.connect(db)
    con.executescript(SCHEMA)
    counts: dict[str, int] = {}
    seen_runs: set[tuple] = set()
    try:
        for folder in exports:
            manifest = json.loads((Path(folder) / "MANIFEST.json").read_text(encoding="utf-8"))
            for entry in manifest["files"]:
                path = Path(folder) / entry["name"]
                if sha256(path) != entry["sha256"]:
                    raise ValueError(f"{path.name} in {Path(folder).name} does not match its MANIFEST.json")
                table = entry["table"]
                with gzip.open(path, "rt", encoding="utf-8", newline="") as text:
                    reader = csv.reader(text)
                    if tuple(next(reader)) != COLUMNS[table]:
                        raise ValueError(f"{path.name}: unexpected columns")
                    if table == "run":
                        rows = []
                        for row in reader:
                            if tuple(row) not in seen_runs:  # every export holds the outcomes from the start
                                seen_runs.add(tuple(row))
                                rows.append(row)
                        con.executemany("INSERT INTO run VALUES (?,?,?,?,?)", rows)
                        n = len(rows)
                    else:
                        marks = ",".join("?" * len(COLUMNS[table]))
                        n = 0

                        def typed(reader=reader):
                            nonlocal n
                            for row in reader:
                                n += 1
                                yield (*row[:-1], _value(row[-1]))

                        con.executemany(f"INSERT OR REPLACE INTO {table} VALUES ({marks})", typed())
                counts[table] = counts.get(table, 0) + n
        con.commit()
    finally:
        con.close()
    return counts


def _time(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=timezone.utc)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    ex = sub.add_parser("export")
    ex.add_argument("db")
    ex.add_argument("points")
    ex.add_argument("out")
    ex.add_argument("--since", required=True)
    ex.add_argument("--until", required=True)
    ex.add_argument("--recorded-until")
    ex.add_argument("--name")
    ex.add_argument("--version")
    ex.add_argument("--commit")
    ex.add_argument("--clean", action="store_true", help="the commit has no uncommitted changes in the benchmark")
    ex.add_argument("--note")
    ld = sub.add_parser("load")
    ld.add_argument("db")
    ld.add_argument("exports", nargs="+")
    a = ap.parse_args()
    if a.command == "export":
        points = json.loads(Path(a.points).read_text(encoding="utf-8"))["points"]
        manifest = export(Path(a.db), points, Path(a.out), _time(a.since), _time(a.until),
                          _time(a.recorded_until) if a.recorded_until else None, a.name, a.version, a.commit,
                          a.clean if a.commit else None, a.note)
        for f in manifest["files"]:
            print(f"{f['name']}: {f['rows']:,} rows, {f['bytes']:,} bytes")
    else:
        if Path(a.db).exists():
            raise SystemExit(f"{a.db} exists; load into a new database")
        for table, n in load(Path(a.db), [Path(p) for p in a.exports]).items():
            print(f"{table}: {n:,} rows")


if __name__ == "__main__":
    main()
