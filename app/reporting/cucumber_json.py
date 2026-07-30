"""Adapter for the Cucumber JSON report artifact.

This module is the Python port of exactly one source construct: the plugin
declaration ``"json:target/cucumber.json"`` from the ``@CucumberOptions`` block
of the documented ``CukesRunner`` in ``README.md``. It is cited here by its
literal value rather than by line number, because the line numbers recorded for
the four plugin strings in the migration plan are off by one -- the literal is
the only safe citation. One Cucumber plugin becomes one reporting adapter, so
this module ports that single declaration and nothing else.

This adapter is a READER, never a writer
----------------------------------------
The report file itself is produced by ``pytest-bdd``'s native Cucumber JSON
writer, requested by the ``--cucumberjson`` option that ``pytest.ini`` carries.
That native writer is what makes report parity achievable without porting a
report serializer at all, and it is the reason nothing below serializes a
Cucumber document, invokes ``pytest``, or shells out to anything. This module
only ever:

1. **loads** the artifact from disk, with an explicit UTF-8 encoding;
2. **validates** it against the frozen schema described below;
3. **normalizes** it into a stable, deterministic, fully typed view for the
   downstream consumers -- the rerun-manifest adapter, the PrettyReports
   adapter, the report service and the run-status endpoint, all of which read
   this same artifact.

Where the path comes from
-------------------------
The artifact location is never spelled out in code here. It is imported from
``app.utils.paths``, which is the single place in the application that knows the
``target`` artifact-root name; ``report_path`` additionally allows the whole
layout to be re-rooted so a test can point the loader at a temporary directory.
This module also never creates, cleans or deletes any directory or file:
creating the artifact tree belongs to ``app/utils/paths.py``, the ``Makefile``
``test``/``dirs`` targets and the BDD ``conftest.py``, and wiping it belongs to
the ``Makefile`` ``clean`` target that ports ``mvn clean``.

The frozen schema
-----------------
The document is a JSON **array** of feature objects. Three key sets are frozen,
and each is published below as an immutable constant so a test can assert
against it directly:

* feature (9 keys) -- ``description``, ``elements``, ``id``, ``keyword``,
  ``language``, ``line``, ``name``, ``tags``, ``uri``
* scenario (8 keys), the members of a feature's ``elements`` array --
  ``description``, ``id``, ``keyword``, ``line``, ``name``, ``steps``, ``tags``,
  ``type``
* step (5 keys), the members of a scenario's ``steps`` array -- ``keyword``,
  ``line``, ``match``, ``name``, ``result``

A step's ``result`` sub-object carries ``status`` and ``duration`` (nanoseconds).
It additionally carries ``error_message`` whenever the step failed, so that key
is registered as a recognised *optional* rather than reported as unexpected;
treating it as unexpected would flag every failing run as schema-invalid and
would starve the rerun manifest of the very data it exists to publish.

The ``@``-stripping rule
------------------------
Tags are emitted **without** the leading ``@``, matching the source toolchain's
own output: ``pytest-bdd`` strips the prefix while parsing. The validator
asserts that no tag value in the document begins with ``@``, and nothing here
ever puts one back. Tag entries are accepted both as objects carrying a ``name``
and as bare strings, because the two shapes occur across report producers.

Preserved defects -- do not "fix" these
---------------------------------------
The port reproduces the source system's behaviour including its defects; the
authoritative register is ``docs/migration-parity.md``. Four of them are visible
in this artifact, and this module is deliberately built so that none can be
repaired by accident:

* **D1** -- the first scenario outline has no ``Examples`` table, so its steps
  are recorded with the literal placeholder text ``<username>`` / ``<password>``
  and they pass. Step names are never interpolated, rendered or rewritten here.
* **D4** -- the third outline feeds the *password* column into the *username*
  step, so the report records ``salesmanager`` / ``posmanager`` where an e-mail
  address would be expected. No column is swapped back and no step name is
  pattern-checked for an e-mail address.
* **D5** -- one assertion expects a French message. Step names and step
  arguments are never trimmed, case-folded, translated or Unicode-normalized, so
  that string survives byte-exactly, trailing period included.
* **D9** -- the second and third outlines parse to the *identical* scenario name,
  so several elements legitimately share one name. Elements are never
  deduplicated or collapsed by name; doing so would destroy the parametrisation
  data and corrupt the rerun manifest.

Defect **D2** -- the runner's default tag expression selects no scenario at all
-- means the default run legitimately produces a report with zero scenarios.
That is a success, not a failure: callers must key off :attr:`RunSummary.has_failures`
rather than off ``status != "passed"``. Mapping the runner's exit codes is the
test-runner service's responsibility, not this module's.

Parallel runs
-------------
The default invocation is parallel, the port of Surefire's method-level
parallelism with unlimited threads. A parallel run produces a complete document
-- larger than, but structurally identical to, the serial one -- with no
guaranteed ordering. Everything this module emits is therefore sorted by a
stable key, and features that share a ``uri`` are merged by *concatenating*
their ``elements`` so a scenario can never be silently dropped. Step order is
the one thing never sorted: it is semantic.

Layering
--------
The dependency direction is ``api -> services -> reporting -> utils``, so this
module imports the Python standard library and ``app.utils`` only. It is
framework-agnostic on purpose: no Flask object, no request or response, no HTTP
status code, no process execution and no logging configuration. It reports
domain results and lets the API layer map them onto HTTP.

Usage
-----
::

    >>> report = load_report()
    >>> report.status in REPORT_STATUSES
    True
    >>> if report.is_valid:
    ...     summary = report.normalized.summary
"""

import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Final

from app.utils.paths import CUCUMBER_JSON_PATH, StrPath, resolve_layout, to_posix

__all__ = [
    "DOCUMENTED_TAGS",
    "DOCUMENTED_TAG_ORDER",
    "ELEMENT_TYPE_BACKGROUND",
    "ELEMENT_TYPE_SCENARIO",
    "FAILING_STATUSES",
    "FEATURE_KEYS",
    "FEATURE_KEY_ORDER",
    "ISSUE_KINDS",
    "ISSUE_MISSING_KEYS",
    "ISSUE_TAG_PREFIXED",
    "ISSUE_UNEXPECTED_KEYS",
    "ISSUE_WRONG_TYPE",
    "KNOWN_STATUSES",
    "LEVEL_DOCUMENT",
    "LEVEL_FEATURE",
    "LEVEL_RESULT",
    "LEVEL_SCENARIO",
    "LEVEL_STEP",
    "LEVEL_TAG",
    "OPTIONAL_RESULT_KEYS",
    "REPORT_ABSENT",
    "REPORT_INVALID",
    "REPORT_STATUSES",
    "REPORT_VALID",
    "RESULT_KEYS",
    "RESULT_KEY_ORDER",
    "SCENARIO_KEYS",
    "SCENARIO_KEY_ORDER",
    "SCHEMA_KEY_SETS",
    "SCHEMA_LEVELS",
    "STATUS_AMBIGUOUS",
    "STATUS_FAILED",
    "STATUS_PASSED",
    "STATUS_PENDING",
    "STATUS_PRECEDENCE",
    "STATUS_SKIPPED",
    "STATUS_UNDEFINED",
    "STEP_KEYS",
    "STEP_KEY_ORDER",
    "TAG_NAME_KEY",
    "TAG_PREFIX",
    "CucumberJsonReport",
    "JsonArray",
    "JsonObject",
    "NormalizedElement",
    "NormalizedFeature",
    "NormalizedReport",
    "NormalizedStep",
    "RunSummary",
    "SchemaIssue",
    "ValidationResult",
    "load_report",
    "normalize_document",
    "report_path",
    "summarize_document",
    "tag_names",
    "validate_document",
]

# A module logger, and nothing more. Handlers, levels and formatters belong to
# `app/logging_config.py`; this module never configures the logging system and
# never writes to standard output, so every message it emits is a structured,
# lazily-formatted record on this logger.
_LOGGER: Final[logging.Logger] = logging.getLogger(__name__)


type JsonObject = Mapping[str, object]
"""A decoded JSON object, read-only and with deliberately untrusted values.

Values are typed ``object`` rather than ``Any`` on purpose: the document is
external input, so every value has to be narrowed with an explicit
``isinstance`` check before it is interpreted. That is what keeps a malformed
report from turning into an ``AttributeError`` three call frames away.
"""

type JsonArray = Sequence[object]
"""A decoded JSON array, read-only and with deliberately untrusted items."""


# =============================================================================
# The frozen report schema.
#
# These are the three key sets the ported report must carry, published as
# immutable constants so that the schema test can assert against them directly
# instead of restating them. Each level is declared twice: once as an ordered
# tuple, which documents the alphabetical reading order used throughout the
# migration plan, and once as the frozen set the validator actually compares
# with. The set is derived from the tuple, so the two can never drift apart.
#
# Configuration values are data, not decisions: nothing below is renamed,
# reordered or "modernised" relative to the schema the source toolchain emitted.
# =============================================================================

FEATURE_KEY_ORDER: Final[tuple[str, ...]] = (
    "description",
    "elements",
    "id",
    "keyword",
    "language",
    "line",
    "name",
    "tags",
    "uri",
)
"""The nine feature-object keys, in alphabetical order."""

FEATURE_KEYS: Final[frozenset[str]] = frozenset(FEATURE_KEY_ORDER)
"""The nine feature-object keys. Every feature object must carry exactly these."""

SCENARIO_KEY_ORDER: Final[tuple[str, ...]] = (
    "description",
    "id",
    "keyword",
    "line",
    "name",
    "steps",
    "tags",
    "type",
)
"""The eight scenario-object keys, in alphabetical order."""

SCENARIO_KEYS: Final[frozenset[str]] = frozenset(SCENARIO_KEY_ORDER)
"""The eight keys of every member of a feature's ``elements`` array.

The same key set applies to a background element as to a scenario element: both
shapes carry ``type``, which is what tells them apart. Validation is therefore
safe for either, while anything that *interprets* an element as a scenario must
branch on ``type`` first.
"""

STEP_KEY_ORDER: Final[tuple[str, ...]] = (
    "keyword",
    "line",
    "match",
    "name",
    "result",
)
"""The five step-object keys, in alphabetical order."""

STEP_KEYS: Final[frozenset[str]] = frozenset(STEP_KEY_ORDER)
"""The five keys of every member of a scenario's ``steps`` array."""

RESULT_KEY_ORDER: Final[tuple[str, ...]] = ("status", "duration")
"""The two required ``result`` keys, in the order the schema states them."""

RESULT_KEYS: Final[frozenset[str]] = frozenset(RESULT_KEY_ORDER)
"""The keys every step ``result`` sub-object must carry.

``duration`` is an integer count of nanoseconds, matching the source
toolchain's own unit.
"""

OPTIONAL_RESULT_KEYS: Final[frozenset[str]] = frozenset({"error_message"})
"""Keys a ``result`` sub-object MAY carry in addition to :data:`RESULT_KEYS`.

The report writer adds ``error_message`` to the result of every failed step -- an
empty string for all but the first failure of a scenario. Registering it as a
recognised optional is what stops a perfectly ordinary failing run from being
reported as schema-invalid, which would in turn deny the rerun manifest the data
it is derived from. It is optional rather than required because a passing run
never contains it.
"""

SCHEMA_KEY_SETS: Final[Mapping[str, frozenset[str]]] = MappingProxyType(
    {
        "feature": FEATURE_KEYS,
        "scenario": SCENARIO_KEYS,
        "step": STEP_KEYS,
        "result": RESULT_KEYS,
    }
)
"""The required key set of every validated level, keyed by level name.

Exposed read-only so a caller can iterate the schema without importing four
constants, and so the mapping itself can never be mutated by a consumer.
"""


# =============================================================================
# Schema-issue vocabulary.
#
# The validator reports precisely what is wrong and where, rather than a bare
# boolean, so a caller can act on -- or surface -- the specific problem.
# =============================================================================

LEVEL_DOCUMENT: Final[str] = "document"
"""Issue level: the top-level JSON array itself."""

LEVEL_FEATURE: Final[str] = "feature"
"""Issue level: a feature object inside the document array."""

LEVEL_SCENARIO: Final[str] = "scenario"
"""Issue level: a member of a feature's ``elements`` array."""

LEVEL_STEP: Final[str] = "step"
"""Issue level: a member of a scenario's ``steps`` array."""

LEVEL_RESULT: Final[str] = "result"
"""Issue level: a step's ``result`` sub-object."""

LEVEL_TAG: Final[str] = "tag"
"""Issue level: an entry of a ``tags`` array."""

SCHEMA_LEVELS: Final[tuple[str, ...]] = (
    LEVEL_DOCUMENT,
    LEVEL_FEATURE,
    LEVEL_SCENARIO,
    LEVEL_STEP,
    LEVEL_RESULT,
    LEVEL_TAG,
)
"""Every level the validator can report against, outermost first."""

ISSUE_MISSING_KEYS: Final[str] = "missing-keys"
"""Issue kind: required keys are absent from an object."""

ISSUE_UNEXPECTED_KEYS: Final[str] = "unexpected-keys"
"""Issue kind: an object carries keys the frozen schema does not define."""

ISSUE_WRONG_TYPE: Final[str] = "wrong-type"
"""Issue kind: a value cannot be interpreted as the schema requires.

Only the values this module has to interpret are type-checked -- the arrays it
walks, the ``result`` object it reads, and the ``line`` and ``uri`` values the
rerun manifest is derived from. Everything else is checked for presence only, so
that a legitimately null ``description`` is never reported as a defect.
"""

ISSUE_TAG_PREFIXED: Final[str] = "tag-prefixed"
"""Issue kind: a tag value still carries the leading ``@``.

Tags are emitted stripped, so a prefixed value means the document was produced
by something other than the ported toolchain -- or that a consumer put the
prefix back, which nothing in this application may do.
"""

ISSUE_KINDS: Final[tuple[str, ...]] = (
    ISSUE_MISSING_KEYS,
    ISSUE_UNEXPECTED_KEYS,
    ISSUE_WRONG_TYPE,
    ISSUE_TAG_PREFIXED,
)
"""Every issue kind the validator can report."""


# =============================================================================
# Tag vocabulary.
# =============================================================================

TAG_PREFIX: Final[str] = "@"
"""The Gherkin tag prefix, which the report must NOT contain.

Never prepend this to a tag value. It exists so the check for its absence has a
name.
"""

TAG_NAME_KEY: Final[str] = "name"
"""The key carrying the tag value when a tag entry is an object."""

DOCUMENTED_TAG_ORDER: Final[tuple[str, ...]] = (
    "Login",
    "PosManager",
    "SalesManager",
    "UPGN-286",
    "UPGN-287",
    "UPGN-288",
)
"""The six tags the documented feature carries, ``@`` already stripped, sorted.

``Login`` is feature-level; ``UPGN-286`` / ``UPGN-287`` / ``UPGN-288`` are
scenario-level; ``SalesManager`` and ``PosManager`` are ``Examples``-level. The
hyphens are part of the Jira issue keys and are preserved exactly -- never
rewritten to underscores.

There is deliberately no ``LogOut`` entry: no scenario carries that tag, which is
precisely why the runner's default tag expression selects nothing (defect D2).
"""

DOCUMENTED_TAGS: Final[frozenset[str]] = frozenset(DOCUMENTED_TAG_ORDER)
"""The six documented tags, as a set.

Informational only. The validator never rejects a tag for being absent from this
set: a new scenario may legitimately introduce a new tag, and inventing a
closed vocabulary would add behaviour the source system never had. Only the
absence of the ``@`` prefix is enforced.
"""


# =============================================================================
# Status vocabulary.
#
# `passed`, `failed` and `skipped` are what the ported writer emits.
# `pending`, `undefined` and `ambiguous` are the remaining Cucumber statuses;
# they are recognised because the CI publisher's own parameter set counts
# pending, skipped and undefined steps alongside failures, so a document
# produced by the Java toolchain can carry them.
# =============================================================================

STATUS_PASSED: Final[str] = "passed"
"""Step or scenario status: executed successfully."""

STATUS_FAILED: Final[str] = "failed"
"""Step or scenario status: executed and failed."""

STATUS_SKIPPED: Final[str] = "skipped"
"""Step or scenario status: not executed."""

STATUS_PENDING: Final[str] = "pending"
"""Step or scenario status: implementation explicitly incomplete."""

STATUS_UNDEFINED: Final[str] = "undefined"
"""Step or scenario status: no step definition matched."""

STATUS_AMBIGUOUS: Final[str] = "ambiguous"
"""Step or scenario status: more than one step definition matched."""

STATUS_PRECEDENCE: Final[tuple[str, ...]] = (
    STATUS_FAILED,
    STATUS_AMBIGUOUS,
    STATUS_UNDEFINED,
    STATUS_PENDING,
    STATUS_SKIPPED,
    STATUS_PASSED,
)
"""Aggregation order, worst first.

A scenario's status is the first entry of this tuple that any of its steps
carries; a document's status is the first entry any of its scenarios carries.
``passed`` comes last, so a single failure is never hidden behind a majority of
passes.
"""

KNOWN_STATUSES: Final[frozenset[str]] = frozenset(STATUS_PRECEDENCE)
"""Every recognised status value.

An unrecognised status is carried through untouched and counted under its own
name: the report is the source of truth about what happened, and silently
remapping a value would fabricate a result.
"""

FAILING_STATUSES: Final[frozenset[str]] = frozenset({STATUS_FAILED})
"""The statuses that make a scenario a failure.

Exactly one entry, matching the report writer's own notion of a failed scenario.
``pending``, ``undefined`` and ``skipped`` are *not* failures here: the CI
publisher counts them under separate thresholds, and folding them into failures
would change what the rerun manifest contains.
"""


# =============================================================================
# Element types.
# =============================================================================

ELEMENT_TYPE_SCENARIO: Final[str] = "scenario"
"""``elements`` entry type: a scenario, or one parametrisation of an outline."""

ELEMENT_TYPE_BACKGROUND: Final[str] = "background"
"""``elements`` entry type: a ``Background`` block.

The documented feature has a ``Background``, so this really occurs. Two report
shapes are possible and both are handled: the background arrives as its own
element (the Java toolchain's shape), or its steps are folded into the front of
every scenario's ``steps`` array (the ported writer's shape). Counting
scenarios therefore has to filter on this value rather than counting
``elements``.
"""


# =============================================================================
# Load outcomes.
#
# Three states, kept strictly distinct so the API layer can map each onto its
# own response. "Absent" is not an error: a fresh checkout has no artifact tree
# at all, and the report simply has not been generated yet.
# =============================================================================

REPORT_ABSENT: Final[str] = "absent"
"""Load outcome: the artifact does not exist yet."""

REPORT_INVALID: Final[str] = "invalid"
"""Load outcome: the artifact exists but is unreadable, unparseable or off-schema."""

REPORT_VALID: Final[str] = "valid"
"""Load outcome: the artifact exists, parsed, and conforms to the frozen schema."""

REPORT_STATUSES: Final[tuple[str, ...]] = (REPORT_ABSENT, REPORT_INVALID, REPORT_VALID)
"""Every load outcome, in increasing order of usability."""


# =============================================================================
# Validation.
# =============================================================================


@dataclass(frozen=True, slots=True)
class SchemaIssue:
    """One precise, immutable finding about a report document.

    Attributes:
        level: Which level of the document the finding concerns -- one of
            :data:`SCHEMA_LEVELS`.
        location: A JSON-path-like pointer to the offending value, for example
            ``[0].elements[2].steps[1].result``. The document is an array, so
            the outermost component is always an index.
        kind: What is wrong -- one of :data:`ISSUE_KINDS`.
        keys: The specific keys involved, sorted, for a key-set finding. Empty
            for findings that are not about keys.
        detail: A short human-readable explanation, safe to log or to surface as
            an API reason.
    """

    level: str
    location: str
    kind: str
    detail: str
    keys: tuple[str, ...] = ()

    def __str__(self) -> str:
        """Render the finding as a single ``location: kind - detail`` line."""
        return f"{self.location}: {self.kind} - {self.detail}"


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """The outcome of validating one document against the frozen schema.

    Deliberately not a boolean: a caller needs to know precisely which keys are
    missing or unexpected, and where, in order to act on the finding or to
    surface it. The element tallies are collected during the same single walk,
    so a caller never has to re-traverse the document to learn its size.

    Attributes:
        issues: Every finding, in document order. Empty means conformant.
        feature_count: Feature objects in the document.
        scenario_count: ``elements`` entries that are scenarios.
        background_count: ``elements`` entries that are ``Background`` blocks.
        step_count: Step objects across every element.
        tag_count: Tag entries across every level.
    """

    issues: tuple[SchemaIssue, ...] = ()
    feature_count: int = 0
    scenario_count: int = 0
    background_count: int = 0
    step_count: int = 0
    tag_count: int = 0

    @property
    def ok(self) -> bool:
        """``True`` when the document conforms to the frozen schema."""
        return not self.issues

    @property
    def missing_keys(self) -> tuple[str, ...]:
        """Every required key found absent anywhere, de-duplicated and sorted."""
        return self._keys_for(ISSUE_MISSING_KEYS)

    @property
    def unexpected_keys(self) -> tuple[str, ...]:
        """Every key found that the frozen schema does not define, sorted."""
        return self._keys_for(ISSUE_UNEXPECTED_KEYS)

    @property
    def prefixed_tags(self) -> tuple[str, ...]:
        """Every tag value found still carrying the leading ``@``, sorted.

        Non-empty means the ``@``-stripping rule has been violated by whatever
        produced the document. Nothing in this application may put the prefix
        back.
        """
        return self._keys_for(ISSUE_TAG_PREFIXED)

    def issues_at(self, level: str) -> tuple[SchemaIssue, ...]:
        """Return the findings recorded at *level*, in document order.

        Args:
            level: One of :data:`SCHEMA_LEVELS`. An unknown level simply yields
                an empty tuple rather than raising, so a caller can probe
                freely.

        Returns:
            The matching findings.
        """
        return tuple(issue for issue in self.issues if issue.level == level)

    def issues_of_kind(self, kind: str) -> tuple[SchemaIssue, ...]:
        """Return the findings of one :data:`ISSUE_KINDS` value, in order.

        Args:
            kind: One of :data:`ISSUE_KINDS`. An unknown kind yields an empty
                tuple.

        Returns:
            The matching findings.
        """
        return tuple(issue for issue in self.issues if issue.kind == kind)

    def describe(self) -> str:
        """Return a compact, deterministic, human-readable verdict.

        Suitable both for a log record and for the ``reason`` of an API
        response. The findings are rendered in document order, so the same
        document always produces the same text.
        """
        if not self.issues:
            return (
                f"conformant: {self.feature_count} feature(s), "
                f"{self.scenario_count} scenario(s), "
                f"{self.background_count} background(s), "
                f"{self.step_count} step(s)"
            )
        return f"{len(self.issues)} schema issue(s): " + "; ".join(
            str(issue) for issue in self.issues
        )

    def _keys_for(self, kind: str) -> tuple[str, ...]:
        """Collect, de-duplicate and sort the keys reported for one kind."""
        collected: set[str] = set()
        for issue in self.issues:
            if issue.kind == kind:
                collected.update(issue.keys)
        return tuple(sorted(collected))


@dataclass(slots=True)
class _Walk:
    """Function-local accumulator for a single validation traversal.

    Private and mutable on purpose: it exists only for the duration of one
    :func:`validate_document` call, so the public surface stays pure and this
    module keeps no mutable state of its own.
    """

    issues: list[SchemaIssue]
    feature_count: int = 0
    scenario_count: int = 0
    background_count: int = 0
    step_count: int = 0
    tag_count: int = 0

    def add(
        self,
        level: str,
        location: str,
        kind: str,
        detail: str,
        keys: tuple[str, ...] = (),
    ) -> None:
        """Record one finding."""
        self.issues.append(
            SchemaIssue(level=level, location=location, kind=kind, detail=detail, keys=keys)
        )

    def result(self) -> ValidationResult:
        """Freeze the accumulated state into an immutable result."""
        return ValidationResult(
            issues=tuple(self.issues),
            feature_count=self.feature_count,
            scenario_count=self.scenario_count,
            background_count=self.background_count,
            step_count=self.step_count,
            tag_count=self.tag_count,
        )


def _json_array_or_none(value: object) -> JsonArray | None:
    """Return *value* as a JSON array, or ``None`` when it is not one.

    ``str``, ``bytes`` and ``bytearray`` are all sequences, so the naive check
    would happily walk a string character by character and report a cascade of
    meaningless findings; they are excluded explicitly.

    Returning the sequence rather than a boolean is what lets a caller both
    report the mismatch and iterate the value afterwards, with the item type
    known statically at every use site. ``None`` rather than an empty sequence is
    returned on a mismatch so that "not an array" stays distinguishable from
    "an empty array" -- the first is a schema finding, the second is not.
    """
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return value
    return None


def _is_json_int(value: object) -> bool:
    """Return ``True`` when *value* is a JSON integer.

    ``bool`` is a subclass of ``int`` in Python and ``true`` is a perfectly
    valid JSON scalar, so it is excluded explicitly: a line number of ``True``
    is a defect, not a line number.
    """
    return isinstance(value, int) and not isinstance(value, bool)


def _check_keys(
    obj: Mapping[str, object],
    required: frozenset[str],
    optional: frozenset[str],
    level: str,
    location: str,
    walk: _Walk,
) -> None:
    """Compare *obj*'s keys with the frozen schema and record both differences."""
    present = frozenset(obj)
    missing = required - present
    if missing:
        ordered = tuple(sorted(missing))
        walk.add(
            level,
            location,
            ISSUE_MISSING_KEYS,
            f"missing required {level} key(s): {', '.join(ordered)}",
            ordered,
        )
    unexpected = present - required - optional
    if unexpected:
        ordered = tuple(sorted(unexpected))
        walk.add(
            level,
            location,
            ISSUE_UNEXPECTED_KEYS,
            f"unexpected {level} key(s): {', '.join(ordered)}",
            ordered,
        )


def _walk_tags(owner: Mapping[str, object], level: str, location: str, walk: _Walk) -> None:
    """Validate the ``tags`` array of a feature or an element.

    Enforces exactly one rule about a tag's value -- that it does not begin with
    ``@`` -- and never checks the value against a closed vocabulary, so a new
    scenario introducing a new tag is not a defect. Hyphenated Jira keys pass
    through untouched.
    """
    if "tags" not in owner:
        # Absence is already reported as a missing key; nothing further to say.
        return
    tags = owner["tags"]
    entries = _json_array_or_none(tags)
    if entries is None:
        walk.add(
            LEVEL_TAG,
            f"{location}.tags",
            ISSUE_WRONG_TYPE,
            f"tags must be an array, found {type(tags).__name__}",
        )
        return
    for index, entry in enumerate(entries):
        entry_location = f"{location}.tags[{index}]"
        walk.tag_count += 1
        if isinstance(entry, Mapping):
            name = entry.get(TAG_NAME_KEY)
            if not isinstance(name, str):
                walk.add(
                    LEVEL_TAG,
                    entry_location,
                    ISSUE_WRONG_TYPE,
                    f"tag object must carry a string {TAG_NAME_KEY!r}, "
                    f"found {type(name).__name__}",
                )
                continue
        elif isinstance(entry, str):
            name = entry
        else:
            walk.add(
                LEVEL_TAG,
                entry_location,
                ISSUE_WRONG_TYPE,
                f"tag must be an object or a string, found {type(entry).__name__}",
            )
            continue
        if name.startswith(TAG_PREFIX):
            walk.add(
                LEVEL_TAG,
                entry_location,
                ISSUE_TAG_PREFIXED,
                f"tag {name!r} still carries the leading {TAG_PREFIX!r}; "
                "the report must emit tags stripped",
                (name,),
            )


def _walk_result(step: Mapping[str, object], location: str, walk: _Walk) -> None:
    """Validate a step's ``result`` sub-object.

    ``status`` and ``duration`` are required; ``error_message`` is a recognised
    optional, because the writer attaches it to every failed step. Any other key
    is reported as unexpected.
    """
    if "result" not in step:
        return
    result = step["result"]
    result_location = f"{location}.result"
    if not isinstance(result, Mapping):
        walk.add(
            LEVEL_RESULT,
            result_location,
            ISSUE_WRONG_TYPE,
            f"result must be an object, found {type(result).__name__}",
        )
        return
    _check_keys(result, RESULT_KEYS, OPTIONAL_RESULT_KEYS, LEVEL_RESULT, result_location, walk)
    status = result.get("status")
    if "status" in result and not isinstance(status, str):
        walk.add(
            LEVEL_RESULT,
            f"{result_location}.status",
            ISSUE_WRONG_TYPE,
            f"status must be a string, found {type(status).__name__}",
        )
    duration = result.get("duration")
    # The writer emits an integer nanosecond count. A float is accepted rather
    # than reported, because a report produced by another Cucumber
    # implementation may carry one and the value is only ever summed.
    if "duration" in result and not (_is_json_int(duration) or isinstance(duration, float)):
        walk.add(
            LEVEL_RESULT,
            f"{result_location}.duration",
            ISSUE_WRONG_TYPE,
            f"duration must be a number of nanoseconds, found {type(duration).__name__}",
        )


def _walk_step(step: object, location: str, walk: _Walk) -> None:
    """Validate one member of a scenario's ``steps`` array.

    A step ``name`` is never inspected beyond its type. That is deliberate and
    load-bearing: the ported feature legitimately contains step names holding
    literal ``<username>`` / ``<password>`` placeholders (defect D1) and a
    username step fed from the password column (defect D4). Pattern-checking a
    name here would turn either preserved defect into a spurious schema error.
    """
    walk.step_count += 1
    if not isinstance(step, Mapping):
        walk.add(
            LEVEL_STEP,
            location,
            ISSUE_WRONG_TYPE,
            f"step must be an object, found {type(step).__name__}",
        )
        return
    _check_keys(step, STEP_KEYS, frozenset(), LEVEL_STEP, location, walk)
    if "line" in step and not _is_json_int(step["line"]):
        walk.add(
            LEVEL_STEP,
            f"{location}.line",
            ISSUE_WRONG_TYPE,
            f"line must be an integer, found {type(step['line']).__name__}",
        )
    if "name" in step and not isinstance(step["name"], str):
        walk.add(
            LEVEL_STEP,
            f"{location}.name",
            ISSUE_WRONG_TYPE,
            f"name must be a string, found {type(step['name']).__name__}",
        )
    if "match" in step and not isinstance(step["match"], Mapping):
        walk.add(
            LEVEL_STEP,
            f"{location}.match",
            ISSUE_WRONG_TYPE,
            f"match must be an object, found {type(step['match']).__name__}",
        )
    _walk_result(step, location, walk)


def _walk_element(element: object, location: str, walk: _Walk) -> None:
    """Validate one member of a feature's ``elements`` array.

    The array may hold scenarios *and* a ``Background`` block, so the tally is
    split by ``type`` rather than assuming every entry is a scenario. An entry
    whose ``type`` is missing or non-textual is counted as a scenario, which is
    what the ported writer always emits.

    Elements are never de-duplicated or collapsed by name. Several entries
    legitimately share one name here: the second and third scenario outlines of
    the documented feature parse to the identical name (defect D9), so the five
    parametrisations of that name are distinct, meaningful entries.
    """
    if not isinstance(element, Mapping):
        walk.scenario_count += 1
        walk.add(
            LEVEL_SCENARIO,
            location,
            ISSUE_WRONG_TYPE,
            f"element must be an object, found {type(element).__name__}",
        )
        return

    element_type = element.get("type")
    if isinstance(element_type, str) and element_type == ELEMENT_TYPE_BACKGROUND:
        walk.background_count += 1
    else:
        walk.scenario_count += 1

    _check_keys(element, SCENARIO_KEYS, frozenset(), LEVEL_SCENARIO, location, walk)
    if "type" in element and not isinstance(element_type, str):
        walk.add(
            LEVEL_SCENARIO,
            f"{location}.type",
            ISSUE_WRONG_TYPE,
            f"type must be a string, found {type(element_type).__name__}",
        )
    if "line" in element and not _is_json_int(element["line"]):
        walk.add(
            LEVEL_SCENARIO,
            f"{location}.line",
            ISSUE_WRONG_TYPE,
            f"line must be an integer, found {type(element['line']).__name__}",
        )
    if "name" in element and not isinstance(element["name"], str):
        walk.add(
            LEVEL_SCENARIO,
            f"{location}.name",
            ISSUE_WRONG_TYPE,
            f"name must be a string, found {type(element['name']).__name__}",
        )
    _walk_tags(element, LEVEL_SCENARIO, location, walk)

    if "steps" not in element:
        return
    steps = element["steps"]
    entries = _json_array_or_none(steps)
    if entries is None:
        walk.add(
            LEVEL_SCENARIO,
            f"{location}.steps",
            ISSUE_WRONG_TYPE,
            f"steps must be an array, found {type(steps).__name__}",
        )
        return
    for index, step in enumerate(entries):
        _walk_step(step, f"{location}.steps[{index}]", walk)


def _walk_feature(feature: object, location: str, walk: _Walk) -> None:
    """Validate one feature object of the document array."""
    walk.feature_count += 1
    if not isinstance(feature, Mapping):
        walk.add(
            LEVEL_FEATURE,
            location,
            ISSUE_WRONG_TYPE,
            f"feature must be an object, found {type(feature).__name__}",
        )
        return
    _check_keys(feature, FEATURE_KEYS, frozenset(), LEVEL_FEATURE, location, walk)
    if "uri" in feature and not isinstance(feature["uri"], str):
        walk.add(
            LEVEL_FEATURE,
            f"{location}.uri",
            ISSUE_WRONG_TYPE,
            f"uri must be a string, found {type(feature['uri']).__name__}",
        )
    if "line" in feature and not _is_json_int(feature["line"]):
        walk.add(
            LEVEL_FEATURE,
            f"{location}.line",
            ISSUE_WRONG_TYPE,
            f"line must be an integer, found {type(feature['line']).__name__}",
        )
    if "name" in feature and not isinstance(feature["name"], str):
        walk.add(
            LEVEL_FEATURE,
            f"{location}.name",
            ISSUE_WRONG_TYPE,
            f"name must be a string, found {type(feature['name']).__name__}",
        )
    _walk_tags(feature, LEVEL_FEATURE, location, walk)

    if "elements" not in feature:
        return
    elements = feature["elements"]
    entries = _json_array_or_none(elements)
    if entries is None:
        walk.add(
            LEVEL_FEATURE,
            f"{location}.elements",
            ISSUE_WRONG_TYPE,
            f"elements must be an array, found {type(elements).__name__}",
        )
        return
    for index, element in enumerate(entries):
        _walk_element(element, f"{location}.elements[{index}]", walk)


def validate_document(document: object) -> ValidationResult:
    """Validate a decoded Cucumber JSON document against the frozen schema.

    Checks all three levels -- feature, scenario and step -- plus every step's
    ``result`` sub-object and every ``tags`` entry, and reports each difference
    precisely rather than collapsing the verdict into a boolean.

    The function is total: it never raises for any input. A value of the wrong
    shape at any depth -- a top-level object where an array belongs, a string
    where a step belongs, a ``null`` ``result`` -- becomes a finding, so a
    caller handed an arbitrary decoded payload always gets a verdict back.

    Args:
        document: A decoded JSON payload, normally the ``list`` returned by
            ``json.loads``. Any other object is accepted and reported on.

    Returns:
        An immutable :class:`ValidationResult`. ``result.ok`` is ``True`` only
        when the document carries no finding at all.
    """
    walk = _Walk(issues=[])
    features = _json_array_or_none(document)
    if features is None:
        walk.add(
            LEVEL_DOCUMENT,
            "$",
            ISSUE_WRONG_TYPE,
            f"document must be a JSON array of features, found {type(document).__name__}",
        )
        return walk.result()
    for index, feature in enumerate(features):
        _walk_feature(feature, f"[{index}]", walk)
    return walk.result()


def tag_names(owner: object) -> tuple[str, ...]:
    """Extract tag values from a feature, an element, or a bare ``tags`` array.

    Accepts every shape a report producer may use: tag entries as objects
    carrying a ``name``, or as bare strings. Values are returned exactly as they
    appear -- in document order, with hyphens intact and with no prefix added or
    removed. Entries that carry no usable value are skipped rather than raising,
    which keeps the helper usable on a document that has already failed
    validation.

    Args:
        owner: A feature or element mapping (its ``tags`` key is read), or the
            ``tags`` array itself.

    Returns:
        The tag values, in document order and with duplicates preserved.
    """
    if isinstance(owner, Mapping):
        raw: object = owner.get("tags")
    else:
        raw = owner
    entries = _json_array_or_none(raw)
    if entries is None:
        return ()
    names: list[str] = []
    for entry in entries:
        if isinstance(entry, Mapping):
            value = entry.get(TAG_NAME_KEY)
            if isinstance(value, str):
                names.append(value)
        elif isinstance(entry, str):
            names.append(entry)
    return tuple(names)


# =============================================================================
# The normalized view.
#
# A typed, immutable projection of the document for the adapters and services
# that consume it. Three properties make it worth existing:
#
#   * it is total -- a missing or mistyped value becomes a neutral default
#     instead of an exception three frames away in a consumer;
#   * it hides the two report shapes behind one API -- a `Background` may arrive
#     as its own element or folded into every scenario's steps, and either way
#     `scenarios` returns scenarios and nothing else;
#   * it is deterministic -- features and elements are ordered by a stable key,
#     so two runs over the same document produce identical output regardless of
#     the order the workers of a parallel run happened to report in.
#
# Step order is the one thing never sorted: Given / When / Then is semantic.
# Nothing is ever de-duplicated: several elements legitimately share one name
# (defect D9) and dropping any of them would corrupt the rerun manifest.
# =============================================================================


@dataclass(frozen=True, slots=True)
class NormalizedStep:
    """One step of one element, with its result flattened onto it.

    Field names mirror the report's own keys; ``status``, ``duration`` and
    ``error_message`` are lifted out of the nested ``result`` object.

    Attributes:
        keyword: The Gherkin keyword, including its trailing space, exactly as
            recorded.
        name: The step name, byte-for-byte as recorded. Never interpolated,
            trimmed, translated or normalized -- see defects D1, D4 and D5.
        line: The 1-based line the step occupies in its feature file.
        status: One of :data:`KNOWN_STATUSES`, or whatever the report carried.
        duration: Execution time in nanoseconds, the report's own unit.
        error_message: The failure text when the report supplied one, otherwise
            ``None``. The writer attaches an empty string to the failed steps
            after the first, so ``""`` and ``None`` are genuinely different.
        match_location: The ``match.location`` value; empty when unset, which is
            what the ported writer always emits.
    """

    keyword: str
    name: str
    line: int
    status: str
    duration: int
    error_message: str | None = None
    match_location: str = ""

    @property
    def failed(self) -> bool:
        """``True`` when this step's status makes its scenario a failure."""
        return self.status in FAILING_STATUSES

    @property
    def passed(self) -> bool:
        """``True`` when this step passed."""
        return self.status == STATUS_PASSED


@dataclass(frozen=True, slots=True)
class NormalizedElement:
    """One member of a feature's ``elements`` array: a scenario or a background.

    Attributes:
        uri: The feature file's URI, carried down so an element is
            self-describing -- the rerun manifest needs both halves of
            ``<uri>:<line>`` and should not have to walk back up.
        feature_name: The owning feature's name, carried down for the same
            reason.
        element_id: The report's ``id`` value. Unique per executed test, which
            is what keeps several same-named scenarios distinguishable.
        keyword: The Gherkin keyword, ``Scenario Outline`` for example.
        name: The element name exactly as recorded. Several elements may share
            one name (defect D9); that is expected, not corruption.
        line: The 1-based line the element starts on in its feature file.
        element_type: The report's ``type`` value -- :data:`ELEMENT_TYPE_SCENARIO`
            or :data:`ELEMENT_TYPE_BACKGROUND`.
        tags: The element's tags, ``@`` already stripped, sorted and
            de-duplicated.
        steps: The element's steps in their recorded order, which is never
            re-sorted.
    """

    uri: str
    feature_name: str
    element_id: str
    keyword: str
    name: str
    line: int
    element_type: str
    tags: tuple[str, ...] = ()
    steps: tuple[NormalizedStep, ...] = ()

    @property
    def is_background(self) -> bool:
        """``True`` when this element is a ``Background`` block, not a scenario."""
        return self.element_type == ELEMENT_TYPE_BACKGROUND

    @property
    def is_scenario(self) -> bool:
        """``True`` when this element counts as a scenario.

        An element with a missing or unrecognised ``type`` counts as a scenario,
        because that is the only shape the ported writer emits.
        """
        return not self.is_background

    @property
    def status(self) -> str:
        """The element's aggregated status, worst step wins.

        An element with no steps reports :data:`STATUS_SKIPPED`: nothing ran, so
        it can be neither a pass nor a failure, and inventing a failure would
        break the non-gating behaviour the port preserves.
        """
        return _aggregate_status(step.status for step in self.steps)

    @property
    def failed(self) -> bool:
        """``True`` when any step of this element failed."""
        return self.status in FAILING_STATUSES

    @property
    def duration(self) -> int:
        """The summed step duration, in nanoseconds."""
        return sum(step.duration for step in self.steps)

    @property
    def location(self) -> str:
        """The element's ``<uri>:<line>`` locator.

        This is the raw material the rerun manifest is built from. Producing the
        manifest itself belongs to the rerun adapter, not here.
        """
        return f"{self.uri}:{self.line}"

    @property
    def failed_steps(self) -> tuple[NormalizedStep, ...]:
        """The failing steps, in their recorded order."""
        return tuple(step for step in self.steps if step.failed)


@dataclass(frozen=True, slots=True)
class NormalizedFeature:
    """One feature of the document, with its elements normalized and ordered.

    Attributes:
        uri: The feature file's URI as recorded, forward slashes included.
        feature_id: The report's ``id`` value.
        keyword: The Gherkin keyword, normally ``Feature``.
        name: The feature name as recorded.
        line: The 1-based line the ``Feature`` keyword occupies.
        description: The feature description, or ``""`` when the report carried
            none -- a legitimately absent description is not a defect.
        language: The feature's language code, or ``""`` when unset.
        tags: The feature-level tags, ``@`` stripped, sorted and de-duplicated.
        elements: Every element, scenarios and backgrounds alike, in a
            deterministic order.
    """

    uri: str
    feature_id: str
    keyword: str
    name: str
    line: int
    description: str = ""
    language: str = ""
    tags: tuple[str, ...] = ()
    elements: tuple[NormalizedElement, ...] = ()

    @property
    def scenarios(self) -> tuple[NormalizedElement, ...]:
        """The elements that are scenarios, backgrounds excluded."""
        return tuple(element for element in self.elements if element.is_scenario)

    @property
    def backgrounds(self) -> tuple[NormalizedElement, ...]:
        """The elements that are ``Background`` blocks."""
        return tuple(element for element in self.elements if element.is_background)

    @property
    def steps(self) -> tuple[NormalizedStep, ...]:
        """Every step of every element, in element then step order."""
        return tuple(step for element in self.elements for step in element.steps)

    @property
    def status(self) -> str:
        """The feature's aggregated status over its scenarios, worst first."""
        return _aggregate_status(scenario.status for scenario in self.scenarios)

    @property
    def failed(self) -> bool:
        """``True`` when any scenario of this feature failed."""
        return self.status in FAILING_STATUSES


@dataclass(frozen=True, slots=True)
class RunSummary:
    """A compact, JSON-friendly summary of one report document.

    This is what a run-status consumer needs: how much ran, how it went, and
    which tags took part.

    A summary with ``scenario_count == 0`` is **not** a failure. The runner's
    preserved default tag expression selects no scenario at all (defect D2), so
    an empty report is the ordinary outcome of the documented invocation.
    Callers must therefore key off :attr:`has_failures`, never off
    ``status != "passed"``.

    Attributes:
        status: The aggregated status across every scenario.
        feature_count: Features in the document, after same-``uri`` merging.
        scenario_count: Scenario elements, backgrounds excluded.
        background_count: ``Background`` elements, when the producer emits them
            separately rather than folding them into each scenario.
        step_count: Steps across every element.
        failed_scenario_count: Scenarios with at least one failed step.
        duration_ns: Summed step duration in nanoseconds.
        scenario_status_counts: Scenario tally by status, keys sorted.
        step_status_counts: Step tally by status, keys sorted.
        tags: Every distinct tag seen at any level, sorted, ``@`` stripped.
    """

    status: str
    feature_count: int = 0
    scenario_count: int = 0
    background_count: int = 0
    step_count: int = 0
    failed_scenario_count: int = 0
    duration_ns: int = 0
    scenario_status_counts: Mapping[str, int] = MappingProxyType({})
    step_status_counts: Mapping[str, int] = MappingProxyType({})
    tags: tuple[str, ...] = ()

    @property
    def has_failures(self) -> bool:
        """``True`` when at least one scenario failed."""
        return self.failed_scenario_count > 0

    @property
    def is_empty(self) -> bool:
        """``True`` when the document contains no scenario at all.

        The expected outcome of the default invocation, and a success.
        """
        return self.scenario_count == 0

    def as_dict(self) -> dict[str, object]:
        """Return the summary as a plain, deterministically ordered dictionary.

        A fresh dictionary is built on every call and the two nested tallies are
        copied out of their read-only views, so the result is the caller's to
        mutate and can never corrupt this summary. Mapping it onto an HTTP
        response belongs to the API layer.
        """
        return {
            "status": self.status,
            "feature_count": self.feature_count,
            "scenario_count": self.scenario_count,
            "background_count": self.background_count,
            "step_count": self.step_count,
            "failed_scenario_count": self.failed_scenario_count,
            "duration_ns": self.duration_ns,
            "scenario_status_counts": dict(self.scenario_status_counts),
            "step_status_counts": dict(self.step_status_counts),
            "tags": list(self.tags),
            "has_failures": self.has_failures,
            "is_empty": self.is_empty,
        }


@dataclass(frozen=True, slots=True)
class NormalizedReport:
    """Every feature of one document, normalized and deterministically ordered."""

    features: tuple[NormalizedFeature, ...] = ()

    @property
    def elements(self) -> tuple[NormalizedElement, ...]:
        """Every element of every feature, in feature then element order."""
        return tuple(element for feature in self.features for element in feature.elements)

    @property
    def scenarios(self) -> tuple[NormalizedElement, ...]:
        """Every scenario element, backgrounds excluded, in document order."""
        return tuple(element for element in self.elements if element.is_scenario)

    @property
    def backgrounds(self) -> tuple[NormalizedElement, ...]:
        """Every ``Background`` element, when the producer emits them separately."""
        return tuple(element for element in self.elements if element.is_background)

    @property
    def steps(self) -> tuple[NormalizedStep, ...]:
        """Every step of every element."""
        return tuple(step for element in self.elements for step in element.steps)

    @property
    def failed_scenarios(self) -> tuple[NormalizedElement, ...]:
        """The scenarios with at least one failed step, in document order.

        Never de-duplicated: five scenarios legitimately share one name and one
        line here (defect D9), and each is a distinct failure. What the rerun
        manifest does with them is the rerun adapter's decision.
        """
        return tuple(scenario for scenario in self.scenarios if scenario.failed)

    @property
    def tags(self) -> tuple[str, ...]:
        """Every distinct tag at any level, sorted, with no ``@`` re-added."""
        collected: set[str] = set()
        for feature in self.features:
            collected.update(feature.tags)
            for element in feature.elements:
                collected.update(element.tags)
        return tuple(sorted(collected))

    @property
    def status(self) -> str:
        """The aggregated status across every scenario, worst first."""
        return _aggregate_status(scenario.status for scenario in self.scenarios)

    @property
    def summary(self) -> RunSummary:
        """Build the :class:`RunSummary` for this report."""
        scenarios = self.scenarios
        steps = self.steps
        return RunSummary(
            status=self.status,
            feature_count=len(self.features),
            scenario_count=len(scenarios),
            background_count=len(self.backgrounds),
            step_count=len(steps),
            failed_scenario_count=len(self.failed_scenarios),
            duration_ns=sum(step.duration for step in steps),
            scenario_status_counts=_count_statuses(scenario.status for scenario in scenarios),
            step_status_counts=_count_statuses(step.status for step in steps),
            tags=self.tags,
        )


# =============================================================================
# Coercion and aggregation helpers.
#
# Every one of these is total: it turns an untrusted value into a usable one, or
# into a neutral default, and never raises. That is what makes the normalized
# view safe to build from a document that has already failed validation.
# =============================================================================


def _as_json_array(value: object) -> JsonArray:
    """Return *value* when it is a JSON array, otherwise an empty sequence.

    The coercing counterpart of :func:`_json_array_or_none`, for the places that
    only want to iterate and have no finding to report -- the normalizer, which
    is total by contract.
    """
    entries = _json_array_or_none(value)
    return () if entries is None else entries


def _as_str(value: object, default: str = "") -> str:
    """Return *value* when it is a string, otherwise *default*.

    A ``null`` description or an absent language code is a legitimate report
    value, not a defect, so it becomes the neutral default rather than an error.
    """
    return value if isinstance(value, str) else default


def _as_optional_str(value: object) -> str | None:
    """Return *value* when it is a string, otherwise ``None``.

    Used where an empty string and an absent value genuinely differ: the report
    writer attaches an empty ``error_message`` to the failed steps after the
    first one, and flattening that to ``None`` would lose the distinction.
    """
    return value if isinstance(value, str) else None


def _as_int(value: object, default: int = 0) -> int:
    """Return *value* as an integer, or *default* when it is not a number.

    ``bool`` is rejected even though it is an ``int`` subclass: ``true`` is a
    valid JSON scalar but never a valid line number or duration. A float is
    truncated, so a duration produced by another Cucumber implementation still
    sums correctly.
    """
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return default


def _aggregate_status(statuses: Iterable[str]) -> str:
    """Reduce many step or scenario statuses to one, worst first.

    Args:
        statuses: The statuses to reduce. May be empty.

    Returns:
        The first :data:`STATUS_PRECEDENCE` entry present. With no statuses at
        all the answer is :data:`STATUS_SKIPPED`: nothing ran, so the outcome is
        neither a pass nor a failure. If only unrecognised statuses are present
        the alphabetically first is returned, which keeps the answer
        deterministic while still reporting what the document actually carried
        rather than remapping it.
    """
    present = set(statuses)
    if not present:
        return STATUS_SKIPPED
    for candidate in STATUS_PRECEDENCE:
        if candidate in present:
            return candidate
    return sorted(present)[0]


def _count_statuses(statuses: Iterable[str]) -> Mapping[str, int]:
    """Tally *statuses* into a read-only mapping whose keys are sorted.

    Sorting the keys makes the tally byte-identical for the same data whatever
    order it arrived in -- which is exactly what a parallel run cannot promise.
    """
    tally: dict[str, int] = {}
    for status in statuses:
        tally[status] = tally.get(status, 0) + 1
    return MappingProxyType({key: tally[key] for key in sorted(tally)})


def _normalize_step(step: object) -> NormalizedStep:
    """Project one raw step object onto :class:`NormalizedStep`.

    The step ``name`` is copied verbatim. No placeholder is substituted, no
    template is rendered, no whitespace is trimmed and no Unicode normalization
    is applied, so the literal ``<username>`` / ``<password>`` names (defect
    D1), the password-column value fed to the username step (defect D4) and the
    French assertion message (defect D5) all survive byte-exactly.
    """
    if not isinstance(step, Mapping):
        return NormalizedStep(keyword="", name="", line=0, status=STATUS_SKIPPED, duration=0)

    status = STATUS_SKIPPED
    duration = 0
    error_message: str | None = None
    result = step.get("result")
    if isinstance(result, Mapping):
        status = _as_str(result.get("status"), STATUS_SKIPPED)
        duration = _as_int(result.get("duration"))
        error_message = _as_optional_str(result.get("error_message"))

    match = step.get("match")
    match_location = _as_str(match.get("location")) if isinstance(match, Mapping) else ""

    return NormalizedStep(
        keyword=_as_str(step.get("keyword")),
        name=_as_str(step.get("name")),
        line=_as_int(step.get("line")),
        status=status or STATUS_SKIPPED,
        duration=duration,
        error_message=error_message,
        match_location=match_location,
    )


def _normalize_element(element: object, uri: str, feature_name: str) -> NormalizedElement:
    """Project one raw ``elements`` entry onto :class:`NormalizedElement`.

    Branches on the entry's ``type`` so a ``Background`` block is never counted
    as a scenario. An absent, empty or non-textual ``type`` is read as a
    scenario, which is the only shape the ported writer emits -- and the other
    possible shape, background steps folded into the front of every scenario's
    ``steps`` array, needs no special handling at all because those steps simply
    belong to the scenario.
    """
    if not isinstance(element, Mapping):
        return NormalizedElement(
            uri=uri,
            feature_name=feature_name,
            element_id="",
            keyword="",
            name="",
            line=0,
            element_type=ELEMENT_TYPE_SCENARIO,
        )
    return NormalizedElement(
        uri=uri,
        feature_name=feature_name,
        element_id=_as_str(element.get("id")),
        keyword=_as_str(element.get("keyword")),
        name=_as_str(element.get("name")),
        line=_as_int(element.get("line")),
        element_type=_as_str(element.get("type"), ELEMENT_TYPE_SCENARIO) or ELEMENT_TYPE_SCENARIO,
        tags=tuple(sorted(set(tag_names(element)))),
        steps=tuple(_normalize_step(raw) for raw in _as_json_array(element.get("steps"))),
    )


def _element_sort_key(element: NormalizedElement) -> tuple[object, ...]:
    """Return the stable ordering key of an element.

    ``line`` first, so a feature reads in file order; then ``id``, which the
    writer makes unique per executed test and which is therefore what keeps the
    five same-named parametrisations of defect D9 in a fixed order; then the
    remaining content, so the ordering is total and re-ordering the input cannot
    change the output. Nothing is dropped and nothing is merged -- this only
    ever reorders.
    """
    return (
        element.line,
        element.element_id,
        element.name,
        element.element_type,
        element.tags,
        tuple(
            (step.line, step.keyword, step.name, step.status, step.duration)
            for step in element.steps
        ),
    )


def _feature_sort_key(feature: NormalizedFeature) -> tuple[object, ...]:
    """Return the stable ordering key of a feature.

    ``uri`` first, because it is what the CI publisher and the rerun manifest
    identify a feature by, and because features sharing a ``uri`` have already
    been merged into one by the time this runs.
    """
    return (feature.uri, feature.feature_id, feature.name, feature.line)


def normalize_document(document: object) -> NormalizedReport:
    """Project a decoded document onto the deterministic normalized view.

    Three things happen here, and each exists for a reason recorded in the
    migration plan:

    * **Features sharing a ``uri`` are merged into one**, by *concatenating*
      their ``elements`` and taking the union of their tags. A parallel run can
      report the same feature more than once, and merging by concatenation is
      what guarantees the parallel document still contains every scenario the
      serial one did. Nothing is ever discarded.
    * **Features and elements are ordered by a stable key**, so the same
      document always yields the same view no matter which worker reported
      first. Steps keep their recorded order, which is semantic.
    * **Nothing is de-duplicated.** Several elements legitimately share one name
      (defect D9); collapsing them would destroy the parametrisation data the
      rerun manifest is built from.

    The function is total: it never raises. A payload that is not an array, or
    whose entries are not objects, simply yields a report with no features.

    Args:
        document: A decoded JSON payload, normally the ``list`` returned by
            ``json.loads``.

    Returns:
        An immutable :class:`NormalizedReport`.
    """
    ordered_keys: list[tuple[str, str | int]] = []
    grouped: dict[tuple[str, str | int], list[Mapping[str, object]]] = {}

    for index, raw_feature in enumerate(_as_json_array(document)):
        if not isinstance(raw_feature, Mapping):
            # A non-object entry carries nothing to normalize. The validator has
            # already reported it, so it is skipped rather than reported twice.
            continue
        raw_uri = raw_feature.get("uri")
        # Features are grouped by `uri` when they have one. A feature without a
        # usable `uri` is keyed by its position instead, so unrelated features
        # can never be merged together just because both lack the value.
        key: tuple[str, str | int] = (
            ("uri", raw_uri) if isinstance(raw_uri, str) else ("index", index)
        )
        if key not in grouped:
            grouped[key] = []
            ordered_keys.append(key)
        grouped[key].append(raw_feature)

    features: list[NormalizedFeature] = []
    for key in ordered_keys:
        members = grouped[key]
        primary = members[0]
        uri = _as_str(primary.get("uri"))
        name = _as_str(primary.get("name"))
        if len(members) > 1:
            _LOGGER.debug(
                "Merging %d report entries for feature %r by concatenating their elements",
                len(members),
                uri or name,
            )
        tags: set[str] = set()
        elements: list[NormalizedElement] = []
        for member in members:
            tags.update(tag_names(member))
            for raw_element in _as_json_array(member.get("elements")):
                elements.append(_normalize_element(raw_element, uri, name))
        elements.sort(key=_element_sort_key)
        features.append(
            NormalizedFeature(
                uri=uri,
                feature_id=_as_str(primary.get("id")),
                keyword=_as_str(primary.get("keyword")),
                name=name,
                line=_as_int(primary.get("line")),
                description=_as_str(primary.get("description")),
                language=_as_str(primary.get("language")),
                tags=tuple(sorted(tags)),
                elements=tuple(elements),
            )
        )

    features.sort(key=_feature_sort_key)
    return NormalizedReport(features=tuple(features))


def summarize_document(document: object) -> RunSummary:
    """Normalize *document* and return its :class:`RunSummary`.

    A convenience for the callers that want the numbers and not the structure --
    the run-status endpoint above all.

    Args:
        document: A decoded JSON payload.

    Returns:
        The summary. A payload with no scenarios yields an empty summary, which
        is a success rather than a failure (defect D2).
    """
    return normalize_document(document).summary


# =============================================================================
# Loading.
#
# Three outcomes, kept strictly apart, because the API layer maps each onto a
# different response and because conflating them would hide real problems:
#
#   absent   the artifact has not been generated yet. A fresh checkout has no
#            artifact tree at all, so this is the ordinary starting state and
#            never an error.
#   invalid  the artifact exists but cannot be trusted: unreadable, empty, not
#            JSON, or off-schema. The reason always says which.
#   valid    the artifact exists, parsed, and conforms to the frozen schema.
#
# No exception escapes, and nothing is papered over: an empty successful result
# is never returned in place of a real problem.
# =============================================================================


def report_path(base_dir: StrPath | None = None) -> Path:
    """Return the location of the Cucumber JSON artifact.

    The path is never composed here. It comes from ``app.utils.paths``, the one
    module that knows the artifact-root name, so the CI publisher's include
    pattern keeps matching and the root can never be renamed by accident.

    Args:
        base_dir: Optional directory the artifact root should sit inside. The
            default returns the repository-relative location that the test
            configuration and the CI pipeline already use; passing a temporary
            directory re-roots the whole layout, which is how a test points the
            loader somewhere harmless.

    Returns:
        The path of the Cucumber JSON report. The file is not required to exist
        and is never created here.
    """
    if base_dir is None:
        return CUCUMBER_JSON_PATH
    return resolve_layout(base_dir).cucumber_json


@dataclass(frozen=True, slots=True)
class CucumberJsonReport:
    """The result of loading, parsing and validating the report artifact.

    Attributes:
        status: One of :data:`REPORT_STATUSES` -- ``absent``, ``invalid`` or
            ``valid``. Always set, so a caller always has something to branch
            on.
        path: The location that was read.
        reason: Why the artifact is absent or invalid, ready to be logged or
            surfaced. ``None`` when the artifact is valid.
        features: The raw feature objects, in the order the document listed them.
            Empty when the artifact is absent or could not be parsed. Kept raw so
            a consumer that needs the untouched document -- the PrettyReports
            adapter, for instance -- is not forced through the normalized view.
        validation: The full schema verdict, including the tallies. Empty and
            conformant when the artifact is absent, because there was nothing to
            contradict.
    """

    status: str
    path: Path
    reason: str | None = None
    features: tuple[JsonObject, ...] = ()
    validation: ValidationResult = ValidationResult()

    @property
    def is_absent(self) -> bool:
        """``True`` when the report has not been generated yet."""
        return self.status == REPORT_ABSENT

    @property
    def is_invalid(self) -> bool:
        """``True`` when the report exists but cannot be trusted."""
        return self.status == REPORT_INVALID

    @property
    def is_valid(self) -> bool:
        """``True`` when the report exists and conforms to the frozen schema."""
        return self.status == REPORT_VALID

    @property
    def normalized(self) -> NormalizedReport:
        """The deterministic normalized view of whatever was parsed.

        Available even when :attr:`status` is ``invalid``: a document that failed
        validation may still hold usable scenarios, and refusing to project it
        would deny a consumer data the source system would have published. An
        absent report normalizes to an empty view.
        """
        return normalize_document(self.features)

    @property
    def summary(self) -> RunSummary:
        """The :class:`RunSummary` of whatever was parsed."""
        return self.normalized.summary


def load_report(path: StrPath | None = None) -> CucumberJsonReport:
    """Load, parse and validate the Cucumber JSON report artifact.

    Every file access below passes an explicit UTF-8 encoding, so the report's
    content -- which this application does not control -- is decoded identically
    on every platform and locale.

    The function is total: no exception escapes it. Each failure mode becomes a
    distinct, reported outcome instead:

    * the file does not exist, or a path component is not a directory -> ``absent``
      with a "not generated yet" reason;
    * the file is a directory, unreadable, or not decodable as UTF-8 ->
      ``invalid`` naming the operating-system or decoding error;
    * the file is empty or is not valid JSON -> ``invalid`` naming the parse
      position;
    * the file parses but breaks the frozen schema -> ``invalid`` carrying the
      full list of findings;
    * otherwise -> ``valid``.

    Args:
        path: Optional explicit location, which lets a test point the loader at
            a fixture. Defaults to :func:`report_path`.

    Returns:
        An immutable :class:`CucumberJsonReport`.
    """
    target = report_path() if path is None else Path(path)
    location = to_posix(target)

    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError, NotADirectoryError:
        # Not an error: `target/` is wiped before every run and a fresh checkout
        # never contains it, so "not generated yet" is the ordinary state.
        _LOGGER.debug("Cucumber JSON report not generated yet at %s", location)
        return CucumberJsonReport(
            status=REPORT_ABSENT,
            path=target,
            reason=f"report has not been generated yet at {location}",
        )
    except OSError as error:
        # Covers a directory in the file's place, a permission failure and every
        # other filesystem refusal.
        reason = f"report at {location} could not be read ({type(error).__name__}: {error})"
        _LOGGER.warning("Cucumber JSON report unreadable: %s", reason)
        return CucumberJsonReport(status=REPORT_INVALID, path=target, reason=reason)
    except UnicodeDecodeError as error:
        # Not an OSError, so it needs its own clause: the report is expected to
        # be UTF-8 and anything else is a defect in whatever produced it.
        reason = f"report at {location} is not valid UTF-8 ({error})"
        _LOGGER.warning("Cucumber JSON report undecodable: %s", reason)
        return CucumberJsonReport(status=REPORT_INVALID, path=target, reason=reason)

    if not text.strip():
        reason = f"report at {location} is empty"
        _LOGGER.warning("Cucumber JSON report empty: %s", reason)
        return CucumberJsonReport(status=REPORT_INVALID, path=target, reason=reason)

    try:
        # `json.loads` on text already decoded as UTF-8, rather than `json.load`
        # on a stream whose encoding would depend on the platform default.
        # The result is bound as `object` so every value has to be narrowed
        # explicitly before it is interpreted.
        document: object = json.loads(text)
    except json.JSONDecodeError as error:
        reason = (
            f"report at {location} is not valid JSON "
            f"({error.msg} at line {error.lineno} column {error.colno})"
        )
        _LOGGER.warning("Cucumber JSON report malformed: %s", reason)
        return CucumberJsonReport(status=REPORT_INVALID, path=target, reason=reason)

    validation = validate_document(document)
    features = tuple(entry for entry in _as_json_array(document) if isinstance(entry, Mapping))

    if not validation.ok:
        reason = f"report at {location} does not match the expected schema: {validation.describe()}"
        _LOGGER.warning(
            "Cucumber JSON report off-schema at %s: %d issue(s)",
            location,
            len(validation.issues),
        )
        return CucumberJsonReport(
            status=REPORT_INVALID,
            path=target,
            reason=reason,
            features=features,
            validation=validation,
        )

    _LOGGER.debug(
        "Loaded Cucumber JSON report from %s: %d feature(s), %d scenario(s), %d step(s)",
        location,
        validation.feature_count,
        validation.scenario_count,
        validation.step_count,
    )
    return CucumberJsonReport(
        status=REPORT_VALID,
        path=target,
        reason=None,
        features=features,
        validation=validation,
    )
