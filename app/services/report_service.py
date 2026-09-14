"""Artifact production for one finished run: the four writers, driven in order.

The Java runner declared four report plugins on ``@CucumberOptions`` - the
``html``, ``json`` and ``rerun`` formatters plus PrettyReports, each paired
with its destination (``CukesRunner.java:9-14``, AAP 0.4.1) - and ran them as
listeners during the run.  Here ``test_run_service`` merges the per-worker
documents first and :func:`generate_reports` fans that one document over the
four writers of :mod:`app.reporting`, in AAP 0.3.3's shape: one merged result
set, four independent writers, none aware of the others.

This module writes no file and spells no path - each writer resolves its own
destination through :mod:`app.utils.paths`, the port's sole owner of every path
(AAP 0.4.2), so ``base`` reaches it untouched.  The one path resolved here is
the intended destination of a writer that *failed*, and it never reaches a
writer: the AAP 0.4.1 writer-failure row requires the failing writer to be
named, while a template or model exception need not mention a path itself.

Writer order is the port's own decision, the plugin list recording a
declaration order only, and the AAP 0.4.1 row "the artifacts written before the
failure remain" makes it observable.  :data:`WRITER_SEQUENCE` therefore drives
the two machine-read artifacts first - the JSON report, the Jenkins publisher's
only input (``Jenkins:15`` narrows ``fileIncludePattern`` to it), then the
rerun manifest ``run-tests --rerun`` reads back as ``FailedTestRunner.java:11``
did - so a fault in the HTML page or the report tree, neither of which has an
automated consumer, cannot cost those two; do not re-sort it.

Nothing here rolls back or deletes, and every :exc:`Exception` a writer raises
becomes a :class:`ReportOutcome` rather than an escape, while
``KeyboardInterrupt`` and ``SystemExit`` propagate untouched.  Artifacts written
before a failure stay on disk, one ERROR record names the writer that failed,
and the outcome is reported once, by ``app/cli.py``.  A *test* outcome is never
a failure (``testFailureIgnore=true`` at ``pom.xml:25``, six ``-1`` thresholds
at ``Jenkins:15``), and a scenario-less run still writes all four, empty.

That resolved path is carried on :attr:`ReportOutcome.failed_path`, for a
caller with something to do about it.  What the **log records** name is a
second rendering of the same identity, :meth:`WriterSpec.artifact_id` - the
relative spelling :attr:`app.utils.paths.ArtifactSpec.relpath` carries,
again from that module's own table.  A console log is archived and shared, so the absolute location of the
workspace a run executed in is disclosure rather than diagnosis (CWE-200), and
the relative form is the one a reader acts on in any case: it is what
``README.md`` quotes and what the publisher's narrowed ``fileIncludePattern``
matches (``Jenkins:15``).  Neither rendering is a path this module spelled.

One fact the fan-out owns: when the run was reported
----------------------------------------------------
A run has one generation time, and this is the layer that can say so, because
it is the last point at which the four artifacts are still one thing.  The
stamp is therefore resolved **once, before the loop** - from the document, whose
``generated_at`` the collector writes at close and the merge keeps the latest
of, or, for a document carrying none, from a single clock reading here - and
every writer is handed one document that carries it.  **No writer has a clock
of its own.**  While one did, an empty run produced a generation time on the
self-contained page and none in the report tree, whose Date cell is deliberately
result-backed, so one document described itself two different ways and the value
changed on every render; a stamp resolved here cannot do either.  It travels
*in the document* rather than as a per-writer rendering argument, which is what
keeps the call shape uniform for all four writers and leaves the two machine-read
contracts untouched by it.

One thing this module does do with a path, and the line it draws
----------------------------------------------------------------
It *resolves* one.  :attr:`WriterSpec.artifact_key` carries each writer's
artifact **identity** - the key :mod:`app.utils.paths` publishes for it, taken
from that module's own constant - and :meth:`WriterSpec.destination` asks that
module to turn the identity into a path, for exactly one purpose: so that a
writer failure can say *where* the writer was writing.  The section 0.4.1 exit
contract requires the failing writer to be named, and a diagnostic that names a
writer without its destination cannot be acted on, because a template fault or
a model error need not mention a path of its own.

The invariant is therefore unchanged, and stated precisely: this module
contains no path and constructs none - it holds four keys and calls one
resolver, so every destination it names is by construction the destination that
writer's own accessor produced.  Nothing resolved here is ever passed to a
writer, so the writers keep sole responsibility for where they write; the
resolution happens only in the failure path, and only to describe it.

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
happened, and returns.  Three of the properties that follow map to a row of the
exit contract, and the fourth is why this service needs no recovery step of its
own:

* **Nothing is rolled back.**  Artifacts written before the failure stay on
  disk.  The run does not delete a completed artifact - there is no ``unlink``,
  ``rmtree`` or "clean" step here, and emptying the build-output directory
  belongs to ``app/cli.py``'s ``--clean``, which runs before the suite does.
* **Repairing a half-written artifact is no part of this service's job**, and
  that is a property of the writers rather than a gap here.  The two HTML
  writers publish atomically and say so in their own documentation: the
  self-contained page is renamed onto its destination from a temporary in that
  destination's own directory, and the report *tree* is built in a staging
  sibling, validated whole, and swapped into place by rename - so a fault in
  either leaves that artifact at its previous complete generation or absent,
  never truncated and never a mixture of two runs.  Whether the same holds of
  each other writer is that writer's contract to state; this module inspects,
  repairs and removes nothing on disk either way.  What it does own is the
  order below: the two machine-read contracts are produced first, so a fault in
  the third or fourth writer cannot cost the publisher and the rerun runner the
  inputs they read.
* **The failing writer and its destination are named on stderr.**  The logger
  comes from the standard library, and ``app/logging_config.py`` routes
  ``WARNING`` and above to stderr, so an ``ERROR`` record lands there without
  this module importing that configuration or writing to a stream itself.  One
  record carries all three facts - writer, destination and exception, with the
  traceback attached - and :attr:`ReportOutcome.failed_path` carries the
  destination onward so ``app/cli.py`` can name it in its exit-class record
  without deriving a path of its own.

  That single record is the whole of what this module says about a failure,
  and the boundary is a rule rather than an accident: **the cause and its
  traceback are reported where the exception was caught, and every fact the
  outcome carries is reported once by the command that reads the outcome.**
  So the writers left unattempted are named by ``app/cli.py`` from
  :attr:`ReportOutcome.skipped` and not here, and the artifacts that survived
  are named there from :attr:`ReportOutcome.written`.  Naming them in both
  places - which this module did until the duplication was reviewed - turns
  one incident into several ERROR records with no canonical owner, which
  inflates the error count a CI console shows and obscures how many things
  actually went wrong.
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
The standard library, the four writer entry points from the
:mod:`app.reporting` barrel, the timestamp format
:mod:`app.reporting.events` owns - taken from that module because the barrel
advertises the writers and the schema's operations rather than its formatting
helper, and used for the one generation stamp above so an artifact cannot tell
a resolved stamp from a result-backed one - and from :mod:`app.utils.paths` the
four artifact keys, the artifact table a record takes its relative
identifier from, and the resolver a failure diagnostic names its destination
with: the ``SV --> RP`` and ``SV --> UT`` edges of the section 0.4.2 graph, and
no others.  Nothing else: no Flask, no Selenium, nothing from ``app/config.py`` or
``app/utils/properties.py`` - the dependency graph has no
services-to-configuration edge, and the properties file is reached only through
the configuration module, whose consumers are the step modules and the driver -
nothing from ``app/web/`` or ``app/automation/``, not the logging configuration
in ``app/logging_config.py`` (the logger comes from the standard library
instead, which is what keeps this boundary intact), and not the sibling
``app/services/test_run_service.py`` - the two services never import each other,
and ``app/cli.py`` connects them.  The dependencies run one way, so nothing in
``app/reporting/``, ``app/pages/``, ``app/automation/`` or ``app/utils/`` may
import this module.

Together with the absence of import-time side effects, of module-level mutable
state and of any environment read, that keeps :func:`generate_reports` callable
from a worker process which never builds a web application and never starts a
browser - and keeps it testable on its own, every destination reachable through
the injectable ``base`` parameter, which is the seam a test uses to point the
whole fan-out at a temporary directory.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Final, NamedTuple, Protocol, runtime_checkable

from app.reporting import (
    write_cucumber_json,
    write_html_report,
    write_pretty_reports,
    write_rerun_txt,
)

# The one timestamp format in the project, from the module that owns the result
# schema this fan-out passes through.  Taken from the defining module rather
# than the barrel because the barrel deliberately advertises the writers and
# the schema's own operations, not its formatting helper; the edge is still the
# ``SV --> RP`` one, and this module reads no clock other than the single
# generation stamp :func:`generate_reports` resolves with it.
from app.reporting.events import format_timestamp

# The artifact *identities*, and the one resolver that turns an identity into a
# destination.  Imported for a diagnostic and for nothing else: this module
# still passes no path to a writer, and it spells none - the four constants
# below are the keys ``app/utils/paths.py`` publishes, so the destination a
# failure names is by construction the destination the writer resolved for
# itself (specification section 0.4.2, "Artifact paths are owned by
# ``app/utils/paths.py``"; section 0.4.2's graph carries the ``SV --> UT`` edge
# this import travels).
from app.utils.paths import (
    ARTIFACT_SPECS,
    CUCUMBER_JSON_NAME,
    CUCUMBER_REPORTS_HTML_NAME,
    PRETTY_REPORTS_DIR_NAME,
    RERUN_TXT_NAME,
    artifact_path,
)

if TYPE_CHECKING:  # pragma: no cover - resolved by a type checker, never at run time
    # Annotation only.  ``ResultSet`` is a plain ``dict[str, Any]`` alias that
    # ``app/reporting/__init__.py`` deliberately withholds from the barrel as
    # "annotations rather than API", so it is taken from its defining module -
    # the same guarded-import shape ``app/automation/waits.py`` uses for the
    # types it never touches at run time.  The document is passed through
    # rather than built: the one key this module reads is ``generated_at``, and
    # the one document it may construct is a shallow copy of the caller's
    # carrying that stamp - see :func:`_document_for_fan_out`.
    from app.reporting.events import ResultSet

__all__ = [
    "WRITER_SEQUENCE",
    "PublicationBoundaryLost",
    "PublicationGuard",
    "ReportOutcome",
    "WriterResult",
    "WriterSpec",
    "generate_reports",
]

logger = logging.getLogger(__name__)

_UNRESOLVED_DESTINATION: Final[str] = "an unresolved destination"

#: The result document's generation-stamp key, in the internal schema
#: ``app/reporting/events.py`` owns - the one key this module reads, and the one
#: it may set on a copy.  Named once here so the fan-out spells it in exactly
#: one place.
_GENERATED_AT_KEY: Final[str] = "generated_at"


# --------------------------------------------------------------------------- #
# The publication boundary
#
# The four artifacts sit at fixed paths that every run in a checkout shares, so
# two runs publishing at once leave a workspace holding a mixture of both -
# this run's JSON beside that run's HTML, each naming scenarios, step arguments
# and screenshots from a different execution.  What prevents that is the claim
# the command line holds on the build output from before the clean until after
# this fan-out, and what this module adds is the *check*: the claim is verified
# immediately before each writer publishes, so a run that has lost it stops
# instead of writing into a workspace another run has taken over.
#
# The claim is passed in rather than acquired here, and it is read through the
# protocol below rather than by its type, because specification section 0.4.2
# fixes the dependency graph: the two services never import each other and
# ``app/cli.py`` connects them.  A structural protocol is what lets this module
# verify the boundary without an import that graph forbids.
#
# What this module deliberately does **not** do is render the four artifacts
# into a private generation and promote them onto their final paths itself.
# Three frozen contracts forbid it, and each of them independently:
#
# * **Specification section 0.4.2** - "Artifact paths are owned by
#   ``app/utils/paths.py``.  Every writer, the artifact route, the clean step
#   and the per-worker invocation take their paths from it, and no other Python
#   module contains a path literal."  Promotion means this module deriving a
#   staged artifact location and then *writing onto* the four final paths, so
#   the destinations would stop being the ones each writer resolved for itself.
# * **Sections 0.3.3 and 0.4.1** - one merged result set, four independent
#   writers, and "each artifact has exactly one producer".  Promotion makes
#   this module the producer of all four final paths.
# * **Section 0.1.1** - "The four artifact paths do not move."  A genuinely
#   atomic *set* switch needs the visible artifacts to sit behind an
#   indirection that can be swapped in one operation, which is precisely a
#   move; ``Jenkins:15``'s narrowed publisher glob reads one of those paths
#   directly.
#
# What that leaves is not a gap where the threat was.  Two runs mixing their
# reports is prevented by the claim, which spans clean, run and publication.
# Within one run, each artifact is replaced atomically by its own writer - the
# self-contained HTML page through a temporary file and :func:`os.replace`, the
# report tree through a staged sibling renamed into place - and every machine
# consumer reads exactly one of them: the Jenkins publisher the JSON report,
# ``--rerun`` the manifest, the HTTP views the JSON report.  None of them can
# observe a cross-artifact mixture, and AAP section 0.4.1's writer-failure row
# requires the artifacts written before a failure to *remain*, which an
# all-or-nothing promotion would contradict.
#
# The claim is therefore checked before each writer **and once more after the
# last one**, so a run that lost the workspace part-way through publication
# says so instead of reporting four artifacts it cannot vouch for.
# --------------------------------------------------------------------------- #


@runtime_checkable
class PublicationGuard(Protocol):
    """Something that can say whether this run still owns the build output.

    Structural rather than nominal, so the fan-out can be handed the command
    line's run lock without this module importing the service that defines it
    (specification section 0.4.2).  Anything answering :meth:`is_held` will do,
    which is also what makes the boundary testable with a two-line double.
    """

    def is_held(self) -> bool:
        """Return whether the claim is still exclusively this process's.

        Returns:
            ``True`` while this run may publish; ``False`` once it may not,
            which stops the fan-out at the next writer.
        """
        ...


class PublicationBoundaryLost(RuntimeError):
    """Raised into a :class:`ReportOutcome` when the run's claim is gone.

    Never raised out of :func:`generate_reports` - like every other cause of a
    failed fan-out it becomes a reported outcome - but a real exception type
    rather than a string, so that :attr:`ReportOutcome.error` carries the same
    kind of value whatever stopped the publication, and a consumer can tell
    this cause from a writer's own failure by type.
    """


# --------------------------------------------------------------------------- #
# The fan-out sequence
# --------------------------------------------------------------------------- #


class WriterSpec(NamedTuple):
    """One writer in the fan-out: a stable name, the callable, the artifact.

    Attributes:
        name: Stable identifier, not a display string: ``app/cli.py``
            recognises a failure by it and :class:`ReportOutcome` reports it,
            so rewording one is a change of contract.
        artifact_key: The :attr:`app.utils.paths.ArtifactSpec.key` of the
            artifact this writer produces, taken from the constant
            :mod:`app.utils.paths` publishes for it.  It is an *identity*, not
            a destination: :meth:`destination` resolves it only for the failure
            diagnostic AAP 0.4.1 requires, and the writer still resolves where
            it writes.  For ``pretty_reports`` the identity is the report
            tree's root while :func:`app.reporting.write_pretty_reports`
            returns the :data:`app.utils.paths.PRETTY_HTML_SUBDIR`
            sub-directory it filled; for the other three the two coincide.
        write: The writer entry point, called as
            ``write(result_set, base=base)`` and returning what it wrote - a
            file for the first three writers, a directory for the report tree.
            ``base`` goes **by keyword** for correctness: it is the second
            positional parameter of three writers but the third of
            :func:`app.reporting.write_rerun_txt`, whose second is ``path``, so
            a positional call would send the manifest to a directory-shaped
            destination.  Typed ``Callable[..., Path]`` because the four
            signatures diverge past ``base``, in arguments never supplied here.
    """

    name: str
    write: Callable[..., Path]
    artifact_key: str

    def destination(self, base: Path | str | None = None) -> Path | None:
        """Resolve the artifact this writer is meant to produce.

        The *intended* destination, from :attr:`artifact_key` through
        :func:`app.utils.paths.artifact_path`, for a diagnostic that has to say
        where a writer was writing - not what the writer returned
        (:attr:`WriterResult.path` is that).  For ``pretty_reports`` this key
        resolves to the report tree's root while the writer returns the
        sub-directory it filled; for the other three the two coincide.

        Called from the one path where an exception is already being reported,
        so every :exc:`Exception` is suppressed rather than displacing the
        writer failure being described: an unknown key, or a ``base`` that
        cannot be combined into a path, yields ``None`` and a ``DEBUG`` record,
        and the caller names :data:`_UNRESOLVED_DESTINATION` instead.
        :exc:`KeyboardInterrupt` and :exc:`SystemExit` still propagate.

        Args:
            base: Directory to resolve against, exactly as handed to
                :func:`generate_reports` and to the writer, so a diagnostic
                names the destination that writer was working on.  ``None``
                resolves against the working directory, as the accessors do.

        Returns:
            The intended artifact path, or ``None`` if unresolved.
        """
        try:
            return artifact_path(self.artifact_key, base=base)
        except Exception:  # noqa: BLE001 - a diagnostic may not raise
            # DEBUG, not ERROR: the incident being reported is the writer's
            # failure, and this is a note about the *description* of it.  The
            # traceback is kept because an unresolvable artifact key is a
            # programming error in this module's own table.
            logger.debug(
                "Could not resolve the destination of report writer %s from "
                "artifact key %r",
                self.name,
                self.artifact_key,
                exc_info=True,
            )
            return None

    def artifact_id(self) -> str:
        """Return the repository-relative identifier of this writer's artifact.

        The name a *log record* calls the artifact by, as distinct from
        :meth:`destination`, which is the absolute path a caller may need to
        act on.  It is :attr:`app.utils.paths.ArtifactSpec.relpath` for this
        writer's key - the four relative identifiers that module publishes -
        so it is the spelling ``README.md`` quotes, the spelling the Jenkins
        publisher's narrowed ``fileIncludePattern`` matches (``Jenkins:15``),
        and the spelling that does not publish the absolute layout of the
        workspace a run happened to execute in (CWE-200).

        Resolved from :data:`app.utils.paths.ARTIFACT_SPECS` rather than
        spelled here, so this module still contains no path of its own and the
        identifier cannot drift from the destination the writer resolves.

        **Never raises**, for the same reason :meth:`destination` does not: it
        is read while a failure is already being reported, and an
        unrecognisable key degrades to the key itself - which still names the
        artifact usefully - rather than displacing the incident.

        Returns:
            The relative identifier, or :attr:`artifact_key` when no spec
            declares that key.
        """
        for spec in ARTIFACT_SPECS:
            if spec.key == self.artifact_key:
                return spec.relpath
        return self.artifact_key


#: The four writers in the order :func:`generate_reports` drives them, and the
#: single home of that order: the two machine-read artifacts first, for the
#: reason the module docstring gives, and not the declaration order of
#: ``CukesRunner.java:9-14``.  Each entry's ``artifact_key`` is the constant
#: :mod:`app.utils.paths` publishes for its artifact, so a writer cannot be
#: registered without a destination a diagnostic can name.
WRITER_SEQUENCE: Final[tuple[WriterSpec, ...]] = (
    WriterSpec(
        name="cucumber_json",
        write=write_cucumber_json,
        artifact_key=CUCUMBER_JSON_NAME,
    ),
    WriterSpec(
        name="rerun_txt",
        write=write_rerun_txt,
        artifact_key=RERUN_TXT_NAME,
    ),
    WriterSpec(
        name="html_report",
        write=write_html_report,
        artifact_key=CUCUMBER_REPORTS_HTML_NAME,
    ),
    WriterSpec(
        name="pretty_reports",
        write=write_pretty_reports,
        artifact_key=PRETTY_REPORTS_DIR_NAME,
    ),
)


@dataclass(frozen=True)
class WriterResult:
    """What one writer did - exactly one of :attr:`path` or :attr:`error`.

    Attributes:
        path: The path the writer returned, or ``None`` if it raised.  It is the
            writer's own return value, never a path this module built: the
            three file writers return their file and
            :func:`app.reporting.write_pretty_reports` returns the directory it
            wrote, which is the report tree's
            :data:`app.utils.paths.PRETTY_HTML_SUBDIR` sub-directory rather
            than the artifact root.  That asymmetry is the
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

    Frozen, because it records a fan-out that has already happened and
    ``app/cli.py`` reads it to choose an exit status.

    Attributes:
        results: One :class:`WriterResult` per writer **attempted**, in
            :data:`WRITER_SEQUENCE` order - shorter than that sequence exactly
            when a writer failed, since the fan-out stops there.
        written: The paths successfully written, in the same order.  Each is a
            writer's own return value and each is still on disk: a later
            failure never removes an earlier artifact.
        failed_writer: :attr:`WriterSpec.name` of the first writer that failed,
            or ``None`` if all four succeeded.
        error: That writer's exception, or ``None`` if all four succeeded.
        skipped: Names of the writers never attempted because of the failure, in
            :data:`WRITER_SEQUENCE` order.  Empty when the fan-out completed, and
            also when the writer that failed was the last one.
        failed_path: The destination the writer named by :attr:`failed_writer`
            was meant to produce, from :meth:`WriterSpec.destination` - the
            *intended* artifact, which for the report tree is its root rather
            than the sub-directory that writer returns.  ``None`` when nothing
            failed, and also when the destination could not be resolved, so a
            consumer must handle the absence rather than assume a path.

            It is carried on the outcome so that ``app/cli.py`` can name the
            destination in its exit-class record without resolving an artifact
            path of its own: the command line owns no path but the build output
            root, and re-deriving this one there is exactly the drift
            :mod:`app.utils.paths` exists to prevent.  Declared last so every
            existing construction of this class, positional or by keyword,
            stays valid.
        boundary_lost: ``True`` when the caller's claim on the build output was
            no longer held at the end of the fan-out, whether or not a writer
            also failed.  All four artifacts may be on disk in that case and
            are left there; what cannot be vouched for is that they are all
            *this* run's, because another run may have claimed the workspace
            part-way through.  :attr:`ok` is ``False`` and ``app/cli.py``
            reports the artifact-failure class, naming this cause rather than a
            writer.
    """

    results: tuple[WriterResult, ...]
    written: tuple[Path, ...]
    failed_writer: str | None = None
    error: BaseException | None = None
    skipped: tuple[str, ...] = ()
    failed_path: Path | None = None
    boundary_lost: bool = False

    @property
    def ok(self) -> bool:
        """Whether every writer succeeded under a claim that held throughout.

        Returns:
            ``True`` if no writer failed and the caller's claim on the build
            output was still held when the last one finished.  A run whose
            scenarios failed still reports ``True``:
            ``testFailureIgnore=true`` (``pom.xml:25``) and the six ``-1``
            publisher thresholds (``Jenkins:15``) keep a test outcome out of
            the exit status, so the only thing this flag describes is whether
            the four artifacts were produced, and produced into a workspace
            this run still owned.
        """
        return self.failed_writer is None and not self.boundary_lost


# --------------------------------------------------------------------------- #
# The one generation stamp
#
# A run has exactly one generation time, and this is where it is resolved: once,
# before the fan-out, so that the four artifacts of one run agree on it.  The
# alternative - each writer reading its own clock when the document carries no
# stamp - produced a generation time in one human artifact and none in the
# other for one and the same document, and a different value on every render.
# Neither HTML writer holds a clock now; this is the only one in the fan-out.
# --------------------------------------------------------------------------- #


def _usable_stamp(value: object) -> str:
    """Reduce a candidate generation stamp to usable text.

    The same judgement ``app/reporting/events.py`` applies when it folds the
    per-worker stamps together and the two HTML writers apply when they read
    one: a stamp is usable when it is non-blank text, and anything else -
    ``None``, a blank string, a number a hand-built document put there - counts
    as no stamp at all.

    Args:
        value: The candidate, from the document or from a caller.

    Returns:
        The trimmed text, or ``""`` when the candidate is not usable.
    """
    return value.strip() if isinstance(value, str) else ""


def _document_for_fan_out(
    result_set: ResultSet,
    generated_at: str | None,
) -> ResultSet:
    """Resolve the one document every writer is handed, stamp included.

    Three cases, and the first is the normal one:

    * the document already carries the stamp the run is to report - the
      collector wrote it at close and the merge kept the latest - so the
      **caller's own object** is returned, unchanged and unwrapped, and all four
      writers receive that one object;
    * the document carries no usable stamp, so one is resolved here and handed
      on in a **single shallow copy**, which all four writers then receive by
      identity for the same reason;
    * the caller pinned a stamp that the document does not already carry, which
      takes the same copy.

    **The caller's document is never mutated.**  :func:`generate_reports`
    documents its input as read-only, and a mutation would be the very fault
    that contract exists to prevent: the same object reaches every writer, so
    writing to it would alter the input of each writer still to run.  The copy
    is shallow on purpose - the features, metadata and errors below the top
    level are shared, not duplicated, because nothing here writes to them and a
    deep copy of a large document would cost the run real time.

    Args:
        result_set: The merged result document, in the internal schema
            ``app/reporting/events.py`` owns.  A value that is not a mapping at
            all is returned untouched: the writers' reads are total and each
            handles such a document on its own, and inventing a mapping here
            would hide the fault from all four of them.
        generated_at: A stamp pinned by the caller, which wins over the
            document's own, or ``None`` to use the document's.

    Returns:
        Either ``result_set`` itself or one shallow copy of it carrying the
        resolved stamp - in both cases one object, for all four writers.
    """
    if not isinstance(result_set, Mapping):
        return result_set

    recorded = _usable_stamp(result_set.get(_GENERATED_AT_KEY))
    resolved = (
        _usable_stamp(generated_at)
        or recorded
        # The fan-out's own clock, read exactly once per call and formatted the
        # way the collector formats every timestamp in the document, so the
        # value on an artifact is indistinguishable from a result-backed one.
        # Reached only by a document that recorded no stamp, which is the
        # empty-run case: nothing was selected, so nothing was ever stamped.
        or format_timestamp(datetime.now(UTC))
    )
    if resolved == recorded:
        return result_set
    return {**result_set, _GENERATED_AT_KEY: resolved}


# --------------------------------------------------------------------------- #
# The fan-out
# --------------------------------------------------------------------------- #


def _boundary_failure(
    spec: WriterSpec,
    index: int,
    results: Sequence[WriterResult],
    written: Sequence[Path],
    base: Path | str | None,
    guard: PublicationGuard | None,
) -> ReportOutcome | None:
    """Return the outcome of a fan-out stopped by a lost claim, or ``None``.

    The publication boundary, checked once per writer.  A claim that is still
    held costs one predicate call and produces nothing; a claim that is gone
    stops the fan-out **before** the writer runs, which is the whole point -
    the artifacts this run has already published stay exactly where they are,
    and nothing further is written into a build output another run has taken
    over.

    Args:
        spec: The writer about to be driven, named as the one not attempted.
        index: Its position in :data:`WRITER_SEQUENCE`, which fixes the
            writers reported as skipped.
        results: What the writers before it did, carried through unchanged.
        written: The artifacts they produced, carried through unchanged and
            **not** deleted: the section 0.4.1 exit contract keeps whatever a
            stopped fan-out had already published.
        base: The directory the destinations resolve against, used only to
            name this writer's intended destination in the outcome.
        guard: The claim to check, or ``None`` when the caller holds none, in
            which case there is no boundary to lose and this returns ``None``.

    Returns:
        ``None`` when publication may proceed; otherwise a
        :class:`ReportOutcome` whose :attr:`~ReportOutcome.failed_writer` is
        this writer and whose :attr:`~ReportOutcome.error` is a
        :class:`PublicationBoundaryLost`, which ``app/cli.py`` maps onto its
        artifact-failure status exactly as it maps a writer's own exception.
    """
    if guard is None or guard.is_held():
        return None

    destination = spec.destination(base)
    named_destination: Path | str = (
        _UNRESOLVED_DESTINATION if destination is None else destination
    )
    error = PublicationBoundaryLost(
        f"this run no longer holds the build output, so {spec.name} did not "
        f"write {named_destination}: another run has claimed the workspace, "
        "and publishing now would leave a mixture of two runs' reports"
    )
    # ERROR, so the stream split sends it to stderr beside the exit class
    # ``app/cli.py`` derives from the outcome.  No traceback: nothing raised,
    # and the message is the whole of the diagnosis.
    logger.error(
        "Report writer %s did not run: %s",
        spec.name,
        error,
    )
    return ReportOutcome(
        results=tuple(results),
        written=tuple(written),
        failed_writer=spec.name,
        error=error,
        skipped=tuple(later.name for later in WRITER_SEQUENCE[index + 1 :]),
        failed_path=destination,
        boundary_lost=True,
    )


def generate_reports(
    result_set: ResultSet,
    *,
    base: Path | str | None = None,
    guard: PublicationGuard | None = None,
    generated_at: str | None = None,
) -> ReportOutcome:
    """Write the four report artifacts from one merged result document.

    Drives :data:`WRITER_SEQUENCE` in order, calling each writer as
    ``write(result_set, base=base)``, and stops at the first that raises.  AAP
    0.4.1's writer-failure row: earlier artifacts stay on disk, nothing is
    deleted, and the failing writer is named on stderr at ``ERROR``.

    When a ``guard`` is supplied it is consulted immediately before each
    writer, so the four artifacts are published only while this run still owns
    the build output - see the publication-boundary section above for why that
    check is here and why the set is not promoted atomically.

    **The run's generation time is resolved here, once, before the fan-out.**
    :func:`_document_for_fan_out` either recognises the stamp the document
    already carries - the normal case, since the collector writes one at close
    and the merge keeps the latest - or resolves one for a document that
    carries none, and the writers are then handed one document that carries it.
    **No writer has a clock of its own**, so every artifact of one run reports
    the same generation time and two renders of one document report the same
    value; a writer that read its own clock instead put a generation time on
    the self-contained page while the report tree's Date cell, which has no
    clock, stayed empty for that same document.  The call shape is uniform for
    all four writers - the stamp travels in the document, not as a per-writer
    rendering argument - so the two machine-read writers are unaffected by it
    and neither HTML writer needs telling.

    Args:
        result_set: The merged result document, in the internal schema
            ``app/reporting/events.py`` owns.  Treated as read-only: the same
            object is handed to all four writers, so a mutation here would
            corrupt the input of every writer that had not run yet.  Stamping
            therefore never writes to it - a document that needs a stamp is
            shallow-copied once and the copy is what the writers receive.
        base: Directory the artifact paths resolve against, passed through
            untouched and defaulting to ``None``, which each writer resolves to
            the working directory through :mod:`app.utils.paths` - the same
            default every accessor there applies.  It is the only override
            mechanism, and the seam a test uses to redirect the whole fan-out
            into a temporary directory.
        generated_at: The generation time to report, which wins over the
            document's own.  Defaults to ``None``, which resolves the stamp
            from the document and, for a document carrying none, from this
            one clock reading.  Keyword-only with a default because it is an
            override rather than an input: ``app/cli.py`` calls this function
            with the merged document alone, and a caller that needs the value
            pinned - a test, or a tool re-rendering a stored document against a
            known time - supplies it by name.

        guard: The claim the caller holds on the build output, checked before
            each writer publishes, or ``None`` to publish unconditionally.
            ``app/cli.py`` always supplies the run lock it took before the
            clean step; ``None`` is what keeps this function callable on its
            own, by a test or by a caller that has established exclusivity
            some other way, and it is the historical behaviour unchanged.

    Returns:
        A :class:`ReportOutcome`.  On success its :attr:`~ReportOutcome.results`
        holds four entries, :attr:`~ReportOutcome.written` the four paths and
        :attr:`~ReportOutcome.ok` is ``True``.  On failure the results end with
        the writer that raised, :attr:`~ReportOutcome.failed_writer`,
        :attr:`~ReportOutcome.error` and :attr:`~ReportOutcome.failed_path`
        describe it - the writer, its exception and the destination it was
        producing - and :attr:`~ReportOutcome.skipped` names the writers left
        unattempted.  The outcome carries no document: the stamped object is
        the writers' input and nothing downstream reads it back.

    Raises:
        KeyboardInterrupt: Propagated untouched - an interrupt stops the run.
        SystemExit: Propagated untouched; every :exc:`Exception` a writer
            raises is caught and reported instead.
    """
    results: list[WriterResult] = []
    written: list[Path] = []

    # Before the loop, and exactly once: every writer below is handed this one
    # object, so the four artifacts cannot disagree about when the run was
    # reported, and no writer is left to invent the answer.
    document = _document_for_fan_out(result_set, generated_at)

    # No empty-result-set guard here, and its absence is deliberate: a run that
    # selected no scenario must still write all four artifacts, empty, so that
    # the Jenkins publisher - whose glob ``Jenkins:15`` narrows to the single
    # JSON file - always has an input, which is the zero-scenario row of the
    # section 0.4.1 exit contract.  The writers already handle an empty
    # document (the JSON one emits an empty list), so an early return or a
    # truthiness check on the features would silently break that row while
    # looking like an optimisation.  Fan out unconditionally.
    for index, spec in enumerate(WRITER_SEQUENCE):
        boundary = _boundary_failure(spec, index, results, written, base, guard)
        if boundary is not None:
            return boundary
        try:
            # ``base`` by keyword - see WriterSpec.write.  No ``path`` or
            # ``directory`` override is ever passed: each writer resolves its
            # own destination, so this module owns no path at all.  The
            # document is the stamped one resolved above, which is the caller's
            # own object whenever that object already carried the run's
            # generation time.
            path = spec.write(document, base=base)
        except Exception as exc:  # noqa: BLE001 - the boundary is the point
            # Broad on purpose.  The writers document OSError, and the tree
            # writer additionally jinja2.TemplateError and FileNotFoundError,
            # but this is the outermost boundary of artifact production: any
            # exception whatever has to become a reported outcome rather than a
            # traceback out of the command line.  ``Exception`` and not
            # ``BaseException``, so an interrupt still stops the run.
            skipped = tuple(later.name for later in WRITER_SEQUENCE[index + 1 :])
            results.append(WriterResult(name=spec.name, error=exc))

            # The destination is resolved *here*, from the same ``base`` the
            # writer was given, and carried on the outcome - so the record
            # below and ``app/cli.py``'s exit-class record name one artifact
            # that was derived once.  The call cannot raise.
            destination = spec.destination(base)

            # What the *record* names it by is the relative identifier, not
            # that absolute path.  The path is what a caller acts on and is
            # carried onward on ``failed_path`` for exactly that; a console log
            # is archived and shared, and the absolute location of a CI
            # workspace in it is disclosure rather than diagnosis (CWE-200).
            # ``artifact_id()`` cannot raise and degrades to the key.
            named_destination: str = (
                _UNRESOLVED_DESTINATION
                if destination is None
                else spec.artifact_id()
            )

            # ERROR, so ``app/logging_config.py`` routes it to stderr, naming
            # the writer and the artifact it was producing as AAP 0.4.1's
            # writer-failure row requires - a template or model exception need
            # not mention a path itself - with the traceback attached, which is
            # the one thing ``ReportOutcome`` cannot carry to a later reader.
            # It is the only record this module emits for the failure: what the
            # outcome carries is reported once, by ``app/cli.py``.
            logger.error(
                "Report writer %s failed writing %s: %r",
                spec.name,
                named_destination,
                exc,
                exc_info=exc,
            )

            return ReportOutcome(
                results=tuple(results),
                written=tuple(written),
                failed_writer=spec.name,
                error=exc,
                skipped=skipped,
                failed_path=destination,
            )

        results.append(WriterResult(name=spec.name, path=path))
        written.append(path)
        # INFO, so the same handler split sends progress to stdout; one line per
        # artifact, both streams being line-buffered for capture by CI.  The
        # artifact is named by its relative identifier for the reason
        # ``artifact_id()`` states - the path the writer returned is on the
        # outcome, for a caller that needs to act on it.
        logger.info("Report writer %s wrote %s", spec.name, spec.artifact_id())

    if guard is not None and not guard.is_held():
        # Checked once more after the last writer, because the check before
        # each one cannot cover the interval during which the last one ran.
        # All four artifacts are on disk and are kept - nothing here deletes an
        # artifact - but the run no longer owns the workspace it put them in,
        # so what a reader will find may be a mixture of two runs' reports and
        # this fan-out will not report success over it.
        logger.error(
            "The four artifacts were written, but this run no longer holds the "
            "build output: another run has claimed the workspace, so the "
            "published set cannot be vouched for as this run's"
        )
        return ReportOutcome(
            results=tuple(results),
            written=tuple(written),
            boundary_lost=True,
        )

    return ReportOutcome(results=tuple(results), written=tuple(written))
