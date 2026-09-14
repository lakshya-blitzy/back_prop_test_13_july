"""The single self-contained HTML report -- one page, every asset inlined.

Written to :func:`app.utils.paths.cucumber_reports_html_path`, the first of
two separate HTML contracts (AAP 0.3.4): ``io.cucumber:html-formatter:17.0.0``
produced this document and ``net.masterthought:cucumber-reporting:5.6.1`` the
report *tree* :mod:`app.reporting.pretty_reports` owns.  Both come from this
project's own templates and are accepted structurally (AAP deviation 9).

The page is ``<!DOCTYPE html>``, the declared title ``Cucumber``, the declared
charset ``text/html;charset=utf-8`` -- both head lines indented with one
literal tab -- an inline SVG data-URI favicon, and **no external asset
reference of any kind**.  Every feature, scenario, step, status, timestamp and
embedded screenshot that :func:`emitted_features` selects -- the JSON writer's
own rule -- is rendered into the DOM server-side, and nothing else is.

This module writes no markup: ``app/templates/artifact/report.html`` is the
root document, and its block templates plus the three ``partials/`` templates
shared with the pretty set and the HTTP views render the rest.  That root
reads exactly the five names :func:`build_render_context` always supplies --
``features``, ``summary``, ``metadata``, ``inline_css`` and ``inline_js``.
One :func:`app.reporting.aggregation.normalize_run` call yields every status,
the selection and every count, and the aggregation names this module exports
are bound from there rather than reimplemented.

Three obligations the templates cannot enforce are discharged here: neither
inlined asset may carry the token that would close its block early
(:func:`inline_asset`), the page ends with the single trailing newline the
engine would otherwise strip (:func:`render_html_report`), and an unselected
scenario never reaches a template (:func:`emitted_features`).

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
the HTTP views -- own the badge, the step row and the screenshot lightbox.  The
two artifact sets render them over the one normalised result model
:mod:`app.reporting.aggregation` computes, which is what makes AAP 0.4.2's
invariant hold *between the artifacts*; the HTTP views share the partials but
not the model, and *"Where the numbers come from"* below states that boundary
and the one figure it leaves open.  This module renders that template with the
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
    :func:`app.reporting.aggregation.build_summary` computes them.
    ``GET /reports/summary`` answers the same shape by the same rules with one
    measured exception -- it tallies the parsed artifact itself and reads a
    step-less element as ``unknown`` where this model reads ``passed`` -- so the
    two agree on every run whose elements carry steps and can differ on one that
    does not.  See *"Where the numbers come from"* below.
``metadata``
    The environment and run descriptor: ``implementation{name,version}``,
    ``runtime{name,version}``, ``os{name}``, ``cpu{name}``, ``generated_at`` and
    ``started_at``.  A probe that yields nothing yields ``""`` and never a
    failure.  **This module holds no clock.**  Both timestamps are the run's
    own -- the caller's argument, or the document's value, and ``""`` when
    there is neither -- because the generation time of a run is resolved once,
    by the collector or by :func:`app.services.generate_reports` before the
    fan-out, and is then the same on every artifact that run produced.  See
    :func:`build_metadata`.
``inline_css`` and ``inline_js``
    The full text of ``app/static/css/main.css`` and
    ``app/static/js/report.js``, read at write time and wrapped in
    :class:`markupsafe.Markup` **here**, which is the one reason no template in
    this project marks anything trusted and autoescaping stays on everywhere.

Where the numbers come from
---------------------------
**Nothing on this page is aggregated here.**
:mod:`app.reporting.aggregation` is the normalised result model AAP 0.3.4 and
0.4.2 require -- *"Both HTML outputs and the HTTP views render over one
normalized result model \u2026 so no view contradicts an artifact"* -- and
:func:`build_render_context` makes **one** call to
:func:`app.reporting.aggregation.normalize_run` and hands its answer to the
template.  Status normalisation, the severity fold, selection, the step and
scenario counts and the run's earliest start all live there.

Its direct consumers, stated as the import graph has them rather than as an
aspiration, because a claimed consumer that does not import it is how two
surfaces come to grade one run differently:

* this module, for the single self-contained page;
* :mod:`app.reporting.pretty_reports`, for the report tree -- and through the
  ``model_``-prefixed globals that module installs on its own template
  environment, the Pretty templates call the authority's own functions rather
  than holding folds of their own;
* :mod:`app.reporting.rerun_report`, which grades a scenario unit through the
  same canonical statuses before deciding what the manifest re-selects.

``app/web/routes.py`` is **not** among them: the viewer computes its own tally
in Python from the parsed artifact, by rules its templates declare, and one of
those rules differs -- a step-less element is ``unknown`` there and
:data:`app.reporting.aggregation.EMPTY_ELEMENT_STATUS`, which is ``passed``,
here.  That boundary is recorded rather than papered over, and it is the last
place where a count shown by this artifact and a count shown over HTTP can
disagree.

The aggregation names this module still exports -- ``status_token``,
``roll_up_status``, ``element_status``, ``feature_status``,
``decorated_features``, ``emitted_features``, ``earliest_start``,
``build_summary`` and the status vocabulary -- are that module's own functions
bound here, not second implementations of them: a second implementation is
exactly what produced the contradictions below.

Two of those contradictions are worth naming, because the tally this module
used to keep got them wrong and its own documentation defended the result:

* **A step-less element is ``passed`` in every artifact this package writes.**
  It was ``passed`` in this writer and ``unknown`` in ``GET /reports/summary``,
  and the writer was right: an empty ``StatusCounter`` in
  ``net.masterthought:cucumber-reporting:5.6.1`` answers ``PASSED``, and the
  reference tree renders ``EmployeeFc.feature``'s empty Background as passed.
  That is now a single measured constant,
  :data:`app.reporting.aggregation.EMPTY_ELEMENT_STATUS`, rather than a
  divergence this file described as deliberate -- and it is the value every
  artifact reads.  The viewer's own tally still answers ``unknown`` for that
  element, which is the boundary recorded above rather than a second reading
  this package holds.
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

No path literal appears here, and **the artifact is not opened here either**:
the only file this module opens itself is a first-party asset it reads to inline
(:func:`inline_asset`).  The output path, the template root and the static root
are :mod:`app.utils.paths`' to own, and so is the write: the page is published
through :func:`app.utils.paths.publish_artifact_file`, which holds one verified
directory descriptor across the temporary's creation, the write, the device
sync and the rename, so the directory the artifact lands in is the directory
that was verified rather than a pathname that may have become a symbolic link
or a junction in between (CWE-59/CWE-367).  That module creates directories and
removes nothing but the dot-prefixed scratch its own publication API created --
on this writer's behalf, exactly one entry: the temporary the page is written
into before it is renamed onto the artifact's name.  Emptying the build output
directory is ``app/cli.py``'s ``--clean`` step, so this module deletes nothing.

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
from pathlib import Path
from typing import Any, Final

from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup

# ``normalize_run``, ``selected_features``, ``as_mapping`` and ``as_text`` are
# used below; the rest are re-exported so that a consumer importing them from
# this writer reaches the one implementation rather than a second one.
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
    run_metadata,
)
from app.utils.paths import (
    cucumber_reports_html_path,
    publish_artifact_file,
    static_dir,
    templates_dir,
)

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

#: The metadata sub-objects that carry a name and a version, so a gap in one can
#: be filled from the local probe without discarding the other half.
_VERSIONED_METADATA_KEYS: Final[tuple[str, ...]] = ("implementation", "runtime")

_NAMED_METADATA_KEYS: Final[tuple[str, ...]] = ("os", "cpu")

#: What a metadata probe that yielded nothing contributes.  The same value
#: :func:`app.reporting.events.run_metadata` uses, so the two agree.
_UNKNOWN_METADATA_VALUE: Final[str] = ""


# The metadata block's own spelling for two of the aggregation coercions.
# Aliases rather than wrappers, so "what is a malformed document?" has exactly
# one answer in this module.
_as_mapping = as_mapping

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
    here -- which is also why they are the strings their producer formatted with
    :func:`app.reporting.events.format_timestamp` and are never reformatted:
    the string on this page is the string the JSON artifact carries.

    **Neither timestamp has a clock behind it.**  ``generated_at`` is the
    argument or the document's own value and nothing else, and a document
    carrying neither yields ``""`` -- which
    ``app/templates/artifact/metadata.html`` filters out, so the page simply
    omits the "Report generated" row.  Reading the clock here stated a fact the
    run had not recorded, differed from render to render, and put a generation
    time on this artifact while :func:`app.reporting.pretty_reports
    .format_build_date` -- which has no clock either -- left the other one's
    Date cell empty for the same input.  One generation time per run is
    resolved once, by the collector or by
    :func:`app.services.generate_reports` before it fans the document out to
    the four writers, so every artifact of one run reports the same instant.

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

    # No clock fallback, and its absence is the contract: this writer reads a
    # generation time and never invents one.  The stamp is resolved once per
    # run -- by the collector, or by ``app/services/report_service.py`` before
    # it fans one document out -- so every artifact of one run reports the same
    # instant, and a document carrying none yields "" rather than the moment
    # somebody happened to render the page.
    metadata["generated_at"] = _first_text(
        generated_at,
        document.get("generated_at"),
    )
    metadata["started_at"] = _first_text(
        document.get("started_at"),
        started_at,
    )
    return metadata


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

    The **one** place in the project where anything is declared trusted: the
    stylesheet and the script are first-party text this module inlines, while
    result data -- assertion text, tracebacks, Selenium messages -- is never
    marked anywhere, which keeps autoescaping meaningful everywhere else.

    Args:
        path: The asset to read.  UTF-8, explicitly, so a non-ASCII comment
            or content string survives.
        forbidden: A token that must not occur in the text, compared
            case-insensitively -- :data:`FORBIDDEN_IN_STYLE` for the
            stylesheet, :data:`FORBIDDEN_IN_SCRIPT` for the script, ``None``
            for no check.

    Returns:
        The asset text as :class:`markupsafe.Markup`, interpolated into its
        block unescaped.

    Raises:
        OSError: If the asset cannot be read -- a packaging fault rather than
            a test outcome, so it is not swallowed.
        ValueError: If the asset is blank, or contains ``forbidden``, which
            would close its block early and render the remainder as text.
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


def build_environment() -> Environment:
    """Build the template environment this writer renders through.

    A plain :class:`jinja2.Environment` and not the framework's, because this
    writer runs in a command-line or worker process that never builds an
    application.  Three settings:

    * the loader root is :func:`app.utils.paths.templates_dir`, which is what
      makes the templates' own ``artifact/element.html`` and
      ``partials/status_badge.html`` references resolve;
    * autoescaping is on for every template regardless of extension -- feature
      names carry quotation marks and apostrophes and failure text carries
      tracebacks -- with the two inlined assets the only exempt values, marked
      in :func:`inline_asset`;
    * the trailing newline is **kept**, because the engine strips one by
      default and this artifact ends with a line feed after its closing tag.

    Whitespace control is left off deliberately: the artifact templates manage
    their own whitespace with explicit stripping markers.

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
    those values depend on each other, and nothing is recomputed here -- the
    aggregate is read, the assets are inlined, and the metadata block takes
    the aggregate's start time as the fallback for its own.

    Args:
        result_set: The merged result document, or ``None`` for a run that
            produced nothing.
        generated_at: An explicit generation time; see :func:`build_metadata`.

    Returns:
        A mapping carrying ``features``, ``summary``, ``metadata``,
        ``inline_css`` and ``inline_js`` -- exactly those five, always, since
        the template treats the two assets as required rather than defaulted.
        ``features`` is a fresh list, so a caller that appends to it cannot
        reach the immutable aggregate behind it.

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

    The pure half: it reads the model and the two assets, renders the template
    and **touches no output file**, so a template fault surfaces before
    anything is written.  A zero-feature document renders a complete, valid
    page carrying the template's empty state, because the exit contract writes
    all four artifacts even when the tag expression selected nothing.

    Args:
        result_set: The merged result document, or ``None``.
        environment: An environment to render through; one is built per call
            when omitted.
        generated_at: An explicit generation time, which makes two renders of
            one input comparable byte for byte.

    Returns:
        The complete document, ending with exactly one newline -- guaranteed
        here too, so a caller's own environment cannot end it mid-line.

    Raises:
        OSError: If either inlined asset cannot be read.
        ValueError: If either inlined asset is unusable.
        jinja2.TemplateError: If a template is missing or fails to render --
            the writer-failure exit class, never a test outcome.
    """
    engine = build_environment() if environment is None else environment
    template = engine.get_template(REPORT_TEMPLATE)
    context = build_render_context(result_set, generated_at=generated_at)
    document = template.render(**context)
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
    then resolves a destination and hands the one write to the path authority's
    :func:`app.utils.paths.publish_artifact_file`.

    **The publication is the path authority's, not this module's.**  Every
    owned directory component is created and verified under a held directory
    descriptor, the temporary is created ``O_CREAT|O_EXCL|O_NOFOLLOW`` at
    :data:`~app.utils.paths.ARTIFACT_FILE_MODE` *relative to that descriptor*,
    the stream is flushed and :func:`os.fsync`'ed when the block below
    completes, and the rename is made with ``src_dir_fd``/``dst_dir_fd`` -- so
    the whole sequence runs against the directory that was verified rather than
    against a pathname that may have become a symbolic link or a junction in
    between, which is what a verification released before the creation cannot
    prevent (CWE-59/CWE-367).  A symlinked or junctioned component, a
    destination that is itself a link, and a destination hard-linked to a file
    elsewhere are each refused before any byte is written.

    **Nothing is ever left partially written.**  The bytes go to a temporary in
    the destination's own verified directory -- the same filesystem, because a
    rename is atomic only within one -- and the destination is reached *only*
    by that rename, so a reader, an archiver or the artifact route serving this
    page sees either the previous complete page or this one, never a document
    truncated at the point a write failed.  That is the difference from opening
    the destination itself: ``"w"`` truncates before the first byte exists, and
    a failure anywhere after that destroys an artifact that was complete.  The
    temporary's name is dot-prefixed, so
    :func:`app.utils.paths.resolve_artifact` cannot serve it during the moment
    it exists, and the authority removes it on **every** path out that is not a
    successful rename, the failed rename included.

    **Exactly one file is produced.**  No stylesheet, script, image, font or
    sibling page is written beside it -- that is the whole contract of this
    artifact -- and nothing is deleted or truncated beyond this one file and
    the publication's own temporary, which is the one entry
    :mod:`app.utils.paths` removes on this writer's behalf: it removes no
    artifact and no directory of the tree, and emptying the build output
    directory is ``app/cli.py``'s ``--clean`` step, which runs before the
    suite does.

    Args:
        result_set: The merged result document, or ``None``; the page is
            written either way, per the exit contract.
        base: Directory the artifact path resolves against; cwd by default.
        path: An explicit destination, overriding ``base`` entirely.
        environment, generated_at: As :func:`render_html_report`.

    Returns:
        The path written -- the one file this writer creates or replaces.

    Raises:
        app.utils.paths.ArtifactPathError: The :exc:`OSError` subclass the path
            authority refuses a hostile publication with -- a symlinked or
            junctioned directory component, a linked or hard-linked
            destination, a temporary whose mode cannot be restricted to its
            owner, or, on a platform without descriptor-relative renaming, a
            bound directory that changed while the page was being written.
        OSError: If an asset cannot be read, an owned directory cannot be
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
    destination = Path(
        cucumber_reports_html_path(base) if path is None else path
    )
    # UTF-8 and newline="\n" are the publication's defaults, named here because
    # they are this artifact's contract rather than the helper's convenience:
    # the French validation message "Veuillez renseigner ce champ." and the
    # apostrophes in scenario names have to survive, and a page written on
    # Windows has to be byte-identical to one written on Linux, because the
    # structure of this artifact must not depend on which branch the pipeline's
    # platform test chose.  Everything else about the write -- the verified
    # parent, the exclusive owner-only temporary, the sync, the rename and the
    # removal of a failed attempt -- belongs to the path authority, and any
    # exception from inside this block reaches it as a failed publication:
    # the temporary goes and the destination keeps the last complete page.
    with publish_artifact_file(
        destination, encoding="utf-8", newline="\n"
    ) as stream:
        stream.write(document)
    logger.info("Wrote %s", destination)
    return destination
