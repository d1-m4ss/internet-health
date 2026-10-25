# Security

- The NetProbe web UI and its API have no authentication (inherited from the upstream app). Run it on a trusted LAN
  and do not publish the port.
- The notification queue endpoints require the secret in `/data/notify-token` and are meant to be called only by the
  host bridge. Keep that file out of version control and backups you share.
- **Telegram:** the bot token is stored in `/data/telegram.json` (mode 600) or comes from `TELEGRAM_BOT_TOKEN`; it is write-only:
  `GET /api/telegram` and every other endpoint report only whether it is set, and error texts have it removed. Because the web UI has no
  authentication (see above), anyone who can open the page on your LAN can switch Telegram on or off, replace the token and the chat
  or press *Send test* — keep the port private. Only `api.telegram.org` is contacted, with a fixed URL built from the token you gave.
- The extension adds no privileges to the container: no Docker socket, no host root, no extra capabilities.
- Outbound requests happen only on request: Speedtest runs (manual or scheduled) and, when *Show my external IP and
  provider* is ticked, a public-IP check (`api.ipify.org` every 15 minutes by default, every minute or only when you press *Refresh* in the card; `ipwho.is` for the provider name only when the address changes and once a day). With it on, the address and provider are stored with every measurement; *Forget WAN addresses…* erases them. Preference changes (`POST /api/prefs`) and history
  cleanup (`POST /api/history/cleanup`) are accepted only from same-origin browser requests, like `/api/settings`; a real
  deletion additionally needs an explicit `confirm`, and the nightly cleanup runs only after a retention period was chosen.

To report a vulnerability, please use GitHub's private "Report a vulnerability" feature on this repository
(Security → Advisories) instead of opening a public issue.
