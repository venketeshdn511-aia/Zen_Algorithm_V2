"""
0003_resource_metrics.py

Adds resource_metrics table for persistent process health logging.

Why a DB table and not just logs:
  - Logs are ephemeral. Container restarts wipe them.
  - DB survives restarts — you can query "show me RSS growth over 6 hours"
  - Alerting queries can run directly: SELECT WHERE rss_mb > threshold
  - Leak detection requires time-series data, not grep

Retention: keep 7 days of 1-minute samples = 10,080 rows. Tiny.
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_resource_metrics"
down_revision = "0002_strategy_states"
branch_labels = None
depends_on = None


def upgrade() -> None:

    # ── resource_metrics ──────────────────────────────────────────────────
    op.create_table(
        "resource_metrics",
        sa.Column("id",            sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("recorded_at",   sa.DateTime, nullable=False),

        # Process memory
        sa.Column("rss_mb",        sa.Float, nullable=False),    # Resident set size
        sa.Column("vms_mb",        sa.Float, nullable=False),    # Virtual memory size
        sa.Column("rss_delta_mb",  sa.Float, nullable=True),     # Change from previous sample

        # CPU
        sa.Column("cpu_pct",       sa.Float, nullable=False),    # Process CPU (not system)
        sa.Column("cpu_sys_pct",   sa.Float, nullable=True),     # System-wide CPU

        # SQLAlchemy pool
        sa.Column("pool_checked_out", sa.Integer, nullable=True),
        sa.Column("pool_size",        sa.Integer, nullable=True),
        sa.Column("pool_overflow",    sa.Integer, nullable=True),

        # Open file descriptors (leak indicator)
        sa.Column("open_fds",      sa.Integer, nullable=True),

        # Active asyncio tasks (leak indicator)
        sa.Column("active_tasks",  sa.Integer, nullable=True),

        # Leak flags — set by monitor when it detects sustained growth
        sa.Column("rss_leak_flag", sa.Boolean, server_default="false"),
        sa.Column("fd_leak_flag",  sa.Boolean, server_default="false"),

        # Strategy executor health
        sa.Column("running_strategies", sa.Integer, nullable=True),
        sa.Column("tick_rate_hz",       sa.Float, nullable=True),  # ticks/sec in last interval
    )

    op.create_index("idx_rm_time",      "resource_metrics", ["recorded_at"])
    op.create_index("idx_rm_rss",       "resource_metrics", ["rss_mb"])
    op.create_index("idx_rm_leak_flag", "resource_metrics", ["rss_leak_flag"],
                    postgresql_where="rss_leak_flag = true")

    # Automatic cleanup: delete rows older than 7 days
    # This runs via the monitor worker, not a DB job, to keep it portable.
    # But we add a partial index to make the delete fast.
    op.create_index("idx_rm_cleanup", "resource_metrics", ["recorded_at"],
                    postgresql_where="recorded_at < NOW() - INTERVAL '7 days'")

    # ── resource_alerts ───────────────────────────────────────────────────
    # When monitor detects a threshold breach, it inserts here.
    # Separate from audit_logs for query clarity.
    op.create_table(
        "resource_alerts",
        sa.Column("id",          sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("alerted_at",  sa.DateTime, nullable=False),
        sa.Column("alert_type",  sa.String(40), nullable=False),
        # RSS_GROWTH | FD_LEAK | CPU_SPIKE | POOL_EXHAUSTED | TASK_LEAK
        sa.Column("metric_name", sa.String(40), nullable=False),
        sa.Column("current_val", sa.Float, nullable=False),
        sa.Column("threshold",   sa.Float, nullable=False),
        sa.Column("message",     sa.Text, nullable=False),
        sa.Column("resolved_at", sa.DateTime, nullable=True),
    )
    op.create_index("idx_ra_time",     "resource_alerts", ["alerted_at"])
    op.create_index("idx_ra_type",     "resource_alerts", ["alert_type"])
    op.create_index("idx_ra_open",     "resource_alerts", ["resolved_at"],
                    postgresql_where="resolved_at IS NULL")


def downgrade() -> None:
    for table in ["resource_alerts", "resource_metrics"]:
        op.drop_table(table)
