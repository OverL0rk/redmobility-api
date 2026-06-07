# Migrado desde server.py líneas 1182–1221
# 1 endpoint: POST /reviews (cliente, booking completado)
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from database import AsyncSessionLocal
from models import (
    Booking as BookingModel,
    Review as ReviewModel,
)
from ..dependencies import require_auth

router = APIRouter(prefix="/api")

# ── Pydantic schema ──────────────────────────────────────────────

class ReviewCreate(BaseModel):
    booking_id: str
    rating: int
    comment: str


# ── Endpoint ─────────────────────────────────────────────────────

@router.post("/reviews")
async def create_review(review_data: ReviewCreate, request: Request, authorization: Optional[str] = Header(None)):
    user = await require_auth(request, authorization)
    if review_data.rating < 1 or review_data.rating > 5:
        raise HTTPException(status_code=422, detail="Rating must be between 1 and 5")
    if len(review_data.comment.strip()) < 10:
        raise HTTPException(status_code=422, detail="Comment must be at least 10 characters")
    async with AsyncSessionLocal() as db:
        b_result = await db.execute(select(BookingModel).where(BookingModel.id == review_data.booking_id))
        booking = b_result.scalar_one_or_none()
        if not booking:
            raise HTTPException(status_code=404, detail="Booking not found")
        if booking.client_id != user.id:
            raise HTTPException(status_code=403, detail="You can only review your own bookings")
        status_val = booking.status.value if hasattr(booking.status, 'value') else booking.status
        if status_val != "completed":
            raise HTTPException(status_code=400, detail="You can only review completed bookings")
        # Check no existing review for this booking
        existing = await db.execute(
            select(ReviewModel).where(ReviewModel.booking_id == review_data.booking_id, ReviewModel.client_id == user.id)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="You have already reviewed this booking")
        review = ReviewModel(
            booking_id=review_data.booking_id,
            client_id=user.id,
            asset_id=booking.asset_id,
            provider_id=booking.provider_id,
            rating=review_data.rating,
            comment=review_data.comment,
            status="pending"
        )
        db.add(review)
        await db.commit()
        await db.refresh(review)
    return {"review_id": review.id, "status": "pending", "message": "Review submitted and pending moderation"}
