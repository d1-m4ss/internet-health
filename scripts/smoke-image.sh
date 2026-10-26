#!/bin/bash
# Starts an Internet Health image next to nothing else (no host mounts but a throw-away data folder) and checks that it serves.
# Usage: smoke-image.sh <image> [platform]      e.g. smoke-image.sh internet-health:test linux/amd64
# Exit code 0: the page and the API answer; 1 otherwise. Cleans up after itself.
set -euo pipefail
IMAGE="${1:?usage: smoke-image.sh <image> [platform]}"
PLATFORM="${2:-}"
NAME="internet-health-smoke-$$"
DATA=$(mktemp -d)
cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; rm -rf "$DATA"; }
trap cleanup EXIT
chmod 777 "$DATA"

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
[[ -f "$DATA/netprobe-prefs.json" || -f "$DATA/alert-settings.json" ]] || fail "no settings were written to /data"
curl -fsS -m 5 "http://127.0.0.1:$port/api/health" | grep -q '"status":"ok"' || fail "/api/health does not report ok"
[[ "$(docker inspect --format '{{.Config.Healthcheck.Test}}' "$NAME")" == *"/api/health"* ]] || fail "the health check does not use /api/health"
ookla=$(docker exec "$NAME" internet-health-ookla status 2>&1 || true)   # exit code 1 = not installed
grep -q "not installed" <<< "$ookla" || fail "the Ookla installer helper is missing"
docker exec "$NAME" python -c "import psycopg2" 2>/dev/null && fail "psycopg2 is still in the image"
docker exec "$NAME" python -c "import speedtest" 2>/dev/null && fail "speedtest-cli is still in the image"
curl -fsS -m 5 -H 'Accept-Encoding: gzip' -o /dev/null -w '%{http_code}\n' "http://127.0.0.1:$port/" | grep -q 200 || fail "page request failed"
docker logs "$NAME" 2>&1 | grep -qi "extension failed to start" && fail "the extension failed to start"
echo "OK: $IMAGE serves Internet Health on a fresh data folder"
