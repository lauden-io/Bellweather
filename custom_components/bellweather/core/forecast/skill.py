"""How good each model has been at this location: the numbers behind the blend.

The method is the one I measured at six airports over 90 days
(docs/forecast.md), on the run from one day earlier:

- for temperature and wind speed, each model's mean error (bias) and mean
  squared error over the last 30 days; since 2026-11-10 also for dew point
  and sea-level pressure (out of sample at 10 public airports on four
  continents, 90 days: lower error than the plain mean at 9 of 10 for each,
  see docs/forecast.md);
- the blend removes each model's bias and weights the models by 1 / MSE;
- it does so only when at least 20 of those 30 days have data. With 3 to 4
  days, correcting made the forecast worse in my test, so until then every
  model counts the same and nothing is corrected;
- precipitation is not corrected: an hour is wet when at least half of the
  models say at least 0.1 mm. That rule had the best hit rate at four of six
  airports.

The same data also gives the blend's own error, out of sample: each day is
scored with the corrections learned from the 30 days before it.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

WINDOW_DAYS = 30
MIN_DAYS = 20
WET_MM = 0.1
WET_SHARE = 0.5

# hour -> {"t": observed temperature, "w": observed wind, "wet": bool}
Observations = dict[datetime, dict[str, float | bool]]
# member -> hour -> {"t", "w", "p"}
Previous = dict[str, dict[datetime, dict[str, float]]]


@dataclass(frozen=True)
class MemberStats:
    bias: float
    mse: float
    mae: float
    hours: int


@dataclass
class Corrections:
    """What the blend applies. Empty dicts mean: equal weights, no bias."""

    temperature: dict[str, MemberStats] = field(default_factory=dict)
    wind: dict[str, MemberStats] = field(default_factory=dict)
    dew_point: dict[str, MemberStats] = field(default_factory=dict)
    pressure: dict[str, MemberStats] = field(default_factory=dict)
    days: int = 0  # days with data in the window
    source: str | None = None  # "airport" (archive), "home" (own observations) or "mixed"

    def active(self, var: str) -> dict[str, MemberStats]:
        return {"t": self.temperature, "w": self.wind, "d": self.dew_point, "pr": self.pressure}.get(var, {})


def _pairs(obs: Observations, prev: Previous, var: str, start: datetime, end: datetime) -> dict[str, list[tuple[datetime, float, float]]]:
    """member -> [(hour, forecast, observed)] for hours in [start, end)."""
    out: dict[str, list[tuple[datetime, float, float]]] = {}
    for member, hours in prev.items():
        rows = []
        for t, row in hours.items():
            if start <= t < end and var in row and t in obs and isinstance(obs[t].get(var), (int, float)) \
                    and not isinstance(obs[t].get(var), bool):
                rows.append((t, row[var], float(obs[t][var])))
        out[member] = rows
    return out


def member_stats(obs: Observations, prev: Previous, var: str, end: datetime, window_days: int = WINDOW_DAYS) -> tuple[dict[str, MemberStats], int]:
    """Stats over the `window_days` before `end`, and how many days had data."""
    start = end - timedelta(days=window_days)
    pairs = _pairs(obs, prev, var, start, end)
    stats: dict[str, MemberStats] = {}
    days: set[date] = set()
    for member, rows in pairs.items():
        if not rows:
            continue
        errors = [f - o for _, f, o in rows]
        stats[member] = MemberStats(
            bias=statistics.fmean(errors), mse=statistics.fmean(e * e for e in errors),
            mae=statistics.fmean(abs(e) for e in errors), hours=len(rows),
        )
        days |= {t.date() for t, _, _ in rows}
    return stats, len(days)


def corrections(obs: Observations, prev: Previous, end: datetime, source: str | None = None) -> Corrections:
    t_stats, t_days = member_stats(obs, prev, "t", end)
    w_stats, w_days = member_stats(obs, prev, "w", end)
    d_stats, d_days = member_stats(obs, prev, "d", end)
    p_stats, p_days = member_stats(obs, prev, "pr", end)
    return Corrections(
        temperature=t_stats if t_days >= MIN_DAYS else {},
        wind=w_stats if w_days >= MIN_DAYS else {},
        dew_point=d_stats if d_days >= MIN_DAYS else {},
        pressure=p_stats if p_days >= MIN_DAYS else {},
        days=max(t_days, w_days), source=source,
    )


def combine(values: dict[str, float], stats: dict[str, MemberStats]) -> float | None:
    """Bias-corrected, 1/MSE-weighted mean. Members without stats (new, or
    a weather entity still collecting) count with the average weight and no
    correction; with no stats at all it is the plain mean."""
    if not values:
        return None
    if not stats:
        return statistics.fmean(values.values())
    known = [s for s in stats.values() if s.mse > 0]
    if not known:
        return statistics.fmean(values.values())
    # Weight from the error left after removing the bias: MSE - bias^2 is the variance of the error.
    weights = {m: 1.0 / max(s.mse - s.bias * s.bias, 1e-6) for m, s in stats.items() if s.mse > 0}
    average = statistics.fmean(weights.values())
    total = num = 0.0
    for member, value in values.items():
        s = stats.get(member)
        w = weights.get(member, average)
        num += w * (value - (s.bias if s else 0.0))
        total += w
    return num / total if total else None


def wet_share(amounts: dict[str, float]) -> float | None:
    if not amounts:
        return None
    return sum(1 for a in amounts.values() if a >= WET_MM) / len(amounts)


# --- the blend's own track record ------------------------------------------

@dataclass
class Score:
    """Out-of-sample errors over the scored days."""

    blend_mae: float | None
    member_mae: dict[str, float]
    plain_mean_mae: float | None
    hours: int
    days: int


def score(obs: Observations, prev: Previous, var: str, end: datetime, days: int = WINDOW_DAYS) -> Score:
    """Score the last `days` days before `end`. Each day uses corrections
    learned from the 30 days before it, exactly like the live blend."""
    blend_err: list[float] = []
    plain_err: list[float] = []
    member_err: dict[str, list[float]] = {m: [] for m in prev}
    scored: set[date] = set()
    day_end = end.replace(hour=0, minute=0, second=0, microsecond=0)
    for d in range(days, 0, -1):
        start = day_end - timedelta(days=d)
        stop = start + timedelta(days=1)
        stats, n_days = member_stats(obs, prev, var, start)
        if n_days < MIN_DAYS:
            stats = {}
        test = _pairs(obs, prev, var, start, stop)
        by_hour: dict[datetime, dict[str, float]] = {}
        observed: dict[datetime, float] = {}
        for member, rows in test.items():
            for t, f, o in rows:
                by_hour.setdefault(t, {})[member] = f
                observed[t] = o
                member_err[member].append(abs(f - o))
        for t, values in by_hour.items():
            if len(values) < max(2, len(prev) // 2):
                continue
            blended = combine(values, stats)
            if blended is None:
                continue
            blend_err.append(abs(blended - observed[t]))
            plain_err.append(abs(statistics.fmean(values.values()) - observed[t]))
            scored.add(t.date())
    return Score(
        blend_mae=statistics.fmean(blend_err) if blend_err else None,
        member_mae={m: statistics.fmean(v) for m, v in member_err.items() if v},
        plain_mean_mae=statistics.fmean(plain_err) if plain_err else None,
        hours=len(blend_err), days=len(scored),
    )


@dataclass
class WetScore:
    csi: dict[str, float]  # member or "blend" -> critical success index
    wet_hours: int
    hours: int


def wet_score(obs: Observations, prev: Previous, end: datetime, days: int = WINDOW_DAYS) -> WetScore:
    """Hit rate for "wet hour" (CSI = hits / (hits + misses + false alarms))."""
    start = end - timedelta(days=days)
    counts: dict[str, list[int]] = {}
    hours: set[datetime] = set()
    wet_hours = 0
    times = sorted({t for m in prev.values() for t in m if start <= t < end and isinstance(obs.get(t, {}).get("wet"), bool)})
    for t in times:
        observed = bool(obs[t]["wet"])
        amounts = {m: h[t]["p"] for m, h in prev.items() if t in h and "p" in h[t]}
        if len(amounts) < max(2, len(prev) // 2):
            continue
        hours.add(t)
        wet_hours += observed
        share = wet_share(amounts) or 0.0
        for name, said in [*((m, a >= WET_MM) for m, a in amounts.items()), ("blend", share >= WET_SHARE)]:
            c = counts.setdefault(name, [0, 0, 0])
            if said and observed:
                c[0] += 1
            elif observed:
                c[1] += 1
            elif said:
                c[2] += 1
    csi = {name: (c[0] / sum(c) if sum(c) else math.nan) for name, c in counts.items()}
    return WetScore(csi=csi, wet_hours=wet_hours, hours=len(hours))
