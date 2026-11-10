"""Airport weather reports (METAR) from the NOAA Aviation Weather Center.

Worldwide, keyless, JSON. The only free global source with a present-weather
code (rain, drizzle, snow, freezing rain, thunder, fog). Airports report every
30 or 60 minutes and are often 10-50 km away, so the fusion step ranks them
after radar and official stations for rain, and uses them as the global
fallback for type, wind, visibility and cloud.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from ..derive import humidity_from_dew_point
from ..geo import distance_km
from ..model import Field, IntensityClass, Kind, Location, PrecipType, Reading, plausible
from ..privacy import Precision, query_box
from ..safety import CAP_METAR, clean_text, finite, not_future, valid_id, valid_position
from .base import Http, Source

URL = "https://aviationweather.gov/api/data/metar"
SEARCH_RADIUS_KM = 50.0  # the largest default airport limit (wind); grows with the user's limits, up to MAX_RADIUS_KM
MAX_RADIUS_KM = 100.0
KT_TO_KMH = 1.852
MILE_TO_KM = 1.609344
# Cloud amount in eighths -> %, middle of each METAR class.
CLOUD_PERCENT = {"FEW": 19, "SCT": 44, "BKN": 75, "OVC": 100, "OVX": 100, "VV": 100}
CLEAR_CODES = ("CAVOK", "SKC", "CLR", "NSC", "NCD")

MAX_WX_CHARS = 64
MAX_WX_TOKENS = 8
MAX_TOKEN_CHARS = 16
_PRECIP = ("DZ", "RA", "SN", "SG", "PL", "GR", "GS", "IC", "UP")
_TOKEN = re.compile(r"^(?P<int>[-+]|VC)?(?P<desc>MI|PR|BC|DR|BL|SH|TS|FZ)*(?P<rest>[A-Z]*)$")


class WeatherCode:
    """What a METAR present-weather group says about the panel's fields."""

    def __init__(self, wx: str | None) -> None:
        self.precipitating = False
        self.thunder = False
        self.fog = False
        self.type = PrecipType.NONE
        self.intensity: IntensityClass = IntensityClass.DRY
        codes: set[str] = set()
        intensity = ""
        # Real groups are short; long input is cut before any regex sees it.
        for token in str(wx or "")[:MAX_WX_CHARS].split()[:MAX_WX_TOKENS]:
            if len(token) > MAX_TOKEN_CHARS:
                continue
            m = _TOKEN.match(token)
            if not m:
                continue
            if m.group("int") == "VC":  # in the vicinity, not at the station
                continue
            if "TS" in token:
                self.thunder = True
            body = token.lstrip("+-")
            found = {body[i : i + 2] for i in range(0, len(body), 2)}
            if "FG" in found and not body.startswith(("MI", "BC", "PR")):
                self.fog = True
            precip = found & set(_PRECIP)
            if precip:
                self.precipitating = True
                codes |= precip
                if "FZ" in body and precip & {"RA", "DZ"}:
                    codes.add("FZ")
                intensity = intensity or (m.group("int") or "=")
        if not self.precipitating:
            return
        if codes & {"GR", "GS"}:
            self.type = PrecipType.HAIL
        elif "FZ" in codes:
            self.type = PrecipType.FREEZING_RAIN
        elif codes & {"PL", "IC"} or (codes & {"SN", "SG"} and codes & {"RA", "DZ"}):
            self.type = PrecipType.SLEET
        elif codes & {"SN", "SG"}:
            self.type = PrecipType.SNOW
        elif codes == {"DZ"}:
            self.type = PrecipType.DRIZZLE
        else:
            self.type = PrecipType.RAIN
        if self.type is PrecipType.DRIZZLE:
            self.intensity = IntensityClass.LIGHT if intensity == "+" else IntensityClass.DRIZZLE
        else:
            self.intensity = {
                "-": IntensityClass.LIGHT,
                "+": IntensityClass.HEAVY,
            }.get(intensity, IntensityClass.MODERATE)


def _number(value: Any) -> float | None:
    """A finite number; AWC writes "10+" for "10 or more"."""
    if isinstance(value, str):
        value = value.strip().rstrip("+")[:16]
    return finite(value)


MAX_REPORTS = 1000


def parse(reports: list[dict[str, Any]], location: Location, attribution: str, box=None) -> list[Reading]:
    """Readings from AWC JSON, newest report per station. A broken report is
    skipped; the others still count."""
    newest: dict[str, dict[str, Any]] = {}
    for rep in (reports if isinstance(reports, list) else [])[: MAX_REPORTS * 20]:
        if len(newest) >= MAX_REPORTS:
            break
        if not isinstance(rep, dict):
            continue
        sid = valid_id(rep.get("icaoId"))
        when = finite(rep.get("obsTime"))
        if not sid or when is None:
            continue
        # Only airports inside the box count towards the cap.
        lat, lon = _number(rep.get("lat")), _number(rep.get("lon"))
        if not valid_position(lat, lon) or (box is not None and not box.contains(lat, lon)):
            continue
        rep = {**rep, "obsTime": when}
        if sid not in newest or when > newest[sid]["obsTime"]:
            newest[sid] = rep
    readings: list[Reading] = []
    for sid, rep in newest.items():
        try:
            readings.extend(_parse_report(sid, rep, location, attribution, box))
        except (TypeError, ValueError, AttributeError, KeyError, OverflowError, OSError):
            continue
    return readings


def _parse_report(sid: str, rep: dict[str, Any], location: Location, attribution: str, box) -> list[Reading]:
    readings: list[Reading] = []
    if True:
        lat, lon = _number(rep.get("lat")), _number(rep.get("lon"))
        if not valid_position(lat, lon) or (box is not None and not box.contains(lat, lon)):
            return readings
        observed = datetime.fromtimestamp(rep["obsTime"], timezone.utc)
        if not not_future(observed, datetime.now(timezone.utc)):
            return readings
        # AWC names look like "London/Heathrow Intl, EN, GB": keep the place.
        place = clean_text(str(rep.get("name") or "").split(",")[0])
        common = dict(
            kind=Kind.AIRPORT,
            source="metar",
            observed=observed,
            station=f"{place} ({sid})" if place else sid,
            station_id=sid,
            latitude=lat,
            longitude=lon,
            distance_km=round(distance_km(location.latitude, location.longitude, lat, lon), 1),
            elevation=_number(rep.get("elev")),
            attribution=attribution,
        )

        def add(field: Field, value: float | bool | str | None) -> None:
            if value is None:
                return
            if isinstance(value, (int, float)) and not isinstance(value, bool) and not plausible(field, value):
                return
            readings.append(Reading(field=field, value=value, **common))

        wx = WeatherCode(rep.get("wxString"))
        add(Field.RAIN_NOW, wx.precipitating)
        add(Field.PRECIP_TYPE, wx.type.value)
        add(Field.INTENSITY_CLASS, wx.intensity.value)
        add(Field.THUNDER, wx.thunder)
        speed = _number(rep.get("wspd"))
        add(Field.WIND_SPEED, None if speed is None else round(speed * KT_TO_KMH, 1))
        gust = _number(rep.get("wgst"))
        # No gust group means no significant gust: report the mean wind.
        gust_kt = gust if gust is not None else speed
        add(Field.WIND_GUST, None if gust_kt is None else round(gust_kt * KT_TO_KMH, 1))
        add(Field.WIND_DIRECTION, _number(rep.get("wdir")))  # "VRB" -> None
        vis = _number(rep.get("visib"))
        vis_km = None if vis is None else round(vis * MILE_TO_KM, 1)
        add(Field.VISIBILITY, vis_km)
        add(Field.FOG, wx.fog or (vis_km is not None and vis_km < 1.0))
        temp, dewp = _number(rep.get("temp")), _number(rep.get("dewp"))
        add(Field.TEMPERATURE, temp)
        add(Field.DEW_POINT, dewp)
        if temp is not None and dewp is not None:
            add(Field.HUMIDITY, round(humidity_from_dew_point(temp, dewp), 1))
        # Sea-level pressure where the report has it (North America), else
        # QNH, the altimeter setting. QNH is reduced with the standard
        # atmosphere, so it can differ from sea-level pressure by a few hPa
        # at high or cold airports. AWC gives both in hPa.
        add(Field.PRESSURE, _number(rep.get("slp")) or _number(rep.get("altim")))
        clouds = [c for c in (rep.get("clouds") if isinstance(rep.get("clouds"), list) else [])[:10] if isinstance(c, dict)]
        if clouds:
            add(Field.CLOUD_COVER, max(CLOUD_PERCENT.get(c.get("cover"), 0) for c in clouds))
        elif any(code in str(rep.get("rawOb") or "")[:300] for code in CLEAR_CODES):
            add(Field.CLOUD_COVER, 0)
    return readings


class MetarSource(Source):
    key = "metar"
    kind = Kind.AIRPORT
    attribution = "METAR: NOAA Aviation Weather Center"
    sends = "a box around the shared point (search radius 50 km by default, plus the rounding)"

    def __init__(self, radius_km: float = SEARCH_RADIUS_KM) -> None:
        self.radius_km = min(max(radius_km, 1.0), MAX_RADIUS_KM)

    async def fetch(self, http: Http, location: Location, precision: Precision, now: datetime) -> list[Reading]:
        box = query_box(location, precision, self.radius_km)
        data = await http.get_json(
            URL,
            {
                "bbox": f"{box.south:.3f},{box.west:.3f},{box.north:.3f},{box.east:.3f}",
                "format": "json",
                "hours": "2",
            },
            limit=CAP_METAR,
        )
        return parse(data or [], location, self.attribution, box)
