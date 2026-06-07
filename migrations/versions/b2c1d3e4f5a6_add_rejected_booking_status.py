"""Add REJECTED to BookingStatus enum

Revision ID: b2c1d3e4f5a6
Revises: a4b0ebdc7665
Create Date: 2026-04-10 12:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'b2c1d3e4f5a6'
down_revision: Union[str, Sequence[str], None] = 'a4b0ebdc7665'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add 'rejected' value to the existing PostgreSQL enum type.
    # IF NOT EXISTS prevents failure if already present (PostgreSQL 9.6+).
    op.execute("ALTER TYPE bookingstatus ADD VALUE IF NOT EXISTS 'rejected'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values.
    # Manual intervention required if rollback is needed.
    pass
