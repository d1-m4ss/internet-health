"""Speedtest schedule (daily times / every N hours) and the notification after an automatic test."""
from datetime import datetime, timedelta, timezone
import importlib.util
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
from fixture_db import make_database  # noqa: E402
import netprobe_extensions as npx  # noqa: E402

BUCHAREST = ZoneInfo("Europe/Bucharest")
HEADERS = {"X-NetProbe-Request": "settings"}


def local(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=BUCHAREST).timestamp()


class Base(unittest.TestCase):
    def make_ext(self, now):
        from flask import Flask
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "netprobe.sqlite"
        make_database(self.db)
        self.conn = sqlite3.connect(self.db)
        self.addCleanup(self.conn.close)
        self.conn.execute("DELETE FROM speedtests")
        self.conn.commit()
        env = patch.dict(os.environ, {"APP_TIMEZONE": "Europe/Bucharest"})
        env.start()
        self.addCleanup(env.stop)
        self.now = now
        self.ext = npx.Extension(str(self.db), str(self.root), clock=lambda: self.now)
        app = Flask(__name__)
        npx.install_routes(app, self.ext)
        self.client = app.test_client()

    def prefs(self, **values):
        self.ext.update_prefs(values)

    def add_test(self, ts, down=400.0, up=90.0, ping=12.0, backend="fast"):
        self.conn.execute("INSERT INTO speedtests(ts,ping_ms,download_mbps,upload_mbps,backend,server_name,server_country) VALUES (?,?,?,?,?,?,?)",
                          (int(ts), ping, down, up, backend, "Netflix, Brasov", "RO"))
        self.conn.commit()


class ValidationTests(Base):
    def setUp(self):
        self.make_ext(local(2026, 3, 10, 12))

    def test_defaults_keep_todays_behaviour(self):
        values = self.ext.prefs()
        self.assertEqual((values["speedtest_schedule"], values["speedtest_notify"]), ("container", False))
        self.assertIsNone(self.ext.speedtest_due(self.now))

    def test_invalid_schedule_values_are_rejected(self):
        for bad in ({"speedtest_schedule": "weekly"}, {"speedtest_schedule": None}, {"speedtest_interval_h": 0},
                    {"speedtest_interval_h": 169}, {"speedtest_interval_h": True}, {"speedtest_interval_h": "6"},
                    {"speedtest_interval_h": 1.5}, {"speedtest_times": []}, {"speedtest_times": "03:00"},
                    {"speedtest_times": ["03:00", "03:00"]}, {"speedtest_times": ["24:00"]}, {"speedtest_times": ["3:00"]},
                    {"speedtest_times": ["03:60"]}, {"speedtest_times": ["03:00", "04:00", "05:00", "06:00", "07:00"]},
                    {"speedtest_times": [300]}, {"speedtest_notify": "yes"}, {"speedtest_notify": 1}):
            with self.subTest(bad):
                with self.assertRaises(ValueError):
                    self.ext.update_prefs(bad)
                self.assertEqual(self.client.post("/api/prefs", json=bad, headers=HEADERS).status_code, 400)
        self.assertFalse(self.ext.prefs_path.exists())

    def test_valid_values_are_stored_exactly(self):
        body = {"speedtest_schedule": "daily", "speedtest_interval_h": 12, "speedtest_times": ["03:00", "15:30"], "speedtest_notify": True}
        saved = self.client.post("/api/prefs", json=body, headers=HEADERS).get_json()["prefs"]
        self.assertEqual({k: saved[k] for k in body}, body)
        self.assertEqual(npx.Extension(str(self.db), str(self.root)).prefs()["speedtest_times"], ["03:00", "15:30"])


class ScheduleTests(Base):
    def setUp(self):
        self.make_ext(local(2026, 3, 10, 3, 10))
        self.prefs(speedtest_schedule="daily", speedtest_times=["03:00"])

    def test_off_never_runs_and_container_defers(self):
        self.prefs(speedtest_schedule="off")
        self.assertIs(self.ext.speedtest_due(self.now), False)
        self.prefs(speedtest_schedule="container")
        self.assertIsNone(self.ext.speedtest_due(self.now))

    def test_daily_runs_once_after_the_time_and_not_again(self):
        self.assertTrue(self.ext.speedtest_due(self.now), "03:10, nothing measured since yesterday")
        self.add_test(local(2026, 3, 10, 3, 2))
        self.assertFalse(self.ext.speedtest_due(self.now), "already measured after 03:00")
        self.now = local(2026, 3, 10, 23, 59)
        self.assertFalse(self.ext.speedtest_due(self.now))
        self.now = local(2026, 3, 11, 3, 0)
        self.assertTrue(self.ext.speedtest_due(self.now), "next day")

    def test_a_failed_attempt_is_not_retried_every_cycle(self):
        self.assertTrue(self.ext.speedtest_due(self.now, last_attempt=0))
        self.assertFalse(self.ext.speedtest_due(self.now, last_attempt=self.now - 50))

    def test_missed_slot_catches_up_for_two_hours_only(self):
        self.now = local(2026, 3, 10, 4, 59)
        self.assertTrue(self.ext.speedtest_due(self.now), "container restarted at 03:00, back before 05:00")
        self.now = local(2026, 3, 10, 5, 1)
        self.assertFalse(self.ext.speedtest_due(self.now), "too late: wait for the next slot instead of a daytime test")

    def test_before_the_time_nothing_runs_even_without_any_history(self):
        self.now = local(2026, 3, 10, 2, 59)
        self.assertFalse(self.ext.speedtest_due(self.now), "yesterday's slot is long past the catch-up window")

    def test_several_times_a_day(self):
        self.prefs(speedtest_times=["03:00", "15:30"])
        self.add_test(local(2026, 3, 10, 3, 5))
        self.now = local(2026, 3, 10, 15, 29)
        self.assertFalse(self.ext.speedtest_due(self.now))
        self.now = local(2026, 3, 10, 15, 31)
        self.assertTrue(self.ext.speedtest_due(self.now))

    def test_manual_test_near_the_slot_counts(self):
        self.add_test(local(2026, 3, 10, 3, 9))
        self.assertFalse(self.ext.speedtest_due(self.now))

    def test_times_use_the_container_timezone(self):
        now_utc = datetime(2026, 3, 10, 3, 10, tzinfo=timezone.utc).timestamp()
        self.assertFalse(self.ext.speedtest_due(now_utc), "03:10 UTC is 05:10 in Bucharest: the 03:00 slot is past its catch-up window")
        with patch.dict(os.environ, {"APP_TIMEZONE": "UTC"}):
            self.assertTrue(self.ext.speedtest_due(now_utc), "the same instant is 03:10 in UTC")
        with patch.dict(os.environ, {"APP_TIMEZONE": "Nowhere/Land"}):
            self.assertEqual(str(self.ext.tz), "UTC", "an unknown zone falls back to UTC instead of failing")

    def test_clocks_going_back_do_not_run_the_slot_twice(self):
        # 2026-10-25: Bucharest goes from 04:00 EEST back to 03:00 EET, so 03:xx happens twice
        self.now = local(2026, 10, 25, 3, 10)
        self.assertTrue(self.ext.speedtest_due(self.now))
        self.add_test(self.now)
        self.now += 3600  # the repeated 03:10
        self.assertFalse(self.ext.speedtest_due(self.now))

    def test_next_test_is_reported(self):
        self.assertEqual(self.ext.next_speedtest(self.now), local(2026, 3, 11, 3, 0))
        self.prefs(speedtest_times=["03:00", "15:30"])
        self.assertEqual(self.ext.next_speedtest(self.now), local(2026, 3, 10, 15, 30))
        self.prefs(speedtest_schedule="off")
        self.assertIsNone(self.ext.next_speedtest(self.now))
        data = self.client.get("/api/prefs").get_json()
        self.assertEqual((data["schedule"]["next_ts"], data["schedule"]["timezone"]), (None, "Europe/Bucharest"))

    def test_api_shows_the_next_daily_test(self):
        data = self.client.get("/api/prefs").get_json()
        self.assertEqual(data["schedule"], {"next_ts": local(2026, 3, 11, 3, 0), "timezone": "Europe/Bucharest"})


class IntervalTests(Base):
    def setUp(self):
        self.make_ext(local(2026, 3, 10, 12))
        self.prefs(speedtest_schedule="interval", speedtest_interval_h=6)

    def test_waits_for_the_interval_since_the_last_test(self):
        self.add_test(self.now - 5 * 3600)
        self.assertFalse(self.ext.speedtest_due(self.now))
        self.assertEqual(self.ext.next_speedtest(self.now), self.now + 3600)
        self.now += 3600
        self.assertTrue(self.ext.speedtest_due(self.now))

    def test_runs_when_nothing_was_ever_measured_and_respects_attempts(self):
        self.assertTrue(self.ext.speedtest_due(self.now))
        self.assertFalse(self.ext.speedtest_due(self.now, last_attempt=self.now - 60))
        self.assertEqual(self.ext.next_speedtest(self.now), self.now, "overdue: now")

    def test_damaged_database_never_starts_a_test(self):
        with patch.object(self.ext, "last_speedtest_ts", side_effect=sqlite3.OperationalError("locked")):
            self.assertIs(self.ext.speedtest_due(self.now), False)


class NotificationTests(Base):
    def setUp(self):
        self.make_ext(local(2026, 3, 10, 3, 10))
        self.result = {"download_mbps": 482.34, "upload_mbps": 91.0, "ping_ms": 12.04, "backend": "fast",
                       "server": {"name": "Netflix, Brasov", "country": "RO"}}

    def queued(self):
        return self.conn.execute("SELECT level,subject,description,message FROM np_notifications ORDER BY id").fetchall()

    def test_nothing_is_queued_unless_asked(self):
        self.assertIsNone(self.ext.notify_speedtest(self.result))
        self.assertEqual(self.queued(), [])

    def test_result_message_is_readable_and_safe_for_unraid_agents(self):
        self.prefs(speedtest_notify=True)
        self.add_test(local(2026, 3, 9, 3, 1), down=470.0, up=88.0)
        self.add_test(self.now, down=482.34, up=91.0)
        self.assertIsNotNone(self.ext.notify_speedtest(self.result))
        (level, subject, description, message), = self.queued()
        self.assertEqual(level, "normal")
        self.assertEqual(subject, "📶 NetProbe: Speedtest ↓ 482 / ↑ 91")
        self.assertEqual(description, "Scheduled Speedtest (Fast.com): download 482.3 Mbps, upload 91.0 Mbps.")
        lines = message.split("\\n")
        self.assertEqual(lines[0], "📶 Scheduled Speedtest (Fast.com)")
        self.assertIn("• Download: 482.3 Mbps", lines)
        self.assertIn("• Upload: 91.0 Mbps", lines)
        self.assertIn("• Ping: 12.0 ms", lines)
        self.assertIn("• Server: Netflix, Brasov, RO", lines)
        self.assertEqual([l for l in lines if l.startswith("• Previous")], ["• Previous: ↓ 470 / ↑ 88 Mbps (03:01, 09.03)"])
        self.assertTrue(re.fullmatch(r"Time: \d\d:\d\d, 10\.03\.2026 .+", lines[-1]), lines[-1])
        self.assertNotRegex(message.replace("\\n", ""), r"[\x00-\x1f\\]")

    def test_unmeasured_upload_is_never_a_number(self):
        self.prefs(speedtest_notify=True)
        self.result["upload_mbps"] = None
        self.ext.notify_speedtest(self.result)
        (_, subject, description, message), = self.queued()
        self.assertEqual(subject, "📶 NetProbe: Speedtest ↓ 482 / ↑ —")
        self.assertIn("upload not measured", description)
        self.assertIn("• Upload: not measured", message)

    def test_failure_is_a_warning_with_a_sanitized_reason(self):
        self.prefs(speedtest_notify=True)
        self.ext.notify_speedtest(error=RuntimeError("fast.com is not reachable:\ntimed out \\n injected"), backend="fast")
        (level, subject, description, message), = self.queued()
        self.assertEqual((level, subject), ("warning", "⚠️ NetProbe: Speedtest failed (Fast.com)"))
        self.assertIn("• Reason: fast.com is not reachable: timed out n injected", message)
        self.assertNotIn("\\ninjected", message)
        self.assertNotRegex(message.replace("\\n", ""), r"[\x00-\x1f\\]")

    def test_master_switch_of_notifications_is_respected(self):
        self.prefs(speedtest_notify=True)
        self.ext.update_settings({"enabled": False})
        self.assertIsNone(self.ext.notify_speedtest(self.result))
        self.assertEqual(self.queued(), [])

    def test_delivered_through_the_normal_queue_with_the_bridge_key(self):
        self.prefs(speedtest_notify=True)
        self.ext.notify_speedtest(self.result)
        key = (self.root / "notify-token").read_text().strip()
        pending = self.client.get("/api/notifications/pending", headers={"X-NetProbe-Notify-Key": key}).get_json()["notifications"]
        self.assertEqual([p["level"] for p in pending], ["normal"])
        self.assertEqual(self.client.get("/api/notifications/pending").status_code, 403)

    def test_a_speedtest_warning_does_not_cover_a_turbo_outage(self):
        self.prefs(speedtest_notify=True)
        self.ext.turbo_configure(None, ["1.1.1.1"])
        self.ext.notify_speedtest(error=RuntimeError("boom"), backend="python")
        row = {"start_ts": int(self.now) - 30, "end_ts": int(self.now) + 30}
        with self.ext.db() as conn:
            self.assertFalse(self.ext.turbo_covered(conn, row))


class AppSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "netprobe.sqlite"
        make_database(self.db)
        env = {"DB_PATH": str(self.db), "DB_ENGINE": "sqlite", "USE_POSTGRES": "false", "SPEEDTEST_ENABLED": "false",
               "SPEEDTEST_INTERVAL": "3600", "APP_TIMEZONE": "UTC", "ROUTER_IP": "192.168.1.1", "SITES": "google.com",
               "DNS_NAMESERVER_1_IP": "8.8.8.8", "DNS_NAMESERVER_2_IP": "", "DNS_NAMESERVER_3_IP": "", "DNS_NAMESERVER_4_IP": ""}
        patcher = patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        spec = importlib.util.spec_from_file_location("tested_schedule_app", ROOT / "src/app.py")
        self.module = importlib.util.module_from_spec(spec)
        with patch("threading.Thread.start"):
            spec.loader.exec_module(self.module)
        self.ext = self.module.custom_extension
        self.module.last_speedtest_ts = 0
        self.result = {"download_mbps": 300.0, "upload_mbps": 50.0, "ping_ms": 9.0, "backend": "python", "server": {"name": "Example", "country": "EX"}}

    def queued(self):
        return sqlite3.connect(self.db).execute("SELECT level,subject FROM np_notifications ORDER BY id").fetchall()

    def run_due(self, outcome):
        with patch.object(self.module, "run_speedtest_internal", side_effect=[outcome] if isinstance(outcome, Exception) else None,
                          return_value=None if isinstance(outcome, Exception) else outcome) as run:
            self.module.run_speedtest_if_due()
        return run

    def local_time_in(self, hours):
        return (datetime.now(timezone.utc) + timedelta(hours=hours)).strftime("%H:%M")

    def test_container_mode_with_the_variable_off_never_runs(self):
        self.assertEqual(self.run_due(self.result).call_count, 0)

    def test_container_mode_keeps_the_interval_variable(self):
        with patch.object(self.module, "SPEEDTEST_ENABLED", True):
            self.assertEqual(self.run_due(self.result).call_count, 1)
            self.assertEqual(self.run_due(self.result).call_count, 0, "interval not over yet")

    def test_daily_schedule_runs_even_when_the_container_variable_is_off(self):
        self.ext.update_prefs({"speedtest_schedule": "daily", "speedtest_times": [self.local_time_in(-0.1)], "speedtest_notify": True})
        self.assertEqual(self.run_due(self.result).call_count, 1)
        self.assertEqual(self.queued(), [("normal", "📶 NetProbe: Speedtest ↓ 300 / ↑ 50")])

    def test_daily_schedule_waits_for_its_time(self):
        self.ext.update_prefs({"speedtest_schedule": "daily", "speedtest_times": [self.local_time_in(6)]})
        self.assertEqual(self.run_due(self.result).call_count, 0)

    def test_off_stops_the_container_interval_too(self):
        self.ext.update_prefs({"speedtest_schedule": "off"})
        with patch.object(self.module, "SPEEDTEST_ENABLED", True):
            self.assertEqual(self.run_due(self.result).call_count, 0)

    def test_a_failure_is_reported_once_and_not_retried_every_cycle(self):
        self.ext.update_prefs({"speedtest_schedule": "interval", "speedtest_interval_h": 1, "speedtest_notify": True, "speedtest_service": "fast"})
        self.assertEqual(self.run_due(self.module.SpeedtestRunError("fast.com is not reachable: timed out")).call_count, 1)
        self.assertEqual(self.queued(), [("warning", "⚠️ NetProbe: Speedtest failed (Fast.com)")])
        self.assertEqual(self.run_due(self.result).call_count, 0, "the next probe cycle must not hammer a broken service")
        self.assertEqual(len(self.queued()), 1)

    def test_no_message_without_the_notify_switch(self):
        self.ext.update_prefs({"speedtest_schedule": "interval", "speedtest_interval_h": 1})
        self.assertEqual(self.run_due(self.result).call_count, 1)
        self.assertEqual(self.queued(), [])

    def test_a_running_test_postpones_instead_of_failing(self):
        self.ext.update_prefs({"speedtest_schedule": "interval", "speedtest_interval_h": 1, "speedtest_notify": True})
        self.module.speedtest_run_lock.acquire()
        try:
            self.assertEqual(self.run_due(self.result).call_count, 0)
        finally:
            self.module.speedtest_run_lock.release()
        self.assertEqual(self.queued(), [])
        self.assertEqual(self.run_due(self.result).call_count, 1, "the slot is still pending after the manual test finished")

    def test_probe_loop_starts_the_scheduler_even_when_the_container_variable_is_off(self):
        started = []

        class Stop(Exception):
            pass
        with patch.object(self.module, "get_default_gateway", return_value=None), \
             patch.object(self.module, "run_ping", return_value={"host": "x", "latency": 5.0, "jitter": 1.0, "loss": 0.0,
                                                                  "address": "192.0.2.1", "measured": True, "sample_ts": 1}), \
             patch.object(self.module, "measure_dns_latency_multi", return_value=3.0), \
             patch.object(self.module.threading, "Thread", side_effect=lambda **kw: started.append(kw["target"]) or type("T", (), {"start": lambda s: None})()), \
             patch.object(self.module.time, "sleep", side_effect=Stop):
            with self.assertRaises(Stop):
                self.module.probe_loop()
        self.assertIn(self.module.run_speedtest_if_due, started)


if __name__ == "__main__":
    unittest.main()
