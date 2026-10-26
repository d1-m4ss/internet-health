"""User-facing notification text: English, plain-language diagnosis, icons, no spam while a problem persists."""
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
from fixture_db import make_database  # noqa: E402

GATEWAY, ROUTER = "172.17.0.1", "192.168.1.1"
ROLES = {
    GATEWAY: ("gateway", "Docker gateway"),
    ROUTER: ("router", "Router"),
    "google.com": ("site", "Google"),
    "cloudflare.com": ("site", "Cloudflare"),
    "1.1.1.1": ("site", "Cloudflare IP"),
    "dns:8.8.8.8": ("dns", "DNS Google"),
    "dns:1.1.1.1": ("dns", "DNS Cloudflare"),
}
SITES = ["google.com", "cloudflare.com"]  # names; "1.1.1.1" is a ping-by-IP site probed via ip=
DNS = ["8.8.8.8", "1.1.1.1"]


class MessageTests(unittest.TestCase):
    def setUp(self):
        from netprobe_extensions import Extension
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "netprobe.sqlite"
        make_database(self.db)
        self.now = 2_000_000_000
        self.ext = Extension(str(self.db), str(self.root), clock=lambda: self.now)
        self.ext.roles = dict(ROLES)

    def cycle(self, router=0, sites=(0, 0), dns=(True, True), latency=20, gateway=0, ip=0):
        def ping(host, loss):
            ok = loss < 100
            return {"host": host, "address": "192.0.2.1", "latency": latency if ok else None,
                    "jitter": 1 if ok else None, "loss": loss, "measured": ok}
        rows = [ping(GATEWAY, gateway), ping(ROUTER, router)] + [ping(h, l) for h, l in zip(SITES, sites)] + [ping("1.1.1.1", ip)]
        answered = {a: 20.0 for a, up in zip(DNS, dns) if up}
        failed = [a for a, up in zip(DNS, dns) if not up]
        self.ext.record_probe(self.now, rows, answered, dns_failed=failed)
        self.now += 30

    def run_cycles(self, n, **kw):
        for _ in range(n):
            self.cycle(**kw)

    def notes(self):
        with sqlite3.connect(self.db) as c:
            c.row_factory = sqlite3.Row
            return [dict(r) for r in c.execute("SELECT * FROM np_notifications ORDER BY id")]

    def last(self):
        return self.notes()[-1]

    def text(self, note):
        return note["subject"] + "\n" + note["description"] + "\n" + note["message"].replace("\\n", "\n")

    # -- diagnosis ---------------------------------------------------------------
    def test_router_down_is_named_with_hint_and_critical_level(self):
        self.run_cycles(3)
        self.run_cycles(2, router=100, sites=(100, 100), ip=100, dns=(False, False))
        note = self.last()
        self.assertTrue(note["subject"].startswith("🔴"), note["subject"])
        self.assertIn("Router is not responding", note["subject"])
        self.assertEqual(note["level"], "alert")
        text = self.text(note)
        self.assertIn("Router (192.168.1.1)", text)
        self.assertIn("no ping reply", text)
        self.assertIn("power", text)
        self.assertNotRegex(text, r"DOWN|ICMP|DEGRADED")

    def test_internet_down_while_router_answers_points_at_the_provider(self):
        self.run_cycles(3)
        self.run_cycles(2, sites=(100, 100), ip=100, dns=(False, False))
        note = self.last()
        self.assertIn("No internet", note["subject"])
        text = self.text(note)
        self.assertIn("Google (google.com)", text)
        self.assertIn("Cloudflare (cloudflare.com)", text)
        self.assertIn("DNS Google (8.8.8.8)", text)
        self.assertIn("ISP", text)
        self.assertEqual(note["level"], "alert")

    def test_only_one_site_down_is_not_called_an_internet_outage(self):
        self.run_cycles(3)
        self.run_cycles(2, sites=(100, 0))
        note = self.last()
        self.assertIn("Google", note["subject"])
        self.assertNotIn("No internet", self.text(note))
        self.assertIn("Other external hosts work", self.text(note))
        self.assertEqual(note["level"], "warning")
        self.assertTrue(note["subject"].startswith("🟠"))

    def test_loss_and_latency_are_explained_in_plain_russian(self):
        self.run_cycles(3)
        self.run_cycles(2, sites=(50, 0))
        self.assertIn("packet loss 50%", self.text(self.last()))
        self.run_cycles(3)
        self.run_cycles(2, latency=400)
        self.assertIn("latency 400 ms", self.text(self.last()))

    def test_single_dead_dns_server_is_named(self):
        self.run_cycles(3)
        self.run_cycles(2, dns=(True, False))
        text = self.text(self.last())
        self.assertIn("DNS Cloudflare (1.1.1.1)", text)
        self.assertIn("not responding", text)
        self.assertNotIn("No internet", text)

    def test_docker_gateway_failure_points_inside_unraid(self):
        self.run_cycles(3)
        self.run_cycles(2, gateway=100)
        self.assertIn("Docker gateway", self.text(self.last()))

    # -- recovery ----------------------------------------------------------------
    def test_recovery_has_check_icon_duration_and_normal_level(self):
        self.run_cycles(3)
        self.run_cycles(20, router=100)  # 10 minutes
        self.run_cycles(2)
        note = self.last()
        self.assertTrue(note["subject"].startswith("✅"), note["subject"])
        self.assertEqual(note["level"], "normal")
        self.assertRegex(self.text(note), r"lasted (10|11) min")

    def test_partial_recovery_lists_what_is_still_broken(self):
        self.run_cycles(3)
        self.run_cycles(2, router=100, sites=(100, 100))
        self.run_cycles(2, router=0, sites=(100, 100))
        text = self.text(self.last())
        self.assertIn("Still not OK", text)
        self.assertIn("Google", text)
        self.assertIn("Router", text)

    def test_duration_counts_the_whole_episode_across_down_and_degraded(self):
        self.run_cycles(3)
        self.run_cycles(20, sites=(50, 50))   # 10 min degraded
        self.run_cycles(20, sites=(100, 100)) # 10 min down
        self.run_cycles(2)
        self.assertRegex(self.text(self.last()), r"lasted (19|2\d) min")

    # -- format for Unraid email/Telegram ----------------------------------------
    def test_message_uses_literal_backslash_n_and_description_is_a_single_short_line(self):
        self.run_cycles(3)
        self.run_cycles(2, router=100, sites=(100, 100), dns=(False, False))
        note = self.last()
        self.assertNotRegex(note["message"], r"[\x00-\x1f\x7f]")
        self.assertIn("\\n", note["message"])
        self.assertEqual(len(re.findall(r"\\(?!n)", note["message"])), 0)
        self.assertNotRegex(note["description"], r"[\x00-\x1f\x7f\\]")
        self.assertTrue(0 < len(note["description"]) <= 240)
        self.assertNotRegex(note["subject"], r"[\x00-\x1f\x7f\\]")
        self.assertLessEqual(len(note["subject"]), 120)

    def test_hostile_target_names_cannot_inject_escapes(self):
        self.ext.roles["evil.example"] = ("site", "Ev\\x41il\nname")
        for _ in range(2):
            self.ext.record_probe(self.now, [dict(host="evil.example", address=None, latency=None, jitter=None,
                                                  loss=100, measured=False)], {})
            self.now += 30
        message = self.last()["message"]
        self.assertNotRegex(message, r"[\x00-\x1f\x7f]")
        self.assertEqual(len(re.findall(r"\\(?!n)", message)), 0)

    # -- no spam while the problem persists --------------------------------------
    def test_steady_outage_sends_one_message_even_after_hours(self):
        self.run_cycles(3)
        self.run_cycles(720, router=100, sites=(100, 100), dns=(False, False))  # 6 h
        self.assertEqual(len(self.notes()), 1)

    def test_worsening_and_partial_improvement_are_announced_with_their_own_icons(self):
        self.run_cycles(3)
        self.run_cycles(2, sites=(50, 0))      # problem (degraded)
        self.assertEqual(len(self.notes()), 1)
        self.run_cycles(2, sites=(100, 0))     # worse
        self.assertEqual(len(self.notes()), 2)
        self.assertIn("🔴⬇️", self.text(self.last()))
        self.assertIn("got worse", self.text(self.last()))
        self.run_cycles(2, sites=(50, 0))      # better within the hour: in the event log, no extra message
        self.assertEqual(len(self.notes()), 2)
        self.run_cycles(130, sites=(50, 0))    # 65 quiet minutes
        self.run_cycles(2, sites=(100, 0))     # worse again, more than an hour after the last update
        self.assertEqual(len(self.notes()), 3)
        self.run_cycles(140, sites=(100, 0))   # 70 more minutes
        self.run_cycles(2, sites=(50, 0))      # better
        self.assertEqual(len(self.notes()), 4)
        self.assertIn("🟠⬆️", self.text(self.last()))
        self.assertIn("improving, not fully recovered", self.text(self.last()))
        self.assertTrue(self.last()["subject"].startswith("🟠"))
        self.run_cycles(2)                     # fully recovered
        self.assertTrue(self.last()["subject"].startswith("✅"))

    def test_fluctuating_unresolved_problem_for_six_hours_stays_quiet(self):
        pattern = [100, 100, 50, 50, 50, 100, 50, 50, 100, 100, 100, 50]
        self.run_cycles(3)
        for i in range(720):
            self.cycle(sites=(pattern[(i // 1) % len(pattern)], 0))
        self.assertLessEqual(len(self.notes()), 6, [n["subject"] for n in self.notes()])

    def test_mute_and_unmute_messages_explain_themselves(self):
        for i in range(60):
            self.cycle(sites=(100 if i % 4 < 2 else 0, 0))
        unstable = [n for n in self.notes() if "muted" in self.text(n)]
        self.assertEqual(len(unstable), 1)
        self.assertIn("Google", unstable[0]["subject"])
        self.assertTrue(unstable[0]["subject"].startswith("🟡"))
        self.run_cycles(80)
        self.assertIn("stabilized", self.text(self.last()))


    def test_router_that_only_ignores_ping_is_not_called_a_dead_router(self):
        self.run_cycles(3)
        self.run_cycles(2, router=100)
        note = self.last()
        self.assertIn("Router does not answer ping", note["subject"])
        self.assertEqual(note["level"], "warning")
        self.assertNotIn("While it is down", self.text(note))

    def test_dns_outage_is_reported_as_names_not_resolving_not_as_no_internet(self):
        self.run_cycles(3)
        self.run_cycles(2, sites=(100, 100), ip=0, dns=(False, False))  # names fail, pings by IP work
        note = self.last()
        self.assertIn("Names do not resolve", note["subject"])
        self.assertEqual(note["level"], "alert")
        text = self.text(note)
        self.assertNotIn("No internet", text)
        self.assertIn("DNS Google (8.8.8.8)", text)
        self.assertIn("by IP", text)

    def test_all_pings_silent_while_dns_servers_answer_is_not_declared_an_outage(self):
        self.run_cycles(3)
        self.run_cycles(2, sites=(100, 100), ip=100, dns=(True, True))
        note = self.last()
        self.assertNotIn("No internet", self.text(note))
        self.assertIn("External hosts do not answer ping", note["subject"])
        self.assertEqual(note["level"], "warning")

    def test_slowly_alternating_problem_sends_at_most_one_change_message_per_hour(self):
        self.run_cycles(3)
        for phase in range(18):  # 20-minute phases for 6 hours
            for _ in range(40):
                self.cycle(sites=((100, 0) if phase % 2 == 0 else (50, 0)))
        self.assertLessEqual(len(self.notes()), 8, [n["subject"] for n in self.notes()])
        with sqlite3.connect(self.db) as c:  # every transition is still in the event log
            self.assertGreaterEqual(c.execute("SELECT COUNT(*) FROM np_events WHERE target='google.com'").fetchone()[0], 15)

    def test_a_single_worsening_after_a_long_quiet_time_is_still_announced(self):
        self.run_cycles(3)
        self.run_cycles(2, sites=(50, 0))
        self.run_cycles(140, sites=(50, 0))          # 70 minutes of the same problem
        self.run_cycles(2, sites=(100, 0))
        self.assertIn("got worse", self.text(self.last()))


if __name__ == "__main__":
    unittest.main()
