#!/bin/bash
# Host-only cron bridge. No Docker access and no synthetic installation alert.
# A crash after notify succeeds but before its checkpoint can cause a duplicate;
# the queue and checkpoint provide retry durability, not exactly-once delivery.
set -euo pipefail
umask 077

API_BASE="${NETPROBE_API_BASE:-http://127.0.0.1:8788}"
NOTIFY="${UNRAID_NOTIFY:-/usr/local/emhttp/webGui/scripts/notify}"
TOKEN_FILE="${NETPROBE_NOTIFY_TOKEN_FILE:-/mnt/user/appdata/netprobe_database/notify-token}"
SETTINGS_FILE="${NETPROBE_SETTINGS_FILE:-/mnt/user/appdata/netprobe_database/alert-settings.json}"
STATE_FILE="${NETPROBE_STATE_FILE:-/mnt/user/appdata/netprobe_database/notify-bridge-state.json}"
WEB_URL="${NETPROBE_WEB_URL:-}"
# Lock lives in RAM (not on the /mnt/user FUSE share, where flock is not guaranteed).
LOCK_FILE="${NETPROBE_LOCK_FILE:-/var/run/netprobe-notify.lock}"

die() { printf 'NetProbe bridge: %s\n' "$1" >&2; exit 1; }
for cmd in curl jq timeout flock date stat mktemp mv wc; do
    command -v "$cmd" >/dev/null 2>&1 || die "missing dependency: $cmd"
done
[[ "$API_BASE" =~ ^http://(127\.0\.0\.1|localhost):[0-9]{1,5}$ ]] || die 'API must use a localhost HTTP port'
[[ -z "$WEB_URL" || "$WEB_URL" =~ ^https?://[^[:space:][:cntrl:]]+$ ]] || die 'invalid web URL'
[[ -x "$NOTIFY" ]] || die 'notify executable unavailable'
[[ -d "$(dirname "$STATE_FILE")" ]] || die 'persistent state directory missing'
exec 9>"$LOCK_FILE"
flock -n 9 || exit 0

# Fail closed if the settings file cannot establish the user's notification choice.
[[ -r "$SETTINGS_FILE" ]] || die 'settings file unavailable'
enabled=$(jq -esr 'select(length == 1) | .[0] | select(type == "object") | .enabled | select(type == "boolean") | tostring' "$SETTINGS_FILE") || die 'invalid enabled setting'
[[ "$enabled" == true ]] || exit 0
[[ -r "$TOKEN_FILE" && ! -L "$TOKEN_FILE" ]] || die 'token file unavailable'
mode=$(stat -c '%a' "$TOKEN_FILE" 2>/dev/null) || mode=$(stat -f '%Lp' "$TOKEN_FILE")
[[ "$mode" == 600 ]] || die 'token file must have mode 600'
token=$(cat "$TOKEN_FILE")
[[ "$token" =~ ^[a-fA-F0-9]+$ && ${#token} -ge 32 && ${#token} -le 256 ]] || die 'invalid token file'

work=$(mktemp -d "${STATE_FILE}.work.XXXXXX")
state_tmp="${STATE_FILE}.tmp.$$"
trap 'rm -rf -- "$work"; rm -f -- "$state_tmp"' EXIT
printf 'X-NetProbe-Notify-Key: %s\n' "$token" > "$work/header"
unset token
curl_args=(-q --silent --show-error --fail --noproxy '*' --connect-timeout 2 --max-time 5
           --max-filesize 524288 --header "@$work/header")

state='{"health":"OK","candidate":"OK","streak":0,"last_poll":0,"delivered":[]}'
if [[ -e "$STATE_FILE" ]]; then
    [[ $(wc -c < "$STATE_FILE") -le 131072 ]] || die 'state file too large'
    state=$(jq -cse '
      def integer: type == "number" and isfinite and . == floor and . >= 0 and . <= 9007199254740991;
      select(length == 1) | .[0] |
      select(type == "object" and (.health == "OK" or .health == "UNAVAILABLE")
        and (.candidate == "OK" or .candidate == "UNAVAILABLE")
        and (.streak | integer) and (.last_poll | integer)
        and (.delivered | type == "array" and length <= 1000 and all(.[]; integer and . > 0)))
    ' "$STATE_FILE") || die 'invalid persistent state; refusing duplicate delivery'
fi

save_state() {
    printf '%s\n' "$state" > "$state_tmp" && mv -f -- "$state_tmp" "$STATE_FILE"
}

# Unraid email/Telegram agents expect the long message (-m) to use the two characters "\n" as line
# breaks and a short single-line description (-d). The API supplies the description; without one,
# fall back to a flat excerpt of the message.
send_notification() {
    local level=$1 subject=$2 message=$3 description=${4:-}
    if [[ -z "$description" ]]; then
        description=${message//\\n/ }
        description=${description//$'\n'/ }
    fi
    local args=(-e NetProbe -s "$subject" -d "${description:0:240}" -i "$level" -m "$message")
    [[ -z "$WEB_URL" ]] || args+=(-l "$WEB_URL")
    timeout 15 "$NOTIFY" "${args[@]}" < /dev/null > /dev/null 2>&1
}

now=$(date +%s)
[[ "$now" =~ ^[0-9]{1,12}$ ]] || die 'invalid clock'
api_ok=false
http_code=''
if http_code=$(timeout 8 curl "${curl_args[@]}" --output "$work/pending" --write-out '%{http_code}' \
        "$API_BASE/api/notifications/pending" 2>/dev/null) && [[ "$http_code" == 200 ]] &&
   jq -se --argjson now "$now" '
      def integer: type == "number" and isfinite and . == floor and . > 0 and . <= 9007199254740991;
      def timestamp: type == "number" and isfinite and . == floor and . >= 0 and . <= 9007199254740991;
      length == 1 and (.[0] | type == "object"
        and has("latest_probe_ts") and (.latest_probe_ts == null or (.latest_probe_ts | timestamp))
        and (.process_started_at | timestamp) and .process_started_at <= $now
        and (.max_sample_age | type == "number" and . == 180)
        and (.notifications | type == "array" and length <= 100 and all(.[];
        type == "object" and (.id | integer)
        and (.ts | type == "number" and isfinite and . >= 0 and . <= 9007199254740991)
        and (.level == "normal" or .level == "warning" or .level == "alert")
        and (.subject | type == "string" and length > 0 and length <= 200 and (test("[\\x00-\\x1f\\x7f]") | not))
        and (.message | type == "string" and length > 0 and length <= 4096 and (test("[\\x00-\\x08\\x0b\\x0c\\x0e-\\x1f\\x7f]") | not))
        and (.description == null or (.description | type == "string" and length <= 240 and (test("[\\x00-\\x1f\\x7f]") | not)))
      )))
   ' "$work/pending" >/dev/null 2>&1; then
    api_ok=true
fi

health=UNAVAILABLE
if [[ "$api_ok" == true ]]; then
    health=$(jq -r --argjson now "$now" '
      if .latest_probe_ts == null then
        if $now - .process_started_at <= .max_sample_age then "GRACE" else "UNAVAILABLE" end
      elif $now - .latest_probe_ts >= 0 and $now - .latest_probe_ts <= .max_sample_age then "OK"
      else "UNAVAILABLE" end' "$work/pending")
fi
last_poll=$(jq -r '.last_poll' <<< "$state")
# Do not turn repeated/manual invocations of one cron interval into confirmations.
if (( last_poll == 0 || now - last_poll >= 50 )); then
    state=$(jq -c --arg health "$health" --argjson now "$now" '
      .streak = (if $health == "GRACE" then 0 elif .candidate == $health then ([.streak + 1, 2] | min) else 1 end)
      | .candidate = (if $health == "GRACE" then .health else $health end) | .last_poll = $now' <<< "$state")
    save_state
    active=$(jq -r '.health' <<< "$state")
    streak=$(jq -r '.streak' <<< "$state")
    if [[ "$health" != GRACE && "$health" != "$active" ]] && (( streak >= 2 )); then
        if [[ "$health" == UNAVAILABLE ]]; then
            level=warning
            subject='⚠️ NetProbe: monitoring is not working'
            message='⚠️ NetProbe monitoring is not responding or its data is stale (two checks in a row).\n\nNetProbe cannot report network problems right now. Check the NetProbe container in Unraid and its probe loop.'
        else
            level=normal
            subject='✅ NetProbe: monitoring is working again'
            message='✅ Fresh NetProbe measurements were confirmed by two checks in a row. Network monitoring is working.'
        fi
        send_notification "$level" "$subject" "$message" || die 'health notification failed; will retry'
        state=$(jq -c --arg health "$health" '.health = $health' <<< "$state")
        save_state
    fi
fi
[[ "$api_ok" == true ]] || die 'notification API unavailable or malformed'
# Startup grace never confirms recovery. Keep queued events intact until actual
# measurements are fresh, including after a reachable-but-stalled API response.
[[ "$health" != GRACE ]] || exit 0
[[ "$health" == OK ]] || die 'monitoring samples stale, missing, or in the future'

# A checkpointed ID that is no longer pending was acknowledged (or the queue was recreated):
# forget it so it can neither fill the checkpoint nor suppress a future notification with the
# same ID. Only prune when the response is not truncated by the server's page size.
if (( $(jq '.notifications | length' "$work/pending") < 20 )); then
    state=$(jq -c --slurpfile p "$work/pending" \
        '.delivered |= map(select(. as $d | ($p[0].notifications | map(.id) | index($d)) != null))' <<< "$state")
    save_state
fi

# Deduplicate repeated IDs in a response before taking any external action.
jq -c '.notifications | unique_by(.id) | .[]' "$work/pending" > "$work/records"
while IFS= read -r record; do
    id=$(jq -r '.id' <<< "$record")
    if ! jq -e --argjson id "$id" '.delivered | index($id) != null' <<< "$state" >/dev/null; then
        [[ $(jq '.delivered | length' <<< "$state") -lt 1000 ]] || die 'delivery checkpoint is full; resolve pending acknowledgements'
        level=$(jq -r '.level' <<< "$record")
        subject=$(jq -r '.subject' <<< "$record")
        message=$(jq -r '.message' <<< "$record")
        description=$(jq -r '.description // ""' <<< "$record")
        send_notification "$level" "$subject" "$message" "$description" || die 'notification failed; remains queued'
        state=$(jq -c --argjson id "$id" '.delivered += [$id]' <<< "$state")
        save_state
    fi
    http_code=$(timeout 8 curl "${curl_args[@]}" --request POST --data '' --output "$work/ack" \
        --write-out '%{http_code}' "$API_BASE/api/notifications/$id/ack" 2>/dev/null) || die 'acknowledgement failed; will retry without resending'
    [[ "$http_code" == 200 || "$http_code" == 204 ]] || die 'unexpected acknowledgement response; will retry without resending'
    state=$(jq -c --argjson id "$id" '.delivered -= [$id]' <<< "$state")
    save_state
done < "$work/records"
