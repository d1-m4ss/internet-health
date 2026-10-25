#!/usr/bin/env python3
"""Dashboard on invented data: the real app and extension, a fake network, nothing external is touched.

    python tests/demo_server.py                  # http://127.0.0.1:8799  (36 h of history, then a probe every 30 s)
    python tests/demo_server.py --failing        # the last minutes are an internet outage
    python tests/demo_server.py --port 8800 --hours 72

The history is produced by the real probe loop (src/app.py) on a virtual clock with a fake `ping` and DNS
resolver, exactly like tests/simulate_network.py, so every chart, table and notification text is genuine
output of the code -- only the network is invented (router "ASUS RT-AX86U" at 192.168.1.1).
Used by tests/screenshots.cjs to produce the pictures in docs/screenshots.
"""
import argparse
import os
from pathlib import Path
import random
import sqlite3
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import simulate_network as sim  # noqa: E402

sim.ROUTER, sim.GATEWAY = "192.168.1.1", "172.20.0.1"
RTT = {"fast.com": 58.0, "google.com": 27.0, "youtube.com": 52.0, "1.1.1.1": 13.0, "8.8.8.8": 33.0}
DNS_MS = {"8.8.8.8": 14.0, "9.9.9.9": 23.0, "1.1.1.1": 9.0}
rnd = random.Random(11)
real_cond = sim.cond


def noisy_cond(name):
    ping, dns = real_cond(name)
    out = {}
    for host, (loss, rtt) in ping.items():
        if rtt == 22.0:  # a healthy internet target: give each its own typical round trip
            rtt = RTT.get(host, rtt)
        if loss < 100:
            rtt *= rnd.uniform(0.9, 1.18) * (rnd.uniform(1.6, 2.4) if rnd.random() < 0.015 else 1)
        out[host] = (loss, rtt)
    return out, dns


sim.cond = noisy_cond


def timeline(total, failing):
    """tick -> condition; incidents are listed as (hours before the end, ticks, condition)."""
    incidents = [(30, 4, "loss25"), (22, 2, "google_down"), (14, 14, "wan_down"), (9, 20, "dns_dead"), (5, 8, "loss50"),
                 (2, 6, "lat400"), (0.7, 3, "google_loss50"), (0.4, 2, "loss25")]
    plan = {}
    for hours, ticks, name in incidents:
        first = total - int(hours * 3600 / sim.STEP)
        for i in range(first, first + ticks):
            plan[i] = name

    def condition(i):
        if failing and i >= total - 8:
            return "wan_down"
        return plan.get(i, "ok")
    return condition


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--port", type=int, default=8799)
    parser.add_argument("--hours", type=float, default=36)
    parser.add_argument("--failing", action="store_true", help="end with an internet outage")
    args = parser.parse_args()
    os.environ["ROUTER_NAME"] = "ASUS RT-AX86U"
    total = int(args.hours * 3600 / sim.STEP)
    scenario = dict(name="demo", ticks=total, f=timeline(total, args.failing))
    world = sim.Sim(scenario, verbose=False)
    module = world.module
    import jinja2  # the app renders templates/index.html in the image; here the page lives in src/
    module.app.jinja_loader = jinja2.FileSystemLoader(str(Path(__file__).resolve().parents[1] / "src"))
    module.get_default_gateway = lambda: sim.GATEWAY  # no `ip route` on a Mac; the container would detect its bridge gateway
    real_dns_latency = module.measure_dns_latency
    module.measure_dns_latency = lambda domain, server, count: (
        None if real_dns_latency(domain, server, count) is None else DNS_MS.get(server, 12.0) * rnd.uniform(0.8, 1.4))
    world.start = int(time.time()) - total * sim.STEP
    # WAN history: every sample carries the address and provider that carried it. Invented values (documentation addresses): the
    # primary provider, with a stretch on a backup mobile link in the middle of the history. Nothing here touches the network.
    ext = module.custom_extension
    wans = {"primary": ("198.51.100.23", "Example Broadband Ltd"), "backup": ("203.0.113.45", "Sample Mobile Backup")}
    ext.ip_fetch = lambda url, timeout=None: wans["primary"]
    ext.update_prefs({"show_public_ip": True, "speedtest_service": "python", "speedtest_schedule": "daily",
                      "speedtest_times": ["03:00"], "speedtest_notify": True})

    def carried_by(name):
        ext.ip_cache = dict(ip=wans[name][0], isp=wans[name][1], source="ipwho.is", checked_at=int(ext.clock()), error=None)
    for i in range(total):
        carried_by("backup" if 0.45 * total <= i < 0.6 * total else "primary")
        world.tick(i)
    carried_by("primary")
    now = int(time.time())
    with sqlite3.connect(world.db) as conn:  # drop the 2023 fixture rows and add recent Speedtests
        conn.execute("DELETE FROM measurements WHERE ts < ?", (now - int(args.hours * 3600) - 3600,))
        conn.execute("DELETE FROM dns_measurements WHERE ts < ?", (now - int(args.hours * 3600) - 3600,))
        conn.execute("DELETE FROM speedtests")
        for hours_ago, down, up, ping in ((26, 468.0, 88.0, 24.0), (14, 479.5, 90.1, 23.0), (2.5, 472.8, 89.4, 25.0)):
            conn.execute("INSERT INTO speedtests(ts,ping_ms,download_mbps,upload_mbps,server_id,server_name,server_host,server_country,"
                         "requested_server_id,backend) VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (now - int(hours_ago * 3600), ping, down, up, "10001", "Example City", "speedtest.example.net:8080", "Exampleland", None, "python"))

    def keep_probing():  # new probe every STEP seconds so the dashboard stays LIVE
        index = total
        while True:
            time.sleep(sim.STEP)
            world.start = int(time.time()) - index * sim.STEP
            carried_by("primary")
            world.tick(index)
            index += 1
    threading.Thread(target=keep_probing, daemon=True).start()
    from werkzeug.serving import make_server
    server = make_server("127.0.0.1", args.port, module.app, threaded=True)
    print("READY http://127.0.0.1:%d" % server.server_port, flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
