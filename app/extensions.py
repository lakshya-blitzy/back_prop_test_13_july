"""Unbound Flask extension singletons, attached to an application by the factory.

ADDITIVE module - nothing here ports a source construct
-------------------------------------------------------
The Java/Maven Selenium-Cucumber project this repository is a rewrite of exposed
no HTTP surface whatsoever. Its orchestration was a Groovy scripted pipeline,
``node { ... }`` [Jenkins:L1-L17], and its execution contract was the Surefire
configuration of the POM [pom.xml:L17-L30]; neither has any notion of an
extension registry, because neither ever served a request. This module therefore
has *no* counterpart in the source system. It exists for exactly one reason: the
deliverable is defined as a Python 3 Flask application, which makes the
application-factory-plus-blueprints structure binding rather than decorative.
Every citation below explains *why* a declaration belongs here; none of them
claims that a Java, Groovy or Gherkin construct is being replaced.

What "unbound" means
--------------------
Every object declared in this module is constructed **without an application
argument**. Importing this module consequently attaches nothing to any
application, touches no configuration and mutates no global state. Each
application instance is wired up exactly once, inside ``create_app()`` in
:mod:`app`, which hands the freshly built application to this singleton's
per-application initialiser. This file is a declaration site and nothing more: it
performs no wiring of its own.

Why the unbound pattern is mandatory rather than stylistic
----------------------------------------------------------
An extension attached to one application at import time would defeat the whole
test strategy *and* break behavioural parity with the source build:

* ``tests/conftest.py`` obtains an isolated application per test through the
  factory instead of importing a module-level global, because a global that is
  already attached to one application cannot provide that isolation.
* The suite executes under pytest-xdist with ``-n logical`` worker allocation,
  and fixtures sharing mutable state across those workers is precisely the
  failure the factory pattern exists to prevent. That parallelism is itself a
  parity requirement - it is the port of Surefire's
  ``<parallel>methods</parallel>`` together with
  ``<useUnlimitedThreads>true</useUnlimitedThreads>`` [pom.xml:L22-L23], with the
  ``<threadCount>4</threadCount>`` tuning value at [pom.xml:L24] left disabled
  exactly as the source left it. An import-time attachment would therefore be a
  *behaviour* regression, not merely an untidiness.
* The mechanics of flask-cors 6.0.5 are what make the pattern safe, and they were
  read from the pinned distribution rather than assumed: the per-application
  initialiser mutates only the application handed to it - it registers an
  ``after_request`` hook on that application and wraps that application's
  exception handling - while the singleton itself keeps no reference to any
  application. One module-level object can consequently serve any number of
  independently created applications with zero cross-contamination.

No configuration here, deliberately
-----------------------------------
The singleton below takes no keyword arguments, and that is a load-bearing
decision rather than a minimalist flourish:

* Configuration ownership belongs to :mod:`app.config`, which implements the
  five-rung precedence chain ``explicit constructor argument`` ->
  ``environment variable`` -> ``.env`` file -> ``configuration.properties`` ->
  hard-coded source default. This module reads no environment variable, no
  ``.env`` file and no properties file, so there is exactly one load point for
  every setting.
* flask-cors resolves its options in the order ``library defaults`` ->
  ``CORS_*`` keys read from ``app.config`` -> constructor keyword arguments ->
  arguments passed at attachment time -> per-resource overrides. Constructor
  keyword arguments consequently *outrank* each application's own configuration,
  so any policy baked in here would silently override every application that the
  factory builds, per-test applications included. Policy is supplied at
  attachment time instead - either through the ``CORS_*`` application-config keys
  that flask-cors reads natively, or as arguments the factory passes when it
  attaches the extension.

Why exactly one singleton is the complete answer
------------------------------------------------
The runtime manifest ``requirements.txt`` declares eleven distributions. Seven
are Flask and its own stack, one is the production WSGI server, one is the
``.env`` loader, and one - ``pydantic==2.13.4`` - is a plain validation library
with no per-application lifecycle, whose models belong to :mod:`app.api.schemas`
and are deliberately not instantiated here. That leaves ``flask-cors==6.0.5`` as
the single initialisable Flask extension in the entire runtime dependency
inventory, declared there for "cross-origin handling for the routes that serve
the generated artifacts".

A module holding one object is therefore the correct and complete outcome, not an
unfinished one. Nothing may be added to it: the rewrite mandate is that no
feature is dropped and none is introduced, and this system has no database, no
cache, no message broker, no task queue and no authentication surface to serve.

Layering and import purity
--------------------------
This module sits at the very bottom of the internal import graph, below the
strict one-direction chain ``api -> services -> reporting -> utils``. It imports
one third-party package and nothing first-party, which is what lets the factory
module pull it in at module scope with no circular-import risk. It must never
reach for :mod:`app.config`, :mod:`app.api`, :mod:`app.web`,
:mod:`app.services`, :mod:`app.reporting` or :mod:`app.utils`, never for anything
under ``tests/`` or ``scripts/``, and never for a harness-only distribution - the
deployed container installs ``requirements.txt`` alone, so a stray harness import
here would stop the container from starting.

Importing this module is free of file and network access, of environment reads
and of every other side effect. It builds no application object (only the factory
does that), registers no blueprint and no error handler - handlers live in
:mod:`app.errors` at application scope, because a blueprint does not own a URL
space and a blueprint-scoped 404 or 405 never fires for an unmatched URL - writes
no log configuration, which belongs to :mod:`app.logging_config`, and does not
create the ``target/`` artifact tree, which :mod:`app.utils.paths`, the Makefile
test target and ``tests/conftest.py`` own between them.
"""

from flask_cors import CORS

# The single name this module publishes. ``create_app()`` performs
# ``from app.extensions import cors``, so this identifier is part of the
# application's internal contract and must not be renamed.
__all__ = ["cors"]

# Cross-origin handling for the report-retrieval routes of the ``/api/v1``
# blueprint - the endpoints that serve ``target/cucumber.json``,
# ``target/rerun.txt`` and the screen shots and error shots, so that the four
# report artifacts declared by the Cucumber plugin entries [README.md:L79-L82]
# and the shots described at [README.md:L42-L43] are retrievable over HTTP by a
# consumer served from another origin.
#
# Constructed with no arguments on purpose. The argument-free call is what leaves
# this object unattached: flask-cors only performs its per-application wiring
# when an application is supplied, and passing options here would outrank the
# configuration of every application the factory later builds (see the module
# docstring). Both the attachment and the policy are therefore the factory's
# responsibility.
#
# The annotation is explicit so that the strict type-check gate has a declared
# type to verify against rather than an inferred one; flask-cors ships a
# ``py.typed`` marker at the pinned version, so ``CORS`` is a fully typed symbol.
cors: CORS = CORS()
