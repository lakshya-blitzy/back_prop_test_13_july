"""Tests for ``app/reporting/cucumber_json.py`` -- the one machine-read artifact.

What this module gates
----------------------
``target/cucumber.json`` is the only file this port writes that another program
parses: the pipeline's publisher stage is narrowed to exactly that path
(``Jenkins:15``), so a key with the wrong name, an emitted ``[]`` where the JVM
omits the key, or a float where the contract carries a nanosecond integer is a
*silent* failure -- nothing crashes and the published report is simply wrong.
Every rule of AAP 0.6's Cucumber-JSON contract therefore has an executable
assertion here: the key sets per level, the omission rules, the two tag shapes
and their opposite emptiness rules, the ``id`` slug and its Examples-row form,
duplicate ids, ``match``/``arguments``/``error_message``, the ``after`` array
and its embeddings, the status mapping, the selection rule, element
interleaving, and the I/O wrapper's destination.

The anchors, and where they were measured
-----------------------------------------
``tests/fixtures/golden_cucumber.json`` -- the ``HEAD`` side of the reference
repository's committed report, taken alone (AAP 0.6) -- is the strongest of
them, and the strongest test in this module reconstructs an internal result
document *from* it, feeds that through :func:`build_cucumber_json` and asserts
deep equality against the fixture.  Measured in that baseline and pinned below:
one feature at line 2 with a long-shape ``@Smoke`` tag declared at line 1, a
description carrying its leading indentation verbatim, eight elements
interleaved Background-then-scenario four times with all four Background
occurrences at line 6, nineteen steps, three plain scenario ids and one
Examples-row id ending ``;expected-name;2``, one parameterised step whose three
arguments carry their surrounding quotes at offsets 44, 54 and 63, and the four
result shapes ``{duration,status}`` x14, ``{duration,error_message,status}``
x2, ``{status}`` x2 and ``{duration,status}`` x1 with a ``skipped`` status.  No
element carries ``after`` and none carries an embedding, because the reference
run's teardown hook never fired.

``tests/fixtures/sample_results.json`` is the second anchor, in the port's
*internal* schema: four features (two of which share an ``id`` because they
share a title), ten scenarios, four Background occurrences, thirty steps, one
undefined step, one failed scenario carrying a PNG embedding on its after hook,
two outline rows, and one ``@wip`` scenario the tag expression did not select.

Exactly two reconciliations, both named and both deliberate
-----------------------------------------------------------
1. **The feature directory.** AAP deviation 1 moved the features and preserved
   their filenames, so the golden's URIs carry the Java resource directory
   while the port emits its own.  ``app.utils.paths.normalize_feature_uri``
   owns that rewrite and ``tests/conftest.py`` applies it; this module reaches
   it only through :func:`_reconcile_feature_directory`, and hard-codes neither
   prefix anywhere.
2. **``duration`` inside a ``skipped`` result.** The golden carries one
   ``{"duration": 1000000, "status": "skipped"}`` while AAP 0.6's prose says a
   skipped step is ``{"status": "skipped"}`` with no ``duration`` key.  That
   single cell is the one place the two sources of the contract disagree, and
   this file's own schema prompt is explicit: *"Do not assert that a skipped
   step has no duration ... field presence is per-invocation, not
   per-status."*  :func:`_reconcile_skipped_duration` drops the key from both
   sides, so the module deliberately pins that cell in neither direction while
   pinning the zero-omission rule -- which is what actually governs it -- from
   the sample, where every skipped step carries a duration of zero.

   The writer has since settled the cell in the plan's favour: it gates
   ``duration`` on the status as well as on the value, so a ``skipped`` result
   never carries one whatever was recorded.  That makes this a **real and
   expected** difference from the fixture rather than a hypothetical one, and
   it is why the reconciliation runs **before** ``normalize_volatile`` in
   :func:`comparable`: normalization replaces every duration with a
   placeholder, so a reconciliation applied afterwards could never recognise
   the measured value it is narrowed to and the difference would surface as a
   failure against a writer doing exactly what the plan requires.

Everything else is compared exactly.  Nothing here writes outside pytest's
temporary directories, nothing starts a browser or touches the network, and no
custom pytest marker is used (``pytest.ini`` runs ``--strict-markers``).
"""

from __future__ import annotations

import base64
import copy
import json
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any, Final

import pytest

from app.reporting import events
from app.reporting.cucumber_json import (
    BACKGROUND_ELEMENT_KEYS,
    CUCUMBER_STATUSES,
    FEATURE_KEYS,
    SCENARIO_ELEMENT_KEYS,
    STATUS_ALIASES,
    STATUS_FALLBACK,
    STATUS_PASSED,
    STATUS_UNDEFINED,
    STEP_KEYS,
    build_cucumber_json,
    convert_to_id,
    map_step_status,
    normalize_error_message,
    render_cucumber_json,
    scenario_element_id,
    write_cucumber_json,
)
from app.utils import paths
from conftest import (
    DEFAULT_SCREENSHOT_PNG,
    normalize_feature_uris,
    normalize_volatile,
)

#: One emitted JSON object.  Named rather than repeated, because every helper
#: below moves these around and ``dict[str, Any]`` says nothing about which
#: dict is meant.
JsonDict = dict[str, Any]


# --------------------------------------------------------------------------- #
# Measured anchors in tests/fixtures/golden_cucumber.json
#
# Every constant in this block was read out of that fixture, which is the
# committed reference artifact and is never edited to match a writer's output.
# --------------------------------------------------------------------------- #

#: The golden report holds exactly one feature: under the default ``@Smoke``
#: expression the JVM started test cases from one file only.
GOLDEN_FEATURE_COUNT: Final[int] = 1

#: Eight elements -- four Background occurrences and four scenarios.
GOLDEN_ELEMENT_COUNT: Final[int] = 8

#: Nineteen steps across those eight elements.
GOLDEN_STEP_COUNT: Final[int] = 19

GOLDEN_FEATURE_NAME: Final[str] = "Testinium app CRM Module"
GOLDEN_FEATURE_ID: Final[str] = "testinium-app-crm-module"
GOLDEN_FEATURE_KEYWORD: Final[str] = "Feature"

#: The ``Feature:`` line, and deliberately *not* the tag's line.
GOLDEN_FEATURE_LINE: Final[int] = 2

#: The description with its two leading spaces, which are contract.
GOLDEN_FEATURE_DESCRIPTION: Final[str] = "  Account is: PosManager"

#: The single feature-level tag, declared one line above the feature.
GOLDEN_FEATURE_TAG_NAME: Final[str] = "@Smoke"
GOLDEN_FEATURE_TAG_LINE: Final[int] = 1
GOLDEN_FEATURE_TAG_COLUMN: Final[int] = 1

#: All four Background occurrences carry the Background's own line.
GOLDEN_BACKGROUND_LINE: Final[int] = 6

#: The four scenario elements' lines, in emission order.  The second is an
#: outline *data row*, which is why it is 24 and not the outline's own line.
GOLDEN_SCENARIO_LINES: Final[tuple[int, ...]] = (9, 24, 26, 31)

#: The three plain scenario ids, ``<feature-slug>;<scenario-slug>``.
GOLDEN_PLAIN_SCENARIO_IDS: Final[tuple[str, ...]] = (
    "testinium-app-crm-module;user-can-create-pipeline-in-the-displayed-dashboard",
    "testinium-app-crm-module;user-can-change-the-situation-in-progress",
    "testinium-app-crm-module;user-can-register-new-customer-and-can-print-the-profile",
)

#: The Examples-row id: the Examples block's slug plus the row's position with
#: the header counted as 1, so the first data row is 2.
GOLDEN_OUTLINE_ROW_ID: Final[str] = (
    "testinium-app-crm-module;user-can-change-information-in-dashboard;expected-name;2"
)

#: The outline element's keyword, which is not a plain scenario's.
GOLDEN_OUTLINE_KEYWORD: Final[str] = "Scenario Outline"

#: The outline row's four step lines: the *template*'s lines, while the element
#: itself carries the data row's line.  The asymmetry is measured.
GOLDEN_OUTLINE_STEP_LINES: Final[tuple[int, ...]] = (17, 18, 19, 20)

#: The one parameterised step in the golden report, and its three arguments.
#: ``val`` includes the surrounding double quotes and ``offset`` indexes into
#: the step ``name``.
GOLDEN_ARGUMENT_STEP_NAME: Final[str] = (
    'User can change any user\'s information like "Test2" , "30" and "2"'
)
GOLDEN_ARGUMENTS: Final[tuple[JsonDict, ...]] = (
    {"val": '"Test2"', "offset": 44},
    {"val": '"30"', "offset": 54},
    {"val": '"2"', "offset": 63},
)

#: Result key sets, with their status, and how many steps carry each.  Counted
#: in the golden report, and asserted against it so that a later edit of the
#: baseline is caught here rather than silently changing what the writer is
#: held to.
#: The one measured non-zero duration on a skipped result in the baseline:
#: the step at ``Crm.feature:20``, 1 ms in nanoseconds.  It is the only cell
#: where the baseline and AAP 0.6's prose disagree, and naming it lets the
#: reconciliation below be narrowed to exactly that cell.
GOLDEN_MEASURED_SKIPPED_DURATION: Final[int] = 1_000_000

GOLDEN_RESULT_SHAPES: Final[dict[tuple[tuple[str, ...], str], int]] = {
    (("duration", "status"), "passed"): 14,
    (("duration", "error_message", "status"), "failed"): 2,
    (("status",), "skipped"): 2,
    (("duration", "status"), "skipped"): 1,
}


# --------------------------------------------------------------------------- #
# Measured anchors in tests/fixtures/sample_results.json
# --------------------------------------------------------------------------- #

#: The sample's four features, in source order, by the filename each URI ends
#: with.  A filename is not a directory prefix, so naming them here does not
#: hard-code the feature directory.
SAMPLE_FEATURE_FILENAMES: Final[tuple[str, ...]] = (
    "Contact.feature",
    "Crm.feature",
    "Inventory.feature",
    "Sales.feature",
)

#: Positions of the four features in the emitted list.  Position, not ``id``:
#: two of them share an ``id`` (see the duplicate-id tests), which is why
#: ``app/web/routes.py`` keys its routes on the list index (AAP 0.3.1).
SAMPLE_CONTACT_INDEX: Final[int] = 0
SAMPLE_CRM_INDEX: Final[int] = 1
SAMPLE_INVENTORY_INDEX: Final[int] = 2
SAMPLE_SALES_INDEX: Final[int] = 3

#: The title ``Contact.feature`` and ``Inventory.feature`` share, verbatim from
#: both files' headers -- the mis-titled header is source behaviour the port
#: preserves rather than corrects (AAP 0.2.2).
SAMPLE_DUPLICATE_FEATURE_ID: Final[str] = "testinium-app-inventory-feature"

#: The ten scenarios' element count after the selection rule has run: the
#: ``@wip`` Sales scenario is dropped, so nine survive.
SAMPLE_SELECTED_SCENARIO_COUNT: Final[int] = 9

#: The scenario the tag expression ``not @wip`` excluded, by name and line.
SAMPLE_UNSELECTED_SCENARIO_NAME: Final[str] = (
    "Verify that the user can export the customer list"
)
SAMPLE_UNSELECTED_SCENARIO_LINE: Final[int] = 36

#: The one undefined step in the sample, whose ``match`` must therefore be the
#: empty object.
SAMPLE_UNDEFINED_STEP_NAME: Final[str] = (
    "User can search the customer from the search bar"
)

#: The failed scenario whose after hook carries the screenshot, and the hook's
#: own dotted location.
SAMPLE_EMBEDDING_SCENARIO_NAME: Final[str] = (
    "User can change the situation in progress"
)
SAMPLE_AFTER_HOOK_LOCATION: Final[str] = "features.environment.after_scenario"
SAMPLE_EMBEDDING_MIME_TYPE: Final[str] = "image/png"

#: The teardown hook's own measured duration in the fixture: 412 ms in
#: nanoseconds.  Asserted exactly, because a hook result is where a
#: screenshot's provenance is recorded and the scale has to match a step's.
SAMPLE_AFTER_HOOK_DURATION_NANOS: Final[int] = 412_000_000

#: The status folds this module expects, written out here rather than read
#: from ``app.reporting.cucumber_json.STATUS_ALIASES``.  An oracle taken from
#: the table under test would move with it: changing a fold would change the
#: expectation in the same commit and the test would stay green.  Two tests use
#: this -- one asserting each fold, one asserting the table's membership -- so
#: that both a changed value and an added or removed entry fail.
EXPECTED_STATUS_ALIASES: Final[dict[str, str]] = {
    "error": "failed",
    "hook_error": "failed",
    "cleanup_error": "failed",
    "xfailed": "failed",
    "xpassed": "passed",
    "pending_warn": "pending",
    "untested_pending": "pending",
    "untested_undefined": "undefined",
    "executing": "untested",
    "unknown": "untested",
}

#: The two Sales outline rows' ids.  Their Examples segment is empty and the
#: separator therefore doubled.  That is the *generator's* current behaviour and
#: is being changed under review finding F09 against
#: ``app/reporting/events.py``; the rule under test here is only that the
#: writer copies a supplied ``id`` through unchanged, whatever it contains.
SAMPLE_UNNAMED_EXAMPLES_ROW_IDS: Final[tuple[str, ...]] = (
    "....-app-sales-feature;verify-that-after-creating-a-new-customer--the-page-"
    "title-includes-the-customer-name.;;2",
    "....-app-sales-feature;verify-that-after-creating-a-new-customer--the-page-"
    "title-includes-the-customer-name.;;3",
)


# --------------------------------------------------------------------------- #
# Names used by the hand-built documents
#
# The synthetic documents below need feature URIs.  They are assembled from
# ``app.utils.paths``' own scheme and feature-directory constants so that this
# module names no feature-directory prefix of its own -- the point of
# reconciliation 1.
# --------------------------------------------------------------------------- #


def feature_uri(filename: str) -> str:
    """Build the URI a feature file gets in this port's output.

    :param filename: A feature file's name, e.g. ``"Crm.feature"``.
    :returns: The ``file:``-prefixed, repository-relative URI, assembled from
        :data:`app.utils.paths.FILE_URI_SCHEME` and
        :data:`app.utils.paths.NORMALIZED_FEATURES_PREFIX` rather than from a
        literal, so that the feature directory has exactly one owner.
    """
    return f"{paths.FILE_URI_SCHEME}{feature_path(filename)}"


def feature_path(filename: str) -> str:
    """Build the repository-relative path a feature file gets, with no scheme.

    :param filename: A feature file's name.
    :returns: The path ``app/reporting/rerun_report.py`` and human-facing
        output use, which the internal schema carries beside the URI.
    """
    return f"{paths.NORMALIZED_FEATURES_PREFIX}{filename}"


#: Feature file names for the synthetic documents.  Deliberately not the names
#: of real features, so that a synthetic case can never be mistaken for a
#: measured one.
PROBE_FEATURE_FILENAME: Final[str] = "Probe.feature"
OTHER_PROBE_FEATURE_FILENAME: Final[str] = "OtherProbe.feature"

#: The title ``Login.feature:2`` and ``Notes.feature:1`` share, which is the
#: second of the two duplicate-id pairs AAP 0.6 records.
SHARED_FEATURE_TITLE: Final[str] = "Testinium app login feature"


# --------------------------------------------------------------------------- #
# The two sanctioned reconciliations
# --------------------------------------------------------------------------- #


def _reconcile_feature_directory(document: Any) -> Any:
    """Reconciliation 1: the feature directory, and nothing else.

    AAP deviation 1 moved the feature files while preserving their filenames,
    so the committed baseline's URIs name the Java resource directory and this
    port's name its own.  The rule belongs to
    ``app.utils.paths.normalize_feature_uri`` and is applied by
    ``tests/conftest.py``'s :func:`normalize_feature_uris`; this wrapper exists
    only to give the reconciliation a name at its call sites.  It is
    idempotent, so applying it to output that is already in the port's shape
    changes nothing -- which is why both sides of a comparison can go through
    it unconditionally.

    :param document: A parsed JSON document, or any nesting of mappings,
        sequences and scalars.
    :returns: A new structure with feature-directory prefixes reconciled.
    """
    return normalize_feature_uris(document)


def _reconcile_skipped_duration(document: Any) -> Any:
    """Reconciliation 2: ``duration`` inside a ``skipped`` result.

    The golden report carries one ``{"duration": 1000000, "status":
    "skipped"}`` while AAP 0.6's prose states that a skipped step is
    ``{"status": "skipped"}`` with no ``duration`` key.  That one cell is the
    only place the two sources of this contract disagree, and the writer has
    settled it in the plan's favour: ``_build_result`` gates the field on the
    status as well as on the value, so a ``skipped`` result carries ``status``
    alone whatever the internal document recorded.  This file's own schema
    prompt settles what the tests may say about it: *"Do not assert that a
    skipped step has no duration ... field presence is per-invocation, not
    per-status."*

    So the key is dropped from **both** sides of every comparison, and this
    module pins that cell in neither direction.  What actually governs it --
    ``createResultMap``'s rule that a zero duration is omitted whatever the
    status -- is pinned instead from ``sample_results.json``, where every
    skipped step carries a duration of zero.

    The reconciliation is deliberately **as narrow as the disagreement**: it
    removes ``duration`` only from a skipped result whose value is the one the
    baseline measured, :data:`GOLDEN_MEASURED_SKIPPED_DURATION`.  Every other
    skipped result keeps its key exactly as the writer emitted it, so the
    baseline's two bare ``{"status": "skipped"}`` results still hold the writer
    to emitting no duration at all for them, and a writer that started
    inventing one would fail the golden comparison.  A blanket erasure would
    have surrendered that protection to buy nothing.

    That narrowness is what fixes this function's **position** in
    :func:`comparable`: it must run on the document as it was read, before
    ``normalize_volatile`` replaces every ``duration`` with a placeholder,
    because a placeholder is not :data:`GOLDEN_MEASURED_SKIPPED_DURATION` and a
    reconciliation that recognised nothing would reconcile nothing.  Running it
    first costs nothing else -- it only ever removes a key, so no value
    normalization depends on it.

    :param document: A parsed JSON document, or any nesting of mappings,
        sequences and scalars.  It is applied to the document as read, so the
        durations it sees are the measured ones.
    :returns: A new structure in which a skipped result carrying exactly the
        measured duration no longer carries the key.  Nothing else is altered,
        and the input is never mutated.
    """
    if isinstance(document, dict):
        contested = (
            document.get("status") == "skipped"
            and document.get("duration") == GOLDEN_MEASURED_SKIPPED_DURATION
        )
        return {
            key: _reconcile_skipped_duration(value)
            for key, value in document.items()
            if not (contested and key == "duration")
        }
    if isinstance(document, (list, tuple)):
        return [_reconcile_skipped_duration(item) for item in document]
    return document


def comparable(document: Any) -> Any:
    """Reduce a document to what a comparison may legitimately assert.

    Three transformations, and the **order is load-bearing**: the
    feature-directory reconciliation, then the skipped-duration reconciliation,
    then ``tests/conftest.py``'s :func:`normalize_volatile` -- which
    canonicalises timestamps, durations, failure text and embedded bytes while
    **preserving each key's presence**.  What survives is structure: feature
    order, element order, the Background's position, every key's presence or
    absence, and every value a run does not get to choose.

    The skipped-duration step comes second rather than last because it is
    narrowed to the one measured value the baseline carries and normalization
    would already have replaced that value with a placeholder; see
    :func:`_reconcile_skipped_duration`.  Normalization stays last so that it
    is the thing standing between a measured value and an assertion, which is
    the whole reason it exists.

    :param document: Either side of a comparison.
    :returns: The comparable form.  Applied to both sides, never to one.
    """
    reconciled = _reconcile_skipped_duration(
        _reconcile_feature_directory(document)
    )
    return normalize_volatile(reconciled)


# --------------------------------------------------------------------------- #
# Reconstructing an internal result document from an emitted one
#
# This is what makes the golden fixture a test of the writer rather than of a
# fixture: the emitted document is walked back into the internal schema through
# ``app.reporting.events``' own builders -- the single owners of the internal
# key-presence rules -- and the writer is then asked to reproduce it.
# --------------------------------------------------------------------------- #


def _internal_step(step: JsonDict) -> JsonDict:
    """Rebuild the internal step object behind one emitted step.

    :param step: An emitted step: ``keyword``, ``line``, ``name``, ``match``,
        ``result``.
    :returns: The internal step from :func:`app.reporting.events.new_step`.
        ``matched`` is inferred from the presence of ``match.location``, which
        is what the collector records for a step that resolved to a
        definition -- and its absence is what makes an undefined step's
        ``match`` empty.
    """
    match = step["match"]
    return events.new_step(
        keyword=step["keyword"],
        line=step["line"],
        name=step["name"],
        matched=bool(match.get("location")),
        match=match,
        result=step["result"],
    )


def _internal_element(element: JsonDict) -> JsonDict:
    """Rebuild the internal element behind one emitted element.

    :param element: An emitted Background occurrence or scenario.
    :returns: The internal element from
        :func:`app.reporting.events.new_element`.  A Background is rebuilt
        without ``id``, ``tags``, ``start_timestamp`` or ``after``, because it
        was emitted without them; a scenario's short-shape tags are rebuilt
        through :func:`app.reporting.events.scenario_tag`.
    """
    steps = [_internal_step(step) for step in element["steps"]]
    if element["type"] == events.ELEMENT_TYPE_BACKGROUND:
        return events.new_element(
            element_type=element["type"],
            keyword=element["keyword"],
            line=element["line"],
            name=element["name"],
            description=element["description"],
            steps=steps,
        )
    return events.new_element(
        element_type=element["type"],
        keyword=element["keyword"],
        line=element["line"],
        name=element["name"],
        description=element["description"],
        identifier=element["id"],
        start_timestamp=element["start_timestamp"],
        tags=[events.scenario_tag(tag["name"]) for tag in element.get("tags", ())],
        steps=steps,
    )


def _internal_feature(feature: JsonDict) -> JsonDict:
    """Rebuild the internal feature behind one emitted feature.

    :param feature: An emitted feature object.
    :returns: The internal feature from
        :func:`app.reporting.events.new_feature`.  ``path`` is the URI with its
        scheme removed -- the internal schema carries both so that no consumer
        performs string surgery -- and the long-shape feature tags are rebuilt
        through :func:`app.reporting.events.feature_tag`, preserving each tag's
        own declaration site rather than the feature's line.
    """
    uri = feature["uri"]
    scheme = paths.FILE_URI_SCHEME
    return events.new_feature(
        uri=uri,
        path=uri[len(scheme) :] if uri.startswith(scheme) else uri,
        identifier=feature["id"],
        line=feature["line"],
        name=feature["name"],
        description=feature["description"],
        keyword=feature["keyword"],
        tags=[
            events.feature_tag(
                tag["name"], tag["location"]["line"], tag["location"]["column"]
            )
            for tag in feature["tags"]
        ],
        elements=[_internal_element(element) for element in feature["elements"]],
    )


def internal_result_set(
    document: Sequence[JsonDict], *, dry_run: bool = False
) -> JsonDict:
    """Rebuild a whole internal result document behind an emitted one.

    :param document: An emitted Cucumber-JSON document -- a list of features.
    :param dry_run: Mirrored onto the result set, because the writer's
        dry-run status rule reads it from there.
    :returns: A result document from
        :func:`app.reporting.events.new_result_set`.  ``metadata`` is pinned to
        an empty mapping so that no platform probe runs: the metadata is not
        part of this artifact's contract and the writer never reads it.
    """
    return events.new_result_set(
        dry_run=dry_run,
        metadata={},
        features=[_internal_feature(feature) for feature in document],
    )


# --------------------------------------------------------------------------- #
# Walkers and small builders shared by the assertions below
# --------------------------------------------------------------------------- #


def iter_elements(document: Sequence[JsonDict]) -> Iterator[JsonDict]:
    """Every element of every feature, in emission order."""
    for feature in document:
        yield from feature["elements"]


def iter_steps(document: Sequence[JsonDict]) -> Iterator[JsonDict]:
    """Every step of every element, in emission order."""
    for element in iter_elements(document):
        yield from element["steps"]


def scenarios(document: Sequence[JsonDict]) -> list[JsonDict]:
    """Every scenario element, Background occurrences excluded."""
    return [
        element
        for element in iter_elements(document)
        if element["type"] == events.ELEMENT_TYPE_SCENARIO
    ]


def backgrounds(document: Sequence[JsonDict]) -> list[JsonDict]:
    """Every Background occurrence."""
    return [
        element
        for element in iter_elements(document)
        if element["type"] == events.ELEMENT_TYPE_BACKGROUND
    ]


def result_shape(result: JsonDict) -> tuple[tuple[str, ...], str]:
    """The key set of one ``result`` map, with its status.

    :param result: An emitted ``result`` map.
    :returns: ``(sorted keys, status)`` -- the pair the golden census counts,
        and the shape the omission rules are stated in.
    """
    return tuple(sorted(result)), result["status"]


def emitted_document(*features: JsonDict, dry_run: bool = False) -> list[JsonDict]:
    """Build one hand-written internal document and emit it.

    :param features: Internal feature objects.
    :param dry_run: Whether the run was a dry run.
    :returns: The emitted Cucumber-JSON document.
    """
    return build_cucumber_json(
        {"dry_run": dry_run, "features": list(features)}
    )


def probe_step(
    *,
    keyword: str = "Given",
    line: int = 4,
    name: str = "a probe step",
    matched: bool = True,
    location: str | None = "features.steps.probe_steps.a_probe_step",
    arguments: Sequence[JsonDict] | None = None,
    status: str | None = STATUS_PASSED,
    duration: Any = 1_000,
    error_message: str | None = None,
) -> JsonDict:
    """Build one internal step, with every field the writer reads exposed.

    :param keyword: The Gherkin keyword; the contract's trailing space is added
        by :func:`app.reporting.events.step_keyword`.
    :param line: The step's line.
    :param name: The step text.
    :param matched: Whether a definition resolved.
    :param location: The dotted path of the step function, or ``None`` for an
        undefined step, which yields an empty ``match``.
    :param arguments: ``match.arguments`` entries.
    :param status: The recorded status, or ``None`` to record none at all.
    :param duration: The recorded duration, passed through untouched so that a
        malformed value can be driven into the writer's coercion.  ``None``
        records no ``duration`` key at all, which is the same input the
        coercion sees for an explicit null.
    :param error_message: Failure text, or ``None`` for no error.
    :returns: The internal step object.
    """
    match: JsonDict = {}
    if location is not None:
        match["location"] = location
    if arguments is not None:
        match["arguments"] = [dict(argument) for argument in arguments]

    result: JsonDict = {}
    if status is not None:
        result["status"] = status
    if duration is not None:
        result["duration"] = duration
    if error_message is not None:
        result["error_message"] = error_message

    return events.new_step(
        keyword=keyword,
        line=line,
        name=name,
        matched=matched,
        match=match,
        result=result,
    )


def probe_scenario(
    *,
    keyword: str = "Scenario",
    line: int = 3,
    name: str = "a probe scenario",
    description: str = "",
    selected: bool = True,
    identifier: str | None = None,
    start_timestamp: str | None = None,
    tags: Sequence[JsonDict] | None = None,
    steps: Sequence[JsonDict] | None = None,
    after: Sequence[JsonDict] | None = None,
) -> JsonDict:
    """Build one internal scenario element.  Arguments mirror ``new_element``."""
    return events.new_element(
        element_type=events.ELEMENT_TYPE_SCENARIO,
        keyword=keyword,
        line=line,
        name=name,
        description=description,
        selected=selected,
        identifier=identifier,
        start_timestamp=start_timestamp,
        tags=tags,
        steps=steps if steps is not None else [probe_step()],
        after=after,
    )


def probe_background(
    *,
    line: int = 2,
    name: str = "a probe background",
    description: str = "",
    selected: bool = True,
    steps: Sequence[JsonDict] | None = None,
) -> JsonDict:
    """Build one internal Background occurrence."""
    return events.new_element(
        element_type=events.ELEMENT_TYPE_BACKGROUND,
        keyword=events.BACKGROUND_KEYWORD,
        line=line,
        name=name,
        description=description,
        selected=selected,
        steps=steps if steps is not None else [probe_step()],
    )


def probe_feature(
    *,
    filename: str = PROBE_FEATURE_FILENAME,
    identifier: str = "a-probe-feature",
    line: int = 1,
    name: str = "A probe feature",
    description: str = "",
    tags: Sequence[JsonDict] | None = None,
    elements: Sequence[JsonDict] | None = None,
) -> JsonDict:
    """Build one internal feature object carrying ``elements``."""
    return events.new_feature(
        uri=feature_uri(filename),
        path=feature_path(filename),
        identifier=identifier,
        line=line,
        name=name,
        description=description,
        tags=tags,
        elements=elements if elements is not None else [probe_scenario()],
    )


# --------------------------------------------------------------------------- #
# Fixtures local to this module
# --------------------------------------------------------------------------- #


@pytest.fixture(name="golden_internal")
def _golden_internal(golden_cucumber_normalized: Any) -> JsonDict:
    """The internal result document reconstructed from the golden report.

    :param golden_cucumber_normalized: The committed baseline with the feature
        directory reconciled, from ``tests/conftest.py``.
    :returns: The internal document the writer is asked to turn back into the
        baseline.
    """
    return internal_result_set(golden_cucumber_normalized)


@pytest.fixture(name="sample_emitted")
def _sample_emitted(sample_result_set: Any) -> list[JsonDict]:
    """The emitted Cucumber-JSON document for ``sample_results.json``.

    :param sample_result_set: The hand-built merged result set.
    :returns: The document every sample-driven assertion below reads.
    """
    return build_cucumber_json(sample_result_set)


# =========================================================================== #
# 1. The golden comparison
#
# The strongest test in the module, and the reason the other sections can be
# narrow: it asserts the *whole* document at once.
# =========================================================================== #


def test_golden_fixture_still_carries_its_measured_shape(
    golden_cucumber: Any,
) -> None:
    """The baseline itself, guarded before anything is compared against it.

    This module owns the three pinned fixtures and must never edit one to
    match a writer's output.  If this fails, the baseline changed rather than
    the writer, and every other assertion in this file was measured against
    something that no longer exists.
    """
    assert isinstance(golden_cucumber, list)
    assert len(golden_cucumber) == GOLDEN_FEATURE_COUNT

    feature = golden_cucumber[0]
    assert feature["name"] == GOLDEN_FEATURE_NAME
    assert feature["id"] == GOLDEN_FEATURE_ID
    assert feature["line"] == GOLDEN_FEATURE_LINE
    assert feature["description"] == GOLDEN_FEATURE_DESCRIPTION
    assert feature["tags"] == [
        {
            "name": GOLDEN_FEATURE_TAG_NAME,
            "type": "Tag",
            "location": {
                "line": GOLDEN_FEATURE_TAG_LINE,
                "column": GOLDEN_FEATURE_TAG_COLUMN,
            },
        }
    ]

    elements = feature["elements"]
    assert len(elements) == GOLDEN_ELEMENT_COUNT
    assert [element["type"] for element in elements] == [
        events.ELEMENT_TYPE_BACKGROUND,
        events.ELEMENT_TYPE_SCENARIO,
    ] * 4
    assert sum(len(element["steps"]) for element in elements) == GOLDEN_STEP_COUNT

    census: dict[tuple[tuple[str, ...], str], int] = {}
    for element in elements:
        for step in element["steps"]:
            key = result_shape(step["result"])
            census[key] = census.get(key, 0) + 1
    assert census == GOLDEN_RESULT_SHAPES

    # The reference run's teardown hook never fired, so nothing in the
    # baseline carries an after array or an embedding.  The writer's
    # after/embedding rules are therefore exercised from the sample, not here.
    assert not any("after" in element for element in elements)


def test_build_reproduces_the_golden_report_exactly(
    golden_internal: JsonDict, golden_cucumber_normalized: Any
) -> None:
    """The whole contract, in one comparison.

    An internal document reconstructed from the committed baseline, fed through
    the writer, must come back as the baseline.  A key renamed, a key emitted
    where the JVM omits it, an element reordered, a keyword stripped of its
    trailing space, a description trimmed, a tag shape conflated between the
    two levels, a duration turned into a float or an id mangled would each fail
    here -- which is why the remaining sections can assert one rule at a time
    without having to reassemble the whole document.
    """
    built = build_cucumber_json(golden_internal)

    assert comparable(built) == comparable(golden_cucumber_normalized)


def test_build_is_deterministic_and_leaves_its_input_alone(
    golden_internal: JsonDict,
) -> None:
    """Twice from one input yields one answer, and the input is untouched.

    The writer is documented as pure: it reads no clock, no working directory
    and no filesystem, and never mutates the document it is handed.  Both
    halves matter -- a mutating writer would make the four writers of one run
    order-dependent, and a non-deterministic one would make this whole module
    flaky rather than wrong.
    """
    before = copy.deepcopy(golden_internal)

    first = build_cucumber_json(golden_internal)
    second = build_cucumber_json(golden_internal)

    assert first == second
    assert golden_internal == before


# =========================================================================== #
# 2. Document shape: a list, always, and an empty one rather than nothing
# =========================================================================== #


def test_top_level_is_a_list(sample_emitted: list[JsonDict]) -> None:
    """A list of features -- not an object, and not ``{"features": ...}``.

    The publisher parses a JSON array.  An object at the top level would be
    read as zero features rather than as an error.
    """
    assert isinstance(sample_emitted, list)
    assert len(sample_emitted) == len(SAMPLE_FEATURE_FILENAMES)
    assert all(isinstance(feature, dict) for feature in sample_emitted)


@pytest.mark.parametrize(
    "result_set",
    [
        pytest.param(None, id="none"),
        pytest.param({}, id="empty-mapping"),
        pytest.param({"features": []}, id="no-features"),
        pytest.param({"features": None}, id="features-none"),
        pytest.param({"features": "not-a-list"}, id="features-a-string"),
        pytest.param([], id="a-list-not-a-document"),
        pytest.param("nonsense", id="a-string"),
        pytest.param(7, id="an-int"),
    ],
)
def test_nothing_to_report_yields_an_empty_list(result_set: Any) -> None:
    """Zero selection still produces a readable artifact: ``[]``.

    The exit contract requires all four artifacts to exist even when the tag
    expression selected nothing, so the publisher always has an input.  A
    ``null``, a ``{}`` or an exception here would each break that stage for a
    run that merely matched no scenario.
    """
    assert build_cucumber_json(result_set) == []


def test_render_of_an_empty_document_is_a_bare_array_and_a_newline() -> None:
    """``[]`` plus exactly one trailing newline, which is the reference shape."""
    assert render_cucumber_json(None) == "[]\n"


# =========================================================================== #
# 3. The key sets, level by level
#
# Asserted against the writer's own exported tuples rather than against
# literals repeated per assertion, so the contract has one statement.
# =========================================================================== #


def test_feature_keys_are_exactly_the_declared_set(
    sample_emitted: list[JsonDict],
) -> None:
    """Every feature carries all of FEATURE_KEYS and nothing else.

    The internal schema carries ``path`` and each element carries ``selected``;
    both are the port's own and neither belongs in the published artifact.  An
    extra key here is a field the publisher was never written to see.
    """
    assert len(sample_emitted) == len(SAMPLE_FEATURE_FILENAMES)
    for feature in sample_emitted:
        assert set(feature) == set(FEATURE_KEYS)


def test_background_elements_are_poorer_than_scenarios(
    sample_emitted: list[JsonDict],
) -> None:
    """A Background occurrence carries exactly BACKGROUND_ELEMENT_KEYS.

    No ``id``, no ``tags``, no ``start_timestamp`` and no ``after``: measured
    across all four occurrences of the baseline, and relied upon by the
    templates, which distinguish the two kinds of element by exactly this.
    """
    occurrences = backgrounds(sample_emitted)
    assert occurrences, "the sample carries Background occurrences"
    for element in occurrences:
        assert set(element) == set(BACKGROUND_ELEMENT_KEYS)
        assert element["type"] == events.ELEMENT_TYPE_BACKGROUND


def test_scenario_elements_carry_the_declared_keys(
    sample_emitted: list[JsonDict],
) -> None:
    """A scenario's keys lie within SCENARIO_ELEMENT_KEYS, and always hold six.

    ``tags`` is present only when the scenario has some and ``after`` only when
    a hook produced an attachment, so the declared tuple is the maximal set.
    The six unconditional keys are asserted separately, because a consumer that
    has to test for ``id`` or ``start_timestamp`` would be reading a different
    contract.
    """
    always = set(SCENARIO_ELEMENT_KEYS) - {"tags", "after"}
    elements = scenarios(sample_emitted)
    assert len(elements) == SAMPLE_SELECTED_SCENARIO_COUNT
    for element in elements:
        assert set(element) <= set(SCENARIO_ELEMENT_KEYS)
        assert always <= set(element)
        assert element["type"] == events.ELEMENT_TYPE_SCENARIO


def test_step_keys_are_exactly_the_declared_set_and_exclude_step_type(
    sample_emitted: list[JsonDict],
) -> None:
    """All five step keys, every time, and no ``step_type``.

    ``step_type`` is the engine's own field.  It is absent from the baseline,
    so emitting it would add a key the JVM never wrote; ``matched`` is the
    port's internal flag and must not leak either.
    """
    steps = list(iter_steps(sample_emitted))
    assert steps, "the sample carries steps"
    for step in steps:
        assert set(step) == set(STEP_KEYS)
        assert "step_type" not in step
        assert "matched" not in step


def test_internal_only_keys_are_dropped_even_when_the_input_carries_them() -> None:
    """A hand-built document's extra keys are dropped rather than copied.

    The previous test proves the emitted shape for an input that happens not to
    carry the engine's fields.  This one drives them in deliberately -- a
    ``step_type`` on the step and an unknown field at both levels, on top of
    the ``path`` and ``selected`` keys the internal builders always add -- and
    proves the writer builds its output rather than filtering its input.
    """
    step = probe_step()
    step["step_type"] = "given"
    step["extra_internal_field"] = "ignored"
    feature = probe_feature(elements=[probe_scenario(steps=[step])])
    feature["extra_internal_field"] = "ignored"

    document = emitted_document(feature)

    assert set(document[0]) == set(FEATURE_KEYS)
    emitted_step = document[0]["elements"][0]["steps"][0]
    assert set(emitted_step) == set(STEP_KEYS)


def test_every_line_and_duration_is_an_integer(
    sample_emitted: list[JsonDict],
) -> None:
    """Nanosecond integers and integer lines -- never a float, never a bool.

    The engine reports float seconds; the contract carries integer nanoseconds,
    and ``30202000000`` is 30.202 s.  A float duration would still parse as
    JSON and would still be wrong, which is exactly the silent class of failure
    this artifact is exposed to.
    """
    for feature in sample_emitted:
        assert type(feature["line"]) is int
    for element in iter_elements(sample_emitted):
        assert type(element["line"]) is int
    for step in iter_steps(sample_emitted):
        assert type(step["line"]) is int
        if "duration" in step["result"]:
            assert type(step["result"]["duration"]) is int
        for argument in step["match"].get("arguments", ()):
            if "offset" in argument:
                assert type(argument["offset"]) is int


# =========================================================================== #
# 4. Feature-level fields: the URI, the keyword, the line and the description
# =========================================================================== #


def test_feature_uri_is_copied_through_with_its_scheme(
    sample_emitted: list[JsonDict], sample_result_set: Any
) -> None:
    """``uri`` keeps its ``file:`` prefix and is not rebuilt.

    The URI is one string with a scheme and a repository-relative path.  The
    writer copies it; the feature-directory rewrite belongs to
    ``app.utils.paths.normalize_feature_uri`` on the *fixture* side of a
    comparison, never to this writer, so a writer that normalised on its own
    would produce output that no longer matched its input.
    """
    assert [feature["uri"] for feature in sample_emitted] == [
        feature["uri"] for feature in sample_result_set["features"]
    ]
    for feature, filename in zip(sample_emitted, SAMPLE_FEATURE_FILENAMES):
        assert feature["uri"].startswith(paths.FILE_URI_SCHEME)
        assert feature["uri"].endswith(filename)


def test_feature_uri_falls_back_to_the_scheme_plus_path() -> None:
    """A hand-built feature carrying only ``path`` still gets a URI.

    The internal schema carries both, but a document assembled by hand may
    carry one.  The derivation uses the scheme constant from
    ``app.utils.paths``, so it introduces no path literal -- and a feature with
    neither key yields an empty string rather than the text ``"None"``.
    """
    filename = PROBE_FEATURE_FILENAME
    with_path_only = probe_feature()
    del with_path_only["uri"]
    with_neither = probe_feature()
    del with_neither["uri"]
    del with_neither["path"]

    document = emitted_document(with_path_only)
    assert document[0]["uri"] == f"{paths.FILE_URI_SCHEME}{feature_path(filename)}"

    document = emitted_document(with_neither)
    assert document[0]["uri"] == ""


def test_feature_keyword_and_line_come_from_the_source(
    golden_internal: JsonDict,
) -> None:
    """``keyword`` is ``Feature`` and ``line`` is the ``Feature:`` line.

    The tag sits one line above the feature in the baseline, so a writer that
    reported the tag's line -- the easy mistake, since the tag is the first
    thing in the file -- would be off by one on every tagged feature.
    """
    feature = build_cucumber_json(golden_internal)[0]

    assert feature["keyword"] == GOLDEN_FEATURE_KEYWORD
    assert feature["line"] == GOLDEN_FEATURE_LINE
    assert feature["tags"][0]["location"]["line"] == GOLDEN_FEATURE_TAG_LINE


def test_feature_keyword_falls_back_to_the_gherkin_keyword() -> None:
    """A hand-built feature with no keyword still carries one."""
    feature = probe_feature()
    del feature["keyword"]

    assert emitted_document(feature)[0]["keyword"] == events.FEATURE_KEYWORD


def test_description_preserves_indentation_and_is_empty_rather_than_absent(
    golden_internal: JsonDict, sample_emitted: list[JsonDict]
) -> None:
    """``description`` keeps its leading spaces, and is ``""`` when there is none.

    Both halves are contract: the baseline's ``"  Account is: PosManager"``
    carries two leading spaces that a trimming writer would drop, and an absent
    key reads to a consumer as a shape change while an empty string reads as
    "no description".
    """
    feature = build_cucumber_json(golden_internal)[0]
    assert feature["description"] == GOLDEN_FEATURE_DESCRIPTION

    for element in iter_elements(sample_emitted):
        assert "description" in element
        assert isinstance(element["description"], str)

    without = probe_feature(elements=[probe_scenario()])
    del without["description"]
    document = emitted_document(without)
    assert document[0]["description"] == ""
    assert document[0]["elements"][0]["description"] == ""


# =========================================================================== #
# 5. The two tag shapes, and their opposite emptiness rules
#
# Conflating the levels is the single most likely way to break this artifact,
# which is why each half is asserted on its own as well as together.
# =========================================================================== #


def test_feature_tags_use_the_long_shape(golden_internal: JsonDict) -> None:
    """name, type and the tag's own location -- at feature level only."""
    feature = build_cucumber_json(golden_internal)[0]

    assert feature["tags"] == [
        {
            "name": GOLDEN_FEATURE_TAG_NAME,
            "type": "Tag",
            "location": {
                "line": GOLDEN_FEATURE_TAG_LINE,
                "column": GOLDEN_FEATURE_TAG_COLUMN,
            },
        }
    ]


def test_feature_tags_are_emitted_unconditionally(
    sample_emitted: list[JsonDict],
) -> None:
    """An untagged feature carries ``"tags": []`` -- the key is never omitted.

    ``createFeatureMap`` puts the key with no emptiness guard.  Five of the ten
    features declare no tag, so this is the common case, and it is the exact
    opposite of the scenario-level rule asserted below.
    """
    for feature in sample_emitted:
        assert "tags" in feature
        assert isinstance(feature["tags"], list)

    untagged = [
        sample_emitted[SAMPLE_CONTACT_INDEX],
        sample_emitted[SAMPLE_INVENTORY_INDEX],
        sample_emitted[SAMPLE_SALES_INDEX],
    ]
    assert [feature["tags"] for feature in untagged] == [[], [], []]
    assert sample_emitted[SAMPLE_CRM_INDEX]["tags"] != []


def test_scenario_tags_use_the_short_shape(
    sample_emitted: list[JsonDict],
) -> None:
    """A scenario tag is ``{"name": ...}`` and carries nothing else.

    The same ``@Smoke`` tag carries ``type`` and ``location`` at feature level
    and neither here.  A writer that reused one builder for both levels would
    pass a golden comparison on the feature and fail the JVM on the scenario.
    """
    crm_scenarios = scenarios([sample_emitted[SAMPLE_CRM_INDEX]])
    assert crm_scenarios
    for element in crm_scenarios:
        assert element["tags"] == [{"name": GOLDEN_FEATURE_TAG_NAME}]


def test_scenario_tags_are_omitted_entirely_when_empty(
    sample_emitted: list[JsonDict],
) -> None:
    """No tags means no key -- never ``[]``.

    The JVM guards the scenario-level key with ``if
    (!testCase.getTags().isEmpty())``.  With five untagged features in the
    suite, emitting ``[]`` would diverge from the reference on most of it.
    """
    untagged = scenarios(
        [
            sample_emitted[SAMPLE_CONTACT_INDEX],
            sample_emitted[SAMPLE_INVENTORY_INDEX],
            sample_emitted[SAMPLE_SALES_INDEX],
        ]
    )
    assert untagged
    for element in untagged:
        assert "tags" not in element

    tagged = scenarios([sample_emitted[SAMPLE_CRM_INDEX]])
    assert tagged
    for element in tagged:
        assert element["tags"]


def test_background_occurrences_carry_no_tags_key_at_all(
    sample_emitted: list[JsonDict],
) -> None:
    """Even under a tagged feature, a Background has no ``tags`` key.

    The Crm feature declares ``@Smoke`` and its four Background occurrences
    still carry neither the key nor an empty list, because the JVM's
    Background map has no tag field to fill.
    """
    occurrences = backgrounds([sample_emitted[SAMPLE_CRM_INDEX]])
    assert occurrences
    for element in occurrences:
        assert "tags" not in element
        assert "id" not in element
        assert "start_timestamp" not in element
        assert "after" not in element


def test_feature_level_tags_arrive_as_scenario_tags(
    sample_emitted: list[JsonDict],
) -> None:
    """Propagation happened upstream, and the writer does not repeat it.

    ``@Smoke`` is declared once, at the Crm feature, and every one of its
    scenario elements carries it in the short shape.  The collector propagates,
    so the writer must neither drop the propagated tag nor add the feature's
    tags a second time -- a scenario carrying ``@Smoke`` twice would be as
    wrong as one carrying it not at all.
    """
    crm = sample_emitted[SAMPLE_CRM_INDEX]
    feature_tag_names = [tag["name"] for tag in crm["tags"]]
    assert feature_tag_names == [GOLDEN_FEATURE_TAG_NAME]

    for element in scenarios([crm]):
        assert [tag["name"] for tag in element["tags"]] == feature_tag_names


@pytest.mark.parametrize(
    ("declared", "expected_name"),
    [
        pytest.param("@Smoke", "@Smoke", id="mapping-keeps-the-at-sign"),
        pytest.param("Smoke", "@Smoke", id="mapping-gains-a-missing-at-sign"),
    ],
)
def test_tag_names_keep_their_leading_at_sign(
    declared: str, expected_name: str
) -> None:
    """Both levels keep the ``@``, and add it when the engine stripped it.

    behave reports tag names without the ``@`` and the JVM writes them with it,
    so the leading character is the writer's to guarantee at both levels.
    """
    feature = probe_feature(
        tags=[{"name": declared, "location": {"line": 1, "column": 1}}],
        elements=[probe_scenario(tags=[{"name": declared}])],
    )

    document = emitted_document(feature)

    assert document[0]["tags"][0]["name"] == expected_name
    assert document[0]["elements"][0]["tags"][0]["name"] == expected_name


def test_a_bare_string_tag_is_accepted_and_widened_at_both_levels() -> None:
    """A hand-built document may carry tags as plain strings.

    The internal builders require mappings, so this case is reachable only from
    a document assembled by hand -- and the writer widens rather than drops,
    because losing a tag silently would change which scenarios a reader
    believes were selected.  A feature-level string has no location of its own,
    so the feature's line is recorded for it; a scenario-level string becomes
    the short shape.
    """
    feature = probe_feature(line=7)
    feature["tags"] = ["Smoke", "@Regression", "", {"name": ""}, 17]
    feature["elements"] = [
        probe_scenario(line=9, tags=[{"name": "@Kept"}]),
    ]
    feature["elements"][0]["tags"] = ["Wip", "", {"name": "@Kept"}, None]

    document = emitted_document(feature)

    assert document[0]["tags"] == [
        {"name": "@Smoke", "type": "Tag", "location": {"line": 7, "column": 1}},
        {"name": "@Regression", "type": "Tag", "location": {"line": 7, "column": 1}},
    ]
    assert document[0]["elements"][0]["tags"] == [
        {"name": "@Wip"},
        {"name": "@Kept"},
    ]


def test_a_feature_tag_keeps_its_own_declaration_site() -> None:
    """A tag's ``location`` is the tag's, and only defaults to the feature's.

    Two tags on one feature may sit on different lines and in different
    columns, and the baseline's single tag proves the line is not the
    feature's.  A tag mapping that carries a partial location -- a line but no
    column -- keeps the line it declared.
    """
    feature = probe_feature(
        line=12,
        tags=[
            {"name": "@First", "location": {"line": 10, "column": 1}},
            {"name": "@Second", "location": {"line": 11, "column": 9}},
            {"name": "@PartialLocation", "location": {"line": 11}},
            {"name": "@NoLocation"},
        ],
    )

    tags = emitted_document(feature)[0]["tags"]

    assert [tag["location"] for tag in tags] == [
        {"line": 10, "column": 1},
        {"line": 11, "column": 9},
        {"line": 11, "column": 1},
        {"line": 12, "column": 1},
    ]
    assert {tag["type"] for tag in tags} == {"Tag"}


# =========================================================================== #
# 6. Scenario ids: the slug rule, Examples rows, and preserved collisions
# =========================================================================== #


def test_plain_scenario_ids_match_the_baseline(golden_internal: JsonDict) -> None:
    """``<feature-slug>;<scenario-slug>`` for each of the three plain scenarios."""
    elements = scenarios(build_cucumber_json(golden_internal))
    plain = [
        element["id"]
        for element in elements
        if element["keyword"] != GOLDEN_OUTLINE_KEYWORD
    ]

    assert plain == list(GOLDEN_PLAIN_SCENARIO_IDS)


def test_the_outline_row_id_carries_the_examples_slug_and_its_position(
    golden_internal: JsonDict,
) -> None:
    """An Examples row appends the block's slug and the row's position.

    The header counts as 1, so the baseline's first data row is 2.  The element
    also keeps the *data row's* line while its steps keep the template's --
    that asymmetry is measured and is not normalised.
    """
    elements = scenarios(build_cucumber_json(golden_internal))
    outline = [
        element
        for element in elements
        if element["keyword"] == GOLDEN_OUTLINE_KEYWORD
    ]

    assert len(outline) == 1
    assert outline[0]["id"] == GOLDEN_OUTLINE_ROW_ID
    assert outline[0]["line"] == GOLDEN_SCENARIO_LINES[1]
    assert [step["line"] for step in outline[0]["steps"]] == list(
        GOLDEN_OUTLINE_STEP_LINES
    )


def test_both_sample_outline_rows_number_from_the_header(
    sample_emitted: list[JsonDict],
) -> None:
    """First data row 2, second data row 3 -- and the name loses no suffix.

    The engine annotates an outline element's name with ``" -- @1.1
    Examples"``; no element name in the baseline contains ``" -- "``, so the
    annotation is dropped upstream and must not reappear here.
    """
    rows = [
        element
        for element in scenarios([sample_emitted[SAMPLE_CRM_INDEX]])
        if element["keyword"] == GOLDEN_OUTLINE_KEYWORD
    ]

    assert [row["id"] for row in rows] == [
        GOLDEN_OUTLINE_ROW_ID,
        GOLDEN_OUTLINE_ROW_ID.replace(";2", ";3"),
    ]
    assert {row["name"] for row in rows} == {"User can change information in dashboard"}
    assert all(" -- " not in row["name"] for row in rows)


def test_a_supplied_id_is_copied_through_verbatim(
    sample_emitted: list[JsonDict],
) -> None:
    """Whatever the collector computed is what is emitted -- unrepaired.

    The sample's two Sales outline rows carry an empty Examples segment and so
    a doubled separator.  That is the *generator's* behaviour, which
    ``app/reporting/events.py``'s owner is changing under review finding F09;
    the writer's rule is only that a supplied ``id`` is copied through, because
    an id the writer "fixed" would no longer match the one the HTML reports and
    the publisher key their detail pages on.
    """
    sales_rows = [
        element
        for element in scenarios([sample_emitted[SAMPLE_SALES_INDEX]])
        if element["keyword"] == GOLDEN_OUTLINE_KEYWORD
    ]

    assert [row["id"] for row in sales_rows] == list(SAMPLE_UNNAMED_EXAMPLES_ROW_IDS)
    assert all(";;" in row["id"] for row in sales_rows)


def test_an_absent_id_is_derived_from_the_feature_and_scenario_names() -> None:
    """A hand-built element with no ``id`` still gets the plain form.

    Only a document that did not come from the collector reaches this: a plain
    scenario's id is derivable from the two names, while an Examples row's is
    not, because the block name and row position live on neither element.
    """
    scenario_name = "User can create pipeline in the displayed dashboard"
    feature = probe_feature(
        name=GOLDEN_FEATURE_NAME,
        elements=[probe_scenario(name=scenario_name)],
    )
    feature["elements"][0]["id"] = ""

    element = emitted_document(feature)[0]["elements"][0]

    assert element["id"] == GOLDEN_PLAIN_SCENARIO_IDS[0]
    assert element["id"] == scenario_element_id(GOLDEN_FEATURE_NAME, scenario_name)


def test_duplicate_feature_ids_are_preserved_rather_than_disambiguated(
    sample_emitted: list[JsonDict],
) -> None:
    """Contact and Inventory share an ``id``, and both are still emitted.

    Both files are titled "Testinium app Inventory feature", so the slug
    collides.  The collision is source behaviour the port preserves (AAP
    0.2.2), and it is precisely why ``app/web/routes.py`` keys its routes on a
    feature's zero-based position in this list rather than on its ``id`` (AAP
    0.3.1).  A writer that appended a discriminator would silently "fix" the
    source and break the reference comparison.
    """
    contact = sample_emitted[SAMPLE_CONTACT_INDEX]
    inventory = sample_emitted[SAMPLE_INVENTORY_INDEX]

    assert contact["id"] == inventory["id"] == SAMPLE_DUPLICATE_FEATURE_ID
    assert contact["uri"] != inventory["uri"]
    assert contact["uri"].endswith(SAMPLE_FEATURE_FILENAMES[SAMPLE_CONTACT_INDEX])
    assert inventory["uri"].endswith(SAMPLE_FEATURE_FILENAMES[SAMPLE_INVENTORY_INDEX])
    colliding = [
        feature
        for feature in sample_emitted
        if feature["id"] == SAMPLE_DUPLICATE_FEATURE_ID
    ]
    assert len(colliding) == 2


def test_the_second_duplicate_pair_also_collides_and_is_kept() -> None:
    """Login and Notes share a title too, down to their scenario ids.

    The second of the two pairs AAP 0.6 records is absent from
    ``sample_results.json``, which carries four features, so it is driven here
    from the two titles: ``Login.feature:2`` and ``Notes.feature:1`` are both
    "Testinium app login feature".  Two features and two scenarios collide,
    both entries survive, and only list position tells them apart.
    """
    scenario_name = "User can login"
    document = emitted_document(
        probe_feature(
            filename=PROBE_FEATURE_FILENAME,
            identifier=convert_to_id(SHARED_FEATURE_TITLE),
            name=SHARED_FEATURE_TITLE,
            elements=[probe_scenario(name=scenario_name, identifier=None)],
        ),
        probe_feature(
            filename=OTHER_PROBE_FEATURE_FILENAME,
            identifier=convert_to_id(SHARED_FEATURE_TITLE),
            name=SHARED_FEATURE_TITLE,
            elements=[probe_scenario(name=scenario_name, identifier=None)],
        ),
    )

    assert len(document) == 2
    assert document[0]["id"] == document[1]["id"]
    assert (
        document[0]["elements"][0]["id"]
        == document[1]["elements"][0]["id"]
        == scenario_element_id(SHARED_FEATURE_TITLE, scenario_name)
    )
    assert document[0]["uri"] != document[1]["uri"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param(
            GOLDEN_FEATURE_NAME, GOLDEN_FEATURE_ID, id="spaces-and-case"
        ),
        pytest.param(
            "User can change any user's information",
            "user-can-change-any-user-s-information",
            id="apostrophe",
        ),
        pytest.param(
            "SalesManager's username and password",
            "salesmanager-s-username-and-password",
            id="apostrophe-mid-word",
        ),
        pytest.param(
            'Verify that the user\'s search finds his name "Lucas" from search bar.',
            'verify-that-the-user-s-search-finds-his-name-"lucas"-from-search-bar.',
            id="quotation-marks-and-trailing-period-survive",
        ),
        pytest.param(
            ".... app Sales feature", "....-app-sales-feature", id="leading-periods"
        ),
        pytest.param(
            "Account is: PosManager", "account-is:-posmanager", id="colon-survives"
        ),
        pytest.param("snake_case_name", "snake-case-name", id="underscores"),
        pytest.param(
            "Verify that after creating a new customer, the page title includes"
            " the customer name.",
            "verify-that-after-creating-a-new-customer--the-page-title-includes"
            "-the-customer-name.",
            id="comma-then-space-yields-two-dashes",
        ),
        pytest.param("Wow! Amazing!", "wow--amazing-", id="exclamation-marks"),
        pytest.param("tab\tand\nnewline", "tab-and-newline", id="tab-and-newline"),
        pytest.param(
            "  leading and trailing  ",
            "--leading-and-trailing--",
            id="outer-whitespace-is-not-trimmed",
        ),
        pytest.param(
            "Inventory ---> Products ---> Create",
            "inventory---->-products---->-create",
            id="existing-dashes-are-untouched",
        ),
        pytest.param("(parenthesised)", "(parenthesised)", id="parentheses-survive"),
        pytest.param("", "", id="empty"),
        pytest.param(None, "", id="none"),
    ],
)
def test_convert_to_id_replaces_exactly_five_character_classes(
    text: str | None, expected: str
) -> None:
    """The slug rule is ``[\\s'_,!] -> "-"`` then lower-case, and nothing more.

    Read from the generator's own bytecode, and deliberately narrow: periods,
    colons, quotation marks and parentheses survive verbatim.  AAP 0.6 warns
    that the rule "is not safe to generalize" from the baseline's two examples,
    which is why every class the suite's real names contain is pinned here --
    the apostrophes of the Crm and Sales titles, the quotation marks and
    trailing period of a Sales scenario, and the leading dots of
    ``Sales.feature``'s own title.  A tidier slug would change every id in the
    artifact and every PrettyReports filename derived from one.
    """
    assert convert_to_id(text) == expected


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        pytest.param(
            ("A feature", "A scenario"), "a-feature;a-scenario", id="plain-scenario"
        ),
        pytest.param(
            ("A feature", "An outline", "Expected name", 1),
            "a-feature;an-outline;expected-name;2",
            id="first-data-row-is-2",
        ),
        pytest.param(
            ("A feature", "An outline", "Expected name", 2),
            "a-feature;an-outline;expected-name;3",
            id="second-data-row-is-3",
        ),
        pytest.param(
            ("A feature", "An outline", None, 1),
            "a-feature;an-outline;;2",
            id="unnamed-examples-doubles-the-separator",
        ),
        pytest.param(
            ("A feature", "An outline", "", 1),
            "a-feature;an-outline;;2",
            id="empty-examples-name-behaves-as-unnamed",
        ),
        pytest.param((None, None), ";", id="both-names-absent"),
    ],
)
def test_scenario_element_id_composes_the_jvm_recursion(
    arguments: tuple[Any, ...], expected: str
) -> None:
    """``calculateId`` walks the AST upwards, and this is the shape it yields.

    The row index is the engine's one-based index within the Examples body and
    the JVM counts the header, so the emitted position is one higher.  An
    unnamed block genuinely contributes an empty segment -- that is the
    source's behaviour, not a defect to paper over here.
    """
    assert scenario_element_id(*arguments) == expected


# =========================================================================== #
# 7. Selection, interleaving and order
# =========================================================================== #


def test_element_order_is_background_then_its_own_scenario(
    sample_emitted: list[JsonDict],
) -> None:
    """Each Background occurrence sits immediately in front of its scenario.

    ``handleTestCaseStarted`` appends the Background element first and then the
    scenario's, once per test case, so the occurrence is repeated in full
    before every scenario and all occurrences carry the Background's own line.
    A consumer reads the pairing positionally; reordering would reattach every
    Background's results to the wrong scenario.
    """
    crm = sample_emitted[SAMPLE_CRM_INDEX]
    types = [element["type"] for element in crm["elements"]]

    assert types == [
        events.ELEMENT_TYPE_BACKGROUND,
        events.ELEMENT_TYPE_SCENARIO,
    ] * 4
    assert {
        element["line"]
        for element in crm["elements"]
        if element["type"] == events.ELEMENT_TYPE_BACKGROUND
    } == {GOLDEN_BACKGROUND_LINE}


def test_features_and_scenarios_keep_source_order_and_are_never_sorted(
    sample_emitted: list[JsonDict],
) -> None:
    """Nothing is sorted, alphabetically or otherwise.

    ``sortingMethod: 'ALPHABETICAL'`` (``Jenkins:15``) is a publisher *display*
    option and imposes nothing on the artifact.  The sample's feature order is
    the merge's -- Contact, Crm, Inventory, Sales -- and each feature's
    scenarios stay in line order, which for the Sales feature means 12, 32, 33
    and is not what sorting by name would give.
    """
    assert [
        feature["uri"].rsplit("/", 1)[-1] for feature in sample_emitted
    ] == list(SAMPLE_FEATURE_FILENAMES)

    for feature in sample_emitted:
        lines = [element["line"] for element in scenarios([feature])]
        assert lines == sorted(lines)

    sales_names = [
        element["name"]
        for element in scenarios([sample_emitted[SAMPLE_SALES_INDEX]])
    ]
    assert sales_names != sorted(sales_names), (
        "the Sales scenarios would reorder under alphabetical sorting, "
        "so source order is being asserted rather than coinciding with it"
    )


def test_a_non_selected_scenario_is_dropped(
    sample_emitted: list[JsonDict], sample_result_set: Any
) -> None:
    """The ``@wip`` scenario the expression excluded does not appear.

    The JVM never starts a scenario the tag expression excluded, so it never
    writes one, whereas the engine announces it as skipped.  The sample's
    result set carries it with ``selected: false``; the artifact must not.
    """
    internal_scenarios = [
        element
        for feature in sample_result_set["features"]
        for element in feature["elements"]
        if element["type"] == events.ELEMENT_TYPE_SCENARIO
    ]
    assert any(
        element["name"] == SAMPLE_UNSELECTED_SCENARIO_NAME
        for element in internal_scenarios
    ), "the sample must still carry the unselected scenario"

    emitted_names = [element["name"] for element in scenarios(sample_emitted)]
    assert SAMPLE_UNSELECTED_SCENARIO_NAME not in emitted_names
    assert len(emitted_names) == SAMPLE_SELECTED_SCENARIO_COUNT
    assert all(
        element["line"] != SAMPLE_UNSELECTED_SCENARIO_LINE
        for element in scenarios(sample_emitted)
    )


def test_a_dropped_scenario_takes_its_background_occurrence_with_it() -> None:
    """The Background emitted *for* a test case shares that case's fate.

    An occurrence left behind would describe a test case the artifact does not
    contain, and every consumer pairs the two positionally.  The middle
    scenario here is excluded, so exactly one Background-plus-scenario unit
    disappears and the surviving pairs stay adjacent.
    """
    feature = probe_feature(
        elements=[
            probe_background(line=2),
            probe_scenario(line=5, name="selected first", identifier="f;first"),
            probe_background(line=2),
            probe_scenario(
                line=9, name="excluded", identifier="f;excluded", selected=False
            ),
            probe_background(line=2),
            probe_scenario(line=13, name="selected last", identifier="f;last"),
        ]
    )

    elements = emitted_document(feature)[0]["elements"]

    assert [
        (element["type"], element.get("name")) for element in elements
    ] == [
        (events.ELEMENT_TYPE_BACKGROUND, "a probe background"),
        (events.ELEMENT_TYPE_SCENARIO, "selected first"),
        (events.ELEMENT_TYPE_BACKGROUND, "a probe background"),
        (events.ELEMENT_TYPE_SCENARIO, "selected last"),
    ]


def test_a_feature_left_with_no_selected_scenario_is_omitted_entirely() -> None:
    """No test case started means no feature map -- not an empty one.

    The JVM creates a feature map only when a test case from that file starts.
    That is what reduces the ten-feature suite to the baseline's single feature
    under the default ``@Smoke`` expression, so an empty feature entry here
    would put nine features the JVM never wrote into the published report.
    """
    document = emitted_document(
        probe_feature(
            elements=[
                probe_background(),
                probe_scenario(selected=False),
            ]
        ),
        probe_feature(
            filename=OTHER_PROBE_FEATURE_FILENAME,
            identifier="a-surviving-feature",
            name="A surviving feature",
        ),
    )

    assert len(document) == 1
    assert document[0]["id"] == "a-surviving-feature"


def test_a_feature_carrying_only_backgrounds_is_omitted() -> None:
    """A Background occurrence with no scenario represents no test case."""
    assert emitted_document(probe_feature(elements=[probe_background()])) == []


def test_a_missing_selected_flag_counts_as_selected() -> None:
    """An absent or null flag means "this ran".

    Over-reporting a scenario is recoverable; silently dropping a real result
    is not, which is the same judgement the collector makes when the engine
    cannot answer.
    """
    absent = probe_scenario(name="no flag at all", identifier="f;absent")
    del absent["selected"]
    explicit_null = probe_scenario(name="null flag", identifier="f;null")
    explicit_null["selected"] = None

    elements = emitted_document(
        probe_feature(elements=[absent, explicit_null])
    )[0]["elements"]

    assert [element["name"] for element in elements] == [
        "no flag at all",
        "null flag",
    ]


def test_a_feature_whose_elements_are_malformed_is_omitted() -> None:
    """A non-list ``elements`` value yields no feature rather than an exception.

    A writer failure is fatal to the run's exit class, so a malformed nesting
    degrades to the emptiest legal shape.  Dropping the feature is that shape
    here: there is no test case to describe.
    """
    feature = probe_feature()
    feature["elements"] = "not a list"

    assert emitted_document(feature) == []



# =========================================================================== #
# 8. Steps: the keyword's trailing space, and the three-clause result rule
# =========================================================================== #


def test_step_keyword_carries_exactly_one_trailing_space(
    golden_internal: JsonDict, sample_emitted: list[JsonDict]
) -> None:
    """``"Given "``, ``"And "``, ``"Then "`` -- measured, and load-bearing.

    The engine's keyword carries no trailing space and the JVM's does.  The
    space is added once, by ``app.reporting.events.step_keyword``, and the
    writer copies the keyword through; a writer that stripped or doubled it
    would differ from the reference on all nineteen steps of the baseline.
    """
    for step in iter_steps(build_cucumber_json(golden_internal)):
        keyword = step["keyword"]
        assert keyword.endswith(" ")
        assert not keyword.endswith("  ")
        assert keyword.strip() in {"Given", "When", "And", "Then"}

    for step in iter_steps(sample_emitted):
        assert step["keyword"].endswith(" ")
        assert not step["keyword"].endswith("  ")


def test_status_is_always_present_and_lower_case(
    sample_emitted: list[JsonDict],
) -> None:
    """Every result carries a ``status``, drawn from the emitted vocabulary.

    A consumer never has to test for the key, and every name it reads is one
    the publisher recognises -- an invented token would be silently mis-read
    rather than rejected.
    """
    for step in iter_steps(sample_emitted):
        status = step["result"]["status"]
        assert status in CUCUMBER_STATUSES
        assert status == status.lower()


def test_a_zero_duration_is_omitted_whatever_the_status(
    sample_emitted: list[JsonDict],
) -> None:
    """``createResultMap`` emits ``duration`` only when it is non-zero.

    This is the rule that actually governs the key, and the sample exercises it
    on both of the statuses that reach zero: its skipped steps and its
    undefined step all record ``duration: 0`` internally, and none of them may
    carry the key.  A ``"duration": 0`` in the artifact would tell the
    publisher a step was measured at zero rather than never measured.
    """
    # Guarded, so that a fixture edit cannot turn this into a vacuous pass:
    # both zero-duration statuses must actually occur in the sample.
    statuses_present = {step["result"]["status"] for step in iter_steps(sample_emitted)}
    assert {"skipped", STATUS_UNDEFINED} <= statuses_present

    for step in iter_steps(sample_emitted):
        if step["result"]["status"] in {"skipped", STATUS_UNDEFINED}:
            assert "duration" not in step["result"]


def test_a_skipped_step_with_a_measured_duration_keeps_its_status_and_scale(
    golden_internal: JsonDict,
) -> None:
    """The one cell where the baseline and the AAP's prose disagree.

    The committed baseline carries a skipped step measured at 1,000,000
    nanoseconds, while AAP 0.6's prose describes a skipped result as ``{"status":
    "skipped"}`` with no ``duration`` key, and this file's own specification
    settles what a test may say about it: *"Do not assert that a skipped step
    has no duration ... field presence is per-invocation, not per-status."*  The
    writer's behaviour there is additionally under revision by its owner
    (review finding F05), so the *presence* of the key is asserted in neither
    direction here -- :func:`_reconcile_skipped_duration` removes it from both
    sides of every comparison in this module for exactly that reason.

    What is not in doubt, and is asserted, is everything else about the cell:
    the status stays ``skipped`` rather than being folded into another token,
    and a duration that *is* emitted is the exact integer nanosecond count the
    run recorded -- never a float, never rounded to seconds, never zero.  Were
    this to fail, a skipped step would either be reported to the publisher as
    some other outcome or would carry a duration on a different scale from
    every other step in the artifact.
    """
    measured = [
        step["result"]
        for element in golden_internal["features"][0]["elements"]
        for step in element["steps"]
        if step["result"].get("status") == "skipped"
        and step["result"].get("duration")
    ]
    # Guarded, so a fixture edit cannot turn this into a vacuous pass.
    assert len(measured) == 1, "the baseline's one measured skipped step is gone"
    assert measured[0]["duration"] == GOLDEN_MEASURED_SKIPPED_DURATION

    emitted = [
        step["result"]
        for element in build_cucumber_json(golden_internal)[0]["elements"]
        for step in element["steps"]
        if step["result"]["status"] == "skipped"
    ]

    expected_skipped = (
        GOLDEN_RESULT_SHAPES[(("status",), "skipped")]
        + GOLDEN_RESULT_SHAPES[(("duration", "status"), "skipped")]
    )
    assert len(emitted) == expected_skipped
    carrying = [result for result in emitted if "duration" in result]
    assert len(carrying) <= 1, (
        "a duration appeared on a skipped result that recorded none: "
        f"{emitted}"
    )
    for result in carrying:
        assert result["duration"] == GOLDEN_MEASURED_SKIPPED_DURATION
        assert isinstance(result["duration"], int)
        assert not isinstance(result["duration"], bool)
    for result in emitted:
        assert result["status"] == "skipped"


def test_the_skipped_duration_rule_is_applied_uniformly(
    tmp_artifact_root: Path,
) -> None:
    """Whichever rule governs the contested cell, it governs every instance.

    The companion to the test above, and the part that does not depend on
    which way the cell is settled.  Two skipped steps recording the *same*
    non-zero duration must come out with the *same* result shape: either both
    carry it, which is what the baseline shows, or neither does, which is what
    AAP 0.6's prose describes.  A writer that emitted one and dropped the other
    would be reporting two identical observations differently, and the
    publisher -- which reads this file and nothing else -- would have no way to
    tell which of the two it was being told about.

    Asserted through the written artifact rather than the in-memory document,
    so that the rule is proven where the publisher actually reads it.  The
    zero-duration half of the rule is not in doubt and is pinned separately
    from the sample, where every skipped step records zero.
    """
    steps = [
        probe_step(
            line=line,
            name=f"a skipped step on line {line}",
            status="skipped",
            duration=GOLDEN_MEASURED_SKIPPED_DURATION,
        )
        for line in (11, 12)
    ]
    document = {
        "features": [probe_feature(elements=[probe_scenario(steps=steps)])]
    }

    written = write_cucumber_json(document, base=tmp_artifact_root)
    emitted = json.loads(written.read_text(encoding="utf-8"))[0]["elements"][0]

    assert [step["result"]["status"] for step in emitted["steps"]] == [
        "skipped",
        "skipped",
    ]
    shapes = {
        tuple(sorted(step["result"])) for step in emitted["steps"]
    }
    assert len(shapes) == 1, (
        "two identical skipped observations were reported with different "
        f"result shapes: {[step['result'] for step in emitted['steps']]}"
    )
    for step in emitted["steps"]:
        if "duration" in step["result"]:
            assert step["result"]["duration"] == (
                GOLDEN_MEASURED_SKIPPED_DURATION
            )


def test_a_failed_step_carries_both_a_duration_and_a_message(
    sample_emitted: list[JsonDict],
) -> None:
    """Failure is data: a measured duration and the message, both emitted.

    ``testFailureIgnore=true`` and all six publisher thresholds are ``-1``, so
    a failure is something to serialise faithfully rather than something to
    react to.  Dropping either field would leave the published report unable to
    say what failed or how long it took.
    """
    failed = [
        step
        for step in iter_steps(sample_emitted)
        if step["result"]["status"] == "failed"
    ]
    assert failed, "the sample carries failed steps"

    for step in failed:
        assert step["result"]["duration"] > 0
        assert step["result"]["error_message"]
        assert result_shape(step["result"]) == (
            ("duration", "error_message", "status"),
            "failed",
        )


def test_a_passing_step_carries_no_error_message(
    sample_emitted: list[JsonDict],
) -> None:
    """``error_message`` is omitted rather than emitted empty or null.

    A ``null`` or ``""`` would render as an empty error box in both HTML
    reports and would read to the publisher as a step that failed without
    saying why.
    """
    for step in iter_steps(sample_emitted):
        if step["result"]["status"] != "failed":
            assert "error_message" not in step["result"]


@pytest.mark.parametrize(
    ("duration", "expected"),
    [
        pytest.param(30_202_000_000, 30_202_000_000, id="nanoseconds-pass-through"),
        pytest.param(0, None, id="zero-is-omitted"),
        pytest.param(None, None, id="no-duration-recorded-is-omitted"),
        pytest.param(True, None, id="a-bool-is-not-a-duration"),
        pytest.param(2.7, 2, id="a-float-is-truncated"),
        pytest.param("1500", 1500, id="a-numeric-string-is-read"),
        pytest.param(" 1500 ", 1500, id="a-padded-numeric-string-is-read"),
        pytest.param("not a number", None, id="a-non-numeric-string-is-omitted"),
        pytest.param(float("inf"), None, id="infinity-is-omitted"),
        pytest.param(float("nan"), None, id="nan-is-omitted"),
        pytest.param([1], None, id="a-list-is-omitted"),
    ],
)
def test_duration_is_coerced_to_an_integer_or_omitted(
    duration: Any, expected: int | None
) -> None:
    """A malformed duration degrades; it never raises and never leaks a float.

    Every value the internal document can hold passes through one coercion, and
    that is what makes a serialisation fault impossible by construction: the
    emitted document contains only strings, integers, lists and mappings.  A
    non-finite float would otherwise render as the token ``NaN``, which is not
    JSON and which the publisher cannot parse.
    """
    document = emitted_document(
        probe_feature(
            elements=[probe_scenario(steps=[probe_step(duration=duration)])]
        )
    )
    result = document[0]["elements"][0]["steps"][0]["result"]

    if expected is None:
        assert "duration" not in result
    else:
        assert result["duration"] == expected
        assert type(result["duration"]) is int


def test_a_non_finite_duration_still_renders_as_valid_json() -> None:
    """The rendered text parses, and carries no ``NaN`` token.

    Asserted on the rendered bytes rather than on the built structure, because
    ``json.dumps`` is configured with ``allow_nan=False`` and it is the
    combination -- coercion plus that option -- that makes an unparseable
    artifact unreachable.
    """
    text = render_cucumber_json(
        {
            "features": [
                probe_feature(
                    elements=[
                        probe_scenario(steps=[probe_step(duration=float("nan"))])
                    ]
                )
            ]
        }
    )

    assert "NaN" not in text
    assert "Infinity" not in text
    assert json.loads(text)[0]["elements"][0]["steps"][0]["result"] == {
        "status": STATUS_PASSED
    }


def test_a_malformed_step_nesting_degrades_to_the_emptiest_legal_shape() -> None:
    """A step whose ``match`` or ``result`` is not a mapping still emits.

    The status falls back rather than raising, and the five keys are still
    there, so a corrupt worker file costs the run its data for that step and
    not the artifact.
    """
    step = probe_step()
    step["match"] = "not a mapping"
    step["result"] = "not a mapping either"

    emitted = emitted_document(
        probe_feature(elements=[probe_scenario(steps=[step])])
    )[0]["elements"][0]["steps"][0]

    assert set(emitted) == set(STEP_KEYS)
    assert emitted["match"] == {}
    assert emitted["result"] == {"status": STATUS_FALLBACK}


def test_non_mapping_steps_are_skipped_rather_than_iterated() -> None:
    """A ``steps`` list holding scalars yields only the real steps."""
    scenario = probe_scenario(steps=[probe_step(name="real")])
    scenario["steps"] = ["a string", None, 7, *scenario["steps"]]

    steps = emitted_document(probe_feature(elements=[scenario]))[0]["elements"][0][
        "steps"
    ]

    assert [step["name"] for step in steps] == ["real"]


# =========================================================================== #
# 9. error_message: line-ending normalisation, and what is parity
# =========================================================================== #


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("plain", "plain", id="unchanged"),
        pytest.param("a\r\nb", "a\nb", id="crlf-becomes-lf"),
        pytest.param("a\rb", "a\nb", id="lone-cr-becomes-lf"),
        pytest.param("a\r\nb\rc\nd", "a\nb\nc\nd", id="mixed-endings"),
        pytest.param("a\n\nb", "a\n\nb", id="blank-lines-survive"),
        pytest.param("", "", id="empty"),
        pytest.param(None, "", id="none-becomes-empty"),
        pytest.param(42, "42", id="non-string-is-coerced"),
        pytest.param(
            "The title is not same as the expected! ",
            "The title is not same as the expected! ",
            id="a-trailing-space-is-parity",
        ),
        pytest.param(
            "The title is not same as the expected!",
            "The title is not same as the expected!",
            id="the-same-message-without-one-is-too",
        ),
    ],
)
def test_normalize_error_message_unifies_line_endings_and_nothing_else(
    text: Any, expected: str
) -> None:
    """LF everywhere, and the message text left exactly as it was.

    The reference failure text is a Java assertion message and stack trace with
    ``\\r\\n`` endings that Python cannot produce, so AAP deviation 16 makes the
    *assertion subject and message* parity while their surrounding formatting
    is not.  The two cases above are the reason the trailing space matters: the
    source carries the same assertion message both with and without one, and
    parity keeps both rather than tidying either.  The function is also
    idempotent, so applying it after the collector already normalised is
    harmless.
    """
    assert normalize_error_message(text) == expected
    assert normalize_error_message(normalize_error_message(text)) == expected


def test_an_emitted_error_message_is_lf_only_and_keeps_its_subject() -> None:
    """A CRLF message reaches the artifact as LF, with its text intact.

    Asserted through the writer rather than on the helper alone, because it is
    the writer that decides the key is present at all -- and an empty message
    must make it absent rather than empty.
    """
    subject = "The title is not same as the expected! "
    document = emitted_document(
        probe_feature(
            elements=[
                probe_scenario(
                    steps=[
                        probe_step(
                            status="failed",
                            duration=4_211_000_000,
                            error_message=f"{subject}\r\nTraceback\r\n  frame\r",
                        ),
                        probe_step(name="empty message", error_message=""),
                    ]
                )
            ]
        )
    )
    steps = document[0]["elements"][0]["steps"]

    assert "\r" not in steps[0]["result"]["error_message"]
    assert steps[0]["result"]["error_message"] == f"{subject}\nTraceback\n  frame\n"
    assert "error_message" not in steps[1]["result"]


def test_the_sample_failure_messages_keep_their_assertion_subjects(
    sample_emitted: list[JsonDict],
) -> None:
    """The subject survives the writer, and no ``\\r`` reaches the artifact.

    The assertion's own message is parity; the traceback that follows it is
    not, which is why the subject is asserted rather than the whole text.
    """
    messages = [
        step["result"]["error_message"]
        for step in iter_steps(sample_emitted)
        if "error_message" in step["result"]
    ]
    assert messages

    for message in messages:
        assert "\r" not in message
        assert message.strip()


# =========================================================================== #
# 10. match: the location rule, and the arguments the baseline cannot show
# =========================================================================== #


def test_match_location_is_a_dotted_python_path(
    sample_emitted: list[JsonDict],
) -> None:
    """A dotted module path, with no parentheses and no parameter types.

    AAP deviation 8: the Java report recorded
    ``com.testinium.step_definitions.Crm.userCanSeeNewPipeline()`` and this
    port records the resolved Python callable's dotted path instead, because
    Python has no signature to reproduce.  The shape is what both HTML writers
    and the viewer display, so it is pinned here.
    """
    locations = [
        step["match"]["location"]
        for step in iter_steps(sample_emitted)
        if "location" in step["match"]
    ]
    assert locations

    for location in locations:
        assert "(" not in location
        assert ")" not in location
        assert location.count(".") >= 2
        assert location == location.strip()


def test_an_undefined_step_has_an_empty_match(
    sample_emitted: list[JsonDict],
) -> None:
    """``createMatchMap`` skips ``location`` when the status is undefined.

    So an undefined step's ``match`` is ``{}`` -- not a location pointing at
    whatever the engine last resolved.  The sample's Sales feature carries
    exactly one such step, recorded with ``matched: false``.
    """
    undefined = [
        step
        for step in iter_steps(sample_emitted)
        if step["result"]["status"] == STATUS_UNDEFINED
    ]

    assert len(undefined) == 1
    assert undefined[0]["name"] == SAMPLE_UNDEFINED_STEP_NAME
    assert undefined[0]["match"] == {}
    assert "duration" not in undefined[0]["result"]


def test_a_location_is_dropped_when_the_mapped_status_is_undefined() -> None:
    """The rule keys on the *mapped* status, not on the recorded one.

    A hand-built step recording the engine's ``untested_undefined`` maps to
    ``undefined``, and its location must go with it: the alias fold and the
    location rule have to agree, or a step would be published as undefined
    while still naming an implementation.
    """
    document = emitted_document(
        probe_feature(
            elements=[
                probe_scenario(
                    steps=[
                        probe_step(
                            name="folded to undefined",
                            status="untested_undefined",
                            duration=0,
                        )
                    ]
                )
            ]
        )
    )
    step = document[0]["elements"][0]["steps"][0]

    assert step["result"]["status"] == STATUS_UNDEFINED
    assert step["match"] == {}


def test_arguments_are_emitted_with_offsets_that_index_into_the_name(
    golden_internal: JsonDict,
) -> None:
    """``val`` keeps its quotes and ``offset`` indexes into ``name``.

    The baseline's one parameterised step records ``("\\"Test2\\"", 44)``,
    ``("\\"30\\"", 54)`` and ``("\\"2\\"", 63)``.  The invariant
    ``name[offset:offset + len(val)] == val`` is what makes an offset
    meaningful, and the offsets are never recomputed by the writer: an offset
    computed against a differently-substituted outline name would be silently
    wrong.
    """
    parameterised = [
        step
        for step in iter_steps(build_cucumber_json(golden_internal))
        if "arguments" in step["match"]
    ]

    assert len(parameterised) == 1
    step = parameterised[0]
    assert step["name"] == GOLDEN_ARGUMENT_STEP_NAME
    assert step["match"]["arguments"] == [dict(a) for a in GOLDEN_ARGUMENTS]

    for argument in step["match"]["arguments"]:
        offset = argument["offset"]
        value = argument["val"]
        assert step["name"][offset : offset + len(value)] == value
        assert value.startswith('"') and value.endswith('"')


def test_every_sample_argument_indexes_into_its_own_step_name(
    sample_emitted: list[JsonDict],
) -> None:
    """The same invariant across every parameterised step of the sample.

    The sample's outline rows substitute different values into one template, so
    this is where a recomputed offset would show up: the two Crm rows and the
    two Sales rows each carry their own span.
    """
    checked = 0
    for step in iter_steps(sample_emitted):
        for argument in step["match"].get("arguments", ()):
            offset = argument["offset"]
            value = argument["val"]
            assert step["name"][offset : offset + len(value)] == value
            checked += 1

    assert checked >= 4


def test_arguments_are_absent_when_the_step_took_none(
    golden_internal: JsonDict,
) -> None:
    """A parameterless step's ``match`` carries only ``location``."""
    steps = list(iter_steps(build_cucumber_json(golden_internal)))
    parameterless = [step for step in steps if "arguments" not in step["match"]]

    assert len(parameterless) == GOLDEN_STEP_COUNT - 1
    for step in parameterless:
        assert set(step["match"]) == {"location"}


def test_an_argument_with_no_value_becomes_an_empty_object() -> None:
    """Arity is contract, so a null-valued argument is kept as ``{}``.

    ``createMatchMap`` does the same when ``argument.getValue()`` is null.  The
    length of the list is the arity of the step's parameters, so dropping an
    entry would misreport the step's shape; an unquoted placeholder keeps its
    span exactly as the collector recorded it.
    """
    name = 'A "quoted" and an unquoted 42 parameter'
    document = emitted_document(
        probe_feature(
            elements=[
                probe_scenario(
                    steps=[
                        probe_step(
                            name=name,
                            arguments=[
                                {"val": None, "offset": 2},
                                {"val": '"quoted"', "offset": 2},
                                {"val": "42", "offset": 27},
                            ],
                        )
                    ]
                )
            ]
        )
    )
    arguments = document[0]["elements"][0]["steps"][0]["match"]["arguments"]

    assert arguments == [
        {},
        {"val": '"quoted"', "offset": 2},
        {"val": "42", "offset": 27},
    ]
    assert name[2:10] == '"quoted"'
    assert name[27:29] == "42"


def test_an_empty_arguments_list_omits_the_key() -> None:
    """A step recorded with no arguments carries no ``arguments`` key."""
    document = emitted_document(
        probe_feature(elements=[probe_scenario(steps=[probe_step(arguments=[])])])
    )

    assert set(document[0]["elements"][0]["steps"][0]["match"]) == {"location"}


def test_matched_is_inferred_when_a_hand_built_step_omits_it() -> None:
    """A step that resolved to a location is by definition one that matched.

    The internal flag exists because the dry-run rule needs it; a document
    assembled by hand may omit it, and the writer must then fall back on the
    location rather than treat the step as unmatched.
    """
    with_location = probe_step(name="has a location")
    del with_location["matched"]
    without_location = probe_step(name="has none", location=None, status=None)
    del without_location["matched"]

    document = emitted_document(
        probe_feature(
            elements=[
                probe_scenario(steps=[with_location, without_location])
            ]
        ),
        dry_run=True,
    )
    steps = document[0]["elements"][0]["steps"]

    assert steps[0]["result"]["status"] == STATUS_PASSED
    assert steps[1]["result"]["status"] == STATUS_UNDEFINED


# =========================================================================== #
# 11. The after array and its embeddings
# =========================================================================== #


def test_the_failed_scenario_carries_an_after_entry_with_its_screenshot(
    sample_emitted: list[JsonDict],
) -> None:
    """One hook entry, with a location, a result and one named embedding.

    No committed artifact contains an embedding -- the reference's teardown
    hook never ran -- so the shape follows from the attach call plus the
    generator, which hangs an attachment off the *test case* map rather than
    off a step.  ``mime_type``'s underscore is the contract, and ``name`` is
    the scenario's name, exactly as ``scenario.attach(screenshot, "image/png",
    scenario.getName())`` supplied it.
    """
    carrying = [
        element for element in scenarios(sample_emitted) if "after" in element
    ]

    assert len(carrying) == 1
    element = carrying[0]
    assert element["name"] == SAMPLE_EMBEDDING_SCENARIO_NAME

    assert len(element["after"]) == 1
    entry = element["after"][0]
    assert set(entry) == {"match", "result", "embeddings"}
    assert entry["match"] == {"location": SAMPLE_AFTER_HOOK_LOCATION}
    # The hook's own result, exactly: the fixture records the teardown as
    # passing in 412 ms, and the writer's job is to carry that through with the
    # same nanosecond scale and the same key set as a step's result.  A hook
    # reported without its duration would leave the screenshot's provenance
    # unmeasurable in the artifact.
    assert entry["result"] == {
        "status": STATUS_PASSED,
        "duration": SAMPLE_AFTER_HOOK_DURATION_NANOS,
    }
    assert isinstance(entry["result"]["duration"], int)
    assert not isinstance(entry["result"]["duration"], bool)

    assert len(entry["embeddings"]) == 1
    embedding = entry["embeddings"][0]
    assert set(embedding) == {"mime_type", "data", "name"}
    assert embedding["mime_type"] == SAMPLE_EMBEDDING_MIME_TYPE
    assert embedding["name"] == SAMPLE_EMBEDDING_SCENARIO_NAME


def test_embedded_data_is_unchunked_base64_that_decodes(
    sample_emitted: list[JsonDict],
) -> None:
    """The payload decodes, and carries no line breaks.

    Both HTML writers render it as a ``data:`` URI, which a chunked payload
    would break, and a payload that does not decode would render as a broken
    image in the published report rather than fail anywhere.
    """
    embeddings = [
        embedding
        for element in scenarios(sample_emitted)
        for entry in element.get("after", ())
        for embedding in entry["embeddings"]
    ]
    assert embeddings

    for embedding in embeddings:
        data = embedding["data"]
        assert "\n" not in data
        assert "\r" not in data
        assert base64.b64decode(data, validate=True)


def test_a_scenario_with_no_attachment_carries_no_after_key(
    sample_emitted: list[JsonDict],
) -> None:
    """A passing scenario has no ``after`` -- not an entry with an empty list.

    That keeps the emitted document identical in shape to the reference, where
    no element carries the key at all.
    """
    without = [
        element for element in scenarios(sample_emitted) if "after" not in element
    ]

    assert len(without) == SAMPLE_SELECTED_SCENARIO_COUNT - 1
    for element in without:
        assert "after" not in element


@pytest.mark.parametrize(
    ("embeddings", "expected_after"),
    [
        pytest.param([], False, id="a-hook-with-no-embedding-contributes-nothing"),
        pytest.param(
            [{"mime_type": SAMPLE_EMBEDDING_MIME_TYPE, "data": ""}],
            False,
            id="an-embedding-with-empty-data-is-dropped",
        ),
        pytest.param(
            [{"mime_type": SAMPLE_EMBEDDING_MIME_TYPE, "data": "QUJD"}],
            True,
            id="an-embedding-with-data-is-kept",
        ),
    ],
)
def test_only_hooks_that_produced_an_attachment_reach_the_artifact(
    embeddings: list[JsonDict], expected_after: bool
) -> None:
    """A capture that failed must leave no trace at all.

    AAP deviation 19 suppresses a screenshot failure: it is logged, the
    scenario's status is unchanged, and no embedding is emitted.  An entry
    carrying an empty ``embeddings`` list -- or an embedding with empty
    ``data`` -- would instead render as a broken image in both HTML reports.
    """
    element = probe_scenario(
        after=[
            events.new_hook_entry(
                status="passed", duration=412_000_000, embeddings=embeddings
            )
        ]
    )

    emitted = emitted_document(probe_feature(elements=[element]))[0]["elements"][0]

    assert ("after" in emitted) is expected_after
    if expected_after:
        assert emitted["after"][0]["embeddings"][0]["data"] == "QUJD"


def test_an_embedding_without_a_name_omits_the_key() -> None:
    """``name`` is emitted only when the attachment actually has one.

    The generator guards it with ``if (name != null)``, and an embedding named
    ``""`` would render as an empty caption in both HTML reports.
    """
    element = probe_scenario(
        after=[
            events.new_hook_entry(
                embeddings=[
                    {"mime_type": SAMPLE_EMBEDDING_MIME_TYPE, "data": "QUJD"},
                    {
                        "mime_type": SAMPLE_EMBEDDING_MIME_TYPE,
                        "data": "QUJD",
                        "name": "named",
                    },
                ]
            )
        ]
    )

    embeddings = emitted_document(probe_feature(elements=[element]))[0]["elements"][0][
        "after"
    ][0]["embeddings"]

    assert set(embeddings[0]) == {"mime_type", "data"}
    assert embeddings[1]["name"] == "named"


def test_a_hook_with_no_location_carries_an_empty_match() -> None:
    """No location means an empty ``match``, mirroring the generator's omission.

    The hook's own result still follows the three-clause rule: its status is
    its own -- never dry-run mapped, since the engine runs no hooks in a dry
    run -- and its zero duration is omitted like any other.
    """
    element = probe_scenario(
        after=[
            events.new_hook_entry(
                location=None,
                status="failed",
                duration=0,
                embeddings=[
                    {"mime_type": SAMPLE_EMBEDDING_MIME_TYPE, "data": "QUJD"}
                ],
            )
        ]
    )

    entry = emitted_document(
        probe_feature(elements=[element]), dry_run=True
    )[0]["elements"][0]["after"][0]

    assert entry["match"] == {}
    assert entry["result"] == {"status": "failed"}


def test_a_background_never_carries_an_after_array() -> None:
    """Even handed hook entries, a Background occurrence emits none.

    The generator puts AFTER hooks on the test-case map, and a Background
    occurrence is not a test case.
    """
    background = probe_background()
    background["after"] = [
        events.new_hook_entry(
            embeddings=[{"mime_type": SAMPLE_EMBEDDING_MIME_TYPE, "data": "QUJD"}]
        )
    ]

    elements = emitted_document(
        probe_feature(elements=[background, probe_scenario()])
    )[0]["elements"]

    assert set(elements[0]) == set(BACKGROUND_ELEMENT_KEYS)
    assert "after" not in elements[0]


def test_a_malformed_after_value_is_ignored() -> None:
    """A non-list or scalar-holding ``after`` yields no key rather than raising."""
    element = probe_scenario()
    element["after"] = "not a list"
    assert "after" not in emitted_document(probe_feature(elements=[element]))[0][
        "elements"
    ][0]

    element = probe_scenario()
    element["after"] = ["a string", None, 7]
    assert "after" not in emitted_document(probe_feature(elements=[element]))[0][
        "elements"
    ][0]


# =========================================================================== #
# 12. start_timestamp
# =========================================================================== #


def test_start_timestamp_is_present_on_every_scenario(
    sample_emitted: list[JsonDict],
) -> None:
    """The key is part of the scenario contract, and its value passes through.

    The collector already emits the contract's millisecond-precision UTC form
    with a literal ``Z``, so the writer copies it byte for byte -- reformatting
    it here would put a second timestamp format in the port.
    """
    for element in scenarios(sample_emitted):
        assert "start_timestamp" in element
        assert isinstance(element["start_timestamp"], str)
        assert element["start_timestamp"].endswith("Z")


def test_an_unknown_start_timestamp_is_null_rather_than_missing() -> None:
    """A hand-built element with no usable timestamp keeps the key as ``null``.

    A ``null`` reads as "unknown" to every consumer, where a missing key would
    read as a shape change.  Both unusable cases are driven: no value at all,
    and a value that is not a string -- the second would otherwise reach the
    artifact as a number where every consumer parses a timestamp.
    """
    absent = probe_scenario(name="no timestamp", start_timestamp=None)
    not_a_string = probe_scenario(name="not a string", identifier="f;x")
    not_a_string["start_timestamp"] = 1_662_557_846_297

    elements = emitted_document(
        probe_feature(elements=[absent, not_a_string])
    )[0]["elements"]

    assert elements[0]["start_timestamp"] is None
    assert elements[1]["start_timestamp"] is None
    assert all("start_timestamp" in element for element in elements)


# =========================================================================== #
# 13. Status mapping
# =========================================================================== #


class StubStatus:
    """A behave-style status enum stand-in.

    behave's ``Status`` exposes both ``name`` and ``normalized_name``, and the
    normalised one is what folds ``untested_pending`` to ``pending`` and
    ``untested_undefined`` to ``undefined``.  A stub rather than the real enum,
    because importing the engine into this module would load it at collection
    time for a test that needs two attributes -- ``tests/conftest.py`` keeps
    behave out of collection deliberately.
    """

    def __init__(self, name: str, normalized_name: str | None = None) -> None:
        self.name = name
        if normalized_name is not None:
            self.normalized_name = normalized_name

    def __str__(self) -> str:
        return self.name


class OpaqueStatus:
    """An object exposing neither ``name`` nor ``normalized_name``."""

    def __init__(self, text: str) -> None:
        self._text = text

    def __str__(self) -> str:
        return self._text


@pytest.mark.parametrize("status", sorted(CUCUMBER_STATUSES))
def test_map_step_status_is_the_identity_on_the_emitted_vocabulary(
    status: str,
) -> None:
    """Each of the seven Cucumber statuses maps to itself.

    ``skipped`` in particular: it is a *genuine* status for a step after a
    failure in the same scenario -- the baseline carries three of them -- and
    folding it to ``untested`` would misreport every failed scenario's tail.
    """
    assert map_step_status(status) == status


@pytest.mark.parametrize(
    ("alias", "expected"),
    sorted(EXPECTED_STATUS_ALIASES.items()),
    ids=sorted(EXPECTED_STATUS_ALIASES),
)
def test_every_declared_alias_folds_to_its_expected_status(
    alias: str, expected: str
) -> None:
    """The whole alias table, against an oracle this file owns.

    The expectation comes from :data:`EXPECTED_STATUS_ALIASES` -- written out
    in this module from the engine's vocabulary and the contract's -- and
    **not** from the production table under test, so that changing a fold in
    the implementation cannot change the expectation with it.  The engine's
    vocabulary is wider than Cucumber's: ``error``, ``hook_error`` and
    ``cleanup_error`` are its names for an exception rather than a failed
    assertion, which Cucumber has one ``failed`` for; ``xfailed``/``xpassed``
    come from expected-failure marking; ``pending_warn`` and
    ``untested_pending`` are its two spellings of pending; and ``executing``
    and ``unknown`` describe a step whose outcome was never established, which
    is ``untested``.

    Were this to fail with the implementation folding ``hook_error`` to
    ``passed``, a scenario whose teardown blew up would be published as a pass.
    """
    assert map_step_status(alias) == expected
    assert expected in CUCUMBER_STATUSES


def test_the_alias_table_holds_exactly_the_expected_folds() -> None:
    """The oracle and the implementation describe the same table.

    The companion to the test above, and the half that catches an *addition*:
    a fold added to the implementation without being considered here would
    otherwise go unasserted, and one removed from the implementation would
    leave a parametrized case that no longer proves anything.  Together the two
    tests pin the table's membership and each of its values independently of
    the implementation.
    """
    assert STATUS_ALIASES == EXPECTED_STATUS_ALIASES


@pytest.mark.parametrize(
    "status",
    [
        pytest.param(None, id="none"),
        pytest.param("", id="empty-string"),
        pytest.param("   ", id="whitespace-only"),
        pytest.param("nonsense", id="an-invented-token"),
        pytest.param(StubStatus("nonsense"), id="an-enum-carrying-an-invented-name"),
        pytest.param(OpaqueStatus("nonsense"), id="an-object-with-no-name-attribute"),
    ],
)
def test_an_unusable_status_falls_back_rather_than_leaking(status: Any) -> None:
    """``untested`` is the honest token for "this step has no outcome".

    The fallback matters because the publisher parses these names: an invented
    one would be silently mis-read rather than rejected, and it is also the
    engine's own initial status, which is what the collector writes when it is
    handed ``None``.
    """
    assert map_step_status(status) == STATUS_FALLBACK
    assert STATUS_FALLBACK in CUCUMBER_STATUSES


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        pytest.param("PASSED", STATUS_PASSED, id="upper-case"),
        pytest.param(" passed ", STATUS_PASSED, id="padded"),
        pytest.param("Error", "failed", id="an-alias-in-mixed-case"),
    ],
)
def test_a_status_is_matched_case_insensitively(status: str, expected: str) -> None:
    """Case and padding are normalised before the fold and the membership test."""
    assert map_step_status(status) == expected


def test_a_status_enum_is_read_through_its_normalized_name() -> None:
    """``normalized_name`` wins over ``name``, and a bare ``name`` still works.

    behave's own enum already folds its two pending spellings and its
    undefined spelling, so an object handed over directly must be read the same
    way the engine reads it -- and an object exposing only ``name`` is still
    understood, since that is all a simpler stand-in carries.
    """
    assert map_step_status(StubStatus("pending_warn", "pending")) == "pending"
    assert map_step_status(StubStatus("untested_undefined", STATUS_UNDEFINED)) == (
        STATUS_UNDEFINED
    )
    assert map_step_status(StubStatus("failed")) == "failed"
    assert map_step_status(OpaqueStatus(STATUS_PASSED)) == STATUS_PASSED


@pytest.mark.parametrize(
    "recorded",
    [
        pytest.param("untested", id="untested"),
        pytest.param("passed", id="passed"),
        pytest.param("failed", id="failed"),
        pytest.param(None, id="none"),
        pytest.param("nonsense", id="an-invented-token"),
    ],
)
def test_under_dry_run_the_answer_follows_matched_alone(recorded: Any) -> None:
    """Matched is ``passed`` and unmatched is ``undefined``, whatever was recorded.

    Under ``dryRun`` the JVM emits exactly that, while the engine reports
    ``untested`` for both -- so the recorded status is not consulted at all,
    and ``untested`` is never the answer.  The path is opt-in through
    ``run-tests --dry-run``, and it must still be right.
    """
    assert map_step_status(recorded, matched=True, dry_run=True) == STATUS_PASSED
    assert map_step_status(recorded, matched=False, dry_run=True) == STATUS_UNDEFINED
    assert (
        map_step_status(recorded, matched=True, dry_run=True) != STATUS_FALLBACK
    )


def test_dry_run_is_read_from_the_result_set_and_applied_to_every_step(
    sample_result_set: Any,
) -> None:
    """The flag lives on the document, and the writer applies it document-wide.

    Every matched step becomes ``passed`` and the sample's single unmatched one
    becomes ``undefined``; nothing stays ``skipped``, ``failed`` or
    ``untested``, because under a dry run no step ran.
    """
    sample_result_set["dry_run"] = True

    document = build_cucumber_json(sample_result_set)
    statuses = {step["result"]["status"] for step in iter_steps(document)}

    assert statuses == {STATUS_PASSED, STATUS_UNDEFINED}


def test_a_dry_run_keeps_the_undefined_step_location_rule() -> None:
    """A dry-run unmatched step is undefined, so its match is empty too.

    Both steps below record a location -- an unmatched step in a dry run may
    still have been written against one -- so this proves the location is
    dropped on the strength of the *mapped* status alone.
    """
    location = "features.steps.probe_steps.a_probe_step"
    document = emitted_document(
        probe_feature(
            elements=[
                probe_scenario(
                    steps=[
                        probe_step(name="matched", matched=True, location=location),
                        probe_step(name="unmatched", matched=False, location=location),
                    ]
                )
            ]
        ),
        dry_run=True,
    )
    steps = document[0]["elements"][0]["steps"]

    assert steps[0]["match"] == {"location": location}
    assert steps[1]["match"] == {}


def test_skipped_stays_skipped_for_a_step_after_a_failure(
    sample_emitted: list[JsonDict],
) -> None:
    """The step following a failure is ``skipped``, not ``untested``.

    The distinction is visible in the published report: ``skipped`` means the
    scenario stopped, ``untested`` means it never started.
    """
    crm_scenario = next(
        element
        for element in scenarios(sample_emitted)
        if element["name"] == SAMPLE_EMBEDDING_SCENARIO_NAME
    )
    statuses = [step["result"]["status"] for step in crm_scenario["steps"]]

    assert statuses == [STATUS_PASSED, "failed", "skipped"]


# =========================================================================== #
# 14. The I/O wrapper
#
# Everything here writes under pytest's temporary directories through the
# ``base=`` seam.  The repository's real target/ is never touched, and no test
# changes the working directory.
# =========================================================================== #


def test_write_puts_the_artifact_where_paths_says_it_goes(
    sample_result_set: Any, tmp_artifact_root: Path
) -> None:
    """The destination comes from ``paths.cucumber_json_path``, not a literal.

    That function is the single owner of the four artifact paths, and the
    publisher's ``fileIncludePattern`` is narrowed to the value it produces.
    The expected path is therefore taken from it here as well: a test carrying
    its own literal would keep passing while the two drifted apart.
    """
    expected = paths.cucumber_json_path(tmp_artifact_root)
    assert not expected.exists()

    written = write_cucumber_json(sample_result_set, base=tmp_artifact_root)

    assert written == expected
    assert written.is_file()
    assert written.is_relative_to(tmp_artifact_root)


def test_write_creates_its_parent_directory(
    sample_result_set: Any, tmp_artifact_root: Path
) -> None:
    """The build-output directory need not exist beforehand.

    A worker or a fresh clone may have no such directory, and the writer
    creating it is what lets the exit contract promise the artifact exists.
    Nothing is deleted or truncated beyond the one file -- emptying that
    directory is the CLI's ``--clean`` step.
    """
    target = paths.target_root(tmp_artifact_root)
    assert not target.exists()

    written = write_cucumber_json(sample_result_set, base=tmp_artifact_root)

    assert target.is_dir()
    assert written.parent == target


def test_write_accepts_an_explicit_path_that_overrides_base(
    sample_result_set: Any, tmp_artifact_root: Path
) -> None:
    """``path=`` wins over ``base=`` entirely, and its parent is created too.

    For a caller that already holds a destination, so that nobody has to
    reimplement the default.
    """
    destination = tmp_artifact_root / "elsewhere" / "copy.json"

    written = write_cucumber_json(
        sample_result_set, base=tmp_artifact_root, path=destination
    )

    assert written == destination
    assert destination.is_file()
    assert not paths.cucumber_json_path(tmp_artifact_root).exists()


def test_the_written_file_is_utf8_with_lf_and_one_trailing_newline(
    sample_result_set: Any, tmp_artifact_root: Path
) -> None:
    """One line, LF-terminated, decodable as UTF-8, and re-loadable.

    The byte shape is the reference artifact's: a single compact line with one
    trailing newline.  ``newline="\\n"`` is what keeps a report written on
    Windows byte-identical to one written on Linux, so the structure cannot
    depend on which branch of the pipeline's ``isUnix()`` ran.
    """
    written = write_cucumber_json(sample_result_set, base=tmp_artifact_root)
    raw = written.read_bytes()

    assert b"\r" not in raw
    assert raw.endswith(b"\n")
    assert not raw.endswith(b"\n\n")
    assert raw.count(b"\n") == 1

    text = raw.decode("utf-8")
    assert json.loads(text) == build_cucumber_json(sample_result_set)

    with written.open(encoding="utf-8") as stream:
        assert json.load(stream) == build_cucumber_json(sample_result_set)


def test_writing_an_empty_document_still_leaves_a_readable_file(
    tmp_artifact_root: Path,
) -> None:
    """A run that selected nothing still gives the publisher an input.

    The zero-selection exit row is not a failure, so the artifact has to exist
    and parse -- as ``[]``, never as an empty file.
    """
    written = write_cucumber_json(None, base=tmp_artifact_root)

    assert written.read_bytes() == b"[]\n"
    assert json.loads(written.read_text(encoding="utf-8")) == []


def test_build_and_render_write_nothing(
    sample_result_set: Any, tmp_artifact_root: Path
) -> None:
    """The pure half touches no filesystem at all.

    That is what lets the golden comparison run in memory and the reporting
    package's coverage gate be met without a browser -- and it is why the
    temporary root below is still empty after both calls.
    """
    build_cucumber_json(sample_result_set)
    render_cucumber_json(sample_result_set)

    assert list(tmp_artifact_root.iterdir()) == []
    assert not paths.cucumber_json_path(tmp_artifact_root).exists()


def test_render_is_compact_and_leaves_non_ascii_as_characters() -> None:
    """Compact separators, no key sorting, and characters rather than escapes.

    ``ensure_ascii=False`` is what keeps the French required-field message the
    ``@UPGN-288`` scenario asserts readable in the artifact; compact separators
    and the absence of sorting reproduce the reference's byte shape, where the
    whole report is one line and the keys are in emission order.
    """
    french = "Veuillez renseigner ce champ."
    text = render_cucumber_json(
        {
            "features": [
                probe_feature(
                    name=french,
                    elements=[probe_scenario(steps=[probe_step(name=french)])],
                )
            ]
        }
    )

    assert french in text
    assert "\\u" not in text
    assert '", "' not in text
    assert '": "' not in text
    assert text.count("\n") == 1
    assert list(json.loads(text)[0]) == list(FEATURE_KEYS)


def test_hostile_values_are_carried_as_data_and_cannot_inject_structure(
    tmp_artifact_root: Path,
) -> None:
    """Every text field survives verbatim, and none of it becomes syntax.

    The values a run puts into this artifact are not under the port's control:
    a feature title, a scenario name, a step argument, an assertion message and
    a screenshot's name all originate in the suite or in the system under test.
    So each field below carries the characters that would end a JSON string, a
    physical line or a C string if the writer concatenated rather than encoded
    -- a double quote, a backslash, CRLF and LF, a tab and a NUL -- alongside
    ``</script>`` and non-ASCII text.

    Three properties are asserted together, because each alone would miss a
    real defect.  The document's *shape* is unchanged, so nothing was injected:
    one feature, one element, one step, one hook entry, and the declared key
    sets.  Every value comes back *identical*, so nothing was dropped or
    trimmed on the way through -- with the single documented exception of
    ``error_message``, whose line endings the contract normalises to LF (AAP
    deviation 16).  And the rendered text carries **no raw newline other than
    its single terminator and no raw NUL**, which is what keeps the artifact
    readable by the line-oriented tooling a CI workspace applies to it.

    Were this to fail, a scenario name containing a quotation mark -- which
    this suite's real names do -- could produce an artifact the Jenkins
    publisher cannot parse, and the failure would surface as a silently
    unpublished build rather than as an error here.
    """
    hostile = 'a"b\\c\nd\r\ne\tf\x00g</script>h\u00e9'
    step_name = f'A step whose argument is "{hostile}"'
    argument_value = f'"{hostile}"'
    document = {
        "features": [
            probe_feature(
                name=hostile,
                description=f"  {hostile}",
                tags=[events.feature_tag(f"@{hostile}", 1, 1)],
                elements=[
                    probe_scenario(
                        name=hostile,
                        identifier=hostile,
                        description=hostile,
                        tags=[events.scenario_tag(f"@{hostile}")],
                        steps=[
                            probe_step(
                                name=step_name,
                                arguments=[
                                    {
                                        "val": argument_value,
                                        "offset": step_name.index(argument_value),
                                    }
                                ],
                                status="failed",
                                error_message=hostile,
                            )
                        ],
                        after=[
                            events.new_hook_entry(
                                location=hostile,
                                embeddings=[
                                    {
                                        "mime_type": SAMPLE_EMBEDDING_MIME_TYPE,
                                        "data": base64.b64encode(
                                            DEFAULT_SCREENSHOT_PNG
                                        ).decode("ascii"),
                                        "name": hostile,
                                    }
                                ],
                            )
                        ],
                    )
                ],
            )
        ]
    }

    text = render_cucumber_json(document)
    written = write_cucumber_json(document, base=tmp_artifact_root)
    reparsed = json.loads(written.read_text(encoding="utf-8"))

    # Nothing was injected: the shape is exactly what was handed in.
    assert text.count("\n") == 1, "an embedded newline reached the artifact raw"
    assert "\x00" not in text, "an embedded NUL reached the artifact raw"
    assert reparsed == json.loads(text)
    assert len(reparsed) == 1
    feature = reparsed[0]
    assert set(feature) == set(FEATURE_KEYS)
    assert len(feature["elements"]) == 1
    element = feature["elements"][0]
    assert set(element) == set(SCENARIO_ELEMENT_KEYS)
    assert len(element["steps"]) == 1
    assert len(element["after"]) == 1

    # Nothing was altered: every value is the one that went in, except the
    # failure text, whose endings the contract normalises to LF.
    step = element["steps"][0]
    hook = element["after"][0]
    assert feature["name"] == hostile
    assert feature["description"] == f"  {hostile}"
    assert feature["tags"][0]["name"] == f"@{hostile}"
    assert element["name"] == hostile
    assert element["id"] == hostile
    assert element["description"] == hostile
    assert element["tags"] == [{"name": f"@{hostile}"}]
    assert step["name"] == step_name
    assert step["match"]["location"] == "features.steps.probe_steps.a_probe_step"
    assert step["match"]["arguments"] == [
        {"val": argument_value, "offset": step_name.index(argument_value)}
    ]
    assert step["name"][
        step["match"]["arguments"][0]["offset"] :
    ].startswith(argument_value)
    assert hook["match"]["location"] == hostile
    assert hook["embeddings"][0]["name"] == hostile
    assert step["result"]["error_message"] == hostile.replace("\r\n", "\n")
    assert "\r" not in step["result"]["error_message"]


def test_render_and_write_agree_byte_for_byte(
    sample_result_set: Any, tmp_artifact_root: Path
) -> None:
    """The file holds exactly what ``render_cucumber_json`` produced.

    The two entry points must not drift: a service that renders for inspection
    and a CLI that writes have to be describing the same artifact.
    """
    expected = render_cucumber_json(sample_result_set)
    written = write_cucumber_json(sample_result_set, base=tmp_artifact_root)

    assert written.read_text(encoding="utf-8") == expected


def test_write_reports_an_unusable_destination_rather_than_swallowing_it(
    sample_result_set: Any, tmp_artifact_root: Path
) -> None:
    """An I/O fault propagates, because the exit table needs it named.

    Producing this artifact is the writer's contract with the exit contract,
    whose writer-failure class requires the failing writer to be named on
    stderr while the artifacts written before it remain.  A test *outcome*, by
    contrast, never reaches this path: failures and skips are data that has
    already been serialised by the time the file is opened.
    """
    # A regular file standing where the build-output directory would go, so
    # that creating the parent cannot succeed.  The destination is still taken
    # from paths.cucumber_json_path rather than spelled out here.
    blocker = tmp_artifact_root / "blocked"
    blocker.write_text("not a directory", encoding="utf-8")

    with pytest.raises(OSError):
        write_cucumber_json(sample_result_set, path=paths.cucumber_json_path(blocker))
