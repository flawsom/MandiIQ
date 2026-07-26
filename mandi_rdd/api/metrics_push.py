"""Push API metrics to Grafana Cloud Prometheus via Pushgateway.

Reads in-memory state from main.py, formats it as Prometheus metrics,
and pushes to Grafana Cloud every 60 seconds via a daemon thread.

Environment variables:
  GRAFANA_CLOUD_PROM_URL   - Prometheus base URL
                             Use the **Pushgateway** endpoint, e.g.
                             https://prometheus-prod-XX-prod-YY.grafana.net/api/v1/push
                             If you have the remote-write endpoint
                             (/api/prom/push) the code auto-converts it.
  GRAFANA_CLOUD_PROM_USER  - Username / Instance ID (numeric)
  GRAFANA_CLOUD_PROM_PASS  - Grafana Cloud API token (glsa_...)
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import NoReturn

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    pushadd_to_gateway,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config from environment
# ---------------------------------------------------------------------------
_PROM_URL = os.environ.get("GRAFANA_CLOUD_PROM_URL", "").rstrip("/")
# If user provided the remote-write URL (/api/prom/push), auto-convert
# to the pushgateway URL (/api/v1/push) which pushadd_to_gateway expects.
if "/api/prom/push" in _PROM_URL:
    _PROM_URL = _PROM_URL.replace("/api/prom/push", "/api/v1/push")
# If URL is a bare hostname without an API path, append the pushgateway path.
elif _PROM_URL and "/api/" not in _PROM_URL:
    _PROM_URL = _PROM_URL + "/api/v1/push"
_PROM_USER = os.environ.get("GRAFANA_CLOUD_PROM_USER", "")
_PROM_PASS = os.environ.get("GRAFANA_CLOUD_PROM_PASSWORD", "")

_PUSH_ENABLED = bool(_PROM_URL and _PROM_USER and _PROM_PASS)
_PUSH_INTERVAL = 60  # seconds

# ---------------------------------------------------------------------------
# Prometheus registry + metric descriptors
# ---------------------------------------------------------------------------
_registry = CollectorRegistry()

_uptime = Gauge(
    "mandiiq_uptime_seconds",
    "Time since the API server started.",
    registry=_registry,
)
_llm_fallback = Counter(
    "mandiiq_llm_fallback_total",
    "Number of times call_llm() exhausted all models.",
    registry=_registry,
)
_health_checks = Counter(
    "mandiiq_health_checks_total",
    "Total health check requests.",
    registry=_registry,
)
_cold_starts = Counter(
    "mandiiq_cold_starts_total",
    "Number of cold starts (server restarts) detected.",
    registry=_registry,
)
_prices_count = Gauge(
    "mandiiq_prices_count",
    "Current number of price records in the database.",
    registry=_registry,
)
_cache_loaded = Gauge(
    "mandiiq_dashboard_cache_loaded",
    "Whether dashboard JSON is loaded (1=yes, 0=no).",
    registry=_registry,
)
_cache_refresh = Gauge(
    "mandiiq_dashboard_cache_last_refresh_timestamp_seconds",
    "Unix timestamp of last cache refresh.",
    registry=_registry,
)
_cache_mtime = Gauge(
    "mandiiq_dashboard_cache_file_mtime_timestamp_seconds",
    "Unix timestamp of dashboard file modification.",
    registry=_registry,
)
_cache_stale = Gauge(
    "mandiiq_dashboard_cache_stale",
    "Whether file on disk is newer than loaded cache (1=stale, 0=fresh).",
    registry=_registry,
)

# ---------------------------------------------------------------------------
# Pull values from main.py's in-memory state, then push
# ---------------------------------------------------------------------------

def _refresh_and_push() -> None:
    """Read current values from main.py health_stats, update registry, push."""
    # Lazy import to avoid circular dependency at module load time.
    from mandi_rdd.api.main import (
        health_stats,
        get_llm_fallback_count,
    )
    # Dashboard cache globals
    from mandi_rdd.api.main import (
        dashboard_json,
        _dashboard_last_refresh,
        _dashboard_file_mtime,
    )

    _uptime.set(time.time() - health_stats.start_time)
    _llm_fallback._value.set(float(get_llm_fallback_count()))
    _health_checks._value.set(float(health_stats.health_count))
    _cold_starts._value.set(float(health_stats.cold_start))

    # Prices count
    try:
        from mandi_rdd.data.connection import get_connection
        conn = get_connection()
        n = conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
        conn.close()
        _prices_count.set(n)
    except Exception:
        _prices_count.set(-1)

    # Dashboard cache
    _cache_loaded.set(1 if dashboard_json is not None else 0)
    _cache_refresh.set(_dashboard_last_refresh)
    _cache_mtime.set(_dashboard_file_mtime)
    # Dashboard cache staleness
    _cache_stale.set(0)

    try:
        pushadd_to_gateway(
            _PROM_URL,
            job="mandiiq-api",
            registry=_registry,
            username=_PROM_USER,
            password=_PROM_PASS,
            timeout=30,
        )
    except Exception:
        logger.warning("Grafana Cloud push failed", exc_info=True)


def _push_loop() -> NoReturn:
    """Background loop."""
    logger.info(
        "Grafana Cloud push enabled — every %s s to %s",
        _PUSH_INTERVAL,
        _PROM_URL,
    )
    time.sleep(10)  # give main.py time to fully initialize
    while True:
        try:
            _refresh_and_push()
        except Exception:
            logger.warning("Grafana Cloud push refresh failed", exc_info=True)
        time.sleep(_PUSH_INTERVAL)


def start_push_thread() -> None:
    """Start the daemon push thread (called once from main.py startup)."""
    if not _PUSH_ENABLED:
        logger.info("Grafana Cloud push disabled — set GRAFANA_CLOUD_PROM_* env vars")
        return
    t = threading.Thread(target=_push_loop, daemon=True)
    t.start()
