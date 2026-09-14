"""The ``run-tests`` command - this port's single execution entry point.

It replaces two Java runner classes whose behaviour lived entirely in
annotations: ``CukesRunner.java:7-20`` (four plugin destinations, a glue
package, ``dryRun = false``, ``tags = "@Smoke"``, an empty class body) and
``FailedTestRunner.java:8-12``, whose two load-bearing *absences* - no
``plugin`` list and no ``tags`` attribute - are what ``--rerun`` reproduces.
This module owns only the **option surface** and the **exit contract**;
execution belongs to ``app/services/test_run_service.py``, artifact writing to
``app/services/report_service.py``, and every path to :mod:`app.utils.paths`,
which is why no path literal appears here and why the ``--clean`` step reaches
the build output directory through :func:`~app.utils.paths.target_root` alone.

The six options, their defaults, and what a caller cannot read off them:

* ``--tags EXPR`` defaults to :data:`DEFAULT_TAG_EXPRESSION`
  (``CukesRunner.java:18``); it is validated here and evaluated by the run
  service.
* ``--browser NAME`` has no literal default - left unset, the ``browser``
  property decides.  A given value reaches every worker as engine userdata,
  which ``app/config.py`` resolves ahead of the properties file, and is passed
  on unvalidated, so an unrecognised value fails at first driver use exactly
  as ``Driver.java:29-42``'s missing default branch makes it.
* ``--workers N`` defaults to the CPU count; ``--workers 1`` is sequential.
* ``--dry-run`` and ``--rerun`` are both off by default.  ``--rerun`` is four
  coupled behaviours, each from an absence in ``FailedTestRunner.java:9-12``:
  it clears the tag filter, rejects ``--tags`` as a usage error, writes no
  artifact and leaves the existing ones untouched, and never cleans.
* ``--clean/--no-clean`` defaults to ``--clean``, standing in for ``mvn clean
  test``, and is ignored under ``--rerun``, which must not delete the manifest
  it reads.

What this file owns, and what it does not
-----------------------------------------
It owns exactly two things - the **option surface** and the **exit contract**.
It owns no execution logic, which belongs to
``app/services/test_run_service.py``, and no artifact writing, which belongs
to ``app/services/report_service.py`` and the four writers behind it.  It also
owns no path: **not one path literal appears in this module**, because
:mod:`app.utils.paths` is the port's sole owner of every path, and the build
output directory reaches the ``--clean`` step below through
:func:`~app.utils.paths.target_root` alone.

The option surface: six options, and no seventh
-----------------------------------------------
======================  ========================  ============================
Option                  Default                   Source
======================  ========================  ============================
``--tags EXPR``         ``@Smoke``                ``CukesRunner.java:18``
``--browser NAME``      the ``browser`` property  ``Driver.java:27``
``--workers N``         the CPU count             ``pom.xml:22-23``
``--dry-run``           off                       ``CukesRunner.java:17``
``--rerun``             off                       ``FailedTestRunner.java:11``
``--clean/--no-clean``  ``--clean``               ``mvn clean test``
======================  ========================  ============================

Deliberately absent, each for a stated reason: no ``--features`` override,
because the discovery root is the one ``behave.ini`` declares; no
``--format`` or ``--out``, because a per-worker output path cannot come from a
shared option and is built from :mod:`app.utils.paths` per shard; no
log-level option, because ``app/logging_config.py`` publishes one console
contract and the configuration surface is fixed at six keys; and **no
``generate-reports`` command**, because the source's only documented route to
a report was to re-run the suite with a chosen plugin, so rebuilding reports
from stored results would be an unrequested addition with a durable-state
contract of its own.

Two details that are easy to get subtly wrong:

* ``--browser`` has **no literal default**.  Unset means "use the ``browser``
  property", which ``app/config.py`` resolves with userdata ahead of the
  properties file, so the option stays ``None`` here and reaches a worker as
  behave userdata only when it was actually given.  The value is **not
  validated**: the Java driver switches on ``browser`` with no default branch,
  so an unrecognised value must fail at first driver use, exactly as today.
* ``--rerun`` is **four coupled behaviours**, not a flag, each tracing to a
  specific absence in ``FailedTestRunner.java:9-12``.  It clears the tag
  filter - leaving ``@Smoke`` applied would silently skip every failure from
  the five features that carry no feature-level tag, which is precisely what a
  rerun exists to re-execute; it rejects ``--tags`` as a usage error; it writes
  **no** artifact and leaves the existing ones untouched; and it **never
  cleans**, even when ``--clean`` is passed explicitly, because deleting the
  manifest it is about to read would make the command self-defeating.

The exit contract
-----------------
The source ignores every test-execution outcome and this command preserves
that.  ``pom.xml:25`` sets ``testFailureIgnore=true`` and all six Jenkins
publisher thresholds are ``-1`` (``Jenkins:15``), so **a test outcome never
reaches the exit status** - not one failing scenario, not a whole failing
suite.  Only errors that occur *before or outside* execution, and failures of
this port's own artifact production, are non-zero.

=====================================================  ======  ==============
Situation                                              Status  Artifacts
=====================================================  ======  ==============
Scenario failures or errors, undefined or skipped      ``0``   all four, from
steps, a browser that fails to start, an unknown               whatever ran
``browser`` value, a feature that fails to parse, a
missing or malformed rerun manifest
Zero scenarios selected by the tag expression          ``0``   all four, empty
Unknown or conflicting option, incl.                   ``2``   none
``--rerun --tags``
A worker process that dies                             ``3``   all four, from
                                                               completed shards
Artifact production, or the storage it needs, fails:  ``4``   per cause, below
the merge produces no result set at all, a writer
fails after earlier writers succeeded, the
``--clean`` step cannot empty the build output, the
run's per-worker intermediate directory cannot be
prepared or removed, or another run in this checkout
already holds the build output
=====================================================  ======  ==============

The concrete values are this file's contract - see :class:`ExitCode` - and
``1`` is deliberately never used, so a non-zero status always names its class.
The table has exactly **three** non-zero classes because AAP deviation 15
defines exactly three the source lacks - a usage error, a dead worker, and a
merge *or* writer failure - and a fourth status would publish a class nothing
has agreed to.  One status does not mean one diagnostic: each cause of ``4``
keeps its own stderr message, its own log line and its own artifact behaviour,
which is what a CI log needs and is stated per cause in :class:`ExitCode`.

Three consequences worth stating outright, because all three look like
optimisations worth "fixing" and none is:

* **A zero-scenario selection still writes all four artifacts, empty.**  The
  publisher's ``fileIncludePattern`` is narrowed to the single JSON report, so
  that file must exist for the third pipeline stage to have any input at all.
* **A writer failure rolls nothing back.**  Artifacts written before it stay
  exactly where their writers put them, and the failing writer is named on
  stderr.
* **A failed ``--clean`` step runs nothing at all.**  Execution does not
  proceed over build output whose state is unknown: obsolete report pages and
  stale worker data would otherwise survive the command and be published, so
  the suite is not started and no writer is reached.

Precedence, when several signals coexist, is this file's decision, since the
run service records every signal independently and encodes no order.  It is
the artifact-and-infrastructure class ``4`` and then the execution-facing
``3``: ``4`` describes what the publisher will find, or that the build output
could not be put into a known state at all, and that outranks a run that
merely lost a shard.

Every signal is **reported before** a status is chosen, which is what makes
that ordering cost no information.  The case that proves it is the one where
both signals are at their strongest: when every worker dies there is nothing
to merge, so the run reports the empty merge at ``4`` - and the shard
identities, their locations and the reason each one produced nothing are the
only record of what was lost.  Those are emitted by
:func:`_report_dead_shards` ahead of every precedence return, and the status
functions add nothing but the line naming the class they return.

Streams
-------
:func:`~app.logging_config.configure_logging` is called first, before any
work.  It routes ``INFO`` and below to stdout and ``WARNING`` and above to
stderr, and line-buffers both, which is what keeps a Jenkins log live while a
run is in progress - Python would otherwise block-buffer stdout through a pipe
and a slow run would look silent.  Progress goes to stdout; engine
diagnostics, tolerated selection problems, dead-shard reports and writer
failures go to stderr.  Nothing is accumulated and printed at the end.

How this command is reached
---------------------------
Two ways, and only the first is supported:

1. The ``run-tests`` console script declared under ``[project.scripts]`` in
   ``pyproject.toml``, pointing at :data:`run_tests` and invoked from the
   virtual environment's ``bin``/``Scripts`` directory.  ``scripts/run_tests.sh``,
   ``scripts/run_tests.ps1``, the ``Makefile`` and the README all use it.
2. The Flask application's CLI group, to which ``create_app()`` attaches this
   same command object because ``app/__init__.py`` is the port's sole
   registration point.  That path exists to satisfy the registration
   invariant; **nothing invokes the suite through the Flask CLI.**

Both reach the *same* command object: it is defined once here and referenced,
never redefined.  Importing this module builds no Flask application, reads no
``configuration.properties``, creates and touches no build output, configures
no logging and starts no browser - every one of those happens inside the
command function.

The command object is a :class:`_RunTestsCommand`, which exists for exactly
one reason: removal of the per-worker intermediate directory has to happen on
**every** exit path, and three of them - an unknown option, a malformed
``--tags`` expression and a non-positive ``--workers`` - are rejected by
Click's parser before the command function is entered, so a ``finally`` inside
that function could never reach them.  The subclass wraps
:meth:`click.Command.main`, which is the frame the console script's
``__call__`` and ``CliRunner.invoke`` both reach, and that wrapper holds this
module's **one and only** cleanup implementation, :func:`_remove_intermediates`,
reached from that class's two boundary methods and from nowhere else in this
file.  There are two because there are two ways this command is parsed:
``main`` is the console entry point's frame, and ``make_context`` is the frame
*both* routes share - the Flask CLI group builds the child command's context
and invokes it directly, never calling the child's ``main``.  Cleanup being
idempotent is what makes covering both harmless.

Three properties of that cleanup, because each is load-bearing and none is
obvious.  It removes **what this invocation created**, which is the guarantee
the exit contract states.  It additionally reclaims the intermediates of
**abandoned** runs - a previous process that crashed or was killed outright -
while leaving alone the directories of runs that are still in progress,
because deleting a live sibling's working files is exactly how live results
become missing worker files.  And a cleanup that **fails** is not survivable:
it upgrades a success to ``4``, because this run's per-worker documents would
otherwise be left in a workspace the publisher reads while the command reports
that everything went well.  It upgrades nothing else - a usage error's ``2``
and a dead worker's ``3`` outrank a tidy-up and keep their own class.

The same distinction governs ``--clean``: it empties the build output
directory but hands the intermediate directory to the run service rather than
deleting it outright, because only the service can tell an abandoned run's
leftovers from a running one's working files.  A retained live directory is
reported and is not a failed clean, and so is the run lock described next.

One run at a time in one checkout
---------------------------------
The clean step, the run and the publication all operate on state the whole
checkout shares - the four artifact paths, and the intermediate directory
underneath them - so this command takes one claim on the build output before
the clean and gives it back after the publication
(:func:`~app.services.acquire_run_lock`).  Two runs interleaved across those
phases would empty each other's output and leave a workspace holding a mixture
of both runs' reports, scenario data and screenshots, with nothing in either
artifact to say so.  A second run in the same checkout is therefore **refused**
rather than queued, at class ``4``, having executed nothing and touched
nothing: a run that holds the lock may be driving a browser suite for many
minutes, and a CI stage that waited that out would hang instead.  Runs in
separate checkouts share nothing and never contend.

The claim travels with the merged document into the fan-out, which verifies it
before each writer publishes, so a run that has somehow lost it stops instead
of writing into a workspace another run has taken over.  The four artifacts
are still published one writer at a time and are **not** promoted as an
all-or-nothing set: the writer-failure row above requires the artifacts
written before a failure to remain.

Import boundary
---------------
``click``, the tag-expression grammar, and three of this port's own modules:
``app.services`` for the two services, ``app.utils.paths`` for the build
output directory and ``app.logging_config`` for the console contract.  From
the standard library: ``logging``, ``enum``, ``typing``, ``pathlib``, and
``os``, ``stat`` and ``shutil``, the last three for the clean step, which
needs no-follow inspection and descriptor-relative removal and cannot be
written with :class:`~pathlib.Path` alone.
``cucumber_tag_expressions`` is a pinned runtime dependency, imported for one
purpose only - rejecting a malformed ``--tags`` value at parse time, which is
option validation and therefore this file's job.  Nothing else: no
``selenium``, no ``app.pages``, no ``app.automation``, no ``app.web`` and no
``app.config`` - the browser name is passed through untouched and resolved
where the driver reads it.

What ``tests/test_cli.py`` must cover
-------------------------------------
Every item below is required, using Click's ``CliRunner`` with the two streams
captured separately:

1. **Defaults** - no options resolves ``--tags`` to ``@Smoke``, ``--workers``
   to the CPU count, ``--dry-run`` off, ``--rerun`` off, ``--clean`` on, and
   ``--browser`` to *unset* rather than ``"chrome"``.
2. **Each option individually** - each of the six is accepted, appears in
   ``--help`` with its default, and reaches the service layer with the right
   value (patch :func:`~app.services.run_suite` and assert the call keywords).
   The two forwarded *values* carry a second requirement each, and the two
   requirements are **opposite**, which is what has to be pinned rather than
   left to be inferred.  For ``--browser``, a value containing a newline or a
   terminal escape sequence is **accepted** and reaches the service
   byte-for-byte, because ``Driver.java``'s switch has no default branch and
   an unrecognised browser must fail at first driver use.  For ``--tags`` the
   same value is a **usage error**: :func:`_validate_tag_expression` refuses
   any character that is neither printable nor a plain space, and refuses an
   expression longer than :data:`TAG_EXPRESSION_LENGTH_LIMIT`, so exit ``2``
   with the service never called, nothing written, and the reason on **one**
   physical line naming the offending character's index or the two lengths -
   never echoing the text.  An accepted ``--tags`` expression still reaches
   the service byte-for-byte, unstripped and unnormalised.  Whatever is
   accepted, the "Starting the suite" record shows it quoted, bounded and on
   one physical line, because both values are rendered for the record by
   :func:`~app.logging_config.render_option_value` and by nothing else, and no
   second record may appear whatever the value contains.
3. **``--rerun --tags``** - exit ``2``, the service never called, no artifact
   written.
4. **``--rerun`` semantics** - the tag filter reaching the service is
   *cleared* rather than ``@Smoke``; no writer is invoked; pre-existing
   artifacts are byte-unchanged; and **no clean occurs** even with ``--clean``
   passed explicitly alongside it.
5. **Exit ``0`` on every execution outcome** - failing scenarios, an undefined
   step, a skipped step, a browser that fails to start, an unknown ``browser``
   value, a feature that fails to parse, and a missing or malformed rerun
   manifest: exit ``0`` every time, with the parse and manifest cases
   producing stderr output.
6. **Zero scenarios selected** - exit ``0``, all four artifacts written, and
   the JSON report holding an empty **list**.
7. **A dead worker** - exit ``3``, all four artifacts written from the shards
   that completed, and the incomplete shard named on stderr.
8. **An empty merge** - exit ``4``, no artifact written, the build output
   directory left exactly as the clean step left it, and the stderr message
   naming the merge rather than a writer.
9. **A writer failure** - exit ``4`` as well, *and distinguishable from the
   row above by its diagnostics and by what survives*: the artifacts written
   before it still present and unmodified, the failing writer *and the
   destination it was writing* named on stderr, and nothing deleted.  The
   destination comes from :attr:`~app.services.ReportOutcome.failed_path`, so
   the assertion is that the record carries what the outcome carried - this
   file resolves no artifact path of its own.  The two rows share a status and
   share nothing else.
10. **Intermediate cleanup is unconditional, and its failure is not
    survivable** - after every scenario above, after a simulated
    ``KeyboardInterrupt``, and after each usage error that Click rejects while
    *parsing* - an unknown option, a malformed ``--tags`` expression, a
    non-positive ``--workers`` - no directory this invocation created
    survives, and an **abandoned** run's directory is reclaimed while a
    **live** run's is left in place.  The parse-time cases are reached
    through :class:`_RunTestsCommand`, so they must be invoked through the
    command object's ``main`` for the console route and through the Flask CLI
    group for the grouped route, which is the one that never calls ``main``.
    Three more assertions belong here: a run whose intermediate
    directory cannot be **removed** still writes its four artifacts and then
    exits ``4``; a run whose directory cannot be **prepared** exits ``4``
    having executed nothing, ``--rerun`` included, which is the case a rerun's
    "writes no artifact by design" would otherwise disguise as success; and a
    cleanup failure alongside a usage error or a dead worker leaves those
    statuses untouched.
11. **``--clean`` behaviour** - with stale files in the build output
    directory, ``--clean`` empties it before the run, ``--no-clean`` leaves
    them, and under ``--rerun`` neither cleans.  Its failure modes are part
    of the exit contract and each is asserted: a build output directory that
    is a **symlink** is refused, exit ``4``, with the link's target
    untouched and :func:`~app.services.run_suite` never called; the same for
    a non-directory occupying the path and for an entry that cannot be
    removed; and a clean that succeeds leaves the directory observably empty
    before the run starts.  Two more cases, both about the intermediate
    directory it does not delete itself: a **live** run's directory survives
    a concurrent invocation's clean and that clean still succeeds, and an
    **abandoned** one is reclaimed by it.  The staggered form is the one
    worth writing - a second invocation cleaning while the first is mid-run,
    asserting the first finishes with no dead shard.
12. **Stream routing** - progress on stdout, diagnostics on stderr, with no
    cross-contamination.
13. **No path literal** - the module's source contains none, anywhere: no
    build output directory name, no artifact filename, no per-worker
    directory name and no Gherkin discovery root, and not even inside a
    docstring, where it would be the first step of the drift
    :mod:`app.utils.paths` exists to prevent.  Every path in this module
    comes from that module.
14. **Import purity** - importing this module creates no Flask application,
    reads no ``configuration.properties`` and neither creates nor touches the
    build output directory.
15. **Entry-point wiring** - after an editable install, ``run-tests --help``
    from the environment's ``bin``/``Scripts`` succeeds and prints the six
    options; and ``create_app()`` exposes this same command object on the
    Flask CLI group.
16. **One record per incident** - with both services *real* rather than
    stubbed, each incident appears in the log exactly once.  Capture the
    records from the ``app`` logger over a whole command and count them: a
    tolerated selection problem, a dead shard's reason, the skipped-writer
    list and the retained-artifact list each appear once and only from this
    module's logger, and the writer-failure *cause with its traceback*
    appears once and only from ``app.services.report_service``.  No text is
    both emitted by a service and repeated here; asserting it needs the real
    services, because a stub that logs nothing makes any allocation look
    correct.
17. **Coverage** - this module sits outside the four gated ``Makefile``
    scopes, so no numeric gate applies, but every row of the exit table above
    must be exercised.
"""

import logging
import os
import shutil
import stat
from collections.abc import Sequence
from enum import IntEnum
from pathlib import Path
from typing import Any, Final

import click

# The same grammar the JVM used (``tag-expressions:4.1.0`` there,
# ``cucumber-tag-expressions`` 11.0.1 here), imported for validation only:
# a malformed expression is an invalid *option value*, so it is rejected at
# parse time by this file rather than surfacing as a traceback out of the run
# service, which documents ``TagExpressionError`` as its one propagating
# exception.  The expression itself is never evaluated here - selection is the
# service's work.
from cucumber_tag_expressions import TagExpressionError, TagExpressionParser

# ``configure_logging`` installs the stream split this module's whole account
# depends on; ``render_option_value``, ``render_path`` and ``sanitize_log_text``
# are the other half of the same contract - ``app/logging_config.py`` owns what
# is allowed *onto* the console, so the two option values this command echoes
# back, every artifact path it names and the one third-party message it
# reflects (a tag-expression parser error, which Click prints itself while
# logging is not yet configured) are all rendered there rather than here.
# ``sanitize_log_text`` and the bound it publishes are that same ownership
# applied to the third kind of text this command did not author: the problems
# a run tolerated, which arrive on the outcome from the engine, the readers and
# the workers and are rendered by that one implementation rather than by a
# second one written here.
from app.logging_config import (
    RELAYED_LINE_LIMIT,
    configure_logging,
    render_option_value,
    render_path,
    sanitize_log_text,
)
from app.services import (
    ReportOutcome,
    RunLock,
    RunOutcome,
    acquire_run_lock,
    cleanup_workers_dir,
    default_worker_count,
    delete_verified_entry,
    generate_reports,
    reclaim_workers_root,
    run_suite,
)

# The two paths this module knows about, both reached through their accessor
# rather than assembled, because ``app/utils/paths.py`` owns every path in the
# port and a second spelling of either is exactly how the ``--clean`` step, the
# writers and the artifact route would drift apart.  ``workers_dir`` is used
# for its *name* alone: the clean step has to recognise the one entry of the
# build output it must not delete outright, and the run service owns what
# happens inside it.
from app.utils.paths import target_root, workers_dir

__all__ = [
    "COMMAND_NAME",
    "DEFAULT_TAG_EXPRESSION",
    "TAG_EXPRESSION_LENGTH_LIMIT",
    "ExitCode",
    "run_tests",
]

logger = logging.getLogger(__name__)


COMMAND_NAME: Final[str] = "run-tests"

#: The default tag filter, from ``CukesRunner.java:18``.  It is declared once
#: in the suite, at ``Crm.feature:1``, so a default run selects the CRM feature
#: alone - behaviour to reproduce, not a defect to correct.  ``behave.ini``
#: carries the same value as ``default_tags``; this option always passes an
#: explicit expression, so that default can never apply implicitly and can be
#: cleared outright under ``--rerun``.
DEFAULT_TAG_EXPRESSION: Final[str] = "@Smoke"

#: The longest ``--tags`` expression this command accepts, in characters.
#: Generous next to anything the suite can express - its tags are of the form
#: ``@Smoke`` and ``@UPGN-286``, and the whole suite declares nine of them, so
#: an expression naming every tag with an operator between each pair is an
#: order of magnitude short of this - and firm, because the grammar itself
#: imposes no bound at all: a single expression of hundreds of kilobytes
#: parses in a fraction of a second, is then copied into the "Starting the
#: suite" record and into every worker's command line, and is retained for the
#: whole run.  A caller who needs a wider selection than this expresses names
#: fewer tags, not more characters.
TAG_EXPRESSION_LENGTH_LIMIT: Final[int] = 1024


class ExitCode(IntEnum):
    """The command's exit statuses - the whole published set.

    Three non-zero members, because AAP deviation 15 licenses exactly three
    classes the source lacks.  ``1`` is absent deliberately: it is what an
    uncaught exception or an interrupt produces.

    Attributes:
        SUCCESS: The run completed.  **Every test outcome lands here** -
            failures, errors, undefined or skipped steps, a browser that will
            not start, an unrecognised ``browser`` value, a feature that fails
            to parse, a missing or malformed rerun manifest, and a tag
            expression that selected nothing (``pom.xml:25``, ``Jenkins:15``).
        USAGE_ERROR: An unknown or conflicting option, including
            ``--rerun --tags``, a ``--tags`` expression that is malformed,
            over-long or carries a non-printable character, and a
            non-positive ``--workers``.  Click's own convention for a
            :exc:`click.UsageError`, so the two agree.  Nothing is executed
            and no artifact is written.
        WORKER_DIED: A worker process produced no results.  The artifacts are
            still written from the shards that completed, and every incomplete
            shard is named on stderr.
        ARTIFACT_FAILURE: One class, four causes, each keeping its own
            diagnostics: the merge produced no result set (nothing written), a
            writer failed (earlier artifacts retained, nothing rolled back),
            the ``--clean`` step could not empty the build output (nothing
            executed), or this run's per-worker intermediate directory could
            not be prepared or removed.
    """

    SUCCESS = 0
    USAGE_ERROR = 2
    WORKER_DIED = 3
    ARTIFACT_FAILURE = 4


# --------------------------------------------------------------------------- #
# Option validation
#
# Both helpers below run while Click is still parsing, so a rejection happens
# before logging is configured, before the clean step and before the run
# service is reached - which is what the usage-error row of the exit contract
# requires: "nothing executed", and nothing written or removed either.
#
# That "before logging is configured" is also why the ``--tags`` callback does
# its own rendering.  Click prints a rejected option's reason itself, on
# stderr, through no handler of this port's, so the sanitizing formatter
# ``app/logging_config.py`` installs on every handler does not cover a single
# character of it: whatever the callback puts in a ``click.BadParameter``
# message reaches the console exactly as written.  A tag-expression parser
# error is not this port's text - it is a third-party message that quotes the
# user's own expression back and spans *three physical lines* while doing it -
# so it is rendered through :func:`~app.logging_config.sanitize_log_text`
# before it is handed to Click, and the value is bounded and checked for
# printability before it is ever parsed.
# --------------------------------------------------------------------------- #

#: How much of a tag-expression parser error is reflected back, in characters.
#: The grammar's own diagnostics quote the whole expression twice - once in
#: prose and once under a caret marker - so an accepted-length expression can
#: produce a message of some kilobytes, and the reason for a rejected option
#: has to stay short enough to read next to the usage text Click prints under
#: it.  The bound is applied by :func:`~app.logging_config.sanitize_log_text`,
#: which appends a notice naming how many characters it dropped, so a
#: truncated reason is never mistaken for the whole one.
_TAG_ERROR_MESSAGE_LIMIT: Final[int] = 200


def _validate_tag_expression(
    ctx: click.Context,
    param: click.Parameter,
    value: str,
) -> str:
    """Bound, screen and parse a ``--tags`` expression, or reject it.

    The full grammar is supported - ``@Smoke``, ``@Login and not @wip``,
    ``@UPGN-286 or @UPGN-287`` - and the parsed form is deliberately
    discarded: evaluating it against the suite is the run service's work, and
    parsing it a second time there is cheap next to the alternative, which is
    a malformed value surfacing as a traceback in the middle of a run.

    Three checks, in this order, and the order is the point:

    1. **Length**, against :data:`TAG_EXPRESSION_LENGTH_LIMIT`, first - so
       nothing downstream examines, copies, logs or forwards an expression of
       arbitrary size.  The grammar accepts one of hundreds of kilobytes
       without complaint, and such a value would then be held for the whole
       run, echoed in a record and repeated on every worker's command line.
       The rejection states the limit and the actual length as numbers and
       quotes none of the text.
    2. **Printability**, character by character - every C0 control including
       ``TAB``, ``CR`` and ``LF``, ``DEL``, the C1 range and the Unicode line
       and paragraph separators ``U+2028``/``U+2029`` are refused, and a plain
       space is the one non-printable-adjacent character allowed because the
       grammar's own separator is a space.  This removes nothing that ever
       worked: a tag is word-shaped, and an expression carrying a newline or a
       tab already fails to parse.  It does remove two live problems - a
       terminal escape sequence, which the grammar *accepts* inside a tag and
       which would then travel into a log record and a worker command line,
       and any control character reaching the console through Click's own
       unsanitized error path.  The rejection names the offending character by
       its index and its escape spelling (``repr`` of the single character,
       which is printable ASCII by construction) and never echoes the
       expression, so the reason cannot carry the offence it reports.
    3. **The grammar**, last.  Its error message is a third party's text
       spanning several lines and quoting the expression back, so it is
       rendered through :func:`~app.logging_config.sanitize_log_text` with the
       :data:`_TAG_ERROR_MESSAGE_LIMIT` bound: one physical line, no control
       character, no escape sequence, and a notice when anything was dropped.

    Every rejection is a :exc:`click.BadParameter`, so all three share the one
    outcome the exit contract defines for a bad option value -
    :attr:`ExitCode.USAGE_ERROR`, nothing executed, nothing written and
    nothing removed.  ``--browser`` is deliberately *not* screened this way:
    ``Driver.java``'s switch has no default branch, so an unrecognised browser
    must reach the driver and fail at first use, and that asymmetry between
    the two forwarded option values is required rather than incidental.

    Args:
        ctx: The Click context, supplied by the parser.  Unused, and named
            because Click calls a callback positionally.
        param: The parameter being processed, used to name it in the error.
        value: The expression as the user wrote it, or the declared default
            when the option was not given.  A callback runs for a default too,
            which is intentional: it keeps the shipped default honest, and
            :data:`DEFAULT_TAG_EXPRESSION` passes all three checks.

    Returns:
        ``value`` unchanged - not stripped, not normalised, not rewritten - so
        an accepted expression reaches the service exactly as the user wrote
        it.

    Raises:
        click.BadParameter: If the expression is longer than
            :data:`TAG_EXPRESSION_LENGTH_LIMIT`, contains a character that is
            neither printable nor a plain space, or cannot be parsed.  Click
            turns it into :attr:`ExitCode.USAGE_ERROR` and prints the reason
            on stderr, on one physical line.
    """
    del ctx  # The callback signature is Click's; the context is not needed.
    length = len(value)
    if length > TAG_EXPRESSION_LENGTH_LIMIT:
        raise click.BadParameter(
            f"the expression is {length} characters long, and at most "
            f"{TAG_EXPRESSION_LENGTH_LIMIT} are accepted",
            param=param,
        )
    for index, character in enumerate(value):
        if character != " " and not character.isprintable():
            raise click.BadParameter(
                f"the expression contains the non-printable character "
                f"{character!r} at index {index}; a tag expression is made of "
                f"printable characters separated by spaces",
                param=param,
            )
    try:
        TagExpressionParser.parse(value)
    except TagExpressionError as error:
        raise click.BadParameter(
            sanitize_log_text(str(error), limit=_TAG_ERROR_MESSAGE_LIMIT),
            param=param,
        ) from error
    return value


def _option_given(ctx: click.Context, name: str) -> bool:
    """Return whether an option's value came from the caller.

    Distinguishing "given" from "defaulted" is what makes the
    ``--rerun``/``--tags`` conflict detectable while ``--tags`` keeps a
    visible default: comparing the value against
    :data:`DEFAULT_TAG_EXPRESSION` instead would silently accept
    ``--rerun --tags @Smoke``, which is the same conflicting request spelled
    out in full.

    Args:
        ctx: The active Click context.
        name: The parameter's Python name, e.g. ``"tags"``.

    Returns:
        ``True`` when the value came from the command line, an environment
        variable, a default map or a prompt; ``False`` when it is the
        parameter's own declared default.
    """
    return ctx.get_parameter_source(name) is not click.ParameterSource.DEFAULT


# The clean step reproduces ``mvn clean test`` (``Jenkins:8``,
# ``Jenkins:10``), which emptied the build output before every run.  It is the
# only code in this port that deletes anything the user did not name, so it is
# written against both of its hostile directions at once:
#
# * **Following a link out of the checkout.**  A build output directory that is
#   a symlink, or an entry inside it that is one, must never be traversed while
#   deleting; a plain ``is_dir()`` test is true for a symlink to a directory,
#   which is how a recursive delete escapes the repository (CWE-59).
# * **Following a *reparse point* out of the checkout**, which is the same
#   escape by a route a link test does not see.  A Windows junction is reported
#   by ``os.lstat`` with the directory bit set and the link bit clear, and with
#   its own device and inode, so it satisfies every identity check while
#   ``iterdir`` and ``shutil.rmtree`` walk straight through it - and
#   ``Path.resolve()`` resolves *through* it too, so a containment test made
#   against the resolved root would find the external tree's own children
#   "inside" it and delete them (CWE-22, and CWE-367 for the swap that installs
#   one mid-operation).  Every indirection is therefore refused by one test,
#   :func:`_indirection_problem`, applied to the root, to every entry, and
#   again at each re-check.
# * **Carrying on over output whose state is unknown.**  A clean that could not
#   finish leaves obsolete report pages and stale worker intermediates where
#   the run's own writers may not overwrite them and the publisher will still
#   read them, so it is an artifact-infrastructure failure and not a cosmetic
#   one - see :attr:`ExitCode.ARTIFACT_FAILURE`.

#: Whether this platform can enumerate and delete *relative to an open
#: directory descriptor*, which is what removes the window between checking
#: what the build output directory is and acting on it.  All four capabilities
#: are required together - the flag that refuses to open a symlink, the
#: descriptor-based scan, descriptor-relative unlinking, and a
#: :func:`shutil.rmtree` that walks with ``openat``-style calls rather than
#: paths (its own ``avoids_symlink_attacks`` flag is exactly the condition
#: under which it accepts ``dir_fd``).  POSIX supplies all four; Windows
#: supplies none, and AAP §0.8 makes Windows a supported platform, so the
#: path-based fallback below is a real code path rather than a formality.
_SUPPORTS_DESCRIPTOR_CLEANING: Final[bool] = (
    hasattr(os, "O_DIRECTORY")
    and os.scandir in os.supports_fd
    and os.unlink in os.supports_dir_fd
    and shutil.rmtree.avoids_symlink_attacks
)

_MAX_REPORTED_CLEAN_PROBLEMS: Final[int] = 3


def _indirection_problem(path: Path, info: os.stat_result) -> str | None:
    """Return why a path must not be emptied or traversed, or ``None``.

    One test for **every** kind of indirection a directory can be, because two
    of the three are indistinguishable from an ordinary directory to the tests
    that preceded this function:

    * A **symbolic link**, which :data:`stat.S_ISLNK` reports on both
      platforms.  Neither traversed nor unlinked: deleting a link a user
      deliberately placed is as presumptuous as deleting what it points at,
      and ``--no-clean`` is the escape hatch for a checkout laid out that way.
    * A **Windows junction**, which it does not.  ``os.lstat`` reports a
      junction as a directory, with the link bit clear and with its own device
      and inode, so it passes a link test and every identity check while
      ``iterdir`` and :func:`shutil.rmtree` resolve through it into whatever it
      points at.  It is recognised here through the two Windows-only fields
      :class:`os.stat_result` carries for reparse points, read with
      :func:`getattr` so that this is one code path on every platform rather
      than a Windows-only branch nothing else ever exercises.
    * Any **other reparse point** - a mounted volume, a cloud-storage or
      deduplication placeholder - refused for the same reason and stated as
      such: whatever it redirects to, a deletion that resolves a name cannot
      be bound to the object that was checked, so this step fails closed
      instead of deleting something it cannot identify.

    Args:
        path: The path being considered, named in the returned reason.
        info: Its :func:`os.lstat` result - never :func:`os.stat`, which
            resolves the very indirection this looks for.

    Returns:
        ``None`` when the path may be operated on by name, and otherwise a
        one-line reason naming what it is and that nothing was removed.  The
        caller turns a reason into :attr:`ExitCode.ARTIFACT_FAILURE`: a build
        output whose state cannot be established is an artifact-infrastructure
        failure, not a cosmetic one.
    """
    if stat.S_ISLNK(info.st_mode):
        return (
            f"the build output directory {path} is a symbolic link; emptying "
            "it would delete whatever it points at, which may lie anywhere "
            "outside this checkout, so it was not traversed and nothing was "
            "removed - pass --no-clean, or replace the link with a real "
            "directory"
        )
    attributes = getattr(info, "st_file_attributes", 0)
    tag = getattr(info, "st_reparse_tag", 0)
    if attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT or tag:
        return (
            f"the build output directory {path} is a reparse point (a "
            "junction or a mounted volume) rather than a plain directory; "
            "emptying it would recurse into whatever it redirects to, which "
            "may lie anywhere outside this checkout, so it was not traversed "
            "and nothing was removed - pass --no-clean, or replace it with a "
            "real directory"
        )
    return None


def _workers_dir_name() -> str:
    """Return the name of the intermediate directory inside the build output.

    Returns:
        The final component of :func:`~app.utils.paths.workers_dir` -- taken
        from that module rather than written out, so this module still holds
        no path of its own, and used only to recognise the one entry of the
        build output that the run service, not the clean step, empties.
    """
    return workers_dir().name


def _empty_build_output() -> str | None:
    """Empty the build output directory, reproducing ``mvn clean test``.

    The directory itself is kept and its contents removed, so an emptied
    directory is observably empty rather than absent - which is the state the
    exit contract refers to when it says the build output is "left as the
    clean step left it".  A missing directory is not created: there is nothing
    to clean, and the writers create their own parents.

    Three things are refused outright rather than worked around, because in
    each case emptying the path would destroy something that is not this
    command's build output:

    * **An indirection at the root** - a symlink, a Windows junction, or any
      other reparse point.  All three are identified with :func:`os.lstat`, so
      the entry itself is examined rather than its target, and all three are
      refused by :func:`_indirection_problem` rather than traversed,
      unlinked or walked.  ``--no-clean`` is the escape hatch for a checkout
      laid out that way.
    * **A non-directory at the root.**  A file occupying the path cannot be
      emptied, and the writers cannot write into it either.
    * **An entry whose removal fails, and an entry that survives the
      attempt.**  Both mean the directory is not empty, which is the one
      postcondition this function exists to establish.

    Where the platform allows it, everything after the initial check operates
    on an open directory descriptor whose identity is confirmed against that
    check, so the directory cannot be swapped for a symlink in between
    (a check-then-use race) and no deletion is ever resolved through a path a
    second time.  Where it does not - Windows - the root's identity is
    re-established before every removal and the step fails closed the moment
    it changes, and each entry is removed by being moved to a private name
    inside that verified root and re-identified there before anything
    recursive happens to it
    (:func:`~app.services.delete_verified_entry`), so a name substituted
    between the check and the removal is refused rather than followed.

    **One entry is not deleted here**: the per-worker intermediate directory
    is handed to :func:`~app.services.reclaim_workers_root`, which alone can
    tell an abandoned run's leftovers from a live run's results, and what it
    retains is reported rather than counted a failure.

    Returns:
        ``None`` when the directory is known to be empty afterwards, including
        when it never existed; otherwise a one-line reason, which the caller
        turns into :attr:`ExitCode.ARTIFACT_FAILURE` without starting the run.
    """
    root = target_root()

    try:
        # ``lstat`` and not ``exists()``/``is_dir()``: those follow a symlink
        # and answer about its target, which is precisely the question that
        # must not be asked here.
        root_status = os.lstat(root)
    except FileNotFoundError:
        logger.info("Nothing to clean: %s does not exist", root)
        return None
    except OSError as error:
        return f"cannot inspect the build output directory {root}: {error}"

    indirection = _indirection_problem(root, root_status)
    if indirection is not None:
        logger.error("Clean refused: %s", indirection)
        return indirection
    if not stat.S_ISDIR(root_status.st_mode):
        return (
            f"the build output directory {root} is not a directory, so it "
            "cannot be emptied and the report writers cannot write into it"
        )

    if _SUPPORTS_DESCRIPTOR_CLEANING:
        problems = _empty_through_descriptor(root, root_status)
    else:
        problems = _empty_through_paths(root, root_status)

    if problems:
        return _clean_failure_reason(root, problems)
    return None


def _clean_failure_reason(root: Path, problems: list[str]) -> str:
    """Combine the problems a clean hit into one reportable reason.

    Args:
        root: The build output directory, named so the reason is
            self-contained in a log where no other line mentions it.
        problems: Every problem encountered, in discovery order and each
            already logged individually.  Must not be empty - an empty list
            means the clean succeeded and no reason exists to report.

    Returns:
        A single line naming at most :data:`_MAX_REPORTED_CLEAN_PROBLEMS`
        problems and counting any remainder.
    """
    named = problems[:_MAX_REPORTED_CLEAN_PROBLEMS]
    remainder = len(problems) - len(named)
    summary = "; ".join(named)
    if remainder:
        summary = (
            f"{summary}; and {remainder} further problem(s), each already "
            "reported above"
        )
    return f"{root} could not be emptied: {summary}"


def _empty_through_descriptor(
    root: Path,
    expected: os.stat_result,
) -> list[str]:
    """Empty the build output directory through an open descriptor.

    This is the safe path, and the one every POSIX platform takes.  The
    descriptor is opened with ``O_NOFOLLOW``, so a symlink substituted for the
    directory fails the open instead of being followed, and its identity is
    compared against the caller's :func:`os.lstat` result, so a directory
    swapped for another directory between the two calls is detected rather
    than emptied.  Every subsequent operation names an entry relative to that
    descriptor, which means no name is resolved from the root a second time
    and a renamed or relinked parent cannot redirect a deletion.

    Args:
        root: The build output directory.  Used to open the descriptor and to
            name entries in diagnostics; never re-resolved for a deletion.
        expected: The ``lstat`` result the caller validated, whose device and
            inode the opened directory must match.

    Returns:
        Every problem encountered, in discovery order, and an empty list when
        the directory is verified empty.
    """
    try:
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as error:
        problem = f"cannot open {root} for cleaning: {error}"
        logger.error("Clean failed: %s", problem)
        return [problem]

    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (
            expected.st_dev,
            expected.st_ino,
        ):
            problem = (
                f"{root} was replaced between being checked and being opened "
                "(its device and inode changed), so nothing was removed"
            )
            logger.error("Clean failed: %s", problem)
            return [problem]

        problems, retained = _remove_descriptor_entries(root, descriptor)
        problems.extend(
            _verify_descriptor_empty(root, descriptor, retained=retained)
        )
    finally:
        os.close(descriptor)
    return problems


def _remove_descriptor_entries(
    root: Path, descriptor: int
) -> tuple[list[str], list[Path]]:
    """Remove every entry the open directory holds, following no symlink.

    A real subdirectory is removed with :func:`shutil.rmtree` relative to the
    descriptor, which walks it with descriptor-relative calls and therefore
    cannot be redirected mid-walk.  **Everything else is unlinked** - files,
    sockets, and symlinks whatever they point at - because
    ``is_dir(follow_symlinks=False)`` answers about the entry itself, so a
    symlink to a directory lands here and is removed as the link it is.

    Args:
        root: The build output directory, used only to name entries in
            diagnostics.
        descriptor: An open descriptor for ``root``, already identity-checked.

    Returns:
        A ``(problems, retained)`` pair: every problem encountered, in
        discovery order, and the live run directories the run service asked
        this step to leave in place.
    """
    try:
        with os.scandir(descriptor) as entries:
            # Materialised, and sorted for a reproducible log, before
            # anything is deleted: mutating a directory while iterating it is
            # unspecified, and the type test must happen before its entry can
            # be replaced.
            names = sorted(
                (entry.name, entry.is_dir(follow_symlinks=False))
                for entry in entries
            )
    except OSError as error:
        problem = f"cannot list the entries of {root}: {error}"
        logger.error("Clean failed: %s", problem)
        return [problem], []

    problems: list[str] = []
    removed = 0
    retained: list[Path] = []
    for name, is_directory in names:
        if is_directory and name == _workers_dir_name():
            # The one entry this step does not delete outright.  It holds the
            # per-worker intermediates of *every* run in this checkout, and a
            # second invocation's clean removing a first invocation's live
            # working files is precisely how live results become missing
            # worker files.  The run service owns that directory and is the
            # only thing that can tell an abandoned run from a running one,
            # so it reclaims what it can and reports what it kept.
            reason, live = reclaim_workers_root()
            retained.extend(live)
            if reason is not None:
                problems.append(reason)
            else:
                removed += 1
            continue
        try:
            if is_directory:
                shutil.rmtree(name, dir_fd=descriptor)
            else:
                os.unlink(name, dir_fd=descriptor)
        except FileNotFoundError:
            # Gone between the scan and the removal, which satisfies this
            # step's postcondition rather than violating it: the entry is not
            # in the directory.  It happens for real - a second ``run-tests``
            # in the same checkout cleans the same directory, and its own
            # intermediate directory disappears as its run finishes - and
            # counting it as a failure would abort a run whose build output is
            # demonstrably empty, which the verification below establishes
            # independently of who removed what.
            removed += 1
        except OSError as error:
            problem = f"could not remove {root / name}: {error}"
            logger.error("Clean failed: %s", problem)
            problems.append(problem)
        else:
            removed += 1

    logger.info("Cleaned %d item(s) from %s", removed, root)
    _note_retained(retained)
    return problems, retained


def _note_retained(retained: Sequence[Path]) -> None:
    """Report the live intermediates a clean deliberately left in place.

    Said out loud rather than passed over, because "the build output was
    emptied" is otherwise untrue: what is left belongs to a run that is still
    using it, and an operator reading a CI log is entitled to know why an
    entry survived a clean.

    Args:
        retained: The entries the run service kept, from
            :func:`~app.services.reclaim_workers_root` - a live run's
            directory, and this run's own lock file, which that function never
            deletes because it is what makes one run exclusive.  Empty only
            when the intermediate directory held nothing at all.
    """
    if not retained:
        return
    logger.info(
        "Retained %d entr(y/ies) still in use by a run: %s",
        len(retained),
        ", ".join(str(directory) for directory in retained),
    )


def _verify_descriptor_empty(
    root: Path, descriptor: int, *, retained: Sequence[Path]
) -> list[str]:
    """Confirm the open directory is empty, through the same descriptor.

    The removals above report what they could not do; this reports what is
    still there, which is the postcondition the exit contract needs and is not
    the same statement - a deletion that reported success and left the entry
    behind, or an entry a concurrent writer created during the clean, is only
    visible from here.

    Args:
        root: The build output directory, used to name survivors.
        descriptor: An open descriptor for ``root``.  Re-scanned rather than
            re-opened, so the verification observes the same inode that was
            emptied.
        retained: The live run directories the run service kept.  When there
            are any, the intermediate directory legitimately survives this
            clean and is not a survivor in the sense that matters - the
            entries the run's own writers would collide with are gone, and
            what is left belongs to a run still using it.

    Returns:
        A single problem naming the survivors, or an empty list when the
        directory holds nothing but a legitimately retained intermediate
        directory.
    """
    allowed = {_workers_dir_name()} if retained else set()
    try:
        with os.scandir(descriptor) as entries:
            survivors = sorted(
                entry.name for entry in entries if entry.name not in allowed
            )
    except OSError as error:
        problem = f"cannot verify that {root} is empty: {error}"
        logger.error("Clean failed: %s", problem)
        return [problem]

    return _report_survivors(root, survivors)


def _empty_through_paths(root: Path, expected: os.stat_result) -> list[str]:
    """Empty the build output directory by path, for platforms without ``at``.

    The fallback for a platform where :data:`_SUPPORTS_DESCRIPTOR_CLEANING` is
    false - Windows, which AAP §0.8 lists as supported.  It keeps every
    guarantee the descriptor path keeps: the caller has already refused a
    symlinked or re-pointed root, a symlinked entry is unlinked and never
    followed, a reparse point is refused outright, each real directory is
    confirmed to still resolve directly inside the root before anything is
    removed through it, and the result is verified.

    **The check-then-use window is closed too, and not by re-checking the
    name.**  Every removal goes through
    :func:`~app.services.delete_verified_entry`, which moves a directory to a
    private random name inside this already-verified root before removing it
    recursively and re-establishes the object's identity through that private
    name.  An entry substituted between the containment check and the removal
    is therefore moved aside and refused rather than followed, and a recursive
    deletion can no longer be redirected outside this root by replacing a
    name.  What remains outside what a build tool can defend is a build output
    directory whose *root* a hostile process can replace faster than each
    per-entry identity re-check below can read it - which is detected and
    fails closed, having removed nothing further.

    Args:
        root: The build output directory, already established by the caller to
            be a real directory and not a symlink.
        expected: The ``lstat`` result the caller validated.  The root's
            identity is re-checked against it before **every** removal, so a
            root replaced after that check is detected and the step fails
            closed rather than traversing it.

    Returns:
        Every problem encountered, in discovery order, and an empty list when
        the directory is verified empty.
    """
    identity = _root_identity_problem(root, expected)
    if identity is not None:
        logger.error("Clean refused: %s", identity)
        return [identity]

    try:
        entries = sorted(root.iterdir())
    except OSError as error:
        problem = f"cannot list the entries of {root}: {error}"
        logger.error("Clean failed: %s", problem)
        return [problem]

    problems: list[str] = []
    removed = 0
    retained: list[Path] = []
    for entry in entries:
        # Re-checked per entry, because without descriptor-relative calls
        # every operation below re-resolves the root by name: a root
        # substituted at any point during the walk would otherwise redirect
        # the rest of it out of the checkout (CWE-59).
        identity = _root_identity_problem(root, expected)
        if identity is not None:
            logger.error("Clean refused: %s", identity)
            problems.append(identity)
            break

        if (
            entry.name == _workers_dir_name()
            and entry.is_dir()
            and not entry.is_symlink()
        ):
            reason, live = reclaim_workers_root()
            retained.extend(live)
            if reason is not None:
                problems.append(reason)
            else:
                removed += 1
            continue

        try:
            # ``lstat`` first, so what follows is decided about the entry
            # itself and never about what it points at.
            entry_status = os.lstat(entry)
        except FileNotFoundError:
            # Gone between the listing and the check, which satisfies this
            # step's postcondition rather than violating it - see the same
            # case in :func:`_remove_descriptor_entries`.
            removed += 1
            continue
        except OSError as error:
            problem = f"could not inspect {entry}: {error}"
            logger.error("Clean failed: %s", problem)
            problems.append(problem)
            continue

        if stat.S_ISDIR(entry_status.st_mode) and not _entry_is_reparse_point(
            entry_status
        ):
            # Only a *real directory* is walked, so only a real directory has
            # to resolve inside the root: an entry reached through a relinked
            # or re-pointed intermediate component is refused before anything
            # is removed.  A link and a reparse point never reach this test -
            # the first is unlinked as the link it is, which cannot touch its
            # target, and the second is refused outright below.
            try:
                containment = _containment_problem(entry, root)
            except FileNotFoundError:
                removed += 1
                continue
            if containment is not None:
                logger.error("Clean refused: %s", containment)
                problems.append(containment)
                continue

        # The removal itself is the run service's one destructive primitive,
        # shared with the reclaim of the intermediate directory so that this
        # port has a single implementation of it.  It refuses a link or a
        # reparse point, and for a directory it moves the entry to a private
        # random name inside this verified root *before* removing it
        # recursively, re-establishing the object's identity through that
        # private name - which is what closes the window a pathname deletion
        # otherwise leaves open, where an entry verified as a plain directory
        # is replaced by a junction before ``rmtree`` resolves the name again.
        problem = delete_verified_entry(
            root,
            entry.name,
            role="entry of the build output",
            # A link here is an entry of a directory being emptied, so the
            # link goes and its target is untouched - the asymmetry with a
            # link *at* the root, which is refused because emptying it would
            # mean emptying whatever it points at.
            unlink_links=True,
        )
        if problem is not None:
            logger.error("Clean failed: %s", problem)
            problems.append(problem)
        else:
            removed += 1

    logger.info("Cleaned %d item(s) from %s", removed, root)
    _note_retained(retained)

    identity = _root_identity_problem(root, expected)
    if identity is not None:
        logger.error("Clean refused: %s", identity)
        problems.append(identity)
        return problems

    allowed = {_workers_dir_name()} if retained else set()
    try:
        survivors = sorted(
            item.name for item in root.iterdir() if item.name not in allowed
        )
    except OSError as error:
        problem = f"cannot verify that {root} is empty: {error}"
        logger.error("Clean failed: %s", problem)
        problems.append(problem)
        return problems

    problems.extend(_report_survivors(root, survivors))
    return problems


def _entry_is_reparse_point(info: os.stat_result) -> bool:
    """Return whether a stat result describes a reparse point.

    The predicate half of :func:`_indirection_problem`, for the places that
    need the fact rather than a reason phrased about the build output root.
    Both Windows-only fields are read with :func:`getattr` and default to
    zero, so this answers ``False`` on POSIX - where the kind does not exist -
    without a platform branch.

    Args:
        info: An :func:`os.lstat` result.

    Returns:
        ``True`` for a junction, a mounted volume, or any other reparse point.
    """
    attributes = getattr(info, "st_file_attributes", 0)
    return bool(
        attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
        or getattr(info, "st_reparse_tag", 0)
    )


def _root_identity_problem(root: Path, expected: os.stat_result) -> str | None:
    """Return why the build output root is no longer the one checked.

    The path-based strategy's substitute for an open descriptor: it cannot
    hold the directory open, so it re-establishes that the name still refers
    to the same inode, and that the inode is still a real directory, before
    each operation that resolves the name again.

    Args:
        root: The build output directory.
        expected: The ``lstat`` result :func:`_empty_build_output` validated.

    Returns:
        ``None`` when the name still refers to the checked directory, and
        otherwise a one-line reason.  A vanished root counts as changed: an
        operation resolving the name after that would act on whatever took
        its place.
    """
    try:
        current = os.lstat(root)
    except OSError as error:
        return (
            f"{root} could not be re-checked while it was being emptied "
            f"({error}), so nothing further was removed"
        )
    if (
        stat.S_ISLNK(current.st_mode)
        or _entry_is_reparse_point(current)
        or not stat.S_ISDIR(current.st_mode)
    ):
        # The reparse test makes this re-check symmetrical with the initial
        # one, so a root that has become an indirection is refused in those
        # terms wherever it is first observed, rather than being reported as a
        # changed inode - which is true but describes the wrong hazard, and
        # would be the *only* thing said about a junction installed here.
        return (
            f"{root} is no longer a plain directory, so it was replaced while "
            "it was being emptied and nothing further was removed"
        )
    if (current.st_dev, current.st_ino) != (expected.st_dev, expected.st_ino):
        return (
            f"{root} was replaced between being checked and being emptied "
            "(its device and inode changed), so nothing further was removed"
        )
    return None


def _containment_problem(entry: Path, root: Path) -> str | None:
    """Return why an entry is not safely inside the root, or ``None``.

    The containment test for the path-based fallback: an entry may only be
    walked when it fully resolves to a direct child of the fully resolved
    root.  Any entry reached through a relinked or re-pointed intermediate
    component fails here and is reported instead of being walked.

    It is reached only for an entry already established to be a plain
    directory, and only while the root is established to be a plain directory
    too - both by :func:`_indirection_problem` and its predicate, on every
    pass.  That ordering is what makes this test meaningful rather than
    circular: :meth:`~pathlib.Path.resolve` resolves *through* a junction, so
    a root that was one would resolve to the external tree and every one of
    that tree's own children would compare as contained.

    Args:
        entry: The candidate entry, a direct child of ``root`` by
            construction.
        root: The build output directory.

    Returns:
        ``None`` when the entry resolves to a direct child of the resolved
        root, and otherwise a one-line reason naming what it resolved to.

    Raises:
        FileNotFoundError: If the entry no longer exists.  Deliberately not
            turned into a reason: an entry that vanished between the listing
            and this check has satisfied the clean rather than defeated it,
            and the caller's handler counts it as removed.
    """
    try:
        resolved_entry = entry.resolve(strict=True)
        resolved_root = root.resolve(strict=True)
    except FileNotFoundError:
        raise
    except OSError as error:
        return f"could not confirm that {entry} lies inside {root}: {error}"

    if resolved_entry.parent != resolved_root:
        return (
            f"{entry} resolves to {resolved_entry}, which is not inside "
            f"{resolved_root}, so it was not removed"
        )
    return None


def _report_survivors(root: Path, survivors: list[str]) -> list[str]:
    """Report what a clean left behind, shared by both strategies.

    Args:
        root: The build output directory.
        survivors: The names still present, in sorted order.  Empty when the
            clean completed.

    Returns:
        An empty list when ``survivors`` is empty, and otherwise a single
        problem naming at most :data:`_MAX_REPORTED_CLEAN_PROBLEMS` of them
        and counting the rest.
    """
    if not survivors:
        logger.info("Verified empty: %s", root)
        return []

    named = survivors[:_MAX_REPORTED_CLEAN_PROBLEMS]
    remainder = len(survivors) - len(named)
    listed = ", ".join(named)
    if remainder:
        listed = f"{listed} and {remainder} more"
    problem = f"{len(survivors)} entry/entries survived the clean: {listed}"
    logger.error("Clean failed: %s", problem)
    return [problem]


# Each helper below writes what the exit contract requires to be visible, and
# this file is the **single emitter** of every one of those facts: a fact an
# outcome carries is reported exactly once, by the command that reads it.  The
# services therefore return structured facts and log none of them - a tolerated
# selection problem on ``RunOutcome.parse_errors``, a dead shard's reason on
# ``RunOutcome.dead_shards``, the writers left unattempted on
# ``ReportOutcome.skipped`` and the artifacts that survived on
# ``ReportOutcome.written`` - so one incident is one record under one logger
# name, and a reader counting ERROR records counts incidents.
#
# One fact is not an outcome field and so is not this file's to emit: the cause
# of a writer failure, with its traceback, which
# ``app/services/report_service.py`` reports where it catches the exception.
# The records below are the *consequence* - the status, the writer and the
# destination it was producing - and deliberately do not repeat that cause.


#: Character bound applied to one rendered selection problem, and the reason
#: it is the console contract's published bound rather than a number of this
#: file's own.  A tolerated problem is engine-authored diagnostic prose - a
#: parser error carrying its source position, a manifest reader's grammar
#: reason and errno, a shard's own reason for producing nothing - which is
#: exactly the class of text ``app/logging_config.py`` sized
#: :data:`~app.logging_config.RELAYED_LINE_LIMIT` for, and a second constant
#: here could only drift from it.  Two consequences, both deliberate: the
#: longest diagnostic the port really produces is an order of magnitude
#: shorter than this bound, escape spellings counted, so truncation never
#: clips a real one in practice; and a producer that hands over a megabyte of
#: text still costs one bounded record instead of burying the rest of the
#: run's log, with the truncation notice naming exactly how many characters
#: were dropped so a clipped record cannot be mistaken for a complete one.
_SELECTION_PROBLEM_LIMIT: Final[int] = RELAYED_LINE_LIMIT


def _render_selection_problem(problem: str) -> str:
    """Render one tolerated selection problem as a single safe log record.

    The emitter of a fact does not get to assume the fact is safe text.  A
    problem on :attr:`~app.services.RunOutcome.parse_errors` is built by
    whichever producer tolerated it - the engine's own parser, the rerun
    manifest reader, a worker's diagnostics - and each of those strings can
    carry text the port did not author.  Two things follow, and both are
    handled here rather than trusted to every producer in turn:

    * A ``CR`` or ``LF`` inside a problem would end this record and start
      what a reader, a log scraper or a CI parser takes for a second one, so
      a line such as ``ERROR app.cli: Exit 3: ...`` could be forged wholesale;
      an ``ESC`` sequence inside it can recolour, erase or overwrite what the
      console has already printed (CWE-117).  Every control character
      therefore leaves here spelled printably and no line break of any kind
      survives, which is what makes "one problem, one record" true of a
      hostile problem as well as an ordinary one.
    * An unbounded problem is an unbounded record, so the rendering is cut to
      :data:`_SELECTION_PROBLEM_LIMIT` characters with a notice naming how
      many were dropped.

    Rendering, never rewriting: a control-free problem within the bound comes
    back character for character, because the whole value of this record is
    that it says what the run tolerated.  The escaping and the bound are
    applied by :func:`~app.logging_config.sanitize_log_text`, the port's one
    implementation of both, so this command's diagnostics and a relayed
    worker line cannot diverge in what they let onto the console.

    Args:
        problem: One element of
            :attr:`~app.services.RunOutcome.parse_errors`.

    Returns:
        One physical line, free of control characters and bounded, ready to
        pass as a ``%s`` argument.
    """
    return sanitize_log_text(problem, limit=_SELECTION_PROBLEM_LIMIT)


def _report_selection_problems(outcome: RunOutcome) -> None:
    """Report the problems a run tolerated, without changing its status.

    A feature that failed to parse, a missing or malformed rerun manifest and
    a missing feature directory all belong here: each is reported on stderr
    and each leaves the run at :attr:`ExitCode.SUCCESS`, matching the
    source's tolerance of a missing configuration file, which it logged before
    carrying on.

    This is the **only** place those problems are logged.  The run service
    carries them on :attr:`~app.services.RunOutcome.parse_errors` and stays
    silent about them, so the count below and the line naming each one are the
    whole of what a reader sees - one incident, one record, and an error count
    that matches the number of things that went wrong.

    Being the only emitter is also why each problem goes through
    :func:`_render_selection_problem` before it reaches a record: the count in
    the summary line is a promise about how many records follow, and a problem
    carrying a line break would break that promise by becoming two of them.
    The rendering is what keeps the record count equal to the problem count
    plus this one summary, whatever a producer put in the text.

    Args:
        outcome: The finished run.
    """
    if not outcome.parse_errors:
        return

    logger.error(
        "%d problem(s) were reported during selection; every one is tolerated "
        "and the exit status stays %d",
        len(outcome.parse_errors),
        int(ExitCode.SUCCESS),
    )
    for problem in outcome.parse_errors:
        logger.error("Tolerated: %s", _render_selection_problem(problem))


def _report_dead_shards(outcome: RunOutcome) -> None:
    """Name every shard that produced no results, whatever the run's status.

    Each reason is logged here and nowhere else: the run service builds it -
    naming the shard, its scenario count and what went wrong - carries it on
    :attr:`~app.services.RunOutcome.dead_shards` and deliberately does not log
    it, so the count and the per-shard lines below are one account rather than
    a second copy of one.

    **Separated from the exit class on purpose.**  A dead shard and an
    artifact failure can coexist, and the artifact failure outranks it - most
    obviously when *every* worker dies, which leaves nothing to merge, so the
    run reports the empty merge.  While this emission lived inside
    :func:`_worker_status`, that path returned its status before any of these
    lines were written and the log named only the aggregate merge failure: the
    shard indices, their locations and their reasons - the only record of
    *which* work was lost and why - were computed and then discarded.  This
    function is therefore called once, before the precedence returns in
    :func:`_publish`, and the status functions below only name the class they
    return.

    Args:
        outcome: The finished run.  A run with no dead shard is silent here.
    """
    if not outcome.dead_shards:
        return

    # Status-neutral on purpose.  This record is written before the exit class
    # is known - that is the whole point of it - so it states only what the
    # run observed.  What became of the artifacts is a different fact, decided
    # further down and reported by whichever branch decides it: all four from
    # the completed shards at :attr:`ExitCode.WORKER_DIED`, none at all when
    # the merge produced nothing, a prefix when a writer failed, and none by
    # design under ``--rerun``.  Claiming any of those here would contradict
    # the record that follows on three of those four paths.
    logger.error(
        "%d of %d worker shard(s) produced no results; each one is named below",
        len(outcome.dead_shards),
        outcome.worker_count,
    )
    for reason in outcome.dead_shards:
        logger.error("Incomplete shard: %s", reason)


def _worker_status(outcome: RunOutcome) -> ExitCode:
    """Return the status a run's shard results imply.

    The reasons themselves are not logged here: :func:`_report_dead_shards`
    has already named every one of them, before whichever exit class the run
    settles on, so this adds only the status line for the class it returns and
    the two never duplicate each other.

    Args:
        outcome: The finished run.

    Returns:
        :attr:`ExitCode.WORKER_DIED` when a shard produced no results, and
        :attr:`ExitCode.SUCCESS` otherwise - **whatever the scenarios did**.
        A dead shard does not suppress the artifacts: they are written from
        the shards that completed, and only then is this status returned.
    """
    if not outcome.dead_shards:
        return ExitCode.SUCCESS

    logger.error(
        "Exit %d: %d of %d worker shard(s) produced no results, so the four "
        "artifacts were written from the shards that completed",
        int(ExitCode.WORKER_DIED),
        len(outcome.dead_shards),
        outcome.worker_count,
    )
    return ExitCode.WORKER_DIED


_UNRESOLVED_DESTINATION: Final[str] = "an unresolved destination"


def _writer_failure_status(report: ReportOutcome) -> ExitCode:
    """Report a failed writer and return the status it implies.

    Nothing is rolled back, here or anywhere else: the artifacts already
    written stay where their writers put them, which is the writer-failure
    cause of :attr:`ExitCode.ARTIFACT_FAILURE`.  The failing writer is named
    together with the destination it was producing, the writers left
    unattempted are named, and the surviving artifacts are named, so a CI log
    identifies what the publisher will find without a second run.

    Every field comes off the outcome and none is re-derived - the destination
    in particular, because resolving it again here would put a second spelling
    of one path in the port.  It is optional on the outcome, so an unresolved
    destination degrades to readable text instead of a bare ``None``.  The
    exception itself is deliberately absent: its cause and traceback are
    reported once, by ``app/services/report_service.py`` where it was caught.

    One outcome reaches here with **no** failing writer: the run's claim on
    the build output was lost during publication
    (:attr:`~app.services.ReportOutcome.boundary_lost`).  All four artifacts
    exist and are kept, so there is no writer to name and nothing was skipped;
    what is reported instead is that the published set cannot be vouched for
    as this run's, because another run may have claimed the workspace while
    the writers were running.

    Args:
        report: The fan-out's outcome, not ``ok``.  Its
            :attr:`~app.services.ReportOutcome.failed_writer` names the writer
            that failed and its
            :attr:`~app.services.ReportOutcome.failed_path` that writer's
            intended destination when it could be resolved; both are absent on
            the lost-claim outcome above.

    Returns:
        :attr:`ExitCode.ARTIFACT_FAILURE`.
    """
    if report.failed_writer is None:
        # The claim on the build output was lost, and no writer failed: all
        # four artifacts were written and are kept, but into a workspace this
        # run no longer owned, so what a reader will find there may be a
        # mixture of two runs' reports.  The report service has already named
        # the loss; this record names the class it produces.
        logger.error(
            "Exit %d: the four artifacts were written, but this run lost its "
            "claim on %s during publication, so the published set is not "
            "vouched for as this run's",
            int(ExitCode.ARTIFACT_FAILURE),
            render_path(target_root()),
        )
        if report.written:
            logger.error(
                "Retained, and not deleted: %s",
                ", ".join(render_path(path) for path in report.written),
            )
        return ExitCode.ARTIFACT_FAILURE

    # Every path this command names is rendered by ``app/logging_config.py``
    # into the repository-relative identifier a reader acts on - the same
    # spelling README.md quotes and the publisher's narrowed glob matches
    # (``Jenkins:15``), and one this module still does not hold.  The absolute
    # location of a CI workspace is not diagnostic and publishing it in an
    # archived console log discloses the topology of the machine that produced
    # it (CWE-200), so the rendering is applied here, at the point the value
    # becomes a record, rather than trusted to whoever reads the log.
    destination = (
        _UNRESOLVED_DESTINATION
        if report.failed_path is None
        else render_path(report.failed_path)
    )
    logger.error(
        "Exit %d: report writer %s failed writing %s",
        int(ExitCode.ARTIFACT_FAILURE),
        report.failed_writer,
        destination,
    )
    if report.skipped:
        logger.error(
            "Not attempted after %s failed: %s",
            report.failed_writer,
            ", ".join(report.skipped),
        )
    if report.written:
        logger.error(
            "Retained, and not deleted: %s",
            ", ".join(render_path(path) for path in report.written),
        )
    else:
        logger.error("No artifact had been written when the failure occurred")
    return ExitCode.ARTIFACT_FAILURE


def _publish(outcome: RunOutcome, guard: RunLock | None = None) -> ExitCode:
    """Turn a finished run into artifacts and a status.

    The three states of :attr:`~app.services.RunOutcome.result_set` are read
    apart rather than collapsed into one truthiness test, because two look
    alike and mean opposite things: an **empty** document is a tag expression
    that selected nothing, so all four artifacts are written empty at
    :attr:`ExitCode.SUCCESS` and the publisher always has an input, while a
    **missing** one is a merge that produced nothing and writes no artifact
    at :attr:`ExitCode.ARTIFACT_FAILURE`.  A rerun writes nothing by design.

    Precedence between coexisting signals is decided here, since the run
    service records each one independently: the artifact failure, then the
    dead worker, with each cause reporting itself on stderr first.
    ``infrastructure_error`` is read at two points: before the ``--rerun``
    short-circuit, where a rerun that could not create a directory to work in
    is otherwise indistinguishable from a successful one, and after the
    fan-out, where intermediates that could not be removed must not cost a
    completed run its artifacts.

    Args:
        outcome: The finished run.
        guard: The claim this run holds on the build output, passed on to the
            fan-out so that each writer's publication is checked against it,
            or ``None`` when the caller holds none.

    Returns:
        The status the run's artifacts, shards and intermediate storage imply.
    """
    # First, and before any of the precedence returns below: a dead shard's
    # identity is reported whatever class the run ends up in.  See
    # :func:`_report_dead_shards` for why it cannot live inside the status
    # function it used to.
    _report_dead_shards(outcome)

    infrastructure = outcome.infrastructure_error
    if infrastructure is not None:
        logger.error(
            "Exit %d: this run's own intermediate storage failed: %s",
            int(ExitCode.ARTIFACT_FAILURE),
            infrastructure,
        )

    if outcome.rerun:
        # The Java rerun runner declared an empty plugin list, so a rerun
        # produces no artifact and modifies none.  The report service is not
        # merely given nothing to do - it is not invoked at all.
        logger.info(
            "Rerun finished: no artifact was written and none was modified, "
            "matching the empty plugin list of FailedTestRunner.java:9-12"
        )
        if infrastructure is not None:
            # Not "a rerun writes nothing, so all is well": this rerun
            # executed no selected scenario at all, or left its intermediates
            # behind, and either way it did not do what it was asked to.
            return ExitCode.ARTIFACT_FAILURE
        return _worker_status(outcome)

    if outcome.result_set is None or outcome.merge_produced_nothing:
        # Equivalent conditions by the run service's construction, and both
        # tested, because either alone is sufficient grounds: a fan-out needs
        # a document, and there is none.  The infrastructure line above, when
        # there is one, has already said why there is none.
        if infrastructure is None:
            logger.error(
                "Exit %d: %d scenario(s) were selected and no worker result "
                "could be read, so the merge produced nothing, no artifact "
                "was written and %s is left as the clean step left it",
                int(ExitCode.ARTIFACT_FAILURE),
                outcome.selected_count,
                render_path(target_root()),
            )
        else:
            logger.error(
                "Exit %d: no artifact was written and %s is left as the clean "
                "step left it",
                int(ExitCode.ARTIFACT_FAILURE),
                render_path(target_root()),
            )
        return ExitCode.ARTIFACT_FAILURE

    # One merged document, four independent writers, in the service's own
    # order: the two machine-read contracts first.  No path is passed - each
    # writer resolves its own destination.
    # The guard travels with the document: the fan-out checks it before each
    # writer, so a run that lost its claim on the build output stops
    # publishing instead of writing into a workspace another run has taken
    # over.  ``None`` is accepted there, which is what keeps the fan-out
    # callable on its own.
    report = generate_reports(outcome.result_set, guard=guard)
    if not report.ok:
        return _writer_failure_status(report)

    logger.info(
        "Wrote %d artifact(s): %s",
        len(report.written),
        ", ".join(render_path(path) for path in report.written),
    )
    if infrastructure is not None:
        logger.error(
            "Exit %d: all four artifacts were written, but the run's "
            "intermediate documents could not be removed, so the status is "
            "not success",
            int(ExitCode.ARTIFACT_FAILURE),
        )
        return ExitCode.ARTIFACT_FAILURE
    return _worker_status(outcome)



# --------------------------------------------------------------------------- #
# The command
#
# Defined exactly once.  ``pyproject.toml``'s console script points at the
# object below, and ``create_app()`` attaches the same object to the Flask CLI
# group, so the two routes cannot diverge.
# --------------------------------------------------------------------------- #


def _settle_release(problem: str | None, status: ExitCode) -> ExitCode:
    """Fold a failed run-lock release into the status about to be published.

    A release that leaves this run's lock file, or the shared intermediate
    directory it sat in, behind is not a cosmetic failure: AAP 0.4.1 requires
    that directory to be gone by the time the command returns, and a lock file
    with no lock behind it is what the next run in this checkout has to
    reason about.  So it is reported here and it costs a success its status.

    The precedence is :func:`_remove_intermediates`'s, for the same reason: a
    usage error, a dead worker or an artifact failure already names something
    the operator must act on, and a tidy-up does not outrank it.

    Args:
        problem: What :meth:`~app.services.RunLock.release` reported, or
            ``None`` when nothing of the claim survived.
        status: The status the command was about to publish.

    Returns:
        ``status`` unchanged, except that :attr:`ExitCode.SUCCESS` becomes
        :attr:`ExitCode.ARTIFACT_FAILURE` when the release failed.
    """
    if problem is None:
        return status
    if status is ExitCode.SUCCESS:
        logger.error(
            "Exit %d: %s",
            int(ExitCode.ARTIFACT_FAILURE),
            problem,
        )
        return ExitCode.ARTIFACT_FAILURE
    logger.error(
        "%s; the status stays %d, because the failure already being reported "
        "outranks a tidy-up",
        problem,
        int(status),
    )
    return status


def _status_of(exit_error: SystemExit) -> int | None:
    """Return the status a :exc:`SystemExit` carries, if it carries one.

    Args:
        exit_error: The exception Click raised on its way out.

    Returns:
        Its integer status; ``0`` for the ``None`` code, which is what
        :exc:`SystemExit` means by a successful exit; and ``None`` when the
        code is neither - a string code, which the interpreter prints and
        exits ``1`` for, is not a status this module may reason about.
    """
    code = exit_error.code
    if code is None:
        return int(ExitCode.SUCCESS)
    if isinstance(code, int):
        return code
    return None


def _remove_intermediates(status: int | None) -> int | None:
    """Remove this invocation's intermediate directories, and settle a status.

    The removal itself belongs to ``app/services/test_run_service.py``, the
    single owner of that directory; this is the module's one call into it, and
    what it adds is the refusal to report success when the removal failed.
    A surviving directory holds this run's per-worker result documents -
    tracebacks and screenshot attachments among them - in a workspace whose
    Cucumber publisher glob is narrowed to the single JSON report
    (``Jenkins:15``) precisely so that nothing intermediate is publishable.

    Args:
        status: The status the command is about to publish, or ``None`` when
            it is publishing none - an exception is in flight, or the value
            Click returned is not a status.  ``None`` is reported and never
            upgraded: there is nothing to upgrade, and an exception must not
            be turned into an exit code here.

    Returns:
        ``status`` unchanged, except that :attr:`ExitCode.SUCCESS` becomes
        :attr:`ExitCode.ARTIFACT_FAILURE` when the removal failed.  A non-zero
        status is never overwritten, so a usage error, a dead worker or an
        artifact failure keeps its own class and the higher-priority failure
        stays visible.
    """
    # Two steps, and both belong to this one moment.  The first removes what
    # *this* invocation created, which is the guarantee the exit contract
    # makes.  The second reclaims what an **abandoned** run left behind - a
    # crash, a ``kill -9``, a machine that went away - so a stale intermediate
    # document cannot sit in the workspace indefinitely while the publisher
    # reads it.  What the second step will not touch is a directory belonging
    # to a run still in progress, because deleting a live sibling's working
    # files is the concurrency defect this whole design exists to avoid; the
    # service reports those and they are not a failure here.
    own = cleanup_workers_dir()
    abandoned, _retained = reclaim_workers_root()
    reasons = [text for text in (own, abandoned) if text is not None]
    if not reasons:
        return status
    reason = "; ".join(reasons)

    if status == int(ExitCode.SUCCESS):
        logger.error(
            "Exit %d: %s",
            int(ExitCode.ARTIFACT_FAILURE),
            reason,
        )
        return int(ExitCode.ARTIFACT_FAILURE)

    logger.error(
        "%s; the status stays %s, because the failure already being reported "
        "outranks a tidy-up",
        reason,
        "unset" if status is None else status,
    )
    return status


class _RunTestsCommand(click.Command):
    """The command class that makes intermediate cleanup unconditional.

    The guarantee is that the per-worker intermediate directory - named by
    :mod:`app.utils.paths` and removed by
    :func:`~app.services.cleanup_workers_dir` - does not survive an
    invocation, because ``Jenkins:15`` narrows the Cucumber publisher's
    ``fileIncludePattern`` to the single JSON report and an intermediate
    per-worker document must never be left where the publisher can read it.
    A ``finally`` inside the command callback cannot deliver it: Click rejects
    an unknown option, a malformed ``--tags`` expression and a non-positive
    ``--workers`` *while parsing*, before the callback is entered.

    :meth:`main` and :meth:`make_context` hold this module's only two cleanup
    call sites, both into :func:`_remove_intermediates` - ``main`` is the
    outermost frame of a console invocation and ``make_context`` the parse
    boundary both invocation routes share, and cleanup being idempotent is
    what makes covering both harmless.  The callback holds none, so nothing
    can disagree about whether cleanup ran.

    A cleanup failure **upgrades a success to**
    :attr:`ExitCode.ARTIFACT_FAILURE` and leaves every other status alone:
    returning ``0`` with this run's intermediates still in the workspace would
    be a false success, while overwriting a usage error's ``2`` or a dead
    worker's ``3`` would hide a higher-priority failure behind a tidy-up.
    """

    def main(self, *args: Any, **kwargs: Any) -> Any:
        """Run the command, removing this invocation's intermediates after it.

        Args:
            *args: Positional arguments for :meth:`click.Command.main`,
                forwarded unchanged - Click's own signature is deliberately
                not restated, so an upgrade cannot silently drop a parameter.
            **kwargs: Keyword arguments for the same, forwarded unchanged.

        Returns:
            Whatever :meth:`click.Command.main` returns, except that a
            successful return with a failed cleanup returns
            :attr:`ExitCode.ARTIFACT_FAILURE`.  In standalone mode - every
            real invocation - it does not return at all but raises
            :exc:`SystemExit`.

        Raises:
            SystemExit: Click's own, with its status intact, unless that
                status is a success and the cleanup failed, in which case it
                carries :attr:`ExitCode.ARTIFACT_FAILURE`.
            BaseException: Anything else propagates unchanged once cleanup has
                been attempted and reported: an interrupt is the operator's
                decision and an unexpected exception is a defect, and neither
                may become a published status.
        """
        try:
            result = super().main(*args, **kwargs)
        except SystemExit as exit_error:  # noqa: TRY302 - see the re-raise below
            status = _status_of(exit_error)
            resolved = _remove_intermediates(status)
            if resolved != status:
                raise SystemExit(resolved) from exit_error
            raise
        except BaseException:
            # An interrupt, or a defect.  Cleanup is still attempted and its
            # failure still reported, but nothing here may replace an
            # exception with a status.
            _remove_intermediates(None)
            raise
        # Reached only outside standalone mode, where Click returns the status
        # instead of raising it - and where a callback's own return value,
        # which is not a status at all, has to pass through untouched.
        if isinstance(result, int):
            return _remove_intermediates(result)
        _remove_intermediates(None)
        return result

    def make_context(self, *args: Any, **kwargs: Any) -> click.Context:
        """Parse the arguments, removing intermediates if parsing fails.

        :meth:`main` covers the console entry point but is not the only way
        this command is parsed: when ``create_app()`` attaches it to the Flask
        CLI group, Click's group dispatch builds the child's context and
        invokes it directly, so a value rejected while parsing there never
        reaches ``main``.  This is the boundary both routes share - ``main``
        calls it too - and cleanup is idempotent, so covering both is
        harmless.

        Args:
            *args: Positional arguments for
                :meth:`click.Command.make_context`, forwarded unchanged.
            **kwargs: Keyword arguments for the same, forwarded unchanged.

        Returns:
            The context Click built.

        Raises:
            BaseException: Whatever parsing raised, unchanged and after the
                cleanup - a :exc:`click.UsageError` for a rejected value, or
                the :exc:`SystemExit` Click's error handling produces.  The
                status is never altered here: on this route there is none yet,
                and inventing one would mask the usage error.
        """
        try:
            return super().make_context(*args, **kwargs)
        except BaseException:
            _remove_intermediates(None)
            raise




@click.command(name=COMMAND_NAME, cls=_RunTestsCommand)
@click.option(
    "--tags",
    "tags",
    metavar="EXPR",
    default=DEFAULT_TAG_EXPRESSION,
    show_default=True,
    callback=_validate_tag_expression,
    help=(
        "Tag expression selecting what to run, in the full grammar - "
        "'@Smoke', '@Login and not @wip', '@UPGN-286 or @UPGN-287'. "
        "Rejected together with --rerun, which applies no filter at all."
    ),
)
@click.option(
    "--browser",
    "browser",
    metavar="NAME",
    default=None,
    show_default="the browser property in configuration.properties",
    help=(
        "Browser to drive, chrome or firefox, forwarded to every worker as "
        "engine userdata, which outranks the properties file. Left unset the "
        "browser property decides. Not validated here: an unrecognised value "
        "fails at first driver use, exactly as it does in the Java driver."
    ),
)
@click.option(
    "--workers",
    "workers",
    metavar="N",
    type=click.IntRange(min=1),
    default=None,
    show_default="the CPU count",
    help=(
        "How many worker processes share the selected scenarios, sharded one "
        "scenario at a time. '--workers 1' runs sequentially. Never exceeds "
        "the number of scenarios selected."
    ),
)
@click.option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    default=False,
    show_default=True,
    help=(
        "Resolve every step without executing it, and report the artifacts "
        "accordingly. No browser is started. Off by default."
    ),
)
@click.option(
    "--rerun",
    "rerun",
    is_flag=True,
    default=False,
    show_default=True,
    help=(
        "Re-run only the scenarios the last run recorded as failing, taken "
        "from the rerun manifest. Applies no tag filter, writes no artifact, "
        "leaves the existing artifacts untouched and never cleans. Off by "
        "default."
    ),
)
@click.option(
    "--clean/--no-clean",
    "clean",
    default=True,
    show_default=True,
    help=(
        "Empty the build output directory before running, as 'mvn clean test' "
        "did. Ignored under --rerun, which must not delete the manifest it "
        "reads."
    ),
)
def run_tests(
    *,
    tags: str,
    browser: str | None,
    workers: int | None,
    dry_run: bool,
    rerun: bool,
    clean: bool,
) -> None:
    """Run the Gherkin suite and write the four report artifacts.

    Ports the two Java runner classes: the options below are the knobs their
    annotations and the Maven build fixed, and the artifacts are written to the
    destinations their plugin list named.

    \b
    Exit status - a test outcome is never one of them:
      0  the run completed, whatever the scenarios did, including a tag
         expression that selected nothing, a browser that failed to start, an
         unrecognised browser value, a feature that failed to parse and a
         missing or malformed rerun manifest
      2  an unknown or conflicting option, including --rerun with --tags
      3  a worker process produced no results; the artifacts are still
         written from the shards that completed
      4  artifact production failed: the merge produced no result set
         (nothing is written), a report writer failed (what was already
         written is retained), --clean could not empty the build output
         (nothing is executed), or another run in this checkout already
         holds the build output (nothing is executed). Each cause names
         itself on stderr

    A run needs a browser and a populated configuration.properties; the
    repository ships a template only, and a missing file is tolerated so that
    --dry-run and the report writers work without one.
    """
    configure_logging()

    context = click.get_current_context()

    # Unconditional removal of the per-worker intermediate directory is *not*
    # here.  It lives in :class:`_RunTestsCommand`, which encloses option
    # parsing as well as this function, because a value Click rejects while
    # parsing never reaches a ``finally`` written inside the callback.
    if rerun and _option_given(context, "tags"):
        raise click.UsageError(
            "--rerun cannot be combined with --tags: a rerun applies no "
            "tag filter, because the Java rerun runner declared none "
            "(FailedTestRunner.java:9-12), and applying one would "
            "silently skip the failures a rerun exists to re-execute.",
            ctx=context,
        )

    # The intent handed to the service is unambiguous, never a sentinel it
    # could read as "unset": ``None`` is documented there as "no filter",
    # which is what clears the engine configuration's default tag, and the
    # worker count is resolved here so the service is told a number rather
    # than asked to guess one.
    tag_expression = None if rerun else tags
    worker_count = default_worker_count() if workers is None else workers
    cleaning = clean and not rerun

    # Both option values below are whatever the caller typed, so both are
    # rendered through ``app/logging_config.py`` before they reach a record:
    # it quotes them, deletes terminal escape sequences, spells any remaining
    # control character printably so the record stays one physical line, and
    # bounds the length.  Without that, a newline or a CSI sequence inside a
    # value forges or overwrites CI log lines (CWE-117).  The rendering is
    # **for the log record only** - ``--browser`` is passed to the run service
    # verbatim below, unvalidated and unnormalised, because an unrecognised
    # browser must still fail at first driver use exactly as
    # ``Driver.java:29-42``'s missing default branch makes it.
    logger.info(
        "Starting the suite: tags=%s, browser=%s, workers=%d, "
        "dry-run=%s, rerun=%s, clean=%s",
        "cleared" if rerun else render_option_value(tag_expression),
        "unset" if browser is None else render_option_value(browser),
        worker_count,
        dry_run,
        rerun,
        cleaning,
    )
    if clean and rerun:
        logger.info(
            "--clean is ignored under --rerun: the manifest a rerun reads "
            "lives in the build output and must survive"
        )

    # One claim on this checkout's build output, taken **before** the clean
    # and given back after the publication, because those two and everything
    # between them operate on state the whole checkout shares: the clean
    # empties artifacts another run has just published, the run reclaims from
    # the intermediate directory another run is writing into, and the fan-out
    # publishes four artifacts one writer at a time.  Two runs interleaved
    # across that sequence leave a workspace holding a mixture of both -
    # reports, scenario data, credentials-bearing step arguments and
    # screenshots from two different executions, with nothing in either
    # artifact to say so.  The run service owns the lock because it owns the
    # directory the lock file lives in; the lifecycle is owned here, because
    # this is the frame that spans the three phases.
    run_lock, lock_refusal = acquire_run_lock()
    if run_lock is None:
        # Refused rather than queued - see the run service's section comment.
        # The class is the existing artifact-infrastructure failure: nothing
        # was executed, no writer was reached and no artifact was touched,
        # which is exactly the clean-failure row's shape, and AAP 0.1.3
        # deviation 15 fixes the three non-zero classes so no fourth exists to
        # invent.
        logger.error(
            "Exit %d: this run did not start, and nothing in the build "
            "output was read, written or deleted: %s",
            int(ExitCode.ARTIFACT_FAILURE),
            lock_refusal,
        )
        context.exit(int(ExitCode.ARTIFACT_FAILURE))

    try:
        clean_failure = _empty_build_output() if cleaning else None

        if clean_failure is not None:
            # The run stops here, and that is the point: the build output is
            # in an unknown state, so anything executed now would publish a
            # mixture of this run's artifacts and whatever the clean could not
            # remove.  Nothing is executed, no writer is reached, and what
            # survives is left exactly where it is for an operator to look at.
            logger.error(
                "Exit %d: the build output directory could not be emptied, so "
                "the suite was not started, no artifact was written and "
                "nothing was deleted beyond what is reported above: %s",
                int(ExitCode.ARTIFACT_FAILURE),
                clean_failure,
            )
            exit_code = ExitCode.ARTIFACT_FAILURE
        else:
            # Selection, sharding, per-worker invocation and the merge - none
            # of which is this file's business.  The service exits no process
            # and raises nothing for a test outcome; every signal arrives on
            # the outcome, and the malformed-expression exception it documents
            # cannot reach here because the option callback rejected such a
            # value while Click was still parsing.
            outcome = run_suite(
                tags=tag_expression,
                browser=browser,
                workers=worker_count,
                dry_run=dry_run,
                rerun=rerun,
            )

            # Reported first and separately: these never change the status,
            # and reading them before the artifacts keeps the stderr account
            # in the order the run discovered things.
            _report_selection_problems(outcome)

            exit_code = _publish(outcome, guard=run_lock)

        # Released **before** the status is published, not merely in the
        # ``finally`` below, because a release that leaves the lock file or
        # the shared intermediate directory behind is the state AAP 0.4.1
        # forbids and has to be able to change the outcome.  The rule is
        # ``_remove_intermediates``'s: a success becomes an artifact failure,
        # and a status that is already non-zero keeps its own class.
        exit_code = _settle_release(run_lock.release(), exit_code)

        if exit_code is ExitCode.SUCCESS:
            # The non-zero classes have already named themselves on stderr,
            # each carrying its status, so only the successful case needs a
            # line here.
            logger.info("Finished with status %d", int(exit_code))

        # The one place a status leaves this command.  An unexpected exception
        # - one no documented contract predicts - is deliberately *not* mapped
        # onto a published class: it propagates with its traceback, so a
        # defect is never disguised as one of the four published statuses
        # above.
        context.exit(int(exit_code))
    finally:
        # The net, for every way out of this frame the line above does not
        # reach: the ``SystemExit`` it raises, an interrupt, and a defect
        # alike.  Releasing is idempotent, so on the ordinary path this does
        # nothing and reports nothing; on an exceptional path there is no
        # status to change, and an exception must not be turned into one.
        run_lock.release()
