#!/bin/sh
set -e
echo "[entrypoint] Corriendo migraciones Alembic..."
alembic upgrade head
echo "[entrypoint] Migraciones completas. Iniciando gunicorn en puerto ${PORT:-8001}..."
exec gunicorn app.main:app \
  --workers 2 \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:${PORT:-8001} \
  --timeout 120 \
  --access-logfile - \
  --error-logfile -
