"""Unit suite over the ``target/`` artifact layout -- the unit half of criterion V7.

What this module proves
======================
:mod:`app.utils.paths` is the single owner of *where* every report artifact the ported
project writes is placed, and the single owner of *creating* the directories those
artifacts live in. This suite is the executable evidence for that ownership. Its headline
obligation is validation criterion **V7**:

    "Starting from a state where ``target/`` does not exist, a run completes and
    ``target/cucumber.json`` is written. This closes the trap that the Cucumber-JSON writer
    does not create its parent directory and raises ``FileNotFoundError`` if it is absent.
    Directory creation is owned by ``app/utils/paths.py`` and additionally guaranteed by the
    Makefile ``test`` target and by the BDD ``conftest.py``."

The unit half is asserted here; the end-to-end half belongs to
``tests/integration/test_run_flow.py``. The trap is not hypothetical and this suite does not
take it on trust: :func:`test_report_writers_fail_before_ensure_and_succeed_after`
reproduces the ``FileNotFoundError`` first and only then shows the ensure call removing it.

Three owners, on purpose
------------------------
Creation is guaranteed in three independent places -- :mod:`app.utils.paths`, the
``Makefile`` test target, and ``tests/conftest.py`` -- because the duty is easy to miss and
expensive to miss: the JSON writer fails at *session finish*, after the tests have already
run. The redundancy is neither to be reduced to one nor extended to a fourth.

A closely related cross-module contract is asserted here as well. ``app/__init__.py`` must
contain no ``mkdir`` of its own and must delegate to this module instead, so that the
artifact-root name is defined in exactly one place. If the ensure function were missing or
renamed, the application factory could not satisfy that contract, so
:func:`test_application_factory_delegates_directory_creation` guards the edge from this side
too.

Why the directory is still called ``target``
--------------------------------------------
The pipeline publishes the reports with, verbatim::

    cucumber failedFeaturesNumber: -1, failedScenariosNumber: -1, failedStepsNumber: -1,
    fileIncludePattern: '**/*.json', pendingStepsNumber: -1, skippedStepsNumber: -1,
    sortingMethod: 'ALPHABETICAL', undefinedStepsNumber: -1

``[Jenkins:L15]``. Because the ported application keeps writing underneath ``target/``, that
publisher invocation needs no change at all -- which is the entire payoff of retaining the
Java-flavoured directory name. Renaming the root to ``build``, ``out``, ``dist``, ``reports``
or ``artifacts`` would silently break CI report publication, so the literal name is asserted
here as a hard requirement rather than left to preference. The include pattern itself is
*not* declared by this suite nor by the module under test: it belongs to
``app/reporting/thresholds.py`` alongside the publisher thresholds, and duplicating it would
create a second source of truth. Its absence from the module under test is asserted, which
is a different thing from restating it.

The load-bearing test-design constraint
=======================================
``tests/conftest.py`` creates the *real* ``target/`` tree from ``pytest_configure``, and it
has to: the documented default run selects zero scenarios -- the tag selector ``-m "LogOut"``
ports ``tags = "@LogOut"`` ``[README.md:L87]`` and no scenario carries that tag -- so a
session-scoped autouse fixture would never fire, while the report writers still run at
session finish.

The consequence for this file is absolute: **by the time these tests execute, the real
``target/`` already exists.** Every creation assertion therefore drives the ensure function
through its optional base-directory parameter against pytest's ``tmp_path``. Nothing here
depends on the real tree being absent, and nothing here deletes it -- deleting it would
sabotage the sibling suites running beside this one in parallel, and wiping the tree is the
``Makefile`` clean target's job, the port of ``mvn clean``.

Source of truth for every asserted literal
==========================================
Configuration values are data, never decisions: each expected value below is carried over
verbatim from the source project and cited beside the assertion that checks it.

* ``[README.md:L79]``  ``"html:target/cucumber-reports.html"``
* ``[README.md:L80]``  ``"json:target/cucumber.json"``
* ``[README.md:L81]``  ``"rerun:target/rerun.txt"``
* ``[README.md:L82]``  ``"me.jvt.cucumber.report.PrettyReports:target/cucumber"``
* ``[README.md:L42-L43]`` "It generate JSON, HTML and Txt reporters as well. It also generate
  ``screen shots`` for your tests if you enable it and also generate ``error shots`` for your
  failed test cases as well." -- the origin of ``target/screenshots/`` and
  ``target/error-shots/``.
* ``[Jenkins:L15]`` the publisher invocation quoted above -- why the root is immutable.
* ``[pom.xml:L22-L23]`` ``<parallel>methods</parallel>`` with
  ``<useUnlimitedThreads>true</useUnlimitedThreads>`` -- ported to one worker per logical
  CPU, which is why concurrent creation has to be race-safe rather than merely idempotent.
* ``[.gitignore:L6]`` ``*.log`` -- honoured by ``app/logging_config.py``, which owns the log
  location; this module must own no log path, because ``target/`` is wiped on every run and a
  log placed there would vanish.

``target/surefire-reports/`` has no README counterpart. Its Surefire name is retained for
report-consumer parity, so anything keyed on that path keeps finding content, and the
retention is asserted rather than assumed.

Note on one citation: the Agent Action Plan's body cites the four plugin declarations as
``[README.md:L78-L81]``. That is a confirmed off-by-one -- the declarations are at
``L79-L82`` in the file, verified by reading it -- and the accurate locators are the ones used
throughout this module.

Deliberate omissions
====================
* **No deletion of anything**, least of all the real artifact tree.
* **No pytest, ``make`` or ported-script invocation.** The only child process started here is
  a bare ``python -c`` import probe, and it exists to prove the *absence* of a side effect.
* **No browser, driver or synthetic-data import.** Browser interaction is confined to
  ``tests/pages/`` and ``tests/support/driver_factory.py``.
* **No publisher thresholds, sort order or include glob declared.** Those belong to
  ``app/reporting/thresholds.py`` and are asserted by ``tests/unit/test_thresholds_parity.py``.
* **No repair of a preserved defect.** The catalogued source defects are behaviour and are
  preserved on purpose; nothing here corrects or conceals one.
* **No ``conftest.py`` beside this file.** The fixtures used are ``project_root`` from
  ``tests/conftest.py`` plus pytest's own ``tmp_path`` and ``caplog``.

Provenance
==========
This module has no counterpart in the source project, which is a Java/Maven
Selenium-Cucumber skeleton whose only tracked files are its build descriptor, its pipeline
definition, its ignore rules and its README. ``README.md``, ``Jenkins`` and ``.gitignore``
are cited above as the origin of the paths, the immutable root name, the parallelism and the
log-location contract this suite checks -- not because any line of them is reproduced here.
"""

from __future__ import annotations

import ast
import logging
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Final

import pytest

try:
    from app.utils import paths
except ImportError as _import_error:  # pragma: no cover - depends on what is installed
    # Reaching `app.utils.paths` executes the `app` package's `__init__`, which is the Flask
    # application factory, so this import transitively needs Flask and the rest of the
    # runtime stack. Where they are absent the whole module SKIPS with a reason rather than
    # erroring during collection, which keeps a dependency-free session -- the parity suite
    # above all -- collectable in an environment that cannot build the application.
    pytest.skip(
        "app.utils.paths could not be imported. Importing it executes app/__init__.py, the "
        "Flask application factory, so the runtime dependencies of requirements.txt must be "
        f"installed for this suite to run ({type(_import_error).__name__}: {_import_error}).",
        allow_module_level=True,
    )


# =============================================================================
# Expected values. Every one is carried over verbatim from the cited source and
# is spelled here independently of the module under test, so that a change to
# either side is a visible disagreement rather than a silent agreement.
# =============================================================================

# [Jenkins:L15] is why this name can never change; [README.md:L79-L82] is where it is
# documented, as the shared prefix of all four plugin outputs.
_ARTIFACT_ROOT_NAME: Final[str] = "target"

# Names a "modernising" rename would plausibly reach for. Any of them would invalidate the
# publisher's include pattern, because the pattern is matched against what lands under the
# root that keeps its Maven-era name.
_FORBIDDEN_ROOT_NAMES: Final[frozenset[str]] = frozenset(
    {"build", "out", "dist", "reports", "artifacts", "output", "tmp"}
)

# The eight path constants of the layout contract, each with its source locator. The ninth
# entry is the JUnit-XML file inside the Surefire directory: it is not one of the eight, but
# it is part of the same layout and the test configuration points `--junitxml` straight at
# it, so it is verified alongside them.
_ARTIFACT_PATH_CASES: Final[tuple[tuple[str, Path, str], ...]] = (
    # Artifact root -- [README.md:L79-L82] prefix, immutable because of [Jenkins:L15].
    ("artifact-root", paths.TARGET_DIR, "target"),
    # [README.md:L79] "html:target/cucumber-reports.html"
    ("cucumber-html-report", paths.CUCUMBER_HTML_PATH, "target/cucumber-reports.html"),
    # [README.md:L80] "json:target/cucumber.json"
    ("cucumber-json-report", paths.CUCUMBER_JSON_PATH, "target/cucumber.json"),
    # [README.md:L81] "rerun:target/rerun.txt"
    ("rerun-manifest", paths.RERUN_TXT_PATH, "target/rerun.txt"),
    # [README.md:L82] "me.jvt.cucumber.report.PrettyReports:target/cucumber"
    ("pretty-reports-directory", paths.PRETTY_REPORTS_DIR, "target/cucumber"),
    # [README.md:L42-L43] "... generate `screen shots` for your tests if you enable it ..."
    ("screen-shots-directory", paths.SCREENSHOTS_DIR, "target/screenshots"),
    # [README.md:L42-L43] "... also generate `error shots` for your failed test cases ..."
    ("error-shots-directory", paths.ERROR_SHOTS_DIR, "target/error-shots"),
    # No README counterpart: the Surefire name, retained for report-consumer parity.
    ("surefire-reports-directory", paths.SUREFIRE_REPORTS_DIR, "target/surefire-reports"),
    # `TEST-<class>.xml` is Surefire's own convention and `CukesRunner` is the class the
    # source build collected with <include>**/CukesRunner*.java</include> [pom.xml:L27].
    (
        "surefire-junit-xml",
        paths.SUREFIRE_JUNIT_XML_PATH,
        "target/surefire-reports/TEST-CukesRunner.xml",
    ),
)

# The ordered, immutable five-directory sequence. A cross-module contract: `app/__init__.py`,
# the `Makefile` and `tests/conftest.py` all name exactly this set, root first so that no
# caller walking the sequence asks for a child before its parent. Do not add or drop an
# entry -- no feature may be dropped and none may be added.
_EXPECTED_DIRECTORIES: Final[tuple[str, ...]] = (
    "target",
    "target/cucumber",
    "target/screenshots",
    "target/error-shots",
    "target/surefire-reports",
)

# The four directories directly beneath the root, in the same order.
_EXPECTED_SUBDIR_NAMES: Final[tuple[str, ...]] = (
    "cucumber",
    "screenshots",
    "error-shots",
    "surefire-reports",
)

# The three report *files*, in the plugin order of [README.md:L79-L81]. The PrettyReports
# output is excluded because it is a directory, not a file.
_EXPECTED_REPORT_FILES: Final[tuple[str, ...]] = (
    "target/cucumber-reports.html",
    "target/cucumber.json",
    "target/rerun.txt",
)

# Concurrency figures for the race-safety check. The suite runs with one worker per logical
# CPU -- the port of <parallel>methods</parallel> [pom.xml:L22] together with
# <useUnlimitedThreads>true</useUnlimitedThreads> [pom.xml:L23] -- so several callers really
# do reach the same `mkdir` at the same instant. `mkdir(parents=True, exist_ok=True)` is what
# makes that safe, and these figures are the ones the behaviour was established at.
_RACE_WORKERS: Final[int] = 32
_RACE_TASKS: Final[int] = 200

# The import probe is a bare `python -c`; a minute is generous even on a cold filesystem.
# An explicit timeout is mandatory: no subprocess in this project may run unbounded.
_IMPORT_PROBE_TIMEOUT_SECONDS: Final[float] = 120.0

# `app/utils` is the terminal node of the one-way chain api -> services -> reporting ->
# utils, and the three modules inside it are mutually independent. The module under test may
# therefore import nothing but these four standard-library packages.
_ALLOWED_IMPORT_ROOTS: Final[frozenset[str]] = frozenset({"logging", "os", "pathlib", "typing"})

# Sibling modules of the same package. Importing either would couple three modules that are
# deliberately independent of one another.
_SIBLING_MODULE_NAMES: Final[frozenset[str]] = frozenset({"properties", "platform_exec"})

# Attribute and function names that must not appear in the module under test, grouped by the
# responsibility each one would wrongly pull into it.
_DELETION_NAMES: Final[frozenset[str]] = frozenset(
    {"rmtree", "unlink", "rmdir", "remove", "removedirs", "shutil"}
)
_ENVIRONMENT_NAMES: Final[frozenset[str]] = frozenset(
    {"environ", "getenv", "putenv", "expandvars", "expanduser", "load_dotenv", "dotenv_values"}
)
_PROCESS_NAMES: Final[frozenset[str]] = frozenset(
    {"subprocess", "Popen", "system", "popen", "check_call", "check_output", "spawnl", "spawnv"}
)
_LOGGING_CONFIG_NAMES: Final[frozenset[str]] = frozenset(
    {"basicConfig", "dictConfig", "fileConfig", "addHandler", "setLevel", "removeHandler"}
)
_TEXT_IO_NAMES: Final[frozenset[str]] = frozenset({"open", "read_text", "write_text"})
_BINARY_IO_NAMES: Final[frozenset[str]] = frozenset({"read_bytes", "write_bytes"})

# True when `chmod` cannot be used to make a directory unwritable for this process. The
# superuser bypasses the permission bits entirely, so the read-only check has to be skipped
# rather than allowed to report a spurious failure.
_CHMOD_IS_INEFFECTIVE: Final[bool] = os.name != "posix" or (
    hasattr(os, "geteuid") and os.geteuid() == 0
)


# =============================================================================
# Source-inspection helpers.
#
# Every structural claim about `app/utils/paths.py` is checked against its parsed
# abstract syntax tree rather than against raw text. That is a correctness
# requirement, not a preference: the module's own docstring quotes the publisher
# invocation, include pattern and all, so a substring search over the file would
# report the pattern as "declared" when it is merely documented. Parsing lets
# documentation and data be told apart exactly.
# =============================================================================


def _documentation_constant_ids(tree: ast.AST) -> set[int]:
    """Return the ids of every string constant that is a bare statement.

    A string on its own line is documentation -- a module, class or function docstring, or
    the attribute docstring convention this project uses beneath each constant. It is never
    data, so it must be excluded before the remaining literals are inspected.

    Args:
        tree: Any parsed syntax tree.

    Returns:
        The :func:`id` of each documentation string constant found in *tree*.
    """
    documentation: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            if isinstance(node.value.value, str):
                documentation.add(id(node.value))
    return documentation


def _declared_string_literals(tree: ast.AST) -> tuple[str, ...]:
    """Return every string literal in *tree* that is data rather than documentation.

    Args:
        tree: Any parsed syntax tree.

    Returns:
        The literal values, in traversal order, with docstrings and attribute docstrings
        removed.
    """
    documentation = _documentation_constant_ids(tree)
    return tuple(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in documentation
    )


def _module_level_string_constants(tree: ast.Module) -> dict[str, str]:
    """Return the module-level constants of *tree* whose value is a plain string.

    Only top-level assignments are considered, annotated or not, because those are what a
    sibling module imports as a constant.

    Args:
        tree: The parsed module.

    Returns:
        A mapping of constant name to its literal string value.
    """
    constants: dict[str, str] = {}
    for statement in tree.body:
        if isinstance(statement, ast.AnnAssign):
            annotated_target = statement.target
            annotated_value = statement.value
            if isinstance(annotated_target, ast.Name) and isinstance(annotated_value, ast.Constant):
                if isinstance(annotated_value.value, str):
                    constants[annotated_target.id] = annotated_value.value
        elif isinstance(statement, ast.Assign) and isinstance(statement.value, ast.Constant):
            if isinstance(statement.value.value, str):
                for plain_target in statement.targets:
                    if isinstance(plain_target, ast.Name):
                        constants[plain_target.id] = statement.value.value
    return constants


def _imported_roots(tree: ast.AST) -> set[str]:
    """Return the top-level module name of every import in *tree*.

    A relative import is reported with its leading dots preserved -- ``.properties`` rather
    than ``properties`` -- so that a sibling import cannot hide behind a bare name.

    Args:
        tree: Any parsed syntax tree.

    Returns:
        The set of imported roots.
    """
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                roots.add("." * node.level + module)
            elif module:
                roots.add(module.split(".")[0])
    return roots


def _imported_module_components(tree: ast.AST) -> set[str]:
    """Return every dotted component of every module path the imports in *tree* reach.

    ``_imported_roots`` deliberately reports only the first component, which is what the
    allow-list is expressed in terms of. That is too coarse for spotting a sibling import,
    because ``from app.utils import properties`` and ``from . import platform_exec`` both hide
    the sibling's name *after* the root. Decomposing every import target into its components
    -- including the names introduced by a ``from`` clause, which may themselves be submodules
    -- catches the sibling however it is spelled.

    Args:
        tree: Any parsed syntax tree.

    Returns:
        The set of components, with leading dots of relative imports stripped.
    """
    reached: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                reached.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            prefix = "." * node.level + (node.module or "")
            reached.add(prefix)
            separator = "" if not prefix or prefix.endswith(".") else "."
            for alias in node.names:
                reached.add(f"{prefix}{separator}{alias.name}")
    return {
        component
        for module_path in reached
        for component in module_path.strip(".").split(".")
        if component
    }


def _imported_member_names(tree: ast.AST) -> set[str]:
    """Return every name introduced by a ``from ... import ...`` statement in *tree*.

    Args:
        tree: Any parsed syntax tree.

    Returns:
        The imported member names, ignoring any local alias.
    """
    members: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                members.add(alias.name)
    return members


def _referenced_names(tree: ast.AST) -> set[str]:
    """Return every bare name and attribute name referenced anywhere in *tree*.

    Attribute names are collected without their owner, so ``shutil.rmtree`` contributes both
    ``shutil`` and ``rmtree``. That is deliberate: it catches a forbidden call however it is
    reached.

    Args:
        tree: Any parsed syntax tree.

    Returns:
        The set of referenced names.
    """
    referenced: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            referenced.add(node.attr)
        elif isinstance(node, ast.Name):
            referenced.add(node.id)
    return referenced


def _calls_to(tree: ast.AST, names: frozenset[str]) -> list[ast.Call]:
    """Return every call in *tree* whose callee is one of *names*.

    Args:
        tree: Any parsed syntax tree.
        names: Callee names to match, whether reached as a bare name or an attribute.

    Returns:
        The matching call nodes, in traversal order.
    """
    matched: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        if isinstance(callee, ast.Attribute) and callee.attr in names:
            matched.append(node)
        elif isinstance(callee, ast.Name) and callee.id in names:
            matched.append(node)
    return matched


def _string_keyword(call: ast.Call, keyword: str) -> str | None:
    """Return the string value passed to *keyword* in *call*, if there is one.

    Args:
        call: The call node to inspect.
        keyword: The keyword argument name to look for.

    Returns:
        The literal string value, or ``None`` when the keyword is absent or is not a plain
        string literal.
    """
    for entry in call.keywords:
        if entry.arg == keyword and isinstance(entry.value, ast.Constant):
            value = entry.value.value
            if isinstance(value, str):
                return value
    return None


def _relative_posix_paths(base: Path) -> list[str]:
    """Return every path under *base*, relative and POSIX-rendered, sorted.

    Report consumers -- above all the publisher's include pattern ``[Jenkins:L15]`` -- are
    written in terms of forward slashes, so comparisons are made in that form and stay valid
    on any platform.

    Args:
        base: The directory to walk.

    Returns:
        The sorted relative paths of everything inside *base*.
    """
    return sorted(entry.relative_to(base).as_posix() for entry in base.rglob("*"))


def _module_warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    """Return the ``WARNING`` records emitted by the module under test.

    Filtering by logger name keeps the assertion about *this* module rather than about
    whatever else the session happens to log.

    Args:
        caplog: pytest's log-capture fixture.

    Returns:
        The captured warning records whose logger is the module under test.
    """
    return [
        record
        for record in caplog.records
        if record.name == paths.__name__ and record.levelno == logging.WARNING
    ]


# =============================================================================
# Fixtures. `project_root` comes from tests/conftest.py and is derived from that
# file's own location, never from the working directory: every parallel worker is
# a separate process and none is guaranteed to have been started from the root,
# so a working-directory-relative path would be a latent flake.
# =============================================================================


@pytest.fixture(scope="module")
def paths_module_file(project_root: Path) -> Path:
    """Return the absolute path of the module under test.

    Args:
        project_root: The repository root, injected by ``tests/conftest.py``.

    Returns:
        The path of ``app/utils/paths.py``.
    """
    module_file = project_root / "app" / "utils" / "paths.py"
    assert module_file.is_file(), f"the module under test is missing at {module_file}"
    return module_file


@pytest.fixture(scope="module")
def paths_source(paths_module_file: Path) -> str:
    """Return the source text of the module under test.

    Read with an explicit UTF-8 encoding, as every file access in this project is, so the
    result cannot depend on the host's locale.

    Args:
        paths_module_file: The path of the module under test.

    Returns:
        The decoded source text.
    """
    return paths_module_file.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def paths_tree(paths_source: str) -> ast.Module:
    """Return the parsed syntax tree of the module under test.

    Args:
        paths_source: The source text of the module under test.

    Returns:
        The parsed module.
    """
    return ast.parse(paths_source)


@pytest.fixture(scope="module")
def factory_tree(project_root: Path) -> ast.Module:
    """Return the parsed syntax tree of the application factory.

    The factory is inspected -- never invoked -- to prove it delegates directory creation to
    the module under test instead of creating directories itself.

    Args:
        project_root: The repository root, injected by ``tests/conftest.py``.

    Returns:
        The parsed ``app/__init__.py``.
    """
    factory_file = project_root / "app" / "__init__.py"
    assert factory_file.is_file(), f"the application factory is missing at {factory_file}"
    return ast.parse(factory_file.read_text(encoding="utf-8"))


# =============================================================================
# The path constants.
#
# Configuration values are data, never decisions: no value below is rounded,
# renamed or modernised, and each carries the locator it was taken from.
# =============================================================================


@pytest.mark.parametrize(
    ("case", "constant", "expected"),
    _ARTIFACT_PATH_CASES,
    ids=[case for case, _constant, _expected in _ARTIFACT_PATH_CASES],
)
def test_artifact_path_matches_its_documented_source_value(
    case: str, constant: Path, expected: str
) -> None:
    """Each artifact path equals the literal documented for it in the source project.

    The eight constants of the layout contract, plus the JUnit-XML file the test
    configuration points ``--junitxml`` at, are compared in POSIX form so the assertion
    proves the exact ``target/...`` shape without hard-coding a platform separator.

    Args:
        case: The identifier of the artifact under test, used in the failure message.
        constant: The path constant published by the module under test.
        expected: The literal value carried over from the cited source line.
    """
    assert isinstance(constant, Path), f"{case} must be a pathlib.Path, not {type(constant)}"
    assert constant.as_posix() == expected, (
        f"{case} must stay exactly {expected!r}: it is quoted verbatim from the source "
        f"project and the report consumers are keyed on it"
    )


def test_artifact_root_is_the_literal_target_directory() -> None:
    """The artifact root is the literal ``target`` and nothing else.

    This is the single most important constant in the layout. The pipeline's report
    publisher matches artifacts with ``fileIncludePattern: '**/*.json'`` ``[Jenkins:L15]``
    and needs no change only because the output stays underneath a directory with this exact
    name. A rename would break CI report publication silently -- the publisher would simply
    find nothing.
    """
    assert paths.TARGET_DIR_NAME == _ARTIFACT_ROOT_NAME
    assert paths.TARGET_DIR.as_posix() == _ARTIFACT_ROOT_NAME
    assert paths.TARGET_DIR.name == _ARTIFACT_ROOT_NAME
    assert paths.TARGET_DIR_NAME not in _FORBIDDEN_ROOT_NAMES, (
        "the Maven-era root name is retained deliberately; renaming it would invalidate the "
        "publisher's include pattern [Jenkins:L15]"
    )


def test_report_file_paths_follow_the_documented_plugin_order() -> None:
    """The three report files are published in the order the plugin array declares them.

    ``[README.md:L79-L81]`` lists the HTML report, then the JSON report, then the rerun
    manifest. The PrettyReports entry ``[README.md:L82]`` is absent from this tuple because
    it names a directory rather than a file, and it is exposed separately.
    """
    assert isinstance(paths.REPORT_FILES, tuple)
    assert tuple(path.as_posix() for path in paths.REPORT_FILES) == _EXPECTED_REPORT_FILES
    assert paths.PRETTY_REPORTS_DIR not in paths.REPORT_FILES, (
        "target/cucumber [README.md:L82] is a directory, so it must not appear among the "
        "report files"
    )


def test_surefire_directory_name_is_retained_verbatim() -> None:
    """The Surefire report directory keeps its Java-flavoured name.

    That name has no README counterpart: it is retained for report-consumer parity, so that
    any downstream consumer keyed on ``target/surefire-reports/`` keeps finding content. It
    is populated by the JUnit-XML output the test runner requests.
    """
    assert paths.SUREFIRE_REPORTS_DIR_NAME == "surefire-reports"
    assert paths.SUREFIRE_REPORTS_DIR.name == "surefire-reports"
    assert paths.SUREFIRE_JUNIT_XML_NAME == "TEST-CukesRunner.xml"
    assert paths.SUREFIRE_JUNIT_XML_PATH.parent == paths.SUREFIRE_REPORTS_DIR


def test_name_literals_compose_the_published_paths() -> None:
    """Every path constant is composed from the published name literal, not respelled.

    Checking that each path's final component is the corresponding ``*_NAME`` constant proves
    there is one spelling of each artifact name rather than two that merely happen to agree
    today.
    """
    assert paths.CUCUMBER_HTML_PATH.name == paths.CUCUMBER_HTML_NAME
    assert paths.CUCUMBER_JSON_PATH.name == paths.CUCUMBER_JSON_NAME
    assert paths.RERUN_TXT_PATH.name == paths.RERUN_TXT_NAME
    assert paths.PRETTY_REPORTS_DIR.name == paths.PRETTY_REPORTS_DIR_NAME
    assert paths.SCREENSHOTS_DIR.name == paths.SCREENSHOTS_DIR_NAME
    assert paths.ERROR_SHOTS_DIR.name == paths.ERROR_SHOTS_DIR_NAME
    assert paths.SUREFIRE_REPORTS_DIR.name == paths.SUREFIRE_REPORTS_DIR_NAME
    assert paths.SUREFIRE_JUNIT_XML_PATH.name == paths.SUREFIRE_JUNIT_XML_NAME


def test_every_artifact_path_sits_under_the_artifact_root() -> None:
    """No artifact escapes ``target/``.

    The whole tree is ephemeral and is wiped by the clean target, the port of ``mvn clean``.
    An artifact written outside the root would survive that wipe and would also fall outside
    the publisher's include pattern ``[Jenkins:L15]``.
    """
    published: tuple[Path, ...] = (
        paths.CUCUMBER_HTML_PATH,
        paths.CUCUMBER_JSON_PATH,
        paths.RERUN_TXT_PATH,
        paths.PRETTY_REPORTS_DIR,
        paths.SCREENSHOTS_DIR,
        paths.ERROR_SHOTS_DIR,
        paths.SUREFIRE_REPORTS_DIR,
        paths.SUREFIRE_JUNIT_XML_PATH,
        *paths.TARGET_SUBDIRS,
        *paths.MANAGED_DIRECTORIES[1:],
        *paths.REPORT_FILES,
    )
    for path in published:
        assert path.as_posix().startswith(f"{_ARTIFACT_ROOT_NAME}/"), (
            f"{path.as_posix()} must sit under {_ARTIFACT_ROOT_NAME}/ so that the clean "
            f"target wipes it and the publisher's include pattern still reaches it"
        )


def test_artifact_paths_mapping_exposes_every_published_path() -> None:
    """The configuration-friendly mapping covers the whole layout and nothing more.

    The keys mirror the environment-variable and properties-file names the committed
    templates document, so a configuration layer can map one onto the other without a
    translation table. A fresh mapping is returned on every call, so a caller mutating it
    cannot corrupt module state.
    """
    mapping = paths.artifact_paths()
    assert set(mapping) == {
        "target_dir",
        "cucumber_json_path",
        "cucumber_html_path",
        "rerun_txt_path",
        "pretty_reports_dir",
        "screenshots_dir",
        "error_shots_dir",
        "surefire_reports_dir",
        "surefire_junit_xml_path",
    }
    assert mapping["target_dir"] == paths.TARGET_DIR
    assert mapping["cucumber_json_path"] == paths.CUCUMBER_JSON_PATH
    assert mapping["surefire_junit_xml_path"] == paths.SUREFIRE_JUNIT_XML_PATH

    mapping["target_dir"] = Path("mutated")
    assert paths.artifact_paths()["target_dir"] == paths.TARGET_DIR, (
        "artifact_paths() must build a fresh mapping per call, so a caller can never corrupt "
        "the module's own constants"
    )


def test_posix_rendering_never_emits_a_backslash() -> None:
    """String forms of a path leave the application with forward slashes.

    The publisher's include pattern ``[Jenkins:L15]`` and every report consumer are written
    in terms of forward slashes, so a Windows-flavoured rendering in a report, a JSON
    response or a log line would invalidate them.
    """
    assert paths.to_posix(paths.CUCUMBER_JSON_PATH) == "target/cucumber.json"
    assert paths.to_posix("target/rerun.txt") == "target/rerun.txt"
    for path in paths.MANAGED_DIRECTORIES:
        assert "\\" not in paths.to_posix(path)


# =============================================================================
# The ordered, immutable five-directory sequence -- a cross-module contract.
# =============================================================================


def test_managed_directories_is_the_exact_ordered_five_entry_contract() -> None:
    """The managed sequence is exactly the five contracted directories, root first.

    ``app/__init__.py``, the ``Makefile`` and ``tests/conftest.py`` all name this same set.
    The root comes first so that a caller walking the sequence never asks for a child before
    its parent. No directory may be added and none may be dropped.
    """
    rendered = tuple(directory.as_posix() for directory in paths.MANAGED_DIRECTORIES)
    assert rendered == _EXPECTED_DIRECTORIES
    assert len(paths.MANAGED_DIRECTORIES) == 5
    assert paths.MANAGED_DIRECTORIES[0] == paths.TARGET_DIR
    assert paths.MANAGED_DIRECTORIES[1:] == paths.TARGET_SUBDIRS


def test_target_subdirectory_names_are_the_four_contracted_names_in_order() -> None:
    """The four subdirectory names are the contracted ones, in the contracted order.

    ``cucumber`` is the PrettyReports output ``[README.md:L82]``, ``screenshots`` and
    ``error-shots`` come from ``[README.md:L42-L43]``, and ``surefire-reports`` is the
    retained Surefire name.
    """
    assert paths.TARGET_SUBDIR_NAMES == _EXPECTED_SUBDIR_NAMES
    assert isinstance(paths.TARGET_SUBDIR_NAMES, tuple)
    assert len(paths.TARGET_SUBDIR_NAMES) == 4
    assert tuple(path.name for path in paths.TARGET_SUBDIRS) == _EXPECTED_SUBDIR_NAMES


def test_managed_directory_sequences_are_immutable() -> None:
    """The published sequences cannot be mutated by a caller.

    They are shared module state that several modules read, so they are tuples rather than
    lists: a caller cannot append to them, cannot assign into them, and a second read returns
    an identical sequence. Hashability is the positive proof of the same property.
    """
    for sequence in (
        paths.MANAGED_DIRECTORIES,
        paths.TARGET_SUBDIRS,
        paths.TARGET_SUBDIR_NAMES,
        paths.REPORT_FILES,
    ):
        assert isinstance(sequence, tuple)
        assert getattr(sequence, "__setitem__", None) is None
        assert getattr(sequence, "append", None) is None
        assert isinstance(hash(sequence), int)

    first_read = paths.MANAGED_DIRECTORIES
    assert paths.MANAGED_DIRECTORIES == first_read
    assert paths.managed_directories() == first_read
    assert paths.managed_directories() is not None


def test_layout_directories_agree_with_the_managed_sequence_for_any_base(tmp_path: Path) -> None:
    """Re-basing the layout shifts every directory and reorders nothing.

    The optional base directory is what lets a test point the whole layout at a temporary
    directory. It must change only where the root sits, never the root's name, the set of
    directories, or their order.
    """
    layout = paths.resolve_layout(tmp_path)
    assert layout.directories == paths.managed_directories(tmp_path)
    assert len(layout.directories) == len(paths.MANAGED_DIRECTORIES)
    assert layout.directories[0] == layout.root
    assert layout.directories[1:] == layout.subdirectories

    relative = tuple(directory.relative_to(tmp_path).as_posix() for directory in layout.directories)
    assert relative == _EXPECTED_DIRECTORIES
    assert tuple(path.relative_to(tmp_path).as_posix() for path in layout.report_files) == (
        _EXPECTED_REPORT_FILES
    )


# =============================================================================
# The root name is immutable, and this module is its only owner.
# =============================================================================


def test_base_directory_parameter_never_renames_the_artifact_root(tmp_path: Path) -> None:
    """A base directory re-bases the root; it can never rename it.

    Every entry point that accepts the parameter is exercised, because a rename slipping in
    through any one of them would invalidate the publisher's include pattern
    ``[Jenkins:L15]`` just as surely as editing the constant would.
    """
    nested = tmp_path / "deeply" / "nested" / "workspace"

    assert paths.target_root() == paths.TARGET_DIR
    assert paths.target_root().as_posix() == _ARTIFACT_ROOT_NAME

    for base in (tmp_path, nested, str(tmp_path)):
        assert paths.target_root(base).name == _ARTIFACT_ROOT_NAME
        assert paths.resolve_layout(base).root.name == _ARTIFACT_ROOT_NAME
        assert paths.managed_directories(base)[0].name == _ARTIFACT_ROOT_NAME
        assert paths.artifact_paths(base)["target_dir"].name == _ARTIFACT_ROOT_NAME

    assert paths.target_root(tmp_path) == tmp_path / _ARTIFACT_ROOT_NAME
    assert paths.target_root(nested) == nested / _ARTIFACT_ROOT_NAME


def test_module_declares_no_alternative_artifact_root(paths_tree: ast.Module) -> None:
    """The module declares the literal ``target`` and no alternative root name.

    Asserted twice over: by value through the published constant, and by source inspection
    over the module's own top-level string constants, so a second constant offering a
    "modernised" root would be caught even if nothing imported it yet.
    """
    constants = _module_level_string_constants(paths_tree)
    assert constants.get("TARGET_DIR_NAME") == _ARTIFACT_ROOT_NAME
    assert _ARTIFACT_ROOT_NAME in set(constants.values())

    offending = {name: value for name, value in constants.items() if value in _FORBIDDEN_ROOT_NAMES}
    assert offending == {}, (
        f"{offending} looks like an alternative artifact root. The Maven-era name is retained "
        f"deliberately: the publisher's include pattern [Jenkins:L15] depends on it"
    )


def test_module_does_not_declare_the_publisher_include_pattern(paths_tree: ast.Module) -> None:
    """The publisher's include pattern is not declared by this module.

    The pattern belongs to ``app/reporting/thresholds.py``, beside the publisher thresholds
    and the sort order, and is asserted by ``tests/unit/test_thresholds_parity.py``.
    Declaring it here as well would create a second source of truth for one CI setting. This
    module's obligation is only never to invalidate it, which the root-name assertions above
    are what guarantee.

    The needle below is a local search term for an absence check, not a constant of this
    suite. The check has to be structural rather than textual, because the module's own
    docstring quotes the publisher invocation in full -- pattern included -- and a substring
    search over the file would therefore report it as declared when it is merely documented.
    """
    forbidden_pattern = "**/*.json"
    declared = _declared_string_literals(paths_tree)
    assert forbidden_pattern not in declared, (
        "the report include pattern belongs to app/reporting/thresholds.py alone; this module "
        "must not restate it"
    )
    assert "ALPHABETICAL" not in declared, (
        "the report sort order belongs to app/reporting/thresholds.py alone; this module must "
        "not restate it"
    )


def test_application_factory_delegates_directory_creation(factory_tree: ast.Module) -> None:
    """The application factory creates no directory itself and hard-codes no artifact path.

    A cross-module contract, and the reason the ensure function must keep its published name:
    ``app/__init__.py`` is required to contain no ``mkdir`` of its own, so the layout is
    created in exactly one place and the artifact-root name is defined in exactly one place.
    Were the ensure function missing or renamed, the factory could not satisfy that
    requirement at all.
    """
    assert "mkdir" not in _referenced_names(factory_tree), (
        "the application factory must delegate directory creation to app/utils/paths.py "
        "rather than creating directories itself"
    )
    assert "ensure_target_layout" in _imported_member_names(factory_tree), (
        "the application factory must import the published ensure function from "
        "app/utils/paths.py"
    )

    hard_coded = [
        literal
        for literal in _declared_string_literals(factory_tree)
        if literal == _ARTIFACT_ROOT_NAME or literal.startswith(f"{_ARTIFACT_ROOT_NAME}/")
    ]
    assert hard_coded == [], (
        f"{hard_coded} hard-codes an artifact path in the application factory; every such "
        f"path must come from the constants published by app/utils/paths.py"
    )


# =============================================================================
# Criterion V7: the tree is created before a run, so the report writers cannot
# fail at session finish.
#
# Every creation assertion drives the ensure function through its optional base
# directory against `tmp_path`. The real `target/` already exists by the time
# these tests run -- `tests/conftest.py` creates it from `pytest_configure` -- and
# it is never deleted here: sibling suites run beside this one in parallel, and
# wiping the tree is the `Makefile` clean target's job.
# =============================================================================


def test_ensure_creates_the_whole_layout_from_an_empty_base(tmp_path: Path) -> None:
    """One call creates exactly the five contracted directories, and nothing else.

    This is the core of criterion V7. Starting from a base where the artifact root does not
    exist, a single call must leave the root and all four subdirectories in place, in the
    contracted order, with no extra directory and no stray file.
    """
    layout = paths.resolve_layout(tmp_path)
    assert not layout.root.exists(), "the base must start without an artifact root"

    created = paths.ensure_target_layout(tmp_path)

    assert created == paths.managed_directories(tmp_path)
    assert len(created) == 5
    for directory in created:
        assert directory.is_dir(), f"{directory} was reported as created but is not a directory"

    assert _relative_posix_paths(tmp_path) == sorted(_EXPECTED_DIRECTORIES), (
        "the call must create exactly the five contracted directories: no directory may be "
        "added and none may be dropped"
    )


def test_report_writers_fail_before_ensure_and_succeed_after(tmp_path: Path) -> None:
    """The trap is real, and the ensure call is what closes it.

    The Cucumber-JSON writer opens its file directly and does not create the parent
    directory, so a run begun while the artifact root is absent dies at session finish with
    ``FileNotFoundError`` -- after the tests have run, which is the worst possible moment to
    find out. Maven created the directory implicitly as part of its lifecycle; the Python port
    has to create it explicitly.

    The failure is reproduced first so that the success afterwards is evidence rather than
    assertion.
    """
    layout = paths.resolve_layout(tmp_path)

    with pytest.raises(FileNotFoundError):
        layout.cucumber_json.write_text("{}", encoding="utf-8")

    paths.ensure_target_layout(tmp_path)

    layout.cucumber_json.write_text("{}", encoding="utf-8")
    assert layout.cucumber_json.is_file()
    assert layout.cucumber_json.read_text(encoding="utf-8") == "{}"


def test_every_report_artifact_is_writable_after_ensure(tmp_path: Path) -> None:
    """All four plugin outputs, both shot directories and the JUnit-XML file are writable.

    ``[README.md:L79-L82]`` declares the four plugin outputs and ``[README.md:L42-L43]``
    describes the screen shots and error shots. Each is written here with an explicit UTF-8
    encoding, exactly as the reporting adapters do, so the whole documented artifact set is
    proven reachable rather than only the JSON report.
    """
    layout = paths.resolve_layout(tmp_path)
    paths.ensure_target_layout(tmp_path)

    layout.cucumber_html.write_text("<html></html>", encoding="utf-8")
    layout.cucumber_json.write_text("[]", encoding="utf-8")
    layout.rerun_txt.write_text("features/login.feature:21\n", encoding="utf-8")
    layout.surefire_junit_xml.write_text("<testsuite/>", encoding="utf-8")
    (layout.pretty_reports_dir / "index.html").write_text("<html></html>", encoding="utf-8")
    (layout.screenshots_dir / "step.png").write_bytes(b"")
    (layout.error_shots_dir / "failure.png").write_bytes(b"")

    for artifact in (
        layout.cucumber_html,
        layout.cucumber_json,
        layout.rerun_txt,
        layout.surefire_junit_xml,
        layout.pretty_reports_dir / "index.html",
        layout.screenshots_dir / "step.png",
        layout.error_shots_dir / "failure.png",
    ):
        assert artifact.is_file(), f"{artifact} could not be written after the ensure call"


def test_ensure_creates_missing_intermediate_directories(tmp_path: Path) -> None:
    """A base directory that does not exist yet is created along with the layout.

    Creation uses ``parents=True``, so a caller-supplied base -- a fresh workspace, a
    temporary directory that has only been named -- need not exist beforehand.
    """
    base = tmp_path / "workspace" / "checkout"
    assert not base.exists()

    created = paths.ensure_target_layout(base)

    assert created == paths.managed_directories(base)
    assert base.is_dir()
    assert _relative_posix_paths(base) == sorted(_EXPECTED_DIRECTORIES)


def test_ensure_parent_directory_closes_the_same_trap_for_one_file(tmp_path: Path) -> None:
    """A single report writer can have its own parent directory ensured.

    Report writers are handed a *file* path and need its directory to exist before they open
    it. Routing that through the module keeps the layout knowledge in one place instead of
    scattering ``mkdir`` calls across the reporting adapters.
    """
    target_file = paths.resolve_layout(tmp_path).surefire_junit_xml
    assert not target_file.parent.exists()

    assert paths.ensure_parent_directory(target_file) is True
    assert target_file.parent.is_dir()

    target_file.write_text("<testsuite/>", encoding="utf-8")
    assert target_file.is_file()

    assert paths.ensure_parent_directory(target_file) is True, "the call must be idempotent"


# =============================================================================
# Idempotency, race safety, and never raising at the caller.
# =============================================================================


def test_ensure_is_idempotent_across_three_consecutive_calls(tmp_path: Path) -> None:
    """Three calls in a row are indistinguishable from one.

    The application factory, the Makefile-driven run and the BDD conftest all reach for this
    behaviour, so a single session can call it several times. It has to be free of duplicate
    side effects: same result, same tree, no exception.
    """
    first = paths.ensure_target_layout(tmp_path)
    second = paths.ensure_target_layout(tmp_path)
    third = paths.ensure_target_layout(tmp_path)

    assert first == second == third == paths.managed_directories(tmp_path)
    assert _relative_posix_paths(tmp_path) == sorted(_EXPECTED_DIRECTORIES)


def test_ensure_is_race_safe_under_concurrent_callers(tmp_path: Path) -> None:
    """Concurrent callers racing on the same directories raise nothing.

    Not a theoretical concern. The suite runs with one worker per logical CPU -- the port of
    ``<parallel>methods</parallel>`` ``[pom.xml:L22]`` together with
    ``<useUnlimitedThreads>true</useUnlimitedThreads>`` ``[pom.xml:L23]`` -- so several
    callers really do reach the same ``mkdir`` at the same instant. ``exist_ok=True`` is what
    removes the race a hand-rolled "check, then create" would introduce.

    Threads are used rather than processes so the check stays fast and deterministic.
    """
    failures: list[Exception] = []
    results: list[tuple[Path, ...]] = []

    with ThreadPoolExecutor(max_workers=_RACE_WORKERS) as executor:
        futures = [
            executor.submit(paths.ensure_target_layout, tmp_path) for _ in range(_RACE_TASKS)
        ]
        for future in futures:
            try:
                results.append(future.result())
            except Exception as error:
                # Collected rather than propagated so the assertion below can name every
                # failure at once instead of stopping at the first one.
                failures.append(error)

    assert failures == [], f"concurrent creation raised {failures!r}"
    assert len(results) == _RACE_TASKS
    expected = paths.managed_directories(tmp_path)
    assert all(result == expected for result in results)
    assert _relative_posix_paths(tmp_path) == sorted(_EXPECTED_DIRECTORIES)


def test_ensure_returns_a_meaningful_signal_rather_than_none(tmp_path: Path) -> None:
    """The call reports what it achieved, so a caller always has something to act on.

    A bare ``None`` would leave callers unable to tell a full success from a partial failure.
    The returned sequence is compared against the expected one by length, which is how a
    partial failure is detected.
    """
    created = paths.ensure_target_layout(tmp_path)

    assert created is not None
    assert isinstance(created, tuple)
    assert created, "a successful call must return a non-empty sequence"
    assert len(created) == len(paths.managed_directories(tmp_path))
    assert all(isinstance(directory, Path) for directory in created)


def test_ensure_warns_and_returns_when_the_root_cannot_be_created(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A filesystem that refuses is absorbed into one warning, never an exception.

    Two guarantees have to hold at once. The directories must really be created wherever the
    filesystem allows it, and the call must never abort the caller: the application factory
    invokes it during start-up, so raising here would make importing the WSGI entrypoint fail
    outright on a hostile filesystem.

    A plain file is placed where the artifact root belongs, which makes ``mkdir`` fail for
    reasons no privilege can bypass -- so this check is meaningful whichever user runs it,
    including the superuser.
    """
    blocked_root = tmp_path / _ARTIFACT_ROOT_NAME
    blocked_root.write_text("this is a file, not a directory", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger=paths.__name__):
        created = paths.ensure_target_layout(tmp_path)

    assert created == (), "nothing could be created, so nothing may be reported as created"
    assert created is not None

    warnings = _module_warnings(caplog)
    assert warnings, "a refused filesystem must be reported at WARNING level"
    assert any(
        paths.to_posix(blocked_root) in record.getMessage() for record in warnings
    ), "the warning must name the artifact root it could not create"

    assert blocked_root.is_file(), "the blocking file must be left exactly as it was found"


@pytest.mark.skipif(
    _CHMOD_IS_INEFFECTIVE,
    reason=(
        "chmod cannot make a directory unwritable for this process: either the platform is "
        "not POSIX, or the process is running as the superuser, which bypasses the permission "
        "bits entirely. The privilege-independent equivalent -- a plain file occupying the "
        "artifact root -- is covered by "
        "test_ensure_warns_and_returns_when_the_root_cannot_be_created."
    ),
)
def test_ensure_absorbs_an_unwritable_base_directory(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An unwritable base directory produces a warning and no exception.

    The read-only-filesystem case stated directly. Permissions are restored in the teardown
    path so that pytest can clean the temporary directory up afterwards.
    """
    read_only_base = tmp_path / "read-only"
    read_only_base.mkdir()
    os.chmod(read_only_base, 0o500)
    try:
        with caplog.at_level(logging.WARNING, logger=paths.__name__):
            created = paths.ensure_target_layout(read_only_base)

        assert created == ()
        assert _module_warnings(caplog), "an unwritable base must be reported at WARNING level"
        assert not (read_only_base / _ARTIFACT_ROOT_NAME).exists()
    finally:
        os.chmod(read_only_base, 0o700)


def test_ensure_parent_directory_reports_failure_without_raising(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The single-parent helper degrades the same way, and says so in its return value.

    It returns a boolean rather than a sequence, so the signal is explicit: ``False`` means
    the filesystem refused, a warning has been logged, and nothing was raised.
    """
    blocking_file = tmp_path / "occupied"
    blocking_file.write_text("this is a file, not a directory", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger=paths.__name__):
        result = paths.ensure_parent_directory(blocking_file / "cucumber.json")

    assert result is False
    assert _module_warnings(caplog), "a refused parent directory must be reported at WARNING level"
    assert blocking_file.is_file()


def test_ensure_aliases_are_the_same_callable(tmp_path: Path) -> None:
    """Every published spelling of the ensure function is one implementation.

    The factory, the Makefile-driven run and the BDD conftest each reach for this behaviour by
    name. The aliases exist so that every reasonable spelling resolves to the same object,
    which is what stops behaviour from drifting between them.
    """
    assert paths.ensure_target_dirs is paths.ensure_target_layout
    assert paths.ensure_target_directories is paths.ensure_target_layout
    assert paths.ensure_layout is paths.ensure_target_layout
    assert paths.ensure_layout(tmp_path) == paths.managed_directories(tmp_path)


# =============================================================================
# Importing the module must not touch the filesystem.
# =============================================================================


def test_importing_the_module_creates_nothing(tmp_path: Path, project_root: Path) -> None:
    """Merely importing the module leaves the working directory untouched.

    Directories are created if and only if a caller invokes the ensure function, so module
    discovery -- by the linter, by the type checker, or by a collection pass -- never writes
    to disk. An import-time side effect would also make the three-owner contract meaningless,
    because the tree would appear without anyone asking for it.

    The check is only observable in a fresh working directory, and the repository's own
    artifact root already exists, so it runs in a child process rooted at a temporary
    directory. That child is started from an argument list with ``shell=False`` and an
    explicit timeout -- this project starts no process any other way -- and bytecode writing
    is disabled so nothing is left behind anywhere. A stale bytecode cache once made a defect
    experiment appear to disprove itself, which is why the caches are git-ignored and removed
    by the clean target; keeping this child cache-free is the same precaution.

    Args:
        tmp_path: The empty working directory the child is started in.
        project_root: The repository root, used to put the ``app`` package on the child's
            import path. Never the process working directory: every parallel worker is a
            separate process and none is guaranteed to have been started from the root.
    """
    environment = os.environ.copy()
    inherited_path = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        str(project_root)
        if not inherited_path
        else os.pathsep.join([str(project_root), inherited_path])
    )
    # Keeps the child from writing __pycache__ directories, in its own tree or in the
    # repository's, so "nothing was created" means exactly that.
    environment["PYTHONDONTWRITEBYTECODE"] = "1"

    completed = subprocess.run(
        [sys.executable, "-c", f"import {paths.__name__}"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=_IMPORT_PROBE_TIMEOUT_SECONDS,
        check=False,
    )

    if completed.returncode != 0:
        pytest.skip(
            "the child interpreter could not import the module, so the absence of an "
            "import-time side effect cannot be observed. Importing it executes "
            "app/__init__.py, the Flask application factory, so the runtime dependencies "
            f"must be installed for the child too (exit {completed.returncode}): "
            f"{completed.stderr.strip()[-500:]}"
        )

    assert not (tmp_path / _ARTIFACT_ROOT_NAME).exists(), (
        "importing the module must not create the artifact root; creation happens only on an "
        "explicit call to the ensure function"
    )
    assert sorted(entry.name for entry in tmp_path.iterdir()) == [], (
        f"importing the module left {sorted(entry.name for entry in tmp_path.iterdir())} "
        f"behind in the working directory"
    )


# =============================================================================
# Source inspection: the responsibilities this module must NOT take on.
#
# Each check below is structural, over the parsed tree, for the reason given at
# the top of the helper section: the module documents neighbouring contracts in
# its docstring, and documentation must not be mistaken for data.
# =============================================================================


def test_inspected_source_is_the_imported_module(paths_module_file: Path) -> None:
    """The file being inspected is the very module the tests import.

    Without this, every source-inspection assertion below could be passing against a
    different copy of the file than the one whose behaviour is being asserted.
    """
    assert paths.__file__ is not None
    assert Path(paths.__file__).resolve() == paths_module_file.resolve()
    assert paths.__name__ == "app.utils.paths"


def test_module_never_deletes_anything(paths_tree: ast.Module) -> None:
    """The module creates directories and removes nothing.

    Wiping the artifact tree is the ``Makefile`` clean target's job -- the port of
    ``mvn clean``. This module contributes the layout *knowledge*; the Makefile performs the
    deletion. Nothing in the migration deletes anything else either.
    """
    referenced = _referenced_names(paths_tree)
    offending = sorted(referenced & _DELETION_NAMES)
    assert offending == [], (
        f"{offending} would give this module the power to delete. Wiping target/ belongs to "
        f"the Makefile clean target, the port of mvn clean"
    )
    assert "mkdir" in referenced, "the module must still be the one that creates directories"


def test_module_declares_no_log_path(paths_tree: ast.Module) -> None:
    """The module owns no log destination.

    ``app/logging_config.py`` owns the log location, honouring the ``*.log`` ignore rule
    ``[.gitignore:L6]``. The log file must not live under the artifact root: the root holds
    exactly the artifacts enumerated in the layout contract, and it is wiped on every run, so
    a log placed there would vanish. Holding a module logger for warnings is expected -- what
    is forbidden is a log *path*.
    """
    log_literals = [
        literal
        for literal in _declared_string_literals(paths_tree)
        if literal.endswith(".log") or ".log" in literal
    ]
    assert log_literals == [], (
        f"{log_literals} looks like a log destination. The log location belongs to "
        f"app/logging_config.py, and target/ is wiped on every run"
    )
    assert "getLogger" in _referenced_names(paths_tree), (
        "the module is still expected to hold a module logger, which is how it reports a "
        "refused filesystem"
    )


def test_module_reads_no_environment_and_starts_no_process(paths_tree: ast.Module) -> None:
    """Configuration resolution and process execution belong elsewhere.

    The five-rung precedence chain -- explicit argument, environment variable, ``.env``,
    ``configuration.properties``, hard-coded default -- is ``app/config.py``'s
    responsibility, and process execution is the service layer's. This module composes paths
    and creates directories, so it reads no environment and starts nothing.
    """
    referenced = _referenced_names(paths_tree)

    environment_use = sorted(referenced & _ENVIRONMENT_NAMES)
    assert environment_use == [], (
        f"{environment_use} would move configuration resolution into this module; that chain "
        f"belongs to app/config.py"
    )

    process_use = sorted(referenced & _PROCESS_NAMES)
    assert process_use == [], (
        f"{process_use} would move process execution into this module; that belongs to "
        f"app/services/"
    )

    configuration_literals = [
        literal for literal in _declared_string_literals(paths_tree) if ".env" in literal
    ]
    assert configuration_literals == [], (
        f"{configuration_literals} names a configuration file; reading those is "
        f"app/config.py's and app/utils/properties.py's job"
    )


def test_module_imports_only_the_standard_library(paths_tree: ast.Module) -> None:
    """The module is the terminal node of the dependency chain.

    Internal dependencies run one way only: ``api -> services -> reporting -> utils``, and
    ``app/utils`` is where that chain ends. Importing only the standard library means this
    module can never participate in an import cycle and can never drag a third-party package
    into a deployment. The three modules inside ``app/utils`` are mutually independent as
    well, so neither sibling may be imported either.
    """
    roots = _imported_roots(paths_tree)

    assert roots <= _ALLOWED_IMPORT_ROOTS, (
        f"{sorted(roots - _ALLOWED_IMPORT_ROOTS)} is imported but not allowed here: this "
        f"module may import nothing beyond {sorted(_ALLOWED_IMPORT_ROOTS)}"
    )
    assert roots <= set(
        sys.stdlib_module_names
    ), f"{sorted(roots - set(sys.stdlib_module_names))} is not part of the standard library"
    assert not any(root.startswith(".") for root in roots), (
        f"{sorted(root for root in roots if root.startswith('.'))} is a relative import; the "
        f"three app/utils modules are deliberately independent of one another"
    )
    for forbidden in ("app", "tests", "scripts"):
        assert forbidden not in roots, (
            f"importing {forbidden!r} here would break the one-way dependency chain "
            f"api -> services -> reporting -> utils"
        )
    # Checked against every dotted component rather than the first one only: a sibling reached
    # as `from app.utils import properties` or `from . import platform_exec` hides its name
    # after the root, so a root-level check alone would silently never fire.
    sibling_use = sorted(_imported_module_components(paths_tree) & _SIBLING_MODULE_NAMES)
    assert sibling_use == [], f"{sibling_use} couples modules that must stay independent"


def test_module_logs_structurally_and_configures_nothing(paths_tree: ast.Module) -> None:
    """Diagnostics go through a logger; the logging system itself is configured elsewhere.

    Handlers, levels and formatters are set in exactly one place, ``app/logging_config.py``.
    A utility module that configured logging would silently outrank the application's own
    settings for every process that imported it, and a ``print`` would bypass the structured
    destination altogether.
    """
    referenced = _referenced_names(paths_tree)

    assert (
        "print" not in referenced
    ), "diagnostics must go through the module logger, never to standard output"
    configuration_use = sorted(referenced & _LOGGING_CONFIG_NAMES)
    assert configuration_use == [], (
        f"{configuration_use} configures the logging system; that belongs to "
        f"app/logging_config.py alone"
    )
    assert (
        "warning" in referenced
    ), "a refused filesystem is reported at WARNING level, so the module must still emit one"


def test_module_performs_no_unencoded_file_io(paths_tree: ast.Module) -> None:
    """Text is never read or written without an explicit UTF-8 encoding.

    Every file access in this project declares its encoding, so no artifact can depend on the
    host's locale. The module under test composes paths and creates directories, so it opens
    no stream at all today; the encoding requirement is asserted regardless, so that adding
    one later without an encoding is caught here rather than in a mis-decoded report.
    """
    text_io_calls = _calls_to(paths_tree, _TEXT_IO_NAMES)
    for call in text_io_calls:
        assert _string_keyword(call, "encoding") == "utf-8", (
            f'the text I/O call on line {call.lineno} must pass encoding="utf-8" so the '
            f"result cannot depend on the host locale"
        )

    binary_io_calls = _calls_to(paths_tree, _BINARY_IO_NAMES)
    assert text_io_calls == [] and binary_io_calls == [], (
        "the module is documented as performing no file I/O at all: it composes paths and "
        'creates directories. Any stream added here must declare encoding="utf-8".'
    )


def test_module_publishes_every_symbol_this_suite_relies_on(paths_tree: ast.Module) -> None:
    """The public surface this suite asserts is the surface the module declares.

    Sibling modules import these names, and the ensure function's published name is what lets
    the application factory delegate directory creation instead of calling ``mkdir`` itself.
    Checking the declared export list keeps a rename from quietly turning a real assertion
    elsewhere into a vacuous one.
    """
    exported: tuple[str, ...] = tuple(paths.__all__)
    assert exported, "the module must declare its public surface"

    required = {
        "CUCUMBER_HTML_PATH",
        "CUCUMBER_JSON_PATH",
        "ERROR_SHOTS_DIR",
        "MANAGED_DIRECTORIES",
        "PRETTY_REPORTS_DIR",
        "REPORT_FILES",
        "RERUN_TXT_PATH",
        "SCREENSHOTS_DIR",
        "SUREFIRE_JUNIT_XML_PATH",
        "SUREFIRE_REPORTS_DIR",
        "TARGET_DIR",
        "TARGET_DIR_NAME",
        "TARGET_SUBDIRS",
        "TARGET_SUBDIR_NAMES",
        "artifact_paths",
        "ensure_parent_directory",
        "ensure_target_layout",
        "managed_directories",
        "resolve_layout",
        "target_root",
        "to_posix",
    }
    missing = sorted(required - set(exported))
    assert missing == [], f"{missing} is relied on by this suite but is not exported"

    module_level_names = {
        statement.target.id
        for statement in paths_tree.body
        if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name)
    }
    undeclared = sorted(
        name for name in required if name.isupper() and name not in module_level_names
    )
    assert (
        undeclared == []
    ), f"{undeclared} is exported but is not a module-level constant of the inspected source"
