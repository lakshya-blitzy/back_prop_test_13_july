"""The Cucumber-JVM JSON report writer -- the port's one machine-read artifact.

Serialises the internal result document defined in :mod:`app.reporting.events`
into the Cucumber-JVM JSON report at
:data:`app.utils.paths.CUCUMBER_JSON_RELPATH`, the one artifact the Jenkins
publisher reads (``Jenkins:15``).  A wrong key name, an emitted ``[]`` where
the JVM omits the key, or a float where it writes a nanosecond integer are
*silent* parity failures: nothing crashes, the report is simply wrong.  The
contract below is measured from the committed baseline
``tests/fixtures/golden_cucumber.json`` and from
``io.cucumber:cucumber-core:7.2.3``'s ``JsonFormatter``/``TestSourcesModel``:

* The top level is a JSON *list* of feature objects, and ``[]`` for a run that
  selected no scenario, because all four artifacts exist even then.
* ``uri`` is ``file:``-prefixed and repository-relative
  (``file:features/Crm.feature``); ``description`` is verbatim and ``""``
  rather than absent; a step's ``keyword`` keeps one trailing space.
* A feature tag carries ``name``, ``type`` and ``location`` and its ``tags``
  key is emitted unconditionally; a scenario tag carries ``name`` alone, and
  that key is omitted entirely, never ``[]``, when it has none.  Feature tags
  arrive already propagated onto every scenario element by the collector.
* Elements interleave each Background occurrence with the scenario it precedes.
* Durations are integer nanoseconds and ``skipped`` carries no ``duration``
  key.  ``error_message`` is AAP deviation 16 -- Python assertion text and
  traceback, newlines normalised, subject and message parity, formatting not.
* Scenarios the tag expression did not select are dropped **together with the
  Background occurrences emitted for them**, and a feature left with no
  selected scenario does not appear at all: the JVM never starts them, where
  behave reports them as skipped.
* A scenario ``id`` is ``<feature-slug>;<scenario-slug>``, an Examples row
  appending the Examples block's slug and the row's one-based position.  Two
  features sharing a title share an ``id``, preserved rather than
  disambiguated, which is why ``app/web/routes.py`` keys routes on position.

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
  of the scenario-level rule below.  The two levels are never conflated.  Each
  tag's ``location`` is the **tag's own**, copied from the internal document
  and never derived from the feature's ``line``: the pinned case has the tag on
  line 1 and the feature on line 2, so a derived location is wrong for the only
  tagged feature the baseline contains.  :func:`_build_feature_tags` holds that
  rule.

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
* **``name`` and ``match.arguments[].val`` are emitted redacted, and
  ``error_message`` sanitized.**  Both rules belong to
  :mod:`app.reporting.events` -- :func:`app.reporting.events.redact_step_text`
  and :func:`app.reporting.events.sanitize_failure_text` -- and the collector
  has already applied them; this writer applies them again because a worker's
  JSON file is untrusted input to it and this artifact is the one that leaves
  the workspace (review findings SEC2-F03 and SEC2-F20).  Both are idempotent,
  so a document this build produced is emitted unchanged, and both are
  value-level: no key is added, removed or renamed, and ``location``, ``id``,
  ``uri``, the element names, the keywords, the statuses, the durations and an
  embedding's ``data`` are untouched.  The offset contract
  ``name[offset:offset + len(val)] == val`` holds of the emitted pair, because
  a step's name and its arguments are redacted in one call.  The suite's
  Gherkin ``Examples`` credentials stay verbatim in the feature files, which
  AAP 0.8 requires; what changes is only what a report keeps.
* ``result`` **omits fields rather than emitting zeros**: ``status`` always and
  lowercase, ``error_message`` only when there is an error, and ``duration``
  whenever the value is non-zero -- **field presence is per-invocation, not
  per-status**.  ``createResultMap`` gates that field on the duration alone
  (``if (!result.getDuration().isZero())``) and the baseline shows the clause's
  four shapes: 14 ``{duration, status}`` passed, 2 ``{duration, error_message,
  status}`` failed, 2 bare ``{status}`` skipped that recorded zero, *and* one
  ``{duration, status}`` skipped carrying ``duration: 1000000``.  All four are
  reproduced; :func:`_build_result` records the measurement and why the plan's
  "skipped is ``{"status": "skipped"}``" sentence does not override it.
  Durations are integer nanoseconds (``30202000000`` is 30.202 s); a float is
  never emitted.

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
:mod:`app.reporting.events`, which computes each element's id at collection
time, and is re-exported here as :func:`convert_to_id` and
:func:`scenario_element_id` so that the contract has one owner and one import
site.  **This writer copies the id and never recomputes it**: an Examples
row's two trailing segments are not recorded on the element, so a
reconstruction could only guess at them -- see :func:`_build_scenario`.

**``after`` and embeddings.**  No committed artifact contains an embedding --
``Hooks.java:5`` imported ``org.junit.After``, so Cucumber never invoked the
teardown -- so the shape follows from the attach call at ``Hooks.java:15`` plus
the generator, which hangs an attachment off the *test case* map rather than
off a step.  A failed scenario whose teardown captured a screenshot therefore
gains ``after: [{"match": {"location": ...}, "result": {...}, "embeddings":
[{"mime_type": "image/png", "data": "<base64>", "name": "<scenario name>"}]}]``.
``mime_type``'s underscore is the contract, not a typo.

An entry is published when it carries an attachment **or** when its mapped
result status is anything but ``passed``; only a *silently passing* hook --
one that attached nothing and recorded no problem -- contributes nothing, so
a scenario that passed carries no ``after`` key at all, as every element of
the reference does.  A failing teardown that captured nothing is therefore
published with its ``failed`` result and **no** ``embeddings`` key, rather
than being dropped or carrying an empty list: the internal document already
records a hook entry only when something real happened
(:mod:`app.reporting.events` states that rule), and a hook failure is exactly
what a dead session produces when the screenshot cannot be taken.
:func:`_build_after` owns both halves.

Boundaries and invariants
-------------------------
* The only intra-package imports are :mod:`app.reporting.events` and
  :mod:`app.utils.paths` (the plan's ``RP --> UT`` edge).  No service is
  imported -- the dependency edge is ``SV --> RP`` and never the reverse -- and
  neither Flask, Selenium, ``app.config``, ``app.pages``, ``app.automation``
  nor ``app.web`` appears, so this module is importable inside a worker process
  that never builds a Flask application.
* **No path literal.**  The destination comes from
  :func:`app.utils.paths.cucumber_json_path`, and the bytes reach it through
  :func:`app.utils.paths.open_artifact_write`, which is the module's *write
  authority*: it creates and verifies every owned directory component under a
  held directory descriptor, refuses a symlinked or hard-linked destination,
  creates the file owner-only and truncates it only once the object it holds is
  established.  Nothing is deleted or truncated beyond writing this one file:
  emptying the build-output directory is ``app/cli.py``'s ``--clean`` step.
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
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Final

from app.reporting.events import (
    BACKGROUND_KEYWORD,
    ELEMENT_TYPE_BACKGROUND,
    ELEMENT_TYPE_SCENARIO,
    FEATURE_KEYWORD,
    TAG_TYPE,
    JsonDict,
    ResultSet,
    convert_to_id,
    element_units,
    feature_tag,
    redact_step_text,
    sanitize_failure_text,
    scenario_element_id,
    scenario_tag,
)

# The attachment validator, imported rather than reimplemented: the inline-PNG
# embedding contract has exactly one owner in this project, and a second
# opinion about which payloads are renderable is precisely how the JSON
# artifact came to disagree with the two HTML ones.  See _build_embeddings.
from app.reporting.screenshots import normalize_embeddings
from app.utils.paths import FILE_URI_SCHEME, cucumber_json_path, open_artifact_write

__all__ = [
    "BACKGROUND_ELEMENT_KEYS",
    "CUCUMBER_STATUSES",
    "FEATURE_KEYS",
    "SCENARIO_ELEMENT_KEYS",
    "STATUS_ALIASES",
    "STATUS_FALLBACK",
    "STATUS_PASSED",
    "STATUS_SKIPPED",
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

logger = logging.getLogger(__name__)


#: Status token for a step the JVM considers executed and successful.  Named
#: because the dry-run rule maps to it rather than to whatever behave recorded.
STATUS_PASSED: Final[str] = "passed"

#: Status token whose presence *removes* ``match.location``:
#: ``createMatchMap`` adds the location only ``if
#: (!result.getStatus().is(UNDEFINED))``, so an undefined step's ``match`` is
#: the empty object.
STATUS_UNDEFINED: Final[str] = "undefined"

#: Status token for a step the scenario never reached -- the steps after a
#: failure in the same scenario, of which the baseline carries three.  It is a
#: *genuine* outcome and is passed through untouched, in particular never
#: folded to ``untested``, which would misreport every failed scenario's tail:
#: ``skipped`` means the scenario stopped, ``untested`` means it never started.
#: It gates **nothing** in :func:`_build_result` -- ``duration`` is omitted on
#: a zero and emitted on a measured value whatever the status -- and it is
#: named here because the omission rules are stated per field and a reader
#: checking which statuses influence them should find the answer beside each
#: token rather than infer it from a literal.
STATUS_SKIPPED: Final[str] = "skipped"

#: The status vocabulary a Cucumber report may carry.  Anything outside this
#: set is folded by :data:`STATUS_ALIASES` or, failing that, by
#: :data:`STATUS_FALLBACK`, because the publisher parses these names and an
#: invented one would be silently mis-read.
CUCUMBER_STATUSES: Final[frozenset[str]] = frozenset(
    {
        STATUS_PASSED,
        "failed",
        STATUS_SKIPPED,
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

_TRAILING_NEWLINE: Final[str] = "\n"


# Every value this module emits passes through one of these helpers, so a
# document built from an internal result set the event collector's schema
# admits holds only ``str``, ``int``, ``list`` and ``dict`` and serialises
# without a fault.  Over that validated input -- and not over an arbitrary
# object, whose ``str()`` or mapping access can itself raise -- a malformed
# field degrades to an empty string or a zero, diagnosable in the published
# report, rather than failing a writer the exit table treats as fatal.


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

    The single owner of the status rules, whose names every consumer of the
    artifact reads.  Under ``dry_run`` the JVM emits a matched step ``passed``
    and an unmatched one ``undefined`` where behave reports ``untested`` for
    both, so the answer follows ``matched`` alone and is never ``untested``.
    ``error`` folds to ``failed``, because behave distinguishes an exception
    from a failed assertion and Cucumber does not (:data:`STATUS_ALIASES` is
    the whole fold), while ``skipped`` is genuine for a step after a failure in
    the same scenario and passes through untouched.

    Presentation normalisation is not done here:
    ``app/templates/partials/status_badge.html``'s ``status_token`` owns the
    token a template renders.

    Args:
        status: behave's normalised name, a behave status enum, or ``None``.
        matched: Whether a step definition was resolved.  Read only under
            ``dry_run``, where it is the whole of the decision.
        dry_run: Whether the run that produced the status was a dry run.

    Returns:
        A member of :data:`CUCUMBER_STATUSES`; :data:`STATUS_FALLBACK` for an
        absent or unrecognised name.
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


def _build_result(result: JsonDict, *, matched: bool, dry_run: bool) -> JsonDict:
    """Build a ``result`` map, omitting fields rather than emitting zeros.

    Three clauses in emission order: ``status`` always, mapped by
    :func:`map_step_status`; ``duration`` when non-zero **and** the mapped
    status is not :data:`STATUS_SKIPPED`; ``error_message`` when present,
    LF-normalised by :func:`normalize_error_message` (AAP deviation 16).

    * ``status`` **unconditionally**, lower-cased and folded onto the Cucumber
      vocabulary by :func:`map_step_status`, so no consumer has to test for it;
    * ``duration`` **whenever the normalised value is non-zero**, whatever the
      status;
    * ``error_message`` when the result carries one, LF-normalised by
      :func:`normalize_error_message` and then put through
      :func:`app.reporting.events.sanitize_failure_text`, which masks
      classified values, relativises frame paths and bounds the length.  That
      second call is defence in depth and not a second rule: the collector
      already sanitized the text and the rule is idempotent, but this artifact
      is published to Jenkins and archived, and a worker's JSON file is
      untrusted input to this writer (review finding SEC2-F20).  It cannot
      empty a non-empty message, so the key-presence rule above is unchanged.

    **Field presence is per-invocation, not per-status.**  The generator this
    writer ports -- ``createResultMap`` in
    ``io.cucumber:cucumber-core:7.2.3`` -- gates the field on the duration
    alone::

        if (!result.getDuration().isZero())

    and never consults the status.  Measured over the 19 steps of the
    committed ``tests/fixtures/golden_cucumber.json``, that clause produces
    exactly four result shapes:

    * 14 x ``{duration, status}``, status ``passed``
    * 2 x ``{duration, error_message, status}``, status ``failed``
    * 2 x bare ``{status}``, status ``skipped`` -- both recorded zero
    * 1 x ``{"duration": 1000000, "status": "skipped"}`` -- the step
      ``"User can verify the information"``, skipped *and* measured

    All four are reproduced here, and the fourth is why the gate is the value
    and not the status.  **This is the one place in this writer that departs
    from the literal wording of specification section 0.6, and the departure
    is between two statements of that same section**, which is why it is
    resolved here rather than deferred:

    * the section's per-status sentence reads "skipped is
      ``{"status": "skipped"}`` with **no** ``duration`` key", which
      generalises from the two zero-duration skips of the baseline it is
      describing;
    * the clause governing field presence, in that same bullet, reads that a
      result "omits fields **rather than emitting zeros**" -- a value test,
      not a status test;
    * and the baseline the section describes is byte-pinned as the golden
      fixture by section 0.4.1, which makes the fixture the authority on any
      disagreement, and it carries a measured ``skipped`` cell whose duration
      is not zero.

    Only the value test satisfies all three, and it is what the JVM generator
    quoted above actually does.  Gating on the status would delete that cell,
    putting an unsanctioned delta between this writer and the one artifact a
    machine reads -- and it would report two identical observations
    differently, since a measured skip and an unmeasured one would publish the
    same shape.  So the feature-directory prefix of ``uri`` (section 0.4.1,
    deviation 1) is the **only** expected difference between this writer's
    output and the fixture; ``error_message``'s CRLF-to-LF normalisation
    (deviation 16) is a difference in *content* that the comparison's own
    canonicalisation already covers.

    The literal per-status sentence is not discarded by this: it describes the
    shape of **every skipped step a live run produces**, because behave
    records zero for a step skipped after a failure and the value test omits a
    zero.  ``tests/test_cucumber_json.py`` pins both halves -- that shape for
    the zero case, and the measured baseline cell for the non-zero one.

    **No live behaviour turns on it.**  behave reports duration ``0`` for a
    step skipped after a failure in the same scenario, and a zero duration is
    omitted by the first half of the same clause, so a real run's skipped step
    emits ``{"status": "skipped"}`` either way.  The shape carrying a duration
    is reachable from a document that recorded a non-zero one -- a
    JVM-authored artifact replayed through this writer, or an engine that
    measures the skip -- and it is exactly the observation that must not be
    discarded: the publisher cannot distinguish "not measured" from "measured
    and dropped".

    A **fourth** difference lives in the same field and comes from the
    sanitization of ``error_message`` above: the baseline's Selenium failure
    quotes a Windows profile directory inside a capability dump, and this
    writer shortens any absolute path to its last two components.  Measured
    rather than left to be discovered - the two baseline messages change in
    exactly that one place, and otherwise only in their line endings.
    ``tests/test_cucumber_json.py`` normalizes ``error_message`` on both sides
    of the golden comparison, because its content is not parity (deviation
    16), so the difference is invisible there and is named here instead.

    Hook results reach this same builder from :func:`_build_after` with
    ``matched=True`` and ``dry_run=False``: a hook's recorded status (folded by
    :data:`STATUS_ALIASES`, which already covers ``hook_error`` and
    ``cleanup_error``) and its error text pass through this one generic path
    rather than through a second rule -- and :func:`_build_after`'s own
    publication rule reads the status this function returns.

    Args:
        result: The internal result mapping.
        matched: Whether the step resolved to a definition.
        dry_run: Whether the run was a dry run.

    Returns:
        The result map.  ``status`` is always present, so a consumer never has
        to test for it.

    Examples:
        A non-zero duration survives, on any status:

        >>> _build_result({"status": "passed", "duration": 30202000000},
        ...               matched=True, dry_run=False)
        {'status': 'passed', 'duration': 30202000000}
        >>> _build_result({"status": "skipped", "duration": 1000000},
        ...               matched=True, dry_run=False)
        {'status': 'skipped', 'duration': 1000000}

        A zero is omitted, on any status -- which is the shape every skipped
        step of a real run has:

        >>> _build_result({"status": "skipped", "duration": 0},
        ...               matched=True, dry_run=False)
        {'status': 'skipped'}
        >>> _build_result({"status": "passed", "duration": 0},
        ...               matched=True, dry_run=False)
        {'status': 'passed'}

        A failure carries all three fields, and ``error_message`` arrives
        LF-normalised:

        >>> failed = _build_result(
        ...     {"status": "error", "duration": 4000000,
        ...      "error_message": "expected 8\\r\\nwas 89"},
        ...     matched=True, dry_run=False)
        >>> list(failed)
        ['status', 'duration', 'error_message']
        >>> failed["status"], failed["duration"]
        ('failed', 4000000)
        >>> failed["error_message"]
        'expected 8\\nwas 89'
    """
    status = map_step_status(result.get("status"), matched=matched, dry_run=dry_run)
    built: JsonDict = {"status": status}

    duration = _as_int(result.get("duration"))
    if duration:
        built["duration"] = duration

    message = sanitize_failure_text(
        normalize_error_message(result.get("error_message"))
    )
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
    against a differently-substituted name would be silently wrong.  This
    function copies the pair through; masking a credential-bearing one and
    moving the offsets that follow it is :func:`_build_step`'s call to
    :func:`app.reporting.events.redact_step_text`, which needs the step's name
    as well and is therefore the only place that can do it.

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


def _build_match(
    match: JsonDict,
    *,
    status: str,
    arguments: Sequence[JsonDict],
) -> JsonDict:
    """Build a step's ``match`` map.

    Args:
        match: The internal match mapping, read for ``location`` only.
        status: The step's *mapped* status, because it decides whether
            ``location`` is emitted at all.
        arguments: The step's argument entries, already built by
            :func:`_build_arguments` **and already redacted jointly with the
            step's name** by :func:`_build_step`.  They are passed in rather
            than rebuilt here because ``offset`` indexes into the emitted
            ``name``: recomputing the entries against the internal name would
            reintroduce exactly the disagreement the joint redaction exists to
            prevent.

    Returns:
        The match map: ``arguments`` when the step took parameters, and
        ``location`` unless the mapped status is ``undefined``.  An undefined
        step therefore yields ``{}`` -- ``createMatchMap`` adds the location
        only ``if (!result.getStatus().is(UNDEFINED))``.  ``arguments`` is
        emitted first because that is the order the reference artifact carries;
        object key order is not semantic, but matching it costs nothing.
        ``location`` is copied through untouched: a dotted Python path is not a
        classified value, and it is what a reader uses to find the step.
    """
    built: JsonDict = {}

    if arguments:
        built["arguments"] = list(arguments)

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

        ``name`` and ``match.arguments`` are emitted through
        :func:`app.reporting.events.redact_step_text`, which masks a
        credential-bearing span in the step text and in the matching argument
        entry *together* and keeps ``name[offset:offset + len(val)] == val``
        true of the emitted pair.  The collector already applied the same rule
        and the rule is idempotent, so a document this build produced comes
        through unchanged; what the second application buys is the case this
        writer cannot rule out -- a worker JSON file, or a hand-built
        document, whose step text was never classified (review finding
        SEC2-F03).  Redacting the two fields in one call is also why the
        arguments are built here and handed to :func:`_build_match`.
    """
    match = _as_mapping(step.get("match"))
    result = _as_mapping(step.get("result"))

    matched = step.get("matched")
    if not isinstance(matched, bool):
        # A hand-built document may omit the flag; a step that resolved to a
        # location is by definition one that matched.
        matched = bool(_as_text(match.get("location")))

    built_result = _build_result(result, matched=matched, dry_run=dry_run)
    name, arguments = redact_step_text(
        _as_text(step.get("name")), _build_arguments(match.get("arguments"))
    )
    return {
        "keyword": _as_text(step.get("keyword")),
        "line": _as_int(step.get("line")),
        "name": name,
        "match": _build_match(
            match, status=built_result["status"], arguments=arguments
        ),
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


def _build_embeddings(embeddings: Any) -> list[JsonDict]:
    """Build a hook entry's ``embeddings`` list, through the one validator.

    The shape follows ``Hooks.java:15``'s ``scenario.attach(screenshot,
    "image/png", scenario.getName())``: bytes, a MIME type and a name.
    ``mime_type``'s underscore spelling is the contract: the generator's own
    comment records that it should have been the media type and that renaming
    it was not worth the migration.

    **Every attachment is validated by**
    :func:`app.reporting.screenshots.normalize_embeddings` **before it is
    serialised, and this writer trusts nothing the result declared.**  That
    matters because the values arriving here are result-controlled -- they come
    from whatever the per-worker event files contained -- and this document is
    what the continuous-integration publisher ingests and what the viewer
    renders as a ``data:`` URI.  Copying the declared media type through would
    let a result choose the media type of an inline document, and copying the
    payload through would let base64 of something that is not an image travel
    under the name of one.  The two HTML writers already validate by the same
    function, via ``app/reporting/aggregation.py``; validating here is what
    stops the JSON artifact from being the one place an unvalidated attachment
    survives, and what keeps all four artifacts describing the same run.

    The validator's guarantees, in the terms this function's output shows:
    ``mime_type`` is the literal ``image/png`` written by
    ``app/reporting/screenshots.py`` rather than any value from the input;
    ``data`` is the canonical base64 of bytes proven to be a structurally
    valid PNG -- signature, ``IHDR``, geometry, CRC-checked chunks, terminal
    ``IEND`` and image data that inflates to its declared size; and ``name``
    survives verbatim when the attachment carried a textual one.

    Args:
        embeddings: The internal hook entry's ``embeddings`` value.

    Returns:
        One mapping per attachment that passed validation, in input order,
        carrying ``mime_type``, ``data`` and -- only when the attachment
        actually has one, mirroring the generator's ``if (name != null)`` --
        ``name``.  An attachment with no data, with a media type other than
        ``image/png``, or whose payload is not a valid inline PNG is
        **dropped and logged by the validator**: a screenshot that failed to
        capture is suppressed under plan deviation 19 and must leave no trace,
        and an attachment that is not a renderable image would appear in the
        report as a broken one, which is worse than no evidence at all.
        Dropping an attachment never alters a status -- a screenshot is
        evidence about a result, never part of one.
    """
    built: list[JsonDict] = []
    for embedding in normalize_embeddings(list(_mappings(embeddings))):
        entry: JsonDict = {
            "mime_type": embedding["mime_type"],
            "data": embedding["data"],
        }
        if "name" in embedding:
            entry["name"] = embedding["name"]
        built.append(entry)
    return built


def _build_after(after: Any) -> list[JsonDict]:
    """Build a scenario's ``after`` array.

    The array lives on the scenario element rather than on a step, because
    ``addHookStepToTestCaseMap`` puts ``AFTER`` hooks on the test-case map.

    **What makes an entry publishable.**  An entry exists in the internal
    document only when something real happened -- an attachment arrived,
    behave's scenario model showed a hook or context-cleanup failure, or a
    caller reported an outcome through
    :func:`app.reporting.events.record_hook_result` -- so the question here is
    not whether to invent one but whether to carry one forward.  Two
    conditions, either of which is sufficient:

    * the entry carries at least one usable attachment, or
    * its *mapped* result status is anything other than
      :data:`STATUS_PASSED`.

    Only a **silently passing** hook -- one that attached nothing and recorded
    no problem -- is omitted, which is what keeps the emitted document
    identical in shape to the reference, where the teardown hook never ran and
    no element carries ``after`` at all.  Gating on the attachment alone, as
    this builder previously did, dropped exactly the case that matters most: a
    teardown that failed without managing a screenshot -- a dead session,
    which is precisely *why* a capture fails -- was recorded by the collector
    and rendered by both HTML writers while the JSON the Jenkins publisher
    reads said the scenario's teardown passed.  A hook failure is a scenario
    whose driver may not have been quit and whose evidence may not have been
    captured, so the machine-read artifact is the last place it may go
    missing.

    Args:
        after: The internal scenario's ``after`` value.

    Returns:
        One entry per publishable hook, each carrying ``match`` and ``result``,
        plus ``embeddings`` **only when there is at least one attachment** --
        this writer omits rather than emits an empty list, exactly as it does
        for ``tags`` at scenario level and for ``after`` itself, and an entry
        with an empty ``embeddings`` array would render as a broken image in
        both HTML reports.  An empty list here makes
        :func:`_build_scenario` omit the ``after`` key entirely.
    """
    built: list[JsonDict] = []
    for entry in _mappings(after):
        embeddings = _build_embeddings(entry.get("embeddings"))
        # A hook's outcome is its own: it is neither dry-run mapped (behave
        # runs no hooks in a dry run) nor dependent on a step match, so the
        # recorded status passes straight through the same generic result rule
        # -- which is also what folds behave's ``hook_error`` and
        # ``cleanup_error`` onto ``failed`` through :data:`STATUS_ALIASES`.
        result = _build_result(
            _as_mapping(entry.get("result")), matched=True, dry_run=False
        )
        if not embeddings and result["status"] == STATUS_PASSED:
            logger.debug(
                "Omitting a passing after-hook entry that attached nothing"
            )
            continue
        match: JsonDict = {}
        location = _as_text(_as_mapping(entry.get("match")).get("location"))
        if location:
            match["location"] = location
        built_entry: JsonDict = {"match": match, "result": result}
        if embeddings:
            built_entry["embeddings"] = embeddings
        built.append(built_entry)
    return built


# The two tag levels have deliberately different shapes and deliberately
# different emptiness rules; conflating them is the single most likely way to
# break this artifact.


def _build_feature_tags(tags: Any) -> list[JsonDict]:
    """Build a feature's ``tags`` list in the JVM's long shape.

    **A tag's ``location`` is the tag's own, and is never derived from the
    feature.**  That is not a preference, it is the pinned case: ``@Smoke``
    sits at ``Crm.feature:1`` and the ``Feature:`` keyword at line 2, so a
    location synthesised from the feature's line is off by one on the single
    tagged feature the baseline contains -- and would be off by however many
    tag lines a multi-tag feature declares.  This builder previously fell back
    on the feature's line for a tag that arrived without a location, which is
    why :func:`app.reporting.events._validate_tag` now requires the long shape
    at feature level: every tag reaching this writer from a collected or a
    loaded document carries its own declaration site, so there is nothing left
    to synthesise.

    The residual paths are an in-process document assembled by hand -- a bare
    string, or a mapping with no ``location`` -- and they gain **no** location
    key at all.  An absent site reads to a consumer as "not recorded"; an
    invented one reads as a source position that does not exist, and
    ``app/reporting/pretty_reports.py`` builds a tag page per distinct name
    from exactly these mappings.

    Args:
        tags: The internal feature's ``tags`` value, normally already
            long-shape mappings from :func:`app.reporting.events.feature_tag`.

    Returns:
        A list of ``{"name": ..., "type": "Tag", "location": {"line": ...,
        "column": ...}}`` mappings, with the leading ``@`` retained and
        ``location`` present only for a tag that carried one.  The list may be
        empty, and the *caller emits the key regardless* -- feature-level
        ``tags`` is unconditional.
    """
    built: list[JsonDict] = []
    for tag in tags or ():
        if isinstance(tag, str):
            name, source = tag, {}
        elif isinstance(tag, dict):
            name, source = _as_text(tag.get("name")), tag
        else:
            continue
        if not name:
            continue
        # :func:`app.reporting.events.scenario_tag` owns the restoration of the
        # leading ``@`` behave strips, so the long shape borrows it for the
        # ``name`` member rather than repeating the rule at a second level.
        entry: JsonDict = dict(scenario_tag(name))
        entry["type"] = _as_text(source.get("type")) or TAG_TYPE
        location = _as_mapping(source.get("location"))
        if location:
            # Floored at one exactly as
            # :func:`app.reporting.events.feature_tag` floors it: Gherkin
            # numbers lines and columns from one, so a partial location keeps
            # the member it recorded and the other becomes the minimum legal
            # position -- never the feature's line, which is the value this
            # builder must not reach for.
            entry["location"] = {
                "line": max(_as_int(location.get("line")), 1),
                "column": max(_as_int(location.get("column")), 1),
            }
        built.append(entry)
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


def _build_scenario(element: JsonDict, *, dry_run: bool) -> JsonDict:
    """Build a scenario element.

    **The ``id`` is copied, never reconstructed.**  It is the identity every
    other artifact keys on -- ``app/reporting/pretty_reports.py`` derives a
    detail page's filename from it and the publisher groups by it -- and
    :func:`app.reporting.events.scenario_element_id` is its single owner,
    which computes it with the JVM's own upward recursion at collection time.
    This builder used to rebuild a missing one from the feature's and the
    scenario's names, and that fallback was wrong in a way no consumer could
    detect: an Examples row's id carries two further segments, the Examples
    block's slug and the row's position counting the header as 1
    (``...;expected-name;2``), and **neither is recorded on the element**.  So
    for the one shape where an id can go missing, the fallback published a
    plausibly-shaped identifier for a test case the suite does not contain,
    and the pages keyed on it pointed at nothing.  A copied empty string is
    diagnosable; an invented id is not.

    The shape is guaranteed rather than hoped for: ``new_element`` refuses to
    build a scenario without an ``id`` and ``load_result_set`` rejects an
    empty one, so every document that came through
    :mod:`app.reporting.events` carries one.  The residual path is an
    in-process document assembled by hand, and it is announced at ``WARNING``
    rather than silently patched, because a scenario with no identity is a
    defect in whatever built the document.

    Args:
        element: The internal scenario element.
        dry_run: Whether the run was a dry run.

    Returns:
        A mapping whose keys are :data:`SCENARIO_ELEMENT_KEYS`, less ``tags``
        when the scenario has none and less ``after`` when no teardown hook is
        publishable.  ``id`` and ``start_timestamp`` are always present -- the
        first from the collector, which computed it with the JVM's own
        recursion, the second passed through byte-for-byte because
        :func:`app.reporting.events.format_timestamp` already emits the
        contract's millisecond-precision UTC form with a literal ``Z``.
    """
    identifier = _as_text(element.get("id"))
    if not identifier:
        # ``%r`` rather than ``%s`` for the name: it is text from a feature
        # file, so a newline in it would otherwise split one record across two
        # log lines, and this record goes to stderr where the exit contract's
        # reader is line-oriented.  The line number locates the element even
        # when the name is unhelpful.
        logger.warning(
            "Scenario element %r at line %s carries no id; emitting it empty "
            "rather than inventing one, because an Examples row's id cannot be "
            "reconstructed from the element",
            _as_text(element.get("name")),
            _as_int(element.get("line")),
        )

    timestamp = element.get("start_timestamp")
    built: JsonDict = {
        "keyword": _as_text(element.get("keyword")),
        "line": _as_int(element.get("line")),
        "name": _as_text(element.get("name")),
        "description": _as_text(element.get("description")),
        "type": ELEMENT_TYPE_SCENARIO,
        "id": identifier,
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
    # The unit grouping comes from :func:`app.reporting.events.element_units`
    # rather than from a copy kept here.  The *rule* applied to a unit is this
    # writer's -- a Background occurrence is emitted for a scenario and shares
    # its fate, so a unit whose scenario the filter excluded is dropped whole
    # or the artifact would carry a background for a test case that never
    # appears -- but the grouping it is applied to is one fact about the
    # document, and four consumers each holding their own implementation of it
    # is four chances to disagree about a feature whose elements are not units.
    units = [
        unit
        for unit in element_units(_mappings(feature.get("elements")))
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
                elements.append(_build_scenario(element, dry_run=dry_run))

    return {
        "uri": _feature_uri(feature),
        "id": _as_text(feature.get("id")) or convert_to_id(name),
        "keyword": _as_text(feature.get("keyword")) or FEATURE_KEYWORD,
        "line": line,
        "name": name,
        "description": _as_text(feature.get("description")),
        # Unconditional, and empty for the five features that declare no tag.
        # ``line`` is deliberately not passed: a tag carries its own
        # declaration site and the feature's line is never a substitute for it.
        "tags": _build_feature_tags(feature.get("tags")),
        "elements": elements,
    }


def build_cucumber_json(result_set: ResultSet | None) -> list[JsonDict]:
    """Build the Cucumber-JVM JSON document from a merged result set.

    The pure half of this writer: no clock, no working directory, no
    filesystem, and ``result_set`` is never mutated -- every emitted mapping
    is newly constructed -- which is what lets the golden-fixture comparison
    run in memory and the ``app/reporting`` coverage gate be met without a
    browser.

    Args:
        result_set: The merged internal document from
            :func:`app.reporting.events.merge_result_sets`, or a hand-built
            one in the same schema.  ``None`` and any non-mapping yield an
            empty document, so a run that produced nothing still leaves the
            publisher a readable file.  Within a document the event
            collector's schema admits, a malformed field degrades through the
            value helpers rather than aborting the build; a value outside that
            schema is not covered by that guarantee.

    Returns:
        A list of feature objects in the Cucumber-JVM schema -- a list, not an
        object -- features in the order the merge established and nothing
        sorted.  Non-selected scenarios and the Background occurrences emitted
        for them are dropped and a feature with no surviving scenario is
        omitted, so a run whose tag expression selected nothing yields ``[]``.
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

    The impure half, and deliberately thin: it resolves a destination and
    hands it to :func:`app.utils.paths.open_artifact_write`, which is the one
    route by which this artifact reaches disk.  That opener creates and
    verifies every owned directory component under a *held* directory
    descriptor with ``O_NOFOLLOW``, opens the final entry relative to that
    descriptor, and truncates it only after the descriptor has been confirmed
    to hold a lone regular file -- so the object written is the object that was
    verified.  Resolving the parent and then reopening the *pathname* is what
    this writer no longer does: between the check and the open, a symbolic or
    hard link put in the artifact's place would have redirected the write, or
    truncated a file outside the build-output directory, before anything could
    refuse it (CWE-367/CWE-59).  The file is created owner-only
    (:data:`app.utils.paths.ARTIFACT_FILE_MODE`) and one an earlier
    ``--no-clean`` run left group- or world-readable is tightened through that
    same descriptor before the new content exists, because the report carries
    substituted step arguments and failure text (CWE-732/CWE-359).

    Nothing is deleted or truncated beyond this one file.  Emptying the
    build-output directory is ``app/cli.py``'s ``--clean`` step and the
    per-worker intermediate directory beneath it is
    ``app/services/test_run_service.py``'s; the only entries
    :mod:`app.utils.paths` ever removes are the dot-prefixed temporary and
    staging entries its own publication API created, and an in-place stream
    such as this one creates none of those.

    Args:
        result_set: As :func:`build_cucumber_json`.
        base: Directory to resolve the artifact path against, defaulting to
            the working directory, as every :mod:`app.utils.paths` accessor
            does.  This is how a test writes into a temporary directory.
        path: An explicit destination, which overrides ``base`` entirely, for
            a caller that already holds one.

    Returns:
        The path written, so a caller can name it on stdout or hand it on.

    Raises:
        OSError: If the parent directory cannot be created or the file cannot
            be written.  Deliberately not swallowed: the exit table's
            writer-failure class requires the failing writer to be named on
            stderr while the artifacts written before it remain.  A *test*
            outcome, by contrast, never reaches this path -- failures,
            undefined steps and skips are data that has already been
            serialised by the time the file is opened.
            :exc:`app.utils.paths.ArtifactPathError` -- a refused link, a
            destination that is not a lone regular file, an artifact that
            cannot be restricted to its owner -- is an :exc:`OSError` subclass,
            so it arrives through this same contract and needs no separate
            handling from any caller.  In every refusal case nothing has been
            written and the previous artifact is intact.
    """
    destination = Path(cucumber_json_path(base) if path is None else path)
    text = render_cucumber_json(result_set)
    # newline="\n" so a report written on Windows is byte-identical to one
    # written on Linux: the structure of this artifact must not depend on the
    # platform the pipeline's ``isUnix()`` branch happened to choose.  Both
    # arguments are passed explicitly rather than left to the opener's
    # defaults, so this writer's byte contract stays readable here.
    with open_artifact_write(destination, encoding="utf-8", newline="\n") as stream:
        stream.write(text)
    logger.info("Wrote %s", destination)
    return destination
