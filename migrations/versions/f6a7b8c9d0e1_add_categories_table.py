"""add_categories_table

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-04-17

Crea tabla categories para gestión dinámica de tipos de activo por el admin.
El campo Asset.type sigue siendo un String; categories.id es el slug que valida.
"""
from alembic import op
import sqlalchemy as sa

revision = 'f6a7b8c9d0e1'
down_revision = 'e5f6a7b8c9d0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IF NOT EXISTS: safe on fresh DBs where 0000_create_all_tables already created this table
    op.execute("""
        CREATE TABLE IF NOT EXISTS categories (
            id VARCHAR NOT NULL,
            name VARCHAR NOT NULL,
            icon VARCHAR,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ,
            PRIMARY KEY (id)
        )
    """)


def downgrade() -> None:
    op.drop_table('categories')
