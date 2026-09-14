"""Tests for the six-key configuration surface, ``app/config.py``.

This module owns the *read surface* of the port of
``com.testinium.utilities.ConfigurationReader``: the six configuration key
names, the six named accessors, the shared ``get_property`` guard in front of
them, and the behave-userdata override precedence layered over the properties
file (AAP 0.4.1, ``app/config.py`` row and the ``--browser`` row).

What it asserts, and why each assertion exists
----------------------------------------------
1. **All and only six keys.**  ``CONFIG_KEYS`` is the comparison point the
   module publishes for exactly this purpose.  AAP 0.6 states twice that "the
   configuration surface stays at six keys" - once for the browser locale the
   ``@UPGN-288`` outline needs and once for screenshots, where "no enable flag
   is added".  The inventory assertions are therefore written so that *adding*
   a seventh key, or an accessor for one, fails here.
2. **Accessor wiring, discriminatingly.**  Every accessor is driven from a
   properties file that defines its key *alone*, so an accessor wired to a
   neighbouring key fails rather than passing on a shared value.
   ``web.table.url`` (the sign-in page, ``LoginSD.java:22``, ``Session.java:14``,
   ``EmployeeStage.java:18``) and ``url`` (the Employee module,
   ``EmployeeStage.java:24``, ``:60``, ``:93``) address different pages and are
   not interchangeable; a swap between the two is the specific mistake these
   tests exist to catch.
3. **Precedence is userdata first, then the file** - and it is decided by
   *membership*, not truthiness, so userdata mapping a key to ``""`` shadows
   the file with ``""`` instead of falling through.
4. **No environment layer and no locale layer.**  AAP 0.4.1's ``--browser``
   row: "This is the only override path; no environment layer is added".  AAP
   0.6 and 0.8 leave the browser locale unresolved and invent no key for it.
   Both absences are asserted positively - behaviourally with
   ``monkeypatch.setenv``, and structurally over the module's own AST.
5. **No validation and no case folding of any value.**  ``Driver.java:29-42``
   switches on the ``browser`` string with cases for ``"chrome"`` and
   ``"firefox"`` and **no default branch**, so an unrecognised value must reach
   the driver and fail at first use (AAP 0.4.1: "Any other value fails at first
   driver use, as today").  This module asserts the configuration half - the
   value is returned untouched; ``tests/test_driver.py`` asserts the driver
   half.
6. **The import boundary.**  AAP 0.4.2: ``app.config`` is the only module
   importing ``app/utils/properties.py``.  The scan is AST-based over real
   import statements rather than a text grep, because the tree carries the
   reader's name in docstring prose in several places and ``app/cli.py:651``
   carries the properties filename in an executable ``show_default`` string -
   a substring grep would report either as a violation.

Isolation this module relies on, and the one it must provide itself
-------------------------------------------------------------------
``tests/conftest.py``'s autouse ``isolate_process_state`` fixture resets
``app/utils/properties.py``'s process-wide cache before *and* after every test,
which is what lets each test below decide, through ``monkeypatch.chdir``, which
properties file - or none - the reader sees.  Every file-based test writes into
``tmp_path``: none writes a ``configuration.properties`` into the repository
root, and none relies on the root lacking one.

That fixture deliberately does **not** reset ``app/config.py``'s installed
userdata, because most tests never touch it.  A leaked userdata slot is
process-global and would silently corrupt every later test in the session, so
this module installs userdata only through the :fixture:`install_userdata`
fixture, whose clearing finalizer is registered *before* anything is
installed - the idiom ``isolate_process_state``'s own docstring prescribes -
so that a failing assertion cannot leak the slot.  The last test in the file is
a canary for exactly that failure.

No configured value is asserted anywhere.  No ``configuration.properties``
exists at either revision, so no real value is known and the committed template
ships all six keys empty (AAP 0.4.1); every value used below is obviously
synthetic and lives only under ``tmp_path``.
"""

from __future__ import annotations

import ast
import json
import logging
import os
import subprocess
import sys
from typing import TYPE_CHECKING, Final, NamedTuple

import pytest

from app import config
from app.utils import properties

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

    from flask import Flask


# --------------------------------------------------------------------------
# The contract under test, restated as data
#
# These constants are written out as literals on purpose.  Deriving them from
# the module under test would make every assertion below a tautology; stating
# them independently is what makes a change to the configuration surface fail
# here, which is the whole point of the inventory tests.
# --------------------------------------------------------------------------

#: The six keys, in the order AAP 0.4.1 tabulates them - which is also the
#: order ``configuration.properties.example`` declares them in.
EXPECTED_KEYS: Final[tuple[str, ...]] = (
    "browser",
    "web.table.url",
    "url",
    "username",
    "password",
    "EmplTitle",
)

#: ``app/config.py``'s published surface: the key tuple, the six named
#: accessors, the shared guarded reader and the userdata installer.  Nothing
#: else, and in particular no seventh accessor.
EXPECTED_PUBLIC_NAMES: Final[tuple[str, ...]] = (
    "CONFIG_KEYS",
    "get_browser",
    "get_empl_title",
    "get_password",
    "get_property",
    "get_url",
    "get_username",
    "get_web_table_url",
    "set_userdata",
)

#: Key -> the accessor that must serve it.  The mapping is the wiring contract:
#: a test that drives one key and reads through this table fails if the
#: accessor is bound to a different key.
ACCESSORS: Final[dict[str, Callable[[], str | None]]] = {
    "browser": config.get_browser,
    "web.table.url": config.get_web_table_url,
    "url": config.get_url,
    "username": config.get_username,
    "password": config.get_password,
    "EmplTitle": config.get_empl_title,
}

#: Six deliberately distinct, obviously synthetic values - one per key - so
#: that an accessor returning a neighbour's value is detectable.  The two URLs
#: use the reserved ``.invalid`` TLD: nothing here can resolve, and no real
#: address or credential of any system under test is invented (AAP 0.8).
SYNTHETIC_VALUES: Final[dict[str, str]] = {
    "browser": "firefox",
    "web.table.url": "http://sign-in.invalid/web/login",
    "url": "http://employee-module.invalid/odoo/employees",
    "username": "synthetic-user-name",
    "password": "synthetic-pass-phrase",
    "EmplTitle": "Synthetic Employee Page Title",
}

#: Names that must NOT be configuration keys.  Each is a setting the port was
#: never asked for: a browser locale or region (AAP 0.6 and 0.8 leave the
#: ``@UPGN-288`` French message unresolved and "add no locale key"), a headless
#: switch, a timeout (every wait carries its own per-call-site timeout, AAP
#: 0.4.1), a screenshot enable flag (AAP 0.6: "No enable flag is added"), and a
#: base URL (the two URL keys are absolute and separate).  The last two are
#: command-line options - ``--workers`` and ``--tags`` (AAP 0.4.1's CLI
#: table) - which belong to ``app/cli.py`` and must not acquire a second home
#: in the properties file.
FORBIDDEN_KEYS: Final[tuple[str, ...]] = (
    "locale",
    "language",
    "lang",
    "browser.locale",
    "browser.language",
    "region",
    "headless",
    "browser.headless",
    "timeout",
    "implicit.wait",
    "explicit.wait",
    "screenshot",
    "screenshots",
    "screenshot.on.failure",
    "base.url",
    "baseUrl",
    "base_url",
    "workers",
    "tags",
)

#: Substrings that may not appear in a configuration key name or in a public
#: name of the module, for the same reasons ``FORBIDDEN_KEYS`` lists.
UNPORTED_SETTING_TOKENS: Final[tuple[str, ...]] = (
    "locale",
    "language",
    "lang",
    "region",
    "headless",
    "timeout",
    "screenshot",
    "base_url",
    "baseurl",
    "environ",
)

#: Environment variables a reader might *expect* to override configuration,
#: paired with the key each would plausibly target.  None of them has any
#: effect: AAP 0.4.1 makes behave userdata the only override path.
ENV_OVERRIDE_CANDIDATES: Final[tuple[tuple[str, str], ...]] = (
    ("BROWSER", "browser"),
    ("browser", "browser"),
    ("WEB_TABLE_URL", "web.table.url"),
    ("URL", "url"),
    ("USERNAME", "username"),
    ("PASSWORD", "password"),
    ("EMPL_TITLE", "EmplTitle"),
    ("EMPLTITLE", "EmplTitle"),
)

#: A value planted in the environment by the tests above.  Distinct from every
#: synthetic file and userdata value, so finding it anywhere proves an
#: environment layer exists.
ENV_SENTINEL: Final[str] = "value-from-the-process-environment-must-be-ignored"

#: The module names of the two production modules this suite reasons about,
#: taken from the modules themselves so that neither name is spelled twice.
CONFIG_MODULE: Final[str] = config.__name__
READER_MODULE: Final[str] = properties.__name__

#: The package that owns the reader.  Its own members may import it; AAP 0.4.2
#: constrains every module *outside* it.
READER_PACKAGE: Final[str] = READER_MODULE.rsplit(".", 1)[0]

#: The reader's public names, as re-exported by the ``app.utils`` barrel.
#: Importing one of these *through the barrel* would reach the properties file
#: without going through ``app/config.py``, which AAP 0.4.2 forbids just as
#: plainly as importing the module itself.
READER_PUBLIC_NAMES: Final[frozenset[str]] = frozenset(properties.__all__)

#: Third-party and sibling-package roots ``app/config.py`` must never import.
#: ``app.automation`` depends on *this* module for the ``browser`` key, so
#: importing it back would create a cycle.
FORBIDDEN_CONFIG_IMPORT_ROOTS: Final[tuple[str, ...]] = (
    "flask",
    "jinja2",
    "click",
    "behave",
    "selenium",
    "webdriver_manager",
    "app.services",
    "app.reporting",
    "app.pages",
    "app.web",
    "app.automation",
    "app.cli",
    "app.errors",
    "app.logging_config",
)

#: Standard-library modules that expose the process environment, plus the
#: dotenv loader.  ``app/config.py`` imports none of them: it cannot consult
#: the environment because it has no way to reach it.
ENVIRONMENT_BEARING_MODULES: Final[tuple[str, ...]] = ("os", "posix", "nt", "dotenv")

#: Attribute and bare names that would betray an environment read.
ENVIRONMENT_ACCESS_NAMES: Final[tuple[str, ...]] = (
    "environ",
    "getenv",
    "putenv",
    "environb",
    "get_environ",
)

#: Ceiling on the two fresh-interpreter probes.  Each imports one module and
#: prints a JSON line; the bound exists so that a hung child fails the test
#: instead of the session.
SUBPROCESS_TIMEOUT_SECONDS: Final[float] = 120.0


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _write_properties(directory: Path, values: Mapping[str, str]) -> Path:
    """Write ``values`` as a properties file in ``directory`` and return it.

    The filename and the encoding come from ``app/utils/properties.py``, the
    single owner of both (AAP 0.4.2), rather than being spelled again here.
    ISO-8859-1 is the encoding ``java.util.Properties.load(InputStream)``
    applies on the Java 8 level ``pom.xml:12-13`` pins.

    Keys are written in ``EXPECTED_KEYS`` order where they are among the six,
    so a file this helper produces reads like the committed template; any other
    key is appended afterwards, which is what lets a test write a deliberately
    mis-cased name such as ``empltitle``.

    :param directory: Directory to write into - always a ``tmp_path``, never
        the repository root.
    :param values: Key/value pairs to write, used verbatim.
    :returns: The path written.
    """
    ordered = [key for key in EXPECTED_KEYS if key in values]
    ordered += [key for key in values if key not in EXPECTED_KEYS]

    path = directory / properties.PROPERTIES_FILENAME
    body = "".join(f"{key}={values[key]}\n" for key in ordered)
    path.write_text(body, encoding=properties.DEFAULT_ENCODING)
    return path


def _use_directory(
    monkeypatch: pytest.MonkeyPatch,
    directory: Path,
    values: Mapping[str, str] | None = None,
) -> Path | None:
    """Make ``directory`` the working directory, optionally with a config file.

    The reader resolves the bare properties filename against the working
    directory **at first read**, so the order here matters: the file is created
    and the working directory changed before any accessor is called.  The
    autouse ``isolate_process_state`` fixture has already cleared the cache, so
    the first accessor call in the test performs the load.

    :param monkeypatch: pytest's patcher, whose ``chdir`` is undone at teardown.
    :param directory: The directory to run in - a ``tmp_path``.
    :param values: Properties to write, or ``None`` to leave the directory
        without a properties file at all.
    :returns: The properties file written, or ``None`` when ``values`` is
        ``None``.
    """
    written = _write_properties(directory, values) if values is not None else None
    monkeypatch.chdir(directory)
    return written


class _ImportSurvey(NamedTuple):
    """Every import one module performs, resolved to absolute dotted names.

    :param modules: The modules imported - ``import x.y`` and the *base* of
        ``from x.y import z``, with relative bases resolved against the
        importing module's package.
    :param targets: ``base.name`` for every ``from base import name`` member,
        which is what makes ``from app.utils import properties`` recognisable
        as an import of ``app.utils.properties``.
    :param members: ``(base, name)`` pairs, for asking which *names* a module
        pulled out of a package.
    """

    modules: frozenset[str]
    targets: frozenset[str]
    members: tuple[tuple[str, str], ...]


def _module_and_package(relpath: Path) -> tuple[str, str]:
    """Return the dotted module name and package of a repository-relative path.

    ``app/config.py`` -> ``("app.config", "app")`` and
    ``app/utils/__init__.py`` -> ``("app.utils", "app.utils")``, which is what
    relative-import resolution needs.

    :param relpath: Path of a ``.py`` file relative to the repository root.
    :returns: The module name and the package it lives in.
    """
    parts = list(relpath.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
        return ".".join(parts), ".".join(parts)
    return ".".join(parts), ".".join(parts[:-1])


def _survey_imports(path: Path, package: str) -> _ImportSurvey:
    """Parse ``path`` and return every import statement it contains.

    AST-based, and deliberately so.  A text search for the reader's name
    reports the several docstrings in the tree that discuss it, and a text
    search for the properties filename reports ``app/cli.py:651``, where it is
    a Click ``show_default`` string rather than an import.  Only the import
    statements answer the question AAP 0.4.2 asks.

    ``ast.walk`` rather than a scan of the module body, because the port defers
    imports into function bodies where it must - ``app/__init__.py``'s factory
    imports Flask inside ``create_app()`` - and a deferred import of the reader
    would evade a top-level-only scan.

    :param path: The file to parse.
    :param package: The package the module lives in, for resolving relative
        imports.
    :returns: The module's import surface.
    :raises SyntaxError: If the file does not parse, which is a real failure
        rather than something to swallow: the suite runs against source that
        must compile.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package_parts = package.split(".") if package else []

    modules: set[str] = set()
    targets: set[str] = set()
    members: list[tuple[str, str]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                base = node.module or ""
            else:
                # ``from . import x`` is level 1 and resolves to the package
                # itself; each further dot climbs one level.
                anchor = package_parts[: len(package_parts) - (node.level - 1)]
                base = ".".join([*anchor, node.module] if node.module else anchor)
            if base:
                modules.add(base)
            for alias in node.names:
                targets.add(f"{base}.{alias.name}" if base else alias.name)
                members.append((base, alias.name))

    return _ImportSurvey(frozenset(modules), frozenset(targets), tuple(members))


def _production_modules(repo_root: Path) -> list[tuple[str, Path, str]]:
    """Return every production Python module in the tree, as name/path/package.

    Covers ``app/`` - the application package - and ``features/`` - the behave
    environment hook and the ten step modules, which are production glue rather
    than tests.  ``tests/`` is excluded: this suite imports the reader itself,
    through its documented test-support entry point, and is not bound by an
    invariant about production import paths.

    A directory that does not exist contributes nothing rather than failing,
    so the scan degrades to "fewer modules" instead of an error; the callers
    assert the census is non-empty and contains ``app/config.py``, which is what
    stops a scan over nothing from passing vacuously.

    :param repo_root: The repository root.
    :returns: ``(module name, path, package)`` for each module found, sorted by
        path so that a failure message lists offenders deterministically.
    """
    found: list[tuple[str, Path, str]] = []
    for directory in ("app", "features"):
        for path in sorted((repo_root / directory).glob("**/*.py")):
            module, package = _module_and_package(path.relative_to(repo_root))
            found.append((module, path, package))
    return found


def _run_fresh_interpreter(
    script: str,
    *,
    cwd: Path,
    repo_root: Path,
) -> subprocess.CompletedProcess[str]:
    """Run ``script`` in a brand-new interpreter and return the finished process.

    Two contracts can only be proved in a process that has not already been
    through this test session: that every accessor works with
    ``set_userdata`` never having been called, and that importing
    ``app.config`` pulls in no heavyweight third-party module.  Both are
    properties of a *fresh* process, so both get one.

    The environment is inherited so that the child finds the same interpreter
    and packages, with two changes: the repository root is prepended to
    ``PYTHONPATH``, so the child works whether or not the editable install is
    present, and every environment variable named in
    ``ENV_OVERRIDE_CANDIDATES`` is removed, so a value left in the ambient
    environment cannot influence a child that is asserting values.

    :param script: Python source for ``-c``.
    :param cwd: Working directory for the child - the directory whose
        properties file, or lack of one, the child must see.
    :param repo_root: The repository root, prepended to ``PYTHONPATH``.
    :returns: The completed process, with text streams captured.
    :raises subprocess.TimeoutExpired: If the child outlives
        ``SUBPROCESS_TIMEOUT_SECONDS``, which fails the test rather than
        hanging the session.
    """
    env = dict(os.environ)
    for name, _key in ENV_OVERRIDE_CANDIDATES:
        env.pop(name, None)

    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{repo_root}{os.pathsep}{existing}" if existing else str(repo_root)
    )

    return subprocess.run(  # noqa: S603 - fixed argv, interpreter is sys.executable
        [sys.executable, "-c", script],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
        check=False,
    )


class _RecordingHandler(logging.Handler):
    """A handler that keeps every record it is given, for later assertions."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        """Append ``record`` to :attr:`records`.

        :param record: The record emitted by the logger under observation.
        """
        self.records.append(record)


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def install_userdata(
    request: pytest.FixtureRequest,
) -> Callable[[Mapping[str, str] | None], None]:
    """Install behave userdata for one test, cleared however the test ends.

    ``tests/conftest.py``'s autouse ``isolate_process_state`` fixture
    deliberately leaves ``app/config.py``'s userdata slot alone, and its
    docstring prescribes the per-test idiom this fixture packages: register the
    clearing finalizer **before** installing anything, so that a failing
    assertion - or an error raised between the install and the end of the
    test - cannot leave the slot populated for the rest of the session.  The
    slot is process-global, so a leak would silently change what every later
    test in the run reads, including tests in other modules.

    ``app/config.py`` documents ``None`` as clearing the slot and restoring the
    file-only path, so that single call is the whole of the cleanup required.

    :param request: pytest's request object, used only for ``addfinalizer``.
    :returns: A callable that installs a userdata mapping, which may be called
        more than once in a test to replace the installed mapping.
    """
    # Registered first, and therefore run last, whatever the test does next.
    request.addfinalizer(lambda: config.set_userdata(None))

    def install(mapping: Mapping[str, str] | None) -> None:
        config.set_userdata(mapping)

    return install


@pytest.fixture
def log_capture(
    request: pytest.FixtureRequest,
) -> Callable[[str], list[logging.LogRecord]]:
    """Capture the records a named logger emits, by attaching to it directly.

    Attaching a handler to the logger itself rather than using ``caplog``,
    because ``app/logging_config.py``'s ``configure_logging()`` - called by
    ``create_app()`` and by the ``run-tests`` command - sets
    ``propagate = False`` on the ``app`` logger and pins its level to ``INFO``.
    Any test relying on records reaching the root logger would therefore pass
    or fail according to whether some earlier test in the session happened to
    build an application, which is exactly the kind of order dependence a
    process-global holder introduces.  A handler on the named logger, with that
    logger's level lowered to ``DEBUG`` and restored at teardown, is immune to
    both.

    :param request: pytest's request object, used only for ``addfinalizer``.
    :returns: A callable taking a logger name and returning the live list of
        records that logger emits from that moment on.
    """

    def capture(logger_name: str) -> list[logging.LogRecord]:
        logger = logging.getLogger(logger_name)
        handler = _RecordingHandler()
        previous_level = logger.level

        def restore() -> None:
            logger.removeHandler(handler)
            logger.setLevel(previous_level)

        request.addfinalizer(restore)
        logger.addHandler(handler)
        # Set on this logger, so the effective level is DEBUG regardless of
        # what an ancestor was left at.
        logger.setLevel(logging.DEBUG)
        return handler.records

    return capture


# ==========================================================================
# Phase 1 - the six-key inventory and the published surface
# ==========================================================================


def test_config_keys_is_exactly_the_six_keys_in_aap_order() -> None:
    """``CONFIG_KEYS`` holds the six keys of AAP 0.4.1, in its order.

    The six and their read sites in ``ConfigurationReader``'s callers:
    ``browser`` (``Driver.java:27``), ``web.table.url`` (``LoginSD.java:22``,
    ``Session.java:14``, ``EmployeeStage.java:18``), ``url``
    (``EmployeeStage.java:24``, ``:60``, ``:93``), ``username``
    (``Session.java:15``), ``password`` (``Session.java:16``) and ``EmplTitle``
    (``EmployeeStage.java:31``) - ten read sites across six keys.

    Order is asserted, not just membership: ``app/config.py`` documents the
    tuple as comparable element by element with the declaration order of
    ``configuration.properties.example``.
    """
    assert config.CONFIG_KEYS == EXPECTED_KEYS


def test_config_keys_is_a_six_entry_tuple_without_duplicates() -> None:
    """The key inventory is a fixed-length tuple of six distinct names.

    A seventh entry - or a duplicate masking one - fails here.  AAP 0.6 states
    the surface "stays at six keys" for both of the settings a reader might
    expect to find: a browser locale and a screenshot enable flag.
    """
    assert isinstance(config.CONFIG_KEYS, tuple)
    assert len(config.CONFIG_KEYS) == 6
    assert len(set(config.CONFIG_KEYS)) == 6


def test_public_surface_is_the_six_accessors_the_guard_and_the_installer() -> None:
    """``__all__`` is the six accessors, ``get_property``, ``set_userdata``, keys.

    Asserted as an equality rather than a containment, so that an eighth
    function - an accessor for a seventh key, a validator, a loader - fails
    here rather than arriving unnoticed.
    """
    assert tuple(config.__all__) == EXPECTED_PUBLIC_NAMES
    for name in EXPECTED_PUBLIC_NAMES:
        assert hasattr(config, name), f"{name} is exported but not defined"
        if name != "CONFIG_KEYS":
            assert callable(getattr(config, name)), f"{name} is not callable"


def test_every_key_has_exactly_one_accessor() -> None:
    """The accessor table covers all six keys and invents none.

    Guards this suite's own wiring table as much as the module's: a key
    without an accessor, or an accessor for something that is not a key, makes
    every wiring test below meaningless.
    """
    assert tuple(ACCESSORS) == EXPECTED_KEYS
    assert len(set(ACCESSORS.values())) == 6


@pytest.mark.parametrize("key", FORBIDDEN_KEYS)
def test_get_property_rejects_a_seventh_key(key: str) -> None:
    """No name outside the six is readable - locale, headless, timeout, and more.

    Each name in ``FORBIDDEN_KEYS`` is a setting the port was never asked for.
    AAP 0.6 rules out two of them explicitly: no locale key, because the
    ``@UPGN-288`` French validation message's mechanism is unknown and AAP 0.8
    carries the string verbatim instead of inventing configuration for it; and
    no screenshot enable flag, because "no setting and no branch in ``Hooks``
    provides it".  The timeouts are per call site (AAP 0.4.1) and the two URL
    keys are absolute, so neither a timeout nor a base URL has anything to
    configure.

    Written so that adding an accessor for any of these fails here first.
    """
    with pytest.raises(ValueError, match="is not a configuration key"):
        config.get_property(key)


@pytest.mark.parametrize("token", UNPORTED_SETTING_TOKENS)
def test_no_configuration_key_names_an_unported_setting(token: str) -> None:
    """No key name mentions a locale, a headless mode, a timeout or a screenshot.

    The complement of the previous test: that one proves such a key cannot be
    *read*, this one proves none was *declared*.  Together they close the
    surface at six.
    """
    offenders = [key for key in config.CONFIG_KEYS if token in key.lower()]
    assert offenders == [], f"CONFIG_KEYS names {token!r} in {offenders}"


@pytest.mark.parametrize("token", UNPORTED_SETTING_TOKENS)
def test_module_exposes_no_accessor_for_an_unported_setting(token: str) -> None:
    """The module defines no public name for a setting the port does not have.

    Scans every public attribute, not just ``__all__``, so a helper that was
    defined but not exported is caught too.  ``environ`` is among the tokens:
    a name mentioning it would mean an environment layer had appeared, which
    AAP 0.4.1 rules out.
    """
    offenders = [
        name
        for name in dir(config)
        if not name.startswith("_") and token in name.lower()
    ]
    assert offenders == [], f"app/config.py exposes {offenders} for {token!r}"


def test_config_keys_matches_the_committed_template(repo_root: Path) -> None:
    """``configuration.properties.example`` declares the six keys, in order, empty.

    ``app/config.py`` publishes ``CONFIG_KEYS`` precisely so the template and
    the tuple are compared against each other rather than against a list
    hard-coded twice.  AAP 0.4.1 requires the template to carry "the six keys
    with empty values and a comment each; no values invented, since no
    ``configuration.properties`` exists at either revision".

    The set and the values are checked with the port's own grammar
    implementation; the declaration *order* is read off the file's own lines,
    which the template keeps in one simple ``key=`` form.
    """
    template = repo_root / f"{properties.PROPERTIES_FILENAME}.example"
    assert template.is_file(), f"{template} is missing"

    text = template.read_text(encoding=properties.DEFAULT_ENCODING)
    parsed = properties.parse_properties(text)
    assert set(parsed) == set(EXPECTED_KEYS)
    assert all(value == "" for value in parsed.values()), (
        "the committed template must invent no configuration value"
    )

    declared = [
        line.split("=", 1)[0].strip()
        for line in (raw.strip() for raw in text.splitlines())
        if line and not line.startswith(("#", "!")) and "=" in line
    ]
    assert tuple(declared) == EXPECTED_KEYS


# ==========================================================================
# Phase 2 - the guard, and the missing-value contract
# ==========================================================================


@pytest.mark.parametrize("key", EXPECTED_KEYS)
def test_unconfigured_key_reads_as_none_and_never_raises(
    key: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A key among the six that is not configured returns ``None``.

    ``Properties.getProperty`` returns ``null`` for a name the file does not
    define (``ConfigurationReader:27-29``), so a configuration problem surfaces
    at the point of use rather than at start-up.  Note the asymmetry with the
    previous phase, which is the whole design of the guard: a name outside the
    six raises, because it can never be satisfied; a name among the six that is
    simply unset does not.
    """
    _use_directory(monkeypatch, tmp_path)

    assert config.get_property(key) is None
    assert ACCESSORS[key]() is None


def test_every_accessor_tolerates_a_missing_properties_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no properties file at all, all six accessors return ``None`` quietly.

    ``ConfigurationReader:21-24`` catches the missing file, logs and continues,
    and AAP 0.1.1 records that "that tolerance is behaviour".  Nothing raises
    and nothing exits, which is what lets the unit suite, the viewer, the
    artifact writers and dry runs all work without the file (AAP 0.4.1: "None
    is required at startup").
    """
    _use_directory(monkeypatch, tmp_path)
    assert not (tmp_path / properties.PROPERTIES_FILENAME).exists()

    assert [accessor() for accessor in ACCESSORS.values()] == [None] * 6


def test_get_property_error_names_the_offending_key_and_the_six() -> None:
    """The rejection is a loud programming error that says what went wrong.

    A caller reaching for a name outside the six has made a mistake in code,
    not in configuration, so the message carries the offending name and the
    names that would have been accepted.
    """
    with pytest.raises(ValueError) as excinfo:
        config.get_property("locale")

    message = str(excinfo.value)
    assert "'locale'" in message
    for key in EXPECTED_KEYS:
        assert key in message, f"the message does not name {key}"


@pytest.mark.parametrize(
    "variant",
    [
        "Browser",
        "BROWSER",
        "Url",
        "URL",
        "Web.Table.Url",
        "WEB.TABLE.URL",
        "empltitle",
        "EMPLTITLE",
        "Username",
        "Password",
    ],
)
def test_the_key_guard_is_case_sensitive(variant: str) -> None:
    """Key names are matched exactly: no case folding, in either direction.

    ``java.util.Properties`` is a ``Hashtable`` lookup, so the Java original
    matches byte for byte.  ``EmplTitle`` is mixed case and ``web.table.url``
    is dotted; a port that lower-cased names would silently accept
    ``EMPLTITLE`` and diverge from ``EmployeeStage.java:31``.
    """
    assert variant not in EXPECTED_KEYS
    with pytest.raises(ValueError, match="is not a configuration key"):
        config.get_property(variant)


@pytest.mark.parametrize(
    "key",
    [pytest.param(None, id="none"), pytest.param(42, id="int")],
)
def test_get_property_rejects_a_key_that_is_not_one_of_the_six(key: object) -> None:
    """A non-string key is rejected by the same guard, with the same error.

    The guard tests membership of the six literal names, so anything that is
    not one of them - including a caller that passed ``None`` because an
    earlier lookup failed - raises ``ValueError`` rather than reaching the
    reader and returning a misleading ``None``.
    """
    with pytest.raises(ValueError, match="is not a configuration key"):
        config.get_property(key)  # type: ignore[arg-type]


def test_the_missing_file_warning_belongs_to_the_reader_not_to_this_module(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    log_capture: Callable[[str], list[logging.LogRecord]],
) -> None:
    """``app/config.py`` logs nothing while reading; the reader logs the warning.

    The responsibility split of AAP 0.4.2: the file, its one-time load and its
    single missing-file warning belong to ``app/utils/properties.py``, whose
    message is ``ConfigurationReader:22``'s verbatim.  ``app/config.py`` owns
    the key names and the precedence and performs no I/O, so reading through it
    must produce no record of its own - not even when every one of the six
    reads comes back empty.
    """
    config_records = log_capture(CONFIG_MODULE)
    reader_records = log_capture(READER_MODULE)
    _use_directory(monkeypatch, tmp_path)

    for accessor in ACCESSORS.values():
        assert accessor() is None

    assert config_records == [], "app/config.py emitted a record while reading"

    warnings = [
        record for record in reader_records if record.levelno >= logging.WARNING
    ]
    assert len(warnings) == 1, "the missing file must be reported exactly once"
    assert warnings[0].getMessage() == properties.MISSING_FILE_MESSAGE
    assert warnings[0].name == READER_MODULE


def test_a_key_present_with_no_value_reads_as_empty_string(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``EmplTitle=`` yields ``""``; only an absent key yields ``None``.

    The absent-versus-empty distinction ``Properties`` itself draws, preserved
    end to end: a template copied but left unfilled therefore reads as six
    empty strings rather than as six missing keys, and the failure lands at the
    point of use with the value the file actually contains.
    """
    _use_directory(monkeypatch, tmp_path, {"EmplTitle": "", "browser": ""})

    assert config.get_empl_title() == ""
    assert config.get_browser() == ""
    # Not written at all, and therefore genuinely absent.
    assert config.get_username() is None


# ==========================================================================
# Phase 3 - behave-userdata precedence and the slot's semantics
# ==========================================================================

#: The full precedence matrix of AAP 0.4.1's ``--browser`` row: "userdata
#: first, then the properties file".  ``None`` means "not supplied by this
#: layer" - not "supplied as empty", which is the case immediately below.
PRECEDENCE_MATRIX: Final[tuple[tuple[str | None, str | None, str | None], ...]] = (
    ("value-from-userdata", "value-from-file", "value-from-userdata"),
    ("value-from-userdata", None, "value-from-userdata"),
    (None, "value-from-file", "value-from-file"),
    (None, None, None),
)


@pytest.mark.parametrize(
    ("userdata_value", "file_value", "expected"),
    PRECEDENCE_MATRIX,
    ids=["userdata-and-file", "userdata-only", "file-only", "neither"],
)
@pytest.mark.parametrize("key", ["browser", "username"])
def test_userdata_precedes_the_properties_file(
    key: str,
    userdata_value: str | None,
    file_value: str | None,
    expected: str | None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    install_userdata: Callable[[Mapping[str, str] | None], None],
) -> None:
    """Userdata wins where present, the file serves otherwise, else ``None``.

    The precedence AAP 0.4.1 fixes for ``--browser``: the chosen browser is
    "passed to each worker as behave userdata (``-D browser=...``) and read by
    ``app/config.py``, whose precedence is userdata first, then the properties
    file".

    Run for a second key as well as ``browser``, because the mechanism belongs
    to ``get_property`` and must not be special-cased to the one key the CLI
    happens to override.  Both the shared reader and the named accessor are
    asserted, so a precedence that worked through only one of them fails.
    """
    _use_directory(
        monkeypatch,
        tmp_path,
        {key: file_value} if file_value is not None else None,
    )
    if userdata_value is not None:
        install_userdata({key: userdata_value})

    assert config.get_property(key) == expected
    assert ACCESSORS[key]() == expected


@pytest.mark.parametrize("key", ["browser", "EmplTitle"])
def test_userdata_membership_not_truthiness_decides_precedence(
    key: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    install_userdata: Callable[[Mapping[str, str] | None], None],
) -> None:
    """Userdata mapping a key to ``""`` shadows the file with ``""``.

    The implementation asks ``if key in userdata``, not ``if userdata.get(key)``
    - the distinction ``app/config.py`` documents explicitly and the one most
    easily broken by a well-meaning simplification.  A caller that means "no
    override" must omit the key rather than pass an empty value, and this test
    is what keeps that contract honest.
    """
    _use_directory(monkeypatch, tmp_path, {key: SYNTHETIC_VALUES[key]})
    install_userdata({key: ""})

    assert config.get_property(key) == ""
    assert ACCESSORS[key]() == ""


def test_userdata_leaves_keys_it_does_not_mention_to_the_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    install_userdata: Callable[[Mapping[str, str] | None], None],
) -> None:
    """Overriding ``browser`` does not disturb the other five keys.

    The shape of a real run: ``app/cli.py``'s ``--browser`` reaches each worker
    as ``-D browser=...`` and nothing else is overridden, so the two URLs, the
    credentials and the expected title must still come from the file.
    """
    _use_directory(monkeypatch, tmp_path, SYNTHETIC_VALUES)
    install_userdata({"browser": "chrome"})

    assert config.get_browser() == "chrome"
    for key, accessor in ACCESSORS.items():
        if key != "browser":
            assert accessor() == SYNTHETIC_VALUES[key]


def test_set_userdata_snapshots_the_caller_mapping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    install_userdata: Callable[[Mapping[str, str] | None], None],
) -> None:
    """A mutation of the caller's mapping after installation changes nothing.

    ``features/environment.py``'s ``before_all`` hook hands over
    ``context.config.userdata``, which is a live ``dict`` subclass behave
    continues to own.  ``set_userdata`` therefore copies and wraps it, so
    neither a later mutation by the engine nor a caller keeping a reference can
    silently change what the module reports mid-run.
    """
    _use_directory(monkeypatch, tmp_path, {"browser": SYNTHETIC_VALUES["browser"]})
    live: dict[str, str] = {"browser": "chrome"}
    install_userdata(live)

    live["browser"] = "safari"
    live["username"] = "injected-after-installation"

    assert config.get_browser() == "chrome"
    # The added key never entered the slot, so this one still comes from the
    # file - which in this test does not define it.
    assert config.get_username() is None


def test_set_userdata_accepts_the_mapping_type_behave_supplies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    install_userdata: Callable[[Mapping[str, str] | None], None],
) -> None:
    """Any ``Mapping`` is accepted, including behave's ``dict`` subclass.

    behave's userdata is not a plain ``dict``; the parameter is typed
    ``Mapping[str, str]`` and the implementation copies through ``dict()``, so a
    subclass carrying extra behaviour is snapshotted like any other mapping.
    """

    class UserdataLike(dict):  # type: ignore[type-arg]
        """Stand-in for behave's ``dict`` subclass."""

    _use_directory(monkeypatch, tmp_path)
    install_userdata(UserdataLike({"browser": "firefox", "username": "u"}))

    assert config.get_browser() == "firefox"
    assert config.get_username() == "u"


@pytest.mark.parametrize(
    "cleared",
    [pytest.param(None, id="none"), pytest.param({}, id="empty-mapping")],
)
def test_clearing_userdata_restores_the_file_only_path(
    cleared: Mapping[str, str] | None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    install_userdata: Callable[[Mapping[str, str] | None], None],
) -> None:
    """``None`` and an empty mapping both clear the slot, and reads fall back.

    Clearing must restore access to *every* file value, not merely stop the
    override: this is the operation the suite's own cleanup relies on, so a
    "clear" that left the reader unable to see the file would quietly break
    every later test.
    """
    _use_directory(monkeypatch, tmp_path, SYNTHETIC_VALUES)
    install_userdata({"browser": "chrome", "username": "override"})
    assert config.get_browser() == "chrome"

    install_userdata(cleared)

    for key, accessor in ACCESSORS.items():
        assert accessor() == SYNTHETIC_VALUES[key]


def test_userdata_keys_outside_the_six_stay_unreachable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    install_userdata: Callable[[Mapping[str, str] | None], None],
) -> None:
    """Userdata is not a back door for a seventh configuration key.

    A run may legitimately carry other ``-D name=value`` pairs - behave's own
    userdata is a general mechanism - so the extra entries are stored but
    unreachable: ``get_property`` serves the six and rejects everything else,
    whatever the slot happens to contain.
    """
    _use_directory(monkeypatch, tmp_path, {"browser": SYNTHETIC_VALUES["browser"]})
    install_userdata({"locale": "fr", "headless": "true", "browser": "chrome"})

    with pytest.raises(ValueError, match="is not a configuration key"):
        config.get_property("locale")
    with pytest.raises(ValueError, match="is not a configuration key"):
        config.get_property("headless")
    assert config.get_browser() == "chrome"


def test_accessors_read_the_file_with_set_userdata_never_called(
    tmp_path: Path,
    repo_root: Path,
) -> None:
    """In a fresh process every accessor works without the slot being installed.

    ``app/config.py`` states that calling ``set_userdata`` is never required:
    the slot starts empty, which is what lets ``app/automation/driver.py`` -
    which has no behave context in sight - read ``browser``, and what lets a
    unit test import an accessor directly.

    Proved in a new interpreter rather than in this one, because "never
    called" is a property of a whole process and cannot be observed in a
    session where other tests install userdata deliberately.
    """
    _write_properties(tmp_path, SYNTHETIC_VALUES)

    # The proof is what the script does *not* contain: no set_userdata call.
    script = (
        "import json\n"
        "from app import config\n"
        "print(json.dumps({key: config.get_property(key)"
        " for key in config.CONFIG_KEYS}))\n"
    )
    completed = _run_fresh_interpreter(script, cwd=tmp_path, repo_root=repo_root)

    assert completed.returncode == 0, (
        f"fresh interpreter failed: {completed.stderr}"
    )
    assert json.loads(completed.stdout) == dict(SYNTHETIC_VALUES)


# ==========================================================================
# Phase 4 - accessor wiring against a properties file
# ==========================================================================


def test_each_accessor_returns_its_own_value(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Six distinct values in the file reach six distinct accessors.

    The values are distinct precisely so that a crossed pair of accessors
    cannot pass: with one shared value the test would be blind to exactly the
    defect it is here to find.
    """
    _use_directory(monkeypatch, tmp_path, SYNTHETIC_VALUES)

    assert {key: accessor() for key, accessor in ACCESSORS.items()} == dict(
        SYNTHETIC_VALUES
    )


@pytest.mark.parametrize("key", EXPECTED_KEYS)
def test_accessor_is_wired_to_its_own_key_alone(
    key: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With one key in the file, only its accessor sees a value.

    The strongest available wiring assertion: the file defines a single key, so
    an accessor reading the wrong name returns ``None`` and fails here, and an
    accessor reading *several* names is caught by the other five all having to
    be empty.
    """
    _use_directory(monkeypatch, tmp_path, {key: SYNTHETIC_VALUES[key]})

    for candidate, accessor in ACCESSORS.items():
        expected = SYNTHETIC_VALUES[key] if candidate == key else None
        assert accessor() == expected, f"{candidate} accessor is mis-wired"


def test_the_two_url_keys_are_not_interchangeable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``web.table.url`` is the sign-in page; ``url`` is the Employee module.

    Two separate keys addressing two different pages: ``web.table.url`` is read
    by the login steps, by the shared precondition other features' backgrounds
    invoke and by the opening step of the Employee flow
    (``LoginSD.java:22``, ``Session.java:14``, ``EmployeeStage.java:18``), while
    ``url`` is navigated to three times inside the Employee flow alone
    (``EmployeeStage.java:24``, ``:60``, ``:93``).  A swap between them would
    leave every scenario plausibly green on the wrong page, which is why the
    assertion is stated in both directions.
    """
    _use_directory(monkeypatch, tmp_path, SYNTHETIC_VALUES)

    assert config.get_web_table_url() == SYNTHETIC_VALUES["web.table.url"]
    assert config.get_url() == SYNTHETIC_VALUES["url"]
    assert config.get_web_table_url() != config.get_url()
    assert config.get_web_table_url() != SYNTHETIC_VALUES["url"]
    assert config.get_url() != SYNTHETIC_VALUES["web.table.url"]


@pytest.mark.parametrize(
    ("written_key", "found"),
    [
        pytest.param("EmplTitle", True, id="exact"),
        pytest.param("empltitle", False, id="lower-case"),
        pytest.param("EMPLTITLE", False, id="upper-case"),
        pytest.param("Empltitle", False, id="capitalised"),
    ],
)
def test_empl_title_is_matched_case_sensitively(
    written_key: str,
    found: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only ``EmplTitle`` satisfies :func:`app.config.get_empl_title`.

    Asserted in both directions - the exact name is found, every re-cased
    variant is not - because a case-folding reader would pass a
    membership-only test while accepting files the JVM would ignore
    (``EmployeeStage.java:31`` reads the mixed-case name).
    """
    value = SYNTHETIC_VALUES["EmplTitle"]
    _use_directory(monkeypatch, tmp_path, {written_key: value})

    assert config.get_empl_title() == (value if found else None)


def test_the_dotted_key_reaches_its_accessor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``web.table.url`` survives the grammar with its dots intact.

    The dotted name is one reason ``configparser`` is unusable for this file
    and ``app/utils/properties.py`` implements the ``java.util`` grammar by
    hand: a reader that treated the first dotted segment as a section, or
    flattened the name, would break this accessor and nothing else.
    """
    _use_directory(
        monkeypatch,
        tmp_path,
        {"web.table.url": SYNTHETIC_VALUES["web.table.url"]},
    )

    assert config.get_property("web.table.url") == SYNTHETIC_VALUES["web.table.url"]
    assert config.get_web_table_url() == SYNTHETIC_VALUES["web.table.url"]


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("safari", id="unrecognised-browser"),
        pytest.param("Chrome", id="mixed-case"),
        pytest.param("CHROME", id="upper-case"),
        pytest.param("chromium", id="another-browser"),
        pytest.param("", id="empty"),
        pytest.param("chrome ", id="trailing-space"),
    ],
)
def test_a_browser_value_from_userdata_is_returned_verbatim(
    value: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    install_userdata: Callable[[Mapping[str, str] | None], None],
) -> None:
    """The ``browser`` value is never validated, normalized or case-folded.

    ``Driver.java:29-42`` switches on the string with cases for ``"chrome"``
    and ``"firefox"`` and **no default branch**, so an unrecognised value
    leaves the driver unset and the scenario fails at first use - AAP 0.4.1:
    "Any other value fails at first driver use, as today".  Rejecting or
    correcting the value here would change that behaviour, so ``get_browser``
    returns it unchanged: no exception, no warning, no normalization.
    ``tests/test_driver.py`` covers the driver half of the same contract.
    """
    _use_directory(monkeypatch, tmp_path)
    install_userdata({"browser": value})

    assert config.get_browser() == value


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("safari", id="unrecognised-browser"),
        pytest.param("Firefox", id="mixed-case"),
    ],
)
def test_a_browser_value_from_the_file_is_returned_verbatim(
    value: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The file path applies no validation either, only the userdata path.

    Both layers must behave identically here; a validator added to one of them
    would make the browser's acceptability depend on *where* it was configured.
    """
    _use_directory(monkeypatch, tmp_path, {"browser": value})

    assert config.get_browser() == value


def test_installing_userdata_logs_key_names_and_no_configured_value(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    install_userdata: Callable[[Mapping[str, str] | None], None],
    log_capture: Callable[[str], list[logging.LogRecord]],
) -> None:
    """``password`` is one of the six, so no configured value is ever logged.

    ``app/config.py``'s secret-hygiene rule: the single DEBUG record it emits
    on installation names the recognised *keys* only.  A record carrying values
    would print the credential for the system under test into the console and
    into every CI log.
    """
    records = log_capture(CONFIG_MODULE)
    _use_directory(monkeypatch, tmp_path)
    install_userdata(
        {
            "browser": SYNTHETIC_VALUES["browser"],
            "password": SYNTHETIC_VALUES["password"],
            "username": SYNTHETIC_VALUES["username"],
        }
    )

    assert len(records) == 1, "installation must emit exactly one record"
    message = records[0].getMessage()
    assert records[0].levelno == logging.DEBUG
    assert "password" in message, "the record should name the recognised keys"
    for key in ("browser", "password", "username"):
        assert SYNTHETIC_VALUES[key] not in message, (
            f"the value configured for {key} was logged"
        )
        assert SYNTHETIC_VALUES[key] not in str(records[0].args)


# ==========================================================================
# Phase 5 - the boundaries: no environment layer, one importer, not Flask config
# ==========================================================================


@pytest.mark.parametrize(
    ("variable", "key"),
    ENV_OVERRIDE_CANDIDATES,
    ids=[f"{variable}-vs-{key}" for variable, key in ENV_OVERRIDE_CANDIDATES],
)
def test_an_environment_variable_cannot_override_the_file(
    variable: str,
    key: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No environment variable displaces a configured value.

    AAP 0.4.1's ``--browser`` row is categorical: behave userdata is "the only
    override path; no environment layer is added".  ``app/cli.py``'s
    ``--browser`` reaches this module as ``-D browser=...`` and by no other
    route, so a variable in the process environment - however plausibly named -
    must be inert.
    """
    _use_directory(monkeypatch, tmp_path, SYNTHETIC_VALUES)
    monkeypatch.setenv(variable, ENV_SENTINEL)

    assert config.get_property(key) == SYNTHETIC_VALUES[key]
    assert ACCESSORS[key]() == SYNTHETIC_VALUES[key]


@pytest.mark.parametrize(
    ("variable", "key"),
    ENV_OVERRIDE_CANDIDATES,
    ids=[f"{variable}-vs-{key}" for variable, key in ENV_OVERRIDE_CANDIDATES],
)
def test_an_environment_variable_cannot_supply_a_missing_value(
    variable: str,
    key: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nor can one stand in for a key the file does not define.

    The complement of the previous test: with no file at all, an unset key
    stays ``None``.  An environment fallback would make a run's configuration
    depend on ambient state that nothing in the port records or reports.
    """
    _use_directory(monkeypatch, tmp_path)
    monkeypatch.setenv(variable, ENV_SENTINEL)

    assert config.get_property(key) is None
    assert ACCESSORS[key]() is None


def test_the_module_source_never_consults_the_process_environment(
    repo_root: Path,
) -> None:
    """``app/config.py`` has no way to reach the environment at all.

    The structural counterpart to the two behavioural tests above: it imports
    none of the modules that expose the environment and refers to no
    environment attribute, so no future edit can add an environment layer
    without failing here.

    AST-based rather than a substring search, which could not work on this
    file: its docstring discusses "no environment-variable layer" and names
    ``features/environment.py``, so a text grep for the token would report
    prose as a violation.
    """
    path = repo_root / "app" / "config.py"
    assert path.is_file(), f"{path} is missing"

    survey = _survey_imports(path, "app")
    imported_environment_modules = sorted(
        name
        for name in survey.modules
        if name.split(".")[0] in ENVIRONMENT_BEARING_MODULES
    )
    assert imported_environment_modules == []

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    referenced = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    } | {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    offenders = sorted(referenced.intersection(ENVIRONMENT_ACCESS_NAMES))
    assert offenders == [], f"app/config.py references {offenders}"


def test_app_config_is_the_only_module_importing_the_properties_reader(
    repo_root: Path,
) -> None:
    """One importer of ``app/utils/properties.py``, and it is ``app.config``.

    AAP 0.4.2: "``configuration.properties`` is read only in
    ``app/utils/properties.py``, reached only through ``app/config.py``".  That
    is what keeps the file's name, its grammar, its one-time load and its
    missing-file warning in one place, and the key names and the userdata
    precedence in another.

    Modules inside the reader's own package are exempt - ``app/utils/__init__``
    re-exports its siblings' public names, which is the package's declared job.
    """
    census = _production_modules(repo_root)
    assert census, "the production-module census found nothing to scan"
    assert CONFIG_MODULE in {module for module, _path, _pkg in census}

    importers: list[str] = []
    for module, path, package in census:
        if module == READER_PACKAGE or module.startswith(f"{READER_PACKAGE}."):
            continue
        survey = _survey_imports(path, package)
        if READER_MODULE in (survey.modules | survey.targets):
            importers.append(module)

    assert sorted(importers) == [CONFIG_MODULE]


def test_no_module_reaches_the_reader_through_the_utils_barrel(
    repo_root: Path,
) -> None:
    """Nor is the reader reached indirectly, through ``app.utils``' re-exports.

    ``app/utils/__init__.py`` re-exports the reader's public names, so
    ``from app.utils import get_property`` would read the properties file
    without importing the reader's module path and would slip past the previous
    test while breaking the same AAP 0.4.2 invariant.
    """
    offenders: list[str] = []
    for module, path, package in _production_modules(repo_root):
        if module == READER_PACKAGE or module.startswith(f"{READER_PACKAGE}."):
            continue
        for base, name in _survey_imports(path, package).members:
            if base == READER_PACKAGE and name in READER_PUBLIC_NAMES:
                offenders.append(f"{module} imports {name} from {base}")

    assert offenders == [], f"the reader is reached indirectly: {offenders}"


def test_app_config_imports_only_the_standard_library_and_the_reader(
    repo_root: Path,
) -> None:
    """The module's own imports are the reader plus the standard library.

    AAP 0.4.2 and the module's own contract: it must never import Flask,
    Selenium, behave, ``click``, or anything from ``app.services``,
    ``app.reporting``, ``app.pages``, ``app.web`` or ``app.automation`` -
    ``app.automation`` depends on *this* module for the ``browser`` key, so
    importing it back would create a cycle.  Keeping the module free of every
    heavyweight dependency is also what makes it importable in a worker process
    that never builds a Flask application.
    """
    path = repo_root / "app" / "config.py"
    survey = _survey_imports(path, "app")

    assert READER_MODULE in (survey.modules | survey.targets)

    forbidden = sorted(
        name
        for name in survey.modules
        if any(
            name == root or name.startswith(f"{root}.")
            for root in FORBIDDEN_CONFIG_IMPORT_ROOTS
        )
    )
    assert forbidden == [], f"app/config.py imports {forbidden}"

    non_stdlib = sorted(
        name
        for name in survey.modules
        if name.split(".")[0] not in sys.stdlib_module_names
        and name not in {READER_PACKAGE, READER_MODULE}
    )
    assert non_stdlib == [], f"app/config.py imports {non_stdlib}"


def test_importing_app_config_pulls_in_no_heavyweight_dependency(
    tmp_path: Path,
    repo_root: Path,
) -> None:
    """Importing the module loads no Flask, Selenium, behave or ``click``.

    The runtime counterpart of the previous test, which reads only this one
    file: an indirect import through ``app.utils`` would satisfy the AST check
    and still drag a browser stack into every worker process.  Measured in a
    fresh interpreter, since this session has long since imported Flask through
    ``tests/conftest.py``.
    """
    script = (
        "import json, sys\n"
        "import app.config\n"
        "roots = {name.split('.')[0] for name in sys.modules}\n"
        "print(json.dumps(sorted(roots.intersection("
        "{'flask', 'jinja2', 'click', 'behave', 'selenium', 'webdriver_manager'}"
        "))))\n"
    )
    completed = _run_fresh_interpreter(script, cwd=tmp_path, repo_root=repo_root)

    assert completed.returncode == 0, (
        f"fresh interpreter failed: {completed.stderr}"
    )
    assert json.loads(completed.stdout) == []


def test_create_app_succeeds_with_no_properties_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The viewer starts with no configuration file in sight.

    AAP 0.4.1: of the six keys, "None is required at startup".  The read-only
    artifact viewer has no business with any of them - it renders generated
    report artifacts - so building an application must neither read the
    properties file nor create one.
    """
    from app import create_app

    _use_directory(monkeypatch, tmp_path)
    flask_app = create_app()

    assert "web" in flask_app.blueprints
    assert not (tmp_path / properties.PROPERTIES_FILENAME).exists()


def test_create_app_copies_no_configuration_key_into_flask_config(
    flask_app: Flask,
) -> None:
    """Flask's ``app.config`` carries none of the six keys, in any spelling.

    The two ``config`` surfaces are unrelated, and conflating them is the most
    likely misreading of this part of the port: ``app/config.py`` serves the
    *browser* suite, while a Flask application's ``.config`` serves the viewer.
    ``create_app()`` must not seed one from the other, or a key would acquire a
    second source of truth with different precedence.
    """
    present = sorted(
        name
        for key in EXPECTED_KEYS
        for name in (
            key,
            key.upper(),
            key.replace(".", "_"),
            key.replace(".", "_").upper(),
        )
        if name in flask_app.config
    )
    assert present == [], f"Flask config carries configuration keys: {present}"


def test_create_app_copies_no_configured_value_into_flask_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nor does a configured *value* appear in Flask config under another name.

    The name-based check above would miss a factory that copied the sign-in URL
    into ``SERVER_NAME`` or the password into ``SECRET_KEY``, so this one builds
    an application with all six keys configured and looks for the values
    themselves.
    """
    from app import create_app

    _use_directory(monkeypatch, tmp_path, SYNTHETIC_VALUES)
    flask_app = create_app()

    flat = {
        str(value)
        for value in flask_app.config.values()
        if isinstance(value, (str, bytes))
    }
    leaked = sorted(value for value in SYNTHETIC_VALUES.values() if value in flat)
    assert leaked == [], f"Flask config carries configured values: {leaked}"


def test_no_userdata_is_left_installed_by_this_module(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Leak canary: the userdata slot is empty by the end of this module.

    Placed last on purpose.  The slot is process-global and
    ``isolate_process_state`` deliberately does not reset it, so a test that
    installed userdata without the pre-registered finalizer - here or in any
    module that ran earlier - would leave every subsequent read overridden.
    This reads all six keys in an empty directory: any value at all means a
    slot survived its test.
    """
    _use_directory(monkeypatch, tmp_path)

    leaked = {
        key: value
        for key, accessor in ACCESSORS.items()
        if (value := accessor()) is not None
    }
    assert leaked == {}, (
        "behave userdata leaked out of a test; install it only through the "
        f"install_userdata fixture. Leaked: {leaked}"
    )
