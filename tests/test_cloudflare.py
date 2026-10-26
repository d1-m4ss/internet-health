"""The Cloudflare speed test client against a local fake of speed.cloudflare.com, and its wiring as a service."""
import importlib.util
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
from fixture_db import make_database  # noqa: E402
import netprobe_extensions as npx  # noqa: E402


class FakeCloudflare(ThreadingHTTPServer):
    """GET /__down?bytes=N sends N bytes, POST /__up swallows the body; both answer with the data centre in a header."""
    daemon_threads = True

    def __init__(self, down_status=200, up_status=200, colo="OTP"):
        super().__init__(("127.0.0.1", 0), self.Handler)
        self.down_status, self.up_status, self.colo = down_status, up_status, colo
        self.hits = []
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def url(self):
        return "http://127.0.0.1:%d/__down" % self.server_address[1]

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def reply(self, status, size=0):
            """Answers with `size` bytes sent in blocks, so a client that stops reading does not leave a huge buffer."""
            self.send_response(status)
            self.send_header("Content-Length", str(size))
            if self.server.colo:
                self.send_header("colo", self.server.colo)
            self.end_headers()
            block = b"x" * 65536
            try:
                while size > 0:
                    self.wfile.write(block[:size])
                    size -= len(block)
            except OSError:  # the client stopped reading at the end of its measuring window
                self.close_connection = True

        def do_GET(self):
            self.server.hits.append(("GET", self.path))
            if not self.path.startswith("/__down?bytes="):
                return self.reply(404)
            size = int(self.path.split("=", 1)[1])
            self.reply(self.server.down_status, size if self.server.down_status < 400 else 0)

        def do_POST(self):
            self.server.hits.append(("POST", self.path))
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.reply(self.server.up_status if self.path == "/__up" else 404)


class ClientTests(unittest.TestCase):
    def serve(self, **options):
        server = FakeCloudflare(**options)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def run_test(self, server):
        # a huge answer per request keeps one connection per worker busy (loopback would otherwise open thousands)
        with patch.object(npx, "FAST_CHUNK", 1_000_000_000):
            return npx.run_cloudflare_test(server.url, down_seconds=1.2, up_seconds=1.2, warmup=0.3)

    def test_a_full_measurement_has_every_value_the_other_services_have(self):
        server = self.serve()
        result = self.run_test(server)
        self.assertGreater(result["download_mbps"], 0)
        self.assertGreater(result["upload_mbps"], 0)
        self.assertGreater(result["ping_ms"], 0)
        self.assertIsNotNone(result["jitter_ms"])
        self.assertEqual((result["server"]["name"], result["server"]["host"], result["server"]["country"]), ("OTP", "127.0.0.1", None))
        self.assertEqual((result["protocol"], result["packet_loss_pct"], result["isp"]), ("https", None, None))
        kinds = {(method, path.split("?")[0]) for method, path in server.hits}
        self.assertEqual(kinds, {("GET", "/__down"), ("POST", "/__up")})
        self.assertIn(("GET", "/__down?bytes=0"), server.hits, "latency is the shortest possible request")
        self.assertTrue(any(path.startswith("/__down?bytes=1000000000") for _m, path in server.hits), "downloads ask for a large body")

    def test_the_data_centre_is_unknown_without_the_header(self):
        result = self.run_test(self.serve(colo=""))
        self.assertEqual(result["server"]["name"], "Cloudflare")
        result = self.run_test(self.serve(colo="not a code!"))
        self.assertEqual(result["server"]["name"], "Cloudflare", "a header that is not an airport code is not shown")

    def test_a_failing_upload_keeps_the_download(self):
        result = self.run_test(self.serve(up_status=500))
        self.assertGreater(result["download_mbps"], 0)
        self.assertIsNone(result["upload_mbps"], "an unmeasured upload is unknown, not zero")

    def test_a_refused_download_is_an_error_that_names_cloudflare(self):
        with self.assertRaises(npx.FastTestError) as caught:
            self.run_test(self.serve(down_status=403))
        self.assertEqual(str(caught.exception), "Cloudflare server answered HTTP 403")

    def test_an_unreachable_service_is_an_error_that_names_cloudflare(self):
        server = FakeCloudflare()
        url = server.url
        server.shutdown()
        server.server_close()
        with self.assertRaises(npx.FastTestError) as caught:
            npx.run_cloudflare_test(url, down_seconds=1, up_seconds=1, warmup=0.2)
        self.assertTrue(str(caught.exception).startswith("Cloudflare is not reachable"), str(caught.exception))

    def test_fast_com_messages_still_say_fast_com(self):
        self.assertEqual(npx._label("https://ipv4-c001-xyz.1.oca.nflxvideo.net/speedtest?c=ro"), "fast.com")
        self.assertEqual(npx._label("https://speed.cloudflare.com/__up"), "Cloudflare")
        self.assertEqual(npx._fast_path("https://speed.cloudflare.com/__down", 1000), "/__down?bytes=1000")
        self.assertEqual(npx._fast_path("https://speed.cloudflare.com/__down", upload=True), "/__up")


class WiringTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = Path(tmp.name) / "netprobe.sqlite"
        make_database(db)
        env = {"DB_PATH": str(db), "SPEEDTEST_ENABLED": "false", "SITES": "google.com", "DNS_NAMESERVER_1_IP": "8.8.8.8",
               "DNS_NAMESERVER_2_IP": "", "DNS_NAMESERVER_3_IP": "", "DNS_NAMESERVER_4_IP": ""}
        env_patch = patch.dict(os.environ, env)
        env_patch.start()
        self.addCleanup(env_patch.stop)
        spec = importlib.util.spec_from_file_location("tested_cloudflare_app", ROOT / "src/app.py")
        self.module = importlib.util.module_from_spec(spec)
        with patch("threading.Thread.start"):
            spec.loader.exec_module(self.module)
        self.client = self.module.app.test_client()
        self.result = {"ping_ms": 9.5, "download_mbps": 640.25, "upload_mbps": 310.5,
                       "server": {"id": None, "name": "OTP", "host": "speed.cloudflare.com", "country": None},
                       "jitter_ms": 2.0, "packet_loss_pct": None, "isp": None, "result_url": None,
                       "loaded_ping_ms": 40.0, "loaded_up_ping_ms": 55.0, "protocol": "https", "secure": True}

    def test_it_is_offered_next_to_fast_com_and_nothing_else(self):
        config = self.client.get("/api/config").get_json()
        self.assertEqual(config["speedtest_backends_available"], {"fast": True, "cloudflare": True})
        self.assertFalse([key for key in config if "ookla" in key or key in ("speedtest_server", "speedtest_csv", "speedtest_exclude")])

    def test_a_manual_run_stores_the_result_under_the_name_cloudflare(self):
        with patch.object(self.module.custom_extension, "run_cloudflare_speedtest", return_value=self.result) as run:
            body = self.client.post("/api/speedtest/run", json={"backend": "cloudflare"}).get_json()
        run.assert_called_once()
        self.assertTrue(body["success"], body)
        self.assertEqual((body["result"]["backend"], body["result"]["download_mbps"], body["result"]["upload_mbps"],
                          body["result"]["loaded_ping_ms"]), ("cloudflare", 640.25, 310.5, 40.0))
        self.assertEqual(self.client.get("/api/speedtest/latest").get_json()["result"]["backend"], "cloudflare")
        history = self.client.get("/api/speedtest/history?limit=1").get_json()["tests"][-1]
        self.assertEqual((history["backend"], history["server_name"], history["download_mbps"]), ("cloudflare", "OTP", 640.25))
        series = self.client.get("/api/speedtest/series?range=all").get_json()["tests"][-1]
        self.assertEqual((series["backend"], series["jitter_ms"], series["loaded_down_ms"], series["loaded_up_ms"]),
                         ("cloudflare", 2.0, 40.0, 55.0), "jitter and the delay under load are stored next to the test")

    def test_the_chosen_service_runs_for_scheduled_tests_too(self):
        self.client.post("/api/prefs", json={"speedtest_service": "cloudflare"}, headers={"X-NetProbe-Request": "settings"})
        with patch.object(self.module.custom_extension, "run_cloudflare_speedtest", return_value=self.result) as cloudflare, \
             patch.object(self.module.custom_extension, "run_fast_speedtest") as fast:
            self.assertEqual(self.module.run_speedtest_internal()["backend"], "cloudflare")
        cloudflare.assert_called_once()
        fast.assert_not_called()

    def test_removed_services_in_old_settings_mean_fast_com(self):
        for name in ("ookla", "python", "speedtest-cli", "official"):
            self.assertEqual(self.module.normalize_speedtest_backend(name), "fast", name)
        self.assertEqual(self.module.normalize_speedtest_backend("Cloudflare"), "cloudflare")
        response = self.client.post("/api/prefs", json={"speedtest_service": "ookla"}, headers={"X-NetProbe-Request": "settings"})
        self.assertEqual(response.status_code, 400, "a removed service cannot be chosen any more")

    def test_notifications_name_the_service(self):
        self.assertEqual(npx.SPEED_NAMES["cloudflare"], "Cloudflare")
        self.assertEqual(npx.SPEED_NAMES["python"], "Speedtest.net", "old Speedtest.net results keep their name")
        self.assertNotIn("ookla", npx.SPEED_NAMES)


if __name__ == "__main__":
    unittest.main()
