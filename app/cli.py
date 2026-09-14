"""The ``run-tests`` command - this port's single execution entry point.

It replaces two Java runner classes whose entire behaviour lived in
annotations, verified verbatim at the pinned reference revision:

``CukesRunner.java:7-20``
    ``@RunWith(Cucumber.class)`` with a four-plugin list, a features root, a
    glue package, ``dryRun = false`` and ``tags = "@Smoke"`` - and an
    **empty class body**.  Its four plugin destinations are the four artifact
    constants :mod:`app.utils.paths` owns; not one of them is named here.
``FailedTestRunner.java:8-12``
    ``glue`` plus a ``features`` attribute naming the rerun manifest, and two
    load-bearing *absences*: **no** ``plugin`` list, so a rerun writes no
    artifact, and **no** ``tags`` attribute, so a rerun applies no tag filter.

``pom.xml:21-29`` supplied the execution semantics - ``parallel=methods``,
``useUnlimitedThreads=true`` with ``<threadCount>4</threadCount>`` commented
out at ``pom.xml:24``, and ``testFailureIgnore=true``.  None of it ever ran:
every runner sits under ``src/main/java`` and surefire's include matches test
classes only, so the pipeline's own command (``Jenkins:8``, ``Jenkins:10``)
reports "No tests to run." and BUILD SUCCESS and produces no artifact at all.
This command implements the intent those annotations encode rather than that
null result: the suite really runs, and the four artifacts are really written.

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
``--clean`` step cannot empty the build output, or
the run's per-worker intermediate directory cannot
be prepared or removed
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
merely lost a shard.  Every signal is logged whatever the status, so choosing
a status discards no information.

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
reported and is not a failed clean.

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
   For ``--browser`` and ``--tags`` that assertion carries a second
   requirement: a value containing a newline or a terminal escape sequence
   reaches the service **byte-for-byte**, while the "Starting the suite" record
   shows it quoted, bounded and on one physical line, because those two values
   are rendered for the record by
   :func:`~app.logging_config.render_option_value` and by nothing else.  No
   second record may appear, whatever the value contains.
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
# depends on; ``render_option_value`` is the other half of the same contract -
# ``app/logging_config.py`` owns what is allowed *onto* the console, so the two
# option values this command echoes back are rendered there rather than here.
from app.logging_config import configure_logging, render_option_value
from app.services import (
    ReportOutcome,
    RunOutcome,
    cleanup_workers_dir,
    default_worker_count,
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
    "ExitCode",
    "run_tests",
]

#: Progress at ``INFO`` reaches stdout and diagnostics at ``ERROR`` reach
#: stderr through the handler split ``app/logging_config.py`` installs on the
#: ``app`` package logger.  This module writes to neither stream directly.
logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Command vocabulary
# --------------------------------------------------------------------------- #

#: The command's name, as ``pyproject.toml`` declares the console script and as
#: it appears on the Flask CLI group.  Named once so the two cannot disagree.
COMMAND_NAME: Final[str] = "run-tests"

#: The default tag filter, from ``CukesRunner.java:18``.  It is declared once
#: in the suite, at ``Crm.feature:1``, so a default run selects the CRM feature
#: alone - behaviour to reproduce, not a defect to correct.  ``behave.ini``
#: carries the same value as ``default_tags``; this option always passes an
#: explicit expression, so that default can never apply implicitly and can be
#: cleared outright under ``--rerun``.
DEFAULT_TAG_EXPRESSION: Final[str] = "@Smoke"


class ExitCode(IntEnum):
    """The command's exit statuses - the whole published set.

    Three non-zero members, and exactly three, because AAP deviation 15
    licenses exactly three classes the source lacks: a CLI usage error, a dead
    worker, and a merge *or* writer failure.  The last of those is one class,
    so it is one member - :attr:`ARTIFACT_FAILURE` - with no alias beside it,
    since a second name for the same number would preserve the appearance of a
    fourth class in ``--help``, in a log line and in a reader's head.

    ``1`` is absent deliberately: it is what an uncaught exception and an
    interrupt produce, so reusing it for a defined class would make a status
    ambiguous.  Every value below is asserted by ``tests/test_cli.py``.

    Attributes:
        SUCCESS: The run completed.  **Every test outcome lands here** -
            failing scenarios, errors, undefined and skipped steps, a browser
            that fails to start, an unrecognised ``browser`` value, a feature
            that fails to parse, a missing or malformed rerun manifest, and a
            tag expression that selected nothing.  ``pom.xml:25`` and the six
            ``-1`` publisher thresholds (``Jenkins:15``) put a test outcome
            outside the exit status, and this port preserves that.
        USAGE_ERROR: An unknown or conflicting option, including
            ``--rerun --tags``, a malformed ``--tags`` expression and a
            non-positive ``--workers``.  Click's own convention for a
            :exc:`click.UsageError`, so the two agree.  Nothing is executed
            and no artifact is written.
        WORKER_DIED: A worker process produced no results.  The artifacts are
            still written from the shards that completed, and every incomplete
            shard is named on stderr.
        ARTIFACT_FAILURE: This port's own artifact production, or the build
            output directory it needs, failed.  One status, four causes, and
            each keeps its own diagnostics and its own artifact behaviour:

            * **The merge produced no result set.**  Scenarios were selected
              and not one worker result could be read, so there is no document
              to write; no artifact is written and the build output directory
              is left as the clean step left it.
            * **A report writer failed.**  Artifacts written before it remain
              - nothing is rolled back - and the failing writer is named on
              stderr.
            * **The ``--clean`` step could not empty the build output.**  A
              symlink or a non-directory occupying its path, an entry that
              could not be removed, or an entry that survived the attempt.
              Nothing is executed and no writer is reached, because the state
              of what the publisher would read is unknown.
            * **The per-worker intermediate directory could not be prepared
              or removed.**  That directory's whole lifecycle - creating it,
              handing each worker a path inside it, and removing it - belongs
              to ``app/services/test_run_service.py``; this member is the
              published class such a failure is reported under, so an
              intermediate result document left visible to the publisher
              (``Jenkins:15`` narrows its glob to the JSON report alone) can
              never be reported as success.
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
# --------------------------------------------------------------------------- #


def _validate_tag_expression(
    ctx: click.Context,
    param: click.Parameter,
    value: str,
) -> str:
    """Reject a malformed ``--tags`` expression as a usage error.

    The full grammar is supported - ``@Smoke``, ``@Login and not @wip``,
    ``@UPGN-286 or @UPGN-287`` - and the parsed form is deliberately
    discarded: evaluating it against the suite is the run service's work, and
    parsing it a second time there is cheap next to the alternative, which is
    a malformed value surfacing as a traceback in the middle of a run.

    Args:
        ctx: The Click context, supplied by the parser.  Unused, and named
            because Click calls a callback positionally.
        param: The parameter being processed, used to name it in the error.
        value: The expression as the user wrote it, or the declared default
            when the option was not given.  A callback runs for a default too,
            which is intentional: it keeps the shipped default honest.

    Returns:
        ``value`` unchanged, so the expression reaches the service exactly as
        the user wrote it.

    Raises:
        click.BadParameter: If the expression cannot be parsed.  Click turns
            it into :attr:`ExitCode.USAGE_ERROR` and prints the reason on
            stderr.
    """
    del ctx  # The callback signature is Click's; the context is not needed.
    try:
        TagExpressionParser.parse(value)
    except TagExpressionError as error:
        raise click.BadParameter(str(error), param=param) from error
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


# --------------------------------------------------------------------------- #
# The clean step
#
# ``mvn clean test`` (``Jenkins:8``, ``Jenkins:10``) emptied the build output
# before every run, and this is that half of the command.  It is the only code
# in this port that deletes anything the user did not name, so it is written
# against both of its hostile directions at once:
#
# * **Following a link out of the checkout.**  A build output directory that is
#   a symlink, or an entry inside it that is one, must never be traversed while
#   deleting; a plain ``is_dir()`` test is true for a symlink to a directory,
#   which is how a recursive delete escapes the repository (CWE-59).
# * **Carrying on over output whose state is unknown.**  A clean that could not
#   finish leaves obsolete report pages and stale worker intermediates where
#   the run's own writers may not overwrite them and the publisher will still
#   read them, so it is an artifact-infrastructure failure and not a cosmetic
#   one - see :attr:`ExitCode.ARTIFACT_FAILURE`.
# --------------------------------------------------------------------------- #

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

#: How many individual problems a returned clean-failure reason names before
#: it abbreviates.  Every one of them is logged in full as it is discovered;
#: this only bounds the single summary line, so one unreadable directory with
#: two hundred entries cannot turn the status line into a wall of text.
_MAX_REPORTED_CLEAN_PROBLEMS: Final[int] = 3


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

    * **A symlink at the root.**  It is identified with :func:`os.lstat`, so
      the link itself is examined rather than its target, and it is neither
      traversed nor unlinked - deleting a link a user deliberately placed is
      as presumptuous as deleting what it points at.  ``--no-clean`` is the
      escape hatch for a checkout laid out that way.
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
    it changes, which is the same defence by the only means available there.

    **One entry is not deleted here**: the per-worker intermediate directory
    is handed to :func:`~app.services.reclaim_workers_root`, because it holds
    the working files of every run in this checkout and only the run service
    can tell an abandoned run's leftovers from a live run's results.  What
    that call retains is reported and is not a failure - a clean that deleted
    a concurrent run's intermediates would turn its live results into missing
    worker files, which is the one outcome worse than an entry surviving.

    Failures are **collected rather than returned on the first one**, so a
    single command reports every reason the build output is not clean instead
    of one reason per invocation.

    Returns:
        ``None`` when the build output directory is known to be empty
        afterwards, which includes the case where it never existed at all; a
        one-line reason otherwise, suitable for a log record and naming what
        could not be done.  The caller turns a reason into
        :attr:`ExitCode.ARTIFACT_FAILURE` and does not start the suite: the
        clean step's completion is a precondition of the run, not a detail of
        it.
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

    if stat.S_ISLNK(root_status.st_mode):
        return (
            f"the build output directory {root} is a symbolic link; emptying "
            "it would delete whatever it points at, which may lie anywhere "
            "outside this checkout, so it was not traversed and nothing was "
            "removed - pass --no-clean, or replace the link with a real "
            "directory"
        )
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
        # In a ``finally`` because every return above and any unexpected
        # exception must still give the descriptor back.
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
        retained: The live run directories, from
            :func:`~app.services.reclaim_workers_root`.  Empty in the normal
            case of a single run, which is why this is silent then.
    """
    if not retained:
        return
    logger.info(
        "Retained the intermediates of %d run(s) still in progress: %s",
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
    guarantee the descriptor path keeps except the closing of the check-then-use
    window, which cannot be closed without descriptor-relative calls: the
    caller has already refused a symlinked root, a symlinked entry is unlinked
    and never followed, each directory is confirmed to still resolve directly
    inside the root before it is walked, and the result is verified.  The
    residual race is stated rather than hidden: between the containment check
    and the walk, a privileged concurrent process could still substitute the
    entry.  A build output directory writable by a hostile process is outside
    what a build tool can defend, and the platform offers nothing stronger.

    Args:
        root: The build output directory, already established by the caller to
            be a real directory and not a symlink.
        expected: The ``lstat`` result the caller validated.  The root's
            identity is re-checked against it before **every** removal, which
            is what keeps a root replaced after that check from being
            traversed: the substitution is detected and the step fails
            closed, having removed nothing further.

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
            # The run service's directory - see the descriptor strategy for
            # why the clean step does not empty it itself.
            reason, live = reclaim_workers_root()
            retained.extend(live)
            if reason is not None:
                problems.append(reason)
            else:
                removed += 1
            continue

        try:
            if entry.is_symlink() or not entry.is_dir():
                # A symlink is removed as the link it is, whatever it points
                # at, so nothing outside the build output is ever reached.
                # Its own containment is the root identity check above: the
                # link sits directly in the checked directory, and unlinking
                # it never touches what it points at.
                entry.unlink()
            else:
                # A directory is walked, so where it *resolves* matters: a
                # junction or a link that slipped past the type test, and
                # anything reached through a relinked component, is refused.
                containment = _containment_problem(entry, root)
                if containment is not None:
                    logger.error("Clean refused: %s", containment)
                    problems.append(containment)
                    continue
                shutil.rmtree(entry)
        except FileNotFoundError:
            # Gone between the listing and the removal, from either the type
            # test or the removal itself.  That is this step's postcondition
            # and not a failure of it - see the same case in
            # :func:`_remove_descriptor_entries`.
            removed += 1
        except OSError as error:
            problem = f"could not remove {entry}: {error}"
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
    if stat.S_ISLNK(current.st_mode) or not stat.S_ISDIR(current.st_mode):
        return (
            f"{root} is no longer a directory, so it was replaced while it "
            "was being emptied and nothing further was removed"
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
    root.  A junction or a directory symlink that slipped past the type test,
    and any entry reached through a relinked intermediate component, fail here
    and are reported instead of being walked.

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


# --------------------------------------------------------------------------- #
# Reporting what a finished run reports
#
# Each helper below writes what the exit contract requires to be visible, and
# this file is the **single emitter** of every one of those facts.  The rule
# the three modules share, stated the same way in each: a fact an outcome
# carries is reported exactly once, by the command that reads the outcome.
#
# So the services return structured facts and do not log them.  A tolerated
# selection problem arrives on ``RunOutcome.parse_errors``, a dead shard's
# reason on ``RunOutcome.dead_shards``, the writers left unattempted on
# ``ReportOutcome.skipped`` and the artifacts that survived on
# ``ReportOutcome.written`` - and each is named here, beside the exit class it
# implies, rather than here *and* there.  Emitting from both layers, which is
# what this command used to do, turned one incident into several ERROR
# records under two logger names: a reader counting errors over-counted the
# run, and neither layer was the canonical account of anything.
#
# One fact is not an outcome field and is therefore not this file's to emit:
# the cause of a writer failure, with its traceback, which
# ``app/services/report_service.py`` reports at the point it catches the
# exception.  The record below is the *consequence* - the status, the writer
# and the destination it was producing - and it deliberately does not repeat
# that cause.
# --------------------------------------------------------------------------- #


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
        logger.error("Tolerated: %s", problem)


def _worker_status(outcome: RunOutcome) -> ExitCode:
    """Return the status a run's shard results imply, naming any dead shard.

    Each reason is logged here and nowhere else: the run service builds it -
    naming the shard, its scenario count and what went wrong - carries it on
    :attr:`~app.services.RunOutcome.dead_shards` and does not log it, so the
    count and the per-shard lines below are one account rather than a second
    copy of one.

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
        "Exit %d: %d of %d worker shard(s) produced no results",
        int(ExitCode.WORKER_DIED),
        len(outcome.dead_shards),
        outcome.worker_count,
    )
    for reason in outcome.dead_shards:
        logger.error("Incomplete shard: %s", reason)
    return ExitCode.WORKER_DIED


#: Printed in place of a failed writer's destination when the fan-out could not
#: resolve one.  Deliberately not path-shaped: a reader must not be able to
#: mistake it for somewhere to look.  Not a path literal, and not a second
#: spelling of one - the destination itself always arrives on the outcome.
_UNRESOLVED_DESTINATION: Final[str] = "an unresolved destination"


def _writer_failure_status(report: ReportOutcome) -> ExitCode:
    """Report a failed writer and return the status it implies.

    Nothing is rolled back here and nothing is rolled back anywhere else: the
    artifacts already written stay exactly where their writers put them, which
    is the writer-failure cause of :attr:`ExitCode.ARTIFACT_FAILURE`.  The
    failing writer is named **together with the destination it was
    producing**, the writers left unattempted are named, and the artifacts
    that survive are named, so a CI log identifies what the publisher will
    find without anyone re-running the command.

    The destination is read from the outcome, never re-derived: the fan-out
    resolved it from the artifact key ``app/utils/paths.py`` publishes, and
    resolving it a second time here would put a second spelling of one path in
    the port - which is why this file knows no artifact path at all.  It is
    optional on the outcome, so an unresolved destination degrades to readable
    text instead of printing a bare ``None``.

    What these records deliberately leave out is the exception itself.  The
    cause and its traceback are one fact reported once, by
    ``app/services/report_service.py`` at the point it caught the exception,
    because a traceback is the one thing the outcome cannot usefully carry
    here.  These records are the consequence of that failure - the status, the
    identity of what failed, what was skipped and what survived - and every
    field in them comes off the outcome.

    This is also what keeps one status from costing any information.  A writer
    failure and an empty merge share the status and share nothing else: the
    message below names a writer where the merge's names the merge, and the
    lines that follow it enumerate what was retained where an empty merge
    reports that nothing was written at all.

    Args:
        report: The fan-out's outcome, whose
            :attr:`~app.services.ReportOutcome.failed_writer` is set and whose
            :attr:`~app.services.ReportOutcome.failed_path` is that writer's
            intended destination when it could be resolved.

    Returns:
        :attr:`ExitCode.ARTIFACT_FAILURE`.
    """
    destination = (
        _UNRESOLVED_DESTINATION
        if report.failed_path is None
        else report.failed_path
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
            ", ".join(str(path) for path in report.written),
        )
    else:
        logger.error("No artifact had been written when the failure occurred")
    return ExitCode.ARTIFACT_FAILURE


def _publish(outcome: RunOutcome) -> ExitCode:
    """Turn a finished run into artifacts and a status.

    The three states of :attr:`~app.services.RunOutcome.result_set` are read
    apart here rather than collapsed into one truthiness test, because two of
    them look alike and mean opposite things: an **empty** document is a
    tag expression that selected nothing, which writes all four artifacts
    empty at :attr:`ExitCode.SUCCESS` so the publisher always has an input,
    while a **missing** document is a merge that produced nothing, which
    writes no artifact at :attr:`ExitCode.ARTIFACT_FAILURE`.  Under ``--rerun``
    a missing document is neither: a rerun writes nothing by design.

    Precedence, when several signals coexist, is the module docstring's: the
    artifact failure - whether its cause is the merge, a writer, or the
    intermediate storage the run needed - and then the dead worker.  Each
    cause reports itself on stderr in its own words before this function
    returns, so the single status names the class while the log names the
    cause.

    The run service's ``infrastructure_error`` is read at **two** points, and
    both are deliberate:

    * **Before the ``--rerun`` short-circuit.**  A rerun writes no artifact
      by design, so its ordinary outcome and a rerun that could not create a
      directory to work in are indistinguishable from the artifacts - and the
      second executed not one of the failures it was asked to re-run.  Reading
      the signal first is what keeps that from being reported as success.
    * **After the fan-out.**  When the run produced a document, the failure is
      that its intermediates could not be removed afterwards.  That must not
      cost a completed run its four artifacts, so the writers run first and
      the status is settled second.

    Args:
        outcome: The finished run.

    Returns:
        The status the run's artifacts, shards and intermediate storage imply.
    """
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
                target_root(),
            )
        else:
            logger.error(
                "Exit %d: no artifact was written and %s is left as the clean "
                "step left it",
                int(ExitCode.ARTIFACT_FAILURE),
                target_root(),
            )
        return ExitCode.ARTIFACT_FAILURE

    # One merged document, four independent writers, in the service's own
    # order: the two machine-read contracts first.  No path is passed - each
    # writer resolves its own destination.
    report = generate_reports(outcome.result_set)
    if not report.ok:
        return _writer_failure_status(report)

    logger.info(
        "Wrote %d artifact(s): %s",
        len(report.written),
        ", ".join(str(path) for path in report.written),
    )
    if infrastructure is not None:
        # The artifacts are written and kept; what is wrong is the workspace.
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
    per-worker document must never be left where the publisher can read it.  A
    ``finally`` inside the command callback cannot deliver that guarantee:
    Click rejects an unknown option, a malformed ``--tags`` expression and a
    non-positive ``--workers`` *while parsing*, before the callback is ever
    entered, so those three exits would leave the directory behind.

    :meth:`click.Command.main` is the outermost frame of an invocation - it is
    what the console script's ``__call__`` reaches, what ``CliRunner.invoke``
    calls, and what Click's own standalone-mode error handling and
    ``sys.exit`` live inside - so a ``finally`` there encloses parsing,
    ``--help``, every published status, an interrupt during parsing and an
    unexpected exception alike.  Overriding it is the whole of this class, and
    it changes nothing else about the command: the console entry point still
    points at the same object under the same name, which is what keeps
    ``pyproject.toml`` untouched by this concern.

    **This class holds the module's only cleanup call sites**, both of them
    into the one helper :func:`_remove_intermediates`: :meth:`main` for the
    console route and :meth:`make_context` for the parse boundary both routes
    share.  The callback holds none, deliberately, so nothing can disagree
    about whether cleanup ran or about what its failure means; the
    directory's preparation and removal are owned by
    ``app/services/test_run_service.py``, and this module only guarantees
    that the removal is *reached* on every exit path and that its failure is
    not silent.  What it asks for is this invocation's own intermediates plus
    any abandoned run's, never a directory a live run is still using.

    A cleanup failure **upgrades a success to**
    :attr:`ExitCode.ARTIFACT_FAILURE` and leaves every other status alone -
    the one asymmetry in this class, and the reason it catches anything at
    all.  Returning ``0`` while this run's intermediate documents are still in
    the workspace would be a false success, while overwriting a usage error's
    ``2`` or a dead worker's ``3`` with ``4`` would hide a higher-priority
    failure behind a tidy-up.
    """

    def main(self, *args: Any, **kwargs: Any) -> Any:
        """Run the command, removing this invocation's intermediates after it.

        Args:
            *args: Positional arguments for :meth:`click.Command.main`,
                forwarded unchanged - ``args``, ``prog_name`` and the rest of
                Click's own signature, which is deliberately not restated
                here so a Click upgrade cannot silently drop a parameter.
            **kwargs: Keyword arguments for :meth:`click.Command.main`,
                forwarded unchanged.

        Returns:
            Whatever :meth:`click.Command.main` returns, except that a
            successful return with a failed cleanup returns
            :attr:`ExitCode.ARTIFACT_FAILURE` instead.  In standalone mode -
            every real invocation - it does not return at all: it raises
            :exc:`SystemExit`.

        Raises:
            SystemExit: Click's own, with its status intact, unless that
                status is a success and the cleanup failed, in which case it
                carries :attr:`ExitCode.ARTIFACT_FAILURE`.
            BaseException: Anything else leaves this method unchanged, after
                the cleanup has been attempted and reported: an interrupt is
                the operator's decision, and an unexpected exception is a
                defect that must keep its traceback rather than become a
                published status.
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

        :meth:`main` covers the console entry point, but it is **not** the
        only way this command is parsed: when ``create_app()`` attaches it to
        the Flask CLI group, Click's group dispatch builds the child's
        context and invokes it directly, so a value rejected while parsing
        *there* never reaches ``main`` at all.  This is the boundary both
        routes share - ``main`` calls it too - so the guarantee holds
        wherever the parsing happens.  Cleanup being idempotent is what makes
        covering both harmless: on the console route this runs, and then
        ``main`` finds nothing left to do.

        Args:
            *args: Positional arguments for :meth:`click.Command.make_context`,
                forwarded unchanged.
            **kwargs: Keyword arguments for the same, forwarded unchanged.

        Returns:
            The context Click built.

        Raises:
            BaseException: Whatever parsing raised, unchanged and after the
                cleanup - a :exc:`click.UsageError` for a rejected value, or
                the :exc:`SystemExit` Click's own error handling produces.
                The status is never altered here: on this route there is no
                status yet to upgrade, and inventing one would mask the usage
                error itself.
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
         written is retained), or --clean could not empty the build
         output (nothing is executed). Each cause names itself on stderr

    A run needs a browser and a populated configuration.properties; the
    repository ships a template only, and a missing file is tolerated so that
    --dry-run and the report writers work without one.
    """
    # First, before any work of any kind: progress to stdout, diagnostics to
    # stderr, both line-buffered.  A Jenkins log stays live only because this
    # happens here rather than on the first message.
    configure_logging()

    context = click.get_current_context()

    # Unconditional removal of the per-worker intermediate directory is *not*
    # here.  It lives in :class:`_RunTestsCommand`, which encloses option
    # parsing as well as this function, because a value Click rejects while
    # parsing never reaches a ``finally`` written inside the callback.
    if rerun and _option_given(context, "tags"):
        # Non-zero, nothing executed, nothing written: raised before the
        # clean step and before the run service is reached.
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
        # Stated rather than silent, because the user asked for something
        # that is deliberately not being done.
        logger.info(
            "--clean is ignored under --rerun: the manifest a rerun reads "
            "lives in the build output and must survive"
        )

    clean_failure = _empty_build_output() if cleaning else None

    if clean_failure is not None:
        # The run stops here, and that is the point: the build output is in an
        # unknown state, so anything executed now would publish a mixture of
        # this run's artifacts and whatever the clean could not remove.
        # Nothing is executed, no writer is reached, and what survives is left
        # exactly where it is for an operator to look at.
        logger.error(
            "Exit %d: the build output directory could not be emptied, so "
            "the suite was not started, no artifact was written and nothing "
            "was deleted beyond what is reported above: %s",
            int(ExitCode.ARTIFACT_FAILURE),
            clean_failure,
        )
        exit_code = ExitCode.ARTIFACT_FAILURE
    else:
        # Selection, sharding, per-worker invocation and the merge - none of
        # which is this file's business.  The service exits no process and
        # raises nothing for a test outcome; every signal arrives on the
        # outcome, and the malformed-expression exception it documents cannot
        # reach here because the option callback rejected such a value while
        # Click was still parsing.
        outcome = run_suite(
            tags=tag_expression,
            browser=browser,
            workers=worker_count,
            dry_run=dry_run,
            rerun=rerun,
        )

        # Reported first and separately: these never change the status, and
        # reading them before the artifacts keeps the stderr account in the
        # order the run discovered things.
        _report_selection_problems(outcome)

        exit_code = _publish(outcome)

    if exit_code is ExitCode.SUCCESS:
        # The non-zero classes have already named themselves on stderr, each
        # carrying its status, so only the successful case needs a line here.
        logger.info("Finished with status %d", int(exit_code))

    # The one place a status leaves this command.  An unexpected exception -
    # one no documented contract predicts - is deliberately *not* mapped onto
    # a published class: it propagates with its traceback, so a defect is
    # never disguised as one of the four published statuses above.
    context.exit(int(exit_code))
