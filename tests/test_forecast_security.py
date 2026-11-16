"""Tests for the forecast, the nowcast and the safety rules. Run: python -m unittest discover -s tests"""

from __future__ import annotations

import gzip
import math
import struct
import sys
import unittest
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "bellweather"))

from core import nowcast as nc  # noqa: E402
from core import png, safety  # noqa: E402
from core.forecast import blend, history, members, met_nowcast, skill  # noqa: E402
from core.forecast.engine import merge  # noqa: E402
from core.fusion import Choice, fuse  # noqa: E402
from core.model import Field, Kind, Location, Reading  # noqa: E402
from core.sources import ims, metar, rainviewer  # noqa: E402

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
HOME = Location(48.8566, 2.3522, 35.0)


def hours_back(n: int) -> list[datetime]:
    return [NOW - timedelta(hours=h) for h in range(n, 0, -1)]


class MembersTest(unittest.TestCase):
    def test_amount_is_shifted_to_the_hour_it_falls_in(self):
        data = {"hourly": {
            "time": ["2026-11-09T12:00", "2026-11-09T13:00", "2026-11-09T14:00"],
            "temperature_2m_a": [5.0, 6.0, 7.0], "precipitation_a": [0.0, 1.5, 0.2],
            "temperature_2m_b": [None, None, None],
        }}
        got = members.parse_forecast(data, ["a", "b"])
        self.assertNotIn("b", got)  # no data: not a member
        noon = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
        self.assertEqual(got["a"][noon].temperature, 5.0)
        self.assertEqual(got["a"][noon].precipitation, 1.5)  # the 13:00 sum covers 12-13

    def test_broken_values_are_dropped(self):
        data = {"hourly": {"time": ["2026-11-09T12:00", "bad"], "temperature_2m": [float("nan"), 999], "cloud_cover": [150, 50]}}
        got = members.parse_forecast(data, ["a"])
        self.assertEqual(got, {})  # no finite, plausible temperature at all

    def test_previous_runs(self):
        data = {"hourly": {"time": ["2026-11-09T12:00", "2026-11-09T13:00"],
                           "temperature_2m_previous_day1_a": [5.0, 6.0], "precipitation_previous_day1_a": [0.0, 0.4]}}
        got = members.parse_previous(data, ["a"])
        noon = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
        self.assertEqual(got["a"][noon], {"t": 5.0, "p": 0.4})


class SkillTest(unittest.TestCase):
    def _data(self, days: int, bias_a: float, bias_b: float, own: bool = True):
        """own: the hours were measured at the home itself (own sensor)."""
        obs, prev = {}, {"a": {}, "b": {}}
        for i, t in enumerate(hours_back(days * 24)):
            true = 10 + 5 * math.sin(i / 7)
            obs[t] = {"t": true, "t_own": True} if own else {"t": true}
            prev["a"][t] = {"t": true + bias_a + (0.5 if i % 2 else -0.5)}
            prev["b"][t] = {"t": true + bias_b + (2.0 if i % 2 else -2.0)}
        return obs, prev

    def test_no_correction_before_20_days(self):
        obs, prev = self._data(10, 2.0, -1.0)
        self.assertEqual(skill.corrections(obs, prev, NOW).temperature, {})

    def test_bias_and_weights_after_20_days(self):
        obs, prev = self._data(25, 2.0, -1.0)
        corr = skill.corrections(obs, prev, NOW)
        self.assertAlmostEqual(corr.temperature["a"].bias, 2.0, delta=0.01)
        # a has error spread 0.5, b 2.0: weights 4 : 0.25
        value = skill.combine({"a": 12.0, "b": 9.0}, corr.temperature)
        self.assertAlmostEqual(value, (4 * (12 - 2) + 0.25 * (9 + 1)) / 4.25, delta=0.01)

    def test_hours_measured_elsewhere_remove_a_share_of_the_bias(self):
        obs, prev = self._data(25, 2.0, -1.0, own=False)
        corr = skill.corrections(obs, prev, NOW)
        a, b = corr.temperature["a"], corr.temperature["b"]
        self.assertAlmostEqual(a.bias, 2.0, delta=0.01)  # the error itself is the same
        self.assertAlmostEqual(a.removed, 2.0 * skill.ELSEWHERE_SHARE["t"], delta=0.01)
        share = skill.ELSEWHERE_SHARE["t"]
        value = skill.combine({"a": 12.0, "b": 9.0}, corr.temperature)  # the weights stay 4 : 0.25
        self.assertAlmostEqual(value, (4 * (12 - 2 * share) + 0.25 * (9 + share)) / 4.25, delta=0.01)
        self.assertAlmostEqual(b.removed, -1.0 * share, delta=0.01)
        # half the hours at home, half elsewhere: in between
        mixed, _ = self._data(25, 2.0, -1.0)
        for t in mixed:
            if t.toordinal() % 2:  # every other day (whole days, so the hourly noise cancels)
                mixed[t].pop("t_own")
        self.assertAlmostEqual(skill.corrections(mixed, prev, NOW).temperature["a"].removed, (2.0 + 2.0 * share) / 2, delta=0.02)
        self.assertEqual(skill.ELSEWHERE_SHARE["w"], 0.25)  # wind depends most on the site

    def test_plain_mean_without_stats(self):
        self.assertEqual(skill.combine({"a": 1.0, "b": 3.0}, {}), 2.0)

    def test_out_of_sample_score_beats_raw_members(self):
        obs, prev = self._data(60, 2.0, -1.0)
        sc = skill.score(obs, prev, "t", NOW)
        self.assertLess(sc.blend_mae, min(sc.member_mae.values()))
        self.assertEqual(sc.days, 30)

    def test_wet_rule(self):
        self.assertEqual(skill.wet_share({"a": 0.1, "b": 0.0, "c": 0.5, "d": 0.05}), 0.5)


class BlendTest(unittest.TestCase):
    def _hour(self, **kw):
        base = dict(temperature=10.0, humidity=80.0, wind_speed=10.0, wind_direction=0.0, cloud_cover=50.0,
                    precipitation=0.0, snow_water=0.0, weather_code=3)
        return members.Hour(**{**base, **kw})

    def test_vector_mean_of_directions(self):
        hours = {"a": self._hour(wind_direction=350.0), "b": self._hour(wind_direction=10.0)}
        got = blend.blend_hour(NOW, hours, skill.Corrections(), HOME)
        self.assertIn(got.wind_bearing, (0.0, 360.0))

    def test_half_of_members_wet_makes_the_hour_wet(self):
        hours = {"a": self._hour(precipitation=1.0), "b": self._hour(precipitation=0.0)}
        got = blend.blend_hour(NOW, hours, skill.Corrections(), HOME)
        self.assertEqual(got.precipitation_probability, 50)
        self.assertEqual(got.condition, "rainy")
        hours["b"] = self._hour(precipitation=0.0)
        hours["c"] = self._hour(precipitation=0.0)
        self.assertEqual(blend.blend_hour(NOW, hours, skill.Corrections(), HOME).precip_type, "none")

    def test_snow_share(self):
        hours = {m: self._hour(precipitation=2.0, snow_water=1.9, temperature=-1.0) for m in "abc"}
        self.assertEqual(blend.blend_hour(NOW, hours, skill.Corrections(), HOME).condition, "snowy")

    def test_days_need_most_members_for_a_wet_day(self):
        start = datetime(2026, 11, 10, tzinfo=timezone.utc)
        member_set = {}
        for name, wet in (("a", True), ("b", False), ("c", False)):
            member_set[name] = {start + timedelta(hours=h): self._hour(precipitation=0.5 if wet and h == 12 else 0.0)
                                for h in range(24)}
        hours = blend.blend_hours(member_set, skill.Corrections(), HOME, start)
        days = blend.blend_days(hours, member_set, timezone.utc, start.date())
        self.assertEqual(days[0].precipitation_probability, 0)  # 0.5 mm < 1 mm rain day
        self.assertNotIn(days[0].condition, ("rainy", "pouring"))


class HistoryTest(unittest.TestCase):
    def test_iem_parse(self):
        text = "station,valid,tmpc,sknt,wxcodes\nLFPB,2026-11-07 00:00,4.00,2.00,BCFG\nLFPB,2026-11-07 00:30,4.00,0.00,-RA\nLFPB,2026-11-07 00:50,M,M,M\n"
        got = history.parse_iem(text)
        h0 = datetime(2026, 11, 7, 0, tzinfo=timezone.utc)
        self.assertEqual(got[h0]["t"], 4.0)
        self.assertTrue(got[h0]["wet"])  # -RA at 00:30

    def test_log_keeps_only_measured_values(self):
        log = history.ObservationLog()
        t = datetime(2026, 11, 9, 11, 55, tzinfo=timezone.utc)
        model = Reading(field=Field.TEMPERATURE, value=9.0, kind=Kind.MODEL, source="open_meteo", observed=t)
        log.add({Field.TEMPERATURE: Choice(model, 1)}, NOW)
        self.assertEqual(log.hours, {})
        measured = Reading(field=Field.TEMPERATURE, value=8.0, kind=Kind.STATION, source="esoh", observed=t)
        log.add({Field.TEMPERATURE: Choice(measured, 1)}, NOW)
        self.assertEqual(log.hours[datetime(2026, 11, 9, 12, tzinfo=timezone.utc)]["t"], 8.0)

    def test_log_takes_own_sensors_only_when_sited_and_marks_them(self):
        t = datetime(2026, 11, 9, 11, 55, tzinfo=timezone.utc)
        hour = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
        own = Reading(field=Field.TEMPERATURE, value=12.0, kind=Kind.LOCAL, source="local", observed=t)
        station = Reading(field=Field.TEMPERATURE, value=8.0, kind=Kind.STATION, source="esoh", observed=t)
        log = history.ObservationLog()
        log.add({Field.TEMPERATURE: Choice(own, 1)}, NOW)
        self.assertEqual(log.hours, {})  # a sensor by a warm wall must not teach the forecast
        log.add({Field.TEMPERATURE: Choice(own, 1)}, NOW, own_sited=True)
        self.assertEqual(log.hours[hour], {"t": 12.0, "t_own": True})
        log.add({Field.TEMPERATURE: Choice(station, 1)}, NOW, own_sited=True)
        self.assertEqual(log.hours[hour], {"t": 8.0})  # measured elsewhere now: the mark goes
        log.add({Field.TEMPERATURE: Choice(own, 1)}, NOW, own_sited=True)
        back = history.ObservationLog(log.as_dict())
        self.assertEqual(back.hours[hour], {"t": 12.0, "t_own": True})
        junk = history.ObservationLog({"hours": {hour.isoformat(): {"w": 3, "t_own": True, "w_own": "yes"}}})
        self.assertEqual(junk.hours[hour], {"w": 3.0})  # a mark without its value, or not a bool, is dropped

    def test_own_barometer_counts_even_when_not_sited(self):
        t = datetime(2026, 11, 9, 11, 55, tzinfo=timezone.utc)
        hour = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
        baro = Reading(field=Field.PRESSURE, value=1012.0, kind=Kind.LOCAL, source="local", observed=t)
        log = history.ObservationLog()
        log.add({Field.PRESSURE: Choice(baro, 1)}, NOW)
        self.assertEqual(log.hours[hour], {"pr": 1012.0, "pr_own": True})  # where it hangs does not change the pressure

    def test_unsited_own_sensors_give_way_to_the_best_station_in_the_log(self):
        t = datetime(2026, 11, 9, 11, 55, tzinfo=timezone.utc)
        own_t = Reading(field=Field.TEMPERATURE, value=14.0, kind=Kind.LOCAL, source="local", observed=t)
        own_p = Reading(field=Field.PRESSURE, value=1012.0, kind=Kind.LOCAL, source="local", observed=t)
        station = Reading(field=Field.TEMPERATURE, value=9.0, kind=Kind.STATION, source="esoh", observed=t,
                          distance_km=5.0, elevation=40.0, station="A", station_id="a")
        readings = [own_t, own_p, station]
        shown = {Field.TEMPERATURE: Choice(own_t, 1), Field.PRESSURE: Choice(own_p, 1)}
        asked = []

        def refuse(rest):
            asked.append(rest)
            return {Field.TEMPERATURE: Choice(station, 1), Field.PRESSURE: Choice(station, 1)}

        got = history.choices_for_log(readings, shown, False, refuse)
        self.assertEqual(asked, [[station]])  # the fusion again, without the own sensors
        self.assertIs(got[Field.TEMPERATURE].reading, station)
        self.assertIs(got[Field.PRESSURE].reading, own_p)  # the own barometer stays
        self.assertIs(history.choices_for_log(readings, shown, True, refuse), shown)  # sited: what was shown
        self.assertIs(history.choices_for_log([station], shown, False, refuse), shown)  # no own sensors at all

    def test_old_logs_forget_values_that_may_be_an_unsited_own_sensor(self):
        hour = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
        old = history.ObservationLog({"hours": {hour.isoformat(): {"t": 14.0, "w": 5.0, "pr": 1012.0, "wet": False}}})
        self.assertTrue(old.legacy)
        old.drop_legacy({"t"})
        self.assertEqual(old.hours[hour], {"w": 5.0, "pr": 1012.0, "wet": False})
        old.drop_legacy({"w"})  # once only
        self.assertIn("w", old.hours[hour])
        new = history.ObservationLog(old.as_dict())
        self.assertFalse(new.legacy)
        new.drop_legacy({"t", "w"})
        self.assertIn("w", new.hours[hour])

    def test_merge_takes_each_value_from_where_it_was_measured(self):
        t = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
        home = {t: {"w": 3.0, "wet": False}}  # an own temperature sensor not sited: no temperature at home
        prev_home = {"a": {t: {"t": 12.0, "w": 4.0, "p": 0.0}}}
        air = {t: {"t": 9.0, "w": 20.0, "wet": True}}
        prev_air = {"a": {t: {"t": 10.0, "w": 18.0, "p": 1.0}}}
        obs, prev = merge(home, prev_home, air, prev_air)
        self.assertEqual(obs[t], {"t": 9.0, "w": 3.0, "wet": False})
        self.assertEqual(prev["a"][t], {"t": 10.0, "w": 4.0, "p": 0.0})
        from core.forecast.engine import _source
        self.assertEqual(_source(home, air, obs, prev_home), "mixed")
        self.assertIs(home[t].get("t"), None)  # the inputs are not changed

    def test_log_reload_drops_garbage(self):
        log = history.ObservationLog({"hours": {"2026-11-09T12:00:00+00:00": {"t": "nan", "w": 5}, "x": {}, "2026-11-09T13:00:00": {"t": 1}}})
        self.assertEqual(list(log.hours.values()), [{"w": 5.0}])

    def test_merge_prefers_home(self):
        t = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
        obs, prev = merge({t: {"t": 1.0}}, {"a": {t: {"t": 2.0}}}, {t: {"t": 5.0}}, {"a": {t: {"t": 6.0}}})
        self.assertEqual(obs[t]["t"], 1.0)
        self.assertEqual(prev["a"][t]["t"], 2.0)


class MetNowcastTest(unittest.TestCase):
    def test_parse_to_ten_minute_steps(self):
        series = [{"time": (NOW + timedelta(minutes=5 * i)).isoformat().replace("+00:00", "Z"),
                   "data": {"instant": {"details": {"precipitation_rate": 1.0 if i >= 4 else 0.0}}}} for i in range(25)]
        got = met_nowcast.parse({"properties": {"timeseries": series}}, NOW)
        self.assertEqual(got.method, "met_norway")
        self.assertEqual(got.steps[0].probability, 0.0)  # 5 and 10 min dry
        self.assertEqual(got.starts_in(False), 20)

    def test_outside_box_is_never_asked(self):
        self.assertFalse(met_nowcast.maybe_inside(Location(47.5, 19.0)))
        self.assertTrue(met_nowcast.maybe_inside(Location(59.9, 10.7)))


class NowcastTest(unittest.TestCase):
    def test_motion_and_steps(self):
        size = 2 * (nc.WINDOW_PX + nc.SEARCH_PX) + 1

        def grid(oy, ox):
            return [[5.0 if (math.sin((x - ox) / 9.0) + math.cos((y - oy) / 13.0)) > 0.6 else 0.0 for x in range(size)]
                    for y in range(size)]

        self.assertEqual(nc.motion(grid(0, 0), grid(4, 6)), (4.0, 6.0))
        cast = nc.nowcast_from_grids(grid(0, 0), grid(4, 6), 20, NOW, 48.85)
        self.assertEqual(cast.motion_towards, 124)  # south-east
        self.assertTrue(cast.steps)

    def test_dry_window_is_persistence(self):
        size = 2 * (nc.WINDOW_PX + nc.SEARCH_PX) + 1
        dry = [[0.0] * size for _ in range(size)]
        cast = nc.nowcast_from_grids(dry, dry, 20, NOW, 48.85)
        self.assertIsNone(cast.motion_kmh)
        self.assertFalse(cast.expected_within(60))

    def test_check_counts(self):
        events = [nc.CheckEvent(NOW, 30, True, False, True), nc.CheckEvent(NOW, 30, False, False, True)]
        got = nc.check(events, NOW)
        self.assertEqual(got[30]["nowcast"], 0.5)
        self.assertEqual(got[30]["persistence"], 0.0)


def _png(width, height, raw_rows: bytes, extra: bytes = b"") -> bytes:
    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw_rows + extra)) + chunk(b"IEND", b"")


class SafetyTest(unittest.TestCase):
    def test_finite(self):
        for bad in (None, True, "x", float("nan"), float("inf"), "1e999", [1]):
            self.assertIsNone(safety.finite(bad))
        self.assertEqual(safety.finite("2.5"), 2.5)

    def test_text_and_ids(self):
        self.assertEqual(safety.clean_text("Paris\x00\x1b[31m" + "x" * 200)[:8], "Paris[31")
        self.assertEqual(len(safety.clean_text("y" * 500)), 80)
        self.assertIsNone(safety.valid_id("../../etc/passwd"))
        self.assertIsNone(safety.valid_id("A B"))
        self.assertEqual(safety.valid_id("0-250-0-07156"), "0-250-0-07156")

    def test_errors_never_carry_the_url(self):
        class ClientError(Exception):
            status = 503
        err = ClientError("503, message='x', url='https://api.example/?latitude=48.85&longitude=2.35'")
        self.assertEqual(safety.safe_error(err), "ClientError (HTTP 503)")
        self.assertNotIn("48.85", safety.safe_error(ValueError("latitude=48.85")))

    def test_https_only(self):
        with self.assertRaises(safety.UnsafeResponse):
            safety.check_url("http://example.com/")
        safety.check_url("https://example.com/")

    def test_png_decompression_bomb_is_refused(self):
        row = b"\x00" + b"\x00" * 16  # 4 RGBA pixels, filter 0
        good = _png(4, 2, row * 2)
        self.assertEqual(png.pixel(good, 0, 1), (0, 0, 0, 0))
        bomb = _png(4, 2, row * 2, extra=b"\x00" * 5_000_000)
        with self.assertRaises(png.PngError):
            png.pixel(bomb, 0, 0)
        huge = _png(100_000, 100_000, b"")
        with self.assertRaises(png.PngError):
            png.pixel(huge, 0, 0)

    def test_ims_line_without_end_is_refused(self):
        gz = gzip.compress(b"header\n" + b"2" * 100_000)
        with self.assertRaises(safety.UnsafeResponse):
            ims.read_cells(gz, 10, 10)

    def test_radar_index_cannot_redirect_requests(self):
        good = {"host": "https://tilecache.rainviewer.com", "radar": {"past": [{"time": int(NOW.timestamp()), "path": "/v2/radar/82714e5fba4d"}]}}
        self.assertEqual(len(rainviewer.valid_frames(good, NOW)), 1)
        evil_host = {**good, "host": "https://attacker.example"}
        self.assertEqual(rainviewer.valid_frames(evil_host, NOW), [])
        evil_path = {"host": good["host"], "radar": {"past": [{"time": int(NOW.timestamp()), "path": "/../../x?y=1"}]}}
        self.assertEqual(rainviewer.valid_frames(evil_path, NOW), [])
        future = {"host": good["host"], "radar": {"past": [{"time": int((NOW + timedelta(hours=2)).timestamp()), "path": "/v2/radar/abcdef"}]}}
        self.assertEqual(rainviewer.valid_frames(future, NOW), [])

    def test_future_readings_never_count(self):
        r = Reading(field=Field.TEMPERATURE, value=30.0, kind=Kind.STATION, source="x",
                    observed=NOW + timedelta(hours=1), distance_km=1.0)
        self.assertNotIn(Field.TEMPERATURE, fuse([r], HOME, NOW))

    def test_metar_garbage(self):
        reports = [
            {"icaoId": "BAD ID", "obsTime": NOW.timestamp(), "lat": 1, "lon": 1},
            {"icaoId": "LFPB", "obsTime": NOW.timestamp(), "lat": float("nan"), "lon": 2.4},
            {"icaoId": "LFPG", "obsTime": NOW.timestamp(), "lat": 49.0, "lon": 2.5, "wxString": "RA" * 5000,
             "name": "x\x00" * 100, "temp": "inf"},
            "not a dict",
        ]
        got = metar.parse(reports, HOME, "x")
        self.assertTrue(all(r.station_id == "LFPG" for r in got))
        self.assertFalse(any(r.field is Field.TEMPERATURE for r in got))
        self.assertLessEqual(len(got[0].station), 90)


if __name__ == "__main__":
    unittest.main()
