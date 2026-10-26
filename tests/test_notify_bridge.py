"""Host bridge integration tests: only local fixture HTTP and fake notify."""
import contextlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


SCRIPT = Path(__file__).resolve().parents[1] / "src/netprobe-unraid-notify.sh"
TOKEN = "a" * 64


class NotifyBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.clock = self.root / "clock"
        self.clock.write_text("1000")
        self.log = self.root / "notify.jsonl"
        self.fail_flag = self.root / "fail"
        self.delay = self.root / "delay"
        self.token = self.root / "notify-token"
        self.token.write_text(TOKEN + "\n")
        self.token.chmod(0o600)
        self.settings = self.root / "alert-settings.json"
        self.settings.write_text('{"enabled":true}')
        self.state = self.root / "bridge-state.json"
        self.records = []
        self.monitoring = {}
        self.raw = None
        self.status = 200
        self.ack_status = 200
        self.acks = []
        self.requests = []
        case = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                case.requests.append((self.path, self.headers.get("X-NetProbe-Notify-Key")))
                payload = case.raw if case.raw is not None else json.dumps(case.payload()).encode()
                self.send_response(case.status)
                self.end_headers()
                with contextlib.suppress(BrokenPipeError):
                    self.wfile.write(payload)

            def do_POST(self):
                case.acks.append((self.path, self.headers.get("X-NetProbe-Notify-Key"), self.headers.get("Content-Length")))
                self.send_response(case.ack_status)
                self.end_headers()
                if case.ack_status == 200:
                    case.records = [r for r in case.records if self.path != f'/api/notifications/{r["id"]}/ack']

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.env = dict(os.environ, NETPROBE_API_BASE=f"http://127.0.0.1:{self.server.server_port}",
                        NETPROBE_NOTIFY_TOKEN_FILE=str(self.token), NETPROBE_STATE_FILE=str(self.state),
                        NETPROBE_SETTINGS_FILE=str(self.settings), UNRAID_NOTIFY=str(self.bin / "notify"),
                        FIXTURE_CLOCK=str(self.clock), FIXTURE_LOG=str(self.log), FIXTURE_FAIL=str(self.fail_flag),
                        FIXTURE_DELAY=str(self.delay), NETPROBE_LOCK_FILE=str(self.root / "bridge.lock"), PATH=str(self.bin) + os.pathsep + os.environ["PATH"])
        self.executable("date", 'import os\nprint(open(os.environ["FIXTURE_CLOCK"]).read().strip())\n')
        self.executable("notify", 'import json,os,sys,time\nfrom pathlib import Path\n'
                        'with open(os.environ["FIXTURE_LOG"],"a") as f: f.write(json.dumps(sys.argv[1:])+"\\n")\n'
                        'if Path(os.environ["FIXTURE_DELAY"]).exists(): time.sleep(1)\n'
                        'sys.exit(1 if Path(os.environ["FIXTURE_FAIL"]).exists() else 0)\n')
        # macOS has no flock CLI. This fixture uses the real OS flock on the
        # inherited open descriptor, matching `flock -n 9` lifetime semantics.
        if not shutil.which("flock"):
            self.executable("flock", 'import fcntl,sys\ntry: fcntl.flock(int(sys.argv[-1]),fcntl.LOCK_EX|fcntl.LOCK_NB)\nexcept BlockingIOError: sys.exit(1)\n')

    def executable(self, name, code):
        path = self.bin / name
        path.write_text(f"#!{sys.executable}\n" + code)
        path.chmod(0o700)

    def record(self, **updates):
        record = dict(id=1, ts=999, level="warning", subject="Internet Health: degraded", message="Loss 20%; $(touch /tmp/no-eval)")
        record.update(updates)
        return record

    def payload(self):
        payload = dict(notifications=self.records, latest_probe_ts=int(self.clock.read_text()) - 1,
                       process_started_at=900, max_sample_age=180)
        payload.update(self.monitoring)
        return payload

    def run_bridge(self, now=1000):
        self.clock.write_text(str(now))
        result = subprocess.run(["bash", str(SCRIPT)], env=self.env, capture_output=True, text=True, timeout=15)
        return result

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_delivery_uses_literal_arguments_and_authenticated_empty_ack(self):
        self.records = [self.record()]
        result = self.run_bridge()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.requests, [("/api/notifications/pending", TOKEN)])
        self.assertEqual(self.acks, [("/api/notifications/1/ack", TOKEN, "0")])
        args = self.calls()[0]
        self.assertEqual(args[args.index("-i") + 1], "warning")
        self.assertEqual(args[args.index("-m") + 1], self.record()["message"])
        self.assertEqual(args[args.index("-s") + 1], "Internet Health: degraded")
        self.assertEqual(args[args.index("-e") + 1], "Internet Health")
        self.assertNotIn("-l", args)

    def test_the_data_folder_alone_locates_the_token_the_settings_and_the_state(self):
        folder = self.root / "data-folder"
        folder.mkdir()
        shutil.copy(self.token, folder / "notify-token")
        shutil.copy(self.settings, folder / "alert-settings.json")
        for name in ("NETPROBE_NOTIFY_TOKEN_FILE", "NETPROBE_SETTINGS_FILE", "NETPROBE_STATE_FILE"):
            del self.env[name]
        self.env["NETPROBE_DATA_DIR"] = str(folder)
        self.records = [self.record()]
        result = self.run_bridge()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.requests, [("/api/notifications/pending", TOKEN)])
        self.assertTrue((folder / "notify-bridge-state.json").exists(), "the state is kept in the same folder")

    def test_explicit_web_link_is_literal_and_rejects_non_http_or_controls(self):
        self.env["NETPROBE_WEB_URL"] = "http://192.168.1.4:8788/"
        self.records = [self.record()]
        self.assertEqual(self.run_bridge().returncode, 0)
        args = self.calls()[0]
        self.assertEqual(args[args.index("-l") + 1], "http://192.168.1.4:8788/")
        for invalid in ("javascript:alert(1)", "http://localhost/\nbad"):
            self.env["NETPROBE_WEB_URL"] = invalid
            self.assertNotEqual(self.run_bridge().returncode, 0)
        self.assertEqual(len(self.calls()), 1)

    def test_empty_queue_initializes_without_synthetic_notification(self):
        self.assertEqual(self.run_bridge().returncode, 0)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.acks, [])

    def test_failed_notify_remains_queued_for_later_retry(self):
        self.records = [self.record()]
        self.fail_flag.touch()
        self.assertNotEqual(self.run_bridge().returncode, 0)
        self.assertEqual(self.acks, [])
        self.fail_flag.unlink()
        self.assertEqual(self.run_bridge(1060).returncode, 0)
        self.assertEqual(len(self.calls()), 2)
        self.assertEqual(len(self.acks), 1)
        self.assertEqual(self.records, [])

    def test_ack_failure_retries_without_resending_delivered_notification(self):
        self.records = [self.record()]
        self.ack_status = 503
        self.assertNotEqual(self.run_bridge().returncode, 0)
        self.ack_status = 200
        self.assertEqual(self.run_bridge(1060).returncode, 0)
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual(len(self.acks), 2)
        self.assertEqual(self.records, [])

    def test_malformed_record_rejects_entire_batch_before_delivery(self):
        for bad in ({"id": "../../oops"}, {"id": True}, {"level": "critical"}, {"message": None},
                    {"ts": "123"}, {"subject": "bad\nheader"}, {"message": "x" * 4097}):
            with self.subTest(bad=bad.keys()):
                self.records = [self.record(), self.record(id=2, **bad)] if "id" not in bad else [self.record(), self.record(**bad)]
                self.run_bridge()
                self.assertEqual(self.calls(), [])
                self.assertEqual(self.acks, [])

    def test_invalid_json_and_oversized_body_never_ack(self):
        for raw in (b"not json", b'{"notifications":null}', b"x" * 600000):
            self.raw = raw
            self.run_bridge()
            self.assertEqual(self.calls(), [])
            self.assertEqual(self.acks, [])

    def test_multiple_json_documents_are_not_accepted_as_one_response(self):
        self.records = [self.record()]
        self.raw = (json.dumps(self.payload()) + json.dumps(self.payload())).encode()
        self.assertNotEqual(self.run_bridge().returncode, 0)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.acks, [])

    def test_corrupt_checkpoint_refuses_delivery_instead_of_forgetting_ids(self):
        self.state.write_text('{"delivered": [1]')
        self.records = [self.record()]
        self.assertNotEqual(self.run_bridge().returncode, 0)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.acks, [])

    def test_duplicate_record_in_response_is_delivered_once(self):
        self.records = [self.record(), self.record()]
        self.assertEqual(self.run_bridge().returncode, 0)
        self.assertEqual(len(self.calls()), 1)

    def test_disabled_alerts_do_not_poll_or_notify(self):
        self.settings.write_text('{"enabled":false}')
        self.records = [self.record()]
        self.status = 503
        self.run_bridge()
        self.run_bridge(1060)
        self.assertEqual(self.requests, [])
        self.assertEqual(self.calls(), [])

    def test_invalid_settings_fail_closed(self):
        for text in ('{"enabled":null}', '{"enabled":"false"}', '{}', 'invalid',
                     '{"enabled":false}{"enabled":true}'):
            self.settings.write_text(text)
            self.assertNotEqual(self.run_bridge().returncode, 0)
            self.assertEqual(self.requests, [])
            self.assertEqual(self.calls(), [])

    def test_two_distinct_spaced_polls_confirm_unavailable_then_recovery(self):
        self.status = 503
        self.run_bridge(1000)
        self.run_bridge(1000)
        self.run_bridge(1049)
        self.assertEqual(self.calls(), [])
        self.run_bridge(1060)
        self.run_bridge(1120)
        self.assertEqual(len(self.calls()), 1)
        self.assertIn("monitoring is not working", self.calls()[0][self.calls()[0].index("-s") + 1])
        self.status = 200
        self.run_bridge(1180)
        self.run_bridge(1180)
        self.assertEqual(len(self.calls()), 1)
        self.run_bridge(1240)
        self.run_bridge(1300)
        self.assertEqual(len(self.calls()), 2)
        self.assertEqual(self.calls()[1][self.calls()[1].index("-i") + 1], "normal")

    def test_failed_health_notification_retries_without_repeat_after_success(self):
        self.status = 503
        self.run_bridge(1000)
        self.fail_flag.touch()
        self.run_bridge(1060)
        self.fail_flag.unlink()
        self.run_bridge(1120)
        self.run_bridge(1180)
        self.assertEqual(len(self.calls()), 2)

    def test_http_200_with_stale_samples_warns_and_requires_two_fresh_polls_to_recover(self):
        self.monitoring["latest_probe_ts"] = 600
        self.run_bridge(1000)
        self.run_bridge(1049)
        self.assertEqual(self.calls(), [])
        self.run_bridge(1060)
        self.run_bridge(1120)
        self.assertEqual(len(self.calls()), 1)
        self.assertIn("monitoring is not working", self.calls()[0][self.calls()[0].index("-s") + 1])
        self.monitoring["latest_probe_ts"] = 1170
        self.run_bridge(1180)
        self.assertEqual(len(self.calls()), 1)
        self.run_bridge(1240)
        self.assertEqual(len(self.calls()), 2)
        self.assertEqual(self.calls()[1][self.calls()[1].index("-i") + 1], "normal")

    def test_reachable_but_stale_api_does_not_clear_confirmed_outage(self):
        self.status = 503
        self.run_bridge(1000)
        self.run_bridge(1060)
        self.assertEqual(len(self.calls()), 1)
        self.status = 200
        self.monitoring["latest_probe_ts"] = 600
        self.run_bridge(1120)
        self.run_bridge(1180)
        self.assertEqual(len(self.calls()), 1)

    def test_future_sample_is_unhealthy_and_queued_events_are_not_delivered_or_acked(self):
        self.monitoring["latest_probe_ts"] = 2000
        self.records = [self.record()]
        self.assertNotEqual(self.run_bridge(1000).returncode, 0)
        self.assertEqual(self.calls(), [])
        self.run_bridge(1060)
        self.assertEqual(len(self.calls()), 1)
        self.assertIn("monitoring is not working", self.calls()[0][self.calls()[0].index("-s") + 1])
        self.assertEqual(self.acks, [])

    def test_startup_grace_is_quiet_but_cannot_confirm_recovery_without_a_sample(self):
        self.monitoring.update(latest_probe_ts=None, process_started_at=1000)
        self.run_bridge(1000)
        self.run_bridge(1060)
        self.run_bridge(1180)
        self.assertEqual(self.calls(), [])
        self.run_bridge(1240)
        self.run_bridge(1300)
        self.assertEqual(len(self.calls()), 1)
        self.monitoring["process_started_at"] = 1300
        self.run_bridge(1360)
        self.run_bridge(1420)
        self.assertEqual(len(self.calls()), 1)
        self.monitoring["latest_probe_ts"] = 1470
        self.run_bridge(1480)
        self.assertEqual(len(self.calls()), 1)
        self.run_bridge(1540)
        self.assertEqual(len(self.calls()), 2)

    def test_malformed_or_missing_monitoring_metadata_fails_closed(self):
        self.records = [self.record()]
        invalid_fields = [("latest_probe_ts", "missing"), ("process_started_at", "missing"),
                          ("max_sample_age", "missing"), ("latest_probe_ts", True),
                          ("latest_probe_ts", 999.5), ("latest_probe_ts", float("inf")),
                          ("process_started_at", None), ("process_started_at", 1001),
                          ("max_sample_age", "180"), ("max_sample_age", None)]
        for key, value in invalid_fields:
            with self.subTest(key=key, value=value):
                payload = self.payload()
                if value == "missing":
                    payload.pop(key)
                else:
                    payload[key] = value
                self.raw = json.dumps(payload).encode()
                self.assertNotEqual(self.run_bridge().returncode, 0)
                self.assertEqual(self.calls(), [])
                self.assertEqual(self.acks, [])

    def test_sample_exactly_at_age_limit_still_delivers(self):
        self.monitoring["latest_probe_ts"] = 820
        self.records = [self.record()]
        self.assertEqual(self.run_bridge(1000).returncode, 0)
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual(len(self.acks), 1)

    def test_missing_or_permissive_token_does_not_poll(self):
        self.token.chmod(0o644)
        self.assertNotEqual(self.run_bridge().returncode, 0)
        self.token.unlink()
        self.assertNotEqual(self.run_bridge().returncode, 0)
        self.assertEqual(self.requests, [])

    def test_notify_reading_stdin_cannot_swallow_remaining_records(self):
        self.executable("notify", 'import json,os,sys\n'
                        'sys.stdin.read()\n'
                        'with open(os.environ["FIXTURE_LOG"],"a") as f: f.write(json.dumps(sys.argv[1:])+"\\n")\n')
        self.records = [self.record(id=1), self.record(id=2, subject="Internet Health: second")]
        result = self.run_bridge()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.calls()), 2)
        self.assertEqual([a[0] for a in self.acks], ["/api/notifications/1/ack", "/api/notifications/2/ack"])

    def test_checkpoint_ids_that_are_no_longer_pending_are_forgotten(self):
        self.state.write_text('{"health":"OK","candidate":"OK","streak":0,"last_poll":0,"delivered":[7,8]}')
        self.records = [self.record(id=8)]
        result = self.run_bridge()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [])  # id 8 was already delivered, only its ack is retried
        self.assertEqual(json.loads(self.state.read_text())["delivered"], [])

    def test_lock_file_location_is_configurable_and_off_the_user_share(self):
        lock = self.root / "elsewhere.lock"
        self.env["NETPROBE_LOCK_FILE"] = str(lock)
        self.records = [self.record()]
        self.assertEqual(self.run_bridge().returncode, 0)
        self.assertTrue(lock.exists())
        self.assertFalse(Path(str(self.state) + ".lock").exists())

    def test_token_of_at_least_32_hex_characters_is_accepted(self):
        self.token.write_text("b" * 32 + "\n")
        self.records = [self.record()]
        result = self.run_bridge()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.requests, [("/api/notifications/pending", "b" * 32)])

    def test_description_from_the_api_is_used_and_message_is_passed_unchanged(self):
        self.records = [self.record(description="Нет интернета. Проверьте провайдера.", message="🔴 Нет интернета\\nGoogle — нет ответа")]
        result = self.run_bridge()
        self.assertEqual(result.returncode, 0, result.stderr)
        args = self.calls()[0]
        self.assertEqual(args[args.index("-d") + 1], "Нет интернета. Проверьте провайдера.")
        self.assertEqual(args[args.index("-m") + 1], "🔴 Нет интернета\\nGoogle — нет ответа")

    def test_missing_description_falls_back_to_a_flat_excerpt_without_literal_newlines(self):
        self.records = [self.record(message="Line one\\nLine two")]
        self.assertEqual(self.run_bridge().returncode, 0)
        args = self.calls()[0]
        self.assertEqual(args[args.index("-d") + 1], "Line one Line two")

    def test_invalid_description_rejects_the_whole_batch(self):
        for bad in ("x" * 241, "bad\nline", 5):
            self.records = [self.record(description=bad)]
            self.assertNotEqual(self.run_bridge().returncode, 0)
            self.assertEqual(self.calls(), [])
            self.assertEqual(self.acks, [])

    def test_health_messages_are_russian_with_icons(self):
        self.monitoring = {"latest_probe_ts": 100}
        self.assertNotEqual(self.run_bridge(1000).returncode, 0)
        self.assertNotEqual(self.run_bridge(1100).returncode, 0)
        subject = self.calls()[0][self.calls()[0].index("-s") + 1]
        self.assertTrue(subject.startswith("⚠️") and "Internet Health" in subject, subject)

    def test_concurrent_invocation_skips_locked_delivery(self):
        self.records = [self.record()]
        self.delay.touch()
        first = subprocess.Popen(["bash", str(SCRIPT)], env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 5
            while not self.log.exists() and first.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            result = self.run_bridge()
            out, err = first.communicate(timeout=10)
            self.assertEqual(first.returncode, 0, out + err)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(self.calls()), 1)
            self.assertEqual(len(self.requests), 1)
        finally:
            if first.poll() is None:
                first.kill()
                first.communicate()


if __name__ == "__main__":
    unittest.main()
