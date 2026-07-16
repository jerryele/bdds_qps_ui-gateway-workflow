# Copyright 2026 BlueCat Networks (USA) Inc. and its affiliates. All Rights Reserved.
"""
Constants for talking to BAM's built-in Prometheus server.
"""

# BAM's Prometheus HTTP API. The BDDS servers' own :10048 exporters require mutual TLS and
# are only meant to be scraped by BAM itself (see /etc/prometheus/scrape_configs.yml on BAM),
# so this workflow queries BAM's aggregated Prometheus instead of any BDDS directly.
# The host is never hardcoded here - it's derived from the BAM this Gateway is already
# configured against (see bam_service.get_prometheus_base_url()), since Prometheus always
# runs alongside BAM on that same host. Only the port is fixed.
PROMETHEUS_PORT = 9090
PROMETHEUS_TIMEOUT_SECONDS = 8

# Must match the `global.scrape_interval` configured in BAM's /etc/prometheus/prometheus.yml.
# The `*_since_poll` metrics are counts accumulated over one scrape interval, so dividing by
# this value converts them into an average per-second rate.
POLL_INTERVAL_SECONDS = 60

BDDS_JOB_LABEL = "bdds"

# BIND nsstats counters representing incoming DNS requests (see `nsstat` label values on
# bc_dns_nsstats_since_poll / bc_dns_nsstats_total).
DNS_REQUEST_NSSTATS = ["Requestv4", "Requestv6"]

# `bc_dns_cachestats` has a `view` label for every BIND view configured on the server, plus
# BIND's own builtin "_bind" view (always zero here) - only "default" carries real traffic.
BIND_DEFAULT_VIEW = "default"

# `bc_dns_cachestats{cachestat=...}` counters are cumulative since the BDDS's named process
# last started, not "since last poll" like nsstats - there's no delta variant of this metric.
CACHE_HIT_CACHESTAT = "CacheHits"
CACHE_MISS_CACHESTAT = "CacheMisses"
QUERY_HIT_CACHESTAT = "QueryHits"
QUERY_MISS_CACHESTAT = "QueryMisses"

# Host-level system metrics from BAM's Telegraf-style exporter, one series per BDDS with no
# extra labels to aggregate over (unlike per-core/per-device metrics on some exporters).
CPU_USAGE_METRIC = "bc_system_cpu_usage"  # 0-1 fraction, not already a percentage
MEMORY_USED_METRIC = "bc_system_memory_used_bytes"
MEMORY_AVAILABLE_METRIC = "bc_system_memory_available_bytes"

# `_since_poll` variants exist for these (unlike bc_dns_cachestats), so they convert to a
# per-second rate the same way DNS QPS does: divide by POLL_INTERVAL_SECONDS.
DISK_READS_METRIC = "bc_system_diskio_reads_since_poll"
DISK_WRITES_METRIC = "bc_system_diskio_writes_since_poll"
NET_RX_PACKETS_METRIC = "bc_system_netio_received_packets_since_poll"
NET_TX_PACKETS_METRIC = "bc_system_netio_sent_packets_since_poll"

# Every metric key `/current` and `/history` can return. Both endpoints accept a repeated
# `?metric=` filter to fetch only some of these - each one skipped means real Prometheus
# queries skipped too, not just columns/charts hidden after the fact.
ALL_METRIC_KEYS = frozenset({
    "dns_qps", "dhcp_lps", "cache_hit_ratio", "query_hit_ratio",
    "cpu_percent", "memory_percent", "disk_read_iops", "disk_write_iops",
    "net_rx_pps", "net_tx_pps",
})
