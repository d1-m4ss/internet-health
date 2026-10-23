"""Synthetic database with the upstream NetProbe schema (no real measurements are shipped)."""
import random
import sqlite3

SCHEMA = """
CREATE TABLE measurements (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, avg_latency_ms REAL,
    avg_jitter_ms REAL, avg_loss_pct REAL, avg_dns_latency_ms REAL, score REAL);
CREATE TABLE dns_measurements (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, server_ip TEXT NOT NULL, latency_ms REAL);
CREATE TABLE speedtests (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, ping_ms REAL, download_mbps REAL, upload_mbps REAL,
    server_id TEXT, server_name TEXT, server_host TEXT, server_country TEXT, requested_server_id TEXT, backend TEXT);
"""


def make_database(path, rows=60, start=1_700_000_000):
    """Create a deterministic fixture database at `path` (same content on every call)."""
    rnd = random.Random(7)
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(SCHEMA)
        for i in range(rows):
            ts = start + i * 30
            conn.execute("INSERT INTO measurements(ts,avg_latency_ms,avg_jitter_ms,avg_loss_pct,avg_dns_latency_ms,score) VALUES (?,?,?,?,?,?)",
                         (ts, round(rnd.uniform(15, 30), 2), round(rnd.uniform(0.2, 3), 2), rnd.choice([0, 0, 0, 0, 5]), round(rnd.uniform(2, 6), 2), round(rnd.uniform(90, 98), 2)))
            for server in ("8.8.8.8", "9.9.9.9", "1.1.1.1", "192.0.2.53"):
                conn.execute("INSERT INTO dns_measurements(ts,server_ip,latency_ms) VALUES (?,?,?)", (ts, server, round(rnd.uniform(2, 8), 2)))
        for i in range(3):
            conn.execute("INSERT INTO speedtests(ts,ping_ms,download_mbps,upload_mbps,server_id,server_name,server_host,server_country,requested_server_id,backend) VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (start + i * 14400, 30 + i, 500.0 + i * 10, 400.0 + i * 5, "1000" + str(i), "Example City", "speedtest.example.net:8080", "Exampleland", None, "python"))
        conn.commit()
    finally:
        conn.close()
