"""Tests for ``create_app()``, the console-logging contract and the project pins.

Four whole-application contracts meet here, each about the application rather
than about a single view, writer or page object.  ``app/__init__.py`` is the
port's sole registration point (AAP 0.4.2) -- one blueprint, two error
handlers, one command, nothing else -- and it assigns the assertions for its
numbered acceptance criteria to this module, so each appears below with its
criterion number; the deferred-import criterion runs in a fresh interpreter,
the only place this session's imports cannot influence it.  The packaging is
proved from outside this session too, in a child interpreter started with the
checkout off its path, so a broken editable install or package mapping fails
here rather than being answered by the source tree.
``app/logging_config.py``'s stream split -- both streams line-buffered,
progress on stdout, engine diagnostics on stderr, per AAP 0.4.1 -- is asserted
on its factory side only, the command-line half belonging to
``tests/test_cli.py``.  And the declarations AAP 0.4.1 and 0.5.1 fix -- the
runtime pin, build and entry-point consistency, the requirement pins,
``.gitattributes``, the runner scripts and the CI publisher -- are asserted as
contracts rather than as wording, so rewording cannot fail a test while
removing a guarantee does.

``app/__init__.py`` - the factory
    The port's sole registration point (AAP 0.4.2): one blueprint, two error
    handlers, one command, and nothing else.  That file's docstring states
    twelve numbered acceptance criteria and says explicitly that this module
    owns their assertions, so every one of them is implemented below and the
    criterion number appears in the test's docstring.  The most important of
    them is criterion 1, the deferred-import proof: ``import app.utils.paths``
    must not drag Flask into a worker process, and it is checked in a fresh
    interpreter because that is the only place the result cannot be influenced
    by this session's own imports.

The packaging, proved from outside this session
    ``tests/conftest.py`` measures whether ``app`` resolves from the installed
    environment *before* it touches ``sys.path``, and publishes the answer as
    :fixture:`project_importable_from_environment`.  This module asserts that
    answer is ``True`` and then proves the rest in a child interpreter started
    with the checkout kept off its path, so a broken editable install or a
    broken ``[tool.setuptools.packages.find]`` / ``[tool.setuptools.package-
    data]`` mapping fails here instead of being answered by the source tree.

``app/logging_config.py`` - the stream split
    AAP 0.4.1 closes its CLI contract with "Both streams are line-buffered:
    progress to stdout, engine diagnostics to stderr".  The factory is one of
    the two callers of ``configure_logging()``, so the factory-side half of
    that contract is asserted here: the partition itself, non-duplication
    across repeated configuration, the line-buffer fallback, the once-per-
    process missing-configuration warning, and the rule that no configured
    credential ever reaches a stream.  The command-line half - the option
    table, the exit rows and the CLI's own streams - belongs to
    ``tests/test_cli.py`` and is not duplicated here.

The project-level declarations
    The runtime pin, the build and entry-point consistency, the runtime
    requirement pins, ``.gitattributes``, the two runner scripts' parity and
    the CI publisher's continuity are all invariants that no other test would
    notice drifting.  They are asserted as *contracts* - the fact the AAP
    fixes - rather than as exact wording, so that an editor rewording a
    sentence does not fail a test while an editor removing a guarantee does.

Two rules govern every test below.

**Every application comes from the factory.**  Either through
:fixture:`flask_app` / :fixture:`client`, or by calling ``create_app()``
directly where the test is about the factory itself.  Nothing here constructs
``Flask(...)``: an application assembled locally would prove nothing about the
wiring a user of this project can actually obtain.

**Nothing is skipped.**  Every file this module reads exists in the checkout,
and a skip would turn a missing guarantee into a green run - which is exactly
what the review of this checkpoint rejected.  A test that cannot establish its
contract fails, and says why.
"""

from __future__ import annotations

import ast
import contextlib
import io
import logging
import os
import re
import subprocess
import sys
import tomllib
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any, Final

import pytest
from flask import Flask, template_rendered
from flask.testing import FlaskClient

from app import create_app
from app.logging_config import (
    PACKAGE_LOGGER_NAME,
    STDERR_HANDLER_NAME,
    STDOUT_HANDLER_NAME,
    configure_logging,
)
from app.utils import paths, properties

# --------------------------------------------------------------------------- #
# The HTTP surface, as AAP 0.3.1 fixes it
#
# Endpoint name -> rule string, and the rule strings carry their converters, so
# this one mapping pins the endpoint names, the paths and the ``int``/``int``/
# ``path`` converters together.  The blueprint name is part of it: the viewer
# templates address ``web.index`` and its five siblings by name, so renaming
# the blueprint would break them at render time.
# --------------------------------------------------------------------------- #

#: The blueprint's registered key, fixed by the templates that use it.
BLUEPRINT_NAME: Final[str] = "web"

#: The six endpoints of AAP 0.3.1 and the rule each one answers on.
EXPECTED_RULES: Final[Mapping[str, str]] = {
    "web.index": "/",
    "web.reports_overview": "/reports",
    "web.report_feature": "/reports/features/<int:findex>",
    "web.report_scenario": (
        "/reports/features/<int:findex>/scenarios/<int:sindex>"
    ),
    "web.reports_summary": "/reports/summary",
    "web.artifact": "/artifacts/<path:name>",
}

#: Flask's own static endpoint - the one rule in ``url_map`` that this port
#: does not declare and the only one permitted alongside the six above.
FLASK_STATIC_ENDPOINT: Final[str] = "static"

#: The methods Werkzeug reports for a rule declared without ``methods``:
#: ``GET`` plus the two Flask adds automatically.  Every route in this port is
#: read-only, so this set is the whole of what any of them accepts.
READ_ONLY_METHODS: Final[frozenset[str]] = frozenset({"GET", "HEAD", "OPTIONS"})

#: Methods that would write.  Probed against every route, because "read-only"
#: has to be a property of the routing table rather than of the view bodies.
WRITING_METHODS: Final[tuple[str, ...]] = ("POST", "PUT", "PATCH", "DELETE")

#: One concrete URL per endpoint, for the tests that have to issue a request
#: rather than inspect ``url_map``.  The indexes and the artifact name need not
#: exist: a method rejection and a 404 both happen before any artifact is read.
CONCRETE_URLS: Final[Mapping[str, str]] = {
    "web.index": "/",
    "web.reports_overview": "/reports",
    "web.report_feature": "/reports/features/0",
    "web.report_scenario": "/reports/features/0/scenarios/0",
    "web.reports_summary": "/reports/summary",
    "web.artifact": "/artifacts/cucumber.json",
}

#: The name the CLI command is registered under, and the console script's name.
COMMAND_NAME: Final[str] = "run-tests"

#: Every template the HTTP surface renders, including the shell they extend and
#: the two error pages.  Each must resolve through Flask's package-relative
#: default, with no ``template_folder`` override anywhere.
VIEW_TEMPLATES: Final[tuple[str, ...]] = (
    "base.html",
    "index.html",
    "errors/404.html",
    "errors/500.html",
    "view/overview.html",
    "view/feature.html",
    "view/scenario.html",
)

#: The two static assets the base template and the artifact writer both need.
STATIC_ASSETS: Final[tuple[str, ...]] = ("css/main.css", "js/report.js")

# --------------------------------------------------------------------------- #
# Host-header validation, as the factory fixes it
#
# Nothing behind the six routes authenticates anything: they are public within
# a local trust boundary, and what stops that boundary being crossed *by name*
# - a page loaded under an attacker-controlled hostname reaching this machine's
# artifacts through the browser that loaded it - is the ``TRUSTED_HOSTS``
# allowlist the factory installs.  The spellings below are restated here rather
# than imported from the factory's private constant, so that the contract and
# the code have to be changed together instead of silently agreeing with each
# other.
# --------------------------------------------------------------------------- #

#: The allowlist every factory-built application must carry, in order: the
#: loopback address, its name, and the IPv6 loopback in the bracketed form a
#: browser sends.
LOCAL_TRUSTED_HOSTS: Final[tuple[str, ...]] = ("127.0.0.1", "localhost", "[::1]")

#: ``Host`` headers a local client genuinely sends, all of which must be
#: answered.  Werkzeug compares only the text before the first colon, so the
#: port-bearing spellings are admitted by the same three entries - which is
#: why a fixed port is not part of the contract.
ACCEPTED_HOST_HEADERS: Final[tuple[str, ...]] = (
    "localhost",
    "localhost:5000",
    "127.0.0.1",
    "127.0.0.1:5030",
    "[::1]",
    "[::1]:5000",
)

#: ``Host`` headers no local viewer is ever reached by, and every class of
#: them:
#:
#: * a foreign name, and a foreign name carrying a port, since the port is not
#:   what is being checked;
#: * a **foreign IPv6 literal**, which is the class Werkzeug's own comparison
#:   cannot refuse: it splits a host and every trusted entry at the first
#:   colon, so ``[::1]`` and ``[::2]`` both reduce to ``[`` and the loopback
#:   entry would admit every compressed literal - the global, the
#:   documentation-range and the IPv4-mapped ones alike.  ``app/__init__.py``
#:   closes that with a before-request check, and these are the cases that
#:   prove it closed;
#: * a **bracketed value that is not a host at all** - unterminated, with a
#:   non-numeric port, or carrying text after the closing bracket - which is
#:   refused rather than parsed leniently, because a value the check cannot
#:   judge is not a value it may trust;
#: * ``[0:0:0:0:0:0:0:1]``, which denotes the loopback and is still refused.
#:   The allowlist is exact by design: it names spellings, and one that
#:   expanded them would no longer be an allowlist.  The cost is one
#:   documented spelling; the alternative admits a class of address.
REJECTED_HOST_HEADERS: Final[tuple[str, ...]] = (
    "attacker.example",
    "evil.test:80",
    "[::2]",
    "[::2]:5000",
    "[2001:db8::1]:5000",
    "[fe80::1]",
    "[::ffff:127.0.0.1]",
    "[0:0:0:0:0:0:0:1]",
    "[::1",
    "[::1]:abc",
    "[::1]x",
)

#: The name an application built with an explicit ``TRUSTED_HOSTS`` override
#: answers for - a server deliberately reached under its own hostname, which
#: is the one supported way to widen the allowlist.
OVERRIDDEN_TRUSTED_HOST: Final[str] = "reports.example"

# --------------------------------------------------------------------------- #
# The factory module's own source, and what it may contain
#
# Criterion 10 has to be asserted over the *parsed* module rather than by
# substring search: ``app/__init__.py``'s docstring names every prohibited
# thing in order to prohibit it, so a grep for "selenium" or for
# "configuration.properties" matches the prose that bans them.
# --------------------------------------------------------------------------- #

#: The only imports ``app/__init__.py`` may execute at module level.  Anything
#: else there would run for every ``import app.<anything>``, including in a
#: worker process that never builds an application.
FACTORY_MODULE_LEVEL_IMPORTS: Final[frozenset[str]] = frozenset(
    {"__future__", "typing"}
)

#: The imports that must live inside ``create_app()``'s body: the framework,
#: the blueprint, the error handlers, the command and the logging setup.
FACTORY_DEFERRED_IMPORTS: Final[frozenset[str]] = frozenset(
    {
        "logging",
        "collections.abc",
        "flask",
        "app.cli",
        "app.errors",
        "app.logging_config",
        "app.web",
    }
)

#: Every top-level module name ``app/__init__.py`` may name in any import,
#: at module level or deferred.  A name outside this set is either the circular
#: import the deferral exists to avoid or a dependency the factory must not
#: acquire.
FACTORY_PERMITTED_IMPORT_ROOTS: Final[frozenset[str]] = frozenset(
    {"__future__", "typing", "logging", "collections", "flask", "app"}
)

#: Imports the factory must never carry, named individually so a failure says
#: which prohibition was broken.  ``app.pages``, ``app.automation`` and
#: ``app.reporting`` are the layers a read-only viewer has no business in;
#: ``werkzeug.middleware`` and ``gunicorn`` are the production-server surface
#: Conflict 3 excludes; ``flask_`` prefixes any Flask extension.
FACTORY_FORBIDDEN_IMPORTS: Final[tuple[str, ...]] = (
    "selenium",
    "behave",
    "app.pages",
    "app.automation",
    "app.reporting",
    "app.services",
    "werkzeug",
    "gunicorn",
    "flask_",
)

#: Registration calls the factory makes exactly once each.  A second call would
#: duplicate a blueprint, a handler or a command onto one application.
FACTORY_SINGLE_CALLS: Final[tuple[str, ...]] = (
    "register_blueprint",
    "register_error_handlers",
    "add_command",
)

# --------------------------------------------------------------------------- #
# Child-interpreter probes
#
# Three questions cannot be answered inside this session, because this session
# has already imported the package and has the checkout on its path:
#
#   * does importing ``app.utils.paths`` pull in Flask, behave or selenium?
#   * does importing ``app`` itself create anything or expose an application?
#   * does the *installed* distribution import, and does it carry its package
#     data?
#
# All three are therefore asked of a fresh interpreter.  Each child prints the
# offending module or path to stderr and exits non-zero, so a failure here
# names the defect instead of reporting "exit status 1".
# --------------------------------------------------------------------------- #

#: Upper bound on a child interpreter.  Generous - it only has to import a
#: handful of modules - and present so that a hung child fails the test rather
#: than the session.
CHILD_TIMEOUT_SECONDS: Final[float] = 120.0

#: The three distributions that must stay out of a worker's import graph:
#: the web framework, the Gherkin engine and the browser binding.
HEAVY_DEPENDENCIES: Final[tuple[str, ...]] = ("flask", "behave", "selenium")

_DEFERRED_IMPORT_PROBE: Final[str] = '''\
"""Import one module of the port and report which heavy dependencies came in.

argv[1] the module to import, argv[2] the checkout whose copy must answer,
argv[3:] the distributions that must be absent from sys.modules afterwards.
"""
import importlib
import sys
from pathlib import Path

module_name = sys.argv[1]
checkout_root = Path(sys.argv[2]).resolve()
forbidden = tuple(sys.argv[3:])

importlib.import_module(module_name)

# The measurement is only about this checkout if this checkout answered.
package_file = Path(sys.modules["app"].__file__).resolve()
if checkout_root not in package_file.parents:
    print(
        "app resolved to {0}, which is outside the checkout under test {1}".format(
            package_file, checkout_root
        ),
        file=sys.stderr,
    )
    raise SystemExit(2)

leaked = sorted(name for name in forbidden if name in sys.modules)
if leaked:
    print(
        "importing {0} pulled in {1}".format(module_name, ", ".join(leaked)),
        file=sys.stderr,
    )
    raise SystemExit(1)

print("{0} imported from {1} without {2}".format(
    module_name, package_file, ", ".join(forbidden)
))
'''

_INSTALLED_PACKAGE_PROBE: Final[str] = '''\
"""Import the installed distribution with the checkout off sys.path.

argv[1] is the checkout root, which must appear nowhere on this child's path:
if it did, the source tree rather than the installation would be answering.

An empty entry is the working directory - that is what "" means on sys.path,
and it is what an interpreter started without -P puts there first - so it is
resolved rather than skipped.  Comparison is by equality and not by
containment, because the virtual environment's site-packages lives *inside*
the checkout and is the installation this probe is meant to be using.
"""
import sys
from pathlib import Path

checkout_root = Path(sys.argv[1]).resolve()


def resolved_entries():
    for entry in sys.path:
        try:
            yield entry, Path(entry if entry else ".").resolve()
        except OSError:
            continue


on_path = [
    entry
    for entry, resolved in resolved_entries()
    if resolved == checkout_root
]
if on_path:
    print(
        "the checkout is on this child's sys.path: {0!r}".format(on_path),
        file=sys.stderr,
    )
    raise SystemExit(2)

import app
import app.utils.paths
import app.web

origin = Path(app.__file__).resolve()
if origin.name != "__init__.py" or origin.parent.name != "app":
    print(
        "app resolved to an unexpected location: {0}".format(origin),
        file=sys.stderr,
    )
    raise SystemExit(3)

templates_dir = app.utils.paths.templates_dir()
static_dir = app.utils.paths.static_dir()
missing = [
    str(candidate)
    for candidate in (
        templates_dir / "base.html",
        static_dir / "css" / "main.css",
    )
    if not candidate.is_file()
]
if missing:
    print(
        "package data missing from the installed distribution: {0}".format(
            ", ".join(missing)
        ),
        file=sys.stderr,
    )
    raise SystemExit(4)

print("app: {0}".format(origin))
print("templates: {0}".format(templates_dir))
print("static: {0}".format(static_dir))
'''


_IMPORT_SIDE_EFFECT_PROBE: Final[str] = '''\
"""Import the package in a fresh interpreter and report any side effect.

argv[1] is the checkout whose copy must answer.  The working directory is the
parent's empty scratch directory, which is where a build-output or instance
directory would appear, since every artifact path in the port is
working-directory-relative.
"""
import sys
from pathlib import Path

checkout_root = Path(sys.argv[1]).resolve()
working_directory = Path.cwd()
before = sorted(entry.name for entry in working_directory.iterdir())

import app

package_file = Path(app.__file__).resolve()
if checkout_root not in package_file.parents:
    print(
        "app resolved to {0}, which is outside the checkout under test {1}".format(
            package_file, checkout_root
        ),
        file=sys.stderr,
    )
    raise SystemExit(2)

after = sorted(entry.name for entry in working_directory.iterdir())
if before != after:
    print(
        "importing app created {0}".format(sorted(set(after) - set(before))),
        file=sys.stderr,
    )
    raise SystemExit(1)

if sorted(app.__all__) != ["create_app"]:
    print(
        "app publishes {0}, not the factory alone".format(sorted(app.__all__)),
        file=sys.stderr,
    )
    raise SystemExit(3)

# No application object at module level: building one at import time is exactly
# the side effect this package must not have, and it would defeat the isolation
# the unit suite depends on.  Detected without importing Flask - which is the
# point of the check below - by looking at type names only.
applications = [
    name
    for name in dir(app)
    if type(getattr(app, name)).__name__ in {"Flask", "Blueprint"}
]
if applications:
    print(
        "app exposes {0} at module level".format(applications),
        file=sys.stderr,
    )
    raise SystemExit(4)

if "flask" in sys.modules:
    print("importing app imported flask", file=sys.stderr)
    raise SystemExit(5)

print("import app: no side effect, surface {0}".format(sorted(app.__all__)))
'''


def _run_probe(
    source: str,
    *arguments: str,
    cwd: Path,
    isolated: bool,
) -> subprocess.CompletedProcess[str]:
    """Run ``source`` in a child interpreter and return the completed process.

    :param source: The probe program, passed with ``-c``.
    :param arguments: ``sys.argv[1:]`` for the child.
    :param cwd: The child's working directory.  A directory outside the
        checkout is what keeps the checkout off an isolated child's path,
        since ``-c`` would otherwise put the working directory there.
    :param isolated: When ``True`` the child runs under ``-P``, which drops the
        working-directory entry from ``sys.path`` - the "installed environment
        only" configuration.  When ``False`` the working directory answers, so
        the child measures *this* checkout's source.
    :returns: The completed process, with both streams captured as text.

    ``sys.executable`` is used rather than a bare ``python``, so the child is
    the same interpreter this session runs in - the pinned 3.14.6 virtual
    environment when pytest is invoked from it.  ``PYTHONPATH`` is removed from
    the child's environment for the same reason the working directory is
    controlled: an inherited path entry could answer for either source.
    """
    environment = {
        name: value
        for name, value in os.environ.items()
        if name != "PYTHONPATH"
    }
    command = [sys.executable]
    if isolated:
        command.append("-P")
    command.extend(["-c", source, *arguments])
    return subprocess.run(  # noqa: S603 - fixed argv, no shell, own interpreter
        command,
        cwd=str(cwd),
        env=environment,
        capture_output=True,
        text=True,
        timeout=CHILD_TIMEOUT_SECONDS,
        check=False,
    )


def _probe_report(completed: subprocess.CompletedProcess[str]) -> str:
    """Render a child's outcome for an assertion message."""
    return (
        f"exit status {completed.returncode}\n"
        f"--- child stdout ---\n{completed.stdout}"
        f"--- child stderr ---\n{completed.stderr}"
    )


# --------------------------------------------------------------------------- #
# Source-level helpers
#
# Used by the criterion-10 assertions and by the ``wsgi.py`` / ``run.py``
# entry-point checks.  All of them work on a parsed tree, never on the text.
# --------------------------------------------------------------------------- #


def _parse(path: Path) -> ast.Module:
    """Parse a tracked Python file, naming it in any syntax error."""
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imported_modules(node: ast.AST) -> set[str]:
    """Every module name imported anywhere under ``node``.

    ``import a.b`` contributes ``"a.b"`` and ``from a.b import c`` contributes
    ``"a.b"``, which is the granularity the prohibitions are written at - they
    name modules, not the names taken from them.
    """
    modules: set[str] = set()
    for candidate in ast.walk(node):
        if isinstance(candidate, ast.Import):
            modules.update(alias.name for alias in candidate.names)
        elif isinstance(candidate, ast.ImportFrom) and candidate.module:
            modules.add(candidate.module)
    return modules


def _type_checking_guards(tree: ast.Module) -> list[ast.If]:
    """The module-level ``if TYPE_CHECKING:`` blocks, which never execute."""
    return [
        statement
        for statement in tree.body
        if isinstance(statement, ast.If)
        and isinstance(statement.test, ast.Name)
        and statement.test.id == "TYPE_CHECKING"
    ]


def _executed_module_level_imports(tree: ast.Module) -> set[str]:
    """Module names imported at module level and actually executed.

    Imports inside an ``if TYPE_CHECKING:`` block are excluded: the guard is
    ``False`` at runtime, so a type checker sees those names and the
    interpreter never does.  That is the mechanism ``app/__init__.py`` uses to
    annotate its signature without importing Flask, and the exclusion is what
    keeps this helper measuring runtime cost rather than syntax.
    """
    return {
        module
        for statement in tree.body
        if isinstance(statement, (ast.Import, ast.ImportFrom))
        for module in _imported_modules(statement)
    }


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for statement in tree.body:
        if isinstance(statement, ast.FunctionDef) and statement.name == name:
            return statement
    message = f"{name}() is not defined at module level"
    raise AssertionError(message)


def _called_names(node: ast.AST) -> list[str]:
    """The name or attribute each call under ``node`` invokes."""
    names: list[str] = []
    for candidate in ast.walk(node):
        if not isinstance(candidate, ast.Call):
            continue
        function = candidate.func
        if isinstance(function, ast.Attribute):
            names.append(function.attr)
        elif isinstance(function, ast.Name):
            names.append(function.id)
    return names


def _literal_strings(tree: ast.Module) -> list[str]:
    """Every string constant that is *not* a docstring.

    The distinction is the whole point of criterion 10: the prohibitions are
    written in prose, so only the strings the code actually carries may be
    examined for a forbidden literal.
    """
    docstrings: set[int] = set()
    for candidate in ast.walk(tree):
        if not isinstance(
            candidate,
            (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
        ):
            continue
        body = getattr(candidate, "body", None)
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            docstrings.add(id(first.value))
    return [
        candidate.value
        for candidate in ast.walk(tree)
        if isinstance(candidate, ast.Constant)
        and isinstance(candidate.value, str)
        and id(candidate) not in docstrings
    ]


# --------------------------------------------------------------------------- #
# Logging helpers
#
# Two mechanics need stating once rather than in every logging test.
#
# ``configure_logging()`` sets ``propagate = False`` on the ``app`` logger, by
# design: a host that called ``basicConfig()`` must not print every record a
# second time, on the wrong stream.  pytest's ``caplog`` captures through a
# handler on the *root* logger, so with propagation off it would see nothing.
# :func:`package_records_captured` therefore attaches ``caplog``'s own handler
# to the ``app`` logger for the duration of a test, which is capture at the
# point records are emitted and independent of whether propagation is on.
#
# The handlers this module installs resolve ``sys.stdout``/``sys.stderr`` on
# every use rather than binding a stream object, which is exactly what makes
# ``capsys`` able to observe the split at all.
# --------------------------------------------------------------------------- #


@contextlib.contextmanager
def package_records_captured(
    caplog: pytest.LogCaptureFixture,
) -> Iterator[None]:
    """Capture ``app`` logger records in ``caplog`` regardless of propagation.

    :param caplog: pytest's log-capture fixture.
    :yields: ``None``; records emitted inside the block land in
        ``caplog.records``.
    """
    logger = logging.getLogger(PACKAGE_LOGGER_NAME)
    caplog.set_level(logging.DEBUG, logger=PACKAGE_LOGGER_NAME)
    logger.addHandler(caplog.handler)
    try:
        yield
    finally:
        logger.removeHandler(caplog.handler)


@pytest.fixture(autouse=True)
def restore_package_logger() -> Iterator[None]:
    """Return the ``app`` logger to its pre-test configuration.

    Every ``create_app()`` call configures logging, and this module calls the
    factory many times, so without this fixture the module's own tests would
    depend on each other's order and would leave the logger reconfigured for
    whatever runs next in the session.  Autouse, because a test that builds an
    application is configuring logging whether or not that is its subject.

    Handlers are detached but deliberately **not closed**: ``Handler.close()``
    removes a handler from ``logging``'s name registry, so closing a replaced
    handler would evict its same-named successor from that registry - the very
    ordering hazard ``app/logging_config.py`` documents in its idempotency
    guard.  A ``StreamHandler`` holds no resource of its own to release, so
    detaching is the whole of the cleanup required.

    :yields: ``None`` - the fixture is entirely about the state around a test.
    """
    logger = logging.getLogger(PACKAGE_LOGGER_NAME)
    handlers = list(logger.handlers)
    level = logger.level
    propagate = logger.propagate
    try:
        yield
    finally:
        logger.handlers[:] = handlers
        logger.setLevel(level)
        logger.propagate = propagate


# =========================================================================== #
# The factory: registration
# =========================================================================== #


def test_factory_returns_a_flask_application(flask_app: Flask) -> None:
    """Criterion 3: ``create_app()`` yields a usable ``Flask`` instance.

    Obtained through the shared fixture, which calls the factory and nothing
    else, so this also pins that the fixture's application is the production
    one rather than a locally assembled stand-in.
    """
    assert isinstance(flask_app, Flask)
    assert flask_app.import_name == "app"


def test_exactly_one_blueprint_is_registered_and_it_is_web_bp(
    flask_app: Flask,
) -> None:
    """AAP 0.3.3: one blueprint for the whole surface, and it is ``web_bp``.

    Identity rather than a name match, and against ``app.web.web_bp`` rather
    than anything reachable from ``app.web.routes``: the routes module imports
    the blueprint from its package, so a second ``Blueprint`` object appearing
    anywhere would be a second surface with the same name.
    """
    from app.web import bp, web_bp

    assert list(flask_app.blueprints) == [BLUEPRINT_NAME]
    assert flask_app.blueprints[BLUEPRINT_NAME] is web_bp
    # ``app/web`` publishes the conventional alias as well; it must be the very
    # same object, or registering either name would register two blueprints.
    assert bp is web_bp


def test_url_map_holds_the_six_rules_and_nothing_but_flask_static(
    flask_app: Flask,
) -> None:
    """Criterion 3, and the absence that goes with it.

    The six endpoints and their rule strings are pinned exactly, and every
    other rule is rejected except Flask's own ``static``.  The absence is the
    load-bearing half: an added route - a run trigger above all - has to fail
    here first, before any test of what that route does.
    """
    rules = {
        rule.endpoint: rule.rule
        for rule in flask_app.url_map.iter_rules()
        if rule.endpoint != FLASK_STATIC_ENDPOINT
    }

    assert rules == dict(EXPECTED_RULES)

    endpoints = {rule.endpoint for rule in flask_app.url_map.iter_rules()}
    assert endpoints == set(EXPECTED_RULES) | {FLASK_STATIC_ENDPOINT}


def test_no_endpoint_can_trigger_a_test_run(flask_app: Flask) -> None:
    """AAP 0.3.1: the surface is a viewer - no route starts, schedules or
    triggers a run.

    Two independent checks, because either alone could pass for the wrong
    reason: no rule accepts a writing method, and no endpoint or rule carries
    the vocabulary of execution.  The ``run-tests`` command is registered on
    the CLI, which is not reachable over HTTP; AAP 0.4.1 states plainly that
    nothing invokes it through the Flask CLI either.
    """
    for rule in flask_app.url_map.iter_rules():
        assert rule.methods is not None
        assert not rule.methods & set(WRITING_METHODS), (
            f"{rule.endpoint} accepts a writing method: {sorted(rule.methods)}"
        )

    execution_vocabulary = ("run", "execute", "start", "trigger", "launch")
    for rule in flask_app.url_map.iter_rules():
        target = f"{rule.endpoint} {rule.rule}".lower()
        for word in execution_vocabulary:
            assert word not in target, (
                f"{rule.endpoint} ({rule.rule}) reads as a run trigger"
            )


def test_every_route_is_get_only(flask_app: Flask) -> None:
    """AAP 0.3.1: ``GET`` everywhere, plus the ``HEAD``/``OPTIONS`` Flask adds.

    Asserted on the routing table rather than on the view functions, so a
    ``methods=`` argument appearing on any rule fails regardless of what the
    view would have done with the request.
    """
    for endpoint, rule_string in EXPECTED_RULES.items():
        rule = next(
            candidate
            for candidate in flask_app.url_map.iter_rules()
            if candidate.endpoint == endpoint
        )
        assert rule.rule == rule_string
        assert rule.methods == READ_ONLY_METHODS


@pytest.mark.parametrize("method", WRITING_METHODS)
def test_a_writing_request_is_rejected_by_routing(
    client: FlaskClient, method: str
) -> None:
    """Every route answers 405 to a writing method, on every rule.

    The complement of the previous test, through the client: method rejection
    happens in routing, before a view function runs and before any artifact is
    read, so a 405 here is proof that no writing request can reach a view.
    """
    for endpoint, url in CONCRETE_URLS.items():
        response = client.open(url, method=method)
        assert response.status_code == 405, (
            f"{method} {url} ({endpoint}) answered {response.status_code}"
        )


def test_the_converters_bind_the_documented_argument_types(
    flask_app: Flask,
) -> None:
    """The ``int``/``int``/``path`` converters, proved by matching URLs.

    Introspecting the rule strings shows the converters are *written*; binding
    the map and matching shows they are *in force* - that the two report
    indexes arrive as integers, that a non-numeric index does not match at all,
    and that the artifact name spans path separators, which is what lets
    ``cucumber/cucumber-html-reports/...`` be requested as one name.
    """
    from werkzeug.exceptions import NotFound

    adapter = flask_app.url_map.bind("localhost")

    assert adapter.match("/reports/features/3") == (
        "web.report_feature",
        {"findex": 3},
    )
    assert adapter.match("/reports/features/3/scenarios/7") == (
        "web.report_scenario",
        {"findex": 3, "sindex": 7},
    )
    assert adapter.match("/artifacts/cucumber/cucumber-html-reports/x.html") == (
        "web.artifact",
        {"name": "cucumber/cucumber-html-reports/x.html"},
    )

    with pytest.raises(NotFound):
        adapter.match("/reports/features/not-an-index")


def test_both_error_handlers_are_registered(flask_app: Flask) -> None:
    """Criterion 4: the 404 and 500 handlers from ``app/errors.py`` are on the
    application, registered application-wide rather than per blueprint.

    The handler objects are identified by their defining module and name, so
    the assertion fails if either handler is redefined locally - which is the
    shape a duplicated error surface would take.
    """
    from werkzeug.exceptions import InternalServerError, NotFound

    # ``None`` keys the handlers registered on the application itself; a
    # blueprint-scoped registration would appear under ``"web"`` and would not
    # cover an error raised outside the blueprint.
    application_handlers = flask_app.error_handler_spec[None]

    assert set(application_handlers) == {404, 500}
    assert set(application_handlers[404]) == {NotFound}
    assert set(application_handlers[500]) == {InternalServerError}

    registered = {
        404: application_handlers[404][NotFound],
        500: application_handlers[500][InternalServerError],
    }
    for status, handler in registered.items():
        assert handler.__module__ == "app.errors", (
            f"the {status} handler comes from {handler.__module__}"
        )
    assert registered[404].__qualname__ == "_handle_not_found"
    assert registered[500].__qualname__ == "_handle_internal_server_error"


def test_the_not_found_page_is_rendered_from_the_errors_template(
    flask_app: Flask,
) -> None:
    """Criterion 8, through the client: a 404 renders
    ``app/templates/errors/404.html``.

    Flask's ``template_rendered`` signal is what makes this a proof rather than
    an inference: it reports the template object, so the assertion is against
    the file on disk that was rendered, not against words that happen to appear
    in the response body.
    """
    rendered: list[Any] = []

    def record(_sender: Any, template: Any, **_extra: Any) -> None:
        rendered.append(template)

    template_rendered.connect(record, flask_app)
    try:
        with flask_app.test_client() as client:
            response = client.get("/no-such-page")
    finally:
        template_rendered.disconnect(record, flask_app)

    assert response.status_code == 404
    assert [template.name for template in rendered] == ["errors/404.html"]
    assert rendered[0].filename is not None
    assert Path(rendered[0].filename) == paths.templates_dir() / "errors" / "404.html"


def test_the_run_tests_command_is_registered_by_identity(
    flask_app: Flask,
) -> None:
    """Criterion 5: ``run-tests`` on the application's CLI group *is* the object
    ``app/cli.py`` defines.

    An identity check, not a name match: the point of the criterion is that the
    factory registers the command rather than redefining or wrapping it, which
    is what makes ``app/__init__.py`` the sole registration point for the
    command-line surface.  Registration is also not an invocation path - AAP
    0.4.1 states that nothing invokes the command through the Flask CLI - so
    this asserts the wiring and nothing about running it.
    """
    from app.cli import run_tests

    assert list(flask_app.cli.commands) == [COMMAND_NAME]
    assert flask_app.cli.commands[COMMAND_NAME] is run_tests
    assert run_tests.name == COMMAND_NAME


# =========================================================================== #
# The factory: isolation, overrides and argument rejection
# =========================================================================== #


def test_two_applications_share_no_state() -> None:
    """Criterion 6: applications built in one interpreter are independent.

    The factory holds no module-level state, so a value set on one application
    must be invisible to the next, and neither may accumulate a duplicate
    blueprint, handler or command.  This is the property the whole suite rests
    on, since every test that wants an application builds its own.
    """
    first = create_app({"TESTING": True})
    second = create_app({"TESTING": False})

    assert first is not second
    assert first.config["TESTING"] is True
    assert second.config["TESTING"] is False

    first.config["A_VALUE_ONLY_THE_FIRST_APPLICATION_HAS"] = "sentinel"
    assert "A_VALUE_ONLY_THE_FIRST_APPLICATION_HAS" not in second.config

    for application in (first, second):
        assert list(application.blueprints) == [BLUEPRINT_NAME]
        assert list(application.cli.commands) == [COMMAND_NAME]
        assert set(application.error_handler_spec[None]) == {404, 500}
        assert len(list(application.url_map.iter_rules())) == len(
            EXPECTED_RULES
        ) + 1


def test_overrides_absent_none_and_supplied_all_behave_as_documented() -> None:
    """Criterion 7, and both branches of criterion 11's coverage note.

    ``create_app()`` and ``create_app(None)`` are the same call - the parameter
    defaults to ``None`` and applies nothing - while a mapping is merged into
    Flask's config.  All three are asserted together because the documented
    contract is the relationship between them.
    """
    default = create_app()
    explicit_none = create_app(None)
    overridden = create_app({"TESTING": True, "SECRET_MARKER": "supplied"})

    assert isinstance(default, Flask)
    assert isinstance(explicit_none, Flask)
    assert default.config["TESTING"] is explicit_none.config["TESTING"]
    assert "SECRET_MARKER" not in default.config
    assert "SECRET_MARKER" not in explicit_none.config

    assert overridden.config["TESTING"] is True
    assert overridden.config["SECRET_MARKER"] == "supplied"


def test_overrides_are_applied_before_registration() -> None:
    """The documented ordering: overrides land before anything is registered.

    A mapping that configures Jinja is the observable case - Flask reads
    ``TEMPLATES_AUTO_RELOAD`` when it builds the Jinja environment - so a
    factory that merged overrides after registration would hand back an
    application whose environment was built from the defaults.
    """
    application = create_app({"TESTING": True, "TEMPLATES_AUTO_RELOAD": True})

    assert application.config["TEMPLATES_AUTO_RELOAD"] is True
    assert application.jinja_env.auto_reload is True


@pytest.mark.parametrize(
    "argument",
    [
        ["TESTING", True],
        ("TESTING", True),
        "TESTING=1",
        42,
        object(),
    ],
)
def test_a_non_mapping_argument_is_rejected_before_anything_is_built(
    monkeypatch: pytest.MonkeyPatch, argument: object
) -> None:
    """The documented ``TypeError``, raised before construction begins.

    "Before anything is constructed" is asserted rather than assumed: Flask's
    own class is replaced with a recorder for the duration, and the recorder
    must never be called.  Replacing it works *because* the factory imports
    ``Flask`` inside its body - which makes this test a second, behavioural
    proof of the deferred import as well.
    """
    import flask

    constructions: list[tuple[Any, ...]] = []

    class _RecordingFlask:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            constructions.append((args, kwargs))

    monkeypatch.setattr(flask, "Flask", _RecordingFlask)

    with pytest.raises(TypeError) as raised:
        create_app(argument)  # type: ignore[arg-type]

    assert type(argument).__name__ in str(raised.value)
    assert constructions == []


# =========================================================================== #
# The factory: Host-header validation
#
# Criterion 12.  Flask accepts every ``Host`` header unless it is told which
# ones to trust, and this viewer has no authentication behind its routes, so
# the allowlist the factory installs is the whole of what keeps a foreign name
# away from the artifacts - which carry the suite's configured credentials, its
# failure screenshots and its tracebacks (CWE-346, Host header / DNS
# rebinding).  Every test below drives ``GET /``, the one route AAP 0.3.1 makes
# "200 always, including before any run", so an outcome here is about the host
# and never about whether a run has happened.
# =========================================================================== #


def test_a_factory_built_application_trusts_only_the_local_names(
    flask_app: Flask,
) -> None:
    """Criterion 12: the local allowlist is the factory's default.

    Asserted as the exact list, not as a membership test: an entry beyond these
    three would admit a name the security model does not cover, and an absent
    one would refuse a spelling a local browser really sends.
    """
    assert flask_app.config["TRUSTED_HOSTS"] == list(LOCAL_TRUSTED_HOSTS)


@pytest.mark.parametrize("host", ACCEPTED_HOST_HEADERS)
def test_every_local_host_spelling_is_answered(
    client: FlaskClient, host: str
) -> None:
    """Criterion 12: each way of addressing a local viewer still works.

    The allowlist is only correct if it is invisible in normal use, so all five
    spellings a local client sends - the bare name, the bare address, either
    with a port, and the bracketed IPv6 loopback with one - are exercised
    through the client rather than reasoned about from the three entries.
    """
    response = client.get("/", headers={"Host": host})

    assert response.status_code == 200
    assert response.mimetype == "text/html"


@pytest.mark.parametrize("host", REJECTED_HOST_HEADERS)
def test_a_foreign_host_is_refused_before_any_view_runs(
    flask_app: Flask, host: str
) -> None:
    """Criterion 12: a foreign ``Host`` is 400, and nothing is rendered.

    The status is asserted both ways round - 400, and *not* 200 - because the
    defect this closes was a 200: with the allowlist unset, ``Host:
    attacker.example`` was served the index like any other request.  "Before
    any view runs" is proved with Flask's ``template_rendered`` signal rather
    than inferred from the status: no template renders at all, so the index
    view was never entered and no artifact was read on behalf of the rejected
    name.
    """
    rendered: list[Any] = []

    def record(_sender: Any, template: Any, **_extra: Any) -> None:
        rendered.append(template)

    template_rendered.connect(record, flask_app)
    try:
        with flask_app.test_client() as client:
            response = client.get("/", headers={"Host": host})
    finally:
        template_rendered.disconnect(record, flask_app)

    assert response.status_code == 400, (
        f"Host: {host} was answered {response.status_code} rather than refused"
    )
    assert rendered == []


def test_an_explicit_override_replaces_the_local_allowlist() -> None:
    """Criterion 12: ``TRUSTED_HOSTS`` in the overrides wins outright.

    The factory sets the local allowlist before it merges the overrides, so a
    deployment reached under its own hostname states that in one place - and it
    replaces the list rather than extending it, which is asserted from both
    ends: the supplied name is answered and the local name it displaced is
    refused.  A merge would be the worse contract, because it would leave a
    revised security model still trusting names nobody reviewed.
    """
    application = create_app(
        {"TESTING": True, "TRUSTED_HOSTS": [OVERRIDDEN_TRUSTED_HOST]}
    )

    assert application.config["TRUSTED_HOSTS"] == [OVERRIDDEN_TRUSTED_HOST]

    with application.test_client() as client:
        accepted = client.get("/", headers={"Host": OVERRIDDEN_TRUSTED_HOST})
        displaced = client.get("/", headers={"Host": "localhost"})

    assert accepted.status_code == 200
    assert displaced.status_code == 400


def test_an_overridden_ipv6_entry_is_enforced_exactly_too() -> None:
    """Criterion 12: the exact check reads the live allowlist, not the default.

    A deployment served over IPv6 names its own literal, and the before-request
    check must then trust that literal and refuse every other - including the
    loopback the default names, which the override displaced.  Asserted from
    both ends for the same reason the previous test is: a check that fell back
    to the module default would trust ``[::1]`` here, and one that only ever
    compared against the default would refuse the supplied name.
    """
    served_as = "[fe80::1]"
    application = create_app({"TESTING": True, "TRUSTED_HOSTS": [served_as]})

    with application.test_client() as client:
        supplied = client.get("/", headers={"Host": f"{served_as}:9000"})
        displaced = client.get("/", headers={"Host": "[::1]:9000"})

    assert supplied.status_code == 200
    assert displaced.status_code == 400


def test_a_malformed_allowlist_entry_widens_nothing() -> None:
    """Criterion 12: an entry the check cannot read is not an entry it trusts.

    An operator's ``TRUSTED_HOSTS`` is hand-written configuration, so it can
    carry a value that is not a host - here a name with a junk port.  Such an
    entry must be ignored rather than treated as a wildcard, and the one
    well-formed entry beside it must still work: the failure mode this rules
    out is a malformed list that admits every IPv6 literal because nothing in
    it could be parsed to compare against.

    The host part of the entry is asserted too, and it is not incidental:
    Werkzeug splits an entry at its first colon, so ``localhost:not-a-port``
    still trusts the *name* ``localhost`` at that layer while contributing
    nothing to the exact IPv6 comparison - two layers reading one malformed
    entry differently, and neither of them widening it.
    """
    application = create_app(
        {"TESTING": True, "TRUSTED_HOSTS": ["localhost:not-a-port", "[::1]"]}
    )

    with application.test_client() as client:
        trusted = client.get("/", headers={"Host": "[::1]:5000"})
        untrusted = client.get("/", headers={"Host": "[::2]:5000"})
        by_name = client.get("/", headers={"Host": "localhost"})

    assert trusted.status_code == 200
    assert untrusted.status_code == 400
    assert by_name.status_code == 200


def test_a_host_value_that_is_not_text_is_not_a_trusted_host() -> None:
    """Criterion 12: the parse is total, and answers "not a host" for a scalar.

    Asserted against the helper directly because the WSGI path cannot deliver
    a non-string: an environment value is always text, and a non-string
    *allowlist* entry is refused inside Werkzeug's own comparison - with an
    ``AttributeError`` - before any hook of ours is reached, so the guard
    cannot be provoked through a request.  It stays because the helper's
    contract is a total coercion, the pattern every reader of this port's
    documents follows, and a caller handing it a scalar must get "untrusted"
    rather than an exception out of a security check.
    """
    from app import _host_without_port

    for value in (None, 1234, ["[::1]"], {"host": "[::1]"}, b"[::1]"):
        assert _host_without_port(value) == "", value

    # And the two shapes it does read, so the assertion above is a statement
    # about non-text rather than about everything.
    assert _host_without_port("[::1]:5000") == "[::1]"
    assert _host_without_port("LOCALHOST:5000") == "localhost"


def test_an_allowlist_naming_nothing_restricts_nothing() -> None:
    """Criterion 12: ``None`` is the framework's "every name", and stays so.

    A deployment that deliberately turns host validation off sets the key to
    ``None``, which is Flask's own documented default and means every name is
    accepted.  The exact IPv6 check must add no restriction of its own to that
    decision: a hook that refused a bracketed literal whenever the allowlist
    named no bracketed entry would silently break IPv6 for an application
    whose operator had chosen to validate nothing.
    """
    application = create_app({"TESTING": True, "TRUSTED_HOSTS": None})

    with application.test_client() as client:
        literal = client.get("/", headers={"Host": "[::2]:1"})
        foreign = client.get("/", headers={"Host": "anything.example"})

    assert literal.status_code == 200
    assert foreign.status_code == 200


def test_one_applications_allowlist_cannot_widen_the_next_ones() -> None:
    """Criterion 6 applied to criterion 12: the default is not shared state.

    The factory hands each application its own list, so code holding one
    application's config - a test, a shell session, an extension - cannot
    append a name to it and have the next application built in that
    interpreter trust it.  Asserted behaviourally as well as by identity: the
    second application refuses the name the first was widened with.
    """
    widened = create_app({"TESTING": True})
    allowlist = widened.config["TRUSTED_HOSTS"]
    assert isinstance(allowlist, list)
    allowlist.append(REJECTED_HOST_HEADERS[0])

    later = create_app({"TESTING": True})

    assert later.config["TRUSTED_HOSTS"] == list(LOCAL_TRUSTED_HOSTS)
    assert later.config["TRUSTED_HOSTS"] is not allowlist

    with later.test_client() as client:
        response = client.get("/", headers={"Host": REJECTED_HOST_HEADERS[0]})

    assert response.status_code == 400


# =========================================================================== #
# The factory: no side effects
# =========================================================================== #


def test_building_an_application_writes_nothing_and_reads_no_properties(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Criterion 2: the factory creates no directory, no file, and reads no
    configuration file.

    The working directory is moved to an empty ``tmp_path`` first, because
    every artifact path in the port is working-directory-relative
    (``app/utils/paths.py``), so that directory is where a build-output or
    instance directory would appear.  The properties read is instrumented
    rather than inferred: ``load_properties`` is replaced with a counter, and
    the absence of the missing-file warning is asserted as well, so neither a
    successful read nor a tolerated failure can slip past.
    """
    monkeypatch.chdir(tmp_path)

    loads: list[object] = []

    def _counting_load(*args: object, **kwargs: object) -> dict[str, str]:
        loads.append((args, kwargs))
        return {}

    monkeypatch.setattr(properties, "load_properties", _counting_load)

    with package_records_captured(caplog):
        application = create_app({"TESTING": True})

    assert isinstance(application, Flask)
    assert loads == []
    assert properties.MISSING_FILE_MESSAGE not in caplog.text
    assert sorted(entry.name for entry in tmp_path.iterdir()) == []
    assert not paths.target_root().exists()
    assert not (tmp_path / "instance").exists()


def test_no_configuration_key_or_value_reaches_flask_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The name collision ``app/__init__.py`` warns about, asserted.

    ``app.config`` is the port's six-key configuration module and
    ``flask_app.config`` is Flask's mapping; they are unrelated, and none of
    the six keys - browser, the two URLs, the credentials, the expected page
    title - is ever copied into Flask's config.  A populated properties file is
    in place while the application is built, so the assertion is about the
    factory declining to read it rather than about there being nothing to read.
    """
    from app import config as port_config

    values = {
        "browser": "chrome",
        "web.table.url": "https://example.invalid/login-sentinel",
        "url": "https://example.invalid/employee-sentinel",
        "username": "factory-user-sentinel",
        "password": "factory-password-sentinel",
        "EmplTitle": "Employees-title-sentinel",
    }
    properties_file = tmp_path / "configuration.properties"
    properties_file.write_text(
        "".join(f"{key}={value}\n" for key, value in values.items()),
        encoding="latin-1",
    )
    # Owner-only, because the reader refuses a credential-bearing file that is
    # readable beyond its owner and would then report every key as ``None`` -
    # which would make the assertions below pass for the wrong reason.
    os.chmod(properties_file, 0o600)
    monkeypatch.chdir(tmp_path)

    application = create_app({"TESTING": True})

    flask_config_keys = {str(key) for key in application.config}
    for key in port_config.CONFIG_KEYS:
        assert key not in flask_config_keys
        assert key.upper() not in flask_config_keys
        assert key.upper().replace(".", "_") not in flask_config_keys

    rendered_values = {str(value) for value in application.config.values()}
    for value in values.values():
        assert value not in rendered_values

    # The file really was readable, so the absence above is a decision rather
    # than an accident of there being no configuration present.
    assert port_config.get_username() == values["username"]


def test_the_index_answers_200_with_no_build_output_at_all(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Criterion 9: ``GET /`` is 200 before any run has happened.

    AAP 0.3.1 makes the index the one route the data-availability rule does not
    govern - "200 always, including before any run" - and the artifact
    directory is absent in a fresh checkout because version control ignores it.
    The working directory is an empty ``tmp_path``, so there is genuinely
    nothing to describe, and the request must still be answered and must still
    write nothing.
    """
    monkeypatch.chdir(tmp_path)

    assert not paths.target_root().exists()

    response = client.get("/")

    assert response.status_code == 200
    assert response.mimetype == "text/html"
    assert sorted(entry.name for entry in tmp_path.iterdir()) == []


# =========================================================================== #
# The factory: template and static resolution
# =========================================================================== #


def test_every_view_template_resolves_from_the_package(
    flask_app: Flask,
) -> None:
    """Criterion 8: templates come from ``app/templates`` with no override.

    ``Flask(__name__)`` resolves the root path from the package, so the default
    ``template_folder`` is the correct one and is deliberately not overridden -
    that is what keeps an *installed* copy working, the tree travelling as
    package data.  Each template is loaded and its resolved filename checked
    against ``app/utils/paths.py``, the port's sole owner of that location.
    """
    assert flask_app.template_folder == "templates"
    assert Path(flask_app.root_path) == paths.package_root()

    with flask_app.app_context():
        for name in VIEW_TEMPLATES:
            template = flask_app.jinja_env.get_template(name)
            assert template.filename is not None
            assert Path(template.filename) == paths.templates_dir() / name


def test_static_assets_resolve_from_the_package(client: FlaskClient) -> None:
    """Criterion 8's other half: ``app/static`` is served at its default path.

    Both assets are requested through the client rather than merely located on
    disk, because the contract the base template depends on is that the
    ``static`` endpoint answers for them.
    """
    assert client.application.static_url_path == "/static"
    assert client.application.static_folder is not None
    assert Path(client.application.static_folder) == paths.static_dir()

    for asset in STATIC_ASSETS:
        response = client.get(f"/static/{asset}")
        assert response.status_code == 200, f"/static/{asset} is unreachable"
        assert response.get_data(), f"/static/{asset} is empty"


# =========================================================================== #
# The factory: the deferred-import proof
#
# Criterion 1, and the assertion ``app/__init__.py`` calls the most important
# one in this module's suite.  Every probe runs in a fresh interpreter, and
# every probe names the offending module on failure.
# =========================================================================== #


def test_paths_imports_without_flask_behave_or_selenium(
    repo_root: Path,
) -> None:
    """Criterion 1: ``import app.utils.paths`` pulls in none of the three.

    This is the invariant AAP 0.4.2 states - "app/utils imports nothing from
    the package, so paths.py is importable in a worker that never builds a
    Flask application" - and importing ``app.utils.paths`` runs
    ``app/__init__.py`` in full first, so a single module-level Flask import
    there would break it.  Measured in a child interpreter whose working
    directory is the checkout, so the copy under test is the one that answers.
    """
    completed = _run_probe(
        _DEFERRED_IMPORT_PROBE,
        "app.utils.paths",
        str(repo_root),
        *HEAVY_DEPENDENCIES,
        cwd=repo_root,
        isolated=False,
    )

    assert completed.returncode == 0, _probe_report(completed)
    assert "app.utils.paths imported from" in completed.stdout


def test_importing_the_package_has_no_side_effects(
    repo_root: Path, tmp_path: Path
) -> None:
    """Criterion 2's import-time half: ``import app`` does nothing observable.

    The factory's own no-side-effect test covers *building* an application;
    this covers merely importing the package, which is what every worker
    process does and what runs before any of them can decide not to build one.
    A fresh interpreter is required because this session imported ``app`` long
    ago, so nothing it could observe now would be attributable to the import.

    The child works in an empty scratch directory - the location a build-output
    or instance directory would appear in, artifact paths being
    working-directory-relative - and checks four things the criterion names:
    nothing is created, the published surface is the factory alone, no
    application or blueprint object exists at module level, and Flask was not
    imported.  The parent then confirms the directory is still empty, so the
    absence is established on both sides.
    """
    completed = _run_probe(
        _IMPORT_SIDE_EFFECT_PROBE,
        str(repo_root),
        cwd=tmp_path,
        isolated=False,
    )

    assert completed.returncode == 0, _probe_report(completed)
    assert "no side effect" in completed.stdout
    assert sorted(entry.name for entry in tmp_path.iterdir()) == []
    assert not (tmp_path / "target").exists()


@pytest.mark.parametrize(
    ("module_name", "forbidden"),
    [
        ("app", HEAVY_DEPENDENCIES),
        ("app.utils.properties", HEAVY_DEPENDENCIES),
        ("app.config", HEAVY_DEPENDENCIES),
        ("app.logging_config", HEAVY_DEPENDENCIES),
        # The reporting and service layers legitimately import the Gherkin
        # engine - ``events.py`` registers a formatter on its event stream and
        # the service drives a run - so behave is expected there.  Flask is
        # not: nothing outside ``app/web`` and the factory may need it.
        ("app.reporting.events", ("flask",)),
        ("app.services", ("flask",)),
    ],
)
def test_the_package_imports_without_the_framework_it_does_not_need(
    repo_root: Path, module_name: str, forbidden: tuple[str, ...]
) -> None:
    """The deferred-import proof extended across the package initialiser.

    ``import app`` itself, the configuration pair and the logging module must
    all stay free of every heavy dependency, and the reporting and service
    layers must stay free of Flask.  Each case runs in its own interpreter,
    because the answer to "what did this import pull in" is only meaningful in
    a process that has imported nothing else.
    """
    completed = _run_probe(
        _DEFERRED_IMPORT_PROBE,
        module_name,
        str(repo_root),
        *forbidden,
        cwd=repo_root,
        isolated=False,
    )

    assert completed.returncode == 0, _probe_report(completed)
    assert f"{module_name} imported from" in completed.stdout


# =========================================================================== #
# The factory module's source: criterion 10
# =========================================================================== #


def test_the_factory_executes_only_typing_imports_at_module_level(
    repo_root: Path,
) -> None:
    """Criterion 10: at module level the factory imports only ``__future__``
    and ``typing``.

    This is the structural form of the deferred-import proof, and it fails
    faster and more specifically than the child-interpreter probes: a
    module-level ``import flask`` is named here as a violation before it is
    observed there as a leaked module.  The ``if TYPE_CHECKING:`` block is
    excluded deliberately - the guard is ``False`` at runtime, so the names it
    imports cost an importer nothing - and its presence is asserted, since it
    is how the signature's annotations resolve for a type checker.
    """
    tree = _parse(repo_root / "app" / "__init__.py")

    assert _executed_module_level_imports(tree) == FACTORY_MODULE_LEVEL_IMPORTS

    guards = _type_checking_guards(tree)
    assert len(guards) == 1
    assert _imported_modules(guards[0]) == {"collections.abc", "flask"}


def test_the_factory_defers_every_wiring_import_into_create_app(
    repo_root: Path,
) -> None:
    """Criterion 10: the framework, blueprint, CLI, error-handler and logging
    imports all live inside ``create_app()``'s body.

    Not merely "absent from module level": each one is located inside the
    function, so moving an import into a helper or a class body - where it
    would run at import time again - does not satisfy this test.
    """
    tree = _parse(repo_root / "app" / "__init__.py")
    factory = _function(tree, "create_app")

    assert _imported_modules(factory) == FACTORY_DEFERRED_IMPORTS


def test_the_factory_registers_each_thing_exactly_once(
    repo_root: Path,
) -> None:
    """Criterion 10: one ``register_blueprint``, one handler registration, one
    ``add_command``, and no route decorator anywhere.

    A second registration call is the shape a duplicated surface takes, and a
    route decorator here would put a view outside ``app/web/routes.py``, which
    owns all six.  Counted over the parsed module, so a call inside a comment
    or a docstring cannot contribute and a real one cannot hide.
    """
    tree = _parse(repo_root / "app" / "__init__.py")
    calls = _called_names(tree)

    for name in FACTORY_SINGLE_CALLS:
        assert calls.count(name) == 1, (
            f"{name} is called {calls.count(name)} times"
        )

    assert calls.count("Flask") == 1

    decorators = [
        decorator
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        for decorator in node.decorator_list
    ]
    assert decorators == []

    attributes = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    assert "route" not in attributes
    assert "add_url_rule" not in attributes


def test_the_factory_carries_no_forbidden_import_or_literal(
    repo_root: Path,
) -> None:
    """Criterion 10's remaining prohibitions, over the parsed module.

    No browser or Gherkin dependency, no page/automation/reporting/service
    import, no production-server surface, no Flask extension - and no path
    literal or ``configuration.properties`` reference, because
    ``app/utils/paths.py`` owns every path in the port and
    ``app/utils/properties.py`` is the only reader of that file.  Docstrings
    are excluded from the literal check for the reason the criterion states:
    the module's own prose names each prohibited thing in order to prohibit it,
    so a substring search matches the documentation rather than the code.
    """
    tree = _parse(repo_root / "app" / "__init__.py")
    imported = _imported_modules(tree)

    roots = {module.split(".")[0] for module in imported}
    assert roots <= FACTORY_PERMITTED_IMPORT_ROOTS, (
        f"unexpected import roots: {sorted(roots - FACTORY_PERMITTED_IMPORT_ROOTS)}"
    )
    for forbidden in FACTORY_FORBIDDEN_IMPORTS:
        assert not any(
            module == forbidden or module.startswith(forbidden)
            for module in imported
        ), f"the factory imports {forbidden}"

    for literal in _literal_strings(tree):
        assert "configuration.properties" not in literal
        assert ".properties" not in literal
        assert "target" not in literal.lower()
        assert "/" not in literal
        assert "\\" not in literal


# =========================================================================== #
# The entry points: wsgi.py and run.py
#
# Both are checked by importing them and by reading their parsed source.  No
# test starts a server: that is the one thing ``run.py``'s ``__main__`` guard
# exists to prevent, so provoking it would defeat the purpose.
# =========================================================================== #


def test_wsgi_exposes_a_module_level_application_and_serves_nothing(
    repo_root: Path,
) -> None:
    """``wsgi.py`` publishes one application under both conventional names and
    never calls ``run()``.

    A WSGI server loads ``wsgi:app``, so the name has to exist at module level
    and has to be an application the factory built.  ``application`` is the
    other conventional spelling and must be the *same* object - a second
    factory call would give a server two applications and one of them no
    traffic.  The absence of ``run()`` is what separates this file from
    ``run.py``: importing it must never start a server.
    """
    import wsgi

    assert isinstance(wsgi.app, Flask)
    assert wsgi.application is wsgi.app
    assert list(wsgi.app.blueprints) == [BLUEPRINT_NAME]

    tree = _parse(repo_root / "wsgi.py")
    calls = _called_names(tree)
    assert calls.count("create_app") == 1
    assert "run" not in calls
    assert not [
        statement for statement in tree.body if isinstance(statement, ast.If)
    ]


def test_run_py_guards_the_development_server_behind_main(
    repo_root: Path,
) -> None:
    """``run.py`` builds its application from the factory and runs the server
    only under ``if __name__ == "__main__"``.

    Importing the module is therefore safe - which this test demonstrates by
    importing it - and the guard is verified structurally: every ``run()`` call
    in the file lies inside the guard's line range, so no importer, test or
    documentation tool can start a server by accident.
    """
    import run

    assert isinstance(run.app, Flask)
    assert list(run.app.blueprints) == [BLUEPRINT_NAME]

    tree = _parse(repo_root / "run.py")
    assert _called_names(tree).count("create_app") == 1

    guards = [
        statement
        for statement in tree.body
        if isinstance(statement, ast.If)
        and ast.unparse(statement.test).replace('"', "'")
        == "__name__ == '__main__'"
    ]
    assert len(guards) == 1
    guard = guards[0]

    server_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "run"
    ]
    assert server_calls, "run.py never starts the development server"
    for call in server_calls:
        assert guard.lineno < call.lineno <= (guard.end_lineno or call.lineno), (
            f"a run() call at line {call.lineno} sits outside the __main__ guard"
        )


# =========================================================================== #
# The packaging, proved from a clean interpreter
# =========================================================================== #


def test_the_project_is_importable_from_the_installed_environment(
    project_importable_from_environment: bool,
) -> None:
    """``import app`` resolves from the environment, not from this checkout.

    ``tests/conftest.py`` measures this before it touches ``sys.path`` and
    publishes the answer, precisely so that the difference is assertable.
    ``True`` is the mandated state - the pinned 3.14.6 environment with
    ``pip install -e .`` in place, which both runner scripts and the
    ``Makefile`` establish - and ``False`` means every ``import app`` in this
    session is reading the source tree because the installation could not
    answer.  Failing here is the point: a broken editable install or a broken
    ``[tool.setuptools.packages.find]`` mapping must not be papered over by a
    source-tree import.
    """
    assert project_importable_from_environment is True, (
        "app does not resolve from the installed environment; run "
        "`pip install -e .` into the pinned 3.14.6 virtual environment "
        "(scripts/run_tests.sh does this) before running the suite"
    )


def test_the_installed_distribution_imports_and_carries_its_package_data(
    repo_root: Path, tmp_path: Path
) -> None:
    """The installed distribution imports with the checkout off the path, and
    its templates and static assets travel with it.

    The child runs under ``-P`` from a working directory outside the checkout
    and with ``PYTHONPATH`` dropped, so no entry can let the source tree answer
    for the installation; the child asserts that itself before importing
    anything.  ``templates_dir()`` and ``static_dir()`` are then resolved
    *inside* that interpreter and their contents checked, which is what proves
    ``[tool.setuptools.package-data]`` actually ships the two trees the report
    writers and the viewer render from.
    """
    completed = _run_probe(
        _INSTALLED_PACKAGE_PROBE,
        str(repo_root),
        cwd=tmp_path,
        isolated=True,
    )

    assert completed.returncode == 0, _probe_report(completed)
    assert "templates:" in completed.stdout
    assert "static:" in completed.stdout


# =========================================================================== #
# The console-logging contract, factory half
#
# AAP 0.4.1: "Both streams are line-buffered: progress to stdout, engine
# diagnostics to stderr".  ``create_app()`` is one of the two callers of
# ``configure_logging()``, so what is asserted here is the partition itself and
# the properties the factory's repeated use depends on.  The command-line half
# - the option table, the exit rows and the CLI's own stream routing - belongs
# to ``tests/test_cli.py``.
#
# Records are emitted on a child of the ``app`` logger rather than on ``app``
# itself, because that is how every module in the port logs:
# ``logging.getLogger(__name__)`` with no handler of its own.
# =========================================================================== #

#: The logger a test emits on: a child of the configured package logger, named
#: so a stray line in captured output is attributable to this module.
PROBE_LOGGER_NAME: Final[str] = f"{PACKAGE_LOGGER_NAME}.test_app_factory_probe"

#: The two handlers ``configure_logging()`` owns, by the names it sets on them.
MANAGED_HANDLER_NAMES: Final[frozenset[str]] = frozenset(
    {STDOUT_HANDLER_NAME, STDERR_HANDLER_NAME}
)


def _handler_names(logger: logging.Logger) -> list[str]:
    """The names of a logger's handlers, in attachment order.

    The test harness's own handlers are excluded, and the exclusion is a
    correctness requirement rather than a convenience.  pytest's log capture
    attaches its handler to **every non-propagating logger** for the duration
    of a test phase, and ``configure_logging()`` makes the ``app`` logger
    non-propagating by design - so as soon as any test in the session has built
    an application, the ``app`` logger carries one or two pytest handlers that
    no production process ever sees.  Asserting over the raw list would make
    every handler-set assertion below depend on which module ran first, while
    saying nothing about the contract, which is that ``configure_logging()``
    installs exactly the handlers it owns and leaves every other handler in
    place.  Handlers defined by a test - the foreign handler below - are *not*
    excluded, since those are part of what is being asserted.

    :param logger: The logger to inspect.
    :returns: One name per handler the port or a test is accountable for, in
        attachment order; an unnamed handler contributes an empty string, so a
        genuinely nameless leak still shows up.
    """
    return [
        handler.get_name() or ""
        for handler in logger.handlers
        if not type(handler).__module__.startswith("_pytest")
    ]


class _StreamWithoutReconfigure(io.StringIO):
    """A text stream carrying no ``reconfigure``, like ``capsys``'s own.

    ``io.StringIO`` already has no ``reconfigure`` attribute, so this subclass
    adds nothing but a name that says what it stands for in a failure message.
    """


class _StreamWhoseReconfigureRaises(io.StringIO):
    """A text stream whose ``reconfigure`` fails the way a detached one does.

    The realistic causes are a closed or detached stream (``ValueError``) and a
    stream whose file descriptor is gone (``OSError``); both are outcomes
    ``configure_logging()`` has to absorb, because line buffering is an
    optimisation for pipe capture and never a correctness requirement.
    """

    def __init__(self, error: BaseException) -> None:
        super().__init__()
        self._error = error
        self.reconfigure_calls = 0

    def reconfigure(self, **_kwargs: object) -> None:
        """Record the attempt and fail."""
        self.reconfigure_calls += 1
        raise self._error


def test_info_goes_to_stdout_and_warnings_to_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The stream split of AAP 0.4.1, and the partition that makes it exact.

    ``INFO`` and below on stdout, ``WARNING`` and above on stderr, and **no
    record on both** - the stdout handler's maximum-level filter is what turns
    two overlapping handlers into a partition, so a warning appearing on stdout
    as well would double every diagnostic line in a CI log.

    ``capsys`` can observe this only because the handlers resolve
    ``sys.stdout``/``sys.stderr`` on every use instead of binding the object
    that was current when the factory ran; that is the property this test
    exercises incidentally and the reason no fixture has to configure logging
    before capture starts.
    """
    create_app({"TESTING": True})
    logger = logging.getLogger(PROBE_LOGGER_NAME)

    logger.info("progress-sentinel")
    logger.warning("warning-sentinel")
    logger.error("error-sentinel")

    captured = capsys.readouterr()

    assert "INFO" in captured.out
    assert "progress-sentinel" in captured.out
    assert "warning-sentinel" not in captured.out
    assert "error-sentinel" not in captured.out

    assert "WARNING" in captured.err
    assert "warning-sentinel" in captured.err
    assert "error-sentinel" in captured.err
    assert "progress-sentinel" not in captured.err

    # The format is the module's own, so a reader can tell which module spoke.
    assert f"INFO {PROBE_LOGGER_NAME}: progress-sentinel" in captured.out
    assert f"WARNING {PROBE_LOGGER_NAME}: warning-sentinel" in captured.err


def test_repeated_configuration_emits_each_record_exactly_once(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Non-duplication: several applications and several explicit calls leave
    exactly the two managed handlers, and one line per record per stream.

    The unit suite builds an application per test, so a factory that stacked
    handlers would multiply every line by the number of applications built.
    Occurrences are counted rather than merely found present, because presence
    is exactly what a duplicated handler also satisfies.
    """
    create_app({"TESTING": True})
    create_app({"TESTING": True})
    create_app({"TESTING": True})
    configure_logging()
    configure_logging()

    logger = logging.getLogger(PACKAGE_LOGGER_NAME)
    assert sorted(_handler_names(logger)) == sorted(MANAGED_HANDLER_NAMES)

    logging.getLogger(PROBE_LOGGER_NAME).info("once-on-stdout")
    logging.getLogger(PROBE_LOGGER_NAME).warning("once-on-stderr")

    captured = capsys.readouterr()

    assert captured.out.count("once-on-stdout") == 1
    assert captured.err.count("once-on-stderr") == 1
    assert captured.out.count("once-on-stderr") == 0
    assert captured.err.count("once-on-stdout") == 0


def test_configuration_leaves_foreign_handlers_alone(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The idempotency guard removes only the handlers this module installed.

    An embedding application may attach its own handler to the ``app`` logger,
    and a reconfiguration that discarded it would silently stop that
    application's logging.  Propagation is asserted in the same test because it
    is the other half of the same decision: records stop at the ``app`` logger,
    so a host that called ``basicConfig()`` does not print every line a second
    time on the wrong stream.
    """
    logger = logging.getLogger(PACKAGE_LOGGER_NAME)
    foreign_records: list[logging.LogRecord] = []

    class _ForeignHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            foreign_records.append(record)

    foreign = _ForeignHandler()
    foreign.set_name("an-embedding-application-handler")
    logger.addHandler(foreign)

    create_app({"TESTING": True})
    configure_logging()

    assert foreign in logger.handlers
    assert sorted(_handler_names(logger)) == sorted(
        MANAGED_HANDLER_NAMES | {"an-embedding-application-handler"}
    )
    assert logger.propagate is False

    logging.getLogger(PROBE_LOGGER_NAME).warning("reaches-every-handler")

    captured = capsys.readouterr()
    assert captured.err.count("reaches-every-handler") == 1
    assert [record.getMessage() for record in foreign_records] == [
        "reaches-every-handler"
    ]


def test_configuration_succeeds_on_a_stream_without_reconfigure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The line-buffer fallback: a stream with no ``reconfigure`` is fine.

    Line buffering is applied for the CI case - output captured through a pipe,
    where Python would otherwise hold progress lines in a block buffer - and it
    is an optimisation, never a requirement.  A stream that cannot be
    reconfigured is therefore an ordinary outcome: both handlers must still be
    installed, both must still write, and nothing may be reported as an error.

    The internal-report stream is replaced as well, because that is where the
    module writes a problem it cannot route through a logger; it staying empty
    is what proves this path is treated as normal rather than as a failure.
    """
    stdout = _StreamWithoutReconfigure()
    stderr = _StreamWithoutReconfigure()
    internal_reports = io.StringIO()

    assert not hasattr(stdout, "reconfigure")

    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    monkeypatch.setattr(sys, "__stderr__", internal_reports)

    configure_logging()
    logger = logging.getLogger(PROBE_LOGGER_NAME)
    logger.info("buffered-progress")
    logger.warning("buffered-diagnostic")

    assert sorted(
        _handler_names(logging.getLogger(PACKAGE_LOGGER_NAME))
    ) == sorted(MANAGED_HANDLER_NAMES)
    assert stdout.getvalue().count("buffered-progress") == 1
    assert stderr.getvalue().count("buffered-diagnostic") == 1
    assert internal_reports.getvalue() == ""


@pytest.mark.parametrize(
    "error",
    [
        ValueError("I/O operation on closed file"),
        OSError("stream is detached"),
        TypeError("reconfigure() got an unexpected keyword argument"),
        AttributeError("reconfigure"),
    ],
)
def test_configuration_survives_a_reconfigure_that_raises(
    monkeypatch: pytest.MonkeyPatch, error: BaseException
) -> None:
    """The line-buffer fallback's other shape: ``reconfigure`` exists and
    fails.

    Each of the four failures a real stream produces is covered - closed,
    detached, a custom stream rejecting the keyword, and an attribute
    disappearing mid-call - and every one of them must leave configuration
    complete and silent.  The call is asserted to have been *attempted*, so
    this cannot pass because the guard skipped the stream for some other
    reason.
    """
    stdout = _StreamWhoseReconfigureRaises(error)
    stderr = _StreamWhoseReconfigureRaises(error)
    internal_reports = io.StringIO()

    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    monkeypatch.setattr(sys, "__stderr__", internal_reports)

    configure_logging()
    logging.getLogger(PROBE_LOGGER_NAME).warning("survived-the-failure")

    assert stdout.reconfigure_calls == 1
    assert stderr.reconfigure_calls == 1
    assert sorted(
        _handler_names(logging.getLogger(PACKAGE_LOGGER_NAME))
    ) == sorted(MANAGED_HANDLER_NAMES)
    assert stderr.getvalue().count("survived-the-failure") == 1
    assert internal_reports.getvalue() == ""


def test_the_missing_properties_file_is_reported_once_per_process(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``ConfigurationReader``'s static-initializer semantics, in the log.

    ``ConfigurationReader.java:21-24`` prints its message once per JVM because
    the read happens in a static initializer; the port loads once per process
    and logs the same message once, at ``WARNING`` and therefore on stderr.
    Repeated reads - through the properties module and through the six-key
    accessors - must not produce a second record, because an initialized reader
    never re-reads.

    The assertion is made inside one test because ``tests/conftest.py``'s
    autouse ``isolate_process_state`` fixture resets that cache around every
    test; the count would be meaningless spread across two.
    """
    monkeypatch.chdir(tmp_path)
    create_app({"TESTING": True})

    from app import config as port_config

    with package_records_captured(caplog):
        assert properties.get_properties() == {}
        assert properties.get_properties() == {}
        assert properties.get_property("browser") is None
        assert port_config.get_browser() is None
        assert port_config.get_password() is None

    missing_file_records = [
        record
        for record in caplog.records
        if record.getMessage() == properties.MISSING_FILE_MESSAGE
    ]

    assert len(missing_file_records) == 1
    assert missing_file_records[0].levelno == logging.WARNING
    assert missing_file_records[0].name == "app.utils.properties"
    # The traceback the Java original printed alongside the message.
    assert missing_file_records[0].exc_info is not None


def test_no_configured_credential_reaches_a_stream_or_flask_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Configured values are never logged - ``password`` is one of the six keys.

    ``app/config.py`` logs key *names* only, and this test holds the whole
    round trip to that rule: a properties file carrying representative
    credentials is read through the public accessors, every value is confirmed
    to have actually been read, and then none of them may appear on stdout, on
    stderr, in the captured records or anywhere in Flask's config.

    Confirming the read first is what stops this passing for the wrong reason:
    a configuration layer that returned ``None`` for everything would satisfy
    the absence assertions trivially.
    """
    from app import config as port_config

    values = {
        "browser": "chrome",
        "web.table.url": "https://odoo.invalid/web/login?token=WEBTABLESENTINEL",
        "url": "https://odoo.invalid/web#menu_id=EMPLOYEEURLSENTINEL",
        "username": "qa-operator@USERNAMESENTINEL.invalid",
        "password": "PASSWORDSENTINEL-do-not-log-me",
        "EmplTitle": "Employees EMPLTITLESENTINEL",
    }
    secrets = tuple(
        value for key, value in values.items() if key != "browser"
    )

    properties_file = tmp_path / "configuration.properties"
    properties_file.write_text(
        "".join(f"{key}={value}\n" for key, value in values.items()),
        encoding="latin-1",
    )
    # Owner-only: the reader refuses a group- or world-accessible credential
    # file, so an unrestricted fixture would read back as six ``None`` values
    # and the secret-hygiene assertions below would prove nothing.
    os.chmod(properties_file, 0o600)
    monkeypatch.chdir(tmp_path)

    application = create_app({"TESTING": True})

    with package_records_captured(caplog):
        read_back = {
            "browser": port_config.get_browser(),
            "web.table.url": port_config.get_web_table_url(),
            "url": port_config.get_url(),
            "username": port_config.get_username(),
            "password": port_config.get_password(),
            "EmplTitle": port_config.get_empl_title(),
        }

    assert read_back == values

    captured = capsys.readouterr()
    for secret in secrets:
        assert secret not in captured.out
        assert secret not in captured.err
        assert secret not in caplog.text
        assert secret not in str(dict(application.config))


def test_the_command_configures_logging_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other caller of ``configure_logging()``, proved by removing the
    factory's.

    ``app/cli.py`` calls it first, before any work of any kind, because a
    Jenkins log stays live only if the handlers exist before the first
    message; ``create_app()`` calls it for the viewer.  Asserting the factory
    half alone leaves the command's call unprotected - a deleted line there
    would silently send every progress message through the standard library's
    last-resort handler, onto the wrong stream and without the format - so
    this test starts from a logger with **no** handlers at all and lets the
    command install them.

    The command is invoked for real, in an empty directory: with no feature
    tree there is nothing to select, so the run completes in a fraction of a
    second, writes its four artifacts under that directory and exits 0 - the
    documented outcome for a tag expression that selects nothing.  No worker
    process is spawned, no browser is started and nothing outside ``tmp_path``
    is written.
    """
    from click.testing import CliRunner

    from app.cli import run_tests

    package_logger = logging.getLogger(PACKAGE_LOGGER_NAME)
    package_logger.handlers[:] = []
    package_logger.propagate = True
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(
        run_tests, ["--dry-run", "--workers", "1"], catch_exceptions=False
    )

    assert result.exit_code == 0, result.output
    assert sorted(_handler_names(package_logger)) == sorted(MANAGED_HANDLER_NAMES)
    assert package_logger.level == logging.INFO
    assert package_logger.propagate is False

    # The split holds on the command's own path: its progress line is on
    # stdout and the problems it tolerated are on stderr, neither crossing.
    assert "Starting the suite" in result.stdout
    assert "Starting the suite" not in result.stderr
    assert f"INFO {PACKAGE_LOGGER_NAME}.cli: Starting the suite" in result.stdout
    assert "ERROR" not in result.stdout
    assert "ERROR" in result.stderr

    assert (tmp_path / "target" / "cucumber.json").is_file()


def test_repeated_command_and_factory_configuration_do_not_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two command runs and an application built between them: one line each.

    The two callers configure the same logger, so the idempotency guard has to
    hold *across* them and not only within one of them.  A run inside a
    process that has already built an application - which is what happens
    whenever the unit suite exercises both - would otherwise emit every
    progress line as many times as the handlers had been stacked, and a
    Jenkins console would show each message two or three times.

    Occurrences are counted rather than found, because presence is exactly
    what duplication also satisfies.
    """
    from click.testing import CliRunner

    from app.cli import run_tests

    package_logger = logging.getLogger(PACKAGE_LOGGER_NAME)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    first = runner.invoke(
        run_tests, ["--dry-run", "--workers", "1"], catch_exceptions=False
    )
    create_app({"TESTING": True})
    second = runner.invoke(
        run_tests, ["--dry-run", "--workers", "1"], catch_exceptions=False
    )

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert sorted(_handler_names(package_logger)) == sorted(MANAGED_HANDLER_NAMES)

    for result in (first, second):
        assert result.stdout.count("Starting the suite") == 1, result.stdout
        assert result.stdout.count("Finished with status 0") == 1, result.stdout


def test_the_verbosity_control_is_the_only_thing_that_admits_debug(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``verbose`` moves the threshold, and nothing else does.

    The factory's own build message is logged at DEBUG precisely so that an
    application being built stays off the console, which is what keeps it from
    disturbing the stdout/stderr split the command-line contract depends on.
    Both halves are asserted in one test because the claim is a comparison: the
    default threshold suppresses a DEBUG record, the verbose threshold admits
    it, and the record lands on stdout either way rather than moving stream
    with its level.  ``verbose`` is the port's only verbosity control - no
    environment variable and no configuration key affects logging, the
    configuration surface being fixed at six keys - so a second lever
    appearing here would be a surface the port does not have.
    """
    logger = logging.getLogger(PROBE_LOGGER_NAME)

    configure_logging()
    assert logging.getLogger(PACKAGE_LOGGER_NAME).level == logging.INFO
    logger.debug("quiet-sentinel")
    logger.info("default-sentinel")
    default = capsys.readouterr()

    configure_logging(verbose=True)
    assert logging.getLogger(PACKAGE_LOGGER_NAME).level == logging.DEBUG
    logger.debug("verbose-sentinel")
    verbose = capsys.readouterr()

    assert "quiet-sentinel" not in default.out
    assert "quiet-sentinel" not in default.err
    assert "default-sentinel" in default.out

    assert f"DEBUG {PROBE_LOGGER_NAME}: verbose-sentinel" in verbose.out
    assert "verbose-sentinel" not in verbose.err


def test_the_merged_stream_option_installs_one_handler_on_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``stream_split=False`` merges both levels onto the error stream.

    The alternative the module publishes for a caller that wants the standard
    library's own single-stream behaviour.  It is asserted here because the
    branch is reachable through the published signature and because the
    partition test above would pass just as well if the split were the *only*
    thing the function could do - the two together pin that the split is a
    choice the caller makes rather than an accident of the implementation.
    Exactly one handler is installed, so a merged configuration cannot emit a
    record twice either.
    """
    package_logger = logging.getLogger(PACKAGE_LOGGER_NAME)
    logger = logging.getLogger(PROBE_LOGGER_NAME)

    configure_logging(stream_split=False)

    managed = [
        name for name in _handler_names(package_logger) if name in MANAGED_HANDLER_NAMES
    ]
    assert managed == [STDERR_HANDLER_NAME]

    logger.info("merged-progress-sentinel")
    logger.warning("merged-warning-sentinel")
    captured = capsys.readouterr()

    assert captured.out == ""
    assert f"INFO {PROBE_LOGGER_NAME}: merged-progress-sentinel" in captured.err
    assert f"WARNING {PROBE_LOGGER_NAME}: merged-warning-sentinel" in captured.err
    assert captured.err.count("merged-progress-sentinel") == 1


def test_handlers_write_to_the_streams_the_process_carries_now() -> None:
    """The handlers resolve ``sys.stdout``/``sys.stderr`` on every use.

    Not a detail: ``configure_logging()`` runs while an application is being
    built or before a run starts, long before most records are emitted, so a
    handler that bound the stream object current at that moment would keep
    writing to it after a host, a harness or a wrapper had legitimately
    replaced it.  The streams are replaced *after* configuration here, and the
    records must follow.

    The documented degradation is asserted in the same test, because it is the
    same mechanism seen from the other side: a process with no ``sys.stdout``
    at all - a Windows GUI host, and this port supports Windows - merges onto
    the error stream instead of failing the run, and nothing is raised.
    """
    logger = logging.getLogger(PROBE_LOGGER_NAME)
    configure_logging()

    out_stream, err_stream = io.StringIO(), io.StringIO()
    # A context-managed patcher rather than the fixture, because the process
    # streams must be restored before pytest's own capture is read back; the
    # fixture's teardown would run too late for that.
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(sys, "stdout", out_stream)
        patcher.setattr(sys, "stderr", err_stream)

        logger.info("late-bound-progress")
        logger.warning("late-bound-warning")

        assert "late-bound-progress" in out_stream.getvalue()
        assert "late-bound-progress" not in err_stream.getvalue()
        assert "late-bound-warning" in err_stream.getvalue()

        # No stdout at all: the split collapses onto the error stream rather
        # than raising, and the record is not lost.
        patcher.setattr(sys, "stdout", None)
        logger.info("degraded-progress")

        assert "degraded-progress" in err_stream.getvalue()


# =========================================================================== #
# The project-level declarations
#
# Read from ``repo_root`` and asserted as contracts rather than as wording:
# what the AAP fixes, in the form it fixes it, so that an editor rewording a
# sentence does not fail a test while an editor removing a guarantee does.
# ``pyproject.toml`` is parsed with the standard library's ``tomllib``; no
# dependency is added for these tests.
# =========================================================================== #

#: The interpreter the port is pinned to (AAP 0.3.1, §0.8).
PINNED_PYTHON_VERSION: Final[str] = "3.14.6"

#: The range ``pyproject.toml`` declares, which must admit the pin above.
DECLARED_PYTHON_RANGE: Final[str] = "==3.14.*"

#: Distribution identity, translated from ``pom.xml``'s coordinates.
DISTRIBUTION_NAME: Final[str] = "testinium-qa"
DISTRIBUTION_VERSION: Final[str] = "1.0.0.dev0"

#: The one console script, and the callable it must point at.
CONSOLE_SCRIPT_TARGET: Final[str] = "app.cli:run_tests"

#: The seven runtime distributions AAP 0.5.1 tabulates, at the versions it
#: pins.  Keys are compared case-insensitively, PyPI-style, so ``Flask`` and
#: ``flask`` are the same requirement; the versions are exact and are the
#: measured output of installing into the pinned interpreter.
AAP_RUNTIME_PINS: Final[Mapping[str, str]] = {
    "flask": "3.1.3",
    "jinja2": "3.1.6",
    "click": "8.5.0",
    "behave": "1.3.3",
    "cucumber-tag-expressions": "11.0.1",
    "selenium": "4.48.0",
    "webdriver-manager": "4.1.2",
}

#: An exact pin, and the only form either manifest may use (AAP 0.7: "exact
#: pinned versions with no ``latest`` and no placeholders").
EXACT_PIN: Final[re.Pattern[str]] = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)==(?P<version>\d+(?:\.\d+)*)$"
)

#: Spellings that would make a pin inexact or unresolved.
FORBIDDEN_PIN_TOKENS: Final[tuple[str, ...]] = (
    "latest",
    ">=",
    "<=",
    "~=",
    "!=",
    "*",
    "todo",
    "tbd",
    "xxx",
)

#: The four scoped coverage gates AAP 0.5.1 pins, as the flags a command passes:
#: ``app/utils`` at 90 percent, ``app/pages`` at 85, ``app/automation`` and
#: ``app/reporting`` at 80.  A single ``--cov-fail-under`` cannot express four
#: thresholds, so each scope is its own pytest run and each run gates only its
#: own package.  The ``Makefile``'s ``coverage`` target is their canonical
#: declaration and both runner scripts repeat them, because CI must not depend
#: on ``make`` being installed - so the set is asserted equal across the two
#: scripts *and* equal to this declaration, which is what keeps a threshold from
#: being lowered in three files at once without anything noticing.
COVERAGE_GATE_FLAGS: Final[frozenset[str]] = frozenset(
    {
        "--cov=app/utils",
        "--cov=app/pages",
        "--cov=app/automation",
        "--cov=app/reporting",
        "--cov-fail-under=90",
        "--cov-fail-under=85",
        "--cov-fail-under=80",
    }
)

#: The pipeline's three stages, in order (AAP 0.4.1: names and order preserved).
JENKINS_STAGES: Final[tuple[str, ...]] = (
    "Clone code",
    "Run tests",
    "Generate report",
)

#: The publisher's six threshold fields, every one of which must be ``-1`` -
#: the setting that makes a test outcome unable to fail the build, which is the
#: exit contract the port preserves.
JENKINS_THRESHOLD_FIELDS: Final[tuple[str, ...]] = (
    "failedFeaturesNumber",
    "failedScenariosNumber",
    "failedStepsNumber",
    "pendingStepsNumber",
    "skippedStepsNumber",
    "undefinedStepsNumber",
)

#: A ``# Step N - ...`` banner, the stage structure both runner scripts carry.
SCRIPT_STAGE_HEADER: Final[re.Pattern[str]] = re.compile(
    r"^#\s*(Step\s+\d+\s*-\s*.+?)\s*$"
)

#: How each script invokes the console script: ``exec .venv/bin/run-tests`` on
#: POSIX and, on Windows, a native invocation of the variable holding the
#: ``run-tests.exe`` shim - either through the call operator (``& $venvRunTests``)
#: or through the script's own ``Invoke-NativeCommand`` helper, which is what
#: captures the shim's exit status without a PowerShell 5.1 agent turning a
#: stderr write into a terminating error.  Three spellings of one contract.
#:
#: Anchored at the start of a statement on purpose: the PowerShell script
#: *assigns* that variable, and names it again in the guard that checks the
#: shim exists, well before step 6.  Matching a mention rather than an
#: invocation would report the suite run as happening before the unit gate.
RUN_TESTS_INVOCATION: Final[re.Pattern[str]] = re.compile(
    r"^(?:exec\s+)?\S*run-tests\b"
    r"|^[&.]\s*\$\w*RunTests\b"
    r"|^Invoke-NativeCommand\b[^\n]*-FilePath\s+\$\w*RunTests\b",
    re.IGNORECASE,
)


def _pyproject(repo_root: Path) -> dict[str, Any]:
    """Parse ``pyproject.toml`` with the standard library."""
    return tomllib.loads((repo_root / "pyproject.toml").read_text("utf-8"))


def _requirement_lines(text: str) -> list[str]:
    """The requirement lines of a pip manifest: no comments, no blanks."""
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _shell_statements(text: str) -> list[str]:
    """The lines of a POSIX script that are not comments or blank."""
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _unquoted_paren_delta(line: str) -> int:
    """How many parentheses *line* leaves open, quoted text excluded.

    PowerShell's two quoting forms are scanned rather than stripped, because a
    diagnostic string in this script legitimately contains parentheses - "the
    unit gate failed (pytest exit status ...)" - and counting those would fold
    the rest of the file into one statement.

    :param line: One physical line of a PowerShell script.
    :returns: Opening parentheses minus closing ones, outside quotes.
    """
    depth = 0
    quote: str | None = None
    for character in line:
        if quote is not None:
            if character == quote:
                quote = None
            continue
        if character in "'\"":
            quote = character
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
    return depth


def _powershell_command_text(statement: str) -> str:
    """One PowerShell statement as the command line it expresses.

    A native command in this script is written as a cmdlet call with an
    argument *array* - ``-ArgumentList @('-m', 'pip', 'install', ...)`` - where
    the POSIX script writes the same command as a plain command line.  The two
    say the same thing and must be comparable, so the array's punctuation is
    normalised away: tokens are split on whitespace and commas outside quotes,
    one level of quoting is removed from each, and the result is re-joined with
    single spaces.  ``pip install`` and ``-r requirements.txt`` are then
    literally present in both scripts, which is what lets one set of assertions
    describe both.

    Quoted text is preserved as a token rather than dropped, so a diagnostic
    string stays visible - it simply reads as words, and none of the command
    tokens the assertions look for can be forged by one, since every such token
    carries the option syntax a message does not.

    :param statement: A logical statement, already folded onto one line.
    :returns: The same statement with array punctuation and one level of
        quoting removed.
    """
    tokens: list[str] = []
    for raw in re.findall(
        r"""'(?:[^']|'')*'|"(?:[^"]|"")*"|@\(|[()]|[^\s,()]+""", statement
    ):
        if len(raw) >= 2 and raw[0] in "'\"" and raw[-1] == raw[0]:
            tokens.append(raw[1:-1].replace(raw[0] * 2, raw[0]))
        else:
            tokens.append(raw)
    return " ".join(tokens)


def _powershell_statements(text: str) -> list[str]:
    """The executable statements of a PowerShell script, one per element.

    Three transformations, each required before a line of this script can be
    compared with a line of its POSIX twin:

    * **Comments removed.**  PowerShell carries two forms and both matter here:
      ``#`` to end of line, and ``<# ... #>`` spanning lines - which is what
      the comment-based help header at the top of ``run_tests.ps1`` is written
      in, and which mentions pytest and the console script in prose that must
      not be mistaken for a command.
    * **Continuations folded.**  A statement whose parentheses are still open
      continues on the following lines, which is how every native invocation in
      the script is written: the cmdlet, then its argument array over several
      lines.  Folding them onto one statement is what makes a command's
      arguments part of the command rather than five separate statements, and
      keeps the statement *index* a meaningful position in the bootstrap order.
    * **Array punctuation normalised**, by :func:`_powershell_command_text`.

    :param text: The script's source.
    :returns: One normalised statement per logical statement, in order.
    """
    statements: list[str] = []
    pending: list[str] = []
    depth = 0
    in_block_comment = False

    for line in text.splitlines():
        stripped = line.strip()
        if in_block_comment:
            if "#>" in stripped:
                in_block_comment = False
            continue
        if depth == 0 and stripped.startswith("<#"):
            if "#>" not in stripped[2:]:
                in_block_comment = True
            continue
        if not stripped or stripped.startswith("#"):
            # A blank or commented line carries no argument, and inside a
            # folded array it does not end the statement either.
            continue

        pending.append(stripped)
        depth = max(depth + _unquoted_paren_delta(stripped), 0)
        if depth == 0:
            statements.append(_powershell_command_text(" ".join(pending)))
            pending = []

    if pending:
        # An unbalanced tail cannot happen in a script PowerShell can parse,
        # but dropping it silently would hide the statement rather than the
        # defect, so it is reported as the statement it is.
        statements.append(_powershell_command_text(" ".join(pending)))

    return statements


def _stage_headers(text: str) -> list[str]:
    """The ordered ``Step N - ...`` banners of a runner script, normalised.

    Case is folded and runs of whitespace collapsed, so that parity between
    the two scripts is about the stages and their order rather than about how
    either file happens to wrap or capitalise a banner.
    """
    headers: list[str] = []
    for line in text.splitlines():
        match = SCRIPT_STAGE_HEADER.match(line.strip())
        if match:
            headers.append(" ".join(match.group(1).lower().split()))
    return headers


def _stage_number(header: str) -> int:
    """The step number a normalised banner carries.

    Parity is asserted over these rather than over the banners' words: the two
    scripts are written in different languages and their comments are allowed
    to read differently, while a stage present in one and absent from the other
    must fail.

    :param header: A banner from :func:`_stage_headers`.
    :returns: Its step number.
    :raises AssertionError: If the banner carries no number, which would mean
        :data:`SCRIPT_STAGE_HEADER` and this function had drifted apart.
    """
    match = re.match(r"step\s+(\d+)\b", header)
    assert match is not None, f"banner {header!r} carries no step number"
    return int(match.group(1))


def _bootstrap_order(statements: Sequence[str]) -> dict[str, int]:
    """Locate each bootstrap stage of a runner script by statement index.

    Positions rather than line numbers, and the *first* occurrence of each, so
    the result describes the order the stages run in whatever else the script
    grows.  Deliberately indifferent to how many pytest invocations a script
    carries: the coverage gates AAP 0.5.1 places before the browser run are
    further pytest calls, and this must keep describing the same five stages
    when they are added.

    :param statements: A script's executable lines, in order.
    :returns: Stage name -> index of the statement that performs it, for every
        stage found.  A missing key is a missing stage, which the assertions
        report by name.
    """
    stages: dict[str, int] = {}
    for index, statement in enumerate(statements):
        lowered = statement.lower()
        if (
            "interpreter_pin" not in stages
            and PINNED_PYTHON_VERSION in statement
            and "version" in lowered
        ):
            stages["interpreter_pin"] = index
        if (
            "manifest_install" not in stages
            and "-r requirements.txt" in statement
            and "-r requirements-test.txt" in statement
        ):
            stages["manifest_install"] = index
        if (
            "editable_install" not in stages
            and "pip install" in statement
            and "-e ." in statement
        ):
            stages["editable_install"] = index
        if "unit_gate" not in stages and "-m pytest" in statement:
            stages["unit_gate"] = index
        if "suite_run" not in stages and RUN_TESTS_INVOCATION.match(statement):
            stages["suite_run"] = index
    return stages


def _markdown_section(text: str, pattern: re.Pattern[str]) -> str:
    """The body of the first heading whose text matches ``pattern``.

    Sections are delimited by the next heading at any level, so the body is
    exactly what a reader would consider to be under that heading.
    """
    heading = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
    collecting = False
    body: list[str] = []
    for line in text.splitlines():
        match = heading.match(line)
        if match:
            if collecting:
                break
            collecting = bool(pattern.search(match.group(2)))
            continue
        if collecting:
            body.append(line)
    return "\n".join(body)


def _numbered_items(section: str) -> list[str]:
    """The numbered list items of a markdown section, continuations included."""
    items: list[str] = []
    current: list[str] = []
    for line in section.splitlines():
        if re.match(r"^\s*\d+\.\s", line):
            if current:
                items.append(" ".join(current))
            current = [line.strip()]
        elif current and line.strip():
            current.append(line.strip())
        elif current:
            items.append(" ".join(current))
            current = []
    if current:
        items.append(" ".join(current))
    return items


def test_the_runtime_pin_agrees_between_its_two_declarations(
    repo_root: Path,
) -> None:
    """AAP 0.3.1: ``.python-version`` is 3.14.6 and ``pyproject.toml`` declares
    ``==3.14.*``, and the range admits the pin.

    Two files state the interpreter, for two different readers - the version
    manager and the build backend - so the invariant is not either value alone
    but their agreement.  Agreement is computed rather than asserted as a
    literal pair: the declared range is the pinned version's major and minor
    with a wildcard patch, which is what "the range admits the pin" means for a
    ``==X.Y.*`` specifier.
    """
    recorded = (repo_root / ".python-version").read_text("utf-8").strip()
    declared = _pyproject(repo_root)["project"]["requires-python"]

    assert recorded == PINNED_PYTHON_VERSION
    assert declared == DECLARED_PYTHON_RANGE

    major, minor, *patch = recorded.split(".")
    assert patch, f"{recorded!r} names no patch level, so it is not an exact pin"
    assert declared == f"=={major}.{minor}.*"


def test_the_console_script_declaration_resolves_to_the_command(
    flask_app: Flask, repo_root: Path
) -> None:
    """AAP 0.4.1: the distribution, its version and its one entry point - and
    the entry point actually resolves.

    A ``[project.scripts]`` target is a string until something imports it, so
    this test imports it and compares the result *by identity* with the command
    the factory registers.  That closes the loop between the packaging and the
    application: the ``run-tests`` a user gets from ``.venv/bin`` is the same
    object ``app/cli.py`` defines and the factory attaches.
    """
    import importlib

    project = _pyproject(repo_root)["project"]

    assert project["name"] == DISTRIBUTION_NAME
    assert project["version"] == DISTRIBUTION_VERSION
    assert project["scripts"] == {COMMAND_NAME: CONSOLE_SCRIPT_TARGET}

    module_name, _, attribute = CONSOLE_SCRIPT_TARGET.partition(":")
    target = getattr(importlib.import_module(module_name), attribute)

    assert target is flask_app.cli.commands[COMMAND_NAME]
    assert target.name == COMMAND_NAME


def test_the_build_installs_the_application_package_and_nothing_else(
    repo_root: Path,
) -> None:
    """AAP 0.3.1's flat layout: ``app*`` is installable, the three sibling trees
    are not.

    ``features/``, ``tests/`` and ``scripts/`` sit beside ``app/`` and are not
    distributions - behave loads the feature tree by path, pytest discovers
    ``tests/`` from the repository root, and ``scripts/`` holds shell entry
    points - so including any of them would ship code that has no importable
    contract.  ``namespaces = false`` is part of the same decision: it keeps
    ``templates/`` and ``static/``, which carry no ``__init__.py``, from being
    discovered as implicit namespace packages instead of shipped as data.
    """
    found = _pyproject(repo_root)["tool"]["setuptools"]["packages"]["find"]

    assert found["include"] == ["app*"]
    for excluded in ("features*", "tests*", "scripts*"):
        assert excluded in found["exclude"]
    assert found["namespaces"] is False


def test_package_data_covers_every_template_and_static_asset(
    repo_root: Path,
) -> None:
    """AAP 0.4.3: the template and static trees travel with an installed copy.

    Asserted against the trees as they actually are, rather than against the
    declaration alone: every file under ``app/templates`` and ``app/static`` is
    matched against the declared patterns, so a new template group or asset
    directory that the declaration does not cover fails here.  A wheel missing
    them renders unstyled reports with broken ``href``/``src`` references,
    which is a defect no import-time test would notice.
    """
    package_data = _pyproject(repo_root)["tool"]["setuptools"]["package-data"]
    patterns = package_data["app"]

    assert "templates/*.html" in patterns
    assert "templates/**/*.html" in patterns
    assert "static/css/*.css" in patterns
    assert "static/js/*.js" in patterns

    package_root = repo_root / "app"
    shipped = [
        path
        for directory in ("templates", "static")
        for path in (package_root / directory).rglob("*")
        if path.is_file()
    ]
    assert shipped, "the template and static trees are empty"

    for path in shipped:
        relative = PurePosixPath(path.relative_to(package_root).as_posix())
        assert any(
            relative.full_match(pattern) for pattern in patterns
        ), f"{relative} is not covered by [tool.setuptools.package-data]"


def test_requirements_pins_the_whole_runtime_list_exactly(
    repo_root: Path,
) -> None:
    """AAP 0.5.1: the runtime distributions, every one exact-pinned, and nothing
    else.

    The manifest is the list every installer in the port consumes - both runner
    scripts and the ``Makefile`` - so an unpinned or floating entry would make
    a run irreproducible without anything else noticing.  It is asserted as an
    exact mapping in both directions: the seven distributions AAP 0.5.1
    tabulates must be present at the versions it pins, exactly, and an eighth
    name arriving from anywhere - a transitive distribution promoted to a
    direct pin included - fails here.

    ``pyproject.toml`` no longer restates the list, and that is the point of
    the second half: it declares ``dependencies`` **dynamic** and derives the
    field from this very file, so the two declarations every installer consumes
    cannot state different versions.  What is asserted is therefore the
    mechanism rather than a second copy - a literal array here would be the
    drift this arrangement removes, so its *absence* is part of the contract.
    """
    lines = _requirement_lines(
        (repo_root / "requirements.txt").read_text("utf-8")
    )

    pins: dict[str, str] = {}
    for line in lines:
        match = EXACT_PIN.match(line)
        assert match is not None, f"{line!r} is not an exact == pin"
        lowered = line.lower()
        for token in FORBIDDEN_PIN_TOKENS:
            assert token not in lowered, f"{line!r} carries {token!r}"
        pins[match.group("name").lower()] = match.group("version")

    assert pins == dict(AAP_RUNTIME_PINS)
    for name, version in AAP_RUNTIME_PINS.items():
        assert pins.get(name) == version, f"{name} is not pinned at {version}"

    project = _pyproject(repo_root)["project"]

    assert "dependencies" in project.get("dynamic", []), (
        "requirements.txt is the authoritative runtime list, so [project] must "
        "declare dependencies dynamic"
    )
    assert "dependencies" not in project, (
        "dependencies are declared dynamic and must not also be restated as a "
        "literal array, which is what let the two copies drift"
    )
    assert _pyproject(repo_root)["tool"]["setuptools"]["dynamic"]["dependencies"] == {
        "file": ["requirements.txt"]
    }, "the dynamic dependency table must derive the metadata from the manifest"


def test_gitattributes_keeps_the_html_and_line_ending_rules(
    repo_root: Path,
) -> None:
    """AAP 0.4.1: the ``*.html`` linguist attribute is retained, and the two
    text types are LF-normalised.

    The linguist rule stays because the port still emits HTML - two of the four
    report artifacts are HTML trees - so without it the repository's language
    statistics would describe generated output rather than source.  The LF
    rules matter for the two file kinds that are executed rather than read:
    Python modules and the POSIX runner script, which a CRLF checkout would
    break at the shebang.
    """
    lines = [
        line.strip()
        for line in (repo_root / ".gitattributes").read_text("utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    assert "*.html linguist-detectable=false" in lines

    for pattern in ("*.py", "*.sh"):
        rules = [line for line in lines if line.split()[0] == pattern]
        assert rules, f"{pattern} carries no line-ending rule"
        assert any("eol=lf" in rule for rule in rules), (
            f"{pattern} is not LF-normalised: {rules}"
        )


def test_the_two_runner_scripts_bootstrap_in_the_same_order(
    repo_root: Path,
) -> None:
    """AAP 0.4.1: the POSIX and PowerShell entry points are the same script in
    two languages.

    The pipeline's ``isUnix()`` branch selects one or the other, so a stage
    present in one and missing from the other means CI does different work on
    the two platforms - the defect no single-platform run can reveal.  Parity
    is asserted two ways: the ordered stage banners must be identical text, and
    each script independently must pin the interpreter, install both manifests,
    install the project itself, gate on pytest and only then invoke the console
    script.

    Deliberately expressed as an *order* rather than as fixed line content, so
    that the two scripts evolving together - the coverage gates AAP 0.5.1
    places before the browser run, for instance - keeps this test true while
    either script drifting from the other still fails it.
    """
    posix = (repo_root / "scripts" / "run_tests.sh").read_text("utf-8")
    powershell = (repo_root / "scripts" / "run_tests.ps1").read_text("utf-8")

    posix_headers = _stage_headers(posix)
    powershell_headers = _stage_headers(powershell)

    assert posix_headers, "run_tests.sh carries no stage banners"
    assert powershell_headers, "run_tests.ps1 carries no stage banners"
    # The stage *sequence* is the contract, not the prose of a banner: these
    # are two scripts in two languages, and a stage added to one but not the
    # other is the defect - a comment worded differently in each is not.  Each
    # script must therefore number its stages 1..N contiguously and in order,
    # and the two must declare the same N.
    posix_numbers = [_stage_number(header) for header in posix_headers]
    powershell_numbers = [_stage_number(header) for header in powershell_headers]

    assert posix_numbers == list(range(1, len(posix_numbers) + 1)), (
        f"run_tests.sh numbers its stages {posix_numbers}"
    )
    assert powershell_numbers == list(range(1, len(powershell_numbers) + 1)), (
        f"run_tests.ps1 numbers its stages {powershell_numbers}"
    )
    assert len(posix_headers) == len(powershell_headers), (
        f"run_tests.sh declares {len(posix_headers)} stages "
        f"{posix_headers} and run_tests.ps1 declares "
        f"{len(powershell_headers)} {powershell_headers}"
    )

    orders = {
        "run_tests.sh": _bootstrap_order(_shell_statements(posix)),
        "run_tests.ps1": _bootstrap_order(_powershell_statements(powershell)),
    }

    for name, stages in orders.items():
        for stage in (
            "interpreter_pin",
            "manifest_install",
            "editable_install",
            "unit_gate",
            "suite_run",
        ):
            assert stage in stages, f"{name} performs no {stage}"

        assert stages["interpreter_pin"] < stages["manifest_install"], name
        assert stages["manifest_install"] <= stages["editable_install"], name
        assert stages["editable_install"] < stages["unit_gate"], name
        assert stages["unit_gate"] < stages["suite_run"], name

    # Both name the console script the distribution installs, rather than
    # invoking behave or pytest as the suite runner.
    assert any(
        ".venv/bin/run-tests" in statement
        for statement in _shell_statements(posix)
    )
    assert any(
        "run-tests" in statement
        for statement in _powershell_statements(powershell)
    )

    # The command surfaces themselves, not only the stage order: a stage that
    # ran in both scripts but installed different manifests, gated on a
    # different runner or pinned a different interpreter would satisfy every
    # assertion above while still making CI do different work on the two
    # platforms.  Each token below is checked in both scripts, so parity is
    # asserted over what the commands actually say.
    for name, source in (("run_tests.sh", posix), ("run_tests.ps1", powershell)):
        statements = (
            _shell_statements(source)
            if name.endswith(".sh")
            else _powershell_statements(source)
        )
        joined = "\n".join(statements)

        assert PINNED_PYTHON_VERSION in source, f"{name} pins no interpreter version"
        assert "-r requirements.txt" in joined, f"{name} installs no runtime manifest"
        assert "-r requirements-test.txt" in joined, f"{name} installs no test manifest"
        assert re.search(r"pip install[^\n]*\s-e\s+\.", joined), (
            f"{name} performs no editable install"
        )
        assert re.search(r"-m\s+pip\s+install", joined), (
            f"{name} does not install through the environment's own pip"
        )
        assert re.search(r"-m\s+pytest", joined), f"{name} runs no pytest gate"

    # Any pytest invocation added to one script must be added to the other,
    # counted rather than merely present: the coverage gates AAP 0.5.1 places
    # before the browser run are further pytest calls, and a gate reaching only
    # one platform is the same defect as a missing stage.  The scoped
    # thresholds are compared as sets for the same reason.
    posix_pytest = re.findall(r"-m\s+pytest[^\n]*", "\n".join(_shell_statements(posix)))
    powershell_pytest = re.findall(
        r"-m\s+pytest[^\n]*", "\n".join(_powershell_statements(powershell))
    )
    assert len(posix_pytest) == len(powershell_pytest), (
        f"run_tests.sh makes {len(posix_pytest)} pytest call(s) {posix_pytest} and "
        f"run_tests.ps1 makes {len(powershell_pytest)} {powershell_pytest}"
    )
    # Read from the statements rather than from the files: both scripts
    # *discuss* coverage in their comments - "a single --cov-fail-under cannot
    # express four thresholds", "$1 the --cov scope" - and a set built from the
    # raw text compares two prose styles alongside the four gates.  What has to
    # match is the flags the commands actually pass.
    posix_cov = set(re.findall(r"--cov\S*", "\n".join(_shell_statements(posix))))
    powershell_cov = set(
        re.findall(r"--cov\S*", "\n".join(_powershell_statements(powershell)))
    )
    assert posix_cov == powershell_cov, (
        "the two scripts declare different coverage scopes or thresholds: "
        f"run_tests.sh passes {sorted(posix_cov)} and run_tests.ps1 passes "
        f"{sorted(powershell_cov)}"
    )
    # And the gates are the four AAP 0.5.1 pins, so a pair of scripts that
    # agreed on the wrong thresholds - or on none - still fails.
    assert posix_cov == COVERAGE_GATE_FLAGS


def test_jenkins_keeps_the_publisher_and_stage_invariants(
    repo_root: Path,
) -> None:
    """AAP 0.4.1: the pipeline's continuity invariants, which its update leaves
    unchanged.

    **Scope, stated because it decides what this test may assert.** The AAP
    makes exactly four changes to ``Jenkins`` - ``checkout scm``, the two
    script invocations and narrowing the publisher glob to
    ``target/cucumber.json`` - and it declares everything else preserved: the
    three stage names and their order, the ``isUnix()`` branch with one command
    per arm, one ``cucumber`` publisher call, all six ``-1`` thresholds and
    alphabetical sorting.  Only those preserved invariants are asserted here,
    and each holds both before and after that update, so this test describes
    the pipeline's continuity rather than which side of the update the file is
    on.  The publisher pattern is therefore checked for naming a JSON path -
    true of both the original glob and the narrowed one - rather than for a
    literal value.
    """
    text = (repo_root / "Jenkins").read_text("utf-8")

    assert re.findall(r"stage\('([^']+)'\)", text) == list(JENKINS_STAGES)

    assert "isUnix()" in text
    assert len(re.findall(r"^\s*sh\s+\S", text, re.MULTILINE)) == 1
    assert len(re.findall(r"^\s*bat\s+\S", text, re.MULTILINE)) == 1

    publisher_calls = re.findall(r"^\s*cucumber\s+\w+:", text, re.MULTILINE)
    assert len(publisher_calls) == 1

    for field in JENKINS_THRESHOLD_FIELDS:
        assert len(re.findall(rf"\b{field}:\s*-1\b", text)) == 1, (
            f"{field} is absent or is not -1"
        )

    assert "sortingMethod: 'ALPHABETICAL'" in text

    pattern = re.search(r"fileIncludePattern:\s*'([^']+)'", text)
    assert pattern is not None
    assert pattern.group(1).endswith(".json"), (
        f"the publisher pattern {pattern.group(1)!r} names no JSON path"
    )


def test_jenkins_matches_its_era_exactly(repo_root: Path) -> None:
    """AAP 0.4.1: the pipeline's command surface, pinned to the ported contract.

    The four changes AAP 0.4.1 makes to ``Jenkins`` are the whole of its
    command surface, and all four are pinned here: ``checkout scm`` in place of
    the hard-coded reference clone, ``sh "sh scripts/run_tests.sh"`` on the
    POSIX arm, the PowerShell runner on the Windows arm, and the publisher glob
    narrowed to ``target/cucumber.json``.  No Maven-era literal may survive
    anywhere in the file, so a half-applied update - a retargeted checkout
    still running ``mvn``, a runner script stage still publishing ``**/*.json``
    - fails rather than passing as some intermediate state.

    The narrowed glob is asserted together with the absence of ``**/*.json``
    because that absence is the reason it was narrowed: a run writes per-worker
    intermediate JSON under ``target/.workers/``, and the recursive glob would
    have had the publisher ingest every one of them alongside the real report.

    The Windows arm is asserted as a command made of required parts rather than
    as one literal string, because it carries invocation hardening on top of
    the AAP's spelling: the interpreter is named by its absolute System32 path,
    so ``PATH`` cannot decide which ``powershell`` runs, and ``-NoLogo``,
    ``-NoProfile`` and ``-NonInteractive`` stop the Jenkins account's profile
    and the all-user profiles from executing ahead of the tracked script on an
    unattended agent.  ``-File`` stays last, since PowerShell passes everything
    after it to the script.  Flag order otherwise is not part of the contract;
    the executable, the three hardening flags, the execution policy and the
    script file are.  Every path separator is matched as one or two
    backslashes throughout, because a backslash is a Groovy string escape and
    either spelling reaches ``cmd`` as one.

    The invariants this update leaves untouched - the three stage names and
    their order, the ``isUnix()`` branch, the single publisher call, the six
    ``-1`` thresholds and alphabetical sorting - are asserted by the test
    above, so this test adds the command surface to them.
    """
    text = (repo_root / "Jenkins").read_text("utf-8")

    assert re.search(r"^\s*checkout scm\s*$", text, re.MULTILINE) is not None, (
        "the pipeline does not take its repository from the job configuration"
    )
    assert (
        re.search(r"""^\s*sh\s+"sh\s+scripts/run_tests\.sh"\s*$""", text, re.MULTILINE)
        is not None
    ), "the POSIX arm does not run scripts/run_tests.sh"

    windows = re.search(r"""^\s*bat\s+"([^"]+)"\s*$""", text, re.MULTILINE)
    assert windows is not None, "the pipeline declares no Windows command"
    command = windows.group(1)
    for description, pattern in (
        (
            "the absolute System32 PowerShell executable",
            r"^%SystemRoot%[\\/]{1,2}System32[\\/]{1,2}WindowsPowerShell"
            r"[\\/]{1,2}v1\.0[\\/]{1,2}powershell\.exe\b",
        ),
        ("-NoLogo", r"\s-NoLogo\b"),
        ("-NoProfile", r"\s-NoProfile\b"),
        ("-NonInteractive", r"\s-NonInteractive\b"),
        ("-ExecutionPolicy Bypass", r"\s-ExecutionPolicy\s+Bypass\b"),
        (
            "-File scripts\\run_tests.ps1 last, so later arguments reach the script",
            r"\s-File\s+scripts[\\/]{1,2}run_tests\.ps1\s*$",
        ),
    ):
        assert re.search(pattern, command, re.IGNORECASE) is not None, (
            f"the Windows command does not carry {description}: {command!r}"
        )

    assert (
        re.search(r"fileIncludePattern:\s*'target/cucumber\.json'", text) is not None
    ), "the publisher glob is not narrowed to the one report artifact"
    assert "**/*.json" not in text, (
        "the recursive publisher glob would ingest the per-worker intermediates"
    )

    for description, pattern in (
        (
            "the hard-coded reference checkout",
            r"git\s+'https://github\.com/BalamiRR/Upgenix-QA\.git'",
        ),
        ("a Maven command", r"""^\s*(?:sh|bat)\s+"mvn\b"""),
        ("a Maven build reference", r"\bmvn\s+clean\s+test\b"),
    ):
        assert re.search(pattern, text, re.MULTILINE) is None, (
            f"the pipeline still carries {description}, which the port removes"
        )


def test_the_readme_documents_the_python_port(repo_root: Path) -> None:
    """AAP 0.2.1 and 0.5.3: what the README must say, and must no longer say.

    Asserted as contract facts rather than as sentences, so that the
    documentation owner's rewording cannot fail this test while a removed
    guarantee still does: the stack and the one entry point are named;
    ``pom.xml`` is labelled historical reference and explicitly not a supported
    build; the Jenkins section and both ``./image/...`` references survive
    (AAP 0.2.1 keeps them, the binaries being the reference repository's and
    not copied here); no Maven command is offered to a reader; and no
    prerequisite asks for a JDK, Maven or IntelliJ.

    The Maven check is deliberately about *commands* - a line beginning with
    ``mvn`` - rather than about the word: the document has to be able to say
    that there is no Maven build any more.
    """
    text = (repo_root / "README.md").read_text("utf-8")
    lowered = text.lower()

    for tool in ("python", "flask", "behave", "selenium", "pytest"):
        assert tool in lowered, f"the README does not name {tool}"
    assert COMMAND_NAME in text

    pom_paragraph = next(
        (
            paragraph
            for paragraph in re.split(r"\n\s*\n", text)
            if "pom.xml" in paragraph and "historical" in paragraph.lower()
        ),
        None,
    )
    assert pom_paragraph is not None, (
        "the README does not label pom.xml as historical reference"
    )
    pom_wording = pom_paragraph.lower()
    assert "supported build" in pom_wording
    assert any(
        negation in pom_wording
        for negation in ("not a supported", "no longer a supported", "not supported")
    ), f"the pom.xml paragraph does not deny that it is a build: {pom_paragraph!r}"

    assert re.search(r"^#{1,6}\s+.*Jenkins", text, re.MULTILINE) is not None
    assert "./image/Jenkins-Cucumber-Reports.png" in text
    assert "./image/Jira-Test-Exectuion.png" in text

    assert "-Dcucumber.options" not in text
    typed_maven_commands = re.findall(r"^\s*mvn\s+\S", text, re.MULTILINE)
    assert typed_maven_commands == []

    prerequisites = _markdown_section(
        text, re.compile(r"pre-?requisite|installation", re.IGNORECASE)
    )
    assert prerequisites.strip(), (
        "the README has no installation/prerequisites section"
    )
    assert PINNED_PYTHON_VERSION in prerequisites

    items = _numbered_items(prerequisites)
    assert items, "the prerequisites section lists no numbered requirement"
    for item in items:
        for retired in (r"\bjdk\b", r"\bintellij\b", r"\bmaven\b"):
            assert re.search(retired, item, re.IGNORECASE) is None, (
                f"a prerequisite still asks for {retired}: {item!r}"
            )


def test_the_readme_states_the_implemented_screenshot_policy(
    repo_root: Path,
) -> None:
    """AAP 0.4.1: the screenshot sentence is replaced by the actual policy.

    The document this port replaced claimed screenshots for passing tests as
    well, and no setting and no branch ever provided them, so the AAP has the
    README state what the code does instead: capture on failure only, once,
    while the session is live and before the driver is quit, embedded in the
    results artifact under the failed scenario, and with a failed capture
    logged and never changing a test outcome.  The last of those is AAP
    deviation 19, and a reader who believed the old sentence would take a
    missing screenshot for a defect.

    Asserted as the policy's four facts rather than as a sentence, and
    asserted to say nothing about an enable switch, because there is none -
    documenting one would describe a configuration surface the port does not
    have, its six keys being fixed.
    """
    text = (repo_root / "README.md").read_text("utf-8")

    # Scoped to the passage that describes the policy, and deliberately so: the
    # document mentions screenshots in four places, and a claim satisfied by a
    # sentence elsewhere - the lifecycle bullet, say, or the lightbox - would
    # let the policy itself be deleted while this test stayed green.
    paragraphs = re.split(r"\n\s*\n", text)
    policy_indexes = [
        index
        for index, paragraph in enumerate(paragraphs)
        if re.search(r"screen\s?shot", paragraph, re.IGNORECASE)
        and re.search(r"captur", paragraph, re.IGNORECASE)
        and re.search(r"fail", paragraph, re.IGNORECASE)
    ]
    assert policy_indexes, "the README describes no screenshot policy at all"
    # The policy paragraph plus the two that finish it: a sentence ending in
    # "as" introduces the embedding's shape as an indented block, and the
    # sentence about a failed capture follows that block.  A window rather
    # than the whole document, so a claim satisfied elsewhere - the lifecycle
    # bullet, say, or the lightbox - cannot stand in for the policy itself.
    start = policy_indexes[0]
    passage = "\n\n".join(paragraphs[start : start + 3])
    lowered = passage.lower()

    assert re.search(r"fail(?:ed|ing|ure)[^.\n]{0,40}only|only[^.\n]{0,40}fail", lowered), (
        "the screenshot passage does not restrict capture to failures"
    )
    assert re.search(r"before the driver is quit|session is still live", lowered), (
        "the screenshot passage does not say when the capture happens"
    )
    assert "target/cucumber.json" in passage, (
        "the screenshot passage does not say where the PNG is embedded"
    )
    assert '"mime_type": "image/png"' in passage, (
        "the screenshot passage does not show the embedding's shape"
    )
    assert re.search(
        r"(never changes|does not change|never affects|does not affect)[^.\n]*"
        r"(outcome|status)|"
        r"(outcome|status)[^.\n]*(unchanged|left exactly as it was|is not changed)",
        lowered,
    ), "the screenshot passage does not say a failed capture leaves the outcome alone"
    assert re.search(r"logged|stderr", lowered), (
        "the screenshot passage does not say a failed capture is reported"
    )
    lowered = text.lower()

    # No enable flag is documented, because none exists.
    for invented in (
        "screenshot.enabled",
        "screenshots.enabled",
        "enable_screenshots",
        "--screenshots",
    ):
        assert invented not in lowered, f"the README documents {invented!r}"


def test_the_readme_documents_the_command_and_report_contract(
    repo_root: Path,
) -> None:
    """AAP 0.4.1 and 0.5.3: one command with six options, and four artifacts.

    The README is the only place a person is told how to run this project, and
    the AAP fixes exactly what it has to tell them: the single ``run-tests``
    entry point, each of its six options with the default the Java runner's
    annotations fixed, and the four artifact paths with their consumers - the
    publisher reads the results file, ``--rerun`` reads the manifest, and the
    two HTML outputs are read by people.  A missing option or a moved path
    turns the document into instructions that do not work, which no other test
    in this suite would notice.
    """
    text = (repo_root / "README.md").read_text("utf-8")

    for option in (
        "--tags",
        "--browser",
        "--workers",
        "--dry-run",
        "--rerun",
        "--clean",
    ):
        assert option in text, f"the README does not document {option}"

    # The two defaults a reader most needs, and the one usage error.
    assert "@Smoke" in text
    assert re.search(r"--workers[^\n]*CPU", text, re.IGNORECASE) is not None, (
        "the README does not give --workers its CPU-count default"
    )
    assert re.search(
        r"--rerun[^\n]*--tags|--tags[^\n]*--rerun", text
    ), "the README does not state that --rerun and --tags conflict"

    for artifact in (
        "target/cucumber.json",
        "target/cucumber-reports.html",
        "target/rerun.txt",
        "target/cucumber/",
    ):
        assert artifact in text, f"the README does not name {artifact}"

    assert "cucumber-html-reports/overview-features.html" in text, (
        "the README does not name the report tree's entry page"
    )
    assert "target/.workers/" in text, (
        "the README does not mention the per-worker intermediates"
    )
