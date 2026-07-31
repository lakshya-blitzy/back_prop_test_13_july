"""Application package and factory: the Flask port of the pipeline's ``node { }`` container.

This package is the CPython/Flask rewrite of the only orchestration the source repository
ever had -- a Groovy *scripted* Jenkins pipeline, 512 bytes and seventeen physical lines
long, whose outermost construct is an executor block ``[Jenkins:L1-L17]``::

    node {
        stage('Clone code')      { git 'https://github.com/BalamiRR/Upgenix-QA.git' }
        stage('Run tests')       { if (isUnix()) { sh  "mvn clean test" }
                                   else          { bat "mvn clean test" } }
        stage('Generate report') { cucumber failedFeaturesNumber: -1, ... }
    }

``node { ... }`` allocates an executor and *holds* the three stages; it performs no work of
its own. Its Python analogue is therefore :func:`create_app`, the application factory that
builds the object holding the three ported stage services -- one module per stage, per Agent
Action Plan (AAP) Rule T2, "One source construct, one target module":

* ``app/services/clone_service.py``       ports stage ``'Clone code'``     ``[Jenkins:L2-L4]``
* ``app/services/test_runner_service.py`` ports stage ``'Run tests'``      ``[Jenkins:L6-L11]``
  together with the Maven Surefire execution semantics ``[pom.xml:L21-L29]``
* ``app/services/report_service.py``      ports stage ``'Generate report'`` ``[Jenkins:L13-L15]``
* ``app/services/pipeline_service.py``    ports the container that runs all three, in order

This module imports none of them. It reaches them only transitively, through the two
blueprints it registers, which is what keeps the layering below honest.

Why an HTTP surface exists at all
--------------------------------
The source system had none. AAP section 0.8 is binding on the point: "'a Python 3 Flask
application' -- Flask is mandatory, not one option among several. This is why an HTTP surface
exists at all in a system that had none (AMB-6), and why the application factory and
blueprint patterns are structural rather than stylistic."

Structural, concretely: AAP section 0.4.2 records that without a factory "tests could not
build isolated application instances, and fixtures would share mutable state across the
parallel workers introduced by ``-n logical``". That parallelism is itself a *parity*
requirement -- the port of ``<parallel>methods</parallel>`` plus
``<useUnlimitedThreads>true</useUnlimitedThreads>`` ``[pom.xml:L22-L23]`` -- so a
module-level global application would break behavioural parity, not merely tidiness. There
is consequently no module-level ``Flask(__name__)`` here, and no module-level mutable
singleton of any kind.

The HTTP surface is CLOSED at ten rules
---------------------------------------
Every route was derived mechanically from a pipeline stage or a report artifact (AAP
AMB-6), so an eleventh would invent capability the user never had -- AAP section 0.8: "No
feature may be dropped, and none may be added."

===================================================  ==========================  ==========
Method and path                                      Ports                       Owner
===================================================  ==========================  ==========
``GET  /health``                                     ADDITIVE -- liveness probe  **here**
``GET  /api/v1/config``                              configuration introspection ``app.api``
``POST /api/v1/clone``                               ``'Clone code'``            ``app.api``
``POST /api/v1/runs``                                ``'Run tests'``             ``app.api``
``GET  /api/v1/runs/<run_id>``                       run status and summary       ``app.api``
``POST /api/v1/reports``                             ``'Generate report'``       ``app.api``
``GET  /api/v1/reports/<run_id>/cucumber.json``      ``[README.md:L79]``         ``app.api``
``GET  /api/v1/reports/<run_id>/rerun.txt``          ``[README.md:L80]``         ``app.api``
``GET  /api/v1/reports/<run_id>/screenshots``        ``[README.md:L42-L43]``     ``app.api``
``GET  /`` and ``GET  /reports``                     ``[README.md:L152-L161]``   ``app.web``
===================================================  ==========================  ==========

Flask adds ``/static/<path:filename>`` automatically, which is how
``app/static/css/main.css`` reaches the rendered report surface.

Layering (AAP Rule T7, enterprise baseline B4)
----------------------------------------------
    "Strict one-direction internal dependencies. ``api -> services -> reporting -> utils``.
    Nothing under ``app/`` may import from ``tests/``."

AAP section 0.4.2's graph fixes this module's edges exactly: ``FAC --> API``,
``FAC --> WEB``, ``FAC --> ERR``, ``FAC --> CFG``, ``FAC --> LOG``, and
``FAC -.->|MUST NOT import| TST``. Therefore:

* MAY import at module scope: the standard library, ``flask``, :mod:`app.config`,
  :mod:`app.extensions`, :mod:`app.errors`, :mod:`app.logging_config` and
  :mod:`app.utils.paths`.
* MUST import inside :func:`create_app`: :mod:`app.api` and :mod:`app.web`. AAP section
  0.4.2, verbatim -- "Blueprint objects are imported inside ``create_app()``, after
  construction, to prevent circular imports." The blueprint packages import
  ``app.services``, which imports ``app.reporting`` and ``app.utils``; a module-scope import
  here would risk a cycle straight back through this package's ``__init__``.
* MUST NEVER import: ``app.services`` or ``app.reporting`` directly, anything under
  ``tests/`` or ``scripts/``, or a harness-only distribution (``pytest``, ``pytest_bdd``,
  ``selenium``, ``webdriver_manager``, ``faker``). Those live in
  ``requirements-test.txt``, while the deployed container installs ``requirements.txt``
  alone, so the ``wsgi.py`` -> this module chain has to import cleanly there.

Nothing is re-exported from this package root either: a convenience re-export of a service,
reporting or utility symbol would let a consumer bypass the very layering above. No
``__version__`` is declared here, because version metadata lives once, in ``pyproject.toml``
(``version = "1.0.0.dev0"``, the PEP 440 rendering of ``<version>1.0-SNAPSHOT</version>``
``[pom.xml:L9]``); two sources would drift.

Import purity
-------------
Importing this package has no side effects: no application is built, no configuration is
read, no logging is configured, no directory is created, no file is opened and no port is
bound. Every one of those happens inside :func:`create_app`, once per application.

Behaviour that must not be broken
---------------------------------
The rewrite mandate is literal -- "fully matches the behavior and logic of the current
implementation" -- so the source's defects are behaviour and are preserved as defaults (AAP
Rule T4). Two of them constrain this module by telling it what *not* to add:

* **D2** -- the documented runner selects zero scenarios. ``tags = "@LogOut"``
  ``[README.md:L87]`` matches no tag in the feature file, so the preserved default
  ``-m "LogOut"`` deselects everything and pytest exits ``5``. That is a *successful
  zero-scenario run*, mapped in ``app/services/test_runner_service.py`` (validation
  criterion V6). This factory adds no gating that could override it.
* **D3** -- the pipeline is non-gating. ``<testFailureIgnore>true</testFailureIgnore>``
  ``[pom.xml:L25]`` plus the six ``-1`` publisher thresholds ``[Jenkins:L15]`` mean test
  failures never fail the run, and report generation happens even after a failed test stage
  from the finally-equivalent path in ``pipeline_service.py`` (criterion V12). No health
  check, start-up check or middleware here fails on a test outcome.

``pom.xml`` itself is a READ-ONLY parity contract (AAP AMB-4): it is the authoritative source
of every version, plugin setting and threshold mirrored into the Python manifests, and it is
never compiled and never edited. No ``.java``, ``.js``, ``.ts`` or ``.mjs`` file exists
anywhere in this tree (AAP section 0.9.4, Rule T6).

Usage
-----
::

    from app import create_app

    app = create_app()                       # profile from APP_CONFIG, then .env, then default
    app = create_app("testing")              # explicit profile
    app = create_app("testing", DEBUG=False)  # plus rung-one setting overrides

``wsgi.py`` binds the result to a module-level name ``app`` so that gunicorn can load
``wsgi:app``; ``run.py`` starts the development server from it. Neither loads ``.env``:
:mod:`app.config` is the single load point for every rung of the precedence chain.

Further reading
---------------
``docs/architecture.md`` maps the modules, ``docs/api.md`` is the route reference,
``docs/configuration.md`` documents the precedence chain, ``docs/ci.md`` explains the
non-gating property, and ``docs/migration-parity.md`` is the authoritative D1-D9 defect
register -- read it before "fixing" anything this application reports.
"""

import logging
from pathlib import Path
from typing import Final

from flask import Flask, Response, jsonify

from app.config import load_config
from app.errors import register_error_handlers
from app.extensions import cors
from app.logging_config import configure_logging
from app.utils.paths import (
    TARGET_DIR_NAME,
    ensure_target_layout,
    managed_directories,
    resolve_layout,
    to_posix,
)

# The single name this package publishes. `wsgi.py`, `run.py`, the Makefile, the Dockerfile
# and docker-compose.yml all reach the application exclusively through this callable, so the
# name, the `config_name` parameter and the returned `Flask` instance are a fixed contract.
# Nothing else is exported: see "Layering" in the module docstring for why a convenience
# re-export would be a defect rather than a courtesy.
__all__ = ["create_app"]

# This module's logger, obtained and never configured -- app/logging_config.py owns every
# handler, level and formatter, and states the rule for everyone else: "Anywhere else --
# obtain a logger, never configure one". The name resolves to `app`, which is both this
# package's import name and `logging_config.APPLICATION_LOGGER_NAME`, so this is the very
# logger `configure_logging` sets the level on and the same object Flask exposes as
# `app.logger`. Nothing here prints (enterprise baseline B9).
_LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

# Flask's own defaults, passed explicitly rather than relied upon, so that the two
# presentation roots of AAP section 0.3.1's tree -- `app/templates/` holding base.html,
# index.html, reports.html and errors/{404,500}.html, and `app/static/` holding css/main.css
# -- are confirmed at the call site instead of assumed, and cannot drift if a future Flask
# release changes a default. Both are resolved relative to this package's directory.
_TEMPLATE_FOLDER: Final[str] = "templates"
_STATIC_FOLDER: Final[str] = "static"

# The one ADDITIVE rule in the entire HTTP surface. See `_health` for why it lives here.
_HEALTH_RULE: Final[str] = "/health"
_HEALTH_ENDPOINT: Final[str] = "health"
_HEALTH_STATUS: Final[str] = "ok"

# Configuration key naming the artifact root. `app/config.py` resolves it through the
# five-rung chain (`TARGET_DIR` in the environment, `target.dir` in
# `configuration.properties`) and defaults it to `app/utils/paths.TARGET_DIR`, so an operator
# who mounts a volume elsewhere re-bases the whole layout rather than stranding artifacts.
_TARGET_DIR_CONFIG_KEY: Final[str] = "TARGET_DIR"


# =============================================================================
# GET /health -- the single ADDITIVE route in the entire HTTP surface.
#
# IT IS REGISTERED HERE, AND NOWHERE ELSE. Do not add it to app/api/routes.py or
# to app/web/routes.py: a second registration would either raise
# `AssertionError: View function mapping is overwriting an existing endpoint` or,
# under a different endpoint name, add an eleventh application rule to a surface
# the plan closes at ten.
#
# Ownership is not arbitrary. AAP section 0.4.1 scopes app/api/routes.py to "the
# three ported stages plus report retrieval endpoints" and app/web/routes.py to a
# "server-rendered index of generated reports"; a liveness probe is neither. And
# its path is `/health`, not `/api/v1/health`, so it could not sit beneath the API
# blueprint's prefix even if the scoping allowed it. The application object is the
# only registration point left, and the factory is the only place that holds one.
#
# AAP section 0.3.1 marks it "ADDITIVE -- no source equivalent" and gives the sole
# justification: "only the health endpoint is additive, and it exists because a
# deployable service needs a liveness probe." It is not a feature; it is what makes
# the ported system deployable at all.
# =============================================================================


def _health() -> Response:
    """Answer the liveness probe with HTTP 200 and a minimal JSON body.

    Deliberately incapable of failing for a reason unrelated to liveness. It reads no
    configuration, touches no filesystem, opens no file, starts no subprocess, imports
    nothing lazily and calls into no service: if the WSGI worker can execute Python and
    Flask can route a request, this returns ``200 {"status": "ok"}``. A probe that can
    report ill health because a report artifact is missing or a clone target is unreachable
    is worse than no probe, because it takes a healthy container out of rotation.

    That restraint is also why it does not gate on anything the ported pipeline does. The
    source build could not fail on test results -- ``<testFailureIgnore>true</testFailureIgnore>``
    ``[pom.xml:L25]`` with all six publisher thresholds at ``-1`` ``[Jenkins:L15]``, preserved
    defect D3 -- and neither may this endpoint.

    Five sibling artifacts assert this contract: ``wsgi.py``, ``run.py``,
    ``gunicorn.conf.py``, the ``Dockerfile`` health check and ``docker-compose.yml`` all
    verify ``GET /health -> 200`` (validation criterion V11).

    Returns:
        A JSON response whose body is exactly ``{"status": "ok"}``. ``jsonify`` sets the
        ``application/json`` content type and the implicit ``200`` status.
    """
    return jsonify(status=_HEALTH_STATUS)


def _configured_artifact_root(app: Flask) -> Path | None:
    """Return the artifact root *app* is configured to use, or ``None`` to use the default.

    Args:
        app: The application under construction, already carrying its configuration.

    Returns:
        The configured root as a :class:`~pathlib.Path`, or ``None`` when the key is absent
        or holds something that cannot be a path -- in which case
        :func:`~app.utils.paths.ensure_target_layout` falls back to ``target/`` relative to
        the process working directory, which is what the ``Makefile``, ``pytest.ini`` and the
        Jenkins publisher glob all already express. A bogus value is reported and absorbed
        rather than raised: refusing to build an application over one malformed setting would
        be a worse failure than starting with the documented default.
    """
    configured = app.config.get(_TARGET_DIR_CONFIG_KEY)
    if isinstance(configured, str | Path):
        return Path(configured)
    if configured is not None:
        _LOGGER.warning(
            "Configuration key %s holds %r, which cannot be a filesystem path; using the "
            "default %r directory relative to the working directory instead.",
            _TARGET_DIR_CONFIG_KEY,
            configured,
            TARGET_DIR_NAME,
        )
    return None


def _ensure_artifact_layout(app: Flask) -> None:
    """Create the ``target/`` artifact tree by delegating to :mod:`app.utils.paths`.

    This closes the port's most consequential silent failure. AAP section 0.6, verbatim:
    "The Cucumber JSON writer does not create its parent directory. With ``target/`` absent,
    the run fails at session finish with a file-not-found error. Maven implicitly created
    ``target/``; the Python port must create it first. This requirement is assigned to three
    places so it cannot be missed: ``app/utils/paths.py``, the ``Makefile`` test target, and
    ``tests/conftest.py``." Validation criterion V7 grades it.

    NOT ONE DIRECTORY IS CREATED IN THIS MODULE. There is no directory-creation call anywhere
    in this file, on purpose: :mod:`app.utils.paths` is the named owner, and a fourth owner
    would diverge from the specification the moment the layout changed. The five directories it
    creates are ``target/``, ``target/cucumber/``, ``target/screenshots/``,
    ``target/error-shots/`` and ``target/surefire-reports/`` -- the last name retained
    verbatim for report-consumer parity.

    The root is never renamed. AAP section 0.3.1: the publisher glob
    ``fileIncludePattern: '**/*.json'`` ``[Jenkins:L15]`` needs no change, and "this is
    precisely why the Java-flavoured ``target/`` name is retained rather than renamed"; AAP
    section 0.4.3 adds that the glob "is preserved verbatim as data ... and must not be
    rewritten". :mod:`app.utils.paths` enforces the name, so a configured value re-bases the
    root rather than renaming it -- and a value that would rename it is reported below.

    This function never raises. :func:`~app.utils.paths.ensure_target_layout` degrades to a
    warning when the filesystem refuses, because the factory runs while ``wsgi.py`` is being
    imported and a read-only or restricted filesystem must not make that import fail.

    Args:
        app: The application under construction, already carrying its configuration.
    """
    configured_root = _configured_artifact_root(app)
    # `ensure_target_layout` takes the PARENT of the artifact root, not the root itself,
    # because `TargetLayout.for_base()` always appends the literal name `target`. For the
    # default `Path("target")` the parent is `Path(".")` and `Path(".") / "target"` is
    # `Path("target")` again, so the common case round-trips byte for byte; a re-based root
    # such as `<tmp>/target` round-trips identically.
    base_dir = None if configured_root is None else configured_root.parent
    layout_root = resolve_layout(base_dir).root

    if configured_root is not None and layout_root != configured_root:
        _LOGGER.warning(
            "Configured artifact root %s was not created: app/utils/paths.py owns the root "
            "name %r and the layout was materialised at %s instead. The Java-flavoured name "
            "is retained deliberately, because the Jenkins publisher matches reports with "
            "fileIncludePattern '**/*.json' [Jenkins:L15]; re-base the root instead of "
            "renaming it, or report writers pointed at the configured value will fail.",
            to_posix(configured_root),
            TARGET_DIR_NAME,
            to_posix(layout_root),
        )

    expected = managed_directories(base_dir)
    ensured = ensure_target_layout(base_dir)

    if len(ensured) < len(expected):
        # app/utils/paths.py has already logged the first underlying OSError. What it cannot
        # say, and what matters operationally, is that the application was built regardless.
        _LOGGER.warning(
            "Application %s was built with %d of %d artifact directories present under %s; "
            "start-up continues deliberately rather than failing, so that importing the WSGI "
            "entrypoint still succeeds on a read-only or restricted filesystem. Report "
            "artifacts written to the missing directories may fail.",
            app.name,
            len(ensured),
            len(expected),
            to_posix(layout_root),
        )
    else:
        # DEBUG, not INFO: the factory runs once per test and once per `-n logical` xdist
        # worker, so an INFO line here would be noise. Anomalies above are WARNING, so
        # nothing that matters is quiet.
        _LOGGER.debug(
            "Ensured %d artifact directories under %s for application %s",
            len(ensured),
            to_posix(layout_root),
            app.name,
        )


def _log_registered_surface(app: Flask) -> None:
    """Record the wired application's complete surface at ``DEBUG``, for observability.

    The factory is the only place that can see the *union* of the surface: neither blueprint
    can observe the other, and neither can observe the additive ``/health`` rule. Logging it
    here is what makes drift visible -- the plan closes the surface at ten rules plus Flask's
    automatic ``/static/<path:filename>`` (AAP section 0.3.1), and an eleventh would invent
    capability the source system never had (AAP section 0.8).

    Deliberately a log line and NOT an assertion, for two reasons. First, gating start-up on
    a rule inventory would contradict the non-gating property this whole port preserves
    (defect D3), and a deployment must not refuse to start over a route-map disagreement.
    Second, an expected-rule list held here would duplicate knowledge the blueprints own and
    would misfire on a harmless spelling difference -- ``<run_id>`` versus
    ``<string:run_id>`` render as different rule strings while routing identically. The
    authoritative surface is AAP section 0.3.1's table, and it is *asserted* by
    ``tests/unit/test_app_factory.py`` and ``tests/integration/test_api_routes.py``, where an
    assertion belongs.

    The error-handler scopes are included because they are the subject of validation
    criterion V11: Flask files an application-scoped handler under the ``None`` key of
    ``error_handler_spec``, so a line reading ``error-handler scope(s) ['application']`` is
    direct evidence that nothing was registered under a blueprint key -- where a 404 or 405
    handler would never fire for an unmatched URL (AAP section 0.3.2).

    Args:
        app: The fully wired application.
    """
    # Guarded because the inventory below is built eagerly; `%`-style lazy formatting cannot
    # defer that work, and there is no reason to pay for it when DEBUG is off.
    if not _LOGGER.isEnabledFor(logging.DEBUG):
        return

    rules = sorted(rule.rule for rule in app.url_map.iter_rules())
    blueprints = {name: blueprint.url_prefix or "/" for name, blueprint in app.blueprints.items()}
    handler_scopes = sorted(
        "application" if scope is None else f"blueprint:{scope}" for scope in app.error_handler_spec
    )
    _LOGGER.debug(
        "Application %s wired: %d rule(s) %s; blueprint(s) %s; error-handler scope(s) %s",
        app.name,
        len(rules),
        rules,
        blueprints,
        handler_scopes,
    )


def create_app(config_name: str | None = None, **overrides: object) -> Flask:
    """Build one fully wired Flask application -- the port of the pipeline's ``node { }``.

    A FRESH APPLICATION PER CALL, always. Two successive calls return two distinct objects
    with two distinct ``config`` mappings, two distinct blueprint registries and two distinct
    error-handler registries. Enterprise baseline B3 asks for "application factory plus
    blueprints rather than a module-level global application", and AAP section 0.4.2 names
    the failure mode a global would cause: "without it, tests could not build isolated
    application instances, and fixtures would share mutable state across the parallel
    workers introduced by ``-n logical``". Since that parallelism is the port of
    ``<parallel>methods</parallel>`` plus ``<useUnlimitedThreads>true</useUnlimitedThreads>``
    ``[pom.xml:L22-L23]``, isolation here is a parity requirement, not a preference.

    Wiring order, exactly as performed below and deliberately in this sequence:

    1. Construct the application and load the resolved configuration from
       :mod:`app.config`.
    2. Configure structured logging via :mod:`app.logging_config` -- early, so every step
       after it is observable, and before anything touches ``app.logger``.
    3. Bind the unbound extension singletons declared in :mod:`app.extensions`.
    4. Ensure the ``target/`` artifact layout exists, by delegating to
       :mod:`app.utils.paths`.
    5. Register the two blueprints, imported *inside this function*.
    6. Register the error handlers on the APPLICATION via :mod:`app.errors`.
    7. Register the single additive ``GET /health`` rule.
    8. Log the resulting surface and return the application.

    NOTHING IS EXECUTED THAT THE PIPELINE WOULD HAVE EXECUTED. No repository is cloned, no
    test run is started and no report is generated during construction. Those three stages
    are reachable only through the HTTP surface and the service layer; performing any of them
    at start-up would invent behaviour the source system never had (AAP section 0.8: "No
    feature may be dropped, and none may be added").

    Args:
        config_name: Profile to build -- ``"development"``, ``"testing"`` or
            ``"production"``, or one of the short forms :mod:`app.config` accepts
            (``dev``, ``test``, ``prod``, ``default``). ``None`` -- the default -- means
            "consult the environment", which :func:`app.config.resolve_config_name` reads as
            ``APP_CONFIG`` then an ``APP_CONFIG`` entry in ``.env`` then the documented
            default. An unrecognised name is never fatal: it falls back to the default with a
            warning. This module re-implements no rung of the precedence chain and never
            loads ``.env`` itself, so :mod:`app.config` stays the single load point.
        **overrides: Rung-one setting overrides, keyed exactly like their environment keys
            (``TAG_EXPRESSION=""``, ``IGNORE_TEST_FAILURES=False``, ``TARGET_DIR=...``).
            Forwarded verbatim to :func:`app.config.load_config`, which is what makes
            "explicit constructor argument" -- the first rung of the five-rung chain in AAP
            section 0.3.1 -- reachable through the factory. Ordinary Flask keys such as
            ``TESTING`` are accepted too; a key that is not upper case raises
            :class:`app.config.ConfigurationError`, because it cannot be a configuration key.

    Returns:
        The wired :class:`~flask.Flask` application, ready to serve. ``wsgi.py`` binds it to
        a module-level name ``app`` so gunicorn can load ``wsgi:app``.

    Raises:
        app.config.ConfigurationError: If an override key is not upper case, or if the
            selected profile requires a signing key and none was supplied -- which is exactly
            what stops a production deployment from silently signing sessions with the
            committed development placeholder. No signing key is hard-coded here (enterprise
            baseline B5); :mod:`app.config` owns that policy in full.
    """
    # -- 1. Construct the application, then load its configuration -------------------------
    #
    # `__name__` is `"app"`, so the package directory is the application root and the two
    # folder arguments below resolve to `app/templates/` and `app/static/`. `app.logger` is
    # therefore the logger named `app` -- the very logger `_LOGGER` holds and the one
    # `configure_logging` configures in step 2.
    app = Flask(
        __name__,
        template_folder=_TEMPLATE_FOLDER,
        static_folder=_STATIC_FOLDER,
    )
    # `load_config` resolves every setting through the five-rung chain at the moment of this
    # call, which is what lets an environment change between two calls be honoured -- exactly
    # what per-test application instances need. Debug mode is never switched on here: every
    # profile defaults `DEBUG` to false and production refuses it outright.
    app.config.from_object(load_config(config_name, **overrides))

    # -- 2. Configure structured logging ---------------------------------------------------
    #
    # Early on purpose, so every diagnostic the remaining steps emit is rendered in the
    # configured format instead of escaping through `logging.lastResort`. Idempotent by
    # requirement: it removes only the handlers a previous call installed, so handler counts
    # never grow across the many applications one process builds -- one per test, one per
    # `-n logical` worker. Its return value is `app.logger`, which for this package's import
    # name is the same object `_LOGGER` already holds, so there is nothing to rebind; and it
    # must run before anything touches `app.logger`, which is why no logging happens above.
    configure_logging(app)

    # -- 3. Bind the extension singletons --------------------------------------------------
    #
    # `app/extensions.py` declares `cors` UNBOUND on purpose -- AAP section 0.3.2 records
    # that Flask "recommends the application-factory function so extensions are not bound to
    # one application instance at import time". Binding happens here, once per application,
    # and never there. No options are passed: flask-cors reads `CORS_ORIGINS` natively from
    # `app.config`, so the policy stays configurable through the five-rung chain instead of
    # being baked into the factory where it would outrank every application's own setting.
    cors.init_app(app)

    # -- 4. Ensure the artifact layout exists ----------------------------------------------
    #
    # Before any request can ask a report writer to produce a file. Delegated in full to
    # `app/utils/paths.py`; this module creates no directory itself (criterion V7).
    _ensure_artifact_layout(app)

    # -- 5. Register the two blueprints ----------------------------------------------------
    #
    # IMPORTED HERE, INSIDE THE FUNCTION BODY -- AAP section 0.4.2, verbatim: "Blueprint
    # objects are imported inside `create_app()`, after construction, to prevent circular
    # imports." The mechanism is concrete: these packages import `app.services`, which
    # imports `app.reporting` and `app.utils`, and any of those may legitimately be imported
    # while this module is still initialising. A module-scope import here would close that
    # cycle back through `app/__init__.py`. Keeping the import local is what keeps it open.
    from app.api import api_bp
    from app.web import web_bp

    # `url_prefix` is NOT re-specified. `app/api/__init__.py` declares
    # `Blueprint("api", __name__, url_prefix="/api/v1")` and is the single authoritative
    # place that fixes the mount point; Flask concatenates a registration-time prefix onto
    # the blueprint's own, so naming it again here would mount every rule at
    # `/api/v1/api/v1/...` and break the whole surface. `web_bp` carries no prefix at all,
    # which is what places its two rules at `/` and `/reports`.
    #
    # EXACTLY TWO BLUEPRINTS. There is no third: the additive `/health` rule goes on the
    # application in step 7, and `wsgi.py` asserts that `sorted(app.blueprints)` lists these
    # two and only these two.
    app.register_blueprint(api_bp)
    app.register_blueprint(web_bp)

    # -- 6. Register the error handlers on the APPLICATION ---------------------------------
    #
    # On the application object, never on a blueprint. AAP section 0.3.2 records the
    # researched reason: "Blueprint-level 404 and 405 handlers are not invoked for invalid
    # URLs, because a blueprint does not own a URL space. ... Had this been overlooked,
    # unknown routes would have produced Flask's default HTML error page instead of the
    # intended structured response." `app/errors.py` implements the dual shape baseline B8
    # asks for -- JSON beneath `/api/v1`, a rendered page elsewhere -- and this factory only
    # wires it. Registration order relative to step 5 is irrelevant: an application-scoped
    # handler is found by status code, not by sequence.
    register_error_handlers(app)

    # -- 7. Register the single ADDITIVE route ---------------------------------------------
    #
    # See the block comment above `_health`: this is the only route in the system with no
    # source equivalent, it is owned here, and it must not be registered anywhere else.
    # `methods=["GET"]` is explicit; Werkzeug adds `HEAD` and `OPTIONS` itself, which is
    # correct HTTP behaviour for a probe endpoint.
    app.add_url_rule(
        _HEALTH_RULE,
        endpoint=_HEALTH_ENDPOINT,
        view_func=_health,
        methods=["GET"],
    )

    # -- 8. Report the resulting surface, and return ---------------------------------------
    _log_registered_surface(app)
    return app
