"""
Script para crear el usuario administrador de RedMobility.
Corre una sola vez: python crear_admin.py
"""
import asyncio
import os
import uuid
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent / '.env')

from database import AsyncSessionLocal, engine, Base
from models import User, UserRole
from passlib.context import CryptContext
from sqlalchemy import select

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

ADMIN_EMAIL    = "admin@redmobility.com"
ADMIN_PASSWORD = "Admin2026!"
ADMIN_NAME     = "Administrador RedMobility"

async def crear_admin():
    async with AsyncSessionLocal() as db:
        # Verificar si ya existe
        result = await db.execute(select(User).where(User.email == ADMIN_EMAIL))
        existing = result.scalar_one_or_none()

        if existing:
            # Aseguramos que tenga rol admin y verified
            existing.role = UserRole.ADMIN
            existing.verified = True
            existing.hashed_password = pwd_context.hash(ADMIN_PASSWORD)
            await db.commit()
            print(f"✅ Admin ya existía — rol y contraseña actualizados.")
        else:
            admin = User(
                id=f"user_{uuid.uuid4().hex[:12]}",
                email=ADMIN_EMAIL,
                name=ADMIN_NAME,
                hashed_password=pwd_context.hash(ADMIN_PASSWORD),
                role=UserRole.ADMIN,
                verified=True,
                picture=None,
            )
            db.add(admin)
            await db.commit()
            print(f"✅ Admin creado exitosamente.")

        print(f"   Email:      {ADMIN_EMAIL}")
        print(f"   Contraseña: {ADMIN_PASSWORD}")
        print(f"   URL:        http://localhost:3000/login")

asyncio.run(crear_admin())
