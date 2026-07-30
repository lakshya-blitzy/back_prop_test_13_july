"""Locator and descriptor for the Cucumber HTML report artifact.

The artifact this module speaks for is served **verbatim** and is **never
re-rendered**. The migration plan states the reason outright: the generated
Cucumber HTML report "is served as a static artifact, not re-rendered.
Re-rendering it would risk diverging from the source's output." This module
therefore does exactly three things -- it *locates* the artifact, *validates*
whether it is usable, and *describes* it for the HTTP layer -- and it
deliberately never produces, templates, transforms, rewrites, minifies,
prettifies, inspects or post-processes a single byte of the report's contents.

Ported source construct
-----------------------
One reporting adapter per Cucumber plugin declaration. The declaration this
module ports is the first entry of the ``@CucumberOptions.plugin`` array on the
documented ``CukesRunner``::

    "html:target/cucumber-reports.html"

That is quoted verbatim, and it is cited by literal value rather than by line
number on purpose: the line numbers recorded for the four plugin strings in the
migration plan are off by one -- the HTML entry is on ``README.md`` line 79, not
78 -- so the literal is the only citation that cannot be wrong. Note the file
name closely: ``cucumber-reports.html``, plural and hyphenated. A singular or
underscored spelling would silently break the artifact contract, which is one of
several reasons the name is never spelled out in this module's code and is
instead imported from ``app/utils/paths.py``.

What writes the file
--------------------
Not this module, and not any code the project owns. ``pytest-html`` 4.2.0 writes
it during the test run, driven by the ``--html`` option and
``--self-contained-html`` that the ported ``pytest.ini`` carries -- the direct
translation of the plugin declaration above. Two of the four source plugins map
onto a pytest option in that way, the JSON one and this HTML one, while the
other two have no option at all and are produced by adapters the project owns
(``rerun_report.py`` and ``pretty_reports.py``). That asymmetry is precisely why
this is the smallest of the four adapters: its artifact arrives ready to serve.

``pytest-html`` is consequently the most tempting import in this file and the
most firmly forbidden one. It is a harness distribution declared in
``requirements-test.txt``, whereas the deployed container is built from
``requirements.txt`` alone, so importing it would break the application at
start-up in production while looking perfectly healthy in development. Read the
file it wrote; never import the writer.

"Not generated yet" is the normal state
---------------------------------------
An absent report is *not* an error, for two independent reasons:

* A fresh checkout has nothing beneath the artifact root at all. The whole tree
  is ephemeral and git-ignored, and no run has happened yet.
* The ported runner's default tag expression selects zero scenarios. The source
  runner declared ``tags = "@LogOut"`` while no scenario in the feature file
  carries that tag, so a default run legitimately produces no report at all.
  That is preserved defect **D2**; ``docs/migration-parity.md`` is its
  authoritative register and records the configuration switch that opts into a
  fix.

Absence is therefore *reported*, never raised -- and never papered over. No
placeholder report is fabricated to make the surface look populated, because
inventing output the source system would not have produced is exactly the
fabrication the migration plan forbids.

Non-gating by design
--------------------
The source build could not fail on test failures: Surefire was configured with
``<testFailureIgnore>true</testFailureIgnore>`` ``[pom.xml:L25]`` and all six CI
publisher thresholds are ``-1`` ``[Jenkins:L15]``. The report stage runs
unconditionally after the test stage, including after failures. This module
honours that in the only way available to it: every function returns a value,
none of them raises, and nothing here contributes to an exit code or a pipeline
verdict. That is preserved defect **D3** -- intentional, documented, and never
to be turned into a gate.

Scope boundaries -- deliberate omissions
----------------------------------------
* **No rendering, no parsing.** Nothing here emits markup, and nothing reads the
  report's bytes to scrape counts back out of them. A run summary comes from the
  Cucumber JSON through ``cucumber_json.py``, which is the artifact that has a
  schema; the HTML is a presentation surface and is treated as opaque.
* **No file I/O at all.** Metadata comes from a single ``stat`` call, which is
  cheaper than reading the file and sidesteps encoding entirely: no text stream
  is ever opened here, so the project-wide "always state ``encoding='utf-8'``"
  requirement has nothing in this module to bind to.
* **No filesystem mutation.** Creating the artifact tree belongs to
  ``app/utils/paths.py``, the ``Makefile`` ``test`` target and the BDD
  ``conftest.py``; wiping it belongs to the ``Makefile`` ``clean`` target, the
  port of ``mvn clean``. This module is strictly read-only on disk.
* **No process execution.** Running the suite belongs to
  ``app/services/test_runner_service.py``, which also owns the platform dispatch
  and the exit-code policy.
* **No HTTP.** No framework object, no request, no response, no status code and
  no error handler. This module hands the API and web blueprints an immutable
  value object; mapping it onto a response, and streaming the bytes, is theirs.
* **No report sorting.** ``sortingMethod: 'ALPHABETICAL'`` ``[Jenkins:L15]``
  belongs to ``app/services/report_service.py``.
* **No sibling-asset handling.** The report is written self-contained, so its
  CSS and JavaScript are inlined and exactly one file has to be served. There is
  no asset directory to locate, and looking for one would be dead code.

One cross-reference is worth recording rather than acting on: the repository's
``.gitattributes`` already carries ``*.html linguist-detectable=false`` as its
first rule, so the generated report needs no attribute work of its own and
nothing here touches that file.

Layering and runtime dependencies
---------------------------------
The application's single permitted direction is ``api -> services -> reporting
-> utils``. This module may therefore import the Python standard library and
``app.utils.*``, and nothing else: never ``app.services``, ``app.api``,
``app.web``, ``app.config`` or the ``app`` package root, and never anything
beneath ``tests/`` or ``scripts/``. It has zero third-party imports, so it loads
cleanly in a deployment that installs the eleven runtime pins alone.

Artifact locations always come from ``app/utils/paths.py``. No path literal is
composed here: the artifact root name is defined in exactly one place in the
application, and re-spelling it anywhere else would put the CI publisher's
include pattern at risk -- the very contract that retaining the Maven-flavoured
root name exists to protect.

Usage
-----
::

    >>> from app.reporting import html_report
    >>> artifact = html_report.describe_html_report()
    >>> artifact.status                    # a fresh checkout has no report yet
    <HtmlReportStatus.MISSING: 'missing'>
    >>> artifact.is_available
    False
    >>> artifact.content_type              # what the download route must send
    'text/html; charset=utf-8'
    >>> html_report.html_report_path().name
    'cucumber-reports.html'
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from stat import S_ISREG
from types import MappingProxyType
from typing import Final

from app.utils import paths

__all__ = [
    "HTML_CHARSET",
    "HTML_CONTENT_TYPE",
    "HTML_MEDIA_TYPE",
    "PLUGIN_DECLARATION",
    "PLUGIN_KEYWORD",
    "PRODUCER_DISTRIBUTION",
    "SELF_CONTAINED",
    "STATUS_DETAILS",
    "HtmlReportArtifact",
    "HtmlReportStatus",
    "describe_html_report",
    "html_report_path",
    "is_html_report_available",
    "is_within_artifact_root",
]

# A module logger, and nothing more. Handlers, levels and formatters belong
# exclusively to `app/logging_config.py`, which this module must not import
# (it sits above the reporting layer). Every message emitted below is therefore
# a structured, lazily-formatted record on this logger, and never console output.
_LOGGER: Final[logging.Logger] = logging.getLogger(__name__)


# =============================================================================
# Content type.
#
# The download route sends the bytes on disk unchanged, so the only thing it
# needs from this module -- besides the path -- is the header value to send with
# them. The report is written as UTF-8 and declares that charset internally, so
# stating it explicitly here keeps the header and the document in agreement
# instead of leaving the encoding to a browser's guess.
# =============================================================================

HTML_MEDIA_TYPE: Final[str] = "text/html"
"""The report's media type, without parameters."""

HTML_CHARSET: Final[str] = "utf-8"
"""The character encoding the report is written in."""

HTML_CONTENT_TYPE: Final[str] = f"{HTML_MEDIA_TYPE}; charset={HTML_CHARSET}"
"""``text/html; charset=utf-8`` -- the exact header value a download route sends.

Assembled from the two parts above so that the media type and the charset can
never disagree with the header. Callers that need the parts separately have
:data:`HTML_MEDIA_TYPE` and :data:`HTML_CHARSET`.
"""


# =============================================================================
# Source parity.
#
# Configuration values are data, never decisions, so the ported plugin
# declaration is reproduced rather than paraphrased. The keyword below is the
# Cucumber plugin name; the path half comes from `app/utils/paths.py`, which
# means the declaration is assembled from the one authoritative definition of
# the artifact location and can never drift away from it.
#
# The construct being ported, quoted verbatim from the `@CucumberOptions.plugin`
# array of the documented `CukesRunner` and cited by literal rather than by line
# number, is:
#
#     "html:target/cucumber-reports.html"
#
# Its Python producer is `pytest-html` 4.2.0, driven from `pytest.ini` by the
# `--html` option plus `--self-contained-html`. The README additionally
# documents a one-off command for this artifact; as printed it carries an EN
# DASH instead of a double hyphen and so would not parse, and the corrected
# `--plugin` spelling belongs to the README, the docs and the report script --
# never to this module, which invokes nothing.
# =============================================================================

PLUGIN_KEYWORD: Final[str] = "html"
"""The Cucumber plugin keyword this adapter ports."""

PLUGIN_DECLARATION: Final[str] = f"{PLUGIN_KEYWORD}:{paths.to_posix(paths.CUCUMBER_HTML_PATH)}"
"""The ported plugin declaration, rebuilt from the canonical artifact path.

Exposed as data so that a parity test, a configuration-introspection endpoint or
the documentation can assert the port still names the same artifact the Java
runner named, without any of them hard-coding the path a second time. It is
rendered with forward slashes on every platform, exactly as the source declared
it.
"""

PRODUCER_DISTRIBUTION: Final[str] = "pytest-html"
"""Name of the distribution that writes the artifact.

Recorded for provenance only -- so that an operator reading a log line or an API
payload knows what to install to obtain the report. This module never imports it:
it is a harness dependency and absent from the deployed image.
"""

SELF_CONTAINED: Final[bool] = True
"""Whether the report inlines its own assets, making it a single servable file.

``True`` because the run is configured with ``--self-contained-html``, which
inlines the report's CSS and JavaScript. The practical consequence for the HTTP
layer is that there are no sibling asset files: serving exactly one path is
complete, and no asset-directory handling is required anywhere.
"""


# =============================================================================
# Outcomes.
# =============================================================================


class HtmlReportStatus(StrEnum):
    """Every outcome describing the HTML report can have.

    Six members rather than a bare boolean, because the HTTP layer can only
    answer a caller honestly if it can tell "no run has happened yet" apart from
    "a run happened but wrote nothing" and from "something is in the way". Only
    :attr:`AVAILABLE` means the artifact can be served.

    A :class:`~enum.StrEnum` so that the value serialises straight into a JSON
    payload or a log line without a conversion table.
    """

    AVAILABLE = "available"
    """A non-empty regular file is present and can be served as-is."""

    EMPTY = "empty"
    """The file is present but zero bytes long, so there is nothing to serve."""

    MISSING = "missing"
    """Nothing exists at the path: the report has not been generated yet."""

    NOT_A_FILE = "not_a_file"
    """Something that is not a regular file -- a directory, say -- is in the way."""

    INACCESSIBLE = "inaccessible"
    """The path exists in some form but could not be inspected."""

    OUTSIDE_ROOT = "outside_root"
    """The path resolves outside the artifact root and is refused on principle."""


STATUS_DETAILS: Final[Mapping[HtmlReportStatus, str]] = MappingProxyType(
    {
        HtmlReportStatus.AVAILABLE: "The Cucumber HTML report is present and can be served as-is.",
        HtmlReportStatus.EMPTY: (
            "The Cucumber HTML report exists but is empty, so there is nothing to serve."
        ),
        HtmlReportStatus.MISSING: (
            "The Cucumber HTML report has not been generated yet. Run the test suite to "
            "produce it; note that the preserved default tag expression selects no "
            "scenarios, so a default run writes no report."
        ),
        HtmlReportStatus.NOT_A_FILE: (
            "The Cucumber HTML report path is not a regular file, so it cannot be served."
        ),
        HtmlReportStatus.INACCESSIBLE: "The Cucumber HTML report could not be inspected.",
        HtmlReportStatus.OUTSIDE_ROOT: (
            "The Cucumber HTML report path resolves outside the artifact root and was refused."
        ),
    }
)
"""Human-readable explanation for each :class:`HtmlReportStatus`.

A read-only view, so a caller cannot reword one of these messages for everybody
else. The :attr:`~HtmlReportStatus.INACCESSIBLE` entry is the only one a caller
receives augmented: :func:`describe_html_report` appends the underlying error to
it, because "could not be inspected" is useless to an operator without the
reason.
"""


# =============================================================================
# The descriptor.
# =============================================================================


@dataclass(frozen=True, slots=True)
class HtmlReportArtifact:
    """An immutable snapshot of the HTML report artifact at one point in time.

    This is the value object the API and web blueprints consume: it carries
    everything a download route needs to serve the report -- the path, whether
    there is anything worth serving, how large it is and what header to send --
    and everything a report index needs to describe it, without either layer
    touching the filesystem itself.

    Frozen on purpose. A route handler holding this object must not be able to
    edit the path it is about to serve, and the snapshot must not appear to
    change under a caller that passes it on. It is a snapshot rather than a live
    view: the artifact tree is rewritten by every run, so a descriptor is only
    ever a statement about the moment :func:`describe_html_report` looked. Call
    that function again for a fresh answer instead of caching one of these.

    Note what is deliberately absent: no scenario counts, no pass/fail totals and
    no summary of any kind. Deriving those would mean reading the report's
    markup, and this artifact is served unmodified precisely so that it cannot
    diverge from what the source toolchain produced. Summaries come from the
    Cucumber JSON, which has a schema for exactly that purpose.
    """

    path: Path
    """Where the artifact is expected to live.

    Always inside the artifact root, and always ending in the report's file name
    as ``app/utils/paths.py`` defines it. It is populated even when the artifact
    is absent, so an API layer can tell a caller *where* the missing report would
    appear.
    """

    status: HtmlReportStatus
    """The outcome of inspecting :attr:`path`."""

    detail: str
    """A human-readable explanation of :attr:`status`, safe to show an operator.

    Taken from :data:`STATUS_DETAILS`, with the underlying error appended when
    the artifact could not be inspected.
    """

    exists: bool = False
    """Whether *something* is present at :attr:`path`.

    True for a usable report, for an empty one and for a non-regular entry in the
    way; false when nothing is there, when the entry could not be inspected at
    all, and when the path resolved outside the artifact root. Existence alone
    never means servable -- use :attr:`is_available` for that.
    """

    size_bytes: int = 0
    """Size of the report in bytes, or ``0`` when there is no regular file.

    Reported as ``0`` for a non-regular entry as well, because the size of a
    directory says nothing about a report.
    """

    modified_timestamp: float | None = None
    """Raw POSIX modification time, or ``None`` when it could not be determined.

    Kept alongside :attr:`modified_at` deliberately: this is the unrounded value
    the filesystem reported, so a caller comparing against a fresh stat, or
    deciding whether a cached copy is current, can do so exactly.
    """

    modified_at: datetime | None = None
    """Timezone-aware UTC modification time, or ``None`` if unavailable.

    UTC rather than local time so that a report index, an API payload and a log
    line all agree regardless of where the process runs.
    """

    content_type: str = HTML_CONTENT_TYPE
    """The header value a download route must send with the bytes."""

    self_contained: bool = SELF_CONTAINED
    """Whether the report inlines its assets, so that serving one file suffices."""

    @property
    def is_available(self) -> bool:
        """Whether the artifact can be served right now.

        True only for :attr:`~HtmlReportStatus.AVAILABLE` -- a non-empty regular
        file inside the artifact root. Every other outcome, including a zero-byte
        file, is unservable, and this is the single predicate a route should
        branch on.
        """
        return self.status is HtmlReportStatus.AVAILABLE

    @property
    def posix_path(self) -> str:
        """:attr:`path` rendered with forward slashes on every platform.

        Report consumers are written in terms of forward slashes, so this is the
        form that belongs in a JSON payload, a rendered page or a log line --
        never a platform-native rendering.
        """
        return paths.to_posix(self.path)

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-serialisable view of this descriptor.

        Every value is a primitive: the path is rendered POSIX-style, the status
        as its string value, and the modification time as an ISO-8601 string (or
        ``None``). That lets an API route return the mapping directly, with no
        custom encoder and no risk of leaking a platform-native path.

        A fresh dictionary is built on each call, so the returned mapping is the
        caller's to modify and can never corrupt this frozen snapshot.
        """
        return {
            "path": self.posix_path,
            "status": self.status.value,
            "detail": self.detail,
            "exists": self.exists,
            "available": self.is_available,
            "size_bytes": self.size_bytes,
            "modified_at": None if self.modified_at is None else self.modified_at.isoformat(),
            "modified_timestamp": self.modified_timestamp,
            "content_type": self.content_type,
            "self_contained": self.self_contained,
        }


# =============================================================================
# Behaviour.
#
# Every function below returns a value for every input it can be given. None of
# them raises, none of them writes to disk, none of them starts a process, and
# none of them reads the report's contents: a single `stat` call supplies
# everything a caller needs. That is what lets the report stage stay
# unconditional, exactly as the non-gating source pipeline was.
# =============================================================================


def _modified_at(timestamp: float) -> datetime | None:
    """Convert a POSIX modification time to a timezone-aware UTC datetime.

    Args:
        timestamp: The raw ``st_mtime`` value reported by the filesystem.

    Returns:
        The corresponding UTC datetime, or ``None`` if the value cannot be
        represented -- which a filesystem reporting a nonsensical timestamp can
        cause. Returning ``None`` keeps the "never raise" guarantee absolute
        while still surfacing every other field of the descriptor.
    """
    try:
        return datetime.fromtimestamp(timestamp, tz=UTC)
    except (OSError, OverflowError, ValueError) as error:
        _LOGGER.debug(
            "Could not represent modification time %r as a UTC datetime (%s: %s)",
            timestamp,
            type(error).__name__,
            error,
        )
        return None


def html_report_path(base_dir: paths.StrPath | None = None) -> Path:
    """Return where the Cucumber HTML report lives.

    The path is taken from ``app/utils/paths.py`` and never composed here, so the
    artifact root name and the report's file name are defined in exactly one
    place in the application.

    Args:
        base_dir: Optional directory the artifact root should sit inside. ``None``
            -- the default -- yields the repository-relative location the test
            configuration and the CI pipeline already use. A test passes a
            temporary directory to redirect the whole layout without touching the
            working tree. This is the only override accepted on purpose: an
            arbitrary artifact-path override would both re-spell a path this
            module must not compose and open a traversal vector, since a download
            route serves whatever path this module names.

    Returns:
        The report's path, always ending in the file name
        :data:`app.utils.paths.CUCUMBER_HTML_NAME`. The file itself may or may
        not exist -- use :func:`describe_html_report` to find out.
    """
    return paths.resolve_layout(base_dir).cucumber_html


def is_within_artifact_root(
    candidate: paths.StrPath,
    base_dir: paths.StrPath | None = None,
) -> bool:
    """Report whether *candidate* stays inside the artifact root.

    A containment guard, exposed because a download route serves whatever path it
    is handed: a path escaping the artifact root would turn a report endpoint into
    arbitrary file disclosure. Both sides are fully resolved before comparison, so
    ``..`` segments, an absolute path elsewhere on the filesystem and a symbolic
    link pointing out of the tree are all rejected -- a textual check on the
    unresolved string would miss the last of those entirely.

    Args:
        candidate: The path to test. It need not exist; a path that does not
            exist yet is resolved without complaint, which is what makes this
            usable before a run has produced anything.
        base_dir: Optional directory the artifact root sits inside, matching
            :func:`html_report_path`.

    Returns:
        ``True`` if *candidate* resolves to the artifact root itself or to
        something beneath it, ``False`` otherwise -- including when the path
        cannot be resolved at all, because an unverifiable path must never be
        treated as safe.
    """
    try:
        resolved_root = Path(paths.target_root(base_dir)).resolve()
        resolved_candidate = Path(candidate).resolve()
    except (OSError, RuntimeError, ValueError) as error:
        # ValueError covers a hostile string such as one carrying an embedded NUL;
        # OSError and RuntimeError cover symbolic-link cycles and filesystems that
        # refuse to resolve. All three mean "cannot be proven safe", so all three
        # are refused rather than guessed at.
        _LOGGER.warning(
            "Refusing a report path that could not be resolved (%s: %s)",
            type(error).__name__,
            error,
        )
        return False

    return resolved_candidate.is_relative_to(resolved_root)


def describe_html_report(base_dir: paths.StrPath | None = None) -> HtmlReportArtifact:
    """Inspect the Cucumber HTML report and describe what is there.

    This is the module's primary entry point and the one function the HTTP layer
    needs. It performs a single ``stat`` -- it never reads, rewrites or parses the
    report -- and it always returns a descriptor, whatever it finds.

    Every absence and every obstruction is reported rather than raised, because a
    missing report is the *normal* state of this system: a fresh checkout has run
    nothing, and the preserved default tag expression selects no scenarios at all.
    Concretely, nothing at the path yields :attr:`~HtmlReportStatus.MISSING`; a
    directory in the way yields :attr:`~HtmlReportStatus.NOT_A_FILE`; a zero-byte
    file yields :attr:`~HtmlReportStatus.EMPTY`, kept distinct from missing so the
    API layer can answer honestly; a refused or failing ``stat`` yields
    :attr:`~HtmlReportStatus.INACCESSIBLE` with the reason appended; and a path
    that resolves out of the artifact root yields
    :attr:`~HtmlReportStatus.OUTSIDE_ROOT` and is described no further.

    No placeholder report is ever created to avoid one of those outcomes.

    Args:
        base_dir: Optional directory the artifact root sits inside, matching
            :func:`html_report_path`.

    Returns:
        An immutable :class:`HtmlReportArtifact`. Check
        :attr:`~HtmlReportArtifact.is_available` before serving the file, and use
        :attr:`~HtmlReportArtifact.status` and
        :attr:`~HtmlReportArtifact.detail` to explain the outcome to a caller.
    """
    path = html_report_path(base_dir)

    # Containment first: if the path cannot be proven to stay inside the artifact
    # root -- which a symbolic link planted in the tree is enough to cause -- it
    # is not described any further, so no metadata about a file outside the root
    # can leak through the descriptor.
    if not is_within_artifact_root(path, base_dir):
        _LOGGER.warning(
            "Report path %s resolves outside the artifact root; refusing to describe it",
            paths.to_posix(path),
        )
        return HtmlReportArtifact(
            path=path,
            status=HtmlReportStatus.OUTSIDE_ROOT,
            detail=STATUS_DETAILS[HtmlReportStatus.OUTSIDE_ROOT],
        )

    try:
        # `stat` follows symbolic links, so a link to a regular file inside the
        # root is served like the file it names. Nothing is ever opened: size and
        # modification time are all this module needs, which keeps it independent
        # of the report's encoding and of its size.
        stat_result = path.stat()
    except FileNotFoundError:
        # The overwhelmingly common case, and not an error: no run has produced
        # the report yet. Logged at debug level precisely because it is routine.
        _LOGGER.debug("Cucumber HTML report not generated yet at %s", paths.to_posix(path))
        return HtmlReportArtifact(
            path=path,
            status=HtmlReportStatus.MISSING,
            detail=STATUS_DETAILS[HtmlReportStatus.MISSING],
        )
    except (OSError, ValueError) as error:
        # OSError covers the whole family that matters: a refused permission, a
        # non-directory in the middle of the path, a symbolic-link cycle, an
        # unreachable mount. ValueError covers a path the platform rejects
        # outright. None of them may escape to the caller.
        _LOGGER.warning(
            "Could not inspect the Cucumber HTML report at %s (%s: %s)",
            paths.to_posix(path),
            type(error).__name__,
            error,
        )
        return HtmlReportArtifact(
            path=path,
            status=HtmlReportStatus.INACCESSIBLE,
            detail=(
                f"{STATUS_DETAILS[HtmlReportStatus.INACCESSIBLE]} "
                f"({type(error).__name__}: {error})"
            ),
        )

    modified_timestamp = stat_result.st_mtime
    modified_at = _modified_at(modified_timestamp)

    if not S_ISREG(stat_result.st_mode):
        # A directory -- or a socket, or a device node -- sitting where the report
        # belongs. Its size would be meaningless, so it is not reported, but the
        # modification time still helps whoever has to clear the obstruction.
        _LOGGER.warning(
            "Cucumber HTML report path %s is not a regular file; it cannot be served",
            paths.to_posix(path),
        )
        return HtmlReportArtifact(
            path=path,
            status=HtmlReportStatus.NOT_A_FILE,
            detail=STATUS_DETAILS[HtmlReportStatus.NOT_A_FILE],
            exists=True,
            modified_timestamp=modified_timestamp,
            modified_at=modified_at,
        )

    if stat_result.st_size == 0:
        # Present but empty. Kept distinct from missing so that a caller can be
        # told a run did happen and still wrote nothing worth serving.
        _LOGGER.warning("Cucumber HTML report at %s is empty", paths.to_posix(path))
        return HtmlReportArtifact(
            path=path,
            status=HtmlReportStatus.EMPTY,
            detail=STATUS_DETAILS[HtmlReportStatus.EMPTY],
            exists=True,
            modified_timestamp=modified_timestamp,
            modified_at=modified_at,
        )

    _LOGGER.debug(
        "Cucumber HTML report available at %s (%d bytes)",
        paths.to_posix(path),
        stat_result.st_size,
    )
    return HtmlReportArtifact(
        path=path,
        status=HtmlReportStatus.AVAILABLE,
        detail=STATUS_DETAILS[HtmlReportStatus.AVAILABLE],
        exists=True,
        size_bytes=stat_result.st_size,
        modified_timestamp=modified_timestamp,
        modified_at=modified_at,
    )


def is_html_report_available(base_dir: paths.StrPath | None = None) -> bool:
    """Report whether the HTML report can be served right now.

    A convenience predicate for callers that need a yes-or-no answer -- a report
    index deciding whether to offer a download link, for instance -- and nothing
    more. Anything that needs to explain *why* the answer is no should call
    :func:`describe_html_report` and read its status and detail instead of
    inferring a reason from a bare ``False``.

    Args:
        base_dir: Optional directory the artifact root sits inside, matching
            :func:`html_report_path`.

    Returns:
        ``True`` only when a non-empty regular file exists inside the artifact
        root at the report's path.
    """
    return describe_html_report(base_dir).is_available
