"""internet-health-ookla: the optional installer of the official Ookla CLI (against a local fake download server)."""
import importlib.machinery
import importlib.util
import io
import os
import platform
import subprocess
import sys
import tarfile
import tempfile
import threading
import unittest
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
from fixture_db import make_database  # noqa: E402
SCRIPT = ROOT / "src" / "internet-health-ookla"
FAKE_BINARY = b"#!/bin/sh\necho fake ookla speedtest\n"


def load_helper(env):
    with patch.dict(os.environ, env):
        loader = importlib.machinery.SourceFileLoader("ookla_helper", str(SCRIPT))
        spec = importlib.util.spec_from_loader("ookla_helper", loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
    return module


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class OoklaInstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.data = root / "data"
        self.data.mkdir()
        self.web = root / "web"
        self.web.mkdir()
        arch = {"x86_64": "x86_64", "amd64": "x86_64", "arm64": "aarch64", "aarch64": "aarch64"}.get(platform.machine().lower(), "x86_64")
        self.archive = self.web / f"ookla-speedtest-1.2.0-linux-{arch}.tgz"
        self.make_archive({"speedtest": FAKE_BINARY, "speedtest.5": b"man page", "speedtest.md": b"readme"})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(self.web)))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.env = {"DB_PATH": str(self.data / "netprobe.sqlite"), "OOKLA_DOWNLOAD_BASE": "http://127.0.0.1:%d" % self.server.server_address[1],
                    "SPEEDTEST_OOKLA_ACCEPT_LICENSE": "", "SPEEDTEST_OOKLA_PATH": "", "SPEEDTEST_OOKLA_ACCEPTANCE_FILE": ""}

    def make_archive(self, files):
        with tarfile.open(self.archive, "w:gz") as tar:
            for name, content in files.items():
                info = tarfile.TarInfo(name)
                info.size = len(content)
                info.mode = 0o644
                tar.addfile(info, io.BytesIO(content))

    def run_helper(self, *args, env=None):
        return subprocess.run([sys.executable, str(SCRIPT), *args], env={**os.environ, **self.env, **(env or {})},
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)

    def test_without_acceptance_nothing_is_downloaded(self):
        result = self.run_helper("install")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("https://www.speedtest.net/about/eula", result.stdout)
        self.assertIn("interactive terminal", result.stderr)
        self.assertFalse((self.data / "ookla").exists())
        self.assertFalse((self.data / "ookla-eula-accepted.txt").exists())

    def test_accepting_by_variable_installs_only_the_binary_into_the_data_folder(self):
        result = self.run_helper("install", env={"SPEEDTEST_OOKLA_ACCEPT_LICENSE": "I_ACCEPT"})
        self.assertEqual(result.returncode, 0, result.stderr)
        binary = self.data / "ookla" / "speedtest"
        self.assertEqual(binary.read_bytes(), FAKE_BINARY)
        self.assertTrue(os.access(binary, os.X_OK))
        self.assertEqual(sorted(p.name for p in (self.data / "ookla").iterdir()), ["speedtest"], "the man page and readme are not extracted")
        self.assertRegex(result.stdout, r"sha256 [0-9a-f]{64}")
        self.assertEqual(subprocess.run([str(binary)], capture_output=True, text=True).stdout.strip(), "fake ookla speedtest")
        self.assertEqual(self.run_helper("status", env={"SPEEDTEST_OOKLA_ACCEPT_LICENSE": "I_ACCEPT"}).returncode, 0)

    def test_interactive_acceptance_is_recorded_in_the_data_folder(self):
        helper = load_helper(self.env)
        with patch.object(helper.sys.stdin, "isatty", return_value=True, create=True), \
             patch("builtins.input", return_value="I ACCEPT"), patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(helper.install(), 0)
        record = (self.data / "ookla-eula-accepted.txt").read_text().splitlines()
        self.assertEqual(record[0], "I_ACCEPT")
        self.assertTrue(record[1].startswith("accepted_at_utc="))
        self.assertEqual(oct((self.data / "ookla-eula-accepted.txt").stat().st_mode & 0o777), "0o600")
        self.assertTrue((self.data / "ookla" / "speedtest").exists())
        # a later run needs no question
        self.assertEqual(self.run_helper("install").returncode, 0)

    def test_a_wrong_answer_installs_nothing(self):
        helper = load_helper(self.env)
        with patch.object(helper.sys.stdin, "isatty", return_value=True, create=True), \
             patch("builtins.input", return_value="yes"), patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(helper.install(), 1)
        self.assertFalse((self.data / "ookla").exists())
        self.assertFalse((self.data / "ookla-eula-accepted.txt").exists())

    def test_an_archive_without_the_binary_is_refused(self):
        self.make_archive({"readme.md": b"nothing here", "sub/speedtest": b"nested is not the program"})
        result = self.run_helper("install", env={"SPEEDTEST_OOKLA_ACCEPT_LICENSE": "I_ACCEPT"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not contain a speedtest binary", result.stderr)
        self.assertFalse((self.data / "ookla" / "speedtest").exists())

    def test_remove_deletes_the_binary_and_keeps_the_acceptance(self):
        self.run_helper("install", env={"SPEEDTEST_OOKLA_ACCEPT_LICENSE": "I_ACCEPT"})
        helper = load_helper(self.env)
        helper.record_acceptance()
        with patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(helper.remove(), 0)
        self.assertFalse((self.data / "ookla").exists())
        self.assertTrue((self.data / "ookla-eula-accepted.txt").exists())

    def test_the_app_finds_what_the_helper_installed_and_its_acceptance(self):
        make_database(self.data / "netprobe.sqlite")
        env = dict(self.env, SITES="google.com", SPEEDTEST_ENABLED="false")
        with patch.dict(os.environ, env):
            spec = importlib.util.spec_from_file_location("tested_ookla_app", ROOT / "src/app.py")
            app = importlib.util.module_from_spec(spec)
            with patch("threading.Thread.start"):
                spec.loader.exec_module(app)
        self.assertEqual(app.SPEEDTEST_OOKLA_PATH, str(self.data / "ookla" / "speedtest"))
        self.assertFalse(app.ookla_binary_available())
        self.assertEqual(app.ookla_acceptance_status(), (False, None))
        self.run_helper("install", env={"SPEEDTEST_OOKLA_ACCEPT_LICENSE": "I_ACCEPT"})
        helper = load_helper(self.env)
        helper.record_acceptance()
        self.assertTrue(app.ookla_binary_available())
        self.assertEqual(app.ookla_acceptance_status(), (True, "persistent-file"))

    def test_what_root_creates_is_handed_to_the_owner_of_the_data_folder(self):
        """docker exec runs as root, the app as an unprivileged user: it must be able to read the acceptance and run the binary."""
        helper = load_helper(self.env)
        owner = os.stat(self.data)
        chowned = []
        with patch.object(helper.os, "geteuid", return_value=0), \
             patch.object(helper.os, "chown", side_effect=lambda path, uid, gid: chowned.append((Path(path).name, uid, gid))), \
             patch.dict(os.environ, {"SPEEDTEST_OOKLA_ACCEPT_LICENSE": "I_ACCEPT"}), patch("sys.stdout", new_callable=io.StringIO):
            helper.record_acceptance()
            self.assertEqual(helper.install(), 0)
        names = [name for name, _uid, _gid in chowned]
        self.assertEqual(names, ["ookla-eula-accepted.txt", "ookla", "speedtest"])
        self.assertTrue(all((uid, gid) == (owner.st_uid, owner.st_gid) for _n, uid, gid in chowned))

    def test_a_non_root_run_changes_no_owner(self):
        helper = load_helper(self.env)
        with patch.object(helper.os, "geteuid", return_value=1000), patch.object(helper.os, "chown") as chown:
            helper.hand_over(str(self.data))
        chown.assert_not_called()


if __name__ == "__main__":
    unittest.main()
