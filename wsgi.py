"""Production WSGI entrypoint: the module gunicorn imports as ``wsgi:app``.

Flask's development server is explicitly not production grade, so the deliverable ships a
production WSGI server and this module is the single object it loads::

    gunicorn -c gunicorn.conf.py wsgi:app        # make serve / Dockerfile CMD

Four committed artifacts already spell that target, so the module name ``wsgi`` and the
attribute name ``app`` are a fixed contract rather than a convention: ``Dockerfile`` ends with
``CMD ["gunicorn", "-c", "gunicorn.conf.py", "wsgi:app"]``, the ``Makefile`` ``serve`` target
invokes the same string, ``gunicorn.conf.py`` defaults ``wsgi_app`` to ``"wsgi:app"``, and
``.env.example`` records both ``FLASK_APP=wsgi.py`` and ``GUNICORN_WSGI_APP=wsgi:app``. Rename
either half and every one of them breaks.

``run.py`` is the separate DEVELOPMENT entrypoint (``make run``); it starts Flask's own server
and is never used by gunicorn. This module never starts a server itself -- see "What this
module deliberately does not do" below.

ADDITIVE -- nothing here ports a source construct
-------------------------------------------------
Agent Action Plan (AAP) section 0.4.1, "Packaging and Entrypoints", binds this file in full:
``wsgi.py`` | CREATE | **ADDITIVE** | "``app = create_app()`` for gunicorn". Section 0.1.1
gives the reason it exists at all: "A WSGI entrypoint and a production server are required,
because Flask's development server is not production-grade. This adds ``wsgi.py``, ``run.py``
and ``gunicorn.conf.py``."

The system being rewritten exposed NO HTTP surface whatsoever. Its entire orchestration was a
512-byte Groovy scripted pipeline -- ``node { stage('Clone code') ... stage('Run tests') ...
stage('Generate report') ... }`` ``[Jenkins:L1-L17]`` -- and its execution contract was the
Maven Surefire configuration ``[pom.xml:L17-L30]``. Neither has any notion of a WSGI callable.
This module therefore replaces no Java, Groovy or Gherkin construct; it exists solely because
AAP section 0.8 records the prompt directive as mandatory: "'a Python 3 Flask application' --
Flask is mandatory, not one option among several."

Why the factory, and never a module-level Flask instance
-------------------------------------------------------
AAP section 0.3.3: "**Application Factory** -- ``app/__init__.py::create_app(config_name)``.
... Chosen because Flask's documentation recommends it and because ``tests/conftest.py`` needs
an isolated application per test, which a module-level global cannot provide." Section 0.4.2
names the concrete failure a global would cause: "without it, tests could not build isolated
application instances, and fixtures would share mutable state across the parallel workers
introduced by ``-n logical``."

That parallelism is itself a PARITY requirement -- the port of ``<parallel>methods</parallel>``
with ``<useUnlimitedThreads>true</useUnlimitedThreads>`` ``[pom.xml:L22-L23]`` -- so calling the
factory here is behavioural fidelity, not tidiness. The same property carries into deployment:
``gunicorn.conf.py`` leaves ``preload_app`` disabled precisely because "the application factory
reads configuration at import time; preloading would share that state across workers", so every
worker imports this module and receives its OWN application with its own configuration mapping,
blueprint registry and error-handler registry.

What this module deliberately does not do
-----------------------------------------
Each omission below has exactly one owner elsewhere in the tree, and duplicating it here would
create a second, competing point of truth:

* **No blueprint, error-handler, extension or CLI registration.** ``create_app()`` performs all
  of it, and imports the two blueprint objects *inside* its own body because AAP section 0.4.2
  requires it: "Blueprint objects are imported inside ``create_app()``, after construction, to
  prevent circular imports."
* **No development-server call and no ``__main__`` guard block.** Under gunicorn this module is
  imported, never executed as a script; starting a server belongs to ``run.py`` alone.
* **No ``.env`` loading.** ``app/config.py`` is the single ``python-dotenv==1.2.2`` load point --
  the third rung of the precedence chain -- and two load points would order unpredictably.
* **No logging configuration.** ``app/logging_config.py`` owns every handler, level and
  formatter, and the factory wires it. Nothing here prints (enterprise baseline B9).
* **No creation of ``target/``.** AAP section 0.6 assigns that to exactly three places --
  ``app/utils/paths.py``, the ``Makefile`` ``test`` target and ``tests/conftest.py`` -- and the
  factory reaches the first of them. A fourth creator would diverge from the specification.
* **No hard-coded secret key, host or port.** ``gunicorn.conf.py`` resolves the bind address and
  ``app/config.py`` owns the ``SECRET_KEY`` policy, including the deliberate refusal to start
  the production profile without a supplied key. That refusal is left to propagate: catching it
  here would turn a fatal misconfiguration into a service that looks healthy while signing
  sessions with a placeholder.

Layering (AAP Rule T7, enterprise baseline B4)
----------------------------------------------
"``tests/`` may import from ``app/``; nothing under ``app/`` may import from ``tests/``." As the
head of the deployed import chain this module holds itself to the stricter form: it imports the
standard library, ``flask`` (for the annotation on :data:`app`), the factory, and the one
configuration constant naming the profile environment key. It imports nothing from ``tests/``,
``scripts/`` or ``docs/``, and reaches past the factory into no sub-package -- not ``app.api``,
``app.web``, ``app.services``, ``app.reporting`` nor ``app.utils`` -- because bypassing the
factory would bypass the layering the AAP diagrams.

It also imports no harness distribution: no ``pytest``, ``pytest_bdd``, ``selenium``,
``webdriver_manager`` or ``faker``. Those belong to ``requirements-test.txt``, while the
deployed container installs ``requirements.txt`` alone (eleven pinned distributions), so
``python3 -c "import wsgi"`` has to succeed against the runtime manifest by itself. Six modules
under ``app/`` cite that chain as the reason they stay harness-free; this file is where it
starts.

Choosing the configuration profile
----------------------------------
The profile is read from the environment so the factory can be pointed at a different
configuration class without editing code, consistent with the five-rung precedence chain of AAP
section 0.3.1: ``explicit constructor argument`` -> ``environment variable`` -> ``.env`` file ->
``configuration.properties`` -> hard-coded default.

An unset or empty variable resolves to ``None``, which hands the remaining rungs back to
``app/config.py`` -- the module that owns them, and which resolves a whitespace-only value to
the same documented default. No profile name is hard-coded here, and that is deliberate on three
independent grounds:

1. An explicit argument is rung ONE, so any literal passed from here would OUTRANK an operator's
   ``.env``. Pinning a name would therefore not merely duplicate resolution, it would break the
   documented ordering.
2. ``ProductionConfig`` requires ``SECRET_KEY`` to be supplied and raises
   ``ConfigurationError`` otherwise, so a hard-coded ``"production"`` would make the documented
   smoke check ``python3 -c "import wsgi"`` -- and ``make serve``, which sets no profile -- fail
   in every checkout that has not exported a key.
3. ``.env.example`` already documents the arrangement: ``APP_CONFIG=development`` suits a working
   copy, while "The container overrides it to ``production`` (Dockerfile ENV
   ``APP_CONFIG=production``; docker-compose.yml passes ``${APP_CONFIG:-production}``), so this
   default never reaches a deployment."

Reading with :func:`os.environ.get` rather than subscripting is what makes the default
production-safe in the operational sense that matters here: a missing variable can never raise
during a worker boot, so a deployment cannot be taken down by an unset orchestration setting,
and the profile a deployment actually runs comes from that deployment's own environment.

The contract this module must satisfy
-------------------------------------
Verified by ``tests/unit/test_app_factory.py`` and the integration suites, and by validation
criterion V11:

* ``type(app).__name__ == "Flask"`` and ``callable(app)`` -- a Flask application IS the WSGI
  callable gunicorn invokes.
* ``gunicorn --check-config -c gunicorn.conf.py wsgi:app`` exits 0.
* ``GET /health`` answers ``200`` with ``{"status": "ok"}``.
* ``sorted(app.blueprints) == ["api", "web"]`` -- exactly two, the ``/api/v1`` API blueprint and
  the root-mounted web blueprint. ``/health`` is on the application itself, not a third one.
* ``app.url_map`` carries the ten-rule surface of AAP section 0.3.1 -- ``/health``,
  ``/api/v1/config``, ``/api/v1/clone``, ``/api/v1/runs``, ``/api/v1/runs/<run_id>``,
  ``/api/v1/reports``, the three report-retrieval rules, plus ``/`` and ``/reports`` -- and
  Flask's own ``/static/<path:filename>``.
* An unknown URL returns ``404`` from the APPLICATION-level handler registered by
  ``app/errors.py``. AAP section 0.3.2: "Blueprint-level 404 and 405 handlers are **not**
  invoked for invalid URLs, because a blueprint does not own a URL space."

Usage
-----
::

    gunicorn -c gunicorn.conf.py wsgi:app          # production (Dockerfile CMD, make serve)
    APP_CONFIG=testing gunicorn wsgi:app           # any other profile, no code change
    python3 -c "import wsgi"                       # deployment smoke check
"""

import os
from typing import Final

from flask import Flask

from app import create_app
from app.config import CONFIG_NAME_ENV_VAR

# The one name this module publishes. gunicorn resolves `wsgi:app` by attribute lookup, so
# `__all__` does not gate it -- it is declared to state that the profile constant below is
# internal and that nothing else here is part of the public surface.
__all__ = ["app"]

# Profile requested by the process environment, or None to defer.
#
# `CONFIG_NAME_ENV_VAR` is imported rather than spelled as a literal so that the string
# "APP_CONFIG" exists in exactly one place: app/config.py, which also documents it in
# `.env.example` section 1 and excludes it from `configuration.properties` on purpose (the
# profile decides HOW the rest of the configuration is read, so it cannot be read from a file
# the profile selects).
#
# `or None` normalises an EMPTY assignment -- the `APP_CONFIG=` that a compose file or a CI
# environment produces when its own variable is unset -- to "not requested", so that the empty
# string is never handed on as a profile name. A whitespace-only value needs no handling here:
# `app.config.resolve_config_name` already treats any blank name as "consult the environment",
# and duplicating that test would re-implement resolution this module deliberately delegates.
# The remaining rungs stay with app/config.py; see "Choosing the configuration profile" above
# for why no profile name is pinned here.
_CONFIG_NAME: Final[str | None] = os.environ.get(CONFIG_NAME_ENV_VAR) or None

# THE WSGI CALLABLE. Built by the factory, named exactly `app`, at module import time.
#
# Import time is correct rather than incidental: gunicorn imports this module in each worker
# (`preload_app` stays disabled), so every worker builds its own fully wired application and no
# mutable state is shared between them. A Flask instance implements `__call__(environ,
# start_response)`, so this object needs no adapter to serve as the WSGI application.
#
# `config_name` is passed by keyword to say plainly which of the factory's parameters it is;
# `**overrides` is deliberately not used, because rung-one setting overrides baked into an
# entrypoint would outrank every environment this module is deployed into.
#
# The annotation is explicit so the strict type-check gate verifies a declared type rather than
# an inferred one. `Flask` is imported for that purpose only and is never instantiated here: a
# Flask instance built at module scope is precisely the module-level global the application
# factory exists to eliminate.
app: Flask = create_app(config_name=_CONFIG_NAME)
