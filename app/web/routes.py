"""The two server-rendered views of the ``web_bp`` blueprint.

``GET /`` is the landing page and ``GET /reports`` is the index over the report
artifacts a test run writes. Those two rules are the whole of this module, and
the whole of this blueprint.

Source binding
--------------
The report sections of the project README, ``[README.md:L152-L161]``, are what
this module ports: ``### Jenkins Cucumber Reports`` (L152), ``##### HTML
Report:`` (L155) and ``##### Txt Report:`` (L159), together with the reporting
prose at ``[README.md:L42-L43]``, which records that the project produces JSON,
HTML and Txt reports plus screen shots -- "if you enable it" -- and error shots
"for your failed test cases". In the source system those artifacts were reachable
only from a CI job. This module makes them browsable over HTTP and adds nothing
else: the design goal of the whole surface is to make the four report artifacts,
the screen shots and the error shots retrievable and browsable, "and nothing
more".

There is no user interface being migrated here. The source project is a
Java/Maven Selenium-Cucumber automation scaffold that requires no interface at
all, and the only real interface in the picture is the external application under
test, which is driven through WebDriver and is out of scope. No component
library, style framework, design token vocabulary or client-side code is
introduced, and none is available to introduce.

Why exactly two rules
---------------------
The application's HTTP surface is deliberately closed. It is the smallest surface
that makes the existing behaviour addressable without inventing capability, and
every rule on it traces to a named pipeline stage or to a named report artifact.
This blueprint's share of that surface is one row of the plan's route table --
``GET /`` and ``GET /reports`` -- so this module registers two rules and no more.
The liveness probe is the one additive rule in the system and is registered
directly on the application by the factory, not here. Every ``/api/v1`` rule
belongs to :mod:`app.api`, which this module must never import: the two are
siblings at the same architectural layer.

Neither view accepts a request body or a path parameter, and neither declares any
verb beyond the framework default. Reading is all they do.

Artifact hyperlinks
-------------------
Every artifact record this module hands the page carries a falsy URL, so the page
renders each artifact as a plain path, carrying no hyperlink, beside a plain-text
availability word. That is a decision rather than an omission, and
:data:`_ARTIFACT_URL` records the reasoning at the point where the value is
defined.

Side-effect freedom
-------------------
Both views are pure reads. They inspect the artifact tree through the read-only
query surfaces in :mod:`app.reporting` and through the path constants in
:mod:`app.utils.paths`, and they change nothing: no directory is created, no file
is written or removed, no child process is started and no pipeline stage is
triggered. Loading a page can never clone a repository, run a test suite or
publish a report -- each of those belongs to a service module and each is reached
only through an explicit ``/api/v1`` call.

Creation of the artifact tree is owned by three places on purpose -- the path
module invoked by the application factory, the ``Makefile`` test recipe, and the
BDD ``conftest.py`` -- and a view handler is none of them.

Absence is the ordinary case
----------------------------
The artifact tree is ignored by version control, so it is absent in a fresh
checkout; the clean step wipes it before every run; screen shots appear only when
they are enabled; and error shots appear only once a test case has failed. Both
views therefore render normally when nothing has been generated, and every helper
below degrades to a described "not generated yet" state instead of raising. The
runtime configuration file is git-ignored and normally absent too, which is a
non-issue here because these views read no configuration at all: the page's
render contract has slots for the artifact list and a run summary, and nothing
else, so there is no setting for this module to read or default.

Deliberately preserved source behaviour
---------------------------------------
Three defects of the source system are visible from this page and are preserved
rather than corrected. ``docs/migration-parity.md`` is the authoritative register
of all of them, D1 through D9, and records the switch that opts into each
available fix.

* **D2 -- a run that selects no scenario is a success.** The preserved default
  selection expression comes from ``tags = "@LogOut"`` ``[README.md:L87]``, and no
  scenario in the feature file carries that tag, so the default run deselects
  everything. That outcome is reported here as an ordinary successful run with a
  scenario count of zero: no alarm, no fault state and no call to action. See
  :func:`_run_detail`.
* **D3 -- the test stage is non-gating.** Failure tolerance is switched on at
  ``[pom.xml:L25]`` and all six report thresholds are ``-1`` at ``[Jenkins:L15]``,
  which the publisher reads as no threshold. A run that records failing scenarios
  still completes and still publishes, so failure counts are presented here as
  plain facts and never as a service fault. See :func:`_run_detail`.
* **D8 -- the two broken README image links.** ``[README.md:L153]`` and
  ``[README.md:L165]`` reference two image files that have never existed. They are
  not recreated, not referenced and not stood in for: this module emits no image
  of any kind, and the page carries availability in words instead.

Rules status
------------
No user-specified rules were provided for this project; the rules document is
empty, so no file enters scope by rule mandate and none is invented here. The
work is held instead to the plan's enterprise baseline: full type annotations,
one-direction internal dependencies, configuration never read from a module
global, no shell invocation, explicit encodings on any byte stream, a module
logger rather than console writes, and every preserved defect documented where it
is observable.
"""

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Final, NamedTuple, TypedDict

from flask import render_template

from app.reporting import cucumber_json, html_report, pretty_reports, screenshots
from app.utils import paths
from app.web import web_bp

# The two view functions are this module's public surface. They are attached to
# the blueprint by the decorators below, so `app.web` only has to import this
# module; nothing needs to read these names directly.
__all__ = ["index", "reports"]

# A module logger, and nothing else. Handlers, levels and formatters belong
# exclusively to `app/logging_config.py`, consistent with the preserved `*.log`
# ignore rule at [.gitignore:L6]. Nothing here writes to a console stream.
_LOGGER: Final[logging.Logger] = logging.getLogger(__name__)


# =============================================================================
# Presentation vocabulary.
#
# Human-readable artifact names and the two artifact kinds the page renders in
# its "File or directory" column. These are display strings only: not one of
# them spells a path, because the artifact root is spelled in exactly one place
# in the application package -- `app/utils/paths.py` -- and every path below is
# imported from there.
# =============================================================================

KIND_FILE: Final[str] = "file"
"""Kind of an artifact that is a single file."""

KIND_DIRECTORY: Final[str] = "directory"
"""Kind of an artifact that is a directory of files."""

NAME_CUCUMBER_HTML: Final[str] = "Cucumber HTML report"
"""Display name of the HTML report declared at [README.md:L79]."""

NAME_CUCUMBER_JSON: Final[str] = "Cucumber JSON report"
"""Display name of the JSON report declared at [README.md:L80]."""

NAME_RERUN_MANIFEST: Final[str] = "Rerun manifest"
"""Display name of the rerun manifest declared at [README.md:L81]."""

NAME_PRETTY_REPORTS: Final[str] = "Pretty reports"
"""Display name of the PrettyReports directory declared at [README.md:L82]."""

NAME_SCREEN_SHOTS: Final[str] = "Screen shots"
"""Display name of the screen shots described at [README.md:L42-L43]."""

NAME_ERROR_SHOTS: Final[str] = "Error shots"
"""Display name of the error shots described at [README.md:L42-L43]."""

NAME_SUREFIRE_REPORTS: Final[str] = "Surefire reports"
"""Display name of the report directory whose name is retained for parity."""


# =============================================================================
# THE ARTIFACT URL RESOLUTION.
#
# Every artifact record this module produces carries this value as its `url`,
# and the value is None. The page renders a hyperlink only while an artifact's
# URL is truthy, so the result is that each artifact is shown as a plain path,
# carrying no hyperlink, with its availability described in words. That is the
# correct answer here, for three reasons that all hold at once:
#
#   1. The HTTP surface is closed. It is the smallest surface that makes the
#      existing behaviour addressable without inventing capability, and the
#      binding directive on this refactor is that no feature may be dropped and
#      none may be added. Registering a rule that streams an artifact would add
#      one, so no such rule exists and none may be added here.
#
#   2. The artifact-retrieval rules that do exist are `/api/v1` rules, and each
#      one is addressed by an opaque run correlation identifier. That identifier
#      lives only in the test runner service's in-memory run registry, is never
#      used to build a filesystem path, and is not available to a page load. An
#      unknown identifier is answered with a not-found, so inventing, guessing
#      or synthesising one would produce precisely the dead hyperlink this page
#      forbids. It is therefore never done.
#
#   3. Naming a rule that is not registered raises while the page is being
#      rendered, which would turn a 200 into a 500. Only a view function can
#      know which retrieval rules exist, which is why the page asks its view for
#      each URL rather than building one itself -- and why the honest answer
#      from this view is "none".
#
# None of this loses a capability: the report artifacts remain retrievable over
# HTTP through the `/api/v1` retrieval rules, which `docs/api.md` and
# `docs/reporting.md` document. This page's job is to say what exists and where
# it is written, which it does for all seven artifacts whether or not they are
# there.
#
# One artifact deserves an explicit note. The generated HTML report is
# self-contained: the toolchain writes a single file that carries its own
# styling and has no sibling assets. It is listed and, where a retrieval rule
# exists, served byte for byte as that toolchain wrote it. It is never parsed,
# never re-templated, never restyled and never reduced to a fragment, because
# re-rendering it would risk diverging from the source's own output.
# =============================================================================

_ARTIFACT_URL: Final[str | None] = None


# =============================================================================
# State sentences.
#
# One operator-safe sentence per state that no reporting adapter describes for
# us. Every sentence is a statement of fact: none implies a fault, because
# neither an absent artifact nor a failing scenario is one here.
# =============================================================================

_RERUN_PRESENT_DETAIL: Final[str] = (
    "The rerun manifest is present. It holds one location for every scenario "
    "that recorded a failing step, and is empty when none did."
)

_RERUN_ABSENT_DETAIL: Final[str] = (
    "The rerun manifest has not been generated yet. It is derived from the "
    "Cucumber JSON report, so it appears once a run has produced one."
)

_SUREFIRE_PRESENT_DETAIL: Final[str] = (
    "The Surefire-compatible report directory is present. Its name is retained "
    "verbatim so that any report consumer keyed on that path keeps finding content."
)

_SUREFIRE_ABSENT_DETAIL: Final[str] = (
    "The Surefire-compatible report directory has not been generated yet. It is "
    "populated by the JUnit-XML report the test runner requests."
)

_JSON_VALID_DETAIL: Final[str] = (
    "The Cucumber JSON report is present and matches the documented report "
    "schema. It is the report the CI publisher collects."
)

_UNINSPECTABLE_DETAIL: Final[str] = (
    "This artifact could not be inspected just now, so it is listed as not "
    "available. Nothing else on this page is affected."
)

_RUN_STATUS_COMPLETED: Final[str] = "Completed"
"""The only run status this page reports.

The test stage is non-gating: failure tolerance is on ``[pom.xml:L25]`` and all
six report thresholds are ``-1`` ``[Jenkins:L15]``. A run that selects nothing
and a run that records failing scenarios both complete, and the report stage runs
either way, so there is no second status for this page to report. Defect D3 is
preserved deliberately -- see ``docs/migration-parity.md``.
"""

_RUN_EMPTY_DETAIL: Final[str] = (
    "No scenario was selected. That is the expected result of the preserved "
    "default selection expression, and a run that selects nothing completes "
    "successfully."
)


_SENTENCE_TERMINATORS: Final[tuple[str, ...]] = (".", "!", "?")
"""Characters that already end a sentence, for :func:`_as_sentence`."""


def _as_sentence(text: str) -> str:
    """Normalise an adapter's state wording into one displayable sentence.

    The page's render contract asks each artifact record for "one operator-safe
    sentence", and the reporting adapters do not all speak in whole sentences: a
    couple describe a state with a lower-case fragment carrying no terminator,
    because their wording is also consumed by callers that embed it mid-phrase.
    Presenting those side by side with the fully-formed sentences supplied here
    would read as an inconsistency to an operator, so this is where the two are
    reconciled.

    Purely presentational, and deliberately so. It capitalises and terminates; it
    never translates, paraphrases, truncates or reorders. The adapters remain the
    single source of what each state *means* -- this only decides how that reads
    in a table cell, which is a view concern.

    Args:
        text: Wording from an adapter, or one of this module's own sentences.

    Returns:
        The text as a sentence, or an empty string when there is nothing to say.
        An empty result is intentional: the page renders the detail element only
        for a non-empty value, so nothing appears rather than an empty element.
    """
    stripped = text.strip()
    if not stripped:
        return ""
    if not stripped.endswith(_SENTENCE_TERMINATORS):
        stripped = f"{stripped}."
    return stripped[0].upper() + stripped[1:]


# =============================================================================
# Record types.
#
# The page accepts mapping-like records and resolves every field through a
# guard, so a typed mapping is the natural shape: key access and attribute
# access resolve identically in the template, and a `TypedDict` gives the type
# checker the exact key set without the page having to change how it reads a
# row.
# =============================================================================


class _ArtifactState(NamedTuple):
    """The part of an artifact record that has to be read from the filesystem.

    Split out from the record itself so that reading state and describing an
    artifact stay separate concerns: the name, path and kind of every artifact are
    static facts known from the specification, while these three fields are the
    only ones that depend on what is on disk right now.
    """

    exists: bool
    """Whether anything at all is present at the artifact's path."""

    available: bool
    """Whether the artifact can be served right now.

    Deliberately distinct from :attr:`exists`. A shots directory can exist while
    holding no shots, and a report directory can exist without the document that
    makes it usable; in both cases something is present but nothing is servable.
    The page keys its availability wording on this field and consults
    :attr:`exists` only when it is absent, so both are always supplied.
    """

    detail: str
    """One operator-safe sentence explaining the state."""


class ArtifactRecord(TypedDict):
    """One row of the report index, in exactly the shape the page consumes.

    Field meanings follow the page's own render contract:

    * ``name`` -- human-readable artifact name.
    * ``path`` -- the artifact path relative to the repository root, spelled with
      forward slashes. Always produced by :func:`app.utils.paths.to_posix` from a
      path constant, so a platform-native separator can never reach the page and
      invalidate the CI publisher's include glob, and so an absolute host path --
      which would leak the deployment layout -- can never be rendered.
    * ``kind`` -- :data:`KIND_FILE` or :data:`KIND_DIRECTORY`.
    * ``exists`` and ``available`` -- see :class:`_ArtifactState`.
    * ``detail`` -- one operator-safe sentence.
    * ``url`` -- where to retrieve the artifact. Always :data:`_ARTIFACT_URL`;
      see that constant for why.
    """

    name: str
    path: str
    kind: str
    exists: bool
    available: bool
    detail: str
    url: str | None


class RunRecord(TypedDict):
    """The summary the page renders beneath the artifact table.

    The run correlation identifier is deliberately not a field. It lives only in
    the test runner service's in-memory run registry, a page load has no way to
    obtain one, and fabricating one would be worse than useless. The page treats
    the identifier as optional, so omitting the key simply omits that row.
    """

    status: str
    scenarios: int
    detail: str


# =============================================================================
# Artifact state probes.
#
# One probe per artifact. Each is a pure read that asks a reporting adapter --
# never a service -- for the current state, and each is called with no base
# directory. That default matters twice over: it is what keeps every returned
# path and every returned sentence relative to the repository root rather than
# absolute, and it is the same spelling the test configuration, the committed
# configuration templates and the CI publisher already use.
#
# No probe parses a report. Loading and validating the JSON report, describing
# the HTML report, indexing the shot directories and inspecting the generated
# report directory are all adapter responsibilities, and each adapter is
# documented to degrade gracefully when its artifact is missing.
# =============================================================================


def _probe_cucumber_html() -> _ArtifactState:
    """Describe the HTML report declared at [README.md:L79].

    The adapter is a locator and describer only: it reports where the report is,
    whether it can be served and how large it is, and it never re-renders the
    document. That is the required behaviour -- the report is self-contained and
    must reach a consumer exactly as the toolchain wrote it.

    Returns:
        The report's current state.
    """
    artifact = html_report.describe_html_report()
    return _ArtifactState(
        exists=artifact.exists,
        available=artifact.is_available,
        detail=artifact.detail,
    )


def _probe_cucumber_json() -> _ArtifactState:
    """Describe the JSON report declared at [README.md:L80].

    The adapter distinguishes three states -- absent, present but not matching the
    documented schema, and valid -- and supplies its own sentence for the first
    two. Only the valid case needs wording from here.

    Returns:
        The report's current state.
    """
    report = cucumber_json.load_report()
    return _ArtifactState(
        exists=not report.is_absent,
        available=report.is_valid,
        detail=report.reason or _JSON_VALID_DETAIL,
    )


def _probe_rerun_manifest() -> _ArtifactState:
    """Describe the rerun manifest declared at [README.md:L81].

    The rerun adapter is the sole producer of this manifest, and every entry point
    it offers either writes the file or needs the file's content already in hand.
    A view handler may do neither, so availability is established here with a
    single read-only status check against the path constant, and the sentence is
    supplied locally. This is the one artifact for which no adapter offers a
    read-only description.

    Returns:
        The manifest's current state.
    """
    present = paths.RERUN_TXT_PATH.is_file()
    return _ArtifactState(
        exists=present,
        available=present,
        detail=_RERUN_PRESENT_DETAIL if present else _RERUN_ABSENT_DETAIL,
    )


def _probe_pretty_reports() -> _ArtifactState:
    """Describe the report directory declared at [README.md:L82].

    Uses the adapter's cheap status query rather than its generator, which is the
    whole reason that query exists: an index page has to be able to ask "has this
    been generated?" without generating anything.

    The wording is taken from the adapter's per-status sentence table rather than
    from its composed description. Both say the same thing, but the composed form
    embeds the directory path twice, and the path is already the neighbouring
    column on this page -- printing it three times across one row is noise, not
    information.

    Returns:
        The directory's current state.
    """
    directory = pretty_reports.describe_pretty_reports()
    absent = directory.status is pretty_reports.PrettyReportsDirectoryStatus.ABSENT
    return _ArtifactState(
        exists=not absent,
        available=directory.is_generated,
        detail=pretty_reports.DIRECTORY_STATUS_DETAILS[directory.status],
    )


def _shot_state(collection: screenshots.ShotCollection) -> _ArtifactState:
    """Convert one shot collection into an artifact state.

    Shared by the two shot probes, which stay separate functions so that the two
    categories remain distinct rows on the page. They are separate artifacts with
    separate triggers -- screen shots are produced only when enabled, error shots
    only for a failed test case ``[README.md:L42-L43]`` -- and merging them would
    hide that difference.

    Args:
        collection: The indexed collection for one category.

    Returns:
        The collection's current state. A directory that exists but holds no shots
        is present without being servable, which is exactly the distinction
        between the two boolean fields.
    """
    return _ArtifactState(
        exists=collection.exists,
        available=collection.has_shots,
        detail=collection.description,
    )


def _probe_screen_shots() -> _ArtifactState:
    """Describe the screen shots described at [README.md:L42-L43].

    Returns:
        The screen shot directory's current state.
    """
    return _shot_state(screenshots.list_screen_shots())


def _probe_error_shots() -> _ArtifactState:
    """Describe the error shots described at [README.md:L42-L43].

    Returns:
        The error shot directory's current state.
    """
    return _shot_state(screenshots.list_error_shots())


def _probe_surefire_reports() -> _ArtifactState:
    """Describe the report directory whose name is retained for consumer parity.

    This directory has no README counterpart and no reporting adapter, so -- as
    with the rerun manifest -- availability is a single read-only status check
    against the path constant.

    Returns:
        The directory's current state.
    """
    present = paths.SUREFIRE_REPORTS_DIR.is_dir()
    return _ArtifactState(
        exists=present,
        available=present,
        detail=_SUREFIRE_PRESENT_DETAIL if present else _SUREFIRE_ABSENT_DETAIL,
    )


# =============================================================================
# The artifact table, and its order.
#
# ORDER IS THIS MODULE'S RESPONSIBILITY. The page renders the rows strictly as it
# is handed them and never re-orders, groups or filters them, so the order below
# is the order an operator sees. It is fixed at import time from a literal tuple,
# which makes it deterministic, identical on every request, and independent of
# any filesystem iteration order.
#
# The order chosen is the source declaration order of the four report plugins in
# the documented test runner, [README.md:L79-L82], followed by the screen shots
# and then the error shots of [README.md:L42-L43], and finally the report
# directory whose name is retained for consumer parity. Declaration order is
# preferred over an alphabetical arrangement because it is the order the source
# system itself states these artifacts in, so a reader comparing this page with
# the runner block reads the same list twice.
#
# The publisher's own `sortingMethod: 'ALPHABETICAL'` [Jenkins:L15] is a
# different thing and is deliberately not implemented here: it orders report
# *features* and belongs to `app/services/report_service.py`, while the constant
# itself belongs to `app/reporting/thresholds.py`. Neither value is redeclared in
# this module -- configuration values are data and live in exactly one place.
#
# Every path comes from `app/utils/paths.py`, which is the single place in the
# application package where the artifact root is spelled. No path is written out
# here, and the root is never renamed or relabelled: the CI publisher's include
# glob [Jenkins:L15] keeps working precisely because the output stays underneath
# the original root, which makes that name a contract rather than a leftover.
# =============================================================================


class _ArtifactSpec(NamedTuple):
    """Everything needed to build one row of the report index."""

    name: str
    """Human-readable artifact name."""

    path: Path
    """The artifact path, always a constant imported from :mod:`app.utils.paths`."""

    kind: str
    """:data:`KIND_FILE` or :data:`KIND_DIRECTORY`."""

    probe: Callable[[], _ArtifactState]
    """The read-only probe that reports this artifact's current state."""


_ARTIFACT_SPECS: Final[tuple[_ArtifactSpec, ...]] = (
    _ArtifactSpec(
        name=NAME_CUCUMBER_HTML,
        path=paths.CUCUMBER_HTML_PATH,
        kind=KIND_FILE,
        probe=_probe_cucumber_html,
    ),
    _ArtifactSpec(
        name=NAME_CUCUMBER_JSON,
        path=paths.CUCUMBER_JSON_PATH,
        kind=KIND_FILE,
        probe=_probe_cucumber_json,
    ),
    _ArtifactSpec(
        name=NAME_RERUN_MANIFEST,
        path=paths.RERUN_TXT_PATH,
        kind=KIND_FILE,
        probe=_probe_rerun_manifest,
    ),
    _ArtifactSpec(
        name=NAME_PRETTY_REPORTS,
        path=paths.PRETTY_REPORTS_DIR,
        kind=KIND_DIRECTORY,
        probe=_probe_pretty_reports,
    ),
    _ArtifactSpec(
        name=NAME_SCREEN_SHOTS,
        path=paths.SCREENSHOTS_DIR,
        kind=KIND_DIRECTORY,
        probe=_probe_screen_shots,
    ),
    _ArtifactSpec(
        name=NAME_ERROR_SHOTS,
        path=paths.ERROR_SHOTS_DIR,
        kind=KIND_DIRECTORY,
        probe=_probe_error_shots,
    ),
    _ArtifactSpec(
        name=NAME_SUREFIRE_REPORTS,
        path=paths.SUREFIRE_REPORTS_DIR,
        kind=KIND_DIRECTORY,
        probe=_probe_surefire_reports,
    ),
)
"""The seven artifacts this page lists, in render order. Do not reorder."""


def _record_for(spec: _ArtifactSpec) -> ArtifactRecord:
    """Build one artifact record, and never raise while doing it.

    This is the single guard point for the whole page. Every reporting adapter is
    documented to degrade gracefully rather than raise, so the fallback below is
    defence in depth rather than an expected path -- but an index page whose only
    job is to report what exists must not turn an unreadable directory or a
    restricted filesystem into a server error. An artifact that cannot be
    inspected is therefore reported as not available, with a sentence saying so,
    and the remaining rows are unaffected.

    Args:
        spec: The artifact to describe.

    Returns:
        A fully populated record. ``path`` is always relative and always spelled
        with forward slashes; ``detail`` is always a sentence; ``url`` is always
        :data:`_ARTIFACT_URL`.
    """
    try:
        state = spec.probe()
    except Exception:
        # Logged, not raised, and logged through the module logger so that the
        # logging configuration owned by `app/logging_config.py` decides where it
        # goes. `exc_info` keeps the cause diagnosable.
        _LOGGER.warning("Could not inspect the %s artifact", spec.name, exc_info=True)
        state = _ArtifactState(exists=False, available=False, detail=_UNINSPECTABLE_DETAIL)
    return ArtifactRecord(
        name=spec.name,
        path=paths.to_posix(spec.path),
        kind=spec.kind,
        exists=state.exists,
        available=state.available,
        detail=_as_sentence(state.detail),
        url=_ARTIFACT_URL,
    )


def _collect_artifacts() -> tuple[ArtifactRecord, ...]:
    """Describe all seven artifacts, in render order.

    Returns:
        An immutable tuple of records, one per entry in :data:`_ARTIFACT_SPECS`
        and in the same order. Always seven records: an artifact that has not been
        generated is described rather than hidden, because a fresh checkout has
        none of them and the clean step removes all of them before every run.
    """
    return tuple(_record_for(spec) for spec in _ARTIFACT_SPECS)


def _run_detail(summary: cucumber_json.RunSummary) -> str:
    """Describe a run's result in one sentence, without ever implying a fault.

    Two preserved source behaviours are visible here, and both are deliberate.
    ``docs/migration-parity.md`` is their authoritative register.

    **D2.** The preserved default selection expression, ``tags = "@LogOut"``
    ``[README.md:L87]``, matches no scenario in the feature file, so the default
    run selects nothing. That is a successful zero-scenario run, and it is
    described as one: no fault state and no call to action.

    **D3.** Failure tolerance is switched on at ``[pom.xml:L25]`` and all six
    report thresholds are ``-1`` at ``[Jenkins:L15]``, which the publisher reads
    as no threshold. A run that records failing scenarios still completes and
    still publishes, so a failure count is stated as a plain fact.

    Args:
        summary: The summary derived from the JSON report.

    Returns:
        One factual sentence.
    """
    if summary.scenario_count == 0:
        return _RUN_EMPTY_DETAIL
    if summary.failed_scenario_count:
        return (
            f"{summary.failed_scenario_count} of {summary.scenario_count} scenarios "
            "recorded a failing step. The test stage tolerates that by design, so the "
            "run completed and the artifacts above were published either way."
        )
    return f"Every one of the {summary.scenario_count} selected scenarios passed."


def _latest_run() -> RunRecord | None:
    """Summarise the most recent run, if one can be summarised.

    The summary is derived from the JSON report, which is the only durable record
    of a run this layer can read. No service is consulted and no run is started.

    Returns:
        A record, or ``None`` when there is nothing to summarise -- either because
        no run has produced a report yet or because the report present does not
        match the documented schema. In both cases the artifact table already
        carries the explanation, and the page simply omits the summary.
    """
    try:
        report = cucumber_json.load_report()
        if not report.is_valid:
            return None
        summary = report.summary
    except Exception:
        _LOGGER.warning("Could not summarise the most recent run", exc_info=True)
        return None
    return RunRecord(
        status=_RUN_STATUS_COMPLETED,
        scenarios=summary.scenario_count,
        detail=_run_detail(summary),
    )


# =============================================================================
# The two views.
#
# One rule per view function, rather than one function carrying both rules. Two
# functions give the two pages two distinct endpoint names, which keeps them
# separately addressable and separately testable, and it keeps each function
# doing one thing -- the landing page needs no render context at all, while the
# report index needs the whole artifact table.
#
# Neither declares a verb: the framework default is a read, which is all either
# does. Every rule that changes something is a `/api/v1` rule and belongs to
# `app/api/routes.py`.
#
# No error handler is registered on this blueprint, deliberately. A blueprint
# does not own a URL space -- even one mounted at the root, which is exactly why
# it looks as though it should -- so a blueprint-scoped not-found or
# method-not-allowed handler never fires for an unmatched URL. All of them live
# in `app/errors.py` at application scope, and adding an application-wide handler
# from here would shadow or duplicate them.
# =============================================================================


@web_bp.route("/")
def index() -> str:
    """Render the landing page.

    Deliberately passes no render context. The template is static: it extends the
    shared layout and overrides two blocks, and references no variable at all, so
    handing it a context would be handing it something it cannot use. The shared
    layout owns the document shell, the single style sheet reference and the two
    navigation links, so this view supplies none of them.

    Returns:
        The rendered page.
    """
    return render_template("index.html")


@web_bp.route("/reports")
def reports() -> str:
    """Render the index over the generated report artifacts.

    Lists all seven artifacts in a fixed order with their real relative paths and
    their current availability, plus a summary of the most recent run when one can
    be derived. Renders identically whether or not anything has been generated:
    absence is the ordinary state of this tree, not an error.

    The page's two broken image references, ``[README.md:L153]`` and
    ``[README.md:L165]``, are defect D8 and are preserved: neither asset has ever
    existed, so neither is recreated, referenced or stood in for, and this view
    emits no image at all. Availability is carried by words instead. See
    ``docs/migration-parity.md``.

    Returns:
        The rendered page.
    """
    return render_template(
        "reports.html",
        artifacts=_collect_artifacts(),
        run=_latest_run(),
    )
