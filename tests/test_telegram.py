"""Telegram delivery straight from the container: configuration, the token staying private, delivery, retries, the API."""
import http.server
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
import netprobe_extensions as npx  # noqa: E402
from fixture_db import make_database  # noqa: E402

HEADERS = {"X-NetProbe-Request": "settings"}
TOKEN = "123456789:AAH-test_token_value_0123456789abc"
CHAT = "-1001234567890"
NL = npx.NEWLINE


class TelegramBase(unittest.TestCase):
    def setUp(self):
        from flask import Flask
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
            os.environ.pop(name, None)
        self.root = Path(self.tmp.name)
        self.db = self.root / "netprobe.sqlite"
        make_database(self.db)
        self.now = 2_000_000_000
        self.ext = npx.Extension(str(self.db), str(self.root), clock=lambda: self.now)
        self.addCleanup(self.ext.stop_telegram)
        self.start_sender = self.ext.start_telegram          # the real one, for the thread test
        self.ext.start_telegram = lambda rounds=None: None   # saving the settings must not start a thread that races the test
        self.sent = []
        self.outcome = (200, {"ok": True, "result": {}})

        def post(url, payload, timeout=None):
            self.sent.append((url, payload))
            if isinstance(self.outcome, Exception):
                raise self.outcome
            return self.outcome
        self.ext.tg_post = post
        app = Flask(__name__)
        npx.install_routes(app, self.ext)
        self.client = app.test_client()
        self.conn = sqlite3.connect(self.db)
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)

    def configure(self, enabled=True, token=TOKEN, chat=CHAT):
        return self.ext.update_telegram({"enabled": enabled, "token": token, "chat_id": chat})

    def notify(self, text="🔴 Alert" + NL + "line two", subject="Internet Health: alert", offset=0):
        with self.ext.db() as conn:
            return self.ext.queue(conn, self.now + offset, "alert", subject, "d", text)

    def tg_of(self):
        return [row["tg"] for row in self.conn.execute("SELECT tg FROM np_notifications ORDER BY id")]


class ConfigTests(TelegramBase):
    def test_off_and_empty_by_default(self):
        view = self.client.get("/api/telegram").get_json()
        self.assertEqual(view, {"enabled": False, "configured": False, "token_set": False, "chat_set": False, "source": None,
                                "last_ok_ts": None, "last_error": None, "last_error_ts": None,
                                "queued": 0, "oldest_ts": None, "retry_ts": None})
        self.assertEqual(self.ext.telegram_tick(), 0)
        self.assertEqual(self.sent, [])

    def test_the_token_is_never_returned_and_the_file_is_private(self):
        response = self.client.post("/api/telegram", json={"enabled": True, "token": TOKEN, "chat_id": CHAT}, headers=HEADERS)
        self.assertEqual(response.status_code, 200)
        for body in (response.get_data(as_text=True), self.client.get("/api/telegram").get_data(as_text=True)):
            self.assertNotIn(TOKEN, body)
            self.assertNotIn("AAH-test", body)
            self.assertNotIn(CHAT, body, "the chat ID is kept private like the token")
        view = response.get_json()
        self.assertEqual((view["enabled"], view["configured"], view["token_set"], view["chat_set"], view["source"]), (True, True, True, True, "ui"))
        self.assertNotIn("chat_id", view)
        self.assertEqual(oct((self.root / "telegram.json").stat().st_mode & 0o777), "0o600")
        self.assertEqual(self.client.get("/api/telegram").headers["Cache-Control"], "no-store")
        for path in ("/api/prefs", "/api/config", "/api/settings"):
            self.assertNotIn(TOKEN, self.client.get(path).get_data(as_text=True), path)

    def test_a_save_without_a_token_keeps_the_saved_one_and_clear_forgets_it(self):
        self.configure()
        self.client.post("/api/telegram", json={"chat_id": "-100999"}, headers=HEADERS)
        self.assertEqual((self.ext.telegram_config()["token"], self.ext.telegram_config()["chat_id"]), (TOKEN, "-100999"))
        view = self.client.post("/api/telegram", json={"clear": True, "enabled": False}, headers=HEADERS).get_json()
        self.assertEqual((view["token_set"], view["chat_set"], view["configured"], view["enabled"]), (False, False, False, False))

    def test_an_empty_token_or_chat_keeps_the_saved_one(self):
        self.configure()
        view = self.client.post("/api/telegram", json={"enabled": True, "token": "", "chat_id": ""}, headers=HEADERS).get_json()
        self.assertEqual((view["token_set"], view["chat_set"], view["configured"]), (True, True, True))
        saved = self.ext.telegram_config()
        self.assertEqual((saved["token"], saved["chat_id"]), (TOKEN, CHAT))

    def test_values_are_validated(self):
        for body in ({"token": "1111"}, {"token": "abc"}, {"chat_id": "chat"}, {"chat_id": "12"}, {"enabled": "yes"}, {"enabled": 1},
                     {"unknown": 1}, {"clear": False}, {"token": 5}, [1], "text"):
            response = self.client.post("/api/telegram", json=body, headers=HEADERS)
            self.assertEqual(response.status_code, 400, body)
        self.assertFalse((self.root / "telegram.json").exists(), "a rejected save writes nothing")
        for good in (CHAT, "123456", "@my_channel"):
            self.assertEqual(self.client.post("/api/telegram", json={"chat_id": good}, headers=HEADERS).status_code, 200, good)

    def test_settings_changes_need_the_same_origin_header(self):
        self.assertEqual(self.client.post("/api/telegram", json={"enabled": True}).status_code, 403)
        self.assertEqual(self.client.post("/api/telegram/test", json={}).status_code, 403)
        self.assertEqual(self.client.post("/api/telegram", json={"enabled": True}, headers={**HEADERS, "Origin": "http://evil.example"}).status_code, 403)
        self.assertEqual(self.sent, [])

    def test_the_environment_wins_and_switches_it_on(self):
        os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"] = TOKEN, CHAT
        view = self.client.get("/api/telegram").get_json()
        self.assertEqual((view["enabled"], view["configured"], view["source"], view["chat_set"]), (True, True, "env", True))
        self.assertNotIn(TOKEN, json.dumps(view))
        self.assertNotIn(CHAT, json.dumps(view))
        self.assertEqual(self.client.post("/api/telegram", json={"token": "999999:" + "B" * 30}, headers=HEADERS).status_code, 400)
        self.assertEqual(self.client.post("/api/telegram", json={"clear": True}, headers=HEADERS).status_code, 400)
        paused = self.client.post("/api/telegram", json={"enabled": False}, headers=HEADERS).get_json()
        self.assertEqual((paused["enabled"], paused["source"]), (False, "env"), "it can still be paused")

    def test_the_sender_starts_with_the_app_only_when_telegram_is_on_and_configured(self):
        from flask import Flask
        starts = []
        with patch.object(self.ext, "start_telegram", lambda rounds=None: starts.append(1)):
            npx.install_routes(Flask("quiet"), self.ext)
            self.assertEqual(starts, [], "off and unconfigured: no thread")
            os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"] = TOKEN, CHAT
            npx.install_routes(Flask("configured"), self.ext)
            self.assertEqual(starts, [1])

    def test_a_bad_environment_value_is_not_used(self):
        os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"] = "1111", CHAT
        self.assertFalse(self.client.get("/api/telegram").get_json()["configured"])
        self.assertEqual(self.ext.telegram_tick(), 0)


class DeliveryTests(TelegramBase):
    def test_only_messages_after_switch_on_are_sent_with_real_new_lines(self):
        self.notify("old" + NL + "message", offset=-100)
        self.configure()
        self.now += 5
        self.notify("🔴 Alert" + NL + "line two" + NL + "Time: now", subject="s", offset=0)
        self.assertEqual(self.ext.telegram_tick(), 1)
        (url, payload), = self.sent
        self.assertEqual(url, "https://api.telegram.org/bot%s/sendMessage" % TOKEN)
        self.assertEqual(payload["chat_id"], CHAT)
        self.assertEqual(payload["text"], "🔴 Alert\nline two\nTime: now")
        self.assertEqual(self.tg_of(), [2, 1], "the old one is skipped, the new one is sent")
        self.assertEqual(self.ext.telegram_tick(), 0, "nothing is sent twice")
        self.assertEqual(len(self.sent), 1)

    def test_unraid_still_gets_the_same_messages(self):
        self.configure()
        self.now += 1
        self.notify()
        self.ext.telegram_tick()
        key = (self.root / "notify-token").read_text().strip()
        pending = self.client.get("/api/notifications/pending", headers={"X-NetProbe-Notify-Key": key}).get_json()["notifications"]
        self.assertEqual(len(pending), 1, "sending to Telegram does not take the message away from the bridge")

    def test_nothing_is_sent_while_off_or_unconfigured(self):
        self.notify()
        self.assertEqual(self.ext.telegram_tick(), 0)
        self.configure(enabled=False)
        self.now += 1
        self.notify()
        self.assertEqual(self.ext.telegram_tick(), 0)
        self.ext.update_telegram({"clear": True, "enabled": True})
        self.assertEqual(self.ext.telegram_tick(), 0)
        self.assertEqual(self.sent, [])

    def test_a_failure_keeps_the_message_backs_off_and_then_delivers(self):
        self.configure()
        self.now += 1
        self.notify()
        self.outcome = (401, {"ok": False, "description": "Unauthorized"})
        self.assertEqual(self.ext.telegram_tick(), 0)
        self.assertEqual(self.tg_of(), [0])
        view = self.ext.telegram_view()
        self.assertIn("Telegram refused the message (401): Unauthorized", view["last_error"])
        self.assertEqual(view["last_error_ts"], self.now)
        self.ext.telegram_tick()
        self.assertEqual(len(self.sent), 1, "no second attempt inside the back-off")
        self.now += 30
        self.ext.telegram_tick()
        self.assertEqual(len(self.sent), 2)
        self.now += 59
        self.ext.telegram_tick()
        self.assertEqual(len(self.sent), 2, "the wait doubled to a minute")
        self.outcome = (200, {"ok": True})
        self.now += 2
        self.assertEqual(self.ext.telegram_tick(), 1)
        self.assertEqual(self.tg_of(), [1])
        view = self.ext.telegram_view()
        self.assertEqual((view["last_ok_ts"], view["last_error"]), (self.now, None), "a success clears the error")

    def test_telegrams_own_wait_request_is_honoured_and_capped(self):
        self.configure()
        self.now += 1
        self.notify()
        self.outcome = (429, {"ok": False, "description": "Too Many Requests: retry after 40", "parameters": {"retry_after": 40}})
        self.ext.telegram_tick()
        self.now += 39
        self.ext.telegram_tick()
        self.assertEqual(len(self.sent), 1)
        self.now += 2
        self.ext.telegram_tick()
        self.assertEqual(len(self.sent), 2)

    def test_network_errors_are_reported_without_the_token(self):
        self.configure()
        self.now += 1
        self.notify()
        self.outcome = OSError("failed to reach https://api.telegram.org/bot%s/sendMessage" % TOKEN)
        self.ext.telegram_tick()
        error = self.ext.telegram_view()["last_error"]
        self.assertIn("not reachable", error)
        self.assertNotIn(TOKEN, error)
        self.assertNotIn(TOKEN, (self.root / "telegram-state.json").read_text())

    def test_a_message_older_than_a_day_is_given_up(self):
        self.configure()
        self.now += 1
        self.notify()
        self.now += npx.TELEGRAM_MAX_AGE + 5
        self.assertEqual(self.ext.telegram_tick(), 0)
        self.assertEqual((self.tg_of(), self.sent), ([2], []))

    def test_a_burst_is_sent_in_order_a_batch_at_a_time(self):
        self.configure()
        self.now += 1
        for i in range(12):
            self.notify("message %d" % i, offset=i)
        with patch("time.sleep"):
            self.assertEqual(self.ext.telegram_tick(), npx.TELEGRAM_BATCH)
            self.assertEqual(self.ext.telegram_tick(), 2)
        self.assertEqual([p["text"] for _, p in self.sent], ["message %d" % i for i in range(12)])

    def test_the_background_thread_delivers_and_stops(self):
        self.notify()
        self.now += 1
        self.configure()
        self.now += 1
        self.notify("second")
        thread = self.start_sender(rounds=1)
        self.assertIs(self.start_sender(), thread, "one sender per process")
        thread.join(5)
        self.assertEqual([p["text"] for _, p in self.sent], ["second"])


class LateDeliveryTests(TelegramBase):
    def setUp(self):
        super().setUp()
        os.environ["TZ"] = "UTC"
        time.tzset()
        self.addCleanup(lambda: (os.environ.pop("TZ", None), time.tzset()))
        self.configure()
        self.now += 1

    def test_a_prompt_message_is_sent_as_it_is(self):
        self.notify("🔴 No internet")
        self.now += npx.TELEGRAM_LATE - 1
        self.assertEqual(self.ext.telegram_tick(), 1)
        self.assertEqual(self.sent[0][1]["text"], "🔴 No internet")

    def test_a_message_that_waited_says_when_it_happened_and_how_late_it_is(self):
        event = self.now  # 2033-05-18 03:33:21 UTC
        self.notify("🔴 No internet" + NL + "Time: 03:33")
        self.now += 12 * 60 + 30
        self.assertEqual(self.ext.telegram_tick(), 1)
        self.assertEqual(self.sent[0][1]["text"], "🔴 No internet\nTime: 03:33\n\n⏱ Delivered 12 min late: the event happened at 03:33")
        self.assertEqual(npx.Extension.late_note(event, event + 3 * 3600 + 5 * 60), "⏱ Delivered 3 h 05 min late: the event happened at 03:33")

    def test_the_date_is_added_when_the_event_was_on_another_day(self):
        self.assertEqual(npx.Extension.late_note(self.now, self.now + 86400),
                         "⏱ Delivered 24 h 00 min late: the event happened at 03:33, 18.05")

    def test_an_outage_delivers_every_message_in_order_with_its_own_delay(self):
        self.notify("down", offset=0)
        self.notify("up", offset=420)
        self.outcome = OSError("network is unreachable")
        for _ in range(3):
            self.ext.telegram_tick()
            self.now += 900
        self.assertEqual(self.tg_of(), [0, 0])
        self.outcome = (200, {"ok": True})
        self.now += 900
        with patch("time.sleep"):
            self.assertEqual(self.ext.telegram_tick(), 2)
        texts = [p["text"] for _, p in self.sent[-2:]]  # (the earlier entries are the attempts that failed)
        self.assertTrue(texts[0].startswith("down\n\n⏱ Delivered ") and texts[0].endswith("the event happened at 03:33"), texts[0])
        self.assertTrue(texts[1].startswith("up\n\n⏱ Delivered ") and texts[1].endswith("the event happened at 03:40"), texts[1])

    def test_the_note_survives_the_length_limit(self):
        self.notify("x" * 5000)
        self.now += 600
        self.ext.telegram_tick()
        text = self.sent[0][1]["text"]
        self.assertEqual(len(text), 4096)
        self.assertTrue(text.endswith("⏱ Delivered 10 min late: the event happened at 03:33"))

    def test_the_queue_is_shown_with_its_oldest_message_and_the_next_attempt(self):
        view = self.ext.telegram_view()
        self.assertEqual((view["queued"], view["oldest_ts"], view["retry_ts"]), (0, None, None))
        self.notify("one", offset=0)
        self.notify("two", offset=5)
        view = self.ext.telegram_view()
        self.assertEqual((view["queued"], view["oldest_ts"], view["retry_ts"]), (2, self.now, None))
        self.outcome = (401, {"ok": False, "description": "Unauthorized"})
        self.ext.telegram_tick()
        view = self.client.get("/api/telegram").get_json()
        self.assertEqual((view["queued"], view["retry_ts"]), (2, self.now + 30))
        self.now += 31
        self.outcome = (200, {"ok": True})
        with patch("time.sleep"):
            self.ext.telegram_tick()
        view = self.ext.telegram_view()
        self.assertEqual((view["queued"], view["oldest_ts"], view["retry_ts"]), (0, None, None))

    def test_expired_and_skipped_messages_are_not_counted(self):
        self.notify("old", offset=0)
        self.now += npx.TELEGRAM_MAX_AGE + 10
        self.assertEqual(self.ext.telegram_view()["queued"], 0, "too old to be sent, so not waiting")
        self.ext.update_telegram({"enabled": False})
        self.notify("while off")
        self.assertEqual(self.ext.telegram_view()["queued"], 0, "nothing waits while Telegram is off")


class TestMessageTests(TelegramBase):
    def test_the_button_uses_the_form_values_without_saving_them(self):
        response = self.client.post("/api/telegram/test", json={"token": TOKEN, "chat_id": CHAT}, headers=HEADERS)
        self.assertEqual((response.status_code, response.get_json()["ok"]), (200, True))
        (url, payload), = self.sent
        self.assertIn("/bot%s/" % TOKEN, url)
        self.assertEqual(payload["chat_id"], CHAT)
        self.assertIn("test message", payload["text"])
        self.assertFalse((self.root / "telegram.json").exists(), "testing does not save")
        self.assertIsNotNone(self.ext.telegram_view()["last_ok_ts"])

    def test_without_values_the_saved_ones_are_used_and_a_refusal_is_a_readable_502(self):
        self.configure()
        self.outcome = (400, {"ok": False, "description": "Bad Request: chat not found"})
        response = self.client.post("/api/telegram/test", json={}, headers=HEADERS)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json()["error"], "Telegram refused the message (400): Bad Request: chat not found")
        self.assertNotIn(TOKEN, response.get_data(as_text=True))

    def test_missing_configuration_and_junk_bodies(self):
        response = self.client.post("/api/telegram/test", json={}, headers=HEADERS)
        self.assertEqual(response.status_code, 502)
        self.assertIn("not configured", response.get_json()["error"])
        for body in ({"token": 5}, {"other": 1}):
            self.assertEqual(self.client.post("/api/telegram/test", json=body, headers=HEADERS).status_code, 400, body)
        self.assertEqual(self.sent, [])


class WireTests(unittest.TestCase):
    """telegram_post against a real local HTTP server: success, Telegram's JSON errors, and a non-JSON answer."""
    def serve(self, status, body):
        seen = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                seen.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"]))), self.headers["Content-Type"]))
                self.send_response(status)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass
        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.addCleanup(server.server_close)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        return "http://127.0.0.1:%d/botX/sendMessage" % server.server_port, seen

    def test_success_and_error_bodies_are_parsed(self):
        url, seen = self.serve(200, b'{"ok": true, "result": {}}')
        self.assertEqual(npx.telegram_post(url, {"chat_id": "1", "text": "hi"}), (200, {"ok": True, "result": {}}))
        self.assertEqual(seen, [("/botX/sendMessage", {"chat_id": "1", "text": "hi"}, "application/json")])
        url, _ = self.serve(403, b'{"ok": false, "description": "Forbidden: bot was blocked by the user"}')
        self.assertEqual(npx.telegram_post(url, {})[1]["description"], "Forbidden: bot was blocked by the user")
        url, _ = self.serve(502, b"<html>bad gateway</html>")
        self.assertEqual(npx.telegram_post(url, {}), (502, {}))

    def test_an_unreachable_server_raises_for_the_caller_to_report(self):
        with self.assertRaises(OSError):
            npx.telegram_post("http://127.0.0.1:1/botX/sendMessage", {}, timeout=2)


class MigrationTests(unittest.TestCase):
    def test_an_old_notification_table_gets_the_column_and_keeps_its_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_database(root / "netprobe.sqlite")
            conn = sqlite3.connect(root / "netprobe.sqlite")
            conn.execute("""CREATE TABLE np_notifications (id INTEGER PRIMARY KEY, ts INTEGER NOT NULL UNIQUE, level TEXT NOT NULL,
                subject TEXT NOT NULL, message TEXT NOT NULL, acked INTEGER NOT NULL DEFAULT 0, description TEXT NOT NULL DEFAULT '')""")
            conn.execute("INSERT INTO np_notifications(ts,level,subject,message) VALUES (1,'alert','old','old message')")
            conn.commit()
            conn.close()
            ext = npx.Extension(str(root / "netprobe.sqlite"), str(root))
            with ext.db() as db:
                self.assertEqual([tuple(r) for r in db.execute("SELECT subject,tg FROM np_notifications")], [("old", 0)])


class IntegrationSwitchTests(TelegramBase):
    """Unraid and Telegram are independent integrations: each can be on or off, any combination."""
    def pending(self):
        key = (self.root / "notify-token").read_text().strip()
        return self.client.get("/api/notifications/pending", headers={"X-NetProbe-Notify-Key": key}).get_json()

    def unraid(self, on):
        response = self.client.post("/api/prefs", json={"notify_unraid": on}, headers=HEADERS)
        self.assertEqual(response.status_code, 200)

    def test_unraid_is_on_by_default(self):
        self.assertIs(self.ext.prefs()["notify_unraid"], True)
        self.notify()
        self.assertEqual(len(self.pending()["notifications"]), 1)

    def test_switched_off_the_bridge_receives_nothing_but_still_sees_the_monitor_alive(self):
        self.notify()
        self.unraid(False)
        body = self.pending()
        self.assertEqual(body["notifications"], [])
        self.assertIn("latest_probe_ts", body)
        self.assertIn("process_started_at", body)
        self.assertEqual(body["max_sample_age"], 180, "the bridge's health check keeps working")
        self.assertEqual(self.tg_of(), [0])
        self.assertEqual(self.conn.execute("SELECT acked FROM np_notifications").fetchone()[0], 0)

    def test_switching_it_back_on_does_not_replay_what_happened_meanwhile(self):
        self.unraid(False)
        self.notify("while off", offset=0)
        self.notify("while off too", offset=1)
        self.unraid(True)
        self.assertEqual(self.pending()["notifications"], [])
        self.now += 5
        self.notify("after")
        self.assertEqual([n["message"] for n in self.pending()["notifications"]], ["after"])
        self.unraid(True)  # saving the same value again changes nothing
        self.assertEqual(len(self.pending()["notifications"]), 1)

    def test_telegram_only(self):
        self.unraid(False)
        self.configure()
        self.now += 1
        self.notify("only telegram")
        self.assertEqual(self.ext.telegram_tick(), 1)
        self.assertEqual(self.pending()["notifications"], [])

    def test_unraid_only(self):
        self.now += 1
        self.notify("only unraid")
        self.assertEqual(self.ext.telegram_tick(), 0)
        self.assertEqual([n["message"] for n in self.pending()["notifications"]], ["only unraid"])
        self.assertEqual(self.sent, [])

    def test_everything_off_delivers_nowhere_and_keeps_the_history(self):
        self.unraid(False)
        self.configure(enabled=False)
        self.now += 1
        self.notify("nobody listens")
        self.assertEqual((self.pending()["notifications"], self.ext.telegram_tick(), self.sent), ([], 0, []))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM np_notifications").fetchone()[0], 1)

    def test_the_global_notifications_switch_silences_both(self):
        self.configure()
        self.now += 1
        self.notify()
        self.ext.update_settings({"enabled": False})
        self.assertEqual((self.pending()["notifications"], self.ext.telegram_tick(), self.sent), ([], 0, []))
        self.ext.update_settings({"enabled": True})
        self.assertEqual(self.ext.telegram_tick(), 1)

    def test_the_preference_is_a_real_boolean(self):
        for bad in ("false", 0, None):
            self.assertEqual(self.client.post("/api/prefs", json={"notify_unraid": bad}, headers=HEADERS).status_code, 400)
        self.assertIs(self.ext.prefs()["notify_unraid"], True)


if __name__ == "__main__":
    unittest.main()
