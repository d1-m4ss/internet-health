#!/bin/bash
# Starts an Internet Health image next to nothing else (no host mounts but a throw-away data volume) and checks that it serves.
# Usage: smoke-image.sh <image> [platform]      e.g. smoke-image.sh internet-health:test linux/amd64
# Exit code 0: the page and the API answer; 1 otherwise. Cleans up after itself.
set -euo pipefail
IMAGE="${1:?usage: smoke-image.sh <image> [platform]}"
PLATFORM="${2:-}"
NAME="internet-health-smoke-$$"
DATA="$NAME-data"      # a named volume: the owner of its files can really be changed (a macOS bind mount cannot)
cleanup() { docker rm -f "$NAME" "$NAME-root" >/dev/null 2>&1 || true; docker volume rm -f "$DATA" "$NAME-rootdata" >/dev/null 2>&1 || true; }
trap cleanup EXIT
docker volume create "$DATA" >/dev/null
# an installation older than 1.4.0 has files that belong to root: the entrypoint must hand them over
docker run --rm --entrypoint sh ${PLATFORM:+--platform $PLATFORM} -v "$DATA:/data" "$IMAGE" -c "echo '{\"kept\": true}' > /data/old-file.json; chmod 600 /data/old-file.json; chown 0:0 /data /data/old-file.json"

args=(-d --name "$NAME" -p 127.0.0.1:0:8080 -v "$DATA:/data" -e SPEEDTEST_ENABLED=false
      -e ROUTER_IP=192.0.2.1 -e SITES=example.com -e PROBE_INTERVAL=30)
[[ -n "$PLATFORM" ]] && args+=(--platform "$PLATFORM")
docker run "${args[@]}" "$IMAGE" >/dev/null
port=$(docker port "$NAME" 8080/tcp | head -1 | sed 's/.*://')

for i in $(seq 1 60); do
    if curl -fsS -m 3 "http://127.0.0.1:$port/api/prefs" >/dev/null 2>&1; then break; fi
    [[ $i -eq 60 ]] && { echo "the container did not start serving in time" >&2; docker logs --tail 40 "$NAME" >&2; exit 1; }
    sleep 2
done
fail() { echo "FAIL: $1" >&2; docker logs --tail 40 "$NAME" >&2; exit 1; }
page=$(curl -fsS -m 10 "http://127.0.0.1:$port/")
grep -q "<title>Internet Health" <<< "$page" || fail "the page is not Internet Health"
grep -q 'rel="icon"' <<< "$page" || fail "no icon in the page"
curl -fsS -m 5 "http://127.0.0.1:$port/api/prefs" | grep -q '"wan_check_min"' || fail "/api/prefs does not know the new preferences"
curl -fsS -m 5 "http://127.0.0.1:$port/api/telegram" | grep -q '"token_set":false' || fail "/api/telegram is missing"
docker exec "$NAME" sh -c "[ -f /data/netprobe-prefs.json ] || [ -f /data/alert-settings.json ]" || fail "no settings were written to /data"
curl -fsS -m 5 "http://127.0.0.1:$port/api/health" | grep -q '"status":"ok"' || fail "/api/health does not report ok"
[[ "$(docker inspect --format '{{.Config.Healthcheck.Test}}' "$NAME")" == *"/api/health"* ]] || fail "the health check does not use /api/health"
docker exec "$NAME" python -c "import psycopg2" 2>/dev/null && fail "psycopg2 is still in the image"
docker exec "$NAME" python -c "import speedtest" 2>/dev/null && fail "speedtest-cli is still in the image"
curl -fsS -m 5 -H 'Accept-Encoding: gzip' -o /dev/null -w '%{http_code}\n' "http://127.0.0.1:$port/" | grep -q 200 || fail "page request failed"
docker logs "$NAME" 2>&1 | grep -qi "extension failed to start" && fail "the extension failed to start"

# the app runs as 99:100, not as root, and owns the data folder (also the files that were there before)
uid=$(docker exec "$NAME" sh -c "awk '/^Uid:/{print \$2}' /proc/1/status")
[[ "$uid" == "99" ]] || fail "the app runs as uid $uid, not 99"
[[ "$(docker exec "$NAME" stat -c %u:%g /data/old-file.json)" == "99:100" ]] || fail "the old file in /data was not handed to 99:100"
[[ "$(docker exec "$NAME" stat -c %u /data/netprobe.sqlite)" == "99" ]] || fail "the database is not owned by 99"
docker exec --user 99:100 "$NAME" ping -q -c 1 -W 2 127.0.0.1 >/dev/null || fail "ping does not work for an unprivileged user"
docker logs "$NAME" 2>&1 | grep -q "Permission denied\|PermissionError\|readonly database" && fail "a permission error in the log"

# PUID=0 keeps the old behaviour (root)
docker run -d --name "$NAME-root" -e PUID=0 -v "$NAME-rootdata:/data" -e SPEEDTEST_ENABLED=false ${PLATFORM:+--platform $PLATFORM} "$IMAGE" >/dev/null
for i in $(seq 1 30); do [[ -n "$(docker exec "$NAME-root" sh -c "awk '/^Uid:/{print \$2}' /proc/1/status" 2>/dev/null)" ]] && break; sleep 1; done
[[ "$(docker exec "$NAME-root" sh -c "awk '/^Uid:/{print \$2}' /proc/1/status")" == "0" ]] || fail "PUID=0 does not keep root"
docker rm -f "$NAME-root" >/dev/null 2>&1

echo "OK: $IMAGE serves Internet Health on a fresh data folder"
