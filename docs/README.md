<!-- Copyright 2026 BlueCat Networks (USA) Inc. and its affiliates. All Rights Reserved. -->

Workflow Version: **1.4** <br/>
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

`/current` also returns a `totals` object: DNS QPS / DHCP LPS summed across servers, and an
overall cache/query hit ratio computed from each server's *raw* hit/miss counts summed
first and divided second — not an average of each server's percentage, which would
misweight servers with very different traffic volumes. `/history` carries the same four
metrics as time series, one per server per metric.

`/current` also returns `api_calls`: every PromQL request this call made against BAM's
Prometheus, in order, each with the query string, the full request URL, and the raw
Prometheus JSON response (or `error` if the request failed). Purely informational, for the
UI's "API calls to BAM's Prometheus" panel — nothing else in this workflow reads it.

REST endpoints (mounted at `/bdds_qps/v1/stats`):
- `GET /bdds_qps/v1/stats/servers` — list BDDS servers currently reporting to Prometheus.
- `GET /bdds_qps/v1/stats/current` — current DNS QPS / DHCP LPS / cache hit ratio / query hit
  ratio for all servers (plus a `totals` rollup), or for one server via
  `?server=<exported_instance>`.
- `GET /bdds_qps/v1/stats/history` — the same four metrics as time series over a window.
- `GET /bdds_qps/v1/doc/` — Swagger UI for the above.

UI page: `/bdds_qps_ui/page` (nav entry "BDDS Performance Statistics"), polls `/current` every 60
seconds — matching Prometheus's `global.scrape_interval` on BAM, so faster polling would
just re-read the same sample. Shows four history charts side by side: DNS QPS, DHCP LPS,
Cache Hit %, and Query Hit % (the latter two fixed to a 0-100% Y axis). Below that, an
"API calls to BAM's Prometheus" panel lists every PromQL request the latest `/current` call
made, each collapsed to its query string by default — expand one to see the full request
URL and raw Prometheus response.

Known Errors and Bugs:
- If the network path from this Gateway to BAM's Prometheus port (9090) is blocked, the
  API degrades gracefully (HTTP 502 with an error message naming the unreachable host)
  instead of crashing.
- Server-side rate is only as fresh as BAM's Prometheus scrape interval (1 minute).

Change Log:
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
