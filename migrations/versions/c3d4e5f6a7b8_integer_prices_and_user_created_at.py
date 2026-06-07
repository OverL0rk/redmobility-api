"""integer_prices_and_user_created_at

Revision ID: c3d4e5f6a7b8
Revises: b2c1d3e4f5a6
Create Date: 2026-04-11

Changes:
- Asset.price_per_hour / price_per_day: DOUBLE PRECISION → INTEGER (ROUND)
- Booking.total_price / platform_commission / insurance_price: DOUBLE PRECISION → INTEGER (ROUND)
- PaymentTransaction.amount: DOUBLE PRECISION → INTEGER (ROUND)
- User.created_at: new column TIMESTAMPTZ DEFAULT NOW()

Business rule: RedMobility operates in whole USD only — no cents.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers
revision = 'c3d4e5f6a7b8'
down_revision = 'b2c1d3e4f5a6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Idempotent: safe on fresh DBs (0000_create_all_tables already has correct schema)
    # and on pre-0000 DBs that still had Float columns or missing created_at.

    # ── 1. User.created_at ────────────────────────────────────────────────────
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NOW()")
    op.execute("UPDATE users SET created_at = NOW() WHERE created_at IS NULL")

    # ── 2-4. Float → Integer conversions (skipped if already integer) ─────────
    op.execute("""
        DO $$
        DECLARE col_type text;
        BEGIN
            SELECT data_type INTO col_type FROM information_schema.columns
            WHERE table_name='assets' AND column_name='price_per_day';
            IF col_type != 'integer' THEN
                ALTER TABLE assets ALTER COLUMN price_per_day TYPE INTEGER USING ROUND(price_per_day)::INTEGER;
            END IF;

            SELECT data_type INTO col_type FROM information_schema.columns
            WHERE table_name='assets' AND column_name='price_per_hour';
            IF col_type != 'integer' THEN
                ALTER TABLE assets ALTER COLUMN price_per_hour TYPE INTEGER
                    USING CASE WHEN price_per_hour IS NULL THEN NULL ELSE ROUND(price_per_hour)::INTEGER END;
            END IF;

            SELECT data_type INTO col_type FROM information_schema.columns
            WHERE table_name='bookings' AND column_name='total_price';
            IF col_type != 'integer' THEN
                ALTER TABLE bookings ALTER COLUMN total_price TYPE INTEGER USING ROUND(total_price)::INTEGER;
            END IF;

            SELECT data_type INTO col_type FROM information_schema.columns
            WHERE table_name='bookings' AND column_name='platform_commission';
            IF col_type != 'integer' THEN
                ALTER TABLE bookings ALTER COLUMN platform_commission TYPE INTEGER USING ROUND(platform_commission)::INTEGER;
            END IF;

            SELECT data_type INTO col_type FROM information_schema.columns
            WHERE table_name='bookings' AND column_name='insurance_price';
            IF col_type != 'integer' THEN
                ALTER TABLE bookings ALTER COLUMN insurance_price TYPE INTEGER USING ROUND(insurance_price)::INTEGER;
            END IF;

            SELECT data_type INTO col_type FROM information_schema.columns
            WHERE table_name='payment_transactions' AND column_name='amount';
            IF col_type != 'integer' THEN
                ALTER TABLE payment_transactions ALTER COLUMN amount TYPE INTEGER USING ROUND(amount)::INTEGER;
            END IF;
        END $$;
    """)


def downgrade() -> None:
    # Restore Float columns (data precision already lost)
    op.execute("ALTER TABLE assets ALTER COLUMN price_per_day TYPE DOUBLE PRECISION USING price_per_day::DOUBLE PRECISION")
    op.execute("ALTER TABLE assets ALTER COLUMN price_per_hour TYPE DOUBLE PRECISION USING price_per_hour::DOUBLE PRECISION")
    op.execute("ALTER TABLE bookings ALTER COLUMN total_price TYPE DOUBLE PRECISION USING total_price::DOUBLE PRECISION")
    op.execute("ALTER TABLE bookings ALTER COLUMN platform_commission TYPE DOUBLE PRECISION USING platform_commission::DOUBLE PRECISION")
    op.execute("ALTER TABLE bookings ALTER COLUMN insurance_price TYPE DOUBLE PRECISION USING insurance_price::DOUBLE PRECISION")
    op.execute("ALTER TABLE payment_transactions ALTER COLUMN amount TYPE DOUBLE PRECISION USING amount::DOUBLE PRECISION")
    op.drop_column('users', 'created_at')
