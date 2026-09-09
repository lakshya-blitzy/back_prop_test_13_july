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
The merge produces no result set at all                ``4``   none
A writer fails after earlier writers succeeded         ``5``   those already
                                                               written remain
=====================================================  ======  ==============

The concrete values are this file's contract - see :class:`ExitCode` - and
``1`` is deliberately never used, so a non-zero status always names its class.
Two consequences worth stating outright, because both look like optimisations
worth "fixing" and neither is:

* **A zero-scenario selection still writes all four artifacts, empty.**  The
  publisher's ``fileIncludePattern`` is narrowed to the single JSON report, so
  that file must exist for the third pipeline stage to have any input at all.
* **A writer failure rolls nothing back.**  Artifacts written before it stay
  exactly where their writers put them, and the failing writer is named on
  stderr.

Precedence, when several signals coexist, is this file's decision, since the
run service records every signal independently and encodes no order.  It is
``4`` then ``5`` then ``3``: the two artifact-facing failures outrank the
execution-facing one, because they describe what the publisher will find, and
the total absence of a document outranks a partial write.  Every signal is
logged whatever the status, so choosing a status discards no information.

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

Import boundary
---------------
``click``, the tag-expression grammar, and three of this port's own modules:
``app.services`` for the two services, ``app.utils.paths`` for the build
output directory and ``app.logging_config`` for the console contract.
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
8. **An empty merge** - exit ``4``, no artifact written, and the build output
   directory left exactly as the clean step left it.
9. **A writer failure** - exit ``5``, the artifacts written before it still
   present and unmodified, the failing writer named on stderr, and nothing
   deleted.
10. **Intermediate cleanup is unconditional** - after every scenario above and
    after a simulated ``KeyboardInterrupt``, the per-worker directory does not
    exist.
11. **``--clean`` behaviour** - with stale files in the build output
    directory, ``--clean`` empties it before the run, ``--no-clean`` leaves
    them, and under ``--rerun`` neither cleans.
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
16. **Coverage** - this module sits outside the four gated ``Makefile``
    scopes, so no numeric gate applies, but every row of the exit table above
    must be exercised.
"""

import logging
import shutil
from enum import IntEnum
from typing import Final

import click

# The same grammar the JVM used (``tag-expressions:4.1.0`` there,
# ``cucumber-tag-expressions`` 11.0.1 here), imported for validation only:
# a malformed expression is an invalid *option value*, so it is rejected at
# parse time by this file rather than surfacing as a traceback out of the run
# service, which documents ``TagExpressionError`` as its one propagating
# exception.  The expression itself is never evaluated here - selection is the
# service's work.
from cucumber_tag_expressions import TagExpressionError, TagExpressionParser

from app.logging_config import configure_logging
from app.services import (
    ReportOutcome,
    RunOutcome,
    cleanup_workers_dir,
    default_worker_count,
    generate_reports,
    run_suite,
)

# The build output directory, and the only path this module knows about.  It is
# reached through the accessor rather than assembled, because
# ``app/utils/paths.py`` owns every path in the port and a second spelling of
# this one is exactly how the ``--clean`` step, the writers and the artifact
# route would drift apart.
from app.utils.paths import target_root

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
        EMPTY_MERGE: Scenarios were selected and not one worker result could
            be read, so there is no document to write.  No artifact is
            written and the build output directory is left as the clean step
            left it.
        WRITER_FAILED: A report writer failed.  Artifacts written before it
            remain - nothing is rolled back - and the failing writer is named
            on stderr.
    """

    SUCCESS = 0
    USAGE_ERROR = 2
    WORKER_DIED = 3
    EMPTY_MERGE = 4
    WRITER_FAILED = 5


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
# --------------------------------------------------------------------------- #


def _empty_build_output() -> None:
    """Empty the build output directory, reproducing ``mvn clean test``.

    The directory itself is kept and its contents removed, so an emptied
    directory is observably empty rather than absent - which is the state the
    exit contract's empty-merge row refers to when it says the build output is
    "left as the clean step left it".  A missing directory is not created:
    there is nothing to clean, and the writers create their own parents.

    An entry that cannot be removed is **reported and survived**.  The exit
    table names no status for a failed clean, only three non-zero classes for
    situations the source could not have, and inventing a fourth would publish
    a status nothing has agreed to; the writers overwrite their own
    destinations regardless, so a stale entry is cosmetic.  That tolerance is
    the same shape as the source's own tolerance of a missing configuration
    file: report it, and carry on.

    Returns:
        ``None``.  What happened is reported on the progress and diagnostic
        streams, which is where a build log needs it; the command takes no
        decision from the result, because a clean has no bearing on the exit
        status.
    """
    root = target_root()

    if not root.exists():
        logger.info("Nothing to clean: %s does not exist", root)
        return
    if not root.is_dir():
        # A file occupying the directory's place.  Reported rather than
        # removed: deleting something that is not build output is not this
        # command's business, and the writers will fail loudly if it blocks
        # them, which is the writer-failure class.
        logger.error("Cannot clean %s: it is not a directory", root)
        return

    try:
        entries = sorted(root.iterdir())
    except OSError as error:
        logger.error("Cannot clean %s: %s", root, error)
        return

    removed = 0
    for entry in entries:
        try:
            if entry.is_dir() and not entry.is_symlink():
                # Covers the per-worker directory too, since it lives inside
                # the build output; the outer ``finally`` removes it again,
                # and that call is idempotent by contract.
                shutil.rmtree(entry)
            else:
                # A symlink to a directory is unlinked, never followed.
                entry.unlink()
        except OSError as error:
            logger.error("Could not remove %s: %s", entry, error)
        else:
            removed += 1

    logger.info("Cleaned %d item(s) from %s", removed, root)


# --------------------------------------------------------------------------- #
# Reporting what a finished run reports
#
# Each helper below writes what the exit contract requires to be visible, and
# each does so from this file rather than relying on the services' own log
# records.  That is deliberate: a caller - the unit suite included - may
# substitute either service, and the command's account of what it tolerated,
# which shard was incomplete and which writer failed must not depend on
# another module having logged it.  In a real run the same fact may therefore
# appear twice, once from the module that discovered it and once here, which
# is the cost of an account that is always complete.
# --------------------------------------------------------------------------- #


def _report_selection_problems(outcome: RunOutcome) -> None:
    """Report the problems a run tolerated, without changing its status.

    A feature that failed to parse, a missing or malformed rerun manifest and
    a missing feature directory all belong here: each is reported on stderr
    and each leaves the run at :attr:`ExitCode.SUCCESS`, matching the
    source's tolerance of a missing configuration file, which it logged before
    carrying on.

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


def _writer_failure_status(report: ReportOutcome) -> ExitCode:
    """Report a failed writer and return the status it implies.

    Nothing is rolled back here and nothing is rolled back anywhere else: the
    artifacts already written stay exactly where their writers put them, which
    is the writer-failure row of the exit contract.  The failing writer is
    named, the writers left unattempted are named, and the artifacts that
    survive are named, so a CI log identifies what the publisher will find
    without anyone re-running the command.

    Args:
        report: The fan-out's outcome, whose
            :attr:`~app.services.ReportOutcome.failed_writer` is set.

    Returns:
        :attr:`ExitCode.WRITER_FAILED`.
    """
    logger.error(
        "Exit %d: report writer %s failed: %r",
        int(ExitCode.WRITER_FAILED),
        report.failed_writer,
        report.error,
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
    return ExitCode.WRITER_FAILED


def _publish(outcome: RunOutcome) -> ExitCode:
    """Turn a finished run into artifacts and a status.

    The three states of :attr:`~app.services.RunOutcome.result_set` are read
    apart here rather than collapsed into one truthiness test, because two of
    them look alike and mean opposite things: an **empty** document is a
    tag expression that selected nothing, which writes all four artifacts
    empty at :attr:`ExitCode.SUCCESS` so the publisher always has an input,
    while a **missing** document is a merge that produced nothing, which
    writes no artifact at :attr:`ExitCode.EMPTY_MERGE`.  Under ``--rerun`` a
    missing document is neither: a rerun writes nothing by design.

    Precedence, when several signals coexist, is the module docstring's:
    empty merge, then writer failure, then dead worker.

    Args:
        outcome: The finished run.

    Returns:
        The status the run's artifacts and shards imply.
    """
    if outcome.rerun:
        # The Java rerun runner declared an empty plugin list, so a rerun
        # produces no artifact and modifies none.  The report service is not
        # merely given nothing to do - it is not invoked at all.
        logger.info(
            "Rerun finished: no artifact was written and none was modified, "
            "matching the empty plugin list of FailedTestRunner.java:9-12"
        )
        return _worker_status(outcome)

    if outcome.result_set is None or outcome.merge_produced_nothing:
        # Equivalent conditions by the run service's construction, and both
        # tested, because either alone is sufficient grounds: a fan-out needs
        # a document, and there is none.
        logger.error(
            "Exit %d: %d scenario(s) were selected and no worker result could "
            "be read, so no artifact was written and %s is left as the clean "
            "step left it",
            int(ExitCode.EMPTY_MERGE),
            outcome.selected_count,
            target_root(),
        )
        return ExitCode.EMPTY_MERGE

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
    return _worker_status(outcome)



# --------------------------------------------------------------------------- #
# The command
#
# Defined exactly once.  ``pyproject.toml``'s console script points at the
# object below, and ``create_app()`` attaches the same object to the Flask CLI
# group, so the two routes cannot diverge.
# --------------------------------------------------------------------------- #


@click.command(name=COMMAND_NAME)
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
      4  the merge produced no result set; no artifact is written
      5  a report writer failed; artifacts written before it are retained

    A run needs a browser and a populated configuration.properties; the
    repository ships a template only, and a missing file is tolerated so that
    --dry-run and the report writers work without one.
    """
    # First, before any work of any kind: progress to stdout, diagnostics to
    # stderr, both line-buffered.  A Jenkins log stays live only because this
    # happens here rather than on the first message.
    configure_logging()

    context = click.get_current_context()
    exit_code = ExitCode.SUCCESS

    # Everything from here runs under one ``finally``, so the per-worker
    # intermediate directory is removed before the command returns in *every*
    # case - success, each non-zero class, the usage error below, and an
    # interrupt.  That removal and the pipeline's narrowed publisher glob are
    # two halves of one guarantee: no intermediate result document is ever
    # visible to the Cucumber publisher.
    try:
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

        logger.info(
            "Starting the suite: tags=%s, browser=%s, workers=%d, "
            "dry-run=%s, rerun=%s, clean=%s",
            "cleared" if rerun else repr(tag_expression),
            "unset" if browser is None else browser,
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

        if cleaning:
            _empty_build_output()

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
    finally:
        # Idempotent by contract - the run service removes the same directory
        # in its own ``finally`` - and it never raises, so it cannot mask
        # whatever the run was already reporting.  An interrupt passes
        # straight through it, uncaught: stopping a run is the operator's
        # decision, not an outcome to translate into a status.
        cleanup_workers_dir()

    if exit_code is ExitCode.SUCCESS:
        # The non-zero classes have already named themselves on stderr, each
        # carrying its status, so only the successful case needs a line here.
        logger.info("Finished with status %d", int(exit_code))

    # The one place a status leaves this command.  An unexpected exception -
    # one no documented contract predicts - is deliberately *not* mapped onto
    # a published class: it propagates with its traceback, so a defect is
    # never disguised as one of the five outcomes above.
    context.exit(int(exit_code))

