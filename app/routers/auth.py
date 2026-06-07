# Migrado desde server.py líneas 257–553
# 10 endpoints: google, register, login, me (GET/PATCH), verify-email,
# resend-verification, forgot-password, reset-password, logout
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr
from sqlalchemy import select

from database import AsyncSessionLocal
from models import (
    EmailVerificationToken as EmailVerificationTokenModel,
    PasswordResetToken as PasswordResetTokenModel,
    User as UserModel,
    UserSession as SessionModel,
    UserRole,
)
from ..dependencies import (
    COOKIE_SECURE,
    FRONTEND_URL,
    GOOGLE_CLIENT_ID,
    _check_rate_limit,
    get_current_user,
    pwd_context,
    require_auth,
    send_email,
)
from ..utils import user_to_dict

router = APIRouter(prefix="/api")

# ── Validadores de identidad dominicanos ─────────────────────────
import re as _re

_CEDULA_RE = _re.compile(r'^\d{3}-\d{7}-\d{1}$')          # 001-1234567-8
_PASSPORT_RE = _re.compile(r'^[A-Z0-9]{6,20}$', _re.I)   # AA123456 / alphanumeric 6-20

def _validate_document(doc: str) -> str:
    """Returns error message or empty string if valid."""
    doc = doc.strip()
    if not doc:
        return "El documento de identidad es requerido"
    # Detect cédula by format XXX-XXXXXXX-X
    if _re.match(r'^\d', doc):
        if not _CEDULA_RE.match(doc):
            return "Cédula inválida. Formato requerido: 001-1234567-8"
    else:
        if not _PASSPORT_RE.match(doc):
            return "Pasaporte inválido. Solo letras y números, entre 6 y 20 caracteres"
    return ""

def _validate_phone(phone: str) -> str:
    """Returns error message or empty string if valid."""
    digits = _re.sub(r'\D', '', phone)
    if len(digits) < 10:
        return "Teléfono inválido. Debe tener al menos 10 dígitos (ej: +1-809-555-0100)"
    return ""

# ── Pydantic schemas (solo usados en este router) ────────────────

class GoogleAuthRequest(BaseModel):
    token: str

class EmailRegisterRequest(BaseModel):
    email: EmailStr
    password: str
    name: str
    role: str = "client"
    phone_whatsapp: Optional[str] = None
    document_id: Optional[str] = None

class EmailLoginRequest(BaseModel):
    email: EmailStr
    password: str

class UserProfileUpdate(BaseModel):
    name: Optional[str] = None
    phone_whatsapp: Optional[str] = None
    document_id: Optional[str] = None
    # allow clients to mark their KYC as submitted after completing profile
    kyc_submitted: Optional[bool] = None

class VerifyEmailRequest(BaseModel):
    token: str

class ResendVerificationRequest(BaseModel):
    email: EmailStr

class ForgotPasswordRequest(BaseModel):
    email: EmailStr

class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


# ── Endpoints ────────────────────────────────────────────────────

@router.post("/auth/google")
async def google_auth(request: Request, response: Response, auth_data: GoogleAuthRequest):
    """Authenticate user with Google ID token"""
    from google.oauth2 import id_token
    from google.auth.transport import requests as google_requests
    try:
        idinfo = id_token.verify_oauth2_token(auth_data.token, google_requests.Request(), GOOGLE_CLIENT_ID)
        email = idinfo['email']
        name = idinfo.get('name', 'User')
        picture = idinfo.get('picture', '')

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(UserModel).where(UserModel.email == email))
            user = result.scalar_one_or_none()
            if not user:
                user = UserModel(email=email, name=name, picture=picture, role=UserRole.CLIENT)
                db.add(user)
                await db.commit()
                await db.refresh(user)

            session_token = f"session_{uuid.uuid4().hex}"
            session = SessionModel(
                user_id=user.id,
                session_token=session_token,
                expires_at=datetime.now(timezone.utc) + timedelta(days=7)
            )
            db.add(session)
            await db.commit()

        response.set_cookie(key="session_token", value=session_token, httponly=True,
                            secure=COOKIE_SECURE, samesite="lax", max_age=7*24*60*60, path="/")
        return {"user": user_to_dict(user)}
    except ValueError as e:
        raise HTTPException(status_code=401, detail="Token inválido")


@router.post("/auth/register")
async def email_register(request: Request, response: Response, auth_data: EmailRegisterRequest):
    client_ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(f"register:{client_ip}", limit=5, window=60):
        raise HTTPException(status_code=429, detail="Demasiados intentos de registro. Espera un minuto.")

    # Validate phone if provided at registration time
    if auth_data.phone_whatsapp:
        err = _validate_phone(auth_data.phone_whatsapp)
        if err:
            raise HTTPException(status_code=422, detail=err)

    # Validate document if provided at registration time
    if auth_data.document_id:
        err = _validate_document(auth_data.document_id)
        if err:
            raise HTTPException(status_code=422, detail=err)

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(UserModel).where(UserModel.email == auth_data.email))
        if result.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Este correo ya está registrado")

        role_enum = UserRole.PROVIDER if auth_data.role == "provider" else UserRole.CLIENT
        user = UserModel(
            email=auth_data.email,
            name=auth_data.name,
            hashed_password=pwd_context.hash(auth_data.password),
            role=role_enum,
            phone_whatsapp=auth_data.phone_whatsapp or None,
            document_id=auth_data.document_id or None,
            verified=False  # Requiere verificación de email para ambos roles
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

        # Generar token de verificación de email
        ev_token = uuid.uuid4().hex
        ev_record = EmailVerificationTokenModel(
            user_id=user.id,
            token=ev_token,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=48)
        )
        db.add(ev_record)

        session_token = f"session_{uuid.uuid4().hex}"
        session = SessionModel(
            user_id=user.id,
            session_token=session_token,
            expires_at=datetime.now(timezone.utc) + timedelta(days=7)
        )
        db.add(session)
        await db.commit()

    await send_email(
        user.email,
        "Verifica tu cuenta — RedMobility",
        f"""<h2>¡Bienvenido a RedMobility, {user.name}!</h2>
        <p>Por favor verifica tu correo haciendo clic en el siguiente enlace:</p>
        <p><a href="{FRONTEND_URL}/verify-email?token={ev_token}"
           style="background:#134168;color:white;padding:12px 24px;border-radius:24px;text-decoration:none;font-weight:bold;">
          Verificar mi cuenta
        </a></p>
        <p>Este enlace expira en 48 horas.</p>
        <p>Si no creaste esta cuenta, puedes ignorar este email.</p>"""
    )

    response.set_cookie(key="session_token", value=session_token, httponly=True,
                        secure=COOKIE_SECURE, samesite="lax", max_age=7*24*60*60, path="/")
    return {"user": user_to_dict(user), "email_verification_sent": True}


@router.post("/auth/login")
async def email_login(request: Request, response: Response, auth_data: EmailLoginRequest):
    client_ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(f"login:{client_ip}", limit=10, window=60):
        raise HTTPException(status_code=429, detail="Demasiados intentos de inicio de sesión. Espera un minuto.")
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(UserModel).where(UserModel.email == auth_data.email))
        user = result.scalar_one_or_none()

        if not user or not user.hashed_password or not pwd_context.verify(auth_data.password, user.hashed_password):
            raise HTTPException(status_code=401, detail="Email o contraseña incorrectos")

        session_token = f"session_{uuid.uuid4().hex}"
        session = SessionModel(
            user_id=user.id,
            session_token=session_token,
            expires_at=datetime.now(timezone.utc) + timedelta(days=7)
        )
        db.add(session)
        await db.commit()

    response.set_cookie(key="session_token", value=session_token, httponly=True,
                        secure=COOKIE_SECURE, samesite="lax", max_age=7*24*60*60, path="/")
    return {"user": user_to_dict(user)}


@router.get("/auth/me")
async def get_me(request: Request, authorization: Optional[str] = Header(None)):
    user = await get_current_user(request, authorization)
    if not user:
        raise HTTPException(status_code=401, detail="No autenticado")
    return user_to_dict(user)


@router.patch("/auth/me")
async def update_profile(update_data: UserProfileUpdate, request: Request, authorization: Optional[str] = Header(None)):
    user = await require_auth(request, authorization)
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(UserModel).where(UserModel.id == user.id))
        db_user = result.scalar_one_or_none()
        if not db_user:
            raise HTTPException(status_code=404, detail="Usuario no encontrado")
        if update_data.name is not None:
            db_user.name = update_data.name
        if update_data.phone_whatsapp is not None:
            err = _validate_phone(update_data.phone_whatsapp)
            if err:
                raise HTTPException(status_code=422, detail=err)
            db_user.phone_whatsapp = update_data.phone_whatsapp
        if update_data.document_id is not None:
            err = _validate_document(update_data.document_id)
            if err:
                raise HTTPException(status_code=422, detail=err)
            db_user.document_id = update_data.document_id
        await db.commit()
        await db.refresh(db_user)
    return user_to_dict(db_user)


@router.post("/auth/verify-email")
async def verify_email(body: VerifyEmailRequest):
    """Marca el email como verificado usando el token enviado al registro."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(EmailVerificationTokenModel).where(EmailVerificationTokenModel.token == body.token)
        )
        record = result.scalar_one_or_none()
        if not record:
            raise HTTPException(status_code=400, detail="Token de verificación inválido")
        if record.used:
            raise HTTPException(status_code=400, detail="Este enlace ya fue utilizado")
        if record.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
            raise HTTPException(status_code=400, detail="El enlace de verificación ha expirado")
        user_result = await db.execute(select(UserModel).where(UserModel.id == record.user_id))
        user = user_result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="Usuario no encontrado")
        user.verified = True
        record.used = True
        await db.commit()
    return {"message": "Email verificado correctamente"}


@router.post("/auth/resend-verification")
async def resend_verification(body: ResendVerificationRequest):
    """Reenvía el email de verificación si la cuenta aún no está verificada."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(UserModel).where(UserModel.email == body.email))
        user = result.scalar_one_or_none()
        # Respuesta genérica para no revelar si el email existe
        if not user or user.verified:
            return {"message": "Si el correo existe y no está verificado, recibirás un email"}
        ev_token = uuid.uuid4().hex
        ev_record = EmailVerificationTokenModel(
            user_id=user.id,
            token=ev_token,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=48)
        )
        db.add(ev_record)
        await db.commit()
    await send_email(
        user.email,
        "Verifica tu cuenta — RedMobility",
        f"""<h2>Verifica tu cuenta en RedMobility</h2>
        <p><a href="{FRONTEND_URL}/verify-email?token={ev_token}"
           style="background:#134168;color:white;padding:12px 24px;border-radius:24px;text-decoration:none;">
          Verificar mi cuenta
        </a></p>
        <p>Este enlace expira en 48 horas.</p>"""
    )
    return {"message": "Si el correo existe y no está verificado, recibirás un email"}


@router.post("/auth/forgot-password")
async def forgot_password(request: Request, body: ForgotPasswordRequest):
    """Genera token de reset y envía email. Respuesta siempre genérica (anti-enumeration)."""
    client_ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(f"forgot:{client_ip}", limit=3, window=60):
        raise HTTPException(status_code=429, detail="Demasiados intentos. Espera un minuto.")

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(UserModel).where(UserModel.email == body.email))
        user = result.scalar_one_or_none()
        if user and user.hashed_password:  # Solo usuarios con contraseña (no solo Google)
            reset_token = uuid.uuid4().hex
            prt = PasswordResetTokenModel(
                user_id=user.id,
                token=reset_token,
                expires_at=datetime.now(timezone.utc) + timedelta(hours=2)
            )
            db.add(prt)
            await db.commit()
            await send_email(
                user.email,
                "Restablece tu contraseña — RedMobility",
                f"""<h2>Solicitud de cambio de contraseña</h2>
                <p>Haz clic en el siguiente enlace para restablecer tu contraseña:</p>
                <p><a href="{FRONTEND_URL}/reset-password?token={reset_token}"
                   style="background:#134168;color:white;padding:12px 24px;border-radius:24px;text-decoration:none;">
                  Restablecer contraseña
                </a></p>
                <p>Este enlace expira en 2 horas. Si no solicitaste esto, ignora este email.</p>"""
            )
    # Siempre responder igual — no revela si el email existe
    return {"message": "Si existe una cuenta con ese correo, recibirás instrucciones"}


@router.post("/auth/reset-password")
async def reset_password(body: ResetPasswordRequest):
    """Valida token y actualiza la contraseña."""
    if len(body.new_password) < 8:
        raise HTTPException(status_code=422, detail="La contraseña debe tener al menos 8 caracteres")
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(PasswordResetTokenModel).where(PasswordResetTokenModel.token == body.token)
        )
        prt = result.scalar_one_or_none()
        if not prt:
            raise HTTPException(status_code=400, detail="Token inválido o expirado")
        if prt.used:
            raise HTTPException(status_code=400, detail="Este enlace ya fue utilizado")
        if prt.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
            raise HTTPException(status_code=400, detail="El enlace ha expirado. Solicita uno nuevo.")
        user_result = await db.execute(select(UserModel).where(UserModel.id == prt.user_id))
        user = user_result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="Usuario no encontrado")
        user.hashed_password = pwd_context.hash(body.new_password)
        prt.used = True
        await db.commit()
    return {"message": "Contraseña actualizada correctamente"}


@router.post("/auth/logout")
async def logout(request: Request, response: Response, authorization: Optional[str] = Header(None)):
    session_token = request.cookies.get("session_token")
    if not session_token and authorization:
        session_token = authorization.replace("Bearer ", "")
    if session_token:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(SessionModel).where(SessionModel.session_token == session_token)
            )
            session = result.scalar_one_or_none()
            if session:
                await db.delete(session)
                await db.commit()
    response.delete_cookie("session_token", path="/")
    return {"message": "Logged out"}
