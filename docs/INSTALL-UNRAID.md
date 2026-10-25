# Installing on Unraid

This guide follows the exact procedure used on the author's server. Nothing here modifies the NetProbe image,
and every step has an undo (see [Rollback](#rollback)).

> Installing from scratch? Start with [INSTALL-COMPOSE.md](INSTALL-COMPOSE.md) (example compose file).

> **Before you start:** the NetProbe web UI has no authentication. Run this on a LAN, not on an exposed port.

Variables used below (adjust to your setup):

```sh
D=/mnt/user/appdata/netprobe_database      # host folder mounted as /data in the container
C=$D/custom                                # where the three override files will live
```

## 1. Back up

```sh
mkdir -p $D/backups/pre-extension && cd $D/backups/pre-extension
docker inspect NetProbe > container-inspect.json && chmod 600 container-inspect.json
cp /boot/config/plugins/dockerMan/templates-user/my-NetProbe.xml .
docker stop NetProbe && cp -p $D/netprobe.sqlite . && docker start NetProbe   # consistent copy of the database
```

## 2. Check the image and pin it by digest

The overrides replace files of one specific image version. Pinning by digest keeps an automatic update from
swapping the base under them.

```sh
docker image inspect bmmbmm01/netprobe:latest --format '{{index .RepoDigests 0}}'
# from your computer: scp scripts/verify-image.sh root@<unraid>:/root/   then, on the server:
bash /root/verify-image.sh bmmbmm01/netprobe@sha256:<your digest>
```

`verify-image.sh` compares `app.py` and `templates/index.html` **inside the image** with the versions this
repository was built against (see [upstream/UPSTREAM.md](../upstream/UPSTREAM.md)). On a mismatch, re-apply the
patch to your image's `app.py` (`patch app.py < src/app.py.patch`), run the tests, and only then continue.

## 3. Copy the files

```sh
mkdir -p $C
scp src/app.py src/netprobe_extensions.py src/index.html root@<unraid>:/mnt/user/appdata/netprobe_database/custom/
scp src/netprobe-unraid-notify.sh scripts/netprobe-notify.cron root@<unraid>:$C/   # used in step 5; run from the repository root, with C set on your computer too
```

## 4. Mount them into the container

The container needs three **read-only** bind mounts and a few variables.

| Host path | Container path | Mode |
|---|---|---|
| `$C/app.py` | `/app/app.py` | read-only |
| `$C/netprobe_extensions.py` | `/app/netprobe_extensions.py` | read-only |
| `$C/index.html` | `/app/templates/index.html` | read-only |

| Variable | Suggested value |
|---|---|
| `ROUTER_IP` | your router, e.g. `192.168.1.1` |
| `ROUTER_NAME` | label for the UI and notifications, e.g. `Router` |
| `SITES` | `fast.com,google.com,youtube.com,1.1.1.1,8.8.8.8` — two IP targets next to the names |
| `GUNICORN_CMD_ARGS` | `--threads 4` (optional, recommended) — see [Web server threads](#web-server-threads-optional-recommended) |

**Option A — Unraid GUI.** *Docker → NetProbe → Edit*: set *Repository* to
`bmmbmm01/netprobe@sha256:<digest>`, add the three *Path* entries (access mode *Read Only*), set the variables,
*Apply*. With a digest reference the image pull is a no-op. *(The author used option B; option A is the standard
Unraid flow but was not exercised end to end.)*

**Option B — command line, no pull.** Recreate the container from the local image with the same settings plus the
overrides. The old container is kept (renamed, stopped) for rollback:

```sh
IMG=bmmbmm01/netprobe@sha256:<your digest>
# environment = what the container has now, minus variables baked into the image, plus your changes
docker image inspect $IMG --format '{{range .Config.Env}}{{println .}}{{end}}' | sort > /tmp/img-env
docker inspect NetProbe --format '{{range .Config.Env}}{{println .}}{{end}}' | sort | comm -23 - /tmp/img-env \
  | grep -v -e '^$' -e '^SITES=' -e '^ROUTER_NAME=' > /tmp/np-env
echo 'SITES=fast.com,google.com,youtube.com,1.1.1.1,8.8.8.8' >> /tmp/np-env
echo 'ROUTER_NAME=Router'                                    >> /tmp/np-env

docker stop NetProbe && docker rename NetProbe NetProbe-pre-ext && docker update --restart=no NetProbe-pre-ext
docker run -d --name NetProbe --pull=never --restart unless-stopped --env-file /tmp/np-env \
  --label net.unraid.docker.managed=dockerman \
  --label 'net.unraid.docker.webui=http://[IP]:[PORT:8080]' \
  --log-driver json-file --log-opt max-size=50m --log-opt max-file=1 \
  -p 8788:8080 -v $D:/data/:rw \
  -v $C/app.py:/app/app.py:ro -v $C/netprobe_extensions.py:/app/netprobe_extensions.py:ro \
  -v $C/index.html:/app/templates/index.html:ro  $IMG
rm -f /tmp/img-env /tmp/np-env
```

Adjust the published port, labels and options to match your own container (`docker inspect NetProbe-pre-ext`).
Then update the Unraid template (`my-NetProbe.xml`: repository digest, the three paths, the variables) so a later
*Apply* in the GUI keeps them.

## 5. Install the notification bridge

```sh
install -d /boot/config/custom
cp $C/netprobe-unraid-notify.sh /boot/config/custom/
cp $C/netprobe-notify.cron /boot/config/plugins/dynamix/ && update_cron
crontab -c /etc/cron.d -l | grep netprobe          # the entry must be listed
```

The bridge needs `curl`, `jq` and `flock` (present on Unraid). It reads `/data/notify-token` and
`/data/alert-settings.json` from the host path `$D` (override with `NETPROBE_NOTIFY_TOKEN_FILE`,
`NETPROBE_SETTINGS_FILE`, `NETPROBE_STATE_FILE`, `NETPROBE_API_BASE` if yours differs). Configure at least one
notification agent in *Settings → Notification Settings*.

## 6. Verify

```sh
curl -s http://<unraid>:8788/api/settings | jq .settings            # 200 with the thresholds
curl -s http://<unraid>:8788/api/prefs | jq .                       # 200: speedtest service/schedule, show_public_ip false
curl -s http://<unraid>:8788/api/targets/recent | jq '.data|length' # one entry per target
curl -s -o /dev/null -w '%{http_code}\n' http://<unraid>:8788/api/notifications/pending      # 403 without the key
ls -l $D/notify-token                                               # -rw------- (600)
cat $D/notify-bridge-state.json                                     # health "OK", last_poll updates every minute
docker logs NetProbe 2>&1 | grep -i -E 'traceback|extension failed'  # nothing
```

Open `http://<unraid>:8788`, pick a style and language in the header. No notification is sent during
installation; the first real event goes through the same path the simulator exercises
(`python tests/simulate_network.py -v`).

## Rollback

```sh
docker stop NetProbe && docker rename NetProbe NetProbe-ext-failed
docker rename NetProbe-pre-ext NetProbe && docker update --restart=unless-stopped NetProbe && docker start NetProbe
cp $D/backups/pre-extension/my-NetProbe.xml /boot/config/plugins/dockerMan/templates-user/
rm /boot/config/plugins/dynamix/netprobe-notify.cron && update_cron
```

The extension keeps its data in separate `np_*` tables; the upstream tables are untouched, so nothing needs to be
cleaned up for the stock app to work.

## Updating NetProbe Enhanced

For an installation made with an earlier version of this repository (with or without Turbo mode). A release changes
only the **three container files**; the notification bridge (`netprobe-unraid-notify.sh`), the cron line, the
container settings, the image digest, the database, `alert-settings.json` and `notify-token` stay as they are. New
tables or files are created on first use, so there is no migration. After the update every new feature is **off or
unchanged** until you switch it on: the Speedtest still follows `SPEEDTEST_ENABLED` / `SPEEDTEST_INTERVAL`, the
service follows `SPEEDTEST_BACKEND`, no Speedtest message is sent and no external IP is looked up.

```sh
D=/mnt/user/appdata/netprobe_database ; C=$D/custom
# 1. back up what is installed now (files + settings + a consistent copy of the database)
B=$D/backups/ext-$(date +%Y%m%d-%H%M) && mkdir -p $B
cp -p $C/app.py $C/netprobe_extensions.py $C/index.html $D/alert-settings.json $B/
[ -f $D/netprobe-prefs.json ] && cp -p $D/netprobe-prefs.json $B/
docker stop NetProbe && cp -p $D/netprobe.sqlite $B/ && docker start NetProbe
# 2. from your computer, in the updated repository (git pull): replace the three files
scp src/app.py src/netprobe_extensions.py src/index.html root@<unraid>:$C/
# 3. restart: templates and Python modules are read once at start
docker restart NetProbe
```

If your container was created with the GUI or option B above, nothing else is needed: the three bind mounts
already point at `$C`. (If the bridge script or cron line changed in a release, the CHANGELOG says so; none of the
releases so far changed them.)

Check:

```sh
docker logs NetProbe 2>&1 | grep -i -E 'traceback|extension failed'     # nothing
curl -s http://<unraid>:8788/api/prefs | jq .prefs.speedtest_schedule   # "container"  (new version is running)
curl -s http://<unraid>:8788/api/settings | jq .settings                # your thresholds, unchanged
```

Then reload the page (Ctrl+F5 — the browser may keep the old page). A browser-side check: the Speedtest tab now has a
*Measurement service* selector and *Schedule and notifications*.

Rollback: copy the three files back from `$B` and `docker restart NetProbe`. The extra file `netprobe-prefs.json`
is simply ignored by the older version; database rows written meanwhile stay valid.

## Web server threads (optional, recommended)

The NetProbe image starts gunicorn with a single synchronous worker, so one slow request makes every other request
(including the page itself) wait. This version moves the known slow things out of the request (a manual Speedtest
and the external-IP lookup run in the background), and one more container variable lets the server answer several
requests at once and keep connections open:

| Variable | Value |
|---|---|
| `GUNICORN_CMD_ARGS` | `--threads 4` |

*Docker → NetProbe → Edit → Add another Path, Port, Variable → Variable* (name and key `GUNICORN_CMD_ARGS`,
value `--threads 4`) → *Apply*; with `docker run` add `-e GUNICORN_CMD_ARGS='--threads 4'`; in compose it is already in
[`examples/docker-compose.yml`](../examples/docker-compose.yml). Check: `docker logs NetProbe | grep -i 'Using worker'`
prints `gthread`. Do **not** raise `--workers`: every worker would start its own probe loop. To undo, remove the variable.

## Updating the NetProbe image later

1. `docker pull bmmbmm01/netprobe:latest` (does not touch the running container).
2. `bash verify-image.sh bmmbmm01/netprobe:latest` — if both files match, the overrides are compatible.
3. Otherwise port the three hooks with `patch`, run the test-suite, and update the expected hashes.
4. Change the digest in the template and recreate the container.

## Troubleshooting

| Symptom | Check |
|---|---|
| `404` on `/api/targets/*` | `netprobe_extensions.py` is not mounted or failed to start: `docker logs NetProbe \| grep -i extension` |
| UI looks like the stock one | the `index.html` mount is missing, or the container was not restarted (templates are cached) |
| No messages arrive | `grep netprobe-notify /var/log/syslog`; Unraid agents configured? alerts enabled in the UI? `cat $D/notify-bridge-state.json` |
| "monitoring is not working" | the probe loop stalled or the API is unreachable from the host (`curl http://127.0.0.1:8788/api/settings`) |
| Token errors | `notify-token` must be mode `600`, a regular file, ≥ 32 hex characters |
