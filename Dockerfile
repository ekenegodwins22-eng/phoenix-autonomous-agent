FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates wget gosu && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install -r requirements.txt && playwright install --with-deps chromium
COPY . .
RUN mkdir -p /app/data && useradd -m -u 10001 phoenix && chown -R phoenix:phoenix /app
EXPOSE 8000
ENTRYPOINT ["/app/docker-entrypoint.sh"]
