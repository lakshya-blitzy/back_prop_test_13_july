"""The six view functions of the read-only artifact viewer.

Specification section 0.3.1 fixes the route table and calls it *"the complete
contract"* - these six rules, and no seventh:

* ``GET /`` -> :func:`index`
* ``GET /reports`` -> :func:`reports_overview`
* ``GET /reports/features/<int:findex>`` -> :func:`report_feature`
* ``GET /reports/features/<int:findex>/scenarios/<int:sindex>`` ->
  :func:`report_scenario`
* ``GET /reports/summary`` -> :func:`reports_summary`
* ``GET /artifacts/<path:name>`` -> :func:`artifact`

Endpoint names are a contract: the templates build every URL with ``url_for``
against the view function names under the blueprint's ``web.`` prefix, so no
``endpoint=`` argument is passed here.  The views are plain functions bound by
:func:`register_routes`, which ``app/web/__init__.py`` calls once with the
blueprint it owns; nothing here imports ``app.web``, so that edge runs one way.

The viewer is deviation 12 of the specification's inventory, authorized by its
Conflict 3: the request mandates a Flask application, and Flask is held to the
minimum that compels - a viewer over the artifacts a run already produces.
Every route is synchronous and read-only; none writes, creates a directory,
spawns a process or starts a run, and no service, report writer or automation
module is imported (section 0.4.2 gives the package one outward edge,
``WEB --> UT``).  Every filesystem path comes from ``app/utils/paths.py``; the
path-like literals below are Jinja template names.

The four report routes share one data-availability rule (section 0.3.1):
:func:`_load_features` answers 404 when the results artifact is absent,
unreadable, unparseable or not a list - the same response for every cause,
through a bare ``abort(404)`` with no body of this module's own, which is what
lets ``app/errors.py`` render the four identically.  An empty feature list is a
success at 200, and ``GET /`` has no such precondition at all.

Read-only, and structurally so
------------------------------
Every route is synchronous, and every route only reads.  This module opens no
file for writing, creates no directory, spawns no process and imports no
service, no page object, no browser-automation module and no report *writer*,
so "no route can start a run" is a property of the import graph rather than a
convention someone has to remember.

Two outward edges, and why the second one exists
------------------------------------------------
``app/utils`` is the first - specification section 0.4.2's ``WEB --> UT`` -
and it owns every filesystem path this module names.  The second is
``app.reporting.aggregation``, and it is deliberate.

That module is the project's **one normalized result model**: the single place
a status is normalised, a status is folded, a step is counted, a duration is
summed and a run's start is chosen.  Section 0.4.2's invariant list requires
exactly one of them - *"Both HTML artifacts and the HTTP views render over one
normalized result model \u2026 so no view contradicts an artifact"* - and section
0.3.4 repeats it of these pages specifically.  A viewer with a private copy of
that calculation cannot satisfy it and did not: a failed after-hook read
*passed* here and *failed* in the generated artifacts, and an element with
nothing in it read *unknown* here and *passed* there.  Nothing in that
invariant list forbids this edge; what it forbids is a second model.

The edge is narrow by construction.  ``aggregation`` is pure computation - no
Flask, no Jinja, no Selenium, no service, no path accessor, no writer and no
filesystem side effect of any kind - so importing it adds arithmetic and no
capability.  It is the *only* name from ``app.reporting`` this module may
reach: the four report writers, the event collector and the screenshot module
stay out, and ``tests/test_web_routes.py`` asserts that boundary as a property
of this module's namespace rather than describing it.

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

That module owns the *reads*, too, and not only the names.  Every byte this
module reads off disk comes out of a descriptor it opened and verified -
``open_artifact_read`` for the results artifact, ``open_resolved_artifact`` for
a request-named one - which opens each component from the artifact root inward
with ``O_NOFOLLOW`` and then ``fstat``\\ s what it holds, so a symbolic link
anywhere in the path, an entry hard-linked to a file outside the artifact root,
a directory and a FIFO are all refused.  No byte this module serves or parses
is ever obtained by naming a pathname: a pathname is a statement about the
past, and this module is a viewer over files something else wrote.  One
pathname operation remains, and it can only refuse - the containment check
:func:`_within_artifact_root` makes over the path the authority returned, which
resolves that path to judge it and never to read it.

The artifact route, and what it delegates
-----------------------------------------
``GET /artifacts/<path:name>`` hands the **raw** request segment to
``open_resolved_artifact``, which validates it against section 0.3.1's
allowlist and opens the file in one operation, and then serves that open
stream.  Three properties follow, and each of them is a decision:

* **Nothing is normalised here.** The segment is not stripped, collapsed or
  rewritten before it is validated.  The allowlist authorizes exactly two
  spellings of the report tree's own directory - its bare key, and that key
  with exactly one trailing slash - and every other trailing-separator
  spelling is a 404, which only holds while no caller trims a separator on the
  way in.
* **Those two spellings are canonicalized by a redirect, not served.** The
  overview page inside the tree references its assets and its sibling pages
  relatively, so it is only usable under a base URL inside the tree; the
  directory alias answers a 302 to the page's own URL, and the page's bytes are
  served at that URL alone.
* **There is no fallback from one artifact to another.** The single authorized
  directory rewrite is the path module's own - both spellings are handed to it
  raw, and it is what maps them to the page - so anything else a request
  resolves to is either served as itself or refused.

What the resolved path is used for afterwards is metadata and refusal: the
served file's own name, from which the media type follows, and the containment
check above.  It is never re-opened, re-stat'ed for content, or named again to
obtain a byte.

One response for every unusable-results cause
---------------------------------------------
Section 0.3.1 states the data-availability rule once, for all four report
routes: when the results artifact a run writes is absent, unreadable or
unparseable, the route returns 404 - *"the same response for all three causes,
because a run has not produced usable results and the distinction is not the
viewer's to make."*  :func:`_load_features` is that rule, in one place, and a
document that parses but is not a list joins the same three causes, because
section 0.6 pins the top level of that artifact as a list of feature objects.
So does an entry the path authority refuses to read through, and so does a
document over any of the resource budgets this module reads within: a report a
reader cannot be shown is a report a reader cannot be shown, and the viewer
answers for it in one way.

Two consequences are easy to get backwards, so both are stated:

* An **empty list is a success**.  A run whose tag expression selected no
  scenario still writes all four artifacts, so ``[]`` renders at 200 with every
  tally at zero.  Only a missing or corrupt artifact is a 404.
* ``GET /`` **has no data-availability precondition at all** and answers 200
  always, including on a checkout where the artifact root has never existed.
  That directory is generated output; the landing page has to be useful before
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
Nothing here counts.  ``app/reporting/aggregation.py`` owns every rule, and
this module calls it:

1. **Steps** - ``step_statuses`` per element, every element counted,
   **backgrounds included**, because a repeated background genuinely ran once
   per scenario.  A hook is never a step.
2. **Element status** - ``element_status``: the severity fold over that
   element's own steps **and its own hooks**, so a scenario whose steps all
   passed but whose after-hook failed is not reported as a pass.  An element
   with neither steps nor hooks is ``passed`` (``EMPTY_ELEMENT_STATUS``),
   which is the reference generator's measured behaviour for the step-less
   Background in ``EmployeeFc.feature``.
3. **Feature status** - the fold over that feature's element statuses,
   backgrounds included, so a Background failure moves its feature's status
   without moving any scenario's.  A feature carrying no element at all is
   ``unknown`` (``EMPTY_AGGREGATE_STATUS``): nothing ran.
4. **Scenarios** - elements whose ``type`` is ``"scenario"``
   (``is_scenario_element``) and never the element count, because backgrounds
   interleave and repeat.
5. **Earliest start** - ``earliest_start``, the lowest **parseable**
   ``start_timestamp`` among scenario elements.

The four report routes build their whole render context from those calls, and
the three templates format the values they are handed.  So ``GET
/reports/summary``, ``/reports``, a feature page, a scenario page and both
generated HTML artifacts are readings of one calculation, and none of them can
drift from another or from the artifact it describes.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO, Final, NamedTuple

from flask import (
    Blueprint,
    Response,
    abort,
    jsonify,
    redirect,
    render_template,
    send_file,
    url_for,
)

# The one normalized result model, and the only name this module takes from
# ``app.reporting``; see "Two outward edges" above.  Imported by name rather
# than as a module so that the exact surface this viewer depends on is visible
# here and a widening of it is a visible change.
from app.reporting.aggregation import (
    STATUS_PRECEDENCE,
    SUMMARY_BY_STATUS_KEY,
    SUMMARY_GROUPS,
    SUMMARY_START_KEY,
    SUMMARY_TOTAL_KEY,
    UNKNOWN_STATUS,
    as_mapping,
    as_text,
    build_summary,
    count_group,
    decorate_feature,
    display_timestamp,
    earliest_start,
    format_duration_seconds,
    is_background,
    is_scenario_element,
    mappings,
    parse_timestamp,
    status_token,
    step_statuses,
)
from app.utils import (
    ARTIFACT_SPECS,
    CUCUMBER_JSON_NAME,
    CUCUMBER_REPORTS_HTML_NAME,
    PRETTY_HTML_SUBDIR,
    PRETTY_OVERVIEW_INDEX,
    PRETTY_REPORTS_DIR_NAME,
    RERUN_TXT_NAME,
    ArtifactSpec,
    cucumber_json_path,
    open_artifact_read,
    open_resolved_artifact,
    target_root,
    workers_dir,
)

__all__ = [
    "artifact",
    "index",
    "register_routes",
    "report_feature",
    "report_scenario",
    "reports_overview",
    "reports_summary",
]

#: Module logger, acquired from the standard library exactly as every other
#: module in the port does.  ``app/logging_config.py`` installs the handler
#: split for the whole ``app`` hierarchy and is deliberately not imported: it
#: is called once per process by that process's own entry point - for a viewer
#: process, ``create_app()`` - and its effect reaches this module through the
#: logger hierarchy without any import at all, including the sanitizing
#: formatter that bounds and renders every record these routes emit.
logger = logging.getLogger(__name__)

_INDEX_TEMPLATE: Final[str] = "index.html"
_OVERVIEW_TEMPLATE: Final[str] = "view/overview.html"
_FEATURE_TEMPLATE: Final[str] = "view/feature.html"
_SCENARIO_TEMPLATE: Final[str] = "view/scenario.html"

# --------------------------------------------------------------------------- #
# The vocabulary the *templates* branch on.  The result model's own vocabulary
# - the statuses, their severity order and their reading order - is
# app/reporting/aggregation.py's and is imported above, never restated here.
# --------------------------------------------------------------------------- #

#: The three kinds a rendered element block can have.  These are the values
#: ``view/feature.html`` branches its markup on, not a type discrimination of
#: this module's own: the discrimination is ``is_scenario_element`` and
#: ``is_background``, and an element the model does not name is rendered as
#: :data:`_OTHER_ELEMENT_KIND` - a neutral block - because dropping it would
#: misreport the run and raising on it would take the page down.
_SCENARIO_ELEMENT_KIND: Final[str] = "scenario"
_BACKGROUND_ELEMENT_KIND: Final[str] = "background"
_OTHER_ELEMENT_KIND: Final[str] = "other"

#: The failed token, obtained from the authority's normalisation rather than
#: typed out: the scenario page attaches its failure screenshot to the first
#: step carrying this status, and that spelling has to be the one every badge
#: and every count uses.
_FAILED_STATUS: Final[str] = status_token("failed")

#: The keys ``decorate_element`` and ``decorate_feature`` add to a copy, read
#: by name here because the authority keeps its own key constants private.  A
#: decorated mapping always carries all four, so every read below is a plain
#: lookup rather than a guarded one.
_STATUS_KEY: Final[str] = "status"
_STATS_KEY: Final[str] = "stats"
_DURATION_NS_KEY: Final[str] = "duration_ns"
_DURATION_SAMPLES_KEY: Final[str] = "duration_samples"

#: Severity order for the filter controls: the authority's precedence, highest
#: first, with the fallback token last.  A ``by_status`` map from the authority
#: is already in *reading* order, which is the order the tallies present; the
#: controls have always offered severity order instead, and that is preserved.
_FILTER_ORDER: Final[tuple[str, ...]] = (*STATUS_PRECEDENCE, UNKNOWN_STATUS)

_ARTIFACT_LABELS: Final[tuple[tuple[str, str], ...]] = (
    (CUCUMBER_REPORTS_HTML_NAME, "Cucumber HTML report"),
    (CUCUMBER_JSON_NAME, "Cucumber JSON"),
    (RERUN_TXT_NAME, "Rerun manifest"),
    (PRETTY_REPORTS_DIR_NAME, "PrettyReports tree"),
)


#: The one name ``web.artifact`` serves the PrettyReports overview page under,
#: assembled from the path module's three constants rather than written out:
#: the tree's directory name, the sub-directory its generator writes into, and
#: the overview page's file name.  Both the landing page's link and the
#: redirect the two directory spellings answer with are built from this, so a
#: reader always arrives at the page under a base URL the page's own relative
#: references resolve against.
_PRETTY_OVERVIEW_ROUTE_NAME: Final[str] = "/".join(
    (PRETTY_REPORTS_DIR_NAME, PRETTY_HTML_SUBDIR, PRETTY_OVERVIEW_INDEX)
)

#: The two spellings of the report tree's own directory that AAP 0.3.1
#: authorizes: its bare key, and that key with exactly one trailing slash.
#: Membership is tested against the raw request segment, so ``cucumber//``,
#: ``cucumber///`` and every longer variant are not members and are left to the
#: path authority, which refuses them.  Nothing here strips or collapses a
#: separator.
_PRETTY_DIRECTORY_NAMES: Final[frozenset[str]] = frozenset(
    (PRETTY_REPORTS_DIR_NAME, f"{PRETTY_REPORTS_DIR_NAME}/")
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
        route_name: The value the page passes as ``web.artifact``'s ``name``
            argument for this artifact - :func:`_artifact_route_name` decides
            it, and for the report tree it is the nested overview page rather
            than the directory alias.
        relpath: :attr:`ArtifactSpec.relpath`, for display only.
        servable: Whether a request for :attr:`route_name` would be served
            right now, established by the artifact route's own opener rather
            than by a filesystem-kind test - so a link is offered only when it
            resolves.
        modified_iso: Millisecond-precision UTC ISO-8601 ending in a literal
            ``Z``, the shape the artifacts' own timestamps carry, or ``None``.
        modified_display: The same instant, human-readable, or ``None``.

    """

    label: str
    route_name: str
    relpath: str
    servable: bool
    modified_iso: str | None
    modified_display: str | None


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


def _modification_times(
    handle: BinaryIO, name: str
) -> tuple[str | None, str | None]:
    """Both timestamp forms for an artifact already held open, or two ``None``s.

    Derived from :func:`os.fstat` on the descriptor the path authority returned,
    never from a ``stat`` of a pathname: the descriptor is the object that was
    verified and, for the index, the object the artifact route would serve, so
    the time shown beside an artifact is that artifact's own and cannot be the
    time of a symbolic link's target or of a file hard-linked in from outside
    the artifact root.

    A modification time is decoration on every page that shows one, so no
    failure to read one may turn a working page into an error: a refused
    ``fstat`` and a clock value the platform cannot represent both answer the
    same "unavailable", which ``index.html`` and the report views each render
    in words.

    The cause never reaches the page and is always logged, at WARNING: the
    absent case cannot arise here, because an absent artifact has no open
    descriptor and is refused by the call that would have produced one, so
    anything that fails at this point is an anomaly an operator has to see.
    The record names the artifact only by its final component - no path is
    spelled here, per specification section 0.4.2 - and carries the exception
    itself through ``exc_info``.

    Args:
        handle: An open stream over the artifact, owned by the caller, which
            closes it.  Its position is not moved and nothing is read from it.
        name: The artifact's final path component, for the log record only.

    Returns:
        ``(iso, display)``, or ``(None, None)`` if the time cannot be read.

    """
    try:
        info = os.fstat(handle.fileno())
        moment = datetime.fromtimestamp(info.st_mtime, tz=UTC)
    except (OSError, ValueError, OverflowError) as exc:
        # ValueError and OverflowError cover a stored timestamp outside the
        # range datetime can represent, which a corrupt filesystem can produce;
        # OSError covers a descriptor the platform refuses to stat.
        logger.warning(
            "Modification time unavailable for artifact %r",
            name,
            exc_info=exc,
        )
        return None, None
    return _iso_millis_z(moment), _display_utc(moment)


# --------------------------------------------------------------------------- #
# Servability, and the results file every report route reads.  Both questions
# are put to the path authority: what the landing page advertises is what the
# artifact route can open, and what the report routes parse is what that
# authority read out of a descriptor it verified.
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


def _artifact_route_name(spec: ArtifactSpec) -> str:
    """The ``web.artifact`` name that serves one artifact's own content.

    For the three single-file artifacts that is the spec's key, which is the
    allowlisted name the route accepts.  For the report tree it is the nested
    overview page inside it, and not the tree's own key: the key is a directory
    alias, and the artifact route answers it with a redirect to this name
    precisely so that a reader's browser ends up with its base URL *inside* the
    generated tree, where that page's relative stylesheet, script, image and
    sibling-page references resolve.  Linking the alias instead would deliver
    the same bytes under a base URL one level too high, which breaks every one
    of those references.

    Args:
        spec: The artifact to name.

    Returns:
        The value to pass as ``web.artifact``'s ``name`` argument.  Built from
        the path module's constants, never from a path written out here.

    """
    return _PRETTY_OVERVIEW_ROUTE_NAME if spec.is_dir else spec.key


def _describe_artifacts() -> tuple[ArtifactView, ...]:
    """Describe the four artifacts in :data:`ARTIFACT_SPECS` order.

    That order is the plugin order of the Java runner - the HTML report, the
    JSON report, the rerun manifest, then the report tree - and ``index.html``
    lists the rows exactly as they arrive, so the order is preserved here
    rather than sorted.

    Availability is the artifact route's own question, put to the artifact
    route's own authority: each artifact is opened through
    :func:`app.utils.open_resolved_artifact` - the function :func:`artifact`
    serves from, called with the same untrusted-name semantics - so a row is
    advertised exactly when a request for it would be served, and the page
    cannot offer a link that is certain to 404.  A test of what kind of thing
    is on disk answers a different question and would be wrong here: it accepts
    a symlinked or hard-linked file that route refuses, and for the report tree
    it accepts the mere presence of the directory even when the overview page
    inside it has not been written.  The authority rewrites the tree's
    directory key to that page, so the page is what is verified.

    Timestamps come from :func:`os.fstat` on the descriptor that answered, so
    the instant shown is the instant of the object a reader would receive.
    Each handle is closed before the next artifact is examined; nothing is read
    from any of them.

    Returns:
        One :class:`ArtifactView` per spec.  An artifact that cannot be served
        carries ``servable=False`` and no timestamps, which the page renders
        without a link.

    """
    described: list[ArtifactView] = []
    for spec in ARTIFACT_SPECS:
        opened = open_resolved_artifact(spec.key)
        modified_iso: str | None = None
        modified_display: str | None = None
        if opened is None:
            # DEBUG, not WARNING: the authority absorbs every rejection into
            # one answer and deliberately reports no cause, so this record
            # cannot distinguish the viewer's ordinary pre-run state - nothing
            # has been written yet - from an anomaly, and the landing page
            # renders on every request.  Putting that on the error stream
            # would make a fresh checkout noisy.
            logger.debug(
                "Artifact %r is not servable; listing it as unavailable",
                spec.key,
            )
        else:
            resolved, handle = opened
            try:
                modified_iso, modified_display = _modification_times(
                    handle, resolved.name
                )
            finally:
                # The landing page serves no bytes, so the handle has done its
                # work - it proved the artifact openable and carried its mtime
                # - and is closed here rather than left to the garbage
                # collector.
                handle.close()
        described.append(
            ArtifactView(
                label=_artifact_label(spec),
                route_name=_artifact_route_name(spec),
                relpath=spec.relpath,
                servable=opened is not None,
                modified_iso=modified_iso,
                modified_display=modified_display,
            )
        )
    return tuple(described)


# --------------------------------------------------------------------------- #
# Resource budgets for the results artifact.
#
# The artifact is generated output, but it is generated output this process
# neither wrote nor can authenticate, and four routes read it on every request,
# so its size and shape are inputs and are bounded like any other input
# (CWE-400).  Each budget below is set from a measured figure with room over
# it, and every breach is refused with the same cause-neutral 404 the absent
# and the unparseable cases already answer - a report a reader cannot be shown
# is a report a reader cannot be shown, whichever budget said so.
#
# The measurements are of ``tests/fixtures/golden_cucumber.json``, the document
# the JSON writer produces for one feature of this suite: 10,184 bytes, 509
# nodes, nesting depth 10, longest string 3,121 characters, largest single
# collection 9 members.
# --------------------------------------------------------------------------- #

#: Largest results file this module will read, in bytes.  The whole document is
#: materialised to be walked and rendered, so this is what bounds the memory
#: one request can cost.  The measured artifact above is 10 KB for one feature;
#: the ten features of this suite with a screenshot embedded on every failing
#: scenario - real full-window PNGs are a few hundred KB, base64 at 4/3 - come
#: to tens of megabytes at the very worst, so 64 MiB is several times the
#: largest legitimate report and still a firm ceiling.
_MAX_RESULTS_BYTES: Final[int] = 64 * 1024 * 1024

#: Longest numeric token :func:`json.loads` may convert.  Without this a
#: 5,000-digit integer reaches CPython's integer-string conversion limit, which
#: raises :exc:`ValueError` from inside the parser; the longest number any
#: writer in this port emits is a nanosecond duration, at most 19 digits for a
#: 64-bit value, so 64 characters is triple the legitimate maximum and the
#: refusal happens before the conversion is attempted.
_MAX_NUMBER_CHARS: Final[int] = 64

#: Deepest nesting the document may carry.  The measured depth is 10 - list,
#: feature, elements, element, steps, step, match, arguments, argument, value -
#: so 64 is six times it, and low enough that a document this budget admits is
#: nowhere near the depth at which the parser exhausts the interpreter's
#: recursion limit.
_MAX_NESTING_DEPTH: Final[int] = 64

#: Most values the document may hold in total, counting every container, key
#: and scalar.  This is the render budget: the three report views iterate the
#: structure, so the node count is what bounds their CPU.  509 nodes were
#: measured for one feature, which puts a whole suite in the low thousands.
_MAX_NODES: Final[int] = 1_000_000

#: Most members any single mapping or list may hold.  The largest measured
#: collection has 9 members; a feature's element list grows with the scenarios
#: in it and stays in the hundreds.
_MAX_COLLECTION_ITEMS: Final[int] = 100_000

#: Longest single string the document may carry.  A screenshot embedding's
#: ``data`` member is base64 text and is by far the longest value in a real
#: report, so this cap is set from the writer's own inline-embedding ceiling -
#: ``app/reporting/screenshots.py`` refuses a screenshot above 32 MiB, which is
#: roughly 42.7 MiB of base64 - with room over it.  A lower cap would refuse an
#: artifact the writer is entitled to produce.
_MAX_STRING_CHARS: Final[int] = 48 * 1024 * 1024


class _ResultsBudgetExceeded(ValueError):
    """One of the budgets above was exceeded while reading the artifact.

    A :exc:`ValueError` subclass deliberately: a document too large, too deep
    or too wide to be read is unusable for exactly the reason a document that
    does not parse is unusable, and :func:`json.JSONDecodeError` is a
    ``ValueError`` too - so one ``except`` clause in :func:`_load_features`
    answers both with the same 404, and no budget can grow a response of its
    own.  The message names the budget and the measured figure for the log; it
    never reaches a response.
    """


def _bounded_int(token: str) -> int:
    """Convert one JSON integer token, refusing an implausibly long one.

    Args:
        token: The integer token exactly as it appears in the document.

    Returns:
        The parsed integer.

    Raises:
        _ResultsBudgetExceeded: If the token is longer than
            :data:`_MAX_NUMBER_CHARS`.  Raised *before* :class:`int` is called,
            so the conversion cost is never paid and CPython's own digit limit
            is never the thing that reports the problem.

    """
    if len(token) > _MAX_NUMBER_CHARS:
        raise _ResultsBudgetExceeded(
            f"a numeric token of {len(token)} characters exceeds the "
            f"{_MAX_NUMBER_CHARS}-character limit"
        )
    return int(token)


def _bounded_float(token: str) -> float:
    """Convert one JSON floating-point token, refusing an implausibly long one.

    Args:
        token: The token exactly as it appears in the document, including any
            sign, decimal point and exponent.

    Returns:
        The parsed float.

    Raises:
        _ResultsBudgetExceeded: If the token is longer than
            :data:`_MAX_NUMBER_CHARS`.

    """
    if len(token) > _MAX_NUMBER_CHARS:
        raise _ResultsBudgetExceeded(
            f"a numeric token of {len(token)} characters exceeds the "
            f"{_MAX_NUMBER_CHARS}-character limit"
        )
    return float(token)


def _enforce_structural_budgets(document: Any) -> None:
    """Walk a parsed document and refuse it if it breaks a structural budget.

    Iterative, with an explicit stack, and never recursive: a document nested
    deeply enough to exhaust the interpreter's stack must be *refused*, and a
    recursive walk would have failed in the act of measuring it.  Every value
    is visited once - mapping keys included, since a key is a string a page
    can render - so the four structural budgets are enforced over the whole
    document before any view iterates it.

    Args:
        document: The value :func:`json.loads` returned, of any shape.

    Raises:
        _ResultsBudgetExceeded: For the first budget the document breaks:
            :data:`_MAX_NODES`, :data:`_MAX_NESTING_DEPTH`,
            :data:`_MAX_COLLECTION_ITEMS` or :data:`_MAX_STRING_CHARS`.  The
            walk stops there, so a hostile document costs no more than the
            budget it broke.

    """
    pending: list[tuple[Any, int]] = [(document, 1)]
    visited = 0
    while pending:
        node, depth = pending.pop()
        visited += 1
        if visited > _MAX_NODES:
            raise _ResultsBudgetExceeded(
                f"the document holds more than {_MAX_NODES} values"
            )
        if depth > _MAX_NESTING_DEPTH:
            raise _ResultsBudgetExceeded(
                f"the document nests deeper than {_MAX_NESTING_DEPTH} levels"
            )
        if isinstance(node, str):
            if len(node) > _MAX_STRING_CHARS:
                raise _ResultsBudgetExceeded(
                    f"a string of {len(node)} characters exceeds the "
                    f"{_MAX_STRING_CHARS}-character limit"
                )
        elif isinstance(node, dict):
            if len(node) > _MAX_COLLECTION_ITEMS:
                raise _ResultsBudgetExceeded(
                    f"a mapping of {len(node)} members exceeds the "
                    f"{_MAX_COLLECTION_ITEMS}-member limit"
                )
            for key, value in node.items():
                pending.append((key, depth + 1))
                pending.append((value, depth + 1))
        elif isinstance(node, list):
            if len(node) > _MAX_COLLECTION_ITEMS:
                raise _ResultsBudgetExceeded(
                    f"a list of {len(node)} members exceeds the "
                    f"{_MAX_COLLECTION_ITEMS}-member limit"
                )
            for item in node:
                pending.append((item, depth + 1))


def _read_results_bytes(path: Path) -> bytes:
    """Read the results artifact through the path authority, up to the budget.

    Two guarantees, and both are why this does not call
    :meth:`~pathlib.Path.read_text`.  The first is provenance:
    :func:`app.utils.open_artifact_read` opens every component from ``target``
    inward with ``O_NOFOLLOW`` under its verified parent and then ``fstat``\\ s
    the descriptor it holds, so a symbolic link anywhere in the path, an entry
    hard-linked to a file outside the artifact root, a directory and a FIFO are
    all refused - none of which a check on the pathname can establish, and any
    of which would otherwise reflect a file no run wrote through an
    unauthenticated viewer.  The second is the byte budget: the read is capped,
    and a file over the cap is refused rather than loaded.

    Args:
        path: The results artifact's own path, from the path module.

    Returns:
        The file's bytes, at most :data:`_MAX_RESULTS_BYTES` of them.

    Raises:
        _ResultsBudgetExceeded: If the file is larger than that.  One byte over
            the budget is read to detect the case, and is then discarded with
            the rest.
        app.utils.ArtifactPathError: If the path authority refuses the entry -
            a symbolic link, a hard-linked entry, a directory, a non-regular
            file.  It is an :exc:`OSError`, so :func:`_load_features` answers
            it with the same 404 as every other cause.
        OSError: If the artifact is absent, refused or otherwise unreadable.

    """
    with open_artifact_read(path) as handle:
        payload = handle.read(_MAX_RESULTS_BYTES + 1)
    if len(payload) > _MAX_RESULTS_BYTES:
        raise _ResultsBudgetExceeded(
            f"the results artifact is larger than {_MAX_RESULTS_BYTES} bytes"
        )
    return payload


def _load_features() -> list[Any]:
    """Parse the results artifact, or answer 404 for every reason it cannot be.

    The one data-availability rule, in one place, for all four report routes.
    Absent, unreadable, unparseable, over a resource budget and structurally
    wrong are one outcome here: a run has not produced usable results, and
    which of those it was is not the viewer's business to report.  The cause
    reaches the log, never the response - no exception text, no path, no hint -
    which is what keeps the four bodies ``app/errors.py`` renders
    byte-identical across the causes.

    The read itself is delegated to :func:`_read_results_bytes`, so the bytes
    this function parses came from a descriptor the path authority opened and
    verified rather than from a pathname, and there are at most
    :data:`_MAX_RESULTS_BYTES` of them.  Parsing is bounded too: the numeric
    hooks refuse an implausibly long token, and
    :func:`_enforce_structural_budgets` refuses a document too deep, too wide
    or too large in nodes or in any one string for a page to render.

    What the log carries is the artifact's own name, taken from the runtime
    path, and the exception itself through ``exc_info``.  The level separates
    the one ordinary cause from the anomalies: an artifact that has not been
    written yet is DEBUG, because a checkout where nothing has run is the
    viewer's normal state and every request for a report page would otherwise
    write to the error stream, while a refused read, an entry the path
    authority will not read through, an undecodable byte sequence, a document
    that does not parse, one over a budget and one that parses into something
    other than a list are WARNING - each means a run wrote something unusable,
    or something other than a run wrote it, and an operator can only explain
    the 404 if the record says which.

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
            absent, is refused by the path authority, cannot be read, cannot be
            decoded as UTF-8, does not parse as JSON, breaks one of this
            module's resource budgets, or parses as anything other than a list.

    """
    path = cucumber_json_path()
    try:
        payload = _read_results_bytes(path)
        # Decoded explicitly rather than by handing bytes to json.loads, so
        # that an undecodable file is reported as the decode failure it is.
        document = json.loads(
            payload.decode("utf-8"),
            parse_int=_bounded_int,
            parse_float=_bounded_float,
        )
        _enforce_structural_budgets(document)
    # OSError covers the absent file, the directory standing where the file
    # belongs, the unreadable parent, the denied permission and the path
    # authority's own ArtifactPathError alike; UnicodeDecodeError,
    # JSONDecodeError and _ResultsBudgetExceeded are all ValueError subclasses,
    # and ValueError also catches the numeric conversions CPython refuses
    # outright; RecursionError is what a pathologically nested document raises,
    # and a document too deep to parse is unparseable rather than a server
    # fault.
    except (
        OSError,
        ValueError,
        RecursionError,
    ) as exc:
        if isinstance(exc, (FileNotFoundError, NotADirectoryError)):
            # The pre-run state: nothing has written the artifact, or the
            # directory that holds it does not exist yet.  DEBUG, so a viewer
            # running against a fresh checkout does not fill the error stream.
            logger.debug(
                "Results artifact %r has not been produced yet; answering 404",
                path.name,
                exc_info=exc,
            )
        else:
            # A refused read, an entry the path authority will not read
            # through, a directory standing where the file belongs, an
            # undecodable byte sequence, a document that does not parse, one
            # nested too deep to parse and one over a resource budget.
            # WARNING: a run produced something unusable - or something that is
            # not a run's output is standing in its place - and this record is
            # the only place that says which.
            logger.warning(
                "Results artifact %r is unreadable, unparseable or outside "
                "this module's resource budgets; answering 404",
                path.name,
                exc_info=exc,
            )
        abort(404)

    if not isinstance(document, list):
        # Specification section 0.6 pins the top level as a list of feature
        # objects.  Anything else parsed cleanly but describes no run, which is
        # a malformed artifact rather than a pre-run absence - hence WARNING,
        # naming the type that arrived instead so the log distinguishes a
        # mapping from a bare scalar without quoting the document itself.
        logger.warning(
            "Results artifact %r parsed as %s rather than a list of features; "
            "answering 404",
            path.name,
            type(document).__name__,
        )
        abort(404)

    return document


def _artifact_modified() -> str | None:
    """The results artifact's modification time, for the report views.

    Opened through the path authority and ``fstat``\\ ed, exactly as the read
    that produced the document beside it: the time a report page shows is the
    time of the object the page's contents came out of, and an entry the
    authority refuses - a symbolic link, a hard-linked file, a directory -
    reports no time at all rather than the time of whatever it pointed at.

    Returns:
        Millisecond-precision UTC ISO-8601 ending in ``Z``, or ``None`` when the
        time cannot be read - which the three views render as an em dash.  The
        value is optional context, so its absence never affects a status code.

    """
    path = cucumber_json_path()
    try:
        with open_artifact_read(path) as handle:
            modified_iso, _ = _modification_times(handle, path.name)
    except (OSError, ValueError) as exc:
        # The same split the results read applies, and for the same reason: an
        # artifact that has not been written is the viewer's ordinary state, so
        # it is DEBUG, while a refused open, an entry the authority will not
        # read through and a directory standing where the file belongs are
        # anomalies an operator has to see.  Neither outcome affects the page's
        # status: the three views render an em dash where the time would be.
        if isinstance(exc, (FileNotFoundError, NotADirectoryError)):
            logger.debug(
                "Modification time unavailable for artifact %r: it is absent",
                path.name,
                exc_info=exc,
            )
        else:
            logger.warning(
                "Modification time unavailable for artifact %r",
                path.name,
                exc_info=exc,
            )
        return None
    return modified_iso


# --------------------------------------------------------------------------- #
# The model layer.  Every status, count, duration and timestamp the three
# pages and the summary route show is obtained from
# app/reporting/aggregation.py here and merely formatted for a template.
# Nothing below normalises a status, folds one, counts a step, sums a duration
# or chooses a timestamp: that module does all five, and a second
# implementation of any of them is precisely what put this viewer at odds with
# the generated artifacts - a failed after-hook read passed here and failed
# there, and an element with nothing in it read unknown here and passed there.
#
# Every read is total.  The artifact is what a reader turns to when a run has
# gone wrong, so a malformed member answers a neutral value rather than taking
# a page down, and the coercions are the authority's own - as_mapping, mappings
# and as_text - so a member a template declines to render cannot still reach a
# count and a count cannot include something no page shows.
# --------------------------------------------------------------------------- #


def _present_features() -> list[dict[str, Any]]:
    """Every feature the results artifact carries, decorated, in file order.

    Each member of the parsed list is coerced to a mapping **in its own
    position** and then decorated, so a feature that is not a mapping becomes
    an empty one and the list keeps its length.

    ``normalize_run`` and ``selected_features`` are deliberately **not**
    called, and this is the one place a reader will wonder why.  Both drop a
    feature left with no test case, and dropping one would renumber the
    zero-based positions specification section 0.3.1 fixes as the feature
    route's keys - *"keyed by the feature's zero-based index in the JSON
    list"*, with a 404 for an out-of-range index and for nothing else.  A
    feature present in the artifact would then be unreachable and its
    neighbours would answer at the wrong URL.  The artifact this reads is
    already selection-filtered by the JSON writer, which applies exactly the
    rule ``selected_features`` applies, so for any document a run produced the
    two lists are the same list; they differ only for a hand-built document,
    where keeping the position is the safer answer.

    Decoration on its own preserves order throughout: features stay in source
    order, each feature's elements stay exactly where the model put them, and
    each Background occurrence stays in its own position.

    Returns:
        One decorated feature mapping per member of the results list, each
        carrying ``status``, ``verdict``, ``duration_ns``, ``duration_samples``
        and ``stats``, over a decorated element list.

    Raises:
        werkzeug.exceptions.NotFound: Through :func:`_load_features`, if the
            results artifact is absent, unreadable, unparseable or not a list.

    """
    return [decorate_feature(as_mapping(member)) for member in _load_features()]


def _prose(value: Any) -> str:
    """One free-text field, untrimmed, or the empty string.

    Deliberately not :func:`as_text`: a description carries its own leading
    indentation and the stylesheet's pre-wrap rule preserves it, so trimming
    here would change what the page shows.  A non-string is still dropped -
    surfacing the characters ``None`` as a description would be worse than
    surfacing nothing.

    Args:
        value: A ``description``, or anything at all.

    Returns:
        The string as it stands, or ``""``.

    """
    return value if isinstance(value, str) else ""


def _integer_text(value: Any) -> str:
    """One integer field as text, or the empty string.

    The rule ``integer_value`` applies in ``view/feature.html``, in Python: a
    line number is tested for its *type* rather than its truth, since zero is
    a legitimate value, and a boolean is rejected because :class:`bool` is a
    subclass of :class:`int` and ``True`` would render as ``1``.  A caller
    renders the empty answer as an em dash.

    Args:
        value: A ``line``, or anything at all.

    Returns:
        The decimal text, or ``""``.

    """
    if isinstance(value, bool) or not isinstance(value, int):
        return ""
    return str(value)


def _tag_names(value: Any) -> list[str]:
    """The tag names one mapping declares, in order, blanks dropped.

    A feature tag is a mapping of name, type and location and a scenario tag
    carries a name alone; only the name is read from either and the leading
    ``@`` is kept.  A tags value that is not a list of mappings yields no tag
    rather than being iterated: iterating a string would yield characters and
    iterating a mapping would yield key names.

    Args:
        value: A ``tags`` value, or anything at all.

    Returns:
        The non-blank names, in document order.

    """
    return [
        name for name in (as_text(tag.get("name")) for tag in mappings(value)) if name
    ]


def _timestamp_text(value: Any) -> str:
    """One timestamp, but only when the authority can parse it.

    The page may show a timestamp exactly when ``GET /reports/summary`` would
    report one, which is what closes the gap a lexical acceptance left open: an
    unparseable ``start_timestamp`` used to be displayed while the summary
    route answered ``null`` for the same document.  ``parse_timestamp`` is the
    gate and ``display_timestamp`` supplies the text, so the value on the page
    is the artifact's own string, verbatim, and never a reformatting of it.

    Args:
        value: A timestamp string, or anything at all.

    Returns:
        The trimmed timestamp, or ``""`` - which every page renders as an em
        dash.

    """
    if parse_timestamp(value) is None:
        return ""
    return display_timestamp(value)


def _duration_text(source: Mapping[str, Any]) -> str | None:
    """The seconds figure for one decorated element or feature.

    Args:
        source: A mapping carrying the two keys decoration adds -
            ``duration_ns`` and ``duration_samples``.

    Returns:
        Seconds to three decimals, or ``None`` when there was no usable sample
        to sum - which is unknown rather than zero, and renders as an em dash.

    """
    return format_duration_seconds(
        source.get(_DURATION_NS_KEY),
        source.get(_DURATION_SAMPLES_KEY),
    )


def _run_duration_text(features: Sequence[Mapping[str, Any]]) -> str | None:
    """The whole run's duration, from the per-feature figures.

    A sum of values the authority computed, which is the one arithmetic this
    module performs: ``build_row_totals`` sums the nanoseconds a statistics
    table needs but carries no sample count, and the sample count is what
    separates a genuinely zero duration from a duration of nothing at all.

    Args:
        features: The decorated features, each carrying ``duration_ns`` and
            ``duration_samples``.

    Returns:
        Seconds to three decimals, or ``None`` when no feature carried a
        usable sample.

    """
    total_ns = sum(int(feature.get(_DURATION_NS_KEY, 0)) for feature in features)
    samples = sum(int(feature.get(_DURATION_SAMPLES_KEY, 0)) for feature in features)
    return format_duration_seconds(total_ns, samples)


def _element_kind(element: Mapping[str, Any]) -> str:
    """Which kind of block one element renders as.

    The discrimination is the authority's, and its asymmetry is deliberate:
    only an element declaring the type ``scenario`` is a scenario, while an
    element of an unexpected type is *rendered* - as :data:`_OTHER_ELEMENT_KIND`
    - because losing its results would be worse than showing it neutrally.

    Args:
        element: A decorated element mapping.

    Returns:
        One of the three kinds ``view/feature.html`` branches on.

    """
    if is_scenario_element(element):
        return _SCENARIO_ELEMENT_KIND
    if is_background(element):
        return _BACKGROUND_ELEMENT_KIND
    return _OTHER_ELEMENT_KIND


def _tally(tokens: Sequence[str]) -> dict[str, Any]:
    """One "by status" tally, from the authority's own counter.

    Used for both kinds of tally a page shows - the steps it renders and the
    statuses of its filterable units - so neither is counted by a loop of this
    module's own.

    Args:
        tokens: Normalised status tokens, one per counted thing: step statuses
            from :func:`app.reporting.aggregation.step_statuses`, or the
            ``status`` a decorated element or feature carries.

    Returns:
        The group ``count_group`` builds: ``total``, a ``by_status`` map
        carrying only the non-zero statuses in reading order, and those same
        statuses flattened onto the group for the artifact template that reads
        them that way.  A page reads the first two.

    """
    return count_group(list(tokens))


def _first_failed(tokens: Sequence[str]) -> int | None:
    """Where the first failed step of one element stands, or ``None``.

    The scenario page attaches the failure screenshot to that step, so the
    image sits with the assertion text it documents rather than in a region of
    its own.  The tokens are the authority's normalisation of the very steps
    the page renders, in the same order, so the position cannot point at a
    different row than the one that failed.

    Args:
        tokens: Normalised step statuses, one per rendered step, in order.

    Returns:
        The zero-based position of the first ``failed`` token, or ``None``
        when no step failed.

    """
    for position, token in enumerate(tokens):
        if token == _FAILED_STATUS:
            return position
    return None


def _filter_controls(counts: Mapping[str, int]) -> list[dict[str, Any]]:
    """The filter controls one page offers, in severity order.

    A control is offered only for a status the page's own filterable units
    actually carry: offering one no unit carries would give the reader a
    button that empties the page.

    Args:
        counts: A ``by_status`` map from :func:`app.reporting.aggregation.
            count_group`, whose keys are already normalised tokens.

    Returns:
        One ``{"token": …, "count": …}`` per status present, ordered by
        :data:`_FILTER_ORDER`.

    """
    return [
        {"token": token, "count": counts[token]}
        for token in _FILTER_ORDER
        if counts.get(token)
    ]


def _element_blocks(feature: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One render block per element of one feature, in source order.

    The scenario index is the sharpest rule on the feature page and it is
    applied here: the counter advances only on a scenario element, so a
    scenario's key is its position among the *scenarios* and not among the
    elements, which is what keeps the links this page emits and the scenario
    route's own lookup in agreement.  The repeated Background occurrences are
    neither reordered, deduplicated nor collapsed - each one genuinely ran.

    Args:
        feature: One decorated feature mapping.

    Returns:
        One block per element, each carrying its kind, the decorated element
        itself, its element position, its scenario index or ``None``, the step
        mappings the page renders, those steps' normalised statuses, the
        element's **own** status - the fold over its steps *and its hooks* - its
        start timestamp, its duration text and its step count.

    """
    blocks: list[dict[str, Any]] = []
    sindex = 0
    for position, element in enumerate(mappings(feature.get("elements"))):
        kind = _element_kind(element)
        blocks.append(
            {
                "kind": kind,
                "element": element,
                "position": position,
                "sindex": sindex if kind == _SCENARIO_ELEMENT_KIND else None,
                "steps": mappings(element.get("steps")),
                "tokens": step_statuses(element),
                "status": element[_STATUS_KEY],
                "started": _timestamp_text(element.get("start_timestamp")),
                "duration": _duration_text(element),
                "step_count": as_mapping(element.get(_STATS_KEY)).get("steps_total", 0),
            }
        )
        if kind == _SCENARIO_ELEMENT_KIND:
            sindex += 1
    return blocks


def _scenario_blocks(
    blocks: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """The blocks a reader can filter and follow: the scenario elements alone.

    Backgrounds interleave and repeat, so counting elements would count every
    feature's scenarios several times over, and a Background occurrence has no
    detail view of its own to link to.

    Args:
        blocks: One feature's blocks, from :func:`_element_blocks`.

    Returns:
        The scenario blocks, in source order, so a member's index in this list
        is its ``sindex``.

    """
    return [block for block in blocks if block["kind"] == _SCENARIO_ELEMENT_KIND]


def _feature_label(feature: Mapping[str, Any], findex: int) -> str:
    """The text that names one feature, wherever a page names it.

    A blank name would leave a link with no accessible name at all, so the
    source path stands in, and the feature's own zero-based position behind
    that - both values the results file itself carries, and neither of them a
    label invented for a product this port deliberately does not rename.

    Args:
        feature: A decorated feature mapping.
        findex: Its zero-based position, for the last fallback.

    Returns:
        The feature's name, its uri, or ``"Feature <findex>"``.

    """
    return (
        as_text(feature.get("name"))
        or as_text(feature.get("uri"))
        or f"Feature {findex}"
    )


def _overview_rows(features: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One row of the overview page per feature, in file order.

    Each row carries the feature twice over: the aggregate figures the
    statistics table shows, and the values that describe a feature rather than
    count it.  The aggregate half is the decorated feature's own - its status
    is the fold over its element statuses, hooks included, and its ``stats``
    are the statistics-table arithmetic the generated overview page states, so
    a row on this page and the corresponding row of the artifact cannot
    disagree.

    Args:
        features: The decorated features, in file order.

    Returns:
        One row per feature, keyed by ``findex`` - its zero-based position,
        which is the feature route's key and never the feature's own id, since
        two pairs of features in this suite share an id because they share a
        title.

    """
    return [
        {
            "findex": findex,
            "feature": feature,
            "label": _feature_label(feature, findex),
            "status": feature[_STATUS_KEY],
            "stats": as_mapping(feature.get(_STATS_KEY)),
            "duration": _duration_text(feature),
            "started": _timestamp_text(earliest_start([feature])),
            "uri": as_text(feature.get("uri")),
            "id": as_text(feature.get("id")),
            "line": _integer_text(feature.get("line")),
            "description": _prose(feature.get("description")),
            "tags": _tag_names(feature.get("tags")),
        }
        for findex, feature in enumerate(features)
    ]


def _summary_body(summary: Mapping[str, Any]) -> dict[str, Any]:
    """The published shape of ``GET /reports/summary``, from one tally.

    ``count_group`` deliberately answers in two shapes at once: the nested
    ``by_status`` map this route publishes, and the same per-status figures
    flattened onto the group, which ``app/templates/artifact/metadata.html``
    reads by looking each status up on the group itself.  Those flattened
    members are that template's, not this route's, so the two keys the
    contract fixes are projected out rather than the group being serialised as
    it stands - the body carries exactly ``total`` and ``by_status`` per group
    and nothing else, which is what ``tests/test_web_routes.py`` pins.

    The same projection reaches ``view/overview.html``, so the page and the
    route are two renderings of one ``build_summary`` result per request
    rather than two results that happen to agree.

    Args:
        summary: A :func:`app.reporting.aggregation.build_summary` result.

    Returns:
        ``{"features": {...}, "scenarios": {...}, "steps": {...},
        "start_timestamp": …}``, each group carrying the total and the
        non-zero statuses in reading order.

    """
    body: dict[str, Any] = {}
    for group in SUMMARY_GROUPS:
        counted = as_mapping(summary.get(group))
        body[group] = {
            SUMMARY_TOTAL_KEY: counted.get(SUMMARY_TOTAL_KEY, 0),
            SUMMARY_BY_STATUS_KEY: counted.get(SUMMARY_BY_STATUS_KEY, {}),
        }
    body[SUMMARY_START_KEY] = summary.get(SUMMARY_START_KEY)
    return body


# --------------------------------------------------------------------------- #
# Positional lookup.  Both keys are positions and neither is an identifier
# slug: two pairs of features in this suite share an id because they share a
# title, and keying on that would serve one member of a pair in place of the
# other.  The collision is source behaviour the port preserves.
# --------------------------------------------------------------------------- #


def _feature_at(
    features: Sequence[Mapping[str, Any]],
    findex: int,
) -> Mapping[str, Any]:
    """The feature at one zero-based position, or 404.

    Args:
        features: The decorated features, in the order the results list
            carries them - the positions :func:`_present_features` preserves.
        findex: The requested position.

    Returns:
        The decorated feature mapping.

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


def _scenario_at(
    blocks: Sequence[Mapping[str, Any]],
    sindex: int,
) -> tuple[Mapping[str, Any], Mapping[str, Any] | None]:
    """One scenario block by its position among the scenario blocks.

    The index counts scenario elements alone.  Backgrounds interleave and
    repeat, so an index over the element list would serve a background where a
    scenario was asked for and would land every link one element early.

    Args:
        blocks: One feature's blocks, from :func:`_element_blocks`.
        sindex: The requested position among the scenario blocks.

    Returns:
        ``(scenario, background)``.  The background is the block immediately
        preceding this scenario when that block is one, and ``None``
        otherwise: each repeated occurrence belongs to the scenario it
        preceded, so no distant copy is substituted and none is synthesized.
        That is the pairing ``element_units`` makes in the authority, over the
        same mapping-only element list, so the page and the generated
        artifacts group a Background with the same scenario.

    Raises:
        werkzeug.exceptions.NotFound: Through ``abort(404)``, if the position is
            negative or past the last scenario element - which includes every
            position of a feature carrying no scenario at all.

    """
    scenarios = _scenario_blocks(blocks)
    if sindex < 0 or sindex >= len(scenarios):
        logger.debug("Scenario position out of range")
        abort(404)

    scenario = scenarios[sindex]
    position = int(scenario["position"])
    background: Mapping[str, Any] | None = None
    if position > 0:
        preceding = blocks[position - 1]
        if preceding["kind"] == _BACKGROUND_ELEMENT_KIND:
            background = preceding
    return scenario, background


# --------------------------------------------------------------------------- #
# The artifact route's open-and-serve.  The allowlist, the traversal decision
# and the open all belong to app/utils/paths.py: the raw request segment goes
# to open_resolved_artifact, which validates and opens in one operation, and
# what comes back is a held stream over the object that was checked.  Nothing
# here normalises the segment, nothing here falls back to another artifact, and
# the object is never named again once it is open.
#
# What is added is a second, independent containment check over the path the
# authority returned, because a traversal defect on this route is a real
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
        path: The path the artifact authority returned for an allowlisted
            request - already resolved by it, and resolved again here so that
            this judgement rests on no other function's promise.  Nothing is
            opened: the bytes a reader receives come from a descriptor that is
            already held, so all this decision can do is refuse.

    Returns:
        ``True`` only for a path that resolves inside the artifact root and not
        inside the worker-intermediates directory.  A path that cannot be
        resolved at all - one carrying an embedded null byte raises
        ``ValueError``, an unreadable or over-long one raises ``OSError`` - is
        not contained, and so is one whose components have been replaced since
        the authority resolved them: the answer fails closed.

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


def _served_artifact(name: str) -> Response:
    """Open one allowlisted artifact and build the response that streams it.

    The raw request segment goes to :func:`app.utils.open_resolved_artifact`
    unaltered - not stripped, not collapsed, not rewritten - which applies the
    whole allowlist and, in the same operation, opens the file it validated
    under a held directory descriptor with ``O_NOFOLLOW``.  There is therefore
    no window between the check and the open for a component to be swapped into
    (CWE-367), and no second open at all: the response streams the descriptor
    that was verified, so a pathname that changes afterwards cannot change what
    is served, and an artifact that becomes unreadable after validation can no
    longer turn a 404 into a 500 - there is nothing left to fail.

    The resolved path is put to two uses and no third.  One is metadata: the
    download name, which is where the framework's media type is derived from,
    while the content length comes through the descriptor.  The other is
    refusal - it is handed to :func:`_within_artifact_root`, which resolves it,
    the artifact root and the worker-intermediates directory in order to judge
    containment, and whose only possible effect is a 404.  Neither use can
    obtain a byte: the response streams the descriptor, and a pathname that
    changes afterwards changes nothing about what is served.

    Conditional and range responses are deliberately not requested: the
    framework derives them from a pathname's ``stat``, which is exactly the
    second look at the filesystem this route no longer takes, so
    ``Last-Modified`` and ``Content-Length`` are supplied from
    :func:`os.fstat` on the held descriptor instead.

    Args:
        name: The raw request segment, untrusted, passed through as it arrived.

    Returns:
        A streaming response over the open artifact.  The framework's file
        wrapper closes the stream when the response closes; every path that
        does not reach a response closes it here.

    Raises:
        werkzeug.exceptions.NotFound: Through ``abort(404)``, for every
            rejection: a name off the allowlist, an absent file, a directory,
            a symbolic link, a hard-linked entry, a path that resolves outside
            the artifact root, and any worker-intermediates path.  Never a 403,
            and the response carries neither the rejected name nor any
            filesystem path - reflecting either would confirm the layout to a
            prober and separate a probe from an honest mistake.  The reason is
            logged instead, with the name rendered through ``%r`` so that a
            control character in a crafted request cannot forge a second log
            line.

    """
    opened = open_resolved_artifact(name)
    if opened is None:
        logger.debug("Artifact request %r is not an allowlisted artifact", name)
        abort(404)

    resolved, handle = opened
    try:
        if not _within_artifact_root(resolved):
            # Unreachable through the path module's own validation, and checked
            # anyway: this is the one route that turns request input into a
            # filesystem read, so its containment does not rest on a single
            # implementation.  It judges the path the authority returned, with
            # this module's own idea of where the artifact root and the worker
            # intermediates are, and it opens nothing - the object being served
            # is the descriptor above.  WARNING rather than DEBUG, because
            # reaching here means one of the two checks disagreed with the
            # other.
            logger.warning(
                "Artifact request %r resolves outside the artifact root or "
                "into the worker intermediates",
                name,
            )
            abort(404)
        # One fstat, on the descriptor being served, for both headers: the
        # length and the instant then describe the same object, which a stat of
        # the pathname could not promise.
        info = os.fstat(handle.fileno())
        response = send_file(
            handle,
            download_name=resolved.name,
            conditional=False,
            last_modified=info.st_mtime,
        )
    except BaseException:
        # Every exit that is not the response closes the stream, the two
        # abort() calls above included: abort raises, so without this the
        # descriptor would be left to the garbage collector on exactly the
        # paths a prober can drive repeatedly.  BaseException rather than
        # Exception, so an interrupt does not leak one either.
        handle.close()
        raise
    # Set here rather than passed in: the framework derives a length from a
    # path's stat, and this route hands it a stream, so the length it would
    # otherwise omit comes from the fstat above.
    response.content_length = info.st_size
    return response


def _redirect_to_tree_overview(name: str) -> Response:
    """Answer a report-tree directory request with the nested overview's URL.

    The generated tree's pages reference their stylesheets, scripts, images and
    each other **relatively**, so what the browser has to end up with is a base
    URL inside the tree.  Serving the overview page's bytes at the directory
    alias leaves the base URL at the alias instead, one level too high, and
    every one of those references then 404s - the artifact is unusable exactly
    as delivered.  A redirect fixes the base URL, which is why this route
    canonicalizes the alias rather than serving through it.

    The redirect is only offered once the request has been established as
    servable, and it is established the way :func:`_served_artifact`
    establishes it: the **raw** segment goes to
    :func:`app.utils.open_resolved_artifact`, unaltered, and that function's
    own allowlist is what maps the directory key to the page inside the tree.
    So the alias decision is made in one place, by the authority that
    documents it, and this function adds no rewrite of its own - a tree whose
    overview page was never written stays a 404 rather than becoming a
    redirect into one.

    Args:
        name: The raw request segment, which is one of
            :data:`_PRETTY_DIRECTORY_NAMES`.  It is validated and opened as it
            arrived; the redirect's destination is built separately, from the
            path module's constants.

    Returns:
        A 302 to ``web.artifact`` at :data:`_PRETTY_OVERVIEW_ROUTE_NAME`, built
        with ``url_for`` like every other URL in this port.  That is the URL
        the page's bytes are served at, and the only one they are served at.

    Raises:
        werkzeug.exceptions.NotFound: Through ``abort(404)``, if the request
            cannot be opened - an absent overview page, a refused one, or one
            that is not a lone regular file.  The same plain 404 as every
            other rejection.

    """
    opened = open_resolved_artifact(name)
    if opened is None:
        logger.debug(
            "Artifact request %r names the report tree, whose overview page "
            "is not servable",
            name,
        )
        abort(404)
    # The handle was taken to answer one question - is that page servable now -
    # and this response carries no body of its own, so it is closed here.
    opened[1].close()
    return redirect(url_for("web.artifact", name=_PRETTY_OVERVIEW_ROUTE_NAME))


def index() -> str:
    """Servability and modification time of each of the four report artifacts.

    The one route the data-availability rule does not govern: it answers **200
    always**, including on a checkout where the artifact root has never
    existed, since that directory is generated output and the landing page has
    to be useful before anything has ever run.  An artifact this viewer cannot
    serve is reported as unavailable rather than as a failure, and the
    per-worker intermediates are neither listed nor examined - they are never
    reachable over HTTP.

    What each row is advertised on is the artifact route's own answer for it,
    obtained from the artifact route's own opener, so every link this page
    offers resolves and an artifact that is on disk but unservable - a symbolic
    link, a hard-linked file, a report tree missing its overview page - is
    listed exactly as one that has not been written.

    No result counts are computed here; those belong to ``/reports`` and
    ``/reports/summary``.

    Returns:
        The rendered landing page, always at status 200.

    """
    return render_template(_INDEX_TEMPLATE, artifacts=_describe_artifacts())


def reports_overview() -> str:
    """The run overview, over the one normalized result model.

    Every figure on the page is obtained here, from
    ``app/reporting/aggregation.py``, and the template formats it: one row per
    feature in file order - carrying the decorated feature's own status and its
    own statistics figures - and, above the table, **the same ``build_summary``
    result ``GET /reports/summary`` answers with**.  One tally per request
    reaches both surfaces, so the page and that route cannot state different
    numbers for one run, and neither can contradict the generated HTML
    artifacts, which render the same model.

    The zero-based row positions are the feature route's keys, and no feature
    is filtered out of the list; see :func:`_present_features`.

    Returns:
        The rendered overview.

    Raises:
        werkzeug.exceptions.NotFound: If the results artifact is absent,
            unreadable, unparseable or not a list.  An empty list is a success.

    """
    features = _present_features()
    summary = _summary_body(build_summary(features))
    rows = _overview_rows(features)
    return render_template(
        _OVERVIEW_TEMPLATE,
        rows=rows,
        summary=summary,
        run_duration=_run_duration_text(features),
        run_started=_timestamp_text(summary[SUMMARY_START_KEY]),
        filters=_filter_controls(
            _tally([row["status"] for row in rows])[SUMMARY_BY_STATUS_KEY]
        ),
        artifact_modified=_timestamp_text(_artifact_modified()),
    )


def report_feature(findex: int) -> str:
    """One feature, addressed by its zero-based position in the results file.

    Keyed by position and never by the feature's own identifier: two pairs of
    features in this suite share an identifier slug because they share a title,
    and an identifier used as a link key would serve one member of a pair in
    place of the other.  No identifier lookup, redirect or de-duplication is
    offered here - the collision is source behaviour the port preserves.

    Every aggregate the page shows comes from the authority: the feature's own
    status and statistics figures, and one block per element in source order
    carrying **that element's own** status - the fold over its steps *and its
    hooks*, so a scenario whose after-hook failed is badged failed here exactly
    as it is in the generated artifacts and in ``GET /reports/summary``.

    Args:
        findex: The feature's zero-based position.

    Returns:
        The rendered feature page, with the decorated feature, its element
        blocks and its index.

    Raises:
        werkzeug.exceptions.NotFound: If the results artifact is unusable, or
            the position lies outside the feature list.

    """
    feature = _feature_at(_present_features(), findex)
    blocks = _element_blocks(feature)
    scenarios = _scenario_blocks(blocks)
    stats = as_mapping(feature.get(_STATS_KEY))
    return render_template(
        _FEATURE_TEMPLATE,
        feature=feature,
        findex=findex,
        blocks=blocks,
        feature_status=feature[_STATUS_KEY],
        feature_stats=stats,
        feature_duration=_duration_text(feature),
        feature_started=_timestamp_text(earliest_start([feature])),
        # Every step the page renders, counted once by the authority's own
        # counter: the elements it lists are exactly the ones these tokens
        # come from, so the tally and the step rows below it are one reading.
        step_tally=_tally(
            [token for block in blocks for token in block["tokens"]]
        ),
        scenario_count=len(scenarios),
        filters=_filter_controls(
            _tally([block["status"] for block in scenarios])[SUMMARY_BY_STATUS_KEY]
        ),
        artifact_modified=_timestamp_text(_artifact_modified()),
    )


def report_scenario(findex: int, sindex: int) -> str:
    """One scenario, addressed by two positional keys.

    ``sindex`` counts the feature's **scenario elements** alone, so the
    backgrounds that interleave and repeat never consume an index - the same
    position ``view/feature.html`` links by, because both pages read the
    blocks :func:`_element_blocks` builds.  The background immediately
    preceding the scenario is passed alongside it when there is one.

    **The background's status and duration are its own, and so are the
    scenario's**: each element is graded by the authority from its own steps
    and its own hooks, so a failed after-hook shows on the scenario's badge
    while a Background failure stays on the Background's, exactly as the
    generated artifacts grade them.

    The element mappings themselves are handed to the template as the model
    carries them.  The shared partials read the artifact's own keys - a step
    keyword with its trailing space, a match location and its arguments, a
    nanosecond-integer duration, the untruncated error text, and a screenshot
    embedding's mime type, data and name - so renaming a key, reformatting a
    duration or pre-rendering error text here would break them and would put
    this view at odds with the two generated HTML artifacts.

    Args:
        findex: The feature's zero-based position in the results file.
        sindex: The scenario's zero-based position among that feature's
            scenario elements.

    Returns:
        The rendered scenario page, carrying the background immediately
        preceding the scenario when that element is one.

    Raises:
        werkzeug.exceptions.NotFound: If the results artifact is unusable, or
            either position is out of range.

    """
    feature = _feature_at(_present_features(), findex)
    blocks = _element_blocks(feature)
    scenario, background = _scenario_at(blocks, sindex)

    scenario_tokens = list(scenario["tokens"])
    background_tokens = list(background["tokens"]) if background else []
    # The filter's unit on this page is the step, and it acts on every step the
    # page renders - the background's included - so this tally spans both
    # elements while the summary in the header counts the scenario's own.  It
    # is counted once and read twice, by the controls and by their labels.
    page_tally = _tally(background_tokens + scenario_tokens)

    return render_template(
        _SCENARIO_TEMPLATE,
        feature=feature,
        findex=findex,
        sindex=sindex,
        scenario=scenario["element"],
        scenario_status=scenario["status"],
        scenario_steps=scenario["steps"],
        scenario_started=scenario["started"],
        scenario_duration=scenario["duration"],
        scenario_tally=_tally(scenario_tokens),
        background=background["element"] if background else None,
        background_status=background["status"] if background else "",
        background_steps=background["steps"] if background else [],
        background_duration=background["duration"] if background else None,
        background_tally=_tally(background_tokens),
        page_tally=page_tally,
        filters=_filter_controls(page_tally[SUMMARY_BY_STATUS_KEY]),
        failure_position=_first_failed(scenario_tokens),
        total_scenarios=len(_scenario_blocks(blocks)),
        artifact_modified=_timestamp_text(_artifact_modified()),
    )


def reports_summary() -> Response:
    """Counts of features, scenarios and steps by status, and the run's start.

    The only route whose success body is JSON.  That body carries four members:
    a ``features``, ``scenarios`` and ``steps`` block, each
    ``{"total": …, "by_status": {…}}`` with the total always present and the map
    holding only the non-zero statuses, and ``start_timestamp``, the run's
    earliest scenario start time as the artifact's own string or ``null``.

    The rules are ``app/reporting/aggregation.py``'s ``build_summary``, which
    is the same call ``/reports`` renders from, so the page and this route
    cannot disagree.  Three of them are worth stating on the route itself,
    because they are the ones a reader would otherwise have to infer:

    * **Background steps are counted** in the step totals, because a repeated
      background genuinely ran once per scenario.  A hook is never a step.
    * A scenario's status is its **own** steps *and its own hooks*, so a
      background failure is not reported as a scenario failure while a failed
      after-hook is.
    * A feature's status is the fold over its element statuses, backgrounds
      included, so a background-only failure moves a feature's status without
      moving any scenario's.

    The body is projected from that result rather than serialised from it:
    ``count_group`` also flattens each group's per-status figures onto the
    group itself for ``app/templates/artifact/metadata.html``, and those
    members are that template's contract rather than this route's; see
    :func:`_summary_body`.

    This is the one route whose success body is JSON, and it is governed by the
    same data-availability rule as the HTML report routes; because it shares
    :func:`_load_features`, ``app/errors.py`` recognises the endpoint and gives
    its 404 a JSON body too.

    Returns:
        The summary as JSON, at status 200.  An empty feature list answers
        three zero totals, three empty maps and ``null``.

    Raises:
        werkzeug.exceptions.NotFound: If the results artifact is absent,
            unreadable, unparseable or not a list.  ``app/errors.py``
            recognises this endpoint and gives that 404 a JSON body too.

    """
    return jsonify(_summary_body(build_summary(_present_features())))


def artifact(name: str) -> Response:
    """Serve one allowlisted artifact, and nothing else.

    The allowlist is the three artifact files and any path beneath the report
    tree.  The two spellings AAP 0.3.1 authorizes for the tree's own directory
    - its bare key and that key with exactly one trailing slash - are
    canonicalized with a 302 to the nested overview page's URL, so that the
    page arrives under a base URL its relative references resolve against.
    Everything else is a plain 404: any other directory, any absent file, any
    path resolving outside the artifact root, any worker-intermediates path,
    and every other trailing-separator spelling, ``cucumber//`` and
    ``cucumber///`` among them.  Nothing about the requested name is
    normalised here and there is no fallback from one artifact to another - the
    raw segment goes to the path authority, which is the only thing that
    decides what it addresses.

    ``merge_slashes`` is off so that a doubled separator, which is how an
    absolute path arrives at this rule, is answered 404 by the router rather
    than redirected: *everything else* means every rejection is the same 404.

    The results artifact is servable here even while ``/reports`` answers 404 for
    it, because an unparseable file is still a downloadable one; this route
    therefore never parses what it serves.

    Args:
        name: The requested artifact path, relative to the artifact root,
            untrusted and passed on unaltered.

    Returns:
        The artifact, streamed from the descriptor the path authority opened
        and verified, with its media type derived from that file's own name.
        Nothing is read into memory here.  Or, for the two directory
        spellings, a 302 to the nested overview page.  Either response carries
        the blueprint's cache policy, applied by :func:`_no_store` after this
        function returns: the results file, the rerun manifest and the report
        pages are run evidence, and a copy of one held in a browser's cache
        would outlive the artifacts the clean step removes.

    Raises:
        werkzeug.exceptions.NotFound: Through :func:`_served_artifact` or
            :func:`_redirect_to_tree_overview`, for every rejection,
            disclosing no filesystem path.

    """
    if name in _PRETTY_DIRECTORY_NAMES:
        return _redirect_to_tree_overview(name)
    return _served_artifact(name)


# --------------------------------------------------------------------------- #
# Cache policy.  One rule for every response this blueprint produces, because
# every one of them carries run evidence: a report page quotes the step text
# and the assertion text of a suite whose fixtures are credentials, a scenario
# page embeds the failure screenshot, and the artifact route serves the results
# file itself.  A run's artifacts are removed by the clean step; a copy left in
# a browser's cache or a private intermediary's outlives them (CWE-525).
# --------------------------------------------------------------------------- #

#: What every viewer response tells a cache, and it is deliberately
#: ``no-store`` rather than ``no-cache``: RFC 9111 makes ``no-cache`` a
#: revalidation requirement, so the representation is still *written to disk*
#: and only checked before reuse, while ``no-store`` forbids keeping it at all.
#: ``private`` bars a shared cache from holding it even where a proxy ignores
#: the first directive, and ``max-age=0`` is the same statement for a cache
#: that predates them both.
_CACHE_CONTROL_POLICY: Final[str] = "no-store, private, max-age=0"

#: The HTTP/1.0 spelling, for an intermediary that understands nothing newer.
#: Both headers are stated because either one alone leaves a real class of
#: cache unaddressed.
_PRAGMA_POLICY: Final[str] = "no-cache"

#: The two header names, written once so the hook below cannot spell either of
#: them differently from the tests that read them.
_CACHE_CONTROL_HEADER: Final[str] = "Cache-Control"
_PRAGMA_HEADER: Final[str] = "Pragma"


def _no_store(response: Response) -> Response:
    """Apply the no-store policy to one response, whatever produced it.

    Registered on the blueprint rather than on the application, which is what
    makes the scope exactly right: it sees every response of the six views -
    the five pages, the JSON summary and the served artifact alike - and it
    does not see Flask's own packaged-static route, whose stylesheet and
    script carry no run evidence and stay ordinarily cacheable.

    The header is **assigned**, not appended, so the ``no-cache`` that
    ``send_file`` sets on the artifact response is replaced rather than joined:
    a response advertising both would leave the weaker directive in force for
    a cache that read it first.

    Args:
        response: The response the view produced, or the one the framework
            produced for a ``HEAD`` or ``OPTIONS`` request derived from it.

    Returns:
        The same response, carrying the policy.  Nothing else about it is
        touched - no validator is removed and no status is changed - because
        conditional revalidation is the framework's business and this is a
        storage prohibition.

    """
    response.headers[_CACHE_CONTROL_HEADER] = _CACHE_CONTROL_POLICY
    response.headers[_PRAGMA_HEADER] = _PRAGMA_POLICY
    return response


def register_routes(blueprint: Blueprint) -> None:
    """Bind the six views above to ``blueprint``, and nothing else to it.

    The six rows of specification section 0.3.1's route table, in the table's
    own order, added with ``add_url_rule`` rather than with a decorator so that
    this module needs no reference to the blueprint at import time - the
    inversion that keeps its import edge with the package running one way.
    Three properties are obtained rather than restated: each endpoint defaults
    to ``view_func.__name__``, so the names the templates resolve with
    ``url_for`` cannot drift from the functions they address; no ``methods`` is
    passed, so every rule answers ``GET`` with ``HEAD`` and ``OPTIONS`` and no
    route accepts a method that could write; and ``merge_slashes=False`` on the
    artifact rule alone has the router answer 404 for a doubled separator -
    how an absolute path arrives at that rule - instead of redirecting it.

    Four properties are load-bearing and are obtained rather than restated:

    * **Endpoint names.** ``add_url_rule`` defaults each endpoint to
      ``view_func.__name__``, which yields ``index``, ``reports_overview``,
      ``report_feature``, ``report_scenario``, ``reports_summary`` and
      ``artifact`` under the blueprint's ``web.`` prefix.  No endpoint string
      is written out here, so the names the six templates resolve with
      ``url_for`` cannot drift from the functions they address.
    * **Methods.** None is passed, so Flask keeps its default: each rule
      answers ``GET``, with ``HEAD`` and ``OPTIONS`` derived from it.  No route
      accepts a method that could write, which is the read-only guarantee
      expressed at the router.
    * **Slash merging.** ``merge_slashes=False`` on the artifact rule only.  A
      doubled separator - how an absolute path arrives at that rule - is
      answered 404 by the router instead of redirected, so that *everything
      else is 404* holds for that route without exception.  The other five
      rules keep Flask's default, under which the router may redirect a
      doubled separator to the merged path instead of refusing it.
    * **Cache policy.** :func:`_no_store` is registered here, on the
      blueprint, so it reaches every response of these six rules and no
      other: Flask's packaged-static route belongs to the application rather
      than to this blueprint and keeps its own ordinary cacheability, which is
      what makes "nothing carrying run evidence is stored" a scope rather than
      a blanket.

    Called exactly once per interpreter, from ``app/web/__init__.py``'s module
    body, immediately after the blueprint is constructed; module caching makes
    that a single call.  Registering the blueprint on an *application* is a
    separate act and stays the sole responsibility of ``create_app()``
    (specification sections 0.3.3 and 0.4.2) - this function touches no
    application, and calling it does not make any application serve anything.

    Args:
        blueprint: The blueprint to bind the views to, normally the package's
            ``web_bp``.  It must not already carry these rules: they are added
            unconditionally, so calling this twice on one blueprint would
            register each rule twice.  The blueprint is mutated in place.

    """
    blueprint.add_url_rule("/", view_func=index)
    blueprint.add_url_rule("/reports", view_func=reports_overview)
    blueprint.add_url_rule(
        "/reports/features/<int:findex>",
        view_func=report_feature,
    )
    blueprint.add_url_rule(
        "/reports/features/<int:findex>/scenarios/<int:sindex>",
        view_func=report_scenario,
    )
    blueprint.add_url_rule("/reports/summary", view_func=reports_summary)
    blueprint.add_url_rule(
        "/artifacts/<path:name>",
        view_func=artifact,
        merge_slashes=False,
    )
    # Every response of those six rules, and no response of any other rule.
    # An unmatched URL never reaches a blueprint hook at all, which is why
    # app/errors.py states the same policy on the bodies it renders.
    blueprint.after_request(_no_store)
    # DEBUG, and emitted once at import: an operator chasing a missing endpoint
    # can see which blueprint the surface was bound to, while a normally
    # configured process never prints it.
    logger.debug("Read-only viewer routes bound to blueprint %r", blueprint.name)
