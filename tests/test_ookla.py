"""The Ookla service: the official CLI downloaded on first use (from a local fake of install.speedtest.net), run and parsed."""
import importlib.util
import io
import json
import os
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
import netprobe_extensions as npx  # noqa: E402

# what `speedtest --format=json` prints (abridged from the CLI 1.2.0 output; invented values)
RESULT = {"type": "result", "timestamp": "2026-10-26T15:00:00Z",
          "ping": {"jitter": 0.8, "latency": 6.2, "low": 5.9, "high": 7.4},
          "download": {"bandwidth": 117_000_000, "bytes": 1_000_000_000, "elapsed": 8000,
                       "latency": {"iqm": 21.5, "low": 6.0, "high": 80.0, "jitter": 3.1}},
          "upload": {"bandwidth": 62_500_000, "bytes": 500_000_000, "elapsed": 8000,
                     "latency": {"iqm": 34.0, "low": 6.0, "high": 120.0, "jitter": 5.0}},
          "packetLoss": 0.5, "isp": "Example ISP",
          "server": {"id": 12345, "host": "speedtest.example.net", "port": 8080, "name": "Example Net", "location": "Example City",
                     "country": "Exampleland", "ip": "192.0.2.10"},
          "result": {"id": "abc", "url": "https://www.speedtest.net/result/c/abc", "persisted": True}}


def fake_cli(path, stdout="", stderr="", code=0, sleep=0):
    """A stand-in for the Ookla CLI: records its arguments, prints what it is told and exits with the code."""
    path.write_text("#!/bin/sh\necho \"$@\" > \"$0.args\"\n%s" % ("sleep %s\n" % sleep if sleep else "")
                    + "cat <<'OUT'\n%s\nOUT\ncat >&2 <<'ERR'\n%s\nERR\nexit %d\n" % (stdout, stderr, code))
    path.chmod(0o755)
    return path


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class DownloadTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.data = self.root / "data"
        self.data.mkdir()
        self.web = self.root / "web"
        self.web.mkdir()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(self.web)))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]

    def archive(self, files, arch="x86_64"):
        with tarfile.open(self.web / f"ookla-speedtest-1.2.0-linux-{arch}.tgz", "w:gz") as tar:
            for name, content in files.items():
                info = tarfile.TarInfo(name)
                info.size = len(content)
                tar.addfile(info, io.BytesIO(content))

    def test_first_use_downloads_only_the_program_into_the_data_folder_and_later_uses_it(self):
        self.archive({"speedtest": b"#!/bin/sh\necho ok\n", "speedtest.5": b"man", "speedtest.md": b"readme"})
        path = npx.ookla_binary(self.data, base=self.base, machine="x86_64")
        self.assertEqual(path, self.data / "ookla" / "speedtest")
        self.assertTrue(os.access(path, os.X_OK))
        self.assertEqual(sorted(p.name for p in path.parent.iterdir()), ["speedtest"])
        (self.web / "ookla-speedtest-1.2.0-linux-x86_64.tgz").unlink()
        self.assertEqual(npx.ookla_binary(self.data, base=self.base, machine="x86_64"), path, "no second download")

    def test_the_architecture_picks_the_archive(self):
        self.archive({"speedtest": b"arm"}, arch="aarch64")
        self.assertEqual(npx.ookla_binary(self.data, base=self.base, machine="aarch64").read_bytes(), b"arm")

    def test_failures_are_readable_and_leave_nothing_behind(self):
        with self.assertRaises(npx.FastTestError) as caught:
            npx.ookla_binary(self.data, base=self.base, machine="x86_64")
        self.assertIn("could not be downloaded", str(caught.exception))
        self.archive({"sub/speedtest": b"nested", "readme": b"x"})
        with self.assertRaises(npx.FastTestError) as caught:
            npx.ookla_binary(self.data, base=self.base, machine="x86_64")
        self.assertIn("does not contain the speedtest program", str(caught.exception))
        with self.assertRaises(npx.FastTestError) as caught:
            npx.ookla_binary(self.data, base=self.base, machine="mips")
        self.assertIn("no Speedtest CLI for the mips architecture", str(caught.exception))
        self.assertFalse((self.data / "ookla" / "speedtest").exists())


class RunTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def test_the_result_is_shaped_like_the_other_services(self):
        cli = fake_cli(self.dir / "speedtest", stdout=json.dumps(RESULT))
        result = npx.run_ookla_test(cli)
        self.assertEqual((cli.parent / "speedtest.args").read_text().split(), ["--accept-license", "--accept-gdpr", "--format=json", "--progress=no"])
        self.assertEqual((result["ping_ms"], result["jitter_ms"], result["packet_loss_pct"]), (6.2, 0.8, 0.5))
        self.assertEqual((result["download_mbps"], result["upload_mbps"]), (936.0, 500.0), "bytes per second -> decimal Mbit/s")
        self.assertEqual((result["loaded_ping_ms"], result["loaded_up_ping_ms"]), (21.5, 34.0), "the latency while downloading / uploading")
        self.assertEqual(result["server"], {"id": "12345", "name": "Example Net, Example City", "host": "speedtest.example.net", "country": "Exampleland"})
        self.assertEqual((result["isp"], result["result_url"], result["protocol"]), ("Example ISP", "https://www.speedtest.net/result/c/abc", "ookla"))

    def test_log_lines_around_the_result_are_ignored(self):
        lines = [json.dumps({"type": "log", "level": "info", "message": "Server selected"}), json.dumps(RESULT)]
        self.assertEqual(npx.run_ookla_test(fake_cli(self.dir / "speedtest", stdout="\n".join(lines)))["download_mbps"], 936.0)

    def test_missing_parts_are_unknown_not_zero(self):
        partial_result = {"type": "result", "download": {"bandwidth": 10_000_000}, "upload": {"bandwidth": 0}, "server": {}}
        result = npx.run_ookla_test(fake_cli(self.dir / "speedtest", stdout=json.dumps(partial_result)))
        self.assertEqual((result["download_mbps"], result["upload_mbps"], result["ping_ms"], result["loaded_ping_ms"], result["packet_loss_pct"]),
                         (80.0, None, None, None, None))
        self.assertEqual(result["server"]["name"], "Ookla")

    def test_errors_carry_the_cli_message(self):
        error = json.dumps({"type": "log", "level": "error", "message": "Configuration - Couldn't resolve host name"})
        with self.assertRaises(npx.FastTestError) as caught:
            npx.run_ookla_test(fake_cli(self.dir / "speedtest", stderr=error, code=2))
        self.assertEqual(str(caught.exception), "The Ookla test failed: Configuration - Couldn't resolve host name")
        with self.assertRaises(npx.FastTestError) as caught:
            npx.run_ookla_test(fake_cli(self.dir / "speedtest", stdout=json.dumps({"type": "result", "download": {}})))
        self.assertEqual(str(caught.exception), "The Ookla test returned no download speed")
        with self.assertRaises(npx.FastTestError) as caught:
            npx.run_ookla_test(fake_cli(self.dir / "speedtest", sleep=3), timeout=1)
        self.assertEqual(str(caught.exception), "The Ookla test did not finish within 1 s")
        with self.assertRaises(npx.FastTestError) as caught:
            npx.run_ookla_test(self.dir / "missing")
        self.assertIn("could not be started", str(caught.exception))


class WiringTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name)
        make_database(self.data / "netprobe.sqlite")
        env = {"DB_PATH": str(self.data / "netprobe.sqlite"), "SPEEDTEST_ENABLED": "false", "SITES": "google.com",
               "DNS_NAMESERVER_1_IP": "8.8.8.8", "DNS_NAMESERVER_2_IP": "", "DNS_NAMESERVER_3_IP": "", "DNS_NAMESERVER_4_IP": ""}
        env_patch = patch.dict(os.environ, env)
        env_patch.start()
        self.addCleanup(env_patch.stop)
        spec = importlib.util.spec_from_file_location("tested_ookla_app", ROOT / "src/app.py")
        self.module = importlib.util.module_from_spec(spec)
        with patch("threading.Thread.start"):
            spec.loader.exec_module(self.module)
        self.client = self.module.app.test_client()

    def test_a_chosen_ookla_runs_the_downloaded_program_and_stores_everything(self):
        self.assertEqual(self.client.get("/api/config").get_json()["speedtest_backends_available"]["ookla"], True)
        self.assertEqual(self.client.post("/api/prefs", json={"speedtest_service": "ookla"}, headers={"X-NetProbe-Request": "settings"}).status_code, 200)
        cli = fake_cli(self.data / "fake-speedtest", stdout=json.dumps(RESULT))
        with patch.object(npx, "ookla_binary", return_value=cli) as binary:
            result = self.module.run_speedtest_internal()
        binary.assert_called_once_with(self.module.custom_extension.data_dir)
        self.assertEqual((result["backend"], result["download_mbps"], result["packet_loss_pct"]), ("ookla", 936.0, 0.5))
        series = self.client.get("/api/speedtest/series?range=all").get_json()["tests"][-1]
        self.assertEqual((series["backend"], series["jitter_ms"], series["loaded_down_ms"], series["loaded_up_ms"], series["packet_loss_pct"]),
                         ("ookla", 0.8, 21.5, 34.0, 0.5))
        history = self.client.get("/api/speedtest/history?limit=1").get_json()["tests"][-1]
        self.assertEqual((history["server_name"], history["server_id"]), ("Example Net, Example City", "12345"))

    def test_the_names_for_it_and_the_notifications(self):
        for name in ("ookla", "Official", "speedtest"):
            self.assertEqual(self.module.normalize_speedtest_backend(name), "ookla", name)
        self.assertEqual(npx.SPEED_NAMES["ookla"], "Ookla")


if __name__ == "__main__":
    unittest.main()
