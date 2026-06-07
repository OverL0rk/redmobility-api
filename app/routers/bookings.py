# Migrado desde server.py líneas 722–873, 1272–1330, 1535–1593
# 5 endpoints: create booking, list bookings, get booking, cancel, open dispute
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import stripe
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from database import AsyncSessionLocal
from models import (
    Asset as AssetModel,
    Booking as BookingModel,
    BlockedDate as BlockedDateModel,
    Dispute as DisputeModel,
    PaymentTransaction as PaymentModel,
    Review as ReviewModel,
    User as UserModel,
    BookingStatus,
)
from ..dependencies import (
    STRIPE_API_KEY,
    get_commission_rate,
    require_auth,
    send_email,
)
from ..utils import asset_to_dict, booking_to_dict

router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)

# ── Pydantic schemas (solo usados en este router) ────────────────

class InsuranceType:
    NONE     = "none"
    STANDARD = "standard"
    PREMIUM  = "premium"

class BookingCreate(BaseModel):
    asset_id: str
    start_datetime: str
    end_hours: int
    insurance_type: str = InsuranceType.NONE
    client_name: str
    client_email: EmailStr
    client_phone: str
    client_document: str
    delivery_requested: bool = False
    delivery_address:   Optional[str] = None

class OpenDisputeRequest(BaseModel):
    reason: str


# ── Endpoints ────────────────────────────────────────────────────

@router.post("/bookings")
async def create_booking(booking_data: BookingCreate, request: Request, authorization: Optional[str] = Header(None)):
    user = await require_auth(request, authorization)
    # Only clients can book
    role_val = user.role.value if hasattr(user.role, 'value') else user.role
    if role_val != "client":
        raise HTTPException(status_code=403, detail="Only clients can create bookings")

    # SECURITY: Banned users cannot book
    if getattr(user, 'is_banned', False):
        ban_reason = getattr(user, 'ban_reason', None) or 'Cuenta suspendida'
        raise HTTPException(
            status_code=403,
            detail=f"Tu cuenta ha sido suspendida: {ban_reason}. Contacta a soporte@redmobility.com"
        )

    # KYC: Profile must be complete before booking (phone + document required)
    if not getattr(user, 'phone_whatsapp', None) or not getattr(user, 'document_id', None):
        raise HTTPException(
            status_code=403,
            detail="PERFIL_INCOMPLETO: Debes completar tu perfil con teléfono y documento de identidad antes de realizar una reserva."
        )

    # BUG-10: Validate end_hours
    if booking_data.end_hours < 1:
        raise HTTPException(status_code=422, detail="end_hours must be at least 1")
    if booking_data.end_hours > 720:
        raise HTTPException(status_code=422, detail="end_hours cannot exceed 720 (30 days)")

    async with AsyncSessionLocal() as db:
        # G-2: lock asset row to prevent double-booking race condition
        result = await db.execute(select(AssetModel).where(AssetModel.id == booking_data.asset_id).with_for_update())
        asset = result.scalar_one_or_none()
        if not asset:
            raise HTTPException(status_code=404, detail="Asset not found")
        # BUG-06: Check asset is active
        if not asset.is_active:
            raise HTTPException(status_code=400, detail="This asset is not available for booking")

        # DELIVERY: Validar si el activo ofrece delivery cuando el cliente lo pide
        if booking_data.delivery_requested:
            if not getattr(asset, 'delivery_available', False):
                raise HTTPException(
                    status_code=400,
                    detail="Este activo no ofrece servicio de delivery. Deberás desplazarte a recogerlo."
                )
            if not booking_data.delivery_address or len(booking_data.delivery_address.strip()) < 5:
                raise HTTPException(
                    status_code=422,
                    detail="Debes indicar una dirección de entrega válida para el delivery."
                )

        # BUG-07: Validate future date
        start_dt = datetime.fromisoformat(booking_data.start_datetime.replace('Z', '+00:00'))
        if start_dt <= datetime.now(timezone.utc):
            raise HTTPException(status_code=422, detail="start_datetime must be in the future")

        # BUG-02: Double-booking detection con buffer configurable
        end_dt = start_dt + timedelta(hours=booking_data.end_hours)
        buf_before = timedelta(hours=getattr(asset, 'buffer_before_hours', 2.0))
        buf_after  = timedelta(hours=getattr(asset, 'buffer_after_hours',  2.0))
        # ventana efectiva de disponibilidad requerida
        needed_start = start_dt - buf_before   # cliente necesita el activo libre desde aqui
        needed_end   = end_dt   + buf_after    # y libre hasta aqui

        # 1) Solapamiento con reservas existentes (incluyendo sus buffers)
        overlap_result = await db.execute(
            select(BookingModel).where(
                BookingModel.asset_id == booking_data.asset_id,
                BookingModel.status.in_([BookingStatus.PENDING, BookingStatus.CONFIRMED, BookingStatus.ACTIVE]),
                BookingModel.start_datetime < needed_end,
            )
        )
        overlapping = overlap_result.scalars().all()
        for ob in overlapping:
            ob_end   = ob.start_datetime + timedelta(hours=ob.end_hours)
            ob_start_buf = ob.start_datetime - buf_before
            ob_end_buf   = ob_end + buf_after
            if ob_end_buf > start_dt and ob_start_buf < end_dt:
                raise HTTPException(
                    status_code=409,
                    detail=f"El activo no está disponible para ese horario (incluye {int(getattr(asset,'buffer_before_hours',2))}h de preparación antes y {int(getattr(asset,'buffer_after_hours',2))}h después)"
                )

        # 2) Solapamiento con bloqueos manuales del proveedor
        block_result = await db.execute(
            select(BlockedDateModel).where(
                BlockedDateModel.asset_id == booking_data.asset_id,
                BlockedDateModel.start_date < end_dt,
                BlockedDateModel.end_date   > start_dt,
            )
        )
        if block_result.scalar_one_or_none():
            raise HTTPException(
                status_code=409,
                detail="El proveedor ha bloqueado el activo para esas fechas (mantenimiento u otro motivo)"
            )

        # Price calculation — integer USD only, no cents
        total_hours = booking_data.end_hours
        hour_rate = asset.price_per_hour if asset.price_per_hour else asset.price_per_day // 8
        if total_hours >= 24:
            days = total_hours // 24
            remaining_hours = total_hours % 24
            base_price = (days * asset.price_per_day) + (remaining_hours * hour_rate)
        else:
            base_price = total_hours * hour_rate

        insurance_price = 0
        if booking_data.insurance_type == InsuranceType.STANDARD:
            insurance_price = round(base_price * 0.05)   # 5% redondeado a entero
        elif booking_data.insurance_type == InsuranceType.PREMIUM:
            insurance_price = round(base_price * 0.08)   # 8% redondeado a entero

        # NEG-01: Read commission from DB, not hardcoded
        commission_rate = await get_commission_rate(asset.provider_id)
        # Delivery cost se suma al total
        delivery_cost = 0
        if booking_data.delivery_requested and getattr(asset, 'delivery_cost_usd', 0):
            delivery_cost = int(asset.delivery_cost_usd)

        total_price = base_price + insurance_price + delivery_cost  # entero
        platform_commission = round(base_price * commission_rate)   # entero
        booking_code = f"RM-{datetime.now(timezone.utc).year}-{uuid.uuid4().hex[:5].upper()}"

        booking = BookingModel(
            booking_code=booking_code,
            client_id=user.id,
            asset_id=booking_data.asset_id,
            provider_id=asset.provider_id,
            start_datetime=start_dt,
            end_hours=booking_data.end_hours,
            total_price=total_price,
            platform_commission=platform_commission,
            insurance_type=booking_data.insurance_type,
            insurance_price=insurance_price,
            client_name=booking_data.client_name,
            client_email=booking_data.client_email,
            client_phone=booking_data.client_phone,
            client_document=booking_data.client_document,
            delivery_requested=booking_data.delivery_requested,
            delivery_address=booking_data.delivery_address if booking_data.delivery_requested else None,
        )
        db.add(booking)
        await db.commit()
        await db.refresh(booking)

    await send_email(
        booking.client_email,
        f"Reserva recibida {booking.booking_code} — RedMobility",
        f"""<h2>¡Reserva recibida, {booking.client_name}!</h2>
        <p>Tu solicitud está siendo revisada por el proveedor.</p>
        <table>
          <tr><td><b>Código:</b></td><td>{booking.booking_code}</td></tr>
          <tr><td><b>Fecha:</b></td><td>{booking.start_datetime.strftime('%d/%m/%Y %H:%M')}</td></tr>
          <tr><td><b>Duración:</b></td><td>{booking.end_hours} horas</td></tr>
          <tr><td><b>Total:</b></td><td>${booking.total_price}</td></tr>
        </table>
        <p>Recibirás una confirmación cuando el proveedor apruebe la reserva.</p>""",
    )

    return booking_to_dict(booking)


@router.get("/bookings")
async def get_my_bookings(request: Request, authorization: Optional[str] = Header(None)):
    user = await require_auth(request, authorization)
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(BookingModel)
            .options(selectinload(BookingModel.asset).selectinload(AssetModel.images))
            .where(BookingModel.client_id == user.id)
        )
        bookings = result.scalars().all()
        # Fetch provider data for each booking
        provider_ids = list({b.provider_id for b in bookings if b.provider_id})
        providers = {}
        if provider_ids:
            prov_result = await db.execute(select(UserModel).where(UserModel.id.in_(provider_ids)))
            for p in prov_result.scalars().all():
                providers[p.id] = p
        # Check existing reviews for completed bookings
        booking_ids = [b.id for b in bookings]
        review_booking_ids = set()
        if booking_ids:
            rev_result = await db.execute(
                select(ReviewModel.booking_id).where(ReviewModel.booking_id.in_(booking_ids))
            )
            review_booking_ids = {r[0] for r in rev_result.all()}

        # Check existing open disputes
        dispute_booking_ids = set()
        if booking_ids:
            disp_result = await db.execute(
                select(DisputeModel.booking_id).where(
                    DisputeModel.booking_id.in_(booking_ids),
                    DisputeModel.status == "open"
                )
            )
            dispute_booking_ids = {r[0] for r in disp_result.all()}

        data = []
        for b in bookings:
            d = booking_to_dict(b)
            if b.asset:
                d["asset"] = {
                    "name": b.asset.name,
                    "type": b.asset.type,
                    "images": [{"url": i.url, "is_primary": i.is_primary} for i in b.asset.images if i.is_primary]
                }
            if b.provider_id and b.provider_id in providers:
                p = providers[b.provider_id]
                d["provider"] = {
                    "name": p.name,
                    "email": p.email,
                    "phone_whatsapp": p.phone_whatsapp,
                }
            d["has_review"] = b.id in review_booking_ids
            d["has_open_dispute"] = b.id in dispute_booking_ids
            data.append(d)
    return data


@router.get("/bookings/{booking_id}")
async def get_booking(booking_id: str, request: Request, authorization: Optional[str] = Header(None)):
    user = await require_auth(request, authorization)
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(BookingModel)
            .options(selectinload(BookingModel.asset).selectinload(AssetModel.images))
            .where(BookingModel.id == booking_id)
        )
        booking = result.scalar_one_or_none()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    role_val = user.role.value if hasattr(user.role, 'value') else user.role
    if booking.client_id != user.id and booking.provider_id != user.id and role_val != "admin":
        raise HTTPException(status_code=403, detail="Access denied")
    data = booking_to_dict(booking)
    if booking.asset:
        data["asset"] = asset_to_dict(booking.asset)
    return data


@router.post("/bookings/{booking_id}/cancel")
async def cancel_booking(booking_id: str, request: Request, authorization: Optional[str] = Header(None)):
    """NEG-07: Cancellation with Stripe refund based on policy (>24h=100%, 2-24h=50%, <2h=0%)"""
    user = await require_auth(request, authorization)
    async with AsyncSessionLocal() as db:
        b_result = await db.execute(select(BookingModel).where(BookingModel.id == booking_id))
        booking = b_result.scalar_one_or_none()
        if not booking:
            raise HTTPException(status_code=404, detail="Booking not found")
        if booking.client_id != user.id:
            raise HTTPException(status_code=403, detail="Access denied")
        status_val = booking.status.value if hasattr(booking.status, 'value') else booking.status
        if status_val not in ["pending", "confirmed"]:
            raise HTTPException(status_code=400, detail=f"Cannot cancel a booking with status '{status_val}'")

        # Determine refund percentage
        now = datetime.now(timezone.utc)
        hours_until_start = (booking.start_datetime - now).total_seconds() / 3600
        if hours_until_start >= 24:
            refund_pct = 1.0
            refund_label = "100%"
        elif hours_until_start >= 2:
            refund_pct = 0.5
            refund_label = "50%"
        else:
            refund_pct = 0.0
            refund_label = "0%"

        refund_amount = round(booking.total_price * refund_pct)  # entero USD

        # Execute Stripe refund if applicable
        refund_id = None
        if refund_amount > 0:
            txn_result = await db.execute(
                select(PaymentModel).where(PaymentModel.booking_id == booking_id, PaymentModel.status == "completed")
            )
            txn = txn_result.scalar_one_or_none()
            if txn:
                pi_id = txn.metadata_info.get("payment_intent") if txn.metadata_info else None
                if pi_id:
                    stripe.api_key = STRIPE_API_KEY
                    try:
                        refund = await stripe.Refund.create_async(payment_intent=pi_id, amount=int(refund_amount * 100))
                        refund_id = refund.id
                    except Exception as se:
                        logger.error(f"Stripe refund error during cancellation: {se}")
                        raise HTTPException(status_code=502, detail=f"Refund failed: {str(se)}")

        booking.status = BookingStatus.CANCELLED
        await db.commit()

    await send_email(
        booking.client_email,
        "Reserva cancelada — RedMobility",
        f"<h2>Reserva cancelada</h2><p>Tu reserva <strong>{booking.booking_code}</strong> fue cancelada.</p>"
        f"<p>Reembolso: <strong>{refund_label}</strong> (${refund_amount})</p>"
        + (f"<p>Referencia del reembolso: {refund_id}</p>" if refund_id else "")
        + "<p>El reembolso puede tardar 5-10 días hábiles en reflejarse en tu tarjeta.</p>"
    )
    return {"message": "Booking cancelled", "refund_percentage": refund_label, "refund_amount": refund_amount, "refund_id": refund_id}


@router.post("/bookings/{booking_id}/dispute", status_code=201)
async def open_dispute(
    booking_id: str,
    body: OpenDisputeRequest,
    request: Request,
    authorization: Optional[str] = Header(None),
):
    """Cliente abre una disputa sobre una de sus reservas."""
    if not body.reason or len(body.reason.strip()) < 10:
        raise HTTPException(status_code=422, detail="El motivo debe tener al menos 10 caracteres")

    user = await require_auth(request, authorization)
    role_val = user.role.value if hasattr(user.role, 'value') else user.role
    if role_val != "client":
        raise HTTPException(status_code=403, detail="Solo clientes pueden abrir disputas")

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(BookingModel).where(BookingModel.id == booking_id))
        booking = result.scalar_one_or_none()
        if not booking:
            raise HTTPException(status_code=404, detail="Reserva no encontrada")
        if booking.client_id != user.id:
            raise HTTPException(status_code=403, detail="No tienes acceso a esta reserva")

        allowed_statuses = [BookingStatus.CONFIRMED, BookingStatus.ACTIVE, BookingStatus.COMPLETED]
        if booking.status not in allowed_statuses:
            raise HTTPException(
                status_code=400,
                detail="Solo puedes abrir una disputa en reservas confirmadas, activas o completadas"
            )

        # Verificar que no exista disputa abierta para esta reserva
        existing = await db.execute(
            select(DisputeModel).where(
                DisputeModel.booking_id == booking_id,
                DisputeModel.status == "open"
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Ya existe una disputa abierta para esta reserva")

        dispute = DisputeModel(
            booking_id=booking_id,
            opened_by=user.id,
            reason=body.reason.strip(),
        )
        db.add(dispute)
        await db.commit()
        await db.refresh(dispute)

    logger.info(f"Dispute opened: {dispute.id} for booking {booking_id} by client {user.id}")
    return {
        "dispute_id": dispute.id,
        "booking_id": booking_id,
        "status": dispute.status,
        "reason": dispute.reason,
        "created_at": dispute.created_at.isoformat() if dispute.created_at else None,
        "message": "Disputa abierta. El equipo de RedMobility la revisará en las próximas 24-48h."
    }
