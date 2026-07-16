# Copyright 2026 BlueCat Networks (USA) Inc. and its affiliates. All Rights Reserved.
"""
Business logic for retrieving BDDS QPS/LPS statistics from BAM's Prometheus.
"""
from datetime import datetime, timedelta, timezone

from . import bam_service
from .prometheus_client import PrometheusClient
from ..utils.constants import (
    BDDS_JOB_LABEL,
    BIND_DEFAULT_VIEW,
    CACHE_HIT_CACHESTAT,
    CACHE_MISS_CACHESTAT,
    CPU_USAGE_METRIC,
    DISK_READS_METRIC,
    DISK_WRITES_METRIC,
    DNS_REQUEST_NSSTATS,
    MEMORY_AVAILABLE_METRIC,
    MEMORY_USED_METRIC,
    NET_RX_PACKETS_METRIC,
    NET_TX_PACKETS_METRIC,
    POLL_INTERVAL_SECONDS,
    QUERY_HIT_CACHESTAT,
    QUERY_MISS_CACHESTAT,
)


class QPSService:
    """Reads DNS/DHCP performance counters for BDDS servers out of BAM's Prometheus."""

    def __init__(self, client: PrometheusClient = None):
        self.client = client or PrometheusClient(bam_service.get_prometheus_base_url())

    def list_servers(self, configuration: str = None) -> list:
        """
        Return the BDDS servers currently reporting DNS statistics to Prometheus.

        :param configuration: A BAM configuration ID. When given, the list is scoped to
            servers that belong to that configuration; when omitted, every reporting
            BDDS is returned.
        """
        promql = f'group by (exported_instance, instance, server_id) (bc_dns_nsstats_since_poll{{job="{BDDS_JOB_LABEL}"}})'
        result = self.client.instant_query(promql)
        servers = [self._server_labels(series["metric"]) for series in result]

        if configuration:
            allowed_ids = bam_service.list_server_ids(configuration)
            servers = [s for s in servers if s["server_id"] in allowed_ids]

        return servers

    def get_qps(self, servers: list = None, configuration: str = None) -> dict:
        """
        Return current DNS QPS and DHCP LPS.

        :param servers: A list of `exported_instance` label values to filter to. When
            empty or omitted, every server in scope is included.
        :param configuration: A BAM configuration ID to scope the server list to (see
            `list_servers`). Ignored if `servers` is given.
        """
        all_servers = self.list_servers(configuration)
        if servers:
            wanted = set(servers)
            all_servers = [s for s in all_servers if s["exported_instance"] in wanted]

        results = [self._read_server_stats(s) for s in all_servers]
        stats = [public for public, _raw in results]
        raw_counts = [raw for _public, raw in results]
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "poll_interval_seconds": POLL_INTERVAL_SECONDS,
            "servers": stats,
            "totals": self._compute_totals(stats, raw_counts),
            # Every PromQL request this call made against BAM's Prometheus, for the page's
            # "API calls to BAM's Prometheus" panel - purely informational.
            "api_calls": self.client.calls,
        }

    def get_history(
        self,
        servers: list = None,
        configuration: str = None,
        start: datetime = None,
        end: datetime = None,
    ) -> dict:
        """
        Return time series for all ten `/current` metrics between `start` and `end`, one
        series per server per metric.

        :param servers: A list of `exported_instance` label values to filter to. When
            empty or omitted, every server in scope is included.
        :param configuration: A BAM configuration ID to scope the server list to (see
            `list_servers`). Ignored if `servers` is given.
        :param start: Start of the window (defaults to `end` minus 60 minutes).
        :param end: End of the window (defaults to now).
        """
        all_servers = self.list_servers(configuration)
        if servers:
            wanted = set(servers)
            all_servers = [s for s in all_servers if s["exported_instance"] in wanted]

        end = end or datetime.now(timezone.utc)
        start = start or (end - timedelta(minutes=60))
        step_seconds = self._pick_step_seconds((end - start).total_seconds())
        step = f"{step_seconds}s"
        nsstat_filter = "|".join(DNS_REQUEST_NSSTATS)

        series = {key: [] for key in (
            "dns_qps", "dhcp_lps", "cache_hit_ratio", "query_hit_ratio",
            "cpu_percent", "memory_percent", "disk_read_iops", "disk_write_iops",
            "net_rx_pps", "net_tx_pps",
        )}
        for s in all_servers:
            instance_filter = f'exported_instance="{s["exported_instance"]}"'

            def series_point(key, points):
                series[key].append({"exported_instance": s["exported_instance"], "points": points})

            dns_promql = f'sum(bc_dns_nsstats_since_poll{{{instance_filter}, nsstat=~"{nsstat_filter}"}})'
            dns_result = self.client.range_query(dns_promql, start.timestamp(), end.timestamp(), step)
            # The metric is always "count over the last poll interval" regardless of the
            # query step above, so the QPS conversion always divides by the poll interval,
            # never by `step_seconds` (a coarser step only thins out how often we sample it).
            series_point("dns_qps", self._to_points(dns_result, scale=1 / POLL_INTERVAL_SECONDS))

            dhcp_promql = f"bc_dhcp4_leases_per_second{{{instance_filter}}}"
            dhcp_result = self.client.range_query(dhcp_promql, start.timestamp(), end.timestamp(), step)
            series_point("dhcp_lps", self._to_points(dhcp_result))

            series_point("cache_hit_ratio", self._read_ratio_history(
                instance_filter, CACHE_HIT_CACHESTAT, CACHE_MISS_CACHESTAT, start, end, step
            ))
            series_point("query_hit_ratio", self._read_ratio_history(
                instance_filter, QUERY_HIT_CACHESTAT, QUERY_MISS_CACHESTAT, start, end, step
            ))

            cpu_promql = f"{CPU_USAGE_METRIC}{{{instance_filter}}}"
            cpu_result = self.client.range_query(cpu_promql, start.timestamp(), end.timestamp(), step)
            series_point("cpu_percent", self._to_points(cpu_result, scale=100))

            series_point("memory_percent", self._read_metric_pair_ratio_history(
                instance_filter, MEMORY_USED_METRIC, MEMORY_AVAILABLE_METRIC, start, end, step
            ))

            disk_history = self._read_combined_history(
                instance_filter, [DISK_READS_METRIC, DISK_WRITES_METRIC], start, end, step,
                scale=1 / POLL_INTERVAL_SECONDS,
            )
            series_point("disk_read_iops", disk_history.get(DISK_READS_METRIC, []))
            series_point("disk_write_iops", disk_history.get(DISK_WRITES_METRIC, []))

            net_history = self._read_combined_history(
                instance_filter, [NET_RX_PACKETS_METRIC, NET_TX_PACKETS_METRIC], start, end, step,
                scale=1 / POLL_INTERVAL_SECONDS,
            )
            series_point("net_rx_pps", net_history.get(NET_RX_PACKETS_METRIC, []))
            series_point("net_tx_pps", net_history.get(NET_TX_PACKETS_METRIC, []))

        return {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "step_seconds": step_seconds,
            "series": series,
        }

    @staticmethod
    def _pick_step_seconds(span_seconds: float) -> int:
        """Coarsen the query step for longer windows, so long ranges stay a reasonable size."""
        if span_seconds <= 3600:
            return POLL_INTERVAL_SECONDS
        if span_seconds <= 24 * 3600:
            return 300
        if span_seconds <= 7 * 24 * 3600:
            return 3600
        return 7200

    def _read_server_stats(self, server: dict) -> tuple:
        """
        Return `(public_stats, raw_cache_counts)` for one server: the dict shown in the API
        response, plus the raw hit/miss counts backing its ratios (needed to roll up an
        overall, correctly-weighted ratio across servers in `_compute_totals` - simply
        averaging per-server percentages would misweight servers with very different
        traffic volumes).
        """
        instance_filter = f'exported_instance="{server["exported_instance"]}"'

        # `bc_dns_nsstats_since_poll` is a per-scrape delta (see POLL_INTERVAL_SECONDS), so
        # summing the incoming-request counters and dividing by the poll interval gives an
        # average queries-per-second figure for that window.
        nsstat_filter = "|".join(DNS_REQUEST_NSSTATS)
        dns_promql = f'sum(bc_dns_nsstats_since_poll{{{instance_filter}, nsstat=~"{nsstat_filter}"}})'
        dns_requests = self._first_value(self.client.instant_query(dns_promql))

        # `bc_dhcp4_leases_per_second` is already a computed rate, no conversion needed.
        dhcp_promql = f"bc_dhcp4_leases_per_second{{{instance_filter}}}"
        dhcp_lps = self._first_value(self.client.instant_query(dhcp_promql))

        cache_hits, cache_misses = self._read_cache_counts(instance_filter, CACHE_HIT_CACHESTAT, CACHE_MISS_CACHESTAT)
        query_hits, query_misses = self._read_cache_counts(instance_filter, QUERY_HIT_CACHESTAT, QUERY_MISS_CACHESTAT)

        # `bc_system_cpu_usage` is a 0-1 fraction already, not a percentage.
        cpu_usage = self._first_value(self.client.instant_query(f"{CPU_USAGE_METRIC}{{{instance_filter}}}"))
        cpu_percent = round(cpu_usage * 100, 2) if cpu_usage is not None else None

        # No "total memory" metric is exposed; used/(used+available) matches the same
        # arithmetic `free`-style tools use for "% memory used".
        memory = self._read_metrics(instance_filter, [MEMORY_USED_METRIC, MEMORY_AVAILABLE_METRIC])
        memory_percent = self._ratio_percent(memory.get(MEMORY_USED_METRIC), memory.get(MEMORY_AVAILABLE_METRIC))

        disk = self._read_metrics(instance_filter, [DISK_READS_METRIC, DISK_WRITES_METRIC])
        disk_read_iops = self._rate_per_second(disk.get(DISK_READS_METRIC))
        disk_write_iops = self._rate_per_second(disk.get(DISK_WRITES_METRIC))

        net = self._read_metrics(instance_filter, [NET_RX_PACKETS_METRIC, NET_TX_PACKETS_METRIC])
        net_rx_pps = self._rate_per_second(net.get(NET_RX_PACKETS_METRIC))
        net_tx_pps = self._rate_per_second(net.get(NET_TX_PACKETS_METRIC))

        public = {
            **server,
            "dns_qps": round(dns_requests / POLL_INTERVAL_SECONDS, 2) if dns_requests is not None else None,
            "dhcp_lps": round(dhcp_lps, 2) if dhcp_lps is not None else None,
            "cache_hit_ratio": self._ratio_percent(cache_hits, cache_misses),
            "query_hit_ratio": self._ratio_percent(query_hits, query_misses),
            "cpu_percent": cpu_percent,
            "memory_percent": memory_percent,
            "disk_read_iops": disk_read_iops,
            "disk_write_iops": disk_write_iops,
            "net_rx_pps": net_rx_pps,
            "net_tx_pps": net_tx_pps,
        }
        raw_counts = {
            "cache_hits": cache_hits,
            "cache_misses": cache_misses,
            "query_hits": query_hits,
            "query_misses": query_misses,
        }
        return public, raw_counts

    def _read_metrics(self, instance_filter: str, metric_names: list) -> dict:
        """
        Fetch several differently-*named* instant-value metrics for one server in a single
        Prometheus request (matching on `__name__` directly), keyed by metric name.

        Unlike `bc_dns_cachestats{cachestat=...}`, these host metrics each have their own
        metric name rather than sharing one name with a distinguishing label, so this is
        the equivalent trick for keeping it to one request instead of one per metric.
        """
        names_filter = "|".join(metric_names)
        promql = f'{{__name__=~"{names_filter}", {instance_filter}}}'
        result = self.client.instant_query(promql)
        return {series["metric"]["__name__"]: float(series["value"][1]) for series in result}

    def _read_combined_history(
        self, instance_filter: str, metric_names: list, start: datetime, end: datetime, step: str, scale: float = 1.0
    ) -> dict:
        """
        The history-series equivalent of `_read_metrics`: range-query several differently-
        *named* metrics in one Prometheus request, returned as `{metric_name: points}`
        using the same scale/rounding as `_to_points`.
        """
        names_filter = "|".join(metric_names)
        promql = f'{{__name__=~"{names_filter}", {instance_filter}}}'
        result = self.client.range_query(promql, start.timestamp(), end.timestamp(), step)
        return {series["metric"]["__name__"]: self._to_points(result=[series], scale=scale) for series in result}

    def _read_metric_pair_ratio_history(
        self, instance_filter: str, numerator_metric: str, denominator_metric: str,
        start: datetime, end: datetime, step: str,
    ) -> list:
        """
        Return `{"t", "v"}` ratio-percentage points (numerator / (numerator + denominator) *
        100) from two differently-*named* instant-value metrics, one point per timestamp
        both sides report. Used for memory % (used / (used + available)) - the host-metric
        equivalent of `_read_ratio_history`, which does the same for two metrics that share
        one name and are distinguished by a label instead (`bc_dns_cachestats`).
        """
        history = self._read_combined_history(instance_filter, [numerator_metric, denominator_metric], start, end, step)
        numerator_by_ts = {p["t"]: p["v"] for p in history.get(numerator_metric, [])}
        denominator_by_ts = {p["t"]: p["v"] for p in history.get(denominator_metric, [])}

        points = []
        for t in sorted(numerator_by_ts):
            if t not in denominator_by_ts:
                continue
            ratio = self._ratio_percent(numerator_by_ts[t], denominator_by_ts[t])
            if ratio is not None:
                points.append({"t": t, "v": ratio})
        return points

    @staticmethod
    def _rate_per_second(since_poll_value):
        return round(since_poll_value / POLL_INTERVAL_SECONDS, 2) if since_poll_value is not None else None

    def _read_cache_counts(self, instance_filter: str, hit_stat: str, miss_stat: str) -> tuple:
        """
        Return the raw `(hits, misses)` values (either may be `None`) for a pair of
        `bc_dns_cachestats` counters.

        These are lifetime counters (since the BDDS's named process last started), not a
        recent-window rate like `dns_qps`/`dhcp_lps` - there's no "since last poll" variant
        of `bc_dns_cachestats` for BAM's exporter to expose.
        """
        promql = (
            f"bc_dns_cachestats{{{instance_filter}, view=\"{BIND_DEFAULT_VIEW}\", "
            f'cachestat=~"{hit_stat}|{miss_stat}"}}'
        )
        result = self.client.instant_query(promql)
        values = {series["metric"]["cachestat"]: float(series["value"][1]) for series in result}
        return values.get(hit_stat), values.get(miss_stat)

    def _read_ratio_history(
        self, instance_filter: str, hit_stat: str, miss_stat: str, start: datetime, end: datetime, step: str
    ) -> list:
        """Return `{"t", "v"}` hit-ratio-percentage points, one per Prometheus sample."""
        promql = (
            f"bc_dns_cachestats{{{instance_filter}, view=\"{BIND_DEFAULT_VIEW}\", "
            f'cachestat=~"{hit_stat}|{miss_stat}"}}'
        )
        result = self.client.range_query(promql, start.timestamp(), end.timestamp(), step)
        series_by_stat = {series["metric"]["cachestat"]: series["values"] for series in result}
        hit_values = series_by_stat.get(hit_stat, [])
        # Look misses up by timestamp rather than zipping the two lists index-for-index, in
        # case Prometheus drops/staggers a sample on one side (e.g. a scrape that timed out).
        misses_by_ts = {int(t): float(v) for t, v in series_by_stat.get(miss_stat, [])}

        points = []
        for t, hits in hit_values:
            ts = int(t)
            if ts not in misses_by_ts:
                continue
            ratio = self._ratio_percent(float(hits), misses_by_ts[ts])
            if ratio is not None:
                points.append({"t": ts, "v": ratio})
        return points

    @staticmethod
    def _ratio_percent(hits, misses):
        """A hit-ratio percentage (0-100) from a pair of hit/miss counts, or `None` if
        either is missing or both are zero."""
        if hits is None or misses is None or (hits + misses) == 0:
            return None
        return round(100 * hits / (hits + misses), 2)

    @staticmethod
    def _compute_totals(stats: list, raw_counts: list) -> dict:
        """
        Roll up each metric the way that makes sense for its shape:
        - DNS QPS / DHCP LPS / disk IOPS / network pps are rates, so they're summed.
        - Cache/query hit ratio: hits and misses are summed across servers *first*, then
          divided - not an average of each server's percentage, which would misweight
          servers with very different traffic volumes.
        - CPU % / memory % have no traffic-like weight to sum by, so this is a plain mean
          across servers reporting a value.
        """
        def total(key):
            return sum(s[key] for s in stats if s[key] is not None)

        def mean(key):
            values = [s[key] for s in stats if s[key] is not None]
            return round(sum(values) / len(values), 2) if values else None

        cache_hits_total = sum(r["cache_hits"] for r in raw_counts if r["cache_hits"] is not None)
        cache_misses_total = sum(r["cache_misses"] for r in raw_counts if r["cache_misses"] is not None)
        query_hits_total = sum(r["query_hits"] for r in raw_counts if r["query_hits"] is not None)
        query_misses_total = sum(r["query_misses"] for r in raw_counts if r["query_misses"] is not None)
        return {
            "dns_qps": round(total("dns_qps"), 2),
            "dhcp_lps": round(total("dhcp_lps"), 2),
            "cache_hit_ratio": QPSService._ratio_percent(cache_hits_total, cache_misses_total),
            "query_hit_ratio": QPSService._ratio_percent(query_hits_total, query_misses_total),
            "cpu_percent": mean("cpu_percent"),
            "memory_percent": mean("memory_percent"),
            "disk_read_iops": round(total("disk_read_iops"), 2),
            "disk_write_iops": round(total("disk_write_iops"), 2),
            "net_rx_pps": round(total("net_rx_pps"), 2),
            "net_tx_pps": round(total("net_tx_pps"), 2),
        }

    @staticmethod
    def _server_labels(metric: dict) -> dict:
        return {
            "exported_instance": metric.get("exported_instance"),
            "instance": metric.get("instance"),
            "server_id": metric.get("server_id"),
        }

    @staticmethod
    def _first_value(result: list):
        if not result:
            return None
        return float(result[0]["value"][1])

    @staticmethod
    def _to_points(result: list, scale: float = 1.0) -> list:
        if not result:
            return []
        return [{"t": int(v[0]), "v": round(float(v[1]) * scale, 2)} for v in result[0]["values"]]
