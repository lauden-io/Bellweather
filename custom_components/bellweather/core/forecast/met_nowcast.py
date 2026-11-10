"""MET Norway's official radar nowcast (Nowcast 2.0), Nordic countries.

Where it exists it comes before Bellweather's own extrapolation: a national
service's nowcast uses its own radar network at 1 km and 5 minutes. No
key; MET Norway asks for an identifying User-Agent and coordinates with at
most 4 decimals; data is CC BY 4.0.

It receives the shared (rounded) point, like every point service. Outside
its area it answers 422; Bellweather then stops asking for a week, and it
does not ask at all south of 53.5 N or outside 10 W - 35 E, which is
clearly outside the Nordic radar area.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from ..model import Location
from ..nowcast import STEP_MINUTES, WET, Nowcast, Step
from ..privacy import Precision, shared_point
from ..safety import CAP_SMALL_JSON, HttpStatusError, finite
from ..sources.base import Http

URL = "https://api.met.no/weatherapi/nowcast/2.0/complete"
ATTRIBUTION = "Nowcast: MET Norway (CC BY 4.0)"
RETRY_OUTSIDE = timedelta(days=7)
REQUEST_DEADLINE = 30  # seconds


def maybe_inside(location: Location) -> bool:
    return location.latitude >= 53.5 and -10.0 <= location.longitude <= 35.0


def parse(data: object, now: datetime) -> Nowcast | None:
    if not isinstance(data, dict):
        return None
    series = ((data.get("properties") or {}).get("timeseries")) if isinstance(data.get("properties"), dict) else None
    if not isinstance(series, list) or not series:
        return None
    points: list[tuple[datetime, float]] = []
    for item in series[:60]:
        if not isinstance(item, dict):
            continue
        try:
            t = datetime.fromisoformat(str(item.get("time"))[:25].replace("Z", "+00:00"))
        except ValueError:
            continue
        details = ((item.get("data") or {}).get("instant") or {}).get("details") or {}
        rate = finite(details.get("precipitation_rate")) if isinstance(details, dict) else None
        if t.tzinfo is not None and rate is not None and 0 <= rate <= 500:
            points.append((t.astimezone(timezone.utc), rate))
    if not points:
        return None
    issued = points[0][0]
    steps: list[Step] = []
    for k in range(1, 13):
        start, stop = issued + timedelta(minutes=(k - 1) * STEP_MINUTES), issued + timedelta(minutes=k * STEP_MINUTES)
        rates = [r for t, r in points if start < t <= stop]
        if not rates:
            break
        mean = sum(rates) / len(rates)
        steps.append(Step(minutes=k * STEP_MINUTES, rate=round(mean, 2), probability=1.0 if mean >= WET else 0.0))
    return Nowcast(issued=issued, method="met_norway", steps=steps, attribution=ATTRIBUTION)


class MetNowcast:
    key = "met_nowcast"

    def __init__(self) -> None:
        self._outside_until: datetime | None = None
        self._failures = 0
        self._next_try: datetime | None = None

    async def fetch(self, http: Http, location: Location, precision: Precision, now: datetime) -> Nowcast | None:
        lat, lon = shared_point(location, precision)
        # The area test uses the shared point too: whether a request is sent
        # must not reveal more than the request itself would.
        if not maybe_inside(Location(lat, lon)) or (self._outside_until and now < self._outside_until):
            return None
        if self._next_try and now < self._next_try:
            return None
        try:
            async with asyncio.timeout(REQUEST_DEADLINE):
                data = await http.get_json(URL, {"lat": f"{lat:.4f}", "lon": f"{lon:.4f}"}, limit=CAP_SMALL_JSON)
        except HttpStatusError as err:
            if err.status == 422:  # outside the product area
                self._outside_until = now + RETRY_OUTSIDE
                return None
            self._back_off(now, err.retry_after)
            raise
        except BaseException:  # timeouts and cancellation too: a hanging service also rests
            self._back_off(now, None)
            raise
        self._failures, self._next_try = 0, None
        return parse(data, now)

    def _back_off(self, now: datetime, retry_after: float | None) -> None:
        """5, 10, 20, 40, 60 minutes, or what Retry-After asks (at most 6 h)."""
        self._failures += 1
        minutes = (5, 10, 20, 40, 60)[min(self._failures, 5) - 1]
        seconds = minutes * 60.0
        if retry_after is not None and retry_after > 0:
            seconds = max(seconds, min(retry_after, 6 * 3600.0))
        self._next_try = now + timedelta(seconds=seconds)
