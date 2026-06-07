"""
Shared utilities — serializers and logging formatter.
No imports from routers or dependencies (no circular risk).
"""
import json
import logging
import sys

from models import (
    User as UserModel,
    Asset as AssetModel,
    Booking as BookingModel,
)


# ── JSON logging ────────────────────────────────────────────────

class _JsonFormatter(logging.Formatter):
    def format(self, record):
        log_obj = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_obj, ensure_ascii=False)


def configure_logging():
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    logging.root.setLevel(logging.INFO)
    logging.root.addHandler(handler)


# ── Serializers ─────────────────────────────────────────────────

def asset_to_dict(asset: AssetModel) -> dict:
    return {
        "asset_id": asset.id,
        "provider_id": asset.provider_id,
        "type": asset.type,
        "name": asset.name,
        "description": asset.description,
        "brand": asset.brand,
        "model": asset.model,
        "year": asset.year,
        "capacity": asset.capacity,
        "price_per_hour": asset.price_per_hour,
        "price_per_day": asset.price_per_day,
        "location_zone": asset.location_zone,
        "pickup_address": asset.pickup_address,
        "what_included": asset.what_included or [],
        "is_active": asset.is_active,
        "review_status": getattr(asset, "review_status", "approved") or "approved",
        "rating": asset.rating,
        "total_rentals": asset.total_rentals,
        "buffer_before_hours": getattr(asset, "buffer_before_hours", 2.0),
        "buffer_after_hours":  getattr(asset, "buffer_after_hours", 2.0),
        "delivery_available": getattr(asset, "delivery_available", False),
        "delivery_radius_km": getattr(asset, "delivery_radius_km", 0.0),
        "delivery_cost_usd":  getattr(asset, "delivery_cost_usd", 0),
        "images": [
            {"image_id": i.id, "url": i.url, "is_primary": i.is_primary}
            for i in (asset.images or [])
        ],
    }


def user_to_dict(user: UserModel) -> dict:
    return {
        "user_id": user.id,
        "email": user.email,
        "name": user.name,
        "picture": user.picture,
        "phone_whatsapp": getattr(user, "phone_whatsapp", None),
        "document_id": getattr(user, "document_id", None),
        "role": user.role.value if hasattr(user.role, "value") else user.role,
        "verified": user.verified,
        "is_banned": getattr(user, "is_banned", False),
        "ban_reason": getattr(user, "ban_reason", None),
        "document_type": getattr(user, "document_type", None),
        "verification_status": getattr(user, "verification_status", "none") or "none",
        "has_document": bool(getattr(user, "document_media_id", None)),
        "created_at": user.created_at.isoformat() if getattr(user, "created_at", None) else None,
    }


def booking_to_dict(booking: BookingModel) -> dict:
    return {
        "booking_id": booking.id,
        "booking_code": booking.booking_code,
        "asset_id": booking.asset_id,
        "client_id": booking.client_id,
        "provider_id": booking.provider_id,
        "start_datetime": booking.start_datetime.isoformat() if booking.start_datetime else None,
        "end_hours": booking.end_hours,
        "total_price": booking.total_price,
        "platform_commission": booking.platform_commission,
        "insurance_type": booking.insurance_type,
        "insurance_price": booking.insurance_price,
        "status": booking.status.value if hasattr(booking.status, "value") else booking.status,
        "client_name": booking.client_name,
        "client_email": booking.client_email,
        "client_phone": booking.client_phone,
        "client_document": booking.client_document,
        "created_at": booking.created_at.isoformat() if booking.created_at else None,
    }
