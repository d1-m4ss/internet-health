# Quick install with docker-compose (from scratch)

Use this guide for a **fresh** install: you create the container from a YAML file, put three files into the
container's data folder, and add a one-line cron job on the Unraid host. If you already run NetProbe and want to
convert that container, use [INSTALL-UNRAID.md](INSTALL-UNRAID.md) instead (it also covers backup and rollback).

> **Shortcut:** from the first release there is a ready image, no files to copy: see [INSTALL-UNRAID.md → Install the ready image](INSTALL-UNRAID.md#install-the-ready-image-recommended) and [`examples/docker-compose.image.yml`](../examples/docker-compose.image.yml). This guide is the file-mount way.

> The web UI has **no authentication** (as in upstream NetProbe). Keep port 8788 on your LAN.

What ends up where:

```
/mnt/user/appdata/netprobe_database/         ← the container's /data (database, settings, token)
├── custom/                                  ← YOU put the 3 extension files here
│   ├── app.py
│   ├── netprobe_extensions.py
│   └── index.html
├── netprobe.sqlite                          ← created by the container
├── alert-settings.json                      ← created by the container on first start (alert thresholds)
├── netprobe-prefs.json                      ← created the first time you change a Speedtest / external-IP / history setting in the UI
├── history-cleanup.json                     ← created by the first cleanup (what was deleted when)
└── notify-token                             ← created by the container on first start (secret, mode 600)

/boot/config/custom/netprobe-unraid-notify.sh                 ← the bridge, on the Unraid host
/boot/config/plugins/dynamix/netprobe-notify.cron             ← runs the bridge every minute
```

Requirements: Unraid with *Settings → Notification Settings* configured (email and/or Telegram agent), the `docker compose` command
(check with `docker compose version`; if it is missing, install the *Docker Compose Manager* plugin from Community
Applications and paste the same YAML there, or use plain `docker run`, see [INSTALL-UNRAID.md](INSTALL-UNRAID.md) option B).

## 1. Get the files

On your computer (or directly on Unraid):

```sh
git clone https://github.com/<you>/netprobe-enhanced.git && cd netprobe-enhanced
```

Everything below is run on the Unraid server as `root` (SSH or the web terminal); copy the repository there first,
for example `scp -r netprobe-enhanced root@<unraid>:/root/`.

## 2. Create the data folder and add the three extension files

```sh
D=/mnt/user/appdata/netprobe_database
mkdir -p $D/custom
cp src/app.py src/netprobe_extensions.py src/index.html $D/custom/
ls -l $D/custom        # three regular files — not directories!
```

This step must come **before** the first start. The compose file mounts these three paths; if a file is missing,
Docker silently creates an empty *directory* with that name and the container fails.

## 3. Check that the image is the one these files were written for

The three files replace files inside one specific NetProbe image, so the image is pinned by digest.

```sh
docker pull bmmbmm01/netprobe@sha256:386bdc2c177b11aef4a1223dfd91ae47fe2f9804c5449c5f4c2caae1d048c155
bash scripts/verify-image.sh          # expected: "app.py: OK" and "templates/index.html: OK"
```

On `MISMATCH` do not continue with this digest; see *Updating the NetProbe image later* in [INSTALL-UNRAID.md](INSTALL-UNRAID.md).

## 4. Create the container

Copy [`examples/docker-compose.yml`](../examples/docker-compose.yml) to the server and edit these lines:

| Setting | What to put |
|---|---|
| `TZ`, `APP_TIMEZONE` | your time zone, e.g. `Europe/London` |
| `ROUTER_IP` | your router's LAN address, e.g. `192.168.1.1` |
| `ROUTER_NAME` | the label shown in the UI and in notifications, e.g. `ASUS RT-AX86U` |
| `SITES` | keep names **and two IP addresses**: `fast.com,google.com,youtube.com,1.1.1.1,8.8.8.8`. The IP targets let NetProbe tell "DNS is down" from "the internet is down" |
| `DNS_NAMESERVER_n_IP` | DNS servers to probe (up to 4); add your router/AdGuard as number 4 if you like |
| `ports` | change the left number (`8788`) if the port is taken |
| `volumes` | only if your data folder is not `/mnt/user/appdata/netprobe_database` |

Then start it:

```sh
docker compose -f docker-compose.yml up -d
docker logs NetProbe 2>&1 | tail -20        # a probe cycle takes ~50 s; no "Traceback" / "extension failed"
```

Open `http://<unraid>:8788`. The first probe cycle takes about a minute to appear. The header drop-downs switch
language (English / Russian) and style (NetProbe / UniFi). After the first start the data folder contains
`netprobe.sqlite`, `alert-settings.json` and `notify-token`.

## 5. Install the notification bridge on the Unraid host

The container only *queues* notifications; this small script hands them to Unraid's own `notify` (so they reach
every agent you configured).

```sh
install -d /boot/config/custom
cp src/netprobe-unraid-notify.sh /boot/config/custom/
cp scripts/netprobe-notify.cron /boot/config/plugins/dynamix/ && update_cron
crontab -c /etc/cron.d -l | grep netprobe          # the entry must be listed
```

If your data folder is not `/mnt/user/appdata/netprobe_database`, or the web port is not 8788, give the bridge the
new locations by editing the cron file: put the variables in front of the command, e.g.

```
* * * * * NETPROBE_API_BASE=http://127.0.0.1:8800 NETPROBE_NOTIFY_TOKEN_FILE=/mnt/user/appdata/np/notify-token NETPROBE_SETTINGS_FILE=/mnt/user/appdata/np/alert-settings.json NETPROBE_STATE_FILE=/mnt/user/appdata/np/notify-bridge-state.json /usr/bin/bash /boot/config/custom/netprobe-unraid-notify.sh 2>&1 | /usr/bin/logger -t netprobe-notify
```

## 6. Check that everything works

```sh
curl -s http://127.0.0.1:8788/api/targets/recent | jq '.data|length'      # one row per target (> 0 after a minute)
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8788/api/notifications/pending   # 403 (needs the secret)
ls -l $D/notify-token                                                       # -rw------- (600)
cat $D/notify-bridge-state.json                                             # "health":"OK", last_poll changes every minute
grep netprobe-notify /var/log/syslog | tail                                 # no errors
```

Nothing is sent during installation. The first real event (for example a few minutes without internet) is delivered
through the same path that [`tests/simulate_network.py`](../tests/simulate_network.py) exercises with a fake `notify`.

**How to test the alerts yourself:** unplug the router's internet cable for **at least 3 minutes**. A change needs two
bad probe cycles in a row; a cycle takes about 50 s (about 100 s while the internet is down), so an interruption
shorter than about 1.5 minutes is never reported and one of about 2.5 minutes always is — by design, short ones are treated as blips (see [NOTIFICATIONS.md](NOTIFICATIONS.md)). You should receive one
"No internet" message, and one "back to normal" message with the outage duration when the cable is back.

To test **Turbo mode**, enable *⚡ Turbo mode* in the thresholds, save, and unplug the cable for 30–60 s. About
10 s after the cable is back (plus up to ~1.5 min for the regular cycle that was running, and up to a minute for the
cron bridge) you get one "⚡ Short internet outage" message with the duration; the outage also appears under
*Short outages (Turbo)* in the loss report.

## 6b. Optional: nightly Speedtest, Speedtest service, external IP

All of this is set in the web UI and needs no restart (stored in `$D/netprobe-prefs.json`):

- *Speedtest* tab → **Measurement service**: Speedtest.net (default), the official Ookla CLI (only selectable when
  its binary is installed in the container) or **Fast.com**.
- *Speedtest* tab → **Schedule and notifications**: *Every day at set times* (for example `03:00`; the time zone is
  `APP_TIMEZONE` from your compose file) or *Every N hours*, and tick the notification box to get one Unraid message
  with the result after every automatic test (a failed test sends a warning with the reason). Leaving *As set in the
  container* keeps the old behaviour: `SPEEDTEST_ENABLED` / `SPEEDTEST_INTERVAL` decide, and with
  `SPEEDTEST_ENABLED: "false"` nothing runs by itself.
- Settings → **History** → *Keep history*: the database grows forever by default. Choose 7 / 30 / 90 / 180 days or 1 year and records older than that are deleted once a night. *Show how many*
  previews, *Delete…* deletes on demand after a confirmation. Deletion cannot be undone — copy `$D/netprobe.sqlite`
  first if the history matters.
- Settings → **Show my external IP and provider**: off by default; when ticked, the container asks `api.ipify.org` for
  your address every 15 minutes by default (or every minute, or only when you press *Refresh* in the card; and `ipwho.is` for the provider name when it changes) and shows them in the *WAN / ISP* card
  under the status banner. Every measurement is then stored with the address and provider that carried it (the *WAN
  source* selectors above the chart and the targets table); tick *Send a notification when the external IP or provider
  changes* to be told about a failover. *Forget WAN addresses…* (History) erases what was recorded.

## 7. Tuning later (all in the data folder)

| Want to… | Do this |
|---|---|
| change alert thresholds (loss %, RTT, jitter, DNS, number of samples to alert/recover, alerts on/off, Turbo mode) | UI → *Notification thresholds → Configure*, or edit `$D/alert-settings.json` (the bridge reads the same file); no restart needed |
| change targets, router name, DNS servers, probe interval | edit the `environment:` block and run `docker compose up -d` again |
| make the web server answer several requests at once | `GUNICORN_CMD_ARGS: "--threads 4"` in the `environment:` block (already in the example file); do not raise the worker count |
| update the extension files | back up the old ones, copy the new files into `$D/custom/` and `docker restart NetProbe` (templates are cached, so a restart is required) — see [Updating Internet Pulse](INSTALL-UNRAID.md#updating-internet-pulse) |
| Speedtest service, schedule, Speedtest notifications, external IP | UI → *Speedtest* tab and the settings (section 6b); or edit `$D/netprobe-prefs.json` (keys in [API.md](API.md)) |
| see/clear history | everything is in `$D/netprobe.sqlite` (extension tables are prefixed `np_`) |
| stop the notifications only | switch alerts off in the UI, or remove the cron file: `rm /boot/config/plugins/dynamix/netprobe-notify.cron && update_cron` |
| go back to stock NetProbe | remove the three `custom/` mounts from the compose file and `docker compose up -d`; the stock app ignores the `np_*` tables |

Never copy `$D/notify-token`, `$D/alert-settings.json` or the database into a public place.
