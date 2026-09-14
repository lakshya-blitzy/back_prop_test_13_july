"""The one normalised result model every report surface reads.

Source anchor: the aggregation the report surfaces used to each perform for
themselves -- ``app/reporting/html_report.py``'s ``build_summary``,
``app/reporting/pretty_reports.py``'s ``_tag_row``, the derivations inside
``app/templates/pretty/*.html`` and ``app/templates/view/*.html``, and
``app/web/routes.py``'s ``_summarize``.  AAP 0.3.4 states the invariant those
copies were meant to satisfy -- *"Both HTML outputs and the HTTP views render
over one normalized result model \u2026 so no view contradicts an artifact"* -- and
0.4.2 repeats it as a cross-file invariant.  Six independent implementations of
one calculation cannot satisfy it, and they did not: a step-less Background
counted as *passed* in the single-page writer and *unknown* in
``GET /reports/summary``; hook statuses reached some Pretty surfaces and not
others; ``ambiguous`` folded onto Undefined on the features overview and
nowhere else; and failed scenarios were derived as *total minus passed* on one
page and as *a literal failed token* on the next.

This module is that model.  **It is the only place a status is normalised, a
status is folded, a step is counted, a duration is summed or a run's start is
chosen.**  Every other module and template is a consumer: it reads the values
computed here and formats them.

Two readings of one run, and why both are needed
------------------------------------------------
The two HTML artifacts come from two different generators with two different
status semantics, and reproducing both is the point of the port rather than an
inconsistency:

``status``  -- the **severity fold**
    ``failed > undefined > ambiguous > pending > skipped > untested > passed``,
    which is the conventional Cucumber precedence and the reading the
    single-page artifact, the HTTP views and ``GET /reports/summary`` present:
    a scenario whose steps were skipped reads *skipped*, not merely *not
    passed*.  Nothing in the Java sources pins an ordering -- the step classes
    compute no aggregate status at all -- so the order is declared once, in
    :data:`STATUS_PRECEDENCE`, and shared.

``verdict`` -- the **binary PrettyReports verdict**, ``passed`` or ``failed``
    ``net.masterthought:cucumber-reporting:5.6.1`` has no third answer, and
    that is measured from its bytecode rather than inferred:
    ``StatusCounter.finalStatus`` starts at ``PASSED`` and
    ``incrementFor(status)`` moves it to ``FAILED`` the moment any counted
    status is not ``PASSED``, so ``getFinalStatus()`` is binary and **an empty
    counter answers PASSED**.  ``Element.setMetaData`` builds one counter per
    group -- ``steps``, ``before``, ``after`` -- and
    ``calculateElementStatus()`` folds those three, so an element is
    ``PASSED`` only when its steps *and* both hook groups are.
    ``TagObject.addElement`` then counts ``getPassedScenarios()`` as the
    elements whose status is ``PASSED`` and ``getFailedScenarios()`` as the
    rest, which is why a row's failed count is *total minus passed* and not a
    search for a failed step.

The empty-element case is the same in both readings and it is not a choice:
``EmployeeFc.feature`` declares a Background with an empty body, an empty
``StatusCounter`` answers ``PASSED``, and the reference generator renders each
of those step-less occurrences as passed.  :data:`EMPTY_ELEMENT_STATUS` is
therefore ``"passed"`` for every surface, which is the disagreement between the
writer and the summary route settled in the writer's favour, with the bytecode
as the evidence.

What counts as what
-------------------
* **Steps are steps.**  The five step columns and the step duration read
  ``element["steps"]`` only.  A hook is never counted as a step and a hook's
  duration is never added -- ``TagObject.addElement`` sums
  ``Step.getDuration()`` alone, so the after-hook that carries a failure
  screenshot does not lengthen its scenario.
* **Hooks decide a verdict.**  Both hook groups take part in
  :func:`element_status` and :func:`element_verdict` and in nothing else.
* **Only an element typed ``scenario`` is a scenario.**  A Background
  occurrence repeats once per scenario, so counting elements would double
  every feature's scenarios.  An element of an unexpected type is *rendered*
  as a test case -- :func:`app.reporting.events.new_element` records one as a
  scenario rather than losing its results -- but it is not *counted* as one.
* **``ambiguous`` folds onto ``undefined`` for counting only.**
  5.6.1's ``StatusDeserializer`` holds ``UNKNOWN_STATUSES = ["ambiguous"]`` and
  maps it to ``UNDEFINED`` before any counting happens, so the fold belongs in
  :func:`counter_token` and never in the severity precedence, where
  ``ambiguous`` keeps its own rank.
* **A status the model never produced is not a pass.**  Anything unrecognised
  normalises to :data:`UNKNOWN_STATUS`, which no step column names, so it
  counts towards a total and towards no column -- and it can never make a
  verdict ``passed``.

Selection is applied once, here
------------------------------
A scenario the tag expression did not select never started, the JVM emitted no
test case for it, and the merged JSON report does not carry it.
:func:`selected_features` applies exactly the rule
``app/reporting/cucumber_json.py`` applies -- drop a Background-plus-scenario
unit whose members are not selected, then drop a feature left with no test case
at all -- so the JSON artifact, both HTML artifacts and the viewer describe the
same run.  Under the default ``@Smoke`` filter that is what reduces the suite's
ten features to one.

Boundaries and behaviour
------------------------
Imports are the standard library and :mod:`app.reporting.events`, which owns
the document's schema, and nothing else: no Flask, no Jinja, no Selenium, no
service, no path accessor -- this module computes numbers and resolves no
destination.  Consumers import it directly (``from app.reporting.aggregation
import \u2026``); it is deliberately absent from the package barrel, whose own rule
is that a name is added there only when a consumer imports it *through* the
barrel.

**Nothing is mutated and nothing raises on a test outcome.**  Every read of
the result document goes through a total coercion, so a malformed document
yields a poorer report rather than an exception -- ``testFailureIgnore`` is
true in the retained build file and all six publisher thresholds are ``-1``,
so a failed, undefined, pending or skipped scenario is data to count.
Decoration copies: the same merged document is handed to all four writers, and
a writer that edited it in place would change what the others see.

Determinism is structural (AAP 0.6).  Features stay in source order, elements
stay exactly where the model puts them, each Background occurrence repeats in
its own position, and every derived collection is built in a fixed order.
Nothing here sorts: ``sortingMethod: 'ALPHABETICAL'`` is a publisher display
option and imposes nothing on an artifact.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from app.reporting.events import (
    ELEMENT_TYPE_BACKGROUND,
    ELEMENT_TYPE_SCENARIO,
    JsonDict,
    ResultSet,
)
from app.reporting.screenshots import normalize_element_attachments

__all__ = [
    "AMBIGUOUS_STATUS",
    "COUNT_KEYS",
    "COUNTED_STEP_STATUSES",
    "EMPTY_AGGREGATE_STATUS",
    "EMPTY_ELEMENT_STATUS",
    "HOOK_KEYS",
    "KNOWN_STATUSES",
    "STATUS_PRECEDENCE",
    "STATUS_READING_ORDER",
    "SUMMARY_BY_STATUS_KEY",
    "SUMMARY_GROUPS",
    "SUMMARY_START_KEY",
    "SUMMARY_TOTAL_KEY",
    "UNDEFINED_STATUS",
    "UNKNOWN_STATUS",
    "VERDICT_FAILED",
    "VERDICT_PASSED",
    "RunAggregate",
    "as_mapping",
    "as_text",
    "build_row_totals",
    "build_summary",
    "build_tag_rows",
    "count_group",
    "count_steps",
    "counter_token",
    "decorate_element",
    "decorate_feature",
    "decorated_features",
    "display_timestamp",
    "duration_sample",
    "earliest_start",
    "element_duration",
    "element_duration_ns",
    "element_status",
    "element_steps_status",
    "element_units",
    "element_verdict",
    "feature_status",
    "feature_verdict",
    "format_duration_seconds",
    "hook_statuses",
    "is_background",
    "is_scenario_element",
    "is_selected",
    "mappings",
    "normalize_run",
    "parse_timestamp",
    "roll_up_status",
    "selected_features",
    "stats_of",
    "stats_row",
    "step_statuses",
    "status_token",
    "tag_row",
    "worst_status",
]


# --------------------------------------------------------------------------- #
# Vocabulary.  Declared once here; every other module and template reads these
# rather than restating them, which is what makes "no two surfaces grade one
# run differently" checkable instead of hopeful.
# --------------------------------------------------------------------------- #

#: Every status token a report surface recognises, quoted from ``status_token``
#: in ``app/templates/partials/status_badge.html``, which quotes the
#: ``data-report-status`` enumeration in ``app/static/css/main.css``.
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

#: The status 5.6.1's ``StatusDeserializer`` rewrites ``ambiguous`` to before
#: any counting happens.
UNDEFINED_STATUS: Final[str] = "undefined"

#: The status that rewrite applies to.  It keeps its own severity rank; only
#: the *counters* fold it.
AMBIGUOUS_STATUS: Final[str] = "ambiguous"

#: Severity order, most severe first.  Read it as: a failure beats an undefined
#: or ambiguous step, which beat a pending one, which beats a skipped or
#: untested one, which beat a pass -- so one failure is never averaged away by
#: the passes around it.  A maximum over a total order is associative, which is
#: why folding steps into elements and elements into a feature gives the same
#: answer as folding every step of the feature at once.
STATUS_PRECEDENCE: Final[tuple[str, ...]] = (
    "failed",
    "undefined",
    "ambiguous",
    "pending",
    "skipped",
    "untested",
    "passed",
)

#: Reading order for a ``by_status`` map: the reference overview page's own
#: column order first, then the three states it has no column for, then the
#: fallback.  Fixed rather than sorted, so identical input yields an identical
#: tally.  Every token :func:`status_token` can answer with occurs exactly once.
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

#: The status an element with no steps and no hooks takes.  Measured rather
#: than chosen, twice over: ``EmployeeFc.feature`` declares a Background with
#: an empty body which the reference generator renders as passed, and 5.6.1's
#: ``StatusCounter`` answers ``PASSED`` for an empty counter.
EMPTY_ELEMENT_STATUS: Final[str] = "passed"

#: The status an aggregate with nothing at all under it takes -- a feature
#: carrying no element.  Deliberately not a pass: nothing ran.
EMPTY_AGGREGATE_STATUS: Final[str] = UNKNOWN_STATUS

#: The two answers a PrettyReports verdict has.  ``Status`` in 5.6.1 is an enum
#: of five, but ``StatusCounter.getFinalStatus`` only ever answers with these
#: two, so an element, a feature row and a tag row carry one of them.
VERDICT_PASSED: Final[str] = "passed"
VERDICT_FAILED: Final[str] = "failed"

#: The five step statuses with a column of their own in the statistics table.
#: ``untested`` and :data:`UNKNOWN_STATUS` deliberately have none: they count
#: towards Total, where they honestly belong, and assigning them a column would
#: fabricate a number the source cannot produce.
COUNTED_STEP_STATUSES: Final[tuple[str, ...]] = (
    "passed",
    "failed",
    "skipped",
    "pending",
    UNDEFINED_STATUS,
)

#: The nine count keys ``app/templates/pretty/_stats_table.html`` reads on a
#: row and sums in its footer, in its own column order.
COUNT_KEYS: Final[tuple[str, ...]] = (
    "steps_passed",
    "steps_failed",
    "steps_skipped",
    "steps_pending",
    "steps_undefined",
    "steps_total",
    "scenarios_passed",
    "scenarios_failed",
    "scenarios_total",
)

#: The element keys holding hook entries.  ``before`` is accepted although the
#: collector records only ``after`` today, because
#: ``pretty/overview_features.html`` reads both and a document from another
#: producer may carry both.
HOOK_KEYS: Final[tuple[str, ...]] = ("before", "after")

#: The three group names of the summary, in the order
#: ``app/templates/artifact/metadata.html`` presents them.
SUMMARY_GROUPS: Final[tuple[str, ...]] = ("features", "scenarios", "steps")

#: The summary key carrying the run's earliest scenario start.
SUMMARY_START_KEY: Final[str] = "start_timestamp"

#: The nested count map's key, which is the shape ``GET /reports/summary``
#: answers with.
SUMMARY_BY_STATUS_KEY: Final[str] = "by_status"

#: A summary group's total key.
SUMMARY_TOTAL_KEY: Final[str] = "total"

#: The keys :func:`decorate_element` and :func:`decorate_feature` add.
_STATUS_KEY: Final[str] = "status"
_VERDICT_KEY: Final[str] = "verdict"
_DURATION_KEY: Final[str] = "duration_ns"
_SAMPLES_KEY: Final[str] = "duration_samples"
_STATS_KEY: Final[str] = "stats"

#: The element key recording whether the tag expression selected it.
_SELECTED_KEY: Final[str] = "selected"

#: Nanoseconds in one second, for the seconds rendering the views use.
_NANOSECONDS_PER_SECOND: Final[int] = 1_000_000_000


# --------------------------------------------------------------------------- #
# Total coercions.  Every read of the result document goes through one of
# these: a malformed document has to produce a poorer report rather than an
# exception, because the report is what a reader turns to when a run has gone
# wrong.
# --------------------------------------------------------------------------- #


def as_mapping(value: Any) -> JsonDict:
    """Return ``value`` when it is a mapping, otherwise an empty mapping.

    Args:
        value: Anything at all, including ``None`` and a key that was never
            there.

    Returns:
        The mapping, or ``{}``.  Never raises.
    """
    return value if isinstance(value, dict) else {}


def mappings(value: Any) -> list[JsonDict]:
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


def as_text(value: Any) -> str:
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


# --------------------------------------------------------------------------- #
# Predicates
# --------------------------------------------------------------------------- #


def is_background(element: JsonDict) -> bool:
    """Report whether ``element`` is a Background occurrence.

    The declared type is the model's own discriminator, and the keyword answers
    for a hand-built document that carries no type.  An unrecognised value
    reads as a test case, which is what :func:`app.reporting.events.new_element`
    does with one and what keeps its results from being lost.

    **This is also where every attachment is validated**, because it is the
    one point at which a rendered element is assembled: both HTML writers read
    their features from :func:`normalize_run`, which decorates through here, so
    no template of either writer can be reached by an attachment that has not
    been through
    :func:`app.reporting.screenshots.normalize_element_attachments`.  An
    attachment surviving it carries the media type ``image/png`` written from a
    literal and a payload proven to be canonical base64 of bytes beginning
    with PNG's complete eight-byte signature; one that does not survive is
    discarded with a log before any template sees it.  It validates a copy and
    computes no number, so nothing below depends on it and the single-
    aggregation rule is untouched.  The shared Jinja partial applies the same
    rules again, which is defence in depth rather than the control -- the Flask
    viewer renders from the machine-read JSON report without passing through
    here at all.

    Args:
        element: A Background or scenario element.

    Returns:
        ``True`` only for a Background occurrence.
    """
    declared = as_text(element.get("type")).lower()
    if declared:
        return declared == ELEMENT_TYPE_BACKGROUND
    return as_text(element.get("keyword")).lower() == ELEMENT_TYPE_BACKGROUND


def is_scenario_element(element: JsonDict) -> bool:
    """Report whether ``element`` is counted as a scenario.

    Strictly ``type == "scenario"``, case-insensitively, which is the
    generator's own ``isScenario()``.  Note the deliberate asymmetry with
    :func:`is_background`: an element of an unexpected type is *rendered* as a
    scenario, because losing its results would be worse, but it is not
    *counted* as one, because an unexpected type is not evidence that a test
    case ran.

    Args:
        element: A Background or scenario element.

    Returns:
        ``True`` only for an element whose declared type is ``"scenario"``.
    """
    return as_text(element.get("type")).lower() == ELEMENT_TYPE_SCENARIO


def is_selected(element: JsonDict) -> bool:
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
# Status: normalisation, the severity fold, and the binary verdict
# --------------------------------------------------------------------------- #


def status_token(status: Any) -> str:
    """Normalise one status.

    The single normalisation point: coerce to text, trim, fold to lower case,
    and answer with that token when it is one of :data:`KNOWN_STATUSES` --
    otherwise :data:`UNKNOWN_STATUS`.  Normalising and folding are deliberately
    separate jobs: this function decides how a status is *spelled*,
    :func:`roll_up_status` decides *which* status an aggregate has, and
    :func:`counter_token` decides which *column* counts it.

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
    candidate = as_text(status).lower()
    return candidate if candidate in KNOWN_STATUSES else UNKNOWN_STATUS


def roll_up_status(
    statuses: Iterable[Any],
    empty: str = EMPTY_ELEMENT_STATUS,
) -> str:
    """Fold a collection of statuses into the one that describes them all.

    The ordering is :data:`STATUS_PRECEDENCE`.  Each member is put through
    :func:`status_token` first, so a mixed collection of raw and normalised
    values is fine.

    Args:
        statuses: Raw or already-normalised statuses, in any order.
        empty: The answer for a collection that is empty, or that holds nothing
            the precedence names.  The element-level default is
            :data:`EMPTY_ELEMENT_STATUS`; a caller asking a run-level question
            -- "what is a feature with no elements at all?" -- passes
            :data:`EMPTY_AGGREGATE_STATUS`.

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


#: Historical name for :func:`roll_up_status`, kept because the Pretty writer
#: and its test module were written against it.  The two are one function: a
#: second fold, however thin, is a second place the precedence could drift.
worst_status = roll_up_status


def counter_token(status: Any) -> str:
    """Normalise one status for *counting*, folding ``ambiguous`` onto Undefined.

    5.6.1's ``StatusDeserializer`` holds ``UNKNOWN_STATUSES = ["ambiguous"]``
    and answers ``UNDEFINED`` for it before any counting happens, so an
    ambiguous step lands in the Undefined column on every surface.  The
    severity precedence is untouched: for grading, ``ambiguous`` keeps its own
    rank between ``undefined`` and ``pending``.

    Args:
        status: A raw or already-normalised status.

    Returns:
        The column token: :data:`UNDEFINED_STATUS` for ``ambiguous``, otherwise
        :func:`status_token`'s answer.

    Examples:
        >>> counter_token("ambiguous")
        'undefined'
        >>> counter_token("Skipped")
        'skipped'
        >>> counter_token(None)
        'unknown'
    """
    token = status_token(status)
    return UNDEFINED_STATUS if token == AMBIGUOUS_STATUS else token


def step_statuses(element: JsonDict) -> list[str]:
    """Return the normalised status of every step of ``element``, in order.

    Args:
        element: A Background or scenario element.

    Returns:
        One token per step.  A step whose result carries no status at all -- a
        step the run never reached -- contributes :data:`UNKNOWN_STATUS` rather
        than being dropped, so it cannot be silently read as a pass.
    """
    return [
        status_token(as_mapping(step.get("result")).get("status"))
        for step in mappings(element.get("steps"))
    ]


def hook_statuses(element: JsonDict) -> list[str]:
    """Return the normalised status of every hook of ``element``, in order.

    Both hook groups, ``before`` first, exactly as ``Element.setMetaData``
    reads them.  A hook is not a step: these statuses reach
    :func:`element_status` and :func:`element_verdict` and nothing else -- no
    step column counts one and no duration sum includes one.

    Args:
        element: A Background or scenario element.

    Returns:
        One token per hook entry, ``before`` entries before ``after`` entries.
    """
    return [
        status_token(as_mapping(hook.get("result")).get("status"))
        for key in HOOK_KEYS
        for hook in mappings(element.get(key))
    ]


def _fold(tokens: Sequence[str], empty: str = EMPTY_ELEMENT_STATUS) -> str:
    """Fold element-level tokens, keeping the two empty cases apart.

    The distinction matters and a single fallback cannot express it:

    * **nothing to fold at all** is ``empty`` -- the measured behaviour of
      ``EmployeeFc.feature``'s step-less Background;
    * **tokens that are all unrecognised** is :data:`UNKNOWN_STATUS`, because a
      status the result model never produced must not be reported as a pass.

    Args:
        tokens: Normalised tokens, in any order.
        empty: The answer for an empty collection.

    Returns:
        The most severe status present, or the appropriate empty answer.
    """
    if not tokens:
        return empty
    return roll_up_status(tokens, empty=UNKNOWN_STATUS)


def element_steps_status(
    element: JsonDict,
    empty: str = EMPTY_ELEMENT_STATUS,
) -> str:
    """Return the severity fold of ``element``'s **steps alone**.

    The generator's ``stepsStatus``.  Kept separate from
    :func:`element_status` for the surfaces that state a steps-only reading,
    and used by nothing that grades an element.

    Args:
        element: A Background or scenario element.
        empty: The answer for an element with no steps.

    Returns:
        The most severe step status; ``empty`` for an element with no steps;
        :data:`UNKNOWN_STATUS` when it has steps but none carries a status the
        model recognises.
    """
    return _fold(step_statuses(element), empty=empty)


def element_status(
    element: JsonDict,
    empty: str = EMPTY_ELEMENT_STATUS,
) -> str:
    """Return the severity fold of one element -- its steps and its hooks.

    A scenario is not coloured by its neighbours and a Background occurrence is
    not coloured by the scenario that follows it: each element's badge answers
    for that element.  Hooks take part because ``Element.calculateElementStatus``
    folds ``stepsStatus`` with ``beforeStatus`` and ``afterStatus``, so a
    scenario whose steps all passed but whose after-hook failed is not reported
    as a pass -- which is the "hooks counted on some surfaces only" divergence
    settled in one place.

    Args:
        element: A Background or scenario element.
        empty: The answer for an element with neither steps nor hooks.

    Returns:
        The most severe status among its steps and hooks; ``empty`` for an
        element with neither; :data:`UNKNOWN_STATUS` when it has some but none
        carries a status the model recognises.

    Examples:
        >>> element_status({"steps": [{"result": {"status": "passed"}},
        ...                           {"result": {"status": "failed"}}]})
        'failed'
        >>> element_status({"steps": []})
        'passed'
        >>> element_status({"steps": [{"result": {}}]})
        'unknown'
        >>> element_status({"steps": [{"result": {"status": "passed"}}],
        ...                 "after": [{"result": {"status": "failed"}}]})
        'failed'
    """
    return _fold(step_statuses(element) + hook_statuses(element), empty=empty)


def element_verdict(element: JsonDict) -> str:
    """Return the binary PrettyReports verdict of one element.

    ``Element.calculateElementStatus`` runs a ``StatusCounter`` over
    ``stepsStatus``, ``beforeStatus`` and ``afterStatus``, and that counter
    answers ``PASSED`` only while every status it counted was ``PASSED``.  So:
    an element is passed when every step and every hook passed, and failed
    otherwise -- an undefined step, a pending step, a skipped step, a status the
    model does not recognise and a failed hook all produce ``failed``.  An
    element with nothing to count is passed, which is that counter's initial
    value and the reference tree's rendering of the empty Background.

    Args:
        element: A Background or scenario element.

    Returns:
        :data:`VERDICT_PASSED` or :data:`VERDICT_FAILED`.

    Examples:
        >>> element_verdict({"steps": [{"result": {"status": "passed"}}]})
        'passed'
        >>> element_verdict({"steps": [{"result": {"status": "skipped"}}]})
        'failed'
        >>> element_verdict({"steps": [], "after": []})
        'passed'
        >>> element_verdict({"steps": [{"result": {"status": "passed"}}],
        ...                  "after": [{"result": {"status": "failed"}}]})
        'failed'
    """
    tokens = step_statuses(element) + hook_statuses(element)
    if all(token == VERDICT_PASSED for token in tokens):
        return VERDICT_PASSED
    return VERDICT_FAILED


def feature_status(feature: JsonDict) -> str:
    """Return the severity fold of one feature, from its elements.

    The fold is over **every** element, Background occurrences included: a
    Background failure moves its feature's status without moving any
    scenario's, because the failure is real and belongs to that feature.

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
    elements = mappings(feature.get("elements"))
    if not elements:
        return EMPTY_AGGREGATE_STATUS
    return roll_up_status(
        (element_status(element) for element in elements),
        empty=UNKNOWN_STATUS,
    )


def feature_verdict(feature: JsonDict) -> str:
    """Return the binary PrettyReports verdict of one feature.

    ``Feature``'s own elements counter folds the same way an element's does, so
    a feature is passed when every one of its elements is -- Background
    occurrences included, which is what the features overview's row status
    shows.  A feature with no elements is passed, the counter's initial value;
    it does not reach a row in practice, because :func:`selected_features`
    drops a feature with no test case.

    Args:
        feature: A feature mapping.

    Returns:
        :data:`VERDICT_PASSED` or :data:`VERDICT_FAILED`.
    """
    elements = mappings(feature.get("elements"))
    if all(element_verdict(element) == VERDICT_PASSED for element in elements):
        return VERDICT_PASSED
    return VERDICT_FAILED


# --------------------------------------------------------------------------- #
# Durations.  Steps only, in nanoseconds, with the sample count that separates
# a genuinely zero total from a total of nothing at all.
# --------------------------------------------------------------------------- #


def duration_sample(value: Any) -> int | None:
    """Return ``value`` when it is a usable nanosecond duration, else ``None``.

    The single duration predicate, so a value the step row declines to render
    cannot still reach a total.  Usable means a non-negative integer that is
    not a boolean: :class:`bool` is a subclass of :class:`int`, so ``True``
    would otherwise add one nanosecond and hide a malformed document, and a
    float or a negative is a document this project's collector cannot produce.
    A skipped step legitimately carries no ``duration`` key at all, so ``None``
    is ordinary rather than exceptional.

    Args:
        value: A raw ``result.duration``, or anything at all.

    Returns:
        The duration in nanoseconds, or ``None`` for no sample.

    Examples:
        >>> duration_sample(5)
        5
        >>> duration_sample(0)
        0
        >>> duration_sample(True) is None
        True
        >>> duration_sample(1.5) is None
        True
        >>> duration_sample(-1) is None
        True
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def element_duration(element: JsonDict) -> tuple[int, int]:
    """Return one element's step duration and its sample count.

    The sum of its **step** durations and nothing else: hook durations are
    never added, because ``TagObject.addElement`` sums ``Step.getDuration()``
    alone, so the after-hook that carries a failure screenshot does not
    lengthen its scenario.

    Args:
        element: A Background or scenario element.

    Returns:
        ``(total_nanoseconds, samples)``.  ``samples`` counts the steps that
        carried a usable duration, which is what separates a real zero from a
        total of nothing at all; a malformed value contributes to neither.

    Examples:
        >>> element_duration({"steps": [{"result": {"duration": 5}},
        ...                             {"result": {"status": "skipped"}}]})
        (5, 1)
        >>> element_duration({})
        (0, 0)
    """
    total = 0
    samples = 0
    for step in mappings(element.get("steps")):
        sample = duration_sample(as_mapping(step.get("result")).get("duration"))
        if sample is not None:
            total += sample
            samples += 1
    return total, samples


def element_duration_ns(element: JsonDict) -> int:
    """Return one element's step duration in nanoseconds.

    Args:
        element: A Background or scenario element.

    Returns:
        The total in nanoseconds, never negative.  An element with no usable
        sample answers ``0``, which the generator's own arithmetic also
        produces and which the macros render as ``0.000``.
    """
    return element_duration(element)[0]


def format_duration_seconds(total_ns: Any, samples: Any = None) -> str | None:
    """Render a nanosecond total as seconds to three decimals, or ``None``.

    The single formatting rule behind the seconds figure the HTTP views and the
    step row show, so a duration cannot read one way in a table cell and
    another in the total above it.  The raw nanosecond integer is never
    rendered as such.

    Args:
        total_ns: A nanosecond total, or anything at all.
        samples: How many durations the total was built from.  ``0`` means the
            duration is unknown rather than zero and answers ``None``, which a
            caller renders as an em dash.  ``None`` skips the check, for a
            caller that holds a single step's duration.

    Returns:
        The seconds figure, e.g. ``"1.500"``, or ``None`` when there is nothing
        to render.

    Examples:
        >>> format_duration_seconds(1_500_000_000, 1)
        '1.500'
        >>> format_duration_seconds(0, 0) is None
        True
        >>> format_duration_seconds("nonsense") is None
        True
    """
    if samples is not None:
        counted = duration_sample(samples)
        if counted is None or counted == 0:
            return None
    total = duration_sample(total_ns)
    if total is None:
        return None
    return f"{total / _NANOSECONDS_PER_SECOND:.3f}"


# --------------------------------------------------------------------------- #
# Timestamps
# --------------------------------------------------------------------------- #


def parse_timestamp(value: Any) -> datetime | None:
    """Parse one of the document's timestamps, or answer ``None``.

    :func:`app.reporting.events.format_timestamp` emits millisecond-precision
    UTC ISO-8601 ending in a literal ``Z``.  That suffix is accepted natively
    by :meth:`datetime.datetime.fromisoformat` on the interpreters this project
    supports and is also substituted explicitly, so a document written by an
    older or hand-edited producer still parses.  A value that parses without an
    offset is read as UTC, so every instant returned here is aware and any two
    of them compare without raising.

    Args:
        value: A timestamp string, or anything at all.

    Returns:
        The instant, or ``None`` when the value is not a timestamp.  Never
        raises.
    """
    text = as_text(value)
    if not text:
        return None
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return None


def display_timestamp(value: Any) -> str:
    """Return one timestamp exactly as the document spells it, trimmed.

    The results document already carries fixed-width UTC ISO-8601, so
    reformatting it on the way to a page would only invite the machine-readable
    and human-readable forms to disagree.  This function exists so that
    "render it verbatim" is a rule with one implementation rather than a habit
    three templates each keep.

    Args:
        value: A timestamp string, or anything at all.

    Returns:
        The trimmed string, or ``""`` when there is none -- which a caller
        renders as an em dash.
    """
    return as_text(value)


def earliest_start(features: Sequence[JsonDict]) -> str | None:
    """Return the run's earliest scenario start, as the model spells it.

    Scenario elements only: a Background occurrence carries no
    ``start_timestamp`` at all.  Selection is by parsed instant and the string
    is returned **verbatim** rather than reformatted, so the value on a page is
    the value the JSON artifact carries; the string breaks a tie between two
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
        for element in mappings(feature.get("elements")):
            if not is_scenario_element(element):
                continue
            candidate = as_text(element.get("start_timestamp"))
            if not candidate:
                continue
            moment = parse_timestamp(candidate)
            if moment is not None:
                parsed.append((moment, candidate))
    if not parsed:
        return None
    return min(parsed, key=lambda pair: (pair[0], pair[1]))[1]


# --------------------------------------------------------------------------- #
# Selection.  Applied once, by the rule the JSON writer applies, so that every
# surface describes the same run.
# --------------------------------------------------------------------------- #


def element_units(elements: Sequence[JsonDict]) -> list[list[JsonDict]]:
    """Group elements into Background-occurrence-plus-scenario units.

    The Background occurrence emitted for a scenario belongs immediately in
    front of it and shares its fate: if the scenario is dropped, its Background
    occurrence goes with it, or a page would show a background for a test case
    it does not show.  Grouping first is what makes that exact.

    Args:
        elements: One feature's elements, in document order.

    Returns:
        The units, in input order: ``[background, scenario]`` normally,
        ``[scenario]`` for a feature with no Background, and ``[background]``
        for the pathological trailing occurrence with no scenario, which is
        kept as a unit of its own rather than attached to something it did not
        precede.
    """
    units: list[list[JsonDict]] = []
    for element in elements:
        if is_background(element):
            units.append([element])
            continue
        if units and len(units[-1]) == 1 and is_background(units[-1][0]):
            units[-1].append(element)
        else:
            units.append([element])
    return units


def selected_features(result_set: ResultSet | None) -> list[JsonDict]:
    """Return the features a report surface presents, in source order.

    Two rules, both ``app/reporting/cucumber_json.py``'s, so that the JSON
    artifact, the two HTML artifacts and the viewer describe the same run:

    * a unit whose scenario -- or whose Background occurrence -- carries an
      explicit ``"selected": False`` is dropped whole;
    * a feature left with no test case is dropped altogether, which includes a
      feature left with nothing but Background occurrences: an occurrence is
      emitted *for* a test case, so one without its scenario represents none.
      Under the default ``@Smoke`` filter that is what reduces the suite's ten
      features to the one the reference artifact carries.

    Nothing is sorted and nothing is mutated: a feature whose elements survive
    unchanged is passed through as it stands, and one that loses a unit is
    **copied** with a new element list, so the caller's document is untouched.

    Args:
        result_set: The merged result document, or ``None`` for a run that
            produced nothing -- all four artifacts are still written in that
            case, so ``None`` yields an empty list rather than an error.

    Returns:
        The feature mappings to present, in document order.  Never raises.
    """
    document = as_mapping(result_set)
    kept: list[JsonDict] = []
    for feature in mappings(document.get("features")):
        elements = mappings(feature.get("elements"))
        units = [
            unit
            for unit in element_units(elements)
            if all(is_selected(element) for element in unit)
        ]
        surviving = [element for unit in units for element in unit]
        if not any(not is_background(element) for element in surviving):
            # No test case survived, so the JVM would have created no feature
            # map at all.  Dropped rather than presented as a row of zeros.
            continue
        if len(surviving) == len(elements):
            kept.append(feature)
        else:
            kept.append({**feature, "elements": surviving})
    return kept


# --------------------------------------------------------------------------- #
# Counting
# --------------------------------------------------------------------------- #


def count_steps(statuses: Iterable[Any]) -> JsonDict:
    """Count step statuses into the five columns and the total.

    Args:
        statuses: Raw or normalised step statuses, one per step.

    Returns:
        A mapping carrying ``steps_passed``, ``steps_failed``,
        ``steps_skipped``, ``steps_pending``, ``steps_undefined`` -- every one
        present and ``0`` included, because the statistics table renders a cell
        for each -- and ``steps_total``.  ``ambiguous`` is counted as Undefined
        (:func:`counter_token`); ``untested`` and an unrecognised status count
        towards the total and towards no column.

    Examples:
        >>> counts = count_steps(["passed", "ambiguous", "untested"])
        >>> counts["steps_undefined"], counts["steps_total"]
        (1, 3)
    """
    counts: JsonDict = {f"steps_{status}": 0 for status in COUNTED_STEP_STATUSES}
    total = 0
    for status in statuses:
        total += 1
        key = f"steps_{counter_token(status)}"
        if key in counts:
            counts[key] = counts[key] + 1
    counts["steps_total"] = total
    return counts


def count_group(tokens: Sequence[str]) -> JsonDict:
    """Build one summary group, in both shapes its readers need.

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
        SUMMARY_TOTAL_KEY: len(tokens),
        SUMMARY_BY_STATUS_KEY: by_status,
    }
    group.update(by_status)
    return group


def stats_of(elements: Sequence[JsonDict]) -> JsonDict:
    """Aggregate a group of elements into the statistics table's figures.

    The one implementation of the PrettyReports arithmetic, reproducing
    ``TagObject.addElement`` and ``Feature``'s counters:

    * the five step columns and ``steps_total`` count **steps only**, with
      ``ambiguous`` folded onto Undefined;
    * ``duration_ns`` sums **step** durations only;
    * ``scenarios_total`` counts elements typed ``scenario``, and
      ``scenarios_passed`` those of them whose verdict is passed, so
      ``scenarios_failed`` is the remainder -- which is
      ``getFailedScenarios()``, the count of elements the counter did not
      record as ``PASSED``, and not a search for a failed step;
    * ``status`` is the binary verdict over **every** element handed in,
      Background occurrences included.

    An unselected element contributes nothing at all, so a caller that has not
    already filtered cannot inflate a row with a scenario that never ran.

    Args:
        elements: The elements of one feature, one tag or one scenario.

    Returns:
        A mapping carrying the nine keys of :data:`COUNT_KEYS`, plus
        ``duration_ns``, ``duration_samples`` and ``status``.

    Examples:
        >>> stats = stats_of([{"type": "scenario",
        ...                    "steps": [{"result": {"status": "failed",
        ...                                          "duration": 7}}]}])
        >>> stats["scenarios_failed"], stats["steps_failed"], stats["status"]
        (1, 1, 'failed')
    """
    statuses: list[str] = []
    duration_ns = 0
    samples = 0
    scenarios_total = 0
    scenarios_passed = 0
    passed = True

    for element in elements:
        if not is_selected(element):
            continue
        statuses.extend(step_statuses(element))
        total, counted = element_duration(element)
        duration_ns += total
        samples += counted
        verdict = element_verdict(element)
        if is_scenario_element(element):
            scenarios_total += 1
            if verdict == VERDICT_PASSED:
                scenarios_passed += 1
        if verdict != VERDICT_PASSED:
            passed = False

    stats: JsonDict = dict(count_steps(statuses))
    stats["scenarios_passed"] = scenarios_passed
    stats["scenarios_failed"] = scenarios_total - scenarios_passed
    stats["scenarios_total"] = scenarios_total
    stats[_DURATION_KEY] = duration_ns
    stats[_SAMPLES_KEY] = samples
    stats[_STATUS_KEY] = VERDICT_PASSED if passed else VERDICT_FAILED
    return stats


def stats_row(
    name: str,
    elements: Sequence[JsonDict],
    href: str = "",
) -> JsonDict:
    """Build one statistics-table row: a label, a link and the figures.

    The row shape ``app/templates/pretty/_stats_table.html`` reads, shared by
    the features overview, the tags overview and both detail pages, so an
    overview row and the detail page it links cannot state different numbers.
    Counts and durations are raw integers and the status is a raw token,
    because formatting belongs to ``pretty/_macros.html`` and badge
    normalisation to ``partials/status_badge.html``.

    Args:
        name: The row's label -- a feature name or a tag, leading ``@``
            included.
        elements: The elements the row aggregates; see :func:`stats_of`.
        href: The detail page's filename, or ``""`` for a row that renders as
            plain text rather than as a link that goes nowhere.

    Returns:
        ``{"name": ..., "href": ..., **stats_of(elements)}``.
    """
    return {"name": name, "href": href, **stats_of(elements)}


def build_row_totals(rows: Sequence[JsonDict]) -> JsonDict:
    """Sum statistics rows for the table's footer.

    The footer reads the nine counts and the duration, and its last two cells
    read ``features`` and ``features_passed`` -- the generator's own key names
    for "how many subjects does this table have, and how many of them passed".
    The subject is whatever the rows describe: a feature on the features
    overview, a tag on the tags overview.

    Args:
        rows: The rows from :func:`stats_row`.

    Returns:
        A mapping carrying the nine counts of :data:`COUNT_KEYS`,
        ``duration_ns``, ``features`` and ``features_passed``.  Every value is
        ``0`` for an empty row set, so the footer still renders.
    """
    totals: JsonDict = {
        key: sum(int(row.get(key, 0)) for row in rows) for key in COUNT_KEYS
    }
    totals[_DURATION_KEY] = sum(int(row.get(_DURATION_KEY, 0)) for row in rows)
    totals["features"] = len(rows)
    totals["features_passed"] = sum(
        1 for row in rows if row.get(_STATUS_KEY) == VERDICT_PASSED
    )
    return totals


def tag_row(
    name: str,
    subjects: Sequence[JsonDict],
    href: str = "",
) -> JsonDict:
    """Build one tags-overview row.

    A tag's subjects are the selected scenario elements carrying it, so this is
    :func:`stats_row` under the name the Pretty writer's callers use.  It is
    the same arithmetic the tag's own detail page shows, which is what makes
    the overview row and that page two readings of one calculation.

    Args:
        name: The tag, leading ``@`` included, which is also the row's label.
        subjects: The tag's selected scenario elements.
        href: The tag's detail-page filename, or ``""`` for no link.

    Returns:
        A row from :func:`stats_row`.
    """
    return stats_row(name, subjects, href)


def build_tag_rows(
    tags: Mapping[str, Sequence[JsonDict]],
    hrefs: Mapping[str, str] | None = None,
) -> list[JsonDict]:
    """Build every tags-overview row, in the order the tags were collected.

    Args:
        tags: Tag name to its selected scenario elements.
        hrefs: Tag name to detail-page filename.  A tag with no entry renders
            as plain text rather than as a link, which is the template's own
            behaviour for an absent href.

    Returns:
        One row per tag, in the mapping's iteration order.  An empty mapping
        yields an empty list, which the template renders as a complete page
        carrying "You have no tags in your cucumber report" -- the ordinary
        outcome for a run over the five features that declare no feature-level
        tag.
    """
    links = hrefs if hrefs is not None else {}
    return [
        tag_row(name, subjects, links.get(name, ""))
        for name, subjects in tags.items()
    ]


def build_summary(features: Sequence[JsonDict]) -> JsonDict:
    """Count the run: features, scenarios and steps by status, and its start.

    One pass, three tallies, and the rules are stated once here for every
    surface that shows a total -- the single page's metadata block, the
    viewer's overview and ``GET /reports/summary``:

    * **Steps** -- every step of every element, Background occurrences
      included, because an occurrence genuinely runs once per scenario.  A
      hook is not a step.
    * **Scenarios** -- elements typed ``scenario`` and never the element count,
      since backgrounds interleave and repeat.  A scenario's status is
      :func:`element_status`: the worst among its own steps and hooks, so a
      Background failure is not reported as a scenario failure.
    * **Features** -- the worst status among that feature's elements,
      Background occurrences included, so a Background failure moves its
      feature's status without moving any scenario's.

    Args:
        features: The feature mappings the surface presents -- already
            selection-filtered by :func:`selected_features`, so the tally
            counts what the page shows and what the JSON artifact carries, and
            not the scenarios neither holds.  Decoration is irrelevant: every
            figure is recomputed from the steps, so a hand-built feature list
            without a ``status`` key counts identically.

    Returns:
        A mapping carrying ``features``, ``scenarios`` and ``steps`` -- each a
        group from :func:`count_group` -- and ``start_timestamp``.  An empty
        feature list answers three zero totals, three empty maps and ``None``.
        Never raises.
    """
    feature_tokens: list[str] = []
    scenario_tokens: list[str] = []
    step_tokens: list[str] = []

    for feature in features:
        elements = mappings(feature.get("elements"))
        element_tokens: list[str] = []
        for element in elements:
            step_tokens.extend(step_statuses(element))
            # One fold per element, reused for that element's own tally entry
            # and for its feature's, which is what makes the figures on a page
            # and the badges above them one reading of one list.
            token = element_status(element)
            element_tokens.append(token)
            if is_scenario_element(element):
                scenario_tokens.append(token)
        feature_tokens.append(
            roll_up_status(element_tokens, empty=UNKNOWN_STATUS)
            if elements
            else EMPTY_AGGREGATE_STATUS
        )

    counted = (feature_tokens, scenario_tokens, step_tokens)
    summary: JsonDict = {
        name: count_group(tokens)
        for name, tokens in zip(SUMMARY_GROUPS, counted, strict=True)
    }
    summary[SUMMARY_START_KEY] = earliest_start(features)
    return summary


# --------------------------------------------------------------------------- #
# Decoration and the run aggregate.  This is what a template reads: values it
# formats, never values it derives.
# --------------------------------------------------------------------------- #


def decorate_element(element: JsonDict) -> JsonDict:
    """Return a copy of ``element`` carrying its own aggregate.

    Args:
        element: A Background or scenario element.

    Returns:
        A new mapping: the element's keys with its attachments validated, plus
        ``status`` (the severity fold), ``verdict`` (the binary one),
        ``duration_ns``, ``duration_samples`` and ``stats`` (this element's own
        statistics figures).  The original is untouched.
    """
    duration_ns, samples = element_duration(element)
    return {
        **normalize_element_attachments(element),
        _STATUS_KEY: element_status(element),
        _VERDICT_KEY: element_verdict(element),
        _DURATION_KEY: duration_ns,
        _SAMPLES_KEY: samples,
        _STATS_KEY: stats_of([element]),
    }


def decorate_feature(feature: JsonDict) -> JsonDict:
    """Return a copy of ``feature`` with every level's aggregate filled in.

    Each element is decorated by :func:`decorate_element` and the feature's own
    values are rolled up from those copies, so a feature's badge is exactly the
    fold of the badges shown beneath it.

    Args:
        feature: A feature mapping, already selection-filtered.

    Returns:
        A new feature mapping carrying a new element list, plus ``status``,
        ``verdict``, ``duration_ns``, ``duration_samples`` and ``stats``.
        Order is preserved throughout: elements exactly where the model puts
        them, each Background occurrence repeated in its own position.
    """
    elements = [
        decorate_element(element) for element in mappings(feature.get("elements"))
    ]
    stats = stats_of(elements)
    rolled = (
        roll_up_status(
            (element[_STATUS_KEY] for element in elements),
            empty=UNKNOWN_STATUS,
        )
        if elements
        else EMPTY_AGGREGATE_STATUS
    )
    return {
        **feature,
        "elements": elements,
        _STATUS_KEY: rolled,
        _VERDICT_KEY: stats[_STATUS_KEY],
        _DURATION_KEY: stats[_DURATION_KEY],
        _SAMPLES_KEY: stats[_SAMPLES_KEY],
        _STATS_KEY: stats,
    }


def decorated_features(features: Sequence[JsonDict]) -> list[JsonDict]:
    """Return ``features`` decorated, in input order.

    Args:
        features: The feature mappings to decorate, already selection-filtered
            by :func:`selected_features`.

    Returns:
        A new list of new feature mappings; see :func:`decorate_feature`.
        Never raises.
    """
    return [decorate_feature(feature) for feature in features]


@dataclass(frozen=True)
class RunAggregate:
    """One run, aggregated once, for every surface that presents it.

    Frozen on purpose, and the guarantee is worth stating exactly rather than
    loosely.  The dataclass's fields cannot be rebound, and
    :func:`normalize_run` builds every mapping and list it carries **fresh on
    each call**, sharing nothing with the merged result document and nothing
    between two calls.  So the property that matters holds: no consumer's edit
    can reach another surface's figures or the document the other writers
    read.  What is *not* claimed is deep immutability -- the nested mappings
    are ordinary dictionaries, because a template iterates them, the summary
    is serialised as the body of ``GET /reports/summary``, and a read-only
    proxy would break both.  Treat the contents as read-only; a consumer that
    edits them corrupts its own render and nothing else.

    Attributes:
        features: The selection-filtered, decorated features in source order;
            see :func:`decorate_feature`.
        summary: The tally from :func:`build_summary`, which is exactly the
            body ``GET /reports/summary`` answers with.
        start_timestamp: The run's earliest scenario start, verbatim, or
            ``None``.
        feature_rows: One statistics row per feature, in the same order as
            :attr:`features`; see :func:`stats_row`.
        totals: The footer sums over :attr:`feature_rows`; see
            :func:`build_row_totals`.
    """

    features: tuple[JsonDict, ...]
    summary: JsonDict
    start_timestamp: str | None
    feature_rows: tuple[JsonDict, ...]
    totals: JsonDict


def normalize_run(
    result_set: ResultSet | None,
    feature_hrefs: Mapping[str, str] | None = None,
) -> RunAggregate:
    """Aggregate one merged result document into :class:`RunAggregate`.

    The order of work is the order the values depend on each other: select,
    decorate, tally, then build the rows the statistics tables show.

    Args:
        result_set: The merged result document, or ``None`` for a run that
            produced nothing -- which yields an aggregate of zeros rather than
            an error, because the exit contract writes all four artifacts even
            then.
        feature_hrefs: A map from a feature's ``uri`` or ``path`` to its detail
            page's filename, for the surfaces that link one.  A feature with no
            entry gets an empty href and renders as plain text; a surface with
            no detail pages passes nothing.

    Returns:
        The aggregate.  Never raises, and never mutates ``result_set``.
    """
    links = feature_hrefs if feature_hrefs is not None else {}
    features = decorated_features(selected_features(result_set))
    rows: list[JsonDict] = []
    for feature in features:
        uri = as_text(feature.get("uri"))
        path = as_text(feature.get("path"))
        href = links.get(uri, "") or links.get(path, "")
        rows.append(
            stats_row(
                as_text(feature.get("name")),
                mappings(feature.get("elements")),
                href,
            )
        )
    summary = build_summary(features)
    return RunAggregate(
        features=tuple(features),
        summary=summary,
        start_timestamp=summary.get(SUMMARY_START_KEY),
        feature_rows=tuple(rows),
        totals=build_row_totals(rows),
    )
