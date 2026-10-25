"""Public IP display: opt-in, cached, tolerant of failing lookup services, never requested while switched off."""
import json
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
S1, S2, S3 = [u.split("//")[1].split("/")[0] for u in npx.PUBLIC_IP_SOURCES]  # ipwho.is (address + ISP), then plain text


class PublicIpTests(unittest.TestCase):
    def setUp(self):
        from flask import Flask
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        make_database(root / "netprobe.sqlite")
        self.now = 2_000_000_000
        self.ext = npx.Extension(str(root / "netprobe.sqlite"), str(root), clock=lambda: self.now)
        self.answers = {S1: ("8.8.8.8", "Example Net"), S2: ("8.8.4.4", None), S3: ("1.1.1.1", None)}
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

    def interval(self, minutes):
        response = self.client.post("/api/prefs", json={"wan_check_min": minutes}, headers=HEADERS)
        self.assertEqual(response.status_code, 200)

    def get(self):
        """The answer after any background lookup has finished (the first answer after enabling is `pending`, an
        expired one is served while the refresh runs; both are covered by their own tests)."""
        self.client.get("/api/public-ip")
        if self.ext.ip_thread:
            self.ext.ip_thread.join(5)
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

    def test_the_first_answer_is_pending_and_never_waits_for_the_lookup(self):
        self.turn(True)
        gate = threading.Event()
        original = self.ext.ip_fetch
        self.ext.ip_fetch = lambda url, timeout=None: (gate.wait(5), original(url))[1]
        started = time.monotonic()
        first = self.client.get("/api/public-ip").get_json()
        self.assertLess(time.monotonic() - started, 1, "the request returns while the lookup is still running")
        self.assertEqual(first, {"enabled": True, "pending": True, "refreshing": True, "every_min": 15, "ip": None, "isp": None, "source": None, "checked_at": None, "error": None})
        self.assertEqual(self.client.get("/api/public-ip").get_json()["pending"], True)
        gate.set()
        self.ext.ip_thread.join(5)
        self.assertEqual(self.client.get("/api/public-ip").get_json()["ip"], "8.8.8.8")
        self.assertEqual(self.calls, [S1], "polling while pending did not start a second lookup")

    def test_an_expired_answer_is_served_while_a_fresh_one_is_fetched(self):
        self.turn(True)
        self.interval(1)
        self.assertEqual(self.get()["ip"], "8.8.8.8")
        self.answers[S1] = ("9.9.9.9", "Other Net")
        self.now += 60 + npx.WAN_GRACE + 1
        stale = self.client.get("/api/public-ip").get_json()
        self.assertEqual(stale["ip"], "8.8.8.8", "no empty card while refreshing")
        self.ext.ip_thread.join(5)
        self.assertEqual(self.client.get("/api/public-ip").get_json()["ip"], "9.9.9.9")

    def test_switching_off_while_a_lookup_runs_keeps_nothing(self):
        self.turn(True)
        gate = threading.Event()
        original = self.ext.ip_fetch
        self.ext.ip_fetch = lambda url, timeout=None: (gate.wait(5), original(url))[1]
        self.client.get("/api/public-ip")
        self.turn(False)
        gate.set()
        self.ext.ip_thread.join(5)
        self.assertIsNone(self.ext.ip_cache)
        self.assertEqual(self.get(), {"enabled": False})

    def test_enabled_returns_the_address_and_where_it_came_from(self):
        self.turn(True)
        self.assertEqual(self.get(), {"enabled": True, "refreshing": False, "every_min": 15, "ip": "8.8.8.8", "isp": "Example Net", "source": S1,
                                      "checked_at": self.now, "error": None})
        self.assertEqual(self.calls, [S1])

    def test_answers_are_cached_and_refreshed_after_the_ttl(self):
        self.turn(True)
        self.interval(1)
        for _ in range(5):
            self.assertEqual(self.get()["ip"], "8.8.8.8")
        self.assertEqual(self.calls, [S1], "five page refreshes, one lookup")
        self.answers[S1] = ("9.9.9.9", "Other Net")
        self.now += 60 + npx.WAN_GRACE - 1
        self.assertEqual(self.get()["ip"], "8.8.8.8")
        self.now += 2
        changed = self.get()
        self.assertEqual((changed["ip"], changed["isp"]), ("9.9.9.9", "Other Net"), "a changed address shows up after the cache expires")
        self.assertEqual(self.calls, [S1, S1])

    def test_a_failing_service_falls_back_to_the_next_one(self):
        self.answers[S1] = OSError("timed out")
        self.answers[S2] = ValueError("not an IP")
        self.turn(True)
        result = self.get()
        self.assertEqual((result["ip"], result["isp"], result["source"], result["error"]), ("1.1.1.1", None, S3, None),
                         "plain-text services know the address only; the provider stays unknown, not invented")
        self.assertEqual(self.calls, [S1, S2, S3])

    def test_all_services_failing_is_reported_and_retried_only_after_a_minute(self):
        for host in self.answers:
            self.answers[host] = OSError("network is unreachable")
        self.turn(True)
        result = self.get()
        self.assertEqual((result["enabled"], result["ip"], result["isp"], result["source"]), (True, None, None, None))
        for host in (S1, S2, S3):
            self.assertIn(host + ": network is unreachable", result["error"])
        self.assertEqual(len(self.calls), 3)
        self.get()
        self.assertEqual(len(self.calls), 3, "no hammering while the network is down")
        self.now += npx.PUBLIC_IP_RETRY
        self.answers[S1] = ("8.8.8.8", "Example Net")
        self.assertEqual(self.get()["ip"], "8.8.8.8", "recovers by itself")

    def test_switching_off_stops_requests_and_forgets_the_address(self):
        self.turn(True)
        self.get()
        self.turn(False)
        self.assertEqual(self.get(), {"enabled": False})
        self.assertIsNone(self.ext.ip_cache)
        self.turn(True)
        self.answers[S1] = ("9.9.9.9", "Other Net")
        self.assertEqual(self.get()["ip"], "9.9.9.9", "no stale address after switching it on again")

    def test_simultaneous_requests_cause_one_lookup(self):
        self.turn(True)
        original = self.ext.ip_fetch

        def slow(url, timeout=None):
            time.sleep(0.2)
            return original(url)
        self.ext.ip_fetch = slow
        answers = []
        threads = [threading.Thread(target=lambda: answers.append(self.ext.public_ip())) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.ext.ip_thread.join(5)
        self.assertEqual([a.get("pending") for a in answers], [True] * 4)
        self.assertEqual(self.ext.public_ip()["ip"], "8.8.8.8")
        self.assertEqual(self.calls, [S1])

    def test_damaged_preferences_file_means_off(self):
        self.ext.prefs_path.write_text("{broken")
        self.assertEqual(self.get(), {"enabled": False})
        self.assertEqual(self.calls, [])


    def test_a_page_view_does_not_look_up_within_the_chosen_interval(self):
        self.turn(True)
        self.assertEqual(self.get()["every_min"], 15)
        self.now += 15 * 60 - 1
        self.get()
        self.assertEqual(self.calls, [S1], "the watcher keeps the 15-minute card fresh; opening the page costs no request")
        self.now += 15 * 60 + npx.WAN_GRACE
        self.get()
        self.assertEqual(self.calls, [S1, S1], "a watcher that is long late is replaced by the page view")

    def test_manual_mode_never_looks_up_by_itself(self):
        self.turn(True)
        self.interval(0)
        empty = self.get()
        self.assertEqual((empty["enabled"], empty["every_min"], empty["ip"], empty["checked_at"], empty["pending"], empty["refreshing"]),
                         (True, 0, None, None, False, False))
        self.now += 7 * 86400
        self.get()
        self.assertEqual(self.calls, [], "page views, however many, send nothing in manual mode")

    def test_the_refresh_button_looks_up_once_and_the_answer_stays_for_good(self):
        self.turn(True)
        self.interval(0)
        started = self.client.post("/api/public-ip/refresh", headers=HEADERS)
        self.assertEqual((started.status_code, started.get_json()["enabled"]), (202, True))
        self.ext.ip_thread.join(5)
        answer = self.client.get("/api/public-ip").get_json()
        self.assertEqual((answer["ip"], answer["isp"], answer["every_min"], answer["refreshing"]), ("8.8.8.8", "Example Net", 0, False))
        self.now += 30 * 86400
        self.assertEqual(self.client.get("/api/public-ip").get_json()["checked_at"], answer["checked_at"], "an old answer is shown, not replaced")
        self.assertEqual(self.calls, [S1])

    def test_repeated_presses_while_a_lookup_runs_start_no_second_one(self):
        self.turn(True)
        self.interval(0)
        gate = threading.Event()
        original = self.ext.ip_fetch
        self.ext.ip_fetch = lambda url, timeout=None: (gate.wait(5), original(url))[1]
        for _ in range(3):
            body = self.client.post("/api/public-ip/refresh", headers=HEADERS).get_json()
        self.assertTrue(body["refreshing"])
        gate.set()
        self.ext.ip_thread.join(5)
        self.assertEqual(self.calls, [S1])

    def test_refresh_needs_the_option_and_the_same_origin_header(self):
        self.assertEqual(self.client.post("/api/public-ip/refresh", headers=HEADERS).get_json(), {"enabled": False})
        self.assertEqual(self.calls, [])
        self.turn(True)
        self.assertEqual(self.client.post("/api/public-ip/refresh").status_code, 403)
        self.assertEqual(self.calls, [])

    def test_the_interval_is_one_of_the_three_choices(self):
        for value in (0, 1, 15):
            self.interval(value)
            self.assertEqual(self.ext.prefs()["wan_check_min"], value)
        for bad in (5, -1, 60, "15", True, None, 1.0):
            response = self.client.post("/api/prefs", json={"wan_check_min": bad}, headers=HEADERS)
            self.assertEqual(response.status_code, 400, repr(bad))
        self.assertEqual(self.ext.prefs()["wan_check_min"], 15)


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

    def json_body(self, **fields):
        payload = {"ip": "8.8.8.8", "success": True, "connection": {"asn": 15169, "org": "Org Name", "isp": "Example Net"}}
        payload.update(fields)
        self.body = json.dumps(payload).encode()

    def test_plain_text_addresses_are_accepted_without_a_provider(self):
        self.assertEqual(npx.fetch_public_ip(self.url), ("8.8.8.8", None))
        self.body = b" 2606:4700:4700::1111 \r\n"
        self.assertEqual(npx.fetch_public_ip(self.url), ("2606:4700:4700::1111", None))

    def test_json_answer_gives_address_and_provider(self):
        self.json_body()
        self.assertEqual(npx.fetch_public_ip(self.url), ("8.8.8.8", "Example Net"))

    def test_provider_falls_back_to_the_organisation_and_is_cleaned(self):
        self.json_body(connection={"org": "  Org \n Name\t<b>x</b> \\ "})
        ip, isp = npx.fetch_public_ip(self.url)
        self.assertEqual(isp, "Org Name <b>x</b>", "control characters and backslashes are dropped, spaces collapsed (the UI escapes markup)")
        self.json_body(connection={"isp": "N" * 500})
        self.assertEqual(len(npx.fetch_public_ip(self.url)[1]), 120)

    def test_missing_or_odd_provider_is_unknown_not_invented(self):
        for connection in (None, "text", [], {}, {"isp": 5}, {"isp": ""}, {"isp": "   "}):
            with self.subTest(connection=connection):
                self.json_body(connection=connection)
                self.assertEqual(npx.fetch_public_ip(self.url), ("8.8.8.8", None))

    def test_failed_or_malformed_json_is_refused(self):
        for body in (b'{"success": false, "message": "limit"}', b'{"ip": "8.8.8.8"}', b'{"success": true}', b'{"success": true, "ip": 5}',
                     b'{broken', b'[1]', b'{"success": true, "ip": "192.168.1.5"}', b'{"success": true, "ip": "10.1.1.1"}'):
            with self.subTest(body=body):
                self.body = body
                with self.assertRaises(ValueError):
                    npx.fetch_public_ip(self.url)

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
