"""Fixes made when the project stopped being a patch on the NetProbe image: ping timing, DNS averages, indexes,
a probe loop that survives errors and the /api/health used by the container health check."""
import importlib.util
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
import dns.resolver  # noqa: E402
from fixture_db import make_database  # noqa: E402


class StopLoop(Exception):
    pass


class AppCase(unittest.TestCase):
    def load(self, **extra_env):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "netprobe.sqlite"
        make_database(self.db)
        env = {"DB_PATH": str(self.db), "SPEEDTEST_ENABLED": "false", "ROUTER_IP": "", "SITES": "google.com",
               "DNS_NAMESERVER_1_IP": "8.8.8.8", "DNS_NAMESERVER_2_IP": "", "DNS_NAMESERVER_3_IP": "",
               "DNS_NAMESERVER_4_IP": "", "PING_COUNT": "4"}
        env.update(extra_env)
        env_patch = patch.dict(os.environ, env)
        env_patch.start()
        self.addCleanup(env_patch.stop)
        spec = importlib.util.spec_from_file_location("tested_fork_app", ROOT / "src/app.py")
        module = importlib.util.module_from_spec(spec)
        with patch("threading.Thread.start"):
            spec.loader.exec_module(module)
        return module


class PingTests(AppCase):
    def test_ping_does_not_wait_ten_seconds_for_the_last_reply(self):
        module = self.load()
        output = "4 packets transmitted, 4 received, 0% packet loss, time 3004ms\nrtt min/avg/max/mdev = 1.0/2.0/3.0/0.5 ms\n"
        with patch.object(module.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout=output, stderr="")) as run:
            result = module.run_ping("1.1.1.1", 4)
        self.assertEqual(run.call_args.args[0], ["ping", "-q", "-c", "4", "-W", "2", "1.1.1.1"])
        # a dead host ends after the packets plus the wait (~5 s); the limit leaves room for that and is not the old 8 s
        self.assertEqual(run.call_args.kwargs["timeout"], 4 + 2 + 5)
        self.assertEqual((result["loss"], result["latency"]), (0.0, 2.0))

    def test_ping_wait_is_configurable(self):
        module = self.load(PING_WAIT="3")
        with patch.object(module.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, stdout="", stderr="")) as run:
            result = module.run_ping("10.255.255.1", 4)
        self.assertEqual(run.call_args.args[0], ["ping", "-q", "-c", "4", "-W", "3", "10.255.255.1"])
        self.assertEqual(run.call_args.kwargs["timeout"], 4 + 3 + 5)
        self.assertEqual(result["loss"], 100.0, "no output at all is a failed check")


class DnsTests(AppCase):
    def resolver(self, outcomes):
        """A dns.resolver.Resolver whose resolve() returns / raises the given outcomes in turn."""
        instance = MagicMock()
        instance.resolve.side_effect = list(outcomes)
        return patch.object(dns.resolver, "Resolver", return_value=instance)

    def test_timeouts_are_not_averaged_in_as_latency(self):
        module = self.load()
        with self.resolver([None, dns.resolver.LifetimeTimeout(), None]), \
             patch.object(module.time, "perf_counter", side_effect=[0.0, 0.010, 0.0, 0.0, 0.030]):  # a failed query reads the clock once
            latency = module.measure_dns_latency("example.com", "8.8.8.8", 3)
        self.assertAlmostEqual(latency, 20.0, places=3, msg="(10 ms + 30 ms) / 2; the 3 s timeout is not a latency")
        self.assertEqual(module._dns_answers["8.8.8.8"], 2)

    def test_a_server_that_answers_nothing_has_no_latency(self):
        module = self.load()
        with self.resolver([dns.resolver.LifetimeTimeout(), dns.resolver.NoNameservers(), dns.resolver.LifetimeTimeout()]):
            self.assertIsNone(module.measure_dns_latency("example.com", "8.8.8.8", 3))
        self.assertNotIn("8.8.8.8", module._dns_answers)
        with patch.object(module, "measure_dns_latency", return_value=None):
            self.assertIsNone(module.measure_dns_latency_multi(["a.com", "b.com"], "8.8.8.8", 3))

    def test_nxdomain_is_an_answer(self):
        module = self.load()
        with self.resolver([dns.resolver.NXDOMAIN(), dns.resolver.NoAnswer()]):
            self.assertIsNotNone(module.measure_dns_latency("nothing.invalid", "8.8.8.8", 2))
        self.assertEqual(module._dns_answers["8.8.8.8"], 2)

    def test_cycle_where_no_dns_server_answers_gets_the_failure_penalty(self):
        module = self.load()
        ping = {"host": "google.com", "latency": 10.0, "jitter": 1.0, "loss": 0.0, "address": None, "measured": True,
                "sample_ts": 1}
        with patch.object(module, "run_ping", return_value=ping), \
             patch.object(module, "measure_dns_latency_multi", return_value=None), \
             patch.object(module, "insert_measurement") as insert, patch.object(module, "insert_dns_measurements") as insert_dns:
            module.run_probe_cycle(None)
        stored_dns = insert.call_args.args[4]
        self.assertEqual(stored_dns, module.THRESHOLD_DNS_LATENCY * 2)
        insert_dns.assert_called_once()
        self.assertEqual(insert_dns.call_args.args[1], {}, "no per-server value is stored for a server that did not answer")

    def test_cycle_averages_only_the_servers_that_answered(self):
        module = self.load(DNS_NAMESERVER_2_IP="1.1.1.1")
        ping = {"host": "google.com", "latency": 10.0, "jitter": 1.0, "loss": 0.0, "address": None, "measured": True,
                "sample_ts": 1}
        with patch.object(module, "run_ping", return_value=ping), \
             patch.object(module, "measure_dns_latency_multi", side_effect=[12.0, None]), \
             patch.object(module, "insert_measurement") as insert, patch.object(module, "insert_dns_measurements") as insert_dns:
            module.run_probe_cycle(None)
        self.assertEqual(insert.call_args.args[4], 12.0)
        self.assertEqual(insert_dns.call_args.args[1], {"8.8.8.8": 12.0})


class IndexTests(AppCase):
    def test_history_tables_are_indexed_by_time_and_the_queries_use_the_index(self):
        module = self.load()
        conn = sqlite3.connect(self.db)
        for table in ("measurements", "dns_measurements", "speedtests"):
            names = {row[1] for row in conn.execute(f"PRAGMA index_list({table})")}
            self.assertIn(f"{table}_ts", names)
            plan = " ".join(str(row[3]) for row in conn.execute(f"EXPLAIN QUERY PLAN SELECT * FROM {table} ORDER BY ts DESC LIMIT 5"))
            self.assertIn(f"{table}_ts", plan)
            self.assertNotIn("TEMP B-TREE", plan, "no sorting of the whole table")
        plan = " ".join(str(row[3]) for row in conn.execute("EXPLAIN QUERY PLAN SELECT * FROM dns_measurements WHERE ts IN (1, 2, 3)"))
        self.assertIn("dns_measurements_ts", plan)
        conn.close()
        module.ensure_db()  # a second start does not fail on the existing indexes

    def test_postgres_settings_are_ignored_not_fatal(self):
        module = self.load(DB_ENGINE="postgres", USE_POSTGRES="true")
        self.assertEqual(module.DB_ENGINE, "sqlite")
        self.assertEqual(module.get_db_connection().execute("select 1").fetchone(), (1,))


class ProbeLoopTests(AppCase):
    def test_an_error_in_one_cycle_does_not_stop_the_probes(self):
        module = self.load()
        calls = []

        def cycle(gw):
            calls.append(gw)
            if len(calls) == 1:
                raise sqlite3.OperationalError("database or disk is full")

        with patch.object(module, "get_default_gateway", return_value="172.17.0.1"), \
             patch.object(module, "run_probe_cycle", side_effect=cycle), \
             patch.object(module.time, "sleep", side_effect=[None, StopLoop()]), \
             self.assertRaises(StopLoop):
            module.probe_loop()
        self.assertEqual(calls, ["172.17.0.1", "172.17.0.1"], "the loop ran the second cycle after the failure")
        self.assertIn("disk is full", module.probe_state["last_error"])
        self.assertEqual(module.probe_state["errors_in_row"], 1)

    def test_a_stored_cycle_clears_the_error_count(self):
        module = self.load()
        module.probe_state.update(errors_in_row=3)
        ping = {"host": "google.com", "latency": 10.0, "jitter": 1.0, "loss": 0.0, "address": None, "measured": True,
                "sample_ts": 1}
        with patch.object(module, "run_ping", return_value=ping), \
             patch.object(module, "measure_dns_latency_multi", return_value=12.0):
            module.run_probe_cycle(None)
        self.assertEqual(module.probe_state["errors_in_row"], 0)
        self.assertAlmostEqual(module.probe_state["last_ok_ts"], time.time(), delta=5)
        conn = sqlite3.connect(self.db)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM measurements WHERE ts >= ?", (int(time.time()) - 10,)).fetchone()[0], 1)
        conn.close()


class HealthTests(AppCase):
    def setUp(self):
        self.module = self.load()
        self.client = self.module.app.test_client()

    def test_healthy_right_after_the_start_and_while_cycles_are_stored(self):
        body = self.client.get("/api/health")
        self.assertEqual((body.status_code, body.get_json()["status"], body.get_json()["last_cycle_ts"]), (200, "ok", None))
        self.module.probe_state.update(last_ok_ts=time.time() - 100, started=time.time() - 9999)
        body = self.client.get("/api/health")
        self.assertEqual((body.status_code, body.get_json()["status"]), (200, "ok"))

    def test_stalled_when_no_cycle_was_stored_for_the_interval_plus_five_minutes(self):
        limit = self.module.PROBE_INTERVAL + 300
        self.module.probe_state.update(last_ok_ts=time.time() - limit - 30, last_error="database is locked", errors_in_row=7)
        response = self.client.get("/api/health")
        data = response.get_json()
        self.assertEqual((response.status_code, data["status"], data["last_error"], data["errors_in_row"]),
                         (503, "stalled", "database is locked", 7))
        self.assertGreaterEqual(data["seconds_since_cycle"], limit)

    def test_stalled_when_the_first_cycle_never_came(self):
        self.module.probe_state.update(last_ok_ts=None, started=time.time() - self.module.PROBE_INTERVAL - 400)
        self.assertEqual(self.client.get("/api/health").status_code, 503)


if __name__ == "__main__":
    unittest.main()
