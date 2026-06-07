from sqlalchemy import Column, String, Float, Boolean, ForeignKey, DateTime, Enum, Text, JSON, Integer, Numeric  # Float kept for legacy compatibility only
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
import uuid
from database import Base
import enum

class UserRole(str, enum.Enum):
    CLIENT = "client"
    PROVIDER = "provider"
    ADMIN = "admin"

class BookingStatus(str, enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    REJECTED = "rejected"

class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, default=lambda: f"user_{uuid.uuid4().hex[:12]}")
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=True)
    name = Column(String, nullable=False)
    picture = Column(String, nullable=True)
    phone_whatsapp = Column(String, nullable=True)
    document_id = Column(String, nullable=True)
    role = Column(Enum(UserRole, values_callable=lambda x: [e.value for e in x]), default=UserRole.CLIENT, nullable=False)
    verified = Column(Boolean, default=False)
    is_banned = Column(Boolean, default=False, nullable=False)
    ban_reason = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    assets = relationship("Asset", back_populates="provider", lazy="selectin", foreign_keys="Asset.provider_id")

class Asset(Base):
    __tablename__ = "assets"
    id = Column(String, primary_key=True, default=lambda: f"asset_{uuid.uuid4().hex[:12]}")
    provider_id = Column(String, ForeignKey("users.id"), nullable=False)
    type = Column(String, nullable=False)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    brand = Column(String, nullable=True)
    model = Column(String, nullable=True)
    year = Column(Integer, nullable=True)
    capacity = Column(Integer, nullable=True)
    price_per_hour = Column(Integer, nullable=True)   # USD enteros, sin centavos
    price_per_day = Column(Integer, nullable=False)   # USD enteros, sin centavos
    location_zone = Column(String, nullable=False)
    pickup_address = Column(String, nullable=True)
    what_included = Column(JSON, nullable=True)
    is_active = Column(Boolean, default=True)
    rating = Column(Float, default=0.0)
    total_rentals = Column(Integer, default=0)
    buffer_before_hours = Column(Float, default=2.0, nullable=False)
    buffer_after_hours  = Column(Float, default=2.0, nullable=False)
    # Delivery (Modelo C: el proveedor decide)
    delivery_available  = Column(Boolean, default=False, nullable=False)  # ofrece delivery?
    delivery_radius_km  = Column(Float, default=0.0, nullable=False)       # radio máximo en km
    delivery_cost_usd   = Column(Integer, default=0, nullable=False)        # 0 = gratis
    provider = relationship("User", back_populates="assets", foreign_keys=[provider_id])
    images = relationship("AssetImage", back_populates="asset", lazy="selectin", cascade="all, delete-orphan")
    bookings = relationship("Booking", back_populates="asset", lazy="noload")

class AssetImage(Base):
    __tablename__ = "asset_images"
    id = Column(String, primary_key=True, default=lambda: f"img_{uuid.uuid4().hex[:12]}")
    asset_id = Column(String, ForeignKey("assets.id"), nullable=False)
    url = Column(String, nullable=False)
    is_primary = Column(Boolean, default=False)
    asset = relationship("Asset", back_populates="images")

class Booking(Base):
    __tablename__ = "bookings"
    id = Column(String, primary_key=True, default=lambda: f"booking_{uuid.uuid4().hex[:12]}")
    booking_code = Column(String, nullable=False, unique=True)
    asset_id = Column(String, ForeignKey("assets.id"), nullable=False)
    client_id = Column(String, ForeignKey("users.id"), nullable=False)
    provider_id = Column(String, ForeignKey("users.id"), nullable=False)
    start_datetime = Column(DateTime(timezone=True), nullable=False)
    end_hours = Column(Integer, nullable=False)
    total_price = Column(Integer, nullable=False)          # USD enteros
    platform_commission = Column(Integer, nullable=False)  # USD enteros
    insurance_type = Column(String, default="none")
    insurance_price = Column(Integer, default=0)           # USD enteros
    status = Column(Enum(BookingStatus, values_callable=lambda x: [e.value for e in x]), default=BookingStatus.PENDING, nullable=False)
    client_name = Column(String, nullable=False)
    client_email = Column(String, nullable=False)
    client_phone = Column(String, nullable=False)
    client_document = Column(String, nullable=False)
    delivery_requested  = Column(Boolean, default=False, nullable=False)   # cliente pide delivery
    delivery_address    = Column(Text, nullable=True)                       # dirección de entrega
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    payout_sent = Column(Boolean, default=False, nullable=False)
    reminder_sent = Column(Boolean, default=False, nullable=False)
    asset = relationship("Asset", back_populates="bookings")

class PaymentTransaction(Base):
    __tablename__ = "payment_transactions"
    id = Column(String, primary_key=True, default=lambda: f"txn_{uuid.uuid4().hex[:12]}")
    booking_id = Column(String, ForeignKey("bookings.id"), nullable=False)
    session_id = Column(String, nullable=False, unique=True)
    amount = Column(Integer, nullable=False)  # USD enteros
    currency = Column(String, default="usd")
    method = Column(String, default="card")
    status = Column(String, default="pending")
    metadata_info = Column(JSON, nullable=True)

class UserSession(Base):
    __tablename__ = "user_sessions"
    id = Column(String, primary_key=True, default=lambda: f"session_{uuid.uuid4().hex}")
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    session_token = Column(String, nullable=False, unique=True, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)

class Review(Base):
    __tablename__ = "reviews"
    id = Column(String, primary_key=True, default=lambda: f"rev_{uuid.uuid4().hex[:12]}")
    booking_id = Column(String, ForeignKey("bookings.id"), nullable=False)
    client_id = Column(String, ForeignKey("users.id"), nullable=False)
    asset_id = Column(String, ForeignKey("assets.id"), nullable=False)
    provider_id = Column(String, ForeignKey("users.id"), nullable=False)
    rating = Column(Integer, nullable=False)
    comment = Column(Text, nullable=False)
    status = Column(String, default="pending")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"
    id = Column(String, primary_key=True, default=lambda: f"prt_{uuid.uuid4().hex}")
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    token = Column(String, nullable=False, unique=True, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class EmailVerificationToken(Base):
    __tablename__ = "email_verification_tokens"
    id = Column(String, primary_key=True, default=lambda: f"evt_{uuid.uuid4().hex}")
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    token = Column(String, nullable=False, unique=True, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Dispute(Base):
    __tablename__ = "disputes"
    id = Column(String, primary_key=True, default=lambda: f"dispute_{uuid.uuid4().hex[:12]}")
    booking_id = Column(String, ForeignKey("bookings.id"), nullable=False)
    opened_by = Column(String, ForeignKey("users.id"), nullable=False)
    reason = Column(Text, nullable=False)
    status = Column(String, default="open")
    resolution = Column(Text, nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Category(Base):
    """Catálogo de tipos de activo gestionable por el admin.
    El id es el slug usado en Asset.type (ej: 'car', 'jet_ski').
    """
    __tablename__ = "categories"
    id = Column(String, primary_key=True)          # slug: "car", "bicycle", etc.
    name = Column(String, nullable=False)           # "Carro", "Bicicleta"
    icon = Column(String, nullable=True)            # emoji: "🚗"
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class CommissionConfig(Base):
    """Single-row table for platform-wide commission settings.

    provider_overrides JSON structure:
        { "<provider_id>": {"rate": 0.10, "expires_at": "2025-12-31"}, ... }
    """
    __tablename__ = "commission_config"
    id = Column(
        String,
        primary_key=True,
        default=lambda: f"cfg_{uuid.uuid4().hex[:12]}",
    )
    global_rate = Column(Numeric(5, 4), nullable=False, default=0.15)
    provider_overrides = Column(JSON, nullable=True)
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class Favorite(Base):
    """Cliente guarda un activo en su lista de deseos."""
    __tablename__ = "favorites"
    id         = Column(String, primary_key=True, default=lambda: f"fav_{uuid.uuid4().hex[:12]}")
    client_id  = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    asset_id   = Column(String, ForeignKey("assets.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class BlockedDate(Base):
    """Proveedor bloquea un rango de fechas para un activo (mantenimiento, uso personal, etc.)."""
    __tablename__ = "blocked_dates"
    id          = Column(String, primary_key=True, default=lambda: f"blk_{uuid.uuid4().hex[:12]}")
    asset_id    = Column(String, ForeignKey("assets.id"), nullable=False, index=True)
    provider_id = Column(String, ForeignKey("users.id"), nullable=False)
    start_date  = Column(DateTime(timezone=True), nullable=False)
    end_date    = Column(DateTime(timezone=True), nullable=False)
    reason      = Column(String, nullable=True)   # "Mantenimiento", "Uso personal", etc.
    created_at  = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Notification(Base):
    """Notificaciones in-app para el cliente (ej: proveedor confirmó/rechazó reserva)."""
    __tablename__ = "notifications"
    id          = Column(String, primary_key=True, default=lambda: f"notif_{uuid.uuid4().hex[:10]}")
    user_id     = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    booking_id  = Column(String, ForeignKey("bookings.id"), nullable=True)
    type        = Column(String, nullable=False)   # "booking_confirmed" | "booking_rejected" | "dispute_resolved"
    title       = Column(String, nullable=False)
    message     = Column(Text, nullable=False)
    seen        = Column(Boolean, default=False, nullable=False)
    created_at  = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

