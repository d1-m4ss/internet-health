"""History retention: what is deleted, what never is, when the nightly run starts, and that nothing real is touched."""
from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
from fixture_db import make_database  # noqa: E402
import netprobe_extensions as npx  # noqa: E402

HEADERS = {"X-NetProbe-Request": "settings"}
BERLIN = ZoneInfo("Europe/Berlin")
DAY = 86400
NOW = int(datetime(2026, 3, 10, 12, 0, tzinfo=BERLIN).timestamp())
AGES = (1, 10, 40, 100, 200, 400)  # days old


def at(hour, day=10, month=3):
    return int(datetime(2026, month, day, hour, 0, tzinfo=BERLIN).timestamp())


class CleanupBase(unittest.TestCase):
    def setUp(self):
        from flask import Flask
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "netprobe.sqlite"
        make_database(self.db)
        env = patch.dict(os.environ, {"APP_TIMEZONE": "Europe/Berlin"})
        env.start()
        self.addCleanup(env.stop)
        self.now = NOW
        self.ext = npx.Extension(str(self.db), str(self.root), clock=lambda: self.now)
        self.conn = sqlite3.connect(self.db)
        self.addCleanup(self.conn.close)
        for table in ("measurements", "dns_measurements", "speedtests"):
            self.conn.execute("DELETE FROM " + table)
        for age in AGES:
            ts = NOW - age * DAY - 60  # a minute inside the age bucket
            self.conn.execute("INSERT INTO measurements(ts,avg_latency_ms) VALUES (?,20)", (ts,))
            self.conn.execute("INSERT INTO dns_measurements(ts,server_ip,latency_ms) VALUES (?,'8.8.8.8',4)", (ts,))
            self.conn.execute("INSERT INTO speedtests(ts,download_mbps,backend) VALUES (?,400,'python')", (ts,))
            self.conn.execute("INSERT INTO np_targets(cycle_ts,ts,target,address,kind,latency,jitter,loss) VALUES (?,?,?,?,?,?,?,?)",
                              (ts, ts, "google.com", "8.8.8.8", "ping", 20.0, 1.0, 0.0))
            self.conn.execute("INSERT INTO np_events(ts,target,previous,state,reasons,metrics) VALUES (?,?,?,?,?,?)", (ts, "google.com", "OK", "DOWN", "[]", "{}"))
            self.conn.execute("INSERT INTO np_notifications(ts,level,subject,message,acked) VALUES (?,?,?,?,?)", (ts, "normal", "acked %d" % age, "m", 1))
            self.conn.execute("INSERT INTO np_notifications(ts,level,subject,message,acked) VALUES (?,?,?,?,?)", (ts + 1, "normal", "queued %d" % age, "m", 0))
            for status in ("sent", "open", "pending"):
                self.conn.execute("INSERT INTO np_outages(start_ts,end_ts,last_ts,kind,status) VALUES (?,?,?,?,?)",
                                  (ts - 30, None if status == "open" else ts, ts, "internet", status))
        self.conn.execute("INSERT INTO np_state VALUES ('google.com','OK','OK',3,?,?,'[]')", (NOW - 400 * DAY, NOW - 400 * DAY))
        self.conn.execute("INSERT INTO np_flap VALUES ('google.com',0,?,0)", (NOW - 400 * DAY,))
        self.conn.commit()
        self.app = Flask(__name__)
        npx.install_routes(self.app, self.ext)
        self.client = self.app.test_client()

    def count(self, table, where="1"):
        return self.conn.execute("SELECT COUNT(*) FROM %s WHERE %s" % (table, where)).fetchone()[0]

    def total(self):
        return sum(self.count(t) for t, _ in npx.CLEANUP_TABLES)


class CleanupTests(CleanupBase):
    def test_dry_run_counts_and_changes_nothing(self):
        before = self.total()
        result = self.ext.cleanup(30, dry_run=True)
        self.assertEqual(self.total(), before)
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["deleted"]["measurements"], 4)  # 40, 100, 200 and 400 days old
        self.assertEqual(result["total"], sum(result["deleted"].values()))
        self.assertFalse(self.ext.cleanup_path.exists(), "a preview leaves no trace")

    def test_deletes_exactly_what_the_preview_announced(self):
        preview = self.ext.cleanup(90, dry_run=True)
        real = self.ext.cleanup(90)
        self.assertEqual(real["deleted"], preview["deleted"])
        self.assertEqual(real["cutoff"], NOW - 90 * DAY)
        for table in ("measurements", "dns_measurements", "speedtests", "np_targets", "np_events"):
            self.assertEqual(self.count(table), 3, table)  # 1, 10 and 40 days old remain
            self.assertEqual(self.count(table, "ts < %d" % (NOW - 90 * DAY)), 0, table)
        self.assertEqual(self.count("measurements", "ts >= %d" % (NOW - 60 * DAY)), 3)

    def test_the_cutoff_is_strict(self):
        cutoff = NOW - 30 * DAY
        self.conn.execute("INSERT INTO measurements(ts,avg_latency_ms) VALUES (?,1)", (cutoff,))
        self.conn.execute("INSERT INTO measurements(ts,avg_latency_ms) VALUES (?,1)", (cutoff - 1,))
        self.conn.commit()
        self.ext.cleanup(30)
        self.assertEqual(self.count("measurements", "ts = %d" % cutoff), 1, "a record exactly at the cutoff stays")
        self.assertEqual(self.count("measurements", "ts = %d" % (cutoff - 1)), 0)

    def test_what_is_still_needed_is_never_deleted(self):
        self.ext.cleanup(7)
        self.assertEqual(self.count("np_notifications", "acked=0"), len(AGES), "undelivered messages are kept whatever their age")
        self.assertEqual(self.count("np_notifications", "acked=1"), 1, "delivered ones are cleaned")
        self.assertEqual(self.count("np_outages", "status='open'"), len(AGES))
        self.assertEqual(self.count("np_outages", "status='pending'"), len(AGES))
        self.assertEqual(self.count("np_outages", "status='sent'"), 1)
        self.assertEqual(self.count("np_state"), 1)
        self.assertEqual(self.count("np_flap"), 1)
        self.assertEqual(self.conn.execute("SELECT streak FROM np_state").fetchone()[0], 3)

    def test_only_the_offered_ranges_are_accepted(self):
        for bad in (0, 1, 5, -30, 31, 366, True, False, None, "30", 30.0, [30]):
            with self.subTest(days=bad):
                with self.assertRaises(ValueError):
                    self.ext.cleanup(bad)
        self.assertEqual(self.count("measurements"), len(AGES), "nothing was deleted by the refused calls")
        for days in (7, 30, 90, 180, 365):
            self.ext.cleanup(days, dry_run=True)

    def test_a_second_cleanup_waits_instead_of_running_in_parallel(self):
        with self.ext.cleanup_lock:
            with self.assertRaises(npx.CleanupBusy):
                self.ext.cleanup(30)
            response = self.client.post("/api/history/cleanup", json={"days": 30, "dry_run": True}, headers=HEADERS)
            self.assertEqual(response.status_code, 409)
        self.assertEqual(self.count("measurements"), len(AGES))

    def test_large_tables_are_deleted_in_batches(self):
        rows = [(NOW - 200 * DAY + i, 1.0) for i in range(53)]
        self.conn.executemany("INSERT INTO measurements(ts,avg_latency_ms) VALUES (?,?)", rows)
        self.conn.commit()
        with patch.object(npx, "CLEANUP_BATCH", 10), patch.object(npx, "CLEANUP_PAUSE", 0):
            result = self.ext.cleanup(30)
        self.assertEqual(result["deleted"]["measurements"], 53 + 4)
        self.assertEqual(self.count("measurements", "ts < %d" % (NOW - 30 * DAY)), 0)

    def test_the_file_shrinks_after_a_big_cleanup(self):
        payload = "x" * 200
        self.conn.executemany("INSERT INTO np_targets(cycle_ts,ts,target,address,kind,latency) VALUES (?,?,?,?,'ping',1)",
                              [(NOW - 300 * DAY + i, NOW - 300 * DAY + i, "t" + payload, payload) for i in range(20000)])
        self.conn.commit()
        self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        before = self.db.stat().st_size
        result = self.ext.cleanup(180)
        self.assertGreater(result["total"], 20000)
        self.assertLess(self.db.stat().st_size, before / 4, "VACUUM gave the space back")
        self.assertEqual((result["bytes_before"] >= before, result["bytes_after"] < before), (True, True))
        self.assertEqual(self.conn.execute("PRAGMA quick_check").fetchone()[0], "ok")

    def test_big_databases_are_not_compacted_in_the_background(self):
        self.conn.executemany("INSERT INTO measurements(ts,avg_latency_ms) VALUES (?,?)", [(NOW - 300 * DAY + i, 1) for i in range(1500)])
        self.conn.commit()
        with patch.object(npx, "CLEANUP_VACUUM_MAX", 1):
            self.assertEqual(self.ext.compact(), False)
            self.ext.cleanup(30)
        self.assertEqual(self.count("measurements", "ts < %d" % (NOW - 30 * DAY)), 0, "rows are deleted even when the file is not compacted")

    def test_a_busy_database_is_not_a_failure(self):
        self.conn.executemany("INSERT INTO measurements(ts,avg_latency_ms) VALUES (?,?)", [(NOW - 300 * DAY + i, 1) for i in range(1500)])
        self.conn.commit()
        blocker = sqlite3.connect(self.db, isolation_level=None)
        self.addCleanup(blocker.close)
        blocker.execute("BEGIN IMMEDIATE")  # a writer (the probe loop) holds the lock: VACUUM must give up quietly
        try:
            with patch.object(npx, "CLEANUP_LOCK_TIMEOUT", 0.2):
                self.assertEqual(self.ext.compact(), False)
        finally:
            blocker.execute("ROLLBACK")
        self.assertEqual(self.ext.compact(), True, "and works once the lock is gone")

    def test_writers_are_not_blocked_while_cleaning(self):
        self.conn.executemany("INSERT INTO measurements(ts,avg_latency_ms) VALUES (?,?)", [(NOW - 300 * DAY + i, 1) for i in range(300)])
        self.conn.commit()
        failures, stop = [], threading.Event()

        def writer():
            writer_conn = sqlite3.connect(self.db, timeout=5)
            n = 0
            while not stop.is_set():
                try:
                    writer_conn.execute("INSERT INTO measurements(ts,avg_latency_ms) VALUES (?,2)", (NOW - n,))
                    writer_conn.commit()
                    n += 1
                    stop.wait(0.002)  # the real probe loop writes every ~30 s; this is a thousand times busier
                except sqlite3.Error as exc:
                    failures.append(str(exc))
            writer_conn.close()
        thread = threading.Thread(target=writer)
        thread.start()
        try:
            with patch.object(npx, "CLEANUP_BATCH", 25), patch.object(npx, "CLEANUP_PAUSE", 0.001), patch.object(npx, "CLEANUP_VACUUM_MIN", 10 ** 9):
                self.ext.cleanup(30)
        finally:
            stop.set()
            thread.join()
        self.assertEqual(failures, [])

    def test_the_result_is_remembered_for_the_page(self):
        self.ext.cleanup(30)
        last = self.ext.cleanup_state()["last"]
        self.assertEqual((last["ts"], last["days"]), (NOW, 30))
        self.assertGreater(last["total"], 0)
        info = self.client.get("/api/prefs").get_json()["history"]
        self.assertEqual(info["last"], last)
        self.assertGreater(info["db_bytes"], 0)
        self.assertEqual(info["oldest_ts"], self.conn.execute("SELECT MIN(ts) FROM np_targets").fetchone()[0])

    def test_a_target_without_a_state_row_does_not_break_the_page(self):
        self.conn.execute("DELETE FROM np_state")
        self.conn.execute("INSERT INTO np_targets(cycle_ts,ts,target,address,kind,latency,jitter,loss) VALUES (?,?,?,?,?,?,?,?)",
                          (NOW, NOW, "orphan.example", "192.0.2.1", "ping", 5.0, 0.5, 0.0))
        self.conn.commit()
        response = self.client.get("/api/targets/recent?range=3600")
        self.assertEqual(response.status_code, 200)
        row = next(r for r in response.get_json()["data"] if r["target"] == "orphan.example")
        self.assertEqual((row["state"], row["streak"]), ("UNKNOWN", 0))

    def test_missing_tables_are_skipped(self):
        self.conn.execute("DROP TABLE np_outages")
        self.conn.commit()
        result = self.ext.cleanup(30)
        self.assertNotIn("np_outages", result["deleted"])


class ApiTests(CleanupBase):
    def post(self, body, headers=HEADERS):
        return self.client.post("/api/history/cleanup", json=body, headers=headers)

    def test_a_real_deletion_needs_an_explicit_confirmation(self):
        for body in ({"days": 30}, {"days": 30, "dry_run": False}, {"days": 30, "confirm": False}, {"days": 30, "confirm": "yes"},
                     {"days": 30, "confirm": 1}):
            with self.subTest(body=body):
                self.assertEqual(self.post(body).status_code, 400)
        self.assertEqual(self.count("measurements"), len(AGES))
        done = self.post({"days": 30, "confirm": True})
        self.assertEqual(done.status_code, 200)
        self.assertEqual(done.get_json()["dry_run"], False)
        self.assertEqual(self.count("measurements"), 2)

    def test_preview_needs_no_confirmation(self):
        body = self.post({"days": 30, "dry_run": True}).get_json()
        self.assertEqual((body["dry_run"], body["deleted"]["measurements"]), (True, 4))
        self.assertEqual(self.count("measurements"), len(AGES))

    def test_invalid_requests_are_refused(self):
        for body in ({}, {"days": 5, "dry_run": True}, {"days": 0, "dry_run": True}, {"days": "30", "dry_run": True},
                     {"days": 30, "dry_run": "true"}, {"days": 30, "dry_run": True, "extra": 1}, [], "30"):
            with self.subTest(body=body):
                self.assertEqual(self.post(body).status_code, 400)
        self.assertEqual(self.client.post("/api/history/cleanup", data="not json", headers=HEADERS).status_code, 400)

    def test_only_same_origin_settings_requests_are_accepted(self):
        self.assertEqual(self.post({"days": 30, "confirm": True}, headers={}).status_code, 403)
        self.assertEqual(self.post({"days": 30, "confirm": True}, headers={**HEADERS, "Origin": "http://evil.example"}).status_code, 403)
        self.assertEqual(self.client.get("/api/history/cleanup").status_code, 405, "no deletion through GET")
        self.assertEqual(self.count("measurements"), len(AGES))

    def test_retention_is_a_choice_from_the_list(self):
        for bad in (1, 31, -7, "30", True, None, 30.0):
            self.assertEqual(self.client.post("/api/prefs", json={"history_retention_days": bad}, headers=HEADERS).status_code, 400)
        for good in (30, 0, 365):
            response = self.client.post("/api/prefs", json={"history_retention_days": good}, headers=HEADERS)
            self.assertEqual(response.get_json()["prefs"]["history_retention_days"], good)
        self.assertEqual(self.client.get("/api/settings").get_json()["storage"]["retention_days"], 365)
        self.client.post("/api/prefs", json={"history_retention_days": 0}, headers=HEADERS)
        self.assertIsNone(self.client.get("/api/settings").get_json()["storage"]["retention_days"])
        self.assertEqual(self.count("measurements"), len(AGES), "choosing a period deletes nothing by itself")


class NightlyTests(CleanupBase):
    def choose(self, days):
        self.ext.update_prefs({"history_retention_days": days})

    def test_default_is_to_keep_everything(self):
        self.assertEqual(self.ext.prefs()["history_retention_days"], 0)
        for hour in range(24):
            self.assertIsNone(self.ext.maintenance_tick(at(hour, day=11)))
        self.assertIsNone(self.ext.next_cleanup())
        self.assertEqual(self.count("measurements"), len(AGES))

    def test_nothing_happens_at_the_moment_it_is_switched_on(self):
        self.now = at(4)  # even inside the quiet window
        self.choose(30)
        for offset in (0, 600, 3600, 3 * 3600 - 1):
            self.assertIsNone(self.ext.maintenance_tick(self.now + offset))
        self.assertEqual(self.count("measurements"), len(AGES), "switching the option on deletes nothing")
        self.assertIsNotNone(self.ext.maintenance_tick(at(3, day=11)), "the next quiet night does")

    def test_it_runs_in_the_quiet_hours_after_the_option_was_chosen(self):
        self.now = at(14)
        self.choose(30)
        for hour in (15, 20, 23):
            self.assertIsNone(self.ext.maintenance_tick(at(hour)), "daytime: no")
        self.assertIsNone(self.ext.maintenance_tick(at(2, day=11)), "02:xx is before the window")
        result = self.ext.maintenance_tick(at(3, day=11))
        self.assertIsNotNone(result)
        self.assertEqual(result["days"], 30)
        self.assertEqual(self.count("measurements", "ts < %d" % (at(3, day=11) - 30 * DAY)), 0)

    def test_once_per_night_not_every_ten_minutes(self):
        self.now = at(14)
        self.choose(30)
        self.assertIsNotNone(self.ext.maintenance_tick(at(3, day=11)))
        self.now = at(3, day=11)
        for minute_offset in (600, 1200, 3600, 7200):
            self.assertIsNone(self.ext.maintenance_tick(at(3, day=11) + minute_offset), "already done tonight")
        self.assertIsNotNone(self.ext.maintenance_tick(at(3, day=12) + 60), "and again the next night")

    def test_a_missed_night_is_made_up_after_two_days_at_any_hour(self):
        self.now = at(14)
        self.choose(30)
        self.assertIsNone(self.ext.maintenance_tick(at(15, day=11)))
        self.assertIsNone(self.ext.maintenance_tick(at(13, day=11) + DAY // 2))
        self.assertIsNotNone(self.ext.maintenance_tick(at(14, day=12)), "two days without a cleanup")

    def test_next_cleanup_is_reported_for_the_page(self):
        self.now = at(14)
        self.choose(30)
        self.assertEqual(self.ext.next_cleanup(), at(3, day=11))
        info = self.client.get("/api/prefs").get_json()["history"]
        self.assertEqual(info["next_ts"], at(3, day=11))
        self.now = at(4, day=11)
        self.assertEqual(self.ext.next_cleanup(), at(4, day=11), "inside the window it starts at once")

    def test_a_damaged_preferences_file_never_deletes(self):
        self.now = at(14)
        self.choose(30)
        self.ext.prefs_path.write_text("{broken")
        self.assertIsNone(self.ext.maintenance_tick(at(3, day=11)))
        self.assertEqual(self.count("measurements"), len(AGES))

    def test_the_thread_survives_errors(self):
        self.now = at(14)
        self.choose(30)
        calls = []

        def boom(*args, **kwargs):
            calls.append(1)
            raise sqlite3.OperationalError("database is locked")
        with patch.object(self.ext, "maintenance_tick", side_effect=boom):
            thread = self.ext.start_maintenance(sleep=lambda s: None, rounds=3)
            thread.join(5)
        self.assertEqual(len(calls), 3)


class CommandLineTests(CleanupBase):
    def run_cli(self, *args):
        env = dict(os.environ, DB_PATH=str(self.db), APP_TIMEZONE="UTC")
        return subprocess.run([sys.executable, str(ROOT / "src/netprobe_extensions.py"), *args], capture_output=True, text=True, env=env, timeout=60)

    def test_dry_run_prints_what_would_go_and_touches_nothing(self):
        before = self.total()
        done = self.run_cli("cleanup", "--days", "30", "--dry-run")
        self.assertEqual(done.returncode, 0, done.stderr)
        report = json.loads(done.stdout)
        self.assertTrue(report["dry_run"])
        self.assertGreater(report["total"], 0)
        self.assertEqual(self.total(), before)

    def test_bad_range_is_refused(self):
        done = self.run_cli("cleanup", "--days", "31", "--dry-run")
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(self.count("measurements"), len(AGES))


if __name__ == "__main__":
    unittest.main()
