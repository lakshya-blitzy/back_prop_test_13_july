"""Gunicorn configuration for the testinium-qa Flask application.

Flask's development server is not production grade, so the deliverable ships a
production WSGI server. Start it with::

    gunicorn -c gunicorn.conf.py wsgi:app

Every setting can be overridden through the environment so the same file serves
local runs, containers and CI. ``APP_PORT`` shifts with ``CLONE_INDEX`` when
several clones of the repository run their own instance side by side.
"""

from __future__ import annotations

import multiprocessing
import os


def _int_env(name: str, default: int) -> int:
    """Return ``name`` from the environment as an int, falling back to *default*."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# Binds on all interfaces so the container/compose service is reachable.
_HOST = os.getenv("APP_HOST", "0.0.0.0")
_PORT = _int_env("APP_PORT", 8000 + _int_env("CLONE_INDEX", 0))

# --- Socket ------------------------------------------------------------------
bind = os.getenv("GUNICORN_BIND", f"{_HOST}:{_PORT}")
backlog = _int_env("GUNICORN_BACKLOG", 2048)

# --- Worker processes --------------------------------------------------------
# (2 x CPU) + 1 is the conventional starting point, capped so that a
# many-core CI host does not spawn dozens of workers for a small service.
_MAX_DEFAULT_WORKERS = 9
workers = _int_env(
    "GUNICORN_WORKERS",
    min(multiprocessing.cpu_count() * 2 + 1, _MAX_DEFAULT_WORKERS),
)
worker_class = os.getenv("GUNICORN_WORKER_CLASS", "sync")
threads = _int_env("GUNICORN_THREADS", 1)
# Report generation and repository cloning are slow; keep a generous timeout.
timeout = _int_env("GUNICORN_TIMEOUT", 120)
graceful_timeout = _int_env("GUNICORN_GRACEFUL_TIMEOUT", 30)
keepalive = _int_env("GUNICORN_KEEPALIVE", 5)
max_requests = _int_env("GUNICORN_MAX_REQUESTS", 0)
max_requests_jitter = _int_env("GUNICORN_MAX_REQUESTS_JITTER", 0)

# --- Application ------------------------------------------------------------
wsgi_app = os.getenv("GUNICORN_WSGI_APP", "wsgi:app")
# The application factory reads configuration at import time; preloading would
# share that state across workers, so it stays disabled.
preload_app = os.getenv("GUNICORN_PRELOAD", "false").lower() == "true"
chdir = os.getenv("GUNICORN_CHDIR", ".")

# --- Logging (stdout/stderr so containers and CI capture everything) ---------
accesslog = os.getenv("GUNICORN_ACCESS_LOG", "-")
errorlog = os.getenv("GUNICORN_ERROR_LOG", "-")
loglevel = os.getenv("GUNICORN_LOG_LEVEL", os.getenv("LOG_LEVEL", "info")).lower()
capture_output = True
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)sus'

# --- Process naming ---------------------------------------------------------
proc_name = os.getenv("GUNICORN_PROC_NAME", "testinium-qa")
