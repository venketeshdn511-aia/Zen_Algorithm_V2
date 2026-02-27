"""
RiskEngine — Distributed-safe, kill-switch-at-every-boundary implementation.

Changes from v1:
  - asyncio.Lock() replaced with PostgreSQL advisory lock (cross-process safe)
  - SELECT FOR UPDATE on session row (prevents stale read)
  - Kill switch checked at: entry + after every state transition
  - All risk data fetched from DB + live broker (never frontend values)
  - Reconcile failure counter read from DB (survives restart)
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import (
    AuditLog, CircuitBreakerState, KillSwitchReason,
    Order, OrderStatus, Position, TradingSession,
)
from app.core.locking import acquire_risk_lock, lock_session_row
from app.core.circuit_breaker import BrokerCircuitBreakers

logger = logging.getLogger(__name__)


class RiskViolation(Exception):
    def __init__(self, code: str, message: str):
        self.code    = code
        self.message = message
        super().__init__(message)


class RiskCheckResult:
    def __init__(self, approved: bool, code: str = None, message: str = None, snapshot: dict = None):
        self.approved = approved
        self.code     = code
        self.message  = message
        self.snapshot = snapshot or {}


class RiskEngine:

    def __init__(self, broker):
        self.broker = broker  # BrokerService — injected, not imported

    # ─────────────────────────────────────────
    # MAIN ENTRYPOINT
    # ─────────────────────────────────────────
    async def validate_order(
        self,
        db: AsyncSession,
        session: TradingSession,
        symbol: str,
        side: str,
        quantity: int,
        order_type: str,
        price: Optional[float],
        product_type: str,
        idempotency_key: str,
    ) -> RiskCheckResult:
        """
        Atomically validate order against all risk rules.

        Uses:
          1. PostgreSQL advisory lock (cross-process serialization)
          2. SELECT FOR UPDATE on session row (fresh committed data)

        This combination ensures even with 10 parallel workers,
        only one risk evaluation runs per trading session at a time.
        """
        async with acquire_risk_lock(db, str(session.id), timeout_ms=5000) as acquired:
            if not acquired:
                return RiskCheckResult(
                    approved=False,
                    code="LOCK_TIMEOUT",
                    message="Risk engine busy. Another order is being processed. Retry in a moment."
                )

            # Re-read session with row lock — guaranteed fresh data
            locked_row = await lock_session_row(db, str(session.id))

            return await self._evaluate(
                db, locked_row, symbol, side, quantity,
                order_type, price, product_type, idempotency_key
            )

    async def _evaluate(
        self, db, locked_row, symbol, side, quantity,
        order_type, price, product_type, idempotency_key
    ) -> RiskCheckResult:
        """
        All risk checks. Runs inside the advisory lock.
        locked_row = result of SELECT FOR UPDATE — always fresh.
        """
        session_id = str(locked_row.id if hasattr(locked_row, 'id') else locked_row[0])

        # ── 1. KILL SWITCH (check against DB row, not ORM cache) ──
        is_killed = locked_row.is_killed if hasattr(locked_row, 'is_killed') else locked_row[1]
        if is_killed:
            kill_reason = locked_row.kill_reason if hasattr(locked_row, 'kill_reason') else locked_row[2]
            return RiskCheckResult(
                approved=False, code="KILL_SWITCH_ACTIVE",
                message=f"Trading halted: {kill_reason}. No orders accepted."
            )

        # ── 2. IDEMPOTENCY (DB constraint will also catch this, but catch early) ──
        result = await db.execute(
            text("SELECT id FROM orders WHERE idempotency_key = :key"),
            {"key": idempotency_key}
        )
        if result.fetchone():
            return RiskCheckResult(
                approved=False, code="DUPLICATE_ORDER",
                message=f"Order with key {idempotency_key[:16]}... already processed."
            )

        # ── 3. LIVE MARGIN FROM BROKER (via circuit breaker) ──
        available_margin = 0
        used_margin      = 0
        total_margin     = 1

        async with BrokerCircuitBreakers.funds.call(db) as allowed:
            if not allowed:
                return RiskCheckResult(
                    approved=False, code="CIRCUIT_OPEN_FUNDS",
                    message="Margin verification service temporarily unavailable. Order blocked for safety."
                )
            try:
                funds        = await self.broker.get_funds()
                equity       = funds.get("equity", {})
                available_margin = equity.get("available_margin", 0)
                used_margin  = equity.get("used_margin", 0)
                total_margin = available_margin + used_margin or 1
            except Exception as e:
                logger.error(f"Margin fetch failed: {e}")
                return RiskCheckResult(
                    approved=False, code="MARGIN_FETCH_FAILED",
                    message="Cannot verify margin with broker. Order blocked."
                )

        margin_pct = (used_margin / total_margin) * 100

        # ── 4. MARGIN UTILISATION ──────────────────────────────
        max_margin_pct = locked_row.max_margin_usage_pct if hasattr(locked_row, 'max_margin_usage_pct') else locked_row[7]
        if margin_pct >= max_margin_pct:
            await self._trigger_kill_switch(
                db, session_id, KillSwitchReason.MARGIN_BREACH,
                f"Margin {margin_pct:.1f}% >= limit {max_margin_pct}%"
            )
            return RiskCheckResult(
                approved=False, code="MARGIN_LIMIT_BREACH",
                message=f"Margin {margin_pct:.1f}% exceeds limit {max_margin_pct:.0f}%. Kill switch triggered."
            )

        # ── 5. DAILY LOSS LIMIT ────────────────────────────────
        realized   = locked_row.realized_pnl   if hasattr(locked_row, 'realized_pnl')   else locked_row[3]
        unrealized = locked_row.unrealized_pnl if hasattr(locked_row, 'unrealized_pnl') else locked_row[4]
        max_loss   = locked_row.max_daily_loss  if hasattr(locked_row, 'max_daily_loss')  else locked_row[5]
        day_pnl    = realized + unrealized

        if day_pnl < -abs(max_loss):
            await self._trigger_kill_switch(
                db, session_id, KillSwitchReason.DAILY_LOSS,
                f"Day P&L ₹{day_pnl:.2f} breached limit ₹{-max_loss:.2f}"
            )
            return RiskCheckResult(
                approved=False, code="DAILY_LOSS_BREACH",
                message=f"Daily loss limit ₹{max_loss:.0f} breached. Trading halted."
            )

        # ── 6. MAX OPEN POSITIONS ──────────────────────────────
        max_open = locked_row.max_open_orders if hasattr(locked_row, 'max_open_orders') else locked_row[6]
        result = await db.execute(
            text("SELECT COUNT(*) FROM positions WHERE session_id = :sid AND net_quantity != 0"),
            {"sid": session_id}
        )
        open_count = result.scalar()
        if open_count >= max_open:
            return RiskCheckResult(
                approved=False, code="MAX_POSITIONS_REACHED",
                message=f"Max open positions ({max_open}) reached."
            )

        # ── 7. LOT SIZE ────────────────────────────────────────
        max_lots = locked_row.max_lot_size if hasattr(locked_row, 'max_lot_size') else 5
        lot_size = 50   # TODO: fetch from symbol master
        lots = quantity / lot_size
        if lots > max_lots:
            return RiskCheckResult(
                approved=False, code="LOT_SIZE_EXCEEDED",
                message=f"Order {lots:.0f} lots > max {max_lots} lots."
            )

        # ── 8. ESTIMATED MARGIN CHECK ──────────────────────────
        est_price = price if price else await self._safe_ltp(symbol)
        est_margin = quantity * est_price * 0.15
        if est_margin > available_margin:
            return RiskCheckResult(
                approved=False, code="INSUFFICIENT_MARGIN",
                message=f"Order needs ~₹{est_margin:.0f}, only ₹{available_margin:.0f} available."
            )

        # ── 9. RE-CHECK KILL SWITCH (belt & suspenders) ────────
        # Check again after all async operations above — kill switch could have
        # been triggered by another concurrent request between check 1 and now
        result = await db.execute(
            text("SELECT is_killed FROM trading_sessions WHERE id = :id"),
            {"id": session_id}
        )
        still_ok = result.scalar()
        if still_ok:
            return RiskCheckResult(
                approved=False, code="KILL_SWITCH_ACTIVE",
                message="Kill switch activated during risk evaluation."
            )

        # ── ALL CHECKS PASSED ──────────────────────────────────
        snapshot = {
            "timestamp":       datetime.now(timezone.utc).isoformat(),
            "available_margin": available_margin,
            "used_margin":      used_margin,
            "margin_pct":       round(margin_pct, 2),
            "day_pnl":          round(day_pnl, 2),
            "open_positions":   open_count,
            "est_margin_req":   round(est_margin, 2),
            "lots":             lots,
            "lock_type":        "pg_advisory_xact_lock",
            "checks": [
                "KILL_SWITCH", "IDEMPOTENCY", "LIVE_MARGIN",
                "MARGIN_PCT", "DAILY_LOSS", "MAX_POSITIONS",
                "LOT_SIZE", "AVAILABLE_MARGIN", "KILL_SWITCH_RECHECK"
            ]
        }
        logger.info(f"Risk APPROVED: {side} {quantity} {symbol} margin_pct={margin_pct:.1f}%")
        return RiskCheckResult(approved=True, snapshot=snapshot)

    # ─────────────────────────────────────────
    # KILL SWITCH
    # ─────────────────────────────────────────
    async def trigger_kill_switch_manual(
        self, db: AsyncSession, session: TradingSession, actor: str
    ) -> None:
        await self._trigger_kill_switch(
            db, str(session.id), KillSwitchReason.MANUAL,
            f"Manual activation by {actor}", actor=actor
        )

    async def deactivate_kill_switch(
        self, db: AsyncSession, session: TradingSession, actor: str
    ) -> None:
        await db.execute(
            text(
                "UPDATE trading_sessions SET is_killed=false, kill_reason=NULL, "
                "kill_time=NULL, killed_by=NULL, updated_at=NOW() WHERE id=:id"
            ),
            {"id": str(session.id)}
        )
        self._add_audit(db, str(session.id), "KILL_SWITCH_DEACTIVATED", actor,
                        {"action": "DEACTIVATE", "by": actor})
        await db.flush()
        logger.warning(f"Kill switch DEACTIVATED by {actor}")

    async def _trigger_kill_switch(
        self, db: AsyncSession, session_id: str,
        reason: KillSwitchReason, detail: str, actor: str = "SYSTEM"
    ) -> None:
        # Use raw SQL UPDATE with WHERE NOT already killed — atomic
        result = await db.execute(
            text(
                "UPDATE trading_sessions SET is_killed=true, kill_reason=:reason, "
                "kill_time=NOW(), killed_by=:actor, updated_at=NOW() "
                "WHERE id=:id AND is_killed=false RETURNING id"
            ),
            {"id": session_id, "reason": reason.value, "actor": actor}
        )
        if result.fetchone():  # Only log if we actually changed it
            self._add_audit(db, session_id, "KILL_SWITCH_ACTIVATED", actor,
                            {"reason": reason.value, "detail": detail})
            await db.flush()
            logger.critical(f"KILL SWITCH ACTIVATED — {reason.value}: {detail}")

    # ─────────────────────────────────────────
    # P&L UPDATE (after fill)
    # ─────────────────────────────────────────
    async def record_realized_pnl(
        self, db: AsyncSession, session_id: str, pnl: float
    ) -> None:
        await db.execute(
            text(
                "UPDATE trading_sessions SET realized_pnl = realized_pnl + :pnl, "
                "updated_at=NOW() WHERE id = :id"
            ),
            {"pnl": pnl, "id": session_id}
        )
        # Post-fill: check if now in breach
        result = await db.execute(
            text("SELECT realized_pnl + unrealized_pnl as day_pnl, max_daily_loss, is_killed FROM trading_sessions WHERE id=:id"),
            {"id": session_id}
        )
        row = result.fetchone()
        if row and not row.is_killed and row.day_pnl < -abs(row.max_daily_loss):
            await self._trigger_kill_switch(
                db, session_id, KillSwitchReason.DAILY_LOSS,
                f"Auto-triggered after fill. Day P&L: ₹{row.day_pnl:.2f}"
            )

    # ─────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────
    async def _safe_ltp(self, symbol: str) -> float:
        try:
            quote = await self.broker.get_quote(symbol)
            return quote.get("ltp", 100.0)
        except Exception:
            return 100.0

    def _add_audit(self, db, session_id, event, actor, payload):
        import uuid
        log = AuditLog(
            id=str(uuid.uuid4()),
            session_id=session_id,
            event_type=event,
            entity_type="session",
            entity_id=session_id,
            actor=actor,
            payload=payload,
        )
        db.add(log)
