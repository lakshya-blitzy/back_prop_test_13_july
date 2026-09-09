"""Scenario selection, sharding, worker invocation and result merging.

This module is the executable form of the Java build's test-execution
configuration.  Everything the source project said about *how* the suite runs
it said in the POM, verified verbatim in this repository::

    pom.xml:17-20   maven-surefire-plugin 3.0.0-M5
    pom.xml:22      <parallel>methods</parallel>
    pom.xml:23      <useUnlimitedThreads>true</useUnlimitedThreads>
    pom.xml:24      <!--                    <threadCount>4</threadCount>-->
    pom.xml:25      <testFailureIgnore>true</testFailureIgnore>
    pom.xml:26-28   <includes><include>**/CukesRunner*.java</include></includes>

The activation decision
-----------------------
None of that configuration executes in the source project.  Every runner class
lives under ``src/main/java``, there is no ``src/test/java``, and the surefire
include above matches *test* classes only, so the pipeline's own command --
``mvn -B clean test`` (``Jenkins:8``, ``Jenkins:10``) -- reports "No tests to
run." and BUILD SUCCESS, leaving ``target/`` with nothing but ``classes``,
``generated-sources`` and ``maven-status``: no ``cucumber.json``, no
``rerun.txt``, no HTML.  The surefire block is **latent configuration**.

This port **activates** that latent behaviour: the suite really runs and really
produces the four artifacts, because literal parity with a stage that executes
nothing would preserve no feature at all, and "keeping every feature and
functionality exactly" is the requirement.  **This module is where that
decision lands** -- it is the thing that makes the suite run.

Three consequences, and one of them is a deviation
--------------------------------------------------
* ``parallel=methods`` is *method*-level, which at Gherkin level is
  *scenario*-level.  The sharding unit here is therefore the scenario, never
  the feature -- see :func:`shard_scenarios`.
* ``useUnlimitedThreads=true`` **cannot be mirrored, and equivalence is not
  claimed.**  It removes a thread-count cap on threads inside a single JVM;
  the commented-out ``<threadCount>4</threadCount>`` at ``pom.xml:24`` is
  exactly what it replaced.  A Python Selenium session is not thread-shareable
  in that way, so concurrency here is **process-based with a CPU-count
  default** (:func:`default_worker_count`).  That is a recorded deviation from
  the source's execution model, not a translation of it.
* ``testFailureIgnore=true`` (``pom.xml:25``), together with the six ``-1``
  thresholds on the Jenkins Cucumber publisher (``Jenkins:15``), means **a
  test outcome must never reach the exit status**.  This module never exits a
  process and never converts a scenario outcome into an error; see
  :class:`RunOutcome` and the note on behave's own exit code in
  :func:`_run_one_shard`.

What this module deliberately does not own
------------------------------------------
Each of these has exactly one owner elsewhere, and none of it appears here:
checking out code (the pipeline's ``checkout scm``), publishing and
thresholding (the Jenkins Cucumber publisher, ``Jenkins:15``), choosing the
shell (the pipeline's ``isUnix()`` branch, ``Jenkins:7-11``), driver creation
and disposal (``app/automation/driver.py``, called from
``features/environment.py``), ``--clean`` and the exit codes
(``app/cli.py``), the artifact paths (:mod:`app.utils.paths`), the result
schema and the merge algorithm (:mod:`app.reporting.events`), and the rerun
manifest's grammar (:mod:`app.reporting.rerun_report`).  There is no platform
abstraction here, no clone logic, no threshold logic and no driver code.

Testability is structural
-------------------------
Every non-trivial step is a pure function that can be called directly --
:func:`select_scenarios`, :func:`shard_scenarios`,
:func:`build_worker_command`, :func:`merge_worker_results` -- the subprocess
launch sits behind the injectable ``spawn`` seam, and every filesystem
location is reachable through the injectable ``base`` parameter.  That is what
lets ``tests/test_test_run_service.py`` assert the two hard invariants without
launching a browser or a real engine:

1. every selected scenario is assigned to **exactly one** worker, for any
   worker count; and
2. for a fixed set of shard inputs the merged structure -- feature order,
   scenario order, background position, statuses -- is **identical whatever
   the worker count**.

A note for ``app/cli.py``, which consumes this module
-----------------------------------------------------
:class:`RunOutcome` is the interface to the exit contract, and two of its
states look alike but are not:

* ``result_set`` is an **empty result set** when the tag expression selected
  nothing -- status ``0``, all four artifacts written empty.
* ``result_set`` is ``None`` **with** ``merge_produced_nothing`` set when
  scenarios were selected and not one worker file could be read -- non-zero,
  nothing written.

A single falsy check would collapse the two and break both rows.  And under
``rerun`` a ``None`` ``result_set`` means neither of those things: a rerun
writes no artifacts at all (the Java ``FailedTestRunner`` declared an empty
plugin list), so ``merge_produced_nothing`` stays ``False`` and a ``None``
document there must **not** be read as the empty-merge condition.
"""

import logging
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final, Protocol, runtime_checkable

# behave's own parser, deliberately rather than a hand-rolled Gherkin reader:
# selection must not be able to disagree with what the engine will actually
# run.  ``ParserError`` is what a malformed feature file raises (measured).
from behave.parser import ParserError, parse_file

# The same tag-expression grammar the JVM used (``tag-expressions:4.1.0``
# there, ``cucumber-tag-expressions`` 11.0.1 here).  Only the parser is
# imported: ``TagExpressionError`` is documented as the one exception the
# selection functions propagate, and it is deliberately never caught here.
from cucumber_tag_expressions import TagExpressionParser

from app.reporting.events import (
    FORMATTER_SCOPED_NAME,
    ResultSetError,
    load_result_set,
    merge_result_sets,
    new_result_set,
)

# ``RerunManifestError`` is not re-exported by ``app/reporting/__init__.py``,
# so both names come from the submodule -- which is also the import form that
# package's docstring prefers, since a submodule import tolerates a
# half-initialised parent package.
from app.reporting.rerun_report import (
    LINE_SEPARATOR,
    RerunManifestError,
    parse_rerun_file,
)
from app.utils.paths import (
    FILE_URI_SCHEME,
    NORMALIZED_FEATURES_PREFIX,
    ensure_dir,
    features_dir,
    normalize_feature_uri,
    rerun_txt_path,
    target_root,
    worker_result_path,
    workers_dir,
)

__all__ = [
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
]

#: Module logger.  ``app/logging_config.py`` installs the handler split this
#: module's output depends on -- ``INFO`` and below to stdout as progress,
#: ``WARNING`` and above to stderr as diagnostics -- and that module is
#: imported only by the two process entry points, never from here.
logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# The neutral tag expression
# --------------------------------------------------------------------------- #

#: A tag expression that selects everything, passed to a worker whenever no
#: filter applies.
#:
#: **Why a tautology rather than nothing at all.**  ``behave.ini`` declares
#: ``default_tags = @Smoke``, and that default applies whenever the command
#: line supplies no filter of its own -- *including* a command line that names
#: explicit scenario locations.  Measured, on this suite:
#:
#: * ``python -m behave --dry-run features/Session.feature:3`` reports
#:   "1 skipped" and never even resolves the step (it prints ``# None``),
#:   because ``Session.feature`` does not carry ``@Smoke``;
#: * the same command plus ``--tags="not @<undeclared>"`` reports
#:   "1 untested" and resolves the step to
#:   ``features/steps/session_steps.py:146``.
#:
#: So an explicit ``--tags`` on the command line is the mechanism that
#: suppresses ``default_tags``, and this module passes one on **every** worker
#: invocation.  Under ``--rerun`` that is not a nicety: the Java
#: ``FailedTestRunner`` declared no tag filter at all, so re-running failures
#: has to be able to clear the default outright, or every failure from a
#: non-``@Smoke`` feature would be silently skipped.
#:
#: The expression is a negation of a tag no feature declares rather than an
#: empty value, because an empty ``--tags`` may be rejected outright, and it
#: evaluates ``True`` against an empty tag list -- which is what the five
#: untagged features need.  ``behave.ini`` is **not** edited to solve this:
#: its formatter-free, path-free shape is load-bearing (see
#: :func:`build_worker_command`).
NEUTRAL_TAG_EXPRESSION: Final[str] = "not @__testinium_qa_no_such_tag__"

#: Extension of a Gherkin feature file.  This is a file *extension*, not a
#: path: :mod:`app.utils.paths` owns every directory and artifact name in the
#: port (and supplies :data:`~app.utils.paths.NORMALIZED_FEATURES_PREFIX`
#: below), and it deliberately declares no Gherkin suffix.
_FEATURE_SUFFIX: Final[str] = ".feature"

#: Tag sigil.  behave's model stores tag names **without** it -- measured:
#: ``Crm.feature``'s feature tag arrives as ``'Smoke'`` -- while a tag
#: expression is written with it, so it is re-added before evaluation.
#: Forgetting this is the failure mode that silently selects zero scenarios.
_TAG_SIGIL: Final[str] = "@"


# --------------------------------------------------------------------------- #
# Worker command-line vocabulary
#
# Names rather than inline literals, so the command construction reads as a
# contract and a change lands in exactly one place.
# --------------------------------------------------------------------------- #

#: The engine is invoked as a module of the *current* interpreter, so a run
#: works inside the bootstrapped ``.venv`` with no assumption about ``PATH``.
_BEHAVE_MODULE: Final[str] = "behave"
_FORMAT_FLAG: Final[str] = "--format"
_OUTFILE_FLAG: Final[str] = "-o"
_TAGS_FLAG: Final[str] = "--tags"
_USERDATA_FLAG: Final[str] = "-D"
_DRY_RUN_FLAG: Final[str] = "--dry-run"

#: **Verified by execution, and required.**  Without it a worker records every
#: scenario of a feature it touches, not just the ones it was told to run: a
#: worker given ``features/Contact.feature:17`` writes elements for the
#: scenarios at lines 17, 19, 36 *and* 38, the last three as skipped.  Two
#: shards holding different scenarios of one feature would then merge into a
#: feature whose every scenario appears twice, breaking both the
#: exactly-once rule and the worker-count independence of the merged
#: structure.  With this flag the same command records exactly the background
#: and the scenario at line 17.  It suppresses whole non-selected scenarios
#: only -- a genuinely skipped *step* inside an executed scenario keeps its
#: ``skipped`` status, which the JSON contract requires and which was measured
#: separately.
_NO_SKIPPED_FLAG: Final[str] = "--no-skipped"

#: behave userdata key carrying the browser override.  ``app/config.py`` reads
#: it with userdata-first precedence inside the worker, and it is the only
#: override path -- no environment layer is added.
_BROWSER_USERDATA_KEY: Final[str] = "browser"


@runtime_checkable
class WorkerProcess(Protocol):
    """The result of one worker launch, as :func:`run_suite` reads it.

    :class:`subprocess.CompletedProcess` satisfies this structurally, which is
    what lets the ``spawn`` seam be stubbed with any object carrying the same
    three attributes.

    Attributes:
        returncode: The engine's exit status.  **A positive non-zero value is
            normal** -- behave exits ``1`` when scenarios fail -- and is never
            treated as an error here.
        stdout: Captured standard output, relayed as run progress.
        stderr: Captured standard error, relayed as engine diagnostics.
    """

    returncode: int | None
    stdout: str | None
    stderr: str | None


#: Signature of the ``spawn`` seam: it takes the argument list and the working
#: directory, and returns something shaped like a completed process.
SpawnCallable = Callable[[Sequence[str], Path], WorkerProcess]


@dataclass(frozen=True, slots=True)
class ScenarioRef:
    """One executable unit of the suite: a scenario, or one Examples row.

    Attributes:
        feature_path: Repository-relative path with forward slashes, e.g.
            ``features/Crm.feature``.  Built from
            :data:`~app.utils.paths.NORMALIZED_FEATURES_PREFIX` and the file's
            own name, so it is identical on every platform and identical to
            the ``path`` the result collector records -- which is what lets
            the merge and this module agree on feature identity.  Feature
            filenames are preserved exactly as the Java project had them; they
            appear in the JSON ``uri``, in the rerun manifest and in user
            commands, so none is ever renamed or normalised.
        line: The executable unit's **own** line.  For a scenario outline that
            is the Examples **data-row** line, never the outline header's:
            the JSON contract records an outline row with the data row's line
            and the rerun manifest lists those same numbers.
        name: The unit's name as behave reports it.  For a generated outline
            row that includes behave's ``" -- @1.1 <Examples>"`` annotation;
            stripping it for the JSON report belongs to
            :mod:`app.reporting.events`, which owns that contract, so nothing
            is rewritten here.  This field is informational -- it appears in
            log messages and nowhere else.
        tags: The unit's **effective** tags -- the feature's, the scenario's
            and, for an outline row, the Examples block's -- each carrying the
            leading ``@`` and the whole sorted, so the value is deterministic
            across processes (behave exposes them as a set).
    """

    feature_path: str
    line: int
    name: str
    tags: tuple[str, ...]

    @property
    def location(self) -> str:
        """Return the unit's location in behave's ``path:line`` syntax.

        Returns:
            ``features/Crm.feature:24`` and the like.  The separator comes
            from :mod:`app.reporting.rerun_report`, which owns the rerun
            manifest's grammar: the manifest's separator and behave's location
            syntax are the same character, and ``--rerun`` feeds one straight
            into the other, so defining a second copy here is exactly how the
            round trip would come apart.
        """
        return f"{self.feature_path}{LINE_SEPARATOR}{self.line}"


@dataclass(frozen=True, slots=True)
class ShardPlan:
    """One worker's share of the run.

    Attributes:
        index: Zero-based shard index, used in the worker's output filename
            and in every log line about it.
        locations: The scenario locations this worker executes, grouped by
            feature and ascending by line within a feature.  Never empty: a
            worker is never spawned with nothing to do.
        output_path: Where this worker writes its intermediate result
            document, from :func:`~app.utils.paths.worker_result_path`.
            Computed in the **parent**, so the parent knows every expected
            path before a single child starts and can tell an absent file from
            an unreadable one at merge time.
    """

    index: int
    locations: tuple[str, ...]
    output_path: Path


@dataclass(frozen=True, slots=True)
class ShardResult:
    """What became of one shard.

    Attributes:
        plan: The shard this describes.
        returncode: The engine's exit status, or ``None`` when the launch
            itself failed.
        dead: Whether the shard failed to produce results.  **Not** whether
            its scenarios passed: see :func:`_run_one_shard`.
        reason: Human-readable explanation naming the shard, present exactly
            when ``dead`` is set, and reported on stderr.
    """

    plan: ShardPlan
    returncode: int | None
    dead: bool
    reason: str | None


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """The result of a whole run, and the interface to the exit contract.

    ``app/cli.py`` owns the exit codes and decides from these fields alone, so
    every signal is surfaced independently and **no precedence is encoded
    here**.  This module never terminates the interpreter: no process-exit
    call appears anywhere in it, neither the one in :mod:`sys` nor the
    low-level one in :mod:`os`, and it never prints an exit code.

    Attributes:
        result_set: The merged result document, an **empty** document when
            nothing was selected, or ``None``.  The three states are
            distinct -- see the module docstring and
            :attr:`merge_produced_nothing`.
        selected_count: How many executable units the selection produced.
            ``0`` means the tag expression matched nothing, which is a
            status-``0`` outcome with four empty artifacts.
        worker_count: How many workers actually ran; ``0`` when none was
            spawned.
        shard_results: One entry per shard, in shard order.
        dead_shards: The ``reason`` of every dead shard, already logged at
            ``ERROR``.  A dead shard is non-zero **and** non-suppressing: the
            artifacts are still written from the shards that completed.
        parse_errors: Problems that were reported and survived -- a feature
            that failed to parse, a missing or malformed rerun manifest, a
            missing or empty features directory.  Each stays at status ``0``,
            matching the source's own tolerance of a missing configuration
            file.
        merge_produced_nothing: Set only when scenarios were selected and not
            one worker file could be read.  Never set merely because nothing
            was selected, and never set under ``rerun``.
        rerun: Whether this was a rerun.  When set, ``app/cli.py`` must not
            invoke the report service at all, and must not read a ``None``
            ``result_set`` as the empty-merge condition.
        dry_run: Whether the engine was asked not to execute steps.
        tag_expression: The filter as the user expressed it, or ``None`` when
            no filter applied and under ``rerun``.  This is deliberately
            **not** :data:`NEUTRAL_TAG_EXPRESSION`: that tautology is the
            mechanism that suppresses ``behave.ini``'s default, not a filter
            the user asked for, and it must never surface in a report.
    """

    result_set: dict[str, Any] | None
    selected_count: int
    worker_count: int
    shard_results: tuple[ShardResult, ...]
    dead_shards: tuple[str, ...]
    parse_errors: tuple[str, ...]
    merge_produced_nothing: bool
    rerun: bool
    dry_run: bool
    tag_expression: str | None


# --------------------------------------------------------------------------- #
# Worker count
# --------------------------------------------------------------------------- #


def default_worker_count() -> int:
    """Return the default number of workers.

    The CPU count is this port's stand-in for ``useUnlimitedThreads=true``
    (``pom.xml:23``).  It is a deviation rather than a translation -- see the
    module docstring -- and the commented-out ``<threadCount>4</threadCount>``
    at ``pom.xml:24`` records what the source replaced with the uncapped
    setting.

    Returns:
        :func:`os.cpu_count`, or ``1`` where the platform cannot report it.
    """
    return os.cpu_count() or 1


def _effective_worker_count(requested: int | None, selected_count: int) -> int:
    """Clamp a requested worker count to something spawnable.

    Args:
        requested: The caller's ``--workers`` value, or ``None`` for the
            default.  A non-positive value is treated as "unspecified" rather
            than rejected: validating an option value belongs to
            ``app/cli.py``, and this service stays tolerant.
        selected_count: How many executable units were selected.

    Returns:
        ``0`` when nothing was selected, so no worker is spawned at all.
        Otherwise at least ``1`` and never more than ``selected_count`` -- a
        worker is never given an empty shard.  ``1`` is the sequential mode.
    """
    if selected_count <= 0:
        return 0
    unspecified = requested is None or requested <= 0
    resolved = default_worker_count() if unspecified else requested
    return max(1, min(resolved, selected_count))


# --------------------------------------------------------------------------- #
# Tag expressions
# --------------------------------------------------------------------------- #


def _prefixed_tags(names: Iterable[str]) -> tuple[str, ...]:
    """Return tag names with the sigil restored, sorted and deduplicated.

    behave's model stores a tag as its bare name, while a tag expression is
    written with the sigil, so the two only meet after this conversion.  The
    sort is what makes the value deterministic: behave exposes effective tags
    as a set, whose iteration order is not stable across processes.

    Args:
        names: Tag names as behave reports them, with or without the sigil.

    Returns:
        The ``@``-prefixed names, sorted.
    """
    return tuple(
        sorted(
            {
                name if name.startswith(_TAG_SIGIL) else f"{_TAG_SIGIL}{name}"
                for name in names
            }
        )
    )


def _parse_tag_expression(tags: str | None) -> Any | None:
    """Parse a tag expression once, for evaluation against every unit.

    Args:
        tags: The expression as the user wrote it, or ``None``/blank for no
            filter.  The full grammar is supported -- ``@Smoke``,
            ``@Login and not @wip``, ``@UPGN-286 or @UPGN-287``.

    Returns:
        The parsed expression, or ``None`` when no filter applies, in which
        case every unit is selected.

    Raises:
        TagExpressionError: If the expression is malformed.  **This is the one
            exception the selection functions raise, and it is deliberate.**
            A malformed expression is an invalid option value -- the usage
            error class ``app/cli.py`` owns, which exits non-zero and writes
            nothing -- and not a test outcome.  Swallowing it would exit ``0``
            with four empty artifacts and never tell the user their filter was
            nonsense.
    """
    if tags is None:
        return None
    text = tags.strip()
    if not text:
        return None
    return TagExpressionParser.parse(text)


def _effective_tag_expression(tags: str | None) -> str:
    """Return the expression to put on a worker's command line.

    Never returns ``None`` or an empty string: an explicit ``--tags`` is what
    stops ``behave.ini``'s ``default_tags = @Smoke`` from applying implicitly,
    so every worker invocation carries one.  See
    :data:`NEUTRAL_TAG_EXPRESSION` for the measurement behind that.

    Args:
        tags: The expression as the user wrote it, or ``None``/blank.

    Returns:
        The user's expression, or :data:`NEUTRAL_TAG_EXPRESSION`.
    """
    if tags is None:
        return NEUTRAL_TAG_EXPRESSION
    return tags.strip() or NEUTRAL_TAG_EXPRESSION


def _recorded_tag_expression(tags: str | None, rerun: bool) -> str | None:
    """Return the expression to record on the outcome and in the document.

    Deliberately different from :func:`_effective_tag_expression`: the neutral
    tautology is a mechanism for suppressing a configuration default, not a
    filter anybody asked for, so it never reaches a report.

    Args:
        tags: The expression as the user wrote it, or ``None``.
        rerun: Whether this is a rerun, which carries no filter at all
            because ``FailedTestRunner`` declared none.

    Returns:
        The user's expression, or ``None`` when no filter applied.
    """
    if rerun or tags is None:
        return None
    return tags.strip() or None


# --------------------------------------------------------------------------- #
# Selection
# --------------------------------------------------------------------------- #


def _feature_files(base: Path | str | None) -> tuple[list[Path], list[str]]:
    """List the suite's feature files in canonical order.

    The sort is the run's **canonical feature order** and is reused by the
    merge, because the merged structure has to come out identical whatever the
    worker count.

    Args:
        base: Directory the ``features`` directory hangs off, or ``None`` for
            the working directory.

    Returns:
        A ``(paths, problems)`` pair.  ``paths`` holds the ``*.feature`` files
        directly under the features directory, sorted.  ``problems`` names a
        missing, unreadable or empty directory -- each a tolerated condition
        that yields zero selected scenarios and status ``0``, never an
        exception.
    """
    directory = features_dir(base)
    problems: list[str] = []
    try:
        entries = [
            entry
            for entry in directory.iterdir()
            if entry.is_file() and entry.suffix == _FEATURE_SUFFIX
        ]
    except OSError as error:
        problems.append(f"{directory}: feature directory cannot be listed ({error})")
        return [], problems
    if not entries:
        problems.append(f"{directory}: no {_FEATURE_SUFFIX} files found")
        return [], problems
    return sorted(entries), problems


def _feature_path_of(path: Path) -> str:
    """Return the repository-relative, forward-slashed path of a feature file.

    Built from the features-directory prefix and the file's own name rather
    than from :meth:`pathlib.Path.relative_to`, so the value is identical on
    POSIX and Windows and identical whatever ``base`` was -- and identical to
    the ``path`` the result collector writes, which is what the merge keys on.

    Args:
        path: The feature file.

    Returns:
        For example ``features/Crm.feature``.
    """
    return f"{NORMALIZED_FEATURES_PREFIX}{path.name}"


def _units_of(path: Path) -> tuple[list[ScenarioRef], list[str]]:
    """Parse one feature file into its executable units.

    Args:
        path: The feature file to parse.

    Returns:
        A ``(units, problems)`` pair.  Outlines are expanded into their
        generated example scenarios, each carrying its own Examples data-row
        line, by way of behave's ``walk_scenarios()`` -- the engine's own
        traversal, so selection cannot disagree with execution.  ``problems``
        carries a message naming the file when it could not be parsed, in
        which case ``units`` is empty and **the caller carries on with the
        remaining features**: a parse failure must never abort a run.
    """
    feature_path = _feature_path_of(path)
    try:
        feature = parse_file(str(path))
    except ParserError as error:
        return [], [f"{feature_path}: cannot be parsed ({error})"]
    except (OSError, UnicodeDecodeError) as error:
        return [], [f"{feature_path}: cannot be read ({error})"]
    except Exception as error:  # noqa: BLE001
        # Deliberately broad.  behave's parser raises ParserError for the
        # malformed input measured here, but a defect in a single feature file
        # must not be able to abort a whole run under any exception type; the
        # file is named and the run continues.
        return [], [f"{feature_path}: cannot be parsed ({error!r})"]

    if feature is None:
        # An empty or comment-only file parses to nothing.  It contributes no
        # units and is not a problem: there is simply nothing to run.
        return [], []

    units = [
        ScenarioRef(
            feature_path=feature_path,
            line=int(scenario.line),
            name=str(scenario.name or ""),
            tags=_prefixed_tags(scenario.effective_tags),
        )
        for scenario in feature.walk_scenarios()
    ]
    return units, []


def select_scenarios(
    *,
    tags: str | None = None,
    base: Path | str | None = None,
) -> tuple[list[ScenarioRef], list[str]]:
    """Select the executable units a run should execute.

    Pure apart from reading the feature files, and the whole of the selection
    logic: what this returns is exactly what gets sharded.

    Args:
        tags: Tag expression to filter by, or ``None`` for no filter, which
            selects everything.  ``app/cli.py`` passes ``"@Smoke"`` by
            default, reproducing ``CukesRunner``'s own default -- a tag
            declared exactly once in the suite, at ``Crm.feature:1``, so a
            default run selects the CRM feature alone.  Five features carry no
            feature-level tag at all, which leaves them unreachable by a
            positive expression over feature tags and perfectly reachable by a
            negative one such as ``not @Smoke``.  Both are the suite's own
            shape, faithfully reproduced rather than corrected.
        base: Directory the features directory hangs off, or ``None`` for the
            working directory.

    Returns:
        A ``(selected, parse_errors)`` pair.  ``selected`` is ordered by
        feature path and then by line -- the canonical order the sharding and
        the merge both rely on.  ``parse_errors`` holds the messages of every
        tolerated problem, each of which leaves the run at status ``0``.

    Raises:
        TagExpressionError: If ``tags`` is malformed; see
            :func:`_parse_tag_expression` for why this one propagates.
    """
    # Parsed once, before any file is read, so a malformed expression fails
    # immediately rather than after the whole suite has been parsed.
    expression = _parse_tag_expression(tags)

    paths, parse_errors = _feature_files(base)
    selected: list[ScenarioRef] = []
    for path in paths:
        units, problems = _units_of(path)
        parse_errors.extend(problems)
        for unit in units:
            if expression is None or expression.evaluate(list(unit.tags)):
                selected.append(unit)

    # Both keys matter: the feature order is the canonical one, and ascending
    # line order within a feature is what the round-robin walk expects.
    selected.sort(key=lambda unit: (unit.feature_path, unit.line))
    return selected, parse_errors


def select_rerun_scenarios(
    *,
    base: Path | str | None = None,
) -> tuple[list[ScenarioRef], list[str]]:
    """Select the scenarios a rerun should execute, from the rerun manifest.

    This is the port of ``FailedTestRunner``, whose whole configuration was
    ``features = "@target/rerun.txt"``.  The manifest is parsed by
    :func:`~app.reporting.rerun_report.parse_rerun_file`, the single owner of
    that grammar -- nothing is parsed here, so the format the writer produces
    and the format the rerun consumes cannot drift apart.

    No tag filter is applied, and that is the point: ``FailedTestRunner``
    declared none, so a rerun must reach failures from features that carry no
    ``@Smoke`` tag.  See :data:`NEUTRAL_TAG_EXPRESSION` for how the same
    requirement is enforced on the worker's command line.

    Args:
        base: Directory the manifest and the features directory hang off, or
            ``None`` for the working directory.

    Returns:
        A ``(selected, problems)`` pair.  Every location named by the manifest
        is selected, enriched with the scenario's name and effective tags
        where the feature file still declares one at that line.  A location
        that can no longer be resolved is **kept anyway** and noted in
        ``problems``: dropping it would silently discard a failure, which is
        the one thing a rerun must not do.  A missing, unreadable or malformed
        manifest yields no scenarios and a problem message, never an
        exception, and leaves the run at status ``0``.
    """
    manifest = rerun_txt_path(base)
    try:
        entries = parse_rerun_file(base=base)
    except RerunManifestError as error:
        return [], [str(error)]

    problems: list[str] = []
    selected: list[ScenarioRef] = []
    for entry in entries:
        # A manifest written by this port already carries the features/ prefix;
        # normalising covers one written against the Java layout, whose
        # src/main/resources/features/ prefix maps onto this one.
        feature_path = normalize_feature_uri(entry.path)
        source = features_dir(base) / Path(feature_path).name

        units: list[ScenarioRef] = []
        if source.is_file():
            units, unit_problems = _units_of(source)
            problems.extend(unit_problems)
        else:
            problems.append(
                f"{feature_path}: named by {manifest} but the feature file is absent"
            )
        by_line = {unit.line: unit for unit in units}

        for line in entry.lines:
            unit = by_line.get(line)
            if unit is not None:
                # The unit's own path is used rather than the manifest's: it
                # points at the file actually found, which is what behave has
                # to be given.
                selected.append(unit)
                continue
            if by_line:
                # The feature parsed and simply has no scenario at that line -
                # an edited feature, or a stale manifest.
                problems.append(
                    f"{feature_path}{LINE_SEPARATOR}{line}: named by {manifest} "
                    "but no scenario is declared at that line"
                )
            selected.append(
                # Kept unenriched rather than dropped.  The name is empty
                # because the manifest carries none and none could be
                # recovered; it is informational only.
                ScenarioRef(feature_path=feature_path, line=line, name="", tags=())
            )

    selected.sort(key=lambda unit: (unit.feature_path, unit.line))
    return selected, problems


# --------------------------------------------------------------------------- #
# Sharding
# --------------------------------------------------------------------------- #


def shard_scenarios(
    scenarios: Sequence[ScenarioRef],
    worker_count: int,
    *,
    base: Path | str | None = None,
) -> list[ShardPlan]:
    """Distribute the selected units over the workers.

    The algorithm, which follows from ``parallel=methods`` (``pom.xml:22``)
    being method- and therefore scenario-level:

        Walk the canonical feature order; within each feature walk its
        selected units in ascending line order; assign each unit round-robin
        across the workers.

    Enumerating grouped by feature is what keeps each worker's runs of a given
    feature contiguous, so that feature's Background executes once per
    scenario **inside the worker that runs it** -- never split across workers
    and never shared between them.

    The function is pure: it performs no I/O beyond computing each shard's
    output path, and the ordering is imposed here rather than assumed of the
    input, so the invariant holds however the caller ordered its list.

    Args:
        scenarios: The selected units.  Duplicate locations are collapsed --
            handing the engine one location twice would run the scenario twice
            and put two copies of it in the report.
        worker_count: Requested worker count; clamped by
            :func:`_effective_worker_count`, so it is never more than the
            number of units and never less than one.
        base: Directory the per-worker output paths hang off, or ``None`` for
            the working directory.

    Returns:
        One :class:`ShardPlan` per worker, in shard order, none of them empty.
        An empty input yields an empty list, and no worker is spawned.
        **Every unit lands in exactly one shard** -- no duplicates, no drops,
        for any worker count.
    """
    unique: dict[str, ScenarioRef] = {}
    for unit in scenarios:
        unique.setdefault(unit.location, unit)
    ordered = sorted(unique.values(), key=lambda unit: (unit.feature_path, unit.line))
    if not ordered:
        return []

    count = _effective_worker_count(worker_count, len(ordered))
    buckets: list[list[ScenarioRef]] = [[] for _ in range(count)]
    for position, unit in enumerate(ordered):
        buckets[position % count].append(unit)

    return [
        ShardPlan(
            index=index,
            locations=tuple(unit.location for unit in bucket),
            # Named in the parent, so its pid is the one embedded and the
            # parent knows every expected path before a child starts.
            output_path=worker_result_path(index, base=base),
        )
        for index, bucket in enumerate(buckets)
    ]


# --------------------------------------------------------------------------- #
# Worker command line
# --------------------------------------------------------------------------- #


def build_worker_command(
    plan: ShardPlan,
    *,
    tags: str | None = None,
    browser: str | None = None,
    dry_run: bool = False,
) -> list[str]:
    """Build the argument list that runs one shard.

    Why the invocation is assembled here rather than configured in
    ``behave.ini``: the engine writes **only** an intermediate result document,
    one per worker, through the custom formatter in
    :mod:`app.reporting.events` -- its native JSON formatter omits fields the
    JVM schema requires.  A static configuration file cannot hand each worker
    a distinct output path, so ``behave.ini`` declares no ``format`` and no
    ``outfiles`` key and both arrive here, per worker, on the command line.  A
    static formatter key there would aim every worker at the same file and the
    merge would silently see one shard's results.

    Exactly one ``--format`` and exactly one ``-o`` appear, because behave
    pairs formatters with output files **positionally**; one of each keeps the
    pairing unambiguous.

    Args:
        plan: The shard to run.  Its locations go last, and its output path is
            the sole ``-o`` value.
        tags: The user's tag expression, or ``None``.  An explicit expression
            is always emitted -- :data:`NEUTRAL_TAG_EXPRESSION` when there is
            no filter -- so ``behave.ini``'s ``default_tags`` can never apply
            implicitly.
        browser: Browser override, forwarded as behave userdata **only when
            one was given**.  The value is deliberately **not validated**: an
            unrecognised browser must fail at first driver use, exactly as the
            Java driver's missing default branch causes today.
        dry_run: Whether to add ``--dry-run``.

    Returns:
        The argument list, to be run with ``cwd`` set to the run base so that
        ``behave.ini`` is discovered, ``features/environment.py`` is found
        relative to ``paths = features``, ``app.reporting.events`` is
        importable and ``configuration.properties`` is read from the working
        directory exactly as the Java reader read it.
    """
    command = [
        sys.executable,
        "-m",
        _BEHAVE_MODULE,
        # Verified by execution to be required; see _NO_SKIPPED_FLAG.
        _NO_SKIPPED_FLAG,
        _FORMAT_FLAG,
        # Never a hard-coded string: the formatter's scoped name is owned by
        # the module that defines the formatter.
        FORMATTER_SCOPED_NAME,
        _OUTFILE_FLAG,
        str(plan.output_path),
        _TAGS_FLAG,
        _effective_tag_expression(tags),
    ]
    if browser is not None:
        command += [_USERDATA_FLAG, f"{_BROWSER_USERDATA_KEY}={browser}"]
    if dry_run:
        command.append(_DRY_RUN_FLAG)
    command += list(plan.locations)
    return command


# --------------------------------------------------------------------------- #
# The target/.workers/ lifecycle
# --------------------------------------------------------------------------- #


def prepare_workers_dir(*, base: Path | str | None = None) -> Path:
    """Create the directory the per-worker result files are written into.

    Args:
        base: Directory it hangs off, or ``None`` for the working directory.

    Returns:
        The created directory.

    Raises:
        OSError: If it cannot be created.  :func:`run_suite` converts that
            into the empty-merge outcome rather than letting it escape, since
            a run whose workers have nowhere to write produces no results.
    """
    return ensure_dir(workers_dir(base))


def cleanup_workers_dir(*, base: Path | str | None = None) -> None:
    """Remove the per-worker directory and everything in it.

    **Idempotent by contract.**  ``app/cli.py`` removes this directory in an
    outer ``finally`` as a belt-and-braces guarantee and :func:`run_suite`
    removes it in its own, so this function is routinely called twice; the
    second call must be a silent no-op.  :mod:`app.utils.paths` deliberately
    never deletes anything, which is why the removal lives here.

    It matters concretely that this happens: ``Jenkins:15`` narrows the
    publisher's ``fileIncludePattern`` to ``target/cucumber.json``, and no
    intermediate worker JSON may be left visible in the workspace.  Emptying
    ``target/`` itself belongs to ``app/cli.py``'s ``--clean``, which covers
    this directory implicitly because it sits inside ``target/``.

    Args:
        base: Directory it hangs off, or ``None`` for the working directory.
    """
    directory = workers_dir(base)
    try:
        shutil.rmtree(directory)
    except FileNotFoundError:
        # Already gone: the idempotent case, and not worth a log line.
        return
    except OSError as error:
        # Never raised onward.  This runs in a ``finally``, where an exception
        # would mask whatever the run was already reporting.
        logger.warning("Could not remove %s: %s", directory, error)


# --------------------------------------------------------------------------- #
# Running the shards
# --------------------------------------------------------------------------- #

#: How many of a shard's locations a diagnostic message names before it
#: abbreviates.  Enough to identify the shard's work without turning a single
#: dead-worker line into eighty.
_MAX_REPORTED_LOCATIONS: Final[int] = 5
_ELLIPSIS: Final[str] = "..."


def _describe(plan: ShardPlan) -> str:
    """Describe a shard for a human-readable diagnostic.

    Args:
        plan: The shard to describe.

    Returns:
        The shard's index, how many scenarios it holds and the first few of
        their locations -- which is what "the incomplete shard is named on
        stderr" requires.
    """
    shown = list(plan.locations[:_MAX_REPORTED_LOCATIONS])
    if len(plan.locations) > _MAX_REPORTED_LOCATIONS:
        shown.append(_ELLIPSIS)
    return (
        f"shard {plan.index} ({len(plan.locations)} scenario(s): {' '.join(shown)})"
    )


def _run_base(base: Path | str | None) -> Path:
    """Resolve the directory each worker runs in.

    Derived from :func:`~app.utils.paths.target_root` rather than resolved
    here, so the rule that ``None`` means the working directory lives in
    exactly one module -- the one that owns every path in the port.

    Args:
        base: The caller's base, or ``None`` for the working directory.

    Returns:
        The run base: the repository root, as an absolute path when ``base``
        was ``None``.
    """
    return target_root(base).parent


def _spawn_worker(
    command: Sequence[str], cwd: Path
) -> subprocess.CompletedProcess[str]:
    """Launch one worker and wait for it.

    The default implementation behind :func:`run_suite`'s ``spawn`` seam.  It
    is the only place in this module that starts a process.

    Args:
        command: The argument list from :func:`build_worker_command`.  Passed
            as a list with no shell, so nothing in a tag expression or a
            location can be interpreted by one.
        cwd: The run base.

    Returns:
        The completed process, with output captured as text.

    Notes:
        ``check=False`` is essential rather than incidental: behave exits
        non-zero when scenarios fail, and :func:`subprocess.run` would raise
        on that, converting a test outcome into an exception in direct
        violation of ``testFailureIgnore=true`` (``pom.xml:25``).

        No timeout is imposed.  A browser suite's duration is not predictable
        -- this one alone carries seventeen fixed sleeps -- and surefire
        imposed none either, so a limit invented here could only kill
        legitimate runs.
    """
    # The suppression on the call below is deliberate: the argv is fixed, no
    # shell is involved, and the program is this very interpreter rather than
    # anything a caller supplied.
    return subprocess.run(
        list(command),
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


def _relay_output(plan: ShardPlan, process: WorkerProcess) -> None:
    """Relay one worker's captured output, tagged with its shard index.

    The tag is what keeps concurrent output readable.  Levels follow the
    stream split ``app/logging_config.py`` installs, so the engine's progress
    reaches stdout and its diagnostics reach stderr, exactly as the CLI
    contract requires.

    Args:
        plan: The shard whose output this is.
        process: The completed worker.
    """
    for line in (getattr(process, "stdout", None) or "").splitlines():
        if line.strip():
            logger.info("[shard %d] %s", plan.index, line)
    for line in (getattr(process, "stderr", None) or "").splitlines():
        if line.strip():
            logger.warning("[shard %d] %s", plan.index, line)


def _run_one_shard(
    plan: ShardPlan,
    *,
    tags: str | None,
    browser: str | None,
    dry_run: bool,
    cwd: Path,
    spawn: SpawnCallable,
) -> ShardResult:
    """Run one shard and classify what happened to it.

    **The behave exit trap, which this function exists to get right.**  behave
    exits non-zero when scenarios fail.  Treating a worker's non-zero return
    code as an error would propagate a test outcome into the exit status,
    breaking ``testFailureIgnore=true`` (``pom.xml:25``) and the six ``-1``
    publisher thresholds (``Jenkins:15``).  So a **positive** non-zero return
    code is completely normal here and is *not* a dead worker.

    A shard is dead only when:

    * the launch itself raised -- typically :exc:`OSError`;
    * the process was terminated by a **signal**, which POSIX reports as a
      negative return code; or
    * its expected result file is absent, empty or unparseable at merge time,
      which :func:`_collect_shard_documents` decides.

    Args:
        plan: The shard to run.
        tags: The user's tag expression, or ``None``.
        browser: Browser override, or ``None``.
        dry_run: Whether to pass ``--dry-run``.
        cwd: The run base, which each worker runs in.
        spawn: The launch seam.

    Returns:
        The shard's result.  Never raises: a launch failure is recorded, not
        propagated, so one bad worker cannot abort a run.
    """
    command = build_worker_command(plan, tags=tags, browser=browser, dry_run=dry_run)
    logger.info(
        "Shard %d starting: %d scenario(s)", plan.index, len(plan.locations)
    )
    try:
        process = spawn(command, cwd)
    except Exception as error:  # noqa: BLE001
        # Deliberately broad: a launch failure is data, not an abort.  The
        # documented case is OSError, but whatever a launch raises, one bad
        # worker must not take the other shards down with it.
        return ShardResult(
            plan=plan,
            returncode=None,
            dead=True,
            reason=f"{_describe(plan)} could not be started ({error!r})",
        )

    _relay_output(plan, process)
    returncode = getattr(process, "returncode", None)
    if isinstance(returncode, int) and returncode < 0:
        # Killed by a signal.  On Windows a killed process reports a large
        # positive status instead, which is indistinguishable from an ordinary
        # failure here; the result-file check catches that case at merge time.
        return ShardResult(
            plan=plan,
            returncode=returncode,
            dead=True,
            reason=(
                f"{_describe(plan)} was terminated by signal {-returncode} "
                "and its results are incomplete"
            ),
        )

    # Alive, whatever the status: see the trap above.
    return ShardResult(plan=plan, returncode=returncode, dead=False, reason=None)


def _run_shards(
    plans: Sequence[ShardPlan],
    *,
    tags: str | None,
    browser: str | None,
    dry_run: bool,
    base: Path | str | None,
    spawn: SpawnCallable,
) -> list[ShardResult]:
    """Run every shard, concurrently, and return the results in shard order.

    The concurrency unit is the **OS process** -- one subprocess per shard,
    which is what makes this port's deviation from ``useUnlimitedThreads``
    process-based.  The threads exist only to wait on those children, so they
    hold no test state and do no work; a :class:`ProcessPoolExecutor` wrapped
    around a subprocess launch would buy nothing.

    Args:
        plans: The shards, in shard order.
        tags: The user's tag expression, or ``None``.
        browser: Browser override, or ``None``.
        dry_run: Whether to pass ``--dry-run``.
        base: Directory the run resolves against.
        spawn: The launch seam.

    Returns:
        One result per shard, ordered by shard index rather than by completion
        order, so everything downstream is deterministic.
    """
    cwd = _run_base(base)
    if len(plans) == 1:
        # --workers 1 is the sequential mode, and takes no pool at all.
        return [
            _run_one_shard(
                plans[0],
                tags=tags,
                browser=browser,
                dry_run=dry_run,
                cwd=cwd,
                spawn=spawn,
            )
        ]

    collected: dict[int, ShardResult] = {}
    with ThreadPoolExecutor(
        max_workers=len(plans), thread_name_prefix="testinium-qa-shard"
    ) as pool:
        futures = {
            pool.submit(
                _run_one_shard,
                plan,
                tags=tags,
                browser=browser,
                dry_run=dry_run,
                cwd=cwd,
                spawn=spawn,
            ): plan
            for plan in plans
        }
        # Output is relayed as each worker finishes, which is what keeps a
        # long run informative rather than silent until the end.
        for future in as_completed(futures):
            plan = futures[future]
            try:
                result = future.result()
            except Exception as error:  # noqa: BLE001
                # Deliberately broad, and the last line of defence:
                # supervision must never abort the run, so anything the task
                # failed with becomes a dead shard instead of an exception.
                result = ShardResult(
                    plan=plan,
                    returncode=None,
                    dead=True,
                    reason=(
                        f"{_describe(plan)} failed while being supervised "
                        f"({error!r})"
                    ),
                )
            collected[result.plan.index] = result

    return [collected[plan.index] for plan in plans]


# --------------------------------------------------------------------------- #
# Merging
# --------------------------------------------------------------------------- #


def _collect_shard_documents(
    shard_results: Sequence[ShardResult],
) -> tuple[list[dict[str, Any]], list[ShardResult], list[str]]:
    """Load each live shard's result file, promoting failures to dead shards.

    This is where the third dead-worker condition is decided, because it is
    the first moment it can be: a worker's result file may be absent, empty or
    unparseable however cleanly the process exited.  The result collector
    opens its output file eagerly, which is what makes the distinction below
    meaningful -- an **empty** file means a worker started and died, an
    **absent** one means it never got that far.

    Args:
        shard_results: The results from :func:`_run_shards`, in shard order.

    Returns:
        A ``(documents, shard_results, problems)`` triple.  ``documents``
        holds one result document per shard that completed, in shard order,
        and a dead shard contributes nothing to it.  ``shard_results`` is the
        input with merge-time deaths recorded.  ``problems`` holds every dead
        shard's reason, including those already dead on arrival, so a caller
        that only wants the messages gets all of them.
    """
    documents: list[dict[str, Any]] = []
    updated: list[ShardResult] = []
    problems: list[str] = []

    for result in shard_results:
        if result.dead:
            updated.append(result)
            if result.reason:
                problems.append(result.reason)
            continue

        path = result.plan.output_path
        reason: str | None = None
        try:
            if not path.exists():
                reason = (
                    f"{_describe(result.plan)} wrote no result file at {path}; "
                    "its scenarios are missing from the report"
                )
            elif path.stat().st_size == 0:
                reason = (
                    f"{_describe(result.plan)} wrote an empty result file at "
                    f"{path}; the worker started but did not finish"
                )
        except OSError as error:
            reason = (
                f"{_describe(result.plan)} has a result file that cannot be "
                f"inspected at {path} ({error})"
            )

        if reason is None:
            try:
                documents.append(load_result_set(path))
            except ResultSetError as error:
                reason = (
                    f"{_describe(result.plan)} produced an unusable result "
                    f"set ({error})"
                )

        if reason is None:
            updated.append(result)
        else:
            updated.append(replace(result, dead=True, reason=reason))
            problems.append(reason)

    return documents, updated, problems


def _canonical_feature_keys(base: Path | str | None) -> tuple[str, ...]:
    """Return the canonical feature order as merge-comparable identities.

    Args:
        base: Directory the features directory hangs off.

    Returns:
        The repository-relative feature paths, sorted -- the same order
        :func:`select_scenarios` walked.  An empty tuple when the directory
        cannot be listed, which makes the reordering below a no-op rather than
        an error.
    """
    paths, _ = _feature_files(base)
    return tuple(_feature_path_of(path) for path in paths)


def _reorder_features(
    document: dict[str, Any], canonical: Sequence[str]
) -> dict[str, Any]:
    """Put the merged document's features into canonical order.

    Defensive rather than corrective: :func:`~app.reporting.events.merge_result_sets`
    already orders features by ascending feature path, which for this suite
    *is* the canonical order, so on a real run this changes nothing.  It earns
    its place on hand-built inputs -- a shard document that carries only a
    ``uri`` and no ``path`` is keyed by the ``file:``-prefixed string, which
    sorts into a different place than a plain path, and a mixture of the two
    would otherwise interleave.  This is a reordering, never a second merge:
    no element is added, removed or altered.

    Args:
        document: The merged document, modified in place.
        canonical: The canonical feature identities.

    Returns:
        The same document, for convenient chaining.
    """
    if not canonical:
        return document

    rank: dict[str, int] = {}
    for position, path in enumerate(canonical):
        rank[path] = position
        rank[f"{FILE_URI_SCHEME}{path}"] = position

    features = document.get("features") or []
    unknown = len(canonical)

    def _key(item: tuple[int, Any]) -> tuple[int, int]:
        position, feature = item
        identity = ""
        if isinstance(feature, dict):
            for field_name in ("path", "uri"):
                value = feature.get(field_name)
                if isinstance(value, str) and value:
                    identity = value
                    break
        # A feature the canonical list does not name keeps its incoming
        # position, after everything that is named.
        return (rank.get(identity, unknown), position)

    document["features"] = [
        feature for _, feature in sorted(enumerate(features), key=_key)
    ]
    return document


def _merge_documents(
    documents: Sequence[dict[str, Any]],
    *,
    base: Path | str | None,
) -> dict[str, Any] | None:
    """Merge shard documents into the one document the writers consume.

    The merge algorithm itself is **not** implemented here: it belongs to
    :func:`~app.reporting.events.merge_result_sets`, which groups features by
    path, keeps each Background immediately in front of its scenario, orders
    elements by test-case line and deep-copies everything.  That is what makes
    a feature split across two shards come out as one feature containing each
    of its scenarios exactly once -- verified on real shard files rather than
    assumed.

    Args:
        documents: The readable shard documents, in shard order.
        base: Directory used to recover the canonical feature order.

    Returns:
        The merged document, or ``None`` when there was nothing to merge.
        ``None`` is returned rather than an empty document because the two
        mean different things to the exit contract, and only the caller knows
        which situation it is in.
    """
    if not documents:
        return None
    return _reorder_features(
        merge_result_sets(documents), _canonical_feature_keys(base)
    )


def merge_worker_results(
    shard_results: Sequence[ShardResult],
    *,
    base: Path | str | None = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Load and merge the shards' results.

    Args:
        shard_results: The shards to merge, in shard order.
        base: Directory the per-worker files and the features directory hang
            off, or ``None`` for the working directory.

    Returns:
        A ``(result_set, problems)`` pair.  ``result_set`` is the merged
        document, or ``None`` when not one shard file could be read -- which
        the caller turns into the empty-merge outcome only when scenarios were
        actually selected.  ``problems`` names every dead shard.
    """
    documents, _, problems = _collect_shard_documents(shard_results)
    return _merge_documents(documents, base=base), problems


# --------------------------------------------------------------------------- #
# The run
# --------------------------------------------------------------------------- #


def run_suite(
    *,
    tags: str | None = None,
    browser: str | None = None,
    workers: int | None = None,
    dry_run: bool = False,
    rerun: bool = False,
    base: Path | str | None = None,
    spawn: SpawnCallable | None = None,
) -> RunOutcome:
    """Select, shard, execute and merge -- the whole run, in one call.

    This is the function that makes the suite actually execute, which the
    source build's configuration never did (see the module docstring).  It
    exits no process and raises nothing for a test outcome; everything it
    learns is reported through :class:`RunOutcome`, which ``app/cli.py`` turns
    into a status.

    Args:
        tags: Tag expression, or ``None`` for no filter.  ``app/cli.py``
            passes ``"@Smoke"`` by default and ``None`` under ``--rerun``.
        browser: Browser override forwarded to each worker as behave
            userdata, or ``None`` to leave the choice to
            ``configuration.properties``.  Not validated here: an
            unrecognised value must fail at first driver use, as it does
            today.
        workers: Worker count, or ``None`` for :func:`default_worker_count`.
            ``1`` runs sequentially.  Never exceeds the number of selected
            scenarios.
        dry_run: Whether to ask the engine not to execute steps.
        rerun: Whether to take the scenarios from the rerun manifest instead
            of from the feature files.  A rerun applies **no** tag filter,
            because ``FailedTestRunner`` declared none, and it writes **no**
            artifacts, because that runner declared an empty plugin list --
            so ``app/cli.py`` must not invoke the report service for one.
            ``--rerun`` combined with ``--tags`` is a usage error, rejected by
            ``app/cli.py``; should a filter arrive here anyway it is ignored
            rather than honoured, because honouring it is precisely how a
            rerun silently skips the failures it exists to re-run.
        base: Directory every path resolves against, or ``None`` for the
            working directory.  The injection seam the unit suite uses.
        spawn: **Test-only seam.**  A callable taking the argument list and
            the working directory and returning something shaped like a
            completed process.  Defaults to the real subprocess launch.

    Returns:
        The outcome.  Every signal is independent and no precedence is
        encoded: exit-code precedence, when several coexist, is
        ``app/cli.py``'s decision.

    Raises:
        TagExpressionError: If ``tags`` is malformed.  The one propagating
            failure, because an invalid option value is a usage error rather
            than a test outcome; see :func:`_parse_tag_expression`.
    """
    spawn_worker: SpawnCallable = _spawn_worker if spawn is None else spawn
    # What the run records, as against what goes on the command line: the
    # neutral tautology is a mechanism and must never surface in a report.
    recorded_tags = _recorded_tag_expression(tags, rerun)

    if rerun:
        selected, problems = select_rerun_scenarios(base=base)
    else:
        selected, problems = select_scenarios(tags=tags, base=base)

    # Tolerated failures: reported on stderr and survived, never raised and
    # never counted as a dead worker.  Each leaves the run at status 0,
    # matching the source's own tolerance of a missing configuration file.
    for message in problems:
        logger.error("%s", message)
    parse_errors = list(problems)

    selected_count = len(selected)
    if rerun:
        source = "the rerun manifest"
    elif recorded_tags is None:
        source = "the whole suite (no tag filter)"
    else:
        source = f"the tag expression {recorded_tags!r}"
    logger.info("Selected %d scenario(s) from %s", selected_count, source)

    if selected_count == 0:
        # STATE ONE of the two that must never be conflated: nothing was
        # selected.  The result set is EMPTY, not missing, because the exit
        # row is "zero scenarios selected -> status 0 -> all four artifacts
        # written, empty" - the Jenkins publisher always gets a JSON to read.
        # merge_produced_nothing stays False; a falsy check on result_set
        # would collapse this state into the empty-merge one below and break
        # both rows.  No worker is spawned.
        return RunOutcome(
            result_set=(
                None
                if rerun
                else new_result_set(dry_run=dry_run, tag_expression=recorded_tags)
            ),
            selected_count=0,
            worker_count=0,
            shard_results=(),
            dead_shards=(),
            parse_errors=tuple(parse_errors),
            merge_produced_nothing=False,
            rerun=rerun,
            dry_run=dry_run,
            tag_expression=recorded_tags,
        )

    worker_count = _effective_worker_count(workers, selected_count)
    plans = shard_scenarios(selected, worker_count, base=base)
    logger.info(
        "Sharding %d scenario(s) over %d worker(s)", selected_count, len(plans)
    )

    try:
        prepare_workers_dir(base=base)
    except OSError as error:
        # Nowhere for the workers to write, so nothing can be produced.  This
        # is not a tolerated failure and not a dead worker: it is the
        # empty-merge state, reached without spawning anything.
        message = (
            f"{workers_dir(base)}: the per-worker directory cannot be created "
            f"({error}); no scenario was executed"
        )
        logger.error("%s", message)
        parse_errors.append(message)
        return RunOutcome(
            result_set=None,
            selected_count=selected_count,
            worker_count=0,
            shard_results=(),
            dead_shards=(),
            parse_errors=tuple(parse_errors),
            merge_produced_nothing=not rerun,
            rerun=rerun,
            dry_run=dry_run,
            tag_expression=recorded_tags,
        )

    try:
        shard_results = _run_shards(
            plans,
            # A rerun carries no filter, so the workers get the neutral
            # expression and behave.ini's default cannot apply.
            tags=None if rerun else tags,
            browser=browser,
            dry_run=dry_run,
            base=base,
            spawn=spawn_worker,
        )
        documents, shard_results, _ = _collect_shard_documents(shard_results)
        merged = _merge_documents(documents, base=base)
    finally:
        # Removed whether the merge succeeded or failed, so no intermediate
        # worker JSON is ever left where the Jenkins publisher could see it.
        # Idempotent, because app/cli.py removes it again in an outer finally.
        cleanup_workers_dir(base=base)

    # A dead shard is reported even if the caller ignores the field, and it
    # never suppresses artifacts: the merged set from the shards that did
    # complete is still returned.
    dead_shards = tuple(
        result.reason for result in shard_results if result.dead and result.reason
    )
    for reason in dead_shards:
        logger.error("%s", reason)

    if merged is not None:
        # Only the two run-level fields the merge cannot know.  started_at,
        # generated_at and metadata belong to the merge and the writers and
        # are deliberately left alone.
        merged["tag_expression"] = recorded_tags
        merged["dry_run"] = bool(dry_run)

    if rerun:
        # A rerun writes no artifacts at all, so it publishes no document.
        # This None is NOT the empty-merge condition - see the module
        # docstring's note to app/cli.py.
        result_set: dict[str, Any] | None = None
        merge_produced_nothing = False
    else:
        result_set = merged
        # STATE TWO: scenarios were selected and not one worker file could be
        # read.  Distinct from STATE ONE above in both value and consequence -
        # non-zero, and target/ left exactly as the clean step left it.
        merge_produced_nothing = merged is None

    logger.info(
        "Run finished: %d scenario(s) over %d worker(s), %d dead",
        selected_count,
        len(plans),
        len(dead_shards),
    )
    return RunOutcome(
        result_set=result_set,
        selected_count=selected_count,
        worker_count=len(plans),
        shard_results=tuple(shard_results),
        dead_shards=dead_shards,
        parse_errors=tuple(parse_errors),
        merge_produced_nothing=merge_produced_nothing,
        rerun=rerun,
        dry_run=dry_run,
        tag_expression=recorded_tags,
    )
