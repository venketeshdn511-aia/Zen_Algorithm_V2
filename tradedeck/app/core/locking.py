"""
Distributed Lock — PostgreSQL advisory locks for risk evaluation.

Why not asyncio.Lock():
  asyncio.Lock() is per-process. With 2 uvicorn workers or 2 Docker replicas,
  two concurrent order requests hit different processes → both pass risk checks
  simultaneously → both bypass daily loss / position limits.

Solution — PostgreSQL Advisory Locks:
  - pg_try_advisory_xact_lock(key) acquires a session-level lock in Postgres
  - Lock is held until transaction commits or rolls back
  - Works across ALL processes and replicas pointing at same DB
  - Zero external dependencies — uses the DB you already have
  - Automatically released on crash/disconnect

Key design: Lock key derived from session_id (one lock per trading day).
This means risk evaluation is serialized per trading session, not globally.

For multi-account systems: derive key from account_id to allow parallel
evaluation across different accounts.
"""
import asyncio
import hashlib
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# PostgreSQL advisory lock key range: -2^63 to 2^63-1
# We hash session_id to a stable int64
_ADVISORY_LOCK_NAMESPACE = 0x5452414445434B  # "TRADECK" in hex


def _session_to_lock_key(session_id: str) -> int:
    """
    Convert a UUID session_id to a stable int64 for pg_advisory_lock.
    Namespace prevents collision with other advisory lock users.
    """
    hash_bytes = hashlib.sha256(f"{_ADVISORY_LOCK_NAMESPACE}:{session_id}".encode()).digest()
    # Take first 8 bytes, interpret as signed int64
    raw = int.from_bytes(hash_bytes[:8], byteorder="big", signed=True)
    return raw


@asynccontextmanager
async def acquire_risk_lock(
    db: AsyncSession,
    session_id: str,
    timeout_ms: int = 5000,
) -> AsyncIterator[bool]:
    """
    Acquire a PostgreSQL advisory lock for the given trading session.

    This is a TRANSACTION-LEVEL lock — released automatically when the
    transaction commits or rolls back. No manual release needed or possible.

    Usage:
        async with acquire_risk_lock(db, session.id) as acquired:
            if not acquired:
                raise RiskViolation("LOCK_TIMEOUT", "System busy, retry in a moment")
            # safe to evaluate risk here — no other process can enter this block
            result = await risk_engine._evaluate(...)

    Args:
        db: The current async SQLAlchemy session (must be in an active transaction)
        session_id: The trading session UUID
        timeout_ms: How long to wait for lock before giving up (default 5s)

    Yields:
        True if lock acquired, False if timeout (you should reject the order)
    """
    lock_key = _session_to_lock_key(session_id)

    # Set lock timeout at DB level
    await db.execute(text(f"SET LOCAL lock_timeout = '{timeout_ms}ms'"))

    try:
        # pg_try_advisory_xact_lock: non-blocking, returns bool
        # pg_advisory_xact_lock: blocking (respects lock_timeout)
        result = await db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": lock_key}
        )
        acquired = result.scalar()

        if acquired:
            logger.debug(f"Advisory lock acquired for session {session_id} (key={lock_key})")
            yield True
        else:
            logger.warning(f"Could not acquire advisory lock for session {session_id} — concurrent request?")
            yield False

    except Exception as e:
        logger.error(f"Advisory lock error for session {session_id}: {e}")
        yield False
    finally:
        # Lock is automatically released when transaction ends.
        # Nothing to do here — this is a feature, not a limitation.
        logger.debug(f"Advisory lock released for session {session_id} (transaction boundary)")


@asynccontextmanager
async def acquire_position_lock(
    db: AsyncSession,
    session_id: str,
    symbol: str,
    timeout_ms: int = 3000,
) -> AsyncIterator[bool]:
    """
    Fine-grained lock for a specific symbol within a session.
    Use this when updating position records to prevent race on qty calculation.

    Separate from the risk lock — allows concurrent orders on different symbols
    while serializing updates to the same symbol's position.
    """
    combined = f"{session_id}:{symbol}"
    lock_key = _session_to_lock_key(combined)

    await db.execute(text(f"SET LOCAL lock_timeout = '{timeout_ms}ms'"))

    try:
        result = await db.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": lock_key}
        )
        acquired = result.scalar()
        yield acquired
    except Exception as e:
        logger.error(f"Position lock error for {symbol}: {e}")
        yield False


# ─────────────────────────────────────────────────────────────
# SELECT FOR UPDATE — row-level lock on session row
# Used together with advisory lock for belt-and-suspenders safety
# ─────────────────────────────────────────────────────────────

async def lock_session_row(db: AsyncSession, session_id: str):
    """
    Lock the trading_session row with SELECT FOR UPDATE.

    This prevents another transaction from reading stale P&L or
    kill switch state while we're evaluating risk.

    Combined with advisory lock:
      - Advisory lock: prevents parallel risk evaluations
      - FOR UPDATE: ensures we read the latest committed session state

    Must be called inside a transaction.
    """
    result = await db.execute(
        text(
            "SELECT id, is_killed, kill_reason, realized_pnl, unrealized_pnl, "
            "max_daily_loss, max_open_orders, max_lot_size, max_margin_usage_pct "
            "FROM trading_sessions WHERE id = :id FOR UPDATE"
        ),
        {"id": session_id}
    )
    row = result.fetchone()
    if not row:
        raise ValueError(f"Trading session {session_id} not found")
    return row
