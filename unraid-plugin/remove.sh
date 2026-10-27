#!/bin/bash
# Runs on the Unraid host when the plugin is removed. The container, its data folder and the Docker template stay.
set -u
BOOT=${INTERNET_HEALTH_BOOT:-/boot}
UPDATE_CRON=${INTERNET_HEALTH_UPDATE_CRON:-update_cron}

rm -f "$BOOT/config/plugins/dynamix/netprobe-notify.cron" "$BOOT/config/custom/netprobe-unraid-notify.sh"
rm -rf "$BOOT/config/plugins/internet-health"
"$UPDATE_CRON"
echo "Internet Health plugin removed. The container, /mnt/user/appdata/internet-health and the Docker template were not touched."
