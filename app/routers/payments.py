# Migrado desde server.py líneas 882–988
# 3 endpoints: checkout, payment status, stripe webhook
import logging
from typing import Optional

import stripe
from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy import select

from database import AsyncSessionLocal
from models import (
    Booking as BookingModel,
    PaymentTransaction as PaymentModel,
    BookingStatus,
)
from ..dependencies import (
    STRIPE_API_KEY,
    STRIPE_WEBHOOK_SECRET,
    _ALLOWED_CHECKOUT_ORIGINS,
    require_auth,
    send_email,
)

router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)


@router.post("/payments/checkout")
async def create_checkout(request: Request, booking_id: str, origin_url: str, authorization: Optional[str] = Header(None)):
    # ALTO-05: Validar origin_url contra lista blanca para evitar open redirect vía Stripe
    sanitized_origin = origin_url.rstrip("/")
    if sanitized_origin not in _ALLOWED_CHECKOUT_ORIGINS:
        raise HTTPException(status_code=400, detail="origin_url no permitido")

    user = await require_auth(request, authorization)
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(BookingModel).where(BookingModel.id == booking_id))
        booking = result.scalar_one_or_none()
        if not booking:
            raise HTTPException(status_code=404, detail="Booking not found")
        if booking.client_id != user.id:
            raise HTTPException(status_code=403, detail="Access denied")

        stripe.api_key = STRIPE_API_KEY
        try:
            session = await stripe.checkout.Session.create_async(
                payment_method_types=["card"],
                line_items=[{"price_data": {
                    "currency": "usd",
                    "product_data": {"name": f"Reserva {booking.booking_code}"},
                    "unit_amount": int(booking.total_price * 100),
                }, "quantity": 1}],
                mode="payment",
                success_url=f"{origin_url}/booking-confirmation?session_id={{CHECKOUT_SESSION_ID}}",
                cancel_url=f"{origin_url}/checkout",
                metadata={"booking_id": booking_id, "user_id": user.id}
            )
            txn = PaymentModel(
                booking_id=booking_id,
                session_id=session.id,
                amount=booking.total_price,
                metadata_info={"session_url": session.url}
            )
            db.add(txn)
            await db.commit()
            return {"url": session.url, "session_id": session.id}
        except Exception as e:
            logger.error(f"Stripe error: {str(e)}")
            raise HTTPException(status_code=400, detail=str(e))


@router.get("/payments/status/{session_id}")
async def get_payment_status(session_id: str, request: Request, authorization: Optional[str] = Header(None)):
    user = await require_auth(request, authorization)
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(PaymentModel).where(PaymentModel.session_id == session_id))
        txn = result.scalar_one_or_none()
        if not txn:
            raise HTTPException(status_code=404, detail="Transaction not found")
        result = await db.execute(select(BookingModel).where(BookingModel.id == txn.booking_id))
        booking = result.scalar_one_or_none()
        if booking and booking.client_id != user.id:
            raise HTTPException(status_code=403, detail="Access denied")

        stripe.api_key = STRIPE_API_KEY
        stripe_session = await stripe.checkout.Session.retrieve_async(session_id)

        if stripe_session.payment_status == "paid" and txn.status != "completed":
            txn.status = "completed"
            if booking:
                booking.status = BookingStatus.CONFIRMED
                await send_email(
                    booking.client_email,
                    "Confirmación de tu reserva - RedMobility",
                    f"<h1>¡Reserva confirmada!</h1><p>Tu código: <strong>{booking.booking_code}</strong></p>"
                )
            await db.commit()

        return {"status": stripe_session.status, "payment_status": stripe_session.payment_status, "booking_id": txn.booking_id}


@router.post("/webhook/stripe")
async def stripe_webhook(request: Request):
    body = await request.body()
    signature = request.headers.get("stripe-signature")
    stripe.api_key = STRIPE_API_KEY
    try:
        if not STRIPE_WEBHOOK_SECRET:
            logger.error("STRIPE_WEBHOOK_SECRET not configured — webhook rejected")
            raise HTTPException(status_code=400, detail="Webhook secret not configured")
        if not signature:
            raise HTTPException(status_code=400, detail="Missing stripe-signature header")
        event = stripe.Webhook.construct_event(body, signature, STRIPE_WEBHOOK_SECRET)
        if event['type'] == 'checkout.session.completed':
            stripe_obj = event['data']['object']
            sid = stripe_obj['id']
            payment_intent = stripe_obj.get('payment_intent')  # CRITICAL: save for refunds
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(PaymentModel).where(PaymentModel.session_id == sid))
                txn = result.scalar_one_or_none()
                if txn:
                    txn.status = "completed"
                    # Store payment_intent so cancellations and dispute refunds can use it
                    existing_meta = txn.metadata_info or {}
                    txn.metadata_info = {**existing_meta, "payment_intent": payment_intent, "session_url": existing_meta.get("session_url")}
                    result = await db.execute(select(BookingModel).where(BookingModel.id == txn.booking_id))
                    booking = result.scalar_one_or_none()
                    if booking:
                        booking.status = BookingStatus.CONFIRMED
                    await db.commit()
                    logger.info(f"Payment confirmed — session {sid} | payment_intent {payment_intent} | booking {txn.booking_id}")
        return {"received": True}
    except Exception as e:
        logger.error(f"Webhook error: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
