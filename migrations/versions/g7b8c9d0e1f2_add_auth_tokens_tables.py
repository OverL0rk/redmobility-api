"""add_auth_tokens_tables

Revision ID: g7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-04-29

Crea tablas para tokens de verificación de email y reset de contraseña.
Ambas tablas siguen el mismo patrón que user_sessions:
  - token único con índice
  - expires_at para expiración
  - used (bool) para one-time use
"""
from alembic import op
import sqlalchemy as sa

revision = 'g7b8c9d0e1f2'
down_revision = 'f6a7b8c9d0e1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IF NOT EXISTS: safe on fresh DBs where 0000_create_all_tables already created these tables
    op.execute("""
        CREATE TABLE IF NOT EXISTS password_reset_tokens (
            id VARCHAR NOT NULL,
            user_id VARCHAR NOT NULL REFERENCES users(id),
            token VARCHAR NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            used BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ,
            PRIMARY KEY (id),
            UNIQUE (token)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_password_reset_tokens_token ON password_reset_tokens (token)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS email_verification_tokens (
            id VARCHAR NOT NULL,
            user_id VARCHAR NOT NULL REFERENCES users(id),
            token VARCHAR NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            used BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ,
            PRIMARY KEY (id),
            UNIQUE (token)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_email_verification_tokens_token ON email_verification_tokens (token)")


def downgrade() -> None:
    op.drop_index('ix_email_verification_tokens_token', 'email_verification_tokens')
    op.drop_table('email_verification_tokens')
    op.drop_index('ix_password_reset_tokens_token', 'password_reset_tokens')
    op.drop_table('password_reset_tokens')
