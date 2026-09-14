"""The single self-contained HTML report -- one page, every asset inlined.

Its destination is :func:`app.utils.paths.cucumber_reports_html_path`; source
anchor: the reference build's copy of the same report, whose AAP 0.4.1 row
reads *"Single self-contained page, per 0.3.4"*.  This is the **first of the
project's two HTML contracts** and it is never collapsed into the second.
Resolving the retained historical build file settled which generator produces
which: ``io.cucumber:cucumber-java:7.2.3`` pulls
``io.cucumber:html-formatter:17.0.0``, which produced *this* one document, while
``me.jvt.cucumber:reporting-plugin:7.2.0`` pulls
``net.masterthought:cucumber-reporting:5.6.1`` over Velocity, which produces the
report *tree* owned by :mod:`app.reporting.pretty_reports`.  The two share
nothing but the three partials named below -- different page set, different
assets, different navigation -- and the specification's Mustache description is
wrong on both counts and is not used (AAP 0.3.4).

What the reference artifact is, measured
----------------------------------------
1,440,365 bytes, line feeds only, and its head is::

    <!DOCTYPE html>
    <html lang="en">
    <head>
    \t<title>Cucumber</title>
    \t<meta content="text/html;charset=utf-8" http-equiv="Content-Type">
    \t<link rel="icon" href="data:image/svg+xml,%3Csvg%20xmlns%3D...">

with the title and meta lines indented with one literal tab, and it ends
``...</script></body></html>``.  **There is no external asset reference of any
kind** -- no stylesheet link, no script source, no image address, no sibling
page.  The favicon is a percent-encoded inline SVG data URI.

Two consequences shape this module:

* **This is not a byte-parity target.**  The committed copy carries an
  unresolved merge block and predates the source, so AAP 0.3.4 reads it *for
  shape*, 0.4.1 maps **no HTML golden fixture**, and deviation 9 accepts both
  HTML outputs *structurally*, generated from this project's own templates.
  Nothing here tries to match the reference's size, markup or asset bytes.
* **The reference renders in the browser; this port renders server-side.**  The
  reference's content container is empty and a 1.4 MB inlined bundle draws the
  report from an embedded message blob at load time.  The acceptance criterion
  for this artifact is the exact opposite -- every feature, scenario, step,
  status, timestamp and embedded screenshot from the input result set has to be
  **present in the DOM** -- and ``app/static/css/main.css`` declares no bare or
  identifier selector, so a bare content container would be unstyled anyway.
  ``app/templates/artifact/report.html`` is written on precisely that basis.

The division of labour
----------------------
**This module writes no markup.**  ``app/templates/artifact/report.html`` is the
root document and owns the shell; ``artifact/metadata.html``,
``artifact/feature.html`` and ``artifact/element.html`` own the blocks; and
``partials/status_badge.html``, ``partials/step_row.html`` and
``partials/lightbox.html`` -- the three templates shared with the pretty set and
the HTTP views -- own the badge, the step row and the screenshot lightbox over
one normalised result model, which is what makes AAP 0.4.2's invariant true:
*no view contradicts an artifact*.  This module renders that template with the
right context and writes the result.

The render context is a contract.  ``artifact/report.html`` reads exactly five
names and this module supplies all five, always:

``features``
    The ordered feature list, in **source order** with scenarios in line order,
    each feature and each element carrying the ``status``
    :mod:`app.reporting.aggregation` computed for it: the templates read a
    status and never derive one, and neither does this module.  Selection is
    applied there too -- see below.
``summary``
    Counts of features, scenarios and steps by status, plus the run's earliest
    scenario start, exactly as
    :func:`app.reporting.aggregation.build_summary` computes them.  This is no
    longer *the same rules* as ``GET /reports/summary`` applies -- that was the
    claim two independent tallies made while disagreeing -- but the same
    **values**, because both surfaces read the one aggregate.
``metadata``
    The environment and run descriptor: ``implementation{name,version}``,
    ``runtime{name,version}``, ``os{name}``, ``cpu{name}``, ``generated_at`` and
    ``started_at``.  A probe that yields nothing yields ``""`` and never a
    failure.
``inline_css`` and ``inline_js``
    The full text of ``app/static/css/main.css`` and
    ``app/static/js/report.js``, read at write time and wrapped in
    :class:`markupsafe.Markup` **here**, which is the one reason no template in
    this project marks anything trusted and autoescaping stays on everywhere.

Where the numbers come from
---------------------------
**Nothing on this page is aggregated here.**
:mod:`app.reporting.aggregation` is the one normalised result model AAP 0.3.4
and 0.4.2 require -- *"Both HTML outputs and the HTTP views render over one
normalized result model \u2026 so no view contradicts an artifact"* -- and this
module is one of its four consumers, alongside
:mod:`app.reporting.pretty_reports`, the Pretty templates and
``app/web/routes.py``.  Status normalisation, the severity fold, selection, the
step and scenario counts and the run's earliest start all live there;
:func:`build_render_context` makes **one** call to
:func:`app.reporting.aggregation.normalize_run` and hands its answer to the
template.  The aggregation names this module still exports -- ``status_token``,
``roll_up_status``, ``element_status``, ``feature_status``,
``decorated_features``, ``emitted_features``, ``earliest_start``,
``build_summary`` and the status vocabulary -- are that module's own functions
bound here, not second implementations of them: a second implementation is
exactly what produced the contradictions below.

Two of those contradictions are worth naming, because the tally this module
used to keep got them wrong and its own documentation defended the result:

* **A step-less element is ``passed`` on every surface.**  It was ``passed`` in
  this writer and ``unknown`` in ``GET /reports/summary``, and the writer was
  right: an empty ``StatusCounter`` in
  ``net.masterthought:cucumber-reporting:5.6.1`` answers ``PASSED``, and the
  reference tree renders ``EmployeeFc.feature``'s empty Background as passed.
  That is now a single measured constant,
  :data:`app.reporting.aggregation.EMPTY_ELEMENT_STATUS`, rather than a
  divergence this file described as deliberate.
* **Hook statuses take part in an element's and a feature's status.**  They
  reached some Pretty surfaces and were ignored here.
  ``Element.calculateElementStatus`` folds ``stepsStatus`` with
  ``beforeStatus`` and ``afterStatus``, so a scenario whose steps all passed
  but whose after-hook failed is no longer badged as a pass on this page.  A
  hook is still never counted as a *step* and its duration is never added,
  because ``TagObject.addElement`` sums ``Step.getDuration()`` alone.

Three obligations the templates cannot enforce and this module discharges:

* the stylesheet text must not contain a closing style tag and the script text
  must not contain a closing script tag, either of which would terminate its
  block early and corrupt the document from that point on;
* the rendered document must end with exactly one trailing newline, which the
  engine would otherwise strip;
* **non-selected scenarios are dropped before rendering.**
  ``artifact/element.html`` states it as a premise -- the selection flag is not
  rendered *because* such scenarios never reach a template -- and the rule is
  the JSON writer's, applied once by
  :func:`app.reporting.aggregation.selected_features`: a scenario the tag
  expression did not select never started, the JVM emitted no test case for it,
  and a feature left with no test case is omitted altogether.  Rendering it
  would put a scenario on this page that the merged Cucumber JSON report does
  not carry, and the viewer reads that report.

Boundaries
----------
Imports are stdlib, :mod:`jinja2`, :mod:`markupsafe`,
:mod:`app.reporting.aggregation`, :mod:`app.reporting.events` and
:mod:`app.utils.paths`, and nothing else: the dependency graph's edge runs from
the services to the writers and never back, so no service is imported here, and
neither is Flask, Selenium, :mod:`app.config` nor :mod:`app.web`.  The
aggregation module is a peer within this package and imports nothing beyond the
standard library and the result schema, so consuming it adds no edge.  The
template environment is a plain
:class:`jinja2.Environment` because this writer runs inside a worker process
that never builds an application -- the framework's template helper and
application proxy both need an application context, and a framework-built static
address could not resolve from a file opened over the file protocol in any case.

No path literal appears here.  The output path, the template root and the static
root are :mod:`app.utils.paths`' to own.  That module creates directories and
never removes them; emptying the build output directory is ``app/cli.py``'s
``--clean`` step, so this module deletes nothing.

Determinism is **structural** (AAP 0.6).  Byte stability is impossible -- the
page surfaces per-scenario timestamps, durations and a generation time -- so
what holds for identical input is the structure: features in source order,
scenarios in line order, each Background occurrence repeated where the model
puts it, and every derived collection built in a fixed order.  Nothing is
sorted; the pipeline publisher's alphabetical sorting is a display option of
that publisher and imposes nothing on artifacts.

**A test outcome never raises.**  ``testFailureIgnore`` is true in the retained
build file and all six publisher thresholds are ``-1``: a failed, undefined,
pending or skipped scenario is data to render, and every read of the result set
here and in :mod:`app.reporting.aggregation` is total.  Only a genuine render
or I/O fault propagates, which is the command's writer-failure exit class --
artifacts written before it remain, **this artifact's own previous copy
included**, because the page is published by rename rather than by truncation
(see :func:`write_html_report`), and the failing writer is named on stderr.

No merge-conflict marker is ever emitted.  The markers exist only in the
unmodified reference checkout (AAP 0.2.2), and ``.gitattributes`` keeps
``*.html linguist-detectable=false`` precisely because this writer still emits
HTML.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final
from uuid import uuid4

from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup

# The aggregation authority.  Every name here is imported for one of two
# reasons and never for a third: ``normalize_run``, ``selected_features``,
# ``as_mapping`` and ``as_text`` are *used* below, and the rest are the surface
# this module has always exported -- kept importable from here, with the
# authority's meaning, because a consumer that imported them from this writer
# must keep working and must not be handed a second implementation.
from app.reporting.aggregation import (
    EMPTY_AGGREGATE_STATUS,
    EMPTY_ELEMENT_STATUS,
    KNOWN_STATUSES,
    STATUS_PRECEDENCE,
    STATUS_READING_ORDER,
    UNKNOWN_STATUS,
    as_mapping,
    as_text,
    build_summary,
    decorated_features,
    earliest_start,
    element_status,
    feature_status,
    normalize_run,
    roll_up_status,
    selected_features,
    status_token,
)
from app.reporting.events import (
    JsonDict,
    ResultSet,
    format_timestamp,
    run_metadata,
)
from app.utils.paths import (
    cucumber_reports_html_path,
    ensure_parent,
    static_dir,
    templates_dir,
)

#: Module logger.  Deliberately without a handler of its own: the command-line
#: entry point installs the handler split that routes WARNING-and-above to
#: stderr, and Python's ``lastResort`` handler covers a bare import in a test.
logger = logging.getLogger(__name__)

__all__ = [
    "CSS_ASSET_PARTS",
    "EMPTY_AGGREGATE_STATUS",
    "EMPTY_ELEMENT_STATUS",
    "FORBIDDEN_IN_SCRIPT",
    "FORBIDDEN_IN_STYLE",
    "JS_ASSET_PARTS",
    "KNOWN_STATUSES",
    "REPORT_TEMPLATE",
    "STATUS_PRECEDENCE",
    "STATUS_READING_ORDER",
    "UNKNOWN_STATUS",
    "build_environment",
    "build_metadata",
    "build_render_context",
    "build_summary",
    "css_asset_path",
    "decorated_features",
    "earliest_start",
    "element_status",
    "emitted_features",
    "feature_status",
    "inline_asset",
    "js_asset_path",
    "render_html_report",
    "roll_up_status",
    "status_token",
    "write_html_report",
]


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

#: The root document, addressed from the template loader's root, which is
#: :func:`app.utils.paths.templates_dir`.  The folder prefix is what makes the
#: template's own ``artifact/feature.html`` and ``partials/lightbox.html``
#: references resolve against the same root.
REPORT_TEMPLATE: Final[str] = "artifact/report.html"

#: ``app/static/css/main.css``, as path components under
#: :func:`app.utils.paths.static_dir`.  Components rather than a joined string
#: so the separator is the platform's and no path literal is spelled out.
CSS_ASSET_PARTS: Final[tuple[str, ...]] = ("css", "main.css")

#: ``app/static/js/report.js``, likewise.
JS_ASSET_PARTS: Final[tuple[str, ...]] = ("js", "report.js")

#: The token that must not occur in the inlined stylesheet.  Its presence would
#: close the style block early and render the remainder of the stylesheet as
#: document text.  Compared case-insensitively, because the parser is.
FORBIDDEN_IN_STYLE: Final[str] = "</style"

#: The same for the inlined behaviour script.  A script block is closed by this
#: token wherever it occurs -- inside a string literal or a comment included --
#: which is why the check is a substring scan rather than a parse.
FORBIDDEN_IN_SCRIPT: Final[str] = "</script"

#: How the temporary file the page is published from is named.  Dot-prefixed,
#: because :func:`app.utils.paths.resolve_artifact` rejects every path
#: component beginning with a dot, so the temporary cannot be served by
#: ``GET /artifacts/<name>`` during the moment it exists; derived from the
#: destination, so it is recognisable in a directory listing; and carrying the
#: process identifier and a random token, so two writers publishing at once
#: cannot claim one temporary.  Formatted with ``name``, ``pid`` and ``token``;
#: see :func:`write_html_report`.
_TEMPORARY_NAME: Final[str] = ".{name}.{pid}.{token}.partial"

#: The metadata sub-objects that carry a name and a version, so a gap in one can
#: be filled from the local probe without discarding the other half.
_VERSIONED_METADATA_KEYS: Final[tuple[str, ...]] = ("implementation", "runtime")

#: The metadata sub-objects that carry a name alone.
_NAMED_METADATA_KEYS: Final[tuple[str, ...]] = ("os", "cpu")

#: What a metadata probe that yielded nothing contributes.  The same value
#: :func:`app.reporting.events.run_metadata` uses, so the two agree.
_UNKNOWN_METADATA_VALUE: Final[str] = ""


# --------------------------------------------------------------------------- #
# What this module no longer computes.
#
# The coercions, the predicates, the status fold, the selection rule and the
# tally all used to live here, and the four other report surfaces each kept a
# copy.  They now live once, in :mod:`app.reporting.aggregation`, and the names
# below are that module's functions bound into this namespace: importing
# ``status_token`` or ``build_summary`` from this writer still works and still
# means what it meant, and there is no second implementation to drift.  The two
# aliases exist so the metadata block's call sites keep the spelling they
# always had -- a thin wrapper would be a second answer to "what is a
# malformed document?", and a rename would churn lines no finding touches.
# --------------------------------------------------------------------------- #

#: :func:`app.reporting.aggregation.as_mapping`, under the name the metadata
#: block calls it by.
_as_mapping = as_mapping

#: :func:`app.reporting.aggregation.as_text`, likewise.
_as_text = as_text


def emitted_features(result_set: ResultSet | None) -> list[JsonDict]:
    """Return the features this page renders, in source order.

    This writer's name for :func:`app.reporting.aggregation.selected_features`,
    which applies the JSON writer's two selection rules -- drop a
    Background-plus-scenario unit that was not selected, then drop a feature
    left with no test case -- so that this page and the merged Cucumber JSON
    report describe the same run.  The delegation is deliberate rather than an alias:
    ``artifact/element.html`` and the tests written against this module address
    the rule by *this* name, while the rule itself must have exactly one
    implementation.

    Args:
        result_set: The merged result document, or ``None`` for a run that
            produced nothing -- all four artifacts are still written in that
            case, so ``None`` yields an empty list rather than an error.

    Returns:
        The feature mappings to render, in document order.  Nothing is sorted
        and nothing is mutated: a feature that loses a unit is copied, so the
        document the other three writers see is untouched.  Never raises.
    """
    return selected_features(result_set)


# --------------------------------------------------------------------------- #
# Metadata.  The vocabulary is the reference payload's own, rendered by
# ``app/templates/artifact/metadata.html``, and a probe is never allowed to
# fail a run: a report is not worth a failed suite.
# --------------------------------------------------------------------------- #


def _skeleton_metadata() -> JsonDict:
    """Return the metadata vocabulary with every value blank.

    The floor this module never falls below: the four objects and their
    sub-keys are always present, so the template's guards are exercised on
    shape rather than on membership, and an unavailable probe surfaces as an
    omitted row instead of an exception.

    Returns:
        A fresh mapping carrying ``implementation``, ``runtime``, ``os`` and
        ``cpu``, each with blank values.
    """
    return {
        "implementation": {"name": "", "version": ""},
        "runtime": {"name": "", "version": ""},
        "os": {"name": ""},
        "cpu": {"name": ""},
    }


def _local_metadata() -> JsonDict:
    """Describe this process, and never raise.

    :func:`app.reporting.events.run_metadata` already guards each individual
    probe -- ``platform.processor()`` is empty on many Linux builds, for
    instance, and yields the machine type instead -- so this wrapper exists for
    the one case that function cannot cover: a probe replaced wholesale, by a
    test or by a platform that fails at import.  Then the blank skeleton stands
    in and the run continues.

    Returns:
        The probed metadata, or :func:`_skeleton_metadata` if probing failed.
    """
    try:
        probed = _as_mapping(run_metadata())
    except Exception:
        logger.warning(
            "Run metadata could not be probed; the report will omit it",
            exc_info=True,
        )
        return _skeleton_metadata()
    return probed or _skeleton_metadata()


def _first_text(*candidates: Any) -> str:
    """Return the first candidate that is non-blank text.

    Args:
        *candidates: Values in order of preference -- typically the value the
            result set recorded, then the value probed locally.

    Returns:
        The first non-blank trimmed string, or :data:`_UNKNOWN_METADATA_VALUE`
        when every candidate is blank, absent or not text at all.
    """
    for candidate in candidates:
        text = _as_text(candidate)
        if text:
            return text
    return _UNKNOWN_METADATA_VALUE


def build_metadata(
    result_set: ResultSet | None,
    generated_at: str | None = None,
    started_at: str | None = None,
) -> JsonDict:
    """Build the ``metadata`` context value.

    The result set's **own** metadata is preferred and gaps are filled from
    this process, so a document merged from several workers reports the *run's*
    environment rather than the environment of whichever process happened to
    write the page.  Filling is per field, not per object: a recorded
    implementation name with no version keeps the name and takes the version
    locally.

    The two timestamps are **lifted into** this mapping from the result set's
    top level, because ``app/templates/artifact/metadata.html`` reads them from
    here -- which is also why they are formatted by
    :func:`app.reporting.events.format_timestamp` and never reformatted: the
    string on this page is the string the JSON artifact carries.

    Args:
        result_set: The merged result document, or ``None``.
        generated_at: An explicit generation time, which wins over the
            document's own.  A caller pins it so that two renders of one input
            can be compared byte for byte.
        started_at: The fallback run start, normally the tally's earliest
            scenario start.  Used only when the document carries no run-level
            ``started_at`` of its own.

    Returns:
        A mapping carrying exactly ``implementation``, ``runtime``, ``os``,
        ``cpu``, ``generated_at`` and ``started_at``.  Every value is a string
        and an unavailable one is ``""``, never ``None`` and never an
        exception.
    """
    document = _as_mapping(result_set)
    recorded = _as_mapping(document.get("metadata"))
    local = _local_metadata()

    metadata: JsonDict = {}
    for key in _VERSIONED_METADATA_KEYS:
        stated = _as_mapping(recorded.get(key))
        probed = _as_mapping(local.get(key))
        metadata[key] = {
            "name": _first_text(stated.get("name"), probed.get("name")),
            "version": _first_text(stated.get("version"), probed.get("version")),
        }
    for key in _NAMED_METADATA_KEYS:
        stated = _as_mapping(recorded.get(key))
        probed = _as_mapping(local.get(key))
        metadata[key] = {
            "name": _first_text(stated.get("name"), probed.get("name")),
        }

    metadata["generated_at"] = _first_text(
        generated_at,
        document.get("generated_at"),
    ) or format_timestamp(datetime.now(UTC))
    metadata["started_at"] = _first_text(
        document.get("started_at"),
        started_at,
    )
    return metadata


# --------------------------------------------------------------------------- #
# The two inlined assets.  Self-containment is the whole point of this
# artifact: the only address permitted anywhere in the output is an inline data
# URI -- the favicon in the shell, and the failure screenshots the lightbox
# partial emits from the results' own attachment payload.
# --------------------------------------------------------------------------- #


def css_asset_path() -> Path:
    """Return the stylesheet this writer inlines.

    Resolved under :func:`app.utils.paths.static_dir`, which is
    package-relative, so it is correct whatever directory the process runs in
    and whether the package is used from the source tree or an installed wheel.

    Returns:
        The absolute path of ``app/static/css/main.css``.
    """
    return static_dir().joinpath(*CSS_ASSET_PARTS)


def js_asset_path() -> Path:
    """Return the behaviour script this writer inlines.

    Returns:
        The absolute path of ``app/static/js/report.js``.
    """
    return static_dir().joinpath(*JS_ASSET_PARTS)


def inline_asset(path: Path | str, forbidden: str | None = None) -> Markup:
    """Read a first-party asset and hand it back as trusted markup.

    This is the **one** place in the project where anything is declared trusted,
    and the division is deliberate: the stylesheet and the script are
    first-party text this module inlines, so it is this module that marks them,
    in Python -- whereas result data carries assertion text, tracebacks and
    Selenium messages full of angle brackets, quotes and braces, and is never
    marked anywhere, which is what keeps autoescaping meaningful on every other
    value the templates interpolate.

    Args:
        path: The asset to read.  UTF-8, explicitly, so a non-ASCII comment or
            content string survives.
        forbidden: A token that must not occur in the text, compared
            case-insensitively -- :data:`FORBIDDEN_IN_STYLE` for the stylesheet
            and :data:`FORBIDDEN_IN_SCRIPT` for the script.  ``None`` skips the
            check.

    Returns:
        The asset text as :class:`markupsafe.Markup`, so the template
        interpolates it into its block unescaped.

    Raises:
        OSError: If the asset cannot be read.  Not swallowed: an empty style
            block would yield a plausible-looking page with no styling at all,
            which is worse than a loud failure at write time, and this is a
            packaging fault rather than a test outcome.
        ValueError: If the asset is empty or blank, or contains ``forbidden``.
            The token would close its block early and render the remainder of
            the asset as document text, corrupting everything after it.
    """
    location = Path(path)
    text = location.read_text(encoding="utf-8")
    if not text.strip():
        raise ValueError(f"Inlined asset is empty: {location}")
    if forbidden and forbidden.lower() in text.lower():
        raise ValueError(
            f"Inlined asset {location} contains {forbidden!r}, "
            "which would terminate its block early"
        )
    return Markup(text)  # noqa: S704 - first-party asset, see the docstring


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def build_environment() -> Environment:
    """Build the template environment this writer renders through.

    A plain :class:`jinja2.Environment` and not the framework's, because this
    writer runs inside a command-line or worker process that never builds an
    application: the framework's template helper and its application proxy both
    require an application context, and this page is written to a file that is
    opened over the file protocol from an archive or a build workspace, where a
    framework-built static address could not resolve in any case.

    Three settings, each for a stated reason:

    * the loader root is :func:`app.utils.paths.templates_dir`, which is what
      makes the templates' own ``artifact/element.html`` and
      ``partials/status_badge.html`` references resolve;
    * autoescaping is on for every template regardless of extension -- feature
      names in this suite carry quotation marks, periods and apostrophes, one
      opens with ``....``, and failure text carries tracebacks -- and the two
      inlined assets are the only values exempt, marked in
      :func:`inline_asset`;
    * the trailing newline is **kept**, because the engine strips one by default
      and the reference artifact ends with a line feed after its closing tag.

    Whitespace control is deliberately left off: every template in the artifact
    set manages its own whitespace with explicit stripping markers, so turning
    block trimming on would strip newlines those templates expect to keep and
    change the emitted source of a document people read and diff.

    Returns:
        A fresh environment.  A caller rendering repeatedly may build one and
        pass it to :func:`render_html_report`, which avoids re-reading the
        templates.
    """
    return Environment(
        loader=FileSystemLoader(str(templates_dir())),
        autoescape=True,
        keep_trailing_newline=True,
    )


def build_render_context(
    result_set: ResultSet | None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build the five-name context ``artifact/report.html`` reads.

    **One** call to :func:`app.reporting.aggregation.normalize_run` supplies
    every number on the page: it selects, decorates and tallies in the order
    those values depend on each other, and this function takes the features,
    the summary and the run's earliest scenario start from that single answer.
    Computing any of them a second time here is what made this page and
    ``GET /reports/summary`` capable of contradicting each other, so nothing
    here recomputes -- the aggregate is read, the assets are inlined, and the
    metadata block takes the aggregate's start time as the fallback for its
    own.

    Args:
        result_set: The merged result document, or ``None`` for a run that
            produced nothing.
        generated_at: An explicit generation time; see :func:`build_metadata`.

    Returns:
        A mapping carrying ``features``, ``summary``, ``metadata``,
        ``inline_css`` and ``inline_js`` -- exactly those five, always: the
        template reads no other name and treats the two assets as required
        rather than defaulted.  ``features`` is a fresh list, so a caller that
        appends to it cannot reach the immutable aggregate behind it.

    Raises:
        OSError: If either inlined asset cannot be read.
        ValueError: If either inlined asset is empty or would close its block
            early; see :func:`inline_asset`.
    """
    aggregate = normalize_run(result_set)
    metadata = build_metadata(
        result_set,
        generated_at=generated_at,
        started_at=aggregate.start_timestamp,
    )
    return {
        "features": list(aggregate.features),
        "summary": aggregate.summary,
        "metadata": metadata,
        "inline_css": inline_asset(css_asset_path(), FORBIDDEN_IN_STYLE),
        "inline_js": inline_asset(js_asset_path(), FORBIDDEN_IN_SCRIPT),
    }


def render_html_report(
    result_set: ResultSet | None,
    environment: Environment | None = None,
    generated_at: str | None = None,
) -> str:
    """Render the whole artifact and return it as one string.

    The pure half: it reads the model, reads the two assets, renders the
    template and **touches no output file**, so a template fault surfaces
    before anything is written and a test can assert on the markup without a
    filesystem.

    A zero-feature document renders a complete, valid page carrying the
    template's empty state, because the command's exit contract writes all four
    artifacts even when the tag expression selected nothing.

    Args:
        result_set: The merged result document, or ``None``.
        environment: An environment to render through; one is built per call
            when omitted.
        generated_at: An explicit generation time, which is what makes two
            renders of one input comparable byte for byte.

    Returns:
        The complete document, ending with exactly one newline.  The final
        newline is guaranteed here as well as by :func:`build_environment`, so
        that an environment supplied by a caller cannot leave the file ending
        mid-line.

    Raises:
        OSError: If either inlined asset cannot be read.
        ValueError: If either inlined asset is unusable.
        jinja2.TemplateError: If a template is missing or fails to render.  A
            render fault is the command's writer-failure exit class; a *test*
            outcome never reaches it, because every read of the result set in
            this module is total.
    """
    engine = build_environment() if environment is None else environment
    template = engine.get_template(REPORT_TEMPLATE)
    context = build_render_context(result_set, generated_at=generated_at)
    document = template.render(**context)
    # The reference artifact ends with a line feed after its closing tag, and a
    # file that ends mid-line is a diagnostic from ordinary text tooling.  No
    # further scan of the rendered text happens here: a merge-conflict marker
    # cannot come from these templates or these assets, and scanning result
    # data for one would let a scenario's own failure text fail the run.
    return document if document.endswith("\n") else f"{document}\n"


def write_html_report(
    result_set: ResultSet | None,
    base: Path | str | None = None,
    path: Path | str | None = None,
    environment: Environment | None = None,
    generated_at: str | None = None,
) -> Path:
    """Write the artifact to :func:`app.utils.paths.cucumber_reports_html_path`.

    The impure half, and deliberately thin: it renders the whole document
    first, so a template or asset fault happens before any file is touched,
    then resolves a destination, creates its parent and **publishes the page by
    rename**.

    **Nothing is ever left partially written.**  The bytes go to a temporary
    file in the destination's own directory -- the same filesystem, because a
    rename is atomic only within one -- which is flushed and
    :func:`os.fsync`'ed before it is closed, and only then does
    :func:`os.replace` move it onto the destination in one indivisible step.
    So a reader, an archiver or the artifact route serving this page sees
    either the previous complete page or this one, never a document
    truncated at the point a write failed.  That is the difference from opening
    the destination itself: ``"w"`` truncates before the first byte exists, and
    a failure anywhere after that destroys an artifact that was complete.  The
    temporary is named by :data:`_TEMPORARY_NAME`, so it is dot-prefixed and
    unservable while it exists, and it is removed on **every** failure path,
    the failed rename included.

    **Exactly one file is produced.**  No stylesheet, script, image, font or
    sibling page is written beside it -- that is the whole contract of this
    artifact -- and nothing is deleted or truncated beyond this one file and
    this writer's own temporary: :mod:`app.utils.paths` creates directories and
    never removes them, and emptying the build output directory is
    ``app/cli.py``'s ``--clean`` step, which runs before the suite does.

    Args:
        result_set: The merged result document, or ``None`` for a run that
            produced nothing -- the page is still written, per the exit
            contract.
        base: Directory to resolve the artifact path against, defaulting to the
            working directory, exactly as every :mod:`app.utils.paths` accessor
            does.  This is the mechanism a test uses to write into a temporary
            directory.
        path: An explicit destination, which overrides ``base`` entirely, for a
            caller that already holds a path.
        environment: As :func:`render_html_report`.
        generated_at: As :func:`render_html_report`.

    Returns:
        The path written, so a caller can name it on stdout or hand it on.

    Raises:
        OSError: If an asset cannot be read, the parent directory cannot be
            created, or the temporary cannot be created, written, synced or
            renamed onto the destination.  Deliberately **not** swallowed:
            producing this artifact is the writer's contract with the exit
            table, whose writer-failure class requires the failing writer to be
            named on stderr while the artifacts written before it remain -- and
            after any of these failures the destination still holds the last
            complete page, because it was never opened for writing.
        ValueError: If either inlined asset is unusable.
        jinja2.TemplateError: If the document fails to render.
    """
    document = render_html_report(
        result_set,
        environment=environment,
        generated_at=generated_at,
    )
    destination = ensure_parent(
        cucumber_reports_html_path(base) if path is None else path
    )
    temporary = destination.with_name(
        _TEMPORARY_NAME.format(
            name=destination.name,
            pid=os.getpid(),
            token=uuid4().hex,
        )
    )
    try:
        # Mode "x" rather than "w": exclusive creation, so this writer can
        # never truncate a file it did not create -- not the destination, and
        # not a temporary another process is publishing from.  UTF-8
        # explicitly, so the French validation message "Veuillez renseigner ce
        # champ." and the apostrophes in scenario names survive; newline="\n"
        # so a page written on Windows is byte-identical to one written on
        # Linux, because the structure of this artifact must not depend on
        # which branch the pipeline's platform test chose.
        with open(temporary, "x", encoding="utf-8", newline="\n") as stream:
            stream.write(document)
            # Flushed out of the interpreter's buffer and synced to the device
            # before the rename, so the name never points at bytes that are
            # still in flight: a power loss or a full filesystem between the
            # two leaves the previous complete artifact rather than an empty
            # or truncated new one.
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    except BaseException:
        # Every failure path, the failed rename and an interrupt included.
        # missing_ok covers the exclusive open that never created the file and
        # the rename that already consumed it; a removal that itself fails is
        # logged and never allowed to replace the original exception, which
        # travels on to app/services/report_service.py -- it names this writer
        # on stderr and turns the fault into the exit contract's
        # writer-failure class.
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            logger.warning(
                "Could not remove the temporary file %s",
                temporary,
                exc_info=True,
            )
        raise
    logger.info("Wrote %s", destination)
    return destination
