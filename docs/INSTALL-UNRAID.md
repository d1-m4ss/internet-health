# Installing Internet Health on Unraid

Internet Health is published as a ready container image, `ghcr.io/d1-m4ss/internet-health` (tags `latest` and the version numbers such
as `1.1.0`). It is the NetProbe image, pinned by digest, with the Internet Health files already inside (see the
[Dockerfile](../Dockerfile)): **nothing to copy, nothing to mount** besides the data folder. Everything is configured in the web UI.

> **Before you start:** the web UI has no authentication. Run it on a LAN, not on an exposed port.

Variables used below (adjust to your setup):

```sh
D=/mnt/user/appdata/netprobe_database      # host folder mounted as /data in the container (database, settings, backups)
```

- [New install](#new-install) · [Notifications](#notifications) · [Verify](#verify) · [Updating](#updating) ·
  [Switching from a NetProbe + files install](#switching-from-a-netprobe--files-install) · [Restoring a backup](#restoring-a-backup) ·
  [Rollback](#rollback) · [Troubleshooting](#troubleshooting) · [Appendix: the file-mount way](#appendix-the-file-mount-way)

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
| Data | `/mnt/user/appdata/netprobe_database` — keep this default if you will use the Unraid notification bridge |
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

The data folder is untouched by an update: new database columns and files are created on first use, and a new feature stays **off** until you
switch it on in the UI. Before a big step make a copy: *Settings → History → Back up now* (see [Restoring a backup](#restoring-a-backup)).
After the update reload the page with Ctrl+F5 (the browser may keep the old one) and run the [checks above](#verify).

To go back, set the repository to the previous version tag (for example `:1.0.1`); the database stays valid for the older version.

## Switching from a NetProbe + files install

If you ran the earlier way (the `bmmbmm01/netprobe` image with three read-only bind-mounted files, [described below](#appendix-the-file-mount-way)),
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
| `unhealthy` | `docker logs <name> \| tail`; the health check asks `GET /api/prefs` inside the container |
| The page does not change after an update | Ctrl+F5; check the footer version |

## Appendix: the file-mount way

Before the image existed (and still, to run a version that is not released), Internet Health was installed as three files bind-mounted read-only
over the stock NetProbe image. It still works and is what developers use. Variables:

```sh
C=$D/custom                                # where the three files live
```

1. **Back up:** `mkdir -p $D/backups/pre-extension && cd $D/backups/pre-extension`; `docker inspect NetProbe > container-inspect.json && chmod 600 container-inspect.json`;
   `cp /boot/config/plugins/dockerMan/templates-user/my-NetProbe.xml .`; `docker stop NetProbe && cp -p $D/netprobe.sqlite . && docker start NetProbe`.
2. **Check the image and pin it by digest.** The files replace files of one specific image version:
   `docker image inspect bmmbmm01/netprobe:latest --format '{{index .RepoDigests 0}}'`, then `bash scripts/verify-image.sh bmmbmm01/netprobe@sha256:<digest>`
   (compares `app.py` and `templates/index.html` inside the image with the versions this repository was built against, see
   [upstream/UPSTREAM.md](../upstream/UPSTREAM.md); on a mismatch re-apply `src/app.py.patch` and run the tests).
3. **Copy the files:** `mkdir -p $C && scp src/app.py src/netprobe_extensions.py src/index.html root@<unraid>:$C/`.
4. **Mount them read-only** (*Docker → NetProbe → Edit*, or recreate the container): `$C/app.py → /app/app.py`,
   `$C/netprobe_extensions.py → /app/netprobe_extensions.py`, `$C/index.html → /app/templates/index.html`; repository
   `bmmbmm01/netprobe@sha256:<digest>`; variables `ROUTER_IP`, `ROUTER_NAME`, `SITES`, and `GUNICORN_CMD_ARGS=--threads 4` (the stock image runs one
   single-threaded worker; never raise `--workers`: every worker would start its own probe loop). With compose use
   [`examples/docker-compose.yml`](../examples/docker-compose.yml).
5. The Unraid bridge: as in [Notifications](#notifications), or copy `src/netprobe-unraid-notify.sh` and `scripts/netprobe-notify.cron` from your clone.
6. **Update the files later:** back up (`cp -p $C/*.{py,html} $D/alert-settings.json $D/netprobe-prefs.json` into a folder, and a database copy after `docker stop`),
   `scp` the three new files into `$C`, `docker restart NetProbe` (templates and modules are read once at start), reload the page.
   Rollback: copy the old files back and restart.
7. **Updating the NetProbe base image:** `docker pull bmmbmm01/netprobe:latest`, `bash scripts/verify-image.sh bmmbmm01/netprobe:latest` — if both files match,
   the files are compatible; otherwise port the hooks with `patch`, run the test suite, update the expected hashes and the digest in the template.
