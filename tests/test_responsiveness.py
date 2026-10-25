"""The web worker must stay free: manual speedtests run in the background, answers are compressed and revalidated."""
import gzip
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
from fixture_db import make_database  # noqa: E402


class AppTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "netprobe.sqlite"
        make_database(self.db)
        env = {"DB_PATH": str(self.db), "DB_ENGINE": "sqlite", "USE_POSTGRES": "false", "SPEEDTEST_ENABLED": "false",
               "ROUTER_IP": "192.168.1.1", "SITES": "google.com", "DNS_NAMESERVER_1_IP": "8.8.8.8",
               "DNS_NAMESERVER_2_IP": "", "DNS_NAMESERVER_3_IP": "", "DNS_NAMESERVER_4_IP": ""}
        patcher = patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        spec = importlib.util.spec_from_file_location("tested_responsive_app", ROOT / "src/app.py")
        self.module = importlib.util.module_from_spec(spec)
        with patch("threading.Thread.start"):
            spec.loader.exec_module(self.module)
        # in the container the page is /app/templates/index.html; give the app the same layout
        (Path(self.tmp.name) / "templates").mkdir()
        (Path(self.tmp.name) / "templates" / "index.html").write_bytes((ROOT / "src/index.html").read_bytes())
        self.module.app.root_path = self.tmp.name
        self.client = self.module.app.test_client()


class BackgroundSpeedtestTests(AppTestCase):
    RESULT = {"timestamp": 1, "ping_ms": 10.0, "download_mbps": 100.0, "upload_mbps": 50.0, "backend": "python",
              "server": {"name": "Example"}}

    def slow_test(self, outcome):
        """A fake run_speedtest_internal that holds the real speedtest lock until released."""
        self.release = threading.Event()
        self.entered = threading.Event()

        def run(**_kwargs):
            with self.module.speedtest_run_lock:
                self.entered.set()
                self.release.wait(10)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        return patch.object(self.module, "run_speedtest_internal", side_effect=run)

    def wait_done(self):
        deadline = time.time() + 5
        while time.time() < deadline:
            status = self.client.get("/api/speedtest/status").get_json()
            if not status["running"]:
                return status
            time.sleep(0.02)
        self.fail("the background test did not finish")

    def test_start_returns_at_once_and_the_rest_of_the_app_stays_responsive(self):
        with self.slow_test(self.RESULT):
            started = time.monotonic()
            response = self.client.post("/api/speedtest/run", json={"backend": "python", "async": True})
            self.assertEqual((response.status_code, response.get_json()), (202, {"success": True, "started": True}))
            self.assertLess(time.monotonic() - started, 1)
            self.assertTrue(self.entered.wait(5))
            began = time.monotonic()
            for path in ("/", "/api/config", "/api/targets/recent", "/api/speedtest/latest", "/api/prefs"):
                self.assertEqual(self.client.get(path).status_code, 200, path)
            self.assertLess(time.monotonic() - began, 3, "pages are served while the test runs")
            status = self.client.get("/api/speedtest/status").get_json()
            self.assertTrue(status["running"])
            self.assertIsNone(status["success"])
            self.release.set()
            done = self.wait_done()
        self.assertEqual((done["success"], done["error"], done["result"]["download_mbps"]), (True, None, 100.0))
        self.assertIsNotNone(done["finished_at"])

    def test_a_second_start_is_refused_while_one_runs(self):
        with self.slow_test(self.RESULT):
            self.assertEqual(self.client.post("/api/speedtest/run", json={"async": True}).status_code, 202)
            self.assertTrue(self.entered.wait(5))
            again = self.client.post("/api/speedtest/run", json={"async": True})
            self.assertEqual(again.status_code, 409)
            self.assertIn("already running", again.get_json()["error"])
            self.release.set()
            self.wait_done()

    def test_a_scheduled_test_counts_as_running_and_blocks_a_manual_start(self):
        self.module.speedtest_run_lock.acquire()
        try:
            self.assertTrue(self.client.get("/api/speedtest/status").get_json()["running"])
            self.assertEqual(self.client.post("/api/speedtest/run", json={"async": True}).status_code, 409)
        finally:
            self.module.speedtest_run_lock.release()
        self.assertFalse(self.client.get("/api/speedtest/status").get_json()["running"])

    def test_a_failure_is_kept_for_the_ui_and_the_next_test_can_start(self):
        with self.slow_test(self.module.SpeedtestRunError("fast.com is not reachable: timed out")):
            self.client.post("/api/speedtest/run", json={"async": True})
            self.release.set()
            done = self.wait_done()
        self.assertEqual((done["success"], done["error"], done["result"]), (False, "fast.com is not reachable: timed out", None))
        with self.slow_test(self.RESULT):
            self.release.set()
            self.assertEqual(self.client.post("/api/speedtest/run", json={"async": True}).status_code, 202)
            self.assertTrue(self.wait_done()["success"])

    def test_real_run_through_the_job_stores_the_result(self):
        fast = {"ping_ms": 11.5, "download_mbps": 321.5, "upload_mbps": None, "server": {"id": None, "name": "Netflix, Example City", "host": "oca", "country": "RO"},
                "jitter_ms": 1.0, "packet_loss_pct": None, "isp": None, "result_url": None, "protocol": "https", "secure": True}
        with patch.object(self.module.custom_extension, "run_fast_speedtest", return_value=fast):
            self.client.post("/api/speedtest/run", json={"backend": "fast", "async": True})
            done = self.wait_done()
        self.assertEqual((done["success"], done["result"]["backend"], done["result"]["upload_mbps"]), (True, "fast", None))
        self.assertEqual(self.client.get("/api/speedtest/latest").get_json()["result"]["backend"], "fast")

    def test_the_old_synchronous_call_still_works_for_scripts(self):
        with patch.object(self.module, "run_speedtest_internal", return_value=self.RESULT):
            body = self.client.post("/api/speedtest/run", json={"backend": "python"}).get_json()
        self.assertEqual((body["success"], body["result"]["download_mbps"]), (True, 100.0))

    def test_only_a_real_true_starts_a_background_job(self):
        with patch.object(self.module, "run_speedtest_internal", return_value=self.RESULT) as run:
            for value in ("true", 1, "yes"):
                body = self.client.post("/api/speedtest/run", json={"async": value}).get_json()
                self.assertTrue(body["success"] and "started" not in body, "non-boolean async runs the legacy way")
        self.assertEqual(run.call_count, 3)


class CompressionTests(AppTestCase):
    def test_page_is_compressed_for_browsers_and_identical_when_decompressed(self):
        plain = self.client.get("/")
        packed = self.client.get("/", headers={"Accept-Encoding": "gzip, deflate, br"})
        self.assertNotIn("Content-Encoding", plain.headers)
        self.assertEqual(packed.headers["Content-Encoding"], "gzip")
        self.assertIn("Accept-Encoding", packed.headers["Vary"])
        self.assertEqual(gzip.decompress(packed.data), plain.data)
        self.assertLess(len(packed.data), len(plain.data) / 3)
        self.assertEqual(int(packed.headers["Content-Length"]), len(packed.data))

    def test_page_revalidates_with_an_etag_and_a_reload_costs_no_body(self):
        first = self.client.get("/", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(first.headers["Cache-Control"], "no-cache")
        etag = first.headers["ETag"]
        self.assertTrue(etag.startswith("W/"))
        again = self.client.get("/", headers={"Accept-Encoding": "gzip", "If-None-Match": etag})
        self.assertEqual((again.status_code, again.data), (304, b""))
        other = self.client.get("/", headers={"If-None-Match": 'W/"something-else"'})
        self.assertEqual(other.status_code, 200)

    def test_json_and_csv_answers_are_compressed_when_large_enough(self):
        for path in ("/api/export/targets.csv?range=86400", "/api/config"):
            response = self.client.get(path, headers={"Accept-Encoding": "gzip"})
            raw = self.client.get(path).data
            body = gzip.decompress(response.data) if response.headers.get("Content-Encoding") == "gzip" else response.data
            self.assertEqual(body, raw, path)
        self.assertEqual(self.client.get("/api/config", headers={"Accept-Encoding": "gzip"}).headers.get("Content-Encoding"), "gzip")

    def test_small_answers_and_other_methods_are_left_alone(self):
        small = self.client.get("/api/events?range=3600", headers={"Accept-Encoding": "gzip"})
        self.assertNotIn("Content-Encoding", small.headers)
        self.assertEqual(small.get_json(), {"data": []})
        posted = self.client.post("/api/prefs", json={"show_public_ip": False}, headers={"X-NetProbe-Request": "settings", "Accept-Encoding": "gzip"})
        self.assertNotIn("Content-Encoding", posted.headers)
        self.assertEqual(posted.status_code, 200)

    def test_clients_without_gzip_get_the_page_unchanged(self):
        response = self.client.get("/", headers={"Accept-Encoding": "identity"})
        self.assertNotIn("Content-Encoding", response.headers)
        self.assertIn(b"Internet Health", response.data)


class BrandTests(AppTestCase):
    def test_title_and_icon_are_the_product_ones_and_need_no_extra_file(self):
        page = self.client.get("/").data.decode()
        self.assertIn("<title>Internet Health · Unraid monitor</title>", page)
        self.assertRegex(page, r'<link rel="icon" type="image/svg\+xml" href="data:image/svg\+xml,%3Csvg')
        self.assertNotIn("NetProbe Enhanced", page, "the old product name is gone from the page")
        self.assertIn('<span class="brand-icon" aria-hidden="true"><svg ', page)


if __name__ == "__main__":
    unittest.main()
