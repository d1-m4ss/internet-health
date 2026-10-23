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

Delivery is at-least-once: a message stays queued until the bridge acknowledges it, and a failing `notify` never
loses messages. The bridge (`src/netprobe-unraid-notify.sh`) also reports its own health: two stale polls in a row
produce *monitoring is not working*, two fresh ones *monitoring is working again*.
