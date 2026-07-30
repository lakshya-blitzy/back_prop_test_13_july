"""Adapter for the PrettyReports report *directory*.

This module is the Python port of exactly one source construct: the fourth
plugin declaration of the ``@CucumberOptions`` block on the documented
``CukesRunner`` in ``README.md``::

    "me.jvt.cucumber.report.PrettyReports:target/cucumber"

backed in the source build by the Maven dependency declared -- with no
``<scope>`` element, and therefore at compile scope -- in ``pom.xml``::

    <dependency>
        <groupId>me.jvt.cucumber</groupId>
        <artifactId>reporting-plugin</artifactId>
        <version>7.2.0</version>
    </dependency>

Both literals are carried verbatim above and cited by *value* rather than by
line number, because the line numbers recorded for the four plugin strings in
the migration plan are off by one -- the literal is the only safe citation. One
Cucumber plugin becomes one reporting adapter, so this module ports that single
declaration and nothing else.

This module is the SOLE producer of the PrettyReports directory
----------------------------------------------------------------
There is no test-runner option that produces this artifact, so if nothing here
runs, the directory never appears. The reason is a hard incompatibility rather
than an oversight: pytest-bdd's Gherkin terminal reporter cannot coexist with
the xdist worker plugin -- it raises an explicit exception during configuration
and aborts the whole run with an internal error. The default invocation *is*
parallel, because that is the faithful port of Surefire's ``<parallel>methods
</parallel>`` plus ``<useUnlimitedThreads>true</useUnlimitedThreads>``
[pom.xml:L22-L23], so the terminal reporter can never be part of the default
option set. Validation criterion **V8** grades exactly that: the default option
set must not contain it.

The resolution is to render the artifact by post-processing the Cucumber JSON
report instead, which is what every function below does. That is not a
compromise -- it is *more* faithful to the source than a terminal reporter would
have been, because the Java plugin likewise rendered a report directory from the
JSON rather than writing terminal output. A serial developer-only target offers
the terminal reporter separately; it is documented as mutually exclusive with
the default invocation and is deliberately out of this module's scope. Nothing
below sets, requires or validates that option.

Criterion **V8** also fixes the second half of this module's contract: a
parallel run emits a report containing *every* scenario a serial run did, larger
than but structurally identical to it, with no guaranteed ordering and possibly
with the same feature reported more than once. Reading the document through
``app/reporting/cucumber_json.py`` is what makes that safe here: that module
merges features sharing a ``uri`` by *concatenating* their elements and orders
everything by a stable total key, so no scenario can be dropped and the
rendering is identical whichever worker reported first.

No third-party dependency
-------------------------
The Java reporting plugin has no Python counterpart and needs none: this module
renders plain semantic HTML with the standard library alone. Nothing here
imports a web framework, a template engine, a test framework or a browser
driver, and nothing here executes another program.

What is produced
----------------
A directory -- the source declaration names a directory, not a file, hence the
extension-less target -- holding an index document, one document per feature,
and one hand-written stylesheet. The rendering is deterministic: the same input
document always yields byte-identical output, because every ordering is fixed by
a stable total key and no wall-clock reading, host name, working directory,
absolute path or run identifier is ever written into the content. Regeneration
is idempotent and removes the documents an earlier, larger run left behind,
while never touching a file this module did not write.

Preserved defects -- do not "fix" these
---------------------------------------
The port reproduces the source system's behaviour including its defects; the
authoritative register is ``docs/migration-parity.md``. Five are visible in what
this module renders, and the rendering is built so that none can be repaired by
accident:

* **D1** -- the first scenario outline has no ``Examples`` table, so its steps
  are recorded with the literal placeholder text ``<username>`` / ``<password>``
  and they pass. Step names are escaped for HTML and never interpolated, so
  those angle brackets survive as *visible text*.
* **D2** -- the runner's preserved default tag expression selects no scenario at
  all, so an empty report is the ordinary outcome. Rendering an empty but valid
  directory is a quiet success, clearly distinguished from having no report to
  post-process at all.
* **D3** -- the source build can never fail. Generating this directory therefore
  influences no exit code and no verdict, and succeeds even when the run it
  describes failed.
* **D4** -- the third outline feeds the *password* column into the *username*
  step, so the report records ``salesmanager`` / ``posmanager`` where an e-mail
  address would be expected. Whatever the report says is what is rendered.
* **D5** -- one assertion expects a French message. Step text is never trimmed,
  translated, case-folded or Unicode-normalized, so it survives byte-exactly,
  trailing period included.
* **D9** -- the second and third outlines parse to the *identical* scenario name,
  so several elements legitimately share one name. No file name and no anchor is
  ever derived from a scenario name, and no element is ever de-duplicated by
  name: doing either would silently discard four fifths of the parametrisation
  data.

Layering
--------
The dependency direction is ``api -> services -> reporting -> utils``, so this
module imports the standard library, its own layer's JSON adapter and
``app.utils`` only. It is framework-agnostic on purpose: no application object,
no request or response, no HTTP status code, no process execution and no logging
configuration. It returns domain results and lets the API and web layers map
them. The report-generation script also renders this artifact, by calling
:func:`generate_pretty_reports`; the dependency runs that way only.

Usage
-----
::

    >>> result = generate_pretty_reports()
    >>> result.successful or result.status.name in {"SOURCE_ABSENT"}
    True
    >>> files = render_pretty_reports(normalized_report)   # pure, no file I/O
    >>> sorted(files)[0]
    'feature-01.html'
"""

import html
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Final

from app.reporting.cucumber_json import (
    STATUS_SKIPPED,
    NormalizedElement,
    NormalizedFeature,
    NormalizedReport,
    NormalizedStep,
    RunSummary,
    load_report,
    normalize_document,
    report_path,
)
from app.utils import paths

__all__ = [
    "CSS_CONTENT_TYPE",
    "CSS_MEDIA_TYPE",
    "DIRECTORY_STATUS_DETAILS",
    "FALLBACK_CONTENT_TYPE",
    "FEATURE_PAGE_PREFIX",
    "FEATURE_PAGE_SUFFIX",
    "GENERATED_NAME_EXTENSIONS",
    "HTML_CHARSET",
    "HTML_CONTENT_TYPE",
    "HTML_MEDIA_TYPE",
    "INDEX_NAME",
    "NOT_GENERATED_DESCRIPTION",
    "PLUGIN_DECLARATION",
    "PLUGIN_KEYWORD",
    "PRODUCER_DISTRIBUTION",
    "STATUS_DETAILS",
    "STYLESHEET_NAME",
    "SUCCESSFUL_STATUSES",
    "PrettyReportsDirectory",
    "PrettyReportsDirectoryStatus",
    "PrettyReportsResult",
    "PrettyReportsStatus",
    "content_type_for",
    "describe_pretty_reports",
    "feature_page_name",
    "generate_pretty_reports",
    "is_generated_name",
    "is_pretty_reports_generated",
    "is_within_pretty_reports_dir",
    "pretty_reports_dir",
    "render_document",
    "render_pretty_reports",
    "resolve_page",
]

# A module logger, and nothing more. Handlers, levels and formatters belong
# exclusively to `app/logging_config.py`, which sits above this layer and must
# not be imported here. Every message emitted below is therefore a structured,
# lazily-formatted record on this logger, and never console output.
_LOGGER: Final[logging.Logger] = logging.getLogger(__name__)


# =============================================================================
# Provenance of the ported plugin.
#
# Configuration values are data, not decisions: nothing below is renamed,
# reordered or "modernised" relative to the source declaration.
#
# The declaration string is ASSEMBLED from the keyword and the output location
# owned by `app/utils/paths.py`, rather than being written out as a path
# literal. That is deliberate twice over: the artifact-root name is defined in
# exactly one place in the whole application, and the provenance string this
# module renders can therefore never drift from the directory it actually
# writes. The verbatim source literal appears in the module docstring and in the
# comment above the keyword, which is where a reader looking for the citation
# will find it.
# =============================================================================

# From the plugin array of the documented runner's @CucumberOptions block, whose
# fourth entry reads, in full and byte-exactly:
#   "me.jvt.cucumber.report.PrettyReports:target/cucumber"
# The part before the colon is the reporter's fully qualified Java class name;
# the part after it is the output directory, which is owned by app/utils/paths.py.
PLUGIN_KEYWORD: Final[str] = "me.jvt.cucumber.report.PrettyReports"
"""The reporter's fully qualified name, exactly as the source declared it."""

PLUGIN_DECLARATION: Final[str] = f"{PLUGIN_KEYWORD}:{paths.to_posix(paths.PRETTY_REPORTS_DIR)}"
"""The complete source plugin declaration, reassembled from its two halves.

Equal, character for character, to the fourth plugin string of the documented
runner. It is rendered into the index document as provenance, so that anyone
reading the artifact can see which source construct produced it.
"""

# [pom.xml:L66-L70] the Maven dependency that supplied the reporter. Declared
# with no <scope> element, so it was a compile-scope dependency:
#   <groupId>me.jvt.cucumber</groupId>
#   <artifactId>reporting-plugin</artifactId>
#   <version>7.2.0</version>
PRODUCER_DISTRIBUTION: Final[str] = "me.jvt.cucumber:reporting-plugin:7.2.0"
"""The source distribution this module replaces, in Maven coordinate form.

It has no Python counterpart and needs none: the artifact is rendered from the
JSON report with the standard library alone, which is why no third-party
distribution is introduced anywhere in this layer.
"""


# =============================================================================
# Names of the files inside the directory.
#
# Exactly three name shapes are ever written, which is what makes ownership
# decidable: `is_generated_name` recognises them, and regeneration removes only
# names it recognises. A file this module did not write is never touched.
#
# No name is derived from a feature or scenario name. Five scenarios in the
# ported feature legitimately share one name (defect D9); keying a file name on
# a scenario name would let four of the five overwrite the fifth and silently
# destroy the parametrisation data the parity suite exists to protect. Feature
# documents are keyed on the feature's 1-based position in the deterministically
# ordered report instead, and elements are anchored on their position within
# that feature, with the `<uri>:<line>` locator rendered as visible text.
# =============================================================================

INDEX_NAME: Final[str] = "index.html"
"""File name of the landing document, and the marker that the directory exists."""

STYLESHEET_NAME: Final[str] = "pretty-reports.css"
"""File name of the single hand-written stylesheet.

One stylesheet, written into the directory next to the documents that link it.
No framework, no component library, no design-token system and no reference to
any external origin: the directory is self-contained and renders offline.
"""

FEATURE_PAGE_PREFIX: Final[str] = "feature-"
"""Prefix of a per-feature document name."""

FEATURE_PAGE_SUFFIX: Final[str] = ".html"
"""Suffix of a per-feature document name."""

_FEATURE_PAGE_DIGITS: Final[int] = 2
"""Minimum zero-padded width of the ordinal in a per-feature document name.

Two digits keeps the ordinary single-feature report reading as ``feature-01``
and keeps lexical and numeric order the same for the first ninety-nine
features; beyond that the ordinal simply grows, and the index document is
authoritative about order in every case.
"""

GENERATED_NAME_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {FEATURE_PAGE_SUFFIX, Path(STYLESHEET_NAME).suffix}
)
"""The only file extensions this module ever writes, lower-cased.

Derived from the two names above rather than restated, so the set can never
disagree with what is actually produced. Published so a route serving the
directory can reject anything else outright.
"""


# =============================================================================
# Content types.
#
# A route serving a file out of this directory needs the header value to send
# with the bytes, and nothing more; constructing the response itself belongs to
# the API and web layers. The documents are written as UTF-8 and declare that
# charset internally, so stating it here keeps the header and the document in
# agreement instead of leaving the encoding to a browser's guess.
# =============================================================================

HTML_MEDIA_TYPE: Final[str] = "text/html"
"""Media type of the index and per-feature documents, without parameters."""

CSS_MEDIA_TYPE: Final[str] = "text/css"
"""Media type of the stylesheet, without parameters."""

HTML_CHARSET: Final[str] = "utf-8"
"""The character encoding every file in the directory is written in."""

HTML_CONTENT_TYPE: Final[str] = f"{HTML_MEDIA_TYPE}; charset={HTML_CHARSET}"
"""``text/html; charset=utf-8`` -- the exact header value for a document."""

CSS_CONTENT_TYPE: Final[str] = f"{CSS_MEDIA_TYPE}; charset={HTML_CHARSET}"
"""``text/css; charset=utf-8`` -- the exact header value for the stylesheet."""

_CONTENT_TYPES: Final[Mapping[str, str]] = MappingProxyType(
    {
        FEATURE_PAGE_SUFFIX: HTML_CONTENT_TYPE,
        Path(STYLESHEET_NAME).suffix: CSS_CONTENT_TYPE,
    }
)
"""Content type per produced file extension, keyed on the lower-cased suffix."""

FALLBACK_CONTENT_TYPE: Final[str] = "application/octet-stream"
"""Content type for a name this module would never have written.

Serving an unrecognised name as opaque bytes rather than as markup means a file
that somehow arrived in the directory can never be interpreted as a document by
a browser.
"""

NOT_GENERATED_DESCRIPTION: Final[str] = "not generated yet"
"""The phrase used for an artifact that no run has produced.

An absent directory is the ordinary state of a fresh checkout -- the artifact
root is wiped before every run and is not tracked -- so this is a plain
statement of fact and never an error.
"""

_NANOSECONDS_PER_SECOND: Final[int] = 1_000_000_000
"""Report durations are recorded in nanoseconds, the report's own unit."""

_NANOSECONDS_PER_MILLISECOND: Final[int] = 1_000_000
"""Divisor for the millisecond fraction of a rendered duration."""


# =============================================================================
# Outcome of a generation attempt.
#
# Six outcomes, kept strictly apart, because conflating any two of them would
# hide a real problem or invent one. Two are successes; the other four each name
# a distinct, actionable cause.
#
# The distinction the migration plan insists on most is between the first two
# failures below: "there is no report to post-process" is NOT the same fact as
# "the report exists and recorded zero scenarios". The second is the ordinary
# outcome of the documented invocation (defect D2) and is a success; the first
# means no run has happened yet and nothing is written, because writing an empty
# directory then would imply a clean run that never took place.
# =============================================================================


class PrettyReportsStatus(StrEnum):
    """Why a generation attempt ended the way it did.

    A :class:`str` enumeration, so a value can be logged, compared with a plain
    string and serialised by the API layer without conversion.

    Never gate on any of these values. The source build could not fail --
    ``<testFailureIgnore>true</testFailureIgnore>`` [pom.xml:L25] together with
    six report thresholds of ``-1`` [Jenkins:L15] -- and the port preserves that
    (defect D3), so the report stage runs unconditionally after the test stage
    and its outcome influences no exit code and no verdict.
    """

    GENERATED = "generated"
    """The directory was written and describes at least one scenario."""

    EMPTY = "empty"
    """The report exists, is well-formed, and recorded no scenario at all.

    A success, and the *expected* outcome of the documented invocation: the
    preserved default tag expression matches no scenario in the feature, so the
    ordinary run produces an empty report (defect D2). The directory is written
    and says so plainly.
    """

    SOURCE_ABSENT = "source-absent"
    """There is no report to post-process: no run has produced one yet.

    Nothing is written. This is not an error -- a fresh checkout has no artifact
    tree at all -- but it is emphatically not :attr:`EMPTY` either.
    """

    SOURCE_INVALID = "source-invalid"
    """The report exists but yielded nothing that could be rendered.

    Covers an empty file, a file that is not JSON, and a document that parses
    but breaks the frozen report schema without carrying a single usable
    scenario. The reason always says which. Nothing is written, because an
    unusable input must not be presented as a rendered report.

    A document that breaks the schema yet still carries scenarios is rendered
    anyway, as :attr:`GENERATED` with the schema finding recorded: the source
    system would have published those scenarios, and withholding them would
    change observable behaviour.
    """

    WRITE_FAILED = "write-failed"
    """The rendering succeeded but the filesystem refused to store it."""

    REFUSED = "refused"
    """The requested output location lies outside the artifact root.

    Nothing is read, written or removed. This is the destructive-path guard:
    regeneration deletes the documents an earlier run left behind, so the
    location it operates on is proven to sit inside the artifact root before any
    filesystem change is attempted.
    """


SUCCESSFUL_STATUSES: Final[frozenset[PrettyReportsStatus]] = frozenset(
    {PrettyReportsStatus.GENERATED, PrettyReportsStatus.EMPTY}
)
"""The two outcomes that mean the directory was written.

Published so a caller can test membership instead of restating the pair, and so
that the zero-scenario outcome can never be mistaken for a failure.
"""

STATUS_DETAILS: Final[Mapping[PrettyReportsStatus, str]] = MappingProxyType(
    {
        PrettyReportsStatus.GENERATED: "report directory generated",
        PrettyReportsStatus.EMPTY: (
            "report directory generated; the report recorded no scenario, which is the "
            "expected outcome of the preserved default tag expression"
        ),
        PrettyReportsStatus.SOURCE_ABSENT: f"Cucumber JSON report {NOT_GENERATED_DESCRIPTION}",
        PrettyReportsStatus.SOURCE_INVALID: "Cucumber JSON report could not be post-processed",
        PrettyReportsStatus.WRITE_FAILED: "report directory could not be written",
        PrettyReportsStatus.REFUSED: "refused an output location outside the artifact root",
    }
)
"""A human-readable sentence per outcome, ready to log or surface.

Read-only, so a consumer can never mutate the shared mapping.
"""


# =============================================================================
# State of the directory on disk.
#
# A separate vocabulary from the one above on purpose: "what happened when we
# tried to generate" and "what is sitting in the directory right now" are
# different questions, and an index page needs the second one answered without
# triggering the first.
# =============================================================================


class PrettyReportsDirectoryStatus(StrEnum):
    """What an inspection of the output directory found."""

    ABSENT = "absent"
    """The directory does not exist: no run has produced the artifact yet."""

    EMPTY = "empty"
    """The directory exists but holds no index document.

    The ordinary state after the artifact tree has been created but before this
    module has rendered anything into it.
    """

    GENERATED = "generated"
    """The directory exists and holds an index document."""

    UNREADABLE = "unreadable"
    """The directory exists but could not be listed.

    Reported rather than silently folded into :attr:`ABSENT`, because a
    permission failure and an absent artifact call for different responses.
    """


DIRECTORY_STATUS_DETAILS: Final[Mapping[PrettyReportsDirectoryStatus, str]] = MappingProxyType(
    {
        PrettyReportsDirectoryStatus.ABSENT: f"report directory {NOT_GENERATED_DESCRIPTION}",
        PrettyReportsDirectoryStatus.EMPTY: "report directory exists but holds no index document",
        PrettyReportsDirectoryStatus.GENERATED: "report directory generated",
        PrettyReportsDirectoryStatus.UNREADABLE: "report directory could not be listed",
    }
)
"""A human-readable sentence per directory state, ready to log or surface."""


_EMPTY_SUMMARY: Final[RunSummary] = RunSummary(status=STATUS_SKIPPED)
"""The summary reported when nothing could be parsed.

``skipped`` is the report layer's aggregate for "no step ran", so it is what an
absent or unusable report honestly aggregates to. Inventing a failure here would
break the non-gating behaviour the port preserves (defect D3).
"""


@dataclass(frozen=True, slots=True)
class PrettyReportsResult:
    """The outcome of one attempt to generate the report directory.

    Immutable, and always fully populated: whichever way the attempt ended, a
    caller has a status to branch on, a location to name and -- when something
    went wrong -- a reason to surface.

    Attributes:
        status: One of :class:`PrettyReportsStatus`.
        output_dir: The directory that was written, or would have been.
        source_path: The Cucumber JSON report that was read, or would have been.
        files: The names written, sorted. Empty unless the directory was
            written. A partial write reports the names that did land, so a
            caller is never told nothing happened when something did.
        removed: The names of documents an earlier, larger run had left behind
            and that this run deleted, sorted. Always a subset of the names this
            module itself writes.
        reason: Why the attempt did not produce a directory, ready to log or
            surface. ``None`` on success.
        source_reason: The report loader's own finding, when it had one. Carried
            even on success, because a document that breaks the schema yet still
            holds scenarios is rendered rather than withheld, and the finding
            must not be lost when that happens.
        summary: The report's own summary -- counts, statuses and tags. Empty
            when nothing could be parsed.
    """

    status: PrettyReportsStatus
    output_dir: Path
    source_path: Path
    files: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    reason: str | None = None
    source_reason: str | None = None
    summary: RunSummary = _EMPTY_SUMMARY

    @property
    def successful(self) -> bool:
        """``True`` when the directory was written.

        Both :attr:`PrettyReportsStatus.GENERATED` and
        :attr:`PrettyReportsStatus.EMPTY` qualify: a report with no scenario is
        the ordinary outcome of the documented invocation, not a failure.
        """
        return self.status in SUCCESSFUL_STATUSES

    @property
    def is_empty(self) -> bool:
        """``True`` when a directory was written for a report with no scenario."""
        return self.status is PrettyReportsStatus.EMPTY

    @property
    def description(self) -> str:
        """A single sentence describing the outcome, ready to log or surface."""
        detail = STATUS_DETAILS[self.status]
        location = paths.to_posix(self.output_dir)
        if self.reason is None:
            return f"{detail} at {location}"
        return f"{detail} at {location}: {self.reason}"

    def as_dict(self) -> dict[str, object]:
        """Return the result as a plain, deterministically ordered dictionary.

        A fresh dictionary is built on every call and every nested value is
        copied out, so the result is the caller's to mutate and can never
        corrupt this instance. Mapping it onto an HTTP response belongs to the
        API layer.
        """
        return {
            "status": str(self.status),
            "successful": self.successful,
            "output_dir": paths.to_posix(self.output_dir),
            "source_path": paths.to_posix(self.source_path),
            "files": list(self.files),
            "removed": list(self.removed),
            "reason": self.reason,
            "source_reason": self.source_reason,
            "description": self.description,
            "summary": self.summary.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class PrettyReportsDirectory:
    """What an inspection of the output directory found, without generating it.

    Attributes:
        status: One of :class:`PrettyReportsDirectoryStatus`.
        path: The directory that was inspected.
        files: The regular-file names found, sorted. Empty when the directory is
            absent or could not be listed.
        reason: Why the directory is absent or unreadable. ``None`` otherwise.
    """

    status: PrettyReportsDirectoryStatus
    path: Path
    files: tuple[str, ...] = ()
    reason: str | None = None

    @property
    def is_generated(self) -> bool:
        """``True`` when the directory holds an index document."""
        return self.status is PrettyReportsDirectoryStatus.GENERATED

    @property
    def index_name(self) -> str | None:
        """The index document's name when it is present, otherwise ``None``."""
        return INDEX_NAME if self.is_generated else None

    @property
    def description(self) -> str:
        """A single sentence describing the state, ready to log or surface."""
        detail = DIRECTORY_STATUS_DETAILS[self.status]
        location = paths.to_posix(self.path)
        if self.reason is None:
            return f"{detail} at {location}"
        return f"{detail} at {location}: {self.reason}"

    def as_dict(self) -> dict[str, object]:
        """Return the inspection as a plain, deterministically ordered dictionary."""
        return {
            "status": str(self.status),
            "generated": self.is_generated,
            "path": paths.to_posix(self.path),
            "files": list(self.files),
            "index_name": self.index_name,
            "reason": self.reason,
            "description": self.description,
        }


# =============================================================================
# The stylesheet.
#
# One hand-written stylesheet, plain CSS, no framework and no reference to any
# external origin, so the directory renders identically offline. It is a
# constant rather than a file read at run time, which keeps the rendering pure
# and keeps the artifact reproducible from the JSON report alone.
#
# Every document below is written as semantic HTML that reads correctly with no
# styling at all; this only makes it pleasanter.
# =============================================================================

_STYLESHEET: Final[str] = """\
/* PrettyReports -- generated stylesheet. Hand-written, framework-free. */
:root {
  --ink: #1b1b1b;
  --muted: #5d5d5d;
  --rule: #d8d8d8;
  --surface: #ffffff;
  --shade: #f5f5f5;
  --passed: #1a6b32;
  --failed: #a41414;
  --neutral: #59595f;
}
* {
  box-sizing: border-box;
}
body {
  margin: 0 auto;
  padding: 1.5rem;
  max-width: 60rem;
  font-family: system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  font-size: 16px;
  line-height: 1.5;
  color: var(--ink);
  background: var(--surface);
}
h1 {
  margin: 0 0 0.25rem;
  font-size: 1.6rem;
}
h2 {
  margin: 2rem 0 0.5rem;
  font-size: 1.2rem;
  border-bottom: 1px solid var(--rule);
  padding-bottom: 0.25rem;
}
h3 {
  margin: 1.5rem 0 0.25rem;
  font-size: 1rem;
}
a {
  color: #10457f;
}
code,
pre,
.location,
.duration {
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace;
  font-size: 0.875em;
}
code,
.location {
  /* Plugin declarations and Maven coordinates are long unbreakable tokens; allow
     them to wrap rather than push the page wider than the viewport. This changes
     nothing at desktop widths, where the tokens already fit. */
  overflow-wrap: anywhere;
}
.provenance,
.note,
.meta,
.location {
  color: var(--muted);
}
.provenance,
.note {
  margin: 0.25rem 0 1rem;
}
.summary {
  display: grid;
  grid-template-columns: max-content auto;
  gap: 0.125rem 1rem;
  margin: 0.5rem 0 1.5rem;
}
.summary dt {
  font-weight: 600;
}
.summary dd {
  margin: 0;
}
table {
  border-collapse: collapse;
  width: 100%;
  margin: 0.5rem 0 1.5rem;
}
th,
td {
  border: 1px solid var(--rule);
  padding: 0.375rem 0.5rem;
  text-align: left;
  vertical-align: top;
}
th {
  background: var(--shade);
}
td.numeric,
th.numeric {
  text-align: right;
}
ul.tags {
  list-style: none;
  display: flex;
  flex-wrap: wrap;
  gap: 0.375rem;
  margin: 0.25rem 0;
  padding: 0;
}
li.tag {
  border: 1px solid var(--rule);
  border-radius: 0.75rem;
  padding: 0 0.5rem;
  background: var(--shade);
  font-size: 0.8125rem;
}
ol.steps {
  margin: 0.25rem 0 0;
  padding-left: 1.5rem;
}
ol.steps > li {
  margin: 0.125rem 0;
}
.keyword {
  font-weight: 600;
}
.step-name {
  white-space: pre-wrap;
}
.status {
  display: inline-block;
  margin-left: 0.375rem;
  padding: 0 0.375rem;
  border-radius: 0.25rem;
  background: var(--shade);
  color: var(--neutral);
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}
.status-passed {
  color: var(--passed);
}
.status-failed {
  color: var(--failed);
}
pre.error {
  margin: 0.25rem 0 0.5rem;
  padding: 0.5rem;
  border-left: 3px solid var(--failed);
  background: var(--shade);
  overflow-x: auto;
  white-space: pre-wrap;
}
section.element {
  margin-bottom: 1rem;
}
footer {
  margin-top: 2rem;
  padding-top: 0.5rem;
  border-top: 1px solid var(--rule);
  color: var(--muted);
  font-size: 0.875rem;
}
@media (max-width: 40rem) {
  /* The feature table has a wider intrinsic minimum than a narrow viewport can
     show. Scroll the table itself instead of the whole document, honouring the
     ``width=device-width`` viewport declaration emitted in every document head.
     Desktop rendering is untouched because the query does not match there. */
  table {
    display: block;
    overflow-x: auto;
  }
}
"""


# =============================================================================
# Rendering helpers -- pure, total and deterministic.
#
# Nothing below reads the clock, the environment, the host or the filesystem, so
# the same report always renders to the same bytes. That is what makes the
# artifact diffable and assertable; a rendering that varied between runs could
# not be either.
# =============================================================================


def _text(value: str) -> str:
    """Escape *value* for inclusion in HTML, as text or as an attribute value.

    Every single piece of report-derived content passes through here, and this is
    load-bearing rather than hygiene. The ported feature's first scenario outline
    has no ``Examples`` table, so its steps are recorded with the literal
    placeholder text ``<username>`` and ``<password>`` (defect D1): those angle
    brackets have to survive as *visible text*. Escaping them is what makes that
    happen -- and it is also what stops report content, which this application
    does not author, from being interpreted as markup.

    Quotes are escaped as well, so the same function is safe for an attribute
    value.

    Args:
        value: The text to escape.

    Returns:
        The escaped text. Nothing is trimmed, translated, case-folded or
        Unicode-normalized, so a French assertion message survives byte-exactly,
        trailing period included (defect D5).
    """
    return html.escape(value)


def _status_slug(status: str) -> str:
    """Return *status* reduced to a safe CSS class suffix.

    The report is the source of truth about what happened, so an unrecognised
    status is carried through and styled neutrally rather than remapped. Only the
    characters that would break a class name are replaced.

    Args:
        status: A status value from the report.

    Returns:
        A lower-cased slug of ASCII letters, digits and hyphens. ``unknown``
        when *status* reduces to nothing at all.
    """
    slug = "".join(
        character if character.isascii() and (character.isalnum() or character == "-") else "-"
        for character in status.lower()
    )
    return slug.strip("-") or "unknown"


def _status_markup(status: str) -> str:
    """Return the escaped status badge for *status*."""
    return f'<span class="status status-{_status_slug(status)}">{_text(status)}</span>'


def _format_duration(nanoseconds: int) -> str:
    """Render *nanoseconds* as whole seconds and milliseconds.

    Integer arithmetic throughout, so the result never depends on a floating
    point representation or on a locale's decimal separator. A negative value --
    which the report should never carry -- is treated as zero rather than
    rendered as a negative duration.

    Args:
        nanoseconds: A duration in the report's own unit.

    Returns:
        A string such as ``0.012s``.
    """
    total = max(nanoseconds, 0)
    seconds, remainder = divmod(total, _NANOSECONDS_PER_SECOND)
    milliseconds = remainder // _NANOSECONDS_PER_MILLISECOND
    return f"{seconds}.{milliseconds:03d}s"


def _render_tags(tags: tuple[str, ...]) -> list[str]:
    """Render *tags* as a list, or nothing at all when there are none.

    Tags arrive with the leading ``@`` already stripped, matching the source
    toolchain's own output, and nothing here puts one back. Hyphens are part of
    the Jira issue keys and are rendered exactly as they arrive -- never
    rewritten to underscores.

    Args:
        tags: The element's or feature's tags, already sorted and de-duplicated.

    Returns:
        The markup lines, or an empty list.
    """
    if not tags:
        return []
    items = "".join(f'<li class="tag">{_text(tag)}</li>' for tag in tags)
    return [f'<ul class="tags">{items}</ul>']


def _render_definitions(rows: tuple[tuple[str, str], ...]) -> list[str]:
    """Render *rows* as a definition list.

    Args:
        rows: Label and value pairs, in the order they should appear. Both halves
            are escaped here, so callers pass raw text.

    Returns:
        The markup lines.
    """
    lines = ['<dl class="summary">']
    for label, value in rows:
        lines.append(f"<dt>{_text(label)}</dt><dd>{_text(value)}</dd>")
    lines.append("</dl>")
    return lines


def _render_steps(steps: tuple[NormalizedStep, ...]) -> list[str]:
    """Render *steps* in their recorded order.

    Step order is semantic and is never sorted. Step names are rendered exactly
    as the report recorded them, which is what preserves the literal placeholders
    of defect D1, the password value in the username step of defect D4 and the
    French assertion message of defect D5.

    Args:
        steps: The element's steps.

    Returns:
        The markup lines.
    """
    if not steps:
        return ['<p class="note">No step was recorded for this element.</p>']
    lines = ['<ol class="steps">']
    for step in steps:
        parts = [
            "<li>",
            f'<span class="keyword">{_text(step.keyword)}</span>',
            f'<span class="step-name">{_text(step.name)}</span>',
            _status_markup(step.status),
            f'<span class="duration">{_text(_format_duration(step.duration))}</span>',
        ]
        if step.error_message is not None:
            # `None` and `""` are genuinely different in the report: the writer
            # attaches an empty message to the failing steps after the first, so
            # the block is rendered whenever a message key was present at all.
            parts.append(f'<pre class="error">{_text(step.error_message)}</pre>')
        parts.append("</li>")
        lines.append("".join(parts))
    lines.append("</ol>")
    return lines


def _render_element(element: NormalizedElement, anchor: str) -> list[str]:
    """Render one element -- a scenario or a background -- and its steps.

    The *anchor* is supplied by the caller and is derived from the element's
    position, never from its name. Five elements of the ported feature share one
    name (defect D9), so a name-derived anchor would collide four times over and
    the duplicates would become unreachable. The element's ``<uri>:<line>``
    locator is rendered as visible text instead, which is what actually
    identifies it.

    Args:
        element: The element to render.
        anchor: The element's document-unique HTML identifier.

    Returns:
        The markup lines.
    """
    # The Gherkin keyword and the element name are joined with a colon, the way
    # Gherkin itself writes them. The name is reproduced exactly as recorded --
    # never re-derived from the keyword line, so nothing about the source text is
    # inferred or corrected here.
    heading = _text(element.name)
    if element.keyword:
        heading = f"{_text(element.keyword)}: {heading}"
    lines = [
        f'<section class="element" id="{_text(anchor)}">',
        f"<h3>{heading}{_status_markup(element.status)}</h3>",
        f'<p class="meta"><span class="location">{_text(element.location)}</span></p>',
    ]
    lines.extend(_render_tags(element.tags))
    lines.extend(_render_steps(element.steps))
    lines.append("</section>")
    return lines


def _document(title: str, body: list[str]) -> str:
    """Wrap *body* in the shared document shell.

    Args:
        title: The document title, escaped here.
        body: The already-rendered body lines.

    Returns:
        A complete HTML document, newline-separated and newline-terminated, so
        the file is line-oriented and diffable.
    """
    lines = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        # An empty ``data:`` icon suppresses the browser's implicit ``/favicon.ico``
        # probe, which would otherwise log a 404 for an artifact that deliberately
        # ships no icon. A ``data:`` URI resolves in-document, so this adds no
        # network request, no external origin, and no non-deterministic content --
        # the artifact stays byte-stable and dependency-free.
        '<link rel="icon" href="data:,">',
        f"<title>{_text(title)}</title>",
        f'<link rel="stylesheet" href="{_text(STYLESHEET_NAME)}">',
        "</head>",
        "<body>",
        *body,
        "</body>",
        "</html>",
    ]
    return "\n".join(lines) + "\n"


def _render_footer() -> list[str]:
    """Render the shared provenance footer.

    Names the ported source construct and the source distribution it replaces, so
    the artifact carries its own citation. Both values are fixed constants, so
    the footer contributes nothing that could vary between runs.
    """
    return [
        "<footer>",
        f"<p>Ported source declaration: <code>{_text(PLUGIN_DECLARATION)}</code>, "
        f"originally produced by <code>{_text(PRODUCER_DISTRIBUTION)}</code>. "
        "This directory is rendered by post-processing the Cucumber JSON report.</p>",
        "</footer>",
    ]


# =============================================================================
# File naming.
# =============================================================================


def feature_page_name(ordinal: int) -> str:
    """Return the document name for the feature at 1-based position *ordinal*.

    The ordinal is the feature's position in the deterministically ordered
    report, never anything derived from its name: a name-derived file name would
    let same-named entries overwrite one another, which is exactly the silent
    data loss defect D9 makes possible.

    Args:
        ordinal: The feature's 1-based position. Must be positive.

    Returns:
        A name such as ``feature-01.html``.

    Raises:
        ValueError: If *ordinal* is not positive. This is a caller error rather
            than a report-content problem -- the report itself can never reach
            this function with a bad value -- so it is surfaced instead of being
            silently clamped.
    """
    if ordinal < 1:
        message = f"feature ordinal must be positive, got {ordinal}"
        raise ValueError(message)
    return f"{FEATURE_PAGE_PREFIX}{ordinal:0{_FEATURE_PAGE_DIGITS}d}{FEATURE_PAGE_SUFFIX}"


def is_generated_name(name: str) -> bool:
    """Report whether *name* is one this module itself writes.

    Exactly three name shapes qualify: the index document, the stylesheet, and a
    per-feature document whose ordinal is ASCII digits. This is what makes
    regeneration safe: a stale document from an earlier, larger run is recognised
    and removed, while a file this module never wrote is left strictly alone.

    Args:
        name: A bare file name, with no directory component.

    Returns:
        ``True`` only for a name this module produces.
    """
    if name in {INDEX_NAME, STYLESHEET_NAME}:
        return True
    if not (name.startswith(FEATURE_PAGE_PREFIX) and name.endswith(FEATURE_PAGE_SUFFIX)):
        return False
    ordinal = name[len(FEATURE_PAGE_PREFIX) : -len(FEATURE_PAGE_SUFFIX)]
    # `str.isdigit` alone would also accept non-ASCII digit characters, which this
    # module never emits, so ASCII is required explicitly.
    return bool(ordinal) and ordinal.isascii() and ordinal.isdigit()


# =============================================================================
# The pure renderer.
#
# `render_pretty_reports` is deliberately separated from the function that
# writes: it maps a parsed report onto file names and file contents and touches
# nothing else, so the rendering can be asserted in full without any filesystem
# effect at all.
# =============================================================================


def _render_feature_page(feature: NormalizedFeature, ordinal: int, total: int) -> str:
    """Render the document for one feature.

    Args:
        feature: The feature to render.
        ordinal: Its 1-based position in the ordered report.
        total: How many features the report holds, for the navigation line.

    Returns:
        A complete HTML document.
    """
    scenarios = feature.scenarios
    backgrounds = feature.backgrounds
    title = feature.name or feature.uri or f"Feature {ordinal}"
    body = [
        "<header>",
        f'<p class="meta"><a href="{_text(INDEX_NAME)}">Report index</a> '
        f"&middot; feature {ordinal} of {total}</p>",
        f"<h1>{_text(feature.keyword or 'Feature')}: {_text(title)}"
        f"{_status_markup(feature.status)}</h1>",
        f'<p class="location">{_text(feature.uri)}:{feature.line}</p>',
    ]
    body.extend(_render_tags(feature.tags))
    if feature.description:
        body.append(f'<pre class="description">{_text(feature.description)}</pre>')
    body.extend(
        _render_definitions(
            (
                # Backgrounds are counted separately and are deliberately NOT
                # part of the scenario count: an `elements` entry may be a
                # background rather than a scenario, and counting the array
                # itself would overstate the scenario total.
                ("Scenarios", str(len(scenarios))),
                ("Backgrounds", str(len(backgrounds))),
                ("Steps", str(len(feature.steps))),
                ("Failed scenarios", str(sum(1 for entry in scenarios if entry.failed))),
                ("Language", feature.language or "unspecified"),
            )
        )
    )
    body.append("</header>")

    if backgrounds:
        body.append("<h2>Background</h2>")
        for index, background in enumerate(backgrounds):
            body.extend(_render_element(background, f"background-{ordinal}-{index}"))

    body.append("<h2>Scenarios</h2>")
    if not scenarios:
        # A feature with no scenario is legitimate: the preserved default tag
        # expression selects nothing (defect D2), and a feature may also carry a
        # background only.
        body.append('<p class="note">No scenario was recorded for this feature.</p>')
    else:
        for index, scenario in enumerate(scenarios):
            # The anchor is keyed on position, never on the scenario name: five
            # scenarios of the ported feature share one name (defect D9).
            body.extend(_render_element(scenario, f"scenario-{ordinal}-{index}"))

    body.extend(_render_footer())
    return _document(f"{title} -- PrettyReports", body)


def _render_index(report: NormalizedReport, page_names: tuple[str, ...]) -> str:
    """Render the landing document.

    Args:
        report: The parsed report.
        page_names: The per-feature document names, positionally aligned with
            ``report.features``.

    Returns:
        A complete HTML document.
    """
    summary = report.summary
    body = [
        "<header>",
        "<h1>Cucumber PrettyReports</h1>",
        '<p class="provenance">Rendered from the Cucumber JSON report, feature by feature.</p>',
        "</header>",
        "<h2>Summary</h2>",
    ]
    body.extend(
        _render_definitions(
            (
                ("Status", summary.status),
                ("Features", str(summary.feature_count)),
                ("Scenarios", str(summary.scenario_count)),
                ("Backgrounds", str(summary.background_count)),
                ("Steps", str(summary.step_count)),
                ("Failed scenarios", str(summary.failed_scenario_count)),
                ("Duration", _format_duration(summary.duration_ns)),
            )
        )
    )

    if summary.is_empty:
        # Defect D2, stated plainly rather than dressed up as an error: the
        # runner's preserved default tag expression matches no scenario in the
        # feature, so the documented invocation legitimately records none. This
        # is a successful, complete report of an empty run.
        body.append(
            '<p class="note">The report recorded no scenario. This is the expected outcome '
            "of the preserved default tag expression, which matches no scenario in the "
            "feature, and it is a successful run rather than an error.</p>"
        )

    body.append("<h2>Tags</h2>")
    if summary.tags:
        # Rendered exactly as the report carries them: no leading marker is added
        # back, and hyphenated issue keys keep their hyphens.
        body.extend(_render_tags(summary.tags))
    else:
        body.append('<p class="note">No tag was recorded.</p>')

    body.append("<h2>Features</h2>")
    if not report.features:
        body.append('<p class="note">No feature was recorded.</p>')
    else:
        body.extend(
            [
                "<table>",
                "<thead><tr><th>#</th><th>Feature</th><th>Location</th>"
                '<th class="numeric">Scenarios</th><th class="numeric">Failed</th>'
                "<th>Status</th></tr></thead>",
                "<tbody>",
            ]
        )
        for index, feature in enumerate(report.features):
            ordinal = index + 1
            page = page_names[index]
            scenarios = feature.scenarios
            failed = sum(1 for entry in scenarios if entry.failed)
            label = feature.name or feature.uri or f"Feature {ordinal}"
            body.append(
                f'<tr><td class="numeric">{ordinal}</td>'
                f'<td><a href="{_text(page)}">{_text(label)}</a></td>'
                f'<td><span class="location">{_text(feature.uri)}:{feature.line}</span></td>'
                f'<td class="numeric">{len(scenarios)}</td>'
                f'<td class="numeric">{failed}</td>'
                f"<td>{_status_markup(feature.status)}</td></tr>"
            )
        body.extend(["</tbody>", "</table>"])

    body.extend(_render_footer())
    return _document("PrettyReports", body)


def render_pretty_reports(report: NormalizedReport) -> Mapping[str, str]:
    """Render *report* into a mapping of file name to file content.

    The pure half of this module: it performs no file access whatsoever, so the
    whole artifact can be asserted from memory. Pair it with
    :func:`generate_pretty_reports` to put the result on disk.

    The output is deterministic. Features are rendered in the order the report
    layer already fixed with a stable total key, feature documents are named
    after their position, and no clock reading, host name, working directory,
    absolute path or run identifier is written into any file. The same report
    therefore always renders to the same bytes -- which is what lets a test
    compare two renderings for byte equality and lets a reviewer diff two runs.

    Args:
        report: A parsed report, normally obtained from the Cucumber JSON adapter
            so that features sharing a ``uri`` have already been merged by
            concatenating their elements. That merge is what guarantees a
            parallel run renders every scenario a serial run did (criterion V8).

    Returns:
        A read-only mapping from bare file name to complete file content: the
        index document, the stylesheet, and one document per feature. Always at
        least those first two, so an empty report still yields a valid,
        browsable directory.
    """
    page_names = tuple(feature_page_name(index + 1) for index in range(len(report.features)))
    rendered: dict[str, str] = {
        INDEX_NAME: _render_index(report, page_names),
        STYLESHEET_NAME: _STYLESHEET,
    }
    total = len(report.features)
    for index, feature in enumerate(report.features):
        # Positional naming, never name-based: several features could share a
        # name just as several scenarios do (defect D9), and a collision here
        # would silently drop a whole feature document.
        rendered[page_names[index]] = _render_feature_page(feature, index + 1, total)
    return MappingProxyType(rendered)


def render_document(document: object) -> Mapping[str, str]:
    """Normalize a decoded report *document* and render it.

    A convenience for a caller holding a decoded payload rather than a parsed
    report -- the report-generation script, or a test building a document by
    hand. Normalization is total and never raises, so a payload of the wrong
    shape simply renders as a report with no feature.

    Args:
        document: A decoded JSON payload, normally the list returned by a JSON
            parser.

    Returns:
        The same read-only mapping :func:`render_pretty_reports` returns.
    """
    return render_pretty_reports(normalize_document(document))


# =============================================================================
# Locations.
#
# No path is composed here. The output directory and the input report both come
# from `app/utils/paths.py`, the one module that knows the artifact-root name, so
# the root can never be renamed by accident and the CI publisher's report include
# pattern keeps matching. Creating directories is delegated to that module as
# well: this one never calls a directory-creation primitive itself, and it never
# creates -- nor removes -- the artifact root.
# =============================================================================


def pretty_reports_dir(base_dir: paths.StrPath | None = None) -> Path:
    """Return the location of the PrettyReports output directory.

    Args:
        base_dir: Optional directory the artifact root should sit inside. The
            default returns the repository-relative location the test
            configuration and the CI pipeline already use; passing a temporary
            directory re-roots the whole layout, which is how a test points this
            module somewhere harmless.

    Returns:
        The output directory. It is not required to exist and is not created
        here.
    """
    if base_dir is None:
        return paths.PRETTY_REPORTS_DIR
    return paths.resolve_layout(base_dir).pretty_reports_dir


def _output_directory(
    output_dir: paths.StrPath | None,
    base_dir: paths.StrPath | None,
) -> Path:
    """Return the directory a caller means, honouring both optional arguments."""
    if output_dir is not None:
        return Path(output_dir)
    return pretty_reports_dir(base_dir)


def _is_inside(candidate: paths.StrPath, container: paths.StrPath) -> bool:
    """Report whether *candidate* resolves to *container* or something beneath it.

    Both sides are fully resolved before comparison, so a ``..`` segment, an
    absolute path elsewhere on the filesystem and a symbolic link pointing out of
    the tree are all caught -- a textual comparison on the unresolved strings
    would miss the last of those entirely.

    Args:
        candidate: The path to test. It need not exist.
        container: The directory it must stay inside.

    Returns:
        ``True`` only when containment can be positively established. A path that
        cannot be resolved at all yields ``False``, because an unverifiable path
        must never be treated as safe.
    """
    try:
        resolved_container = Path(container).resolve()
        resolved_candidate = Path(candidate).resolve()
    except (OSError, RuntimeError, ValueError) as error:
        # ValueError covers a hostile string such as one carrying an embedded NUL;
        # OSError and RuntimeError cover symbolic-link cycles and filesystems
        # that refuse to resolve. All three mean "cannot be proven safe".
        _LOGGER.warning(
            "Refusing a PrettyReports path that could not be resolved (%s: %s)",
            type(error).__name__,
            error,
        )
        return False
    return resolved_candidate.is_relative_to(resolved_container)


def is_within_pretty_reports_dir(
    candidate: paths.StrPath,
    base_dir: paths.StrPath | None = None,
    output_dir: paths.StrPath | None = None,
) -> bool:
    """Report whether *candidate* stays inside the PrettyReports directory.

    The containment guard, exposed because two callers need it: a route serving a
    file out of the directory -- where a path escaping it would turn a report
    endpoint into arbitrary file disclosure -- and regeneration, which deletes
    the documents an earlier run left behind and must prove where it is deleting
    before it does.

    Args:
        candidate: The path to test. It need not exist.
        base_dir: Optional directory the artifact root sits inside.
        output_dir: Optional explicit output directory, overriding *base_dir*.

    Returns:
        ``True`` if *candidate* resolves to the output directory itself or to
        something beneath it, ``False`` otherwise -- including when the path
        cannot be resolved.
    """
    return _is_inside(candidate, _output_directory(output_dir, base_dir))


def content_type_for(name: str) -> str:
    """Return the content type a route should send with *name*.

    Args:
        name: A bare file name from inside the directory.

    Returns:
        The document or stylesheet content type, including its charset, or
        :data:`FALLBACK_CONTENT_TYPE` for a name this module would never have
        written -- so a stray file can never be interpreted as markup.
    """
    return _CONTENT_TYPES.get(Path(name).suffix.lower(), FALLBACK_CONTENT_TYPE)


def _is_servable_name(name: str) -> bool:
    """Report whether *name* is a plain file name this module could have written.

    Rejects everything that could escape the directory -- a directory component,
    a parent reference, an absolute path, an embedded NUL -- and then requires one
    of the two extensions this module produces.
    """
    if not name or name in {".", ".."} or "\x00" in name:
        return False
    if "/" in name or "\\" in name:
        return False
    candidate = Path(name)
    if candidate.is_absolute() or len(candidate.parts) != 1:
        return False
    return candidate.suffix.lower() in GENERATED_NAME_EXTENSIONS


def resolve_page(
    name: str,
    base_dir: paths.StrPath | None = None,
    output_dir: paths.StrPath | None = None,
) -> Path | None:
    """Resolve *name* to a readable file inside the directory, or refuse.

    Two independent checks have to pass: *name* must be a plain file name with one
    of the produced extensions, and the composed path must still resolve inside
    the directory. Only then is the file's existence tested.

    Args:
        name: A bare file name, as a route would receive it.
        base_dir: Optional directory the artifact root sits inside.
        output_dir: Optional explicit output directory, overriding *base_dir*.

    Returns:
        The resolved path when the file exists and is safe to serve, otherwise
        ``None``. No exception is raised for a hostile or absent name: refusal is
        reported, and the API layer decides what response that becomes.
    """
    directory = _output_directory(output_dir, base_dir)
    if not _is_servable_name(name):
        _LOGGER.warning("Refusing an unsafe PrettyReports file name %r", name)
        return None
    candidate = directory / name
    if not _is_inside(candidate, directory):
        _LOGGER.warning("Refusing a PrettyReports path outside %s", paths.to_posix(directory))
        return None
    if not candidate.is_file():
        # `Path.is_file` reports False rather than raising for a missing path, a
        # permission failure or a path component that is not a directory, so a
        # fresh checkout with no artifact tree simply yields None here.
        return None
    return candidate


# =============================================================================
# Inspection -- the cheap "has it been generated?" query.
#
# Deliberately separate from generation: an index page has to be able to say
# whether the artifact exists without producing it as a side effect.
# =============================================================================


def describe_pretty_reports(
    base_dir: paths.StrPath | None = None,
    output_dir: paths.StrPath | None = None,
) -> PrettyReportsDirectory:
    """Inspect the output directory and describe what is there.

    Reads directory entries only -- no file content -- and never generates
    anything. The function is total: no exception escapes it.

    Args:
        base_dir: Optional directory the artifact root sits inside.
        output_dir: Optional explicit output directory, overriding *base_dir*.

    Returns:
        An immutable :class:`PrettyReportsDirectory`. An absent directory is
        reported as such rather than as an error: the artifact root is wiped
        before every run and is untracked, so an absent directory is the ordinary
        starting state.
    """
    directory = _output_directory(output_dir, base_dir)
    location = paths.to_posix(directory)

    if not directory.is_dir():
        _LOGGER.debug("PrettyReports directory %s %s", location, NOT_GENERATED_DESCRIPTION)
        return PrettyReportsDirectory(
            status=PrettyReportsDirectoryStatus.ABSENT,
            path=directory,
            reason=f"{location} has not been generated yet",
        )

    try:
        names = tuple(sorted(entry.name for entry in directory.iterdir() if entry.is_file()))
    except OSError as error:
        reason = f"{location} could not be listed ({type(error).__name__}: {error})"
        _LOGGER.warning("PrettyReports directory unreadable: %s", reason)
        return PrettyReportsDirectory(
            status=PrettyReportsDirectoryStatus.UNREADABLE,
            path=directory,
            reason=reason,
        )

    if INDEX_NAME not in names:
        return PrettyReportsDirectory(
            status=PrettyReportsDirectoryStatus.EMPTY,
            path=directory,
            files=names,
            reason=f"{location} holds no {INDEX_NAME}",
        )

    return PrettyReportsDirectory(
        status=PrettyReportsDirectoryStatus.GENERATED,
        path=directory,
        files=names,
    )


def is_pretty_reports_generated(
    base_dir: paths.StrPath | None = None,
    output_dir: paths.StrPath | None = None,
) -> bool:
    """Report whether the directory holds a rendered index document.

    The cheapest possible question, for an index page that wants to link to the
    artifact only when it exists.

    Args:
        base_dir: Optional directory the artifact root sits inside.
        output_dir: Optional explicit output directory, overriding *base_dir*.

    Returns:
        ``True`` only when the directory exists and holds an index document.
    """
    return describe_pretty_reports(base_dir, output_dir).is_generated


# =============================================================================
# Generation -- the impure half.
#
# Directory creation is delegated to `app/utils/paths.py`, which owns the layout
# and whose helpers never raise. Nothing here creates or removes the artifact
# root: wiping it is the build file's clean target, the port of the source
# build's own clean step.
#
# Removal is confined to files this module itself wrote, inside the directory it
# was asked to write, and is guarded twice over -- see `_remove_generated_file`.
# =============================================================================


def _ensure_output_directory(output_dir: Path, base_dir: paths.StrPath | None) -> bool:
    """Make sure *output_dir* exists, delegating creation to the layout module.

    Two cases, and neither calls a directory-creation primitive here:

    * the caller wants the canonical location, so the whole artifact layout is
      ensured through the layout module's own helper -- the same helper the
      application factory, the build file and the test configuration call, which
      is what makes creation guaranteed in more than one place;
    * the caller redirected the output somewhere else inside the artifact root, so
      only that leaf is ensured, again through the layout module.

    Args:
        output_dir: The directory that has to exist.
        base_dir: The base directory the caller supplied, if any.

    Returns:
        ``True`` when the directory exists once the call returns.
    """
    if output_dir == paths.resolve_layout(base_dir).pretty_reports_dir:
        paths.ensure_target_layout(base_dir)
    else:
        # `ensure_parent_directory` creates the parent of the path it is given, so
        # handing it a file inside the directory creates exactly that directory.
        paths.ensure_parent_directory(output_dir / INDEX_NAME)
    return output_dir.is_dir()


def _remove_generated_file(path: Path, output_dir: Path) -> bool:
    """Delete *path*, but only once it is proven safe to delete.

    This is the only place in the module that deletes anything, and it is guarded
    twice before it does:

    1. *path* must resolve inside *output_dir*. A path that escapes -- through a
       ``..`` segment, an absolute location or a symbolic link -- is refused.
    2. *path* must carry a name this module itself writes. A file that arrived in
       the directory some other way is never touched, and no directory is ever
       removed.

    Neither guard is an optimisation: together they are what keeps regeneration
    from reaching the artifact root, a sibling artifact directory, or anything
    outside the tree.

    Args:
        path: The file to delete.
        output_dir: The directory it must sit inside.

    Returns:
        ``True`` when the file is gone, ``False`` when it was refused or the
        filesystem declined. Never raises.
    """
    if not _is_inside(path, output_dir):
        _LOGGER.error(
            "Refusing to delete %s: it is outside the PrettyReports directory %s",
            paths.to_posix(path),
            paths.to_posix(output_dir),
        )
        return False
    if not is_generated_name(path.name):
        _LOGGER.error(
            "Refusing to delete %s: %r is not a name this module writes",
            paths.to_posix(path),
            path.name,
        )
        return False
    try:
        path.unlink()
    except OSError as error:
        _LOGGER.warning(
            "Could not delete the stale PrettyReports document %s (%s: %s)",
            paths.to_posix(path),
            type(error).__name__,
            error,
        )
        return False
    return True


def _prune_stale_documents(output_dir: Path, keep: frozenset[str]) -> tuple[str, ...]:
    """Delete the documents an earlier, larger run left behind.

    Regeneration has to be idempotent: a report that once held nine features and
    now holds two must not leave the other seven documents lying in the directory,
    where a reader would take them for current. Only names this module writes are
    considered, so a file placed there by anything else survives untouched.

    Args:
        output_dir: The directory to prune.
        keep: The names this run is about to write.

    Returns:
        The names actually deleted, sorted. Never raises.
    """
    try:
        entries = sorted(entry.name for entry in output_dir.iterdir() if entry.is_file())
    except OSError as error:
        _LOGGER.warning(
            "Could not list %s to prune stale documents (%s: %s)",
            paths.to_posix(output_dir),
            type(error).__name__,
            error,
        )
        return ()

    removed: list[str] = []
    for name in entries:
        if name in keep or not is_generated_name(name):
            continue
        if _remove_generated_file(output_dir / name, output_dir):
            removed.append(name)
    if removed:
        _LOGGER.debug(
            "Removed %d stale PrettyReports document(s) from %s",
            len(removed),
            paths.to_posix(output_dir),
        )
    return tuple(removed)


def _write_documents(
    output_dir: Path,
    documents: Mapping[str, str],
) -> tuple[tuple[str, ...], str | None]:
    """Write *documents* into *output_dir*.

    Every file is written with an explicit UTF-8 encoding and an explicit newline,
    so the artifact is byte-identical on every platform: the encoding never falls
    back to a locale default and no line ending is translated.

    Names are written in sorted order, so the sequence of filesystem operations is
    itself deterministic.

    Args:
        output_dir: The directory to write into. It must already exist.
        documents: File name to file content.

    Returns:
        A pair of the names written and, when the filesystem refused, the reason.
        A partial write reports the names that did land, so a caller is never told
        nothing happened when something did. Never raises.
    """
    written: list[str] = []
    for name in sorted(documents):
        destination = output_dir / name
        try:
            destination.write_text(documents[name], encoding="utf-8", newline="\n")
        except OSError as error:
            reason = (
                f"{paths.to_posix(destination)} could not be written "
                f"({type(error).__name__}: {error})"
            )
            _LOGGER.warning("PrettyReports document not written: %s", reason)
            return tuple(written), reason
        written.append(name)
    return tuple(written), None


def generate_pretty_reports(
    source: paths.StrPath | None = None,
    output_dir: paths.StrPath | None = None,
    base_dir: paths.StrPath | None = None,
) -> PrettyReportsResult:
    """Render the PrettyReports directory from the Cucumber JSON report.

    The public entry point, and the only thing in the repository that produces
    this artifact: no test-runner option emits it, because the reporter that would
    have done so cannot run under the parallel execution the port preserves. The
    directory is therefore rendered from the JSON report, which is what the source
    plugin effectively did as well.

    The function is total -- no exception escapes it -- and it is non-gating by
    design. The source build could not fail, so generating this artifact
    influences no exit code and no verdict, and it succeeds for a report full of
    failures exactly as it does for a report full of passes.

    Every outcome is reported rather than raised:

    * the report has not been generated yet -> ``SOURCE_ABSENT``, nothing written,
      and pointedly *not* an empty directory, which would imply a run that never
      happened;
    * the report is empty, unparseable or off-schema with nothing usable in it ->
      ``SOURCE_INVALID`` naming the reason, nothing written;
    * the report is well-formed but recorded no scenario -> ``EMPTY``, a valid
      directory that says so, and a success: that is the ordinary outcome of the
      documented invocation;
    * the report holds scenarios -> ``GENERATED``. A document that breaks the
      schema yet still carries scenarios lands here too, with the schema finding
      recorded, because the source system would have published those scenarios;
    * the filesystem refused -> ``WRITE_FAILED`` naming the failure;
    * the requested location is outside the artifact root -> ``REFUSED``, with
      nothing read, written or deleted.

    Args:
        source: Optional explicit Cucumber JSON report to read. Defaults to the
            location the report adapter owns.
        output_dir: Optional explicit output directory. Defaults to the location
            the layout module owns. It must resolve inside the artifact root:
            regeneration deletes stale documents, so an unvalidated location is
            refused outright.
        base_dir: Optional directory the artifact root sits inside, which re-roots
            both defaults at once. This is how a test redirects the whole layout
            into a temporary directory.

    Returns:
        An immutable :class:`PrettyReportsResult`.
    """
    target = _output_directory(output_dir, base_dir)
    source_path = report_path(base_dir) if source is None else Path(source)
    artifact_root = paths.target_root(base_dir)

    # The destructive-path guard, and the first thing that happens: pruning stale
    # documents deletes files, so the location is proven to sit inside the
    # artifact root before a single byte is read, written or removed.
    if not _is_inside(target, artifact_root):
        reason = (
            f"{paths.to_posix(target)} is outside the artifact root "
            f"{paths.to_posix(artifact_root)}"
        )
        _LOGGER.error("Refusing to generate the PrettyReports directory: %s", reason)
        return PrettyReportsResult(
            status=PrettyReportsStatus.REFUSED,
            output_dir=target,
            source_path=source_path,
            reason=reason,
        )

    # Reading, validating and normalizing the report belongs to the JSON adapter,
    # which is total and reports absence, unreadability, malformed content and
    # schema findings as outcomes rather than exceptions. Going through it is also
    # what merges features that share a URI -- one per worker after a parallel run
    # -- so that no scenario can be dropped from the rendering (criterion V8).
    report = load_report(source_path)
    normalized = report.normalized
    summary = normalized.summary

    if report.is_absent:
        reason = report.reason or f"{paths.to_posix(source_path)} has not been generated yet"
        _LOGGER.debug("No Cucumber JSON report to post-process: %s", reason)
        return PrettyReportsResult(
            status=PrettyReportsStatus.SOURCE_ABSENT,
            output_dir=target,
            source_path=source_path,
            reason=reason,
        )

    if report.is_invalid and summary.scenario_count == 0:
        # Nothing usable came out of the document, so there is nothing to render.
        # This is deliberately distinct from the well-formed empty report above:
        # that one is a successful run of zero scenarios, this one is a report
        # that could not be read.
        reason = report.reason or f"{paths.to_posix(source_path)} could not be post-processed"
        _LOGGER.warning("Cucumber JSON report unusable for PrettyReports: %s", reason)
        return PrettyReportsResult(
            status=PrettyReportsStatus.SOURCE_INVALID,
            output_dir=target,
            source_path=source_path,
            reason=reason,
            source_reason=report.reason,
            summary=summary,
        )

    documents = render_pretty_reports(normalized)

    if not _ensure_output_directory(target, base_dir):
        reason = f"{paths.to_posix(target)} is not a directory and could not be created"
        _LOGGER.warning("PrettyReports directory unavailable: %s", reason)
        return PrettyReportsResult(
            status=PrettyReportsStatus.WRITE_FAILED,
            output_dir=target,
            source_path=source_path,
            reason=reason,
            source_reason=report.reason,
            summary=summary,
        )

    removed = _prune_stale_documents(target, frozenset(documents))
    written, failure = _write_documents(target, documents)

    if failure is not None:
        return PrettyReportsResult(
            status=PrettyReportsStatus.WRITE_FAILED,
            output_dir=target,
            source_path=source_path,
            files=written,
            removed=removed,
            reason=failure,
            source_reason=report.reason,
            summary=summary,
        )

    status = PrettyReportsStatus.EMPTY if summary.is_empty else PrettyReportsStatus.GENERATED
    result = PrettyReportsResult(
        status=status,
        output_dir=target,
        source_path=source_path,
        files=written,
        removed=removed,
        source_reason=report.reason,
        summary=summary,
    )
    _LOGGER.info(
        "%s (%d file(s), %d feature(s), %d scenario(s), %d failed)",
        result.description,
        len(written),
        summary.feature_count,
        summary.scenario_count,
        summary.failed_scenario_count,
    )
    return result
