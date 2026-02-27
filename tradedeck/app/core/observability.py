"""
app/core/logging_config.py — Structured JSON logging for production.

Why JSON logs:
  - Parseable by Grafana Loki, Datadog, CloudWatch, ELK
  - Every log entry is a structured event, not a string to grep
  - Request ID threaded through all logs for trace correlation
"""
import json
import logging
import sys
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Optional

# Thread-local request context
_request_id: ContextVar[Optional[str]] = ContextVar("request_id", default=None)
_user_id:    ContextVar[Optional[str]] = ContextVar("user_id",    default=None)


def set_request_context(request_id: str, user_id: str = None):
    _request_id.set(request_id)
    _user_id.set(user_id)


def get_request_id() -> Optional[str]:
    return _request_id.get()


class JSONFormatter(logging.Formatter):
    """Formats log records as single-line JSON for structured log ingestion."""

    LEVEL_MAP = {
        "DEBUG":    "debug",
        "INFO":     "info",
        "WARNING":  "warn",
        "ERROR":    "error",
        "CRITICAL": "critical",
    }

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts":         datetime.now(timezone.utc).isoformat() + "Z",
            "level":      self.LEVEL_MAP.get(record.levelname, record.levelname.lower()),
            "logger":     record.name,
            "msg":        record.getMessage(),
            "module":     record.module,
            "func":       record.funcName,
            "line":       record.lineno,
        }

        # Inject request context if available
        req_id = _request_id.get()
        if req_id:
            entry["request_id"] = req_id
        usr_id = _user_id.get()
        if usr_id:
            entry["user_id"] = usr_id

        # Include exception info
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)

        # Include any extra fields passed via logger.info(..., extra={...})
        for key, val in record.__dict__.items():
            if key not in {
                "name", "msg", "args", "levelname", "levelno", "pathname",
                "filename", "module", "exc_info", "exc_text", "stack_info",
                "lineno", "funcName", "created", "msecs", "relativeCreated",
                "thread", "threadName", "processName", "process", "message",
                "taskName",
            } and not key.startswith("_"):
                entry[key] = val

        return json.dumps(entry, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Call once at startup to configure structured JSON logging."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove default handlers
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    root.addHandler(handler)

    # Quiet noisy libraries
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


# ═══════════════════════════════════════════════════════════
# PROMETHEUS METRICS
# ═══════════════════════════════════════════════════════════
"""
app/core/metrics.py — Prometheus metrics for trading system observability.

Exposes:
  - Order counters by status/side/product
  - Risk rejection counters by code
  - API latency histograms
  - Kill switch state gauge
  - Reconciliation lag gauge
  - Circuit breaker state gauge

Scrape endpoint: GET /metrics (Prometheus format)
"""
try:
    from prometheus_client import (
        Counter, Gauge, Histogram, Info,
        generate_latest, CONTENT_TYPE_LATEST, CollectorRegistry
    )
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False


if PROMETHEUS_AVAILABLE:
    REGISTRY = CollectorRegistry(auto_describe=True)

    # ── Order metrics ─────────────────────────────────────────
    orders_total = Counter(
        "tradedeck_orders_total",
        "Total orders by status, side, product type",
        ["status", "side", "product_type"],
        registry=REGISTRY,
    )

    orders_pnl = Gauge(
        "tradedeck_day_pnl_rupees",
        "Current day P&L in rupees",
        registry=REGISTRY,
    )

    # ── Risk metrics ──────────────────────────────────────────
    risk_rejections = Counter(
        "tradedeck_risk_rejections_total",
        "Orders rejected by risk engine, by rejection code",
        ["code"],
        registry=REGISTRY,
    )

    kill_switch_active = Gauge(
        "tradedeck_kill_switch_active",
        "1 if kill switch is active, 0 if not",
        registry=REGISTRY,
    )

    margin_usage_pct = Gauge(
        "tradedeck_margin_usage_pct",
        "Current margin utilisation percentage",
        registry=REGISTRY,
    )

    open_positions = Gauge(
        "tradedeck_open_positions",
        "Number of open positions",
        registry=REGISTRY,
    )

    # ── API performance ───────────────────────────────────────
    http_request_duration = Histogram(
        "tradedeck_http_request_duration_seconds",
        "HTTP request duration",
        ["method", "path", "status_code"],
        buckets=[.005, .01, .025, .05, .1, .25, .5, 1.0, 2.5, 5.0],
        registry=REGISTRY,
    )

    # ── Reconciliation ────────────────────────────────────────
    reconciliation_duration = Histogram(
        "tradedeck_reconciliation_duration_seconds",
        "Time taken per reconciliation cycle",
        buckets=[.1, .25, .5, 1.0, 2.5, 5.0, 10.0],
        registry=REGISTRY,
    )

    reconciliation_mismatches = Counter(
        "tradedeck_reconciliation_mismatches_total",
        "Total position/order mismatches detected",
        ["type"],
        registry=REGISTRY,
    )

    reconciliation_failures = Gauge(
        "tradedeck_reconciliation_consecutive_failures",
        "Consecutive reconciliation failures (persisted)",
        registry=REGISTRY,
    )

    # ── Circuit breakers ──────────────────────────────────────
    circuit_breaker_state = Gauge(
        "tradedeck_circuit_breaker_state",
        "Circuit breaker state: 0=CLOSED, 1=HALF_OPEN, 2=OPEN",
        ["service"],
        registry=REGISTRY,
    )

    CB_STATE_MAP = {"CLOSED": 0, "HALF_OPEN": 1, "OPEN": 2}

    def record_order(status: str, side: str, product_type: str):
        if PROMETHEUS_AVAILABLE:
            orders_total.labels(status=status, side=side, product_type=product_type).inc()

    def record_risk_rejection(code: str):
        if PROMETHEUS_AVAILABLE:
            risk_rejections.labels(code=code).inc()

    def update_session_metrics(pnl: float, margin_pct: float, pos_count: int, is_killed: bool):
        if PROMETHEUS_AVAILABLE:
            orders_pnl.set(pnl)
            margin_usage_pct.set(margin_pct)
            open_positions.set(pos_count)
            kill_switch_active.set(1 if is_killed else 0)

    def update_circuit_breaker(service: str, state: str):
        if PROMETHEUS_AVAILABLE:
            circuit_breaker_state.labels(service=service).set(CB_STATE_MAP.get(state, 0))

    def get_metrics_output() -> bytes:
        if PROMETHEUS_AVAILABLE:
            return generate_latest(REGISTRY)
        return b"# prometheus_client not installed\n"

else:
    # No-op stubs when prometheus_client not installed
    def record_order(*a, **k): pass
    def record_risk_rejection(*a, **k): pass
    def update_session_metrics(*a, **k): pass
    def update_circuit_breaker(*a, **k): pass
    def get_metrics_output() -> bytes:
        return b"# prometheus_client not installed\n"
