"""Almacenamiento de imágenes en la base de datos (Render no tiene disco persistente).
- POST /api/media  : sube imagen pública (fotos de servicios). Requiere sesión.
- GET  /api/media/{id} : sirve la imagen pública.
Las imágenes privadas (documentos de verificación) se sirven por endpoints de admin.
"""
import io
import asyncio
import uuid
from typing import Optional

from fastapi import APIRouter, File, Header, HTTPException, Request, Response, UploadFile
from PIL import Image
from sqlalchemy import select

from database import AsyncSessionLocal
from models import MediaFile
from ..dependencies import require_auth

router = APIRouter(prefix="/api")

_MAX_BYTES = 5 * 1024 * 1024
_FMT_CT = {"jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
MIN_W, MIN_H = 600, 400  # resolución mínima razonable


def _inspect(data: bytes):
    img = Image.open(io.BytesIO(data))
    fmt = (img.format or "").lower()
    w, h = img.size
    img.close()
    return fmt, w, h


async def store_media(data: bytes, *, owner_id: Optional[str], private: bool) -> tuple[str, str]:
    """Valida y guarda una imagen en la BD. Devuelve (id, content_type)."""
    if len(data) > _MAX_BYTES:
        raise HTTPException(status_code=413, detail="Imagen demasiado grande (máx. 5 MB).")
    try:
        fmt, w, h = await asyncio.to_thread(_inspect, data)
    except Exception:
        raise HTTPException(status_code=422, detail="El archivo no es una imagen válida (jpg/png/webp).")
    if fmt not in _FMT_CT:
        raise HTTPException(status_code=422, detail="Formato no permitido. Usa JPG, PNG o WebP.")
    if w < MIN_W or h < MIN_H:
        raise HTTPException(status_code=422, detail=f"Imagen de baja calidad. Mínimo {MIN_W}×{MIN_H}px (recibida {w}×{h}).")
    mid = f"media_{uuid.uuid4().hex}"
    async with AsyncSessionLocal() as db:
        db.add(MediaFile(id=mid, owner_id=owner_id, content_type=_FMT_CT[fmt], data=data, is_private=private))
        await db.commit()
    return mid, _FMT_CT[fmt]


@router.post("/media")
async def upload_media(request: Request, file: UploadFile = File(...), authorization: Optional[str] = Header(None)):
    user = await require_auth(request, authorization)
    content = await file.read(_MAX_BYTES + 1)
    mid, _ = await store_media(content, owner_id=user.id, private=False)
    base = str(request.base_url).rstrip("/")
    return {"url": f"{base}/api/media/{mid}", "id": mid}


@router.get("/media/{media_id}")
async def get_media(media_id: str):
    async with AsyncSessionLocal() as db:
        m = await db.get(MediaFile, media_id)
    if not m or m.is_private:
        raise HTTPException(status_code=404, detail="No encontrado")
    return Response(content=m.data, media_type=m.content_type,
                    headers={"Cache-Control": "public, max-age=31536000, immutable"})
