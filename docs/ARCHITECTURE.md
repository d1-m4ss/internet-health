# Architecture

## Components

Internet Health is built from this repository's `Dockerfile`: `python:3.14-slim` pinned by digest, the packages of `requirements.txt` (pinned), and the files
below. `.github/workflows/ci.yml` runs the tests and builds/starts the image; a version tag runs `release.yml` (checks, tests, build, smoke test, publish to
GHCR, GitHub Release); Dependabot proposes newer packages and base digests weekly. The project started as a fork of NetProbe_Python (`upstream/`).

| File | Runs | Role |
|---|---|---|
| `src/app.py` | in the container (`/app/app.py`) | the probe loop, the native tables and API (grown from NetProbe_Python's `app.py`, kept in `upstream/` for reference), the hooks into the extension (which holds the Fast.com and Cloudflare clients). The extension is imported defensively: if it fails to start, the basic monitoring keeps running. |
| `src/netprobe_extensions.py` | in the container | storage, state machine, flood control, message composition, HTTP routes |
| `src/index.html` | served by Flask | the single-file UI (two styles, two languages, no external assets) |
| `src/netprobe-unraid-notify.sh` | on the Unraid host (cron, every minute), optional | the Unraid integration: drains the queue through Unraid's `notify` (email and the Unraid agents), acknowledges what was delivered. Telegram does not need it: the container sends to Telegram itself |

## Data

Only additive tables are created in the existing SQLite file: `np_targets` (every sample of every target),
`np_state` (current state and streaks), `np_events` (confirmed changes), `np_notifications` (the delivery queue),
`np_flap` (muting state). Settings live in `/data/alert-settings.json` and the bridge token in
`/data/notify-token`. Nothing is deleted unless a retention period is chosen (below).

## WAN history

`np_targets.wan_ip` / `wan_isp` (added by `ALTER TABLE` when missing) hold the address and provider confirmed recently (within 15 minutes, or two check intervals if longer) when the sample was stored; they stay empty while *Show my external IP* is off. A watcher thread (`start_wan_watcher`)
wakes up every minute and, when the chosen interval (`wan_check_min`: 15, 1 or 0 = manual, never) is due, checks the address with a plain-text request (`api.ipify.org`) and only asks `ipwho.is` for the provider when it
changed or is a day old. The same observation feeds the WAN-change message (two checks in a row, state kept in
`/data/wan-state.json`). Queries accept a `(wan_ip, wan_isp)` filter; the per-WAN summary is rebuilt from `np_targets`, so the native
quality score is not available for it.

## History retention

`history_retention_days` in `/data/netprobe-prefs.json` is `0` (keep everything, the default) or one of 7, 30, 90, 180,
365. With a period chosen, a background thread checks every ten minutes and, in the quiet hours 03:00–05:59 of the
`APP_TIMEZONE` day (and at most once per night; a night missed for two days is made up at any hour), deletes records
older than the period from `measurements`, `dns_measurements`, `speedtests`, `np_targets`, `np_events`, delivered
`np_notifications` and finished `np_outages`. `np_state`, `np_flap`, undelivered notifications and open or pending
outages are never touched. Deletion runs in batches of 20 000 rows with a pause between them and waits for the probe
loop's write lock instead of failing; afterwards the WAL is checkpointed and, for databases under 256 MiB, the file is
`VACUUM`ed. Choosing a period never deletes at once: the first run waits for the next quiet window at least three
hours later. The same code serves the settings page (`POST /api/history/cleanup`: preview with `dry_run`, deletion needs
`confirm`) and a command line: `docker exec NetProbe python /app/netprobe_extensions.py cleanup --days 90 --dry-run`.
The result of the last run is kept in `/data/history-cleanup.json`.

## State machine

Per target, each probe sample is classified `OK` / `DEGRADED` / `DOWN` (thresholds from the settings; a ping with
100 % loss or no reply is `DOWN`; a DNS server that produced no reply is `DOWN`). A change of state is
**confirmed** after N consecutive samples: leaving `OK` counts any non-OK samples, while moving between `DOWN`
and `DEGRADED` needs the same state to repeat. Duplicate or replayed timestamps never advance a streak, and the
state survives container restarts.

## No invented data

A ping with no reply stores `NULL` RTT/jitter (the upstream app substitutes a penalty value for its own score;
that value never reaches the per-target history). Missing samples are drawn as gaps; DNS servers have no loss
series; the path view and uptime bar are computed from stored samples only.

## Notification pipeline

`record_probe` → state machine → `route_event` (flood control: per-hour limit for Down↔Degraded updates, flap
muting) → `compose` (diagnosis from the states of all targets, readable lines, size bounds) → row in
`np_notifications` → bridge `GET /pending` → Unraid `notify` → bridge `POST /ack`. The bridge keeps a small
checkpoint so a crash between "sent" and "acknowledged" cannot resend more than that one message (at-least-once delivery), and runs under `flock` in `/var/run`.

### Telegram

A second consumer of the same queue: `np_notifications.tg` is 0 (not sent), 1 (sent) or 2 (skipped). A sender thread
(`start_telegram`, started from `install_routes` when Telegram is on and configured, or when it is switched on) looks every 5 s for
rows with `tg=0`, sends them oldest first with the Bot API (`telegram_post`, one message a second, a batch of 10), and marks
them. Rows created before it was switched on (`armed_at`) or older than a day are skipped, a failure backs off 30 s doubling to
15 min (or the `retry_after` Telegram asks for). Delivery to Telegram and acknowledgement by the Unraid bridge are independent:
neither takes the message away from the other. The token lives only in `/data/telegram.json` (or the environment).

## Testing strategy

- `tests/test_extensions.py`, `test_messages.py`, `test_loss_window.py`, `test_review_fixes.py` — the extension
  against a synthetic database (`tests/fixture_db.py`) with a fake clock.
- `tests/test_app_integration.py` — the real `probe_loop` with only `ping` and the DNS resolver faked.
- `tests/test_notify_bridge.py` — the real Bash bridge against a fixture HTTP server and a fake `notify`.
- `tests/simulate_network.py` — end-to-end failure scenarios on a virtual clock.
- `tests/test_ui.cjs` — Playwright: behaviour, both styles, both colour schemes, narrow layout, WCAG AA contrast.
