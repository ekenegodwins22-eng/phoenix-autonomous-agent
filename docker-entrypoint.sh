#!/bin/sh
set -eu
mkdir -p /app/data
chown -R phoenix:phoenix /app/data
if ! gosu phoenix python -m app.runtime --check-browser; then
  echo "Phoenix startup check failed: Playwright Chromium is unavailable or incompatible" >&2
  exit 1
fi
exec gosu phoenix uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
