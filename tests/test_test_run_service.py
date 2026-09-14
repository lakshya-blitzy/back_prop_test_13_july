"""Orchestration tests for ``app/services/test_run_service.py``.

This module is the gate for the two invariants AAP 0.6 says are the only ones
that *can* hold for a sharded browser suite, and it is the only owned module
that drives ``features/environment.py``'s scenario hooks:

1. **Exact-once assignment.** *"Every selected scenario is assigned to exactly
   one worker"* (AAP 0.6) - for any worker count, with no duplicate and no lost
   scenario.
2. **Worker-count independence.** *"For a fixed set of shard inputs the merged
   structure - feature order, scenario order, background position, statuses -
   is identical whatever the worker count"* (AAP 0.6), with timestamps,
   durations and error text normalized before comparison because those vary by
   construction.

Everything else here follows the same source contract: ``pom.xml:22``'s
``parallel=methods`` is method- and therefore scenario-level sharding,
``pom.xml:25``'s ``testFailureIgnore=true`` together with the six ``-1``
publisher thresholds at ``Jenkins:15`` forbid a test outcome from reaching the
exit status, ``Jenkins:15``'s narrowed ``target/cucumber.json`` glob is why
``target/.workers/`` must never survive a run, and ``FailedTestRunner.java:9-12``
declares no tag filter, which is what the neutral tag expression exists to
reproduce.

How these tests avoid launching anything
----------------------------------------
Three seams, all published by the production code, and no monkeypatching of
:mod:`subprocess` anywhere:

``base=``
    Every path-taking call receives :fixture:`tmp_artifact_root`, so nothing is
    written outside pytest's ``tmp_path`` and the repository's real ``target/``
    is never created.  The feature files a test selects from are written into
    that same temporary root by :func:`feature_tree`, so selection is exercised
    against a tree this module controls line by line; the one measured claim
    about the *real* suite - that the ``@Smoke`` default selects the CRM feature
    alone - is asserted against the real ``features/`` directory, because that
    is a fact about the suite rather than about the algorithm.

``spawn=``
    :class:`RecordingSpawn` stands in for the engine launch.  It records the
    argument list and working directory it was handed and writes a canned
    worker document, built through ``app/reporting/events.py``'s own document
    builders, to the ``-o`` path it finds in that argument list.  No process is
    started, no timeout is waited on and no test sleeps.

``monkeypatch`` on the names the module under test binds
    Used only where a delegation is the thing being asserted - the rerun
    grammar's owner, the hooks' collaborators - never to replace the subject.

What this module deliberately does not assert
---------------------------------------------
* **Not byte equality on a merged document.**  AAP 0.6 forbids it outright;
  :func:`~conftest.normalize_volatile` is how the structural comparison is made.
* **Not that a feature is never split across shards.**  The implementation
  round-robins per *scenario* while enumerating grouped by feature, which is
  what ``parallel=methods`` means; see
  :func:`test_worker_locations_stay_grouped_by_feature_and_ascending_by_line`.
* **Not the transport of the launch.**  Whether the seam's default runs the
  engine through :func:`subprocess.run` or drains a :class:`subprocess.Popen`
  is that function's business; these tests assert the argument list and the
  documents, which is the contract every caller downstream depends on.
* **Not an exit status.**  ``app/cli.py`` owns the exit table; this module
  asserts the :class:`~app.services.test_run_service.RunOutcome` fields the
  table is computed from, and that nothing here terminates the interpreter.
"""

from __future__ import annotations

import ast
import configparser
import itertools
import logging
import os
import re
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import pytest
from cucumber_tag_expressions import TagExpressionError

import features.environment as environment
from app import config
from app.reporting import events, rerun_report
from app.services import test_run_service as service
from app.utils import paths
from conftest import FakeContext

# --------------------------------------------------------------------------- #
# The temporary feature tree
#
# Six executable units over two files, which is the smallest tree that can
# distinguish the properties this module asserts: two features (so a feature
# order is observable), a Background (so its position among the merged elements
# is observable), a feature-level tag, a scenario-level tag, a tagged Examples
# block and a two-row outline (so effective-tag propagation and outline-row
# expansion are observable), and an untagged feature (so the five untagged
# features of the real suite - Contact, Inventory, Notes, Sales and Session -
# have a stand-in that a positive tag expression cannot reach).
#
# The two titles are deliberately in the reverse of their filenames' order:
# AAP 0.6 requires features in *source* order, which the merge reproduces as
# ascending feature path, and a document ordered by name would pass an
# ascending-path assertion on any tree whose names happen to sort the same way.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FeatureFile:
    """One feature file of the temporary tree.

    Attributes:
        filename: The file's own name, which with
            :data:`~app.utils.paths.NORMALIZED_FEATURES_PREFIX` forms the
            repository-relative path the service and the merge key on.
        title: The ``Feature:`` title, reused by :func:`worker_document` so a
            canned worker document names its features exactly as the real
            engine would.
        text: The Gherkin source, written verbatim.
    """

    filename: str
    title: str
    text: str

    @property
    def feature_path(self) -> str:
        """Return the repository-relative, forward-slashed path.

        Returns:
            For example ``features/Alpha.feature`` - built from the paths
            module's own prefix, so this module carries no path literal.
        """
        return f"{paths.NORMALIZED_FEATURES_PREFIX}{self.filename}"


#: The tagged feature: a feature-level tag, a Background, a plain scenario, a
#: scenario carrying its own tag, and a two-row outline under a tagged Examples
#: block.  Line numbers are load-bearing throughout this module and are stated
#: in :data:`ALPHA_LOCATION_LINES` rather than recomputed.
ALPHA_FEATURE: Final[FeatureFile] = FeatureFile(
    filename="Alpha.feature",
    title="Zulu alpha feature",
    text="""@Alpha
Feature: Zulu alpha feature

  Background: shared setup
    Given a precondition

  Scenario: first alpha
    Given a precondition

  @Beta
  Scenario: second alpha
    Given a precondition

  Scenario Outline: third alpha
    Given a precondition of <value>

    @Gamma
    Examples: rows
      | value |
      | one   |
      | two   |
""",
)

#: The untagged feature, the stand-in for the real suite's five untagged ones.
BETA_FEATURE: Final[FeatureFile] = FeatureFile(
    filename="Beta.feature",
    title="Alpha beta feature",
    text="""Feature: Alpha beta feature

  Scenario: first beta
    Given a precondition

  Scenario: second beta
    Given a precondition
""",
)

#: The tree, in the order the files are written; the order is irrelevant to
#: every assertion, which is part of what is being asserted.
FEATURE_TREE: Final[tuple[FeatureFile, ...]] = (ALPHA_FEATURE, BETA_FEATURE)

#: ``Alpha.feature``'s executable units, in ascending line order: the plain
#: scenario, the tagged scenario, and the outline's two Examples data rows.
#: An outline row's own line is the data row's, never the header's (AAP 0.6).
ALPHA_LOCATION_LINES: Final[tuple[int, ...]] = (7, 11, 20, 21)

#: ``Beta.feature``'s two scenario lines.
BETA_LOCATION_LINES: Final[tuple[int, ...]] = (3, 6)

#: Logger of the module that owns the rerun manifest's grammar and its
#: confinement.  A manifest entry this service never sees - one naming a file
#: that is not executable - is reported from there, so a test asserting that it
#: *was* reported has to look for it under that name.
RERUN_REPORT_LOGGER_NAME: Final[str] = rerun_report.__name__

#: The Background's own line in ``Alpha.feature``; its single step sits on the
#: next one.  A Background occurrence is emitted once per scenario and all of
#: them share this line, which is exactly why the merge groups a Background with
#: the scenario it precedes instead of sorting the flat element list.
BACKGROUND_LINE: Final[int] = 4

#: Statuses handed to the canned documents, cycled by scenario line so that a
#: status is a function of the location alone.  Were it a function of the shard,
#: the worker-count-independence comparison could not fail and would prove
#: nothing.
STATUS_CYCLE: Final[tuple[str, ...]] = ("passed", "failed", "skipped")

#: Error text attached to a failing step, so that
#: :func:`~conftest.normalize_volatile`'s ``error_message`` canonicalisation is
#: exercised by the comparison rather than assumed.
FAILURE_TEXT: Final[str] = "AssertionError: the canned shard failed here"

#: A fixed start timestamp for every canned scenario.  Normalized away before
#: any comparison; it exists because the merge derives ``started_at`` from the
#: scenarios when no shard carries a run-level stamp.
CANNED_TIMESTAMP: Final[str] = "2024-01-01T00:00:00.000Z"

#: A fixed generation timestamp for every canned shard document.
CANNED_GENERATED_AT: Final[str] = "2024-01-01T00:00:01.000Z"

#: What a canned shard writes, keyed by shard index in
#: :class:`RecordingSpawn`.  Each value is one of the settled outcomes the
#: service classifies (AAP 0.4.1's dead-worker row): a complete document, no
#: file at all, an empty file, a file that is not JSON, or a launch that fails.
COMPLETE: Final[str] = "complete"
NO_FILE: Final[str] = "no-file"
EMPTY_FILE: Final[str] = "empty-file"
INVALID_JSON: Final[str] = "invalid-json"
LAUNCH_RAISES: Final[str] = "launch-raises"

#: What an ``invalid-json`` shard writes: a truncated document, which is what a
#: worker killed mid-write leaves behind.
TRUNCATED_JSON_TEXT: Final[str] = '{"features": [{'

# --------------------------------------------------------------------------- #
# The worker command line's vocabulary, named once and shared by the seam that
# reads a command and the tests that assert one.
# --------------------------------------------------------------------------- #

#: Selects the result collector; its value is
#: :data:`~app.reporting.events.FORMATTER_SCOPED_NAME`.
FORMAT_FLAG: Final[str] = "--format"

#: Names this shard's output file, the sole path on the command line.
OUTFILE_FLAG: Final[str] = "-o"

#: Carries the user's tag expression, or the neutral tautology.
TAGS_FLAG: Final[str] = "--tags"

#: Carries behave userdata, used only for the browser override.
USERDATA_FLAG: Final[str] = "-D"

#: Asks the engine not to execute steps.
DRY_RUN_FLAG: Final[str] = "--dry-run"


@pytest.fixture(autouse=True)
def isolate_the_run_directory_registry() -> Iterator[None]:
    """Leave the service's registry of live run directories as it was found.

    :func:`~app.services.prepare_workers_dir` records the directory it creates
    in a module-level registry, so that
    :func:`~app.services.cleanup_workers_dir` called with no argument - the
    call ``app/cli.py`` makes on every exit path - removes precisely what this
    *process* created and never another run's.  In production that is one
    invocation per process and the run removes its own directory as it
    finishes.

    A test process is not one invocation.  A test that prepares a directory
    without running a suite leaves an entry behind, and a **later** test's
    no-argument cleanup then tries to remove a directory under a temporary
    root of its own that no longer exists - which the service correctly
    refuses, because the directory is not inside the shared directory *that*
    base names, and reports as a cleanup failure.  The failure surfaces in
    whichever test ran later, ``tests/test_cli.py``'s invocations included,
    where it looks like an exit-status defect.

    The registry is therefore snapshotted and restored around every test in
    this module, which both makes each test independent of the order and stops
    this module from handing the rest of the suite a registry full of paths
    that have been deleted underneath it.

    :yields: ``None`` - the fixture is entirely about the surrounding state.
    """
    with service._active_run_dirs_lock:
        snapshot = set(service._active_run_dirs)
    try:
        yield
    finally:
        with service._active_run_dirs_lock:
            service._active_run_dirs.clear()
            service._active_run_dirs.update(snapshot)


@pytest.fixture
def feature_tree(tmp_artifact_root: Path) -> Path:
    """Write :data:`FEATURE_TREE` into a temporary checkout root.

    The whole of this module's filesystem footprint, apart from the one test
    that reads the real ``features/`` directory: every other path is derived
    from this root through ``app/utils/paths.py``, so no test writes into the
    repository's own ``target/``.

    Args:
        tmp_artifact_root: The per-test temporary checkout root from
            ``tests/conftest.py``.

    Returns:
        The root, ready to pass as ``base=``.  Its ``features`` directory is
        created through :func:`app.utils.paths.ensure_dir` so the fixture uses
        the same code path production does.
    """
    directory = paths.ensure_dir(paths.features_dir(tmp_artifact_root))
    for feature in FEATURE_TREE:
        (directory / feature.filename).write_text(feature.text, encoding="utf-8")
    return tmp_artifact_root


def all_locations() -> tuple[str, ...]:
    """Return every location of the temporary tree, in canonical order.

    Returns:
        The six ``path:line`` strings, ordered by feature path and then by
        line - the order :func:`~app.services.test_run_service.select_scenarios`
        imposes and the order the round-robin walk expects.
    """
    return tuple(
        f"{feature.feature_path}{rerun_report.LINE_SEPARATOR}{line}"
        for feature, lines in (
            (ALPHA_FEATURE, ALPHA_LOCATION_LINES),
            (BETA_FEATURE, BETA_LOCATION_LINES),
        )
        for line in lines
    )


def trailing_locations(arguments: Sequence[str]) -> tuple[str, ...]:
    """Return the location arguments at the end of a worker command line.

    Read from the right, stopping at the first argument that is not
    ``<path>:<line>``: the locations are the engine's positional arguments, so
    nothing follows them, and recovering them this way keeps the seam usable
    for a command naming a location the temporary tree does not declare - a
    rerun manifest's stale entry, say.

    Args:
        arguments: A whole worker command line.

    Returns:
        The trailing locations, in the order they appear.
    """
    locations: list[str] = []
    for argument in reversed(list(arguments)):
        path, separator, line = argument.rpartition(rerun_report.LINE_SEPARATOR)
        if not separator or not path or not line.isdigit():
            break
        locations.append(argument)
    return tuple(reversed(locations))


def split_location(location: str) -> tuple[str, int]:
    """Split a ``path:line`` location into its two parts.

    Args:
        location: A location in behave's own syntax.

    Returns:
        The feature path and the line number.
    """
    path, _, line = location.rpartition(rerun_report.LINE_SEPARATOR)
    return path, int(line)


def status_of(location: str) -> str:
    """Return the canned status of one location.

    Args:
        location: The scenario's location.

    Returns:
        A member of :data:`STATUS_CYCLE`, chosen by the scenario's line so that
        the value depends on the scenario and not on which shard ran it.
    """
    _, line = split_location(location)
    return STATUS_CYCLE[line % len(STATUS_CYCLE)]


def title_of(feature_path: str) -> str:
    """Return the title of the temporary tree's feature at ``feature_path``.

    Args:
        feature_path: A repository-relative feature path.

    Returns:
        The ``Feature:`` title, or the path itself for a feature the tree does
        not declare - which keeps the canned-document builder usable for a
        rerun manifest naming a file that is not there.
    """
    for feature in FEATURE_TREE:
        if feature.feature_path == feature_path:
            return feature.title
    return feature_path


def worker_document(locations: Sequence[str]) -> dict[str, Any]:
    """Build the result document a worker running ``locations`` would write.

    Assembled exclusively from ``app/reporting/events.py``'s builders, which
    own the port's internal schema (AAP 0.6), so this module cannot drift from
    the shape a real worker produces: a Background occurrence per scenario,
    immediately before it; an outline row keyed by the data row's line; and a
    step whose ``result`` carries the location's canned status.

    Args:
        locations: The shard's locations, in any order.

    Returns:
        A complete result set, with features in ascending path order and
        elements in ascending line order within a feature.
    """
    grouped: dict[str, list[str]] = {}
    for location in locations:
        feature_path, _ = split_location(location)
        grouped.setdefault(feature_path, []).append(location)

    features: list[dict[str, Any]] = []
    for feature_path in sorted(grouped):
        elements: list[dict[str, Any]] = []
        ordered = sorted(
            grouped[feature_path], key=lambda item: split_location(item)[1]
        )
        for location in ordered:
            _, line = split_location(location)
            status = status_of(location)
            result: dict[str, Any] = {"status": status}
            if status != "skipped":
                # A skipped step carries no duration key at all, which is the
                # JVM distinction AAP 0.6 names and normalization preserves.
                result["duration"] = line * 1_000_000
            if status == "failed":
                result["error_message"] = FAILURE_TEXT
            elements.append(
                events.new_element(
                    element_type=events.ELEMENT_TYPE_BACKGROUND,
                    keyword="Background",
                    line=BACKGROUND_LINE,
                    name="shared setup",
                    steps=[
                        events.new_step(
                            keyword="Given",
                            line=BACKGROUND_LINE + 1,
                            name="a precondition",
                            matched=True,
                            result={"status": "passed", "duration": 1_000_000},
                        )
                    ],
                )
            )
            elements.append(
                events.new_element(
                    element_type=events.ELEMENT_TYPE_SCENARIO,
                    keyword="Scenario",
                    line=line,
                    name=f"scenario at {location}",
                    identifier=events.convert_to_id(location),
                    start_timestamp=CANNED_TIMESTAMP,
                    steps=[
                        events.new_step(
                            keyword="Given",
                            line=line + 1,
                            name="a precondition",
                            matched=True,
                            result=result,
                        )
                    ],
                )
            )
        features.append(
            events.new_feature(
                uri=f"{paths.FILE_URI_SCHEME}{feature_path}",
                path=feature_path,
                identifier=events.convert_to_id(title_of(feature_path)),
                line=2,
                name=title_of(feature_path),
                elements=elements,
            )
        )

    return events.new_result_set(
        features=features,
        started_at=CANNED_TIMESTAMP,
        generated_at=CANNED_GENERATED_AT,
    )


@dataclass(frozen=True)
class SpawnCall:
    """One recorded launch.

    Attributes:
        command: The argument list :func:`build_worker_command` produced.
        cwd: The working directory the service asked for, which has to be the
            run base so that ``behave.ini``, ``features/environment.py`` and
            ``configuration.properties`` are all discovered from it.
        shard_index: Which shard this was, recovered from the ``-o`` path
            through :func:`app.utils.paths.worker_result_path` rather than by
            counting calls, because shards run concurrently.
        output_path: The ``-o`` value.
        locations: The locations this launch was given.
    """

    command: tuple[str, ...]
    cwd: Path
    shard_index: int
    output_path: Path
    locations: tuple[str, ...]


@dataclass
class FakeProcess:
    """The three attributes :func:`run_suite` reads off a completed worker.

    Structurally compatible with :class:`subprocess.CompletedProcess`, which is
    what the ``spawn`` seam's :class:`~app.services.test_run_service.WorkerProcess`
    protocol requires - and deliberately nothing more, so a test cannot come to
    depend on how the real launch captures output.

    Attributes:
        returncode: The engine's status.  A **positive** non-zero value is
            normal: behave exits ``1`` when scenarios fail, and
            ``pom.xml:25``'s ``testFailureIgnore=true`` forbids treating that
            as an error.
        stdout: Progress text.  Relayed after the fact, at ``INFO`` **or
            above** - the stream supplies a floor, and a line whose own
            ``LOG_`` token names a higher level is emitted at that level.
        stderr: Diagnostic text, relayed under the same rule with a
            ``WARNING`` floor.

    Notes:
        This stub carries **no** ``output_relayed`` attribute, which is what
        puts it on the after-the-fact relay path: the real launch relays its
        output line by line as it arrives and marks itself so that nothing is
        printed twice.  :class:`RelayedProcess` is the marked counterpart.
    """

    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


@dataclass
class RelayedProcess(FakeProcess):
    """A completed worker whose output was already relayed live.

    What the real launch returns.  The marker is an *optional convention* read
    with :func:`getattr` rather than a member of the
    :class:`~app.services.test_run_service.WorkerProcess` protocol, precisely
    so that a plain :class:`subprocess.CompletedProcess` still satisfies that
    protocol - so the stub has to carry it as an ordinary attribute, exactly as
    the launch does.

    Attributes:
        output_relayed: Always ``True``.  Every line has already reached the
            parent log as it arrived, so the after-the-fact relay prints
            nothing for this process; printing the retained tail would show
            those lines twice.
    """

    output_relayed: bool = True


@dataclass
class RecordingSpawn:
    """A launch seam that records its argv and writes a canned worker document.

    The stand-in for the engine everywhere ``run_suite`` is exercised.  It
    starts no process, waits on nothing and imposes no timeout, so a whole
    orchestration matrix runs in milliseconds.

    Attributes:
        base: The run base, used to recover a shard index from its output path
            through the public path accessor.
        behaviour: Per-shard-index override drawn from :data:`COMPLETE`,
            :data:`NO_FILE`, :data:`EMPTY_FILE`, :data:`INVALID_JSON` and
            :data:`LAUNCH_RAISES`; an index not named behaves as
            :data:`COMPLETE`.
        returncodes: Per-shard-index return code; an index not named exits
            ``0``.
        stdout: Text every launch reports as progress.
        stderr: Text every launch reports as diagnostics.
        relayed: Whether each launch reports its output as **already relayed**,
            which is what the real launch does.  The default is ``False``, so a
            plain recording spawn is relayed after the fact exactly as a
            stubbed :class:`subprocess.CompletedProcess` is.
        calls: Every launch, in completion order.
    """

    base: Path
    behaviour: dict[int, str] = field(default_factory=dict)
    returncodes: dict[int, int] = field(default_factory=dict)
    stdout: str = ""
    stderr: str = ""
    relayed: bool = False
    calls: list[SpawnCall] = field(default_factory=list)

    def __call__(self, command: Sequence[str], cwd: Path) -> FakeProcess:
        """Record the launch, write what this shard was told to write.

        Args:
            command: The argument list under test.
            cwd: The working directory the service chose.

        Returns:
            A :class:`FakeProcess` carrying this shard's return code.

        Raises:
            OSError: When this shard's behaviour is :data:`LAUNCH_RAISES`,
                which is the documented launch-failure case ``_run_one_shard``
                turns into a dead shard rather than an exception.
        """
        arguments = list(command)
        output_path = Path(arguments[arguments.index(OUTFILE_FLAG) + 1])
        index = self.shard_index_of(output_path)
        locations = trailing_locations(arguments)
        self.calls.append(
            SpawnCall(
                command=tuple(arguments),
                cwd=Path(cwd),
                shard_index=index,
                output_path=output_path,
                locations=locations,
            )
        )

        behaviour = self.behaviour.get(index, COMPLETE)
        if behaviour == LAUNCH_RAISES:
            raise OSError(f"canned launch failure for shard {index}")
        if behaviour == COMPLETE:
            events.dump_result_set(worker_document(locations), output_path)
        elif behaviour == EMPTY_FILE:
            output_path.write_text("", encoding="utf-8")
        elif behaviour == INVALID_JSON:
            output_path.write_text(TRUNCATED_JSON_TEXT, encoding="utf-8")
        elif behaviour != NO_FILE:  # pragma: no cover - guards a typo in a test
            raise AssertionError(f"unknown canned behaviour {behaviour!r}")

        completed = RelayedProcess if self.relayed else FakeProcess
        return completed(
            returncode=self.returncodes.get(index, 0),
            stdout=self.stdout,
            stderr=self.stderr,
        )

    def shard_index_of(self, output_path: Path) -> int:
        """Recover a shard index from the output path the service chose.

        Two things are established at once, and both are the contract rather
        than this helper's convenience.

        The **file name** is resolved through
        :func:`app.utils.paths.worker_result_path`, the public accessor that
        owns the pid-plus-index naming rule, so this helper depends on no
        private filename template and a service that invented a name of its
        own would be caught here.  The search is bounded by the tree's size
        because a shard is never given an empty share of the work, so there
        can never be more shards than scenarios.

        The **directory** is this run's own, a direct child of the shared
        intermediate directory :func:`app.utils.paths.workers_dir` names, whose
        name :func:`~app.services.run_directory_owner` reads back as a run
        directory.  That is the confinement the per-run design exists for: no
        shard's output path can name a file another run wrote, which is what a
        single shared directory could not guarantee.

        Args:
            output_path: The ``-o`` value from one launch.

        Returns:
            The zero-based shard index.

        Raises:
            AssertionError: If the name is not one the accessor produces, or
                the directory is not a run directory inside the shared one -
                either of which would mean the service invented a path of its
                own.
        """
        shared_root = paths.workers_dir(self.base)
        run_dir = output_path.parent
        assert run_dir.parent == shared_root, (
            f"{output_path} is not inside a run directory under {shared_root}"
        )
        assert service.run_directory_owner(run_dir.name) is not None, (
            f"{run_dir.name} is not a name prepare_workers_dir produces"
        )

        for index in range(len(all_locations()) + 1):
            if paths.worker_result_path(index, base=self.base).name == (
                output_path.name
            ):
                return index
        raise AssertionError(
            f"{output_path} is not a path app.utils.paths.worker_result_path produced"
        )

    def calls_by_shard(self) -> dict[int, SpawnCall]:
        """Return the recorded launches keyed by shard index.

        Returns:
            One entry per shard; shards run concurrently, so completion order
            carries no meaning and every assertion keys on the index.
        """
        return {call.shard_index: call for call in self.calls}


def selected_locations(**kwargs: Any) -> list[str]:
    """Return the locations :func:`select_scenarios` selects.

    Args:
        **kwargs: Passed straight through to
            :func:`~app.services.test_run_service.select_scenarios`.

    Returns:
        The selected units' locations, in the order the function returned them.
    """
    selected, _ = service.select_scenarios(**kwargs)
    return [unit.location for unit in selected]


# =========================================================================== #
# Invariant 1 - exact-once assignment (AAP 0.6)
#
# "Every selected scenario is assigned to exactly one worker."  Asserted as
# four separate properties, because a single set comparison can hide three of
# them: the union equals the selection, the shards are pairwise disjoint, no
# shard is empty, and the assignment is deterministic.
# =========================================================================== #

#: Worker counts every assignment property is asserted at.  ``1`` is the
#: sequential mode of AAP 0.4.1's ``--workers 1``; ``7`` is more workers than
#: there are scenarios, which is what the clamp in ``_effective_worker_count``
#: exists for.
ASSIGNMENT_WORKER_COUNTS: Final[tuple[int, ...]] = (1, 2, 3, 5, 7)


@pytest.mark.parametrize("workers", ASSIGNMENT_WORKER_COUNTS)
def test_every_selected_scenario_is_assigned_to_exactly_one_shard(
    feature_tree: Path, workers: int
) -> None:
    """The union of the shards is the selection, with nothing duplicated.

    AAP 0.6's first invariant, stated as a multiset comparison rather than a set
    one: a set union would be satisfied by a plan that handed the same scenario
    to two workers, which is exactly the failure ``--no-skipped`` and the
    round-robin walk exist to prevent.
    """
    selected, problems = service.select_scenarios(base=feature_tree)
    assert problems == []

    plans = service.shard_scenarios(selected, workers, base=feature_tree)
    assigned = [location for plan in plans for location in plan.locations]

    assert sorted(assigned) == sorted(unit.location for unit in selected)
    assert len(assigned) == len(set(assigned))
    assert set(assigned) == set(all_locations())


@pytest.mark.parametrize("workers", ASSIGNMENT_WORKER_COUNTS)
def test_shards_are_pairwise_disjoint_and_never_empty(
    feature_tree: Path, workers: int
) -> None:
    """No scenario is shared between shards and no worker is given nothing.

    ``ShardPlan.locations`` is documented as never empty - "a worker is never
    spawned with nothing to do" - which is what makes the worker count the
    service reports meaningful when it is smaller than the one requested.
    """
    selected, _ = service.select_scenarios(base=feature_tree)
    plans = service.shard_scenarios(selected, workers, base=feature_tree)

    for plan in plans:
        assert plan.locations, f"shard {plan.index} was given no scenarios"

    for left in plans:
        for right in plans:
            if left.index < right.index:
                assert not set(left.locations) & set(right.locations)


@pytest.mark.parametrize("workers", ASSIGNMENT_WORKER_COUNTS)
def test_assignment_is_deterministic_for_a_fixed_input(
    feature_tree: Path, workers: int
) -> None:
    """Two shardings of one selection produce identical plans.

    Determinism is the precondition of AAP 0.6's second invariant: a merge
    cannot be worker-count independent if the same worker count does not even
    agree with itself.  ``shard_scenarios`` imposes the order itself rather than
    assuming the caller's, so the input is deliberately shuffled here.
    """
    selected, _ = service.select_scenarios(base=feature_tree)
    reversed_selection = list(reversed(selected))

    first = service.shard_scenarios(selected, workers, base=feature_tree)
    second = service.shard_scenarios(reversed_selection, workers, base=feature_tree)

    assert [plan.locations for plan in first] == [plan.locations for plan in second]
    assert [plan.index for plan in first] == list(range(len(first)))


def test_one_worker_yields_a_single_shard_holding_everything(
    feature_tree: Path,
) -> None:
    """``--workers 1`` is the sequential mode: one shard, every scenario.

    AAP 0.1.3's Conflict 4 keeps the specification's sequential mode reachable
    at ``--workers 1``, and this is the shape that makes it so - one shard, no
    pool, the whole selection in source order.
    """
    selected, _ = service.select_scenarios(base=feature_tree)
    plans = service.shard_scenarios(selected, 1, base=feature_tree)

    assert len(plans) == 1
    assert plans[0].index == 0
    assert plans[0].locations == all_locations()


def test_zero_selected_scenarios_yields_an_empty_plan_list(
    feature_tree: Path,
) -> None:
    """Nothing selected shards into nothing, without raising.

    The plan list is empty rather than a list of empty shards, which is what
    lets ``run_suite`` spawn no worker at all for AAP 0.4.1's "zero scenarios
    selected" row.
    """
    assert service.shard_scenarios([], 4, base=feature_tree) == []
    assert service.shard_scenarios((), 1, base=feature_tree) == []


def test_duplicate_locations_are_collapsed_before_assignment(
    feature_tree: Path,
) -> None:
    """A location handed in twice is executed once.

    Handing the engine one location twice runs the scenario twice and puts two
    copies of it in the report, which breaks the exact-once invariant from the
    input side rather than from the algorithm's.
    """
    selected, _ = service.select_scenarios(base=feature_tree)
    doubled = list(selected) + list(selected)

    plans = service.shard_scenarios(doubled, 3, base=feature_tree)
    assigned = [location for plan in plans for location in plan.locations]

    assert sorted(assigned) == sorted(all_locations())


def test_shard_output_paths_come_from_paths_and_are_distinct(
    feature_tree: Path,
) -> None:
    """Each shard's output path is the paths module's, unique, and under .workers.

    AAP 0.4.1 puts every per-worker intermediate under ``target/.workers/`` with
    a name unique per worker by process id and shard index, and the leading dot
    is what keeps the directory unreachable through the HTTP artifact route
    (AAP 0.3.1).  The path is computed in the parent, which is what lets the
    merge tell an absent file from an unreadable one.
    """
    selected, _ = service.select_scenarios(base=feature_tree)
    plans = service.shard_scenarios(selected, 3, base=feature_tree)

    expected = [
        paths.worker_result_path(plan.index, base=feature_tree) for plan in plans
    ]
    assert [plan.output_path for plan in plans] == expected
    assert len({plan.output_path for plan in plans}) == len(plans)

    workers_directory = paths.workers_dir(feature_tree)
    for plan in plans:
        assert plan.output_path.parent == workers_directory


def test_worker_locations_stay_grouped_by_feature_and_ascending_by_line(
    feature_tree: Path,
) -> None:
    """Each worker's share is grouped by feature and ascending within a feature.

    **Pinned against the implementation, deliberately.**  The round-robin is per
    *scenario*, not per feature: ``pom.xml:22``'s ``parallel=methods`` is
    method- and therefore scenario-level, so a feature *is* split across shards
    and no test here may assert otherwise.  What the implementation guarantees,
    and what AAP 0.6 actually requires, is the weaker and sufficient property
    asserted below - a worker's runs of a given feature are contiguous and in
    line order, so that feature's Background executes once per scenario inside
    the worker that runs it, never split across workers and never shared.
    """
    selected, _ = service.select_scenarios(base=feature_tree)
    plans = service.shard_scenarios(selected, 2, base=feature_tree)

    split_features = {
        feature_path
        for feature_path in {split_location(item)[0] for item in all_locations()}
        if sum(
            1
            for plan in plans
            if any(split_location(item)[0] == feature_path for item in plan.locations)
        )
        > 1
    }
    assert split_features, (
        "the two-worker plan did not split a feature, so this test would pass "
        "vacuously against a feature-level round-robin"
    )

    for plan in plans:
        feature_order = [split_location(item)[0] for item in plan.locations]
        # One contiguous run per feature, the runs themselves in canonical
        # order: ``groupby`` collapses each run to one key, so a feature that
        # reappeared after another had intervened would show up twice here.
        runs = [key for key, _ in itertools.groupby(feature_order)]
        assert runs == sorted(set(feature_order))
        for feature_path in set(feature_order):
            lines = [
                split_location(item)[1]
                for item in plan.locations
                if split_location(item)[0] == feature_path
            ]
            assert lines == sorted(lines)


# =========================================================================== #
# Worker bounds (AAP 0.4.1's --workers row, AAP deviation 4)
# =========================================================================== #


def test_default_worker_count_is_the_cpu_count() -> None:
    """The default pool size is the CPU count, the stand-in for uncapped threads.

    ``pom.xml:23``'s ``useUnlimitedThreads=true`` cannot be mirrored by a
    process pool, and AAP deviation 4 records the CPU count as what replaces it
    rather than claiming equivalence.
    """
    assert service.default_worker_count() == (os.cpu_count() or 1)


def test_default_worker_count_falls_back_to_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A platform that cannot report a CPU count yields one worker.

    :func:`os.cpu_count` is documented to return ``None`` when the count is
    indeterminable, and a ``None`` pool size would shard into nothing.
    """
    monkeypatch.setattr(os, "cpu_count", lambda: None)
    assert service.default_worker_count() == 1

    monkeypatch.setattr(os, "cpu_count", lambda: 7)
    assert service.default_worker_count() == 7


def test_explicit_worker_count_overrides_the_default(
    feature_tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--workers N`` decides the pool size, not the CPU count.

    Asserted with the CPU count pinned high, so that the explicit value is
    visibly the thing being honoured rather than coinciding with the default.
    """
    monkeypatch.setattr(os, "cpu_count", lambda: 64)
    selected, _ = service.select_scenarios(base=feature_tree)

    assert len(service.shard_scenarios(selected, 2, base=feature_tree)) == 2
    assert len(service.shard_scenarios(selected, 3, base=feature_tree)) == 3


def test_worker_count_is_bounded_by_the_scenarios_available(
    feature_tree: Path,
) -> None:
    """The shard count stays between one and the number of scenarios.

    Stated as a range rather than an equality on purpose: the lower bound and
    the "never more shards than scenarios" upper bound are the contract - a
    worker is never handed an empty shard - while the exact number a large
    request resolves to is the service's own clamping decision.
    """
    selected, _ = service.select_scenarios(base=feature_tree)
    available = len(selected)

    for requested in (1, 2, 3, 5, 7, 1_000):
        plans = service.shard_scenarios(selected, requested, base=feature_tree)
        assert 1 <= len(plans) <= min(requested, available)

    for requested in (1, 2, 3):
        assert len(service.shard_scenarios(selected, requested, base=feature_tree)) == (
            requested
        )


def test_a_non_positive_worker_count_is_treated_as_unspecified(
    feature_tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zero and negative counts fall back to the default rather than raising.

    Validating an option value belongs to ``app/cli.py`` per AAP 0.4.1; the
    service stays tolerant, and tolerance here means a spawnable pool rather
    than an empty one.
    """
    monkeypatch.setattr(os, "cpu_count", lambda: 2)
    selected, _ = service.select_scenarios(base=feature_tree)

    for requested in (0, -4):
        plans = service.shard_scenarios(selected, requested, base=feature_tree)
        assert len(plans) == 2
        assert sorted(
            location for plan in plans for location in plan.locations
        ) == sorted(all_locations())


# =========================================================================== #
# Invariant 2 - worker-count independence (AAP 0.6)
#
# "For a fixed set of shard inputs the merged structure - feature order,
# scenario order, background position, statuses - is identical whatever the
# worker count", with timestamps, durations and error text normalized before
# comparison since those vary by construction.  Every test in this section
# drives the whole of ``run_suite`` through the ``spawn`` seam, so the shard
# files are real files written by a real document builder and read back by the
# real merge.
# =========================================================================== #

#: The worker counts the merged structure is compared across: sequential, an
#: even split, and more workers than one feature has scenarios - which is the
#: count that splits a feature across shards and therefore the one that would
#: expose a merge keyed on anything but the feature path.
MERGE_WORKER_COUNTS: Final[tuple[int, ...]] = (1, 2, 4)


def run_with_workers(
    base: Path, workers: int, **kwargs: Any
) -> tuple[service.RunOutcome, RecordingSpawn]:
    """Run the whole suite at one worker count through the recording seam.

    Args:
        base: The temporary checkout root.
        workers: The worker count to run at.
        **kwargs: Further keyword arguments for
            :func:`~app.services.test_run_service.run_suite`.

    Returns:
        The outcome and the seam that recorded the launches.
    """
    spawn = RecordingSpawn(base=base)
    outcome = service.run_suite(workers=workers, base=base, spawn=spawn, **kwargs)
    return outcome, spawn


def scenario_elements(feature: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a merged feature's scenario elements, in document order.

    Args:
        feature: A feature object from a merged document.

    Returns:
        Its elements of type ``scenario``, Backgrounds excluded.
    """
    return [
        element
        for element in feature["elements"]
        if element["type"] == events.ELEMENT_TYPE_SCENARIO
    ]


def test_merged_structure_is_identical_whatever_the_worker_count(
    feature_tree: Path, normalize_volatile: Any
) -> None:
    """The merged document is structurally identical at one, two and four workers.

    AAP 0.6's second invariant, asserted the way that section prescribes:
    normalized first - ``start_timestamp``, ``started_at``, ``generated_at``,
    measured durations and failure text all vary by construction - and never
    compared byte for byte, which the same section forbids outright.
    """
    documents: dict[int, Any] = {}
    for workers in MERGE_WORKER_COUNTS:
        outcome, spawn = run_with_workers(feature_tree, workers)
        assert outcome.dead_shards == ()
        assert len(spawn.calls) == outcome.worker_count
        assert outcome.result_set is not None
        documents[workers] = normalize_volatile(outcome.result_set)

    baseline = documents[MERGE_WORKER_COUNTS[0]]
    for workers in MERGE_WORKER_COUNTS[1:]:
        assert documents[workers] == baseline, (
            f"the merged structure at {workers} workers differs from the "
            f"sequential one"
        )


def test_merged_feature_order_is_source_order_not_name_order(
    feature_tree: Path,
) -> None:
    """Features come out in ascending path order, never sorted by title.

    AAP 0.6 requires features in *source* order, which the merge reproduces as
    ascending feature path - behave's own discovery order, and the only ordering
    independent of how the scenarios were sharded.  The tree's two titles sort
    in the reverse of their filenames precisely so this assertion can tell the
    two orders apart, and ``sortingMethod: 'ALPHABETICAL'`` at ``Jenkins:15`` is
    a publisher display option that imposes nothing on the artifact.
    """
    expected_paths = [feature.feature_path for feature in FEATURE_TREE]
    assert expected_paths == sorted(expected_paths)
    assert [feature.title for feature in FEATURE_TREE] != sorted(
        feature.title for feature in FEATURE_TREE
    )

    for workers in MERGE_WORKER_COUNTS:
        outcome, _ = run_with_workers(feature_tree, workers)
        assert outcome.result_set is not None
        features = outcome.result_set["features"]
        assert [feature["path"] for feature in features] == expected_paths
        assert [feature["name"] for feature in features] != sorted(
            feature["name"] for feature in features
        )


def test_merged_scenarios_are_in_line_order_within_each_feature(
    feature_tree: Path,
) -> None:
    """Scenario order is line order, whatever shard each scenario ran in.

    The other half of AAP 0.6's ordering requirement.  At four workers the
    scenarios of ``Alpha.feature`` are spread across every shard, so the order
    below is produced by the merge rather than inherited from one worker's
    document.
    """
    expected = {
        ALPHA_FEATURE.feature_path: list(ALPHA_LOCATION_LINES),
        BETA_FEATURE.feature_path: list(BETA_LOCATION_LINES),
    }

    for workers in MERGE_WORKER_COUNTS:
        outcome, _ = run_with_workers(feature_tree, workers)
        assert outcome.result_set is not None
        for feature in outcome.result_set["features"]:
            lines = [element["line"] for element in scenario_elements(feature)]
            assert lines == expected[feature["path"]]


def test_each_background_sits_immediately_before_its_scenario(
    feature_tree: Path,
) -> None:
    """A Background occurrence is never separated from the scenario it ran for.

    AAP 0.6 names the Background's position as part of the structure that must
    be deterministic.  Every occurrence of a given Background shares the
    Background's own line, so ordering the flat element list by line would
    collect them all at the front; the merge groups each with its scenario
    instead, and that grouping has to survive a shard boundary that falls
    between two scenarios of the same feature.
    """
    for workers in MERGE_WORKER_COUNTS:
        outcome, _ = run_with_workers(feature_tree, workers)
        assert outcome.result_set is not None

        alpha = next(
            feature
            for feature in outcome.result_set["features"]
            if feature["path"] == ALPHA_FEATURE.feature_path
        )
        types = [element["type"] for element in alpha["elements"]]
        assert types == [
            events.ELEMENT_TYPE_BACKGROUND,
            events.ELEMENT_TYPE_SCENARIO,
        ] * len(ALPHA_LOCATION_LINES)
        for position, element in enumerate(alpha["elements"]):
            if element["type"] == events.ELEMENT_TYPE_BACKGROUND:
                assert element["line"] == BACKGROUND_LINE
                following = alpha["elements"][position + 1]
                assert following["type"] == events.ELEMENT_TYPE_SCENARIO


def test_statuses_survive_the_merge_at_every_worker_count(
    feature_tree: Path,
) -> None:
    """Every scenario's step status is the one its shard recorded.

    A merge that lost or reassigned a status would satisfy every ordering
    assertion above while reporting the wrong outcome, so the statuses are
    asserted against the per-location mapping the canned documents were built
    from - which depends on the scenario alone and never on its shard.
    """
    expected = {
        location: status_of(location) for location in all_locations()
    }

    for workers in MERGE_WORKER_COUNTS:
        outcome, _ = run_with_workers(feature_tree, workers)
        assert outcome.result_set is not None

        observed: dict[str, str] = {}
        for feature in outcome.result_set["features"]:
            for element in scenario_elements(feature):
                location = (
                    f"{feature['path']}{rerun_report.LINE_SEPARATOR}{element['line']}"
                )
                statuses = {step["result"]["status"] for step in element["steps"]}
                assert len(statuses) == 1
                observed[location] = statuses.pop()

        assert observed == expected


def test_a_feature_split_across_shards_merges_into_one_feature_object(
    feature_tree: Path,
) -> None:
    """Two shards holding scenarios of one feature yield one feature object.

    The merge is keyed on the feature path (AAP 0.6), so a feature the
    scenario-level round-robin split across workers comes back as a single
    object whose elements are the union of the shards' - each scenario present
    exactly once.  The split is asserted first, so the test cannot pass
    vacuously.
    """
    selected, _ = service.select_scenarios(base=feature_tree)
    plans = service.shard_scenarios(selected, 4, base=feature_tree)
    holders = [
        plan.index
        for plan in plans
        if any(
            split_location(location)[0] == ALPHA_FEATURE.feature_path
            for location in plan.locations
        )
    ]
    assert len(holders) > 1

    outcome, spawn = run_with_workers(feature_tree, 4)
    assert outcome.result_set is not None
    features = outcome.result_set["features"]

    assert len(features) == len(FEATURE_TREE)
    assert [feature["path"] for feature in features] == sorted(
        {split_location(location)[0] for location in all_locations()}
    )
    alpha = next(
        feature
        for feature in features
        if feature["path"] == ALPHA_FEATURE.feature_path
    )
    assert [element["line"] for element in scenario_elements(alpha)] == list(
        ALPHA_LOCATION_LINES
    )

    # And the shards really did write separate documents for it.
    alpha_writers = [
        call.shard_index
        for call in spawn.calls
        if any(
            split_location(location)[0] == ALPHA_FEATURE.feature_path
            for location in call.locations
        )
    ]
    assert sorted(alpha_writers) == sorted(holders)


def test_run_level_fields_of_the_merged_document_are_worker_count_independent(
    feature_tree: Path,
) -> None:
    """``tag_expression`` and ``dry_run`` are set once, identically, per run.

    The two run-level fields the merge cannot know are stamped by ``run_suite``
    after it, and the tag expression recorded is the user's - never the neutral
    tautology that suppresses ``behave.ini``'s ``default_tags``, which is a
    mechanism and must not surface in a report.
    """
    for workers in MERGE_WORKER_COUNTS:
        outcome, _ = run_with_workers(
            feature_tree, workers, tags="@Alpha", dry_run=True
        )
        assert outcome.result_set is not None
        assert outcome.result_set["tag_expression"] == "@Alpha"
        assert outcome.result_set["dry_run"] is True
        assert service.NEUTRAL_TAG_EXPRESSION not in str(outcome.result_set)


# =========================================================================== #
# Selection (AAP 0.6's tag semantics, AAP 0.4.1's --tags row)
#
# The grammar is exercised against the temporary tree, whose tags this module
# declares line by line.  The one claim about the *real* suite - that the
# ``@Smoke`` default selects the CRM feature alone - is asserted against the
# real ``features/`` directory, because it is a fact about the suite that
# ``CukesRunner.java:18`` and ``behave.ini``'s ``default_tags`` both depend on.
# =========================================================================== #

#: The CRM feature's file name, the one feature of the real suite carrying a
#: feature-level tag (``@Smoke`` at ``Crm.feature:1``).
CRM_FEATURE_FILENAME: Final[str] = "Crm.feature"

#: The exact locations ``--tags @Smoke`` selects from the real suite, measured
#: against the tree: the plain scenario at line 9, the outline's two Examples
#: rows at 24 and 26, and the scenario at 31.  ``@Smoke`` is declared exactly
#: once, at ``Crm.feature:1``, and propagates onto every scenario of that
#: feature (AAP 0.6).
SMOKE_LOCATION_LINES: Final[tuple[int, ...]] = (9, 24, 26, 31)

#: The five features of the real suite that carry no tag at all - AAP 0.6 names
#: them as unreachable by a positive expression over feature tags and perfectly
#: reachable by a negative one.  Source behaviour, reproduced rather than fixed.
UNTAGGED_FEATURE_FILENAMES: Final[tuple[str, ...]] = (
    "Contact.feature",
    "Inventory.feature",
    "Notes.feature",
    "Sales.feature",
    "Session.feature",
)

#: The default tag expression ``app/cli.py`` passes, from
#: ``CukesRunner.java:18`` by way of ``behave.ini``'s ``default_tags``.
SMOKE_TAG: Final[str] = "@Smoke"


def write_feature(base: Path, filename: str, text: str) -> Path:
    """Write one extra feature file into a tree's features directory.

    Args:
        base: The temporary checkout root.
        filename: The file's name, which becomes the last segment of the
            repository-relative feature path.
        text: The Gherkin source, written verbatim.

    Returns:
        The file written.
    """
    directory = paths.ensure_dir(paths.features_dir(base))
    path = directory / filename
    path.write_text(text, encoding="utf-8")
    return path


def test_a_bare_tag_selects_the_features_and_scenarios_carrying_it(
    feature_tree: Path,
) -> None:
    """``@Alpha`` selects every unit whose effective tags include it.

    The full ``cucumber-tag-expressions`` grammar is supported, matching the
    JVM's ``tag-expressions:4.1.0`` (AAP 0.6).  A feature-level tag propagates
    onto every unit of its feature, which is why all four of ``Alpha.feature``'s
    units answer to the feature's own tag.
    """
    alpha_locations = [
        location
        for location in all_locations()
        if split_location(location)[0] == ALPHA_FEATURE.feature_path
    ]

    assert selected_locations(tags="@Alpha", base=feature_tree) == alpha_locations
    assert selected_locations(tags="@Beta", base=feature_tree) == [
        f"{ALPHA_FEATURE.feature_path}{rerun_report.LINE_SEPARATOR}11"
    ]


def test_an_examples_block_tag_selects_its_rows_only(feature_tree: Path) -> None:
    """A tag on an Examples block reaches that block's generated rows.

    An outline expands into one executable unit per data row, each carrying the
    Examples block's tags as well as the outline's and the feature's, and each
    keyed by the **data row's** line rather than the outline header's (AAP 0.6).
    """
    assert selected_locations(tags="@Gamma", base=feature_tree) == [
        f"{ALPHA_FEATURE.feature_path}{rerun_report.LINE_SEPARATOR}20",
        f"{ALPHA_FEATURE.feature_path}{rerun_report.LINE_SEPARATOR}21",
    ]


def test_conjunction_with_negation_selects_the_documented_grammar(
    feature_tree: Path,
) -> None:
    """``@Alpha and not @Beta`` parses and evaluates as the JVM's grammar did.

    The compound form AAP 0.6 names verbatim (``@Login and not @wip``).
    """
    assert selected_locations(tags="@Alpha and not @Beta", base=feature_tree) == [
        f"{ALPHA_FEATURE.feature_path}{rerun_report.LINE_SEPARATOR}7",
        f"{ALPHA_FEATURE.feature_path}{rerun_report.LINE_SEPARATOR}20",
        f"{ALPHA_FEATURE.feature_path}{rerun_report.LINE_SEPARATOR}21",
    ]


def test_disjunction_selects_the_union(feature_tree: Path) -> None:
    """``@Beta or @Gamma`` selects both operands' units.

    The other compound form AAP 0.6 names (``@UPGN-286 or @UPGN-287``).
    """
    assert selected_locations(tags="@Beta or @Gamma", base=feature_tree) == [
        f"{ALPHA_FEATURE.feature_path}{rerun_report.LINE_SEPARATOR}11",
        f"{ALPHA_FEATURE.feature_path}{rerun_report.LINE_SEPARATOR}20",
        f"{ALPHA_FEATURE.feature_path}{rerun_report.LINE_SEPARATOR}21",
    ]


def test_a_negated_tag_reaches_the_untagged_feature(feature_tree: Path) -> None:
    """``not @Alpha`` selects exactly the units of the untagged feature.

    The mechanism AAP 0.6 points at for the real suite's five untagged
    features: unreachable by a positive expression over feature tags, perfectly
    reachable by a negative one.  A unit with no tags at all evaluates a
    negation to true, which is what makes the neutral tautology on every
    worker's command line safe as well.
    """
    beta_locations = [
        location
        for location in all_locations()
        if split_location(location)[0] == BETA_FEATURE.feature_path
    ]

    assert selected_locations(tags="not @Alpha", base=feature_tree) == beta_locations
    assert selected_locations(
        tags=service.NEUTRAL_TAG_EXPRESSION, base=feature_tree
    ) == list(all_locations())


def test_no_filter_selects_everything_in_canonical_order(
    feature_tree: Path,
) -> None:
    """``None`` and a blank expression both mean "no filter".

    ``run_suite`` passes ``None`` when the user gave no ``--tags``; a blank
    string is the same condition arriving by a different route, and neither may
    be mistaken for a filter that selects nothing.
    """
    assert selected_locations(base=feature_tree) == list(all_locations())
    assert selected_locations(tags=None, base=feature_tree) == list(all_locations())
    assert selected_locations(tags="   ", base=feature_tree) == list(all_locations())


def test_effective_tags_are_sorted_and_carry_the_sigil(feature_tree: Path) -> None:
    """``ScenarioRef.tags`` is the effective set, ``@``-prefixed and sorted.

    behave's model stores a tag as its bare name while a tag expression is
    written with the sigil, and behave exposes effective tags as a *set*, whose
    iteration order is not stable across processes.  Restoring the sigil and
    sorting is what makes the value both evaluable and deterministic - and
    forgetting the sigil is the failure mode that silently selects nothing.
    """
    selected, _ = service.select_scenarios(base=feature_tree)
    by_location = {unit.location: unit for unit in selected}
    prefix = ALPHA_FEATURE.feature_path + rerun_report.LINE_SEPARATOR

    assert by_location[f"{prefix}7"].tags == ("@Alpha",)
    assert by_location[f"{prefix}11"].tags == ("@Alpha", "@Beta")
    assert by_location[f"{prefix}20"].tags == ("@Alpha", "@Gamma")
    assert by_location[f"{prefix}21"].tags == ("@Alpha", "@Gamma")
    assert (
        by_location[f"{BETA_FEATURE.feature_path}{rerun_report.LINE_SEPARATOR}3"].tags
        == ()
    )

    for unit in selected:
        assert list(unit.tags) == sorted(unit.tags)
        assert all(tag.startswith("@") for tag in unit.tags)


def test_scenario_ref_location_is_behaves_own_syntax(feature_tree: Path) -> None:
    """A unit's location is ``path:line`` with the manifest's own separator.

    The rerun manifest's separator and behave's location syntax are the same
    character and ``--rerun`` feeds one straight into the other, so the service
    takes the separator from ``app/reporting/rerun_report.py`` rather than
    declaring a second copy (AAP 0.6's round-trip requirement).
    """
    selected, _ = service.select_scenarios(base=feature_tree)
    for unit in selected:
        assert unit.location == (
            f"{unit.feature_path}{rerun_report.LINE_SEPARATOR}{unit.line}"
        )
        assert unit.feature_path.startswith(paths.NORMALIZED_FEATURES_PREFIX)


def test_outline_rows_keep_behaves_annotation_in_the_informational_name(
    feature_tree: Path,
) -> None:
    """A generated row's name is behave's, annotation and all.

    Stripping the ``" -- @1.1 <Examples>"`` suffix for the JSON report belongs
    to ``app/reporting/events.py``, which owns that contract (AAP 0.6); the
    service leaves the engine's own name alone because the field is
    informational - it appears in log messages and nowhere else.
    """
    selected, _ = service.select_scenarios(tags="@Gamma", base=feature_tree)
    assert len(selected) == 2
    for unit in selected:
        assert unit.name.startswith("third alpha")
        assert " -- " in unit.name


def test_a_feature_that_cannot_be_parsed_does_not_abort_selection(
    feature_tree: Path,
) -> None:
    """A malformed feature is reported and the other features still select.

    AAP 0.4.1 keeps "a feature that fails to parse" at status ``0`` with the
    problem reported on stderr and all four artifacts written from whatever
    executed, so a parse failure must never abort a run.
    """
    write_feature(
        feature_tree,
        "Broken.feature",
        "Feature: broken\n  Scenario: one\n    Given a precondition\n"
        "  Scenariox: not a keyword\n    Whatever this is\n",
    )

    selected, problems = service.select_scenarios(base=feature_tree)

    assert [unit.location for unit in selected] == list(all_locations())
    assert len(problems) == 1
    assert problems[0].startswith(f"{paths.NORMALIZED_FEATURES_PREFIX}Broken.feature")


def test_an_empty_feature_file_is_neither_a_unit_nor_a_problem(
    feature_tree: Path,
) -> None:
    """A comment-only feature contributes nothing and reports nothing.

    There is simply nothing to run in it, which is not a defect: reporting it
    would put a permanent problem line on every run of a suite that happened to
    carry a placeholder file.
    """
    write_feature(feature_tree, "Comment.feature", "# nothing here yet\n")

    selected, problems = service.select_scenarios(base=feature_tree)

    assert [unit.location for unit in selected] == list(all_locations())
    assert problems == []


def test_a_missing_or_empty_features_directory_is_a_tolerated_problem(
    tmp_artifact_root: Path,
) -> None:
    """Both conditions yield no scenarios, a problem message and no exception.

    Each leaves the run at status ``0``, matching the source's own tolerance of
    a missing configuration file (``ConfigurationReader.java:21-24``).
    """
    selected, problems = service.select_scenarios(base=tmp_artifact_root)
    assert selected == []
    assert len(problems) == 1
    assert str(paths.features_dir(tmp_artifact_root)) in problems[0]

    paths.ensure_dir(paths.features_dir(tmp_artifact_root))
    selected, problems = service.select_scenarios(base=tmp_artifact_root)
    assert selected == []
    assert len(problems) == 1
    assert str(paths.features_dir(tmp_artifact_root)) in problems[0]


def test_a_malformed_tag_expression_propagates(feature_tree: Path) -> None:
    """A nonsense filter raises rather than selecting nothing.

    The module's one documented propagating exception, and deliberately so: a
    malformed expression is an invalid option value - the usage-error class
    ``app/cli.py`` owns, which exits non-zero and writes nothing (AAP 0.4.1) -
    and not a test outcome.  Swallowing it would exit ``0`` with four empty
    artifacts and never tell the user their filter was nonsense.
    """
    with pytest.raises(TagExpressionError):
        service.select_scenarios(tags="@Alpha and and", base=feature_tree)

    with pytest.raises(TagExpressionError):
        service.run_suite(
            tags="(@Alpha", base=feature_tree, spawn=RecordingSpawn(base=feature_tree)
        )


def test_the_smoke_default_selects_the_crm_feature_alone(repo_root: Path) -> None:
    """``@Smoke`` selects exactly four scenarios, all in ``Crm.feature``.

    Asserted against the **real** suite, because this is the fact
    ``CukesRunner.java:18`` and ``behave.ini``'s ``default_tags`` encode:
    ``@Smoke`` is declared exactly once, at ``Crm.feature:1``, so the default
    run selects the CRM feature alone - corroborated by the committed baseline
    holding exactly one feature (AAP 0.6).  Measured line by line: the scenario
    at 9, the outline rows at 24 and 26, and the scenario at 31.
    """
    crm_path = f"{paths.NORMALIZED_FEATURES_PREFIX}{CRM_FEATURE_FILENAME}"
    expected = [
        f"{crm_path}{rerun_report.LINE_SEPARATOR}{line}"
        for line in SMOKE_LOCATION_LINES
    ]

    selected, problems = service.select_scenarios(tags=SMOKE_TAG, base=repo_root)

    assert problems == []
    assert [unit.location for unit in selected] == expected
    assert {unit.feature_path for unit in selected} == {crm_path}
    for unit in selected:
        assert SMOKE_TAG in unit.tags


def test_the_untagged_features_are_reachable_only_by_a_negative_expression(
    repo_root: Path,
) -> None:
    """The five untagged features answer to ``not @Smoke`` and to nothing positive.

    AAP 0.6 records this as the suite's own shape, faithfully reproduced rather
    than corrected: Contact, Inventory, Notes, Sales and Session carry no
    feature-level tag, so a positive expression over feature tags cannot reach
    them, while ``not @Smoke`` reaches every one.
    """
    untagged_paths = {
        f"{paths.NORMALIZED_FEATURES_PREFIX}{filename}"
        for filename in UNTAGGED_FEATURE_FILENAMES
    }

    positive, positive_problems = service.select_scenarios(
        tags=SMOKE_TAG, base=repo_root
    )
    negative, negative_problems = service.select_scenarios(
        tags=f"not {SMOKE_TAG}", base=repo_root
    )

    assert positive_problems == []
    assert negative_problems == []
    assert not untagged_paths & {unit.feature_path for unit in positive}
    assert untagged_paths <= {unit.feature_path for unit in negative}
    assert not {unit.location for unit in positive} & {
        unit.location for unit in negative
    }


# =========================================================================== #
# The worker command line (AAP 0.4.1's engine/writer division)
#
# "behave.ini declares no formatter: test_run_service invokes the engine once
# per worker with the formatter and -o <path> on the command line, the path
# coming from app.utils.paths and unique per worker by process id and shard
# index."  Every property below follows from that sentence or from the
# measurement behind NEUTRAL_TAG_EXPRESSION.
# =========================================================================== #

#: The keys ``behave.ini`` must not declare.  Their absence is load-bearing: a
#: static formatter key would aim every worker at the same file and the merge
#: would silently see one shard's results.
FORBIDDEN_BEHAVE_INI_KEYS: Final[tuple[str, ...]] = ("format", "outfiles")

#: The section ``behave.ini`` carries, per the engine's own configuration
#: format.
BEHAVE_INI_SECTION: Final[str] = "behave"

#: The file the engine reads its configuration from, at the repository root.
BEHAVE_INI_NAME: Final[str] = "behave.ini"

#: The suffix a per-worker intermediate carries, taken from the artifact name
#: the paths module owns rather than written out here.
JSON_SUFFIX: Final[str] = Path(paths.CUCUMBER_JSON_NAME).suffix


def only_plan(base: Path, **kwargs: Any) -> service.ShardPlan:
    """Return the single shard of a one-worker plan over the temporary tree.

    Args:
        base: The temporary checkout root.
        **kwargs: Passed to
            :func:`~app.services.test_run_service.select_scenarios`.

    Returns:
        The one and only shard, which is the sequential mode's plan.
    """
    selected, _ = service.select_scenarios(base=base, **kwargs)
    plans = service.shard_scenarios(selected, 1, base=base)
    assert len(plans) == 1
    return plans[0]


def test_the_command_is_an_argument_list_run_without_a_shell(
    feature_tree: Path,
) -> None:
    """The command is a list of strings whose first element is this interpreter.

    A list with no shell is what keeps anything in a tag expression or a
    location from being interpreted by one, and ``sys.executable`` is what makes
    a run work inside the bootstrapped ``.venv`` with no assumption about
    ``PATH`` - the engine is invoked as a module of the *current* interpreter.
    """
    command = service.build_worker_command(only_plan(feature_tree))

    assert isinstance(command, list)
    assert all(isinstance(argument, str) for argument in command)
    assert command[0] == sys.executable
    assert command[1] == "-m"


def test_exactly_one_formatter_and_one_output_file_are_passed(
    feature_tree: Path,
) -> None:
    """One ``--format``, one ``-o``, and the formatter's name is its owner's.

    behave pairs formatters with output files **positionally**, so one of each
    keeps the pairing unambiguous.  The formatter name is taken from
    ``app/reporting/events.py``, the module that defines the formatter, and
    never written out as a string here or there.
    """
    plan = only_plan(feature_tree)
    command = service.build_worker_command(plan)

    assert command.count(FORMAT_FLAG) == 1
    assert command.count(OUTFILE_FLAG) == 1
    assert command[command.index(FORMAT_FLAG) + 1] == events.FORMATTER_SCOPED_NAME
    assert command[command.index(OUTFILE_FLAG) + 1] == str(plan.output_path)


def test_every_shards_output_file_is_its_own(feature_tree: Path) -> None:
    """Each worker is given the path the parent computed for it, and no other.

    A static configuration file cannot hand each worker a distinct output path,
    which is the whole reason the formatter and the output file arrive on the
    command line (AAP 0.4.1).
    """
    selected, _ = service.select_scenarios(base=feature_tree)
    plans = service.shard_scenarios(selected, 3, base=feature_tree)

    outfiles = []
    for plan in plans:
        command = service.build_worker_command(plan)
        outfiles.append(command[command.index(OUTFILE_FLAG) + 1])

    assert outfiles == [str(plan.output_path) for plan in plans]
    assert len(set(outfiles)) == len(plans)


def test_a_tag_expression_is_always_passed(feature_tree: Path) -> None:
    """``--tags`` appears on every invocation, neutral when no filter applies.

    Measured, and the reason this matters: ``behave.ini`` declares
    ``default_tags = @Smoke``, and that default applies whenever the command
    line supplies no filter of its own - *including* a command line that names
    explicit scenario locations.  An explicit ``--tags`` is the mechanism that
    suppresses it, so one is passed always.  Under ``--rerun`` that is not a
    nicety: ``FailedTestRunner.java:9-12`` declares no tag filter at all, so
    re-running failures has to clear the default outright or every failure from
    a non-``@Smoke`` feature is silently skipped.
    """
    plan = only_plan(feature_tree)

    for tags in (None, "", "   "):
        command = service.build_worker_command(plan, tags=tags)
        assert command.count(TAGS_FLAG) == 1
        assert command[command.index(TAGS_FLAG) + 1] == service.NEUTRAL_TAG_EXPRESSION

    command = service.build_worker_command(plan, tags="@Alpha and not @Beta")
    assert command.count(TAGS_FLAG) == 1
    assert command[command.index(TAGS_FLAG) + 1] == "@Alpha and not @Beta"


def test_the_neutral_expression_is_a_negation_no_feature_declares(
    feature_tree: Path,
) -> None:
    """The tautology selects everything, including a unit with no tags at all.

    It is a negation of an undeclared tag rather than an empty value because an
    empty ``--tags`` may be rejected outright, and it has to evaluate true
    against an empty tag list - which is what the real suite's five untagged
    features need.
    """
    assert service.NEUTRAL_TAG_EXPRESSION.startswith("not ")
    assert selected_locations(
        tags=service.NEUTRAL_TAG_EXPRESSION, base=feature_tree
    ) == list(all_locations())

    tag_name = service.NEUTRAL_TAG_EXPRESSION.removeprefix("not ")
    for feature in FEATURE_TREE:
        assert tag_name not in feature.text


def test_a_browser_override_is_passed_only_when_one_was_given(
    feature_tree: Path,
) -> None:
    """``-D browser=<name>`` appears exactly when ``--browser`` was used.

    behave userdata is the only override path in the port - no environment
    layer exists (AAP 0.4.1) - and the value is deliberately not validated: an
    unrecognised browser must fail at first driver use, exactly as
    ``Driver.java:29-42``'s missing default branch arranges.
    """
    plan = only_plan(feature_tree)

    without = service.build_worker_command(plan)
    assert USERDATA_FLAG not in without

    for name in ("chrome", "firefox", "netscape-navigator"):
        command = service.build_worker_command(plan, browser=name)
        assert command.count(USERDATA_FLAG) == 1
        assert command[command.index(USERDATA_FLAG) + 1] == f"browser={name}"


def test_dry_run_is_passed_only_when_asked(feature_tree: Path) -> None:
    """``--dry-run`` appears only for a dry run.

    ``behave.ini`` states ``dry_run = false`` explicitly, from
    ``CukesRunner.java:17``; the flag overrides it for a single run.
    """
    plan = only_plan(feature_tree)

    assert DRY_RUN_FLAG not in service.build_worker_command(plan)
    assert DRY_RUN_FLAG in service.build_worker_command(plan, dry_run=True)
    assert service.build_worker_command(plan, dry_run=True).count(DRY_RUN_FLAG) == 1


def test_the_shards_locations_come_last(feature_tree: Path) -> None:
    """The shard's locations are the command's trailing arguments, in its order.

    They are positional arguments to the engine, so nothing may follow them -
    and the order is the shard's, which is grouped by feature and ascending by
    line so that a feature's Background runs once per scenario inside the
    worker that runs it.
    """
    selected, _ = service.select_scenarios(base=feature_tree)
    for plan in service.shard_scenarios(selected, 2, base=feature_tree):
        for browser, dry_run in ((None, False), ("chrome", True)):
            command = service.build_worker_command(
                plan, tags="@Alpha", browser=browser, dry_run=dry_run
            )
            assert command[-len(plan.locations) :] == list(plan.locations)


def test_the_command_carries_no_artifact_path_of_its_own(
    feature_tree: Path,
) -> None:
    """The only path in the command is the one the paths module produced.

    ``app/utils/paths.py`` owns every artifact path in the port; a second
    spelling of one on this command line is how a worker would come to write
    where the publisher's narrowed ``target/cucumber.json`` glob could see it
    (``Jenkins:15``).
    """
    plan = only_plan(feature_tree)
    command = service.build_worker_command(
        plan, tags="@Alpha", browser="chrome", dry_run=True
    )

    carrying_target = [
        argument for argument in command if paths.TARGET_DIR_NAME in argument
    ]
    assert carrying_target == [str(plan.output_path)]

    for spec in paths.ARTIFACT_SPECS:
        for argument in command:
            if argument == str(plan.output_path):
                continue
            assert spec.key not in argument
            assert spec.relpath not in argument


def test_behave_ini_declares_no_formatter_and_no_output_file(
    repo_root: Path,
) -> None:
    """The engine's configuration file carries neither key, by decision.

    behave accepts both, so leaving them out is a decision rather than an
    oversight: a static formatter key would aim every worker at the same file
    and the merge would silently see a single shard's results (AAP 0.4.1).
    The file is read with :mod:`configparser`, which is how the engine reads it.
    """
    parser = configparser.ConfigParser()
    read = parser.read(repo_root / BEHAVE_INI_NAME, encoding="utf-8")

    assert read, f"{BEHAVE_INI_NAME} could not be read"
    assert parser.sections() == [BEHAVE_INI_SECTION]
    for key in FORBIDDEN_BEHAVE_INI_KEYS:
        assert not parser.has_option(BEHAVE_INI_SECTION, key)
    for value in parser[BEHAVE_INI_SECTION].values():
        assert paths.TARGET_DIR_NAME not in value


def test_the_launch_receives_the_built_command_in_the_run_base(
    feature_tree: Path,
) -> None:
    """What the seam is handed is exactly what ``build_worker_command`` builds.

    And it is handed the run base as its working directory, which is what makes
    ``behave.ini`` discoverable, ``features/environment.py`` findable relative
    to ``paths = features``, ``app.reporting.events`` importable and
    ``configuration.properties`` read from the working directory exactly as the
    Java reader read it.

    The plans compared against are the run's **own**, taken off
    :attr:`~app.services.RunOutcome.shard_results`, because only the run knows
    which directory its shards write into: :func:`~app.services.run_suite`
    prepares a directory for this run and passes it to
    :func:`~app.services.shard_scenarios`, while a caller sharding without a
    prepared run - the line below, which is what fixes the *work* each shard
    is given - gets the shared directory instead.  Both halves are asserted:
    the locations and the file name are the same either way, and the run's
    destination differs only by sitting inside this run's own directory.
    """
    selected, _ = service.select_scenarios(tags="@Alpha", base=feature_tree)
    unplaced = service.shard_scenarios(selected, 2, base=feature_tree)

    spawn = RecordingSpawn(base=feature_tree)
    outcome = service.run_suite(
        tags="@Alpha",
        browser="firefox",
        workers=2,
        dry_run=True,
        base=feature_tree,
        spawn=spawn,
    )

    assert outcome.worker_count == len(unplaced)
    recorded = spawn.calls_by_shard()
    assert sorted(recorded) == [plan.index for plan in unplaced]

    executed = {result.plan.index: result.plan for result in outcome.shard_results}
    assert sorted(executed) == [plan.index for plan in unplaced]

    for expected in unplaced:
        plan = executed[expected.index]

        # Same work, same file name; only the directory is this run's own.
        assert plan.locations == expected.locations
        assert plan.output_path.name == expected.output_path.name
        assert plan.output_path.parent.parent == expected.output_path.parent
        assert service.run_directory_owner(plan.output_path.parent.name) == (
            os.getpid()
        )

        call = recorded[expected.index]
        assert list(call.command) == service.build_worker_command(
            plan, tags="@Alpha", browser="firefox", dry_run=True
        )
        assert call.cwd == feature_tree
        assert call.locations == plan.locations


# =========================================================================== #
# The target/.workers/ lifecycle (AAP 0.4.1)
#
# "That directory is created before the run, emptied by --clean, and removed
# after the merge whether it succeeds or fails."  ``Jenkins:15`` narrows the
# publisher's fileIncludePattern to target/cucumber.json, so no intermediate
# worker JSON may be left visible in the workspace.
# =========================================================================== #


def worker_json_left(base: Path) -> list[Path]:
    """Return every JSON file surviving under a run's ``target/``.

    Args:
        base: The temporary checkout root.

    Returns:
        The JSON files, if any.  After a run this must be empty: the four
        artifacts belong to ``app/services/report_service.py``, which no test
        here invokes, and an intermediate worker file surviving is exactly what
        ``Jenkins:15``'s narrowed glob must never be able to see.
    """
    target = paths.target_root(base)
    if not target.exists():
        return []
    return sorted(target.rglob(f"*{JSON_SUFFIX}"))


def test_prepare_workers_dir_creates_this_runs_own_directory(
    tmp_artifact_root: Path,
) -> None:
    """Each run gets its own directory inside ``target/.workers``.

    The shared directory is still the accessor's own -
    :func:`app.utils.paths.workers_dir` remains its single owner, and the
    leading dot is what keeps it unreachable through the HTTP artifact route
    (AAP 0.3.1) - but what a run is handed is a **child** of it, named after
    this run.  A single shared directory has two failure modes a concurrent
    checkout meets for real: one invocation's ``--clean`` empties a second
    invocation's live results, and one invocation's cleanup removes the
    directory a second is still writing into.  Neither is expressible once the
    unit of ownership is per run.

    The name is what carries that ownership:
    :func:`~app.services.run_directory_owner` reads this process's id back out
    of it, which is how a *live* run's directory is told apart from an
    abandoned one's leftovers.
    """
    shared_root = paths.workers_dir(tmp_artifact_root)
    assert not shared_root.exists()

    created = service.prepare_workers_dir(base=tmp_artifact_root)

    assert created.is_dir()
    assert created.parent == shared_root
    assert shared_root.parent == paths.target_root(tmp_artifact_root)
    assert created != shared_root

    # Ownership and liveness, both read from the name the service chose.
    assert service.run_directory_owner(created.name) == os.getpid()
    assert service.run_directory_is_active(created) is True


def test_prepare_workers_dir_gives_every_run_a_directory_of_its_own(
    tmp_artifact_root: Path,
) -> None:
    """Preparing twice yields two directories, and destroys nothing.

    This replaces an idempotence the shared directory had and the per-run
    contract deliberately does not: a second call is a second *run*, so it is
    given somewhere else to write.  What idempotence is still required of is
    the removal - :func:`~app.services.cleanup_workers_dir` is called from two
    ``finally`` blocks - and
    :func:`test_cleanup_workers_dir_is_a_silent_no_op_when_already_gone`
    asserts that.

    Nothing already in the shared directory is touched, which is the property
    the concurrency finding turns on: the marker below stands for a
    concurrent run's work, and it survives.
    """
    first = service.prepare_workers_dir(base=tmp_artifact_root)
    marker = first / f"marker{JSON_SUFFIX}"
    marker.write_text("{}", encoding="utf-8")

    second = service.prepare_workers_dir(base=tmp_artifact_root)

    assert second != first
    assert second.is_dir()
    assert first.is_dir()
    assert marker.exists()
    assert second.parent == first.parent == paths.workers_dir(tmp_artifact_root)
    assert service.run_directory_owner(second.name) == os.getpid()


def test_cleanup_workers_dir_removes_the_directory_and_its_contents(
    tmp_artifact_root: Path,
) -> None:
    """Cleanup takes the directory and everything in it.

    ``app/utils/paths.py`` deliberately never deletes anything, which is why the
    removal lives in the service.
    """
    directory = service.prepare_workers_dir(base=tmp_artifact_root)
    (directory / f"worker{JSON_SUFFIX}").write_text("{}", encoding="utf-8")

    service.cleanup_workers_dir(base=tmp_artifact_root)

    assert not directory.exists()
    assert paths.target_root(tmp_artifact_root).is_dir()


def test_cleanup_workers_dir_is_a_silent_no_op_when_already_gone(
    tmp_artifact_root: Path,
) -> None:
    """A second cleanup returns quietly rather than raising.

    Idempotent **by contract**, not by luck: ``app/cli.py`` removes this
    directory in an outer ``finally`` as a belt-and-braces guarantee and
    ``run_suite`` removes it in its own, so the function is routinely called
    twice - and it also runs in a ``finally``, where an exception would mask
    whatever the run was already reporting.
    """
    service.prepare_workers_dir(base=tmp_artifact_root)

    service.cleanup_workers_dir(base=tmp_artifact_root)
    service.cleanup_workers_dir(base=tmp_artifact_root)

    assert not paths.workers_dir(tmp_artifact_root).exists()

    # Never created in the first place is the same silent case.
    fresh = tmp_artifact_root / "never-run"
    fresh.mkdir()
    service.cleanup_workers_dir(base=fresh)
    assert not paths.workers_dir(fresh).exists()


def test_a_successful_run_leaves_no_worker_directory_behind(
    feature_tree: Path,
) -> None:
    """After a merge that succeeded this run's directory is gone.

    And nothing that the publisher's narrowed glob could match is left: the
    per-worker documents existed only between the launch and the merge.

    What is removed is **this run's own directory**, and the shared one goes
    with it only because nothing else is left in it: ``rmdir`` and never
    ``rmtree``, so a concurrent run's live directory both prevents that last
    step and survives it.  The removal is also confirmed rather than assumed -
    the service reports a directory that outlived its removal instead of
    reporting success - so the absence asserted here is the absence the
    outcome claims, and ``infrastructure_error`` is ``None``.
    """
    outcome, spawn = run_with_workers(feature_tree, 3)

    assert outcome.result_set is not None
    assert len(spawn.calls) == 3

    # The workers really were pointed into one directory of this run's own,
    # inside the shared one, so the assertions below are about work that
    # happened.
    run_dirs = {call.output_path.parent for call in spawn.calls}
    assert len(run_dirs) == 1, run_dirs
    run_dir = run_dirs.pop()
    assert run_dir.parent == paths.workers_dir(feature_tree)
    assert service.run_directory_owner(run_dir.name) == os.getpid()

    assert not run_dir.exists()
    assert outcome.infrastructure_error is None

    # And the run stopped answering for it: the no-argument cleanup - the call
    # ``app/cli.py`` makes on every exit path, meaning "everything this
    # process created and has not yet removed" - finds nothing left to do.
    # A directory whose removal had failed would still be registered, and
    # would be reported again here.
    assert service.cleanup_workers_dir(base=feature_tree) is None

    # Nothing was left in the shared directory, so it was pruned too.
    assert not paths.workers_dir(feature_tree).exists()
    assert paths.iter_worker_result_paths(feature_tree) == ()
    assert worker_json_left(feature_tree) == []


def test_a_run_whose_merge_produced_nothing_still_removes_the_directory(
    feature_tree: Path,
) -> None:
    """The directory goes whether the merge succeeded or failed.

    Here every shard writes an unusable file, so the merge produces no document
    at all - AAP 0.4.1's "the merge produces no result set" row - and the
    intermediate directory must still be gone before the call returns.
    """
    spawn = RecordingSpawn(
        base=feature_tree, behaviour={0: INVALID_JSON, 1: INVALID_JSON}
    )
    outcome = service.run_suite(workers=2, base=feature_tree, spawn=spawn)

    assert outcome.result_set is None
    assert outcome.merge_produced_nothing is True
    assert len(outcome.dead_shards) == 2
    assert not paths.workers_dir(feature_tree).exists()
    assert worker_json_left(feature_tree) == []


def test_a_run_whose_launches_all_failed_still_removes_the_directory(
    feature_tree: Path,
) -> None:
    """A failed launch is data, not an abort, and the cleanup happens anyway."""
    spawn = RecordingSpawn(
        base=feature_tree, behaviour={0: LAUNCH_RAISES, 1: LAUNCH_RAISES}
    )
    outcome = service.run_suite(workers=2, base=feature_tree, spawn=spawn)

    assert outcome.result_set is None
    assert outcome.merge_produced_nothing is True
    assert not paths.workers_dir(feature_tree).exists()


def test_the_directory_is_removed_even_when_the_run_is_interrupted(
    feature_tree: Path,
) -> None:
    """An interruption unwinds through the same ``finally``.

    ``_run_one_shard`` converts every ``Exception`` a launch raises into a dead
    shard, and deliberately not ``BaseException``: a ``KeyboardInterrupt`` must
    still end the run.  When it does, the cleanup is what stops an interrupted
    run from leaving intermediate JSON in a workspace the publisher then reads.
    """

    def interrupt(command: Sequence[str], cwd: Path) -> FakeProcess:
        """Interrupt the run at the moment the first worker would start."""
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        service.run_suite(workers=1, base=feature_tree, spawn=interrupt)

    assert not paths.workers_dir(feature_tree).exists()
    assert worker_json_left(feature_tree) == []


def test_the_worker_directory_exists_while_the_workers_run(
    feature_tree: Path,
) -> None:
    """It is created before the launch, which is why a worker can write into it.

    Asserted from inside the seam, the only moment at which the directory is
    supposed to exist.
    """
    observed: list[bool] = []
    inner = RecordingSpawn(base=feature_tree)

    def observing(command: Sequence[str], cwd: Path) -> FakeProcess:
        """Record whether the directory exists, then write as usual."""
        observed.append(paths.workers_dir(feature_tree).is_dir())
        return inner(command, cwd)

    outcome = service.run_suite(workers=2, base=feature_tree, spawn=observing)

    assert observed == [True, True]
    assert outcome.result_set is not None
    assert not paths.workers_dir(feature_tree).exists()


def test_a_worker_directory_that_cannot_be_created_is_the_empty_merge_state(
    feature_tree: Path,
) -> None:
    """Nowhere to write means nothing produced, and nothing spawned.

    Not a tolerated failure and not a dead worker: it is a failure of this
    port's own intermediate storage, reached without launching anything, and it
    is reported on its own field - :attr:`~app.services.RunOutcome
    .infrastructure_error` - precisely so that it cannot be mistaken for
    either.  ``parse_errors`` would make it a status-``0`` outcome, which is
    the one thing a run that executed no scenario must not be.  The condition
    is provoked by putting a *file* where the directory has to go, which is
    what the service's ``OSError`` branch exists for.

    The message is **carried, not logged**: this module emits no record for it,
    because ``app/cli.py`` names it once beside the exit class it produces
    (``tests/test_cli.py``,
    ``test_a_run_whose_intermediate_directory_cannot_be_prepared_exits_four``).
    One fact, one emitter.
    """
    paths.ensure_dir(paths.target_root(feature_tree))
    paths.workers_dir(feature_tree).write_text("not a directory", encoding="utf-8")

    spawn = RecordingSpawn(base=feature_tree)
    outcome = service.run_suite(workers=2, base=feature_tree, spawn=spawn)

    assert spawn.calls == []
    assert outcome.result_set is None
    assert outcome.merge_produced_nothing is True
    assert outcome.worker_count == 0
    assert outcome.selected_count == len(all_locations())

    assert outcome.infrastructure_error is not None
    assert str(paths.workers_dir(feature_tree)) in outcome.infrastructure_error
    assert "no scenario was executed" in outcome.infrastructure_error
    # Neither of the two status-0 channels carries it.
    assert outcome.parse_errors == ()
    assert outcome.dead_shards == ()


# =========================================================================== #
# Dead workers and merge outcomes (AAP 0.4.1's exit table)
#
# "A worker process that dies -> non-zero, all four written from the shards
# that completed, the incomplete shard named on stderr" and "the merge produces
# no result set at all -> non-zero, none written".  Against that,
# ``pom.xml:25``'s testFailureIgnore=true: a positive non-zero return code is
# behave reporting scenario failures and must never be read as a death.
# =========================================================================== #

#: The three ways a shard that launched cleanly can still fail to produce
#: results, each decided at merge time because that is the first moment it can
#: be: an absent file means the worker never got that far, an empty one means it
#: started and died, and an unparseable one means it died mid-write.
DEAD_FILE_BEHAVIOURS: Final[tuple[str, ...]] = (NO_FILE, EMPTY_FILE, INVALID_JSON)


def surviving_locations(result_set: dict[str, Any]) -> set[str]:
    """Return every scenario location present in a merged document.

    Args:
        result_set: A merged result document.

    Returns:
        The ``path:line`` locations of its scenario elements.
    """
    return {
        f"{feature['path']}{rerun_report.LINE_SEPARATOR}{element['line']}"
        for feature in result_set["features"]
        for element in scenario_elements(feature)
    }


@pytest.mark.parametrize("behaviour", DEAD_FILE_BEHAVIOURS)
def test_a_shard_without_usable_results_is_dead_and_named(
    feature_tree: Path, behaviour: str
) -> None:
    """An absent, empty or unparseable result file makes its shard dead.

    The reason names the shard, which is what AAP 0.4.1's "the incomplete shard
    is named on stderr" requires, and the shard is reported as dead on the
    outcome rather than raised.
    """
    spawn = RecordingSpawn(base=feature_tree, behaviour={1: behaviour})
    outcome = service.run_suite(workers=2, base=feature_tree, spawn=spawn)

    by_index = {result.plan.index: result for result in outcome.shard_results}
    assert by_index[0].dead is False
    assert by_index[0].reason is None
    assert by_index[1].dead is True
    assert by_index[1].reason is not None
    assert "shard 1" in by_index[1].reason
    assert outcome.dead_shards == (by_index[1].reason,)


@pytest.mark.parametrize("behaviour", DEAD_FILE_BEHAVIOURS)
def test_a_dead_shard_does_not_suppress_the_shards_that_completed(
    feature_tree: Path, behaviour: str
) -> None:
    """The merge still carries every scenario the live shards ran.

    A dead shard is non-zero **and** non-suppressing: AAP 0.4.1 writes all four
    artifacts "from the shards that completed", so the merged document has to
    contain exactly the live shards' scenarios and nothing of the dead one's.
    """
    selected, _ = service.select_scenarios(base=feature_tree)
    plans = service.shard_scenarios(selected, 2, base=feature_tree)
    lost = set(plans[1].locations)
    survived = set(plans[0].locations)

    spawn = RecordingSpawn(base=feature_tree, behaviour={1: behaviour})
    outcome = service.run_suite(workers=2, base=feature_tree, spawn=spawn)

    assert outcome.result_set is not None
    assert outcome.merge_produced_nothing is False
    assert surviving_locations(outcome.result_set) == survived
    assert not surviving_locations(outcome.result_set) & lost


def test_a_launch_that_raises_becomes_a_dead_shard(feature_tree: Path) -> None:
    """An ``OSError`` from the launch is recorded, not propagated.

    One bad worker must not be able to abort a run: the failure is data, and
    the other shards still run and still merge.
    """
    spawn = RecordingSpawn(base=feature_tree, behaviour={0: LAUNCH_RAISES})
    outcome = service.run_suite(workers=2, base=feature_tree, spawn=spawn)

    by_index = {result.plan.index: result for result in outcome.shard_results}
    assert by_index[0].dead is True
    assert by_index[0].returncode is None
    assert by_index[0].reason is not None
    assert "shard 0" in by_index[0].reason
    assert by_index[1].dead is False
    assert outcome.result_set is not None
    assert outcome.merge_produced_nothing is False


def test_a_negative_return_code_is_a_dead_shard(feature_tree: Path) -> None:
    """A worker killed by a signal is dead even if it left a result file.

    POSIX reports a signal death as a negative return code, and such a worker's
    results are incomplete by definition - the file it wrote covers only the
    scenarios it reached.
    """
    spawn = RecordingSpawn(base=feature_tree, returncodes={1: -9})
    outcome = service.run_suite(workers=2, base=feature_tree, spawn=spawn)

    by_index = {result.plan.index: result for result in outcome.shard_results}
    assert by_index[1].dead is True
    assert by_index[1].returncode == -9
    assert by_index[1].reason is not None
    assert "shard 1" in by_index[1].reason
    assert len(outcome.dead_shards) == 1
    assert outcome.result_set is not None


@pytest.mark.parametrize("returncode", [1, 2, 130])
def test_a_positive_non_zero_return_code_is_not_a_dead_shard(
    feature_tree: Path, returncode: int
) -> None:
    """behave exiting non-zero because scenarios failed is a normal outcome.

    The behave exit trap, and the reason this module asserts it three times
    over: treating a worker's non-zero status as an error would propagate a test
    outcome into the exit status, breaking ``pom.xml:25``'s
    ``testFailureIgnore=true`` and the six ``-1`` publisher thresholds at
    ``Jenkins:15``.
    """
    spawn = RecordingSpawn(
        base=feature_tree, returncodes={0: returncode, 1: returncode}
    )
    outcome = service.run_suite(workers=2, base=feature_tree, spawn=spawn)

    assert [result.dead for result in outcome.shard_results] == [False, False]
    assert [result.returncode for result in outcome.shard_results] == [
        returncode,
        returncode,
    ]
    assert outcome.dead_shards == ()
    assert outcome.result_set is not None
    assert surviving_locations(outcome.result_set) == set(all_locations())
    assert outcome.merge_produced_nothing is False


# =========================================================================== #
# Relaying a worker's output into the parent log (AAP 0.4.1's stream split)
#
# A worker is a separate interpreter writing to pipes, so everything it printed
# reaches an operator only through the parent's records.  Three properties are
# asserted below and each one is a decision rather than an implementation
# detail: nothing is printed twice, no line is trusted as log text, and the
# stream a line arrived on is a severity **floor** rather than a ceiling.
# =========================================================================== #


def relayed_records(
    caplog: pytest.LogCaptureFixture, shard: int
) -> list[logging.LogRecord]:
    """Return the parent records carrying one shard's relayed output.

    Args:
        caplog: pytest's log-capture fixture.
        shard: The shard index whose tag the records must carry.

    Returns:
        The service's own records for that shard's relayed lines, in order.
        The ``[shard N]`` tag on every line is what makes concurrent output
        attributable without tracing, so it is also what identifies a relayed
        record here.
    """
    tag = f"[shard {shard}]"
    return [
        record
        for record in caplog.records
        if record.name == service.__name__ and tag in record.getMessage()
    ]


def test_a_worker_that_already_relayed_its_output_is_not_relayed_again(
    feature_tree: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The marked launch prints nothing after the fact.

    The real launch drains both pipes in reader threads and logs every line the
    moment it is complete, so the stream split holds *during* the run rather
    than after it, and what it hands back is a bounded tail.  Relaying that
    tail as well would show those lines twice - once live, once at the end -
    so a process carrying a true ``output_relayed`` attribute is relayed not at
    all.

    The marker is read with :func:`getattr` rather than declared on the
    ``WorkerProcess`` protocol, which is what keeps a plain
    :class:`subprocess.CompletedProcess` a valid stub; the two paths therefore
    have to coexist, and the test below is the other one.
    """
    spawn = RecordingSpawn(
        base=feature_tree,
        stdout="progress the launch already printed",
        stderr="a diagnostic the launch already printed",
        relayed=True,
    )

    with caplog.at_level(logging.DEBUG, logger=service.__name__):
        outcome = service.run_suite(workers=1, base=feature_tree, spawn=spawn)

    assert outcome.result_set is not None
    assert relayed_records(caplog, 0) == []
    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "already printed" not in messages


def test_an_unmarked_worker_has_every_line_relayed_tagged_and_at_level(
    feature_tree: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Every non-blank line becomes one tagged parent record, stream by stream.

    The after-the-fact path, which the ``spawn`` seam's stubs take.  ``stdout``
    is relayed first and then ``stderr``; blank lines are dropped because they
    carry nothing; and every record carries ``[shard N]``, which is what makes
    the output of concurrent workers attributable.

    The levels are the stream's own defaults here - ``INFO`` for ``stdout`` and
    ``WARNING`` for ``stderr``, which is the AAP 0.4.1 split that
    ``app/logging_config.py`` routes to stdout and stderr respectively - because
    neither line names a level of its own.  What happens when one does is the
    next test.
    """
    spawn = RecordingSpawn(
        base=feature_tree,
        stdout="first progress line\n\nsecond progress line\n",
        stderr="a diagnostic line\n",
    )

    with caplog.at_level(logging.DEBUG, logger=service.__name__):
        service.run_suite(workers=1, base=feature_tree, spawn=spawn)

    records = relayed_records(caplog, 0)
    assert [record.getMessage() for record in records] == [
        "[shard 0] first progress line",
        "[shard 0] second progress line",
        "[shard 0] a diagnostic line",
    ]
    assert [record.levelno for record in records] == [
        logging.INFO,
        logging.INFO,
        logging.WARNING,
    ]


def test_the_stream_is_a_severity_floor_and_never_a_ceiling(
    feature_tree: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A child's own level is honoured upwards, and can never pull a line down.

    The child is a separate interpreter with its own logging state, and
    behave's default ``logging_format`` puts ``LOG_<LEVEL>:<logger>:`` at
    column 0 - so a screenshot helper's suppressed-capture ``logger.exception``
    arrives as ``LOG_ERROR:app.reporting.screenshots: ...`` on ``stderr`` and
    again inside the captured-log block on ``stdout``.  Relaying every
    ``stderr`` line at ``WARNING`` flattens exactly the records a run is judged
    on, so the parent emits at ``max(child level, stream floor)``:

    * an ``ERROR`` token is honoured on **either** stream, so the stdout copy
      is not silently demoted to progress; and
    * a ``DEBUG`` token on ``stderr`` stays at ``WARNING``, which keeps the
      published stream split intact and stops a child muting its own
      diagnostics by printing a low token - a forgery that must not be
      honoured.

    The token itself is left in the text, because it names the child logger
    that spoke and that identity is most of the diagnostic's value.
    """
    spawn = RecordingSpawn(
        base=feature_tree,
        stdout="LOG_ERROR:app.reporting.screenshots: the capture was suppressed",
        stderr="LOG_DEBUG:behave: selecting features",
    )

    with caplog.at_level(logging.DEBUG, logger=service.__name__):
        service.run_suite(workers=1, base=feature_tree, spawn=spawn)

    records = relayed_records(caplog, 0)
    assert len(records) == 2

    upgraded, floored = records
    assert upgraded.levelno == logging.ERROR
    assert "LOG_ERROR:app.reporting.screenshots:" in upgraded.getMessage()

    assert floored.levelno == logging.WARNING
    assert "LOG_DEBUG:behave:" in floored.getMessage()


def test_no_relayed_line_is_trusted_as_log_text(
    feature_tree: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The relay renders through the module that owns the console contract.

    What arrives is whatever the engine, a driver, a page object or a step
    printed, so every line goes through
    :func:`~app.logging_config.render_worker_line` before it reaches a record -
    the single implementation of that rendering in the port.  Three of its
    guarantees are observable from here, and each of them is the reason the
    ``[shard N]`` tag can be trusted at all:

    * **Control-safe.**  A control character cannot break a record into a
      second, **untagged** line and a terminal escape sequence cannot recolour
      or erase what the console already printed (CWE-117).  The two halves of
      that are visible separately: a character the caller's own
      ``splitlines`` recognises produces two parent records and *both* carry
      the tag, while one it does not - a bell, a backspace, a ``NUL`` - is
      spelled out printably inside the single record it belongs to.
    * **Redacted** of credential-shaped content, which includes the shape of
      this suite's own ``User enters "<username>" username`` step text: a child
      diagnostic quoting a substituted step no longer carries the account into
      a CI console.  Redaction is **log-only** - AAP 0.8 requires the features,
      the JSON, the manifest and both HTML reports to carry that fixture data
      verbatim, and a console log is none of those.
    * **Nothing is silenced.**  The engine's diagnostics are contract-required
      output: sanitizing them is the fix, dropping them would be a regression,
      so the record is still there and still names what happened.
    """
    spawn = RecordingSpawn(
        base=feature_tree,
        stdout="bell\x07and \x1b[31mcolour\x1b[0m\ncarriage\rreturn",
        stderr='User enters "a-real-account" username',
    )

    with caplog.at_level(logging.DEBUG, logger=service.__name__):
        service.run_suite(workers=1, base=feature_tree, spawn=spawn)

    records = relayed_records(caplog, 0)
    messages = [record.getMessage() for record in records]

    # Every record is tagged - which is the guarantee, and is what a forged
    # line break would have broken.
    assert len(messages) == 4, messages
    assert all(message.startswith("[shard 0] ") for message in messages)
    for message in messages:
        assert "\r" not in message
        assert "\n" not in message
        assert "\x1b" not in message
        assert "\x07" not in message

    # The bell is spelled out printably rather than emitted, the escape
    # sequence is gone and the text it coloured is kept.
    assert messages[0] == "[shard 0] bell\\aand colour"

    # A break ``splitlines`` recognises is two records, both attributable.
    assert messages[1:3] == ["[shard 0] carriage", "[shard 0] return"]

    # The credential is gone and the line that carried it is not.
    assert "a-real-account" not in messages[3]
    assert "username" in messages[3]


def test_what_the_outcome_carries_this_module_does_not_also_log(
    feature_tree: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """One fact, one emitter - and for these facts the emitter is the CLI.

    A dead shard's reason and a tolerated selection problem are both built
    here, each naming its shard or its file, and both are **carried** on the
    outcome rather than logged: ``app/cli.py`` emits one record counting them
    and one naming each, beside the exit class each implies
    (``tests/test_cli.py``, ``test_exit_row_4_...`` and
    ``test_exit_status_is_independent_of_logged_errors``).  Emitting them here
    as well would put one incident in the CI console twice under two logger
    names, doubling the error count a publisher and a reader see for a run
    whose status the exit contract keeps at ``0``.

    So what is asserted is the absence on this side together with the presence
    on the outcome - an absence alone would be satisfied by a service that
    lost the information altogether.
    """
    write_feature(
        feature_tree,
        "AlsoBroken.feature",
        "Feature: broken\n  Scenario: one\n    Given a precondition\n"
        "  Scenariox: not a keyword\n    Whatever this is\n",
    )

    spawn = RecordingSpawn(base=feature_tree, behaviour={1: NO_FILE})

    with caplog.at_level(logging.DEBUG, logger=service.__name__):
        outcome = service.run_suite(workers=2, base=feature_tree, spawn=spawn)

    # Both facts exist, so neither half of the assertion below is vacuous.
    assert len(outcome.dead_shards) == 1
    assert len(outcome.parse_errors) == 1
    reason = outcome.dead_shards[0]
    assert "shard" in reason

    service_errors = [
        record.getMessage()
        for record in caplog.records
        if record.name == service.__name__ and record.levelno >= logging.ERROR
    ]
    assert service_errors == [], service_errors

    emitted = "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name == service.__name__
    )
    assert reason not in emitted
    for problem in outcome.parse_errors:
        assert problem not in emitted


def test_every_shard_dead_yields_no_result_set_at_all(feature_tree: Path) -> None:
    """Not one readable shard file is the empty-merge state.

    AAP 0.4.1's "the merge produces no result set at all" row: ``result_set`` is
    ``None`` **and** ``merge_produced_nothing`` is set, which is what lets
    ``app/cli.py`` exit non-zero and write nothing while leaving ``target/``
    exactly as the clean step left it.
    """
    spawn = RecordingSpawn(
        base=feature_tree, behaviour={0: NO_FILE, 1: EMPTY_FILE, 2: INVALID_JSON}
    )
    outcome = service.run_suite(workers=3, base=feature_tree, spawn=spawn)

    assert outcome.result_set is None
    assert outcome.merge_produced_nothing is True
    assert len(outcome.dead_shards) == 3
    assert outcome.selected_count == len(all_locations())
    assert outcome.worker_count == 3


def test_zero_selected_scenarios_yields_an_empty_document_not_none(
    feature_tree: Path,
) -> None:
    """Nothing selected is an **empty** result set, and no worker is spawned.

    The other of the two states that must never be conflated: AAP 0.4.1's "zero
    scenarios selected" row is status ``0`` with all four artifacts written
    empty, so the Jenkins publisher always gets a JSON to read.  A single falsy
    check on ``result_set`` would collapse this into the empty-merge state and
    break both rows, which is why the document is asserted key by key here.
    """
    spawn = RecordingSpawn(base=feature_tree)
    outcome = service.run_suite(
        tags="@NoSuchTagInThisTree", base=feature_tree, spawn=spawn
    )

    assert spawn.calls == []
    assert outcome.selected_count == 0
    assert outcome.worker_count == 0
    assert outcome.shard_results == ()
    assert outcome.dead_shards == ()
    assert outcome.merge_produced_nothing is False

    assert outcome.result_set is not None
    assert outcome.result_set["features"] == []
    assert outcome.result_set["schema_version"] == events.SCHEMA_VERSION
    assert outcome.result_set["tag_expression"] == "@NoSuchTagInThisTree"
    assert outcome.result_set["dry_run"] is False
    assert not paths.workers_dir(feature_tree).exists()


def test_the_empty_selection_and_the_empty_merge_are_distinguishable(
    feature_tree: Path,
) -> None:
    """The two zero-result states differ in both value and consequence.

    Asserted side by side, because the failure this guards against is a
    consumer that reads one of them as the other.
    """
    nothing_selected = service.run_suite(
        tags="@NoSuchTagInThisTree",
        base=feature_tree,
        spawn=RecordingSpawn(base=feature_tree),
    )
    nothing_merged = service.run_suite(
        workers=1,
        base=feature_tree,
        spawn=RecordingSpawn(base=feature_tree, behaviour={0: NO_FILE}),
    )

    assert nothing_selected.result_set is not None
    assert nothing_selected.merge_produced_nothing is False
    assert nothing_selected.dead_shards == ()

    assert nothing_merged.result_set is None
    assert nothing_merged.merge_produced_nothing is True
    assert len(nothing_merged.dead_shards) == 1


def test_run_suite_never_terminates_the_interpreter(
    feature_tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No exit call is reachable from a run, whatever became of its shards.

    ``app/cli.py`` owns the exit codes and decides from the outcome's fields
    alone; this module's subject never terminates the interpreter and never
    prints an exit code.  Both exit functions are replaced with a failing stub
    for the duration, and the source is checked for the low-level one that a
    stub cannot intercept.
    """

    def forbidden(status: object = 0) -> None:
        """Fail the test rather than exiting the interpreter."""
        raise AssertionError(f"run_suite tried to exit with {status!r}")

    monkeypatch.setattr(sys, "exit", forbidden)
    monkeypatch.setattr(os, "_exit", forbidden)

    behaviours = ({}, {0: NO_FILE}, {0: LAUNCH_RAISES}, {0: INVALID_JSON})
    for behaviour in behaviours:
        outcome = service.run_suite(
            workers=1,
            base=feature_tree,
            spawn=RecordingSpawn(base=feature_tree, behaviour=dict(behaviour)),
        )
        assert isinstance(outcome, service.RunOutcome)

    source = Path(service.__file__).read_text(encoding="utf-8")
    assert "sys.exit" not in source
    assert "os._exit" not in source


def test_the_outcome_carries_the_runs_own_parameters(feature_tree: Path) -> None:
    """``RunOutcome`` reports selection, pool size, filter and flags faithfully.

    Every signal is surfaced independently and no precedence is encoded here:
    when several coexist, which one decides the status is ``app/cli.py``'s
    decision.
    """
    outcome = service.run_suite(
        tags="@Alpha",
        browser="chrome",
        workers=2,
        dry_run=True,
        base=feature_tree,
        spawn=RecordingSpawn(base=feature_tree),
    )

    assert outcome.selected_count == len(ALPHA_LOCATION_LINES)
    assert outcome.worker_count == 2
    assert outcome.tag_expression == "@Alpha"
    assert outcome.dry_run is True
    assert outcome.rerun is False
    assert outcome.parse_errors == ()
    assert len(outcome.shard_results) == 2
    assert [result.plan.index for result in outcome.shard_results] == [0, 1]


def test_the_outcome_records_the_users_expression_never_the_tautology(
    feature_tree: Path,
) -> None:
    """A run with no filter records ``None``, not the neutral expression.

    The tautology is the mechanism that suppresses ``behave.ini``'s
    ``default_tags``, not a filter anybody asked for, so it must never surface
    in a report - while still appearing on every worker's command line.
    """
    spawn = RecordingSpawn(base=feature_tree)
    outcome = service.run_suite(workers=1, base=feature_tree, spawn=spawn)

    assert outcome.tag_expression is None
    assert outcome.result_set is not None
    assert outcome.result_set["tag_expression"] is None

    command = spawn.calls[0].command
    assert command[command.index(TAGS_FLAG) + 1] == service.NEUTRAL_TAG_EXPRESSION


def test_a_parse_problem_is_reported_without_becoming_a_dead_shard(
    feature_tree: Path,
) -> None:
    """A tolerated problem lands in ``parse_errors`` and nowhere else.

    AAP 0.4.1 keeps a parse problem at status ``0`` with all four artifacts
    written, so it must not be reported as a dead worker - the two rows of the
    exit table have different statuses.
    """
    write_feature(
        feature_tree,
        "AlsoBroken.feature",
        "Feature: broken\n  Scenario: one\n    Given a precondition\n"
        "  Scenariox: not a keyword\n    Whatever this is\n",
    )

    outcome = service.run_suite(
        workers=2, base=feature_tree, spawn=RecordingSpawn(base=feature_tree)
    )

    assert len(outcome.parse_errors) == 1
    assert outcome.dead_shards == ()
    assert outcome.result_set is not None
    assert surviving_locations(outcome.result_set) == set(all_locations())


def test_merge_worker_results_reports_the_same_deaths_as_a_run(
    feature_tree: Path,
) -> None:
    """The merge primitive is usable on its own and agrees with ``run_suite``.

    ``app/services/report_service.py`` consumes the merged document and never
    the shards, so the merge is exercised here directly as well as through a
    run: a shard whose file was never written is dead, and the remaining
    document carries the other shard's scenarios.
    """
    selected, _ = service.select_scenarios(base=feature_tree)
    plans = service.shard_scenarios(selected, 2, base=feature_tree)
    service.prepare_workers_dir(base=feature_tree)
    try:
        events.dump_result_set(
            worker_document(plans[0].locations), plans[0].output_path
        )
        shard_results = [
            service.ShardResult(plan=plans[0], returncode=0, dead=False, reason=None),
            service.ShardResult(plan=plans[1], returncode=0, dead=False, reason=None),
        ]

        merged, problems = service.merge_worker_results(
            shard_results, base=feature_tree
        )
    finally:
        service.cleanup_workers_dir(base=feature_tree)

    assert merged is not None
    assert surviving_locations(merged) == set(plans[0].locations)
    assert len(problems) == 1
    assert "shard 1" in problems[0]


def test_merging_nothing_yields_none_rather_than_an_empty_document() -> None:
    """An empty shard list merges to ``None``, which only the caller can read.

    ``None`` is returned rather than an empty document because the two mean
    different things to the exit contract, and only the caller knows which
    situation it is in.
    """
    merged, problems = service.merge_worker_results([])

    assert merged is None
    assert problems == []


# =========================================================================== #
# Rerun selection (FailedTestRunner.java:9-12, AAP 0.6's round-trip
# requirement)
#
# "features = '@target/rerun.txt'" was that runner's whole configuration, and
# AAP 0.6 requires that "the file the writer produces must select exactly the
# scenarios that failed".  The grammar has exactly one owner,
# ``app/reporting/rerun_report.py``, so the service parses nothing itself.
# =========================================================================== #


def write_manifest(base: Path, text: str) -> Path:
    """Write a rerun manifest at the path the paths module owns.

    Args:
        base: The temporary checkout root.
        text: The manifest's exact contents.

    Returns:
        The manifest path.
    """
    destination = paths.ensure_parent(paths.rerun_txt_path(base))
    destination.write_text(text, encoding="utf-8")
    return destination


def manifest_line(feature_path: str, *lines: int) -> str:
    """Build one manifest line in the format the writer produces.

    One line per feature: the ``file:`` scheme, the path, then each failing
    line appended colon-separated, ascending (AAP 0.6's measured baseline,
    ``file:src/main/resources/features/Crm.feature:9:24``).

    Args:
        feature_path: The feature's repository-relative path.
        *lines: The failing scenarios' line numbers.

    Returns:
        The line, terminated.
    """
    suffix = "".join(
        f"{rerun_report.LINE_SEPARATOR}{line}" for line in sorted(lines)
    )
    return (
        f"{paths.FILE_URI_SCHEME}{feature_path}{suffix}{rerun_report.LINE_ENDING}"
    )


def test_rerun_selection_delegates_the_grammar_to_its_owner(
    feature_tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The manifest is parsed by ``rerun_report.parse_rerun_file`` and nowhere else.

    One owner for the grammar is what keeps the format the writer produces and
    the format the rerun consumes from drifting apart, so the delegation itself
    is the contract: the name is replaced in the service's namespace and the
    service is observed to use its result rather than reading the file.
    """
    calls: list[dict[str, Any]] = []
    entry = rerun_report.RerunEntry(
        path=ALPHA_FEATURE.feature_path, lines=(ALPHA_LOCATION_LINES[0],)
    )

    def recording_parse(**kwargs: Any) -> list[rerun_report.RerunEntry]:
        """Record the call and return one entry, reading nothing from disk."""
        calls.append(kwargs)
        return [entry]

    monkeypatch.setattr(service, "parse_rerun_file", recording_parse)

    selected, problems = service.select_rerun_scenarios(base=feature_tree)

    assert calls == [{"base": feature_tree}]
    assert problems == []
    assert [unit.location for unit in selected] == list(entry.locations)


def test_a_manifest_naming_known_scenarios_round_trips_exactly(
    feature_tree: Path,
) -> None:
    """Every location the manifest names is selected, and nothing else is.

    The grouped format is one line per feature with its failing lines appended,
    so a round trip has to survive both the grouping and the enrichment: each
    selected unit comes back with the scenario's own name and effective tags
    where the feature file still declares one at that line.
    """
    wanted = [
        f"{ALPHA_FEATURE.feature_path}{rerun_report.LINE_SEPARATOR}{line}"
        for line in (ALPHA_LOCATION_LINES[0], ALPHA_LOCATION_LINES[2])
    ] + [f"{BETA_FEATURE.feature_path}{rerun_report.LINE_SEPARATOR}3"]

    write_manifest(
        feature_tree,
        manifest_line(
            ALPHA_FEATURE.feature_path,
            ALPHA_LOCATION_LINES[0],
            ALPHA_LOCATION_LINES[2],
        )
        + manifest_line(BETA_FEATURE.feature_path, 3),
    )

    selected, problems = service.select_rerun_scenarios(base=feature_tree)

    assert problems == []
    assert [unit.location for unit in selected] == wanted
    by_location = {unit.location: unit for unit in selected}
    assert by_location[wanted[0]].name == "first alpha"
    assert by_location[wanted[0]].tags == ("@Alpha",)
    assert by_location[wanted[-1]].tags == ()


def test_the_writers_manifest_selects_exactly_what_failed(
    feature_tree: Path,
) -> None:
    """A run's own manifest, written and read back, selects the failures.

    AAP 0.6 states the requirement as a round trip rather than as a format, so
    it is asserted as one: the merged document of a canned run is handed to the
    manifest writer, and the service's rerun selection over what it wrote is
    compared against the locations whose canned status was ``failed``.
    """
    outcome, _ = run_with_workers(feature_tree, 2)
    assert outcome.result_set is not None
    failures = {
        location for location in all_locations() if status_of(location) == "failed"
    }
    assert failures, "the canned documents recorded no failure to re-run"

    rerun_report.write_rerun_txt(outcome.result_set, base=feature_tree)
    selected, problems = service.select_rerun_scenarios(base=feature_tree)

    assert problems == []
    assert {unit.location for unit in selected} == failures


def test_a_missing_manifest_is_a_reported_problem(feature_tree: Path) -> None:
    """No manifest yields no scenarios, one problem and no exception.

    AAP 0.4.1 keeps "a missing or malformed rerun manifest" at status ``0``
    with the problem reported on stderr.
    """
    assert not paths.rerun_txt_path(feature_tree).exists()

    selected, problems = service.select_rerun_scenarios(base=feature_tree)

    assert selected == []
    assert len(problems) == 1
    assert str(paths.rerun_txt_path(feature_tree)) in problems[0]


def test_a_malformed_manifest_is_a_reported_problem(feature_tree: Path) -> None:
    """A line that is not a manifest entry is reported, not raised."""
    write_manifest(feature_tree, f"not a manifest entry{rerun_report.LINE_ENDING}")

    selected, problems = service.select_rerun_scenarios(base=feature_tree)

    assert selected == []
    assert len(problems) == 1
    assert str(paths.rerun_txt_path(feature_tree)) in problems[0]


def test_an_empty_manifest_is_neither_a_selection_nor_a_problem(
    feature_tree: Path,
) -> None:
    """A run with no failures writes zero bytes, and reading that back is normal."""
    write_manifest(feature_tree, "")

    selected, problems = service.select_rerun_scenarios(base=feature_tree)

    assert selected == []
    assert problems == []


def test_a_stale_manifest_line_is_reported_and_still_re_run(
    feature_tree: Path,
) -> None:
    """A line with no scenario on it is noted and kept rather than dropped.

    Dropping it would silently discard a failure, which is the one thing a
    rerun must not do - so the location survives unenriched and the problem is
    reported alongside it.
    """
    stale = max(ALPHA_LOCATION_LINES) + 100
    write_manifest(feature_tree, manifest_line(ALPHA_FEATURE.feature_path, stale))

    selected, problems = service.select_rerun_scenarios(base=feature_tree)

    assert [unit.location for unit in selected] == [
        f"{ALPHA_FEATURE.feature_path}{rerun_report.LINE_SEPARATOR}{stale}"
    ]
    assert len(problems) == 1
    assert str(stale) in problems[0]
    assert selected[0].name == ""
    assert selected[0].tags == ()


def test_a_manifest_naming_an_absent_feature_file_is_reported(
    feature_tree: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A feature that no longer exists is reported, and nothing else is lost.

    The entry never reaches this service: the manifest's grammar and its
    confinement both belong to
    :func:`~app.reporting.rerun_report.parse_rerun_file`, which drops an entry
    whose feature file is not a regular file directly inside the features
    directory and warns about it - a location handed to the engine has to be
    the file it was checked as, and ``features/Vanished.feature:3`` is not
    executable by anything.

    So the guarantee asserted is the one the port actually makes, and all three
    parts of it: nothing raises, the drop is **reported** on stderr by the
    module that owns the manifest (``WARNING`` and above is what
    ``app/logging_config.py`` routes there), and the real failures of every
    other feature in the same manifest are still selected - dropping one stale
    line must not discard the rest, which is what raising would have done.
    """
    missing_path = f"{paths.NORMALIZED_FEATURES_PREFIX}Vanished.feature"
    survivor_line = min(ALPHA_LOCATION_LINES)
    write_manifest(
        feature_tree,
        "\n".join(
            (
                manifest_line(missing_path, 3),
                manifest_line(ALPHA_FEATURE.feature_path, survivor_line),
            )
        ),
    )

    with caplog.at_level(logging.WARNING, logger=RERUN_REPORT_LOGGER_NAME):
        selected, problems = service.select_rerun_scenarios(base=feature_tree)

    # The absent feature is not executed, and it is named where an operator
    # reads it.
    assert missing_path not in {unit.feature_path for unit in selected}
    reported = [
        record.getMessage()
        for record in caplog.records
        if record.name == RERUN_REPORT_LOGGER_NAME
        and record.levelno >= logging.WARNING
    ]
    assert any("Vanished.feature" in message for message in reported), reported

    # The rest of the manifest survived it.
    assert [unit.location for unit in selected] == [
        f"{ALPHA_FEATURE.feature_path}{rerun_report.LINE_SEPARATOR}{survivor_line}"
    ]
    assert problems == []


@pytest.mark.parametrize(
    "hostile",
    [
        "/etc/passwd",
        "../../elsewhere/Escaped.feature",
        "features/../../Escaped.feature",
    ],
)
def test_a_hostile_manifest_entry_is_reported_and_never_raises(
    feature_tree: Path, hostile: str
) -> None:
    """A manifest naming a path outside the suite is a reported problem.

    Deliberately narrow: what is asserted is that the condition is *reported*
    and that nothing propagates, because whether such an entry is kept
    unresolved or rejected outright is the containment decision
    ``app/utils/paths.py`` and its callers own - and either way it must not
    become an exception, since AAP 0.4.1 keeps a malformed manifest at status
    ``0``.
    """
    write_manifest(feature_tree, manifest_line(hostile, 3))

    selected, problems = service.select_rerun_scenarios(base=feature_tree)

    assert problems, "a hostile manifest entry was accepted without comment"
    assert all(isinstance(unit, service.ScenarioRef) for unit in selected)


def test_a_legacy_manifest_prefix_is_normalized(feature_tree: Path) -> None:
    """A manifest written against the Java layout still selects.

    AAP deviation 1 moved the features from ``src/main/resources/features/`` to
    ``features/`` while preserving their filenames, and the normalisation is
    the paths module's - the service holds no prefix of its own.
    """
    legacy = f"{paths.LEGACY_FEATURES_PREFIX}{ALPHA_FEATURE.filename}"
    write_manifest(feature_tree, manifest_line(legacy, ALPHA_LOCATION_LINES[0]))

    selected, problems = service.select_rerun_scenarios(base=feature_tree)

    assert problems == []
    assert [unit.location for unit in selected] == [
        f"{ALPHA_FEATURE.feature_path}"
        f"{rerun_report.LINE_SEPARATOR}{ALPHA_LOCATION_LINES[0]}"
    ]


def test_a_rerun_applies_no_tag_filter_and_publishes_no_document(
    feature_tree: Path,
) -> None:
    """``--rerun`` clears the filter, writes nothing, and is not an empty merge.

    Three properties of one row of AAP 0.4.1.  ``FailedTestRunner`` declared no
    tags, so a filter arriving here is ignored rather than honoured - honouring
    it is precisely how a rerun silently skips the failures it exists to
    re-run - and it declared an empty plugin list, so the run publishes no
    document at all.  That ``None`` is **not** the empty-merge condition:
    ``merge_produced_nothing`` stays ``False`` and existing artifacts are left
    untouched.
    """
    write_manifest(
        feature_tree,
        manifest_line(ALPHA_FEATURE.feature_path, ALPHA_LOCATION_LINES[0])
        + manifest_line(BETA_FEATURE.feature_path, *BETA_LOCATION_LINES),
    )
    untouched = paths.ensure_parent(paths.cucumber_json_path(feature_tree))
    untouched.write_text("{}", encoding="utf-8")

    spawn = RecordingSpawn(base=feature_tree)
    outcome = service.run_suite(
        tags=SMOKE_TAG, rerun=True, workers=2, base=feature_tree, spawn=spawn
    )

    assert outcome.rerun is True
    assert outcome.result_set is None
    assert outcome.merge_produced_nothing is False
    assert outcome.tag_expression is None
    assert outcome.selected_count == 3
    assert outcome.dead_shards == ()
    assert untouched.read_text(encoding="utf-8") == "{}"

    for call in spawn.calls:
        command = list(call.command)
        assert command.count(TAGS_FLAG) == 1
        assert command[command.index(TAGS_FLAG) + 1] == service.NEUTRAL_TAG_EXPRESSION
        assert SMOKE_TAG not in command
    assert not paths.workers_dir(feature_tree).exists()


def test_a_rerun_with_no_manifest_stays_at_a_reported_problem(
    feature_tree: Path,
) -> None:
    """A rerun with nothing to re-run spawns nothing and reports the manifest.

    Status ``0`` per AAP 0.4.1, which is why the outcome carries a problem and
    neither a dead shard nor the empty-merge flag.
    """
    spawn = RecordingSpawn(base=feature_tree)
    outcome = service.run_suite(rerun=True, base=feature_tree, spawn=spawn)

    assert spawn.calls == []
    assert outcome.selected_count == 0
    assert outcome.worker_count == 0
    assert len(outcome.parse_errors) == 1
    assert outcome.dead_shards == ()
    assert outcome.result_set is None
    assert outcome.merge_produced_nothing is False
    assert outcome.rerun is True


def test_a_rerun_still_reports_a_dead_worker(feature_tree: Path) -> None:
    """A rerun's dead shard is reported even though the run publishes nothing.

    The dead-worker row of the exit table is independent of the rerun row: no
    precedence is encoded in the outcome, so both signals are present and
    ``app/cli.py`` decides.
    """
    write_manifest(
        feature_tree,
        manifest_line(ALPHA_FEATURE.feature_path, *ALPHA_LOCATION_LINES),
    )

    spawn = RecordingSpawn(base=feature_tree, behaviour={0: NO_FILE})
    outcome = service.run_suite(
        rerun=True, workers=2, base=feature_tree, spawn=spawn
    )

    assert len(outcome.dead_shards) == 1
    assert "shard 0" in outcome.dead_shards[0]
    assert outcome.result_set is None
    assert outcome.merge_produced_nothing is False


# =========================================================================== #
# Import boundaries
#
# The module's own docstring lists what it does not own: the artifact paths
# belong to app.utils.paths, the result schema and the merge algorithm to
# app.reporting.events, the manifest grammar to app.reporting.rerun_report.
# It runs in a worker parent that builds no web application and drives no
# browser, so neither the web framework nor the browser bindings may be
# reachable from it - and importing either would make a cheap orchestration
# import expensive as well as wrong.
#
# Asserted statically, by reading the module's own source: a runtime check of
# ``sys.modules`` would depend on what the rest of the suite happened to import
# first, and a subprocess check would start a process this module has promised
# not to start.
# =========================================================================== #

#: Module prefixes the subject may not import, each with the reason it may not.
FORBIDDEN_IMPORT_PREFIXES: Final[tuple[str, ...]] = (
    # The viewer's framework: these code paths run in a worker parent that
    # builds no application, and app/__init__.py defers every Flask import
    # into create_app() precisely so an orchestration import stays cheap.
    "flask",
    # Only app/automation/{driver,waits,interactions}.py may import selenium.
    "selenium",
    "webdriver_manager",
    # The six configuration keys are read inside the worker, by the engine's
    # own process - never by the parent that spawns it.
    "app.config",
    # The four writers are driven from the merged document by the sibling
    # service, after this module returns; importing it here would put the
    # writers inside the worker parent and invert the two services' order.
    "app.services.report_service",
)

#: The one module the subject may reach paths through.
PATHS_MODULE: Final[str] = "app.utils.paths"

#: The one module the subject may reach the result schema and the merge
#: primitives through.
EVENTS_MODULE: Final[str] = "app.reporting.events"


def service_tree() -> ast.Module:
    """Parse the module under test into an abstract syntax tree.

    Returns:
        The parsed module, read from the file the imported module was loaded
        from so that the source examined is the source that runs.
    """
    return ast.parse(Path(service.__file__).read_text(encoding="utf-8"))


def imported_modules(tree: ast.Module) -> set[str]:
    """Return every module name the tree imports.

    Args:
        tree: The parsed module.

    Returns:
        The dotted names from ``import x`` and ``from x import y`` statements.
        A relative import contributes the empty string, which no assertion
        below matches - and the subject uses none.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
    return names


def code_string_constants(tree: ast.Module) -> list[str]:
    """Return the tree's string constants, docstrings excluded.

    Docstrings and free-standing string expressions are excluded because they
    are prose: this module's subject documents ``target/.workers/`` in its own
    docstring, and that is documentation rather than a path literal the code
    could use.

    Args:
        tree: The parsed module.

    Returns:
        Every string constant reachable by the running code.
    """
    prose: set[int] = set()
    for node in ast.walk(tree):
        # ``body`` is a statement list on a module, class, function and every
        # compound statement, and a single expression on a lambda or a
        # conditional expression - only the former can hold a docstring.
        body = getattr(node, "body", None)
        for child in body if isinstance(body, list) else ():
            if (
                isinstance(child, ast.Expr)
                and isinstance(child.value, ast.Constant)
                and isinstance(child.value.value, str)
            ):
                prose.add(id(child.value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in prose
    ]


def test_the_service_imports_neither_the_web_framework_nor_the_browser() -> None:
    """No Flask, no selenium, no ``app.config`` and no sibling service."""
    imports = imported_modules(service_tree())

    for forbidden in FORBIDDEN_IMPORT_PREFIXES:
        # Matched at a module boundary, so a package named like a forbidden one
        # without being it cannot be caught by accident.
        offenders = [
            name
            for name in imports
            if name == forbidden or name.startswith(f"{forbidden}.")
        ]
        assert not offenders, f"{service.__name__} imports {offenders}"


def test_the_service_reaches_paths_and_the_merge_through_their_owners() -> None:
    """Paths come from ``app.utils.paths``, the merge from ``app.reporting.events``.

    Asserted both ways: the owner modules are imported, and no other module of
    the application package is - so a path, a merge primitive or the rendering
    of a worker's output cannot be arriving from anywhere else.

    ``app.logging_config`` is one of those owners rather than an exception to
    the rule.  The relay turns a child process's ``stdout`` and ``stderr`` into
    parent records, and every line of it is rendered by
    :func:`~app.logging_config.render_worker_line` - control-safe, bounded and
    redacted, with the child's own severity honoured above the stream's floor.
    That module owns what is allowed onto the console, so a second
    implementation of that rendering here is exactly the drift this assertion
    exists to catch.

    What that module contributes at **import** time is the rendering and
    nothing else.  ``configure_logging`` is reached too, but from inside the
    pool task alone: a pool worker is a new process, so it installs the
    handler split itself rather than inheriting it, and the import is
    deliberately function-scoped so that importing this module - which every
    child does, and which selection and merging in the parent also do - stays
    free of it.  Both halves are asserted, because collapsing them would let a
    module-scope ``configure_logging`` in.
    """
    imports = imported_modules(service_tree())
    application_imports = {name for name in imports if name.split(".")[0] == "app"}

    assert PATHS_MODULE in imports
    assert EVENTS_MODULE in imports
    assert application_imports == {
        PATHS_MODULE,
        EVENTS_MODULE,
        "app.reporting.rerun_report",
        "app.logging_config",
    }

    # At module scope the rendering, and only the rendering.
    tree = service_tree()
    at_module_scope = {
        alias.asname or alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == "app.logging_config"
        for alias in node.names
    }
    assert at_module_scope == {"render_worker_line"}, at_module_scope

    # And the console contract a child installs for itself, inside a function.
    everywhere = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "app.logging_config"
        for alias in node.names
    }
    assert everywhere - at_module_scope == {"configure_logging"}, everywhere


def path_shaped(literal: str) -> list[str]:
    """Return the path separators of one string constant, plurals excepted.

    A separator anywhere in a string the code can use is treated as a path,
    which is as strict as it can be and is deliberate: the port's directory and
    artifact names belong to ``app/utils/paths.py`` alone, and a fragment of a
    path is enough for the merge and the publisher to come to disagree about
    where a worker wrote.

    The one exception is a separator inside a **parenthesised group**, which is
    how English writes an inflected plural rather than how anything writes a
    path - the relay's ``"Retained %d live run director(y/ies) in %s: %s"`` is
    the case in this module.  Nothing else is exempt, so a literal that merely
    looks harmless is still reported.

    Args:
        literal: One string constant the running code can use.

    Returns:
        Every separator that is not inside a parenthesised group, with a little
        surrounding text so a failure can be acted on; empty when there is
        none.
    """
    found: list[str] = []
    depth = 0
    for index, character in enumerate(literal):
        if character == "(":
            depth += 1
            continue
        if character == ")":
            depth = max(0, depth - 1)
            continue
        if character in "/\\" and depth == 0:
            found.append(literal[max(0, index - 20) : index + 20])
    return found


def test_the_service_contains_no_path_literal() -> None:
    """Not one string the code can use carries a path.

    ``app/utils/paths.py`` owns every directory and artifact name in the port,
    and a second spelling of one here is how the merge and the publisher would
    come to disagree about where a worker wrote.  The file extension
    ``.feature`` is the one path-adjacent literal the module declares, and it is
    an extension rather than a path: the paths module deliberately declares no
    Gherkin suffix.

    Every separator is a violation, with one exception stated and bounded in
    :func:`path_shaped`: the inflected plural of a log template, written
    ``director(y/ies)``, which is English rather than a path.  The owned names
    are checked separately, so a literal naming one with no separator near it
    is still caught.
    """
    literals = code_string_constants(service_tree())
    assert literals, "the module was parsed but no code string was found"

    for literal in literals:
        assert not path_shaped(literal), f"{literal!r} looks like a path"
        assert paths.TARGET_DIR_NAME not in literal
        for spec in paths.ARTIFACT_SPECS:
            assert spec.key not in literal
        assert paths.WORKERS_DIR_NAME not in literal


def test_the_services_public_surface_is_the_one_its_callers_use() -> None:
    """``__all__`` names every entry point this module and ``app/cli.py`` use.

    The service is the CLI's only route to a run, and a name missing from its
    export list is a name a caller is reaching for by accident.
    """
    exported = set(service.__all__)
    used = {
        "NEUTRAL_TAG_EXPRESSION",
        "RunOutcome",
        "ScenarioRef",
        "ShardPlan",
        "ShardResult",
        "build_worker_command",
        "cleanup_workers_dir",
        "default_worker_count",
        "merge_worker_results",
        "prepare_workers_dir",
        "run_suite",
        "select_rerun_scenarios",
        "select_scenarios",
        "shard_scenarios",
    }

    assert used <= exported
    for name in exported:
        assert hasattr(service, name)


# =========================================================================== #
# The scenario lifecycle (features/environment.py, the port of Hooks.java)
#
# The behavioural source is eight lines long:
#
#     @After                                          // Hooks.java:11
#     public void teardownScenario(Scenario scenario){
#         if(scenario.isFailed()){                    // :13
#             byte [] screenshot = ((TakesScreenshot) Driver.getDriver())
#                     .getScreenshotAs(OutputType.BYTES);          // :14
#             scenario.attach(screenshot, "image/png", scenario.getName()); // :15
#         }
#         Driver.closeDriver();                       // :17 - OUTSIDE the if
#     }
#
# AAP 0.6 states the intended behaviour the port implements per Conflict 6:
# capture "only on failure, once, before the driver is quit", a capture failure
# logged with the scenario's status unchanged, and teardown unconditional.
#
# The hooks are driven directly, with their collaborators patched in the
# ``features.environment`` namespace - never in selenium, which this module
# does not import and which the hooks never reach.
# =========================================================================== #

#: The names :mod:`features.environment` binds at import time and reaches its
#: collaborators through.  Patching these is patching the seam; patching
#: anything deeper would test a different module.
HOOK_COLLABORATORS: Final[tuple[str, ...]] = (
    "get_driver",
    "quit_driver",
    "capture_png",
    "set_userdata",
)

#: The recorder labels used to assert the *order* of the teardown steps from a
#: single shared log, which is the only way to assert that capture precedes the
#: quit rather than merely that both happened.
CAPTURE_STEP: Final[str] = "capture"
QUIT_STEP: Final[str] = "quit"

#: The bytes a patched capture returns.  A distinctive, non-empty payload, so
#: the attachment assertion is about this value and not about truthiness.
CAPTURED_PNG: Final[bytes] = b"\x89PNG\r\n\x1a\n-canned-capture"


class ScenarioStatus:
    """behave's scenario status, as the hook reads it.

    ``Scenario.isFailed()`` is true for an exception as well as for an assertion
    failure, so the analogue is behave's own ``status.has_failed()`` - which
    unions ``failed``, ``error``, ``hook_error``, ``cleanup_error``,
    ``undefined`` and ``pending``.  An equality test against a single status
    member would silently drop the screenshot for every scenario that died on
    an exception rather than an assertion, which in a Selenium suite is most of
    them.

    Attributes:
        failed: What :meth:`has_failed` reports.
        queries: How many times the hook asked.
    """

    def __init__(self, failed: bool) -> None:
        """Build a status.

        Args:
            failed: Whether this scenario is to report itself as failed.
        """
        self.failed = failed
        self.queries = 0

    def has_failed(self) -> bool:
        """Report the outcome, counting the question.

        Returns:
            ``True`` for a failed scenario.
        """
        self.queries += 1
        return self.failed


class FakeScenario:
    """behave's scenario object, with every write to it recorded.

    Nothing in the lifecycle may change a scenario's outcome: AAP 0.6 has the
    status read and never written, and a screenshot is evidence *about* a result
    rather than part of one.  This object records every attribute assignment so
    that a write to ``status`` - or to anything else - is a failure rather than
    an invisible side effect.

    Attributes:
        writes: Every ``(name, value)`` assignment made after construction.
    """

    def __init__(
        self,
        name: str,
        *,
        failed: bool,
        filename: str | None = None,
        line: int | None = None,
    ) -> None:
        """Build a scenario.

        Args:
            name: The scenario's name, which the Java ``attach`` call passed as
                its third argument and which the result collector supplies from
                the scenario it is already tracking.
            failed: Whether the scenario reports itself as failed.
            filename: The feature file behave reports the scenario from, or
                ``None`` to expose none - which a unit-test scenario object
                legitimately does, and which the identity the hook builds has
                to survive.
            line: The scenario's own line, or ``None`` for the same reason.

        Notes:
            The three attributes are installed through
            :meth:`object.__setattr__`, so constructing a scenario is not a
            *write* to one: :attr:`writes` stays empty until something under
            test assigns to it, which is the whole point of this object.
        """
        object.__setattr__(self, "writes", [])
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "status", ScenarioStatus(failed))
        object.__setattr__(self, "filename", filename)
        object.__setattr__(self, "line", line)

    def __setattr__(self, name: str, value: Any) -> None:
        """Record an assignment, then perform it.

        Args:
            name: The attribute assigned.
            value: The value assigned.
        """
        self.writes.append((name, value))
        object.__setattr__(self, name, value)


class AttachFailingContext(FakeContext):
    """A context whose ``attach`` raises, to test the teardown's ``finally``.

    Built on ``tests/conftest.py``'s :class:`~conftest.FakeContext` so that
    every other behaviour - the driver slot, the recorded attachments, the
    userdata-carrying config - is the shared one, and only the injected failure
    differs.
    """

    def attach(self, mime_type: str, data: Any) -> None:
        """Fail the way behave's formatter chain could.

        Args:
            mime_type: Ignored.
            data: Ignored.

        Raises:
            RuntimeError: Always.
        """
        raise RuntimeError("the formatter chain refused the attachment")


@dataclass
class HookRecorder:
    """Recorders for the four collaborators the hooks reach through.

    Attributes:
        steps: The teardown steps, in the order they happened - the shared log
            that makes "capture before quit" assertable.
        sessions: The sessions :func:`get_driver` handed out, in order.
        png: What the patched capture returns, or ``None`` to report a failed
            capture.
        capture_error: An exception the patched capture raises instead of
            returning.
        quit_error: An exception the patched quit raises instead of returning.
        captured: The driver each capture was handed.
        scenario_ids: The ``scenario_id`` each capture was handed, in the same
            order as :attr:`captured`.  The diagnostic identity of the scenario
            being photographed: ``app/reporting/screenshots.py`` suppresses a
            capture failure by design (AAP deviation 19), so its log record is
            the only trace such a failure leaves, and this is the one fact only
            the hook knows - which scenario the missing evidence belongs to.
        userdata: Each mapping ``set_userdata`` was given.
    """

    steps: list[str] = field(default_factory=list)
    sessions: list[Any] = field(default_factory=list)
    png: bytes | None = CAPTURED_PNG
    capture_error: BaseException | None = None
    quit_error: BaseException | None = None
    captured: list[Any] = field(default_factory=list)
    scenario_ids: list[str | None] = field(default_factory=list)
    userdata: list[Any] = field(default_factory=list)

    def install(
        self, monkeypatch: pytest.MonkeyPatch, *, session_factory: Any = None
    ) -> None:
        """Patch the four collaborator names in the hooks' own namespace.

        Args:
            monkeypatch: pytest's patcher, whose teardown restores the module.
            session_factory: Called with no arguments to produce each session
                :func:`get_driver` returns.  ``None`` yields a fresh object per
                call, which is what makes "one session per scenario" observable.
        """
        factory = session_factory or (lambda: object())

        def get_driver() -> Any:
            """Hand out a session, recording it."""
            session = factory()
            self.sessions.append(session)
            return session

        def capture_png(
            driver: Any, *, scenario_id: str | None = None
        ) -> bytes | None:
            """Record the capture, the driver and the scenario identity.

            ``scenario_id`` is keyword-only here because it is keyword-only in
            :func:`app.reporting.screenshots.capture_png`, so a hook that
            passed the identity positionally would fail against this stub
            exactly as it would against the real function.
            """
            self.steps.append(CAPTURE_STEP)
            self.captured.append(driver)
            self.scenario_ids.append(scenario_id)
            if self.capture_error is not None:
                raise self.capture_error
            return self.png

        def quit_driver() -> None:
            """Record the teardown."""
            self.steps.append(QUIT_STEP)
            if self.quit_error is not None:
                raise self.quit_error

        def set_userdata(mapping: Any) -> None:
            """Record the userdata handshake."""
            self.userdata.append(mapping)

        monkeypatch.setattr(environment, "get_driver", get_driver)
        monkeypatch.setattr(environment, "capture_png", capture_png)
        monkeypatch.setattr(environment, "quit_driver", quit_driver)
        monkeypatch.setattr(environment, "set_userdata", set_userdata)


def test_the_environment_module_binds_exactly_the_three_hooks() -> None:
    """Only ``before_all``, ``before_scenario`` and ``after_scenario`` exist.

    ``Hooks.java`` defines exactly one hook and behave only ever imports this
    file, so there is no ``after_all``, no feature-, step- or tag-scoped hook
    and no direct-execution entry point.  The canonical names are deliberate:
    ``Hooks.java:5`` imports ``@After`` from ``org.junit.After``, so the Java
    teardown never ran, and AAP Conflict 6 registers these as real hooks rather
    than reproducing that defect.
    """
    assert sorted(environment.__all__) == [
        "after_scenario",
        "before_all",
        "before_scenario",
    ]
    for name in environment.__all__:
        assert callable(getattr(environment, name))
    for name in HOOK_COLLABORATORS:
        assert hasattr(environment, name)
    assert not hasattr(environment, "after_all")
    assert not hasattr(environment, "before_feature")
    assert not hasattr(environment, "after_step")


def test_before_all_installs_the_runs_userdata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The hook hands ``context.config.userdata`` to ``app.config.set_userdata``.

    The handshake that makes ``run-tests --browser <name>`` effective: the run
    service passes each worker ``-D browser=<name>``, behave collects it into
    ``context.config.userdata``, and this call installs it in front of the
    properties file.  Omitting it would make the option silently inert, because
    userdata is the only override path in the port - there is no
    environment-variable layer to fall back on.
    """
    recorder = HookRecorder()
    recorder.install(monkeypatch)
    context = FakeContext(userdata={"browser": "firefox"})

    environment.before_all(context)

    assert recorder.userdata == [context.config.userdata]
    assert dict(recorder.userdata[0]) == {"browser": "firefox"}
    # No session is created here: behave skips the scenario hooks under
    # --dry-run but still runs this one, and a session started here would
    # survive as a browser nobody quits.
    assert recorder.sessions == []
    assert recorder.steps == []


def test_before_all_makes_the_browser_override_effective(
    request: pytest.FixtureRequest,
) -> None:
    """Installed userdata outranks the properties file, per AAP 0.4.1.

    Asserted through the real ``app/config.py`` rather than a recorder, because
    the precedence - userdata first, then ``configuration.properties`` - is the
    property the option depends on.  The finalizer is registered *before* the
    installation and clears the process-wide slot, which is the cleanup
    ``app/config.py`` documents.
    """
    request.addfinalizer(lambda: config.set_userdata(None))
    # A value no properties file could plausibly supply, so the assertion is
    # about the userdata layer rather than about this machine's configuration -
    # and one nothing validates, since an unrecognised browser has to fail at
    # first driver use (``Driver.java:29-42`` has no default branch).
    override = "canned-browser-override"
    context = FakeContext(userdata={"browser": override})

    environment.before_all(context)

    assert config.get_browser() == override

    config.set_userdata(None)
    assert config.get_browser() != override


def test_before_scenario_publishes_the_session_on_the_context(
    monkeypatch: pytest.MonkeyPatch, stub_driver: Any
) -> None:
    """``context.driver`` is whatever ``get_driver()`` returned.

    The explicit place a scenario's session begins - the half of the lifecycle
    contract that guarantees a fresh session per scenario once
    ``after_scenario`` has cleared the slot.  The scenario itself is not
    inspected: no tag, name or status changes what happens, because the Java
    lifecycle draws no such distinction.
    """
    recorder = HookRecorder()
    recorder.install(monkeypatch, session_factory=lambda: stub_driver)
    context = FakeContext()
    scenario = FakeScenario("a scenario", failed=False)

    environment.before_scenario(context, scenario)

    assert context.driver is stub_driver
    assert recorder.sessions == [stub_driver]
    # Nothing is called on the session here, and nothing is captured or torn
    # down: the hook publishes and returns.
    assert stub_driver.operations() == ()
    assert recorder.steps == []
    assert scenario.writes == []


def test_before_scenario_passes_a_none_session_through_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``None`` session is published as it is, with no validation and no raise.

    ``get_driver()`` legitimately returns ``None``: ``Driver.java:29-42``
    switches on the ``browser`` property with cases ``"chrome"`` and
    ``"firefox"`` only and no default branch, so an unrecognised value yields no
    session.  Nothing here validates the browser name, raises on ``None`` or
    substitutes a default - the failure has to surface at the point of use, in
    the step that needed the browser rather than in a hook that hid it.
    """
    recorder = HookRecorder()
    recorder.install(monkeypatch, session_factory=lambda: None)
    context = FakeContext(userdata={"browser": "netscape-navigator"})
    scenario = FakeScenario("a scenario", failed=False)

    environment.before_scenario(context, scenario)

    assert context.driver is None
    assert recorder.sessions == [None]
    assert scenario.writes == []


def test_after_scenario_captures_nothing_for_a_passed_scenario(
    monkeypatch: pytest.MonkeyPatch, fake_context: FakeContext
) -> None:
    """A passing scenario is photographed never, and torn down all the same.

    ``Hooks.java:13`` makes only the screenshot conditional; ``:17`` sits
    outside the ``if``.  AAP 0.6 adds that no enable flag exists: ``README.md``
    claims screenshots for passing tests, but no setting and no branch in
    ``Hooks`` provides one, so that claim is aspirational and the configuration
    surface stays at six keys.
    """
    recorder = HookRecorder()
    recorder.install(monkeypatch)
    scenario = FakeScenario("a passing scenario", failed=False)

    environment.after_scenario(fake_context, scenario)

    assert recorder.steps == [QUIT_STEP]
    assert recorder.captured == []
    assert fake_context.attachments == []
    assert fake_context.driver is None
    assert scenario.writes == []
    assert scenario.status.queries == 1


def test_after_scenario_captures_once_for_a_failed_scenario(
    monkeypatch: pytest.MonkeyPatch, fake_context: FakeContext, stub_driver: Any
) -> None:
    """A failed scenario is photographed exactly once, from the live session.

    The session comes from the context - the one ``before_scenario`` published -
    and deliberately not from a fresh ``get_driver()`` call, which is
    create-on-demand and would start a browser during teardown just to
    photograph a blank page.
    """
    recorder = HookRecorder()
    recorder.install(monkeypatch)
    scenario = FakeScenario("a failing scenario", failed=True)

    environment.after_scenario(fake_context, scenario)

    assert recorder.captured == [stub_driver]
    assert recorder.steps.count(CAPTURE_STEP) == 1
    assert recorder.sessions == []
    assert scenario.status.queries == 1

    # And the capture is told *which* scenario it is photographing.  The
    # screenshot module suppresses a capture failure by design, so its log
    # record is the only trace one leaves; the identity is the one fact only
    # this hook knows, and without it a shard that ran many scenarios reports
    # missing evidence it cannot attribute.
    assert recorder.scenario_ids == [environment._scenario_identity(scenario)]
    assert recorder.scenario_ids[0] is not None
    assert scenario.name in recorder.scenario_ids[0]


def test_after_scenario_names_the_scenario_whose_evidence_was_sought(
    monkeypatch: pytest.MonkeyPatch, fake_context: FakeContext
) -> None:
    """The identity handed over is the file, the line and the name.

    ``features/environment.py`` builds it in the ``file:line`` shape behave
    uses for a location, then the scenario's own name in quotes, and hands it
    to :func:`app.reporting.screenshots.capture_png` as ``scenario_id``.  Three
    attributes and no more - no tag, no step text and no table or example row,
    because those are where a scenario outline's ``<placeholder>`` values land
    and ``Login.feature``'s Examples tables hold literal credentials, so
    restricting the identity keeps one out of the log by construction.

    A scenario exposing none of the three - which a unit-test object
    legitimately is - yields ``None`` rather than a placeholder, leaving the
    suppression record reading exactly as it did before identities existed.
    """
    recorder = HookRecorder(png=None)
    recorder.install(monkeypatch)
    scenario = FakeScenario(
        "UPGN-287 Create a new opportunity",
        failed=True,
        filename="features/Crm.feature",
        line=9,
    )

    environment.after_scenario(fake_context, scenario)

    assert recorder.scenario_ids == [
        "features/Crm.feature:9 'UPGN-287 Create a new opportunity'"
    ]
    assert scenario.writes == []

    # Nothing beyond the three attributes: the identity is not built from
    # anything that could carry a substituted Examples value.
    identity = recorder.scenario_ids[0]
    assert identity is not None
    for absent in ("@", "Given ", "When ", "Then ", "|"):
        assert absent not in identity, identity

    # An object exposing none of the three is not a failure and not a
    # placeholder.
    anonymous = HookRecorder()
    anonymous.install(monkeypatch)
    environment.after_scenario(
        FakeContext(driver=object()), FakeScenario("", failed=True)
    )
    assert anonymous.scenario_ids == [None]


def test_after_scenario_attaches_the_png_bytes_with_their_mime_type(
    monkeypatch: pytest.MonkeyPatch, fake_context: FakeContext
) -> None:
    """The attachment is ``("image/png", <raw bytes>)`` and nothing else.

    ``Hooks.java:15`` supplies bytes, a MIME type and a name; behave's
    ``Context.attach`` takes only the first two, so the result collector
    supplies the third - the scenario's name - from the scenario it is already
    tracking.  Raw bytes rather than a base64 string: behave's own formatter
    encodes whatever it is handed, so a pre-encoded payload would be encoded
    twice.
    """
    recorder = HookRecorder()
    recorder.install(monkeypatch)
    scenario = FakeScenario("a failing scenario", failed=True)

    environment.after_scenario(fake_context, scenario)

    assert fake_context.attachments == [
        (environment.DEFAULT_MIME_TYPE, CAPTURED_PNG)
    ]
    assert environment.DEFAULT_MIME_TYPE == "image/png"
    assert isinstance(fake_context.attachments[0][1], bytes)


def test_after_scenario_captures_before_it_quits(
    monkeypatch: pytest.MonkeyPatch, fake_context: FakeContext
) -> None:
    """The order is capture, then quit - asserted from one shared log.

    AAP 0.6 fixes capture as happening "only on failure, once, before the
    driver is quit", and the order is a necessity rather than a preference: a
    screenshot needs a live session, and once the teardown returns there is
    nothing left to photograph.  Two separate call counters could both be
    satisfied by the wrong order, so both recorders append to one list.
    """
    recorder = HookRecorder()
    recorder.install(monkeypatch)
    scenario = FakeScenario("a failing scenario", failed=True)

    environment.after_scenario(fake_context, scenario)

    assert recorder.steps == [CAPTURE_STEP, QUIT_STEP]


def test_after_scenario_attaches_nothing_when_the_capture_yielded_nothing(
    monkeypatch: pytest.MonkeyPatch, fake_context: FakeContext
) -> None:
    """A capture that reports failure attaches nothing and changes no outcome.

    ``capture_png`` logs and returns ``None`` for a dead session, for an object
    that cannot be photographed at all - the ``None`` a mis-configured browser
    leaves behind - and for an unusable payload.  AAP deviation 19 places that
    suppression there rather than in this caller, so the hook simply has nothing
    to attach.
    """
    recorder = HookRecorder(png=None)
    recorder.install(monkeypatch)
    scenario = FakeScenario("a failing scenario", failed=True)

    environment.after_scenario(fake_context, scenario)

    assert recorder.steps == [CAPTURE_STEP, QUIT_STEP]
    assert fake_context.attachments == []
    assert fake_context.driver is None
    assert scenario.writes == []


def test_after_scenario_quits_even_when_the_capture_raises(
    monkeypatch: pytest.MonkeyPatch, fake_context: FakeContext
) -> None:
    """A raising capture cannot skip the teardown or leave a session published.

    ``Driver.closeDriver()`` sits outside the ``if`` at ``Hooks.java:17``, and
    here it sits outside the ``try`` as well: getting this wrong leaks one
    browser process per failing scenario and, across a process pool, exhausts
    the host.  The injected error still propagates - the ``finally`` guarantees
    the teardown, not the suppression of a defect in the capture path, which
    ``capture_png`` itself is documented never to raise.
    """
    recorder = HookRecorder(capture_error=RuntimeError("the session was gone"))
    recorder.install(monkeypatch)
    scenario = FakeScenario("a failing scenario", failed=True)

    with pytest.raises(RuntimeError):
        environment.after_scenario(fake_context, scenario)

    assert recorder.steps == [CAPTURE_STEP, QUIT_STEP]
    assert fake_context.driver is None
    assert fake_context.attachments == []
    assert scenario.writes == []


def test_after_scenario_quits_even_when_the_attachment_raises(
    monkeypatch: pytest.MonkeyPatch, stub_driver: Any
) -> None:
    """A raising ``attach`` cannot skip the teardown either.

    The same ``finally``, one step further along the evidence-gathering path:
    the capture succeeded and the formatter chain refused the payload.
    """
    recorder = HookRecorder()
    recorder.install(monkeypatch)
    context = AttachFailingContext(driver=stub_driver)
    scenario = FakeScenario("a failing scenario", failed=True)

    with pytest.raises(RuntimeError):
        environment.after_scenario(context, scenario)

    assert recorder.steps == [CAPTURE_STEP, QUIT_STEP]
    assert context.driver is None
    assert scenario.writes == []


def test_after_scenario_clears_the_context_even_when_the_quit_raises(
    monkeypatch: pytest.MonkeyPatch, fake_context: FakeContext
) -> None:
    """A raising teardown still empties the slot, and the error propagates.

    The nested ``finally``: the slot is emptied by ``quit_driver`` itself and
    this clears the context's reference to the session it just closed, so no
    later reader can reach a quit driver - and it happens even in the impossible
    case of the teardown raising, which is not swallowed because it would be a
    genuine defect in the driver holder.
    """
    recorder = HookRecorder(quit_error=RuntimeError("the browser would not close"))
    recorder.install(monkeypatch)
    scenario = FakeScenario("a passing scenario", failed=False)

    with pytest.raises(RuntimeError):
        environment.after_scenario(fake_context, scenario)

    assert recorder.steps == [QUIT_STEP]
    assert fake_context.driver is None
    assert scenario.writes == []


def test_the_lifecycle_takes_one_session_per_scenario(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second scenario asks for a new session, after the first was cleared.

    ``Driver.java:21-45`` keeps one session per thread and creates on first use;
    ``closeDriver()`` quits and removes (``:50-55``).  So exactly one live
    session exists per worker at any moment, every scenario gets a fresh one,
    and no code ever touches a driver after ``quit()``.  Driving
    before/after/before is the only way to observe that the slot really was
    empty when the second scenario started.
    """
    recorder = HookRecorder()
    recorder.install(monkeypatch)
    context = FakeContext()

    first_scenario = FakeScenario("first", failed=False)
    environment.before_scenario(context, first_scenario)
    first_session = context.driver
    environment.after_scenario(context, first_scenario)

    assert context.driver is None

    second_scenario = FakeScenario("second", failed=True)
    environment.before_scenario(context, second_scenario)
    second_session = context.driver

    assert second_session is not None
    assert second_session is not first_session
    assert recorder.sessions == [first_session, second_session]

    environment.after_scenario(context, second_scenario)

    assert context.driver is None
    assert recorder.steps == [QUIT_STEP, CAPTURE_STEP, QUIT_STEP]
    assert recorder.captured == [second_session]
    assert recorder.sessions == [first_session, second_session]


def test_the_lifecycle_never_writes_a_scenarios_status(
    monkeypatch: pytest.MonkeyPatch, fake_context: FakeContext
) -> None:
    """No hook assigns to the scenario, whatever happens during teardown.

    AAP 0.6: a screenshot is evidence about a result, never part of one.  The
    status is read - once - and never written, and no result is marked failed,
    passed or skipped.  Asserted across the passing path, the failing path and
    a failing path whose capture raised, because a status write would most
    plausibly appear in an error handler.
    """
    for failed, capture_error, expected_raise in (
        (False, None, False),
        (True, None, False),
        (True, RuntimeError("capture failed"), True),
    ):
        recorder = HookRecorder(capture_error=capture_error)
        recorder.install(monkeypatch)
        fake_context.driver = object()
        scenario = FakeScenario("a scenario", failed=failed)

        if expected_raise:
            with pytest.raises(RuntimeError):
                environment.after_scenario(fake_context, scenario)
        else:
            environment.after_scenario(fake_context, scenario)

        assert scenario.writes == []
        assert scenario.status.failed is failed
        assert scenario.status.queries == 1
        assert fake_context.driver is None
