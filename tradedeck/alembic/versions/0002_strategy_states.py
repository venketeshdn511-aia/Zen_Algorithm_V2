"""
Alembic migration: 0002_strategy_states.py

Adds the strategy_states table — the authoritative source of truth
for strategy status. The executor reads this. The UI polls this.
Control endpoints write here. Everything stays consistent.

Also adds:
  - feed_heartbeat table (Redis fallback for WS tick timestamps)
  - strategy_control_log table (audit trail for all control actions)
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_strategy_states"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:

    # ── strategy_states ───────────────────────────────────────────────────
    # One row per strategy. Updated by executor on every cycle.
    # The strategy executor owns writes. Observability layer reads.
    # Control endpoints write intent → executor confirms.
    op.create_table(
        "strategy_states",
        sa.Column("id",              sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("strategy_name",   sa.String(100), nullable=False),
        sa.Column("session_id",      sa.String(36),  nullable=True),

        # Core status — written by executor, read by UI
        sa.Column("status",          sa.String(20), nullable=False, server_default="stopped"),
        # running | paused | error | stopped | starting | stopping

        # Control intent — written by API, consumed by executor
        sa.Column("control_intent",  sa.String(20), nullable=True),
        # pause | resume | stop | null (null = no pending intent)

        # Intent acknowledgement
        sa.Column("intent_set_at",   sa.DateTime, nullable=True),
        sa.Column("intent_acked_at", sa.DateTime, nullable=True),
        sa.Column("intent_actor",    sa.String(100), nullable=True),

        # Live metrics — updated by executor each cycle (typically every tick)
        sa.Column("pnl",             sa.Float, server_default="0"),
        sa.Column("allocated_capital", sa.Float, server_default="0"),
        sa.Column("open_qty",        sa.Integer, server_default="0"),
        sa.Column("avg_entry",       sa.Float, nullable=True),
        sa.Column("ltp",             sa.Float, nullable=True),
        sa.Column("win_rate",        sa.Float, server_default="0"),
        sa.Column("total_trades",    sa.Integer, server_default="0"),
        sa.Column("winning_trades",  sa.Integer, server_default="0"),

        # Risk metrics
        sa.Column("net_delta",       sa.Float, server_default="0"),
        sa.Column("drawdown_pct",    sa.Float, server_default="0"),
        sa.Column("max_dd_pct",      sa.Float, server_default="5"),
        sa.Column("risk_pct",        sa.Float, server_default="0"),
        sa.Column("direction_bias",  sa.String(10), server_default="NEUTRAL"),  # BULL|BEAR|NEUTRAL

        # Signal state
        sa.Column("current_signal",  sa.String(20), nullable=True),   # LONG|SHORT|FLAT|WAITING
        sa.Column("symbol",          sa.String(100), nullable=True),
        sa.Column("strategy_type",   sa.String(50), nullable=True),    # CE_BUY|STRADDLE|etc.

        # Error fields
        sa.Column("error_message",   sa.Text, nullable=True),
        sa.Column("error_trace",     sa.Text, nullable=True),
        sa.Column("error_count",     sa.Integer, server_default="0"),
        sa.Column("last_error_at",   sa.DateTime, nullable=True),
        sa.Column("last_good_at",    sa.DateTime, nullable=True),  # last successful trade
        sa.Column("restart_count",   sa.Integer, server_default="0"),
        sa.Column("auto_restart",    sa.Boolean, server_default="true"),  # disabled after 5 failures

        # Timing
        sa.Column("last_trade_at",   sa.DateTime, nullable=True),
        sa.Column("last_tick_at",    sa.DateTime, nullable=True),  # executor heartbeat
        sa.Column("started_at",      sa.DateTime, nullable=True),
        sa.Column("created_at",      sa.DateTime, server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at",      sa.DateTime, server_default=sa.text("NOW()"), nullable=False),

        sa.UniqueConstraint("strategy_name", name="uq_strategy_name"),
    )
    op.create_index("idx_ss_status",   "strategy_states", ["status"])
    op.create_index("idx_ss_session",  "strategy_states", ["session_id"])
    op.create_index("idx_ss_intent",   "strategy_states", ["control_intent"],
                    postgresql_where="control_intent IS NOT NULL")
    op.create_index("idx_ss_name",     "strategy_states", ["strategy_name"])

    # updated_at trigger
    op.execute("""
        CREATE TRIGGER strategy_states_updated_at
        BEFORE UPDATE ON strategy_states
        FOR EACH ROW EXECUTE FUNCTION update_updated_at();
    """)

    # ── strategy_control_log ──────────────────────────────────────────────
    # Append-only audit trail for all control actions.
    # Separate from audit_logs for query performance.
    op.create_table(
        "strategy_control_log",
        sa.Column("id",            sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("strategy_name", sa.String(100), nullable=False),
        sa.Column("action",        sa.String(20), nullable=False),   # pause|resume|stop|start
        sa.Column("actor",         sa.String(100), nullable=False),
        sa.Column("ip_address",    sa.String(45), nullable=True),
        sa.Column("from_status",   sa.String(20), nullable=True),
        sa.Column("to_status",     sa.String(20), nullable=True),
        sa.Column("acked_at",      sa.DateTime, nullable=True),      # when executor confirmed
        sa.Column("ack_latency_ms", sa.Integer, nullable=True),
        sa.Column("notes",         sa.Text, nullable=True),
        sa.Column("created_at",    sa.DateTime, server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_index("idx_scl_strategy", "strategy_control_log", ["strategy_name"])
    op.create_index("idx_scl_time",     "strategy_control_log", ["created_at"])

    # Prevent tampering — same as audit_logs
    op.execute("""
        CREATE OR REPLACE FUNCTION prevent_control_log_modification()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'strategy_control_log is append-only.';
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER scl_immutable
        BEFORE UPDATE OR DELETE ON strategy_control_log
        FOR EACH ROW EXECUTE FUNCTION prevent_control_log_modification();
    """)

    # ── feed_heartbeat ────────────────────────────────────────────────────
    # Fallback when Redis is unavailable. Executor writes last tick time here.
    # Observability reads from Redis first, falls back to this table.
    op.create_table(
        "feed_heartbeat",
        sa.Column("id",           sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("feed_name",    sa.String(50), nullable=False),   # "fyers_ws" | "fyers_rest"
        sa.Column("last_tick_at", sa.DateTime, nullable=False),
        sa.Column("symbols_count", sa.Integer, server_default="0"),
        sa.Column("is_connected", sa.Boolean, server_default="false"),
        sa.Column("updated_at",   sa.DateTime, server_default=sa.text("NOW()"), nullable=False),
        sa.UniqueConstraint("feed_name", name="uq_feed_name"),
    )
    op.execute("""
        CREATE TRIGGER feed_heartbeat_updated_at
        BEFORE UPDATE ON feed_heartbeat
        FOR EACH ROW EXECUTE FUNCTION update_updated_at();
    """)

    # Seed initial rows for known feeds
    op.execute("""
        INSERT INTO feed_heartbeat (feed_name, last_tick_at, is_connected)
        VALUES ('fyers_ws', NOW(), false), ('fyers_rest', NOW(), false)
        ON CONFLICT DO NOTHING;
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS scl_immutable ON strategy_control_log")
    op.execute("DROP FUNCTION IF EXISTS prevent_control_log_modification()")
    op.execute("DROP TRIGGER IF EXISTS strategy_states_updated_at ON strategy_states")
    op.execute("DROP TRIGGER IF EXISTS feed_heartbeat_updated_at ON feed_heartbeat")
    for table in ["strategy_control_log", "strategy_states", "feed_heartbeat"]:
        op.drop_table(table)
