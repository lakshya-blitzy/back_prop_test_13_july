"""Artifact production for one finished run - the port of the Cucumber plugin list.

The Java runner produced its reports by declaring four plugins on
``@CucumberOptions`` - one each of the ``html``, ``json`` and ``rerun``
formatters plus ``me.jvt.cucumber.report.PrettyReports``, every one of them
paired with the destination it wrote.  That declaration is reproduced verbatim
inside this repository at ``README.md:78-83``, which quotes the original
``CukesRunner.java:9-14``, and its four destinations are the four artifact
constants :mod:`app.utils.paths` declares.  It is not re-quoted here: this
module names no destination at all, so the plugin list is cited rather than
copied.

Cucumber-JVM attached those four as event listeners, and each wrote its own
artifact as the run progressed.  The port keeps the shape and moves the moment:
``app/services/test_run_service.py`` merges the per-worker documents into one
first, and :func:`generate_reports` then fans that single document out over the
four writers in :mod:`app.reporting`, so every artifact has exactly one
producer - specification section 0.3.3's *"one merged result set, four
independent writers, none aware of the others."*

This module is the fan-out and nothing besides.  It writes no file itself: each
writer owns its own I/O and resolves its own destination through
:mod:`app.utils.paths`, the port's sole owner of every path (specification
section 0.4.2).  That is why not one path, literal or otherwise, appears
anywhere below - not even in a docstring, where it would be the first step of
exactly the drift that module exists to prevent - and why ``base`` is handed to
each writer untouched rather than resolved on its behalf.

Writer order is a decision, and must not be re-sorted
-----------------------------------------------------
There is no execution order in the source to copy.  The four JVM plugins were
concurrent event listeners, and the plugin list cited above records a
*declaration* order only - so nothing in the original behaviour binds the port
here.

Order became observable all the same, because the section 0.4.1 exit contract
has a row for it: *"A writer fails after earlier writers succeeded -> the
artifacts written before the failure remain."*  It is therefore fixed on the
one criterion that actually distinguishes the four artifacts - whether a
machine reads them:

1. ``cucumber_json``   the JSON report.  The Jenkins publisher's **only**
   input: ``Jenkins:15`` narrows ``fileIncludePattern`` to exactly that one
   file, so if it is missing the CI stage has nothing at all to ingest.
2. ``rerun_txt``       the rerun manifest.  Machine input too: the second Java
   runner named that very manifest as its ``features`` source
   (``FailedTestRunner.java:11``), and this port's ``run-tests --rerun`` reads
   it back to select the scenarios that failed.
3. ``html_report``     the single self-contained page.  No automated consumer.
4. ``pretty_reports``  the PrettyReports tree.  No automated consumer.

The two machine-read contracts go first, so that a template fault in the third
writer or a missing asset in the fourth still leaves the publisher and the
rerun runner the inputs they read.  "Tidying" the sequence back to that
declaration order - which begins with the HTML page - would trade a
CI-visible artifact for a human-readable one at precisely the moment something
has already gone wrong.  The order lives in :data:`WRITER_SEQUENCE` and nowhere
else, so it can be asserted directly instead of being inferred from a sequence
of calls.

Failure
-------
:func:`generate_reports` stops at the first writer that raises, records what
happened, and returns.  Each of the three properties that follow maps to a row
of the exit contract:

* **Nothing is rolled back.**  Artifacts written before the failure stay on
  disk.  The run does not delete a completed artifact - there is no ``unlink``,
  ``rmtree`` or "clean" step here, and emptying the build-output directory
  belongs to ``app/cli.py``'s ``--clean``, which runs before the suite does.
* **The failing writer is named on stderr.**  The logger comes from the
  standard library, and ``app/logging_config.py`` routes ``WARNING`` and above
  to stderr, so an ``ERROR`` record lands there without this module importing
  that configuration or writing to a stream itself.
* **It returns rather than raises.**  ``app/cli.py`` owns every exit status and
  maps a failed outcome to its writer-failure class.  Only :exc:`Exception` is
  caught, so :exc:`KeyboardInterrupt` and :exc:`SystemExit` still propagate.

A *test* outcome is never a failure here.  ``pom.xml:25`` sets
``testFailureIgnore=true`` and all six publisher thresholds are ``-1``
(``Jenkins:15``), so a scenario that failed is ordinary input to this module;
the only failures it reports are its writers' own.

Nor is an empty run: one that selected no scenario still writes all four
artifacts, empty, so the narrowed publisher glob always finds a file to read.
That behaviour belongs to the writers, which already handle an empty document,
which is why there is no short-circuit below - see the comment inside
:func:`generate_reports`.

Deliberately absent
-------------------
* **Exit codes.**  ``app/cli.py`` defines and owns them.  Nothing here ends the
  process - there is no exit call of any kind below - and no precedence between
  a dead worker and a writer failure is encoded here either: this module reports
  its own outcome faithfully and the command line decides what it means.
* **Thresholds and sorting.**  The Jenkins publisher owns both
  (``Jenkins:15``).  ``sortingMethod: 'ALPHABETICAL'`` is a publisher *display*
  option that imposes nothing on the artifacts, so neither this service nor any
  writer sorts on its account.
* **A ``--rerun`` parameter, or any rerun detection.**  Under ``--rerun`` this
  service is **not invoked at all**: the second Java runner declared an empty
  plugin list, so a rerun writes no artifact and leaves the existing ones
  untouched.  That decision is ``app/cli.py``'s, and a flag here would move it
  into the wrong file.
* **Rebuilding reports from stored results.**  There is no ``generate-reports``
  command.  The source's only documented route to a report was to re-run the
  suite with a chosen plugin (``README.md:157``, ``README.md:161``), so a report
  comes from a live run's merged document and from nothing else.  This module
  reads no artifact - not one it wrote, and not one anybody else did.

Import boundary
---------------
The standard library, and the four writer entry points from the
:mod:`app.reporting` barrel.  Nothing else: no Flask, no Selenium, nothing from
``app/config.py`` or ``app/utils/properties.py`` - the dependency graph has no
services-to-configuration edge, and the properties file is reached only through
the configuration module, whose consumers are the step modules and the driver -
nothing from ``app/web/`` or ``app/automation/``, not the logging configuration
in ``app/logging_config.py`` (the logger comes from the standard library
instead, which is what keeps this boundary intact), and not the sibling
``app/services/test_run_service.py`` - the two services never import each other,
and ``app/cli.py`` connects them.  The dependency runs one way, ``SV --> RP`` in
the section 0.4.2 graph, so nothing in ``app/reporting/``, ``app/pages/`` or
``app/automation/`` may import this module.

Together with the absence of import-time side effects, of module-level mutable
state and of any environment read, that keeps :func:`generate_reports` callable
from a worker process which never builds a web application and never starts a
browser - and keeps it testable on its own, every destination reachable through
the injectable ``base`` parameter, which is the seam a test uses to point the
whole fan-out at a temporary directory.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, NamedTuple

# The barrel, not the six sibling modules, because specification section 0.4.2
# makes it the package's advertised surface and it documents this very loop
# (``app/reporting/__init__.py``, "The four writers drive in a loop").
from app.reporting import (
    write_cucumber_json,
    write_html_report,
    write_pretty_reports,
    write_rerun_txt,
)

if TYPE_CHECKING:  # pragma: no cover - resolved by a type checker, never at run time
    # Annotation only.  ``ResultSet`` is a plain ``dict[str, Any]`` alias that
    # ``app/reporting/__init__.py`` deliberately withholds from the barrel as
    # "annotations rather than API", so it is taken from its defining module -
    # the same guarded-import shape ``app/automation/waits.py`` uses for the
    # types it never touches at run time.  This module only passes the document
    # through; it never builds or inspects one.
    from app.reporting.events import ResultSet

__all__ = [
    "WRITER_SEQUENCE",
    "ReportOutcome",
    "WriterResult",
    "WriterSpec",
    "generate_reports",
]

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# The fan-out sequence
# --------------------------------------------------------------------------- #


class WriterSpec(NamedTuple):
    """One writer in the fan-out: a stable name, and the callable that writes.

    Attributes:
        name: Stable identifier for the writer.  It is not a display string:
            ``app/cli.py`` recognises a failure by it,
            :attr:`ReportOutcome.failed_writer` and
            :attr:`ReportOutcome.skipped` report it, and the test module
            asserts the fan-out order by it - so it is part of this module's
            contract and is not to be reworded.
        write: The writer entry point.  Called uniformly as
            ``write(result_set, base=base)`` and returns the
            :class:`~pathlib.Path` it wrote - a file for the first three
            writers, a directory for the report tree.

            ``base`` is passed **by keyword**, and that is a correctness
            requirement rather than a style choice: it is the second positional
            parameter of three of the writers but the third of
            :func:`app.reporting.write_rerun_txt`, whose second is ``path``, so
            a positional call would write the rerun manifest to a
            directory-shaped destination.  The barrel states the same rule.

            Typed ``Callable[..., Path]`` because the four writers agree on the
            document and ``base`` and then diverge - ``path`` against
            ``directory``, and the rendering arguments only the two HTML writers
            accept - none of which this module ever supplies.
    """

    name: str
    write: Callable[..., Path]


#: The four writers, in the order :func:`generate_reports` drives them: the two
#: machine-read contracts first.  This constant is the single home of that
#: order - the module docstring explains why it is what it is, and why it must
#: not be re-sorted into the plugin-declaration order of ``README.md:78-83``.
WRITER_SEQUENCE: Final[tuple[WriterSpec, ...]] = (
    WriterSpec(name="cucumber_json", write=write_cucumber_json),
    WriterSpec(name="rerun_txt", write=write_rerun_txt),
    WriterSpec(name="html_report", write=write_html_report),
    WriterSpec(name="pretty_reports", write=write_pretty_reports),
)


# --------------------------------------------------------------------------- #
# What the fan-out reports back
#
# Both are frozen, because an outcome describes a fan-out that has already
# happened: ``app/cli.py`` reads it to pick an exit status and must not be able
# to alter the record on the way.  The tuple fields are immutable for the same
# reason, and make the defaults below safe to declare inline.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class WriterResult:
    """What one writer did - exactly one of :attr:`path` or :attr:`error`.

    Attributes:
        path: The path the writer returned, or ``None`` if it raised.  It is the
            writer's own return value, never a path this module built: the
            three file writers return their file and
            :func:`app.reporting.write_pretty_reports` returns the directory it
            wrote, which is the report tree's ``cucumber-html-reports``
            sub-directory rather than the artifact root.  That asymmetry is the
            writers' business and is simply recorded here.
        error: The exception the writer raised, or ``None`` on success.  Typed
            :exc:`BaseException` for the widest contract with a consumer, though
            only an :exc:`Exception` is ever caught and stored (see
            :func:`generate_reports`).
    """

    name: str
    path: Path | None = None
    error: BaseException | None = None


@dataclass(frozen=True)
class ReportOutcome:
    """The result of one fan-out over :data:`WRITER_SEQUENCE`.

    Attributes:
        results: One :class:`WriterResult` per writer **attempted**, in
            :data:`WRITER_SEQUENCE` order.  Shorter than that sequence exactly
            when a writer failed, since the fan-out stops there.
        written: The paths successfully written, in the same order.  Every entry
            is a writer's return value, and every one of them is still on disk:
            a later failure never removes an earlier artifact.
        failed_writer: :attr:`WriterSpec.name` of the first writer that failed,
            or ``None`` if all four succeeded.
        error: That writer's exception, or ``None`` if all four succeeded.
        skipped: Names of the writers never attempted because of the failure, in
            :data:`WRITER_SEQUENCE` order.  Empty when the fan-out completed, and
            also when the writer that failed was the last one.
    """

    results: tuple[WriterResult, ...]
    written: tuple[Path, ...]
    failed_writer: str | None = None
    error: BaseException | None = None
    skipped: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """Whether every writer succeeded.

        Returns:
            ``True`` if no writer failed.  A run whose scenarios failed still
            reports ``True``: ``testFailureIgnore=true`` (``pom.xml:25``) and the
            six ``-1`` publisher thresholds (``Jenkins:15``) keep a test outcome
            out of the exit status, so the only thing this flag describes is
            whether the four artifacts were produced.
        """
        return self.failed_writer is None


# --------------------------------------------------------------------------- #
# The fan-out
# --------------------------------------------------------------------------- #


def generate_reports(
    result_set: ResultSet,
    *,
    base: Path | str | None = None,
) -> ReportOutcome:
    """Write the four report artifacts from one merged result document.

    Drives :data:`WRITER_SEQUENCE` in order, calling each writer as
    ``write(result_set, base=base)``, and stops at the first one that raises.
    The function never raises on a writer's behalf and never deletes anything:
    artifacts written before a failure remain on disk, which is the section
    0.4.1 exit contract's writer-failure row, and ``app/cli.py`` turns the
    returned outcome into an exit status.

    Args:
        result_set: The merged result document, in the internal schema
            ``app/reporting/events.py`` owns.  Treated as read-only: the same
            object is handed to all four writers, so a mutation here would
            corrupt the input of every writer that had not run yet.
        base: Directory the artifact paths resolve against, passed through
            untouched and defaulting to ``None``, which each writer resolves to
            the working directory through :mod:`app.utils.paths` - the same
            default every accessor there applies.  It is the only override
            mechanism, and the seam a test uses to redirect the whole fan-out
            into a temporary directory.

    Returns:
        A :class:`ReportOutcome`.  On success its :attr:`~ReportOutcome.results`
        holds four entries, :attr:`~ReportOutcome.written` the four paths and
        :attr:`~ReportOutcome.ok` is ``True``.  On failure the results end with
        the writer that raised, :attr:`~ReportOutcome.failed_writer` and
        :attr:`~ReportOutcome.error` describe it, and
        :attr:`~ReportOutcome.skipped` names the writers left unattempted.

    Raises:
        KeyboardInterrupt: Propagated untouched - an interrupt is the operator
            stopping the run, not an artifact-production failure.
        SystemExit: Propagated untouched, for the same reason.  Every
            :exc:`Exception` a writer raises is caught and reported instead.
    """
    results: list[WriterResult] = []
    written: list[Path] = []

    # No empty-result-set guard here, and its absence is deliberate: a run that
    # selected no scenario must still write all four artifacts, empty, so that
    # the Jenkins publisher - whose glob ``Jenkins:15`` narrows to the single
    # JSON file - always has an input, which is the zero-scenario row of the
    # section 0.4.1 exit contract.  The writers already handle an empty
    # document (the JSON one emits an empty list), so an early return or a
    # truthiness check on the features would silently break that row while
    # looking like an optimisation.  Fan out unconditionally.
    for index, spec in enumerate(WRITER_SEQUENCE):
        try:
            # ``base`` by keyword - see WriterSpec.write.  No ``path`` or
            # ``directory`` override is ever passed: each writer resolves its
            # own destination, so this module owns no path at all.
            path = spec.write(result_set, base=base)
        except Exception as exc:  # noqa: BLE001 - the boundary is the point
            # Broad on purpose.  The writers document OSError, and the tree
            # writer additionally jinja2.TemplateError and FileNotFoundError,
            # but this is the outermost boundary of artifact production: any
            # exception whatever has to become a reported outcome rather than a
            # traceback out of the command line.  ``Exception`` and not
            # ``BaseException``, so an interrupt still stops the run.
            skipped = tuple(later.name for later in WRITER_SEQUENCE[index + 1 :])
            results.append(WriterResult(name=spec.name, error=exc))

            # ERROR, so ``app/logging_config.py`` routes it to stderr, naming
            # the writer that failed as the exit contract requires.  ``exc_info``
            # carries the traceback with it, because the cause of a failed write
            # is diagnosed from nothing else.
            logger.error(
                "Report writer %s failed: %r", spec.name, exc, exc_info=exc
            )
            if skipped:
                logger.error(
                    "Report writers not attempted after %s failed: %s",
                    spec.name,
                    ", ".join(skipped),
                )

            # Return, rather than raise or roll back.  The paths already in
            # ``written`` stay exactly where their writers put them.
            return ReportOutcome(
                results=tuple(results),
                written=tuple(written),
                failed_writer=spec.name,
                error=exc,
                skipped=skipped,
            )

        results.append(WriterResult(name=spec.name, path=path))
        written.append(path)
        # INFO, so the same handler split sends progress to stdout; one line per
        # artifact, both streams being line-buffered for capture by CI.
        logger.info("Report writer %s wrote %s", spec.name, path)

    return ReportOutcome(results=tuple(results), written=tuple(written))
