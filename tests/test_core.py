"""Tests for the Home Assistant-free core. Run: python -m unittest discover tests"""

from __future__ import annotations

import struct
import sys
import unittest
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "bellweather"))

from core import derive, geo, png  # noqa: E402
from core.condition import Condition, condition  # noqa: E402
from core.fusion import DEFAULT_LIMITS, Group, GroupLimits, Settings, fuse  # noqa: E402
from core.model import Field, IntensityClass, Kind, Location, PrecipType, Reading, intensity_class  # noqa: E402
from core.privacy import Precision, query_box, shared_point  # noqa: E402
from core.sources import esoh, ims, metar, open_meteo, rainviewer  # noqa: E402

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
# A neutral test point (central Paris).
HOME = Location(48.8566, 2.3522, 35.0)


class GeoTest(unittest.TestCase):
    def test_distance_paris_london(self):
        self.assertAlmostEqual(geo.distance_km(48.8566, 2.3522, 51.5074, -0.1278), 343.5, delta=1.0)

    def test_snap_is_stable_and_close(self):
        a = geo.snap_to_grid(48.8566, 2.3522, 5)
        b = geo.snap_to_grid(48.8570, 2.3530, 5)
        self.assertEqual(a, b)
        self.assertLess(geo.distance_km(48.8566, 2.3522, *a), 5 * 0.75)

    def test_tile_origin(self):
        self.assertEqual(geo.tile_for(0.0, 0.0, 0)[:2], (0, 0))
        x, y, px, py = geo.tile_for(48.8566, 2.3522, 7)
        self.assertEqual((x, y), (64, 44))


class PrivacyTest(unittest.TestCase):
    def test_exact_shares_the_point(self):
        self.assertEqual(shared_point(HOME, Precision.EXACT), (HOME.latitude, HOME.longitude))

    def test_rounded_point_differs(self):
        self.assertNotEqual(shared_point(HOME, Precision.KM_10), (HOME.latitude, HOME.longitude))

    def test_box_still_covers_the_search_circle(self):
        for p in Precision:
            box = query_box(HOME, p, 40)
            for bearing_lat, bearing_lon in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                lat = HOME.latitude + bearing_lat * 40 / 111.32
                lon = HOME.longitude + bearing_lon * 40 / (111.32 * 0.6578)
                self.assertTrue(box.contains(lat, lon), (p, bearing_lat, bearing_lon))


class IntensityTest(unittest.TestCase):
    def test_classes(self):
        self.assertIs(intensity_class(0.0), IntensityClass.DRY)
        self.assertIs(intensity_class(0.3), IntensityClass.DRIZZLE)
        self.assertIs(intensity_class(0.3, PrecipType.SNOW), IntensityClass.LIGHT)
        self.assertIs(intensity_class(2.0), IntensityClass.LIGHT)
        self.assertIs(intensity_class(5.0), IntensityClass.MODERATE)
        self.assertIs(intensity_class(10.0), IntensityClass.HEAVY)
        self.assertIs(intensity_class(60.0), IntensityClass.EXTREME)


class MetarTest(unittest.TestCase):
    def test_weather_codes(self):
        cases = {
            "-RA BR": (True, PrecipType.RAIN, IntensityClass.LIGHT, False, False),
            "+TSRA": (True, PrecipType.RAIN, IntensityClass.HEAVY, True, False),
            "DZ": (True, PrecipType.DRIZZLE, IntensityClass.DRIZZLE, False, False),
            "-SN": (True, PrecipType.SNOW, IntensityClass.LIGHT, False, False),
            "RASN": (True, PrecipType.SLEET, IntensityClass.MODERATE, False, False),
            "-FZRA": (True, PrecipType.FREEZING_RAIN, IntensityClass.LIGHT, False, False),
            "FG": (False, PrecipType.NONE, IntensityClass.DRY, False, True),
            "BCFG": (False, PrecipType.NONE, IntensityClass.DRY, False, False),
            "VCSH": (False, PrecipType.NONE, IntensityClass.DRY, False, False),
            "TS": (False, PrecipType.NONE, IntensityClass.DRY, True, False),
            "SHGS": (True, PrecipType.HAIL, IntensityClass.MODERATE, False, False),
        }
        for wx, (wet, ptype, cls, thunder, fog) in cases.items():
            code = metar.WeatherCode(wx)
            self.assertEqual(
                (code.precipitating, code.type, code.intensity, code.thunder, code.fog),
                (wet, ptype, cls, thunder, fog),
                wx,
            )

    def test_parse_keeps_newest_report(self):
        ts = int(NOW.timestamp())
        reports = [
            {"icaoId": "LFPO", "name": "Paris/Orly", "obsTime": ts - 3600, "lat": 48.723, "lon": 2.379, "elev": 89,
             "wxString": None, "wspd": 5, "wdir": 200, "visib": "6+", "clouds": [{"cover": "FEW", "base": 2000}]},
            {"icaoId": "LFPO", "name": "Paris/Orly", "obsTime": ts - 600, "lat": 48.723, "lon": 2.379, "elev": 89,
             "wxString": "-RA", "wspd": 10, "wgst": 22, "wdir": "VRB", "visib": 3, "clouds": [{"cover": "OVC", "base": 900}]},
        ]
        got = {r.field: r for r in metar.parse(reports, HOME, "x")}
        self.assertTrue(got[Field.RAIN_NOW].value)
        self.assertEqual(got[Field.WIND_SPEED].value, 18.5)
        self.assertEqual(got[Field.WIND_GUST].value, 40.7)
        self.assertNotIn(Field.WIND_DIRECTION, got)  # variable wind
        self.assertEqual(got[Field.CLOUD_COVER].value, 100)
        self.assertAlmostEqual(got[Field.VISIBILITY].value, 4.8, places=1)
        self.assertAlmostEqual(got[Field.RAIN_NOW].distance_km, 14.9, delta=0.5)


class EsohTest(unittest.TestCase):
    def test_parse_units_and_names(self):
        data = {
            "parameters": {
                "precipitation_amount:0.0:sum:PT10M": {"unit": {"symbol": {"value": "kg/m²"}}},
                "wind_speed:10.0:mean:PT10M": {"unit": {"symbol": {"value": "m/s"}}},
                "surface_snow_thickness:0.0:point:PT0S": {"unit": {"symbol": {"value": "cm"}}},
                "cloud_area_fraction:0.0:point:PT0S": {"unit": {"symbol": {"value": "okta"}}},
            },
            "coverages": [
                {
                    "metocean:wigosId": "0-250-0-07150",
                    "domain": {"axes": {"x": {"values": [2.35]}, "y": {"values": [48.90]},
                                        "t": {"values": ["2026-11-09T11:40:00Z", "2026-11-09T11:50:00Z"]}}},
                    "ranges": {
                        "precipitation_amount:0.0:sum:PT10M": {"values": [0.2, None]},
                        "wind_speed:10.0:mean:PT10M": {"values": [3.0, 4.0]},
                        "surface_snow_thickness:0.0:point:PT0S": {"values": [None, 0]},
                        "cloud_area_fraction:0.0:point:PT0S": {"values": [8, 6]},
                    },
                }
            ],
        }
        got = {r.field: r for r in esoh.parse(data, HOME, "x")}
        self.assertEqual(got[Field.INTENSITY].value, 1.2)  # 0.2 mm in 10 min
        self.assertTrue(got[Field.RAIN_NOW].value)
        self.assertEqual(got[Field.WIND_SPEED].value, 14.4)
        self.assertEqual(got[Field.SNOW_DEPTH].value, 0)
        self.assertEqual(got[Field.CLOUD_COVER].value, 75)
        self.assertEqual(got[Field.WIND_SPEED].observed, datetime(2026, 11, 9, 11, 50, tzinfo=timezone.utc))


def _png_rgba(width: int, height: int, pixels: dict[tuple[int, int], tuple[int, int, int, int]]) -> bytes:
    raw = bytearray()
    for y in range(height):
        raw.append(1 if y % 2 else 0)  # alternate Sub and None filters
        prev = (0, 0, 0, 0)
        for x in range(width):
            px = pixels.get((x, y), (0, 0, 0, 0))
            if y % 2:
                raw.extend((px[i] - prev[i]) & 0xFF for i in range(4))
            else:
                raw.extend(px)
            prev = px

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(bytes(raw))) + chunk(b"IEND", b"")


class PngTest(unittest.TestCase):
    def test_pixel(self):
        data = _png_rgba(4, 3, {(2, 1): (10, 20, 30, 255), (3, 1): (40, 50, 60, 128), (1, 2): (1, 2, 3, 4)})
        self.assertEqual(png.pixel(data, 2, 1), (10, 20, 30, 255))
        self.assertEqual(png.pixel(data, 3, 1), (40, 50, 60, 128))
        self.assertEqual(png.pixel(data, 1, 2), (1, 2, 3, 4))
        self.assertEqual(png.pixel(data, 0, 0), (0, 0, 0, 0))


class RadarTest(unittest.TestCase):
    def test_colour_table_halves(self):
        text = "dBZ / RGBA,Universal Blue\n10,#00000000\n20,#112233ff\n10,#00000000\n20,#445566ff\n"
        table = rainviewer.parse_colour_table(text)
        self.assertEqual(table[(0x11, 0x22, 0x33, 0xFF)], (20.0, False))
        self.assertEqual(table[(0x44, 0x55, 0x66, 0xFF)], (20.0, True))

    def test_marshall_palmer(self):
        self.assertAlmostEqual(rainviewer.rain_rate(23.0), 1.0, delta=0.05)


def r(field, value, kind, minutes=5, dist=None, elev=None, source="s", sid=None, priority=0):
    return Reading(field=field, value=value, kind=kind, source=source,
                   observed=NOW - timedelta(minutes=minutes), distance_km=dist, elevation=elev,
                   station_id=sid, priority=priority)


class FusionTest(unittest.TestCase):
    def test_chain_order(self):
        got = fuse([
            r(Field.RAIN_NOW, False, Kind.AIRPORT, dist=12),
            r(Field.RAIN_NOW, True, Kind.RADAR, dist=0),
        ], HOME, NOW)
        self.assertIs(got[Field.RAIN_NOW].reading.kind, Kind.RADAR)

    def test_stale_and_far_are_skipped(self):
        got = fuse([
            r(Field.RAIN_NOW, True, Kind.RADAR, minutes=60),
            r(Field.RAIN_NOW, True, Kind.STATION, dist=50),
            r(Field.RAIN_NOW, False, Kind.AIRPORT, dist=20),
        ], HOME, NOW)
        self.assertIs(got[Field.RAIN_NOW].reading.kind, Kind.AIRPORT)

    def test_nearest_station_wins(self):
        got = fuse([
            r(Field.WIND_SPEED, 30.0, Kind.STATION, dist=25),
            r(Field.WIND_SPEED, 10.0, Kind.STATION, dist=8),
        ], HOME, NOW)
        self.assertEqual(got[Field.WIND_SPEED].reading.value, 10.0)
        self.assertEqual(got[Field.WIND_SPEED].candidates, 2)

    def test_mountain_station_skipped_for_wind(self):
        got = fuse([
            r(Field.WIND_SPEED, 60.0, Kind.STATION, dist=5, elev=1200),
            r(Field.WIND_SPEED, 12.0, Kind.AIRPORT, dist=20, elev=90),
        ], HOME, NOW)
        self.assertEqual(got[Field.WIND_SPEED].reading.value, 12.0)

    def test_snow_observed_zero_beats_model(self):
        got = fuse([
            r(Field.SNOW_DEPTH, 0.0, Kind.STATION, minutes=120, dist=10),
            r(Field.SNOW_DEPTH, 14.0, Kind.MODEL, minutes=30),
        ], HOME, NOW)
        self.assertFalse(got[Field.SNOW_ON_GROUND].reading.value)

    def test_snow_model_fallback(self):
        got = fuse([r(Field.SNOW_DEPTH, 14.0, Kind.MODEL, minutes=30)], HOME, NOW)
        self.assertTrue(got[Field.SNOW_ON_GROUND].reading.value)

    def test_type_follows_rain_now(self):
        got = fuse([
            r(Field.RAIN_NOW, True, Kind.RADAR),
            r(Field.PRECIP_TYPE, PrecipType.NONE.value, Kind.AIRPORT, dist=14),
            r(Field.PRECIP_TYPE, PrecipType.RAIN.value, Kind.RADAR),
        ], HOME, NOW)
        self.assertEqual(got[Field.PRECIP_TYPE].reading.value, PrecipType.RAIN.value)
        got = fuse([
            r(Field.RAIN_NOW, False, Kind.RADAR),
            r(Field.PRECIP_TYPE, PrecipType.SNOW.value, Kind.AIRPORT, dist=14),
        ], HOME, NOW)
        self.assertEqual(got[Field.PRECIP_TYPE].reading.value, PrecipType.NONE.value)

    def test_missing_value_marker_is_dropped(self):
        data = {"coverages": [{
            "metocean:wigosId": "x",
            "domain": {"axes": {"x": {"values": [2.35]}, "y": {"values": [48.86]}, "t": {"values": ["2026-11-09T11:50:00Z"]}}},
            "parameters": {"surface_snow_thickness:0.0:point:PT0S": {"unit": {"symbol": {"value": "cm"}}}},
            "ranges": {"surface_snow_thickness:0.0:point:PT0S": {"values": [-32767]}},
        }]}
        self.assertEqual(esoh.parse(data, HOME, "x"), [])

    def test_snowfall_alone_is_not_snow_cover(self):
        got = fuse([r(Field.PRECIP_TYPE, PrecipType.SNOW.value, Kind.RADAR)], HOME, NOW)
        self.assertNotIn(Field.SNOW_ON_GROUND, got)


class DeriveTest(unittest.TestCase):
    def test_humidity_and_dew_point_round_trip(self):
        rh = derive.humidity_from_dew_point(20.0, 10.0)
        self.assertAlmostEqual(rh, 52.5, delta=0.5)  # psychrometric tables give about 52 %
        self.assertAlmostEqual(derive.dew_point_from_humidity(20.0, rh), 10.0, delta=0.05)
        self.assertEqual(derive.humidity_from_dew_point(5.0, 5.0), 100.0)

    def test_apparent_temperature(self):
        # BoM formula by hand: e = 0.5 * 6.105 * exp(17.27*20/257.7) = 11.66 hPa
        # AT = 20 + 0.33*11.66 - 0.70*(18/3.6) - 4.00 = 16.35
        self.assertAlmostEqual(derive.apparent_temperature(20.0, 50.0, 18.0), 16.35, delta=0.01)

    def test_solar_elevation(self):
        # Paris at the June solstice, 12:00 UTC: about 64.5 degrees (90 - 48.86 + 23.44).
        june = datetime(2026, 6, 21, 12, tzinfo=timezone.utc)
        self.assertAlmostEqual(derive.solar_elevation(48.8566, 2.3522, june), 64.4, delta=1.0)
        december_midnight = datetime(2026, 12, 21, 0, tzinfo=timezone.utc)
        self.assertLess(derive.solar_elevation(48.8566, 2.3522, december_midnight), -40)

    def test_cardinal(self):
        self.assertEqual(derive.cardinal(0), "N")
        self.assertEqual(derive.cardinal(359), "N")
        self.assertEqual(derive.cardinal(225), "SW")
        self.assertEqual(derive.cardinal(100), "E")


class ConditionTest(unittest.TestCase):
    base = dict(precipitating=False, precip_type="none", intensity_class="dry", thunder=False, fog=False,
                wind_kmh=10.0, cloud_pct=10.0, sun_elevation=30.0)

    def c(self, **kw):
        return condition(**{**self.base, **kw})

    def test_order(self):
        self.assertEqual(self.c(), Condition.SUNNY)
        self.assertEqual(self.c(sun_elevation=-5), Condition.CLEAR_NIGHT)
        self.assertEqual(self.c(cloud_pct=50), Condition.PARTLYCLOUDY)
        self.assertEqual(self.c(cloud_pct=75), Condition.CLOUDY)
        self.assertEqual(self.c(wind_kmh=45, cloud_pct=80), Condition.WINDY_VARIANT)
        self.assertEqual(self.c(wind_kmh=45), Condition.WINDY)
        self.assertEqual(self.c(fog=True, wind_kmh=45), Condition.FOG)
        self.assertEqual(self.c(precipitating=True, precip_type="rain", intensity_class="light", fog=True), Condition.RAINY)
        self.assertEqual(self.c(precipitating=True, precip_type="rain", intensity_class="heavy"), Condition.POURING)
        self.assertEqual(self.c(precipitating=True, precip_type="snow"), Condition.SNOWY)
        self.assertEqual(self.c(precipitating=True, precip_type="freezing_rain"), Condition.SNOWY_RAINY)
        self.assertEqual(self.c(precipitating=True, precip_type="hail"), Condition.HAIL)
        self.assertEqual(self.c(precipitating=True, precip_type="rain", thunder=True), Condition.LIGHTNING_RAINY)
        self.assertEqual(self.c(thunder=True), Condition.LIGHTNING)

    def test_unknown_cloud_gives_nothing(self):
        self.assertIsNone(self.c(cloud_pct=None))


class NewFieldsTest(unittest.TestCase):
    def test_excluded_station_is_skipped(self):
        readings = [
            r(Field.TEMPERATURE, 30.0, Kind.STATION, dist=3, sid="bad"),
            r(Field.TEMPERATURE, 12.0, Kind.STATION, dist=9, sid="good"),
        ]
        self.assertEqual(fuse(readings, HOME, NOW)[Field.TEMPERATURE].reading.value, 30.0)
        got = fuse(readings, HOME, NOW, Settings(excluded_stations=frozenset({"bad"})))
        self.assertEqual(got[Field.TEMPERATURE].reading.value, 12.0)

    def test_user_limits(self):
        readings = [r(Field.WIND_SPEED, 20.0, Kind.STATION, dist=35)]
        self.assertIn(Field.WIND_SPEED, fuse(readings, HOME, NOW))
        limits = {**DEFAULT_LIMITS, Group.WIND: GroupLimits(30, 50, 400)}
        self.assertNotIn(Field.WIND_SPEED, fuse(readings, HOME, NOW, Settings(limits=limits)))

    def test_temperature_height_limit(self):
        got = fuse([
            r(Field.TEMPERATURE, 2.0, Kind.STATION, dist=5, elev=400),  # 365 m above the test point
            r(Field.TEMPERATURE, 8.0, Kind.AIRPORT, dist=20, elev=60),
        ], HOME, NOW)
        self.assertEqual(got[Field.TEMPERATURE].reading.value, 8.0)

    def test_users_weather_entity_before_default_model(self):
        got = fuse([
            r(Field.TEMPERATURE, 9.0, Kind.MODEL, source="open_meteo", priority=1),
            r(Field.TEMPERATURE, 7.0, Kind.MODEL, source="weather.x"),
        ], HOME, NOW)
        self.assertEqual(got[Field.TEMPERATURE].reading.source, "weather.x")

    def test_derived_values(self):
        got = fuse([
            r(Field.TEMPERATURE, 20.0, Kind.STATION, dist=3),
            r(Field.HUMIDITY, 50.0, Kind.STATION, dist=3),
            r(Field.WIND_SPEED, 18.0, Kind.STATION, dist=3),
            r(Field.WIND_DIRECTION, 225.0, Kind.STATION, dist=3),
            r(Field.RAIN_NOW, False, Kind.RADAR),
            r(Field.CLOUD_COVER, 100.0, Kind.MODEL),
        ], HOME, NOW)
        self.assertAlmostEqual(got[Field.APPARENT_TEMPERATURE].reading.value, 16.3, delta=0.05)
        self.assertEqual(got[Field.WIND_DIRECTION].details["cardinal"], "SW")
        self.assertEqual(got[Field.CONDITION].reading.value, "cloudy")
        self.assertEqual(got[Field.CONDITION].details["based_on"]["precipitation"], "radar:s")

    def test_last_precipitation(self):
        wet_at = (NOW - timedelta(minutes=40)).isoformat()
        got = fuse([r(Field.RAIN_NOW, False, Kind.RADAR), r(Field.LAST_PRECIP, wet_at, Kind.RADAR)], HOME, NOW)
        self.assertEqual(got[Field.LAST_PRECIP].reading.value, wet_at)
        # Raining now: now wins.
        got = fuse([r(Field.RAIN_NOW, True, Kind.RADAR, minutes=2), r(Field.LAST_PRECIP, wet_at, Kind.RADAR)], HOME, NOW)
        self.assertEqual(got[Field.LAST_PRECIP].reading.value, (NOW - timedelta(minutes=2)).isoformat())
        # Dry and nothing new: the remembered time stays.
        old = fuse([r(Field.RAIN_NOW, False, Kind.RADAR), r(Field.LAST_PRECIP, wet_at, Kind.RADAR)], HOME, NOW)
        later = NOW + timedelta(hours=5)
        got = fuse([r(Field.RAIN_NOW, False, Kind.RADAR)], HOME, later, previous_last_precip=old[Field.LAST_PRECIP])
        self.assertEqual(got[Field.LAST_PRECIP].reading.value, wet_at)


class SourceParseTest(unittest.TestCase):
    def test_esoh_qudt_units_and_new_fields(self):
        t = "2026-11-09T11:50:00Z"

        def param(qudt, text):
            # Some live symbols arrive with broken encoding; the QUDT id decides.
            return {"unit": {"label": {"en": text}, "symbol": {"value": "Ã‚Â°C", "type": f"https://qudt.org/vocab/unit/{qudt}"}}}

        data = {"coverages": [{
            "metocean:wigosId": "0-1-2-3",
            "domain": {"axes": {"x": {"values": [2.36]}, "y": {"values": [48.86]}, "t": {"values": [t]}}},
            "parameters": {
                "air_temperature:2.0:point:PT0S": param("DEG_C", "Cel"),
                "air_pressure_at_mean_sea_level:0.0:point:PT0S": param("PA", "Pa"),
                "precipitation_amount:2.0:point:PT1H": param("KiloGM-PER-M2", "kg/m2"),
            },
            "ranges": {
                "air_temperature:2.0:point:PT0S": {"values": [7.5]},
                "air_pressure_at_mean_sea_level:0.0:point:PT0S": {"values": [101320]},
                "precipitation_amount:2.0:point:PT1H": {"values": [0.4]},
            },
        }]}
        parsed = esoh.parse(data, HOME, "x", names={"0-1-2-3": "Test Park"}, elevations={"0-1-2-3": 40.0})
        got = {x.field: x for x in parsed}
        self.assertEqual(got[Field.TEMPERATURE].value, 7.5)
        self.assertEqual(got[Field.PRESSURE].value, 1013.2)
        self.assertEqual(got[Field.PRECIP_LAST_HOUR].value, 0.4)
        self.assertEqual(got[Field.LAST_PRECIP].value, "2026-11-09T11:50:00+00:00")
        self.assertEqual(got[Field.TEMPERATURE].station, "Test Park (0-1-2-3)")
        self.assertEqual(got[Field.TEMPERATURE].station_id, "0-1-2-3")
        self.assertEqual(got[Field.TEMPERATURE].elevation, 40.0)

    def test_esoh_locations(self):
        data = {"features": [{"id": "0-250-0-07156", "geometry": {"coordinates": [2.3378, 48.8217]},
                              "properties": {"name": "PARIS_MONTSOURIS"}}]}
        self.assertEqual(esoh.parse_locations(data)["0-250-0-07156"][0], "Paris Montsouris")

    def test_metar_temperature_humidity_pressure(self):
        rep = [{"icaoId": "KORD", "obsTime": int(NOW.timestamp()), "lat": 41.98, "lon": -87.9, "elev": 201,
                "name": "Chicago/O'Hare Intl, IL, US", "temp": 15, "dewp": 3.9, "altim": 1018, "slp": 1018.2,
                "wspd": 8, "wdir": 210, "visib": "10+", "clouds": [], "rawOb": "KORD ... SKC"}]
        got = {x.field: x for x in metar.parse(rep, Location(41.88, -87.63, 180.0), "x")}
        self.assertEqual(got[Field.TEMPERATURE].value, 15.0)
        self.assertEqual(got[Field.PRESSURE].value, 1018.2)
        self.assertAlmostEqual(got[Field.HUMIDITY].value, 47.0, delta=1.0)
        self.assertEqual(got[Field.TEMPERATURE].station, "Chicago/O'Hare Intl (KORD)")

    def test_open_meteo_parse(self):
        start = NOW - timedelta(hours=24)
        times = [(start + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(25)]
        data = {"current": {"time": "2026-11-09T11:45", "temperature_2m": 8.2, "snow_depth": 0.14, "wind_gusts_10m": 16.6},
                "hourly": {"time": times, "precipitation": [0.1] * 25}}
        got = {x.field: x for x in open_meteo.parse(data, NOW)}
        self.assertEqual(got[Field.SNOW_DEPTH].value, 14.0)
        self.assertEqual(got[Field.TEMPERATURE].priority, 1)
        self.assertEqual(got[Field.PRECIP_LAST_24H].value, 2.4)


class RadarHistoryTest(unittest.TestCase):
    def test_last_hour(self):
        frames = [rainviewer.Frame(NOW - timedelta(minutes=10 * i), 6.0 if i < 3 else 0.0, False) for i in range(13)]
        self.assertEqual(rainviewer.last_hour_mm(frames, NOW), 3.0)  # three 10-min frames at 6 mm/h
        self.assertIsNone(rainviewer.last_hour_mm(frames[:4], NOW))

    def test_coverage_pixel(self):
        self.assertTrue(rainviewer.covered((255, 255, 255, 0)))
        self.assertFalse(rainviewer.covered((0, 0, 0, 255)))


class ImsTest(unittest.TestCase):
    def test_cell_of_the_pole(self):
        self.assertEqual(ims.cell(90.0, 0.0), (3072, 3072))
        row, col = ims.cell(48.8566, 2.3522)
        self.assertTrue(0 <= row < ims.SIZE and 0 <= col < ims.SIZE)

    def test_newest_file(self):
        html = '<a href="ims2026312_00UTC_4km_v1.3.asc.gz"></a><a href="ims2026313_00UTC_4km_v1.3.asc.gz"></a>'
        name, valid = ims.newest_file(html)
        self.assertEqual(name, "ims2026313_00UTC_4km_v1.3.asc.gz")
        self.assertEqual(valid, datetime(2026, 11, 9, tzinfo=timezone.utc))

    def test_read_cells_bottom_row_first(self):
        import gzip
        rows = ["0" * ims.SIZE for _ in range(ims.SIZE)]
        rows[0] = "4" + "2" * (ims.SIZE - 1)  # the file's first data row is the bottom of the map
        gz = gzip.compress(("header line\n" + "\n".join(rows) + "\n").encode("ascii"))
        self.assertEqual(ims.read_cells(gz, ims.SIZE - 1, 0, radius=0), [["4"]])

    def test_coast_uses_land_around(self):
        self.assertTrue(ims.snow_from_cells([["1", "4", "4"], ["1", "1", "4"], ["1", "1", "2"]]))
        self.assertFalse(ims.snow_from_cells([["1", "2", "2"], ["1", "1", "2"], ["1", "1", "4"]]))
        self.assertIsNone(ims.snow_from_cells([["1"] * 3] * 3))
        self.assertTrue(ims.snow_from_cells([["2"] * 3, ["2", "4", "2"], ["2"] * 3]))


if __name__ == "__main__":
    unittest.main()
