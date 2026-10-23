#!/usr/bin/env python3
"""End-to-end network-failure simulator.

Runs the REAL probe_loop from src/app.py on an isolated copy of the database with a
virtual clock, a fake `ping` (real Linux output format) and a fake DNS resolver, then
delivers the resulting queue through the REAL host bridge script over the REAL HTTP API
into a fake `notify` (nothing external is ever contacted or notified).

    python tests/simulate_network.py            # all scenarios, exits 1 on any mismatch
    python tests/simulate_network.py -v flap    # print delivered messages for matching scenarios
"""
import contextlib
import importlib.util
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
from fixture_db import make_database  # noqa: E402
BRIDGE = ROOT / "src/netprobe-unraid-notify.sh"
GATEWAY, ROUTER = "172.17.0.1", "192.168.1.1"
SITES = ["fast.com", "google.com", "youtube.com", "1.1.1.1", "8.8.8.8"]  # production SITES (+2 IP targets)
DNS = ["8.8.8.8", "9.9.9.9", "1.1.1.1"]
ADDR = {"google.com": "142.250.1.1", "youtube.com": "142.250.2.2", "fast.com": "23.1.1.1"}
STEP = 30  # seconds per probe cycle (PROBE_INTERVAL)


class EndCycle(Exception):
    pass


# --- network conditions: name -> (ping {host: (loss%, avg_rtt_ms)}, dns {server: "ok"|"dead"|"servfail"}) ---
def cond(name):
    ping = {GATEWAY: (0, 1.0), ROUTER: (0, 2.0), **{h: (0, 22.0) for h in SITES}}
    dns = {s: "ok" for s in DNS}
    names = [h for h in SITES if h[0].isalpha()]
    if name == "wan_down":
        ping.update({h: (100, 0) for h in SITES}); dns = {s: "dead" for s in DNS}
    elif name == "dns_outage":  # resolver broken: pinging by name fails, pinging by IP still works
        ping.update({h: (100, 0) for h in names}); dns = {s: "dead" for s in DNS}
    elif name == "router_dead":
        ping[ROUTER] = (100, 0); ping.update({h: (100, 0) for h in SITES}); dns = {s: "dead" for s in DNS}
    elif name == "loss50":
        ping.update({h: (50, 25.0) for h in SITES})
    elif name == "loss25":
        ping.update({h: (25, 25.0) for h in SITES})
    elif name == "lat400":
        ping.update({h: (0, 400.0) for h in SITES})
    elif name == "dns_dead":
        dns["9.9.9.9"] = "dead"
    elif name == "dns_servfail":
        dns["9.9.9.9"] = "servfail"
    elif name == "router_down":
        ping[ROUTER] = (100, 0)
    elif name == "google_down":
        ping["google.com"] = (100, 0)
    elif name == "google_loss50":
        ping["google.com"] = (50, 30.0)
    return ping, dns


def fake_ping_output(host, loss, rtt):
    addr = ADDR.get(host, host)
    received = round(4 * (1 - loss / 100))
    out = "PING %s (%s) 56(84) bytes of data.\n\n--- %s ping statistics ---\n" % (host, addr, host)
    out += "4 packets transmitted, %d received, %d%% packet loss, time 3004ms\n" % (received, loss)
    if received:
        out += "rtt min/avg/max/mdev = %.3f/%.3f/%.3f/0.4 ms\n" % (rtt * 0.9, rtt, rtt * 1.1)
    return out, (0 if received else 1)


# --- scenarios: (name, ticks, condition_for_tick(i), (min, max) notifications, required text, extras) ---
def phases(*parts):
    seq = []
    for name, minutes in parts:
        seq += [name] * (minutes * 60 // STEP)
    return lambda i: seq[min(i, len(seq) - 1)]


SCENARIOS = [
    dict(name="healthy 30 min", ticks=60, f=lambda i: "ok", expect=(0, 0)),
    dict(name="single bad probe (blip)", ticks=40, f=lambda i: "wan_down" if i == 10 else "ok", expect=(0, 0)),
    dict(name="sporadic 25% loss every 3rd probe", ticks=240, f=lambda i: "loss25" if i % 3 == 0 else "ok", expect=(0, 0)),
    dict(name="internet outage 10 min", ticks=100, f=phases(("ok", 10), ("wan_down", 10), ("ok", 30)),
         expect=(2, 2), text=["No internet", "Google (google.com)", "Google IP (8.8.8.8)", "DNS Google (8.8.8.8)", "back to normal"], grouped=True, retry_delivery=True),
    dict(name="50% packet loss 15 min", ticks=120, f=phases(("ok", 10), ("loss50", 15), ("ok", 35)),
         expect=(2, 2), text=["packet loss 50%"]),
    dict(name="high latency 400 ms 10 min", ticks=100, f=phases(("ok", 10), ("lat400", 10), ("ok", 30)),
         expect=(2, 2), text=["latency 400 ms"]),
    dict(name="one DNS server dead 10 min", ticks=100, f=phases(("ok", 10), ("dns_dead", 10), ("ok", 30)),
         expect=(2, 2), text=["DNS Quad9 (9.9.9.9)", "not answering DNS queries"], absent=["Google (google.com)"]),
    dict(name="DNS server answering SERVFAIL", ticks=100, f=phases(("ok", 10), ("dns_servfail", 10), ("ok", 30)),
         expect=(2, 2), text=["DNS Quad9 (9.9.9.9)"]),
    dict(name="router ignores ping, internet fine", ticks=100, f=phases(("ok", 10), ("router_down", 10), ("ok", 30)),
         expect=(2, 2), text=["Router (192.168.1.1)", "Router does not answer ping"], absent=["Google (google.com)", "No internet"]),
    dict(name="router really dead (everything silent)", ticks=100, f=phases(("ok", 10), ("router_dead", 10), ("ok", 30)),
         expect=(2, 2), text=["Router is not responding", "power"], absent=["No internet"]),
    dict(name="DNS-only outage (names fail, IPs work)", ticks=100, f=phases(("ok", 10), ("dns_outage", 10), ("ok", 30)),
         expect=(2, 2), text=["Names do not resolve", "DNS Google (8.8.8.8)"], absent=["No internet", "Unreachable"], grouped=True),
    dict(name="alternating 100%/50% loss 1 h", ticks=140, f=lambda i: ("ok" if i < 20 else "google_down" if i % 2 else "google_loss50") if i < 140 else "ok",
         expect=(1, 3), text=["Google"]),
    dict(name="flapping 2 bad/2 good for 3 h", ticks=360 + 200, f=lambda i: ("google_down" if i % 4 < 2 else "ok") if i < 360 else "ok",
         expect=(5, 8), text=["keeps changing", "stabilized"], single="keeps changing"),
    dict(name="flapping all targets 3 h", ticks=360, f=lambda i: "wan_down" if i % 4 < 2 else "ok", expect=(5, 8)),
    dict(name="unresolved problem fluctuating 6 h", ticks=20 + 720,
         f=lambda i: "ok" if i < 20 else ["wan_down", "wan_down", "loss50", "loss50", "loss50", "wan_down", "loss50", "wan_down", "wan_down", "loss50"][(i // 2) % 10],
         expect=(1, 6), text=["No internet"]),
    dict(name="slow alternation 100%/50% every 20 min, 6 h", ticks=20 + 720,
         f=lambda i: "ok" if i < 20 else ("wan_down" if ((i - 20) // 40) % 2 == 0 else "loss50"), expect=(1, 12)),
    dict(name="permanent outage 6 h", ticks=20 + 720, f=lambda i: "wan_down" if i >= 20 else "ok", expect=(1, 1), text=["No internet", "Google (google.com)"]),
    dict(name="restart mid-outage (no duplicate)", ticks=100, f=phases(("ok", 10), ("wan_down", 10), ("ok", 30)),
         expect=(2, 2), restart_at=45),
    dict(name="restart mid-confirmation streak", ticks=60, f=lambda i: "wan_down" if i in (20, 21) else "ok",
         expect=(1, 2), restart_at=21),
    dict(name="alerts disabled during outage", ticks=100, f=phases(("ok", 10), ("wan_down", 10), ("ok", 30)),
         expect=(0, 0), disabled=True),
]


class Sim:
    def __init__(self, scenario, verbose):
        self.sc, self.verbose = scenario, verbose
        self.tmp = Path(tempfile.mkdtemp(prefix="np-sim-"))
        self.db = self.tmp / "data" / "netprobe.sqlite"
        self.db.parent.mkdir()
        make_database(self.db)
        self.clock = [0]
        self.load()

    def load(self):
        env = {"DB_PATH": str(self.db), "DB_ENGINE": "sqlite", "USE_POSTGRES": "false", "PROBE_INTERVAL": "30",
               "PING_COUNT": "4", "SPEEDTEST_ENABLED": "false", "ROUTER_IP": ROUTER, "SITES": ",".join(SITES),
               "DNS_NAMESERVER_1_IP": DNS[0], "DNS_NAMESERVER_2_IP": DNS[1], "DNS_NAMESERVER_3_IP": DNS[2],
               "DNS_NAMESERVER_4_IP": ""}
        with patch.dict(os.environ, env), patch("threading.Thread.start"):
            spec = importlib.util.spec_from_file_location("sim_app_%d" % time.time_ns(), ROOT / "src/app.py")
            self.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.module)
        self.module.DNS_SERVERS[:] = DNS

    def tick(self, index):
        ping, dns = cond(self.sc["f"](index))
        import dns.resolver as resolver

        def fake_run(args, **kw):
            out, code = fake_ping_output(args[-1], *ping[args[-1]])
            return subprocess.CompletedProcess(args, code, stdout=out, stderr="")

        class FakeResolver:
            def __init__(self, configure=False):
                self.nameservers = []

            def resolve(self, domain, rdtype, lifetime=None):
                mode = dns[self.nameservers[0]]
                if mode == "dead":
                    raise TimeoutError("no reply")
                if mode == "servfail":
                    raise resolver.NoNameservers()

        m = self.module
        with patch.object(m, "get_default_gateway", return_value=GATEWAY), \
             patch.object(m.subprocess, "run", side_effect=fake_run), \
             patch.object(m.dns.resolver, "Resolver", FakeResolver), \
             patch.object(time, "time", side_effect=lambda: self.clock[0]), \
             patch.object(time, "sleep", side_effect=EndCycle), \
             contextlib.redirect_stdout(open(os.devnull, "w")):
            self.clock[0] = self.start + index * STEP
            with contextlib.suppress(EndCycle):
                m.probe_loop()

    def run(self):
        total = self.sc["ticks"]
        self.start = int(time.time()) - total * STEP + 5  # last probe lands "now" so the bridge sees fresh data
        self.clock[0] = self.start
        for i in range(total):
            if self.sc.get("restart_at") == i:
                self.load()  # container restart: new process, same persistent files
            if self.sc.get("disabled") and i == 0:
                self.module.custom_extension.update_settings({"enabled": False})
            self.tick(i)
        return self.deliver()

    def deliver(self):
        from werkzeug.serving import make_server
        server = make_server("127.0.0.1", 0, self.module.app, threaded=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        log, fail = self.tmp / "notify.log", self.tmp / "notify-fail"
        (bin_dir / "notify").write_text('#!/bin/bash\n[ -e "%s" ] && exit 1\nprintf "%%s\\n" "$*" >> "%s"\n' % (fail, log))
        (bin_dir / "notify").chmod(0o700)
        if not shutil.which("flock"):  # macOS: same stand-in the unit tests use
            (bin_dir / "flock").write_text("#!%s\nimport fcntl,sys\ntry: fcntl.flock(int(sys.argv[-1]),fcntl.LOCK_EX|fcntl.LOCK_NB)\n"
                                           "except BlockingIOError: sys.exit(1)\n" % sys.executable)
            (bin_dir / "flock").chmod(0o700)
        data = self.db.parent
        env = dict(os.environ, NETPROBE_API_BASE="http://127.0.0.1:%d" % server.server_port,
                   NETPROBE_NOTIFY_TOKEN_FILE=str(data / "notify-token"), NETPROBE_SETTINGS_FILE=str(data / "alert-settings.json"),
                   NETPROBE_STATE_FILE=str(self.tmp / "bridge-state.json"), NETPROBE_LOCK_FILE=str(self.tmp / "bridge.lock"),
                   UNRAID_NOTIFY=str(bin_dir / "notify"), PATH=str(bin_dir) + os.pathsep + os.environ["PATH"])

        def bridge():
            return subprocess.run(["bash", str(BRIDGE)], env=env, capture_output=True, text=True, timeout=30)

        def pending():
            import sqlite3
            with sqlite3.connect(self.db) as c:
                return c.execute("SELECT COUNT(*) FROM np_notifications WHERE acked=0").fetchone()[0]

        queued = None
        import sqlite3
        with sqlite3.connect(self.db) as c:
            queued = c.execute("SELECT COUNT(*) FROM np_notifications").fetchone()[0]
            events = c.execute("SELECT COUNT(*) FROM np_events").fetchone()[0]
        problems = []
        if self.sc.get("retry_delivery"):  # failing notify must keep everything queued, then deliver exactly once
            fail.touch()
            bridge()
            if log.exists() or pending() != queued:
                problems.append("failed delivery lost or acked notifications")
            fail.unlink()
        for _ in range(12):
            result = bridge()
            if result.returncode != 0 and "will retry" not in result.stderr:
                problems.append("bridge error: " + result.stderr.strip()[:200])
                break
            if pending() == 0 or self.sc.get("disabled"):
                break
        server.shutdown()
        delivered = log.read_text().splitlines() if log.exists() else []
        return dict(queued=queued, events=events, delivered=delivered, left=pending(), problems=problems)


def check(sc, r):
    problems = list(r["problems"])
    lo, hi = sc["expect"]
    if not lo <= len(r["delivered"]) <= hi:
        problems.append("delivered %d notifications, expected %d..%d" % (len(r["delivered"]), lo, hi))
    if not sc.get("disabled") and r["left"]:
        problems.append("%d notifications left undelivered" % r["left"])
    joined = "\n".join(r["delivered"])
    for fragment in sc.get("text", []):
        if fragment not in joined:
            problems.append("missing text: " + fragment)
    for fragment in sc.get("absent", []):
        if fragment in joined:
            problems.append("unexpected text: " + fragment)
    if sc.get("single"):  # one notify call per line (-d and -m both carry the text)
        hits = sum(sc["single"] in line for line in r["delivered"])
        if hits != 1:
            problems.append("'%s' appeared in %d notifications, expected 1" % (sc["single"], hits))
    if sc.get("grouped") and len(r["delivered"]) != 2:
        problems.append("confirmed changes of one probe were not grouped into one message")
    if sc.get("disabled") and r["events"] < 2:
        problems.append("events must still be recorded while alerts are disabled")
    return problems


def main():
    logging.disable(logging.CRITICAL)
    args = sys.argv[1:]
    verbose = "-v" in args
    patterns = [a for a in args if a != "-v"]
    failed = 0
    print("%-42s %7s %7s %9s  %s" % ("scenario", "events", "queued", "delivered", "result"))
    for sc in SCENARIOS:
        if patterns and not any(p.lower() in sc["name"].lower() for p in patterns):
            continue
        sim = Sim(sc, verbose)
        try:
            r = sim.run()
        finally:
            shutil.rmtree(sim.tmp, ignore_errors=True)
        problems = check(sc, r)
        failed += bool(problems)
        print("%-42s %7d %7d %9d  %s" % (sc["name"], r["events"], r["queued"], len(r["delivered"]), "OK" if not problems else "FAIL"))
        for p in problems:
            print("    !", p)
        if verbose:
            for line in r["delivered"]:
                subject = line.split(" -s ", 1)[1].split(" -d ", 1)[0]
                level = line.split(" -i ", 1)[1].split(" -m ", 1)[0]
                body = line.split(" -m ", 1)[1].replace("\\n", "\n      ")
                print("    [%s] %s\n      %s\n" % (level, subject, body))
    print("\n%s" % ("ALL SCENARIOS PASSED" if not failed else "%d SCENARIO(S) FAILED" % failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
