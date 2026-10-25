# HTTP API

All endpoints are served by the NetProbe container (default `http://<unraid>:8788`). The extension **adds**
endpoints and optional parameters; the upstream API (`/api/config`, `/api/score/*`, `/api/speedtest/*`,
`/api/logs/live`) keeps working. There is no authentication, so keep the port on your LAN.

## Choosing a period

Every history endpoint accepts either

- `range=<seconds>` — the last N seconds (1 … 2 678 400, i.e. 31 days), or
- `from=<epoch>&to=<epoch>` — an absolute window; both are required, `to` is clamped to "now", and the window
  must be positive and at most 31 days. Invalid input → `400`.

## Endpoints added by the extension

| Endpoint | Returns |
|---|---|
| `GET /api/targets/recent` | one row per target: latest sample plus statistics for the period (`samples`, `avg_*`, `max_*`, `state`, per-IP breakdown in `addresses`), and `started_at` |
| `GET /api/targets/history?target=HOST[&address=IP]` | time-bucketed samples of one target (≤ 600 buckets): `ts`, `latency`, `jitter`, `loss`, `max_loss`, `address` |
| `GET /api/history` | native aggregate rows (bucketed ≤ 600) with `max_loss_pct` and `dns_per_server` |
| `GET /api/events` | latest 200 confirmed state changes: `ts`, `target`, `previous`, `state`, `reasons[]`, `metrics` |
| `GET /api/loss[&target=HOST]` | packet-loss report: `data.summary` (`samples`, `lossy`, `avg_loss`, `max_loss`, `episodes`), `data.targets[]`, `data.episodes[]` (`target`, `start`, `end`, `duration`, `peak`, `avg`, `samples`; newest first, ≤ 100), `data.outages[]` (Turbo-mode outages overlapping the window: `id`, `start_ts`, `end_ts` (`null` while ongoing), `duration`, `kind` `internet`/`router`, `status` `open`/`pending`/`sent`/`covered`/`off`/`capped`/`counted`/`stopped`; newest first, ≤ 100) and `truncated` |
| `GET /api/export/targets.csv` | raw target samples as CSV (formula-injection safe, capped at 50 000 rows, `X-Export-Truncated` header) |
| `GET /api/settings` | `{settings, storage, started_at}` |
| `POST /api/settings` | partial update of `enabled`, `turbo` (booleans; the response also carries `turbo_available` — whether the watcher runs), `loss_pct`, `latency_ms`, `jitter_ms`, `dns_ms`, `confirm_samples`, `recovery_samples`; strict validation (unknown / null / blank / non-finite → `400`); same-origin browser requests with header `X-NetProbe-Request: settings` only |
| `GET /api/prefs` | `{prefs, schedule:{next_ts, timezone}}`. `prefs.speedtest_service` is `""` (follow the container variable `SPEEDTEST_BACKEND`), `python` (Speedtest.net via speedtest-cli), `ookla` (official Ookla CLI) or `fast` (Fast.com). `prefs.speedtest_schedule` is `container` (the variables `SPEEDTEST_ENABLED`/`SPEEDTEST_INTERVAL` decide), `off`, `interval` (`speedtest_interval_h`, integer 1–168) or `daily` (`speedtest_times`, 1–4 different `HH:MM` values in `timezone`); `prefs.speedtest_notify` (boolean) queues a message after every automatic test; `prefs.show_public_ip` (boolean) enables `/api/public-ip`. `schedule.next_ts` is the next planned test (`null` for `container`/`off`). `503` when the file is damaged |
| `POST /api/prefs` | partial update of the preferences; strict validation (unknown key / value → `400`); same-origin browser requests with header `X-NetProbe-Request: settings` only |
| `GET /api/public-ip` | `{enabled:false}` while `prefs.show_public_ip` is off (no outbound request is made). When on: `{enabled:true, ip, isp, source, checked_at, error}` — the address and ISP name are looked up with `ipwho.is` (fallbacks `api.ipify.org`, `icanhazip.com`; they give `isp: null`), only public addresses are accepted, the answer is cached for 5 minutes (a failure for 1 minute); `ip` is `null` and `error` names the failing services when every lookup failed |
| `POST /api/history/cleanup` | body `{days, dry_run, confirm}`; `days` is 7, 30, 90, 180 or 365. `dry_run: true` only counts; a real deletion needs `confirm: true` (otherwise `400`). Returns `{days, cutoff, dry_run, deleted:{table:rows}, total, bytes_before, bytes_after, ts}`; `409` while another cleanup runs. Same-origin browser requests with header `X-NetProbe-Request: settings` only. `prefs.history_retention_days` (0, 7, 30, 90, 180, 365) sets the nightly cleanup; `GET /api/prefs` also returns `history:{last, next_ts, db_bytes, oldest_ts}` |
| `GET /api/config` | upstream config plus `router_name`; `speedtest_backends_available` also has `fast` |
| `GET /api/speedtest/status` | `{running, started_at, finished_at, success, error, result}` of the last test started with `"async": true`; `running` is also `true` while a scheduled test runs |
| `POST /api/speedtest/run` | upstream endpoint; with `"async": true` (the UI) it answers `202` at once and runs the test in the background (`409` when one is already running; follow it with `GET /api/speedtest/status`); without it the call blocks until the result as before. `backend` may now be `fast`. Without `backend` the service from `/api/prefs` is used. A Fast.com result has `server.name` `Netflix, <city>`; `upload_mbps` is `null` when only the download could be measured |

An *episode* is a run of samples with loss > 0 that is not interrupted by a clean sample or by a gap longer than
180 s. DNS targets have no loss measurement and are not part of the report.

## Host-only endpoints (notification queue)

Require the header `X-NetProbe-Notify-Key: <contents of /data/notify-token>`; otherwise `403`.

| Endpoint | Purpose |
|---|---|
| `GET /api/notifications/pending` | `{notifications:[{id,ts,level,subject,description,message}], latest_probe_ts, process_started_at, max_sample_age}` — at most 20, oldest first; empty while alerts are disabled |
| `POST /api/notifications/<id>/ack` | idempotent acknowledgement; `404` for unknown ids |

There is intentionally no endpoint that sends a test notification.
