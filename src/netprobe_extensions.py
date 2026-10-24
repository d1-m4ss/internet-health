"""Additive storage and state-change alerts for the installed NetProbe app."""
from concurrent.futures import ThreadPoolExecutor
import csv
from contextlib import contextmanager
from datetime import datetime, timezone
import hmac
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
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlsplit


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
        if not self.settings_path.exists():
            self._write_settings(DEFAULTS)
        self.token = self._load_token(self.data_dir / "notify-token")
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
            target_columns = {row["name"] for row in conn.execute("PRAGMA table_info(np_targets)")}
            if "wan_ip" not in target_columns:
                conn.execute("ALTER TABLE np_targets ADD COLUMN wan_ip TEXT")
            if "wan_isp" not in target_columns:
                conn.execute("ALTER TABLE np_targets ADD COLUMN wan_isp TEXT")
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

    def _write_settings(self, values):
        fd, name = tempfile.mkstemp(prefix=".alert-settings-", dir=self.data_dir)
        try:
            with os.fdopen(fd, "w") as handle:
                json.dump(values, handle, indent=2, allow_nan=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, self.settings_path)
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

    def record_probe(self, ts, ping_results, dns_per_server, dns_failed=(), wan_info=None):
        if type(ts) is not int or ts <= 0:
            raise ValueError("Invalid probe timestamp")
        wan_ip = wan_isp = None
        if wan_info is not None:
            if not isinstance(wan_info, dict):
                raise ValueError("Invalid WAN information")
            try:
                parsed_wan_ip = ipaddress.ip_address(wan_info.get("public_ip", ""))
            except ValueError as exc:
                raise ValueError("Invalid WAN public IP") from exc
            if not parsed_wan_ip.is_global:
                raise ValueError("WAN address must be public")
            wan_ip = str(parsed_wan_ip)
            wan_isp = wan_info.get("isp")
            if wan_isp is not None:
                if not isinstance(wan_isp, str):
                    raise ValueError("WAN ISP must be text")
                wan_isp = " ".join(wan_isp.split())[:120] or None
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
        with self.lock, self.db() as conn:
            cfg = self.effective_settings()
            changes = []
            for row in rows:
                target = row["target"]
                previous = conn.execute("SELECT * FROM np_state WHERE target=?", (target,)).fetchone()
                if previous and ts <= previous["last_ts"]:
                    continue
                conn.execute("""INSERT OR IGNORE INTO np_targets
                    (cycle_ts,ts,target,address,kind,latency,jitter,loss,wan_ip,wan_isp)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (ts, row["ts"], target, row["address"], row["kind"], row["latency"],
                     row["jitter"], row["loss"], wan_ip, wan_isp))
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
                AND ts>=? AND ts<=?""", (TURBO_ICON + "%", row["start_ts"] - TURBO_LOOKBACK, row["end_ts"])).fetchone():
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

    def recent_targets(self, seconds, end=None):
        start, upper = self.window_bounds(seconds, end)
        with self.db() as conn:
            rows = conn.execute("""SELECT target,COUNT(*) samples,AVG(latency) avg_latency,
                MAX(latency) max_latency,AVG(loss) avg_loss,MAX(loss) max_loss
                FROM np_targets WHERE ts>=? AND ts<=? GROUP BY target ORDER BY target""", (start, upper)).fetchall()
            out = []
            for r in rows:
                latest = dict(conn.execute("SELECT * FROM np_targets WHERE target=? ORDER BY ts DESC,id DESC LIMIT 1", (r["target"],)).fetchone())
                latest.update(dict(r))
                state = conn.execute("SELECT active,candidate,streak FROM np_state WHERE target=?", (r["target"],)).fetchone()
                latest.update(dict(state));latest["state"] = latest.pop("active")
                latest["addresses"] = [dict(x) for x in conn.execute("""SELECT address,COUNT(*) samples,
                    AVG(latency) avg_latency,MAX(latency) max_latency,AVG(loss) avg_loss,MAX(loss) max_loss
                    FROM np_targets WHERE target=? AND ts>=? AND ts<=? GROUP BY address""", (r["target"], start, upper))]
                latest["wan_sources"] = [dict(x) for x in conn.execute("""SELECT wan_ip,wan_isp,
                    COUNT(*) samples,AVG(latency) avg_latency,MAX(latency) max_latency,
                    AVG(loss) avg_loss,MAX(loss) max_loss
                    FROM np_targets WHERE target=? AND ts>=? AND ts<=?
                    GROUP BY wan_ip,wan_isp ORDER BY samples DESC,wan_ip,wan_isp""",
                    (r["target"], start, upper))]
                out.append(latest)
            return out

    def target_history(self, target, seconds, address=None, end=None, wan_ip=None, wan_isp=None):
        step = max(1, math.ceil(seconds / 599))
        conditions = ["target=?"]
        values = [target]
        if address is not None:
            conditions.append("address=?")
            values.append(address)
        if wan_ip is not None:
            conditions.extend(["wan_ip IS NULL" if not wan_ip else "wan_ip=?",
                               "COALESCE(wan_isp,'')=?"])
            if wan_ip:
                values.append(wan_ip)
            values.append(wan_isp or "")
        start, upper = self.window_bounds(seconds, end)
        params = [step, start, upper] + values
        with self.db() as conn:
            return [dict(r) for r in conn.execute("""SELECT CAST(ts/? AS INTEGER) bucket,MAX(ts) ts,
                AVG(latency) latency,AVG(jitter) jitter,AVG(loss) loss,MAX(loss) max_loss,
                CASE WHEN COUNT(DISTINCT address)=1 THEN MAX(address) ELSE NULL END address
                FROM np_targets WHERE ts>=? AND ts<=? AND """ + " AND ".join(conditions) +
                " GROUP BY bucket ORDER BY bucket", params)]

    def history(self, seconds, end=None, wan_ip=None, wan_isp=None):
        step = max(1, math.ceil(seconds / 599))
        start, upper = self.window_bounds(seconds, end)
        with self.db() as conn:
            if wan_ip is not None:
                wan_clause = "wan_ip IS NULL" if not wan_ip else "wan_ip=?"
                params = [step, start, upper] + ([wan_ip] if wan_ip else []) + [wan_isp or ""]
                rows = [dict(r) for r in conn.execute("""SELECT CAST(ts/? AS INTEGER) bucket,MAX(ts) ts,
                    AVG(CASE WHEN kind='ping' THEN latency END) avg_latency_ms,
                    AVG(CASE WHEN kind='ping' THEN jitter END) avg_jitter_ms,
                    AVG(CASE WHEN kind='ping' THEN loss END) avg_loss_pct,
                    MAX(CASE WHEN kind='ping' THEN loss END) max_loss_pct,
                    AVG(CASE WHEN kind='dns' THEN latency END) avg_dns_latency_ms
                    FROM np_targets WHERE ts>=? AND ts<=? AND """ + wan_clause +
                    """ AND COALESCE(wan_isp,'')=?
                    GROUP BY bucket ORDER BY bucket""", params)]
                dns = {}
                for r in conn.execute("""SELECT CAST(ts/? AS INTEGER) bucket,target,AVG(latency) latency
                    FROM np_targets WHERE ts>=? AND ts<=? AND """ + wan_clause +
                    """ AND COALESCE(wan_isp,'')=? AND kind='dns' GROUP BY bucket,target""", params):
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

    @app.get("/api/targets/recent")
    def np_recent():
        seconds, end = window()
        return jsonify(data=ext.recent_targets(seconds, end), started_at=ext.started_at())

    @app.get("/api/targets/history")
    def np_history_target():
        seconds, end = window()
        wan_ip = request.args.get("wan_ip") if "wan_ip" in request.args else None
        wan_isp = request.args.get("wan_isp", "")
        return jsonify(data=ext.target_history(request.args.get("target", ""), seconds,
                                               request.args.get("address"), end, wan_ip, wan_isp))

    @app.get("/api/history")
    def np_history():
        seconds, end = window()
        wan_ip = request.args.get("wan_ip") if "wan_ip" in request.args else None
        wan_isp = request.args.get("wan_isp", "")
        return jsonify(data=ext.history(seconds, end, wan_ip, wan_isp))

    @app.get("/api/events")
    def np_events():
        seconds, end = window(86400)
        return jsonify(data=ext.events(seconds, end))

    @app.get("/api/loss")
    def np_loss():
        seconds, end = window()
        report, truncated = ext.loss_report(seconds, end, request.args.get("target") or None)
        return jsonify(data=report, truncated=truncated)

    @app.route("/api/settings", methods=["GET", "POST"])
    def np_settings():
        if request.method == "POST":
            if (request.headers.get("X-NetProbe-Request") != "settings" or
                (request.headers.get("Origin") and urlsplit(request.headers["Origin"]).netloc != request.host)):
                return jsonify(error="Same-origin settings request required"), 403
            try:
                ext.update_settings(request.get_json())
            except (ValueError, TypeError):
                return jsonify(error="Invalid settings; thresholds must be finite positive numbers and confirmation counts integers 1–20"), 400
        try:
            values = ext.settings()
        except (ValueError, OSError):
            return jsonify(error="Persistent settings cannot be read"), 503
        size = sum(p.stat().st_size for p in [Path(ext.db_path), Path(ext.db_path + "-wal")] if p.exists())
        return jsonify(settings=values, storage=dict(bytes=size, retention_days=None), started_at=ext.started_at(),
                       turbo_available=ext.turbo_available)

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
        return jsonify(notifications=rows if ext.effective_settings()["enabled"] else [], latest_probe_ts=latest,
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
