"""
Health Check Router — /health endpoints for all subsystems.

Endpoints:
  GET /health           — Quick liveness probe (no DB, for load balancer)
  GET /health/ready     — Full readiness: DB + broker + circuit breakers
  GET /health/detailed  — Complete diagnostic for ops dashboard
"""
import time
import logging
from datetime import datetime, timezone, date

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.circuit_breaker import BrokerCircuitBreakers

logger    = logging.getLogger(__name__)
router    = APIRouter(prefix="/health", tags=["Health"])
_start_ts = time.time()


# ─────────────────────────────────────────────
# LIVENESS — Used by Docker / k8s: is the process alive?
# Should NEVER fail unless process is dead. No external checks.
# ─────────────────────────────────────────────
@router.get("", summary="Liveness probe")
async def liveness():
    return {
        "status":   "ok",
        "service":  "tradedeck-api",
        "time":     datetime.now(timezone.utc).isoformat(),
        "uptime_s": round(time.time() - _start_ts, 1),
    }


# ─────────────────────────────────────────────
# READINESS — Is the service ready to handle traffic?
# Fails if DB or critical services are down.
# ─────────────────────────────────────────────
@router.get("/ready", summary="Readiness probe")
async def readiness(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    checks   = {}
    is_ready = True

    # ── Database ──────────────────────────────
    try:
        result = await db.execute(text("SELECT 1"))
        result.fetchone()
        checks["database"] = {"status": "ok"}
    except Exception as e:
        checks["database"] = {"status": "error", "detail": str(e)}
        is_ready = False

    # ── Broker connectivity (via circuit breaker state) ────────
    try:
        cb_statuses = await BrokerCircuitBreakers.all_statuses(db)
        order_cb    = next(c for c in cb_statuses if c["service"] == "fyers_orders")
        checks["broker_orders_circuit"] = {
            "status": "ok" if order_cb["state"] != "OPEN" else "degraded",
            "circuit_state": order_cb["state"],
        }
        if order_cb["state"] == "OPEN":
            is_ready = False  # Can't place orders if circuit is open
    except Exception as e:
        checks["broker_circuit"] = {"status": "error", "detail": str(e)}

    # ── Trading session ────────────────────────
    try:
        result = await db.execute(
            text("SELECT is_killed, kill_reason FROM trading_sessions WHERE date=:d"),
            {"d": date.today().isoformat()}
        )
        row = result.fetchone()
        if row:
            checks["trading_session"] = {
                "status":     "killed" if row.is_killed else "ok",
                "is_killed":  row.is_killed,
                "kill_reason": row.kill_reason,
            }
        else:
            checks["trading_session"] = {"status": "no_session", "detail": "Session not yet created"}
    except Exception as e:
        checks["trading_session"] = {"status": "error", "detail": str(e)}

    # ── Reconciliation health ──────────────────
    try:
        result = await db.execute(
            text(
                "SELECT reconcile_failure_count, last_reconcile_at, last_reconcile_status "
                "FROM trading_sessions WHERE date=:d"
            ),
            {"d": date.today().isoformat()}
        )
        row = result.fetchone()
        if row:
            checks["reconciliation"] = {
                "status":          "degraded" if row.reconcile_failure_count > 0 else "ok",
                "failure_count":   row.reconcile_failure_count,
                "last_run":        row.last_reconcile_at.isoformat() if row.last_reconcile_at else None,
                "last_status":     row.last_reconcile_status,
            }
    except Exception as e:
        checks["reconciliation"] = {"status": "error", "detail": str(e)}

    http_status = 200 if is_ready else 503
    return {
        "status":  "ready" if is_ready else "not_ready",
        "checks":  checks,
        "time":    datetime.now(timezone.utc).isoformat(),
    }


# ─────────────────────────────────────────────
# DETAILED — Full diagnostics for ops/monitoring
# ─────────────────────────────────────────────
@router.get("/detailed", summary="Detailed diagnostic")
async def detailed_health(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    result_data = {
        "service":       "tradedeck-api",
        "time":          datetime.now(timezone.utc).isoformat(),
        "uptime_s":      round(time.time() - _start_ts, 1),
        "database":      {},
        "circuit_breakers": [],
        "trading_session": {},
        "reconciliation": {},
        "orders_today":  {},
    }

    # Database stats
    try:
        # Check if we can reach the DB
        await db.execute(text("SELECT 1"))
        result_data["database"] = {
            "status": "ok",
            "active_connections": "N/A (SQLite)",
        }
    except Exception as e:
        result_data["database"] = {"status": "error", "detail": str(e)}

    # Circuit breaker states
    try:
        result_data["circuit_breakers"] = await BrokerCircuitBreakers.all_statuses(db)
    except Exception as e:
        result_data["circuit_breakers"] = [{"error": str(e)}]

    # Today's session summary
    try:
        result = await db.execute(
            text("SELECT * FROM trading_sessions WHERE date=:d"),
            {"d": date.today().isoformat()}
        )
        row = result.fetchone()
        if row:
            result_data["trading_session"] = {
                "id":                row.id,
                "date":              row.date,
                "is_killed":         row.is_killed,
                "kill_reason":       row.kill_reason,
                "realized_pnl":      row.realized_pnl,
                "unrealized_pnl":    row.unrealized_pnl,
                "day_pnl":           round(row.realized_pnl + row.unrealized_pnl, 2),
                "total_orders":      row.total_orders,
                "rejected_orders":   row.rejected_orders,
                "max_daily_loss":    row.max_daily_loss,
                "max_lot_size":      row.max_lot_size,
                "reconcile_failures": row.reconcile_failure_count,
                "last_reconcile":    row.last_reconcile_at.isoformat() if row.last_reconcile_at else None,
                "last_reconcile_status": row.last_reconcile_status,
            }
    except Exception as e:
        result_data["trading_session"] = {"error": str(e)}

    # Today's order breakdown by status
    try:
        result = await db.execute(text(
            "SELECT status, COUNT(*) as count FROM orders o "
            "JOIN trading_sessions s ON o.session_id=s.id "
            "WHERE s.date=:d GROUP BY status"
        ), {"d": date.today().isoformat()})
        result_data["orders_today"] = {row.status: row.count for row in result.fetchall()}
    except Exception as e:
        result_data["orders_today"] = {"error": str(e)}

    return result_data
