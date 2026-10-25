# Changelog

The project is called **NetProbe Enhanced** (container, image, file and table names keep the upstream `netprobe` /
`np_` names so existing installations are unaffected).

## Unreleased

- **History retention** (settings → *History*, off by default): keep 7 / 30 / 90 / 180 days or 1 year; records older than that
  are deleted once a night in the quiet hours (batches, no long locks, file compacted afterwards), with a preview and a
  confirmed *Delete…* for on-demand cleaning and `python netprobe_extensions.py cleanup --days N [--dry-run]` inside the
  container. Undelivered notifications, open outages and state tables are never deleted. Choosing a period deletes nothing at
  once. The *Targets* page no longer fails with an error when a target has no state row.
- Screenshots in `docs/screenshots` are regenerated (WAN / ISP card, Speedtest schedule, history settings, pinned menu); all
  values are invented (documentation address `198.51.100.23`, fictional provider). Test fixtures no longer contain place names.
- Schedule and cleanup times on the page are shown in the server's time zone (`APP_TIMEZONE`), not the browser's.
- **Faster, no more frozen page.** A manual Speedtest used to run inside the request, and the stock image has a single
  synchronous web worker, so the page and every other request waited for the whole test (30–45 s). It now runs in the
  background (`POST /api/speedtest/run` with `"async": true` answers `202`; `GET /api/speedtest/status`); the page
  follows it, also when a scheduled test is running. The external-IP lookup is a background refresh too (the first
  answer is *pending*, an expired one is served while it is renewed). The page and the JSON answers are gzip-compressed
  (172 kB → 44 kB) and the page is revalidated with an ETag (a reload is a `304`). The browser asks for
  `/api/history` once instead of twice and draws every answer as it arrives instead of waiting for the slowest one.
  Optional container variable `GUNICORN_CMD_ARGS=--threads 4` lets the server handle requests in parallel
  (in `examples/docker-compose.yml`; see *Web server threads* in `docs/INSTALL-UNRAID.md`).
- Times in Speedtest messages use the schedule's time zone (`APP_TIMEZONE`) even when the process runs in another one.
- The external-IP lookup runs beside the dashboard refresh instead of inside it: a slow or unreachable lookup service
  can no longer delay the data on screen.
- **WAN / ISP card with the external IP on request** (setting *Show my external IP and provider*, off by default): a
  card under the status banner with the address the internet sees and the provider's name (the layout follows the
  proposal in PR 1). Nothing is requested while the setting is off; with it on the container asks `ipwho.is`
  (fallbacks `api.ipify.org`, `icanhazip.com`, which give the address only) at most every 5 minutes, accepts only
  public IP addresses and shows lookup failures plainly (`GET /api/public-ip`). An unknown provider reads *not
  reported*.
- **Speedtest schedule and notifications** (*Speedtest* tab → *Schedule and notifications*): automatic tests off, every
  N hours, or every day at up to four times in `APP_TIMEZONE` (a missed daily test still runs within two hours),
  with an optional Unraid message after every automatic test (`normal`) or failure (`warning`, with the reason).
  Default is unchanged: the container variables decide. Previously the schedule could only be set with
  `SPEEDTEST_INTERVAL` (every 4 h by default) and no message was sent.
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
