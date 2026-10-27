# Changelog

The project is called **Internet Health** (formerly “Internet Pulse” in 1.0.0 and “NetProbe Enhanced” before; container, image, file and table names keep the upstream `netprobe` /
`np_` names so existing installations are unaffected).

## Unreleased

- Language and theme are one-click buttons in the header: **RU / EN** (no dropdown) and **Light / Dark / Auto**. Auto follows the browser's setting live;
  the choice is remembered in this browser. Classic now has a light theme too (it used to be dark only); UniFi no longer depends on the browser alone.
- Maintainers: an Unraid plugin is prepared in `unraid-plugin/` (installs the notification bridge and the Docker template; built by
  `scripts/build-plugin.py`, see `docs/UNRAID-PLUGIN.md`). It is not published and changes nothing for the container.

## 1.4.3 — 2026-10-27

- The banner for a silent DNS server reads "Interruptions: 1 of 10 checks" (it said "1 checks") and shows the hint *answers ping, not DNS queries*
  for an amber check as well, not only for a red one.

## 1.4.2 — 2026-10-27

- **One silent DNS server of several is an interruption, not an outage.** If at least one other DNS server answers, a server that gave no
  answer is *degraded* (amber banner, "working with interruptions"); the check is *down* (red) only when every DNS server is silent. Alerts
  already treated a single dead DNS server as a warning, so the page now agrees with them.
- **The log says why a DNS server was silent.** Once when it stops answering (`DNS server 1.1.1.1 gave no answer to any query (last error:
  LifetimeTimeout …)`) and once when it answers again, not every cycle.
- The line `Detected default gateway inside container` was logged on every refresh of the web page; it is now logged only when the gateway changes.

- The top banner no longer says *targets* for what it counts: DNS servers are checks of their own (7 addresses + 3 DNS servers was shown as
  "1 of 10 targets", while the Targets table lists only the addresses). It now reads "No response from 1 of 10 checks", names a DNS server as
  *DNS 1.1.1.1* instead of `dns:1.1.1.1`, and says when that server's address still answers ping but not DNS queries.

## 1.4.1 — 2026-10-26

- **Fixed: the Speedtest chart blew its text up on wide screens.** It was drawn in a fixed 640-unit canvas and stretched to the card, so on a
  1700 px window the axis text was about 2.7 times too large. It is now drawn in real pixels (like the main chart) and redrawn when the
  window is resized; a UI test checks the text size at 390–2400 px.
- The page footer, the browser tab title, the jitter caption and the start-up log line no longer say *NetProbe* / *Unraid monitor*.

## 1.4.0 — 2026-10-26

- **Not tied to Unraid or NetProbe any more.** The README and the install guides are written for any Docker machine (mini PC, NAS, Proxmox,
  Docker Desktop), with a `docker run` quick start, compose (`examples/docker-compose.yml`, data in `./data`) and the NAS GUIs; Unraid has its own
  guide. New guides in Russian: `docs/INSTALL.ru.md`, `docs/INSTALL-UNRAID.ru.md` (`docs/INSTALL-COMPOSE.md` became `docs/INSTALL.md`).
- **Messages are signed *Internet Health*** instead of *NetProbe* (`🔴 Internet Health: No internet`), the Unraid bridge sends them as the event
  *Internet Health*, and the page no longer says Unraid or NetProbe where it means the machine or the app: the path starts at *Server*, the
  style is called *Classic*, the gateway message says *Docker gateway on the server*. File, table and header names (`netprobe.sqlite`, `np_*`,
  `X-NetProbe-Request`, the bridge script) stay, so existing installations keep working.

- **Three Speedtest services: Fast.com, Cloudflare and Ookla.**
  - **Cloudflare** (new): Internet Health measures against the nearest Cloudflare data centre itself (speed.cloudflare.com, the endpoints its
    page uses): download, upload, latency, jitter and the latency under load; nothing to install. Shown as *Cloudflare* with the data centre
    (e.g. `OTP`); with *All services* its lines are darker and dotted with ▲ markers.
  - **Ookla** (Speedtest.net servers) works without a console now: the official CLI is not in the image; the first Ookla test downloads it from
    Ookla (install.speedtest.net, version 1.2.0, ~1 MB) into `<data>/ookla` and later tests reuse it (it survives updates). Choosing the service is
    the acceptance of Ookla's terms, which the service's help text says; the CLI runs with `--accept-license --accept-gdpr` (as MySpeed does).
    It reports packet loss and the latency while downloading / uploading too. Lighter, dash-dotted lines with ◆ markers.
  - Removed with the old way of installing Ookla: `internet-health-ookla`, the separate terms acceptance (`SPEEDTEST_OOKLA_*`), the server choice
    of manual tests (`SPEEDTEST_SERVER`, `SPEEDTEST_CSV*`, `SPEEDTEST_EXCLUDE`, `server_id` / `force_auto` of `POST /api/speedtest/run`), the
    `speedtest_ookla_*` / `speedtest_server*` fields of `/api/config` and `requested_server_id(s)` of the speedtest history answers.
  - Cloudflare and Fast.com publish no API guarantee for their endpoints: if they change, that service fails with a readable message and the
    others keep working.

- **The app no longer runs as root.** The container starts as root only to hand the data folder to `PUID:PGID` (default `99:100`, Unraid's nobody:users;
  files created by root in older versions are converted on the first start) and then drops the privileges for good. `PUID=0` keeps the old behaviour.
  The smoke test of the image checks the user, the handed-over files, ping without privileges and `PUID=0`.

## 1.3.0 — 2026-10-26

**The project no longer depends on the NetProbe Docker image.** The image is built from this repository's own `Dockerfile` on `python:3.14-slim`
(pinned by digest) with every Python package pinned in `requirements.txt` (Flask 3.1.3, Werkzeug 3.1.9, dnspython 2.9.0, gunicorn 26.2.0 …); Dependabot
proposes newer ones weekly. `src/app.py` is plain source now: no `app.py.patch`, no hash check of a foreign image, the file-mount way is gone.
The image is smaller and holds only what is used: no Postgres client, none of the original's `static/` scripts, no `curl` / `gnupg` / `bash`.

- **The Speedtest.net service (speedtest-cli) is removed.** Its project was archived in April 2026, it is unofficial and it reported Mibit/s as
  Mbps (about 5 % low next to the other services). Services are now **Fast.com** (the default) and the **official Ookla CLI**. A saved choice of
  Speedtest.net or `SPEEDTEST_BACKEND=python` means Fast.com; old results keep the name *Speedtest.net* in the history and the chart. The HTTPS
  switch of manual tests and `speedtest_secure` of `/api/config` are gone with it.
- **Ookla CLI is installed with one command** that survives updates: `docker exec -it InternetHealth internet-health-ookla install` shows Ookla's
  terms, asks for `I ACCEPT`, downloads Ookla's own program into `/data/ookla` (it is not part of the image) and records the acceptance in the data
  folder. `status` and `remove` are the other commands. `SPEEDTEST_OOKLA_PATH` and `SPEEDTEST_OOKLA_ACCEPTANCE_FILE` default to the data folder.
- **A failing probe cycle no longer stops the monitoring.** An exception in a cycle (a locked or full database, …) used to end the probe thread
  silently while the container stayed "healthy". Now the cycle is logged and the next one runs; the Docker health check asks the new
  **`/api/health`**, which answers `503` (with the last error) when no cycle has been stored for the interval plus five minutes.
- **DNS latency counts only answered queries.** A timeout or a refused query used to be averaged in as the time spent waiting (up to 3 s), so one lost
  reply looked like a slow server. A server that answers nothing now gets the same penalty a failed ping gets and no per-server value is stored.
- **History queries use an index.** `measurements`, `dns_measurements` and `speedtests` had no index on the time; "the newest N rows" read the whole
  table (0.22 s per request with 1 million rows, now under a millisecond). The indexes are created at start. SQLite writers wait up to 30 s for a lock.
- **Ping to an unreachable host no longer takes 13 s.** ping waited its default 10 s for the last reply, longer than the 8 s limit, so every check of a
  dead host was logged as an error and lengthened the cycle. Now `-W 2` (`PING_WAIT`) and a limit that fits.
- The Postgres mode (`DB_ENGINE`, `USE_POSTGRES`, `psycopg2`) is removed; the variables are ignored (a warning is logged). SQLite was the only supported database.

## 1.2.3 — 2026-10-26

- Fixed: long line names in a chart popup (e.g. “Download ↓ (download) · Fast.com”) stuck out of the popup; they now wrap inside it.

## 1.2.2 — 2026-10-26

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
