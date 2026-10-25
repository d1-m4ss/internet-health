"""Database backups: a consistent copy while the monitor keeps writing, the newest few kept, listed and downloadable."""
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
import netprobe_extensions as npx  # noqa: E402
from fixture_db import make_database  # noqa: E402

HEADERS = {"X-NetProbe-Request": "settings"}
NOW = 1_800_000_000


class BackupBase(unittest.TestCase):
    def setUp(self):
        from flask import Flask
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "netprobe.sqlite"
        make_database(self.db)
        self.now = NOW
        self.ext = npx.Extension(str(self.db), str(self.root), clock=lambda: self.now)
        app = Flask(__name__)
        npx.install_routes(app, self.ext)
        self.client = app.test_client()

    def backup(self):
        self.assertTrue(self.ext.start_backup(wait=True))
        status = self.ext.backup_status()
        self.assertIsNone(status["error"], status)
        return status["name"]


class CreationTests(BackupBase):
    def test_a_backup_is_a_complete_private_copy(self):
        with self.ext.db() as conn:
            self.ext.queue(conn, NOW, "alert", "subject", "description", "message")
        name = self.backup()
        self.assertRegex(name, r"^netprobe-\d{8}-\d{6}\.sqlite$")
        path = self.root / "db-backups" / name
        self.assertEqual(oct(path.stat().st_mode & 0o777), "0o600")
        self.assertEqual(oct((self.root / "db-backups").stat().st_mode & 0o777), "0o700")
        copy = sqlite3.connect(path)
        self.addCleanup(copy.close)
        self.assertEqual(copy.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        for table in ("speedtests", "measurements", "np_targets", "np_notifications"):
            self.assertEqual(copy.execute("SELECT COUNT(*) FROM " + table).fetchone(), sqlite3.connect(self.db).execute("SELECT COUNT(*) FROM " + table).fetchone(), table)
        self.assertEqual([f.name for f in (self.root / "db-backups").iterdir()], [name], "no partial files are left")

    def test_the_copy_is_consistent_while_another_connection_keeps_writing(self):
        stop = threading.Event()

        def writer():
            conn = sqlite3.connect(self.db, timeout=30)
            i = 0
            while not stop.is_set():
                conn.execute("INSERT INTO np_notifications(ts,level,subject,message) VALUES (?,?,?,?)", (NOW + 1000 + i, "normal", "w", "w"))
                conn.commit()
                i += 1
            conn.close()
        thread = threading.Thread(target=writer)
        thread.start()
        try:
            name = self.backup()
        finally:
            stop.set()
            thread.join()
        copy = sqlite3.connect(self.root / "db-backups" / name)
        self.addCleanup(copy.close)
        self.assertEqual(copy.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_only_the_newest_copies_are_kept(self):
        names = []
        for i in range(npx.BACKUP_KEEP + 2):
            self.now = NOW + i * 60
            names.append(self.backup())
        left = [b["name"] for b in self.ext.backups()]
        self.assertEqual(left, sorted(names, reverse=True)[:npx.BACKUP_KEEP])
        self.assertNotIn(names[0], left)

    def test_a_second_request_while_one_runs_is_refused(self):
        self.ext.backup_lock.acquire()
        self.addCleanup(lambda: self.ext.backup_lock.locked() and self.ext.backup_lock.release())
        self.assertFalse(self.ext.start_backup())
        response = self.client.post("/api/backups", headers=HEADERS)
        self.assertEqual((response.status_code, response.get_json()["error"]), (409, "A backup is already running"))

    def test_not_enough_disk_space_is_said_and_nothing_is_started(self):
        with patch.object(self.ext, "_free_bytes", return_value=1024):
            response = self.client.post("/api/backups", headers=HEADERS)
        self.assertEqual(response.status_code, 507)
        self.assertIn("Not enough free space", response.get_json()["error"])
        self.assertEqual(self.ext.backups(), [])
        self.assertFalse(self.ext.backup_status()["running"])

    def test_a_failure_is_reported_and_the_next_backup_can_start(self):
        with patch.object(self.ext, "db_path", str(self.root / "missing" / "x.sqlite")):
            self.assertTrue(self.ext.start_backup(wait=True))
        status = self.ext.backup_status()
        self.assertEqual((status["running"], status["name"]), (False, None))
        self.assertIn("unable to open database file", status["error"])
        self.assertEqual([f for f in (self.root / "db-backups").iterdir()], [], "no partial file")
        self.assertTrue(self.ext.start_backup(wait=True))
        self.assertIsNone(self.ext.backup_status()["error"])


class ApiTests(BackupBase):
    def test_create_list_download_delete(self):
        self.assertEqual(self.client.post("/api/backups").status_code, 403, "same-origin header needed")
        response = self.client.post("/api/backups", headers=HEADERS)
        self.assertEqual(response.status_code, 202)
        for _ in range(100):
            if not self.client.get("/api/backups").get_json()["running"]:
                break
            threading.Event().wait(0.05)
        status = self.client.get("/api/backups").get_json()
        self.assertEqual((status["running"], status["error"], status["keep"]), (False, None, npx.BACKUP_KEEP))
        (item,) = status["backups"]
        self.assertEqual(set(item), {"name", "ts", "bytes"})
        self.assertGreater(item["bytes"], 1000)
        self.assertIn("db_bytes", status)
        self.assertEqual(self.client.get("/api/backups").headers["Cache-Control"], "no-store")
        download = self.client.get("/api/backups/" + item["name"])
        self.assertEqual(download.status_code, 200)
        self.assertIn("attachment", download.headers["Content-Disposition"])
        self.assertIn(item["name"], download.headers["Content-Disposition"])
        self.assertTrue(download.data.startswith(b"SQLite format 3\x00"))
        self.assertEqual(len(download.data), item["bytes"])
        self.assertEqual(self.client.post("/api/backups/%s/delete" % item["name"]).status_code, 403)
        gone = self.client.post("/api/backups/%s/delete" % item["name"], headers=HEADERS)
        self.assertEqual((gone.status_code, gone.get_json()["backups"]), (200, []))
        self.assertEqual(self.client.get("/api/backups/" + item["name"]).status_code, 404)
        self.assertEqual(self.client.post("/api/backups/%s/delete" % item["name"], headers=HEADERS).status_code, 404)

    def test_only_backup_names_are_served_or_deleted(self):
        outside = self.root / "secret.sqlite"
        outside.write_bytes(b"secret")
        (self.root / "db-backups").mkdir()
        (self.root / "db-backups" / "netprobe-20260101-000000.sqlite").symlink_to(outside)
        for name in ("..%2Fsecret.sqlite", "secret.sqlite", "netprobe-1.sqlite", "netprobe-20260101-000000.sqlite.bak", "%2e%2e"):
            self.assertIn(self.client.get("/api/backups/" + name).status_code, (400, 404), name)
            self.assertIn(self.client.post("/api/backups/%s/delete" % name, headers=HEADERS).status_code, (400, 404), name)
        self.assertEqual(self.client.get("/api/backups/netprobe-20260101-000000.sqlite").status_code, 404, "a symlink is not served")
        self.assertEqual(self.ext.backups(), [], "nor listed")
        self.assertEqual(outside.read_bytes(), b"secret")


if __name__ == "__main__":
    unittest.main()
