"""SQLite runs in WAL mode (one log append per transaction instead of a rollback journal + two fsyncs) with synchronous=NORMAL."""
import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
from test_responsiveness import AppTestCase  # noqa: E402
import netprobe_extensions as npx  # noqa: E402


class WalTests(AppTestCase):
    def mode(self):
        c = sqlite3.connect(self.db)
        try:
            return c.execute("PRAGMA journal_mode").fetchone()[0]
        finally:
            c.close()

    def to_rollback_journal(self):
        c = sqlite3.connect(self.db)
        try:
            self.assertEqual(c.execute("PRAGMA journal_mode=DELETE").fetchone()[0], "delete")
        finally:
            c.close()

    def test_existing_rollback_journal_database_is_switched_to_wal(self):
        self.to_rollback_journal()  # a database written by an earlier version
        self.assertEqual(self.mode(), "delete")
        self.module.ensure_db()
        self.assertEqual(self.mode(), "wal")
        self.module.ensure_db()  # idempotent
        self.assertEqual(self.mode(), "wal")

    def test_connections_use_synchronous_normal(self):
        self.module.ensure_db()
        conn = self.module.get_db_connection()
        try:
            self.assertEqual(conn.execute("PRAGMA synchronous").fetchone()[0], 1)  # 1 = NORMAL
        finally:
            conn.close()
        ext = npx.Extension(str(self.db), str(self.db.parent))
        with ext.db() as conn:
            self.assertEqual(conn.execute("PRAGMA synchronous").fetchone()[0], 1)

    def test_writes_and_backup_work_in_wal(self):
        self.module.ensure_db()
        conn = self.module.get_db_connection()
        try:
            before = conn.execute("SELECT COUNT(*) FROM measurements").fetchone()[0]
        finally:
            conn.close()
        copy = self.db.parent / "copy.sqlite"
        c = sqlite3.connect(self.db)
        try:
            c.execute("VACUUM INTO ?", (str(copy),))
        finally:
            c.close()
        c2 = sqlite3.connect(copy)
        try:
            self.assertEqual(c2.execute("SELECT COUNT(*) FROM measurements").fetchone()[0], before)
            self.assertEqual(c2.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        finally:
            c2.close()


if __name__ == "__main__":
    unittest.main()
