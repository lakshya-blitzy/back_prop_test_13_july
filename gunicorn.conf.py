"""Gunicorn configuration: how the production WSGI server runs this Flask application.

Flask's own development server is explicitly not production grade, so the deliverable ships
``gunicorn==26.0.0`` (``requirements.txt``) and this file is the single place its behaviour is
described::

    gunicorn -c gunicorn.conf.py wsgi:app    # `make serve`, and the Dockerfile CMD verbatim

Gunicorn imports this file as an ordinary Python module -- under the synthetic module name
``__config__`` -- and then copies every module-level name that matches one of its own settings
onto its configuration object, silently skipping the names it does not recognise. Everything
private below is therefore prefixed with an underscore, which documents intent and keeps
``gunicorn --check-config`` free of "unknown setting" noise.

ADDITIVE -- nothing here ports a source construct
-------------------------------------------------
Agent Action Plan (AAP) section 0.4.1, "Packaging and Entrypoints", binds this file in one line:
``gunicorn.conf.py`` | CREATE | **ADDITIVE** | "Bind address, worker count, log level". Section
0.3.1 repeats the same three concerns in the target tree -- ``gunicorn.conf.py  (new -- bind,
workers, loglevel)`` -- and section 0.1.1 gives the reason the file exists at all: "A WSGI
entrypoint and a production server are required, because Flask's development server is not
production-grade. This adds ``wsgi.py``, ``run.py``, and ``gunicorn.conf.py``."

The system being rewritten had no HTTP server whatsoever. Its entire orchestration was a
512-byte Groovy *scripted* pipeline ``[Jenkins:L1-L17]`` and its execution contract was the
Maven Surefire configuration ``[pom.xml:L17-L30]``; neither has any notion of a socket, a worker
process or an access log. **No setting below ports a Java or Groovy construct.** Three values
nevertheless trace straight back to the source specification, and each is cited where it is set:
the process name is the Maven ``artifactId`` ``[pom.xml:L8]``, the request timeout is sized
against the ported ``'Run tests'`` stage ``[Jenkins:L6-L11]`` -- whose execution semantics are
``<parallel>methods</parallel>`` with ``<useUnlimitedThreads>true</useUnlimitedThreads>``
``[pom.xml:L22-L23]`` -- and the log destinations must stay covered by the preserved ignore rule
``*.log`` ``[.gitignore:L6]``.

The three settings the AAP names, and everything else
-----------------------------------------------------
``bind``, ``workers`` and ``loglevel`` are the named deliverable; they are set in sections 3, 4
and 7 respectively. Every other setting carries a comment justifying why it is here, because
keeping additions minimal and justified is a requirement rather than a matter of taste.

Settings deliberately NOT present, each for a stated reason:

* ``reload`` -- a development behaviour. ``run.py`` is the development entrypoint (``make run``)
  and this file configures the production server only.
* ``certfile`` / ``keyfile`` and any other TLS material -- the repository holds no certificate
  and AAP section 0.2.2 places secrets management out of scope. TLS belongs to whatever
  terminates it in front of this process.
* ``statsd_host`` and every other metrics or monitoring integration -- nothing in the AAP asks
  for one. The observability requirement is enterprise baseline B9 (structured logging) plus the
  single additive ``GET /health`` liveness probe, and both are already satisfied.
* Any task queue, broker or background-worker framework -- none appears in the AAP section 0.5.1
  dependency inventory, and adding one would add capability the source system never had: "No
  feature may be dropped, and none may be added" (AAP section 0.8).
* Any secret, credential or token -- baseline B5. ``SECRET_KEY`` is resolved by
  ``app/config.py`` inside the application, never here.

``timeout`` is the one setting with a real behavioural consequence
-----------------------------------------------------------------
Everything else here tunes an HTTP server. ``timeout`` decides whether a legitimate unit of the
*ported* work survives, because this service fronts the three pipeline stages of AAP section
0.3.1 over HTTP and two of them are slow by nature:

* ``POST /api/v1/clone`` -- stage ``'Clone code'`` ``[Jenkins:L2-L4]``, network I/O that
  ``app/services/clone_service.py`` runs "with an explicit timeout" (AAP section 0.6),
  ``CLONE_TIMEOUT_SECONDS`` (300 s by default).
* ``POST /api/v1/runs`` -- stage ``'Run tests'`` ``[Jenkins:L6-L11]``, a whole pytest invocation
  under ``-n logical`` worker parallelism, the port of ``[pom.xml:L22-L23]``, bounded by
  ``TEST_TIMEOUT_SECONDS`` (1800 s by default).

Gunicorn's own default is 30 s, which would ``SIGKILL`` the worker -- and drop the client's
connection -- roughly a minute into a run the service itself was still willing to let finish.
Section 5 therefore *derives* the default from those two documented keys plus headroom, so the
two limits can never drift apart: raise ``TEST_TIMEOUT_SECONDS`` and the HTTP timeout follows.

That is a guard rail, not an invitation to hold connections open for half an hour.
``GET /api/v1/runs/<run_id>`` exists precisely so callers do not have to -- AAP section 0.3.1
describes it as "Run status and summary derived from the JSON report" -- so a client should
start a run, then poll that route. The generous timeout is what keeps an honest synchronous
caller from being cut off; polling is what keeps it from being needed.

Logging: who owns which stream
------------------------------
``app/logging_config.py`` states the split explicitly, and this file is the other half of it:
that module owns the root logger's handlers, its one deterministic record format and the
application's ``*.log`` destination, while "``gunicorn.conf.py`` owns gunicorn's own logs:
``accesslog`` (stdout), ``errorlog`` (stderr), ``loglevel`` -- which falls back to the same
``LOG_LEVEL`` key so the two track each other -- and ``capture_output``". Gunicorn sets
``propagate = False`` on its ``gunicorn.error`` and ``gunicorn.access`` loggers, so the two
halves never write the same record twice.

Streams are the default because a container or a CI job captures them for free (enterprise
baseline B9). A file destination is equally supported through the environment and is audited
against exactly two constraints, mirroring ``app/logging_config.py``:

1. It must end in ``.log``, because only then does the preserved pattern ``*.log``
   ``[.gitignore:L6]`` keep it untracked -- provable with ``git check-ignore -v``.
2. It must not sit inside the artifact root ``target/``: everything there is ephemeral by design
   and the port of ``mvn clean`` wipes it before every run (AAP section 0.2.2), so a server log
   placed there would be destroyed exactly when someone needed it. ``target/`` also matches
   ``.gitignore`` by *directory*, not by the ``*.log`` rule, so putting logs there would silently
   swap which contract keeps them untracked.

A destination that breaks either constraint is **honoured and warned about**, never overridden:
an operator's explicit choice stands. Nothing in this file prints -- the warnings go through
:mod:`logging` (baseline B9) -- and nothing in this file opens a file for reading or writing, so
the "explicit ``encoding='utf-8'``" rule of baseline B7 has no site to apply to here. Gunicorn
opens the log files it is given, and byte-level encoding of the reports is owned by
``app/reporting/*``.

Layering (AAP Rule T7, enterprise baseline B4)
----------------------------------------------
This module imports **the standard library and nothing else**. Three separate reasons:

* It must not import the Flask application. Gunicorn imports ``wsgi:app`` itself, at the moment
  it chooses; importing it here would run the application factory at configuration-parse time
  and would defeat the ``preload_app`` semantics of section 6.
* It must not reach into ``app.api``, ``app.web``, ``app.services``, ``app.reporting`` or
  ``app.utils``. AAP Rule T7 fixes the direction ``api -> services -> reporting -> utils`` and
  forbids anything under ``app/`` importing from ``tests/``; this file sits outside that graph
  entirely and stays there. Where a value is also known to the application -- the artifact root
  is ``app.utils.paths.TARGET_DIR_NAME``, the timeouts are ``app.config`` defaults -- it is
  re-read from the same documented environment key instead of imported.
* It must not import a harness distribution: no ``pytest``, ``pytest_bdd``, ``selenium``,
  ``webdriver_manager`` or ``faker``. Those live in ``requirements-test.txt``, while the
  deployed container installs ``requirements.txt`` alone, so ``gunicorn --check-config`` has to
  succeed against the runtime manifest by itself.

Files that must agree with this one
----------------------------------
* ``wsgi.py`` -- exposes the module-level ``app``, which fixes the target string ``wsgi:app``.
* ``Dockerfile`` -- ``CMD ["gunicorn", "-c", "gunicorn.conf.py", "wsgi:app"]``.
* ``docker-compose.yml`` -- starts that image and health-checks ``GET /health``.
* ``Makefile`` -- ``make serve`` runs the same command with ``APP_PORT`` exported.
* ``.env.example`` -- documents every environment key read below, with the same defaults.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Final

# =============================================================================
# SECTION 1 -- Module logger and environment helpers.
#
# The logger is named explicitly rather than taken from `__name__`: gunicorn
# loads a configuration *file* under the synthetic module name `__config__`, so
# `__name__` here would be that placeholder rather than anything diagnostic.
# `gunicorn.conf` places these records inside gunicorn's own namespace while
# staying clear of `gunicorn.error` and `gunicorn.access`, the two loggers
# gunicorn configures with `propagate = False`.
#
# These records are emitted while the configuration file is being parsed, before
# any logging handler exists. That is deliberate and it works: the standard
# library's last-resort handler writes WARNING and above to stderr, which is
# exactly where a misconfiguration needs to be visible. Nothing here prints
# (enterprise baseline B9).
# =============================================================================

_LOGGER: Final[logging.Logger] = logging.getLogger("gunicorn.conf")


def _str_env(name: str, default: str) -> str:
    """Return the environment value for *name*, or *default* when it is unset or blank.

    A variable that is present but empty -- ``GUNICORN_PROC_NAME=`` in a ``.env`` file, or an
    orchestrator passing through an unset value -- carries no decision, so it resolves to the
    documented default rather than to an empty string. This is the same "blank means absent"
    rule ``app/config.py`` applies to every setting it resolves.

    Args:
        name: Environment variable name, as documented in ``.env.example``.
        default: Value to use when the variable is unset, empty or whitespace only.

    Returns:
        The stripped environment value, or *default*.
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip()


def _int_env(name: str, default: int, *, minimum: int | None = None) -> int:
    """Return the environment value for *name* as an ``int``, defending the server's startup.

    A configuration file that raises takes the whole service down before it has bound a socket,
    so neither a typo nor an out-of-range value is allowed to do that. Both are reported at
    WARNING level -- silently ignoring a mistyped ``APP_PORT`` is how a deployment ends up
    listening somewhere nobody expects -- and then resolved to something usable.

    Args:
        name: Environment variable name, as documented in ``.env.example``.
        default: Value to use when the variable is unset, blank or unparseable.
        minimum: Optional inclusive lower bound. A smaller value is clamped up to it, because
            gunicorn rejects a non-positive worker count or timeout outright.

    Returns:
        The parsed integer, clamped to *minimum* when one is given.
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        value = default
    else:
        try:
            value = int(raw.strip(), 10)
        except ValueError:
            _LOGGER.warning(
                "Ignoring %s=%r: not a whole number. Using %d instead.", name, raw, default
            )
            value = default
    if minimum is not None and value < minimum:
        _LOGGER.warning(
            "%s resolved to %d, below the minimum of %d that gunicorn accepts. Using %d.",
            name,
            value,
            minimum,
            minimum,
        )
        return minimum
    return value


def _bool_env(name: str, default: bool) -> bool:
    """Return the environment value for *name* as a ``bool``.

    ``true``/``false`` is the spelling ``.env.example`` and ``docker-compose.yml`` use, and the
    other common spellings are accepted because an orchestrator, a shell and a human all reach
    for different ones. An unrecognised value is reported rather than quietly treated as false.

    Args:
        name: Environment variable name, as documented in ``.env.example``.
        default: Value to use when the variable is unset, blank or unrecognised.

    Returns:
        The parsed boolean, or *default*.
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    text = raw.strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    _LOGGER.warning(
        "Ignoring %s=%r: expected one of true/false/1/0/yes/no/on/off. Using %r instead.",
        name,
        raw,
        default,
    )
    return default


# =============================================================================
# SECTION 2 -- Log destinations, and the two constraints on a file destination.
#
# Mirrors `app/logging_config.py::_audit_destination`: the configured value is
# always honoured, and each of the two hazards is reported because both are
# silent and both are costly.
# =============================================================================

_STREAM_DESTINATION: Final[str] = "-"
"""Gunicorn's spelling of "use the standard stream" -- stdout for the access log, stderr for the
error log. The default for both, because a container runtime, ``docker compose logs`` and a CI
job all capture streams with no configuration at all."""

_IGNORED_LOG_SUFFIX: Final[str] = ".log"
"""The suffix the preserved ignore pattern ``*.log`` ``[.gitignore:L6]`` matches.

That two-line block -- ``# Log file`` / ``*.log`` -- is the only evidence the pre-migration
Java/Maven project produced logs at all, and AAP section 0.4.1 requires the updated
``.gitignore`` to keep it verbatim. A destination ending in this suffix therefore stays untracked
with no new ignore rule and no negation."""

_TARGET_DIR_KEY: Final[str] = "TARGET_DIR"
"""Environment key naming the ephemeral artifact root, documented in ``.env.example``.

The authoritative value lives once, in ``app.utils.paths.TARGET_DIR_NAME``. That module performs
no environment access of its own and this file may not import it (Rule T7 -- see the module
docstring), so the same documented key is re-read here rather than the constant imported."""

_TARGET_DIR_DEFAULT: Final[str] = "target"
"""The Java-flavoured artifact root name, retained deliberately.

AAP section 0.3.1 keeps the name so the Jenkins publisher's ``fileIncludePattern: '**/*.json'``
``[Jenkins:L15]`` needs no change at all. Nothing in this file creates, cleans or writes into it:
AAP section 0.6 assigns that to exactly three places -- ``app/utils/paths.py``, the ``Makefile``
``test`` target and ``tests/conftest.py``."""


def _artifact_root() -> Path:
    """Return the ephemeral artifact root, for the "not inside ``target/``" audit below.

    Returns:
        The configured artifact root as a path, defaulting to ``target``.
    """
    return Path(_str_env(_TARGET_DIR_KEY, _TARGET_DIR_DEFAULT))


def _audit_log_destination(name: str, destination: Path) -> bool:
    """Warn about a file destination that breaks either constraint, without overriding it.

    Args:
        name: The environment key the value came from, so the warning names the fix.
        destination: The configured destination path.

    Returns:
        ``True`` when the destination is known to sit inside the artifact root. ``False`` also
        covers "could not be determined", which is the safe answer for the one caller: it only
        uses this to decide whether to create the parent directory, and an attempt that the
        filesystem refuses is reported rather than fatal.
    """
    if destination.suffix != _IGNORED_LOG_SUFFIX:
        _LOGGER.warning(
            "%s=%s does not end in %r, so the preserved ignore pattern '*.log' "
            "[.gitignore:L6] does not cover it: the file is trackable and could be committed. "
            "Honouring the configured value all the same.",
            name,
            destination,
            _IGNORED_LOG_SUFFIX,
        )
    target_root = _artifact_root()
    # `resolve()` normalises a relative path, an absolute one and a `..` segment alike, which is
    # what makes the containment test correct for all three. It touches the filesystem, so a
    # refusal is caught rather than allowed to abort the parse: failing to produce a diagnostic
    # is never a reason to fail to start a server.
    try:
        inside_artifact_root = (
            destination.expanduser().resolve().is_relative_to(target_root.resolve())
        )
    except (OSError, ValueError) as error:
        _LOGGER.debug(
            "Could not normalise %s=%s for the artifact-root check (%s: %s)",
            name,
            destination,
            type(error).__name__,
            error,
        )
        return False
    if inside_artifact_root:
        _LOGGER.warning(
            "%s=%s sits inside the artifact root %s, which the port of `mvn clean` wipes "
            "before every run, so the log will be destroyed each time; a server log is not a "
            "per-run test artifact. Honouring the configured value all the same.",
            name,
            destination,
            target_root,
        )
    return inside_artifact_root


def _log_destination(name: str) -> str:
    """Resolve one gunicorn log destination from the environment.

    Args:
        name: ``GUNICORN_ACCESS_LOG`` or ``GUNICORN_ERROR_LOG``.

    Returns:
        Either :data:`_STREAM_DESTINATION` or the configured file path, exactly as configured.
    """
    value = _str_env(name, _STREAM_DESTINATION)
    if value == _STREAM_DESTINATION:
        return value
    # No NUL-byte guard is needed here, unlike `app/logging_config.py::_resolve_destination`,
    # which accepts a value from a Flask config mapping or a keyword argument. The only source
    # this file reads is the process environment, and an environment value cannot contain a NUL
    # byte: the C environment is a list of NUL-terminated strings and `os.environ` refuses one
    # outright ("ValueError: embedded null byte") at assignment. The two filesystem calls below
    # are guarded all the same, which covers every path the operating system rejects for any
    # other reason.
    destination = Path(value)
    if _audit_log_destination(name, destination):
        # Inside `target/`: honour the value, but create nothing. AAP section 0.6 assigns the
        # artifact root's creation to exactly three places -- `app/utils/paths.py`, the
        # `Makefile` test target and `tests/conftest.py` -- and this file is none of them.
        return value
    # Gunicorn opens the file itself and does NOT create the directory above it, so a missing
    # parent is a hard startup failure with an opaque message. Creating just that one directory
    # is the same courtesy `app/utils/paths.py::ensure_parent_directory` extends to the report
    # writers.
    parent = destination.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except (OSError, ValueError) as error:
        _LOGGER.warning(
            "Could not create the directory %s for %s=%s (%s: %s); gunicorn will report the "
            "failure if it cannot open the file.",
            parent,
            name,
            value,
            type(error).__name__,
            error,
        )
    return value


# =============================================================================
# SECTION 3 -- `bind`: the first of the three settings the AAP names.
#
# Resolution order, most specific first:
#   1. GUNICORN_BIND      - a literal gunicorn bind string, for anything the two
#                           keys below cannot express (a UNIX socket, say).
#   2. APP_HOST:APP_PORT  - the documented pair, shared with `run.py` so the
#                           development and production servers listen alike.
#   3. 8000 + CLONE_INDEX - the multi-instance offset, so several clones of this
#                           repository can each run their own instance without
#                           colliding on a host-global port. `.env.example`
#                           records that an explicit APP_PORT outranks it, and
#                           the Makefile computes APP_PORT the very same way.
# =============================================================================

# 0.0.0.0 so a container or a compose service is reachable from the host; the Dockerfile sets the
# same value as an ENV default. Narrow it to 127.0.0.1 for a local-only process.
_HOST: Final[str] = _str_env("APP_HOST", "0.0.0.0")
_PORT: Final[int] = _int_env("APP_PORT", 8000 + _int_env("CLONE_INDEX", 0, minimum=0), minimum=1)

bind = _str_env("GUNICORN_BIND", f"{_HOST}:{_PORT}")

# Depth of the queue of connections the kernel accepts on our behalf while every worker is busy.
# Gunicorn's own default, made explicit so it is an operator knob rather than a hidden constant:
# a burst of report downloads should queue rather than be refused.
backlog = _int_env("GUNICORN_BACKLOG", 2048, minimum=1)


# =============================================================================
# SECTION 4 -- `workers`: the second of the three settings the AAP names.
# =============================================================================

_MAX_DEFAULT_WORKERS: Final[int] = 9
"""Cap on the SIZED default, so a 64-core CI host does not fork 129 workers for a service whose
busiest endpoint serves a JSON file. An operator who wants more sets ``GUNICORN_WORKERS``."""


def _default_workers() -> int:
    """Size the worker pool from the CPUs this process may actually use.

    ``os.process_cpu_count()`` honours CPU affinity, so a container pinned to two CPUs on a
    32-CPU host is sized for two rather than for the machine. It is available on the pinned
    interpreter (``.python-version`` -- 3.14.6, ``requires-python = ">=3.14"``); the fallbacks
    exist so the expression can never evaluate to ``None`` or to zero.

    Returns:
        ``(2 x CPU) + 1``, the conventional starting point, capped at
        :data:`_MAX_DEFAULT_WORKERS`.
    """
    cpus = os.process_cpu_count() or os.cpu_count() or 1
    return min(max(cpus, 1) * 2 + 1, _MAX_DEFAULT_WORKERS)


workers = _int_env("GUNICORN_WORKERS", _default_workers(), minimum=1)

# `sync` is correct here, and the reason is specific rather than conventional: this service's
# slow operations are separate SUBPROCESSES with their own timeouts -- `git` for stage
# 'Clone code' [Jenkins:L2-L4] and pytest for stage 'Run tests' [Jenkins:L6-L11] -- not
# in-process socket waits that an async worker could overlap. There is no long-polling, no
# streaming and no websocket anywhere in the eight-route API surface, so an async worker class
# would add a dependency and a concurrency model for no measurable gain.
worker_class = _str_env("GUNICORN_WORKER_CLASS", "sync")

# One thread per worker: concurrency comes from processes. Stated explicitly because it is
# load-bearing -- gunicorn silently promotes the sync worker to the threaded one as soon as this
# exceeds 1, which would change the execution model without changing `worker_class`.
threads = _int_env("GUNICORN_THREADS", 1, minimum=1)


# =============================================================================
# SECTION 5 -- Timeouts. See "`timeout` is the one setting with a real
# behavioural consequence" in the module docstring for the full reasoning; the
# short version is that gunicorn's 30-second default would kill a legitimate
# ported test run, and that the fix is to derive the limit from the service's
# own documented stage timeouts rather than to pick a bigger literal.
# =============================================================================

_CLONE_TIMEOUT_DEFAULT: Final[int] = 300
"""Default of ``CLONE_TIMEOUT_SECONDS`` (``app.config.DEFAULT_CLONE_TIMEOUT_SECONDS``), the
bound on stage ``'Clone code'`` ``[Jenkins:L2-L4]``. Restated rather than imported: Rule T7 keeps
this file out of the ``app/`` import graph, so it reads the same documented key instead."""

_TEST_TIMEOUT_DEFAULT: Final[int] = 1800
"""Default of ``TEST_TIMEOUT_SECONDS`` (``app.config.DEFAULT_TEST_TIMEOUT_SECONDS``), the bound
on stage ``'Run tests'`` ``[Jenkins:L6-L11]`` -- a whole pytest invocation under ``-n logical``
parallelism, the port of ``[pom.xml:L22-L23]``."""

_TIMEOUT_HEADROOM_SECONDS: Final[int] = 60
"""Headroom above the longest stage, covering what happens either side of the subprocess itself:
request parsing, the clean-and-recreate of ``target/``, and the report generation AAP section 0.6
requires to run "in a finally-equivalent path" even after a failed run. Without it the HTTP
timeout would expire at the very moment the stage timeout did, and the client would see a dropped
connection instead of the non-gating result the pipeline is specified to return."""

# The longest legitimate synchronous stage, read from the same environment keys the services
# themselves use, so raising either one carries the HTTP limit with it instead of silently
# leaving gunicorn to kill the very work the service was still willing to finish.
_LONGEST_STAGE_SECONDS: Final[int] = max(
    _int_env("TEST_TIMEOUT_SECONDS", _TEST_TIMEOUT_DEFAULT, minimum=1),
    _int_env("CLONE_TIMEOUT_SECONDS", _CLONE_TIMEOUT_DEFAULT, minimum=1),
)

timeout = _int_env(
    "GUNICORN_TIMEOUT", _LONGEST_STAGE_SECONDS + _TIMEOUT_HEADROOM_SECONDS, minimum=1
)

# Window an in-flight request gets to finish when the process is asked to stop, before the worker
# is killed. Deliberately short and NOT tied to `timeout`: it bounds how long `docker stop`,
# `make serve` under Ctrl-C or a rolling restart waits, and a value near the request timeout
# would make an ordinary shutdown look like a hang. A caller whose long run is interrupted this
# way polls `GET /api/v1/runs/<run_id>` for the outcome.
graceful_timeout = _int_env("GUNICORN_GRACEFUL_TIMEOUT", 30, minimum=0)

# Seconds an idle keep-alive connection is held open. Small on purpose: the report-retrieval
# routes are hit by curl, a browser fetching the rendered index, and the container health check,
# so a few seconds spares the repeat-visitor a fresh handshake without pinning a sync worker to a
# connection that has stopped asking for anything.
keepalive = _int_env("GUNICORN_KEEPALIVE", 5, minimum=0)

# Worker recycling, DISABLED by default (0 means "never"). Recycling exists to bound the effect
# of a slow leak in a long-lived deployment, but a worker that reaches its request quota mid-run
# would be replaced under a client that is still waiting -- the same hazard `timeout` above is
# sized to avoid. It is exposed rather than removed so a long-running deployment can opt in, with
# jitter to keep the workers from recycling in lockstep.
max_requests = _int_env("GUNICORN_MAX_REQUESTS", 0, minimum=0)
max_requests_jitter = _int_env("GUNICORN_MAX_REQUESTS_JITTER", 0, minimum=0)


# =============================================================================
# SECTION 6 -- What to load, and from where.
# =============================================================================

# The WSGI target, so `gunicorn -c gunicorn.conf.py` works with no positional argument while the
# documented `gunicorn -c gunicorn.conf.py wsgi:app` -- the Dockerfile CMD and `make serve` --
# still wins, a command-line value outranking the configuration file. `wsgi.py` fixes both halves
# of this string: the module name and the module-level `app` object it publishes.
wsgi_app = _str_env("GUNICORN_WSGI_APP", "wsgi:app")

# Working directory, kept at the repository root. Load-bearing rather than cosmetic: `target/`,
# `logs/` and the configuration templates are all resolved relative to it, so a server started
# from elsewhere would look for the artifact root in the wrong place.
chdir = _str_env("GUNICORN_CHDIR", ".")

# Preloading is DISABLED, and must stay so. The application factory resolves its configuration
# while the module is imported -- `wsgi.py` runs `create_app()` at import time -- so preloading
# would import the application once in the master process and fork that single, already-resolved
# configuration into every worker. Each worker instead imports `wsgi` for itself and receives its
# own application, its own configuration mapping and its own blueprint and error-handler
# registries, which is the property `wsgi.py` documents and relies on. The key is honoured so an
# operator can opt in knowingly (a faster boot, shared memory), never by default.
preload_app = _bool_env("GUNICORN_PRELOAD", False)


# =============================================================================
# SECTION 7 -- `loglevel`, the third of the three settings the AAP names, and
# the two log destinations. Ownership split with `app/logging_config.py` is set
# out in the module docstring.
# =============================================================================

_GUNICORN_LOG_LEVELS: Final[frozenset[str]] = frozenset(
    {"debug", "info", "warning", "error", "critical"}
)
"""The five level names gunicorn accepts.

The application's own ``LOG_LEVEL`` is read through the standard library, whose vocabulary is
wider (``NOTSET``, and any level a caller registers). Since ``loglevel`` falls back to that
shared key, a value the application tolerates could otherwise abort the server with "Invalid
log level" before it ever binds -- so the value is validated here instead."""

_DEFAULT_LOG_LEVEL: Final[str] = "info"
"""Gunicorn's own default, and the value ``.env.example`` documents for this key."""


def _resolve_loglevel() -> str:
    """Resolve gunicorn's error-log verbosity.

    Order: ``GUNICORN_LOG_LEVEL``, then the application's ``LOG_LEVEL``, then ``info``. Reading
    the shared key second is what keeps the server's verbosity tracking the application's
    instead of drifting away from it, and it is why ``.env.example`` leaves
    ``GUNICORN_LOG_LEVEL`` commented out.

    Returns:
        One of :data:`_GUNICORN_LOG_LEVELS`, lower-cased.
    """
    configured = _str_env("GUNICORN_LOG_LEVEL", _str_env("LOG_LEVEL", _DEFAULT_LOG_LEVEL))
    level = configured.lower()
    if level in _GUNICORN_LOG_LEVELS:
        return level
    _LOGGER.warning(
        "Log level %r is not one of gunicorn's %s, so it cannot be used for the error log; "
        "using %r. The application's own logger still honours LOG_LEVEL.",
        configured,
        sorted(_GUNICORN_LOG_LEVELS),
        _DEFAULT_LOG_LEVEL,
    )
    return _DEFAULT_LOG_LEVEL


loglevel = _resolve_loglevel()

# Streams by default -- `-` is stdout for the access log and stderr for the error log -- because
# a container runtime, `docker compose logs` and a CI job all capture those for free. A file path
# is equally supported and is audited against the two constraints in section 2: it must end in
# `.log` so the preserved `*.log` rule [.gitignore:L6] keeps it untracked, and it must not sit
# inside the `target/` tree that the port of `mvn clean` wipes before every run.
accesslog = _log_destination("GUNICORN_ACCESS_LOG")
errorlog = _log_destination("GUNICORN_ERROR_LOG")

# Anything a worker or a subprocess writes straight to stdout/stderr is folded into the error log
# rather than lost. `app/logging_config.py` names this setting as belonging here, and it is what
# makes a stray library write -- a Selenium Manager notice, a git progress line -- land in the
# same place as everything else instead of vanishing when the destination is a file.
capture_output = True

# Access-log line, gunicorn's combined format plus `%(D)s`, the request duration in microseconds.
# That single addition is what makes the slow ported stages measurable from the log alone, and it
# is how the `timeout` of section 5 can be reviewed against real numbers rather than guessed at.
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)sus'


# =============================================================================
# SECTION 8 -- Process naming.
# =============================================================================

# Name shown for the master and its workers in `ps`, `top` and a container's process table.
# `testinium-qa` is the Maven artifactId [pom.xml:L8], carried over unchanged so the deployed
# process is recognisable as the same project the POM identifies -- the same string
# `pyproject.toml` uses for `name` and the Makefile for the image tag.
proc_name = _str_env("GUNICORN_PROC_NAME", "testinium-qa")


# =============================================================================
# SECTION 9 -- Server lifecycle hooks.
#
# Exactly two, and neither is decoration. Both write through gunicorn's own
# logger -- `server.log`, already formatted, already at the configured level and
# already pointed at `errorlog` -- so the records join the same stream as
# everything else and nothing here prints (enterprise baseline B9).
#
# The effective configuration is worth one line at startup because every value
# above is environment-driven: without it, "which port, how many workers, what
# timeout" is answerable only by reconstructing the environment of a process
# that has already started.
# =============================================================================


def on_starting(server: Any) -> None:
    """Record the effective configuration once, in the master, before any worker is forked.

    ``server`` is typed as :class:`~typing.Any` deliberately: annotating it as gunicorn's
    ``Arbiter`` would mean importing gunicorn here, and this module imports the standard library
    only (Rule T7 -- see the module docstring).

    Args:
        server: The gunicorn arbiter, used only for its configured logger.
    """
    server.log.info(
        "testinium-qa starting: bind=%s workers=%d worker_class=%s threads=%d timeout=%ds "
        "graceful_timeout=%ds loglevel=%s accesslog=%s errorlog=%s preload_app=%s",
        bind,
        workers,
        worker_class,
        threads,
        timeout,
        graceful_timeout,
        loglevel,
        accesslog,
        errorlog,
        preload_app,
    )
    # Stated at startup rather than left to a reader of this file, because it is the operational
    # consequence of the derived timeout above: a client should not hold a connection open for
    # the length of a test run when a status route exists.
    server.log.info(
        "Request timeout is %ds, sized for the longest ported stage (%ds) plus %ds of headroom; "
        "poll GET /api/v1/runs/<run_id> for a run's status rather than holding a request open.",
        timeout,
        _LONGEST_STAGE_SECONDS,
        _TIMEOUT_HEADROOM_SECONDS,
    )


def on_exit(server: Any) -> None:
    """Record that the master exited through the ordinary shutdown path.

    A clean shutdown is otherwise indistinguishable from a killed process in the log, which
    matters here because ``graceful_timeout`` above bounds it: seeing this line is how a
    stop-and-restart is confirmed to have drained rather than been cut off.

    Args:
        server: The gunicorn arbiter, used only for its configured logger.
    """
    server.log.info("testinium-qa stopped: master exited after a graceful shutdown")
