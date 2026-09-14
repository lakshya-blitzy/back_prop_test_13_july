"""Scenario selection, sharding, worker invocation and result merging.

This module is the executable form of the Java build's test-execution
configuration -- ``parallel=methods``, ``useUnlimitedThreads=true`` and
``testFailureIgnore=true`` (``pom.xml:21-29``), latent there because the
surefire include matches test classes the source project does not have.

* The sharding unit is the **scenario**, ``parallel=methods`` being
  method-level: units are distributed round-robin grouped by feature, so a
  feature's Background runs once per scenario inside the one worker that
  holds it (:func:`shard_scenarios`).
* Concurrency is **process-based, sized at the CPU count** by default -- a
  :class:`~concurrent.futures.ProcessPoolExecutor` over
  :func:`_run_shard_task` -- because no in-process thread model applies to a
  Python Selenium session.  This is **AAP deviation 4**, not equivalence with
  ``useUnlimitedThreads``; an explicit ``--workers`` request is bounded, a
  worker here being an OS process driving a browser.
* The merge produces **one ordered set keyed by feature path**, identical
  whatever the worker count (:func:`merge_worker_results`).
* **No test outcome reaches the exit status**: this module exits no process
  and turns no scenario outcome into an error.  Only a dead worker, an empty
  merge or a writer failure is non-zero, and ``app/cli.py`` owns the statuses
  and their precedence, reading the independent signals
  :class:`RunOutcome` carries.
* The per-worker intermediate directory (:func:`~app.utils.paths.workers_dir`)
  is created before the run, and :func:`cleanup_workers_dir` runs from a
  ``finally`` path whether the merge succeeded or not.  A removal that fails is
  reported rather than raised, surfaces as an infrastructure error and makes
  the status non-zero, so a surviving intermediate cannot pass unremarked - and
  ``Jenkins:15`` narrows the publisher to one file, which is not one of them.

Reporting is divided: a fact :class:`RunOutcome` carries is logged by the
command that reads the outcome, never here as well, so one incident produces
one record.  What this module logs is the progress no outcome field carries -
the selection, the shard sizing, each shard's start, the closing counts.

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
manifest's grammar **together with the verified listing and reading of the
feature files themselves** (:mod:`app.reporting.rerun_report`).  There is no
platform abstraction here, no clone logic, no threshold logic and no driver
code -- and, since the feature-file authority moved there, not one filesystem
resolution of a feature path either: selection asks that module for the
contents it verified and for the identity it verified them under, which is
what stops a name from being checked here and opened again later (CWE-367).

What it does own, beyond running the shards
-------------------------------------------
Two exclusion mechanisms, because both of them live in the directory this
module is the single owner of and neither can be answered from outside it:

* A **per-run lease** -- a lock file inside each run's own intermediate
  directory, held for the life of that run, so that another invocation can
  tell a run in progress from a run that ended (:func:`run_directory_is_active`
  and :func:`prepare_workers_dir`).  Liveness is the lock and never the shape
  of a directory name: a name carrying a process id some unrelated process
  happens to own would read as live indefinitely, keeping another run's
  tracebacks, screenshots and scenario data in the workspace for as long as
  that process lived.
* A **run lock** -- one claim on the whole build output, taken by
  ``app/cli.py`` before its clean step and given back after the publication
  (:func:`acquire_run_lock`).  The lifecycle is the command's because the
  command spans the three phases; the lock is here because the file lives
  beside the run directories.

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
import errno
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

# The platform's file-locking primitive, and the reason it is imported this
# way rather than behind a ``sys.platform`` test: CPython ships ``fcntl`` on
# POSIX and ``msvcrt`` on Windows, never both and never neither, so the
# presence of the module *is* the platform test, and importing both here means
# the two module-level names below are the only place the difference appears.
# AAP section 0.8 makes Windows a supported platform, so the second branch is a
# real code path rather than a formality.
try:  # pragma: no cover - the branch not taken on this platform
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - Windows
    _fcntl = None  # type: ignore[assignment]
try:  # pragma: no cover - the branch not taken on this platform
    import msvcrt as _msvcrt
except ImportError:  # pragma: no cover - POSIX
    _msvcrt = None  # type: ignore[assignment]

# behave's own parser, deliberately rather than a hand-rolled Gherkin reader:
# selection must not be able to disagree with what the engine will actually
# run.  ``ParserError`` is what a malformed feature file raises (measured).
#
# ``parse_feature`` rather than ``parse_file``, and the difference is the whole
# of this module's half of the path-safety fix: ``parse_file`` takes a *name*
# and opens it, which is a second resolution of something selection has
# already checked (CWE-367).  Verified against behave's own source on the
# pinned version, ``parse_file`` is exactly::
#
#     with open(filename, "rb") as f:
#         data = f.read().decode("utf8")
#     return parse_feature(data, language, filename)
#
# so handing the text read from the verified descriptor to ``parse_feature``
# is engine-identical and removes the reopen.
from behave.parser import ParserError, parse_feature

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
    RunResultBudget,
    load_result_set,
    merge_result_sets,
    new_result_set,
)

# ``RerunManifestError`` is not re-exported by ``app/reporting/__init__.py``,
# so both names come from the submodule -- which is also the import form that
# package's docstring prefers, since a submodule import tolerates a
# half-initialised parent package.
#
# The four verification names are the feature-file half of the same authority:
# that module opens the features directory and each entry ``O_NOFOLLOW`` under
# a verified parent, returns the *contents it read* together with the object
# identity of the descriptor it read them from, and re-checks that identity on
# demand.  Selection here therefore decides on a checked object rather than on
# a pathname, which is what closes the check-then-reopen race this module used
# to carry.
from app.reporting.rerun_report import (
    LINE_SEPARATOR,
    RerunManifestError,
    VerifiedFeature,
    list_verified_features,
    parse_rerun_file,
    read_verified_feature,
    verify_feature_identity,
)
from app.utils.paths import (
    FILE_URI_SCHEME,
    ensure_dir,
    normalize_feature_uri,
    rerun_txt_path,
    target_root,
    worker_result_path,
    workers_dir,
)

__all__ = [
    "NEUTRAL_TAG_EXPRESSION",
    "RUN_LOCK_NAME",
    "RunLock",
    "RunOutcome",
    "ScenarioRef",
    "ShardPlan",
    "ShardResult",
    "acquire_run_lock",
    "build_worker_command",
    "cleanup_workers_dir",
    "default_worker_count",
    "delete_verified_entry",
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

#: Module logger, acquired from the standard library like every other module's.
#:
#: ``app/logging_config.py`` installs the handler split this module's output
#: depends on -- ``INFO`` and below to stdout as progress, ``WARNING`` and
#: above to stderr as diagnostics -- and every handler it installs carries the
#: sanitizing formatter that module documents, so the records below are
#: relativized, rendered control-safe, redacted and bounded on the way out
#: without this module rendering anything itself.  That is what keeps a
#: diagnostic quoting an absolute intermediate path or an operating system's
#: own error text from publishing workspace topology or forging a record.
#:
#: This module reaches that module twice, and the two are deliberately
#: different: :func:`~app.logging_config.render_worker_line` is imported at
#: module scope, because the relay chooses each line's severity and tag and so
#: must render explicitly; :func:`~app.logging_config.configure_logging` is
#: imported inside the pool task alone, because a worker process installs the
#: split for itself and importing this module -- which every child does, and
#: which selection and merging in the parent do too -- must not pull the
#: configuration in.
logger = logging.getLogger(__name__)


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

#: Tag sigil.  behave's model stores tag names **without** it -- measured:
#: ``Crm.feature``'s feature tag arrives as ``'Smoke'`` -- while a tag
#: expression is written with it, so it is re-added before evaluation.
#: Forgetting this is the failure mode that silently selects zero scenarios.
_TAG_SIGIL: Final[str] = "@"


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

#: An **empty stage**, which is what binds a worker's glue to the tracked
#: ``features/steps`` and ``features/environment.py``.
#:
#: behave selects both by prefixing them with the stage name: a stage of
#: ``product`` means ``product_steps`` and ``product_environment.py``.  The
#: stage is a plain string it will take from the ``BEHAVE_STAGE`` environment
#: variable whenever nothing else supplies one, it is joined to the base
#: directory with :func:`os.path.join`, and the environment file is
#: **executed**.  An absolute value therefore wins outright and redirects
#: both roots outside the checkout.
#:
#: **Verified by execution**, on this interpreter and this engine version:
#: ``Configuration(['--no-color'])`` with ``BEHAVE_STAGE=/external/path`` in
#: the environment yields ``stage='/external/path'``,
#: ``steps_dir='/external/path_steps'`` and
#: ``environment_file='/external/path_environment.py'``, while
#: ``Configuration(['--no-color','--stage='])`` yields ``stage=''`` with
#: ``steps_dir='steps'`` and ``environment_file='environment.py'`` -- the
#: engine consults the variable only when the stage is ``None``, so an
#: explicit empty value is what makes it unreachable.
#:
#: Written as a **single argv element** with the value attached, because an
#: empty value as a separate element would be indistinguishable from a
#: missing one and would swallow whatever argument followed.  ``behave.ini``
#: carries the same empty ``stage`` key, which covers a direct ``behave``
#: invocation; this flag covers a worker even if the key is ever lost, and
#: the two agreeing costs nothing.
_STAGE_FLAG: Final[str] = "--stage="

#: behave userdata key carrying the browser override.  ``app/config.py`` reads
#: it with userdata-first precedence inside the worker, and it is the only
#: override path -- no environment layer is added.
_BROWSER_USERDATA_KEY: Final[str] = "browser"


@runtime_checkable
class WorkerProcess(Protocol):
    """The result of one worker launch, as :func:`run_suite` reads it.

    :class:`subprocess.CompletedProcess` satisfies this structurally, which is
    what lets the ``spawn`` seam be stubbed with any object carrying the same
    three attributes.  ``output_relayed`` is deliberately **not** a member:
    adding it would stop a plain :class:`~subprocess.CompletedProcess` from
    satisfying the protocol, so it is an *optional* convention read with
    :func:`getattr`.  A launch that relayed its output line by line as it
    arrived -- which the real one does, see :func:`_spawn_worker` -- carries
    it as ``True``, and :func:`_relay_output` then prints nothing rather than
    showing every line a second time.

    Attributes:
        returncode: The engine's exit status.  **A positive non-zero value is
            normal** -- behave exits ``1`` when scenarios fail -- and is never
            treated as an error here.
        stdout: Standard output, or ``None``.  Relayed as run progress by
            :func:`_relay_output` unless the launch already relayed it live.
        stderr: Standard error, or ``None``.  Relayed as engine diagnostics
            under the same rule.
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
            filenames are never renamed or normalised.
        line: The executable unit's **own** line.  For a scenario outline that
            is the Examples **data-row** line, never the outline header's:
            the JSON contract records an outline row with the data row's line
            and the rerun manifest lists those same numbers.
        name: The unit's name as behave reports it, including behave's
            ``" -- @1.1 <Examples>"`` annotation on a generated outline row;
            stripping that for the JSON report belongs to
            :mod:`app.reporting.events`, which owns that contract.
            Informational: it appears in log messages and nowhere else.
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
    no precedence is encoded here.  An empty ``result_set``, a ``None`` one
    with ``merge_produced_nothing``, and a rerun's ``None`` are three distinct
    states: one falsy check collapses two different exit rows.

    Attributes:
        result_set: The merged document, **empty** when nothing was selected,
            ``None`` when nothing could be merged and under ``rerun``.
        selected_count: Units selected; ``0`` is status ``0``, artifacts empty.
        worker_count: Workers that ran, ``0`` when none was spawned.
        shard_results: One entry per shard, in shard order.
        dead_shards: Each dead shard's ``reason``, already logged at ``ERROR``.
        parse_errors: Tolerated problems, each leaving the run at status ``0``.
        merge_produced_nothing: Set only when units were selected and not one
            worker file could be read; never under ``rerun``.
        rerun: A rerun writes no artifacts, so no writer is invoked for it.
        dry_run: Whether the engine was asked not to execute steps.
        tag_expression: The filter the user expressed, or ``None``; never
            :data:`NEUTRAL_TAG_EXPRESSION`, a mechanism and not a filter.
        infrastructure_error: Why this run's per-worker directory could not be
            created (nothing executed) or removed (intermediates remain), or
            ``None``.  Read before ``app/cli.py``'s ``rerun`` short-circuit.
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


def _feature_files(base: Path | str | None) -> tuple[list[str], list[str]]:
    """List the suite's feature files in canonical order, through the owner.

    Delegated in full to
    :func:`~app.reporting.rerun_report.list_verified_features`, which holds
    this port's feature-file authority: it opens the features directory
    ``O_NOFOLLOW`` relative to its own parent and examines every entry
    relative to *that* descriptor, so a symbolic link standing where the
    directory should be is refused instead of followed, and a linked or
    hard-linked entry inside it is refused instead of selected.  The listing
    this module used to do itself walked pathnames with
    :meth:`~pathlib.Path.iterdir` and :meth:`~pathlib.Path.is_file`, both of
    which follow links, which is how a redirected features root came to select
    scenarios from outside the suite (CWE-22).

    The order is the run's **canonical feature order** -- ascending by
    repository-relative path -- imposed by that function and reused by the
    merge, because the merged structure has to come out identical whatever the
    worker count.

    Args:
        base: Directory the features directory hangs off, or ``None`` for the
            working directory.

    Returns:
        A ``(paths, problems)`` pair.  ``paths`` holds the accepted feature
        files as repository-relative, forward-slashed paths -- the spelling
        every artifact carries and the spelling the engine is given.
        ``problems`` names a missing, unlistable or empty directory and every
        refused entry, each naming the directory it concerns -- and each a
        tolerated condition that yields fewer (or zero) selected scenarios and
        status ``0``, never an exception.
    """
    return list_verified_features(base)


def _units_of(verified: VerifiedFeature) -> tuple[list[ScenarioRef], list[str]]:
    """Parse one **verified** feature into its executable units.

    Takes the checked object rather than a path, which is the point: the text
    parsed here is the text that was read from the descriptor the features-root
    verification approved, so a swap of the entry between the check and the
    parse cannot change what is selected.  Nothing is opened in this function.

    Args:
        verified: The feature as
            :func:`~app.reporting.rerun_report.read_verified_feature` returned
            it -- its repository-relative path, its object identity and the
            contents that were read.

    Returns:
        A ``(units, problems)`` pair.  Outlines are expanded into their
        generated example scenarios, each carrying its own Examples data-row
        line, by way of behave's ``walk_scenarios()`` -- the engine's own
        traversal, so selection cannot disagree with execution.  ``problems``
        carries a message naming the file when it could not be parsed, in
        which case ``units`` is empty and **the caller carries on with the
        remaining features**: a parse failure must never abort a run.
    """
    feature_path = verified.path
    try:
        # The relative path is handed over as the parser's ``filename`` -- it
        # is only ever used for the model's own bookkeeping and for
        # ``ParserError.filename``, and the relative spelling is both the one
        # every artifact carries and the one that puts no workspace topology
        # into a diagnostic (CWE-532).
        feature = parse_feature(verified.text, None, feature_path)
    except ParserError as error:
        return [], [f"{feature_path}: cannot be parsed ({error})"]
    except (OSError, UnicodeDecodeError) as error:
        # Retained even though this function no longer opens anything: the
        # parser reads no file, but it is a third-party callable and the
        # tolerated classification of an I/O or decoding failure belongs here
        # rather than in a traceback.
        return [], [f"{feature_path}: cannot be read ({error})"]
    except Exception as error:  # noqa: BLE001
        # Deliberately broad.  behave's parser raises ParserError for the
        # malformed input measured here, but a defect in a single feature file
        # must not be able to abort a whole run under any exception type; the
        # file is named and the run continues.
        return [], [f"{feature_path}: cannot be parsed ({error!r})"]

    if feature is None:
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


def _verified_or_problem(
    feature_path: str,
    base: Path | str | None,
    *,
    named_by: Path | None = None,
    position: int | None = None,
) -> tuple[VerifiedFeature | None, list[str]]:
    """Open and verify one feature, turning a refusal into a tolerated problem.

    The single place this module obtains a feature's contents, so both
    selection paths refuse for the same reasons and report them the same way.

    Args:
        feature_path: The feature's repository-relative path, as the verified
            listing or the manifest's grammar spells it.
        base: Directory the features directory hangs off.
        named_by: The input that asked for this feature, when it was not the
            feature directory's own listing -- the rerun manifest's path.
            Named in the message so an operator knows which input to correct;
            ``None`` for an ordinary run, where the suite itself asked.
        position: The entry's one-based position in that input, used **instead
            of** the feature path in the message.  A path a manifest supplied
            is untrusted text, and this message is written to stderr and
            recorded by a CI console verbatim, so echoing it is a disclosure
            channel (CWE-532).  A position locates the entry just as well and
            carries nothing.  With ``named_by`` left ``None`` the feature is
            named instead, which is what an ordinary run wants: there the path
            came from this port's own verified listing of the repository's
            features directory, and every artifact the run writes names it.

    Returns:
        A ``(verified, problems)`` pair.  ``verified`` is ``None`` when the
        entry was refused -- an invalid path, a linked features root, a linked
        or hard-linked entry, a non-regular entry, an absent file, one beyond
        the owner's byte bound, contents that are not UTF-8, or a filesystem
        failure -- in which case ``problems`` carries one message naming the
        feature.  The owner has already logged *which* of those it was, at
        ``WARNING`` and therefore on stderr; this message exists so that the
        condition also reaches the caller on
        :attr:`RunOutcome.parse_errors`, the way every other tolerated
        problem does.  A refusal is never an exception: per the AAP 0.4.1 exit
        table an unusable feature leaves the run at status ``0``.
    """
    verified = read_verified_feature(feature_path, base=base)
    if verified is None:
        if named_by is None:
            subject = feature_path
        else:
            entry = "an entry" if position is None else f"entry {position}"
            subject = f"{entry} of {named_by}"
        return None, [
            f"{subject}: cannot be verified as a feature file of this suite, "
            "so its contents are not read and none of its locations is "
            "executed; the reason is on stderr"
        ]
    return verified, []


def _dropped_for_replacement(
    selected: Sequence[ScenarioRef],
    identities: dict[str, tuple[int, ...]],
    base: Path | str | None,
) -> tuple[list[ScenarioRef], list[str]]:
    """Re-check every selected feature's identity and drop the ones that moved.

    The hand-off check, and the reason selection records an identity at all.
    The engine runs in another process and opens ``features/<name>.feature``
    **by name**, because AAP deviation 1 pins that spelling into the JSON
    ``uri``, the rerun manifest and the commands operators type, so executing
    from a snapshot under some other path would break three published
    contracts.  What is available instead is this: immediately before the
    workers are built, each feature selection read is re-opened under the same
    ``O_NOFOLLOW`` verification and its object identity compared with the one
    recorded when its scenarios were chosen.  An entry that is no longer the
    file that was checked is **dropped and named** rather than silently
    executed (CWE-367).

    **Where the residual window is.**  It is between this comparison and the
    worker's own open of the same name, and it cannot be closed from inside
    this process without abandoning the pinned path spelling above.  What the
    check does remove is the window that mattered: the one spanning selection,
    tag evaluation, sharding and command construction, during which a probe
    could replace an approved file at leisure and have its contents executed
    under the approved file's name.

    Args:
        selected: The units selection chose, in canonical order.
        identities: The object identity recorded per feature path while its
            contents were read.  A feature path absent from this mapping is
            **left alone**: under a rerun a location whose feature could not
            be read is deliberately kept rather than dropped, and this
            function must not undo that.
        base: Directory the features directory hangs off.

    Returns:
        A ``(surviving, problems)`` pair.  ``problems`` carries one message per
        dropped feature; each leaves the run at status ``0`` per the AAP 0.4.1
        exit table, exactly as a feature that fails to parse does.
    """
    replaced: set[str] = set()
    problems: list[str] = []
    # Sorted so the messages come out in canonical feature order whatever the
    # insertion order of the mapping was.
    for feature_path in sorted(identities):
        identity = identities[feature_path]
        if not verify_feature_identity(feature_path, identity, base=base):
            replaced.add(feature_path)
            problems.append(
                f"{feature_path}: the file changed after its scenarios were "
                "selected, so none of them is executed; the reason is on "
                "stderr"
            )
    if not replaced:
        return list(selected), problems
    return [unit for unit in selected if unit.feature_path not in replaced], problems


def select_scenarios(
    *,
    tags: str | None = None,
    base: Path | str | None = None,
) -> tuple[list[ScenarioRef], list[str]]:
    """Select the executable units a run should execute.

    Pure apart from reading the feature files: what this returns is exactly
    what gets sharded.

    Args:
        tags: Tag expression to filter by, or ``None`` for no filter, which
            selects everything.  ``app/cli.py`` passes ``"@Smoke"``, which is
            ``CukesRunner``'s own default and is declared once in the suite at
            ``Crm.feature:1``, so a default run selects the CRM feature alone;
            the five features carrying no feature-level tag are reachable by a
            negative expression such as ``not @Smoke``.
        base: Directory the features directory hangs off, or ``None`` for the
            working directory.

    Returns:
        A ``(selected, parse_errors)`` pair.  ``selected`` is ordered by
        feature path and then by line -- the canonical order the sharding and
        the merge both rely on.  Only features the
        :mod:`app.reporting.rerun_report` authority listed, opened and read
        contribute to it, so a linked features root, a linked or hard-linked
        entry and an unreadable file each select nothing rather than something
        from outside the suite.  ``parse_errors`` holds the messages of every
        tolerated problem -- a refused directory, a refused entry, a feature
        whose contents could not be verified and a feature that could not be
        parsed -- each of which leaves the run at status ``0``.

    Raises:
        TagExpressionError: If ``tags`` is malformed; see
            :func:`_parse_tag_expression` for why this one propagates.
    """
    selected, parse_errors, _ = _verified_selection(tags=tags, base=base)
    return selected, parse_errors


def _verified_selection(
    *,
    tags: str | None,
    base: Path | str | None,
) -> tuple[list[ScenarioRef], list[str], dict[str, tuple[int, ...]]]:
    """Select from the feature files and report what each selection was read from.

    :func:`select_scenarios` without the third value, which is the object
    identity of every feature whose contents were parsed.  It exists because
    the selection and the hand-off to the workers are separate moments:
    :func:`run_suite` re-checks these identities through
    :func:`_dropped_for_replacement` before it builds a single command, and it
    can only do that if the identities travel with the selection.  The public
    function drops them because a caller that merely wants to know what would
    run has nothing to hand off.

    Args:
        tags: Tag expression, or ``None`` for no filter.
        base: Directory the features directory hangs off.

    Returns:
        A ``(selected, parse_errors, identities)`` triple.  The first two are
        exactly what :func:`select_scenarios` returns; ``identities`` maps each
        parsed feature's repository-relative path to the identity of the
        descriptor its contents came from.

    Raises:
        TagExpressionError: If ``tags`` is malformed.
    """
    # Parsed once, before any file is read, so a malformed expression fails
    # immediately rather than after the whole suite has been parsed.
    expression = _parse_tag_expression(tags)

    feature_paths, parse_errors = _feature_files(base)
    identities: dict[str, tuple[int, ...]] = {}
    selected: list[ScenarioRef] = []
    for feature_path in feature_paths:
        verified, problems = _verified_or_problem(feature_path, base)
        parse_errors.extend(problems)
        if verified is None:
            # A refused entry costs its own scenarios and nothing else: the
            # remaining features are still selected, which is the tolerated
            # behaviour the AAP 0.4.1 exit table requires.
            continue
        identities[verified.path] = verified.identity
        units, problems = _units_of(verified)
        parse_errors.extend(problems)
        for unit in units:
            if expression is None or expression.evaluate(list(unit.tags)):
                selected.append(unit)

    # Both keys matter: the feature order is the canonical one, and ascending
    # line order within a feature is what the round-robin walk expects.
    selected.sort(key=lambda unit: (unit.feature_path, unit.line))
    return selected, parse_errors, identities


def select_rerun_scenarios(
    *,
    base: Path | str | None = None,
) -> tuple[list[ScenarioRef], list[str]]:
    """Select the scenarios a rerun should execute, from the rerun manifest.

    The port of ``FailedTestRunner`` (``FailedTestRunner.java:11``), which
    named the manifest as its feature source and declared **no tag filter** --
    so a rerun must reach failures in features carrying no ``@Smoke`` tag; see
    :data:`NEUTRAL_TAG_EXPRESSION` for how that is enforced on the worker's
    command line.  The manifest's grammar belongs to
    :func:`~app.reporting.rerun_report.parse_rerun_file` and nothing is parsed
    here, so writer and consumer cannot drift apart.

    Args:
        base: Directory the manifest and the features directory hang off, or
            ``None`` for the working directory.

    Returns:
        A ``(selected, problems)`` pair.  Every location the parser hands over
        is selected, enriched with the scenario's name and effective tags
        where the feature file still declares one at that line.  A location
        whose line no longer names a scenario is **kept anyway** and noted in
        ``problems``: dropping it would silently discard a failure, which is
        the one thing a rerun must not do.  A location whose feature file does
        not resolve inside the features directory never arrives here at all --
        :func:`~app.reporting.rerun_report.parse_rerun_file` applies that
        confinement tier itself and drops such a line with a warning on
        stderr, which is the tolerated-manifest row of the AAP 0.4.1 exit
        table -- so the unverifiable-feature branch below is reached only when
        the entry stops being the file that tier approved, or when its
        contents cannot be read at all.  That case is reported and its
        locations are **kept unenriched**, for the same reason a stale line is:
        a rerun exists to re-run recorded failures and must not discard one
        because the file could not be read this instant.  A missing,
        unreadable or malformed manifest yields no scenarios and a problem
        message, never an exception, and leaves the run at status ``0``.
    """
    selected, problems, _ = _verified_rerun_selection(base=base)
    return selected, problems


def _verified_rerun_selection(
    *,
    base: Path | str | None,
) -> tuple[list[ScenarioRef], list[str], dict[str, tuple[int, ...]]]:
    """Select from the manifest and report what each selection was read from.

    :func:`select_rerun_scenarios` without the third value, for the same
    reason :func:`_verified_selection` exists: :func:`run_suite` re-checks
    each feature's identity before it hands anything to a worker, and a rerun
    needs that check exactly as much as an ordinary run does.

    Args:
        base: Directory the manifest and the features directory hang off.

    Returns:
        A ``(selected, problems, identities)`` triple.  The first two are
        exactly what :func:`select_rerun_scenarios` returns; ``identities``
        maps each feature whose contents were read to the identity of the
        descriptor they came from.  A feature whose contents could **not** be
        read contributes no location at all, so every path in ``selected`` has
        an identity in the mapping and nothing reaches a worker that was not
        verified -- see the drop in the loop for why that one case is the
        exception to "a rerun never drops a recorded failure".
    """
    manifest = rerun_txt_path(base)
    try:
        entries = parse_rerun_file(base=base)
    except RerunManifestError as error:
        return [], [str(error)], {}

    problems: list[str] = []
    identities: dict[str, tuple[int, ...]] = {}
    selected: list[ScenarioRef] = []
    for position, entry in enumerate(entries, start=1):
        # The entry is opened and verified rather than rebuilt and re-stat'ed.
        # The previous form composed a fresh path from the features directory
        # and the entry's file name, checked *that* with is_file() and then
        # handed the name to a parser that opened it again -- three
        # resolutions of one entry, any two of which a concurrent swap could
        # make disagree (CWE-22, CWE-367).  One verified open now answers all
        # three questions at once.
        #
        # The prefix is normalised first, not because the verification needs
        # it -- it validates the path itself -- but so that a diagnostic
        # carries the port's own spelling even for an entry whose contents
        # could not be read.  A manifest this port wrote already carries that
        # prefix; a manifest written against the Java layout carries the
        # longer one, which the paths module maps onto it.

        feature_path = normalize_feature_uri(entry.path)
        verified, read_problems = _verified_or_problem(
            feature_path, base, named_by=manifest, position=position
        )
        problems.extend(read_problems)
        if verified is None:
            # **Every location of an unverifiable feature is dropped, and this
            # is the one place a rerun drops a recorded failure.**  The rule
            # that a location is kept rather than dropped exists for a
            # *different* condition -- the feature verified and simply no
            # longer declares a scenario at that line -- and it must not be
            # stretched to this one.  Here the port has already decided the
            # entry does not name a feature file of this suite: it is a link,
            # a hard link, a non-regular entry, absent, over the owner's byte
            # bound or not UTF-8.  Retaining its locations would hand the
            # engine the very pathname that was refused, and the engine opens
            # a pathname by name -- so a refusal on this side would be undone
            # on that one and the outside file would execute under the
            # refused entry's spelling (CWE-22).  The drop is reported above
            # and leaves the run at status 0 per the AAP 0.4.1 exit table.
            continue

        # The verified path rather than the normalised one: they agree by
        # construction, and using the verified value keeps the file that was
        # read and the path handed to behave the same single fact.
        feature_path = verified.path
        identities[feature_path] = verified.identity
        units, unit_problems = _units_of(verified)
        problems.extend(unit_problems)
        by_line = {unit.line: unit for unit in units}

        for line in entry.lines:
            unit = by_line.get(line)
            if unit is not None:
                # The unit's own path is used rather than the manifest's: it
                # is the path of the file that was verified and read, which is
                # what behave has to be given.
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
    return selected, problems, identities


def shard_scenarios(
    scenarios: Sequence[ScenarioRef],
    worker_count: int,
    *,
    base: Path | str | None = None,
    run_dir: Path | str | None = None,
) -> list[ShardPlan]:
    """Distribute the selected units over the workers, round-robin by feature.

    Walk the canonical feature order, within each feature its units in
    ascending line order, and assign each unit round-robin across the workers.
    The unit is the scenario -- ``parallel=methods`` (``pom.xml:22``) is
    method-level -- and grouping by feature keeps a worker's units of one
    feature together, so that feature's Background runs once per scenario
    inside the worker that runs it.  The order is imposed here rather than
    assumed of the input; no I/O happens beyond computing the output paths.

    Args:
        scenarios: The selected units.  Duplicate locations are collapsed --
            one location twice would run and report the scenario twice.
        worker_count: Requested count, clamped by
            :func:`_effective_worker_count` to between one and the unit count.
        base: Directory the output paths hang off; ``None`` means the cwd.
        run_dir: This run's own intermediate directory, from
            :func:`prepare_workers_dir`, which every shard's output file sits
            inside.  ``None``, for a caller sharding without a prepared run,
            uses the shared directory :mod:`app.utils.paths` names.

    Returns:
        One :class:`ShardPlan` per worker, in shard order, none empty; an empty
        input yields an empty list.  **Every unit lands in exactly one shard.**
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


def build_worker_command(
    plan: ShardPlan,
    *,
    tags: str | None = None,
    browser: str | None = None,
    dry_run: bool = False,
) -> list[str]:
    """Build the argument list that runs one shard.

    Assembled here rather than configured in ``behave.ini``, which cannot hand
    each worker a distinct output path: a static formatter key would aim every
    worker at one file and leave the merge seeing a single shard.  The engine
    writes only an intermediate result document, one per worker, through the
    custom formatter in :mod:`app.reporting.events`.  Exactly one ``--format``
    and one ``-o`` appear, because behave pairs them **positionally**.

    Every invocation also carries :data:`_STAGE_FLAG`, an empty stage, which
    pins the step directory and the environment file to the tracked
    ``features/steps`` and ``features/environment.py``.  A worker's glue roots
    are part of the command rather than something the environment is trusted
    to leave alone, since the engine takes the stage from ``BEHAVE_STAGE``
    when nothing else sets one and then executes the environment file it
    names.

    Args:
        plan: The shard to run.  Its locations go last, and its output path is
            the sole ``-o`` value.
        tags: The user's tag expression, or ``None``.  An explicit expression
            is always emitted -- :data:`NEUTRAL_TAG_EXPRESSION` when there is
            no filter -- so ``behave.ini``'s ``default_tags`` cannot apply.
        browser: Browser override, forwarded as behave userdata only when one
            was given and deliberately **not validated**: an unrecognised
            browser must fail at first driver use, as in the Java driver.
        dry_run: Whether to add ``--dry-run``.

    Returns:
        The argument list, to be run with ``cwd`` set to the run base, where
        ``behave.ini``, ``features/environment.py``, ``app.reporting.events``
        and ``configuration.properties`` are found as the Java reader found it.
    """
    command = [
        sys.executable,
        "-m",
        _BEHAVE_MODULE,
        _NO_SKIPPED_FLAG,
        # Binds the step directory and the environment file to the tracked
        # ones, whatever the ambient environment says; see _STAGE_FLAG.
        _STAGE_FLAG,
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


# Single owner: this module is the only place in the port that creates or
# removes a per-worker intermediate directory, and every run gets its own
# directory inside the shared one -- a shared one has two failure modes, stale
# reuse of a recycled pid-plus-index file name and one run deleting another
# run's files.  The shared parent is removed only when it is empty.

_RUN_DIR_NAME_SEPARATOR: Final[str] = "-"

#: The lease file a run holds open, inside its own run directory, for as long
#: as the run lives.  It is what makes "is that run still going?" a question
#: about an **operating-system lock held by a living process** rather than
#: about the shape of a directory name: a name can be forged, a recycled
#: process id can be alive while the run that owned it is long gone, and both
#: readings keep another run's intermediate documents -- tracebacks, screenshot
#: attachments, scenario data -- in the workspace indefinitely.  A lock cannot
#: outlive the process that holds it, which is precisely the property needed.
#:
#: The name is this module's, not :mod:`app.utils.paths`'s, for the same reason
#: the run directory's own name is: it names a working file *inside* the
#: directory this module owns, is never an artifact, and is not resolvable
#: through the HTTP artifact route.  The leading dot keeps it clear of
#: :func:`~app.utils.paths.iter_worker_result_paths`, which matches per-worker
#: result files by prefix and suffix and therefore never sees it.
_RUN_LEASE_NAME: Final[str] = ".lease"

#: The lock file that serialises whole runs sharing one checkout, inside the
#: shared intermediate directory.  ``--clean`` empties the build output, the
#: run writes intermediates, and the fan-out publishes four shared artifacts;
#: two runs interleaving those three phases in one checkout can delete each
#: other's intermediates and publish a mixture of both runs' results.  Holding
#: this lock from before the clean until after the publication is what makes
#: that impossible, and it is the branch of the review's own resolution that
#: AAP section 0.4.1 permits -- its writer-failure row requires the artifacts
#: written before a failure to *remain*, so the four artifacts cannot be
#: promoted as an all-or-nothing set.
#:
#: It lives beside the run directories rather than in the build output root so
#: that it is inside the one entry of the build output the clean step does not
#: empty itself, and it is removed by whoever releases it, so the shared
#: directory is still gone by the time the command returns (AAP section 0.4.1).
RUN_LOCK_NAME: Final[str] = ".run.lock"

#: How long :func:`acquire_run_lock` waits for a run already in progress
#: before refusing.  It is a grace period rather than a queue: a run that is
#: finishing its own cleanup releases within a moment, while a run that is
#: genuinely executing a browser suite would hold the lock for minutes, and
#: waiting that out would turn a CI stage into a hang.  Refusing instead is
#: reported as an artifact-infrastructure failure, which is an exit class the
#: port already has (AAP section 0.1.3 deviation 15 fixes the three non-zero
#: classes, so no fourth may be invented).
_RUN_LOCK_WAIT_SECONDS: Final[float] = 30.0

#: How often that wait re-tries while it lasts.
_RUN_LOCK_POLL_SECONDS: Final[float] = 0.1

#: Mode for both lock files: owner read/write and nothing else.  Neither file
#: carries data, but a world-writable lock is a lock anybody can steal.
_LOCK_FILE_MODE: Final[int] = 0o600

#: Flags for opening a lock file.  ``O_CREAT`` rather than ``O_EXCL``: the file
#: is a rendezvous point and every participant opens the *same* one, so its
#: prior existence is normal.  ``O_NOFOLLOW`` where the platform has it, so a
#: symlink left in its place fails the open instead of redirecting the lock
#: onto another file.
_LOCK_OPEN_FLAGS: Final[int] = (
    os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
)

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

#: The lease descriptor this process holds for each run directory it created,
#: kept open for as long as the run lives because the lock exists only while
#: the descriptor does.  Guarded by :data:`_active_run_dirs_lock`, so the
#: registry and the leases it describes cannot disagree.
_run_dir_leases: dict[Path, int] = {}


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


def _lock_exclusive(descriptor: int) -> bool:
    """Take an exclusive advisory lock on an open file, without waiting.

    The one place either platform's locking call is made, so every lock in
    this module -- the per-run lease and the whole-run lock -- has the same
    semantics and the same failure handling.

    Args:
        descriptor: An open, writable file descriptor.

    Returns:
        ``True`` when this descriptor now holds the lock, and ``False`` when
        another descriptor holds it.  Both platforms' calls are *per open file
        description* rather than per process, so a second descriptor opened by
        this same process is reported as contended too -- which is what makes
        a liveness probe answerable from inside the process that holds the
        lock, rather than silently succeeding the way POSIX record locks would.

    Raises:
        OSError: Any failure that is not contention -- a descriptor that
            cannot be locked at all, a filesystem without lock support.  The
            caller decides what an unanswerable probe means; it never reads it
            as "free".
    """
    if _fcntl is not None:
        try:
            _fcntl.flock(descriptor, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        except (BlockingIOError, InterruptedError):
            return False
        except PermissionError:
            # Held by a process this account may not contend with, which is
            # still "held" and is never "free".
            return False
        return True

    if _msvcrt is not None:  # pragma: no cover - Windows only
        # A byte-range lock over the file's first byte, taken from a known
        # offset so two participants always contend over the same range.
        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            _msvcrt.locking(descriptor, _msvcrt.LK_NBLCK, 1)
        except OSError as error:
            # ``EACCES`` is Windows' "another process has locked a portion of
            # the file"; anything else is a genuine failure.
            if error.errno in (errno.EACCES, errno.EDEADLK):
                return False
            raise
        return True

    # No locking primitive at all.  Not reachable on CPython, which ships one
    # or the other on every supported platform, and reported rather than
    # guessed at: the callers fall back to a weaker test and say so.
    raise OSError("this platform provides no file-locking primitive")


def _unlock_and_close(descriptor: int) -> None:
    """Release a lock this process holds and give the descriptor back.

    Args:
        descriptor: The descriptor :func:`_lock_exclusive` locked.  Closing it
            would release the lock on both platforms anyway; unlocking first
            is explicit rather than incidental, and every failure is ignored
            because this runs on teardown paths where an exception would mask
            whatever was already being reported.
    """
    try:
        if _fcntl is not None:
            _fcntl.flock(descriptor, _fcntl.LOCK_UN)
        elif _msvcrt is not None:  # pragma: no cover - Windows only
            os.lseek(descriptor, 0, os.SEEK_SET)
            _msvcrt.locking(descriptor, _msvcrt.LK_UNLCK, 1)
    except OSError as error:
        logger.debug("Could not unlock descriptor %d: %s", descriptor, error)
    finally:
        try:
            os.close(descriptor)
        except OSError as error:  # pragma: no cover - a closed descriptor
            logger.debug("Could not close descriptor %d: %s", descriptor, error)


def _same_file(descriptor: int, path: Path) -> bool:
    """Return whether an open descriptor is still the file at a path.

    The check that makes an unlinked lock file safe.  A departing holder
    removes its lock file while it still holds the lock, so a waiter that
    acquires the lock afterwards is holding a file nobody will ever consult
    again; comparing the descriptor's identity against the name's is how it
    finds out and starts over.

    Args:
        descriptor: The open descriptor.
        path: The name it was opened by.

    Returns:
        ``True`` only when the name still resolves, without following a link,
        to the very object the descriptor holds.
    """
    try:
        opened = os.fstat(descriptor)
        named = os.lstat(path)
    except OSError:
        return False
    return (opened.st_dev, opened.st_ino) == (named.st_dev, named.st_ino)


def _reparse_problem(path: Path, info: os.stat_result, role: str) -> str | None:
    """Return why a path must not be traversed for deletion, or ``None``.

    A single test for **every** kind of indirection a directory entry can be,
    because two of them look like an ordinary directory:

    * A **symbolic link** is reported by :data:`stat.S_ISLNK` on both
      platforms, and is never followed while deleting.
    * A **Windows junction** (a mount-point reparse point) is not.
      ``os.lstat`` reports it with the directory bit set and the link bit
      clear, and its device and inode are its own, so every identity check
      passes while ``iterdir`` and :func:`shutil.rmtree` walk straight through
      it into whatever it points at -- which is how a recursive delete escapes
      a checkout entirely (CWE-22, and CWE-367 for the swap that installs it
      mid-operation).  It is identified here by the two Windows-only fields
      :class:`os.stat_result` carries for it, read through :func:`getattr` so
      this function is one code path on every platform rather than a
      platform-guarded branch that only Windows ever executes.
    * Any **other reparse point** -- a mounted volume, a deduplication or
      cloud-storage placeholder -- is refused for the same reason: whatever it
      redirects to, a deletion resolved through a name cannot be bound to the
      object that was checked, and this module fails closed rather than
      deleting something it cannot identify.

    Args:
        path: The path being considered, named in the returned reason.
        info: Its :func:`os.lstat` result -- **never** :func:`os.stat`, which
            resolves the very indirection being looked for.
        role: What the path is to the caller, e.g. ``"run directory"``, so the
            reason reads as a sentence about this deletion rather than as a
            generic complaint.

    Returns:
        ``None`` when the path is a plain object that may be operated on by
        name, and otherwise a one-line reason naming what it is and that
        nothing was removed.
    """
    if stat.S_ISLNK(info.st_mode):
        return (
            f"{path} is a symbolic link rather than the {role}, so it was "
            "neither followed nor removed"
        )
    attributes = getattr(info, "st_file_attributes", 0)
    tag = getattr(info, "st_reparse_tag", 0)
    if attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT or tag:
        return (
            f"{path} is a reparse point (a junction or a mounted volume) "
            f"rather than the {role}; deleting through it would recurse into "
            "whatever it redirects to, which may lie anywhere outside this "
            "checkout, so it was neither followed nor removed and nothing was "
            "removed through it"
        )
    return None


def _real_directory_problem(path: Path, role: str) -> str | None:
    """Return why a path is not a plain, directly addressable directory.

    Args:
        path: The directory to test.
        role: What it is to the caller, for the reason's wording.

    Returns:
        ``None`` when the path is a real directory that is not a link or a
        reparse point, and otherwise a one-line reason.  A path that does not
        exist is **not** a problem: the callers are removing things, and an
        absent component means there is nothing there to remove.
    """
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as error:
        return f"could not inspect the {role} {path}: {error}"

    problem = _reparse_problem(path, info, role)
    if problem is not None:
        return problem
    if not stat.S_ISDIR(info.st_mode):
        return (
            f"{path} is not a directory, so it is not the {role} and nothing "
            "in it was removed"
        )
    return None


def _verified_real_chain(base: Path | str | None) -> str | None:
    """Return why the path to the shared intermediate directory is untrustworthy.

    The path-based platform's substitute for
    :func:`_confined_workers_fd`'s chain of ``O_NOFOLLOW`` opens.  It cannot
    hold each component open, so it verifies each of them instead: the build
    output root and the shared intermediate directory must both be plain
    directories, neither a link nor a reparse point, before any removal is
    resolved through their names.  Without this, a junction at either level
    redirects a recursive deletion out of the checkout while every check made
    on the *entry* still passes, because the entry really is a directory --
    just not one inside this checkout.

    Args:
        base: Directory the build output hangs off, or ``None`` for the
            working directory.  This is the one component taken on trust: it
            is the checkout the caller chose to run in.

    Returns:
        ``None`` when every component is a plain directory or absent, and
        otherwise the first reason found, already suitable for a diagnostic.
    """
    for path, role in (
        (target_root(base), "build output directory"),
        (workers_dir(base), "intermediate directory"),
    ):
        problem = _real_directory_problem(path, role)
        if problem is not None:
            return problem
    return None


def _lease_path(directory: Path) -> Path:
    """Return the lease file inside a run directory.

    Args:
        directory: The run directory.

    Returns:
        Its lease file's path.  Built here rather than in
        :mod:`app.utils.paths` because it is a working file inside the
        directory this module owns, never an artifact -- the same rule the run
        directory's own name follows.
    """
    return directory / _RUN_LEASE_NAME


def _take_lease(directory: Path) -> None:
    """Open and lock this run's lease, registering the descriptor.

    Args:
        directory: The run directory just created, which this process is
            about to fill with per-worker result documents.

    Raises:
        OSError: If the lease cannot be created or cannot be locked on a
            platform that has locking.  Deliberately fatal to the run rather
            than downgraded to a warning: without a lease every other
            invocation in this checkout would read this directory as
            abandoned and be entitled to delete the results while they are
            being written, which is the very failure the lease exists to
            prevent.  :func:`run_suite` turns it into
            :attr:`RunOutcome.infrastructure_error`, so the run reports an
            artifact-infrastructure failure instead of racing.
    """
    path = _lease_path(directory)
    descriptor = os.open(path, _LOCK_OPEN_FLAGS, _LOCK_FILE_MODE)
    try:
        if not _lock_exclusive(descriptor):
            # Only reachable if another process is holding the lease of a
            # directory *this* call just created under a name no other run
            # uses, so it means the name is not unique after all.
            raise OSError(f"{path} is already leased by another process")
    except OSError:
        _unlock_and_close(descriptor)
        raise
    with _active_run_dirs_lock:
        _run_dir_leases[directory] = descriptor


def _drop_lease(directory: Path) -> None:
    """Release this process's lease on a run directory, if it holds one.

    Called before the directory is removed, and that order matters on
    Windows, where a file with an open handle cannot be deleted at all.

    Args:
        directory: The run directory.  A directory this process never leased
            is a no-op, which is what makes the cleanup paths -- each of which
            may run twice -- idempotent.
    """
    with _active_run_dirs_lock:
        descriptor = _run_dir_leases.pop(directory, None)
    if descriptor is not None:
        _unlock_and_close(descriptor)


def _file_lock_is_held(path: Path) -> bool | None:
    """Return whether some living process holds the lock on one file.

    The one probe behind both exclusion mechanisms -- a run directory's lease
    and the whole-run lock -- and the whole of what makes either trustworthy:
    the answer comes from an operating-system lock, so it cannot outlive the
    process that took it.  A holder that has died, been killed or gone with
    the machine releases automatically, and a file nobody ever locked answers
    honestly that it is free.

    Args:
        path: The lock or lease file to probe.

    Returns:
        ``True`` when the file exists and is locked; ``False`` when it does
        not exist, or exists and is free; and ``None`` when the question
        cannot be answered here -- the platform has no locking primitive, or
        the probe itself failed -- which each caller resolves in its own
        fail-safe direction rather than reading as either answer.
    """
    try:
        descriptor = os.open(path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        # Nothing there to be held.  For a lease this is the case a
        # process-id-shaped name alone got wrong, keeping another run's
        # intermediate documents in the workspace for as long as some
        # unrelated process happened to hold that id.
        return False
    except OSError as error:
        logger.debug("Could not open %s to probe its lock: %s", path, error)
        return None

    try:
        acquired = _lock_exclusive(descriptor)
    except OSError as error:
        logger.debug("Could not probe the lock on %s: %s", path, error)
        return None
    finally:
        # The probe holds nothing: the descriptor is closed either way, which
        # also releases the lock in the "acquired" case, so probing never
        # keeps anything alive.  Both platforms' locks are per open file
        # description, so a probe made by the process that holds the lock
        # still reports it held.
        _unlock_and_close(descriptor)
    return not acquired


def _lease_is_held(directory: Path) -> bool | None:
    """Return whether some living process holds a run directory's lease.

    Args:
        directory: A child of the shared intermediate directory.

    Returns:
        What :func:`_file_lock_is_held` answers about that directory's lease
        file: ``True`` while the run is in progress, ``False`` when the lease
        is absent or free and the directory is therefore reclaimable, and
        ``None`` when the question could not be answered.
    """
    return _file_lock_is_held(_lease_path(directory))


#: Prefix of the private name an entry is moved to before it is deleted.  Dot
#: prefixed for the same reason the intermediate directory is: nothing
#: dot-prefixed is reachable through the HTTP artifact route.  The rest of the
#: name is random, which is the whole point -- see
#: :func:`delete_verified_entry`.
_ASIDE_PREFIX: Final[str] = ".removing-"


def delete_verified_entry(
    parent: Path,
    name: str,
    *,
    role: str,
    unlink_links: bool = False,
) -> str | None:
    """Delete one entry of an already-verified directory, object-bound.

    The port's one destructive primitive for platforms that cannot delete
    relative to an open directory descriptor -- Windows, which AAP section 0.8
    lists as supported.  Both callers are here and in ``app/cli.py``: the
    ``--clean`` step emptying the build output, and the reclaim of the shared
    intermediate directory.  One implementation, because a second one is a
    second place for this to be got wrong.

    **The problem it solves is not the junction that is already there.**  That
    one is refused by :func:`_reparse_problem` on the ``lstat`` below.  This is
    the *substitution*: on a platform where every operation resolves a name,
    an entry verified as a plain directory can be replaced by a junction in
    the instant between the check and the recursive removal, and
    :func:`shutil.rmtree` then resolves through the replacement and deletes the
    children of whatever it points at (CWE-367 over CWE-22).  No amount of
    re-checking a *name* closes that, because the check and the use are two
    resolutions of the same name.

    So the name is taken out of the race instead:

    1. The entry is inspected with :func:`os.lstat`.  A reparse point is
       refused outright, and so is a symbolic link unless ``unlink_links``
       says otherwise.
    2. Anything that is not a directory -- a link the caller allows among
       them -- is removed with a single :func:`os.unlink`, which removes the
       named entry itself and cannot recurse, so there is nothing for a
       substitution to redirect.
    3. A directory is **moved to a private, random name inside the same
       verified parent** with :func:`os.replace`, which is atomic.  After that
       move no other process can address it: it would have to guess the name.
    4. Its identity is then re-established through that private name -- still
       a directory, still not a reparse point, and the **same device and inode
       as the object inspected in step 1**, which a rename preserves on both
       platforms.  Only then is it removed recursively.

    What a substitution can therefore achieve is to be moved aside and
    refused, which is reported and leaves it in place under its private name.
    What it cannot achieve is a recursive deletion outside the parent.

    Args:
        parent: The directory holding the entry.  The caller must already have
            established that this is a plain directory and not an
            indirection -- :func:`_verified_real_chain` for the intermediate
            directory, and the build output's own identity re-check in
            ``app/cli.py``.
        name: The entry's single path component, never a path.
        role: What the entry is to the caller, for the wording of a refusal.
        unlink_links: What a **symbolic link** at this entry means, which is
            the one policy the two callers do not share.  ``False``, the
            default, refuses it: a link standing where a run directory belongs
            is not a run directory, and the reclaim will not guess what the
            operator meant by it.  ``True`` removes the link itself with a
            single :func:`os.unlink`, which is the ``--clean`` step's
            behaviour, because there a link is simply an entry of a directory
            being emptied and unlinking it cannot touch what it points at.
            A **reparse point** is refused either way: removing one needs a
            different call from removing a link, and a deletion this primitive
            cannot bind to an object it verified is not one it will make.

    Returns:
        ``None`` once the entry is gone, which includes it never having been
        there; otherwise a one-line reason, **not** logged here so that each
        caller keeps its own record and its own prefix.
    """
    shown = parent / name
    try:
        info = os.lstat(shown)
    except FileNotFoundError:
        return None
    except OSError as error:
        return f"could not inspect {shown}: {error}"

    if stat.S_ISLNK(info.st_mode):
        if not unlink_links:
            return _reparse_problem(shown, info, role)
        # The link itself, whatever it points at: one unlink, which never
        # reaches the target and cannot recurse.
        try:
            os.unlink(shown)
        except FileNotFoundError:
            return None
        except OSError as error:
            return f"could not remove {shown}: {error}"
        return None

    indirection = _reparse_problem(shown, info, role)
    if indirection is not None:
        return indirection

    if not stat.S_ISDIR(info.st_mode):
        try:
            os.unlink(shown)
        except FileNotFoundError:
            return None
        except OSError as error:
            return f"could not remove {shown}: {error}"
        return None

    aside = parent / f"{_ASIDE_PREFIX}{secrets.token_hex(_RUN_DIR_TOKEN_BYTES)}"
    try:
        os.replace(shown, aside)
    except FileNotFoundError:
        # Gone between the inspection and the move, which satisfies the
        # postcondition rather than defeating it.
        return None
    except OSError as error:
        return f"could not set {shown} aside before removing it: {error}"

    try:
        moved = os.lstat(aside)
    except OSError as error:
        return (
            f"{shown} was set aside as {aside} and could not be re-checked "
            f"({error}), so it was not removed"
        )

    substituted = _reparse_problem(aside, moved, role)
    if substituted is None and not stat.S_ISDIR(moved.st_mode):
        substituted = f"{aside} is no longer a directory"
    if substituted is None and (moved.st_dev, moved.st_ino) != (
        info.st_dev,
        info.st_ino,
    ):
        substituted = (
            f"{aside} is a different object from the one that was checked "
            "(its device and inode changed)"
        )
    if substituted is not None:
        return (
            f"{shown} was replaced while it was being removed, so it was set "
            f"aside as {aside} and nothing was deleted through it: "
            f"{substituted}"
        )

    try:
        shutil.rmtree(aside)
    except FileNotFoundError:
        return None
    except OSError as error:
        return f"could not remove {shown}, set aside as {aside}: {error}"

    try:
        os.lstat(aside)
    except FileNotFoundError:
        return None
    except OSError as error:
        return f"could not confirm that {shown} is gone: {error}"
    return f"{shown} still exists, as {aside}, after it was removed"


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
        ``True`` when the process exists or cannot be ruled out, ``False`` only
        when the kernel says it has exited -- see :func:`_process_is_alive` for
        why the unknown case is ``True``.

    Notes:
        Every entry point is declared through :func:`_declared` for the reason
        that function states: undeclared, ctypes assumes a C ``int`` return and
        **truncates** the 64-bit ``HANDLE`` that ``OpenProcess`` gives back, so
        the wait and the close would both act on a handle the kernel never
        issued.  A probe that reads a truncated handle answers about nothing,
        and on this path the answer decides whether a live run's intermediates
        are deleted underneath it.

        The wait's three outcomes are distinguished rather than compared
        against one value: ``WAIT_TIMEOUT`` is the process still running,
        ``WAIT_OBJECT_0`` is the process exited, and anything else --
        ``WAIT_FAILED`` above all -- is a probe that failed and therefore the
        fail-safe ``True``.  Treating a failed wait as "not running", which a
        single equality test does, is how a running sibling's results get
        removed.
    """
    library = _kernel32()

    if library is None:  # pragma: no cover - exercised through a double
        return True

    # Function-scoped like every other Win32 call site here: this module has to
    # import on POSIX, where the ctypes Windows surface does not exist.
    import ctypes

    handle_type, boolean, dword = _win32_types(ctypes)
    open_process = _declared(library, "OpenProcess", handle_type, (dword, boolean, dword))
    handle = open_process(_PROCESS_SYNCHRONIZE_ACCESS, False, pid)

    if not handle:
        # No handle: the process is gone, or it exists and is not this
        # account's to observe.  The two are told apart by the error code, and
        # only ``ERROR_INVALID_PARAMETER`` means the id does not exist -- every
        # other code, an access denial in particular, describes a process that
        # is alive.
        return _win32_last_error(ctypes) != _WIN32_ERROR_INVALID_PARAMETER

    try:
        wait = _declared(library, "WaitForSingleObject", dword, (handle_type, dword))
        outcome = wait(handle, 0)

        if outcome == _WIN32_WAIT_TIMEOUT:
            return True

        if outcome == _WIN32_WAIT_OBJECT_0:
            return False

        logger.debug(
            "Could not probe process %d: the wait reported 0x%08X; "
            "treating the process as running",
            pid,
            outcome,
        )

        return True
    finally:
        # The close is reported rather than discarded: a probe that leaked a
        # handle per call would accumulate them for the life of the
        # supervisor, and this runs once per candidate run directory per clean.
        reason = _close_win32_handle(library, handle)

        if reason is not None:
            logger.debug("Could not close the probe handle for process %d: %s", pid, reason)


def run_directory_is_active(directory: Path) -> bool:
    """Return whether a run directory belongs to a run still in progress.

    This is what lets one invocation's ``--clean`` empty the build output
    without destroying another invocation's live intermediates, which is the
    concurrency defect the per-run directory alone does not fix: the clean
    step removes the shared directory's contents, and without this test it
    would remove a running sibling's results and turn them into missing
    worker files.

    **Liveness is a lock, not a name.**  The name is read only to recognise a
    run directory at all; whether that run is still going is answered by
    :func:`_lease_is_held`, which asks the operating system whether a living
    process holds the directory's lease.  Inferring it from the process id in
    the name instead -- as this function did until the reading was reviewed --
    is wrong in the one direction that matters: a directory named after a
    process id that some *unrelated* process happens to own reads as active
    for as long as that process lives, so a hand-made or long-abandoned
    directory such as ``1-000000000000`` is preserved indefinitely while it
    holds another run's tracebacks, screenshots and scenario data.  A lease
    cannot outlive its holder, so nothing accumulates.

    Args:
        directory: A child of the shared intermediate directory.

    Returns:
        ``True`` when the directory belongs to a run still in progress -- this
        process's own current run included -- and ``False`` for anything else,
        which is therefore reclaimable: an abandoned run's leftovers, and
        anything in that directory that a run did not create.
    """
    owner = run_directory_owner(directory.name)
    if owner is None:
        return False
    with _active_run_dirs_lock:
        if directory in _active_run_dirs:
            return True

    leased = _lease_is_held(directory)
    if leased is not None:
        return leased

    # The lease could not be consulted at all.  Fall back to the process-id
    # probe, which is weaker but fails safe in the same direction, and say so
    # once per probe rather than silently: a reader of the log has to know
    # that the strong test did not run.
    logger.debug(
        "The lease of %s could not be consulted; falling back to a "
        "process-id probe for its liveness",
        directory,
    )
    return _process_is_alive(owner)


def prepare_workers_dir(*, base: Path | str | None = None) -> Path:
    """Create this run's own directory for its per-worker result files.

    The directory is a fresh, uniquely named child of the shared
    per-worker intermediate directory :mod:`app.utils.paths` names, created on
    every call -- see the section comment above for the two failure modes a
    shared directory has.  It is registered as this process's responsibility,
    so :func:`cleanup_workers_dir` can remove what this invocation created
    without having to guess, and it is **leased**: a lock file inside it is
    opened and locked here and stays locked until the run lets it go, which is
    what lets every other invocation in this checkout tell a run in progress
    from a run that is over (:func:`run_directory_is_active`).

    **It is created owner-only**, along with the two directories above it.
    That is not arranged here: :func:`~app.utils.paths.ensure_dir` creates
    every owned component :data:`~app.utils.paths.ARTIFACT_DIR_MODE` and
    clears the group and other bits of an existing one through its own
    descriptor, so this service adds no mode of its own and no ``umask`` call.
    The intermediates matter as much as the published artifacts do: a shard
    document carries the step arguments substituted from the Examples tables
    and the failure text of whatever it ran, so a world-readable intermediate
    directory discloses both, and its listing additionally names every shard a
    run has (CWE-732/CWE-359).

    Args:
        base: Directory it hangs off, or ``None`` for the working directory.

    Returns:
        The created directory, which the caller passes to
        :func:`shard_scenarios` so every shard's output path lands inside it.

    Raises:
        OSError: If it cannot be created, or if its lease cannot be taken.
            :func:`run_suite` converts that into an outcome carrying
            :attr:`RunOutcome.infrastructure_error` rather than letting it
            escape, since a run whose workers have nowhere to write executes
            nothing and must not report success -- and a run whose directory
            is not leased would have its live results read as abandoned by any
            concurrent invocation, which is worse than not starting.
    """
    directory = ensure_dir(workers_dir(base) / _run_dir_name())
    with _active_run_dirs_lock:
        _active_run_dirs.add(directory)
    try:
        _take_lease(directory)
    except OSError:
        # Registered a moment ago, so it is unregistered again rather than
        # left behind as a directory this process claims and does not hold.
        with _active_run_dirs_lock:
            _active_run_dirs.discard(directory)
        raise
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


def _remove_by_path(directory: Path, base: Path | str | None = None) -> str | None:
    """Remove one run directory by path, for a platform without ``at`` calls.

    The fallback for Windows, which AAP section 0.8 lists as supported and
    which has none of the descriptor-relative calls the POSIX path uses.  It
    keeps what can be kept without them, and by refusal rather than by best
    effort:

    * The **path to** the directory is verified first
      (:func:`_verified_real_chain`), because every operation below resolves a
      name rather than a held object.  A junction at the build output root or
      at the shared intermediate directory would redirect the recursive
      deletion into whatever it points at while the entry itself still looked
      like an ordinary directory, and the deletion would then take that
      directory's children with it (CWE-22, CWE-367).
    * The removal itself goes through :func:`delete_verified_entry`, which
      refuses a link or a reparse point, moves a directory to a private random
      name inside the verified parent before removing it recursively, and
      re-establishes the object's identity through that private name.  That is
      what closes the substitution window a pathname deletion otherwise
      leaves: the recursive removal operates on a name no other process can
      address, and on an object proved to be the one that was checked.
    * The removal's postcondition is confirmed afterwards, so a removal that
      reported success and left the directory behind is never read as success.

    Args:
        directory: The run directory to remove.
        base: Directory the shared intermediate directory hangs off, or
            ``None`` for the working directory.  Used to verify the chain of
            real directories leading to ``directory`` before anything is
            deleted through it.

    Returns:
        ``None`` once it is gone, and otherwise a reason.
    """
    chain = _verified_real_chain(base)
    if chain is not None:
        reason = f"{directory} was not removed: {chain}"
        logger.error("Intermediate cleanup refused: %s", reason)
        return reason

    reason = delete_verified_entry(
        directory.parent, directory.name, role="run directory"
    )
    if reason is not None:
        logger.error("Intermediate cleanup failed: %s", reason)
    return reason


def _remove_run_directory(directory: Path, base: Path | str | None) -> str | None:
    """Remove one run directory, refusing anything that is not one.

    Two confinement rules, and both are refusals rather than best efforts:
    the directory must be a **direct child** of the shared intermediate
    directory this ``base`` names, and the path to that shared directory must
    be a chain of real directories -- verified by :func:`_confined_workers_fd`
    where descriptor-relative calls exist and by :func:`_verified_real_chain`
    inside :func:`_remove_by_path` where they do not, since the lexical parent
    test below says nothing about what the *components* of that path are.
    Together they mean this function cannot be talked into a recursive
    deletion somewhere else by a caller's path, by a relinked or re-pointed
    parent component, or by a link or junction left in place of the directory
    itself.

    This process's own lease on the directory, if it holds one, is released
    first: the lease is what tells every other invocation the run is live, so
    it is given up at the moment the directory stops existing, and on Windows
    an open handle would prevent the removal outright.

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

    _drop_lease(directory)

    if not _SUPPORTS_CONFINED_REMOVAL:
        return _remove_by_path(directory, base)

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

    **The run lock is retained while it is held, and only then.**
    :data:`RUN_LOCK_NAME` is not a run's working file but the rendezvous point
    every run in this checkout contends for, so deleting one a run is holding
    would hand two runs two different lock objects and dissolve the mutual
    exclusion it exists to provide.  Whether it is held is asked of the
    operating system (:func:`_run_lock_is_in_use`) rather than inferred from
    the name: a lock file left behind by a run that did not release it is
    residue, and retaining it on the strength of its name would leave the
    shared intermediate directory in the workspace after every command and
    break AAP section 0.4.1's "the intermediate directory is removed before
    the command returns".  A retained lock is reported as retained rather than
    silently skipped, so the caller's survivor check knows why the shared
    directory legitimately outlived the clean; whoever holds it removes it when
    it releases (:meth:`RunLock.release`).

    Args:
        base: Directory the shared intermediate directory hangs off, or
            ``None`` for the working directory.

    Returns:
        A ``(reason, retained)`` pair.  ``reason`` is ``None`` when
        everything reclaimable is gone, and otherwise names what could not be
        removed; ``retained`` names every entry left in place -- each live run
        directory, and the run lock when one is present -- in sorted order, so
        a caller can report them as deliberately kept rather than treating
        them as a failed clean.
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
            if name == RUN_LOCK_NAME:
                if _run_lock_is_in_use(root / name):
                    retained.append(root / name)
                    continue
            elif run_directory_is_active(root / name):
                retained.append(root / name)
                continue
            # This process's own lease on a directory it is about to remove,
            # if any: released before the removal, never after it.
            _drop_lease(root / name)
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
            "Retained %d entr(y/ies) still in use in %s: %s",
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
    the same retention rule: a live run's directory stays, and so does the run
    lock while some process holds it.  Each removal is made by the same
    object-bound primitive the rest of this module uses
    (:func:`delete_verified_entry`), so an entry replaced between this loop's
    listing and its removal is moved aside and refused rather than followed.

    Both the directory this walks and every entry it removes are checked for
    **any** indirection and not merely for a symbolic link: ``iterdir`` and
    :func:`shutil.rmtree` both resolve names, and a junction here reports as a
    plain directory, so without the reparse test this loop would enumerate and
    recursively delete the children of whatever the junction points at
    (CWE-22, CWE-367).  The root's test is made here; each entry's is made by
    :func:`_remove_by_path`, together with the verification of the chain of
    real directories leading to it.

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

    indirection = _reparse_problem(root, status, "intermediate directory")
    if indirection is not None:
        logger.error("Intermediate cleanup refused: %s", indirection)
        return indirection, ()

    if not stat.S_ISDIR(status.st_mode):
        reason = (
            f"{root} is not a directory, so it is not the intermediate "
            "directory and nothing in it was removed"
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
        if entry.name == RUN_LOCK_NAME:
            if _run_lock_is_in_use(entry):
                retained.append(entry)
                continue
        elif run_directory_is_active(entry):
            retained.append(entry)
            continue
        _drop_lease(entry)
        problem = delete_verified_entry(
            root,
            entry.name,
            # What the entry is, so a refusal reads as a sentence about the
            # thing that was refused: everything in here is a run directory
            # except the lock file, which reaches this line only when the
            # probe above found nothing holding it.
            role=(
                "stale run lock" if entry.name == RUN_LOCK_NAME
                else "run directory"
            ),
        )
        if problem is not None:
            logger.error("Intermediate cleanup failed: %s", problem)
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

    **Idempotent**: an already absent directory is a success, which is what
    lets :func:`run_suite` remove its own directory in its ``finally`` and
    ``app/cli.py`` call this again on the way out.  A failed removal is
    reported and never raised -- an exception in a ``finally`` would mask what
    the run was reporting -- because a surviving directory holds this run's
    intermediate documents in a workspace where ``Jenkins:15`` narrows the
    publisher to one file so nothing intermediate can be read by it.

    Args:
        base: Directory the shared intermediate directory hangs off, or
            ``None`` for the working directory.
        directory: The one run directory to remove.  ``None`` means every
            directory this process created and has not yet removed, which is
            what ``app/cli.py`` asks for: it covers a run whose own cleanup
            never ran and is a no-op after one that cleaned up for itself.  A
            directory another process created is never removed by either form.

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
# The run lock
#
# Per-run directories keep two runs from reading or deleting each other's
# *intermediates*, and that is as far as they reach.  Three phases of a run
# operate on state the whole checkout shares:
#
# * ``--clean`` empties the build output, including artifacts another run has
#   just published or is about to publish.
# * the run writes into the shared intermediate directory, which the other
#   run's clean is entitled to reclaim from.
# * the fan-out publishes four artifacts at fixed paths, one writer at a time.
#
# Interleave two runs across those phases and the workspace ends up holding a
# mixture: this run's JSON beside that run's HTML, each naming scenarios,
# credentials-bearing step arguments and screenshots from a different
# execution, with nothing in either artifact to say so (CWE-362, CWE-367).
# Holding one lock from before the clean until after the publication makes the
# interleaving impossible, and it is the branch of the remedy AAP section
# 0.4.1 leaves open: its writer-failure row requires the artifacts written
# before a failure to *remain*, so the four cannot be promoted as an
# all-or-nothing set, and the two HTML writers already replace their own
# output atomically.
#
# Contention is **refused**, not queued.  A run holding the lock may be
# driving a browser suite for many minutes, and a CI stage that waits that out
# is a hang; a run that is merely finishing its cleanup releases within a
# moment, which is what the grace period absorbs.  The refusal is an
# artifact-infrastructure failure - an exit class the port already has, since
# AAP section 0.1.3 deviation 15 fixes the three non-zero classes and no
# fourth may be invented.
# --------------------------------------------------------------------------- #


def _discard_unheld_lock(path: Path, base: Path | str | None) -> None:
    """Remove a lock file nobody holds, and prune the directory it sat in.

    Called on the one path where this process created the file and then could
    not lock it, so the file is a rendezvous point with nothing behind it.
    Every failure is ignored: this runs while a refusal is already being
    reported, and a leftover file is removed by the next run's reclaim, which
    probes the lock rather than trusting the name.

    Args:
        path: The lock file to discard.
        base: Directory the shared intermediate directory hangs off.
    """
    if _file_lock_is_held(path):
        # Somebody does hold it after all - leave it entirely alone.
        return
    try:
        os.unlink(path)
    except OSError as error:
        logger.debug("Could not remove the unheld lock %s: %s", path, error)
    _prune_workers_root(base)


def _lock_residue_problem(path: Path, base: Path | str | None) -> str | None:
    """Return what a released claim left behind in the workspace, or ``None``.

    The postcondition of a release, established rather than assumed: AAP
    section 0.4.1 requires the shared intermediate directory to be gone by the
    time the command returns, so a lock file or a directory that outlives the
    run that owned them is a reportable failure and not a tidy-up detail.

    Args:
        path: The lock file that was just released.
        base: Directory the shared intermediate directory hangs off.

    Returns:
        ``None`` when nothing of this claim remains, or when what remains
        belongs to somebody else -- a live run's own directory keeps the
        shared directory alive quite legitimately, and that is not this
        release's failure.  Otherwise a one-line reason.
    """
    if path.exists():
        return (
            f"this run's lock file {path} still exists after the run released "
            "it, so the shared intermediate directory outlived the command"
        )

    root = workers_dir(base)
    try:
        residue = sorted(entry.name for entry in root.iterdir())
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as error:
        return f"could not confirm that {root} is gone: {error}"

    if not residue:
        return (
            f"{root} is empty but could not be removed, so the shared "
            "intermediate directory outlived the command"
        )
    # Something else is using it, which is why it could not be pruned.  Not
    # this release's problem, and deliberately not reported as one.
    logger.debug("%s is still in use by %s", root, ", ".join(residue))
    return None


def _run_lock_is_in_use(path: Path) -> bool:
    """Return whether a lock file found in the shared directory is a live claim.

    The reclaim steps must not delete the object a run in progress is holding
    -- that would hand two runs two different lock objects and dissolve the
    exclusion entirely -- and must not preserve a *name* for ever on the
    strength of the name alone, which would leave the shared intermediate
    directory behind after every command and break AAP section 0.4.1's
    requirement that it be gone by the time the command returns.  So the
    question is asked of the operating system.

    Args:
        path: The lock file, found by name inside the shared directory.

    Returns:
        ``True`` while some process holds it -- this one included, because
        both platforms' locks are per open file description -- and ``True``
        also when the probe could not answer, which is the fail-safe
        direction: a lock wrongly kept costs a leftover file that the next
        run removes, while a lock wrongly deleted costs mutual exclusion.
        ``False`` only when the file is demonstrably free, and therefore the
        residue of a run that did not release it.
    """
    held = _file_lock_is_held(path)
    if held is None:
        logger.debug("Could not establish whether %s is held; keeping it", path)
        return True
    return held


@dataclass
class RunLock:
    """An exclusive claim on one checkout's build output, held by this process.

    Obtained from :func:`acquire_run_lock`, never constructed directly, and
    released exactly once by :meth:`release` -- which ``app/cli.py`` calls
    from the ``finally`` around the whole run, so an interrupt, a defect and
    an ordinary exit all give the lock back.

    Attributes:
        path: The lock file this claim is held on, inside the shared
            intermediate directory.
        descriptor: The open, locked descriptor.  ``None`` once released,
            which is what makes :meth:`is_held` answer honestly afterwards.
    """

    path: Path
    descriptor: int | None
    base: Path | str | None = None

    def is_held(self) -> bool:
        """Return whether this process still holds the lock.

        Two conditions, because either alone can be true while the claim is
        worthless: the descriptor must still be open, **and** the lock file's
        name must still resolve to the object that descriptor holds.  The
        second is what detects a lock file replaced or removed underneath a
        holder -- after which another run's :func:`acquire_run_lock` would
        create a fresh file and lock that instead, leaving two runs each
        believing it has exclusive use of the build output.

        Returns:
            ``True`` only while this claim is exclusive.
        """
        if self.descriptor is None:
            return False
        return _same_file(self.descriptor, self.path)

    def release(self) -> str | None:
        """Give the lock back, remove the lock file, and report what survives.

        Three properties, and each of them is a correction of the obvious
        implementation:

        * **Only this claim's own object is removed.**  The unlink is made
          only while the name still resolves to the descriptor this lock
          holds (:func:`_same_file`).  Unlinking by name unconditionally would
          let a holder whose file had already been replaced delete a *second*
          run's live lock on the way out, after which a third run could
          acquire a different object while the second was still running --
          which is the exclusion this lock exists to provide, removed by its
          own teardown.
        * **The file goes while the lock is still held.**  A run waiting on it
          then acquires a file already detached from the name;
          :func:`acquire_run_lock` re-checks that identity after locking and
          starts over, which is what makes removing the file safe rather than
          a race.  Removing it is also what keeps the shared intermediate
          directory from outliving the command (AAP section 0.4.1): it is
          pruned afterwards, and prunes only when empty, so a concurrent run
          holding a directory in it is unaffected.
        * **A failure is reported rather than logged and forgotten.**  A lock
          file or a shared directory left behind is exactly the state AAP
          section 0.4.1 forbids, so the caller is given the reason and can
          decline to call the run a success.

        Idempotent: a second call does nothing and reports nothing, because
        ``app/cli.py`` releases explicitly before it publishes a status and
        again in its ``finally``.

        Returns:
            ``None`` when this claim is given back and nothing of it survives;
            otherwise a one-line reason naming what is still there.
        """
        descriptor = self.descriptor
        if descriptor is None:
            return None
        self.descriptor = None
        problem: str | None = None

        if _same_file(descriptor, self.path):
            try:
                os.unlink(self.path)
            except FileNotFoundError:
                pass
            except OSError as error:
                # Windows refuses to unlink a file this process still has
                # open, so the order is reversed there: unlock, close, then
                # remove.
                logger.debug("Deferring removal of %s: %s", self.path, error)
                _unlock_and_close(descriptor)
                descriptor = None
                try:
                    os.unlink(self.path)
                except FileNotFoundError:
                    pass
                except OSError as second_error:
                    problem = (
                        f"this run's lock file {self.path} could not be "
                        f"removed ({second_error}), so it remains in the "
                        "workspace"
                    )
        else:
            # Not ours any more: something replaced or removed the name while
            # this claim was held.  The descriptor is released and the name is
            # left strictly alone, whatever is behind it now.
            problem = (
                f"this run's lock file {self.path} was replaced or removed "
                "while the run held it, so it was left untouched and this "
                "checkout may have been claimed by another run at the same "
                "time"
            )

        if descriptor is not None:
            _unlock_and_close(descriptor)
        _prune_workers_root(self.base)

        surviving = _lock_residue_problem(self.path, self.base)
        if problem is None:
            problem = surviving
        if problem is not None:
            logger.error("Run lock release failed: %s", problem)
        return problem

    def __enter__(self) -> RunLock:
        """Return this lock, for use as a context manager.

        Returns:
            ``self``, already held.
        """
        return self

    def __exit__(self, *_exception: object) -> None:
        """Release the lock on the way out of the block."""
        self.release()


def acquire_run_lock(
    *,
    base: Path | str | None = None,
    wait_seconds: float | None = None,
) -> tuple[RunLock | None, str | None]:
    """Claim this checkout's build output for one run, or report why not.

    Args:
        base: Directory the build output hangs off, or ``None`` for the
            working directory.  Two runs contend only when they share it,
            which is exactly the scope of the state they would corrupt: runs
            in separate checkouts share nothing and never wait for each other.
        wait_seconds: How long to keep trying before refusing, or ``None``
            for :data:`_RUN_LOCK_WAIT_SECONDS`.  That default is a grace
            period for a run that is finishing, not a queue -- see the section
            comment -- and it is resolved here rather than in the signature so
            that the module constant remains the single value every caller
            gets, including the one caller that passes nothing.  ``0`` makes
            the attempt once.

    Returns:
        A ``(lock, reason)`` pair, exactly one of which is set.  ``lock`` is an
        exclusive :class:`RunLock` the caller must release; ``reason`` is a
        one-line explanation suitable for a diagnostic, which ``app/cli.py``
        turns into its artifact-failure status without starting the suite and
        without writing an artifact.

    Raises:
        OSError: Never.  Every failure -- the directory cannot be created, the
            file cannot be opened, the platform cannot lock -- is returned as
            a reason, because a run that cannot establish exclusivity must be
            refused in the same terms whatever prevented it.
    """
    try:
        root = ensure_dir(workers_dir(base))
    except OSError as error:
        return None, (
            f"{workers_dir(base)}: the shared intermediate directory this "
            f"run must lock cannot be created ({error}), so nothing was "
            "executed"
        )

    path = root / RUN_LOCK_NAME
    grace = _RUN_LOCK_WAIT_SECONDS if wait_seconds is None else wait_seconds
    grace = max(grace, 0.0)
    deadline = time.monotonic() + grace
    announced = False
    while True:
        descriptor: int | None = None
        try:
            descriptor = os.open(path, _LOCK_OPEN_FLAGS, _LOCK_FILE_MODE)
        except FileNotFoundError:
            # **Retryable, and the case the grace period exists for.**  The
            # flags include ``O_CREAT``, so the only thing missing can be the
            # directory holding the file: the run that had the lock has just
            # released it and pruned the shared directory on its way out
            # (:meth:`RunLock.release`).  Refusing here would fail precisely
            # the run that waited politely for its turn, so the directory is
            # recreated below and the attempt is made again.
            pass
        except OSError as error:
            return None, (
                f"{path}: this run's lock file cannot be opened ({error}), so "
                "the build output could not be claimed and nothing was "
                "executed"
            )

        if descriptor is not None:
            try:
                acquired = _lock_exclusive(descriptor)
            except OSError as error:
                # The file was created a moment ago and cannot be locked, so
                # nothing is holding it and nothing ever will: it is removed
                # rather than left as a lock file with no lock behind it, and
                # the shared directory is pruned, because a refused run must
                # leave the workspace as it found it (AAP section 0.4.1).
                _unlock_and_close(descriptor)
                _discard_unheld_lock(path, base)
                return None, (
                    f"{path}: this run's lock cannot be taken on this platform "
                    f"({error}), so the build output could not be claimed and "
                    "nothing was executed"
                )

            if acquired and _same_file(descriptor, path):
                logger.debug("Holding the run lock %s", path)
                return RunLock(path=path, descriptor=descriptor, base=base), None

            # Either another run holds it, or the holder unlinked it on its
            # way out and this descriptor now names nothing.  Both are
            # retried; the second is why the identity is re-checked at all.
            _unlock_and_close(descriptor)

        if time.monotonic() >= deadline:
            return None, (
                f"another run is already using {target_root(base)} (it holds "
                f"{path}), so this run was not started: two runs sharing one "
                "checkout would empty each other's build output and publish a "
                "mixture of both runs' reports"
            )
        if descriptor is not None and not announced:
            # Announced only for genuine contention - a lock file that exists
            # and is held by somebody - and only once, however many times the
            # wait goes round.
            announced = True
            logger.info(
                "Another run holds %s; waiting up to %.0f second(s) for it to "
                "finish before refusing",
                path,
                grace,
            )
        try:
            # Recreated rather than assumed: a departing run's prune may have
            # taken it, and the next attempt needs somewhere to create the
            # file.  A failure here is not retryable - it is the same
            # inability to establish exclusivity reported above.
            root = ensure_dir(workers_dir(base))
        except OSError as error:
            return None, (
                f"{workers_dir(base)}: the shared intermediate directory this "
                f"run must lock cannot be recreated ({error}), so nothing was "
                "executed"
            )
        path = root / RUN_LOCK_NAME
        time.sleep(_RUN_LOCK_POLL_SECONDS)


# --------------------------------------------------------------------------- #
# Running the shards
# --------------------------------------------------------------------------- #

#: How many of a shard's locations a diagnostic message names before it
#: abbreviates.  Enough to identify the shard's work without turning a single
#: dead-worker line into eighty.
_MAX_REPORTED_LOCATIONS: Final[int] = 5
_ELLIPSIS: Final[str] = "..."

_SHARD_THREAD_PREFIX: Final[str] = "testinium-qa-shard"

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


# AAP 0.4.1 requires both streams to be line-buffered -- progress to stdout,
# engine diagnostics to stderr -- which means a worker's line must be relayed
# when it is written and not when the worker exits; a browser suite runs for
# minutes, and buffering its output until the end leaves a Jenkins log silent
# throughout.  And a run that is interrupted must leave nothing behind: a
# worker sits in its own process group precisely so that stopping it stops the
# driver and the browser it started, which needs the parent to know which
# children are still alive.

#: How many of a worker's most recent lines each stream keeps after relaying
#: them.  **Bounded on purpose**: holding a whole run's output in memory grows
#: without limit, and a tail is all an after-the-fact reader of
#: :attr:`WorkerProcess.stderr` needs, since a shard's classification comes
#: from its exit status and its result file rather than from its text.
_RETAINED_OUTPUT_LINES: Final[int] = 20

#: Maximum characters one read of a worker stream may return.
#:
#: **Why the read is bounded at all.**  A child decides where its newlines
#: are, and an unbounded ``readline`` therefore lets it decide how much memory
#: this parent allocates: a worker that writes a gigabyte with no line
#: terminator -- a dumped DOM, a base64 screenshot, a corrupted binary written
#: to a pipe -- would have all of it allocated here as one string and then a
#: sanitized copy of it retained in the tail.  A limit passed to
#: :meth:`io.TextIOWrapper.readline` caps that allocation per read.
#:
#: **Why the live relay is unaffected.**  ``readline(size)`` returns as soon
#: as a newline arrives *or* ``size`` characters have accumulated, whichever
#: comes first, so a line shorter than this bound is still relayed the moment
#: it is complete -- which is the property :func:`_spawn_worker` documents as
#: load-bearing.  A blocking fixed-size ``read`` would have to wait for a full
#: buffer and would destroy it.
#:
#: **Why this value.**  Comfortably above every line the engine, a driver or a
#: step actually writes, and a few multiples of the rendering limit
#: :func:`~app.logging_config.render_worker_line` bounds a record at, so a
#: legitimate line behaves exactly as it did before the bound existed: read
#: whole in one call, then bounded by the renderer with its own truncation
#: notice.  Only a pathological line is fragmented, and
#: :func:`_drain_over_long_line` handles that case without accumulating
#: anything.
_RELAY_READ_LIMIT: Final[int] = 8192

#: Environment variable that stops the *child's* own stdout and stderr being
#: block-buffered.  Without it the relay below is live but its input is not:
#: a Python process whose stdout is a pipe buffers in blocks, so behave's
#: progress would reach this parent in 8 KiB instalments however promptly the
#: parent reads.  Setting it is what makes the line-buffered contract in AAP
#: 0.4.1 true end to end rather than only on the parent's side.
_UNBUFFERED_ENV_VAR: Final[str] = "PYTHONUNBUFFERED"
_UNBUFFERED_ENV_VALUE: Final[str] = "1"

#: The environment variable names a worker is given, and the only ones: every
#: name absent from this set is dropped, so the child's environment is built
#: **by construction** rather than filtered after the fact.  An allowlist is
#: the shape that matters here, because the thing being kept out is not a
#: known list of variables -- it is *whatever* the process that started this
#: run happened to be carrying.
#:
#: Each group below is here because a worker cannot do its job without it:
#:
#: * ``PATH`` -- the engine itself is invoked as a module of this very
#:   interpreter (:data:`_BEHAVE_MODULE`), but the browser and its driver are
#:   located on ``PATH`` by Selenium, so a worker with none finds no browser.
#: * The POSIX session group -- ``HOME`` (where drivers and browsers keep
#:   their per-user caches and profiles), ``USER`` and ``LOGNAME`` (identity a
#:   browser reads when it builds a profile directory), ``TMPDIR`` (scratch
#:   space for the browser's temporary profile), ``TZ`` (the clock a report's
#:   local timestamps are rendered against), and ``DISPLAY``, ``XAUTHORITY``
#:   and ``XDG_RUNTIME_DIR`` (without these a headed browser cannot reach the
#:   X server at all) plus ``XDG_CACHE_HOME`` (where that browser's cache
#:   goes when it is redirected away from ``HOME``).
#: * The locale group -- ``LANG``, ``LANGUAGE``, ``LC_ALL``, ``LC_CTYPE`` and
#:   ``LC_MESSAGES``.  **Load-bearing, not cosmetic**: the browser's own
#:   required-field message follows the process locale, and
#:   ``features/Login.feature:89`` asserts it in French
#:   ("Veuillez renseigner ce champ."), so a worker that lost these would
#:   change whether that outline passes.
#: * The Windows platform group -- ``SYSTEMROOT``, ``WINDIR``, ``COMSPEC``,
#:   ``PATHEXT``, ``SYSTEMDRIVE``, ``HOMEDRIVE``, ``HOMEPATH``, ``USERNAME``,
#:   ``USERPROFILE``, ``APPDATA``, ``LOCALAPPDATA``, ``PROGRAMDATA``,
#:   ``PROGRAMFILES``, ``PROGRAMFILES(X86)``, ``PROGRAMW6432``,
#:   ``NUMBER_OF_PROCESSORS``, ``SESSIONNAME``, ``TEMP`` and ``TMP``.  A
#:   Windows process without ``SYSTEMROOT`` cannot load system libraries and
#:   without ``PATHEXT`` cannot resolve an executable by bare name, and the
#:   rest are where a browser on that platform keeps its profile and its
#:   temporary files.  They cost nothing on POSIX, where no such variable is
#:   set and the group is simply never matched.
#: * ``GH_TOKEN`` -- the operator-supplied credential the driver manager uses
#:   for GitHub's release API when it looks up a geckodriver build.  It is a
#:   credential rather than a policy control: dropping it does not make the
#:   provisioning safer, it makes it rate-limited and eventually broken.
#:
#: **What being absent from this set closes, and why each one is a vector:**
#:
#: * Every ``BEHAVE_*`` variable.  ``BEHAVE_STAGE`` above all: behave takes
#:   the stage from that variable whenever nothing else sets one and then
#:   *prefixes both glue roots with it*, so an inherited ``/external/path``
#:   makes the engine load step modules from ``/external/path_steps`` and
#:   **execute** ``/external/path_environment.py`` (measured -- see
#:   :data:`_STAGE_FLAG`, which pins the same property on the command line).
#:   ``BEHAVE_COLOR``, ``BEHAVE_STORE_CAPTURED_ALWAYS``,
#:   ``BEHAVE_SHOW_CAPTURED_ALWAYS``,
#:   ``BEHAVE_HOOK_STORE_CAPTURED_ON_SUCCESS``,
#:   ``BEHAVE_HOOK_SHOW_CAPTURED_ON_SUCCESS``,
#:   ``BEHAVE_HOOK_STORE_CLEANUP_ON_SUCCESS``,
#:   ``BEHAVE_STRIP_STEPS_WITH_TRAILING_COLON``, ``BEHAVE_UNICODE_ERRORS``
#:   and ``BEHAVE_BROWSER`` are the other eight the engine reads; they change
#:   what a worker captures and reports, and the browser choice has exactly
#:   one override path in this port -- ``-D browser=<name>`` userdata,
#:   per AAP 0.4.1, which adds no environment layer.
#: * Every ``PYTHON*`` variable except the :data:`_UNBUFFERED_ENV_VAR` the
#:   launch sets itself.  ``PYTHONPATH`` and ``PYTHONHOME`` decide *which*
#:   modules the child imports, ``PYTHONSTARTUP`` names a file, and
#:   ``PYTHONOPTIMIZE`` and ``PYTHONWARNINGS`` change whether assertions --
#:   the substance of every step's verification -- run at all.
#: * Every ``WDM_*`` variable, and ``PYTEST_XDIST_WORKER``.  An inherited
#:   ``WDM_SSL_VERIFY=0`` turns off certificate verification in the child's
#:   driver provisioning and ``WDM_LOCAL=1`` relocates its cache writes, so
#:   the driver a worker ends up executing would be chosen under a trust
#:   policy the run never set.  Dropping the whole prefix is this launcher's
#:   half of that: provisioning inside a worker then runs under the driver
#:   module's own policy rather than an inherited one.
#: * ``SE_CHROMEDRIVER`` and ``SE_GECKODRIVER``.  Selenium's ``Service``
#:   reads these **in preference to** the executable path it was handed, so
#:   an inherited value silently substitutes an arbitrary binary for the
#:   verified driver.
#: * The TLS-trust and proxy variables ``SSL_CERT_FILE``, ``SSL_CERT_DIR``,
#:   ``REQUESTS_CA_BUNDLE``, ``CURL_CA_BUNDLE``, ``HTTP_PROXY``,
#:   ``HTTPS_PROXY``, ``ALL_PROXY`` and ``NO_PROXY``, in both upper and lower
#:   case.  Each one redirects or relaxes where the child's driver downloads
#:   come from and whom it trusts to sign them.  **The consequence is real
#:   and is the intended trade**: provisioning inside a worker uses the
#:   system trust store and no proxy, so an environment that requires a proxy
#:   or a private certificate authority must supply the driver another way --
#:   a pre-provisioned driver on ``PATH``, or a host trust store carrying the
#:   authority.  That fails loudly at provisioning time, which is the point:
#:   the alternative is a run that quietly trusts whatever the ambient
#:   environment told it to.
_WORKER_ENV_ALLOWLIST: Final[frozenset[str]] = frozenset(
    {
        # Program lookup, for the browser and its driver.
        "PATH",
        # POSIX session identity, scratch space, clock and display.
        "HOME",
        "USER",
        "LOGNAME",
        "TMPDIR",
        "TZ",
        "DISPLAY",
        "XAUTHORITY",
        "XDG_RUNTIME_DIR",
        "XDG_CACHE_HOME",
        # Locale, which the asserted French browser message follows.
        "LANG",
        "LANGUAGE",
        "LC_ALL",
        "LC_CTYPE",
        "LC_MESSAGES",
        # Windows platform, profile and temporary locations.  Named in upper
        # case because that is how Python exposes them: ``os.environ`` on
        # Windows upper-cases every key, so one spelling matches there and
        # matches nothing on POSIX, where none of these is set.
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "SYSTEMDRIVE",
        "HOMEDRIVE",
        "HOMEPATH",
        "USERNAME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PROGRAMW6432",
        "NUMBER_OF_PROCESSORS",
        "SESSIONNAME",
        "TEMP",
        "TMP",
        # The operator's GitHub credential for driver release lookups.
        "GH_TOKEN",
    }
)

#: Whether this platform can put a child in its own process group and signal
#: that group as a unit, and read back the group and session a process belongs
#: to.  True on POSIX, false on Windows, where the equivalent of the isolation
#: is a creation flag rather than a call and the equivalent of the reclamation
#: is a Job Object.
_HAS_PROCESS_GROUPS: Final[bool] = (
    hasattr(os, "killpg")
    and hasattr(os, "getpgid")
    and hasattr(os, "getsid")
    and hasattr(os, "setsid")
)

#: The signal a worker that ignored the polite request is killed with.
#: ``SIGKILL`` where it exists; on Windows there is no such signal and
#: :meth:`subprocess.Popen.kill` is the terminal action instead.
_KILL_SIGNAL: Final[int] = int(getattr(signal, "SIGKILL", signal.SIGTERM))

_SIGNAL_EXIT_BASE: Final[int] = 128

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

# --------------------------------------------------------------------------- #
# OS-level containment of a worker's process tree
#
# A worker is not one process.  It is the engine, the driver executable the
# engine starts, and the browser that executable starts -- and the browser
# holds the system under test's authenticated session.  Stopping the engine
# stops the engine, which is why cancellation has to reach the *tree*:
#
# * POSIX: the worker leads a **session** of its own
#   (:func:`_process_group_keywords`), so its session id, its process-group id
#   and its pid are all the same number.  A driver the worker starts runs in a
#   process group of its own *within that session*, so the worker's group alone
#   does not reach it -- the session does.  Both ids are captured at launch,
#   while the leader is certainly alive, because after the leader has exited
#   and been reaped neither can be looked up any more.
# * Windows: process groups do not kill a tree there, so the worker is assigned
#   to a Job Object limited with ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``, which
#   its children inherit.  Closing the one handle terminates the whole job.
#
# Reclamation on POSIX is therefore by session and not only by the worker's own
# group: :func:`_session_members` enumerates the session, and every process
# group in it other than the worker's own is signalled beside the worker's
# (:func:`_signal_nested_groups`).
#
# Signalling a group id is only safe once that id has been **attributed**.  A
# process-group id is a recycled resource, so :func:`_group_in_session`
# establishes that the group still belongs to the session captured at launch --
# by ``getsid`` of the group's leader, and by the session's live membership
# once that leader has been reaped -- and a group that belongs elsewhere is
# skipped rather than signalled.  Attribution is what makes reclamation
# possible on the two paths where the worker's own leader has already gone --
# a worker cancelled after its leader exited, and a worker that completed
# normally and left a browser behind.
#
# Asking is not confirming.  Every termination path below ends by checking
# whether anything is still there -- ``killpg(pgid, 0)`` for the worker's own
# group and the session enumeration for everything else on POSIX, and a bounded
# wait on the immediate process on Windows -- and reports a tree it could not
# account for at ``ERROR``, which reaches standard error.  A tree whose state
# this platform cannot determine is a ``WARNING`` and never a pass.
# --------------------------------------------------------------------------- #

#: Containment captured for each live worker, keyed by pid and guarded by
#: :data:`_live_workers_lock`.  A worker may appear here with no group, no
#: session and no job -- a platform offering none of them, or a capture that
#: failed -- which is the state the verification reports rather than passes
#: over.
_worker_containment: dict[int, _WorkerContainment] = {}

#: ``JobObjectExtendedLimitInformation`` from ``winnt.h``: the information
#: class carrying the limit flags.
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION: Final[int] = 9

#: ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``: terminate every process in the job
#: when its last handle closes.  The whole reason a job is used on Windows.
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE: Final[int] = 0x00002000

#: ``PROCESS_SET_QUOTA | PROCESS_TERMINATE`` -- what
#: ``AssignProcessToJobObject`` needs on the process handle.
_PROCESS_ASSIGN_ACCESS: Final[int] = 0x0100 | 0x0001

#: ``SYNCHRONIZE`` -- the single access right the liveness probe asks for.  It
#: permits waiting on the process and nothing else: not reading its memory, not
#: reading its exit code and not terminating it, so a probe cannot become an
#: action even by mistake.
_PROCESS_SYNCHRONIZE_ACCESS: Final[int] = 0x00100000

#: ``WAIT_OBJECT_0`` -- the process handle signalled, which for a process means
#: it has exited.
_WIN32_WAIT_OBJECT_0: Final[int] = 0x00000000

#: ``WAIT_TIMEOUT`` -- the zero-timeout wait expired, so the process is still
#: running.  This is the *only* result that means alive.
_WIN32_WAIT_TIMEOUT: Final[int] = 0x00000102

#: ``ERROR_INVALID_PARAMETER`` -- the one ``OpenProcess`` failure that means
#: the process id does not exist.  Every other failure means a process that
#: exists and is not this account's to observe.
_WIN32_ERROR_INVALID_PARAMETER: Final[int] = 87

#: How often a tree is re-checked while waiting for it to empty.  Polling,
#: because a process *group* is not something a single call can wait on.
_TREE_POLL_SECONDS: Final[float] = 0.05

#: The kernel's process directory, from which a session's live members are
#: read.  Named rather than spelled as a path, because the port's own
#: directory and artifact names belong to ``app/utils/paths.py`` and this is
#: neither: it is an operating-system interface, present on Linux and absent
#: elsewhere, which :func:`_session_members` tests for before reading.
_PROC_DIR_NAME: Final[str] = "proc"
_PROC_ROOT: Final[Path] = Path(os.sep) / _PROC_DIR_NAME

#: The name of the per-process status file under :data:`_PROC_ROOT`.
_PROC_STAT_NAME: Final[str] = "stat"

#: ``/proc/<pid>/stat`` field offsets, counted from the character after the
#: **last** ``)`` of the file -- which is where the fields become splittable,
#: since the second field is the executable name and may itself contain
#: whitespace and parentheses.  Zero is the run state, so the process group is
#: the third and the session the fourth value after it.
_PROC_STAT_GROUP_OFFSET: Final[int] = 2
_PROC_STAT_SESSION_OFFSET: Final[int] = 3

#: The closing delimiter of that second field.
_PROC_COMM_TERMINATOR: Final[str] = ")"


@dataclass(frozen=True, slots=True)
class _WorkerContainment:
    """How one worker's process tree can be reached and confirmed gone.

    Attributes:
        pid: The worker's process id, which is what a diagnostic names.
        process_group: Its process-group id on POSIX, captured at launch;
            ``None`` where the platform has no process groups or the lookup
            failed.
        job_handle: The kill-on-close Job Object handle on Windows; ``None``
            elsewhere or when one could not be established.
        session: Its session id on POSIX, captured at launch beside the group.
            The session is what a driver the worker starts stays inside even
            though it has a process group of its own, so this is the id
            reclamation and verification work from; ``None`` where the
            platform has no sessions or the lookup failed, which is reported
            as unverified rather than treated as nothing to reclaim.
    """

    pid: int
    process_group: int | None
    job_handle: int | None
    session: int | None = None


@dataclass(frozen=True, slots=True)
class _SessionMembers:
    """The live membership of one process session.

    Attributes:
        session: The session this membership was read for.
        pids: Every live process id in the session, the session leader
            included.  Empty means the session holds nothing.
        groups: The distinct process-group ids among those processes,
            unfiltered -- the worker's own group is one of them, and the
            others are the groups a driver and the browser it started occupy.
    """

    session: int
    pids: frozenset[int]
    groups: frozenset[int]

    def nested_groups(self, own_group: int | None = None) -> tuple[int, ...]:
        """The session's process groups other than the worker's own.

        The worker's own group is signalled directly rather than through the
        enumeration, so it is excluded here -- by its captured id and by the
        session id, which are the same number for a session leader.

        Args:
            own_group: The worker's captured process group, if any.

        Returns:
            The remaining group ids, ascending, so the order a reclamation
            signals in is deterministic and so is what it reports.
        """
        return tuple(sorted(self.groups - {self.session, own_group}))


def _kernel32() -> Any:
    """Return the Win32 ``kernel32`` binding, or ``None`` off Windows.

    A function rather than a module-level import, so this module still imports
    on POSIX -- where ``ctypes.WinDLL`` does not exist -- and so the Windows
    branches have one seam a test can put a double in front of.

    Returns:
        The loaded library, or ``None`` when it is unavailable.
    """
    if os.name != "nt":
        return None

    try:
        import ctypes

        return ctypes.WinDLL("kernel32", use_last_error=True)
    except (ImportError, OSError, AttributeError):  # pragma: no cover - POSIX
        logger.warning("Win32 kernel32 unavailable; worker trees cannot be contained")
        return None


def _win32_types(ctypes_module: Any) -> tuple[Any, Any, Any]:
    """Return the ctypes types the Win32 containment calls are declared with.

    ``ctypes.wintypes`` is preferred, because it is the authority on what each
    Windows type is.  Where that module cannot be imported the
    platform-independent equivalents stand in, and they are the same widths:
    a ``HANDLE`` **is** a void pointer, a ``BOOL`` a 32-bit signed integer and
    a ``DWORD`` a 32-bit unsigned one.

    Args:
        ctypes_module: The imported :mod:`ctypes`, passed in so this function
            adds no import of its own.

    Returns:
        ``(HANDLE, BOOL, DWORD)``.
    """
    try:
        # Function-scoped: the module has to import on POSIX too, and these
        # types are read only where a Win32 call is about to be declared.
        from ctypes import wintypes
    except (ImportError, ValueError):  # pragma: no cover - present on CPython
        return (ctypes_module.c_void_p, ctypes_module.c_int32, ctypes_module.c_uint32)

    return (wintypes.HANDLE, wintypes.BOOL, wintypes.DWORD)


def _declared(library: Any, name: str, restype: Any, argtypes: tuple[Any, ...]) -> Any:
    """Return one Win32 entry point with its call signature declared.

    Declaring the signature is not cosmetic.  Left undeclared, ctypes assumes
    a C ``int`` return, which **truncates** a 64-bit ``HANDLE`` to 32 bits on
    64-bit Windows: the job or process handle that comes back is then a
    different handle from the one the kernel created, and closing it neither
    reclaims the worker's tree nor reports that it did not.

    Args:
        library: The ``kernel32`` binding.
        name: The entry point's name.
        restype: The ctypes return type to declare.
        argtypes: The ctypes argument types to declare.

    Returns:
        The callable.  A binding that does not accept a declaration -- a
        double driven by value rather than by the C ABI -- is returned
        undeclared, since there is no ABI to get wrong in that case.
    """
    function = getattr(library, name)

    try:
        function.restype = restype
        function.argtypes = argtypes
    except (AttributeError, TypeError):
        logger.debug("Win32 %s accepts no prototype declaration", name)

    return function


def _win32_last_error(ctypes_module: Any) -> int:
    """Return the error code the last Win32 call through this module set.

    :func:`_kernel32` loads ``kernel32`` with ``use_last_error=True``, which is
    what makes ``ctypes.get_last_error`` the right source: it reads the value
    ctypes saved immediately after the call, where ``kernel32.GetLastError``
    would read whatever the thread's error state holds by the time Python gets
    round to asking -- ctypes' own intervening calls included.

    Args:
        ctypes_module: The imported :mod:`ctypes`, passed in so this function
            adds no import of its own.

    Returns:
        The saved error code, or ``0`` where the platform cannot report one --
        which no caller may read as success, since each one names the single
        code it is looking for.
    """
    reader = getattr(ctypes_module, "get_last_error", None)

    return int(reader()) if reader is not None else 0


def _close_win32_handle(library: Any, handle: int) -> str | None:
    """Close one Win32 handle and report whether the close took effect.

    The result is never discarded, because on Windows this close *is* the
    reclamation: a kill-on-close job terminates its members when its last
    handle goes, so a close that quietly failed is a browser still running.

    Args:
        library: The ``kernel32`` binding.
        handle: The handle to close.

    Returns:
        ``None`` when the handle was closed, or the reason it was not, for the
        caller to report with its own context.
    """
    # Function-scoped for the same reason as everywhere else on this path:
    # nothing here is reached off Windows.
    import ctypes

    handle_type, boolean, _ = _win32_types(ctypes)
    close_handle = _declared(library, "CloseHandle", boolean, (handle_type,))

    try:
        if close_handle(handle):
            return None
    except OSError as error:
        return str(error)

    return f"the call reported failure (Win32 error {_win32_last_error(ctypes)})"


def _assign_kill_on_close_job(pid: int) -> int | None:
    """Put one worker in a fresh job that kills its tree when closed.

    Assignment happens after the process has started, which Windows 8 and
    later permit because jobs nest; processes the worker starts afterwards
    inherit the job.

    Every entry point used here is called through :func:`_declared`, so the
    handles crossing this boundary keep their full width.

    Args:
        pid: The worker's process id.

    Returns:
        The job handle, or ``None`` when no job could be established -- in
        which case the worker is recorded without one and its verification
        says so rather than assuming success.
    """
    library = _kernel32()

    if library is None:
        return None

    import ctypes

    class _BasicLimits(ctypes.Structure):
        """``JOBOBJECT_BASIC_LIMIT_INFORMATION`` from ``winnt.h``."""

        _fields_ = (
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", ctypes.c_uint32),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", ctypes.c_uint32),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", ctypes.c_uint32),
            ("SchedulingClass", ctypes.c_uint32),
        )

    class _IoCounters(ctypes.Structure):
        """``IO_COUNTERS`` from ``winnt.h``."""

        _fields_ = tuple(
            (name, ctypes.c_uint64)
            for name in (
                "ReadOperationCount",
                "WriteOperationCount",
                "OtherOperationCount",
                "ReadTransferCount",
                "WriteTransferCount",
                "OtherTransferCount",
            )
        )

    class _ExtendedLimits(ctypes.Structure):
        """``JOBOBJECT_EXTENDED_LIMIT_INFORMATION`` from ``winnt.h``."""

        _fields_ = (
            ("BasicLimitInformation", _BasicLimits),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        )

    handle_type, boolean, dword = _win32_types(ctypes)
    create_job = _declared(
        library,
        "CreateJobObjectW",
        handle_type,
        (ctypes.c_void_p, ctypes.c_wchar_p),
    )
    set_job_information = _declared(
        library,
        "SetInformationJobObject",
        boolean,
        (handle_type, ctypes.c_int, ctypes.c_void_p, dword),
    )
    open_process = _declared(
        library, "OpenProcess", handle_type, (dword, boolean, dword)
    )
    assign_to_job = _declared(
        library, "AssignProcessToJobObject", boolean, (handle_type, handle_type)
    )

    job = None
    process_handle = None

    try:
        job = create_job(None, None)

        if not job:
            logger.warning("Could not create a containment job for worker %d", pid)
            return None

        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE

        if not set_job_information(
            job,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            logger.warning("Could not limit the containment job for worker %d", pid)
            _close_job_handle(library, job, pid)
            return None

        process_handle = open_process(_PROCESS_ASSIGN_ACCESS, False, pid)

        if not process_handle:
            logger.warning("Could not open worker %d for containment", pid)
            _close_job_handle(library, job, pid)
            return None

        if not assign_to_job(job, process_handle):
            logger.warning("Could not contain worker %d in a job", pid)
            _close_job_handle(library, job, pid)
            return None

        return int(job)
    except OSError as error:
        logger.warning(
            "Containment job for worker %d failed: %s", pid, error, exc_info=True
        )

        if job:
            _close_job_handle(library, job, pid)

        return None
    finally:
        if process_handle:
            reason = _close_win32_handle(library, process_handle)

            if reason is not None:
                logger.warning(
                    "Could not close the process handle for worker %d: %s", pid, reason
                )


def _close_job_handle(library: Any, job: int, pid: int) -> None:
    """Close an abandoned containment job, reporting a close that failed.

    A job carrying a kill-on-close limit and holding no process is exactly
    what must not be leaked, so every path that abandons one comes through
    here.

    Args:
        library: The ``kernel32`` binding.
        job: The job handle to close.
        pid: The worker the job was being built for, which is what the
            diagnostic names.

    Returns:
        ``None``.
    """
    reason = _close_win32_handle(library, job)

    if reason is not None:
        logger.warning(
            "Could not close the abandoned containment job for worker %d: %s",
            pid,
            reason,
        )


def _stat_ids(text: str) -> tuple[int, int] | None:
    """Read the process group and session out of one ``stat`` file's text.

    The second field of that file is the executable name in parentheses, and
    an executable name may contain whitespace and parentheses of its own --
    ``(my program (2).py)`` is a legal one.  Splitting the whole line on
    whitespace therefore mis-numbers every field after it, so the text is cut
    at its **last** closing parenthesis first and only the remainder is split.

    Args:
        text: The contents of one ``stat`` file.

    Returns:
        ``(process group, session)``, or ``None`` when the text does not have
        that shape -- a file read at the moment its process exited can be
        short or empty, which is not an error.
    """
    _, delimiter, remainder = text.rpartition(_PROC_COMM_TERMINATOR)

    if not delimiter:
        return None

    fields = remainder.split()

    if len(fields) <= _PROC_STAT_SESSION_OFFSET:
        return None

    try:
        return (
            int(fields[_PROC_STAT_GROUP_OFFSET]),
            int(fields[_PROC_STAT_SESSION_OFFSET]),
        )
    except ValueError:
        return None


def _session_members(session: int) -> _SessionMembers | None:
    """Enumerate the live processes of one session, and their groups.

    This is what makes a driver reachable.  The worker leads its own session
    and everything it starts stays in that session, but a driver runs in a
    process group of its own inside it, so the worker's group is not the whole
    tree and signalling that group alone leaves the driver and its browser
    running.

    Args:
        session: The session id captured at the worker's launch.

    Returns:
        The membership, or ``None`` when this platform cannot be asked -- the
        kernel's process directory is absent, or could not be listed.  Every
        caller reads ``None`` as *cannot determine* and reports it as
        unverified; none of them reads it as an empty session.

    Notes:
        Never raises.  A process can exit between the listing and the read of
        its status file, which is the ordinary case rather than a failure, and
        a status file may be unreadable for a process owned by somebody else.
        Both are skipped.
    """
    if session <= 0 or not _PROC_ROOT.is_dir():
        return None

    try:
        entries = list(_PROC_ROOT.iterdir())
    except OSError as error:
        logger.debug("Could not list the process directory: %s", error)
        return None

    pids: set[int] = set()
    groups: set[int] = set()

    for entry in entries:
        if not entry.name.isdigit():
            continue

        try:
            text = (entry / _PROC_STAT_NAME).read_text(
                encoding="utf-8", errors="replace"
            )
        except (OSError, ValueError):
            # Exited between the listing and the read, or not ours to read.
            continue

        ids = _stat_ids(text)

        if ids is None or ids[1] != session:
            continue

        pids.add(int(entry.name))

        if ids[0] > 0:
            groups.add(ids[0])

    return _SessionMembers(session, frozenset(pids), frozenset(groups))


def _group_in_session(
    group: int | None,
    session: int | None,
    members: _SessionMembers | None = None,
) -> bool | None:
    """Whether a process group still belongs to a captured session.

    The attribution every signal is conditioned on.  A process-group id is a
    recycled resource: once the group is gone the same number can be handed to
    an unrelated process, and signalling it then would kill something this run
    never started.

    Two instruments, in order, because neither answers alone:

    * ``getsid`` of the group id, which is the group *leader's* pid.  It
      answers while that leader is alive, and a POSIX process group belongs
      entirely to one session, so a match settles the question.
    * the session's live membership, for a group whose leader has already
      exited while other members of the group have not.  A live process in the
      captured session that carries this group id is the same evidence, and it
      is the only evidence available once the leader has been reaped -- which
      is the state on every path that reclaims after a worker has exited.

    Args:
        group: The candidate group id.
        session: The session id captured at the worker's launch.
        members: A membership already read for that session, used instead of
            reading it again; omitted, the session is read only if the first
            instrument cannot answer.

    Returns:
        ``True`` when the group is attributed to the session, ``False`` when it
        demonstrably belongs to another session, and ``None`` when neither
        instrument can answer -- which callers distinguish, because "not this
        worker's" and "no evidence either way" are not the same thing.
    """
    if group is None or session is None or group <= 0 or session <= 0:
        return None

    if not _HAS_PROCESS_GROUPS:
        return None

    try:
        return os.getsid(group) == session
    except ProcessLookupError:
        # The group's leader has gone; the membership below is what is left.
        pass
    except OSError as error:
        logger.debug("Could not attribute process group %d: %s", group, error)
        return None

    if members is None:
        members = _session_members(session)

    if members is None:
        return None

    return group in members.groups


def _capture_containment(process: subprocess.Popen[str]) -> _WorkerContainment:
    """Record how a just-launched worker's tree can be reached.

    Called while the worker is certainly alive, which is the only moment its
    process group and session can be looked up: once it has exited and been
    reaped the ids are gone, and a later lookup would either fail or -- worse
    -- resolve a recycled one.  Both are read here, independently, because
    each is used for something the other cannot do: the group is what the
    worker itself is signalled through, and the session is what a driver in a
    group of its own is found and reclaimed through.

    Args:
        process: The worker just started.

    Returns:
        The containment record.  A lookup that failed leaves its field
        ``None``, which the verification reports as unverified; nothing is
        inferred from the pid.
    """
    if _HAS_PROCESS_GROUPS:
        group: int | None = None
        session: int | None = None

        try:
            group = os.getpgid(process.pid)
        except OSError as error:
            logger.warning(
                "Worker %d has no reachable process group: %s", process.pid, error
            )

        try:
            session = os.getsid(process.pid)
        except OSError as error:
            logger.warning(
                "Worker %d has no reachable session: %s", process.pid, error
            )

        return _WorkerContainment(process.pid, group, None, session)

    return _WorkerContainment(
        process.pid, None, _assign_kill_on_close_job(process.pid)
    )


def _process_group_is_empty(group: int | None) -> bool | None:
    """Whether nothing is left in one process group, where that is knowable.

    Signal ``0`` performs the existence and permission checks and delivers
    nothing, so this asks the kernel rather than inferring the answer from what
    was signalled earlier.  It **succeeding** means at least one process is
    still in the group, which is the sense the answer inverts on.

    Args:
        group: The group id to probe, or ``None`` for a record that has none.

    Returns:
        ``True`` when the group is confirmed empty, ``False`` when at least one
        process remains, and ``None`` when it cannot be answered.
    """
    if group is None or group <= 0:
        return None

    try:
        os.killpg(group, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        # Something is there, and this process may no longer signal it.
        return False
    except OSError as error:
        logger.debug("Could not inspect worker group %d: %s", group, error)
        return None

    # Delivery succeeded, so at least one process is still in the group.
    return False


def _session_is_empty(session: int | None) -> bool | None:
    """Whether nothing is left in one session, where that is knowable.

    The wider of the two questions, and the one a driver answers: a driver
    runs in a process group of its own, so a worker's group can be empty while
    its session still holds a driver and a browser.

    Args:
        session: The session id captured at launch, or ``None`` for a record
            that has none.

    Returns:
        ``True`` when the session is confirmed empty, ``False`` when at least
        one process remains, and ``None`` when this platform cannot enumerate
        a session -- which is reported as unverified and never as clean.
    """
    if session is None:
        return None

    members = _session_members(session)

    if members is None:
        return None

    return not members.pids


def _worker_tree_is_empty(containment: _WorkerContainment) -> bool | None:
    """Whether nothing of a worker's tree is left, where that is knowable.

    The tree is both dimensions together -- the worker's own process group and
    the session that holds every group anything it started runs in -- because
    either one alone can be empty while the other is not, and a release that
    only half of them confirms is not a release.

    Args:
        containment: The record captured at launch.

    Returns:
        ``True`` only when every dimension of the record is confirmed gone,
        ``False`` when any of them still holds a process, and ``None`` when
        what is left could not be determined -- reported as unverified and
        never as clean.
    """
    states = (
        _process_group_is_empty(containment.process_group),
        _session_is_empty(containment.session),
    )

    if False in states:
        return False

    if all(state is True for state in states):
        return True

    return None


def _await_worker_tree_exit(containment: _WorkerContainment, deadline: float) -> bool | None:
    """Poll a worker's tree until it empties or ``deadline`` passes.

    Args:
        containment: The record captured at launch.
        deadline: A :func:`time.monotonic` value to stop at.

    Returns:
        What :func:`_worker_tree_is_empty` last reported.
    """
    while True:
        empty = _worker_tree_is_empty(containment)

        if empty is not False or time.monotonic() >= deadline:
            return empty

        time.sleep(_TREE_POLL_SECONDS)


def _close_worker_job(pid: int) -> None:
    """Close a worker's containment job, terminating whatever is left in it.

    Idempotent, and a no-op on POSIX and for a worker that never got a job:
    the record is removed from the registry first, so a second call finds
    nothing. On Windows this is what actually reclaims a browser the worker
    left behind, since the job's limit terminates its members on close.

    Args:
        pid: The worker's process id.

    Returns:
        ``None``.
    """
    with _live_workers_lock:
        containment = _worker_containment.pop(pid, None)

    if containment is None or containment.job_handle is None:
        return

    library = _kernel32()

    if library is None:  # pragma: no cover - POSIX has no job to close
        return

    reason = _close_win32_handle(library, containment.job_handle)

    if reason is not None:
        logger.warning(
            "Could not close the containment job for worker %d, whose tree may "
            "still be running: %s",
            pid,
            reason,
        )


def _report_surviving_tree(containment: _WorkerContainment, empty: bool | None) -> None:
    """Record a worker tree that could not be confirmed stopped.

    Silent for a tree confirmed gone. A tree that is still there is an
    ``ERROR``, because a surviving browser holds the system under test's
    authenticated session; a tree this platform cannot inspect is a
    ``WARNING``, because the state is unknown rather than known bad.

    The worker is named either way, and the ``ERROR`` carries both ids an
    operator needs to find what is left: the group the worker itself led and
    the session every group anything it started runs in.

    Args:
        containment: The record captured at launch.
        empty: What the verification reported.

    Returns:
        ``None``.
    """
    if empty is True:
        return

    if empty is False:
        logger.error(
            "Worker %d left processes running in its group (%s) or session "
            "(%s); a browser may still hold an authenticated session",
            containment.pid,
            containment.process_group,
            containment.session,
        )
        return

    logger.warning(
        "Worker %d exited but its process tree could not be verified empty",
        containment.pid,
    )


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
            The text is what :func:`_relay_stream` logged, so it is
            control-safe, redacted and bounded rather than raw: a reader of
            this cannot recover what the console declined to print.
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

    The containment for its whole tree is captured in the same step, because
    the only moment a worker's process group can be looked up safely is while
    the worker is alive -- and because a worker recorded without containment
    would be one that cancellation could reach only as a single process.

    Args:
        process: The worker just started.

    Returns:
        ``None``.
    """
    containment = _capture_containment(process)

    with _live_workers_lock:
        _live_workers[process.pid] = process
        _worker_containment[process.pid] = containment


def _forget_worker(
    process: subprocess.Popen[str],
    *,
    grace_seconds: float = _TERMINATION_GRACE_SECONDS,
) -> None:
    """Drop a worker from the registry once it has been waited for.

    Idempotent: the owning thread forgets its worker in a ``finally``, and
    :func:`terminate_live_workers` forgets whatever it stopped, so the same
    worker is routinely dropped twice -- and the second drop finds no record,
    says nothing and raises nothing.

    Dropping a worker is also where its tree is reclaimed and accounted for,
    which is why this runs on **every** path and not only on cancellation. A
    worker that exited on its own can still have left a driver executable or
    a browser behind, holding the system under test's authenticated session,
    and nothing else in the run would notice. So the session is reclaimed the
    way a cancelled worker's is: the worker's own group is never signalled
    here, because its leader has been reaped and that id is no longer
    attributable, while every group nested in its session is attributed and
    reclaimed. On Windows the job is closed here, and that close is itself the
    reclamation.

    Args:
        process: The worker to drop.
        grace_seconds: How long a surviving session gets to empty after each
            signal of the reclamation.

    Returns:
        ``None``.
    """
    with _live_workers_lock:
        _live_workers.pop(process.pid, None)
        containment = _worker_containment.get(process.pid)

    _reclaim_worker_session(containment, grace_seconds=grace_seconds)

    _close_worker_job(process.pid)


def _containment_of(pid: int) -> _WorkerContainment | None:
    """Read one worker's containment record under the registry's lock.

    Args:
        pid: The worker's process id.

    Returns:
        The record captured at launch, or ``None`` for a worker that was never
        registered or has already been accounted for.
    """
    with _live_workers_lock:
        return _worker_containment.get(pid)


def _signal_worker_group(
    process: subprocess.Popen[str], signal_number: int
) -> bool:
    """Signal a worker's own process group, where the platform allows it.

    Signalling the *group* rather than the process is what reaches the engine
    together with anything it started inside that group.  The group exists
    because :func:`_spawn_worker` asked for one; the groups a driver runs in
    are separate and are reached by :func:`_signal_nested_groups`.

    The id comes from the record :func:`_register_worker` captured at launch,
    and falls back to a live lookup only for a worker that was never
    registered. That order matters on the one path where it differs: once the
    worker has exited *and been waited for*, ``os.getpgid`` can no longer
    resolve it.

    The group is **attributed** to the captured session first, so an id that
    now belongs to another session is skipped rather than signalled.  An
    attribution that cannot be made either way does not stop the signal: this
    group's id is the pid of a process this run started and holds, the
    platform offered no evidence against it, and the alternative to signalling
    is leaving an authenticated browser running.

    Args:
        process: The worker to signal.
        signal_number: The signal to send.

    Returns:
        ``True`` when the group was signalled, ``False`` when this platform
        has no process groups, the group belongs to another session, or the
        signal failed -- in which case the caller falls back to the process
        itself.
    """
    if not _HAS_PROCESS_GROUPS:
        return False

    containment = _containment_of(process.pid)
    group = containment.process_group if containment is not None else None
    session = containment.session if containment is not None else None

    if _group_in_session(group, session) is False:
        logger.debug(
            "Process group %s of worker %d is no longer in session %s; not signalled",
            group,
            process.pid,
            session,
        )
        return False

    try:
        os.killpg(group if group is not None else os.getpgid(process.pid), signal_number)
    except OSError as error:
        logger.debug(
            "Could not signal the process group of worker %d: %s",
            process.pid,
            error,
        )
        return False
    return True


def _signal_nested_groups(
    containment: _WorkerContainment | None, signal_number: int
) -> tuple[int, ...]:
    """Signal every group of a worker's session other than the worker's own.

    The driver a worker starts leads a process group of its own inside the
    worker's session, and the browser that driver starts is in that same
    group, so this is what reaches them.  Every candidate is attributed to the
    captured session with :func:`_group_in_session` before it is signalled --
    positively attributed, not merely *not* refuted, because these ids come
    from the operating system rather than from anything this run holds open.
    That is what keeps a recycled group id from being killed by mistake, and
    it is why reclaiming here is safe on the paths where the worker's own
    leader has already exited.

    Args:
        containment: The record captured at launch, or ``None`` for a worker
            with no record, for which nothing is signalled.
        signal_number: The signal to send to each attributed group.

    Returns:
        The groups that were signalled, ascending; empty when there were none,
        when the session could not be enumerated, or when this platform has no
        process groups.

    Notes:
        Never raises.  This runs on cancellation and completion paths where an
        exception would replace what the run was reporting.
    """
    if containment is None or not _HAS_PROCESS_GROUPS:
        return ()

    session = containment.session

    if session is None:
        return ()

    members = _session_members(session)

    if members is None:
        return ()

    signalled: list[int] = []

    for group in members.nested_groups(containment.process_group):
        if _group_in_session(group, session, members) is not True:
            logger.debug(
                "Process group %d is not attributable to session %d; not signalled",
                group,
                session,
            )
            continue

        try:
            os.killpg(group, signal_number)
        except OSError as error:
            # Gone between the enumeration and the signal, which is ordinary.
            logger.debug("Could not signal process group %d: %s", group, error)
            continue

        signalled.append(group)

    if signalled:
        logger.warning(
            "Signalled %d process group(s) nested in worker %d's session: %s",
            len(signalled),
            containment.pid,
            signalled,
        )

    return tuple(signalled)


def _reclaim_worker_session(
    containment: _WorkerContainment | None,
    *,
    grace_seconds: float = _TERMINATION_GRACE_SECONDS,
) -> bool | None:
    """Reclaim and account for what a worker's session still holds.

    The path for a worker whose own leader is already gone -- cancelled after
    it exited, or completed normally -- where a driver and its browser can
    still be alive in their own process group inside the worker's session.

    Only nested groups are signalled here.  The worker's own group is outside
    this function's remit: on these paths its leader has been reaped, which is
    both why nothing else in the run would notice the survivors and why that
    one id is the one thing attribution cannot vouch for.  Every nested group
    can be vouched for, which is what makes reclaiming them safe.

    Silent and immediate for a session confirmed empty, which is every
    ordinary completion.

    Args:
        containment: The record captured at launch, or ``None`` for a worker
            with no record, for which there is nothing to reclaim or report.
        grace_seconds: How long the session gets to empty after the polite
            signal, and again after the kill.

    Returns:
        What the verification last reported: ``True`` confirmed empty,
        ``False`` still holding a process, ``None`` undeterminable.  ``None``
        is also the answer for a worker with no record.

    Notes:
        Never raises, for the same reason :func:`_signal_nested_groups` does
        not.
    """
    if containment is None:
        return None

    empty = _worker_tree_is_empty(containment)

    if empty is True:
        return True

    if _signal_nested_groups(containment, signal.SIGTERM):
        empty = _await_worker_tree_exit(
            containment, time.monotonic() + grace_seconds
        )

        if empty is False:
            _signal_nested_groups(containment, _KILL_SIGNAL)
            empty = _await_worker_tree_exit(
                containment, time.monotonic() + grace_seconds
            )

    _report_surviving_tree(containment, empty)

    return empty


def _terminate_worker(
    process: subprocess.Popen[str],
    *,
    grace_seconds: float = _TERMINATION_GRACE_SECONDS,
) -> bool:
    """Stop one worker and everything it started, and **wait** for it.

    Polite first: ``SIGTERM`` to the worker's process group *and* to every
    attributed group nested in its session, so the engine can close its
    driver session and write what it has while the driver and browser it
    started are reached too.  Then a bounded grace, then a kill of the same
    set.  Waiting after each signal is not optional -- a cleanup that returns
    while the process it signalled is still running is exactly how a browser
    outlives its run.

    A worker whose own leader has already exited is **not** passed over: its
    session can still hold a live driver and browser, so it is reclaimed and
    accounted for before the answer below is given.

    Args:
        process: The worker to stop.
        grace_seconds: How long it gets to exit before it is killed, and
            again after the kill before the attempt is reported as failed.

    Returns:
        ``True`` if the worker was running and has now been stopped,
        ``False`` if it had already exited.  Either way its session has been
        reclaimed and what could not be reclaimed has been reported.
    """
    if process.poll() is not None:
        _reclaim_worker_session(
            _containment_of(process.pid), grace_seconds=grace_seconds
        )
        return False

    logger.warning("Stopping worker process %d", process.pid)
    if not _signal_worker_group(process, signal.SIGTERM):
        try:
            process.terminate()
        except OSError as error:
            logger.debug("Could not terminate worker %d: %s", process.pid, error)
    _signal_nested_groups(_containment_of(process.pid), signal.SIGTERM)
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        logger.warning(
            "Worker process %d did not exit within %.0fs; killing it",
            process.pid,
            grace_seconds,
        )
    else:
        # The worker itself is gone, which is **not** the same as its tree
        # being gone: the ``SIGTERM`` above went to the worker's group and to
        # the groups nested in its session, but a driver executable or a
        # browser that ignored it is still running.  So the polite path is
        # verified exactly like the forceful one, and only returns once the
        # tree has been accounted for.
        if _verify_tree_stopped(process, grace_seconds=grace_seconds) is not False:
            return True

        logger.warning(
            "Worker %d exited but left its tree running; killing the group",
            process.pid,
        )

    if not _signal_worker_group(process, _KILL_SIGNAL):
        try:
            process.kill()
        except OSError as error:
            logger.debug("Could not kill worker %d: %s", process.pid, error)
    _signal_nested_groups(_containment_of(process.pid), _KILL_SIGNAL)
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

    _verify_tree_stopped(process, grace_seconds=grace_seconds)

    return True


def _verify_tree_stopped(
    process: subprocess.Popen[str],
    *,
    grace_seconds: float = _TERMINATION_GRACE_SECONDS,
) -> bool | None:
    """Confirm that a signalled worker's whole tree has actually gone.

    The step that turns "it was signalled" into "nothing of it is left". On
    POSIX a bounded poll of the worker's process group **and of its session**
    answers it, so a driver or browser left running in a group of its own
    inside that session is a non-empty tree rather than an unobserved one; on
    Windows the containment job is closed, which terminates the job's members,
    and the immediate process is then waited for so the closure is known to
    have taken effect. Either way the outcome is reported rather than assumed.

    Never raises: this runs while a run is being cancelled, where an exception
    would replace what the cancellation was reporting.

    Args:
        process: The worker that was signalled.
        grace_seconds: How long the tree gets to disappear.

    Returns:
        ``True`` when the tree is confirmed gone, ``False`` when something
        remains, and ``None`` when it could not be determined.
    """
    containment = _containment_of(process.pid)

    if containment is None:
        return None

    if containment.process_group is not None or containment.session is not None:
        empty = _await_worker_tree_exit(containment, time.monotonic() + grace_seconds)
        _report_surviving_tree(containment, empty)
        return empty

    # Windows: closing the job is the reclamation, and the wait below is its
    # verification.  ``_close_worker_job`` removes the record, so the report is
    # built from what the wait observed rather than from another group probe.
    _close_worker_job(process.pid)

    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        logger.error(
            "Worker %d survived its containment job and may still be running",
            process.pid,
        )
        return False
    except (OSError, ValueError) as error:
        logger.warning("Could not wait for worker %d: %s", process.pid, error)
        return None

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

    On Windows the creation flag isolates the worker from this process's
    console signals but does **not** make its tree killable, which is why
    :func:`_assign_kill_on_close_job` adds a Job Object there once the process
    exists. The two are complementary and neither replaces the other.

    Returns:
        ``{"start_new_session": True}`` on POSIX,
        ``{"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}`` on Windows,
        and an empty mapping on a platform offering neither -- where the
        launch still works and only the group isolation is unavailable.
    """
    if _HAS_PROCESS_GROUPS:
        return {"start_new_session": True}
    creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", None)
    if creation_flags is None:
        return {}
    return {"creationflags": creation_flags}


def _drain_over_long_line(stream: IO[str]) -> tuple[int, bool]:
    """Discard the rest of a logical line that exceeded the read bound.

    Called once the first :data:`_RELAY_READ_LIMIT` characters of an
    over-long line have been relayed as its head.  The remainder is read in
    bounded instalments and **dropped**: nothing is logged for it, nothing is
    retained from it, and no instalment is appended to anything, so the peak
    allocation stays at one instalment however much the child writes.  Only a
    character count survives, which is what the caller reports.

    Dropping is deliberate rather than a shortcut.  Relaying every instalment
    would turn one pathological child line into an unbounded number of parent
    records -- the same denial of service in a different currency -- and each
    record after the first carries no diagnostic value: a reader already has
    the head, and a shard's classification comes from its exit status and its
    result file rather than from its text.

    Args:
        stream: The worker's text-mode pipe, positioned inside the over-long
            line.

    Returns:
        ``(discarded, ended_at_eof)``: how many characters of the line were
        dropped, excluding the terminator, and whether the stream ended
        before that terminator ever arrived.
    """
    discarded = 0
    while True:
        fragment = stream.readline(_RELAY_READ_LIMIT)
        if not fragment:
            # The worker exited, or was killed, mid-line.
            return discarded, True
        if fragment.endswith("\n"):
            # The line finally ended.  The terminator itself is not part of
            # what a reader lost, so it is not counted.
            return discarded + len(fragment.rstrip("\r\n")), False
        discarded += len(fragment)


def _relay_stream(
    stream: IO[str], tag: str, level: int, retained: deque[str]
) -> None:
    """Relay one worker stream to the log, rendered, as each line arrives.

    Runs in its own thread, one per stream, because a worker writes to both
    and draining them in sequence would deadlock the moment the stream not
    being read filled its pipe buffer.  Blank lines are dropped -- behave
    emits many -- and every other line is logged the moment it is complete,
    so the record reaches the console handler while the worker is still
    running.

    **No line is trusted as log text, and this is the path production
    takes.**  What arrives here is whatever the engine, a driver, a page
    object or a step printed, so every line is rendered by
    :func:`~app.logging_config.render_worker_line` -- the single
    implementation of that rendering in this port, in the module that owns the
    console contract -- before it reaches a record and before it is retained.
    That rendering is control-safe (a terminal escape sequence cannot recolour
    what the console printed and a line break cannot forge a second, untagged
    record), redacted of credential-shaped content, and bounded at
    :data:`~app.logging_config.RELAYED_LINE_LIMIT` characters with a suffix
    naming how many were dropped.  :func:`_relay_output` renders identically;
    the two are the live and the after-the-fact halves of one contract, and
    neither may construct safe text of its own.  Redaction is log-only: it
    applies to the parent record built here and to nothing in the worker's
    result document or the published artifacts, which AAP 0.8 requires to
    carry the suite's fixture data verbatim.

    **The stream is a severity floor, never a ceiling.**  behave's default
    ``logging_format`` puts ``LOG_<LEVEL>:<logger>:`` at column 0, so a
    child's own severity is readable from the line; the record is emitted at
    ``max(child level, level)``.  An ``ERROR`` token is therefore honoured on
    either stream -- which is what keeps a screenshot helper's suppressed
    capture or a formatter failure visible as a failure -- while a ``DEBUG``
    token on standard error cannot pull a diagnostic below ``WARNING``, so the
    published stream split holds and a child cannot mute itself by printing a
    low token.

    **The read is bounded.**  Each instalment is taken with
    :data:`_RELAY_READ_LIMIT` as a limit, so a child that never writes a
    newline cannot decide how much memory this parent allocates.  A line
    within the bound is read whole exactly as before.  One that is not has its
    head relayed like any other line, and its remainder is discarded by
    :func:`_drain_over_long_line`, after which exactly one further record
    names how many characters were lost -- the count, never the text.

    Nothing here decides anything.  Relayed output is diagnostics: a shard's
    classification, and the run's exit status, come from the worker's exit
    status and its result file alone (``pom.xml:25``'s
    ``testFailureIgnore=true``).

    Args:
        stream: The worker's text-mode pipe.  Opened with ``text=True``, so
            universal-newline translation is in effect and a complete line
            ends with ``"\\n"`` whatever the child wrote.
        tag: Shard label every record is prefixed with, which is what keeps
            concurrent shards readable -- and what the control-safe rendering
            above makes a guarantee rather than a convention.
        level: ``logging.INFO`` for standard output, ``logging.WARNING`` for
            standard error, which is the stream split AAP 0.4.1 requires.
            Read as a floor, per above.
        retained: Bounded tail the rendered lines are also appended to.  The
            sanitized, bounded text is what is kept, so a reader of
            :attr:`WorkerProcess.stderr` sees what the log saw.

    Returns:
        ``None``.  Returns at the pipe's EOF, which is the worker's exit.
    """
    try:
        while True:
            # readline(limit) rather than iterating the file object: a line is
            # taken the moment it is complete, and a child that writes no
            # newline at all is bounded instead of allocated whole.
            fragment = stream.readline(_RELAY_READ_LIMIT)
            if not fragment:
                return
            line = fragment.rstrip("\r\n")
            if line.strip():
                line_level, safe_text = render_worker_line(
                    line, default_level=level
                )
                retained.append(safe_text)
                logger.log(line_level, "[%s] %s", tag, safe_text)
            if fragment.endswith("\n"):
                continue
            # No terminator: either the head of an over-long logical line, or
            # the child's last line, unterminated, at EOF.  The drain tells
            # the two apart and costs nothing in the second case.
            discarded, ended_at_eof = _drain_over_long_line(stream)
            if discarded:
                # One record, a count and no text.  Logged as a diagnostic
                # rather than as progress even for standard output, because
                # output being dropped is an anomaly of the child rather than
                # something the run is reporting about itself.
                logger.warning(
                    "[%s] <relay bound reached: %d further characters of"
                    " this line were discarded>",
                    tag,
                    discarded,
                )
            if ended_at_eof:
                return
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


def _worker_environment() -> dict[str, str]:
    """Build the environment a worker is started with.

    The child's environment is **composed**, not inherited: every name in
    :data:`_WORKER_ENV_ALLOWLIST` that this process actually carries is copied
    across with its value untouched, everything else is left behind, and the
    one variable the launch needs for itself is then set.  That ordering is
    deliberate -- the unbuffered setting is the run's own decision, so it is
    written last and cannot be displaced by an inherited value of the same
    name.

    Why the environment is composed at all: every ambient variable of the
    process that started the run would otherwise reach an engine that reads
    variables to decide **which Python it imports and executes** -- the stage
    variables at the head of :data:`_WORKER_ENV_ALLOWLIST`'s notes being the
    sharp end of it -- and to decide what it trusts when it fetches a browser
    driver.  Nothing about a worker's job needs that inheritance.

    Returns:
        A fresh mapping, one per launch, safe for the caller to hand straight
        to :class:`subprocess.Popen`.  It never aliases :data:`os.environ`
        and nothing here mutates this process's own environment: a run is
        supervised from several threads, and an in-place edit of the parent's
        environment would be visible to all of them and to every later
        launch.

    Notes:
        Names are matched exactly, which is what makes the set readable as a
        policy.  ``os.environ`` is case-preserving on POSIX and upper-casing
        on Windows, so the upper-case spellings in the set are the right ones
        on both: a POSIX lower-case ``http_proxy`` is not a member and is
        therefore dropped, which is the intended outcome rather than a gap.
    """
    environment = {
        name: value
        for name, value in os.environ.items()
        if name in _WORKER_ENV_ALLOWLIST
    }
    environment[_UNBUFFERED_ENV_VAR] = _UNBUFFERED_ENV_VALUE
    return environment


def _spawn_worker(command: Sequence[str], cwd: Path) -> _RelayedWorker:
    """Launch one worker, relay its output as it arrives, and wait for it.

    The default implementation behind :func:`run_suite`'s ``spawn`` seam, and
    the only place in this module that starts a process.

    Three properties, each of them load-bearing:

    * **The output is live, and none of it is trusted.**  Both pipes are
      drained by their own reader thread and every line is logged the moment
      it is complete, so the stream split in AAP 0.4.1 holds *during* the run
      rather than after it; the child's own buffering is switched off for the
      same reason.  ``INFO`` for stdout and ``WARNING`` for stderr are that
      split's **floors**: :func:`_relay_stream` renders each line through
      :func:`~app.logging_config.render_worker_line`, which makes it
      control-safe, redacts credential-shaped content and bounds its length,
      and emits it at the higher of the child's own severity and the floor, so
      a child ``ERROR`` stays an error and a child ``DEBUG`` on stderr stays a
      warning.  Each read is bounded too, at :data:`_RELAY_READ_LIMIT`
      characters, so a child that writes no newline cannot make this process
      allocate its output whole: the head is relayed and the remainder is
      discarded with one record naming the count.  What the return value
      carries is a bounded tail of that rendered text, never the whole
      transcript and never the raw text.
    * **The child is isolated.**  It runs in its own process group, so
      stopping it stops the driver and browser it started, and a ``Ctrl-C``
      delivered to this process does not reach it behind the supervisor's
      back.  :func:`terminate_live_workers` is the only thing that stops it.
      Its *environment* is isolated in the same sense and for a stronger
      reason: it is composed from an allowlist by
      :func:`_worker_environment` rather than inherited, so no ambient
      variable of the process that started the run can redirect what the
      engine imports and executes or what its driver provisioning trusts.
    * **It is accounted for.**  The worker is in the registry from the moment
      it exists until it has been waited for, so an interrupt on any thread
      can find and reclaim it.

    Args:
        command: The argument list from :func:`build_worker_command`, passed
            as a list with no shell, so nothing in it is interpreted by one.
        cwd: The run base.

    Returns:
        The finished worker: its status, a bounded tail of each stream and the
        ``output_relayed`` marker.  A non-zero status is never raised, which
        would put a test outcome into the exit status (``pom.xml:25``,
        ``testFailureIgnore=true``); no timeout or retry is imposed.

    Raises:
        OSError: If the process cannot be started; :func:`_run_one_shard`
            records that as a dead shard.
        BaseException: An interrupt, re-raised once the worker is stopped.
    """
    # A composed environment, never this process's own and never a mutation
    # of it: the child is given the allowlisted names it needs and the one
    # variable that keeps its output unbuffered, and nothing else.
    environment = _worker_environment()

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

    Nothing is printed for a worker that already relayed its output live (a
    true ``output_relayed``).  Otherwise each non-blank ``stdout`` line, then
    each ``stderr`` line, becomes one parent record tagged ``[shard N]`` and
    rendered by :func:`~app.logging_config.render_worker_line`: no child line
    is trusted as log text, so escapes and control characters are spelled out
    (a line cannot forge a second, untagged record, CWE-117), the line is
    bounded, and credential-shaped content is redacted -- **log-only**, since
    every artifact keeps the ``Examples`` credentials verbatim (AAP 0.8).

    The stream is a **floor**, not a ceiling -- ``INFO`` for ``stdout``,
    ``WARNING`` for ``stderr``, emitted at ``max(child_level, floor)`` -- so a
    child's ``LOG_ERROR:`` token is honoured on either stream and a ``DEBUG``
    token on ``stderr`` cannot pull a diagnostic below ``WARNING``.

    Args:
        plan: The shard whose output this is; only its ``index`` is read.
        process: The completed worker.  ``stdout`` and ``stderr`` are read
            defensively, the ``spawn`` seam admitting any object shaped like
            :class:`WorkerProcess`.

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

    A **positive** non-zero return code is normal and is *not* a dead worker:
    behave exits non-zero when scenarios fail, and treating that as an error
    would put a test outcome into the exit status, against
    ``testFailureIgnore=true`` (``pom.xml:25``) and the six ``-1`` publisher
    thresholds (``Jenkins:15``).  A shard is dead only when the launch raised,
    when a **signal** terminated it (a negative code on POSIX), or when
    :func:`_collect_shard_documents` finds its result file unusable.

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
        BaseException: An interrupt alone travels through untouched.
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

    The function the process pool executes: module-level, taking only
    picklable arguments, because ``forkserver`` and ``spawn`` re-import this
    module in the child and resolve the call by name -- no closure, bound
    method or injected seam crosses that boundary, hence the thread path in
    :func:`_run_shards_in_threads`.  Being new, the child installs this port's
    handler split over the inherited descriptors first (AAP 0.4.1).

    Args:
        plan: The shard to run; its ``output_path`` came from the parent, so
            the child writes where the parent will look.
        tags: The user's tag expression, or ``None``.
        browser: Browser override, or ``None``.
        dry_run: Whether to pass ``--dry-run``.
        cwd: The run base, which the worker subprocess runs in.
        verbose: Whether the parent's logger, unreadable here, was at ``DEBUG``.

    Returns:
        The shard's result, pickled back to the parent; a launch failure and a
        non-zero engine status are recorded, never raised.

    Raises:
        BaseException: Re-raised once this task's engine subprocess is stopped.
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
    :func:`run_suite` keeps them.  Each is asked to stop and then **waited
    for**: returning while a signalled process is still alive is what leaves a
    browser running after the run is over.

    **Every child is signalled before any is waited for or killed.**  A pool
    that notices one worker has died terminates the rest itself, and a repeat
    signal arriving in the middle of another worker's unwinding abandons it
    half-done, orphaning the engine process this function exists to reclaim.

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

    for child in children:
        try:
            child.terminate()
        except (OSError, ValueError) as error:
            # Never raised onward: this runs while an interrupt or a
            # supervision failure is being reported.
            logger.warning(
                "Could not stop worker process %s: %s", child.pid, error
            )

    deadline = time.monotonic() + grace_seconds
    for child in children:
        child.join(max(0.0, deadline - time.monotonic()))

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
    """Run the shards through a process pool -- the model AAP deviation 4 names.

    One :class:`~concurrent.futures.ProcessPoolExecutor` process per shard
    (bounded by :func:`_pool_process_count`) running :func:`_run_shard_task`,
    each supervising its own engine subprocess, on the platform's default
    start method.  **Nothing escapes but an interrupt**: pool construction,
    submission, a lost worker and a failed task each become a named dead
    shard, the results already collected kept, never an exit ``1``.  One
    caller requirement, :mod:`multiprocessing`'s own: the parent's
    ``__main__`` must be import-safe, since every child imports it.

    Args:
        plans: The shards, in shard order; two or more.
        tags: The user's tag expression, or ``None``.
        browser: Browser override, or ``None``.
        dry_run: Whether to pass ``--dry-run``.
        cwd: The run base, which each worker runs in.

    Returns:
        One result per shard, ordered by shard index.

    Raises:
        BaseException: Re-raised for an interrupt only, once the pool is shut
            down without waiting and its children are terminated and joined.
    """
    collected: dict[int, ShardResult] = {}
    preexisting = _child_process_pids()
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

    Which of three paths runs is fully determined by the inputs: a single
    shard is the ``--workers 1`` sequential mode (AAP 0.6) and builds no
    executor; an injected ``spawn`` seam runs threads here, a stub not being
    picklable across a process boundary; anything else -- every real run --
    uses the process pool AAP deviation 4 prescribes.  The unit is the **OS
    process** in all three, a Selenium session not being thread-shareable.

    Args:
        plans: The shards, in shard order.
        tags: The user's tag expression, or ``None``.
        browser: Browser override, or ``None``.
        dry_run: Whether to pass ``--dry-run``.
        base: Directory the run resolves against.
        spawn: The launch seam.

    Returns:
        One result per shard, ordered by shard index and not by completion.
        Every shard is accounted for, and no supervision failure is raised.

    Raises:
        BaseException: Re-raised for an interrupt only, after this run's
            children have been stopped and waited for.
    """
    cwd = _run_base(base)
    if len(plans) == 1:
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

    The resource limits :func:`load_result_set` enforces bound **one file**,
    and this function holds every live shard at once: ``documents`` is
    complete before :func:`_merge_documents` reads any of it, so 87 shards
    each just inside the 256 MiB per-file cap would have this process hold
    about 21.75 GiB with every individual file valid and the sum fatal.  The
    single :class:`RunResultBudget` built below is the run-wide counterpart
    that closes it -- charged each shard's bytes before that shard is parsed
    and its nodes as it is validated.  One budget covers one merge: it is
    constructed here rather than passed in or held at module scope, because
    what it bounds is one run's results, so each call gets its own and none
    of them accumulates across calls.

    A breach is **this shard's** death, not the run's.  The refusal arrives
    as the same :class:`ResultSetError` an oversized single file raises, so
    it becomes this shard's reason below with the breached limit's name in
    it, and the shards that did load are still merged, still written and
    still published under the exit contract's dead-worker row.  That is why
    the budget refuses a shard instead of raising out of the collection:
    failing the whole merge would discard results the run genuinely
    produced.

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
    # Defaults only: the run-wide caps live with the per-file ones in
    # ``app/reporting/events.py``, which is their single owner.
    budget = RunResultBudget()

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
                documents.append(load_result_set(path, budget=budget))
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
        :func:`select_scenarios` walked, because it is the same verified
        listing.  An empty tuple when the directory cannot be listed, which
        makes the reordering below a no-op rather than an error.
    """
    feature_paths, _ = _feature_files(base)
    return tuple(feature_paths)


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

    Between the selection and the sharding sits one further step, and it is
    the reason selection returns object identities at all: every feature whose
    contents were read is re-verified through
    :func:`_dropped_for_replacement` immediately before any worker command is
    built, and a feature that is no longer the file that was checked has its
    scenarios dropped and is named on :attr:`RunOutcome.parse_errors`.  The
    run executes the rest and still returns status ``0`` material, exactly as
    it does for a feature that fails to parse (AAP 0.4.1).

    This is the function that makes the suite actually execute, which the
    source build's configuration never did (see the module docstring).  It
    exits no process and raises nothing for a test outcome; everything it
    learns is reported through :class:`RunOutcome`, which ``app/cli.py`` turns
    into a status.

    Args:
        tags: Tag expression, or ``None`` for no filter.  ``app/cli.py``
            passes ``"@Smoke"`` by default and ``None`` under ``--rerun``.
        browser: Browser override forwarded as behave userdata, or ``None``.
        workers: Worker count; ``None`` is the CPU default, ``1`` sequential.
        dry_run: Whether to ask the engine not to execute steps.
        rerun: Take the scenarios from the rerun manifest.  A rerun applies
            **no** tag filter and writes **no** artifacts (``FailedTestRunner``
            declared neither), and a filter arriving anyway is ignored.
        base: Every path resolves against it; ``None`` means the cwd.
        spawn: **Test-only seam**, defaulting to the real subprocess launch.

    Returns:
        The outcome.  No test outcome becomes an exception or an exit status
        here; :attr:`RunOutcome.infrastructure_error` reports a per-worker
        directory this run could not create (nothing ran) or remove.

    Raises:
        TagExpressionError: If ``tags`` is malformed -- a usage error.
        BaseException: An interrupt, re-raised untouched once this run's
            children are stopped and its intermediates removed.
        SystemExit: What a ``SIGTERM`` becomes for the run's duration.
    """
    spawn_worker: SpawnCallable = _spawn_worker if spawn is None else spawn
    recorded_tags = _recorded_tag_expression(tags, rerun)

    if rerun:
        selected, problems, identities = _verified_rerun_selection(base=base)
    else:
        selected, problems, identities = _verified_selection(tags=tags, base=base)

    # The hand-off check, for both selection paths.  Every feature whose
    # contents were read is re-opened under the same no-follow verification
    # and its object identity compared with the one recorded a moment ago; a
    # feature that is no longer the file that was checked has its scenarios
    # dropped and is named, rather than being executed from whatever now
    # stands at its path (CWE-367).
    #
    # This is as late as the structure allows: everything below derives from
    # the surviving selection -- the selected count, the worker count, the
    # shard plans, the exactly-once assignment and the canonical order the
    # merge relies on -- so a drop after any of them would either leave an
    # empty shard or make a plan disagree with what was selected.
    # :func:`_dropped_for_replacement` documents where the residual window
    # between this check and the worker's own open of the same name lies, and
    # why closing it entirely would break the path spelling AAP deviation 1
    # pins into every artifact.
    selected, replaced = _dropped_for_replacement(selected, identities, base)
    problems.extend(replaced)

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
    # emits one record per reason, **before whichever exit class it settles
    # on**, so the incident and its consequence read as one account instead of
    # appearing twice under two logger names.  That the emission precedes
    # every one of that file's precedence returns is what keeps the shard
    # identities visible on the path where a dead shard coincides with a
    # higher-precedence artifact failure - an all-workers-dead run, whose
    # merge therefore produces nothing, being the case that matters.
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
        # This None is NOT the empty-merge condition - see RunOutcome.
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
