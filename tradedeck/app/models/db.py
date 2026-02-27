"""
Database Models — Complete schema with all production constraints.

Design principles:
  - AuditLog is INSERT-only (enforced via DB trigger in migration)
  - All state enums defined here — single source of truth
  - Idempotency key has UNIQUE constraint at DB level (not just app level)
  - broker_order_id indexed for reconciliation lookups
  - All timestamps in UTC
  - JSONB for status_history (indexed, queryable)
"""
import uuid
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    Boolean, Column, DateTime, Enum, Float, ForeignKey,
    Index, Integer, String, Text, UniqueConstraint, event,
    UUID as GenericUUID, JSON
)
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy.sql import func


def gen_uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


# ─────────────────────────────────────────────────────────────
# ENUMS
# ─────────────────────────────────────────────────────────────

class OrderSide(str, PyEnum):
    BUY  = "BUY"
    SELL = "SELL"


class OrderType(str, PyEnum):
    MARKET = "MARKET"
    LIMIT  = "LIMIT"
    SL     = "SL"
    SL_M   = "SL-M"


class ProductType(str, PyEnum):
    MIS  = "MIS"
    NRML = "NRML"


class OrderStatus(str, PyEnum):
    CREATED          = "CREATED"
    RISK_CHECKING    = "RISK_CHECKING"
    RISK_APPROVED    = "RISK_APPROVED"
    RISK_REJECTED    = "RISK_REJECTED"
    SENDING          = "SENDING"
    ACKNOWLEDGED     = "ACKNOWLEDGED"
    PENDING          = "PENDING"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED           = "FILLED"
    CANCELLED        = "CANCELLED"
    REJECTED         = "REJECTED"
    EXPIRED          = "EXPIRED"


class KillSwitchReason(str, PyEnum):
    MANUAL         = "MANUAL"
    DAILY_LOSS     = "DAILY_LOSS_BREACH"
    MARGIN_BREACH  = "MARGIN_BREACH"
    SYSTEM_ERROR   = "SYSTEM_ERROR"
    RECONCILE_FAIL = "RECONCILE_FAIL"


class ReconcileStatus(str, PyEnum):
    OK        = "OK"
    MISMATCH  = "MISMATCH"
    CORRECTED = "CORRECTED"
    PENDING   = "PENDING"


# ─────────────────────────────────────────────────────────────
# TRADING SESSION
# One row per trading day. The authoritative risk + kill state.
# ─────────────────────────────────────────────────────────────

class TradingSession(Base):
    __tablename__ = "trading_sessions"

    id         = Column(GenericUUID(as_uuid=False), primary_key=True, default=gen_uuid)
    date       = Column(String(10), nullable=False)   # YYYY-MM-DD

    # Kill switch — persisted here, never in memory alone
    is_killed  = Column(Boolean, default=False, nullable=False)
    kill_reason = Column(Enum(KillSwitchReason), nullable=True)
    kill_time  = Column(DateTime(timezone=False), nullable=True)
    killed_by  = Column(String(100), nullable=True)

    # Risk limits — snapshotted at session creation, can be updated with audit
    max_daily_loss       = Column(Float,   nullable=False, default=10000.0)
    max_position_size    = Column(Integer, nullable=False, default=100)
    max_open_orders      = Column(Integer, nullable=False, default=10)
    max_margin_usage_pct = Column(Float,   nullable=False, default=80.0)
    max_lot_size         = Column(Integer, nullable=False, default=5)

    # Running P&L — updated atomically after each fill
    realized_pnl   = Column(Float, default=0.0, nullable=False)
    unrealized_pnl = Column(Float, default=0.0, nullable=False)

    # Counters
    total_orders    = Column(Integer, default=0, nullable=False)
    rejected_orders = Column(Integer, default=0, nullable=False)

    # Reconciliation health
    reconcile_failure_count   = Column(Integer, default=0, nullable=False)  # Persisted — survives restart
    last_reconcile_at         = Column(DateTime(timezone=False), nullable=True)
    last_reconcile_status     = Column(String(20), default="PENDING")

    created_at = Column(DateTime(timezone=False), default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=False), default=func.now(), onupdate=func.now(), nullable=False)

    orders     = relationship("Order",     back_populates="session", lazy="dynamic")
    positions  = relationship("Position",  back_populates="session", lazy="dynamic")
    audit_logs = relationship("AuditLog",  back_populates="session", lazy="dynamic")

    __table_args__ = (
        UniqueConstraint("date", name="uq_session_date"),
        Index("idx_session_date", "date"),
    )


# ─────────────────────────────────────────────────────────────
# ORDER — full state machine
# ─────────────────────────────────────────────────────────────

class Order(Base):
    __tablename__ = "orders"

    id               = Column(GenericUUID(as_uuid=False), primary_key=True, default=gen_uuid)
    session_id       = Column(GenericUUID(as_uuid=False), ForeignKey("trading_sessions.id", ondelete="RESTRICT"), nullable=False)

    # DB-level idempotency constraint — no application-layer bypass possible
    idempotency_key  = Column(String(64), nullable=False)

    # Identity
    symbol           = Column(String(100), nullable=False)
    display_symbol   = Column(String(100), nullable=False)
    side             = Column(Enum(OrderSide),    nullable=False)
    order_type       = Column(Enum(OrderType),    nullable=False)
    product_type     = Column(Enum(ProductType),  nullable=False)
    quantity         = Column(Integer, nullable=False)
    price            = Column(Float,   nullable=True)
    trigger_price    = Column(Float,   nullable=True)
    validity         = Column(String(10), default="DAY", nullable=False)

    # State machine — current status
    status           = Column(Enum(OrderStatus), nullable=False, default=OrderStatus.CREATED)

    # Full state history as JSONB — queryable, e.g. find all orders that hit RISK_REJECTED
    # Format: [{"status": "CREATED", "time": "ISO", "actor": "user", "reason": "..."}]
    status_history   = Column(JSON, default=list, nullable=False)

    # Broker integration
    broker_order_id  = Column(String(100), nullable=True)
    filled_quantity  = Column(Integer, default=0, nullable=False)
    avg_fill_price   = Column(Float,   nullable=True)
    fill_timestamp   = Column(DateTime(timezone=False), nullable=True)

    # Risk snapshot at approval time — immutable record of what was checked
    risk_snapshot    = Column(JSON, nullable=True)
    margin_blocked   = Column(Float, nullable=True)

    # Rejection details
    reject_reason    = Column(Text, nullable=True)
    broker_reject_code = Column(String(50), nullable=True)

    # Timestamps
    created_at = Column(DateTime(timezone=False), default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=False), default=func.now(), onupdate=func.now(), nullable=False)
    sent_at    = Column(DateTime(timezone=False), nullable=True)
    acked_at   = Column(DateTime(timezone=False), nullable=True)

    session     = relationship("TradingSession", back_populates="orders")
    pnl_records = relationship("PnLRecord", back_populates="order")

    __table_args__ = (
        # THE critical constraint — DB enforces uniqueness, not just application code
        UniqueConstraint("idempotency_key", name="uq_order_idempotency"),
        # broker_order_id must be unique when set (NULLs allowed for pending)
        UniqueConstraint("broker_order_id", name="uq_order_broker_id"),
        Index("idx_order_status",     "status"),
        Index("idx_order_symbol",     "symbol"),
        Index("idx_order_session",    "session_id"),
        Index("idx_order_broker_id",  "broker_order_id"),
        Index("idx_order_created",    "created_at"),
        # Partial index for active orders only — fast lookup for reconciliation
        Index("idx_order_active", "session_id", "status",
              postgresql_where="status NOT IN ('FILLED','CANCELLED','REJECTED','EXPIRED','RISK_REJECTED')"),
    )


# ─────────────────────────────────────────────────────────────
# POSITION
# ─────────────────────────────────────────────────────────────

class Position(Base):
    __tablename__ = "positions"

    id             = Column(GenericUUID(as_uuid=False), primary_key=True, default=gen_uuid)
    session_id     = Column(GenericUUID(as_uuid=False), ForeignKey("trading_sessions.id", ondelete="RESTRICT"), nullable=False)
    symbol         = Column(String(100), nullable=False)
    display_symbol = Column(String(100), nullable=False)
    product_type   = Column(Enum(ProductType), nullable=False)

    net_quantity   = Column(Integer, default=0,   nullable=False)
    buy_quantity   = Column(Integer, default=0,   nullable=False)
    sell_quantity  = Column(Integer, default=0,   nullable=False)
    avg_buy_price  = Column(Float,   default=0.0, nullable=False)
    avg_sell_price = Column(Float,   default=0.0, nullable=False)

    ltp            = Column(Float, nullable=True)
    unrealized_pnl = Column(Float, default=0.0, nullable=False)
    realized_pnl   = Column(Float, default=0.0, nullable=False)

    # Reconciliation
    broker_quantity     = Column(Integer, nullable=True)
    reconcile_status    = Column(Enum(ReconcileStatus), default=ReconcileStatus.PENDING, nullable=False)
    last_reconciled_at  = Column(DateTime(timezone=False), nullable=True)

    created_at = Column(DateTime(timezone=False), default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=False), default=func.now(), onupdate=func.now(), nullable=False)

    session = relationship("TradingSession", back_populates="positions")

    __table_args__ = (
        UniqueConstraint("session_id", "symbol", "product_type", name="uq_position_session_symbol_product"),
        Index("idx_position_session",    "session_id"),
        Index("idx_position_symbol",     "symbol"),
        Index("idx_position_active",     "session_id", "net_quantity",
              postgresql_where="net_quantity != 0"),
    )


# ─────────────────────────────────────────────────────────────
# PNL RECORD — immutable P&L events
# ─────────────────────────────────────────────────────────────

class PnLRecord(Base):
    __tablename__ = "pnl_records"

    id          = Column(GenericUUID(as_uuid=False), primary_key=True, default=gen_uuid)
    order_id    = Column(GenericUUID(as_uuid=False), ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False)
    symbol      = Column(String(100), nullable=False)
    pnl_type    = Column(String(20),  nullable=False)   # REALIZED | UNREALIZED
    amount      = Column(Float,       nullable=False)
    recorded_at = Column(DateTime(timezone=False), default=func.now(), nullable=False)

    order = relationship("Order", back_populates="pnl_records")

    __table_args__ = (
        Index("idx_pnl_order",   "order_id"),
        Index("idx_pnl_time",    "recorded_at"),
    )


# ─────────────────────────────────────────────────────────────
# AUDIT LOG — append-only, tamper-evident
# DB trigger in migration prevents UPDATE/DELETE on this table.
# ─────────────────────────────────────────────────────────────

class AuditLog(Base):
    __tablename__ = "audit_logs"

    id          = Column(GenericUUID(as_uuid=False), primary_key=True, default=gen_uuid)
    session_id  = Column(GenericUUID(as_uuid=False), ForeignKey("trading_sessions.id"), nullable=True)
    event_type  = Column(String(60),  nullable=False)
    entity_type = Column(String(30),  nullable=True)
    entity_id   = Column(String(100), nullable=True)
    actor       = Column(String(100), nullable=True)
    ip_address  = Column(String(45),  nullable=True)
    payload     = Column(JSON, nullable=True)
    created_at  = Column(DateTime(timezone=False), default=func.now(), nullable=False)

    session = relationship("TradingSession", back_populates="audit_logs")

    __table_args__ = (
        Index("idx_audit_event",  "event_type"),
        Index("idx_audit_entity", "entity_type", "entity_id"),
        Index("idx_audit_time",   "created_at"),
        Index("idx_audit_session","session_id"),
    )


# ─────────────────────────────────────────────────────────────
# CIRCUIT BREAKER STATE — persisted across restarts
# ─────────────────────────────────────────────────────────────

class CircuitBreakerState(Base):
    """
    Persisted circuit breaker state.
    In-memory circuit breakers reset on restart — unacceptable for a trading system.
    """
    __tablename__ = "circuit_breaker_states"

    id             = Column(GenericUUID(as_uuid=False), primary_key=True, default=gen_uuid)
    service_name   = Column(String(50), nullable=False)   # "fyers_orders", "fyers_quotes", etc.
    state          = Column(String(20), default="CLOSED", nullable=False)  # CLOSED, OPEN, HALF_OPEN
    failure_count  = Column(Integer, default=0, nullable=False)
    success_count  = Column(Integer, default=0, nullable=False)  # for half-open recovery
    last_failure_at = Column(DateTime(timezone=False), nullable=True)
    opened_at      = Column(DateTime(timezone=False), nullable=True)
    next_attempt_at = Column(DateTime(timezone=False), nullable=True)
    updated_at     = Column(DateTime(timezone=False), default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("service_name", name="uq_cb_service"),
        Index("idx_cb_service", "service_name"),
    )


# ─────────────────────────────────────────────────────────────
# RECONCILIATION LOG
# ─────────────────────────────────────────────────────────────

class ReconciliationLog(Base):
    __tablename__ = "reconciliation_logs"

    id                  = Column(GenericUUID(as_uuid=False), primary_key=True, default=gen_uuid)
    run_at              = Column(DateTime(timezone=False), default=func.now(), nullable=False)
    status              = Column(String(20), nullable=False)   # OK | MISMATCH | FAILED
    positions_checked   = Column(Integer, default=0)
    orders_checked      = Column(Integer, default=0)
    mismatches          = Column(JSON, default=list)
    corrections         = Column(JSON, default=list)
    error_message       = Column(Text, nullable=True)
    duration_ms         = Column(Integer, nullable=True)

    __table_args__ = (
        Index("idx_recon_time", "run_at"),
    )
# ─────────────────────────────────────────────────────────────
# STRATEGY STATE — authoritative source of truth
# ─────────────────────────────────────────────────────────────

class StrategyState(Base):
    __tablename__ = "strategy_states"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    strategy_name   = Column(String(100), nullable=False, unique=True)
    session_id      = Column(GenericUUID(as_uuid=False), nullable=True)

    status          = Column(String(20), nullable=False, default="stopped")
    control_intent  = Column(String(20), nullable=True)

    intent_set_at   = Column(DateTime, nullable=True)
    intent_acked_at = Column(DateTime, nullable=True)
    intent_actor    = Column(String(100), nullable=True)

    pnl             = Column(Float, default=0.0)
    allocated_capital = Column(Float, default=0.0)
    open_qty        = Column(Integer, default=0)
    avg_entry       = Column(Float, nullable=True)
    ltp             = Column(Float, nullable=True)
    win_rate        = Column(Float, default=0.0)
    total_trades    = Column(Integer, default=0)
    winning_trades  = Column(Integer, default=0)

    net_delta       = Column(Float, default=0.0)
    drawdown_pct    = Column(Float, default=0.0)
    max_dd_pct      = Column(Float, default=5.0)
    risk_pct        = Column(Float, default=0.0)
    direction_bias  = Column(String(10), default="NEUTRAL")

    current_signal  = Column(String(20), nullable=True)
    symbol          = Column(String(100), nullable=True)
    strategy_type   = Column(String(50), nullable=True)

    error_message   = Column(Text, nullable=True)
    error_trace     = Column(Text, nullable=True)
    error_count     = Column(Integer, default=0)
    last_error_at   = Column(DateTime, nullable=True)
    last_good_at    = Column(DateTime, nullable=True)
    restart_count   = Column(Integer, default=0)
    auto_restart    = Column(Boolean, default=True)

    last_trade_at   = Column(DateTime, nullable=True)
    last_tick_at    = Column(DateTime, nullable=True)
    started_at      = Column(DateTime, nullable=True)
    created_at      = Column(DateTime, default=func.now(), nullable=False)
    updated_at      = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("idx_ss_status", "status"),
        Index("idx_ss_name", "strategy_name"),
    )


class StrategyControlLog(Base):
    __tablename__ = "strategy_control_log"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    strategy_name = Column(String(100), nullable=False)
    action        = Column(String(20), nullable=False)
    actor         = Column(String(100), nullable=False)
    ip_address    = Column(String(45), nullable=True)
    from_status   = Column(String(20), nullable=True)
    to_status     = Column(String(20), nullable=True)
    acked_at      = Column(DateTime, nullable=True)
    ack_latency_ms = Column(Integer, nullable=True)
    notes         = Column(Text, nullable=True)
    created_at    = Column(DateTime, default=func.now(), nullable=False)

    __table_args__ = (
        Index("idx_scl_strategy", "strategy_name"),
        Index("idx_scl_time", "created_at"),
    )


# Seed initial row for Known Feeds table exists in migration,
# but we define the model here for completeness if needed.
class FeedHeartbeat(Base):
    __tablename__ = "feed_heartbeat"
    id           = Column(Integer, primary_key=True, autoincrement=True)
    feed_name    = Column(String(50), nullable=False, unique=True)
    last_tick_at = Column(DateTime, nullable=False)
    symbols_count = Column(Integer, default=0)
    is_connected = Column(Boolean, default=False)
    updated_at   = Column(DateTime, default=func.now(), onupdate=func.now())
