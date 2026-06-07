"""add_payout_sent_to_bookings

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-04-15

Agrega columna payout_sent a bookings para evitar que el scheduler
envíe múltiples emails de pago al mismo proveedor (CRIT-02).
"""
from alembic import op
import sqlalchemy as sa

revision = 'e5f6a7b8c9d0'
down_revision = 'd4e5f6a7b8c9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IF NOT EXISTS: safe on fresh DBs where 0000_create_all_tables already created this column
    op.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS payout_sent BOOLEAN NOT NULL DEFAULT FALSE")


def downgrade() -> None:
    op.drop_column('bookings', 'payout_sent')
