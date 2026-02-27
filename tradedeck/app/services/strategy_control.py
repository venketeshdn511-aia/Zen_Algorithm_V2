"""
app/services/strategy_control.py

Intent/Acknowledge pattern for strategy control.

Why this pattern:
  Direct approach: API → sets status="paused" → returns 200
  Problem: Executor is mid-tick. It reads status AFTER the tick completes.
           During that window, it keeps running. If it places an order in
           that window, the UI thinks it's paused but the order went through.

  Intent/Ack approach:
    1. API writes control_intent="pause" to strategy_states
    2. API waits up to ACK_TIMEOUT_S for executor to set intent=NULL + status="paused"
    3. If executor acks → return confirmed state
    4. If timeout → return PENDING (UI can poll for resolution)
    5. Executor polls for intents at start of every tick cycle — sees intent,
       finishes current tick safely, then transitions status + clears intent

This means:
  - No mid-tick state corruption
  - Executor always finishes its atomic unit of work before stopping
  - Control log records exact timing of intent → ack
  - Timeout doesn't leave a stuck intent — executor will still consume it

For the single-process case: executor runs as asyncio task in same process.
For the distributed case: executor on separate process/container polls the
same DB table. Zero code change needed — the intent table is the contract.
"""
import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import AuditLog

logger = logging.getLogger(__name__)

ACK_TIMEOUT_S   = 10    # Max wait for executor ack
ACK_POLL_MS     = 200   # How often to check for ack
VALID_INTENTS   = {"pause", "resume", "stop", "start"}
VALID_STATUSES  = {"running", "paused", "stopped", "error", "starting", "stopping"}

# Intent → expected resulting status after executor acks
INTENT_RESULT = {
    "pause":  "paused",
    "resume": "running",
    "stop":   "stopped",
    "start":  "running",
}


class StrategyControlError(Exception):
    def __init__(self, code: str, message: str):
        self.code    = code
        self.message = message
        super().__init__(message)


class StrategyControlService:

    async def send_intent(
        self,
        db: AsyncSession,
        strategy_name: str,
        intent: str,
        actor: str,
        ip_address: Optional[str] = None,
        wait_for_ack: bool = True,
    ) -> dict:
        """
        Send a control intent and optionally wait for executor acknowledgement.

        Returns:
            {
                "success": bool,
                "strategy": str,
                "action": intent,
                "status": "confirmed" | "pending" | "error",
                "current_status": str,
                "ack_latency_ms": int | None,
                "message": str
            }
        """
        if intent not in VALID_INTENTS:
            raise StrategyControlError("INVALID_INTENT", f"Unknown intent: {intent}")

        # ── 1. Read current state ──────────────────────────────────────────
        row = await self._get_strategy(db, strategy_name)
        if not row:
            raise StrategyControlError("NOT_FOUND", f"Strategy '{strategy_name}' not found")

        from_status = row.status

        # Guard: don't pause something already paused, etc.
        self._validate_transition(from_status, intent)

        # Guard: don't accept new intent if one is already pending
        if row.control_intent is not None:
            raise StrategyControlError(
                "INTENT_PENDING",
                f"Strategy has unacknowledged intent '{row.control_intent}'. Wait for executor to ack."
            )

        intent_set_at = datetime.now(timezone.utc)

        # ── 2. Write intent atomically ────────────────────────────────────
        await db.execute(
            text(
                "UPDATE strategy_states SET "
                "  control_intent=:intent, intent_set_at=:ts, "
                "  intent_actor=:actor, intent_acked_at=NULL, updated_at=CURRENT_TIMESTAMP "
                "WHERE strategy_name=:name AND control_intent IS NULL"
            ),
            {
                "intent": intent,
                "ts":     intent_set_at,
                "actor":  actor,
                "name":   strategy_name,
            }
        )
        # Flush to ensure change is visible for next check
        await db.flush()
        
        # Verify if update succeeded (manual check instead of RETURNING)
        row_check = await self._get_strategy(db, strategy_name)
        if not row_check or row_check.control_intent != intent:
            # Race: another request set an intent between our read and write
            raise StrategyControlError(
                "INTENT_RACE",
                "Another control command is pending. Retry in a moment."
            )

        # ── 3. Write control log (append-only) ────────────────────────────
        await self._log_control_action(
            db, strategy_name, intent, actor, ip_address, from_status
        )

        await db.commit()
        logger.info(
            f"Control intent '{intent}' set for '{strategy_name}' "
            f"by {actor}. Waiting for executor ack..."
        )

        # ── 4. Wait for ack (or return pending) ───────────────────────────
        if not wait_for_ack:
            return {
                "success": True,
                "strategy": strategy_name,
                "action": intent,
                "status": "pending",
                "current_status": from_status,
                "ack_latency_ms": None,
                "message": f"Intent '{intent}' queued. Executor will process at next tick.",
            }

        ack_result = await self._wait_for_ack(db, strategy_name, intent, intent_set_at)

        if ack_result["acked"]:
            ack_ms = ack_result["latency_ms"]
            # Update control log with ack timing
            # Dialect agnostic way to update the last record
            await db.execute(
                text(
                    "UPDATE strategy_control_log SET acked_at=CURRENT_TIMESTAMP, ack_latency_ms=:ms "
                    "WHERE id IN (SELECT id FROM strategy_control_log "
                    "             WHERE strategy_name=:name AND action=:action AND acked_at IS NULL "
                    "             ORDER BY created_at DESC LIMIT 1)"
                ),
                {"ms": ack_ms, "name": strategy_name, "action": intent}
            )
            await db.commit()
            logger.info(f"Executor acked '{intent}' for '{strategy_name}' in {ack_ms}ms")
            return {
                "success": True,
                "strategy": strategy_name,
                "action": intent,
                "status": "confirmed",
                "current_status": ack_result["final_status"],
                "ack_latency_ms": ack_ms,
                "message": f"Strategy {intent}d successfully. Executor confirmed.",
            }
        else:
            logger.warning(
                f"Executor did not ack '{intent}' for '{strategy_name}' "
                f"within {ACK_TIMEOUT_S}s. Intent remains pending."
            )
            return {
                "success": False,
                "strategy": strategy_name,
                "action": intent,
                "status": "pending",
                "current_status": from_status,
                "ack_latency_ms": None,
                "message": (
                    f"Executor did not confirm within {ACK_TIMEOUT_S}s. "
                    f"Intent is still queued — executor will process it at next tick. "
                    f"Poll /api/v1/observe/strategies to check final state."
                ),
            }

    async def _wait_for_ack(
        self,
        db: AsyncSession,
        strategy_name: str,
        intent: str,
        intent_set_at: datetime,
    ) -> dict:
        """Poll DB until executor clears the intent or timeout."""
        deadline = datetime.now(timezone.utc) + timedelta(seconds=ACK_TIMEOUT_S)
        expected_status = INTENT_RESULT[intent]

        while datetime.now(timezone.utc) < deadline:
            await asyncio.sleep(ACK_POLL_MS / 1000)

            row = await self._get_strategy(db, strategy_name)
            if not row:
                return {"acked": False, "latency_ms": None, "final_status": None}

            # Executor acks by clearing intent and updating status
            if row.control_intent is None and row.status == expected_status:
                if row.intent_acked_at and row.intent_acked_at >= intent_set_at:
                    latency_ms = round(
                        (row.intent_acked_at - intent_set_at).total_seconds() * 1000
                    )
                    return {
                        "acked": True,
                        "latency_ms": latency_ms,
                        "final_status": row.status,
                    }

        return {"acked": False, "latency_ms": None, "final_status": None}

    async def _get_strategy(self, db: AsyncSession, name: str):
        result = await db.execute(
            text(
                "SELECT id, status, control_intent, intent_set_at, intent_acked_at "
                "FROM strategy_states WHERE strategy_name=:name"
            ),
            {"name": name}
        )
        return result.fetchone()

    async def executor_acknowledge_intent(
        self,
        db: AsyncSession,
        strategy_name: str,
        new_status: str,
    ) -> None:
        """
        Called by the strategy executor after it processes an intent.
        This is the ONLY place that clears control_intent.

        Executor flow:
          1. Read intent at start of tick
          2. Finish current tick safely
          3. Apply the transition (stop generating signals, etc.)
          4. Call this method
          5. Intent is cleared, UI gets confirmation
        """
        await db.execute(
            text(
                "UPDATE strategy_states SET "
                "  status=:status, control_intent=NULL, "
                "  intent_acked_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP "
                "WHERE strategy_name=:name AND control_intent IS NOT NULL"
            ),
            {"status": new_status, "name": strategy_name}
        )
        logger.info(f"Executor acked: '{strategy_name}' → {new_status}")

    def _validate_transition(self, current: str, intent: str) -> None:
        """Reject nonsensical transitions early."""
        invalid = {
            ("paused",  "pause"),
            ("stopped", "stop"),
            ("stopped", "pause"),
            ("running", "resume"),
            ("running", "start"),
        }
        if (current, intent) in invalid:
            raise StrategyControlError(
                "INVALID_TRANSITION",
                f"Cannot '{intent}' a strategy that is already '{current}'."
            )

    async def _log_control_action(
        self, db, strategy_name, action, actor, ip, from_status
    ):
        await db.execute(
            text(
                "INSERT INTO strategy_control_log "
                "(strategy_name, action, actor, ip_address, from_status, created_at) "
                "VALUES (:name, :action, :actor, :ip, :from_s, CURRENT_TIMESTAMP)"
            ),
            {
                "name":   strategy_name,
                "action": action,
                "actor":  actor,
                "ip":     ip,
                "from_s": from_status,
            }
        )

    async def get_pending_intents(self, db: AsyncSession) -> list:
        """
        Called by executor at top of every tick to check for pending intents.
        Executor processes these in order of intent_set_at.
        """
        result = await db.execute(
            text(
                "SELECT strategy_name, control_intent, intent_set_at, intent_actor "
                "FROM strategy_states "
                "WHERE control_intent IS NOT NULL "
                "ORDER BY intent_set_at ASC"
            )
        )
        return result.fetchall()
