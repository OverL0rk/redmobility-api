# Migrado desde server.py líneas 658–719, 1223–1265, 1334–1401, 1403–1528, 1597–1742
# 15 endpoints originales + 2 nuevos: admin/bookings, admin/users (BUG-06)
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import stripe
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select

from database import AsyncSessionLocal
from models import (
    Asset as AssetModel,
    Booking as BookingModel,
    Category as CategoryModel,
    CommissionConfig as CommissionConfigModel,
    Dispute as DisputeModel,
    PaymentTransaction as PaymentModel,
    Review as ReviewModel,
    User as UserModel,
    BookingStatus,
    UserRole,
)
from ..dependencies import (
    STRIPE_API_KEY,
    get_commission_rate,
    require_role,
    send_email,
)
from ..utils import booking_to_dict, user_to_dict

router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)

# ── Pydantic schemas (solo usados en este router) ────────────────

class CategoryCreate(BaseModel):
    id: str        # slug: "car", "bicycle" — inmutable una vez creado
    name: str
    icon: Optional[str] = None

class CategoryUpdate(BaseModel):
    name: Optional[str] = None
    icon: Optional[str] = None
    is_active: Optional[bool] = None

class CommissionConfigUpdate(BaseModel):
    global_rate: float

class ProviderOverrideCreate(BaseModel):
    provider_email: str
    rate: float
    expires_at: Optional[str] = None  # ISO date string e.g. "2025-12-31"

class DisputeResolveBody(BaseModel):
    resolution_type: str   # 'client' | 'provider' | 'partial'
    refund_amount: int     # USD enteros
    notes: str

class ProviderRejectBody(BaseModel):
    reason: str

class BanUserBody(BaseModel):
    reason: str

class BookingStatusUpdate(BaseModel):
    status: str  # 'confirmed' | 'cancelled' | 'completed' | 'rejected'
    reason: Optional[str] = None


# ── Categories ───────────────────────────────────────────────────

@router.get("/admin/categories")
async def admin_list_categories(request: Request, authorization: Optional[str] = Header(None)):
    await require_role(request, "admin", authorization)
    async with AsyncSessionLocal() as db:
        cats = (await db.execute(
            select(CategoryModel).order_by(CategoryModel.name)
        )).scalars().all()
        counts = {r[0]: r[1] for r in (await db.execute(
            select(AssetModel.type, func.count(AssetModel.id)).group_by(AssetModel.type)
        )).all()}
    return [
        {"id": c.id, "name": c.name, "icon": c.icon, "is_active": c.is_active,
         "asset_count": counts.get(c.id, 0)}
        for c in cats
    ]


@router.post("/admin/categories", status_code=201)
async def admin_create_category(data: CategoryCreate, request: Request, authorization: Optional[str] = Header(None)):
    await require_role(request, "admin", authorization)
    # Slug: solo letras minúsculas, números y guion bajo
    if not re.match(r'^[a-z0-9_]+$', data.id):
        raise HTTPException(status_code=422, detail="El id solo puede contener letras minúsculas, números y guion bajo.")
    async with AsyncSessionLocal() as db:
        existing = await db.get(CategoryModel, data.id)
        if existing:
            raise HTTPException(status_code=409, detail=f"Ya existe una categoría con id '{data.id}'.")
        cat = CategoryModel(id=data.id, name=data.name, icon=data.icon, is_active=True)
        db.add(cat)
        await db.commit()
        await db.refresh(cat)
    return {"id": cat.id, "name": cat.name, "icon": cat.icon, "is_active": cat.is_active}


@router.patch("/admin/categories/{category_id}")
async def admin_update_category(category_id: str, data: CategoryUpdate, request: Request, authorization: Optional[str] = Header(None)):
    await require_role(request, "admin", authorization)
    async with AsyncSessionLocal() as db:
        cat = await db.get(CategoryModel, category_id)
        if not cat:
            raise HTTPException(status_code=404, detail="Categoría no encontrada.")
        if data.name is not None:
            cat.name = data.name
        if data.icon is not None:
            cat.icon = data.icon
        if data.is_active is not None:
            cat.is_active = data.is_active
        await db.commit()
        await db.refresh(cat)
    return {"id": cat.id, "name": cat.name, "icon": cat.icon, "is_active": cat.is_active}


# ── Dashboard ────────────────────────────────────────────────────

@router.get("/admin/dashboard")
async def get_admin_dashboard(
    request: Request,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    authorization: Optional[str] = Header(None)
):
    await require_role(request, "admin", authorization)
    async with AsyncSessionLocal() as db:
        booking_query = select(BookingModel)
        if date_from:
            try:
                df = datetime.fromisoformat(date_from).replace(tzinfo=timezone.utc)
                booking_query = booking_query.where(BookingModel.created_at >= df)
            except ValueError:
                pass
        if date_to:
            try:
                dt = datetime.fromisoformat(date_to).replace(tzinfo=timezone.utc) + timedelta(days=1)
                booking_query = booking_query.where(BookingModel.created_at < dt)
            except ValueError:
                pass
        result = await db.execute(booking_query)
        all_bookings = result.scalars().all()
        total_users = (await db.execute(select(func.count(UserModel.id)))).scalar()
        total_providers = (await db.execute(select(func.count(UserModel.id)).where(UserModel.role == UserRole.PROVIDER))).scalar()
        total_assets = (await db.execute(select(func.count(AssetModel.id)).where(AssetModel.is_active == True))).scalar()
        pending_providers = (await db.execute(select(UserModel).where(UserModel.role == UserRole.PROVIDER, UserModel.verified == False))).scalars().all()
        open_disputes = (await db.execute(select(DisputeModel).where(DisputeModel.status == "open"))).scalars().all()
        total_clients = (await db.execute(select(func.count(UserModel.id)).where(UserModel.role == UserRole.CLIENT))).scalar()
        # Clientes con document_id pero sin verificar (KYC pendiente)
        pending_kyc = (await db.execute(
            select(func.count(UserModel.id)).where(
                UserModel.role == UserRole.CLIENT,
                UserModel.verified == False,
                UserModel.document_id.isnot(None),
                UserModel.document_id != "",
            )
        )).scalar()

    this_month = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    monthly = [b for b in all_bookings if b.created_at and (b.created_at if b.created_at.tzinfo else b.created_at.replace(tzinfo=timezone.utc)) >= this_month]
    completed = [b for b in all_bookings if (b.status.value if hasattr(b.status, 'value') else b.status) == "completed"]
    recent = sorted(all_bookings, key=lambda x: x.created_at, reverse=True)[:5] if all_bookings else []

    return {
        "gmv": sum(b.total_price for b in completed),
        "commissions": sum(b.platform_commission for b in completed),
        "total_bookings": len(all_bookings),
        "bookings_this_month": len(monthly),
        "total_users": total_users,
        "total_clients": total_clients,
        "total_providers": total_providers,
        "total_assets": total_assets,
        "pending_approvals": len(pending_providers),
        "pending_kyc": pending_kyc or 0,
        "open_disputes": len(open_disputes),
        "recent_bookings": [booking_to_dict(b) for b in recent]
    }


# ── Providers ────────────────────────────────────────────────────

@router.get("/admin/providers/pending")
async def get_pending_providers(request: Request, authorization: Optional[str] = Header(None)):
    await require_role(request, "admin", authorization)
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(UserModel).where(UserModel.role == UserRole.PROVIDER, UserModel.verified == False)
        )
        providers = result.scalars().all()
    return [user_to_dict(p) for p in providers]


@router.post("/admin/providers/{provider_id}/verify")
async def verify_provider(provider_id: str, request: Request, authorization: Optional[str] = Header(None)):
    await require_role(request, "admin", authorization)
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(UserModel).where(UserModel.id == provider_id))
        provider = result.scalar_one_or_none()
        if not provider:
            raise HTTPException(status_code=404, detail="Provider not found")
        provider.verified = True
        await db.commit()
    return {"message": "Provider verified"}


@router.post("/admin/providers/{provider_id}/reject")
async def reject_provider(
    provider_id: str,
    body: ProviderRejectBody,
    request: Request,
    authorization: Optional[str] = Header(None),
):
    await require_role(request, "admin", authorization)
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(UserModel).where(UserModel.id == provider_id))
        provider = result.scalar_one_or_none()
        if not provider:
            raise HTTPException(status_code=404, detail="Provider not found")

    await send_email(
        to_email=provider.email,
        subject="RedMobility — Tu solicitud como proveedor no fue aprobada",
        html_content=f"""
        <h2>Hola {provider.name},</h2>
        <p>Lamentablemente tu solicitud para ser proveedor en RedMobility no pudo ser aprobada en este momento.</p>
        <p><strong>Motivo:</strong> {body.reason}</p>
        <p>Si crees que esto es un error o deseas más información, contáctanos respondiendo a este correo.</p>
        <p>Saludos,<br/>El equipo de RedMobility</p>
        """,
    )
    return {"message": "Provider rejected, notification sent", "provider_id": provider_id}


# ── Commission config ────────────────────────────────────────────

@router.get("/admin/commission-config")
async def get_commission_config(request: Request, authorization: Optional[str] = Header(None)):
    await require_role(request, "admin", authorization)
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(CommissionConfigModel))
        cfg = result.scalar_one_or_none()
    if not cfg:
        return {"global_rate": 0.15, "provider_overrides": {}, "updated_at": None}
    return {
        "global_rate": float(cfg.global_rate),
        "provider_overrides": cfg.provider_overrides or {},
        "updated_at": cfg.updated_at.isoformat() if cfg.updated_at else None,
    }


@router.patch("/admin/commission-config")
async def update_commission_config(
    body: CommissionConfigUpdate,
    request: Request,
    authorization: Optional[str] = Header(None),
):
    await require_role(request, "admin", authorization)
    if body.global_rate < 0 or body.global_rate > 0.30:
        raise HTTPException(status_code=422, detail="global_rate must be between 0 and 0.30")
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(CommissionConfigModel))
        cfg = result.scalar_one_or_none()
        if cfg:
            cfg.global_rate = body.global_rate
            cfg.updated_at = datetime.now(timezone.utc)
        else:
            cfg = CommissionConfigModel(global_rate=body.global_rate)
            db.add(cfg)
        await db.commit()
        await db.refresh(cfg)
    return {
        "message": "Commission config updated",
        "global_rate": float(cfg.global_rate),
        "updated_at": cfg.updated_at.isoformat() if cfg.updated_at else None,
    }


@router.post("/admin/commission-config/provider-override")
async def add_provider_override(
    body: ProviderOverrideCreate,
    request: Request,
    authorization: Optional[str] = Header(None),
):
    await require_role(request, "admin", authorization)
    if body.rate < 0 or body.rate > 0.30:
        raise HTTPException(status_code=422, detail="rate must be between 0 and 0.30")
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(UserModel).where(UserModel.email == body.provider_email))
        provider = result.scalar_one_or_none()
        if not provider:
            raise HTTPException(status_code=404, detail="Provider not found with that email")

        result = await db.execute(select(CommissionConfigModel))
        cfg = result.scalar_one_or_none()
        override_entry: dict = {"rate": body.rate}
        if body.expires_at:
            override_entry["expires_at"] = body.expires_at

        if cfg:
            overrides = dict(cfg.provider_overrides or {})
            overrides[provider.id] = override_entry
            cfg.provider_overrides = overrides
            cfg.updated_at = datetime.now(timezone.utc)
        else:
            cfg = CommissionConfigModel(
                global_rate=0.15,
                provider_overrides={provider.id: override_entry},
            )
            db.add(cfg)
        await db.commit()
        await db.refresh(cfg)
    return {
        "message": "Provider override saved",
        "provider_id": provider.id,
        "override": override_entry,
    }


# ── Reviews moderation ───────────────────────────────────────────

@router.get("/admin/reviews/pending")
async def get_pending_reviews(request: Request, authorization: Optional[str] = Header(None)):
    await require_role(request, "admin", authorization)
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(ReviewModel).where(ReviewModel.status == "pending"))
        reviews = result.scalars().all()
    return [
        {
            "review_id": r.id,
            "booking_id": r.booking_id,
            "asset_id": r.asset_id,
            "rating": r.rating,
            "comment": r.comment,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in reviews
    ]


@router.post("/admin/reviews/{review_id}/approve")
async def approve_review(review_id: str, request: Request, authorization: Optional[str] = Header(None)):
    await require_role(request, "admin", authorization)
    async with AsyncSessionLocal() as db:
        r_result = await db.execute(select(ReviewModel).where(ReviewModel.id == review_id))
        review = r_result.scalar_one_or_none()
        if not review:
            raise HTTPException(status_code=404, detail="Review not found")
        review.status = "approved"
        await db.commit()
        # BUG-12: Recalculate asset rating
        all_reviews = await db.execute(
            select(ReviewModel).where(ReviewModel.asset_id == review.asset_id, ReviewModel.status == "approved")
        )
        approved = all_reviews.scalars().all()
        if approved:
            new_rating = sum(r.rating for r in approved) / len(approved)
            asset_result = await db.execute(select(AssetModel).where(AssetModel.id == review.asset_id))
            asset = asset_result.scalar_one_or_none()
            if asset:
                asset.rating = round(new_rating, 2)
                await db.commit()
    return {"message": "Review approved and asset rating updated"}


@router.post("/admin/reviews/{review_id}/reject")
async def reject_review(review_id: str, request: Request, authorization: Optional[str] = Header(None)):
    await require_role(request, "admin", authorization)
    async with AsyncSessionLocal() as db:
        r_result = await db.execute(select(ReviewModel).where(ReviewModel.id == review_id))
        review = r_result.scalar_one_or_none()
        if not review:
            raise HTTPException(status_code=404, detail="Review not found")
        review.status = "rejected"
        await db.commit()
    return {"message": "Review rejected"}


# ── Disputes ─────────────────────────────────────────────────────

@router.get("/admin/disputes")
async def get_disputes(
    request: Request,
    status: Optional[str] = None,
    authorization: Optional[str] = Header(None),
):
    await require_role(request, "admin", authorization)
    async with AsyncSessionLocal() as db:
        query = select(DisputeModel)
        if status and status != "all":
            query = query.where(DisputeModel.status == status)
        result = await db.execute(query)
        disputes = result.scalars().all()

        enriched = []
        for d in disputes:
            booking = None
            client = None
            provider = None

            if d.booking_id:
                b_res = await db.execute(select(BookingModel).where(BookingModel.id == d.booking_id))
                booking = b_res.scalar_one_or_none()

            if booking:
                c_res = await db.execute(select(UserModel).where(UserModel.id == booking.client_id))
                client = c_res.scalar_one_or_none()
                p_res = await db.execute(select(UserModel).where(UserModel.id == booking.provider_id))
                provider = p_res.scalar_one_or_none()

            enriched.append({
                "id": d.id,
                "booking_id": d.booking_id,
                "booking_code": booking.booking_code if booking else None,
                "client_name": client.name if client else (booking.client_name if booking else None),
                "client_email": client.email if client else (booking.client_email if booking else None),
                "provider_name": provider.name if provider else None,
                "provider_email": provider.email if provider else None,
                "reason": d.reason,
                "status": d.status,
                "resolution": d.resolution,
                "total_price": booking.total_price if booking else None,
                "created_at": d.created_at.isoformat() if d.created_at else None,
            })
    return enriched


@router.post("/admin/disputes/{dispute_id}/resolve")
async def resolve_dispute_v2(
    dispute_id: str,
    body: DisputeResolveBody,
    request: Request,
    authorization: Optional[str] = Header(None),
):
    await require_role(request, "admin", authorization)

    if body.resolution_type not in ("client", "provider", "partial"):
        raise HTTPException(status_code=422, detail="resolution_type must be 'client', 'provider', or 'partial'")

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(DisputeModel).where(DisputeModel.id == dispute_id))
        dispute = result.scalar_one_or_none()
        if not dispute:
            raise HTTPException(status_code=404, detail="Dispute not found")
        if dispute.status == "resolved":
            raise HTTPException(status_code=400, detail="Dispute is already resolved")

        b_res = await db.execute(select(BookingModel).where(BookingModel.id == dispute.booking_id))
        booking = b_res.scalar_one_or_none()

        if body.resolution_type in ("client", "partial") and body.refund_amount > 0:
            stripe.api_key = STRIPE_API_KEY
            txn_res = await db.execute(
                select(PaymentModel).where(PaymentModel.booking_id == dispute.booking_id)
            )
            txn = txn_res.scalar_one_or_none()
            payment_intent_id = None
            if txn and txn.metadata_info:
                payment_intent_id = txn.metadata_info.get("payment_intent")

            if payment_intent_id:
                try:
                    await stripe.Refund.create_async(
                        payment_intent=payment_intent_id,
                        amount=int(body.refund_amount * 100),
                    )
                except stripe.error.StripeError as e:
                    logger.error(f"Stripe refund error: {str(e)}")
                    raise HTTPException(
                        status_code=502,
                        detail=f"Stripe refund failed: {e.user_message or str(e)}",
                    )
            else:
                logger.warning(f"No payment_intent found for booking {dispute.booking_id} — skipping Stripe refund")

        if body.resolution_type == "provider" and booking:
            booking.status = BookingStatus.COMPLETED

        dispute.status = "resolved"
        dispute.resolution = body.notes
        await db.commit()

    return {"message": "Dispute resolved", "dispute_id": dispute_id}


# ── Admin: listado operativo (BUG-06) ────────────────────────────

@router.get("/admin/bookings")
async def admin_list_bookings(
    request: Request,
    status: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    delivery_only: bool = False,
    authorization: Optional[str] = Header(None)
):
    await require_role(request, "admin", authorization)
    async with AsyncSessionLocal() as db:
        q = select(BookingModel).order_by(BookingModel.created_at.desc())
        if status and status != "all":
            q = q.where(BookingModel.status == status)
        if date_from:
            try:
                df = datetime.fromisoformat(date_from).replace(tzinfo=timezone.utc)
                q = q.where(BookingModel.created_at >= df)
            except ValueError:
                pass
        if date_to:
            try:
                dt = datetime.fromisoformat(date_to).replace(tzinfo=timezone.utc) + timedelta(days=1)
                q = q.where(BookingModel.created_at < dt)
            except ValueError:
                pass
        if delivery_only:
            q = q.where(BookingModel.delivery_requested == True)
        result = await db.execute(q)
        bookings = result.scalars().all()
    return [booking_to_dict(b) for b in bookings]


@router.patch("/admin/bookings/{booking_id}/status")
async def admin_update_booking_status(
    booking_id: str,
    body: BookingStatusUpdate,
    request: Request,
    authorization: Optional[str] = Header(None),
):
    await require_role(request, "admin", authorization)
    allowed = {"confirmed", "cancelled", "completed", "rejected", "active"}
    if body.status not in allowed:
        raise HTTPException(status_code=422, detail=f"Estado no permitido. Usa uno de: {', '.join(allowed)}")
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(BookingModel).where(BookingModel.id == booking_id))
        booking = result.scalar_one_or_none()
        if not booking:
            raise HTTPException(status_code=404, detail="Reserva no encontrada")
        booking.status = body.status
        await db.commit()
        await db.refresh(booking)
    return {"message": f"Estado cambiado a '{body.status}'", "booking_id": booking_id, "new_status": body.status}



@router.get("/admin/users")
async def admin_list_users(request: Request, authorization: Optional[str] = Header(None)):
    await require_role(request, "admin", authorization)
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(UserModel).order_by(UserModel.created_at.desc())
        )
        users = result.scalars().all()
    return [user_to_dict(u) for u in users]


# ── User ban / unban ─────────────────────────────────────────

@router.post("/admin/users/{user_id}/ban")
async def ban_user(
    user_id: str,
    body: BanUserBody,
    request: Request,
    authorization: Optional[str] = Header(None),
):
    """Suspend a user account. Prevents login and booking creation."""
    await require_role(request, "admin", authorization)
    if not body.reason or len(body.reason.strip()) < 5:
        raise HTTPException(status_code=422, detail="Debes proporcionar un motivo de al menos 5 caracteres")
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(UserModel).where(UserModel.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="Usuario no encontrado")
        role_val = user.role.value if hasattr(user.role, 'value') else user.role
        if role_val == "admin":
            raise HTTPException(status_code=403, detail="No puedes suspender una cuenta administradora")
        user.is_banned = True
        user.ban_reason = body.reason.strip()
        await db.commit()
        await db.refresh(user)
    await send_email(
        user.email,
        "Tu cuenta en RedMobility ha sido suspendida",
        f"""
        <h2>Cuenta suspendida</h2>
        <p>Hola {user.name}, tu cuenta en RedMobility ha sido suspendida por el siguiente motivo:</p>
        <p><strong>{body.reason}</strong></p>
        <p>Si crees que esto es un error, contacta a soporte@redmobility.com</p>
        """
    )
    return {"message": "Usuario suspendido", "user_id": user_id, "ban_reason": body.reason}


@router.post("/admin/users/{user_id}/unban")
async def unban_user(
    user_id: str,
    request: Request,
    authorization: Optional[str] = Header(None),
):
    """Reactivate a previously suspended user account."""
    await require_role(request, "admin", authorization)
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(UserModel).where(UserModel.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="Usuario no encontrado")
        user.is_banned = False
        user.ban_reason = None
        await db.commit()
        await db.refresh(user)
    await send_email(
        user.email,
        "Tu cuenta en RedMobility ha sido reactivada",
        f"""
        <h2>¡Cuenta reactivada!</h2>
        <p>Hola {user.name}, tu cuenta en RedMobility ha sido reactivada.</p>
        <p>Ya puedes acceder y utilizar todos los servicios de la plataforma.</p>
        <p>Si tienes alguna pregunta, contacta a soporte@redmobility.com</p>
        """
    )
    return {"message": "Usuario reactivado", "user_id": user_id}


# ── Client KYC verification ─────────────────────────────────

@router.get("/admin/clients/pending-kyc")
async def get_clients_pending_kyc(request: Request, authorization: Optional[str] = Header(None)):
    """List clients who have submitted their phone+document but are not yet verified."""
    await require_role(request, "admin", authorization)
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(UserModel).where(
                UserModel.role == UserRole.CLIENT,
                UserModel.verified == False,
                UserModel.is_banned == False,
                UserModel.phone_whatsapp != None,
                UserModel.document_id != None,
            ).order_by(UserModel.created_at.desc())
        )
        clients = result.scalars().all()
    return [user_to_dict(c) for c in clients]


@router.post("/admin/clients/{client_id}/verify-kyc")
async def verify_client_kyc(
    client_id: str,
    request: Request,
    authorization: Optional[str] = Header(None),
):
    """Mark a client as KYC-verified, enabling them to make bookings."""
    await require_role(request, "admin", authorization)
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(UserModel).where(UserModel.id == client_id))
        client = result.scalar_one_or_none()
        if not client:
            raise HTTPException(status_code=404, detail="Cliente no encontrado")
        role_val = client.role.value if hasattr(client.role, 'value') else client.role
        if role_val != "client":
            raise HTTPException(status_code=400, detail="Solo se pueden verificar clientes")
        client.verified = True
        await db.commit()
        await db.refresh(client)
    await send_email(
        client.email,
        "¡Tu identidad ha sido verificada! — RedMobility",
        f"""
        <h2>¡Bienvenido oficialmente, {client.name}!</h2>
        <p>Tu identidad ha sido verificada por el equipo de RedMobility.</p>
        <p>Ya puedes realizar reservas con total tranquilidad.</p>
        <p>Explora nuestro catálogo de vehículos recreativos en <a href="{FRONTEND_URL}">RedMobility</a>.</p>
        """
    )
    return {"message": "Cliente KYC verificado", "client_id": client_id}
