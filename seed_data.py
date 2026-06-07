import asyncio
import uuid
from datetime import datetime, timezone
from passlib.context import CryptContext
from database import AsyncSessionLocal
from models import User, UserRole, Category, Asset, AssetImage

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

async def seed():
    async with AsyncSessionLocal() as db:
        # 1. Seed Categories
        categories = [
            Category(id="jetski", name="Jet Ski", icon="🌊", is_active=True),
            Category(id="atv", name="ATV / Quad", icon="🚜", is_active=True),
            Category(id="boat", name="Lancha / Bote", icon="🛥️", is_active=True),
        ]
        for cat in categories:
            existing = await db.get(Category, cat.id)
            if not existing:
                db.add(cat)
                print(f"Added category: {cat.name}")

        # 2. Seed Provider
        provider_email = "provider@redmobility.com"
        from sqlalchemy import select
        res = await db.execute(select(User).where(User.email == provider_email))
        provider = res.scalar_one_or_none()
        
        if not provider:
            provider = User(
                id=f"user_prov{uuid.uuid4().hex[:8]}",
                email=provider_email,
                name="Juan el Proveedor",
                hashed_password=pwd_context.hash("Provider2026!"),
                role=UserRole.PROVIDER,
                verified=True,
                phone_whatsapp="+18095551234",
            )
            db.add(provider)
            print(f"Added provider: {provider.email}")
        else:
            provider.verified = True
            provider.hashed_password = pwd_context.hash("Provider2026!")
            print(f"Provider already exists, updated password and verification status.")

        # 3. Seed Client
        client_email = "client@redmobility.com"
        res = await db.execute(select(User).where(User.email == client_email))
        client = res.scalar_one_or_none()
        
        if not client:
            client = User(
                id=f"user_clie{uuid.uuid4().hex[:8]}",
                email=client_email,
                name="Pedro el Cliente",
                hashed_password=pwd_context.hash("Client2026!"),
                role=UserRole.CLIENT,
                verified=True,
                phone_whatsapp="+18095555678",
                document_id="001-1234567-8",
            )
            db.add(client)
            print(f"Added client: {client.email}")
        else:
            client.hashed_password = pwd_context.hash("Client2026!")
            client.phone_whatsapp = client.phone_whatsapp or "+18095555678"
            client.document_id = client.document_id or "001-1234567-8"
            print(f"Client already exists, updated password.")

        await db.commit()
        await db.refresh(provider)

        # 3.5 Seed Admin
        admin_email = "admin@redmobility.com"
        res = await db.execute(select(User).where(User.email == admin_email))
        admin_user = res.scalar_one_or_none()
        
        if not admin_user:
            admin_user = User(
                id=f"user_admin{uuid.uuid4().hex[:8]}",
                email=admin_email,
                name="Administrador Principal",
                hashed_password=pwd_context.hash("Admin2026!"),
                role=UserRole.ADMIN,
                verified=True,
                phone_whatsapp="+18095559999",
            )
            db.add(admin_user)
            print(f"Added admin: {admin_user.email}")
        else:
            admin_user.role = UserRole.ADMIN
            admin_user.verified = True
            admin_user.hashed_password = pwd_context.hash("Admin2026!")
            print(f"Admin already exists, updated password, role and verification.")
        
        await db.commit()

        # 4. Seed Assets (if none exist for this provider)
        res = await db.execute(select(Asset).where(Asset.provider_id == provider.id))
        existing_assets = res.scalars().all()
        
        if not existing_assets:
            asset1 = Asset(
                id="asset_yamaha_vx",
                provider_id=provider.id,
                type="jetski",
                name="Yamaha VX Deluxe 2023",
                description="Excelente jet ski para disfrutar de las playas de Las Terrenas. Muy económico y rápido.",
                brand="Yamaha",
                model="VX Deluxe",
                year=2023,
                capacity=3,
                price_per_hour=25,
                price_per_day=180,
                location_zone="Las Terrenas",
                pickup_address="Playa Cosón, Las Terrenas",
                what_included=["Chalecos salvavidas", "Combustible lleno", "Instrucciones de seguridad"],
                is_active=True,
                rating=4.8,
            )
            db.add(asset1)
            
            img1 = AssetImage(
                id="img_yamaha1",
                asset_id=asset1.id,
                url="https://images.unsplash.com/photo-1569263979104-865ab7cd8d13?auto=format&fit=crop&w=600&q=80",
                is_primary=True
            )
            db.add(img1)
            
            asset2 = Asset(
                id="asset_atv_honda",
                provider_id=provider.id,
                type="atv",
                name="Honda TRX 420 FourTrax",
                description="Perfecto quad para recorrer las lomas y senderos de Samaná. Fuerza y estabilidad garantizadas.",
                brand="Honda",
                model="TRX 420",
                year=2022,
                capacity=2,
                price_per_hour=15,
                price_per_day=90,
                location_zone="Las Terrenas",
                pickup_address="Calle Principal, Las Terrenas",
                what_included=["Cascos", "Tanque lleno de gasolina", "Mapa de rutas recomendadas"],
                is_active=True,
                rating=4.9,
            )
            db.add(asset2)
            
            img2 = AssetImage(
                id="img_honda1",
                asset_id=asset2.id,
                url="https://images.unsplash.com/photo-1551524559-8af4e6624178?auto=format&fit=crop&w=600&q=80",
                is_primary=True
            )
            db.add(img2)
            
            print("Added sample assets (Yamaha VX and Honda TRX).")
            await db.commit()
        else:
            print("Sample assets already exist.")

asyncio.run(seed())
