#!/bin/sh
set -eu
: "${RAILWAY_VOLUME_MOUNT_PATH:?Attach a persistent Railway volume before starting}"
export DATA_DIR="${DATA_DIR:-$RAILWAY_VOLUME_MOUNT_PATH}"
export MODEL_DIR="${MODEL_DIR:-$DATA_DIR/models}"
# Volumes are mounted at runtime, not build/pre-deploy time.
python scripts/download_models.py
flask --app app:create_app db upgrade
exec gunicorn 'app:create_app()' --bind "0.0.0.0:${PORT:-8000}" --workers "${WEB_CONCURRENCY:-1}" --threads 2 --timeout 120
