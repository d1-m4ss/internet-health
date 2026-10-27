#!/bin/bash
# Runs on the Unraid host after the plugin manager has written the plugin's files.
# INTERNET_HEALTH_BOOT / INTERNET_HEALTH_UPDATE_CRON exist only so the tests can run this against a scratch folder.
set -u
BOOT=${INTERNET_HEALTH_BOOT:-/boot}
UPDATE_CRON=${INTERNET_HEALTH_UPDATE_CRON:-update_cron}
PLUGDIR="$BOOT/config/plugins/internet-health"
TEMPLATES="$BOOT/config/plugins/dockerMan/templates-user"

chmod 0755 "$BOOT/config/custom/netprobe-unraid-notify.sh" 2>/dev/null || true
"$UPDATE_CRON"

# The user's copy of the template holds their settings: never overwrite it.
mkdir -p "$TEMPLATES"
if [ -e "$TEMPLATES/my-InternetHealth.xml" ]; then
    echo "Docker template my-InternetHealth.xml already exists: left as it is."
else
    cp "$PLUGDIR/internet-health.xml" "$TEMPLATES/my-InternetHealth.xml"
    echo "Docker template installed: Docker > Add Container > InternetHealth."
fi
echo "Notification bridge installed (runs every minute; it reads /mnt/user/appdata/internet-health)."
echo "Until the container is running the bridge logs 'settings file unavailable' once a minute."
