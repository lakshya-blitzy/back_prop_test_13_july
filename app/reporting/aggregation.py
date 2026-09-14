"""The normalised result model the report artifacts are graded by.

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

This module is that model, and **within the artifact pipeline it is the only
place a status is normalised, a status is folded, a step is counted, a duration
is summed or a run's start is chosen**: the two HTML writers, every template of
``app/templates/pretty/`` and the rerun manifest read the values computed here
and format them.  The invariant is therefore **not yet satisfied for every
surface** -- ``app/web/routes.py`` still tallies the parsed artifact itself, and
one of its rules differs.  The section *"Who reads this, and where the claim
stops"* below names the consumers, both boundaries and the one figure on which
HTTP and an artifact can still disagree; nothing in this module claims the
cross-file invariant is met while that is true.

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
  verdict ``passed``.  It also has its own rank in :data:`STATUS_PRECEDENCE`,
  because a fold that dropped it answered ``passed`` for an element this
  module's own binary reading called ``failed``.
* **A status is canonicalised once, here.**  :func:`canonical_status` owns
  behave's wider vocabulary (:data:`STATUS_ALIASES` -- ``error``,
  ``hook_error`` and ``cleanup_error`` are ``failed``) and the dry-run rule
  (under ``dryRun`` a matched step is ``passed`` and an unmatched one
  ``undefined``, never ``untested``).  Both rules used to exist in
  ``app/reporting/cucumber_json.py`` alone, so one run was graded differently
  depending on which file a reader opened.  Decoration applies them to the
  *copy* it hands the templates, which is what keeps the run's mode and
  behave's spellings out of every surface downstream.

A scenario and its Background are one test case
-----------------------------------------------
A Background occurrence runs once per scenario and the JVM emits it as an
element of its own -- with its failed step, and the scenario's own steps
``skipped`` -- so the element shape is not the question; every *derived*
reading is.  :func:`unit_status` and :func:`unit_verdict` are that reading,
over the unit :func:`element_units` groups, and they are what
:func:`build_summary`'s scenario tally, :func:`stats_of`'s scenario counts, the
``effective_status`` decoration writes onto each element, and
``app/reporting/rerun_report.py``'s selection all consult.  Before they
existed, one Background-only failure read ``skipped`` in the summary,
``failed`` on the Pretty pages and *selected* in the rerun manifest.

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

Who reads this, and where the claim stops
-----------------------------------------
Stated as the import graph has it, because a documented consumer that does not
import the model is how two surfaces come to grade one run differently:

* ``app/reporting/html_report.py`` -- one :func:`normalize_run` call per
  single-page artifact;
* ``app/reporting/pretty_reports.py`` -- one per report tree, and it installs
  the functions below as ``model_``-prefixed globals of its own template
  environment, so the templates of ``app/templates/pretty/`` call **these**
  functions rather than holding folds of their own;
* ``app/reporting/rerun_report.py`` -- the canonical statuses and the scenario
  unit's reading, before it decides what the manifest re-selects.

Two boundaries rather than one, both deliberate and both asserted:

* ``app/reporting/cucumber_json.py`` keeps its own ``STATUS_ALIASES`` and
  ``map_step_status`` because its vocabulary has no ``unknown`` -- the
  publisher parses the names it emits -- so :func:`canonical_status` takes a
  ``fallback`` argument and answers exactly what that writer answers when it is
  given ``untested``.  The table is the same ten entries and a test pins the
  two against each other.
* ``app/web/routes.py`` is **not** a consumer.  The viewer tallies the parsed
  artifact itself by the rules its own templates declare, and one of them
  differs: a step-less element is ``unknown`` there and
  :data:`EMPTY_ELEMENT_STATUS`, which is ``passed``, here.  Until that route
  reads :func:`normalize_run`, that is the one place a figure shown over HTTP
  and a figure written into an artifact can disagree.

Boundaries and behaviour
------------------------
Imports are the standard library and :mod:`app.reporting.events`, which owns
the document's schema, and nothing else: no Flask, no Jinja, no Selenium, no
service, no path accessor -- this module computes numbers and resolves no
destination.  Consumers import it directly (``from app.reporting.aggregation
import \u2026``); it is deliberately absent from the package barrel, which stays the
fan-out surface for the four writers rather than this model's API.

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
    "EFFECTIVE_STATUS_KEY",
    "EFFECTIVE_VERDICT_KEY",
    "EMPTY_AGGREGATE_STATUS",
    "EMPTY_ELEMENT_STATUS",
    "FAILURE_TOKENS",
    "HOOK_KEYS",
    "KNOWN_STATUSES",
    "STATUS_ALIASES",
    "STATUS_PRECEDENCE",
    "STATUS_READING_ORDER",
    "STEPS_STATUS_KEY",
    "SUMMARY_BY_STATUS_KEY",
    "SUMMARY_GROUPS",
    "SUMMARY_START_KEY",
    "SUMMARY_TOTAL_KEY",
    "UNDEFINED_STATUS",
    "UNKNOWN_STATUS",
    "UNTESTED_STATUS",
    "VERDICT_FAILED",
    "VERDICT_PASSED",
    "RunAggregate",
    "as_mapping",
    "as_text",
    "build_row_totals",
    "build_summary",
    "build_tag_rows",
    "canonical_status",
    "canonical_step_status",
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
    "is_dry_run",
    "is_failure_token",
    "is_passed_token",
    "is_scenario_element",
    "is_selected",
    "mappings",
    "normalize_run",
    "parse_timestamp",
    "roll_up_status",
    "selected_features",
    "stats_of",
    "stats_row",
    "status_name",
    "status_token",
    "step_statuses",
    "tag_row",
    "unit_status",
    "unit_verdict",
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

#: behave's own initial status, and the honest token for "this step has no
#: outcome".  Named because :data:`STATUS_ALIASES` folds two of behave's names
#: onto it and because it is the answer ``app/reporting/cucumber_json.py``
#: falls back to, which is the boundary :func:`canonical_status` documents.
UNTESTED_STATUS: Final[str] = "untested"

#: behave status names with no Cucumber counterpart, folded onto the nearest
#: one.  **The same ten entries, with the same values, as ``STATUS_ALIASES`` in
#: ``app/reporting/cucumber_json.py``**, and that is load-bearing rather than
#: tidy: the table living in the JSON writer alone is what published a step
#: behave recorded as ``hook_error`` as ``failed`` in ``cucumber.json`` while
#: both HTML artifacts rendered it *Unknown*, so one run was graded differently
#: depending on which file a reader opened.  The fold belongs here, where every
#: surface reads it, and ``tests/test_aggregation.py`` asserts this table
#: against the writer's own so the two cannot drift.
#:
#: The provenance of each entry, which is behave 1.3.3's enum being wider than
#: Cucumber's:
#:
#: * ``error`` is behave's name for an *exception* in a step as against a
#:   failed assertion, and ``hook_error``/``cleanup_error`` are the same
#:   distinction for hook code; Cucumber has one ``failed`` for all of them.
#: * ``xfailed``/``xpassed`` come from behave's expected-failure marking, which
#:   this suite never uses; each folds onto the outcome that actually occurred.
#: * ``pending_warn`` and ``untested_pending`` are behave's two spellings of
#:   pending and ``untested_undefined`` its spelling of undefined.  behave's
#:   own ``Status.normalized_name`` already folds these three, so they arrive
#:   only from a hand-built or foreign document.
#: * ``executing`` and ``unknown`` describe a step whose outcome was never
#:   established -- a worker killed mid-step, say -- which is
#:   :data:`UNTESTED_STATUS`.
STATUS_ALIASES: Final[dict[str, str]] = {
    "error": "failed",
    "hook_error": "failed",
    "cleanup_error": "failed",
    "xfailed": "failed",
    "xpassed": "passed",
    "pending_warn": "pending",
    "untested_pending": "pending",
    "untested_undefined": UNDEFINED_STATUS,
    "executing": UNTESTED_STATUS,
    "unknown": UNTESTED_STATUS,
}

#: Severity order, most severe first.  Read it as: a failure beats an undefined
#: or ambiguous step, which beat a pending one, which beats a skipped or
#: untested one, which beat a pass -- so one failure is never averaged away by
#: the passes around it.  A maximum over a total order is associative, which is
#: why folding steps into elements and elements into a feature gives the same
#: answer as folding every step of the feature at once.
#: :data:`UNKNOWN_STATUS` sits between ``untested`` and ``passed`` rather than
#: outside the order, and that placement is load-bearing: while it was absent,
#: ``roll_up_status(["passed", "unknown"])`` answered ``passed`` although
#: :func:`element_verdict` of the same element answered ``failed``, so the model
#: itself reported an outcome nobody established as a pass and contradicted its
#: own binary reading.  It ranks *below* ``untested`` because ``untested`` is a
#: state behave recorded while ``unknown`` is the absence of one.
STATUS_PRECEDENCE: Final[tuple[str, ...]] = (
    "failed",
    "undefined",
    "ambiguous",
    "pending",
    "skipped",
    UNTESTED_STATUS,
    UNKNOWN_STATUS,
    "passed",
)

#: The tokens that make a scenario unit a failure -- the complement of
#: Cucumber's ``Status.isOk()``, which is ``PASSED || SKIPPED`` alone.
#: Measured, not chosen: a Cucumber-JVM 7.2.3 probe's
#: ``RerunFormatter.handleTestCaseFinished`` records a test case whenever
#: ``isOk()`` is false, which is why ``undefined``, ``pending`` and
#: ``ambiguous`` join ``failed`` here even though none of them is spelled
#: ``failed``.
#:
#: Two properties this set has, and both are relied on:
#:
#: * it is a **prefix of** :data:`STATUS_PRECEDENCE`, so "any member of a unit
#:   failed" and "the unit's fold is a failure" are the same predicate -- which
#:   is what lets :func:`unit_status` and
#:   ``app/reporting/rerun_report.py``'s selection be one rule rather than two;
#: * ``skipped``, ``untested``, :data:`UNKNOWN_STATUS` and ``passed`` are
#:   outside it, so the steps behave skips *after* a failure never select a
#:   scenario on their own account -- the failing step already did.
FAILURE_TOKENS: Final[frozenset[str]] = frozenset(
    {
        "failed",
        UNDEFINED_STATUS,
        AMBIGUOUS_STATUS,
        "pending",
    }
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

#: The steps-only fold, added to every decorated element so a surface that
#: states a steps-only reading reads it instead of re-deriving it.
STEPS_STATUS_KEY: Final[str] = "steps_status"

#: The **effective** scenario-unit reading, added to every decorated element:
#: the fold of the preceding Background occurrence and the scenario for a
#: scenario element, and the element's own reading for a Background occurrence,
#: so a consumer reads one key uniformly and never has to pair elements itself.
EFFECTIVE_STATUS_KEY: Final[str] = "effective_status"
EFFECTIVE_VERDICT_KEY: Final[str] = "effective_verdict"

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


def status_name(status: Any) -> str:
    """Return the status *name* for a value that may be a behave enum.

    The internal document stores status names as strings, so this is reached
    only when a caller hands over a behave ``Status`` directly -- which a test,
    a hand-built document, or a step recorded straight off behave's model
    legitimately does.  The rule is
    ``app/reporting/cucumber_json.py``'s ``_status_text`` exactly, because the
    two surfaces have to read one value the same way.

    Args:
        status: A string, a behave status enum, or anything at all.

    Returns:
        The status name, preferring behave's ``normalized_name`` (which folds
        ``untested_undefined`` to ``undefined`` and both pending spellings to
        ``pending``) over the raw ``name``; ``""`` for ``None``; and
        ``str(status)`` for anything else, which :func:`canonical_status` then
        refuses rather than reporting as a pass.  Never raises.
    """
    if isinstance(status, str):
        return status
    if status is None:
        return ""
    for attribute in ("normalized_name", "name"):
        value = getattr(status, attribute, None)
        if isinstance(value, str) and value:
            return value
    return str(status)


def canonical_status(
    status: Any,
    *,
    matched: bool = True,
    dry_run: bool = False,
    fallback: str = UNKNOWN_STATUS,
    recorded: bool = True,
) -> str:
    """Normalise one status -- **the single canonicalisation point**.

    Every surface's grade for one step, hook, element or feature starts here:
    the two HTML writers through :func:`normalize_run`, the rerun manifest
    through ``app/reporting/rerun_report.py``'s failure predicate, and the
    machine-readable report through the ``fallback`` handover described below.
    Four operations, in this order:

    1. **Coerce to a name** with :func:`status_name`, so a behave status enum
       reads the same as the string the collector would have written.
    2. **Apply the dry-run rule**, when asked.  Under ``dryRun`` the JVM emits
       a matched step ``passed`` and an unmatched one ``undefined``, while
       behave records ``untested`` for both; so under ``dry_run`` the answer
       follows ``matched`` alone, the recorded name is never consulted, and the
       answer is never ``untested``.  Without this, a dry run published 19
       steps ``passed`` in the machine-readable JSON artifact and 60
       ``untested`` badges on the single-page HTML artifact from one document.
    3. **Fold the behave-only spellings** through :data:`STATUS_ALIASES`, so
       ``error``, ``hook_error``, ``cleanup_error`` and ``xfailed`` grade as
       ``failed`` on every surface rather than as an unrecognised token on two
       of them.
    4. **Answer with the token** when it is one of :data:`KNOWN_STATUSES`, and
       with ``fallback`` when it is not.

    Normalising and folding stay separate jobs: this function decides how a
    status is *spelled*, :func:`roll_up_status` decides *which* status an
    aggregate has, and :func:`counter_token` decides which *column* counts it.

    **The ``fallback`` boundary, stated rather than left silent.** This model's
    vocabulary has an eighth token, :data:`UNKNOWN_STATUS`, for a status
    nothing established; ``app/reporting/cucumber_json.py``'s vocabulary has no
    such name, because the publisher parses the seven Cucumber names and would
    mis-read an invented one, so its fallback is :data:`UNTESTED_STATUS`.  A
    caller holding to that contract passes ``fallback="untested"`` and gets
    that writer's answer for **every** input from this one implementation,
    ``unknown`` included: behave's own ``Status.unknown`` is a *recorded name*
    and folds through the table like the other nine, so a step the engine
    recorded as ``unknown`` grades ``untested`` on the report pages and in the
    artifact alike.

    **What ``recorded`` is for, and why one word needs two readings.** The
    word ``unknown`` is both a behave status name and this model's own
    :data:`UNKNOWN_STATUS` -- the token it answers with when a document
    recorded no outcome it recognises, which the statistics pages and the
    steps overview render as *Unknown*.  Those are different facts and the
    caller knows which it holds:

    * ``recorded=True``, the default and every **ingress** call
      (:func:`canonical_step_status`, :func:`hook_statuses`, decoration), reads
      the value as a name the document carried, so ``unknown`` folds to
      :data:`UNTESTED_STATUS`.
    * ``recorded=False``, which :func:`status_token` supplies, reads it as a
      value this module may itself have produced, so its own fallback token is
      echoed rather than folded.  That is what makes re-normalisation
      idempotent -- decoration writes a canonical status into the copy a fold
      then re-reads, and a feature whose element graded ``unknown`` must not
      read ``untested`` one level up.

    Args:
        status: A raw ``result.status``, a behave status enum, or anything at
            all: a number, ``None``, a container, or a key that was never
            there.
        matched: Whether a step definition was resolved for this step.  Read
            **only** under ``dry_run``, where it is the whole of the decision.
        dry_run: Whether the run that produced the status was a dry run; see
            :func:`is_dry_run`, which reads the flag off the document.
        fallback: The answer for a blank or unrecognised status.
        recorded: Whether ``status`` is a name the result document carried, as
            against a token this model produced; see above.

    Returns:
        One of :data:`KNOWN_STATUSES`, or ``fallback``.  Never raises.

    Examples:
        >>> canonical_status("Passed")
        'passed'
        >>> canonical_status("hook_error")
        'failed'
        >>> canonical_status(None)
        'unknown'
        >>> canonical_status(None, fallback=UNTESTED_STATUS)
        'untested'
        >>> canonical_status(UNKNOWN_STATUS)
        'untested'
        >>> canonical_status(UNKNOWN_STATUS, fallback=UNTESTED_STATUS)
        'untested'
        >>> canonical_status(UNKNOWN_STATUS, recorded=False)
        'unknown'
        >>> canonical_status("untested", matched=True, dry_run=True)
        'passed'
        >>> canonical_status("untested", matched=False, dry_run=True)
        'undefined'
    """
    if dry_run:
        return "passed" if matched else UNDEFINED_STATUS
    candidate = status_name(status).strip().lower()
    if not candidate:
        return fallback
    if not recorded and candidate == UNKNOWN_STATUS:
        return fallback
    candidate = STATUS_ALIASES.get(candidate, candidate)
    return candidate if candidate in KNOWN_STATUSES else fallback


def status_token(status: Any, fallback: str = UNKNOWN_STATUS) -> str:
    """Return the spelling of one status, for a value of either provenance.

    The historical name both HTML writers, the Pretty templates and their test
    modules import.  It is :func:`canonical_status` with ``recorded=False`` and
    nothing else: the same table, the same vocabulary and the same fallback,
    differing only in echoing this model's own :data:`UNKNOWN_STATUS` instead
    of reading it as behave's status name -- because its callers fold and count
    values *this module* produced as often as values a document carried, and a
    grade must not change on being read twice.

    Args:
        status: A raw ``result.status``, a token this module produced, a behave
            status enum, or anything at all.
        fallback: The answer for a blank or unrecognised status; see
            :func:`canonical_status` for the boundary this parameter names.

    Returns:
        One of :data:`KNOWN_STATUSES`, or ``fallback``.  Never raises.

    Examples:
        >>> status_token("Passed")
        'passed'
        >>> status_token("hook_error")
        'failed'
        >>> status_token(UNKNOWN_STATUS)
        'unknown'
        >>> status_token(None)
        'unknown'
    """
    return canonical_status(status, fallback=fallback, recorded=False)


def canonical_step_status(
    step: JsonDict,
    *,
    dry_run: bool = False,
    fallback: str = UNKNOWN_STATUS,
    recorded: bool = False,
) -> str:
    """Normalise one step's status, reading its own match state.

    The step-shaped entry point to :func:`canonical_status`, so that the
    dry-run rule -- which needs to know whether *this* step resolved to a
    definition -- is applied from the step object rather than reassembled by
    each caller.

    ``recorded`` is ``False`` by default because a step mapping reaches this
    function from **either** side of decoration: the document the collector
    merged, or the copy decoration wrote with canonical statuses already in it.
    The default therefore echoes this model's own :data:`UNKNOWN_STATUS`, which
    is what keeps a fold of a decorated element equal to a fold of the raw one
    underneath it.  The fold of behave's *recorded* ``unknown`` happens exactly
    once, where decoration builds that copy and passes ``recorded=True``; that
    is the model's ingress, and it is the counterpart of
    ``app/reporting/cucumber_json.py`` folding the same name as it builds the
    machine-readable report, which is why a run graded through
    :func:`normalize_run` and the same run published as JSON name the same
    status for every step.

    Args:
        step: A step object: ``result.status`` carries the outcome and
            ``matched`` whether a step definition was resolved.
        dry_run: Whether the run was a dry run.
        fallback: The answer for a blank or unrecognised status; see
            :func:`canonical_status` for the boundary this parameter names.
        recorded: Whether the status is a name the document carried, as against
            a token this model produced; see above and :func:`canonical_status`.

    Returns:
        One of :data:`KNOWN_STATUSES`, or ``fallback``.  Never raises.

    Note:
        A step that carries no boolean ``matched`` is read as **matched**,
        which is this module's convention for an absent flag (compare
        :func:`is_selected`) and the conservative answer: under ``dry_run`` the
        alternative would grade a hand-built step ``undefined`` and so invent a
        failure, putting a scenario nobody reported as failing into the rerun
        manifest.  ``app/reporting/events.py``'s ``new_step`` always records
        the flag, so every document the collector produces is unaffected; the
        JSON writer, which must decide the same question for its ``match``
        object, derives the flag from ``match.location`` when it is absent.
    """
    matched = step.get("matched")
    return canonical_status(
        as_mapping(step.get("result")).get("status"),
        matched=matched if isinstance(matched, bool) else True,
        dry_run=dry_run,
        fallback=fallback,
        recorded=recorded,
    )


def is_dry_run(result_set: Any) -> bool:
    """Report whether the document describes a dry run.

    The one reader of the flag :func:`app.reporting.events.new_result_set`
    records and :func:`app.reporting.events.merge_result_sets` folds across
    shards (true when any shard ran dry), so no consumer spells the key.

    Args:
        result_set: The merged result document, or anything at all.

    Returns:
        ``True`` only when the document carries a truthy ``dry_run``.  Never
        raises.
    """
    return bool(as_mapping(result_set).get("dry_run"))


def is_failure_token(status: Any) -> bool:
    """Report whether a status means the scenario unit failed.

    Membership of :data:`FAILURE_TOKENS` after canonicalisation, which is the
    complement of Cucumber's ``Status.isOk()``.  This is the predicate the
    rerun manifest selects on, so that "what the report calls a failure" and
    "what a retry re-executes" are one rule.

    Args:
        status: A raw or already-canonical status.

    Returns:
        ``True`` for ``failed``, ``undefined``, ``ambiguous`` and ``pending``
        and for every name :data:`STATUS_ALIASES` folds onto one of them;
        ``False`` for everything else, an absent status included.

    Examples:
        >>> is_failure_token("hook_error")
        True
        >>> is_failure_token("skipped")
        False
        >>> is_failure_token(None)
        False
    """
    return canonical_status(status) in FAILURE_TOKENS


def is_passed_token(status: Any) -> bool:
    """Report whether a status is a pass in 5.6.1's sense.

    ``Status.isPassed()`` in ``net.masterthought:cucumber-reporting:5.6.1`` is
    true for ``PASSED`` and for nothing else -- not for ``SKIPPED`` -- which is
    why its failures overview shows every element that is not a pass rather
    than only the ones spelled ``failed``.

    Args:
        status: A raw or already-canonical status.

    Returns:
        ``True`` only for ``passed`` and for the alias that folds onto it.

    Examples:
        >>> is_passed_token("xpassed")
        True
        >>> is_passed_token("skipped")
        False
    """
    return canonical_status(status) == VERDICT_PASSED


def roll_up_status(
    statuses: Iterable[Any],
    empty: str = EMPTY_ELEMENT_STATUS,
) -> str:
    """Fold a collection of statuses into the one that describes them all.

    The ordering is :data:`STATUS_PRECEDENCE`.  Each member is put through
    :func:`status_token` first, so a mixed collection of raw and
    already-canonical values is fine.

    Re-reading a token this model already produced is ordinary and it is
    exact: :func:`status_token` is idempotent on each of the eight tokens the
    model can answer with, :data:`UNKNOWN_STATUS` included, so folding
    :func:`step_statuses`' output gives the same answer as folding the raw
    statuses underneath it.  That is why this fold reads through that function
    rather than through :func:`canonical_status` directly: a feature whose
    element graded ``unknown`` must read ``unknown`` one level up, not
    ``untested``, which is what the recorded behave status of that name folds
    onto.

    Args:
        statuses: Raw or already-canonical statuses, in any order.
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
        >>> roll_up_status(["passed", UNKNOWN_STATUS])
        'unknown'
        >>> roll_up_status(["nonsense"], empty=UNKNOWN_STATUS)
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
    ambiguous step lands in the Undefined column of every statistics table
    this model feeds.  The
    severity precedence is untouched: for grading, ``ambiguous`` keeps its own
    rank between ``undefined`` and ``pending``.

    Args:
        status: A raw or already-normalised status.

    Returns:
        The column token: :data:`UNDEFINED_STATUS` for ``ambiguous``, otherwise
        :func:`status_token`'s answer -- the reading that echoes this model's
        own :data:`UNKNOWN_STATUS`, so a column tally and the badge above it
        cannot disagree about an element the model graded unknown.

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


def step_statuses(element: JsonDict, *, dry_run: bool = False) -> list[str]:
    """Return the canonical status of every step of ``element``, in order.

    Args:
        element: A Background or scenario element.
        dry_run: Whether the run was a dry run, in which case each step's own
            ``matched`` flag decides its status; see
            :func:`canonical_step_status`.  A caller reading a **decorated**
            element passes nothing: decoration has already applied the rule to
            the copy, which is what keeps the flag out of every template.

    Returns:
        One token per step.  A step whose result carries no status at all -- a
        step the run never reached -- contributes :data:`UNKNOWN_STATUS` rather
        than being dropped, so it cannot be silently read as a pass.
    """
    return [
        canonical_step_status(step, dry_run=dry_run)
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
        The dry-run rule is deliberately **not** applied to a hook: a hook is
        not a step, it resolves no step definition, and the JSON writer builds
        a hook's result with ``dry_run=False`` for the same reason, so the two
        surfaces grade one hook identically.  Read through
        :func:`status_token`, for the reason :func:`canonical_step_status`
        records: a hook mapping arrives from either side of decoration, and the
        recorded fold belongs at the one ingress rather than at every read.
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


def _unit_tokens(
    unit: Sequence[JsonDict],
    *,
    dry_run: bool = False,
) -> list[str]:
    """Return every canonical token a scenario unit is graded on.

    Args:
        unit: A unit from :func:`element_units`: a Background occurrence and
            the scenario it precedes, or a lone element.
        dry_run: Whether the run was a dry run.

    Returns:
        Every member's step tokens followed by its hook tokens, in member
        order.
    """
    tokens: list[str] = []
    for member in unit:
        tokens.extend(step_statuses(member, dry_run=dry_run))
        tokens.extend(hook_statuses(member))
    return tokens


def unit_status(
    unit: Sequence[JsonDict],
    empty: str = EMPTY_ELEMENT_STATUS,
    *,
    dry_run: bool = False,
) -> str:
    """Return the **effective** severity fold of one scenario unit.

    A scenario and the Background occurrence in front of it are one test case,
    and this is the single reading of it.  Three surfaces used to answer
    differently for the same run -- a Background-only failure was
    ``background=failed``/``scenario=skipped`` in the JSON artifact, *skipped*
    in the summary, *failed* on the Pretty pages and *selected* by the rerun
    manifest -- which is four gradings of one test case.  The JSON element
    shape is correct and measured (a Cucumber-JVM 7.2.3 probe emits the failed
    Background step and the scenario's own steps as ``skipped``), so what had
    to be settled is every *derived* reading, and it is settled here.

    The fold is over every member's steps **and** both hook groups, so a
    scenario whose setup hook failed before its steps ran is not reported as a
    pass either.

    Args:
        unit: A unit from :func:`element_units`.
        empty: The answer for a unit with no steps and no hooks -- the measured
            behaviour of ``EmployeeFc.feature``'s step-less Background and of
            5.6.1's empty ``StatusCounter``, both of which are a pass.
        dry_run: Whether the run was a dry run; see
            :func:`canonical_step_status`.

    Returns:
        The most severe status among the unit's steps and hooks; ``empty`` for
        a unit with neither; :data:`UNKNOWN_STATUS` when it has some but none
        carries a status the model recognises.

    Examples:
        >>> failed_background = {"type": "background",
        ...                      "steps": [{"result": {"status": "failed"}}]}
        >>> skipped_scenario = {"type": "scenario",
        ...                     "steps": [{"result": {"status": "skipped"}}]}
        >>> unit_status([failed_background, skipped_scenario])
        'failed'
        >>> unit_status([skipped_scenario])
        'skipped'
    """
    return _fold(_unit_tokens(unit, dry_run=dry_run), empty=empty)


def unit_verdict(unit: Sequence[JsonDict], *, dry_run: bool = False) -> str:
    """Return the binary PrettyReports verdict of one scenario unit.

    The companion of :func:`unit_status`, on the same members: a unit is passed
    only when every step and every hook of the Background occurrence *and* of
    the scenario passed, which is what makes 5.6.1's *total minus passed*
    scenario arithmetic count a Background-only failure as a failed scenario.
    A unit with nothing to count is passed -- ``StatusCounter``'s initial
    value.

    Args:
        unit: A unit from :func:`element_units`.
        dry_run: Whether the run was a dry run.

    Returns:
        :data:`VERDICT_PASSED` or :data:`VERDICT_FAILED`.
    """
    tokens = _unit_tokens(unit, dry_run=dry_run)
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
      ``scenarios_passed`` those of them whose **effective** verdict is passed,
      so ``scenarios_failed`` is the remainder -- which is
      ``getFailedScenarios()``, the count of elements the counter did not
      record as ``PASSED``, and not a search for a failed step;
    * ``status`` is the binary verdict over **every** element handed in,
      Background occurrences included.

    An unselected element contributes nothing at all, so a caller that has not
    already filtered cannot inflate a row with a scenario that never ran.

    The effective verdict is read from the element when
    :func:`decorate_feature` recorded one and computed from the element's own
    body otherwise, which is what lets a tag row -- whose subjects are
    scenario elements lifted away from their Background occurrences -- still
    count a Background-only failure as a failed scenario.  For every document
    this project's collector can produce the two coincide anyway: behave skips
    a scenario's own steps once its Background has failed, so the element's own
    verdict is already not a pass.

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
        recorded = element.get(EFFECTIVE_VERDICT_KEY)
        verdict = (
            recorded
            if recorded in (VERDICT_PASSED, VERDICT_FAILED)
            else element_verdict(element)
        )
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
      since backgrounds interleave and repeat.  A scenario is counted by its
      **effective** status, :func:`unit_status` over the Background occurrence
      in front of it and itself, so a Background-only failure counts as a
      failed scenario here exactly as it selects that scenario for a rerun and
      exactly as the Pretty pages badge it.  Counting the element's own body
      alone is what had this figure read *skipped* for a run the manifest
      called a failure.
    * **Features** -- the worst status among that feature's elements,
      Background occurrences included.  Deliberately left as the element fold:
      a feature's badge answers for everything beneath it, and the Background
      occurrence that failed is one of those things, so the feature reading
      needs no roll-up of its own.

    Args:
        features: The feature mappings the surface presents -- already
            selection-filtered by :func:`selected_features`, so the tally
            counts what the page shows and what the JSON artifact carries, and
            not the scenarios neither holds.  Decoration is not required:
            every figure is recomputed from the steps, so a hand-built feature
            list without a ``status`` key counts identically.  For a
            **decorated** list the recomputation reads the canonicalised
            copies, which is what makes the tally agree with the
            ``effective_status`` on each element and, under ``--dry-run``, with
            the mapped statuses the artifacts publish.

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
        for unit in element_units(elements):
            # One unit fold, reused for every scenario in the unit, so the
            # figure on a page and the badge above it are one reading of one
            # list rather than two derivations of one document.
            effective = unit_status(unit)
            for element in unit:
                step_tokens.extend(step_statuses(element))
                element_tokens.append(element_status(element))
                if is_scenario_element(element):
                    scenario_tokens.append(effective)
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


def _canonical_result(result: Any, status: str) -> JsonDict:
    """Return a copy of one ``result`` mapping carrying ``status``.

    Args:
        result: A step's or hook's ``result`` value, or anything at all.
        status: The canonical status to record.

    Returns:
        A new mapping with the same keys in the same order and ``status``
        replaced.  A result that declared no ``status`` gains one -- the token
        the model graded it on -- and **no other key is added**: a result
        carrying no ``duration`` must not acquire one, because the JSON
        artifact omits that key for a skipped step and a template renders its
        absence as an em dash rather than as a zero.
    """
    copied = dict(as_mapping(result))
    copied[_STATUS_KEY] = status
    return copied


def _canonical_steps(element: JsonDict, *, dry_run: bool) -> list[JsonDict]:
    """Return ``element``'s steps as copies carrying canonical statuses.

    Args:
        element: A Background or scenario element.
        dry_run: Whether the run was a dry run.

    Returns:
        One new step mapping per step, in order, each with its ``result``
        replaced by a copy whose ``status`` is
        :func:`canonical_step_status`'s answer.  Every other key of the step
        and of its result survives unchanged and in place.
    """
    return [
        {
            **step,
            "result": _canonical_result(
                step.get("result"),
                canonical_step_status(step, dry_run=dry_run, recorded=True),
            ),
        }
        for step in mappings(element.get("steps"))
    ]


def _canonical_hooks(element: JsonDict, key: str) -> list[JsonDict]:
    """Return one hook group of ``element`` as copies carrying canonical statuses.

    Args:
        element: A Background or scenario element.
        key: ``"before"`` or ``"after"``; see :data:`HOOK_KEYS`.

    Returns:
        One new hook mapping per entry, in order.  The dry-run rule is not
        applied, for the reason :func:`hook_statuses` documents.
    """
    return [
        {
            **hook,
            "result": _canonical_result(
                hook.get("result"),
                canonical_status(
                    as_mapping(hook.get("result")).get("status"), recorded=True
                ),
            ),
        }
        for hook in mappings(element.get(key))
    ]


def _canonical_element(element: JsonDict, *, dry_run: bool) -> JsonDict:
    """Return a copy of ``element`` whose recorded statuses are canonical.

    This is where the shared canonicalisation reaches the *rendered* page and
    not merely the computed figures.  Both HTML writers read
    ``step.result.status`` in their templates, so before this copy existed a
    step behave recorded as ``hook_error`` was published ``failed`` in the
    machine-readable JSON artifact and rendered *Unknown* on both HTML
    artifacts, and under ``--dry-run`` one document produced 19 ``passed``
    steps in that artifact and 60 ``untested`` badges on the single-page
    report.  Rewriting the copy is what
    makes the flag invisible downstream: no template and no writer has to know
    that the run was a dry run or that behave's vocabulary is wider than
    Cucumber's.

    Args:
        element: A Background or scenario element.
        dry_run: Whether the run was a dry run.

    Returns:
        A new mapping: the element's keys, with its attachments validated and
        with a new ``steps`` list and new hook lists whose statuses are
        canonical.  A key the element did not declare is not added -- an
        element with no ``before`` group does not gain an empty one -- and the
        input is never mutated: one merged document feeds four writers, and a
        writer that edited it in place would change what the others see.
    """
    normalized = normalize_element_attachments(element)
    if "steps" in normalized:
        normalized["steps"] = _canonical_steps(element, dry_run=dry_run)
    for key in HOOK_KEYS:
        if key in normalized:
            normalized[key] = _canonical_hooks(normalized, key)
    return normalized


def decorate_element(element: JsonDict, *, dry_run: bool = False) -> JsonDict:
    """Return a copy of ``element`` carrying its own aggregate.

    Args:
        element: A Background or scenario element.
        dry_run: Whether the run was a dry run.  :func:`normalize_run` reads it
            off the document with :func:`is_dry_run` and threads it through, so
            no caller downstream of that has to hold the flag.

    Returns:
        A new mapping: the element's keys with its attachments validated and
        its step and hook statuses canonicalised in the copy, plus ``status``
        (the severity fold), ``steps_status`` (the steps-only fold),
        ``verdict`` (the binary reading), ``effective_status`` and
        ``effective_verdict`` (the scenario-unit readings), ``duration_ns``,
        ``duration_samples`` and ``stats`` (this element's own statistics
        figures).  The original is untouched.

    Note:
        Decorated on its own, an element is its own unit, so
        ``effective_status`` equals ``status`` here.
        :func:`decorate_feature` is what pairs a scenario with the Background
        occurrence in front of it and fills the key with the unit's reading;
        both are written, so a consumer reads one key whichever route produced
        the element.
    """
    canonical = _canonical_element(element, dry_run=dry_run)
    duration_ns, samples = element_duration(canonical)
    return {
        **canonical,
        _STATUS_KEY: element_status(canonical),
        STEPS_STATUS_KEY: element_steps_status(canonical),
        _VERDICT_KEY: element_verdict(canonical),
        EFFECTIVE_STATUS_KEY: unit_status([canonical]),
        EFFECTIVE_VERDICT_KEY: unit_verdict([canonical]),
        _DURATION_KEY: duration_ns,
        _SAMPLES_KEY: samples,
        _STATS_KEY: stats_of([canonical]),
    }


def decorate_feature(feature: JsonDict, *, dry_run: bool = False) -> JsonDict:
    """Return a copy of ``feature`` with every level's aggregate filled in.

    Each element is decorated by :func:`decorate_element` and the feature's own
    values are rolled up from those copies, so a feature's badge is exactly the
    fold of the badges shown beneath it.

    The elements are also **paired into units** here -- a Background occurrence
    with the scenario it precedes, by :func:`element_units` -- and each
    scenario's ``effective_status`` and ``effective_verdict`` are the unit's
    readings rather than the element's own.  That is the whole of the
    Background fold: a Background-only failure leaves the JSON element shape
    exactly as the JVM writes it (the Background's step ``failed``, the
    scenario's own steps ``skipped``) while every derived reading of that
    scenario -- its badge, the failures overview, the scenario counts and the
    rerun manifest -- agrees that the test case failed.  A Background
    occurrence keeps its own reading in the same keys, so a consumer never has
    to ask which kind of element it is holding.

    Args:
        feature: A feature mapping, already selection-filtered.
        dry_run: Whether the run was a dry run; threaded to
            :func:`decorate_element`.

    Returns:
        A new feature mapping carrying a new element list, plus ``status``,
        ``verdict``, ``duration_ns``, ``duration_samples`` and ``stats``.
        Order is preserved throughout: elements exactly where the model puts
        them, each Background occurrence repeated in its own position.
    """
    elements = [
        decorate_element(element, dry_run=dry_run)
        for element in mappings(feature.get("elements"))
    ]
    for unit in element_units(elements):
        # The statuses in these copies are already canonical, so the unit is
        # folded with ``dry_run=False``: applying the rule twice would read a
        # ``matched`` flag against a status that no longer needs it.
        effective_status = unit_status(unit)
        effective_verdict = unit_verdict(unit)
        for member in unit:
            if is_background(member):
                continue
            member[EFFECTIVE_STATUS_KEY] = effective_status
            member[EFFECTIVE_VERDICT_KEY] = effective_verdict
            # The element's own figures are rebuilt from its own copy now that
            # the effective verdict is on it, so its ``scenarios_passed`` can
            # never disagree with the badge beside it.
            member[_STATS_KEY] = stats_of([member])
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


def decorated_features(
    features: Sequence[JsonDict],
    *,
    dry_run: bool = False,
) -> list[JsonDict]:
    """Return ``features`` decorated, in input order.

    Args:
        features: The feature mappings to decorate, already selection-filtered
            by :func:`selected_features`.
        dry_run: Whether the run was a dry run; threaded to
            :func:`decorate_feature`.

    Returns:
        A new list of new feature mappings; see :func:`decorate_feature`.
        Never raises.
    """
    return [decorate_feature(feature, dry_run=dry_run) for feature in features]


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
    decorate, tally, then build the rows the statistics tables show.  The
    document's ``dry_run`` flag is read **here**, once, by :func:`is_dry_run`,
    and threaded into decoration; every surface downstream reads statuses that
    already carry the dry-run mapping and never has to know the run's mode.

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
    features = decorated_features(
        selected_features(result_set),
        dry_run=is_dry_run(result_set),
    )
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
