"""
RedMobility API — entry point de la arquitectura modular.
Crea la app FastAPI, configura logging, middleware y registra todos los routers.
"""
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.cors import CORSMiddleware

# load_dotenv ANTES de importar cualquier módulo que lea env vars
ROOT_DIR = Path(__file__).parent.parent  # backend/
load_dotenv(ROOT_DIR / ".env")

from .utils import configure_logging
from .routers import auth, assets, bookings, payments, provider, reviews, admin, client, media

configure_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title="RedMobility API")


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.on_event("startup")
async def startup():
    from database import init_db
    await init_db()
    logger.info("Backend iniciado. Schema gestionado por init_db (SQLite local).")


# ── Routers ──────────────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(assets.router)
app.include_router(bookings.router)
app.include_router(payments.router)
app.include_router(provider.router)
app.include_router(reviews.router)
app.include_router(admin.router)
app.include_router(client.router)
app.include_router(media.router)

# ── CORS ─────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "Origin", "X-Requested-With"],
)
