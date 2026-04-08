FROM python:3.12-slim@sha256:3d5ed973e45820f5ba5e46bd065bd88b3a504ff0724d85980dcd05eab361fcf4 AS gateway-base
WORKDIR /app
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

COPY backend/requirements-core.txt backend/requirements-core.txt
COPY backend/requirements-iot.txt backend/requirements-iot.txt
COPY backend/requirements-ai.txt backend/requirements-ai.txt
COPY backend/requirements-media.txt backend/requirements-media.txt

RUN groupadd -r -g 1000 appgroup && useradd -r -u 1000 -g appgroup -s /sbin/nologin appuser

ARG CACHEBUST=1
COPY --chown=appuser:appgroup backend ./backend

FROM gateway-base AS gateway-iot
RUN pip install --no-cache-dir \
    -r backend/requirements-core.txt \
    -r backend/requirements-iot.txt

USER appuser
CMD ["sh", "-c", "exec uvicorn backend.api.main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS:-2} --timeout-graceful-shutdown 15"]

FROM gateway-base AS gateway-kernel
RUN pip install --no-cache-dir -r backend/requirements-core.txt

USER appuser
CMD ["sh", "-c", "exec uvicorn backend.api.main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS:-2} --timeout-graceful-shutdown 15"]

# Keep the default build output pinned to kernel-only dependencies.
FROM gateway-kernel AS gateway-default
