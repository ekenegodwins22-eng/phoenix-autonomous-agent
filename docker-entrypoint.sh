#!/bin/sh
set -eu
mkdir -p /app/data
chown -R phoenix:phoenix /app/data
exec gosu phoenix uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
