"""
Initial migration — creates all tables with constraints.

Critical: Includes DB trigger to make audit_logs INSERT-only.
No application code can bypass this — it's enforced at DB level.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── trading_sessions ──────────────────────────────────────
    op.create_table(
        "trading_sessions",
        sa.Column("id",           sa.String(36), primary_key=True),
        sa.Column("date",         sa.String(10), nullable=False),
        sa.Column("is_killed",    sa.Boolean, default=False, nullable=False),
        sa.Column("kill_reason",  sa.String(30), nullable=True),
        sa.Column("kill_time",    sa.DateTime, nullable=True),
        sa.Column("killed_by",    sa.String(100), nullable=True),
        sa.Column("max_daily_loss",       sa.Float, nullable=False, server_default="10000"),
        sa.Column("max_position_size",    sa.Integer, nullable=False, server_default="100"),
        sa.Column("max_open_orders",      sa.Integer, nullable=False, server_default="10"),
        sa.Column("max_margin_usage_pct", sa.Float, nullable=False, server_default="80"),
        sa.Column("max_lot_size",         sa.Integer, nullable=False, server_default="5"),
        sa.Column("realized_pnl",         sa.Float, nullable=False, server_default="0"),
        sa.Column("unrealized_pnl",       sa.Float, nullable=False, server_default="0"),
        sa.Column("total_orders",         sa.Integer, nullable=False, server_default="0"),
        sa.Column("rejected_orders",      sa.Integer, nullable=False, server_default="0"),
        sa.Column("reconcile_failure_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_reconcile_at",    sa.DateTime, nullable=True),
        sa.Column("last_reconcile_status", sa.String(20), server_default="PENDING"),
        sa.Column("created_at", sa.DateTime, server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime, server_default=sa.text("NOW()"), nullable=False),
        sa.UniqueConstraint("date", name="uq_session_date"),
    )
    op.create_index("idx_session_date", "trading_sessions", ["date"])

    # ── orders ────────────────────────────────────────────────
    op.create_table(
        "orders",
        sa.Column("id",               sa.String(36), primary_key=True),
        sa.Column("session_id",       sa.String(36), sa.ForeignKey("trading_sessions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("idempotency_key",  sa.String(64), nullable=False),
        sa.Column("symbol",           sa.String(100), nullable=False),
        sa.Column("display_symbol",   sa.String(100), nullable=False),
        sa.Column("side",             sa.String(10), nullable=False),
        sa.Column("order_type",       sa.String(10), nullable=False),
        sa.Column("product_type",     sa.String(10), nullable=False),
        sa.Column("quantity",         sa.Integer, nullable=False),
        sa.Column("price",            sa.Float, nullable=True),
        sa.Column("trigger_price",    sa.Float, nullable=True),
        sa.Column("validity",         sa.String(10), server_default="DAY", nullable=False),
        sa.Column("status",           sa.String(20), nullable=False, server_default="CREATED"),
        sa.Column("status_history",   postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("broker_order_id",  sa.String(100), nullable=True),
        sa.Column("filled_quantity",  sa.Integer, nullable=False, server_default="0"),
        sa.Column("avg_fill_price",   sa.Float, nullable=True),
        sa.Column("fill_timestamp",   sa.DateTime, nullable=True),
        sa.Column("risk_snapshot",    postgresql.JSONB, nullable=True),
        sa.Column("margin_blocked",   sa.Float, nullable=True),
        sa.Column("reject_reason",    sa.Text, nullable=True),
        sa.Column("broker_reject_code", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime, server_default=sa.text("NOW()"), nullable=False),
        sa.Column("sent_at",    sa.DateTime, nullable=True),
        sa.Column("acked_at",   sa.DateTime, nullable=True),
        # THE critical DB-level idempotency constraint
        sa.UniqueConstraint("idempotency_key", name="uq_order_idempotency"),
        sa.UniqueConstraint("broker_order_id", name="uq_order_broker_id"),
    )
    op.create_index("idx_order_status",  "orders", ["status"])
    op.create_index("idx_order_symbol",  "orders", ["symbol"])
    op.create_index("idx_order_session", "orders", ["session_id"])
    op.create_index("idx_order_broker",  "orders", ["broker_order_id"])

    # ── positions ─────────────────────────────────────────────
    op.create_table(
        "positions",
        sa.Column("id",             sa.String(36), primary_key=True),
        sa.Column("session_id",     sa.String(36), sa.ForeignKey("trading_sessions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("symbol",         sa.String(100), nullable=False),
        sa.Column("display_symbol", sa.String(100), nullable=False),
        sa.Column("product_type",   sa.String(10), nullable=False),
        sa.Column("net_quantity",   sa.Integer, nullable=False, server_default="0"),
        sa.Column("buy_quantity",   sa.Integer, nullable=False, server_default="0"),
        sa.Column("sell_quantity",  sa.Integer, nullable=False, server_default="0"),
        sa.Column("avg_buy_price",  sa.Float, nullable=False, server_default="0"),
        sa.Column("avg_sell_price", sa.Float, nullable=False, server_default="0"),
        sa.Column("ltp",            sa.Float, nullable=True),
        sa.Column("unrealized_pnl", sa.Float, nullable=False, server_default="0"),
        sa.Column("realized_pnl",   sa.Float, nullable=False, server_default="0"),
        sa.Column("broker_quantity",    sa.Integer, nullable=True),
        sa.Column("reconcile_status",   sa.String(20), server_default="PENDING"),
        sa.Column("last_reconciled_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime, server_default=sa.text("NOW()"), nullable=False),
        sa.UniqueConstraint("session_id", "symbol", "product_type", name="uq_position_session_symbol_product"),
    )
    op.create_index("idx_position_session", "positions", ["session_id"])

    # ── pnl_records ───────────────────────────────────────────
    op.create_table(
        "pnl_records",
        sa.Column("id",          sa.String(36), primary_key=True),
        sa.Column("order_id",    sa.String(36), sa.ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("symbol",      sa.String(100), nullable=False),
        sa.Column("pnl_type",    sa.String(20), nullable=False),
        sa.Column("amount",      sa.Float, nullable=False),
        sa.Column("recorded_at", sa.DateTime, server_default=sa.text("NOW()"), nullable=False),
    )

    # ── audit_logs ────────────────────────────────────────────
    op.create_table(
        "audit_logs",
        sa.Column("id",          sa.String(36), primary_key=True),
        sa.Column("session_id",  sa.String(36), nullable=True),
        sa.Column("event_type",  sa.String(60), nullable=False),
        sa.Column("entity_type", sa.String(30), nullable=True),
        sa.Column("entity_id",   sa.String(100), nullable=True),
        sa.Column("actor",       sa.String(100), nullable=True),
        sa.Column("ip_address",  sa.String(45), nullable=True),
        sa.Column("payload",     postgresql.JSONB, nullable=True),
        sa.Column("created_at",  sa.DateTime, server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_index("idx_audit_event",   "audit_logs", ["event_type"])
    op.create_index("idx_audit_entity",  "audit_logs", ["entity_type", "entity_id"])
    op.create_index("idx_audit_time",    "audit_logs", ["created_at"])

    # ── AUDIT LOG TAMPER-PROOF TRIGGER ────────────────────────
    # Prevents any UPDATE or DELETE on audit_logs at DB level.
    # Even a compromised application cannot modify audit records.
    op.execute("""
        CREATE OR REPLACE FUNCTION prevent_audit_modification()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'audit_logs is append-only. UPDATE and DELETE are not permitted.';
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER audit_log_immutable
        BEFORE UPDATE OR DELETE ON audit_logs
        FOR EACH ROW EXECUTE FUNCTION prevent_audit_modification();
    """)

    # ── circuit_breaker_states ────────────────────────────────
    op.create_table(
        "circuit_breaker_states",
        sa.Column("id",              sa.String(36), primary_key=True),
        sa.Column("service_name",    sa.String(50), nullable=False),
        sa.Column("state",           sa.String(20), server_default="CLOSED", nullable=False),
        sa.Column("failure_count",   sa.Integer, server_default="0", nullable=False),
        sa.Column("success_count",   sa.Integer, server_default="0", nullable=False),
        sa.Column("last_failure_at", sa.DateTime, nullable=True),
        sa.Column("opened_at",       sa.DateTime, nullable=True),
        sa.Column("next_attempt_at", sa.DateTime, nullable=True),
        sa.Column("updated_at",      sa.DateTime, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("service_name", name="uq_cb_service"),
    )
    op.create_index("idx_cb_service", "circuit_breaker_states", ["service_name"])

    # ── reconciliation_logs ───────────────────────────────────
    op.create_table(
        "reconciliation_logs",
        sa.Column("id",                 sa.String(36), primary_key=True),
        sa.Column("run_at",             sa.DateTime, server_default=sa.text("NOW()"), nullable=False),
        sa.Column("status",             sa.String(20), nullable=False),
        sa.Column("positions_checked",  sa.Integer, server_default="0"),
        sa.Column("orders_checked",     sa.Integer, server_default="0"),
        sa.Column("mismatches",         postgresql.JSONB, server_default="[]"),
        sa.Column("corrections",        postgresql.JSONB, server_default="[]"),
        sa.Column("error_message",      sa.Text, nullable=True),
        sa.Column("duration_ms",        sa.Integer, nullable=True),
    )
    op.create_index("idx_recon_time", "reconciliation_logs", ["run_at"])

    # ── updated_at triggers ───────────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION update_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    for table in ["trading_sessions", "orders", "positions", "circuit_breaker_states"]:
        op.execute(f"""
            CREATE TRIGGER {table}_updated_at
            BEFORE UPDATE ON {table}
            FOR EACH ROW EXECUTE FUNCTION update_updated_at();
        """)


def downgrade() -> None:
    for table in ["trading_sessions", "orders", "positions", "circuit_breaker_states"]:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_updated_at ON {table}")
    op.execute("DROP TRIGGER IF EXISTS audit_log_immutable ON audit_logs")
    op.execute("DROP FUNCTION IF EXISTS prevent_audit_modification()")
    op.execute("DROP FUNCTION IF EXISTS update_updated_at()")

    for table in ["reconciliation_logs", "circuit_breaker_states", "audit_logs",
                  "pnl_records", "positions", "orders", "trading_sessions"]:
        op.drop_table(table)
