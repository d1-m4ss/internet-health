# Changelog

The project is called **Internet Health** (formerly “Internet Pulse” in 1.0.0 and “NetProbe Enhanced” before; container, image, file and table names keep the upstream `netprobe` /
`np_` names so existing installations are unaffected).

## Unreleased

- **Speedtest chart: speed, delay and loss together.** The Speed / Delay tabs are gone: speed (Mbps), delay (ms) and packet loss (%) are three
  panels on one time axis, each with its own scale, and every line has its own on / off button, so any combination can be shown at once. The
  colours are fixed and conventional: download blue, upload red, idle ping green, ping while downloading amber, ping while uploading purple,
  jitter cyan, loss pink. With *All services* every service keeps the colour of the line but gets its own shade, dashes and marker
  (Fast.com solid ●, Speedtest.net lighter and dashed ■, Ookla CLI darker and dotted ▲), with a key under the buttons; the popup names the service.
  The grey buttons say why a line is empty: the delay under load and jitter come from Fast.com tests made since 1.1.0.
- **The main chart has a colour per metric** in the NetProbe style too (loss coral, delay blue, jitter violet, DNS green), and the legend swatch
  follows it; the peak markers of the loss chart stay amber.

## 1.2.1 — 2026-10-26

- **The Speedtest chart, rebuilt.** Speed, delay and (when measured) packet-loss views; every line has a button to show or hide it (remembered);
  one service is drawn at a time (the newest test's by default, or *All services*), so Fast.com and Speedtest.net results no longer tangle into
  one zig-zag; clear names (*Download ↓*, *Upload ↑*, idle ping, ping while downloading / uploading, jitter, packet loss) and the unit on the axis;
  the axis makes room for wide numbers; the period includes *All time* and is applied without asking the server again
  (`GET /api/speedtest/series?range=all`). The placeholder delay that speedtest-cli stores when it cannot measure (1 800 000 ms) is shown
  as unknown, never as a spike.
- **The Speedtest history table**: newest first, the last 10, the chart's period, or all (the choice is remembered).
- **A popup on every chart**: hover (or touch) a point of the main chart, the compare chart or the Speedtest chart to see the time and the
  value(s) at once instead of the slow browser tooltip.
- **The Telegram chat ID is kept private like the token**: `GET /api/telegram` returns `chat_set` instead of `chat_id`, the field shows
  *saved · leave empty to keep it*, and an empty `chat_id` in a save keeps the saved one.

## 1.2.0 — 2026-10-26

- **The default data folder is `/mnt/user/appdata/internet-health`** in the Unraid template, the compose examples and the guides. Existing
  installations keep working with `netprobe_database` (nothing is moved by an update); the bridge script looks in `internet-health` first, then in
  `netprobe_database`, and takes the whole folder from `NETPROBE_DATA_DIR` when set. INSTALL-UNRAID describes moving the folder.

- **Telegram, when the link is down.** The queue and its retries already existed (stored in the database, 30 s … 15 min pauses, a day); now a
  message that waited more than two minutes ends with `⏱ Delivered N min late: the event happened at HH:MM`, so a late alert is not taken for a
  current one, and the note is kept even when the text has to be cut to Telegram's 4096 characters. *Settings → Integrations* shows how many
  messages are waiting, since when and when the next attempt is; `GET /api/telegram` gained `queued`, `oldest_ts`, `retry_ts`.

## 1.1.0 — 2026-10-26

- **Documentation for the image-first install.** README (EN/RU), INSTALL-UNRAID and INSTALL-COMPOSE now start from the ready image and the Unraid
  template (Telegram needs nothing on the host; the Unraid bridge is optional), with updating by tags, switching from the earlier "image + three
  files" install, restoring a backup and troubleshooting; the file-mount way moved to an appendix. ARCHITECTURE, NOTIFICATIONS, SECURITY, API and
  UPSTREAM describe the release pipeline and the integrations.
- **WAN source in the loss report and in the compare chart.** `GET /api/loss` accepts `wan_ip` / `wan_isp` and returns `wan_sources` (how each source
  fared in the window); the report shows that table, clicking a row selects the source. The compare chart gets *One line per WAN source*
  (up to four sources; every picked series is drawn once per source).
- **A compact pinned menu on phones** in the NetProbe style: one row (the mark instead of the name), about 47 px instead of 90–100.
- **Database backups from the page** (Settings → *History*): *Back up now* makes a complete, consistent copy while monitoring keeps running
  (`VACUUM INTO`, a background job, refused when the disk is too full) in `/data/db-backups`, keeps the 5 newest, and lists them with
  *Download* and *Delete*. Deleting history by hand makes a copy first and stops if that fails. `GET/POST /api/backups`,
  `GET /api/backups/<name>`, `POST /api/backups/<name>/delete`; restoring is described in INSTALL-UNRAID.
- **Speedtest, extended.**
  - A **chart** in the *Speedtest* tab: speed (download, upload) or delay (idle ping, ping while downloading / uploading, jitter) over 24 h,
    7 or 30 days (the choice is remembered); an unmeasured value is a gap. `GET /api/speedtest/series`.
  - **Jitter and the delay under load** are measured by the Fast.com test (a one-byte request every 0.4 s while it downloads and
    uploads, median of at least three answers), stored next to each test (`np_speedtest_extra`) and shown in the card, the history and
    the messages; Ookla's jitter and packet loss are kept too.
  - A message **after manual tests** too (a second switch, `speedtest_notify_manual`).
  - A **warning when the speed drops** 20 / 30 / 50 % below the 7-day median of the same service (`speedtest_drop_pct`, scheduled tests
    only, at least three earlier tests): one warning per drop and one message when it is back.
## 1.0.1 — 2026-10-26

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
