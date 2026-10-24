# Architecture

## Components

| File | Runs | Role |
|---|---|---|
| `src/app.py` | in the container (replaces `/app/app.py`) | the upstream app plus probe history, DNS reply tracking, `ROUTER_NAME`, and a cached public WAN lookup whose current result is attached to each probe. See `src/app.py.patch` for the exact diff against `upstream/app.py`. The extension is imported defensively: if it fails to start, the stock monitoring keeps running. |
| `src/netprobe_extensions.py` | in the container | storage, state machine, flood control, message composition, HTTP routes |
| `src/index.html` | served by Flask | the single-file UI (two styles, two languages, no external assets) |
| `src/netprobe-unraid-notify.sh` | on the Unraid host (cron, every minute) | drains the queue through Unraid's `notify`, acknowledges what was delivered |

## Data

Only additive tables/columns are created in the existing SQLite file: `np_targets` (every sample of every target,
including the public WAN IP/ISP known at sample time),
`np_state` (current state and streaks), `np_events` (confirmed changes), `np_notifications` (the delivery queue),
`np_flap` (muting state). Settings live in `/data/alert-settings.json` and the bridge token in
`/data/notify-token`. Nothing is deleted automatically.

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

## Testing strategy

- `tests/test_extensions.py`, `test_messages.py`, `test_loss_window.py`, `test_review_fixes.py` — the extension
  against a synthetic database (`tests/fixture_db.py`) with a fake clock.
- `tests/test_app_integration.py` — the real `probe_loop` with only `ping` and the DNS resolver faked.
- `tests/test_notify_bridge.py` — the real Bash bridge against a fixture HTTP server and a fake `notify`.
- `tests/simulate_network.py` — end-to-end failure scenarios on a virtual clock.
- `tests/test_ui.cjs` — Playwright: behaviour, both styles, both colour schemes, narrow layout, WCAG AA contrast.
