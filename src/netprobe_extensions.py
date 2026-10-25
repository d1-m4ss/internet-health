"""Additive storage and state-change alerts for the installed NetProbe app."""
from concurrent.futures import ThreadPoolExecutor
import csv
from contextlib import contextmanager
import gzip
from datetime import datetime, timedelta, timezone
import hmac
import http.client
import io
import ipaddress
import json
import logging
import math
import os
import re
from pathlib import Path
import secrets
import sqlite3
import ssl
import statistics
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo


VERSION = "1.0.2-dev"  # the only place: scripts/release.py sets it; GET /api/about and the page footer show it

DEFAULTS = dict(enabled=True, loss_pct=10.0, latency_ms=150.0,
                jitter_ms=80.0, dns_ms=250.0, confirm_samples=2, recovery_samples=2, turbo=False)
SWITCHES = ("enabled", "turbo")  # booleans; toggling them never restarts confirmation streaks
MAX_RANGE = 31 * 86400
# Flood control: a target that changes confirmed state more than FLAP_MAX times in
# FLAP_WINDOW seconds is muted (events are still recorded) until it stays unchanged
# for FLAP_QUIET seconds. Only one "unstable" and one "stable again" message are sent.
FLAP_WINDOW = 3600
FLAP_MAX = 4
FLAP_LONG_WINDOW = 6 * 3600  # slow oscillation: more than FLAP_LONG_MAX changes in six hours
FLAP_LONG_MAX = 8
FLAP_QUIET = 1800
MAX_MESSAGE = 3500
LOSS_GAP = 180        # seconds between lossy samples that still belong to one episode
LOSS_EPISODES = 100   # episodes returned per request (the summary still counts all of them)
LOSS_ROWS = 400000    # raw rows scanned per request
# Turbo mode: one echo every TURBO_INTERVAL s to the router and up to TURBO_HOSTS internet IPs, independent of
# the regular probe loop. An outage needs TURBO_CONFIRM failed rounds and ends after TURBO_RECOVER good ones;
# it is reported once, after recovery, unless the regular monitor already alerted for it.
TURBO_INTERVAL = 5
TURBO_CONFIRM = 2
TURBO_RECOVER = 2
TURBO_HOSTS = 2
TURBO_MAX_PER_HOUR = 3
TURBO_WAIT_MAX = 300  # longest wait for the regular cycle that was running at recovery
TURBO_LOOKBACK = 120  # a regular alert stamped up to this long before the outage start still covers it
TURBO_EXPIRE = 3600   # a recovered outage not decided within an hour (turbo off, no watcher) is never sent
TURBO_OUTAGES = 100   # outages returned by /api/loss
TURBO_ICON = "⚡"
# Speedtest service: "" keeps what the container variable SPEEDTEST_BACKEND says. python = speedtest.net servers
# through speedtest-cli, ookla = the official Ookla CLI (a binary the user installs), fast = fast.com (Netflix).
SPEEDTEST_SERVICES = ("", "python", "ookla", "fast")
# Speedtest schedule: container = the container variables SPEEDTEST_ENABLED / SPEEDTEST_INTERVAL decide (the default),
# off = no automatic tests, interval = every N hours since the last test, daily = at fixed local times.
SPEEDTEST_SCHEDULES = ("container", "off", "interval", "daily")
SPEEDTEST_CATCHUP = 7200  # a daily test missed by up to two hours (container was restarting) still runs
SPEEDTEST_TIMES_MAX = 4
SPEED_ICON = "📶"
SPEED_DROP_CHOICES = (0, 20, 30, 50)  # percent below the 7-day median that counts as a drop; 0 = off
SPEED_DROP_WINDOW = 7 * 86400
SPEED_DROP_MIN_TESTS = 3              # earlier tests of the same service needed before a "typical" speed exists
SPEED_NAMES = {"python": "Speedtest.net", "ookla": "Ookla CLI", "fast": "Fast.com"}
TIME_OF_DAY = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
# History retention: 0 keeps everything (the default). A positive value deletes records older than that many days
# once a day in the quiet hours (CLEANUP_HOURS of the APP_TIMEZONE day); the same ranges can be cleaned on demand.
RETENTION_CHOICES = (0, 7, 30, 90, 180, 365)
CLEANUP_HOURS = (3, 4, 5)
CLEANUP_EVERY = 86400
CLEANUP_CATCHUP = 2 * 86400  # a night missed (container stopped) is made up at any hour after two days
CLEANUP_BATCH = 20000        # rows per DELETE statement, so writers are never locked out for long
CLEANUP_PAUSE = 0.05
CLEANUP_LOCK_TIMEOUT = 5     # seconds VACUUM waits for the probe loop's writes before it gives up
CLEANUP_ARM_DELAY = 3 * 3600  # after choosing a period the first run is at least this much later, in a quiet window
CLEANUP_VACUUM_MIN = 1000    # rows deleted before the file is compacted
CLEANUP_VACUUM_MAX = 256 * 1024 * 1024  # larger databases are not VACUUMed in the background (it needs free space and time)
# (table, condition on the cutoff); tables that are missing are skipped. State tables (np_state, np_flap) are never cleaned,
# queued notifications that were not delivered yet and outages still being decided are kept.
CLEANUP_TABLES = (("measurements", "ts < ?"), ("dns_measurements", "ts < ?"), ("speedtests", "ts < ?"),
                  ("np_targets", "ts < ?"), ("np_speedtest_extra", "ts < ?"), ("np_events", "ts < ?"), ("np_notifications", "(acked=1 OR tg=1) AND ts < ?"),
                  ("np_outages", "status NOT IN ('open','pending') AND COALESCE(end_ts,last_ts) < ?"))
PREFS_DEFAULTS = dict(speedtest_service="", speedtest_schedule="container", speedtest_interval_h=6,
                      speedtest_times=["03:00"], speedtest_notify=False, show_public_ip=False, wan_check_min=15, wan_notify=False,
                      history_retention_days=0, notify_unraid=True, speedtest_notify_manual=False, speedtest_drop_pct=0)
# Public IP and provider (opt-in: nothing is requested while show_public_ip is off). ipwho.is answers with JSON
# (address and ISP name); the plain-text services after it only know the address.
PUBLIC_IP_SOURCES = ("https://ipwho.is/", "https://api.ipify.org", "https://icanhazip.com")
PUBLIC_IP_ONLY = ("https://api.ipify.org", "https://icanhazip.com")  # cheap address-only checks between full lookups
WAN_CHECK_CHOICES = (0, 1, 15)  # minutes between address checks; 0 = only when the Refresh button is pressed
WAN_GRACE = 120        # seconds a page view tolerates a late check before it starts a lookup itself
WAN_CHECK_EVERY = 60   # granularity of the watcher thread (the chosen interval is a multiple of it)
WAN_FULL_EVERY = 86400  # the provider name is refreshed at least this often, or at once when the address changes
WAN_ICON = "🌐"
WAN_NOTIFY_MAX_PER_HOUR = 6  # a link that keeps flapping must not flood the phone
WAN_MAX_AGE = 900      # a probe sample is tagged with the last known address only while it was confirmed this recently
                       # (at least; with a longer check interval one missed check is tolerated)
PUBLIC_IP_RETRY = 60   # seconds before another attempt after every source failed
PUBLIC_IP_TIMEOUT = 5
# Telegram (optional, sent straight from the container with the Bot API; the same messages Unraid gets through the bridge).
TELEGRAM_API = "https://api.telegram.org"
TELEGRAM_TOKEN = re.compile(r"^\d{4,}:[A-Za-z0-9_-]{20,}$")
TELEGRAM_CHAT = re.compile(r"^(-?\d{3,20}|@[A-Za-z][A-Za-z0-9_]{4,31})$")
TELEGRAM_POLL = 5       # seconds between looks at the queue (only while Telegram is on and configured)
TELEGRAM_TIMEOUT = 10
TELEGRAM_MAX_AGE = 86400  # a message that could not be delivered for a day is given up
TELEGRAM_BATCH = 10
GZIP_TYPES = ("text/html", "application/json", "text/csv")  # compressed when the browser accepts gzip
GZIP_MIN = 1024
# fast.com client: the same public endpoints the fast.com page uses, no account or key.
FAST_PAGE = "https://fast.com/"
FAST_API = "https://api.fast.com/netflix/speedtest/v2"
FAST_SERVERS = 5         # closest Netflix servers used for the test
FAST_DOWN_CONNECTIONS = 8  # one connection is limited by latency and slow start; fast.com itself opens many
FAST_UP_CONNECTIONS = 4
FAST_DOWN_SECONDS = 8    # measured window per direction (the first FAST_WARMUP seconds are ramp-up and not counted)
FAST_UP_SECONDS = 8
FAST_WARMUP = 2
FAST_CHUNK = 25_000_000  # bytes asked per download request (fast.com uses up to 26 MB)
FAST_UPLOAD_BODY = 1_000_000
FAST_TIMEOUT = 12
logger = logging.getLogger(__name__)
CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f\\]+")  # also drops backslashes: Unraid agents expand \\n escapes
NEWLINE = "\\n"  # Unraid email/Telegram agents split the message on the two characters backslash + n
STATE_ICON = {"DOWN": "🔴", "DEGRADED": "🟠", "OK": "🟢"}
STATE_TEXT = {"DOWN": "not responding", "DEGRADED": "working with interruptions", "OK": "working"}


def turbo_targets(router_ip, sites, dns_servers):
    """(router or None, internet IPs): IP-literal SITES first, else public DNS servers; never private addresses."""
    def public(value):
        try:
            return ipaddress.ip_address(value).is_global
        except ValueError:
            return False
    router = (router_ip or "").strip() or None
    internet = [s for s in sites if public(s) and s != router] or [s for s in dns_servers if public(s) and s != router]
    return router, list(dict.fromkeys(internet))[:TURBO_HOSTS]


def system_ping(address):
    """True when one ICMP echo is answered within a second (-q: no reverse DNS lookups)."""
    try:
        return subprocess.run(["ping", "-q", "-c", "1", "-W", "1", address], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=3, check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def ping_address(host, output):
    match = re.search(r"^PING .*?\(([^)]+)\)", output, re.MULTILINE)
    candidate = match.group(1) if match else host
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return None
    return candidate


class CleanupBusy(RuntimeError):
    """Another history cleanup is running."""


class FastTestError(RuntimeError):
    """A fast.com measurement could not be completed; the message is shown in the UI."""


def _fast_get(url, timeout=FAST_TIMEOUT, limit=4_000_000):
    request = urllib.request.Request(url, headers={"User-Agent": "Internet-Health"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(limit).decode("utf-8", "replace")


def fetch_public_ip(url, timeout=PUBLIC_IP_TIMEOUT):
    """(address, ISP name or None) the internet sees, from one service; ValueError when the answer is unusable."""
    host = urlsplit(url).hostname
    request = urllib.request.Request(url, headers={"User-Agent": "Internet-Health", "Accept": "application/json, text/plain"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        text = response.read(16384).decode("utf-8", "replace").strip()
    isp = None
    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except ValueError:
            raise ValueError("%s did not answer with valid JSON" % host) from None
        if not isinstance(payload, dict) or payload.get("success") is not True:
            raise ValueError("%s reported a failed lookup" % host)
        text = str(payload.get("ip") or "")
        connection = payload.get("connection") if isinstance(payload.get("connection"), dict) else {}
        name = connection.get("isp") or connection.get("org")
        if isinstance(name, str):
            isp = " ".join(CONTROL_CHARS.sub(" ", name).split())[:120] or None
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        raise ValueError("%s did not answer with an IP address" % host) from None
    if not address.is_global:
        raise ValueError("%s answered with a non-public address" % host)
    return str(address), isp


def fast_targets(page=FAST_PAGE, api=FAST_API, count=FAST_SERVERS):
    """(client info, [target urls with their locations]) from fast.com's own API."""
    try:
        html = _fast_get(page)
        script = re.search(r'src="(/?app-[\w.-]+\.js)"', html)
        if not script:
            raise FastTestError("fast.com page layout changed: no application script found")
        token = re.search(r'token:"([A-Za-z0-9=_-]{8,})"', _fast_get(urljoin(page, script.group(1))))
        if not token:
            raise FastTestError("fast.com page layout changed: no API token found")
        data = json.loads(_fast_get("%s?https=true&token=%s&urlCount=%d" % (api, token.group(1), count)))
    except FastTestError:
        raise
    except (OSError, ValueError, http.client.HTTPException) as exc:
        raise FastTestError("fast.com is not reachable: %s" % (str(exc) or type(exc).__name__)) from exc
    targets = [t for t in (data.get("targets") or []) if isinstance(t, dict) and isinstance(t.get("url"), str)
               and urlsplit(t["url"]).scheme in ("http", "https") and urlsplit(t["url"]).hostname]
    if not targets:
        raise FastTestError("fast.com returned no test servers")
    return (data.get("client") if isinstance(data.get("client"), dict) else {}), targets[:count]


def _fast_connect(url):
    parts = urlsplit(url)
    if parts.scheme == "https":
        return http.client.HTTPSConnection(parts.hostname, parts.port, timeout=FAST_TIMEOUT, context=ssl.create_default_context())
    return http.client.HTTPConnection(parts.hostname, parts.port, timeout=FAST_TIMEOUT)


def _fast_path(url, size=None):
    """Request path of a target; size adds fast.com's "/range/0-<size>" so the server sends that many bytes."""
    parts = urlsplit(url)
    path = parts.path
    if size is not None:
        path = path.replace("/speedtest", "/speedtest/range/0-%d" % size, 1) if "/range/" not in path else path.replace("/range/", "/range/0-%d" % size, 1)
    return path + ("?" + parts.query if parts.query else "")


def fast_latency(url, rounds=6):
    """Median time in ms of a one-byte request on an open connection (the first, with TLS setup, is dropped)."""
    conn = _fast_connect(url)
    times = []
    try:
        for _ in range(rounds):
            start = time.perf_counter()
            conn.request("GET", _fast_path(url, 0), headers={"User-Agent": "Internet-Health"})
            response = conn.getresponse()
            response.read()
            if response.status >= 400:
                raise FastTestError("fast.com server answered HTTP %d" % response.status)
            times.append((time.perf_counter() - start) * 1000.0)
    finally:
        conn.close()
    return statistics.median(times[1:] or times), (statistics.pstdev(times[1:]) if len(times) > 2 else None)


class _Counter:
    def __init__(self):
        self.lock = threading.Lock()
        self.total = 0

    def add(self, n):
        with self.lock:
            self.total += n


def _fast_run(workers, seconds, warmup, clock=time.monotonic):
    """Run workers(deadline, counter) in threads; -> Mbit/s counted after the warm-up (decimal, like fast.com)."""
    counter, errors, start = _Counter(), [], clock()
    deadline = start + seconds

    def guard(fn):
        try:
            fn(deadline, counter)
        except Exception as exc:  # reported only when nothing was transferred at all
            errors.append(exc)

    threads = [threading.Thread(target=guard, args=(w,), daemon=True) for w in workers]
    for thread in threads:
        thread.start()
    time.sleep(max(0.0, warmup - (clock() - start)))
    mark_bytes, mark_time = counter.total, clock()
    for thread in threads:
        thread.join(max(0.1, deadline + FAST_TIMEOUT - clock()))
    end_bytes, end_time = counter.total, min(clock(), deadline)
    if end_time <= mark_time or end_bytes <= mark_bytes:  # too short for a warm-up: use the whole run
        mark_bytes, mark_time = 0, start
    if end_bytes <= mark_bytes:
        raise FastTestError(("fast.com transfer failed: %s" % errors[0]) if errors else "fast.com transfer failed: no data")
    return (end_bytes - mark_bytes) * 8 / (end_time - mark_time) / 1e6


FAST_LOADED_EVERY = 0.4   # seconds between latency probes while a transfer runs
FAST_LOADED_MIN = 3       # fewer answers than this is not a measurement


def _fast_loaded_probe(url, delay, out, clock=time.monotonic):
    """A worker for _fast_run: once the ramp-up is over, a one-byte request every FAST_LOADED_EVERY s on its own connection; the times
    (ms) go to `out`. It measures what the same link costs while it is busy; failures only mean fewer samples."""
    def run(deadline, _counter):
        conn = _fast_connect(url)
        try:
            conn.request("GET", _fast_path(url, 0), headers={"User-Agent": "Internet-Health"})
            conn.getresponse().read()   # the first request pays for the TLS handshake and is not counted
            time.sleep(max(0.0, delay))
            while clock() < deadline - 0.2:
                start = time.perf_counter()
                conn.request("GET", _fast_path(url, 0), headers={"User-Agent": "Internet-Health"})
                response = conn.getresponse()
                response.read()
                if response.status < 400:
                    out.append((time.perf_counter() - start) * 1000.0)
                time.sleep(FAST_LOADED_EVERY)
        finally:
            conn.close()
    return run


def _median_or_none(samples):
    return statistics.median(samples) if len(samples) >= FAST_LOADED_MIN else None


def fast_download(urls, seconds=FAST_DOWN_SECONDS, warmup=FAST_WARMUP, connections=FAST_DOWN_CONNECTIONS, loaded=None):
    def worker(url):
        def run(deadline, counter):
            while time.monotonic() < deadline:
                conn = _fast_connect(url)
                try:
                    conn.request("GET", _fast_path(url, FAST_CHUNK), headers={"User-Agent": "Internet-Health"})
                    response = conn.getresponse()
                    if response.status >= 400:
                        raise FastTestError("fast.com server answered HTTP %d" % response.status)
                    while time.monotonic() < deadline:
                        chunk = response.read(65536)
                        if not chunk:
                            break
                        counter.add(len(chunk))
                finally:
                    conn.close()
        return run
    workers = [worker(urls[i % len(urls)]) for i in range(connections)]
    if loaded is not None:
        workers.append(_fast_loaded_probe(urls[0], warmup, loaded))
    return _fast_run(workers, seconds, warmup)


def fast_upload(urls, seconds=FAST_UP_SECONDS, warmup=FAST_WARMUP, connections=FAST_UP_CONNECTIONS, loaded=None):
    body = os.urandom(FAST_UPLOAD_BODY)

    def worker(url):
        def run(deadline, counter):
            conn = _fast_connect(url)
            try:
                while time.monotonic() < deadline:
                    conn.request("POST", _fast_path(url), body=body, headers={
                        "User-Agent": "Internet-Health", "Content-Type": "application/octet-stream"})
                    response = conn.getresponse()
                    response.read()
                    if response.status >= 400:
                        raise FastTestError("fast.com server answered HTTP %d" % response.status)
                    counter.add(len(body))
            finally:
                conn.close()
        return run
    workers = [worker(urls[i % len(urls)]) for i in range(connections)]
    if loaded is not None:
        workers.append(_fast_loaded_probe(urls[0], warmup, loaded))
    return _fast_run(workers, seconds, warmup)


def run_fast_test(page=FAST_PAGE, api=FAST_API, down_seconds=FAST_DOWN_SECONDS, up_seconds=FAST_UP_SECONDS, warmup=FAST_WARMUP):
    """One fast.com measurement shaped like the app's other backends (ping, download, upload, server)."""
    client, targets = fast_targets(page, api)
    urls = [t["url"] for t in targets]
    ping, jitter = fast_latency(urls[0])
    loaded_down, loaded_up = [], []
    download = fast_download(urls, down_seconds, min(warmup, down_seconds / 2), loaded=loaded_down)
    try:
        upload = fast_upload(urls, up_seconds, min(warmup, up_seconds / 2), loaded=loaded_up)
    except FastTestError as exc:  # download is still a real result; upload stays unknown
        logger.warning("fast.com upload failed: %s", exc)
        upload = None
    where = targets[0].get("location") if isinstance(targets[0].get("location"), dict) else {}
    host = urlsplit(urls[0]).hostname
    return {"ping_ms": ping, "download_mbps": download, "upload_mbps": upload,
            "server": {"id": None, "name": "Netflix, " + where["city"] if where.get("city") else "Netflix", "host": host,
                       "country": where.get("country")},
            "jitter_ms": jitter, "packet_loss_pct": None, "isp": client.get("isp"), "result_url": None,
            "loaded_ping_ms": _median_or_none(loaded_down), "loaded_up_ping_ms": _median_or_none(loaded_up),
            "protocol": "https", "secure": True}


class TelegramError(Exception):
    pass


def telegram_post(url, payload, timeout=TELEGRAM_TIMEOUT):
    """POST JSON to the Telegram Bot API -> (HTTP status, answer as a dict). Network failures raise OSError."""
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": "Internet-Health"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status, raw = response.status, response.read(65536)
    except urllib.error.HTTPError as exc:
        with exc:
            status, raw = exc.code, exc.read(65536)
    try:
        body = json.loads(raw)
    except ValueError:
        body = {}
    return status, body if isinstance(body, dict) else {}


class Extension:
    def __init__(self, db_path, data_dir, clock=time.time, roles=None):
        self.db_path = str(db_path)
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.process_started_at = int(clock())
        self.lock = threading.RLock()
        self.roles = dict(roles or {})  # target -> (role, display name); roles: gateway|router|site|dns
        self.settings_path = self.data_dir / "alert-settings.json"
        self.prefs_path = self.data_dir / "netprobe-prefs.json"
        self.cleanup_path = self.data_dir / "history-cleanup.json"
        self.wan_state_path = self.data_dir / "wan-state.json"
        self.speed_state_path = self.data_dir / "speedtest-state.json"  # whether the last scheduled test was a "drop" (one message per episode)
        self.telegram_path = self.data_dir / "telegram.json"          # token, chat, on/off (written 0600, never sent to the browser)
        self.telegram_state_path = self.data_dir / "telegram-state.json"  # last success / last error, shown in the UI
        self.tg_post = telegram_post  # replaced in tests
        self.tg_fails, self.tg_retry_at = 0, 0
        self.tg_thread, self.tg_stop = None, threading.Event()
        self.wan_pending = None   # (address, provider) seen once; a change is reported when the next check sees it again
        self.wan_checked_at = None  # when the watcher last checked (its interval is counted from here)
        self.cleanup_lock = threading.Lock()
        if not self.settings_path.exists():
            self._write_settings(DEFAULTS)
        self.token = self._load_token(self.data_dir / "notify-token")
        self.ip_fetch = fetch_public_ip  # replaced in tests
        self.ip_lock = threading.Lock()
        self.ip_cache = None
        self.ip_thread = None
        self.turbo_router, self.turbo_hosts = None, []
        self.turbo = dict(fails=0, oks=0, first_fail=None, first_ok=None, kind=None, open_id=None)
        self.last_record_at = None  # wall time the regular loop last recorded a cycle
        self.turbo_available = False  # True once the watcher thread runs
        with self.db() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS np_targets (
                    id INTEGER PRIMARY KEY, cycle_ts INTEGER NOT NULL,
                    ts INTEGER NOT NULL, target TEXT NOT NULL, address TEXT,
                    kind TEXT NOT NULL, latency REAL, jitter REAL, loss REAL,
                    UNIQUE(cycle_ts, target));
                CREATE INDEX IF NOT EXISTS np_targets_target_ts ON np_targets(target, ts);
                CREATE INDEX IF NOT EXISTS np_targets_ts ON np_targets(ts);
                CREATE TABLE IF NOT EXISTS np_state (
                    target TEXT PRIMARY KEY, active TEXT NOT NULL,
                    candidate TEXT NOT NULL, streak INTEGER NOT NULL,
                    last_ts INTEGER NOT NULL, since_ts INTEGER NOT NULL,
                    reasons TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS np_events (
                    id INTEGER PRIMARY KEY, ts INTEGER NOT NULL,
                    target TEXT NOT NULL, previous TEXT NOT NULL,
                    state TEXT NOT NULL, reasons TEXT NOT NULL, metrics TEXT NOT NULL,
                    notified INTEGER NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS np_events_ts ON np_events(ts);
                CREATE TABLE IF NOT EXISTS np_notifications (
                    id INTEGER PRIMARY KEY, ts INTEGER NOT NULL UNIQUE,
                    level TEXT NOT NULL, subject TEXT NOT NULL, message TEXT NOT NULL,
                    acked INTEGER NOT NULL DEFAULT 0, description TEXT NOT NULL DEFAULT '');

                -- what the native speedtests table has no columns for: jitter, packet loss and the delay under load (by test time)
                CREATE TABLE IF NOT EXISTS np_speedtest_extra (
                    ts INTEGER PRIMARY KEY, jitter_ms REAL, packet_loss_pct REAL, loaded_down_ms REAL, loaded_up_ms REAL);

                CREATE TABLE IF NOT EXISTS np_flap (
                    target TEXT PRIMARY KEY, muted INTEGER NOT NULL,
                    last_change_ts INTEGER NOT NULL, cleared_ts INTEGER NOT NULL DEFAULT 0);

                -- status: open -> pending (waiting for the regular cycle) -> sent | covered (the regular monitor
                -- alerted) | off (alerts disabled) | capped (flood limit; later "counted" in the next message);
                -- stopped: turbo was switched off or the process restarted while the outage was open.
                CREATE TABLE IF NOT EXISTS np_outages (
                    id INTEGER PRIMARY KEY, start_ts INTEGER NOT NULL, end_ts INTEGER,
                    last_ts INTEGER NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL, sent_ts INTEGER);
                CREATE INDEX IF NOT EXISTS np_outages_start ON np_outages(start_ts);
            """)
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(np_targets)")}
            for column in ("wan_ip", "wan_isp"):  # the address and provider that carried the sample (only with the external-IP option on)
                if column not in columns:
                    conn.execute("ALTER TABLE np_targets ADD COLUMN %s TEXT" % column)
            notify_columns = {row["name"] for row in conn.execute("PRAGMA table_info(np_notifications)")}
            if "tg" not in notify_columns:  # 0 = not yet sent to Telegram, 1 = sent, 2 = skipped (older than the switch-on or a day)
                conn.execute("ALTER TABLE np_notifications ADD COLUMN tg INTEGER NOT NULL DEFAULT 0")
            conn.execute("UPDATE np_outages SET status='stopped',end_ts=last_ts WHERE status='open'")
            self.turbo_pending = bool(conn.execute("SELECT 1 FROM np_outages WHERE status='pending'").fetchone())

    @staticmethod
    def _load_token(path):
        """Return a valid persistent token, creating or repairing the file atomically."""
        for _ in range(3):
            try:
                token = path.read_text().strip()
            except FileNotFoundError:
                token = ""
            if len(token) >= 32:
                return token
            fd, name = tempfile.mkstemp(prefix=".notify-token-", dir=path.parent)
            try:
                with os.fdopen(fd, "w") as handle:
                    handle.write(secrets.token_hex(32) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                if path.exists():
                    os.replace(name, path)  # empty/short leftover from an interrupted start
                else:
                    try:
                        os.link(name, path)  # atomic create; loses cleanly to a concurrent starter
                    except FileExistsError:
                        pass
                    except OSError:  # filesystem without hard links: plain atomic rename (single-worker container)
                        try:
                            os.replace(name, path)
                        except OSError:
                            pass
            finally:
                if os.path.exists(name):
                    os.unlink(name)
        raise ValueError("Invalid persistent notification token")

    @contextmanager
    def db(self):
        conn = sqlite3.connect(self.db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _write_settings(self, values, path=None):
        fd, name = tempfile.mkstemp(prefix=".alert-settings-", dir=self.data_dir)
        try:
            with os.fdopen(fd, "w") as handle:
                json.dump(values, handle, indent=2, allow_nan=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, path or self.settings_path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    @staticmethod
    def validate(values):
        if not isinstance(values, dict) or set(values) - set(DEFAULTS):
            raise ValueError("Unknown or invalid settings")
        for key, value in values.items():
            if key in SWITCHES:
                if type(value) is not bool:
                    raise ValueError(key + " must be a boolean")
            elif key in ("confirm_samples", "recovery_samples"):
                if type(value) is not int or not 1 <= value <= 20:
                    raise ValueError(key + " must be an integer from 1 to 20")
            elif (type(value) not in (int, float)
                  or not 0 < value <= (100 if key == "loss_pct" else 60000)
                  or not math.isfinite(value)):
                raise ValueError(key + " must be a finite positive number")

    def settings(self):
        values = json.loads(self.settings_path.read_text())
        self.validate(values)
        return dict(DEFAULTS, **values)

    def effective_settings(self):
        """Settings for the probe path: a damaged file must never stop history recording."""
        try:
            return self.settings()
        except (OSError, ValueError) as exc:
            logger.error("alert-settings.json unusable (%s); built-in defaults apply until it is fixed", exc)
            return dict(DEFAULTS)

    def update_settings(self, patch):
        self.validate(patch)
        with self.lock:
            old = self.effective_settings()  # a damaged file can always be repaired by a valid POST
            values = dict(old, **patch)
            self._write_settings(values)
            # Only a changed threshold/count invalidates in-flight confirmation streaks.
            # Toggling "enabled" or "turbo" must neither reset them nor discard queued notifications.
            if any(values[k] != old[k] for k in DEFAULTS if k not in SWITCHES):
                with self.db() as conn:
                    conn.execute("UPDATE np_state SET candidate=active, streak=0")
        return values

    # ---- display and speedtest preferences (separate from the alert thresholds) ----------
    @staticmethod
    def validate_prefs(values):
        if not isinstance(values, dict) or set(values) - set(PREFS_DEFAULTS):
            raise ValueError("Unknown or invalid preferences")
        if "speedtest_service" in values and values["speedtest_service"] not in SPEEDTEST_SERVICES:
            raise ValueError("speedtest_service must be one of: " + ", ".join(repr(x) for x in SPEEDTEST_SERVICES))
        if "speedtest_schedule" in values and values["speedtest_schedule"] not in SPEEDTEST_SCHEDULES:
            raise ValueError("speedtest_schedule must be one of: " + ", ".join(SPEEDTEST_SCHEDULES))
        if "speedtest_interval_h" in values and (type(values["speedtest_interval_h"]) is not int
                                                 or not 1 <= values["speedtest_interval_h"] <= 168):
            raise ValueError("speedtest_interval_h must be an integer from 1 to 168")
        if "speedtest_times" in values:
            times = values["speedtest_times"]
            if (not isinstance(times, list) or not 1 <= len(times) <= SPEEDTEST_TIMES_MAX or len(set(times)) != len(times)
                    or not all(isinstance(x, str) and TIME_OF_DAY.match(x) for x in times)):
                raise ValueError("speedtest_times must be 1 to %d different times of day as HH:MM" % SPEEDTEST_TIMES_MAX)
        if "history_retention_days" in values and (type(values["history_retention_days"]) is not int
                                                   or values["history_retention_days"] not in RETENTION_CHOICES):
            raise ValueError("history_retention_days must be one of: " + ", ".join(str(x) for x in RETENTION_CHOICES))
        if "wan_check_min" in values and (type(values["wan_check_min"]) is not int or values["wan_check_min"] not in WAN_CHECK_CHOICES):
            raise ValueError("wan_check_min must be one of: " + ", ".join(str(x) for x in WAN_CHECK_CHOICES))
        if "speedtest_drop_pct" in values and (type(values["speedtest_drop_pct"]) is not int or values["speedtest_drop_pct"] not in SPEED_DROP_CHOICES):
            raise ValueError("speedtest_drop_pct must be one of: " + ", ".join(str(x) for x in SPEED_DROP_CHOICES))
        for key in ("speedtest_notify", "speedtest_notify_manual", "show_public_ip", "wan_notify", "notify_unraid"):
            if key in values and type(values[key]) is not bool:
                raise ValueError(key + " must be a boolean")

    def prefs(self):
        try:
            values = json.loads(self.prefs_path.read_text())
        except FileNotFoundError:
            return dict(PREFS_DEFAULTS)
        self.validate_prefs(values)
        return dict(PREFS_DEFAULTS, **values)

    def effective_prefs(self):
        """Preferences for background work: a damaged file falls back to the defaults instead of stopping it."""
        try:
            return self.prefs()
        except (OSError, ValueError) as exc:
            logger.error("netprobe-prefs.json unusable (%s); defaults apply until it is fixed", exc)
            return dict(PREFS_DEFAULTS)

    def update_prefs(self, patch):
        self.validate_prefs(patch)
        with self.lock:
            old = self.effective_prefs()
            values = dict(old, **patch)
            self._write_settings(values, self.prefs_path)
            if values["history_retention_days"] != old["history_retention_days"]:
                # the first deletion waits for the next quiet night: switching the option on never deletes at once
                state = self.cleanup_state()
                state["armed_at"] = int(self.clock())
                self._write_settings(state, self.cleanup_path)
            if values["notify_unraid"] and not old["notify_unraid"]:
                # switching the Unraid integration back on never replays what happened while it was off
                with self.db() as conn:
                    conn.execute("UPDATE np_notifications SET acked=1 WHERE acked=0")
        return values

    # ---- history retention -------------------------------------------------------------
    def cleanup_state(self):
        try:
            state = json.loads(self.cleanup_path.read_text())
            return state if isinstance(state, dict) else {}
        except (OSError, ValueError):
            return {}

    def db_bytes(self):
        return sum(p.stat().st_size for p in [Path(self.db_path), Path(self.db_path + "-wal")] if p.exists())

    def cleanup(self, days, dry_run=False):
        """Delete records older than `days` days from every history table; with dry_run only count them.
        -> {days, cutoff, dry_run, deleted: {table: rows}, total, bytes_before, bytes_after, ts}"""
        if type(days) is not int or days not in RETENTION_CHOICES or days == 0:
            raise ValueError("days must be one of: " + ", ".join(str(x) for x in RETENTION_CHOICES if x))
        if not self.cleanup_lock.acquire(blocking=False):
            raise CleanupBusy("A history cleanup is already running")
        try:
            now = int(self.clock())
            cutoff = now - days * 86400
            before = self.db_bytes()
            with self.db() as conn:
                tables = [(t, c) for t, c in CLEANUP_TABLES
                          if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()]
                deleted = {t: conn.execute("SELECT COUNT(*) FROM %s WHERE %s" % (t, c), (cutoff,)).fetchone()[0] for t, c in tables}
            if not dry_run:
                for table, condition in tables:
                    while deleted[table]:
                        for attempt in range(20):  # the probe loop writes now and then: wait for its lock instead of failing
                            try:
                                with self.db() as conn:
                                    rows = conn.execute("DELETE FROM %s WHERE rowid IN (SELECT rowid FROM %s WHERE %s LIMIT ?)" % (table, table, condition),
                                                        (cutoff, CLEANUP_BATCH)).rowcount
                                break
                            except sqlite3.OperationalError as exc:
                                if "locked" not in str(exc) or attempt == 19:
                                    raise
                                time.sleep(0.5)
                        if rows < CLEANUP_BATCH:
                            break
                        time.sleep(CLEANUP_PAUSE)
                if sum(deleted.values()) >= CLEANUP_VACUUM_MIN:
                    self.compact()
            result = dict(days=days, cutoff=cutoff, dry_run=dry_run, deleted=deleted, total=sum(deleted.values()),
                          bytes_before=before, bytes_after=self.db_bytes(), ts=now)
            if not dry_run:
                with self.lock:
                    state = self.cleanup_state()
                    state["last"] = {k: result[k] for k in ("ts", "days", "total", "bytes_before", "bytes_after")}
                    self._write_settings(state, self.cleanup_path)
                logger.info("History cleanup: %d records older than %d days deleted (%s), database %d -> %d bytes",
                            result["total"], days, ", ".join("%s %d" % kv for kv in deleted.items() if kv[1]) or "nothing", before, result["bytes_after"])
            return result
        finally:
            self.cleanup_lock.release()

    def compact(self):
        """Give the freed pages back to the file system. Skipped for big databases or when a writer holds the lock."""
        if self.db_bytes() > CLEANUP_VACUUM_MAX:
            return False
        conn = sqlite3.connect(self.db_path, timeout=CLEANUP_LOCK_TIMEOUT, isolation_level=None)
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.execute("VACUUM")
            return True
        except sqlite3.Error as exc:
            logger.warning("History cleanup: the file could not be compacted now (%s); the freed space is reused", exc)
            return False
        finally:
            conn.close()

    def _cleanup_floor(self, state):
        """Earliest instant the next nightly cleanup may start: 18 h after the last one (one per night), or - when none ran
        yet - a few hours after the retention period was chosen, so choosing it never deletes at once."""
        last = (state.get("last") or {}).get("ts", 0)
        return last + 18 * 3600 if last else state.get("armed_at", 0) + CLEANUP_ARM_DELAY

    def next_cleanup(self, now=None):
        """Epoch of the next automatic cleanup (start of the coming quiet window), or None when retention is off."""
        if not self.effective_prefs()["history_retention_days"]:
            return None
        now = int(now if now is not None else self.clock())
        threshold = max(now, self._cleanup_floor(self.cleanup_state()))
        tz = self.tz
        if datetime.fromtimestamp(threshold, tz).hour in CLEANUP_HOURS:
            return int(threshold)
        day = datetime.fromtimestamp(threshold, tz).date()
        for offset in range(0, 3):
            d = day + timedelta(days=offset)
            slot = datetime(d.year, d.month, d.day, CLEANUP_HOURS[0], 0, tzinfo=tz).timestamp()
            if slot >= threshold:
                return int(slot)
        return None

    def maintenance_tick(self, now=None):
        """One check of the automatic cleanup; -> the cleanup result when one ran."""
        days = self.effective_prefs()["history_retention_days"]
        if not days:
            return None
        now = int(now if now is not None else self.clock())
        state = self.cleanup_state()
        night = datetime.fromtimestamp(now, self.tz).hour in CLEANUP_HOURS and now >= self._cleanup_floor(state)
        overdue = now - max((state.get("last") or {}).get("ts", 0), state.get("armed_at", 0)) >= CLEANUP_CATCHUP
        if night or overdue:
            try:
                return self.cleanup(days)
            except CleanupBusy:
                return None
        return None

    def start_maintenance(self, sleep=time.sleep, rounds=None):
        """Background thread: checks every ten minutes whether the nightly cleanup is due (never when retention is off)."""
        def run():
            done = 0
            while rounds is None or done < rounds:
                try:
                    self.maintenance_tick()
                except Exception:
                    logger.exception("History cleanup failed")
                done += 1
                sleep(600)
        thread = threading.Thread(target=run, name="netprobe-maintenance", daemon=True)
        thread.start()
        return thread

    def history_info(self):
        with self.db() as conn:
            oldest = conn.execute("SELECT MIN(ts) FROM np_targets").fetchone()[0]
        return dict(last=self.cleanup_state().get("last"), next_ts=self.next_cleanup(), db_bytes=self.db_bytes(), oldest_ts=oldest)

    def speedtest_service(self):
        return self.effective_prefs()["speedtest_service"]

    def wan_every(self):
        """Seconds between address checks; 0 when the address is only looked up on request."""
        return self.effective_prefs()["wan_check_min"] * 60

    def public_ip(self):
        """The cached internet-facing address and ISP; {enabled: False} without any network access while the option is
        off. The lookup itself runs in a background thread, so a slow service never holds up a web request: a missing
        answer is reported as pending, an expired one is served while a fresh one is fetched. In manual mode nothing is
        ever looked up here: the card shows the last answer (or asks to press Refresh) until the user asks."""
        prefs = self.effective_prefs()
        if not prefs["show_public_ip"]:
            self.ip_cache = None
            return dict(enabled=False)
        every = prefs["wan_check_min"] * 60
        with self.ip_lock:
            now = self.clock()
            cached = self.ip_cache
            if cached:
                fresh = not every or now - cached["checked_at"] < (every + WAN_GRACE if cached["ip"] else PUBLIC_IP_RETRY)
            else:
                fresh = not every
            if not fresh:
                self._start_lookup()
            info = dict(enabled=True, every_min=prefs["wan_check_min"], refreshing=self._lookup_running())
            if cached:
                return dict(cached, **info)
            return dict(info, pending=bool(every), ip=None, isp=None, source=None, checked_at=None, error=None)

    def _lookup_running(self):
        return bool(self.ip_thread and self.ip_thread.is_alive())

    def _start_lookup(self):
        """Caller holds ip_lock. One background lookup at a time."""
        if not self._lookup_running():
            self.ip_thread = threading.Thread(target=self.refresh_public_ip, name="netprobe-public-ip", daemon=True)
            self.ip_thread.start()

    def request_public_ip(self):
        """The Refresh button: start a lookup now (whatever the interval); the answer is polled from public_ip()."""
        if not self.effective_prefs()["show_public_ip"]:
            return dict(enabled=False)
        with self.ip_lock:
            self._start_lookup()
        return self.public_ip()

    def refresh_public_ip(self):
        failures = []
        for url in PUBLIC_IP_SOURCES:
            try:
                address, isp = self.ip_fetch(url)
                result = dict(ip=address, isp=isp, source=urlsplit(url).hostname, checked_at=int(self.clock()), error=None)
                break
            except Exception as exc:
                failures.append("%s: %s" % (urlsplit(url).hostname, str(exc) or type(exc).__name__))
        else:
            logger.warning("Public IP lookup failed: %s", "; ".join(failures))
            result = dict(ip=None, isp=None, source=None, checked_at=int(self.clock()), error="; ".join(failures)[:300])
        with self.ip_lock:
            if self.effective_prefs()["show_public_ip"]:  # switched off meanwhile: keep nothing
                self.ip_cache = result
        if result["ip"]:
            self.wan_observe(result["ip"], result["isp"])

    def current_wan(self):
        """(address, provider) to record with a probe sample: the last confirmed ones, only while the external-IP option is on.
        Never touches the network."""
        cached = self.ip_cache
        prefs = self.effective_prefs()
        if (not cached or not cached.get("ip") or not prefs["show_public_ip"] or not prefs["wan_check_min"]
                or self.clock() - cached["checked_at"] > max(WAN_MAX_AGE, 2 * prefs["wan_check_min"] * 60)):
            return None, None
        return cached["ip"], cached.get("isp")

    def wan_tick(self):
        """One check of the WAN address (made by the watcher at the chosen interval, never in manual mode). A cheap
        address-only request confirms it; the provider lookup (ipwho.is) only runs when the address changed or the name
        is a day old."""
        prefs = self.effective_prefs()
        if not prefs["show_public_ip"] or not prefs["wan_check_min"]:
            return
        now = self.clock()
        self.wan_checked_at = now
        cached = self.ip_cache
        if cached and cached.get("ip") and now - cached["checked_at"] < WAN_FULL_EVERY:
            for url in PUBLIC_IP_ONLY:
                try:
                    address = self.ip_fetch(url)[0]
                except Exception:
                    continue
                if address == cached["ip"]:
                    with self.ip_lock:
                        if self.ip_cache is cached:
                            self.ip_cache = dict(cached, checked_at=int(now))
                    self.wan_observe(address, cached.get("isp"))
                    return
                break  # the address changed: look up the provider too
            else:
                return  # no answer from any service (offline): keep what is known
        self.refresh_public_ip()

    def wan_due(self):
        """Is the watcher's next check due? At the chosen interval; a first sighting of a new address is confirmed a
        minute later instead of waiting for a whole interval."""
        every = self.wan_every()
        if not every or not self.effective_prefs()["show_public_ip"]:
            return False
        return (self.wan_pending is not None or self.wan_checked_at is None
                or self.clock() - self.wan_checked_at >= every - WAN_CHECK_EVERY / 2)

    def wan_state(self):
        try:
            state = json.loads(self.wan_state_path.read_text())
            return state if isinstance(state, dict) and state.get("ip") else None
        except (OSError, ValueError):
            return None

    def wan_observe(self, address, provider):
        """Compare a confirmed address with the last one known; a change of address (confirmed by two checks in a row, so one odd
        answer is not a change) is remembered and, when the user asked for it, announced. A provider name that merely changed
        its spelling with the same address is not a change."""
        if not self.effective_prefs()["show_public_ip"]:
            return None
        now = int(self.clock())
        known = self.wan_state()
        if known is None:
            self._write_settings(dict(ip=address, isp=provider, since=now), self.wan_state_path)
            self.wan_pending = None
            return None
        if address == known["ip"]:
            self.wan_pending = None
            if provider and provider != known.get("isp"):
                self._write_settings(dict(known, isp=provider), self.wan_state_path)
            return None
        if self.wan_pending is None or self.wan_pending[0] != address:
            self.wan_pending = (address, provider)  # first sighting
            return None
        provider = provider or self.wan_pending[1]
        self.wan_pending = None
        self._write_settings(dict(ip=address, isp=provider, since=now), self.wan_state_path)
        return self.notify_wan_change(known, dict(ip=address, isp=provider), now)

    def notify_wan_change(self, before, after, now):
        """Queue the message about a changed WAN, when asked for, alerts are on and the hourly limit is not used up."""
        prefs = self.effective_prefs()
        if not prefs["wan_notify"] or not prefs["wan_check_min"] or not self.effective_settings()["enabled"]:
            return None  # (manual mode does not watch the link, so it cannot announce a change)
        name = lambda isp: " ".join(CONTROL_CHARS.sub(" ", isp or "").split())[:120] or "provider not reported"
        changed = bool(before.get("isp") and after.get("isp") and before["isp"] != after["isp"])
        clock = lambda t: time.strftime("%H:%M, %d.%m", time.localtime(t))
        title = ("WAN changed: " + name(after.get("isp"))) if changed else "External IP changed" + (" (%s)" % name(after["isp"]) if after.get("isp") else "")
        lines = ["%s %s" % (WAN_ICON, title), "",
                 "• Before: %s · %s%s" % (name(before.get("isp")), before["ip"], " (since %s)" % clock(before["since"]) if before.get("since") else ""),
                 "• Now: %s · %s" % (name(after.get("isp")), after["ip"]), "",
                 "The router may have switched to another link, or the provider assigned a new address." if changed else
                 "The provider assigned a new address, or the router switched links.",
                 "", "Time: " + time.strftime("%H:%M, %d.%m.%Y %Z", time.localtime(now))]
        with self.lock, self.db() as conn:
            recent = conn.execute("SELECT COUNT(*) FROM np_notifications WHERE subject LIKE ? AND ts>?", (WAN_ICON + "%", now - 3600)).fetchone()[0]
            if recent >= WAN_NOTIFY_MAX_PER_HOUR:
                logger.warning("WAN changed again; the hourly notification limit is used up")
                return None
            return self.queue(conn, now, "warning" if changed else "normal", ("%s NetProbe: %s" % (WAN_ICON, title))[:120],
                              ("%s: %s → %s" % (title, before["ip"], after["ip"]))[:240], NEWLINE.join(lines)[:MAX_MESSAGE])

    def start_wan_watcher(self, sleep=time.sleep, rounds=None):
        """Background thread behind the WAN history; idle (no requests) while the option is off."""
        def run():
            done = 0
            while rounds is None or done < rounds:
                try:
                    if self.wan_due():
                        self.wan_tick()
                except Exception:
                    logger.exception("WAN address check failed")
                done += 1
                sleep(WAN_CHECK_EVERY)
        thread = threading.Thread(target=run, name="netprobe-wan", daemon=True)
        thread.start()
        return thread

    def forget_wan(self):
        """Erase the recorded addresses and providers from all samples; -> rows changed."""
        with self.lock, self.db() as conn:
            return conn.execute("UPDATE np_targets SET wan_ip=NULL,wan_isp=NULL WHERE wan_ip IS NOT NULL OR wan_isp IS NOT NULL").rowcount

    # ---- Telegram ---------------------------------------------------------------------
    @staticmethod
    def _read_json(path):
        try:
            value = json.loads(Path(path).read_text())
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def telegram_config(self):
        """Effective Telegram settings. TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID in the environment win over the file (and then
        switch it on unless the file says otherwise), so a container template can carry them."""
        saved = self._read_json(self.telegram_path)
        env_token, env_chat = os.getenv("TELEGRAM_BOT_TOKEN", "").strip(), os.getenv("TELEGRAM_CHAT_ID", "").strip()
        from_env = bool(env_token and env_chat)
        token = env_token if from_env else str(saved.get("token") or "")
        chat = env_chat if from_env else str(saved.get("chat_id") or "")
        enabled = saved["enabled"] if isinstance(saved.get("enabled"), bool) else from_env
        armed = saved.get("armed_at") if type(saved.get("armed_at")) is int else 0
        ready = bool(TELEGRAM_TOKEN.match(token) and TELEGRAM_CHAT.match(chat))
        return dict(enabled=enabled, token=token, chat_id=chat, source="env" if from_env else ("ui" if token or chat else None),
                    armed_at=armed, ready=ready)

    def telegram_view(self):
        """What the browser may see: never the token itself."""
        cfg, state = self.telegram_config(), self._read_json(self.telegram_state_path)
        return dict(enabled=cfg["enabled"], configured=cfg["ready"], token_set=bool(cfg["token"]), chat_id=cfg["chat_id"], source=cfg["source"],
                    last_ok_ts=state.get("last_ok_ts"), last_error=state.get("last_error"), last_error_ts=state.get("last_error_ts"))

    def update_telegram(self, patch):
        """patch: any of enabled (bool), token (str, empty = keep), chat_id (str), clear (True forgets token and chat)."""
        if not isinstance(patch, dict) or set(patch) - {"enabled", "token", "chat_id", "clear"}:
            raise ValueError("Unknown or invalid Telegram settings")
        if "enabled" in patch and type(patch["enabled"]) is not bool:
            raise ValueError("enabled must be a boolean")
        if "clear" in patch and patch["clear"] is not True:
            raise ValueError("clear must be true")
        for key in ("token", "chat_id"):
            if key in patch and not isinstance(patch[key], str):
                raise ValueError(key + " must be a string")
        token, chat = (patch.get("token") or "").strip(), (patch.get("chat_id") or "").strip() if "chat_id" in patch else None
        if token and not TELEGRAM_TOKEN.match(token):
            raise ValueError("The bot token looks like 123456789:AAH… (from @BotFather)")
        if chat is not None and chat and not TELEGRAM_CHAT.match(chat):
            raise ValueError("The chat ID is a number such as -1001234567890 (or @channelname)")
        with self.lock:
            before = self.telegram_config()
            if before["source"] == "env" and (token or chat or patch.get("clear")):
                raise ValueError("The token and chat are set by the container environment; change them there")
            saved = self._read_json(self.telegram_path)
            if patch.get("clear"):
                saved.pop("token", None), saved.pop("chat_id", None)
            if token:
                saved["token"] = token
            if chat is not None:
                saved["chat_id"] = chat
            if "enabled" in patch:
                saved["enabled"] = patch["enabled"]
            after_enabled = saved["enabled"] if isinstance(saved.get("enabled"), bool) else before["enabled"]
            if after_enabled and not before["enabled"]:
                saved["armed_at"] = int(self.clock())  # switching it on never replays old messages
            self._write_settings(saved, self.telegram_path)
            view = self.telegram_view()
        if view["enabled"] and view["configured"]:
            self.start_telegram()
        return view

    def _telegram_note(self, error=None, now=None):
        now = int(now if now is not None else self.clock())
        state = self._read_json(self.telegram_state_path)
        if error is None:
            state.update(last_ok_ts=now)
            state.pop("last_error", None), state.pop("last_error_ts", None)
        else:
            state.update(last_error=error[:300], last_error_ts=now)
        try:
            self._write_settings(state, self.telegram_state_path)
        except OSError:
            logger.warning("Telegram state could not be saved")

    def telegram_send(self, text, token=None, chat_id=None):
        """Send one text through the Bot API; TelegramError carries a readable reason (never containing the token)."""
        cfg = self.telegram_config()
        token, chat_id = token or cfg["token"], chat_id or cfg["chat_id"]
        if not (TELEGRAM_TOKEN.match(token or "") and TELEGRAM_CHAT.match(chat_id or "")):
            raise TelegramError("Telegram is not configured: a bot token and a chat ID are needed")
        scrub = lambda message: str(message).replace(token, "***")
        try:
            status, body = self.tg_post("%s/bot%s/sendMessage" % (TELEGRAM_API, token),
                                        {"chat_id": chat_id, "text": text[:4096], "disable_web_page_preview": True}, TELEGRAM_TIMEOUT)
        except Exception as exc:
            raise TelegramError("api.telegram.org is not reachable: " + scrub(str(exc) or type(exc).__name__)) from None
        if status == 200 and body.get("ok") is True:
            return
        reason = " ".join(CONTROL_CHARS.sub(" ", str(body.get("description") or "")).split())[:200]
        error = TelegramError("Telegram refused the message (%s): %s" % (status, scrub(reason) or "no details"))
        retry = (body.get("parameters") or {}).get("retry_after") if isinstance(body.get("parameters"), dict) else None
        error.retry_after = retry if type(retry) is int and 0 < retry <= 3600 else None
        raise error

    def telegram_test(self, token=None, chat_id=None):
        """The Send test button: uses the values from the form when given, else the saved ones."""
        self.telegram_send("✅ Internet Health: test message\nTelegram is connected; alerts will arrive in this chat.", token, chat_id)
        self._telegram_note()

    def telegram_tick(self):
        """Deliver queued notifications that Telegram has not seen yet (oldest first, a few per round, ~1 per second);
        -> number sent. A failure backs off (30 s doubling to 15 min) and keeps the message; after a day it is given up."""
        cfg = self.telegram_config()
        if not (cfg["enabled"] and cfg["ready"]) or not self.effective_settings()["enabled"]:
            return 0  # (the global "notifications enabled" switch of the alert settings silences every integration)
        now = int(self.clock())
        if now < self.tg_retry_at:
            return 0
        sent = 0
        with self.db() as conn:
            # not for messages from before it was switched on, nor ones too old to matter
            conn.execute("UPDATE np_notifications SET tg=2 WHERE tg=0 AND ts<?", (max(cfg["armed_at"], now - TELEGRAM_MAX_AGE),))
            rows = conn.execute("SELECT id,subject,message FROM np_notifications WHERE tg=0 ORDER BY id LIMIT ?", (TELEGRAM_BATCH,)).fetchall()
        for row in rows:
            text = (row["message"] or "").replace(NEWLINE, "\n").strip() or row["subject"]
            try:
                self.telegram_send(text)
            except TelegramError as exc:
                self.tg_fails += 1
                delay = getattr(exc, "retry_after", None) or min(900, 30 * 2 ** (self.tg_fails - 1))
                self.tg_retry_at = now + delay
                if self.tg_fails == 1:
                    logger.warning("Telegram delivery failed: %s", exc)
                self._telegram_note(str(exc), now)
                return sent
            with self.db() as conn:
                conn.execute("UPDATE np_notifications SET tg=1 WHERE id=?", (row["id"],))
            self.tg_fails, sent = 0, sent + 1
            self._telegram_note(None, now)
            if len(rows) > 1:
                time.sleep(1)  # the Bot API allows about one message a second per chat
        return sent

    def start_telegram(self, rounds=None):
        """Background sender; started when Telegram is configured and on (at startup or when it is switched on). One per process."""
        with self.lock:
            if self.tg_thread and self.tg_thread.is_alive():
                return self.tg_thread
            self.tg_stop.clear()

            def run():
                done = 0
                while not self.tg_stop.is_set() and (rounds is None or done < rounds):
                    try:
                        self.telegram_tick()
                    except Exception:
                        logger.exception("Telegram delivery round failed")
                    done += 1
                    self.tg_stop.wait(TELEGRAM_POLL)
            self.tg_thread = threading.Thread(target=run, name="netprobe-telegram", daemon=True)
            self.tg_thread.start()
            return self.tg_thread

    def stop_telegram(self):
        self.tg_stop.set()

    # ---- scheduled speedtests ---------------------------------------------------------
    @property
    def tz(self):
        """Time zone of the schedule: the container's APP_TIMEZONE (UTC when unset or unknown)."""
        name = os.getenv("APP_TIMEZONE", "UTC") or "UTC"
        try:
            return ZoneInfo(name)
        except Exception:
            logger.warning("APP_TIMEZONE %r is unknown; the speedtest schedule uses UTC", name)
            return ZoneInfo("UTC")

    def last_speedtest_ts(self):
        with self.db() as conn:
            return conn.execute("SELECT COALESCE(MAX(ts),0) FROM speedtests").fetchone()[0]

    def schedule_slots(self, times, now):
        """Scheduled instants (epoch) of yesterday, today and tomorrow, ascending."""
        tz = self.tz
        today = datetime.fromtimestamp(now, tz).date()
        slots = []
        for offset in (-1, 0, 1):
            day = today + timedelta(days=offset)
            for text in times:
                slots.append(datetime(day.year, day.month, day.day, int(text[:2]), int(text[3:]), tzinfo=tz).timestamp())
        return sorted(slots)

    def speedtest_due(self, now, last_attempt=0):
        """None: the container variables decide. True/False: the schedule chosen in the UI says run / do not run."""
        try:
            prefs = self.effective_prefs()
            mode = prefs["speedtest_schedule"]
            if mode == "container":
                return None
            if mode == "off":
                return False
            last = max(last_attempt, self.last_speedtest_ts())
            if mode == "interval":
                return now - last >= prefs["speedtest_interval_h"] * 3600
            slot = max((x for x in self.schedule_slots(prefs["speedtest_times"], now) if x <= now), default=None)
            return slot is not None and last < slot and now - slot <= SPEEDTEST_CATCHUP
        except Exception:
            logger.exception("Speedtest schedule could not be evaluated; no test is started")
            return False

    def next_speedtest(self, now):
        """Epoch of the next scheduled test, or None (container variables / schedule off)."""
        prefs = self.effective_prefs()
        if prefs["speedtest_schedule"] == "interval":
            return max(now, self.last_speedtest_ts() + prefs["speedtest_interval_h"] * 3600)
        if prefs["speedtest_schedule"] == "daily":
            return min((x for x in self.schedule_slots(prefs["speedtest_times"], now) if x > now), default=None)
        return None

    def notify_speedtest(self, result=None, error=None, backend=None, manual=False):
        """After a finished (result) or failed (error) test: the completion message when asked for (scheduled tests: speedtest_notify,
        manual ones: speedtest_notify_manual) and, for scheduled results, the drop check against the 7-day median."""
        prefs = self.effective_prefs()
        if not self.effective_settings()["enabled"]:
            return None
        sent = None
        if prefs["speedtest_notify_manual" if manual else "speedtest_notify"]:
            sent = self._speedtest_message(result, error, backend, manual)
        if result is not None and not manual and prefs["speedtest_drop_pct"]:
            try:
                self._speedtest_drop(result, prefs["speedtest_drop_pct"])
            except Exception:
                logger.exception("Speedtest drop check failed")
        return sent

    def _speedtest_message(self, result, error, backend, manual):
        ts = int(self.clock())
        service = SPEED_NAMES.get((result or {}).get("backend") or backend, "Speedtest")
        kind = "Manual" if manual else "Scheduled"
        stamp = lambda t, fmt: datetime.fromtimestamp(t, self.tz).strftime(fmt)  # the schedule's zone, whatever TZ the process has
        clock = stamp(ts, "%H:%M, %d.%m.%Y %Z")
        if error is not None:
            reason = " ".join(CONTROL_CHARS.sub(" ", str(error)).split())[:300] or "unknown error"
            lines = ["⚠️ %s Speedtest failed (%s)" % (kind, service), "", "• Reason: " + reason, "", "Time: " + clock]
            subject, level = "⚠️ NetProbe: Speedtest failed (%s)" % service, "warning"
            description = "The %s Speedtest (%s) failed: %s" % (kind.lower(), service, reason)
        else:
            def mbps(value):
                return "%.1f Mbps" % value if isinstance(value, (int, float)) else "not measured"
            down, up, ping = result.get("download_mbps"), result.get("upload_mbps"), result.get("ping_ms")
            server = result.get("server") or {}
            where = " ".join(CONTROL_CHARS.sub(" ", ", ".join(str(x) for x in (server.get("name"), server.get("country")) if x)).split())
            lines = ["%s %s Speedtest (%s)" % (SPEED_ICON, kind, service), "",
                     "• Download: " + mbps(down), "• Upload: " + mbps(up),
                     "• Ping: " + ("%.1f ms" % ping if isinstance(ping, (int, float)) else "not measured")]
            for label, key in (("Jitter", "jitter_ms"), ("Ping under load", "loaded_ping_ms")):
                if isinstance(result.get(key), (int, float)):
                    lines.append("• %s: %.1f ms" % (label, result[key]))
            if where:
                lines.append("• Server: " + where)
            with self.db() as conn:
                before = conn.execute("""SELECT ts,download_mbps,upload_mbps FROM speedtests ORDER BY ts DESC LIMIT 1 OFFSET 1""").fetchone()
            if before and before["download_mbps"] is not None:
                lines.append("• Previous: ↓ %.0f / ↑ %s Mbps (%s)" % (
                    before["download_mbps"], "%.0f" % before["upload_mbps"] if before["upload_mbps"] is not None else "—",
                    stamp(before["ts"], "%H:%M, %d.%m")))
            lines += ["", "Time: " + clock]
            subject = "%s NetProbe: Speedtest ↓ %s / ↑ %s" % (
                SPEED_ICON, "%.0f" % down if isinstance(down, (int, float)) else "—",
                "%.0f" % up if isinstance(up, (int, float)) else "—")
            level = "normal"
            description = "%s Speedtest (%s): download %s, upload %s." % (kind, service, mbps(down), mbps(up))
        with self.lock, self.db() as conn:
            return self.queue(conn, ts, level, subject[:120], description[:240], NEWLINE.join(lines)[:MAX_MESSAGE])

    def _speedtest_drop(self, result, pct):
        """Compare a scheduled test with the median of the last 7 days (same service, at least SPEED_DROP_MIN_TESTS earlier tests).
        One warning when the speed falls pct % below it, one message when it is back; nothing in between."""
        ts = int(result.get("timestamp") or self.clock())
        backend = result.get("backend") or ""
        with self.db() as conn:
            rows = conn.execute("""SELECT download_mbps,upload_mbps FROM speedtests
                                   WHERE ts>=? AND ts<? AND COALESCE(backend,'')=?""", (ts - SPEED_DROP_WINDOW, ts, backend)).fetchall()
        usual, dropped = {}, {}
        for name, key in (("Download", "download_mbps"), ("Upload", "upload_mbps")):
            history = [r[key] for r in rows if isinstance(r[key], (int, float)) and r[key] > 0]
            value = result.get(key)
            if len(history) < SPEED_DROP_MIN_TESTS or not isinstance(value, (int, float)):
                continue
            usual[name] = (value, statistics.median(history), len(history))
            if value < usual[name][1] * (1 - pct / 100):
                dropped[name] = usual[name]
        if not usual:
            return None
        state = self._read_json(self.speed_state_path)
        low = state.get("low") is True
        if dropped == {} and not low or dropped and low:
            return None
        service = SPEED_NAMES.get(backend, "Speedtest")
        now = int(self.clock())
        clock = datetime.fromtimestamp(now, self.tz).strftime("%H:%M, %d.%m.%Y %Z")
        rows_text = []
        for name, (value, median, count) in (dropped or usual).items():
            change = (value - median) / median * 100
            rows_text.append("• %s: %.0f Mbps — usually %.0f Mbps (%+.0f %%)" % (name, value, median, change))
        if dropped:
            subject = "%s NetProbe: Speedtest slowed down (%s)" % (SPEED_ICON, service)
            lines = ["%s Speedtest is slower than usual (%s)" % (SPEED_ICON, service), ""] + rows_text + [
                "• Compared with the median of %d tests in the last 7 days" % max(c for _v, _m, c in dropped.values()), "", "Time: " + clock]
            level, description = "warning", "Speed is %d %% or more below the 7-day median (%s)." % (pct, service)
        else:
            subject = "%s NetProbe: Speedtest is back to normal (%s)" % (SPEED_ICON, service)
            lines = ["%s Speedtest is back to normal (%s)" % (SPEED_ICON, service), ""] + rows_text + ["", "Time: " + clock]
            level, description = "normal", "Speed is within the usual range again (%s)." % service
        with self.lock, self.db() as conn:
            queued = self.queue(conn, now, level, subject[:120], description[:240], NEWLINE.join(lines)[:MAX_MESSAGE])
        self._write_settings(dict(state, low=bool(dropped)), self.speed_state_path)
        return queued

    def run_fast_speedtest(self):
        return run_fast_test()

    def save_speedtest_extra(self, ts, result):
        """Keep what a finished test measured besides the native columns (jitter, packet loss, delay under load), by test time."""
        number = lambda v: float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None
        values = (number(result.get("jitter_ms")), number(result.get("packet_loss_pct")),
                  number(result.get("loaded_ping_ms")), number(result.get("loaded_up_ping_ms")))
        if all(v is None for v in values):
            return False
        with self.lock, self.db() as conn:
            conn.execute("INSERT OR REPLACE INTO np_speedtest_extra(ts,jitter_ms,packet_loss_pct,loaded_down_ms,loaded_up_ms) VALUES (?,?,?,?,?)",
                         (int(ts),) + values)
        return True

    def speedtest_series(self, seconds, end=None, limit=2000):
        """Tests of the last `seconds`, oldest first, with the extras; the chart and the history table read this."""
        end = int(end if end is not None else self.clock())
        with self.db() as conn:
            rows = conn.execute("""SELECT s.ts, s.ping_ms, s.download_mbps, s.upload_mbps, s.backend, s.server_name, s.server_country,
                                          e.jitter_ms, e.packet_loss_pct, e.loaded_down_ms, e.loaded_up_ms
                                   FROM speedtests s LEFT JOIN np_speedtest_extra e ON e.ts = s.ts
                                   WHERE s.ts >= ? AND s.ts <= ? ORDER BY s.ts DESC LIMIT ?""", (end - seconds, end, limit)).fetchall()
        return [dict(r) for r in reversed(rows)]

    @staticmethod
    def classify(row, cfg):
        if row["kind"] == "dns":
            if row["latency"] is None:
                return "DOWN", ["No DNS answer"]
            reasons =["DNS %.1f ms" % row["latency"]] if row["latency"] >= cfg["dns_ms"] else []
            return ("DEGRADED" if reasons else "OK"), reasons
        if row["loss"] >= 100:
            return "DOWN", ["No ICMP replies"]
        reasons = []
        for field, setting, label, suffix in (
            ("loss", "loss_pct", "Packet loss", "%"),
            ("latency", "latency_ms", "RTT", "ms"),
            ("jitter", "jitter_ms", "Jitter", "ms"),
        ):
            if row[field] is not None and row[field] >= cfg[setting]:
                reasons.append("%s %.1f %s" % (label, row[field], suffix))
        return ("DEGRADED" if reasons else "OK"), reasons

    def record_probe(self, ts, ping_results, dns_per_server, dns_failed=()):
        if type(ts) is not int or ts <= 0:
            raise ValueError("Invalid probe timestamp")
        rows = []
        for r in ping_results:
            loss = float(r["loss"])
            measured = r.get("measured", loss < 100)
            values = [float(r[k]) if measured and r.get(k) is not None else None for k in ("latency", "jitter")]
            if (not math.isfinite(loss) or not 0 <= loss <= 100 or
                not all(v is None or math.isfinite(v) and v >= 0 for v in values)):
                raise ValueError("Invalid probe metrics")
            rows.append(dict(target=r["host"], address=r.get("address"), kind="ping",
                             ts=int(r.get("sample_ts", ts)), latency=values[0], jitter=values[1], loss=loss))
        failed = set(dns_failed)
        for address, latency in dns_per_server.items():
            if address not in failed and latency is not None and math.isfinite(latency) and latency >= 0:
                rows.append(dict(target="dns:" + address, address=address, kind="dns",
                                 ts=ts, latency=float(latency), jitter=None, loss=None))
        for address in dns_failed:  # no DNS reply at all; any measured latency was only elapsed time
            rows.append(dict(target="dns:" + address, address=address, kind="dns",
                             ts=ts, latency=None, jitter=None, loss=None))
        wan_ip, wan_isp = self.current_wan()
        with self.lock, self.db() as conn:
            cfg = self.effective_settings()
            changes = []
            for row in rows:
                target = row["target"]
                previous = conn.execute("SELECT * FROM np_state WHERE target=?", (target,)).fetchone()
                if previous and ts <= previous["last_ts"]:
                    continue
                conn.execute("""INSERT OR IGNORE INTO np_targets
                    (cycle_ts,ts,target,address,kind,latency,jitter,loss,wan_ip,wan_isp) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (ts, row["ts"], target, row["address"], row["kind"], row["latency"], row["jitter"], row["loss"], wan_ip, wan_isp))
                observed, reasons = self.classify(row, cfg)
                active = previous["active"] if previous else "OK"
                # Leaving OK counts consecutive non-OK samples (DOWN/DEGRADED alternate on lossy links);
                # once a problem is active, DOWN<->DEGRADED needs the exact same state to repeat.
                same = previous and (previous["candidate"] == observed or
                                     (active == "OK" and observed != "OK" and previous["candidate"] != "OK"))
                streak = min(21, previous["streak"] + 1) if same else 1
                since = previous["since_ts"] if previous else ts  # start of the current problem episode
                needed = cfg["recovery_samples"] if observed == "OK" else cfg["confirm_samples"]
                if observed != active and streak >= needed:
                    kind = ("problem" if active == "OK" else "recovered" if observed == "OK"
                            else "worse" if observed == "DOWN" else "better")
                    event = dict(kind=kind, ts=ts, target=target, previous=active, state=observed,
                                 reasons=reasons, metrics=row, duration=ts - since)
                    event["id"] = conn.execute("INSERT INTO np_events(ts,target,previous,state,reasons,metrics) VALUES (?,?,?,?,?,?)",
                                               (ts, target, active, observed, json.dumps(reasons), json.dumps(row))).lastrowid
                    if kind == "problem" or kind == "recovered":
                        since = ts
                    active = observed
                    if cfg["enabled"]:
                        self.route_event(conn, event, changes)
                elif cfg["enabled"] and observed == active:
                    flap = conn.execute("SELECT * FROM np_flap WHERE target=? AND muted=1", (target,)).fetchone()
                    if flap and ts - flap["last_change_ts"] >= FLAP_QUIET:
                        conn.execute("UPDATE np_flap SET muted=0,cleared_ts=? WHERE target=?", (ts, target))
                        changes.append(dict(kind="stable", target=target, state=active, reasons=[]))
                conn.execute("""INSERT INTO np_state VALUES (?,?,?,?,?,?,?)
                    ON CONFLICT(target) DO UPDATE SET active=excluded.active,candidate=excluded.candidate,
                    streak=excluded.streak,last_ts=excluded.last_ts,since_ts=excluded.since_ts,reasons=excluded.reasons""",
                    (target, active, observed, streak, ts, since, json.dumps(reasons)))
            if changes and cfg["enabled"]:
                states = {r["target"]: (r["active"], json.loads(r["reasons"])) for r in
                          conn.execute("SELECT target,active,reasons FROM np_state WHERE last_ts=?", (ts,))}
                self.queue(conn, ts, *self.compose(changes, states, ts))
            self.last_record_at = self.clock()

    @staticmethod
    def queue(conn, ts, level, subject, description, message):
        """Insert a notification; np_notifications.ts is unique, so a taken second moves to the next free one."""
        for offset in range(10):
            if conn.execute("INSERT OR IGNORE INTO np_notifications(ts,level,subject,message,description) VALUES (?,?,?,?,?)",
                            (ts + offset, level, subject, message, description)).rowcount:
                return ts + offset
        logger.error("Notification dropped: seconds %d..%d are all taken", ts, ts + 9)
        return None

    # ---- turbo mode ----------------------------------------------------------------
    def turbo_configure(self, router_ip, internet):
        self.turbo_router, self.turbo_hosts = router_ip or None, list(internet)

    def start_turbo(self, router_ip, sites, dns_servers):
        """Start the turbo watcher thread. It sends no pings while the setting is off."""
        self.turbo_configure(*turbo_targets(router_ip, sites, dns_servers))
        if not self.turbo_hosts:
            logger.warning("Turbo mode unavailable: no public IP among SITES or the DNS servers")
            return None
        thread = threading.Thread(target=self.turbo_run, args=(system_ping, time.sleep), name="netprobe-turbo", daemon=True)
        thread.start()
        self.turbo_available = True
        return thread

    def turbo_run(self, pinger, sleep, rounds=None):
        done = 0
        while rounds is None or done < rounds:
            try:
                self.turbo_tick(pinger)
            except Exception:
                logger.exception("Turbo round failed")
            done += 1
            sleep(TURBO_INTERVAL)

    def turbo_tick(self, pinger):
        """One round: re-read the setting, ping everything in parallel, advance the outage state machine."""
        now = int(self.clock())
        if not self.effective_settings()["turbo"]:
            if self.turbo["open_id"] is not None or self.turbo["fails"] or self.turbo_pending:
                self.turbo_stop(now)  # also decides outages that recovered just before turbo was switched off
            return
        if not self.turbo_hosts:
            return
        addresses = ([self.turbo_router] if self.turbo_router else []) + self.turbo_hosts
        def safe(address):
            try:
                return bool(pinger(address))
            except Exception:
                return False
        with ThreadPoolExecutor(max_workers=len(addresses)) as pool:
            answers = dict(zip(addresses, pool.map(safe, addresses)))
        self.turbo_step(now, answers.get(self.turbo_router) if self.turbo_router else None,
                        [answers[a] for a in self.turbo_hosts])

    def turbo_stop(self, now):
        with self.lock, self.db() as conn:
            if self.turbo["open_id"] is not None:
                conn.execute("UPDATE np_outages SET status='stopped',end_ts=? WHERE id=?", (now, self.turbo["open_id"]))
            self.turbo.update(fails=0, oks=0, first_fail=None, first_ok=None, kind=None, open_id=None)
            self.turbo_flush(conn, now)

    def turbo_step(self, now, router_ok, internet):
        """Advance the outage state machine with one round. internet: one bool per internet IP."""
        if not internet:
            return
        st = self.turbo
        with self.lock, self.db() as conn:
            if any(internet):
                st.update(fails=0, first_fail=None)
                if st["open_id"] is None:
                    st["kind"] = None  # a lone failed round must not colour a later outage
                else:
                    st["oks"] += 1
                    st["first_ok"] = st["first_ok"] or now
                    if st["oks"] >= TURBO_RECOVER:
                        conn.execute("UPDATE np_outages SET status='pending',end_ts=? WHERE id=?", (st["first_ok"], st["open_id"]))
                        self.turbo_pending = True
                        st.update(oks=0, first_ok=None, kind=None, open_id=None)
            else:
                kind = "router" if router_ok is False else "internet"
                st.update(oks=0, first_ok=None)
                if st["open_id"] is not None:
                    st["kind"] = "router" if "router" in (st["kind"], kind) else kind  # report the worst seen
                    conn.execute("UPDATE np_outages SET last_ts=?,kind=? WHERE id=?", (now, st["kind"], st["open_id"]))
                else:
                    st["fails"] += 1
                    st["first_fail"] = st["first_fail"] or now
                    st["kind"] = "router" if "router" in (st["kind"], kind) else kind
                    if st["fails"] >= TURBO_CONFIRM:
                        st["open_id"] = conn.execute("INSERT INTO np_outages(start_ts,last_ts,kind,status) VALUES (?,?,?,'open')",
                                                     (st["first_fail"], now, st["kind"])).lastrowid
                        st.update(fails=0, first_fail=None)
            self.turbo_flush(conn, now)

    def turbo_flush(self, conn, now):
        """Decide closed outages once the regular cycle that was running at recovery has been recorded."""
        rows = conn.execute("SELECT * FROM np_outages WHERE status='pending' ORDER BY id").fetchall()
        self.turbo_pending = False
        for row in rows:
            waited = now - row["end_ts"] >= TURBO_WAIT_MAX
            if not waited and (self.last_record_at is None or self.last_record_at <= row["end_ts"]):
                self.turbo_pending = True
                continue
            cfg = self.effective_settings()
            if now - row["end_ts"] > TURBO_EXPIRE:
                status = "stopped"
            elif not cfg["enabled"]:
                status = "off"
            elif self.turbo_covered(conn, row):
                status = "covered"
            elif conn.execute("SELECT COUNT(*) FROM np_outages WHERE status='sent' AND sent_ts>?",
                              (now - 3600,)).fetchone()[0] >= TURBO_MAX_PER_HOUR:
                status = "capped"
            else:
                status = "sent"
                capped = conn.execute("SELECT COUNT(*),MIN(start_ts) FROM np_outages WHERE status='capped'").fetchone()
                self.queue(conn, now, *self.compose_outage(row, capped[0], capped[1], now))
                conn.execute("UPDATE np_outages SET status='counted' WHERE status='capped'")
            conn.execute("UPDATE np_outages SET status=?,sent_ts=? WHERE id=?", (status, now if status == "sent" else None, row["id"]))

    def turbo_covered(self, conn, row):
        """True when the regular monitor alerted for this outage, or already considered one of the turbo addresses
        DOWN when it began (an alert sent before a restart or before turbo was switched on is never repeated)."""
        if conn.execute("""SELECT 1 FROM np_notifications WHERE level IN ('alert','warning') AND subject NOT LIKE ?
                AND subject NOT LIKE ? AND subject NOT LIKE ? AND subject NOT LIKE ? AND ts>=? AND ts<=?""",
                (TURBO_ICON + "%", "%Speedtest%", SPEED_ICON + "%", WAN_ICON + "%", row["start_ts"] - TURBO_LOOKBACK, row["end_ts"])).fetchone():
            return True
        for target in [t for h in self.turbo_hosts for t in (h, "dns:" + h)]:
            last = conn.execute("SELECT state FROM np_events WHERE target=? AND ts<=? ORDER BY ts DESC,id DESC LIMIT 1",
                                (target, row["start_ts"])).fetchone()
            if last and last["state"] == "DOWN":
                return True
        return False

    @staticmethod
    def short_duration_text(seconds):
        seconds = int(seconds)
        if seconds < 60:
            return "%d s" % seconds
        if seconds < 3600:
            return "%d min %d s" % divmod(seconds, 60) if seconds % 60 else "%d min" % (seconds // 60)
        return Extension.duration_text(seconds)

    def compose_outage(self, row, more, more_since, ts):
        """-> (level, subject, description, message) for one short outage caught by turbo mode."""
        duration = self.short_duration_text(row["end_ts"] - row["start_ts"])
        clock = lambda t: time.strftime("%H:%M:%S", time.localtime(t))
        hosts = CONTROL_CHARS.sub(" ", ", ".join(self.turbo_hosts))
        if row["kind"] == "router":
            title = "Short router outage: " + duration
            failed = "the router (%s) and the internet hosts (%s) did not answer" % (
                CONTROL_CHARS.sub(" ", self.label(self.turbo_router or "")), hosts)
        else:
            title = "Short internet outage: " + duration
            failed = ("the router answered, but no internet host did (%s)" if self.turbo_router
                      else "no internet host answered (%s)") % hosts
        hint = "Too short for the regular checks; caught by Turbo mode."
        lines = ["%s %s" % (TURBO_ICON, title), "",
                 "• From %s to %s (%s)" % (clock(row["start_ts"]), clock(row["end_ts"]), duration),
                 "• What failed: " + failed, "", hint]
        if more:
            lines.append("+%d more short outage%s since %s (Turbo sends at most %d messages per hour)." % (
                more, "s" if more > 1 else "", time.strftime("%H:%M" if ts - more_since < 12 * 3600 else "%H:%M, %d.%m",
                                                             time.localtime(more_since)), TURBO_MAX_PER_HOUR))
        lines += ["", "Time: " + time.strftime("%H:%M, %d.%m.%Y %Z", time.localtime(ts))]
        message = NEWLINE.join(lines)[:MAX_MESSAGE]
        return "warning", ("%s NetProbe: %s" % (TURBO_ICON, title))[:120], (title + ". " + hint)[:240], message

    def outages(self, start, upper):
        with self.db() as conn:
            rows = [dict(r) for r in conn.execute("""SELECT id,start_ts,end_ts,kind,status FROM np_outages
                WHERE start_ts<=? AND COALESCE(end_ts,last_ts)>=? ORDER BY start_ts DESC,id DESC LIMIT ?""",
                (upper, start, TURBO_OUTAGES))]
        for row in rows:
            row["duration"] = row["end_ts"] - row["start_ts"] if row["end_ts"] is not None else None
        return rows

    def route_event(self, conn, event, changes):
        """Decide whether a confirmed state change becomes a user message (flood control)."""
        target = event["target"]
        flap = conn.execute("SELECT * FROM np_flap WHERE target=?", (target,)).fetchone()
        if flap and flap["muted"]:
            conn.execute("UPDATE np_flap SET last_change_ts=? WHERE target=?", (event["ts"], target))
            return
        if event["kind"] in ("worse", "better") and conn.execute(
                """SELECT 1 FROM np_events WHERE target=? AND notified=1 AND previous!='OK' AND state!='OK' AND ts>?""",
                (target, event["ts"] - FLAP_WINDOW)).fetchone():
            return  # a problem that merely shifts between DOWN and DEGRADED: one update per hour is enough
        start_ts = max(event["ts"] - FLAP_WINDOW, flap["cleared_ts"] if flap else 0)
        recent = conn.execute("SELECT COUNT(*) FROM np_events WHERE target=? AND ts>?", (target, start_ts)).fetchone()[0]
        long_ts = max(event["ts"] - FLAP_LONG_WINDOW, flap["cleared_ts"] if flap else 0)
        recent_long = conn.execute("SELECT COUNT(*) FROM np_events WHERE target=? AND ts>?", (target, long_ts)).fetchone()[0]
        if recent > FLAP_MAX or recent_long > FLAP_LONG_MAX:
            conn.execute("""INSERT INTO np_flap(target,muted,last_change_ts) VALUES (?,1,?)
                ON CONFLICT(target) DO UPDATE SET muted=1,last_change_ts=excluded.last_change_ts""", (target, event["ts"]))
            count, window = (recent, FLAP_WINDOW) if recent > FLAP_MAX else (recent_long, FLAP_LONG_WINDOW)
            changes.append(dict(kind="unstable", target=target, state=event["state"], reasons=[], count=count, window=window))
        else:
            conn.execute("UPDATE np_events SET notified=1 WHERE id=?", (event["id"],))
            changes.append(event)

    # ---- readable messages ------------------------------------------------------
    def role(self, target):
        return self.roles.get(target, ("dns" if target.startswith("dns:") else "site", None))[0]

    def short_name(self, target):
        name = self.roles.get(target, (None, None))[1]
        base = target[4:] if target.startswith("dns:") else target
        return CONTROL_CHARS.sub(" ", name or ("DNS server " + base if target.startswith("dns:") else base))

    def label(self, target):
        base = CONTROL_CHARS.sub(" ", target[4:] if target.startswith("dns:") else target)
        name = self.short_name(target)
        return base if name == base else "%s (%s)" % (name, base)

    @staticmethod
    def reason_text(reason):
        if reason == "No ICMP replies":
            return "no ping reply"
        if reason == "No DNS answer":
            return "not answering DNS queries"
        m = re.match(r"(Packet loss|RTT|Jitter|DNS) ([\d.]+)", reason)
        if m:
            kind, value = m.group(1), float(m.group(2))
            return {"Packet loss": "packet loss %.0f%%", "RTT": "latency %.0f ms",
                    "Jitter": "unstable latency (jitter %.0f ms)", "DNS": "slow DNS reply, %.0f ms"}[kind] % value
        return CONTROL_CHARS.sub(" ", reason)

    @staticmethod
    def duration_text(seconds):
        minutes = int(seconds // 60)
        if minutes < 1:
            return "less than a minute"
        if minutes < 60:
            return "%d min" % minutes
        hours, minutes = divmod(minutes, 60)
        if hours < 24:
            return "%d h %d min" % (hours, minutes) if minutes else "%d h" % hours
        days, hours = divmod(hours, 24)
        return "%d d %d h" % (days, hours) if hours else "%d d" % days

    @staticmethod
    def is_ip(target):
        try:
            ipaddress.ip_address(target)
        except ValueError:
            return False
        return True

    def diagnose(self, states):
        """(icon, title, hint, level) from the CURRENT state of every target, not just what changed."""
        bad = {t: st for t, (st, _) in states.items() if st != "OK"}
        by_role = {}
        for t in states:
            by_role.setdefault(self.role(t), []).append(t)
        names = lambda ts: ", ".join(self.short_name(t) for t in ts)
        down = lambda role: [t for t in by_role.get(role, []) if bad.get(t) == "DOWN"]
        weak = lambda role: [t for t in by_role.get(role, []) if t in bad]
        sites, router, dns = (by_role.get(r, []) for r in ("site", "router", "dns"))
        if down("gateway"):
            return ("🔴", "Docker gateway on Unraid is not responding",
                    "The problem is inside Unraid or its Docker network, not at the ISP.", "alert")
        if down("router"):
            if any(self.role(t) in ("site", "dns") for t in bad):
                return ("🔴", "Router is not responding",
                        "Check the router's power and cable. While it is down, the internet and DNS are down too.", "alert")
            return ("🟠", "Router does not answer ping",
                    "The internet and DNS work, so the router is probably ignoring or rate-limiting ping, or ROUTER_IP is wrong. "
                    "If devices on the LAN lose connectivity, check the router itself.", "warning")
        if sites and len(down("site")) == len(sites):
            if any(states[t][0] == "OK" for t in dns):
                return ("🟠", "External hosts do not answer ping",
                        "DNS servers still respond, so the internet may be partly working (for example ICMP is filtered). "
                        "Check whether pages open.", "warning")
            which = "The router responds but external hosts do not. " if router else "External hosts are unreachable. "
            return ("🔴", "No internet", which + "Likely an ISP or line problem (cable, ONU).", "alert")
        ip_sites = [t for t in sites if self.is_ip(t)]
        name_sites = [t for t in sites if not self.is_ip(t)]
        if ip_sites and name_sites and len(down("site")) >= len(name_sites) and all(t in down("site") for t in name_sites) \
                and not any(t in bad for t in ip_sites):
            return ("🔴", "Names do not resolve (DNS)",
                    "Pings by IP address work but host names do not resolve. Check DNS on the router/AdGuard and at the ISP.", "alert")
        if down("site"):
            return ("🟠", "Unreachable: " + names(down("site")),
                    "Other external hosts work - likely a problem with this service or the route to it.", "warning")
        if weak("site"):
            return ("🟠", "Unstable connection: " + names(weak("site")),
                    "Packet loss or high latency to external hosts. The internet works, but with interruptions.", "warning")
        if weak("router"):
            return ("🟠", "Router responds with interruptions",
                    "Packet loss or latency on the path to the router: check the cable or Wi-Fi link and network load.", "warning")
        if weak("gateway"):
            return ("🟠", "Docker gateway on Unraid has interruptions", "Check Unraid load and its network interface.", "warning")
        if weak("dns"):
            if len(weak("dns")) == len(dns):
                return ("🟠", "DNS servers are not working",
                        "Sites may open by IP but not by name. Check DNS on the router and at the ISP.", "warning")
            return ("🟠", "DNS server not responding: " + names(weak("dns")),
                    "The other DNS servers work and sites open. Only noticeable if pages load slowly.", "warning")
        return ("🟠", "Network problems detected", "", "warning")

    def change_line(self, c):
        label, state = self.label(c["target"]), c["state"]
        reasons = ", ".join(self.reason_text(r) for r in c.get("reasons", [])) or STATE_TEXT[state]
        kind = c["kind"]
        if kind == "problem":
            return "%s %s - %s" % (STATE_ICON[state], label, reasons)
        if kind == "worse":
            return "🔴⬇️ %s - got worse: %s" % (label, reasons)
        if kind == "better":
            return "🟠⬆️ %s - improving, not fully recovered: %s" % (label, reasons)
        if kind == "recovered":
            return "🟢 %s - back to normal (problem lasted %s)" % (label, self.duration_text(c["duration"]))
        return "%s %s - connection has stabilized, now: %s" % (STATE_ICON[state], label, STATE_TEXT[state])

    def compose(self, changes, states, ts):
        """-> (level, subject, description, message). Message lines are joined by a literal backslash-n."""
        still_bad = {t: v for t, v in states.items() if v[0] != "OK"}
        kinds = {c["kind"] for c in changes}
        if kinds == {"unstable"} or (kinds <= {"unstable", "stable"} and "unstable" in kinds):
            unstable = [c["target"] for c in changes if c["kind"] == "unstable"]
            icon, level = "🟡", "warning"
            title = "Unstable connection: " + ", ".join(self.short_name(t) for t in unstable)
            hint = ("The state keeps changing, so repeated notifications for these targets are muted. "
                    "Details are in the NetProbe event log.")
        elif still_bad:
            icon, title, hint, level = self.diagnose(states)
            if not kinds & {"problem", "worse"}:
                if "recovered" in kinds:
                    icon, title, level = "🟠", "Partially recovered. " + title, "warning"
                elif "better" in kinds:
                    icon, title, level = "🟠", "Improving, not fully recovered. " + title, "warning"
        else:
            icon, level, hint = "✅", "normal", ""
            title = "Everything is back to normal" if "recovered" in kinds else "Connection has stabilized"
        shown = {c["target"] for c in changes}
        lines = ["%s %s" % (icon, title), "", "What changed:"]
        lines += ["• " + self.change_line(c) for c in changes if c["kind"] != "unstable"]
        groups = {}  # flapping targets of the same state share one line
        for c in changes:
            if c["kind"] == "unstable":
                groups.setdefault(c["state"], []).append(c)
        for state, group in groups.items():
            top = max(group, key=lambda c: c["count"])
            lines.append("• 🟡 %s - state keeps changing (%d times in %s), now: %s. Further notifications are muted "
                         "until %s stable for %d min" % (", ".join(self.short_name(c["target"]) for c in group),
                         top["count"], self.duration_text(top["window"]), STATE_TEXT[state],
                         "it stays" if len(group) == 1 else "they stay", FLAP_QUIET // 60))
        others = [t for t in sorted(still_bad) if t not in shown]
        if others:
            lines += ["", "Still not OK:"]
            for t in others:
                st, rs = still_bad[t]
                lines.append("• %s %s - %s" % (STATE_ICON[st], self.label(t), ", ".join(self.reason_text(r) for r in rs) or STATE_TEXT[st]))
        if hint:
            lines += ["", hint]
        lines += ["", "Time: " + time.strftime("%H:%M, %d.%m.%Y %Z", time.localtime(ts))]
        kept = []  # bound the size on line boundaries
        for i, line in enumerate(lines):
            if len(NEWLINE.join(kept + [line])) > MAX_MESSAGE - 60:
                kept.append("... and %d more lines (see the NetProbe event log)" % (len(lines) - i))
                break
            kept.append(line)
        subject = ("%s NetProbe: %s" % (icon, title))[:120]
        description = (title + (". " + hint if hint else ""))[:240]
        return level, subject, description, NEWLINE.join(kept)

    def started_at(self):
        with self.db() as conn:
            return conn.execute("SELECT MIN(ts) FROM np_targets").fetchone()[0]

    def window_bounds(self, seconds, end=None):
        """(start, upper) of a query window: relative to now by default, or ending at an explicit `end`."""
        return (end if end is not None else int(self.clock())) - seconds, (end if end is not None else 2 ** 53)

    @staticmethod
    def wan_filter(wan):
        """(SQL, params) selecting the samples of one WAN source: wan = (address, provider); an empty address means samples
        recorded without a known WAN; None means no filter."""
        if wan is None:
            return "", []
        address, provider = wan
        if not address:
            return " AND wan_ip IS NULL", []
        return " AND wan_ip=? AND COALESCE(wan_isp,'')=?", [address, provider or ""]

    def recent_targets(self, seconds, end=None, wan=None):
        start, upper = self.window_bounds(seconds, end)
        wan_sql, wan_params = self.wan_filter(wan)
        with self.db() as conn:
            rows = conn.execute("""SELECT target,COUNT(*) samples,AVG(latency) avg_latency,
                MAX(latency) max_latency,AVG(loss) avg_loss,MAX(loss) max_loss
                FROM np_targets WHERE ts>=? AND ts<=?""" + wan_sql + " GROUP BY target ORDER BY target", [start, upper] + wan_params).fetchall()
            out = []
            for r in rows:
                latest = dict(conn.execute("SELECT * FROM np_targets WHERE target=? ORDER BY ts DESC,id DESC LIMIT 1", (r["target"],)).fetchone())
                latest.update(dict(r))
                state = conn.execute("SELECT active,candidate,streak FROM np_state WHERE target=?", (r["target"],)).fetchone()
                # a target without a state row (database edited or restored by hand) is unknown, not a 500 for the whole page
                latest.update(dict(state) if state else dict(active="UNKNOWN", candidate="UNKNOWN", streak=0));latest["state"] = latest.pop("active")
                latest["addresses"] = [dict(x) for x in conn.execute("""SELECT address,COUNT(*) samples,
                    AVG(latency) avg_latency,MAX(latency) max_latency,AVG(loss) avg_loss,MAX(loss) max_loss
                    FROM np_targets WHERE target=? AND ts>=? AND ts<=?""" + wan_sql + " GROUP BY address", [r["target"], start, upper] + wan_params)]
                latest["wan_sources"] = [dict(x) for x in conn.execute("""SELECT wan_ip,wan_isp,COUNT(*) samples,
                    AVG(latency) avg_latency,MAX(latency) max_latency,AVG(loss) avg_loss,MAX(loss) max_loss
                    FROM np_targets WHERE target=? AND ts>=? AND ts<=? GROUP BY wan_ip,wan_isp
                    ORDER BY samples DESC,wan_ip,wan_isp""", (r["target"], start, upper))]
                out.append(latest)
            return out

    def wan_sources(self, seconds, end=None):
        """The WAN sources seen in the period (newest first) and the current one, for the selectors."""
        start, upper = self.window_bounds(seconds, end)
        with self.db() as conn:
            rows = [dict(x) for x in conn.execute("""SELECT wan_ip,wan_isp,COUNT(*) samples,MIN(ts) first_ts,MAX(ts) last_ts
                FROM np_targets WHERE ts>=? AND ts<=? GROUP BY wan_ip,wan_isp ORDER BY last_ts DESC,wan_ip""", (start, upper))]
        cached = self.ip_cache
        current = {"wan_ip": cached["ip"], "wan_isp": cached.get("isp")} if cached and cached.get("ip") else None
        return dict(sources=rows, current=current)

    def target_history(self, target, seconds, address=None, end=None, wan=None):
        step = max(1, math.ceil(seconds / 599))
        condition = "target=?" if address is None else "target=? AND address=?"
        start, upper = self.window_bounds(seconds, end)
        wan_sql, wan_params = self.wan_filter(wan)
        params = [step, start, upper, target]
        if address is not None:
            params.append(address)
        with self.db() as conn:
            return [dict(r) for r in conn.execute("""SELECT CAST(ts/? AS INTEGER) bucket,MAX(ts) ts,
                AVG(latency) latency,AVG(jitter) jitter,AVG(loss) loss,MAX(loss) max_loss,
                CASE WHEN COUNT(DISTINCT address)=1 THEN MAX(address) ELSE NULL END address
                FROM np_targets WHERE ts>=? AND ts<=? AND """ + condition + wan_sql + " GROUP BY bucket ORDER BY bucket", params + wan_params)]

    def history(self, seconds, end=None, wan=None):
        step = max(1, math.ceil(seconds / 599))
        start, upper = self.window_bounds(seconds, end)
        with self.db() as conn:
            if wan is not None:
                # one WAN source: rebuilt from the per-target samples; the native quality score belongs to the whole network and
                # cannot be attributed to a single provider, so it is left out (null) instead of invented
                wan_sql, wan_params = self.wan_filter(wan)
                rows = [dict(r) for r in conn.execute("""SELECT CAST(ts/? AS INTEGER) bucket,MAX(ts) ts,
                    AVG(CASE WHEN kind='ping' THEN latency END) avg_latency_ms,
                    AVG(CASE WHEN kind='ping' THEN jitter END) avg_jitter_ms,
                    AVG(CASE WHEN kind='ping' THEN loss END) avg_loss_pct,
                    MAX(CASE WHEN kind='ping' THEN loss END) max_loss_pct,
                    AVG(CASE WHEN kind='dns' THEN latency END) avg_dns_latency_ms
                    FROM np_targets WHERE ts>=? AND ts<=?""" + wan_sql + " GROUP BY bucket ORDER BY bucket", [step, start, upper] + wan_params)]
                dns = {}
                for r in conn.execute("""SELECT CAST(ts/? AS INTEGER) bucket,target,AVG(latency) latency
                    FROM np_targets WHERE ts>=? AND ts<=? AND kind='dns'""" + wan_sql + " GROUP BY bucket,target", [step, start, upper] + wan_params):
                    dns.setdefault(r["bucket"], {})[r["target"][4:]] = r["latency"]
                for row in rows:
                    row["dns_per_server"] = dns.get(row.pop("bucket"), {})
                    row["score"] = None
                    row["iso"] = datetime.fromtimestamp(row["ts"], timezone.utc).isoformat()
                return rows
            rows = [dict(r) for r in conn.execute("""SELECT CAST(ts/? AS INTEGER) bucket,MAX(ts) ts,
                AVG(avg_latency_ms) avg_latency_ms,AVG(avg_jitter_ms) avg_jitter_ms,
                AVG(avg_loss_pct) avg_loss_pct,MAX(avg_loss_pct) max_loss_pct,
                AVG(avg_dns_latency_ms) avg_dns_latency_ms,AVG(score) score
                FROM measurements WHERE ts>=? AND ts<=? GROUP BY bucket ORDER BY bucket""", (step, start, upper))]
            dns = {}
            for r in conn.execute("""SELECT CAST(ts/? AS INTEGER) bucket,server_ip,AVG(latency_ms) latency
                FROM dns_measurements WHERE ts>=? AND ts<=? GROUP BY bucket,server_ip""", (step, start, upper)):
                dns.setdefault(r["bucket"], {})[r["server_ip"]] = r["latency"]
            for row in rows:
                row["dns_per_server"] = dns.get(row.pop("bucket"), {})
                row["iso"] = datetime.fromtimestamp(row["ts"], timezone.utc).isoformat()
            return rows

    def loss_report(self, seconds, end=None, target=None):
        """Packet-loss episodes per ping target in a window. An episode is a run of lossy samples
        (loss > 0) that is not interrupted by a clean sample or by a gap longer than LOSS_GAP seconds."""
        start, upper = self.window_bounds(seconds, end)
        sql = "SELECT target,ts,loss FROM np_targets WHERE kind='ping' AND loss IS NOT NULL AND ts>=? AND ts<=?"
        params = [start, upper]
        if target:
            sql += " AND target=?"
            params.append(target)
        with self.db() as conn:
            rows = conn.execute(sql + " ORDER BY target,ts LIMIT %d" % (LOSS_ROWS + 1), params).fetchall()
        rows_capped = len(rows) > LOSS_ROWS
        per, episodes = {}, []
        for row in rows[:LOSS_ROWS]:
            stat = per.setdefault(row["target"], dict(target=row["target"], samples=0, lossy=0, loss_sum=0.0, max_loss=0.0, episodes=0, _open=None))
            stat["samples"] += 1
            stat["loss_sum"] += row["loss"]
            stat["max_loss"] = max(stat["max_loss"], row["loss"])
            current = stat["_open"]
            if row["loss"] > 0:
                stat["lossy"] += 1
                if current is not None and row["ts"] - current["end"] <= LOSS_GAP:
                    current.update(end=row["ts"], peak=max(current["peak"], row["loss"]), samples=current["samples"] + 1, sum=current["sum"] + row["loss"])
                else:
                    current = dict(target=row["target"], start=row["ts"], end=row["ts"], peak=row["loss"], samples=1, sum=row["loss"])
                    stat["_open"] = current
                    stat["episodes"] += 1
                    episodes.append(current)
            else:
                stat["_open"] = None
        for e in episodes:
            e["duration"] = e["end"] - e["start"]
            e["avg"] = e.pop("sum") / e["samples"]
        episodes.sort(key=lambda e: (-e["start"], e["target"]))
        targets = []
        for stat in per.values():
            stat.pop("_open")
            stat["avg_loss"] = stat.pop("loss_sum") / stat["samples"]
            targets.append(stat)
        targets.sort(key=lambda t: (-t["lossy"], t["target"]))
        samples = sum(t["samples"] for t in targets)
        summary = dict(samples=samples, lossy=sum(t["lossy"] for t in targets), episodes=len(episodes),
                       avg_loss=(sum(t["avg_loss"] * t["samples"] for t in targets) / samples) if samples else 0.0,
                       max_loss=max((t["max_loss"] for t in targets), default=0.0))
        end_ts = end if end is not None else int(self.clock())
        # `truncated`: the episode list was cut at LOSS_EPISODES. `rows_capped`: scanning stopped at LOSS_ROWS rows, so
        # later targets (alphabetically) are missing or partial -- the whole report is incomplete, not just the list.
        return dict(window=dict(**{"from": end_ts - seconds, "to": end_ts}), summary=summary, targets=targets,
                    episodes=episodes[:LOSS_EPISODES], rows_capped=rows_capped,
                    outages=self.outages(start, upper)), len(episodes) > LOSS_EPISODES

    def events(self, seconds, end=None):
        start, upper = self.window_bounds(seconds, end)
        with self.db() as conn:
            rows = [dict(x) for x in conn.execute("SELECT * FROM np_events WHERE ts>=? AND ts<=? ORDER BY ts DESC,id DESC LIMIT 200", (start, upper))]
        for row in rows:
            row["reasons"] = json.loads(row["reasons"])
            row["metrics"] = json.loads(row["metrics"])
        return rows

    def export(self, seconds, end=None):
        start, upper = self.window_bounds(seconds, end)
        with self.db() as conn:
            rows = conn.execute("SELECT * FROM np_targets WHERE ts>=? AND ts<=? ORDER BY ts,id LIMIT 50001", (start, upper)).fetchall()
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["timestamp", "target", "address", "kind", "loss_pct", "rtt_ms", "jitter_ms", "wan_ip", "wan_isp"])
        def safe(value):
            if isinstance(value, str) and (value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n"))):
                return "'" + value
            return value
        for row in rows[:50000]:
            writer.writerow([datetime.fromtimestamp(row["ts"], timezone.utc).isoformat()] +
                            [safe(row[k]) for k in ("target", "address", "kind", "loss", "latency", "jitter", "wan_ip", "wan_isp")])
        return output.getvalue(), len(rows) > 50000


def install_routes(app, ext):
    from flask import jsonify, request, Response
    cfg = ext.telegram_config()
    if cfg["enabled"] and cfg["ready"]:
        ext.start_telegram()  # started here (not from app.py) so that updating the extension alone is enough
    def period(default=3600):
        try:
            value = int(request.args.get("range", default))
            if not 1 <= value <= MAX_RANGE:
                raise ValueError()
            return value
        except (ValueError, TypeError):
            from werkzeug.exceptions import BadRequest
            raise BadRequest("range must be seconds between 1 and %s" % MAX_RANGE)

    def window(default=3600):
        """(seconds, end): `range` seconds back from now, or the absolute window from..to (epoch seconds)."""
        f, t = request.args.get("from"), request.args.get("to")
        if f is None and t is None:
            return period(default), None
        from werkzeug.exceptions import BadRequest
        try:
            f, t = int(f), min(int(t), int(ext.clock()))
        except (TypeError, ValueError):
            raise BadRequest("from and to must both be epoch seconds")
        if not 0 < f < t or t - f > MAX_RANGE:
            raise BadRequest("the window must be positive and at most %s seconds" % MAX_RANGE)
        return t - f, t

    @app.after_request
    def np_compress(response):
        """gzip for the page and the JSON/CSV answers (the page is 172 kB, 44 kB compressed); the page also gets an
        ETag so a reload is answered with 304 and no body."""
        if request.method != "GET" or response.status_code != 200 or response.direct_passthrough:
            return response
        if request.path == "/":
            response.headers["Cache-Control"] = "no-cache"
            response.add_etag(weak=True)
            response.make_conditional(request)
            if response.status_code != 200:
                return response
        if ("Content-Encoding" not in response.headers and response.mimetype in GZIP_TYPES
                and "gzip" in request.headers.get("Accept-Encoding", "").lower()):
            body = response.get_data()
            if len(body) >= GZIP_MIN:
                response.set_data(gzip.compress(body, compresslevel=5, mtime=0))
                response.headers["Content-Encoding"] = "gzip"
                response.headers.add("Vary", "Accept-Encoding")
        return response

    def wan_arg():
        """?wan_ip=<address>&wan_isp=<provider> selects one WAN source; wan_ip= (empty) means samples without a known WAN."""
        if "wan_ip" not in request.args:
            return None
        address, provider = request.args.get("wan_ip", ""), request.args.get("wan_isp", "")
        try:
            if address:
                address = str(ipaddress.ip_address(address))
            if len(provider) > 120:
                raise ValueError()
        except ValueError:
            from werkzeug.exceptions import BadRequest
            raise BadRequest("wan_ip must be an IP address (or empty) and wan_isp at most 120 characters")
        return address, provider

    @app.get("/api/targets/recent")
    def np_recent():
        seconds, end = window()
        return jsonify(data=ext.recent_targets(seconds, end, wan_arg()), started_at=ext.started_at())

    @app.get("/api/wan/sources")
    def np_wan_sources():
        seconds, end = window()
        return jsonify(ext.wan_sources(seconds, end))

    @app.post("/api/wan/forget")
    def np_wan_forget():
        """Erase every recorded WAN address and provider from the history (needs "confirm": true)."""
        if not same_origin_request():
            return jsonify(error="Same-origin settings request required"), 403
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or body.get("confirm") is not True or set(body) - {"confirm"}:
            return jsonify(error="Expected {\"confirm\": true}"), 400
        return jsonify(forgotten=ext.forget_wan())

    @app.get("/api/targets/history")
    def np_history_target():
        seconds, end = window()
        return jsonify(data=ext.target_history(request.args.get("target", ""), seconds, request.args.get("address"), end, wan_arg()))

    @app.get("/api/history")
    def np_history():
        seconds, end = window()
        return jsonify(data=ext.history(seconds, end, wan_arg()))

    @app.get("/api/events")
    def np_events():
        seconds, end = window(86400)
        return jsonify(data=ext.events(seconds, end))

    @app.get("/api/loss")
    def np_loss():
        seconds, end = window()
        report, truncated = ext.loss_report(seconds, end, request.args.get("target") or None)
        return jsonify(data=report, truncated=truncated)

    def same_origin_request():
        return (request.headers.get("X-NetProbe-Request") == "settings" and
                not (request.headers.get("Origin") and urlsplit(request.headers["Origin"]).netloc != request.host))

    @app.route("/api/settings", methods=["GET", "POST"])
    def np_settings():
        if request.method == "POST":
            if not same_origin_request():
                return jsonify(error="Same-origin settings request required"), 403
            try:
                ext.update_settings(request.get_json())
            except (ValueError, TypeError):
                return jsonify(error="Invalid settings; thresholds must be finite positive numbers and confirmation counts integers 1–20"), 400
        try:
            values = ext.settings()
        except (ValueError, OSError):
            return jsonify(error="Persistent settings cannot be read"), 503
        retention = ext.effective_prefs()["history_retention_days"] or None
        return jsonify(settings=values, storage=dict(bytes=ext.db_bytes(), retention_days=retention), started_at=ext.started_at(),
                       turbo_available=ext.turbo_available)

    @app.route("/api/prefs", methods=["GET", "POST"])
    def np_prefs():
        if request.method == "POST":
            if not same_origin_request():
                return jsonify(error="Same-origin settings request required"), 403
            try:
                ext.update_prefs(request.get_json())
            except (ValueError, TypeError):
                return jsonify(error="Invalid preferences"), 400
        try:
            return jsonify(prefs=ext.prefs(), schedule=dict(next_ts=ext.next_speedtest(int(ext.clock())), timezone=str(ext.tz)),
                           history=ext.history_info())
        except (ValueError, OSError):
            return jsonify(error="Persistent preferences cannot be read"), 503

    @app.post("/api/history/cleanup")
    def np_cleanup():
        """Delete (or, with dry_run, only count) history older than `days`; a real deletion needs "confirm": true."""
        if not same_origin_request():
            return jsonify(error="Same-origin settings request required"), 403
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or set(body) - {"days", "dry_run", "confirm"}:
            return jsonify(error="Expected {days, dry_run, confirm}"), 400
        dry_run = body.get("dry_run", False)
        if type(dry_run) is not bool or type(body.get("confirm", False)) is not bool:
            return jsonify(error="dry_run and confirm must be booleans"), 400
        if not dry_run and body.get("confirm") is not True:
            return jsonify(error="Deleting history needs \"confirm\": true (or \"dry_run\": true to only count)"), 400
        try:
            return jsonify(ext.cleanup(body.get("days"), dry_run=dry_run))
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        except CleanupBusy as exc:
            return jsonify(error=str(exc)), 409

    @app.get("/api/speedtest/series")
    def np_speedtest_series():
        seconds, end = window(7 * 86400)
        return jsonify(tests=ext.speedtest_series(seconds, end), range=seconds)

    @app.get("/api/about")
    def np_about():
        return jsonify(name="Internet Health", version=VERSION)

    @app.get("/api/telegram")
    def np_telegram():
        response = jsonify(ext.telegram_view())
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/api/telegram")
    def np_telegram_save():
        if not same_origin_request():
            return jsonify(error="Same-origin settings request required"), 403
        try:
            return jsonify(ext.update_telegram(request.get_json(silent=True)))
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        except OSError as exc:
            return jsonify(error="Telegram settings could not be saved: %s" % exc.__class__.__name__), 500

    @app.post("/api/telegram/test")
    def np_telegram_test():
        if not same_origin_request():
            return jsonify(error="Same-origin settings request required"), 403
        body = request.get_json(silent=True)
        body = body if isinstance(body, dict) else {}
        if set(body) - {"token", "chat_id"} or not all(isinstance(body.get(k, ""), str) for k in ("token", "chat_id")):
            return jsonify(error="Expected {token, chat_id}"), 400
        try:
            ext.telegram_test((body.get("token") or "").strip() or None, (body.get("chat_id") or "").strip() or None)
        except TelegramError as exc:
            return jsonify(error=str(exc)), 502
        return jsonify(ok=True, **ext.telegram_view())

    @app.get("/api/public-ip")
    def np_public_ip():
        return jsonify(ext.public_ip())

    @app.post("/api/public-ip/refresh")
    def np_public_ip_refresh():
        if not same_origin_request():
            return jsonify(error="Same-origin settings request required"), 403
        return jsonify(ext.request_public_ip()), 202

    @app.get("/api/export/targets.csv")
    def np_csv():
        seconds, end = window(86400)
        content, truncated = ext.export(seconds, end)
        return Response(content, mimetype="text/csv", headers={
            "Content-Disposition": 'attachment; filename="netprobe-targets.csv"',
            "X-Export-Truncated": str(truncated).lower(), "X-Export-Row-Limit": "50000"})

    def authorized():
        return hmac.compare_digest(request.headers.get("X-NetProbe-Notify-Key", "").encode(), ext.token.encode())

    @app.get("/api/notifications/pending")
    def np_pending():
        if not authorized():
            return jsonify(error="Forbidden"), 403
        with ext.db() as conn:
            rows = [dict(x) for x in conn.execute("SELECT id,ts,level,subject,message,description FROM np_notifications WHERE acked=0 ORDER BY id LIMIT 20")]
            latest = conn.execute("SELECT MAX(cycle_ts) FROM np_targets").fetchone()[0]
        deliver = ext.effective_settings()["enabled"] and ext.effective_prefs()["notify_unraid"]  # the Unraid integration can be switched off
        return jsonify(notifications=rows if deliver else [], latest_probe_ts=latest,
                       process_started_at=ext.process_started_at, max_sample_age=180)

    @app.post("/api/notifications/<int:notification_id>/ack")
    def np_ack(notification_id):
        if not authorized():
            return jsonify(error="Forbidden"), 403
        with ext.db() as conn:
            found = conn.execute("SELECT id FROM np_notifications WHERE id=?", (notification_id,)).fetchone()
            if not found:
                return jsonify(error="Unknown notification"), 404
            conn.execute("UPDATE np_notifications SET acked=1 WHERE id=?", (notification_id,))
        return jsonify(ok=True)


def main(argv=None):
    """python netprobe_extensions.py cleanup --days 90 [--dry-run] [--db /data/netprobe.sqlite]  (run inside the container)"""
    import argparse
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("command", choices=["cleanup"])
    parser.add_argument("--days", type=int, required=True, choices=[d for d in RETENTION_CHOICES if d])
    parser.add_argument("--dry-run", action="store_true", help="only count what would be deleted")
    parser.add_argument("--db", default=os.getenv("DB_PATH", "/data/netprobe.sqlite"))
    args = parser.parse_args(argv)
    ext = Extension(args.db, os.path.dirname(os.path.abspath(args.db)))
    print(json.dumps(ext.cleanup(args.days, dry_run=args.dry_run), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
