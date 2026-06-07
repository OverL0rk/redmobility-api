"""create all tables

Revision ID: 0000_create_all_tables
Revises:
Create Date: 2026-05-20

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '0000_create_all_tables'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'users',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('email', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('picture', sa.String(), nullable=True),
        sa.Column('phone_whatsapp', sa.String(), nullable=True),
        sa.Column('document_id', sa.String(), nullable=True),
        sa.Column('role', sa.Enum('client', 'provider', 'admin', name='userrole'), nullable=False),
        sa.Column('verified', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email'),
    )
    op.create_index('ix_users_email', 'users', ['email'])

    op.create_table(
        'assets',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('provider_id', sa.String(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('type', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('brand', sa.String(), nullable=True),
        sa.Column('model', sa.String(), nullable=True),
        sa.Column('year', sa.Integer(), nullable=True),
        sa.Column('capacity', sa.Integer(), nullable=True),
        sa.Column('price_per_hour', sa.Integer(), nullable=True),
        sa.Column('price_per_day', sa.Integer(), nullable=False),
        sa.Column('location_zone', sa.String(), nullable=False),
        sa.Column('pickup_address', sa.String(), nullable=True),
        sa.Column('what_included', sa.JSON(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=True),
        sa.Column('rating', sa.Float(), nullable=True),
        sa.Column('total_rentals', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'asset_images',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('asset_id', sa.String(), sa.ForeignKey('assets.id'), nullable=False),
        sa.Column('url', sa.String(), nullable=False),
        sa.Column('is_primary', sa.Boolean(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'bookings',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('booking_code', sa.String(), nullable=False),
        sa.Column('asset_id', sa.String(), sa.ForeignKey('assets.id'), nullable=False),
        sa.Column('client_id', sa.String(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('provider_id', sa.String(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('start_datetime', sa.DateTime(timezone=True), nullable=False),
        sa.Column('end_hours', sa.Integer(), nullable=False),
        sa.Column('total_price', sa.Integer(), nullable=False),
        sa.Column('platform_commission', sa.Integer(), nullable=False),
        sa.Column('insurance_type', sa.String(), nullable=True),
        sa.Column('insurance_price', sa.Integer(), nullable=True),
        sa.Column('status', sa.Enum('pending', 'confirmed', 'active', 'completed', 'cancelled', 'rejected', name='bookingstatus'), nullable=False),
        sa.Column('client_name', sa.String(), nullable=False),
        sa.Column('client_email', sa.String(), nullable=False),
        sa.Column('client_phone', sa.String(), nullable=False),
        sa.Column('client_document', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('payout_sent', sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('booking_code'),
    )

    op.create_table(
        'payment_transactions',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('booking_id', sa.String(), sa.ForeignKey('bookings.id'), nullable=False),
        sa.Column('session_id', sa.String(), nullable=False),
        sa.Column('amount', sa.Integer(), nullable=False),
        sa.Column('currency', sa.String(), nullable=True),
        sa.Column('method', sa.String(), nullable=True),
        sa.Column('status', sa.String(), nullable=True),
        sa.Column('metadata_info', sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('session_id'),
    )

    op.create_table(
        'user_sessions',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('user_id', sa.String(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('session_token', sa.String(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('session_token'),
    )
    op.create_index('ix_user_sessions_session_token', 'user_sessions', ['session_token'])

    op.create_table(
        'reviews',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('booking_id', sa.String(), sa.ForeignKey('bookings.id'), nullable=False),
        sa.Column('client_id', sa.String(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('asset_id', sa.String(), sa.ForeignKey('assets.id'), nullable=False),
        sa.Column('provider_id', sa.String(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('rating', sa.Integer(), nullable=False),
        sa.Column('comment', sa.Text(), nullable=False),
        sa.Column('status', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'disputes',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('booking_id', sa.String(), sa.ForeignKey('bookings.id'), nullable=False),
        sa.Column('opened_by', sa.String(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('status', sa.String(), nullable=True),
        sa.Column('resolution', sa.Text(), nullable=True),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'password_reset_tokens',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('user_id', sa.String(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('token', sa.String(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token'),
    )
    op.create_index('ix_password_reset_tokens_token', 'password_reset_tokens', ['token'])

    op.create_table(
        'email_verification_tokens',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('user_id', sa.String(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('token', sa.String(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token'),
    )
    op.create_index('ix_email_verification_tokens_token', 'email_verification_tokens', ['token'])

    op.create_table(
        'categories',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('icon', sa.String(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'commission_config',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('global_rate', sa.Numeric(5, 4), nullable=False),
        sa.Column('provider_overrides', sa.JSON(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('commission_config')
    op.drop_table('categories')
    op.drop_index('ix_email_verification_tokens_token', 'email_verification_tokens')
    op.drop_table('email_verification_tokens')
    op.drop_index('ix_password_reset_tokens_token', 'password_reset_tokens')
    op.drop_table('password_reset_tokens')
    op.drop_table('disputes')
    op.drop_table('reviews')
    op.drop_index('ix_user_sessions_session_token', 'user_sessions')
    op.drop_table('user_sessions')
    op.drop_table('payment_transactions')
    op.drop_table('bookings')
    op.drop_table('asset_images')
    op.drop_table('assets')
    op.drop_index('ix_users_email', 'users')
    op.drop_table('users')
    sa.Enum(name='userrole').drop(op.get_bind())
    sa.Enum(name='bookingstatus').drop(op.get_bind())