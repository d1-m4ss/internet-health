# Changelog

The project is called **NetProbe Enhanced** (container, image, file and table names keep the upstream `netprobe` /
`np_` names so existing installations are unaffected).

## Unreleased

- **Speedtest service selection** (*Speedtest* tab): Speedtest.net (speedtest-cli, as before), the official Ookla CLI
  and the new **Fast.com (Netflix)** backend, implemented with the standard library only (latency, download, upload;
  8 / 4 parallel connections, 8 s per direction). The choice is stored in `/data/netprobe-prefs.json`
  (`GET`/`POST /api/prefs`), used for manual runs and, from now on, for scheduled ones, and `SPEEDTEST_BACKEND=fast`
  works in the container too. History and the last result name the service in plain words. An unmeasured upload is
  stored and shown as unknown, not zero.
- Phones and tablets: the menu bar now stays pinned to the top while the page scrolls (both styles); in the UniFi style the *Speedtest* and *Targets* menu links open the matching tab instead of doing nothing.
- **Turbo mode** (opt-in, off by default): a 5-second ICMP watcher for the router and up to two public IPs that
  reports outages from about 10 s which the regular checks miss — one message after recovery, at most 3 per hour,
  silent when the regular monitor already alerted. New setting `turbo`, new table `np_outages`, `outages` in
  `/api/loss`, explanation dialog in the UI and a *Short outages (Turbo)* list in the loss report.
- Notifications whose second is already taken in the queue move to the next free second instead of being dropped.

## 0.1.0 — 2026-10-23

First public version.

- Per-target / per-IP history (`np_*` tables), state machine with confirmation, event log, CSV export.
- Readable Unraid notifications (diagnosis from all targets, icons, recovery durations) delivered by a host bridge
  over an authenticated queue; flood control (flap muting, per-hour limit for Down↔Degraded updates).
- DNS servers that stop answering are detected (`DOWN`) instead of being scored by elapsed time.
- Packet-loss report with episodes; custom `from`–`to` windows on every history endpoint; periods from 1 minute to a month.
- Compare mode (several metrics / targets / DNS servers on one chart).
- New UI: *NetProbe* and *UniFi* styles, Russian/English, Unraid → router → Internet path, uptime bar, accessibility
  (AA contrast, reduced motion, 390 px layout).
- Test-suite: unit/integration tests, a network-failure simulator, Playwright UI tests.
