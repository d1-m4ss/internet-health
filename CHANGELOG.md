# Changelog

The project is called **Internet Health** (formerly “Internet Pulse” in 1.0.0 and “NetProbe Enhanced” before; container, image, file and table names keep the upstream `netprobe` /
`np_` names so existing installations are unaffected).

## Unreleased

- **The product is now called Internet Health** (it matches the repository and the image: `ghcr.io/d1-m4ss/internet-health`). New page
  title and header, `docs/unraid/internet-health.xml` (the template's container name is `InternetHealth`; an existing container keeps
  its name and settings), image labels and `GET /api/about`. Nothing else changes: the container, tables, files and variables keep their
  `netprobe` names.
## 1.0.0 — 2026-10-26

- **Versions and releases.** One version in the code (`VERSION`, shown by `GET /api/about` and in the page footer), semver, and
  `scripts/release.py` that turns the CHANGELOG's *Unreleased* into a release, commits and tags; pushing the tag publishes the image
  (`X.Y.Z`, `X.Y`, `latest`) and creates the GitHub Release with the notes (`.github/workflows/release.yml`). See *Releasing* in
  CONTRIBUTING.md.
- **Integrations block** (settings): one switch for **Unraid** (the host bridge) and one for **Telegram**, in any combination, saved together
  with one Save button. Ticking Telegram shows the token and chat fields; values that come from the container environment are not shown.
  With Unraid off, `GET /api/notifications/pending` returns an empty list (new preference `notify_unraid`, default on) and switching it
  on again does not replay what happened meanwhile; the *Notifications enabled* switch silences Telegram as well.
- **A ready container image** (`ghcr.io/d1-m4ss/internet-health`, from the first version tag): the pinned NetProbe image with the
  three Internet Pulse files baked in (`Dockerfile`), `--threads 4` by default, a health check, and an Unraid template
  (`docs/unraid/internet-pulse.xml`) for a one-click install; `examples/docker-compose.image.yml`. No bind mounts to maintain; an existing
  install switches by changing the repository and removing the three mounts (data and settings stay). CI builds the image, checks the base
  digest and starts it on an empty data folder (`scripts/smoke-image.sh`); a `v*` tag publishes it (`.github/workflows/release.yml`).
  The file-mount way keeps working.
- **Telegram straight from the container** (settings → *Integrations*, or `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`): the same messages Unraid
  gets are sent to a chat through the Bot API, with or without the Unraid bridge. Token + chat ID, *Send test*, Save; the token is
  write-only (stored in `/data/telegram.json`, mode 600, never returned by the API); messages from before switch-on are not
  sent, failures are retried with a growing pause and given up after a day. New table column `np_notifications.tg`; endpoints
  `GET/POST /api/telegram` and `POST /api/telegram/test`.
- **New name and icon.** The project is now **Internet Pulse** (it was “NetProbe Enhanced”) with a new icon: a pulse line in a ring,
  used as the browser-tab icon (inline SVG, nothing extra to mount), the mark in the page header, `docs/icon.svg` / `docs/icon.png`
  and the README. The container is still called `NetProbe`; tables, files and variables keep their names.
- **The chart filters are remembered** in the browser (period, WAN source and metric; `localStorage`, this browser only), so a
  reload or the next visit starts where you left off. A remembered provider that is no longer in the data, an unknown value or
  damaged storage is ignored quietly; a custom date window is not remembered.
- **Settings tidy-up:** the database size line moved into the *History* block, and the external-IP options (checkbox, interval,
  notification) are now a draft with a **Save** button instead of applying at once; a failed save keeps what you typed.
- **How often the external IP is checked** (settings, next to *Show my external IP and provider*): every 15 minutes (default),
  every minute, or **manually**. In manual mode nothing is requested until *Refresh* is pressed in the *WAN / ISP* card
  (`POST /api/public-ip/refresh`; ticking the option counts as one request); no WAN history is recorded and no change message
  is sent, because nothing watches the link. Opening the page never starts a lookup while the chosen interval is kept, and a
  newly seen address is confirmed after one minute whatever the interval. New preference `wan_check_min` (0, 1 or 15).
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
  proposal in PR 1). Nothing is requested while the setting is off; with it on the container checks the address with
  `api.ipify.org` at the chosen interval (see above) and asks `ipwho.is` for the provider only when the address changes and once a day,
  accepts only public IP addresses and shows lookup failures plainly (`GET /api/public-ip`). An unknown provider reads
  *not reported*.
- **WAN history** (needs that option): every sample is stored with the address and provider (`np_targets.wan_ip` /
  `wan_isp`, added by an automatic migration; older rows stay without a WAN). A *WAN source* selector above the chart and
  in the targets table (one shared choice) filters the chart, the targets statistics and the compare mode
  (`wan_ip` / `wan_isp` parameters of `/api/history`, `/api/targets/history`, `/api/targets/recent`; `GET /api/wan/sources`);
  with a single WAN selected the overall quality score is not drawn. The CSV export has `wan_ip` and `wan_isp` columns.
  *Forget WAN addresses…* (`POST /api/wan/forget`) erases the recorded addresses; the retention period deletes them with
  the samples.
- **Notification when the external IP or provider changes** (`wan_notify`, off by default): before / now, a warning when the
  provider changed and an information message when only the address did; a change must be seen by two checks in a row,
  the last known WAN survives restarts (`/data/wan-state.json`), at most 6 messages per hour.
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
