# Security

- The NetProbe web UI and its API have no authentication (inherited from the upstream app). Run it on a trusted LAN
  and do not publish the port.
- The notification queue endpoints require the secret in `/data/notify-token` and are meant to be called only by the
  host bridge. Keep that file out of version control and backups you share.
- The extension adds no privileges to the container: no Docker socket, no host root, no extra capabilities.
- Outbound requests happen only on request: Speedtest runs (manual or scheduled) and, when *Show my external IP and
  provider* is ticked, a public-IP check (`api.ipify.org` once a minute, `ipwho.is` for the provider name only when the address changes and once a day). With it on, the address and provider are stored with every measurement; *Forget WAN addresses…* erases them. Preference changes (`POST /api/prefs`) and history
  cleanup (`POST /api/history/cleanup`) are accepted only from same-origin browser requests, like `/api/settings`; a real
  deletion additionally needs an explicit `confirm`, and the nightly cleanup runs only after a retention period was chosen.

To report a vulnerability, please use GitHub's private "Report a vulnerability" feature on this repository
(Security → Advisories) instead of opening a public issue.
