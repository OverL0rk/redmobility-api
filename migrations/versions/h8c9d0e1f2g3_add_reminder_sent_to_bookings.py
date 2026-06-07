"""add_reminder_sent_to_bookings

Revision ID: h8c9d0e1f2g3
Revises: g7b8c9d0e1f2
Create Date: 2026-05-23

Agrega columna reminder_sent a bookings para evitar que el scheduler
envíe emails de recordatorio duplicados cuando la misma reserva cae en
dos ventanas consecutivas de 30 minutos.
"""
from alembic import op

revision = 'h8c9d0e1f2g3'
down_revision = 'g7b8c9d0e1f2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS reminder_sent BOOLEAN NOT NULL DEFAULT FALSE")


def downgrade() -> None:
    op.drop_column('bookings', 'reminder_sent')
