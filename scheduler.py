"""
RedMobility Background Jobs
Uses asyncio tasks started from FastAPI startup event.
Jobs run periodically without external dependencies.
"""
import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta

import stripe
from sqlalchemy import select

from database import AsyncSessionLocal
from models import (
    Booking as BookingModel, BookingStatus,
    PaymentTransaction as PaymentModel,
    Review as ReviewModel,
    User as UserModel,
    Asset as AssetModel,
)

logger = logging.getLogger(__name__)

STRIPE_API_KEY = os.environ.get('STRIPE_API_KEY', '')
BREVO_API_KEY = os.environ.get('BREVO_API_KEY', '')
SENDER_EMAIL = os.environ.get('SENDER_EMAIL', 'noreply@redmobility.com')
SENDER_NAME = os.environ.get('SENDER_NAME', 'RedMobility')


async def _send_email_bg(to_email: str, subject: str, html_content: str):
    """Fire-and-forget email using httpx async."""
    if not BREVO_API_KEY:
        logger.info(f"[EMAIL-BG] To: {to_email} | Subject: {subject}")
        return
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                "https://api.brevo.com/v3/smtp/email",
                headers={"api-key": BREVO_API_KEY, "content-type": "application/json"},
                json={
                    "sender": {"name": SENDER_NAME, "email": SENDER_EMAIL},
                    "to": [{"email": to_email}],
                    "subject": subject,
                    "htmlContent": html_content,
                }
            )
    except Exception as e:
        logger.error(f"Background email error: {e}")


async def job_cancel_unconfirmed_bookings():
    """
    BUG-05: Auto-cancel PENDING bookings older than 2 hours.
    Sends refund via Stripe and notification email to client.
    Runs every 5 minutes.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=2)
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(BookingModel).where(
                BookingModel.status == BookingStatus.PENDING,
                BookingModel.created_at < cutoff,
            )
        )
        stale = result.scalars().all()
        for booking in stale:
            logger.info(f"[SCHEDULER] Auto-cancelling unconfirmed booking {booking.booking_code}")
            # Find completed payment transaction
            txn_res = await db.execute(
                select(PaymentModel).where(
                    PaymentModel.booking_id == booking.id,
                    PaymentModel.status == "completed",
                )
            )
            txn = txn_res.scalar_one_or_none()
            refund_id = None
            if txn and txn.metadata_info:
                pi_id = txn.metadata_info.get("payment_intent")
                if pi_id and STRIPE_API_KEY:
                    try:
                        stripe.api_key = STRIPE_API_KEY
                        refund = await stripe.Refund.create_async(
                            payment_intent=pi_id,
                            amount=int(booking.total_price * 100),
                        )
                        refund_id = refund.id
                        logger.info(f"[SCHEDULER] Refund {refund_id} issued for {booking.booking_code}")
                    except Exception as se:
                        logger.error(f"[SCHEDULER] Stripe refund failed for {booking.booking_code}: {se}")

            booking.status = BookingStatus.CANCELLED
            await db.commit()

            await _send_email_bg(
                booking.client_email,
                "Tu reserva fue cancelada automáticamente — RedMobility",
                f"""
                <h2>Reserva cancelada</h2>
                <p>El proveedor no confirmó tu reserva <strong>{booking.booking_code}</strong> en el tiempo límite (2 horas).</p>
                <p>Se ha procesado un <strong>reembolso completo</strong> de ${booking.total_price}.</p>
                {f'<p>Referencia: {refund_id}</p>' if refund_id else ''}
                <p>El reembolso puede tardar 5-10 días hábiles.</p>
                <p>Te invitamos a buscar otro activo disponible en RedMobility.</p>
                """
            )


async def job_complete_confirmed_bookings():
    """
    P0: Auto-complete CONFIRMED/ACTIVE bookings where service end has passed.
    start_datetime + end_hours <= now → COMPLETED.
    Unblocks payout job and review email job.
    Runs every 10 minutes.
    """
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(BookingModel).where(
                BookingModel.status.in_([BookingStatus.CONFIRMED, BookingStatus.ACTIVE]),
            )
        )
        active = result.scalars().all()
        completed_count = 0
        for booking in active:
            service_end = booking.start_datetime + timedelta(hours=booking.end_hours)
            if service_end <= now:
                booking.status = BookingStatus.COMPLETED
                completed_count += 1
                logger.info(
                    f"[SCHEDULER] Auto-completed {booking.booking_code} — "
                    f"service ended {service_end.isoformat()}"
                )
        if completed_count:
            await db.commit()


async def job_payout_completed_bookings():
    """
    NEG: Auto-mark provider payout ready for bookings completed 48+ hours ago.
    In production this would trigger the actual bank transfer via Stripe Connect/Payouts.
    For now: logs the payout and sends email to provider.
    Runs every 30 minutes.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(BookingModel).where(
                BookingModel.status == BookingStatus.COMPLETED,
            )
        )
        completed = result.scalars().all()

        for booking in completed:
            # Only process bookings completed 48h+ ago that haven't been paid out
            # We use a simple heuristic: check if a payout marker exists
            # In production: check a ProviderPayout table
            if not booking.created_at:
                continue
            # Use start_datetime + end_hours as service end time
            service_end = booking.start_datetime + timedelta(hours=booking.end_hours)
            if service_end > cutoff:
                continue  # Not 48h yet

            # Check for open dispute
            from models import Dispute as DisputeModel
            dispute_res = await db.execute(
                select(DisputeModel).where(
                    DisputeModel.booking_id == booking.id,
                    DisputeModel.status == "open",
                )
            )
            if dispute_res.scalar_one_or_none():
                logger.info(f"[SCHEDULER] Payout skipped for {booking.booking_code} — open dispute")
                continue

            # CRIT-02: skip if payout already sent — evita emails duplicados cada 30 min
            if booking.payout_sent:
                continue

            payout_amount = booking.total_price - booking.platform_commission - booking.insurance_price
            logger.info(
                f"[SCHEDULER] PAYOUT DUE: {booking.booking_code} | "
                f"Provider: {booking.provider_id} | Amount: ${payout_amount}"
            )

            # Marcar como enviado ANTES del email para evitar doble envío si el proceso falla a mitad
            booking.payout_sent = True
            await db.commit()

            # Notify provider
            prov_res = await db.execute(select(UserModel).where(UserModel.id == booking.provider_id))
            provider = prov_res.scalar_one_or_none()
            if provider and provider.email:
                await _send_email_bg(
                    provider.email,
                    f"Pago procesado — {booking.booking_code} — RedMobility",
                    f"""
                    <h2>¡Tu pago está en camino!</h2>
                    <p>El pago de la reserva <strong>{booking.booking_code}</strong> ha sido procesado.</p>
                    <table>
                      <tr><td><b>Monto neto:</b></td><td>${payout_amount}</td></tr>
                      <tr><td><b>Total de la reserva:</b></td><td>${booking.total_price}</td></tr>
                      <tr><td><b>Comisión de plataforma:</b></td><td>${booking.platform_commission}</td></tr>
                    </table>
                    <p>El monto será acreditado a tu cuenta registrada en los próximos 1-2 días hábiles.</p>
                    """
                )


async def job_request_reviews():
    """
    Send review request emails to clients 2 hours after their booking is completed.
    Runs every 15 minutes.
    """
    # Find bookings completed between 2h and 4h ago (2h window to avoid repeated sends)
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(hours=4)
    window_end = now - timedelta(hours=2)

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(BookingModel).where(
                BookingModel.status == BookingStatus.COMPLETED,
            )
        )
        completed = result.scalars().all()

        for booking in completed:
            service_end = booking.start_datetime + timedelta(hours=booking.end_hours)
            if not (window_start <= service_end <= window_end):
                continue

            # Check if review already exists
            rev_res = await db.execute(
                select(ReviewModel).where(ReviewModel.booking_id == booking.id)
            )
            if rev_res.scalar_one_or_none():
                continue  # Already reviewed

            # Get asset name
            asset_res = await db.execute(select(AssetModel).where(AssetModel.id == booking.asset_id))
            asset = asset_res.scalar_one_or_none()
            asset_name = asset.name if asset else "tu alquiler"

            await _send_email_bg(
                booking.client_email,
                f"¿Cómo fue tu experiencia? — {booking.booking_code}",
                f"""
                <h2>¡Hola {booking.client_name}!</h2>
                <p>Esperamos que hayas disfrutado <strong>{asset_name}</strong>.</p>
                <p>Tu opinión ayuda a otros viajeros a tomar mejores decisiones.</p>
                <p>
                  <a href="{os.environ.get('FRONTEND_URL', 'http://localhost:3000')}/client/dashboard"
                     style="background:#134168;color:white;padding:12px 24px;border-radius:24px;text-decoration:none;font-weight:bold;">
                    Escribir reseña
                  </a>
                </p>
                <p>Solo toma 1 minuto. ¡Gracias!</p>
                """
            )
            logger.info(f"[SCHEDULER] Review request sent for {booking.booking_code}")


async def job_send_service_reminders():
    """
    Send reminder emails to both client and provider 24h before the service.
    Runs every 30 minutes. reminder_sent flag prevents duplicate sends.
    """
    now = datetime.now(timezone.utc)
    window_start = now + timedelta(hours=23, minutes=30)
    window_end = now + timedelta(hours=24, minutes=30)

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(BookingModel).where(
                BookingModel.status == BookingStatus.CONFIRMED,
                BookingModel.reminder_sent == False,
                BookingModel.start_datetime >= window_start,
                BookingModel.start_datetime <= window_end,
            )
        )
        upcoming = result.scalars().all()

        for booking in upcoming:
            asset_res = await db.execute(select(AssetModel).where(AssetModel.id == booking.asset_id))
            asset = asset_res.scalar_one_or_none()
            asset_name = asset.name if asset else "tu servicio"
            start_str = booking.start_datetime.strftime('%d/%m/%Y %H:%M')

            # Reminder to client
            await _send_email_bg(
                booking.client_email,
                f"Recordatorio: tu reserva es mañana — {booking.booking_code}",
                f"""
                <h2>¡Tu aventura es mañana!</h2>
                <p>Hola {booking.client_name}, te recordamos que tu reserva de <strong>{asset_name}</strong>
                está programada para <strong>{start_str}</strong>.</p>
                <p>Código de reserva: <strong>{booking.booking_code}</strong></p>
                <p>El proveedor se pondrá en contacto contigo para confirmar el punto de encuentro.</p>
                <p>¡Disfruta tu experiencia con RedMobility!</p>
                """
            )

            # Reminder to provider
            prov_res = await db.execute(select(UserModel).where(UserModel.id == booking.provider_id))
            provider = prov_res.scalar_one_or_none()
            if provider and provider.email:
                await _send_email_bg(
                    provider.email,
                    f"Recordatorio: servicio mañana — {booking.booking_code}",
                    f"""
                    <h2>Tienes un servicio mañana</h2>
                    <p>La reserva <strong>{booking.booking_code}</strong> para <strong>{asset_name}</strong>
                    está programada para <strong>{start_str}</strong>.</p>
                    <p>Cliente: <strong>{booking.client_name}</strong></p>
                    <p>Asegúrate de que el activo esté listo y contacta al cliente para confirmar el punto de encuentro.</p>
                    """
                )

            booking.reminder_sent = True
            logger.info(f"[SCHEDULER] Service reminder sent for {booking.booking_code} — starts {start_str}")

        await db.commit()


async def _run_job_safely(job_fn, name: str):
    try:
        await job_fn()
    except Exception as e:
        logger.error(f"[SCHEDULER] Job '{name}' failed: {e}", exc_info=True)


async def scheduler_loop():
    """
    Main scheduler loop. Runs all jobs at their respective intervals.
    Uses asyncio.sleep for scheduling — no external dependency.
    """
    logger.info("[SCHEDULER] Background job scheduler started")
    tick = 0
    while True:
        await asyncio.sleep(60)  # Base tick: 1 minute
        tick += 1

        # Every 5 minutes: cancel unconfirmed bookings
        if tick % 5 == 0:
            await _run_job_safely(job_cancel_unconfirmed_bookings, "cancel_unconfirmed")

        # Every 10 minutes: auto-complete finished bookings (P0)
        if tick % 10 == 0:
            await _run_job_safely(job_complete_confirmed_bookings, "complete_confirmed")

        # Every 15 minutes: send review requests
        if tick % 15 == 0:
            await _run_job_safely(job_request_reviews, "request_reviews")

        # Every 30 minutes: process payouts + send service reminders
        if tick % 30 == 0:
            await _run_job_safely(job_payout_completed_bookings, "payout_completed")
            await _run_job_safely(job_send_service_reminders, "service_reminders")


def start_scheduler() -> asyncio.Task:
    """Call from FastAPI startup to launch the scheduler as a background task."""
    return asyncio.get_event_loop().create_task(scheduler_loop())
