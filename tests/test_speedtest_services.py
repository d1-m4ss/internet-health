"""Speedtest services: the fast.com client against a local fake fast.com, the service preference and its wiring in app.py."""
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
from fixture_db import make_database  # noqa: E402
import netprobe_extensions as npx  # noqa: E402


class FakeFast(ThreadingHTTPServer):
    """Serves what fast.com serves: the page, its script with the API token, the API and the measurement endpoints."""
    daemon_threads = True

    def __init__(self, **options):
        super().__init__(("127.0.0.1", 0), self.Handler)
        self.options = dict(page_script=True, token=True, targets=2, upload_status=200, download_status=200, **options)
        self.hits = []
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def base(self):
        return "http://127.0.0.1:%d" % self.server_address[1]

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def reply(self, status, body=b"", ctype="text/plain"):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            opts = self.server.options
            self.server.hits.append(("GET", self.path))
            if self.path == "/":
                self.reply(200, (b'<html><script src="/app-0bffe1.js"></script></html>' if opts["page_script"] else b"<html></html>"), "text/html")
            elif self.path == "/app-0bffe1.js":
                self.reply(200, b'x={https:!0,token:"FAKETOKEN12345",urlCount:5}' if opts["token"] else b"x={}")
            elif self.path.startswith("/netflix/speedtest/v2"):
                assert "token=FAKETOKEN12345" in self.path
                targets = [{"name": "t%d" % i, "url": "%s/speedtest?c=ro&t=k%d" % (self.server.base, i),
                            "location": {"city": "Brasov", "country": "RO"}} for i in range(opts["targets"])]
                self.reply(200, json.dumps({"client": {"ip": "203.0.113.9", "isp": "ExampleNet"}, "targets": targets}).encode(), "application/json")
            elif self.path.startswith("/speedtest/range/0-"):
                size = int(self.path.split("/range/0-")[1].split("?")[0])
                if opts["download_status"] != 200:
                    return self.reply(opts["download_status"])
                size = min(size + 1, 2_000_000)  # the range is inclusive; the fake caps it to keep the tests light
                self.send_response(200)
                self.send_header("Content-Length", str(size))
                self.end_headers()
                block = b"\0" * 65536
                sent = 0
                while sent < size:
                    piece = block[:min(len(block), size - sent)]
                    self.wfile.write(piece)
                    sent += len(piece)
                    time.sleep(0.0005)
            else:
                self.reply(404)

        def do_POST(self):
            self.server.hits.append(("POST", self.path))
            remaining = int(self.headers.get("Content-Length", 0))
            while remaining > 0:
                remaining -= len(self.rfile.read(min(65536, remaining)))
            self.reply(self.server.options["upload_status"])


class FastClientTests(unittest.TestCase):
    def setUp(self):
        self.server = FakeFast()
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)
        self.page = self.server.base + "/"
        self.api = self.server.base + "/netflix/speedtest/v2"

    def run_test(self, **kwargs):
        return npx.run_fast_test(self.page, self.api, down_seconds=1.2, up_seconds=1.2, warmup=0.3, **kwargs)

    def test_full_measurement_has_real_numbers_and_the_shape_the_app_stores(self):
        result = self.run_test()
        self.assertGreater(result["download_mbps"], 1)
        self.assertGreater(result["upload_mbps"], 1)
        self.assertGreaterEqual(result["ping_ms"], 0)
        self.assertEqual(result["server"], {"id": None, "name": "Netflix, Brasov", "host": "127.0.0.1", "country": "RO"})
        self.assertEqual((result["isp"], result["protocol"], result["secure"]), ("ExampleNet", "https", True))
        self.assertNotIn("203.0.113.9", json.dumps(result), "the client address is not part of a stored result")
        methods = {m for m, _ in self.server.hits}
        self.assertEqual(methods, {"GET", "POST"})
        self.assertTrue(any(p.startswith("/speedtest/range/0-0?") for _, p in self.server.hits), "latency uses a one-byte range")
        self.assertTrue(any(p.startswith("/speedtest/range/0-%d?" % npx.FAST_CHUNK) for _, p in self.server.hits), "download asks for a large range")

    def test_download_is_spread_over_the_returned_servers(self):
        self.run_test()
        used = {p.split("t=")[1] for m, p in self.server.hits if m == "GET" and p.startswith("/speedtest/range/")}
        self.assertEqual(used, {"k0", "k1"})

    def test_upload_failure_keeps_the_real_download_result(self):
        self.server.options["upload_status"] = 500
        result = self.run_test()
        self.assertGreater(result["download_mbps"], 1)
        self.assertIsNone(result["upload_mbps"], "an unmeasured upload is unknown, not zero")

    def test_failures_have_readable_messages(self):
        for options, text in ((dict(page_script=False), "no application script"), (dict(token=False), "no API token"),
                              (dict(targets=0), "no test servers")):
            with self.subTest(options):
                self.server.options.update(page_script=True, token=True, targets=2)
                self.server.options.update(options)
                with self.assertRaisesRegex(npx.FastTestError, text):
                    self.run_test()
        self.server.options.update(page_script=True, token=True, targets=2, download_status=503)
        with self.assertRaisesRegex(npx.FastTestError, "HTTP 503"):
            self.run_test()

    def test_unreachable_fast_com_is_a_clean_error(self):
        self.server.shutdown()
        self.server.server_close()
        with self.assertRaisesRegex(npx.FastTestError, "not reachable"):
            npx.fast_targets(self.page, self.api)

    def test_targets_without_http_urls_are_ignored(self):
        payload = {"client": {}, "targets": [{"url": "file:///etc/passwd"}, {"url": "ftp://x/y"}, {"url": 5}, "junk"]}
        with patch.object(npx, "_fast_get", side_effect=[
                '<script src="/app-1.js"></script>', 'token:"ABCDEFGH1234"', json.dumps(payload)]):
            with self.assertRaisesRegex(npx.FastTestError, "no test servers"):
                npx.fast_targets("https://fast.example/", "https://api.example/v2")

    def test_download_url_rewrite_matches_what_fast_com_does(self):
        url = "https://oca.example/speedtest?c=ro&t=abc"
        self.assertEqual(npx._fast_path(url, 0), "/speedtest/range/0-0?c=ro&t=abc")
        self.assertEqual(npx._fast_path(url, 100), "/speedtest/range/0-100?c=ro&t=abc")
        self.assertEqual(npx._fast_path(url), "/speedtest?c=ro&t=abc")
        self.assertEqual(npx._fast_path("https://oca.example/speedtest/range/?t=1", 7), "/speedtest/range/0-7?t=1")


class PrefsTests(unittest.TestCase):
    def setUp(self):
        from flask import Flask
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        make_database(self.root / "netprobe.sqlite")
        self.ext = npx.Extension(str(self.root / "netprobe.sqlite"), str(self.root))
        app = Flask(__name__)
        npx.install_routes(app, self.ext)
        self.client = app.test_client()
        self.headers = {"X-NetProbe-Request": "settings"}

    def test_default_follows_the_container_setting(self):
        self.assertEqual(self.client.get("/api/prefs").get_json(), {"prefs": {"speedtest_service": ""}})
        self.assertEqual(self.ext.speedtest_service(), "")

    def test_each_service_is_saved_and_survives_a_restart(self):
        for service in ("python", "ookla", "fast", ""):
            response = self.client.post("/api/prefs", json={"speedtest_service": service}, headers=self.headers)
            self.assertEqual(response.get_json(), {"prefs": {"speedtest_service": service}})
            fresh = npx.Extension(str(self.root / "netprobe.sqlite"), str(self.root))
            self.assertEqual(fresh.speedtest_service(), service)

    def test_invalid_values_are_rejected_and_nothing_is_written(self):
        for body in ({"speedtest_service": "fastcom"}, {"speedtest_service": None}, {"speedtest_service": 1},
                     {"unknown": True}, [], "fast"):
            with self.subTest(body=body):
                self.assertEqual(self.client.post("/api/prefs", json=body, headers=self.headers).status_code, 400)
        self.assertFalse(self.ext.prefs_path.exists())

    def test_changes_need_a_same_origin_settings_request(self):
        self.assertEqual(self.client.post("/api/prefs", json={"speedtest_service": "fast"}).status_code, 403)
        self.assertEqual(self.client.post("/api/prefs", json={"speedtest_service": "fast"},
                                          headers={**self.headers, "Origin": "http://evil.example"}).status_code, 403)
        self.assertEqual(self.ext.speedtest_service(), "")

    def test_damaged_file_is_reported_by_the_api_but_never_stops_background_work_and_can_be_repaired(self):
        self.ext.prefs_path.write_text("{broken")
        self.assertEqual(self.client.get("/api/prefs").status_code, 503)
        self.assertEqual(self.ext.speedtest_service(), "")
        self.assertEqual(self.client.post("/api/prefs", json={"speedtest_service": "fast"}, headers=self.headers).status_code, 200)
        self.assertEqual(self.ext.speedtest_service(), "fast")

    def test_threshold_settings_file_is_untouched_by_preferences(self):
        before = self.ext.settings_path.read_text()
        self.client.post("/api/prefs", json={"speedtest_service": "fast"}, headers=self.headers)
        self.assertEqual(self.ext.settings_path.read_text(), before)


class AppWiringTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "netprobe.sqlite"
        make_database(self.db)
        env = {"DB_PATH": str(self.db), "DB_ENGINE": "sqlite", "USE_POSTGRES": "false", "SPEEDTEST_ENABLED": "false",
               "ROUTER_IP": "192.168.1.1", "SITES": "google.com", "DNS_NAMESERVER_1_IP": "8.8.8.8",
               "DNS_NAMESERVER_2_IP": "", "DNS_NAMESERVER_3_IP": "", "DNS_NAMESERVER_4_IP": ""}
        env_patch = patch.dict(os.environ, env)
        env_patch.start()
        self.addCleanup(env_patch.stop)
        spec = importlib.util.spec_from_file_location("tested_speed_app", ROOT / "src/app.py")
        self.module = importlib.util.module_from_spec(spec)
        with patch("threading.Thread.start"):
            spec.loader.exec_module(self.module)
        self.client = self.module.app.test_client()
        self.fast_result = {"ping_ms": 11.5, "download_mbps": 321.5, "upload_mbps": None,
                            "server": {"id": None, "name": "Netflix, Brasov", "host": "oca.example", "country": "RO"},
                            "jitter_ms": 1.0, "packet_loss_pct": None, "isp": "ExampleNet", "result_url": None,
                            "protocol": "https", "secure": True}

    def choose(self, service):
        return self.client.post("/api/prefs", json={"speedtest_service": service}, headers={"X-NetProbe-Request": "settings"})

    def test_fast_is_offered_as_an_available_service(self):
        self.assertIs(self.client.get("/api/config").get_json()["speedtest_backends_available"]["fast"], True)

    def test_container_variable_accepts_fast_and_rejects_nonsense(self):
        self.assertEqual(self.module.normalize_speedtest_backend("Fast.com"), "fast")
        self.assertEqual(self.module.normalize_speedtest_backend("fast"), "fast")
        with self.assertRaises(ValueError):
            self.module.normalize_speedtest_backend("netflix")

    def test_chosen_service_decides_when_the_request_names_none(self):
        self.assertEqual(self.module.resolve_speedtest_backend(), "python")
        self.choose("fast")
        self.assertEqual(self.module.resolve_speedtest_backend(), "fast")
        self.assertEqual(self.module.resolve_speedtest_backend("ookla"), "ookla", "an explicit request wins")
        self.choose("")
        self.assertEqual(self.module.resolve_speedtest_backend(), "python")

    def test_manual_run_stores_a_fast_result_with_an_unknown_upload(self):
        with patch.object(self.module.custom_extension, "run_fast_speedtest", return_value=self.fast_result):
            body = self.client.post("/api/speedtest/run", json={"backend": "fast"}).get_json()
        self.assertTrue(body["success"], body)
        self.assertEqual((body["result"]["backend"], body["result"]["download_mbps"], body["result"]["upload_mbps"]),
                         ("fast", 321.5, None))
        latest = self.client.get("/api/speedtest/latest").get_json()["result"]
        self.assertEqual((latest["backend"], latest["download_mbps"], latest["upload_mbps"], latest["ping_ms"]), ("fast", 321.5, None, 11.5))
        self.assertEqual(latest["server"]["name"], "Netflix, Brasov")
        row = sqlite3.connect(self.db).execute("SELECT backend, upload_mbps FROM speedtests ORDER BY ts DESC LIMIT 1").fetchone()
        self.assertEqual(row, ("fast", None))

    def test_a_run_without_backend_uses_the_chosen_service(self):
        self.choose("fast")
        with patch.object(self.module.custom_extension, "run_fast_speedtest", return_value=self.fast_result) as fast:
            body = self.client.post("/api/speedtest/run", json={}).get_json()
        self.assertTrue(body["success"], body)
        self.assertEqual(body["result"]["backend"], "fast")
        fast.assert_called_once_with()

    def test_fast_failure_reaches_the_ui_as_a_readable_error(self):
        with patch.object(self.module.custom_extension, "run_fast_speedtest",
                          side_effect=npx.FastTestError("fast.com is not reachable: timed out")):
            response = self.client.post("/api/speedtest/run", json={"backend": "fast"})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_json(), {"success": False, "error": "fast.com is not reachable: timed out"})
        self.assertEqual(self.client.get("/api/speedtest/latest").get_json()["result"]["backend"], "python",
                         "a failed run stores nothing")


if __name__ == "__main__":
    unittest.main()
