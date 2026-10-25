"""Public IP display: opt-in, cached, tolerant of failing lookup services, never requested while switched off."""
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
from fixture_db import make_database  # noqa: E402
import netprobe_extensions as npx  # noqa: E402

HEADERS = {"X-NetProbe-Request": "settings"}
S1, S2, S3 = [u.split("//")[1].split("/")[0] for u in npx.PUBLIC_IP_SOURCES]


class PublicIpTests(unittest.TestCase):
    def setUp(self):
        from flask import Flask
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        make_database(root / "netprobe.sqlite")
        self.now = 2_000_000_000
        self.ext = npx.Extension(str(root / "netprobe.sqlite"), str(root), clock=lambda: self.now)
        self.answers = {S1: "8.8.8.8", S2: "8.8.4.4", S3: "1.1.1.1"}
        self.calls = []

        def fetch(url, timeout=None):
            host = url.split("//")[1].split("/")[0]
            self.calls.append(host)
            answer = self.answers[host]
            if isinstance(answer, Exception):
                raise answer
            return answer
        self.ext.ip_fetch = fetch
        app = Flask(__name__)
        npx.install_routes(app, self.ext)
        self.client = app.test_client()

    def turn(self, on):
        response = self.client.post("/api/prefs", json={"show_public_ip": on}, headers=HEADERS)
        self.assertEqual(response.status_code, 200)

    def get(self):
        return self.client.get("/api/public-ip").get_json()

    def test_off_by_default_and_nothing_is_requested(self):
        self.assertEqual(self.get(), {"enabled": False})
        self.assertEqual(self.calls, [], "no outbound request while the option is off")
        self.assertIs(self.ext.prefs()["show_public_ip"], False)

    def test_option_must_be_a_real_boolean(self):
        for bad in ("yes", 1, None, "true"):
            self.assertEqual(self.client.post("/api/prefs", json={"show_public_ip": bad}, headers=HEADERS).status_code, 400)
        self.assertEqual(self.client.post("/api/prefs", json={"show_public_ip": True}).status_code, 403, "needs the same-origin header")
        self.assertEqual(self.calls, [])

    def test_enabled_returns_the_address_and_where_it_came_from(self):
        self.turn(True)
        self.assertEqual(self.get(), {"enabled": True, "ip": "8.8.8.8", "source": S1, "checked_at": self.now, "error": None})
        self.assertEqual(self.calls, [S1])

    def test_answers_are_cached_and_refreshed_after_the_ttl(self):
        self.turn(True)
        for _ in range(5):
            self.assertEqual(self.get()["ip"], "8.8.8.8")
        self.assertEqual(self.calls, [S1], "five page refreshes, one lookup")
        self.answers[S1] = "9.9.9.9"
        self.now += npx.PUBLIC_IP_TTL - 1
        self.assertEqual(self.get()["ip"], "8.8.8.8")
        self.now += 2
        self.assertEqual(self.get()["ip"], "9.9.9.9", "a changed address shows up after the cache expires")
        self.assertEqual(self.calls, [S1, S1])

    def test_a_failing_service_falls_back_to_the_next_one(self):
        self.answers[S1] = OSError("timed out")
        self.answers[S2] = ValueError("not an IP")
        self.turn(True)
        result = self.get()
        self.assertEqual((result["ip"], result["source"], result["error"]), ("1.1.1.1", S3, None))
        self.assertEqual(self.calls, [S1, S2, S3])

    def test_all_services_failing_is_reported_and_retried_only_after_a_minute(self):
        for host in self.answers:
            self.answers[host] = OSError("network is unreachable")
        self.turn(True)
        result = self.get()
        self.assertEqual((result["enabled"], result["ip"], result["source"]), (True, None, None))
        for host in (S1, S2, S3):
            self.assertIn(host + ": network is unreachable", result["error"])
        self.assertEqual(len(self.calls), 3)
        self.get()
        self.assertEqual(len(self.calls), 3, "no hammering while the network is down")
        self.now += npx.PUBLIC_IP_RETRY
        self.answers[S1] = "8.8.8.8"
        self.assertEqual(self.get()["ip"], "8.8.8.8", "recovers by itself")

    def test_switching_off_stops_requests_and_forgets_the_address(self):
        self.turn(True)
        self.get()
        self.turn(False)
        self.assertEqual(self.get(), {"enabled": False})
        self.assertIsNone(self.ext.ip_cache)
        self.turn(True)
        self.answers[S1] = "9.9.9.9"
        self.assertEqual(self.get()["ip"], "9.9.9.9", "no stale address after switching it on again")

    def test_simultaneous_requests_cause_one_lookup(self):
        self.turn(True)
        started = threading.Event()
        original = self.ext.ip_fetch

        def slow(url, timeout=None):
            started.set()
            time.sleep(0.2)
            return original(url)
        self.ext.ip_fetch = slow
        results = []
        threads = [threading.Thread(target=lambda: results.append(self.ext.public_ip()["ip"])) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(results, ["8.8.8.8"] * 4)
        self.assertEqual(self.calls, [S1])

    def test_damaged_preferences_file_means_off(self):
        self.ext.prefs_path.write_text("{broken")
        self.assertEqual(self.get(), {"enabled": False})
        self.assertEqual(self.calls, [])


class FetchTests(unittest.TestCase):
    """The real fetch function against a local server: only a public IP address is accepted."""

    def setUp(self):
        outer = self
        outer.body = b"8.8.8.8\n"

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", str(len(outer.body)))
                self.end_headers()
                try:
                    self.wfile.write(outer.body)
                except ConnectionError:  # the client stops reading an oversized answer
                    pass
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = "http://127.0.0.1:%d/ip" % self.server.server_address[1]

    def test_public_addresses_are_accepted(self):
        self.assertEqual(npx.fetch_public_ip(self.url), "8.8.8.8")
        self.body = b" 2606:4700:4700::1111 \r\n"
        self.assertEqual(npx.fetch_public_ip(self.url), "2606:4700:4700::1111")

    def test_anything_else_is_refused(self):
        for body in (b"<html>captive portal</html>", b"", b"192.168.1.20", b"10.0.0.7", b"127.0.0.1", b"169.254.1.1",
                     b"8.8.8.8 and more", b"999.1.1.1"):
            with self.subTest(body=body):
                self.body = body
                with self.assertRaises(ValueError):
                    npx.fetch_public_ip(self.url)

    def test_a_huge_answer_is_not_read_into_memory(self):
        self.body = b"8" * 5_000_000
        with self.assertRaises(ValueError):
            npx.fetch_public_ip(self.url)


if __name__ == "__main__":
    unittest.main()
