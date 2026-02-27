"""
Circuit Breaker — Persistent, DB-backed implementation.

Why tenacity retry ≠ circuit breaker:
  tenacity: retry X times with backoff → still hammers the failing service
  circuit breaker: after N failures, STOP calling for cooldown_seconds,
                   then probe with one request (HALF_OPEN), recover or reopen.

States:
  CLOSED    → Normal operation. Failures increment counter.
  OPEN      → Service considered down. All calls fail immediately (fast fail).
              No requests sent to Fyers. Returns cached/error response.
  HALF_OPEN → Cooldown expired. One probe request allowed.
              Success → CLOSED. Failure → back to OPEN.

Why persisted in DB:
  If circuit is OPEN and process restarts, in-memory state resets to CLOSED.
  Next request hammers Fyers again → more failures → more restarts → storm.
  DB persistence means: process restart doesn't reset circuit breaker state.

Usage:
    cb = CircuitBreaker(db, service_name="fyers_orders")
    async with cb.call() as allowed:
        if not allowed:
            raise BrokerError("CIRCUIT_OPEN", "Order service temporarily unavailable")
        result = await broker.place_order(...)
"""
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator, Optional

from sqlalchemy import text, update, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import CircuitBreakerState

logger = logging.getLogger(__name__)

# Default thresholds — tune per service
DEFAULT_FAILURE_THRESHOLD = 5      # Trips OPEN after 5 consecutive failures
DEFAULT_COOLDOWN_SECONDS  = 60     # Stay OPEN for 60 seconds
DEFAULT_SUCCESS_THRESHOLD = 2      # HALF_OPEN → CLOSED after 2 successes


class CircuitBreakerOpen(Exception):
    """Raised when a call is attempted while circuit is OPEN."""
    def __init__(self, service: str, retry_after: Optional[datetime] = None):
        self.service = service
        self.retry_after = retry_after
        super().__init__(f"Circuit breaker OPEN for {service}. Retry after {retry_after}")


class CircuitBreaker:

    def __init__(
        self,
        service_name: str,
        failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
        cooldown_seconds:  int = DEFAULT_COOLDOWN_SECONDS,
        success_threshold: int = DEFAULT_SUCCESS_THRESHOLD,
    ):
        self.service_name      = service_name
        self.failure_threshold = failure_threshold
        self.cooldown_seconds  = cooldown_seconds
        self.success_threshold = success_threshold

    async def _get_or_create_state(self, db: AsyncSession) -> CircuitBreakerState:
        try:
            result = await db.execute(
                text("SELECT id FROM circuit_breaker_states WHERE service_name = :name"),
                {"name": self.service_name}
            )
            row = result.fetchone()
            if row:
                state = await db.get(CircuitBreakerState, row.id)
                if state:
                    return state
        except Exception as e:
            logger.error(f"CircuitBreaker DB error for {self.service_name}: {e}")

        # Create initial CLOSED state if not found
        state = CircuitBreakerState(
            service_name=self.service_name,
            state="CLOSED",
            failure_count=0,
            success_count=0
        )
        db.add(state)
        try:
            await db.flush()
            return state
        except IntegrityError:
            await db.rollback()
            # Fetch it again since a concurrent worker created it
            result = await db.execute(
                text("SELECT id FROM circuit_breaker_states WHERE service_name = :name"),
                {"name": self.service_name}
            )
            row = result.fetchone()
            if row:
                fetched = await db.get(CircuitBreakerState, row.id)
                if fetched:
                    return fetched
                    
            # Fallback to a detached object to avoid NoneType errors
            # if the concurrent transaction hasn't committed yet
            return CircuitBreakerState(
                service_name=self.service_name,
                state="CLOSED",
                failure_count=0,
                success_count=0
            )

    @asynccontextmanager
    async def call(self, db: AsyncSession) -> AsyncIterator[bool]:
        """
        Context manager. Yields True if call is allowed, False if circuit is OPEN.

        async with circuit_breaker.call(db) as allowed:
            if not allowed:
                raise CircuitBreakerOpen(self.service_name)
            # make the actual call
            result = await broker.something()
        # report success/failure automatically
        """
        state = await self._get_or_create_state(db)
        now = datetime.now(timezone.utc)
        allowed = False
        success = False

        try:
            if state.state == "CLOSED":
                allowed = True
                yield True
                success = True

            elif state.state == "OPEN":
                # Check if cooldown expired → transition to HALF_OPEN
                if state.next_attempt_at and now >= state.next_attempt_at:
                    logger.info(f"Circuit {self.service_name}: OPEN → HALF_OPEN (cooldown expired)")
                    state.state = "HALF_OPEN"
                    state.success_count = 0
                    await db.flush()
                    allowed = True
                    yield True
                    success = True
                else:
                    logger.warning(f"Circuit {self.service_name} OPEN — fast failing. Retry after {state.next_attempt_at}")
                    yield False
                    return

            elif state.state == "HALF_OPEN":
                allowed = True
                yield True
                success = True

        except Exception as exc:
            success = False
            raise
        finally:
            if allowed:
                await self._record_outcome(db, state, success, now)

    async def _record_outcome(
        self,
        db: AsyncSession,
        state: CircuitBreakerState,
        success: bool,
        now: datetime,
    ) -> None:
        if success:
            if state.state == "HALF_OPEN":
                state.success_count = (state.success_count or 0) + 1
                if state.success_count >= self.success_threshold:
                    # Recovered
                    logger.info(f"Circuit {self.service_name}: HALF_OPEN → CLOSED (recovered)")
                    state.state = "CLOSED"
                    state.failure_count = 0
                    state.success_count = 0
                    state.opened_at = None
                    state.next_attempt_at = None
            elif state.state == "CLOSED":
                # Reset failure count on success
                if (state.failure_count or 0) > 0:
                    state.failure_count = 0
        else:
            state.failure_count = (state.failure_count or 0) + 1
            state.last_failure_at = now

            if state.state == "HALF_OPEN":
                # Probe failed — reopen
                logger.warning(f"Circuit {self.service_name}: HALF_OPEN → OPEN (probe failed)")
                state.state = "OPEN"
                state.opened_at = now
                state.next_attempt_at = now + timedelta(seconds=self.cooldown_seconds)
                state.success_count = 0

            elif state.state == "CLOSED" and state.failure_count >= self.failure_threshold:
                # Trip the breaker
                logger.error(
                    f"Circuit {self.service_name}: CLOSED → OPEN "
                    f"({state.failure_count} failures >= threshold {self.failure_threshold})"
                )
                state.state = "OPEN"
                state.opened_at = now
                state.next_attempt_at = now + timedelta(seconds=self.cooldown_seconds)

        state.updated_at = now
        await db.flush()

    async def get_status(self, db: AsyncSession) -> dict:
        """Return current circuit breaker status for health endpoint."""
        state = await self._get_or_create_state(db)
        return {
            "service":        self.service_name,
            "state":          state.state,
            "failure_count":  state.failure_count,
            "last_failure_at": state.last_failure_at.isoformat() if state.last_failure_at else None,
            "since_utc":    state.opened_at.isoformat() if state.opened_at else None,
            "next_attempt_at": state.next_attempt_at.isoformat() if state.next_attempt_at else None,
        }


# ─────────────────────────────────────────────────────────────
# Pre-configured circuit breakers per Fyers service
# ─────────────────────────────────────────────────────────────

class BrokerCircuitBreakers:
    """Registry of circuit breakers for each Fyers API endpoint group."""

    orders = CircuitBreaker(
        service_name="fyers_orders",
        failure_threshold=3,    # Trip faster for order placement
        cooldown_seconds=30,    # 30s cooldown for orders (market moves fast)
        success_threshold=2,
    )
    quotes = CircuitBreaker(
        service_name="fyers_quotes",
        failure_threshold=5,
        cooldown_seconds=60,
        success_threshold=3,
    )
    funds = CircuitBreaker(
        service_name="fyers_funds",
        failure_threshold=5,
        cooldown_seconds=60,
        success_threshold=2,
    )
    websocket = CircuitBreaker(
        service_name="fyers_websocket",
        failure_threshold=3,
        cooldown_seconds=120,
        success_threshold=1,
    )

    @classmethod
    async def all_statuses(cls, db: AsyncSession) -> list:
        return [
            await cls.orders.get_status(db),
            await cls.quotes.get_status(db),
            await cls.funds.get_status(db),
            await cls.websocket.get_status(db),
        ]
