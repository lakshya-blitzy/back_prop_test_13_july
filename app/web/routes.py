"""The six view functions of the read-only artifact viewer.

This module is the whole HTTP surface of the port, and it is deliberately the
whole of it: specification section 0.3.1 states a six-row route table and calls
it *"the complete contract"*.  Those six rows, and no seventh:

======================================================  ==================
Rule                                                    Endpoint
======================================================  ==================
``GET /``                                               ``web.index``
``GET /reports``                                        ``web.reports_overview``
``GET /reports/features/<int:findex>``                  ``web.report_feature``
``GET /reports/features/<int:findex>/scenarios/<int:sindex>``
                                                        ``web.report_scenario``
``GET /reports/summary``                                ``web.reports_summary``
``GET /artifacts/<path:name>``                          ``web.artifact``
======================================================  ==================

The endpoint names are a contract rather than a preference.  ``base.html``,
the three view templates and ``index.html`` build every URL with ``url_for``
against exactly those names, and ``app/web/__init__.py`` fixes the blueprint
name ``"web"`` for the same reason, so a renamed view function would break six
templates at render time.  The view functions are therefore named after their
endpoints and no explicit ``endpoint=`` argument is passed anywhere.

Provenance
----------
There is no Java counterpart.  The implementation this project ports exposes no
HTTP surface at all - its build manifest declares no servlet, framework or web
container - so specification section 0.4.1 maps this package as *"No source:
the Java project has no HTTP surface"*.  The whole package is deviation 12 of
that specification's inventory, authorized by its Conflict 3: the request
mandates a Flask application while the specification states the system has no
traditional application UI, and the conflict resolves by holding Flask to *"the
minimum it compels - a read-only viewer over the artifacts a run already
produces"*.  Nothing here is preserved behaviour, and nothing here goes beyond
that minimum: there is no endpoint that starts a run, no write of any kind, and
no route the specification's table does not list.

Read-only, and structurally so
------------------------------
Every route is synchronous, and every route only reads.  This module opens no
file for writing, creates no directory, spawns no process and imports no
service, no report writer, no page object and no browser-automation module, so
"no route can start a run" is a property of the import graph rather than a
convention someone has to remember.  Specification section 0.4.2 gives this
package exactly one outward edge - ``WEB --> UT``, to ``app/utils`` - and the
imports below are that edge and the standard library.

Paths, and why none is spelled here
-----------------------------------
``app/utils/paths.py`` owns every filesystem path in the port; section 0.4.2
states that *"no other Python module contains a path literal"*.  So the artifact
names on the landing page, the results file every report route reads, the
allowlist the artifact route enforces and the report tree's overview page all
arrive from that module.  The only fixed strings below that look like paths are
Jinja loader names passed to ``render_template``: those are template
identifiers resolved by the application's Jinja environment, not locations on
disk.

One response for every unusable-results cause
---------------------------------------------
Section 0.3.1 states the data-availability rule once, for all four report
routes: when the results artifact a run writes is absent, unreadable or
unparseable, the route returns 404 - *"the same response for all three causes,
because a run has not produced usable results and the distinction is not the
viewer's to make."*  :func:`_load_features` is that rule, in one place, and a
document that parses but is not a list joins the same three causes, because
section 0.6 pins the top level of that artifact as a list of feature objects.

Two consequences are easy to get backwards, so both are stated:

* An **empty list is a success**.  A run whose tag expression selected no
  scenario still writes all four artifacts, so ``[]`` renders at 200 with every
  tally at zero.  Only a missing or corrupt artifact is a 404.
* ``GET /`` **has no data-availability precondition at all** and answers 200
  always, including on a checkout where ``target/`` has never existed.  That
  directory is generated output; the landing page has to be useful before
  anything has ever run.

Every failure is signalled with a bare ``abort(404)`` and this module returns no
error body of its own.  ``app/errors.py`` renders all of them, negotiates JSON
for the summary route and keeps the response byte-identical across the causes -
guarantees that only hold while nothing here adds a description, a reason or a
status of its own.  That module is not imported here; the factory wires both
sides and the two never reference each other.  Rejections are logged, because
the rejecting route is the only code that knows which rule refused a request,
and a log record is the one place a cause may appear.

Counting, and the single definition of it
-----------------------------------------
``GET /reports/summary`` and ``view/overview.html`` are two presentations of one
calculation, so a reader comparing them can never be shown two different
answers.  That template is the declared owner of the rules and this module
mirrors them name for name:

1. **Steps** - every step of every element is counted, **backgrounds
   included**, because a repeated background genuinely ran once per scenario.
   Each status is normalised exactly as ``partials/status_badge.html`` does, the
   project's single normalisation point.
2. **Scenario status** - the worst status among that scenario's **own** steps,
   by the precedence in :data:`_STATUS_PRECEDENCE`.  A background's steps never
   contribute, so a background failure is not reported as a scenario failure.
   A scenario with no steps is ``unknown``.
3. **Feature status** - the same precedence applied across the steps of **all**
   that feature's elements, backgrounds included.  This is the template's rule
   and it is not the worst of the feature's scenario statuses: a background-only
   failure moves the feature's status without moving any scenario's.
4. **Scenarios** - elements whose ``type`` is ``"scenario"`` and never the
   element count, because backgrounds interleave and repeat.
5. **Earliest start** - the lowest ``start_timestamp`` among scenario elements.

Only counts and that one timestamp are computed here.  The three HTML report
views receive the raw mappings and derive their own aggregates, so nothing is
pre-computed twice and no view can drift from the artifact it describes.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, NamedTuple

from flask import Response, abort, jsonify, render_template, send_file

from app.utils import (
    ARTIFACT_SPECS,
    CUCUMBER_JSON_NAME,
    CUCUMBER_REPORTS_HTML_NAME,
    PRETTY_REPORTS_DIR_NAME,
    RERUN_TXT_NAME,
    ArtifactSpec,
    artifact_path,
    cucumber_json_path,
    pretty_reports_index_path,
    resolve_artifact,
    target_root,
    workers_dir,
)
from app.web import web_bp

#: The six view functions, in the order a linter's natural sort puts them.  The
#: routes themselves are the module's real surface; these names are exported so
#: that the surface is greppable and a test can address a view directly.
__all__ = [
    "artifact",
    "index",
    "report_feature",
    "report_scenario",
    "reports_overview",
    "reports_summary",
]

#: Module logger, acquired from the standard library exactly as every other
#: module in the port does.  ``app/logging_config.py`` installs the handler
#: split for the whole ``app`` hierarchy and is deliberately not imported: it is
#: called by the two process entry points alone, and its effect reaches this
#: module through the logger hierarchy without any import at all.
logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Jinja loader names.  Not filesystem paths: the application's Jinja
# environment resolves them against the package template directory that
# app/utils/paths.py locates.
# --------------------------------------------------------------------------- #

_INDEX_TEMPLATE: Final[str] = "index.html"
_OVERVIEW_TEMPLATE: Final[str] = "view/overview.html"
_FEATURE_TEMPLATE: Final[str] = "view/feature.html"
_SCENARIO_TEMPLATE: Final[str] = "view/scenario.html"

# --------------------------------------------------------------------------- #
# The result model's vocabulary, quoted from the templates that own it.
# --------------------------------------------------------------------------- #

#: The two element kinds ``elements`` interleaves.  Held as constants here
#: rather than imported from ``app/reporting/events.py``, which declares the
#: same two strings: that package is on the far side of this package's import
#: boundary (specification section 0.4.2), and a viewer that imported a report
#: writer to read two words would trade the boundary for nothing.
_SCENARIO_ELEMENT_TYPE: Final[str] = "scenario"
_BACKGROUND_ELEMENT_TYPE: Final[str] = "background"

#: The seven statuses the result model produces, quoted from ``status_token``
#: in ``app/templates/partials/status_badge.html``.
_KNOWN_STATUS_TOKENS: Final[frozenset[str]] = frozenset(
    {
        "passed",
        "failed",
        "skipped",
        "pending",
        "undefined",
        "untested",
        "ambiguous",
    }
)

#: What anything else normalises to.  The presentation vocabulary folds an
#: unrecognised status to ``unknown`` rather than to a Cucumber status, because
#: a page - or a count - must not claim a status it never read.  The JSON writer
#: makes the opposite choice for the *data* status, and deliberately: the
#: Jenkins publisher parses those names.
_UNKNOWN_STATUS: Final[str] = "unknown"

#: Severity order, highest first, quoted from ``STATUS_PRECEDENCE`` in
#: ``app/templates/view/overview.html``.  The first member of this sequence
#: that occurs among a set of step statuses is the status of the scenario or
#: feature those steps belong to, so one failure is never averaged away by the
#: passes around it.
_STATUS_PRECEDENCE: Final[tuple[str, ...]] = (
    "failed",
    "undefined",
    "ambiguous",
    "pending",
    "skipped",
    "untested",
    "passed",
)

#: Reading order for a ``by_status`` map, quoted from ``STATUS_READING_ORDER``
#: in the same template: the reference overview page's own column order first,
#: then the three states that page has no column for, then the fallback.  Every
#: token :func:`_status_token` can answer with appears exactly once, so no count
#: can be silently dropped from a summary.
_STATUS_READING_ORDER: Final[tuple[str, ...]] = (
    "passed",
    "failed",
    "skipped",
    "pending",
    "undefined",
    "untested",
    "ambiguous",
    _UNKNOWN_STATUS,
)

#: Human-readable names for the four artifacts, paired with the keys
#: :data:`ARTIFACT_SPECS` carries.  The specs model an artifact's key, relative
#: path and directory-ness - the facts the path module owns - and carry no
#: label, so the display text is supplied here, keyed by those constants rather
#: than by a name typed out again.  The wording is ``index.html``'s own.
_ARTIFACT_LABELS: Final[tuple[tuple[str, str], ...]] = (
    (CUCUMBER_REPORTS_HTML_NAME, "Cucumber HTML report"),
    (CUCUMBER_JSON_NAME, "Cucumber JSON"),
    (RERUN_TXT_NAME, "Rerun manifest"),
    (PRETTY_REPORTS_DIR_NAME, "PrettyReports tree"),
)


class ArtifactView(NamedTuple):
    """One row of the landing page's artifact list.

    ``index.html`` addresses these six members by name and reads the last three
    through ``default()``, so a descriptor is complete rather than partial.  A
    named tuple rather than a mapping: the members are fixed, they cannot be
    mutated between construction and render, and a misspelling fails loudly
    here instead of rendering as an empty string in the page.

    Attributes:
        label: Human-readable name, from :data:`_ARTIFACT_LABELS`.
        name: The artifact key, which is :attr:`ArtifactSpec.key` and doubles
            as the allowlisted name ``web.artifact`` accepts, so the link the
            page builds round-trips through that route.
        relpath: :attr:`ArtifactSpec.relpath`, for display only.
        exists: Whether the artifact is on disk now - a directory for the
            report tree, a file for the other three.
        modified_iso: Millisecond-precision UTC ISO-8601 ending in a literal
            ``Z``, the shape the artifacts' own timestamps carry, or ``None``.
        modified_display: The same instant, human-readable, or ``None``.

    """

    label: str
    name: str
    relpath: str
    exists: bool
    modified_iso: str | None
    modified_display: str | None


# --------------------------------------------------------------------------- #
# Timestamps.  Both forms are derived from one aware UTC datetime, so the
# machine-readable attribute and the text beside it can never disagree.
# --------------------------------------------------------------------------- #


def _iso_millis_z(moment: datetime) -> str:
    """Format an aware UTC instant the way the artifacts format theirs.

    Millisecond precision with a literal ``Z``, e.g. ``2022-09-07T13:37:26.297Z``
    - the shape ``start_timestamp`` carries in the results file, which is why
    the views render both through the same time element.  The suffix is
    substituted rather than sliced off, so a value that is not UTC would keep
    its offset and be visibly wrong instead of silently mislabelled.

    Args:
        moment: An aware datetime, expected in UTC.

    Returns:
        ISO-8601 to milliseconds, ending in ``Z`` for a UTC instant.

    """
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _display_utc(moment: datetime) -> str:
    """Format the same instant for a reader.

    ``07 Sep 2022, 15:39 UTC`` - ``index.html``'s documented form, which is how
    the generated report tree renders a build date, with the zone named because
    the value is not local time.

    Args:
        moment: An aware datetime, expected in UTC.

    Returns:
        The human-readable form.

    """
    return f"{moment:%d %b %Y, %H:%M} UTC"


def _modification_times(path: Path) -> tuple[str | None, str | None]:
    """Both timestamp forms for one path, or two ``None`` values.

    A modification time is decoration on every page that shows one, so no
    failure to read one may turn a working page into an error: an artifact that
    vanished between the presence check and the ``stat``, a permission that
    denies the ``stat``, and a clock value the platform cannot represent all
    answer the same "unavailable", which ``index.html`` and the report views
    each render in words.

    Args:
        path: The artifact to stat.  Nothing is created and nothing is written.

    Returns:
        ``(iso, display)``, or ``(None, None)`` if the time cannot be read.

    """
    try:
        moment = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    except (OSError, ValueError, OverflowError):
        # ValueError and OverflowError cover a stored timestamp outside the
        # range datetime can represent, which a corrupt filesystem can produce.
        logger.debug("Modification time unavailable for an artifact")
        return None, None
    return _iso_millis_z(moment), _display_utc(moment)


# --------------------------------------------------------------------------- #
# Presence, and the results file every report route reads.
# --------------------------------------------------------------------------- #


def _artifact_label(spec: ArtifactSpec) -> str:
    """The display name for one artifact spec.

    Args:
        spec: The artifact whose label is wanted.

    Returns:
        The label paired with the spec's key in :data:`_ARTIFACT_LABELS`, or the
        key itself if the specs ever grow a fifth artifact this module has no
        wording for - a new artifact is then listed unlabelled rather than
        omitted from the page or crashing it.

    """
    for key, label in _ARTIFACT_LABELS:
        if key == spec.key:
            return label
    return spec.key


def _artifact_exists(path: Path, *, is_dir: bool) -> bool:
    """Whether one artifact is on disk now.

    Args:
        path: The location to test.
        is_dir: ``True`` for the report tree, whose presence means a
            *directory*; ``False`` for the three single-file artifacts, where a
            directory in place of the file is an absence rather than a
            presence.

    Returns:
        ``True`` only for a present artifact of the expected kind.  An
        ``OSError`` - a denied traversal, a name too long for the platform, a
        broken symlink chain - is an absence, because the landing page answers
        200 unconditionally and has nothing to gain from telling those apart.

    """
    try:
        return path.is_dir() if is_dir else path.is_file()
    except OSError:
        return False


def _describe_artifacts() -> tuple[ArtifactView, ...]:
    """Describe the four artifacts in :data:`ARTIFACT_SPECS` order.

    That order is the plugin order of the Java runner - the HTML report, the
    JSON report, the rerun manifest, then the report tree - and ``index.html``
    lists the rows exactly as they arrive, so the order is preserved here
    rather than sorted.

    Returns:
        One :class:`ArtifactView` per spec.  An absent artifact carries
        ``exists=False`` and no timestamps, which the page renders without a
        link, since the artifact route answers 404 for anything not on disk.

    """
    described: list[ArtifactView] = []
    for spec in ARTIFACT_SPECS:
        path = artifact_path(spec)
        exists = _artifact_exists(path, is_dir=spec.is_dir)
        modified_iso, modified_display = (
            _modification_times(path) if exists else (None, None)
        )
        described.append(
            ArtifactView(
                label=_artifact_label(spec),
                name=spec.key,
                relpath=spec.relpath,
                exists=exists,
                modified_iso=modified_iso,
                modified_display=modified_display,
            )
        )
    return tuple(described)


def _load_features() -> list[Any]:
    """Parse the results artifact, or answer 404 for every reason it cannot be.

    The one data-availability rule, in one place, for all four report routes.
    Absent, unreadable, unparseable and structurally wrong are one outcome
    here: a run has not produced usable results, and which of those it was is
    not the viewer's business to report.  The cause reaches the log, never the
    response - no exception text, no path, no hint - which is what keeps the
    four bodies ``app/errors.py`` renders byte-identical across the causes.

    The file is re-read on every request and never cached.  A cache would serve
    a stale run after a fresh one had overwritten the artifact, and it would be
    hidden state a route test could not control.

    Returns:
        The top-level list exactly as parsed - no re-shaping, no renamed key, no
        coerced value - because the views consume the raw Cucumber mappings and
        the artifact is the model.  An empty list is a legitimate success: a run
        that selected no scenario still writes all four artifacts.

    Raises:
        werkzeug.exceptions.NotFound: Through ``abort(404)``, if the artifact is
            absent, cannot be read, cannot be decoded as UTF-8, does not parse
            as JSON, or parses as anything other than a list.

    """
    path = cucumber_json_path()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    # OSError covers the absent file, the directory standing where the file
    # belongs, the unreadable parent and the denied permission alike;
    # UnicodeDecodeError and JSONDecodeError are both ValueError subclasses and
    # are named individually so the intent is legible; RecursionError is what a
    # pathologically nested document raises, and a document too deep to parse is
    # unparseable rather than a server fault.
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        logger.debug("Results artifact absent, unreadable or unparseable")
        abort(404)

    if not isinstance(document, list):
        # Specification section 0.6 pins the top level as a list of feature
        # objects.  Anything else parsed cleanly but describes no run.
        logger.debug("Results artifact parsed but is not a list of features")
        abort(404)

    return document


def _artifact_modified() -> str | None:
    """The results artifact's modification time, for the report views.

    Returns:
        Millisecond-precision UTC ISO-8601 ending in ``Z``, or ``None`` when the
        time cannot be read - which the three views render as an em dash.  The
        value is optional context, so its absence never affects a status code.

    """
    modified_iso, _ = _modification_times(cucumber_json_path())
    return modified_iso


# --------------------------------------------------------------------------- #
# Reading the result model.  Every lookup is total: the artifact is what a
# reader turns to when a run has gone wrong, so a malformed member answers a
# neutral value rather than taking a page down.  json.loads produces exactly
# dicts, lists, strings, numbers, booleans and None, so those are the only
# types these guards have to admit.
# --------------------------------------------------------------------------- #


def _elements(feature: Any) -> list[Any]:
    """The element list of one feature, in file order.

    Args:
        feature: A parsed feature mapping, or anything at all.

    Returns:
        The ``elements`` list unchanged - backgrounds and scenarios still
        interleaved, the repeated backgrounds neither deduplicated nor
        reordered, because that structure is part of what the port reproduces
        deterministically.  An empty list for a feature that is not a mapping
        or carries no element list.

    """
    if not isinstance(feature, dict):
        return []
    elements = feature.get("elements")
    return elements if isinstance(elements, list) else []


def _element_type(element: Any) -> str:
    """One element's kind, normalised the way the templates normalise it.

    Coerced to text, stripped and folded to lower case - ``|string|trim|lower``
    in Jinja - so ``"Scenario"`` and ``" scenario "`` are the same kind and a
    missing or falsy value is no kind at all.

    Args:
        element: A parsed element mapping, or anything at all.

    Returns:
        The normalised type token, or the empty string.

    """
    if not isinstance(element, dict):
        return ""
    value = element.get("type")
    return str(value).strip().lower() if value else ""


def _steps(element: Any) -> list[Any]:
    """The step list of one element.

    Args:
        element: A parsed element mapping, or anything at all.

    Returns:
        The ``steps`` list, or an empty list.  A scenario with no step is a
        genuinely reachable state that the views render in full.

    """
    if not isinstance(element, dict):
        return []
    steps = element.get("steps")
    return steps if isinstance(steps, list) else []


def _step_status(step: Any) -> Any:
    """The raw ``result.status`` of one step, unnormalised.

    Args:
        step: A parsed step mapping, or anything at all.

    Returns:
        Whatever ``result.status`` holds, or ``None`` when either key is absent
        or of the wrong shape.  Normalisation is :func:`_status_token`'s job, so
        that it happens in exactly one place.

    """
    if not isinstance(step, dict):
        return None
    result = step.get("result")
    if not isinstance(result, dict):
        return None
    return result.get("status")


def _status_token(status: Any) -> str:
    """Normalise one status, exactly as ``status_token`` does in the partial.

    That macro is the project's single normalisation point and this is its
    Python twin, so the counts on ``/reports`` and the counts in the summary
    cannot classify the same status differently.  The rule: coerce to text,
    strip, fold to lower case, and answer with that token when it is one of the
    seven the result model produces - otherwise ``unknown``.

    Args:
        status: Any value at all: a status string, a number, ``None``, a
            container, or a key that was never there.

    Returns:
        One of the seven statuses, or :data:`_UNKNOWN_STATUS`.  This function
        cannot raise.

    """
    candidate = str(status).strip().lower() if status else ""
    return candidate if candidate in _KNOWN_STATUS_TOKENS else _UNKNOWN_STATUS


def _worst_token(tokens: list[str]) -> str:
    """The worst status in a collection of already-normalised tokens.

    Args:
        tokens: Normalised status tokens, in any order.

    Returns:
        The first member of :data:`_STATUS_PRECEDENCE` that occurs among them.
        An empty collection, and one holding nothing the precedence names,
        answer :data:`_UNKNOWN_STATUS` - a status the model never produced must
        not be reported as a pass.

    """
    present = set(tokens)
    for candidate in _STATUS_PRECEDENCE:
        if candidate in present:
            return candidate
    return _UNKNOWN_STATUS


def _counts(tokens: list[str]) -> dict[str, Any]:
    """One ``{"total": …, "by_status": {…}}`` block of the summary.

    Args:
        tokens: One normalised status token per counted thing.

    Returns:
        The total, always present and ``0`` included, and a map carrying only
        the statuses with a non-zero count, built in
        :data:`_STATUS_READING_ORDER` so the map is deterministic for identical
        input.

    """
    by_status: dict[str, int] = {}
    for token in _STATUS_READING_ORDER:
        counted = tokens.count(token)
        if counted:
            by_status[token] = counted
    return {"total": len(tokens), "by_status": by_status}


def _parse_start(value: str) -> datetime | None:
    """Parse one ``start_timestamp``, or answer ``None``.

    The values the JSON writer emits are millisecond-precision UTC ISO-8601
    ending in a literal ``Z``.  That suffix is rewritten to an explicit offset
    before parsing, and a value that parses without one is read as UTC, so
    every instant this function returns is aware and any two of them compare
    without raising.

    Args:
        value: A non-empty timestamp string.

    Returns:
        The instant, or ``None`` if the string is not a timestamp at all.

    """
    text = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _earliest_start(features: list[Any]) -> str | None:
    """The run's earliest scenario start time, as the string the artifact holds.

    Only scenario elements are considered: a background carries no
    ``start_timestamp`` at all.  Selection is by parsed instant, and the string
    is returned verbatim rather than reformatted, so the value a client reads
    here is the value the artifact carries and the one the overview page shows.
    For the fixed-width UTC timestamps the writer emits, that is the same string
    the template's lexicographic ``|sort|first`` selects.

    Args:
        features: The parsed top-level feature list.

    Returns:
        The earliest usable timestamp string, or ``None`` when no scenario
        carries one and when none of the values present can be parsed - a
        malformed value is dropped rather than reported as the run's start.

    """
    parsed: list[tuple[datetime, str]] = []
    for feature in features:
        for element in _elements(feature):
            if _element_type(element) != _SCENARIO_ELEMENT_TYPE:
                continue
            if not isinstance(element, dict):
                continue
            started = element.get("start_timestamp")
            if not isinstance(started, str) or not started.strip():
                continue
            candidate = started.strip()
            moment = _parse_start(candidate)
            if moment is not None:
                parsed.append((moment, candidate))
    if not parsed:
        return None
    # The string breaks a tie between two identical instants, so the answer is
    # the same for identical input whatever order the features were merged in.
    return min(parsed, key=lambda pair: (pair[0], pair[1]))[1]


def _summarize(features: list[Any]) -> dict[str, Any]:
    """Count the run, applying ``view/overview.html``'s rules in Python.

    One pass, three tallies, and the rules the module docstring states: every
    step of every element is counted with backgrounds included; a scenario takes
    the worst status among its own steps; a feature takes the worst status
    across the steps of all its elements, backgrounds included; and only
    elements typed ``scenario`` count as scenarios.

    Args:
        features: The parsed top-level feature list.

    Returns:
        The summary body: a ``features``, ``scenarios`` and ``steps`` block, and
        the run's earliest scenario start time.  An empty feature list answers
        three zero totals, three empty maps and ``None``.

    """
    feature_tokens: list[str] = []
    scenario_tokens: list[str] = []
    step_tokens: list[str] = []

    for feature in features:
        feature_steps: list[str] = []
        for element in _elements(feature):
            element_steps = [
                _status_token(_step_status(step)) for step in _steps(element)
            ]
            # Counted towards the feature and the run whatever the element kind:
            # a background genuinely runs once per scenario.
            feature_steps.extend(element_steps)
            if _element_type(element) == _SCENARIO_ELEMENT_TYPE:
                # A scenario's own steps decide its status; the background's
                # steps, counted above, deliberately do not.
                scenario_tokens.append(_worst_token(element_steps))
        step_tokens.extend(feature_steps)
        feature_tokens.append(_worst_token(feature_steps))

    return {
        "features": _counts(feature_tokens),
        "scenarios": _counts(scenario_tokens),
        "steps": _counts(step_tokens),
        "start_timestamp": _earliest_start(features),
    }


# --------------------------------------------------------------------------- #
# Positional lookup.  Both keys are positions and neither is an identifier
# slug: two pairs of features in this suite share an id because they share a
# title, and keying on that would serve one member of a pair in place of the
# other.  The collision is source behaviour the port preserves.
# --------------------------------------------------------------------------- #


def _feature_at(features: list[Any], findex: int) -> Any:
    """The feature at one zero-based position, or 404.

    Args:
        features: The parsed top-level feature list.
        findex: The requested position.

    Returns:
        The raw feature mapping, handed on unprocessed.

    Raises:
        werkzeug.exceptions.NotFound: Through ``abort(404)``, if the position is
            outside the list.  The negative case is guarded explicitly even
            though the ``<int:…>`` converter rejects a negative segment at
            routing time, so the contract holds however the view is called.

    """
    if findex < 0 or findex >= len(features):
        logger.debug("Feature position out of range")
        abort(404)
    return features[findex]


def _scenario_at(feature: Any, sindex: int) -> tuple[Any, Any]:
    """One scenario by its position among the feature's scenario elements.

    The index counts scenario elements alone.  Backgrounds interleave and
    repeat, so an index over the raw element list would serve a background where
    a scenario was asked for and would land every link one element early.

    Args:
        feature: The raw feature mapping the scenario belongs to.
        sindex: The requested position among the scenario elements.

    Returns:
        ``(scenario, background)``.  The background is the element immediately
        preceding this scenario when that element is one, and ``None``
        otherwise: each repeated copy belongs to the scenario it preceded, so no
        distant copy is substituted and none is synthesized.

    Raises:
        werkzeug.exceptions.NotFound: Through ``abort(404)``, if the position is
            negative or past the last scenario element - which includes every
            position of a feature carrying no scenario at all.

    """
    elements = _elements(feature)
    positions = [
        position
        for position, element in enumerate(elements)
        if _element_type(element) == _SCENARIO_ELEMENT_TYPE
    ]
    if sindex < 0 or sindex >= len(positions):
        logger.debug("Scenario position out of range")
        abort(404)

    position = positions[sindex]
    background: Any = None
    if position > 0:
        preceding = elements[position - 1]
        if _element_type(preceding) == _BACKGROUND_ELEMENT_TYPE:
            background = preceding
    return elements[position], background


# --------------------------------------------------------------------------- #
# The artifact route's validation.  The allowlist and the traversal decision
# belong to app/utils/paths.py; what is added here is a second, independent
# containment check, because a traversal defect on this route is a real
# vulnerability and one owner of a security decision is one point of failure.
# --------------------------------------------------------------------------- #


def _within_artifact_root(path: Path) -> bool:
    """Judge a path contained: inside the artifact root, outside the workers.

    Containment is judged **after** resolution, so a symlink whose target
    escapes the root is caught by the same check as ``..`` traversal.  The
    worker-intermediates rejection is made independently of the allowlist,
    rather than relying on it: the worker-intermediates directory the path
    module locates holds the per-worker results a run merges, and the Jenkins
    publisher's include pattern was deliberately narrowed to the single results
    artifact to keep those intermediates out of the published report.  Serving
    them over HTTP would undo that.

    Args:
        path: A candidate path, already allowlisted.

    Returns:
        ``True`` only for a path that resolves inside the artifact root and not
        inside the worker-intermediates directory.  A path that cannot be
        resolved at all - one carrying an embedded null byte raises
        ``ValueError``, an unreadable or over-long one raises ``OSError`` - is
        not contained.

    """
    try:
        resolved = path.resolve()
        root = target_root().resolve()
        workers = workers_dir().resolve()
    except (OSError, ValueError):
        return False
    if not resolved.is_relative_to(root):
        return False
    return not resolved.is_relative_to(workers)


def _validated_artifact(name: str) -> Path:
    """Resolve a requested artifact name to a file that may be served, or 404.

    Args:
        name: The raw request segment, which may name any of the three
            allowlisted files, the report tree, or a path beneath it.  A
            trailing slash on the report tree's own key is normalised away
            first, so that the directory's two spellings behave identically:
            the landing page links it by its bare key, but a reader can type the
            slash, and the path module tolerates it too - the normalisation is
            belt and braces rather than the only guard.  The slash is *not*
            stripped from anything else, because a directory-shaped request for
            a file is one of the rejections the path module makes deliberately
            and this route does not overrule the allowlist's owner.

    Returns:
        The absolute path of an existing file inside the artifact root.

    Raises:
        werkzeug.exceptions.NotFound: Through ``abort(404)``, for every
            rejection: a name off the allowlist, an absent file, a directory
            that is not the report tree, a path that resolves outside the
            artifact root, and any worker-intermediates path.  Never a 403, and
            the response carries neither the rejected name nor any filesystem
            path - reflecting either would confirm the layout to a prober and
            separate a probe from an honest mistake.  The reason is logged
            instead, with the name rendered through ``%r`` so that a control
            character in a crafted request cannot forge a second log line.

    """
    requested = name
    if requested.rstrip("/") == PRETTY_REPORTS_DIR_NAME:
        requested = PRETTY_REPORTS_DIR_NAME
    candidate = resolve_artifact(requested) if requested else None
    if candidate is None:
        logger.debug("Artifact request %r is not an allowlisted artifact", name)
        abort(404)

    if not _within_artifact_root(candidate):
        # Unreachable through the path module's own validation, and checked
        # anyway: this is the one route that turns request input into a
        # filesystem read, so its containment does not rest on a single
        # implementation.  WARNING rather than DEBUG, because reaching here
        # means one of the two checks disagreed with the other.
        logger.warning(
            "Artifact request %r resolves outside the artifact root or into "
            "the worker intermediates",
            name,
        )
        abort(404)

    served = candidate
    if _artifact_exists(candidate, is_dir=True):
        # A directory request is served the report tree's overview page.  The
        # path module already rewrites the tree's own key to that page, so this
        # branch answers the case where a path became a directory after that
        # rewrite - and it never lists a directory and never invents an index.
        served = pretty_reports_index_path()
        if not _within_artifact_root(served):
            logger.warning("The report tree overview page is not inside the root")
            abort(404)

    if not _artifact_exists(served, is_dir=False):
        logger.debug("Artifact request %r names nothing on disk", name)
        abort(404)

    return served


# --------------------------------------------------------------------------- #
# The six views.  Every one is synchronous and read-only; none writes, none
# starts a run, and none builds an error body of its own.  Methods are left at
# Flask's default, so each answers GET with HEAD and OPTIONS derived from it.
# --------------------------------------------------------------------------- #


@web_bp.route("/")
def index() -> str:
    """Presence and modification time of each of the four report artifacts.

    The one route the data-availability rule does not govern: it answers **200
    always**, including on a checkout where ``target/`` has never existed, since
    that directory is generated output and the landing page has to be useful
    before anything has ever run.  An absent artifact is reported as absent
    rather than as a failure, and the per-worker intermediates are neither
    listed nor stat'd - they are never reachable over HTTP.

    No result counts are computed here; those belong to ``/reports`` and
    ``/reports/summary``.

    Returns:
        The rendered landing page, always at status 200.

    """
    return render_template(_INDEX_TEMPLATE, artifacts=_describe_artifacts())


@web_bp.route("/reports")
def reports_overview() -> str:
    """The run overview, derived by the template from the raw results.

    The feature list is handed over exactly as parsed, in file order, and its
    zero-based positions are the feature route's keys.  No aggregate is
    pre-computed: the template derives every count, status and total from that
    one list, so this page and the artifact it describes cannot drift apart, and
    the summary route applies the same rules in Python rather than reading a
    second, differently-derived set of numbers.

    Returns:
        The rendered overview.

    Raises:
        werkzeug.exceptions.NotFound: If the results artifact is absent,
            unreadable, unparseable or not a list.  An empty list is a success.

    """
    return render_template(
        _OVERVIEW_TEMPLATE,
        features=_load_features(),
        artifact_modified=_artifact_modified(),
    )


@web_bp.route("/reports/features/<int:findex>")
def report_feature(findex: int) -> str:
    """One feature, addressed by its zero-based position in the results file.

    Keyed by position and never by the feature's own identifier: two pairs of
    features in this suite share an identifier slug because they share a title,
    and an identifier used as a link key would serve one member of a pair in
    place of the other.  No identifier lookup, redirect or de-duplication is
    offered here - the collision is source behaviour the port preserves.

    Args:
        findex: The feature's zero-based position.

    Returns:
        The rendered feature page, with the raw feature mapping and its index.

    Raises:
        werkzeug.exceptions.NotFound: If the results artifact is unusable, or
            the position lies outside the feature list.

    """
    features = _load_features()
    return render_template(
        _FEATURE_TEMPLATE,
        feature=_feature_at(features, findex),
        findex=findex,
        artifact_modified=_artifact_modified(),
    )


@web_bp.route("/reports/features/<int:findex>/scenarios/<int:sindex>")
def report_scenario(findex: int, sindex: int) -> str:
    """One scenario, addressed by two positional keys.

    ``sindex`` counts the feature's **scenario elements** alone, so the
    backgrounds that interleave and repeat never consume an index - the same
    derivation ``view/feature.html`` performs when it builds the links that
    bring a reader here.  The background immediately preceding the scenario is
    passed alongside it when there is one.

    Both mappings are handed to the template raw.  The shared partials read the
    artifact's own keys - a step keyword with its trailing space, a match
    location and its arguments, a nanosecond-integer duration, the untruncated
    error text, and a screenshot embedding's mime type, data and name - so
    renaming a key, reformatting a duration or pre-rendering error text here
    would break them and would put this view at odds with the two generated HTML
    artifacts, which render the same model.

    Args:
        findex: The feature's zero-based position in the results file.
        sindex: The scenario's zero-based position among that feature's
            scenario elements.

    Returns:
        The rendered scenario page.

    Raises:
        werkzeug.exceptions.NotFound: If the results artifact is unusable, or
            either position is out of range.

    """
    features = _load_features()
    feature = _feature_at(features, findex)
    scenario, background = _scenario_at(feature, sindex)
    return render_template(
        _SCENARIO_TEMPLATE,
        feature=feature,
        findex=findex,
        scenario=scenario,
        sindex=sindex,
        background=background,
        artifact_modified=_artifact_modified(),
    )


@web_bp.route("/reports/summary")
def reports_summary() -> Response:
    """Counts of features, scenarios and steps by status, and the run's start.

    The body carries four members: a ``features``, ``scenarios`` and ``steps``
    block, each ``{"total": …, "by_status": {…}}`` with the total always present
    and the map holding only the non-zero statuses, and ``start_timestamp``,
    the run's earliest scenario start time as the artifact's own string or
    ``null``.  An empty feature list answers three zero totals, three empty maps
    and ``null``, at status 200.

    The rules are ``view/overview.html``'s, applied in Python so that the page
    and this route cannot disagree.  Two of them are worth stating on the route
    itself, because they are the ones a reader would otherwise have to infer:

    * **Background steps are counted** in the step totals, because a repeated
      background genuinely ran once per scenario.
    * They do **not** contribute to any scenario's status, which is decided by
      that scenario's own steps, but they **do** contribute to their feature's,
      which is the worst status across the steps of all its elements.  So a
      background-only failure moves a feature's status without moving any
      scenario's, exactly as the overview page reports it.

    This is the one route whose success body is JSON, and it is governed by the
    same data-availability rule as the HTML report routes; because it shares
    :func:`_load_features`, ``app/errors.py`` recognises the endpoint and gives
    its 404 a JSON body too.

    Returns:
        The summary as JSON, at status 200.

    Raises:
        werkzeug.exceptions.NotFound: If the results artifact is absent,
            unreadable, unparseable or not a list.

    """
    return jsonify(_summarize(_load_features()))


@web_bp.route("/artifacts/<path:name>", merge_slashes=False)
def artifact(name: str) -> Response:
    """Serve one allowlisted artifact, and nothing else.

    The allowlist is the three artifact files and any path beneath the report
    tree; a request naming the tree itself is served its overview page.
    Everything else is a plain 404 - any other directory, any absent file, any
    path resolving outside the artifact root, and any worker-intermediates path.
    ``merge_slashes`` is off so that a doubled separator, which is how an
    absolute path arrives at this rule, is answered 404 by the router rather
    than redirected: *everything else* means every rejection is the same 404.

    The results artifact is servable here even while ``/reports`` answers 404 for
    it, because an unparseable file is still a downloadable one; this route
    therefore never parses what it serves.

    Args:
        name: The requested artifact path, relative to the artifact root.

    Returns:
        The file, with its mime type derived from its name by the framework.
        Nothing is read into memory here and no caching header is added.

    Raises:
        werkzeug.exceptions.NotFound: Through :func:`_validated_artifact`, for
            every rejection, disclosing no filesystem path.

    """
    return send_file(_validated_artifact(name))
