# Quick install with docker compose

Internet Health runs from one ready container image, `ghcr.io/d1-m4ss/internet-health`: you write a short compose file, start it, and open the
web page. Nothing to copy or mount besides the data folder. (On Unraid you can also use the template: see
[INSTALL-UNRAID.md](INSTALL-UNRAID.md), which also covers updating, backups and switching from the older "image + three files" install.)

> The web UI has **no authentication** (as in upstream NetProbe). Keep port 8788 on your LAN.

What ends up where:

```
/mnt/user/appdata/internet-health/         ← the container's /data (change the path for your host)
├── netprobe.sqlite                          ← the database, created by the container
├── alert-settings.json                      ← alert thresholds, created on first start
├── netprobe-prefs.json                      ← created when you change a Speedtest / external-IP / history / integrations setting in the UI
├── telegram.json                            ← the Telegram token and chat (mode 600), only if you set Telegram in the UI
├── db-backups/                              ← database copies made from the page (Settings → History)
├── history-cleanup.json, wan-state.json, speedtest-state.json …   ← small state files
└── notify-token                             ← secret for the Unraid bridge, created on first start (mode 600)
```

Requirements: Docker with the `docker compose` command (`docker compose version`; on Unraid install the *Docker Compose Manager* plugin from
Community Applications and paste the same YAML there, or use the template). The image is `linux/amd64`.

## 1. The compose file

Copy [`examples/docker-compose.image.yml`](../examples/docker-compose.image.yml) and edit:

| Setting | What to put |
|---|---|
| `TZ`, `APP_TIMEZONE` | your time zone, e.g. `Europe/London` (the daily Speedtest times use `APP_TIMEZONE`) |
| `ROUTER_IP` | your router's LAN address, e.g. `192.168.1.1` |
| `ROUTER_NAME` | the label shown in the UI and in notifications, e.g. `ASUS RT-AX86U` |
| `SITES` | keep names **and two IP addresses**: `fast.com,google.com,youtube.com,1.1.1.1,8.8.8.8`. The IP targets let it tell "DNS is down" from "the internet is down" |
| `DNS_NAMESERVER_n` / `_IP` | DNS servers to probe (up to 4); add your router/AdGuard as number 4 if you like |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | optional; or set Telegram later in the web UI |
| `ports` | change the left number (`8788`) if the port is taken |
| `volumes` | the data folder on your host |
| `PUID`, `PGID` | optional; the user the app runs as (default `99:100`). The container starts as root, gives the data folder to that user and drops the privileges; `PUID=0` keeps root |

The image already starts the web server with four threads (`GUNICORN_CMD_ARGS=--threads 4`); do not raise the number of workers, because every worker
would start its own probe loop. Use `image: ghcr.io/d1-m4ss/internet-health:latest` to follow releases or a version tag such as `:1.1.0` to stay on one.

## 2. Start and check

```sh
docker compose up -d
docker logs InternetHealth 2>&1 | tail -20        # a probe cycle takes ~50 s; no "Traceback" / "extension failed"
curl -s http://127.0.0.1:8788/api/about           # {"name":"Internet Health","version":"1.1.0"}
curl -s http://127.0.0.1:8788/api/targets/recent | jq '.data|length'      # one row per target (> 0 after a minute)
```

Open `http://<host>:8788`. The first probe cycle takes about a minute to appear; the header drop-downs switch language (English / Russian) and style
(NetProbe / UniFi). The container reports `healthy` in `docker ps` after the first minutes.

## 3. Notifications

*Settings → Integrations* in the web UI switches the destinations on their own (either, both or none; history and charts are recorded either way):

- **Telegram**: needs nothing on the host — enter the bot token and the chat ID, *Send test*, Save. See [NOTIFICATIONS.md](NOTIFICATIONS.md#integrations-unraid-and-telegram).
- **Unraid**: only on an Unraid host, through the small bridge script (it calls Unraid's own `notify`, so email and every configured agent work). Install it as
  described under *Notifications* in [INSTALL-UNRAID.md](INSTALL-UNRAID.md#notifications) (two `wget`s and `update_cron`), with the data folder mounted at
  `/mnt/user/appdata/internet-health` or the paths given to the bridge through environment variables in its cron line.

**Testing the alerts:** unplug the router's internet cable for **at least 3 minutes**. A change needs two bad probe cycles in a row; a cycle takes about 50 s
(about 100 s while the internet is down), so an interruption shorter than about 1.5 minutes is never reported and one of about 2.5 minutes always is — by
design, short ones are treated as blips (see [NOTIFICATIONS.md](NOTIFICATIONS.md)). You should receive one "No internet" message, and one "back to normal"
message with the outage duration when the cable is back. To test **Turbo mode**, enable *⚡ Turbo mode* in the thresholds, save, and unplug the cable for 30–60 s.

## 4. Optional settings in the web UI (no restart; stored in `netprobe-prefs.json`)

- *Speedtest* tab → **Measurement service**: **Fast.com** (default) or the official Ookla CLI (only selectable once installed: `docker exec -it InternetHealth internet-health-ookla install` shows Ookla's terms, asks for `I ACCEPT` and downloads the program into `/data/ookla`; `status` and `remove` are the other commands).
- *Speedtest* tab → **Schedule and notifications**: every day at set times or every N hours; a message after every automatic test (and optionally after manual ones);
  a warning when the speed drops 20 / 30 / 50 % below the 7-day median. Leaving *As set in the container* keeps the variables `SPEEDTEST_ENABLED` /
  `SPEEDTEST_INTERVAL` in charge (with `SPEEDTEST_ENABLED: "false"` nothing runs by itself). The tab also draws the results as a chart (speed, or delay under load for Fast.com).
- Settings → **History**: *Keep history* (7 / 30 / 90 / 180 days or 1 year, deleted once a night; off by default), preview and on-demand deletion, and **Back up now**
  for a consistent copy of the database (the 5 newest are kept in `db-backups/`; restoring is described in [INSTALL-UNRAID.md](INSTALL-UNRAID.md#restoring-a-backup)).
- Settings → **Show my external IP and provider**: off by default; with it on the container asks `api.ipify.org` for the address (every 15 minutes by default, every minute,
  or only when you press *Refresh* in the card) and `ipwho.is` for the provider name when it changes, shows the *WAN / ISP* card and records which source carried each
  measurement (the *WAN source* selectors). *Forget WAN addresses…* (History) erases what was recorded. Remember to press **Save** under that block.

## 5. Updating and tuning

| Want to… | Do this |
|---|---|
| update | `docker compose pull && docker compose up -d` (data and settings stay; reload the page with Ctrl+F5). The version is in the page footer |
| stay on a version | `image: ghcr.io/d1-m4ss/internet-health:1.1.0` |
| change alert thresholds (loss %, RTT, jitter, DNS, number of samples, Turbo) | web UI → *Notification thresholds*, or edit `alert-settings.json` (no restart) |
| change targets, router name, DNS servers, probe interval | edit the `environment:` block and `docker compose up -d` |
| back up the database | web UI → Settings → History → *Back up now*, or copy `netprobe.sqlite` after `docker stop` |

Never copy the data folder's `notify-token`, `telegram.json`, `alert-settings.json` or the database into a public place.

## Building from source

To run a version that is not released yet: `docker build -t internet-health:dev .` in a clone of the repository (the base image is pinned by digest, the
packages by `requirements.txt`), then use `image: internet-health:dev` in the compose file. The tests (`python -m unittest discover -s tests`) need only
`pip install -r requirements.txt`.
