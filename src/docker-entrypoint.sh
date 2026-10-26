#!/bin/sh
# Starts the app as an unprivileged user (PUID:PGID, default 99:100 = Unraid's nobody:users) instead of root.
#
# The container starts as root only to hand the data folder to that user (the files of installations older than 1.4.0 belong to
# root), then drops the privileges for good. PUID=0 keeps running as root (the behaviour before 1.4.0). When the container
# is started with `--user`, nothing is changed: the folder must already be writable for that user.
set -e

PUID="${PUID:-99}"
PGID="${PGID:-100}"
DATA_DIR="$(dirname "${DB_PATH:-/data/netprobe.sqlite}")"

case "$PUID$PGID" in
    *[!0-9]*|"") echo "entrypoint: PUID and PGID must be numbers (got '$PUID' and '$PGID')" >&2; exit 64 ;;
esac

if [ "$(id -u)" = "0" ] && [ "$PUID" != "0" ]; then
    mkdir -p "$DATA_DIR"
    if [ -n "$(find "$DATA_DIR" \( ! -user "$PUID" -o ! -group "$PGID" \) -print -quit 2>/dev/null)" ]; then
        echo "entrypoint: handing files in $DATA_DIR that do not belong to $PUID:$PGID over to them (the first start after an older version, or files made by root)"
        chown -R "$PUID:$PGID" "$DATA_DIR" || echo "entrypoint: WARNING: could not change the owner of everything in $DATA_DIR; set PUID=0 to run as root" >&2
    fi
    exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups "$@"
fi

exec "$@"
