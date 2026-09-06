FROM python:3.11-slim-bookworm

LABEL org.opencontainers.image.version="0.1.0"

ARG SABER_BUILD_SHA=unknown
ENV SABER_BUILD_SHA=$SABER_BUILD_SHA

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    SABER_BIND_HOST=0.0.0.0 \
    SABER_PORT=8080 \
    SABER_DB_PATH=/data/sabermetrics.db

RUN groupadd --gid 1001 app && \
    useradd --uid 1001 --gid 1001 --create-home --shell /usr/sbin/nologin app

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir ".[postgres]"

COPY config ./config
COPY fixtures ./fixtures
COPY scripts/setup_db.py ./scripts/setup_db.py

RUN mkdir -p /data && chown 1001:1001 /data

USER 1001:1001

EXPOSE 8080

CMD ["sabermetrics", "serve"]
