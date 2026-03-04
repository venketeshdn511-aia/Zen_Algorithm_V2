"""
Alembic migration: 0004_add_strategy_details.py

Adds thought_process, stop_loss, and target_price columns to strategy_states.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004_add_strategy_details"
down_revision = "0003_resource_metrics"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column("strategy_states", sa.Column("thought_process", sa.Text(), nullable=True))
    op.add_column("strategy_states", sa.Column("stop_loss", sa.Float(), nullable=True))
    op.add_column("strategy_states", sa.Column("target_price", sa.Float(), nullable=True))

def downgrade() -> None:
    op.drop_column("strategy_states", "target_price")
    op.drop_column("strategy_states", "stop_loss")
    op.drop_column("strategy_states", "thought_process")
