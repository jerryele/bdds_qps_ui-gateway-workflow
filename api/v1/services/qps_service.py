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
    DNS_REQUEST_NSSTATS,
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
        Return DNS-QPS and DHCP-LPS time series between `start` and `end`, one series per
        server per metric.

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

        dns_qps_series = []
        dhcp_lps_series = []
        cache_hit_series = []
        query_hit_series = []
        for s in all_servers:
            instance_filter = f'exported_instance="{s["exported_instance"]}"'

            dns_promql = f'sum(bc_dns_nsstats_since_poll{{{instance_filter}, nsstat=~"{nsstat_filter}"}})'
            dns_result = self.client.range_query(dns_promql, start.timestamp(), end.timestamp(), step)
            # The metric is always "count over the last poll interval" regardless of the
            # query step above, so the QPS conversion always divides by the poll interval,
            # never by `step_seconds` (a coarser step only thins out how often we sample it).
            dns_qps_series.append({
                "exported_instance": s["exported_instance"],
                "points": self._to_points(dns_result, scale=1 / POLL_INTERVAL_SECONDS),
            })

            dhcp_promql = f"bc_dhcp4_leases_per_second{{{instance_filter}}}"
            dhcp_result = self.client.range_query(dhcp_promql, start.timestamp(), end.timestamp(), step)
            dhcp_lps_series.append({
                "exported_instance": s["exported_instance"],
                "points": self._to_points(dhcp_result),
            })

            cache_hit_series.append({
                "exported_instance": s["exported_instance"],
                "points": self._read_ratio_history(
                    instance_filter, CACHE_HIT_CACHESTAT, CACHE_MISS_CACHESTAT, start, end, step
                ),
            })
            query_hit_series.append({
                "exported_instance": s["exported_instance"],
                "points": self._read_ratio_history(
                    instance_filter, QUERY_HIT_CACHESTAT, QUERY_MISS_CACHESTAT, start, end, step
                ),
            })

        return {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "step_seconds": step_seconds,
            "series": {
                "dns_qps": dns_qps_series,
                "dhcp_lps": dhcp_lps_series,
                "cache_hit_ratio": cache_hit_series,
                "query_hit_ratio": query_hit_series,
            },
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

        public = {
            **server,
            "dns_qps": round(dns_requests / POLL_INTERVAL_SECONDS, 2) if dns_requests is not None else None,
            "dhcp_lps": round(dhcp_lps, 2) if dhcp_lps is not None else None,
            "cache_hit_ratio": self._ratio_percent(cache_hits, cache_misses),
            "query_hit_ratio": self._ratio_percent(query_hits, query_misses),
        }
        raw_counts = {
            "cache_hits": cache_hits,
            "cache_misses": cache_misses,
            "query_hits": query_hits,
            "query_misses": query_misses,
        }
        return public, raw_counts

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
        Roll up DNS QPS / DHCP LPS (summable rates) and an overall cache/query hit ratio
        (summed hits and misses across servers, *then* divided - not an average of each
        server's percentage, which would misweight servers with very different traffic).
        """
        dns_qps_total = sum(s["dns_qps"] for s in stats if s["dns_qps"] is not None)
        dhcp_lps_total = sum(s["dhcp_lps"] for s in stats if s["dhcp_lps"] is not None)
        cache_hits_total = sum(r["cache_hits"] for r in raw_counts if r["cache_hits"] is not None)
        cache_misses_total = sum(r["cache_misses"] for r in raw_counts if r["cache_misses"] is not None)
        query_hits_total = sum(r["query_hits"] for r in raw_counts if r["query_hits"] is not None)
        query_misses_total = sum(r["query_misses"] for r in raw_counts if r["query_misses"] is not None)
        return {
            "dns_qps": round(dns_qps_total, 2),
            "dhcp_lps": round(dhcp_lps_total, 2),
            "cache_hit_ratio": QPSService._ratio_percent(cache_hits_total, cache_misses_total),
            "query_hit_ratio": QPSService._ratio_percent(query_hits_total, query_misses_total),
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
