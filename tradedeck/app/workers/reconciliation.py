"""
ReconciliationWorker — Broker ↔ DB sync with persistent failure counter.

Critical fix from v1:
  - Failure counter stored in trading_sessions.reconcile_failure_count (DB)
  - Process restart no longer resets failure count
  - Kill switch trigger after MAX_RECONCILE_FAILURES persists across restarts
  - asyncio background task with proper cancellation handling
  - Run-once method exposed for testing and manual trigger
"""
import asyncio
import logging
import time
from datetime import datetime, date, timezone
from typing import Optional

from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.db import (
    Order, OrderStatus, Position, ReconcileStatus,
    ReconciliationLog, TradingSession, KillSwitchReason
)
from app.services.broker_service import BrokerService, BrokerError

logger = logging.getLogger(__name__)

RECONCILE_INTERVAL_SECONDS = 15
MAX_RECONCILE_FAILURES     = 3      # Consecutive DB-persisted failures before kill switch


class ReconciliationWorker:

    def __init__(
        self,
        broker: "BrokerService",
        risk_engine,
        session_factory: async_sessionmaker,
    ):
        self.broker          = broker
        self.risk            = risk_engine
        self.session_factory = session_factory
        self._running        = False
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        self._running = True
        self._task    = asyncio.create_task(self._loop(), name="reconciliation_worker")
        logger.info("Reconciliation worker started (interval=%ds)", RECONCILE_INTERVAL_SECONDS)

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await asyncio.wait_for(self._task, timeout=10.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        logger.info("Reconciliation worker stopped")

    async def _loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(RECONCILE_INTERVAL_SECONDS)
                if self._running:
                    await self.run_once()
            except asyncio.CancelledError:
                break
            except Exception as e:
                # Log but never let the loop die — always reschedule
                logger.error("Reconciliation loop unhandled error: %s", e, exc_info=True)

    # ─────────────────────────────────────────
    # MAIN RECONCILIATION CYCLE
    # ─────────────────────────────────────────
    async def run_once(self) -> Optional[ReconciliationLog]:
        start_ms   = int(time.time() * 1000)
        mismatches = []
        corrections = []

        async with self.session_factory() as db:
            session = await self._get_active_session(db)
            if not session:
                return None

            # ── Fetch from broker ──────────────────────────────
            broker_positions = []
            broker_orders    = []
            fetch_failed     = False

            try:
                broker_positions, broker_orders = await asyncio.gather(
                    self.broker.get_positions(),
                    self.broker.get_orders(),
                )
            except BrokerError as e:
                fetch_failed = True
                logger.error("Reconciliation fetch failed: %s", e)
            except Exception as e:
                fetch_failed = True
                logger.error("Reconciliation unexpected error: %s", e, exc_info=True)

            if fetch_failed:
                # Increment persisted failure counter
                new_count = await self._increment_failure_count(db, session)

                log = ReconciliationLog(
                    status="FAILED",
                    error_message=f"Broker fetch failed (consecutive failures: {new_count})",
                    duration_ms=int(time.time() * 1000) - start_ms,
                )
                db.add(log)
                await db.commit()

                # Kill switch if failure threshold reached
                if new_count >= MAX_RECONCILE_FAILURES:
                    logger.critical(
                        "Triggering kill switch: %d consecutive reconciliation failures",
                        new_count
                    )
                    await self.risk._trigger_kill_switch(
                        db, str(session.id), KillSwitchReason.RECONCILE_FAIL,
                        f"Broker unreachable for {new_count} consecutive reconciliation cycles"
                    )
                    await db.commit()
                return log

            # ── Success — reset failure counter ───────────────
            await db.execute(
                text(
                    "UPDATE trading_sessions SET "
                    "reconcile_failure_count=0, last_reconcile_at=NOW(), "
                    "last_reconcile_status='OK', updated_at=NOW() "
                    "WHERE id=:id"
                ),
                {"id": str(session.id)}
            )

            # ── Reconcile ──────────────────────────────────────
            pm, pc = await self._reconcile_positions(db, session, broker_positions)
            om, oc = await self._reconcile_orders(db, session, broker_orders)
            rc     = await self._recover_orphaned_orders(db, session, broker_orders)

            mismatches.extend(pm + om)
            corrections.extend(pc + oc + rc)

            # Update unrealized P&L from broker
            total_unrealized = sum(p.get("pnl", 0) for p in broker_positions)
            await db.execute(
                text("UPDATE trading_sessions SET unrealized_pnl=:pnl WHERE id=:id"),
                {"pnl": total_unrealized, "id": str(session.id)}
            )

            duration_ms = int(time.time() * 1000) - start_ms
            status = "MISMATCH" if mismatches else "OK"

            # Update session reconcile status
            await db.execute(
                text("UPDATE trading_sessions SET last_reconcile_status=:s WHERE id=:id"),
                {"s": status, "id": str(session.id)}
            )

            log = ReconciliationLog(
                status=status,
                positions_checked=len(broker_positions),
                orders_checked=len(broker_orders),
                mismatches=mismatches,
                corrections=corrections,
                duration_ms=duration_ms,
            )
            db.add(log)
            await db.commit()

            log_fn = logger.warning if mismatches else logger.debug
            log_fn(
                "Reconciliation %s: %d pos, %d orders, %d mismatches, %d corrections (%dms)",
                status, len(broker_positions), len(broker_orders),
                len(mismatches), len(corrections), duration_ms
            )
            return log

    # ─────────────────────────────────────────
    # POSITION RECONCILIATION
    # ─────────────────────────────────────────
    async def _reconcile_positions(self, db, session, broker_positions: list):
        mismatches  = []
        corrections = []

        broker_map = {p["symbol"]: p for p in broker_positions}

        result = await db.execute(
            text("SELECT * FROM positions WHERE session_id=:sid"),
            {"sid": str(session.id)}
        )
        local_positions = result.fetchall()

        for pos in local_positions:
            broker_pos = broker_map.get(pos.symbol)
            broker_qty = broker_pos["net_qty"] if broker_pos else 0

            if broker_qty != pos.net_quantity:
                mismatches.append({
                    "type": "POSITION_QTY_MISMATCH",
                    "symbol": pos.symbol,
                    "local_qty": pos.net_quantity,
                    "broker_qty": broker_qty,
                })
                await db.execute(
                    text(
                        "UPDATE positions SET net_quantity=:qty, broker_quantity=:bq, "
                        "reconcile_status='CORRECTED', last_reconciled_at=NOW() "
                        "WHERE id=:id"
                    ),
                    {"qty": broker_qty, "bq": broker_qty, "id": str(pos.id)}
                )
                corrections.append({"symbol": pos.symbol, "action": "QTY_SYNCED", "to": broker_qty})
            else:
                # Update LTP + mark OK
                ltp = broker_pos.get("ltp", pos.ltp) if broker_pos else pos.ltp
                await db.execute(
                    text(
                        "UPDATE positions SET ltp=:ltp, broker_quantity=:bq, "
                        "reconcile_status='OK', last_reconciled_at=NOW() WHERE id=:id"
                    ),
                    {"ltp": ltp, "bq": broker_qty, "id": str(pos.id)}
                )

        await db.flush()
        return mismatches, corrections

    # ─────────────────────────────────────────
    # ORDER RECONCILIATION
    # ─────────────────────────────────────────
    async def _reconcile_orders(self, db, session, broker_orders: list):
        mismatches  = []
        corrections = []

        broker_map = {o["broker_order_id"]: o for o in broker_orders if o.get("broker_order_id")}
        status_map = {
            "FILLED":           OrderStatus.FILLED.value,
            "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED.value,
            "CANCELLED":        OrderStatus.CANCELLED.value,
            "REJECTED":         OrderStatus.REJECTED.value,
            "PENDING":          OrderStatus.PENDING.value,
        }
        terminal = {"FILLED", "CANCELLED", "REJECTED", "EXPIRED", "RISK_REJECTED"}

        result = await db.execute(
            text(
                "SELECT id, broker_order_id, status, status_history, filled_quantity "
                "FROM orders WHERE session_id=:sid AND status NOT IN :terminal"
            ),
            {"sid": str(session.id), "terminal": tuple(terminal)}
        )
        for order in result.fetchall():
            if not order.broker_order_id:
                continue
            broker_order = broker_map.get(order.broker_order_id)
            if not broker_order:
                continue

            broker_status = broker_order["status"]
            target_status = status_map.get(broker_status)

            if target_status and target_status != order.status:
                mismatches.append({
                    "type":         "ORDER_STATUS_MISMATCH",
                    "order_id":     str(order.id),
                    "local_status": order.status,
                    "broker_status": broker_status,
                })
                history = (order.status_history or []) + [{
                    "status": target_status,
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "actor":  "RECONCILIATION",
                }]
                await db.execute(
                    text(
                        "UPDATE orders SET status=:s, filled_quantity=:fq, "
                        "avg_fill_price=:ap, status_history=:h::jsonb, updated_at=NOW() "
                        "WHERE id=:id"
                    ),
                    {
                        "s":  target_status,
                        "fq": broker_order["filled_qty"],
                        "ap": broker_order["avg_price"],
                        "h":  str(history).replace("'", '"'),
                        "id": str(order.id),
                    }
                )
                corrections.append({"order_id": str(order.id), "action": f"STATUS→{target_status}"})

        await db.flush()
        return mismatches, corrections

    # ─────────────────────────────────────────
    # CRASH RECOVERY
    # ─────────────────────────────────────────
    async def _recover_orphaned_orders(self, db, session, broker_orders: list):
        corrections = []
        broker_map  = {o["broker_order_id"]: o for o in broker_orders}

        result = await db.execute(
            text(
                "SELECT id, broker_order_id, status, sent_at, created_at, status_history "
                "FROM orders WHERE session_id=:sid AND status IN ('SENDING','ACKNOWLEDGED') "
                "AND (sent_at IS NULL OR sent_at < NOW() - INTERVAL '60 seconds')"
            ),
            {"sid": str(session.id)}
        )
        for order in result.fetchall():
            broker_order = broker_map.get(order.broker_order_id) if order.broker_order_id else None
            resolved = "REJECTED"
            if broker_order:
                sm = {"FILLED": "FILLED", "CANCELLED": "CANCELLED", "REJECTED": "REJECTED", "PENDING": "PENDING"}
                resolved = sm.get(broker_order["status"], "REJECTED")

            history = (order.status_history or []) + [{
                "status": resolved, "time": datetime.now(timezone.utc).isoformat(),
                "actor": "CRASH_RECOVERY",
                "reason": f"Orphan recovery — broker: {broker_order['status'] if broker_order else 'NOT_FOUND'}"
            }]
            await db.execute(
                text(
                    "UPDATE orders SET status=:s, status_history=:h::jsonb, "
                    "reject_reason=:r, updated_at=NOW() WHERE id=:id"
                ),
                {
                    "s":  resolved,
                    "h":  str(history).replace("'", '"'),
                    "r":  "Recovered from orphaned state by reconciliation",
                    "id": str(order.id),
                }
            )
            corrections.append({"order_id": str(order.id), "action": f"ORPHAN→{resolved}"})
            logger.warning("Orphan recovery: order %s → %s", order.id, resolved)

        await db.flush()
        return corrections

    # ─────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────
    async def _increment_failure_count(self, db, session) -> int:
        result = await db.execute(
            text(
                "UPDATE trading_sessions SET "
                "reconcile_failure_count = reconcile_failure_count + 1, "
                "last_reconcile_at=NOW(), last_reconcile_status='FAILED', "
                "updated_at=NOW() "
                "WHERE id=:id RETURNING reconcile_failure_count"
            ),
            {"id": str(session.id)}
        )
        row = result.fetchone()
        return row[0] if row else 1

    async def _get_active_session(self, db: AsyncSession) -> Optional[TradingSession]:
        today  = date.today().isoformat()
        result = await db.execute(
            text("SELECT * FROM trading_sessions WHERE date=:d"),
            {"d": today}
        )
        row = result.fetchone()
        if not row:
            return None
        # Return as object-like for attribute access
        return await db.get(TradingSession, row.id)
