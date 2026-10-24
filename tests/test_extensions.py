"""State-machine/API regressions using a real temporary SQLite database."""
import csv
import importlib.util
import io
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
from fixture_db import make_database  # noqa: E402


class ExtensionTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("netprobe_extensions"),
                             "Persistent target history and alert extension is not implemented")
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

    def ping(self, target="google.com", loss=0, latency=25, address="8.8.8.8"):
        return {"host": target, "address": address, "latency": latency,
                "jitter": 1, "loss": loss}

    def record(self, rows, advance=46, dns=None, wan_info=None):
        self.ext.record_probe(self.now, rows, dns or {}, wan_info=wan_info)
        self.now += advance

    def pending(self):
        return self.client.get("/api/notifications/pending", headers={
            "X-NetProbe-Notify-Key": (self.root / "notify-token").read_text().strip()
        }).get_json()["notifications"]

    def test_additive_migration_preserves_every_existing_native_row(self):
        pristine = self.root / "pristine.sqlite"
        make_database(pristine)
        before = sqlite3.connect(pristine)
        after = sqlite3.connect(self.db)
        self.addCleanup(before.close)
        self.addCleanup(after.close)
        for table in ("measurements", "dns_measurements", "speedtests"):
            self.assertEqual(before.execute("SELECT * FROM " + table).fetchall(),
                             after.execute("SELECT * FROM " + table).fetchall())
        self.assertEqual(after.execute("PRAGMA quick_check").fetchone()[0], "ok")

    def test_wan_columns_migrate_without_losing_existing_target_history(self):
        legacy_db = self.root / "legacy.sqlite"
        conn = sqlite3.connect(legacy_db)
        conn.execute("""CREATE TABLE np_targets (
            id INTEGER PRIMARY KEY,cycle_ts INTEGER NOT NULL,ts INTEGER NOT NULL,
            target TEXT NOT NULL,address TEXT,kind TEXT NOT NULL,latency REAL,
            jitter REAL,loss REAL,UNIQUE(cycle_ts,target))""")
        conn.execute("INSERT INTO np_targets VALUES (1,10,10,'google.com','8.8.8.8','ping',20,1,0)")
        conn.commit()
        conn.close()
        self.Extension(str(legacy_db), str(self.root / "legacy-data"), clock=lambda: self.now)
        conn = sqlite3.connect(legacy_db)
        self.addCleanup(conn.close)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(np_targets)")}
        self.assertTrue({"wan_ip", "wan_isp"}.issubset(columns))
        self.assertEqual(conn.execute("SELECT target,wan_ip,wan_isp FROM np_targets").fetchone(),
                         ("google.com", None, None))

    def test_confirmed_failure_is_grouped_and_stable_failure_does_not_spam(self):
        bad = [self.ping(loss=100), self.ping("1.1.1.1", loss=25, address="1.1.1.1")]
        self.record(bad)
        self.assertEqual(self.pending(), [])
        self.record(bad)
        self.assertEqual(len(self.pending()), 1)
        events = self.client.get("/api/events?range=3600").get_json()["data"]
        self.assertEqual({(r["target"], r["state"]) for r in events},
                         {("google.com", "DOWN"), ("1.1.1.1", "DEGRADED")})
        self.record(bad)
        self.record(bad)
        self.assertEqual(len(self.pending()), 1)

    def test_restart_preserves_candidate_active_state_and_confirmed_recovery(self):
        self.record([self.ping(loss=100)])
        self.ext = self.Extension(str(self.db), str(self.root), clock=lambda: self.now)
        self.record([self.ping(loss=100)])
        self.assertEqual(len(self.pending()), 1)
        self.ext = self.Extension(str(self.db), str(self.root), clock=lambda: self.now)
        self.record([self.ping()])
        self.assertEqual(len(self.pending()), 1)
        self.record([self.ping()])
        self.assertEqual([x["level"] for x in self.pending()], ["alert", "normal"])

    def test_duplicate_and_old_samples_do_not_confirm_or_rollback_state(self):
        bad = [self.ping(loss=100)]
        self.ext.record_probe(self.now, bad, {})
        self.ext.record_probe(self.now, bad, {})
        self.ext.record_probe(self.now - 46, bad, {})
        self.assertEqual(self.pending(), [])
        self.now += 46
        self.record(bad)
        self.assertEqual(len(self.pending()), 1)
        self.ext.record_probe(self.now - 92, [self.ping()], {})
        rows = self.client.get("/api/targets/recent").get_json()["data"]
        self.assertEqual(rows[0]["state"], "DOWN")

    def test_nonconsecutive_bad_samples_do_not_confirm(self):
        self.record([self.ping(loss=25)])
        self.record([self.ping()])
        self.record([self.ping(loss=25)])
        self.assertEqual(self.pending(), [])

    def test_latency_and_dns_thresholds_confirm_independently(self):
        for _ in range(2):
            self.record([self.ping(latency=151)], dns={"9.9.9.9": 251})
        rows = self.client.get("/api/targets/recent").get_json()["data"]
        self.assertEqual({r["kind"] for r in rows}, {"ping", "dns"})
        self.assertEqual({r["state"] for r in rows}, {"DEGRADED"})
        self.assertEqual(len(self.pending()), 1)

    def test_missing_resolved_ip_stays_unknown_in_history(self):
        self.record([self.ping(address=None, loss=100)])
        data = self.client.get("/api/targets/history?target=google.com").get_json()["data"]
        self.assertIsNone(data[0]["address"])
        self.assertEqual(data[0]["loss"], 100)

    def test_no_reply_does_not_turn_native_score_penalties_into_measured_rtt(self):
        self.record([dict(self.ping(loss=100, latency=200), jitter=60, measured=False)])
        row = self.client.get("/api/targets/recent").get_json()["data"][0]
        self.assertIsNone(row["latency"])
        self.assertIsNone(row["jitter"])
        self.assertIsNone(row["avg_latency"])
        data = self.client.get("/api/targets/history?target=google.com").get_json()["data"]
        self.assertIsNone(data[0]["latency"])
        export = list(csv.DictReader(io.StringIO(self.client.get("/api/export/targets.csv").get_data(as_text=True))))
        self.assertEqual(export[0]["rtt_ms"], "")

    def test_ping_destination_uses_actual_header_or_a_known_literal_only(self):
        import netprobe_extensions
        self.assertTrue(hasattr(netprobe_extensions, "ping_address"), "Ping destination parsing is not yet integrated")
        resolve = netprobe_extensions.ping_address
        self.assertEqual(resolve("google.com", "PING google.com (142.250.1.1) 56(84) bytes of data."), "142.250.1.1")
        self.assertEqual(resolve("192.168.1.1", ""), "192.168.1.1")
        self.assertEqual(resolve("::1", ""), "::1")
        self.assertIsNone(resolve("unresolved.invalid", ""))

    def test_csv_export_neutralizes_formulas_without_altering_metrics(self):
        self.record([self.ping(target="=HYPERLINK(\"bad\")", loss=25)])
        text = self.client.get("/api/export/targets.csv").get_data(as_text=True)
        row = list(csv.DictReader(io.StringIO(text)))[0]
        self.assertTrue(row["target"].startswith("'="))
        self.assertEqual(float(row["loss_pct"]), 25)

    def test_api_rejects_invalid_ranges_and_corrupt_settings_is_not_healthy(self):
        for value in ("0", "-1", "", "nan", "99999999999999999999"):
            self.assertEqual(self.client.get("/api/history?range=" + value).status_code, 400)
        (self.root / "alert-settings.json").write_text('{"loss_pct": NaN}')
        self.assertEqual(self.client.get("/api/settings").status_code, 503)

    def test_huge_settings_number_is_rejected_without_server_error(self):
        response = self.client.post("/api/settings", json={"loss_pct": 10 ** 400},
                                    headers={"X-NetProbe-Request": "settings"})
        self.assertEqual(response.status_code, 400)

    def test_inclusive_history_window_never_exceeds_six_hundred_buckets(self):
        for i in range(601):
            self.ext.record_probe(self.now - 3600 + i * 6, [self.ping()], {})
        rows = self.client.get("/api/targets/history?target=google.com&range=3600").get_json()["data"]
        self.assertLessEqual(len(rows), 600)

    def test_authenticated_queue_reports_last_durable_probe_and_startup(self):
        headers = {"X-NetProbe-Notify-Key": (self.root / "notify-token").read_text().strip()}
        empty = self.client.get("/api/notifications/pending", headers=headers).get_json()
        self.assertIsNone(empty.get("latest_probe_ts"))
        self.assertEqual(empty.get("process_started_at"), self.now)
        self.assertEqual(empty.get("max_sample_age"), 180)
        self.record([self.ping()])
        full = self.client.get("/api/notifications/pending", headers=headers).get_json()
        self.assertEqual(full["latest_probe_ts"], self.now - 46)

    def test_period_statistics_and_csv_keep_real_addresses_and_losses(self):
        self.record([self.ping(loss=0)])
        self.record([self.ping(loss=50)])
        row = self.client.get("/api/targets/recent?range=3600").get_json()["data"][0]
        self.assertEqual((row["samples"], row["avg_loss"], row["max_loss"]), (2, 25, 50))
        response = self.client.get("/api/export/targets.csv?range=3600")
        data = list(csv.DictReader(io.StringIO(response.get_data(as_text=True))))
        self.assertEqual([float(x["loss_pct"]) for x in data], [0, 50])
        self.assertEqual(data[1]["address"], "8.8.8.8")

    def test_period_statistics_and_csv_attribute_samples_to_the_recorded_wan(self):
        self.record([self.ping(latency=20)], wan_info={"public_ip": "9.9.9.9", "isp": "First ISP"})
        self.record([self.ping(latency=40, loss=25)],
                    wan_info={"public_ip": "1.1.1.1", "isp": "Second ISP"})
        row = self.client.get("/api/targets/recent?range=3600").get_json()["data"][0]
        groups = {(source["wan_ip"], source["wan_isp"]): source for source in row["wan_sources"]}
        self.assertEqual(set(groups), {("9.9.9.9", "First ISP"), ("1.1.1.1", "Second ISP")})
        self.assertEqual(groups[("9.9.9.9", "First ISP")]["samples"], 1)
        self.assertEqual(groups[("9.9.9.9", "First ISP")]["avg_latency"], 20)
        self.assertEqual(groups[("1.1.1.1", "Second ISP")]["avg_loss"], 25)
        recent = self.client.get("/api/targets/recent?range=60").get_json()["data"][0]
        self.assertEqual([(source["wan_ip"], source["samples"]) for source in recent["wan_sources"]],
                         [("1.1.1.1", 1)])
        data = list(csv.DictReader(io.StringIO(
            self.client.get("/api/export/targets.csv?range=3600").get_data(as_text=True))))
        self.assertEqual([(sample["wan_ip"], sample["wan_isp"]) for sample in data],
                         [("9.9.9.9", "First ISP"), ("1.1.1.1", "Second ISP")])

    def test_history_apis_filter_chart_data_by_selected_wan(self):
        self.record([self.ping(latency=20)], dns={"8.8.8.8": 12},
                    wan_info={"public_ip": "9.9.9.9", "isp": "First ISP"})
        self.record([self.ping(latency=40, loss=25)], dns={"8.8.8.8": 30},
                    wan_info={"public_ip": "1.1.1.1", "isp": "Second ISP"})
        summary = self.client.get(
            "/api/history?range=3600&wan_ip=9.9.9.9&wan_isp=First%20ISP").get_json()["data"]
        self.assertEqual(len(summary), 1)
        self.assertEqual((summary[0]["avg_latency_ms"], summary[0]["avg_loss_pct"],
                          summary[0]["avg_dns_latency_ms"]), (20, 0, 12))
        self.assertIsNone(summary[0]["score"], "WAN-specific history must not expose the all-WAN score")
        self.assertEqual(summary[0]["dns_per_server"], {"8.8.8.8": 12})
        target_history = self.client.get(
            "/api/targets/history?target=google.com&range=3600&wan_ip=1.1.1.1&wan_isp=Second%20ISP").get_json()["data"]
        self.assertEqual(len(target_history), 1)
        self.assertEqual((target_history[0]["latency"], target_history[0]["loss"]), (40, 25))

    def test_invalid_wan_source_is_rejected(self):
        with self.assertRaises(ValueError):
            self.ext.record_probe(self.now, [self.ping()], {},
                                  wan_info={"public_ip": "192.168.1.1", "isp": "Private"})

    def test_history_buckets_preserve_a_single_loss_spike(self):
        start = self.now - 2000
        for i in range(1000):
            self.ext.record_probe(start + i, [self.ping(loss=100 if i == 500 else 0)], {})
        data = self.client.get("/api/targets/history?target=google.com&range=3600").get_json()["data"]
        self.assertLessEqual(len(data), 600)
        self.assertEqual(max(x["max_loss"] for x in data), 100)

    def test_no_fake_per_ip_history_is_created_for_older_aggregate_rows(self):
        self.assertEqual(self.client.get("/api/targets/recent").get_json()["data"], [])
        self.assertEqual(self.client.get("/api/events").get_json()["data"], [])

    def test_settings_strict_validation_and_persistence(self):
        headers = {"X-NetProbe-Request": "settings"}
        for value in (None, "", " ", "abc", -1, 0, float("nan"), True):
            with self.subTest(value=value):
                self.assertEqual(self.client.post("/api/settings", json={"loss_pct": value},
                                                  headers=headers).status_code, 400)
        for payload in ({"confirm_samples": 1.5}, {"enabled": "false"}, {"unknown": 5}):
            self.assertEqual(self.client.post("/api/settings", json=payload, headers=headers).status_code, 400)
        self.assertEqual(self.client.post("/api/settings", json={"loss_pct": 25}, headers=headers).status_code, 200)
        restarted = self.Extension(str(self.db), str(self.root), clock=lambda: self.now)
        self.assertEqual(restarted.settings()["loss_pct"], 25)
        self.assertEqual(restarted.settings()["confirm_samples"], 2)

    def test_settings_writes_require_explicit_same_origin_request(self):
        self.assertEqual(self.client.post("/api/settings", json={"loss_pct": 25}).status_code, 403)
        self.assertEqual(self.client.post("/api/settings", json={"loss_pct": 25}, headers={
            "X-NetProbe-Request": "settings", "Origin": "http://attacker.invalid"
        }).status_code, 403)

    def test_queue_is_private_acknowledged_and_restart_safe(self):
        for _ in range(2):
            self.record([self.ping(loss=100)])
        self.assertEqual(self.client.get("/api/notifications/pending").status_code, 403)
        self.assertEqual(self.client.post("/api/notifications/1/ack").status_code, 403)
        h = {"X-NetProbe-Notify-Key": (self.root / "notify-token").read_text().strip()}
        self.assertEqual(self.client.post("/api/notifications/1/ack", headers=h).status_code, 200)
        self.assertEqual(self.client.post("/api/notifications/1/ack", headers=h).status_code, 200)
        self.assertEqual(self.pending(), [])
        restarted = self.Extension(str(self.db), str(self.root), clock=lambda: self.now)
        restarted.record_probe(self.now, [self.ping(loss=100)], {})
        self.assertEqual(self.pending(), [])
        self.assertNotIn((self.root / "notify-token").read_text().strip(),
                         self.client.get("/api/settings").get_data(as_text=True))

    def test_disabled_alerts_still_preserve_monitoring_history(self):
        self.client.post("/api/settings", json={"enabled": False}, headers={"X-NetProbe-Request": "settings"})
        for _ in range(3):
            self.record([self.ping(loss=100)])
        self.assertEqual(self.pending(), [])
        self.assertEqual(self.client.get("/api/targets/recent").get_json()["data"][0]["samples"], 3)


if __name__ == "__main__":
    unittest.main()
