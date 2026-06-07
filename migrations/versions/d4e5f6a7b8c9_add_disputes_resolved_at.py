"""add_disputes_resolved_at

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-04-12

Changes:
- Dispute.resolved_at: new column TIMESTAMPTZ NULL
  (was defined in models.py but never added to the DB via migration)
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers
revision = 'd4e5f6a7b8c9'
down_revision = 'c3d4e5f6a7b8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IF NOT EXISTS: safe on fresh DBs where 0000_create_all_tables already created this column
    op.execute("ALTER TABLE disputes ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ NULL")


def downgrade() -> None:
    op.drop_column('disputes', 'resolved_at')
