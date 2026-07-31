"""Validation criterion **V4** -- the frozen report-publisher constants.

What this module proves
======================
The source system published its Cucumber reports from a single Groovy statement, the
only statement of its pipeline's ``'Generate report'`` stage ``[Jenkins:L15]``::

           cucumber failedFeaturesNumber: -1, failedScenariosNumber: -1, failedStepsNumber: -1, fileIncludePattern: '**/*.json', pendingStepsNumber: -1, skippedStepsNumber: -1, sortingMethod: 'ALPHABETICAL', undefinedStepsNumber: -1

Eight values, passed by name. Six of them are thresholds and every one of them is
``-1``; the remaining two are the report-discovery glob ``'**/*.json'`` and the sort
order ``'ALPHABETICAL'``. Together with ``<testFailureIgnore>true</testFailureIgnore>``
``[pom.xml:L25]`` they are the whole of the source system's report-publication and
failure-tolerance behaviour, and criterion V4 requires the port to carry every one of
them across unchanged.

That is what the assertions below check, and they check it *two-sidedly*: the expected
values are also extracted from the committed source artifacts and compared against the
Python constants, so this module is executable evidence rather than a restatement of the
port's own opinion (baseline B11, "executable parity evidence rather than assertions of
parity"). Rule T1 governs every one of those values -- configuration values are data, not
decisions, so none of them is rounded, renamed or modernised, and each assertion carries
the file and line it came from.

Which half of V4 lives here
===========================
Criterion V4 is realised jointly by two modules, and the split is deliberate:

* **This module** owns the six publisher thresholds, the sort order, the include pattern
  and the failure-tolerance setting.
* ``tests/unit/test_config.py`` owns the five-rung precedence chain and the remaining
  source defaults -- the clone URL ``[Jenkins:L3]``, the preserved tag expression
  ``[README.md:L87]``, the parallelism settings ``[pom.xml:L22-L24]`` and the artifact
  root.

Neither module covers the other's half. Nothing here asserts the precedence chain, the
clone URL, the tag expression, the worker allocation or the ``target/`` layout: those are
asserted next door, and ``tests/unit/test_paths.py`` owns the layout itself.

Defect D3 is preserved here, never repaired
===========================================
``-1`` is not a limit of one, and it is not a mistake in transcription: the publisher
reads ``-1`` as *no threshold*, so a limit of ``-1`` can never be exceeded. No number of
failed features, failed scenarios, failed steps, pending steps, skipped steps or
undefined steps can therefore mark the build unstable or failed. Combined with
``<testFailureIgnore>true</testFailureIgnore>`` ``[pom.xml:L25]``, which made the source
build swallow test failures outright, the source system had **no build-time quality gate
at all**.

That is defect **D3** of the migration register, and it is preserved on purpose under
Rule T4, "defects are behavior": the requirement is that the rewrite fully match the
behaviour and logic of the current implementation. So the assertions below pin the six
``-1`` values in place. None of them expects a positive limit, none of them introduces a
gating switch, and a future change that "fixed" a ``-1`` would be caught here as the
regression it would be. ``docs/migration-parity.md`` is the authoritative register of
defects D1 through D9 and records the configuration switch that opts into each available
fix, per baseline B12.

Two consequences of D3 are asserted *elsewhere*, and are described here only so no reader
assumes this module covers them:

* **The exit-code policy.** While failure tolerance is true the ported runner treats
  pytest's exit code ``1`` -- tests ran and some failed -- as non-failing, which is the
  whole of ``testFailureIgnore``; codes ``2``, ``3`` and ``4`` stay hard failures; and
  code ``5``, nothing collected or everything deselected, is a *successful zero-scenario
  run*, which is the documented default case because the preserved tag expression matches
  no scenario (defect D2). That mapping belongs to
  ``app/services/test_runner_service.py`` and is asserted by criterion **V6**.
* **Report generation after a failed test stage.** The ``'Generate report'`` stage
  ``[Jenkins:L14]`` runs unconditionally after ``'Run tests'``, so
  ``app/services/pipeline_service.py`` invokes it from a finally-equivalent path. That
  behaviour is asserted by ``tests/integration/test_pipeline_service.py`` and criterion
  **V12**. What *is* checked here is the unit-granularity half: the constants module
  cannot itself cause a failure, because it holds no gating logic of any kind.

The include pattern is DATA, and is never evaluated
===================================================
``'**/*.json'`` is the one leading-wildcard string the migration allows to survive into
the Python tree, and it survives strictly as data: a value handed to the CI publisher,
never an instruction this codebase acts on. So this module compares it for exact string
equality and does nothing else with it. It is never passed to ``Path.glob``, ``fnmatch``,
``re.compile`` or ``PurePath.match`` -- not in the assertions and not in the helpers,
which is why no pattern-matching or regular-expression module is imported at all and the
source artifacts are parsed with plain string operations.

The pattern needs no rewriting for a simple reason worth recording: the port still writes
its reports underneath the artifact root whose Java-flavoured name ``app/utils/paths.py``
preserves, so the publisher's glob keeps working unedited. Retaining that directory name
is the concrete payoff, and ``tests/unit/test_paths.py`` is where the name itself is
asserted.

``ALPHABETICAL`` is a no-op today and still has to be right
==========================================================
With a single feature there is nothing for a sort order to reorder. The constant is
asserted anyway, because omitting it would turn a preserved setting into a divergence
that only surfaces the moment a second feature is authored. Applying it -- sorting
features by name before rendering -- is ``app/services/report_service.py``'s behaviour,
not this module's.

Import hygiene
==============
Reaching any module under ``app`` executes ``app/__init__.py`` first, and that module is
the Flask application factory, so *every* ``app.*`` reference transitively requires Flask.
This module therefore imports nothing from ``app`` at module scope. The constants are
reached through :func:`thresholds`, a module-scoped fixture built on
``pytest.importorskip``, and the resolved configuration is reached lazily through
``request.getfixturevalue("config")`` inside a guard, so a partially provisioned
environment *skips* with a documented reason instead of erroring during collection.

Everything that the standard library alone can check is deliberately kept outside those
guards: the two-sided reads of ``Jenkins`` and ``pom.xml``, and the source inspection of
``app/reporting/thresholds.py``. Those assertions therefore still yield evidence in an
environment that cannot build the application at all.

Symbol names are read, never invented
=====================================
``PUBLISHER_THRESHOLDS`` is the one name fixed by the migration plan, which specifies the
import ``from app.reporting.thresholds import PUBLISHER_THRESHOLDS`` as the replacement
for the Groovy publisher call's inline thresholds. Every other symbol this module touches
was read from the module that declares it -- ``REPORT_SORTING_METHOD``,
``REPORT_FILE_INCLUDE_PATTERN``, ``PUBLISHER_THRESHOLD_KEYS`` and the six
``REPORT_*_NUMBER`` constants from ``app/reporting/thresholds.py``, and
``DEFAULT_IGNORE_TEST_FAILURES``, ``IGNORE_TEST_FAILURES``, ``Provenance`` and
``load_config`` from ``app/config.py``. A symbol that is absent produces a skip naming
exactly what was looked for, never an invented name and never a silent pass.

Why the expected values are restated in this module
===================================================
The constants below duplicate the source values on purpose. A parity test that read its
expectations from the code under test would compare that code with itself and could not
fail; holding an independent copy is what makes the comparison meaningful. The copies are
transcribed from ``[Jenkins:L15]`` and ``[pom.xml:L25]``, and the two-sided tests
re-derive the same values from those files at run time, so all three agree or the run
fails.

Test inventory
==============
* Requirement 1 -- six thresholds, all the integer ``-1``, camelCase keys in source
  order, ordered and read-only, individual constants agreeing with the mapping.
* Requirement 2 -- the sort order is exactly ``ALPHABETICAL``.
* Requirement 3 -- the include pattern is exactly ``**/*.json``.
* Requirement 4 -- failure tolerance resolves to ``True`` ``[pom.xml:L25]``.
* Requirement 5 -- the constants module holds no gating logic.
* Requirement 6 -- layering, inertness, frozenness and single-source-of-truth of
  ``app/reporting/thresholds.py``.
* Two-sided parity -- ``Jenkins`` and ``pom.xml`` are read and compared against the
  Python constants.

Provenance
==========
This module has no counterpart in the source project, which never contained a committed
test tree of any kind. It is the executable form of criterion V4, and ``[Jenkins:L15]``
and ``[pom.xml:L25]`` are the two lines it exists to defend.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Mapping
from enum import Enum
from types import MappingProxyType, ModuleType
from typing import TYPE_CHECKING, Any, Final

import pytest

if TYPE_CHECKING:
    # Annotation-only import. `from __future__ import annotations` keeps every annotation a
    # string, so this block never executes and nothing here drags Flask into a session that
    # does not need it, while mypy still sees the real type of the resolved configuration.
    from pathlib import Path

    from flask import Config as FlaskConfig


# =============================================================================
# The expected values, transcribed from the source artifacts.
#
# These are this module's independent copy of the source truth, which is what
# gives the comparisons below any force at all. They are read straight off
# [Jenkins:L15] and [pom.xml:L25]; the two-sided tests re-extract the same
# values from those files, so a divergence in either direction fails the run.
# =============================================================================

# [Jenkins:L15] -- the six threshold parameters, in the order the publisher call lists
# them. The names stay in the publisher's own camelCase: they are its vocabulary carried
# across as data, not Python identifiers, so they are never re-cased (Rule T1).
_SOURCE_THRESHOLD_KEYS: Final[tuple[str, ...]] = (
    "failedFeaturesNumber",
    "failedScenariosNumber",
    "failedStepsNumber",
    "pendingStepsNumber",
    "skippedStepsNumber",
    "undefinedStepsNumber",
)

# [Jenkins:L15] -- all eight named parameters, thresholds and non-thresholds alike, in the
# order they appear. The publisher happens to list them alphabetically, which is why the
# glob and the sort order sit interleaved among the thresholds rather than after them.
_SOURCE_PARAMETER_ORDER: Final[tuple[str, ...]] = (
    "failedFeaturesNumber",
    "failedScenariosNumber",
    "failedStepsNumber",
    "fileIncludePattern",
    "pendingStepsNumber",
    "skippedStepsNumber",
    "sortingMethod",
    "undefinedStepsNumber",
)

# [Jenkins:L15] -- the threshold value, as an integer and as the raw source token. `-1`
# means "no threshold": preserved defect D3, never a limit of one.
_SOURCE_THRESHOLD_VALUE: Final[int] = -1
_SOURCE_THRESHOLD_TOKEN: Final[str] = "-1"

# [Jenkins:L15] sortingMethod: 'ALPHABETICAL'
_SOURCE_SORTING_METHOD: Final[str] = "ALPHABETICAL"

# [Jenkins:L15] fileIncludePattern: '**/*.json'
# DATA ONLY. Never expanded, compiled, split, normalised or rewritten.
_SOURCE_FILE_INCLUDE_PATTERN: Final[str] = "**/*.json"

# [pom.xml:L25] <testFailureIgnore>true</testFailureIgnore>
_SOURCE_FAILURE_TOLERANCE: Final[bool] = True
_SOURCE_FAILURE_TOLERANCE_ELEMENT: Final[str] = "<testFailureIgnore>true</testFailureIgnore>"

# The Groovy step the publisher statement invokes, and the 1-based line it occupies. Only
# the two `mvn clean test` command strings of the pipeline change in this migration, so the
# publisher statement keeps both its content and its line number, and the citation
# "[Jenkins:L15]" carried by every assertion here stays literally true.
_SOURCE_PUBLISHER_STEP: Final[str] = "cucumber"
_JENKINS_PUBLISHER_LINE: Final[int] = 15

# The build descriptor is read-only reference material for the whole migration: never
# compiled, never edited, and read statically. Its failure-tolerance element keeps its line.
_POM_FAILURE_TOLERANCE_LINE: Final[int] = 25

# The two source artifacts, relative to the repository root, and the two Python modules
# whose text is inspected.
_JENKINS_FILE: Final[str] = "Jenkins"
_POM_FILE: Final[str] = "pom.xml"
_THRESHOLDS_MODULE_FILE: Final[str] = "app/reporting/thresholds.py"
_PATHS_MODULE_FILE: Final[str] = "app/utils/paths.py"

# The importable name of the module under test, and of the configuration module that
# resolves the failure-tolerance setting.
_THRESHOLDS_MODULE_NAME: Final[str] = "app.reporting.thresholds"
_CONFIG_MODULE_NAME: Final[str] = "app.config"

# The public symbols this module reads, by the names the modules that declare them use.
_MAPPING_SYMBOL: Final[str] = "PUBLISHER_THRESHOLDS"
_KEYS_SYMBOL: Final[str] = "PUBLISHER_THRESHOLD_KEYS"
_SORTING_SYMBOL: Final[str] = "REPORT_SORTING_METHOD"
_PATTERN_SYMBOL: Final[str] = "REPORT_FILE_INCLUDE_PATTERN"
_FAILURE_TOLERANCE_DEFAULT_SYMBOL: Final[str] = "DEFAULT_IGNORE_TEST_FAILURES"
_FAILURE_TOLERANCE_KEY: Final[str] = "IGNORE_TEST_FAILURES"

# The individually exported threshold constants, mapped to the publisher parameter each one
# carries. Declared as a tuple of pairs rather than a dict so the pairing is ordered and
# obvious, and so a parametrised test can consume it directly.
_THRESHOLD_CONSTANT_SYMBOLS: Final[tuple[tuple[str, str], ...]] = (
    ("REPORT_FAILED_FEATURES_NUMBER", "failedFeaturesNumber"),
    ("REPORT_FAILED_SCENARIOS_NUMBER", "failedScenariosNumber"),
    ("REPORT_FAILED_STEPS_NUMBER", "failedStepsNumber"),
    ("REPORT_PENDING_STEPS_NUMBER", "pendingStepsNumber"),
    ("REPORT_SKIPPED_STEPS_NUMBER", "skippedStepsNumber"),
    ("REPORT_UNDEFINED_STEPS_NUMBER", "undefinedStepsNumber"),
)

# Every public constant of the module under test, for the frozenness check. Rebinding is
# what `Final` forbids, and each of these has to declare it.
_FINAL_CONSTANT_SYMBOLS: Final[tuple[str, ...]] = (
    _MAPPING_SYMBOL,
    _KEYS_SYMBOL,
    _SORTING_SYMBOL,
    _PATTERN_SYMBOL,
    *(symbol for symbol, _ in _THRESHOLD_CONSTANT_SYMBOLS),
)

# Package roots the constants module must never import from. `reporting` sits below
# `services` and above `utils` in the fixed one-way chain api -> services -> reporting ->
# utils, and `app.config` imports this module, so any import back out of it would close a
# cycle that fails at application start-up. Nothing under `app` may import from `tests`.
_FORBIDDEN_IMPORT_ROOTS: Final[frozenset[str]] = frozenset(
    {"app", "tests", "scripts", "flask", "pytest"}
)

# Path machinery, absent on purpose: the artifact root and every report location belong to
# `app/utils/paths.py`, and a frozen publisher-constants module has no business composing a
# filesystem path.
_PATH_IMPORT_ROOTS: Final[frozenset[str]] = frozenset({"os", "pathlib"})

# Call names that would make the module more than a constants module: gating, logging
# configuration, process exit or console output. None may appear.
_FORBIDDEN_CALL_NAMES: Final[frozenset[str]] = frozenset(
    {
        "print",
        "exit",
        "sys.exit",
        "os._exit",
        "basicConfig",
        "logging.basicConfig",
        "dictConfig",
        "fileConfig",
        "addHandler",
        "setLevel",
        "getLogger",
        "logging.getLogger",
        "open",
        "eval",
        "exec",
        "compile",
    }
)

# The artifact root's name. It must not appear as a value anywhere in the constants module.
_ARTIFACT_ROOT_NAME: Final[str] = "target"

# The two boolean spellings the build descriptor could carry, as a closed set. Anything
# else is a defect rather than a value to guess at.
_XML_BOOLEANS: Final[Mapping[str, bool]] = MappingProxyType({"true": True, "false": False})


# =============================================================================
# Fixtures and helpers.
#
# The application package is reached only from here, and only behind a guard, so
# that every standard-library-checkable assertion in this module keeps running in
# an environment where Flask -- and therefore `app` -- cannot be imported.
# =============================================================================


@pytest.fixture(scope="module")
def thresholds() -> ModuleType:
    """Return the imported ``app.reporting.thresholds`` module, or skip.

    Importing it executes ``app/__init__.py`` first, which is the Flask application
    factory, so this constants-only module transitively needs Flask installed. Where it is
    not, the tests that read a constant skip with a reason naming the module that could not
    be imported, while the source-inspection and two-sided tests -- which need nothing but
    the standard library -- still run.

    Module-scoped because the module object cannot change during the session and importing
    it once is enough; the skip is re-raised for every test that asks for it.

    Returns:
        The imported constants module.
    """
    # Assigned to a typed local before it is returned: `importorskip` is typed as returning
    # `Any`, and returning that directly from a function annotated `-> ModuleType` is what
    # mypy's `warn_return_any` exists to catch.
    #
    # `exc_type=ImportError` is required rather than decorative. Since pytest 9.1 the default
    # is `ModuleNotFoundError`, which covers only the case of a distribution being absent
    # outright; a distribution that is present but unusable raises a plain `ImportError` from
    # inside `app/__init__.py`, and with the default that would surface as a collection ERROR
    # rather than the documented skip this guard exists to produce. Both partial-provisioning
    # cases are therefore named explicitly. Verified both ways.
    module: ModuleType = pytest.importorskip(
        _THRESHOLDS_MODULE_NAME,
        reason=(
            f"{_THRESHOLDS_MODULE_NAME} could not be imported; reaching any app.* module "
            "executes app/__init__.py, the Flask application factory, so this requires "
            "Flask to be installed"
        ),
        exc_type=ImportError,
    )
    return module


@pytest.fixture(scope="module")
def config_module() -> ModuleType:
    """Return the imported ``app.config`` module, or skip.

    Only the failure-tolerance assertions need it, and they need it for two names:
    ``DEFAULT_IGNORE_TEST_FAILURES``, the hard-coded default that is the last rung of the
    precedence chain, and the provenance API that reports which rung a resolved value
    actually came from. Guarded for the same reason as :func:`thresholds`.

    Returns:
        The imported configuration module.
    """
    module: ModuleType = pytest.importorskip(
        _CONFIG_MODULE_NAME,
        reason=(
            f"{_CONFIG_MODULE_NAME} could not be imported; reaching any app.* module "
            "executes app/__init__.py, the Flask application factory, so this requires "
            "Flask to be installed"
        ),
        exc_type=ImportError,
    )
    return module


def _symbol(module: ModuleType, name: str) -> Any:
    """Return the public symbol *name* of *module*, or skip naming what was looked for.

    Only ``PUBLISHER_THRESHOLDS`` is a name the migration plan fixes; every other symbol
    was read from the module that declares it. Reading them dynamically is what lets an
    absent one produce an explicit, documented skip instead of an ``AttributeError`` or --
    far worse -- an invented name that quietly asserts nothing.

    Args:
        module: The module to read from.
        name: The attribute name to read.

    Returns:
        The attribute's value. Typed ``Any`` because the read is dynamic by design; every
        caller immediately binds the result to a precisely typed local or asserts its type.
    """
    if not hasattr(module, name):
        pytest.skip(
            f"{module.__name__} does not export {name!r}, so the constant it should carry "
            f"cannot be asserted; looked for the attribute {name!r}"
        )
    return getattr(module, name)


def _read_source(project_root: Path, relative_path: str) -> str:
    """Return the text of a committed file, or skip if it cannot be read.

    Every read is explicitly UTF-8 (baseline B7), so the bytes on disk are interpreted the
    same way on every platform and no locale-dependent default can change what a comparison
    sees.

    Args:
        project_root: Absolute repository root, from the session-scoped fixture of
            ``tests/conftest.py``. Never the process working directory: the suite runs one
            worker per logical CPU and each worker is a separate process, so a
            CWD-relative path would be a latent flake.
        relative_path: Repository-relative path of the file to read.

    Returns:
        The file's decoded text.
    """
    path = project_root / relative_path
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        pytest.skip(
            f"could not read {relative_path} at {path}, so the two-sided comparison "
            f"against it cannot be made ({type(error).__name__}: {error})"
        )


def _parse_module_source(project_root: Path, relative_path: str) -> ast.Module:
    """Return the parsed syntax tree of a committed Python module.

    Inspection is done on the syntax tree rather than on the raw text throughout, and that
    is a correctness decision rather than a stylistic one: the module under test documents
    itself thoroughly, quoting the publisher's camelCase parameter names and the word
    "raises" inside its own docstrings and comments. A text search would match those and
    report a defect that is not there, while the tree contains only what the module
    actually *does*.

    Args:
        project_root: Absolute repository root.
        relative_path: Repository-relative path of the module to parse.

    Returns:
        The parsed module.
    """
    source = _read_source(project_root, relative_path)
    try:
        return ast.parse(source, filename=relative_path)
    except SyntaxError as error:  # pragma: no cover - a syntax error fails the build first
        pytest.fail(f"{relative_path} is not valid Python: {error}")


def _imported_names(tree: ast.Module) -> tuple[str, ...]:
    """Return every module name imported by *tree*, in source order.

    A relative import is reported with its leading dots preserved, because the level is the
    whole point: ``from . import x`` inside the constants module would be an intra-package
    import of application code, which the layering rules forbid just as firmly as an
    absolute one.

    Args:
        tree: The parsed module.

    Returns:
        The imported module names.
    """
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.append("." * node.level + (node.module or ""))
    return tuple(names)


def _called_names(tree: ast.Module) -> tuple[str, ...]:
    """Return the rendered callee of every call in *tree*, in source order.

    ``ast.unparse`` is used so an attribute call reads as it was written --
    ``logging.basicConfig`` rather than a bare ``basicConfig`` -- which is what makes the
    comparison against the forbidden-call list precise.

    Args:
        tree: The parsed module.

    Returns:
        The rendered callees.
    """
    return tuple(ast.unparse(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call))


def _assigned_literals(tree: ast.Module) -> tuple[object, ...]:
    """Return every constant that *tree* assigns, including inside container literals.

    Only the right-hand sides of assignments are visited, so a docstring is never mistaken
    for a declared value. That distinction is what makes the single-source-of-truth check
    below trustworthy: ``app/utils/paths.py`` quotes the publisher statement in its module
    docstring for provenance, which is correct and must not be read as a second
    declaration of those values.

    Args:
        tree: The parsed module.

    Returns:
        Every assigned constant value.
    """
    literals: list[object] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign | ast.AnnAssign) and node.value is not None:
            literals.extend(
                sub.value for sub in ast.walk(node.value) if isinstance(sub, ast.Constant)
            )
    return tuple(literals)


def _assigned_mapping_keys(tree: ast.Module) -> tuple[str, ...]:
    """Return every string key of every dictionary literal that *tree* assigns.

    Complements :func:`_assigned_literals`, which sees a dictionary's values but reports its
    keys too; this narrows the result to keys alone so a mapping that re-declared the
    publisher's parameter names can be named precisely.

    Args:
        tree: The parsed module.

    Returns:
        The assigned dictionary keys.
    """
    keys: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign | ast.AnnAssign) and node.value is not None:
            for sub in ast.walk(node.value):
                if isinstance(sub, ast.Dict):
                    keys.extend(
                        key.value
                        for key in sub.keys
                        if isinstance(key, ast.Constant) and isinstance(key.value, str)
                    )
    return tuple(keys)


def _annotations_by_name(tree: ast.Module) -> Mapping[str, str]:
    """Return the rendered annotation of every annotated module-level assignment.

    Only top-level statements are considered, because that is where a module's constants
    live; the result is what the frozenness check reads to confirm each one declares
    ``Final``.

    Args:
        tree: The parsed module.

    Returns:
        A mapping of constant name to its rendered annotation.
    """
    annotated: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            annotated[node.target.id] = ast.unparse(node.annotation)
    return MappingProxyType(annotated)


def _publisher_statement(jenkins_text: str) -> tuple[int, str]:
    """Locate the pipeline's report-publication statement.

    The statement is found by content rather than by line index, so the location itself can
    be asserted separately and reported when it is wrong. Exactly one line of the pipeline
    may invoke the publisher: a second one would publish twice.

    Args:
        jenkins_text: The decoded text of the pipeline definition.

    Returns:
        The statement's 1-based line number and its whitespace-stripped text.
    """
    matches = [
        (number, line.strip())
        for number, line in enumerate(jenkins_text.splitlines(), start=1)
        if line.strip().startswith(f"{_SOURCE_PUBLISHER_STEP} ")
    ]
    assert len(matches) == 1, (
        f"expected exactly one {_SOURCE_PUBLISHER_STEP!r} publisher statement in "
        f"{_JENKINS_FILE}, found {len(matches)} at lines "
        f"{[number for number, _ in matches]}; the report-publication contract of "
        f"[{_JENKINS_FILE}:L{_JENKINS_PUBLISHER_LINE}] is a single statement"
    )
    return matches[0]


def _publisher_arguments(statement: str) -> Mapping[str, str]:
    """Parse the publisher statement's named arguments into an ordered mapping.

    Deliberately hand-rolled from plain string operations. No regular-expression engine and
    no pattern matcher is involved anywhere in this module, which is what makes it
    self-evident that the include pattern ``'**/*.json'`` is only ever compared as data --
    it is never compiled, expanded or matched against anything.

    Groovy's named-argument syntax here is a flat, comma-separated list of ``name: value``
    pairs whose values contain no comma, so splitting on the comma and then on the first
    colon recovers them exactly. A single-quoted value is unquoted, and a bare value is
    returned as its raw source token so a test can assert the token itself rather than a
    coerced interpretation of it.

    Args:
        statement: The whitespace-stripped publisher statement.

    Returns:
        The argument names mapped to their raw values, in source order.
    """
    prefix = f"{_SOURCE_PUBLISHER_STEP} "
    assert statement.startswith(prefix), (
        f"the publisher statement of {_JENKINS_FILE} should invoke the "
        f"{_SOURCE_PUBLISHER_STEP!r} step; found {statement[:40]!r}"
    )

    arguments: dict[str, str] = {}
    for fragment in statement[len(prefix) :].split(","):
        name, separator, value = fragment.partition(":")
        assert separator, (
            f"expected a 'name: value' pair in the publisher statement of "
            f"{_JENKINS_FILE}, found {fragment.strip()!r}"
        )
        name = name.strip()
        value = value.strip()
        if len(value) >= 2 and value.startswith("'") and value.endswith("'"):
            value = value[1:-1]
        assert (
            name not in arguments
        ), f"the publisher statement of {_JENKINS_FILE} names {name!r} more than once"
        arguments[name] = value
    return MappingProxyType(arguments)


def _element_line(xml_text: str, element: str) -> int:
    """Return the 1-based line of the single occurrence of *element*.

    The build descriptor is reference material for this migration: it is read statically as
    text, never parsed as a build, never compiled and never edited. A plain text search is
    all that is needed to confirm one of its declarations, and it keeps every Java fact in
    this module a static read.

    Args:
        xml_text: The decoded text of the build descriptor.
        element: The exact element text to find, tags included.

    Returns:
        The element's 1-based line number.
    """
    matches = [
        number
        for number, line in enumerate(xml_text.splitlines(), start=1)
        if line.strip() == element
    ]
    assert len(matches) == 1, (
        f"expected exactly one {element!r} declaration in {_POM_FILE}, found "
        f"{len(matches)} at lines {matches}"
    )
    return matches[0]


def _resolved_config(request: pytest.FixtureRequest) -> FlaskConfig:
    """Return the resolved configuration mapping of a per-test application, or skip.

    The mapping is obtained through the ``config`` fixture of ``tests/conftest.py``, which
    is the resolved truth for the run rather than a restatement of the defaults: it is what
    the application factory populated from the five-rung precedence chain. Asking for it
    lazily, rather than declaring it as a test parameter, is what allows an environment in
    which the factory cannot be built -- Flask absent, or an application module not yet
    present -- to skip with a reason that names the cause instead of erroring during
    fixture setup.

    Args:
        request: The active test's request object, used to resolve the fixture on demand.

    Returns:
        The application's configuration mapping.
    """
    try:
        mapping: FlaskConfig = request.getfixturevalue("config")
    except ImportError as error:
        pytest.skip(
            "the application factory could not build an application, so the resolved "
            f"configuration cannot be read ({type(error).__name__}: {error})"
        )
    return mapping


def _skip_unless_source_default(config_module: ModuleType, key: str) -> None:
    """Skip unless *key* resolves from the hard-coded source default.

    Failure tolerance is resolved through five rungs -- explicit constructor argument,
    environment variable, ``.env`` file, ``configuration.properties``, hard-coded default --
    and only the last of those carries the source value from ``[pom.xml:L25]``. An ambient
    override in the environment running this suite would therefore legitimately change the
    resolved value, and reporting that as a parity failure would be wrong: the port would be
    behaving exactly as its documented precedence chain says it should.

    So the rung is checked first and an overridden run is skipped with a reason that names
    the rung responsible. The hard-coded default itself is asserted unconditionally
    elsewhere in this module, where no environment can reach it.

    Args:
        config_module: The imported configuration module.
        key: The environment-style setting name whose provenance is checked.
    """
    load_config = _symbol(config_module, "load_config")
    provenance = _symbol(config_module, "Provenance")
    resolved = load_config()
    actual = resolved.provenance_of(key)
    if actual is not provenance.DEFAULT:
        pytest.skip(
            f"{key} resolves from the {actual.value!r} rung of the precedence chain in this "
            f"environment, not from the hard-coded source default, so the resolved value is "
            f"not the one [{_POM_FILE}:L{_POM_FAILURE_TOLERANCE_LINE}] specifies"
        )


# =============================================================================
# REQUIREMENT 1 -- the six thresholds, every one of them the integer -1.
#
# [Jenkins:L15] failedFeaturesNumber: -1, failedScenariosNumber: -1,
#               failedStepsNumber: -1, pendingStepsNumber: -1,
#               skippedStepsNumber: -1, undefinedStepsNumber: -1
#
# PRESERVED DEFECT D3. `-1` is the publisher's "no threshold", so none of these
# limits can ever be exceeded and the build can never be marked unstable or
# failed. Preserved on purpose under Rule T4; docs/migration-parity.md records
# the switch that opts into a fix. No assertion below expects a positive limit.
# =============================================================================


def test_publisher_thresholds_is_a_read_only_mapping(thresholds: ModuleType) -> None:
    """``PUBLISHER_THRESHOLDS`` is the coordinated export, and it is genuinely frozen.

    The name is the one the migration plan fixes, as the replacement for the Groovy
    publisher call's inline thresholds ``[Jenkins:L15]``, and ``app/services/report_service``
    consumes it under exactly that name.

    Read-only matters beyond tidiness: ``Final`` only forbids rebinding the name, so a plain
    dictionary would still accept item assignment at run time, and this mapping is reachable
    from the configuration-introspection endpoint. A careless handler could otherwise mutate
    the parity contract in-process for every later request.
    """
    mapping: Mapping[str, int] = _symbol(thresholds, _MAPPING_SYMBOL)

    assert isinstance(mapping, Mapping), (
        f"{_MAPPING_SYMBOL} should be a mapping of the publisher's named threshold "
        f"parameters, found {type(mapping).__name__}"
    )
    with pytest.raises(TypeError):
        mapping["failedStepsNumber"] = 0  # type: ignore[index]
    assert mapping["failedStepsNumber"] == _SOURCE_THRESHOLD_VALUE, (
        f"{_MAPPING_SYMBOL} accepted a mutation, so it is not frozen: "
        f"[{_JENKINS_FILE}:L{_JENKINS_PUBLISHER_LINE}] declares -1"
    )


def test_publisher_thresholds_holds_exactly_six_entries(thresholds: ModuleType) -> None:
    """Exactly six thresholds are published -- no more, and none missing.

    ``[Jenkins:L15]`` passes eight named parameters, of which six are thresholds; the other
    two are the include pattern and the sort order, which are separate constants and are
    asserted separately. Six is therefore the exact count, and a seventh entry would be an
    invention rather than a value carried across.
    """
    mapping: Mapping[str, int] = _symbol(thresholds, _MAPPING_SYMBOL)

    assert len(mapping) == len(_SOURCE_THRESHOLD_KEYS), (
        f"{_MAPPING_SYMBOL} should hold the {len(_SOURCE_THRESHOLD_KEYS)} thresholds of "
        f"[{_JENKINS_FILE}:L{_JENKINS_PUBLISHER_LINE}], found {len(mapping)}: "
        f"{sorted(mapping)}"
    )


def test_publisher_threshold_keys_are_the_source_names_in_source_order(
    thresholds: ModuleType,
) -> None:
    """The keys are the publisher's own camelCase names, in the order it lists them.

    They are the publisher's vocabulary carried across as data, not Python identifiers, so
    they are never re-cased to snake_case (Rule T1: no value is rounded, renamed or
    modernised). Preserving the order as well is what makes the mapping auditable against
    ``[Jenkins:L15]`` by reading the two side by side.
    """
    mapping: Mapping[str, int] = _symbol(thresholds, _MAPPING_SYMBOL)

    assert tuple(mapping) == _SOURCE_THRESHOLD_KEYS, (
        f"{_MAPPING_SYMBOL} should be keyed by the camelCase parameter names of "
        f"[{_JENKINS_FILE}:L{_JENKINS_PUBLISHER_LINE}] in their source order "
        f"{_SOURCE_THRESHOLD_KEYS}, found {tuple(mapping)}"
    )


def test_every_publisher_threshold_is_the_integer_minus_one(thresholds: ModuleType) -> None:
    """Every threshold is the integer ``-1``: not ``"-1"``, not ``None``, not ``0``.

    The exact type is checked with ``type(value) is int`` rather than ``isinstance``, and
    that is not pedantry: ``bool`` is a subclass of ``int`` in Python, so ``False`` would
    satisfy a naive ``isinstance(value, int)`` check while meaning something entirely
    different to a report publisher.

    ``-1`` is the publisher's "no threshold" -- preserved defect D3, never a limit of one.
    """
    mapping: Mapping[str, int] = _symbol(thresholds, _MAPPING_SYMBOL)

    for key, value in mapping.items():
        assert type(value) is int, (
            f"{_MAPPING_SYMBOL}[{key!r}] should be an int, found "
            f"{type(value).__name__} ({value!r}); note that bool is a subclass of int and "
            f"is not acceptable here"
        )
        assert value == _SOURCE_THRESHOLD_VALUE, (
            f"{_MAPPING_SYMBOL}[{key!r}] should be {_SOURCE_THRESHOLD_VALUE}, the "
            f'"no threshold" value of [{_JENKINS_FILE}:L{_JENKINS_PUBLISHER_LINE}], found '
            f"{value!r}; preserved defect D3 is never corrected -- see "
            f"docs/migration-parity.md"
        )


def test_publisher_threshold_keys_tuple_matches_the_mapping(thresholds: ModuleType) -> None:
    """``PUBLISHER_THRESHOLD_KEYS`` is an immutable tuple agreeing with the mapping.

    The module publishes the six key strings separately so consumers can iterate the
    publisher's parameter names without depending on a mapping's iteration order. A tuple
    rather than a list, because it is a constant, and equal to the mapping's own order so
    the two can never disagree.
    """
    mapping: Mapping[str, int] = _symbol(thresholds, _MAPPING_SYMBOL)
    keys: tuple[str, ...] = _symbol(thresholds, _KEYS_SYMBOL)

    assert isinstance(keys, tuple), f"{_KEYS_SYMBOL} should be a tuple, found {type(keys).__name__}"
    assert keys == _SOURCE_THRESHOLD_KEYS, (
        f"{_KEYS_SYMBOL} should be the parameter names of "
        f"[{_JENKINS_FILE}:L{_JENKINS_PUBLISHER_LINE}] in source order "
        f"{_SOURCE_THRESHOLD_KEYS}, found {keys}"
    )
    assert keys == tuple(
        mapping
    ), f"{_KEYS_SYMBOL} and {_MAPPING_SYMBOL} disagree: {keys} against {tuple(mapping)}"


@pytest.mark.parametrize(("symbol", "parameter"), _THRESHOLD_CONSTANT_SYMBOLS)
def test_individually_exported_threshold_matches_the_mapping(
    thresholds: ModuleType, symbol: str, parameter: str
) -> None:
    """Each separately exported threshold constant is ``-1`` and agrees with the mapping.

    The module exports the six values individually as well as collectively, so a consumer
    that wants one number does not have to index a mapping with a camelCase string. Both
    forms have to carry the same value, or one of them is wrong and the codebase has two
    answers for a single source literal.

    Args:
        thresholds: The imported constants module.
        symbol: The exported constant's Python name.
        parameter: The publisher parameter ``[Jenkins:L15]`` it carries.
    """
    mapping: Mapping[str, int] = _symbol(thresholds, _MAPPING_SYMBOL)
    value: int = _symbol(thresholds, symbol)

    assert (
        type(value) is int
    ), f"{symbol} should be an int, found {type(value).__name__} ({value!r})"
    assert value == _SOURCE_THRESHOLD_VALUE, (
        f"{symbol} carries {parameter} of [{_JENKINS_FILE}:L{_JENKINS_PUBLISHER_LINE}] and "
        f"should be {_SOURCE_THRESHOLD_VALUE}, found {value!r}"
    )
    assert value == mapping[parameter], (
        f"{symbol} and {_MAPPING_SYMBOL}[{parameter!r}] disagree: {value!r} against "
        f"{mapping[parameter]!r}"
    )


# =============================================================================
# REQUIREMENT 2 -- the sort order.
#
# [Jenkins:L15] sortingMethod: 'ALPHABETICAL'
# =============================================================================


def test_report_sorting_method_is_the_literal_alphabetical(thresholds: ModuleType) -> None:
    """The sort order is the exact string ``ALPHABETICAL``.

    Upper case, no underscores, no ``Alphabetical``, and no enumeration repr leaking into a
    value the publisher will read: the constant is compared for exact string equality
    against ``[Jenkins:L15]``. An enumeration member is tolerated only if its ``.value`` is
    that same literal, which is why the value is normalised before comparison rather than
    the comparison being loosened.

    It must exist even though it is a **no-op today**. With a single feature there is
    nothing to sort, but leaving the setting out would turn a preserved value into a
    divergence that appears the moment a second feature is authored. Sorting features by
    name before rendering is ``app/services/report_service.py``'s behaviour; this constant
    is only the request for it.
    """
    sorting: object = _symbol(thresholds, _SORTING_SYMBOL)
    value = sorting.value if isinstance(sorting, Enum) else sorting

    assert isinstance(value, str), (
        f"{_SORTING_SYMBOL} should be a string, found {type(sorting).__name__} " f"({sorting!r})"
    )
    assert value == _SOURCE_SORTING_METHOD, (
        f"{_SORTING_SYMBOL} should be {_SOURCE_SORTING_METHOD!r} exactly, as declared by "
        f"sortingMethod of [{_JENKINS_FILE}:L{_JENKINS_PUBLISHER_LINE}], found {value!r}"
    )


# =============================================================================
# REQUIREMENT 3 -- the report include pattern, preserved AS DATA.
#
# [Jenkins:L15] fileIncludePattern: '**/*.json'
#
# The one leading-wildcard string that survives into the Python tree. It is a
# value handed to the CI publisher, never an instruction acted on here, so it is
# never rewritten -- and never evaluated. Nothing in this module passes it to
# Path.glob, fnmatch, re.compile or PurePath.match; no such module is imported.
# =============================================================================


def test_report_file_include_pattern_is_the_literal_double_star_json(
    thresholds: ModuleType,
) -> None:
    """The include pattern is the exact string ``**/*.json``.

    Not ``*.json``, not ``**/**.json``, not ``target/**/*.json``, not a compiled regular
    expression and not a path object: a string literal, carried verbatim as data from
    ``[Jenkins:L15]``.

    It keeps working unedited because the port still writes its reports underneath the
    artifact root whose Java-flavoured name is preserved -- the concrete payoff of retaining
    that name, and the reason the pipeline's publisher configuration needed no change at
    all. The root itself is asserted by ``tests/unit/test_paths.py``.

    This test compares the string and does nothing else with it. Expanding it against the
    filesystem here would be the very rewriting the migration forbids.
    """
    pattern: object = _symbol(thresholds, _PATTERN_SYMBOL)

    assert isinstance(pattern, str), (
        f"{_PATTERN_SYMBOL} should be a plain string carried as data, found "
        f"{type(pattern).__name__} ({pattern!r})"
    )
    assert pattern == _SOURCE_FILE_INCLUDE_PATTERN, (
        f"{_PATTERN_SYMBOL} should be {_SOURCE_FILE_INCLUDE_PATTERN!r} exactly, as declared "
        f"by fileIncludePattern of [{_JENKINS_FILE}:L{_JENKINS_PUBLISHER_LINE}], found "
        f"{pattern!r}; it is preserved verbatim as data and must never be rewritten"
    )


# =============================================================================
# REQUIREMENT 4 -- failure tolerance resolves to true.
#
# [pom.xml:L25] <testFailureIgnore>true</testFailureIgnore>
#
# The other half of preserved defect D3. While this is true the ported runner
# treats pytest exit code 1 as non-failing and exit code 5 as a successful
# zero-scenario run; that mapping lives in app/services/test_runner_service.py
# and is criterion V6's, not this module's.
# =============================================================================


def test_failure_tolerance_default_is_true(config_module: ModuleType) -> None:
    """The hard-coded failure-tolerance default is boolean ``True``.

    This is the last rung of the precedence chain, the value no environment can reach, and
    it is the direct port of ``<testFailureIgnore>true</testFailureIgnore>``
    ``[pom.xml:L25]``. Boolean ``True`` rather than the string ``"true"``: the source
    declaration is XML text, and coercing it once at the boundary is what keeps every
    consumer from re-interpreting it.

    Preserved defect D3 again -- the source build swallowed test failures outright, and so
    does the port. ``docs/migration-parity.md`` records the switch that opts into gating,
    which is a deliberate deviation from source behaviour rather than a fix.
    """
    default: object = _symbol(config_module, _FAILURE_TOLERANCE_DEFAULT_SYMBOL)

    assert default is _SOURCE_FAILURE_TOLERANCE, (
        f"{_FAILURE_TOLERANCE_DEFAULT_SYMBOL} should be {_SOURCE_FAILURE_TOLERANCE!r}, the "
        f"port of {_SOURCE_FAILURE_TOLERANCE_ELEMENT} "
        f"[{_POM_FILE}:L{_POM_FAILURE_TOLERANCE_LINE}], found {default!r}"
    )


def test_failure_tolerance_resolves_to_true(
    request: pytest.FixtureRequest, config_module: ModuleType
) -> None:
    """Failure tolerance resolves to ``True`` in a real application's configuration.

    Asserted through the resolved mapping the application factory populated -- the
    ``config`` fixture of ``tests/conftest.py`` -- rather than through a class attribute, so
    what is checked is the value the running service would actually use, resolved through
    the whole precedence chain.

    An environment that legitimately overrides the setting is skipped rather than failed,
    with the responsible rung named: the override would be the documented precedence chain
    working correctly, not a parity defect. The unconditional evidence for
    ``[pom.xml:L25]`` is the hard-coded default asserted above.
    """
    _skip_unless_source_default(config_module, _FAILURE_TOLERANCE_KEY)
    config = _resolved_config(request)

    assert config[_FAILURE_TOLERANCE_KEY] is _SOURCE_FAILURE_TOLERANCE, (
        f"the resolved {_FAILURE_TOLERANCE_KEY} should be {_SOURCE_FAILURE_TOLERANCE!r}, the "
        f"port of {_SOURCE_FAILURE_TOLERANCE_ELEMENT} "
        f"[{_POM_FILE}:L{_POM_FAILURE_TOLERANCE_LINE}], found "
        f"{config[_FAILURE_TOLERANCE_KEY]!r}"
    )


def test_resolved_configuration_serves_the_frozen_publisher_constants(
    request: pytest.FixtureRequest, thresholds: ModuleType
) -> None:
    """A resolved configuration serves exactly the frozen publisher constants.

    The configuration module imports these values from the constants module instead of
    restating them, so with nothing overridden the two must agree exactly. Checking that
    here is what proves the single-source-of-truth arrangement actually holds end to end:
    the numbers an HTTP client would read back from the configuration endpoint are the same
    numbers ``[Jenkins:L15]`` declares.
    """
    config = _resolved_config(request)
    mapping: Mapping[str, int] = _symbol(thresholds, _MAPPING_SYMBOL)
    sorting: object = _symbol(thresholds, _SORTING_SYMBOL)
    pattern: object = _symbol(thresholds, _PATTERN_SYMBOL)

    assert dict(config[_MAPPING_SYMBOL]) == dict(mapping), (
        f"the resolved {_MAPPING_SYMBOL} should equal the frozen mapping of "
        f"{_THRESHOLDS_MODULE_NAME}, found {dict(config[_MAPPING_SYMBOL])} against "
        f"{dict(mapping)}"
    )
    assert config[_SORTING_SYMBOL] == sorting, (
        f"the resolved {_SORTING_SYMBOL} should equal {sorting!r} "
        f"[{_JENKINS_FILE}:L{_JENKINS_PUBLISHER_LINE}], found {config[_SORTING_SYMBOL]!r}"
    )
    assert config[_PATTERN_SYMBOL] == pattern, (
        f"the resolved {_PATTERN_SYMBOL} should equal {pattern!r} "
        f"[{_JENKINS_FILE}:L{_JENKINS_PUBLISHER_LINE}], found {config[_PATTERN_SYMBOL]!r}"
    )


# =============================================================================
# REQUIREMENT 5 -- the constants module cannot cause a failure.
#
# The behavioural half of this -- that the 'Generate report' stage
# [Jenkins:L14] still runs after a FAILED test stage, because
# app/services/pipeline_service.py invokes it from a finally-equivalent path --
# belongs to tests/integration/test_pipeline_service.py and criterion V12. What
# is checked here is the unit-granularity claim: the module holds constants and
# nothing else, so no threshold can be turned into a verdict inside it.
# =============================================================================


def test_thresholds_module_contains_no_gating_logic(project_root: Path) -> None:
    """The constants module stores values and never evaluates them.

    No ``raise``, no ``assert``, no process exit, no function and no class: there is
    deliberately no severity ranking, no limit-exceeded calculation and no "must this fail
    the build" helper anywhere in it. Build-failure gating is out of scope for the whole
    migration and stays switched off, which is exactly what preserves defect D3.

    Checked on the syntax tree rather than in the text on purpose. The module's own
    documentation contains the word "raises" -- explaining that its frozen mapping rejects
    item assignment -- and a text search would report that prose as gating logic.

    Args:
        project_root: Absolute repository root, from ``tests/conftest.py``.
    """
    tree = _parse_module_source(project_root, _THRESHOLDS_MODULE_FILE)

    offenders = [
        type(node).__name__
        for node in ast.walk(tree)
        if isinstance(node, ast.Raise | ast.Assert | ast.FunctionDef | ast.AsyncFunctionDef)
    ]
    assert not offenders, (
        f"{_THRESHOLDS_MODULE_FILE} should hold constants only, found {offenders}; gating a "
        f"build on a threshold is deliberately absent everywhere -- preserved defect "
        f"D3, see docs/migration-parity.md"
    )
    assert not any(
        isinstance(node, ast.ClassDef) for node in ast.walk(tree)
    ), f"{_THRESHOLDS_MODULE_FILE} should define no class; it is a frozen constants module"


# =============================================================================
# REQUIREMENT 6 -- layering, inertness and single source of truth.
#
# The application's dependency direction is fixed at
# api -> services -> reporting -> utils, and app/config.py imports the constants
# module, so anything imported back out of it would close a cycle that fails at
# application start-up. Nothing under app/ may import from tests/.
# =============================================================================


def test_thresholds_module_imports_only_the_standard_library(project_root: Path) -> None:
    """The constants module imports nothing but the standard library.

    No ``app.api``, no ``app.services``, no ``app.web``, no ``app.utils``, no ``app``
    package root, no ``tests``, no ``scripts``, no relative import and no third-party
    distribution -- not even Flask. That is what keeps it importable in a container built
    from the runtime requirements alone, along the deployed chain ``wsgi.py`` ->
    ``app/__init__.py`` -> ``app/config.py`` -> this module.

    Args:
        project_root: Absolute repository root, from ``tests/conftest.py``.
    """
    imported = _imported_names(_parse_module_source(project_root, _THRESHOLDS_MODULE_FILE))

    relative = [name for name in imported if name.startswith(".")]
    assert not relative, (
        f"{_THRESHOLDS_MODULE_FILE} should use no relative import, found {relative}; an "
        f"intra-package import would reach application code and close a dependency cycle"
    )
    for name in imported:
        root = name.split(".")[0]
        assert root not in _FORBIDDEN_IMPORT_ROOTS, (
            f"{_THRESHOLDS_MODULE_FILE} imports {name!r}, which is forbidden: the module "
            f"must reach neither application code, the test tree, the scripts tree nor any "
            f"third-party distribution"
        )
        assert root in sys.stdlib_module_names, (
            f"{_THRESHOLDS_MODULE_FILE} imports {name!r}, which is not part of the standard "
            f"library; a frozen constants module needs nothing else"
        )


def test_thresholds_module_neither_prints_nor_configures_logging(project_root: Path) -> None:
    """The constants module produces no output and configures no logging.

    Structured logging is configured in exactly one place, ``app/logging_config.py``, and a
    constants module that reconfigured it would silently outrank the application's own
    settings for every consumer that imported it. Console output is not an alternative:
    there is no ``print`` anywhere in the application tree.

    The module is inert on import for the same reason -- nothing is read, created or
    inspected when it loads -- so the calls it is allowed to make are only those that build
    its own frozen containers.

    Args:
        project_root: Absolute repository root, from ``tests/conftest.py``.
    """
    called = _called_names(_parse_module_source(project_root, _THRESHOLDS_MODULE_FILE))

    for name in called:
        assert name not in _FORBIDDEN_CALL_NAMES, (
            f"{_THRESHOLDS_MODULE_FILE} calls {name}(), which a frozen constants module must "
            f"not: it produces no output, configures no logging, exits no process and "
            f"performs no I/O on import"
        )
        assert name.split(".")[-1] not in _FORBIDDEN_CALL_NAMES, (
            f"{_THRESHOLDS_MODULE_FILE} calls {name}(), whose final attribute is forbidden "
            f"for a frozen constants module"
        )


def test_thresholds_module_declares_its_constants_final(project_root: Path) -> None:
    """Every published constant is annotated ``Final``.

    ``Final`` is what makes the module's contract checkable: it forbids rebinding the name,
    so a later edit that reassigned one of these values is a type error rather than a silent
    change to a parity constant. The containers additionally have to be immutable at run
    time, which the mapping's read-only nature and the key tuple provide, because ``Final``
    alone would still permit item assignment.

    Args:
        project_root: Absolute repository root, from ``tests/conftest.py``.
    """
    annotations = _annotations_by_name(_parse_module_source(project_root, _THRESHOLDS_MODULE_FILE))

    for symbol in _FINAL_CONSTANT_SYMBOLS:
        assert symbol in annotations, (
            f"{_THRESHOLDS_MODULE_FILE} should declare {symbol} as an annotated "
            f"module-level constant, found none"
        )
        assert annotations[symbol].startswith("Final"), (
            f"{_THRESHOLDS_MODULE_FILE} should annotate {symbol} as Final so the name cannot "
            f"be rebound, found {annotations[symbol]!r}"
        )


def test_thresholds_module_declares_no_artifact_path(project_root: Path) -> None:
    """The constants module declares no filesystem path, and names no artifact root.

    The split of responsibility is deliberate and neither side repeats the other: this
    module owns the publisher constants, while ``app/utils/paths.py`` owns the artifact-root
    layout and the location of every report file. A path constant here would be a second
    place to change when the layout moves, and the include pattern's continued correctness
    depends on that layout being owned in one place.

    Args:
        project_root: Absolute repository root, from ``tests/conftest.py``.
    """
    tree = _parse_module_source(project_root, _THRESHOLDS_MODULE_FILE)

    for name in _imported_names(tree):
        assert name.split(".")[0] not in _PATH_IMPORT_ROOTS, (
            f"{_THRESHOLDS_MODULE_FILE} imports the path machinery {name!r}; composing a "
            f"filesystem path is app/utils/paths.py's job, not this module's"
        )
    for literal in _assigned_literals(tree):
        assert not (isinstance(literal, str) and _ARTIFACT_ROOT_NAME in literal), (
            f"{_THRESHOLDS_MODULE_FILE} assigns the literal {literal!r}, which names the "
            f"artifact root; the layout belongs to app/utils/paths.py alone"
        )


def test_publisher_constants_are_not_redeclared_in_the_paths_module(project_root: Path) -> None:
    """The publisher settings exist in exactly one module.

    Several artifacts have to agree on these eight values -- the pipeline definition and its
    discoverable alias, the two committed configuration templates, the application
    configuration and the service that reproduces the publication step. They agree by
    importing them, never by restating them, so no value can drift in one place while
    staying correct in another.

    ``app/utils/paths.py`` is the module most likely to acquire a copy, because it owns the
    report locations the publisher's glob has to keep matching. Its docstring quotes
    ``[Jenkins:L15]`` for provenance, which is correct and is why only *assigned* values are
    inspected here: a citation in prose is documentation, whereas an assignment would be a
    second declaration.

    Args:
        project_root: Absolute repository root, from ``tests/conftest.py``.
    """
    tree = _parse_module_source(project_root, _PATHS_MODULE_FILE)
    literals = _assigned_literals(tree)
    keys = _assigned_mapping_keys(tree)

    for forbidden in (_SOURCE_SORTING_METHOD, _SOURCE_FILE_INCLUDE_PATTERN):
        assert forbidden not in literals, (
            f"{_PATHS_MODULE_FILE} assigns {forbidden!r}, which is declared by "
            f"{_THRESHOLDS_MODULE_FILE}; the publisher settings must exist in exactly one "
            f"place and be imported from it"
        )
    for key in _SOURCE_THRESHOLD_KEYS:
        assert key not in keys, (
            f"{_PATHS_MODULE_FILE} declares a mapping keyed by the publisher parameter "
            f"{key!r}; the six thresholds belong to {_THRESHOLDS_MODULE_FILE} alone"
        )


# =============================================================================
# TWO-SIDED PARITY -- the source artifacts are read and compared.
#
# Baseline B11: executable parity evidence rather than assertions of parity. The
# tests above compare the port against this module's transcription of the source;
# the tests below re-derive the same values from the committed files themselves,
# so a divergence in either direction fails the run.
#
# Both files are read only. The build descriptor is reference material for the
# whole migration -- never compiled, never edited -- and the pipeline definition
# is modified in this migration only in its two command strings.
# =============================================================================


def test_jenkins_publisher_statement_declares_the_eight_source_parameters(
    project_root: Path,
) -> None:
    """``Jenkins`` still declares the publisher call this module exists to defend.

    Standard library only, so this evidence survives an environment in which the application
    cannot be imported at all. It checks the source side of the comparison: eight named
    parameters in the publisher's own order, six of them the raw token ``-1``, the include
    pattern and the sort order carrying their literals.

    The ``-1`` count is asserted directly as well. Six occurrences is the number that makes
    the build unable to fail -- preserved defect D3 -- so counting them is the most direct
    statement of the source fact this module preserves.

    Args:
        project_root: Absolute repository root, from ``tests/conftest.py``.
    """
    _, statement = _publisher_statement(_read_source(project_root, _JENKINS_FILE))
    arguments = _publisher_arguments(statement)

    assert statement.count(_SOURCE_THRESHOLD_TOKEN) == len(_SOURCE_THRESHOLD_KEYS), (
        f"the publisher statement of [{_JENKINS_FILE}:L{_JENKINS_PUBLISHER_LINE}] should "
        f'carry {len(_SOURCE_THRESHOLD_KEYS)} occurrences of the "no threshold" token '
        f"{_SOURCE_THRESHOLD_TOKEN!r}, found "
        f"{statement.count(_SOURCE_THRESHOLD_TOKEN)}"
    )
    assert tuple(arguments) == _SOURCE_PARAMETER_ORDER, (
        f"the publisher statement of [{_JENKINS_FILE}:L{_JENKINS_PUBLISHER_LINE}] should name "
        f"{_SOURCE_PARAMETER_ORDER}, found {tuple(arguments)}"
    )
    for key in _SOURCE_THRESHOLD_KEYS:
        assert arguments[key] == _SOURCE_THRESHOLD_TOKEN, (
            f"{key} of [{_JENKINS_FILE}:L{_JENKINS_PUBLISHER_LINE}] should be the token "
            f"{_SOURCE_THRESHOLD_TOKEN!r}, found {arguments[key]!r}"
        )
    assert arguments["sortingMethod"] == _SOURCE_SORTING_METHOD, (
        f"sortingMethod of [{_JENKINS_FILE}:L{_JENKINS_PUBLISHER_LINE}] should be "
        f"{_SOURCE_SORTING_METHOD!r}, found {arguments['sortingMethod']!r}"
    )
    assert arguments["fileIncludePattern"] == _SOURCE_FILE_INCLUDE_PATTERN, (
        f"fileIncludePattern of [{_JENKINS_FILE}:L{_JENKINS_PUBLISHER_LINE}] should be "
        f"{_SOURCE_FILE_INCLUDE_PATTERN!r}, found {arguments['fileIncludePattern']!r}"
    )


def test_jenkins_publisher_statement_is_still_the_cited_line(project_root: Path) -> None:
    """The publisher statement still occupies line 15, so every citation here is literal.

    The migration changes exactly two things in the pipeline definition: the two
    ``mvn clean test`` command strings of its test stage. Its container, all three stage
    names, the platform dispatch and the whole publisher invocation stay byte-identical, so
    the line numbering is preserved and the ``[Jenkins:L15]`` citation carried by every
    assertion in this module remains a fact rather than an approximation.

    Args:
        project_root: Absolute repository root, from ``tests/conftest.py``.
    """
    line_number, _ = _publisher_statement(_read_source(project_root, _JENKINS_FILE))

    assert line_number == _JENKINS_PUBLISHER_LINE, (
        f"the publisher statement should occupy line {_JENKINS_PUBLISHER_LINE} of "
        f"{_JENKINS_FILE}, found it on line {line_number}; only the two 'mvn clean test' "
        f"command strings may change in this migration, so its position is part of the "
        f"preserved contract and every [{_JENKINS_FILE}:L{_JENKINS_PUBLISHER_LINE}] citation "
        f"depends on it"
    )


def test_python_constants_match_the_jenkins_publisher_statement(
    project_root: Path, thresholds: ModuleType
) -> None:
    """The Python constants equal the values extracted from ``Jenkins`` itself.

    This is the comparison criterion V4 ultimately rests on, and it is genuinely two-sided:
    the expected values are parsed out of the committed pipeline definition at run time and
    compared with the constants the application actually serves. Neither side is derived
    from the other, so the test cannot pass by tautology.

    The include pattern is compared as a string here, exactly as everywhere else in this
    module. It is never expanded, compiled or matched against anything.

    Args:
        project_root: Absolute repository root, from ``tests/conftest.py``.
        thresholds: The imported constants module.
    """
    _, statement = _publisher_statement(_read_source(project_root, _JENKINS_FILE))
    arguments = _publisher_arguments(statement)
    mapping: Mapping[str, int] = _symbol(thresholds, _MAPPING_SYMBOL)
    sorting: object = _symbol(thresholds, _SORTING_SYMBOL)
    pattern: object = _symbol(thresholds, _PATTERN_SYMBOL)

    for key, value in mapping.items():
        assert key in arguments, (
            f"{_MAPPING_SYMBOL} carries {key!r}, which "
            f"[{_JENKINS_FILE}:L{_JENKINS_PUBLISHER_LINE}] does not name; the mapping must "
            f"carry the publisher's parameters and nothing invented"
        )
        assert value == int(arguments[key]), (
            f"{_MAPPING_SYMBOL}[{key!r}] is {value!r} but "
            f"[{_JENKINS_FILE}:L{_JENKINS_PUBLISHER_LINE}] declares "
            f"{arguments[key]!r}"
        )
    assert sorting == arguments["sortingMethod"], (
        f"{_SORTING_SYMBOL} is {sorting!r} but "
        f"[{_JENKINS_FILE}:L{_JENKINS_PUBLISHER_LINE}] declares "
        f"{arguments['sortingMethod']!r}"
    )
    assert pattern == arguments["fileIncludePattern"], (
        f"{_PATTERN_SYMBOL} is {pattern!r} but "
        f"[{_JENKINS_FILE}:L{_JENKINS_PUBLISHER_LINE}] declares "
        f"{arguments['fileIncludePattern']!r}"
    )


def test_pom_declares_failure_tolerance_true(project_root: Path) -> None:
    """``pom.xml`` still declares ``<testFailureIgnore>true</testFailureIgnore>`` at line 25.

    Standard library only, and a static text read: the build descriptor is read-only
    reference material for this migration -- never compiled, never edited, and requiring no
    Java toolchain to inspect. Its declared value is decoded through a closed set of
    spellings rather than a permissive truthiness test, because anything other than ``true``
    or ``false`` there would be a defect rather than a value to guess at.

    Args:
        project_root: Absolute repository root, from ``tests/conftest.py``.
    """
    pom_text = _read_source(project_root, _POM_FILE)
    line_number = _element_line(pom_text, _SOURCE_FAILURE_TOLERANCE_ELEMENT)

    assert line_number == _POM_FAILURE_TOLERANCE_LINE, (
        f"{_SOURCE_FAILURE_TOLERANCE_ELEMENT} should occupy line "
        f"{_POM_FAILURE_TOLERANCE_LINE} of {_POM_FILE}, found it on line {line_number}; the "
        f"build descriptor is read-only reference material and is never edited"
    )
    opening, _, remainder = _SOURCE_FAILURE_TOLERANCE_ELEMENT.partition(">")
    declared = remainder.partition("<")[0]
    assert _XML_BOOLEANS[declared] is _SOURCE_FAILURE_TOLERANCE, (
        f"{opening}> of [{_POM_FILE}:L{_POM_FAILURE_TOLERANCE_LINE}] should declare "
        f"{_SOURCE_FAILURE_TOLERANCE!r}, found {declared!r}"
    )


def test_configured_failure_tolerance_matches_the_pom_declaration(
    project_root: Path, config_module: ModuleType
) -> None:
    """The configured default equals the value declared in ``pom.xml`` itself.

    The second two-sided comparison, and the other half of preserved defect D3: the boolean
    the port defaults to is decoded from the build descriptor's own text at run time, so the
    port and its source are compared rather than the port being compared with a restatement
    of itself.

    Args:
        project_root: Absolute repository root, from ``tests/conftest.py``.
        config_module: The imported configuration module.
    """
    pom_text = _read_source(project_root, _POM_FILE)
    _element_line(pom_text, _SOURCE_FAILURE_TOLERANCE_ELEMENT)
    declared = _SOURCE_FAILURE_TOLERANCE_ELEMENT.partition(">")[2].partition("<")[0]
    default: object = _symbol(config_module, _FAILURE_TOLERANCE_DEFAULT_SYMBOL)

    assert default is _XML_BOOLEANS[declared], (
        f"{_FAILURE_TOLERANCE_DEFAULT_SYMBOL} is {default!r} but "
        f"[{_POM_FILE}:L{_POM_FAILURE_TOLERANCE_LINE}] declares {declared!r}; failure "
        f"tolerance is preserved defect D3 and is never quietly corrected -- see "
        f"docs/migration-parity.md"
    )
