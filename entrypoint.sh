#!/bin/sh
set -e
echo "[entrypoint] Corriendo migraciones Alembic..."
alembic upgrade head
echo "[entrypoint] DDL extra (media + verificacion)..."
python init_extra.py || echo "[entrypoint] init_extra fallo, continuando"
echo "[entrypoint] Sembrando datos demo (idempotente)..."
python crear_admin.py || echo "[entrypoint] crear_admin fallo, continuando"
python seed_data.py || echo "[entrypoint] seed_data fallo, continuando"
echo "[entrypoint] Iniciando gunicorn en puerto ${PORT:-8001}..."
exec gunicorn app.main:app \
  --workers 2 \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:${PORT:-8001} \
  --timeout 120 \
  --access-logfile - \
  --error-logfile -
