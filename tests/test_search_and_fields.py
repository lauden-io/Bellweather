"""Address search, UV index and the daily dew point, pressure and feels-like
(2026-11-10). Public test points only."""

from __future__ import annotations

import asyncio
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "bellweather"))

from core import geocode  # noqa: E402
from core.cache import DEFAULT_TTL, ttl_for  # noqa: E402
from core.forecast import blend, skill  # noqa: E402
from core.forecast.members import HOURLY, Hour, parse_forecast  # noqa: E402
from core.model import Location  # noqa: E402

PARIS = Location(48.8566, 2.3522, 35.0)


class ScriptedHttp:
    """Answers by URL prefix; records every call."""

    def __init__(self, answers: dict[str, object]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, dict]] = []

    async def get_json(self, url, params=None, limit=0):
        self.calls.append((url, dict(params or {})))
        for prefix, answer in self.answers.items():
            if url.startswith(prefix):
                return answer
        return None


NOMINATIM_ANSWER = [
    {"lat": "48.8608", "lon": "2.3400", "display_name": "99, Rue de Rivoli, Paris 1er Arrondissement, Paris, France"},
    {"lat": "91", "lon": "0", "display_name": "Broken"},
    {"lat": "x", "lon": "0", "display_name": "Broken too"},
    "junk",
]
OPEN_METEO_ANSWER = {"results": [{"name": "Paris", "admin1": "Île-de-France", "country": "France",
                                  "latitude": 48.85341, "longitude": 2.3488, "elevation": 42.0}]}


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class AddressSearchTests(unittest.TestCase):
    def test_what_counts_as_an_address(self):
        self.assertTrue(geocode.looks_like_address("Rue de Rivoli 99"))
        self.assertTrue(geocode.looks_like_address("75001 Paris"))
        self.assertTrue(geocode.looks_like_address("Rue de Rivoli, Paris"))
        self.assertFalse(geocode.looks_like_address("Paris"))
        self.assertFalse(geocode.looks_like_address("75001"))  # a bare postcode: the place search knows it

    def test_server_setting_is_strict(self):
        self.assertEqual(geocode.address_server("https://nominatim.openstreetmap.org/"), "https://nominatim.openstreetmap.org")
        self.assertEqual(geocode.address_server(" https://geo.example.org/nominatim "), "https://geo.example.org/nominatim")
        for bad in ("", "http://nominatim.openstreetmap.org", "https://user:pw@example.org", "https://example.org/?q=1",
                    "https://example.org/#x", "https://exa mple.org", "https://example.org:99999", "ftp://x", None, 5,
                    "https://" + "x" * 300):
            self.assertIsNone(geocode.address_server(bad), bad)

    def test_parse_addresses_keeps_sane_results_and_the_house_number(self):
        places = geocode.parse_addresses(NOMINATIM_ANSWER)
        self.assertEqual(len(places), 1)
        self.assertEqual(places[0].name, "Rue de Rivoli 99")
        self.assertEqual(places[0].source, geocode.OSM)
        self.assertIsNone(places[0].elevation)
        self.assertTrue(places[0].label.startswith("Rue de Rivoli 99, Paris 1er"))
        long_name = ("Rue de Rivoli, Quartier Saint-Germain-l'Auxerrois, Paris 1er Arrondissement, Paris, "
                     "Île-de-France, Metropolitan France, 75001, France")
        twice = [{"lat": "48.8608", "lon": "2.3400", "display_name": long_name},
                 {"lat": "48.8609", "lon": "2.3401", "display_name": long_name}]
        places = geocode.parse_addresses(twice)
        self.assertEqual(len(places), 1)  # the same street as two ways: shown once
        self.assertLessEqual(len(places[0].label), geocode.MAX_LABEL)
        self.assertTrue(long_name.startswith(places[0].label))  # cut between parts, not inside a word
        self.assertEqual(geocode.parse_addresses({"error": "x"}), [])
        self.assertEqual(geocode.parse_addresses(None), [])

    def test_requests_are_a_second_apart_and_send_only_the_text(self):
        clock = FakeClock()
        search = geocode.AddressSearch(clock=clock, sleep=clock.sleep)
        http = ScriptedHttp({geocode.ADDRESS_SERVER: NOMINATIM_ANSWER})
        asyncio.run(search.search(http, geocode.ADDRESS_SERVER, "Rue de Rivoli 99, Paris", "fr-FR"))
        clock.now += 0.25
        asyncio.run(search.search(http, geocode.ADDRESS_SERVER, "Rue de Rivoli 98, Paris", "fr"))
        self.assertEqual(len(clock.slept), 1)
        self.assertAlmostEqual(clock.slept[0], 0.75)
        url, params = http.calls[0]
        self.assertEqual(url, "https://nominatim.openstreetmap.org/search")
        self.assertEqual(params, {"q": "Rue de Rivoli 99, Paris", "format": "jsonv2", "limit": "8",
                                  "accept-language": "fr", "addressdetails": "0"})

    def test_routing_between_the_two_services(self):
        search = geocode.AddressSearch(clock=FakeClock(), sleep=FakeClock().sleep)
        answers = {geocode.URL: OPEN_METEO_ANSWER, geocode.ADDRESS_SERVER: NOMINATIM_ANSWER}

        http = ScriptedHttp(answers)  # a place name: Open-Meteo only
        places = asyncio.run(geocode.find(http, "Paris", "en", geocode.ADDRESS_SERVER, search))
        self.assertEqual([c[0] for c in http.calls], [geocode.URL])
        self.assertEqual(places[0].source, geocode.OPEN_METEO)

        http = ScriptedHttp(answers)  # an address: the address search only
        places = asyncio.run(geocode.find(http, "Rue de Rivoli 99, Paris", "en", geocode.ADDRESS_SERVER, search))
        self.assertEqual([c[0] for c in http.calls], ["https://nominatim.openstreetmap.org/search"])
        self.assertEqual(places[0].source, geocode.OSM)

        http = ScriptedHttp({geocode.URL: {"results": []}, geocode.ADDRESS_SERVER: NOMINATIM_ANSWER})
        asyncio.run(geocode.find(http, "Rue de Rivoli Paris", "en", geocode.ADDRESS_SERVER, search))
        self.assertEqual([c[0] for c in http.calls], [geocode.URL, "https://nominatim.openstreetmap.org/search"])

        http = ScriptedHttp(answers)  # address search off: nothing goes to it
        asyncio.run(geocode.find(http, "Rue de Rivoli 99, Paris", "en", "", search))
        self.assertEqual([c[0] for c in http.calls], [geocode.URL])

    def test_searches_are_cached_for_a_day(self):
        self.assertEqual(ttl_for("https://nominatim.openstreetmap.org/search"), 24 * 3600.0)
        self.assertEqual(ttl_for("https://geocoding-api.open-meteo.com/v1/search"), 24 * 3600.0)
        self.assertEqual(ttl_for("https://geo.example.org/nominatim/search"), DEFAULT_TTL)


def _member(start: datetime, hours: int, **values) -> dict[datetime, Hour]:
    base = {"temperature": 10.0, "humidity": 80.0, "dew_point": 6.7, "pressure": 1015.0, "wind_speed": 10.0,
            "cloud_cover": 50.0, "precipitation": 0.0, "wind_direction": 200.0}
    return {start + timedelta(hours=i): Hour(**{**base, **values}) for i in range(hours)}


class UvAndDailyTests(unittest.TestCase):
    def test_uv_is_requested_and_parsed(self):
        self.assertIn("uv_index", HOURLY)
        t = ["2026-11-10T10:00", "2026-11-10T11:00"]
        data = {"hourly": {"time": t, "temperature_2m_gfs_seamless": [10, 11], "uv_index_gfs_seamless": [1.5, 99],
                           "temperature_2m_icon_seamless": [10, 11]}}
        out = parse_forecast(data, ["gfs_seamless", "icon_seamless"])
        first, second = sorted(out["gfs_seamless"])
        self.assertEqual(out["gfs_seamless"][first].uv_index, 1.5)
        self.assertIsNone(out["gfs_seamless"][second].uv_index)  # above 25: broken data
        self.assertIsNone(out["icon_seamless"][first].uv_index)

    def test_pass_through_uv_counts_once(self):
        start = datetime(2026, 11, 10, tzinfo=timezone.utc)
        members = {
            "gfs_seamless": _member(start, 24, uv_index=3.0),
            "knmi_seamless": _member(start, 24, uv_index=3.0, temperature=11.0),  # GFS's UV passed on
            "metno_seamless": _member(start, 24, uv_index=3.0, temperature=9.0),
            "ukmo_seamless": _member(start, 24, uv_index=1.0, temperature=10.5),
            "icon_seamless": _member(start, 24),  # no UV
        }
        self.assertEqual(blend.own_uv(members), {"gfs_seamless", "ukmo_seamless"})
        hours = blend.blend_hours(members, skill.Corrections(), PARIS, start)
        self.assertEqual(hours[0].uv_index, 2.0)  # (3 + 1) / 2, not (3 + 3 + 3 + 1) / 4

    def test_daily_dew_point_pressure_feels_like_and_uv(self):
        start = datetime(2026, 11, 10, tzinfo=timezone.utc)
        members = {name: _member(start, 24, uv_index=uv) for name, uv in
                   (("gfs_seamless", 2.0), ("ukmo_seamless", 4.0), ("icon_seamless", None))}
        for i, t in enumerate(sorted(members["icon_seamless"])):
            for m in members.values():
                m[t].pressure = 1000.0 + i  # 1000 .. 1023 hPa through the day
                m[t].temperature = 5.0 + (10.0 if i == 14 else 0.0)
        hours = blend.blend_hours(members, skill.Corrections(), PARIS, start)
        days = blend.blend_days(hours, members, timezone.utc, start.date())
        day = days[0]
        self.assertEqual(day.pressure, 1011.5)
        self.assertEqual(day.dew_point, 6.7)
        self.assertEqual(day.uv_index, 3.0)
        self.assertEqual(day.apparent_temperature, max(h.apparent_temperature for h in hours[:24]))
        self.assertGreater(day.apparent_temperature, 10.0)  # the warm hour sets it


if __name__ == "__main__":
    unittest.main()
