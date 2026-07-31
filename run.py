"""Development entrypoint: builds the application from the factory and starts Flask's server.

::

    python run.py     # this file, directly
    make run          # the same thing, with APP_PORT pre-shifted by CLONE_INDEX

NOT PRODUCTION. Flask's built-in server is a development convenience -- single process by
default, unhardened, and explicitly not production grade -- which is why the deliverable also
pins ``gunicorn==26.0.0`` in ``requirements.txt`` and ships a separate WSGI entrypoint. Agent
Action Plan (AAP) section 0.1.1 records the requirement that put both files in the tree: "A
WSGI entrypoint and a production server are required, because Flask's development server is
not production-grade. This adds ``wsgi.py``, ``run.py``, and ``gunicorn.conf.py``." Deploy
with the other one::

    gunicorn -c gunicorn.conf.py wsgi:app     # production (Dockerfile CMD, make serve)

ADDITIVE -- nothing here ports a source construct
-------------------------------------------------
AAP section 0.4.1, "Packaging and Entrypoints", binds this file in full: ``run.py`` | CREATE |
**ADDITIVE** | "Development entrypoint calling the factory".

The system being rewritten exposed no HTTP surface at all. Its entire orchestration was a
512-byte Groovy scripted pipeline -- ``node { stage('Clone code') ... stage('Run tests') ...
stage('Generate report') ... }`` ``[Jenkins:L1-L17]`` -- and its execution contract was the
Maven Surefire configuration ``[pom.xml:L17-L30]``. Neither has any notion of a listen
address. This module therefore replaces no Java, Groovy or Gherkin construct; it exists solely
because AAP section 0.8 records the prompt directive as mandatory: "'a Python 3 Flask
application' -- Flask is mandatory, not one option among several."

run.py versus wsgi.py -- deliberately two files
-----------------------------------------------
AAP section 0.4.1 names each individually, and they must not be merged:

* ``wsgi.py`` binds a module-level ``app`` so gunicorn can resolve ``wsgi:app``. It starts no
  server and carries no ``__main__`` guard, because under gunicorn it is imported, never run.
* ``run.py`` -- this file -- starts the development server, and only when executed as a
  script. It publishes no module-level application, so nothing here competes with the name
  gunicorn loads and importing it costs nothing.

Why the factory, and never a module-level Flask instance
--------------------------------------------------------
AAP section 0.3.3: "**Application Factory** -- ``app/__init__.py::create_app(config_name)``.
... Chosen because Flask's documentation recommends it and because ``tests/conftest.py`` needs
an isolated application per test, which a module-level global cannot provide." Section 0.4.2
names the concrete failure a global would cause: "without it, tests could not build isolated
application instances, and fixtures would share mutable state across the parallel workers
introduced by ``-n logical``" -- and that parallelism is itself a *parity* requirement, the
port of ``<parallel>methods</parallel>`` with ``<useUnlimitedThreads>true</useUnlimitedThreads>``
``[pom.xml:L22-L23]``. So there is no ``Flask(__name__)`` here, no module-level application,
and the single call to :func:`app.create_app` happens inside :func:`main`.

What this module deliberately does not do
-----------------------------------------
Every omission below has exactly one owner elsewhere in the tree; a second point of truth
would diverge the moment either changed:

* **No blueprint, error-handler or extension registration.** ``create_app()`` performs all of
  it, and imports the two blueprint objects *inside* its own body because AAP section 0.4.2
  requires it: "Blueprint objects are imported inside ``create_app()``, after construction, to
  prevent circular imports."
* **No ``.env`` loading.** ``app/config.py`` is the single ``python-dotenv==1.2.2`` load point
  -- rung three of the precedence chain -- and two load points would order unpredictably.
* **No logging configuration.** ``app/logging_config.py`` owns every handler, level and
  formatter, and the factory wires it; this module only obtains ``app.logger`` and emits.
  Nothing here prints (enterprise baseline B9, consistent with the preserved ``*.log`` ignore
  ``[.gitignore:L6]``).
* **No creation of ``target/``.** AAP section 0.6 assigns that to exactly three places --
  ``app/utils/paths.py``, the ``Makefile`` ``test`` target and ``tests/conftest.py`` -- and the
  factory reaches the first of them (validation criterion V7). A fourth creator would diverge
  from the specification.
* **No hard-coded secret key** (enterprise baseline B5). ``app/config.py`` owns the
  ``SECRET_KEY`` policy in full, including the deliberate refusal to start the production
  profile without a supplied key.
* **No clone, no test run and no report generation at start-up.** The three ported stages are
  reachable only through the HTTP surface -- ``POST /api/v1/clone``, ``POST /api/v1/runs``,
  ``POST /api/v1/reports`` -- or the service layer. AAP section 0.8: "No feature may be
  dropped, and none may be added."

Configuration -- read, never resolved
-------------------------------------
Three settings are read straight off the built application's ``config`` mapping, which
``create_app`` has already populated through the five-rung chain of AAP section 0.3.1
(``explicit constructor argument`` -> ``environment variable`` -> ``.env`` file ->
``configuration.properties`` -> hard-coded default). Their documented keys, every one present
in the committed ``.env.example``:

=================  ============================================================================
Key                Effect here
=================  ============================================================================
``APP_CONFIG``     profile handed to ``create_app(config_name)``. Unset or empty defers to
                   ``app/config.py``, whose documented default is ``development``
``APP_HOST``       listen address. ``.env.example`` calls it the "Listen address of both the
                   development server (``run.py``) and the production server"
``APP_PORT``       listen port. Defaults to ``8000 + CLONE_INDEX`` so parallel clones of this
                   repository do not collide -- exactly how the ``Makefile`` and
                   ``gunicorn.conf.py`` compute it
``FLASK_DEBUG``    Flask's own debug switch, surfaced on the mapping as the ``DEBUG`` key
=================  ============================================================================

No rung is re-implemented here and no profile name is passed as a literal: an explicit
argument is rung ONE, so a literal would outrank an operator's ``.env`` and break the
documented ordering. The only values spelled in this file are the two fallbacks below, and
they apply solely when a key is absent from the mapping altogether.

Safety of the two defaults
--------------------------
* **Debug is off unless it is asked for.** Every profile defaults ``DEBUG`` to false,
  ``.env.example`` pins ``FLASK_DEBUG=0``, and ``ProductionConfig`` refuses the switch
  outright. ``debug=True`` is never written here, and the reloader is tied to the same
  resolved flag, so a non-debug run starts exactly one process.
* **The fallback bind is loopback.** ``127.0.0.1`` is the only listen address literal in this
  file: no wildcard is hard-coded anywhere in it. A container-friendly bind stays
  environment-driven -- the ``Dockerfile`` (``ENV APP_HOST=0.0.0.0``) and
  ``docker-compose.yml`` set ``APP_HOST`` themselves -- and a run that resolves to a
  non-loopback address is reported at ``WARNING``, because Flask's interactive debugger would
  otherwise be reachable from off the machine.

Layering (AAP Rule T7, enterprise baseline B4)
----------------------------------------------
"``tests/`` may import from ``app/``; nothing under ``app/`` may import from ``tests/``." As an
entrypoint of the deployed import chain this module holds itself to the stricter form: it
imports the standard library, ``flask`` for one annotation, the factory, and the one
configuration constant naming the profile environment key. It reaches past the factory into no
sub-package -- not ``app.api``, ``app.web``, ``app.services``, ``app.reporting`` nor
``app.utils`` -- and imports nothing from ``tests/``, ``scripts/`` or ``docs/``.

It imports no harness distribution either: no ``pytest``, ``pytest_bdd``, ``selenium``,
``webdriver_manager`` or ``faker``. Those belong to ``requirements-test.txt``, while a
deployment installs ``requirements.txt`` alone (eleven pinned distributions), so
``python -c "import run"`` has to succeed against the runtime manifest by itself.

Import purity
-------------
Importing this module builds no application, reads no configuration, configures no logging,
creates no directory, opens no file and binds no port. Every one of those happens inside
:func:`main`, which runs only under the ``__main__`` guard at the foot of the file.
"""

import os
from typing import Final

from flask import Flask

from app import create_app
from app.config import CONFIG_NAME_ENV_VAR

# The one name this module publishes. `python run.py` reaches `main` through the `__main__`
# guard rather than by import, so `__all__` is declared to state that the constants below are
# internal and that no application object is exported from here -- `wsgi.py` owns that.
__all__ = ["main"]

# Listen address used ONLY when `APP_HOST` is absent from the application's configuration
# mapping. Loopback, deliberately: a development server that is unreachable from off the
# machine cannot expose Flask's interactive debugger to it. This is the only listen address
# literal in this file -- a wildcard bind is never hard-coded, it is requested through
# `APP_HOST`, which `app/config.py` resolves and the container images already set.
_FALLBACK_HOST: Final[str] = "127.0.0.1"

# Listen port used ONLY when `APP_PORT` is absent from the mapping. The same base value the
# `Makefile` (`APP_PORT ?= 8000 + CLONE_INDEX`), `gunicorn.conf.py` and `.env.example` use, so
# all four agree on where the service answers.
_FALLBACK_PORT: Final[int] = 8000

# Addresses that keep the development server on the local machine. Used for one WARNING, never
# to override a resolved value: the operator's `APP_HOST` decision always stands.
_LOOPBACK_HOSTS: Final[frozenset[str]] = frozenset({"127.0.0.1", "localhost", "::1"})


def main() -> None:
    """Build one application through the factory and serve it with Flask's own server.

    Called by the ``__main__`` guard below and by nothing else, so importing this module is
    free of side effects: no application is built and no port is bound until this runs.

    The profile comes from ``APP_CONFIG``; an unset or empty variable resolves to ``None``,
    which hands the remaining rungs back to :mod:`app.config` -- the module that owns them and
    whose documented default is ``development``. Host, port and debug are then read off the
    application's own configuration mapping, already resolved by that same module, so no rung
    of the precedence chain is re-implemented here.

    Returns:
        None. The call to :meth:`flask.Flask.run` blocks until the server is interrupted.
    """
    app: Flask = create_app(config_name=os.environ.get(CONFIG_NAME_ENV_VAR) or None)

    # `or _FALLBACK_HOST` covers an absent key and an empty string alike; `str()` keeps the
    # annotation honest for a mapping Flask types as `dict[str, Any]`.
    host: str = str(app.config.get("APP_HOST") or _FALLBACK_HOST)

    # `app/config.py` resolves APP_PORT with `whole_number(..., minimum=1)`, rejecting any
    # unparseable candidate at its own rung, so an int is what normally arrives here. The
    # isinstance guard covers the one case that bypasses that -- a caller mutating
    # `app.config` directly -- by falling back instead of failing to start.
    configured_port = app.config.get("APP_PORT")
    port: int = configured_port if isinstance(configured_port, int) else _FALLBACK_PORT

    # Off unless the environment asked for it: `Config.DEBUG` defaults to false in every
    # profile and `ProductionConfig` refuses `FLASK_DEBUG` outright. No literal `True` here.
    debug: bool = bool(app.config.get("DEBUG", False))

    if host not in _LOOPBACK_HOSTS:
        app.logger.warning(
            "The Flask DEVELOPMENT server is about to bind %s, which is not a loopback "
            "address, so it will be reachable from other hosts. This server is not production "
            "grade: set APP_HOST=%s to keep it local, or deploy with "
            "'gunicorn -c gunicorn.conf.py wsgi:app' instead.",
            host,
            _FALLBACK_HOST,
        )

    app.logger.info(
        "Starting the Flask DEVELOPMENT server for the %s profile on http://%s:%d (debug=%s). "
        "Production entrypoint is 'gunicorn -c gunicorn.conf.py wsgi:app'.",
        app.config.get("CONFIG_NAME", "unknown"),
        host,
        port,
        debug,
    )

    # `use_reloader` is tied to the resolved flag rather than left to Flask's default so that
    # a non-debug run is guaranteed to be a single process -- which is what `make run`, the
    # container health check and the documented readiness checks all expect.
    app.run(host=host, port=port, debug=debug, use_reloader=debug)


if __name__ == "__main__":
    main()
