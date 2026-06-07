"""DDL idempotente para columnas/tablas nuevas (verificación + media en DB).
Se ejecuta en cada arranque; usa IF NOT EXISTS (PostgreSQL)."""
import asyncio
from sqlalchemy import text
from database import engine

DDL = [
    """CREATE TABLE IF NOT EXISTS media_files (
        id VARCHAR PRIMARY KEY,
        owner_id VARCHAR,
        content_type VARCHAR NOT NULL,
        data BYTEA NOT NULL,
        is_private BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMPTZ DEFAULT now()
    )""",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS document_type VARCHAR",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS document_media_id VARCHAR",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS verification_status VARCHAR NOT NULL DEFAULT 'none'",
    "ALTER TABLE assets ADD COLUMN IF NOT EXISTS review_status VARCHAR NOT NULL DEFAULT 'approved'",
]


async def run():
    async with engine.begin() as conn:
        for stmt in DDL:
            await conn.execute(text(stmt))
    print("[init_extra] columnas/tablas verificadas")


asyncio.run(run())
