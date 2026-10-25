# Notifications

Notifications are produced by the extension (`Extension.compose`) and delivered by the host bridge through
Unraid's own `notify` script, so they reach whatever agents you configured (email, Telegram, the Unraid GUI).

## Format

Each notification has three parts, matching what Unraid's agents expect:

| Part | Used as | Notes |
|---|---|---|
| `subject` (`-s`) | title | `🔴 NetProbe: No internet` — icon + diagnosis, ≤ 120 chars |
| `description` (`-d`) | one-line summary | diagnosis + hint, ≤ 240 chars, no line breaks |
| `message` (`-m`) | details | lines separated by the two characters `\n` (that is how Unraid's email/Telegram agents split a message) |

Importance follows severity: `alert` for router / internet / Docker-gateway outages and names that do not resolve, `warning` for
interruptions, flapping and DNS servers that stop answering, `normal` for recovery. Only names, numbers and fixed text reach the message; control
characters and backslashes are stripped, and the size is bounded so the bridge never rejects a batch.

## Diagnosis

The headline is derived from the **current state of all targets**, not just from what changed in this probe:

| Situation | Headline |
|---|---|
| Docker gateway on the Unraid host silent | 🔴 Docker gateway on Unraid is not responding |
| Router silent **and** other targets failing | 🔴 Router is not responding |
| Router silent but internet and DNS fine | 🟠 Router does not answer ping (probably ICMP filtering / wrong `ROUTER_IP`) |
| All external hosts silent, DNS servers answer | 🟠 External hosts do not answer ping |
| All external hosts and DNS silent | 🔴 No internet |
| Pings by IP work, pings by name fail | 🔴 Names do not resolve (DNS) |
| Some external hosts silent | 🟠 Unreachable: Google, … |
| Loss / latency only | 🟠 Unstable connection: … |
| One DNS server silent | 🟠 DNS server not responding: … |
| Some targets recovered, others not | 🟠 Partially recovered … |
| Everything is OK again | ✅ Everything is back to normal |

A DNS server counts as silent only if **no** query got a reply (NXDOMAIN/NODATA prove it is alive; timeouts and
SERVFAIL/REFUSED do not).

## Example (from `tests/simulate_network.py -v`)

```
[alert] 🔴 NetProbe: No internet
  🔴 No internet

  What changed:
  • 🔴 Google (google.com) - no ping reply
  • 🔴 Google IP (8.8.8.8) - no ping reply
  • 🔴 DNS Google (8.8.8.8) - not answering DNS queries
  ...
  The router responds but external hosts do not. Likely an ISP or line problem (cable, ONU).

  Time: 20:12, 23.10.2026 UTC

[normal] ✅ NetProbe: Everything is back to normal
  What changed:
  • 🟢 Google (google.com) - back to normal (problem lasted 10 min)
  ...
```

Line icons: 🔴 down · 🟠 degraded · 🟢 recovered · 🔴⬇️ got worse · 🟠⬆️ improving but not recovered · 🟡 unstable.

## Flood control

- A change is confirmed only after N consecutive samples (default 2 to alert, 2 to recover).
  Leaving OK counts any non-OK samples, so a link that alternates between 100 % and 50 % loss is still detected.
- Everything confirmed in one probe is **one** message.
- A steady problem sends nothing more — no reminders (a 6-hour outage = 1 message).
- A problem that only shifts between *Down* and *Degraded* produces at most one update per target per hour.
- A target that changes confirmed state more than 4 times in an hour (or more than 8 times in six hours) is
  **muted**: one *Unstable connection* message, then silence (events keep being recorded) until it stays
  unchanged for 30 minutes, followed by one *stabilized* message.
- Single-probe blips are never sent; they only appear in the per-target history and the loss report (events are
  recorded only for confirmed changes).

## Turbo mode (short outages)

The regular checks cannot see an outage shorter than about 1.5 minutes. Turbo mode (UI → *Notification thresholds*
→ *⚡ Turbo mode*, stored as `turbo` in `alert-settings.json`, off by default) adds a watcher thread inside the app:

- Every 5 s one ICMP echo (1 s timeout) goes, in parallel, to `ROUTER_IP` and up to two public IP addresses: the
  IP-literal `SITES`, or the public DNS servers when `SITES` has none. While the setting is off it only re-reads the
  settings file — no pings. Without any public IP address the watcher cannot run; the UI says so
  (`turbo_available: false` in `/api/settings`).
- A round is *down* when **every** internet address fails (kind *router* when the router failed too). An outage
  starts after 2 down rounds and ends after 2 good rounds, so it is detected from about 10 s; a single lost echo,
  or one silent address while another answers, is never an outage.
- One message per outage, **after recovery** (level `warning`):

```text
⚡ Short internet outage: 40 s

• From 14:03:12 to 14:03:52 (40 s)
• What failed: the router answered, but no internet host did (1.1.1.1, 8.8.8.8)

Too short for the regular checks; caught by Turbo mode.

Time: 14:05, 23.10.2026 MSK
```

- No duplicates: the decision waits until the regular probe cycle that was running at recovery has been recorded
  (at most 5 minutes). Turbo stays silent if the regular monitor queued an *alert* or *warning* during the outage
  (or up to 2 minutes before it — a regular alert carries the start time of its cycle), or already considered one of
  the Turbo addresses *Down* when the outage began (for example an alert sent before a container restart).
  An outage that could not be decided within an hour (Turbo switched off, watcher not running) is never sent.
- At most 3 Turbo messages per hour; outages over the limit are counted into the next message
  (*+2 more short outages since 13:10*). With alerts disabled, outages are still recorded but not sent.
- Every Turbo outage, with what happened to its message, is listed in the loss report (`/api/loss` → `outages`).

## Scheduled Speedtest

The *Speedtest* tab (*Schedule and notifications*) can run the test automatically and tell you the result:

- **As set in the container** (default): the variables `SPEEDTEST_ENABLED` / `SPEEDTEST_INTERVAL` decide, as before.
- **Off**, **every N hours** (1–168, counted from the last test, manual ones included) or **every day at set times**
  (up to four `HH:MM` values in the container's `APP_TIMEZONE`). A daily test missed because the container was
  restarting still runs within two hours of its time; later it waits for the next time. A failed test is not
  retried before the next slot.
- **Notification** (off by default; follows the main *notifications enabled* switch): after every automatic test one
  message, level `normal`; when the test fails, level `warning` with the reason. The measuring service (Speedtest.net,
  Ookla CLI or Fast.com) is named; an unmeasured upload reads *not measured*, never 0.

```text
📶 Scheduled Speedtest (Fast.com)

• Download: 482.3 Mbps
• Upload: 91.0 Mbps
• Ping: 12.0 ms
• Server: Netflix, Example City, RO
• Previous: ↓ 470 / ↑ 88 Mbps (03:01, 09.03)

Time: 03:02, 10.03.2026 EET
```

## WAN change

With *Show my external IP and provider* on, NetProbe remembers the last WAN (`/data/wan-state.json`). *Send a notification
when the external IP or provider changes* sends one message when the address changes — useful when the router fails over to
a backup link. The new address must be seen by two checks in a row (the second one a minute after the first, whatever the check interval), so one odd answer is not a change; there is no message in manual mode, where nothing watches the link; a
provider name that only changed its spelling with the same address is ignored; at most 6 messages per hour. The level is
`warning` when the provider changed and `normal` when only the address did.

```text
🌐 WAN changed: Beta Cable

• Before: Alpha Net · 203.0.113.10 (since 14:03, 24.10)
• Now: Beta Cable · 198.51.100.20

The router may have switched to another link, or the provider assigned a new address.

Time: 14:05, 25.10.2026 CET
```

Delivery is at-least-once: a message stays queued until the bridge acknowledges it, and a failing `notify` never
loses messages. The bridge (`src/netprobe-unraid-notify.sh`) also reports its own health: two stale polls in a row
produce *monitoring is not working*, two fresh ones *monitoring is working again*.
