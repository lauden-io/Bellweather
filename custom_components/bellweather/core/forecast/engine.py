"""Puts the forecast together: members, verification, corrections, blend.

- Every hour: the members' newest forecasts are fetched and blended with
  the current corrections.
- Once a day: the verification is rebuilt from what each model said one
  day earlier (Open-Meteo Previous Runs) against the observations, and the
  corrections and scores are recomputed.
- The user's weather entities, when picked as members, are recorded about
  24 hours ahead every hour, so they can be scored the same way after about
  20 days. Until then they count with the average weight and no correction.

The verification is stored with the rest of the location's history, so a
restart or a reload uses it instead of fetching 65 days again. It is used
only while it is younger than a day and was made for the same shared point,
precision, models and archive setting (a hash of those is stored, never
the point itself). If the airport archive fails, the verification still
runs with the home's own data; a recent verification that had the archive
is then kept, and the archive is tried again an hour later.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, tzinfo
from typing import Any

from ..model import Location
from ..privacy import Precision, shared_point
from ..safety import finite, safe_error, valid_id
from ..sources.base import Http
from . import skill
from .blend import ForecastDay, ForecastHour, blend_days, blend_hours
from .history import ObservationLog, Observations, fetch_iem
from .members import DEFAULT_MODELS, Member, fetch_forecast, fetch_previous
from .skill import Corrections, MemberStats, Previous, Score, WetScore

HISTORY_DAYS = 65  # 30 days of corrections + 30 scored days + margin
VERIFICATION_VERSION = 3  # 2: archive hours at :15/:45 count; 3: dew point and pressure corrected (2026-11-10)
VERIFICATION_MAX_AGE = timedelta(days=1)
KEEP_ARCHIVE_VERIFICATION = timedelta(days=3)  # kept when only the archive failed
RECORD_LEAD = timedelta(hours=24)
KEEP_RECORDS = timedelta(days=70)


@dataclass
class Airport:
    station_id: str
    latitude: float
    longitude: float
    distance_km: float | None = None


@dataclass
class Verification:
    corrections: Corrections = field(default_factory=Corrections)
    temperature: Score | None = None
    wind: Score | None = None
    wet: WetScore | None = None
    updated: datetime | None = None
    airport: str | None = None  # the archive used, if any
    home_hours: int = 0


def verification_key(location: Location, precision: Precision, models: list[str], use_archive: bool) -> str:
    """What a stored verification was made for, as a short hash: the shared
    point, precision, models and archive setting. The point is not stored."""
    lat, lon = shared_point(location, precision)
    text = json.dumps([round(lat, 4), round(lon, 4), str(precision.value), sorted(models), bool(use_archive)])
    return hashlib.sha256(text.encode()).hexdigest()[:24]


def _stats_out(stats: dict[str, MemberStats]) -> dict[str, list[float]]:
    return {str(m)[:255]: [s.bias, s.mse, s.mae, s.hours] for m, s in stats.items()}


def _stats_in(data: Any) -> dict[str, MemberStats]:
    out: dict[str, MemberStats] = {}
    for member, row in (data.items() if isinstance(data, dict) else []):
        if not isinstance(row, list) or len(row) != 4:
            continue
        bias, mse, mae, hours = (finite(v) for v in row)
        if None in (bias, mse, mae, hours) or mse < 0 or mae < 0 or hours < 0:
            continue
        out[str(member)[:255]] = MemberStats(bias=bias, mse=mse, mae=mae, hours=int(hours))
    return out


def _score_out(score: Score | None) -> dict[str, Any] | None:
    if score is None:
        return None
    return {"blend": score.blend_mae, "members": dict(score.member_mae), "plain": score.plain_mean_mae,
            "hours": score.hours, "days": score.days}


def _score_in(data: Any) -> Score | None:
    if not isinstance(data, dict):
        return None
    raw = data.get("members") if isinstance(data.get("members"), dict) else {}
    members = {str(m)[:255]: v for m, x in raw.items() if (v := finite(x)) is not None}
    return Score(blend_mae=finite(data.get("blend")), member_mae=members, plain_mean_mae=finite(data.get("plain")),
                 hours=int(finite(data.get("hours")) or 0), days=int(finite(data.get("days")) or 0))


def _wet_out(wet: WetScore | None) -> dict[str, Any] | None:
    if wet is None:
        return None
    return {"csi": {k: (None if math.isnan(v) else v) for k, v in wet.csi.items()},
            "wet_hours": wet.wet_hours, "hours": wet.hours}


def _wet_in(data: Any) -> WetScore | None:
    if not isinstance(data, dict) or not isinstance(data.get("csi"), dict):
        return None
    csi = {}
    for k, x in data["csi"].items():
        v = finite(x)
        csi[str(k)[:255]] = v if v is not None else math.nan
    return WetScore(csi=csi, wet_hours=int(finite(data.get("wet_hours")) or 0), hours=int(finite(data.get("hours")) or 0))


def verification_out(v: Verification, key: str) -> dict[str, Any] | None:
    if v.updated is None:
        return None
    c = v.corrections
    return {
        "version": VERIFICATION_VERSION, "key": key, "updated": v.updated.isoformat(),
        "temperature_stats": _stats_out(c.temperature), "wind_stats": _stats_out(c.wind),
        "dew_point_stats": _stats_out(c.dew_point), "pressure_stats": _stats_out(c.pressure),
        "days": c.days, "source": c.source,
        "temperature": _score_out(v.temperature), "wind": _score_out(v.wind), "wet": _wet_out(v.wet),
        "airport": v.airport, "home_hours": v.home_hours,
    }


def verification_in(data: Any, key: str, now: datetime) -> Verification | None:
    """A stored verification, if it is for the same key, younger than a day
    and sane; otherwise None (and it is made again)."""
    if not isinstance(data, dict) or data.get("version") != VERIFICATION_VERSION or data.get("key") != key:
        return None
    try:
        updated = datetime.fromisoformat(str(data.get("updated"))[:40])
    except ValueError:
        return None
    if updated.tzinfo is None or not timedelta(0) <= now - updated < VERIFICATION_MAX_AGE:
        return None
    source = data.get("source") if data.get("source") in ("home", "airport", "mixed") else None
    airport = valid_id(data.get("airport")) if data.get("airport") else None
    return Verification(
        corrections=Corrections(
            temperature=_stats_in(data.get("temperature_stats")), wind=_stats_in(data.get("wind_stats")),
            dew_point=_stats_in(data.get("dew_point_stats")), pressure=_stats_in(data.get("pressure_stats")),
            days=int(finite(data.get("days")) or 0), source=source,
        ),
        temperature=_score_in(data.get("temperature")), wind=_score_in(data.get("wind")), wet=_wet_in(data.get("wet")),
        updated=updated, airport=airport, home_hours=int(finite(data.get("home_hours")) or 0),
    )


class ForecastEngine:
    def __init__(self, models: list[str] | None = None, use_open_meteo: bool = True) -> None:
        self.models = list(models or DEFAULT_MODELS) if use_open_meteo else []
        self.log = ObservationLog()
        self.records: dict[str, dict[datetime, dict[str, float]]] = {}
        self.verification = Verification()
        self.hours: list[ForecastHour] = []
        self.days: list[ForecastDay] = []
        self.members_used: list[str] = []
        self.fetched: datetime | None = None
        self.key: str | None = None  # verification_key() of this location's settings

    # --- persistence ----------------------------------------------------------

    def load(self, data: dict[str, Any] | None, now: datetime | None = None) -> None:
        data = data if isinstance(data, dict) else {}
        self.log = ObservationLog(data.get("log"))
        if self.key is not None and now is not None:
            restored = verification_in(data.get("verification"), self.key, now)
            if restored is not None:
                self.verification = restored
        records = data.get("records") if isinstance(data.get("records"), dict) else {}
        for entity, rows in records.items():
            if not isinstance(rows, dict):
                continue
            clean: dict[datetime, dict[str, float]] = {}
            for key, row in rows.items():
                try:
                    t = datetime.fromisoformat(str(key)[:25])
                except ValueError:
                    continue
                if t.tzinfo is None or not isinstance(row, dict):
                    continue
                values = {k: v for k in ("t", "w", "p") if (v := finite(row.get(k))) is not None}
                if values:
                    clean[t] = values
            if clean:
                self.records[str(entity)[:255]] = clean

    def dump(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "log": self.log.as_dict(),
            "records": {e: {t.isoformat(): v for t, v in rows.items()} for e, rows in self.records.items()},
        }
        stored = verification_out(self.verification, self.key) if self.key else None
        if stored is not None:
            out["verification"] = stored
        return out

    # --- verification (daily) ---------------------------------------------------

    async def verify(
        self, http: Http, location: Location, precision: Precision, now: datetime, airport: Airport | None
    ) -> str | None:
        """Rebuild the verification. Returns the airport archive's error when
        the archive failed (the rest is used anyway), else None. A failure of
        the models' history (Open-Meteo) raises."""
        start = now - timedelta(days=HISTORY_DAYS)
        obs_home: Observations = dict(self.log.hours)
        prev_home: Previous = {}
        if self.models:
            lat, lon = shared_point(location, precision)
            prev_home = await fetch_previous(http, lat, lon, self.models, start, now)
        obs_air: Observations = {}
        prev_air: Previous = {}
        archive_error: str | None = None
        if airport is not None and self.models:
            try:
                obs_air = await fetch_iem(http, airport.station_id, start, now)
                if obs_air:
                    prev_air = await fetch_previous(http, airport.latitude, airport.longitude, self.models, start, now)
            except Exception as err:  # noqa: BLE001 - the home's own data still counts
                archive_error = safe_error(err)
                obs_air, prev_air = {}, {}
                old = self.verification
                if old.airport and old.updated is not None and now - old.updated < KEEP_ARCHIVE_VERIFICATION:
                    return archive_error  # keep the one made with the archive; try again later
        # About a second of pure arithmetic: done in a worker thread, never in
        # Home Assistant's event loop.
        records = {e: dict(rows) for e, rows in self.records.items()}
        self.verification = await asyncio.to_thread(
            _compute, obs_home, prev_home, obs_air, prev_air, records, now, airport.station_id if obs_air and airport else None
        )
        return archive_error


    # --- forecast (hourly) --------------------------------------------------------

    async def forecast(
        self, http: Http, location: Location, precision: Precision, now: datetime, zone: tzinfo,
        entity_members: dict[str, Member] | None = None,
    ) -> None:
        members: dict[str, Member] = {}
        if self.models:
            members.update(await fetch_forecast(http, location, precision, self.models))
        for entity, member in (entity_members or {}).items():
            if member:
                members[entity] = member
                self._record(entity, member, now)
        start = now.replace(minute=0, second=0, microsecond=0)
        self.hours = blend_hours(members, self.verification.corrections, location, start)
        today: date = now.astimezone(zone).date()
        self.days = blend_days(self.hours, members, zone, today)
        self.members_used = sorted(members)
        self.fetched = now

    def _record(self, entity: str, member: Member, now: datetime) -> None:
        target = (now + RECORD_LEAD).replace(minute=0, second=0, microsecond=0)
        hour = member.get(target)
        if hour is None:
            return
        row = {k: v for k, v in (("t", hour.temperature), ("w", hour.wind_speed), ("p", hour.precipitation)) if v is not None}
        if row:
            self.records.setdefault(entity, {})[target] = row
        cutoff = now - KEEP_RECORDS
        self.records[entity] = {t: v for t, v in self.records[entity].items() if t >= cutoff}


def _compute(
    obs_home: Observations, prev_home: Previous, obs_air: Observations, prev_air: Previous,
    records: dict[str, dict[datetime, dict[str, float]]], now: datetime, airport: str | None,
) -> Verification:
    obs, prev = merge(obs_home, prev_home, obs_air, prev_air)
    corr = skill.corrections(obs, prev, now, source=_source(obs_home, obs_air, obs))
    # Weather entities enter the weighting once they have enough days of
    # their own record, and only when the models' corrections are active.
    for var, target in (("t", corr.temperature), ("w", corr.wind)):
        if not target:
            continue
        for entity, rows in records.items():
            stats, days = skill.member_stats(obs_home, {entity: rows}, var, now)
            if days >= skill.MIN_DAYS and entity in stats:
                target[entity] = stats[entity]
    return Verification(
        corrections=corr,
        temperature=skill.score(obs, prev, "t", now),
        wind=skill.score(obs, prev, "w", now),
        wet=skill.wet_score(obs, prev, now),
        updated=now, airport=airport,
        home_hours=sum(1 for row in obs_home.values() if "t" in row),
    )


def merge(obs_home: Observations, prev_home: Previous, obs_air: Observations, prev_air: Previous) -> tuple[Observations, Previous]:
    """One series: for each hour the home's own observation and forecast
    when there is one, else the airport's."""
    obs: Observations = {}
    prev: Previous = {m: {} for m in set(prev_home) | set(prev_air)}
    for t in set(obs_home) | set(obs_air):
        use_home = t in obs_home and any(t in rows for rows in prev_home.values())
        src_obs, src_prev = (obs_home, prev_home) if use_home else (obs_air, prev_air)
        if t not in src_obs:
            continue
        obs[t] = src_obs[t]
        for member, rows in src_prev.items():
            if t in rows:
                prev.setdefault(member, {})[t] = rows[t]
    return obs, {m: rows for m, rows in prev.items() if rows}


def _source(obs_home: Observations, obs_air: Observations, merged: Observations) -> str | None:
    home = sum(1 for t in merged if merged[t] is obs_home.get(t))
    air = len(merged) - home
    if not merged:
        return None
    if home and air:
        return "mixed"
    return "home" if home else "airport"


_ = MemberStats
