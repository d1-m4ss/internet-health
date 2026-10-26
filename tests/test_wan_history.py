"""WAN history: samples carry the address and provider that carried them (only with the external-IP option on)."""
import csv
import io
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
from fixture_db import make_database  # noqa: E402
import netprobe_extensions as npx  # noqa: E402

HEADERS = {"X-NetProbe-Request": "settings"}
T0 = 2_000_000_000
A = ("203.0.113.10", "Alpha Net")   # documentation addresses and invented providers
B = ("198.51.100.20", "Beta Cable")


class WanBase(unittest.TestCase):
    def setUp(self):
        from flask import Flask
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "netprobe.sqlite"
        make_database(self.db)
        self.now = T0
        self.ext = npx.Extension(str(self.db), str(self.root), clock=lambda: self.now)
        self.fetches = []
        self.answers = {"https://ipwho.is/": A, "https://api.ipify.org": (A[0], None), "https://icanhazip.com": (A[0], None)}

        def fetch(url, timeout=None):
            self.fetches.append(url)
            answer = self.answers[url]
            if isinstance(answer, Exception):
                raise answer
            return answer
        self.ext.ip_fetch = fetch
        app = Flask(__name__)
        npx.install_routes(app, self.ext)
        self.client = app.test_client()
        self.conn = sqlite3.connect(self.db)
        self.addCleanup(self.conn.close)

    def option(self, on):
        self.ext.update_prefs({"show_public_ip": on})

    def know(self, wan):
        self.ext.ip_cache = dict(ip=wan[0], isp=wan[1], source="ipwho.is", checked_at=int(self.now), error=None)

    def cycle(self, loss=0, latency=20.0, hosts=("google.com", "1.1.1.1")):
        rows = [dict(host=h, address="192.0.2.%d" % (i + 1), latency=latency, jitter=1.0, loss=loss) for i, h in enumerate(hosts)]
        self.ext.record_probe(self.now, rows, {"8.8.8.8": 9.0})
        self.now += 30

    def wan_of(self, target="google.com"):
        return [tuple(r) for r in self.conn.execute("SELECT wan_ip,wan_isp FROM np_targets WHERE target=? ORDER BY ts", (target,))]


class RecordingTests(WanBase):
    def test_nothing_is_recorded_while_the_option_is_off(self):
        self.know(A)
        self.cycle()
        self.assertEqual(self.wan_of(), [(None, None)])

    def test_samples_carry_the_current_wan_when_the_option_is_on(self):
        self.option(True)
        self.know(A)
        self.cycle()
        self.know(B)
        self.cycle()
        self.assertEqual(self.wan_of(), [A, B])
        self.assertEqual(self.wan_of("dns:8.8.8.8"), [A, B], "DNS samples are tagged too")

    def test_a_stale_or_missing_address_is_never_attached(self):
        self.option(True)
        self.ext.update_prefs({"wan_check_min": 1})
        self.cycle()  # no lookup yet
        self.know(A)
        self.now += npx.WAN_MAX_AGE + 1
        self.cycle()  # last confirmation is too old
        self.ext.ip_cache = dict(ip=None, isp=None, source=None, checked_at=int(self.now), error="offline")
        self.cycle()  # the lookup failed
        self.assertEqual(self.wan_of(), [(None, None)] * 3)

    def test_a_slower_interval_tolerates_one_missed_check_and_manual_mode_records_nothing(self):
        self.option(True)                      # default: every 15 minutes
        self.know(A)
        self.now += 2 * 15 * 60                # one check missed: still tagged
        self.cycle()
        self.now += 1
        self.cycle()                           # more than two intervals: no longer trusted
        self.ext.update_prefs({"wan_check_min": 0})
        self.know(B)
        self.cycle()                           # manual mode: the address is shown, never recorded
        self.assertEqual(self.wan_of(), [A, (None, None), (None, None)])

    def test_an_unknown_provider_is_recorded_as_unknown_not_invented(self):
        self.option(True)
        self.know((B[0], None))
        self.cycle()
        self.assertEqual(self.wan_of(), [(B[0], None)])

    def test_old_databases_are_migrated_and_keep_their_rows(self):
        legacy = self.root / "legacy.sqlite"
        make_database(legacy)
        conn = sqlite3.connect(legacy)
        conn.execute("""CREATE TABLE np_targets (id INTEGER PRIMARY KEY, cycle_ts INTEGER NOT NULL, ts INTEGER NOT NULL, target TEXT NOT NULL,
            address TEXT, kind TEXT NOT NULL, latency REAL, jitter REAL, loss REAL, UNIQUE(cycle_ts, target))""")
        conn.execute("INSERT INTO np_targets(cycle_ts,ts,target,address,kind,latency,jitter,loss) VALUES (1,1,'old.example','192.0.2.9','ping',5,1,0)")
        conn.commit()
        conn.close()
        npx.Extension(str(legacy), str(self.root / "legacy-data"))
        npx.Extension(str(legacy), str(self.root / "legacy-data"))  # a second start changes nothing
        conn = sqlite3.connect(legacy)
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute("SELECT target,wan_ip,wan_isp FROM np_targets").fetchall(), [("old.example", None, None)])


class WatcherTests(WanBase):
    def test_off_means_no_requests_at_all(self):
        self.ext.wan_tick()
        self.assertEqual(self.fetches, [])

    def test_the_first_check_is_a_full_lookup(self):
        self.option(True)
        self.ext.wan_tick()
        self.assertEqual(self.fetches, ["https://ipwho.is/"])
        self.assertEqual((self.ext.ip_cache["ip"], self.ext.ip_cache["isp"]), A)

    def test_an_unchanged_address_costs_one_tiny_request_and_keeps_the_provider(self):
        self.option(True)
        self.ext.wan_tick()
        self.fetches.clear()
        self.now += 60
        self.ext.wan_tick()
        self.assertEqual(self.fetches, ["https://api.ipify.org"], "no provider lookup for an unchanged address")
        self.assertEqual((self.ext.ip_cache["ip"], self.ext.ip_cache["isp"]), A)
        self.assertEqual(self.ext.ip_cache["checked_at"], self.now, "and the confirmation time moves on")

    def test_a_changed_address_triggers_the_provider_lookup(self):
        self.option(True)
        self.ext.wan_tick()
        self.fetches.clear()
        self.answers["https://api.ipify.org"] = (B[0], None)
        self.answers["https://ipwho.is/"] = B
        self.now += 60
        self.ext.wan_tick()
        self.assertEqual(self.fetches, ["https://api.ipify.org", "https://ipwho.is/"])
        self.assertEqual((self.ext.ip_cache["ip"], self.ext.ip_cache["isp"]), B)

    def test_offline_keeps_what_is_known_and_does_not_hammer_the_provider_service(self):
        self.option(True)
        self.ext.wan_tick()
        self.fetches.clear()
        for url in ("https://api.ipify.org", "https://icanhazip.com"):
            self.answers[url] = OSError("network is unreachable")
        self.now += 60
        self.ext.wan_tick()
        self.assertEqual(self.fetches, ["https://api.ipify.org", "https://icanhazip.com"])
        self.assertEqual(self.ext.ip_cache["ip"], A[0])

    def test_the_provider_name_is_refreshed_once_a_day(self):
        self.option(True)
        self.ext.wan_tick()
        self.fetches.clear()
        self.answers["https://ipwho.is/"] = (A[0], "Alpha Net Renamed")
        self.now += npx.WAN_FULL_EVERY + 1
        self.ext.wan_tick()
        self.assertEqual(self.fetches, ["https://ipwho.is/"])
        self.assertEqual(self.ext.ip_cache["isp"], "Alpha Net Renamed")

    def test_the_thread_survives_errors_and_stops_asking_when_switched_off(self):
        self.option(True)
        self.ext.ip_fetch = lambda url, timeout=None: (_ for _ in ()).throw(RuntimeError("boom"))
        thread = self.ext.start_wan_watcher(sleep=lambda s: None, rounds=3)
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.option(False)
        self.fetches.clear()
        self.ext.ip_fetch = lambda url, timeout=None: self.fetches.append(url) or A
        self.ext.start_wan_watcher(sleep=lambda s: None, rounds=2).join(5)
        self.assertEqual(self.fetches, [])


class IntervalTests(WanBase):
    """The watcher thread wakes every minute; the chosen interval decides whether a check is due."""
    def setUp(self):
        super().setUp()
        self.option(True)

    def test_a_check_is_due_at_the_chosen_interval_only(self):
        for minutes in (1, 15):
            self.ext.update_prefs({"wan_check_min": minutes})
            self.ext.wan_checked_at = None
            self.assertTrue(self.ext.wan_due(), "the first check is made at once")
            self.ext.wan_tick()
            self.now += minutes * 60 - 31
            self.assertFalse(self.ext.wan_due(), "%d min: not yet" % minutes)
            self.now += 31
            self.assertTrue(self.ext.wan_due(), "%d min: due" % minutes)

    def test_manual_mode_and_the_switched_off_option_are_never_due(self):
        self.ext.update_prefs({"wan_check_min": 0})
        self.assertFalse(self.ext.wan_due())
        self.ext.update_prefs({"wan_check_min": 1, "show_public_ip": False})
        self.assertFalse(self.ext.wan_due())

    def test_a_new_address_is_confirmed_a_minute_later_not_a_whole_interval_later(self):
        self.ext.wan_tick()                              # 15-minute mode, first check
        self.assertFalse(self.ext.wan_due())
        self.now += 15 * 60
        self.answers["https://api.ipify.org"] = (B[0], None)
        self.answers["https://ipwho.is/"] = B
        self.ext.wan_tick()                              # first sighting of the new address
        self.assertIsNotNone(self.ext.wan_pending)
        self.now += 60
        self.assertTrue(self.ext.wan_due(), "the second look is not postponed by 15 minutes")
        self.ext.wan_tick()
        self.assertIsNone(self.ext.wan_pending)
        self.assertEqual(self.ext.wan_state()["ip"], B[0])

    def test_the_thread_checks_only_when_due(self):
        self.ext.start_wan_watcher(sleep=lambda s: None, rounds=5).join(5)
        self.assertEqual(self.fetches, ["https://ipwho.is/"], "five wake-ups at the same instant, one check")


class QueryTests(WanBase):
    def setUp(self):
        super().setUp()
        self.option(True)
        self.cycle(loss=0, latency=20.0)           # no WAN known yet
        self.know(A)
        for loss, latency in ((0, 20.0), (0, 22.0), (50, 40.0)):
            self.cycle(loss=loss, latency=latency)
        self.know(B)
        for loss, latency in ((0, 60.0), (10, 80.0)):
            self.cycle(loss=loss, latency=latency)
        self.now += 5

    def api(self, path, **query):
        return self.client.get(path, query_string=dict(range=3600, **query))

    def test_sources_list_every_wan_of_the_period_newest_first(self):
        self.know(B)
        data = self.api("/api/wan/sources").get_json()
        self.assertEqual([(s["wan_ip"], s["wan_isp"], s["samples"]) for s in data["sources"]],
                         [(B[0], B[1], 6), (A[0], A[1], 9), (None, None, 3)])  # three samples per probe cycle: two pings and a DNS server
        self.assertEqual(data["current"], {"wan_ip": B[0], "wan_isp": B[1]})
        self.assertEqual(data["sources"][0]["last_ts"], max(s["last_ts"] for s in data["sources"]))

    def test_the_loss_report_can_be_limited_to_one_wan_and_always_lists_how_each_fared(self):
        everything = self.api("/api/loss").get_json()["data"]
        self.assertEqual(everything["summary"]["samples"], 12)
        by = {(w["wan_ip"], w["wan_isp"]): w for w in everything["wan_sources"]}
        self.assertEqual(set(by), {(A[0], A[1]), (B[0], B[1]), (None, "")})
        self.assertEqual((by[(A[0], A[1])]["samples"], by[(A[0], A[1])]["lossy"], by[(A[0], A[1])]["max_loss"]), (6, 2, 50))
        self.assertEqual((by[(B[0], B[1])]["samples"], by[(B[0], B[1])]["lossy"], by[(B[0], B[1])]["max_loss"]), (4, 2, 10))
        self.assertEqual((by[(None, "")]["samples"], by[(None, "")]["lossy"]), (2, 0))
        self.assertAlmostEqual(by[(B[0], B[1])]["avg_loss"], 5.0)
        only_a = self.api("/api/loss", wan_ip=A[0], wan_isp=A[1]).get_json()["data"]
        self.assertEqual((only_a["summary"]["samples"], only_a["summary"]["lossy"], only_a["summary"]["max_loss"], only_a["summary"]["episodes"]), (6, 2, 50.0, 2))
        only_b = self.api("/api/loss", wan_ip=B[0], wan_isp=B[1]).get_json()["data"]
        self.assertEqual((only_b["summary"]["samples"], only_b["summary"]["max_loss"]), (4, 10.0))
        self.assertTrue(all(e["peak"] == 10 for e in only_b["episodes"]), "episodes of the other source are not mixed in")
        self.assertEqual(only_b["wan_sources"], everything["wan_sources"], "the breakdown does not depend on the filter")
        unknown = self.api("/api/loss", wan_ip="").get_json()["data"]
        self.assertEqual((unknown["summary"]["samples"], unknown["summary"]["lossy"], unknown["episodes"]), (2, 0, []))

    def test_the_loss_breakdown_follows_the_chosen_target_and_bad_filters_are_refused(self):
        one = self.api("/api/loss", target="google.com").get_json()["data"]
        self.assertEqual(sum(w["samples"] for w in one["wan_sources"]), 6, "one target: half of the samples")
        for bad in ({"wan_ip": "not-an-ip"}, {"wan_ip": A[0], "wan_isp": "x" * 121}):
            self.assertEqual(self.api("/api/loss", **bad).status_code, 400)

    def test_targets_are_filtered_by_wan_with_their_own_statistics(self):
        everything = {r["target"]: r for r in self.api("/api/targets/recent").get_json()["data"]}
        only_a = {r["target"]: r for r in self.api("/api/targets/recent", wan_ip=A[0], wan_isp=A[1]).get_json()["data"]}
        only_b = {r["target"]: r for r in self.api("/api/targets/recent", wan_ip=B[0], wan_isp=B[1]).get_json()["data"]}
        unknown = {r["target"]: r for r in self.api("/api/targets/recent", wan_ip="").get_json()["data"]}
        self.assertEqual(everything["google.com"]["samples"], 6)
        self.assertEqual((only_a["google.com"]["samples"], round(only_a["google.com"]["avg_latency"], 1), only_a["google.com"]["max_loss"]), (3, 27.3, 50))
        self.assertEqual((only_b["google.com"]["samples"], round(only_b["google.com"]["avg_latency"], 1)), (2, 70.0))
        self.assertEqual(unknown["google.com"]["samples"], 1)
        breakdown = {(w["wan_ip"], w["wan_isp"]): w["samples"] for w in everything["google.com"]["wan_sources"]}
        self.assertEqual(breakdown, {A: 3, B: 2, (None, None): 1}, "the per-target breakdown is always the whole period")

    def test_a_target_without_samples_from_that_wan_is_left_out(self):
        self.know(B)
        self.cycle(hosts=("only-b.example",))
        names = [r["target"] for r in self.api("/api/targets/recent", wan_ip=A[0], wan_isp=A[1]).get_json()["data"]]
        self.assertNotIn("only-b.example", names)
        self.assertIn("only-b.example", [r["target"] for r in self.api("/api/targets/recent", wan_ip=B[0], wan_isp=B[1]).get_json()["data"]])

    def test_target_history_follows_the_filter(self):
        rows = self.api("/api/targets/history", target="google.com", wan_ip=A[0], wan_isp=A[1]).get_json()["data"]
        self.assertEqual(len(rows), 3)
        self.assertEqual(max(r["max_loss"] for r in rows), 50)
        rows_b = self.api("/api/targets/history", target="google.com", wan_ip=B[0], wan_isp=B[1]).get_json()["data"]
        self.assertEqual(sorted(round(r["latency"]) for r in rows_b), [60, 80])
        self.assertEqual(len(self.api("/api/targets/history", target="google.com").get_json()["data"]), 6)

    def test_summary_history_is_rebuilt_for_one_wan_without_a_made_up_score(self):
        rows = self.api("/api/history", wan_ip=A[0], wan_isp=A[1]).get_json()["data"]
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(r["score"] is None for r in rows), "the quality score cannot be attributed to one provider")
        self.assertEqual([round(r["avg_latency_ms"]) for r in rows], [20, 22, 40])
        self.assertEqual(max(r["max_loss_pct"] for r in rows), 50)
        self.assertEqual({k for k in rows[0]}, {"ts", "iso", "avg_latency_ms", "avg_jitter_ms", "avg_loss_pct", "max_loss_pct", "avg_dns_latency_ms", "dns_per_server", "score"})
        self.assertEqual(rows[0]["dns_per_server"], {"8.8.8.8": 9.0})
        self.assertEqual(rows[0]["avg_dns_latency_ms"], 9.0)

    def test_without_a_filter_the_answers_are_exactly_what_they_were(self):
        self.conn.execute("INSERT INTO measurements(ts,avg_latency_ms,avg_jitter_ms,avg_loss_pct,avg_dns_latency_ms,score) VALUES (?,25,1,0,5,93)", (T0 + 10,))
        self.conn.commit()
        rows = self.api("/api/history").get_json()["data"]
        self.assertEqual([(r["avg_latency_ms"], r["score"]) for r in rows], [(25.0, 93.0)], "the native table, with its quality score, as before")

    def test_bad_filters_are_refused(self):
        for query in ({"wan_ip": "not-an-ip"}, {"wan_ip": "999.1.1.1"}, {"wan_ip": A[0], "wan_isp": "x" * 121}, {"wan_ip": "'; DROP TABLE np_targets;--"}):
            for path in ("/api/targets/recent", "/api/history", "/api/targets/history"):
                self.assertEqual(self.api(path, target="google.com", **query).status_code, 400, (path, query))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM np_targets").fetchone()[0] > 0, True)

    def test_a_provider_filter_matches_exactly_and_is_not_a_sql_pattern(self):
        self.assertEqual(self.api("/api/targets/recent", wan_ip=A[0], wan_isp="Alpha%").get_json()["data"], [])
        self.assertEqual(self.api("/api/targets/recent", wan_ip=A[0], wan_isp="").get_json()["data"], [])

    def test_the_export_carries_the_wan_columns(self):
        text = self.client.get("/api/export/targets.csv?range=3600").get_data(as_text=True)
        rows = list(csv.DictReader(io.StringIO(text)))
        self.assertEqual(list(rows[0])[-2:], ["wan_ip", "wan_isp"])
        self.assertEqual({(r["wan_ip"], r["wan_isp"]) for r in rows}, {A, B, ("", "")})


class ForgetTests(WanBase):
    def setUp(self):
        super().setUp()
        self.option(True)
        self.know(A)
        self.cycle()
        self.cycle()

    def test_forgetting_erases_every_address_and_needs_a_confirmation(self):
        for body in ({}, {"confirm": False}, {"confirm": "yes"}, {"confirm": True, "x": 1}, []):
            self.assertEqual(self.client.post("/api/wan/forget", json=body, headers=HEADERS).status_code, 400)
        self.assertEqual(self.client.post("/api/wan/forget", json={"confirm": True}).status_code, 403, "same-origin header required")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM np_targets WHERE wan_ip IS NOT NULL").fetchone()[0], 6)
        done = self.client.post("/api/wan/forget", json={"confirm": True}, headers=HEADERS).get_json()
        self.assertEqual(done, {"forgotten": 6})
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM np_targets WHERE wan_ip IS NOT NULL OR wan_isp IS NOT NULL").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM np_targets").fetchone()[0], 6, "only the addresses are erased, the measurements stay")
        self.assertEqual(self.client.post("/api/wan/forget", json={"confirm": True}, headers=HEADERS).get_json(), {"forgotten": 0})


class ChangeNotificationTests(WanBase):
    def setUp(self):
        super().setUp()
        self.option(True)
        self.ext.update_prefs({"wan_notify": True})

    def see(self, wan, times=2):
        """The watcher sees `wan` on `times` checks in a row (a minute apart)."""
        result = None
        for _ in range(times):
            result = self.ext.wan_observe(*wan)
            self.now += 60
        return result

    def queued(self):
        return self.conn.execute("SELECT level,subject,description,message FROM np_notifications ORDER BY id").fetchall()

    def test_the_first_address_is_only_remembered(self):
        self.see(A)
        self.assertEqual(self.queued(), [])
        self.assertEqual(self.ext.wan_state()["ip"], A[0])

    def test_nothing_is_sent_unless_asked_for(self):
        self.ext.update_prefs({"wan_notify": False})
        self.see(A)
        self.see(B)
        self.assertEqual(self.queued(), [])
        self.assertEqual(self.ext.wan_state()["ip"], B[0], "the new address is still remembered")

    def test_a_change_of_provider_is_a_warning_with_before_and_now(self):
        self.see(A)
        self.see(B)
        (level, subject, description, message), = self.queued()
        self.assertEqual(level, "warning")
        self.assertEqual(subject, "🌐 Internet Health: WAN changed: Beta Cable")
        self.assertEqual(description, "WAN changed: Beta Cable: 203.0.113.10 → 198.51.100.20")
        lines = message.split("\\n")
        self.assertEqual(lines[0], "🌐 WAN changed: Beta Cable")
        self.assertTrue(lines[2].startswith("• Before: Alpha Net · 203.0.113.10 (since "), lines[2])
        self.assertEqual(lines[3], "• Now: Beta Cable · 198.51.100.20")
        self.assertIn("another link", message)
        self.assertNotRegex(message.replace("\\n", ""), r"[\x00-\x1f\\]")

    def test_a_new_address_from_the_same_provider_is_informational(self):
        self.see(A)
        self.see(("198.51.100.99", "Alpha Net"))
        (level, subject, description, message), = self.queued()
        self.assertEqual((level, subject), ("normal", "🌐 Internet Health: External IP changed (Alpha Net)"))
        self.assertIn("assigned a new address", message)

    def test_an_unknown_provider_is_said_not_invented(self):
        self.see(A)
        self.see((B[0], None))
        (level, subject, description, message), = self.queued()
        self.assertIn("• Now: provider not reported · 198.51.100.20", message)

    def test_one_odd_answer_is_not_a_change(self):
        self.see(A)
        self.see(B, times=1)       # a single sighting
        self.see(A)                # and it is gone
        self.assertEqual(self.queued(), [])
        self.assertEqual(self.ext.wan_state()["ip"], A[0])

    def test_a_renamed_provider_with_the_same_address_is_not_a_change(self):
        self.see(A)
        self.see((A[0], "Alpha Net Renamed"))
        self.assertEqual(self.queued(), [])
        self.assertEqual(self.ext.wan_state()["isp"], "Alpha Net Renamed")

    def test_an_address_only_check_never_looks_like_a_change_of_provider(self):
        self.see(A)
        self.see((A[0], None))
        self.assertEqual((self.queued(), self.ext.wan_state()["isp"]), ([], "Alpha Net"))

    def test_the_last_known_wan_survives_a_restart_and_a_change_made_meanwhile_is_reported(self):
        self.see(A)
        restarted = npx.Extension(str(self.db), str(self.root), clock=lambda: self.now)
        restarted.wan_observe(*B)
        restarted.wan_observe(*B)
        self.assertEqual(len(self.queued()), 1)
        self.assertEqual(restarted.wan_state()["ip"], B[0])

    def test_a_link_that_keeps_flapping_cannot_flood(self):
        self.see(A)
        for i in range(10):
            self.see(B if i % 2 == 0 else A)
        self.assertEqual(len(self.queued()), npx.WAN_NOTIFY_MAX_PER_HOUR)

    def test_the_master_switch_of_notifications_is_respected(self):
        self.ext.update_settings({"enabled": False})
        self.see(A)
        self.see(B)
        self.assertEqual(self.queued(), [])

    def test_nothing_is_stored_or_sent_while_the_option_is_off(self):
        self.option(False)
        self.see(A)
        self.see(B)
        self.assertEqual((self.queued(), self.ext.wan_state()), ([], None))

    def test_provider_names_cannot_inject_lines(self):
        self.see(A)
        self.see((B[0], "Evil\nNet \\n Extra"))
        (_, subject, _, message), = self.queued()
        self.assertEqual(subject, "🌐 Internet Health: WAN changed: Evil Net n Extra")
        self.assertNotRegex(message.replace("\\n", ""), r"[\x00-\x1f\\]")

    def test_it_does_not_count_as_covering_a_turbo_outage(self):
        self.ext.turbo_configure(None, ["1.1.1.1"])
        self.see(A)
        self.see(B)
        row = {"start_ts": int(self.now) - 30, "end_ts": int(self.now) + 30}
        with self.ext.db() as conn:
            self.assertFalse(self.ext.turbo_covered(conn, row))

    def test_it_works_through_the_real_check(self):
        """wan_tick -> full lookup -> address-only checks -> the failover is seen by two checks and announced once."""
        self.ext.wan_tick()
        self.assertEqual(self.queued(), [])
        self.answers["https://api.ipify.org"] = (B[0], None)
        self.answers["https://ipwho.is/"] = B
        for _ in range(3):
            self.now += 60
            self.ext.wan_tick()
        self.assertEqual([q[1] for q in self.queued()], ["🌐 Internet Health: WAN changed: Beta Cable"])
        key = (self.root / "notify-token").read_text().strip()
        pending = self.client.get("/api/notifications/pending", headers={"X-NetProbe-Notify-Key": key}).get_json()["notifications"]
        self.assertEqual(len(pending), 1)

    def test_manual_mode_makes_no_announcement_and_no_check(self):
        self.ext.update_prefs({"wan_check_min": 0})
        self.see(A)
        self.see(B)
        self.assertEqual(self.queued(), [], "without a watcher a change cannot be announced")
        self.fetches.clear()
        self.ext.wan_tick()
        self.assertEqual(self.fetches, [])

    def test_the_preference_is_a_real_boolean(self):
        for bad in ("yes", 1, None):
            self.assertEqual(self.client.post("/api/prefs", json={"wan_notify": bad}, headers=HEADERS).status_code, 400)


if __name__ == "__main__":
    unittest.main()
