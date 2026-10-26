# Installing Internet Health on Unraid

Internet Health is published as a ready container image, `ghcr.io/d1-m4ss/internet-health` (tags `latest` and the version numbers such
as `1.1.0`). It is the NetProbe image, pinned by digest, with the Internet Health files already inside (see the
[Dockerfile](../Dockerfile)): **nothing to copy, nothing to mount** besides the data folder. Everything is configured in the web UI.

> **Before you start:** the web UI has no authentication. Run it on a LAN, not on an exposed port.

Variables used below (adjust to your setup):

```sh
D=/mnt/user/appdata/internet-health      # host folder mounted as /data in the container (database, settings, backups)
```

- [New install](#new-install) · [Notifications](#notifications) · [Verify](#verify) · [Updating](#updating) ·
  [Moving the data folder](#moving-the-data-folder) · [Switching from a NetProbe + files install](#switching-from-a-netprobe--files-install) · [Restoring a backup](#restoring-a-backup) ·
  [Rollback](#rollback) · [Troubleshooting](#troubleshooting)

## New install

On the Unraid terminal fetch the template once:

```sh
wget -O /boot/config/plugins/dockerMan/templates-user/my-InternetHealth.xml \
  https://raw.githubusercontent.com/d1-m4ss/internet-health/main/docs/unraid/internet-health.xml
```

*Docker → Add Container → Template: **InternetHealth*** and set:

| Field | Value |
|---|---|
| Web UI port | `8788` (or any free port) |
| Data | `/mnt/user/appdata/internet-health` — the bridge looks there first (and in `netprobe_database`, the name earlier versions used, if that is what exists) |
| Router IP / name | your router, e.g. `192.168.1.1` / `Router` — it becomes the first node of the path view |
| Sites to ping | `fast.com,google.com,youtube.com,1.1.1.1,8.8.8.8` — keep **two IP addresses** next to the names: they tell "DNS is down" from "the internet is down" |
| Time zone / schedule time zone | for example `Europe/Berlin` (the daily Speedtest times use the second one) |
| Telegram token / chat ID | optional — you can also set Telegram later in the web UI |

*Apply*, then open `http://<unraid>:8788`. The first probe cycle takes about a minute to appear. Language (English / Russian) and style
(NetProbe / UniFi) are in the page header. With docker compose use [`examples/docker-compose.image.yml`](../examples/docker-compose.image.yml)
(see [INSTALL-COMPOSE.md](INSTALL-COMPOSE.md)).

## Notifications

Where messages go is chosen in the web UI, *Settings → Integrations*, and both channels are independent (either, both or none):

- **Telegram, straight from the container** — nothing to install on the host. Create a bot with @BotFather, add it to your chat, enter the
  token and chat ID, *Send test*, Save. Details in [NOTIFICATIONS.md](NOTIFICATIONS.md#integrations-unraid-and-telegram).
- **Unraid** (email and every agent you configured in *Settings → Notification Settings*) — needs the small bridge on the Unraid host, because
  only the host can call Unraid's own `notify`. Install it once:

  ```sh
  V=main        # or a version tag such as v1.1.0
  install -d /boot/config/custom
  wget -O /boot/config/custom/netprobe-unraid-notify.sh https://raw.githubusercontent.com/d1-m4ss/internet-health/$V/src/netprobe-unraid-notify.sh
  wget -O /boot/config/plugins/dynamix/netprobe-notify.cron https://raw.githubusercontent.com/d1-m4ss/internet-health/$V/scripts/netprobe-notify.cron
  update_cron
  crontab -c /etc/cron.d -l | grep netprobe          # the entry must be listed
  ```

  The bridge needs `curl`, `jq` and `flock` (present on Unraid) and reads `/data/notify-token` and `alert-settings.json` from `$D`
  (override with `NETPROBE_NOTIFY_TOKEN_FILE`, `NETPROBE_SETTINGS_FILE`, `NETPROBE_STATE_FILE`, `NETPROBE_API_BASE` in the cron line if your
  folder or port differ). Switch the *Unraid* integration off in the UI and the bridge gets nothing; switch it on again and nothing from the
  gap is replayed.

## Verify

```sh
curl -s http://<unraid>:8788/api/about                              # {"name":"Internet Health","version":"1.1.0"}
curl -s http://<unraid>:8788/api/prefs | jq .                       # 200: your settings
curl -s http://<unraid>:8788/api/targets/recent | jq '.data|length' # one entry per target (> 0 after a minute)
curl -s -o /dev/null -w '%{http_code}\n' http://<unraid>:8788/api/notifications/pending      # 403 without the key
ls -l $D/notify-token                                               # -rw------- (600)
cat $D/notify-bridge-state.json                                     # with the bridge: health "OK", last_poll updates every minute
docker logs InternetHealth 2>&1 | grep -i -E 'traceback|extension failed'   # nothing
docker ps --format '{{.Names}} {{.Status}}' | grep -i -E 'internethealth|netprobe'   # "(healthy)" after the first minutes
```

(Use your container's name: `InternetHealth` from the template, `NetProbe` if you switched an existing container.) No notification is sent
during installation; use *Send test* in Telegram settings, or unplug the router's internet cable for at least 3 minutes to see the real
thing: a change needs two bad probe cycles in a row (about 50 s each), so shorter interruptions are treated as blips — see
[NOTIFICATIONS.md](NOTIFICATIONS.md). *Turbo mode* (thresholds) catches outages from about 10 s.

## Updating

Releases are published on [GitHub](https://github.com/d1-m4ss/internet-health/releases) with the notes from the
[CHANGELOG](../CHANGELOG.md); the version is in the page footer and in `GET /api/about`.

- **Unraid:** *Docker → Check for updates → Update* (the template uses `:latest`). Or pin a version: set the repository to
  `ghcr.io/d1-m4ss/internet-health:1.1.0` and move on when you decide to.
- **Compose:** `docker compose pull && docker compose up -d`.
- **By hand from the command line:** `docker pull ghcr.io/d1-m4ss/internet-health:latest`, then recreate the container with Unraid's own script
  `/usr/local/emhttp/plugins/dynamix.docker.manager/scripts/rebuild_container <container name>` (it rebuilds from the template).

**From 1.4.0 the app runs as `99:100` (nobody:users), not as root.** The first start after the update gives the files in the data folder to that user
(you see one `entrypoint: handing files …` line in the log); the host-side bridge and your backups keep working because root reads everything. Files that
the bridge creates in the folder stay root's. To keep the old behaviour set the variable `PUID` to `0`; to use another user set `PUID` / `PGID`. Going back
to an older version needs nothing: root can use files of any owner.

The data folder is untouched by an update: new database columns and files are created on first use, and a new feature stays **off** until you
switch it on in the UI. Before a big step make a copy: *Settings → History → Back up now* (see [Restoring a backup](#restoring-a-backup)).
After the update reload the page with Ctrl+F5 (the browser may keep the old one) and run the [checks above](#verify).

To go back, set the repository to the previous version tag (for example `:1.0.1`); the database stays valid for the older version.

## Moving the data folder

Earlier versions suggested `/mnt/user/appdata/netprobe_database`; it keeps working and the bridge finds it. To rename it to the current default, stop
the container, move the folder, change the *Data* path in the container's settings (or the template file) and start it again:

```sh
docker stop InternetHealth
mv /mnt/user/appdata/netprobe_database /mnt/user/appdata/internet-health
# edit the Data path of the container (Docker → InternetHealth → Edit → Apply), which recreates it; then start it if it is stopped
```

If the bridge has its own paths in the cron line (`NETPROBE_NOTIFY_TOKEN_FILE`, `NETPROBE_SETTINGS_FILE`, `NETPROBE_STATE_FILE`, or `NETPROBE_DATA_DIR` for the
folder as a whole), change them too and run `update_cron`; without them it follows the folder by itself. The variable `D` in this guide is that folder.

## Switching from a NetProbe + files install

If you ran the earlier way (the `bmmbmm01/netprobe` image with three read-only bind-mounted files, which Internet Health no longer supports),
switch to the image and keep your data folder, so history and settings stay:

1. Back up: `mkdir -p $D/backups/pre-image && cd $D/backups/pre-image`, then
   `cp /boot/config/plugins/dockerMan/templates-user/my-NetProbe.xml .`, `docker inspect NetProbe > container-inspect.json`, and
   `docker stop NetProbe && cp -p $D/netprobe.sqlite $D/alert-settings.json $D/netprobe-prefs.json . ; docker start NetProbe`
   (skip the files that do not exist).
2. `docker pull ghcr.io/d1-m4ss/internet-health:latest`
3. Edit `/boot/config/plugins/dockerMan/templates-user/my-NetProbe.xml`: set `<Repository>` to `ghcr.io/d1-m4ss/internet-health:latest` and delete the
   three `<Config … Target="/app/…">` lines (the file mounts). Leave everything else. (Or *Docker → NetProbe → Edit*, change *Repository* and remove the
   three path entries, *Apply*.) A mount that stays would override the new file inside the image with the old one.
4. Recreate the container from the template: `/usr/local/emhttp/plugins/dynamix.docker.manager/scripts/rebuild_container NetProbe` (or *Apply* in the GUI).
5. Run the [checks](#verify). The container keeps its name, port, variables and `/data`; the notification bridge and its cron line stay as they are.
6. When you are sure, the old image can go: `docker rmi bmmbmm01/netprobe@sha256:…` (the new image keeps the layers it needs).

This is exactly how the author's own server was switched.

## Restoring a backup

Backups are made on the page (*Settings → History → Back up now*) into `$D/db-backups/` (the 5 newest are kept) and can be downloaded
there. A copy is a complete SQLite file. To go back to one:

```sh
docker stop NetProbe                                                 # your container's name
cp -p $D/netprobe.sqlite $D/netprobe.sqlite.before-restore          # keep what is there now
rm -f $D/netprobe.sqlite-wal $D/netprobe.sqlite-shm                 # leftovers of the old file must not be applied to the new one
cp -p $D/db-backups/netprobe-<date>-<time>.sqlite $D/netprobe.sqlite
docker start NetProbe
```

A copy taken on another machine goes to the same place (`$D/netprobe.sqlite`). The copy contains all history and the Speedtest data, so
treat it like the database itself (the web UI has no authentication either).

## Rollback

- **An update went wrong:** set the repository to the previous version tag and recreate the container (see [Updating](#updating)); restore a database copy
  only if you need to (see above).
- **Back to the old way (image + files) after switching:** restore the template you saved in step 1 of the switch
  (`cp $D/backups/pre-image/my-NetProbe.xml /boot/config/plugins/dockerMan/templates-user/`), `rebuild_container NetProbe`.
- **Stop the Unraid notifications only:** switch the *Unraid* integration off in the UI, or remove the cron file:
  `rm /boot/config/plugins/dynamix/netprobe-notify.cron && update_cron`.

The extension keeps its data in separate `np_*` tables; the upstream tables are untouched, so the stock NetProbe works on the same folder.

## Troubleshooting

| Symptom | Check |
|---|---|
| `404` on `/api/targets/*` or the page is the stock NetProbe | the container is not the Internet Health image (or a leftover file mount overrides it): `docker inspect <name> --format '{{.Config.Image}} {{json .HostConfig.Binds}}'` |
| No messages arrive | *Settings → Integrations*: is the channel on? Telegram: *Send test* and the status line under it; Unraid: `grep netprobe-notify /var/log/syslog`, agents configured?, `cat $D/notify-bridge-state.json` |
| "monitoring is not working" | the probe loop stalled or the API is unreachable from the host (`curl http://127.0.0.1:8788/api/settings`) |
| Token errors from the bridge | `notify-token` must be mode `600`, a regular file, ≥ 32 hex characters |
| `unhealthy` | `docker logs <name> \| tail`; the health check asks `GET /api/health` inside the container: it is `503` when the probe loop has stored no cycle for the interval plus five minutes (the reason is in the answer: `curl http://<host>:8788/api/health`) |
| The page does not change after an update | Ctrl+F5; check the footer version |
