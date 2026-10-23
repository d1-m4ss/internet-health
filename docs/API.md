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
| `GET /api/loss[&target=HOST]` | packet-loss report: `data.summary` (`samples`, `lossy`, `avg_loss`, `max_loss`, `episodes`), `data.targets[]`, `data.episodes[]` (`target`, `start`, `end`, `duration`, `peak`, `avg`, `samples`; newest first, ≤ 100) and `truncated` |
| `GET /api/export/targets.csv` | raw target samples as CSV (formula-injection safe, capped at 50 000 rows, `X-Export-Truncated` header) |
| `GET /api/settings` | `{settings, storage, started_at}` |
| `POST /api/settings` | partial update of `enabled`, `loss_pct`, `latency_ms`, `jitter_ms`, `dns_ms`, `confirm_samples`, `recovery_samples`; strict validation (unknown / null / blank / non-finite → `400`); same-origin browser requests with header `X-NetProbe-Request: settings` only |
| `GET /api/config` | upstream config plus `router_name` |

An *episode* is a run of samples with loss > 0 that is not interrupted by a clean sample or by a gap longer than
180 s. DNS targets have no loss measurement and are not part of the report.

## Host-only endpoints (notification queue)

Require the header `X-NetProbe-Notify-Key: <contents of /data/notify-token>`; otherwise `403`.

| Endpoint | Purpose |
|---|---|
| `GET /api/notifications/pending` | `{notifications:[{id,ts,level,subject,description,message}], latest_probe_ts, process_started_at, max_sample_age}` — at most 20, oldest first; empty while alerts are disabled |
| `POST /api/notifications/<id>/ack` | idempotent acknowledgement; `404` for unknown ids |

There is intentionally no endpoint that sends a test notification.
