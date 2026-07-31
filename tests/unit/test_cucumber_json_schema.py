"""Criterion V5: the emitted Cucumber JSON conforms to the source report schema.

Source construct
================
One plugin declaration, and only one: ``"json:target/cucumber.json"`` from the
``@CucumberOptions`` block of the documented ``CukesRunner`` -- ``[README.md:L80]`` in the
committed file (the migration plan writes ``L79``; the four plugin strings really occupy
``L79-L82``, so the literal is quoted here as well as the locator). It was backed by
``me.jvt.cucumber:reporting-plugin`` version ``7.2.0`` ``[pom.xml:L66-L70]``, and the report
publisher consumes its output through ``fileIncludePattern: '**/*.json'`` ``[Jenkins:L15]``
-- a literal preserved verbatim as data, never rewritten and never evaluated as a glob here.

One source construct maps to one target module, so the artifact this suite asserts about is
owned by :mod:`app.reporting.cucumber_json` alone. The other three plugin declarations --
``html:``, ``rerun:`` and ``PrettyReports:`` -- belong to ``app/reporting/html_report.py``,
``app/reporting/rerun_report.py`` and ``app/reporting/pretty_reports.py``, and nothing about
them is asserted below.

What this suite proves
======================
The third-party Java reporting plugin turned out to be unnecessary: ``pytest-bdd`` ships a
native Cucumber JSON writer that emits the very schema the pipeline's publisher already
consumes. That single fact is what makes report parity achievable without porting a report
serializer, and it is why this module validates a **schema** rather than a bespoke
serializer. Three key sets are frozen, and they are restated here independently of the
application so that this suite is evidence rather than a tautology:

* feature, 9 keys -- ``description``, ``elements``, ``id``, ``keyword``, ``language``,
  ``line``, ``name``, ``tags``, ``uri``
* scenario, 8 keys, the members of a feature's ``elements`` array -- ``description``, ``id``,
  ``keyword``, ``line``, ``name``, ``steps``, ``tags``, ``type``
* step, 5 keys, the members of a scenario's ``steps`` array -- ``keyword``, ``line``,
  ``match``, ``name``, ``result``

A step's ``result`` carries ``status`` and ``duration``; its ``match`` carries ``location``.
Tag values carry **no** leading ``@``: the parser strips the prefix while reading the feature
file, exactly as the source toolchain's own writer did, so a prefixed value would mean
something other than the ported toolchain produced the document.

Two halves, and why they differ
===============================
**The golden fixture half always runs.** It needs nothing beyond ``json``, ``pathlib`` and
``pytest``, so it stays executable in an environment that cannot even build the application,
and it asserts the exact, committed shape of ``tests/fixtures/expected_cucumber_report.json``
-- one feature, six elements, nineteen steps, canonical serialization included.

**The live half degrades to a skip.** ``target/cucumber.json`` exists only after a real run,
and the documented default selector ``-m "LogOut"`` -- the port of ``tags = "@LogOut"``
``[README.md:L87]`` -- selects no scenario at all, so the ordinary state of that artifact is
an empty array. Every live assertion therefore skips with an explicit reason when the
artifact is absent, when the application package cannot be imported, or when the document
holds no feature to assert about; a vacuous pass would be evidence of nothing.

Key sets and structural invariants only
=======================================
The golden fixture deliberately records no ``Background`` element and no prepended
``Background`` step, while a live run's elements do carry that step. Step counts, step
ordering and document size are therefore **never** compared between a live report and the
fixture -- only key sets and structural invariants are, which is what keeps the schema
verdict stable across serial and parallel runs. A parallel run's document is larger than,
but structurally identical to, the serial one, and its tag order is not even deterministic:
the parser stores tags in a set.

For the same reason ``duration`` is asserted by **type** only. The values are integer
nanoseconds and are deliberately non-uniform across steps, so that no reader mistakes them
for a fixed constant; no specific duration value appears anywhere in this module.

Preserved defects -- asserted here, never repaired
==================================================
Defects are behavior. Two are directly visible in this artifact and one shapes the whole
module:

* **D9** -- the second and third scenario outlines parse to the *identical* name, because the
  missing space after the third outline's colon separates the keyword and nothing else. The
  generated test function is derived from the scenario name, so the later definition silently
  replaces the earlier one and ``UPGN-287`` never runs. The fixture therefore records six
  elements, five of which share one name, and ``UPGN-287`` appears nowhere at all. **Every
  assertion about those elements keys on ``tags`` or ``id``, never on ``name``**: five
  elements answer to the same name, so a name-keyed lookup would silently match the wrong
  element and pass while proving nothing. The one-line repair is recorded in
  ``docs/migration-parity.md`` as a decision for the owners and is deliberately not applied.
* **D1** -- the first outline binds no ``Examples`` table, so its steps are recorded with the
  literal ``<username>`` / ``<password>`` placeholder text and they pass. Step names are
  never interpolated, rendered, pattern-checked or rewritten below.
* **D4** -- the third outline feeds the *password* column into the *username* step, so the
  report records ``salesmanager`` / ``posmanager`` where an address would be expected. No
  column is swapped back.

Deliberately out of scope
=========================
Each item below is owned by exactly one other module, and duplicating it here would create
two places that could disagree:

* the rerun manifest's contents -- criterion V9, ``tests/integration/test_rerun_manifest.py``.
  Only the fixture's own zero-failure property is asserted here, as a fixture invariant.
* collection counts and generated test identifiers -- criterion V3,
  ``tests/parity/test_defect_preservation.py``. This module asserts the report artifact's
  shape, not what pytest collected.
* the ``target/`` artifact layout, including that the tree is created before a run because
  the Cucumber JSON writer does not create the directory of the file it writes -- criterion
  V7, ``tests/unit/test_paths.py`` and ``tests/integration/test_run_flow.py``.
* the six report-publisher thresholds, the report sort order and the include glob
  ``[Jenkins:L15]`` -- criterion V4, ``tests/unit/test_thresholds_parity.py``.
* marker registration and warning hygiene -- criterion V10, ``tests/unit/test_markers.py``.
  This module only explains *why* the ``@`` is absent; it registers nothing.
* the HTTP report endpoints -- criterion V11, ``tests/integration/test_report_endpoints.py``.

Nothing here starts a browser, runs the BDD suite, launches a subprocess, or writes anywhere
outside ``tmp_path``. The application under test is external and unreachable from CI, which
is precisely why the evidence of parity is structural: artifact production, schema
conformance and constant equality.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, TypeGuard

import pytest

if TYPE_CHECKING:
    # Annotation-only imports. `from __future__ import annotations` keeps every annotation a
    # string, so this block never executes at run time and the module stays importable on a
    # bare interpreter while `mypy` still sees the real types.
    from collections.abc import Mapping, Sequence

# =============================================================================
# The golden fixture's location.
#
# Assembled from the repository root supplied by the session-scoped `project_root`
# fixture, never from the process working directory. The run is parallel by default and
# every worker is a separate process, so a working-directory-relative path would be a
# latent flake rather than a shortcut. The components are kept separate so the path is
# assembled with `pathlib` on any platform.
# =============================================================================

FIXTURE_PARTS: Final[tuple[str, ...]] = ("tests", "fixtures", "expected_cucumber_report.json")
"""Repository-root-relative components of the golden Cucumber JSON fixture."""


# =============================================================================
# The frozen report schema.
#
# Restated here independently of `app/reporting/cucumber_json.py` on purpose. Importing
# the application's constants and then comparing the fixture against them would prove only
# that the fixture matches whatever the application currently declares. Declaring them
# here makes the two statements independent, and `TestReportingAdapterAgreement` then
# asserts that they agree -- which is a real check rather than a tautology.
#
# Configuration values are data, not decisions: nothing below is renamed, reordered or
# modernised relative to the schema the source toolchain emitted.
# =============================================================================

FEATURE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "description",
        "elements",
        "id",
        "keyword",
        "language",
        "line",
        "name",
        "tags",
        "uri",
    }
)
"""The nine keys every feature object carries, and the only nine."""

SCENARIO_KEYS: Final[frozenset[str]] = frozenset(
    {
        "description",
        "id",
        "keyword",
        "line",
        "name",
        "steps",
        "tags",
        "type",
    }
)
"""The eight keys every member of a feature's ``elements`` array carries."""

STEP_KEYS: Final[frozenset[str]] = frozenset(
    {
        "keyword",
        "line",
        "match",
        "name",
        "result",
    }
)
"""The five keys every member of a scenario's ``steps`` array carries."""

RESULT_KEYS: Final[frozenset[str]] = frozenset({"status", "duration"})
"""The two keys every step ``result`` sub-object must carry."""

OPTIONAL_RESULT_KEYS: Final[frozenset[str]] = frozenset({"error_message"})
"""Keys a ``result`` MAY carry in addition to :data:`RESULT_KEYS`.

The writer attaches ``error_message`` to the result of a failed step -- an empty string for
every failure after a scenario's first. Recognising it as an optional is what stops an
ordinary failing run from being reported as off-schema, which would in turn starve the rerun
manifest of the only data it is derived from. It is optional rather than required because a
wholly passing run never contains it, and the golden fixture -- every step of which passed --
does not.
"""

MATCH_KEYS: Final[frozenset[str]] = frozenset({"location"})
"""The single key a step's ``match`` sub-object carries.

Declared here because the reporting adapter deliberately does not freeze this level: it
type-checks ``match`` as an object and stops there. The value of ``location`` is asserted to
be a string and nothing more -- the live writer emits an empty one, while the golden fixture
records the step-definition module -- so requiring it to be non-empty would fail a perfectly
ordinary live report.
"""

TAG_PREFIX: Final[str] = "@"
"""The Gherkin tag prefix, which a report must NOT contain.

Never prepend this to a tag value. It exists so that the check for its absence has a name.
"""

TAG_NAME_KEY: Final[str] = "name"
"""The key carrying the tag value when a tag entry is an object rather than a string."""


# =============================================================================
# The golden fixture's invariants.
#
# Every value below was read out of the committed fixture and cross-checked against the
# Gherkin block documented in `README.md`, whose first line is the feature's `@Login` tag.
# Counting from there gives the line numbers directly: the `Feature:` keyword on line 2,
# the first outline on line 13 with its four steps on 14-17, the second outline on 21, the
# third on 29 with its three steps on 30-32, the SalesManager rows on 37-39 and the
# PosManager rows on 44-45.
#
# These are FIXTURE invariants. They are never asserted against a live run, whose element
# and step counts legitimately differ.
# =============================================================================

EXPECTED_FEATURE_COUNT: Final[int] = 1
"""The fixture records exactly one feature: the documented specification has exactly one."""

EXPECTED_ELEMENT_COUNT: Final[int] = 6
"""Six elements: one for the first outline plus five ``Examples`` rows of the third.

The second outline contributes nothing, which is defect D9 rendered as a number.
"""

EXPECTED_STEP_COUNT: Final[int] = 19
"""Nineteen steps: four for the first element plus three for each of the five others."""

FEATURE_LINE: Final[int] = 2
"""The ``Feature:`` keyword's line, the tag line above it being line 1."""

FEATURE_URI: Final[str] = "tests/features/login.feature"
"""The materialised specification's repository-relative location, as the report records it."""

FEATURE_LANGUAGE: Final[str] = "en"
"""The Gherkin dialect the specification is written in."""

FEATURE_NAME: Final[str] = "Testinium app login feature"
"""The feature's name, from ``[README.md:L105]``."""

FEATURE_KEYWORD: Final[str] = "Feature"
"""The feature's Gherkin keyword."""

FEATURE_ID: Final[str] = "testinium-app-login-feature"
"""The feature's slug identifier."""

ELEMENT_KEYWORD: Final[str] = "Scenario Outline"
"""Every element derives from a ``Scenario Outline``; none is a plain ``Scenario``."""

ELEMENT_TYPE: Final[str] = "scenario"
"""Every element is a scenario. The fixture records no ``background`` element."""

ELEMENT_LINES: Final[frozenset[int]] = frozenset({13, 37, 38, 39, 44, 45})
"""The six element lines: the first outline, then the five ``Examples`` data rows."""

VALID_CREDENTIALS_STEP_LINES: Final[tuple[int, ...]] = (14, 15, 16, 17)
"""The four step lines of the element tagged :data:`TAG_VALID_CREDENTIALS`."""

EMPTY_FIELD_STEP_LINES: Final[tuple[int, ...]] = (30, 31, 32)
"""The three step lines shared by every element tagged :data:`TAG_EMPTY_FIELD`.

All five parametrisations of the third outline report the same three source lines, because
they are five data rows of one outline rather than five separate outlines.
"""

STATUS_PASSED: Final[str] = "passed"
"""The only step status the golden fixture records."""

STEP_MATCH_LOCATION: Final[str] = "tests/step_defs/login_sd.py"
"""The step-definition module every fixture step is matched against."""

TAG_FEATURE: Final[str] = "Login"
"""The feature-level tag, ``@`` already stripped, from ``[README.md:L104]``."""

TAG_VALID_CREDENTIALS: Final[str] = "UPGN-286"
"""Jira tag of the first outline, ``[README.md:L115]``. Exactly one element carries it."""

TAG_UNREACHABLE: Final[str] = "UPGN-287"
"""Jira tag of the second outline, ``[README.md:L123]``.

It appears nowhere in the report. Its scenario name collides with the third outline's, so the
generated test function is overwritten and the scenario never runs -- defect D9.
"""

TAG_EMPTY_FIELD: Final[str] = "UPGN-288"
"""Jira tag of the third outline, ``[README.md:L131]``. Five elements carry it."""

TAG_SALES_MANAGER: Final[str] = "SalesManager"
"""``Examples``-table tag of the three-row SalesManager data set, ``[README.md:L137]``."""

TAG_POS_MANAGER: Final[str] = "PosManager"
"""``Examples``-table tag of the two-row PosManager data set, ``[README.md:L144]``."""

EXPECTED_ELEMENT_TAGS: Final[frozenset[str]] = frozenset(
    {
        TAG_VALID_CREDENTIALS,
        TAG_EMPTY_FIELD,
        TAG_SALES_MANAGER,
        TAG_POS_MANAGER,
    }
)
"""Every tag value the fixture's elements carry between them."""

EXPECTED_TAG_ELEMENT_COUNTS: Final[Mapping[str, int]] = MappingProxyType(
    {
        TAG_VALID_CREDENTIALS: 1,
        TAG_EMPTY_FIELD: 5,
        TAG_SALES_MANAGER: 3,
        TAG_POS_MANAGER: 2,
    }
)
"""How many elements carry each element-level tag.

One for the first outline; five for the third, of which three come from the SalesManager
table and two from the PosManager table. The second outline contributes no entry at all.
"""

NAME_VALID_CREDENTIALS: Final[str] = "Users log in with valid credentials"
"""The first outline's name, and the only element name that is not shared."""

NAME_COLLIDED: Final[str] = "Users log in with invalid email or invalid password credentials"
"""The name the second and third outlines both parse to -- the mechanism of defect D9.

Five elements answer to it. It is used below only to *count* names, never to look an element
up: keying a lookup on it would silently select whichever of the five came first.
"""

EXPECTED_NAME_COUNTS: Final[Mapping[str, int]] = MappingProxyType(
    {
        NAME_VALID_CREDENTIALS: 1,
        NAME_COLLIDED: 5,
    }
)
"""The multiset of element names, which is defect D9 stated as data."""


# =============================================================================
# Narrowing helpers.
#
# A decoded JSON payload is a tree of `object`, so every value has to be narrowed before it
# is interpreted. These helpers narrow and fail the calling test with a precise message if
# the shape is wrong, which keeps the tests themselves free of isinstance noise.
#
# They are used by the tests only. The reusable validator further below never asserts: it
# reports findings, because it must be total over arbitrary input.
# =============================================================================


def as_json_object(value: object, what: str) -> Mapping[str, object]:
    """Narrow a decoded JSON value to an object.

    Args:
        value: The decoded value.
        what: How to name the value in the failure message.

    Returns:
        The value as a read-only mapping.
    """
    assert isinstance(value, dict), f"{what} must be a JSON object, found {type(value).__name__}"
    return value


def as_json_array(value: object, what: str) -> Sequence[object]:
    """Narrow a decoded JSON value to an array.

    Args:
        value: The decoded value.
        what: How to name the value in the failure message.

    Returns:
        The value as a read-only sequence.
    """
    assert isinstance(value, list), f"{what} must be a JSON array, found {type(value).__name__}"
    return value


def as_json_string(value: object, what: str) -> str:
    """Narrow a decoded JSON value to a string.

    Args:
        value: The decoded value.
        what: How to name the value in the failure message.

    Returns:
        The value as a string.
    """
    assert isinstance(value, str), f"{what} must be a JSON string, found {type(value).__name__}"
    return value


def is_json_int(value: object) -> TypeGuard[int]:
    """Return whether *value* is a JSON integer.

    ``bool`` is excluded explicitly. It subclasses ``int`` in Python, so a JSON ``true``
    would otherwise satisfy every integer check in this module and a line number of ``true``
    would pass unnoticed.

    Declared as a type guard so that a caller which asserts on it also narrows the value,
    rather than having to repeat the ``isinstance`` check for the type checker's benefit.

    Args:
        value: The decoded value.

    Returns:
        ``True`` for an integer that is not a boolean.
    """
    return isinstance(value, int) and not isinstance(value, bool)


def elements_of(feature: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    """Return a feature's ``elements``, each narrowed to an object.

    Args:
        feature: A feature object.

    Returns:
        The elements, in the order the document lists them. Order is preserved rather than
        sorted: the fixture's order is part of what is asserted, and nothing is
        de-duplicated, because five elements legitimately share one name (defect D9).
    """
    raw = as_json_array(feature.get("elements"), "feature 'elements'")
    return tuple(
        as_json_object(element, f"element at index {index}") for index, element in enumerate(raw)
    )


def steps_of(element: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    """Return an element's ``steps``, each narrowed to an object.

    Args:
        element: A scenario element.

    Returns:
        The steps, in document order. Step order is never sorted: Given / When / Then is
        semantic.
    """
    raw = as_json_array(element.get("steps"), "element 'steps'")
    return tuple(as_json_object(step, f"step at index {index}") for index, step in enumerate(raw))


def step_lines_of(element: Mapping[str, object]) -> tuple[int, ...]:
    """Return the source line of each of an element's steps, in document order.

    Args:
        element: A scenario element.

    Returns:
        The step lines.
    """
    lines: list[int] = []
    for index, step in enumerate(steps_of(element)):
        line = step.get("line")
        assert is_json_int(line), f"step {index} 'line' must be an integer, found {line!r}"
        lines.append(line)
    return tuple(lines)


def tag_values(owner: object) -> tuple[str, ...]:
    """Extract tag values from a feature, an element, or a bare ``tags`` array.

    Both shapes a report producer may use are accepted, because both genuinely occur: the
    source toolchain wrote bare strings, which is what the golden fixture records, while the
    ported writer wraps each tag in an object carrying a ``name`` and the line it was
    declared on. Values are returned exactly as they appear -- in document order, hyphens
    intact, with no prefix added or removed -- so that the caller can assert on them.

    An entry carrying no usable value is skipped rather than raising, which keeps this helper
    usable on a document that has already been found off-schema.

    Args:
        owner: A feature or element mapping, whose ``tags`` key is read, or the ``tags``
            array itself.

    Returns:
        The tag values, duplicates preserved.
    """
    raw: object = owner.get("tags") if isinstance(owner, dict) else owner
    if not isinstance(raw, list):
        return ()
    values: list[str] = []
    for entry in raw:
        if isinstance(entry, dict):
            name = entry.get(TAG_NAME_KEY)
            if isinstance(name, str):
                values.append(name)
        elif isinstance(entry, str):
            values.append(entry)
    return tuple(values)


def all_tag_values(document: Sequence[object]) -> tuple[str, ...]:
    """Collect every tag value in a document, at every level.

    Args:
        document: The decoded top-level array.

    Returns:
        Every tag value found on a feature or on any of its elements, duplicates preserved.
    """
    values: list[str] = []
    for feature in document:
        if not isinstance(feature, dict):
            continue
        values.extend(tag_values(feature))
        elements = feature.get("elements")
        if not isinstance(elements, list):
            continue
        for element in elements:
            if isinstance(element, dict):
                values.extend(tag_values(element))
    return tuple(values)


# =============================================================================
# Tag-keyed and id-keyed element selection.
#
# The ONLY sanctioned way to reach an element in this module. Five of the six elements share
# one name because the second and third scenario outlines parse to the identical name
# (defect D9), so a name-keyed lookup silently returns whichever of the five happens to come
# first: it compiles, it runs green, and it proves nothing at all. Selecting by tag or by id
# is what makes every assertion about a specific element actually about that element.
# =============================================================================


def elements_tagged(
    elements: Sequence[Mapping[str, object]], tag: str
) -> tuple[Mapping[str, object], ...]:
    """Return the elements carrying *tag*, in document order.

    Args:
        elements: The elements to filter.
        tag: The tag value, with no leading ``@`` -- the report never records one.

    Returns:
        Every matching element. Empty when none carries the tag, which is itself an assertable
        outcome: it is how the absence of the unreachable scenario is proved.
    """
    return tuple(element for element in elements if tag in tag_values(element))


def single_element_tagged(
    elements: Sequence[Mapping[str, object]], tag: str
) -> Mapping[str, object]:
    """Return the one element carrying *tag*, failing the test if there is not exactly one.

    Args:
        elements: The elements to search.
        tag: The tag value, with no leading ``@``.

    Returns:
        The single matching element.
    """
    matches = elements_tagged(elements, tag)
    assert len(matches) == 1, f"expected exactly one element tagged {tag!r}, found {len(matches)}"
    return matches[0]


def element_ids(elements: Sequence[Mapping[str, object]]) -> tuple[str, ...]:
    """Return each element's ``id``, in document order.

    Args:
        elements: The elements to read.

    Returns:
        The identifiers.
    """
    return tuple(
        as_json_string(element.get("id"), f"element {index} 'id'")
        for index, element in enumerate(elements)
    )


def element_names(elements: Sequence[Mapping[str, object]]) -> tuple[str, ...]:
    """Return each element's ``name``, in document order.

    Used to *count* names -- which is how defect D9 is measured -- never to look an element up.

    Args:
        elements: The elements to read.

    Returns:
        The names, duplicates preserved, because the duplication is the point.
    """
    return tuple(
        as_json_string(element.get("name"), f"element {index} 'name'")
        for index, element in enumerate(elements)
    )


# =============================================================================
# The reusable recursive validator: document -> feature -> element -> step -> result.
#
# One implementation validates the golden fixture AND any live report, so the invariant is
# stated in exactly one place and the two halves of this module cannot drift apart.
#
# It reports KEY SETS and STRUCTURAL INVARIANTS only, and deliberately nothing else:
#
#   * no count of features, elements or steps -- a live run legitimately carries a
#     `Background` step the fixture omits, and a parallel run's document is larger than a
#     serial one's;
#   * no ordering -- the parser stores tags in a set, so even tag order varies between runs;
#   * no `duration` value -- durations are integer nanoseconds and deliberately non-uniform,
#     so only the type is checked;
#   * no inspection of a step `name` beyond its type -- names legitimately hold literal
#     `<username>` placeholders (defect D1) and a password value in a username step
#     (defect D4), and pattern-checking either would turn a preserved defect into a
#     spurious schema error;
#   * no inspection of `description` -- a producer may legitimately emit null there, and the
#     reporting adapter takes the same deliberate stance, so the two cannot disagree.
#
# The function is total: it never raises, for any input, so it can be pointed at an
# arbitrary decoded payload and still return a verdict.
# =============================================================================


def key_set_findings(
    obj: Mapping[str, object],
    required: frozenset[str],
    optional: frozenset[str],
    location: str,
) -> list[str]:
    """Compare one object's key set against the frozen schema.

    Args:
        obj: The object to inspect.
        required: Keys that must all be present.
        optional: Keys that may be present in addition.
        location: A JSON-path-like pointer used in the findings.

    Returns:
        Zero, one or two findings: one for absent required keys, one for keys the schema does
        not define. Both lists are sorted, so a finding is deterministic.
    """
    present = frozenset(obj)
    findings: list[str] = []
    missing = sorted(required - present)
    if missing:
        findings.append(f"{location}: missing key(s) {missing}")
    unexpected = sorted(present - required - optional)
    if unexpected:
        findings.append(f"{location}: unexpected key(s) {unexpected}")
    return findings


def tag_findings(owner: Mapping[str, object], location: str) -> list[str]:
    """Validate one object's ``tags`` array.

    Every value must be usable and must NOT carry the leading ``@``: the parser strips the
    prefix while reading the feature file, exactly as the source toolchain's writer did, so a
    prefixed value means the document came from something else -- or that a consumer put the
    prefix back, which nothing in this project may do.

    Args:
        owner: A feature or element object.
        location: A JSON-path-like pointer used in the findings.

    Returns:
        The findings, in document order.
    """
    findings: list[str] = []
    raw = owner.get("tags")
    if not isinstance(raw, list):
        findings.append(f"{location}.tags: must be a JSON array, found {type(raw).__name__}")
        return findings
    for index, entry in enumerate(raw):
        pointer = f"{location}.tags[{index}]"
        if isinstance(entry, str):
            value = entry
        elif isinstance(entry, dict):
            name = entry.get(TAG_NAME_KEY)
            if not isinstance(name, str):
                findings.append(
                    f"{pointer}: tag object must carry a string {TAG_NAME_KEY!r}, "
                    f"found {type(name).__name__}"
                )
                continue
            value = name
        else:
            findings.append(
                f"{pointer}: tag must be a string or an object carrying "
                f"{TAG_NAME_KEY!r}, found {type(entry).__name__}"
            )
            continue
        if not value:
            findings.append(f"{pointer}: tag value must not be empty")
        elif value.startswith(TAG_PREFIX):
            findings.append(f"{pointer}: tag value {value!r} must not carry the {TAG_PREFIX!r}")
    return findings


def result_findings(step: Mapping[str, object], location: str) -> list[str]:
    """Validate one step's ``result`` sub-object.

    Args:
        step: The step object.
        location: A JSON-path-like pointer to the step used in the findings.

    Returns:
        The findings, in document order.
    """
    pointer = f"{location}.result"
    result = step.get("result")
    if not isinstance(result, dict):
        return [f"{pointer}: must be a JSON object, found {type(result).__name__}"]
    findings = key_set_findings(result, RESULT_KEYS, OPTIONAL_RESULT_KEYS, pointer)
    status = result.get("status")
    if not isinstance(status, str) or not status:
        findings.append(f"{pointer}.status: must be a non-empty string, found {status!r}")
    duration = result.get("duration")
    if not is_json_int(duration):
        findings.append(
            f"{pointer}.duration: must be an integer nanosecond count, "
            f"found {type(duration).__name__}"
        )
    elif duration < 0:
        findings.append(f"{pointer}.duration: must not be negative")
    if "error_message" in result and not isinstance(result["error_message"], str):
        findings.append(
            f"{pointer}.error_message: must be a string, "
            f"found {type(result['error_message']).__name__}"
        )
    return findings


def step_findings(step: object, location: str) -> list[str]:
    """Validate one member of a scenario's ``steps`` array.

    Args:
        step: The candidate step.
        location: A JSON-path-like pointer used in the findings.

    Returns:
        The findings, in document order.
    """
    if not isinstance(step, dict):
        return [f"{location}: step must be a JSON object, found {type(step).__name__}"]
    findings = key_set_findings(step, STEP_KEYS, frozenset(), location)
    keyword = step.get("keyword")
    if not isinstance(keyword, str) or not keyword:
        findings.append(f"{location}.keyword: must be a non-empty string, found {keyword!r}")
    if not isinstance(step.get("name"), str):
        findings.append(
            f"{location}.name: must be a string, found {type(step.get('name')).__name__}"
        )
    if not is_json_int(step.get("line")):
        findings.append(
            f"{location}.line: must be an integer, found {type(step.get('line')).__name__}"
        )
    match = step.get("match")
    if not isinstance(match, dict):
        findings.append(
            f"{location}.match: must be a JSON object, found {type(match).__name__}",
        )
    else:
        findings.extend(key_set_findings(match, MATCH_KEYS, frozenset(), f"{location}.match"))
        if "location" in match and not isinstance(match["location"], str):
            findings.append(
                f"{location}.match.location: must be a string, "
                f"found {type(match['location']).__name__}"
            )
    findings.extend(result_findings(step, location))
    return findings


def element_findings(element: object, location: str) -> list[str]:
    """Validate one member of a feature's ``elements`` array.

    The same key set applies to a ``background`` element as to a ``scenario`` element: both
    carry ``type``, which is what tells them apart. Validation is therefore correct for
    either, and no element is ever rejected, reordered or de-duplicated for sharing a name
    with another (defect D9).

    Args:
        element: The candidate element.
        location: A JSON-path-like pointer used in the findings.

    Returns:
        The findings, in document order.
    """
    if not isinstance(element, dict):
        return [f"{location}: element must be a JSON object, found {type(element).__name__}"]
    findings = key_set_findings(element, SCENARIO_KEYS, frozenset(), location)
    for key in ("id", "keyword", "type"):
        value = element.get(key)
        if not isinstance(value, str) or not value:
            findings.append(f"{location}.{key}: must be a non-empty string, found {value!r}")
    if not isinstance(element.get("name"), str):
        findings.append(
            f"{location}.name: must be a string, found {type(element.get('name')).__name__}"
        )
    if not is_json_int(element.get("line")):
        findings.append(
            f"{location}.line: must be an integer, found {type(element.get('line')).__name__}"
        )
    findings.extend(tag_findings(element, location))
    steps = element.get("steps")
    if not isinstance(steps, list):
        findings.append(f"{location}.steps: must be a JSON array, found {type(steps).__name__}")
    else:
        for index, step in enumerate(steps):
            findings.extend(step_findings(step, f"{location}.steps[{index}]"))
    return findings


def feature_findings(feature: object, location: str) -> list[str]:
    """Validate one feature object of the top-level array.

    Args:
        feature: The candidate feature.
        location: A JSON-path-like pointer used in the findings.

    Returns:
        The findings, in document order.
    """
    if not isinstance(feature, dict):
        return [f"{location}: feature must be a JSON object, found {type(feature).__name__}"]
    findings = key_set_findings(feature, FEATURE_KEYS, frozenset(), location)
    for key in ("id", "keyword", "language", "name", "uri"):
        value = feature.get(key)
        if not isinstance(value, str) or not value:
            findings.append(f"{location}.{key}: must be a non-empty string, found {value!r}")
    if not is_json_int(feature.get("line")):
        findings.append(
            f"{location}.line: must be an integer, found {type(feature.get('line')).__name__}"
        )
    findings.extend(tag_findings(feature, location))
    elements = feature.get("elements")
    if not isinstance(elements, list):
        findings.append(
            f"{location}.elements: must be a JSON array, found {type(elements).__name__}"
        )
    else:
        for index, element in enumerate(elements):
            findings.extend(element_findings(element, f"{location}.elements[{index}]"))
    return findings


def collect_schema_findings(document: object) -> tuple[str, ...]:
    """Validate a decoded Cucumber JSON document against the frozen schema.

    The single entry point of this module's validator, used by both the golden-fixture
    assertions and the live-report assertions so that the invariant exists in one place only.

    Args:
        document: Any decoded JSON payload, normally the ``list`` that ``json.loads``
            returns. Any other object is accepted and reported on rather than raising.

    Returns:
        Every finding, in document order. An empty tuple means the document conforms.
    """
    if not isinstance(document, list):
        return (f"$: document must be a JSON array of features, found {type(document).__name__}",)
    findings: list[str] = []
    for index, feature in enumerate(document):
        findings.extend(feature_findings(feature, f"[{index}]"))
    return tuple(findings)


# =============================================================================
# Guards for everything that reaches beyond the standard library.
#
# Importing any `app.*` module first executes `app/__init__.py`, which IS the Flask
# application factory, so every application import transitively needs Flask installed. The
# golden-fixture half of this module must stay runnable where it is not -- a verification
# environment is not guaranteed to have network access -- so no application import happens
# at module level and every test that needs one skips with a stated reason instead of
# erroring during collection.
# =============================================================================


def skip_unless_reporting_adapter_importable() -> None:
    """Skip the calling test unless the reporting adapter and the path module can be imported.

    Both modules are imported here rather than merely probed, because importability is
    exactly the property in question: a probe that only located the file would still leave a
    later import free to fail inside the test.
    """
    try:
        import app.reporting.cucumber_json  # noqa: F401
        import app.utils.paths  # noqa: F401
    except ImportError as error:
        pytest.skip(
            "the application package is not importable in this environment, so the reporting "
            "adapter cannot be exercised; the golden-fixture assertions in this module cover "
            f"the schema without it ({type(error).__name__}: {error})"
        )


def configured_report_path(project_root: Path) -> Path | None:
    """Return the configured location of the live Cucumber JSON artifact.

    The location is never spelled out here. It comes from ``app.utils.paths``, the single
    module that knows the artifact-root name, so this suite cannot be the reason a rename goes
    unnoticed. The configured value is deliberately relative -- it renders as
    ``target/cucumber.json``, character for character what the test configuration and the
    pipeline publisher use -- and is therefore anchored to the repository root here. Leaving
    it relative would resolve it against each parallel worker's working directory, which is a
    latent flake rather than a shortcut.

    Args:
        project_root: The absolute repository root.

    Returns:
        The absolute artifact location, or ``None`` when the application package cannot be
        imported or does not publish the constant. ``None`` is returned rather than a
        hard-coded fallback: fabricating the path would defeat the point of asking the module
        that owns it.
    """
    try:
        from app.utils.paths import CUCUMBER_JSON_PATH
    except ImportError:
        return None
    configured = CUCUMBER_JSON_PATH
    if not isinstance(configured, Path):
        return None
    return configured if configured.is_absolute() else project_root / configured


# =============================================================================
# Fixtures.
#
# Function-scoped on purpose. The fixture file is under ten kilobytes, so re-reading it per
# test costs nothing measurable, while a session-scoped decoded document would be shared
# mutable state across every test in the module -- and, under the parallel default, across
# every test in a worker.
#
# `project_root` comes from `tests/conftest.py` and is derived from that file's own location,
# never from the working directory. There is deliberately no `conftest.py` beside this
# module and there must not be one.
# =============================================================================


@pytest.fixture
def golden_report_path(project_root: Path) -> Path:
    """Return the absolute location of the golden Cucumber JSON fixture."""
    return project_root.joinpath(*FIXTURE_PARTS)


@pytest.fixture
def golden_bytes(golden_report_path: Path) -> bytes:
    """Return the golden fixture's raw bytes.

    Read as bytes rather than text so that the encoding, the line endings and the trailing
    newline can all be asserted on exactly, without a text-mode translation quietly hiding
    any of them.
    """
    assert golden_report_path.is_file(), (
        f"the golden Cucumber JSON fixture is missing at {golden_report_path}; it is a "
        "committed artifact and criterion V5 has nothing to assert against without it"
    )
    return golden_report_path.read_bytes()


@pytest.fixture
def golden_document(golden_bytes: bytes) -> Sequence[object]:
    """Return the decoded golden fixture, narrowed to the top-level array."""
    decoded: object = json.loads(golden_bytes.decode("utf-8"))
    return as_json_array(decoded, "the golden fixture document")


@pytest.fixture
def golden_feature(golden_document: Sequence[object]) -> Mapping[str, object]:
    """Return the golden fixture's single feature object."""
    assert len(golden_document) == EXPECTED_FEATURE_COUNT, (
        f"the golden fixture must record exactly {EXPECTED_FEATURE_COUNT} feature, "
        f"found {len(golden_document)}"
    )
    return as_json_object(golden_document[0], "the golden fixture feature")


@pytest.fixture
def golden_elements(golden_feature: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    """Return the golden fixture's element objects, in document order."""
    return elements_of(golden_feature)


@pytest.fixture
def golden_steps(
    golden_elements: tuple[Mapping[str, object], ...],
) -> tuple[Mapping[str, object], ...]:
    """Return every step of every element of the golden fixture, in document order."""
    return tuple(step for element in golden_elements for step in steps_of(element))


@pytest.fixture
def live_report_path(project_root: Path) -> Path:
    """Return the live ``target/cucumber.json`` artifact, or skip with a stated reason.

    Three distinct reasons to skip, each reported separately so that a skipped run says which
    one applied:

    * the application package is not importable, so the configured location is unknown;
    * the configured location is known but the artifact has not been produced -- the ordinary
      state of a fresh checkout, since the artifact root is wiped before every run and is
      created empty;
    * the artifact exists but is empty on disk, which no writer produces and which would make
      a decode failure look like a schema violation.
    """
    configured = configured_report_path(project_root)
    if configured is None:
        pytest.skip(
            "the application package is not importable in this environment, so the configured "
            "location of the live Cucumber JSON artifact cannot be resolved; it is never "
            "hard-coded here, because app/utils/paths.py is the single owner of the layout"
        )
    if not configured.is_file():
        pytest.skip(
            f"the live Cucumber JSON artifact has not been produced at {configured}; it is "
            "written only at the end of a real run, and the artifact root is wiped before "
            "every one, so its absence is the ordinary state rather than a failure"
        )
    if configured.stat().st_size == 0:
        pytest.skip(
            f"the live Cucumber JSON artifact at {configured} is zero bytes, which no writer "
            "produces; it is being written concurrently or was truncated, and reading it now "
            "would report a decode failure as a schema violation"
        )
    return configured


# =============================================================================
# A synthesised document in the LIVE writer's shape.
#
# The golden fixture is not the only conformant shape, and pinning the validator to it would
# make the live half fail for reasons that are not defects. A live document differs from the
# fixture in four ways that all matter, and every one of them is represented below:
#
#   1. tag entries are OBJECTS carrying a `name` and the line the tag was declared on,
#      whereas the fixture records bare strings. Both shapes are conformant.
#   2. a scenario's steps include the `Background` step, which the fixture omits entirely --
#      which is exactly why step counts are never compared across the two.
#   3. `match.location` is an empty string, whereas the fixture records the step-definition
#      module.
#   4. a failed step's `result` carries `error_message` in addition to the two required keys.
#
# Two features are present, so multi-feature documents are covered as well. Every duration is
# a different integer, so no reader can mistake one for a constant.
# =============================================================================


def live_shaped_document() -> list[Any]:
    """Build a conformant document in the shape the ported writer actually emits.

    A fresh object graph is returned on every call, so a test may mutate it freely to build a
    negative case without affecting any other test.

    The return type is deliberately loose. A decoded JSON document is a tree of ``object``, and
    typing it as such would force every negative case below to narrow four levels of nesting
    before it could change one value -- which would bury the single mutation each case exists
    to make. The validator under test accepts ``object`` and narrows everything itself, so no
    type safety is lost where it matters.

    Returns:
        A two-feature document that :func:`collect_schema_findings` must accept.
    """
    return [
        {
            "description": "  A synthesised feature, in the shape the live writer emits.",
            "elements": [
                {
                    "description": "",
                    "id": "test_cukes_runner.py::test_users_log_in_with_valid_credentials",
                    "keyword": "Scenario Outline",
                    "line": 13,
                    "name": "Users log in with valid credentials",
                    "steps": [
                        {
                            "keyword": "Given",
                            "line": 9,
                            "match": {"location": ""},
                            "name": "User is on the Testinium login page",
                            "result": {"duration": 12345678, "status": STATUS_PASSED},
                        },
                        {
                            "keyword": "When",
                            "line": 14,
                            "match": {"location": ""},
                            "name": 'User enters "<username>" username',
                            "result": {"duration": 23456789, "status": STATUS_PASSED},
                        },
                        {
                            "keyword": "Then",
                            "line": 17,
                            "match": {"location": ""},
                            "name": "User should see the dashboard",
                            "result": {
                                "duration": 34567891,
                                "error_message": "AssertionError: dashboard not visible",
                                "status": "failed",
                            },
                        },
                    ],
                    "tags": [{"line": 12, "name": TAG_VALID_CREDENTIALS}],
                    "type": ELEMENT_TYPE,
                },
                {
                    "description": "",
                    "id": "test_cukes_runner.py::test_background_only",
                    "keyword": "Background",
                    "line": 8,
                    "name": "For the scenarios in the feature file",
                    "steps": [
                        {
                            "keyword": "Given",
                            "line": 9,
                            "match": {"location": ""},
                            "name": "User is on the Testinium login page",
                            "result": {"duration": 45678912, "status": STATUS_PASSED},
                        }
                    ],
                    "tags": [],
                    "type": "background",
                },
            ],
            "id": FEATURE_URI,
            "keyword": FEATURE_KEYWORD,
            "language": FEATURE_LANGUAGE,
            "line": FEATURE_LINE,
            "name": FEATURE_NAME,
            "tags": [{"line": 1, "name": TAG_FEATURE}],
            "uri": FEATURE_URI,
        },
        {
            "description": "",
            "elements": [],
            "id": "tests/features/second.feature",
            "keyword": FEATURE_KEYWORD,
            "language": FEATURE_LANGUAGE,
            "line": 1,
            "name": "A second feature, so multi-feature documents are covered",
            "tags": [],
            "uri": "tests/features/second.feature",
        },
    ]


# =============================================================================
# The golden fixture as a committed artifact.
#
# Before anything is asserted about the schema, the file itself is pinned: encoding, line
# endings, trailing newline and canonical serialization. That is a cheap, decisive guard
# against hand-editing drift -- a reformatted or partially edited fixture would otherwise
# make every assertion below quietly assert something else.
# =============================================================================


class TestGoldenFixtureArtifact:
    """The committed fixture file's own byte-level properties."""

    def test_fixture_is_utf8_decodable_and_pure_ascii(self, golden_bytes: bytes) -> None:
        """The fixture decodes as UTF-8 and contains no non-ASCII byte.

        Every file this project reads is opened with an explicit UTF-8 encoding, so the
        fixture has to be decodable as UTF-8 on every platform and locale. It is additionally
        pure ASCII: the only non-ASCII characters anywhere in the repository are the two en
        dashes in the report commands documented in ``README.md``, and neither belongs here.
        """
        text = golden_bytes.decode("utf-8")
        assert text.isascii(), "the golden fixture must be pure ASCII"

    def test_fixture_uses_lf_line_endings_and_one_trailing_newline(
        self, golden_bytes: bytes
    ) -> None:
        """The fixture uses LF endings and ends with exactly one newline.

        Line endings are pinned by ``.gitattributes`` so that the comparison is stable on
        every platform; asserting it here is what makes the pin observable rather than
        merely declared.
        """
        assert b"\r" not in golden_bytes, "the golden fixture must use LF line endings only"
        assert golden_bytes.endswith(b"\n"), "the golden fixture must end with a newline"
        assert not golden_bytes.endswith(
            b"\n\n"
        ), "the golden fixture must end with exactly one newline"

    def test_fixture_is_in_canonical_serialized_form(self, golden_bytes: bytes) -> None:
        """The fixture equals its own canonical serialization, byte for byte.

        Canonical form is two-space indentation with keys sorted, plus a single trailing
        newline. Round-tripping the decoded document through that form and comparing bytes
        catches any hand edit that changed the formatting, the key order or the whitespace --
        the failure modes a diff of a nine-kilobyte JSON file is worst at surfacing.
        """
        decoded: object = json.loads(golden_bytes.decode("utf-8"))
        canonical = json.dumps(decoded, indent=2, sort_keys=True) + "\n"
        assert golden_bytes == canonical.encode("utf-8"), (
            "the golden fixture is not in canonical form; regenerate it as "
            "json.dumps(document, indent=2, sort_keys=True) + a single newline"
        )

    def test_fixture_is_a_single_element_top_level_array(
        self, golden_document: Sequence[object]
    ) -> None:
        """The document is a JSON array holding exactly one feature object.

        An array, not an object, and not an envelope carrying a ``features`` key: the source
        toolchain wrote a bare array of features and the ported writer does the same, which
        is why the pipeline's publisher needs no change at all.
        """
        assert isinstance(golden_document, list)
        assert len(golden_document) == EXPECTED_FEATURE_COUNT
        assert isinstance(
            golden_document[0], dict
        ), "element 0 of the document must be the feature object"


# =============================================================================
# Criterion V5, part one: the three frozen key sets, compared for EQUALITY.
#
# Equality rather than containment at every level. A superset would mean the ported writer
# emits something the publisher never saw; a subset would mean a consumer keyed on a key that
# is no longer there.
# =============================================================================


class TestFrozenKeySets:
    """Key-set equality at feature, scenario and step level, plus the two sub-objects."""

    def test_feature_carries_exactly_the_nine_documented_keys(
        self, golden_feature: Mapping[str, object]
    ) -> None:
        """The feature object's key set equals the nine frozen feature keys."""
        assert frozenset(golden_feature) == FEATURE_KEYS

    def test_every_element_carries_exactly_the_eight_documented_keys(
        self, golden_elements: tuple[Mapping[str, object], ...]
    ) -> None:
        """Every element's key set equals the eight frozen scenario keys."""
        for index, element in enumerate(golden_elements):
            assert frozenset(element) == SCENARIO_KEYS, f"element {index} carries the wrong keys"

    def test_every_step_carries_exactly_the_five_documented_keys(
        self, golden_steps: tuple[Mapping[str, object], ...]
    ) -> None:
        """Every step's key set equals the five frozen step keys."""
        for index, step in enumerate(golden_steps):
            assert frozenset(step) == STEP_KEYS, f"step {index} carries the wrong keys"

    def test_every_result_carries_exactly_status_and_duration(
        self, golden_steps: tuple[Mapping[str, object], ...]
    ) -> None:
        """Every step ``result`` carries exactly ``status`` and ``duration``.

        Exact equality is correct *for this fixture* because every step in it passed, and
        ``error_message`` is attached only to a failed step. A live failing run legitimately
        carries the third key, which is why the reusable validator registers it as a
        recognised optional instead.
        """
        for index, step in enumerate(golden_steps):
            result = as_json_object(step.get("result"), f"step {index} 'result'")
            assert frozenset(result) == RESULT_KEYS, f"step {index} result carries the wrong keys"
            assert "error_message" not in result, (
                f"step {index} carries an error message, but every step in the golden fixture "
                "passed"
            )

    def test_every_match_carries_only_location(
        self, golden_steps: tuple[Mapping[str, object], ...]
    ) -> None:
        """Every step ``match`` carries exactly one key, ``location``."""
        for index, step in enumerate(golden_steps):
            match = as_json_object(step.get("match"), f"step {index} 'match'")
            assert frozenset(match) == MATCH_KEYS, f"step {index} match carries the wrong keys"

    def test_schema_levels_are_disjoint_from_each_other_where_it_matters(self) -> None:
        """The three key sets are distinct, and their sizes are the documented nine, eight, five.

        A guard on this module's own constants rather than on the fixture: were two of the
        sets to be written identically by a careless edit, every key-set assertion above would
        still pass while asserting the wrong thing.
        """
        assert len(FEATURE_KEYS) == 9
        assert len(SCENARIO_KEYS) == 8
        assert len(STEP_KEYS) == 5
        assert len(RESULT_KEYS) == 2
        assert len(MATCH_KEYS) == 1
        assert FEATURE_KEYS != SCENARIO_KEYS
        assert SCENARIO_KEYS != STEP_KEYS
        assert FEATURE_KEYS - SCENARIO_KEYS == frozenset({"elements", "language", "uri"})
        assert SCENARIO_KEYS - FEATURE_KEYS == frozenset({"steps", "type"})


# =============================================================================
# Criterion V5, part two: the fixture's structural invariants.
#
# Every value asserted below was read out of the committed fixture and independently
# cross-checked against the Gherkin block documented in `README.md`. These are FIXTURE
# invariants and are never asserted against a live run.
# =============================================================================


class TestFeatureIdentity:
    """The feature object's own values, as the documented specification determines them."""

    def test_feature_line_is_the_documented_keyword_line(
        self, golden_feature: Mapping[str, object]
    ) -> None:
        """The ``Feature:`` keyword sits on line 2, the ``@Login`` tag occupying line 1."""
        assert golden_feature.get("line") == FEATURE_LINE

    def test_feature_uri_is_the_materialised_specification(
        self, golden_feature: Mapping[str, object]
    ) -> None:
        """The feature's ``uri`` is the repository-relative feature-file location.

        It is recorded with forward slashes on every platform, which is what keeps the rerun
        manifest -- derived from this same value -- portable.
        """
        assert golden_feature.get("uri") == FEATURE_URI
        assert "\\" not in as_json_string(golden_feature.get("uri"), "feature 'uri'")

    def test_feature_language_is_the_gherkin_dialect_of_the_specification(
        self, golden_feature: Mapping[str, object]
    ) -> None:
        """The dialect is ``en``, which is what the documented specification is written in."""
        assert golden_feature.get("language") == FEATURE_LANGUAGE

    def test_feature_name_keyword_and_id_are_the_documented_values(
        self, golden_feature: Mapping[str, object]
    ) -> None:
        """The feature's name, keyword and slug identifier are recorded verbatim."""
        assert golden_feature.get("name") == FEATURE_NAME
        assert golden_feature.get("keyword") == FEATURE_KEYWORD
        assert golden_feature.get("id") == FEATURE_ID

    def test_feature_description_preserves_the_documented_user_story(
        self, golden_feature: Mapping[str, object]
    ) -> None:
        """The description carries the user story and the account list, indentation included.

        The description is the only multi-line value in the document. It is asserted for its
        content rather than merely its type, because it is the one place the specification's
        prose survives into the report -- and stripping or re-wrapping it would be a silent
        change to what the publisher displays.
        """
        description = as_json_string(golden_feature.get("description"), "feature 'description'")
        assert "User Story:" in description
        assert "Accounts are: PosManager, SalesManager" in description
        assert "\n" in description, "the description spans several lines"


class TestStructuralInvariants:
    """Counts, lines, statuses and durations of the golden fixture."""

    def test_fixture_records_six_elements(
        self, golden_elements: tuple[Mapping[str, object], ...]
    ) -> None:
        """Six elements, which is defect D9 expressed as a count.

        Seven scenario instances are authored -- one outline with no ``Examples`` table, a
        second with none either, and a third with five data rows -- yet only six are
        reachable. See :class:`TestDefectD9Preservation` for the mechanism.
        """
        assert len(golden_elements) == EXPECTED_ELEMENT_COUNT

    def test_every_element_is_a_scenario_derived_from_an_outline(
        self, golden_elements: tuple[Mapping[str, object], ...]
    ) -> None:
        """Every element is of type ``scenario`` and keyword ``Scenario Outline``.

        The fixture records no ``background`` element. That is deliberate, and it is the
        reason a live run's step counts must never be compared with this fixture's: the live
        writer folds the ``Background`` step into each scenario's steps.
        """
        for index, element in enumerate(golden_elements):
            assert element.get("type") == ELEMENT_TYPE, f"element {index} is not a scenario"
            assert element.get("keyword") == ELEMENT_KEYWORD, f"element {index} keyword differs"
        assert not [
            element for element in golden_elements if element.get("type") == "background"
        ], "the golden fixture records no background element"

    def test_fixture_records_nineteen_steps(
        self,
        golden_elements: tuple[Mapping[str, object], ...],
        golden_steps: tuple[Mapping[str, object], ...],
    ) -> None:
        """Nineteen steps: four for the first element plus three for each of the other five."""
        assert len(golden_steps) == EXPECTED_STEP_COUNT
        assert len(steps_of(golden_elements[0])) == len(VALID_CREDENTIALS_STEP_LINES)
        assert [len(steps_of(element)) for element in golden_elements[1:]] == [
            len(EMPTY_FIELD_STEP_LINES)
        ] * 5

    def test_every_step_passed(self, golden_steps: tuple[Mapping[str, object], ...]) -> None:
        """Every step's status is ``passed``.

        Including the four steps of the first element, which run with the literal
        ``<username>`` / ``<password>`` placeholder text because no ``Examples`` table binds to
        that outline -- defect D1 -- and pass regardless.
        """
        statuses = {
            as_json_object(step.get("result"), "step 'result'").get("status")
            for step in golden_steps
        }
        assert statuses == {STATUS_PASSED}

    def test_fixture_holds_no_failing_step_and_therefore_no_rerun_line(
        self, golden_steps: tuple[Mapping[str, object], ...]
    ) -> None:
        """No step failed, so the fixture yields no rerun-manifest entry at all.

        A fixture invariant, asserted here because it is a property of this document. The
        rerun manifest's own contents are criterion V9 and belong to
        ``tests/integration/test_rerun_manifest.py``; nothing about its format is asserted
        here.
        """
        failed = [
            step
            for step in golden_steps
            if as_json_object(step.get("result"), "step 'result'").get("status") != STATUS_PASSED
        ]
        assert failed == []

    def test_every_duration_is_a_non_negative_integer(
        self, golden_steps: tuple[Mapping[str, object], ...]
    ) -> None:
        """Durations are integer nanosecond counts, asserted by type only.

        No specific duration value is asserted anywhere in this module. The values are
        deliberately non-uniform across steps precisely so that no reader mistakes one for a
        fixed constant, and they change on every real run.
        """
        for index, step in enumerate(golden_steps):
            duration = as_json_object(step.get("result"), f"step {index} 'result'").get("duration")
            assert is_json_int(duration), f"step {index} duration is not an integer"
            assert duration >= 0, f"step {index} duration is negative"

    def test_durations_are_not_a_single_repeated_constant(
        self, golden_steps: tuple[Mapping[str, object], ...]
    ) -> None:
        """The fixture's durations genuinely vary, so a constant cannot be mistaken for one.

        The guard that makes the type-only rule above self-evident: if every duration were the
        same number, a reader could reasonably start asserting that number, and the first real
        run would then fail.
        """
        durations = {
            as_json_object(step.get("result"), "step 'result'").get("duration")
            for step in golden_steps
        }
        assert len(durations) > 1

    def test_element_lines_are_the_documented_outline_and_examples_rows(
        self, golden_elements: tuple[Mapping[str, object], ...]
    ) -> None:
        """The six element lines are the first outline plus the five ``Examples`` data rows.

        Line 13 is the first outline. Lines 37, 38 and 39 are the SalesManager rows and lines
        44 and 45 the PosManager rows -- an element per data row, all five belonging to the
        third outline. Line 21, the second outline, is absent: defect D9.
        """
        lines = {element.get("line") for element in golden_elements}
        assert lines == set(ELEMENT_LINES)
        assert 21 not in lines, "the second outline's line must not appear (defect D9)"

    def test_valid_credentials_element_records_its_four_step_lines(
        self, golden_elements: tuple[Mapping[str, object], ...]
    ) -> None:
        """The element tagged ``UPGN-286`` records step lines 14 to 17, in order.

        Selected by tag, never by name.
        """
        element = single_element_tagged(golden_elements, TAG_VALID_CREDENTIALS)
        assert step_lines_of(element) == VALID_CREDENTIALS_STEP_LINES

    def test_every_empty_field_element_records_the_same_three_step_lines(
        self, golden_elements: tuple[Mapping[str, object], ...]
    ) -> None:
        """Each element tagged ``UPGN-288`` records step lines 30 to 32, in order.

        All five report the same three source lines, because they are five data rows of one
        outline rather than five separate outlines. Selected by tag, never by name.
        """
        tagged = elements_tagged(golden_elements, TAG_EMPTY_FIELD)
        assert len(tagged) == EXPECTED_TAG_ELEMENT_COUNTS[TAG_EMPTY_FIELD]
        for element in tagged:
            assert step_lines_of(element) == EMPTY_FIELD_STEP_LINES

    def test_every_step_is_matched_against_the_ported_step_definition_module(
        self, golden_steps: tuple[Mapping[str, object], ...]
    ) -> None:
        """Every step's ``match.location`` names the ported step-definition module.

        A fixture invariant. The live writer records an empty location instead, which is why
        the reusable validator only requires the value to be a string.
        """
        locations = {
            as_json_object(step.get("match"), "step 'match'").get("location")
            for step in golden_steps
        }
        assert locations == {STEP_MATCH_LOCATION}

    def test_step_keywords_are_the_documented_gherkin_keywords(
        self, golden_steps: tuple[Mapping[str, object], ...]
    ) -> None:
        """Only ``When``, ``And`` and ``Then`` appear, as the documented outlines use.

        No ``Given`` appears, because the fixture omits the ``Background`` step -- the same
        omission that makes step-count comparisons with a live run invalid.
        """
        keywords = {step.get("keyword") for step in golden_steps}
        assert keywords == {"When", "And", "Then"}
        assert "Given" not in keywords


# =============================================================================
# Criterion V5, part three: tag values carry NO leading `@`.
#
# The parser strips the prefix while reading the feature file, exactly as the source
# toolchain's own JSON writer did, so the ported report records `UPGN-286` and never
# `@UPGN-286`. The same stripping is what turns each Gherkin tag into a pytest marker
# attribute -- marker registration itself is criterion V10 and belongs to
# `tests/unit/test_markers.py`, which this module neither duplicates nor depends on.
# =============================================================================


class TestTagEmission:
    """Tag values, their shape, and the absence of the Gherkin prefix."""

    def test_no_tag_value_anywhere_carries_the_gherkin_prefix(
        self, golden_document: Sequence[object]
    ) -> None:
        """No tag value at any level begins with ``@``.

        The whole document is walked -- the feature's tags and every element's tags -- rather
        than a sample, because one prefixed value anywhere would mean something other than the
        ported toolchain produced the document.
        """
        values = all_tag_values(golden_document)
        assert values, "the document must carry at least one tag"
        prefixed = sorted({value for value in values if value.startswith(TAG_PREFIX)})
        assert prefixed == [], f"these tag values still carry the {TAG_PREFIX!r}: {prefixed}"

    def test_prefix_absence_is_asserted_against_the_raw_bytes_as_well(
        self, golden_bytes: bytes
    ) -> None:
        """The ``@`` appears nowhere in the fixture except inside the e-mail addresses.

        A second, independent statement of the same rule that does not go through the tag
        walker: were the walker ever to stop finding tags, the assertion above could pass
        vacuously while a prefixed value sat in the file. Every remaining ``@`` belongs to an
        address in a step name, such as ``salesmanager7@info.com``.
        """
        text = golden_bytes.decode("utf-8")
        assert f'"{TAG_PREFIX}' not in text, "no JSON string value may begin with the tag prefix"
        for tag in (TAG_FEATURE, TAG_VALID_CREDENTIALS, TAG_EMPTY_FIELD):
            assert f"{TAG_PREFIX}{tag}" not in text, f"{tag!r} must be recorded without a prefix"

    def test_tags_are_recorded_as_bare_strings_in_this_fixture(
        self, golden_feature: Mapping[str, object], golden_elements: Sequence[Mapping[str, object]]
    ) -> None:
        """Every tag entry in the fixture is a bare JSON string.

        The shape the source toolchain wrote, and the shape this fixture therefore records. A
        live report wraps each tag in an object carrying a ``name`` instead; both shapes are
        conformant, which is why the reusable validator and :func:`tag_values` accept either.
        """
        containers: list[Mapping[str, object]] = [golden_feature, *golden_elements]
        for index, container in enumerate(containers):
            entries = as_json_array(container.get("tags"), f"container {index} 'tags'")
            assert entries, f"container {index} carries no tag"
            for entry in entries:
                assert isinstance(entry, str), (
                    f"container {index} records a tag as {type(entry).__name__}; this fixture "
                    "records bare strings"
                )

    def test_feature_carries_only_the_login_tag(self, golden_feature: Mapping[str, object]) -> None:
        """The feature-level tag is ``Login``, and it is the only one."""
        assert tag_values(golden_feature) == (TAG_FEATURE,)

    def test_element_tags_are_exactly_the_documented_scenario_and_examples_tags(
        self, golden_elements: tuple[Mapping[str, object], ...]
    ) -> None:
        """Between them the elements carry exactly four tag values, and no others.

        One Jira tag for the first outline, one for the third, and one for each of the two
        ``Examples`` tables. The second outline's Jira tag is absent -- defect D9.
        """
        values: set[str] = set()
        for element in golden_elements:
            values.update(tag_values(element))
        assert values == set(EXPECTED_ELEMENT_TAGS)

    def test_each_element_tag_is_carried_by_the_documented_number_of_elements(
        self, golden_elements: tuple[Mapping[str, object], ...]
    ) -> None:
        """Element counts per tag: one, five, three and two.

        Three SalesManager rows and two PosManager rows make up the five parametrisations of
        the third outline, so the two ``Examples`` counts sum to the ``UPGN-288`` count.
        """
        counts = {
            tag: len(elements_tagged(golden_elements, tag)) for tag in EXPECTED_TAG_ELEMENT_COUNTS
        }
        assert counts == dict(EXPECTED_TAG_ELEMENT_COUNTS)
        assert (
            counts[TAG_SALES_MANAGER] + counts[TAG_POS_MANAGER] == counts[TAG_EMPTY_FIELD]
        ), "every parametrisation of the third outline comes from one of the two Examples tables"

    def test_examples_tagged_elements_also_carry_their_outline_tag(
        self, golden_elements: tuple[Mapping[str, object], ...]
    ) -> None:
        """Every ``Examples``-tagged element also carries the outline's Jira tag.

        Tag inheritance is what preserves Jira traceability through parametrisation: a data row
        tagged only ``SalesManager`` would no longer trace to its issue.
        """
        for tag in (TAG_SALES_MANAGER, TAG_POS_MANAGER):
            for element in elements_tagged(golden_elements, tag):
                assert TAG_EMPTY_FIELD in tag_values(element)

    def test_no_element_carries_both_examples_tags(
        self, golden_elements: tuple[Mapping[str, object], ...]
    ) -> None:
        """An element derives from one ``Examples`` table, never from both."""
        for element in golden_elements:
            values = set(tag_values(element))
            assert not {TAG_SALES_MANAGER, TAG_POS_MANAGER} <= values


# =============================================================================
# Defect D9, preserved and made auditable.
#
# The second scenario outline and the third parse to the IDENTICAL name: the missing space
# after the third outline's colon separates the keyword and nothing else, so the name is the
# same string. The generated test function is derived from the scenario name, so both outlines
# bind the same symbol and the later definition silently replaces the earlier one --
# `UPGN-287` never runs and never reaches the report.
#
# It is asserted here, never repaired. Defects are behavior, and behavioural improvements to
# the ported suite are explicitly out of scope; the one-line fix is recorded in
# `docs/migration-parity.md` as a decision for the owners.
#
# The collection-count evidence -- exactly six collected test identifiers, none deriving from
# the unreachable scenario -- is criterion V3 and belongs to
# `tests/parity/test_defect_preservation.py`. What is asserted below is the shape of the
# REPORT ARTIFACT, which is this module's subject.
# =============================================================================


class TestDefectD9Preservation:
    """The unreachable scenario's absence, and the collided name that causes it."""

    def test_unreachable_scenario_tag_appears_nowhere_in_the_raw_bytes(
        self, golden_bytes: bytes
    ) -> None:
        """The string ``UPGN-287`` does not occur anywhere in the serialized document.

        The bluntest possible statement of the defect, and the one that cannot be fooled by a
        selection helper: the tag is simply not in the file.
        """
        assert TAG_UNREACHABLE.encode("ascii") not in golden_bytes

    def test_no_element_carries_the_unreachable_scenario_tag(
        self, golden_elements: tuple[Mapping[str, object], ...]
    ) -> None:
        """No element carries ``UPGN-287``, while the other three Jira-tagged levels do.

        Keyed on tags. The companion assertion -- that the tags which *are* present are
        carried by the documented number of elements -- is what stops this from passing merely
        because the walker found nothing.
        """
        assert elements_tagged(golden_elements, TAG_UNREACHABLE) == ()
        assert elements_tagged(golden_elements, TAG_VALID_CREDENTIALS) != ()
        assert elements_tagged(golden_elements, TAG_EMPTY_FIELD) != ()

    def test_the_feature_does_not_carry_the_unreachable_scenario_tag_either(
        self, golden_document: Sequence[object]
    ) -> None:
        """``UPGN-287`` is absent at every level of the document, feature level included."""
        assert TAG_UNREACHABLE not in all_tag_values(golden_document)

    def test_six_elements_carry_six_distinct_identifiers(
        self, golden_elements: tuple[Mapping[str, object], ...]
    ) -> None:
        """All six element ``id`` values differ, so every element is individually addressable.

        This is precisely what ``name`` is not: the identifier carries the ``Examples`` table
        and the data-row number, so it distinguishes the five parametrisations that share one
        name.
        """
        ids = element_ids(golden_elements)
        assert len(ids) == EXPECTED_ELEMENT_COUNT
        assert len(set(ids)) == EXPECTED_ELEMENT_COUNT, f"duplicate element identifier in {ids}"

    def test_five_elements_share_one_name_and_one_does_not(
        self, golden_elements: tuple[Mapping[str, object], ...]
    ) -> None:
        """The element-name multiset is one plus five, which is defect D9 as data.

        Names are counted here, never used to select: the count *is* the finding.
        """
        counts = Counter(element_names(golden_elements))
        assert dict(counts) == dict(EXPECTED_NAME_COUNTS)
        assert counts[NAME_COLLIDED] == 5
        assert len(counts) == 2

    def test_keying_on_name_would_lose_four_of_the_six_elements(
        self, golden_elements: tuple[Mapping[str, object], ...]
    ) -> None:
        """A name-keyed mapping collapses six elements to two; an id-keyed one keeps all six.

        The executable form of this module's central rule. Six elements go in; a mapping keyed
        on ``name`` comes out holding two, so four are silently lost and the survivor of the
        collided name is whichever came last. The same elements keyed on ``id`` keep all six.
        That is why every assertion in this module selects by tag or by id.
        """
        by_name = dict(zip(element_names(golden_elements), golden_elements, strict=True))
        by_id = dict(zip(element_ids(golden_elements), golden_elements, strict=True))
        assert len(by_name) == 2
        assert len(by_id) == EXPECTED_ELEMENT_COUNT

    def test_the_collided_name_belongs_to_the_third_outline_not_the_second(
        self, golden_elements: tuple[Mapping[str, object], ...]
    ) -> None:
        """Every element answering to the collided name is tagged ``UPGN-288``.

        The direction of the overwrite, stated as an assertion: the surviving definition is the
        third outline's, so all five parametrisations trace to ``UPGN-288`` and none to
        ``UPGN-287``. Names are read here only to filter; the assertion itself is about tags.
        """
        collided = [
            element
            for element, name in zip(golden_elements, element_names(golden_elements), strict=True)
            if name == NAME_COLLIDED
        ]
        assert len(collided) == 5
        for element in collided:
            values = tag_values(element)
            assert TAG_EMPTY_FIELD in values
            assert TAG_UNREACHABLE not in values

    def test_the_surviving_outline_reports_the_third_outlines_step_lines(
        self, golden_elements: tuple[Mapping[str, object], ...]
    ) -> None:
        """The collided elements report lines 30-32, the third outline's steps, not 22-25.

        Independent corroboration of the overwrite's direction that does not use tags at all:
        the second outline's steps occupy lines 22 to 25 and appear nowhere in the document.
        """
        reported: set[int] = set()
        for element in elements_tagged(golden_elements, TAG_EMPTY_FIELD):
            reported.update(step_lines_of(element))
        assert reported == set(EMPTY_FIELD_STEP_LINES)
        assert reported.isdisjoint({22, 23, 24, 25})


# =============================================================================
# Defects D1 and D4, as the report artifact records them.
#
# Both are properties of THIS artifact, which is why they are asserted here: the report the
# source system would have published is the direct evidence for each. Neither is repaired, and
# the step names are read exactly as recorded -- never interpolated, rendered, translated or
# normalised.
# =============================================================================


class TestPlaceholderSubstitutionDefects:
    """The literal placeholders of D1 and the swapped column of D4, both recorded verbatim."""

    def test_unbound_outline_records_the_literal_placeholder_text(
        self, golden_elements: tuple[Mapping[str, object], ...]
    ) -> None:
        """The first outline's steps record ``<username>`` and ``<password>`` literally.

        No ``Examples`` table binds to that outline -- the tables attach to the immediately
        preceding outline, which is the third -- so its steps run with the placeholder text
        itself and pass. Selected by tag.
        """
        element = single_element_tagged(golden_elements, TAG_VALID_CREDENTIALS)
        names = [as_json_string(step.get("name"), "step 'name'") for step in steps_of(element)]
        assert '"<username>"' in names[0]
        assert '"<password>"' in names[1]

    def test_empty_field_outline_feeds_the_password_column_into_the_username_step(
        self, golden_elements: tuple[Mapping[str, object], ...]
    ) -> None:
        """The username step of the third outline receives the password column's value.

        The report records ``User enters "salesmanager" username`` and
        ``User enters "posmanager" username`` -- the password column throughout, never the
        address from the username column. Direct evidence of the defect in the very artifact
        the publisher consumes. Selected by ``Examples`` tag, never by name.
        """
        for tag, expected_value in (
            (TAG_SALES_MANAGER, "salesmanager"),
            (TAG_POS_MANAGER, "posmanager"),
        ):
            for element in elements_tagged(golden_elements, tag):
                first_step = steps_of(element)[0]
                name = as_json_string(first_step.get("name"), "step 'name'")
                assert name == f'User enters "{expected_value}" username'
                assert (
                    "@" not in name
                ), "the username step receives the password column, so no address appears"

    def test_the_asserted_message_is_recorded_exactly_as_specified(
        self, golden_elements: tuple[Mapping[str, object], ...]
    ) -> None:
        """The final step of the third outline records the specified message verbatim.

        The message is French-language text while the explanatory comment above it in the
        specification is English; the assertion is the executable truth, so the recorded string
        is the French one. It is 29 bytes and pure ASCII, and it is compared exactly -- never
        translated to match the comment, never normalised, and never stripped of its trailing
        period.
        """
        expected = 'User sees "Veuillez renseigner ce champ." message'
        for element in elements_tagged(golden_elements, TAG_EMPTY_FIELD):
            last_step = steps_of(element)[-1]
            name = as_json_string(last_step.get("name"), "step 'name'")
            assert name == expected
            assert name.encode("ascii").endswith(b'champ." message')


# =============================================================================
# The reusable validator itself.
#
# The same function validates the golden fixture and any live report, so it has to be correct
# in both directions: it must accept every conformant shape -- including the live shape, which
# differs from the fixture in four documented ways -- and it must reject a genuine violation.
# A validator that only ever returned "no findings" would make every schema assertion in this
# module vacuous, so its negative cases are exercised explicitly.
# =============================================================================


class TestReusableValidator:
    """Positive and negative cases for :func:`collect_schema_findings`."""

    def test_validator_accepts_the_golden_fixture(self, golden_document: Sequence[object]) -> None:
        """The golden fixture produces no finding at all."""
        assert collect_schema_findings(list(golden_document)) == ()

    def test_validator_accepts_a_document_in_the_live_writers_shape(self) -> None:
        """A live-shaped document produces no finding either.

        Four differences from the fixture are present at once: tag entries as objects, a
        ``Background``-derived step folded into a scenario, an empty ``match.location``, and an
        ``error_message`` on a failed step's result. All four are conformant, and none of them
        is what this module compares.
        """
        assert collect_schema_findings(live_shaped_document()) == ()

    def test_validator_is_indifferent_to_element_count_and_order(self) -> None:
        """Reversing and duplicating the elements changes no verdict.

        Element order and element count are never part of the schema: a parallel run reports in
        whatever order its workers finish, and its document is larger than a serial one's while
        being structurally identical. This is also why no element is ever de-duplicated --
        several legitimately share one name.
        """
        document = live_shaped_document()
        feature = as_json_object(document[0], "synthesised feature")
        elements = list(as_json_array(feature.get("elements"), "synthesised 'elements'"))
        reordered = json.loads(json.dumps(document))
        reordered[0]["elements"] = [*reversed(elements), *elements]
        assert collect_schema_findings(reordered) == ()

    def test_validator_accepts_a_document_with_no_features(self) -> None:
        """An empty array is conformant.

        The ordinary state of the live artifact: the documented default selector matches no
        scenario, so a run that behaves exactly as specified writes an empty document. That is
        a success rather than a failure, which is why the emptiness is tolerated here and
        reported as a skip -- never as an error -- where a feature is actually needed.
        """
        assert collect_schema_findings([]) == ()

    def test_validator_rejects_a_document_that_is_not_an_array(self) -> None:
        """An object, a string and ``None`` are each reported rather than raising.

        The function is total: it is pointed at arbitrary decoded input and always returns a
        verdict, because a report this project does not produce is exactly the input it exists
        to judge.
        """
        payloads: tuple[object, ...] = ({"features": []}, "[]", None, 7)
        for payload in payloads:
            findings = collect_schema_findings(payload)
            assert len(findings) == 1
            assert "must be a JSON array of features" in findings[0]

    def test_validator_reports_a_missing_required_key_at_feature_level(self) -> None:
        """Removing ``language`` from the feature is reported against the feature."""
        document = live_shaped_document()
        del document[0]["language"]
        findings = collect_schema_findings(document)
        assert "[0]: missing key(s) ['language']" in findings

    def test_validator_reports_a_missing_required_key_at_scenario_level(self) -> None:
        """Removing ``type`` from an element is reported against that element."""
        document = live_shaped_document()
        del document[0]["elements"][0]["type"]
        findings = collect_schema_findings(document)
        assert "[0].elements[0]: missing key(s) ['type']" in findings

    def test_validator_reports_a_missing_required_key_at_step_level(self) -> None:
        """Removing ``match`` from a step is reported against that step."""
        document = live_shaped_document()
        del document[0]["elements"][0]["steps"][0]["match"]
        findings = collect_schema_findings(document)
        assert "[0].elements[0].steps[0]: missing key(s) ['match']" in findings

    def test_validator_reports_an_unexpected_key_at_every_level(self) -> None:
        """A key the frozen schema does not define is reported wherever it appears.

        Each case borrows a key that is legitimate at *another* level -- ``status`` from a
        result, ``uri`` from a feature, ``rows`` from neither -- because that is the realistic
        drift: a writer that starts publishing a little more than the publisher ever consumed.
        """
        document = live_shaped_document()
        document[0]["status"] = STATUS_PASSED
        assert "[0]: unexpected key(s) ['status']" in collect_schema_findings(document)

        document = live_shaped_document()
        document[0]["elements"][0]["uri"] = FEATURE_URI
        assert "[0].elements[0]: unexpected key(s) ['uri']" in collect_schema_findings(document)

        document = live_shaped_document()
        document[0]["elements"][0]["steps"][0]["rows"] = []
        assert "[0].elements[0].steps[0]: unexpected key(s) ['rows']" in collect_schema_findings(
            document
        )

    def test_validator_reports_a_result_that_carries_an_unknown_key(self) -> None:
        """``error_message`` is tolerated on a result; anything else is not."""
        document = live_shaped_document()
        document[0]["elements"][0]["steps"][0]["result"]["error_message"] = ""
        assert collect_schema_findings(document) == ()
        document[0]["elements"][0]["steps"][0]["result"]["screenshot"] = "shot.png"
        findings = collect_schema_findings(document)
        assert any("unexpected key(s) ['screenshot']" in finding for finding in findings)

    def test_validator_reports_a_tag_that_still_carries_the_prefix(self) -> None:
        """A prefixed tag value is reported, in either tag shape.

        The rule that criterion V5 states explicitly, exercised in both the object shape a live
        writer emits and the bare-string shape the fixture records.
        """
        document = live_shaped_document()
        document[0]["tags"] = [{"line": 1, "name": f"{TAG_PREFIX}{TAG_FEATURE}"}]
        findings = collect_schema_findings(document)
        assert any("must not carry the '@'" in finding for finding in findings)

        document = live_shaped_document()
        document[0]["elements"][0]["tags"] = [f"{TAG_PREFIX}{TAG_VALID_CREDENTIALS}"]
        findings = collect_schema_findings(document)
        assert any("must not carry the '@'" in finding for finding in findings)

    def test_validator_reports_a_non_integer_line_and_a_boolean_line(self) -> None:
        """``line`` must be an integer, and a JSON boolean does not count as one.

        ``bool`` subclasses ``int`` in Python, so a boolean would satisfy a naive integer check
        and a line number of ``true`` would pass unnoticed.
        """
        document = live_shaped_document()
        document[0]["line"] = "2"
        assert any("line: must be an integer" in f for f in collect_schema_findings(document))

        document = live_shaped_document()
        document[0]["elements"][0]["steps"][0]["line"] = True
        assert any("line: must be an integer" in f for f in collect_schema_findings(document))

    def test_validator_reports_a_non_integer_or_negative_duration(self) -> None:
        """``duration`` must be a non-negative integer nanosecond count.

        Its type is checked; its value never is, beyond not being negative.
        """
        document = live_shaped_document()
        document[0]["elements"][0]["steps"][0]["result"]["duration"] = 0.5
        assert any(
            "duration: must be an integer nanosecond count" in f
            for f in collect_schema_findings(document)
        )

        document = live_shaped_document()
        document[0]["elements"][0]["steps"][0]["result"]["duration"] = -1
        assert any("duration: must not be negative" in f for f in collect_schema_findings(document))

    def test_validator_reports_a_match_that_carries_the_wrong_keys(self) -> None:
        """``match`` carries ``location`` and nothing else, and the value is a string."""
        document = live_shaped_document()
        document[0]["elements"][0]["steps"][0]["match"] = {"location": "", "arguments": []}
        assert any(
            "match: unexpected key(s) ['arguments']" in f for f in collect_schema_findings(document)
        )

        document = live_shaped_document()
        document[0]["elements"][0]["steps"][0]["match"] = {"location": None}
        assert any(
            "match.location: must be a string" in f for f in collect_schema_findings(document)
        )

    def test_validator_reports_a_step_or_element_of_the_wrong_type(self) -> None:
        """A string where a step belongs, and a number where an element belongs."""
        document = live_shaped_document()
        document[0]["elements"][0]["steps"][0] = "Given User is on the login page"
        assert any("step must be a JSON object" in f for f in collect_schema_findings(document))

        document = live_shaped_document()
        document[0]["elements"] = [1]
        assert any("element must be a JSON object" in f for f in collect_schema_findings(document))

    def test_validator_reads_a_document_from_disk_with_an_explicit_encoding(
        self, tmp_path: Path
    ) -> None:
        """A synthesised document round-trips through a file and still validates.

        Written under ``tmp_path`` so nothing outside the temporary directory is touched, and
        read back with an explicit UTF-8 encoding, exactly as every reader in this project
        does. Nothing under the repository's own artifact root is created, read or removed
        here.
        """
        target = tmp_path / "cucumber.json"
        target.write_text(json.dumps(live_shaped_document()), encoding="utf-8")
        decoded: object = json.loads(target.read_text(encoding="utf-8"))
        assert collect_schema_findings(decoded) == ()

    def test_tag_values_accepts_both_tag_shapes_and_skips_unusable_entries(self) -> None:
        """The tag reader handles the object shape, the string shape and a mixture.

        Unusable entries are skipped rather than raising, so the helper stays usable on a
        document that has already been found off-schema -- which is what lets a failure report
        the tags it did manage to read.
        """
        assert tag_values({"tags": [TAG_FEATURE]}) == (TAG_FEATURE,)
        assert tag_values({"tags": [{"line": 1, "name": TAG_FEATURE}]}) == (TAG_FEATURE,)
        assert tag_values({"tags": [TAG_FEATURE, {"name": TAG_EMPTY_FIELD}, 7, {}, None]}) == (
            TAG_FEATURE,
            TAG_EMPTY_FIELD,
        )
        assert tag_values({}) == ()
        assert tag_values({"tags": None}) == ()
        assert tag_values([TAG_FEATURE]) == (TAG_FEATURE,)


# =============================================================================
# Agreement with the reporting adapter.
#
# The schema is declared twice on purpose -- once at the top of this module and once in
# `app/reporting/cucumber_json.py`, the single module that ports the `json:target/cucumber.json`
# plugin declaration. Comparing the fixture against the application's own constants alone would
# prove only that the two files were edited together; comparing the two INDEPENDENT statements
# is what makes either of them evidence.
#
# Every test here skips with a stated reason when the application package cannot be imported,
# because reaching any `app.*` module executes the application factory and therefore needs
# Flask installed. The golden-fixture assertions above cover the schema without it.
# =============================================================================


class TestReportingAdapterAgreement:
    """The application's frozen schema, validator and loader agree with this module."""

    def test_adapter_publishes_the_same_three_key_sets(self) -> None:
        """The adapter's key-set constants equal the ones declared at the top of this module.

        Set equality at all four published levels, plus the recognised optional and the tag
        prefix. A drift in either direction fails here rather than surfacing as a mysterious
        report-parsing failure later.
        """
        skip_unless_reporting_adapter_importable()
        from app.reporting import cucumber_json as adapter

        assert adapter.FEATURE_KEYS == FEATURE_KEYS
        assert adapter.SCENARIO_KEYS == SCENARIO_KEYS
        assert adapter.STEP_KEYS == STEP_KEYS
        assert adapter.RESULT_KEYS == RESULT_KEYS
        assert adapter.OPTIONAL_RESULT_KEYS == OPTIONAL_RESULT_KEYS
        assert adapter.TAG_PREFIX == TAG_PREFIX
        assert dict(adapter.SCHEMA_KEY_SETS) == {
            "feature": FEATURE_KEYS,
            "scenario": SCENARIO_KEYS,
            "step": STEP_KEYS,
            "result": RESULT_KEYS,
        }

    def test_adapter_validator_accepts_the_golden_fixture(
        self, golden_document: Sequence[object]
    ) -> None:
        """The adapter grades the fixture conformant and counts exactly what it holds.

        One feature, six scenarios, no background element and nineteen steps -- the same
        numbers this module asserts independently -- and no tag carrying the prefix.
        """
        skip_unless_reporting_adapter_importable()
        from app.reporting.cucumber_json import validate_document

        result = validate_document(list(golden_document))
        assert result.ok, result.describe()
        assert result.feature_count == EXPECTED_FEATURE_COUNT
        assert result.scenario_count == EXPECTED_ELEMENT_COUNT
        assert result.background_count == 0
        assert result.step_count == EXPECTED_STEP_COUNT
        assert result.prefixed_tags == ()
        assert result.missing_keys == ()
        assert result.unexpected_keys == ()

    def test_adapter_validator_and_this_module_agree_on_the_live_shape(self) -> None:
        """Both validators accept the live-shaped document, and both count its background.

        The adapter distinguishes a ``background`` element from a ``scenario`` one, so its
        tallies also confirm that the live shape this module synthesises is the shape the
        adapter expects.
        """
        skip_unless_reporting_adapter_importable()
        from app.reporting.cucumber_json import validate_document

        document = live_shaped_document()
        assert collect_schema_findings(document) == ()
        result = validate_document(document)
        assert result.ok, result.describe()
        assert result.feature_count == 2
        assert result.scenario_count == 1
        assert result.background_count == 1

    def test_adapter_tag_reader_agrees_with_this_modules_tag_reader(
        self, golden_feature: Mapping[str, object], golden_elements: Sequence[Mapping[str, object]]
    ) -> None:
        """The two independent tag readers return identical values for every container."""
        skip_unless_reporting_adapter_importable()
        from app.reporting.cucumber_json import tag_names

        for container in (golden_feature, *golden_elements):
            assert tag_names(container) == tag_values(container)

    def test_adapter_declares_the_unreachable_tag_for_traceability_only(self) -> None:
        """The adapter's documented tag vocabulary lists all six tags, the unreachable one too.

        Traceability is preserved by name even where coverage is not: the Jira key of the
        overwritten scenario stays a first-class, documented tag, while the report -- correctly
        -- records no element carrying it. Both statements are true at once, and that is defect
        D9 rather than a contradiction.
        """
        skip_unless_reporting_adapter_importable()
        from app.reporting.cucumber_json import DOCUMENTED_TAGS

        assert DOCUMENTED_TAGS == frozenset(
            {
                TAG_FEATURE,
                TAG_VALID_CREDENTIALS,
                TAG_UNREACHABLE,
                TAG_EMPTY_FIELD,
                TAG_SALES_MANAGER,
                TAG_POS_MANAGER,
            }
        )
        assert EXPECTED_ELEMENT_TAGS == DOCUMENTED_TAGS - {TAG_FEATURE, TAG_UNREACHABLE}

    def test_adapter_loader_grades_the_fixture_valid_through_the_configured_layout(
        self, golden_bytes: bytes, tmp_path: Path
    ) -> None:
        """The loader reads a re-rooted artifact and grades it valid, schema tallies included.

        The whole artifact layout is re-rooted at a temporary directory, so the loader is
        exercised through exactly the path resolution a real run uses -- the artifact-root name
        comes from ``app/utils/paths.py``, never from a literal here -- while nothing under the
        repository's own artifact root is created, read or removed.

        This is what makes the live half of this module more than a skip: the live code path is
        proved deterministically, without a browser, a subprocess or a BDD run.
        """
        skip_unless_reporting_adapter_importable()
        from app.reporting.cucumber_json import REPORT_VALID, load_report, report_path

        destination = report_path(base_dir=tmp_path)
        assert destination.name.endswith(".json"), (
            "the configured artifact is a JSON document, which is what the publisher's include "
            "pattern matches"
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(golden_bytes)

        report = load_report(destination)
        assert report.status == REPORT_VALID, report.reason
        assert report.is_valid
        assert report.reason is None
        assert len(report.features) == EXPECTED_FEATURE_COUNT
        assert report.validation.step_count == EXPECTED_STEP_COUNT

    def test_adapter_loader_reports_an_absent_artifact_rather_than_raising(
        self, tmp_path: Path
    ) -> None:
        """A missing artifact is graded ``absent``, not raised.

        The ordinary state of a fresh checkout, and the reason the live assertions below skip
        instead of failing: the artifact root is wiped before every run, so its absence carries
        no information about the schema.
        """
        skip_unless_reporting_adapter_importable()
        from app.reporting.cucumber_json import REPORT_ABSENT, load_report, report_path

        report = load_report(report_path(base_dir=tmp_path))
        assert report.status == REPORT_ABSENT
        assert report.is_absent
        assert report.features == ()


# =============================================================================
# The live artifact.
#
# `target/cucumber.json` is written only at the end of a real run, and the documented default
# selector -- the port of the runner's `tags = "@LogOut"` option -- matches no scenario at all,
# so the ordinary content of that file is an empty array. Every assertion here therefore states
# its own precondition and skips with a reason when it is not met. A vacuous pass would be
# evidence of nothing, which is exactly what a report-schema criterion must not produce.
#
# What is NOT asserted here, and why: no element count, no step count, no step ordering and no
# document size. A live element carries the `Background` step the golden fixture omits, and a
# parallel run's document is larger than a serial one's while being structurally identical.
# Comparing either against the fixture would be a guaranteed false failure.
# =============================================================================


class TestLiveReport:
    """Schema conformance of whatever the most recent run actually wrote."""

    def test_live_report_is_a_json_array(self, live_report_path: Path) -> None:
        """The artifact decodes as UTF-8 JSON and its top level is an array.

        True of an empty document as much as of a populated one, which is why this assertion
        needs no feature to be present: the publisher's contract is the array, and an empty
        array is the correct output of a run that selected nothing.
        """
        decoded: object = json.loads(live_report_path.read_text(encoding="utf-8"))
        assert isinstance(decoded, list), (
            f"{live_report_path} must hold a JSON array of features, "
            f"found {type(decoded).__name__}"
        )

    def test_live_report_conforms_to_the_frozen_schema(self, live_report_path: Path) -> None:
        """Every feature, element, step and result in the live artifact matches the key sets.

        Key sets and structural invariants only, by way of the same validator the golden fixture
        is checked with. Skips when the document holds no feature, because there would be
        nothing to assert about: the documented default selector deselects every scenario, so an
        empty document is the specified outcome rather than a defect.
        """
        decoded: object = json.loads(live_report_path.read_text(encoding="utf-8"))
        features = as_json_array(decoded, f"the document at {live_report_path}")
        if not features:
            pytest.skip(
                f"the live Cucumber JSON artifact at {live_report_path} records no feature. The "
                "documented default tag expression matches no scenario, so a run that behaves "
                "exactly as specified writes an empty array; there is no feature to assert a "
                "key set against. Run the suite with the tag filter overridden to populate it"
            )
        findings = collect_schema_findings(list(features))
        assert findings == (), "the live report departs from the frozen schema: " + "; ".join(
            findings
        )

    def test_live_report_tags_carry_no_prefix(self, live_report_path: Path) -> None:
        """No tag value in the live artifact carries the leading ``@``.

        Stated separately from the schema walk above so that a populated document proves the
        ``@``-stripping rule explicitly rather than as a side effect. Skips when the document
        carries no tag at all, for the same reason.
        """
        decoded: object = json.loads(live_report_path.read_text(encoding="utf-8"))
        features = as_json_array(decoded, f"the document at {live_report_path}")
        values = all_tag_values(features)
        if not values:
            pytest.skip(
                f"the live Cucumber JSON artifact at {live_report_path} carries no tag, so the "
                "prefix rule has nothing to apply to; the documented default tag expression "
                "deselects every scenario, which is the specified outcome"
            )
        prefixed = sorted({value for value in values if value.startswith(TAG_PREFIX)})
        assert prefixed == [], f"these live tag values still carry the {TAG_PREFIX!r}: {prefixed}"

    def test_live_report_is_graded_by_the_reporting_adapter_too(
        self, live_report_path: Path
    ) -> None:
        """The adapter grades the live artifact ``valid`` and finds no prefixed tag.

        The application's own verdict on the artifact a run produced, which is the verdict the
        report service and the run-status endpoint act on. An empty document is valid, so this
        assertion holds whether or not anything was selected.
        """
        skip_unless_reporting_adapter_importable()
        from app.reporting.cucumber_json import REPORT_VALID, load_report

        report = load_report(live_report_path)
        assert report.status == REPORT_VALID, report.reason
        assert report.validation.prefixed_tags == ()
        assert report.validation.unexpected_keys == ()
        assert report.validation.missing_keys == ()
