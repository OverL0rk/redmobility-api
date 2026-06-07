#!/bin/sh
set -e

echo "[entrypoint] Corriendo migraciones Alembic..."
alembic upgrade head
echo "[entrypoint] Migraciones completas. Iniciando gunicorn..."

exec gunicorn app.main:app \
  --workers 4 \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8001 \
  --timeout 120 \
  --access-logfile - \
  --error-logfile -
