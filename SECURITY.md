# Security

- The NetProbe web UI and its API have no authentication (inherited from the upstream app). Run it on a trusted LAN
  and do not publish the port.
- The notification queue endpoints require the secret in `/data/notify-token` and are meant to be called only by the
  host bridge. Keep that file out of version control and backups you share.
- The extension adds no privileges to the container: no Docker socket, no host root, no extra capabilities.

To report a vulnerability, please use GitHub's private "Report a vulnerability" feature on this repository
(Security → Advisories) instead of opening a public issue.
