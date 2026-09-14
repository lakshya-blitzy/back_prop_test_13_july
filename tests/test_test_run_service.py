"""Orchestration tests for ``app/services/test_run_service.py``.

The gate for the two invariants AAP 0.6 says are the only ones that *can* hold
for a sharded browser suite: *"every selected scenario is assigned to exactly
one worker"*, and *"for a fixed set of shard inputs the merged structure -
feature order, scenario order, background position, statuses - is identical
whatever the worker count"*, compared with timestamps, durations and error text
normalized because those vary by construction.  It is also the only owned
module that drives ``features/environment.py``'s scenario hooks.

``pom.xml:21-29``, the surefire configuration the service ports, fixes the
rest: ``parallel=methods`` (``pom.xml:22``) makes sharding scenario-level, and
``testFailureIgnore=true`` (``pom.xml:25``) with the six ``-1`` thresholds at
``Jenkins:15`` keep a test outcome out of the exit status - whose table
``app/cli.py`` owns, so this module asserts the
:class:`~app.services.test_run_service.RunOutcome` fields it is computed from
instead.  ``Jenkins:15``'s narrowed ``target/cucumber.json`` glob is why
``target/.workers/`` must never survive a run, and
``FailedTestRunner.java:9-12`` declares no tag filter, which the neutral tag
expression reproduces.

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
Three seams, all published by the production code, and nothing started:

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
    builders, to the ``-o`` path it finds in that argument list.  No engine is
    started, no timeout is waited on and no test sleeps.

    One group of tests is the deliberate exception, because its subject *is*
    the launch: the live output relay, which only exists on the real
    ``spawn``.  Those five tests run :data:`RELAY_CHILD_SCRIPT` - a
    three-line ``-c`` script of this interpreter that writes what the test
    tells it to and exits - through the module's own launch, so that the
    rendering, redaction, severity floor and read bound a CI log depends on
    are asserted where production applies them.  Still no engine, no browser,
    no network and no artifact tree; see that group's own header.

``monkeypatch`` on the names the module under test binds
    Used only where a delegation is the thing being asserted - the rerun
    grammar's owner, the hooks' collaborators - never to replace the subject.
    :class:`subprocess.Popen` is intercepted in exactly one test, by
    :class:`PopenRecorder`, and for a structural reason: the ``spawn`` seam is
    what stands in for the *default* launch, so that launch's own composition
    of the child's environment cannot be observed through it.  That recorder
    starts no process either.

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
  documents, which is the contract every caller downstream depends on.  What
  the live-relay group asserts is not the transport either but what the launch
  *emits*: a child's line reaches the parent log rendered, at the right
  severity and bounded, whatever the transport underneath.
* **Not an exit status.**  ``app/cli.py`` owns the exit table; this module
  asserts the :class:`~app.services.test_run_service.RunOutcome` fields the
  table is computed from, and that nothing here terminates the interpreter.
"""

from __future__ import annotations

import ast
import configparser
import ctypes
import itertools
import json
import logging
import os
import re
import signal
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import pytest

# The engine's own configuration parser, deliberately rather than a reading of
# the argument list: what a binding has to be judged on is the step directory
# and the environment file behave *resolves*, which is the pair an ambient
# stage would otherwise redirect.
from behave.configuration import Configuration
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

#: The step definition every canned step resolves to.  A step whose
#: ``matched`` is true carries a non-empty ``match.location`` by contract --
#: the flag and the location state one fact and the artifacts read them
#: separately -- so a canned shard names one rather than leaving it out.
CANNED_STEP_LOCATION: Final[str] = "features.steps.canned_steps.a_precondition"

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

#: Pins the engine's glue roots: an empty stage, written as one argv element
#: with its value attached, so the step directory stays ``features/steps`` and
#: the environment file stays ``features/environment.py``.
STAGE_FLAG: Final[str] = "--stage="

#: The ``behave.ini`` key the flag above has a tracked counterpart in.
STAGE_OPTION: Final[str] = "stage"

#: The engine's own stage variable, and the sharp end of the child
#: environment: behave reads it whenever nothing else sets a stage, prefixes
#: both glue roots with its value, and **executes** the environment file it
#: arrives at, so an absolute value names external Python that runs.
STAGE_ENV_VAR: Final[str] = "BEHAVE_STAGE"

#: A stage value that redirects both glue roots outside the checkout.  Chosen
#: absolute because behave joins the stage to the discovery root with
#: :func:`os.path.join`, where an absolute component wins outright.
EXTERNAL_STAGE: Final[str] = "/external/path"

#: The engine's default glue roots, which every binding below has to produce.
DEFAULT_STEPS_DIR: Final[str] = "steps"
DEFAULT_ENVIRONMENT_FILE: Final[str] = "environment.py"

#: Names a worker must **never** inherit, each with the value this module
#: plants in the parent's environment to prove it is dropped.  The value is
#: the hostile one in every case, so a name that leaked would leak something
#: that visibly matters:
#:
#: * the nine ``BEHAVE_*`` variables the engine reads, headed by the stage;
#: * the ``PYTHON*`` variables that decide which modules the child imports
#:   and whether its assertions run at all;
#: * the ``WDM_*`` driver-manager controls over transport verification and
#:   cache location, and the xdist worker marker;
#: * ``SE_*``, which Selenium's service reads **in preference to** the
#:   executable path it was handed; and
#: * the TLS-trust and proxy variables, which decide where a driver download
#:   comes from and who is trusted to have signed it.
DENIED_WORKER_ENV: Final[dict[str, str]] = {
    STAGE_ENV_VAR: EXTERNAL_STAGE,
    "BEHAVE_COLOR": "on",
    "BEHAVE_STORE_CAPTURED_ALWAYS": "true",
    "BEHAVE_SHOW_CAPTURED_ALWAYS": "true",
    "BEHAVE_HOOK_STORE_CAPTURED_ON_SUCCESS": "true",
    "BEHAVE_HOOK_SHOW_CAPTURED_ON_SUCCESS": "true",
    "BEHAVE_HOOK_STORE_CLEANUP_ON_SUCCESS": "true",
    "BEHAVE_STRIP_STEPS_WITH_TRAILING_COLON": "false",
    "BEHAVE_UNICODE_ERRORS": "ignore",
    "BEHAVE_BROWSER": "netscape-navigator",
    "PYTHONPATH": "/external/path",
    "PYTHONHOME": "/external/path",
    "PYTHONSTARTUP": "/external/path/startup.py",
    "PYTHONOPTIMIZE": "2",
    "PYTHONWARNINGS": "ignore",
    "WDM_SSL_VERIFY": "0",
    "WDM_LOCAL": "1",
    "WDM_LOG": "0",
    "PYTEST_XDIST_WORKER": "gw0",
    "SE_CHROMEDRIVER": "/external/path/chromedriver",
    "SE_GECKODRIVER": "/external/path/geckodriver",
    "SSL_CERT_FILE": "/external/path/ca.pem",
    "SSL_CERT_DIR": "/external/path/certs",
    "REQUESTS_CA_BUNDLE": "/external/path/ca.pem",
    "CURL_CA_BUNDLE": "/external/path/ca.pem",
    "HTTP_PROXY": "http://127.0.0.1:1/",
    "HTTPS_PROXY": "http://127.0.0.1:1/",
    "ALL_PROXY": "http://127.0.0.1:1/",
    "NO_PROXY": "",
    "http_proxy": "http://127.0.0.1:1/",
    "https_proxy": "http://127.0.0.1:1/",
}

#: The locale variables a worker keeps, named here because keeping them is
#: **behaviour** and not tidiness: ``features/Login.feature:89`` asserts the
#: browser's own required-field message in French, and that message follows
#: the process locale, so a worker that lost these would change whether the
#: outline carrying it passes.
LOCALE_WORKER_ENV: Final[tuple[str, ...]] = (
    "LANG",
    "LANGUAGE",
    "LC_ALL",
    "LC_CTYPE",
    "LC_MESSAGES",
)


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

    **The lease that goes with each entry is released here too.**  A prepared
    directory now also carries an open, locked lease descriptor, held in a
    second registry beside this one and guarded by the same lock, because
    liveness is answered by an operating-system lock rather than by the shape
    of a directory name.  Clearing only the first registry would leave those
    descriptors open for the remainder of the session - one per prepared
    directory, in a suite that prepares many - and would leave the lease of a
    deleted temporary directory registered under a path nothing can release
    afterwards.  Every lease taken *during* a test is therefore dropped when it
    ends, and a lease that was already held when the test began is left exactly
    as it was found.

    :yields: ``None`` - the fixture is entirely about the surrounding state.
    """
    with service._active_run_dirs_lock:
        snapshot = set(service._active_run_dirs)
        held_before = set(service._run_dir_leases)
    try:
        yield
    finally:
        with service._active_run_dirs_lock:
            taken = set(service._run_dir_leases) - held_before
        # Outside the lock: _drop_lease takes it itself.
        for directory in taken:
            service._drop_lease(directory)
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
                            # ``matched`` and ``match.location`` state one
                            # fact and the schema requires them to agree, so a
                            # matched step names the definition it resolved
                            # to, exactly as a real shard does.
                            match={"location": CANNED_STEP_LOCATION},
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
                            match={"location": CANNED_STEP_LOCATION},
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
        base: The run base, from which a shard index is recovered out of an
            output path through the public path accessor.
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
# Path safety of the selection (CWE-22, CWE-367)
#
# Selection decides what runs, and what runs is opened again by name - by
# behave, in another process, because AAP deviation 1 pins the features/<name>
# spelling into the JSON uri, the rerun manifest and the commands operators
# type.  Two defects followed from that shape and both were reproduced on a
# real filesystem before they were fixed: a listing that walked pathnames with
# iterdir()/is_file() followed a link standing where the features directory
# should be and selected scenarios from outside the suite, and a path that was
# checked and then handed to a parser that opened it again could be a different
# file by the time it was read.
#
# The subject now takes the contents *and* the object identity of every feature
# from app/reporting/rerun_report.py's verified reader, and re-checks that
# identity immediately before it builds a worker command.  Each case below is
# an observable consequence: a link is refused, a swap is detected, and every
# refusal is a tolerated problem that leaves the run at status 0 (AAP 0.4.1).
#
# Every link here is built with os.symlink/os.link in pytest's own temporary
# directory, so the probe is the real filesystem condition rather than a
# stubbed one, and the file a link points at lives outside the checkout root
# entirely - a selection that reached it is visible as a location the temporary
# tree cannot produce.
#
# Planting a link is a *capability*, not a given: AAP 0.8 puts Windows in the
# support matrix, and there an unelevated account without the developer-mode
# privilege cannot create a symbolic link at all (WinError 1314).  Every case
# below therefore goes through `link_or_skip`, which skips rather than failing
# when the platform refuses - a test that cannot plant the hostile condition
# proves nothing about the refusal, and failing in its own setup would report
# a production defect that is not there.
# =========================================================================== #


def link_or_skip(linker: Any, source: Path, destination: Path, **keywords: Any) -> None:
    """Plant a link with ``linker``, or skip the test if the platform cannot.

    :param linker: ``os.symlink`` or ``os.link``.
    :param source: What the link points at.
    :param destination: Where the link is created.
    :param keywords: Passed through - ``target_is_directory`` for a directory
        symlink on Windows.
    """
    try:
        linker(source, destination, **keywords)
    except (AttributeError, NotImplementedError, OSError) as error:
        pytest.skip(f"this platform cannot create the link: {error!r}")

#: The feature planted *outside* the suite.  Its single scenario sits on a line
#: the temporary tree also uses, which is deliberate: a location alone would
#: not distinguish it, so every assertion below keys on the feature path.
OUTSIDE_FEATURE: Final[FeatureFile] = FeatureFile(
    filename="Escaped.feature",
    title="Outside feature",
    text="""Feature: Outside feature

  Scenario: outside one
    Given a precondition
""",
)

#: The outside feature's own scenario line.
OUTSIDE_LOCATION_LINE: Final[int] = 3

#: What replaces a feature file mid-run in the swap cases: a different length
#: and a different scenario line, so the substitution is visible both in the
#: object identity (size and timestamps) and in what a parse would yield.
REPLACEMENT_TEXT: Final[str] = """Feature: Zulu alpha feature

  Scenario: substituted alpha
    Given a precondition
"""

#: The scenario line the replacement declares, which no assertion may ever see
#: selected: seeing it would mean the run executed the file that arrived after
#: the check instead of the one that passed it.
REPLACEMENT_LINE: Final[int] = 3


def write_outside_feature(outside_root: Path) -> Path:
    """Write :data:`OUTSIDE_FEATURE` outside any checkout root.

    Args:
        outside_root: A directory that is **not** the temporary checkout root -
            pytest's ``tmp_path``, whose child the checkout root is - so that
            nothing under it is reachable from the features directory except
            through a link.

    Returns:
        The feature file written.
    """
    directory = outside_root / "outside"
    directory.mkdir(exist_ok=True)
    path = directory / OUTSIDE_FEATURE.filename
    path.write_text(OUTSIDE_FEATURE.text, encoding="utf-8")
    return path


def replace_feature(base: Path | str | None, filename: str, text: str) -> None:
    """Unlink a feature file and write a different one in its place.

    The swap a probe performs while a run is between its check and its use: the
    replacement is a new object, so every field of the recorded identity that
    can change does - the inode may or may not be recycled, and the size and
    the change timestamp move regardless.

    Args:
        base: The checkout root the features directory hangs off, as the
            subject passed it on.
        filename: The feature file's own name.
        text: The Gherkin source to write in its place.
    """
    path = paths.features_dir(base) / filename
    path.unlink()
    path.write_text(text, encoding="utf-8")


def swap_on_read(
    monkeypatch: pytest.MonkeyPatch,
    feature_path: str,
    filename: str,
) -> list[str]:
    """Replace one feature file the instant the subject finishes reading it.

    The narrowest possible reproduction of the race the identity check exists
    to catch, and it patches no part of the subject's decision: the real
    verified reader still runs and still returns what it actually read, and the
    re-check in ``run_suite`` still runs unpatched.  All that is inserted is the
    hostile write, at the one moment a probe would have to win.

    Args:
        monkeypatch: pytest's patcher.
        feature_path: The repository-relative path of the feature to swap.
        filename: That feature's own file name.

    Returns:
        A list that records each swap performed, so a test can assert the race
        was actually run rather than assuming it.
    """
    swapped: list[str] = []
    verified_reader = service.read_verified_feature

    def reading_then_swapping(
        path: object, *, base: Path | str | None = None
    ) -> Any:
        """Read as the subject does, then replace the file that was read."""
        verified = verified_reader(path, base=base)
        if verified is not None and path == feature_path:
            replace_feature(base, filename, REPLACEMENT_TEXT)
            swapped.append(feature_path)
        return verified

    monkeypatch.setattr(service, "read_verified_feature", reading_then_swapping)
    return swapped


def test_a_symlinked_features_directory_selects_nothing_and_is_reported(
    tmp_artifact_root: Path, tmp_path: Path
) -> None:
    """A link standing where the features directory should be is refused.

    The reproduction of the first defect: the listing followed the link and
    four scenarios from outside the suite were selected and would have been
    executed.  The anchor is now opened ``O_NOFOLLOW`` relative to its own
    parent, so the redirected root yields **no** scenarios and one problem
    naming the directory - the tolerated shape AAP 0.4.1 requires, identical to
    the missing-directory row, and not an exception.
    """
    outside = write_outside_feature(tmp_path)
    link_or_skip(
        os.symlink,
        outside.parent,
        paths.features_dir(tmp_artifact_root),
        target_is_directory=True,
    )

    selected, problems = service.select_scenarios(base=tmp_artifact_root)

    assert selected == []
    assert len(problems) == 1
    assert str(paths.features_dir(tmp_artifact_root)) in problems[0]

    # And the whole run agrees: nothing is launched, the empty result set is
    # still produced so the publisher has a JSON to read, and the problem
    # travels to the caller that owns the exit status.
    spawn = RecordingSpawn(base=tmp_artifact_root)
    outcome = service.run_suite(base=tmp_artifact_root, spawn=spawn)

    assert spawn.calls == []
    assert outcome.selected_count == 0
    assert outcome.result_set is not None
    assert outcome.merge_produced_nothing is False
    assert len(outcome.parse_errors) == 1
    assert str(paths.features_dir(tmp_artifact_root)) in outcome.parse_errors[0]


def test_a_symlinked_feature_file_is_not_selected(
    feature_tree: Path, tmp_path: Path
) -> None:
    """A link *inside* the features directory is refused entry by entry.

    The directory can be the real one and a single entry still name a file
    somewhere else, so the refusal is per entry as well as per directory.  The
    rest of the tree is unaffected, which is the tolerated half: one planted
    entry must not cost the suite its own scenarios.
    """
    outside = write_outside_feature(tmp_path)
    link_or_skip(
        os.symlink,
        outside,
        paths.features_dir(feature_tree) / OUTSIDE_FEATURE.filename,
    )

    selected, problems = service.select_scenarios(base=feature_tree)

    assert OUTSIDE_FEATURE.feature_path not in {unit.feature_path for unit in selected}
    assert [unit.location for unit in selected] == list(all_locations())
    assert len(problems) == 1
    assert OUTSIDE_FEATURE.filename in problems[0]


def test_a_hard_linked_feature_file_is_not_selected(
    feature_tree: Path, tmp_path: Path
) -> None:
    """A second hard link to an outside file is refused too.

    A hard link is not a symbolic link and no ``is_symlink`` check sees it: the
    entry *is* a regular file directly inside the features directory, and it is
    also a file outside it, so whoever can write the outside name decides what
    the suite executes.  The link count is what distinguishes it.
    """
    outside = write_outside_feature(tmp_path)
    link_or_skip(
        os.link,
        outside,
        paths.features_dir(feature_tree) / OUTSIDE_FEATURE.filename,
    )

    selected, problems = service.select_scenarios(base=feature_tree)

    assert OUTSIDE_FEATURE.feature_path not in {unit.feature_path for unit in selected}
    assert [unit.location for unit in selected] == list(all_locations())
    assert len(problems) == 1
    assert OUTSIDE_FEATURE.filename in problems[0]


def test_a_feature_whose_contents_cannot_be_read_is_a_tolerated_problem(
    feature_tree: Path,
) -> None:
    """An unreadable feature costs its own scenarios and nothing else.

    A file that is a perfectly ordinary entry of the features directory and yet
    cannot be *read* - here because its bytes are not UTF-8 - is refused by the
    verified reader rather than by the listing, which is the other half of the
    same tolerance: the condition reaches the caller on ``parse_errors``, the
    remaining features are still selected, and nothing propagates (AAP 0.4.1).
    """
    undecodable = paths.features_dir(feature_tree) / "Undecodable.feature"
    undecodable.write_bytes(b"Feature: \xff\xfe not utf-8\n")

    selected, problems = service.select_scenarios(base=feature_tree)

    assert [unit.location for unit in selected] == list(all_locations())
    assert len(problems) == 1
    assert problems[0].startswith(
        f"{paths.NORMALIZED_FEATURES_PREFIX}{undecodable.name}"
    )


def test_selection_parses_the_contents_that_were_verified(
    feature_tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The units come from the verified text, not from a second open of the path.

    The property that closes the reopen half of the defect, asserted by making
    the two disagree: the verified reader is made to return contents that are
    **not** what the file on disk holds, and the selection follows the contents.
    A subject that reopened the pathname - as ``behave.parser.parse_file`` does,
    and as this module used to - would have returned the file's own two
    scenarios and failed here.
    """
    substituted = rerun_report.VerifiedFeature(
        path=BETA_FEATURE.feature_path,
        location=paths.features_dir(feature_tree) / BETA_FEATURE.filename,
        identity=(0, 0, 0, 0, 0),
        text="Feature: substituted\n\n\n\n  Scenario: only one\n    Given a step\n",
    )
    substituted_line = 5
    assert substituted_line not in BETA_LOCATION_LINES, (
        "the substituted line has to be one the file on disk does not declare"
    )
    verified_reader = service.read_verified_feature

    def substituting(path: object, *, base: Path | str | None = None) -> Any:
        """Return the substituted feature for Beta, the real one otherwise."""
        if path == BETA_FEATURE.feature_path:
            return substituted
        return verified_reader(path, base=base)

    monkeypatch.setattr(service, "read_verified_feature", substituting)

    selected, problems = service.select_scenarios(base=feature_tree)

    assert problems == []
    beta = [
        unit for unit in selected if unit.feature_path == BETA_FEATURE.feature_path
    ]
    assert [unit.location for unit in beta] == [
        f"{BETA_FEATURE.feature_path}{rerun_report.LINE_SEPARATOR}{substituted_line}"
    ]
    assert [unit.name for unit in beta] == ["only one"]
    # The other feature was read normally, so the substitution is the only
    # difference between this selection and the ordinary one.
    assert [
        unit.location
        for unit in selected
        if unit.feature_path == ALPHA_FEATURE.feature_path
    ] == [
        location
        for location in all_locations()
        if location.startswith(ALPHA_FEATURE.feature_path)
    ]


def test_a_feature_replaced_after_selection_is_dropped_and_the_run_still_exits_zero(
    feature_tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A feature swapped between the check and the hand-off executes nothing.

    The reproduction of the second defect, end to end: the file is replaced the
    instant selection finishes reading it, which is exactly the window a
    checked-then-reopened path leaves open.  The identity recorded at selection
    no longer matches, so ``Alpha.feature``'s scenarios are dropped and named
    while ``Beta.feature``'s still run - and the run stays on the status-``0``
    row of AAP 0.4.1: a merged document is produced, no shard is dead and
    nothing is raised.
    """
    swapped = swap_on_read(
        monkeypatch, ALPHA_FEATURE.feature_path, ALPHA_FEATURE.filename
    )
    surviving = [
        location
        for location in all_locations()
        if location.startswith(BETA_FEATURE.feature_path)
    ]

    spawn = RecordingSpawn(base=feature_tree)
    outcome = service.run_suite(workers=2, base=feature_tree, spawn=spawn)

    assert swapped == [ALPHA_FEATURE.feature_path], "the swap never happened"
    launched = sorted(
        itertools.chain.from_iterable(call.locations for call in spawn.calls)
    )
    assert launched == sorted(surviving)
    assert outcome.selected_count == len(surviving)
    assert outcome.dead_shards == ()
    assert outcome.result_set is not None
    assert outcome.merge_produced_nothing is False

    dropped = [
        message
        for message in outcome.parse_errors
        if message.startswith(ALPHA_FEATURE.feature_path)
    ]
    assert len(dropped) == 1, outcome.parse_errors

    # The replacement's own scenario is never executed under the approved
    # file's name, which is the disclosure the check exists to prevent.
    replacement_location = (
        f"{ALPHA_FEATURE.feature_path}"
        f"{rerun_report.LINE_SEPARATOR}{REPLACEMENT_LINE}"
    )
    assert replacement_location not in launched


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


def test_an_empty_stage_is_passed_on_every_invocation(feature_tree: Path) -> None:
    """Every worker command pins the glue roots, whatever else it carries.

    behave prefixes the step directory and the environment file with the stage
    name and takes that name from ``BEHAVE_STAGE`` when nothing else sets one,
    joining it to the discovery root with :func:`os.path.join` - so an
    absolute value redirects both roots outside the checkout, and the
    environment file at the end of that redirection is *executed*.  An
    explicit empty stage on the command line is what makes the variable
    unreachable, so it belongs to every invocation rather than to some of
    them, and it is one argv element with its value attached because an empty
    value as a separate element would swallow the argument after it.
    """
    plan = only_plan(feature_tree)

    for tags, browser, dry_run in itertools.product(
        (None, "@Alpha"), (None, "chrome"), (False, True)
    ):
        command = service.build_worker_command(
            plan, tags=tags, browser=browser, dry_run=dry_run
        )
        assert command.count(STAGE_FLAG) == 1
        assert command[-len(plan.locations) :] == list(plan.locations)


def test_the_empty_stage_leaves_the_engine_on_the_tracked_glue(
    feature_tree: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The engine, given that command, resolves the tracked roots regardless.

    The property is asserted through behave's own configuration parser rather
    than through the shape of the argument list, because what matters is the
    step directory and the environment file the engine ends up with - the
    pair it would otherwise take from an ambient ``BEHAVE_STAGE``.
    """
    monkeypatch.setenv(STAGE_ENV_VAR, EXTERNAL_STAGE)
    monkeypatch.chdir(feature_tree)

    assert service._STAGE_FLAG == STAGE_FLAG

    unbound = Configuration(["--no-color"])
    assert unbound.steps_dir != DEFAULT_STEPS_DIR
    assert unbound.environment_file != DEFAULT_ENVIRONMENT_FILE

    bound = Configuration(["--no-color", service._STAGE_FLAG])
    assert bound.stage == ""
    assert bound.steps_dir == DEFAULT_STEPS_DIR
    assert bound.environment_file == DEFAULT_ENVIRONMENT_FILE


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


def test_behave_ini_declares_an_empty_stage(repo_root: Path) -> None:
    """The configuration file carries the stage key, and carries it empty.

    Present and empty is the whole point: behave reads ``BEHAVE_STAGE`` only
    when the stage is ``None``, so a key that exists with an empty value is
    what puts the variable out of reach, while any *name* at all would send
    both glue roots to ``<name>_steps`` and ``<name>_environment.py``, neither
    of which exists in this repository.  The file is read with
    :mod:`configparser`, which is how the engine reads it.
    """
    parser = configparser.ConfigParser()
    read = parser.read(repo_root / BEHAVE_INI_NAME, encoding="utf-8")

    assert read, f"{BEHAVE_INI_NAME} could not be read"
    assert parser.has_option(BEHAVE_INI_SECTION, STAGE_OPTION)
    assert parser[BEHAVE_INI_SECTION][STAGE_OPTION] == ""


def test_behave_ini_pins_the_glue_roots_against_an_ambient_stage(
    repo_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """This repository's own file leaves the engine on the tracked glue.

    The security property itself, asserted end to end on the tracked file: the
    engine is configured in a directory holding nothing but a copy of it, with
    an absolute ``BEHAVE_STAGE`` in the environment, and it still resolves
    ``features/steps`` and ``features/environment.py``.  Without the key it
    would load step modules from ``/external/path_steps`` and **execute**
    ``/external/path_environment.py``, which the first pair of assertions
    below establishes by configuring the same engine in an empty directory.

    A copy in :fixture:`tmp_path` rather than the repository root, because the
    engine reads its configuration from the working directory and a test that
    changed into the checkout would also pick up whatever else is discovered
    there.
    """
    monkeypatch.setenv(STAGE_ENV_VAR, EXTERNAL_STAGE)

    unbound_directory = tmp_path / "unbound"
    unbound_directory.mkdir()
    monkeypatch.chdir(unbound_directory)
    unbound = Configuration(["--no-color"])
    assert unbound.steps_dir == f"{EXTERNAL_STAGE}_{DEFAULT_STEPS_DIR}"
    assert unbound.environment_file == (
        f"{EXTERNAL_STAGE}_{DEFAULT_ENVIRONMENT_FILE}"
    )

    bound_directory = tmp_path / "bound"
    bound_directory.mkdir()
    (bound_directory / BEHAVE_INI_NAME).write_text(
        (repo_root / BEHAVE_INI_NAME).read_text(encoding="utf-8"), encoding="utf-8"
    )
    monkeypatch.chdir(bound_directory)
    bound = Configuration(["--no-color"])
    assert bound.stage == ""
    assert bound.steps_dir == DEFAULT_STEPS_DIR
    assert bound.environment_file == DEFAULT_ENVIRONMENT_FILE


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
# The worker's environment
#
# A worker is an engine process that reads environment variables to decide
# which Python it imports and executes - the stage variables above all - and
# what it trusts when it fetches a browser driver.  Its environment is
# therefore composed from an allowlist rather than inherited, and these tests
# assert the composition itself: which names survive, which are dropped, and
# that the launch is handed precisely that mapping and no other.
#
# This is the one place in the module where :mod:`subprocess` is intercepted,
# and the reason is structural: the ``spawn`` seam is what stands in for the
# default launch, so the default launch's own environment composition is not
# observable through it.  The recorder below starts no process.
# =========================================================================== #


@dataclass
class FakeLaunchedProcess:
    """What the default launch reads off a process it started itself.

    Attributes:
        pid: Identifies the worker in the service's live-worker registry.
            Negative, so it cannot collide with a real process this test
            session might also be holding.
        returncode: What :meth:`wait` reports; ``0``, since these tests are
            about the launch and not about an outcome.
        stdout: ``None``, which is what puts the launch on its no-pipe path:
            no reader thread is started and no stream is closed, so the test
            asserts the environment without a thread in it.
        stderr: ``None``, for the same reason.
    """

    pid: int = -1
    returncode: int = 0
    stdout: str | None = None
    stderr: str | None = None

    def wait(self) -> int:
        """Report the status immediately.

        Returns:
            :attr:`returncode`.  Nothing is waited for: there is no process.
        """
        return self.returncode


@dataclass
class PopenRecorder:
    """A stand-in for :class:`subprocess.Popen` that records and starts nothing.

    Attributes:
        commands: The argument list of each launch, in order.
        environments: The ``env=`` mapping of each launch, copied at the call
            so a later mutation of the original could not disguise itself.
    """

    commands: list[tuple[str, ...]] = field(default_factory=list)
    environments: list[dict[str, str]] = field(default_factory=list)

    def __call__(self, args: Sequence[str], **keywords: Any) -> FakeLaunchedProcess:
        """Record one launch.

        Args:
            args: The argument list the service built.
            **keywords: Everything else the service passes, ``env`` included.

        Returns:
            A :class:`FakeLaunchedProcess` that exits ``0`` at once.
        """
        self.commands.append(tuple(args))
        self.environments.append(dict(keywords["env"]))
        return FakeLaunchedProcess()


def plant_denied_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Put every denied name into this process's environment.

    Every value planted is the hostile one - an absolute stage, disabled
    driver-transport verification, substituted driver executables, redirected
    trust stores - so that a name surviving into a worker would be a name that
    visibly matters.

    Args:
        monkeypatch: pytest's environment patcher, which restores every name
            it set when the test ends.

    Returns:
        ``None``.
    """
    for name, value in DENIED_WORKER_ENV.items():
        monkeypatch.setenv(name, value)


def test_the_worker_environment_drops_every_ambient_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not one denied name reaches a worker, asserted name by name.

    ``BEHAVE_STAGE`` is the one that executes code - behave prefixes both glue
    roots with the stage and runs the environment file it arrives at - but it
    is not asserted alone: each of the others changes what the child imports,
    what it captures, which driver executable it runs or whom it trusts to
    have signed one, so the contract is the whole set rather than the worst
    member of it.
    """
    plant_denied_environment(monkeypatch)

    environment = service._worker_environment()

    for name in DENIED_WORKER_ENV:
        assert name not in environment, f"{name} reached the worker"


def test_the_worker_environment_preserves_every_allowlisted_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An allowlisted name arrives with the value the parent had, untouched.

    Dropping is the default, so the list of things a worker genuinely needs is
    the part that has to be exercised: the program search path that locates
    the browser and its driver, the session and display names a headed
    browser cannot start without, and the locale group the asserted French
    required-field message follows.
    """
    for name in sorted(service._WORKER_ENV_ALLOWLIST):
        monkeypatch.setenv(name, f"value-for-{name}")

    environment = service._worker_environment()

    for name in sorted(service._WORKER_ENV_ALLOWLIST):
        assert environment[name] == f"value-for-{name}"
    assert "PATH" in service._WORKER_ENV_ALLOWLIST
    for name in LOCALE_WORKER_ENV:
        assert name in service._WORKER_ENV_ALLOWLIST
        assert environment[name] == f"value-for-{name}"


def test_the_worker_environment_always_stops_the_child_buffering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The unbuffered setting is the run's own, and is set last.

    The live relay is only live if its input is: a Python child whose stdout is
    a pipe buffers in blocks otherwise.  The value is written after the
    allowlisted names are copied, so an inherited setting of the same name
    cannot displace it - which the second half asserts by planting the
    opposite value in the parent.
    """
    monkeypatch.delenv(service._UNBUFFERED_ENV_VAR, raising=False)
    assert service._worker_environment()[service._UNBUFFERED_ENV_VAR] == (
        service._UNBUFFERED_ENV_VALUE
    )

    monkeypatch.setenv(service._UNBUFFERED_ENV_VAR, "0")
    assert service._worker_environment()[service._UNBUFFERED_ENV_VAR] == (
        service._UNBUFFERED_ENV_VALUE
    )


def test_the_worker_environment_carries_nothing_outside_the_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The key set is a subset of the allowlist plus the unbuffered setting.

    Asserted as a subset rather than as a list of names, so a name added to
    the composition in future is either in the allowlist - where it is
    documented and reviewable - or it fails here.  The mapping is also its own
    object: it never aliases :data:`os.environ`, and composing it leaves this
    process's environment as it was, which matters because a run is supervised
    from several threads that all read it.
    """
    plant_denied_environment(monkeypatch)
    monkeypatch.delenv(service._UNBUFFERED_ENV_VAR, raising=False)
    before = dict(os.environ)

    environment = service._worker_environment()

    permitted = set(service._WORKER_ENV_ALLOWLIST) | {service._UNBUFFERED_ENV_VAR}
    assert set(environment) <= permitted
    assert environment is not os.environ
    assert dict(os.environ) == before


def test_the_launch_is_handed_the_composed_environment_and_no_other(
    feature_tree: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default launch passes exactly the composed mapping as ``env=``.

    The composition would be worth nothing if the launch built its own
    environment beside it, so the two are asserted to be the same mapping, in
    a process whose own environment carries the hostile stage.  Nothing is
    started: :class:`subprocess.Popen` is replaced for the duration by a
    recorder, which is the only way to observe the default launch, since the
    published ``spawn`` seam is what replaces that launch everywhere else in
    this module.
    """
    plant_denied_environment(monkeypatch)
    recorder = PopenRecorder()
    monkeypatch.setattr(service.subprocess, "Popen", recorder)

    command = service.build_worker_command(only_plan(feature_tree))
    worker = service._spawn_worker(command, feature_tree)

    assert worker.returncode == 0
    assert recorder.commands == [tuple(command)]
    assert recorder.environments == [service._worker_environment()]

    passed = recorder.environments[0]
    assert STAGE_ENV_VAR not in passed
    assert passed[service._UNBUFFERED_ENV_VAR] == service._UNBUFFERED_ENV_VALUE


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

    Preparation is deliberately not idempotent: a second call is a second
    *run*, so it is given somewhere else to write.  Idempotence is required of
    the removal instead - :func:`~app.services.cleanup_workers_dir` is called
    from two ``finally`` blocks - and
    :func:`test_cleanup_workers_dir_is_a_silent_no_op_when_already_gone`
    asserts that.

    Nothing already under the shared directory is touched, which is the
    property that makes two runs safe side by side: the marker below stands
    for a concurrent run's intermediates, and it survives.
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
# Links, junctions and the path-based removal (the Windows fallback)
#
# Every removal in this service is either descriptor-relative - the POSIX path,
# which holds each component of the path open with ``O_NOFOLLOW`` - or resolved
# by pathname, which is the only thing Windows offers and which AAP 0.8 lists
# as a supported platform.  The pathname fallback is where a *junction* matters:
# ``os.lstat`` reports one with the directory bit set and the link bit clear and
# with a device and an inode of its own, so a symbolic-link test alone passes it
# and ``iterdir`` plus :func:`shutil.rmtree` then walk through it and recursively
# delete the children of whatever it points at, anywhere on the machine
# (CWE-22, and CWE-367 for a swap made mid-operation).
#
# **Two distinct defects live here, and the second is the harder one.**  A
# junction that is *already* at the path is refused by the inspection every
# removal begins with, and the first group of tests below is about that.  A
# junction *substituted while the removal is in progress* cannot be refused by
# any amount of re-inspecting the name, because the check and the recursive
# deletion are two separate resolutions of one pathname: whatever the check
# saw, ``rmtree`` resolves the name again and walks whatever stands there by
# then.  The service closes that window by taking the name out of the race -
# :func:`~app.services.delete_verified_entry` moves the entry to a private
# random ``.removing-<hex>`` name inside the already-verified parent, which no
# other process can address, and re-establishes the object's identity through
# that private name (still a directory, still not a reparse point, and the same
# device and inode a rename preserves) before anything recursive happens to it.
# The second group of tests drives exactly that substitution, at both
# boundaries the primitive is used at: one run directory, and the shared
# intermediate directory's own reclaim loop.
#
# The junction is simulated rather than created, because no filesystem reachable
# from this suite can hold one: the two Windows-only fields
# :class:`os.stat_result` carries for a reparse point are supplied by
# :class:`JunctionStat` through a patched :func:`os.lstat`, which is precisely
# the shape the production code reads.  A substitution is produced two
# independent ways, because one mechanism proving a refusal would leave the
# other's branch unasserted: :func:`swap_the_private_name` lets the real rename
# happen and then replaces the private name on disk, and
# :func:`pretend_the_private_name_is_a_junction` leaves the rename's result
# alone and makes the re-inspection report it as a reparse point.  The fallback
# itself is reached by patching the capability flag the service dispatches on,
# so the Windows branch is exercised on this host rather than left to a
# platform no test runs on.
#
# Every test below asserts the same two things: a refusal that names what was
# found, and a victim file that is still there afterwards, byte for byte.  A
# refusal alone would be satisfied by a function that deleted the tree and then
# complained.
# =========================================================================== #

#: ``IO_REPARSE_TAG_MOUNT_POINT``, the tag Windows reports for a directory
#: junction.  Carried by :class:`JunctionStat` so the stand-in is a junction
#: specifically rather than "some reparse point", and read by the production
#: code through :func:`getattr` - which is how one code path serves both
#: platforms.
JUNCTION_REPARSE_TAG: Final[int] = 0xA0000003

#: A run-shaped directory name this process never produced: process id ``1`` -
#: init, which exists on every host - and a token of twelve hex zeroes, so
#: :func:`~app.services.run_directory_owner` parses it.  It is the review's own
#: reproduction of the liveness finding, where a name that merely *looks* like a
#: run's kept another run's intermediates indefinitely, and it doubles here as
#: the thing a junction must not be allowed to reach.
FORGED_RUN_DIR_NAME: Final[str] = "1-000000000000"

#: What the service calls the build output root in a refusal.  A refusal has to
#: *name* the component it found the indirection at, because "something on the
#: path is not a directory" is not a diagnostic an operator can act on.
BUILD_OUTPUT_ROLE: Final[str] = "build output directory"

#: What it calls the shared per-worker directory in the same refusals.
INTERMEDIATE_ROLE: Final[str] = "intermediate directory"

#: The two components of the path to a run directory that the pathname fallback
#: verifies before it deletes anything through their names, each paired with the
#: accessor that resolves it and the role its own refusal carries.  The
#: accessors are the paths module's, so no test here spells a path out.
VERIFIED_CHAIN_COMPONENTS: Final[
    tuple[tuple[Callable[[Path], Path], str], ...]
] = (
    (paths.target_root, BUILD_OUTPUT_ROLE),
    (paths.workers_dir, INTERMEDIATE_ROLE),
)

#: The role a run directory itself is given in a refusal, as against the two
#: components of the path leading to it.
RUN_DIRECTORY_ROLE: Final[str] = "run directory"

#: The role ``app/cli.py``'s clean step gives an entry it is emptying the build
#: output of.  Named here because that step and this service's reclaim share
#: one destructive primitive and differ in exactly one argument - what a
#: symbolic link at the entry means - so the difference is asserted at the call
#: shape the other caller makes rather than described in a comment.
BUILD_OUTPUT_ENTRY_ROLE: Final[str] = "entry of the build output"

#: The two removal paths a symbolically linked shared directory has to be
#: refused by, each with the fragment its own refusal carries: the POSIX path
#: fails the ``O_NOFOLLOW`` walk, and the pathname fallback identifies the link
#: itself.  Parametrized over both because the link case is *pre-existing*
#: behaviour that the junction fix must not have cost either branch.
SYMLINKED_ROOT_REFUSALS: Final[tuple[tuple[bool, str], ...]] = (
    (True, "chain of real directories"),
    (False, "symbolic link"),
)

#: A second run-shaped name, so the reclaim loop's tests have more than one
#: entry to iterate over: a substitution defeated at the first entry proves
#: nothing about the second, and a loop that gave up after one refusal would
#: leave the rest of the shared directory unreclaimed and unreported.
SECOND_FORGED_RUN_DIR_NAME: Final[str] = "2-000000000000"

#: The contents of an external victim file, distinctive rather than ``{}`` so
#: that "still there, byte for byte" is a real comparison and not a match
#: against the empty document every other fixture in this module writes.
EXTERNAL_VICTIM_TEXT: Final[str] = '{"outside": "this checkout"}'

#: The refusal a *substitution* carries, as against the refusal an
#: already-present junction carries.  It is the sentence that says the entry
#: was moved out of the race before anything recursive happened to it, so it is
#: asserted on every substitution test: a reason naming a reparse point alone
#: would also be produced by code that walked the replacement first.
SUBSTITUTION_REFUSAL: Final[str] = "was replaced while it was being removed"

#: And the clause of that refusal that speaks for the victim tree.
NOTHING_DELETED_THROUGH_IT: Final[str] = "nothing was deleted through it"


class JunctionStat:
    """An :func:`os.lstat` result shaped like a Windows directory junction's.

    Everything the production code reads off a real junction and nothing else:
    the mode, device and inode are the *real* ones, so every identity and
    directory test passes exactly as it would for an ordinary directory, and
    the two Windows-only fields are the only thing that gives it away.  That is
    the whole point of the finding - a junction is indistinguishable from a
    directory until those fields are consulted.

    Attributes:
        st_mode: The real mode, with the directory bit set and the link bit
            clear, copied from the directory standing in for the junction.
        st_dev: The real device number, so :func:`os.path.samestat`-style
            comparisons cannot tell the difference either.
        st_ino: The real inode number, for the same reason.
        st_file_attributes: ``FILE_ATTRIBUTE_REPARSE_POINT``, the flag Windows
            sets on any reparse point.
        st_reparse_tag: :data:`JUNCTION_REPARSE_TAG`, which says *which* kind.
    """

    def __init__(self, real: os.stat_result) -> None:
        """Copy a real directory's identity and add the reparse fields.

        Args:
            real: The :func:`os.lstat` result of the directory standing in for
                the junction.
        """
        self.st_mode = real.st_mode
        self.st_dev = real.st_dev
        self.st_ino = real.st_ino
        self.st_file_attributes = stat.FILE_ATTRIBUTE_REPARSE_POINT
        self.st_reparse_tag = JUNCTION_REPARSE_TAG


def pretend_a_junction(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    """Make :func:`os.lstat` report one path as a Windows junction.

    Patched at :mod:`os` rather than on the service, because the service calls
    :func:`os.lstat` through the module - which is also the only way a
    :class:`~pathlib.Path` method under test would see it.  Every other path is
    answered by the real call, so the patch is as narrow as the finding: one
    directory entry lies about what it is, and the code either notices or
    deletes through it.

    Args:
        monkeypatch: pytest's patching fixture, which restores :mod:`os` after
            the test whatever the outcome.
        path: The one path to report as a junction.  It must exist, because the
            stand-in is built from its real ``lstat`` result.
    """
    real_lstat = os.lstat

    def lstat(target: Any, *arguments: Any, **keywords: Any) -> Any:
        """Answer for the faked path, and delegate everything else.

        Args:
            target: The path or descriptor being inspected.
            *arguments: Positional arguments passed through untouched.
            **keywords: Keyword arguments - ``dir_fd`` among them - passed
                through untouched.

        Returns:
            A :class:`JunctionStat` for the faked path, and the real result for
            every other path, descriptor or descriptor-relative name.
        """
        info = real_lstat(target, *arguments, **keywords)
        if isinstance(target, (str, os.PathLike)) and Path(target) == path:
            return JunctionStat(info)
        return info

    monkeypatch.setattr(os, "lstat", lstat)


def stranded_run_directory(base: Path, name: str = FORGED_RUN_DIR_NAME) -> Path:
    """Create a run-shaped directory holding one intermediate document.

    The thing every refusal below has to leave intact, and the thing the
    liveness tests have to be able to reclaim: a directory inside the shared
    intermediate directory, named as a run's, carrying a per-worker result
    document - the tracebacks, attachments and scenario data a stale directory
    keeps in the workspace.

    Args:
        base: The temporary checkout root.
        name: The directory's name, defaulting to :data:`FORGED_RUN_DIR_NAME`.

    Returns:
        The created directory.  Its parent is created through
        :func:`app.utils.paths.ensure_dir`, the same accessor production uses,
        so the tree is the one the service expects to find.
    """
    paths.ensure_dir(paths.workers_dir(base))
    directory = paths.workers_dir(base) / name
    directory.mkdir()
    (directory / f"worker{JSON_SUFFIX}").write_text("{}", encoding="utf-8")
    return directory


def external_victim(base: Path, name: str) -> Path:
    """Create a directory outside the build output, holding known bytes.

    The tree a substitution is pointed at, and the only thing that can prove a
    refusal was made *before* the recursive deletion rather than after it: on
    Windows the deletion resolved through a junction would take this
    directory's children with it, so its file surviving with identical
    contents is the assertion the whole junction group turns on.

    Args:
        base: The temporary checkout root.  The victim is created beside the
            build output rather than inside it, which is what makes it
            external: nothing this service owns has any business reaching it.
        name: The victim directory's own name, so one test can hold several.

    Returns:
        The created directory.  It contains one file,
        ``victim<JSON_SUFFIX>``, carrying :data:`EXTERNAL_VICTIM_TEXT`.
    """
    directory = base / name
    directory.mkdir()
    (directory / f"victim{JSON_SUFFIX}").write_text(
        EXTERNAL_VICTIM_TEXT, encoding="utf-8"
    )
    return directory


def assert_victim_survived(victim: Path) -> None:
    """Assert an external victim tree is exactly as it was created.

    Args:
        victim: A directory :func:`external_victim` created.  Both halves are
            asserted, because they fail differently: the directory itself
            surviving means the entry was not unlinked, and the file inside it
            surviving with identical bytes means nothing recursed into it.
    """
    assert victim.is_dir()
    assert (victim / f"victim{JSON_SUFFIX}").read_text(
        encoding="utf-8"
    ) == EXTERNAL_VICTIM_TEXT


def swap_the_private_name(
    monkeypatch: pytest.MonkeyPatch, substitute: Callable[[Path], None]
) -> list[Path]:
    """Let the service's rename happen, then substitute at the private name.

    The first of the two mechanisms this module produces a mid-removal
    substitution with, and the one that makes no assumption about how the
    service inspects anything: the rename really happens, so the entry really
    is at a private name it chose, and something else really is standing there
    by the time it looks again.  It is the closest a POSIX host can come to a
    junction appearing in that window - the attacker has to guess a name it
    cannot see, which is the point of the design, so the test is handed the
    name instead.

    Patched at :mod:`os` rather than on the service, because
    :func:`os.replace` is called through the module.  Only a destination whose
    name carries the service's own private prefix is interfered with, so an
    unrelated rename made anywhere under test is untouched.

    Args:
        monkeypatch: pytest's patching fixture, which restores :mod:`os`
            afterwards whatever the outcome.
        substitute: What to leave at the private name once the rename has
            completed.  Called with that name; a substitution that moves the
            real directory elsewhere first is how a test keeps the evidence it
            needs to assert on afterwards.

    Returns:
        The list the patch appends each private name to, in the order they
        were used - empty if the service never reached its rename at all,
        which is itself worth asserting.
    """
    real_replace = os.replace
    swapped: list[Path] = []

    def replace(
        source: Any, destination: Any, *arguments: Any, **keywords: Any
    ) -> None:
        """Perform the real rename, then substitute at a private name.

        Args:
            source: The entry being moved aside.
            destination: The private name it is being moved to.
            *arguments: Positional arguments passed through untouched.
            **keywords: Keyword arguments passed through untouched.
        """
        real_replace(source, destination, *arguments, **keywords)
        aside = Path(destination)
        if aside.name.startswith(service._ASIDE_PREFIX):
            swapped.append(aside)
            substitute(aside)

    monkeypatch.setattr(os, "replace", replace)
    return swapped


def pretend_the_private_name_is_a_junction(
    monkeypatch: pytest.MonkeyPatch,
) -> list[Path]:
    """Make the re-inspection of any private name report a junction.

    The second mechanism, and the one that reaches the branch a POSIX
    filesystem cannot otherwise present: the rename is left entirely alone, so
    the object at the private name *is* the directory that was checked, and the
    only thing that changes is what ``lstat`` says about it when the service
    looks again.  That is precisely the Windows case - a mount-point reparse
    point with the directory bit set - and the service either notices it or
    recurses into whatever it redirects to.

    The private name cannot be predicted by the caller, which is why
    :func:`pretend_a_junction` cannot serve here: the answer is keyed on the
    service's own prefix instead, and every other path is answered by the real
    call.

    Args:
        monkeypatch: pytest's patching fixture, which restores :mod:`os`
            afterwards whatever the outcome.

    Returns:
        The list the patch appends each private name it answered for to, so a
        test can assert the re-inspection happened rather than inferring it
        from a refusal.
    """
    real_lstat = os.lstat
    answered: list[Path] = []

    def lstat(target: Any, *arguments: Any, **keywords: Any) -> Any:
        """Answer for a private name, and delegate everything else.

        Args:
            target: The path or descriptor being inspected.
            *arguments: Positional arguments passed through untouched.
            **keywords: Keyword arguments - ``dir_fd`` among them - passed
                through untouched.

        Returns:
            A :class:`JunctionStat` for a ``.removing-<hex>`` path, and the
            real result for every other path or descriptor.
        """
        info = real_lstat(target, *arguments, **keywords)
        if isinstance(target, (str, os.PathLike)) and Path(
            target
        ).name.startswith(service._ASIDE_PREFIX):
            answered.append(Path(target))
            return JunctionStat(info)
        return info

    monkeypatch.setattr(os, "lstat", lstat)
    return answered


def entries_left_in(root: Path) -> list[Path]:
    """List a directory's entries without inspecting any of them.

    Used by the tests that leave :func:`os.lstat` patched while they assert:
    :func:`os.scandir` reports a name without ``lstat``-ing it, so a listing
    made this way is unaffected by a patch that answers for private names -
    and a listing is all these tests need, since what they assert about the
    survivor is its *contents*.

    Args:
        root: The directory to list.

    Returns:
        Its entries, sorted, as paths.
    """
    return sorted(root.iterdir())


def test_a_junction_shared_directory_is_refused_and_nothing_in_it_removed(
    tmp_artifact_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A junction where ``target/.workers`` belongs stops the clean dead.

    The reclaim step is what ``--clean`` and every exit path call for that
    directory, and it enumerates the directory's entries and removes each one.
    Resolved through a junction, that enumeration is of somebody else's
    directory and the removals are of somebody else's children, which is how a
    recursive delete leaves the checkout altogether (CWE-22).

    So the answer has to be a refusal *before* the enumeration: a reason naming
    the reparse point, an empty ``retained`` - nothing was inspected, so nothing
    can be claimed to have been deliberately kept - and every child still
    present, contents included.
    """
    stranded = stranded_run_directory(tmp_artifact_root)
    shared_root = paths.workers_dir(tmp_artifact_root)

    monkeypatch.setattr(service, "_SUPPORTS_CONFINED_REMOVAL", False)
    pretend_a_junction(monkeypatch, shared_root)

    reason, retained = service.reclaim_workers_root(base=tmp_artifact_root)

    assert reason is not None
    assert "reparse point" in reason
    assert INTERMEDIATE_ROLE in reason
    assert str(shared_root) in reason
    assert retained == ()

    # The junction's children - which on Windows would be another tree's - are
    # exactly as they were.
    assert stranded.is_dir()
    assert (stranded / f"worker{JSON_SUFFIX}").read_text(encoding="utf-8") == "{}"


def test_a_junction_run_directory_is_neither_walked_nor_removed(
    tmp_artifact_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The entry being removed is itself tested for every indirection.

    ``rmtree`` on a junction deletes what the junction points at rather than
    the junction, so the entry's own inspection is a link test *and* a reparse
    test.  Driven directly at the pathname fallback, which is the only branch
    that can reach a name this way.
    """
    stranded = stranded_run_directory(tmp_artifact_root)
    victim = stranded / f"worker{JSON_SUFFIX}"

    pretend_a_junction(monkeypatch, stranded)

    reason = service._remove_by_path(stranded, tmp_artifact_root)

    assert reason is not None
    assert "reparse point" in reason
    assert RUN_DIRECTORY_ROLE in reason
    assert str(stranded) in reason
    assert stranded.is_dir()
    assert victim.is_file()


def test_the_cleanup_entry_point_refuses_a_junction_run_directory(
    tmp_artifact_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal reaches the caller through the public function.

    :func:`~app.services.cleanup_workers_dir` is what ``app/cli.py`` and
    ``run_suite`` call, and it is the reason a refusal is not merely a silent
    skip: the reason it returns becomes the run's
    :attr:`~app.services.RunOutcome.infrastructure_error`, so a run whose
    intermediates could not be removed cannot report success.  Asserted through
    that function rather than through the private one, because the dispatch to
    the pathname fallback is part of what has to hold.
    """
    stranded = stranded_run_directory(tmp_artifact_root)

    monkeypatch.setattr(service, "_SUPPORTS_CONFINED_REMOVAL", False)
    pretend_a_junction(monkeypatch, stranded)

    reason = service.cleanup_workers_dir(
        base=tmp_artifact_root, directory=stranded
    )

    assert reason is not None
    assert "reparse point" in reason
    assert "could not be removed" in reason
    assert stranded.is_dir()
    assert (stranded / f"worker{JSON_SUFFIX}").is_file()


@pytest.mark.parametrize(("component", "role"), VERIFIED_CHAIN_COMPONENTS)
def test_a_junction_on_the_path_refuses_the_removal_below_it(
    tmp_artifact_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    component: Callable[[Path], Path],
    role: str,
) -> None:
    """A junction *above* the entry is as dangerous as one at it.

    The entry itself can be a perfectly ordinary directory and the deletion
    still escape, because every operation resolves the whole pathname: with
    ``target`` or ``target/.workers`` redirected, ``<base>/target/.workers/<run>``
    names a directory in another tree, and removing it recursively takes that
    tree's children with it while every check made on the entry passes.

    Which is why the fallback verifies the chain *first* and names the
    component it refused - the parametrization here being the two components it
    verifies.

    Args:
        tmp_artifact_root: The temporary checkout root.
        monkeypatch: pytest's patching fixture.
        component: The accessor for the component standing in as a junction.
        role: What the service calls that component in its refusal.
    """
    stranded = stranded_run_directory(tmp_artifact_root)
    victim = stranded / f"worker{JSON_SUFFIX}"

    pretend_a_junction(monkeypatch, component(tmp_artifact_root))

    reason = service._remove_by_path(stranded, tmp_artifact_root)

    assert reason is not None
    assert "reparse point" in reason
    assert role in reason
    assert str(component(tmp_artifact_root)) in reason
    # The refusal happened before the removal rather than after it.
    assert stranded.is_dir()
    assert victim.is_file()


def test_the_verified_chain_accepts_a_real_tree_and_tolerates_absence(
    tmp_artifact_root: Path,
) -> None:
    """Nothing to complain about, and nothing there, are both ``None``.

    Absence is deliberately not a problem: these functions exist to *remove*
    things, and a component that is not there means there is nothing below it
    to remove.  Treating it as a failure would make every second cleanup - the
    idempotent one ``app/cli.py`` performs on its way out - report a refusal.
    """
    # Neither component exists yet.
    assert service._verified_real_chain(tmp_artifact_root) is None

    # The build output exists, the shared directory does not.
    paths.ensure_dir(paths.target_root(tmp_artifact_root))
    assert service._verified_real_chain(tmp_artifact_root) is None

    # Both exist, both plain.
    paths.ensure_dir(paths.workers_dir(tmp_artifact_root))
    assert service._verified_real_chain(tmp_artifact_root) is None


@pytest.mark.parametrize(("component", "role"), VERIFIED_CHAIN_COMPONENTS)
def test_the_verified_chain_names_the_role_of_a_junction(
    tmp_artifact_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    component: Callable[[Path], Path],
    role: str,
) -> None:
    """Each component is checked, and the reason says which one failed.

    Args:
        tmp_artifact_root: The temporary checkout root.
        monkeypatch: pytest's patching fixture.
        component: The accessor for the component standing in as a junction.
        role: What the service calls it.
    """
    paths.ensure_dir(paths.workers_dir(tmp_artifact_root))

    pretend_a_junction(monkeypatch, component(tmp_artifact_root))

    reason = service._verified_real_chain(tmp_artifact_root)

    assert reason is not None
    assert "reparse point" in reason
    assert role in reason


@pytest.mark.parametrize(("component", "role"), VERIFIED_CHAIN_COMPONENTS)
def test_the_verified_chain_refuses_a_file_where_a_directory_belongs(
    tmp_artifact_root: Path, component: Callable[[Path], Path], role: str
) -> None:
    """A plain file standing in for a directory is refused by role too.

    The other way the chain can be untrustworthy, and the one a ``--no-clean``
    run or a clean that failed part-way can leave behind: whatever is at the
    name, it is not the directory this service owns, so nothing is resolved
    through it.

    Args:
        tmp_artifact_root: The temporary checkout root.
        component: The accessor for the component to replace with a file.
        role: What the service calls that component.
    """
    occupied = component(tmp_artifact_root)
    occupied.parent.mkdir(parents=True, exist_ok=True)
    occupied.write_text("not a directory", encoding="utf-8")

    reason = service._verified_real_chain(tmp_artifact_root)

    assert reason is not None
    assert "is not a directory" in reason
    assert role in reason
    assert str(occupied) in reason
    assert occupied.read_text(encoding="utf-8") == "not a directory"


def test_the_reparse_test_tells_a_directory_from_a_link_and_a_junction(
    tmp_artifact_root: Path,
) -> None:
    """The three answers the single indirection test has to give.

    Asserted as a unit, on the function every caller shares, because the
    finding was precisely that two of these three cases had one answer: a
    symbolic link was refused and a junction - reported by ``lstat`` as a
    directory with the link bit clear - was walked.
    """
    plain = tmp_artifact_root / "plain"
    plain.mkdir()
    link = tmp_artifact_root / "link"
    link.symlink_to(plain, target_is_directory=True)

    assert service._reparse_problem(
        plain, os.lstat(plain), RUN_DIRECTORY_ROLE
    ) is None

    link_problem = service._reparse_problem(
        link, os.lstat(link), RUN_DIRECTORY_ROLE
    )
    assert link_problem is not None
    assert "symbolic link" in link_problem
    assert RUN_DIRECTORY_ROLE in link_problem
    assert "neither followed nor removed" in link_problem

    junction_problem = service._reparse_problem(
        plain, JunctionStat(os.lstat(plain)), RUN_DIRECTORY_ROLE
    )
    assert junction_problem is not None
    assert "reparse point" in junction_problem
    assert RUN_DIRECTORY_ROLE in junction_problem
    assert "nothing was removed" in junction_problem


def test_the_directory_test_reads_the_same_three_answers_off_the_disk(
    tmp_artifact_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """:func:`_real_directory_problem` is that test plus the two disk cases.

    It inspects the path itself, so it answers for one case the indirection
    test cannot see - something that is not a directory at all - and for one
    that must never be an error, a path that is simply not there.
    """
    plain = tmp_artifact_root / "plain"
    plain.mkdir()
    link = tmp_artifact_root / "link"
    link.symlink_to(plain, target_is_directory=True)
    ordinary_file = tmp_artifact_root / f"file{JSON_SUFFIX}"
    ordinary_file.write_text("{}", encoding="utf-8")

    assert service._real_directory_problem(plain, INTERMEDIATE_ROLE) is None
    assert (
        service._real_directory_problem(
            tmp_artifact_root / "absent", INTERMEDIATE_ROLE
        )
        is None
    )

    link_problem = service._real_directory_problem(link, INTERMEDIATE_ROLE)
    assert link_problem is not None
    assert "symbolic link" in link_problem

    file_problem = service._real_directory_problem(
        ordinary_file, INTERMEDIATE_ROLE
    )
    assert file_problem is not None
    assert "is not a directory" in file_problem
    assert INTERMEDIATE_ROLE in file_problem

    pretend_a_junction(monkeypatch, plain)
    junction_problem = service._real_directory_problem(plain, INTERMEDIATE_ROLE)
    assert junction_problem is not None
    assert "reparse point" in junction_problem
    assert INTERMEDIATE_ROLE in junction_problem


@pytest.mark.parametrize(("confined", "fragment"), SYMLINKED_ROOT_REFUSALS)
def test_a_symlinked_shared_directory_is_refused_on_both_paths(
    tmp_artifact_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    confined: bool,
    fragment: str,
) -> None:
    """The link case both removal branches already handled still holds.

    The junction test was added *beside* the link test, not in place of it, so
    the pre-existing behaviour is asserted here at both branches: the
    descriptor-relative walk fails its ``O_NOFOLLOW`` open and the pathname
    fallback identifies the link itself.  Either way the linked-to tree is
    untouched, which is the property that matters to whoever owns it.

    Args:
        tmp_artifact_root: The temporary checkout root.
        monkeypatch: pytest's patching fixture.
        confined: Whether to exercise the descriptor-relative path or the
            pathname fallback.
        fragment: The text that branch's refusal carries.
    """
    elsewhere = tmp_artifact_root / "elsewhere"
    elsewhere.mkdir()
    victim = elsewhere / f"victim{JSON_SUFFIX}"
    victim.write_text("{}", encoding="utf-8")

    paths.ensure_dir(paths.target_root(tmp_artifact_root))
    shared_root = paths.workers_dir(tmp_artifact_root)
    shared_root.symlink_to(elsewhere, target_is_directory=True)

    monkeypatch.setattr(service, "_SUPPORTS_CONFINED_REMOVAL", confined)

    reason, retained = service.reclaim_workers_root(base=tmp_artifact_root)

    assert reason is not None
    assert fragment in reason
    assert str(shared_root) in reason
    assert retained == ()
    assert victim.read_text(encoding="utf-8") == "{}"
    assert shared_root.is_symlink()


def test_a_run_directory_outside_the_shared_one_is_refused_lexically(
    tmp_artifact_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cleanup deletes this run's intermediates and nothing else.

    The cheapest of the checks and the first one made: a directory whose parent
    is not the shared intermediate directory is not a run directory, whatever
    its name, and the removal never begins.  It is a *lexical* test, so it is
    not the junction defence - that is the chain verification above - but it is
    what keeps a caller's own mistake, or a path assembled from a manifest,
    from reaching the removal at all.
    """
    outsider = tmp_artifact_root / "outside-the-build-output"
    outsider.mkdir()
    victim = outsider / f"victim{JSON_SUFFIX}"
    victim.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(service, "_SUPPORTS_CONFINED_REMOVAL", False)

    reason = service._remove_run_directory(outsider, tmp_artifact_root)

    assert reason is not None
    assert "is not inside" in reason
    assert str(paths.workers_dir(tmp_artifact_root)) in reason
    assert outsider.is_dir()
    assert victim.read_text(encoding="utf-8") == "{}"


def test_a_run_directory_replaced_mid_removal_is_set_aside_and_refused(
    tmp_artifact_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The substitution race at the run-directory boundary, shape one.

    The finding the object-bound primitive exists for (the review's ``F25``,
    CWE-367 over CWE-22): the entry is a plain directory when it is inspected
    and an indirection by the time a pathname deletion would recurse into it.
    Re-checking the name cannot close that - the check and the deletion are two
    resolutions of one name - so the service moves the entry to a private
    random name first and re-establishes its identity *through that name*.

    Driven here by letting the real rename happen and then replacing the
    private name with a link to a tree outside the build output, which is what
    a junction installed in that window would be on Windows.  Three things are
    asserted, and the refusal alone is the weakest of them: the reason says the
    entry was replaced rather than merely that something is a link, the victim
    tree is untouched byte for byte, and the replacement is still sitting at
    the private name - nothing was deleted through it, in either direction.
    """
    stranded = stranded_run_directory(tmp_artifact_root)
    victim = external_victim(tmp_artifact_root, "elsewhere")
    holder = tmp_artifact_root / "moved-out-of-the-way"
    holder.mkdir()
    rescued = holder / "rescued"

    def substitute(aside: Path) -> None:
        """Move the real directory away and link the private name elsewhere.

        Args:
            aside: The private name the service renamed the entry to.
        """
        os.rename(aside, rescued)
        aside.symlink_to(victim, target_is_directory=True)

    monkeypatch.setattr(service, "_SUPPORTS_CONFINED_REMOVAL", False)
    swapped = swap_the_private_name(monkeypatch, substitute)

    reason = service._remove_run_directory(stranded, tmp_artifact_root)

    assert reason is not None
    assert len(swapped) == 1
    assert SUBSTITUTION_REFUSAL in reason
    assert NOTHING_DELETED_THROUGH_IT in reason
    assert "symbolic link" in reason
    assert RUN_DIRECTORY_ROLE in reason
    assert str(stranded) in reason
    assert str(swapped[0]) in reason

    # The replacement was left exactly where it was put, and what it points at
    # is whole: a deletion resolved through it would have emptied the victim.
    assert swapped[0].is_symlink()
    assert_victim_survived(victim)
    # And the real directory the service had already moved aside still holds
    # the intermediate document, so nothing was deleted at all.
    assert (rescued / f"worker{JSON_SUFFIX}").read_text(
        encoding="utf-8"
    ) == "{}"


def test_a_run_directory_reported_as_a_junction_when_re_checked_is_refused(
    tmp_artifact_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same race, shape two: the rename stands and ``lstat`` changes.

    The branch a POSIX filesystem cannot otherwise present, and the one that
    matters on the platform the fallback exists for.  Nothing is moved and
    nothing is replaced on disk: the object at the private name is the very
    directory that was inspected, and the only thing that differs is that the
    re-inspection reports it with the two Windows-only fields a mount-point
    reparse point carries.  A service that trusted its own rename would
    ``rmtree`` straight through it.

    Asserted through :func:`~app.services.cleanup_workers_dir`, the entry point
    ``app/cli.py`` and ``run_suite`` call, so the refusal is shown reaching a
    caller as the reason that denies the run its success rather than only
    existing inside the private function.
    """
    stranded = stranded_run_directory(tmp_artifact_root)
    victim = external_victim(tmp_artifact_root, "elsewhere")
    shared_root = paths.workers_dir(tmp_artifact_root)

    monkeypatch.setattr(service, "_SUPPORTS_CONFINED_REMOVAL", False)
    answered = pretend_the_private_name_is_a_junction(monkeypatch)

    reason = service.cleanup_workers_dir(
        base=tmp_artifact_root, directory=stranded
    )

    assert reason is not None
    assert len(answered) == 1
    assert "could not be removed" in reason
    assert SUBSTITUTION_REFUSAL in reason
    assert NOTHING_DELETED_THROUGH_IT in reason
    assert "reparse point" in reason
    assert RUN_DIRECTORY_ROLE in reason

    # The entry is still there under the private name, contents included:
    # listed rather than inspected, because ``lstat`` is still answering
    # "junction" for that name and the assertion is about its contents.
    survivors = entries_left_in(shared_root)
    assert len(survivors) == 1
    assert survivors[0].name.startswith(service._ASIDE_PREFIX)
    assert (survivors[0] / f"worker{JSON_SUFFIX}").read_text(
        encoding="utf-8"
    ) == "{}"
    assert_victim_survived(victim)


def test_a_swap_during_the_reclaim_cannot_redirect_the_loop(
    tmp_artifact_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The substitution race at the shared directory's own boundary.

    The reclaim is a *loop*, which is the second half of the same finding: a
    single verified parent is walked and every entry in it is removed by name,
    so a substitution made while the loop is running has as many chances as
    there are entries.  Each removal therefore goes through the same
    object-bound primitive, and each refusal is collected rather than aborting
    the walk - a loop that stopped at the first one would leave the rest of the
    shared directory unreclaimed and its survivors unreported.

    Two entries, two external victims, one swap each: every victim is whole,
    every refusal is in the reason, and nothing is claimed to have been
    deliberately retained, because nothing here is in use.
    """
    stranded = stranded_run_directory(tmp_artifact_root)
    second = stranded_run_directory(
        tmp_artifact_root, SECOND_FORGED_RUN_DIR_NAME
    )
    victims = [
        external_victim(tmp_artifact_root, "elsewhere-one"),
        external_victim(tmp_artifact_root, "elsewhere-two"),
    ]
    holder = tmp_artifact_root / "moved-out-of-the-way"
    holder.mkdir()
    rescued: list[Path] = []

    def substitute(aside: Path) -> None:
        """Move each real directory away and link its private name out.

        Args:
            aside: The private name the service renamed this entry to.
        """
        kept = holder / f"rescued-{len(rescued)}"
        os.rename(aside, kept)
        aside.symlink_to(victims[len(rescued)], target_is_directory=True)
        rescued.append(kept)

    monkeypatch.setattr(service, "_SUPPORTS_CONFINED_REMOVAL", False)
    swapped = swap_the_private_name(monkeypatch, substitute)

    reason, retained = service.reclaim_workers_root(base=tmp_artifact_root)

    assert reason is not None
    assert retained == ()
    assert len(swapped) == 2
    # One refusal per entry, so the walk continued past the first.
    assert reason.count(SUBSTITUTION_REFUSAL) == 2
    assert reason.count(NOTHING_DELETED_THROUGH_IT) == 2
    assert str(stranded) in reason
    assert str(second) in reason
    assert "could not be removed" in reason

    for victim in victims:
        assert_victim_survived(victim)
    for aside in swapped:
        assert aside.is_symlink()
    for kept in rescued:
        assert (kept / f"worker{JSON_SUFFIX}").read_text(
            encoding="utf-8"
        ) == "{}"


def test_a_junction_reported_during_the_reclaim_refuses_entry_by_entry(
    tmp_artifact_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The loop's other shape: every private name re-checks as a junction.

    The reclaim's counterpart of
    :func:`test_a_run_directory_reported_as_a_junction_when_re_checked_is_refused`,
    and the shape that proves the re-inspection is made per entry rather than
    once for the walk: both entries are refused, both are still there under
    their private names with their intermediate documents intact, and the
    external tree that a deletion resolved through either would have reached
    is untouched.
    """
    stranded_run_directory(tmp_artifact_root)
    stranded_run_directory(tmp_artifact_root, SECOND_FORGED_RUN_DIR_NAME)
    victim = external_victim(tmp_artifact_root, "elsewhere")
    shared_root = paths.workers_dir(tmp_artifact_root)

    monkeypatch.setattr(service, "_SUPPORTS_CONFINED_REMOVAL", False)
    answered = pretend_the_private_name_is_a_junction(monkeypatch)

    reason, retained = service.reclaim_workers_root(base=tmp_artifact_root)

    assert reason is not None
    assert retained == ()
    assert len(answered) == 2
    assert reason.count(SUBSTITUTION_REFUSAL) == 2
    assert reason.count("reparse point") == 2

    survivors = entries_left_in(shared_root)
    assert len(survivors) == 2
    for survivor in survivors:
        assert survivor.name.startswith(service._ASIDE_PREFIX)
        assert (survivor / f"worker{JSON_SUFFIX}").read_text(
            encoding="utf-8"
        ) == "{}"
    assert_victim_survived(victim)


def test_a_private_name_holding_a_different_object_is_refused(
    tmp_artifact_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Identity, not shape: the object must be the one that was inspected.

    The first of the three re-establishment branches, asserted on its own
    because each is a different refusal and a test that only ever produced one
    of them would leave the other two unexercised.  Here the private name holds
    a perfectly ordinary directory that is not a link, not a reparse point and
    not the entry that was checked - which is what a swap performed with a
    rename rather than with a junction looks like - so the only thing that
    catches it is the device and inode a rename is guaranteed to preserve.

    The stand-in for the substituted object is the external victim tree itself,
    so the assertion is the direct one: a service that deleted through the
    private name would have deleted that tree's contents.
    """
    stranded = stranded_run_directory(tmp_artifact_root)
    shared_root = paths.workers_dir(tmp_artifact_root)
    victim = external_victim(tmp_artifact_root, "elsewhere")
    holder = tmp_artifact_root / "moved-out-of-the-way"
    holder.mkdir()
    rescued = holder / "rescued"

    def substitute(aside: Path) -> None:
        """Put a different real directory at the private name.

        Args:
            aside: The private name the service renamed the entry to.
        """
        os.rename(aside, rescued)
        os.rename(victim, aside)

    swapped = swap_the_private_name(monkeypatch, substitute)

    reason = service.delete_verified_entry(
        shared_root, stranded.name, role=RUN_DIRECTORY_ROLE
    )

    assert reason is not None
    assert len(swapped) == 1
    assert SUBSTITUTION_REFUSAL in reason
    assert NOTHING_DELETED_THROUGH_IT in reason
    assert "is a different object from the one that was checked" in reason
    assert "device and inode" in reason

    # The substituted tree is intact where the substitution left it.
    assert (swapped[0] / f"victim{JSON_SUFFIX}").read_text(
        encoding="utf-8"
    ) == EXTERNAL_VICTIM_TEXT
    assert (rescued / f"worker{JSON_SUFFIX}").read_text(
        encoding="utf-8"
    ) == "{}"


def test_a_private_name_that_is_no_longer_a_directory_is_refused(
    tmp_artifact_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second branch: whatever is there now, it is not a directory.

    A file at the private name passes every indirection test - it is neither a
    link nor a reparse point - so it is caught by the one remaining question
    the re-establishment asks, and it is left exactly where it was found: this
    primitive deletes an object it has identified and nothing else.
    """
    stranded = stranded_run_directory(tmp_artifact_root)
    shared_root = paths.workers_dir(tmp_artifact_root)
    holder = tmp_artifact_root / "moved-out-of-the-way"
    holder.mkdir()
    rescued = holder / "rescued"

    def substitute(aside: Path) -> None:
        """Put a plain file at the private name.

        Args:
            aside: The private name the service renamed the entry to.
        """
        os.rename(aside, rescued)
        aside.write_text("not a directory", encoding="utf-8")

    swapped = swap_the_private_name(monkeypatch, substitute)

    reason = service.delete_verified_entry(
        shared_root, stranded.name, role=RUN_DIRECTORY_ROLE
    )

    assert reason is not None
    assert len(swapped) == 1
    assert SUBSTITUTION_REFUSAL in reason
    assert NOTHING_DELETED_THROUGH_IT in reason
    assert "is no longer a directory" in reason

    assert swapped[0].read_text(encoding="utf-8") == "not a directory"
    assert (rescued / f"worker{JSON_SUFFIX}").read_text(
        encoding="utf-8"
    ) == "{}"


def test_a_private_name_that_cannot_be_re_checked_is_refused(
    tmp_artifact_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The third branch, and a refusal in its own words.

    An unanswerable re-inspection is not a substitution the service can
    describe, so it does not claim one: the reason says the entry was set aside
    and could not be re-checked, and that nothing was removed.  Reached here by
    taking the private name away between the rename and the re-inspection,
    which is the one thing a process that *could* guess the name would be able
    to do to it.

    What the branch has to establish is the same as the other two: no recursive
    deletion is made through a name whose object has not been identified.
    """
    stranded = stranded_run_directory(tmp_artifact_root)
    shared_root = paths.workers_dir(tmp_artifact_root)
    holder = tmp_artifact_root / "moved-out-of-the-way"
    holder.mkdir()
    rescued = holder / "rescued"

    def substitute(aside: Path) -> None:
        """Take the private name away entirely.

        Args:
            aside: The private name the service renamed the entry to.
        """
        os.rename(aside, rescued)

    swapped = swap_the_private_name(monkeypatch, substitute)

    reason = service.delete_verified_entry(
        shared_root, stranded.name, role=RUN_DIRECTORY_ROLE
    )

    assert reason is not None
    assert len(swapped) == 1
    assert "was set aside as" in reason
    assert "could not be re-checked" in reason
    assert "so it was not removed" in reason
    assert str(swapped[0]) in reason

    assert not swapped[0].exists()
    assert (rescued / f"worker{JSON_SUFFIX}").read_text(
        encoding="utf-8"
    ) == "{}"


def test_a_linked_run_directory_is_refused_and_its_target_left_alone(
    tmp_artifact_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A link where a run directory belongs is not a run directory.

    The default of the one policy the primitive's two callers do not share.
    For the reclaim, a link standing where a run's working space belongs is not
    something to interpret: the run service will not guess what an operator
    meant by it, so it is refused, the link is left alone and what it points at
    is never reached.  ``--clean``'s opposite choice is asserted next.
    """
    victim = external_victim(tmp_artifact_root, "elsewhere")
    shared_root = paths.ensure_dir(paths.workers_dir(tmp_artifact_root))
    link = shared_root / FORGED_RUN_DIR_NAME
    link.symlink_to(victim, target_is_directory=True)

    monkeypatch.setattr(service, "_SUPPORTS_CONFINED_REMOVAL", False)

    reason = service._remove_run_directory(link, tmp_artifact_root)

    assert reason is not None
    assert "symbolic link" in reason
    assert RUN_DIRECTORY_ROLE in reason
    assert "neither followed nor removed" in reason
    assert str(link) in reason

    assert link.is_symlink()
    assert_victim_survived(victim)


def test_the_clean_steps_policy_unlinks_a_link_and_leaves_its_target(
    tmp_artifact_root: Path,
) -> None:
    """``unlink_links=True`` removes the link itself and nothing else.

    The other side of that policy, and the reason it is a parameter rather than
    a rule: to ``app/cli.py``'s clean step a link is simply an entry of a
    directory being emptied, and unlinking it is the only way to empty the
    directory - while it cannot touch what the link points at, because one
    :func:`os.unlink` of the link removes the named entry and can never
    recurse.  The role the clean step passes is used here too, so the asymmetry
    is asserted at the same call shape the caller makes.

    A **reparse point** is refused whichever way this flag is set, which the
    junction tests above already establish; this is about links alone.
    """
    victim = external_victim(tmp_artifact_root, "elsewhere")
    build_output = paths.ensure_dir(paths.target_root(tmp_artifact_root))
    link = build_output / "an-entry-of-the-build-output"
    link.symlink_to(victim, target_is_directory=True)

    reason = service.delete_verified_entry(
        build_output,
        link.name,
        role=BUILD_OUTPUT_ENTRY_ROLE,
        unlink_links=True,
    )

    assert reason is None
    assert not link.exists()
    assert not link.is_symlink()
    assert_victim_survived(victim)


def test_a_non_directory_entry_is_removed_by_a_single_unlink(
    tmp_artifact_root: Path,
) -> None:
    """Anything that is not a directory has nothing to recurse into.

    The primitive's second clause, and the case the shared intermediate
    directory really does hold: a stray file left in it by an interrupted run
    is removed with one :func:`os.unlink`, which addresses the named entry
    itself, so there is no window for a substitution to redirect and no
    recursion for one to redirect *into*.

    Absence is asserted as a success in the same test, because that is what
    makes the cleanup paths idempotent - ``run_suite`` removes its own
    directory and ``app/cli.py`` asks again on the way out.
    """
    shared_root = paths.ensure_dir(paths.workers_dir(tmp_artifact_root))
    stray = shared_root / f"stray{JSON_SUFFIX}"
    stray.write_text("{}", encoding="utf-8")

    assert (
        service.delete_verified_entry(
            shared_root, stray.name, role=RUN_DIRECTORY_ROLE
        )
        is None
    )
    assert not stray.exists()

    # Gone already, and still not a problem.
    assert (
        service.delete_verified_entry(
            shared_root, stray.name, role=RUN_DIRECTORY_ROLE
        )
        is None
    )


# =========================================================================== #
# Liveness is a lease, not a process-id-shaped name
#
# A run directory is only ever reclaimed by another invocation's clean when the
# run that owns it is over, so "is that run still going?" decides whether one
# invocation may delete another's tracebacks, screenshot attachments and
# scenario data.  Answering it from the process id in the directory's *name*
# gets it wrong in the one direction that costs something: process ids are
# recycled and a name can be written by hand, so a directory named after an id
# some unrelated process happens to hold reads as live for as long as that
# process lives.  ``1-000000000000`` is the review's own case - process 1 exists
# on every host, so that directory was immortal and whatever it held stayed in
# the workspace forever (CWE-367, CWE-400).
#
# What answers the question now is a lease: a file inside the run directory,
# opened and locked for as long as the run lives.  An operating-system lock
# cannot outlive the process holding it, so a crashed, killed or long-gone run
# releases automatically, and a directory nobody ever leased has nothing to
# offer at all.  The name is still read, but only as *identity* - to recognise a
# run directory in the first place.
#
# Three answers are possible and all three are asserted: held (live), free or
# absent (reclaimable), and unanswerable - a platform without locking, or a
# probe that failed - which falls back to the weaker process-id test rather than
# being read as either answer.
#
# Every test here that takes a lease gives it back, because the lease is an open
# descriptor held in a module-level registry that the autouse registry fixture
# does not reach.
# =========================================================================== #

#: Names :func:`~app.services.run_directory_owner` must refuse, each for its own
#: reason: no separator, no process id, a non-numeric one, a token of the wrong
#: length, a token that is not hexadecimal, and the service's own two working
#: file names - neither of which may ever be mistaken for a run's directory.
UNPARSEABLE_RUN_DIR_NAMES: Final[tuple[str, ...]] = (
    "",
    "1234",
    "-000000000000",
    "pid-000000000000",
    "1234-0000",
    "1234-0000000000000000",
    "1234-zzzzzzzzzzzz",
    service.RUN_LOCK_NAME,
    service._RUN_LEASE_NAME,
)


def test_a_process_id_shaped_name_without_a_lease_is_reclaimable(
    tmp_artifact_root: Path,
) -> None:
    """The review's own reproduction, pinned: ``1-000000000000`` is not alive.

    Everything the discredited reading needed is true here - the name parses,
    and the process it names really is running - and the directory is still
    reclaimed, because it holds no lease.  Both halves are asserted explicitly:
    without them the test would pass on a service that had simply stopped
    recognising the name.

    What it carries is what made this worth fixing: an intermediate per-worker
    document, in a workspace whose publisher glob (``Jenkins:15``) is narrowed
    to one file precisely because nothing intermediate may be readable.
    """
    stranded = stranded_run_directory(tmp_artifact_root)
    shared_root = paths.workers_dir(tmp_artifact_root)
    document = stranded / f"worker{JSON_SUFFIX}"

    # The two facts the old reading rested on.
    assert service.run_directory_owner(stranded.name) == 1
    assert service._process_is_alive(1) is True
    # And the one that settles it: nothing ever leased this directory.
    assert not service._lease_path(stranded).exists()

    assert service.run_directory_is_active(stranded) is False

    reason, retained = service.reclaim_workers_root(base=tmp_artifact_root)

    assert reason is None
    assert retained == ()
    assert not document.exists()
    assert not stranded.exists()
    # Nothing was left in it, so the shared directory went too.
    assert not shared_root.exists()


def test_prepare_workers_dir_leases_the_directory_it_creates(
    tmp_artifact_root: Path,
) -> None:
    """A run's own directory is leased from the moment it exists.

    The lease is taken inside :func:`~app.services.prepare_workers_dir` rather
    than by the caller, so there is no window in which a live run's directory
    is indistinguishable from an abandoned one - which is the window a
    concurrent ``--clean`` would delete the run's results in.
    """
    directory = service.prepare_workers_dir(base=tmp_artifact_root)
    try:
        assert service._lease_path(directory).is_file()
        assert service._lease_is_held(directory) is True
        assert service.run_directory_is_active(directory) is True
    finally:
        service.cleanup_workers_dir(
            base=tmp_artifact_root, directory=directory
        )

    assert not directory.exists()


def test_liveness_outlives_the_registry_and_ends_with_the_lease(
    tmp_artifact_root: Path,
) -> None:
    """The lease answers for a run whose own process cannot be asked.

    Two readings are available to an invocation: the in-process registry of
    directories *this* process created, and the lease.  Only the second can
    answer for another process's run, so the registry is discarded here and the
    liveness is asserted again - which is the reading a concurrent invocation
    actually performs.

    Then the lease is dropped, which is what the end of a run does, and the
    same directory becomes reclaimable.  Held, it is *retained* by the clean;
    released, it is removed.
    """
    directory = service.prepare_workers_dir(base=tmp_artifact_root)
    try:
        with service._active_run_dirs_lock:
            service._active_run_dirs.discard(directory)

        # Only the lease can answer now, and it does.
        assert service.run_directory_is_active(directory) is True

        reason, retained = service.reclaim_workers_root(base=tmp_artifact_root)
        assert reason is None
        assert retained == (directory,)
        assert directory.is_dir()
    finally:
        service._drop_lease(directory)

    assert service.run_directory_is_active(directory) is False

    reason, retained = service.reclaim_workers_root(base=tmp_artifact_root)
    assert reason is None
    assert retained == ()
    assert not directory.exists()


def test_the_lease_probe_answers_absent_held_and_free(
    tmp_artifact_root: Path,
) -> None:
    """The probe's three states, and the difference between two of them.

    A directory with **no** lease and a directory whose lease is **free** are
    distinct situations that carry the same consequence, and both are asserted
    because they are reached differently: the first is anything that was not
    created by a run of this port, the second is a run that has finished or
    died.  The lease file outliving the lock is what makes the second case
    observable at all - so it is asserted too, rather than left to the
    removal to hide.

    The probe itself holds nothing: it locks and unlocks, so probing a
    directory can never be what keeps it alive.
    """
    unleased = stranded_run_directory(tmp_artifact_root)
    assert service._lease_is_held(unleased) is False

    directory = service.prepare_workers_dir(base=tmp_artifact_root)
    try:
        assert service._lease_is_held(directory) is True
    finally:
        service._drop_lease(directory)

    assert service._lease_path(directory).is_file()
    assert service._lease_is_held(directory) is False

    # And the probe held nothing of its own: it locks to find out and unlocks
    # immediately, so asking twice cannot be what keeps a directory alive.
    # Asserted on the free lease, which is the only case where a kept lock
    # would change the second answer.
    assert service._lease_is_held(directory) is False

    service.cleanup_workers_dir(base=tmp_artifact_root, directory=directory)
    assert not directory.exists()


@pytest.mark.parametrize("name", UNPARSEABLE_RUN_DIR_NAMES)
def test_run_directory_owner_refuses_a_name_it_did_not_produce(
    name: str,
) -> None:
    """Anything not of the service's own making is not a run directory.

    Unchanged by the liveness fix and asserted so it stays that way: the
    function is now identity *only*, and identity is still what tells a run's
    working space apart from anything else found in the shared directory - the
    run lock and a run's lease file included.

    Args:
        name: A single path component the function must refuse.
    """
    assert service.run_directory_owner(name) is None


def test_a_run_shaped_name_alone_no_longer_implies_liveness(
    tmp_artifact_root: Path,
) -> None:
    """Identity and liveness are two questions with two answers.

    The fix in one assertion pair: the name still yields its process id, and
    the directory is still not alive.  A reading that returned the first as
    the second is the finding.
    """
    directory = service.prepare_workers_dir(base=tmp_artifact_root)
    try:
        assert service.run_directory_owner(directory.name) == os.getpid()
        assert service.run_directory_is_active(directory) is True
    finally:
        service.cleanup_workers_dir(
            base=tmp_artifact_root, directory=directory
        )

    stranded = stranded_run_directory(tmp_artifact_root)
    assert service.run_directory_owner(stranded.name) == 1
    assert service.run_directory_is_active(stranded) is False


@pytest.mark.parametrize("alive", [True, False])
def test_an_unanswerable_lease_probe_falls_back_to_the_process_id(
    tmp_artifact_root: Path, monkeypatch: pytest.MonkeyPatch, alive: bool
) -> None:
    """A probe that cannot answer defers to the weaker test, both ways.

    ``None`` is not an answer and must not be read as one: a platform with no
    locking primitive, or a probe that failed on its own terms, leaves the
    question to the process-id test - which is weaker but fails in the same
    safe direction, since believing a dead run alive costs a stale directory
    while believing a live run dead costs its results.

    Both directions are asserted, and the process id the fallback is given is
    asserted too: the one the directory's name carries, not this process's.

    Args:
        tmp_artifact_root: The temporary checkout root.
        monkeypatch: pytest's patching fixture.
        alive: What the process-id probe is made to report.
    """
    stranded = stranded_run_directory(tmp_artifact_root)
    probed: list[int] = []

    def unanswerable(directory: Path) -> bool | None:
        """Report that the lease cannot be consulted.

        Args:
            directory: The run directory being probed.

        Returns:
            ``None``, always.
        """
        return None

    def process_is_alive(pid: int) -> bool:
        """Record the process id asked about and answer as parametrized.

        Args:
            pid: The process id the service chose to probe.

        Returns:
            The parametrized answer.
        """
        probed.append(pid)
        return alive

    monkeypatch.setattr(service, "_lease_is_held", unanswerable)
    monkeypatch.setattr(service, "_process_is_alive", process_is_alive)

    assert service.run_directory_is_active(stranded) is alive
    assert probed == [1]


def test_prepare_workers_dir_propagates_a_lease_failure_and_claims_nothing(
    tmp_artifact_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A directory that cannot be leased is not a directory this run keeps.

    Deliberately fatal rather than downgraded to a warning: an unleased
    directory reads as abandoned to every other invocation in the checkout, so
    a run that carried on would have its live results deleted from under it by
    a concurrent clean.  ``run_suite`` catches the ``OSError`` this raises on
    the same branch that catches a directory it could not create, and reports
    both as :attr:`~app.services.RunOutcome.infrastructure_error` - the
    artifact-infrastructure class of AAP 0.4.1, whose caller-side shape is
    asserted for the creation cause in
    :func:`test_a_worker_directory_that_cannot_be_created_is_the_empty_merge_state`.

    What is asserted here is the state it leaves behind: nothing registered as
    this process's, and therefore nothing this process's own cleanup would
    later refuse to find - and the directory it had already created carries no
    lease, so the next clean reclaims it instead of preserving it forever.
    """

    def refuse(directory: Path) -> None:
        """Fail to take the lease.

        Args:
            directory: The run directory the service just created.

        Raises:
            OSError: Always, standing in for a lease that cannot be created or
                locked.
        """
        raise OSError("canned lease failure")

    monkeypatch.setattr(service, "_take_lease", refuse)
    with service._active_run_dirs_lock:
        before = set(service._active_run_dirs)

    with pytest.raises(OSError, match="canned lease failure"):
        service.prepare_workers_dir(base=tmp_artifact_root)

    with service._active_run_dirs_lock:
        assert set(service._active_run_dirs) == before

    reason, retained = service.reclaim_workers_root(base=tmp_artifact_root)
    assert reason is None
    assert retained == ()
    assert not paths.workers_dir(tmp_artifact_root).exists()


# =========================================================================== #
# The run lock (one run at a time per checkout)
#
# A per-run intermediate directory keeps two runs from reading or deleting each
# other's worker files, and that is as far as it reaches.  Three phases of a run
# operate on state the whole checkout shares: ``--clean`` empties the build
# output, the run writes into the shared intermediate directory, and the fan-out
# publishes four artifacts at fixed paths.  Interleaved, two runs leave a
# workspace holding a mixture - one run's JSON beside the other's HTML, each
# naming scenarios, substituted step arguments and screenshots from a different
# execution, with nothing in either artifact to say so (CWE-362, CWE-367).
#
# One lock held from before the clean until after the publication is what makes
# the interleaving impossible, and contention is **refused** rather than queued:
# a run driving a browser suite holds the lock for minutes, and a CI stage that
# waits that out is a hang, while a run that is merely finishing its cleanup
# releases within the grace period.
#
# Four properties are asserted below, and only the first is what a plain "does
# locking work" test would cover.
#
# **Exclusion.**  A second acquire in the same checkout is refused, and one in
# another checkout is not.
#
# **Identity, while the lock is held.**  The claim is only worth something
# while the lock file's *name* still resolves to the object the descriptor
# holds, because a departing holder unlinks its file while still holding the
# lock, and a holder that kept believing in a name somebody else had replaced
# would leave two runs each convinced it had the build output to itself.
#
# **Identity, at the release.**  The same window read the other way round, and
# the worse half of it: a release that unlinked its path unconditionally would
# delete the file a *second* run is holding at that moment, so a third run
# could then acquire a different object while the second was still executing.
# The unlink is therefore made only while the name still resolves to this
# claim's own object, and otherwise the name is left strictly alone and the
# reason says so.  What the release leaves behind is reported for the same
# reason: AAP 0.4.1 requires ``target/.workers/`` to be gone by the time the
# command returns, so this run's own lock file or an empty shared directory
# outliving it is a reportable failure - while another run's live directory in
# there is not, and is deliberately silent.
#
# **Retention by probe, not by name.**  The clean step meets the lock in one
# directory and has to keep a held one and reclaim an unheld one: keeping every
# file called ``.run.lock`` would leave the shared directory in the workspace
# for the life of the checkout, and deleting a held one would dissolve the
# exclusion.  So the question goes to the operating system, ``None`` - an
# unanswerable probe - is resolved towards keeping, and both reclaim branches
# are asserted, because the rule is implemented twice.  The same probe is what
# lets a lock file this process created but could not lock be discarded rather
# than left, and what makes a departing run's prune a *retryable* absence for
# the waiter whose turn it now is rather than a refusal.
#
# Every test here releases what it takes, in a ``finally``: the lock is an open
# descriptor and a file in a temporary tree, and a leaked one would make the
# next test in this module - or in ``tests/test_cli.py`` - contend with a lock
# nobody holds any more.
# =========================================================================== #

#: The mode :func:`~app.services.acquire_run_lock` creates its lock file with:
#: owner read and write, nothing for anybody else.  The file carries no data,
#: but a world-writable lock is a lock anybody can steal.
OWNER_ONLY_MODE: Final[int] = 0o600

#: The grace period the one wait-path test substitutes for the module's own
#: thirty seconds.  Long enough that the poll loop runs - the announcement is
#: made on the first pass that does not immediately give up - and short enough
#: that the test costs a fraction of a second.
CONTENDED_WAIT_SECONDS: Final[float] = 0.25

#: The ceiling that wait is held to, so a regression that ignored the grace
#: period and waited out the module's default would fail rather than hang.
CONTENDED_WAIT_CEILING: Final[float] = 5.0

#: The two removal branches the run lock has to survive, named as the
#: capability flag the service dispatches on: the descriptor-relative path and
#: the pathname fallback.  The lock is not a run's working file but the
#: rendezvous point every run in the checkout contends for, so *neither* branch
#: may remove it while it is held - deleting a held lock would hand two runs
#: two different lock objects - and *both* must reclaim one that is not, or the
#: shared directory outlives every command that ever ran in the checkout.
RECLAIM_BRANCHES: Final[tuple[bool, ...]] = (True, False)

#: What the service prefixes a release it could not complete with.  A release
#: reports its failure to the caller *and* records it, because the caller turns
#: the reason into an exit status while the record is what an operator reading
#: a CI log has.
RELEASE_FAILURE_PREFIX: Final[str] = "Run lock release failed"

#: A removal that reports success and leaves the file behind, which is what a
#: Windows deferred deletion or a scanner holding the file looks like from
#: inside the call.
UNLINK_LEAVES_THE_FILE: Final[str] = "leaves-the-file"

#: And a removal that refuses outright.
UNLINK_REFUSES: Final[str] = "refuses"

#: The two ways this run's own lock file can outlive the release that should
#: have removed it, each with the reason the release has to report.  Both are
#: reportable and neither may be silent: a lock file left in the shared
#: directory keeps that directory in the workspace, which is the one state AAP
#: 0.4.1 forbids after the command returns, whichever of the two produced it.
SURVIVING_LOCK_CASES: Final[tuple[tuple[str, str], ...]] = (
    (UNLINK_LEAVES_THE_FILE, "still exists after the run released it"),
    (UNLINK_REFUSES, "could not be removed"),
)

#: The poll interval substituted where a test drives the acquire loop round for
#: a reason other than timing it - a first attempt that has to fail and a
#: second that has to succeed.  Nothing in those cases depends on how long the
#: loop sleeps between the two, and the module's own tenth of a second would be
#: spent for nothing.
IMMEDIATE_POLL_SECONDS: Final[float] = 0.0


def release_failures(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Every release-failure record the service emitted.

    Args:
        caplog: pytest's log-capture fixture, with the service's logger
            enabled at ``ERROR`` by the caller.

    Returns:
        The message of each record, so a test can assert both that a reported
        failure was recorded and - the harder half - that a release which
        reported nothing recorded nothing either.
    """
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == service.__name__
        and record.levelno >= logging.ERROR
        and RELEASE_FAILURE_PREFIX in record.getMessage()
    ]


def test_the_run_lock_is_one_owner_only_file_in_the_shared_directory(
    tmp_artifact_root: Path,
) -> None:
    """The lock is taken on a known path, with a known mode.

    Where it lives is a decision rather than a detail: inside the shared
    intermediate directory, which is the one entry of the build output the
    clean step empties rather than deletes, so the lock survives the clean it
    is held across.  It is also the reason the directory can still be gone by
    the time the command returns (AAP 0.4.1) - whoever releases the lock
    removes the file and prunes the directory.
    """
    lock, refusal = service.acquire_run_lock(base=tmp_artifact_root)
    try:
        assert refusal is None
        assert lock is not None
        assert lock.path == (
            paths.workers_dir(tmp_artifact_root) / service.RUN_LOCK_NAME
        )
        assert lock.path.is_file()
        assert stat.S_IMODE(lock.path.stat().st_mode) == OWNER_ONLY_MODE
        assert lock.is_held() is True
    finally:
        if lock is not None:
            lock.release()


def test_a_second_run_in_the_same_checkout_is_refused(
    tmp_artifact_root: Path,
) -> None:
    """Contention is a refusal that names the checkout, not a wait.

    ``wait_seconds=0`` makes the attempt once, which is the shape a test can
    assert without spending the grace period; the wait itself is asserted by
    :func:`test_a_contended_acquire_announces_the_wait_once_and_then_refuses`.

    The refusal names the build output and the lock file, because that is what
    an operator reading a CI log needs in order to know *which* workspace is
    busy - and the first claim is untouched by the refusal, which is the whole
    point of refusing.
    """
    first, refusal = service.acquire_run_lock(base=tmp_artifact_root)
    assert refusal is None
    assert first is not None
    try:
        second, reason = service.acquire_run_lock(
            base=tmp_artifact_root, wait_seconds=0
        )

        assert second is None
        assert reason is not None
        assert str(paths.target_root(tmp_artifact_root)) in reason
        assert service.RUN_LOCK_NAME in reason
        assert "was not started" in reason

        assert first.is_held() is True
        assert first.path.is_file()
    finally:
        first.release()


def test_releasing_the_lock_removes_the_file_and_prunes_the_directory(
    tmp_artifact_root: Path,
) -> None:
    """A released lock leaves nothing of itself behind, twice over.

    AAP 0.4.1 requires ``target/.workers/`` to be gone before the command
    returns, so the lock file cannot simply be left in it: the holder removes
    the file and then prunes the shared directory, which prunes only when it is
    empty and is therefore a no-op while a concurrent run still holds a
    directory there.

    Idempotence is asserted too, because ``app/cli.py`` releases in a
    ``finally`` around the whole run and a success path may have released
    already.
    """
    lock, refusal = service.acquire_run_lock(base=tmp_artifact_root)
    assert refusal is None
    assert lock is not None

    lock.release()

    assert lock.is_held() is False
    assert not lock.path.exists()
    assert not paths.workers_dir(tmp_artifact_root).exists()
    # The build output itself is not the lock's to remove.
    assert paths.target_root(tmp_artifact_root).is_dir()

    # Second release: nothing to do, and nothing raised.
    lock.release()
    assert lock.is_held() is False


def test_the_lock_is_available_again_once_it_is_released(
    tmp_artifact_root: Path,
) -> None:
    """The refusal is about a live holder, never about a leftover file.

    A lock implemented as "does a file exist" would fail here the moment a run
    was killed between creating the file and removing it; this one is an
    operating-system lock on an open descriptor, so it cannot outlive its
    holder.
    """
    first, refusal = service.acquire_run_lock(base=tmp_artifact_root)
    assert refusal is None
    assert first is not None
    first.release()

    second, refusal = service.acquire_run_lock(
        base=tmp_artifact_root, wait_seconds=0
    )
    try:
        assert refusal is None
        assert second is not None
        assert second.is_held() is True
        assert second.path == first.path
    finally:
        if second is not None:
            second.release()


def test_two_checkouts_never_contend_for_one_another(
    tmp_artifact_root: Path, tmp_path: Path
) -> None:
    """The lock's scope is exactly the state two runs would corrupt.

    Two runs in separate checkouts share no build output, no intermediate
    directory and no artifact path, so serialising them would buy nothing and
    cost a CI agent its parallelism - which is also what lets this suite run
    beside others in one container.

    Args:
        tmp_artifact_root: One temporary checkout root.
        tmp_path: pytest's per-test directory, used to make a second root
            beside the first.
    """
    other_root = tmp_path / "second-workspace"
    other_root.mkdir()

    first, refusal = service.acquire_run_lock(base=tmp_artifact_root)
    assert refusal is None
    assert first is not None
    try:
        second, refusal = service.acquire_run_lock(
            base=other_root, wait_seconds=0
        )
        try:
            assert refusal is None
            assert second is not None
            assert second.path != first.path
            assert first.is_held() is True
            assert second.is_held() is True
        finally:
            if second is not None:
                second.release()
    finally:
        first.release()


def test_the_claim_is_lost_when_the_lock_file_is_unlinked(
    tmp_artifact_root: Path,
) -> None:
    """A holder whose file has gone stops claiming to hold anything.

    The identity half of the contract.  An unlinked lock file leaves the
    descriptor locked and the object alive, so the lock "works" in every sense
    an ``flock`` can report - while the *name* is now free for another run to
    create and lock a different object at.  Both runs would then believe they
    had the build output to themselves, which is the interleaving this lock
    exists to prevent, so the answer here has to be ``False``.
    """
    lock, refusal = service.acquire_run_lock(base=tmp_artifact_root)
    assert refusal is None
    assert lock is not None
    try:
        assert lock.is_held() is True

        lock.path.unlink()

        assert lock.is_held() is False
    finally:
        lock.release()
    assert lock.is_held() is False


def test_the_claim_is_lost_when_the_lock_file_is_replaced(
    tmp_artifact_root: Path,
) -> None:
    """A fresh file at the same name is not the object that was locked.

    The case an unlink test alone would miss: the name resolves, it resolves to
    a regular file, and it is a different object - which is exactly the state a
    second run's ``acquire_run_lock`` creates for itself after a holder has
    unlinked its own file.  Identity is compared by device and inode, so the
    substitution is detected rather than believed.
    """
    lock, refusal = service.acquire_run_lock(base=tmp_artifact_root)
    assert refusal is None
    assert lock is not None
    try:
        original = lock.path.stat().st_ino

        lock.path.unlink()
        lock.path.write_text("", encoding="utf-8")
        assert lock.path.stat().st_ino != original

        assert lock.is_held() is False
    finally:
        # The claim is given back either way; what it stops doing is speaking
        # for whatever object now stands at that name.
        lock.release()
    assert lock.descriptor is None
    assert lock.is_held() is False


def test_the_context_manager_releases_on_the_way_out(
    tmp_artifact_root: Path,
) -> None:
    """``with`` is the same release, and yields the lock it was given.

    The form the boundary is expressed in where a caller holds the lock for a
    block rather than across a whole command: entering returns the claim that
    was already taken - there is no second acquire hidden in ``__enter__`` - and
    leaving performs the one release, file removal included.
    """
    lock, refusal = service.acquire_run_lock(base=tmp_artifact_root)
    assert refusal is None
    assert lock is not None

    with lock as held:
        assert held is lock
        assert held.is_held() is True

    assert lock.is_held() is False
    assert not lock.path.exists()


def test_the_context_manager_releases_when_the_block_raises(
    tmp_artifact_root: Path,
) -> None:
    """An exception inside the block gives the lock back and travels on.

    The property the whole design rests on: a run that dies with the lock held
    must not leave the checkout unusable.  Asserted with a defect-shaped
    exception rather than a return path, and followed by a fresh acquire - the
    proof that the lock is genuinely free rather than merely reported free.
    """
    lock, refusal = service.acquire_run_lock(base=tmp_artifact_root)
    assert refusal is None
    assert lock is not None

    with pytest.raises(RuntimeError, match="canned failure inside the lock"):
        with lock:
            raise RuntimeError("canned failure inside the lock")

    assert lock.is_held() is False
    assert not lock.path.exists()

    again, refusal = service.acquire_run_lock(
        base=tmp_artifact_root, wait_seconds=0
    )
    try:
        assert refusal is None
        assert again is not None
    finally:
        if again is not None:
            again.release()


@pytest.mark.parametrize("confined", RECLAIM_BRANCHES)
def test_the_clean_retains_the_run_lock_and_reclaims_the_rest(
    tmp_artifact_root: Path, monkeypatch: pytest.MonkeyPatch, confined: bool
) -> None:
    """``--clean`` empties the shared directory around the lock, not through it.

    The clean and the lock meet in one directory, and the ordering is what
    makes the lock usable at all: the run takes the lock *before* it cleans, so
    the clean it performs is one that must leave the lock alone.  Removing the
    file would leave the holder holding an object nobody will ever consult
    again and the next run creating a second one at the same name.

    It is reported on ``retained`` rather than silently skipped, so a caller
    checking "did the clean empty the directory" can tell a deliberate survivor
    from a failed removal, and the shared directory legitimately outlives the
    clean while the lock is in it.

    Args:
        tmp_artifact_root: The temporary checkout root.
        monkeypatch: pytest's patching fixture.
        confined: Whether the descriptor-relative branch or the pathname
            fallback performs the reclaim.
    """
    lock, refusal = service.acquire_run_lock(base=tmp_artifact_root)
    assert refusal is None
    assert lock is not None
    shared_root = paths.workers_dir(tmp_artifact_root)
    try:
        stranded = stranded_run_directory(tmp_artifact_root)
        monkeypatch.setattr(service, "_SUPPORTS_CONFINED_REMOVAL", confined)

        reason, retained = service.reclaim_workers_root(base=tmp_artifact_root)

        assert reason is None
        assert retained == (lock.path,)
        assert lock.path.is_file()
        assert lock.is_held() is True
        # Everything that was not the lock is gone, and the directory stayed
        # because the lock is still in it.
        assert not stranded.exists()
        assert shared_root.is_dir()
        assert sorted(shared_root.iterdir()) == [lock.path]
    finally:
        lock.release()

    # And the release is what finally leaves the workspace clean.
    assert not shared_root.exists()


def test_a_contended_acquire_announces_the_wait_once_and_then_refuses(
    tmp_artifact_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The grace period is announced once, waited out, and then given up on.

    The default is thirty seconds and is resolved from the module constant at
    call time, which is what lets this test substitute a quarter of a second
    for it rather than sleep through a CI grace period.  Three things are
    asserted about the wait: it says so **once** - a per-poll record would fill
    a CI log with one line per hundred milliseconds - it ends in the same
    refusal a zero wait produces, and it ends at all.
    """
    first, refusal = service.acquire_run_lock(base=tmp_artifact_root)
    assert refusal is None
    assert first is not None
    monkeypatch.setattr(
        service, "_RUN_LOCK_WAIT_SECONDS", CONTENDED_WAIT_SECONDS
    )
    try:
        with caplog.at_level(logging.INFO, logger=service.__name__):
            started = time.monotonic()
            second, reason = service.acquire_run_lock(base=tmp_artifact_root)
            waited = time.monotonic() - started

        assert second is None
        assert reason is not None
        assert str(paths.target_root(tmp_artifact_root)) in reason

        announcements = [
            record.getMessage()
            for record in caplog.records
            if record.name == service.__name__
            and "waiting up to" in record.getMessage()
        ]
        assert len(announcements) == 1, announcements
        assert str(first.path) in announcements[0]

        # It really waited, and it really stopped.
        assert waited >= CONTENDED_WAIT_SECONDS
        assert waited < CONTENDED_WAIT_CEILING
        assert first.is_held() is True
    finally:
        first.release()


def test_a_release_never_removes_a_lock_file_that_is_no_longer_its_own(
    tmp_artifact_root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A departing holder must not delete a second run's live lock.

    The release half of the identity contract, and the finding in one test: a
    release that unlinked its path unconditionally would, in exactly the state
    the two identity tests above set up, remove the file **another run is
    holding right now**.  A third run's acquire would then create a different
    object at the same name and lock that, and two runs would be executing in
    one checkout each convinced it had the build output to itself - the
    exclusion dissolved by its own teardown (CWE-362, CWE-367).

    So the unlink is made only while the name still resolves to this claim's
    own object.  Here it does not, and everything the release is entitled to
    give back it gives back: the reason names the replacement, the descriptor
    is closed, the claim stops answering, and the file is left strictly alone.
    """
    first, refusal = service.acquire_run_lock(base=tmp_artifact_root)
    assert refusal is None
    assert first is not None
    descriptor = first.descriptor
    assert descriptor is not None

    # The window: this holder's file goes, and a second run creates and locks
    # its own file at the same name - which is precisely what a departing
    # holder's own unlink leaves behind for the next acquire to do.
    first.path.unlink()
    second, refusal = service.acquire_run_lock(
        base=tmp_artifact_root, wait_seconds=0
    )
    assert refusal is None
    assert second is not None
    try:
        with caplog.at_level(logging.ERROR, logger=service.__name__):
            reason = first.release()

        # Asserted before anything else here can open a file and be handed
        # the same number: the descriptor really was closed.
        with pytest.raises(OSError):
            os.fstat(descriptor)

        assert reason is not None
        assert "was replaced or removed while the run held it" in reason
        assert "left untouched" in reason
        assert str(first.path) in reason
        assert len(release_failures(caplog)) == 1, caplog.records

        # The whole of the finding: the second run's lock file is still there,
        # and the second run still holds it.
        assert second.path == first.path
        assert second.path.is_file()
        assert second.is_held() is True

        # And the departing claim stops speaking for anything.
        assert first.descriptor is None
        assert first.is_held() is False

        # Idempotent, and silent: ``app/cli.py`` releases before it publishes
        # a status and again in its ``finally``.
        caplog.clear()
        assert first.release() is None
        assert release_failures(caplog) == []
    finally:
        second.release()


@pytest.mark.parametrize(("behaviour", "fragment"), SURVIVING_LOCK_CASES)
def test_a_release_reports_a_lock_file_that_outlived_it(
    tmp_artifact_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    behaviour: str,
    fragment: str,
) -> None:
    """A lock file that survives its own release is reported, not forgotten.

    The postcondition of a release is established rather than assumed, because
    the file the release removes is what keeps the shared intermediate
    directory alive and AAP 0.4.1 requires that directory to be gone before the
    command returns.  Two ways for it to survive are asserted, since they reach
    the reason by different routes: a removal that *reports* success and leaves
    the file behind is caught by the survivor check afterwards, and one that
    refuses is reported by the failure itself.

    Either way the claim is still given back - the descriptor is closed and the
    lock stops being held - because a release that left the lock held on the
    strength of an unremovable file would make the checkout unusable until the
    process exited.

    Args:
        tmp_artifact_root: The temporary checkout root.
        monkeypatch: pytest's patching fixture.
        behaviour: How the stand-in removal misbehaves.
        fragment: The text the reason has to carry for that behaviour.
    """
    lock, refusal = service.acquire_run_lock(base=tmp_artifact_root)
    assert refusal is None
    assert lock is not None
    real_unlink = os.unlink

    def unlink(target: Any, *arguments: Any, **keywords: Any) -> None:
        """Refuse to remove this run's lock file, and pass everything else on.

        Args:
            target: The path being removed.
            *arguments: Positional arguments passed through untouched.
            **keywords: Keyword arguments passed through untouched.

        Raises:
            PermissionError: For the lock file, in the refusing case.
        """
        named = isinstance(target, (str, os.PathLike))
        if named and Path(target) == lock.path:
            if behaviour == UNLINK_REFUSES:
                raise PermissionError("canned removal failure")
            # Reported as done, and not done: the other way a file outlives
            # the call that removed it.
            return
        real_unlink(target, *arguments, **keywords)

    monkeypatch.setattr(os, "unlink", unlink)

    with caplog.at_level(logging.ERROR, logger=service.__name__):
        reason = lock.release()

    assert reason is not None
    assert "this run's lock file" in reason
    assert fragment in reason
    assert str(lock.path) in reason
    assert len(release_failures(caplog)) == 1, caplog.records

    # The file is still there - which is what was reported - and the claim is
    # still given back.
    assert lock.path.is_file()
    assert lock.descriptor is None
    assert lock.is_held() is False


def test_a_release_reports_a_shared_directory_that_could_not_be_pruned(
    tmp_artifact_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An empty shared directory left behind is the same reportable state.

    The second half of the release's postcondition, and the one a test that
    only watched the lock file would miss: the file can be gone and the
    directory that held it still be in the workspace, which is exactly what AAP
    0.4.1 forbids - ``Jenkins:15`` narrows the publisher to one artifact
    because nothing intermediate may be readable, and a surviving
    ``target/.workers`` is that directory reappearing after every command.

    The prune is neutralised rather than the directory made unremovable,
    because what is being asserted is the *report*: a release that pruned
    nothing and said nothing would leave the caller unable to tell.
    """
    lock, refusal = service.acquire_run_lock(base=tmp_artifact_root)
    assert refusal is None
    assert lock is not None
    shared_root = paths.workers_dir(tmp_artifact_root)

    def do_not_prune(base: Path | str | None) -> None:
        """Leave the shared directory exactly where it is.

        Args:
            base: The directory it hangs off, ignored.
        """

    monkeypatch.setattr(service, "_prune_workers_root", do_not_prune)

    with caplog.at_level(logging.ERROR, logger=service.__name__):
        reason = lock.release()

    assert reason is not None
    assert "is empty but could not be removed" in reason
    assert str(shared_root) in reason
    assert len(release_failures(caplog)) == 1, caplog.records

    # Its own file did go, so what is being reported is the directory alone.
    assert not lock.path.exists()
    assert entries_left_in(shared_root) == []
    assert lock.is_held() is False


def test_a_release_is_silent_about_another_runs_live_directory(
    tmp_artifact_root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A shared directory another run is using is not this release's failure.

    The distinction that keeps the reporting usable, and the reason the
    survivor check reads the directory's *contents* rather than its existence:
    a concurrent run's live working directory both prevents the prune and is
    the legitimate reason for it, so a release that reported it would turn
    every second concurrent run into a failed one.

    Asserted on both channels, because a reason suppressed and a record emitted
    anyway would still put a false failure in the operator's log.
    """
    lock, refusal = service.acquire_run_lock(base=tmp_artifact_root)
    assert refusal is None
    assert lock is not None
    shared_root = paths.workers_dir(tmp_artifact_root)
    other = service.prepare_workers_dir(base=tmp_artifact_root)
    try:
        with caplog.at_level(logging.ERROR, logger=service.__name__):
            reason = lock.release()

        assert reason is None
        assert release_failures(caplog) == []

        # This release's own file is gone; what remains is the other run's,
        # untouched and still live.
        assert not lock.path.exists()
        assert lock.is_held() is False
        assert other.is_dir()
        assert service.run_directory_is_active(other) is True
        assert shared_root.is_dir()
    finally:
        service.cleanup_workers_dir(base=tmp_artifact_root, directory=other)

    # And once that run has finished too, nothing is left of either.
    assert not shared_root.exists()


@pytest.mark.parametrize("confined", RECLAIM_BRANCHES)
def test_a_stale_unheld_lock_file_is_reclaimed_and_the_directory_pruned(
    tmp_artifact_root: Path, monkeypatch: pytest.MonkeyPatch, confined: bool
) -> None:
    """Retention is what the operating system answers, never the name.

    The other direction of the retention rule, and the one a "keep anything
    called ``.run.lock``" reading gets wrong: a lock file left behind by a run
    that was killed between creating it and removing it is *residue*, and
    keeping it on the strength of its name would leave the shared intermediate
    directory in the workspace after every command in the checkout's life -
    which AAP 0.4.1 forbids, and which ``Jenkins:15``'s narrowed publisher glob
    exists because of.

    Both halves are asserted, because a test that only checked the outcome
    would pass on a service that had simply stopped recognising the name: the
    probe says the file is free, and the reclaim then removes it, takes the
    stranded directory beside it, and prunes the directory both were in.

    Asserted on both branches, because the rule is implemented twice - once
    descriptor-relative and once by pathname - and the held case is asserted
    on both by
    :func:`test_the_clean_retains_the_run_lock_and_reclaims_the_rest`.

    Args:
        tmp_artifact_root: The temporary checkout root.
        monkeypatch: pytest's patching fixture.
        confined: Whether the descriptor-relative branch or the pathname
            fallback performs the reclaim.
    """
    shared_root = paths.ensure_dir(paths.workers_dir(tmp_artifact_root))
    stale = shared_root / service.RUN_LOCK_NAME
    stale.write_text("", encoding="utf-8")
    stranded = stranded_run_directory(tmp_artifact_root)

    # Nobody holds it, and that is asked rather than assumed.
    assert service._file_lock_is_held(stale) is False
    assert service._run_lock_is_in_use(stale) is False

    monkeypatch.setattr(service, "_SUPPORTS_CONFINED_REMOVAL", confined)

    reason, retained = service.reclaim_workers_root(base=tmp_artifact_root)

    assert reason is None
    assert retained == ()
    assert not stale.exists()
    assert not stranded.exists()
    assert not shared_root.exists()


@pytest.mark.parametrize("confined", RECLAIM_BRANCHES)
def test_a_lock_whose_holder_cannot_be_established_is_retained(
    tmp_artifact_root: Path, monkeypatch: pytest.MonkeyPatch, confined: bool
) -> None:
    """An unanswerable probe is not an answer, and fails safe towards keeping.

    The third state the probe can be in, and the only one where the two costs
    are not symmetrical: a lock wrongly kept costs a leftover file that the
    next run's reclaim removes, while a lock wrongly deleted costs the mutual
    exclusion two concurrent runs depend on.  So ``None`` is resolved towards
    retention, and the retention is *reported* so the caller can tell a
    deliberate survivor from a failed removal.

    The probe is made unanswerable for the lock file alone, which is what makes
    the assertion precise: the stranded run directory beside it is judged by
    its own lease, answered honestly, and is still reclaimed in the same call.

    Args:
        tmp_artifact_root: The temporary checkout root.
        monkeypatch: pytest's patching fixture.
        confined: Whether the descriptor-relative branch or the pathname
            fallback performs the reclaim.
    """
    lock, refusal = service.acquire_run_lock(base=tmp_artifact_root)
    assert refusal is None
    assert lock is not None
    try:
        stranded = stranded_run_directory(tmp_artifact_root)
        real_probe = service._file_lock_is_held

        def unanswerable(path: Path) -> bool | None:
            """Refuse to answer for the lock file, and delegate the rest.

            Args:
                path: The lock or lease file being probed.

            Returns:
                ``None`` for the run lock - a platform with no locking
                primitive, or a probe that failed on its own terms - and the
                real answer for every lease.
            """
            if path.name == service.RUN_LOCK_NAME:
                return None
            return real_probe(path)

        monkeypatch.setattr(service, "_file_lock_is_held", unanswerable)
        monkeypatch.setattr(service, "_SUPPORTS_CONFINED_REMOVAL", confined)

        reason, retained = service.reclaim_workers_root(base=tmp_artifact_root)

        assert reason is None
        assert retained == (lock.path,)
        assert lock.path.is_file()
        # The fail-safe applies to the lock alone: what could be judged was
        # judged, and reclaimed.
        assert not stranded.exists()
        assert paths.workers_dir(tmp_artifact_root).is_dir()
    finally:
        lock.release()


def test_a_lock_that_cannot_be_locked_is_discarded_rather_than_left(
    tmp_artifact_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rendezvous point with no lock behind it is not left in the workspace.

    The one path on which this process creates the lock file and then cannot
    lock it - a filesystem without locking support, which is the platform
    refusal AAP 0.1.3's three non-zero classes absorb as an
    artifact-infrastructure failure.  The file it created is therefore removed
    and the shared directory pruned: a refused run has to leave the workspace
    as it found it, and a lock file nobody can ever hold would otherwise be
    residue that only the next run's reclaim clears.

    The build output root is deliberately *not* removed - the refusal created
    it on the way in, and emptying or deleting it is the clean step's decision
    rather than this one's.
    """
    lock_path = paths.workers_dir(tmp_artifact_root) / service.RUN_LOCK_NAME

    def cannot_lock(descriptor: int) -> bool:
        """Fail to lock, as a filesystem without the primitive would.

        Args:
            descriptor: The open descriptor the service would lock.

        Raises:
            OSError: Always.
        """
        raise OSError("canned locking failure")

    monkeypatch.setattr(service, "_lock_exclusive", cannot_lock)

    lock, reason = service.acquire_run_lock(base=tmp_artifact_root)

    assert lock is None
    assert reason is not None
    assert "cannot be taken on this platform" in reason
    assert "nothing was executed" in reason
    assert str(lock_path) in reason

    assert not lock_path.exists()
    assert not paths.workers_dir(tmp_artifact_root).exists()
    assert paths.target_root(tmp_artifact_root).is_dir()


def test_a_lock_file_taken_by_a_departing_run_is_recreated_not_refused(
    tmp_artifact_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The run that waited politely for its turn must not be the one refused.

    The consequence of the release removing its own file *and* pruning the
    directory it sat in: a waiter's next attempt can find the directory gone
    from underneath it, and the open fails with "no such file" even though the
    flags say create - because what is missing is the directory, not the file.
    Read as a failure that would refuse precisely the run whose turn it now
    is, so it is retryable: the directory is recreated and the attempt made
    again.

    The announcement is asserted to be **absent**, which is the second half of
    the case: "another run holds this, waiting" is for genuine contention, and
    a departure that left nothing behind is not contention.  A per-attempt
    announcement would also put a line in the CI log for a wait nobody made.
    """
    shared_root = paths.workers_dir(tmp_artifact_root)
    real_open = os.open
    attempts: list[Path] = []

    def opener(path: Any, *arguments: Any, **keywords: Any) -> int:
        """Take the directory away once, then open for real.

        Args:
            path: The path being opened.
            *arguments: Positional arguments passed through untouched.
            **keywords: Keyword arguments passed through untouched.

        Returns:
            A descriptor, for every call but the first on the lock file.

        Raises:
            FileNotFoundError: On that first call, as the departing holder's
                prune makes the real one raise.
        """
        named = isinstance(path, (str, os.PathLike))
        if named and Path(path).name == service.RUN_LOCK_NAME and not attempts:
            attempts.append(Path(path))
            shared_root.rmdir()
            raise FileNotFoundError("canned departure")
        return real_open(path, *arguments, **keywords)

    monkeypatch.setattr(os, "open", opener)
    monkeypatch.setattr(
        service, "_RUN_LOCK_POLL_SECONDS", IMMEDIATE_POLL_SECONDS
    )

    with caplog.at_level(logging.INFO, logger=service.__name__):
        lock, reason = service.acquire_run_lock(base=tmp_artifact_root)
    try:
        assert reason is None
        assert lock is not None
        assert attempts == [shared_root / service.RUN_LOCK_NAME]
        assert lock.is_held() is True
        assert lock.path.is_file()
        assert shared_root.is_dir()

        announcements = [
            record.getMessage()
            for record in caplog.records
            if record.name == service.__name__
            and "waiting up to" in record.getMessage()
        ]
        assert announcements == [], announcements
    finally:
        if lock is not None:
            lock.release()


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


# =========================================================================== #
# The *live* relay, driven against a real child process
#
# Everything above drives the after-the-fact path, because that is the one a
# stubbed ``spawn`` takes.  Production takes the other one:
# ``_spawn_worker`` starts a reader thread per pipe and hands back a worker
# marked ``output_relayed``, so ``_relay_output`` returns at its guard and
# every guarantee a reader of a CI log depends on - rendering, redaction, the
# severity floor, the size bound - has to hold in ``_relay_stream`` instead.
# Asserting it on the stub alone is what let the two implementations drift
# apart (review finding ``RUN-F01`` / ``SEC2-F01`` / ``SEC2-F05``), so the
# five tests below drive the real function against a real child.
#
# This is the *only* group in this module that starts a process.  It is a
# short ``-c`` script of this interpreter, it writes what the test tells it to
# and exits, and it touches nothing outside its own two pipes: no engine, no
# browser, no network, no artifact, and a temporary working directory.  The
# module's other seams stay stubbed for the reason its docstring gives.
# =========================================================================== #

#: A child that writes exactly what a test hands it, to whichever stream.
#:
#: Instructions arrive as one JSON argument of ``(stream, text, repeat)``
#: triples.  JSON rather than raw argv entries because the payloads are
#: deliberately hostile - a bell, an ESC, a lone ``CR`` - and ``repeat``
#: rather than pre-expanded text because one case needs a few hundred
#: kilobytes on one line, which belongs in the child's memory rather than in
#: an argument list.  Each write is flushed, so the parent sees it while the
#: child is still running, which is the property the relay is being tested
#: for.
RELAY_CHILD_SCRIPT: Final[str] = (
    "import json, sys\n"
    "for name, text, repeat in json.loads(sys.argv[1]):\n"
    "    stream = sys.stdout if name == 'out' else sys.stderr\n"
    "    stream.write(text * repeat)\n"
    "    stream.flush()\n"
)

#: The shard label the live tests publish, so the records they assert on are
#: found by the same :func:`relayed_records` helper the stubbed tests use.
LIVE_RELAY_SHARD: Final[int] = 0


def spawn_relay_child(
    writes: Sequence[tuple[str, str, int]], cwd: Path
) -> service.WorkerProcess:
    """Run :data:`RELAY_CHILD_SCRIPT` through the module's real launch.

    The launch is reached by its own name rather than through
    :func:`~app.services.test_run_service.run_suite`, which keeps the child a
    three-line script instead of a behave invocation: what is under test is
    the relay, and a real engine would add a browser, a suite and an artifact
    tree to a test about whether a line is sanitized.

    Args:
        writes: ``(stream, text, repeat)`` triples for the child, where
            ``stream`` is ``"out"`` or ``"err"``.
        cwd: Working directory for the child, always a temporary one.

    Returns:
        The finished worker, carrying its status and the bounded, *rendered*
        tail of each stream.
    """
    command = [
        sys.executable,
        "-c",
        RELAY_CHILD_SCRIPT,
        json.dumps([list(write) for write in writes]),
    ]
    # The shard label travels out of band, through the context variable the
    # launch reads in the calling thread - the ``spawn`` seam takes the
    # command and the directory and nothing else.  Reset afterwards, so a
    # label cannot leak into another test through this thread's context.
    token = service._shard_output_label.set(f"shard {LIVE_RELAY_SHARD}")
    try:
        return service._spawn_worker(command, cwd)
    finally:
        service._shard_output_label.reset(token)


def test_the_live_relay_renders_every_line_a_real_child_writes(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A real worker's hostile output reaches the log rendered, not raw.

    The live path, asserted end to end: a child writes control characters, a
    forged line break, a credential-shaped step phrase and two level tokens,
    and every one of them is subject to
    :func:`~app.logging_config.render_worker_line` before it becomes a record
    or enters the retained tail.

    * **Redacted.**  ``User enters "<username>" username`` is this suite's own
      step phrasing [Login.feature:15], so a child diagnostic quoting a
      substituted step is exactly how an account would reach a CI console.
      The line survives and the value does not.  Redaction is log-only - AAP
      0.8 requires the artifacts to carry that fixture data verbatim.
    * **Control-safe.**  No record carries ``CR``, ``LF``, ``ESC`` or a bell:
      a bell is spelled printably inside the record it belongs to, an ANSI
      sequence is removed with the text it coloured kept, and the lone ``CR``
      the child wrote becomes two records that *both* carry ``[shard N]``
      rather than one tagged record and one forged untagged one (CWE-117).
    * **Severity survives the process boundary.**  The child's
      ``LOG_ERROR:`` line is emitted at ``ERROR`` even though it arrived on
      ``stdout``, whose floor is ``INFO``; its ``LOG_DEBUG:`` line on
      ``stderr`` stays at the ``WARNING`` floor, so a child cannot mute its
      own diagnostics by printing a low token.
    * **The tail matches the log.**  What the worker hands back is the
      rendered text, not the raw text, so an after-the-fact reader of
      :attr:`~app.services.test_run_service.WorkerProcess.stderr` cannot
      recover what the log refused to print.

    Blank lines are dropped on this path too, which is why the child's empty
    line produces no record.
    """
    with caplog.at_level(logging.DEBUG, logger=service.__name__):
        worker = spawn_relay_child(
            (
                (
                    "out",
                    "LOG_ERROR:app.reporting.screenshots:"
                    " the capture was suppressed\n",
                    1,
                ),
                ("out", "bell\x07and \x1b[31mcolour\x1b[0m\n", 1),
                ("out", "\n", 1),
                ("out", "carriage\rreturn\n", 1),
                ("err", 'User enters "a-real-account" username\n', 1),
                ("err", "LOG_DEBUG:behave: selecting features\n", 1),
            ),
            tmp_path,
        )

    assert worker.returncode == 0
    records = relayed_records(caplog, LIVE_RELAY_SHARD)
    messages = [record.getMessage() for record in records]

    # Six writes, one of them the blank line that is dropped, and the lone CR
    # is a break the parent's universal-newline translation recognises and
    # therefore tags on both sides: six records.
    assert len(messages) == 6, messages
    assert all(message.startswith("[shard 0] ") for message in messages)
    for message in messages:
        for forbidden in ("\r", "\n", "\x1b", "\x07"):
            assert forbidden not in message, message

    # Asserted per stream, because the two reader threads are concurrent and
    # only the order *within* a stream is a property of the relay.  The bell
    # is spelled printably, the ANSI sequence is gone with the text it
    # coloured kept, and the forged break is two attributable records.
    from_stdout = [
        message
        for message in messages
        if "User enters" not in message and "LOG_DEBUG:behave:" not in message
    ]
    assert from_stdout == [
        "[shard 0] LOG_ERROR:app.reporting.screenshots:"
        " the capture was suppressed",
        "[shard 0] bell\\aand colour",
        "[shard 0] carriage",
        "[shard 0] return",
    ]
    from_stderr = [message for message in messages if message not in from_stdout]
    assert from_stderr == [
        "[shard 0] User enters [redacted] username",
        "[shard 0] LOG_DEBUG:behave: selecting features",
    ]

    levels = {
        record.levelno
        for record in records
        if "LOG_ERROR:app.reporting.screenshots:" in record.getMessage()
    }
    assert levels == {logging.ERROR}
    floored = [
        record for record in records if "LOG_DEBUG:behave:" in record.getMessage()
    ]
    assert [record.levelno for record in floored] == [logging.WARNING]

    # The credential is gone from the record *and* from the tail the worker
    # hands back, and the line that carried it is still there.
    assert "a-real-account" not in "\n".join(messages)
    assert any("username" in message for message in messages)
    assert worker.stderr is not None
    assert "a-real-account" not in worker.stderr
    assert "User enters [redacted] username" in worker.stderr
    assert worker.stdout is not None
    assert "\x07" not in worker.stdout
    assert "\x1b" not in worker.stdout
    assert "bell\\aand colour" in worker.stdout


def test_the_live_relay_bounds_one_very_long_line(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A single enormous line becomes one bounded record, and says so.

    A child can legitimately write a line far longer than a console log can
    carry - a dumped DOM, a driver's full capability payload - and forwarding
    it whole would bury the diagnostics the run is being judged on.  The line
    here is within the relay's read bound, so it is read whole in one call
    exactly as it always was, and the *renderer* bounds it: one record, the
    text cut to :data:`~app.logging_config.RELAYED_LINE_LIMIT` characters and
    a suffix naming exactly how many were dropped, so a reader can tell
    truncation from a line that merely ended.  Nothing is summarised and the
    line is not dropped.
    """
    unit = "step detail "
    repeat = 300

    with caplog.at_level(logging.DEBUG, logger=service.__name__):
        worker = spawn_relay_child(
            (("out", unit, repeat), ("out", "\n", 1)), tmp_path
        )

    records = relayed_records(caplog, LIVE_RELAY_SHARD)
    assert len(records) == 1, [record.getMessage() for record in records]

    message = records[0].getMessage()
    assert records[0].levelno == logging.INFO
    assert message.startswith("[shard 0] " + unit)
    assert "char(s) truncated]" in message
    # Shorter than what the child wrote, and shorter than the read bound - the
    # record is bounded by the renderer, not merely by the reader.
    assert len(message) < len(unit) * repeat
    assert len(message) < service._RELAY_READ_LIMIT

    # The tail carries the same bounded text, so no reader of it recovers the
    # payload the log declined to print.
    assert worker.stdout is not None
    assert worker.stdout.splitlines() == [message.removeprefix("[shard 0] ")]


def test_the_live_relay_drains_a_line_larger_than_its_read_bound(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A child that writes no newline cannot make the parent allocate it all.

    ``iter(stream.readline, "")`` let the *child* decide how much memory this
    process allocated: one unterminated line of any size was read whole into
    the parent and a copy of it retained in the tail (CWE-400).  Each read is
    now bounded at :data:`~app.services.test_run_service._RELAY_READ_LIMIT`
    characters, and the three properties that follow from it are asserted
    here against a child that writes a few hundred kilobytes before its
    first newline:

    * the head of the line is relayed like any other line - rendered, bounded
      and tagged - so the diagnostic is not lost;
    * the remainder is discarded rather than relayed or retained, and exactly
      **one** further record accounts for it: a character count and never the
      text, which is both the honest report and the only bounded one, since
      relaying every instalment would turn one pathological line into an
      unbounded number of parent records;
    * the relay resynchronises on the next newline and keeps going, which is
      what stops one hostile line from costing the rest of the shard's
      output.

    The discarded count is exact - everything the child wrote on that line
    beyond the first read - which is what pins the bound to the reader rather
    than to the renderer, and the record's own size is what proves the count
    was accumulated rather than the text.
    """
    unit = "overlong "
    repeat = 40_000
    written = len(unit) * repeat

    with caplog.at_level(logging.DEBUG, logger=service.__name__):
        worker = spawn_relay_child(
            (
                ("out", unit, repeat),
                ("out", "\n", 1),
                ("out", "the relay resynchronised\n", 1),
            ),
            tmp_path,
        )

    records = relayed_records(caplog, LIVE_RELAY_SHARD)
    messages = [record.getMessage() for record in records]
    assert len(messages) == 3, messages

    head, notice, resynchronised = records
    assert head.levelno == logging.INFO
    assert head.getMessage().startswith("[shard 0] " + unit)
    assert "char(s) truncated]" in head.getMessage()

    # One record for the remainder, carrying a count and no payload.  It is a
    # diagnostic rather than progress: output being dropped is an anomaly of
    # the child, so it is emitted above the stdout floor.
    assert notice.levelno == logging.WARNING
    assert notice.getMessage().startswith("[shard 0] ")
    assert "discarded" in notice.getMessage()
    # The tag is stripped before the count is read, so the shard number in it
    # cannot be mistaken for the figure under test.
    reported = notice.getMessage().removeprefix("[shard 0] ")
    assert re.findall(r"\d+", reported) == [
        str(written - service._RELAY_READ_LIMIT)
    ], reported
    assert unit not in reported

    assert resynchronised.getMessage() == "[shard 0] the relay resynchronised"

    # Nothing anywhere near the payload's size reached a record or the tail,
    # and the parent's own notice is not passed off as something the child
    # wrote.
    for message in messages:
        assert len(message) < service._RELAY_READ_LIMIT, len(message)
    assert worker.stdout is not None
    assert len(worker.stdout.splitlines()) == 2
    assert "discarded" not in worker.stdout
    assert worker.stdout.endswith("the relay resynchronised")
    assert len(worker.stdout) < service._RELAY_READ_LIMIT


def test_the_live_relay_reports_nothing_for_a_line_exactly_at_its_bound(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A line that fills the read bound exactly loses nothing and says nothing.

    The boundary between the two branches, and the one a reader would be
    misled by if it were wrong: the child's line is exactly
    :data:`~app.services.test_run_service._RELAY_READ_LIMIT` characters long,
    so the first read returns it whole *without* its terminator and the drain
    that follows finds the newline immediately.  Nothing was lost, so nothing
    is reported - a discarded-characters record naming zero would claim an
    anomaly that did not happen - and the line itself is relayed exactly once.
    """
    unit = "bound "
    repeat = 1365
    filler = "xy"
    assert len(unit) * repeat + len(filler) == service._RELAY_READ_LIMIT

    with caplog.at_level(logging.DEBUG, logger=service.__name__):
        worker = spawn_relay_child(
            (("out", unit, repeat), ("out", f"{filler}\n", 1)), tmp_path
        )

    records = relayed_records(caplog, LIVE_RELAY_SHARD)
    assert len(records) == 1, [record.getMessage() for record in records]
    assert records[0].levelno == logging.INFO
    assert records[0].getMessage().startswith("[shard 0] " + unit)
    assert "char(s) truncated]" in records[0].getMessage()
    assert "discarded" not in records[0].getMessage()
    assert worker.stdout is not None
    assert len(worker.stdout.splitlines()) == 1


def test_the_live_relay_keeps_an_unterminated_last_line(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A child that exits mid-line loses neither the line nor the run.

    The other way a read can return without a terminator, and the common one:
    the child's last line was never newline-terminated, and EOF is what ends
    it.  That is not an over-long line, so it is relayed once and no
    discarded-characters record is emitted - a count of zero would be a record
    about nothing - and the reader thread then returns at EOF as it always
    did, which is what lets the launch join it and report the exit status.
    """
    with caplog.at_level(logging.DEBUG, logger=service.__name__):
        worker = spawn_relay_child(
            (("err", "unterminated diagnostic, no newline", 1),), tmp_path
        )

    records = relayed_records(caplog, LIVE_RELAY_SHARD)
    assert [record.getMessage() for record in records] == [
        "[shard 0] unterminated diagnostic, no newline"
    ]
    assert [record.levelno for record in records] == [logging.WARNING]
    assert worker.returncode == 0
    assert worker.stderr == "unterminated diagnostic, no newline"


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


def test_an_all_dead_run_attributes_every_shard_and_logs_none_of_them(
    feature_tree: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The producer half of the dead-shard diagnostics, on the worst path.

    An all-workers-dead run is the case where two signals coincide: every
    shard is dead **and** the merge produced nothing, so ``app/cli.py`` settles
    on its artifact-infrastructure class rather than its dead-worker one.  The
    shard identities are the only thing that says *which* workers died and
    where, and they exist nowhere but on this outcome - so what this module has
    to guarantee is that each one is fully attributed before it is handed over,
    and that handing it over is all this module does with it.

    Fully attributed means the shard's index and its scenario count, which is
    AAP 0.4.1's "the incomplete shard is named on stderr" - a property of the
    message rather than of the emitter - and the order is shard order, not
    completion order, so the emitted account is deterministic.

    **Logged by nobody here**: ``app/cli.py`` emits one record per reason
    before whichever exit class it returns, and a second emitter would put one
    incident in the CI console twice under two logger names.  Absence alone
    would be satisfied by a service that lost the reasons altogether, so the
    presence on the outcome is asserted in the same test.
    """
    spawn = RecordingSpawn(
        base=feature_tree, behaviour={0: NO_FILE, 1: EMPTY_FILE, 2: INVALID_JSON}
    )

    with caplog.at_level(logging.DEBUG, logger=service.__name__):
        outcome = service.run_suite(workers=3, base=feature_tree, spawn=spawn)

    assert outcome.result_set is None
    assert outcome.merge_produced_nothing is True
    assert outcome.worker_count == 3

    # One attributed reason per shard, in shard order.
    by_index = {result.plan.index: result for result in outcome.shard_results}
    assert sorted(by_index) == [0, 1, 2]
    for index in sorted(by_index):
        result = by_index[index]
        assert result.dead is True
        assert result.reason is not None
        assert f"shard {index}" in result.reason
        assert f"{len(result.plan.locations)} scenario(s)" in result.reason
        # The locations are in the reason too, which is what makes a dead
        # shard's scenarios findable without tracing the run.
        assert result.plan.locations[0] in result.reason
    assert outcome.dead_shards == tuple(
        by_index[index].reason for index in sorted(by_index)
    )

    # Three distinct deaths, so the reasons are not one message repeated.
    assert len(set(outcome.dead_shards)) == 3

    emitted = "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name == service.__name__
    )
    for reason in outcome.dead_shards:
        assert reason not in emitted
    service_errors = [
        record.getMessage()
        for record in caplog.records
        if record.name == service.__name__ and record.levelno >= logging.ERROR
    ]
    assert service_errors == [], service_errors


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

    # The absent feature is not executed, and the drop is reported where an
    # operator reads it - by the entry's position rather than by echoing the
    # manifest's text, which is untrusted input bound for a console record.
    assert missing_path not in {unit.feature_path for unit in selected}
    reported = [
        record.getMessage()
        for record in caplog.records
        if record.name == RERUN_REPORT_LOGGER_NAME
        and record.levelno >= logging.WARNING
    ]
    assert any("Dropping entry 1" in message for message in reported), reported
    assert not any("Vanished.feature" in message for message in reported), reported

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


# --------------------------------------------------------------------------- #
# Path safety of the rerun selection (CWE-22, CWE-367)
#
# A rerun takes its scenarios from a file in the CI workspace, so its entries
# are machine input from outside the suite and every one of them names a file
# the engine will open by name.  The selection therefore has to be decided on
# a verified object exactly as the ordinary one is - and with one rule the
# ordinary path does not have: a recorded failure is never silently discarded,
# so an entry whose file cannot be read this instant is reported and kept
# rather than dropped.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("linker", [os.symlink, os.link])
def test_a_manifest_entry_that_is_a_link_is_not_selected(
    feature_tree: Path,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    linker: Any,
) -> None:
    """A linked or hard-linked entry is refused, and the manifest survives it.

    Either link makes the entry a file outside the features directory, so
    whoever can write the outside name chooses what a rerun executes.  The
    refusal belongs to the manifest's own owner - it is the confinement tier
    of ``app/reporting/rerun_report.py``, which drops the entry and warns from
    there - so what is asserted here is the property this service is
    responsible for: the outside feature reaches no location, the drop is
    reported where an operator reads it, and the real failures named by the
    same manifest are still selected.
    """
    outside = write_outside_feature(tmp_path)
    link_or_skip(
        linker, outside, paths.features_dir(feature_tree) / OUTSIDE_FEATURE.filename
    )
    survivor_line = min(ALPHA_LOCATION_LINES)
    write_manifest(
        feature_tree,
        manifest_line(OUTSIDE_FEATURE.feature_path, OUTSIDE_LOCATION_LINE)
        + manifest_line(ALPHA_FEATURE.feature_path, survivor_line),
    )

    with caplog.at_level(logging.WARNING, logger=RERUN_REPORT_LOGGER_NAME):
        selected, problems = service.select_rerun_scenarios(base=feature_tree)

    assert OUTSIDE_FEATURE.feature_path not in {unit.feature_path for unit in selected}
    assert [unit.location for unit in selected] == [
        f"{ALPHA_FEATURE.feature_path}{rerun_report.LINE_SEPARATOR}{survivor_line}"
    ]
    assert problems == []
    reported = [
        record.getMessage()
        for record in caplog.records
        if record.name == RERUN_REPORT_LOGGER_NAME
        and record.levelno >= logging.WARNING
    ]
    # The drop is reported, and reported *without* the manifest's own text:
    # an entry a manifest supplied is untrusted input bound for stderr and a
    # CI console record, so the owner names the entry's position and the
    # features directory instead of echoing the path (CWE-532, CWE-117).
    assert any("Dropping entry 1" in message for message in reported), reported
    assert not any(OUTSIDE_FEATURE.filename in message for message in reported), (
        reported
    )


def test_a_manifest_entry_whose_feature_cannot_be_read_is_dropped(
    feature_tree: Path,
) -> None:
    """An unverifiable feature contributes no location, and says so.

    The one case in which a rerun does drop a recorded failure, and the reason
    it has to.  "A location is kept rather than dropped" is the rule for a
    *different* condition - the feature verified and simply no longer declares
    a scenario at that line, which the test below covers - and it cannot be
    stretched to this one.  Here the port has already refused the entry: its
    contents could not be read from a verified descriptor.  Retaining the
    location would hand the engine the pathname that was refused, and the
    engine opens a pathname by name, so the refusal made here would be undone
    there and whatever now stands at that path would execute under the refused
    entry's spelling (CWE-22).

    What the report loses is the *identity* of the failure, which is why the
    problem names the manifest and the entry's position: an operator can see
    that entry 1 was refused, and the reason is on stderr.  What it does not
    lose is any other feature's failures.
    """
    undecodable = paths.features_dir(feature_tree) / "Undecodable.feature"
    undecodable.write_bytes(b"Feature: \xff\xfe not utf-8\n")
    feature_path = f"{paths.NORMALIZED_FEATURES_PREFIX}{undecodable.name}"
    write_manifest(feature_tree, manifest_line(feature_path, OUTSIDE_LOCATION_LINE))

    selected, problems = service.select_rerun_scenarios(base=feature_tree)

    assert selected == []
    assert len(problems) == 1
    assert "entry 1" in problems[0]
    assert str(paths.rerun_txt_path(feature_tree)) in problems[0]
    # The manifest's own path text is not echoed back: the position locates
    # the entry and carries nothing from the file.
    assert undecodable.name not in problems[0]


def test_a_rerun_feature_replaced_after_selection_is_dropped_and_still_exits_zero(
    feature_tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rerun gets the same hand-off check, and the same tolerated outcome.

    The rerun path reads its features through the same verified reader and
    records the same identities, so a file swapped the instant it was read is
    detected here too: the swapped feature's locations are dropped and named,
    the manifest's other failures are still re-run, and the run keeps every
    property of its own row of AAP 0.4.1 - no artifact is published, the
    ``None`` document is not the empty-merge condition, and no shard is dead.
    """
    swapped = swap_on_read(
        monkeypatch, ALPHA_FEATURE.feature_path, ALPHA_FEATURE.filename
    )
    surviving_line = BETA_LOCATION_LINES[0]
    write_manifest(
        feature_tree,
        manifest_line(ALPHA_FEATURE.feature_path, *ALPHA_LOCATION_LINES[:2])
        + manifest_line(BETA_FEATURE.feature_path, surviving_line),
    )

    spawn = RecordingSpawn(base=feature_tree)
    outcome = service.run_suite(rerun=True, workers=2, base=feature_tree, spawn=spawn)

    assert swapped == [ALPHA_FEATURE.feature_path], "the swap never happened"
    launched = sorted(
        itertools.chain.from_iterable(call.locations for call in spawn.calls)
    )
    assert launched == [
        f"{BETA_FEATURE.feature_path}{rerun_report.LINE_SEPARATOR}{surviving_line}"
    ]
    assert outcome.selected_count == 1
    assert outcome.dead_shards == ()
    assert outcome.result_set is None
    assert outcome.merge_produced_nothing is False
    assert [
        message
        for message in outcome.parse_errors
        if message.startswith(ALPHA_FEATURE.feature_path)
    ] != []


def test_a_rerun_entry_that_cannot_be_verified_reaches_no_worker(
    feature_tree: Path,
) -> None:
    """The refusal holds at the hand-off, not only in the selection.

    The half of the confinement a selection assertion alone does not settle.
    An entry refused by the verified reader must not appear in a worker's
    argv, because the engine opens a location **by name** in another process:
    a location retained after the port refused its file would have the port
    refusing an entry on one side and executing whatever stands at that path
    on the other, which is the fail-open this drop removes (CWE-22).

    The other feature named by the same manifest is still re-run, so the drop
    costs exactly the refused entry, and the run keeps its own row of the AAP
    0.4.1 exit table: status 0, no artifact, no dead shard.
    """
    undecodable = paths.features_dir(feature_tree) / "Undecodable.feature"
    undecodable.write_bytes(b"Feature: \xff\xfe not utf-8\n")
    refused_path = f"{paths.NORMALIZED_FEATURES_PREFIX}{undecodable.name}"
    surviving_line = BETA_LOCATION_LINES[0]
    write_manifest(
        feature_tree,
        manifest_line(refused_path, 3)
        + manifest_line(BETA_FEATURE.feature_path, surviving_line),
    )

    spawn = RecordingSpawn(base=feature_tree)
    outcome = service.run_suite(rerun=True, workers=2, base=feature_tree, spawn=spawn)

    launched = sorted(
        itertools.chain.from_iterable(call.locations for call in spawn.calls)
    )
    assert launched == [
        f"{BETA_FEATURE.feature_path}{rerun_report.LINE_SEPARATOR}{surviving_line}"
    ]
    assert refused_path not in " ".join(launched)
    assert outcome.selected_count == 1
    assert outcome.dead_shards == ()
    assert [message for message in outcome.parse_errors if "entry 1" in message] != []


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

#: The recorder labels that carry the *order* of the teardown steps in a
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
    legitimately is - yields ``None`` rather than a placeholder, so the
    suppression record reads exactly as one carrying no identity at all.
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


# --------------------------------------------------------------------------- #
# OS-level containment of a worker's process tree
#
# A worker is the engine, the driver executable it starts, and the browser that
# executable starts -- and the browser holds the system under test's
# authenticated session.  Stopping the engine stops the engine, so cancellation
# has to reach the tree, and "we signalled it" has to become "we confirmed it
# stopped".
#
# The POSIX tests below start real process trees of their own; the Windows
# tests drive the same code through a fake ``kernel32``, which is the only way
# that platform's branches are reachable from this host.  Neither needs a
# browser, a driver binary or the engine.
#
# The topology the live trees reproduce is the production one, and the shape is
# the whole point:
#
#   supervisor  -- this process
#     worker    -- start_new_session=True, so it *leads* a session: its pid,
#                  its process-group id and its session id are one number
#       driver  -- process_group=0, so it leads a process group of its **own**
#                  inside that session, exactly as a Selenium driver does
#
# A test whose child merely inherits its parent's group cannot fail when the
# reclamation only signals the worker's group, so every live-tree assertion
# here states the pgids it measured and the session they share.
#
# Platform guarding is a requirement rather than a convenience: AAP 0.8 makes
# pytest a gate inside ``scripts/run_tests.ps1``, so this module is collected
# and run on Windows, where process groups, sessions, ``/proc`` and ``ps`` do
# not exist. Every test that touches one of those carries
# :data:`REQUIRES_PROCESS_TREES`; the faked-``kernel32`` tests carry no guard
# at all, because they are what gives the Windows branches their coverage on
# every platform.
# --------------------------------------------------------------------------- #


#: The process listing the live-tree tests measure a group's membership with,
#: read rather than inferred so a surviving child is counted after its leader
#: has gone.  An absolute program, so nothing on ``PATH`` can stand in for it.
PS_PROGRAM: Final[Path] = Path("/bin/ps")

#: Whether this platform offers everything the live-tree tests need: process
#: groups, sessions, group signalling and the listing above.
HAS_PROCESS_TREES: Final[bool] = all(
    hasattr(os, name) for name in ("getpgid", "getsid", "killpg", "setsid")
) and PS_PROGRAM.is_file()

#: Guard for every test that starts or inspects a live process tree, or that
#: replaces one of the POSIX calls above.
REQUIRES_PROCESS_TREES: Final[Any] = pytest.mark.skipif(
    not HAS_PROCESS_TREES,
    reason="process groups, sessions and /bin/ps are POSIX facilities, and "
    "this suite is also the Windows gate (AAP 0.8)",
)

#: Guard for a test asserting what a *non*-Windows host does with the Win32
#: seam, which on Windows would load the real library and answer differently.
REQUIRES_POSIX_PLATFORM: Final[Any] = pytest.mark.skipif(
    os.name == "nt", reason="asserts the POSIX side of the Win32 platform guard"
)

#: How long a spawned tree gets to appear before a test inspects it.
TREE_SETTLE_SECONDS: Final[float] = 1.0

#: How long a test waits after a release before re-reading the group, covering
#: the moment between the module's last poll and the kernel reaping the last
#: member.
TREE_REAP_SECONDS: Final[float] = 0.3

#: Grace the containment tests allow, in place of the module's default.  Short
#: because two of them deliberately exercise a tree that will not go quietly.
TEST_GRACE_SECONDS: Final[float] = 1.0

#: An opaque value standing in for a Win32 job handle.
FAKE_JOB_HANDLE: Final[int] = 0x3C

#: ``WAIT_TIMEOUT`` -- a zero-timeout wait that expired, which for a process
#: handle means the process is still running.  Spelled here as the tests'
#: own value so an assertion cannot be satisfied by whatever the module
#: happens to define.
WAIT_TIMEOUT: Final[int] = 0x00000102

#: ``WAIT_OBJECT_0`` -- the handle signalled, which for a process means it
#: exited.
WAIT_OBJECT_0: Final[int] = 0x00000000

#: ``WAIT_FAILED`` -- the wait itself failed and observed nothing.
WAIT_FAILED: Final[int] = 0xFFFFFFFF

#: ``ERROR_INVALID_PARAMETER`` -- the one ``OpenProcess`` failure meaning the
#: process id does not exist.
ERROR_INVALID_PARAMETER: Final[int] = 87

#: ``ERROR_ACCESS_DENIED`` -- a process that exists and is somebody else's.
ERROR_ACCESS_DENIED: Final[int] = 5

#: ``SYNCHRONIZE`` -- the only access right the liveness probe may ask for.
SYNCHRONIZE_ACCESS: Final[int] = 0x00100000

def _raiser(error: type[BaseException]) -> Any:
    """Build a stand-in whose every call raises ``error``.

    Used where the contract is how the module *interprets* a failure of an
    operating-system call, which cannot be provoked reliably on a live process.

    :param error: The exception class to raise.
    :returns: A callable accepting any arguments and raising that class.
    """

    def raise_it(*args: Any, **kwargs: Any) -> None:
        raise error("refused")

    return raise_it


def process_table() -> tuple[tuple[int, int, int], ...]:
    """Return every live process as ``(pid, process group, session)``.

    Measured with the process listing rather than with the subject's own
    ``/proc`` reader: a test that measured through the code under test could
    not distinguish a reclamation from a reader that answers "empty" for the
    wrong reason.

    The whole table is read and filtered here rather than asking ``ps`` to
    select, because ``ps -g`` selects by **session** on this platform, which
    is the one distinction these tests exist to make.

    :returns: One triple per live process, in the listing's order.
    """
    listing = subprocess.run(
        [str(PS_PROGRAM), "-e", "-o", "pid=,pgid=,sess="],
        capture_output=True,
        text=True,
        check=False,
    )
    rows: list[tuple[int, int, int]] = []

    for line in listing.stdout.splitlines():
        fields = line.split()

        if len(fields) == 3 and all(field.isdigit() for field in fields):
            rows.append((int(fields[0]), int(fields[1]), int(fields[2])))

    return tuple(rows)


def group_members(group: int) -> tuple[int, ...]:
    """Return the live process ids of one process group.

    :param group: The group id to list.
    :returns: The pids still in the group, ascending; empty once it has gone.
    """
    return tuple(sorted(pid for pid, pgid, _ in process_table() if pgid == group))


def session_members(session: int) -> tuple[int, ...]:
    """Return the live process ids of one session.

    :param session: The session id to list.
    :returns: The pids still in the session, ascending; empty once it has gone.
    """
    return tuple(sorted(pid for pid, _, sid in process_table() if sid == session))


def process_is_alive(pid: int) -> bool:
    """Return whether one process id still resolves to a live process.

    :param pid: The process id to probe.
    :returns: ``True`` while the process exists, signal ``0`` being the probe
        that performs the existence check and delivers nothing.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


#: A child that ignores ``SIGTERM``, so the escalation path is real rather than
#: simulated.  Its parent sleeps alongside it.
STUBBORN_CHILD_SOURCE: Final[str] = (
    "import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(300)"
)

#: A child that exits on ``SIGTERM`` like anything ordinary.
ORDINARY_CHILD_SOURCE: Final[str] = "import time\ntime.sleep(300)"


class WorkerTree:
    """A real worker-shaped process tree, launched the way a worker is.

    The leader is started through :func:`test_run_service._process_group_keywords`
    -- the module's own isolation request, not a copy of it -- and forks one
    child. That shape is the point: a ``terminate()`` of the leader alone
    leaves the child running, which is the failure containment exists for.

    :param child_source: The program the child runs, which decides whether the
        tree goes quietly.
    """

    __slots__ = ("process",)

    def __init__(self, child_source: str = ORDINARY_CHILD_SOURCE) -> None:
        launcher = (
            "import subprocess, sys, time\n"
            f"subprocess.Popen([sys.executable, '-c', {child_source!r}])\n"
            "time.sleep(300)\n"
        )
        self.process = subprocess.Popen(
            [sys.executable, "-c", launcher],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **service._process_group_keywords(),
        )
        time.sleep(TREE_SETTLE_SECONDS)

    @property
    def process_group(self) -> int:
        """The group every process of the tree belongs to.

        :returns: The group id, read live while the leader is alive.
        """
        return os.getpgid(self.process.pid)

    @property
    def session(self) -> int:
        """The session the whole tree belongs to.

        :returns: The session id, read live while the leader is alive, which
            for a session leader is also its pid and its group id.
        """
        return os.getsid(self.process.pid)

    def member_count(self, group: int) -> int:
        """How many processes are still in one group.

        Read from the group rather than from the leader, so a surviving child
        is counted after the leader has gone.  This tree's child inherits the
        leader's group, so the count covers the whole of it.

        :param group: The group id to count.
        :returns: The number of live members.
        """
        return len(group_members(group))

    def destroy(self, group: int) -> None:
        """Ensure nothing of the tree outlives the test.

        :param group: The group id captured while the leader was alive.
        :returns: ``None``.
        """
        # Best effort by design: the tree may already be gone - the test under
        # way is often the thing that removed it - and a cleanup that raised
        # would replace the test's own result with a teardown error.
        try:
            os.killpg(group, signal.SIGKILL)
        except OSError:
            pass

        try:
            self.process.wait(timeout=TREE_SETTLE_SECONDS)
        except (OSError, subprocess.TimeoutExpired, ValueError):
            pass

        service._forget_worker(self.process)


#: A grandchild that ignores ``SIGTERM``, standing in for a browser that does
#: not close when its driver is asked politely.
STUBBORN_GRANDCHILD_SOURCE: Final[str] = (
    "import signal, time\n"
    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "time.sleep(300)\n"
)

#: An ordinary grandchild, which outlives its parent but not a signal.
ORDINARY_GRANDCHILD_SOURCE: Final[str] = "import time\ntime.sleep(300)\n"


class NestedGroupTree:
    """A worker whose child leads a process group of its own in its session.

    The production topology, reproduced with no browser in it. The leader is
    started through :func:`test_run_service._process_group_keywords` -- the
    module's own isolation request -- so it leads a session; it then starts a
    grandchild with ``process_group=0``, which is what a driver launch does,
    so that grandchild is in a group of its **own** while staying in the
    worker's session.

    That distinction is the one a test has to make. Signalling the worker's
    group reaches the worker and nothing nested inside its session, so a tree
    whose child merely inherited the worker's group cannot tell a reclamation
    that reaches the session from one that does not.

    The leader exits on request rather than on a timer
    (:meth:`stop_leader`), so a test can register the worker while it is
    certainly alive - which is the production contract for capturing the
    session - and only then reproduce the state where the leader has gone and
    the nested group has not.

    :param grandchild_source: The program the grandchild runs.
    """

    __slots__ = ("grandchild", "process")

    def __init__(self, grandchild_source: str = ORDINARY_GRANDCHILD_SOURCE) -> None:
        launcher = (
            "import subprocess, sys\n"
            "child = subprocess.Popen(\n"
            f"    [sys.executable, '-c', {grandchild_source!r}], process_group=0\n"
            ")\n"
            "print(child.pid, flush=True)\n"
            # Blocks until the test asks for the exit, or until a signal
            # arrives, or until this process goes away and the pipe closes.
            "sys.stdin.readline()\n"
        )
        self.process = subprocess.Popen(
            [sys.executable, "-c", launcher],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            **service._process_group_keywords(),
        )

        assert self.process.stdout is not None

        #: The pid of the process in its own group inside the session.
        self.grandchild = int(self.process.stdout.readline().strip())

        time.sleep(TREE_SETTLE_SECONDS)

    def stop_leader(self) -> None:
        """Let the leader exit, leaving the nested group running.

        :returns: ``None``.  Returns once the leader has exited and been
            waited for, which is also what makes its group id unresolvable.
        """
        assert self.process.stdin is not None

        self.process.stdin.write("\n")
        self.process.stdin.flush()
        self.process.stdin.close()
        self.process.wait(timeout=TREE_SETTLE_SECONDS)

    @property
    def process_group(self) -> int:
        """The worker's own process group.

        :returns: The group id, which for a session leader is also its pid.
        """
        return os.getpgid(self.process.pid)

    @property
    def session(self) -> int:
        """The session the whole tree shares.

        :returns: The session id, read while the leader is alive.
        """
        return os.getsid(self.process.pid)

    @property
    def grandchild_group(self) -> int:
        """The group the nested process leads.

        :returns: Its group id, which is its own pid.
        """
        return os.getpgid(self.grandchild)

    @property
    def grandchild_session(self) -> int:
        """The session the nested process belongs to.

        :returns: Its session id, which must be the worker's.
        """
        return os.getsid(self.grandchild)

    def destroy(self, groups: Sequence[int]) -> None:
        """Ensure nothing of the tree outlives the test.

        :param groups: The group ids captured while the tree was alive.
        :returns: ``None``.
        """
        for group in groups:
            # Best effort by design: the test under way is normally what
            # removed the group, and a teardown that raised would replace the
            # test's own result.
            try:
                os.killpg(group, signal.SIGKILL)
            except OSError:
                pass

        for stream in (self.process.stdin, self.process.stdout):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass

        try:
            self.process.wait(timeout=TREE_SETTLE_SECONDS)
        except (OSError, subprocess.TimeoutExpired, ValueError):
            pass

        service._forget_worker(self.process)


class FakeKernel32:
    """A recording stand-in for the Win32 ``kernel32`` binding.

    Only the five entry points the module calls are implemented, each
    returning a value the module has to interpret, so a test asserts the call
    *sequence* rather than only the outcome.

    :param create: What ``CreateJobObjectW`` returns; ``0`` is failure.
    :param limit: What ``SetInformationJobObject`` returns; ``0`` is failure.
    :param open_process: What ``OpenProcess`` returns; ``0`` is failure.
    :param assign: What ``AssignProcessToJobObject`` returns; ``0`` is failure.
    :param wait: What ``WaitForSingleObject`` returns; ``WAIT_TIMEOUT`` means
        the process is still running.
    """

    __slots__ = ("_assign", "_create", "_limit", "_open", "_wait", "calls")

    def __init__(
        self,
        *,
        create: int = FAKE_JOB_HANDLE,
        limit: int = 1,
        open_process: int = 0x77,
        assign: int = 1,
        wait: int = WAIT_TIMEOUT,
    ) -> None:
        self._create = create
        self._limit = limit
        self._open = open_process
        self._assign = assign
        self._wait = wait

        #: Event names in call order, with what each acted on.
        self.calls: list[tuple[str, Any]] = []

    def CreateJobObjectW(self, attributes: Any, name: Any) -> int:
        """Record the creation and return the programmed handle."""
        self.calls.append(("CreateJobObjectW", name))
        return self._create

    def SetInformationJobObject(
        self, job: int, info_class: int, info: Any, length: int
    ) -> int:
        """Record the limit call and return the programmed result."""
        self.calls.append(("SetInformationJobObject", (job, info_class)))
        return self._limit

    def OpenProcess(self, access: int, inherit: bool, pid: int) -> int:
        """Record the open and return the programmed handle."""
        self.calls.append(("OpenProcess", (access, pid)))
        return self._open

    def AssignProcessToJobObject(self, job: int, process: int) -> int:
        """Record the assignment and return the programmed result."""
        self.calls.append(("AssignProcessToJobObject", (job, process)))
        return self._assign

    def WaitForSingleObject(self, handle: int, milliseconds: int) -> int:
        """Record the wait and return the programmed result."""
        self.calls.append(("WaitForSingleObject", (handle, milliseconds)))
        return self._wait

    def CloseHandle(self, handle: int) -> int:
        """Record the close and report success."""
        self.calls.append(("CloseHandle", handle))
        return 1

    def targets(self) -> tuple[str, ...]:
        """The recorded call names, in order.

        :returns: The sequence of entry points called.
        """
        return tuple(name for name, _ in self.calls)


class WorkerProcessDouble:
    """A worker process exposing only what containment reads from one.

    :param pid: The process id to report.
    :param outcome: What ``wait`` does - an exception class to raise, or
        ``None`` to return cleanly.
    """

    __slots__ = ("_outcome", "pid", "waits")

    def __init__(self, pid: int = 8765, outcome: type[BaseException] | None = None) -> None:
        self.pid = pid
        self._outcome = outcome

        #: Every timeout ``wait`` was called with, in order.
        self.waits: list[float | None] = []

    def wait(self, timeout: float | None = None) -> int:
        """Record the wait and produce the programmed outcome.

        :param timeout: The bound the caller allowed.
        :returns: ``0`` for a clean return.
        :raises BaseException: The programmed outcome, when one was given.
        """
        self.waits.append(timeout)

        if self._outcome is subprocess.TimeoutExpired:
            raise subprocess.TimeoutExpired(cmd="worker", timeout=timeout or 0)

        if self._outcome is not None:
            raise self._outcome("wait failed")

        return 0


@pytest.fixture
def worker_tree() -> Iterator[WorkerTree]:
    """Start a real worker-shaped tree and guarantee its removal.

    :yields: The tree, settled and registered nowhere yet.
    """
    tree = WorkerTree()
    group = tree.process_group

    try:
        yield tree
    finally:
        tree.destroy(group)


@pytest.fixture
def stubborn_worker_tree() -> Iterator[WorkerTree]:
    """Start a tree whose child ignores ``SIGTERM``.

    :yields: The tree, settled.
    """
    tree = WorkerTree(STUBBORN_CHILD_SOURCE)
    group = tree.process_group

    try:
        yield tree
    finally:
        tree.destroy(group)


@pytest.fixture
def nested_group_tree() -> Iterator[NestedGroupTree]:
    """Start a tree shaped like a worker with a driver, and remove it.

    :yields: The tree, settled, with its nested group alive and registered
        nowhere yet.
    """
    tree = NestedGroupTree()
    groups = (tree.process_group, tree.grandchild_group)

    try:
        yield tree
    finally:
        tree.destroy(groups)


@pytest.fixture
def stubborn_nested_group_tree() -> Iterator[NestedGroupTree]:
    """Start the same tree with a grandchild that ignores ``SIGTERM``.

    :yields: The tree, settled.
    """
    tree = NestedGroupTree(STUBBORN_GRANDCHILD_SOURCE)
    groups = (tree.process_group, tree.grandchild_group)

    try:
        yield tree
    finally:
        tree.destroy(groups)


@pytest.fixture
def windows_worker_platform(monkeypatch: pytest.MonkeyPatch) -> FakeKernel32:
    """Present the module with a Windows platform and a fake ``kernel32``.

    Both halves are needed and neither is sufficient: the mechanism is chosen
    from ``_HAS_PROCESS_GROUPS`` and Win32 is reached only through
    ``_kernel32``.

    :param monkeypatch: pytest's patcher.
    :returns: The fake library the Windows branches will call.
    """
    library = FakeKernel32()

    monkeypatch.setattr(service, "_HAS_PROCESS_GROUPS", False)
    monkeypatch.setattr(service, "_kernel32", lambda: library)

    return library


@REQUIRES_PROCESS_TREES
def test_registering_a_worker_captures_its_process_group(
    worker_tree: WorkerTree,
) -> None:
    """Pin capture as happening at launch, while the leader is alive.

    That is the only safe moment: once a worker has exited and been waited
    for, neither its group nor its session can be looked up any more, and a
    later lookup would either fail or resolve a recycled id. Cancellation
    therefore depends on this record existing from the moment the worker does.

    Both ids are captured, because each reaches something the other cannot:
    the group is how the worker itself is signalled, and the session is how a
    driver in a process group of its own is found at all. For a worker started
    as a session leader the two are the same number, and that is asserted
    rather than assumed - it is what makes the session id usable as the
    worker's own group id everywhere else.
    """
    group = worker_tree.process_group
    session = worker_tree.session

    service._register_worker(worker_tree.process)

    containment = service._worker_containment[worker_tree.process.pid]

    assert containment.pid == worker_tree.process.pid
    assert containment.process_group == group
    assert containment.session == session
    assert containment.session == worker_tree.process.pid
    assert containment.job_handle is None


@REQUIRES_PROCESS_TREES
def test_a_live_worker_tree_is_not_reported_as_empty(worker_tree: WorkerTree) -> None:
    """Pin the sense of the emptiness check, which is easy to invert.

    ``killpg(pgid, 0)`` performs the existence check and delivers nothing, so
    it **succeeding** means at least one process is still in the group. Read
    the other way round, every live tree would be reported as already stopped.
    """
    containment = service._capture_containment(worker_tree.process)

    assert worker_tree.member_count(worker_tree.process_group) == 2
    assert service._worker_tree_is_empty(containment) is False


@REQUIRES_PROCESS_TREES
def test_an_unanswerable_worker_tree_is_reported_as_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the three answers of the group probe apart: gone, there, unknown.

    An unknown is not a clean result. A record with no group at all cannot
    answer, and an error that is neither "no such group" nor "not permitted"
    is an unknown too - both are reported as unverified rather than passed
    over, because the alternative is claiming a release nobody observed.
    """
    assert service._process_group_is_empty(None) is None

    monkeypatch.setattr(service.os, "killpg", _raiser(ProcessLookupError))

    assert service._process_group_is_empty(4242) is True

    monkeypatch.setattr(service.os, "killpg", _raiser(PermissionError))

    assert service._process_group_is_empty(4242) is False

    monkeypatch.setattr(service.os, "killpg", _raiser(OSError))

    assert service._process_group_is_empty(4242) is None

    # And a record carrying no dimension at all answers the same way, which is
    # what keeps "nothing was captured" out of the clean column.
    assert service._worker_tree_is_empty(service._WorkerContainment(4242, None, None)) is None


@REQUIRES_PROCESS_TREES
def test_cancelling_a_worker_stops_its_whole_tree_and_confirms_it(
    worker_tree: WorkerTree,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Pin cancellation against a real tree, which is the finding's own case.

    The leader and its child are both alive. Signalling the *group* reaches
    both, where ``terminate()`` on the leader would leave the child running,
    and the verification that follows is what turns the signal into a
    confirmed result. Nothing is reported at ``ERROR``, because nothing
    survived.
    """
    group = worker_tree.process_group
    service._register_worker(worker_tree.process)

    assert worker_tree.member_count(group) == 2

    with caplog.at_level(logging.WARNING, logger=service.__name__):
        stopped = service._terminate_worker(
            worker_tree.process, grace_seconds=TEST_GRACE_SECONDS
        )

    time.sleep(TREE_REAP_SECONDS)

    assert stopped is True
    assert worker_tree.member_count(group) == 0
    assert [record for record in caplog.records if record.levelno >= logging.ERROR] == []


@REQUIRES_PROCESS_TREES
def test_a_descendant_that_ignores_the_request_is_escalated_and_reported(
    stubborn_worker_tree: WorkerTree,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Pin the escalation, and the gap that made it necessary.

    The worker exits on ``SIGTERM`` but its child ignores it - the exact shape
    of a browser outliving its engine. Treating the worker's own exit as
    success would end cancellation here with a live process in the group, so
    the polite path is verified too, the survivor is recorded at ``ERROR``,
    and the group is then killed and confirmed empty.
    """
    group = stubborn_worker_tree.process_group
    service._register_worker(stubborn_worker_tree.process)

    with caplog.at_level(logging.WARNING, logger=service.__name__):
        stopped = service._terminate_worker(
            stubborn_worker_tree.process, grace_seconds=TEST_GRACE_SECONDS
        )

    time.sleep(TREE_REAP_SECONDS)

    assert stopped is True
    assert stubborn_worker_tree.member_count(group) == 0
    assert [record.levelno for record in caplog.records if record.levelno >= logging.ERROR] == [
        logging.ERROR
    ]


@REQUIRES_PROCESS_TREES
def test_a_signalled_group_is_reached_through_the_captured_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the group id's source as the record, not a live lookup.

    After a worker has been waited for, ``os.getpgid`` can no longer resolve
    it - which is exactly when a browser still in its group has to be
    reached. The captured id is therefore authoritative, and the live lookup
    is only the fallback for a worker that was never registered.
    """
    signalled: list[tuple[int, int]] = []
    monkeypatch.setattr(
        service.os, "killpg", lambda group, number: signalled.append((group, number))
    )
    monkeypatch.setattr(service.os, "getpgid", _raiser(ProcessLookupError))

    process = WorkerProcessDouble(pid=5150)
    monkeypatch.setitem(service._worker_containment, 5150, service._WorkerContainment(5150, 9090, None))

    assert service._signal_worker_group(process, signal.SIGTERM) is True
    assert signalled == [(9090, signal.SIGTERM)]


@REQUIRES_PROCESS_TREES
def test_forgetting_a_worker_accounts_for_the_tree_it_left(
    worker_tree: WorkerTree,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Pin verification on the path where the worker finished by itself.

    A worker that exited normally can still have left a driver executable or
    a browser behind, and nothing else in the run would notice. The check is
    deliberately signal-free here - the leader has been reaped and its id
    could in principle be recycled, so nothing is killed on this path - but
    the survivor is reported, which is what makes the leak visible.
    """
    group = worker_tree.process_group
    service._register_worker(worker_tree.process)

    with caplog.at_level(logging.WARNING, logger=service.__name__):
        service._forget_worker(worker_tree.process)

    surviving = [record for record in caplog.records if record.levelno >= logging.ERROR]

    assert len(surviving) == 1
    assert str(group) in surviving[0].getMessage()
    assert worker_tree.process.pid not in service._live_workers
    assert worker_tree.process.pid not in service._worker_containment


@REQUIRES_PROCESS_TREES
def test_forgetting_a_worker_twice_is_silent_the_second_time(
    worker_tree: WorkerTree,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Pin the idempotence the two cleanup paths rely on.

    The owning thread forgets its worker in a ``finally`` and
    ``terminate_live_workers`` forgets whatever it stopped, so the same worker
    is routinely dropped twice - and the second drop must neither raise nor
    repeat the diagnosis of the first.
    """
    service._register_worker(worker_tree.process)
    service._forget_worker(worker_tree.process)

    # Cleared deliberately: ``caplog`` accumulates over the whole test, so the
    # first drop's diagnosis would otherwise satisfy the assertion below and
    # the second drop's silence would never be checked at all.
    caplog.clear()

    with caplog.at_level(logging.WARNING, logger=service.__name__):
        service._forget_worker(worker_tree.process)

    assert caplog.records == []


@REQUIRES_PROCESS_TREES
def test_a_worker_tree_confirmed_gone_is_reported_silently(
    worker_tree: WorkerTree,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Pin the clean case as silent - ordinary completion is not a diagnostic.

    Every worker of every run passes through this path, so a record here would
    put one line per shard into a CI log for nothing.

    Both dimensions of the record are present, because silence is what a
    *fully* confirmed release earns: a record whose session was never captured
    is a record whose driver groups nobody could look for, and that is a
    warning rather than a pass.
    """
    group = worker_tree.process_group
    session = worker_tree.session
    worker_tree.destroy(group)

    containment = service._WorkerContainment(
        worker_tree.process.pid, group, None, session
    )

    with caplog.at_level(logging.DEBUG, logger=service.__name__):
        service._report_surviving_tree(containment, service._worker_tree_is_empty(containment))

    assert caplog.records == []


def test_an_unverifiable_worker_tree_is_a_warning_not_an_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Pin the distinction between known-bad and unknown.

    A tree still running is an ``ERROR`` - there is a browser holding a
    session. A tree that could not be inspected is a ``WARNING``: the state is
    unknown, and reporting it as known-bad would train an operator to ignore
    the level that matters.
    """
    containment = service._WorkerContainment(4242, None, None)

    with caplog.at_level(logging.DEBUG, logger=service.__name__):
        service._report_surviving_tree(containment, None)

    assert [record.levelno for record in caplog.records] == [logging.WARNING]


def test_a_windows_worker_is_contained_in_a_kill_on_close_job(
    windows_worker_platform: FakeKernel32,
) -> None:
    """Pin the Windows mechanism and the order of its five calls.

    A job limited with ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` is the only
    Windows facility that terminates a tree, and children inherit it, so the
    browser is in the job too. The process handle is opened with exactly the
    two rights the assignment needs and closed again whatever the outcome.
    """
    containment = service._capture_containment(WorkerProcessDouble(pid=616))

    assert containment.process_group is None
    assert containment.job_handle == FAKE_JOB_HANDLE
    assert windows_worker_platform.targets() == (
        "CreateJobObjectW",
        "SetInformationJobObject",
        "OpenProcess",
        "AssignProcessToJobObject",
        "CloseHandle",
    )

    opened = next(
        value for name, value in windows_worker_platform.calls if name == "OpenProcess"
    )

    assert opened == (service._PROCESS_ASSIGN_ACCESS, 616)


@pytest.mark.parametrize(
    ("failure", "expected_calls"),
    [
        ({"create": 0}, ("CreateJobObjectW",)),
        ({"limit": 0}, ("CreateJobObjectW", "SetInformationJobObject", "CloseHandle")),
        (
            {"open_process": 0},
            ("CreateJobObjectW", "SetInformationJobObject", "OpenProcess", "CloseHandle"),
        ),
        (
            {"assign": 0},
            (
                "CreateJobObjectW",
                "SetInformationJobObject",
                "OpenProcess",
                "AssignProcessToJobObject",
                "CloseHandle",
                "CloseHandle",
            ),
        ),
    ],
)
def test_a_worker_job_that_cannot_be_established_leaks_no_handle(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure: dict[str, int],
    expected_calls: tuple[str, ...],
) -> None:
    """Pin every Win32 failure step: nothing leaks, and the gap is recorded.

    A job half-established is worse than none - its kill-on-close limit would
    be held against a process that was never assigned to it - so each path
    closes what it opened and reports ``None``, which the verification reads as
    "never contained".
    """
    library = FakeKernel32(**failure)

    monkeypatch.setattr(service, "_HAS_PROCESS_GROUPS", False)
    monkeypatch.setattr(service, "_kernel32", lambda: library)

    with caplog.at_level(logging.WARNING, logger=service.__name__):
        assert service._assign_kill_on_close_job(616) is None

    assert library.targets() == expected_calls
    assert [record.levelno for record in caplog.records] == [logging.WARNING]


def test_closing_a_windows_worker_job_terminates_its_tree_once(
    windows_worker_platform: FakeKernel32,
) -> None:
    """Pin the Windows reclamation and its idempotence.

    Closing the last handle terminates every process in the job, so the close
    *is* the reclamation. The record is removed first, which is what makes a
    second close a no-op instead of a double close of a handle Windows may
    already have reused.
    """
    process = WorkerProcessDouble(pid=616)
    service._register_worker(process)

    service._close_worker_job(616)
    service._close_worker_job(616)

    assert windows_worker_platform.calls[-1] == ("CloseHandle", FAKE_JOB_HANDLE)
    assert windows_worker_platform.targets().count("CloseHandle") == 2
    assert 616 not in service._worker_containment


def test_a_windows_worker_surviving_its_job_is_reported_as_unstopped(
    windows_worker_platform: FakeKernel32,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Pin the Windows verification: the wait after the close is the proof.

    A process still running after its kill-on-close job was closed is the one
    outcome that platform's containment cannot explain away, so it is an
    ``ERROR`` and an unconfirmed result rather than a silent success.
    """
    process = WorkerProcessDouble(pid=616, outcome=subprocess.TimeoutExpired)
    service._register_worker(process)

    with caplog.at_level(logging.WARNING, logger=service.__name__):
        assert service._verify_tree_stopped(process, grace_seconds=TEST_GRACE_SECONDS) is False

    assert [record.levelno for record in caplog.records if record.levelno >= logging.ERROR] == [
        logging.ERROR
    ]
    assert process.waits == [TEST_GRACE_SECONDS]


def test_verification_of_an_unregistered_worker_answers_unknown() -> None:
    """Pin the answer for a worker with no record at all.

    Nothing was captured, so nothing can be confirmed. ``None`` keeps that
    distinct from both "confirmed gone" and "still running", which is what
    lets the caller report an unknown as an unknown.
    """
    assert service._verify_tree_stopped(WorkerProcessDouble(pid=4242)) is None



@REQUIRES_POSIX_PLATFORM
def test_a_platform_claiming_windows_without_win32_is_reported(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Pin the Win32 seam's own failure: Windows said, ``WinDLL`` absent.

    Every other Windows test here stands a double in front of this function,
    so this is what exercises the real one. A platform that identifies as
    Windows and cannot supply the binding has no containment at all, which is
    reported rather than raised - a run must still start, and what changes is
    that its verification reports itself unconfirmed instead of claiming
    success.
    """
    # The real platform first, which is the guard's other side: off Windows the
    # answer is ``None`` with nothing loaded and nothing said.
    assert service._kernel32() is None
    assert caplog.records == []

    monkeypatch.setattr(service.os, "name", "nt")

    with caplog.at_level(logging.WARNING, logger=service.__name__):
        assert service._kernel32() is None

    assert [record.levelno for record in caplog.records] == [logging.WARNING]


def test_no_worker_job_is_attempted_without_a_win32_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the behaviour when Win32 itself is unavailable.

    Nothing is attempted and ``None`` is returned, which the verification
    reads as a worker that was never contained.
    """
    monkeypatch.setattr(service, "_kernel32", lambda: None)

    assert service._assign_kill_on_close_job(616) is None


def test_a_win32_error_while_building_a_worker_job_closes_it(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Pin the error path of the job builder, handle included.

    A Win32 call that raises rather than returning a failure code leaves a
    created job behind, and a job holding a kill-on-close limit against
    nothing is exactly what must not be leaked. One close, because the failure
    came before the process handle was opened.
    """

    class ExplodingKernel32(FakeKernel32):
        """A binding whose limit call raises instead of returning a code."""

        def SetInformationJobObject(
            self, job: int, info_class: int, info: Any, length: int
        ) -> int:
            """Record the attempt, then fail the way Win32 can."""
            self.calls.append(("SetInformationJobObject", job))
            raise OSError("win32 failure")

    library = ExplodingKernel32()
    monkeypatch.setattr(service, "_HAS_PROCESS_GROUPS", False)
    monkeypatch.setattr(service, "_kernel32", lambda: library)

    with caplog.at_level(logging.WARNING, logger=service.__name__):
        assert service._assign_kill_on_close_job(616) is None

    assert library.targets() == (
        "CreateJobObjectW",
        "SetInformationJobObject",
        "CloseHandle",
    )
    assert [record.exc_info is not None for record in caplog.records] == [True]


def test_a_worker_job_that_cannot_be_closed_is_reported(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Pin the close failing: recorded, and never raised into cancellation.

    This runs while a run is being cancelled, where an exception would replace
    what the cancellation was reporting - so the failure is recorded and the
    unconfirmed state is what the caller reads.
    """

    class UnclosableKernel32(FakeKernel32):
        """A binding whose ``CloseHandle`` raises."""

        def CloseHandle(self, handle: int) -> int:
            """Record the attempt, then fail."""
            self.calls.append(("CloseHandle", handle))
            raise OSError("win32 failure")

    library = UnclosableKernel32()
    monkeypatch.setattr(service, "_HAS_PROCESS_GROUPS", False)
    monkeypatch.setattr(service, "_kernel32", lambda: library)
    monkeypatch.setitem(
        service._worker_containment, 616, service._WorkerContainment(616, None, FAKE_JOB_HANDLE)
    )

    with caplog.at_level(logging.WARNING, logger=service.__name__):
        service._close_worker_job(616)

    assert [record.levelno for record in caplog.records] == [logging.WARNING]


def test_a_running_windows_process_is_observed_without_being_touched(
    windows_worker_platform: FakeKernel32,
) -> None:
    """Pin the Windows liveness probe: three calls, and no action among them.

    This probe decides whether a run directory found in the shared
    intermediates belongs to a run still writing into it, so on Windows it is
    what stands between one invocation's ``--clean`` and a concurrent
    invocation's results.  ``os.kill`` cannot be used there - every signal
    number but the two console events *terminates* the target - so the probe
    is an open, a zero-timeout wait and a close.

    ``SYNCHRONIZE`` alone is asked for: it permits the wait and nothing else,
    so the probe cannot terminate, read or requery the process even by
    mistake.  And the handle is closed on the way out, because this runs once
    per candidate directory per clean and a leaked handle per call would
    accumulate for the supervisor's whole life.
    """
    assert service._process_is_alive(616) is True

    assert windows_worker_platform.targets() == (
        "OpenProcess",
        "WaitForSingleObject",
        "CloseHandle",
    )
    assert windows_worker_platform.calls[0] == ("OpenProcess", (SYNCHRONIZE_ACCESS, 616))
    assert windows_worker_platform.calls[1] == ("WaitForSingleObject", (0x77, 0))
    assert windows_worker_platform.calls[2] == ("CloseHandle", 0x77)


def test_an_exited_windows_process_is_reported_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the one wait result that means the process is finished.

    A process handle signals when the process exits, so ``WAIT_OBJECT_0`` is
    the answer that releases the directory for cleaning - and the handle is
    still closed, since the probe opened it either way.
    """
    library = FakeKernel32(wait=WAIT_OBJECT_0)
    monkeypatch.setattr(service, "_HAS_PROCESS_GROUPS", False)
    monkeypatch.setattr(service, "_kernel32", lambda: library)

    assert service._process_is_alive(616) is False
    assert library.calls[-1] == ("CloseHandle", 0x77)


def test_a_windows_wait_that_fails_leaves_the_process_presumed_running(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Pin ``WAIT_FAILED`` as an unknown rather than as "not running".

    The finding this closes is a single equality test against ``WAIT_TIMEOUT``:
    it makes every result that is not that one - ``WAIT_FAILED``,
    ``WAIT_ABANDONED`` - read as a finished process, and the consequence is a
    live sibling's intermediates deleted underneath it.  The direction of the
    fail-safe is the whole point, so it is asserted against the failure code
    itself rather than against a stand-in.
    """
    library = FakeKernel32(wait=WAIT_FAILED)
    monkeypatch.setattr(service, "_HAS_PROCESS_GROUPS", False)
    monkeypatch.setattr(service, "_kernel32", lambda: library)

    with caplog.at_level(logging.DEBUG, logger=service.__name__):
        assert service._process_is_alive(616) is True

    reported = [
        record for record in caplog.records if "the wait reported" in record.getMessage()
    ]

    assert [record.levelno for record in reported] == [logging.DEBUG]
    assert f"{WAIT_FAILED:#010X}"[2:] in reported[0].getMessage()
    assert library.calls[-1] == ("CloseHandle", 0x77)


def test_a_windows_process_that_will_not_open_is_told_apart_by_its_error_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the two meanings of a refused open, and that they differ.

    ``OpenProcess`` returning nothing is either a process id that does not
    exist or one this account may not observe, and only
    ``ERROR_INVALID_PARAMETER`` means the former.  An access denial describes a
    process that is very much alive, so reading every failure as "gone" would
    delete the intermediates of a run owned by another account.

    Nothing is waited on or closed in either case, because there is no handle
    to wait on or close.
    """
    monkeypatch.setattr(service, "_HAS_PROCESS_GROUPS", False)

    absent = FakeKernel32(open_process=0)
    monkeypatch.setattr(service, "_kernel32", lambda: absent)
    monkeypatch.setattr(service, "_win32_last_error", lambda module: ERROR_INVALID_PARAMETER)

    assert service._process_is_alive(616) is False
    assert absent.targets() == ("OpenProcess",)

    forbidden = FakeKernel32(open_process=0)
    monkeypatch.setattr(service, "_kernel32", lambda: forbidden)
    monkeypatch.setattr(service, "_win32_last_error", lambda module: ERROR_ACCESS_DENIED)

    assert service._process_is_alive(616) is True
    assert forbidden.targets() == ("OpenProcess",)


def test_the_windows_liveness_probe_declares_the_handle_returning_prototypes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the declaration, which is the defect this probe carried.

    Left undeclared, ctypes assumes a C ``int`` return and **truncates** the
    64-bit ``HANDLE`` ``OpenProcess`` gives back on 64-bit Windows: the wait
    then observes a handle the kernel never issued and the close reclaims
    nothing.  The probe's answer would be about no process at all, which on
    this path decides whether a running sibling's results are removed.

    The double's entry points are plain functions, so they accept the
    declaration and record it - which is what lets a non-Windows host assert
    that the prototypes were set, and set to the widths
    :func:`~app.services.test_run_service._win32_types` supplies.
    """
    handle_type, boolean, dword = service._win32_types(ctypes)

    class PrototypeRecordingKernel32:
        """A binding whose entry points remember how they were declared."""

        def __init__(self) -> None:
            def OpenProcess(access: int, inherit: bool, pid: int) -> int:
                return 0x77

            def WaitForSingleObject(handle: int, milliseconds: int) -> int:
                return WAIT_TIMEOUT

            def CloseHandle(handle: int) -> int:
                return 1

            self.OpenProcess = OpenProcess
            self.WaitForSingleObject = WaitForSingleObject
            self.CloseHandle = CloseHandle

    library = PrototypeRecordingKernel32()
    monkeypatch.setattr(service, "_HAS_PROCESS_GROUPS", False)
    monkeypatch.setattr(service, "_kernel32", lambda: library)

    assert service._process_is_alive(616) is True

    assert library.OpenProcess.restype is handle_type
    assert library.OpenProcess.argtypes == (dword, boolean, dword)
    assert library.WaitForSingleObject.restype is dword
    assert library.WaitForSingleObject.argtypes == (handle_type, dword)
    assert library.CloseHandle.restype is boolean
    assert library.CloseHandle.argtypes == (handle_type,)

    # The handle width is the finding: a declaration narrower than a pointer
    # is what truncates, so the type carried has to be pointer-sized.
    assert ctypes.sizeof(handle_type) == ctypes.sizeof(ctypes.c_void_p)


def test_a_failed_probe_handle_close_is_reported_and_not_raised(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Pin the probe's close failure as recorded, never propagated.

    The probe runs inside the clean step's directory walk, so an exception
    here would abort a clean over one unclosable handle.  The liveness answer
    the probe computed still stands, and the close failure is recorded beside
    it.
    """

    class UnclosableKernel32(FakeKernel32):
        """A binding whose ``CloseHandle`` reports failure."""

        def CloseHandle(self, handle: int) -> int:
            """Record the attempt and report failure rather than raising."""
            self.calls.append(("CloseHandle", handle))
            return 0

    library = UnclosableKernel32()
    monkeypatch.setattr(service, "_HAS_PROCESS_GROUPS", False)
    monkeypatch.setattr(service, "_kernel32", lambda: library)

    with caplog.at_level(logging.DEBUG, logger=service.__name__):
        assert service._process_is_alive(616) is True

    reported = [
        record
        for record in caplog.records
        if "Could not close the probe handle" in record.getMessage()
    ]

    assert [record.levelno for record in reported] == [logging.DEBUG]
    assert library.targets()[-1] == "CloseHandle"


def test_a_windows_worker_that_cannot_be_waited_for_is_unknown(
    windows_worker_platform: FakeKernel32,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Pin a wait that could not be performed as an unknown, not a success.

    Nothing was observed, and a release nobody observed is not a release - so
    the answer is ``None`` and the reason is recorded.
    """
    for outcome in (OSError, ValueError):
        process = WorkerProcessDouble(pid=616, outcome=outcome)
        service._register_worker(process)

        caplog.clear()

        with caplog.at_level(logging.WARNING, logger=service.__name__):
            assert service._verify_tree_stopped(process) is None

        assert [record.levelno for record in caplog.records] == [logging.WARNING]


def test_no_group_is_signalled_where_the_platform_has_none(
    windows_worker_platform: FakeKernel32,
) -> None:
    """Pin the platform guard on group signalling.

    Windows has no ``killpg``, so the answer is ``False`` and the caller falls
    back to the process itself - with the job, not the group, doing the
    containment there.
    """
    assert service._signal_worker_group(WorkerProcessDouble(pid=616), signal.SIGTERM) is False
    assert windows_worker_platform.calls == []


@REQUIRES_PROCESS_TREES
def test_a_group_that_can_no_longer_be_signalled_answers_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the fallback signal: a group that has gone is not an error.

    ``ProcessLookupError`` here means the worker and everything it started
    have already exited, which is the ordinary outcome of a second signal.
    The caller is told the group could not be signalled so it falls back to
    the process, and nothing is raised into the cancellation path.
    """
    monkeypatch.setattr(service.os, "killpg", _raiser(ProcessLookupError))
    monkeypatch.setattr(service.os, "getpgid", lambda pid: pid)

    assert service._signal_worker_group(WorkerProcessDouble(pid=616), signal.SIGTERM) is False


@REQUIRES_PROCESS_TREES
def test_stopping_a_worker_that_has_already_exited_does_nothing(
    worker_tree: WorkerTree,
) -> None:
    """Pin the guard that makes active signalling safe.

    Nothing is ever signalled for a process that has already exited, which is
    what guarantees a recycled process id can never be signalled by mistake -
    and it is why the normal-completion path verifies without signalling.
    """
    group = worker_tree.process_group
    worker_tree.destroy(group)

    assert service._terminate_worker(worker_tree.process) is False


def test_a_windows_worker_confirmed_stopped_answers_true(
    windows_worker_platform: FakeKernel32,
) -> None:
    """Pin the confirmed Windows outcome: job closed, process waited for.

    The close terminates the job's members and the wait is what observes that
    it took effect, so both having happened is the whole of the confirmation.
    """
    process = WorkerProcessDouble(pid=616)
    service._register_worker(process)

    assert service._verify_tree_stopped(process, grace_seconds=TEST_GRACE_SECONDS) is True
    assert windows_worker_platform.calls[-1] == ("CloseHandle", FAKE_JOB_HANDLE)
    assert process.waits == [TEST_GRACE_SECONDS]


@REQUIRES_PROCESS_TREES
def test_a_worker_that_ignores_the_request_is_killed_and_confirmed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Pin the escalation when the *worker itself* will not stop.

    The leader ignores ``SIGTERM`` and its child inherits that, so the polite
    request achieves nothing and the wait runs out. Cancellation must then
    escalate to a kill of the group and confirm the result - a run that
    reported cancellation with a live engine and browser in it would be the
    same defect at a different level.
    """
    launcher = (
        "import signal, subprocess, sys, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"subprocess.Popen([sys.executable, '-c', {STUBBORN_CHILD_SOURCE!r}])\n"
        "time.sleep(300)\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", launcher],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **service._process_group_keywords(),
    )
    time.sleep(TREE_SETTLE_SECONDS)
    group = os.getpgid(process.pid)
    service._register_worker(process)

    assert len(group_members(group)) == 2

    try:
        with caplog.at_level(logging.WARNING, logger=service.__name__):
            stopped = service._terminate_worker(process, grace_seconds=TEST_GRACE_SECONDS)

        time.sleep(TREE_REAP_SECONDS)

        assert stopped is True
        assert group_members(group) == ()
        assert any("killing it" in record.getMessage() for record in caplog.records)
    finally:
        # Best effort: the escalation under test has normally already emptied
        # the group, and a cleanup that raised would mask the test's result.
        try:
            os.killpg(group, signal.SIGKILL)
        except OSError:
            pass

        service._forget_worker(process)


@REQUIRES_PROCESS_TREES
def test_a_worker_whose_group_cannot_be_signalled_is_stopped_directly(
    monkeypatch: pytest.MonkeyPatch,
    worker_tree: WorkerTree,
) -> None:
    """Pin the per-process fallback when the group cannot be reached.

    Group signalling is what reaches the tree, so losing it is a real loss -
    but losing it must not mean the worker is left running. Both the polite
    and the forceful step fall back to the process itself, and the
    verification that follows then reports what the fallback could not reach.
    """
    group = worker_tree.process_group
    service._register_worker(worker_tree.process)
    monkeypatch.setattr(service, "_signal_worker_group", lambda process, number: False)
    monkeypatch.setattr(service.os, "killpg", _raiser(ProcessLookupError))

    assert (
        service._terminate_worker(worker_tree.process, grace_seconds=TEST_GRACE_SECONDS)
        is True
    )
    assert worker_tree.process.poll() is not None
    assert group > 0


# --------------------------------------------------------------------------- #
# Reclaiming a driver's own process group inside the worker's session
#
# The tests above start a tree whose child inherits the worker's group, which
# is not the shape a driver has: a driver is launched into a process group of
# its own, so signalling the worker's group reaches the engine and leaves the
# driver and its browser running.  The tests below reproduce that shape with
# ``NestedGroupTree`` and state the pgids and session ids they measured, so a
# reclamation that only reaches the worker's own group cannot pass them.
# --------------------------------------------------------------------------- #


@REQUIRES_PROCESS_TREES
def test_a_driver_group_is_nested_in_the_session_and_outside_the_workers_group(
    nested_group_tree: NestedGroupTree,
) -> None:
    """Pin the topology the whole reclamation is built on.

    Three measured facts, and every assertion below depends on all three: the
    nested process leads a group of its **own**, it is nevertheless in the
    worker's session, and signalling the worker's group therefore does not
    reach it. The last one is the defect containment exists for, so it is
    measured rather than described - after a ``SIGKILL`` of the worker's group
    the nested group is still listed.

    It is also what keeps the other tests honest: a tree whose child shared
    the worker's group would pass a reclamation that never looked at the
    session at all.
    """
    worker_group = nested_group_tree.process_group
    session = nested_group_tree.session
    nested = nested_group_tree.grandchild_group

    assert nested != worker_group
    assert nested == nested_group_tree.grandchild
    assert nested_group_tree.grandchild_session == session
    assert session == nested_group_tree.process.pid
    assert group_members(worker_group) == (nested_group_tree.process.pid,)
    assert group_members(nested) == (nested_group_tree.grandchild,)

    members = service._session_members(session)

    assert members is not None
    assert members.pids == {nested_group_tree.process.pid, nested_group_tree.grandchild}
    assert members.nested_groups(worker_group) == (nested,)

    os.killpg(worker_group, signal.SIGKILL)
    time.sleep(TREE_REAP_SECONDS)

    assert group_members(nested) == (nested_group_tree.grandchild,)
    assert service._session_is_empty(session) is False


@REQUIRES_PROCESS_TREES
def test_cancelling_a_worker_reaches_a_driver_in_its_own_group(
    stubborn_nested_group_tree: NestedGroupTree,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Pin cancellation against the production topology.

    The nested process is in a group of its own and ignores ``SIGTERM``, which
    together are a browser that does not close when its driver is asked
    politely. Cancellation has to escalate to the nested group specifically -
    the worker's own group does not contain it - and then confirm the session
    empty, not merely the worker's group empty.
    """
    worker_group = stubborn_nested_group_tree.process_group
    session = stubborn_nested_group_tree.session
    nested = stubborn_nested_group_tree.grandchild_group

    assert nested != worker_group
    assert stubborn_nested_group_tree.grandchild_session == session

    service._register_worker(stubborn_nested_group_tree.process)

    with caplog.at_level(logging.DEBUG, logger=service.__name__):
        stopped = service._terminate_worker(
            stubborn_nested_group_tree.process, grace_seconds=TEST_GRACE_SECONDS
        )

    time.sleep(TREE_REAP_SECONDS)

    assert stopped is True
    assert group_members(worker_group) == ()
    assert group_members(nested) == ()
    assert process_is_alive(stubborn_nested_group_tree.grandchild) is False
    assert service._session_is_empty(session) is True
    assert service._worker_tree_is_empty(
        service._WorkerContainment(
            stubborn_nested_group_tree.process.pid, worker_group, None, session
        )
    ) is True
    assert any(
        str(nested) in record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.WARNING
    )


@REQUIRES_PROCESS_TREES
def test_a_worker_whose_leader_exited_is_still_reclaimed(
    nested_group_tree: NestedGroupTree,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Pin the path that used to fail open: the leader has already gone.

    Cancellation arriving after the engine exited is not cancellation with
    nothing to do - the driver and the browser it started are in a group of
    their own and are still holding an authenticated session. So the worker is
    reclaimed before the answer "it was not running" is given, and the answer
    itself is unchanged.

    The reclamation cannot be relying on the worker's own group here, and that
    is asserted too: with the leader reaped, ``getsid`` of that group id no
    longer resolves, so the only thing that can attribute the nested group is
    the session's membership.
    """
    worker_group = nested_group_tree.process_group
    session = nested_group_tree.session
    nested = nested_group_tree.grandchild_group

    service._register_worker(nested_group_tree.process)
    nested_group_tree.stop_leader()

    assert nested_group_tree.process.poll() is not None
    assert group_members(nested) == (nested_group_tree.grandchild,)

    with pytest.raises(ProcessLookupError):
        os.getsid(worker_group)

    with caplog.at_level(logging.DEBUG, logger=service.__name__):
        stopped = service._terminate_worker(
            nested_group_tree.process, grace_seconds=TEST_GRACE_SECONDS
        )

    time.sleep(TREE_REAP_SECONDS)

    assert stopped is False
    assert group_members(nested) == ()
    assert process_is_alive(nested_group_tree.grandchild) is False
    assert service._session_is_empty(session) is True
    assert any(
        str(nested) in record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.WARNING
    )


@REQUIRES_PROCESS_TREES
def test_a_completed_worker_that_left_a_driver_behind_is_reclaimed(
    nested_group_tree: NestedGroupTree,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Pin the ordinary path, which is where a leaked browser actually appears.

    Most runs are never cancelled. A worker that finished on its own can still
    have left a driver and a browser in their own process group, and nothing
    else in the run would notice, so dropping a worker reclaims its session
    exactly as cancelling one does - and stays idempotent, because the owning
    thread and the interrupt path both drop the same worker.
    """
    session = nested_group_tree.session
    nested = nested_group_tree.grandchild_group

    service._register_worker(nested_group_tree.process)
    nested_group_tree.stop_leader()

    assert group_members(nested) == (nested_group_tree.grandchild,)

    with caplog.at_level(logging.WARNING, logger=service.__name__):
        service._forget_worker(
            nested_group_tree.process, grace_seconds=TEST_GRACE_SECONDS
        )

    time.sleep(TREE_REAP_SECONDS)

    assert group_members(nested) == ()
    assert service._session_is_empty(session) is True
    assert nested_group_tree.process.pid not in service._live_workers
    assert nested_group_tree.process.pid not in service._worker_containment
    assert [record for record in caplog.records if record.levelno >= logging.ERROR] == []

    caplog.clear()

    with caplog.at_level(logging.DEBUG, logger=service.__name__):
        service._forget_worker(nested_group_tree.process)

    assert caplog.records == []


def test_a_group_that_left_the_session_is_skipped_rather_than_signalled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin attribution as the condition every signal is subject to.

    A process-group id is recycled: once the group is gone the same number is
    handed to something else, and signalling it then kills a process this run
    never started. So a candidate is signalled only while it is attributable
    to the session captured at launch - and the discrimination is asserted in
    both directions, because a reclamation that signalled nothing would also
    satisfy a one-sided assertion.
    """
    session = 4242
    mine = 4300
    recycled = 4400
    sessions = {mine: session, recycled: 9999}
    signalled: list[tuple[int, int]] = []

    monkeypatch.setattr(service, "_HAS_PROCESS_GROUPS", True)
    monkeypatch.setattr(service.os, "getsid", lambda pid: sessions[pid], raising=False)
    monkeypatch.setattr(
        service.os,
        "killpg",
        lambda group, number: signalled.append((group, number)),
        raising=False,
    )
    monkeypatch.setattr(
        service,
        "_session_members",
        lambda asked: service._SessionMembers(
            asked, frozenset({session, mine, recycled}), frozenset({mine, recycled})
        ),
    )

    containment = service._WorkerContainment(session, session, None, session)

    assert service._group_in_session(mine, session) is True
    assert service._group_in_session(recycled, session) is False
    assert service._signal_nested_groups(containment, signal.SIGTERM) == (mine,)
    assert signalled == [(mine, signal.SIGTERM)]


def test_a_group_whose_leader_has_gone_is_attributed_by_membership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the second instrument, which the two reclamation paths depend on.

    Once a group's leader has been reaped, ``getsid`` of that group id answers
    nothing - and that is precisely the state of every group whose driver
    exited while its browser did not. A live process of the captured session
    carrying the group id is the same evidence, so it is accepted; no evidence
    at all is not, and stays distinct from "belongs to another session".
    """
    session = 4242
    group = 4300

    monkeypatch.setattr(service, "_HAS_PROCESS_GROUPS", True)
    monkeypatch.setattr(service.os, "getsid", _raiser(ProcessLookupError), raising=False)

    members = service._SessionMembers(session, frozenset({4301}), frozenset({group}))

    assert service._group_in_session(group, session, members) is True
    assert service._group_in_session(9999, session, members) is False

    # No membership to fall back on, on a platform that cannot supply one.
    monkeypatch.setattr(service, "_session_members", lambda asked: None)

    assert service._group_in_session(group, session) is None


def test_a_session_that_cannot_be_enumerated_is_unverified_and_unsignalled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Pin the platform without a process directory: unknown, never clean.

    A session that cannot be read is a session whose driver groups nobody
    could look for. Claiming the release would be the same defect as never
    checking, so the answer is ``None``, the report is a ``WARNING`` rather
    than silence, and nothing is signalled - an id that cannot be attributed
    is not an id this run may kill.
    """
    signalled: list[tuple[int, int]] = []

    monkeypatch.setattr(service, "_HAS_PROCESS_GROUPS", True)
    monkeypatch.setattr(service, "_PROC_ROOT", tmp_path / "absent")
    monkeypatch.setattr(
        service.os,
        "killpg",
        lambda group, number: signalled.append((group, number)),
        raising=False,
    )

    containment = service._WorkerContainment(4242, None, None, 4242)

    assert service._session_members(4242) is None
    assert service._session_is_empty(4242) is None
    assert service._worker_tree_is_empty(containment) is None
    assert service._signal_nested_groups(containment, signal.SIGTERM) == ()
    assert signalled == []

    with caplog.at_level(logging.DEBUG, logger=service.__name__):
        assert service._reclaim_worker_session(containment) is None

    assert [record.levelno for record in caplog.records] == [logging.WARNING]


def test_the_session_reader_parses_every_shape_of_status_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Pin the ``stat`` parse, whose one hard case is the program's own name.

    That name is the second field, it is parenthesised, and it may contain
    spaces and parentheses of its own - ``chrome (2)`` is an ordinary one.
    Splitting the whole line on whitespace mis-numbers every field after it,
    which would read a process's group and session from its run state and its
    parent, so the text is cut at its **last** ``)`` first.

    Everything else a reader of a live process directory meets is pinned
    alongside it: entries that are not pids, a process that exited between the
    listing and the read, a truncated file, an unreadable one, and a process
    of another session.
    """
    session = 4242
    root = tmp_path / "proc"
    root.mkdir()

    def plant(pid: int, comm: str, group: int, belongs_to: int) -> None:
        directory = root / str(pid)
        directory.mkdir()
        (directory / "stat").write_text(
            f"{pid} ({comm}) S 1 {group} {belongs_to} 0 -1 4194304 0 0",
            encoding="utf-8",
        )

    plant(101, "python3.14", 101, session)
    plant(102, "a program (2) name", 102, session)
    plant(103, "chrome", 102, session)
    plant(104, "somebody-elses-shell", 104, 9999)
    (root / "not-a-pid").mkdir()
    (root / "105").mkdir()
    (root / "106").mkdir()
    (root / "106" / "stat").write_text("106 (truncated) S", encoding="utf-8")

    monkeypatch.setattr(service, "_PROC_ROOT", root)

    members = service._session_members(session)

    assert members is not None
    assert members.session == session
    assert members.pids == {101, 102, 103}
    assert members.groups == {101, 102}
    assert members.nested_groups(101) == (102,)
    assert members.nested_groups() == (101, 102)
    assert service._session_is_empty(session) is False
    assert service._session_is_empty(9999) is False
    assert service._session_is_empty(5150) is True

    # The parse itself, stated on the case that makes it necessary.
    assert service._stat_ids("7 (a program (2) name) S 1 8 9 0") == (8, 9)
    assert service._stat_ids("7 (short) S 1") is None
    assert service._stat_ids("no parenthesis here") is None
    assert service._stat_ids("7 (odd) S 1 x y") is None


def test_a_worker_tree_answers_only_when_every_dimension_does(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the combination, which is where a half-confirmed release would hide.

    A worker's own group and its session are separate questions with separate
    answers, and the tree is both together: either one still holding a process
    means something survived, and a confirmed release requires every dimension
    the record carries to say so. An unanswerable dimension leaves the whole
    answer unknown rather than letting the other dimension pass for it.
    """
    matrix: list[tuple[bool | None, bool | None, bool | None]] = [
        (True, True, True),
        (True, False, False),
        (False, True, False),
        (False, None, False),
        (None, False, False),
        (True, None, None),
        (None, True, None),
        (None, None, None),
    ]

    for group_state, session_state, expected in matrix:
        monkeypatch.setattr(
            service, "_process_group_is_empty", lambda group, answer=group_state: answer
        )
        monkeypatch.setattr(
            service, "_session_is_empty", lambda session, answer=session_state: answer
        )

        containment = service._WorkerContainment(4242, 4242, None, 4242)

        assert service._worker_tree_is_empty(containment) is expected, (
            group_state,
            session_state,
        )


class DeclaringKernel32:
    """A ``kernel32`` stand-in whose entry points accept a prototype.

    ctypes' own function pointers carry ``restype`` and ``argtypes``; a bound
    method does not, so the recording double above cannot show whether the
    module declares them. These entry points are plain function objects, which
    can, and they are what makes the declaration observable off Windows.

    :param close: What ``CloseHandle`` returns; ``0`` is a close that did not
        take effect.
    """

    def __init__(self, *, close: int = 1) -> None:
        #: Entry point names in call order, with the arguments each received.
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

        for name, result in (
            ("CreateJobObjectW", FAKE_JOB_HANDLE),
            ("SetInformationJobObject", 1),
            ("OpenProcess", 0x77),
            ("AssignProcessToJobObject", 1),
            ("CloseHandle", close),
        ):
            setattr(self, name, self._entry_point(name, result))

    def _entry_point(self, name: str, result: int) -> Any:
        """Build one recording entry point.

        :param name: The entry point's name.
        :param result: What it returns.
        :returns: A plain function, so a prototype can be set on it.
        """

        def entry_point(*arguments: Any) -> int:
            self.calls.append((name, arguments))
            return result

        return entry_point

    def targets(self) -> tuple[str, ...]:
        """The recorded call names, in order.

        :returns: The sequence of entry points called.
        """
        return tuple(name for name, _ in self.calls)


def test_every_win32_containment_call_declares_its_handle_width(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the ctypes prototypes, which are a correctness requirement.

    Undeclared, ctypes assumes a C ``int`` return, and on 64-bit Windows that
    **truncates** the job and process handles the kernel returns: the value
    kept is not the handle that was created, so closing it neither terminates
    the job's members nor reports that it did not - a browser left running
    with nothing recorded. So every handle-returning and handle-taking call is
    declared, and the width of the declared type is asserted against a
    pointer's rather than taken on trust.
    """
    import ctypes

    library = DeclaringKernel32()

    monkeypatch.setattr(service, "_HAS_PROCESS_GROUPS", False)
    monkeypatch.setattr(service, "_kernel32", lambda: library)

    assert service._assign_kill_on_close_job(616) == FAKE_JOB_HANDLE

    handle, boolean, dword = service._win32_types(ctypes)

    assert ctypes.sizeof(handle) == ctypes.sizeof(ctypes.c_void_p)
    assert library.CreateJobObjectW.restype is handle
    assert library.OpenProcess.restype is handle
    assert library.OpenProcess.argtypes == (dword, boolean, dword)
    assert library.SetInformationJobObject.restype is boolean
    assert library.SetInformationJobObject.argtypes[0] is handle
    assert library.AssignProcessToJobObject.argtypes == (handle, handle)
    assert library.CloseHandle.restype is boolean
    assert library.CloseHandle.argtypes == (handle,)

    # And the process handle is still closed exactly once, whatever the
    # declaration: the job outlives the call, the process handle must not.
    assert library.targets().count("CloseHandle") == 1
    assert library.calls[-1] == ("CloseHandle", (0x77,))


def test_a_containment_job_close_that_reports_failure_is_reported(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Pin the close's *result* as something read rather than discarded.

    On Windows the close is the reclamation, so a ``CloseHandle`` that returns
    failure without raising is a job whose kill-on-close limit never fired and
    a tree still running. Reported at ``WARNING`` and never raised, because
    this runs while a run is being cancelled.
    """
    library = DeclaringKernel32(close=0)

    monkeypatch.setattr(service, "_HAS_PROCESS_GROUPS", False)
    monkeypatch.setattr(service, "_kernel32", lambda: library)
    monkeypatch.setitem(
        service._worker_containment,
        616,
        service._WorkerContainment(616, None, FAKE_JOB_HANDLE),
    )

    with caplog.at_level(logging.WARNING, logger=service.__name__):
        service._close_worker_job(616)

    assert library.targets() == ("CloseHandle",)
    assert [record.levelno for record in caplog.records] == [logging.WARNING]
    assert 616 not in service._worker_containment
