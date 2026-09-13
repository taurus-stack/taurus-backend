#!/bin/bash
set -e

CERT_DIR="/app/certs"
if [ ! -w "$CERT_DIR" ]; then
    echo "[entrypoint] ERROR: $CERT_DIR is not writable by current user ($(id -un))"
    echo "[entrypoint] CA certificate auto-generation requires write access to $CERT_DIR"
    echo "[entrypoint] Fix: chown -R 1000:1000 taurus-backend/certs on the host before starting"
    exit 1
fi
mkdir -p "$CERT_DIR/newcerts"

echo "[entrypoint] Running database migrations..."
python manage.py migrate --noinput

echo "[entrypoint] Ensuring SDK client certificate..."
python manage.py ensure_sdk_cert

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