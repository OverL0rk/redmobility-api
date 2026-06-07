import asyncio
from sqlalchemy import select
from database import AsyncSessionLocal
from models import User

async def main():
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(User))
        for u in res.scalars().all():
            print(f"User: {u.name} | Email: {u.email} | Role: {u.role}")

asyncio.run(main())
