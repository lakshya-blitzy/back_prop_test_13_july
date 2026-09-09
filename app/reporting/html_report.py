"""The single self-contained HTML report -- ``target/cucumber-reports.html``.

Source anchor: the reference ``target/cucumber-reports.html``, whose AAP 0.4.1
row reads *"Single self-contained page, per 0.3.4"*.  This is the **first of the
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
    each feature and each element carrying a ``status`` **rolled up here**: the
    templates read a status and never derive one.  Selection is applied here too
    -- see below.
``summary``
    Counts of features, scenarios and steps by status, plus the run's earliest
    scenario start.  Deliberately the same tally, computed by the same rules,
    that ``GET /reports/summary`` answers with.
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

Three obligations the templates cannot enforce and this module discharges:

* the stylesheet text must not contain a closing style tag and the script text
  must not contain a closing script tag, either of which would terminate its
  block early and corrupt the document from that point on;
* the rendered document must end with exactly one trailing newline, which the
  engine would otherwise strip;
* **non-selected scenarios are dropped before rendering.**
  ``artifact/element.html`` states it as a premise -- the selection flag is not
  rendered *because* such scenarios never reach a template -- and the rule is
  the JSON writer's: a scenario the tag expression did not select never started,
  the JVM emitted no test case for it, and a feature left with no test case is
  omitted altogether.  Rendering it would put a scenario on this page that
  ``target/cucumber.json`` does not carry, and the viewer reads that file.

Boundaries
----------
Imports are stdlib, :mod:`jinja2`, :mod:`markupsafe`,
:mod:`app.reporting.events` and :mod:`app.utils.paths`, and nothing else: the
dependency graph's edge runs from the services to the writers and never back, so
no service is imported here, and neither is Flask, Selenium,
:mod:`app.config` nor :mod:`app.web`.  The template environment is a plain
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
here is total.  Only a genuine render or I/O fault propagates, which is the
command's writer-failure exit class -- artifacts written before it remain and
the failing writer is named on stderr.

No merge-conflict marker is ever emitted.  The markers exist only in the
unmodified reference checkout (AAP 0.2.2), and ``.gitattributes`` keeps
``*.html linguist-detectable=false`` precisely because this writer still emits
HTML.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup

from app.reporting.events import (
    ELEMENT_TYPE_BACKGROUND,
    ELEMENT_TYPE_SCENARIO,
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

#: Every status token the templates recognise, quoted from ``status_token`` in
#: ``app/templates/partials/status_badge.html``, which quotes the
#: ``data-tqa-status`` enumeration in ``app/static/css/main.css``.
KNOWN_STATUSES: Final[tuple[str, ...]] = (
    "passed",
    "failed",
    "skipped",
    "pending",
    "undefined",
    "untested",
    "ambiguous",
)

#: What a blank, absent or unrecognised status normalises to.  A status the
#: result model never produced must not be reported as a pass.
UNKNOWN_STATUS: Final[str] = "unknown"

#: Severity order, most severe first.  Nothing in the Java source pins an
#: ordering -- it computes no aggregate status at all -- so this is the
#: conventional Cucumber precedence, and it is the *same tuple* as
#: ``STATUS_PRECEDENCE`` in ``app/templates/view/overview.html``,
#: ``app/reporting/pretty_reports.py`` and ``app/web/routes.py``, because a
#: scenario badge on this page and the same scenario's status in the viewer must
#: not disagree.  Read it as: a failure beats an undefined or ambiguous step,
#: which beat a pending one, which beats a skipped or untested one, which beat a
#: pass -- so one failure is never averaged away by the passes around it.
STATUS_PRECEDENCE: Final[tuple[str, ...]] = (
    "failed",
    "undefined",
    "ambiguous",
    "pending",
    "skipped",
    "untested",
    "passed",
)

#: Reading order for the tally: the reference overview page's own column order
#: first, then the three states it has no column for, then the fallback.  Fixed
#: rather than sorted, so identical input yields an identical tally.
STATUS_READING_ORDER: Final[tuple[str, ...]] = (
    "passed",
    "failed",
    "skipped",
    "pending",
    "undefined",
    "untested",
    "ambiguous",
    UNKNOWN_STATUS,
)

#: The status an element with no steps takes.  Measured rather than chosen:
#: ``EmployeeFc.feature`` declares a Background with an empty body and the
#: reference generator renders each of its step-less occurrences as passed.
EMPTY_ELEMENT_STATUS: Final[str] = "passed"

#: The status an aggregate with nothing at all under it takes -- a feature
#: carrying no element.  Deliberately not a pass: nothing ran.
EMPTY_AGGREGATE_STATUS: Final[str] = UNKNOWN_STATUS

#: The three group names of the tally, in the order ``artifact/metadata.html``
#: presents them.
_SUMMARY_GROUPS: Final[tuple[str, ...]] = ("features", "scenarios", "steps")

#: The key ``artifact/metadata.html`` and ``GET /reports/summary`` both read for
#: the run's earliest scenario start.
_SUMMARY_START_KEY: Final[str] = "start_timestamp"

#: The nested count map's key, which is the shape ``GET /reports/summary``
#: answers with.
_SUMMARY_BY_STATUS_KEY: Final[str] = "by_status"

#: The group total's key.
_SUMMARY_TOTAL_KEY: Final[str] = "total"

#: The metadata sub-objects that carry a name and a version, so a gap in one can
#: be filled from the local probe without discarding the other half.
_VERSIONED_METADATA_KEYS: Final[tuple[str, ...]] = ("implementation", "runtime")

#: The metadata sub-objects that carry a name alone.
_NAMED_METADATA_KEYS: Final[tuple[str, ...]] = ("os", "cpu")

#: What a metadata probe that yielded nothing contributes.  The same value
#: :func:`app.reporting.events.run_metadata` uses, so the two agree.
_UNKNOWN_METADATA_VALUE: Final[str] = ""

#: The key on a feature and an element that this module fills in.
_STATUS_KEY: Final[str] = "status"

#: The key on an element that records whether the tag expression selected it.
_SELECTED_KEY: Final[str] = "selected"


# --------------------------------------------------------------------------- #
# Total coercions.  Every read of the result set goes through one of these, so
# a malformed document produces a poorer page rather than an exception: a test
# outcome, and the shape of the document that records it, must never fail a
# run.
# --------------------------------------------------------------------------- #


def _as_mapping(value: Any) -> JsonDict:
    """Return ``value`` when it is a mapping, otherwise an empty mapping.

    Args:
        value: Anything at all, including ``None`` and a key that was never
            there.

    Returns:
        The mapping, or ``{}``.  Never raises.
    """
    return value if isinstance(value, dict) else {}


def _mappings(value: Any) -> list[JsonDict]:
    """Return the mapping members of ``value``, in order.

    A string and a mapping are rejected outright rather than iterated: both are
    iterable and iterating either yields nonsense -- characters in one case, key
    names in the other.

    Args:
        value: A candidate sequence, or anything at all.

    Returns:
        The members that are mappings, in input order.  Never raises.
    """
    if not isinstance(value, (list, tuple)):
        return []
    return [member for member in value if isinstance(member, dict)]


def _as_text(value: Any) -> str:
    """Return ``value`` as trimmed text, or ``""``.

    Only a string contributes: a number, a container or ``None`` arriving where
    text belongs is a malformed document, and surfacing it as the characters
    ``None`` on a report page would be worse than surfacing nothing.

    Args:
        value: Anything at all.

    Returns:
        The trimmed string, or ``""``.  Never raises.
    """
    return value.strip() if isinstance(value, str) else ""


def _is_background(element: JsonDict) -> bool:
    """Report whether ``element`` is a Background occurrence.

    The type is the model's own discriminator, and the keyword answers for a
    hand-built document that carries no type.  An unrecognised value reads as a
    test case, which is what :func:`app.reporting.events.new_element` does with
    one and what keeps its results from being lost.

    Args:
        element: A Background or scenario element.

    Returns:
        ``True`` only for a Background occurrence.
    """
    declared = _as_text(element.get("type")).lower()
    if declared:
        return declared == ELEMENT_TYPE_BACKGROUND
    return _as_text(element.get("keyword")).lower() == ELEMENT_TYPE_BACKGROUND


def _is_scenario_element(element: JsonDict) -> bool:
    """Report whether ``element`` is counted as a scenario by the tally.

    Strictly ``type == "scenario"``, which is the rule ``GET /reports/summary``
    applies, so the two cannot count the run differently.  Note the deliberate
    asymmetry with :func:`_is_background`: an element of an unexpected type is
    *rendered* as a scenario, because losing its results would be worse, but it
    is not *counted* as one, because an unexpected type is not evidence that a
    test case ran.

    Args:
        element: A Background or scenario element.

    Returns:
        ``True`` only for an element whose declared type is ``"scenario"``.
    """
    return _as_text(element.get("type")).lower() == ELEMENT_TYPE_SCENARIO


def _is_selected(element: JsonDict) -> bool:
    """Report whether the tag expression selected ``element``.

    Absent means selected: a hand-built document that omits the flag means
    "this ran", and over-reporting a scenario is far less harmful than dropping
    one that executed.  Only an explicit ``False`` is a decision.

    Args:
        element: A Background or scenario element.

    Returns:
        ``False`` only when the element carries ``"selected": False``.
    """
    return element.get(_SELECTED_KEY, True) is not False


# --------------------------------------------------------------------------- #
# Status: normalisation, and the roll-up the templates decline to compute
# --------------------------------------------------------------------------- #


def status_token(status: Any) -> str:
    """Normalise one status exactly as ``status_token`` does in the partial.

    ``app/templates/partials/status_badge.html`` is the project's single
    normalisation point and this is its Python twin.  The rule: coerce to text,
    trim, fold to lower case, and answer with that token when it is one of
    :data:`KNOWN_STATUSES` -- otherwise :data:`UNKNOWN_STATUS`.  Normalising and
    rolling up are deliberately separate jobs: this function decides how a
    status is *spelled*, :func:`roll_up_status` decides *which* status an
    aggregate has.

    Args:
        status: A raw ``result.status``, or anything at all: a number, ``None``,
            a container, or a key that was never there.

    Returns:
        One of :data:`KNOWN_STATUSES`, or :data:`UNKNOWN_STATUS`.  Never raises.

    Examples:
        >>> status_token("Passed")
        'passed'
        >>> status_token(None)
        'unknown'
        >>> status_token("executing")
        'unknown'
    """
    candidate = _as_text(status).lower()
    return candidate if candidate in KNOWN_STATUSES else UNKNOWN_STATUS


def roll_up_status(
    statuses: Iterable[Any],
    empty: str = EMPTY_ELEMENT_STATUS,
) -> str:
    """Fold a collection of statuses into the one that describes them all.

    The ordering is :data:`STATUS_PRECEDENCE`, most severe first::

        failed > undefined > ambiguous > pending > skipped > untested > passed

    and it is documented here because **nothing in the Java source pins it**:
    the Java step classes compute no aggregate status, so the precedence is the
    conventional Cucumber one, and it is shared verbatim with
    ``app/templates/view/overview.html``, :mod:`app.reporting.pretty_reports`
    and ``app/web/routes.py`` so that no two surfaces can grade the same run
    differently.  The fold is over a *set*, and a maximum over a total order is
    associative, which is why rolling steps up to an element and elements up to
    a feature gives the same answer as rolling every step of the feature up at
    once.

    Args:
        statuses: Raw or already-normalised statuses, in any order.  Each is put
            through :func:`status_token` first, so a mixed collection is fine.
        empty: The answer for a collection that is empty, or that holds nothing
            the precedence names.  The element-level default is
            :data:`EMPTY_ELEMENT_STATUS`, which is measured rather than chosen:
            ``EmployeeFc.feature`` declares a Background with an empty body and
            the reference generator renders each step-less occurrence as passed.
            A caller asking a run-level question -- "what is a feature with no
            elements at all?" -- passes :data:`EMPTY_AGGREGATE_STATUS`.

    Returns:
        The most severe status present, or ``empty``.  Never raises.

    Examples:
        >>> roll_up_status(["passed", "skipped", "failed"])
        'failed'
        >>> roll_up_status(["passed", "undefined", "pending"])
        'undefined'
        >>> roll_up_status([])
        'passed'
        >>> roll_up_status([], empty=UNKNOWN_STATUS)
        'unknown'
        >>> roll_up_status(["executing"], empty=UNKNOWN_STATUS)
        'unknown'
    """
    present = {status_token(status) for status in statuses}
    for candidate in STATUS_PRECEDENCE:
        if candidate in present:
            return candidate
    return empty


def _step_statuses(element: JsonDict) -> list[str]:
    """Return the normalised status of every step of ``element``, in order.

    Args:
        element: A Background or scenario element.

    Returns:
        One token per step.  A step whose result carries no status at all -- a
        step the run never reached -- contributes :data:`UNKNOWN_STATUS` rather
        than being dropped, so it cannot be silently read as a pass.
    """
    return [
        status_token(_as_mapping(step.get("result")).get("status"))
        for step in _mappings(element.get("steps"))
    ]


def _fold_step_statuses(tokens: Sequence[str]) -> str:
    """Fold step tokens, keeping the two empty cases apart.

    The distinction matters and a single fallback cannot express it:

    * **no steps at all** is :data:`EMPTY_ELEMENT_STATUS`, the measured
      behaviour of ``EmployeeFc.feature``'s empty Background;
    * **steps whose statuses are all unrecognised** is
      :data:`UNKNOWN_STATUS`, because a status the result model never produced
      must not be reported as a pass -- a step that arrived without a status is
      a step whose outcome nobody knows.

    Args:
        tokens: Normalised step tokens, in any order.

    Returns:
        The most severe status present, or the appropriate empty answer.
    """
    if not tokens:
        return EMPTY_ELEMENT_STATUS
    return roll_up_status(tokens, empty=UNKNOWN_STATUS)


def element_status(element: JsonDict) -> str:
    """Return the rolled-up status of one element, from its own steps alone.

    A scenario is not coloured by its neighbours and a Background occurrence is
    not coloured by the scenario that follows it: each element's badge answers
    for that element.

    Args:
        element: A Background or scenario element.

    Returns:
        The most severe status among its steps; :data:`EMPTY_ELEMENT_STATUS`
        for an element with no steps; :data:`UNKNOWN_STATUS` when it has steps
        but none of them carries a status the model recognises.

    Examples:
        >>> element_status({"steps": [{"result": {"status": "passed"}},
        ...                           {"result": {"status": "failed"}}]})
        'failed'
        >>> element_status({"steps": []})
        'passed'
        >>> element_status({"steps": [{"result": {}}]})
        'unknown'
    """
    return _fold_step_statuses(_step_statuses(element))


def feature_status(feature: JsonDict) -> str:
    """Return the rolled-up status of one feature, from its elements.

    The fold is over **every** element, Background occurrences included, which
    is the same rule ``GET /reports/summary`` applies: a Background failure
    moves its feature's status without moving any scenario's, because the
    failure is real and belongs to that feature.

    Args:
        feature: A feature mapping.

    Returns:
        The most severe status among its elements, or
        :data:`EMPTY_AGGREGATE_STATUS` for a feature carrying no element -- a
        feature with nothing under it did not pass, it did not run.

    Examples:
        >>> feature_status({"elements": [
        ...     {"steps": [{"result": {"status": "passed"}}]},
        ...     {"steps": [{"result": {"status": "skipped"}}]}]})
        'skipped'
        >>> feature_status({"elements": []})
        'unknown'
    """
    elements = _mappings(feature.get("elements"))
    if not elements:
        return EMPTY_AGGREGATE_STATUS
    return roll_up_status(
        (element_status(element) for element in elements),
        empty=UNKNOWN_STATUS,
    )



# --------------------------------------------------------------------------- #
# Selection.  A scenario the tag expression did not select never started, so
# the JVM emitted no test case for it and it is absent from the JSON report;
# ``artifact/element.html`` states as a premise that such scenarios never reach
# a template.  The grouping below is local to this writer rather than borrowed,
# because the *rule* it serves is this writer's: the merge in
# :mod:`app.reporting.events` groups for a different purpose, ordering.
# --------------------------------------------------------------------------- #


def _element_units(elements: Sequence[JsonDict]) -> list[list[JsonDict]]:
    """Group elements into Background-occurrence-plus-scenario units.

    The Background occurrence emitted for a scenario belongs immediately in
    front of it and shares its fate: if the scenario is dropped, its Background
    occurrence goes with it, or the page would show a background for a test case
    it does not show.  Grouping first is what makes that exact.

    Args:
        elements: One feature's elements, in document order.

    Returns:
        The units, in input order: ``[background, scenario]`` normally,
        ``[scenario]`` for a feature with no Background, and ``[background]``
        for the pathological trailing occurrence with no scenario, which is kept
        as a unit of its own rather than attached to something it did not
        precede.
    """
    units: list[list[JsonDict]] = []
    for element in elements:
        if _is_background(element):
            units.append([element])
            continue
        if units and len(units[-1]) == 1 and _is_background(units[-1][0]):
            units[-1].append(element)
        else:
            units.append([element])
    return units


def emitted_features(result_set: ResultSet | None) -> list[JsonDict]:
    """Return the features this page renders, in source order.

    Two rules, both the JSON writer's, so that this page and
    ``target/cucumber.json`` describe the same run:

    * a unit whose scenario -- or whose Background occurrence -- carries an
      explicit ``"selected": False`` is dropped whole;
    * a feature left with no test case is dropped altogether, which includes a
      feature left with nothing but Background occurrences: an occurrence is
      emitted *for* a test case, so one without its scenario represents none.
      Under the default ``@Smoke`` filter that is what reduces the suite's ten
      features to the one the reference artifact carries, and it is what makes
      the template's empty state reachable rather than decorative.

    Nothing is sorted and nothing is mutated: a feature whose elements survive
    unchanged is passed through as it stands, and one that loses a unit is
    **copied** with a new element list, so the caller's document is untouched.

    Args:
        result_set: The merged result document, or ``None`` for a run that
            produced nothing -- all four artifacts are still written in that
            case, so ``None`` yields an empty list rather than an error.

    Returns:
        The feature mappings to render, in document order.  Never raises.
    """
    document = _as_mapping(result_set)
    kept: list[JsonDict] = []
    for feature in _mappings(document.get("features")):
        elements = _mappings(feature.get("elements"))
        units = [
            unit
            for unit in _element_units(elements)
            if all(_is_selected(element) for element in unit)
        ]
        surviving = [element for unit in units for element in unit]
        if not any(not _is_background(element) for element in surviving):
            # No test case survived, so the JVM would have created no feature
            # map at all.  Dropped rather than rendered as a row of zeros.
            continue
        if len(surviving) == len(elements):
            kept.append(feature)
        else:
            kept.append({**feature, "elements": surviving})
    return kept


def decorated_features(features: Sequence[JsonDict]) -> list[JsonDict]:
    """Return ``features`` with a rolled-up ``status`` on every level.

    The templates read a status and never derive one -- ``artifact/feature.html``
    and ``artifact/element.html`` both say so -- so this is where a status
    arrives.  Each element gets the status of its own steps and each feature the
    status of its elements, and the shallow copies mean the caller's document
    keeps whatever it had: this writer is one of four fed from a single merged
    result set, and a writer that edited that set in place would change what the
    others see.

    Args:
        features: The feature mappings to decorate, already selection-filtered
            by :func:`emitted_features`.

    Returns:
        A new list of new feature mappings, in input order, each carrying a new
        element list of new element mappings.  Order is preserved throughout:
        features in source order, elements exactly where the model puts them,
        each Background occurrence repeated in its own position.  Never raises.
    """
    decorated: list[JsonDict] = []
    for feature in features:
        elements = [
            {**element, _STATUS_KEY: element_status(element)}
            for element in _mappings(feature.get("elements"))
        ]
        # Rolled up from the decorated copies, so the feature's badge is
        # exactly the fold of the badges shown beneath it.
        rolled = (
            roll_up_status(
                (element[_STATUS_KEY] for element in elements),
                empty=UNKNOWN_STATUS,
            )
            if elements
            else EMPTY_AGGREGATE_STATUS
        )
        decorated.append(
            {**feature, "elements": elements, _STATUS_KEY: rolled}
        )
    return decorated


# --------------------------------------------------------------------------- #
# The tally.  Deliberately the same counting rules ``GET /reports/summary``
# applies, so the artifact and the viewer cannot disagree about one run.
# --------------------------------------------------------------------------- #


def _parse_start(value: str) -> datetime | None:
    """Parse one ``start_timestamp``, or answer ``None``.

    The values :func:`app.reporting.events.format_timestamp` emits are
    millisecond-precision UTC ISO-8601 ending in a literal ``Z``.  That suffix
    is rewritten to an explicit offset before parsing, and a value that parses
    without one is read as UTC, so every instant returned here is aware and any
    two of them compare without raising.

    Args:
        value: A non-empty timestamp string.

    Returns:
        The instant, or ``None`` when the string is not a timestamp at all.
    """
    text = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def earliest_start(features: Sequence[JsonDict]) -> str | None:
    """Return the run's earliest scenario start, as the model spells it.

    Scenario elements only: a Background occurrence carries no
    ``start_timestamp`` at all.  Selection is by parsed instant and the string
    is returned **verbatim** rather than reformatted, so the value on this page
    is the value the JSON artifact carries; the string breaks a tie between two
    identical instants, so the answer does not depend on the order the workers
    merged in.

    Args:
        features: The feature mappings to scan.

    Returns:
        The earliest usable timestamp string, or ``None`` when no scenario
        carries one and when none of the values present can be parsed -- a
        malformed value is dropped rather than reported as the run's start.
        Never raises.
    """
    parsed: list[tuple[datetime, str]] = []
    for feature in features:
        for element in _mappings(feature.get("elements")):
            if not _is_scenario_element(element):
                continue
            candidate = _as_text(element.get("start_timestamp"))
            if not candidate:
                continue
            moment = _parse_start(candidate)
            if moment is not None:
                parsed.append((moment, candidate))
    if not parsed:
        return None
    return min(parsed, key=lambda pair: (pair[0], pair[1]))[1]


def _count_group(tokens: Sequence[str]) -> JsonDict:
    """Build one group of the tally, in both shapes its two readers need.

    ``GET /reports/summary`` answers with ``{"total": ..., "by_status": {...}}``
    and ``app/templates/artifact/metadata.html`` reads a **flat** mapping,
    looking each status up on the group itself.  Emitting both from one count
    is what lets the artifact state per-status figures while remaining exactly
    the body the route returns; the nested map is a mapping rather than a
    number, so the template's own "anything else the writer counted" loop
    renders nothing for it.

    Args:
        tokens: One normalised status token per counted thing.

    Returns:
        The total, always present and ``0`` included; the nested map, carrying
        only the non-zero statuses in :data:`STATUS_READING_ORDER`; and each of
        those same statuses flattened onto the group in that same order, so the
        mapping is deterministic for identical input.
    """
    by_status: dict[str, int] = {}
    for token in STATUS_READING_ORDER:
        counted = tokens.count(token)
        if counted:
            by_status[token] = counted
    group: JsonDict = {
        _SUMMARY_TOTAL_KEY: len(tokens),
        _SUMMARY_BY_STATUS_KEY: by_status,
    }
    group.update(by_status)
    return group


def build_summary(features: Sequence[JsonDict]) -> JsonDict:
    """Count the run: features, scenarios and steps by status, and its start.

    One pass, three tallies, and the rules are ``GET /reports/summary``'s:

    * **Steps** -- every step of every element, Background occurrences
      included, because an occurrence genuinely runs once per scenario.
    * **Scenarios** -- elements typed ``scenario`` and never the element count,
      since backgrounds interleave and repeat.  A scenario's status is the worst
      among its **own** steps, so a background failure is not reported as a
      scenario failure.
    * **Features** -- the worst status among that feature's elements,
      Background occurrences included: a background failure moves its feature's
      status without moving any scenario's.  Folding steps into elements and
      elements into a feature is the *same* maximum as folding every step of
      the feature at once, because a maximum over a total order is associative
      -- so this figure is the route's figure, and it is also, by construction,
      the fold of the badges this page shows.

    The step-less element is the one place this tally and the route can differ,
    and the difference is deliberate: an element with no steps counts as passed
    here, because that is what the reference generator renders for
    ``EmployeeFc.feature``'s empty Background, and counting it any other way
    would put a badge on this page that the tally beside it contradicts.  A
    feature carrying no element at all counts as :data:`UNKNOWN_STATUS` in both.
    For every input carrying at least one step -- which is every real run --
    the two agree exactly.

    Args:
        features: The feature mappings this page renders -- already
            selection-filtered, so the tally counts what the page shows and what
            the JSON artifact carries, and not the scenarios neither holds.
            Decoration is irrelevant here: every figure is recomputed from the
            steps, so a hand-built feature list without a ``status`` key counts
            identically.

    Returns:
        A mapping carrying ``features``, ``scenarios`` and ``steps`` -- each a
        group from :func:`_count_group` -- and ``start_timestamp``.  An empty
        feature list answers three zero totals, three empty maps and ``None``.
        Never raises.
    """
    feature_tokens: list[str] = []
    scenario_tokens: list[str] = []
    step_tokens: list[str] = []

    for feature in features:
        elements = _mappings(feature.get("elements"))
        element_tokens: list[str] = []
        for element in elements:
            own_steps = _step_statuses(element)
            step_tokens.extend(own_steps)
            # One fold per element, reused for that element's own tally entry
            # and for its feature's, which is what makes the figures on this
            # page and the badges above them the same reading of one list.
            token = _fold_step_statuses(own_steps)
            element_tokens.append(token)
            if _is_scenario_element(element):
                scenario_tokens.append(token)
        feature_tokens.append(
            roll_up_status(element_tokens, empty=UNKNOWN_STATUS)
            if elements
            else EMPTY_AGGREGATE_STATUS
        )

    counted = (feature_tokens, scenario_tokens, step_tokens)
    summary: JsonDict = {
        name: _count_group(tokens)
        for name, tokens in zip(_SUMMARY_GROUPS, counted, strict=True)
    }
    summary[_SUMMARY_START_KEY] = earliest_start(features)
    return summary



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

    The order of work is the order the values depend on each other: select,
    decorate, tally, then describe the run with the tally's start time as the
    fallback for its own.

    Args:
        result_set: The merged result document, or ``None`` for a run that
            produced nothing.
        generated_at: An explicit generation time; see :func:`build_metadata`.

    Returns:
        A mapping carrying ``features``, ``summary``, ``metadata``,
        ``inline_css`` and ``inline_js`` -- all five, always, because the
        template treats the two assets as required rather than defaulted.

    Raises:
        OSError: If either inlined asset cannot be read.
        ValueError: If either inlined asset is empty or would close its block
            early; see :func:`inline_asset`.
    """
    features = decorated_features(emitted_features(result_set))
    summary = build_summary(features)
    metadata = build_metadata(
        result_set,
        generated_at=generated_at,
        started_at=summary.get(_SUMMARY_START_KEY),
    )
    return {
        "features": features,
        "summary": summary,
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

    The impure half, and deliberately thin: it renders first, so a template or
    asset fault cannot leave a half-written page, then resolves a destination,
    creates its parent and writes the text.

    **Exactly one file is produced.**  No stylesheet, script, image, font or
    sibling page is written beside it -- that is the whole contract of this
    artifact -- and nothing is deleted or truncated beyond this one file:
    :mod:`app.utils.paths` creates directories and never removes them, and
    emptying the build output directory is ``app/cli.py``'s ``--clean`` step,
    which runs before the suite does.

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
            created, or the file cannot be written.  Deliberately **not**
            swallowed: producing this artifact is the writer's contract with the
            exit table, whose writer-failure class requires the failing writer
            to be named on stderr while the artifacts written before it remain.
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
    # UTF-8 explicitly, so the French validation message
    # "Veuillez renseigner ce champ." and the apostrophes in scenario names
    # survive; newline="\n" so a page written on Windows is byte-identical to
    # one written on Linux, because the structure of this artifact must not
    # depend on which branch the pipeline's platform test chose.
    with open(destination, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(document)
    logger.info("Wrote %s", destination)
    return destination

