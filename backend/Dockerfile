FROM selenium/standalone-chrome@sha256:aae9b4477f2c1af57c0d7c7e3711211d2287ceb865b9ec8d0b59c6c2a83416a5

USER root

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app/backend
ARG PIP_INDEX_URL=https://pypi.org/simple
COPY backend/requirements-lock.txt ./requirements-lock.txt
# Reuse the official image's Python environment and installed Chrome.
RUN python -m pip install --disable-pip-version-check -r requirements-lock.txt gunicorn==23.0.0 \
    && python -m playwright install ffmpeg \
    && apt-get update \
    && apt-get install -y --no-install-recommends antiword \
    && rm -rf /var/lib/apt/lists/*

COPY backend/ ./
COPY docker/backend-entrypoint.sh /usr/local/bin/testpilot-entrypoint
EXPOSE 8000
ENTRYPOINT ["sh", "/usr/local/bin/testpilot-entrypoint"]
CMD ["web"]
