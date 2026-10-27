"""Regression tests for independent-review findings and notification flood control."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
from fixture_db import make_database  # noqa: E402


class ReviewFixTests(unittest.TestCase):
    def setUp(self):
        from netprobe_extensions import Extension, install_routes
        from flask import Flask
        self.Extension = Extension
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "netprobe.sqlite"
        make_database(self.db)
        self.now = 2_000_000_000
        self.ext = Extension(str(self.db), str(self.root), clock=lambda: self.now)
        self.app = Flask(__name__)
        install_routes(self.app, self.ext)
        self.client = self.app.test_client()
        self.headers = {"X-NetProbe-Request": "settings"}

    def ping(self, target="a.example", loss=0, latency=20):
        measured = loss < 100
        return {"host": target, "address": "192.0.2.1", "latency": latency if measured else None,
                "jitter": 1 if measured else None, "loss": loss, "measured": measured}

    def record(self, rows, advance=30, **kwargs):
        self.ext.record_probe(self.now, rows, kwargs.pop("dns", {}), **kwargs)
        self.now += advance

    def pending(self):
        key = (self.root / "notify-token").read_text().strip()
        return self.client.get("/api/notifications/pending", headers={"X-NetProbe-Notify-Key": key}).get_json()["notifications"]

    def events(self):
        return self.client.get("/api/events?range=1000000").get_json()["data"]

    def count(self, table):
        with sqlite3.connect(self.db) as conn:
            return conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]

    # -- state machine -------------------------------------------------
    def test_alternating_down_and_degraded_still_confirms_a_problem_once(self):
        for loss in (100, 50) * 4:
            self.record([self.ping(loss=loss)])
        self.assertEqual(len(self.events()), 1)
        self.assertEqual(self.events()[0]["previous"], "OK")
        self.assertEqual(len(self.pending()), 1)

    def test_down_degraded_flapping_while_already_bad_does_not_repeat_notifications(self):
        for loss in (100, 100) + (50, 100) * 20:
            self.record([self.ping(loss=loss)])
        self.assertEqual(len(self.pending()), 1)

    # -- DNS unavailability --------------------------------------------
    def test_unanswered_dns_server_is_down_then_recovers_after_answers(self):
        for _ in range(2):
            self.record([self.ping()], dns={}, dns_failed=["9.9.9.9"])
        down = [e for e in self.events() if e["target"] == "dns:9.9.9.9"]
        self.assertEqual([e["state"] for e in down], ["DOWN"])
        self.assertIn("No DNS answer", down[0]["reasons"][0])
        for _ in range(2):
            self.record([self.ping()], dns={"9.9.9.9": 12.0})
        states = [e["state"] for e in self.events() if e["target"] == "dns:9.9.9.9"]
        self.assertEqual(states, ["OK", "DOWN"])  # newest first
        with sqlite3.connect(self.db) as conn:
            nulls = conn.execute("SELECT COUNT(*) FROM np_targets WHERE target='dns:9.9.9.9' AND latency IS NULL").fetchone()[0]
        self.assertEqual(nulls, 2)

    # -- settings ------------------------------------------------------
    def test_disabling_alerts_does_not_discard_queued_notifications(self):
        for loss in (100, 100):
            self.record([self.ping(loss=loss)])
        self.assertEqual(len(self.pending()), 1)
        self.assertEqual(self.client.post("/api/settings", json={"enabled": False}, headers=self.headers).status_code, 200)
        self.assertEqual(self.pending(), [])
        self.assertEqual(self.client.post("/api/settings", json={"enabled": True}, headers=self.headers).status_code, 200)
        self.assertEqual(len(self.pending()), 1)

    def test_saving_unchanged_settings_keeps_confirmation_progress(self):
        self.record([self.ping(loss=100)])
        for body in ({}, {"loss_pct": 10}, {"enabled": True}):
            self.assertEqual(self.client.post("/api/settings", json=body, headers=self.headers).status_code, 200)
        self.record([self.ping(loss=100)])
        self.assertEqual(len(self.events()), 1)

    def test_changed_threshold_restarts_confirmation(self):
        self.record([self.ping(loss=100)])
        self.assertEqual(self.client.post("/api/settings", json={"latency_ms": 200}, headers=self.headers).status_code, 200)
        self.record([self.ping(loss=100)])
        self.assertEqual(self.events(), [])

    def test_https_proxy_origin_with_same_host_is_accepted_and_foreign_is_not(self):
        ok = self.client.post("/api/settings", json={}, headers=dict(self.headers, Origin="https://localhost"))
        bad = self.client.post("/api/settings", json={}, headers=dict(self.headers, Origin="https://evil.example"))
        self.assertEqual((ok.status_code, bad.status_code), (200, 403))

    def test_range_is_bounded_to_thirty_one_days(self):
        self.assertEqual(self.client.get("/api/targets/recent?range=2678400").status_code, 200)
        self.assertEqual(self.client.get("/api/targets/recent?range=2678401").status_code, 400)

    # -- startup robustness --------------------------------------------
    def test_empty_token_file_is_replaced_instead_of_crashing(self):
        (self.root / "notify-token").write_text("")
        ext = self.Extension(str(self.db), str(self.root), clock=lambda: self.now)
        self.assertGreaterEqual(len(ext.token), 64)
        self.assertEqual((self.root / "notify-token").read_text().strip(), ext.token)
        self.assertEqual(oct((self.root / "notify-token").stat().st_mode & 0o777), "0o600")

    def test_corrupt_settings_do_not_stop_history_recording(self):
        (self.root / "alert-settings.json").write_text('{"loss_pct": 0}')
        ext = self.Extension(str(self.db), str(self.root), clock=lambda: self.now)
        ext.record_probe(self.now, [self.ping()], {})
        self.assertEqual(self.count("np_targets"), 1)
        self.assertEqual(json.loads((self.root / "alert-settings.json").read_text()), {"loss_pct": 0})

    def test_notification_text_is_single_line_and_bounded(self):
        rows = [self.ping(target="bad\x07name\n%d.example" % i, loss=100) for i in range(300)]
        for _ in range(2):
            self.record(rows)
        message = self.pending()[0]["message"]
        self.assertLessEqual(len(message), 3500)
        self.assertNotRegex(message, r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

    def test_app_keeps_working_when_extension_cannot_start(self):
        env = {"DB_PATH": str(self.root / "app.sqlite"), "DB_ENGINE": "sqlite", "USE_POSTGRES": "false",
               "PROBE_INTERVAL": "30", "PING_COUNT": "4", "SPEEDTEST_ENABLED": "false",
               "ROUTER_IP": "192.168.1.1", "SITES": "google.com", "DNS_NAMESERVER_1_IP": "8.8.8.8"}
        with patch.dict(os.environ, env), patch("threading.Thread.start"), \
                patch("netprobe_extensions.Extension", side_effect=RuntimeError("boom")):
            spec = importlib.util.spec_from_file_location("guarded_app", ROOT / "src/app.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        self.assertIsNone(module.custom_extension)
        self.assertEqual(module.app.test_client().get("/api/config").status_code, 200)

    # -- flood control -------------------------------------------------
    def test_flapping_target_is_muted_after_a_few_notifications_with_one_summary(self):
        for i in range(2880):  # one day of 30 s probes, 2 bad / 2 good
            self.record([self.ping(loss=100 if i % 4 < 2 else 0)])
        notes = self.pending()
        self.assertLessEqual(len(notes), 12, "flapping floods the user")
        self.assertEqual(sum("keeps changing" in n["message"] for n in notes), 1)
        self.assertGreater(len(self.events()), 100)  # every real transition is still recorded

    def test_flapping_other_target_is_not_muted_by_a_neighbour(self):
        for i in range(400):
            self.record([self.ping("noisy.example", loss=100 if i % 4 < 2 else 0), self.ping("quiet.example")])
        self.record([self.ping("noisy.example"), self.ping("quiet.example", loss=100)])
        self.record([self.ping("noisy.example"), self.ping("quiet.example", loss=100)])
        self.assertTrue(any("quiet.example - no ping reply" in n["message"] for n in self.pending()))

    def test_target_that_stays_quiet_gets_one_stable_again_notification(self):
        for i in range(60):
            self.record([self.ping(loss=100 if i % 4 < 2 else 0)])
        before = len(self.pending())
        for _ in range(80):  # 40 minutes without a state change
            self.record([self.ping()])
        notes = self.pending()
        self.assertEqual(len(notes), before + 1)
        self.assertIn("stabilized", notes[-1]["message"])
        for _ in range(10):
            self.record([self.ping()])
        self.assertEqual(len(self.pending()), before + 1)

    def test_steady_problem_after_flapping_is_reported_once(self):
        for i in range(60):
            self.record([self.ping(loss=100 if i % 4 < 2 else 0)])
        for _ in range(400):
            self.record([self.ping(loss=100)])
        notes = self.pending()
        self.assertLessEqual(len(notes), 9)


    def test_one_silent_dns_server_of_several_is_an_interruption_not_an_outage(self):
        for _ in range(2):
            self.record([self.ping()], dns={"8.8.8.8": 12.0}, dns_failed=["1.1.1.1"])
        silent = [e for e in self.events() if e["target"] == "dns:1.1.1.1"]
        self.assertEqual([e["state"] for e in silent], ["DEGRADED"])
        self.assertEqual(silent[0]["reasons"], ["No DNS answer"])
        self.assertFalse([e for e in self.events() if e["target"] == "dns:8.8.8.8"])

    def test_all_dns_servers_silent_is_an_outage(self):
        for _ in range(2):
            self.record([self.ping()], dns={}, dns_failed=["8.8.8.8", "1.1.1.1"])
        self.assertEqual(sorted(e["state"] for e in self.events() if e["target"].startswith("dns:")), ["DOWN", "DOWN"])

    def test_dns_failed_wins_over_a_latency_that_was_only_elapsed_time(self):
        for _ in range(2):
            self.record([self.ping()], dns={"9.9.9.9": 20.0}, dns_failed=["9.9.9.9"])
        self.assertEqual([e["state"] for e in self.events() if e["target"] == "dns:9.9.9.9"], ["DOWN"])

    def test_stable_again_waits_for_the_current_state_to_match_and_recovery_is_not_remuted(self):
        for i in range(60):
            self.record([self.ping(loss=100 if i % 4 < 2 else 0)])
        base = len(self.pending())
        for _ in range(2):
            self.record([self.ping(loss=100)])
        for _ in range(70):  # outage lasts past the quiet period
            self.record([self.ping(loss=100)])
        self.assertFalse(any("stabilized, now: working" in n["message"] for n in self.pending()))
        for _ in range(4):  # real recovery
            self.record([self.ping()])
        text = " | ".join(n["message"] for n in self.pending()[base:])
        self.assertNotIn("keeps changing", text)  # recovery after a long steady outage is named, not re-muted
        self.assertIn("back to normal", text)

    def test_problem_after_a_quiet_period_is_reported_by_name_not_as_unstable(self):
        for i in range(60):
            self.record([self.ping(loss=100 if i % 4 < 2 else 0)])
        for _ in range(80):
            self.record([self.ping()])
        for _ in range(3):
            self.record([self.ping(loss=100)])
        self.assertIn("a.example - no ping reply", self.pending()[-1]["message"])

    def test_unstable_message_names_the_current_state(self):
        for i in range(30):
            self.record([self.ping(loss=100 if i % 4 < 2 else 0)])
        unstable = [n["message"] for n in self.pending() if "keeps changing" in n["message"]]
        self.assertEqual(len(unstable), 1)
        self.assertRegex(unstable[0], r"now: (not responding|working|working with interruptions)")

    def test_settings_file_failing_validation_can_be_repaired_by_a_valid_post(self):
        (self.root / "alert-settings.json").write_text('{"enabled": true, "loss_pct": 0}')
        self.assertEqual(self.client.get("/api/settings").status_code, 503)
        key = (self.root / "notify-token").read_text().strip()
        self.assertEqual(self.client.get("/api/notifications/pending", headers={"X-NetProbe-Notify-Key": key}).status_code, 200)
        fixed = self.client.post("/api/settings", json={"loss_pct": 12}, headers=self.headers)
        self.assertEqual(fixed.status_code, 200)
        self.assertEqual(self.client.get("/api/settings").get_json()["settings"]["loss_pct"], 12)

    def test_alerts_disabled_do_not_build_up_muting_state(self):
        self.client.post("/api/settings", json={"enabled": False}, headers=self.headers)
        for i in range(60):
            self.record([self.ping(loss=100 if i % 4 < 2 else 0)])
        self.assertEqual(self.count("np_flap"), 0)


    def test_token_is_created_even_when_hard_links_are_unsupported(self):
        (self.root / "notify-token").unlink()
        with patch("netprobe_extensions.os.link", side_effect=OSError(1, "Operation not permitted")):
            ext = self.Extension(str(self.db), str(self.root), clock=lambda: self.now)
        self.assertGreaterEqual(len(ext.token), 64)
        self.assertEqual(oct((self.root / "notify-token").stat().st_mode & 0o777), "0o600")


if __name__ == "__main__":
    unittest.main()
