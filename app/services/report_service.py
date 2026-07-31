"""The Python port of the pipeline stage ``'Generate report'``.

This module is the third and last stage of the source repository's Groovy
*scripted* Jenkins pipeline, rewritten for CPython. In the source that stage was
a single statement -- one call to the Jenkins Cucumber publisher with eight named
parameters -- and this module reproduces the whole of its observable behaviour:
the six report thresholds, the report include pattern, the alphabetical feature
ordering, and the production of every report artifact the build published.

The source stage, quoted byte-exactly from ``[Jenkins:L14-L16]`` (the middle line
carries eleven leading spaces in the original)::

    stage('Generate report'){
           cucumber failedFeaturesNumber: -1, failedScenariosNumber: -1, failedStepsNumber: -1, fileIncludePattern: '**/*.json', pendingStepsNumber: -1, skippedStepsNumber: -1, sortingMethod: 'ALPHABETICAL', undefinedStepsNumber: -1
    }

It is reached over HTTP as ``POST /api/v1/reports`` and it is driven in sequence,
after the test stage, by ``app/services/pipeline_service.py``.

What this module is, and what it is not
---------------------------------------
It is an **orchestrator**, never a formatter. Every byte of report-format
knowledge lives in ``app/reporting/``, one adapter per Cucumber plugin, which is
AAP Rule T2 in practice: "One source construct, one target module ... This makes
the mapping auditable by inspection rather than by reading code." Consequently
this module:

* writes no report file itself -- ``app/reporting/rerun_report.py`` is the sole
  producer of ``rerun.txt`` and ``app/reporting/pretty_reports.py`` is the sole
  producer of the PrettyReports directory;
* re-renders and never re-parses the Cucumber HTML report, which
  ``pytest-html`` writes during the test run and which is served as a static
  artifact so it cannot diverge from what the source produced;
* restates no part of the Cucumber JSON schema -- that belongs to
  ``app/reporting/cucumber_json.py``;
* captures no screen shot -- ``app/reporting/screenshots.py`` indexes them and
  the BDD ``conftest.py`` captures them;
* declares no filesystem path and creates no directory -- ``app/utils/paths.py``
  owns the artifact layout and its creation;
* deletes nothing under the artifact root -- wiping it is the ``Makefile``
  ``clean`` target, the port of ``mvn clean``;
* performs no HTTP work, publishes nothing to Jenkins, talks to no Jira, and
  introduces no database, cache, broker or task queue.

The two preserved defects that shape this module
------------------------------------------------
``docs/migration-parity.md`` is the authoritative register of defects D1 through
D9. Two of them are load-bearing here and are preserved deliberately under AAP
Rule T4, "Defects are behavior".

**D3 -- the never-failing build.** All six publisher thresholds are ``-1``, which
the Jenkins Cucumber publisher reads as "no threshold", and the source build also
carried ``<testFailureIgnore>true</testFailureIgnore>`` ``[pom.xml:L25]``. The
source system therefore had no build-time quality gating whatsoever. Nothing
below ever branches on a threshold value: the six numbers are imported as data,
echoed into the result, and never evaluated. Report generation succeeds when the
run it describes was full of failures, and ``pipeline_service.py`` calls this
stage even after the test stage failed.

**D2 -- the zero-scenario run is the default.** The preserved tag expression
``LogOut``, ported from ``tags = "@LogOut"`` ``[README.md:L87]``, matches no
scenario in the feature file, so the ordinary outcome of the documented
invocation is an empty report and an empty rerun manifest. An absent, empty or
zero-feature Cucumber JSON report is therefore reported as a **success with zero
counts**, never as a failure and never as an exception. Treating it as an error
would make the port fail exactly where the source went green.

Public API
----------
Documented exhaustively because ``app/api/routes.py`` and
``app/services/pipeline_service.py`` bind to it.

Behaviour:

``generate_report(*, base_dir=None, sorting_method=None, run_id=None, preceding_stage_failed=None)``
    The one entry point. Returns a :class:`ReportResult` and never raises: every
    outcome, including a fault, comes back as a value. ``generate_reports`` is an
    alias for the same object, spelled the way the route and
    ``scripts/generate_reports.py`` spell it.

``feature_sort_key(feature)`` / ``sort_features_alphabetically(features)``
    The ``sortingMethod: 'ALPHABETICAL'`` implementation, exposed so it can be
    asserted directly.

Values:

``STAGE_NAME``
    ``'Generate report'`` -- the source stage label, byte-identical.

``ReportResult``
    Immutable outcome of one stage invocation. ``to_dict()`` renders the whole
    of it as JSON-ready primitives; ``api_payload()`` renders exactly the keys
    ``app.api.schemas.ReportResponse`` accepts, which matters because that model
    is declared ``extra="forbid"``.

``ArtifactDescriptor``
    One report artifact: its logical name, path, outcome, filesystem state, size
    and detail. ``api_payload()`` matches ``app.api.schemas.ReportArtifact``.

``FeatureDescriptor``
    One feature of the report, in the alphabetical order this stage applies.

``ArtifactOutcome``
    ``produced`` / ``located`` / ``skipped`` / ``failed`` -- what this stage did
    about an artifact.

``ArtifactState``
    ``available`` / ``empty`` / ``missing`` / ``not_a_file`` / ``inaccessible``
    / ``outside_root`` -- what is on disk. Value-for-value the vocabulary of
    ``app.reporting.html_report.HtmlReportStatus`` and of
    ``app.api.schemas.ArtifactStatus``; declared here rather than imported from
    either because this layer may not import upward from ``app.api`` and because
    this enumeration is part of *this* module's published contract.

``ARTIFACT_CUCUMBER_HTML`` / ``ARTIFACT_CUCUMBER_JSON`` / ``ARTIFACT_RERUN_TXT`` / ``ARTIFACT_PRETTY_REPORTS`` / ``ARTIFACT_SCREEN_SHOTS`` / ``ARTIFACT_ERROR_SHOTS``
    The six logical artifact names, taken from ``app/utils/paths.py`` so that no
    path literal is spelled here.

``ARTIFACT_NAMES``
    Those six names in the order the result reports them.

Errors:

``ReportGenerationError`` and ``ArtifactRootUnavailableError``
    Narrow, module-local types. They are raised internally and converted into a
    ``succeeded=False`` result by :func:`generate_report`, so they never escape
    it; ``pipeline_service.py`` may still import them, which is the permitted
    leaf-to-orchestrator direction.

Example::

    >>> from app.services.report_service import STAGE_NAME, generate_report
    >>> STAGE_NAME
    'Generate report'
    >>> result = generate_report()          # no Flask application needed
    >>> result.succeeded, result.gating_applied
    (True, False)
    >>> result.sorting_method
    'ALPHABETICAL'
    >>> sorted(result.thresholds.values())
    [-1, -1, -1, -1, -1, -1]

Configuration
-------------
Every parameter is an explicit, typed keyword argument defaulting to ``None``.
An unset parameter is filled from ``flask.current_app.config`` when an
application context is active and from a documented fallback otherwise, which is
what keeps this module unit-testable with no Flask application in existence. The
five-rung precedence chain -- constructor argument, environment variable,
``.env``, ``configuration.properties``, hard-coded default -- belongs entirely to
``app/config.py``; no rung is re-implemented here and no ``.env`` file is read
here. ``configuration.properties`` is git-ignored ``[.gitignore:L3]`` and is
therefore normally absent, so this module works with no configuration at all.

The ``run_id`` is an opaque correlation identifier and nothing else. It never
becomes a path component: the artifact paths are fixed under a single artifact
root, and a per-run subdirectory would relocate them and invalidate both those
paths and the publisher's include pattern ``[Jenkins:L15]``.

Layering and runtime dependencies
---------------------------------
AAP Rule T7 fixes one direction, ``api -> services -> reporting -> utils``. This
module therefore imports the standard library, ``flask`` for ``current_app``
alone, ``app.reporting.*`` and ``app.utils.paths``. It never imports
``app.api``, ``app.web``, ``app.config`` or the ``app`` package root, and never
anything beneath ``tests/`` or ``scripts/``.

It equally never imports a harness distribution -- not ``pytest``,
``pytest_bdd``, ``pytest_html``, ``pytest_metadata``, ``selenium``,
``webdriver_manager``, ``faker`` or ``requests``. Those live in
``requirements-test.txt`` and are absent from the deployed image, which is built
from ``requirements.txt`` alone; the deployed import chain runs ``wsgi.py`` ->
``app/__init__.py`` -> the blueprints -> this module, so one stray harness import
here would break the container. This module reads what those tools wrote; it
never imports the writer and never spawns a process.

Logging is obtained through ``logging.getLogger(__name__)`` and is never
configured here: handlers, levels and formatters belong exclusively to
``app/logging_config.py``. Nothing is printed. No Flask application is
constructed and none is held at module level.
"""

import logging
import re
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Final, NamedTuple

from flask import current_app

from app.reporting.cucumber_json import (
    CucumberJsonReport,
    NormalizedFeature,
    NormalizedReport,
    RunSummary,
    load_report,
)
from app.reporting.html_report import HTML_CONTENT_TYPE, HtmlReportArtifact, describe_html_report
from app.reporting.pretty_reports import (
    PrettyReportsResult,
    PrettyReportsStatus,
    describe_pretty_reports,
    generate_pretty_reports,
)
from app.reporting.rerun_report import (
    MANIFEST_CONTENT_TYPE,
    RerunManifest,
    RerunManifestStatus,
    generate_rerun_manifest,
)
from app.reporting.screenshots import ShotCollection, ShotDirectoryStatus, ShotIndex, index_shots

# The one import line the migration plan fixes verbatim. AAP section 0.4.2 states
# the transformation as: "FROM the Groovy `cucumber` publisher call with inline
# thresholds [Jenkins:L15] TO `from app.reporting.thresholds import
# PUBLISHER_THRESHOLDS`, consumed by `app/services/report_service.py`". The symbol
# name is part of the contract, so it is neither renamed nor aliased nor wrapped.
from app.reporting.thresholds import PUBLISHER_THRESHOLDS

# The publisher's other two named parameters. They are imported under `SOURCE_`
# names -- the convention `app/config.py` already uses for the same constants --
# so that it stays obvious at every use site that the value came from the frozen
# constants module rather than from anything resolved here.
from app.reporting.thresholds import REPORT_FILE_INCLUDE_PATTERN as SOURCE_FILE_INCLUDE_PATTERN
from app.reporting.thresholds import REPORT_SORTING_METHOD as SOURCE_SORTING_METHOD
from app.utils.paths import (
    CUCUMBER_HTML_NAME,
    CUCUMBER_JSON_NAME,
    ERROR_SHOTS_DIR_NAME,
    PRETTY_REPORTS_DIR_NAME,
    RERUN_TXT_NAME,
    SCREENSHOTS_DIR_NAME,
    TARGET_DIR_NAME,
    StrPath,
    TargetLayout,
    ensure_target_layout,
    managed_directories,
    resolve_layout,
    to_posix,
)

__all__ = [
    "ARTIFACT_CUCUMBER_HTML",
    "ARTIFACT_CUCUMBER_JSON",
    "ARTIFACT_ERROR_SHOTS",
    "ARTIFACT_NAMES",
    "ARTIFACT_PRETTY_REPORTS",
    "ARTIFACT_RERUN_TXT",
    "ARTIFACT_SCREEN_SHOTS",
    "STAGE_NAME",
    "ArtifactDescriptor",
    "ArtifactOutcome",
    "ArtifactRootUnavailableError",
    "ArtifactState",
    "FeatureDescriptor",
    "ReportGenerationError",
    "ReportResult",
    "feature_sort_key",
    "generate_report",
    "generate_reports",
    "sort_features_alphabetically",
]

# Handlers, levels and formatters are configured exclusively by
# `app/logging_config.py`. This module only ever emits records on this logger, and
# it never writes to standard output: no `print`, no `basicConfig`, no handler
# manipulation and no level change anywhere below.
_LOGGER: Final[logging.Logger] = logging.getLogger(__name__)


# =============================================================================
# The stage label.
#
# [Jenkins:L14]     stage('Generate report'){
#
# Carried across byte-identically: that capitalisation, that single space. AAP
# section 0.8 requires it -- "All three stage names, the platform dispatch, and
# the publisher invocation [Jenkins:L15] stay byte-identical. Only the two command
# strings change." `app/services/pipeline_service.py` reads this constant so the
# orchestrator's returned structure shows the source's own label rather than a
# paraphrase of it, and every log record and every result below carries it too.
# =============================================================================

STAGE_NAME: Final[str] = "Generate report"
"""The source pipeline's own label for this stage ``[Jenkins:L14]``."""


# =============================================================================
# The publisher settings, echoed and never evaluated.
#
# DEFECT D3 -- INTENTIONALLY PRESERVED: the never-failing build.
#
# All six values in `PUBLISHER_THRESHOLDS` are the integer `-1`, and the Jenkins
# Cucumber publisher reads `-1` as "no threshold": a limit of `-1` can never be
# exceeded, so no number of failed features, failed scenarios, failed steps,
# pending steps, skipped steps or undefined steps could ever mark the source build
# unstable or failed. Combined with
#
#     <testFailureIgnore>true</testFailureIgnore>          [pom.xml:L25]
#
# -- which made the source build swallow test failures outright -- the source
# system had no build-time quality gating of any kind. That is defect D3 of the
# migration register, preserved on purpose under AAP Rule T4, "Defects are
# behavior", and AAP section 0.2.2 lists build-failure gating as out of scope:
# it "deliberately remains disabled so defect D3 is preserved".
#
# Two consequences bind every line below, and both are deliberate:
#
#   * The six numbers are NEVER re-declared here. They are imported from
#     `app/reporting/thresholds.py`, which exists so that they live in exactly one
#     place; a second declaration would be a divergence validation criterion V4
#     is written to catch.
#   * The six numbers are NEVER evaluated. Nothing below compares a threshold
#     against a count, derives a severity from one, or lets one influence whether
#     this stage succeeded. `app/reporting/thresholds.py` deliberately exports no
#     helper that would compute such a verdict, precisely so that nobody is
#     tempted to. The values are data to report, and reporting them is all that
#     happens to them.
#
# Report generation therefore succeeds when the run it describes was full of
# failures. A failing test run is a normal, reportable outcome, never a
# report-generation error, and `app/services/pipeline_service.py` reaches this
# stage even after the test stage failed. `docs/migration-parity.md` is the
# authoritative register of defects D1 through D9 and records the configuration
# switch that opts into each available fix.
# =============================================================================


# =============================================================================
# The report include pattern. Data, never a glob this application expands.
#
# [Jenkins:L15] fileIncludePattern: '**/*.json'
#
# AAP section 0.4.3 is explicit: "One leading-wildcard string survives into the
# target: the literal publisher value '**/*.json' [Jenkins:L15]. It is preserved
# verbatim AS DATA, not used as a transformation pattern, and must not be
# rewritten." So the value is imported from the frozen constants module, echoed
# into the result verbatim for the configuration-introspection endpoint and for
# criterion V4, and nothing else. It is never handed to `glob`, `rglob`,
# `fnmatch`, a regular-expression engine or a path object anywhere below, and it
# is never used to discover a report file: the artifact locations come from
# `app/utils/paths.py` as explicit paths.
#
# Matching files is the CI publisher's business, and the reason that publisher
# line needs no edit at all is recorded in AAP section 0.3.1: the glob "needs no
# change, because output stays under `target/`. This is precisely why the
# Java-flavoured `target/` name is retained rather than renamed." Accordingly this
# module spells no path of its own and can never rename that root.
# =============================================================================


# =============================================================================
# Logical artifact names.
#
# Taken from `app/utils/paths.py`, which owns every name and every path in the
# artifact tree, so that no path literal is written here. Note the two spellings
# the source uses and that are preserved exactly as they are found: the HTML
# report is `cucumber-reports.html` -- plural and hyphenated -- while the shot
# directories are asymmetric, `screenshots` being one unhyphenated word and
# `error-shots` being hyphenated.
# =============================================================================

# [README.md] "html:target/cucumber-reports.html"
ARTIFACT_CUCUMBER_HTML: Final[str] = CUCUMBER_HTML_NAME
"""Logical name of the Cucumber HTML report artifact."""

# [README.md] "json:target/cucumber.json"
ARTIFACT_CUCUMBER_JSON: Final[str] = CUCUMBER_JSON_NAME
"""Logical name of the Cucumber JSON report artifact."""

# [README.md] "rerun:target/rerun.txt"
ARTIFACT_RERUN_TXT: Final[str] = RERUN_TXT_NAME
"""Logical name of the rerun manifest artifact."""

# [README.md] "me.jvt.cucumber.report.PrettyReports:target/cucumber"
ARTIFACT_PRETTY_REPORTS: Final[str] = PRETTY_REPORTS_DIR_NAME
"""Logical name of the PrettyReports output directory."""

# [README.md:L42-L43] "It also generate `screen shots` for your tests if you
# enable it ..." -- opt-in, so an absent or empty directory is entirely ordinary.
ARTIFACT_SCREEN_SHOTS: Final[str] = SCREENSHOTS_DIR_NAME
"""Logical name of the screen-shot directory."""

# [README.md:L42-L43] "... and also generate `error shots` for your failed test
# cases as well." -- failure-driven, so it stays empty for a clean run.
ARTIFACT_ERROR_SHOTS: Final[str] = ERROR_SHOTS_DIR_NAME
"""Logical name of the error-shot directory."""

# The order the result reports its artifacts in: the four Cucumber plugin
# declarations in their source order, then the two shot directories. This is the
# same order `app/utils/paths.py` publishes for the report files, so a consumer
# reading both sees one consistent sequence.
ARTIFACT_NAMES: Final[tuple[str, ...]] = (
    ARTIFACT_CUCUMBER_HTML,
    ARTIFACT_CUCUMBER_JSON,
    ARTIFACT_RERUN_TXT,
    ARTIFACT_PRETTY_REPORTS,
    ARTIFACT_SCREEN_SHOTS,
    ARTIFACT_ERROR_SHOTS,
)
"""The six artifact names, in the order :class:`ReportResult` reports them."""


# =============================================================================
# Errors.
#
# Narrow, module-local types, declared here rather than in a new module: AAP
# section 0.3.1 shows `app/services/` holding exactly five files and AAP section
# 0.8 forbids adding any, so there is deliberately no `exceptions.py`.
#
# They are raised internally and converted into a `succeeded=False` result by
# `generate_report`, which is total, so neither type ever escapes the public
# entry point. `app/services/pipeline_service.py` may still import them: that is
# leaf to orchestrator, the permitted direction.
# =============================================================================


class ReportGenerationError(RuntimeError):
    """A fault that prevents this stage from producing any artifact at all.

    Deliberately narrow. It is *not* raised for a missing input, an empty report
    or a run full of failures: those are ordinary, successful outcomes of the
    preserved default behaviour (defects D2 and D3) and are reported as values.
    """


class ArtifactRootUnavailableError(ReportGenerationError):
    """The artifact root could not be created, so nothing can be written.

    The genuine report-generation fault: with no artifact root there is no
    directory for any adapter to write into. Raised only after
    ``app/utils/paths.py`` has already tried and failed to create the tree.
    """


# =============================================================================
# Vocabularies.
# =============================================================================


class ArtifactOutcome(StrEnum):
    """What this stage did about one artifact.

    Distinct from :class:`ArtifactState`, which says what is on disk. An artifact
    can be ``SKIPPED`` and still exist -- a stale file from an earlier run -- and
    it can be ``PRODUCED`` and still be ``EMPTY``, which is exactly what a
    zero-byte rerun manifest is.
    """

    PRODUCED = "produced"
    """This stage wrote or refreshed the artifact through its owning adapter."""

    LOCATED = "located"
    """The artifact was inspected and described; another producer writes it."""

    SKIPPED = "skipped"
    """There was nothing to produce it from. A success, never an error."""

    FAILED = "failed"
    """The owning adapter or the filesystem refused. Recorded, never raised."""


class ArtifactState(StrEnum):
    """What is on disk at an artifact's location.

    The six members are value-for-value those of
    ``app.reporting.html_report.HtmlReportStatus`` and of
    ``app.api.schemas.ArtifactStatus``. The vocabulary is restated here rather
    than imported from either because this layer may not import upward from
    ``app.api``, and because this enumeration is part of *this* module's
    published contract: binding it to a sibling adapter's enumeration identity
    would let a change there silently redefine it. Members are ``StrEnum``, so
    they compare equal to the matching members of both those enumerations.

    The artifact tree is ephemeral -- it is wiped at the start of every run -- so
    a state of ``MISSING`` is an ordinary answer rather than a problem.
    """

    AVAILABLE = "available"
    """Present, in the expected form, and not empty."""

    EMPTY = "empty"
    """Present and in the expected form, but holding nothing.

    A success, not a fault. A zero-byte ``rerun.txt`` is the ordinary outcome of
    the documented invocation, because its default tag expression selects no
    scenario at all (defect D2).
    """

    MISSING = "missing"
    """Nothing exists at the location. Ordinary before the first run."""

    NOT_A_FILE = "not_a_file"
    """Something exists, but not in the expected form -- a directory where a file
    belongs, or the reverse."""

    INACCESSIBLE = "inaccessible"
    """The location exists but could not be inspected."""

    OUTSIDE_ROOT = "outside_root"
    """The location resolves outside the artifact root and was refused."""


# The two states in which an artifact exists in its expected form and can
# therefore be served or browsed. `EMPTY` belongs here on purpose: an empty
# artifact is complete, not broken -- see `ArtifactState.EMPTY`.
_SERVABLE_STATES: Final[frozenset[ArtifactState]] = frozenset(
    {ArtifactState.AVAILABLE, ArtifactState.EMPTY}
)

# Outcome of the rerun manifest, by the status its sole producer reports. The two
# written statuses are successes, an unusable source is a skip, and only a refused
# write is a failure. `RerunManifestStatus.EMPTY` maps to `PRODUCED` because the
# zero-byte file really was written: its presence is what distinguishes a clean run
# from a run that never happened.
_RERUN_OUTCOMES: Final[Mapping[RerunManifestStatus, ArtifactOutcome]] = {
    RerunManifestStatus.WRITTEN: ArtifactOutcome.PRODUCED,
    RerunManifestStatus.EMPTY: ArtifactOutcome.PRODUCED,
    RerunManifestStatus.SOURCE_ABSENT: ArtifactOutcome.SKIPPED,
    RerunManifestStatus.SOURCE_UNREADABLE: ArtifactOutcome.SKIPPED,
    RerunManifestStatus.WRITE_FAILED: ArtifactOutcome.FAILED,
}

# What an inspection of a shot directory found, expressed in this module's
# vocabulary. An absent directory is `MISSING` rather than a fault: screen shots
# are opt-in and error shots only appear when something failed.
_SHOT_STATES: Final[Mapping[ShotDirectoryStatus, ArtifactState]] = {
    ShotDirectoryStatus.ABSENT: ArtifactState.MISSING,
    ShotDirectoryStatus.EMPTY: ArtifactState.EMPTY,
    ShotDirectoryStatus.AVAILABLE: ArtifactState.AVAILABLE,
    ShotDirectoryStatus.UNREADABLE: ArtifactState.INACCESSIBLE,
}


class _PathStat(NamedTuple):
    """Filesystem metadata about one location, gathered without ever raising.

    Used only where the owning adapter publishes no metadata of its own. Sizes and
    modification times are read here; nothing is opened, written or removed.
    """

    exists: bool
    """Whether anything exists at the location."""

    is_file: bool
    """Whether a regular file exists there."""

    is_directory: bool
    """Whether a directory exists there."""

    accessible: bool
    """Whether the location could be inspected at all."""

    size_bytes: int
    """A file's size, or the summed size of a directory's immediate files."""

    entry_count: int
    """How many regular files a directory holds. Always ``0`` for a file."""

    modified_at: str | None
    """Modification time as an ISO 8601 string in UTC, or ``None``."""


# Nothing is there, and the location could be inspected to establish that. The
# ordinary state of a wiped or never-populated artifact tree, so it is a shared
# immutable value rather than a tuple rebuilt on every miss.
_MISSING_STAT: Final[_PathStat] = _PathStat(
    exists=False,
    is_file=False,
    is_directory=False,
    accessible=True,
    size_bytes=0,
    entry_count=0,
    modified_at=None,
)


def _iso_timestamp(epoch_seconds: float) -> str | None:
    """Render a POSIX timestamp as an ISO 8601 string in UTC.

    Args:
        epoch_seconds: Seconds since the epoch, as ``os.stat_result`` reports.

    Returns:
        The timestamp in ISO 8601, or ``None`` when the platform hands back a
        value outside the representable range. A missing timestamp is never worth
        failing a report over.
    """
    try:
        return datetime.fromtimestamp(epoch_seconds, tz=UTC).isoformat()
    except (OSError, OverflowError, ValueError) as error:
        _LOGGER.debug(
            "Could not render modification time %r as an ISO 8601 string (%s: %s)",
            epoch_seconds,
            type(error).__name__,
            error,
        )
        return None


def _stat_entry(path: Path) -> _PathStat:
    """Inspect *path*, and never raise while doing so.

    Directory sizes are the summed size of the directory's own regular files.
    Sub-directories are not walked: the artifact tree is one level deep by
    design, so a recursive walk would only add cost and a failure mode.

    Args:
        path: The location to inspect.

    Returns:
        A :class:`_PathStat`. A refused inspection comes back with
        ``accessible`` false rather than as an exception, because a report must
        still be produced on a restricted filesystem.
    """
    try:
        stat_result = path.stat()
    except FileNotFoundError:
        return _MISSING_STAT
    except OSError:
        # PermissionError, a broken symlink target, a non-directory component in
        # the path, and everything else in the family: the location is there in
        # some sense but cannot be described.
        _LOGGER.warning("Could not inspect artifact location %s", to_posix(path))
        return _PathStat(
            exists=True,
            is_file=False,
            is_directory=False,
            accessible=False,
            size_bytes=0,
            entry_count=0,
            modified_at=None,
        )

    modified_at = _iso_timestamp(stat_result.st_mtime)

    if path.is_dir():
        total = 0
        entries = 0
        try:
            for child in path.iterdir():
                try:
                    if child.is_file():
                        total += child.stat().st_size
                        entries += 1
                except OSError:
                    # One unreadable child must not hide the whole directory.
                    continue
        except OSError:
            _LOGGER.warning("Could not list artifact directory %s", to_posix(path))
            return _PathStat(
                exists=True,
                is_file=False,
                is_directory=True,
                accessible=False,
                size_bytes=0,
                entry_count=0,
                modified_at=modified_at,
            )
        return _PathStat(
            exists=True,
            is_file=False,
            is_directory=True,
            accessible=True,
            size_bytes=total,
            entry_count=entries,
            modified_at=modified_at,
        )

    if path.is_file():
        return _PathStat(
            exists=True,
            is_file=True,
            is_directory=False,
            accessible=True,
            size_bytes=stat_result.st_size,
            entry_count=0,
            modified_at=modified_at,
        )

    # Something is there -- a socket, a device node, a symlink to neither -- but
    # it is not the form this artifact is supposed to take.
    return _PathStat(
        exists=True,
        is_file=False,
        is_directory=False,
        accessible=True,
        size_bytes=0,
        entry_count=0,
        modified_at=modified_at,
    )


def _state_for(stat: _PathStat, *, expect_directory: bool) -> ArtifactState:
    """Map filesystem metadata onto this module's artifact vocabulary.

    Args:
        stat: The metadata gathered by :func:`_stat_entry`.
        expect_directory: Whether the artifact is a directory rather than a file.

    Returns:
        The :class:`ArtifactState` describing the location.
    """
    if not stat.exists:
        return ArtifactState.MISSING
    if not stat.accessible:
        return ArtifactState.INACCESSIBLE
    if expect_directory:
        if not stat.is_directory:
            return ArtifactState.NOT_A_FILE
        return ArtifactState.AVAILABLE if stat.entry_count else ArtifactState.EMPTY
    if not stat.is_file:
        return ArtifactState.NOT_A_FILE
    return ArtifactState.AVAILABLE if stat.size_bytes else ArtifactState.EMPTY


# =============================================================================
# The result values.
#
# Frozen dataclasses throughout, so a result cannot be edited after the fact by a
# request handler, a template or a test. Every field is a primitive, a string
# enumeration member, a `Path` or a tuple of the same, which is what makes the
# `to_dict` renderings below JSON-serialisable without a custom encoder: no
# exception is ever stored as a value and no `datetime` object escapes -- times are
# already ISO 8601 strings.
# =============================================================================


@dataclass(frozen=True, slots=True)
class FeatureDescriptor:
    """One feature of the report, as this stage orders and counts it.

    Instances appear in :attr:`ReportResult.features` in the alphabetical order
    the publisher's ``sortingMethod: 'ALPHABETICAL'`` ``[Jenkins:L15]`` asks for,
    which is what makes that ordering observable rather than merely intended.
    """

    name: str
    """The feature's name -- the value the alphabetical ordering sorts on.

    Names are NOT unique in this project. Defect D9 turns on exactly that, which
    is why :func:`feature_sort_key` carries a tie-breaker.
    """

    uri: str
    """The feature file's URI, as the report records it."""

    line: int
    """The line the feature is declared on."""

    scenario_count: int
    """How many scenarios the feature contributed, backgrounds excluded."""

    failed_scenario_count: int
    """How many of those scenarios had at least one failed step."""

    step_count: int
    """How many steps the feature contributed, across every element."""

    status: str
    """The feature's aggregated status, worst scenario first."""

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready view of this descriptor.

        Returns:
            A freshly built dictionary. Building a new one on every call means the
            caller can mutate the result without corrupting this frozen value.
        """
        return {
            "name": self.name,
            "uri": self.uri,
            "line": self.line,
            "scenario_count": self.scenario_count,
            "failed_scenario_count": self.failed_scenario_count,
            "step_count": self.step_count,
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class ArtifactDescriptor:
    """One report artifact: what this stage did about it, and what is on disk.

    The two questions are answered by two separate fields on purpose.
    :attr:`outcome` records this stage's action and :attr:`status` records the
    filesystem, because they legitimately disagree: a manifest that was written
    with no failing scenario is ``PRODUCED`` and ``EMPTY`` at the same time, and
    that pair is the ordinary result of the documented invocation (defect D2).
    """

    name: str
    """The artifact's logical name -- one of :data:`ARTIFACT_NAMES`."""

    path: Path
    """Where it lives. Supplied by ``app/utils/paths.py``, never spelled here."""

    outcome: ArtifactOutcome
    """What this stage did: produced, located, skipped or failed."""

    status: ArtifactState
    """What is on disk at :attr:`path`."""

    is_directory: bool = False
    """Whether the artifact is a directory rather than a file."""

    exists: bool = False
    """Whether anything exists at :attr:`path`."""

    size_bytes: int = 0
    """The file's size, or a directory's summed immediate-file size, in bytes."""

    entry_count: int = 0
    """How many files a directory artifact holds. Always ``0`` for a file."""

    modified_at: str | None = None
    """Modification time as an ISO 8601 string, when it could be read."""

    content_type: str | None = None
    """The media type to serve it with, when the owning adapter publishes one.

    ``None`` rather than a guess: this module never invents a media type for an
    artifact whose adapter declares none, and it never invents one for a
    directory.
    """

    detail: str | None = None
    """The owning adapter's human-readable explanation of the outcome."""

    @property
    def posix_path(self) -> str:
        """:attr:`path` rendered with forward slashes, on every platform.

        Report consumers -- the CI publisher's include pattern above all -- are
        written in terms of forward slashes, so every string form of a path
        leaving this module is produced this way.
        """
        return to_posix(self.path)

    @property
    def available(self) -> bool:
        """Whether the artifact exists in its expected form and can be served.

        ``True`` for an empty artifact as well as a populated one: an empty
        artifact is complete, not broken. A zero-byte ``rerun.txt`` is the
        ordinary outcome of the preserved default invocation (defect D2), and a
        route can serve it perfectly well.
        """
        return self.status in _SERVABLE_STATES

    @property
    def succeeded(self) -> bool:
        """Whether this stage handled the artifact without a fault.

        Only :attr:`ArtifactOutcome.FAILED` is a fault. ``SKIPPED`` is not: there
        was simply nothing to produce the artifact from, which is the normal
        state of a fresh checkout.
        """
        return self.outcome is not ArtifactOutcome.FAILED

    def to_dict(self) -> dict[str, object]:
        """Return the complete, JSON-ready view of this descriptor.

        Returns:
            A freshly built dictionary carrying every field, including the ones
            :meth:`api_payload` leaves out. This is the view for logging, for the
            rendered report surface and for tests.
        """
        return {
            "name": self.name,
            "path": self.posix_path,
            "outcome": str(self.outcome),
            "status": str(self.status),
            "is_directory": self.is_directory,
            "exists": self.exists,
            "available": self.available,
            "size_bytes": self.size_bytes,
            "entry_count": self.entry_count,
            "modified_at": self.modified_at,
            "content_type": self.content_type,
            "detail": self.detail,
            "succeeded": self.succeeded,
        }

    def api_payload(self) -> dict[str, object]:
        """Return exactly the keys ``app.api.schemas.ReportArtifact`` accepts.

        That model is declared ``extra="forbid"``, so a single unexpected key
        would make validation fail. This rendering therefore carries nine keys
        and no more, which lets a request handler write::

            ReportArtifact.model_validate(descriptor.api_payload())

        Returns:
            A freshly built dictionary of primitives.
        """
        return {
            "name": self.name,
            "path": self.posix_path,
            "status": str(self.status),
            "exists": self.exists,
            "available": self.available,
            "size_bytes": self.size_bytes,
            "modified_at": self.modified_at,
            "content_type": self.content_type,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class ReportResult:
    """The immutable outcome of one ``'Generate report'`` invocation.

    Returned by :func:`generate_report`, which never raises: a fault comes back
    as :attr:`succeeded` false with an explanation in :attr:`detail`.

    Reading the surprising successes off this value should need no inference:

    * :attr:`succeeded` true with :attr:`feature_count` and
      :attr:`scenario_count` both zero is the documented default run, whose tag
      expression selects nothing (defect D2).
    * :attr:`succeeded` true with a positive :attr:`failed_scenario_count` is a
      report of a run that failed. Publishing it is the point (defect D3).
    * :attr:`succeeded` true with :attr:`preceding_stage_failed` true is the
      non-gating pipeline behaviour that validation criterion V12 grades.
    * :attr:`gating_applied` is always false, and it is carried explicitly so
      that the absence of a quality gate is a stated property rather than an
      omission a reader has to notice.
    """

    stage: str
    """The source stage label ``[Jenkins:L14]``, byte-identical."""

    succeeded: bool
    """Whether this stage completed. Independent of every count below."""

    detail: str
    """A human-readable summary, free of credentials."""

    run_id: str | None = None
    """The caller's correlation identifier, echoed back unchanged.

    Opaque. It is never used to build a path: the artifact locations are fixed
    beneath a single root, and a per-run subdirectory would relocate them and
    invalidate the publisher's include pattern ``[Jenkins:L15]``.
    """

    preceding_stage_failed: bool = False
    """Whether the test stage before this one failed (defect D3).

    A record for the audit trail, never a condition. It does not and must not
    influence whether the report is generated or whether this stage succeeded.
    """

    sorting_method: str = SOURCE_SORTING_METHOD
    """The report sort order in force ``[Jenkins:L15]``."""

    alphabetical_ordering_applied: bool = True
    """Whether the features below were ordered alphabetically by name.

    False only when configuration asked for an order the source never specifies,
    in which case the report layer's own stable order is kept and a warning names
    the unsupported value.
    """

    thresholds: Mapping[str, int] = PUBLISHER_THRESHOLDS
    """The six publisher thresholds ``[Jenkins:L15]``, echoed unchanged.

    Read-only, keyed by the publisher's own camelCase parameter names in their
    source order, every value ``-1``. Reported, never evaluated: see the module
    banner on defect D3.
    """

    file_include_pattern: str = SOURCE_FILE_INCLUDE_PATTERN
    """The publisher's report-discovery pattern ``[Jenkins:L15]``. Data only.

    Echoed verbatim and never expanded, matched, normalised or rewritten.
    """

    gating_applied: bool = False
    """Always false. There is no build-time quality gating (defect D3)."""

    report_status: str = "absent"
    """Whether the Cucumber JSON document was ``absent``, ``invalid`` or ``valid``.

    The vocabulary of ``app.reporting.cucumber_json`` and of
    ``app.api.schemas.CucumberReportStatus``. ``absent`` is an ordinary answer:
    the artifact tree is wiped before every run.
    """

    artifacts: tuple[ArtifactDescriptor, ...] = ()
    """The six artifacts, in :data:`ARTIFACT_NAMES` order."""

    features: tuple[FeatureDescriptor, ...] = ()
    """The report's features, in the alphabetical order this stage applied."""

    feature_count: int = 0
    """How many features the report held. ``0`` is an ordinary answer (D2)."""

    scenario_count: int = 0
    """How many scenarios the report held, backgrounds excluded."""

    failed_scenario_count: int = 0
    """How many scenarios failed. May be positive while :attr:`succeeded` is true."""

    rerun_line_count: int = 0
    """How many coordinates the rerun manifest lists -- one per failing scenario."""

    summary: Mapping[str, object] | None = None
    """The report summary, present only when a document was actually parsed.

    Mirrors ``app.reporting.cucumber_json.RunSummary.as_dict()`` key for key,
    which is also what ``app.api.schemas.RunSummaryModel`` accepts.

    ``None`` when :attr:`report_status` is ``absent`` or ``invalid``: nothing was
    read, so there is nothing to summarise, and zeroes would wrongly read as "a
    report was read and it was empty". A well-formed report holding no feature --
    the default outcome of the preserved ``@LogOut`` tag expression, defect D2 --
    *was* read, so it does get a summary with every count at zero. The counts on
    this result are zero in every one of those cases either way.
    """

    duration_seconds: float = 0.0
    """Wall-clock duration of the stage, measured with a monotonic clock."""

    @property
    def publisher_settings(self) -> dict[str, object]:
        """Return the publisher parameters, spelled the publisher's own way.

        The keys are the aliases ``app.api.schemas.PublisherSettings`` declares --
        ``thresholds``, ``fileIncludePattern`` and ``sortingMethod`` -- and the
        six nested keys are the publisher's camelCase parameter names, never
        re-cased into Python style. This is what lets criterion V4 observe the
        ``[Jenkins:L15]`` parameters unchanged through the configuration and
        report endpoints.

        Returns:
            A freshly built dictionary of primitives.
        """
        return {
            "thresholds": dict(self.thresholds),
            "fileIncludePattern": self.file_include_pattern,
            "sortingMethod": self.sorting_method,
        }

    @property
    def artifact_names(self) -> tuple[str, ...]:
        """The logical names of :attr:`artifacts`, in reported order."""
        return tuple(artifact.name for artifact in self.artifacts)

    @property
    def failed_artifacts(self) -> tuple[ArtifactDescriptor, ...]:
        """The artifacts whose owning adapter or filesystem refused.

        An entry here does not make the stage fail: one adapter refusing must
        never withhold the artifacts the others produced.
        """
        return tuple(
            artifact for artifact in self.artifacts if artifact.outcome is ArtifactOutcome.FAILED
        )

    def artifact(self, name: str) -> ArtifactDescriptor | None:
        """Return the descriptor named *name*, or ``None``.

        Args:
            name: A logical artifact name, normally one of
                :data:`ARTIFACT_NAMES`.

        Returns:
            The matching descriptor, or ``None`` when this result carries none.
        """
        for artifact in self.artifacts:
            if artifact.name == name:
                return artifact
        return None

    def to_dict(self) -> dict[str, object]:
        """Return the complete, JSON-ready view of this result.

        Returns:
            A freshly built dictionary of primitives, lists and dictionaries
            only: no ``Path``, no ``datetime``, no exception and no enumeration
            object survives the rendering, so it serialises with the standard
            library encoder as it stands.
        """
        return {
            "stage": self.stage,
            "succeeded": self.succeeded,
            "detail": self.detail,
            "run_id": self.run_id,
            "preceding_stage_failed": self.preceding_stage_failed,
            "publisher": self.publisher_settings,
            "sorting_method": self.sorting_method,
            "alphabetical_ordering_applied": self.alphabetical_ordering_applied,
            "gating_applied": self.gating_applied,
            "report_status": self.report_status,
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "features": [feature.to_dict() for feature in self.features],
            "feature_count": self.feature_count,
            "scenario_count": self.scenario_count,
            "failed_scenario_count": self.failed_scenario_count,
            "rerun_line_count": self.rerun_line_count,
            "summary": None if self.summary is None else dict(self.summary),
            "duration_seconds": self.duration_seconds,
        }

    def api_payload(self) -> dict[str, object]:
        """Return exactly the keys ``app.api.schemas.ReportResponse`` accepts.

        That model is declared ``extra="forbid"``, so the richer
        :meth:`to_dict` rendering would be rejected outright. This one carries
        seven keys and no more, which lets a request handler write::

            ReportResponse.model_validate(result.api_payload())

        Returns:
            A freshly built dictionary of primitives.
        """
        return {
            "succeeded": self.succeeded,
            "run_id": self.run_id,
            "preceding_stage_failed": self.preceding_stage_failed,
            "publisher": self.publisher_settings,
            "artifacts": tuple(artifact.api_payload() for artifact in self.artifacts),
            "summary": None if self.summary is None else dict(self.summary),
            "detail": self.detail,
        }


# =============================================================================
# The ALPHABETICAL report ordering.
#
# [Jenkins:L15] sortingMethod: 'ALPHABETICAL'
#
# AAP section 0.6 assigns this behaviour to this module in so many words:
# "sortingMethod: 'ALPHABETICAL' [Jenkins:L15] must still be implemented even
# though it is a no-op with one feature. Sorting features by name before rendering
# in `app/services/report_service.py` means the behavior survives the addition of
# further features rather than becoming a latent divergence."
#
# So it is implemented, and it is implemented here rather than in the reporting
# layer -- `app/reporting/thresholds.py` and `app/reporting/rerun_report.py` both
# say in their own documentation that alphabetical sorting belongs to this module.
#
# WITH THE SINGLE `login.feature` THE SUITE OWNS TODAY THIS IS A NO-OP. One
# feature is already in alphabetical order with itself. It is implemented anyway,
# for exactly the reason quoted above: leaving it out would turn a preserved
# publisher setting into a divergence that only surfaces the day a second feature
# is authored, and by then nobody would connect the wrong order to a missing sort.
#
# `ALPHABETICAL` is the only order the source specifies. The Jenkins publisher also
# accepts a natural order; the source does not use it, so it is deliberately NOT
# implemented here -- AAP section 0.8 is explicit that "No feature may be dropped,
# and none may be added." The value itself is read from
# `app/reporting/thresholds.py` rather than written out again, so the string exists
# in exactly one place.
# =============================================================================


def feature_sort_key(feature: NormalizedFeature) -> tuple[str, str, int]:
    """Return the total ordering key of *feature* for the alphabetical sort.

    ``name`` first, because that is what ``sortingMethod: 'ALPHABETICAL'``
    ``[Jenkins:L15]`` sorts on. Then ``uri`` and ``line`` as tie-breakers, which
    is not defensive padding: names in this project are demonstrably not unique --
    defect D9 exists precisely because two scenarios parse to the same name -- and
    two features could collide the same way. Without a tie-breaker Python's
    ``sorted`` would leave colliding features in whatever order the report layer
    happened to produce, so a parallel run could reorder them between two
    otherwise identical runs. With one, the order is total, deterministic and
    reproducible.

    Args:
        feature: A feature of the normalized report.

    Returns:
        The ordering key: name, then URI, then declaration line.
    """
    return (feature.name, feature.uri, feature.line)


def sort_features_alphabetically(
    features: Iterable[NormalizedFeature],
) -> tuple[NormalizedFeature, ...]:
    """Order *features* by name, ascending, before anything renders them.

    This is the whole of the ``sortingMethod: 'ALPHABETICAL'`` implementation.
    Nothing is added, dropped, merged or de-duplicated: the result holds exactly
    the features handed in, reordered.

    Args:
        features: The features to order, in any order.

    Returns:
        An immutable tuple in ascending :func:`feature_sort_key` order.
    """
    return tuple(sorted(features, key=feature_sort_key))


def _feature_descriptor(feature: NormalizedFeature) -> FeatureDescriptor:
    """Project one normalized feature onto its reportable descriptor.

    Args:
        feature: A feature of the normalized report.

    Returns:
        The :class:`FeatureDescriptor` for it. Counts come from the feature's own
        aggregation, so they cannot disagree with the report summary.
    """
    scenarios = feature.scenarios
    return FeatureDescriptor(
        name=feature.name,
        uri=feature.uri,
        line=feature.line,
        scenario_count=len(scenarios),
        failed_scenario_count=sum(1 for scenario in scenarios if scenario.failed),
        step_count=len(feature.steps),
        status=feature.status,
    )


# =============================================================================
# Configuration resolution.
#
# Every setting arrives as an explicit keyword argument. An unset one is filled
# from the Flask application configuration when a context is active and from a
# documented fallback otherwise, which is what keeps this module callable -- and
# unit-testable -- with no Flask application in existence at all.
#
# The five-rung precedence chain (explicit argument -> environment variable ->
# `.env` -> `configuration.properties` -> hard-coded default) belongs entirely to
# `app/config.py`. No rung is re-implemented here: no environment variable is
# read, no `.env` file is loaded and no properties file is parsed below. The two
# keys named here are the ones `app/config.py` publishes under those names; it is
# imported nowhere in this module, because `app.config` sits outside the
# `services -> reporting -> utils` direction this layer may import along.
#
# `configuration.properties` is git-ignored [.gitignore:L3] and is therefore
# normally absent from a checkout, so every path below has to work with no
# configuration present at all. Each one does.
# =============================================================================

_CONFIG_TARGET_DIR: Final[str] = "TARGET_DIR"
_CONFIG_SORTING_METHOD: Final[str] = "REPORT_SORTING_METHOD"

# Replaces the userinfo of any URL that reaches a log record, so a credential
# embedded in one can never be written to a log file. Nothing this module handles
# is expected to carry a URL, which is exactly why the guard is unconditional
# rather than applied only where one is expected.
_URL_CREDENTIALS: Final[re.Pattern[str]] = re.compile(r"(?<=://)[^/\s@]+(?=@)")
_CREDENTIAL_PLACEHOLDER: Final[str] = "REDACTED"

# The two artifacts this stage itself produces, through their sole-producer
# adapters. A refusal on either is a genuine report-generation fault and is
# reflected in the stage verdict; a refusal on one of the four artifacts this
# stage merely inspects is not, because producing those is somebody else's job.
_PRODUCED_ARTIFACTS: Final[frozenset[str]] = frozenset(
    {ARTIFACT_RERUN_TXT, ARTIFACT_PRETTY_REPORTS}
)


def _redact(text: str) -> str:
    """Return *text* with any URL credential replaced by a placeholder.

    Args:
        text: A message about to be logged or stored on a result.

    Returns:
        The message with URL userinfo removed. Anything without a URL is returned
        unchanged.
    """
    return _URL_CREDENTIALS.sub(_CREDENTIAL_PLACEHOLDER, text)


def _config_value(key: str) -> object | None:
    """Read *key* from the Flask application configuration, if there is one.

    Args:
        key: The configuration key to read.

    Returns:
        The configured value, or ``None`` when no application context is active
        or the key is unset. ``current_app`` raises :class:`RuntimeError` outside
        an application context, and that is the documented signal this function
        translates into ``None`` -- no other error is swallowed.
    """
    try:
        config = current_app.config
    except RuntimeError:
        return None
    value: object | None = config.get(key)
    return value


def _resolve_base_dir(base_dir: StrPath | None) -> StrPath | None:
    """Decide which directory the artifact root should sit inside.

    Args:
        base_dir: The caller's explicit choice, which always wins.

    Returns:
        The base directory to hand to ``app/utils/paths.py``, or ``None`` for the
        repository-relative default. When no explicit value is given, the
        configured artifact root's *parent* is used, because the configuration
        publishes the root itself while the layout module takes the directory the
        root sits inside. That mapping round-trips exactly: the default
        configured root is relative, and its parent is the current directory.

        A configured root whose own name is not the canonical one cannot be
        honoured -- the layout module is deliberately unable to rename the root,
        because the CI publisher's include pattern ``[Jenkins:L15]`` depends on
        that name -- so the canonical layout is used and a warning says so.
    """
    if base_dir is not None:
        return base_dir

    configured = _config_value(_CONFIG_TARGET_DIR)
    if configured is None:
        return None
    if not isinstance(configured, str | Path):
        _LOGGER.warning(
            "Ignoring configured %s of unusable type %s; using the default artifact layout",
            _CONFIG_TARGET_DIR,
            type(configured).__name__,
        )
        return None

    root = Path(configured)
    if root.name != TARGET_DIR_NAME:
        _LOGGER.warning(
            "Configured %s is %r, whose name is not %r; the artifact root name is fixed "
            "because the report publisher's include pattern depends on it, so the "
            "canonical layout is used instead",
            _CONFIG_TARGET_DIR,
            to_posix(root),
            TARGET_DIR_NAME,
        )
        return None

    parent = root.parent
    # `Path('target').parent` is the current directory, and the layout module
    # spells that case as "no base directory at all", which keeps the default
    # layout relative exactly as the test configuration and the CI pipeline
    # spell it.
    return None if parent == Path() else parent


def _canonical_sorting_method(requested: str) -> str:
    """Fold a spelling variant of the source's own sort order onto the constant.

    ``configuration.properties`` is a Java properties file edited by hand, and an
    environment variable is edited by hand too, so ``alphabetical`` or a value with
    a stray trailing space is a spelling of the source's own order rather than a
    request for a different one. Recognising those keeps the echoed value equal to
    the frozen constant, which is what validation criterion V4 asserts.

    Anything that is genuinely not the source's order is returned untouched, so an
    unrecognised configuration stays visible in the result and in the log instead
    of being silently coerced into the only order that is implemented.

    Args:
        requested: The order as the caller or the configuration spelled it.

    Returns:
        ``ALPHABETICAL`` from ``app/reporting/thresholds.py`` when *requested* is
        that order in any casing, with surrounding whitespace ignored; otherwise
        *requested* exactly as given.
    """
    if requested.strip().upper() == SOURCE_SORTING_METHOD:
        return SOURCE_SORTING_METHOD
    return requested


def _resolve_sorting_method(sorting_method: str | None) -> str:
    """Decide which report sort order is in force.

    Args:
        sorting_method: The caller's explicit choice, which always wins.

    Returns:
        The resolved order: the caller's value, else the configured value, else
        the source value from ``app/reporting/thresholds.py``. The fallback is
        never written out as a literal here, and a recognised spelling variant is
        folded onto the constant by :func:`_canonical_sorting_method`.
    """
    if sorting_method is not None:
        return _canonical_sorting_method(sorting_method)
    configured = _config_value(_CONFIG_SORTING_METHOD)
    if isinstance(configured, str) and configured:
        return _canonical_sorting_method(configured)
    return SOURCE_SORTING_METHOD


# =============================================================================
# The artifact steps.
#
# One function per artifact, each bound to the adapter that owns that artifact --
# AAP Rule T2 again, one Cucumber plugin to one reporting adapter. None of them
# knows a file format, a path literal or a schema, and none of them raises: a
# refusal comes back as a `FAILED` descriptor so the remaining artifacts are still
# produced.
#
# Where the owning adapter publishes metadata -- a status, a size, a detail, a
# media type -- that metadata is used as it stands. Where it publishes none, the
# filesystem is inspected through `_stat_entry`. A media type is never invented for
# an artifact whose adapter declares none, and never for a directory.
# =============================================================================


def _locator_outcome(state: ArtifactState) -> ArtifactOutcome:
    """Classify what this stage did about an artifact it only inspects.

    Args:
        state: What was found on disk.

    Returns:
        ``LOCATED`` when the artifact is there in its expected form,
        ``SKIPPED`` when it simply has not been produced yet -- the ordinary state
        of a wiped artifact tree -- and ``FAILED`` when something is there but
        cannot be used.
    """
    if state is ArtifactState.MISSING:
        return ArtifactOutcome.SKIPPED
    if state in _SERVABLE_STATES:
        return ArtifactOutcome.LOCATED
    return ArtifactOutcome.FAILED


def _failed_descriptor(
    name: str,
    path: Path,
    error: BaseException,
    *,
    is_directory: bool = False,
) -> ArtifactDescriptor:
    """Build the descriptor for an artifact step that raised unexpectedly.

    The adapters are documented as total, so reaching this is a bug rather than an
    expected outcome -- which is exactly why it is recorded instead of propagated.
    Letting it propagate would withhold every other artifact, and would break the
    non-gating property in the one situation where it matters most.

    Args:
        name: The artifact's logical name.
        path: Where the artifact belongs.
        error: The exception the step raised. Only its type and message are kept:
            an exception object is not JSON-serialisable and must never become a
            field value.
        is_directory: Whether the artifact is a directory.

    Returns:
        A ``FAILED`` descriptor carrying a redacted explanation.
    """
    detail = _redact(f"{type(error).__name__}: {error}")
    _LOGGER.exception(
        "Stage %r could not handle artifact %s at %s: %s",
        STAGE_NAME,
        name,
        to_posix(path),
        detail,
    )
    stat = _stat_entry(path)
    return ArtifactDescriptor(
        name=name,
        path=path,
        outcome=ArtifactOutcome.FAILED,
        status=_state_for(stat, expect_directory=is_directory),
        is_directory=is_directory,
        exists=stat.exists,
        size_bytes=stat.size_bytes,
        entry_count=stat.entry_count,
        modified_at=stat.modified_at,
        detail=detail,
    )


def _guard(
    name: str,
    path: Path,
    step: Callable[[], ArtifactDescriptor],
    *,
    is_directory: bool = False,
) -> ArtifactDescriptor:
    """Run one artifact step in isolation, converting any fault into a value.

    This is what makes the steps independently fault-tolerant: whichever step
    fails, the others still run and their artifacts are still produced.

    Args:
        name: The artifact's logical name, for the failure record.
        path: Where the artifact belongs, for the failure record.
        step: The zero-argument step to run.
        is_directory: Whether the artifact is a directory.

    Returns:
        The step's descriptor, or a ``FAILED`` descriptor built from the
        exception. ``Exception`` is caught deliberately and broadly: no adapter
        defect may abort this stage.
    """
    try:
        return step()
    except Exception as error:
        return _failed_descriptor(name, path, error, is_directory=is_directory)


def _describe_cucumber_json(report: CucumberJsonReport, path: Path) -> ArtifactDescriptor:
    """Describe ``cucumber.json`` -- read and validated, never written here.

    The document is produced during the test run by the Cucumber-JSON writer the
    test configuration enables, and read here through
    ``app/reporting/cucumber_json.py``, which owns the frozen schema and
    distinguishes absent, invalid and valid without ever raising. All three
    outcomes are honoured: an absent document is the ordinary state of a wiped
    artifact tree, and an off-schema one that still carries features is still
    described rather than discarded, because the source system would have
    published those features.

    Args:
        report: The already-loaded report, so the file is read exactly once here.
        path: Where the document belongs.

    Returns:
        The descriptor. ``content_type`` is ``None`` because the JSON adapter is
        the one reporting module that publishes no media-type constant, and this
        module never invents one -- media types belong to whichever module owns the
        format. ``app/api/routes.py`` should let the response layer derive the type
        from the file name when it serves this artifact, exactly as it would for
        any other static download.
    """
    stat = _stat_entry(path)
    state = _state_for(stat, expect_directory=False)
    detail = report.reason or f"Cucumber JSON report is {report.status}"
    return ArtifactDescriptor(
        name=ARTIFACT_CUCUMBER_JSON,
        path=path,
        outcome=_locator_outcome(state),
        status=state,
        exists=stat.exists,
        size_bytes=stat.size_bytes,
        modified_at=stat.modified_at,
        detail=_redact(detail),
    )


def _describe_cucumber_html(artifact: HtmlReportArtifact) -> ArtifactDescriptor:
    """Describe ``cucumber-reports.html`` -- located, never rendered here.

    The file is written by ``pytest-html`` during the test run and is served as a
    static artifact. AAP section 0.3.4 is explicit that it "is served as a static
    artifact, not re-rendered. Re-rendering it would risk diverging from the
    source's output", so nothing here parses, rewrites or regenerates its markup.
    Note the name the source uses and that is preserved:
    ``cucumber-reports.html``, plural and hyphenated.

    Args:
        artifact: The snapshot ``app/reporting/html_report.py`` produced.

    Returns:
        The descriptor. Every field comes from the owning adapter, whose status
        vocabulary is value-identical to :class:`ArtifactState`, including the
        media type it publishes for the self-contained document.
    """
    state = ArtifactState(str(artifact.status))
    return ArtifactDescriptor(
        name=ARTIFACT_CUCUMBER_HTML,
        path=artifact.path,
        outcome=_locator_outcome(state),
        status=state,
        exists=artifact.exists,
        size_bytes=artifact.size_bytes,
        modified_at=artifact.modified_at.isoformat() if artifact.modified_at else None,
        content_type=HTML_CONTENT_TYPE,
        detail=_redact(artifact.detail),
    )


def _describe_rerun_manifest(manifest: RerunManifest) -> ArtifactDescriptor:
    """Describe ``rerun.txt`` -- produced by its sole producer.

    ``app/reporting/rerun_report.py`` is the only thing in the repository that
    writes this artifact: no test-runner option emits it, so the manifest exists
    only because this stage asked for it. The format is fixed at one line per
    failing scenario, ``<feature-uri>:<scenario-line>``, derived deterministically
    from the JSON report.

    Zero failing scenarios yield a zero-byte file, and that is a success rather
    than an error: it is the ordinary outcome of the documented invocation, whose
    tag expression selects no scenario at all (defect D2). Its presence is what
    distinguishes a clean run from a run that never happened -- which is also why,
    when there is no report to derive from, the sole producer deliberately writes
    nothing at all and this stage records a skip rather than fabricating a file it
    does not own.

    Args:
        manifest: The record its sole producer returned.

    Returns:
        The descriptor, carrying the manifest's own line count in
        :attr:`ArtifactDescriptor.entry_count`.
    """
    stat = _stat_entry(manifest.path)
    return ArtifactDescriptor(
        name=ARTIFACT_RERUN_TXT,
        path=manifest.path,
        outcome=_RERUN_OUTCOMES[manifest.status],
        status=_state_for(stat, expect_directory=False),
        exists=stat.exists,
        size_bytes=stat.size_bytes,
        entry_count=manifest.line_count,
        modified_at=stat.modified_at,
        content_type=MANIFEST_CONTENT_TYPE,
        detail=_redact(manifest.detail),
    )


def _describe_pretty_reports(
    result: PrettyReportsResult,
    base_dir: StrPath | None,
) -> ArtifactDescriptor:
    """Describe the ``cucumber/`` directory -- produced by its sole producer.

    ``app/reporting/pretty_reports.py`` is the only thing that writes it, by
    post-processing the Cucumber JSON report. AAP section 0.6 explains why
    post-processing is *more* faithful than a terminal reporter would be: "the
    Java plugin likewise rendered a report directory from the JSON rather than
    writing terminal output."

    Args:
        result: The record its sole producer returned.
        base_dir: The directory the artifact root sits inside, for the follow-up
            inspection of what actually landed on disk.

    Returns:
        The descriptor. A refused location -- one resolving outside the artifact
        root -- is reported as :attr:`ArtifactState.OUTSIDE_ROOT` and nothing is
        read, written or removed for it.
    """
    output_dir = result.output_dir
    if result.status is PrettyReportsStatus.REFUSED:
        return ArtifactDescriptor(
            name=ARTIFACT_PRETTY_REPORTS,
            path=output_dir,
            outcome=ArtifactOutcome.FAILED,
            status=ArtifactState.OUTSIDE_ROOT,
            is_directory=True,
            detail=_redact(result.description),
        )

    if result.successful:
        outcome = ArtifactOutcome.PRODUCED
    elif result.status is PrettyReportsStatus.WRITE_FAILED:
        outcome = ArtifactOutcome.FAILED
    else:
        # `source-absent` and `source-invalid`: there was nothing to render from,
        # which is a skip and not a fault (defect D2).
        outcome = ArtifactOutcome.SKIPPED

    # The directory inspection is the adapter's own, so what is reported is what
    # that adapter would serve; the size and modification time come from the
    # filesystem, which it does not publish.
    directory = describe_pretty_reports(base_dir=base_dir, output_dir=output_dir)
    stat = _stat_entry(output_dir)
    return ArtifactDescriptor(
        name=ARTIFACT_PRETTY_REPORTS,
        path=output_dir,
        outcome=outcome,
        status=_state_for(stat, expect_directory=True),
        is_directory=True,
        exists=stat.exists,
        size_bytes=stat.size_bytes,
        entry_count=len(directory.files),
        modified_at=stat.modified_at,
        detail=_redact(f"{result.description} ({directory.description})"),
    )


def _describe_shots(name: str, collection: ShotCollection) -> ArtifactDescriptor:
    """Describe one shot directory -- indexed here, captured elsewhere.

    ``[README.md:L42-L43]`` promises both groups verbatim: the project "also
    generate ``screen shots`` for your tests if you enable it and also generate
    ``error shots`` for your failed test cases as well". Screen shots are
    therefore opt-in and error shots are failure-driven, so an absent or empty
    directory is the ordinary state of either and never a fault.

    ``app/reporting/screenshots.py`` indexes them and never captures; capture is a
    hook wrapper in the BDD ``conftest.py``. Note the asymmetry in the source's
    own naming, preserved exactly: one group's directory is a single unhyphenated
    word, the other's is hyphenated.

    Args:
        name: The artifact's logical name.
        collection: The indexed collection for that group.

    Returns:
        The descriptor. The size is the indexer's own total over the artifacts it
        indexed, which is more precise than a directory listing; the modification
        time comes from the filesystem, which the indexer does not publish for the
        directory itself.
    """
    stat = _stat_entry(collection.directory)
    return ArtifactDescriptor(
        name=name,
        path=collection.directory,
        outcome=ArtifactOutcome.LOCATED if collection.exists else ArtifactOutcome.SKIPPED,
        status=_SHOT_STATES[collection.status],
        is_directory=True,
        exists=collection.exists,
        size_bytes=collection.total_size_bytes,
        entry_count=collection.count,
        modified_at=stat.modified_at,
        detail=_redact(collection.description),
    )


def _describe_shot_groups(
    layout: TargetLayout,
    base_dir: StrPath | None,
) -> tuple[ArtifactDescriptor, ArtifactDescriptor]:
    """Index both shot groups in one pass and describe each.

    One pass rather than two, because the indexer walks both directories together
    and a second call would scan the tree again for no gain. The two groups stay
    under their own descriptors and are never merged: their triggers differ -- one
    is opt-in, the other fires on failure -- and merging them would lose the
    distinction between a shot somebody asked for and a shot that exists because
    a test broke.

    Args:
        layout: The resolved artifact layout, for the failure records.
        base_dir: The directory the artifact root sits inside.

    Returns:
        The screen-shot descriptor and the error-shot descriptor, in that order.
        A defect in the indexer yields two ``FAILED`` descriptors rather than an
        exception, so the four other artifacts are still reported.
    """
    try:
        index: ShotIndex = index_shots(base_dir)
    except Exception as error:
        return (
            _failed_descriptor(
                ARTIFACT_SCREEN_SHOTS, layout.screenshots_dir, error, is_directory=True
            ),
            _failed_descriptor(
                ARTIFACT_ERROR_SHOTS, layout.error_shots_dir, error, is_directory=True
            ),
        )
    return (
        _describe_shots(ARTIFACT_SCREEN_SHOTS, index.screen_shots),
        _describe_shots(ARTIFACT_ERROR_SHOTS, index.error_shots),
    )


# =============================================================================
# The artifact root.
# =============================================================================


def _ensure_artifact_root(layout: TargetLayout, base_dir: StrPath | None) -> None:
    """Make the artifact tree exist, and refuse to continue if it cannot.

    This closes the port's most consequential silent failure, which AAP section
    0.6 records: "The Cucumber JSON writer does not create its parent directory.
    With ``target/`` absent, the run fails at session finish with a file-not-found
    error." Report generation can be reached over HTTP with no preceding run at
    all, so the tree has to be created here too rather than assumed.

    Creation is **delegated**, never performed: ``app/utils/paths.py`` owns the
    layout and its creation, so no directory is created by this module. It is
    guaranteed in three independent places -- there, in the ``Makefile`` targets,
    and in the BDD ``conftest.py`` -- specifically so that it cannot be missed.
    Nothing under the root is ever removed here; wiping it belongs to the
    ``Makefile`` ``clean`` target that ports ``mvn clean``.

    Args:
        layout: The resolved artifact layout.
        base_dir: The directory the root sits inside, as the layout module wants
            it.

    Raises:
        ArtifactRootUnavailableError: When the root still does not exist after the
            layout module has tried to create it. With no root there is nowhere
            for any adapter to write, which is the one genuine fault this stage
            has.
    """
    created = ensure_target_layout(base_dir)
    expected = managed_directories(base_dir)

    if not layout.root.is_dir():
        raise ArtifactRootUnavailableError(
            f"artifact root {to_posix(layout.root)} does not exist and could not be created"
        )

    if len(created) < len(expected):
        # A partial failure is survivable: the root is there, so the adapters can
        # still write the artifacts whose directories exist. The layout module has
        # already logged which ones it could not create.
        _LOGGER.warning(
            "Stage %r continuing with %d of %d artifact directories under %s",
            STAGE_NAME,
            len(created),
            len(expected),
            to_posix(layout.root),
        )


# =============================================================================
# The stage itself.
# =============================================================================


def _log_stage_start(
    run_id: str | None,
    sorting_method: str,
    preceding_stage_failed: bool,
) -> None:
    """Emit the stage's opening records, including the criterion V12 evidence.

    Args:
        run_id: The caller's correlation identifier, or ``None``.
        sorting_method: The resolved report sort order.
        preceding_stage_failed: Whether the test stage before this one failed.
    """
    # The correlation identifier is an opaque caller-supplied string, so it is
    # redacted like every other value that reaches a log record: nothing about its
    # contents is assumed, which is the only way a credential accidentally passed
    # as a correlation identifier can never be written to a log file.
    _LOGGER.info(
        "Stage %r starting (run_id=%s, sortingMethod=%s, fileIncludePattern=%s, "
        "thresholds=%s, quality gating disabled)",
        STAGE_NAME,
        _redact(run_id) if run_id is not None else None,
        _redact(sorting_method),
        SOURCE_FILE_INCLUDE_PATTERN,
        dict(PUBLISHER_THRESHOLDS),
    )

    if preceding_stage_failed:
        # Validation criterion V12: reporting runs after a failed test stage, and
        # that has to be visible in the log rather than merely true. The source
        # pipeline ran its report stage unconditionally after the test stage
        # because the build was non-gating by configuration (defect D3), so this
        # is the preserved behaviour being exercised, not a warning about a
        # mistake.
        _LOGGER.warning(
            "Stage %r is generating the report AFTER THE PRECEDING TEST STAGE FAILED; "
            "report generation is unconditional and non-gating, so the failure does not "
            "suppress it (preserved defect D3)",
            STAGE_NAME,
        )


def _stage_detail(
    succeeded: bool,
    artifacts: Sequence[ArtifactDescriptor],
    summary: RunSummary | None,
) -> str:
    """Compose the result's human-readable summary line.

    Args:
        succeeded: The stage verdict.
        artifacts: The descriptors gathered, in reported order.
        summary: The report summary, when one could be derived.

    Returns:
        A single sentence, free of credentials, safe to log and to return in a
        response body.
    """
    produced = sum(1 for artifact in artifacts if artifact.outcome is ArtifactOutcome.PRODUCED)
    located = sum(1 for artifact in artifacts if artifact.outcome is ArtifactOutcome.LOCATED)
    skipped = sum(1 for artifact in artifacts if artifact.outcome is ArtifactOutcome.SKIPPED)
    failed = sum(1 for artifact in artifacts if artifact.outcome is ArtifactOutcome.FAILED)

    if summary is None or summary.is_empty:
        # The ordinary outcome of the documented invocation: its tag expression
        # matches no scenario, so there is nothing to report on and that is a
        # success (defect D2).
        scope = "no scenario was reported"
    else:
        scope = (
            f"{summary.feature_count} feature(s) and {summary.scenario_count} scenario(s) "
            f"were reported, {summary.failed_scenario_count} of them failing"
        )

    verdict = "completed" if succeeded else "did not complete"
    return (
        f"Stage {STAGE_NAME!r} {verdict}: {scope}; artifacts produced={produced}, "
        f"located={located}, skipped={skipped}, failed={failed}; no quality gating was "
        "applied."
    )


def _run(
    *,
    base_dir: StrPath | None,
    sorting_method: str | None,
    run_id: str | None,
    preceding_stage_failed: bool,
    started_at: float,
) -> ReportResult:
    """Execute the stage, in the fixed order the migration plan lays down.

    The order is deliberate and every step is delegated:

    1. Make the artifact tree exist, through ``app/utils/paths.py``.
    2. Read and validate the Cucumber JSON report, through
       ``app/reporting/cucumber_json.py``.
    3. Order the features alphabetically -- the publisher's
       ``sortingMethod: 'ALPHABETICAL'`` ``[Jenkins:L15]`` -- before anything is
       derived from them or handed onward.
    4. Produce the rerun manifest, through its sole producer.
    5. Produce the PrettyReports directory, through its sole producer.
    6. Locate and describe the Cucumber HTML report, through its locator.
    7. Index the screen shots and the error shots, through their indexer.
    8. Assemble the result.

    Args:
        base_dir: Directory the artifact root sits inside, already resolved.
        sorting_method: The requested sort order, or ``None`` to resolve it.
        run_id: The caller's correlation identifier, or ``None``.
        preceding_stage_failed: Whether the test stage before this one failed.
        started_at: The monotonic reading taken when the stage began.

    Returns:
        The assembled :class:`ReportResult`.

    Raises:
        ArtifactRootUnavailableError: When the artifact root cannot be created.
            :func:`generate_report` converts it into a failed result.
    """
    resolved_base = _resolve_base_dir(base_dir)
    resolved_sorting = _resolve_sorting_method(sorting_method)
    layout = resolve_layout(resolved_base)

    _log_stage_start(run_id, resolved_sorting, preceding_stage_failed)

    # Step 1 -- the artifact tree. Raises only when the root itself is refused.
    _ensure_artifact_root(layout, resolved_base)

    # Step 2 -- the report document. The adapter owns the schema and never raises;
    # absent, invalid and valid are all honoured, and absent is ordinary.
    report = load_report(layout.cucumber_json)

    # Step 3 -- the alphabetical ordering, applied before any count is taken, any
    # descriptor is built or any renderer is reached.
    apply_alphabetical = resolved_sorting == SOURCE_SORTING_METHOD
    if apply_alphabetical:
        ordered_features = sort_features_alphabetically(report.normalized.features)
        _LOGGER.debug(
            "Stage %r ordered %d feature(s) by name for %s rendering",
            STAGE_NAME,
            len(ordered_features),
            resolved_sorting,
        )
    else:
        # The source specifies exactly one order and no other is implemented, so
        # an unrecognised request leaves the report layer's own stable order in
        # place rather than inventing a second ordering the source never had.
        _LOGGER.warning(
            "Stage %r was asked for report order %r, but %r is the only order the source "
            "pipeline specifies; leaving the report layer's own stable order in place",
            STAGE_NAME,
            resolved_sorting,
            SOURCE_SORTING_METHOD,
        )
        ordered_features = report.normalized.features

    # Every count, every descriptor and everything handed onward from here is
    # derived from the ORDERED view, never from the unordered one.
    ordered_report = NormalizedReport(features=ordered_features)
    summary = ordered_report.summary
    features = tuple(_feature_descriptor(feature) for feature in ordered_features)

    # The descriptor for the document read in step 2. Guarded like every other
    # step, even though it only inspects: a defect here must not withhold the
    # artifacts the producers below go on to write.
    json_report = _guard(
        ARTIFACT_CUCUMBER_JSON,
        layout.cucumber_json,
        lambda: _describe_cucumber_json(report, layout.cucumber_json),
    )

    # Step 4 -- the rerun manifest, from its sole producer. Both paths are given
    # explicitly so the manifest is derived from, and written beside, exactly the
    # document this stage read.
    rerun = _guard(
        ARTIFACT_RERUN_TXT,
        layout.rerun_txt,
        lambda: _describe_rerun_manifest(
            generate_rerun_manifest(
                source=layout.cucumber_json,
                destination=layout.rerun_txt,
            )
        ),
    )

    # Step 5 -- the PrettyReports directory, from its sole producer.
    pretty = _guard(
        ARTIFACT_PRETTY_REPORTS,
        layout.pretty_reports_dir,
        lambda: _describe_pretty_reports(
            generate_pretty_reports(
                source=layout.cucumber_json,
                output_dir=layout.pretty_reports_dir,
                base_dir=resolved_base,
            ),
            resolved_base,
        ),
        is_directory=True,
    )

    # Step 6 -- the HTML report, located and described but never re-rendered.
    html = _guard(
        ARTIFACT_CUCUMBER_HTML,
        layout.cucumber_html,
        lambda: _describe_cucumber_html(describe_html_report(resolved_base)),
    )

    # Step 7 -- the two shot groups, indexed in one pass.
    shots, error_shots = _describe_shot_groups(layout, resolved_base)

    # Step 8 -- assembly. The artifacts are reported in the source plugin
    # declaration order, then the two shot directories, which is the same order
    # `app/utils/paths.py` publishes for the report files.
    artifacts = (html, json_report, rerun, pretty, shots, error_shots)

    # The verdict covers this stage's own work only. A refusal on one of the two
    # artifacts it produces is a genuine report-generation fault; a refusal on one
    # of the four it merely inspects is not, because producing those belongs to
    # the test run. No count and no threshold takes any part in this decision.
    succeeded = not any(
        artifact.outcome is ArtifactOutcome.FAILED and artifact.name in _PRODUCED_ARTIFACTS
        for artifact in artifacts
    )

    for artifact in artifacts:
        _LOGGER.debug(
            "Stage %r artifact %s: outcome=%s status=%s size=%d entries=%d path=%s",
            STAGE_NAME,
            artifact.name,
            artifact.outcome,
            artifact.status,
            artifact.size_bytes,
            artifact.entry_count,
            artifact.posix_path,
        )

    result = ReportResult(
        stage=STAGE_NAME,
        succeeded=succeeded,
        detail=_stage_detail(succeeded, artifacts, summary),
        run_id=run_id,
        preceding_stage_failed=preceding_stage_failed,
        sorting_method=resolved_sorting,
        alphabetical_ordering_applied=apply_alphabetical,
        report_status=report.status,
        artifacts=artifacts,
        features=features,
        feature_count=summary.feature_count,
        scenario_count=summary.scenario_count,
        failed_scenario_count=summary.failed_scenario_count,
        rerun_line_count=rerun.entry_count,
        # A summary is published only when a report document was actually read.
        # An absent document is the ordinary state of a wiped artifact tree and an
        # invalid one was never parsed, so in both cases there is nothing to
        # summarise and the field is left empty rather than filled with zeroes
        # that would read as "a report was read and it was empty". The zero-feature
        # document -- the default outcome of the preserved `@LogOut` tag filter
        # (defect D2) -- *is* a document that was read, so it does get a summary,
        # with every count at zero.
        summary=summary.as_dict() if report.is_valid else None,
        duration_seconds=time.perf_counter() - started_at,
    )

    _LOGGER.info(
        "Stage %r finished in %.3fs: succeeded=%s, report=%s, features=%d, scenarios=%d, "
        "failing scenarios=%d, rerun lines=%d, sortingMethod=%s, gating applied=%s",
        STAGE_NAME,
        result.duration_seconds,
        result.succeeded,
        result.report_status,
        result.feature_count,
        result.scenario_count,
        result.failed_scenario_count,
        result.rerun_line_count,
        result.sorting_method,
        result.gating_applied,
    )
    return result


def generate_report(
    *,
    base_dir: StrPath | None = None,
    sorting_method: str | None = None,
    run_id: str | None = None,
    preceding_stage_failed: bool | None = None,
) -> ReportResult:
    """Run the pipeline stage ``'Generate report'`` ``[Jenkins:L14-L16]``.

    The one entry point of this module, reached over HTTP as
    ``POST /api/v1/reports`` and in sequence by
    ``app/services/pipeline_service.py``.

    The function is **total**: it never raises, and no outcome is signalled by an
    exception. In particular the following are all successes, deliberately:

    * an absent Cucumber JSON report -- the ordinary state of a wiped or
      never-populated artifact tree;
    * a well-formed report holding no feature at all, which is what the
      documented invocation produces because its preserved tag expression matches
      no scenario (defect D2);
    * a report full of failing scenarios, because the source system had no
      build-time quality gating whatsoever (defect D3) and publishing a failed
      run's report is the entire point of the stage;
    * being called after the test stage failed, which is the non-gating pipeline
      behaviour validation criterion V12 grades.

    It is **idempotent**: calling it twice in succession yields the same artifacts
    and an equivalent result. The rerun manifest is rewritten rather than appended
    to, and the PrettyReports directory is rewritten with its stale documents
    pruned, both by their owning adapters.

    Args:
        base_dir: Directory the artifact root should sit inside. ``None`` -- the
            default -- resolves it from the application configuration when a
            context is active and otherwise uses the repository-relative layout.
            A test passes a temporary directory so the real artifact tree is left
            untouched. The artifact root's own name is fixed and cannot be changed
            through this argument.
        sorting_method: The report sort order. ``None`` resolves it from the
            application configuration and then from the source value in
            ``app/reporting/thresholds.py``. Only the source's own order is
            implemented; any other value leaves the report layer's stable order in
            place and is reported back unchanged.
        run_id: An opaque correlation identifier, echoed into the result. It never
            becomes a path component: the artifact locations are fixed beneath a
            single root, and a per-run subdirectory would relocate them and
            invalidate the publisher's include pattern ``[Jenkins:L15]``.
        preceding_stage_failed: Whether the test stage that ran before this one
            failed. ``None`` is treated as ``False``. It is recorded and logged,
            and it must never influence whether the report is generated.

    Returns:
        A :class:`ReportResult`. ``succeeded`` is false only for a genuine
        report-generation fault -- the artifact root being unwritable, one of the
        two artifacts this stage produces being refused, or an unexpected defect
        in an adapter -- with the explanation in ``detail``.
    """
    started_at = time.perf_counter()
    failed_before = False if preceding_stage_failed is None else bool(preceding_stage_failed)

    try:
        return _run(
            base_dir=base_dir,
            sorting_method=sorting_method,
            run_id=run_id,
            preceding_stage_failed=failed_before,
            started_at=started_at,
        )
    except ReportGenerationError as error:
        detail = _redact(f"Stage {STAGE_NAME!r} could not generate the report: {error}")
        _LOGGER.error("%s", detail)
    except Exception as error:
        # Defensive totality. Every adapter is documented as total and every
        # artifact step is already isolated, so reaching this is a defect rather
        # than an expected path -- but a request handler and a pipeline
        # orchestrator both need a value back, and neither should see a traceback
        # from a reporting stage that cannot fail the build by design.
        detail = _redact(
            f"Stage {STAGE_NAME!r} could not generate the report "
            f"({type(error).__name__}: {error})"
        )
        _LOGGER.exception("%s", detail)

    return ReportResult(
        stage=STAGE_NAME,
        succeeded=False,
        detail=detail,
        run_id=run_id,
        preceding_stage_failed=failed_before,
        sorting_method=_resolve_sorting_method(sorting_method),
        duration_seconds=time.perf_counter() - started_at,
    )


# The route, the pipeline orchestrator and `scripts/generate_reports.py` each
# reach for this behaviour with a slightly different spelling. This alias is the
# same object, not a copy, so behaviour can never drift between the two names.
generate_reports = generate_report
