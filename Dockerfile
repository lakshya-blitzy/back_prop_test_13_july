# =============================================================================
# Dockerfile - reproducible, containerised execution with a pinned interpreter.
#
# ADDITIVE: the source project has no container definition. It exists because the
# Python deliverable must be able to run standalone, and because the interpreter
# version is part of the parity contract (.python-version = 3.14.6).
#
# Build:   docker build -t testinium-qa:1.0.0.dev0 .
# Run:     docker run --rm -p 8000:8000 testinium-qa:1.0.0.dev0
# Compose: CLONE_INDEX=1 APP_PORT=8001 docker compose -p testinium-qa-1 up -d
# =============================================================================
FROM python:3.14.6-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    APP_HOST=0.0.0.0 \
    APP_PORT=8000 \
    APP_CONFIG=production \
    TARGET_DIR=target

WORKDIR /app

# `git` is required by the ported 'Clone code' stage (app/services/clone_service.py);
# `make` exposes the same build surface inside the container as on the host.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git make ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Dependency layer first so it is cached independently of the source tree.
COPY requirements.txt requirements-test.txt ./
RUN python -m pip install --upgrade "pip==26.2" "setuptools==83.0.0" "wheel==0.47.0" \
 && python -m pip install -r requirements.txt -r requirements-test.txt

# Application, harness, scripts and configuration templates.
COPY . .

# The cucumber JSON writer does NOT create its parent directory, so the whole
# ephemeral artifact tree is materialised up front (name retained from Maven).
RUN mkdir -p "${TARGET_DIR}/cucumber" \
             "${TARGET_DIR}/screenshots" \
             "${TARGET_DIR}/error-shots" \
             "${TARGET_DIR}/surefire-reports" \
             logs

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.getenv('APP_PORT','8000')+'/health',timeout=4)" || exit 1

CMD ["gunicorn", "-c", "gunicorn.conf.py", "wsgi:app"]
