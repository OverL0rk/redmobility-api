# Migrado desde server.py líneas 557–655
# Endpoints públicos: health, root, assets, categories
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from database import AsyncSessionLocal
from models import (
    Asset as AssetModel,
    Booking as BookingModel,
    Category as CategoryModel,
    Review as ReviewModel,
    User as UserModel,
    BookingStatus,
)
from ..utils import asset_to_dict

router = APIRouter(prefix="/api")


@router.get("/health")
async def health():
    async with AsyncSessionLocal() as db:
        await db.execute(select(1))
    return {"status": "ok"}


@router.get("/")
async def root():
    return {"message": "RedMobility API", "version": "2.0.0"}


@router.get("/assets")
async def get_assets(
    type: Optional[str] = None,
    zone: Optional[str] = None,
    min_price: Optional[int] = None,
    max_price: Optional[int] = None,
    sort: Optional[str] = "best_rated",
    start_datetime: Optional[str] = None,
    end_hours: Optional[int] = None,
):
    async with AsyncSessionLocal() as db:
        query = (
            select(AssetModel)
            .options(selectinload(AssetModel.images))
            .where(AssetModel.is_active == True)
        )
        if type:
            query = query.where(AssetModel.type == type)
        if zone:
            query = query.where(AssetModel.location_zone == zone)
        if min_price is not None:
            query = query.where(AssetModel.price_per_day >= min_price)
        if max_price is not None:
            query = query.where(AssetModel.price_per_day <= max_price)
        result = await db.execute(query)
        assets = result.scalars().all()

        # Filter by real availability when dates are provided — single query instead of N
        if start_datetime and end_hours and end_hours >= 1:
            try:
                start_dt = datetime.fromisoformat(start_datetime.replace("Z", "+00:00"))
                end_dt = start_dt + timedelta(hours=end_hours)
                overlap_result = await db.execute(
                    select(
                        BookingModel.asset_id,
                        BookingModel.start_datetime,
                        BookingModel.end_hours,
                    ).where(
                        BookingModel.status.in_(
                            [BookingStatus.PENDING, BookingStatus.CONFIRMED, BookingStatus.ACTIVE]
                        ),
                        BookingModel.start_datetime < end_dt,
                    )
                )
                rows = overlap_result.all()
                booked_ids = {
                    row.asset_id for row in rows
                    if (row.start_datetime + timedelta(hours=row.end_hours)) > start_dt
                }
                assets = [a for a in assets if a.id not in booked_ids]
            except ValueError:
                pass  # Invalid date format — return unfiltered results

    data = [asset_to_dict(a) for a in assets]
    if sort == "best_rated":
        data.sort(key=lambda x: x.get("rating", 0), reverse=True)
    elif sort == "lowest_price":
        data.sort(key=lambda x: x.get("price_per_day", 0))
    elif sort == "highest_price":
        data.sort(key=lambda x: x.get("price_per_day", 0), reverse=True)
    return data


@router.get("/assets/{asset_id}")
async def get_asset(asset_id: str):
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(AssetModel)
            .options(selectinload(AssetModel.images))
            .where(AssetModel.id == asset_id)
        )
        asset = result.scalar_one_or_none()
        if not asset:
            raise HTTPException(status_code=404, detail="Asset not found")
        data = asset_to_dict(asset)
        prov_result = await db.execute(
            select(UserModel).where(UserModel.id == asset.provider_id)
        )
        provider = prov_result.scalar_one_or_none()
        if provider:
            data["provider"] = {
                "user_id": provider.id,
                "name": provider.name,
                "verified": provider.verified,
                "picture": provider.picture,
            }
        rev_result = await db.execute(
            select(ReviewModel).where(
                ReviewModel.asset_id == asset_id,
                ReviewModel.status == "approved",
            )
        )
        reviews = rev_result.scalars().all()
        data["reviews"] = [
            {
                "rating": r.rating,
                "comment": r.comment,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in reviews
        ]
    return data


@router.get("/categories")
async def get_categories():
    """Devuelve categorías activas con conteo de activos disponibles."""
    async with AsyncSessionLocal() as db:
        cats = (
            await db.execute(
                select(CategoryModel)
                .where(CategoryModel.is_active == True)
                .order_by(CategoryModel.name)
            )
        ).scalars().all()
        counts = {
            r[0]: r[1]
            for r in (
                await db.execute(
                    select(AssetModel.type, func.count(AssetModel.id))
                    .where(AssetModel.is_active == True)
                    .group_by(AssetModel.type)
                )
            ).all()
        }
    return [
        {"type": c.id, "name": c.name, "icon": c.icon, "count": counts.get(c.id, 0)}
        for c in cats
    ]
