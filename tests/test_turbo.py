"""Turbo mode: a 5-second watcher that reports outages too short for the regular probe loop."""
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
from fixture_db import make_database  # noqa: E402

T0 = 2_000_000_000
ROUTER = "192.168.1.1"
NET = ["1.1.1.1", "8.8.8.8"]


class TurboTests(unittest.TestCase):
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
        self.ext.turbo_configure(ROUTER, NET)
        self.app = Flask(__name__)
        install_routes(self.app, self.ext)
        self.client = self.app.test_client()
        self.down = set()  # addresses that currently do not answer
        self.calls = []

    def pinger(self, address):
        self.calls.append(address)
        return address not in self.down

    def turbo(self, on=True):
        self.ext.update_settings({"turbo": on})

    def rounds(self, n, down=()):
        self.down = set(down)
        for _ in range(n):
            self.ext.turbo_tick(self.pinger)
            self.now += 5

    def main_cycle(self, rows=None, ts=None):
        """One regular probe-loop cycle recorded at `ts` (default: now)."""
        self.ext.record_probe(ts or self.now, rows or [dict(host="google.com", address="8.8.8.8", latency=20, jitter=1, loss=0)], {})

    def notes(self):
        with sqlite3.connect(self.db) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute("SELECT * FROM np_notifications ORDER BY id")]

    def turbo_notes(self):
        return [n for n in self.notes() if n["subject"].startswith("⚡")]

    def outages(self):
        with sqlite3.connect(self.db) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute("SELECT * FROM np_outages ORDER BY id")]

    def outage(self, seconds, down=NET, after=4):
        """Healthy rounds, an outage of `seconds`, recovery, then a main cycle that finishes after it."""
        self.rounds(2)
        self.rounds(seconds // 5, down)
        self.rounds(after)
        self.main_cycle()
        self.rounds(1)

    # -- detection and the message -------------------------------------------
    def test_forty_second_outage_gives_exactly_one_message_after_recovery(self):
        self.turbo()
        self.rounds(2)
        self.rounds(8, NET)
        self.assertEqual(self.turbo_notes(), [], "nothing is sent while the outage lasts")
        self.rounds(3)
        self.main_cycle()
        self.rounds(3)
        notes = self.turbo_notes()
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["level"], "warning")
        self.assertIn("Short internet outage: 40 s", notes[0]["subject"])
        self.assertIn("Turbo mode", notes[0]["message"])
        self.assertNotRegex(notes[0]["message"], r"[\x00-\x1f\x7f]")
        [row] = self.outages()
        self.assertEqual((row["end_ts"] - row["start_ts"], row["kind"], row["status"]), (40, "internet", "sent"))

    def test_one_failed_round_or_one_silent_address_is_not_an_outage(self):
        self.turbo()
        self.rounds(3)
        self.rounds(1, NET)
        self.rounds(5)
        self.rounds(10, NET[:1])  # one of two internet hosts drops every echo: not an outage
        self.rounds(3)
        self.main_cycle()
        self.rounds(2)
        self.assertEqual(self.outages(), [])
        self.assertEqual(self.notes(), [])

    def test_router_down_is_reported_as_a_router_outage(self):
        self.turbo()
        self.outage(30, [ROUTER] + NET)
        [note] = self.turbo_notes()
        self.assertIn("router outage", note["subject"])
        self.assertEqual(self.outages()[0]["kind"], "router")

    def test_message_waits_for_the_regular_cycle_running_during_the_outage(self):
        self.turbo()
        self.rounds(2)
        self.rounds(6, NET)
        self.rounds(4)
        self.assertEqual(self.turbo_notes(), [], "the main cycle running at recovery may still alert")
        self.assertEqual(self.outages()[0]["status"], "pending")
        self.main_cycle()
        self.rounds(1)
        self.assertEqual(len(self.turbo_notes()), 1)

    def test_pending_message_is_sent_anyway_when_the_regular_loop_is_silent(self):
        self.turbo()
        self.rounds(2)
        self.rounds(6, NET)
        self.rounds(70)  # ~6 min without a regular cycle
        self.assertEqual(len(self.turbo_notes()), 1)

    def test_outage_already_reported_by_the_main_monitor_gives_no_turbo_message(self):
        self.turbo()
        self.rounds(2)
        bad = [dict(host="google.com", address="8.8.8.8", latency=None, jitter=None, loss=100)]
        for _ in range(4):  # a long outage: two failing regular cycles confirm it
            self.rounds(10, NET)
            self.main_cycle(bad)
        self.rounds(4)
        self.main_cycle()
        self.rounds(1)
        self.assertTrue(any(n["level"] == "alert" for n in self.notes()), self.notes())
        self.assertEqual(self.turbo_notes(), [])
        self.assertEqual(self.outages()[0]["status"], "covered")

    # -- settings -----------------------------------------------------------
    def test_turbo_off_never_pings(self):
        self.rounds(20, NET)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.outages(), [])

    def test_turbo_is_off_by_default_and_validated_like_enabled(self):
        self.assertIs(self.ext.settings()["turbo"], False)
        for bad in (1, "true", None):
            with self.assertRaises(ValueError):
                self.ext.validate({"turbo": bad})
        r = self.client.post("/api/settings", json={"turbo": True}, headers={"X-NetProbe-Request": "settings"})
        self.assertEqual(r.status_code, 200)
        self.assertIs(r.get_json()["settings"]["turbo"], True)
        self.assertIs(json.loads((self.root / "alert-settings.json").read_text())["turbo"], True)

    def test_toggling_turbo_keeps_the_main_confirmation_progress(self):
        self.main_cycle([dict(host="google.com", address="8.8.8.8", latency=None, jitter=None, loss=100)])
        self.turbo()
        self.now += 30
        self.main_cycle([dict(host="google.com", address="8.8.8.8", latency=None, jitter=None, loss=100)])
        self.assertTrue(any(n["level"] == "alert" for n in self.notes()), "streak was reset by the turbo toggle")

    def test_alerts_disabled_records_the_outage_without_a_message(self):
        self.turbo()
        self.ext.update_settings({"enabled": False})
        self.outage(40)
        self.assertEqual(self.notes(), [])
        self.assertEqual(self.outages()[0]["status"], "off")

    def test_switching_turbo_off_mid_outage_closes_it_silently(self):
        self.turbo()
        self.rounds(2)
        self.rounds(4, NET)
        self.turbo(False)
        self.rounds(3)
        self.assertEqual([o["status"] for o in self.outages()], ["stopped"])
        self.assertEqual(self.notes(), [])

    # -- flood control and queueing ---------------------------------------
    def test_at_most_three_messages_per_hour_and_the_next_one_counts_the_rest(self):
        self.turbo()
        for _ in range(5):
            self.outage(20)
        self.assertEqual(len(self.turbo_notes()), 3)
        self.assertEqual([o["status"] for o in self.outages()], ["sent"] * 3 + ["capped"] * 2)
        self.now += 3600
        self.outage(20)
        notes = self.turbo_notes()
        self.assertEqual(len(notes), 4)
        self.assertIn("+2 more short outages since", notes[-1]["message"])
        self.assertEqual([o["status"] for o in self.outages()][-3:], ["counted", "counted", "sent"])

    def test_timestamp_collision_with_a_queued_notification_is_retried(self):
        self.turbo()
        self.rounds(2)
        self.rounds(6, NET)
        self.rounds(4)
        self.main_cycle()
        with sqlite3.connect(self.db) as conn:  # the second the turbo message will use is already taken
            for ts in (self.now, self.now + 1):
                conn.execute("INSERT INTO np_notifications(ts,level,subject,message) VALUES (?,?,?,?)", (ts, "normal", "x", "y"))
        self.rounds(1)
        self.assertEqual(len(self.turbo_notes()), 1)
        self.assertEqual(self.turbo_notes()[0]["ts"], self.now - 5 + 2)

    def test_main_notification_is_not_lost_when_turbo_took_its_second(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO np_notifications(ts,level,subject,message) VALUES (?,?,?,?)", (self.now, "warning", "⚡ x", "y"))
        bad = [dict(host="google.com", address="8.8.8.8", latency=None, jitter=None, loss=100)]
        self.main_cycle(bad, ts=self.now - 30)
        self.main_cycle(bad)
        self.assertEqual(sum(n["level"] == "alert" for n in self.notes()), 1)

    # -- review regressions ----------------------------------------------------
    def test_a_lone_router_blip_does_not_turn_a_later_internet_outage_into_a_router_outage(self):
        self.turbo()
        self.rounds(1, [ROUTER] + NET)
        self.rounds(3)
        self.outage(30)
        self.assertEqual(self.outages()[0]["kind"], "internet")
        self.assertIn("internet outage", self.turbo_notes()[0]["subject"])

    def test_outage_the_main_monitor_reported_before_a_restart_is_not_repeated(self):
        down = [dict(host="8.8.8.8", address="8.8.8.8", latency=None, jitter=None, loss=100)]
        self.main_cycle(down, ts=self.now - 120)
        self.main_cycle(down, ts=self.now - 60)  # confirmed and alerted long before turbo saw anything
        with sqlite3.connect(self.db) as conn:
            conn.execute("DELETE FROM np_notifications")  # already delivered and acknowledged
        self.turbo()
        self.rounds(6, NET)
        self.rounds(3)
        self.main_cycle()
        self.rounds(1)
        self.assertEqual(self.turbo_notes(), [])
        self.assertEqual(self.outages()[0]["status"], "covered")

    def test_alert_stamped_at_a_cycle_start_before_the_outage_still_covers_it(self):
        self.turbo()
        self.rounds(2)
        with sqlite3.connect(self.db) as conn:  # confirm_samples=1: the alert carries the cycle start, 60 s earlier
            conn.execute("INSERT INTO np_notifications(ts,level,subject,message) VALUES (?,?,?,?)", (self.now - 60, "alert", "🔴 NetProbe: No internet", "x"))
        self.rounds(6, NET)
        self.rounds(3)
        self.main_cycle()
        self.rounds(1)
        self.assertEqual(self.turbo_notes(), [])

    def test_pending_outage_is_decided_when_turbo_is_switched_off_and_old_ones_expire(self):
        self.turbo()
        self.rounds(2)
        self.rounds(6, NET)
        self.rounds(3)
        self.turbo(False)
        self.main_cycle()
        self.rounds(1)
        self.assertEqual(len(self.turbo_notes()), 1, "the outage happened while turbo was on")
        self.assertNotIn("pending", [o["status"] for o in self.outages()])
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO np_outages(start_ts,end_ts,last_ts,kind,status) VALUES (?,?,?,?,?)", (T0 - 9000, T0 - 8960, T0 - 8960, "internet", "pending"))
        from netprobe_extensions import Extension
        ext = Extension(str(self.db), str(self.root), clock=lambda: self.now)
        ext.turbo_configure(ROUTER, NET)
        ext.turbo_tick(self.pinger)
        self.assertEqual(self.outages()[-1]["status"], "stopped", "a week-old outage is never sent")
        self.assertEqual(len(self.turbo_notes()), 1)

    def test_settings_report_whether_the_watcher_runs(self):
        self.assertIs(self.client.get("/api/settings").get_json()["turbo_available"], False)
        with patch("threading.Thread.start"):
            self.ext.start_turbo("10.0.0.1", ["1.1.1.1"], [])
        self.assertIs(self.client.get("/api/settings").get_json()["turbo_available"], True)
        from netprobe_extensions import Extension
        other = Extension(str(self.db), str(self.root), clock=lambda: self.now)
        self.assertIsNone(other.start_turbo("10.0.0.1", ["fast.com"], ["10.0.0.1"]))
        self.assertFalse(other.turbo_available)

    # -- targets, thread and API ----------------------------------------------
    def test_targets_prefer_ip_sites_then_public_dns(self):
        from netprobe_extensions import turbo_targets
        self.assertEqual(turbo_targets("10.0.0.1", ["google.com", "1.1.1.1", "8.8.8.8", "9.9.9.9"], ["8.8.4.4"]),
                         ("10.0.0.1", ["1.1.1.1", "8.8.8.8"]))
        self.assertEqual(turbo_targets("", ["fast.com"], ["10.0.0.1", "9.9.9.9", "1.1.1.1"]), (None, ["9.9.9.9", "1.1.1.1"]))
        self.assertEqual(turbo_targets("10.0.0.1", ["fast.com"], ["10.0.0.1"]), ("10.0.0.1", []))

    def test_watcher_loop_survives_errors_and_only_reads_settings_when_off(self):
        sleeps = []
        def sleep(seconds):
            sleeps.append(seconds)
            self.now += seconds
        def broken(address):
            raise OSError("ping missing")
        self.ext.turbo_run(self.pinger, sleep, rounds=3)
        self.assertEqual((self.calls, sleeps), ([], [5, 5, 5]))
        self.turbo()
        self.ext.turbo_run(broken, sleep, rounds=2)  # a failing pinger counts as no reply, never kills the loop
        self.ext.turbo_run(self.pinger, sleep, rounds=2)
        self.assertEqual(len(self.calls), 6)

    def test_outages_are_listed_in_the_loss_report(self):
        self.turbo()
        self.outage(40)
        self.outage(25, [ROUTER] + NET)
        data = self.client.get("/api/loss?range=3600").get_json()["data"]
        self.assertEqual([(o["kind"], o["duration"]) for o in data["outages"]], [("router", 25), ("internet", 40)])
        self.assertEqual(self.client.get("/api/loss?from=%d&to=%d" % (T0 - 900, T0 - 1)).get_json()["data"]["outages"], [])

    def test_app_starts_the_watcher_with_router_sites_and_dns(self):
        env = {"DB_PATH": str(self.root / "app.sqlite"), "DB_ENGINE": "sqlite", "USE_POSTGRES": "false",
               "PROBE_INTERVAL": "30", "PING_COUNT": "4", "SPEEDTEST_ENABLED": "false",
               "ROUTER_IP": "192.168.1.1", "SITES": "google.com,1.1.1.1", "DNS_NAMESERVER_1_IP": "8.8.8.8"}
        with patch.dict(os.environ, env), patch("threading.Thread.start"), \
                patch("netprobe_extensions.Extension.start_turbo") as start:
            spec = importlib.util.spec_from_file_location("turbo_app", ROOT / "src/app.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        start.assert_called_once()
        self.assertEqual(start.call_args.args[:2], ("192.168.1.1", ["google.com", "1.1.1.1"]))


if __name__ == "__main__":
    unittest.main()
