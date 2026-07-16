<!-- Copyright 2026 BlueCat Networks (USA) Inc. and its affiliates. All Rights Reserved. -->

Workflow Version: **1.11** <br/>
Project Title: **BDDS Performance Statistics** <br/>
Author: **jli@bluecatnetworks.com** <br/>
Date: **15-07-2026** <br/>
BlueCat Gateway Version: **isp-workflows:25.3.3** <br/>
BAM / BDDS Version: **25.1.x (Debian 12 / bookworm)** <br/>
Dependencies: **flask_restx, requests (both already provided by the Gateway runtime)** <br/>

Description/Example Usage:

Displays current DNS queries-per-second (QPS) and DHCP leases-per-second (LPS), plus BIND's
cache hit ratio and query hit ratio, for every BDDS server managed by this BAM. Data comes
from BAM's built-in Prometheus, which scrapes each BDDS's `:10048` metrics exporter over
mutual TLS on our behalf — this workflow never talks to a BDDS's exporter directly. The
Prometheus host is not hardcoded: it's the same BAM host this Gateway is already configured
against (read from `g.user.get_api().get_url()`, see `bam_service.get_prometheus_base_url()`),
on the fixed Prometheus port `9090`.

Cache hit ratio (`CacheHits` / (`CacheHits` + `CacheMisses`)) and query hit ratio (`QueryHits`
/ (`QueryHits` + `QueryMisses`)) come from BIND's `bc_dns_cachestats` counters for the
`default` view. Unlike DNS QPS/DHCP LPS, these are lifetime counters since the BDDS's named
process last started (BAM's exporter doesn't expose a "since last poll" variant of this
metric), so they settle slowly and aren't a live per-minute rate. `null` when a server hasn't
served any cacheable/cached queries yet.

Also shows host-level metrics per BDDS, from BAM's Telegraf-style system exporter: CPU %
(`bc_system_cpu_usage`, a 0-1 fraction), Memory % (`used / (used + available)` — there's no
"total memory" metric to divide by directly), Disk Read/Write IOPS, and Network RX/TX
packets/sec (the latter two from `_since_poll` deltas, converted the same way DNS QPS is).

`/current` also returns a `totals` object, rolled up per metric's shape: DNS QPS / DHCP LPS
/ disk IOPS / network pps are summed (they're rates); cache/query hit ratio sums each
server's *raw* hit/miss counts first and divides second — not an average of each server's
percentage, which would misweight servers with very different traffic volumes; CPU %/Memory
% have no traffic-like weight to sum by, so those are a plain mean across servers. `/history`
now carries all ten metrics as time series, one per server per metric.

`/current` also returns `api_calls`: every PromQL request this call made against BAM's
Prometheus, in order, each with the query string, the full request URL, and the raw
Prometheus JSON response (or `error` if the request failed). Purely informational, for the
UI's "API calls to BAM's Prometheus" panel — nothing else in this workflow reads it.

Both `/current` and `/history` accept a repeated `?metric=<key>` filter (keys listed in
`ALL_METRIC_KEYS`) scoping which metrics are fetched *at all* — an unlisted metric isn't
queried against Prometheus, not just hidden from the response afterward. Omit `?metric=`
entirely to fetch every metric (the default, for callers that predate this filter, e.g.
Swagger). The UI always sends this param explicitly, using the reserved value
`?metric=none` for "every checkbox is off" (there's no other way to tell "sent as empty"
apart from "not sent at all" from a plain query string). An unrecognized metric key is a
`400`, not a silent ignore.

REST endpoints (mounted at `/bdds_qps/v1/stats`):
- `GET /bdds_qps/v1/stats/servers` — list BDDS servers currently reporting to Prometheus.
- `GET /bdds_qps/v1/stats/current` — current DNS QPS / DHCP LPS / cache hit ratio / query hit
  ratio / CPU % / memory % / disk read+write IOPS / network RX+TX pps for all servers (plus
  a `totals` rollup), or for one server via `?server=<exported_instance>`. Scope which
  metrics are fetched with `?metric=`.
- `GET /bdds_qps/v1/stats/history` — the same ten metrics as time series over a window.
  Same `?metric=` filter as `/current`.
- `GET /bdds_qps/v1/doc/` — Swagger UI for the above.

UI page: `/bdds_qps_ui/page` (nav entry "BDDS Performance Statistics"), polls `/current` every 60
seconds — matching Prometheus's `global.scrape_interval` on BAM, so faster polling would
just re-read the same sample. A "Select metrics" panel between the server picker and the
results table has 8 checkboxes, one per chart slot (DNS QPS, DHCP LPS, Cache Hit %, Query
Hit %, CPU %, Memory %, **Disk IOPS**, **Network pkt/s**) — the last two each control *two*
result-table columns at once (Disk Read + Disk Write IOPS, Net RX + Net TX pkt/s), even
though those stay four separate columns in the table itself. Toggling a checkbox re-fetches
`/current` and `/history` with `?metric=` scoped to exactly what's now checked — unchecked
metrics aren't queried against Prometheus at all while they're off, not just hidden in the
UI after being fetched anyway.

The history charts below mirror the same 8 checkboxes 1:1. Disk IOPS and Network pkt/s were
already single charts (one line per (server, metric), labeled e.g. "bdds251a Read" /
"bdds251a Write" to stay distinguishable) — now their checkbox is single too, so the chart
and its two constituent table columns always show/hide together instead of independently.
Charts lay out across up to two rows of up to four each (max 8 total, which is also the
total number of slots, so everything checked always fits). Each row's charts split its width
evenly, so 1 selected slot gets a full-width chart, 2 get half-width, etc. Percentage metrics
(Cache Hit %, Query Hit %, CPU %, Memory %) get a fixed 0-100% Y axis; the rest auto-scale.

The section's overall height is always reserved for two full rows, even when only one row
of charts is populated (an invisible placeholder panel fills the unused row) — so toggling
metrics down to a single row never shifts the "API calls" panel below; only each populated
row's chart widths flex to fit however many charts are actually in it.

Below that, an "API calls to BAM's Prometheus" panel lists every PromQL request the latest
`/current` call made, each collapsed to its query string by default — expand one to see the
full request URL and raw Prometheus response.

Known Errors and Bugs:
- If the network path from this Gateway to BAM's Prometheus port (9090) is blocked, the
  API degrades gracefully (HTTP 502 with an error message naming the unreachable host)
  instead of crashing.
- Server-side rate is only as fresh as BAM's Prometheus scrape interval (1 minute).

Change Log:
- 2026-07-16: `/current` and `/history` now accept a `?metric=` filter - unchecking a
  metric in the UI now skips its Prometheus queries entirely (a real refetch with a
  narrower `?metric=` list), instead of always fetching everything and hiding columns/
  charts client-side afterward.
- 2026-07-15: The "Select metrics" panel now has one checkbox per chart slot (8, not 10) -
  Disk Read/Write IOPS share one checkbox, as do Net RX/TX pkt/s, so a chart and its table
  columns always show/hide together instead of independently. Table columns are still four
  separate ones either way.
- 2026-07-15: The history-charts section now always reserves two rows' worth of height
  (fixed regardless of selection), with an invisible placeholder filling any unused second
  row - only chart widths within a row adjust dynamically now, not the section's height.
  Also fixed a stale CSS selector (`#historyChart`) that never matched any chart's real ID,
  left over from before charts became dynamic.
- 2026-07-15: Disk Read/Write and Net RX/TX each now share one history chart (two lines per
  server) instead of two separate charts, so all 8 chart slots fit at once with everything
  checked.
- 2026-07-15: History charts are now dynamic - one per selected metric (up to 8, 2 rows of
  up to 4), replacing the old fixed set of 4. Added `/history` series for CPU %, Memory %,
  Disk Read/Write IOPS, and Network RX/TX pkt/s to match.
- 2026-07-15: Added CPU %, Memory %, Disk Read/Write IOPS, and Network RX/TX pkt/s to
  `/current` and the metric-selection panel, from BAM's Telegraf-style system exporter.
  `/current`-only for now, no history series/charts yet.
- 2026-07-15: Added a "Select metrics" panel to the UI, letting the results table show only
  the chosen columns among DNS QPS / DHCP LPS / Cache Hit % / Query Hit % (client-side only,
  no API change).
- 2026-07-15: Added an `api_calls` field to `/current` (every PromQL request made, its URL,
  and raw response) and a matching collapsible panel on the UI page.
- 2026-07-15: Added a `totals` rollup (traffic-weighted, not averaged) to `/current`, cache/
  query hit-ratio time series to `/history`, and two matching history charts to the UI.
- 2026-07-15: Added cache hit ratio and query hit ratio columns to `/current` and the UI
  table, from BIND's `bc_dns_cachestats` counters.
- 2026-07-14: Bumped to 1.1 for the first public release; no functional change since 1.0's
  2026-07-10 fix.
- 2026-07-10: Prometheus host is now read from this Gateway's configured BAM connection
  (`bam_service.get_prometheus_base_url()`) instead of a hardcoded IP address.
- 2026-07-09: Initial version, replacing the earlier unfinished `bdds_qps` proof-of-concept
  workflow (generic form template with no real BDDS integration).

Screen Shot
<img width="3496" height="1140" alt="image" src="https://github.com/user-attachments/assets/93bab0a4-1b4e-435e-87ad-31576178eb94" />

Running against a live Gateway/BAM, showing real QPS/LPS data and history charts:
![BDDS Performance Statistics page with live data](images/bdds-performance-statistics.webp)
