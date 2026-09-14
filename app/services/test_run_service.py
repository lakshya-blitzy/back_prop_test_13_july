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
run." and BUILD SUCCESS, leaving the build-output directory with nothing but
``classes``, ``generated-sources`` and ``maven-status``: no JSON report, no
rerun manifest, no HTML.  The surefire block is **latent configuration**.

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
  in that way, so concurrency here is a **process pool sized at the CPU
  count** -- the model AAP deviation D04 records and AAP 0.1.2 names
  ("Process-pool runner; CPU-count default"), built for every real run by
  :func:`_run_shards` as a :class:`~concurrent.futures.ProcessPoolExecutor`
  over :func:`_run_shard_task`.  :func:`default_worker_count` supplies the
  default size and :func:`_effective_worker_count` the ceiling: *uncapped* is
  the one thing the port must not be, because a worker here is an OS process
  driving a browser rather than a thread in a JVM, so an explicit
  ``--workers`` request is bounded at four processes per CPU.  That whole
  model is a recorded deviation from the source's execution model, not a
  translation of it.
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

That seam is also the one thing that selects the supervision mechanism, and
the reason two exist.  A stub or a mock **cannot cross a process boundary** --
it is not picklable -- so an injected ``spawn`` is supervised by threads
inside the calling process, where the stub lives and where a test can observe
it.  An injected seam is therefore the *only* way the thread path is reached;
a real run always uses the process pool D04 prescribes.  Both paths call
:func:`_run_one_shard`, produce exactly one :class:`ShardResult` per shard and
order them by shard index, so nothing downstream can tell them apart.

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

Which is why there is a third signal, independent of both:
``infrastructure_error`` says that *this port's own* intermediate storage
failed -- the per-worker directory could not be created, so nothing executed,
or it could not be removed afterwards, so intermediate documents remain in
the workspace.  Neither is a test outcome and neither is expressible through
the two states above: a rerun that cannot create its directory executes no
selected scenario while looking exactly like the rerun that succeeded, which
is the one case where "a rerun writes nothing" must not be read as "a rerun
went fine".  ``app/cli.py`` therefore reads this field **before** its rerun
short-circuit, and maps it onto its artifact-failure status.

One emitter per incident
------------------------
:class:`RunOutcome` is also how this module *reports*, and the division is
exact: **a fact the outcome carries is logged by the command that reads the
outcome, and never here as well.**  A tolerated selection problem travels on
``parse_errors``, a dead shard's reason on ``dead_shards``, and ``app/cli.py``
is the single emitter of the record that names each one, beside the exit class
it implies.  Logging them here too - which this module did, until the
duplication was reviewed - puts one incident in the CI console twice under two
logger names, so a reader counting ERROR records over-counts the run and
neither layer is the canonical account of anything.

What stays here is the progress this module alone knows and no outcome field
carries: how many scenarios were selected and from what, how the shards were
sized, which shard started, and the closing counts.  That is INFO on stdout,
by the split ``app/logging_config.py`` installs.  The one exception to the
rule, and the reason it is stated as a rule about *facts* rather than about
layers, belongs to the sibling service: ``app/services/report_service.py``
emits the writer-failure cause with its traceback at the point the exception
is caught, because a traceback is the one thing an outcome cannot usefully
carry to a later reader.
"""

import contextvars
import logging
import multiprocessing
import os
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import (
    Executor,
    Future,
    ProcessPoolExecutor,
    ThreadPoolExecutor,
    as_completed,
)

# The pool's own failure mode, and the reason it is imported from the
# submodule: ``concurrent.futures`` re-exports ``BrokenExecutor`` but not this
# subclass (measured on this interpreter), and catching the subclass is what
# lets a lost pool worker be reported as the shard it was running.
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, replace
from pathlib import Path
from types import FrameType
from typing import IO, Any, Final, Protocol, runtime_checkable

# behave's own parser, deliberately rather than a hand-rolled Gherkin reader:
# selection must not be able to disagree with what the engine will actually
# run.  ``ParserError`` is what a malformed feature file raises (measured).
from behave.parser import ParserError, parse_file

# The same tag-expression grammar the JVM used (``tag-expressions:4.1.0``
# there, ``cucumber-tag-expressions`` 11.0.1 here).  Only the parser is
# imported: ``TagExpressionError`` is documented as the one exception the
# selection functions propagate, and it is deliberately never caught here.
from cucumber_tag_expressions import TagExpressionParser

# The rendering half of the worker relay.  ``app/logging_config.py`` owns this
# port's console-logging contract -- the stream split this module's levels
# depend on, and the sanitizing, bounding and redacting of untrusted text --
# so the relay below constructs no safe text of its own.  Importing it here is
# cycle-free by construction: that module imports only the standard library
# and nothing under ``app``.
from app.logging_config import render_worker_line
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
    "reclaim_workers_root",
    "run_directory_is_active",
    "run_directory_owner",
    "run_suite",
    "select_rerun_scenarios",
    "select_scenarios",
    "shard_scenarios",
    "terminate_live_workers",
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
    three attributes.  That is also why ``output_relayed`` below is **not** a
    member of this protocol: adding it would stop a plain
    :class:`~subprocess.CompletedProcess` from satisfying the protocol, so it
    is an *optional* convention read with :func:`getattr` instead.

    Attributes:
        returncode: The engine's exit status.  **A positive non-zero value is
            normal** -- behave exits ``1`` when scenarios fail -- and is never
            treated as an error here.
        stdout: Standard output, or ``None``.  Relayed as run progress by
            :func:`_relay_output` unless the launch already relayed it live.
        stderr: Standard error, or ``None``.  Relayed as engine diagnostics
            under the same rule.

    Notes:
        A launch that has *already* relayed its output line by line -- which
        the real launch does, see :func:`_spawn_worker` -- says so by carrying
        a true ``output_relayed`` attribute, and :func:`_relay_output` then
        prints nothing.  Without that marker every line would appear twice,
        once live and once after the fact; with it, the two paths (live
        relaying, and a stubbed result relayed afterwards) coexist without
        either one changing what a reader sees.
    """

    returncode: int | None
    stdout: str | None
    stderr: str | None


#: Signature of the ``spawn`` seam: it takes the argument list and the working
#: directory, and returns something shaped like a completed process.  Two
#: positional arguments, deliberately and permanently: a shard's identity does
#: **not** travel through this signature, so a stub written against it keeps
#: working.  The shard label a relayed line is tagged with travels through
#: :data:`_shard_output_label` instead.
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
            document: the run's own directory from
            :func:`prepare_workers_dir`, joined with the file name
            :func:`~app.utils.paths.worker_result_path` builds.  Computed in
            the **parent**, so the parent knows every expected path before a
            single child starts and can tell an absent file from an
            unreadable one at merge time -- and confined to this run's
            directory, so no path here can ever name a file another run wrote.
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
        infrastructure_error: Why this port's own intermediate storage failed,
            or ``None`` when it did not.  Two causes, and they are reported
            through one field because they carry one consequence -- the run
            cannot report success:

            * the per-worker directory could not be **created**, so no
              scenario was executed at all; or
            * a per-worker directory could not be **removed** afterwards, so
              intermediate result documents remain in a workspace whose
              publisher glob is narrowed (``Jenkins:15``) precisely because
              nothing intermediate may be read by it.

            ``app/cli.py`` reads this **before** its ``rerun`` short-circuit,
            because a rerun writes no artifacts by design and would otherwise
            hide a rerun that executed nothing; and it reads it **after** the
            report fan-out when a document exists, because a cleanup failure
            must not cost a completed run its four artifacts.  Which of the
            two happened is legible from the two fields above: a creation
            failure leaves ``result_set`` ``None`` with ``worker_count`` at
            ``0``, while a removal failure leaves the merged document intact.
            Defaulted, so every existing construction of this class stays
            valid and the field means "nothing went wrong" by omission.
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
    infrastructure_error: str | None = None


# --------------------------------------------------------------------------- #
# Worker count
#
# The default is the CPU count; the ceiling below exists because a worker here
# is an OS process driving a browser, so an unbounded --workers value would
# spawn one process, one browser and one driver per scenario.
# --------------------------------------------------------------------------- #

#: How many worker processes one CPU may carry before a request is capped.
#:
#: **Why more than one per CPU is legitimate.**  A worker spends almost all of
#: its wall time waiting -- for a browser to start, for a page to load, and in
#: the seventeen fixed sleeps this suite preserves -- so it is I/O-bound and
#: oversubscribing the CPUs is the whole point of running in parallel at all.
#: Four is that oversubscription expressed as a policy: generous enough that
#: no realistic machine is starved of concurrency, small enough that the
#: browsers still fit in memory.
_WORKERS_PER_CPU: Final[int] = 4

#: Floor under the ceiling, so a single-CPU machine (a container with one
#: allotted core is the common case) can still oversubscribe modestly rather
#: than being pinned to four workers.
_MINIMUM_WORKER_CEILING: Final[int] = 8


def default_worker_count() -> int:
    """Return the default number of workers.

    The CPU count is this port's stand-in for ``useUnlimitedThreads=true``
    (``pom.xml:23``).  It is a deviation rather than a translation -- see the
    module docstring -- and the commented-out ``<threadCount>4</threadCount>``
    at ``pom.xml:24`` records what the source replaced with the uncapped
    setting.

    The default is **never** capped by :func:`_worker_ceiling`: the ceiling
    bounds what a user may ask for, and the CPU count is by construction
    already inside it.

    Returns:
        :func:`os.cpu_count`, or ``1`` where the platform cannot report it.
    """
    return os.cpu_count() or 1


def _worker_ceiling() -> int:
    """Return the largest worker count this machine will be asked to run.

    Derived from the CPU count rather than fixed, so the bound follows the
    hardware instead of a number invented here, and floored so the smallest
    machine still gets useful concurrency.

    Returns:
        ``max(_WORKERS_PER_CPU * default_worker_count(),
        _MINIMUM_WORKER_CEILING)`` -- on a 12-CPU machine, 48; on a 1-CPU
        machine, 8.
    """
    return max(_WORKERS_PER_CPU * default_worker_count(), _MINIMUM_WORKER_CEILING)


def _effective_worker_count(requested: int | None, selected_count: int) -> int:
    """Clamp a requested worker count to something spawnable.

    Three bounds apply, in this order: the CPU-derived ceiling from
    :func:`_worker_ceiling` (explicit requests only), the number of selected
    units, and a floor of one.  The ceiling is what stops ``--workers 5000``
    on a large suite from becoming one process, one browser and one driver per
    scenario; capping is reported at ``WARNING`` rather than applied silently,
    because the user asked for something the run did not do.

    Args:
        requested: The caller's ``--workers`` value, or ``None`` for the
            default.  A non-positive value is treated as "unspecified" rather
            than rejected: validating an option value belongs to
            ``app/cli.py``, and this service stays tolerant.
        selected_count: How many executable units were selected.

    Returns:
        ``0`` when nothing was selected, so no worker is spawned at all.
        Otherwise at least ``1``, never more than ``selected_count`` -- a
        worker is never given an empty shard -- and, for an explicit request,
        never more than :func:`_worker_ceiling`.  ``1`` is the sequential
        mode, and both it and the ``None`` default pass through untouched.
    """
    if selected_count <= 0:
        return 0
    if requested is None or requested <= 0:
        resolved = default_worker_count()
    else:
        resolved = requested
        ceiling = _worker_ceiling()
        if resolved > ceiling:
            logger.warning(
                "Requested %d worker(s), which exceeds this machine's ceiling "
                "of %d (%d CPU(s) x %d worker(s) per CPU, minimum %d); "
                "running %d instead",
                requested,
                ceiling,
                default_worker_count(),
                _WORKERS_PER_CPU,
                _MINIMUM_WORKER_CEILING,
                ceiling,
            )
            resolved = ceiling
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

    This is the port of ``FailedTestRunner``, whose whole configuration was a
    ``features`` declaration naming the rerun manifest as its feature source
    (``FailedTestRunner.java:11``).  The manifest is parsed by
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
        A ``(selected, problems)`` pair.  Every location the parser hands
        over is selected, enriched with the scenario's name and effective tags
        where the feature file still declares one at that line.  A location
        whose line no longer names a scenario is **kept anyway** and noted in
        ``problems``: dropping it would silently discard a failure, which is
        the one thing a rerun must not do.  A location whose feature file does
        not resolve inside the features directory never arrives here at all --
        :func:`~app.reporting.rerun_report.parse_rerun_file` applies that
        confinement tier itself and drops such a line with a warning on
        stderr, which is the tolerated-manifest row of the AAP 0.4.1 exit
        table -- so the absent-file branch below is reached only when the file
        disappears between that check and this read.  A missing, unreadable or
        malformed manifest yields no scenarios and a problem message, never an
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
            # Reached on a race rather than on a stale manifest: the parser's
            # confinement tier already dropped any entry whose feature file
            # did not resolve, so arriving here means the file went away
            # between that resolution and this read.
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
    run_dir: Path | str | None = None,
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
        run_dir: This run's own intermediate directory, from
            :func:`prepare_workers_dir`, which every shard's output file is
            placed inside.  ``None`` puts the files straight into the shared
            directory :mod:`app.utils.paths` names, which is what a caller
            sharding without a prepared run -- a test asserting the
            exactly-once invariant, say -- gets, and is why the parameter has
            a default at all.  :func:`run_suite` always supplies one, so no
            executed run ever writes into the shared directory: see the
            lifecycle comment above :func:`prepare_workers_dir` for the stale
            reuse and the cross-run deletion that would otherwise follow.

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
            # parent knows every expected path before a child starts.  The
            # file *name* always comes from app/utils/paths, the port's owner
            # of every artifact name; only the directory it sits in is this
            # run's, which is what isolates concurrent runs from each other.
            output_path=_shard_output_path(index, base=base, run_dir=run_dir),
        )
        for index, bucket in enumerate(buckets)
    ]


def _shard_output_path(
    index: int,
    *,
    base: Path | str | None,
    run_dir: Path | str | None,
) -> Path:
    """Return where one shard writes its intermediate result document.

    Args:
        index: The shard's zero-based index, which the file name carries.
        base: Directory the shared intermediate directory hangs off.
        run_dir: This run's own directory, or ``None`` to use the shared one.

    Returns:
        The run directory joined with the file name
        :func:`~app.utils.paths.worker_result_path` builds -- taking the name
        from that function rather than re-spelling it, so the pid-plus-index
        naming rule stays in the module that owns it -- or that function's own
        path when no run directory was supplied.
    """
    named = worker_result_path(index, base=base)
    if run_dir is None:
        return named
    return Path(run_dir) / named.name


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
# The per-worker intermediate directory lifecycle
#
# This module is the **single owner** of that directory: it is the only place
# in the port that creates one or removes one, and ``app/cli.py`` reaches both
# operations only through the two functions below.  One owner is the point.
# Two owners, each swallowing its own failure, is how a command returns ``0``
# with intermediate documents still sitting in a workspace whose publisher
# glob (``Jenkins:15``) is narrowed to one file precisely because nothing
# intermediate may be publishable.
#
# Every run gets its **own** directory inside the shared one, and that is not
# tidiness either.  Two things go wrong with a single shared directory:
#
# * **Stale reuse.**  A worker file's name carries a process id and a shard
#   index (``app/utils/paths.worker_result_path``).  Both repeat -- process
#   ids are recycled and shard indices start at zero every run -- so a file a
#   previous run failed to remove can be read by a later run as that run's own
#   result, before the new worker has written anything over it.
# * **Cross-run deletion.**  Two runs in one checkout would share the
#   directory, and whichever finished first would delete the other's files,
#   turning live results into "the worker wrote no result file".
#
# A per-run directory removes both: nothing a run reads was written by another
# run, and nothing a run deletes belongs to another run.  The shared parent is
# only ever removed when it is empty, which is exactly when no other run holds
# anything in it.
# --------------------------------------------------------------------------- #

#: Separator between the two parts of a run directory's name.  The name itself
#: is built from runtime values only -- this process's id and a random token --
#: so no *path* literal is introduced here: :mod:`app.utils.paths` remains the
#: owner of every directory and file name in the port, and the per-worker file
#: name inside a run directory is still taken from
#: :func:`~app.utils.paths.worker_result_path` rather than spelled out.
_RUN_DIR_NAME_SEPARATOR: Final[str] = "-"

#: Bytes of randomness in a run directory's name.  Six bytes is twelve hex
#: characters: enough that two runs started in the same second by the same
#: recycled process id cannot collide, short enough to read in a log line.
_RUN_DIR_TOKEN_BYTES: Final[int] = 6

#: Every run directory this process created and has not yet removed, so that
#: :func:`cleanup_workers_dir` called with no argument -- the call
#: ``app/cli.py`` makes on every exit path -- knows precisely what this
#: invocation is responsible for, and therefore never touches another run's.
_active_run_dirs: set[Path] = set()
_active_run_dirs_lock: Final[threading.Lock] = threading.Lock()


def _run_dir_name() -> str:
    """Return a name no other run will use.

    Returns:
        This process's id and a random token, joined by
        :data:`_RUN_DIR_NAME_SEPARATOR` -- ``"48123-9f2c1ab77d04"`` and the
        like.  The process id makes the directory identifiable in a log or an
        ``ls`` while a run is in progress, and :func:`run_directory_owner`
        reads it back so that a *live* run's directory is never deleted by
        anything else; the token is what makes the name unique even when that
        id has been recycled.
    """
    token = secrets.token_hex(_RUN_DIR_TOKEN_BYTES)
    return f"{os.getpid()}{_RUN_DIR_NAME_SEPARATOR}{token}"


def run_directory_owner(name: str) -> int | None:
    """Return the process id a run directory's name carries.

    Args:
        name: A single path component from inside the shared intermediate
            directory.

    Returns:
        The process id that created it, or ``None`` when the name was not
        produced by :func:`_run_dir_name` -- which is how anything else found
        in that directory is told apart from a run's own working space.
    """
    pid_text, separator, token = name.partition(_RUN_DIR_NAME_SEPARATOR)
    if not separator or not pid_text.isdigit():
        return None
    if len(token) != _RUN_DIR_TOKEN_BYTES * 2:
        return None
    try:
        int(token, 16)
    except ValueError:
        return None
    return int(pid_text)


def _process_is_alive(pid: int) -> bool:
    """Return whether a process id still belongs to a running process.

    The test is deliberately **fail-safe**: a platform this cannot ask, or an
    answer it cannot interpret, reports ``True``, because the consequence of
    wrongly believing a run is alive is a stale directory left for the next
    clean to remove, while the consequence of wrongly believing it is dead is
    deleting the results of a run that is still writing them.

    Args:
        pid: The process id to test.

    Returns:
        ``True`` when the process exists or cannot be ruled out.

    Notes:
        :func:`os.kill` is used on POSIX only.  On Windows it does not probe:
        any signal number other than the two console events **terminates**
        the target, so the probe there is a synchronisation-handle open and a
        zero-timeout wait, which observes the process without touching it.
    """
    if pid <= 0:
        return False
    if _HAS_PROCESS_GROUPS:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            # Alive, and owned by somebody else.
            return True
        except OSError as error:
            logger.debug("Could not probe process %d: %s", pid, error)
            return True
        return True
    return _windows_process_is_alive(pid)


def _windows_process_is_alive(pid: int) -> bool:
    """Return whether a Windows process id is still running.

    Args:
        pid: The process id to test.

    Returns:
        ``True`` when a handle can be opened and the process has not
        signalled, and when the probe itself is unavailable -- see
        :func:`_process_is_alive` for why the unknown case is ``True``.
    """
    try:
        import ctypes  # noqa: PLC0415 - Windows only, and only on this path
    except ImportError:  # pragma: no cover - ctypes ships with CPython
        return True

    kernel32 = getattr(ctypes, "windll", None)
    if kernel32 is None:  # pragma: no cover - not Windows
        return True

    synchronize = 0x00100000
    wait_timeout = 0x00000102
    handle = kernel32.kernel32.OpenProcess(synchronize, False, pid)
    if not handle:
        # No handle: the process is gone, or it is not ours to observe.  The
        # second case is indistinguishable here, so the fail-safe answer is
        # the one the last-error code gives: only "invalid parameter" means
        # the id does not exist.
        return kernel32.kernel32.GetLastError() != 87
    try:
        return kernel32.kernel32.WaitForSingleObject(handle, 0) == wait_timeout
    finally:
        kernel32.kernel32.CloseHandle(handle)


def run_directory_is_active(directory: Path) -> bool:
    """Return whether a run directory belongs to a run still in progress.

    This is what lets one invocation's ``--clean`` empty the build output
    without destroying another invocation's live intermediates, which is the
    concurrency defect the per-run directory alone does not fix: the clean
    step removes the shared directory's contents, and without this test it
    would remove a running sibling's results and turn them into missing
    worker files.

    Args:
        directory: A child of the shared intermediate directory.

    Returns:
        ``True`` when the name is a run directory whose creating process is
        still running -- this process's own current run included -- and
        ``False`` for anything else, which is therefore reclaimable: an
        abandoned run's leftovers, and anything in that directory that a run
        did not create.
    """
    owner = run_directory_owner(directory.name)
    if owner is None:
        return False
    with _active_run_dirs_lock:
        if directory in _active_run_dirs:
            return True
    return _process_is_alive(owner)


def prepare_workers_dir(*, base: Path | str | None = None) -> Path:
    """Create this run's own directory for its per-worker result files.

    The directory is a fresh, uniquely named child of the shared
    per-worker intermediate directory :mod:`app.utils.paths` names, created on
    every call -- see the section comment above for the two failure modes a
    shared directory has.  It is registered as this process's responsibility,
    so :func:`cleanup_workers_dir` can remove what this invocation created
    without having to guess.

    Args:
        base: Directory it hangs off, or ``None`` for the working directory.

    Returns:
        The created directory, which the caller passes to
        :func:`shard_scenarios` so every shard's output path lands inside it.

    Raises:
        OSError: If it cannot be created.  :func:`run_suite` converts that
            into an outcome carrying
            :attr:`RunOutcome.infrastructure_error` rather than letting it
            escape, since a run whose workers have nowhere to write executes
            nothing and must not report success.
    """
    directory = ensure_dir(workers_dir(base) / _run_dir_name())
    with _active_run_dirs_lock:
        _active_run_dirs.add(directory)
    return directory


def _prune_workers_root(base: Path | str | None) -> None:
    """Remove the shared intermediate directory if nothing is left in it.

    ``rmdir`` and never ``rmtree``: it succeeds only on an empty directory, so
    a concurrent run's live directory both prevents the removal and is left
    untouched by it.  Every failure is expected here rather than exceptional --
    the directory is absent, or another run still holds a file in it -- so
    none is reported.

    Args:
        base: Directory the shared directory hangs off.
    """
    try:
        workers_dir(base).rmdir()
    except OSError:
        # Absent, not empty, or not ours to remove.  All three are normal.
        return


#: Flags that open a directory without following a symlink at its final
#: component.  ``O_NOFOLLOW`` is the whole point: a path component swapped for
#: a link fails the open instead of redirecting everything that follows.
_NOFOLLOW_DIR_FLAGS: Final[int] = (
    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
)

#: Whether this platform can enumerate and remove *relative to an open
#: directory descriptor*.  All four capabilities are needed together, and
#: POSIX has them while Windows has none; see :func:`_confined_workers_fd` for
#: what the Windows path does instead.
_SUPPORTS_CONFINED_REMOVAL: Final[bool] = (
    hasattr(os, "O_DIRECTORY")
    and hasattr(os, "O_NOFOLLOW")
    and os.scandir in os.supports_fd
    and os.rmdir in os.supports_dir_fd
    and shutil.rmtree.avoids_symlink_attacks
)


def _confined_workers_fd(base: Path | str | None) -> int | None:
    """Open the shared intermediate directory without trusting its path.

    The path to it is ``<base>/<target>/<workers>``, and every component after
    ``base`` is opened with ``O_NOFOLLOW`` relative to the descriptor of the
    one before, so a link substituted for *any* of them fails the open rather
    than redirecting a recursive deletion out of the checkout (CWE-59).  The
    directory names come from :mod:`app.utils.paths`, the port's owner of
    every path, rather than being spelled out here.

    Args:
        base: Directory the build output hangs off, or ``None`` for the
            working directory.  This is the one component taken on trust: it
            is the checkout the caller chose to run in.

    Returns:
        A descriptor for the shared intermediate directory, which the caller
        must close, or ``None`` when it does not exist or cannot be opened
        through a chain of real directories.

    Raises:
        OSError: If a component exists but cannot be opened for a reason
            other than being absent -- a link in the chain among them.  The
            caller reports that as a cleanup failure rather than proceeding.
    """
    root = target_root(base)
    descriptors: list[int] = []
    try:
        parent = os.open(root.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except FileNotFoundError:
        return None
    descriptors.append(parent)
    try:
        for name in (root.name, workers_dir(base).name):
            try:
                child = os.open(name, _NOFOLLOW_DIR_FLAGS, dir_fd=descriptors[-1])
            except FileNotFoundError:
                return None
            descriptors.append(child)
    except OSError:
        raise
    finally:
        # Every descriptor but the last is scaffolding.  The last is the
        # caller's, and is only left open when the walk completed.
        for descriptor in descriptors[:-1]:
            os.close(descriptor)
        if len(descriptors) < 3:
            for descriptor in descriptors[-1:]:
                os.close(descriptor)
    return descriptors[-1]


def _remove_confined_entry(name: str, workers_fd: int, shown: Path) -> str | None:
    """Remove one entry of the shared intermediate directory, following no link.

    Args:
        name: The entry's single path component, never a path.
        workers_fd: Descriptor of the directory holding it, already opened
            through a chain of real directories.
        shown: The entry's path as a human should see it in a diagnostic.

    Returns:
        ``None`` once the entry is gone, and otherwise a reason.  Removal is
        descriptor-relative, so no component of ``shown`` is resolved a
        second time, and a symlink is unlinked as the link it is rather than
        followed.  **Absence is then confirmed** rather than assumed: a
        removal that reported success and left the entry behind is the one
        case a caller must never read as success.
    """
    try:
        if _is_directory_entry(name, workers_fd):
            shutil.rmtree(name, dir_fd=workers_fd)
        else:
            os.unlink(name, dir_fd=workers_fd)
    except FileNotFoundError:
        return None
    except OSError as error:
        reason = f"could not remove {shown}: {error}"
        logger.error("Intermediate cleanup failed: %s", reason)
        return reason

    try:
        os.lstat(name, dir_fd=workers_fd)
    except FileNotFoundError:
        return None
    except OSError as error:
        reason = f"could not confirm that {shown} is gone: {error}"
        logger.error("Intermediate cleanup failed: %s", reason)
        return reason
    reason = f"{shown} still exists after it was removed"
    logger.error("Intermediate cleanup failed: %s", reason)
    return reason


def _is_directory_entry(name: str, workers_fd: int) -> bool:
    """Return whether an entry is a real directory rather than a link to one.

    Args:
        name: The entry's single path component.
        workers_fd: Descriptor of the directory holding it.

    Returns:
        ``True`` only for a real directory.  A symlink - whatever it points
        at - is ``False``, so it is unlinked instead of walked.
    """
    try:
        status = os.lstat(name, dir_fd=workers_fd)
    except OSError:
        return False
    return stat.S_ISDIR(status.st_mode)


def _remove_by_path(directory: Path) -> str | None:
    """Remove one run directory by path, for a platform without ``at`` calls.

    The fallback for Windows, which AAP section 0.8 lists as supported and
    which has none of the descriptor-relative calls the POSIX path uses.  It
    keeps what can be kept without them: the directory is inspected with
    :func:`os.lstat`, a link is **refused** rather than followed or unlinked,
    and the removal's postcondition is confirmed afterwards.  What it cannot
    do is close the window between the inspection and the removal, so that
    residual race is stated here rather than hidden - a build output
    directory writable by a hostile process is outside what a build tool can
    defend on this platform.

    Args:
        directory: The run directory to remove.

    Returns:
        ``None`` once it is gone, and otherwise a reason.
    """
    try:
        status = os.lstat(directory)
    except FileNotFoundError:
        return None
    except OSError as error:
        reason = f"could not inspect {directory}: {error}"
        logger.error("Intermediate cleanup failed: %s", reason)
        return reason

    if stat.S_ISLNK(status.st_mode):
        reason = (
            f"{directory} is a symbolic link rather than a run directory, so "
            "it was neither followed nor removed"
        )
        logger.error("Intermediate cleanup failed: %s", reason)
        return reason

    try:
        shutil.rmtree(directory)
    except FileNotFoundError:
        return None
    except OSError as error:
        reason = f"could not remove {directory}: {error}"
        logger.error("Intermediate cleanup failed: %s", reason)
        return reason

    if directory.exists():
        reason = f"{directory} still exists after it was removed"
        logger.error("Intermediate cleanup failed: %s", reason)
        return reason
    return None


def _remove_run_directory(directory: Path, base: Path | str | None) -> str | None:
    """Remove one run directory, refusing anything that is not one.

    Two confinement rules, and both are refusals rather than best efforts:
    the directory must be a **direct child** of the shared intermediate
    directory this ``base`` names, and the path to that shared directory must
    be a chain of real directories (:func:`_confined_workers_fd`).  Together
    they mean this function cannot be talked into a recursive deletion
    somewhere else by a caller's path, by a relinked parent component, or by
    a link left in place of the directory itself.

    Args:
        directory: The run directory to remove.
        base: Directory the shared intermediate directory hangs off.

    Returns:
        ``None`` once the directory is gone - which includes it never having
        existed - and otherwise a reason, already logged at ``ERROR``.
    """
    expected_parent = workers_dir(base)
    if directory.parent != expected_parent:
        reason = (
            f"{directory} is not inside {expected_parent}, so it was not "
            "removed: this run's intermediates are the only thing cleanup may "
            "delete"
        )
        logger.error("Intermediate cleanup refused: %s", reason)
        return reason

    if not _SUPPORTS_CONFINED_REMOVAL:
        return _remove_by_path(directory)

    try:
        workers_fd = _confined_workers_fd(base)
    except OSError as error:
        reason = (
            f"could not open {expected_parent} through a chain of real "
            f"directories, so {directory} was not removed: {error}"
        )
        logger.error("Intermediate cleanup refused: %s", reason)
        return reason
    if workers_fd is None:
        # The shared directory is gone, so anything inside it is too.
        return None

    try:
        return _remove_confined_entry(directory.name, workers_fd, directory)
    finally:
        os.close(workers_fd)


def reclaim_workers_root(
    *, base: Path | str | None = None
) -> tuple[str | None, tuple[Path, ...]]:
    """Empty the shared intermediate directory of everything not in use.

    This is what the ``--clean`` step calls for that directory instead of
    deleting it outright, and what ``app/cli.py`` calls on an exit path that
    executed nothing.  The distinction it draws is the one the concurrency
    finding turns on: an abandoned run's leftovers are **reclaimed**, and a
    live run's directory is **retained**, because a second invocation's clean
    deleting a first invocation's working files is exactly how live results
    become missing worker files.  Liveness comes from
    :func:`run_directory_is_active`, and this process's own current run counts
    as live.

    Args:
        base: Directory the shared intermediate directory hangs off, or
            ``None`` for the working directory.

    Returns:
        A ``(reason, retained)`` pair.  ``reason`` is ``None`` when
        everything reclaimable is gone, and otherwise names what could not be
        removed; ``retained`` names every live run directory left in place,
        in sorted order, so a caller can report them as deliberately kept
        rather than treating them as a failed clean.
    """
    root = workers_dir(base)
    if not _SUPPORTS_CONFINED_REMOVAL:
        return _reclaim_by_path(root, base)

    try:
        workers_fd = _confined_workers_fd(base)
    except OSError as error:
        reason = (
            f"could not open {root} through a chain of real directories, so "
            f"nothing in it was removed: {error}"
        )
        logger.error("Intermediate cleanup refused: %s", reason)
        return reason, ()
    if workers_fd is None:
        return None, ()

    problems: list[str] = []
    retained: list[Path] = []
    try:
        with os.scandir(workers_fd) as entries:
            names = sorted(entry.name for entry in entries)
        for name in names:
            if run_directory_is_active(root / name):
                retained.append(root / name)
                continue
            problem = _remove_confined_entry(name, workers_fd, root / name)
            if problem is not None:
                problems.append(problem)
    except OSError as error:
        problem = f"could not list the entries of {root}: {error}"
        logger.error("Intermediate cleanup failed: %s", problem)
        problems.append(problem)
    finally:
        os.close(workers_fd)

    with _active_run_dirs_lock:
        for directory in tuple(_active_run_dirs):
            if directory.parent == root and directory not in retained:
                _active_run_dirs.discard(directory)

    if not retained:
        _prune_workers_root(base)
    if retained:
        logger.info(
            "Retained %d live run director(y/ies) in %s: %s",
            len(retained),
            root,
            ", ".join(directory.name for directory in retained),
        )
    return (_cleanup_reason(problems), tuple(retained))


def _reclaim_by_path(
    root: Path, base: Path | str | None
) -> tuple[str | None, tuple[Path, ...]]:
    """Reclaim the shared intermediate directory without ``at`` calls.

    The Windows counterpart of :func:`reclaim_workers_root`'s main path, with
    the same retention rule and the same residual race as
    :func:`_remove_by_path`.

    Args:
        root: The shared intermediate directory.
        base: Directory it hangs off.

    Returns:
        The same ``(reason, retained)`` pair.
    """
    try:
        status = os.lstat(root)
    except FileNotFoundError:
        return None, ()
    except OSError as error:
        reason = f"could not inspect {root}: {error}"
        logger.error("Intermediate cleanup failed: %s", reason)
        return reason, ()

    if stat.S_ISLNK(status.st_mode):
        reason = (
            f"{root} is a symbolic link rather than the intermediate "
            "directory, so nothing in it was removed"
        )
        logger.error("Intermediate cleanup refused: %s", reason)
        return reason, ()

    try:
        entries = sorted(root.iterdir())
    except OSError as error:
        reason = f"could not list the entries of {root}: {error}"
        logger.error("Intermediate cleanup failed: %s", reason)
        return reason, ()

    problems: list[str] = []
    retained: list[Path] = []
    for entry in entries:
        if run_directory_is_active(entry):
            retained.append(entry)
            continue
        problem = _remove_by_path(entry)
        if problem is not None:
            problems.append(problem)

    with _active_run_dirs_lock:
        for directory in tuple(_active_run_dirs):
            if directory.parent == root and directory not in retained:
                _active_run_dirs.discard(directory)

    if not retained:
        _prune_workers_root(base)
    return (_cleanup_reason(problems), tuple(retained))


def _cleanup_reason(problems: Sequence[str]) -> str | None:
    """Combine cleanup problems into the one reason a caller reports.

    Args:
        problems: Every problem encountered, each already logged.

    Returns:
        ``None`` when there were none, and otherwise a single line naming
        them and what their survival means.
    """
    if not problems:
        return None
    return (
        f"{len(problems)} intermediate path(s) could not be removed, so "
        f"per-worker result documents remain in the workspace: "
        f"{'; '.join(problems)}"
    )


def cleanup_workers_dir(
    *,
    base: Path | str | None = None,
    directory: Path | str | None = None,
) -> str | None:
    """Remove a run's intermediate directory, and report whether it is gone.

    **Idempotent, and observably so.**  A directory that is already absent is
    a success: :func:`run_suite` removes its own directory in its ``finally``
    and ``app/cli.py`` calls this function again on the way out, so the second
    call routinely finds nothing to do.  What is *not* silent any more is a
    removal that fails, because a directory that survives holds this run's
    intermediate documents -- tracebacks, attachments, per-scenario results --
    in a workspace where ``Jenkins:15`` narrows the publisher to one file
    exactly so that nothing intermediate can be read by it.  The reason is
    returned so the caller can refuse to report success; it is never raised,
    because this runs in a ``finally`` where an exception would mask whatever
    the run was already reporting.

    Args:
        base: Directory the shared intermediate directory hangs off, or
            ``None`` for the working directory.
        directory: The one run directory to remove.  ``None`` means "every
            directory this process created and has not yet removed", which is
            what ``app/cli.py``'s single call site asks for: it covers a run
            whose own cleanup could not reach (an interrupt during
            preparation, say) and reduces to a no-op after a run that cleaned
            up for itself.  A run directory another process created is never
            removed by either form.

    Returns:
        ``None`` when nothing of this invocation's remains; otherwise a
        one-line reason naming what could not be removed, already logged at
        ``ERROR``.  ``app/cli.py`` maps a reason onto its artifact-failure
        status, so a run cannot return ``0`` while its intermediates survive.
    """
    if directory is not None:
        targets = [Path(directory)]
    else:
        with _active_run_dirs_lock:
            targets = sorted(_active_run_dirs)

    problems: list[str] = []
    for target in targets:
        # Confined, no-follow, and confirmed gone before it is forgotten -
        # see :func:`_remove_run_directory`.  A directory that could not be
        # removed stays registered, so a later call tries it again and the
        # command cannot lose track of it.
        problem = _remove_run_directory(target, base)
        if problem is not None:
            problems.append(problem)
            continue
        with _active_run_dirs_lock:
            _active_run_dirs.discard(target)

    # Whether or not a removal failed: the shared parent goes only when it is
    # empty, so this both tidies up after the last run in a workspace and is a
    # no-op while any other run still holds a directory in it.
    _prune_workers_root(base)
    return _cleanup_reason(problems)


# --------------------------------------------------------------------------- #
# Running the shards
# --------------------------------------------------------------------------- #

#: How many of a shard's locations a diagnostic message names before it
#: abbreviates.  Enough to identify the shard's work without turning a single
#: dead-worker line into eighty.
_MAX_REPORTED_LOCATIONS: Final[int] = 5
_ELLIPSIS: Final[str] = "..."

#: Thread-name prefix for the supervision threads the ``spawn`` seam uses.
#: Named so a stack dump taken during a test names the port rather than
#: ``ThreadPoolExecutor-0_3``.
_SHARD_THREAD_PREFIX: Final[str] = "testinium-qa-shard"

#: ``sys.platform`` value for Windows, where two of this module's decisions
#: differ: the pool's own size limit below, and the process-group flag in
#: :func:`_spawn_worker`.
_WINDOWS_PLATFORM: Final[str] = "win32"

#: Largest :class:`~concurrent.futures.ProcessPoolExecutor` this module builds
#: on Windows.  Not a policy of the port's: the standard library raises
#: ``ValueError("max_workers must be <= 61")`` above it, because a Windows wait
#: handles at most 63 objects and the pool spends two of them on its own
#: queues (``concurrent/futures/process.py``, ``_MAX_WINDOWS_WORKERS``).  A
#: shard count above it is queued through the pool rather than refused: each
#: shard's output path was computed in the parent and carries the shard index,
#: so one pool process running several shards in turn writes distinct files.
_MAX_POOL_PROCESSES: Final[int] = 61

#: How long a process gets to exit after it is asked to, before it is killed.
#: Only ever spent on the cancellation path, where the run is already over and
#: the operator is waiting: long enough for a Python process to unwind its own
#: cleanup, short enough that an interrupt still feels immediate.
_TERMINATION_GRACE_SECONDS: Final[float] = 5.0


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


def _dead_shard(
    plan: ShardPlan, detail: str, *, returncode: int | None = None
) -> ShardResult:
    """Build the dead result for a shard that could not be run or supervised.

    Every dead result in this module is built here, so each one names its
    shard through :func:`_describe` -- "the incomplete shard is named on
    stderr" (AAP 0.4.1) is a property of the message, and a hand-built
    reason string is how that property gets lost.

    Args:
        plan: The shard that failed.
        detail: What became of it, as a verb phrase completing
            "``shard 3 (...)`` " -- for example ``"could not be started"``.
        returncode: The engine's status if there was one; ``None`` when the
            shard never reached the point of having one.

    Returns:
        A :class:`ShardResult` with ``dead`` set and ``reason`` naming the
        shard.  ``app/cli.py`` maps a dead shard to its own non-zero class,
        and the artifacts from the shards that *did* complete are still
        written, so this is a report and never an abort.
    """
    return ShardResult(
        plan=plan,
        returncode=returncode,
        dead=True,
        reason=f"{_describe(plan)} {detail}",
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


# --------------------------------------------------------------------------- #
# Live worker output, and the children this process owns
#
# Two requirements meet here.  AAP 0.4.1 requires both streams to be
# line-buffered -- progress to stdout, engine diagnostics to stderr -- which
# means a worker's line must be relayed when it is written and not when the
# worker exits; a browser suite runs for minutes, and buffering its output
# until the end leaves a Jenkins log silent throughout.  And a run that is
# interrupted must leave nothing behind: a worker sits in its own process
# group precisely so that stopping it stops the driver and the browser it
# started, which needs the parent to know which children are still alive.
# --------------------------------------------------------------------------- #

#: How many of a worker's most recent lines each stream keeps after relaying
#: them.  **Bounded on purpose**: holding a whole run's output in memory is
#: the defect this replaced, and a tail is all an after-the-fact reader of
#: :attr:`WorkerProcess.stderr` needs, since a shard's classification comes
#: from its exit status and its result file rather than from its text.
_RETAINED_OUTPUT_LINES: Final[int] = 20

#: Environment variable that stops the *child's* own stdout and stderr being
#: block-buffered.  Without it the relay below is live but its input is not:
#: a Python process whose stdout is a pipe buffers in blocks, so behave's
#: progress would reach this parent in 8 KiB instalments however promptly the
#: parent reads.  Setting it is what makes the line-buffered contract in AAP
#: 0.4.1 true end to end rather than only on the parent's side.
_UNBUFFERED_ENV_VAR: Final[str] = "PYTHONUNBUFFERED"
_UNBUFFERED_ENV_VALUE: Final[str] = "1"

#: Whether this platform can put a child in its own process group and signal
#: that group as a unit.  True on POSIX, false on Windows, where the
#: equivalent is a creation flag rather than a call.
_HAS_PROCESS_GROUPS: Final[bool] = (
    hasattr(os, "killpg") and hasattr(os, "getpgid") and hasattr(os, "setsid")
)

#: The signal a worker that ignored the polite request is killed with.
#: ``SIGKILL`` where it exists; on Windows there is no such signal and
#: :meth:`subprocess.Popen.kill` is the terminal action instead.
_KILL_SIGNAL: Final[int] = int(getattr(signal, "SIGKILL", signal.SIGTERM))

#: Conventional exit status for a process that died from a signal: ``128 + n``,
#: as every POSIX shell reports it.
_SIGNAL_EXIT_BASE: Final[int] = 128

#: How long a reader thread is waited for once its worker has exited.  It
#: normally ends immediately, at the pipe's EOF; the bound covers the case
#: where a grandchild inherited the pipe and still holds it open, which must
#: delay a finished run by a few seconds at most rather than indefinitely.
_READER_JOIN_SECONDS: Final[float] = 5.0

#: The label relayed lines are tagged with, carried out of band.
#:
#: **Why a context variable rather than a parameter.**  The tag identifies the
#: shard, and :data:`SpawnCallable` -- the documented seam every caller and
#: every stub is written against -- takes the command and the working
#: directory and nothing else.  Widening it would break every stub, so
#: :func:`_run_one_shard` publishes the label here and :func:`_spawn_worker`
#: reads it in its own thread, where the value it set is visible.  Unset, the
#: launch falls back to the child's pid, so a direct call to
#: :func:`_spawn_worker` still produces attributable output.
_shard_output_label: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "testinium_qa_shard_output_label", default=None
)

#: Worker subprocesses this process has started and not yet reaped, by pid,
#: guarded by the lock below because workers are started from several threads
#: on the seam path and read back from whichever thread handles an interrupt.
_live_workers: dict[int, subprocess.Popen[str]] = {}
_live_workers_lock: Final[threading.Lock] = threading.Lock()


@dataclass(frozen=True, slots=True)
class _RelayedWorker:
    """A finished worker whose output was relayed while it ran.

    Satisfies :class:`WorkerProcess` structurally, and adds the
    ``output_relayed`` marker that stops :func:`_relay_output` printing the
    same lines a second time.

    Attributes:
        returncode: The engine's exit status.  Negative means killed by a
            signal, which is the one status :func:`_run_one_shard` treats as
            death; positive is an ordinary test outcome.
        stdout: The last :data:`_RETAINED_OUTPUT_LINES` lines of standard
            output, newline-joined -- a diagnostic tail, not a transcript.
        stderr: The same for standard error.
        output_relayed: Always ``True``.  Present so the marker is explicit
            on the object rather than inferred from its type.
    """

    returncode: int | None
    stdout: str | None
    stderr: str | None
    output_relayed: bool = True


def _register_worker(process: subprocess.Popen[str]) -> None:
    """Record a live worker, so an interrupt can find it.

    Args:
        process: The worker just started.

    Returns:
        ``None``.
    """
    with _live_workers_lock:
        _live_workers[process.pid] = process


def _forget_worker(process: subprocess.Popen[str]) -> None:
    """Drop a worker from the registry once it has been waited for.

    Idempotent: the owning thread forgets its worker in a ``finally``, and
    :func:`terminate_live_workers` forgets whatever it stopped, so the same
    worker is routinely dropped twice.

    Args:
        process: The worker to drop.

    Returns:
        ``None``.
    """
    with _live_workers_lock:
        _live_workers.pop(process.pid, None)


def _signal_worker_group(
    process: subprocess.Popen[str], signal_number: int
) -> bool:
    """Signal a worker's whole process group, where the platform allows it.

    Signalling the *group* is the difference between stopping a run and
    leaving a headless browser behind: the engine starts a driver executable
    which starts a browser, and only the group reaches all three.  The group
    exists because :func:`_spawn_worker` asked for one.

    Args:
        process: The worker to signal.
        signal_number: The signal to send.

    Returns:
        ``True`` when the group was signalled, ``False`` when this platform
        has no process groups or the group could no longer be resolved -- in
        which case the caller falls back to the process itself.
    """
    if not _HAS_PROCESS_GROUPS:
        return False
    try:
        os.killpg(os.getpgid(process.pid), signal_number)
    except OSError as error:
        # Includes ProcessLookupError, which simply means it has already gone.
        logger.debug(
            "Could not signal the process group of worker %d: %s",
            process.pid,
            error,
        )
        return False
    return True


def _terminate_worker(
    process: subprocess.Popen[str],
    *,
    grace_seconds: float = _TERMINATION_GRACE_SECONDS,
) -> bool:
    """Stop one worker and its children, and **wait** for it.

    Polite first: ``SIGTERM`` to the worker's process group, so the engine
    can close its driver session and write what it has.  Then a bounded
    grace, then a kill.  Waiting after each signal is not optional -- a
    cleanup that returns while the process it signalled is still running is
    exactly how a browser outlives its run.

    Args:
        process: The worker to stop.
        grace_seconds: How long it gets to exit before it is killed, and
            again after the kill before the attempt is reported as failed.

    Returns:
        ``True`` if the worker was running and has now been stopped,
        ``False`` if it had already exited.
    """
    if process.poll() is not None:
        return False

    logger.warning("Stopping worker process %d", process.pid)
    if not _signal_worker_group(process, signal.SIGTERM):
        try:
            process.terminate()
        except OSError as error:
            logger.debug("Could not terminate worker %d: %s", process.pid, error)
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        logger.warning(
            "Worker process %d did not exit within %.0fs; killing it",
            process.pid,
            grace_seconds,
        )
    else:
        return True

    if not _signal_worker_group(process, _KILL_SIGNAL):
        try:
            process.kill()
        except OSError as error:
            logger.debug("Could not kill worker %d: %s", process.pid, error)
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        # Unkillable, which on POSIX means stuck in uninterruptible I/O.
        # Reported rather than waited on forever: a run must not hang inside
        # its own cleanup.
        logger.error(
            "Worker process %d could not be stopped and may still be running",
            process.pid,
        )
    return True


def terminate_live_workers(
    *, grace_seconds: float = _TERMINATION_GRACE_SECONDS
) -> int:
    """Stop every worker subprocess this process started, and wait for them.

    The cancellation half of the interrupt contract.  Each supervision mode
    calls it when an interrupt unwinds it -- the sequential path in this
    process, and the pool task inside each pool worker, so a pool process
    that is stopped takes its engine subprocess with it instead of orphaning
    it.  A worker is in its own process group, which means it does **not**
    receive the ``Ctrl-C`` the terminal delivers to this one; that is
    deliberate (the engine must not be interrupted mid-write by a signal
    aimed at the supervisor) and it is precisely why this function exists.

    Idempotent, and safe when nothing is running: the registry is then empty
    and the call returns ``0``.

    Args:
        grace_seconds: How long each worker gets between the termination
            request and the kill.

    Returns:
        How many workers were still running and have now been stopped.
    """
    with _live_workers_lock:
        processes = list(_live_workers.values())

    stopped = 0
    for process in processes:
        try:
            if _terminate_worker(process, grace_seconds=grace_seconds):
                stopped += 1
        except OSError as error:
            # Never raised onward: this runs while an interrupt is unwinding,
            # where an exception would replace what the run was reporting.
            logger.warning(
                "Could not stop worker process %d: %s", process.pid, error
            )
        finally:
            _forget_worker(process)

    if stopped:
        logger.warning("Stopped %d worker process(es) still running", stopped)
    return stopped


def _reraise_termination_as_exit(
    signal_number: int, frame: FrameType | None
) -> None:
    """Turn a termination signal into an exception, so cleanup can run.

    Installed by :func:`_run_shard_task` for the duration of one shard, and
    for one reason: a pool worker terminated on the default disposition dies
    *immediately*, with no opportunity to stop the engine subprocess it
    started, and that subprocess -- in its own process group -- would survive
    as an orphaned browser.  Raising instead unwinds the task through its own
    handler, which stops the engine and waits for it before the process
    leaves with the conventional ``128 + signal`` status.

    Args:
        signal_number: The signal that arrived.
        frame: The interrupted frame.  Unused; part of the handler signature
            :func:`signal.signal` requires.

    Raises:
        SystemExit: Always, carrying ``_SIGNAL_EXIT_BASE + signal_number``.

    Notes:
        POSIX only in effect.  Windows terminates a process without
        delivering a signal, so there the pool worker dies outright and its
        engine subprocess is left to exit on its own; the shard is then
        reported dead from its missing result file, which is the documented
        outcome either way.
    """
    raise SystemExit(_SIGNAL_EXIT_BASE + signal_number)


def _install_termination_handler() -> Any:
    """Make ``SIGTERM`` raise :exc:`SystemExit` and return what it replaced.

    Returns:
        The previous handler, to be given back to
        :func:`_restore_termination_handler`, or ``None`` when no handler
        could be installed -- which happens off the main thread, where
        :func:`signal.signal` refuses, and is not an error here: the
        cancellation path still works, it simply loses the SIGTERM case.
    """
    try:
        return signal.signal(signal.SIGTERM, _reraise_termination_as_exit)
    except (OSError, ValueError) as error:
        logger.debug("SIGTERM handler not installed: %s", error)
        return None


def _suppress_termination_signal() -> None:
    """Stop a further ``SIGTERM`` interrupting a cleanup already under way.

    A supervisor stopping a pool signals every worker, and a pool that then
    notices one worker has died signals the rest again on its own account.
    **Measured**: those repeats land inside a worker's cleanup and abandon it
    half-finished, leaving the engine subprocess -- and the browser it drives
    -- running after the run is over.  Ignoring the signal for the moment the
    cleanup takes is what makes the cleanup complete; a kill still reaches
    this process unconditionally, so nothing here can outlive its supervisor.

    Returns:
        ``None``.  A platform that will not let the disposition be changed
        leaves the cleanup interruptible, which is logged at ``DEBUG`` and is
        not an error.
    """
    try:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    except (OSError, ValueError) as error:
        logger.debug("SIGTERM not suppressed during cleanup: %s", error)


def _restore_termination_handler(previous: Any) -> None:
    """Give ``SIGTERM`` back the handler it had.

    Args:
        previous: The value :func:`_install_termination_handler` returned.
            ``None`` means there is nothing to restore, either because
            installation failed or because the disposition in place was not
            one Python had set.

    Returns:
        ``None``.
    """
    if previous is None:
        return
    try:
        signal.signal(signal.SIGTERM, previous)
    except (OSError, ValueError) as error:
        logger.debug("SIGTERM handler not restored: %s", error)


def _process_group_keywords() -> dict[str, Any]:
    """Return the :class:`subprocess.Popen` keywords that isolate a worker.

    A worker gets its own process group so that stopping it stops everything
    it started, and so that a ``Ctrl-C`` aimed at this process does not tear
    the engine down mid-write behind the supervisor's back.

    Returns:
        ``{"start_new_session": True}`` on POSIX,
        ``{"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}`` on Windows,
        and an empty mapping on a platform offering neither -- where the
        launch still works and only the group isolation is unavailable.
    """
    if _HAS_PROCESS_GROUPS:
        return {"start_new_session": True}
    # Read with getattr because the attribute exists on Windows only, and
    # referring to it directly would fail to import elsewhere.
    creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", None)
    if creation_flags is None:
        return {}
    return {"creationflags": creation_flags}


def _relay_stream(
    stream: IO[str], tag: str, level: int, retained: deque[str]
) -> None:
    """Relay one worker stream to the log, line by line, as it arrives.

    Runs in its own thread, one per stream, because a worker writes to both
    and draining them in sequence would deadlock the moment the stream not
    being read filled its pipe buffer.  Blank lines are dropped -- behave
    emits many -- and every other line is logged immediately at ``level``, so
    the record reaches the console handler while the worker is still running.

    Args:
        stream: The worker's text-mode pipe.
        tag: Shard label the line is prefixed with, which is what keeps
            concurrent shards readable.
        level: ``logging.INFO`` for standard output, ``logging.WARNING`` for
            standard error, which is the stream split AAP 0.4.1 requires.
        retained: Bounded tail the relayed lines are also appended to.

    Returns:
        ``None``.  Returns at the pipe's EOF, which is the worker's exit.
    """
    try:
        # iter(readline, "") rather than iterating the file object: it is
        # explicit that a line is taken the moment it is complete.
        for raw_line in iter(stream.readline, ""):
            line = raw_line.rstrip("\r\n")
            if not line.strip():
                continue
            retained.append(line)
            logger.log(level, "[%s] %s", tag, line)
    except (OSError, ValueError) as error:
        # The pipe was closed under the reader -- what a killed worker and a
        # closed stream both look like from here.  Never fatal: relayed
        # output is diagnostics, and the shard's classification comes from
        # its exit status and its result file.
        logger.debug("Output relay for %s ended early: %s", tag, error)


def _close_worker_streams(process: subprocess.Popen[str]) -> None:
    """Close a finished worker's pipes.

    :meth:`subprocess.Popen.wait` leaves them open -- only
    :meth:`~subprocess.Popen.communicate` closes them -- so a run of many
    shards would otherwise accumulate two descriptors per worker.

    Args:
        process: The worker whose pipes are no longer needed.

    Returns:
        ``None``.
    """
    for stream in (process.stdout, process.stderr):
        if stream is None:
            continue
        try:
            stream.close()
        except OSError as error:
            logger.debug("Could not close a worker pipe: %s", error)


def _spawn_worker(command: Sequence[str], cwd: Path) -> _RelayedWorker:
    """Launch one worker, relay its output as it arrives, and wait for it.

    The default implementation behind :func:`run_suite`'s ``spawn`` seam, and
    the only place in this module that starts a process.

    Three properties, each of them load-bearing:

    * **The output is live.**  Both pipes are drained by their own reader
      thread and every line is logged the moment it is complete, at
      ``INFO`` for stdout and ``WARNING`` for stderr, so the stream split in
      AAP 0.4.1 holds *during* the run rather than after it.  The child's own
      buffering is switched off for the same reason.  What the return value
      carries is a bounded tail, never the whole transcript.
    * **The child is isolated.**  It runs in its own process group, so
      stopping it stops the driver and browser it started, and a ``Ctrl-C``
      delivered to this process does not reach it behind the supervisor's
      back.  :func:`terminate_live_workers` is the only thing that stops it.
    * **It is accounted for.**  The worker is in the registry from the moment
      it exists until it has been waited for, so an interrupt on any thread
      can find and reclaim it.

    Args:
        command: The argument list from :func:`build_worker_command`.  Passed
            as a list with no shell, so nothing in a tag expression or a
            location can be interpreted by one.
        cwd: The run base.

    Returns:
        The finished worker: its status, a bounded tail of each stream, and
        the marker that says its output has already been relayed.

    Raises:
        OSError: If the process cannot be started at all.
            :func:`_run_one_shard` records that as a dead shard.
        BaseException: An interrupt that arrives while waiting is re-raised,
            after the worker has been stopped and waited for.

    Notes:
        There is no ``check`` here and no equivalent of it: behave exits
        non-zero when scenarios fail, and converting that into an exception
        would put a test outcome into the exit status in direct violation of
        ``testFailureIgnore=true`` (``pom.xml:25``).  A **negative** status,
        which POSIX uses for killed-by-signal, is the one the caller treats
        as death.

        No timeout is imposed, and no retry.  A browser suite's duration is
        not predictable -- this one alone carries seventeen fixed sleeps --
        and surefire imposed none either, so a limit invented here could only
        kill legitimate runs.  A hung worker is stopped by the operator's
        interrupt, which the cancellation path above turns into a clean stop.
    """
    # A copy, never a mutation of this process's own environment: the child
    # inherits everything the run was started with, plus the one variable
    # that keeps its output unbuffered.
    environment = dict(os.environ)
    environment[_UNBUFFERED_ENV_VAR] = _UNBUFFERED_ENV_VALUE

    # The argv is fixed, no shell is involved, and the program is this very
    # interpreter rather than anything a caller supplied.
    process = subprocess.Popen(
        list(command),
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=environment,
        **_process_group_keywords(),
    )
    _register_worker(process)

    # Read in this thread, where _run_one_shard set it; a reader thread would
    # see the default, because a new thread starts with an empty context.
    tag = _shard_output_label.get() or f"pid {process.pid}"
    stdout_tail: deque[str] = deque(maxlen=_RETAINED_OUTPUT_LINES)
    stderr_tail: deque[str] = deque(maxlen=_RETAINED_OUTPUT_LINES)
    readers: list[threading.Thread] = []

    try:
        for stream, level, retained, stream_name in (
            (process.stdout, logging.INFO, stdout_tail, "out"),
            (process.stderr, logging.WARNING, stderr_tail, "err"),
        ):
            if stream is None:
                continue
            reader = threading.Thread(
                target=_relay_stream,
                args=(stream, tag, level, retained),
                name=f"{_SHARD_THREAD_PREFIX}-{stream_name}-{process.pid}",
                daemon=True,
            )
            reader.start()
            readers.append(reader)
        returncode = process.wait()
    except BaseException:
        # Everything between the launch and the wait is covered, not just the
        # wait: an interrupt, or a reader thread that could not be started at
        # all, would otherwise leave a live child in its own process group
        # that nothing is left to wait for.  So it is stopped and waited for
        # here, and the exception travels on untouched.
        _terminate_worker(process)
        raise
    finally:
        _forget_worker(process)
        for reader in readers:
            # Bounded: a grandchild holding the pipe open must not keep a
            # finished run waiting indefinitely.  The threads are daemons, so
            # one that outlives this join cannot hold up interpreter exit.
            reader.join(_READER_JOIN_SECONDS)
        _close_worker_streams(process)

    return _RelayedWorker(
        returncode=returncode,
        stdout="\n".join(stdout_tail),
        stderr="\n".join(stderr_tail),
    )


def _relay_output(plan: ShardPlan, process: WorkerProcess) -> None:
    """Relay a worker's output after the fact, tagged, sanitized and at level.

    Nothing is printed for a worker whose output was already relayed live --
    see ``process`` below.  For one that was not, every non-blank physical line of the child's ``stdout``, then every
    non-blank physical line of its ``stderr``, becomes exactly one parent
    record carrying ``[shard N]``.  That tag on *every* line is what makes
    concurrent output attributable without tracing, and the relay is the only
    place a worker's text enters the parent log.

    **No line is trusted as log text.**  What arrives here is whatever the
    engine, a driver, a page object or a step printed, so each line is
    rendered by :func:`~app.logging_config.render_worker_line` -- the single
    implementation of that rendering in this port, in the module that owns
    the console contract -- before it reaches a record:

    * *Control-safe, and that is what makes the tag a guarantee.*  Terminal
      escape sequences are removed and every control character is spelled out
      printably, so a child line containing ``\\r\\n``, ``U+0085`` or
      ``U+2028`` can no longer break itself into a second, untagged parent
      line, and a ``CSI`` sequence can no longer recolour or erase what the
      console already printed.  ``splitlines`` alone never gave that: it
      splits on the breaks it recognises and leaves a record forgeable by the
      ones it does not (CWE-117).
    * *Bounded*, at :data:`~app.logging_config.RELAYED_LINE_LIMIT`
      characters, with a suffix naming how many were dropped.  A child can
      emit a single enormous line -- an embedded screenshot, a dumped DOM, a
      driver's full capability payload -- and forwarding it whole would bury
      the diagnostics the run is being judged on.  The bound is per line, so
      nothing is summarised and no line is dropped.
    * *Redacted* of credential-shaped content: ``key=value`` and
      ``key: value`` for secret-ish keys, URL userinfo, ``Bearer`` tokens,
      base64 ``data:`` payloads, long opaque blobs, and a quoted value
      adjacent to a credential keyword -- which is the shape of this suite's
      own ``User enters "<username>" username`` step text [Login.feature:15],
      so a child diagnostic quoting a substituted step no longer carries the
      account into a CI console.

    **Redaction is log-only, and deliberately so.**  It applies to the parent
    record built here and to nothing else: not to the worker's argv, not to
    the per-worker result document, and not to any of the four artifacts.
    Specification 0.8's test-data note is emphatic that the Gherkin
    ``Examples`` credentials are pre-existing fixture data for an external
    instance which no agent may redact, parameterize or rotate, and parity
    requires the features, the JSON, the rerun manifest and both HTML reports
    to carry them verbatim.  A console log is not one of those artifacts.

    **Severity survives the process boundary.**  The child is a separate
    interpreter with its own logging state, and behave's default
    ``logging_format`` is ``"LOG_%(levelname)s:%(name)s: %(message)s"``
    installed through ``basicConfig`` (measured against the pinned behave
    1.3.3), so a child record arrives at column 0 as
    ``LOG_ERROR:app.reporting.screenshots: ...`` -- on the child's ``stderr``
    and again inside the ``CAPTURED LOG:`` block on its ``stdout``.  That
    measured, anchored shape is why a leading token can be read as a level at
    all.  Relaying every ``stderr`` line at ``WARNING`` -- what this function
    used to do -- flattened exactly the records that matter: the screenshot
    helper's suppressed-capture ``logger.exception``, a formatter failure, a
    system error, all indistinguishable from an ordinary engine warning.  So
    the stream supplies a **floor**, never a ceiling: ``INFO`` for ``stdout``
    and ``WARNING`` for ``stderr``, and the record is emitted at
    ``max(child_level, floor)``.  An ``ERROR`` or ``CRITICAL`` token is
    honoured on either stream; a ``DEBUG`` or ``INFO`` token on ``stderr``
    cannot pull a diagnostic below ``WARNING``, which both keeps the
    published stream split intact and stops a child from muting itself.

    The child's own text is otherwise left alone: the ``LOG_ERROR:`` token
    stays, naming the child logger that spoke, and nothing is re-wrapped,
    reordered, summarised or suppressed.  The engine's diagnostics are
    contract-required output -- sanitizing them is the fix; silencing them
    would be a regression.

    Args:
        plan: The shard whose output this is.  Only its ``index`` is read, as
            the tag.
        process: The completed worker.  One carrying a true
            ``output_relayed`` attribute -- every worker the real launch
            produces -- has already had every line relayed as it arrived, and
            nothing is printed for it; printing the retained tail here would
            show those lines twice.  A stubbed
            :class:`subprocess.CompletedProcess` from the ``spawn`` seam
            carries no such marker and is relayed here, exactly as before.
            ``stdout`` and ``stderr`` are read defensively -- that seam admits
            any object shaped like :class:`WorkerProcess`, and a stub may
            carry ``None`` for either.  Relaying reports and decides nothing:
            a shard's classification and the exit contract come from
            :class:`ShardResult` alone.

    Returns:
        ``None``.
    """
    if getattr(process, "output_relayed", False):
        return
    for line in (getattr(process, "stdout", None) or "").splitlines():
        if not line.strip():
            continue
        level, safe_text = render_worker_line(
            line, default_level=logging.INFO
        )
        logger.log(level, "[shard %d] %s", plan.index, safe_text)
    for line in (getattr(process, "stderr", None) or "").splitlines():
        if not line.strip():
            continue
        level, safe_text = render_worker_line(
            line, default_level=logging.WARNING
        )
        logger.log(level, "[shard %d] %s", plan.index, safe_text)


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
        The shard's result.  No :exc:`Exception` is propagated: a launch
        failure is recorded, so one bad worker cannot abort a run.

    Raises:
        BaseException: An interrupt, and only an interrupt, travels through
            here untouched -- :func:`_spawn_worker` has stopped the worker and
            waited for it by then.  Stopping a run is the operator's decision
            and is never turned into a shard outcome.
    """
    command = build_worker_command(plan, tags=tags, browser=browser, dry_run=dry_run)
    logger.info(
        "Shard %d starting: %d scenario(s)", plan.index, len(plan.locations)
    )
    # Published for the launch to read, because the shard's identity cannot
    # travel through the two-argument spawn seam; see _shard_output_label.
    label_token = _shard_output_label.set(f"shard {plan.index}")
    try:
        process = spawn(command, cwd)
    except Exception as error:  # noqa: BLE001
        # Deliberately broad: a launch failure is data, not an abort.  The
        # documented case is OSError, but whatever a launch raises, one bad
        # worker must not take the other shards down with it.
        return _dead_shard(plan, f"could not be started ({error!r})")
    finally:
        _shard_output_label.reset(label_token)

    _relay_output(plan, process)
    returncode = getattr(process, "returncode", None)
    if isinstance(returncode, int) and returncode < 0:
        # Killed by a signal.  On Windows a killed process reports a large
        # positive status instead, which is indistinguishable from an ordinary
        # failure here; the result-file check catches that case at merge time.
        return _dead_shard(
            plan,
            f"was terminated by signal {-returncode} and its results are "
            "incomplete",
            returncode=returncode,
        )

    # Alive, whatever the status: see the trap above.
    return ShardResult(plan=plan, returncode=returncode, dead=False, reason=None)


def _run_shard_task(
    plan: ShardPlan,
    tags: str | None,
    browser: str | None,
    dry_run: bool,
    cwd: Path,
    verbose: bool,
) -> ShardResult:
    """Run one shard inside a pool worker process and return its result.

    **This is the function the process pool executes.**  It is a module-level
    function taking only picklable arguments because it has to be: the start
    method is ``forkserver`` on Linux and ``spawn`` on Windows, and both
    re-import this module in the child and resolve the call by name.  A
    closure, a bound method, or the injectable ``spawn`` seam cannot make that
    crossing -- which is exactly why the seam keeps a thread path, see
    :func:`_run_shards_in_threads`.  Importing this module in a child is safe
    and cheap because it has no import-time side effects: it defines names and
    opens nothing.

    The child is a **new process**, so it configures the console contract
    itself rather than inheriting it; what it does inherit is the parent's
    stdout and stderr *descriptors*, which is what makes that work.  Its first
    act is therefore to install this port's handler split, so a line it
    relays lands on the same stream it would have landed on had the shard run
    in the parent -- ``INFO`` to stdout, ``WARNING`` and above to stderr (AAP
    0.4.1).  :mod:`app.logging_config` is imported inside the function rather
    than at module scope so that importing this module -- which every child
    does, and which selection and merging in the parent also do -- stays as
    cheap as it is today.

    Args:
        plan: The shard to run.  Its ``output_path`` was computed in the
            parent, so the child writes exactly where the parent will look.
        tags: The user's tag expression, or ``None``.
        browser: Browser override, or ``None``.
        dry_run: Whether to pass ``--dry-run``.
        cwd: The run base, which the worker subprocess runs in.
        verbose: Whether the parent's logger was at ``DEBUG``.  Carried across
            the boundary because the child cannot read the parent's logger,
            and dropping it would silently downgrade a verbose run.

    Returns:
        The shard's result, pickled back to the parent.  A launch failure and
        a non-zero engine status are both recorded rather than raised -- see
        :func:`_run_one_shard`.

    Raises:
        BaseException: Re-raised after the engine subprocess this task
            started has been stopped and waited for.  Reached when the pool
            worker is terminated by the parent's cancellation path (as
            :exc:`SystemExit`, via :func:`_reraise_termination_as_exit`) or
            when a ``Ctrl-C`` reaches the whole process group.
    """
    from app.logging_config import configure_logging  # noqa: PLC0415,RUF100

    configure_logging(verbose=verbose)
    previous_handler = _install_termination_handler()
    try:
        return _run_one_shard(
            plan,
            tags=tags,
            browser=browser,
            dry_run=dry_run,
            cwd=cwd,
            spawn=_spawn_worker,
        )
    except BaseException:
        # This pool worker is going away.  Its engine subprocess is in its
        # own process group and would survive as an orphan -- with a browser
        # attached -- so it is stopped and waited for first, under a
        # suppressed SIGTERM so a repeat cannot abandon that half-done.  The
        # exception itself is untouched: an interrupt is the operator's
        # decision and never becomes a status.
        _suppress_termination_signal()
        terminate_live_workers()
        raise
    finally:
        _restore_termination_handler(previous_handler)


def _ordered_results(
    plans: Sequence[ShardPlan], collected: dict[int, ShardResult]
) -> list[ShardResult]:
    """Return exactly one result per plan, in shard order.

    This enforces the invariant ``app/cli.py``'s exit contract rests on:
    **every plan leaves supervision with a result.**  A plan that reached
    neither the collected results nor an exception handler -- which no known
    path produces -- would otherwise raise :exc:`KeyError` out of a run and
    become an undocumented exit ``1``; here it becomes a dead shard like any
    other supervision failure.

    Args:
        plans: The shards, in shard order.
        collected: Results by shard index, in any order.

    Returns:
        One result per plan, ordered by shard index rather than by completion,
        so everything downstream is deterministic.
    """
    ordered: list[ShardResult] = []
    for plan in plans:
        result = collected.get(plan.index)
        if result is None:
            result = _dead_shard(plan, "was never supervised to completion")
            logger.error("%s", result.reason)
        ordered.append(result)
    return ordered


def _pool_process_count(shard_count: int) -> int:
    """Size the process pool for a shard count.

    Args:
        shard_count: How many shards will be submitted.

    Returns:
        ``shard_count``, at least ``1`` and on Windows at most
        :data:`_MAX_POOL_PROCESSES`, above which the standard library refuses
        to build a pool at all.
    """
    if sys.platform == _WINDOWS_PLATFORM:
        return max(1, min(shard_count, _MAX_POOL_PROCESSES))
    return max(1, shard_count)


def _child_process_pids() -> frozenset[int]:
    """Return the pids of the multiprocessing children this process owns.

    Read through :func:`multiprocessing.active_children`, which is the public
    API for it: the executor's own collection of processes is private, and
    reaching into it would couple this module to standard-library internals
    that change between releases.

    Returns:
        The pids currently reported, omitting any child whose pid is not yet
        assigned.
    """
    return frozenset(
        child.pid
        for child in multiprocessing.active_children()
        if child.pid is not None
    )


def _terminate_new_children(
    preexisting: frozenset[int],
    *,
    grace_seconds: float = _TERMINATION_GRACE_SECONDS,
) -> int:
    """Terminate and join the multiprocessing children started since a snapshot.

    Only children **absent** from the snapshot are touched, so a host
    application that already had multiprocessing children when it called
    :func:`run_suite` keeps them: this module reclaims what it started and
    nothing else.  Each one is asked to stop and then **waited for**, because
    returning while a signalled process is still alive is the behaviour that
    leaves a browser running after the run is over.

    **The three phases are not cosmetic, and the order is the fix.**  Asking
    one child to stop, waiting for it and killing it before turning to the
    next was measured to lose the other children's cleanup: a pool that
    notices one worker has died terminates the rest itself, and those repeated
    signals arrive in the middle of another worker's own unwinding and abandon
    it half-done -- leaving exactly the orphaned engine process this function
    exists to prevent.  So every child is signalled *first*, and none is
    killed while another may still be cleaning up.

    Args:
        preexisting: Pids from :func:`_child_process_pids`, taken before the
            pool was created.
        grace_seconds: How long the children get, **together**, between the
            termination request and the kill, so the whole cancellation is
            bounded by the grace rather than by the number of workers.

    Returns:
        How many children were asked to stop.
    """
    children = [
        child
        for child in multiprocessing.active_children()
        if child.pid not in preexisting
    ]
    if not children:
        return 0

    # Phase one: ask all of them to stop, before waiting for any of them.
    for child in children:
        try:
            child.terminate()
        except (OSError, ValueError) as error:
            # Never raised onward: this runs while an interrupt or a
            # supervision failure is being reported.
            logger.warning(
                "Could not stop worker process %s: %s", child.pid, error
            )

    # Phase two: wait for all of them against one shared deadline.  Each has
    # its own engine subprocess to stop and wait for, which is what the grace
    # is for.
    deadline = time.monotonic() + grace_seconds
    for child in children:
        child.join(max(0.0, deadline - time.monotonic()))

    # Phase three: whatever is left ignored the request or is stuck.
    for child in children:
        if not child.is_alive():
            continue
        logger.warning(
            "Worker process %s did not stop within %.0fs; killing it",
            child.pid,
            grace_seconds,
        )
        try:
            child.kill()
            child.join(grace_seconds)
        except (OSError, ValueError) as error:
            logger.warning(
                "Could not kill worker process %s: %s", child.pid, error
            )

    logger.warning("Stopped %d worker process(es) of this run", len(children))
    return len(children)


def _abandon_executor(
    executor: Executor, *, pool_children: frozenset[int] | None = None
) -> None:
    """Stop an executor now, without waiting for the work still in flight.

    ``shutdown(wait=True)`` -- which is what leaving a ``with`` block does --
    is precisely wrong here: it joins the workers, so a single hung child
    would hold the interrupt, the merge and the reporting open indefinitely.
    So the executor is told to stop and to cancel whatever it has not started,
    and the children are then reclaimed explicitly.

    Args:
        executor: The executor to abandon.
        pool_children: For a process pool, the pids that existed before it was
            created, from :func:`_child_process_pids`; every child started
            since is terminated and joined.  ``None`` for a thread pool, which
            owns no child process of its own.

    Returns:
        ``None``.
    """
    executor.shutdown(wait=False, cancel_futures=True)
    # Workers this process started itself -- the sequential and seam paths --
    # first, then the pool's children, each of which stops its own worker as
    # it goes.
    terminate_live_workers()
    if pool_children is not None:
        _terminate_new_children(pool_children)


def _run_shards_in_pool(
    plans: Sequence[ShardPlan],
    *,
    tags: str | None,
    browser: str | None,
    dry_run: bool,
    cwd: Path,
) -> list[ShardResult]:
    """Run the shards through a process pool -- the model D04 prescribes.

    One :class:`~concurrent.futures.ProcessPoolExecutor` process per shard
    (bounded by :func:`_pool_process_count`), each running
    :func:`_run_shard_task`, each of those launching and supervising its own
    engine subprocess.  Neither ``mp_context`` nor ``max_tasks_per_child`` is
    passed: the platform's default start method is the correct one here, and a
    task limit would buy a fresh interpreter per shard for isolation this
    module does not need, since a worker's real state lives in its behave
    subprocess rather than in the pool process.

    **Nothing escapes this function.**  Pool construction, submission, a
    worker process lost mid-shard, and a task that somehow failed are each
    converted into a named dead shard, and results already collected are
    kept: a supervision failure is a documented non-zero class with artifacts
    written from the shards that completed, never an undocumented exit ``1``.
    The single exception is a :exc:`BaseException` -- an interrupt -- which
    cancels the pool, reclaims its children and is then re-raised untouched,
    because stopping a run is the operator's decision and this module turns no
    such decision into a status.

    **One requirement this places on the caller**, and it is the standard
    :mod:`multiprocessing` one rather than anything of this module's: under
    ``forkserver`` and ``spawn`` the child imports the parent's ``__main__``,
    so that module must be import-safe.  The ``run-tests`` console script is
    (its body sits under an ``if __name__ == "__main__"`` guard, verified in
    the installed script), and a program embedding :func:`run_suite` must be
    too.

    Args:
        plans: The shards, in shard order.  Two or more; a single shard is the
            sequential path.
        tags: The user's tag expression, or ``None``.
        browser: Browser override, or ``None``.
        dry_run: Whether to pass ``--dry-run``.
        cwd: The run base, which each worker runs in.

    Returns:
        One result per shard, ordered by shard index.

    Raises:
        BaseException: Re-raised, and only for an interrupt: the pool is shut
            down without waiting and every child it started is terminated and
            joined first.
    """
    collected: dict[int, ShardResult] = {}
    # Taken before the pool exists, so the cancellation path can tell the
    # pool's own children from any the caller already had.
    preexisting = _child_process_pids()
    # The child cannot read the parent's logger, so verbosity travels with the
    # task rather than being rediscovered there.
    verbose = logger.getEffectiveLevel() <= logging.DEBUG
    size = _pool_process_count(len(plans))

    try:
        executor = ProcessPoolExecutor(max_workers=size)
    except (OSError, RuntimeError, ValueError) as error:
        # Nothing can run, and this must still be an outcome rather than an
        # exception: every shard becomes a named dead shard, which app/cli.py
        # maps to its dead-worker status.
        logger.error(
            "A worker pool of %d process(es) could not be created (%r); no "
            "shard was executed",
            size,
            error,
        )
        return [
            _dead_shard(
                plan,
                f"could not be supervised: a worker pool of {size} "
                f"process(es) could not be created ({error!r})",
            )
            for plan in plans
        ]

    logger.info(
        "Supervising %d shard(s) in a pool of %d worker process(es)",
        len(plans),
        size,
    )
    futures: dict[Future[ShardResult], ShardPlan] = {}
    try:
        for plan in plans:
            try:
                future = executor.submit(
                    _run_shard_task, plan, tags, browser, dry_run, cwd, verbose
                )
            except (OSError, RuntimeError, ValueError) as error:
                # RuntimeError covers a pool already shut down and, as its
                # subclass, a pool broken by an earlier submission.
                collected[plan.index] = _dead_shard(
                    plan,
                    f"could not be submitted to the worker pool ({error!r})",
                )
            else:
                futures[future] = plan

        # Results are taken as they complete, so a dead shard is reported
        # while the others are still running.
        for future in as_completed(futures):
            plan = futures[future]
            try:
                result = future.result()
            except BrokenProcessPool as error:
                # The pool process holding this shard died outright -- killed
                # by the OS, or its interpreter crashed.  Every other pending
                # future fails the same way and each is reported against its
                # own shard.
                result = _dead_shard(
                    plan, f"lost its worker process ({error!r})"
                )
            except Exception as error:  # noqa: BLE001
                # Deliberately broad, and the last line of defence:
                # supervision must never abort the run.
                result = _dead_shard(
                    plan, f"failed while being supervised ({error!r})"
                )
            collected[result.plan.index] = result
    except Exception as error:  # noqa: BLE001
        # Supervision itself failed, outside any one shard.  The pool is
        # abandoned and every shard still without a result is recorded.
        logger.error("The worker pool failed while supervising (%r)", error)
        _abandon_executor(executor, pool_children=preexisting)
        for plan in plans:
            collected.setdefault(
                plan.index,
                _dead_shard(plan, f"could not be supervised ({error!r})"),
            )
    except BaseException:
        _abandon_executor(executor, pool_children=preexisting)
        raise
    else:
        # Every task is finished, so this join is immediate.
        executor.shutdown(wait=True)

    return _ordered_results(plans, collected)


def _run_shards_in_threads(
    plans: Sequence[ShardPlan],
    *,
    tags: str | None,
    browser: str | None,
    dry_run: bool,
    cwd: Path,
    spawn: SpawnCallable,
) -> list[ShardResult]:
    """Run the shards in this process, supervised by threads.

    **Reached only when the caller injected a ``spawn`` seam.**  A stub is not
    picklable and a real run therefore never comes here; what this path buys
    is that a test can observe its own seam, in its own process, while still
    exercising the concurrent code path.  The guarantees are identical to
    :func:`_run_shards_in_pool`'s -- one named result per shard, nothing but
    an interrupt escaping -- so the two are interchangeable downstream.

    Args:
        plans: The shards, in shard order.  Two or more.
        tags: The user's tag expression, or ``None``.
        browser: Browser override, or ``None``.
        dry_run: Whether to pass ``--dry-run``.
        cwd: The run base, which each worker runs in.
        spawn: The injected launch seam.

    Returns:
        One result per shard, ordered by shard index.

    Raises:
        BaseException: Re-raised, and only for an interrupt: the pool is shut
            down without waiting first.
    """
    collected: dict[int, ShardResult] = {}
    try:
        executor = ThreadPoolExecutor(
            max_workers=len(plans), thread_name_prefix=_SHARD_THREAD_PREFIX
        )
    except (OSError, RuntimeError, ValueError) as error:
        logger.error(
            "A supervision pool of %d thread(s) could not be created (%r); "
            "no shard was executed",
            len(plans),
            error,
        )
        return [
            _dead_shard(
                plan,
                f"could not be supervised: a supervision pool of "
                f"{len(plans)} thread(s) could not be created ({error!r})",
            )
            for plan in plans
        ]

    futures: dict[Future[ShardResult], ShardPlan] = {}
    try:
        for plan in plans:
            try:
                future = executor.submit(
                    _run_one_shard,
                    plan,
                    tags=tags,
                    browser=browser,
                    dry_run=dry_run,
                    cwd=cwd,
                    spawn=spawn,
                )
            except (OSError, RuntimeError, ValueError) as error:
                # A thread that cannot be started, or a pool already shut
                # down: the shard is dead, the others carry on.
                collected[plan.index] = _dead_shard(
                    plan,
                    f"could not be submitted for supervision ({error!r})",
                )
            else:
                futures[future] = plan

        for future in as_completed(futures):
            plan = futures[future]
            try:
                result = future.result()
            except Exception as error:  # noqa: BLE001
                # Deliberately broad, and the last line of defence:
                # supervision must never abort the run.
                result = _dead_shard(
                    plan, f"failed while being supervised ({error!r})"
                )
            collected[result.plan.index] = result
    except Exception as error:  # noqa: BLE001
        logger.error("Thread supervision failed (%r)", error)
        _abandon_executor(executor)
        for plan in plans:
            collected.setdefault(
                plan.index,
                _dead_shard(plan, f"could not be supervised ({error!r})"),
            )
    except BaseException:
        _abandon_executor(executor)
        raise
    else:
        executor.shutdown(wait=True)

    return _ordered_results(plans, collected)


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

    Three paths, and which one runs is fully determined by the inputs:

    * **one shard** -- the ``--workers 1`` sequential mode AAP 0.6 names.  It
      runs in this process and builds no executor at all.
    * **an injected ``spawn`` seam** -- threads in this process, because a
      stub cannot be pickled across a process boundary; see
      :func:`_run_shards_in_threads`.
    * **anything else, which is every real run** -- the process pool AAP
      deviation D04 prescribes; see :func:`_run_shards_in_pool`.

    The concurrency unit is the **OS process** in all three: even sequentially
    the engine runs as a child, because a Selenium session is not
    thread-shareable and ``parallel=methods`` (``pom.xml:22``) has no
    thread-level equivalent here.  What the pool adds over the threads it
    replaces is that the supervision itself is a process too, so a shard's
    engine has a parent that can be signalled, waited for and accounted for
    independently of this one.

    Args:
        plans: The shards, in shard order.
        tags: The user's tag expression, or ``None``.
        browser: Browser override, or ``None``.
        dry_run: Whether to pass ``--dry-run``.
        base: Directory the run resolves against.
        spawn: The launch seam.

    Returns:
        One result per shard, ordered by shard index rather than by completion
        order, so everything downstream is deterministic.  Every shard is
        accounted for: no failure of the supervision machinery leaves a plan
        without a result, and none of those failures is raised out of here.

    Raises:
        BaseException: Re-raised, and only for an interrupt, after the
            children this run started have been stopped and waited for.
    """
    cwd = _run_base(base)
    if len(plans) == 1:
        # --workers 1 is the sequential mode, and takes no pool at all.
        try:
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
        except BaseException:
            # Only an interrupt reaches here: _run_one_shard records every
            # Exception as a dead shard.  The worker is in its own process
            # group, so it has to be stopped explicitly before the interrupt
            # travels on -- and it travels on untouched, because stopping a
            # run is the operator's decision and never a status.
            terminate_live_workers()
            raise

    if spawn is not _spawn_worker:
        return _run_shards_in_threads(
            plans,
            tags=tags,
            browser=browser,
            dry_run=dry_run,
            cwd=cwd,
            spawn=spawn,
        )

    return _run_shards_in_pool(
        plans, tags=tags, browser=browser, dry_run=dry_run, cwd=cwd
    )


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
        ``app/cli.py``'s decision.  That includes
        :attr:`RunOutcome.infrastructure_error`, which this function sets when
        the run's own intermediate directory could not be created -- in which
        case nothing was executed -- or could not be removed afterwards, in
        which case the results are still returned and only the workspace is
        unclean.

    Raises:
        TagExpressionError: If ``tags`` is malformed.  The one propagating
            failure, because an invalid option value is a usage error rather
            than a test outcome; see :func:`_parse_tag_expression`.
        BaseException: An interrupt is re-raised untouched, after this run's
            children have been stopped and its intermediate directory
            removed: stopping a run is the operator's decision, and this
            module turns no such decision into an outcome.
        SystemExit: What a ``SIGTERM`` becomes for the duration of the run,
            for the same reason and by the same path: a handler is installed
            around the execution phase so that a terminated supervisor
            unwinds - stopping its engine, driver and browser and removing
            its intermediates - instead of dying between statements and
            orphaning them.  The previous disposition is restored afterwards.
    """
    spawn_worker: SpawnCallable = _spawn_worker if spawn is None else spawn
    # What the run records, as against what goes on the command line: the
    # neutral tautology is a mechanism and must never surface in a report.
    recorded_tags = _recorded_tag_expression(tags, rerun)

    if rerun:
        selected, problems = select_rerun_scenarios(base=base)
    else:
        selected, problems = select_scenarios(tags=tags, base=base)

    # Tolerated failures: survived, never raised and never counted as a dead
    # worker.  Each leaves the run at status 0, matching the source's own
    # tolerance of a missing configuration file.
    #
    # They are *carried* rather than logged.  Every one of them reaches the
    # caller verbatim on ``RunOutcome.parse_errors``, and ``app/cli.py`` is
    # the single emitter of the record that names it - one ERROR counting
    # them, then one naming each.  Emitting them here as well would put one
    # incident in the CI console twice under two logger names, doubling the
    # error count a publisher and a reader see for a run whose status the
    # exit contract keeps at 0.  This is the module's half of the one-emitter
    # rule stated in its docstring: a fact an outcome carries is reported by
    # the command that reads the outcome, never by both.
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

    # Prepared *before* sharding, because every shard's output path lives
    # inside this run's own directory and the plans carry those paths.
    try:
        run_dir = prepare_workers_dir(base=base)
    except OSError as error:
        # Nowhere for the workers to write, so nothing can be produced and
        # nothing is spawned.  This is neither a tolerated failure nor a dead
        # worker: it is a failure of this port's own intermediate storage, and
        # it is reported as one so that it cannot be mistaken -- under
        # ``rerun`` in particular -- for a run that simply wrote no artifact.
        # Carried on ``infrastructure_error``, not logged here: the directory
        # path and the operating system's own reason travel in the message
        # text, and ``app/cli.py`` names it once alongside the exit class it
        # produces.
        message = (
            f"{workers_dir(base)}: this run's per-worker directory cannot be "
            f"created ({error}); no scenario was executed"
        )
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
            infrastructure_error=message,
        )

    plans = shard_scenarios(selected, worker_count, base=base, run_dir=run_dir)
    logger.info(
        "Sharding %d scenario(s) over %d worker(s) in %s",
        selected_count,
        len(plans),
        run_dir,
    )

    cleanup_error: str | None = None
    # For the duration of the run only, SIGTERM raises instead of killing this
    # process outright.  Without it a ``kill`` of the supervisor - which is
    # what a CI job's "abort" sends - takes the default disposition and dies
    # between statements, leaving the engine, its driver and its browser
    # running in their own process groups with nothing left to wait for them.
    # Raising unwinds through the same path an interrupt takes, so the
    # children are stopped and waited for and this run's intermediates are
    # removed.  Installed here rather than in ``app/cli.py`` because this is
    # the frame that owns the children; restored in the ``finally`` so a
    # caller's own disposition survives, and a no-op off the main thread,
    # where signal handlers cannot be installed at all.
    previous_termination_handler = _install_termination_handler()
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
    except BaseException:
        # An interrupt, a SIGTERM turned into one by the handler above, or a
        # defect: every worker this run started is stopped and waited for
        # before the exception travels on, and the ``finally`` below still
        # removes the intermediates.
        terminate_live_workers()
        raise
    finally:
        _restore_termination_handler(previous_termination_handler)
        # Removed whether the merge succeeded or failed, and whether or not an
        # interrupt is travelling through this frame, so no intermediate
        # worker document is ever left where the Jenkins publisher could see
        # it.  Only *this* run's directory is removed -- a concurrent run's is
        # neither read nor deleted -- and a failure to remove it is recorded
        # rather than swallowed: this module owns the directory, so it owns
        # saying whether it is gone.
        cleanup_error = cleanup_workers_dir(base=base, directory=run_dir)

    # A dead shard never suppresses artifacts: the merged set from the shards
    # that did complete is still returned.  Each reason names its shard, its
    # scenario count and what went wrong, and it is carried on
    # ``RunOutcome.dead_shards`` rather than logged here - ``app/cli.py``
    # emits one record per reason beside the status a dead shard implies, so
    # the incident and its consequence read as one account instead of
    # appearing twice under two logger names.
    dead_shards = tuple(
        result.reason for result in shard_results if result.dead and result.reason
    )

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
        # non-zero, and the build output left as the clean step left it.
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
        # The run executed and produced whatever it produced; what failed is
        # the removal of its intermediates.  Reported rather than raised, and
        # reported *with* the results rather than instead of them, so the
        # caller still writes the four artifacts and then declines to call the
        # run a success.
        infrastructure_error=cleanup_error,
    )
