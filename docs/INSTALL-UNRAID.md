# Installing Internet Health on Unraid

[Русская версия](INSTALL-UNRAID.ru.md) · Any other machine: [INSTALL.md](INSTALL.md)

On Unraid, Internet Health is installed from a template like any other container, and Unraid's own notifications (email and every agent) can
receive its messages through a small script on the host. The image is the same one as everywhere else, `ghcr.io/d1-m4ss/internet-health`
(tags `latest` and version numbers such as `1.4.0`); everything is configured in the web page.

> **The web page has no login.** Keep the port on your LAN.

Variables used below (adjust to your setup):

```sh
D=/mnt/user/appdata/internet-health      # host folder mounted as /data in the container (database, settings, backups)
```

- [New install](#new-install) · [Notifications](#notifications) · [Verify](#verify) · [Updating](#updating) ·
  [Moving the data folder](#moving-the-data-folder) · [Switching from a NetProbe + files install](#switching-from-a-netprobe--files-install) ·
  [Restoring a backup](#restoring-a-backup) · [Rollback](#rollback) · [Troubleshooting](#troubleshooting)

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
| Telegram token / chat ID | optional — you can also set Telegram later in the web page |
| Run as user / group (advanced) | `99` / `100` (nobody / users, the Unraid default); `0` runs as root |

*Apply*, then open `http://<unraid>:8788`. The first probe cycle takes about a minute to appear. Language (English / Russian) and style
(Classic / UniFi) are in the page header. Compose works too: [INSTALL.md](INSTALL.md#option-2-docker-compose).

## Notifications

Where messages go is chosen in the web page, *Settings → Integrations*, and both channels are independent (either, both or none):

- **Telegram, straight from the container** — nothing to install on the host. Create a bot with @BotFather, add it to your chat, enter the
  token and chat ID, *Send test*, Save. Details in [NOTIFICATIONS.md](NOTIFICATIONS.md#integrations-unraid-and-telegram).
- **Unraid** (email and every agent you configured in *Settings → Notification Settings*) — needs the small bridge on the Unraid host, because
  only the host can call Unraid's own `notify`. Install it once:

  ```sh
  V=main        # or a version tag such as v1.4.0
  install -d /boot/config/custom
  wget -O /boot/config/custom/netprobe-unraid-notify.sh https://raw.githubusercontent.com/d1-m4ss/internet-health/$V/src/netprobe-unraid-notify.sh
  wget -O /boot/config/plugins/dynamix/netprobe-notify.cron https://raw.githubusercontent.com/d1-m4ss/internet-health/$V/scripts/netprobe-notify.cron
  update_cron
  crontab -c /etc/cron.d -l | grep netprobe          # the entry must be listed
  ```

  (The script and cron file keep their original `netprobe` names so that existing installations keep working.) The bridge needs `curl`, `jq`
  and `flock` (present on Unraid) and reads `notify-token` and `alert-settings.json` from `$D` (override with `NETPROBE_DATA_DIR`, or
  `NETPROBE_NOTIFY_TOKEN_FILE`, `NETPROBE_SETTINGS_FILE`, `NETPROBE_STATE_FILE`, `NETPROBE_API_BASE` in the cron line if your folder or port
  differ). Switch the *Unraid* integration off in the page and the bridge gets nothing; switch it on again and nothing from the gap is replayed.

## Verify

```sh
curl -s http://<unraid>:8788/api/about                              # {"name":"Internet Health","version":"…"}
curl -s http://<unraid>:8788/api/health                             # {"status":"ok", …}
curl -s -o /dev/null -w '%{http_code}\n' http://<unraid>:8788/api/notifications/pending      # 403 without the key
ls -l $D/notify-token                                               # -rw------- (600)
cat $D/notify-bridge-state.json                                     # with the bridge: health "OK", last_poll updates every minute
docker logs InternetHealth 2>&1 | grep -i -E 'traceback|extension failed'   # nothing
docker ps --format '{{.Names}} {{.Status}}' | grep -i internethealth          # "(healthy)" after the first minutes
```

No notification is sent during installation; use *Send test* in the Telegram settings, or unplug the router's internet cable for at least
3 minutes to see the real thing: a change needs two bad probe cycles in a row (about 50 s each), so shorter interruptions are treated as blips —
see [NOTIFICATIONS.md](NOTIFICATIONS.md). *Turbo mode* (thresholds) catches outages from about 10 s.

## Updating

Releases are published on [GitHub](https://github.com/d1-m4ss/internet-health/releases) with the notes from the
[CHANGELOG](../CHANGELOG.md); the version is in the page footer and in `GET /api/about`.

- **In the GUI:** *Docker → Check for updates → Update* (the template uses `:latest`). Or pin a version: set the repository to
  `ghcr.io/d1-m4ss/internet-health:1.4.0` and move on when you decide to.
- **From the command line:** `docker pull ghcr.io/d1-m4ss/internet-health:latest`, then recreate the container with Unraid's own script
  `/usr/local/emhttp/plugins/dynamix.docker.manager/scripts/rebuild_container InternetHealth` (it rebuilds from the template; start the
  container afterwards if it was stopped).

**From 1.4.0 the app runs as `99:100` (nobody:users), not as root.** The first start after the update gives the files in the data folder to that user
(one `entrypoint: handing files …` line in the log); the host-side bridge and your backups keep working because root reads everything. To keep the old
behaviour set the variable `PUID` to `0`. Going back to an older version needs nothing: root can use files of any owner.

The data folder is untouched by an update: new database columns and files are created on first use, and a new feature stays **off** until you
switch it on in the page. Before a big step make a copy: *Settings → History → Back up now* (see [Restoring a backup](#restoring-a-backup)).
After the update reload the page with Ctrl+F5 and run the [checks above](#verify). To go back, set the repository to the previous version tag.

## Moving the data folder

Earlier versions suggested `/mnt/user/appdata/netprobe_database`; it keeps working and the bridge finds it. To rename it to the current default, stop
the container, move the folder, change the *Data* path in the container's settings and start it again:

```sh
docker stop InternetHealth
mv /mnt/user/appdata/netprobe_database /mnt/user/appdata/internet-health
# edit the Data path of the container (Docker → InternetHealth → Edit → Apply), which recreates it; then start it if it is stopped
```

If the bridge has its own paths in the cron line (`NETPROBE_NOTIFY_TOKEN_FILE`, `NETPROBE_SETTINGS_FILE`, `NETPROBE_STATE_FILE`, or `NETPROBE_DATA_DIR` for the
folder as a whole), change them too and run `update_cron`; without them it follows the folder by itself.

## Switching from a NetProbe + files install

Before 1.0, Internet Health was three files mounted over the original NetProbe image (`bmmbmm01/netprobe`). That way is no longer supported; switch to
the image and keep your data folder, so history and settings stay:

1. Back up: `mkdir -p $D/backups/pre-image && cd $D/backups/pre-image`, then
   `cp /boot/config/plugins/dockerMan/templates-user/my-NetProbe.xml .`, `docker inspect NetProbe > container-inspect.json`, and
   `docker stop NetProbe && cp -p $D/netprobe.sqlite $D/alert-settings.json $D/netprobe-prefs.json . ; docker start NetProbe`
   (skip the files that do not exist).
2. Edit `/boot/config/plugins/dockerMan/templates-user/my-NetProbe.xml`: set `<Repository>` to `ghcr.io/d1-m4ss/internet-health:latest` and delete the
   three `<Config … Target="/app/…">` lines (the file mounts). (Or *Docker → NetProbe → Edit*, change *Repository* and remove the three path entries,
   *Apply*.) A mount that stays would override the file inside the image with the old one.
3. Recreate the container: `/usr/local/emhttp/plugins/dynamix.docker.manager/scripts/rebuild_container NetProbe` (or *Apply* in the GUI), run the
   [checks](#verify). The container keeps its name, port, variables and `/data`; rename it in the GUI if you like.
4. When you are sure, the old image can go: `docker rmi bmmbmm01/netprobe@sha256:…`.

## Restoring a backup

Backups are made on the page (*Settings → History → Back up now*) into `$D/db-backups/` (the 5 newest are kept) and can be downloaded
there. A copy is a complete SQLite file. To go back to one:

```sh
docker stop InternetHealth
cp -p $D/netprobe.sqlite $D/netprobe.sqlite.before-restore          # keep what is there now
rm -f $D/netprobe.sqlite-wal $D/netprobe.sqlite-shm                 # leftovers of the old file must not be applied to the copy
cp -p $D/db-backups/netprobe-<date>-<time>.sqlite $D/netprobe.sqlite
docker start InternetHealth
```

A copy taken on another machine goes to the same place. The copy contains all history and the Speedtest data, so treat it like the database
itself (the web page has no login either).

## Rollback

- **An update went wrong:** set the repository to the previous version tag and recreate the container (see [Updating](#updating)); restore a database copy
  only if you need to (see above).
- **Stop the Unraid notifications only:** switch the *Unraid* integration off in the page, or remove the cron file:
  `rm /boot/config/plugins/dynamix/netprobe-notify.cron && update_cron`.

## Troubleshooting

| Symptom | Check |
|---|---|
| `404` on `/api/targets/*` or an unfamiliar page | the container is not the Internet Health image (or a leftover file mount overrides it): `docker inspect <name> --format '{{.Config.Image}} {{json .HostConfig.Binds}}'` |
| No messages arrive | *Settings → Integrations*: is the channel on? Telegram: *Send test* and the status line under it; Unraid: `grep netprobe-notify /var/log/syslog`, agents configured?, `cat $D/notify-bridge-state.json` |
| "monitoring is not working" from the bridge | the probe loop stalled or the API is unreachable from the host (`curl http://127.0.0.1:8788/api/health`) |
| Token errors from the bridge | `notify-token` must be mode `600`, a regular file, ≥ 32 hex characters |
| `unhealthy` | `docker logs InternetHealth \| tail`; `GET /api/health` is `503` with the last error when the probe loop has stored no cycle for the interval plus five minutes |
| The page does not change after an update | Ctrl+F5; check the footer version |
