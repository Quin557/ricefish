FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    RICEFISH_WEB_HOST=0.0.0.0

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config
COPY assets ./assets
COPY scripts ./scripts
COPY run.py ./

RUN pip install --no-cache-dir -e . \
    && useradd --create-home --uid 10001 ricefish \
    && mkdir -p /app/data \
    && chown -R ricefish:ricefish /app

USER ricefish
EXPOSE 8721 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8721/healthz', timeout=3)"

CMD ["python", "run.py"]
