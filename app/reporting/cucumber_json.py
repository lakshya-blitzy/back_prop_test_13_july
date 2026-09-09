"""The Cucumber-JVM JSON report writer -- the port's one machine-read artifact.

This module serialises the internal result document defined in
:mod:`app.reporting.events` into the Cucumber-JVM JSON report.  It is the
highest-risk file in the port, because the artifact is *machine input*: the
Jenkins pipeline's third stage runs the Cucumber publisher against it, and
``Jenkins:15``'s ``fileIncludePattern`` is narrowed from ``'**/*.json'`` to
that one artifact -- :data:`app.utils.paths.CUCUMBER_JSON_RELPATH`, which owns
the value -- precisely so that this file is the one the publisher reads.  A key
with the wrong name, an emitted ``[]`` where the JVM omits the key, or a float
where the JVM writes a nanosecond integer are all *silent* parity failures:
nothing crashes, the published report is simply wrong.

Where the contract comes from
-----------------------------
Two independent sources, which agree:

* **The committed baseline.**  ``tests/fixtures/golden_cucumber.json`` is the
  ``HEAD`` side of the reference repository's committed JSON report, stored
  verbatim -- 10,188 bytes, one feature (``Crm.feature``), 8 elements, 19 steps.
  Every "measured" claim below was read out of it.  (The committed reference
  file carries one unresolved merge-conflict block; the ``HEAD`` side alone
  parses as a complete report, while concatenating the two sides does not,
  which is why the fixture is that side taken alone.)
* **The generator.** ``io.cucumber:cucumber-core:7.2.3``'s
  ``JsonFormatter``/``TestSourcesModel``, which settle the rules a one-feature
  baseline cannot exercise -- ``createResultMap``'s omissions,
  ``createMatchMap``'s undefined-step case and ``calculateId``'s recursion.

The emitted contract, in full
-----------------------------
**Top level** is a JSON *list* of feature objects -- not an object and not
``{"features": ...}``.  A run that selected no scenario writes ``[]``, because
the plan's exit contract requires all four artifacts to exist even then, so the
publisher always has an input.

**Feature** keys, exactly ``uri``, ``id``, ``keyword``, ``line``, ``name``,
``description``, ``tags``, ``elements``:

* ``uri`` is ``file:``-prefixed and repository-relative: this port emits
  ``file:features/Crm.feature`` where the baseline carries the same filename
  under the Java resource directory (plan deviation 1 moved the features and
  preserved their filenames).  It is copied through from the internal
  document; the
  legacy-to-current prefix rewrite belongs to
  :func:`app.utils.paths.normalize_feature_uri`, which the *writer tests* apply
  to the golden fixture, not to this writer's output.
* ``line`` is the ``Feature:`` line and **not** the tag's line: ``@Smoke`` sits
  at ``Crm.feature:1`` and the emitted ``line`` is 2.
* ``description`` is ``""`` when empty -- never absent -- and leading
  indentation is verbatim (``"  Account is: PosManager"``).
* ``tags`` is the **long** shape, ``{"name": "@Smoke", "type": "Tag",
  "location": {"line": 1, "column": 1}}``, and the key is emitted
  **unconditionally**: ``createFeatureMap`` puts it with no emptiness guard, so
  a feature declaring no tag carries ``"tags": []``.  Five of the ten features
  declare none, so that is the common case -- and it is the exact **opposite**
  of the scenario-level rule below.  The two levels are never conflated.

**Elements** are Background occurrences and scenarios, *interleaved*: the
baseline's 8 elements are 4 backgrounds and 4 scenarios, with the background
repeated in full immediately before every scenario and all four occurrences
carrying the Background's own line (6).  ``handleTestCaseStarted`` is the
mechanism -- per test case, the background element is appended first, then the
scenario's.

* A **background** carries exactly ``keyword``, ``line``, ``name``,
  ``description``, ``type``, ``steps``: no ``id``, no ``tags``, no
  ``start_timestamp``, no ``after``.
* A **scenario** carries ``keyword``, ``line``, ``name``, ``description``,
  ``type``, ``id``, ``start_timestamp``, ``tags``, ``steps``, plus ``after``
  when a teardown hook produced an attachment.  ``type`` is the lowercase
  literal ``"scenario"``; ``keyword`` is ``"Scenario"`` or ``"Scenario
  Outline"``.
* Scenario ``tags`` use the **short** shape, ``{"name": "@Smoke"}``, and the
  key is **omitted entirely** when the scenario has no tags -- never ``[]``,
  because the JVM guards it with ``if (!testCase.getTags().isEmpty())``.
  Feature-level tags propagate onto every scenario, which
  :class:`app.reporting.events.ResultCollectorFormatter` already did, so they
  arrive here as ordinary scenario tags.
* **Scenarios the tag expression did not select are dropped**, together with
  the Background occurrence emitted for them: the JVM never starts them, so it
  never writes them, whereas behave announces them.  A feature left with no
  selected scenario **does not appear at all**, because the JVM creates a
  feature map only when a test case from that file starts.  That is what makes
  the baseline a one-feature document although the suite has ten features, and
  it is this writer's correctness check under the default ``@Smoke`` filter.
* Nothing is sorted.  Features stay in source order and scenarios in line
  order, as the merge left them; ``sortingMethod: 'ALPHABETICAL'``
  (``Jenkins:15``) is a publisher *display* option and imposes nothing here.

**Step** keys, exactly ``keyword``, ``line``, ``name``, ``match``, ``result``.
There is no ``step_type`` key -- measured, and asserted negatively in the
tests.  ``keyword`` keeps its single trailing space (``"Given "``, ``"And "``).
For an outline row, the step's ``line`` is the outline *template*'s step line
while the element's own ``line`` is the *data row*; that asymmetry is measured
and is not normalised.  ``name`` is the substituted text.

* ``match`` carries ``location`` -- a dotted Python path such as
  ``features.steps.crm_steps.user_can_change_information``, with no parentheses
  and no parameter types (plan deviation 8) -- **except for an undefined step**,
  where ``createMatchMap`` skips it and the emitted value is the empty object
  ``{}``.
* ``match.arguments`` appears only when the step took parameters, and is copied
  through unchanged: ``val`` is the raw matched substring *including* its
  surrounding double quotes and ``offset`` is its zero-based index into
  ``name`` -- the baseline's ``("\\"Test2\\"", 44)``, ``("\\"30\\"", 54)``,
  ``("\\"2\\"", 63)``.  Offsets are never recomputed here;
  :func:`app.reporting.events.widen_quoted_span` owns them.  An argument whose
  value is ``None`` contributes an empty ``{}`` entry rather than being
  dropped.
* ``result`` **omits fields rather than emitting zeros**, which is the whole of
  ``createResultMap``: ``status`` always and lowercase, ``error_message`` only
  when there is an error, and ``duration`` **only when it is non-zero** --
  whatever the status.  The baseline proves the last rule holds across statuses:
  it contains 14 ``{duration, status}`` passed, 2 ``{duration, error_message,
  status}`` failed, 2 bare ``{status}`` skipped *and* one ``{duration, status}``
  skipped with ``duration: 1000000``.  Durations are integer nanoseconds
  (``30202000000`` is 30.202 s); a float is never emitted.

**Scenario ``id``** is ``TestSourcesModel.calculateId``: a plain scenario is
``<feature-slug>;<scenario-slug>`` and an Examples row appends the Examples
block's slug and the row's position counting the header as 1, e.g.
``testinium-app-crm-module;user-can-change-information-in-dashboard;expected-name;2``.
The slug function replaces only whitespace, apostrophe, underscore, comma and
exclamation mark with ``-`` and lower-cases -- periods, colons, quotes and
parentheses survive -- so an unnamed ``Examples:`` block genuinely yields an
empty segment and a doubled separator, and two features that share a title
share an ``id``.  Both are source behaviours this writer **preserves rather
than disambiguates**, which is why ``app/web/routes.py`` keys its routes on a
feature's list position.  The single implementation lives in
:mod:`app.reporting.events` and is re-exported here as :func:`convert_to_id`
and :func:`scenario_element_id` so that the contract has one owner.

**``after`` and embeddings.**  No committed artifact contains an embedding --
``Hooks.java:5`` imported ``org.junit.After``, so Cucumber never invoked the
teardown -- so the shape follows from the attach call at ``Hooks.java:15`` plus
the generator, which hangs an attachment off the *test case* map rather than
off a step.  A failed scenario whose teardown captured a screenshot therefore
gains ``after: [{"match": {"location": ...}, "result": {...}, "embeddings":
[{"mime_type": "image/png", "data": "<base64>", "name": "<scenario name>"}]}]``.
``mime_type``'s underscore is the contract, not a typo.  When there is no
embedding -- the scenario passed, or capture failed and was suppressed under
plan deviation 19 -- **no** ``after`` entry is emitted, rather than an entry
with an empty ``embeddings`` list.

Boundaries and invariants
-------------------------
* The only intra-package imports are :mod:`app.reporting.events` and
  :mod:`app.utils.paths` (the plan's ``RP --> UT`` edge).  No service is
  imported -- the dependency edge is ``SV --> RP`` and never the reverse -- and
  neither Flask, Selenium, ``app.config``, ``app.pages``, ``app.automation``
  nor ``app.web`` appears, so this module is importable inside a worker process
  that never builds a Flask application.
* **No path literal.**  The destination comes from
  :func:`app.utils.paths.cucumber_json_path` and its parent from
  :func:`app.utils.paths.ensure_parent`.  Nothing is deleted or truncated
  beyond writing this one file: emptying the build-output directory is
  ``app/cli.py``'s ``--clean`` step.
* **Pure/impure split.**  :func:`build_cucumber_json` reads no clock, no
  working directory and no filesystem, and never mutates its input, so the
  golden-fixture comparison runs entirely in memory and the ``app/reporting``
  coverage gate is reachable without a browser.  :func:`write_cucumber_json` is
  the thin impure wrapper.
* **A test outcome never influences control flow.** ``pom.xml:25`` sets
  ``testFailureIgnore=true`` and all six ``Jenkins:15`` thresholds are ``-1``,
  so failures, undefined steps and skips are data to serialise and nothing
  more.  Only a genuine I/O or serialisation fault propagates, which the CLI
  reports as its writer-failure exit class.
* Output is UTF-8 with ``ensure_ascii=False``, so ``Veuillez renseigner ce
  champ.`` survives as characters, and compact (``","``/``":"`` separators plus
  one trailing newline) because that is the byte shape the reference artifact
  has.  Determinism is *structural*: timestamps, durations and traceback text
  vary by construction, while feature order, scenario order, the background's
  position and every key's presence or absence do not.
* Output is always clean: merge-conflict markers exist only in the reference
  checkout's committed artifacts and never in anything this writer produces.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, Final

from app.reporting.events import (
    BACKGROUND_KEYWORD,
    ELEMENT_TYPE_BACKGROUND,
    ELEMENT_TYPE_SCENARIO,
    FEATURE_KEYWORD,
    JsonDict,
    ResultSet,
    convert_to_id,
    feature_tag,
    scenario_element_id,
    scenario_tag,
)
from app.utils.paths import FILE_URI_SCHEME, cucumber_json_path, ensure_parent

__all__ = [
    "BACKGROUND_ELEMENT_KEYS",
    "CUCUMBER_STATUSES",
    "FEATURE_KEYS",
    "SCENARIO_ELEMENT_KEYS",
    "STATUS_ALIASES",
    "STATUS_FALLBACK",
    "STATUS_PASSED",
    "STATUS_UNDEFINED",
    "STEP_KEYS",
    "build_cucumber_json",
    "convert_to_id",
    "map_step_status",
    "normalize_error_message",
    "render_cucumber_json",
    "scenario_element_id",
    "write_cucumber_json",
]

#: Module logger.  Deliberately handler-free: ``app/logging_config.py`` installs
#: the split that routes WARNING-and-above to stderr, which is where the exit
#: contract expects a writer to name itself.
logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# The emitted vocabulary
# --------------------------------------------------------------------------- #

#: Status token for a step the JVM considers executed and successful.  Named
#: because the dry-run rule maps to it rather than to whatever behave recorded.
STATUS_PASSED: Final[str] = "passed"

#: Status token whose presence *removes* ``match.location``:
#: ``createMatchMap`` adds the location only ``if
#: (!result.getStatus().is(UNDEFINED))``, so an undefined step's ``match`` is
#: the empty object.
STATUS_UNDEFINED: Final[str] = "undefined"

#: The status vocabulary a Cucumber report may carry.  Anything outside this
#: set is folded by :data:`STATUS_ALIASES` or, failing that, by
#: :data:`STATUS_FALLBACK`, because the publisher parses these names and an
#: invented one would be silently mis-read.
CUCUMBER_STATUSES: Final[frozenset[str]] = frozenset(
    {
        STATUS_PASSED,
        "failed",
        "skipped",
        "pending",
        STATUS_UNDEFINED,
        "untested",
        "ambiguous",
    }
)

#: Status recorded when there is nothing to record: an absent status, or a name
#: outside both :data:`CUCUMBER_STATUSES` and :data:`STATUS_ALIASES`.
#: ``untested`` is the honest token for "this step has no outcome", and it is
#: also behave's own initial status, which is what
#: :mod:`app.reporting.events` writes when it is handed ``None``.
STATUS_FALLBACK: Final[str] = "untested"

#: behave status names that have no Cucumber counterpart, folded to the
#: nearest one.  behave 1.3.3's enum is wider than Cucumber's:
#:
#: * ``error`` is behave's name for an *exception* in a step, as against a
#:   failed assertion; Cucumber has one ``failed`` for both.  ``hook_error``
#:   and ``cleanup_error`` are the same distinction for hook code.
#: * ``xfailed``/``xpassed`` come from behave's expected-failure marking, which
#:   this suite never uses; folding them to the outcome that actually occurred
#:   keeps a hand-built document readable.
#: * ``pending_warn`` and ``untested_pending`` are behave's two spellings of
#:   pending, and ``untested_undefined`` its spelling of undefined.  behave's
#:   own ``Status.normalized_name`` already folds these three, so they are
#:   reached only from a document written by hand.
#: * ``executing`` and ``unknown`` describe a step whose outcome was never
#:   established -- a worker killed mid-step, say -- which is exactly
#:   ``untested``.
STATUS_ALIASES: Final[dict[str, str]] = {
    "error": "failed",
    "hook_error": "failed",
    "cleanup_error": "failed",
    "xfailed": "failed",
    "xpassed": STATUS_PASSED,
    "pending_warn": "pending",
    "untested_pending": "pending",
    "untested_undefined": STATUS_UNDEFINED,
    "executing": STATUS_FALLBACK,
    "unknown": STATUS_FALLBACK,
}

#: Feature keys, in emission order.  Exposed so that
#: ``tests/test_cucumber_json.py`` can assert the key *set* against one
#: definition instead of a literal repeated per assertion.
FEATURE_KEYS: Final[tuple[str, ...]] = (
    "uri",
    "id",
    "keyword",
    "line",
    "name",
    "description",
    "tags",
    "elements",
)

#: Background-element keys, in emission order.  Deliberately poorer than a
#: scenario's: no ``id``, ``tags``, ``start_timestamp`` or ``after``.
BACKGROUND_ELEMENT_KEYS: Final[tuple[str, ...]] = (
    "keyword",
    "line",
    "name",
    "description",
    "type",
    "steps",
)

#: Scenario-element keys, in emission order.  ``tags`` is present only when the
#: scenario has some and ``after`` only when a hook produced an attachment, so
#: this is the maximal set rather than a guaranteed one.
SCENARIO_ELEMENT_KEYS: Final[tuple[str, ...]] = (
    "keyword",
    "line",
    "name",
    "description",
    "type",
    "id",
    "start_timestamp",
    "tags",
    "steps",
    "after",
)

#: Step keys, in emission order.  All five are always present; ``step_type`` is
#: **not** among them.
STEP_KEYS: Final[tuple[str, ...]] = ("keyword", "line", "name", "match", "result")

#: Serialisation options.  Compact separators and ``ensure_ascii=False``
#: reproduce the reference artifact's byte shape -- it is a single 10,188-byte
#: line with a trailing newline and unescaped punctuation -- and
#: ``allow_nan=False`` turns the one value that would produce invalid JSON into
#: a raised, reportable serialisation fault instead.  Keys are never sorted:
#: order is not semantic here, and sorting would obscure the emission order the
#: builders document.
_JSON_DUMP_KWARGS: Final[dict[str, Any]] = {
    "ensure_ascii": False,
    "separators": (",", ":"),
    "sort_keys": False,
    "allow_nan": False,
}

#: Trailing newline of the artifact, as the reference file carries one.
_TRAILING_NEWLINE: Final[str] = "\n"


# --------------------------------------------------------------------------- #
# Value helpers.
#
# Every value this module emits passes through one of these, which is what makes
# a serialisation fault impossible by construction: the document contains only
# ``str``, ``int``, ``list`` and ``dict``.  A malformed input field therefore
# degrades to an empty string or a zero -- diagnosable in the published report --
# instead of taking down a writer whose failure the exit table treats as fatal.
# --------------------------------------------------------------------------- #


def _as_text(value: Any) -> str:
    """Coerce ``value`` to the text the report contract expects.

    Args:
        value: Any field read out of the internal document.

    Returns:
        ``value`` itself when it is already a string, ``""`` when it is
        ``None``, and ``str(value)`` otherwise.  ``None`` deliberately does not
        become ``"None"``: an absent name, keyword or description is empty, and
        the JVM writes ``""`` for it.
    """
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return str(value)


def _as_int(value: Any) -> int:
    """Coerce ``value`` to the integer the report contract expects.

    Durations are integer nanoseconds and lines and offsets are integer
    positions; the JVM never writes a float for any of them.

    Args:
        value: Any numeric field read out of the internal document.

    Returns:
        The value as an :class:`int`, truncating a float (a fractional
        nanosecond has no meaning) and yielding ``0`` for ``None``, for a
        :class:`bool` -- which is a numeric type in Python but never a
        duration -- and for anything that cannot be interpreted numerically.
    """
    if isinstance(value, bool) or value is None:
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        try:
            return int(value)
        except (OverflowError, ValueError):
            return 0
    if isinstance(value, str):
        try:
            return int(value.strip(), 10)
        except ValueError:
            return 0
    return 0


def normalize_error_message(text: Any) -> str:
    """Normalise a failure message's line endings to LF, and nothing else.

    This is the single owner of that normalisation (plan deviation 16).  The
    reference artifact's failure text is a JUnit assertion message followed by
    a Java stack trace with ``\\r\\n`` endings -- ``"java.lang.AssertionError:
    expected:<8> but was:<89>\\r\\n\\tat org.junit.Assert.fail(...)"`` -- and its
    Selenium failure mixes ``\\r\\n`` with bare ``\\n``.  Python can produce
    neither, so the port emits the assertion's own message text, preserved
    verbatim from the Java assertion strings, followed by the Python traceback,
    with the endings unified here.  **The assertion subject and message are
    parity; their surrounding formatting is not** -- no ``expected:<...> but
    was:<...>`` framing is synthesised and no Java frame is faked.

    Args:
        text: The raw message from the internal document.

    Returns:
        The message with every ``\\r\\n`` and every lone ``\\r`` replaced by
        ``\\n``.  The function is idempotent, so applying it after
        :mod:`app.reporting.events` has already normalised a message is
        harmless.
    """
    return _as_text(text).replace("\r\n", "\n").replace("\r", "\n")


def map_step_status(
    status: Any,
    *,
    matched: bool = True,
    dry_run: bool = False,
) -> str:
    """Map a recorded step status onto the Cucumber vocabulary.

    The only owner of the status rules, because every consumer of this
    artifact -- the publisher, both HTML writers and the HTTP views -- reads the
    names this function produces:

    * **Dry run.** Under ``dryRun`` the JVM emits a matched step ``passed`` and
      an unmatched one ``undefined``, while behave reports ``untested`` for
      both.  So under ``dry_run`` the answer follows ``matched`` alone and is
      never ``untested``.  (``dryRun = false`` was the runner's default, so
      this path is opt-in through ``run-tests --dry-run``, but it must be
      correct.)
    * **``error`` is ``failed``.** behave distinguishes an exception from a
      failed assertion; Cucumber does not.  See :data:`STATUS_ALIASES` for the
      whole fold.
    * **``skipped`` is genuine** for a step after a failure in the same
      scenario -- the baseline carries three of them -- and is passed through
      untouched.

    Presentation-layer normalisation is *not* done here:
    ``app/templates/partials/status_badge.html``'s ``status_token`` owns the
    token a template renders.

    Args:
        status: The recorded status: behave's normalised name as a string, a
            behave status enum, or ``None``.
        matched: Whether a step definition was resolved for this step.  Read
            only under ``dry_run``, where it is the whole of the decision.
        dry_run: Whether the run that produced the status was a dry run.

    Returns:
        A member of :data:`CUCUMBER_STATUSES`.

    Examples:
        >>> map_step_status("passed")
        'passed'
        >>> map_step_status("error")
        'failed'
        >>> map_step_status("untested", matched=True, dry_run=True)
        'passed'
        >>> map_step_status("untested", matched=False, dry_run=True)
        'undefined'
        >>> map_step_status(None)
        'untested'
    """
    if dry_run:
        return STATUS_PASSED if matched else STATUS_UNDEFINED

    name = _status_text(status).strip().lower()
    if not name:
        return STATUS_FALLBACK
    name = STATUS_ALIASES.get(name, name)
    if name in CUCUMBER_STATUSES:
        return name
    logger.debug(
        "Unrecognised step status %r recorded as %r", status, STATUS_FALLBACK
    )
    return STATUS_FALLBACK


def _status_text(status: Any) -> str:
    """Return the status *name* for a value that may be an enum.

    The internal document stores status names as strings, so this is only
    reached when a caller hands over a behave ``Status`` directly -- which a
    test or a hand-built document may legitimately do.

    Args:
        status: A string, a behave status enum, or ``None``.

    Returns:
        The status name, preferring behave's ``normalized_name`` (which folds
        ``untested_undefined`` to ``undefined`` and both pending spellings to
        ``pending``) over the raw ``name``, and ``""`` for ``None``.
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


def _as_mapping(value: Any) -> JsonDict:
    """Return ``value`` when it is a mapping, and an empty mapping otherwise.

    Args:
        value: A nested field -- a step's ``match`` or ``result``, a hook
            entry, a tag -- read out of the internal document.

    Returns:
        The mapping, or ``{}``.  A malformed nesting therefore yields the
        emptiest legal shape rather than an exception, keeping a writer failure
        out of a run whose test outcomes must not influence control flow.
    """
    return value if isinstance(value, dict) else {}


def _mappings(value: Any) -> list[JsonDict]:
    """Return the mappings in ``value``, skipping anything else.

    Args:
        value: A list-valued field -- ``tags``, ``arguments``, ``after``,
            ``embeddings``, ``elements`` -- read out of the internal document.

    Returns:
        The mapping entries, in input order.  A string, ``None`` or a scalar
        yields ``[]``; a string is *not* iterated character by character.
    """
    if isinstance(value, (dict, str, bytes)) or not isinstance(value, Iterable):
        return []
    return [entry for entry in value if isinstance(entry, dict)]


# --------------------------------------------------------------------------- #
# Step-level builders
# --------------------------------------------------------------------------- #


def _build_result(result: JsonDict, *, matched: bool, dry_run: bool) -> JsonDict:
    """Build a ``result`` map, omitting fields rather than emitting zeros.

    ``createResultMap`` is the whole rule and it has three clauses: ``status``
    always, ``error_message`` only when the result carries an error, and
    ``duration`` only when it is non-zero.  The last clause is *not* qualified
    by status -- the baseline contains both a bare ``{"status": "skipped"}``
    and a ``{"duration": 1000000, "status": "skipped"}``, which is why this
    writer tests the duration and never the status when deciding.

    Args:
        result: The internal result mapping.
        matched: Whether the step resolved to a definition; see
            :func:`map_step_status`.
        dry_run: Whether the run was a dry run.

    Returns:
        The result map.  ``status`` is always present, so a consumer never has
        to test for it.
    """
    status = map_step_status(result.get("status"), matched=matched, dry_run=dry_run)
    built: JsonDict = {"status": status}

    duration = _as_int(result.get("duration"))
    if duration:
        built["duration"] = duration

    message = normalize_error_message(result.get("error_message"))
    if message:
        built["error_message"] = message

    return built


def _build_arguments(arguments: Any) -> list[JsonDict]:
    """Build a step's ``match.arguments`` list, copied through unchanged.

    ``val`` is the raw matched substring of the step name *including* its
    surrounding double quotes and ``offset`` is its zero-based index into that
    name, so ``name[offset:offset + len(val)] == val`` holds.  The reference
    step ``User can change any user's information like "Test2" , "30" and "2"``
    records ``("\\"Test2\\"", 44)``, ``("\\"30\\"", 54)`` and ``("\\"2\\"", 63)``.
    Those spans are produced by :func:`app.reporting.events.widen_quoted_span`
    at collection time and are **never recomputed here**: an offset computed
    against a differently-substituted name would be silently wrong.

    Args:
        arguments: The internal ``match.arguments`` value.

    Returns:
        The argument entries, in order of appearance.  An entry whose ``val``
        is ``None`` becomes an empty ``{}`` rather than being dropped, exactly
        as ``createMatchMap`` does when ``argument.getValue()`` is null -- the
        list's length is part of the contract, because it is the arity of the
        step's parameters.
    """
    built: list[JsonDict] = []
    for argument in _mappings(arguments):
        value = argument.get("val")
        if value is None:
            built.append({})
            continue
        built.append(
            {"val": _as_text(value), "offset": _as_int(argument.get("offset"))}
        )
    return built


def _build_match(match: JsonDict, *, status: str) -> JsonDict:
    """Build a step's ``match`` map.

    Args:
        match: The internal match mapping.
        status: The step's *mapped* status, because it decides whether
            ``location`` is emitted at all.

    Returns:
        The match map: ``arguments`` when the step took parameters, and
        ``location`` unless the mapped status is ``undefined``.  An undefined
        step therefore yields ``{}`` -- ``createMatchMap`` adds the location
        only ``if (!result.getStatus().is(UNDEFINED))``.  ``arguments`` is
        emitted first because that is the order the reference artifact carries;
        object key order is not semantic, but matching it costs nothing.
    """
    built: JsonDict = {}

    arguments = _build_arguments(match.get("arguments"))
    if arguments:
        built["arguments"] = arguments

    if status != STATUS_UNDEFINED:
        location = _as_text(match.get("location"))
        if location:
            built["location"] = location

    return built


def _build_step(step: JsonDict, *, dry_run: bool) -> JsonDict:
    """Build one step object.

    Args:
        step: The internal step object.
        dry_run: Whether the run was a dry run.

    Returns:
        A mapping carrying exactly :data:`STEP_KEYS`.  ``matched`` -- which the
        internal schema carries and the JVM does not -- is consumed by the
        status and location rules and then dropped, and no ``step_type`` key is
        added.  ``keyword`` keeps the trailing space
        :func:`app.reporting.events.step_keyword` gave it, and ``line`` is
        copied through, so an outline row's steps keep the outline template's
        line while the element keeps the data row's.
    """
    match = _as_mapping(step.get("match"))
    result = _as_mapping(step.get("result"))

    matched = step.get("matched")
    if not isinstance(matched, bool):
        # A hand-built document may omit the flag; a step that resolved to a
        # location is by definition one that matched.
        matched = bool(_as_text(match.get("location")))

    built_result = _build_result(result, matched=matched, dry_run=dry_run)
    return {
        "keyword": _as_text(step.get("keyword")),
        "line": _as_int(step.get("line")),
        "name": _as_text(step.get("name")),
        "match": _build_match(match, status=built_result["status"]),
        "result": built_result,
    }


def _build_steps(steps: Any, *, dry_run: bool) -> list[JsonDict]:
    """Build every step of one element, in order.

    Args:
        steps: The internal element's ``steps`` value.
        dry_run: Whether the run was a dry run.

    Returns:
        The step objects, in input order -- never sorted, because a step's
        position is its position in the scenario.
    """
    return [_build_step(step, dry_run=dry_run) for step in _mappings(steps)]


# --------------------------------------------------------------------------- #
# After-hook and embedding builders
# --------------------------------------------------------------------------- #


def _build_embeddings(embeddings: Any) -> list[JsonDict]:
    """Build a hook entry's ``embeddings`` list.

    The shape follows ``Hooks.java:15``'s ``scenario.attach(screenshot,
    "image/png", scenario.getName())``: bytes, a MIME type and a name.
    ``app/reporting/screenshots.py`` produces the mapping and this writer only
    serialises it -- the base64 encoding is *not* redone here, and the data is
    emitted unchunked.  ``mime_type``'s underscore spelling is the contract:
    the generator's own comment records that it should have been the media
    type and that renaming it was not worth the migration.

    Args:
        embeddings: The internal hook entry's ``embeddings`` value.

    Returns:
        One mapping per attachment, carrying ``mime_type``, ``data`` and --
        only when the internal entry actually has one, mirroring the
        generator's ``if (name != null)`` -- ``name``.  An entry with no
        ``data`` is dropped: a screenshot that failed to capture is suppressed
        under plan deviation 19 and must leave no trace, and an embedding whose
        data is empty would render as a broken image in both HTML reports.
    """
    built: list[JsonDict] = []
    for embedding in _mappings(embeddings):
        data = _as_text(embedding.get("data"))
        if not data:
            logger.debug("Dropping an attachment that carries no data")
            continue
        entry: JsonDict = {
            "mime_type": _as_text(embedding.get("mime_type")),
            "data": data,
        }
        if embedding.get("name") is not None:
            entry["name"] = _as_text(embedding.get("name"))
        built.append(entry)
    return built


def _build_after(after: Any) -> list[JsonDict]:
    """Build a scenario's ``after`` array.

    The array lives on the scenario element rather than on a step, because
    ``addHookStepToTestCaseMap`` puts ``AFTER`` hooks on the test-case map.

    Args:
        after: The internal scenario's ``after`` value.

    Returns:
        One entry per hook that produced an attachment, each carrying
        ``match``, ``result`` and ``embeddings``.  **A hook with no embedding
        contributes no entry**, so a passing scenario -- and a failed one whose
        capture was suppressed under plan deviation 19 -- yields an empty list
        and :func:`_build_scenario` then omits the key entirely.  That keeps
        the emitted document identical in shape to the reference, where the
        teardown hook never ran and no element carries ``after`` at all.
    """
    built: list[JsonDict] = []
    for entry in _mappings(after):
        embeddings = _build_embeddings(entry.get("embeddings"))
        if not embeddings:
            continue
        match: JsonDict = {}
        location = _as_text(_as_mapping(entry.get("match")).get("location"))
        if location:
            match["location"] = location
        built.append(
            {
                "match": match,
                # A hook's outcome is its own: it is neither dry-run mapped
                # (behave runs no hooks in a dry run) nor dependent on a step
                # match, so the recorded status passes straight through the
                # same three-clause result rule.
                "result": _build_result(
                    _as_mapping(entry.get("result")), matched=True, dry_run=False
                ),
                "embeddings": embeddings,
            }
        )
    return built


# --------------------------------------------------------------------------- #
# Tag builders.  The two levels have deliberately different shapes and
# deliberately different emptiness rules; conflating them is the single most
# likely way to break this artifact.
# --------------------------------------------------------------------------- #


def _build_feature_tags(tags: Any, *, line: int) -> list[JsonDict]:
    """Build a feature's ``tags`` list in the JVM's long shape.

    Args:
        tags: The internal feature's ``tags`` value, normally already
            long-shape mappings from :func:`app.reporting.events.feature_tag`.
        line: The feature's own line, used only as the declaration line of a
            tag that arrived as a bare string and therefore carries no
            location of its own.

    Returns:
        A list of ``{"name": ..., "type": "Tag", "location": {"line": ...,
        "column": ...}}`` mappings, with the leading ``@`` retained.  The list
        may be empty, and the *caller emits the key regardless* -- feature-level
        ``tags`` is unconditional.
    """
    built: list[JsonDict] = []
    for tag in tags or ():
        if isinstance(tag, str):
            if tag:
                built.append(feature_tag(tag, line))
            continue
        if not isinstance(tag, dict):
            continue
        name = _as_text(tag.get("name"))
        if not name:
            continue
        location = _as_mapping(tag.get("location"))
        built.append(
            feature_tag(
                name,
                _as_int(location["line"]) if "line" in location else line,
                _as_int(location["column"]) if "column" in location else 1,
            )
        )
    return built


def _build_scenario_tags(tags: Any) -> list[JsonDict]:
    """Build a scenario's ``tags`` list in the JVM's short shape.

    A scenario tag is ``{"name": "@Smoke"}`` and nothing else: no ``type`` and
    no ``location``, even though the same tag carries both at feature level.
    Feature-level tags have already been propagated onto the scenario by
    :class:`app.reporting.events.ResultCollectorFormatter`, so they arrive here
    as ordinary scenario tags and are not re-propagated.

    Args:
        tags: The internal scenario's ``tags`` value.

    Returns:
        The short-shape tags.  An empty list makes the caller **omit the key
        entirely** rather than emit ``[]``: the JVM guards it with ``if
        (!testCase.getTags().isEmpty())``, and with five of the ten features
        declaring no tag, omission is the common case.
    """
    built: list[JsonDict] = []
    for tag in tags or ():
        if isinstance(tag, str):
            name = tag
        elif isinstance(tag, dict):
            name = _as_text(tag.get("name"))
        else:
            continue
        if name:
            built.append(scenario_tag(name))
    return built


# --------------------------------------------------------------------------- #
# Element builders and the selection rule
# --------------------------------------------------------------------------- #


def _is_selected(element: JsonDict) -> bool:
    """Report whether an element's scenario was selected by the tag expression.

    Args:
        element: An internal element.

    Returns:
        ``element["selected"]`` as a boolean.  An **absent** flag counts as
        selected, because a hand-built document that omits it means "this ran",
        and a ``None`` counts as selected too: over-reporting a scenario is
        recoverable, silently dropping a real result is not -- which is the
        same judgement :class:`app.reporting.events.ResultCollectorFormatter`
        makes when behave cannot answer.
    """
    value = element.get("selected", True)
    return True if value is None else bool(value)


def _element_units(elements: Sequence[JsonDict]) -> list[list[JsonDict]]:
    """Group elements into Background-occurrence-plus-scenario units.

    The Background occurrence emitted for a scenario belongs immediately in
    front of it and shares its fate: if the scenario is dropped because the tag
    expression did not select it, its Background occurrence must go with it, or
    the artifact would carry a background for a test case that never appears.
    Grouping first is what makes that exact.

    The grouping is local to this writer rather than borrowed, because the
    *rule* it serves -- selection -- is this writer's, and the merge in
    :mod:`app.reporting.events` groups for a different purpose (ordering).

    Args:
        elements: One feature's internal elements, in document order.

    Returns:
        The units, in input order: ``[background, scenario]`` normally,
        ``[scenario]`` for a feature with no Background, and ``[background]``
        for the pathological trailing occurrence with no scenario, which is
        kept as a unit of its own rather than attached to something it did not
        precede.
    """
    units: list[list[JsonDict]] = []
    for element in elements:
        if element.get("type") == ELEMENT_TYPE_BACKGROUND:
            units.append([element])
            continue
        if (
            units
            and len(units[-1]) == 1
            and units[-1][0].get("type") == ELEMENT_TYPE_BACKGROUND
        ):
            units[-1].append(element)
        else:
            units.append([element])
    return units


def _build_background(element: JsonDict, *, dry_run: bool) -> JsonDict:
    """Build a Background occurrence.

    Args:
        element: The internal background element.
        dry_run: Whether the run was a dry run.

    Returns:
        A mapping carrying exactly :data:`BACKGROUND_ELEMENT_KEYS`.  A
        background has **no** ``id``, ``tags``, ``start_timestamp`` or
        ``after``, whatever the internal element happens to carry: that is
        measured across all four occurrences in the reference, not a stylistic
        choice.  ``type`` is the lowercase literal ``"background"`` and
        ``keyword`` falls back to ``"Background"`` only when the internal
        element carries none.
    """
    return {
        "keyword": _as_text(element.get("keyword")) or BACKGROUND_KEYWORD,
        "line": _as_int(element.get("line")),
        "name": _as_text(element.get("name")),
        "description": _as_text(element.get("description")),
        "type": ELEMENT_TYPE_BACKGROUND,
        "steps": _build_steps(element.get("steps"), dry_run=dry_run),
    }


def _build_scenario(
    element: JsonDict,
    *,
    feature_name: str,
    dry_run: bool,
) -> JsonDict:
    """Build a scenario element.

    Args:
        element: The internal scenario element.
        feature_name: The owning feature's name, used only to rebuild an ``id``
            for a hand-built element that carries none.
        dry_run: Whether the run was a dry run.

    Returns:
        A mapping whose keys are :data:`SCENARIO_ELEMENT_KEYS`, less ``tags``
        when the scenario has none and less ``after`` when no teardown hook
        produced an attachment.  ``id`` and ``start_timestamp`` are always
        present -- the first from the collector, which computed it with the
        JVM's own recursion, the second passed through byte-for-byte because
        :func:`app.reporting.events.format_timestamp` already emits the
        contract's millisecond-precision UTC form with a literal ``Z``.
    """
    identifier = _as_text(element.get("id"))
    if not identifier:
        # Only reachable for a document that did not come from the collector.
        # A plain scenario's id is derivable; an Examples row's is not, because
        # the block name and row position are not carried on the element.
        identifier = scenario_element_id(feature_name, _as_text(element.get("name")))

    timestamp = element.get("start_timestamp")
    built: JsonDict = {
        "keyword": _as_text(element.get("keyword")),
        "line": _as_int(element.get("line")),
        "name": _as_text(element.get("name")),
        "description": _as_text(element.get("description")),
        "type": ELEMENT_TYPE_SCENARIO,
        "id": identifier,
        # The key is part of the scenario contract, so it is emitted even when
        # a hand-built element has no value for it; a null reads as "unknown"
        # to every consumer, where a missing key would read as a shape change.
        "start_timestamp": timestamp if isinstance(timestamp, str) else None,
    }

    tags = _build_scenario_tags(element.get("tags"))
    if tags:
        built["tags"] = tags

    built["steps"] = _build_steps(element.get("steps"), dry_run=dry_run)

    after = _build_after(element.get("after"))
    if after:
        built["after"] = after

    return built


# --------------------------------------------------------------------------- #
# Feature builder
# --------------------------------------------------------------------------- #


def _feature_uri(feature: JsonDict) -> str:
    """Return a feature's ``file:``-prefixed URI.

    Args:
        feature: The internal feature object.

    Returns:
        ``feature["uri"]`` copied through verbatim -- it is not rebuilt, and the
        legacy-prefix rewrite is :func:`app.utils.paths.normalize_feature_uri`'s
        job on the *fixture* side of a comparison, never this writer's.  When a
        hand-built document carries only ``path``, the scheme constant from
        :mod:`app.utils.paths` is prefixed to it, which is the one derivation
        allowed here and still contains no path literal.
    """
    uri = _as_text(feature.get("uri"))
    if uri:
        return uri
    path = _as_text(feature.get("path"))
    return f"{FILE_URI_SCHEME}{path}" if path else ""


def _build_feature(feature: JsonDict, *, dry_run: bool) -> JsonDict | None:
    """Build one feature object, or decline to emit it.

    Args:
        feature: The internal feature object.
        dry_run: Whether the run was a dry run.

    Returns:
        A mapping carrying exactly :data:`FEATURE_KEYS`, or ``None`` when the
        feature must not appear in the artifact at all -- which is the case
        whenever no selected scenario survives the filter, because the JVM
        creates a feature map only when a test case from that file *starts*.
        Under the default ``@Smoke`` filter that is what reduces the suite's
        ten features to the single one the reference artifact carries.

        A feature left with nothing but Background occurrences is declined for
        the same reason: an occurrence is emitted *for* a test case, so one
        without its scenario represents no test case at all.
    """
    units = [
        unit
        for unit in _element_units(_mappings(feature.get("elements")))
        if all(_is_selected(element) for element in unit)
    ]
    # "Not a background" rather than "is a scenario", so that an element of an
    # unexpected type is treated as a test case here exactly as it is by the
    # build loop below and by :func:`app.reporting.events.new_element`, which
    # records an unknown type as a scenario rather than losing its results.
    if not any(
        element.get("type") != ELEMENT_TYPE_BACKGROUND
        for unit in units
        for element in unit
    ):
        return None

    name = _as_text(feature.get("name"))
    line = _as_int(feature.get("line"))

    elements: list[JsonDict] = []
    for unit in units:
        for element in unit:
            if element.get("type") == ELEMENT_TYPE_BACKGROUND:
                elements.append(_build_background(element, dry_run=dry_run))
            else:
                elements.append(
                    _build_scenario(element, feature_name=name, dry_run=dry_run)
                )

    return {
        "uri": _feature_uri(feature),
        "id": _as_text(feature.get("id")) or convert_to_id(name),
        "keyword": _as_text(feature.get("keyword")) or FEATURE_KEYWORD,
        "line": line,
        "name": name,
        "description": _as_text(feature.get("description")),
        # Unconditional, and empty for the five features that declare no tag.
        "tags": _build_feature_tags(feature.get("tags"), line=line),
        "elements": elements,
    }


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def build_cucumber_json(result_set: ResultSet | None) -> list[JsonDict]:
    """Build the Cucumber-JVM JSON document from a merged result set.

    The pure half of this writer: it reads no clock, no working directory and
    no filesystem, and it never mutates ``result_set`` -- every emitted mapping
    is newly constructed, so a caller may build, inspect and build again and
    compare the two structures for equality.  That is what lets the
    golden-fixture comparison run in memory and the ``app/reporting`` coverage
    gate be met without a browser.

    Args:
        result_set: The merged internal document from
            :func:`app.reporting.events.merge_result_sets`, or a hand-built one
            in the same schema.  ``None`` and any non-mapping are accepted and
            yield an empty document, because a run that produced nothing must
            still leave the publisher a readable file rather than an exception.

    Returns:
        A list of feature objects in the Cucumber-JVM schema -- **a list, not
        an object** -- with the features in the order the merge established and
        nothing sorted.  Non-selected scenarios and the Background occurrences
        emitted for them are dropped, and a feature with no surviving scenario
        is omitted, so a run whose tag expression selected nothing yields
        ``[]``.

    Examples:
        >>> build_cucumber_json({"features": []})
        []
        >>> build_cucumber_json(None)
        []
    """
    if not isinstance(result_set, dict):
        if result_set is not None:
            logger.warning(
                "Result set is a %s, not a document; writing an empty report",
                type(result_set).__name__,
            )
        return []

    dry_run = bool(result_set.get("dry_run"))
    document: list[JsonDict] = []
    for feature in _mappings(result_set.get("features")):
        built = _build_feature(feature, dry_run=dry_run)
        if built is not None:
            document.append(built)
    return document


def render_cucumber_json(result_set: ResultSet | None) -> str:
    """Render the artifact's exact text, without touching the filesystem.

    Args:
        result_set: As :func:`build_cucumber_json`.

    Returns:
        The complete file content: compact JSON -- ``","``/``":"`` separators,
        no key sorting -- with non-ASCII characters left as characters so the
        French validation message ``Veuillez renseigner ce champ.`` stays
        readable, followed by one trailing newline, which is the byte shape the
        reference artifact has.

    Raises:
        ValueError: If the document contains a non-finite float, which no
            builder can produce and which would render as JSON the publisher
            cannot parse.  Raised rather than written, so the failure is
            reportable through the CLI's writer-failure exit class.
    """
    document = json.dumps(build_cucumber_json(result_set), **_JSON_DUMP_KWARGS)
    return f"{document}{_TRAILING_NEWLINE}"


def write_cucumber_json(
    result_set: ResultSet | None,
    base: Path | str | None = None,
    path: Path | str | None = None,
) -> Path:
    """Write the JSON report to :func:`app.utils.paths.cucumber_json_path`.

    The impure half, and deliberately thin: it resolves a destination, creates
    its parent and writes the text :func:`render_cucumber_json` produced.
    Nothing is deleted or truncated beyond this one file -- emptying the
    build-output directory is ``app/cli.py``'s ``--clean`` step, and
    :mod:`app.utils.paths` creates directories but never removes them.

    Args:
        result_set: As :func:`build_cucumber_json`.
        base: Directory to resolve the artifact path against, defaulting to the
            working directory, exactly as every
            :mod:`app.utils.paths` accessor does.  This is the mechanism a test
            uses to write into a temporary directory.
        path: An explicit destination, which overrides ``base`` entirely.  For
            a caller that already holds a path -- a test, or a service writing
            a copy elsewhere -- so that no caller has to reimplement the
            default.

    Returns:
        The path written, so a caller can name it on stdout or hand it on.

    Raises:
        OSError: If the parent directory cannot be created or the file cannot
            be written.  Deliberately **not** swallowed: producing this
            artifact is the writer's contract with the exit table, whose
            writer-failure class requires the failing writer to be named on
            stderr while the artifacts written before it remain.  A *test*
            outcome, by contrast, never reaches this path -- failures,
            undefined steps and skips are data that has already been
            serialised by the time the file is opened.
    """
    destination = ensure_parent(cucumber_json_path(base) if path is None else path)
    text = render_cucumber_json(result_set)
    # newline="\n" so a report written on Windows is byte-identical to one
    # written on Linux: the structure of this artifact must not depend on the
    # platform the pipeline's ``isUnix()`` branch happened to choose.
    with open(destination, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)
    logger.info("Wrote %s", destination)
    return destination
