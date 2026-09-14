"""Tests for the ``run-tests`` command - its option surface and its exit contract.

``app/cli.py`` owns exactly two things and this module is the gate for both:
the six options of the AAP 0.4.1 CLI table and the six rows of the 0.4.1 exit
table, the command being the port of ``CukesRunner.java:9-18``'s options and
``FailedTestRunner.java:11``'s ``features = "@target/rerun.txt"`` entry point.
What reaches the service layer,
what status leaves the process, what is on disk when it does, which stream each
message went to and what was cleaned up are asserted here, in sections
following the numbered requirements ``app/cli.py`` states for this module.
Execution belongs to ``tests/test_test_run_service.py``, artifact production to
``tests/test_report_service.py``.

Two properties are load-bearing, and a later reader would otherwise be right to
remove them.  First, **every test that can reach a path changes the working
directory**: ``app/cli.py`` takes no ``base`` seam - the clean step, the
per-worker cleanup, ``run_suite()`` and ``generate_reports()`` resolve paths
from the process working directory exactly as in a real run - so
:fixture:`cli_root` calls ``monkeypatch.chdir`` into a temporary root, restored
at teardown, which keeps a ``--clean`` test from deleting the repository's own
build output and is why ``tests/conftest.py``'s request that fixtures able to
pass ``base=`` not change directory does not reach here.  Second, **this module
restores the ``app`` logger itself**: the command calls ``configure_logging()``
on every invocation, which installs two named handlers on the process-global
``app`` logger and stops propagation, so a fixture here snapshots and restores
it around every test.

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
``K``
    The claim one run holds on the build output: taken before the clean,
    carried into the fan-out as every writer's guard, given back before the
    status is published and again on every way out - plus what a release that
    leaves something behind costs that status, and the class a publication
    reports when the claim was gone by the time the writers finished.
``L``
    The dead-shard diagnostics, which are emitted whatever exit class the run
    settles on, exactly once, and which claim nothing about the artifacts:
    the aggregate record is written before the exit class is known, so what
    became of the four artifacts is said by the branch that decides it.

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
import os
import re
import shutil
import stat
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
from cucumber_tag_expressions import TagExpressionError, TagExpressionParser

import app.cli as cli
import app.logging_config as logging_config

# The run service's own module, for the one private name a test has to reach:
# the grace period ``acquire_run_lock`` waits out before it refuses.  Section K
# sets it to zero so the contention case is instant rather than a thirty-second
# pause, and the alias keeps a module object from being named ``test_*`` at the
# top level of a test module, where pytest would try to make sense of it.
import app.services.test_run_service as run_service
from app import create_app
from app.logging_config import (
    PACKAGE_LOGGER_NAME,
    REDACTION_PLACEHOLDER,
    STDERR_HANDLER_NAME,
    STDOUT_HANDLER_NAME,
    TRACEBACK_LINE_PREFIX,
    TRUNCATION_SUFFIX_TEMPLATE,
    SanitizingFormatter,
    configure_logging,
    sanitize_log_text,
)
from app.reporting import new_result_set
from app.services import (
    RUN_LOCK_NAME,
    WRITER_SEQUENCE,
    ReportOutcome,
    RunLock,
    RunOutcome,
    WriterResult,
    acquire_run_lock,
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
RELEASE_LABEL: Final[str] = "release"

#: The one line the command logs before it does anything else, used to count
#: records: a duplicated record would double every line of a Jenkins console
#: log, which is what ``configure_logging``'s idempotence exists to prevent.
START_MESSAGE_MARKER: Final[str] = "Starting the suite:"

#: How the dead-shard aggregate record ends - and, because it is the *end*,
#: the whole of what that record says beyond the counts.  It is written
#: before the exit class is known, so section L reads this tail to assert
#: that it says nothing further about what became of the artifacts.
DEAD_SHARD_AGGREGATE_TAIL: Final[str] = (
    "worker shard(s) produced no results; each one is named below"
)

#: The artifact claim that belongs to the exit-3 status line and to no other
#: record.  It is true only where a publication succeeded, so on the three
#: paths where a dead shard coexists with an empty merge, a rerun or a failed
#: writer, section L asserts its absence from the whole of stderr.
DEAD_SHARD_ARTIFACT_CLAIM: Final[str] = (
    "artifacts were written from the shards that completed"
)

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

#: ``--tags`` values carrying a character that is neither printable nor a
#: plain space, each with the escape spelling the refusal has to name and a
#: fragment of the value that must appear **nowhere** on stderr.  The first
#: two already fail to parse, so refusing them takes away nothing that ever
#: worked; the third the grammar *accepts*, which is why the screen exists -
#: an accepted ESC would otherwise travel into a log record and onto every
#: worker's command line.
CONTROL_BEARING_TAG_EXPRESSIONS: Final[tuple[tuple[str, str, str], ...]] = (
    ("@a\n@b", r"'\n'", "@b"),
    ("@a\t@b", r"'\t'", "@b"),
    ("@a\x1b[31m", r"'\x1b'", "[31m"),
)

#: Malformed expressions that are themselves control-free, so what reaches
#: the console is the *grammar's* own message rather than anything the value
#: carried, paired with the number of physical lines that message occupies.
#: The second is the one that matters - a sentence, the expression echoed
#: back, and a caret marker under it - and its count is asserted so that a
#: parser that folded its diagnostic onto one line could not quietly make the
#: single-line assertion pass for the wrong reason.
CONTROL_FREE_MALFORMED_EXPRESSIONS: Final[tuple[tuple[str, int], ...]] = (
    ("@a and", 1),
    ("@a @b", 3),
)

#: Option values shaped to forge a second console record or to recolour one.
#: Used for the ``--tags``/``--browser`` asymmetry, so both halves of it are
#: pinned by the same test with the same value.
FORGING_OPTION_VALUES: Final[tuple[str, ...]] = (
    "chrome\nERROR app.fake: forged",
    "chrome\x1b[31m",
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


def lost_claim_report(*written: Path) -> ReportOutcome:
    """A fan-out whose four writers succeeded under a claim that then went.

    The shape ``generate_reports`` returns when its guard is still held
    before every writer and no longer held after the last one: all four
    artifacts written and **kept**, no failing writer, no exception, nothing
    skipped, and
    :attr:`~app.services.ReportOutcome.boundary_lost` true - which is what
    makes :attr:`~app.services.ReportOutcome.ok` false with no writer to
    blame.  The test that drives this asserts each of those fields, so a
    change to that shape in the service is caught here rather than passing
    silently through a stand-in.

    :param written: The four paths the writers returned, in writer order.
    :returns: The outcome ``app/cli.py`` maps onto
        :attr:`~app.cli.ExitCode.ARTIFACT_FAILURE` by naming the lost claim
        rather than a writer.
    """
    return ReportOutcome(
        results=tuple(
            WriterResult(name=name, path=path)
            for name, path in zip(WRITER_NAMES, written, strict=False)
        ),
        written=tuple(written),
        boundary_lost=True,
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


class RunLockStub:
    """Stands in for the claim the command takes on the build output.

    The one thing it controls is what :meth:`release` reports.  *How* a real
    release comes to leave a lock file or a shared intermediate directory
    behind is the run service's business and is asserted against the real
    lock in ``tests/test_test_run_service.py``; what section K is about is
    what this command does with the reason it is handed - which cannot be
    driven through the real lock without arranging a filesystem failure whose
    shape is not this module's contract.

    Two properties of the real :class:`~app.services.RunLock` are reproduced
    exactly, because the assertions rest on them.  :meth:`release` is
    **idempotent**: the reason is reported once and every later call reports
    nothing, which is what makes the command's ``finally`` net observable as a
    second call that changes and duplicates nothing.  And
    :meth:`is_held` stops answering true once released, so a guard examined
    from inside the fan-out answers as the real one does.

    It deliberately logs nothing.  The real lock reports its own failure under
    the run service's logger, and the port's one-emitter rule splits such an
    incident between the two layers - the cause where it was found, the
    consequence and its exit class here - so a silent stand-in is what lets a
    test count *this command's* records for a release failure and get one.

    :param problem: The reason the first release reports, or ``None`` for a
        release that leaves nothing behind.
    :param trace: A shared list the release appends :data:`RELEASE_LABEL` to,
        for the ordering assertions.
    """

    def __init__(
        self,
        problem: str | None = None,
        *,
        trace: list[str] | None = None,
    ) -> None:
        self._problem = problem
        self._trace = trace
        self.releases = 0

    def release(self) -> str | None:
        """Give the claim back, reporting what survives on the first call."""
        self.releases += 1
        if self._trace is not None:
            self._trace.append(RELEASE_LABEL)
        if self.releases > 1:
            return None
        return self._problem

    def is_held(self) -> bool:
        """Whether this claim has not been given back yet."""
        return self.releases == 0


def install_run_lock(
    monkeypatch: pytest.MonkeyPatch,
    lock: RunLockStub,
) -> list[dict[str, Any]]:
    """Replace the third service name ``app/cli.py`` binds at import.

    ``acquire_run_lock`` is bound on ``app.cli`` exactly as the two services
    are, so this is the same boundary :func:`install_services` works at: what
    the command asks for, and what it does with the answer.  The real
    function is reached by the rest of section K, which is where the lock
    file, the contention refusal and the live guard are asserted; this
    replacement is for the cases that turn on the *reason a release reports*.

    :param monkeypatch: pytest's patcher, for guaranteed restoration.
    :param lock: The claim to hand the command.
    :returns: The keywords of each acquisition, so a test can assert the
        command asks for this checkout's lock and passes nothing of its own.
    """
    calls: list[dict[str, Any]] = []

    def acquire(**kwargs: Any) -> tuple[RunLockStub, None]:
        """Stand in for :func:`app.services.acquire_run_lock`."""
        calls.append(kwargs)
        return lock, None

    monkeypatch.setattr(cli, "acquire_run_lock", acquire)
    return calls


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


def logged_path(path: Path, root: Path) -> str:
    """The spelling a log record names ``path`` by: relative to ``root``.

    Every path this command puts in a record goes through
    :func:`~app.logging_config.render_path`, and every record goes through the
    sanitizing formatter on the handlers ``configure_logging`` installs, so an
    absolute destination under the working directory reaches the console as the
    repository-relative identifier a reader acts on -
    ``target/cucumber.json``.  A CI console log is archived and shared, and the
    absolute location of the workspace a run executed in is disclosure rather
    than diagnosis (CWE-200/532).

    Computed here by plain relativization rather than by calling the rendering
    helper, so the assertion is independent of the code it is checking.

    :param path: The absolute path the command was given.
    :param root: The working directory the command ran in.
    :returns: The relative identifier the record carries.
    """
    return str(path.relative_to(root))


def as_logged(text: str, root: Path) -> str:
    """``text`` as a record shows it: the workspace prefix stripped.

    The counterpart of :func:`logged_path` for a message that *embeds* a path -
    a run service's infrastructure reason, say, which the service builds around
    an absolute directory and this command then emits whole.  The stripping is
    textual and deliberately duplicates none of the production rendering.

    :param text: The message as its producer built it.
    :param root: The working directory the command ran in.
    :returns: The text as the console receives it.
    """
    return text.replace(f"{root}{os.sep}", "")


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


def invalid_value_lines(stderr_text: str, flag: str) -> list[str]:
    """Every stderr line stating that ``flag`` was given an invalid value.

    Click prints a rejected option's reason itself, with the fixed prefix
    ``Error: Invalid value for '<flag>':`` and *no* sanitizing handler in
    between, so counting these lines is how a test tells one reason from a
    reason plus whatever the value managed to append to it.

    :param stderr_text: ``result.stderr``.
    :param flag: The option as it appears in Click's message, e.g.
        ``"--tags"``.
    :returns: The matching lines, in the order they were printed.
    """
    marker = f"Invalid value for '{flag}'"
    return [line for line in message_lines(stderr_text) if marker in line]


def parser_error_text(expression: str) -> str:
    """The tag grammar's own message for an expression it rejects.

    Derived from the pinned parser rather than pasted in, so a test asserting
    "the reason is one line whatever the parser produced" keeps asserting that
    if a future ``cucumber-tag-expressions`` rewords or re-wraps its
    diagnostics.

    :param expression: An expression the grammar must reject.
    :returns: ``str`` of the raised ``TagExpressionError``.
    :raises AssertionError: If the expression parses, which would make the
        calling test vacuous.
    """
    try:
        TagExpressionParser.parse(expression)
    except TagExpressionError as error:
        return str(error)
    raise AssertionError(f"{expression!r} parses, so it has no parser error")


def stderr_record(result: Result, marker: str) -> str:
    """The one stderr line carrying ``marker``, asserting there is just one.

    Several assertions in sections K and L are about *which* record says a
    thing rather than about whether stderr says it anywhere, and a substring
    search over the whole stream cannot tell those apart: it keeps passing
    when the wording it looks for has moved onto a different record, which is
    exactly the drift those two sections exist to catch.  So they read one
    record and assert about that record's own text.

    The level and logger prefix are left on the line deliberately.
    ``app/logging_config.py`` formats every record as
    ``"<level> <logger>: <message>"`` and installs no timestamp, so a
    record's line is stable and two invocations' records are comparable
    verbatim.

    :param result: The invocation's result.
    :param marker: Text the record carries, unique to it across stderr.
    :returns: That record's whole line, prefix included.
    """
    matches = [line for line in message_lines(result.stderr) if marker in line]
    assert len(matches) == 1, (
        f"expected exactly one record carrying {marker!r}, got {matches}"
    )
    return matches[0]


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


@pytest.mark.parametrize(
    "expression",
    (
        cli.DEFAULT_TAG_EXPRESSION,
        *VALID_TAG_EXPRESSIONS,
        "@" + "a" * (cli.TAG_EXPRESSION_LENGTH_LIMIT - 1),
    ),
)
def test_screening_leaves_every_accepted_expression_exactly_as_written(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    expression: str,
) -> None:
    """What the callback screens for, it screens *without* rewriting anything.

    The shipped default, the whole grammar and an expression of exactly
    ``TAG_EXPRESSION_LENGTH_LIMIT`` characters all pass, and each reaches the
    service character for character - not stripped, not normalised, not
    re-spelled.  The callback runs for the declared default too, so this is
    also what keeps ``@Smoke`` [CukesRunner.java:18] honest: a screen that
    rejected its own default would break every bare invocation.
    """
    run_suite = RunSuiteStub()
    install_services(
        monkeypatch, run_suite=run_suite, generate_reports=GenerateReportsStub()
    )

    result = invoke(runner, ["--tags", expression])

    assert result.exit_code == int(cli.ExitCode.SUCCESS)
    forwarded = run_suite.call["tags"]
    assert forwarded == expression
    assert len(forwarded) == len(expression)


@pytest.mark.parametrize(
    ("expression", "spelling", "unechoed"), CONTROL_BEARING_TAG_EXPRESSIONS
)
def test_a_control_bearing_tag_expression_is_refused_on_one_line(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    expression: str,
    spelling: str,
    unechoed: str,
) -> None:
    """A ``--tags`` value carrying a control character never reaches a console.

    Click prints a rejected option's reason itself, before
    ``configure_logging()`` has run and through none of this port's handlers,
    so nothing downstream can make the text safe: a newline inside it becomes
    a second physical line that a reader and a log scraper cannot tell from an
    independent record (CWE-117), and an ESC-bearing expression - which the
    grammar *accepts* - would be forwarded to every worker and echoed in the
    start record.  So the value is screened before it is parsed, and the
    refusal names the offending character by index and escape spelling while
    quoting **none** of the expression: the reason cannot carry the offence it
    reports.

    Asserted together with the usage-error row of the 0.4.1 exit table, since
    the callback runs during parsing: status ``2``, the service never called,
    no writer reached, nothing on disk and no start record.
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
    assert START_MESSAGE_MARKER not in result.stdout

    reasons = invalid_value_lines(result.stderr, "--tags")
    assert len(reasons) == 1, result.stderr
    assert spelling in reasons[0], reasons[0]
    assert "at index 2" in reasons[0], reasons[0]
    assert unechoed not in result.stderr, result.stderr
    assert "\x1b" not in result.stderr, result.stderr


def test_an_over_long_tag_expression_is_refused_by_the_numbers(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The length bound is enforced, stated as numbers, and not off by one.

    The grammar imposes no bound of its own - an expression of hundreds of
    kilobytes parses in a fraction of a second - and such a value would then
    be retained for the whole run, echoed in a record and repeated on every
    worker's command line (CWE-400).  ``TAG_EXPRESSION_LENGTH_LIMIT`` is that
    bound, and the refusal quotes the two *lengths* rather than the text, so
    the diagnostic cannot itself become the flood it refuses.

    One character over is refused and exactly the limit is accepted, in the
    same test, because a bound is only asserted by both of its sides.
    """
    limit = cli.TAG_EXPRESSION_LENGTH_LIMIT
    over_long = "@" + "a" * limit
    run_suite = RunSuiteStub()
    generate_reports = GenerateReportsStub()
    install_services(
        monkeypatch, run_suite=run_suite, generate_reports=generate_reports
    )

    refused = invoke(runner, ["--tags", over_long])

    assert refused.exit_code == int(cli.ExitCode.USAGE_ERROR)
    assert not run_suite.calls
    assert not generate_reports.called
    assert_no_artifacts(cli_root)
    reasons = invalid_value_lines(refused.stderr, "--tags")
    assert len(reasons) == 1, refused.stderr
    assert str(len(over_long)) in reasons[0], reasons[0]
    assert str(limit) in reasons[0], reasons[0]
    assert "a" * 40 not in refused.stderr, reasons[0]

    accepted = invoke(runner, ["--tags", over_long[:limit]])

    assert accepted.exit_code == int(cli.ExitCode.SUCCESS)
    assert run_suite.call["tags"] == over_long[:limit]


@pytest.mark.parametrize(
    ("expression", "raw_line_count"), CONTROL_FREE_MALFORMED_EXPRESSIONS
)
def test_a_parser_error_reaches_stderr_as_one_bounded_line(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    expression: str,
    raw_line_count: int,
) -> None:
    """The grammar's own multi-line message is rendered to a single line.

    Screening the *value* is not enough on its own: for an expression that is
    entirely printable and merely malformed, the text Click prints is the
    parser's, and the parser spans **three** physical lines - a sentence, the
    expression echoed back, and a caret marker under it.  Re-raised verbatim,
    lines two and three are indistinguishable from independent records, which
    is the same forging this port sanitizes log output against; here it is
    Click writing, so ``app/cli.py`` renders the message through
    ``sanitize_log_text`` before handing it over.

    The expectation is derived from the pinned parser rather than pasted, so
    what is asserted is the property - one line, bounded, control-safe - and
    not one library version's wording.  Only the message's *shape* is pinned
    by number, so the three-line case cannot become a one-line case without
    this test saying so.
    """
    raw = parser_error_text(expression)
    assert len(raw.splitlines()) == raw_line_count, raw
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

    reasons = invalid_value_lines(result.stderr, "--tags")
    assert len(reasons) == 1, result.stderr
    rendered = sanitize_log_text(raw, limit=cli._TAG_ERROR_MESSAGE_LIMIT)
    assert rendered in reasons[0], (reasons[0], rendered)
    assert "\n" not in rendered and "\r" not in rendered, rendered
    for continuation in raw.splitlines()[1:]:
        assert continuation not in message_lines(result.stderr), result.stderr


@pytest.mark.parametrize("value", FORGING_OPTION_VALUES)
def test_browser_accepts_the_hostile_value_tags_refuses(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    """The two forwarded values are screened in opposite ways, deliberately.

    One value, two options, two required outcomes.  ``--browser`` must accept
    it and hand it to the service byte-for-byte: ``Driver.java:29-42`` has no
    default branch, so AAP 0.4.1's "any other value fails at first driver use"
    depends on an unrecognised browser *reaching* the driver, and validating
    it here would replace that failure with a usage error the source never
    had.  ``--tags`` must refuse the same value, because a tag expression
    needs no control character and the grammar accepts an ESC inside one.

    The start record is still one physical line with no escape byte in it -
    the browser value is rendered by ``render_option_value`` and the record by
    the sanitizing formatter - so accepting the value costs the console
    nothing.
    """
    run_suite = RunSuiteStub()
    install_services(
        monkeypatch, run_suite=run_suite, generate_reports=GenerateReportsStub()
    )

    accepted = invoke(runner, ["--browser", value])

    assert accepted.exit_code == int(cli.ExitCode.SUCCESS)
    assert run_suite.call["browser"] == value
    starts = [
        line for line in message_lines(accepted.stdout)
        if START_MESSAGE_MARKER in line
    ]
    assert len(starts) == 1, accepted.stdout
    assert "\x1b" not in accepted.stdout, accepted.stdout
    assert "ERROR app.fake: forged" not in message_lines(accepted.stdout)

    refused = invoke(runner, ["--tags", value])

    assert refused.exit_code == int(cli.ExitCode.USAGE_ERROR)
    assert len(run_suite.calls) == 1
    assert len(invalid_value_lines(refused.stderr, "--tags")) == 1, refused.stderr
    assert "\x1b" not in refused.stderr, refused.stderr


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
    assert logged_path(json_path, cli_root) in result.stderr
    assert logged_path(rerun_path, cli_root) in result.stderr
    assert str(cli_root) not in result.stderr
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
        f"{WRITER_NAMES[2]} failed writing "
        f"{logged_path(failed_destination, cli_root)}"
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

    logged_reason = as_logged(reason, cli_root)
    naming = [
        line for line in message_lines(result.stderr) if logged_reason in line
    ]
    assert len(naming) == 1, message_lines(result.stderr)
    assert naming[0].startswith(f"ERROR {CLI_LOGGER_NAME}:")
    assert f"Exit {int(cli.ExitCode.ARTIFACT_FAILURE)}:" in naming[0]
    assert str(cli_root) not in result.stderr
    assert logged_reason not in result.stdout


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
    assert as_logged(reason, cli_root) in result.stderr
    assert str(cli_root) not in result.stderr


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
    assert as_logged(reason, cli_root) in result.stderr
    assert str(cli_root) not in result.stderr
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
    identifier = logged_path(destination, cli_root)
    exit_records = [
        line
        for line in message_lines(result.stderr)
        if line.startswith(f"ERROR {CLI_LOGGER_NAME}:")
        and f"Exit {int(cli.ExitCode.ARTIFACT_FAILURE)}:" in line
    ]
    assert len(exit_records) == 1, message_lines(result.stderr)
    assert (
        f"report writer {WRITER_NAMES[failing_index]} failed writing "
        f"{identifier}"
    ) in exit_records[0]
    # Both emitters name the artifact by its relative identifier, and neither
    # publishes the absolute location of the workspace the run executed in.
    assert identifier in cause_records[0], cause_records[0]
    assert str(cli_root) not in result.stderr

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
# "Unconditionally" includes the exits that never enter the command callback.
# The cleanup call sites both live on ``_RunTestsCommand``: ``main`` encloses
# a whole invocation, and ``make_context`` encloses the parsing that the
# console route and the Flask CLI group's dispatch both go through, so an
# unknown option and a malformed --tags expression - rejected while Click is
# still parsing - reach the same removal a completed run reaches.  Both
# routes are covered below, together with the two properties that make
# covering both safe: the removal is idempotent, so the console route
# reaching it twice does nothing the second time, and it never upgrades a
# status that is already non-zero, so a usage error still exits 2 instead of
# becoming an artifact failure behind a tidy-up.
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


@pytest.mark.parametrize(
    "arguments",
    [
        ["--nonesuch"],
        ["--tags", MALFORMED_TAG_EXPRESSIONS[0]],
    ],
    ids=["unknown-option", "malformed-tags"],
)
def test_a_parse_time_usage_error_still_removes_the_intermediates(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    arguments: list[str],
) -> None:
    """A value Click rejects while parsing reaches the cleanup all the same.

    The two rejections below never enter the command callback: Click refuses
    an unknown option and a failing option callback before it invokes one, so
    a ``finally`` written inside the callback would leave the intermediates
    behind.  ``_RunTestsCommand.make_context`` is where the removal actually
    sits, which is the boundary the console route and the Flask CLI group's
    dispatch share, and this is the case that distinguishes the two: the
    ``--rerun --tags`` conflict in the parametrized test above is raised
    *inside* the callback and so proves nothing about the parse boundary.

    Both halves of the removal are asserted, because ``_remove_intermediates``
    asks for both: this invocation's own directories, of which a run rejected
    while parsing has created none, and an **abandoned** run's leftovers,
    which is what the seeded worker document stands for.  The shared
    directory is pruned once nothing is left in it, so its absence is the
    observable form of "the publisher can find no intermediate JSON".

    The status stays :attr:`~app.cli.ExitCode.USAGE_ERROR` throughout: a
    cleanup that succeeded has nothing to report and cannot change a status,
    which is what keeps a usage error reported as a usage error.  The two
    command lines are the two rejections Click makes without a callback: an
    option it does not know, and an option callback that raised.
    """
    run_suite = RunSuiteStub()
    generate_reports = GenerateReportsStub()
    install_services(
        monkeypatch, run_suite=run_suite, generate_reports=generate_reports
    )
    workers = seed_worker_directory(cli_root)
    stale = paths.worker_result_path(1, base=cli_root)

    result = invoke(runner, arguments)

    assert result.exit_code == int(cli.ExitCode.USAGE_ERROR) == 2
    assert not run_suite.calls, "the suite ran despite a rejected command line"
    assert not generate_reports.called
    assert not stale.exists(), "an abandoned run's result document survived"
    assert not workers.exists(), "the per-worker directory survived the command"
    assert_no_artifacts(cli_root)


def test_a_failed_cleanup_does_not_upgrade_a_parse_time_usage_error(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A cleanup that fails reports itself and leaves the ``2`` alone.

    The one asymmetry in ``_RunTestsCommand`` is that a cleanup failure
    upgrades a *success* to :attr:`~app.cli.ExitCode.ARTIFACT_FAILURE` and
    leaves every other status untouched - overwriting a usage error's ``2``
    would hide the higher-priority failure behind a tidy-up.  The parse
    boundary is where that matters most, because the removal runs there
    before the command has a status at all, and this drives exactly that: the
    reclaim is made to fail, the command line is rejected while parsing, and
    the status is still the usage error.  Both records are asserted, because
    the parse route reaches the removal twice - once from ``make_context``
    with no status to reason about, and once from ``main`` with Click's ``2``
    - and neither may turn either value into a ``4``.

    The reclaim is replaced rather than a real failure arranged, because the
    reason a reclaim fails is the run service's business and what is asserted
    here is only what this command does with one.

    Read from the log records rather than from stderr, which is the one place
    in this module where those differ: ``configure_logging()`` is the first
    statement of the command callback, and a command line rejected while
    parsing never reaches it, so these records are emitted before this
    module's two-handler stream contract exists.  A real console run still
    shows them - with no handler installed, the standard library's own
    last-resort handler writes ``WARNING`` and above to stderr - but the
    routing this module asserts elsewhere is not what carries them here.
    """
    reason = (
        "1 intermediate path(s) could not be removed, so per-worker result "
        "documents remain in the workspace: could not remove a run directory"
    )
    monkeypatch.setattr(cli, "reclaim_workers_root", lambda **_kwargs: (reason, ()))
    # The command's logger reaches pytest's capture only while it propagates;
    # a previous test's ``configure_logging()`` turns that off, and
    # :fixture:`restore_app_logging` puts it back at teardown.
    logging.getLogger(PACKAGE_LOGGER_NAME).propagate = True
    run_suite = RunSuiteStub()
    install_services(
        monkeypatch, run_suite=run_suite, generate_reports=GenerateReportsStub()
    )

    with caplog.at_level(logging.ERROR, logger=CLI_LOGGER_NAME):
        result = invoke(runner, ["--nonesuch"])

    assert result.exit_code == int(cli.ExitCode.USAGE_ERROR) == 2
    assert not run_suite.calls, "the suite ran despite a rejected command line"
    assert caplog.text.count(reason) == 2, (
        "the cleanup failure was not reported at both parse-route call sites"
    )
    assert "the status stays unset" in caplog.text, (
        "the removal invented a status where the command had none"
    )
    assert f"the status stays {int(cli.ExitCode.USAGE_ERROR)}" in caplog.text, (
        "the removal did not say which status it left in place"
    )
    assert f"Exit {int(cli.ExitCode.ARTIFACT_FAILURE)}" not in caplog.text, (
        "a tidy-up failure was announced as this run's exit class"
    )


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
    run and must find the stale intermediate already gone, not removed
    afterwards by the ``finally``.

    What the directory itself holds during a run is this invocation's run
    lock, which the clean step is not allowed to delete - it is what makes
    this run's use of the build output exclusive - so the directory is present
    while the run is in progress and holds nothing but that lock.  It is gone
    again by the time the command returns, because releasing the lock removes
    the file and prunes the directory.
    """
    workers = seed_worker_directory(cli_root)
    stale = paths.worker_result_path(1, base=cli_root)
    run_suite = RunSuiteStub(
        observe=lambda: sorted(entry.name for entry in workers.iterdir())
    )
    install_services(
        monkeypatch, run_suite=run_suite, generate_reports=GenerateReportsStub()
    )

    result = invoke(runner, ["--clean"])

    assert result.exit_code == int(cli.ExitCode.SUCCESS)
    assert run_suite.observations == [[RUN_LOCK_NAME]]
    assert not stale.exists()
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
# app/cli.py requirement 11, in two halves.  The first is the documented
# behaviour of the two flags: --clean empties the build output before the run
# and --no-clean touches nothing.
#
# The second is that this is the only code in the port that deletes something
# the user did not name, so it is **fail-closed and follows nothing**.  A
# symlink at the build output root is refused rather than traversed, because
# emptying it would delete whatever it points at; a symlink *entry* inside the
# build output is removed as the link it is, so its target survives; and an
# entry that cannot be removed leaves the command at
# ExitCode.ARTIFACT_FAILURE with the suite not started, because output whose
# state cannot be established is an artifact-infrastructure failure and not a
# cosmetic one.  Section G2 covers the same refusal for the indirection a link
# test cannot see.
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

    The one entry an emptied build output holds while a run is in progress is
    the intermediate directory carrying this invocation's run lock: the clean
    step hands that directory to the run service rather than deleting it, and
    the service keeps the lock file because it is what makes this run's use of
    the build output exclusive.  Releasing the lock removes both, which is why
    the build output is observably empty once the command has returned.
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
    assert run_suite.observations == [[paths.workers_dir(cli_root).name]]
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


def outside_tree(root: Path) -> dict[Path, bytes]:
    """Build a small directory tree *outside* the build output, with contents.

    Stands for whatever a link or a junction in the build output points at -
    someone's home directory, a sibling checkout, a mounted volume - so that
    "nothing outside was deleted" is an assertion about real bytes rather than
    about the absence of an error.  It is deliberately not written through
    ``app.utils.paths``: these files are not artifacts, and the point of the
    tree is that the port has no business touching it.

    :param root: The checkout root the tree is created beside, so it stays
        inside the test's own temporary directory.
    :returns: Each file mapped to the bytes written there, in the shape
        :func:`assert_decoys_unchanged` reads.
    """
    outside = root / "outside-the-build-output"
    nested = outside / "nested"
    nested.mkdir(parents=True)
    contents: dict[Path, bytes] = {}
    for path in (outside / "victim.txt", nested / "deep.txt"):
        payload = f"outside-{path.name}\n".encode()
        path.write_bytes(payload)
        contents[path] = payload
    return contents


def _rmtree_refusing(name: str, error: OSError) -> Callable[..., None]:
    """A :func:`shutil.rmtree` that refuses one entry and removes the rest.

    The deterministic stand-in for an entry the clean step cannot remove.  A
    read-only parent directory is the natural way to arrange that, and it is
    not used: this suite's own effective user may be - and in this
    environment is - ``root``, for whom the mode bits do not prevent the
    removal at all, so the case would pass while asserting nothing.  Refusing
    one named entry at the removal itself reproduces exactly what the clean
    step sees from a denied removal, on every platform and under every user.

    :param name: The final path component to refuse.  Both call shapes are
        covered: the descriptor strategy passes a bare entry name relative to
        an open directory, and the path fallback passes a whole path.
    :param error: The exception to raise for that entry, standing for what
        the operating system would have raised.
    :returns: A callable with :func:`shutil.rmtree`'s own signature, which
        delegates to the real function for every other entry.
    """
    real_rmtree = shutil.rmtree

    def rmtree(path: Any, *args: Any, **kwargs: Any) -> None:
        if Path(path).name == name:
            raise error
        real_rmtree(path, *args, **kwargs)

    return rmtree


def test_a_symlinked_build_output_root_is_refused_and_nothing_follows_it(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A build output that is a symlink stops the command, deleting nothing.

    ``--clean`` is the only step in the port that deletes something the user
    did not name, and a symlink at the build output root is the shortest route
    out of the checkout: ``is_dir()`` is true for a link to a directory, so a
    step that tested only that would empty whatever the link points at.  The
    command therefore refuses, and every consequence of refusing is asserted
    here - the status is :attr:`~app.cli.ExitCode.ARTIFACT_FAILURE`, the suite
    is never started, no writer is reached, and every byte under the link
    target is still there afterwards, including a nested directory a recursive
    delete would have taken with it.

    **Which layer refuses is worth stating**, because the message names it: by
    the time the clean step is reached this run already holds its claim on the
    build output, and taking that claim means creating the intermediate
    directory *inside* the build output - which the path layer refuses to
    create through a linked component.  So the refusal arrives from the lock
    rather than from the clean, one step earlier and with the same outcome,
    and it names the link.  The clean step's own refusal, in its own words, is
    asserted directly by the test below, which is the only way to reach it
    once nothing upstream of it will follow a link either.
    """
    contents = outside_tree(cli_root)
    outside = cli_root / "outside-the-build-output"
    paths.target_root(cli_root).symlink_to(outside, target_is_directory=True)
    run_suite = RunSuiteStub()
    generate_reports = GenerateReportsStub()
    install_services(
        monkeypatch, run_suite=run_suite, generate_reports=generate_reports
    )

    result = invoke(runner, ["--clean"])

    assert result.exit_code == int(cli.ExitCode.ARTIFACT_FAILURE) == 4
    assert not run_suite.calls, "the suite started over an unknown build output"
    assert not generate_reports.called
    assert_no_artifacts(cli_root)
    assert_decoys_unchanged(contents)
    assert sorted(entry.name for entry in outside.iterdir()) == [
        "nested",
        "victim.txt",
    ], "the link target gained or lost an entry"
    assert paths.target_root(cli_root).is_symlink(), "the link itself was removed"
    assert "is a symbolic link" in result.stderr


def test_the_clean_step_refuses_a_symlinked_root_in_its_own_words(
    cli_root: Path,
) -> None:
    """``_empty_build_output`` names the link and removes nothing.

    Driven directly, because the command can no longer reach this refusal:
    the run lock is taken first and the path layer will not create this run's
    intermediate directory through a linked build output either, so the
    end-to-end case above is refused one step earlier.  The clean step's own
    fail-closed behaviour is still the contract - it is what protects a
    ``--no-clean`` run that later cleans, and a build output relinked between
    two runs - so it is asserted where it lives.

    Three things are pinned: a reason is returned rather than an exception
    raised, the reason says what the path is and that nothing was removed, and
    it names ``--no-clean`` as the way to run in a checkout laid out this way.
    The link itself survives too: deleting a link the user placed is as
    presumptuous as deleting what it points at.
    """
    contents = outside_tree(cli_root)
    outside = cli_root / "outside-the-build-output"
    root = paths.target_root(cli_root)
    root.symlink_to(outside, target_is_directory=True)

    reason = cli._empty_build_output()

    assert reason is not None, "a symlinked build output was accepted"
    assert "is a symbolic link" in reason
    assert "nothing was removed" in reason
    assert "--no-clean" in reason
    assert_decoys_unchanged(contents)
    assert root.is_symlink(), "the link itself was removed"


def test_an_entry_the_clean_cannot_remove_stops_the_run_at_exit_four(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A removal that fails is an artifact failure, not a warning.

    The clean step's whole postcondition is that the build output is empty
    afterwards, so an entry that could not be removed means the state of what
    the publisher will read is unknown: stale report pages and stale worker
    intermediates sit where this run's writers may not overwrite them.  The
    command therefore does not start the suite, reaches no writer, and exits
    :attr:`~app.cli.ExitCode.ARTIFACT_FAILURE`.

    Both records the step owes an operator are asserted, because one without
    the other is not actionable: the *failure* names the entry it could not
    remove and the reason the operating system gave, and the *verification*
    names what is still there - two separate statements, since a removal that
    reported success and left the entry behind is only visible from the
    second.
    """
    root = paths.ensure_dir(paths.target_root(cli_root))
    doomed = root / "unremovable"
    doomed.mkdir()
    held = doomed / "report.html"
    held.write_bytes(b"stale\n")
    removable = root / "removable.json"
    removable.write_bytes(b"stale\n")
    monkeypatch.setattr(
        cli.shutil,
        "rmtree",
        _rmtree_refusing(doomed.name, PermissionError(13, "Permission denied")),
    )
    run_suite = RunSuiteStub()
    generate_reports = GenerateReportsStub()
    install_services(
        monkeypatch, run_suite=run_suite, generate_reports=generate_reports
    )

    result = invoke(runner, ["--clean"])

    assert result.exit_code == int(cli.ExitCode.ARTIFACT_FAILURE) == 4
    assert not run_suite.calls, (
        "the suite started over a build output that is not clean"
    )
    assert not generate_reports.called
    assert_no_artifacts(cli_root)
    assert held.read_bytes() == b"stale\n", (
        "the entry that could not be removed changed"
    )
    assert not removable.exists(), "one refused entry stopped the other removals"
    assert f"could not remove {logged_path(doomed, cli_root)}" in result.stderr
    assert "Permission denied" in result.stderr
    assert f"survived the clean: {doomed.name}" in result.stderr
    assert "the suite was not started" in result.stderr


@pytest.mark.parametrize(
    "descriptor_cleaning",
    [None, False],
    ids=["as-configured", "path-fallback"],
)
def test_a_symlink_entry_is_unlinked_and_its_target_survives(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    descriptor_cleaning: bool | None,
) -> None:
    """A link inside the build output goes; what it points at stays.

    The asymmetry with the root case is deliberate and is the whole of this
    test: a link *at* the root is refused because emptying it would mean
    emptying its target, while a link *inside* the build output is an entry of
    a directory this step is emptying, so it is removed as the link it is -
    one ``unlink``, which never touches the target - and the run proceeds to
    success.

    Both removal strategies are driven, because the platform decides which one
    runs and each recognises a link by a different call: the descriptor
    strategy asks ``is_dir(follow_symlinks=False)`` about a directory entry,
    and the path fallback ``lstat``s the entry itself.  A link to a directory
    is what tells them apart from a naive test, so it is what is planted here.
    The parametrized value is the strategy: ``None`` leaves the platform's own
    choice in place, and ``False`` forces the path fallback, which is how the
    Windows strategy is exercised on a POSIX host.
    """
    contents = outside_tree(cli_root)
    outside = cli_root / "outside-the-build-output"
    root = paths.ensure_dir(paths.target_root(cli_root))
    link = root / "linked-away"
    link.symlink_to(outside, target_is_directory=True)
    stale = root / "stale.json"
    stale.write_bytes(b"stale\n")
    if descriptor_cleaning is not None:
        monkeypatch.setattr(
            cli, "_SUPPORTS_DESCRIPTOR_CLEANING", descriptor_cleaning
        )
    run_suite = RunSuiteStub()
    install_services(
        monkeypatch, run_suite=run_suite, generate_reports=GenerateReportsStub()
    )

    result = invoke(runner, ["--clean"])

    assert result.exit_code == int(cli.ExitCode.SUCCESS)
    assert len(run_suite.calls) == 1, "the clean step stopped a run it should not have"
    assert not link.is_symlink(), "the link entry survived the clean"
    assert not stale.exists()
    assert_decoys_unchanged(contents)
    assert outside.is_dir(), "the link target was removed with the link"


# --------------------------------------------------------------------------- #
# Section G2 - the clean step follows no reparse point either
#
# The second hostile direction of app/cli.py requirement 11, and the one a
# link test cannot see.  A Windows junction is reported by os.lstat as a
# DIRECTORY, with the link bit clear and with its own device and inode, so it
# satisfies every identity check the step makes while iterdir and
# shutil.rmtree resolve straight through it - and Path.resolve() resolves
# through it too, so a containment test taken against the resolved root finds
# the external tree's own children "inside" the build output and deletes them.
# A mounted volume and a cloud-storage placeholder are the same shape.
#
# No junction can be created on this host, and none is needed: what the step
# reads is a stat result, so the tests below hand it a real stat result
# carrying the two Windows-only fields a reparse point sets.  That makes the
# refusal assertable on any platform, which is the point - AAP 0.8 lists
# Windows as supported, and the path-based fallback these cases drive is the
# strategy that runs there.
# --------------------------------------------------------------------------- #


class _ReparsePointStat:
    """A stat result shaped exactly like a Windows junction's.

    Everything a junction shares with an ordinary directory is copied from a
    real :func:`os.lstat` result - the mode with its directory bit set and its
    link bit clear, the device and the inode - so that a test using this
    cannot pass merely because some *other* check rejected the path.  What is
    added is the pair of fields :class:`os.stat_result` carries for reparse
    points on Windows and nowhere else, which is what
    ``app/cli.py`` reads through :func:`getattr` so that its refusal is one
    code path on every platform.

    :param real: The genuine ``lstat`` result of the directory standing in for
        the junction.
    :param tag: The reparse tag to report.  The default is the mount-point tag
        a junction carries; any non-zero value is refused the same way,
        because a deletion resolved through *any* reparse point cannot be
        bound to the object that was checked.
    """

    def __init__(self, real: os.stat_result, *, tag: int = 0xA0000003) -> None:
        self.st_mode = real.st_mode
        self.st_dev = real.st_dev
        self.st_ino = real.st_ino
        self.st_file_attributes = stat.FILE_ATTRIBUTE_REPARSE_POINT
        self.st_reparse_tag = tag


def _report_as_reparse_point(
    monkeypatch: pytest.MonkeyPatch, junction: Path
) -> None:
    """Make :func:`os.lstat` report one path as a reparse point.

    Every other path keeps its real answer, including the descriptor-based
    calls the clean step and the run service make, so the only thing that
    changes about the run is what the operating system says about that one
    entry.

    :param monkeypatch: pytest's patcher, for guaranteed restoration.
    :param junction: The path to report as a reparse point.
    """
    real_lstat = os.lstat

    def lstat(path: Any, *args: Any, **kwargs: Any) -> Any:
        info = real_lstat(path, *args, **kwargs)
        if not isinstance(path, int) and Path(path) == junction:
            return _ReparsePointStat(info)
        return info

    monkeypatch.setattr(os, "lstat", lstat)


def test_a_reparse_point_at_the_build_output_root_is_refused(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A junction where the build output should be stops the command.

    This is the escape a symlink test does not close, and closing it is what
    this asserts end to end: the root reports as a plain directory with its
    own device and inode, so it passes every identity check, and the step
    refuses it anyway on the strength of its reparse fields.  Nothing inside
    is removed - the decoy would be the *external* tree's own child in a real
    junction, which is what a recursive delete would have taken - the suite is
    never started, no writer is reached, and the status is
    :attr:`~app.cli.ExitCode.ARTIFACT_FAILURE`.

    The reason names what the path is rather than reporting a changed inode,
    which is the difference between a diagnostic an operator can act on and
    one that describes the wrong hazard.
    """
    root = paths.ensure_dir(paths.target_root(cli_root))
    decoy = root / "outside-child.txt"
    decoy.write_bytes(b"a child of whatever the junction points at\n")
    _report_as_reparse_point(monkeypatch, root)
    run_suite = RunSuiteStub()
    generate_reports = GenerateReportsStub()
    install_services(
        monkeypatch, run_suite=run_suite, generate_reports=generate_reports
    )

    result = invoke(runner, ["--clean"])

    assert result.exit_code == int(cli.ExitCode.ARTIFACT_FAILURE) == 4
    assert not run_suite.calls, "the suite started over an unidentifiable build output"
    assert not generate_reports.called
    assert_no_artifacts(cli_root)
    assert decoy.read_bytes() == b"a child of whatever the junction points at\n"
    assert "reparse point" in result.stderr
    assert "junction" in result.stderr
    assert "nothing was removed" in result.stderr
    assert "the suite was not started" in result.stderr


def test_a_reparse_point_entry_is_refused_and_its_siblings_are_not(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A junction *inside* the build output is neither followed nor removed.

    The path-based strategy is forced, because it is the one that can reach
    this case: it is what runs on Windows, where reparse points exist and
    where no descriptor-relative deletion is available, and ``rmtree`` there
    resolves the entry's name and would recurse into the tree the junction
    redirects to.  Unlinking the junction itself is refused too - that is a
    deletion this step cannot bind to an object it verified - so the entry
    survives and is reported as a survivor.

    The other entries are still removed, which is the half that keeps the
    refusal proportionate: one entry the step cannot identify stops the run,
    and does not stop the clean from doing everything else it can.
    """
    root = paths.ensure_dir(paths.target_root(cli_root))
    junction = root / "junction-out-of-tree"
    junction.mkdir()
    external = junction / "external-child.txt"
    external.write_bytes(b"a file in the tree the junction points at\n")
    sibling = root / "cucumber-reports.html"
    sibling.write_bytes(b"stale\n")
    monkeypatch.setattr(cli, "_SUPPORTS_DESCRIPTOR_CLEANING", False)
    _report_as_reparse_point(monkeypatch, junction)
    run_suite = RunSuiteStub()
    generate_reports = GenerateReportsStub()
    install_services(
        monkeypatch, run_suite=run_suite, generate_reports=generate_reports
    )

    result = invoke(runner, ["--clean"])

    assert result.exit_code == int(cli.ExitCode.ARTIFACT_FAILURE) == 4
    assert not run_suite.calls, (
        "the suite started over a build output holding a junction"
    )
    assert not generate_reports.called
    assert junction.is_dir(), "the junction itself was removed"
    assert external.read_bytes() == b"a file in the tree the junction points at\n"
    assert not sibling.exists(), "one refused entry stopped the other removals"
    assert f"{logged_path(junction, cli_root)} is a reparse point" in result.stderr
    assert "neither followed nor removed" in result.stderr
    assert f"survived the clean: {junction.name}" in result.stderr


def test_a_root_that_becomes_a_reparse_point_mid_walk_is_refused(
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_root_identity_problem`` refuses a root replaced while it is emptied.

    The path-based strategy cannot hold the directory open, so it re-checks
    the root's identity before every removal; this is that re-check, driven
    directly with the root already verified and then re-pointed underneath it.
    Without the reparse test the re-check would compare device and inode,
    find them unchanged - a junction carries its own - and let the rest of the
    walk resolve through it.

    The clean answer is asserted in the same test, because "returns a reason"
    is only meaningful beside "returns none when nothing changed": a re-check
    that reported a problem every time would fail closed for the wrong
    reason and stop every clean on the platform that needs it.
    """
    root = paths.ensure_dir(paths.target_root(cli_root))
    verified = os.lstat(root)

    assert cli._root_identity_problem(root, verified) is None, (
        "an unchanged build output root was reported as replaced"
    )

    _report_as_reparse_point(monkeypatch, root)
    problem = cli._root_identity_problem(root, verified)

    assert problem is not None, "a root that became a reparse point was accepted"
    assert "no longer a plain directory" in problem
    assert "nothing further was removed" in problem


def test_every_indirection_is_recognised_for_what_it_is(cli_root: Path) -> None:
    """The two predicates behind the refusals, one case per kind.

    ``_indirection_problem`` is applied at the root, on every re-check and,
    through ``_entry_is_reparse_point``, to every entry, so the whole clean
    step rests on these three answers.  Each is pinned here at the level the
    functions work at, which is the only place the *plain directory* case can
    be asserted at all - a run over an ordinary build output proves the
    negative case only by succeeding, and says nothing about which test let it
    through.

    The symlink answer is the one worth reading twice: a link is a problem for
    ``_indirection_problem`` and **not** a reparse point for the predicate,
    because on POSIX it is not one and the two facts have different
    consequences - a link entry is unlinked, a reparse-point entry is refused.
    """
    root = paths.ensure_dir(paths.target_root(cli_root))
    plain = os.lstat(root)
    link = cli_root / "linked-build-output"
    link.symlink_to(root, target_is_directory=True)
    link_status = os.lstat(link)
    junction_status = _ReparsePointStat(plain)

    assert cli._indirection_problem(root, plain) is None, (
        "a plain directory was refused"
    )
    assert cli._entry_is_reparse_point(plain) is False

    symlink_problem = cli._indirection_problem(link, link_status)
    assert symlink_problem is not None, "a symbolic link was accepted"
    assert "is a symbolic link" in symlink_problem
    assert cli._entry_is_reparse_point(link_status) is False, (
        "a symbolic link was reported as a reparse point"
    )

    junction_problem = cli._indirection_problem(root, junction_status)
    assert junction_problem is not None, "a reparse point was accepted"
    assert "is a reparse point (a junction or a mounted volume)" in junction_problem
    assert cli._entry_is_reparse_point(junction_status) is True


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
    # Emptied, except for the intermediate directory holding this run's lock -
    # see ``test_clean_empties_the_build_output_before_the_run`` for why that
    # entry is the one thing a clean leaves while a run is in progress.
    assert run_suite.observations == [[paths.workers_dir(cli_root).name]], (
        "the clean step did not run before the run"
    )
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
# Section H, continued - what a tolerated problem is allowed to put on stderr
#
# The records section H counts are built out of text the command did not
# author: a problem on ``RunOutcome.parse_errors`` is composed by the engine's
# parser, by the rerun manifest reader or by a worker's diagnostics, and any of
# those can carry control characters or arbitrary length.  Section H settles
# *which* stream a record reaches; the tests below settle what one record is
# allowed to **be** - exactly one physical line, with no control character and
# no terminal escape sequence left in it, bounded, and otherwise character for
# character what the producer said.
#
# Why that is a contract and not a detail.  The summary record promises how
# many records follow it, so a problem containing ``CR`` or ``LF`` would make
# the command's own account of the run untrue and would let a producer forge a
# record - ``ERROR app.cli: Exit 3: ...`` on a line of its own - that no
# emitter wrote (CWE-117).  An unbounded problem, equally, is an unbounded
# record that buries the rest of a CI log (CWE-400).  Both are rendered away by
# ``app/cli.py``'s ``_render_selection_problem``, which is why every assertion
# here counts records rather than merely finding a substring among them.
# --------------------------------------------------------------------------- #

#: The bound ``app/cli.py`` applies to one rendered problem, read from the
#: module rather than restated: the value is that file's documented choice, and
#: a copy of the number here would quietly stop exercising the real one.
SELECTION_PROBLEM_LIMIT: Final[int] = cli._SELECTION_PROBLEM_LIMIT

#: How the summary record names the count, so the assertion is on the wording
#: the exit contract publishes rather than on a fragment of it.
SELECTION_SUMMARY_TEMPLATE: Final[str] = (
    "{count} problem(s) were reported during selection"
)

#: The prefix every per-problem record carries, and the separator the helper
#: below splits a record on to recover exactly what was rendered.
TOLERATED_RECORD_PREFIX: Final[str] = "Tolerated: "

#: A record a hostile problem would forge if a line break survived rendering:
#: the command's own logger name, its own level and a dead-worker line that no
#: run reported.  Built from :data:`CLI_LOGGER_NAME` so it is the shape this
#: command really emits rather than a lookalike.
FORGED_RECORD: Final[str] = f"ERROR {CLI_LOGGER_NAME}: Exit 3: every shard died"

#: One problem carrying a full ``CRLF`` and the forged record behind it.
LINE_BREAK_PROBLEM: Final[str] = f"a parse failure\r\n{FORGED_RECORD}"


def run_with_problems(
    runner: CliRunner,
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    document: Any,
    problems: tuple[str, ...],
) -> Result:
    """Invoke a run whose only diagnostics are the tolerated problems given.

    Both services are stand-ins, so nothing is written and stderr carries the
    selection records and nothing else - which is what makes an exact record
    count a meaningful assertion instead of a search among unrelated lines.

    :param runner: The Click runner.
    :param root: The temporary checkout root the reported artifacts resolve
        against.
    :param monkeypatch: pytest's patcher, for the two service names.
    :param document: The merged document the run reports.
    :param problems: What the run tolerated, in the order the outcome carries
        them.
    :returns: Click's result object.
    """
    install_services(
        monkeypatch,
        run_suite=RunSuiteStub(
            make_run_outcome(document, selected_count=5, parse_errors=problems)
        ),
        generate_reports=GenerateReportsStub(ok_report(*artifact_paths(root))),
    )
    return invoke(runner)


def tolerated_records(stream_text: str) -> list[str]:
    """What each per-problem record rendered, the summary line excluded.

    :param stream_text: ``result.stderr``.
    :returns: The text of every record from its ``Tolerated: `` prefix
        onwards, in emission order.
    """
    return [
        line.split(TOLERATED_RECORD_PREFIX, 1)[1]
        for line in message_lines(stream_text)
        if TOLERATED_RECORD_PREFIX in line
    ]


@pytest.mark.parametrize(
    "problem",
    [
        "features/Broken.feature: cannot be parsed",
        "features/Broken.feature:3: Parser failure: expected Given",
        "the rerun manifest could not be read: ENOENT",
    ],
)
def test_an_ordinary_problem_is_reported_character_for_character(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_result_set: Any,
    problem: str,
) -> None:
    """Rendering a problem safely must not change a problem that already is.

    The whole worth of these records is that they say what the run tolerated,
    so the three shapes a real producer emits - a parse failure, a parse
    failure with its source position, a manifest reason with its errno by
    symbol - arrive as the producer wrote them: one record, ending in the
    problem itself with nothing escaped, added or clipped.
    """
    result = run_with_problems(
        runner, cli_root, monkeypatch, sample_result_set, (problem,)
    )

    assert result.exit_code == int(cli.ExitCode.SUCCESS)
    lines = message_lines(result.stderr)
    assert len(lines) == 2, lines
    assert lines[1].startswith(STDERR_LEVEL_PREFIXES)
    assert lines[1].endswith(TOLERATED_RECORD_PREFIX + problem)
    assert tolerated_records(result.stderr) == [problem]
    assert problem not in result.stdout


def test_a_problem_carrying_line_breaks_is_still_exactly_one_record(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_result_set: Any,
) -> None:
    """A ``CRLF`` inside a problem cannot turn one record into two.

    The summary record says how many problems follow, and a producer able to
    end a record early could both contradict that count and forge a line the
    command never emitted - here a dead-worker report at ``ERROR`` under this
    command's own logger name.  What must reach stderr instead is a single
    record in which the break is printable text, with the forged content
    demoted to part of the diagnostic rather than a record of its own.
    """
    result = run_with_problems(
        runner, cli_root, monkeypatch, sample_result_set, (LINE_BREAK_PROBLEM,)
    )

    assert result.exit_code == int(cli.ExitCode.SUCCESS)
    lines = message_lines(result.stderr)
    assert len(lines) == 2, lines
    assert result.stderr.count("\n") == 2
    assert "\r" not in result.stderr
    assert FORGED_RECORD not in lines
    assert lines[1].startswith(STDERR_LEVEL_PREFIXES)
    assert tolerated_records(result.stderr) == [
        f"a parse failure\\r\\n{FORGED_RECORD}"
    ]


#: One case per shape an escape sequence takes in practice, with what each
#: must render to: a colour sequence and a screen-erase sequence are
#: instructions to a terminal and are dropped while their text survives, a
#: window-title sequence is dropped whole, and a lone ``ESC`` carries no
#: instruction at all and is spelled printably.
_ESCAPE_CASES: Final[tuple[tuple[str, str, str], ...]] = (
    (
        "colour",
        "a parse failure \x1b[31min red\x1b[0m",
        "a parse failure in red",
    ),
    (
        "erase-screen",
        "a parse failure \x1b[2Jcleared",
        "a parse failure cleared",
    ),
    (
        "window-title",
        "a parse failure \x1b]0;retitled\x07 here",
        "a parse failure  here",
    ),
    ("lone-escape", "a parse failure \x1b", "a parse failure \\x1b"),
)


@pytest.mark.parametrize(
    ("case_id", "problem", "expected"),
    _ESCAPE_CASES,
    ids=[case[0] for case in _ESCAPE_CASES],
)
def test_no_escape_character_from_a_problem_reaches_stderr(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_result_set: Any,
    case_id: str,
    problem: str,
    expected: str,
) -> None:
    """No ``ESC`` byte survives a problem, whatever it was part of.

    A CI console is a terminal often enough that this matters: a sequence
    inside a diagnostic can recolour, retitle or erase what has already been
    printed, so an operator reads a log that no longer says what the run said.
    Each case asserts both halves of the rendering - that not one ``ESC``
    byte reaches the stream, and that the human-readable text around it is
    still there to read.
    """
    result = run_with_problems(
        runner, cli_root, monkeypatch, sample_result_set, (problem,)
    )

    assert result.exit_code == int(cli.ExitCode.SUCCESS), case_id
    assert "\x1b" not in result.stderr
    assert "\x1b" not in result.stdout
    assert len(message_lines(result.stderr)) == 2
    assert tolerated_records(result.stderr) == [expected]


def test_a_problem_longer_than_the_bound_is_truncated_and_says_so(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_result_set: Any,
) -> None:
    """An oversized problem costs one bounded record that admits the clipping.

    A producer handing over a megabyte of text must not be able to bury the
    rest of a run's log in one record, and a reader must be able to tell a
    clipped diagnostic from one that merely ended - so the record keeps the
    first :data:`SELECTION_PROBLEM_LIMIT` characters and then names the exact
    number dropped.  The overflow here is an arbitrary non-round number so
    the count in the notice cannot match by coincidence.

    The filler is ordinary diagnostic prose rather than one long opaque run
    of characters, because the two rules are separate and this case is about
    the bound: an unbroken alphanumeric blob is credential-shaped on its own
    terms, so the handler's sanitizer masks it before the record is written,
    and a masked record would prove the redaction rule instead of this one.
    """
    overflow = 137
    problem = ("unexpected token in feature file, " * 200)[
        : SELECTION_PROBLEM_LIMIT + overflow
    ]
    assert len(problem) == SELECTION_PROBLEM_LIMIT + overflow

    result = run_with_problems(
        runner, cli_root, monkeypatch, sample_result_set, (problem,)
    )

    assert result.exit_code == int(cli.ExitCode.SUCCESS)
    assert len(message_lines(result.stderr)) == 2
    records = tolerated_records(result.stderr)
    assert records == [
        problem[:SELECTION_PROBLEM_LIMIT]
        + TRUNCATION_SUFFIX_TEMPLATE.format(dropped=overflow)
    ]
    assert len(records[0]) < len(problem)
    assert problem not in result.stderr


def test_every_problem_gets_one_record_beside_the_summary(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_result_set: Any,
) -> None:
    """The stderr record count is the problem count plus the summary, exactly.

    The four hostile shapes together: an ordinary diagnostic, one carrying a
    ``CRLF`` with a forged record behind it, one carrying an escape sequence
    and one past the bound.  One record each, the summary naming four, every
    line carrying a stderr level prefix - so no fragment of any problem
    reached the stream as a record of its own - and the status still ``0``,
    because a tolerated problem is not an exit class.
    """
    problems = (
        "features/A.feature:3: Parser failure: expected Given",
        LINE_BREAK_PROBLEM,
        "a parse failure \x1b[2Jcleared",
        "z" * (SELECTION_PROBLEM_LIMIT + 10),
    )

    result = run_with_problems(
        runner, cli_root, monkeypatch, sample_result_set, problems
    )

    assert result.exit_code == int(cli.ExitCode.SUCCESS)
    lines = message_lines(result.stderr)
    assert len(lines) == len(problems) + 1, lines
    assert SELECTION_SUMMARY_TEMPLATE.format(count=len(problems)) in lines[0]
    assert len(tolerated_records(result.stderr)) == len(problems)
    assert all(line.startswith(STDERR_LEVEL_PREFIXES) for line in lines), lines
    assert result.stderr.count("\n") == len(problems) + 1
    assert "\r" not in result.stderr
    assert "\x1b" not in result.stderr
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
    situation where a CI log is the only evidence available.  Both shapes are
    parametrized: a stream with no ``reconfigure`` attribute at all, and one
    whose ``reconfigure`` raises.
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
        assert logged_path(path, cli_root) in out.getvalue()


# --------------------------------------------------------------------------- #
# Section H3 - the global record sanitizer
#
# The other half of requirement 12, and the half no call site can be trusted
# to apply for itself.  This command's records quote text nobody rendered: a
# tolerated selection problem carries a parser's own message, an
# infrastructure reason carries an absolute directory and an operating
# system's error, a writer failure carries an exception and its traceback.
# ``configure_logging`` therefore installs one
# ``SanitizingFormatter`` on every handler it creates, and what the four tests
# below assert is that boundary rather than any one caller's diligence: a
# record reaching a console is relativized, single-line, redacted and bounded,
# a traceback is marked line by line so none of it can pass for a record, and
# the record object itself is handed on unchanged so a capture handler still
# sees what was logged.
#
# The command is the natural place for these: it is the process entry point
# that installs the configuration, and the records it emits are the ones a CI
# console is read from.
# --------------------------------------------------------------------------- #


def configured_logger(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[logging.Logger, PlainStream, PlainStream]:
    """Configure logging exactly as the command does, over captured streams.

    :param monkeypatch: pytest's patcher, which restores both streams.
    :returns: A logger inside the ``app`` hierarchy, and the stdout and stderr
        stand-ins its handlers resolve at emit time.
    """
    out = PlainStream()
    err = PlainStream()
    install_recording_streams(monkeypatch, out, err)
    configure_logging()
    return logging.getLogger(f"{PACKAGE_LOGGER_NAME}.sanitizer_probe"), out, err


def test_every_installed_handler_renders_through_the_sanitizer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One sanitizer, on every handler, in both configurations.

    The protection is the *boundary*, so it cannot be attached to some
    handlers and not others: a record routed to stderr must be rendered by the
    same code as one routed to stdout, and the merged single-handler
    configuration must be covered as well.  One shared instance is asserted
    too - two would be two places for a bound to be changed.
    """
    install_recording_streams(monkeypatch, PlainStream(), PlainStream())
    logger = logging.getLogger(PACKAGE_LOGGER_NAME)

    configure_logging()
    formatters = [handler.formatter for handler in logger.handlers]
    assert len(formatters) == 2
    assert all(
        isinstance(formatter, SanitizingFormatter) for formatter in formatters
    ), formatters
    assert formatters[0] is formatters[1]

    configure_logging(stream_split=False)
    merged = [handler.formatter for handler in logger.handlers]
    assert len(merged) == 1
    assert isinstance(merged[0], SanitizingFormatter)


def test_a_record_naming_an_absolute_workspace_path_is_logged_relatively(
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An absolute path under the workspace reaches the console relative.

    The identifier a reader acts on is the relative one - it is what
    ``README.md`` quotes and what the publisher's narrowed glob matches - and
    the absolute prefix is the layout of whichever machine happened to run the
    suite, which an archived, shared console log has no business carrying
    (CWE-200/532).  Asserted for a path a *caller did not render*, because
    that is the case the boundary exists for: ``app/cli.py`` renders its own
    artifact paths with ``render_path``, while the run service's diagnostics
    embed a path in a sentence.
    """
    logger, out, _ = configured_logger(monkeypatch)
    absolute = paths.workers_dir(cli_root)

    logger.info("could not remove %s: [Errno 39] Directory not empty", absolute)

    written = out.getvalue()
    assert logged_path(absolute, cli_root) in written, written
    assert str(cli_root) not in written, written
    assert "[Errno 39] Directory not empty" in written


def test_an_unrendered_record_cannot_forge_flood_or_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Control safety, redaction and bounding, applied to any record.

    Three properties of one boundary:

    * a ``CRLF`` inside a value cannot start a second physical line, so the
      ``ERROR`` a child or a system message contains stays inside the record
      that quoted it (CWE-117);
    * a credential shape is masked, because an exception message can quote a
      substituted step carrying this suite's fixture account (CWE-532) - and
      the masking is log-only: AAP 0.8 requires the features and all four
      artifacts to carry that data verbatim, and nothing here touches them;
    * a pathological length is bounded with a notice naming what was dropped,
      so one record cannot bury a run's log (CWE-400).
    """
    logger, _, err = configured_logger(monkeypatch)

    logger.warning("tolerated: %s", "pwd=hunter2\r\nERROR app.fake: forged")
    logger.error("selection problem: %s", "\x1b[31mred\x1b[0m alert")
    logger.error("long: %s", "word " * 3000)

    lines = [line for line in err.getvalue().splitlines() if line]
    assert len(lines) == 3, lines
    assert all(line.startswith(("WARNING ", "ERROR ")) for line in lines), lines

    forged, coloured, long_line = lines
    assert "hunter2" not in forged, forged
    assert REDACTION_PLACEHOLDER in forged, forged
    assert "\x1b" not in coloured and "red alert" in coloured, coloured
    assert len(long_line) < 3000 * len("word "), len(long_line)
    assert "truncated]" in long_line, long_line[-80:]


def test_a_traceback_reaches_the_console_marked_and_left_on_the_record(
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A traceback keeps its lines, each marked, and the record is untouched.

    ``logger.exception`` is how the report service reports a writer failure
    and how ``app/reporting/screenshots.py`` reports a suppressed capture, and
    an exception's own message is text the port did not author: a newline
    inside it would otherwise produce an unmarked line indistinguishable from
    a record of its own.  So the block keeps its structure - a traceback is
    what a failure is diagnosed from - and every line of it carries
    ``TRACEBACK_LINE_PREFIX``, including the forged one.

    The second half is why the sanitizer is a formatter and not a rewriting
    filter: after the record has been emitted, ``msg``, ``args`` and
    ``exc_text`` are exactly what the caller passed, so a second handler, an
    embedding application's handler and a capture handler all still see what
    was logged rather than what a console was shown.
    """
    logger, _, err = configured_logger(monkeypatch)
    captured: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        """Stand in for a handler this module's configuration does not own."""

        def emit(self, record: logging.LogRecord) -> None:
            """Keep the record object itself, unformatted."""
            captured.append(record)

    logging.getLogger(PACKAGE_LOGGER_NAME).addHandler(Capture())
    absolute = paths.cucumber_json_path(cli_root)
    try:
        raise OSError(f"no space left on {absolute}\nERROR app.fake: forged")
    except OSError:
        logger.exception("writer failed for %s", absolute)

    written = err.getvalue()
    body = written.splitlines()
    assert body[0].startswith("ERROR ")
    assert logged_path(absolute, cli_root) in body[0], body[0]
    block = body[1:]
    assert block, written
    assert all(line.startswith(TRACEBACK_LINE_PREFIX) for line in block), block
    assert any("Traceback (most recent call last)" in line for line in block)
    assert f"{TRACEBACK_LINE_PREFIX}ERROR app.fake: forged" in block, block
    assert str(cli_root) not in written, written

    assert len(captured) == 1
    record = captured[0]
    assert record.msg == "writer failed for %s"
    assert record.args == (absolute,)
    assert record.exc_text is None
    assert str(absolute) in record.getMessage()


def test_relativization_reduces_a_workspace_path_in_every_spelling() -> None:
    """One directory has several spellings, and all of them must reduce.

    On Windows ``\\`` and ``/`` are interchangeable and case is not
    significant, so ``C:\\ws\\job\\target``, ``C:/ws/job/target`` and
    ``c:\\WS\\Job\\target`` name one directory.  A reduction that matched only
    the native, exactly-cased spelling would publish the other two in full,
    which is the disclosure the rendering exists to prevent - and the port
    runs on Windows, Linux and macOS alike.

    The Windows rules are asserted on this host by giving the matcher its
    platform parameters explicitly, which is the only way to test them
    without a Windows agent; the platform's own defaults are asserted
    separately by the tests around this one.
    """
    root = r"C:\Jenkins\workspace\Job"
    pattern = logging_config._root_pattern(
        root, separators=("\\", "/"), case_insensitive=True
    )

    def reduced(text: str) -> str:
        return pattern.sub(logging_config._reduce_root, text)

    assert reduced(rf"failed at {root}\target\report.json") == (
        "failed at target\\report.json"
    )
    assert reduced("failed at C:/Jenkins/workspace/Job/target/report.json") == (
        "failed at target/report.json"
    )
    assert reduced(r"failed at c:\jenkins\WORKSPACE\job\target\report.json") == (
        "failed at target\\report.json"
    )
    assert reduced(f"failed at {root}") == (
        f"failed at {logging_config.WORKSPACE_ROOT_PLACEHOLDER}"
    )

    # A POSIX host must not treat a backslash as a separator: it is an
    # ordinary filename character there, so a case-sensitive, slash-only
    # matcher is what its paths require.
    posix = logging_config._root_pattern(
        "/ws/job", separators=("/",), case_insensitive=False
    )
    assert posix.sub(logging_config._reduce_root, "at /ws/job/target/x") == (
        "at target/x"
    )
    assert posix.sub(logging_config._reduce_root, "at /WS/Job/target/x") == (
        "at /WS/Job/target/x"
    )


def test_relativization_leaves_a_sibling_of_the_workspace_absolute(
    cli_root: Path,
) -> None:
    """A name that merely begins with the workspace's is a different place.

    ``<workspace>-archive`` and Jenkins's own ``<workspace>@tmp`` sit *outside*
    the workspace, so reducing them would say a file is somewhere it is not -
    a false diagnostic, and a worse outcome than the disclosure the reduction
    exists to prevent.  The boundary is asserted in both directions here: a
    root named on its own or before a delimiter reduces, a root followed by
    more name does not, and a path that merely ends with the workspace's
    components is untouched because it is reached from somewhere else.
    """
    root = str(cli_root)
    relativize = logging_config.relativize_paths
    placeholder = logging_config.WORKSPACE_ROOT_PLACEHOLDER

    for sibling in (f"{root}-archive/output.log", f"{root}@tmp/x.log"):
        assert relativize(sibling) == sibling

    assert relativize(f"could not remove {root}: [Errno 39] not empty") == (
        f"could not remove {placeholder}: [Errno 39] not empty"
    )
    assert relativize(root) == placeholder
    assert relativize(f"{root}{os.sep}") == placeholder
    assert relativize(f"/elsewhere{root}/x") == f"/elsewhere{root}/x"

    # A path outside every root keeps every component: it is not this
    # workspace's topology, and it is the whole of what such a record says.
    system_path = os.path.join(os.sep, "usr", "lib", "python3.14", "os.py")
    assert relativize(system_path) == system_path


def test_a_path_no_encoding_can_round_trip_is_still_printable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An undecodable byte in a path becomes a printable escape, not a crash.

    The interpreter decodes filesystem bytes with ``surrogateescape``, so a
    path carrying a byte the filesystem encoding cannot decode - which
    ``os.getcwd()`` itself can return - arrives as a lone surrogate.  Writing
    one to a UTF-8 console raises ``UnicodeEncodeError`` *inside the handler*,
    which loses the record altogether, so the sanitizer spells surrogates out
    like any other unprintable code point.  Asserted at both levels: the path
    renderer, and a record emitted through the configured handlers.
    """
    rendered = logging_config.render_path(b"report-\xff.json")
    assert rendered.isprintable(), rendered
    assert "\\udcff" in rendered or "\\xff" in rendered, rendered

    logger, out, _ = configured_logger(monkeypatch)
    logger.info("wrote %s", "report-\udcff.json")

    written = out.getvalue()
    assert written.strip().isprintable(), repr(written)
    assert written.encode("utf-8"), "the record could not even be encoded"


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
    # One keyword, and it is not a path: the run's claim on the build output,
    # which the fan-out checks before each writer publishes.  Every
    # destination is still resolved by the writer itself.
    assert [sorted(keywords) for keywords in generate_reports.keywords] == [["guard"]]
    guard = generate_reports.keywords[0]["guard"]
    assert hasattr(guard, "is_held")


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
        assert logged_path(path, cli_root) in result.stdout
    assert logged_path(
        paths.pretty_reports_html_dir(cli_root), cli_root
    ) in result.stdout
    assert str(cli_root) not in result.stdout
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


# --------------------------------------------------------------------------- #
# Section K - the claim one run holds on the build output
#
# Three phases of this command operate on state the whole checkout shares:
# --clean empties artifacts another run has just published, the run reclaims
# from the intermediate directory another run is writing into, and the
# fan-out publishes four artifacts one writer at a time.  Two invocations
# interleaved across that sequence leave a workspace holding a mixture of
# both - reports, scenario data, credential-bearing step arguments and
# screenshots from two different executions, with nothing in either artifact
# to say so.
#
# So the command takes one lock before the clean and gives it back after the
# publication, and contention is refused rather than queued: a run driving a
# browser suite holds it for minutes, and waiting that out would turn a CI
# stage into a hang.  What this section pins is the whole lifecycle - held
# across the three phases, handed to the fan-out as the boundary each writer
# publishes under, refused for a second run, and released on every way out,
# including the ways that are not exits at all.
#
# Two of those are not merely lifecycle but *status*, and the last tests here
# are about them.  The release happens before the status is published rather
# than only in the command's ``finally``, because a release that leaves this
# run's lock file - or the shared intermediate directory it sat in - behind is
# exactly the state AAP 0.4.1 forbids, and reporting it after the status has
# left the process would be reporting it to nobody: so it folds into the
# status by ``_remove_intermediates``'s rule, costing a success its zero and
# leaving every class that already names a failure alone.  And a fan-out whose
# four writers all succeeded can still have finished under a claim this run no
# longer held, which is an artifact failure with no failing writer in it: the
# artifacts are kept, and what is reported is that the published set cannot be
# vouched for as this run's.
# --------------------------------------------------------------------------- #


def run_lock_path(root: Path) -> Path:
    """The lock file one run holds on ``root``'s build output.

    Assembled from the two names their owners publish -
    ``app.utils.paths.workers_dir`` and ``app.services.RUN_LOCK_NAME`` - for
    the same reason the rest of this module takes its paths from the modules
    that own them: a second spelling here would still pass while the command
    locked somewhere else entirely.

    :param root: The checkout root the lock resolves against.
    :returns: The lock file's path, whether or not it currently exists.
    """
    return paths.workers_dir(root) / RUN_LOCK_NAME


def test_the_run_holds_its_claim_inside_the_build_output_while_it_runs(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """During the run, the emptied build output holds the claim and nothing else.

    Both halves are observed from inside the run, in one observation, because
    the two together are the state this design actually produces and either
    alone reads like a defect: a ``--clean`` run leaves the build output
    holding exactly one entry - the intermediate directory - and that
    directory holds exactly one entry - this invocation's lock file.  The
    clean step hands that directory to the run service rather than deleting
    it, and the service retains the lock because deleting it would hand two
    runs two different lock objects and dissolve the exclusion it exists to
    provide.

    Afterwards both are gone: releasing the lock unlinks the file and prunes
    the directory, so the build output a Jenkins publisher reads is the four
    artifacts and nothing intermediate (AAP 0.4.1).
    """
    write_decoy_artifacts(cli_root)
    target = paths.target_root(cli_root)
    workers = paths.workers_dir(cli_root)
    run_suite = RunSuiteStub(
        observe=lambda: (
            sorted(entry.name for entry in target.iterdir()),
            sorted(entry.name for entry in workers.iterdir()),
        )
    )
    install_services(
        monkeypatch, run_suite=run_suite, generate_reports=GenerateReportsStub()
    )

    result = invoke(runner, ["--clean"])

    assert result.exit_code == int(cli.ExitCode.SUCCESS)
    assert run_suite.observations == [([workers.name], [RUN_LOCK_NAME])]
    assert target.is_dir(), "the clean step removes the contents, never the directory"
    assert list(target.iterdir()) == []
    assert not run_lock_path(cli_root).exists(), "the lock file outlived the command"


@pytest.mark.parametrize(
    ("case_id", "arguments"),
    [
        ("exit-0-success", ["--no-clean"]),
        ("exit-3-dead-worker", ["--no-clean"]),
        ("exit-2-usage-error", ["--nonesuch"]),
    ],
    ids=["exit-0-success", "exit-3-dead-worker", "exit-2-usage-error"],
)
def test_the_lock_file_never_outlives_the_command(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_result_set: Any,
    case_id: str,
    arguments: list[str],
) -> None:
    """Whatever the status, no lock and no intermediate directory are left behind.

    A lock file nobody holds is worse than no lock at all: the next run in
    this checkout contends with a dead claim, waits out its grace period and
    is then refused, so "released on every exit path" is as load-bearing as
    the exclusion itself.  Three statuses are driven because they leave the
    command by three different routes - a completed publication, a dead shard
    whose artifacts are still written, and a command line Click rejects before
    the lock is ever taken.

    The usage error is the case that keeps the guarantee honest in the other
    direction: nothing is executed there, so what is asserted is that the
    lifecycle left *no* residue rather than that it cleaned up after itself.
    """
    expected = {
        "exit-0-success": int(cli.ExitCode.SUCCESS),
        "exit-3-dead-worker": int(cli.ExitCode.WORKER_DIED),
        "exit-2-usage-error": int(cli.ExitCode.USAGE_ERROR),
    }[case_id]
    dead_shards = (
        ("shard 1 of 2 (3 scenario(s)) produced no result file",)
        if case_id == "exit-3-dead-worker"
        else ()
    )
    install_services(
        monkeypatch,
        run_suite=RunSuiteStub(
            make_run_outcome(
                sample_result_set,
                selected_count=5,
                worker_count=2,
                dead_shards=dead_shards,
            )
        ),
        generate_reports=GenerateReportsStub(ok_report(*artifact_paths(cli_root))),
    )

    result = invoke(runner, arguments)

    assert result.exit_code == expected
    assert not run_lock_path(cli_root).exists(), "the lock file outlived the command"
    assert not paths.workers_dir(cli_root).exists(), (
        "the shared intermediate directory outlived the command"
    )


def test_a_second_run_in_the_same_checkout_is_refused(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A checkout already in use is refused, with nothing executed.

    The lock is taken here first, exactly as a run already in progress holds
    it, and then the command is invoked against the same checkout.  It does
    not queue: the grace period is a moment for a run that is *finishing* to
    release, not a waiting room, so a run that is genuinely executing is
    refused - and the refusal is the artifact-infrastructure class the port
    already publishes, because AAP 0.1.3 deviation 15 fixes the three non-zero
    classes and a fourth would publish a class nothing has agreed to.

    "Nothing executed" is asserted in full rather than taken on trust: the
    suite is not started, no writer is reached, no artifact appears, and the
    first run's claim is still its own afterwards - a refusal that stole the
    lock it refused would be worse than no lock at all.

    The grace period is set to zero for the duration, because what is being
    asserted is the refusal and not the wait; leaving the shipped default in
    place would spend thirty seconds arriving at the same answer.
    """
    monkeypatch.setattr(run_service, "_RUN_LOCK_WAIT_SECONDS", 0.0)
    run_suite = RunSuiteStub()
    generate_reports = GenerateReportsStub()
    install_services(
        monkeypatch, run_suite=run_suite, generate_reports=generate_reports
    )
    first, refusal = acquire_run_lock(base=cli_root)
    assert first is not None, f"the first run could not claim the checkout: {refusal}"

    with first:
        result = invoke(runner, ["--no-clean"])

        assert result.exit_code == int(cli.ExitCode.ARTIFACT_FAILURE) == 4
        assert not run_suite.calls, "a second run executed against a claimed checkout"
        assert not generate_reports.called
        assert_no_artifacts(cli_root)
        assert "another run is already using" in result.stderr
        assert logged_path(paths.target_root(cli_root), cli_root) in result.stderr
        assert first.is_held(), "the refused run took the lock it was refused"
        assert run_lock_path(cli_root).is_file()

    assert not first.is_held()
    assert not run_lock_path(cli_root).exists(), "releasing left the lock file behind"
    assert not paths.workers_dir(cli_root).exists()


def test_the_fan_out_publishes_under_this_runs_own_lock(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_result_set: Any,
) -> None:
    """The guard the fan-out receives is this run's lock, held at fan-out time.

    The publication boundary is only worth having if what the writers check is
    the live claim rather than a copy of a fact that was true earlier, so the
    guard is examined *from inside the fan-out*: it is the lock object itself,
    its file is the one the run service names inside the intermediate
    directory, and :meth:`~app.services.RunLock.is_held` answers true at the
    moment the first writer would publish.

    It is also the fan-out's **only** keyword.  No path is passed - each
    writer resolves its own destination, which is what keeps this command free
    of artifact paths - so a second keyword appearing here would be a new
    contract rather than a detail.

    Afterwards the same object answers false and its file is gone, which is
    the other half of a boundary: a run that has finished publishing holds
    nothing, and a writer that somehow ran later would find the claim
    withdrawn.
    """
    observed: list[tuple[RunLock, bool]] = []
    fan_out: GenerateReportsStub

    def publish(document: Any) -> ReportOutcome:
        """Record the live guard, then report four successful writers.

        :param document: The merged document, unused - this stands in for the
            fan-out to observe its keywords, not to write anything.
        :returns: An outcome in which every writer succeeded.
        """
        del document
        guard = fan_out.keywords[-1]["guard"]
        observed.append((guard, guard.is_held()))
        return ok_report(*artifact_paths(cli_root))

    fan_out = GenerateReportsStub(publish)
    install_services(
        monkeypatch,
        run_suite=RunSuiteStub(
            make_run_outcome(sample_result_set, selected_count=5, worker_count=2)
        ),
        generate_reports=fan_out,
    )

    result = invoke(runner, ["--no-clean"])

    assert result.exit_code == int(cli.ExitCode.SUCCESS)
    assert [sorted(keywords) for keywords in fan_out.keywords] == [["guard"]]
    assert len(observed) == 1, "the fan-out ran more than once"
    guard, held_at_fan_out = observed[0]
    assert isinstance(guard, RunLock), f"the guard was a {type(guard).__name__}"
    assert guard.path == run_lock_path(cli_root)
    assert held_at_fan_out, "the writers were asked to publish under a lost claim"
    assert not guard.is_held(), "the run kept its claim after publishing"
    assert not run_lock_path(cli_root).exists()


def test_an_interrupted_run_gives_the_claim_back(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An interrupt releases the lock, and the checkout is usable again.

    The command releases the claim before it publishes its status, so that a
    release which leaves something behind can still change that status - and
    a ``finally`` around the three phases nets every way out that line does
    not reach.  This is what the net is for: an interrupt is the operator's
    decision and an unexpected exception is a defect, neither reaches the
    release above it, and neither may leave a checkout that the next run
    cannot claim.  Stopping the run mid-flight is the sharpest form of that -
    the command never reaches its own exit line - so it is what is driven
    here, and nothing is expected to turn the interrupt into a status.

    The proof is not the absent file but the successful claim after it: a
    stale lock file is one failure mode and a lock still held by a finished
    run is another, and only taking the lock again rules out both.
    """
    install_services(monkeypatch, run_suite=RunSuiteStub(raises=KeyboardInterrupt()))

    result = invoke(runner, ["--no-clean"])

    assert result.exit_code not in {int(member) for member in cli.ExitCode}
    assert "Aborted!" in result.stderr
    assert not run_lock_path(cli_root).exists(), "the interrupt left the lock file"
    assert not paths.workers_dir(cli_root).exists()

    next_run, refusal = acquire_run_lock(base=cli_root, wait_seconds=0)
    assert next_run is not None, (
        f"the interrupted run's claim outlived it: {refusal}"
    )
    with next_run:
        assert next_run.is_held()


def surviving_lock_problem(root: Path) -> str:
    """The reason a release reports when its lock file outlived it.

    Shaped exactly as :meth:`app.services.RunLock.release` builds it - the
    lock file named by the path the run service publishes, the operating
    system's complaint in parentheses, and what that leaves in the workspace
    - so the text the command folds into a status here is the text a real
    release would hand it.  What makes a real release fail is the run
    service's business and is asserted against the real lock in
    ``tests/test_test_run_service.py``.

    :param root: The checkout root the lock resolves against.
    :returns: The one-line reason.
    """
    return (
        f"this run's lock file {run_lock_path(root)} could not be removed "
        "(canned removal failure), so it remains in the workspace"
    )


def test_a_release_that_leaves_something_behind_costs_a_success_its_zero(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_result_set: Any,
) -> None:
    """A run that would have exited ``0`` exits ``4`` when the release failed.

    A lock file with no lock behind it, or the shared intermediate directory
    it sat in, is what the *next* run in this checkout has to reason about,
    and AAP 0.4.1 requires that directory to be gone by the time the command
    returns - so a release that leaves either behind is not cosmetic and this
    command does not report success over it.  The fold is
    ``_remove_intermediates``'s: a success becomes
    :attr:`~app.cli.ExitCode.ARTIFACT_FAILURE`, and the reason is named
    beside that class.

    That the release happens **before the status is published** is what the
    rest of the assertions are about, because a release folded in after the
    status has left the process would be a diagnostic nobody acts on.  Three
    independent readings of it: the published status is the release's ``4``
    and not the run's ``0``, which is only possible if the fold preceded the
    exit; the success record the command writes only for a settled zero was
    never written; and the trace puts the release after the fan-out, which is
    the phase it has to outlive.

    The trace's fourth entry is the ``finally`` net calling release a second
    time, and the last two assertions are that this changes nothing and
    duplicates nothing - a second copy of the reason would make one incident
    two ``ERROR`` records in a Jenkins console log.
    """
    problem = as_logged(surviving_lock_problem(cli_root), cli_root)
    trace: list[str] = []
    lock = RunLockStub(problem, trace=trace)
    acquisitions = install_run_lock(monkeypatch, lock)
    install_services(
        monkeypatch,
        run_suite=RunSuiteStub(
            make_run_outcome(sample_result_set, selected_count=5),
            trace=trace,
        ),
        generate_reports=GenerateReportsStub(
            ok_report(*artifact_paths(cli_root)), trace=trace
        ),
    )

    result = invoke(runner, ["--no-clean"])

    assert result.exit_code == int(cli.ExitCode.ARTIFACT_FAILURE) == 4
    assert acquisitions == [{}], (
        "the command claimed something other than this checkout's build "
        "output"
    )
    assert stderr_record(result, problem).endswith(
        f"Exit {int(cli.ExitCode.ARTIFACT_FAILURE)}: {problem}"
    ), "the release failure was not named beside the class it produced"
    assert "Finished with status" not in result.stdout, (
        "the run reported success before the release was settled"
    )
    assert trace == [
        RUN_SUITE_LABEL,
        GENERATE_REPORTS_LABEL,
        RELEASE_LABEL,
        RELEASE_LABEL,
    ], "the release did not settle after the fan-out, then again as the net"
    assert lock.releases == 2, "the finally net did not release a second time"
    assert result.stderr.count(problem) == 1, (
        "the second release reported the same incident again"
    )


@pytest.mark.parametrize(
    "case_id",
    ["exit-3-dead-worker", "exit-4-empty-merge"],
    ids=["exit-3-dead-worker", "exit-4-empty-merge"],
)
def test_a_failed_release_never_overwrites_a_non_zero_status(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_result_set: Any,
    case_id: str,
) -> None:
    """A status that already names a failure keeps its own class.

    The asymmetry is the whole point of the fold, and it is the one
    ``_remove_intermediates`` already has: a dead worker and an artifact
    failure each name something an operator must act on, and a tidy-up that
    could not give a lock file back does not outrank either.  Overwriting the
    ``3`` would hide which work was lost behind a workspace complaint; the
    ``4`` is already the class the release failure would have produced, so
    re-announcing it would put a second ``Exit 4:`` line in the log for a
    different cause.

    Both classes reachable at that point are driven - a published run with a
    dead shard, and a merge that produced nothing - and each asserts the
    record that says so in full, including *which* status was left in place,
    because a record that merely mentioned the problem would not distinguish
    "kept the 3" from "quietly replaced it".
    """
    expected = {
        "exit-3-dead-worker": int(cli.ExitCode.WORKER_DIED),
        "exit-4-empty-merge": int(cli.ExitCode.ARTIFACT_FAILURE),
    }[case_id]
    outcome = (
        make_run_outcome(
            sample_result_set,
            selected_count=5,
            worker_count=2,
            dead_shards=(
                "shard 1 of 2 (2 scenario(s), features/Sales.feature:11) "
                "produced no result file",
            ),
        )
        if case_id == "exit-3-dead-worker"
        else make_run_outcome(
            None, selected_count=5, merge_produced_nothing=True
        )
    )
    problem = as_logged(surviving_lock_problem(cli_root), cli_root)
    lock = RunLockStub(problem)
    install_run_lock(monkeypatch, lock)
    install_services(
        monkeypatch,
        run_suite=RunSuiteStub(outcome),
        generate_reports=GenerateReportsStub(
            ok_report(*artifact_paths(cli_root))
        ),
    )

    result = invoke(runner, ["--no-clean"])

    assert result.exit_code == expected
    assert stderr_record(result, problem).endswith(
        f"{problem}; the status stays {expected}, because the failure "
        "already being reported outranks a tidy-up"
    ), "the settlement did not say which status it left in place"
    announced = f"Exit {int(cli.ExitCode.ARTIFACT_FAILURE)}: {problem}"
    assert announced not in result.stderr, (
        "a tidy-up failure was announced as this run's exit class"
    )
    assert lock.releases == 2, "the finally net did not release a second time"
    assert result.stderr.count(problem) == 1, (
        "the second release reported the same incident again"
    )


@pytest.mark.parametrize(
    "case_id",
    ["exit-0-success", "exit-3-dead-worker", "exit-4-empty-merge"],
    ids=["exit-0-success", "exit-3-dead-worker", "exit-4-empty-merge"],
)
def test_a_release_that_reports_nothing_leaves_the_status_untouched(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_result_set: Any,
    case_id: str,
) -> None:
    """The ordinary release is silent and decides nothing.

    The other half of the fold, and the half every real run takes: a release
    that gives the claim back and leaves nothing behind reports ``None``, and
    the status the run had arrived at is published unchanged.  A settlement
    that were merely *usually* silent would turn every green run into a
    ``4``, so all three classes reachable at that point are driven and each
    asserts that no settlement record was written at all.

    The success case additionally asserts the record the command writes only
    for a settled zero, which is the positive form of the previous test's
    absence.
    """
    expected = {
        "exit-0-success": int(cli.ExitCode.SUCCESS),
        "exit-3-dead-worker": int(cli.ExitCode.WORKER_DIED),
        "exit-4-empty-merge": int(cli.ExitCode.ARTIFACT_FAILURE),
    }[case_id]
    outcome = (
        make_run_outcome(
            None, selected_count=5, merge_produced_nothing=True
        )
        if case_id == "exit-4-empty-merge"
        else make_run_outcome(
            sample_result_set,
            selected_count=5,
            worker_count=2,
            dead_shards=(
                "shard 1 of 2 (2 scenario(s), features/Sales.feature:11) "
                "produced no result file",
            )
            if case_id == "exit-3-dead-worker"
            else (),
        )
    )
    lock = RunLockStub()
    install_run_lock(monkeypatch, lock)
    install_services(
        monkeypatch,
        run_suite=RunSuiteStub(outcome),
        generate_reports=GenerateReportsStub(
            ok_report(*artifact_paths(cli_root))
        ),
    )

    result = invoke(runner, ["--no-clean"])

    assert result.exit_code == expected
    assert "the status stays" not in result.stderr, (
        "a silent release settled a status it had nothing to say about"
    )
    assert lock.releases == 2, "the finally net did not release a second time"
    if expected == int(cli.ExitCode.SUCCESS):
        assert f"Finished with status {expected}" in result.stdout
    else:
        assert "Finished with status" not in result.stdout


def test_the_release_settlement_is_defined_for_every_published_class(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Two properties of the settlement the command itself cannot reach.

    The fold takes whatever status the command had arrived at, and two of its
    rows are not reachable through an invocation.  A usage error is one:
    ``--rerun --tags`` and every value Click rejects while parsing are raised
    **before** the claim is taken, so no command line can put a ``2`` in
    front of a release failure - and yet the row has to be defined, because
    the fold's contract is over :class:`~app.cli.ExitCode` and not over the
    subset one call site happens to produce.  It is therefore asserted
    directly, where the private name is reached deliberately rather than
    incidentally.

    The other is totality in the silent direction: ``None`` returns *every*
    member unchanged, including the ones an invocation drives above, which is
    what rules out a fold that special-cases the classes it was tested with.

    Read from the log records rather than from stderr, for the reason
    :func:`test_a_failed_cleanup_does_not_upgrade_a_parse_time_usage_error`
    states: nothing here configures logging, so the two-handler stream
    contract this module asserts elsewhere is not what carries these
    records.
    """
    for member in cli.ExitCode:
        assert cli._settle_release(None, member) is member, (
            f"a silent release changed {member!r}"
        )

    problem = "this run's lock file remains in the workspace"
    logging.getLogger(PACKAGE_LOGGER_NAME).propagate = True

    with caplog.at_level(logging.ERROR, logger=CLI_LOGGER_NAME):
        settled = cli._settle_release(problem, cli.ExitCode.USAGE_ERROR)

    assert settled is cli.ExitCode.USAGE_ERROR
    assert caplog.text.count(problem) == 1
    assert (
        f"the status stays {int(cli.ExitCode.USAGE_ERROR)}" in caplog.text
    ), "the settlement did not say which status it left in place"
    assert f"Exit {int(cli.ExitCode.ARTIFACT_FAILURE)}" not in caplog.text, (
        "a tidy-up failure was announced as an exit class over a usage error"
    )


@pytest.mark.parametrize(
    "case_id",
    ["ordinary-publication", "dead-shard-publication"],
    ids=["ordinary-publication", "dead-shard-publication"],
)
def test_the_fan_out_is_always_given_this_runs_claim_as_its_guard(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_result_set: Any,
    case_id: str,
) -> None:
    """The command never publishes without handing over its claim.

    The publication boundary is worth exactly as much as the weakest path
    that reaches it: a guard checked before every writer protects nothing on
    a path where the command passed ``None``, and ``None`` is accepted by the
    fan-out by design, so that it stays callable by a test or by a caller
    that established exclusivity some other way.  Nothing in the signature
    distinguishes the two, which is why the *production caller* supplying a
    guard on every path is asserted here rather than assumed.

    Both paths that reach the fan-out at all are driven, because they differ
    downstream of it and not before: an ordinary publication, and one whose
    run lost a shard - the class the second settles on comes from the shard,
    and the publication it performed on the way there is still this run's.
    The guard is asserted to be **the very object the command acquired**, by
    identity, and ``guard`` is still the only keyword: the paths are the
    writers' own to resolve, which is what keeps this command free of them.
    """
    expected, dead_shards = {
        "ordinary-publication": (int(cli.ExitCode.SUCCESS), ()),
        "dead-shard-publication": (
            int(cli.ExitCode.WORKER_DIED),
            (
                "shard 1 of 2 (2 scenario(s), features/Sales.feature:11) "
                "produced no result file",
            ),
        ),
    }[case_id]
    lock = RunLockStub()
    acquisitions = install_run_lock(monkeypatch, lock)
    fan_out = GenerateReportsStub(ok_report(*artifact_paths(cli_root)))
    install_services(
        monkeypatch,
        run_suite=RunSuiteStub(
            make_run_outcome(
                sample_result_set,
                selected_count=5,
                worker_count=2,
                dead_shards=dead_shards,
            )
        ),
        generate_reports=fan_out,
    )

    result = invoke(runner, ["--no-clean"])

    assert result.exit_code == expected
    assert acquisitions == [{}]
    assert fan_out.keywords == [{"guard": lock}], (
        "the fan-out was given something other than this run's own claim"
    )
    assert fan_out.keywords[0]["guard"] is lock
    assert fan_out.keywords[0]["guard"] is not None, (
        "the command published without a claim for the writers to check"
    )


def test_a_publication_that_lost_this_runs_claim_exits_four_and_keeps_all(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_result_set: Any,
) -> None:
    """Four artifacts, no failing writer, and still an artifact failure.

    The outcome asserted here is the one the fan-out returns when its guard
    was held before every writer and gone after the last one: another run
    claimed this checkout while the four were being written, so the four
    files on disk may be a mixture of two runs' reports with nothing in
    either artifact to say so.  Nothing can be un-published at that point,
    and nothing here tries: the exit contract keeps whatever was written, so
    what the command owes a reader is the class and the reason.

    It is the first artifact failure with **no writer in it**, which is why
    the negative assertions matter as much as the positive ones.  A record
    naming a writer would send an operator to look at a writer that did
    exactly what it was asked, and an "unattempted" list would be a claim
    about work that all completed; the cause is the workspace, and the record
    names the build output root it was lost on.

    The four artifacts are seeded before the run and compared byte for byte
    afterwards, because "retained, and not deleted" is a statement about the
    filesystem and not only about a log line.
    """
    decoys = write_decoy_artifacts(cli_root)
    written = artifact_paths(cli_root)
    outcome = lost_claim_report(*written)
    assert outcome.ok is False, "the lost claim did not fail the fan-out"
    assert outcome.boundary_lost is True
    assert outcome.failed_writer is None, "this path has no failing writer"
    assert outcome.error is None
    assert outcome.skipped == ()
    assert outcome.written == written
    install_services(
        monkeypatch,
        run_suite=RunSuiteStub(
            make_run_outcome(sample_result_set, selected_count=5)
        ),
        generate_reports=GenerateReportsStub(outcome),
    )

    result = invoke(runner, ["--no-clean"])

    assert result.exit_code == int(cli.ExitCode.ARTIFACT_FAILURE) == 4
    assert stderr_record(result, "lost its claim on").endswith(
        f"Exit {int(cli.ExitCode.ARTIFACT_FAILURE)}: the four artifacts were "
        f"written, but this run lost its claim on "
        f"{logged_path(paths.target_root(cli_root), cli_root)} during "
        "publication, so the published "
        "set is not vouched for as this run's"
    ), "the lost claim was not named beside the class it produced"
    assert stderr_record(result, "Retained, and not deleted:").endswith(
        "Retained, and not deleted: "
        + ", ".join(logged_path(path, cli_root) for path in written)
    ), "the four retained artifacts were not named"
    for name in WRITER_NAMES:
        assert name not in result.stderr, (
            f"{name} was blamed for a failure no writer had"
        )
    assert "Not attempted after" not in result.stderr, (
        "writers were reported unattempted where all four ran"
    )
    assert "Wrote 4 artifact(s)" not in result.stdout, (
        "the run reported a successful publication it cannot vouch for"
    )
    assert_four_artifacts(cli_root)
    assert_decoys_unchanged(decoys)


def test_a_publication_whose_claim_held_throughout_still_exits_zero(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_result_set: Any,
) -> None:
    """The ordinary run is unaffected by the boundary the previous test lost.

    The other side of the same field, and the one every green run depends
    on: a fan-out that kept its claim reports
    :attr:`~app.services.ReportOutcome.boundary_lost` false, which leaves
    :attr:`~app.services.ReportOutcome.ok` deciding the status exactly as it
    did before the boundary existed.  A default that leaned the other way -
    or an ``ok`` that read the field wrongly - would fail every ordinary run
    with a record about a claim nobody lost, so the zero is asserted
    together with the absence of that record.
    """
    outcome = ok_report(*artifact_paths(cli_root))
    assert outcome.boundary_lost is False
    assert outcome.ok is True
    install_services(
        monkeypatch,
        run_suite=RunSuiteStub(
            make_run_outcome(sample_result_set, selected_count=5)
        ),
        generate_reports=GenerateReportsStub(outcome),
    )

    result = invoke(runner, ["--no-clean"])

    assert result.exit_code == int(cli.ExitCode.SUCCESS) == 0
    assert "lost its claim on" not in result.stderr, (
        "an ordinary publication was reported as having lost its claim"
    )
    assert "Retained, and not deleted:" not in result.stderr
    assert f"Wrote {len(outcome.written)} artifact(s)" in result.stdout
    assert f"Finished with status {int(cli.ExitCode.SUCCESS)}" in result.stdout


# --------------------------------------------------------------------------- #
# Section L - the dead-shard diagnostics, whatever the exit class
#
# A dead shard and an artifact failure can coexist, and the artifact failure
# outranks it - most obviously when *every* worker dies, which leaves nothing
# to merge, so the run reports the empty merge.  That precedence decides the
# status and must not decide what is said: the run service builds one reason
# per shard, naming the shard, its scenario count and what went wrong, carries
# them on RunOutcome.dead_shards and deliberately logs none of them, because
# this command is the single emitter of every fact an outcome carries.
#
# So the emission is separated from the exit class: every reason is named
# before each of the precedence returns, which is what keeps the only record
# of *which* work was lost from being computed and then discarded on the
# all-workers-dead path.  The other half of that is anti-duplication - one
# incident, one record - so the partial-success path below counts.
#
# The separation has a consequence this section also pins: the aggregate
# record is **status-neutral**, and says only how many shards produced no
# results and that each is named below.  It is written before the exit class
# is known, so it cannot say what became of the artifacts - and on three of
# the four paths below the obvious claim is false: nothing at all is written
# when the merge produced nothing, a rerun writes nothing by design, and a
# failed writer leaves a prefix.  The claim belongs to the one path where it
# holds, so it sits on ``_worker_status``'s exit-3 line, which is reached only
# after a publication succeeded.  Every assertion below therefore names the
# record it is reading - the aggregate or the status line - and the three
# false paths assert the claim's absence from the whole of stderr, because
# that absence is the finding.
# --------------------------------------------------------------------------- #


def test_every_dead_shard_is_named_when_the_merge_produced_nothing(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All workers dead: the empty merge decides the status, not the account.

    This is the path the separation exists for.  Every shard died, so nothing
    could be merged, so the run reports the empty merge and exits
    :attr:`~app.cli.ExitCode.ARTIFACT_FAILURE` - and a status settled before
    the shards were named would leave a CI log saying only that the merge
    produced nothing, with the shard indices, their locations and their
    reasons lost.

    Three things are asserted: each reason appears on stderr verbatim, each
    appears exactly once, and each appears **before** the empty-merge record
    that ends the run.  The ordering is the assertion that would fail if the
    emission drifted back inside the status functions, because that is where
    the aggregate line still gets written and the per-shard lines do not.

    A fourth, and it is what makes this path the sharpest case for the
    status-neutral aggregate: **no artifact was written here at all**, so any
    record claiming the four were written from the completed shards would be
    false, and the reader of a CI log would go looking for reports that do
    not exist.  The aggregate is therefore read as one record and asserted to
    end where it does, and the claim is searched for across the whole of
    stderr and required to be absent.
    """
    reasons = (
        (
            "shard 0 of 2 (3 scenario(s), features/Crm.feature:9) produced no "
            "result file"
        ),
        (
            "shard 1 of 2 (2 scenario(s), features/Login.feature:14) exited "
            "with status 9"
        ),
    )
    run_suite = RunSuiteStub(
        make_run_outcome(
            None,
            selected_count=5,
            worker_count=2,
            dead_shards=reasons,
            merge_produced_nothing=True,
        )
    )
    generate_reports = GenerateReportsStub()
    install_services(
        monkeypatch, run_suite=run_suite, generate_reports=generate_reports
    )

    result = invoke(runner, ["--no-clean"])

    assert result.exit_code == int(cli.ExitCode.ARTIFACT_FAILURE) == 4
    assert not generate_reports.called
    assert_no_artifacts(cli_root)
    assert stderr_record(result, DEAD_SHARD_AGGREGATE_TAIL).endswith(
        f"2 of 2 {DEAD_SHARD_AGGREGATE_TAIL}"
    ), "the aggregate record said something beyond the shards it named"
    assert DEAD_SHARD_ARTIFACT_CLAIM not in result.stderr, (
        "a record claimed artifacts were written where none was"
    )
    assert "the merge produced nothing" in result.stderr
    for reason in reasons:
        assert result.stderr.count(reason) == 1, reason
        assert reason not in result.stdout, reason
        assert result.stderr.index(reason) < result.stderr.index(
            "the merge produced nothing"
        ), f"{reason} was named after the status that suppressed it"


def test_dead_shards_are_named_on_the_rerun_infrastructure_path(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rerun that lost its workspace still names the shards it lost.

    ``--rerun`` writes no artifact by design, so its ordinary outcome and a
    rerun whose intermediate storage failed are indistinguishable from the
    artifacts - which is why the infrastructure signal is read before the
    rerun short-circuit and settles the status at
    :attr:`~app.cli.ExitCode.ARTIFACT_FAILURE`.  That return is the second of
    the four the emission had to be lifted above, and this is the case that
    holds it there: the shard that produced no result is named even though the
    status comes from the workspace rather than from the shard.

    It is also the path on which the artifact claim is false *by design*
    rather than by accident: a rerun writes nothing whatever its shards did,
    which the rerun record on stdout says in its own words.  So the aggregate
    is read as one record, its tail is asserted, and the claim is required to
    be absent from the whole of stderr - a log that said both would contradict
    itself across two streams.
    """
    reason = (
        "shard 0 of 1 (4 scenario(s), features/Employee.feature:22) produced "
        "no result file"
    )
    run_suite = RunSuiteStub(
        make_run_outcome(
            None,
            selected_count=4,
            worker_count=1,
            dead_shards=(reason,),
            rerun=True,
            tag_expression=None,
            infrastructure_error=(
                "this run's intermediate directory could not be prepared"
            ),
        )
    )
    generate_reports = GenerateReportsStub()
    install_services(
        monkeypatch, run_suite=run_suite, generate_reports=generate_reports
    )

    result = invoke(runner, ["--rerun"])

    assert result.exit_code == int(cli.ExitCode.ARTIFACT_FAILURE) == 4
    assert not generate_reports.called
    assert_no_artifacts(cli_root)
    assert result.stderr.count(reason) == 1
    assert stderr_record(result, DEAD_SHARD_AGGREGATE_TAIL).endswith(
        f"1 of 1 {DEAD_SHARD_AGGREGATE_TAIL}"
    ), "the aggregate record said something beyond the shard it named"
    assert DEAD_SHARD_ARTIFACT_CLAIM not in result.stderr, (
        "a rerun claimed artifacts it never writes"
    )
    assert "this run's own intermediate storage failed" in result.stderr
    assert "no artifact was written and none was modified" in result.stdout


def test_dead_shards_are_named_when_a_writer_failed(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_result_set: Any,
) -> None:
    """A writer failure outranks a dead shard and does not silence it.

    The two facts are independent - a shard produced no results, and a writer
    could not write - and a reader needs both: the artifacts on disk are
    incomplete for one reason and drawn from fewer shards than were planned
    for another.  The status is the writer's, the shard is still named, and
    the two records are distinguishable, which is what the assertions below
    pin: the failing writer's own line names it beside the exit class, and the
    shard's line names the shard.

    The third path on which the artifact claim is false, and the subtlest:
    *some* artifacts were written here - the two the earlier writers produced
    and which are retained - but not the four, and not the set a publisher
    expects.  So the writer-failure account is asserted in full, retained and
    unattempted writers included, and the claim is required to be absent:
    "the four artifacts were written from the shards that completed" beside a
    report of two retained files is a contradiction a reader has to resolve
    by re-running the command.
    """
    failed_destination = artifact_paths(cli_root)[2]
    reason = (
        "shard 2 of 3 (1 scenario(s), features/Notes.feature:8) exited with "
        "status 9"
    )
    run_suite = RunSuiteStub(
        make_run_outcome(
            sample_result_set,
            selected_count=9,
            worker_count=3,
            dead_shards=(reason,),
        )
    )
    generate_reports = GenerateReportsStub(
        failed_report(
            written=list(artifact_paths(cli_root)[:2]),
            failed_index=2,
            error=OSError("no space left on device"),
            failed_path=failed_destination,
        )
    )
    install_services(
        monkeypatch, run_suite=run_suite, generate_reports=generate_reports
    )

    result = invoke(runner, ["--no-clean"])

    retained = artifact_paths(cli_root)[:2]
    assert result.exit_code == int(cli.ExitCode.ARTIFACT_FAILURE) == 4
    assert result.stderr.count(reason) == 1
    assert stderr_record(result, DEAD_SHARD_AGGREGATE_TAIL).endswith(
        f"1 of 3 {DEAD_SHARD_AGGREGATE_TAIL}"
    ), "the aggregate record said something beyond the shard it named"
    assert DEAD_SHARD_ARTIFACT_CLAIM not in result.stderr, (
        "a failed fan-out claimed all four artifacts were written"
    )
    assert (
        f"Exit {int(cli.ExitCode.ARTIFACT_FAILURE)}: report writer "
        f"{WRITER_NAMES[2]} failed writing "
        f"{logged_path(failed_destination, cli_root)}"
    ) in result.stderr
    assert (
        f"Not attempted after {WRITER_NAMES[2]} failed: {WRITER_NAMES[3]}"
    ) in result.stderr, "the unattempted writer was not named"
    assert (
        "Retained, and not deleted: "
        + ", ".join(logged_path(path, cli_root) for path in retained)
    ) in result.stderr, "the surviving artifacts were not named"
    assert f"Exit {int(cli.ExitCode.WORKER_DIED)}" not in result.stderr, (
        "two exit classes were announced for one run"
    )


def test_a_partial_success_names_each_dead_shard_exactly_once(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_result_set: Any,
) -> None:
    """A run that published names each shard once, and says ``3`` once.

    The anti-duplication half of the same guarantee.  On this path both the
    shard emission and the status function run - the artifacts were written
    and the dead shard settles the class at
    :attr:`~app.cli.ExitCode.WORKER_DIED` - so it is the path where a second
    copy of each reason would appear if the status function still repeated
    what it used to report.  The counts are therefore the assertion, not the
    presence: one aggregate line, one line per reason, and one status line.

    This is also the **one** path where the artifact claim is true, so it is
    where the claim lives: the publication succeeded, all four artifacts were
    written from the shards that completed, and the record that says so is
    the exit-3 status line - the line reached only after the fan-out
    returned ``ok``.  Both halves are read as records here: the status line
    carries the claim, and the aggregate line above it still does not.
    That division is what lets the three false paths assert the claim's
    absence without also losing it where it belongs.

    Emitting from both places is not a cosmetic fault: a reader counting
    ``ERROR`` records over a Jenkins console log over-counts the incident, and
    neither record is then the canonical account of it.
    """
    reason = (
        "shard 1 of 2 (2 scenario(s), features/Sales.feature:11) produced no "
        "result file"
    )
    run_suite = RunSuiteStub(
        make_run_outcome(
            sample_result_set,
            selected_count=5,
            worker_count=2,
            dead_shards=(reason,),
        )
    )
    generate_reports = GenerateReportsStub(ok_report(*artifact_paths(cli_root)))
    install_services(
        monkeypatch, run_suite=run_suite, generate_reports=generate_reports
    )

    result = invoke(runner, ["--no-clean"])

    assert result.exit_code == int(cli.ExitCode.WORKER_DIED) == 3
    assert result.stderr.count(reason) == 1, "the dead shard was reported twice"
    assert result.stderr.count("Incomplete shard:") == 1
    assert stderr_record(result, DEAD_SHARD_AGGREGATE_TAIL).endswith(
        f"1 of 2 {DEAD_SHARD_AGGREGATE_TAIL}"
    ), "the aggregate record claimed what the status line is there to claim"
    assert stderr_record(
        result, f"Exit {int(cli.ExitCode.WORKER_DIED)}:"
    ).endswith(
        f"Exit {int(cli.ExitCode.WORKER_DIED)}: 1 of 2 worker shard(s) "
        f"produced no results, so the four {DEAD_SHARD_ARTIFACT_CLAIM}"
    ), "the status line did not carry the claim this path makes true"
    assert result.stderr.count(DEAD_SHARD_ARTIFACT_CLAIM) == 1, (
        "the claim that four artifacts were written was made more than once"
    )


def test_the_aggregate_record_is_the_same_whether_the_artifacts_exist(
    runner: CliRunner,
    cli_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_result_set: Any,
) -> None:
    """The aggregate record does not vary with the class that follows it.

    Status-neutrality stated as one property rather than inferred from four
    separate paths: the same shard failure is driven twice, once where the
    publication succeeded and all four artifacts were written, and once where
    the merge produced nothing and none was, and the record naming the dead
    shards is required to be **the same line** in both.  It cannot be, if
    that record says anything about the artifacts - which is the defect this
    is the gate for, since the wording it used to carry was true of the first
    run and false of the second.

    Comparing whole records is what makes the assertion strict, and
    ``app/logging_config.py``'s timestamp-free format is what makes it
    possible: two invocations' records are comparable verbatim, prefix
    included.  The counts are held equal between the two runs on purpose, so
    that the only thing a difference could come from is the wording.

    The claim itself is then located: exactly once in the run that published,
    on the exit-3 status line, and nowhere at all in the run that wrote
    nothing.
    """
    reason = (
        "shard 1 of 2 (2 scenario(s), features/Sales.feature:11) produced no "
        "result file"
    )
    install_services(
        monkeypatch,
        run_suite=RunSuiteStub(
            make_run_outcome(
                sample_result_set,
                selected_count=5,
                worker_count=2,
                dead_shards=(reason,),
            )
        ),
        generate_reports=GenerateReportsStub(
            ok_report(*artifact_paths(cli_root))
        ),
    )

    published = invoke(runner, ["--no-clean"])

    install_services(
        monkeypatch,
        run_suite=RunSuiteStub(
            make_run_outcome(
                None,
                selected_count=5,
                worker_count=2,
                dead_shards=(reason,),
                merge_produced_nothing=True,
            )
        ),
        generate_reports=GenerateReportsStub(),
    )

    nothing_written = invoke(runner, ["--no-clean"])

    assert published.exit_code == int(cli.ExitCode.WORKER_DIED) == 3
    assert nothing_written.exit_code == int(cli.ExitCode.ARTIFACT_FAILURE) == 4
    aggregate = stderr_record(published, DEAD_SHARD_AGGREGATE_TAIL)
    assert aggregate == stderr_record(
        nothing_written, DEAD_SHARD_AGGREGATE_TAIL
    ), "the aggregate record varied with the exit class that followed it"
    assert aggregate.endswith(f"1 of 2 {DEAD_SHARD_AGGREGATE_TAIL}")
    assert published.stderr.count(DEAD_SHARD_ARTIFACT_CLAIM) == 1, (
        "the published run did not carry the claim exactly once"
    )
    assert DEAD_SHARD_ARTIFACT_CLAIM not in nothing_written.stderr, (
        "the run that wrote nothing claimed four artifacts"
    )
