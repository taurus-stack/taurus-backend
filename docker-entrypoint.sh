#!/bin/bash
set -e

echo "[entrypoint] Running database migrations..."
python manage.py migrate --noinput

echo "[entrypoint] Initializing system data..."
python manage.py init -y

echo "[entrypoint] Collecting static files..."
python manage.py collectstatic --noinput --clear

GUNICORN_WORKERS=${GUNICORN_WORKERS:-4}
GUNICORN_THREADS=${GUNICORN_THREADS:-2}
GUNICORN_TIMEOUT=${GUNICORN_TIMEOUT:-120}
GUNICORN_BIND=${GUNICORN_BIND:-0.0.0.0:8000}

echo "[entrypoint] Starting gunicorn (workers=$GUNICORN_WORKERS, threads=$GUNICORN_THREADS)..."
exec gunicorn application.wsgi:application \
    --bind "$GUNICORN_BIND" \
    --workers "$GUNICORN_WORKERS" \
    --worker-class gthread \
    --threads "$GUNICORN_THREADS" \
    --timeout "$GUNICORN_TIMEOUT" \
    --access-logfile - \
    --error-logfile -