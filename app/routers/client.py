"""
Router: endpoints exclusivos del cliente autenticado.
- GET/POST/DELETE /api/client/favorites   → lista de deseos
- GET             /api/client/notifications → notificaciones in-app
- POST            /api/client/notifications/{id}/seen → marcar como vista
- GET             /api/client/loyalty      → puntos de fidelidad
"""
from typing import Optional
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select, func

from database import AsyncSessionLocal
from models import (
    Asset as AssetModel,
    AssetImage as AssetImageModel,
    Booking as BookingModel,
    Favorite as FavoriteModel,
    Notification as NotificationModel,
)
from ..dependencies import require_auth

router = APIRouter(prefix="/api/client")


# ── Favoritos ─────────────────────────────────────────────────────

@router.get("/favorites")
async def get_favorites(request: Request, authorization: Optional[str] = Header(None)):
    user = await require_auth(request, authorization)
    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(FavoriteModel).where(FavoriteModel.client_id == user.id)
        )
        favs = res.scalars().all()
        # Cargar datos de activo para cada favorito
        asset_ids = [f.asset_id for f in favs]
        assets = {}
        if asset_ids:
            a_res = await db.execute(
                select(AssetModel).where(AssetModel.id.in_(asset_ids))
            )
            for a in a_res.scalars().all():
                assets[a.id] = a
            # Imágenes primarias
            img_res = await db.execute(
                select(AssetImageModel).where(
                    AssetImageModel.asset_id.in_(asset_ids),
                    AssetImageModel.is_primary == True
                )
            )
            imgs = {i.asset_id: i.url for i in img_res.scalars().all()}
        else:
            imgs = {}

    result = []
    for f in favs:
        a = assets.get(f.asset_id)
        if not a:
            continue
        result.append({
            "favorite_id": f.id,
            "asset_id": f.asset_id,
            "name": a.name,
            "type": a.type,
            "price_per_day": a.price_per_day,
            "price_per_hour": a.price_per_hour,
            "location_zone": a.location_zone,
            "rating": a.rating,
            "is_active": a.is_active,
            "image": imgs.get(f.asset_id),
            "saved_at": f.created_at.isoformat() if f.created_at else None,
        })
    return result


class FavoriteCreate(BaseModel):
    asset_id: str

@router.post("/favorites", status_code=201)
async def add_favorite(body: FavoriteCreate, request: Request, authorization: Optional[str] = Header(None)):
    user = await require_auth(request, authorization)
    async with AsyncSessionLocal() as db:
        # Verificar que el activo existe
        asset = await db.get(AssetModel, body.asset_id)
        if not asset:
            raise HTTPException(status_code=404, detail="Activo no encontrado")
        # Evitar duplicados
        existing = await db.execute(
            select(FavoriteModel).where(
                FavoriteModel.client_id == user.id,
                FavoriteModel.asset_id == body.asset_id
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Ya está en tu lista de deseos")
        fav = FavoriteModel(client_id=user.id, asset_id=body.asset_id)
        db.add(fav)
        await db.commit()
        await db.refresh(fav)
    return {"favorite_id": fav.id, "asset_id": fav.asset_id, "message": "Añadido a favoritos"}


@router.delete("/favorites/{asset_id}", status_code=200)
async def remove_favorite(asset_id: str, request: Request, authorization: Optional[str] = Header(None)):
    user = await require_auth(request, authorization)
    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(FavoriteModel).where(
                FavoriteModel.client_id == user.id,
                FavoriteModel.asset_id == asset_id
            )
        )
        fav = res.scalar_one_or_none()
        if not fav:
            raise HTTPException(status_code=404, detail="No está en tu lista de deseos")
        await db.delete(fav)
        await db.commit()
    return {"message": "Eliminado de favoritos"}


# ── Notificaciones ────────────────────────────────────────────────

@router.get("/notifications")
async def get_notifications(request: Request, authorization: Optional[str] = Header(None)):
    user = await require_auth(request, authorization)
    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(NotificationModel)
            .where(NotificationModel.user_id == user.id)
            .order_by(NotificationModel.created_at.desc())
            .limit(50)
        )
        notifs = res.scalars().all()
    return [
        {
            "notification_id": n.id,
            "type": n.type,
            "title": n.title,
            "message": n.message,
            "booking_id": n.booking_id,
            "seen": n.seen,
            "created_at": n.created_at.isoformat() if n.created_at else None,
        }
        for n in notifs
    ]


@router.post("/notifications/{notification_id}/seen")
async def mark_notification_seen(notification_id: str, request: Request, authorization: Optional[str] = Header(None)):
    user = await require_auth(request, authorization)
    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(NotificationModel).where(
                NotificationModel.id == notification_id,
                NotificationModel.user_id == user.id
            )
        )
        notif = res.scalar_one_or_none()
        if not notif:
            raise HTTPException(status_code=404, detail="Notificación no encontrada")
        notif.seen = True
        await db.commit()
    return {"message": "Marcada como vista"}


@router.post("/notifications/seen-all")
async def mark_all_notifications_seen(request: Request, authorization: Optional[str] = Header(None)):
    user = await require_auth(request, authorization)
    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(NotificationModel).where(
                NotificationModel.user_id == user.id,
                NotificationModel.seen == False
            )
        )
        for n in res.scalars().all():
            n.seen = True
        await db.commit()
    return {"message": "Todas marcadas como vistas"}


# ── Programa de fidelidad ─────────────────────────────────────────

POINTS_PER_DOLLAR = 1       # 1 punto por cada $1 gastado
TIER_THRESHOLDS = [
    (0,    "Bronze",   "🥉"),
    (500,  "Silver",   "🥈"),
    (2000, "Gold",     "🥇"),
    (5000, "Platinum", "💎"),
]

@router.get("/loyalty")
async def get_loyalty(request: Request, authorization: Optional[str] = Header(None)):
    user = await require_auth(request, authorization)
    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(BookingModel).where(
                BookingModel.client_id == user.id,
                BookingModel.status == "completed"
            )
        )
        completed = res.scalars().all()

    total_spent   = sum(b.total_price or 0 for b in completed)
    total_points  = int(total_spent * POINTS_PER_DOLLAR)
    total_rentals = len(completed)

    # Determinar tier
    tier_name, tier_icon = "Bronze", "🥉"
    next_tier_points = TIER_THRESHOLDS[1][0]
    for threshold, name, icon in TIER_THRESHOLDS:
        if total_points >= threshold:
            tier_name, tier_icon = name, icon
    # Próximo tier
    next_tier = None
    for i, (threshold, name, icon) in enumerate(TIER_THRESHOLDS):
        if total_points < threshold:
            next_tier = {"name": name, "icon": icon, "points_needed": threshold - total_points, "at_points": threshold}
            break

    return {
        "total_points": total_points,
        "total_spent": round(total_spent, 2),
        "total_rentals": total_rentals,
        "tier": {"name": tier_name, "icon": tier_icon},
        "next_tier": next_tier,
        "points_per_dollar": POINTS_PER_DOLLAR,
    }


# ── Upgrade de rol: cliente → proveedor ───────────────────────────

@router.post("/become-provider")
async def become_provider(request: Request, authorization: Optional[str] = Header(None)):
    """Convierte la cuenta del cliente en proveedor (queda sin verificar:
    debe pasar la verificación de identidad antes de publicar)."""
    from models import User as UserModel, UserRole
    user = await require_auth(request, authorization)
    role_val = user.role.value if hasattr(user.role, "value") else user.role
    if role_val == "provider":
        return {"message": "Ya eres proveedor", "role": "provider"}
    if role_val == "admin":
        raise HTTPException(status_code=400, detail="Un administrador no puede convertirse en proveedor.")
    async with AsyncSessionLocal() as db:
        u = await db.get(UserModel, user.id)
        u.role = UserRole.PROVIDER
        u.verified = False          # requiere verificación de identidad (cédula/pasaporte)
        if hasattr(u, "verification_status"):
            u.verification_status = "none"
        await db.commit()
    return {"message": "Ahora eres proveedor. Verifica tu identidad para publicar.", "role": "provider"}
