FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates wget gosu && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install -r requirements.txt && mkdir -p /ms-playwright && playwright install --with-deps chromium
COPY . .
RUN mkdir -p /app/data /tmp/phoenix && useradd -m -u 10001 phoenix && chown -R phoenix:phoenix /app /tmp/phoenix && chmod -R a+rX /ms-playwright
EXPOSE 8000
ENTRYPOINT ["/app/docker-entrypoint.sh"]
