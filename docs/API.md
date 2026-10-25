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
| `GET /api/loss[&target=HOST]` | packet-loss report: `data.summary` (`samples`, `lossy`, `avg_loss`, `max_loss`, `episodes`), `data.targets[]`, `data.episodes[]` (`target`, `start`, `end`, `duration`, `peak`, `avg`, `samples`; newest first, ≤ 100), `data.outages[]` (Turbo-mode outages overlapping the window: `id`, `start_ts`, `end_ts` (`null` while ongoing), `duration`, `kind` `internet`/`router`, `status` `open`/`pending`/`sent`/`covered`/`off`/`capped`/`counted`/`stopped`; newest first, ≤ 100) and `truncated` Also `wan_ip` / `wan_isp` (limit the report to one WAN source; empty `wan_ip` = samples without a WAN) and `data.wan_sources`: `{wan_ip, wan_isp, samples, lossy, avg_loss, max_loss, first_ts, last_ts}` for every source in the window, whatever the filter (the Turbo `outages` are not split by source) |
| `GET /api/export/targets.csv` | raw target samples as CSV (formula-injection safe, capped at 50 000 rows, `X-Export-Truncated` header) |
| `GET /api/settings` | `{settings, storage, started_at}` |
| `POST /api/settings` | partial update of `enabled`, `turbo` (booleans; the response also carries `turbo_available` — whether the watcher runs), `loss_pct`, `latency_ms`, `jitter_ms`, `dns_ms`, `confirm_samples`, `recovery_samples`; strict validation (unknown / null / blank / non-finite → `400`); same-origin browser requests with header `X-NetProbe-Request: settings` only |
| `GET /api/prefs` | `{prefs, schedule:{next_ts, timezone}}`. `prefs.speedtest_service` is `""` (follow the container variable `SPEEDTEST_BACKEND`), `python` (Speedtest.net via speedtest-cli), `ookla` (official Ookla CLI) or `fast` (Fast.com). `prefs.speedtest_schedule` is `container` (the variables `SPEEDTEST_ENABLED`/`SPEEDTEST_INTERVAL` decide), `off`, `interval` (`speedtest_interval_h`, integer 1–168) or `daily` (`speedtest_times`, 1–4 different `HH:MM` values in `timezone`); `prefs.speedtest_notify` (boolean) queues a message after every automatic test; `prefs.show_public_ip` (boolean) enables `/api/public-ip` and the recording of the WAN with every sample; `prefs.wan_check_min` is how often the address is checked: `15` (default), `1` or `0` (only on `POST /api/public-ip/refresh`; no WAN history and no change message then); `prefs.wan_notify` (boolean) queues a message when the address changes; `prefs.speedtest_notify_manual` (boolean) sends the completion message after manual tests too; `prefs.speedtest_drop_pct` (`0` off, `20`, `30` or `50`) warns when an automatic test is that many percent below the 7-day median of the same service; `prefs.notify_unraid` (boolean, default `true`) is the Unraid integration: when `false`, `GET /api/notifications/pending` returns an empty list (the other fields stay), and switching it on acknowledges what accumulated meanwhile. `/api/history`, `/api/targets/history` and `/api/targets/recent` accept `wan_ip=<address>&wan_isp=<provider>` (empty `wan_ip` = samples without a WAN; an invalid address is `400`); `history` without the quality score (`score: null`) and `recent` rows carry `wan_sources`. `schedule.next_ts` is the next planned test (`null` for `container`/`off`). `503` when the file is damaged |
| `POST /api/prefs` | partial update of the preferences; strict validation (unknown key / value → `400`); same-origin browser requests with header `X-NetProbe-Request: settings` only |
| `GET /api/backups` | `{running, started_at, finished_at, error, name, backups:[{name, ts, bytes}], keep, db_bytes, free_bytes}` (newest first; the last job's result in `error` / `name`) |
| `POST /api/backups` | same-origin header required. Starts a background copy of the database (`VACUUM INTO`, consistent while monitoring writes) into `/data/db-backups/netprobe-YYYYMMDD-HHMMSS.sqlite` (mode 600) and answers `202` with the status; `409` while one runs, `507` when the free space is below the database size + 20 % + 8 MiB. The newest `keep` (5) are kept |
| `GET /api/backups/<name>` | downloads one backup (only names of that form; symlinks are never served) |
| `POST /api/backups/<name>/delete` | same-origin header required; deletes one backup, answers with the status |
| `GET /api/speedtest/series` | `{tests, range}`: the Speedtests of the last `range` seconds (default 7 days, `from`/`to` also work; at most 2000, oldest first) with `ts, ping_ms, download_mbps, upload_mbps, backend, server_name, server_country` and the extras `jitter_ms, packet_loss_pct, loaded_down_ms, loaded_up_ms` (`null` when the service did not measure them). The manual-test result (`POST /api/speedtest/run`) also carries `loaded_ping_ms` and `loaded_up_ping_ms` |
| `GET /api/telegram` | `{enabled, configured, token_set, chat_id, source: "ui"\|"env"\|null, last_ok_ts, last_error, last_error_ts}` — never the token (`Cache-Control: no-store`) |
| `POST /api/telegram` | same-origin header required. Any of `enabled` (boolean), `token` (empty or missing = keep the saved one; `123456789:AAH…`), `chat_id` (`-1001234567890`, a number or `@channel`), `clear: true` (forget token and chat). `400` with a reason for a bad value or when the token/chat come from the environment (`source: "env"`). Switching it on does not send older messages. Answers with the same object as `GET` |
| `POST /api/telegram/test` | same-origin header required. Optional `{token, chat_id}` (the form values, not saved), else the saved ones; sends a test message. `{ok:true, …}` or `502` with Telegram's reason (the token is removed from the text) |
| `GET /api/public-ip` | `{enabled:false}` while `prefs.show_public_ip` is off (no outbound request is made). When on: `{enabled:true, every_min, refreshing, ip, isp, source, checked_at, error}` — the address is checked with `api.ipify.org` every `every_min` minutes (never when it is `0`) and the ISP name is looked up with `ipwho.is` when the address changes and once a day (`icanhazip.com` is the fallback for the address; plain-text answers give `isp: null`), only public addresses are accepted, the answer is served from the cache (a failure is retried after 1 minute), `pending: true` while the first lookup runs (in manual mode the first answer is empty, `checked_at: null`, until a refresh), `refreshing: true` while a lookup is running; `ip` is `null` and `error` names the failing services when every lookup failed |
| `POST /api/public-ip/refresh` | The *Refresh* button of the WAN / ISP card (needs the `X-NetProbe-Request: settings` header, `403` without it): starts one background lookup (a second press while it runs starts none) and answers `202` with the current state of `GET /api/public-ip`; `{enabled:false}` while the option is off. Poll `GET /api/public-ip` until `refreshing` is `false` |
| `POST /api/history/cleanup` | body `{days, dry_run, confirm}`; `days` is 7, 30, 90, 180 or 365. `dry_run: true` only counts; a real deletion needs `confirm: true` (otherwise `400`). Returns `{days, cutoff, dry_run, deleted:{table:rows}, total, bytes_before, bytes_after, ts}`; `409` while another cleanup runs. Same-origin browser requests with header `X-NetProbe-Request: settings` only. `prefs.history_retention_days` (0, 7, 30, 90, 180, 365) sets the nightly cleanup; `GET /api/prefs` also returns `history:{last, next_ts, db_bytes, oldest_ts}` |
| `GET /api/wan/sources` | WAN sources seen in the period (same `range` / `from`+`to` as the other history endpoints): `{sources:[{wan_ip, wan_isp, samples, first_ts, last_ts}] newest first (wan_ip null = samples recorded without a WAN), current:{wan_ip, wan_isp}\|null}` |
| `POST /api/wan/forget` | body `{"confirm": true}` erases every recorded `wan_ip` / `wan_isp`; -> `{forgotten: rows}`. Same-origin browser requests with header `X-NetProbe-Request: settings` only |
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
