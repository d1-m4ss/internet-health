# Roadmap

Order is not a promise.

## Interface redesign: three layouts — done

Implemented as the UniFi (1), Classic (2) and Control (3) styles, see the CHANGELOG. The original brief is kept below. Not done: a separate
"Provider" node in the Control route chain (the provider is shown in the WAN / ISP block next to it). A restyling of the page, one layout per theme. Each keeps the data and the light/dark/auto theme buttons.

1. **Compact / high-density grid.** Neutral charcoal greys (`#121417`, `#1E2228`, `#8C96A6`); green only for an active OK status. The left panel becomes
   compact rows with 6 px status dots instead of large green blocks. Larger key numbers, a fixed 12 px gap between widgets.
2. **Modern card-based.** Card padding 12–16 px to shorten the page. Compact tables (targets, DNS, loss) with monospace IP addresses, ping and percentages,
   a clear border under every row and brighter column headers (`#A0AEC0`).
3. **Executive control / master-detail, no vertical scroll (100vh).**
   - Top bar: live route chain (server → router → ISP → Internet) and the main numbers (speed, latency, loss) in one row.
   - Left master panel: tree of hosts and services with a status colour, quick filter, and a small latency histogram for the last hour per host.
   - Centre detail pane with tabs (overview and tables, charts, DNS and diagnostics); a click on a host focuses the pane on that host only.
   - "Tactical dark" look: background `#08090C`–`#090A0F`, 1 px borders (`#1B2030`–`#1E2638`), monospace for all technical values, accent `#00E5FF`,
     OK `#10B981`/`#00E676`, warning `#F59E0B`, failure `#EF4444`/`#FF5252`.

## Unraid plugin

Prepared, not published: see [UNRAID-PLUGIN.md](UNRAID-PLUGIN.md).
