"""Jitter, packet loss and the delay under load are kept next to each test and served with the series the chart draws."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
import netprobe_extensions as npx  # noqa: E402
from test_speedtest_schedule import Base  # noqa: E402

DAY = 86400
NOW = 1_800_000_000


class ExtraTests(Base):
    def setUp(self):
        self.make_ext(NOW)

    def test_extras_are_stored_by_test_time_and_joined_in_the_series(self):
        self.add_test(NOW - 100, down=400.0, up=90.0, ping=12.0)
        self.add_test(NOW - 50, down=380.0, up=85.0, ping=13.0)
        self.assertTrue(self.ext.save_speedtest_extra(NOW - 100, {"jitter_ms": 1.5, "packet_loss_pct": 0.0, "loaded_ping_ms": 48.2, "loaded_up_ping_ms": 95.0}))
        series = self.client.get("/api/speedtest/series?range=3600").get_json()["tests"]
        self.assertEqual([t["ts"] for t in series], [NOW - 100, NOW - 50], "oldest first")
        self.assertEqual({k: series[0][k] for k in ("jitter_ms", "packet_loss_pct", "loaded_down_ms", "loaded_up_ms", "download_mbps", "backend")},
                         {"jitter_ms": 1.5, "packet_loss_pct": 0.0, "loaded_down_ms": 48.2, "loaded_up_ms": 95.0, "download_mbps": 400.0, "backend": "fast"})
        self.assertEqual((series[1]["jitter_ms"], series[1]["loaded_down_ms"]), (None, None), "a test without extras has unknown, not zero")

    def test_nothing_is_stored_when_nothing_was_measured(self):
        self.assertFalse(self.ext.save_speedtest_extra(NOW, {"jitter_ms": None, "ping_ms": 5}))
        self.assertFalse(self.ext.save_speedtest_extra(NOW, {"jitter_ms": float("nan"), "loaded_ping_ms": float("inf"), "packet_loss_pct": "3"}))
        self.assertFalse(self.ext.save_speedtest_extra(NOW, {"jitter_ms": True}))
        with self.ext.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM np_speedtest_extra").fetchone()[0], 0)

    def test_the_range_limits_the_series(self):
        for i, age in enumerate((10 * DAY, 3 * DAY, 3600)):
            self.add_test(NOW - age, down=100.0 + i)
        self.assertEqual(len(self.client.get("/api/speedtest/series?range=%d" % DAY).get_json()["tests"]), 1)
        self.assertEqual(len(self.client.get("/api/speedtest/series").get_json()["tests"]), 2, "the default is a week")
        self.assertEqual(len(self.client.get("/api/speedtest/series?range=%d" % (30 * DAY)).get_json()["tests"]), 3)
        self.assertEqual(self.client.get("/api/speedtest/series?range=0").status_code, 400)
        self.assertEqual(self.client.get("/api/speedtest/series?range=x").status_code, 400)

    def test_the_series_is_capped(self):
        for i in range(5):
            self.add_test(NOW - 10 * i - 1)
        self.assertEqual(len(self.ext.speedtest_series(DAY, limit=3)), 3)
        self.assertEqual([t["ts"] for t in self.ext.speedtest_series(DAY, limit=3)], [NOW - 21, NOW - 11, NOW - 1], "the newest three, oldest first")

    def test_retention_removes_extras_with_their_tests(self):
        old, recent = NOW - 100 * DAY, NOW - DAY
        self.add_test(old)
        self.add_test(recent)
        self.ext.save_speedtest_extra(old, {"jitter_ms": 1.0})
        self.ext.save_speedtest_extra(recent, {"jitter_ms": 2.0})
        self.ext.cleanup(90)
        with self.ext.db() as conn:
            self.assertEqual([r[0] for r in conn.execute("SELECT ts FROM np_speedtest_extra")], [recent])
            self.assertEqual([r[0] for r in conn.execute("SELECT ts FROM speedtests")], [recent])


if __name__ == "__main__":
    unittest.main()
