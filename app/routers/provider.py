# Migrado desde server.py líneas 992–1178, 1744–1801
# 8 endpoints: create/get/patch/delete assets, get/patch bookings, dashboard, upload/image
import io
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

import asyncio

import stripe
from fastapi import APIRouter, File, Form, Header, HTTPException, Request, UploadFile
from PIL import Image
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from database import AsyncSessionLocal
from models import (
    Asset as AssetModel,
    AssetImage as AssetImageModel,
    Booking as BookingModel,
    BlockedDate as BlockedDateModel,
    Category as CategoryModel,
    Notification as NotificationModel,
    PaymentTransaction as PaymentModel,
    User as UserModel,
)
from ..dependencies import (
    FRONTEND_URL,
    STRIPE_API_KEY,
    UPLOAD_DIR,
    require_role,
    send_email,
)
from ..utils import asset_to_dict, booking_to_dict
from .media import store_media

router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)

# ── Constantes de upload ─────────────────────────────────────────
_ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
_PILLOW_FORMAT_MAP  = {"jpeg": "jpg", "png": "png", "webp": "webp"}
_MAX_BYTES          = 5 * 1024 * 1024   # 5 MB

# ── Pydantic schemas (solo usados en este router) ────────────────

class AssetCreate(BaseModel):
    type: str
    name: str
    description: str
    brand: Optional[str] = None
    model: Optional[str] = None
    year: Optional[int] = None
    capacity: Optional[int] = None
    price_per_hour: Optional[int] = None   # USD enteros — sin centavos
    price_per_day: int                      # USD enteros — sin centavos
    location_zone: str
    pickup_address: Optional[str] = None
    what_included: List[str] = []
    images: List[str] = []


# ── Endpoints ────────────────────────────────────────────────────

@router.post("/provider/assets")
async def create_asset(asset_data: AssetCreate, request: Request, authorization: Optional[str] = Header(None)):
    user = await require_role(request, "provider", authorization)
    if not user.verified:
        raise HTTPException(status_code=403, detail="Your account is pending verification. Contact support.")
    # Validar tipo contra categorías activas en DB (reemplaza set hardcodeado)
    async with AsyncSessionLocal() as db:
        cat = await db.get(CategoryModel, asset_data.type)
    if not cat or not cat.is_active:
        raise HTTPException(status_code=422, detail=f"Tipo de activo inválido o inactivo: {asset_data.type}")
    # SEC-09: Validate image URLs (HTTPS only)
    for url in asset_data.images:
        if not url.startswith("https://"):
            raise HTTPException(status_code=422, detail=f"Image URLs must use HTTPS: {url}")
    async with AsyncSessionLocal() as db:
        asset = AssetModel(
            provider_id=user.id,
            type=asset_data.type,
            name=asset_data.name,
            description=asset_data.description,
            brand=asset_data.brand,
            model=asset_data.model,
            year=asset_data.year,
            capacity=asset_data.capacity,
            price_per_hour=asset_data.price_per_hour,
            price_per_day=asset_data.price_per_day,
            location_zone=asset_data.location_zone,
            pickup_address=asset_data.pickup_address,
            what_included=asset_data.what_included,
            review_status="pending",   # requiere aprobación del admin antes de ser público
        )
        db.add(asset)
        await db.flush()
        for idx, url in enumerate(asset_data.images):
            db.add(AssetImageModel(asset_id=asset.id, url=url, is_primary=(idx == 0)))
        await db.commit()

        # Recargar el asset con las imágenes para serializarlo sin errores de lazy loading
        result = await db.execute(
            select(AssetModel).options(selectinload(AssetModel.images)).where(AssetModel.id == asset.id)
        )
        asset = result.scalar_one()
    return asset_to_dict(asset)


@router.get("/provider/assets")
async def get_provider_assets(request: Request, authorization: Optional[str] = Header(None)):
    user = await require_role(request, "provider", authorization)
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(AssetModel).options(selectinload(AssetModel.images)).where(AssetModel.provider_id == user.id)
        )
        assets = result.scalars().all()
        # Enrich with revenue per asset from completed bookings
        bookings_res = await db.execute(
            select(BookingModel).where(BookingModel.provider_id == user.id)
        )
        all_bookings = bookings_res.scalars().all()

    # Build revenue map per asset_id
    revenue_map: dict = {}
    for b in all_bookings:
        status = b.status.value if hasattr(b.status, "value") else b.status
        if status == "completed":
            net = (b.total_price or 0) - (b.platform_commission or 0) - (b.insurance_price or 0)
            revenue_map[b.asset_id] = revenue_map.get(b.asset_id, 0) + net

    enriched = []
    for a in assets:
        d = asset_to_dict(a)
        d["total_revenue"] = round(revenue_map.get(a.id, 0), 2)
        enriched.append(d)
    return enriched



@router.patch("/provider/assets/{asset_id}")
async def update_asset(asset_id: str, updates: Dict[str, Any], request: Request, authorization: Optional[str] = Header(None)):
    user = await require_role(request, "provider", authorization)
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(AssetModel).where(AssetModel.id == asset_id))
        asset = result.scalar_one_or_none()
        if not asset:
            raise HTTPException(status_code=404, detail="Asset not found")
        if asset.provider_id != user.id:
            raise HTTPException(status_code=403, detail="Access denied")
        allowed = {"name", "description", "price_per_day", "price_per_hour",
                   "is_active", "pickup_address", "what_included",
                   "buffer_before_hours", "buffer_after_hours",
                   "delivery_available", "delivery_radius_km", "delivery_cost_usd"}
        for key, val in updates.items():
            if key in allowed:
                if key in ("buffer_before_hours", "buffer_after_hours"):
                    val = max(0.0, min(24.0, float(val)))
                elif key == "delivery_radius_km":
                    val = max(0.0, min(200.0, float(val)))
                elif key == "delivery_cost_usd":
                    val = max(0, min(500, int(val)))
                setattr(asset, key, val)
        await db.commit()
    return {"message": "Asset updated"}


@router.delete("/provider/assets/{asset_id}")
async def delete_asset(asset_id: str, request: Request, authorization: Optional[str] = Header(None)):
    user = await require_role(request, "provider", authorization)
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(AssetModel).where(AssetModel.id == asset_id))
        asset = result.scalar_one_or_none()
        if not asset:
            raise HTTPException(status_code=404, detail="Asset not found")
        if asset.provider_id != user.id:
            raise HTTPException(status_code=403, detail="Access denied")
        # Soft delete — desactiva en lugar de borrar para preservar historial de reservas
        asset.is_active = False
        if not asset.name.startswith("[ELIMINADO]"):
            asset.name = f"[ELIMINADO] {asset.name}"
        await db.commit()
    return {"message": "Asset deleted"}


@router.patch("/provider/bookings/{booking_id}")
async def update_booking_status(booking_id: str, updates: Dict[str, Any], request: Request, authorization: Optional[str] = Header(None)):
    user = await require_role(request, "provider", authorization)
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(BookingModel).where(BookingModel.id == booking_id))
        booking = result.scalar_one_or_none()
        if not booking:
            raise HTTPException(status_code=404, detail="Booking not found")
        if booking.provider_id != user.id:
            raise HTTPException(status_code=403, detail="Access denied")

        if "status" in updates:
            new_status = updates["status"]
            current = booking.status.value if hasattr(booking.status, "value") else booking.status
            # ALTO-06: Transiciones válidas para un provider.
            # "pending" excluido — un provider no puede revertir una reserva ya procesada.
            valid_transitions = {
                "pending":   {"confirmed", "rejected"},
                "confirmed": {"completed", "cancelled"},
            }
            allowed = valid_transitions.get(current, set())
            if new_status not in allowed:
                raise HTTPException(
                    status_code=400,
                    detail=f"Transición '{current}' → '{new_status}' no permitida"
                )

            # G-1: proveedor cancela reserva ya pagada → reembolso 100% al cliente
            if current == "confirmed" and new_status == "cancelled":
                txn_res = await db.execute(
                    select(PaymentModel).where(
                        PaymentModel.booking_id == booking_id,
                        PaymentModel.status == "completed"
                    )
                )
                txn = txn_res.scalar_one_or_none()
                if txn and txn.metadata_info:
                    pi_id = txn.metadata_info.get("payment_intent")
                    if pi_id:
                        stripe.api_key = STRIPE_API_KEY
                        try:
                            await stripe.Refund.create_async(payment_intent=pi_id, amount=int(booking.total_price * 100))
                        except Exception as se:
                            logger.error(f"Stripe refund error on provider cancellation: {se}")
                            raise HTTPException(status_code=502, detail=f"Refund failed: {str(se)}")
                await send_email(
                    booking.client_email,
                    "Tu reserva fue cancelada por el proveedor — RedMobility",
                    f"<h2>Hola {booking.client_name},</h2>"
                    f"<p>Lamentablemente el proveedor canceló tu reserva <strong>{booking.booking_code}</strong>.</p>"
                    "<p>Recibirás un reembolso completo en 5–10 días hábiles.</p>",
                )

            booking.status = new_status

            # Notificación in-app al cliente cuando el proveedor actúa
            notif_map = {
                "confirmed": (
                    "booking_confirmed",
                    "✅ Reserva confirmada",
                    f"Tu reserva {booking.booking_code} fue confirmada por el proveedor. ¡Todo listo!",
                ),
                "rejected": (
                    "booking_rejected",
                    "❌ Reserva rechazada",
                    f"Tu reserva {booking.booking_code} fue rechazada por el proveedor. Puedes buscar otra opción.",
                ),
                "completed": (
                    "booking_completed",
                    "✔️ Servicio completado",
                    f"Tu reserva {booking.booking_code} fue marcada como completada. ¡Deja una reseña!",
                ),
            }
            if new_status in notif_map:
                ntype, ntitle, nmsg = notif_map[new_status]
                db.add(NotificationModel(
                    user_id=booking.client_id,
                    booking_id=booking_id,
                    type=ntype,
                    title=ntitle,
                    message=nmsg,
                ))

        await db.commit()
    return {"message": "Booking status updated"}


@router.get("/provider/bookings")
async def get_provider_bookings(request: Request, authorization: Optional[str] = Header(None)):
    user = await require_role(request, "provider", authorization)
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(BookingModel).options(selectinload(BookingModel.asset)).where(BookingModel.provider_id == user.id)
        )
        bookings = result.scalars().all()
    data = []
    for b in bookings:
        d = booking_to_dict(b)
        if b.asset:
            d["asset"] = {"name": b.asset.name, "type": b.asset.type}
        data.append(d)
    return data


@router.get("/provider/dashboard")
async def get_provider_dashboard(request: Request, authorization: Optional[str] = Header(None)):
    user = await require_role(request, "provider", authorization)
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(BookingModel).where(BookingModel.provider_id == user.id))
        bookings = result.scalars().all()
        result = await db.execute(select(AssetModel).where(AssetModel.provider_id == user.id))
        assets = result.scalars().all()

    completed = [b for b in bookings if (b.status.value if hasattr(b.status, 'value') else b.status) == "completed"]
    # G-3: provider earns total_price minus commission and insurance (insurance stays with platform)
    total_revenue = sum(b.total_price - b.platform_commission - b.insurance_price for b in completed)
    this_month = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    monthly = [b for b in bookings if b.created_at and (b.created_at if b.created_at.tzinfo else b.created_at.replace(tzinfo=timezone.utc)) >= this_month]
    monthly_completed = [b for b in monthly if (b.status.value if hasattr(b.status, 'value') else b.status) == "completed"]
    monthly_revenue = sum(b.total_price - b.platform_commission - b.insurance_price for b in monthly_completed)
    monthly_cancelled = [b for b in monthly if (b.status.value if hasattr(b.status, 'value') else b.status) == "cancelled"]
    active_bookings = [b for b in bookings if (b.status.value if hasattr(b.status, 'value') else b.status) in ["pending", "confirmed"]]
    avg_rating = sum(a.rating for a in assets) / len(assets) if assets else 0
    active_assets = [a for a in assets if a.is_active]
    inactive_assets = [a for a in assets if not a.is_active]

    # Monthly breakdown — last 6 months for trend chart
    from collections import defaultdict
    month_map = defaultdict(float)
    MONTH_NAMES = ['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic']
    for b in completed:
        dt = b.created_at if b.created_at else None
        if dt:
            if not dt.tzinfo:
                dt = dt.replace(tzinfo=timezone.utc)
            key = f"{MONTH_NAMES[dt.month - 1]} {str(dt.year)[2:]}"
            month_map[key] += b.total_price - b.platform_commission - b.insurance_price
    # Build ordered list of last 6 calendar months
    now = datetime.now(timezone.utc)
    monthly_breakdown = []
    for i in range(5, -1, -1):
        mo = (now.month - i - 1) % 12
        yr = now.year - ((now.month - i - 1) // 12)
        label = f"{MONTH_NAMES[mo]} {str(yr)[2:]}"
        monthly_breakdown.append({"month": label, "revenue": round(month_map.get(label, 0), 2)})

    return {
        "total_revenue": total_revenue,
        "monthly_revenue": monthly_revenue,
        "bookings_this_month": len(monthly),
        "monthly_revenue_count": len(monthly_completed),
        "total_bookings": len(bookings),
        "active_assets": len(active_assets),
        "inactive_assets": len(inactive_assets),
        "active_bookings_count": len(active_bookings),
        "cancelled_this_month": len(monthly_cancelled),
        "avg_rating": round(avg_rating, 1),
        "upcoming_bookings": [booking_to_dict(b) for b in active_bookings],
        "monthly_breakdown": monthly_breakdown,
    }


@router.post("/upload/image")
async def upload_image(
    request: Request,
    file: UploadFile = File(...),
    authorization: Optional[str] = Header(None),
):
    """
    Sube una imagen para un activo. Solo proveedores verificados.
    - Valida extensión y MIME real (no solo el nombre del archivo)
    - Renombra a UUID para evitar path traversal y colisiones
    - Guarda en volumen aislado; FastAPI nunca sirve el contenido
    """
    user = await require_role(request, "provider", authorization)
    if not user.verified:
        raise HTTPException(status_code=403, detail="Cuenta pendiente de verificación.")

    # 1. Validar extensión
    original_name = file.filename or ""
    ext = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else ""
    if ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=422, detail=f"Extensión no permitida. Usa: {', '.join(_ALLOWED_EXTENSIONS)}")

    # 2. Leer contenido (con límite)
    content = await file.read(_MAX_BYTES + 1)
    if len(content) > _MAX_BYTES:
        raise HTTPException(status_code=413, detail="Imagen demasiado grande. Máximo 5 MB.")

    # 3. Validar MIME real con Pillow — ejecutado en threadpool para no bloquear el event loop
    def _validate_image(data: bytes) -> str:
        img = Image.open(io.BytesIO(data))
        detected = (img.format or "").lower()
        img.close()
        return detected

    try:
        fmt = await asyncio.to_thread(_validate_image, content)
    except Exception:
        raise HTTPException(status_code=422, detail="El archivo no es una imagen válida (jpg/png/webp).")
    if fmt not in _PILLOW_FORMAT_MAP:
        raise HTTPException(status_code=422, detail="El archivo no es una imagen válida (jpg/png/webp).")

    # 5. Guardar la imagen en la base de datos (Render no tiene disco persistente)
    #    store_media revalida formato/tamaño/resolución mínima.
    media_id, _ct = await store_media(content, owner_id=user.id, private=False)

    # 6. Devolver URL pública servida por el propio backend
    base = str(request.base_url).rstrip("/")
    url = f"{base}/api/media/{media_id}"
    return {"url": url, "filename": media_id}


# ── Verificación de identidad del proveedor (cédula/pasaporte) ─────

@router.post("/provider/verification")
async def submit_verification(
    request: Request,
    document_type: str = Form(...),
    document_number: str = Form(...),
    file: UploadFile = File(...),
    authorization: Optional[str] = Header(None),
):
    """El proveedor sube su cédula o pasaporte. Queda en estado 'pending' hasta que el admin lo apruebe."""
    user = await require_role(request, "provider", authorization)
    if document_type not in ("cedula", "pasaporte"):
        raise HTTPException(status_code=422, detail="Tipo de documento inválido (cedula o pasaporte).")
    if not (document_number or "").strip():
        raise HTTPException(status_code=422, detail="Número de documento requerido.")
    content = await file.read(5 * 1024 * 1024 + 1)
    media_id, _ = await store_media(content, owner_id=user.id, private=True)
    async with AsyncSessionLocal() as db:
        u = await db.get(UserModel, user.id)
        u.document_type = document_type
        u.document_id = document_number.strip()
        u.document_media_id = media_id
        u.verification_status = "pending"
        await db.commit()
    # Notificar al equipo (si Brevo no está configurado, queda en el log del servidor)
    try:
        await send_email(
            to_email="soporte@redmobility.net",
            subject="Nueva verificación de proveedor pendiente",
            html_content=f"<p>El proveedor <b>{user.email}</b> subió su {document_type} (N.º {document_number}). Revísalo en el panel de admin → Verificación.</p>",
        )
    except Exception:
        pass
    return {"status": "pending", "message": "Documento recibido. Tu cuenta está en revisión."}


@router.get("/provider/verification")
async def get_verification_status(request: Request, authorization: Optional[str] = Header(None)):
    user = await require_role(request, "provider", authorization)
    async with AsyncSessionLocal() as db:
        u = await db.get(UserModel, user.id)
    return {
        "verified": bool(u.verified),
        "verification_status": getattr(u, "verification_status", "none") or "none",
        "document_type": getattr(u, "document_type", None),
        "document_number": u.document_id,
        "has_document": bool(getattr(u, "document_media_id", None)),
    }


# ── Gestión de fechas bloqueadas ──────────────────────────────────

class BlockedDateCreate(BaseModel):
    start_date: str   # ISO 8601
    end_date:   str   # ISO 8601
    reason: Optional[str] = None


@router.get("/provider/assets/{asset_id}/blocked-dates")
async def list_blocked_dates(asset_id: str, request: Request, authorization: Optional[str] = Header(None)):
    """Lista todos los bloqueos manuales del proveedor para un activo."""
    user = await require_role(request, "provider", authorization)
    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(BlockedDateModel).where(
                BlockedDateModel.asset_id == asset_id,
                BlockedDateModel.provider_id == user.id,
            ).order_by(BlockedDateModel.start_date)
        )
        blocks = res.scalars().all()
    return [
        {
            "block_id":   b.id,
            "asset_id":   b.asset_id,
            "start_date": b.start_date.isoformat(),
            "end_date":   b.end_date.isoformat(),
            "reason":     b.reason,
            "created_at": b.created_at.isoformat() if b.created_at else None,
        }
        for b in blocks
    ]


@router.post("/provider/assets/{asset_id}/blocked-dates", status_code=201)
async def create_blocked_date(
    asset_id: str,
    body: BlockedDateCreate,
    request: Request,
    authorization: Optional[str] = Header(None),
):
    """Proveedor bloquea un rango de fechas (mantenimiento, uso personal, etc.)."""
    user = await require_role(request, "provider", authorization)
    try:
        start = datetime.fromisoformat(body.start_date.replace("Z", "+00:00"))
        end   = datetime.fromisoformat(body.end_date.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=422, detail="Formato de fecha inválido. Usa ISO 8601.")
    if end <= start:
        raise HTTPException(status_code=422, detail="end_date debe ser posterior a start_date")
    if start < datetime.now(timezone.utc):
        raise HTTPException(status_code=422, detail="No puedes bloquear fechas pasadas")

    async with AsyncSessionLocal() as db:
        # Verificar que el activo pertenece al proveedor
        asset = await db.get(AssetModel, asset_id)
        if not asset or asset.provider_id != user.id:
            raise HTTPException(status_code=404, detail="Activo no encontrado")
        block = BlockedDateModel(
            asset_id=asset_id,
            provider_id=user.id,
            start_date=start,
            end_date=end,
            reason=body.reason,
        )
        db.add(block)
        await db.commit()
        await db.refresh(block)
    return {
        "block_id":   block.id,
        "asset_id":   block.asset_id,
        "start_date": block.start_date.isoformat(),
        "end_date":   block.end_date.isoformat(),
        "reason":     block.reason,
    }


@router.delete("/provider/blocked-dates/{block_id}")
async def delete_blocked_date(block_id: str, request: Request, authorization: Optional[str] = Header(None)):
    """Elimina un bloqueo manual."""
    user = await require_role(request, "provider", authorization)
    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(BlockedDateModel).where(BlockedDateModel.id == block_id)
        )
        block = res.scalar_one_or_none()
        if not block:
            raise HTTPException(status_code=404, detail="Bloqueo no encontrado")
        if block.provider_id != user.id:
            raise HTTPException(status_code=403, detail="Acceso denegado")
        await db.delete(block)
        await db.commit()
    return {"message": "Bloqueo eliminado"}


# ── Disponibilidad pública (usada por ListingDetail) ─────────────

@router.get("/assets/{asset_id}/availability")
async def get_asset_availability(asset_id: str):
    """
    Devuelve todos los períodos NO disponibles para un activo:
    - Reservas activas/confirmadas/pendientes (con su buffer antes y después)
    - Bloqueos manuales del proveedor
    El frontend usa esta lista para deshabilitar fechas en el picker.
    """
    async with AsyncSessionLocal() as db:
        asset = await db.get(AssetModel, asset_id)
        if not asset:
            raise HTTPException(status_code=404, detail="Activo no encontrado")

        buf_before = asset.buffer_before_hours or 2.0
        buf_after  = asset.buffer_after_hours  or 2.0

        # Reservas activas
        bk_res = await db.execute(
            select(BookingModel).where(
                BookingModel.asset_id == asset_id,
                BookingModel.status.in_(["pending", "confirmed", "active"]),
            )
        )
        bookings = bk_res.scalars().all()

        # Bloqueos manuales
        bl_res = await db.execute(
            select(BlockedDateModel).where(BlockedDateModel.asset_id == asset_id)
        )
        manual_blocks = bl_res.scalars().all()

    occupied = []

    for b in bookings:
        start = b.start_datetime - timedelta(hours=buf_before)
        end   = b.start_datetime + timedelta(hours=b.end_hours) + timedelta(hours=buf_after)
        occupied.append({
            "type":       "booking",
            "start":      start.isoformat(),
            "end":        end.isoformat(),
            "booking_id": b.id,
        })

    for bl in manual_blocks:
        occupied.append({
            "type":     "manual",
            "start":    bl.start_date.isoformat(),
            "end":      bl.end_date.isoformat(),
            "reason":   bl.reason or "No disponible",
            "block_id": bl.id,
        })

    return {
        "asset_id":           asset_id,
        "buffer_before_hours": buf_before,
        "buffer_after_hours":  buf_after,
        "occupied":            occupied,
    }
