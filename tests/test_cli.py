"""Tests for the ``run-tests`` command - its option surface and its exit contract.

``app/cli.py`` owns exactly two things, and this module is the gate for both:
the **six options** of the AAP 0.4.1 CLI table and the **six rows** of the
0.4.1 exit table.  Everything the command does with them is asserted here -
what reaches the service layer, what status leaves the process, what is on
disk when it does, which stream each message went to, and what was cleaned up
on the way out.  Execution belongs to ``tests/test_test_run_service.py`` and
artifact production to ``tests/test_report_service.py``; neither is re-asserted
here.

The sixteen numbered requirements in ``app/cli.py``'s own
"What ``tests/test_cli.py`` must cover" section map onto the sections below:

``A``
    Requirements 1 and 2 - the defaults, each option individually, the usage
    errors, ``--help``, the command name and the declared console script.
``B``
    AAP 0.4.1's "no environment layer is added": userdata is the only
    override path.
``C``
    AAP 0.4.1's "an unknown ``browser`` value" exits ``0``.
``D``
    Requirements 3 and 4 - the four coupled ``--rerun`` behaviours.
``E``
    Requirements 5 to 9, and 16 - one test per row of the exit table.
``F``
    Requirement 10 - the per-worker directory, removed unconditionally.
``G``
    Requirement 11 - ``--clean`` and ``--no-clean``.
``H``
    Requirement 12 - stream routing, and the logging the contract rests on.
``I``
    Requirements 13, 14 and 15 - no path literal, import purity, and the two
    entry points that reach this command.
``J``
    The real writers, end to end, and the proof that none of this reaches the
    repository's own build output.

Two properties of this module are load-bearing, and both are stated here
because a later reader would otherwise be right to remove them.

**Every test that can reach a path changes the working directory first.**
``app/cli.py`` takes no ``base`` seam: ``_empty_build_output()``,
``cleanup_workers_dir()``, ``run_suite()`` and ``generate_reports()`` all
resolve their paths from the process working directory, exactly as they do in
a real run.  A ``--clean`` test executed in the repository root would delete
real build output, so :fixture:`cli_root` calls ``monkeypatch.chdir`` into
:fixture:`tmp_artifact_root` before the command is invoked.  ``chdir`` is the
one thing ``tests/conftest.py`` asks fixtures not to do, and that note is
about fixtures that could pass ``base=`` instead; this command cannot be given
a base, ``monkeypatch.chdir`` is restored at teardown, and this file's own
contract authorises it here.  Nothing in this module writes outside
``tmp_path``, and :func:`test_the_repository_build_output_is_never_touched`
asserts that outright.

**This module restores the ``app`` logger itself.**  ``configure_logging()``
installs two named handlers on the process-global ``app`` logger and sets
``propagate`` to ``False``; the command calls it on every invocation and
``tests/conftest.py`` does not reset it.  :fixture:`restore_app_logging`
snapshots and restores that logger around every test in this module, so the
suite's behaviour does not depend on whether this module ran first.

Nothing here spawns a worker, starts a browser or needs a populated
``configuration.properties``.  The service layer is replaced at the two names
``app/cli.py`` binds at import - ``app.cli.run_suite`` and
``app.cli.generate_reports`` - except in section J, which drives the real
writers over ``tests/fixtures/sample_results.json`` inside a temporary root.
"""

from __future__ import annotations

import ast
import io
import json
import logging
import re
import subprocess
import sys
import tomllib
from collections.abc import Callable, Iterator, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any, Final

import pytest
from click import Command, Group, Parameter
from click.testing import CliRunner, Result

import app.cli as cli
from app import create_app
from app.logging_config import (
    PACKAGE_LOGGER_NAME,
    STDERR_HANDLER_NAME,
    STDOUT_HANDLER_NAME,
)
from app.reporting import new_result_set
from app.services import (
    WRITER_SEQUENCE,
    ReportOutcome,
    RunOutcome,
    WriterResult,
    default_worker_count,
)
from app.utils import paths, properties

# --------------------------------------------------------------------------- #
# Locations and fixed names
#
# Every path name below is taken from the module that owns it rather than
# spelled out here, which is the same rule app/cli.py itself obeys and what
# makes test_module_source_contains_no_path_literal a real check instead of a
# restatement of one file's literals in another.
# --------------------------------------------------------------------------- #

#: The repository root, from this file's own position: ``tests/`` -> root.
REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]

#: The module under test, read as source by the boundary tests in section I.
CLI_SOURCE_PATH: Final[Path] = REPO_ROOT / "app" / "cli.py"

#: The two service modules, for the "neither imports the other" assertion.
RUN_SERVICE_PATH: Final[Path] = REPO_ROOT / "app" / "services" / "test_run_service.py"
REPORT_SERVICE_PATH: Final[Path] = (
    REPO_ROOT / "app" / "services" / "report_service.py"
)

#: The declaration that has to agree with :data:`app.cli.COMMAND_NAME`.
PYPROJECT_PATH: Final[Path] = REPO_ROOT / "pyproject.toml"

#: The callers AAP 0.4.1 names: both runner scripts and the developer target.
RUNNER_ENTRY_POINT_PATHS: Final[tuple[Path, ...]] = (
    REPO_ROOT / "scripts" / "run_tests.sh",
    REPO_ROOT / "scripts" / "run_tests.ps1",
    REPO_ROOT / "Makefile",
)

#: The six options, by the Python names ``app/cli.py`` binds them to.  The
#: order is the declaration order of the CLI table in AAP 0.4.1.
EXPECTED_PARAMETER_NAMES: Final[tuple[str, ...]] = (
    "tags",
    "browser",
    "workers",
    "dry_run",
    "rerun",
    "clean",
)

#: The keyword arguments ``app/cli.py`` hands to the run service - the whole
#: set, so a seventh appearing is a failure rather than a silent addition.
EXPECTED_SERVICE_KEYWORDS: Final[frozenset[str]] = frozenset(
    {"tags", "browser", "workers", "dry_run", "rerun"}
)

#: What ``--help`` must list, and nothing else.  ``--help`` itself is Click's
#: and is the *only* entry beyond the six.
EXPECTED_HELP_FLAGS: Final[tuple[str, ...]] = (
    "--tags",
    "--browser",
    "--workers",
    "--dry-run",
    "--rerun",
    "--clean / --no-clean",
    "--help",
)

#: The four writer names, in :data:`app.services.WRITER_SEQUENCE` order.  Taken
#: from the service so the writer-failure row follows the settled order.
WRITER_NAMES: Final[tuple[str, ...]] = tuple(spec.name for spec in WRITER_SEQUENCE)

#: The two logger names an incident's records are attributed to.  The port's
#: one-emitter rule splits a writer failure between them - the cause with its
#: traceback from the service that caught it, the consequence from this command
#: - so a test asserting "each fact once" has to tell them apart by emitter.
CLI_LOGGER_NAME: Final[str] = cli.logger.name
REPORT_SERVICE_LOGGER_NAME: Final[str] = "app.services.report_service"

#: Trace labels for the composition-order assertions.
CONFIGURE_LOGGING_LABEL: Final[str] = "configure_logging"
RUN_SUITE_LABEL: Final[str] = "run_suite"
GENERATE_REPORTS_LABEL: Final[str] = "generate_reports"

#: The one line the command logs before it does anything else, used to count
#: records: a duplicated record would double every line of a Jenkins console
#: log, which is what ``configure_logging``'s idempotence exists to prevent.
START_MESSAGE_MARKER: Final[str] = "Starting the suite:"

#: The levels the two handlers partition records by (``app/logging_config.py``
#: routes ``INFO`` and below to stdout, ``WARNING`` and above to stderr).
STDOUT_LEVEL_PREFIXES: Final[tuple[str, ...]] = ("DEBUG ", "INFO ")
STDERR_LEVEL_PREFIXES: Final[tuple[str, ...]] = (
    "WARNING ",
    "ERROR ",
    "CRITICAL ",
)

#: Tag expressions the full grammar accepts, which must reach the service
#: character for character - AAP 0.4.1 promises the whole grammar, not a
#: single-tag subset.
VALID_TAG_EXPRESSIONS: Final[tuple[str, ...]] = (
    "@Smoke",
    "@Login and not @wip",
    "@UPGN-286 or @UPGN-287",
    "not @wip",
)

#: Expressions the grammar rejects.  Each must be refused while Click is still
#: parsing, so nothing is executed, written or removed.
MALFORMED_TAG_EXPRESSIONS: Final[tuple[str, ...]] = (
    "@a and and",
    "and",
    "not",
    "(",
    "((@a)",
    "@a )",
)


# --------------------------------------------------------------------------- #
# Building the outcomes the command reads
#
# RunOutcome and ReportOutcome are frozen dataclasses with no defaults, by
# design: the run service surfaces every signal independently and encodes no
# precedence, so a consumer has to state all of them.  The builders below
# supply the "nothing remarkable happened" value for each field and let a test
# name only the signal it is about.
# --------------------------------------------------------------------------- #


def make_run_outcome(
    result_set: dict[str, Any] | None,
    *,
    selected_count: int = 1,
    worker_count: int = 1,
    dead_shards: tuple[str, ...] = (),
    parse_errors: tuple[str, ...] = (),
    merge_produced_nothing: bool = False,
    rerun: bool = False,
    dry_run: bool = False,
    tag_expression: str | None = cli.DEFAULT_TAG_EXPRESSION,
    infrastructure_error: str | None = None,
) -> RunOutcome:
    """Build a :class:`~app.services.RunOutcome` for the command to read.

    :param result_set: The merged document, an empty document, or ``None`` -
        the three states ``_publish`` reads apart.
    :param selected_count: How many units the selection produced.
    :param worker_count: How many workers ran.
    :param dead_shards: Reasons for shards that produced no results.
    :param parse_errors: Problems the run tolerated.
    :param merge_produced_nothing: The empty-merge signal.
    :param rerun: Whether this stands for a ``--rerun`` invocation.
    :param dry_run: Whether steps were resolved without executing.
    :param tag_expression: The filter as the user expressed it.
    :param infrastructure_error: Why the run's own intermediate storage
        failed, or ``None``.  The run service carries it rather than logging
        it - one emitter per fact - so the record naming it beside an exit
        class is this command's, and asserted here.
    :returns: The outcome, with ``shard_results`` empty - the command reads
        ``dead_shards`` and never the per-shard detail.
    """
    return RunOutcome(
        result_set=result_set,
        selected_count=selected_count,
        worker_count=worker_count,
        shard_results=(),
        dead_shards=dead_shards,
        parse_errors=parse_errors,
        merge_produced_nothing=merge_produced_nothing,
        rerun=rerun,
        dry_run=dry_run,
        tag_expression=tag_expression,
        infrastructure_error=infrastructure_error,
    )


def empty_document() -> dict[str, Any]:
    """A result document with no features, as a zero-scenario run produces.

    Built by the production factory rather than by hand, so the schema this
    module feeds the real writers in section J is the schema they are given in
    a real run.

    :returns: A fresh empty document.
    """
    return new_result_set(dry_run=False, tag_expression=cli.DEFAULT_TAG_EXPRESSION)


def ok_report(*written: Path) -> ReportOutcome:
    """A fan-out outcome in which all four writers succeeded.

    :param written: The paths the writers returned, in writer order.
    :returns: An outcome whose :attr:`~app.services.ReportOutcome.ok` is true.
    """
    return ReportOutcome(
        results=tuple(
            WriterResult(name=name, path=path)
            for name, path in zip(WRITER_NAMES, written, strict=False)
        ),
        written=tuple(written),
    )


def failed_report(
    *,
    written: Sequence[Path],
    failed_index: int,
    error: BaseException,
    failed_path: Path | None = None,
) -> ReportOutcome:
    """A fan-out outcome in which one writer failed after earlier successes.

    :param written: The artifacts the earlier writers produced, still on disk.
    :param failed_index: Position in :data:`WRITER_NAMES` of the failing
        writer, so the names come from the service's own sequence.
    :param error: The exception that writer raised.
    :param failed_path: The destination that writer was producing, as the real
        fan-out resolves it from the artifact key ``app/utils/paths.py``
        publishes and carries on
        :attr:`~app.services.ReportOutcome.failed_path`.  ``None`` stands for
        a destination the fan-out could not resolve, which the command line
        has to report readably rather than as a bare ``None``.
    :returns: The outcome ``app/cli.py`` turns into
        :attr:`~app.cli.ExitCode.ARTIFACT_FAILURE`, naming the writer *and*
        this destination.
    """
    results = [
        WriterResult(name=name, path=path)
        for name, path in zip(WRITER_NAMES[:failed_index], written, strict=True)
    ]
    results.append(WriterResult(name=WRITER_NAMES[failed_index], error=error))
    return ReportOutcome(
        results=tuple(results),
        written=tuple(written),
        failed_writer=WRITER_NAMES[failed_index],
        error=error,
        skipped=WRITER_NAMES[failed_index + 1 :],
        failed_path=failed_path,
    )


# --------------------------------------------------------------------------- #
# The service-layer stand-ins
#
# app/cli.py binds run_suite and generate_reports at import from the
# app.services barrel, so these replace the names on app.cli - which is also
# the boundary the tests are about: what the command asks of the service layer
# and what it does with the answer.
# --------------------------------------------------------------------------- #


class RunSuiteStub:
    """Records the command's request and returns a prepared outcome.

    :param outcome: The outcome to return, or a callable taking the recorded
        keywords and returning one - used where a test needs the answer to
        depend on the request, as the rerun tests do.
    :param raises: An exception to raise instead of returning, for the
        ``finally``-cleanup tests.
    :param observe: A zero-argument callable invoked at call time, whose
        result is appended to :attr:`observations`.  It is how a test observes
        the state of the build output *during* the run and therefore asserts
        that the clean step ran before it.
    :param trace: A shared list the call appends its label to, for ordering.
    """

    def __init__(
        self,
        outcome: RunOutcome | Callable[..., RunOutcome] | None = None,
        *,
        raises: BaseException | None = None,
        observe: Callable[[], Any] | None = None,
        trace: list[str] | None = None,
    ) -> None:
        self._outcome = outcome
        self._raises = raises
        self._observe = observe
        self._trace = trace
        self.calls: list[dict[str, Any]] = []
        self.observations: list[Any] = []

    def __call__(self, **kwargs: Any) -> RunOutcome:
        """Stand in for :func:`app.services.run_suite`."""
        self.calls.append(kwargs)
        if self._trace is not None:
            self._trace.append(RUN_SUITE_LABEL)
        if self._observe is not None:
            self.observations.append(self._observe())
        if self._raises is not None:
            raise self._raises
        if callable(self._outcome):
            return self._outcome(**kwargs)
        if self._outcome is None:
            return make_run_outcome(empty_document(), selected_count=0, worker_count=0)
        return self._outcome

    @property
    def call(self) -> dict[str, Any]:
        """The single call's keywords, asserting there was exactly one."""
        assert len(self.calls) == 1, f"expected one run, got {len(self.calls)}"
        return self.calls[0]


class GenerateReportsStub:
    """Records the fan-out request and returns a prepared report outcome.

    :param outcome: The outcome to return, or a callable taking the document.
    :param trace: A shared list the call appends its label to, for ordering.
    """

    def __init__(
        self,
        outcome: ReportOutcome | Callable[[Any], ReportOutcome] | None = None,
        *,
        trace: list[str] | None = None,
    ) -> None:
        self._outcome = outcome
        self._trace = trace
        self.documents: list[Any] = []
        self.keywords: list[dict[str, Any]] = []

    def __call__(self, result_set: Any, **kwargs: Any) -> ReportOutcome:
        """Stand in for :func:`app.services.generate_reports`."""
        self.documents.append(result_set)
        self.keywords.append(kwargs)
        if self._trace is not None:
            self._trace.append(GENERATE_REPORTS_LABEL)
        if callable(self._outcome):
            return self._outcome(result_set)
        if self._outcome is None:
            return ok_report()
        return self._outcome

    @property
    def called(self) -> bool:
        """Whether any writer fan-out was requested."""
        return bool(self.documents)


def _raising_writer(error: BaseException) -> Callable[..., Path]:
    """A writer entry point that raises instead of writing.

    Used where the **real** fan-out has to fail, which is the only way to
    assert how the two layers divide one incident's records: a stubbed fan-out
    logs nothing, so it cannot show that the cause is reported exactly once and
    by the service that caught it.

    :param error: The exception the writer raises.
    :returns: A callable with the writers' own signature - the document
        positionally, ``base`` by keyword - which never returns.
    """

    def write(result_set: Any, **kwargs: Any) -> Path:
        del result_set, kwargs
        raise error

    return write


def install_services(
    monkeypatch: pytest.MonkeyPatch,
    *,
    run_suite: RunSuiteStub,
    generate_reports: GenerateReportsStub | None = None,
) -> None:
    """Replace the two service names ``app/cli.py`` binds at import.

    ``cleanup_workers_dir`` is deliberately **never** replaced: it is cheap,
    idempotent by contract, and its real behaviour is exactly what section F
    is about.

    :param monkeypatch: pytest's patcher, for guaranteed restoration.
    :param run_suite: The run stand-in.
    :param generate_reports: The fan-out stand-in, or ``None`` to leave the
        real one in place.
    """
    monkeypatch.setattr(cli, "run_suite", run_suite)
    if generate_reports is not None:
        monkeypatch.setattr(cli, "generate_reports", generate_reports)


# --------------------------------------------------------------------------- #
# Artifact helpers
# --------------------------------------------------------------------------- #


def artifact_paths(root: Path) -> tuple[Path, ...]:
    """The four artifact destinations under ``root``, in writer order.

    The fourth is the PrettyReports overview page rather than its directory,
    so "the artifact exists" means a rendered page and not an empty tree.

    :param root: The checkout root the paths resolve against.
    :returns: The four paths, in :data:`WRITER_NAMES` order.
    """
    return (
        paths.cucumber_json_path(root),
        paths.rerun_txt_path(root),
        paths.cucumber_reports_html_path(root),
        paths.pretty_reports_index_path(root),
    )


def assert_four_artifacts(root: Path) -> None:
    """Assert all four artifacts exist under ``root``.

    :param root: The checkout root.
    """
    missing = [str(path) for path in artifact_paths(root) if not path.is_file()]
    assert not missing, f"artifacts missing: {missing}"


def assert_no_artifacts(root: Path) -> None:
    """Assert not one of the four artifacts exists under ``root``.

    :param root: The checkout root.
    """
    present = [str(path) for path in artifact_paths(root) if path.exists()]
    assert not present, f"artifacts written when none was expected: {present}"


def write_decoy_artifacts(root: Path) -> dict[Path, bytes]:
    """Put a recognisable stale byte sequence at each artifact destination.

    Used two ways: as the "existing artifacts" a rerun must leave untouched,
    and as the stale build output ``--clean`` must remove.  A sub-directory is
    included because ``--clean`` has to remove a directory tree as well as a
    file, and the per-worker directory is itself a directory.

    :param root: The checkout root.
    :returns: Each decoy path mapped to the bytes written there.
    """
    paths.ensure_dir(paths.target_root(root))
    decoys: dict[Path, bytes] = {}
    for index, path in enumerate(artifact_paths(root)):
        paths.ensure_parent(path)
        payload = f"decoy-{index}-{path.name}\n".encode()
        path.write_bytes(payload)
        decoys[path] = payload
    stale = paths.target_root(root) / "stale-subdirectory" / "stale.txt"
    paths.ensure_parent(stale)
    stale.write_bytes(b"stale\n")
    decoys[stale] = b"stale\n"
    return decoys


def assert_decoys_unchanged(decoys: dict[Path, bytes]) -> None:
    """Assert every decoy is still on disk with exactly its original bytes.

    :param decoys: The mapping :func:`write_decoy_artifacts` returned.
    """
    for path, payload in decoys.items():
        assert path.is_file(), f"{path} was removed"
        assert path.read_bytes() == payload, f"{path} was rewritten"


def seed_worker_directory(root: Path) -> Path:
    """Create ``target/.workers`` with an intermediate result file in it.

    Stands for what a run leaves behind mid-flight.  The name comes from
    ``app.utils.paths.worker_result_path`` so the file is shaped exactly like a
    real worker's output.

    :param root: The checkout root.
    :returns: The per-worker directory, now existing and non-empty.
    """
    directory = paths.ensure_dir(paths.workers_dir(root))
    worker_file = paths.worker_result_path(1, base=root)
    worker_file.write_text(json.dumps(empty_document()), encoding="utf-8")
    assert worker_file.is_file()
    return directory


def document_with_step_status(
    document: dict[str, Any],
    status: str,
    *,
    error_message: str | None = None,
) -> dict[str, Any]:
    """A deep copy of ``document`` with every step result set to ``status``.

    The command takes no decision from a scenario's outcome, so what varies
    between the execution-outcome cases of exit row 1 is the document, not the
    signals: this is how a "failing scenarios" run differs from an "undefined
    steps" run as far as ``app/cli.py`` can see.

    :param document: The merged document to base the copy on.
    :param status: The step status to write into every step result.
    :param error_message: Failure text to attach, for the cases that carry
        one - a browser that failed to start, for instance.
    :returns: The modified copy; ``document`` itself is left untouched.
    """
    copy = deepcopy(document)
    for feature in copy["features"]:
        for element in feature["elements"]:
            for step in element["steps"]:
                result = step.setdefault("result", {})
                result["status"] = status
                if error_message is not None:
                    result["error_message"] = error_message
    return copy


# --------------------------------------------------------------------------- #
# Stream helpers
# --------------------------------------------------------------------------- #


def message_lines(stream_text: str) -> list[str]:
    """The non-empty lines of a captured stream.

    :param stream_text: ``result.stdout`` or ``result.stderr``.
    :returns: The lines, blank ones dropped.
    """
    return [line for line in stream_text.splitlines() if line.strip()]


def invoke(
    runner: CliRunner,
    args: Sequence[str] = (),
    **kwargs: Any,
) -> Result:
    """Invoke the command with the two streams captured separately.

    Click 8.5's ``CliRunner`` has no ``mix_stderr`` parameter - ``stdout`` and
    ``stderr`` are separate on the result already - so the stream-routing
    assertions read them directly.

    :param runner: The Click runner.
    :param args: The command line, without the program name.
    :param kwargs: Extra keywords forwarded to ``CliRunner.invoke``.
    :returns: Click's result object.
    """
    return runner.invoke(cli.run_tests, list(args), **kwargs)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def restore_app_logging() -> Iterator[None]:
    """Return the ``app`` logger to its pre-test configuration.

    The command calls ``configure_logging()`` on every invocation, which
    installs two named handlers on the process-global ``app`` logger and turns
    propagation off.  ``tests/conftest.py`` does not reset that, so without
    this fixture this module would hand the rest of the suite a reconfigured
    logger and the suite's result would depend on module order.

    :yields: ``None`` - the fixture is entirely about the surrounding state.
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


@pytest.fixture
def runner() -> CliRunner:
    """A Click runner with the two streams captured separately.

    :returns: The runner every invocation in this module goes through.
    """
    return CliRunner()


@pytest.fixture
def cli_root(tmp_artifact_root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A temporary checkout root, made the working directory for the test.

    ``app/cli.py`` has no ``base`` parameter anywhere - the clean step, the
    per-worker cleanup and both services resolve paths from the working
    directory - so redirecting the working directory is the only way to invoke
    the command without touching the repository's own build output.
    ``monkeypatch.chdir`` restores the previous directory at teardown, and this
    module's docstring records why the suite's general "no test should chdir"
    note does not reach here.

    :param tmp_artifact_root: The conftest-owned temporary checkout root.
    :param monkeypatch: pytest's patcher, for the restored ``chdir``.
    :returns: The directory the command will resolve its paths against.
    """
    monkeypatch.chdir(tmp_artifact_root)
    return tmp_artifact_root


# --------------------------------------------------------------------------- #
# Section A - the option surface: six options, and no seventh
#
# app/cli.py requirements 1 and 2; the CLI table of AAP 0.4.1.
# --------------------------------------------------------------------------- #


def test_no_options_forwards_every_documented_default(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bare invocation resolves each default from the CLI table.

    ``--tags`` is ``@Smoke`` (``CukesRunner.java:18``), ``--workers`` is the
    CPU count (``pom.xml:22-23``), ``--dry-run`` and ``--rerun`` are off
    (``CukesRunner.java:17``, ``FailedTestRunner.java:11``) and ``--browser``
    is **unset**: the service must receive ``None`` and never a substituted
    ``"chrome"``, because an unset value means "let the ``browser`` property
    decide" and ``app/config.py`` is what resolves it.  The worker default is
    compared against ``default_worker_count()`` rather than a literal, since
    the CPU count differs per host.
    """
    run_suite = RunSuiteStub()
    install_services(
        monkeypatch, run_suite=run_suite, generate_reports=GenerateReportsStub()
    )

    result = invoke(runner)

    assert result.exit_code == int(cli.ExitCode.SUCCESS)
    call = run_suite.call
    assert set(call) == EXPECTED_SERVICE_KEYWORDS
    assert call["tags"] == cli.DEFAULT_TAG_EXPRESSION == "@Smoke"
    assert call["browser"] is None
    assert call["workers"] == default_worker_count()
    assert call["dry_run"] is False
    assert call["rerun"] is False


@pytest.mark.parametrize("expression", VALID_TAG_EXPRESSIONS)
def test_tags_option_reaches_the_service_verbatim(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    expression: str,
) -> None:
    """The full tag grammar reaches the service character for character.

    AAP 0.4.1 promises the whole expression grammar, not a single-tag subset,
    and ``app/cli.py``'s option callback parses only to validate and then
    discards the parsed form - so a compound expression must arrive exactly as
    written, with selection left to the service.
    """
    run_suite = RunSuiteStub()
    install_services(
        monkeypatch, run_suite=run_suite, generate_reports=GenerateReportsStub()
    )

    result = invoke(runner, ["--tags", expression])

    assert result.exit_code == int(cli.ExitCode.SUCCESS)
    assert run_suite.call["tags"] == expression


@pytest.mark.parametrize("browser", ["chrome", "firefox"])
def test_browser_option_reaches_the_service_verbatim(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    browser: str,
) -> None:
    """``--browser`` is forwarded untouched, for both supported browsers.

    ``Driver.java:27-42`` switches on the ``browser`` property with two cases,
    and the command adds no normalisation of its own: the value is passed to
    the worker as engine userdata and read where the driver is built.
    """
    run_suite = RunSuiteStub()
    install_services(
        monkeypatch, run_suite=run_suite, generate_reports=GenerateReportsStub()
    )

    result = invoke(runner, ["--browser", browser])

    assert result.exit_code == int(cli.ExitCode.SUCCESS)
    assert run_suite.call["browser"] == browser


@pytest.mark.parametrize("workers", [1, 2, 7])
def test_workers_option_reaches_the_service(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    workers: int,
) -> None:
    """``--workers N`` is forwarded as a number, ``1`` included.

    ``--workers 1`` is the sequential mode AAP conflict 4 keeps reachable, and
    it is forwarded as ``1`` rather than translated into a "sequential" flag:
    the service resolves the sharding.
    """
    run_suite = RunSuiteStub()
    install_services(
        monkeypatch, run_suite=run_suite, generate_reports=GenerateReportsStub()
    )

    result = invoke(runner, ["--workers", str(workers)])

    assert result.exit_code == int(cli.ExitCode.SUCCESS)
    assert run_suite.call["workers"] == workers


def test_dry_run_flag_reaches_the_service(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--dry-run`` turns on the flag ``CukesRunner.java:17`` fixed at false."""
    run_suite = RunSuiteStub()
    install_services(
        monkeypatch, run_suite=run_suite, generate_reports=GenerateReportsStub()
    )

    result = invoke(runner, ["--dry-run"])

    assert result.exit_code == int(cli.ExitCode.SUCCESS)
    assert run_suite.call["dry_run"] is True
    assert run_suite.call["rerun"] is False


def test_rerun_flag_reaches_the_service(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--rerun`` reaches the service as the rerun request itself.

    The other three behaviours the flag couples to are asserted in section D;
    this is the plain forwarding, which is what makes the service read the
    manifest named at ``FailedTestRunner.java:11`` instead of the feature
    files.
    """
    run_suite = RunSuiteStub(
        lambda **kwargs: make_run_outcome(
            None, rerun=True, tag_expression=None, selected_count=2
        )
    )
    generate_reports = GenerateReportsStub()
    install_services(
        monkeypatch, run_suite=run_suite, generate_reports=generate_reports
    )

    result = invoke(runner, ["--rerun"])

    assert result.exit_code == int(cli.ExitCode.SUCCESS)
    assert run_suite.call["rerun"] is True
    assert run_suite.call["dry_run"] is False


@pytest.mark.parametrize("clean_flag", ["--clean", "--no-clean"])
def test_clean_flag_is_not_forwarded_to_the_service(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_flag: str,
) -> None:
    """The clean step is the command's own work, not the service's.

    Whichever way it is spelled, the request the service receives is the same
    five keywords: ``app/cli.py`` owns ``--clean`` (it stands in for ``mvn
    clean test``) and the service is never told about it.
    """
    run_suite = RunSuiteStub()
    install_services(
        monkeypatch, run_suite=run_suite, generate_reports=GenerateReportsStub()
    )

    result = invoke(runner, [clean_flag])

    assert result.exit_code == int(cli.ExitCode.SUCCESS)
    assert set(run_suite.call) == EXPECTED_SERVICE_KEYWORDS


@pytest.mark.parametrize("value", ["0", "-1", "-4", "abc", "1.5", ""])
def test_non_positive_or_non_numeric_worker_count_is_a_usage_error(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    """``--workers`` accepts integers of one and above, and nothing else.

    ``click.IntRange(min=1)`` rejects the rest while parsing, which is the
    usage-error row of the 0.4.1 exit table: status ``2``, nothing executed
    and nothing written.
    """
    run_suite = RunSuiteStub()
    generate_reports = GenerateReportsStub()
    install_services(
        monkeypatch, run_suite=run_suite, generate_reports=generate_reports
    )

    result = invoke(runner, ["--workers", value])

    assert result.exit_code == int(cli.ExitCode.USAGE_ERROR)
    assert not run_suite.calls
    assert not generate_reports.called
    assert_no_artifacts(cli_root)
    assert "--workers" in result.stderr


@pytest.mark.parametrize("expression", MALFORMED_TAG_EXPRESSIONS)
def test_malformed_tag_expression_is_rejected_while_parsing(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    expression: str,
) -> None:
    """A malformed ``--tags`` value is an invalid option, not a run failure.

    ``app/cli.py`` parses the expression in an option callback purely so this
    is rejected before logging is configured, before the clean step and before
    the service is reached - the run service documents ``TagExpressionError``
    as its one propagating exception, and a traceback out of the middle of a
    run is what the callback exists to prevent.
    """
    run_suite = RunSuiteStub()
    generate_reports = GenerateReportsStub()
    install_services(
        monkeypatch, run_suite=run_suite, generate_reports=generate_reports
    )

    result = invoke(runner, ["--tags", expression])

    assert result.exit_code == int(cli.ExitCode.USAGE_ERROR)
    assert not run_suite.calls
    assert not generate_reports.called
    assert_no_artifacts(cli_root)
    assert "--tags" in result.stderr
    assert START_MESSAGE_MARKER not in result.stdout


def test_unknown_option_is_a_usage_error(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An option the command does not declare is refused, not ignored.

    The six options are the whole surface, so an unrecognised one is a usage
    error rather than something forwarded to the engine - there is no
    pass-through and no ``--`` escape.
    """
    run_suite = RunSuiteStub()
    install_services(
        monkeypatch, run_suite=run_suite, generate_reports=GenerateReportsStub()
    )

    result = invoke(runner, ["--threads", "4"])

    assert result.exit_code == int(cli.ExitCode.USAGE_ERROR)
    assert not run_suite.calls
    assert_no_artifacts(cli_root)
    assert "--threads" in result.stderr


def help_flags(help_text: str) -> tuple[str, ...]:
    """The flag entries listed in a ``--help`` output's Options section.

    :param help_text: The whole help text.
    :returns: One entry per listed option, in the order Click printed them.
    """
    _, _, options_section = help_text.partition("Options:")
    assert options_section, "the help output has no Options section"
    return tuple(
        match.group(1)
        for match in re.finditer(r"^  (--\S+(?: / --\S+)?)", options_section, re.M)
    )


def test_help_lists_exactly_the_six_options_and_no_seventh(
    runner: CliRunner,
    cli_root: Path,
) -> None:
    """``--help`` advertises the six options plus Click's own ``--help``.

    The deliberate absences are part of the contract: no ``--features``
    override (the discovery root is ``behave.ini``'s), no ``--format`` or
    ``--out`` (a per-worker path cannot come from a shared option), and no
    log-level option (``app/logging_config.py`` publishes one console
    contract).
    """
    result = invoke(runner, ["--help"])

    assert result.exit_code == int(cli.ExitCode.SUCCESS)
    assert help_flags(result.stdout) == EXPECTED_HELP_FLAGS
    assert tuple(parameter.name for parameter in cli.run_tests.params) == (
        EXPECTED_PARAMETER_NAMES
    )


def test_help_shows_the_documented_default_for_each_valued_option(
    runner: CliRunner,
    cli_root: Path,
) -> None:
    """Each option carrying a value shows the default the CLI table names.

    ``--dry-run`` and ``--rerun`` are pinned rather than asserted: Click
    prints no default for a flag that is off, so their "off by default" is
    stated in their help text and asserted through the service in section A
    instead.
    """
    result = invoke(runner, ["--help"])
    text = " ".join(result.stdout.split())

    assert f"[default: {cli.DEFAULT_TAG_EXPRESSION}]" in text
    assert (
        f"[default: (the browser property in {properties.PROPERTIES_FILENAME})]"
        in text
    )
    assert "[default: (the CPU count); x>=1]" in text
    assert "[default: clean]" in text
    assert "Off by default" in text


def test_command_name_and_declared_console_script_agree(
    cli_root: Path,
) -> None:
    """The command is named ``run-tests`` and ``pyproject.toml`` says so.

    AAP 0.4.1 fixes the console script as the supported entry point, so the
    declared name and target have to match the object defined here: a
    mismatch would ship an entry point that cannot be reached.
    """
    assert cli.COMMAND_NAME == "run-tests"
    assert cli.run_tests.name == cli.COMMAND_NAME

    declared = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
    scripts = declared["project"]["scripts"]
    assert scripts[cli.COMMAND_NAME] == "app.cli:run_tests"
    assert len(scripts) == 1, f"a second console script appeared: {sorted(scripts)}"


def test_installed_console_script_prints_the_six_options(
    cli_root: Path,
) -> None:
    """The installed ``run-tests`` script runs and lists the six options.

    Requirement 15 of ``app/cli.py``'s coverage list: after the editable
    install the environment's ``bin``/``Scripts`` directory carries the script
    and ``--help`` succeeds through it.  Run from the temporary root, so a
    failure of that claim cannot be masked by the repository being the working
    directory - and ``--help`` executes no run.
    """
    script = Path(sys.executable).with_name(cli.COMMAND_NAME)
    assert script.exists(), f"the console script is not installed at {script}"

    completed = subprocess.run(
        [str(script), "--help"],
        cwd=cli_root,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )

    assert help_flags(completed.stdout) == EXPECTED_HELP_FLAGS
    assert list(cli_root.iterdir()) == [], "--help touched the working directory"


def test_module_defines_exactly_one_command_and_no_generate_reports() -> None:
    """There is one command in the module, and it is not a group.

    AAP 0.4.1 states plainly that ``generate-reports`` is not provided: the
    source's only documented route to a report was to re-run the suite with a
    chosen plugin, so rebuilding reports from stored results would be an
    unrequested addition with a durable-state contract of its own.
    """
    commands = {
        name: value
        for name, value in vars(cli).items()
        if isinstance(value, Command)
    }

    assert list(commands) == ["run_tests"]
    assert not isinstance(cli.run_tests, Group)
    assert cli.run_tests.name == cli.COMMAND_NAME


# --------------------------------------------------------------------------- #
# Section B - no environment layer
#
# AAP 0.4.1: behave userdata is "the only override path; no environment layer
# is added".
# --------------------------------------------------------------------------- #


def test_no_option_declares_an_environment_variable() -> None:
    """Not one of the six options reads an environment variable.

    Click would happily give an option an ``envvar``; none has one, because
    the configuration surface is fixed at six properties keys and userdata is
    the only override path.
    """
    for parameter in cli.run_tests.params:
        assert isinstance(parameter, Parameter)
        assert parameter.envvar is None, f"{parameter.name} reads an environment"


@pytest.mark.parametrize("variable", ["BROWSER", "browser", "RUN_TESTS_BROWSER"])
def test_browser_environment_variables_do_not_reach_the_service(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    variable: str,
) -> None:
    """An environment variable named after the option changes nothing.

    The service still receives ``browser=None``, so the ``browser`` property
    decides - which is what keeps the six-key configuration surface the only
    place a browser is configured.
    """
    monkeypatch.setenv(variable, "firefox")
    run_suite = RunSuiteStub()
    install_services(
        monkeypatch, run_suite=run_suite, generate_reports=GenerateReportsStub()
    )

    result = invoke(runner)

    assert result.exit_code == int(cli.ExitCode.SUCCESS)
    assert run_suite.call["browser"] is None


# --------------------------------------------------------------------------- #
# Section C - an unrecognised browser value is accepted
# --------------------------------------------------------------------------- #


def test_unknown_browser_value_is_accepted_and_exits_zero(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--browser safari`` is accepted, forwarded and exits ``0``.

    Counter-intuitive and deliberate.  ``Driver.java:29-42`` switches on the
    browser name with cases for chrome and firefox and **no default branch**,
    so an unrecognised value in the Java project reached the driver and failed
    at first use.  Validating it here would change behaviour, and the 0.4.1
    exit table lists "an unknown ``browser`` value" among the situations that
    exit ``0``.
    """
    run_suite = RunSuiteStub()
    install_services(
        monkeypatch, run_suite=run_suite, generate_reports=GenerateReportsStub()
    )

    result = invoke(runner, ["--browser", "safari"])

    assert result.exit_code == int(cli.ExitCode.SUCCESS)
    assert run_suite.call["browser"] == "safari"
    assert "safari" in result.stdout


# --------------------------------------------------------------------------- #
# Section D - --rerun is four coupled behaviours
#
# Each traces to an absence in FailedTestRunner.java:9-12.
# --------------------------------------------------------------------------- #


def test_rerun_clears_the_tag_filter(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Under ``--rerun`` the service is told ``tags=None``, not ``@Smoke``.

    ``FailedTestRunner`` declared no ``tags`` attribute, and leaving ``@Smoke``
    applied would silently skip every failure from the five features that
    carry no feature-level tag - precisely what a rerun exists to re-execute.
    ``None`` is the run service's documented spelling of "no filter"; the
    neutral tautology that suppresses ``behave.ini``'s ``default_tags`` is a
    worker-command-level mechanism asserted in
    ``tests/test_test_run_service.py``, and must never surface here.
    """
    run_suite = RunSuiteStub(
        lambda **kwargs: make_run_outcome(None, rerun=True, tag_expression=None)
    )
    install_services(
        monkeypatch, run_suite=run_suite, generate_reports=GenerateReportsStub()
    )

    result = invoke(runner, ["--rerun"])

    assert result.exit_code == int(cli.ExitCode.SUCCESS)
    assert run_suite.call["tags"] is None
    assert run_suite.call["rerun"] is True


def test_rerun_with_tags_is_a_usage_error(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--rerun --tags`` is rejected: status ``2``, nothing executed.

    The conflict is detected by parameter *source* rather than by comparing
    the value against the default, so ``--rerun --tags @Smoke`` - the same
    conflicting request spelled out in full - is rejected too, which the
    second invocation below covers.
    """
    decoys = write_decoy_artifacts(cli_root)
    run_suite = RunSuiteStub()
    generate_reports = GenerateReportsStub()
    install_services(
        monkeypatch, run_suite=run_suite, generate_reports=generate_reports
    )

    for arguments in (
        ["--rerun", "--tags", "@Login"],
        ["--rerun", "--tags", cli.DEFAULT_TAG_EXPRESSION],
        ["--tags", "@Login", "--rerun"],
    ):
        result = invoke(runner, arguments)

        assert result.exit_code == int(cli.ExitCode.USAGE_ERROR), arguments
        assert "--rerun" in result.stderr
        assert "--tags" in result.stderr
        assert START_MESSAGE_MARKER not in result.stdout

    assert not run_suite.calls
    assert not generate_reports.called
    assert_decoys_unchanged(decoys)


def test_rerun_invokes_no_writer_and_leaves_artifacts_byte_identical(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rerun writes no artifact and modifies none.

    ``FailedTestRunner.java:9-12`` declared **no** plugin list, so the report
    service is not merely given nothing to do - it is not invoked at all.  The
    existing artifacts are compared byte for byte before and after, which is
    the only way to tell "not rewritten" from "rewritten identically".
    """
    decoys = write_decoy_artifacts(cli_root)
    run_suite = RunSuiteStub(
        lambda **kwargs: make_run_outcome(
            None, rerun=True, tag_expression=None, selected_count=3, worker_count=2
        )
    )
    generate_reports = GenerateReportsStub()
    install_services(
        monkeypatch, run_suite=run_suite, generate_reports=generate_reports
    )

    result = invoke(runner, ["--rerun"])

    assert result.exit_code == int(cli.ExitCode.SUCCESS)
    assert not generate_reports.called
    assert_decoys_unchanged(decoys)
    assert "no artifact was written" in result.stdout


def test_rerun_never_cleans_even_with_clean_passed_explicitly(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--rerun --clean`` cleans nothing, and says so.

    Deleting the build output would delete the manifest the rerun is about to
    read, which would make the command self-defeating.  The manifest is still
    there when the run starts - the stub observes the directory from inside the
    run - and the command states on stdout that ``--clean`` was ignored,
    because the user asked for something that is deliberately not being done.
    """
    decoys = write_decoy_artifacts(cli_root)
    manifest = paths.rerun_txt_path(cli_root)
    run_suite = RunSuiteStub(
        lambda **kwargs: make_run_outcome(None, rerun=True, tag_expression=None),
        observe=lambda: sorted(
            entry.name for entry in paths.target_root(cli_root).iterdir()
        ),
    )
    install_services(
        monkeypatch, run_suite=run_suite, generate_reports=GenerateReportsStub()
    )

    result = invoke(runner, ["--rerun", "--clean"])

    assert result.exit_code == int(cli.ExitCode.SUCCESS)
    assert manifest.name in run_suite.observations[0]
    assert manifest.is_file()
    assert_decoys_unchanged(decoys)
    assert "--clean is ignored under --rerun" in result.stdout


# --------------------------------------------------------------------------- #
# Section E - the exit table, one row per test
#
# Six separately named tests, so a failure names the row it broke, and each
# asserts both the status and the state of the artifacts.
# --------------------------------------------------------------------------- #

#: Row 1 of the 0.4.1 exit table, case by case.  Each entry is
#: ``(id, command line, step status, failure text, tolerated problems,
#: whether artifacts are expected)``.
#:
#: The command cannot tell a failing scenario from an errored one by any
#: *signal* - both arrive as a merged document and it never inspects one - so
#: what distinguishes those cases here is the document the engine would really
#: have handed over: the step status it carries, and the failure text with it.
_ROW_ONE_CASES: Final[
    tuple[tuple[str, list[str], str, str | None, tuple[str, ...], bool], ...]
] = (
    (
        "failing-scenarios",
        [],
        "failed",
        "AssertionError: expected the Employee page title",
        (),
        True,
    ),
    (
        "errored-step",
        [],
        "failed",
        "NoSuchElementException: Unable to locate element",
        (),
        True,
    ),
    ("undefined-steps", [], "undefined", None, (), True),
    ("skipped-steps", [], "skipped", None, (), True),
    (
        "browser-failed-to-start",
        [],
        "failed",
        "WebDriverException: cannot start the browser process",
        (),
        True,
    ),
    (
        "unknown-browser-value",
        ["--browser", "safari"],
        "failed",
        "AttributeError: no driver was created for browser 'safari'",
        (),
        True,
    ),
    (
        "feature-failed-to-parse",
        [],
        "passed",
        None,
        ("features/Broken.feature:3: Parser failure: expected Given",),
        True,
    ),
    (
        "missing-rerun-manifest",
        ["--rerun"],
        "passed",
        None,
        ("the rerun manifest is missing or unreadable",),
        False,
    ),
)


@pytest.mark.parametrize(
    (
        "case_id",
        "arguments",
        "step_status",
        "error_message",
        "parse_errors",
        "expect_artifacts",
    ),
    _ROW_ONE_CASES,
    ids=[case[0] for case in _ROW_ONE_CASES],
)
def test_exit_row_1_execution_outcomes_exit_zero(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_result_set: Any,
    case_id: str,
    arguments: list[str],
    step_status: str,
    error_message: str | None,
    parse_errors: tuple[str, ...],
    expect_artifacts: bool,
) -> None:
    """Row 1: a test outcome never reaches the exit status.

    ``pom.xml:25`` sets ``testFailureIgnore=true`` and all six Jenkins
    publisher thresholds are ``-1`` (``Jenkins:15``), so failing scenarios, an
    error, undefined steps, skipped steps, a browser that fails to start, an
    unrecognised ``browser`` value, a feature that fails to parse and a
    missing or malformed rerun manifest all exit ``0``.  The real writers run,
    so "all four artifacts, from whatever executed" is asserted on disk.

    One case carries its own artifact expectation and says why: the manifest
    case is a ``--rerun`` invocation, and a rerun writes no artifact at all
    (section D), so for that case alone the expectation is that none appears.
    The parse and manifest problems are reported on **stderr** and tolerated
    either way.
    """
    rerun = "--rerun" in arguments
    document = (
        None
        if rerun
        else document_with_step_status(
            sample_result_set, step_status, error_message=error_message
        )
    )

    run_suite = RunSuiteStub(
        make_run_outcome(
            document,
            selected_count=0 if rerun else 5,
            worker_count=0 if rerun else 2,
            parse_errors=parse_errors,
            rerun=rerun,
            tag_expression=None if rerun else cli.DEFAULT_TAG_EXPRESSION,
        )
    )
    install_services(monkeypatch, run_suite=run_suite)

    result = invoke(runner, arguments)

    assert result.exit_code == int(cli.ExitCode.SUCCESS), case_id
    if expect_artifacts:
        assert_four_artifacts(cli_root)
    else:
        assert_no_artifacts(cli_root)
    for problem in parse_errors:
        assert problem in result.stderr
        assert problem not in result.stdout
    assert not paths.workers_dir(cli_root).exists()


def test_exit_row_2_zero_scenarios_selected_writes_four_empty_artifacts(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Row 2: a tag expression that selected nothing exits ``0``, empty.

    The publisher's ``fileIncludePattern`` is narrowed to the single JSON
    report (AAP deviation 3), so that file must exist for the third pipeline
    stage to have any input at all - which is why an empty selection writes
    all four artifacts rather than skipping the fan-out.  With the real
    writers the JSON report holds an empty **list**, not an object and not an
    absent file.
    """
    run_suite = RunSuiteStub(
        make_run_outcome(empty_document(), selected_count=0, worker_count=0)
    )
    install_services(monkeypatch, run_suite=run_suite)

    result = invoke(runner)

    assert result.exit_code == int(cli.ExitCode.SUCCESS)
    assert_four_artifacts(cli_root)
    report = paths.cucumber_json_path(cli_root).read_text(encoding="utf-8")
    assert report.strip() == "[]"
    assert json.loads(report) == []


@pytest.mark.parametrize(
    ("case_id", "arguments"),
    [
        ("unknown-option", ["--parallel", "methods"]),
        ("rerun-with-tags", ["--rerun", "--tags", "@Login"]),
    ],
    ids=["unknown-option", "rerun-with-tags"],
)
def test_exit_row_3_usage_error_exits_two_and_writes_nothing(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    case_id: str,
    arguments: list[str],
) -> None:
    """Row 3: an unknown or conflicting option exits ``2``, writing nothing.

    ``2`` is Click's own convention for a :exc:`click.UsageError`, so the
    command's published class and the parser agree.  Nothing is executed,
    nothing is written and nothing is removed: the conflict is raised before
    the clean step and before the run service is reached, so the build output
    directory is not so much as created - which is the strict reading of the
    row's "None written; nothing executed".
    """
    run_suite = RunSuiteStub()
    generate_reports = GenerateReportsStub()
    install_services(
        monkeypatch, run_suite=run_suite, generate_reports=generate_reports
    )

    result = invoke(runner, arguments)

    assert result.exit_code == int(cli.ExitCode.USAGE_ERROR), case_id
    assert not run_suite.calls
    assert not generate_reports.called
    assert_no_artifacts(cli_root)
    assert not paths.target_root(cli_root).exists()
    assert START_MESSAGE_MARKER not in result.stdout
    assert "Error" in result.stderr


def test_exit_row_4_dead_worker_exits_three_with_all_four_artifacts(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_result_set: Any,
) -> None:
    """Row 4: a worker that produced no results exits ``3``, not ``0``.

    A dead shard is non-zero **and** non-suppressing: the four artifacts are
    still written from the shards that completed - asserted here with the real
    writers - and every incomplete shard is named on stderr so a CI log
    identifies what is missing without a re-run.
    """
    dead_shards = (
        "shard 1 of 3: worker exited with status 9",
        "shard 2 of 3: no result file was produced",
    )
    run_suite = RunSuiteStub(
        make_run_outcome(
            sample_result_set,
            selected_count=9,
            worker_count=3,
            dead_shards=dead_shards,
        )
    )
    install_services(monkeypatch, run_suite=run_suite)

    result = invoke(runner)

    assert result.exit_code == int(cli.ExitCode.WORKER_DIED) == 3
    assert_four_artifacts(cli_root)
    for reason in dead_shards:
        assert reason in result.stderr
        assert reason not in result.stdout
    assert not paths.workers_dir(cli_root).exists()


@pytest.mark.parametrize(
    ("with_document", "merge_produced_nothing"),
    [
        (False, True),
        (True, True),
        (False, False),
    ],
    ids=["both-signals", "merge-flag-only", "result-set-none-only"],
)
def test_exit_row_5_empty_merge_exits_four_and_writes_no_artifact(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_result_set: Any,
    with_document: bool,
    merge_produced_nothing: bool,
) -> None:
    """Row 5: scenarios ran and no document survived - exit ``4``, nothing written.

    ``app/cli.py`` reads the two signals apart rather than collapsing them
    into one truthiness test, because an *empty* document (row 2) and a
    *missing* one (this row) look alike and mean opposite things.  Either
    signal alone is sufficient grounds, so all three combinations are covered.
    The build output is left exactly as the clean step left it: present and
    empty, with the stale decoys gone and no artifact in their place.

    The status is :attr:`~app.cli.ExitCode.ARTIFACT_FAILURE`, which this row
    shares with a writer failure because AAP deviation 15 licenses one class
    for "a merge *or* writer failure".  What distinguishes the two rows is
    their diagnostics and what survives, and both halves are asserted here:
    the message names the *merge* and says nothing was written, and no writer
    was invoked at all.
    """
    write_decoy_artifacts(cli_root)
    document = sample_result_set if with_document else None
    run_suite = RunSuiteStub(
        make_run_outcome(
            document,
            selected_count=4,
            worker_count=2,
            merge_produced_nothing=merge_produced_nothing,
        )
    )
    generate_reports = GenerateReportsStub()
    install_services(
        monkeypatch, run_suite=run_suite, generate_reports=generate_reports
    )

    result = invoke(runner, ["--clean"])

    assert result.exit_code == int(cli.ExitCode.ARTIFACT_FAILURE) == 4
    assert not generate_reports.called
    assert_no_artifacts(cli_root)
    target = paths.target_root(cli_root)
    assert target.is_dir(), "the clean step removes the contents, never the directory"
    assert list(target.iterdir()) == []
    assert str(int(cli.ExitCode.ARTIFACT_FAILURE)) in result.stderr

    # This row's own account, which is what keeps the shared status from
    # costing information: the merge is named, nothing was written, and no
    # writer name appears - a writer failure's message names one.
    assert "merge" in result.stderr
    assert "no artifact was written" in result.stderr
    for name in WRITER_NAMES:
        assert name not in result.stderr, name


def test_exit_row_6_writer_failure_exits_four_and_retains_earlier_artifacts(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_result_set: Any,
) -> None:
    """Row 6: a writer failed after earlier writers succeeded - exit ``4``.

    Nothing is rolled back, and that is the row rather than an oversight: the
    artifacts written before the failure stay exactly where their writers put
    them, so the publisher still has the JSON report it is narrowed to.  The
    two earlier artifacts are placed on disk first - standing for what the
    first two writers produced - and compared byte for byte afterwards, and
    the failing writer, the writers never attempted and the retained paths are
    all named on stderr.

    The status is the same :attr:`~app.cli.ExitCode.ARTIFACT_FAILURE` as an
    empty merge, because AAP deviation 15 licenses one class for "a merge *or*
    writer failure"; the two rows are told apart by their diagnostics and by
    what survives, which is what the stderr assertions below pin.  The
    destination named in the record is the one the fan-out resolved and
    carried on :attr:`~app.services.ReportOutcome.failed_path` - the command
    line resolves no artifact path of its own - so the failing writer's
    intended artifact is asserted to be exactly that value.
    """
    paths.ensure_dir(paths.target_root(cli_root))
    json_path, rerun_path = artifact_paths(cli_root)[:2]
    payloads = {}
    for path in (json_path, rerun_path):
        paths.ensure_parent(path)
        payloads[path] = f"written-by-{path.name}\n".encode()
        path.write_bytes(payloads[path])

    error = OSError("no space left on device")
    failed_destination = artifact_paths(cli_root)[2]
    generate_reports = GenerateReportsStub(
        failed_report(
            written=[json_path, rerun_path],
            failed_index=2,
            error=error,
            failed_path=failed_destination,
        )
    )
    run_suite = RunSuiteStub(
        make_run_outcome(sample_result_set, selected_count=5, worker_count=2)
    )
    install_services(
        monkeypatch, run_suite=run_suite, generate_reports=generate_reports
    )

    result = invoke(runner, ["--no-clean"])

    assert result.exit_code == int(cli.ExitCode.ARTIFACT_FAILURE) == 4
    for path, payload in payloads.items():
        assert path.is_file(), f"{path} was deleted after a later writer failed"
        assert path.read_bytes() == payload
    assert WRITER_NAMES[2] in result.stderr
    assert WRITER_NAMES[3] in result.stderr
    assert str(json_path) in result.stderr
    assert str(rerun_path) in result.stderr
    assert not paths.workers_dir(cli_root).exists()

    # The *cause* is deliberately absent from this command's records and is
    # not missing from the run: the fan-out is stubbed here, and in a real run
    # ``app/services/report_service.py`` reports the exception with its
    # traceback at the point it caught it, because a traceback is the one fact
    # the outcome cannot carry onward.  This command reports the consequence.
    # test_a_writer_failures_cause_and_consequence_are_each_reported_once
    # drives the real fan-out and asserts both records, each exactly once.
    assert "no space left on device" not in result.stderr

    # This row's own account: the failing *writer* is named, together with the
    # destination it was producing and beside the exit class, and the artifacts
    # that survive are named as retained.  An empty merge's message names none
    # of those and reports that nothing was written at all.
    assert (
        f"Exit {int(cli.ExitCode.ARTIFACT_FAILURE)}: report writer "
        f"{WRITER_NAMES[2]} failed writing {failed_destination}"
    ) in result.stderr
    assert f"Not attempted after {WRITER_NAMES[2]} failed" in result.stderr
    assert "Retained, and not deleted" in result.stderr


def test_a_writer_failure_whose_destination_is_unresolved_still_reports_it(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_result_set: Any,
) -> None:
    """``failed_path`` is optional, and its absence degrades readably.

    :attr:`~app.services.ReportOutcome.failed_path` is ``None`` when the
    fan-out could not resolve the destination at all, so the command line must
    handle the absence rather than assume a path - and must not print a bare
    ``None`` beside the writer's name, which a reader could mistake for a
    destination.  It substitutes text that is deliberately not path-shaped,
    and the status and the writer's name are unaffected.
    """
    generate_reports = GenerateReportsStub(
        failed_report(
            written=[],
            failed_index=0,
            error=OSError("no space left on device"),
            failed_path=None,
        )
    )
    run_suite = RunSuiteStub(
        make_run_outcome(sample_result_set, selected_count=5, worker_count=2)
    )
    install_services(
        monkeypatch, run_suite=run_suite, generate_reports=generate_reports
    )

    result = invoke(runner, ["--no-clean"])

    assert result.exit_code == int(cli.ExitCode.ARTIFACT_FAILURE) == 4
    assert (
        f"Exit {int(cli.ExitCode.ARTIFACT_FAILURE)}: report writer "
        f"{WRITER_NAMES[0]} failed writing "
    ) in result.stderr
    assert "None" not in result.stderr
    assert "No artifact had been written when the failure occurred" in (
        result.stderr
    )


def test_a_run_whose_intermediate_directory_cannot_be_prepared_exits_four(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The intermediate-storage cause of exit ``4``, and its single emitter.

    A run whose workers have nowhere to write executes nothing, so there is no
    document and no artifact - and the reason cannot be a tolerated selection
    problem or a dead shard, because neither of those is non-zero.  The run
    service reports it on
    :attr:`~app.services.RunOutcome.infrastructure_error` and stays silent,
    which is the port's one-emitter rule: the command that reads the outcome
    is what names the fact, once, beside the exit class it produces.
    """
    reason = (
        f"{paths.workers_dir(cli_root)}: this run's per-worker directory "
        "cannot be created ([Errno 20] Not a directory); no scenario was "
        "executed"
    )
    generate_reports = GenerateReportsStub()
    install_services(
        monkeypatch,
        run_suite=RunSuiteStub(
            make_run_outcome(
                None,
                selected_count=6,
                worker_count=0,
                merge_produced_nothing=True,
                infrastructure_error=reason,
            )
        ),
        generate_reports=generate_reports,
    )

    result = invoke(runner, ["--no-clean"])

    assert result.exit_code == int(cli.ExitCode.ARTIFACT_FAILURE) == 4
    assert not generate_reports.called
    assert_no_artifacts(cli_root)

    naming = [line for line in message_lines(result.stderr) if reason in line]
    assert len(naming) == 1, message_lines(result.stderr)
    assert naming[0].startswith(f"ERROR {CLI_LOGGER_NAME}:")
    assert f"Exit {int(cli.ExitCode.ARTIFACT_FAILURE)}:" in naming[0]
    assert reason not in result.stdout


def test_a_rerun_that_could_not_prepare_its_directory_still_exits_four(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rerun writes no artifact by design - and that must not hide this.

    ``_publish`` reads the signal **before** the ``--rerun`` short-circuit,
    and this is the case that makes the order load-bearing: a rerun's ordinary
    outcome and a rerun that could not create a directory to work in are
    indistinguishable from the artifacts, so reading the signal afterwards
    would report a rerun that executed not one of the failures it was asked to
    re-run as a success.
    """
    reason = (
        f"{paths.workers_dir(cli_root)}: this run's per-worker directory "
        "cannot be created ([Errno 13] Permission denied); no scenario was "
        "executed"
    )
    generate_reports = GenerateReportsStub()
    install_services(
        monkeypatch,
        run_suite=RunSuiteStub(
            make_run_outcome(
                None,
                selected_count=3,
                worker_count=0,
                rerun=True,
                tag_expression=None,
                infrastructure_error=reason,
            )
        ),
        generate_reports=generate_reports,
    )

    result = invoke(runner, ["--rerun"])

    assert result.exit_code == int(cli.ExitCode.ARTIFACT_FAILURE) == 4
    assert not generate_reports.called
    assert_no_artifacts(cli_root)
    assert reason in result.stderr


def test_a_completed_run_whose_intermediates_survive_writes_then_exits_four(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_result_set: Any,
) -> None:
    """A removal failure costs the status, never the four artifacts.

    The other cause the same field carries: the run executed and produced a
    document, and what failed was the removal of its per-worker documents from
    a workspace whose publisher glob is narrowed (``Jenkins:15``) precisely
    because nothing intermediate may be read by it.  So the writers run first
    and the status is settled second - the artifacts are written and kept, and
    the run still does not report success.
    """
    reason = (
        "1 intermediate path(s) could not be removed, so per-worker result "
        f"documents remain in the workspace: could not remove "
        f"{paths.workers_dir(cli_root)}: [Errno 39] Directory not empty"
    )
    generate_reports = GenerateReportsStub(ok_report(*artifact_paths(cli_root)))
    install_services(
        monkeypatch,
        run_suite=RunSuiteStub(
            make_run_outcome(
                sample_result_set,
                selected_count=5,
                worker_count=2,
                infrastructure_error=reason,
            )
        ),
        generate_reports=generate_reports,
    )

    result = invoke(runner, ["--no-clean"])

    assert result.exit_code == int(cli.ExitCode.ARTIFACT_FAILURE) == 4
    assert generate_reports.called
    assert reason in result.stderr
    assert "all four artifacts were written" in result.stderr
    assert "Finished with status 0" not in result.stdout


def test_a_writer_failures_cause_and_consequence_are_each_reported_once(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_result_set: Any,
) -> None:
    """One incident, two records, one emitter each - requirement 16.

    The fan-out is **real** here, which is the only way to assert this: a stub
    that logs nothing makes any allocation of records look correct.  A writer
    is made to fail by replacing the service's own ``WRITER_SEQUENCE``, the
    seam ``tests/test_report_service.py`` uses, so the real
    :func:`~app.services.generate_reports` catches the exception and the real
    command reads the outcome.

    The division is the port's one-emitter rule: the **cause** with its
    traceback is reported where it was caught, by
    ``app.services.report_service``, and the **consequence** - the exit class,
    the writer, the destination it was producing, what was skipped and what
    survives - is reported by ``app.cli`` from the outcome's fields.  Neither
    repeats the other, so a reader counting ``ERROR`` records over a CI console
    counts incidents rather than layers.
    """
    from app.services import report_service

    failure = OSError("no space left on device")
    failing_index = 1
    sequence = tuple(
        spec._replace(write=_raising_writer(failure))
        if index == failing_index
        else spec
        for index, spec in enumerate(WRITER_SEQUENCE)
    )
    monkeypatch.setattr(report_service, "WRITER_SEQUENCE", sequence)
    install_services(
        monkeypatch,
        run_suite=RunSuiteStub(
            make_run_outcome(sample_result_set, selected_count=5, worker_count=2)
        ),
    )

    result = invoke(runner, ["--no-clean"])

    assert result.exit_code == int(cli.ExitCode.ARTIFACT_FAILURE) == 4

    # The cause: once, from the service that caught it, with its traceback.
    cause_records = [
        line
        for line in message_lines(result.stderr)
        if REPORT_SERVICE_LOGGER_NAME in line
    ]
    assert len(cause_records) == 1, message_lines(result.stderr)
    assert str(failure) in cause_records[0]
    assert "Traceback (most recent call last)" in result.stderr

    # The consequence: once, from this command, naming the exit class, the
    # writer and the destination the outcome carried.
    destination = WRITER_SEQUENCE[failing_index].destination(cli_root)
    assert destination is not None
    exit_records = [
        line
        for line in message_lines(result.stderr)
        if line.startswith(f"ERROR {CLI_LOGGER_NAME}:")
        and f"Exit {int(cli.ExitCode.ARTIFACT_FAILURE)}:" in line
    ]
    assert len(exit_records) == 1, message_lines(result.stderr)
    assert (
        f"report writer {WRITER_NAMES[failing_index]} failed writing "
        f"{destination}"
    ) in exit_records[0]

    # Neither layer repeats the other: the command does not restate the
    # exception text, and the service does not name the exit class.
    assert str(failure) not in exit_records[0]
    assert "Exit " not in cause_records[0]


def test_the_non_zero_statuses_are_distinct_and_never_one() -> None:
    """The published statuses are ``0, 2, 3, 4`` - and ``1`` is absent.

    ``1`` is what an uncaught exception and an interrupt produce, so reusing
    it for a defined class would make a status ambiguous; every other value is
    distinct so that a non-zero status always names its class.

    There are **three** non-zero classes and exactly three, because AAP
    deviation 15 licenses exactly three the source lacks: a usage error, a dead
    worker, and a merge *or* writer failure.  The last of those is one class,
    so it is one member and carries no alias beside it: a second name for
    ``4`` would preserve the appearance of a fourth class in a log line and in
    a reader's head, and a fourth *status* would publish a class nothing has
    agreed to.  The member set itself is asserted, not just the values, which
    is what makes an added or renamed class visible here.
    """
    statuses = [int(member) for member in cli.ExitCode]

    assert statuses == sorted(set(statuses))
    assert 1 not in statuses
    assert int(cli.ExitCode.SUCCESS) == 0
    assert int(cli.ExitCode.USAGE_ERROR) == 2
    assert int(cli.ExitCode.WORKER_DIED) == 3
    assert int(cli.ExitCode.ARTIFACT_FAILURE) == 4

    assert statuses == [0, 2, 3, 4]
    assert {member.name for member in cli.ExitCode} == {
        "SUCCESS",
        "USAGE_ERROR",
        "WORKER_DIED",
        "ARTIFACT_FAILURE",
    }
    # No alias: ``ExitCode.__members__`` includes aliases where iteration does
    # not, so the two agreeing is what rules a second name for ``4`` out.
    assert list(cli.ExitCode.__members__) == [
        member.name for member in cli.ExitCode
    ]
    assert len([member for member in cli.ExitCode if member != 0]) == 3


@pytest.mark.parametrize(
    "status", ["passed", "failed", "skipped", "pending", "undefined"]
)
def test_no_scenario_status_reaches_the_exit_status(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_result_set: Any,
    status: str,
) -> None:
    """Whatever the steps did, the command exits ``0``.

    The fan-out is stubbed here on purpose: the point is that ``app/cli.py``
    takes no decision from a step status at all - it never inspects the
    document - so the five statuses are driven through the command and the
    writers' own status mapping stays ``tests/test_cucumber_json.py``'s
    business.
    """
    document = document_with_step_status(sample_result_set, status)
    run_suite = RunSuiteStub(
        make_run_outcome(document, selected_count=5, worker_count=2)
    )
    generate_reports = GenerateReportsStub(ok_report(*artifact_paths(cli_root)))
    install_services(
        monkeypatch, run_suite=run_suite, generate_reports=generate_reports
    )

    result = invoke(runner)

    assert result.exit_code == int(cli.ExitCode.SUCCESS)
    assert generate_reports.documents[0] is document


# --------------------------------------------------------------------------- #
# Section F - the per-worker directory is removed unconditionally
#
# app/cli.py requirement 10.  The removal and the pipeline's narrowed
# publisher glob are two halves of one guarantee: no intermediate result
# document is ever visible to the Cucumber publisher.
#
# Note what is deliberately NOT asserted here: that a *parse-time* usage
# error leaves the directory alone.  Click rejects such a value before the
# command callback runs at all, and the lifecycle boundary around parsing is
# being changed elsewhere; every case below is reachable from inside the
# callback, so each holds before and after that change.
# --------------------------------------------------------------------------- #


def _cleanup_case_success(
    monkeypatch: pytest.MonkeyPatch, root: Path, document: Any
) -> int:
    """Install a successful run and return the status it must produce."""
    install_services(
        monkeypatch,
        run_suite=RunSuiteStub(make_run_outcome(document, selected_count=2)),
        generate_reports=GenerateReportsStub(ok_report(*artifact_paths(root))),
    )
    return int(cli.ExitCode.SUCCESS)


@pytest.mark.parametrize(
    ("case_id", "arguments"),
    [
        ("exit-0-success", []),
        ("exit-2-rerun-with-tags", ["--rerun", "--tags", "@Login"]),
        ("exit-3-dead-worker", []),
        ("exit-4-empty-merge", []),
        ("exit-4-writer-failed", []),
    ],
    ids=[
        "exit-0-success",
        "exit-2-rerun-with-tags",
        "exit-3-dead-worker",
        "exit-4-empty-merge",
        "exit-4-writer-failed",
    ],
)
def test_worker_directory_is_removed_after_every_exit_class(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_result_set: Any,
    case_id: str,
    arguments: list[str],
) -> None:
    """Whatever the status, ``target/.workers`` is gone when the command returns.

    The directory is seeded with a worker result file before each invocation,
    so its absence afterwards is a removal and not an artefact of it never
    having been created.  ``--no-clean`` is used throughout, so the removal
    cannot be credited to the clean step.

    The empty merge and the writer failure are two *causes* of one status,
    :attr:`~app.cli.ExitCode.ARTIFACT_FAILURE`, and both are driven here
    because the guarantee is about every exit path rather than every value:
    each cause reaches the cleanup through a different branch of ``_publish``.
    """
    expected = {
        "exit-0-success": int(cli.ExitCode.SUCCESS),
        "exit-2-rerun-with-tags": int(cli.ExitCode.USAGE_ERROR),
        "exit-3-dead-worker": int(cli.ExitCode.WORKER_DIED),
        "exit-4-empty-merge": int(cli.ExitCode.ARTIFACT_FAILURE),
        "exit-4-writer-failed": int(cli.ExitCode.ARTIFACT_FAILURE),
    }[case_id]

    if case_id == "exit-3-dead-worker":
        install_services(
            monkeypatch,
            run_suite=RunSuiteStub(
                make_run_outcome(
                    sample_result_set,
                    selected_count=4,
                    worker_count=2,
                    dead_shards=("shard 2 of 2: worker died",),
                )
            ),
            generate_reports=GenerateReportsStub(
                ok_report(*artifact_paths(cli_root))
            ),
        )
    elif case_id == "exit-4-empty-merge":
        install_services(
            monkeypatch,
            run_suite=RunSuiteStub(
                make_run_outcome(None, selected_count=4, merge_produced_nothing=True)
            ),
            generate_reports=GenerateReportsStub(),
        )
    elif case_id == "exit-4-writer-failed":
        install_services(
            monkeypatch,
            run_suite=RunSuiteStub(
                make_run_outcome(sample_result_set, selected_count=4)
            ),
            generate_reports=GenerateReportsStub(
                failed_report(
                    written=[artifact_paths(cli_root)[0]],
                    failed_index=1,
                    error=OSError("read-only file system"),
                    failed_path=artifact_paths(cli_root)[1],
                )
            ),
        )
    else:
        _cleanup_case_success(monkeypatch, cli_root, sample_result_set)

    workers = seed_worker_directory(cli_root)

    result = invoke(runner, [*arguments, "--no-clean"])

    assert result.exit_code == expected
    assert not workers.exists(), "the per-worker directory survived the command"


def test_worker_directory_is_removed_when_the_service_raises(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unexpected exception still leaves the intermediates removed.

    The removal is in a ``finally``, which is what this asserts: an exception
    no documented contract predicts propagates with its traceback - it is
    deliberately *not* mapped onto a published class - and the per-worker
    directory is gone all the same.
    """
    failure = RuntimeError("the engine could not be launched")
    install_services(monkeypatch, run_suite=RunSuiteStub(raises=failure))
    workers = seed_worker_directory(cli_root)

    result = invoke(runner, ["--no-clean"])

    assert result.exception is failure
    assert result.exit_code != int(cli.ExitCode.SUCCESS)
    assert not workers.exists()


def test_worker_directory_is_removed_after_a_keyboard_interrupt(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An interrupted run cleans up before it stops.

    Stopping a run is the operator's decision rather than an outcome to
    translate into a status, so the interrupt passes straight through the
    cleanup uncaught - Click turns it into its own abort - and the per-worker
    directory is still removed on the way out.
    """
    install_services(monkeypatch, run_suite=RunSuiteStub(raises=KeyboardInterrupt()))
    workers = seed_worker_directory(cli_root)

    result = invoke(runner, ["--no-clean"])

    assert result.exit_code not in {int(member) for member in cli.ExitCode}
    assert "Aborted!" in result.stderr
    assert not workers.exists()


def test_clean_removes_the_worker_directory_before_the_run(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--clean`` empties the per-worker directory too, before the run starts.

    It sits inside the build output, so the clean step covers it implicitly -
    and the ordering matters: the stub observes the directory from inside the
    run and must find it already gone, not removed afterwards by the
    ``finally``.
    """
    workers = seed_worker_directory(cli_root)
    run_suite = RunSuiteStub(observe=workers.exists)
    install_services(
        monkeypatch, run_suite=run_suite, generate_reports=GenerateReportsStub()
    )

    result = invoke(runner, ["--clean"])

    assert result.exit_code == int(cli.ExitCode.SUCCESS)
    assert run_suite.observations == [False]
    assert not workers.exists()


def test_no_worker_json_survives_where_the_publisher_glob_could_match(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_result_set: Any,
) -> None:
    """The only JSON left in the build output is the report the publisher reads.

    ``Jenkins:15``'s ``fileIncludePattern`` narrows from ``**/*.json`` to
    ``target/cucumber.json`` (AAP deviation 3) precisely because this port
    adds other JSON to the workspace - the per-worker intermediates here, and
    ``tests/fixtures/*.json`` elsewhere.  The narrowed glob and this removal
    are two halves of one guarantee, so with the real writers the build output
    must contain exactly one JSON file when the command returns.
    """
    seed_worker_directory(cli_root)
    install_services(
        monkeypatch,
        run_suite=RunSuiteStub(
            make_run_outcome(sample_result_set, selected_count=5, worker_count=2)
        ),
    )

    result = invoke(runner, ["--no-clean"])

    assert result.exit_code == int(cli.ExitCode.SUCCESS)
    json_files = sorted(paths.target_root(cli_root).rglob("*.json"))
    assert json_files == [paths.cucumber_json_path(cli_root)]
    assert not paths.workers_dir(cli_root).exists()


# --------------------------------------------------------------------------- #
# Section G - the clean step
#
# app/cli.py requirement 11.  The symlinked-root and unremovable-entry
# tolerance paths are deliberately not asserted here: they are being changed
# elsewhere, and what this section pins is the documented behaviour of the two
# flags.
# --------------------------------------------------------------------------- #


def test_clean_empties_the_build_output_before_the_run(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--clean`` empties the build output first, reproducing ``mvn clean test``.

    Both halves are asserted: that the stale files and the stale
    sub-directory are gone, and that they were gone **before** the run - the
    stub lists the directory from inside the run.  The directory itself
    survives, so an emptied build output is observably empty rather than
    absent, which is the state the empty-merge row refers to.
    """
    decoys = write_decoy_artifacts(cli_root)
    target = paths.target_root(cli_root)
    run_suite = RunSuiteStub(
        observe=lambda: sorted(entry.name for entry in target.iterdir())
    )
    install_services(
        monkeypatch, run_suite=run_suite, generate_reports=GenerateReportsStub()
    )

    result = invoke(runner, ["--clean"])

    assert result.exit_code == int(cli.ExitCode.SUCCESS)
    assert run_suite.observations == [[]]
    assert target.is_dir()
    assert list(target.iterdir()) == []
    for path in decoys:
        assert not path.exists()


def test_no_clean_leaves_the_build_output_untouched(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--no-clean`` keeps every stale entry, bytes included.

    The run still sees them, so a caller that asked to keep previous output
    keeps it: nothing is emptied, renamed or rewritten on its behalf.
    """
    decoys = write_decoy_artifacts(cli_root)
    target = paths.target_root(cli_root)
    run_suite = RunSuiteStub(
        observe=lambda: sorted(entry.name for entry in target.iterdir())
    )
    install_services(
        monkeypatch, run_suite=run_suite, generate_reports=GenerateReportsStub()
    )

    result = invoke(runner, ["--no-clean"])

    assert result.exit_code == int(cli.ExitCode.SUCCESS)
    assert run_suite.observations[0] != []
    assert_decoys_unchanged(decoys)


# --------------------------------------------------------------------------- #
# Section H - streams and logging
#
# app/cli.py requirement 12, and AAP 0.4.1: "Both streams are line-buffered:
# progress to stdout, engine diagnostics to stderr".
# --------------------------------------------------------------------------- #


def test_progress_goes_to_stdout_and_nothing_diagnostic_leaks(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_result_set: Any,
) -> None:
    """A clean run puts progress on stdout and leaves stderr empty.

    ``app/logging_config.py`` gives the stdout handler a filter that excludes
    ``WARNING`` and above, so the two handlers partition the records rather
    than overlapping; a run with nothing to report must therefore produce no
    stderr output at all.
    """
    install_services(
        monkeypatch,
        run_suite=RunSuiteStub(
            make_run_outcome(sample_result_set, selected_count=5, worker_count=2)
        ),
        generate_reports=GenerateReportsStub(ok_report(*artifact_paths(cli_root))),
    )

    result = invoke(runner)

    assert result.exit_code == int(cli.ExitCode.SUCCESS)
    lines = message_lines(result.stdout)
    assert lines, "a run reported no progress at all"
    assert all(line.startswith(STDOUT_LEVEL_PREFIXES) for line in lines), lines
    assert START_MESSAGE_MARKER in result.stdout
    assert "Finished with status 0" in result.stdout
    assert result.stderr == ""


@pytest.mark.parametrize(
    "case_id",
    ["tolerated-selection-problem", "dead-shard", "writer-failure"],
)
def test_diagnostics_go_to_stderr(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_result_set: Any,
    case_id: str,
) -> None:
    """Every diagnostic the exit contract requires to be visible is on stderr.

    A tolerated selection problem, a dead-shard report and a writer failure
    are the three the contract names, and each must be on stderr with nothing
    of it leaking onto stdout - a Jenkins log that mixed them would make the
    progress stream unreadable and hide the failure among it.
    """
    marker = {
        "tolerated-selection-problem": "features/Broken.feature:7: Parser failure",
        "dead-shard": "shard 3 of 4: worker died",
        "writer-failure": WRITER_NAMES[1],
    }[case_id]

    if case_id == "tolerated-selection-problem":
        run_suite = RunSuiteStub(
            make_run_outcome(
                sample_result_set, selected_count=5, parse_errors=(marker,)
            )
        )
        generate_reports = GenerateReportsStub(ok_report(*artifact_paths(cli_root)))
    elif case_id == "dead-shard":
        run_suite = RunSuiteStub(
            make_run_outcome(
                sample_result_set,
                selected_count=8,
                worker_count=4,
                dead_shards=(marker,),
            )
        )
        generate_reports = GenerateReportsStub(ok_report(*artifact_paths(cli_root)))
    else:
        run_suite = RunSuiteStub(
            make_run_outcome(sample_result_set, selected_count=5)
        )
        generate_reports = GenerateReportsStub(
            failed_report(
                written=[artifact_paths(cli_root)[0]],
                failed_index=1,
                error=OSError("permission denied"),
            )
        )

    install_services(
        monkeypatch, run_suite=run_suite, generate_reports=generate_reports
    )

    result = invoke(runner, ["--no-clean"])

    assert marker in result.stderr
    assert marker not in result.stdout
    assert all(
        line.startswith(STDERR_LEVEL_PREFIXES)
        for line in message_lines(result.stderr)
    ), result.stderr
    assert all(
        line.startswith(STDOUT_LEVEL_PREFIXES)
        for line in message_lines(result.stdout)
    ), result.stdout


def test_configure_logging_runs_before_the_service(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Logging is configured first, before any work of any kind.

    It is what keeps a Jenkins log live while a run is in progress: Python
    would otherwise block-buffer stdout through a pipe and a slow run would
    look silent.  Configuring it after the run had started would lose exactly
    the records the operator is waiting for.

    "Before any work" is asserted against the two things that come next, not
    only against the run: the recorder reads the build output at the moment it
    is called and still finds the stale entries there, so the clean step had
    not run either - while the run itself, called afterwards, finds the
    directory emptied.
    """
    decoys = write_decoy_artifacts(cli_root)
    target = paths.target_root(cli_root)
    trace: list[str] = []
    seen_at_configure_time: list[list[str]] = []

    def recorder() -> None:
        """Stand in for ``configure_logging``, recording when it was called."""
        trace.append(CONFIGURE_LOGGING_LABEL)
        seen_at_configure_time.append(sorted(entry.name for entry in target.iterdir()))

    monkeypatch.setattr(cli, "configure_logging", recorder)
    run_suite = RunSuiteStub(
        trace=trace,
        observe=lambda: sorted(entry.name for entry in target.iterdir()),
    )
    install_services(
        monkeypatch,
        run_suite=run_suite,
        generate_reports=GenerateReportsStub(trace=trace),
    )

    result = invoke(runner, ["--clean"])

    assert result.exit_code == int(cli.ExitCode.SUCCESS)
    assert trace[0] == CONFIGURE_LOGGING_LABEL
    assert trace.index(CONFIGURE_LOGGING_LABEL) < trace.index(RUN_SUITE_LABEL)
    assert trace.count(CONFIGURE_LOGGING_LABEL) == 1
    assert seen_at_configure_time[0], (
        "the clean step ran before logging was configured"
    )
    assert run_suite.observations == [[]], "the clean step did not run before the run"
    assert all(not path.exists() for path in decoys)


def test_no_record_is_emitted_twice_across_two_invocations(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two runs in one process still log each record exactly once.

    ``configure_logging`` replaces the handlers it installed rather than
    appending to them, so the ``app`` logger carries its two named handlers no
    matter how many times a process configures it.  A duplicate would double
    every line of a Jenkins console log.

    Only the two *managed* handlers are counted, and deliberately so: that
    function documents that it has no effect on handlers it did not install,
    and this logger is shared - the Flask application is named ``app`` too, so
    a test that built one earlier in the session may have left an unnamed
    handler here.  Tolerating it is the contract; duplicating our own would
    not be.
    """
    install_services(
        monkeypatch,
        run_suite=RunSuiteStub(),
        generate_reports=GenerateReportsStub(),
    )

    first = invoke(runner)
    second = invoke(runner)

    assert first.exit_code == int(cli.ExitCode.SUCCESS)
    assert second.exit_code == int(cli.ExitCode.SUCCESS)
    assert first.stdout.count(START_MESSAGE_MARKER) == 1
    assert second.stdout.count(START_MESSAGE_MARKER) == 1

    logger = logging.getLogger(PACKAGE_LOGGER_NAME)
    managed_names = {STDOUT_HANDLER_NAME, STDERR_HANDLER_NAME}
    installed = [
        handler.get_name()
        for handler in logger.handlers
        if handler.get_name() in managed_names
    ]
    assert sorted(installed) == sorted(managed_names)
    assert logger.propagate is False


def test_exit_status_is_independent_of_logged_errors(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_result_set: Any,
) -> None:
    """A run that logs errors still exits ``0`` unless it hits a non-zero class.

    Every signal is logged whatever the status, so choosing a status discards
    no information - and the converse holds too: the presence of ``ERROR``
    records is not itself a status.
    """
    install_services(
        monkeypatch,
        run_suite=RunSuiteStub(
            make_run_outcome(
                sample_result_set,
                selected_count=5,
                parse_errors=(
                    "features/A.feature: Parser failure",
                    "features/B.feature: Parser failure",
                ),
            )
        ),
        generate_reports=GenerateReportsStub(ok_report(*artifact_paths(cli_root))),
    )

    result = invoke(runner)

    assert result.exit_code == int(cli.ExitCode.SUCCESS)
    assert len(message_lines(result.stderr)) >= 3
    assert "Finished with status 0" in result.stdout


# --------------------------------------------------------------------------- #
# Section H2 - line buffering, the other half of the stream contract
#
# AAP 0.4.1 fixes two things about the streams, and they are separate
# mechanisms: *which* stream a record reaches, which section H covers, and
# that both streams are **line-buffered**, which is what this section covers.
# The second cannot be observed through ``CliRunner``: its capture installs
# in-memory replacement objects, so a reconfiguration request lands on those
# and never on a process stream.  These tests therefore drive the command the
# way the console script does - ``Command.main()``, which exits the
# interpreter - with recording objects standing in for the process streams.
#
# Why it is worth its own section: ``StreamHandler`` flushes after every
# record, so the records already leave one line at a time.  What line
# buffering adds is that the *underlying* stream stays line-buffered when the
# process writes into a pipe, which is exactly the Jenkins case - without it
# Python switches stdout to block buffering and a slow browser run looks
# silent until the buffer fills.
# --------------------------------------------------------------------------- #

#: The keyword arguments a line-buffering request must carry, and the whole of
#: them: ``reconfigure`` accepts several, and a request that also changed the
#: encoding or the newline translation of a CI console would be a different
#: thing from the one AAP 0.4.1 asks for.
EXPECTED_RECONFIGURE_KWARGS: Final[dict[str, Any]] = {"line_buffering": True}

#: Event labels the recording streams below log, so one ordered list shows
#: both that the request happened and that it happened before any output.
RECONFIGURE_EVENT: Final[str] = "reconfigure"
WRITE_EVENT: Final[str] = "write"


class PlainStream(io.StringIO):
    """A text stream with **no** ``reconfigure``, recording what it is given.

    The stand-in for a stream that cannot be line-buffered at all - a
    ``StringIO``, the objects a harness installs, or a stream a host has
    wrapped.  ``app/logging_config.py`` documents that case as ordinary rather
    than exceptional, and this class is how that claim is exercised: the
    attribute is genuinely absent, so the guard's ``getattr`` finds nothing.
    """

    __slots__ = ("events",)

    def __init__(self) -> None:
        """Build an empty stream with an empty event log."""
        super().__init__()

        #: Every event, in order, as ``(label, payload)`` pairs.
        self.events: list[tuple[str, Any]] = []

    def write(self, text: str) -> int:
        """Record the text and store it.

        :param text: What the handler wrote.
        :returns: The number of characters written.
        """
        self.events.append((WRITE_EVENT, text))
        return super().write(text)

    @property
    def reconfigure_calls(self) -> list[Any]:
        """The reconfiguration requests this stream received.

        :returns: Always empty - the point of this class is that there is no
            ``reconfigure`` to call.
        """
        return []


class RecordingStream(PlainStream):
    """A :class:`PlainStream` that also has a ``reconfigure`` and records it.

    The stand-in for a real process stream.  ``reconfigure`` may be made to
    raise, which covers the second half of the guard: a detached or closed
    stream, or a custom one whose ``reconfigure`` rejects the keyword.
    """

    __slots__ = ("_error",)

    def __init__(
        self, *, error: BaseException | None = None
    ) -> None:
        """Build a stream whose ``reconfigure`` records and optionally fails.

        :param error: Raised by :meth:`reconfigure` when given, so the
            guarded path in ``app/logging_config.py`` is taken.
        """
        super().__init__()
        self._error = error

    def reconfigure(self, *args: Any, **kwargs: Any) -> None:
        """Record a reconfiguration request, then honour or refuse it.

        :param args: Positional arguments, recorded so a test can assert
            there were none - the port passes the keyword by name.
        :param kwargs: The requested settings.
        :raises BaseException: The error this stream was built with, if any.
        """
        self.events.append((RECONFIGURE_EVENT, (args, dict(kwargs))))
        if self._error is not None:
            raise self._error

    @property
    def reconfigure_calls(self) -> list[Any]:
        """The reconfiguration requests this stream received, in order.

        :returns: One ``(args, kwargs)`` pair per call.
        """
        return [payload for label, payload in self.events if label == RECONFIGURE_EVENT]


def run_as_console_script(args: Sequence[str] = ()) -> int:
    """Run the command the way ``run-tests`` does, and return its status.

    ``CliRunner`` is deliberately not used here: it replaces the process
    streams, which is precisely what these tests are about.  ``Command.main``
    is the entry point ``pyproject.toml``'s console script reaches, and it
    ends the interpreter, so the status is read from the :exc:`SystemExit`.

    :param args: The command line, without the program name.
    :returns: The exit status the command produced.
    """
    with pytest.raises(SystemExit) as raised:
        cli.run_tests.main(list(args), prog_name=cli.COMMAND_NAME)

    code = raised.value.code
    return 0 if code is None else int(code)


def install_recording_streams(
    monkeypatch: pytest.MonkeyPatch,
    out: PlainStream,
    err: PlainStream,
) -> None:
    """Put ``out`` and ``err`` in place of the two process streams.

    ``monkeypatch`` restores both at teardown, and the handlers
    ``configure_logging`` installs resolve ``sys.stdout`` and ``sys.stderr``
    by name each time they emit, so these objects receive the records as well
    as the reconfiguration request - which is what lets one test assert that
    the buffering and the routing concern the same two streams.

    :param monkeypatch: pytest's patcher.
    :param out: The stand-in for ``sys.stdout``.
    :param err: The stand-in for ``sys.stderr``.
    """
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)


def reporting_run(
    monkeypatch: pytest.MonkeyPatch, root: Path, document: Any
) -> GenerateReportsStub:
    """Arrange a run that writes to both streams, with no real work done.

    One tolerated selection problem gives stderr a record and the ordinary
    progress lines give stdout several, so a single invocation exercises both
    handlers.  The fan-out is a stand-in, so no artifact is produced and the
    caller asserts that the command reached it rather than what it wrote -
    which artifacts a run produces is settled by the exit-table tests in
    section E.

    :param monkeypatch: pytest's patcher.
    :param root: The checkout root the reported artifact paths resolve against.
    :param document: The merged document the run reports.
    :returns: The fan-out stand-in, so a test can assert the command reached
        the end of its work rather than dying on a stream.
    """
    reports = GenerateReportsStub(ok_report(*artifact_paths(root)))
    install_services(
        monkeypatch,
        run_suite=RunSuiteStub(
            make_run_outcome(
                document,
                selected_count=4,
                worker_count=2,
                parse_errors=("features/Broken.feature: cannot be parsed",),
            )
        ),
        generate_reports=reports,
    )
    return reports


def test_both_process_streams_are_line_buffered_for_pipe_capture(
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_result_set: Any,
) -> None:
    """The command line-buffers stdout and stderr before it reports anything.

    AAP 0.4.1: "Both streams are line-buffered: progress to stdout, engine
    diagnostics to stderr."  ``configure_logging`` puts each stream in
    line-buffered mode by requesting exactly ``reconfigure(line_buffering=
    True)``, and this test asserts the request itself rather than the
    captured output, because a Jenkins log that arrives only at the end of a
    run is indistinguishable from one that arrives live once the run has
    finished.

    Three properties, all of them the contract rather than the implementation:
    both streams are asked, each exactly once and with that keyword alone, and
    the request precedes the first line of output - a stream reconfigured
    after the progress had been written would have buffered the very lines
    the setting exists for.
    """
    out = RecordingStream()
    err = RecordingStream()
    install_recording_streams(monkeypatch, out, err)
    reporting_run(monkeypatch, cli_root, sample_result_set)

    assert run_as_console_script() == int(cli.ExitCode.SUCCESS)

    for stream, name in ((out, "stdout"), (err, "stderr")):
        calls = stream.reconfigure_calls
        assert len(calls) == 1, f"{name} was reconfigured {len(calls)} time(s)"
        positional, keywords = calls[0]
        assert positional == (), f"{name} was reconfigured positionally"
        assert keywords == EXPECTED_RECONFIGURE_KWARGS
        labels = [label for label, _ in stream.events]
        assert labels[0] == RECONFIGURE_EVENT, (
            f"{name} was written to before it was line-buffered"
        )

    # The same two objects carry the records, so the line-buffering request and
    # the stream split are not describing different streams.
    assert START_MESSAGE_MARKER in out.getvalue()
    assert "Finished with status 0" in out.getvalue()
    assert "cannot be parsed" in err.getvalue()
    assert "cannot be parsed" not in out.getvalue()


@pytest.mark.parametrize(
    "case_id",
    ["no-reconfigure-attribute", "reconfigure-raises"],
)
def test_a_stream_that_cannot_be_line_buffered_does_not_stop_the_run(
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_result_set: Any,
    case_id: str,
) -> None:
    """Line buffering is an optimisation for pipe capture, never a requirement.

    ``app/logging_config.py`` guards the request fully and treats "this stream
    cannot be reconfigured" as an ordinary handled outcome, so a host that
    installed a stream without ``reconfigure`` - or one whose ``reconfigure``
    refuses the keyword, which a detached or closed stream does - still gets a
    run that reports on both streams and still gets the documented status.
    Without the guard the command would die before its first line, in the one
    situation where a CI log is the only evidence available.

    :param case_id: Which unreconfigurable stream to install.
    """
    if case_id == "no-reconfigure-attribute":
        out: PlainStream = PlainStream()
        err: PlainStream = PlainStream()
        assert getattr(out, "reconfigure", None) is None
    else:
        out = RecordingStream(error=ValueError("underlying buffer detached"))
        err = RecordingStream(error=ValueError("underlying buffer detached"))

    install_recording_streams(monkeypatch, out, err)
    reports = reporting_run(monkeypatch, cli_root, sample_result_set)

    assert run_as_console_script() == int(cli.ExitCode.SUCCESS)

    # Attempted once per stream where there was anything to attempt, and the
    # failure was absorbed rather than reported as a run failure.
    expected_attempts = 0 if case_id == "no-reconfigure-attribute" else 1
    assert len(out.reconfigure_calls) == expected_attempts
    assert len(err.reconfigure_calls) == expected_attempts

    # The run went all the way through: selection, the fan-out, and both
    # streams still carrying what the contract routes to them.
    assert reports.called
    assert START_MESSAGE_MARKER in out.getvalue()
    assert "Finished with status 0" in out.getvalue()
    assert "cannot be parsed" in err.getvalue()
    for path in artifact_paths(cli_root):
        assert str(path) in out.getvalue()


# --------------------------------------------------------------------------- #
# Section I - composition and boundaries
#
# app/cli.py requirements 13, 14 and 15.
# --------------------------------------------------------------------------- #


def test_cli_calls_run_suite_then_generate_reports_in_that_order(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_result_set: Any,
) -> None:
    """The command is the only composer: run first, then report.

    It hands the fan-out the run's own merged document - the same object, not
    a copy - and passes no path, because each writer resolves its own
    destination from ``app/utils/paths.py``.
    """
    trace: list[str] = []
    outcome = make_run_outcome(sample_result_set, selected_count=5, worker_count=2)
    generate_reports = GenerateReportsStub(
        ok_report(*artifact_paths(cli_root)), trace=trace
    )
    install_services(
        monkeypatch,
        run_suite=RunSuiteStub(outcome, trace=trace),
        generate_reports=generate_reports,
    )

    result = invoke(runner)

    assert result.exit_code == int(cli.ExitCode.SUCCESS)
    assert trace == [RUN_SUITE_LABEL, GENERATE_REPORTS_LABEL]
    assert generate_reports.documents == [outcome.result_set]
    assert generate_reports.documents[0] is outcome.result_set
    assert generate_reports.keywords == [{}]


def imported_module_names(source: str) -> frozenset[str]:
    """Every module name an import statement in ``source`` names.

    Parsed rather than grepped, so a module mentioned in prose or in a
    docstring is not mistaken for an import.

    :param source: Python source text.
    :returns: The imported module names, relative imports included with their
        leading dots.
    """
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = "." * node.level
            names.add(f"{prefix}{node.module or ''}")
    return frozenset(names)


def test_neither_service_imports_the_other() -> None:
    """The two services are independent; the command connects them.

    AAP 0.4.2 draws the dependency edge one way - ``app/cli.py`` ->
    ``app.services`` -> (``app.reporting``, ``app.utils``) - so a service
    reaching for its sibling, or for the barrel that re-exports it, would put
    execution and artifact production on one another's import path.
    """
    forbidden = {
        RUN_SERVICE_PATH: ("report_service", "app.services"),
        REPORT_SERVICE_PATH: ("test_run_service", "app.services"),
    }
    for path, sibling_names in forbidden.items():
        imported = imported_module_names(path.read_text(encoding="utf-8"))
        for name in sibling_names:
            offenders = [
                module for module in imported if module.lstrip(".").startswith(name)
            ]
            assert not offenders, f"{path.name} imports {offenders}"


def path_shaped_matches(source: str, name: str) -> list[str]:
    """Occurrences of ``name`` in ``source`` that are shaped like a path.

    ``target``, ``features`` and ``cucumber`` are ordinary words as well as
    directory names - ``app/cli.py`` legitimately says "a features root" in
    prose and calls ``target_root()`` - so a bare substring search would be
    noise.  What is looked for instead is the shapes a *path literal* takes:
    quoted with a single or double quote (never a backtick, which is reST
    markup in this codebase), or adjacent to a separator.

    :param source: The module source.
    :param name: The directory name owned by ``app/utils/paths.py``.
    :returns: The matched text of every path-shaped occurrence.
    """
    escaped = re.escape(name)
    pattern = re.compile(
        rf"""['"]{escaped}['"]|/{escaped}\b|\b{escaped}/|\\{escaped}\b"""
    )
    return [match.group(0) for match in pattern.finditer(source)]


def is_dotted_symbol_reference(source: str, start: int, end: int) -> bool:
    """Whether ``source[start:end]`` sits inside a dotted symbol reference.

    ``app/utils/paths.py`` owns the port's paths, so ``app/cli.py`` refers to
    the one build-output entry it must recognise **by the symbol that owns
    it** - ``app.utils.paths.workers_dir`` - and asks that function for the
    name at run time.  The intermediate directory's own name, ``.workers``,
    is a substring of that symbol, so a plain substring search reports the
    citation as if it were the duplicated literal it is the opposite of.

    What is looked for is therefore the *shape* of the occurrence.  The match
    is grown over the characters a dotted Python name is made of; if what
    results is a longer chain of two or more identifier segments, the
    occurrence is a reference to a symbol - a docstring citation, an
    attribute access - and not a path.  A genuine literal never grows: it is
    bounded by a quote or by a path separator, so it comes back equal to the
    name and is reported.

    :param source: The module source.
    :param start: Start offset of the occurrence.
    :param end: End offset of the occurrence.
    :returns: ``True`` when the occurrence is part of a dotted symbol.
    """
    name_characters = "_."
    left = start
    while left > 0 and (
        source[left - 1].isalnum() or source[left - 1] in name_characters
    ):
        left -= 1
    right = end
    while right < len(source) and (
        source[right].isalnum() or source[right] in name_characters
    ):
        right += 1

    token = source[left:right]
    if token == source[start:end]:
        return False

    segments = token.strip(".").split(".")
    return len(segments) >= 2 and all(
        segment.isidentifier() for segment in segments
    )


def path_literal_occurrences(source: str, name: str) -> list[str]:
    """Occurrences of ``name`` in ``source`` that are path literals.

    Every occurrence that is part of a dotted symbol reference is dropped -
    see :func:`is_dotted_symbol_reference` - so what remains is the module
    spelling the path out for itself.

    :param source: The module source.
    :param name: The path name owned by ``app/utils/paths.py``.
    :returns: The surrounding text of each occurrence that is a literal, for
        a failure message that can be acted on.
    """
    occurrences: list[str] = []
    for match in re.finditer(re.escape(name), source):
        if is_dotted_symbol_reference(source, match.start(), match.end()):
            continue
        occurrences.append(source[max(0, match.start() - 40) : match.end() + 40])
    return occurrences


def non_docstring_string_literals(source: str) -> list[str]:
    """Every string constant in ``source`` that is not a docstring.

    :param source: Python source text.
    :returns: The literal values - help texts, log messages, names.
    """
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                docstrings.add(id(first.value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_module_source_contains_no_path_literal() -> None:
    """Not one path literal appears in ``app/cli.py``, anywhere.

    Requirement 13, and the reason it reaches into docstrings and comments: a
    build output directory name written in prose is the first step of the
    drift ``app/utils/paths.py`` exists to prevent, and the ``--clean`` step,
    the writers and the artifact route would then have two spellings of one
    path.  Every name checked below is read from ``app/utils/paths.py`` rather
    than spelled out here, so this test cannot drift from the module it
    protects either.

    What a *dotted symbol reference* is not is a duplicated literal: naming
    ``app.utils.paths.workers_dir`` cites the owner of the path and is how the
    module documents that it holds none, so an occurrence inside such a chain
    is skipped by :func:`path_literal_occurrences`.  A literal is bounded by a
    quote or a separator and is reported.
    """
    source = CLI_SOURCE_PATH.read_text(encoding="utf-8")

    # Names no English sentence contains, so their mere presence is a literal.
    unmistakable = (
        paths.WORKERS_DIR_NAME,
        paths.CUCUMBER_JSON_NAME,
        paths.RERUN_TXT_NAME,
        paths.CUCUMBER_REPORTS_HTML_NAME,
        paths.PRETTY_HTML_SUBDIR,
        paths.PRETTY_OVERVIEW_INDEX,
        paths.CUCUMBER_JSON_RELPATH,
        paths.RERUN_TXT_RELPATH,
        paths.CUCUMBER_REPORTS_HTML_RELPATH,
        paths.PRETTY_REPORTS_RELPATH,
        paths.NORMALIZED_FEATURES_PREFIX,
        paths.LEGACY_FEATURES_PREFIX,
    )
    for name in unmistakable:
        spelled = path_literal_occurrences(source, name)
        assert not spelled, f"app/cli.py names the path {name!r}: {spelled}"

    # Names that are also ordinary words: only a path *shape* is a violation.
    word_like = (
        paths.TARGET_DIR_NAME,
        paths.FEATURES_DIR_NAME,
        paths.PRETTY_REPORTS_DIR_NAME,
    )
    for name in word_like:
        assert not path_shaped_matches(source, name), (
            f"app/cli.py spells the path {name!r}"
        )

    # And no runtime string - a help text, a log message, an option name -
    # carries one of those words at all, docstring prose aside.
    for literal in non_docstring_string_literals(source):
        for name in word_like:
            assert not re.search(rf"\b{re.escape(name)}\b", literal), (
                f"a string literal in app/cli.py names {name!r}: {literal!r}"
            )


def _constructs_a_path(func: ast.expr) -> bool:
    """Whether a call target builds a path out of its arguments.

    ``str.join`` is deliberately not one of them: joining reasons into a log
    line shares a method name with ``os.path.join`` and nothing else, so the
    receiver is what tells them apart - a string literal joins text, while a
    module or an object joins components into a path.

    :param func: The ``func`` of an :class:`ast.Call`.
    :returns: ``True`` for a path constructor or a path-joining call.
    """
    if isinstance(func, ast.Name):
        return func.id in {"Path", "PurePath", "PurePosixPath"}
    if isinstance(func, ast.Attribute):
        if func.attr in {"Path", "PurePath", "PurePosixPath", "joinpath"}:
            return True
        return func.attr == "join" and not isinstance(func.value, ast.Constant)
    return False


def test_module_resolves_no_artifact_path_of_its_own() -> None:
    """Every path the module names it was given; it resolves none.

    Requirement 13's other half, and the structural form of it.  Banning
    ``pathlib`` outright was the old shape of this check and it no longer
    expresses the rule: the fail-closed ``--clean`` step needs
    :class:`~pathlib.Path` for its annotations and the ``os``, ``shutil`` and
    ``stat`` primitives for descriptor-relative removal, which is what lets it
    refuse a symlinked build output instead of following it.  Those are
    operations **on** a directory the module was handed, not the resolution of
    a destination.

    So what is asserted is where a path can come *from*:

    * ``app.utils.paths`` is imported, and only ``target_root`` and
      ``workers_dir`` are taken from it - the build output root the clean step
      empties and the name of the one entry it must not delete outright.  Not
      one artifact accessor and not the generic ``artifact_path`` resolver
      appears, so the destination in a writer-failure record can only be the
      one the fan-out resolved and carried on
      :attr:`~app.services.ReportOutcome.failed_path`.
    * ``os.path`` is absent, so there is no join/dirname toolkit in scope.
    * :class:`~pathlib.Path` is never *called*: it appears in annotations
      only, so no path is constructed from a name this module chose.
    """
    source = CLI_SOURCE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = imported_module_names(source)

    assert "app.utils.paths" in imported
    assert "os.path" not in imported, imported

    taken_from_paths = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "app.utils.paths"
        for alias in node.names
    }
    assert taken_from_paths == {"target_root", "workers_dir"}, taken_from_paths

    constructed = [
        ast.unparse(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _constructs_a_path(node.func)
    ]
    assert not constructed, constructed


#: Probe run in a clean interpreter for requirement 14.  It records every file
#: opened while ``app.cli`` is imported, then reports what that import did.
_IMPORT_PROBE: Final[str] = """
import json
import logging
import os
import sys

opened = []


def _record(event, args):
    if event == "open":
        opened.append(str(args[0]))


sys.addaudithook(_record)

import app.cli

json.dump(
    {
        "flask_imported": "flask" in sys.modules,
        "web_imported": "app.web" in sys.modules,
        "selenium_imported": "selenium" in sys.modules,
        "entries": sorted(os.listdir(".")),
        "properties_opened": [p for p in opened if p.endswith(PROPERTIES_FILENAME)],
        "app_handlers": len(logging.getLogger("app").handlers),
        "command_name": app.cli.COMMAND_NAME,
    },
    sys.stdout,
)
"""


def test_importing_the_module_has_no_side_effects(cli_root: Path) -> None:
    """Importing ``app.cli`` builds nothing, reads nothing and creates nothing.

    Requirement 14, in a fresh interpreter because the claim is about the
    *process-initial* state and this one has already imported half the port.
    A decoy ``configuration.properties`` sits in the working directory and an
    audit hook records every file opened, so "reads no configuration" is
    observed rather than assumed; the working directory is checked for a build
    output directory the import must not have created; and the ``app`` logger
    has no handlers, because ``configure_logging`` is called inside the
    command and never at import.
    """
    (cli_root / properties.PROPERTIES_FILENAME).write_text(
        "browser=chrome\n", encoding="utf-8"
    )
    probe = _IMPORT_PROBE.replace(
        "PROPERTIES_FILENAME", repr(properties.PROPERTIES_FILENAME)
    )

    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=cli_root,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    report = json.loads(completed.stdout)

    assert report["command_name"] == cli.COMMAND_NAME
    assert report["flask_imported"] is False
    assert report["web_imported"] is False
    assert report["selenium_imported"] is False
    assert report["properties_opened"] == []
    assert report["app_handlers"] == 0
    assert report["entries"] == [properties.PROPERTIES_FILENAME]
    assert not paths.target_root(cli_root).exists()


def test_create_app_exposes_the_same_command_object() -> None:
    """The Flask CLI group carries *this* command object, not a copy.

    ``app/__init__.py`` is the port's sole registration point, so identity is
    the assertion: a redefined or wrapped command would let the two routes
    diverge silently.  Nothing invokes the suite through the Flask CLI - the
    registration exists to satisfy that invariant - and the group carries no
    second command.
    """
    flask_app = create_app({"TESTING": True})

    assert flask_app.cli.commands[cli.COMMAND_NAME] is cli.run_tests
    assert set(flask_app.cli.commands) == {cli.COMMAND_NAME}


def executable_lines(path: Path) -> str:
    """The lines of ``path`` with comment-only lines removed.

    Both runner scripts and the ``Makefile`` discuss in comments exactly what
    they must never do - the ``Makefile`` says "never ``python -m app.cli``"
    in as many words - so a check that read the whole file would fail on the
    documentation of the rule it is checking.

    :param path: The file to read.
    :returns: The executable text: comment lines and PowerShell block
        comments removed.
    """
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"<#.*?#>", "", text, flags=re.DOTALL)
    return "\n".join(
        line
        for line in text.splitlines()
        if not line.lstrip().startswith("#")
    )


@pytest.mark.parametrize(
    "path", RUNNER_ENTRY_POINT_PATHS, ids=[p.name for p in RUNNER_ENTRY_POINT_PATHS]
)
def test_every_runner_entry_point_invokes_the_console_script(path: Path) -> None:
    """Each caller reaches the suite through the ``run-tests`` console script.

    AAP 0.4.1: both runner scripts, the ``Makefile`` and the README invoke the
    entry point from the virtual environment's ``bin``/``Scripts`` directory,
    and **nothing** invokes it through the Flask CLI.  The assertions are kept
    to that - another unit owns these files, so their wording is not pinned
    here, only the entry point they use and the two routes they must not.
    """
    if not path.exists():
        pytest.skip(f"{path.name} is owned by another unit and is not present")

    body = executable_lines(path)

    assert cli.COMMAND_NAME in body, f"{path.name} never names the console script"
    assert "flask run-tests" not in body
    assert "python -m app.cli" not in body
    assert not re.search(r"\bbehave\b", body), f"{path.name} invokes the engine"


# --------------------------------------------------------------------------- #
# Section J - the real writers, end to end
# --------------------------------------------------------------------------- #


def test_real_writers_produce_the_four_artifacts_and_exit_zero(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_result_set: Any,
) -> None:
    """One run, the real fan-out, four artifacts where the port says they go.

    Only the run is stubbed: ``generate_reports`` and all four writers are the
    production ones, resolving their destinations through
    ``app/utils/paths.py`` from the working directory.  This is the test that
    would notice the command passing a path, passing the wrong document, or
    reporting artifacts it did not produce.
    """
    install_services(
        monkeypatch,
        run_suite=RunSuiteStub(
            make_run_outcome(sample_result_set, selected_count=14, worker_count=3)
        ),
    )

    result = invoke(runner)

    assert result.exit_code == int(cli.ExitCode.SUCCESS)
    assert_four_artifacts(cli_root)
    assert json.loads(
        paths.cucumber_json_path(cli_root).read_text(encoding="utf-8")
    ), "the JSON report is empty for a populated run"

    # What the command reports it wrote has to be what is on disk.  The three
    # file writers return their file; the report tree's writer returns the
    # directory it wrote, which is the asymmetry the report service records.
    for path in artifact_paths(cli_root)[:3]:
        assert str(path) in result.stdout
    assert str(paths.pretty_reports_html_dir(cli_root)) in result.stdout
    assert paths.pretty_reports_html_dir(cli_root).is_dir()
    assert not paths.workers_dir(cli_root).exists()


def test_the_repository_build_output_is_never_touched(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_result_set: Any,
) -> None:
    """A run under test never reaches the repository's own build output.

    The command resolves every path from the working directory, so this is the
    assertion that the redirection in :fixture:`cli_root` really holds: a
    ``--clean`` run with the real writers, and the repository's build output
    directory in exactly the state it was in beforehand.  It is stated as
    "unchanged" rather than "absent" so that a checkout which legitimately has
    build output from a real run is not mistaken for a violation.
    """
    repository_target = paths.target_root(REPO_ROOT)
    existed_before = repository_target.exists()
    contents_before = (
        sorted(entry.name for entry in repository_target.iterdir())
        if existed_before
        else None
    )
    install_services(
        monkeypatch,
        run_suite=RunSuiteStub(
            make_run_outcome(sample_result_set, selected_count=14, worker_count=3)
        ),
    )

    result = invoke(runner, ["--clean"])

    assert result.exit_code == int(cli.ExitCode.SUCCESS)
    assert_four_artifacts(cli_root)
    assert repository_target.exists() == existed_before
    if existed_before:
        assert (
            sorted(entry.name for entry in repository_target.iterdir())
            == contents_before
        )
