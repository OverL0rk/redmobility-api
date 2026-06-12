"""
Shared dependencies — imported by all routers.
REGLA: este módulo NO importa de ningún router (evita circulares).
"""
import logging
import os
import time as _time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from fastapi import Header, HTTPException, Request
from passlib.context import CryptContext
from sqlalchemy import select

from database import AsyncSessionLocal
from models import (
    CommissionConfig as CommissionConfigModel,
    User as UserModel,
    UserSession as SessionModel,
)

# ── Variables de entorno (se leen aquí, una vez) ─────────────────
STRIPE_API_KEY        = os.environ.get("STRIPE_API_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
BREVO_API_KEY         = os.environ.get("BREVO_API_KEY", "")
SENDER_EMAIL          = os.environ.get("SENDER_EMAIL", "noreply@redmobility.com")
SENDER_NAME           = os.environ.get("SENDER_NAME", "RedMobility")
GOOGLE_CLIENT_ID      = os.environ.get("GOOGLE_CLIENT_ID", "")
FRONTEND_URL          = os.environ.get("FRONTEND_URL", "http://localhost:3000")
COOKIE_SECURE         = os.environ.get("COOKIE_SECURE", "false").lower() == "true"
UPLOAD_DIR            = Path(os.environ.get("UPLOAD_DIR", "/app/uploads"))
CORS_ORIGINS          = os.environ.get("CORS_ORIGINS", "http://localhost:3000")

# Whitelist de origins permitidos para el redirect de Stripe Checkout.
# Incluye CORS_ORIGINS (sin path) y FRONTEND_URL (con path, ej. /redmobility).
_ALLOWED_CHECKOUT_ORIGINS = (
    {o.strip().rstrip("/") for o in CORS_ORIGINS.split(",") if o.strip()}
    | ({FRONTEND_URL.strip().rstrip("/")} if FRONTEND_URL else set())
)

# ── Password hashing ─────────────────────────────────────────────
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ── Rate limiter in-memory (sin dependencias externas) ───────────
_rl_store: dict = defaultdict(list)


def _check_rate_limit(key: str, limit: int = 5, window: int = 60) -> bool:
    # NOTE: per-worker store — with multiple gunicorn workers each worker has its own dict.
    # Effective limit = limit × workers. Acceptable for current traffic; Redis needed to fix.
    now = _time.time()
    recent = [t for t in _rl_store[key] if now - t < window]
    if len(recent) >= limit:
        _rl_store[key] = recent
        # Purge keys with no recent activity (only on blocked requests — low overhead)
        stale = [k for k, v in list(_rl_store.items()) if not v]
        for k in stale:
            del _rl_store[k]
        return False
    recent.append(now)
    _rl_store[key] = recent
    return True


logger = logging.getLogger(__name__)

# ── Auth ─────────────────────────────────────────────────────────

async def get_current_user(
    request: Request, authorization: Optional[str] = None
) -> Optional[UserModel]:
    session_token = request.cookies.get("session_token")
    if not session_token and authorization:
        session_token = authorization.replace("Bearer ", "")
    if not session_token:
        return None
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(SessionModel).where(SessionModel.session_token == session_token)
        )
        session = result.scalar_one_or_none()
        if not session:
            return None
        if session.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
            return None
        result = await db.execute(
            select(UserModel).where(UserModel.id == session.user_id)
        )
        return result.scalar_one_or_none()


async def require_auth(
    request: Request, authorization: Optional[str] = Header(None)
) -> UserModel:
    user = await get_current_user(request, authorization)
    if not user:
        raise HTTPException(status_code=401, detail="No autenticado")
    return user


async def require_role(
    request: Request, required_role: str, authorization: Optional[str] = Header(None)
) -> UserModel:
    user = await require_auth(request, authorization)
    role_val = user.role.value if hasattr(user.role, "value") else user.role
    if role_val != required_role:
        raise HTTPException(status_code=403, detail=f"Requires {required_role} role")
    return user


# ── Email (SMTP Hostinger o Brevo) ───────────────────────────────
# Prioridad: 1) SMTP si SMTP_HOST está configurado (ej. smtp.hostinger.com)
#            2) Brevo API si hay BREVO_API_KEY
#            3) Log en consola (desarrollo)

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")


def _send_smtp_sync(to_email: str, subject: str, html_content: str):
    import smtplib
    from email.mime.text import MIMEText
    from email.utils import formataddr
    msg = MIMEText(html_content, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = formataddr((SENDER_NAME, SMTP_USER or SENDER_EMAIL))
    msg["To"] = to_email
    if SMTP_PORT == 465:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=15) as s:
            s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(SMTP_USER, [to_email], msg.as_string())
    else:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(SMTP_USER, [to_email], msg.as_string())


async def send_email(to_email: str, subject: str, html_content: str):
    if SMTP_HOST and SMTP_USER and SMTP_PASS:
        try:
            import asyncio as _asyncio
            await _asyncio.to_thread(_send_smtp_sync, to_email, subject, html_content)
            logger.info(f"Email (SMTP) sent: {subject} to {to_email}")
        except Exception as e:
            logger.error(f"Email SMTP error: {e}")
        return
    if not BREVO_API_KEY or BREVO_API_KEY == "TU_BREVO_KEY_AQUI":
        print(f"[EMAIL LOG] To: {to_email} Subject: {subject}")
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                "https://api.brevo.com/v3/smtp/email",
                headers={
                    "accept": "application/json",
                    "api-key": BREVO_API_KEY,
                    "content-type": "application/json",
                },
                json={
                    "sender": {"name": SENDER_NAME, "email": SENDER_EMAIL},
                    "to": [{"email": to_email}],
                    "subject": subject,
                    "htmlContent": html_content,
                },
            )
        if response.status_code == 201:
            logger.info(f"Email sent: {subject} to {to_email}")
        else:
            logger.error(f"Email failed: {response.json()}")
    except Exception as e:
        logger.error(f"Email error: {str(e)}")


# ── Comisiones ───────────────────────────────────────────────────

async def get_commission_rate(provider_id: str) -> float:
    """Tasa de comisión efectiva para un proveedor.
    Usa override por proveedor si existe y no expiró, si no usa global_rate."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(CommissionConfigModel))
        cfg = result.scalar_one_or_none()
    if not cfg:
        return 0.15
    overrides: dict = cfg.provider_overrides or {}
    if provider_id in overrides:
        override = overrides[provider_id]
        expires_at_str = override.get("expires_at")
        if expires_at_str:
            try:
                expires_at = datetime.fromisoformat(expires_at_str)
                if expires_at.date() >= datetime.utcnow().date():
                    return float(override["rate"])
            except ValueError:
                pass
        else:
            return float(override["rate"])
    return float(cfg.global_rate)
