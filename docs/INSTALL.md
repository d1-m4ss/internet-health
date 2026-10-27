# Installing Internet Health

[Русская версия](INSTALL.ru.md) · Unraid: [INSTALL-UNRAID.md](INSTALL-UNRAID.md)

Internet Health is one container image, `ghcr.io/d1-m4ss/internet-health`. It runs on any machine with Docker that stays on at home: a mini PC
or an old laptop with Linux, a NAS with a Docker app (Synology Container Manager, QNAP Container Station, TrueNAS, OpenMediaVault, Unraid), a
Proxmox VM or LXC, a Windows or Mac computer with Docker Desktop. You start it, open the web page and configure everything there; the only
thing it keeps on the machine is its data folder.

> **The image is for x86-64 (amd64) machines only for now.** ARM boards such as a Raspberry Pi or an ARM-based NAS cannot run it yet.
>
> **The web page has no login.** Keep the port on your home network; never forward it to the internet.

- [What the machine needs](#what-the-machine-needs) · [docker run](#option-1-docker-run) · [docker compose](#option-2-docker-compose) ·
  [NAS and other GUIs](#option-3-a-nas-or-another-docker-gui) · [First steps in the page](#first-steps-in-the-page) · [Check that it works](#check-that-it-works) ·
  [Notifications](#notifications) · [Updating](#updating) · [Backups and restoring](#backups-and-restoring) · [Removing it](#removing-it) ·
  [Building from source](#building-from-source) · [Troubleshooting](#troubleshooting)

## What the machine needs

- Docker (`docker version` answers) and, for option 2, `docker compose version`.
- To be on the same network as the router, wired if possible: Internet Health measures the connection **of the network it is in**. A machine
  on Wi-Fi measures the Wi-Fi too.
- About 150 MB for the image; the database grows by roughly 10–15 MB a week with the default targets (you can limit how long history is kept).
- Outbound internet for the checks; nothing inbound except the web page on your LAN.

## Option 1: docker run

```sh
mkdir -p ~/internet-health
docker run -d --name InternetHealth --restart unless-stopped \
  -p 8788:8080 \
  -v ~/internet-health:/data \
  -e TZ=Europe/Berlin -e APP_TIMEZONE=Europe/Berlin \
  -e ROUTER_IP=192.168.1.1 -e ROUTER_NAME=Router \
  -e SITES=fast.com,google.com,youtube.com,1.1.1.1,8.8.8.8 \
  ghcr.io/d1-m4ss/internet-health:latest
```

Open `http://<that machine's address>:8788`.

## Option 2: docker compose

Copy [`examples/docker-compose.yml`](../examples/docker-compose.yml) into a folder, edit the values below and run `docker compose up -d` there.

| Setting | What to put |
|---|---|
| `TZ`, `APP_TIMEZONE` | your time zone, e.g. `Europe/London` (the daily Speedtest times use `APP_TIMEZONE`) |
| `ROUTER_IP` | your router's LAN address, e.g. `192.168.1.1` (`ip route` on Linux, `ipconfig` on Windows: "default gateway") |
| `ROUTER_NAME` | the name shown in the page and in messages, e.g. `ASUS RT-AX86U` |
| `SITES` | names **and two IP addresses**: `fast.com,google.com,youtube.com,1.1.1.1,8.8.8.8`; the addresses let it tell "DNS is down" from "the internet is down" |
| `DNS_NAMESERVER_n` / `_IP` | DNS servers to check (up to 4; Google, Quad9 and Cloudflare by default); add your router or Pi-hole / AdGuard as number 4 if you like |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | optional; or set Telegram later in the page |
| `ports` | change the left number (`8788`) if that port is taken |
| `volumes` | the data folder on this machine (`./data` = next to the compose file) |
| `PUID`, `PGID` | optional; the user the app runs as (default `99:100`). The container starts as root only to give the data folder to that user, then drops the privileges; use your own user's numbers (`id -u`, `id -g`) if you want to open the files yourself, `PUID=0` to run as root |

All variables: [README → Configuration](../README.md#configuration).

## Option 3: a NAS or another Docker GUI

Every Docker GUI asks for the same things: create a container from the image `ghcr.io/d1-m4ss/internet-health:latest`, map **a local port
(8788) to container port 8080**, map **a folder to `/data`**, add the variables from the table above, set the restart policy to "unless stopped"
/ "always". A compose / "project" / "stack" screen (Synology Container Manager, Portainer, Dockge, TrueNAS apps) takes the compose file above
as it is — only change the `./data` path to a folder of the NAS. On Unraid use the template instead: [INSTALL-UNRAID.md](INSTALL-UNRAID.md).

## First steps in the page

1. The first measurements appear after about a minute. Language (English / Russian) and style (Classic / UniFi / Control) are in the page header.
2. *Settings → Integrations*: switch Telegram on to get messages (see [Notifications](#notifications)).
3. *Speedtest* tab: pick the service (Fast.com, Cloudflare or Ookla) and, if you want, a schedule (for example every night at 04:00).
4. *Settings*: optionally *Show my external IP and provider*, and *Keep history* to limit how long data is kept.

## Check that it works

```sh
docker ps --format '{{.Names}} {{.Status}}' | grep InternetHealth          # "(healthy)" after the first minutes
curl -s http://127.0.0.1:8788/api/about                                    # {"name":"Internet Health","version":"…"}
curl -s http://127.0.0.1:8788/api/health                                   # {"status":"ok", …}
docker logs InternetHealth 2>&1 | tail -20                                 # probe lines, no "Traceback"
```

To see a real alert: unplug the router's internet cable for **at least 3 minutes**. A change needs two bad probe cycles in a row (about 50 s
each, about 100 s while the internet is down), so shorter interruptions are treated as blips — *Turbo mode* in the thresholds catches outages from
about 10 s. You get one "No internet" message and, when the cable is back, one "back to normal" with the duration.

## Notifications

*Settings → Integrations* switches each destination on its own; history and charts are recorded either way.

- **Telegram** — works on any machine, nothing to install: create a bot with @BotFather, add it to your chat, enter the token and the chat ID,
  *Send test*, *Save*. Details: [NOTIFICATIONS.md](NOTIFICATIONS.md#integrations-unraid-and-telegram).
- **Unraid** (email and every Unraid agent) — only on Unraid, through a small script on the host: [INSTALL-UNRAID.md → Notifications](INSTALL-UNRAID.md#notifications).

Messages wait in a queue while the internet is down and are delivered when it is back (with a note how late they are).

## Updating

```sh
docker compose pull && docker compose up -d          # compose
# docker run: docker pull ghcr.io/d1-m4ss/internet-health:latest, then remove and start the container again with the same command
```

Releases and their notes: [GitHub Releases](https://github.com/d1-m4ss/internet-health/releases), [CHANGELOG](../CHANGELOG.md); the version is in the
page footer. The data folder stays as it is, and a new feature stays off until you switch it on. Reload the page with Ctrl+F5 after an update. To stay
on a version, use a tag such as `:1.4.0` instead of `:latest`; to go back, start the previous tag on the same folder.

Since 1.4.0 the app runs as `99:100`, not as root; the first start after updating from an older version gives the files in the data folder to that user
(one `entrypoint: handing files …` line in the log).

## Backups and restoring

*Settings → History → Back up now* writes a consistent copy of the database while monitoring keeps running (`<data>/db-backups/`, the 5 newest are
kept; download or delete them there). To go back to one:

```sh
docker stop InternetHealth
cp -p data/netprobe.sqlite data/netprobe.sqlite.before-restore          # keep what is there now
rm -f data/netprobe.sqlite-wal data/netprobe.sqlite-shm                 # leftovers of the old file must not be applied to the copy
cp -p data/db-backups/netprobe-<date>-<time>.sqlite data/netprobe.sqlite
docker start InternetHealth
```

What is in the data folder (the file names come from the project's origin and stay for compatibility):

```
data/
├── netprobe.sqlite          ← the database (history, Speedtest results, message queue)
├── alert-settings.json      ← alert thresholds
├── netprobe-prefs.json      ← Speedtest, external IP, history and integration settings from the page
├── telegram.json            ← Telegram token and chat (mode 600), only if set in the page
├── db-backups/              ← copies made from the page
├── ookla/                   ← Ookla's program, only after the first Ookla test
└── notify-token, *-state.json …   ← small state files
```

Treat the folder and its copies as private: they hold your history, your external IP addresses and the Telegram token.

## Removing it

`docker rm -f InternetHealth` (or `docker compose down`), then delete the data folder if you do not need the history.

## Building from source

`docker build -t internet-health:dev .` in a clone of the repository (the base image is pinned by digest, the packages by `requirements.txt`), then
use `internet-health:dev` as the image. The tests need only `pip install -r requirements.txt`: `python -m unittest discover -s tests`.

## Troubleshooting

| Symptom | Check |
|---|---|
| The page does not open | the port mapping (`docker ps`: `0.0.0.0:8788->8080`), a firewall on the machine, the address you open |
| `unhealthy` in `docker ps` | `curl http://127.0.0.1:8788/api/health` says why: `503` with the last error when no probe cycle was stored for the interval plus five minutes; `docker logs InternetHealth` |
| `no matching manifest for linux/arm64` or `exec format error` | the machine is ARM (Raspberry Pi, ARM NAS): the image is x86-64 only for now |
| `Permission denied` / `readonly database` | the data folder must be writable by `PUID:PGID` (default 99:100); the container fixes the owner at start unless it runs with `--user`; on network shares that refuse `chown` set `PUID=0` |
| No Telegram messages | *Settings → Integrations*: is Telegram on? *Send test* and the status line under it (queued messages, last error) |
| The page does not change after an update | Ctrl+F5; check the version in the footer |
