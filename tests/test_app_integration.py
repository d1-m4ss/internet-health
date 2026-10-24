"""Run the real app loop on an isolated copy; only external probes are replaced."""
import importlib.util
import io
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
from fixture_db import make_database  # noqa: E402


class AppIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue((ROOT / "src/app.py").exists(), "Installed app has no persistent-history integration yet")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "netprobe.sqlite"
        make_database(self.db)
        env = {"DB_PATH": str(self.db), "DB_ENGINE": "sqlite", "USE_POSTGRES": "false",
               "PROBE_INTERVAL": "30", "PING_COUNT": "4", "SPEEDTEST_ENABLED": "false",
               "ROUTER_IP": "192.168.1.1", "SITES": "google.com", "DNS_NAMESERVER_1_IP": "8.8.8.8",
               "DNS_NAMESERVER_2_IP": "", "DNS_NAMESERVER_3_IP": "", "DNS_NAMESERVER_4_IP": ""}
        self.env = patch.dict(os.environ, env)
        self.env.start()
        self.addCleanup(self.env.stop)
        spec = importlib.util.spec_from_file_location("tested_live_app", ROOT / "src/app.py")
        self.module = importlib.util.module_from_spec(spec)
        with patch("threading.Thread.start"):
            spec.loader.exec_module(self.module)

    def test_real_probe_cycle_persists_target_history_without_changing_cadence(self):
        ping_output = "PING google.com (142.250.1.1) 56(84) bytes of data.\n4 packets transmitted, 3 received, 25% packet loss, time 3000ms\nrtt min/avg/max/mdev = 20.0/25.0/30.0/1.0 ms\n"
        calls = []
        def external_run(args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0, stdout=ping_output, stderr="")
        class EndCycle(Exception):
            pass
        with patch.object(self.module, "get_default_gateway", return_value="172.17.0.1"), \
             patch.object(self.module.subprocess, "run", side_effect=external_run), \
             patch.object(self.module, "measure_dns_latency_multi", return_value=2.0), \
        patch.object(self.module, "get_cached_wan_info",
                     return_value={"public_ip": "9.9.9.9", "isp": "Sample ISP"}), \
        patch.object(self.module.time, "sleep", side_effect=EndCycle) as sleep:
            with self.assertRaises(EndCycle):
                self.module.probe_loop()
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(cmd[1:4] == ["-q", "-c", "4"] for cmd in calls))
        sleep.assert_called_once_with(30)
        data = self.module.app.test_client().get("/api/targets/recent").get_json()["data"]
        google = next(r for r in data if r["target"] == "google.com")
        self.assertEqual((google["address"], google["loss"], google["latency"]), ("142.250.1.1", 25, 25))
        self.assertEqual((google["wan_ip"], google["wan_isp"]), ("9.9.9.9", "Sample ISP"))
        self.assertEqual(google["wan_sources"][0]["samples"], 1)
        self.assertEqual(len(data), 4)
        self.assertEqual(self.module.app.test_client().get("/api/score/latest").status_code, 200)
        conn = sqlite3.connect(self.db)
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute("PRAGMA quick_check").fetchone()[0], "ok")

    def test_timeout_keeps_actual_ping_destination_but_no_synthetic_latency(self):
        output = b"PING google.com (142.250.1.1) 56(84) bytes of data.\n"
        with patch.object(self.module.subprocess, "run", side_effect=subprocess.TimeoutExpired(["ping"], 8, output=output)):
            result = self.module.run_ping("google.com", 4)
        self.assertEqual(result["address"], "142.250.1.1")
        self.assertFalse(result["measured"])
        self.module.custom_extension.record_probe(2_000_000_000, [result], {})
        conn = sqlite3.connect(self.db)
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute("SELECT latency,jitter FROM np_targets").fetchone(), (None, None))


    def probe_dns_cycle(self, behaviours):
        """One real probe cycle where only the resolver object is faked (per-server behaviour)."""
        import dns.resolver
        module = self.module

        class FakeResolver:
            def __init__(self, configure=False):
                self.nameservers = []

            def resolve(self, domain, rdtype, lifetime=None):
                action = behaviours[self.nameservers[0]]
                if action != "ok":
                    raise action()

        ping_output = "PING google.com (142.250.1.1) 56(84) bytes of data.\n4 packets transmitted, 4 received, 0% packet loss, time 3000ms\nrtt min/avg/max/mdev = 20.0/25.0/30.0/1.0 ms\n"
        class EndCycle(Exception):
            pass
        with patch.object(module, "get_default_gateway", return_value="172.17.0.1"), \
             patch.object(module, "DNS_SERVERS", list(behaviours)), \
             patch.object(module.dns.resolver, "Resolver", FakeResolver), \
             patch.object(module.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout=ping_output, stderr="")), \
             patch.object(module.time, "sleep", side_effect=EndCycle):
            with self.assertRaises(EndCycle):
                module.probe_loop()
        conn = sqlite3.connect(self.db)
        self.addCleanup(conn.close)
        return dict(conn.execute("SELECT target,latency IS NULL FROM np_targets WHERE kind='dns'").fetchall())

    def test_dns_servers_that_do_not_answer_are_recorded_as_unavailable_in_production_path(self):
        import dns.resolver
        rows = self.probe_dns_cycle({"8.8.8.8": "ok", "192.0.2.9": dns.resolver.NoNameservers,
                                     "192.0.2.10": TimeoutError})
        self.assertEqual(rows, {"dns:8.8.8.8": 0, "dns:192.0.2.9": 1, "dns:192.0.2.10": 1})

    def test_dns_server_that_answers_with_nxdomain_is_alive(self):
        import dns.resolver
        rows = self.probe_dns_cycle({"8.8.8.8": dns.resolver.NXDOMAIN})
        self.assertEqual(rows, {"dns:8.8.8.8": 0})


    def test_router_name_is_configurable_and_used_in_config_and_notifications(self):
        module = self.module
        self.assertEqual(module.ROUTER_NAME, "Router", "default name")
        with patch.object(module, "ROUTER_NAME", "ASUS RT-AX86U"):
            self.assertEqual(module.app.test_client().get("/api/config").get_json()["router_name"], "ASUS RT-AX86U")
            roles = module.notification_roles("172.17.0.1")
            self.assertEqual(roles[module.ROUTER_IP], ("router", "ASUS RT-AX86U"))

    def test_wan_info_endpoint_returns_public_ip_and_isp_and_caches_lookup(self):
        module = self.module
        module._wan_info_cache.update(expires_at=0, data=None, error=None)
        provider_response = io.BytesIO(
            b'{"success":true,"ip":"8.8.8.8","connection":{"isp":"Example ISP"}}'
        )
        with patch.object(module, "urlopen", return_value=provider_response) as lookup:
            client = module.app.test_client()
            first = client.get("/api/wan-info")
            second = client.get("/api/wan-info")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.get_json()["data"]["public_ip"], "8.8.8.8")
        self.assertEqual(first.get_json()["data"]["isp"], "Example ISP")
        self.assertEqual(second.get_json(), first.get_json())
        lookup.assert_called_once()
        self.assertEqual(lookup.call_args.kwargs["timeout"], module.WAN_INFO_TIMEOUT)

    def test_wan_info_lookup_failure_is_explicit_and_short_cached(self):
        from urllib.error import URLError

        module = self.module
        module._wan_info_cache.update(expires_at=0, data=None, error=None)
        with patch.object(module, "urlopen", side_effect=URLError("offline")) as lookup:
            client = module.app.test_client()
            first = client.get("/api/wan-info")
            second = client.get("/api/wan-info")

        self.assertEqual(first.status_code, 502)
        self.assertEqual(second.status_code, 502)
        self.assertIn("unavailable", first.get_json()["error"])
        lookup.assert_called_once()


if __name__ == "__main__":
    unittest.main()
