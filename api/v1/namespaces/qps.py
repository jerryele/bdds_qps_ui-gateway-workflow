# Copyright 2026 BlueCat Networks (USA) Inc. and its affiliates. All Rights Reserved.
"""
REST endpoints for BDDS DNS/DHCP performance statistics, backed by BAM's built-in Prometheus.
"""
from datetime import datetime

from flask import request
from flask_restx import Namespace, Resource

from ..services.qps_service import QPSService
from ..services import bam_service
from ..utils.constants import ALL_METRIC_KEYS
from ..utils.exceptions import PrometheusQueryError

stats_ns = Namespace("stats", description="BDDS DNS/DHCP performance statistics")


def _parse_iso(value: str) -> datetime:
    # `datetime.fromisoformat` only accepts a trailing "Z" from Python 3.11 onward; this
    # workflow runs on 3.9, so normalize it to an explicit UTC offset first.
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _parse_metrics(request_args) -> list:
    """
    Return the requested `?metric=` filter as a list, or `None` if the param was omitted
    entirely (meaning "fetch everything", for backward compatibility with callers that
    don't know about this filter).

    The UI always sends this param explicitly, using the reserved value "none" for "every
    metric checkbox is off" - `getlist` can't otherwise tell "sent as empty" apart from
    "not sent at all", and the latter must still mean "fetch everything" for callers that
    predate this filter.

    :raises ValueError: If any given metric key isn't one `/current`/`/history` recognize.
    """
    metrics = request_args.getlist("metric")
    if not metrics:
        return None
    if metrics == ["none"]:
        return []
    unknown = set(metrics) - ALL_METRIC_KEYS
    if unknown:
        raise ValueError(f"Unknown metric(s): {', '.join(sorted(unknown))}")
    return metrics


@stats_ns.route("/configurations")
class ConfigurationList(Resource):
    """List the BAM configurations available to scope the server list to."""

    def get(self):
        return {"configurations": bam_service.list_configurations()}, 200


@stats_ns.route("/servers")
class ServerList(Resource):
    """List the BDDS servers currently reporting statistics to BAM's Prometheus."""

    def get(self):
        configuration = request.args.get("configuration")
        try:
            return {"servers": QPSService().list_servers(configuration)}, 200
        except PrometheusQueryError as e:
            return {"error": str(e)}, 502


@stats_ns.route("/current")
class CurrentStats(Resource):
    """
    Current DNS QPS, DHCP LPS, and other per-server metrics (see `ALL_METRIC_KEYS`).

    Accepts a repeated `?server=<exported_instance>` query param to scope to one or more
    servers, and/or `?configuration=<id>` to scope to a BAM configuration. With neither,
    every reporting server is included. A repeated `?metric=<key>` param scopes which
    metrics are fetched at all - unlisted metrics aren't queried against Prometheus, not
    just hidden from the response. With no `?metric=` given, every metric is fetched.
    """

    def get(self):
        servers = request.args.getlist("server")
        configuration = request.args.get("configuration")
        try:
            metrics = _parse_metrics(request.args)
        except ValueError as e:
            return {"error": str(e)}, 400
        try:
            return QPSService().get_qps(servers, configuration, metrics), 200
        except PrometheusQueryError as e:
            return {"error": str(e)}, 502


@stats_ns.route("/history")
class HistoryStats(Resource):
    """
    Time series over a time window, one series per server per metric.

    Accepts the same `?server=` / `?configuration=` / `?metric=` scoping as `/current`,
    plus optional `?start=` / `?end=` ISO 8601 timestamps. With neither, defaults to the
    last 60 minutes.
    """

    def get(self):
        servers = request.args.getlist("server")
        configuration = request.args.get("configuration")
        start_param = request.args.get("start")
        end_param = request.args.get("end")
        try:
            metrics = _parse_metrics(request.args)
        except ValueError as e:
            return {"error": str(e)}, 400
        try:
            start = _parse_iso(start_param) if start_param else None
            end = _parse_iso(end_param) if end_param else None
        except ValueError:
            return {"error": "start/end must be ISO 8601 timestamps"}, 400
        try:
            return QPSService().get_history(servers, configuration, start, end, metrics), 200
        except PrometheusQueryError as e:
            return {"error": str(e)}, 502
