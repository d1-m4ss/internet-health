"""Custom time windows (from/to) on every history endpoint and the packet-loss report (/api/loss)."""
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
import unittest.mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
from fixture_db import make_database  # noqa: E402

T0 = 2_000_000_000
STEP = 30


class LossWindowTests(unittest.TestCase):
    def setUp(self):
        from netprobe_extensions import Extension, install_routes
        from flask import Flask
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "netprobe.sqlite"
        make_database(self.db)
        self.now = T0
        self.ext = Extension(str(self.db), str(self.root), clock=lambda: self.now)
        app = Flask(__name__)
        install_routes(app, self.ext)
        self.client = app.test_client()
        # two hours of probes: a has a 3-sample episode (50%) and a lone 25% sample, b a 4-sample outage (100%)
        loss_a = {600: 50, 630: 50, 660: 50, 3000: 25}
        loss_b = {5010: 100, 5040: 100, 5070: 100, 5100: 100}
        for i in range(0, 7200, STEP):
            ts = T0 + i
            rows = [self.ping("a.example", loss_a.get(i, 0)), self.ping("b.example", loss_b.get(i, 0))]
            self.ext.record_probe(ts, rows, {"9.9.9.9": 12.0})
        self.now = T0 + 7200
        with sqlite3.connect(self.db) as conn:  # native aggregate rows for /api/history
            for i in range(0, 7200, 300):
                conn.execute("INSERT INTO measurements(ts,avg_latency_ms,avg_jitter_ms,avg_loss_pct,avg_dns_latency_ms,score) VALUES (?,?,?,?,?,?)",
                             (T0 + i, 20, 1, 5 if i == 600 else 0, 4, 95))

    @staticmethod
    def ping(target, loss):
        ok = loss < 100
        return {"host": target, "address": "192.0.2.1", "latency": 20 if ok else None, "jitter": 1 if ok else None, "loss": loss, "measured": ok}

    def get(self, path, **params):
        query = "&".join("%s=%s" % kv for kv in params.items())
        return self.client.get(path + ("?" + query if query else ""))

    # -- windows -------------------------------------------------------------
    def test_history_endpoints_respect_an_absolute_window(self):
        f, t = T0 + 580, T0 + 700
        history = self.get("/api/history", **{"from": f, "to": t}).get_json()["data"]
        self.assertTrue(history and all(f <= r["ts"] <= t for r in history), history)
        target = self.get("/api/targets/history", target="a.example", **{"from": f, "to": t}).get_json()["data"]
        self.assertTrue(target and all(f <= r["ts"] <= t for r in target))
        self.assertEqual(max(r["max_loss"] for r in target), 50)
        recent = {r["target"]: r for r in self.get("/api/targets/recent", **{"from": f, "to": t}).get_json()["data"]}
        self.assertEqual(recent["a.example"]["samples"], 4)  # 600, 630, 660, 690
        self.assertEqual(recent["a.example"]["max_loss"], 50)
        self.assertEqual(recent["b.example"]["max_loss"], 0)
        csv = self.get("/api/export/targets.csv", **{"from": f, "to": t}).get_data(as_text=True).splitlines()
        self.assertEqual(len(csv), 1 + 4 * 3, "export is limited to the window: 4 probes x (2 ping + 1 DNS) rows plus the header")

    def test_without_from_and_to_the_relative_range_still_works(self):
        a = self.get("/api/targets/recent", range=3600).get_json()["data"]
        b = {r["target"]: r["samples"] for r in a}
        self.assertEqual(b["a.example"], 120)
        self.assertEqual(self.get("/api/history", range=600).status_code, 200)

    def test_invalid_windows_are_rejected(self):
        for params in ({"from": T0 + 100}, {"to": T0 + 100}, {"from": T0 + 200, "to": T0 + 100}, {"from": "x", "to": "y"},
                       {"from": 0, "to": 10}, {"from": T0 - 40 * 86400, "to": T0 + 7000}, {"from": T0, "to": T0}):
            for path in ("/api/history", "/api/targets/recent", "/api/events", "/api/loss", "/api/export/targets.csv"):
                self.assertEqual(self.get(path, **params).status_code, 400, (path, params))

    def test_a_window_ending_in_the_future_is_clamped_to_now(self):
        r = self.get("/api/loss", **{"from": T0 + 3600, "to": T0 + 10 ** 6})
        self.assertEqual(r.status_code, 200)
        self.assertLessEqual(r.get_json()["data"]["window"]["to"], self.now)

    # -- loss report ---------------------------------------------------------
    def test_loss_report_lists_episodes_with_peak_duration_and_per_target_totals(self):
        data = self.get("/api/loss", **{"from": T0, "to": T0 + 7200}).get_json()["data"]
        episodes = data["episodes"]
        self.assertEqual(len(episodes), 3, episodes)
        newest_first = [(e["target"], e["start"], e["end"], e["peak"], e["samples"]) for e in episodes]
        self.assertEqual(newest_first, [("b.example", T0 + 5010, T0 + 5100, 100, 4), ("a.example", T0 + 3000, T0 + 3000, 25, 1), ("a.example", T0 + 600, T0 + 660, 50, 3)])
        self.assertEqual(episodes[2]["duration"], 60)
        s = data["summary"]
        self.assertEqual((s["lossy"], s["episodes"], s["max_loss"]), (8, 3, 100))
        self.assertGreater(s["samples"], 400)
        self.assertAlmostEqual(s["avg_loss"], (50 * 3 + 25 + 100 * 4) / s["samples"], places=4)
        per = {t["target"]: t for t in data["targets"]}
        self.assertEqual((per["a.example"]["lossy"], per["a.example"]["episodes"], per["a.example"]["max_loss"]), (4, 2, 50))
        self.assertEqual((per["b.example"]["lossy"], per["b.example"]["episodes"], per["b.example"]["max_loss"]), (4, 1, 100))
        self.assertNotIn("dns:9.9.9.9", per, "DNS servers have no packet-loss measurement")

    def test_loss_report_window_and_target_filter(self):
        only_b = self.get("/api/loss", target="b.example", **{"from": T0, "to": T0 + 7200}).get_json()["data"]
        self.assertEqual({e["target"] for e in only_b["episodes"]}, {"b.example"}); self.assertEqual([t["target"] for t in only_b["targets"]], ["b.example"])
        early = self.get("/api/loss", **{"from": T0 + 500, "to": T0 + 900}).get_json()["data"]
        self.assertEqual([e["start"] for e in early["episodes"]], [T0 + 600])
        quiet = self.get("/api/loss", **{"from": T0 + 1000, "to": T0 + 2000}).get_json()["data"]
        self.assertEqual((quiet["episodes"], quiet["summary"]["lossy"], quiet["summary"]["max_loss"]), ([], 0, 0))

    def test_samples_further_apart_than_the_gap_limit_are_separate_episodes(self):
        with sqlite3.connect(self.db) as conn:
            for ts in (T0 + 100, T0 + 130, T0 + 1000):  # 30 s apart -> one episode, then a 15 minute gap -> another
                conn.execute("INSERT INTO np_targets(cycle_ts,ts,target,address,kind,latency,jitter,loss) VALUES (?,?,?,?,?,?,?,?)",
                             (ts + 1, ts, "c.example", "192.0.2.3", "ping", 20.0, 1.0, 25.0))
        data = self.get("/api/loss", target="c.example", **{"from": T0, "to": T0 + 7200}).get_json()["data"]
        self.assertEqual([(e["start"], e["end"], e["samples"]) for e in data["episodes"]][::-1], [(T0 + 100, T0 + 130, 2), (T0 + 1000, T0 + 1000, 1)])

    def test_loss_report_is_bounded_and_says_so(self):
        with sqlite3.connect(self.db) as conn:
            rows = [(T0 + 7300 + i * 600, T0 + 7300 + i * 600, "d.example", "192.0.2.4", "ping", 20.0, 1.0, 10.0) for i in range(150)]
            conn.executemany("INSERT INTO np_targets(cycle_ts,ts,target,address,kind,latency,jitter,loss) VALUES (?,?,?,?,?,?,?,?)", rows)
        self.now = T0 + 7300 + 150 * 600 + 100
        data = self.get("/api/loss", target="d.example", **{"from": T0 + 7200, "to": self.now}).get_json()
        self.assertEqual(len(data["data"]["episodes"]), 100); self.assertTrue(data["truncated"])
        self.assertEqual(data["data"]["summary"]["episodes"], 150, "summary counts every episode even when the list is capped")

    def test_row_cap_is_reported_separately_from_the_episode_cap(self):
        import netprobe_extensions
        full = self.get("/api/loss", **{"from": T0, "to": T0 + 7200}).get_json()
        self.assertFalse(full["data"]["rows_capped"])
        self.assertFalse(full["truncated"])
        with unittest.mock.patch.object(netprobe_extensions, "LOSS_ROWS", 100):
            capped = self.get("/api/loss", **{"from": T0, "to": T0 + 7200}).get_json()
        self.assertTrue(capped["data"]["rows_capped"], "scanning stopped early: the report must say it is incomplete")
        self.assertFalse(capped["truncated"], "the episode list itself was not cut")
        self.assertEqual(capped["data"]["summary"]["samples"], 100)



if __name__ == "__main__":
    unittest.main()
