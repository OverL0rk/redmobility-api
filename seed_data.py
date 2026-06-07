import asyncio
import uuid
from passlib.context import CryptContext
from sqlalchemy import select
from database import AsyncSessionLocal
from models import User, UserRole, Category, Asset, AssetImage

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

IMG = lambda slug: f"https://dev-rd.cloud/redmobility/images/categories/{slug}.jpg"

# Categorías: id (slug) DEBE coincidir con Asset.type y con los slugs del frontend
CATEGORIES = [
    ("jet_ski",    "Jet Skis",        "🌊"),
    ("boat",       "Lanchas",         "🛥️"),
    ("atv",        "ATVs / Buggies",  "🏎️"),
    ("motorcycle", "Motocicletas",    "🏍️"),
    ("scooter",    "Scooters",        "🛵"),
    ("car",        "Coches",          "🚗"),
    ("bicycle",    "Bicicletas",      "🚲"),
    ("horse",      "Caballos",        "🐎"),
]

# Activos demo: (id, type, name, brand, model, year, capacity, p_hour, p_day, zone, pickup, included)
ASSETS = [
    ("asset_jet_yamaha", "jet_ski", "Yamaha VX Deluxe 2023", "Yamaha", "VX Deluxe", 2023, 3, 25, 180,
     "Punta Cana", "Playa Bavaro, Punta Cana", ["Chalecos salvavidas", "Combustible lleno", "Instruccion de seguridad"]),
    ("asset_boat_searay", "boat", "Lancha Sea Ray 230", "Sea Ray", "SLX 230", 2022, 8, 70, 450,
     "Bavaro", "Marina Cap Cana", ["Capitan incluido", "Hielera y bebidas", "Equipo de snorkel"]),
    ("asset_atv_honda", "atv", "Honda TRX 420 FourTrax", "Honda", "TRX 420", 2022, 2, 15, 90,
     "Punta Cana", "Macao, Punta Cana", ["Cascos", "Tanque lleno", "Guia de rutas"]),
    ("asset_moto_vstrom", "motorcycle", "Suzuki V-Strom 650", "Suzuki", "V-Strom 650", 2023, 2, 18, 70,
     "Santo Domingo", "Zona Colonial, Santo Domingo", ["Casco", "Guantes", "Seguro basico"]),
    ("asset_scooter_vespa", "scooter", "Vespa Primavera 150", "Vespa", "Primavera 150", 2024, 2, 8, 35,
     "Santo Domingo", "Piantini, Santo Domingo", ["Casco", "Candado", "Tanque lleno"]),
    ("asset_car_wrangler", "car", "Jeep Wrangler Sport", "Jeep", "Wrangler", 2023, 5, 30, 120,
     "Punta Cana", "Aeropuerto PUJ, Punta Cana", ["Aire acondicionado", "GPS", "Kilometraje ilimitado"]),
    ("asset_bike_urbana", "bicycle", "Bicicleta urbana Trek", "Trek", "FX 2", 2023, 1, 5, 20,
     "Cabarete", "Centro de Cabarete", ["Casco", "Candado", "Luces LED"]),
    ("asset_horse_playa", "horse", "Paseo a caballo en la playa", "-", "Criollo", 2020, 1, 35, 60,
     "Punta Cana", "Playa Macao, Punta Cana", ["Guia ecuestre", "Casco", "Fotos del recorrido"]),
]


async def seed():
    async with AsyncSessionLocal() as db:
        # 1. Categorias
        for cid, name, icon in CATEGORIES:
            if not await db.get(Category, cid):
                db.add(Category(id=cid, name=name, icon=icon, is_active=True))
                print(f"  + categoria {cid}")

        # 2. Proveedor demo
        res = await db.execute(select(User).where(User.email == "provider@redmobility.com"))
        provider = res.scalar_one_or_none()
        if not provider:
            provider = User(
                id=f"user_prov{uuid.uuid4().hex[:8]}",
                email="provider@redmobility.com",
                name="Juan el Proveedor",
                hashed_password=pwd_context.hash("Provider2026!"),
                role=UserRole.PROVIDER, verified=True, phone_whatsapp="+18095551234",
            )
            db.add(provider)
            print("  + proveedor demo")
        else:
            provider.role = UserRole.PROVIDER
            provider.verified = True
            provider.hashed_password = pwd_context.hash("Provider2026!")

        # 3. Cliente demo
        res = await db.execute(select(User).where(User.email == "client@redmobility.com"))
        client = res.scalar_one_or_none()
        if not client:
            db.add(User(
                id=f"user_clie{uuid.uuid4().hex[:8]}",
                email="client@redmobility.com",
                name="Pedro el Cliente",
                hashed_password=pwd_context.hash("Client2026!"),
                role=UserRole.CLIENT, verified=True,
                phone_whatsapp="+18095555678", document_id="001-1234567-8",
            ))
            print("  + cliente demo")
        else:
            client.hashed_password = pwd_context.hash("Client2026!")

        await db.commit()
        await db.refresh(provider)

        # 4. Activos demo
        for (aid, atype, name, brand, model, year, cap, ph, pd, zone, pickup, included) in ASSETS:
            if await db.get(Asset, aid):
                continue
            db.add(Asset(
                id=aid, provider_id=provider.id, type=atype, name=name,
                description=f"{name} disponible en {zone}. Reserva verificada y confirmacion inmediata con RedMobility.",
                brand=brand, model=model, year=year, capacity=cap,
                price_per_hour=ph, price_per_day=pd,
                location_zone=zone, pickup_address=pickup,
                what_included=included, is_active=True, rating=4.8,
            ))
            db.add(AssetImage(id=f"img_{aid}", asset_id=aid, url=IMG(atype), is_primary=True))
            print(f"  + activo {aid} ({atype})")

        await db.commit()
        print("[seed] Completado.")


asyncio.run(seed())
