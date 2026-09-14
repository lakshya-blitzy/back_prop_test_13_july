"""Tests for the four-writer fan-out - ``app/services/report_service.py``.

The module under test is the port of the Cucumber plugin list the Java runner
declared (``CukesRunner.java:9-14``), moved to the one moment the port can
place it: AAP 0.3.3's *"one merged result set, four independent writers, none
aware of the others"*, driven after the per-worker documents have been merged
so that **every artifact has exactly one producer** (AAP 0.4.1, the concurrency
note).  What this module gates is therefore not the content of any artifact -
each writer's own test module owns that - but the fan-out's five observable
properties:

* the **order** the four writers run in, which AAP 0.4.1's exit table made
  observable by giving a writer failure a row of its own;
* **one invocation each**, which is what "exactly one producer" means in code;
* the **identity** of the document all four receive, and its immutability;
* what an **empty run** produces, which is the exit table's zero-scenario row;
* what **survives a writer failure**, which is the exit table's writer-failure
  row: *"The artifacts written before the failure remain; the failing writer is
  named on stderr, and the run does not delete completed artifacts."*

Two facts about the module under test shape every test below, and both were
measured against it rather than assumed.

**The injection seam is the sequence, not the writer names.**
``report_service.WRITER_SEQUENCE`` is built at import time out of direct
function references, so monkeypatching ``report_service.write_cucumber_json``
or the :mod:`app.reporting` barrel changes nothing about what
:func:`~app.services.generate_reports` calls - the tuple already holds the
original objects.  The recorders below are injected by replacing
``report_service.WRITER_SEQUENCE`` itself, and every recorder takes its
``name`` from the real sequence, so the fan-out order a test observes is still
the real module's order.  The real sequence's names and callable identities are
asserted separately, by :class:`TestWriterSequence`, which is what keeps the
injected tests honest.

**Nothing here writes outside pytest's ``tmp_path``.**  Every call passes
``base=`` from ``conftest``'s :fixture:`tmp_artifact_root`, which is the
artifact-path seam AAP 0.4.2 leaves open by giving every
:mod:`app.utils.paths` accessor a ``base`` parameter; the repository's real
``target/`` is neither created nor read by any test in this module.

The import below is through ``app.services``, the package barrel, because that
is the surface ``app/services/__init__.py`` declares for exactly this module -
``report_service`` itself is imported as well, and only so that
``WRITER_SEQUENCE`` can be replaced on its defining module.
"""

from __future__ import annotations

import ast
import copy
import dataclasses
import inspect
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, NamedTuple

import pytest

from app.reporting import (
    new_result_set,
    write_cucumber_json,
    write_html_report,
    write_pretty_reports,
    write_rerun_txt,
)
from app.services import (
    WRITER_SEQUENCE,
    ReportOutcome,
    WriterResult,
    WriterSpec,
    generate_reports,
    report_service,
)
from app.utils import paths

# =========================================================================== #
# Fixed names and the contract they encode
# =========================================================================== #

#: The four writer names, in the order :data:`WRITER_SEQUENCE` drives them.
#: These are contract rather than display strings: ``app/cli.py`` reports a
#: failure by ``ReportOutcome.failed_writer``, whose value is one of these, so
#: rewording one would break the command line's stderr message and the exit
#: table row that requires the failing writer to be named.
EXPECTED_WRITER_NAMES: Final[tuple[str, ...]] = (
    "cucumber_json",
    "rerun_txt",
    "html_report",
    "pretty_reports",
)

#: The writer entry point each name must be bound to, taken from the
#: :mod:`app.reporting` barrel - the surface that package advertises, and the
#: one ``report_service`` imports from.
EXPECTED_WRITER_CALLABLES: Final[tuple[Callable[..., Path], ...]] = (
    write_cucumber_json,
    write_rerun_txt,
    write_html_report,
    write_pretty_reports,
)

#: The artifact *identity* each writer must carry, in the same order - taken
#: from :mod:`app.utils.paths`, never spelled out here, because that module is
#: the port's sole owner of every destination (AAP 0.4.2).  The key exists so a
#: writer failure can name the artifact it was producing, which AAP 0.4.1's
#: writer-failure row requires: ``ReportOutcome.failed_path`` carries what
#: :meth:`app.services.WriterSpec.destination` resolved from it.
EXPECTED_ARTIFACT_KEYS: Final[tuple[str, ...]] = (
    paths.CUCUMBER_JSON_NAME,
    paths.RERUN_TXT_NAME,
    paths.CUCUMBER_REPORTS_HTML_NAME,
    paths.PRETTY_REPORTS_DIR_NAME,
)

#: The two writers whose artifacts a machine reads: the JSON report, which is
#: the Jenkins publisher's only input once ``fileIncludePattern`` is narrowed
#: to it (``Jenkins:15``), and the rerun manifest, which the second Java runner
#: named as its ``features`` source (``FailedTestRunner.java:11``) and which
#: ``run-tests --rerun`` reads back.
MACHINE_READ_WRITERS: Final[frozenset[str]] = frozenset(
    {"cucumber_json", "rerun_txt"}
)

#: The plugin *declaration* order of ``CukesRunner.java:9-14``: html, json,
#: rerun, PrettyReports.  Recorded here as the order the fan-out must **not**
#: use - the JVM plugins were concurrent listeners, so that order bound
#: nothing, and beginning with the HTML page would put a human-readable
#: artifact ahead of the publisher's only input.
PLUGIN_DECLARATION_ORDER: Final[tuple[str, ...]] = (
    "html_report",
    "cucumber_json",
    "rerun_txt",
    "pretty_reports",
)

#: Logger the module under test writes to - ``logging.getLogger(__name__)`` in
#: its own source.  Named here because the failure tests assert on the record
#: it emits, which ``app/logging_config.py`` routes to stderr at ``ERROR``.
SERVICE_LOGGER_NAME: Final[str] = "app.services.report_service"

#: File extensions that make a string literal a path rather than an
#: identifier.  Used by the source scan below; ``cucumber_json`` is a writer
#: name and carries no extension, while ``cucumber.json`` would be a
#: destination and belongs to ``app/utils/paths.py`` alone (AAP 0.4.2).
PATH_LITERAL_SUFFIXES: Final[tuple[str, ...]] = (".json", ".txt", ".html")

#: Names of calls that would make the module a writer in its own right, or let
#: it undo one.  The exit table forbids the second outright: a completed
#: artifact is never deleted.
FILESYSTEM_CALL_NAMES: Final[frozenset[str]] = frozenset(
    {
        "open",
        "mkdir",
        "makedirs",
        "write_text",
        "write_bytes",
        "unlink",
        "remove",
        "rmdir",
        "rmtree",
        "copy",
        "copy2",
        "copytree",
        "move",
        "touch",
        "replace",
    }
)

#: Modules ``report_service``'s own docstring declares out of bounds for it.
#: The dependency edge AAP 0.4.2 draws is one-way - ``app/cli.py`` ->
#: ``app.services`` -> (``app.reporting``, ``app.utils``) - and the two
#: services never import each other.
FORBIDDEN_IMPORT_PREFIXES: Final[tuple[str, ...]] = (
    "flask",
    "selenium",
    "webdriver_manager",
    "behave",
    "app.config",
    "app.utils.properties",
    "app.web",
    "app.automation",
    "app.pages",
    "app.logging_config",
    "app.services.test_run_service",
)

#: Directories scanned for call sites.  ``app/`` and ``features/`` are the
#: whole of the port's runtime code; ``tests/`` is excluded deliberately,
#: because this module is itself a caller.
SCANNED_SOURCE_DIRS: Final[tuple[str, ...]] = ("app", "features")


# =========================================================================== #
# The injection seam
#
# One recorder per writer, carrying the real sequence's name, so that a
# recorded call order *is* the module's order.  A recorder may delegate to the
# genuine writer, so a test can have the first two artifacts really written
# while the third fails - which is the only way to assert that a completed
# artifact survives.
# =========================================================================== #


class RecordedCall(NamedTuple):
    """One invocation of one writer, exactly as the fan-out made it.

    Attributes:
        name: :attr:`~app.services.WriterSpec.name` of the writer called.
        args: Positional arguments received.
        kwargs: Keyword arguments received.  ``base`` must appear here and not
            in :attr:`args`: it is the *third* parameter of
            :func:`app.reporting.write_rerun_txt`, whose second is ``path``, so
            a positional call would write the manifest to a directory-shaped
            destination.
    """

    name: str
    args: tuple[Any, ...]
    kwargs: dict[str, Any]

    @property
    def result_set(self) -> Any:
        """The document the writer was handed.

        :returns: The first positional argument.
        :raises AssertionError: If the fan-out passed no positional argument at
            all, which would mean the document reached the writer by keyword -
            a shape none of the four writers is required to accept.
        """
        assert self.args, f"{self.name} was called with no positional document"
        return self.args[0]


class _Recorder:
    """A stand-in writer that records its call and then behaves as told.

    Deliberately not a :class:`unittest.mock.Mock`: the fan-out's contract is
    about argument *shape* - positional document, keyword ``base``, nothing
    else - and a recorder that stores the call verbatim lets a test assert that
    shape directly instead of through a matcher.
    """

    def __init__(
        self,
        name: str,
        calls: list[RecordedCall],
        snapshots: dict[str, bytes],
        *,
        delegate: Callable[..., Path] | None = None,
        raises: BaseException | None = None,
    ) -> None:
        """Build one recorder.

        :param name: The writer name to record under, taken from the real
            :data:`app.services.WRITER_SEQUENCE`.
        :param calls: Shared, ordered log every recorder appends to.
        :param snapshots: Shared map of writer name to the bytes its artifact
            held immediately after it wrote, filled only when ``delegate`` is
            given and returned a file.  The failure tests compare these against
            what is on disk after the fan-out returned.
        :param delegate: The genuine writer to call, or ``None`` to write
            nothing and return a path that is never created.
        :param raises: Exception to raise instead of returning, or ``None``.
        """
        self._name = name
        self._calls = calls
        self._snapshots = snapshots
        self._delegate = delegate
        self._raises = raises

    def __call__(self, *args: Any, **kwargs: Any) -> Path:
        """Record the call, then fail or write as configured.

        :returns: The delegate's own return value, or - with no delegate - a
            path under the ``base`` that was passed, named after the writer and
            never created, so that a recorded fan-out leaves the temporary root
            completely empty.
        :raises BaseException: Whatever ``raises`` was constructed with.
        """
        self._calls.append(RecordedCall(self._name, args, dict(kwargs)))

        if self._raises is not None:
            raise self._raises

        if self._delegate is not None:
            path = self._delegate(*args, **kwargs)
            if path.is_file():
                self._snapshots[self._name] = path.read_bytes()
            return path

        base = kwargs.get("base")
        return Path(base if base is not None else ".") / self._name


@dataclass
class WriterHarness:
    """The fan-out's injection seam, and the record of what it observed.

    :func:`install` replaces ``report_service.WRITER_SEQUENCE`` through
    ``monkeypatch``, so the module's own tuple is restored at teardown and no
    later test in the session sees the substitution.
    """

    monkeypatch: pytest.MonkeyPatch
    calls: list[RecordedCall] = field(default_factory=list)
    snapshots: dict[str, bytes] = field(default_factory=dict)
    installed: tuple[WriterSpec, ...] = ()

    @property
    def called_names(self) -> tuple[str, ...]:
        """The writer names in the order the fan-out invoked them.

        :returns: One entry per invocation - repeated names included, so that a
            writer called twice is visible rather than collapsed away.
        """
        return tuple(call.name for call in self.calls)

    def call_for(self, name: str) -> RecordedCall:
        """The single recorded call for one writer.

        :param name: The writer name.
        :returns: Its recorded call.
        :raises AssertionError: If it was not called exactly once.
        """
        matching = [call for call in self.calls if call.name == name]
        assert len(matching) == 1, (
            f"expected exactly one call to {name}, recorded {len(matching)}"
        )
        return matching[0]

    def install(
        self,
        *,
        fails: Mapping[str, BaseException] | None = None,
        real: Sequence[str] = (),
    ) -> tuple[WriterSpec, ...]:
        """Install a recorder sequence carrying the real sequence's names.

        Each recorder keeps the real spec's ``artifact_key`` as well as its
        ``name``, so the destination a failure names - resolved by
        :meth:`app.services.WriterSpec.destination` from that key through
        ``app/utils/paths.py`` - is the real writer's destination and not one
        this module invented.

        :param fails: Writer name to the exception its recorder raises.
        :param real: Writer names whose recorder delegates to the genuine
            entry point, so its artifact is actually written.
        :returns: The installed sequence, for a test that wants to assert
            against the specs themselves.
        """
        failures = dict(fails or {})
        genuine = {spec.name: spec.write for spec in WRITER_SEQUENCE}

        self.installed = tuple(
            WriterSpec(
                name=spec.name,
                write=_Recorder(
                    spec.name,
                    self.calls,
                    self.snapshots,
                    delegate=genuine[spec.name] if spec.name in real else None,
                    raises=failures.get(spec.name),
                ),
                artifact_key=spec.artifact_key,
            )
            for spec in WRITER_SEQUENCE
        )
        self.monkeypatch.setattr(report_service, "WRITER_SEQUENCE", self.installed)
        return self.installed


@pytest.fixture
def writers(monkeypatch: pytest.MonkeyPatch) -> WriterHarness:
    """A :class:`WriterHarness` bound to this test's ``monkeypatch``.

    :param monkeypatch: pytest's patcher, for its guaranteed teardown.
    :returns: The harness; nothing is installed until ``install()`` is called,
        so a test that wants the real sequence simply does not call it.
    """
    return WriterHarness(monkeypatch=monkeypatch)


@pytest.fixture
def service_records(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> Callable[[], list[logging.LogRecord]]:
    """Capture the service's own log records, whatever the process did earlier.

    ``app/logging_config.py`` sets ``propagate = False`` on the ``app`` logger
    when it installs the stdout/stderr split, and it is called from both
    process entry points - so another test in the same session may already have
    detached this module's records from the root logger where ``caplog``'s
    handler lives.  Propagation is forced back on for the three loggers in the
    chain, through ``monkeypatch`` so the process is left exactly as it was.

    :param caplog: pytest's log-capture fixture.
    :param monkeypatch: pytest's patcher.
    :returns: A callable giving the records this module's logger emitted.
    """
    for name in ("app", "app.services", SERVICE_LOGGER_NAME):
        monkeypatch.setattr(logging.getLogger(name), "propagate", True)

    caplog.set_level(logging.INFO, logger=SERVICE_LOGGER_NAME)

    def records() -> list[logging.LogRecord]:
        return [
            record
            for record in caplog.records
            if record.name == SERVICE_LOGGER_NAME
        ]

    return records


# =========================================================================== #
# Source-scan helpers
#
# Four of this module's assertions are about the module's *text* rather than
# its behaviour, because they are prohibitions: a path literal that is not
# there, an import that is not there, a rerun branch that is not there, a
# second call site that is not there.  None of those can be observed from a
# call, and all four are stated contracts - so they are read out of the source
# with ast, never with a regular expression over raw text, which would match
# inside the docstrings that legitimately discuss all four.
# =========================================================================== #


def _parse(path: Path) -> ast.Module:
    """Parse one source file.

    :param path: The file to parse.
    :returns: Its module AST.
    """
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _docstring_nodes(tree: ast.Module) -> set[int]:
    """Identify every docstring constant in a parsed module.

    Docstrings are excluded from the literal scan because ``report_service``
    documents the paths it deliberately does not own - naming
    ``cucumber-html-reports`` while explaining that the tree writer returns a
    directory, for one - and prose about a path is the opposite of a path
    literal.

    :param tree: The parsed module.
    :returns: ``id()`` of each :class:`ast.Constant` serving as a docstring.
    """
    found: set[int] = set()
    holders = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)

    for node in ast.walk(tree):
        if not isinstance(node, holders):
            continue
        body = node.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            found.add(id(body[0].value))

    return found


def _runtime_string_literals(tree: ast.Module) -> tuple[str, ...]:
    """Every string constant a module evaluates at run time.

    :param tree: The parsed module.
    :returns: The string constants that are not docstrings.
    """
    docstrings = _docstring_nodes(tree)
    return tuple(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    )


def _imported_modules(tree: ast.Module) -> tuple[str, ...]:
    """Every module name a module imports, guarded imports included.

    :param tree: The parsed module.
    :returns: The imported module names.
    """
    names: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.append(node.module)

    return tuple(names)


def _called_names(tree: ast.Module) -> tuple[str, ...]:
    """The callee name of every call in a module.

    Both shapes are reported: ``open(...)`` by its name and ``path.unlink()``
    by its attribute, since a prohibited filesystem operation could be written
    either way.

    :param tree: The parsed module.
    :returns: The callee names.
    """
    names: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            names.append(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            names.append(node.func.attr)

    return tuple(names)


def _identifiers(tree: ast.Module) -> frozenset[str]:
    """Every identifier a module actually uses.

    Names, attributes, parameters, keyword-argument names and assignment
    targets - the whole of what a *branch* could be written against, which is
    what makes this the right way to prove a branch is absent.

    :param tree: The parsed module.
    :returns: The identifiers.
    """
    found: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, (ast.arg, ast.keyword)) and node.arg is not None:
            # ``ast.arg.arg`` is always a name; ``ast.keyword.arg`` is ``None``
            # for ``**kwargs``, which names nothing and is skipped.
            found.add(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found.add(node.name)

    return frozenset(found)


def _parameter_names(tree: ast.Module) -> frozenset[str]:
    """Every parameter name declared anywhere in a module.

    :param tree: The parsed module.
    :returns: The parameter names.
    """
    return frozenset(
        node.arg for node in ast.walk(tree) if isinstance(node, ast.arg)
    )


def _branch_identifiers(tree: ast.Module) -> frozenset[str]:
    """Every identifier a module *branches on*.

    Only the condition subtrees are read - ``if``, the conditional
    expression, ``while`` and ``match`` - because what proves a mode is absent
    is not that its word never appears but that nothing is decided by it.

    :param tree: The parsed module.
    :returns: The identifiers appearing in a branch condition.
    """
    conditions: list[ast.AST] = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.IfExp, ast.While)):
            conditions.append(node.test)
        elif isinstance(node, ast.Match):
            conditions.append(node.subject)

    found: set[str] = set()
    for condition in conditions:
        for node in ast.walk(condition):
            if isinstance(node, ast.Name):
                found.add(node.id)
            elif isinstance(node, ast.Attribute):
                found.add(node.attr)

    return frozenset(found)


def _calls_generate_reports(tree: ast.Module) -> bool:
    """Whether a module calls the fan-out.

    :param tree: The parsed module.
    :returns: ``True`` if ``generate_reports(...)`` is invoked, by bare name or
        through an attribute such as ``report_service.generate_reports(...)``.
    """
    return "generate_reports" in _called_names(tree)


def _python_sources(repo_root: Path) -> tuple[Path, ...]:
    """Every runtime Python source file of the port.

    :param repo_root: The repository root.
    :returns: The ``.py`` files under :data:`SCANNED_SOURCE_DIRS`, sorted, with
        absent directories skipped - a module the AAP lists may be written by a
        later change, and a scan that exploded on its absence would be
        asserting the file inventory rather than the call boundary.
    """
    found: list[Path] = []

    for name in SCANNED_SOURCE_DIRS:
        directory = repo_root / name
        if directory.is_dir():
            found.extend(sorted(directory.rglob("*.py")))

    return tuple(found)


def _looks_like_a_path(literal: str) -> bool:
    """Whether a string literal names a filesystem location.

    Three shapes count: a separator of either flavour, one of
    :data:`PATH_LITERAL_SUFFIXES`, and the build-output directory's own name.
    A writer name such as ``cucumber_json`` matches none of them, which is the
    distinction this predicate exists to draw.

    :param literal: The string constant to judge.
    :returns: ``True`` if it looks like a path.
    """
    if "/" in literal or "\\" in literal:
        return True
    if literal.strip().lower() == "target":
        return True
    return literal.lower().endswith(PATH_LITERAL_SUFFIXES)


@pytest.fixture
def service_source(repo_root: Path) -> ast.Module:
    """``app/services/report_service.py``, parsed.

    :param repo_root: conftest's repository root.
    :returns: The parsed module.
    """
    return _parse(repo_root / "app" / "services" / "report_service.py")


# =========================================================================== #
# Area 1 - the sequence itself
# =========================================================================== #


class TestWriterSequence:
    """``WRITER_SEQUENCE``: what it holds, and why in that order.

    This class is what makes the recorder-based tests below trustworthy: they
    assert the order of a substituted sequence, and these assert that the real
    sequence is the one whose names and callables they borrowed.
    """

    def test_holds_exactly_four_writers(self) -> None:
        """Four writers, one per Cucumber plugin (``CukesRunner.java:9-14``).

        Neither more - the port adds no artifact of its own - nor fewer, since
        AAP 0.4.1's exit table requires all four for every run that executed.
        """
        assert isinstance(WRITER_SEQUENCE, tuple)
        assert len(WRITER_SEQUENCE) == 4

    def test_names_are_the_contract_names_in_order(self) -> None:
        """The four names, in the fan-out's order.

        ``app/cli.py`` names the failing writer by this value on stderr, so
        each string is part of the port's observable contract rather than a
        label.
        """
        assert tuple(spec.name for spec in WRITER_SEQUENCE) == (
            EXPECTED_WRITER_NAMES
        )

    def test_every_write_is_the_app_reporting_entry_point(self) -> None:
        """Each ``write`` is *identically* the barrel's writer.

        Asserted by identity, not by name: the sequence is built at import time
        from direct function references, so an equal-looking wrapper in its
        place would silently change what a run writes.
        """
        bound = tuple(spec.write for spec in WRITER_SEQUENCE)
        assert bound == EXPECTED_WRITER_CALLABLES
        for spec, expected in zip(
            WRITER_SEQUENCE, EXPECTED_WRITER_CALLABLES, strict=True
        ):
            assert spec.write is expected, f"{spec.name} is not the real writer"

    def test_machine_read_contracts_run_before_the_human_facing_pages(
        self,
    ) -> None:
        """The two machine-read artifacts come first - the ordering rationale.

        ``Jenkins:15`` narrows ``fileIncludePattern`` to the single JSON report
        and ``FailedTestRunner.java:11`` declares ``features =
        "@target/rerun.txt"``, so those two artifacts have automated consumers
        and the two HTML outputs have none.  Stated as a property rather than a
        restatement of the expected order: every machine-read writer precedes
        every writer that is not one.
        """
        positions = {spec.name: index for index, spec in enumerate(WRITER_SEQUENCE)}
        machine = {positions[name] for name in MACHINE_READ_WRITERS}
        human = set(positions.values()) - machine

        assert machine, "no machine-read writer found in the sequence"
        assert human, "no human-facing writer found in the sequence"
        assert max(machine) < min(human)

    def test_order_is_neither_the_plugin_declaration_order_nor_alphabetical(
        self,
    ) -> None:
        """Two orders the sequence must not drift into.

        ``CukesRunner.java:9-14`` declared the HTML formatter first, but the
        JVM plugins were concurrent listeners so that order bound nothing;
        adopting it would put a human-readable page ahead of the publisher's
        only input.  Alphabetical order is the other plausible tidy-up, and
        ``sortingMethod: 'ALPHABETICAL'`` (``Jenkins:15``) is a publisher
        *display* option that imposes nothing on artifact production.
        """
        actual = tuple(spec.name for spec in WRITER_SEQUENCE)

        assert actual != PLUGIN_DECLARATION_ORDER
        assert actual != tuple(sorted(actual))
        assert set(actual) == set(PLUGIN_DECLARATION_ORDER)

    def test_writer_spec_is_a_name_a_callable_and_an_artifact_identity(
        self,
    ) -> None:
        """``WriterSpec`` carries a name, a callable and an artifact *key*.

        The third field is an identity, never a destination, and that is what
        keeps the invariant AAP 0.4.2 states: every destination belongs to
        ``app/utils/paths.py``, so the spec holds the key **that module
        publishes** and asks it to resolve one only for a diagnostic - which
        AAP 0.4.1's writer-failure row needs, because a template or model
        exception is not obliged to mention the artifact it was writing.  A
        fourth field would be the configuration this module still may not hold:
        no destination of its own, and no rendering argument, which belongs to
        the writer that accepts it.
        """
        assert issubclass(WriterSpec, tuple)
        assert WriterSpec._fields == ("name", "write", "artifact_key")

        # Identities, not paths: every key is identically the constant
        # ``app/utils/paths.py`` publishes for that artifact, and no field of
        # any spec is a path object.
        assert tuple(spec.artifact_key for spec in WRITER_SEQUENCE) == (
            EXPECTED_ARTIFACT_KEYS
        )
        for spec in WRITER_SEQUENCE:
            assert spec.artifact_key in {
                artifact.key for artifact in paths.ARTIFACT_SPECS
            }, spec.artifact_key
            assert not any(isinstance(value, Path) for value in spec)

    def test_writer_spec_resolves_its_destination_through_the_paths_module(
        self, tmp_artifact_root: Path
    ) -> None:
        """``destination()`` is the paths module's answer, and it never raises.

        The resolution is what lets a writer failure say *where* the writer was
        writing while this module still owns no path: each key goes back to
        :func:`app.utils.paths.artifact_path`, so the destination named in a
        diagnostic is by construction the one that writer's own accessor
        produces.  ``pretty_reports`` is the deliberate asymmetry - the key
        resolves to the report tree's root, which is the artifact the plugin
        list declared, while the writer returns the sub-directory it filled.

        The method is also called from a path where an exception is already
        being reported, so a key it cannot resolve must degrade to ``None``
        rather than displace the failure it was describing.
        """
        by_name = {spec.name: spec for spec in WRITER_SEQUENCE}

        assert by_name["cucumber_json"].destination(tmp_artifact_root) == (
            paths.cucumber_json_path(tmp_artifact_root)
        )
        assert by_name["rerun_txt"].destination(tmp_artifact_root) == (
            paths.rerun_txt_path(tmp_artifact_root)
        )
        assert by_name["html_report"].destination(tmp_artifact_root) == (
            paths.cucumber_reports_html_path(tmp_artifact_root)
        )
        assert by_name["pretty_reports"].destination(tmp_artifact_root) == (
            paths.pretty_reports_dir(tmp_artifact_root)
        )

        unresolvable = WriterSpec(
            name="cucumber_json",
            write=by_name["cucumber_json"].write,
            artifact_key="no-such-artifact",
        )
        assert unresolvable.destination(tmp_artifact_root) is None

    def test_barrel_re_exports_the_identical_sequence(self) -> None:
        """``app.services.WRITER_SEQUENCE`` is the defining module's own tuple.

        The barrel re-exports rather than rebuilds, so the order asserted here
        is the order a caller importing from ``app.services`` gets.
        """
        assert WRITER_SEQUENCE is report_service.WRITER_SEQUENCE


# =========================================================================== #
# Area 2 - the fan-out
# =========================================================================== #


class TestFanOut:
    """How :func:`generate_reports` drives the sequence."""

    def test_drives_every_writer_in_writer_sequence_order(
        self, writers: WriterHarness, sample_result_set: Any, tmp_artifact_root: Path
    ) -> None:
        """The recorded call *order* is the sequence's order.

        Order, not set membership: AAP 0.4.1's writer-failure row is the reason
        the order is observable at all, so a test that only checked which
        writers ran would leave the contract untested.
        """
        writers.install()

        generate_reports(sample_result_set, base=tmp_artifact_root)

        assert writers.called_names == EXPECTED_WRITER_NAMES
        assert writers.called_names == tuple(
            spec.name for spec in WRITER_SEQUENCE
        )

    def test_calls_each_writer_exactly_once(
        self, writers: WriterHarness, sample_result_set: Any, tmp_artifact_root: Path
    ) -> None:
        """One invocation per writer - "exactly one producer" per artifact.

        AAP 0.4.1's concurrency note: the engine writes only per-worker
        intermediates, and the fan-out runs once over the merged document, so
        no artifact can be written twice within one run.
        """
        writers.install()

        generate_reports(sample_result_set, base=tmp_artifact_root)

        assert len(writers.calls) == len(EXPECTED_WRITER_NAMES)
        assert sorted(writers.called_names) == sorted(EXPECTED_WRITER_NAMES)

    def test_hands_the_same_result_set_object_to_all_four_writers(
        self, writers: WriterHarness, sample_result_set: Any, tmp_artifact_root: Path
    ) -> None:
        """All four receive the *same object* - asserted with ``is``.

        AAP 0.3.3's "one merged result set, four independent writers": a copy
        per writer would let the four artifacts of one run describe four
        different documents, which is exactly what a single object forbids.
        """
        writers.install()

        generate_reports(sample_result_set, base=tmp_artifact_root)

        assert len(writers.calls) == 4
        for call in writers.calls:
            assert call.result_set is sample_result_set, (
                f"{call.name} received a different document object"
            )

    def test_forwards_base_to_every_writer_by_keyword(
        self, writers: WriterHarness, sample_result_set: Any, tmp_artifact_root: Path
    ) -> None:
        """``base`` reaches every writer as a keyword, and untouched.

        A correctness requirement rather than a style preference - see
        :meth:`test_rerun_writer_signature_makes_the_keyword_pass_mandatory`
        for the signature that makes it one.
        """
        writers.install()

        generate_reports(sample_result_set, base=tmp_artifact_root)

        for name in EXPECTED_WRITER_NAMES:
            call = writers.call_for(name)
            assert "base" in call.kwargs, f"{name} got base positionally"
            assert call.kwargs["base"] is tmp_artifact_root
            assert len(call.args) == 1, (
                f"{name} received more than the document positionally"
            )

    def test_rerun_writer_signature_makes_the_keyword_pass_mandatory(
        self,
    ) -> None:
        """Why ``base`` must be a keyword: the rerun writer's parameter order.

        :func:`app.reporting.write_rerun_txt` takes ``(result_set, path,
        base)`` while the other three take ``base`` second, so one positional
        call would hand the manifest writer a directory as its ``path`` - and
        ``FailedTestRunner.java:11`` reads that manifest as a file.
        """
        parameters = list(inspect.signature(write_rerun_txt).parameters)

        assert parameters[:3] == ["result_set", "path", "base"]
        assert list(inspect.signature(write_cucumber_json).parameters)[:2] == [
            "result_set",
            "base",
        ]

    def test_passes_no_destination_override_to_any_writer(
        self, writers: WriterHarness, sample_result_set: Any, tmp_artifact_root: Path
    ) -> None:
        """Only ``base`` is forwarded - never ``path`` or ``directory``.

        Each writer resolves its own destination through
        :mod:`app.utils.paths`, which AAP 0.4.2 makes the port's sole owner of
        every path; a destination passed from here would be a second owner.
        """
        writers.install()

        generate_reports(sample_result_set, base=tmp_artifact_root)

        for call in writers.calls:
            assert set(call.kwargs) == {"base"}, (
                f"{call.name} received unexpected keywords: {sorted(call.kwargs)}"
            )

    def test_base_is_keyword_only_on_the_fan_out_itself(self) -> None:
        """``generate_reports(result_set, *, base=None)``.

        The document is the fan-out's subject and ``base`` its one seam; making
        it keyword-only keeps a call site from ever confusing the two.
        """
        signature = inspect.signature(generate_reports)
        parameters = signature.parameters

        assert list(parameters) == ["result_set", "base"]
        assert parameters["result_set"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
        assert parameters["base"].kind is inspect.Parameter.KEYWORD_ONLY
        assert parameters["base"].default is None

    def test_writes_no_file_of_its_own(
        self, writers: WriterHarness, sample_result_set: Any, tmp_artifact_root: Path
    ) -> None:
        """With every writer recorded, the fan-out leaves the root empty.

        The behavioural half of "this module writes no file": the writers own
        all the I/O, so replacing them must leave nothing whatever on disk -
        not even the build-output directory.
        """
        writers.install()

        outcome = generate_reports(sample_result_set, base=tmp_artifact_root)

        assert outcome.ok
        assert list(tmp_artifact_root.iterdir()) == []
        assert not paths.target_root(tmp_artifact_root).exists()

    def test_source_holds_no_path_literal_and_no_filesystem_call(
        self, service_source: ast.Module
    ) -> None:
        """The textual half: no path literal, no write, no delete.

        AAP 0.4.2 gives ``app/utils/paths.py`` sole ownership of every path, and
        AAP 0.4.1's writer-failure row forbids deleting a completed artifact -
        so the absence of a delete call is the strongest form that row can be
        asserted in.  Docstrings are excluded, since the module's prose
        discusses the paths it deliberately does not own.
        """
        literals = _runtime_string_literals(service_source)
        offending = [value for value in literals if _looks_like_a_path(value)]
        assert offending == [], f"path literals found: {offending}"

        called = set(_called_names(service_source))
        assert called & FILESYSTEM_CALL_NAMES == set()
        assert "sorted" not in called, "the fan-out must not sort anything"

        imported = _imported_modules(service_source)
        for module in ("os", "shutil", "io", "tempfile"):
            assert module not in imported


# =========================================================================== #
# Area 3 - the document is read-only
# =========================================================================== #


class TestInputImmutability:
    """The merged document survives the fan-out unchanged."""

    def test_does_not_mutate_the_document_it_hands_to_four_writers(
        self, sample_result_set: Any, tmp_artifact_root: Path
    ) -> None:
        """Deep-compared against a deep copy, across a real fan-out.

        Run against the genuine writers rather than recorders, because the
        mutation this guards against could come from any of them: all four
        receive the same object, so a writer that annotated the document in
        place would corrupt the input of every writer still to run - and the
        artifacts of one run would stop describing one document.
        """
        expected = copy.deepcopy(sample_result_set)

        outcome = generate_reports(sample_result_set, base=tmp_artifact_root)

        assert outcome.ok, outcome.error
        assert sample_result_set == expected


# =========================================================================== #
# Area 4 - what the fan-out reports back
# =========================================================================== #


class TestOutcomeModel:
    """``ReportOutcome`` and ``WriterResult``: shape, and immutability."""

    def test_success_reports_four_results_four_paths_and_ok(
        self, writers: WriterHarness, sample_result_set: Any, tmp_artifact_root: Path
    ) -> None:
        """Every field of a fully successful outcome.

        ``ok`` describes artifact production and nothing else: a run whose
        scenarios failed still reports ``True``, because ``pom.xml:25`` sets
        ``testFailureIgnore=true`` and all six publisher thresholds are ``-1``
        (``Jenkins:15``) - and the fixture document this runs over carries a
        failing scenario.
        """
        installed = writers.install()

        outcome = generate_reports(sample_result_set, base=tmp_artifact_root)

        assert isinstance(outcome, ReportOutcome)
        assert outcome.ok is True
        assert outcome.failed_writer is None
        assert outcome.error is None
        assert outcome.skipped == ()

        assert len(outcome.results) == 4
        assert all(isinstance(result, WriterResult) for result in outcome.results)
        assert all(result.error is None for result in outcome.results)
        assert all(result.path is not None for result in outcome.results)

        assert outcome.written == tuple(
            result.path for result in outcome.results
        )
        assert len(outcome.written) == len(installed) == 4

    def test_writer_result_names_follow_the_writer_sequence_names(
        self, writers: WriterHarness, sample_result_set: Any, tmp_artifact_root: Path
    ) -> None:
        """Each result is labelled with its writer's contract name, in order.

        ``app/cli.py`` reads those names back, so a result labelled anything
        else would make the stderr message name a writer that does not exist.
        """
        writers.install()

        outcome = generate_reports(sample_result_set, base=tmp_artifact_root)

        assert tuple(result.name for result in outcome.results) == (
            EXPECTED_WRITER_NAMES
        )

    def test_writer_result_is_frozen(self) -> None:
        """A record of what a writer did cannot be edited afterwards."""
        result = WriterResult(name="cucumber_json", path=Path("unused"))

        assert dataclasses.is_dataclass(result)
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.path = Path("other")  # type: ignore[misc]

    def test_report_outcome_is_frozen(self) -> None:
        """The outcome ``app/cli.py`` maps to an exit status is frozen.

        The command line reads the outcome to choose a status; a mutable record
        would let a later step alter the verdict on the way to the exit call.
        """
        outcome = ReportOutcome(results=(), written=())

        assert dataclasses.is_dataclass(outcome)
        with pytest.raises(dataclasses.FrozenInstanceError):
            outcome.failed_writer = "cucumber_json"  # type: ignore[misc]

    def test_ok_is_derived_and_not_a_settable_field(self) -> None:
        """``ok`` is computed from ``failed_writer``, and cannot be assigned.

        A settable flag could disagree with the record it summarises; a
        property cannot.
        """
        assert isinstance(ReportOutcome.ok, property)
        assert ReportOutcome.ok.fset is None
        assert "ok" not in {
            field_.name for field_ in dataclasses.fields(ReportOutcome)
        }

        outcome = ReportOutcome(results=(), written=())
        assert outcome.ok is True
        with pytest.raises(dataclasses.FrozenInstanceError):
            outcome.ok = False  # type: ignore[misc]

        failed = ReportOutcome(
            results=(WriterResult(name="rerun_txt", error=OSError("disk")),),
            written=(),
            failed_writer="rerun_txt",
            error=OSError("disk"),
            skipped=("html_report", "pretty_reports"),
        )
        assert failed.ok is False


# =========================================================================== #
# Area 5 - the empty run is not a special case
# =========================================================================== #


class TestEmptyRun:
    """A run that selected no scenario still produces all four artifacts.

    AAP 0.4.1's exit table, zero-scenario row: *"Zero scenarios selected by the
    tag expression | 0 | All four written, empty"*.
    """

    def test_empty_document_still_drives_all_four_writers(
        self, writers: WriterHarness, tmp_artifact_root: Path
    ) -> None:
        """No short-circuit: four calls, in order, and ``ok``.

        The guard this forbids would look like an optimisation and would break
        the zero-scenario row silently.
        """
        writers.install()

        outcome = generate_reports(new_result_set(), base=tmp_artifact_root)

        assert writers.called_names == EXPECTED_WRITER_NAMES
        assert outcome.ok is True
        assert len(outcome.written) == 4

    def test_empty_run_writes_an_empty_json_list_for_the_publisher(
        self, tmp_artifact_root: Path
    ) -> None:
        """``target/cucumber.json`` holds exactly ``[]`` - not ``{}``, not absent.

        This is what guarantees the Jenkins publisher always has an input:
        ``Jenkins:15`` narrows ``fileIncludePattern`` to that one file, so a
        missing or non-list document leaves the CI stage nothing to ingest.
        """
        outcome = generate_reports(new_result_set(), base=tmp_artifact_root)
        assert outcome.ok, outcome.error

        json_path = paths.cucumber_json_path(tmp_artifact_root)
        assert json_path.is_file()

        document = json.loads(json_path.read_text(encoding="utf-8"))
        assert document == []
        assert isinstance(document, list)

    def test_empty_run_writes_the_manifest_the_page_and_the_overviews(
        self, tmp_artifact_root: Path
    ) -> None:
        """The other three artifacts exist for an empty run too.

        The rerun manifest is present and empty - a file with no failures in
        it, which is what ``FailedTestRunner.java:11``'s consumer reads as "no
        scenarios" - and both HTML outputs render, the report tree included:
        AAP 0.4.1 names the PrettyReports overviews for features, failures,
        steps and tags, and an empty run is not an exception to any of them.
        """
        outcome = generate_reports(new_result_set(), base=tmp_artifact_root)
        assert outcome.ok, outcome.error

        rerun_path = paths.rerun_txt_path(tmp_artifact_root)
        assert rerun_path.is_file()
        assert rerun_path.read_text(encoding="utf-8") == ""

        page = paths.cucumber_reports_html_path(tmp_artifact_root)
        assert page.is_file()
        assert page.read_text(encoding="utf-8").strip() != ""

        index = paths.pretty_reports_index_path(tmp_artifact_root)
        assert index.is_file()

        tree = paths.pretty_reports_html_dir(tmp_artifact_root)
        overviews = {path.name for path in tree.glob("overview-*.html")}
        assert index.name in overviews
        assert len(overviews) >= 4, sorted(overviews)


# =========================================================================== #
# Area 6 - a writer fails, and what survives it
#
# AAP 0.4.1's exit table, writer-failure row: "The artifacts written before the
# failure remain; the failing writer is named on stderr, and the run does not
# delete completed artifacts."
# =========================================================================== #


class WriterBroke(RuntimeError):
    """The injected failure.

    A distinct class so a test can assert the very exception it injected came
    back on the outcome, rather than something that merely looks like it.
    """


class TestWriterFailure:
    """Failure is reported, is fail-fast, and rolls nothing back."""

    def test_third_writer_failure_keeps_the_first_two_artifacts_and_fails_fast(
        self, writers: WriterHarness, sample_result_set: Any, tmp_artifact_root: Path
    ) -> None:
        """The exit table's row, in full, with the first two artifacts real.

        The first two writers are the genuine entry points, so ``cucumber.json``
        and ``rerun.txt`` are really on disk when the third raises.  Their bytes
        are captured the instant each writer returned and compared against disk
        after the fan-out, which is what proves the failure path neither
        rewrote nor removed them.

        **Fail-fast is pinned here deliberately**: the fourth writer must not
        be attempted at all.  It is named in ``skipped`` instead, so the command
        line can report what was not produced rather than leaving a caller to
        infer it.
        """
        failure = WriterBroke("template fault")
        writers.install(
            real=("cucumber_json", "rerun_txt"),
            fails={"html_report": failure},
        )

        outcome = generate_reports(sample_result_set, base=tmp_artifact_root)

        # Nothing propagated: app/cli.py owns the exit status, and a traceback
        # out of the fan-out would bypass it.
        assert outcome.ok is False
        assert outcome.failed_writer == "html_report"
        assert outcome.error is failure

        # Fail-fast: three attempted, the fourth never called.
        assert writers.called_names == ("cucumber_json", "rerun_txt", "html_report")
        assert "pretty_reports" not in writers.called_names
        assert outcome.skipped == ("pretty_reports",)

        assert tuple(result.name for result in outcome.results) == (
            "cucumber_json",
            "rerun_txt",
            "html_report",
        )
        assert outcome.results[-1].error is failure
        assert outcome.results[-1].path is None

        # The two completed artifacts: still there, and byte-for-byte what
        # their writers left.
        assert len(outcome.written) == 2
        assert outcome.written == (
            paths.cucumber_json_path(tmp_artifact_root),
            paths.rerun_txt_path(tmp_artifact_root),
        )
        for path in outcome.written:
            assert path.is_file(), f"{path} was deleted by the failure path"
        assert writers.snapshots["cucumber_json"] == (
            paths.cucumber_json_path(tmp_artifact_root).read_bytes()
        )
        assert writers.snapshots["rerun_txt"] == (
            paths.rerun_txt_path(tmp_artifact_root).read_bytes()
        )

        # Nothing beyond those two artifacts was produced or removed.
        assert not paths.cucumber_reports_html_path(tmp_artifact_root).exists()
        assert not paths.pretty_reports_html_dir(tmp_artifact_root).exists()

    def test_first_writer_failure_reports_it_and_names_the_three_skipped(
        self, writers: WriterHarness, sample_result_set: Any, tmp_artifact_root: Path
    ) -> None:
        """A failure at the head of the sequence: nothing written, three skipped.

        The command line still gets a complete account - which writer failed,
        and which three never ran - and the temporary root is left exactly as
        the clean step left it.
        """
        failure = WriterBroke("no space left")
        writers.install(fails={"cucumber_json": failure})

        outcome = generate_reports(sample_result_set, base=tmp_artifact_root)

        assert outcome.ok is False
        assert outcome.failed_writer == "cucumber_json"
        assert outcome.error is failure
        assert outcome.written == ()
        assert outcome.skipped == ("rerun_txt", "html_report", "pretty_reports")
        assert writers.called_names == ("cucumber_json",)
        assert len(outcome.results) == 1
        assert list(tmp_artifact_root.iterdir()) == []

    def test_last_writer_failure_leaves_nothing_skipped(
        self, writers: WriterHarness, sample_result_set: Any, tmp_artifact_root: Path
    ) -> None:
        """A failure in the final writer: three artifacts, and an empty ``skipped``.

        ``skipped`` is empty for a *completed* fan-out and for a failure in the
        last writer alike, which is why ``ok`` is read from ``failed_writer``
        and never from ``skipped``.
        """
        failure = WriterBroke("asset missing")
        writers.install(fails={"pretty_reports": failure})

        outcome = generate_reports(sample_result_set, base=tmp_artifact_root)

        assert outcome.ok is False
        assert outcome.failed_writer == "pretty_reports"
        assert outcome.skipped == ()
        assert writers.called_names == EXPECTED_WRITER_NAMES
        assert len(outcome.written) == 3
        assert len(outcome.results) == 4

    def test_failure_logs_an_error_record_naming_the_failing_writer(
        self,
        writers: WriterHarness,
        sample_result_set: Any,
        tmp_artifact_root: Path,
        service_records: Callable[[], list[logging.LogRecord]],
    ) -> None:
        """The failing writer, its destination and its traceback, at ``ERROR``.

        AAP 0.4.1 requires the failing writer on stderr, and
        ``app/logging_config.py`` is what puts it there: it routes ``WARNING``
        and above to stderr, so an ``ERROR`` record here satisfies the row
        without this module importing that configuration or touching a stream.

        The destination is part of that row rather than an extra: a template
        fault or a model error need not mention a path of its own, so a record
        naming only the writer cannot be acted on.  It is resolved from the
        spec's ``artifact_key`` against the same ``base`` the writer was given
        - so it is that writer's own destination - and the same value is
        carried onward on :attr:`~app.services.ReportOutcome.failed_path` for
        ``app/cli.py`` to name without deriving a path of its own.
        """
        failure = WriterBroke("template fault")
        writers.install(fails={"html_report": failure})

        outcome = generate_reports(sample_result_set, base=tmp_artifact_root)
        assert outcome.failed_writer == "html_report"

        expected_destination = paths.cucumber_reports_html_path(tmp_artifact_root)
        assert outcome.failed_path == expected_destination

        errors = [
            record
            for record in service_records()
            if record.levelno == logging.ERROR
        ]
        assert errors, "no ERROR record was emitted for the failing writer"

        naming = [
            record for record in errors if "html_report" in record.getMessage()
        ]
        assert naming, [record.getMessage() for record in errors]
        assert str(expected_destination) in naming[0].getMessage(), (
            naming[0].getMessage()
        )
        assert naming[0].exc_info is not None, (
            "the failure was reported without its traceback"
        )

    def test_the_writers_it_did_not_attempt_are_carried_not_logged_twice(
        self,
        writers: WriterHarness,
        sample_result_set: Any,
        tmp_artifact_root: Path,
        service_records: Callable[[], list[logging.LogRecord]],
    ) -> None:
        """The skipped writers reach the operator once, from one emitter.

        An operator reading only stderr still learns that a later artifact was
        never produced - but that line has a single owner.  This module reports
        the **cause** with its traceback, where the exception was caught, and
        carries every other fact on the outcome;
        ``app/cli.py``'s ``_writer_failure_status`` emits the consequence,
        including ``"Not attempted after <writer> failed: ..."`` from
        :attr:`~app.services.ReportOutcome.skipped`, which
        ``tests/test_cli.py`` asserts.  Naming them here as well - which this
        module did until the duplication was reviewed - made one incident two
        ERROR records under two logger names, inflating the error count a CI
        console shows and leaving neither layer the account of it.

        So what is asserted is both halves of that rule: the names are on the
        outcome for the command line to read, and this module emitted exactly
        one ERROR record, which is about the cause rather than the skipping.
        """
        writers.install(fails={"cucumber_json": WriterBroke("no space left")})

        outcome = generate_reports(sample_result_set, base=tmp_artifact_root)

        # Carried, in sequence order, so the command line can name them.
        assert outcome.skipped == ("rerun_txt", "html_report", "pretty_reports")

        errors = [
            record
            for record in service_records()
            if record.levelno == logging.ERROR
        ]
        messages = [record.getMessage() for record in errors]
        assert len(errors) == 1, messages
        assert "cucumber_json" in messages[0]
        assert not any(
            name in messages[0] for name in outcome.skipped
        ), messages[0]

    def test_success_logs_one_progress_record_per_artifact(
        self,
        writers: WriterHarness,
        sample_result_set: Any,
        tmp_artifact_root: Path,
        service_records: Callable[[], list[logging.LogRecord]],
    ) -> None:
        """Four ``INFO`` records, one per artifact, and no ``ERROR``.

        ``INFO`` is the level ``app/logging_config.py`` routes to stdout, which
        is the progress half of the CLI's stream split.
        """
        writers.install()

        generate_reports(sample_result_set, base=tmp_artifact_root)

        records = service_records()
        assert [record.levelno for record in records] == [logging.INFO] * 4
        for name in EXPECTED_WRITER_NAMES:
            assert any(name in record.getMessage() for record in records), name

    @pytest.mark.parametrize(
        "failure",
        [
            pytest.param(OSError("disk full"), id="os-error"),
            pytest.param(ValueError("unserialisable value"), id="value-error"),
            pytest.param(KeyError("features"), id="key-error"),
            pytest.param(RuntimeError("template not found"), id="runtime-error"),
        ],
    )
    def test_an_ordinary_exception_never_escapes_the_fan_out(
        self,
        failure: Exception,
        writers: WriterHarness,
        sample_result_set: Any,
        tmp_artifact_root: Path,
    ) -> None:
        """Any ``Exception`` becomes an outcome, whatever its class.

        The boundary is broad on purpose: the writers document ``OSError`` and
        template faults, but this is the outermost point of artifact
        production, so a class nobody anticipated must still become a reported
        outcome rather than a traceback out of the command line.
        """
        writers.install(fails={"rerun_txt": failure})

        outcome = generate_reports(sample_result_set, base=tmp_artifact_root)

        assert outcome.ok is False
        assert outcome.error is failure
        assert outcome.failed_writer == "rerun_txt"
        assert outcome.skipped == ("html_report", "pretty_reports")


# =========================================================================== #
# Area 7 - a BaseException is not an artifact failure
# =========================================================================== #


class TestBaseExceptionPassesThrough:
    """An interrupt stops the run; it is not reported as a writer failure."""

    @pytest.mark.parametrize(
        "failure",
        [
            pytest.param(KeyboardInterrupt(), id="keyboard-interrupt"),
            pytest.param(SystemExit(2), id="system-exit"),
        ],
    )
    def test_base_exception_from_a_writer_propagates(
        self,
        failure: BaseException,
        writers: WriterHarness,
        sample_result_set: Any,
        tmp_artifact_root: Path,
    ) -> None:
        """``KeyboardInterrupt`` and ``SystemExit`` leave the fan-out untouched.

        The module catches ``Exception``, not ``BaseException``: an interrupt is
        the operator stopping the run, and swallowing it into an outcome would
        turn Ctrl-C into a non-zero writer failure - and would let a
        ``SystemExit`` raised deeper in the stack be reported as a bad report
        instead of an exiting process.
        """
        writers.install(fails={"html_report": failure})

        with pytest.raises(type(failure)) as raised:
            generate_reports(sample_result_set, base=tmp_artifact_root)

        assert raised.value is failure
        assert writers.called_names == (
            "cucumber_json",
            "rerun_txt",
            "html_report",
        )


# =========================================================================== #
# Area 8 - the caller boundary
# =========================================================================== #


class TestCallerBoundary:
    """Who calls the fan-out, what it knows, and what it imports."""

    def test_app_cli_is_the_only_call_site(self, repo_root: Path) -> None:
        """Exactly one caller: ``app/cli.py``.

        The dependency edge AAP 0.4.2 draws is ``app/cli.py`` ->
        ``app.services`` -> (``app.reporting``, ``app.utils``).  A second
        caller - a route, a step module, the worker service - would give an
        artifact a second producer, which AAP 0.4.1's concurrency note
        forbids: the engine writes per-worker intermediates only, and the
        fan-out runs once, in the parent, over the merged document.
        """
        sources = _python_sources(repo_root)
        assert sources, "no runtime sources found to scan"

        callers = sorted(
            source.relative_to(repo_root).as_posix()
            for source in sources
            if _calls_generate_reports(_parse(source))
        )

        assert callers == ["app/cli.py"]

    def test_no_writer_runs_inside_a_worker(self, repo_root: Path) -> None:
        """The worker service neither imports nor calls the fan-out.

        A worker produces an intermediate result document and nothing else
        (AAP 0.4.1): if it wrote an artifact, N workers would produce N
        conflicting versions of the same file and the publisher would read
        whichever finished last.
        """
        worker_service = repo_root / "app" / "services" / "test_run_service.py"
        assert worker_service.is_file(), worker_service

        tree = _parse(worker_service)
        assert not _calls_generate_reports(tree)

        imported = _imported_modules(tree)
        assert not any(
            module.endswith("report_service") for module in imported
        ), imported
        assert "generate_reports" not in _identifiers(tree)

    def test_the_fan_out_has_no_rerun_awareness(
        self, service_source: ast.Module
    ) -> None:
        """No ``rerun`` parameter, and no ``rerun`` branch.

        Under ``--rerun`` this service is not invoked at all: the second Java
        runner declared an empty plugin list (``FailedTestRunner.java:9-12``),
        so a rerun writes no artifact and leaves the existing ones alone.  That
        decision is ``app/cli.py``'s, and a flag here would move it into the
        wrong file.

        Asserted over parameters and branch conditions rather than raw text,
        because the module's prose explains this very absence - and because the
        word legitimately occurs in two names that are about the manifest
        rather than about a mode: ``write_rerun_txt``, the manifest *writer*
        and a member of the fan-out, and ``RERUN_TXT_NAME``, the artifact
        identity :mod:`app.utils.paths` publishes for what that writer
        produces, which the fan-out holds so a failure can name its
        destination.
        """
        assert "rerun" not in inspect.signature(generate_reports).parameters

        rerun_parameters = sorted(
            name
            for name in _parameter_names(service_source)
            if "rerun" in name.lower()
        )
        assert rerun_parameters == [], rerun_parameters

        rerun_branches = sorted(
            name
            for name in _branch_identifiers(service_source)
            if "rerun" in name.lower()
        )
        assert rerun_branches == [], rerun_branches

        rerun_identifiers = {
            name
            for name in _identifiers(service_source)
            if "rerun" in name.lower()
        }
        assert rerun_identifiers <= {
            "write_rerun_txt",
            "RERUN_TXT_NAME",
        }, sorted(rerun_identifiers)

    def test_the_barrel_re_exports_the_whole_fan_out_surface(self) -> None:
        """``app.services`` advertises the five names this module imports.

        ``app/services/__init__.py`` calls itself the one import surface for
        ``app/cli.py`` and for this test module, so the barrel is the seam used
        here - and every name has to be the defining module's own object, not a
        copy.
        """
        import app.services as barrel

        expected = {
            "generate_reports": generate_reports,
            "WRITER_SEQUENCE": WRITER_SEQUENCE,
            "WriterSpec": WriterSpec,
            "WriterResult": WriterResult,
            "ReportOutcome": ReportOutcome,
        }

        for name, obj in expected.items():
            assert name in barrel.__all__, f"{name} missing from the barrel"
            assert getattr(barrel, name) is obj
            assert getattr(report_service, name) is obj

        assert set(report_service.__all__) == set(expected)

    def test_imports_stay_inside_the_declared_boundary(
        self, service_source: ast.Module
    ) -> None:
        """The standard library, the reporting barrel and the paths module.

        Those are the ``SV --> RP`` and ``SV --> UT`` edges of AAP 0.4.2's
        dependency graph, and nothing else travels: what the exclusions buy is
        stated in the module's own docstring - no Flask and no Selenium, so the
        fan-out is callable from a process that builds no web application and
        starts no browser; no configuration module, since the graph has no
        services-to-configuration edge; and not the sibling service, because
        ``app/cli.py`` is what connects the two.

        The ``app.utils.paths`` edge is the artifact *identity* import: the
        four keys the fan-out holds and the one resolver a failure diagnostic
        names its destination with.  Reaching the port's path owner is the
        opposite of owning a path - a second spelling of any destination here
        is what AAP 0.4.2 exists to prevent, and the separate source scan for
        path literals is what pins that.
        """
        imported = _imported_modules(service_source)

        for module in imported:
            for forbidden in FORBIDDEN_IMPORT_PREFIXES:
                assert module != forbidden, (
                    f"{module} is outside the declared import boundary"
                )
                assert not module.startswith(f"{forbidden}."), (
                    f"{module} is outside the declared import boundary"
                )

        application_imports = sorted(
            module for module in imported if module.split(".")[0] == "app"
        )
        assert application_imports, "the writers must be imported from somewhere"
        assert all(
            module == "app.reporting"
            or module.startswith("app.reporting.")
            or module == "app.utils.paths"
            for module in application_imports
        ), application_imports
        assert "app.utils.paths" in application_imports, application_imports


# =========================================================================== #
# Area 9 - the real writers, over the real paths
# =========================================================================== #


class TestRealWriters:
    """One fan-out with nothing injected, asserted against ``app.utils.paths``."""

    def test_the_four_artifacts_land_at_the_paths_module_locations(
        self, sample_result_set: Any, tmp_artifact_root: Path
    ) -> None:
        """Each artifact exists at exactly the accessor's location.

        These four accessors are the destinations the Java plugin list declared
        (``CukesRunner.java:9-14``) and AAP 0.4.2 makes
        ``app/utils/paths.py`` their sole owner, so the fan-out is correct only
        if what it produced is where that module says it is.
        """
        outcome = generate_reports(sample_result_set, base=tmp_artifact_root)

        assert outcome.ok is True, outcome.error
        assert paths.cucumber_json_path(tmp_artifact_root).is_file()
        assert paths.rerun_txt_path(tmp_artifact_root).is_file()
        assert paths.cucumber_reports_html_path(tmp_artifact_root).is_file()
        assert paths.pretty_reports_index_path(tmp_artifact_root).is_file()

    def test_written_paths_are_the_writers_own_return_values(
        self, sample_result_set: Any, tmp_artifact_root: Path
    ) -> None:
        """``written`` records what each writer returned - directory included.

        The asymmetry is the writers' business and is simply recorded: the
        three file writers return their file, while
        :func:`app.reporting.write_pretty_reports` returns the *directory* it
        wrote, whose index page is one file inside it.  A fan-out that
        "corrected" that to the index page would be inventing a path it does
        not own.
        """
        outcome = generate_reports(sample_result_set, base=tmp_artifact_root)
        assert outcome.ok, outcome.error

        assert outcome.written == (
            paths.cucumber_json_path(tmp_artifact_root),
            paths.rerun_txt_path(tmp_artifact_root),
            paths.cucumber_reports_html_path(tmp_artifact_root),
            paths.pretty_reports_html_dir(tmp_artifact_root),
        )

        *files, tree = outcome.written
        assert all(path.is_file() for path in files)
        assert tree.is_dir()
        assert paths.pretty_reports_index_path(tmp_artifact_root).parent == tree


# =========================================================================== #
# Area 10 - determinism
# =========================================================================== #


class TestDeterminism:
    """Two fan-outs over one document agree on everything a run can control.

    Content is compared through ``conftest``'s ``normalize_volatile``, which
    canonicalises the five fields AAP 0.6 lists as inherently variable -
    ``start_timestamp``, ``started_at``/``generated_at``, ``duration``,
    ``error_message`` and embedding ``data``.  ``target/rerun.txt`` is the one
    artifact compared verbatim, because it carries none of them: it is feature
    paths and line numbers only.  Byte equality is never asserted over a whole
    HTML or JSON artifact.
    """

    def test_two_calls_produce_the_same_paths_in_the_same_writer_order(
        self, sample_result_set: Any, tmp_artifact_root: Path
    ) -> None:
        """Same artifact set, same locations, same order of production."""
        first = generate_reports(sample_result_set, base=tmp_artifact_root)
        second = generate_reports(sample_result_set, base=tmp_artifact_root)

        assert first.ok, first.error
        assert second.ok, second.error
        assert first.written == second.written
        assert tuple(result.name for result in first.results) == tuple(
            result.name for result in second.results
        )
        assert tuple(result.name for result in first.results) == (
            EXPECTED_WRITER_NAMES
        )

    def test_two_calls_produce_the_same_content(
        self,
        sample_result_set: Any,
        tmp_artifact_root: Path,
        normalize_volatile: Callable[[Any], Any],
    ) -> None:
        """Structure is identical; the manifest is identical byte for byte.

        A second run over one document is the closest thing this suite has to a
        reproducibility check on the fan-out: anything that differed between
        the two would have come from the writers' own ordering rather than from
        the document.
        """
        generate_reports(sample_result_set, base=tmp_artifact_root)
        json_path = paths.cucumber_json_path(tmp_artifact_root)
        rerun_path = paths.rerun_txt_path(tmp_artifact_root)
        page_path = paths.cucumber_reports_html_path(tmp_artifact_root)

        first_json = json.loads(json_path.read_text(encoding="utf-8"))
        first_rerun = rerun_path.read_text(encoding="utf-8")
        assert page_path.stat().st_size > 0

        generate_reports(sample_result_set, base=tmp_artifact_root)

        # Structure, through the normalizer - never bytes, for the reason
        # conftest's Determinism section gives: a measured duration and a
        # generation timestamp differ between two runs by construction.
        second_json = json.loads(json_path.read_text(encoding="utf-8"))
        assert normalize_volatile(second_json) == normalize_volatile(first_json)

        # The manifest is the one artifact comparable verbatim: feature paths
        # and line numbers, and no field a run gets to choose.
        assert rerun_path.read_text(encoding="utf-8") == first_rerun

        # The page was rewritten in place rather than left half-written.
        assert page_path.stat().st_size > 0

    def test_the_fan_out_introduces_no_alphabetical_sorting(
        self, sample_result_set: Any, tmp_artifact_root: Path
    ) -> None:
        """Neither the writer order nor the written paths are sorted.

        ``sortingMethod: 'ALPHABETICAL'`` (``Jenkins:15``) is a publisher
        display option; imposing it on production would reorder the sequence
        and put a human-readable page ahead of the publisher's only input.
        """
        outcome = generate_reports(sample_result_set, base=tmp_artifact_root)
        assert outcome.ok, outcome.error

        names = [result.name for result in outcome.results]
        assert names != sorted(names)

        written = [str(path) for path in outcome.written]
        assert written != sorted(written)


# =========================================================================== #
# A closing guard on this module itself
# =========================================================================== #


def test_every_path_lookup_in_this_module_passes_a_base() -> None:
    """No test here may resolve an artifact path against the checkout.

    The artifact-path seam exists so that a writer test builds its artifacts
    under pytest's ``tmp_path``: every :mod:`app.utils.paths` accessor takes
    ``base``, and it defaults to the *working directory*, so one call that
    omitted it would create ``target/`` inside the repository and the
    pipeline's publisher would then be reading a unit test's output.  Asserted
    over this file's own source, so the rule cannot decay as tests are added
    above - and no ``chdir``, for the reason ``conftest`` gives: it leaks into
    whatever runs next in the same process.
    """
    tree = _parse(Path(__file__))

    lookups = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "paths"
    ]
    assert lookups, "no path accessor call found in this module"

    for call in lookups:
        accessor = call.func.attr  # type: ignore[union-attr]
        supplied = len(call.args) + len(call.keywords)
        assert supplied >= 1, (
            f"paths.{accessor}() on line {call.lineno} omits base"
        )

    assert "chdir" not in _called_names(tree)
