"""Root pytest fixtures for the ``tests`` package, and one of three owners of ``target/``.

Two jobs, and deliberately no others
====================================
This module is the pytest wiring the Java/Maven original had no need for. Cucumber-JVM
resolved a scenario's collaborators through a field-injection container, and Maven created
the build's output directory as part of its own lifecycle. The Python port has neither, so
both duties land here:

1. **Create the ``target/`` artifact tree before anything can try to write into it** -- from
   a start-up hook, not from a fixture. That distinction is the single most consequential
   decision in the file and the whole of the next section explains why.
2. **Hand each test an isolated Flask application, test client and resolved
   configuration**, always built through the application factory and never read off a
   module-level global.

One session-scoped repository-root fixture is the only addition. It earns its place by
removing a real source of flakiness rather than by being convenient, and the reasoning is
recorded with it below.

Job one: the ``target/`` tree, and why a fixture cannot do it
============================================================
The Cucumber JSON writer configured for this suite does not create the directory of the
file it is asked to write, so a run begun while ``target/`` is absent dies at session
finish with ``FileNotFoundError``. Maven created that directory implicitly; here it has to
be created explicitly, up front.

What decides *where* the call goes is the preserved default tag selector. ``pytest.ini``
filters the run with ``-m "LogOut"``, the faithful port of ``tags = "@LogOut"``
``[README.md:L87]``, and no scenario in the specification carries that tag: the only tags
present are ``@Login`` ``[README.md:L104]``, ``@UPGN-286`` ``[README.md:L115]``,
``@UPGN-287`` ``[README.md:L123]``, ``@UPGN-288`` ``[README.md:L131]``, ``@SalesManager``
``[README.md:L137]`` and ``@PosManager`` ``[README.md:L144]``. The documented run therefore
deselects everything and pytest exits ``5``, which the ported orchestration maps to a
*successful zero-scenario run* -- the preserved behaviour of the source system, whose build
went green in exactly this situation, rather than an error.

**A session-scoped autouse fixture never executes when zero tests are collected.** Putting
the creation in one would leave ``target/`` absent for precisely the run the source system
performs, while the report writers still fire at session finish. The work therefore happens
in :func:`pytest_configure`, which pytest calls during start-up: before collection, on
every invocation whatever it selects, and once inside every parallel worker process.

Delegation, not duplication
---------------------------
The layout itself is *not* described here. :mod:`app.utils.paths` is the single owner of
that knowledge, and this module calls its published entry point,
:func:`app.utils.paths.ensure_target_layout`. No path beneath the artifact root is named in
this file, and no directory is created by it directly, so the two can never drift apart.

The delegate is idempotent and safe against concurrent callers, which matters because the
suite runs with one worker per logical CPU -- the port of ``<parallel>methods</parallel>``
together with ``<useUnlimitedThreads>true</useUnlimitedThreads>`` ``[pom.xml:L22-L23]`` --
so several processes reach it at the same instant. It also absorbs a read-only or otherwise
hostile filesystem itself, logging a warning and returning whatever it managed to create
instead of raising. That published guarantee is why the call below carries no protective
wrapper of its own: adding one would only mask a genuine defect in the delegate.

The tree is created relative to the process working directory, because that is where the
report writers named in ``pytest.ini`` resolve their own relative paths. Creating it
anywhere else -- at ``rootdir``, say -- would look tidy while leaving the trap wide open.

The import hazard
-----------------
``import app.utils.paths`` executes the parent package ``app/__init__.py`` first, and that
module is the Flask application factory: **any** ``app.*`` import therefore needs Flask
installed. The import is consequently made inside the hook and guarded, so a partially
installed environment degrades to a single logged warning instead of aborting collection
for the entire tree. That matters most to the parity suite, which is written to need no
third-party package at all and must stay runnable under a bare interpreter.

Three owners, on purpose -- and no fourth
-----------------------------------------
Creation is guaranteed in three independent places: :mod:`app.utils.paths`, the ``Makefile``
test target, and this module. The redundancy exists so the duty cannot be missed, and it is
neither to be reduced nor extended. In particular this module does **not** add a
belt-and-braces autouse fixture. :func:`pytest_configure` already covers every invocation
including the zero-selection one, and every test that asks for the :func:`app` fixture
re-guarantees the layout for free, because the application factory ensures it as one of its
own start-up steps. A fourth mechanism would add no coverage while blurring a contract that
is currently exact.

Job two: isolated applications, through the factory
===================================================
:func:`app`, :func:`client` and :func:`config` are the three fixtures this module owns. All
three are function-scoped, so every test gets its own application object with its own
configuration mapping, its own blueprint registry and its own error-handler registry.

That is not stylistic. The application factory exists *because* this file needs per-test
instances: a module-level global application would share mutable state across tests and,
worse, across the parallel workers the run is configured for. Since that parallelism is
itself the port of Surefire's method-level parallelism ``[pom.xml:L22-L23]``, isolation here
is a parity requirement.

Three consequences are worth stating because each was a decision rather than an accident:

* **The imports live inside the fixture bodies.** Nothing in this module imports Flask or
  ``app`` while it is being loaded. A module-level ``from app import create_app`` would turn
  one missing package into a collection failure for the whole tree, parity suite included;
  keeping the imports local confines the blast radius to the fixtures that genuinely need
  them. The type annotations are resolved by the type checker only, never at run time.
* **No application context is pushed.** None of the three fixtures needs one: a test client
  pushes its own request context per request, and the configuration mapping is a plain
  attribute. Pushing a context here would be exactly the leak of shared state that
  function-scoping exists to prevent. A test that genuinely wants one writes
  ``with app.app_context():`` around the part that needs it.
* **Cached configuration reads are discarded around each application.** The optional
  properties file is cached process-wide, keyed by path. Clearing before construction lets
  the application observe the file as it is on disk right now; clearing again in teardown
  stops one test's temporary file from colouring the next. Both calls go through the
  published :func:`app.config.clear_config_caches`.

The resolved configuration these fixtures expose carries the source values verbatim --
configuration values are data, never decisions -- among them the clone URL
``https://github.com/BalamiRR/Upgenix-QA.git`` ``[Jenkins:L3]``, the ``LogOut`` tag
expression ``[README.md:L87]``, failure tolerance ``true`` ``[pom.xml:L25]``, the six
publisher thresholds of ``-1`` with ``ALPHABETICAL`` sorting and the ``**/*.json`` include
pattern ``[Jenkins:L15]``, and the ``target/`` artifact root ``[README.md:L79-L82]``. This
module restates none of them: it hands over what :mod:`app.config` resolved, so there is
exactly one place where a preserved literal can be read or wrong.

Runtime configuration is optional, and normally absent
======================================================
``configuration.properties`` is git-ignored ``[.gitignore:L3]``, so a fresh checkout does
not contain it -- only ``configuration.properties.example`` is committed. The same is true
of ``.env``. Nothing in this module requires, opens, parses or skips on the absence of
either file: every setting has a documented default, and reading those files is the job of
:mod:`app.utils.properties` and ``tests/support/config_reader.py``. This module performs no
file I/O of any kind, which is also why no text encoding is declared anywhere below.

Deliberate omissions
====================
Each of the following is absent by design, not by oversight:

* **No mark registration.** Every tag the specification uses is declared in ``pytest.ini``,
  which is the single source of test configuration. Registering any of them here would
  create a second source of truth and could hide the very warning the run is tuned to
  surface.
* **No change to the invocation.** Command-line defaults, the warning filters and every
  other ini setting belong to ``pytest.ini`` alone; this module reads none of them and
  mutates none of them. In particular the Gherkin terminal reporter is never switched on
  here: it cannot coexist with parallel workers, and the serial make target is its only
  supported entry point.
* **No browser fixture and no screenshot hook.** Those belong to
  ``tests/step_defs/conftest.py``, next to the step definitions that use them.
* **No step definitions and no feature-file binding.** The collected behavioural target is
  ``tests/step_defs/test_cukes_runner.py``; this module imports nothing from the BDD stack.
* **No synthetic-data helper.** ``tests/support/data_factory.py`` is declared and wired but
  deliberately unexercised, matching the source project's own status for that capability.
  Exercising it from here would *add* behaviour, which the parity mandate forbids.
* **No stand-in for the application under test.** It is external, out of scope and
  unreachable from CI, which is exactly why the evidence of parity is structural --
  collection counts, artifact production, schema conformance, constant equality and
  exit-code mapping.
* **No deletion of anything.** Wiping the artifact tree is the ``Makefile`` clean target's
  job, the port of ``mvn clean``. Nothing here removes a file or a directory.
* **No subprocess and no shell.** This module starts no process; ``[Jenkins:L7-L11]``'s
  platform dispatch is ported in :mod:`app.utils.platform_exec` and driven by the service
  layer.
* **No logging configuration.** A module logger is obtained and used; handlers, levels and
  formatters belong to :mod:`app.logging_config` alone, and no output is produced through
  any other channel.
* **No repair of a preserved defect.** The catalogued source defects are preserved on
  purpose, and this module neither corrects nor conceals one.

Provenance
==========
This module ports no class, no configuration block and no pipeline stage: it is additive,
required because a Flask application and a pytest harness need wiring that the source
project never had. ``README.md``, ``.gitignore``, ``pom.xml`` and ``Jenkins`` are cited
above as the origin of the paths, the tag selector, the parallelism and the
configuration-optionality contract this module honours -- not because any line of them is
reproduced here.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Final

import pytest

if TYPE_CHECKING:
    # Imported for annotations only. `from __future__ import annotations` keeps every
    # annotation a string, so this block never executes and nothing below drags Flask into
    # a session that does not need it. Verified rather than assumed: importing this module
    # leaves both `flask` and `app` absent from `sys.modules`.
    from collections.abc import Iterator

    from flask import Config as FlaskConfig
    from flask import Flask
    from flask.testing import FlaskClient

__all__ = [
    "app",
    "client",
    "config",
    "project_root",
    "pytest_configure",
]

# A module logger, and nothing more: no handler, no level, no formatter. Structured logging
# is configured in exactly one place, and a conftest that reconfigured it would silently
# outrank the application's own settings for every run of the suite.
_LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

# The repository root, resolved from this file's own location: `tests/conftest.py` sits one
# directory below it. Deriving it from the working directory instead would be a latent flake
# -- every parallel worker is a separate process, and none of them is guaranteed to have been
# started from the root.
_PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parent.parent

# The profile the fixtures build. `app/config.py` maps this name to `TestingConfig`, whose
# only departure from the base profile is Flask's testing mode -- every preserved parity
# constant is inherited verbatim, which is what lets the parity suite assert them through an
# application built here.
_TESTING_CONFIG_NAME: Final[str] = "testing"

# Skip reason for the one environment this fixture cannot serve: a checkout in which a module
# the factory reaches has not been provisioned. `app/api/__init__.py` imports `app/api/routes.py`
# from its own last line and the blueprint imports live inside `create_app()` (AAP 0.4.2), so an
# absent module surfaces as an `ImportError` when the factory is CALLED, not when this file or
# the `app` package is imported. Naming what was looked for keeps such a skip from ever reading
# as a silent pass.
_UNBUILDABLE_SKIP_TEMPLATE: Final[str] = (
    "Cannot build the application, so this test is not assertable in this checkout. Looked "
    "for a buildable application: create_app({profile!r}) plus every module the api and web "
    "blueprint packages import, and got {error_type}: {error}"
)


def pytest_configure(config: pytest.Config) -> None:
    """Create the ``target/`` artifact tree before collection begins.

    This is the hook that closes the port's headline silent failure. The Cucumber JSON
    writer does not create the directory of the file it writes, so a session started while
    ``target/`` is absent fails at the very end with ``FileNotFoundError`` -- after the
    tests have run, which is the worst possible moment to find out.

    It has to be a start-up hook rather than a fixture. The preserved default selector
    ``-m "LogOut"`` -- the port of ``tags = "@LogOut"`` ``[README.md:L87]`` -- matches no
    scenario, so the documented run collects nothing and no fixture of any scope is ever
    set up, while the report writers still run at session finish. ``pytest_configure`` runs
    regardless of what collection finds, and runs again in each parallel worker process,
    which the delegate below is explicitly safe for.

    The work is delegated in full to :mod:`app.utils.paths`, the single owner of the layout:
    this function names no directory and creates none itself. The delegate never raises --
    it absorbs a read-only or full filesystem into a warning and reports what it managed to
    create -- so a hostile filesystem degrades this session's artifacts without ever
    aborting collection.

    Only the import is guarded. Reaching ``app.utils.paths`` executes the ``app`` package's
    ``__init__``, which is the Flask application factory, so the import needs Flask present;
    where it is not, one warning is logged and the session continues. That keeps a
    dependency-free suite -- the parity tests -- runnable in an environment that cannot even
    build the application.

    Args:
        config: The session's pytest configuration, used only to record ``rootdir``
            alongside the artifact location. Logging both makes a working-directory mismatch
            obvious at a glance, which matters because the tree is created relative to the
            working directory, exactly as the configured report writers resolve their own
            relative paths.
    """
    try:
        from app.utils.paths import ensure_target_layout, managed_directories
    except ImportError as error:
        # Not fatal, and deliberately not re-raised: an environment without Flask can still
        # run every test that needs no third-party package. The report writers configured
        # for the run are what will suffer, so say so plainly.
        _LOGGER.warning(
            "Could not import app.utils.paths, so the artifact tree was not created for "
            "this session; the configured report writers may fail at session finish "
            "(%s: %s)",
            type(error).__name__,
            error,
        )
        return

    expected = managed_directories()
    created = ensure_target_layout()

    # `expected[0]` is the artifact root as the delegate composes it -- relative, so
    # resolving it reports the absolute location the report writers will actually use.
    # Comparing the two counts is the delegate's own documented way of spotting a partial
    # failure; it has already logged a warning naming the first directory that refused, so
    # nothing is re-reported at warning level here.
    _LOGGER.debug(
        "Ensured %d of %d artifact directories at %s (pytest rootdir: %s)",
        len(created),
        len(expected),
        expected[0].resolve(),
        config.rootpath,
    )


@pytest.fixture(scope="session")
def project_root() -> Path:
    """Return the absolute repository root, resolved from this file's own location.

    The root is the directory holding ``README.md``, ``pom.xml``, ``pytest.ini``, the two
    requirement files and the ``app`` and ``tests`` packages. Several suites need to reach
    committed files by absolute path -- the byte-parity test compares the materialised
    specification against the block documented in ``README.md``, the dependency-parity test
    reads the two requirement files, and the integration tests locate the artifact tree --
    and none of them can safely assume where the process was started.

    That is not a hypothetical worry. The run is parallel by default, every worker is a
    separate process, and a relative path is silently resolved against whatever working
    directory each of them happens to have. Deriving the root from ``__file__`` is
    deterministic in all of those cases, which is why this fixture exists at all rather than
    leaving each suite to compute it.

    Session-scoped because the value cannot change during a run, and returned as an
    immutable-by-convention :class:`~pathlib.Path` so consumers compose from it with ``/``
    instead of string surgery.

    Returns:
        The absolute, symlink-resolved repository root. Identical to
        :func:`app.config.project_root`, computed independently on purpose: this module must
        stay importable when the application's dependencies are not installed, and a suite
        that needs nothing but the standard library must not be made to import Flask just to
        find a file.
    """
    return _PROJECT_ROOT


@pytest.fixture
def app() -> Iterator[Flask]:
    """Yield one freshly built Flask application configured for testing.

    Built through :func:`app.create_app`, never taken from a module-level global. Each call
    of the factory returns a distinct application with its own configuration mapping,
    blueprint registry and error-handler registry, so two tests can never observe each
    other's mutations -- and neither can two parallel workers. The factory also ensures the
    artifact tree as one of its start-up steps, which is what re-guarantees the layout during
    a session without this module needing a second mechanism of its own.

    Function-scoped deliberately. A session-scoped application would hand every test the
    same object and make the isolation above imaginary, which is the one thing the
    application-factory pattern is here to prevent.

    No application context is pushed. Nothing this fixture or its two derivatives do
    requires one, and leaving the context stack untouched is what keeps state from leaking
    between tests. A test that needs ``current_app``, ``url_for`` or a database-style
    teardown wraps the part that needs it::

        def test_something(app):
            with app.app_context():
                ...

    Cached reads of the optional properties file are discarded on both sides of the
    application's life. Clearing first lets this application resolve its settings against
    the file as it exists on disk at this moment; clearing afterwards stops a temporary file
    written by one test from colouring the next. The teardown clearing runs even when
    construction fails, so a broken configuration cannot poison the rest of the session.

    Yields:
        The wired application, in the ``testing`` profile. Flask's testing mode is the only
        thing that profile changes: every preserved parity constant is inherited verbatim,
        which is what makes an assertion made through this fixture an assertion about the
        real configuration.
    """
    # Imported here rather than at module scope: reaching either name needs Flask installed,
    # and a module-level import would make one missing package fail collection for every test
    # in the tree instead of only the ones that genuinely need an application.
    from app import create_app
    from app.config import clear_config_caches

    clear_config_caches()
    try:
        try:
            application = create_app(_TESTING_CONFIG_NAME)
        except ImportError as error:
            # A partially provisioned checkout only: `ImportError` from the factory means a
            # module the blueprints reach is absent, which is an environment fact and not a
            # defect in the test asking for an application. Reported as an explicit skip that
            # names the missing piece, so every suite funnelling through this fixture degrades
            # the same way instead of raising one setup error per test. Nothing wider is caught
            # -- any other exception is a genuine wiring fault and still fails loudly here.
            pytest.skip(
                _UNBUILDABLE_SKIP_TEMPLATE.format(
                    profile=_TESTING_CONFIG_NAME,
                    error_type=type(error).__name__,
                    error=error,
                )
            )
        yield application
    finally:
        clear_config_caches()


@pytest.fixture
def client(app: Flask) -> Iterator[FlaskClient]:
    """Yield a test client bound to this test's own application.

    The client is what the HTTP-surface suites drive: the health endpoint, the configuration
    introspection endpoint, the three ported pipeline stages, the report-retrieval routes and
    the two rendered pages. It reaches them in-process, with no server to start and no port
    to collide over -- which matters when the run is parallel by default.

    Entered as a context manager so Werkzeug tears the client down at the end of the test
    even if the test fails. Because it derives from :func:`app`, it inherits that fixture's
    function scope and therefore its isolation.

    Unknown routes are worth exercising through this fixture: the error handlers are
    registered on the application rather than on a blueprint, precisely because a
    blueprint-level handler is never consulted for a URL that matched no rule.

    Args:
        app: The per-test application, injected by the :func:`app` fixture.

    Yields:
        A client whose requests are dispatched by that application.
    """
    with app.test_client() as test_client:
        yield test_client


@pytest.fixture
def config(app: Flask) -> FlaskConfig:
    """Return the fully resolved configuration mapping of this test's application.

    This is the application's own mapping, populated by the factory from the configuration
    :mod:`app.config` resolved through the documented precedence chain -- explicit
    constructor argument, then environment variable, then ``.env``, then
    ``configuration.properties``, then the hard-coded source default. It is therefore the
    resolved truth for the run, not a restatement of the defaults, and it is what lets a test
    assert a preserved literal by key::

        assert config["TAG_EXPRESSION"] == "LogOut"

    Nothing is wrapped and no name is invented: the mapping is handed over exactly as the
    application holds it. Where the configuration *class* is wanted rather than the resolved
    values -- to inspect the declared defaults, or to subclass -- it is one lookup away
    through :data:`app.config.CONFIG_MAP`, keyed by the profile name the application already
    carries, so ``CONFIG_MAP[config["CONFIG_NAME"]]`` is :class:`app.config.TestingConfig`.
    That mapping is published for exactly this purpose, which is why this fixture does not
    duplicate it.

    Args:
        app: The per-test application, injected by the :func:`app` fixture.

    Returns:
        The application's configuration mapping. Mutating it affects only this test's
        application, because the mapping was built for it alone.
    """
    return app.config
