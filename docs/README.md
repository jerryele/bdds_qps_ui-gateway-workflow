<!-- Copyright 2026 BlueCat Networks (USA) Inc. and its affiliates. All Rights Reserved. -->

Workflow Version: **1.0** <br/>
Project Title: **BDDS Performance Statistics** <br/>
Author: **jli@bluecatnetworks.com** <br/>
Date: **09-07-2026** <br/>
BlueCat Gateway Version: **isp-workflows:25.3.3** <br/>
BAM / BDDS Version: **25.1.x (Debian 12 / bookworm)** <br/>
Dependencies: **flask_restx, requests (both already provided by the Gateway runtime)** <br/>

Description/Example Usage:

Displays current DNS queries-per-second (QPS) and DHCP leases-per-second (LPS) for every
BDDS server managed by this BAM. Data comes from BAM's built-in Prometheus, which scrapes
each BDDS's `:10048` metrics exporter over mutual TLS on our behalf — this workflow never
talks to a BDDS's exporter directly. The Prometheus host is not hardcoded: it's the same
BAM host this Gateway is already configured against (read from `g.user.get_api().get_url()`,
see `bam_service.get_prometheus_base_url()`), on the fixed Prometheus port `9090`.

REST endpoints (mounted at `/bdds_qps/v1/stats`):
- `GET /bdds_qps/v1/stats/servers` — list BDDS servers currently reporting to Prometheus.
- `GET /bdds_qps/v1/stats/current` — current DNS QPS / DHCP LPS for all servers, or for one
  server via `?server=<exported_instance>`.
- `GET /bdds_qps/v1/doc/` — Swagger UI for the above.

UI page: `/bdds_qps_ui/page` (nav entry "BDDS Performance Statistics"), polls `/current` every 60
seconds — matching Prometheus's `global.scrape_interval` on BAM, so faster polling would
just re-read the same sample.

Known Errors and Bugs:
- As of this writing, the network path from this Gateway to BAM's Prometheus port (9090)
  is blocked (only port 443 is reachable between the two hosts). The API degrades
  gracefully (HTTP 502 with an error message) until that path is opened.
- Server-side rate is only as fresh as BAM's Prometheus scrape interval (1 minute).

Change Log:
- 2026-07-10: Prometheus host is now read from this Gateway's configured BAM connection
  (`bam_service.get_prometheus_base_url()`) instead of a hardcoded IP address.
- 2026-07-09: Initial version, replacing the earlier unfinished `bdds_qps` proof-of-concept
  workflow (generic form template with no real BDDS integration).

Screen Shot
<img width="3496" height="1140" alt="image" src="https://github.com/user-attachments/assets/93bab0a4-1b4e-435e-87ad-31576178eb94" />
