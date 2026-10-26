"""Messages after manual Speedtests and the "slower than usual" warning (7-day median)."""
import sqlite3
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
import netprobe_extensions as npx  # noqa: E402
from test_speedtest_schedule import Base, HEADERS  # noqa: E402

DAY = 86400
NOW = 1_800_000_000


class NotifyBase(Base):
    def setUp(self):
        self.make_ext(NOW)

    def queued(self):
        return [tuple(r) for r in self.conn.execute("SELECT level,subject,description FROM np_notifications ORDER BY id")]

    def messages(self):
        return [r[0] for r in self.conn.execute("SELECT message FROM np_notifications ORDER BY id")]

    def result(self, down=400.0, up=90.0, backend="fast", ts=None):
        return {"timestamp": ts or self.now, "ping_ms": 12.0, "download_mbps": down, "upload_mbps": up, "backend": backend,
                "server": {"name": "Netflix, Example City", "country": "RO"}}

    def history(self, values, backend="fast", up=90.0):
        for i, value in enumerate(values):
            self.add_test(self.now - (len(values) - i) * DAY, down=value, up=up, backend=backend)


class ManualNotificationTests(NotifyBase):
    def test_off_by_default_for_manual_tests(self):
        self.prefs(speedtest_notify=True)
        self.assertEqual(self.ext.prefs()["speedtest_notify_manual"], False)
        self.assertIsNone(self.ext.notify_speedtest(self.result(), manual=True))
        self.assertIsNone(self.ext.notify_speedtest(error="boom", backend="fast", manual=True))
        self.assertEqual(self.queued(), [])

    def test_a_manual_result_and_a_manual_failure_are_announced_as_manual(self):
        self.prefs(speedtest_notify_manual=True)
        self.add_test(self.now - 3600, down=380.0, up=85.0)
        self.add_test(self.now, down=400.0, up=90.0)
        self.ext.notify_speedtest(self.result(), manual=True)
        self.now += 1
        self.ext.notify_speedtest(error=RuntimeError("fast.com is not reachable: timed out"), backend="fast", manual=True)
        (ok, bad) = self.queued()
        self.assertEqual(ok[:2], ("normal", "📶 Internet Health: Speedtest ↓ 400 / ↑ 90"))
        self.assertIn("Manual Speedtest (Fast.com)", ok[2])
        self.assertEqual(bad, ("warning", "⚠️ Internet Health: Speedtest failed (Fast.com)", "The manual Speedtest (Fast.com) failed: fast.com is not reachable: timed out"))
        text = self.messages()[0]
        self.assertIn("Manual Speedtest (Fast.com)", text)
        self.assertIn("• Previous: ↓ 380 / ↑ 85 Mbps", text)

    def test_the_two_switches_are_independent(self):
        self.prefs(speedtest_notify=True)                 # scheduled only
        self.ext.notify_speedtest(self.result())
        self.assertEqual(len(self.queued()), 1)
        self.prefs(speedtest_notify=False, speedtest_notify_manual=True)  # manual only
        self.now += 1
        self.ext.notify_speedtest(self.result())
        self.assertEqual(len(self.queued()), 1, "a scheduled test says nothing now")
        self.now += 1
        self.ext.notify_speedtest(self.result(), manual=True)
        self.assertEqual(len(self.queued()), 2)

    def test_the_global_switch_silences_it(self):
        self.prefs(speedtest_notify_manual=True)
        self.ext.update_settings({"enabled": False})
        self.assertIsNone(self.ext.notify_speedtest(self.result(), manual=True))

    def test_new_preferences_are_validated(self):
        for bad in ({"speedtest_notify_manual": "yes"}, {"speedtest_notify_manual": 1}, {"speedtest_drop_pct": 10}, {"speedtest_drop_pct": "30"},
                    {"speedtest_drop_pct": True}, {"speedtest_drop_pct": None}, {"speedtest_drop_pct": 100}):
            with self.assertRaises(ValueError, msg=bad):
                self.ext.validate_prefs(bad)
        for good in (0, 20, 30, 50):
            self.prefs(speedtest_drop_pct=good)
            self.assertEqual(self.ext.prefs()["speedtest_drop_pct"], good)
        self.assertEqual(self.client.post("/api/prefs", json={"speedtest_drop_pct": 25}, headers=HEADERS).status_code, 400)


class DropTests(NotifyBase):
    def setUp(self):
        super().setUp()
        self.prefs(speedtest_drop_pct=30)
        self.history([400, 410, 390, 405, 395])        # median 400

    def drop_check(self, down, up=90.0, backend="fast", manual=False, ts=None):
        self.now += DAY
        result = self.result(down, up, backend, ts or self.now)
        self.add_test(result["timestamp"], down=down, up=up, backend=backend)
        return self.ext.notify_speedtest(result, manual=manual)

    def test_a_drop_warns_once_and_recovery_is_announced_once(self):
        self.drop_check(260)                              # -35 %
        (level, subject, description), = self.queued()
        self.assertEqual((level, subject), ("warning", "📶 Internet Health: Speedtest slowed down (Fast.com)"))
        self.assertIn("30 %", description)
        text = self.messages()[0]
        self.assertIn("• Download: 260 Mbps — usually 400 Mbps (-35 %)", text)
        self.assertIn("median of 5 tests in the last 7 days", text)
        self.assertNotIn("Upload", text, "only what dropped is listed")
        self.drop_check(240)
        self.drop_check(250)
        self.assertEqual(len(self.queued()), 1, "no repeats while it stays low")
        self.drop_check(395)
        self.assertEqual([q[1] for q in self.queued()], ["📶 Internet Health: Speedtest slowed down (Fast.com)", "📶 Internet Health: Speedtest is back to normal (Fast.com)"])
        self.assertEqual(self.queued()[1][0], "normal")
        self.drop_check(400)
        self.assertEqual(len(self.queued()), 2, "normal stays quiet")
        self.drop_check(200)
        self.assertEqual(len(self.queued()), 3, "a new episode warns again")

    def test_a_small_dip_is_not_a_drop(self):
        self.drop_check(300)                              # -25 %, threshold 30 %
        self.assertEqual(self.queued(), [])

    def test_upload_drops_too(self):
        self.drop_check(400, up=40.0)
        self.assertIn("• Upload: 40 Mbps — usually 90 Mbps (-56 %)", self.messages()[0])

    def test_nothing_without_enough_history_or_with_another_service(self):
        for table in ("speedtests",):
            self.conn.execute("DELETE FROM " + table)
        self.conn.commit()
        self.history([400, 410], backend="fast")           # two tests: no "typical" yet
        self.drop_check(50)
        self.assertEqual(self.queued(), [])
        self.conn.execute("DELETE FROM speedtests")
        self.conn.commit()
        self.history([900, 950, 920, 940], backend="python")   # plenty of history, but for Speedtest.net
        self.drop_check(50)
        self.assertEqual(self.queued(), [], "a Fast.com result is not compared with Speedtest.net numbers")

    def test_old_tests_do_not_count(self):
        self.conn.execute("DELETE FROM speedtests")
        self.conn.commit()
        for i in range(5):
            self.add_test(self.now - 20 * DAY - i, down=900.0)
        self.drop_check(50)
        self.assertEqual(self.queued(), [])

    def test_off_means_off_and_manual_tests_are_not_judged(self):
        self.drop_check(100, manual=True)
        self.assertEqual(self.queued(), [], "a manual test can be made under load: no drop warning")
        self.prefs(speedtest_drop_pct=0)
        self.drop_check(100)
        self.assertEqual(self.queued(), [])

    def test_missing_numbers_are_not_zero(self):
        self.drop_check(400, up=None)
        self.assertEqual(self.queued(), [])

    def test_the_message_does_not_count_as_covering_an_outage(self):
        self.drop_check(100)
        with self.ext.db() as conn:
            row = {"start_ts": self.now - 30, "end_ts": self.now + 30}
            self.assertFalse(self.ext.turbo_covered(conn, row))

    def test_the_global_switch_silences_it(self):
        self.ext.update_settings({"enabled": False})
        self.drop_check(100)
        self.assertEqual(self.queued(), [])

    def test_a_damaged_state_file_does_not_stop_the_check(self):
        (self.root / "speedtest-state.json").write_text("{broken")
        self.drop_check(100)
        self.assertEqual(len(self.queued()), 1)


class AppHookTests(unittest.TestCase):
    """The web UI's manual test (a background job in app.py) reports to the extension."""
    def test_a_manual_job_queues_the_message_when_asked(self):
        from test_responsiveness import BackgroundSpeedtestTests
        case = BackgroundSpeedtestTests("test_real_run_through_the_job_stores_the_result")
        case.setUp()
        self.addCleanup(case.doCleanups)
        module, client = case.module, case.client
        module.custom_extension.update_prefs({"speedtest_notify_manual": True})
        with patch.object(module, "run_speedtest_internal", return_value=dict(case.RESULT, backend="fast")):
            client.post("/api/speedtest/run", json={"backend": "fast", "async": True})
            deadline = time.time() + 5
            while client.get("/api/speedtest/status").get_json()["running"] and time.time() < deadline:
                time.sleep(0.02)
        with module.custom_extension.db() as conn:
            rows = [tuple(r) for r in conn.execute("SELECT subject FROM np_notifications")]
        self.assertEqual(rows, [("📶 Internet Health: Speedtest ↓ 100 / ↑ 50",)])
        with patch.object(module, "run_speedtest_internal", side_effect=module.SpeedtestRunError("timed out")):
            client.post("/api/speedtest/run", json={"async": True})
            deadline = time.time() + 5
            while client.get("/api/speedtest/status").get_json()["running"] and time.time() < deadline:
                time.sleep(0.02)
        with module.custom_extension.db() as conn:
            subjects = [r[0] for r in conn.execute("SELECT subject FROM np_notifications ORDER BY id")]
        self.assertEqual(len(subjects), 2)
        self.assertIn("Speedtest failed", subjects[1])

    def test_without_the_switch_a_manual_job_says_nothing(self):
        from test_responsiveness import BackgroundSpeedtestTests
        case = BackgroundSpeedtestTests("test_real_run_through_the_job_stores_the_result")
        case.setUp()
        self.addCleanup(case.doCleanups)
        module, client = case.module, case.client
        with patch.object(module, "run_speedtest_internal", return_value=case.RESULT):
            client.post("/api/speedtest/run", json={"async": True})
            deadline = time.time() + 5
            while client.get("/api/speedtest/status").get_json()["running"] and time.time() < deadline:
                time.sleep(0.02)
        with module.custom_extension.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM np_notifications").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
