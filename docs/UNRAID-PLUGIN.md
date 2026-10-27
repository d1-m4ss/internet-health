# Unraid plugin (prepared, not published)

Internet Health is a container, so most of an Unraid install is the Docker template ([INSTALL-UNRAID.md](INSTALL-UNRAID.md)). A plugin
adds what a container cannot do: it lives on the host. The plugin in `unraid-plugin/` does two things and nothing else:

1. installs the **notification bridge** (`/boot/config/custom/netprobe-unraid-notify.sh` and its cron file), so the manual `wget` + `update_cron`
   steps of the guide disappear;
2. puts the **Docker template** into *Add Container* (`my-InternetHealth.xml`), but never overwrites an existing one: that file holds the
   user's settings.

It does not install, start or update the container, and removing it leaves the container, the data folder and the template alone.

## Build

```sh
python scripts/build-plugin.py          # writes dist/internet-health.plg (not committed)
```

The bridge, cron file, template and install / remove scripts are embedded with their SHA-256, so the plugin needs no other download. The
plugin version is the first `###YYYY.MM.DD` heading of `unraid-plugin/CHANGES.txt` (Unraid compares versions as text). **Bump it, with a note,
whenever a file inside the plugin changes** (`src/netprobe-unraid-notify.sh`, `scripts/netprobe-notify.cron`, `docs/unraid/internet-health.xml`,
`unraid-plugin/*.sh`); otherwise users never see the update. `tests/test_unraid_plugin.py` checks the embedded text, the hashes and what the
install / remove scripts do to a scratch `/boot`.

## What is not done yet (decisions for the owner)

- **Not tested on a real Unraid.** The `.plg` follows the format of existing plugins and is checked as XML, but nobody has installed it. Try it on
  the server first: *Plugins > Install Plugin* with the local file, then the install output, `crontab -c /etc/cron.d -l | grep netprobe`,
  and *Remove*. Unconfirmed: that Unraid's `<SHA256>` check accepts the embedded text byte for byte (a mismatch fails the install visibly),
  and that `min="6.9.0"` is right.
- **Where the `.plg` is served from.** `pluginURL` points at `unraid-plugin/internet-health.plg` on `main`, so publishing means committing the
  built file there (`python scripts/build-plugin.py --out unraid-plugin/internet-health.plg`) in the same commit as the change. The other
  option is a release asset: attach `dist/internet-health.plg` in `release.yml` and set `--plugin-url` to
  `https://github.com/d1-m4ss/internet-health/releases/latest/download/internet-health.plg`. Nothing in `release.yml` builds or publishes it now.
- **Community Apps.** To be listed, CA needs the plugin's XML entry in a template repository of its own and a support thread on the Unraid
  forum; the container template is a separate entry. See Unraid's *Community Applications* submission notes.
- **Bridge before container.** With the plugin installed and no container, the bridge logs `settings file unavailable` to the syslog once a
  minute (the install output says so). Making it silent while `/mnt/user/appdata/internet-health` does not exist is a small change to the
  bridge, left out because the bridge is covered by its own tests and works for current users.
- **A Settings page** (status of the bridge, link to the web UI, a button for the template) is possible with a `.page` file in
  `/usr/local/emhttp/plugins/internet-health/`; it needs PHP written against a live Unraid and was left out.
