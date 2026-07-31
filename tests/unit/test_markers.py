"""Marker registration and warning hygiene for the ported BDD harness.

This module is the executable evidence for validation criterion **V10** -- *marker and
warning hygiene is clean* -- and for the engineering baseline that criterion enforces:
*registered pytest markers and explicit warning filters so nothing is silently swallowed*.
It owns one narrow contract and asserts it from every angle that can fail:

1. Every Gherkin tag in ``tests/features/login.feature`` is declared as a pytest marker in
   ``pytest.ini``, and nothing else is declared as a Gherkin-derived marker.
2. A full run therefore emits **zero** ``PytestUnknownMarkWarning``.
3. The warning filter list stays explicit enough that a genuinely new warning still
   surfaces.

Why registration is load-bearing
================================

pytest-bdd turns every Gherkin tag into a ``pytest.mark`` attribute and strips the leading
``@``, so the six tags of the materialised specification arrive as the marks ``Login``,
``UPGN-286``, ``UPGN-287``, ``UPGN-288``, ``SalesManager`` and ``PosManager``. An
undeclared mark raises ``PytestUnknownMarkWarning`` once per application: eight warnings in
a serial run, and one hundred and twenty-eight under the default ``-n logical``
parallelism, because every xdist worker is a separate process that warns independently.
Those two counts are the regression this module exists to prevent. Both are reproduced
here on purpose -- the negative control deliberately unregisters the marks in a throwaway
nested run and asserts the warnings come back -- so the positive control cannot pass
vacuously.

Registration is also the whole of the traceability port. The three Jira issue keys are
carried across as marker names and nothing else: no Jira client is introduced, because the
source project never had one and adding one would add behaviour rather than preserve it.
The hyphens in those keys are part of the issue key. They are legal in the ``markers``
list, they stay selectable (``pytest -m "UPGN-288"``), and they must never be rewritten to
underscores -- all three properties are asserted below rather than assumed.

Two measured traps this module is written around
================================================

**The phantom seventh tag.** Harvesting tags with a general pattern over the whole feature
file finds a seventh, ``info.com``, scavenged from the e-mail addresses in the Examples
tables (``salesmanager7@info.com`` and friends); a word-only pattern instead finds five and
truncates every Jira key to ``UPGN``. Gherkin permits a tag only on a line of its own,
immediately preceding a ``Feature``, ``Scenario``, ``Scenario Outline`` or ``Examples``
keyword, so :func:`harvest_gherkin_tags` recognises tags **only** on lines whose stripped
content begins with the tag prefix. That yields exactly the correct six. The module
imports no regular-expression machinery at all, which keeps the guard obvious by
inspection.

**The registered-marker superset.** ``pytestconfig.getini("markers")`` does not return the
declared list: it returns the declared names plus pytest's own built-ins
(``filterwarnings``, ``skip``, ``skipif``, ``xfail``, ``parametrize``, ``usefixtures``,
``tryfirst``, ``trylast``) plus whatever markers the installed plugins contribute, which
varies by machine. Every assertion against it is therefore containment, never equality;
the authoritative six come from parsing ``pytest.ini`` directly.

Scope, and what is deliberately left to its owner
=================================================

``pytest.ini`` and ``pyproject.toml`` are asserted against, never authored or edited here,
and the same is true of the feature file -- it is read only to harvest its tags. Its bytes
are the subject of the byte-parity suite, its collection count and the unreachable
scenario belong to the defect-preservation suite, the exit-code mapping to the default-run
suite, the publisher thresholds and the artifact layout to their own unit suites, and the
parallel-run report contents to the parallel-execution suite. Nothing here duplicates
them.

The default selector is likewise preserved, not corrected. ``addopts`` carries
``-m "LogOut"``, the faithful port of the documented runner's ``tags = "@LogOut"``
``[README.md:L87]``, and no scenario carries that tag, so the documented run selects
nothing. That is a preserved defect and this module asserts it stays that way; widening
the selector to make the suite "do something" would break the parity contract.

Environment tolerance
=====================

The module needs the standard library and pytest, nothing more. It never imports the
application package -- doing so would execute the Flask application factory and drag a
third-party dependency into a session that has no use for it -- and it never imports
pytest-bdd. Every check that depends on a file or an interpreter feature degrades to a
skip carrying the reason, so the module is importable and collectable under a bare
interpreter and can never turn a missing dependency into a collection error.

Provenance
==========

There is no counterpart to this file in the source project: that project is a Java/Maven
Selenium-Cucumber skeleton whose only tracked files are its build descriptor, its pipeline
definition, its ignore rules and its README, and it never contained a committed test tree.
``README.md`` is cited throughout as the origin of the tag names, the tag selector and the
Gherkin specification, and ``pom.xml`` as the origin of the parallelism settings, because
they are the only behavioural specification the project possesses. No line of either is
reproduced here.
"""

from __future__ import annotations

import configparser
import os
import subprocess
import sys
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, NamedTuple

import pytest

if TYPE_CHECKING:
    # Annotations only. ``from __future__ import annotations`` keeps every annotation a
    # string, so this block never executes and the module stays free of runtime imports it
    # does not need.
    from collections.abc import Iterable, Mapping, Sequence

# Only the reusable helpers are exported. The test functions are collected by pytest from
# their names and are deliberately absent, so that importing this module from another suite
# cannot re-run them.
__all__ = [
    "GHERKIN_TAG_MARKERS",
    "WarningFilter",
    "declared_marker_names",
    "harvest_gherkin_tags",
    "import_roots",
    "ini_value_lines",
    "parse_warning_filter",
    "read_text_or_skip",
    "registry_marker_names",
]

# =============================================================================
# The specification under assertion -- values, not decisions.
# =============================================================================

# The six Gherkin tags of the materialised specification, with the leading prefix stripped
# exactly as pytest-bdd strips it. This is the complete set: the feature file carries no
# other tag, and the harvester below proves it against the file itself rather than trusting
# this constant.
GHERKIN_TAG_MARKERS: Final[frozenset[str]] = frozenset(
    {
        "Login",
        "UPGN-286",
        "UPGN-287",
        "UPGN-288",
        "SalesManager",
        "PosManager",
    }
)

# Where each tag is specified. Carried as data so the assertions can cite their source and
# so a declaration in ``pytest.ini`` that loses its citation is caught.
MARKER_SOURCE_LOCATORS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "Login": "[README.md:L104]",
        "UPGN-286": "[README.md:L115]",
        "UPGN-287": "[README.md:L123]",
        "UPGN-288": "[README.md:L131]",
        "SalesManager": "[README.md:L137]",
        "PosManager": "[README.md:L144]",
    }
)

# The three Jira issue keys, kept separately because their hyphens are the property most
# likely to be "helpfully" normalised by a later edit.
JIRA_TRACEABILITY_MARKERS: Final[tuple[str, ...]] = ("UPGN-286", "UPGN-287", "UPGN-288")

# The documented runner's tag selector, ``tags = "@LogOut"`` [README.md:L87]. It is a
# declared marker but NOT a Gherkin tag: no scenario carries it, which is precisely the
# preserved defect. It is the single legitimate exception to "every declared marker is a
# feature-file tag", and it is registered so the defect is documented rather than silent.
DEFAULT_TAG_SELECTOR: Final[str] = "LogOut"
DEFAULT_TAG_SELECTOR_LOCATOR: Final[str] = "[README.md:L87]"

# The tag prefix Gherkin uses, and the one pytest-bdd strips. Named rather than inlined so
# the line-anchored harvest reads unambiguously.
TAG_PREFIX: Final[str] = "@"

# =============================================================================
# Configuration artefacts. Read as data; never written by this module.
# =============================================================================

PYTEST_INI_FILENAME: Final[str] = "pytest.ini"
PYTEST_INI_SECTION: Final[str] = "pytest"
PYPROJECT_FILENAME: Final[str] = "pyproject.toml"
FEATURE_FILE_RELATIVE_PATH: Final[tuple[str, ...]] = ("tests", "features", "login.feature")

# The TOML table names that must not exist, so that ``pytest.ini`` cannot be silently
# superseded by packaging metadata.
FORBIDDEN_PYPROJECT_TABLE_PREFIX: Final[str] = "tool.pytest"
PYPROJECT_TOOL_TABLE: Final[str] = "tool"
PYPROJECT_FORBIDDEN_TOOL_KEY: Final[str] = "pytest"

# =============================================================================
# The default option set, and the two settings that make unknown marks impossible.
# =============================================================================

# ``--strict-markers`` upgrades an unregistered mark from a warning to a collection error.
# It is the structural reason the eight-serial and one-hundred-and-twenty-eight-parallel
# warning counts can never recur: they cannot be emitted at all.
STRICT_MARKERS_FLAG: Final[str] = "--strict-markers"

# The selector that preserves the zero-selection default run [README.md:L87], spelled
# exactly as ``addopts`` carries it.
DEFAULT_SELECTOR_ADDOPT: Final[str] = '-m "LogOut"'

# <parallel>methods</parallel> [pom.xml:L22] with
# <useUnlimitedThreads>true</useUnlimitedThreads> [pom.xml:L23].
PARALLEL_WORKERS_ADDOPT: Final[str] = "-n logical"

# The tuning default the source build left commented out,
# <!-- <threadCount>4</threadCount> --> [pom.xml:L24]. It must stay present, documented and
# DISABLED; an active entry would silently replace the unlimited-thread semantics above.
DISABLED_THREAD_COUNT_ADDOPT: Final[str] = "-n 4"
INI_COMMENT_PREFIXES: Final[tuple[str, ...]] = ("#", ";")

# The report plugins that are expressible as pytest options: json:target/cucumber.json
# [README.md:L79] and html:target/cucumber-reports.html [README.md:L78]. The rerun and
# PrettyReports plugins have no pytest flag and are produced by the reporting package, so
# they are deliberately not looked for here.
REQUIRED_REPORT_ADDOPT_PREFIXES: Final[tuple[str, ...]] = (
    "--cucumberjson",
    "--html=",
    "--self-contained-html",
)

# Mutually exclusive with xdist: pytest-bdd refuses to install this reporter once xdist is
# registered, so a run configured for parallelism aborts. It is reachable only through the
# dedicated serial target and must never appear in the default option set.
GHERKIN_TERMINAL_REPORTER_FLAG: Final[str] = "--gherkin-terminal-reporter"

# =============================================================================
# Warning hygiene.
# =============================================================================

UNKNOWN_MARK_WARNING: Final[str] = "PytestUnknownMarkWarning"

# The counts observed when the marks are not registered: one warning per mark application
# serially, and one per application per worker under parallel execution. Quoted in failure
# messages so a regression names the exact behaviour it restored.
UNREGISTERED_WARNING_COUNT_SERIAL: Final[int] = 8
UNREGISTERED_WARNING_COUNT_PARALLEL: Final[int] = 128

# The strict base policy: any warning not explicitly excepted fails the run.
STRICT_WARNING_ACTION: Final[str] = "error"
SUPPRESSING_WARNING_ACTIONS: Final[frozenset[str]] = frozenset({"ignore"})

# The one granted exception originates in the Gherkin parser, so the filter that grants it
# must name that module. Matched by substring: the exact module within the package is the
# parser's business, not this suite's.
GHERKIN_FILTER_MODULE_HINT: Final[str] = "gherkin"

# ``PytestUnknownMarkWarning`` inherits from ``PytestWarning``, which inherits from
# ``UserWarning``, which inherits from ``Warning``. An ``ignore`` naming any of those --
# or naming no category at all -- would swallow the very warning registration exists to
# prevent, so none of them may be suppressed.
MARK_SWALLOWING_CATEGORIES: Final[frozenset[str]] = frozenset(
    {
        "",
        "Warning",
        "UserWarning",
        "PytestWarning",
        UNKNOWN_MARK_WARNING,
    }
)

# =============================================================================
# Traceability without a client: the import roots that must not appear.
# =============================================================================

# Distribution roots of the Python Jira clients. Traceability is carried by the tag names
# alone, matching the source project's convention-only status, so none of these may be
# imported anywhere in the application package or the harness.
JIRA_CLIENT_IMPORT_ROOTS: Final[frozenset[str]] = frozenset(
    {
        "jira",
        "jira_client",
        "atlassian",
        "atlassian_python_api",
    }
)
SCANNED_SOURCE_ROOTS: Final[tuple[str, ...]] = ("app", "tests")
IMPORT_STATEMENT_PREFIXES: Final[tuple[str, ...]] = ("import ", "from ")

# =============================================================================
# The nested-run experiment.
# =============================================================================

# A generous ceiling rather than a tuning value: the runs below take well under a second,
# and the timeout exists so a wedged subprocess fails loudly instead of hanging a session.
NESTED_PYTEST_TIMEOUT_SECONDS: Final[float] = 300.0

# Inherited variables that would change what the nested run does. ``PYTEST_ADDOPTS`` would
# inject the parent's options, the xdist variables would leak worker identity into a
# deliberately serial run, and ``PYTHONWARNINGS`` would rewrite the very warning behaviour
# under test.
INHERITED_ENV_VARS_TO_DROP: Final[tuple[str, ...]] = (
    "PYTEST_ADDOPTS",
    "PYTEST_CURRENT_TEST",
    "PYTEST_DEBUG",
    "PYTEST_PLUGINS",
    "PYTEST_XDIST_WORKER",
    "PYTEST_XDIST_WORKER_COUNT",
    "PYTEST_XDIST_TESTRUNUID",
    "PYTHONWARNINGS",
)

# Bytecode caching is disabled for the nested runs. A stale cache left behind by a copied
# test tree has already produced one false negative in this migration's verification work,
# and a probe that can silently prove the opposite of the truth is worse than no probe.
NESTED_ENV_OVERRIDES: Final[Mapping[str, str]] = MappingProxyType({"PYTHONDONTWRITEBYTECODE": "1"})

# Each case pairs a generated test name with the marks applied to it as decorators. The
# shape mirrors the specification: the feature-level tag on every case, one Jira key per
# case, and both Examples-table tags on the third. That is eight mark applications in
# total, which is exactly the serial warning count quoted above -- the negative control
# reproduces that number rather than merely observing "some" warnings.
NESTED_PROBE_CASES: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("valid_credentials", ("Login", "UPGN-286")),
    ("invalid_credentials", ("Login", "UPGN-287")),
    ("empty_field", ("Login", "UPGN-288", "SalesManager", "PosManager")),
)
NESTED_PROBE_MODULE_NAME: Final[str] = "test_generated_marks.py"
NESTED_PROBE_SENTINEL_MARK: Final[str] = "Login"


# =============================================================================
# Helpers -- small, typed and reusable.
# =============================================================================


class WarningFilter(NamedTuple):
    """One entry of the ``filterwarnings`` list, split into its five fields.

    A warning filter is written ``action:message:category:module:lineno`` and every field
    after the action is optional. Only the action is mandatory, so the fields that were not
    supplied are represented by empty strings rather than ``None``: every consumer below
    asks "was this narrowed?", and an empty string answers that directly.

    Attributes:
        action: What to do with a matching warning -- ``error``, ``ignore``, ``default``
            and the rest of the standard vocabulary.
        message: A substring the warning's message must start with, or ``""``.
        category: The warning class name, or ``""`` for every category.
        module: A pattern the originating module must match, or ``""`` for every module.
        lineno: The originating line number, or ``""`` for every line.
    """

    action: str
    message: str
    category: str
    module: str
    lineno: str

    @property
    def suppresses(self) -> bool:
        """Whether this filter makes matching warnings disappear rather than surface."""
        return self.action in SUPPRESSING_WARNING_ACTIONS

    @property
    def is_narrowed(self) -> bool:
        """Whether the filter is limited to a specific message or a specific module.

        A filter that names neither applies to every warning of its category, which is the
        blanket suppression this suite exists to forbid.
        """
        return bool(self.message) or bool(self.module)


def parse_warning_filter(entry: str) -> WarningFilter:
    """Split one ``filterwarnings`` entry into its fields.

    The separator is a colon and there are at most five fields, so the split is bounded at
    four: any further colon belongs to the trailing field, exactly as the standard library's
    own filter parsing treats it. Missing trailing fields are padded with empty strings so
    the result is always a complete :class:`WarningFilter`.

    Args:
        entry: One line of the ``filterwarnings`` value, already stripped.

    Returns:
        The parsed filter. An empty entry yields a filter whose every field is empty, which
        the callers below reject on the action field alone.
    """
    fields = entry.split(":", 4)
    padded = (*fields, "", "", "", "")
    return WarningFilter(*padded[:5])


def ini_value_lines(value: str) -> list[str]:
    """Return the non-empty, stripped lines of a multi-line ini value.

    ``configparser`` hands a continued value back as a single string whose first line may be
    empty -- ``markers =`` followed by indented entries is the common shape in this project
    -- so both the blank lines and the indentation have to go before anything can be
    compared.

    Args:
        value: The raw value as ``configparser`` returned it.

    Returns:
        One entry per meaningful line, in declaration order.
    """
    return [line.strip() for line in value.splitlines() if line.strip()]


def declared_marker_names(raw_markers: str) -> list[str]:
    """Extract the declared marker names from the raw ``markers`` ini value.

    Each entry is written ``name: description``, and a description is free prose that may
    itself contain colons -- every declaration in this project ends with a bracketed source
    citation, and several contain more -- so the split has to be bounded to the first colon.
    Names are returned in declaration order and duplicates are preserved, because a
    duplicated declaration is itself a defect a caller may want to see.

    Args:
        raw_markers: The raw ``markers`` value from the ``[pytest]`` section.

    Returns:
        The declared names, in order, without their descriptions.
    """
    return [entry.split(":", 1)[0].strip() for entry in ini_value_lines(raw_markers)]


def registry_marker_names(entries: Iterable[str]) -> set[str]:
    """Return the marker names from a runtime marker registry listing.

    The registry pytest exposes at runtime mixes two shapes. The names declared in
    configuration arrive as ``name: description``, while pytest's own built-ins arrive with
    their call signature attached -- ``parametrize(argnames, argvalues): ...`` -- so the
    description has to be dropped at the first colon *and* the signature at the first
    parenthesis. Missing the second step is not harmless: it silently prevents any built-in
    from ever being recognised, and a containment check written against it would look
    correct while proving nothing.

    Args:
        entries: The registry entries, exactly as pytest reports them.

    Returns:
        The bare marker names.
    """
    names: set[str] = set()
    for entry in entries:
        name = entry.split(":", 1)[0].split("(", 1)[0].strip()
        if name:
            names.add(name)
    return names


def harvest_gherkin_tags(text: str) -> set[str]:
    """Return the tag names declared in a Gherkin document, without their prefix.

    Gherkin permits a tag only on a line of its own, immediately preceding a ``Feature``,
    ``Scenario``, ``Scenario Outline`` or ``Examples`` keyword, and several tags may share
    one such line. This function exploits exactly that rule: a line qualifies only when its
    *stripped* content begins with the tag prefix, and then every prefixed token on it is a
    tag.

    That anchoring is the whole point. A general pattern applied to the document body
    instead scavenges the local parts of the e-mail addresses in the Examples tables and
    reports a tag that does not exist, while a word-only pattern truncates every hyphenated
    Jira key. Anchoring on the line yields the specification's tags and nothing else, and it
    needs no pattern machinery whatsoever.

    Args:
        text: The full Gherkin document, decoded.

    Returns:
        The tag names with the leading prefix removed. Order is not meaningful, so a set is
        returned; an empty set means the document declares no tags.
    """
    tags: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith(TAG_PREFIX):
            # Not a tag line. Every e-mail address, table row and step lives here, and none
            # of them can contribute a tag.
            continue
        for token in stripped.split():
            if not token.startswith(TAG_PREFIX):
                # Gherkin does not allow trailing prose on a tag line, but ignoring
                # anything unprefixed costs nothing and keeps the harvest honest.
                continue
            name = token.removeprefix(TAG_PREFIX)
            if name:
                tags.add(name)
    return tags


def import_roots(source: str) -> set[str]:
    """Return the root packages imported by a Python source file.

    The scan is line-anchored for the same reason the tag harvest is: the word "Jira"
    appears in prose throughout this project -- in marker descriptions, docstrings and
    citations -- and a plain text search would match all of it. Only a line whose stripped
    content begins with an import keyword is considered, and only the first dotted segment
    of the imported name is returned.

    Relative imports are reported by the leading dots they were written with, which is
    harmless: no distribution root can collide with them.

    Args:
        source: The decoded contents of a Python module.

    Returns:
        The root package names the module imports.
    """
    roots: set[str] = set()
    for line in source.splitlines():
        stripped = line.strip()
        if not stripped.startswith(IMPORT_STATEMENT_PREFIXES):
            continue
        parts = stripped.split()
        if len(parts) < 2:
            continue
        # ``import a.b, c`` and ``from a.b import c`` both name the module second; a
        # parenthesised ``from a import (`` is unaffected because only the module matters.
        roots.add(parts[1].split(".")[0])
    return roots


def read_text_or_skip(path: Path, purpose: str) -> str:
    """Return the decoded contents of ``path``, or skip the calling test.

    Encoding is stated explicitly, never inherited from the platform, so the same bytes
    decode identically wherever the suite runs.

    Args:
        path: Absolute path to the file, composed from the repository root rather than from
            the working directory.
        purpose: What the caller needed the file for, quoted in the skip reason so a skipped
            run says which contract went unverified instead of merely disappearing.

    Returns:
        The file's contents.

    Raises:
        Skipped: Raised through :func:`pytest.skip` when the file is absent. A missing
            configuration artefact is reported as an unverified contract rather than
            fabricated expectations.
    """
    if not path.is_file():
        pytest.skip(f"{path} is absent, so {purpose} cannot be verified from it")
    return path.read_text(encoding="utf-8")


def _nested_environment() -> dict[str, str]:
    """Build the environment for a nested pytest run.

    The parent's environment is inherited so the interpreter keeps its path and its virtual
    environment, then every variable that would change the nested run's behaviour is
    removed and bytecode writing is disabled.

    Returns:
        A fresh mapping; the caller's own environment is never mutated.
    """
    environment = dict(os.environ)
    for name in INHERITED_ENV_VARS_TO_DROP:
        environment.pop(name, None)
    environment.update(NESTED_ENV_OVERRIDES)
    return environment


def _run_nested_pytest(directory: Path, extra_args: Sequence[str] = ()) -> str:
    """Run pytest in ``directory`` and return its combined output.

    The invocation is an argument list executed without a shell, with an explicit timeout,
    so nothing in it can be reinterpreted and a wedged child cannot hang the session. The
    cache provider is switched off: the directory is throwaway and a cache written into it
    would be the only state capable of carrying results between runs.

    Args:
        directory: The directory to run in. For the controlled experiments this is a prepared
            throwaway directory holding its own ``pytest.ini`` and generated test module, so
            it becomes both the working directory and the rootdir and the nested run cannot
            reach this repository's configuration. For the read-only marker listing it is the
            repository root, with the option set cleared on the command line.
        extra_args: Additional options for the nested run, such as a tag expression.

    Returns:
        Standard output and standard error concatenated, which is where pytest writes its
        warnings summary and its final counts.

    Raises:
        Skipped: Raised through :func:`pytest.skip` when the nested interpreter cannot be
            started or does not finish within the timeout. The static assertions in this
            module are the load-bearing evidence, so an unavailable subprocess degrades the
            corroboration instead of failing the suite.
    """
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-p",
        "no:cacheprovider",
        "-q",
        *extra_args,
        ".",
    ]
    try:
        # A fixed argument list, executed without a shell and with an explicit timeout.
        completed = subprocess.run(
            command,
            cwd=directory,
            env=_nested_environment(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=NESTED_PYTEST_TIMEOUT_SECONDS,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        pytest.skip(
            "a nested pytest run could not be completed in this environment "
            f"({type(error).__name__}: {error}), so only the static evidence applies"
        )
    return completed.stdout + completed.stderr


def _render_marker_decorator(name: str) -> str:
    """Render one mark as a decorator line for the generated probe module.

    A mark whose name is not a Python identifier -- every Jira key, because of its hyphen --
    cannot be written in attribute form and is fetched by name instead. Applying marks as
    decorators is essential rather than stylistic: a mark added inside a test body arrives
    after collection, which is when tag expressions are evaluated, so it could never be
    selected and the probe would prove the opposite of the truth.

    Args:
        name: The marker name, prefix already stripped.

    Returns:
        A single decorator line, without its trailing newline.
    """
    if name.isidentifier():
        return f"@pytest.mark.{name}"
    return f'@getattr(pytest.mark, "{name}")'


def _render_nested_probe_module() -> str:
    """Render the throwaway test module used by the nested runs.

    Each generated test asserts that its own feature-level mark is attached, so the probe
    exercises a real assertion rather than an empty body, and every mark is applied as a
    decorator.

    Returns:
        The complete module source, ending in a single newline.
    """
    lines = [
        '"""Generated marker probe. Written to a throwaway directory, never committed."""',
        "",
        "import pytest",
        "",
    ]
    for test_name, marker_names in NESTED_PROBE_CASES:
        lines.append("")
        lines.extend(_render_marker_decorator(name) for name in marker_names)
        lines.append(f"def test_{test_name}(request: pytest.FixtureRequest) -> None:")
        lines.append(
            f'    assert request.node.get_closest_marker("{NESTED_PROBE_SENTINEL_MARK}")'
            " is not None"
        )
    return "\n".join(lines) + "\n"


def _render_nested_ini(marker_names: Iterable[str]) -> str:
    """Render the throwaway ``pytest.ini`` for a nested run.

    Deliberately minimal: a ``[pytest]`` section and, when marker names are supplied, a
    ``markers`` list. No option set and no warning filters, so the nested run reports the
    warnings pytest itself decides to emit rather than any policy imposed on it. In
    particular it carries no strict-marker flag, because the negative control needs the
    warning that flag would replace with an error.

    Args:
        marker_names: The names to declare. An empty iterable produces a configuration that
            declares nothing, which is the negative control.

    Returns:
        The complete ini source, ending in a single newline.
    """
    declared = list(marker_names)
    if not declared:
        return "[pytest]\n"
    lines = ["[pytest]", "markers ="]
    lines.extend(f"    {name}: generated probe marker" for name in declared)
    return "\n".join(lines) + "\n"


def _prepare_nested_probe(directory: Path, marker_names: Iterable[str]) -> int:
    """Write a self-contained nested probe into ``directory``.

    Args:
        directory: A throwaway directory, empty on entry.
        marker_names: The marker names to declare in the generated configuration.

    Returns:
        The number of mark applications in the generated module, which is the upper bound on
        the number of unknown-mark warnings an unregistered run can emit.
    """
    ini_path = directory / PYTEST_INI_FILENAME
    ini_path.write_text(_render_nested_ini(marker_names), encoding="utf-8")
    module_path = directory / NESTED_PROBE_MODULE_NAME
    module_path.write_text(_render_nested_probe_module(), encoding="utf-8")
    return sum(len(marker_names_for_case) for _, marker_names_for_case in NESTED_PROBE_CASES)


# =============================================================================
# Fixtures. Every path is composed from the session-scoped repository root, never from the
# working directory: the default run is parallel, every worker is a separate process, and a
# relative path would resolve against whatever directory each of them happens to have.
# =============================================================================


@pytest.fixture
def pytest_ini_path(project_root: Path) -> Path:
    """Return the absolute path of the root ``pytest.ini``."""
    return project_root / PYTEST_INI_FILENAME


@pytest.fixture
def pytest_ini_text(pytest_ini_path: Path) -> str:
    """Return the raw text of ``pytest.ini``, or skip when it is absent."""
    return read_text_or_skip(pytest_ini_path, "the ported test configuration")


@pytest.fixture
def pytest_ini_parser(pytest_ini_text: str, pytest_ini_path: Path) -> configparser.ConfigParser:
    """Return ``pytest.ini`` parsed by the standard library.

    Parsing the committed file directly is what makes the assertions authoritative. What
    pytest reports at runtime is whatever the invocation resolved to -- a command line may
    override the option set, and a different rootdir may supply a different file altogether --
    whereas the committed file is the contract under test. No special parser settings are
    needed: the file is ordinary ini with a single section.

    Args:
        pytest_ini_text: The file's contents, already read and decoded.
        pytest_ini_path: The file's path, recorded as the parse source so any syntax error
            names the real file rather than an anonymous string.

    Returns:
        The parser, with the file already read.
    """
    parser = configparser.ConfigParser()
    parser.read_string(pytest_ini_text, source=str(pytest_ini_path))
    return parser


@pytest.fixture
def pytest_ini_config(
    pytest_ini_parser: configparser.ConfigParser, pytest_ini_path: Path
) -> configparser.SectionProxy:
    """Return the ``[pytest]`` section, which every setting under test lives in."""
    if not pytest_ini_parser.has_section(PYTEST_INI_SECTION):
        pytest.fail(
            f"{pytest_ini_path} declares no [{PYTEST_INI_SECTION}] section, so pytest reads "
            "none of its settings and every ported runner option is silently lost"
        )
    return pytest_ini_parser[PYTEST_INI_SECTION]


@pytest.fixture
def declared_markers(pytest_ini_config: configparser.SectionProxy) -> tuple[str, ...]:
    """Return the marker names declared in ``pytest.ini``, in declaration order."""
    return tuple(declared_marker_names(pytest_ini_config.get("markers", "")))


@pytest.fixture
def addopts_arguments(pytest_ini_config: configparser.SectionProxy) -> tuple[str, ...]:
    """Return the active ``addopts`` entries of ``pytest.ini``, one per line.

    ``configparser`` already drops a full-line comment from a continued value, so the
    disabled tuning default never reaches this list; the prefix filter below states that
    intent explicitly rather than relying on it. The commented text is asserted separately,
    against the raw file, so both its presence and its inertness are proven.
    """
    entries = ini_value_lines(pytest_ini_config.get("addopts", ""))
    return tuple(entry for entry in entries if not entry.startswith(INI_COMMENT_PREFIXES))


@pytest.fixture
def warning_filters(pytest_ini_config: configparser.SectionProxy) -> tuple[WarningFilter, ...]:
    """Return the parsed ``filterwarnings`` entries of ``pytest.ini``, in order.

    Order is preserved because it is meaningful: pytest applies the entries in sequence and
    later ones win, so a narrow exception is only effective when it follows the strict base
    policy.
    """
    entries = ini_value_lines(pytest_ini_config.get("filterwarnings", ""))
    return tuple(parse_warning_filter(entry) for entry in entries)


@pytest.fixture
def feature_file_path(project_root: Path) -> Path:
    """Return the absolute path of the materialised Gherkin specification."""
    return project_root.joinpath(*FEATURE_FILE_RELATIVE_PATH)


@pytest.fixture
def feature_tags(feature_file_path: Path) -> frozenset[str]:
    """Return the tags declared in the feature file, harvested line by line.

    The file is read for its tags alone. Its bytes are the subject of the byte-parity suite
    and are deliberately not compared here.
    """
    text = read_text_or_skip(feature_file_path, "the Gherkin tags of the ported specification")
    return frozenset(harvest_gherkin_tags(text))


# =============================================================================
# Requirement 1 -- the six markers, and only six.
# =============================================================================


def test_every_gherkin_tag_is_a_registered_marker(declared_markers: tuple[str, ...]) -> None:
    """All six Gherkin tags are declared as pytest markers in ``pytest.ini``.

    This is the core of the criterion. Each name is the tag with its prefix stripped, exactly
    as pytest-bdd converts it: ``Login`` [README.md:L104], the three Jira keys
    [README.md:L115], [README.md:L123] and [README.md:L131], and the two Examples-table tags
    [README.md:L137] and [README.md:L144].
    """
    missing = sorted(GHERKIN_TAG_MARKERS - set(declared_markers))
    assert not missing, (
        f"{PYTEST_INI_FILENAME} does not declare {missing}; every unregistered mark raises "
        f"{UNKNOWN_MARK_WARNING} once per application -- "
        f"{UNREGISTERED_WARNING_COUNT_SERIAL} warnings serially and "
        f"{UNREGISTERED_WARNING_COUNT_PARALLEL} under parallel execution, because each "
        "worker warns independently"
    )


def test_no_marker_beyond_the_six_tags_and_the_documented_selector(
    declared_markers: tuple[str, ...],
) -> None:
    """Nothing else is declared as a Gherkin-derived marker.

    Exactly one declared name is not a feature-file tag: the default selector ``LogOut``
    ``[README.md:L87]``, which no scenario carries and which is registered so that the
    zero-selection default run is documented rather than silent. Any other extra name means
    either a tag that no longer exists in the specification or -- the measured failure mode
    -- a phantom harvested from the e-mail addresses in the Examples tables.
    """
    permitted = GHERKIN_TAG_MARKERS | {DEFAULT_TAG_SELECTOR}
    unexpected = sorted(set(declared_markers) - permitted)
    assert not unexpected, (
        f"{PYTEST_INI_FILENAME} declares {unexpected}, which neither appears in the feature "
        f"file nor is the documented default selector {DEFAULT_TAG_SELECTOR!r} "
        f"{DEFAULT_TAG_SELECTOR_LOCATOR}; a marker list that drifts from the specification is "
        "exactly what this criterion exists to catch"
    )


def test_declared_markers_are_not_duplicated(declared_markers: tuple[str, ...]) -> None:
    """No marker is declared twice.

    ``configparser`` keeps every line of a continued value, so a duplicated declaration
    survives into the list and would let two descriptions disagree about the same mark.
    """
    duplicated = sorted({name for name in declared_markers if declared_markers.count(name) > 1})
    assert not duplicated, (
        f"{PYTEST_INI_FILENAME} declares {duplicated} more than once; one mark must have "
        "exactly one description"
    )


def test_marker_declarations_cite_their_source_locator(
    pytest_ini_config: configparser.SectionProxy,
) -> None:
    """Each Gherkin marker's declaration cites the line that specifies its tag.

    Configuration values are data carried across from the source project, not decisions taken
    here, so each declaration names where its tag comes from. Losing the citation would leave
    the marker list unauditable, which is why the citation is asserted rather than trusted.
    """
    entries = ini_value_lines(pytest_ini_config.get("markers", ""))
    described = {entry.split(":", 1)[0].strip(): entry for entry in entries}
    for name, locator in MARKER_SOURCE_LOCATORS.items():
        declaration = described.get(name)
        assert declaration is not None, (
            f"{PYTEST_INI_FILENAME} does not declare the marker {name!r} at all, so its "
            f"source citation {locator} cannot be checked"
        )
        assert locator in declaration, (
            f"the declaration of marker {name!r} does not cite {locator}; the tag names are "
            "carried over from the documented specification and each one keeps its origin"
        )


def test_feature_file_tags_and_registered_markers_agree(
    feature_tags: frozenset[str], declared_markers: tuple[str, ...]
) -> None:
    """The tags in the feature file are exactly the six, and every one is registered.

    This is the criterion stated end to end: the specification and the configuration are
    compared against each other rather than each against a hard-coded list. A tag added to
    the feature file without a declaration would fail here, and so would a declaration for a
    tag the specification no longer contains.
    """
    assert feature_tags == GHERKIN_TAG_MARKERS, (
        f"the feature file declares {sorted(feature_tags)}, which is not the specified set "
        f"{sorted(GHERKIN_TAG_MARKERS)}"
    )
    unregistered = sorted(feature_tags - set(declared_markers))
    assert not unregistered, (
        f"the feature file declares {unregistered}, which {PYTEST_INI_FILENAME} does not "
        "register; pytest-bdd converts every tag into a mark, so each one needs a declaration"
    )


def test_default_selector_is_not_a_feature_file_tag(feature_tags: frozenset[str]) -> None:
    """The default tag selector matches no scenario, and that is preserved deliberately.

    ``addopts`` carries ``-m "LogOut"``, the faithful port of the documented runner's
    ``tags = "@LogOut"`` ``[README.md:L87]``, while the specification's only tags are the six
    above. The documented run therefore selects nothing. Widening the selector to make the
    suite execute something would break the parity contract; this assertion is what stops
    that happening quietly.
    """
    assert DEFAULT_TAG_SELECTOR not in feature_tags, (
        f"the feature file now carries the {DEFAULT_TAG_SELECTOR!r} tag "
        f"{DEFAULT_TAG_SELECTOR_LOCATOR}, so the documented default run would no longer "
        "select zero scenarios -- a preserved defect must not be corrected here"
    )


def test_jira_keys_keep_their_hyphens(declared_markers: tuple[str, ...]) -> None:
    """The Jira issue keys are registered verbatim, hyphens intact.

    Hyphens are legal in the ``markers`` list and the names stay selectable by tag
    expression, so there is no reason to normalise them -- and every reason not to: the key
    is what links a scenario to its issue. The two failure modes worth naming are an
    underscore rewrite and the truncation a word-only harvest produces, and both are checked.
    """
    registered = set(declared_markers)
    for key in JIRA_TRACEABILITY_MARKERS:
        assert key in registered, (
            f"the Jira traceability marker {key!r} {MARKER_SOURCE_LOCATORS[key]} is not "
            f"declared in {PYTEST_INI_FILENAME}"
        )
        assert key.replace("-", "_") not in registered, (
            f"{PYTEST_INI_FILENAME} declares {key.replace('-', '_')!r}; the hyphen is part of "
            "the issue key and must not be rewritten"
        )
    truncated = {key.split("-", 1)[0] for key in JIRA_TRACEABILITY_MARKERS}
    assert not (truncated & registered), (
        f"{PYTEST_INI_FILENAME} declares {sorted(truncated & registered)}, the truncation a "
        "word-only tag harvest produces; the full issue key is the marker name"
    )


def test_no_jira_client_is_imported_anywhere(project_root: Path) -> None:
    """Traceability is carried by tag names alone -- no Jira client exists in the tree.

    The source project's traceability was a naming convention and nothing more, so the port
    keeps the convention and introduces no client. Adding one would add behaviour the source
    never had. The scan is line-anchored on import statements because the word appears
    throughout the prose of this project, including in the marker descriptions themselves.
    """
    offenders: list[str] = []
    for root_name in SCANNED_SOURCE_ROOTS:
        root = project_root / root_name
        if not root.is_dir():
            continue
        for module_path in sorted(root.rglob("*.py")):
            imported = import_roots(module_path.read_text(encoding="utf-8"))
            for forbidden in sorted(imported & JIRA_CLIENT_IMPORT_ROOTS):
                offenders.append(f"{module_path.relative_to(project_root)} imports {forbidden}")
    assert not offenders, (
        "traceability is preserved through the Jira tag names alone and no client is "
        f"introduced, but found: {offenders}"
    )


# =============================================================================
# Requirement 2 -- the phantom-tag trap. The harvester is guarded by its own tests.
# =============================================================================

# A miniature Gherkin document carrying every shape that has misled a tag harvest: an
# indented tag line with two tags on it, a step whose argument contains a prefixed token,
# table rows full of e-mail addresses, and a comment mentioning a tag. Written here as data
# rather than read from the specification so the guard keeps working even if the
# specification is unavailable.
_HARVEST_PROBE_DOCUMENT: Final[str] = """\
@Login
Feature: harvest probe

  #1 - the comment below mentions @NotATag and must not contribute one
  @UPGN-286
  Scenario Outline: probe
    When User enters "<username>" username
    And User signs in as service@example.com
    Then User should see the dashboard

    @SalesManager @PosManager
    Examples: probe data
      |username               |password    |
      |salesmanager7@info.com |salesmanager|
      |posmanager5@info.com   |posmanager  |
"""

_HARVEST_PROBE_EXPECTED: Final[frozenset[str]] = frozenset(
    {"Login", "UPGN-286", "SalesManager", "PosManager"}
)

# The local part of the Examples e-mail addresses ends at this domain, and a general pattern
# applied to the document body reports it as a tag. It is the measured phantom.
_PHANTOM_TAG: Final[str] = "info.com"


def test_harvester_reads_tags_only_from_tag_lines() -> None:
    """Tags come only from lines whose stripped content begins with the prefix.

    Gherkin allows a tag nowhere else, so anchoring on the line is both correct and
    sufficient. The probe document deliberately hides prefixed tokens in a step argument, in
    a comment and in two table rows; none of them may reach the result, and the two tags
    sharing one indented line must both be found.
    """
    assert harvest_gherkin_tags(_HARVEST_PROBE_DOCUMENT) == _HARVEST_PROBE_EXPECTED


def test_harvester_does_not_invent_a_tag_from_an_email_address() -> None:
    """The phantom tag scavenged from the Examples e-mail addresses never appears.

    This is the trap that makes a naive harvest assert on a marker that does not exist. Both
    the probe document and the real specification are checked, because the real file is what
    the criterion is ultimately about.
    """
    assert _PHANTOM_TAG not in harvest_gherkin_tags(_HARVEST_PROBE_DOCUMENT)
    assert not any(
        _PHANTOM_TAG in tag for tag in harvest_gherkin_tags(_HARVEST_PROBE_DOCUMENT)
    ), "no harvested tag may contain the domain of an Examples-table e-mail address"


def test_harvester_keeps_hyphenated_tags_whole() -> None:
    """A hyphenated Jira key survives the harvest unmangled.

    A word-only harvest truncates every key at the hyphen, which silently turns three
    distinct traceability tags into one meaningless name.
    """
    harvested = harvest_gherkin_tags("  @UPGN-286 @UPGN-287\n  Scenario Outline: probe\n")
    assert harvested == {"UPGN-286", "UPGN-287"}


def test_harvester_ignores_a_bare_prefix_and_returns_nothing_for_a_tagless_document() -> None:
    """A lone prefix contributes no name, and a document without tag lines yields nothing."""
    assert harvest_gherkin_tags("@\nFeature: bare prefix\n") == set()
    assert harvest_gherkin_tags("Feature: no tags\n  Scenario: none\n") == set()


def test_real_specification_harvests_exactly_the_six_tags(feature_tags: frozenset[str]) -> None:
    """Applied to the committed specification, the harvest yields exactly six tags.

    The file contains five e-mail addresses across its two Examples tables and three
    hyphenated Jira keys, so it exercises both halves of the trap at once.
    """
    assert feature_tags == GHERKIN_TAG_MARKERS
    assert len(feature_tags) == len(GHERKIN_TAG_MARKERS)
    assert _PHANTOM_TAG not in feature_tags


# =============================================================================
# Requirement 3 -- the registered-marker superset. Containment, never equality.
# =============================================================================

# A pytest built-in that is always registered. Its presence proves the runtime list is a
# superset of the declared one, which is why every assertion against that list is
# containment.
_BUILT_IN_MARKER_WITNESS: Final[str] = "parametrize"


def test_runtime_marker_registry_contains_every_declared_gherkin_marker(
    pytestconfig: pytest.Config, pytest_ini_path: Path
) -> None:
    """The running session has all six markers registered, as a subset of its registry.

    The registry pytest reports is not the declared list: it also holds pytest's own
    built-ins and whatever the installed plugins contribute, and that ambient set differs
    from machine to machine. Equality would therefore be both wrong and unstable, so the
    assertion is containment and the built-in witness below documents why.

    The premise is that this session is configured by the repository's ``pytest.ini``. That
    is checked against the configuration file pytest actually resolved rather than assumed:
    a session started somewhere else reads a different file, or none, and would report the
    absence of the six as a failure of registration when it is really a failure of premise.
    """
    resolved_config_file = pytestconfig.inipath
    if resolved_config_file is None or resolved_config_file != pytest_ini_path:
        pytest.skip(
            f"this session resolved {resolved_config_file or 'no configuration file'} rather "
            f"than {pytest_ini_path}, so its marker registry says nothing about the ported "
            "configuration"
        )
    registered = registry_marker_names(pytestconfig.getini("markers"))
    if not registered:
        pytest.skip(
            "this session resolved no marker registry at all, which means it is running "
            f"against a configuration other than the repository's {PYTEST_INI_FILENAME}"
        )
    missing = sorted(GHERKIN_TAG_MARKERS - registered)
    assert not missing, (
        f"the running session has not registered {missing}, so every application of those "
        f"marks raises {UNKNOWN_MARK_WARNING}"
    )
    assert _BUILT_IN_MARKER_WITNESS in registered, (
        "pytest's own built-in markers are missing from the registry, so the registry cannot "
        "be interpreted as a superset of the declared list"
    )


def test_runtime_marker_registry_is_a_strict_superset(
    pytestconfig: pytest.Config, declared_markers: tuple[str, ...]
) -> None:
    """The runtime registry holds strictly more than ``pytest.ini`` declares.

    Stated as an executable assertion so the trap cannot be forgotten: anyone tempted to
    compare the runtime registry with the declared list for equality is contradicted here.
    """
    registered = registry_marker_names(pytestconfig.getini("markers"))
    if not registered:
        pytest.skip(
            "this session resolved no marker registry, so the superset relationship cannot "
            "be demonstrated from it"
        )
    ambient = sorted(registered - set(declared_markers))
    assert ambient, (
        "the runtime registry contains nothing beyond the declared markers, which contradicts "
        "pytest registering its own built-ins; the registry must not be compared for equality"
    )
    assert set(declared_markers) <= registered, (
        f"{PYTEST_INI_FILENAME} declares "
        f"{sorted(set(declared_markers) - registered)} which the running session has not "
        "registered, so this session is reading a different configuration"
    )


def test_marker_registry_reported_by_pytest_lists_the_hyphenated_names(
    project_root: Path, pytest_ini_path: Path
) -> None:
    """A real pytest invocation lists every declared marker under its exact name.

    Secondary, empirical corroboration of the static assertions: it proves the hyphenated
    Jira keys are accepted and reported verbatim by pytest itself rather than merely written
    in a file. The option set is cleared for the probe so the repository's parallelism and
    report options play no part, and the whole check degrades to a skip when the subprocess
    cannot be run.

    The nested run reads its configuration from the repository root, so its listing can only
    contain the declared markers when the file that declares them is there. Its absence is
    reported as an unverified contract, exactly as the static assertions report it.
    """
    if not pytest_ini_path.is_file():
        pytest.skip(
            f"{pytest_ini_path} is absent, so a nested run cannot be expected to list the "
            "ported markers"
        )
    output = _run_nested_pytest(project_root, ("--markers", "-o", "addopts="))
    if "@pytest.mark." not in output:
        pytest.skip(
            "the marker listing could not be obtained from a nested pytest run, so only the "
            f"static evidence from {PYTEST_INI_FILENAME} applies"
        )
    for name in sorted(GHERKIN_TAG_MARKERS):
        assert f"@pytest.mark.{name}:" in output, (
            f"pytest does not report the marker {name!r} as registered, even though "
            f"{PYTEST_INI_FILENAME} declares it"
        )


# =============================================================================
# Requirement 4 -- zero unknown-mark warnings, and filters recorded by name.
# =============================================================================


def test_warning_filters_are_declared_at_all(warning_filters: tuple[WarningFilter, ...]) -> None:
    """``filterwarnings`` is configured explicitly rather than left to pytest's defaults.

    Without an explicit policy a warning is printed and forgotten, which is how a marker
    registration defect goes unnoticed: the run still passes.
    """
    assert warning_filters, (
        f"{PYTEST_INI_FILENAME} declares no filterwarnings entries, so every warning is merely "
        f"printed -- including the {UNKNOWN_MARK_WARNING} that marker registration exists to "
        "prevent"
    )
    for warning_filter in warning_filters:
        assert warning_filter.action, (
            f"the filterwarnings entry {warning_filter!r} has no action; every entry must say "
            "what it does with a matching warning"
        )


def test_warning_filters_are_default_strict(warning_filters: tuple[WarningFilter, ...]) -> None:
    """The base policy turns any unexcepted warning into a failure.

    ``error`` must also come before the narrow exceptions: pytest applies the entries in
    order and the later one wins, so an exception placed above the base policy would be
    overridden by it and a suppression placed below would apply to everything that follows.
    """
    actions = [warning_filter.action for warning_filter in warning_filters]
    assert STRICT_WARNING_ACTION in actions, (
        f"{PYTEST_INI_FILENAME} does not declare a {STRICT_WARNING_ACTION!r} filter, so a "
        "genuinely new warning would be printed and ignored instead of surfacing"
    )
    strict_index = actions.index(STRICT_WARNING_ACTION)
    for index, warning_filter in enumerate(warning_filters):
        if warning_filter.suppresses:
            assert index > strict_index, (
                f"the suppressing filter {warning_filter!r} is declared before the "
                f"{STRICT_WARNING_ACTION!r} base policy, where the base policy overrides it; "
                "narrow exceptions belong after it"
            )


def test_warning_filters_grant_only_narrow_exceptions(
    warning_filters: tuple[WarningFilter, ...],
) -> None:
    """No filter suppresses warnings wholesale.

    A bare suppression, or one naming only a category, hides every warning of that category --
    and the categories an unknown-mark warning belongs to include the broadest ones there
    are. That is precisely how the registration defect this suite guards would go unnoticed,
    so each suppression must name a message, a module, or both.
    """
    for warning_filter in warning_filters:
        if not warning_filter.suppresses:
            continue
        assert warning_filter.is_narrowed, (
            f"the filter {warning_filter!r} suppresses warnings without naming a message or a "
            "module, so it is a blanket suppression; every exception must be narrow enough to "
            "explain itself"
        )
        assert warning_filter.category not in MARK_SWALLOWING_CATEGORIES, (
            f"the filter {warning_filter!r} suppresses the category "
            f"{warning_filter.category or '<every category>'!r}, which {UNKNOWN_MARK_WARNING} "
            "belongs to; that warning must always be able to surface"
        )


def test_warning_filters_name_the_gherkin_parser_by_module(
    warning_filters: tuple[WarningFilter, ...],
) -> None:
    """The one benign third-party deprecation is excepted by name, not by category.

    The Gherkin parser the BDD plugin depends on passes an argument positionally in a way a
    current interpreter deprecates. The warning is third-party, harmless and unavoidable
    here, so it is excepted -- but the exception names the module it originates in and the
    message it carries, which is what keeps it from masking anything else.
    """
    named = [
        warning_filter
        for warning_filter in warning_filters
        if GHERKIN_FILTER_MODULE_HINT in warning_filter.module
    ]
    assert named, (
        f"no filterwarnings entry names the {GHERKIN_FILTER_MODULE_HINT!r} module, so the one "
        "known benign third-party deprecation is either unhandled -- and fails the run under "
        "the strict base policy -- or is being suppressed by something broader"
    )
    for warning_filter in named:
        assert warning_filter.message, (
            f"the filter {warning_filter!r} names a module but no message, so it applies to "
            "every warning that module can emit rather than the one known deprecation"
        )
        assert warning_filter.category, (
            f"the filter {warning_filter!r} names no warning category, so it is broader than "
            "the single deprecation it exists for"
        )


def test_unknown_mark_warnings_cannot_be_emitted_at_all(
    addopts_arguments: tuple[str, ...], warning_filters: tuple[WarningFilter, ...]
) -> None:
    """Strict markers plus the strict warning base make the warning structurally impossible.

    ``--strict-markers`` upgrades an unregistered mark from a warning to a collection error,
    so the counts this criterion is about -- eight serially, one hundred and twenty-eight
    under parallel execution, one per application per worker -- cannot be reached: the run
    stops instead. The strict warning base is the second half of the guarantee, because it
    means that even a warning nobody anticipated fails the run rather than scrolling past.
    """
    assert STRICT_MARKERS_FLAG in addopts_arguments, (
        f"{PYTEST_INI_FILENAME} does not carry {STRICT_MARKERS_FLAG} in its default option "
        f"set, so an unregistered mark degrades to {UNKNOWN_MARK_WARNING} -- "
        f"{UNREGISTERED_WARNING_COUNT_SERIAL} of them serially and "
        f"{UNREGISTERED_WARNING_COUNT_PARALLEL} under {PARALLEL_WORKERS_ADDOPT} -- instead of "
        "stopping the run"
    )
    assert any(
        warning_filter.action == STRICT_WARNING_ACTION for warning_filter in warning_filters
    ), (
        "the strict warning base is missing, so a warning that no filter anticipates would be "
        "printed and ignored"
    )


def test_registered_marks_emit_no_unknown_mark_warning(tmp_path: Path) -> None:
    """Empirically: with the six marks declared, a real run emits no unknown-mark warning.

    Run in a throwaway directory with its own minimal configuration, so nothing about this
    repository's option set can influence the result, and with the cache provider disabled
    so no state can survive from a previous run. A stale bytecode cache has already produced
    one false negative in this migration's verification work, which is why bytecode writing
    is disabled too.
    """
    _prepare_nested_probe(tmp_path, sorted(GHERKIN_TAG_MARKERS))
    output = _run_nested_pytest(tmp_path)
    assert (
        UNKNOWN_MARK_WARNING not in output
    ), f"a run with all six marks declared still emitted {UNKNOWN_MARK_WARNING}:\n{output}"
    assert f"{len(NESTED_PROBE_CASES)} passed" in output, (
        f"the nested probe did not report its {len(NESTED_PROBE_CASES)} tests as passing, so "
        f"the absence of {UNKNOWN_MARK_WARNING} proves nothing:\n{output}"
    )


def test_unregistered_marks_do_emit_unknown_mark_warnings(tmp_path: Path) -> None:
    """The negative control: without the declarations, the warnings come back.

    Without this, the positive control above could pass for the wrong reason -- an
    environment that never emits the warning at all would satisfy it trivially. The same
    generated module is run against a configuration that declares nothing, and the warning
    must reappear, once per mark application: the module applies eight of them, which is
    exactly the serial count this criterion quotes.
    """
    applications = _prepare_nested_probe(tmp_path, ())
    output = _run_nested_pytest(tmp_path)
    observed = output.count(UNKNOWN_MARK_WARNING)
    assert observed >= 1, (
        "unregistering the marks did not produce a single "
        f"{UNKNOWN_MARK_WARNING}, so this environment cannot detect the very defect marker "
        f"registration prevents:\n{output}"
    )
    assert observed <= applications, (
        f"the run reported {observed} occurrences of {UNKNOWN_MARK_WARNING} for only "
        f"{applications} mark applications, which means the count is measuring something "
        f"other than the marks:\n{output}"
    )
    assert applications == UNREGISTERED_WARNING_COUNT_SERIAL, (
        f"the generated probe applies {applications} marks rather than the "
        f"{UNREGISTERED_WARNING_COUNT_SERIAL} that reproduce the documented serial warning "
        "count; the probe is meant to mirror the specification's tag layout"
    )


def test_hyphenated_marker_is_selectable_by_tag_expression(tmp_path: Path) -> None:
    """Empirically: a hyphenated Jira key is selectable, so traceability stays usable.

    The marks are applied as decorators, which is the whole point of the experiment. A mark
    attached inside a test body arrives after collection -- and collection is when a tag
    expression is evaluated -- so such a probe would report that hyphenated marks cannot be
    selected, which is false.
    """
    _prepare_nested_probe(tmp_path, sorted(GHERKIN_TAG_MARKERS))
    selected = JIRA_TRACEABILITY_MARKERS[0]
    output = _run_nested_pytest(tmp_path, ("-m", selected))
    expected_deselected = len(NESTED_PROBE_CASES) - 1
    assert "1 passed" in output, (
        f"selecting the hyphenated marker {selected!r} ran no test, so the Jira keys are not "
        f"usable as tag expressions:\n{output}"
    )
    assert f"{expected_deselected} deselected" in output, (
        f"selecting {selected!r} did not deselect the other {expected_deselected} tests, so "
        f"the expression is not filtering on the marker:\n{output}"
    )
    assert UNKNOWN_MARK_WARNING not in output, (
        f"selecting {selected!r} emitted {UNKNOWN_MARK_WARNING}, so the hyphenated name was "
        f"not recognised as declared:\n{output}"
    )


# =============================================================================
# Requirement 5 -- pytest.ini is the single source of test configuration.
# =============================================================================


def test_pyproject_declares_no_pytest_configuration_table(project_root: Path) -> None:
    """Packaging metadata carries no pytest configuration, so it cannot supersede the ini.

    A pytest table in the packaging metadata would win over ``pytest.ini`` silently, taking
    the ported runner options -- the tag selector, the parallelism, the report writers -- with
    it. The document is parsed rather than searched as text, because the prose in that file
    mentions the forbidden table by name in order to explain its own absence, and a substring
    search would match the explanation. The table-header scan that follows is the same
    line-anchored discipline the tag harvest uses, and it also catches an array-of-tables
    form the parsed check would not distinguish.
    """
    toml_reader = pytest.importorskip(
        "tomllib",
        reason="the standard library TOML reader is unavailable, so the packaging metadata "
        "cannot be parsed to prove it declares no pytest configuration",
    )
    pyproject_path = project_root / PYPROJECT_FILENAME
    text = read_text_or_skip(pyproject_path, "the single source of test configuration")
    document = toml_reader.loads(text)
    tool_tables = document.get(PYPROJECT_TOOL_TABLE, {})
    assert PYPROJECT_FORBIDDEN_TOOL_KEY not in tool_tables, (
        f"{PYPROJECT_FILENAME} declares a "
        f"[{PYPROJECT_TOOL_TABLE}.{PYPROJECT_FORBIDDEN_TOOL_KEY}] table, which supersedes "
        f"{PYTEST_INI_FILENAME} silently; test configuration lives in exactly one file"
    )
    headers = [line.strip() for line in text.splitlines() if line.strip().startswith("[")]
    offending = [
        header
        for header in headers
        if header.lstrip("[").startswith(FORBIDDEN_PYPROJECT_TABLE_PREFIX)
    ]
    assert not offending, (
        f"{PYPROJECT_FILENAME} declares the table header(s) {offending}; no table under "
        f"{FORBIDDEN_PYPROJECT_TABLE_PREFIX} may exist there"
    )


def test_pytest_ini_declares_the_pytest_section(
    pytest_ini_parser: configparser.ConfigParser, pytest_ini_path: Path
) -> None:
    """``pytest.ini`` carries a ``[pytest]`` section holding the settings under assertion.

    pytest reads its settings from that section and nowhere else in this file, so its absence
    would leave every ported option -- markers included -- unread while the file still looked
    configured.
    """
    assert pytest_ini_parser.has_section(
        PYTEST_INI_SECTION
    ), f"{pytest_ini_path} declares no [{PYTEST_INI_SECTION}] section"
    section = pytest_ini_parser[PYTEST_INI_SECTION]
    for setting in ("markers", "addopts", "filterwarnings"):
        assert (
            setting in section
        ), f"{pytest_ini_path} does not set {setting!r}, which this criterion depends on"


def test_default_option_set_preserves_the_zero_selection_selector(
    addopts_arguments: tuple[str, ...],
) -> None:
    """The default option set carries the documented tag selector, unchanged.

    ``tags = "@LogOut"`` ``[README.md:L87]`` becomes ``-m "LogOut"``: the prefix is dropped
    because pytest-bdd strips it when it converts a tag into a mark, and nothing else about
    the value changes. No scenario carries that tag, so the documented run selects nothing --
    a preserved defect, asserted here so it cannot be quietly widened.
    """
    assert DEFAULT_SELECTOR_ADDOPT in addopts_arguments, (
        f"{PYTEST_INI_FILENAME} does not carry {DEFAULT_SELECTOR_ADDOPT} in its default option "
        f"set; that selector is the port of the documented runner's tag expression "
        f"{DEFAULT_TAG_SELECTOR_LOCATOR} and selects zero scenarios by design"
    )
    assert TAG_PREFIX not in DEFAULT_SELECTOR_ADDOPT, (
        "the ported selector must not keep the Gherkin tag prefix, because pytest-bdd strips "
        "it when converting a tag into a mark"
    )


def test_default_option_set_ports_the_source_parallelism(
    addopts_arguments: tuple[str, ...],
) -> None:
    """Method-level parallelism with unlimited threads is ported to worker parallelism.

    ``<parallel>methods</parallel>`` ``[pom.xml:L22]`` with
    ``<useUnlimitedThreads>true</useUnlimitedThreads>`` ``[pom.xml:L23]`` becomes a worker
    count derived from the host rather than a fixed number, which is the closest faithful
    reading of "unlimited".
    """
    assert PARALLEL_WORKERS_ADDOPT in addopts_arguments, (
        f"{PYTEST_INI_FILENAME} does not carry {PARALLEL_WORKERS_ADDOPT!r}, so the source "
        "build's method-level parallelism with unlimited threads [pom.xml:L22-L23] is not "
        "reproduced"
    )


def test_default_option_set_requests_the_two_reportable_plugins(
    addopts_arguments: tuple[str, ...],
) -> None:
    """The two report plugins that map onto pytest options are requested by default.

    ``json:target/cucumber.json`` ``[README.md:L79]`` and
    ``html:target/cucumber-reports.html`` ``[README.md:L78]`` are expressible as options and
    so belong here. The rerun and pretty-report plugins have no option to request and are
    produced by the reporting package instead, which is why they are deliberately not
    expected in this list.
    """
    for prefix in REQUIRED_REPORT_ADDOPT_PREFIXES:
        assert any(argument.startswith(prefix) for argument in addopts_arguments), (
            f"{PYTEST_INI_FILENAME} does not request {prefix!r} in its default option set, so "
            "the ported report artefacts would not be produced by a default run"
        )


def test_disabled_thread_count_default_is_present_but_inert(
    pytest_ini_text: str, addopts_arguments: tuple[str, ...]
) -> None:
    """The source build's commented-out thread count survives as a comment, and only that.

    ``<!-- <threadCount>4</threadCount> -->`` ``[pom.xml:L24]`` was a documented tuning option
    the source build left disabled. It is preserved in exactly that state: present so the
    option is discoverable, commented so it changes nothing. An active entry would pin the
    worker count and silently replace the unlimited-thread semantics above it.
    """
    commented = [
        line.strip()
        for line in pytest_ini_text.splitlines()
        if line.strip().startswith(INI_COMMENT_PREFIXES) and DISABLED_THREAD_COUNT_ADDOPT in line
    ]
    assert commented, (
        f"{PYTEST_INI_FILENAME} no longer documents the disabled tuning default "
        f"{DISABLED_THREAD_COUNT_ADDOPT!r} [pom.xml:L24]; it is preserved as a comment so the "
        "option stays discoverable"
    )
    assert DISABLED_THREAD_COUNT_ADDOPT not in addopts_arguments, (
        f"{DISABLED_THREAD_COUNT_ADDOPT!r} is active in the default option set; the source "
        "build left that value disabled [pom.xml:L24] and enabling it would replace the "
        "unlimited-thread semantics [pom.xml:L22-L23]"
    )


def test_gherkin_terminal_reporter_is_absent_from_the_default_option_set(
    addopts_arguments: tuple[str, ...], pytest_ini_text: str
) -> None:
    """The reporter that cannot coexist with parallel execution is not requested by default.

    The BDD plugin's terminal reporter refuses to install once the parallelism plugin is
    registered, so a default run that requested both would abort during configuration rather
    than run anything. The default option set reproduces the source build's parallelism, so
    the reporter is available only through the dedicated serial target. Asserted against the
    whole file, not just the option list, because a commented occurrence would be one edit
    away from being active.
    """
    for argument in addopts_arguments:
        assert GHERKIN_TERMINAL_REPORTER_FLAG not in argument, (
            f"{PYTEST_INI_FILENAME} requests {GHERKIN_TERMINAL_REPORTER_FLAG} in its default "
            f"option set, which is mutually exclusive with {PARALLEL_WORKERS_ADDOPT!r} and "
            "aborts the run during configuration"
        )
    assert GHERKIN_TERMINAL_REPORTER_FLAG not in pytest_ini_text, (
        f"{PYTEST_INI_FILENAME} mentions {GHERKIN_TERMINAL_REPORTER_FLAG} at all; keeping the "
        "flag out of this file entirely is what makes searching for it an exact guard"
    )
