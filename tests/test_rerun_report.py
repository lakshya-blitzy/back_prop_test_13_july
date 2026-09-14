"""Tests for the grouped rerun manifest -- ``app/reporting/rerun_report.py``.

This module gates a **machine input**, not a log.  ``FailedTestRunner.java:11``
declares ``features = "@target/rerun.txt"`` and in this port ``run-tests
--rerun`` reads the same file, so whatever the writer emits is what a second
run executes: a format error silently changes which scenarios are retried and
nothing downstream notices.  That is why the manifest's *bytes* are asserted
and not merely its meaning, and why a comment, an absolute path or a traversal
component is asserted to be refused through the module's single error channel,
with a production read confining every entry to a real Gherkin source file.

AAP 0.6 fixes the format -- one line per feature, a ``file:`` prefix, each
failing line appended colon-separated, features in source order, line numbers
ascending and deduplicated, failures only, an empty run writing zero bytes --
and the round trip: the file the writer produces must select exactly the
scenarios that failed.  What counts as a failure is ``FAILURE_STATUSES`` alone,
restated below as a literal so the two are comparable in both directions.

``tests/fixtures/golden_rerun.txt`` is the committed reference artifact taken
verbatim: 50 bytes, one line, one trailing LF, free of merge-conflict markers
and of any header -- one feature with its two failing scenario lines appended
colon-separated.  It is the only one of the four committed artifacts that
needed no surgery, and it carries no timestamp, no duration and no traceback,
which makes it the one artifact where **byte equality is a legitimate
assertion** (AAP 0.6 bars byte comparison for the other three).

The one expected difference between that fixture and this port's output is the
feature-directory prefix, which AAP deviation 1 moved while preserving the
filenames.  Reconciling it is *production* code -- ``normalize_feature_uri`` in
``app/utils/paths.py``, reached here through ``conftest``'s
``golden_rerun_normalized`` fixture.  Consequently **no prefix literal appears
anywhere in this file**: paths are composed from the constants
``app/utils/paths.py`` exports, and the last test of section 1 reads this
file's own source to prove that absence rather than trusting it.

What this module gates, section by section
------------------------------------------
1. the pinned baseline, byte for byte, in both directions;
2. the grouping rule -- one line per feature, features in source order, line
   numbers ascending and deduplicated, failures only, an empty run writing zero
   bytes;
3. the parser, its single error channel, and the round trip AAP 0.6 requires --
   *"the file the writer produces must select exactly the scenarios that
   failed"*;
4. the four ``--rerun`` couplings this writer underpins;
5. the I/O wrapper: where it writes, what bytes reach disk, and what it never
   does to a file it did not create;
6. the published surface ``app/cli.py`` depends on, and the single
   implementation of the failure rule every ordering property falls out of;
7. the bounded, verified read of the manifest itself, and the verified
   resolution of the feature files its entries name.

The two rules this module holds the parser to
---------------------------------------------
Both are properties of the code as it stands, asserted in one direction only.
A test that accepted either of two outcomes for machine input would pass
whichever way the parser drifted, which for a file a second run *executes from*
is not a gate at all.

*The failure vocabulary is wider than the literal* ``failed``.
``FAILURE_STATUSES`` is the single statement of it, and :data:`FAILING_STATUSES`
below restates it as a literal so the two can be compared in both directions.
``failed`` is still what every scenario expected **in** the manifest carries
unless the vocabulary itself is the subject, and ``passed``/``skipped`` what
every scenario expected **out** carries -- those two are the only statuses the
JVM's ``Status.isOk()`` admits, since it is ``PASSED || SKIPPED`` and nothing
else.

*Anything that is not an entry of this grammar is rejected, through*
``RerunManifestError`` *and nothing else.*  A comment -- behave's own
``# -- RERUN:`` header included -- an absolute path, a path with a ``.`` or
``..`` component, a hidden, nested, padded or non-Gherkin name, a missing
``file:`` scheme and a line carrying no line number are each refused, and the
refusal discards the whole document rather than skipping the line: behave's
header means the remaining lines are in *its* ungrouped grammar, so reading on
would select a scenario set nobody asked for.  Two further properties travel
with every refusal and are asserted with it -- the message names the source and
the entry's one-based position, and it reproduces **none** of the refused text,
because it reaches stderr and a Jenkins console record (CWE-117, CWE-532).

On top of the grammar, a **production read confines every entry to a verified
regular file inside the features directory**: the manifest is opened no-follow
through ``app/utils/paths.py`` and read under a byte bound, and each entry is
opened no-follow beneath a no-follow features root and checked on the
descriptor, so a linked manifest, a named pipe, an over-sized file, a linked
features root, a linked or hard-linked entry and a non-regular entry are each
refused rather than executed.  Section 8 is that surface, end to end.
"""

from __future__ import annotations

import copy
import hashlib
import inspect
import logging
import os
import signal
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Final

import pytest

from app.reporting import aggregation, cucumber_json, rerun_report
from app.reporting.events import (
    ELEMENT_TYPE_BACKGROUND,
    ELEMENT_TYPE_SCENARIO,
    JsonDict,
    ResultSet,
    new_element,
    new_feature,
    new_hook_entry,
    new_result_set,
    new_step,
)
from app.utils import paths

# --------------------------------------------------------------------------- #
# Fixed names and measured constants
#
# Feature *filenames* are pinned here because AAP deviation 1 preserves them
# unchanged; the directory they sit in is not, and is never spelled out -- it
# comes from ``paths.NORMALIZED_FEATURES_PREFIX`` in :func:`_feature_path`.
# --------------------------------------------------------------------------- #

#: Byte size of the committed reference manifest, measured with ``od -c``.
GOLDEN_RERUN_BYTES: Final[int] = 50

#: MD5 of the same file.  Pinned so that an edit to the baseline -- the one
#: thing a writer test must never be "fixed" by -- fails here and names itself,
#: instead of quietly moving the target the port is measured against.
GOLDEN_RERUN_MD5: Final[str] = "199ee9d0fdb6debceadbd73af5b846e4"

#: The two failing scenario lines the baseline records, in the order it records
#: them: a plain scenario, and an outline **data row**.
GOLDEN_SCENARIO_LINES: Final[tuple[int, ...]] = (9, 24)

#: Feature filenames used by the synthetic result sets.  Five of the ten
#: features carry no feature-level tag (Contact, Inventory, Notes, Sales and
#: Session), which is what makes them the right subjects for the "the writer
#: applies no tag filtering of its own" assertion.
CRM_FEATURE: Final[str] = "Crm.feature"
CONTACT_FEATURE: Final[str] = "Contact.feature"
INVENTORY_FEATURE: Final[str] = "Inventory.feature"
NOTES_FEATURE: Final[str] = "Notes.feature"
SALES_FEATURE: Final[str] = "Sales.feature"
SESSION_FEATURE: Final[str] = "Session.feature"

#: The untagged five, in a deliberately non-alphabetical order.
UNTAGGED_FEATURES: Final[tuple[str, ...]] = (
    SESSION_FEATURE,
    SALES_FEATURE,
    NOTES_FEATURE,
    INVENTORY_FEATURE,
    CONTACT_FEATURE,
)

#: Statuses used below.  ``FAILED`` is taken from the module under test rather
#: than spelled again, because it is the one status whose name the manifest's
#: contract depends on.
FAILED: Final[str] = rerun_report.FAILED_STATUS
PASSED: Final[str] = "passed"
SKIPPED: Final[str] = "skipped"
UNDEFINED: Final[str] = "undefined"

#: What behave records for **every** step of a dry run, matched or not, which
#: is why the dry-run rule reads a step's match state instead of its status.
DRY_RUN_RECORDED_STATUS: Final[str] = "untested"

#: The duration and the assertion text a genuinely failed step carries, for
#: the one document that has to reproduce the JVM baseline's *result key set*
#: rather than only its statuses.  Nothing asserts on the values themselves;
#: what matters is that both keys are present and the duration is non-zero,
#: because the JSON writer omits a zero one.
FAILED_STEP_DURATION_NS: Final[int] = 1_000_000
FAILED_STEP_MESSAGE: Final[str] = "AssertionError: the background step failed"

#: Every status that puts a scenario in the manifest, spelled out here rather
#: than read from the module so the comparison against
#: ``rerun_report.FAILURE_STATUSES`` can fail in **both** directions -- a
#: status quietly dropped from the vocabulary is as much a defect as one
#: quietly added, because the manifest and ``cucumber.json`` are read as a
#: matched pair and a failure the JSON reports has to be re-selectable.
#:
#: The membership is measured, not chosen.  Cucumber-JVM 7.2.3's
#: ``RerunFormatter.handleTestCaseFinished`` records a test case whenever
#: ``Status.isOk()`` is false, and ``isOk()`` is ``PASSED || SKIPPED`` only --
#: so ``undefined``, ``pending`` and ``ambiguous`` are in the JVM's own
#: manifest even though none of them is spelled ``failed``.  behave 1.3.3's
#: ``Status.has_failed()`` adds ``error``, ``hook_error`` and
#: ``cleanup_error``, which ``cucumber_json.STATUS_ALIASES`` folds onto
#: ``failed``; ``xfailed`` is folded there too, which is what keeps the two
#: artifacts agreeing about the same scenario.
#:
#: The committed baseline cannot arbitrate this: its run carries only
#: ``passed``, ``failed`` and ``skipped``, so ``tests/fixtures/golden_rerun.txt``
#: is silent on every other member and the JVM's own rule is the evidence.
FAILING_STATUSES: Final[frozenset[str]] = frozenset(
    {
        FAILED,
        "error",
        "hook_error",
        "cleanup_error",
        "xfailed",
        UNDEFINED,
        "pending",
        "ambiguous",
    }
)

#: The statuses whose *exclusion* is settled by the same measurement: the two
#: halves of ``Status.isOk()``.  ``skipped`` is deliberately here -- the steps
#: behave skips after a failure never put a scenario in the manifest on their
#: own account, because the failing step already did.
NON_FAILING_STATUSES: Final[frozenset[str]] = frozenset({PASSED, SKIPPED})

#: Gherkin keywords, as ``app/reporting/events.py`` records them.
SCENARIO_KEYWORD: Final[str] = "Scenario"
OUTLINE_KEYWORD: Final[str] = "Scenario Outline"
BACKGROUND_KEYWORD_NAME: Final[str] = "Background"

#: Neutral text for a synthetic step and element.  Nothing asserts on it; it
#: exists so the documents are well-formed rather than minimal.
STEP_NAME: Final[str] = "the user is on the landing page"
STEP_LOCATION: Final[str] = "features.steps.crm_steps.step_impl"
SCENARIO_NAME: Final[str] = "a synthetic scenario"
BACKGROUND_NAME: Final[str] = "a synthetic background"

#: Body of a stand-in feature file, for the two tests that need a manifest
#: entry to be backed by a real file on disk.  Nothing parses it: the parser's
#: filesystem tier asks only whether the entry names a regular, non-symlinked
#: file inside the features directory, so the content exists to make the file
#: recognisably Gherkin to a human reading the temporary directory.
GHERKIN_STANDIN: Final[str] = "Feature: a stand-in for the confinement tier\n"

#: Default line numbers for the synthetic documents.  ``Crm.feature`` carries
#: its ``Feature:`` on line 2 because line 1 is the ``@Smoke`` tag, and every
#: Background occurrence in the reference report shares the Background's own
#: line 6 -- which is exactly why emitting a background's line would be wrong.
FEATURE_LINE: Final[int] = 2
BACKGROUND_LINE: Final[int] = 6
DEFAULT_STEP_LINE: Final[int] = 10

#: The Cucumber-JVM baseline for a **Background-only failure**, measured
#: offline: Cucumber-JVM 7.2.3 (``cucumber-java`` 7.2.3, ``cucumber-junit``
#: 7.3.4, JUnit 4.13.2) driven through ``io.cucumber.core.cli.Main`` with the
#: ``rerun:`` and ``json:`` plugins, over a probe feature whose Background step
#: fails and whose two following scenarios -- at lines 6 and 10 -- hold steps
#: that would pass.
#:
#: The manifest it wrote is below.  Three properties of it are what this port
#: is held to, and none of them is inferred: the **scenario** lines appear,
#: they are **grouped on one line** for the feature, and the Background's own
#: line is **absent** -- a Background is not an addressable test case.  The
#: probe's own directory component is reached through
#: ``paths.NORMALIZED_FEATURES_PREFIX`` for the reason
#: :func:`test_this_module_names_no_feature_directory_prefix` states: no test
#: in this file spells that prefix.  The probe's path is deliberately *not*
#: compared with this port's, whose feature directory AAP deviation 1 moved;
#: the committed reference manifest remains the only byte-for-byte baseline
#: and is untouched by this measurement.
JVM_BACKGROUND_ONLY_MANIFEST: Final[str] = (
    f"{paths.FILE_URI_SCHEME}src/test/resources/"
    f"{paths.NORMALIZED_FEATURES_PREFIX}Probe.feature:6:10"
)

#: The result keys the same run's JSON carried on the **failed Background
#: step**: a duration, the failure text and the status, and the status is
#: ``failed``.
JVM_FAILED_STEP_RESULT_KEYS: Final[frozenset[str]] = frozenset(
    {"duration", "error_message", "status"}
)

#: The result keys it carried on each **scenario step behave skipped** after
#: the Background failed: the status alone, in this order, with **no
#: ``duration``** -- a skipped step's duration is absent rather than zero.
JVM_SKIPPED_STEP_RESULT_KEYS: Final[tuple[str, ...]] = ("status",)

#: The key **no element** of that JSON carried.  Cucumber's elements have no
#: status of their own: a scenario's outcome is derived from its steps and the
#: Background occurrence in front of it, which is why the roll-up lives in
#: ``app/reporting/aggregation.py`` and not in either artifact.
JVM_ELEMENT_STATUS_KEY_ABSENT: Final[str] = "status"

#: behave's own rerun formatter header, measured in ``behave/formatter/rerun.py``
#: and the shape this writer must never pass through.  The port's manifest is
#: grouped, ``file:``-prefixed and headerless.
BEHAVE_RERUN_HEADER: Final[str] = (
    "# -- RERUN: 2 failing scenarios during last test run."
)

#: Substrings that must never appear inside a *selected path*.  A writer that
#: passed behave's header through, or a parser that turned a hand-written
#: comment into a selection, would produce one of these; asserted over every
#: emitted line by :func:`_assert_manifest_shape`.
NEVER_IN_A_SELECTED_PATH: Final[tuple[str, ...]] = ("#", "RERUN", " ")

#: The ``source`` label every parse in this module passes.  A rejection message
#: has to name where the entry came from -- that is how an operator finds it,
#: given that the message deliberately reproduces nothing of the entry itself --
#: so the label is one constant the assertions can look for rather than a word
#: repeated per test.
SOURCE_LABEL: Final[str] = "a-parsed-manifest"

#: A run specification: features in source order, each with its scenarios as
#: ``(line, status)`` pairs.  Used by the round-trip tests, where the point is
#: that the manifest selects exactly the failing pairs and nothing else.
RunSpec = Sequence[tuple[str, Sequence[tuple[int, str]]]]


# =========================================================================== #
# Document builders
#
# Everything below is built through the production builders in
# ``app/reporting/events.py`` rather than as hand-written dicts, so a change to
# the internal schema reaches these tests instead of being papered over by a
# fixture that still has the old shape.
# =========================================================================== #


def _feature_path(filename: str) -> str:
    """Return a feature's repository-relative path for ``filename``.

    The directory component comes from ``paths.NORMALIZED_FEATURES_PREFIX``,
    the production constant, which is what keeps this module free of a prefix
    literal (see :func:`test_this_module_names_no_feature_directory_prefix`).

    :param filename: A feature file's name, e.g. :data:`CRM_FEATURE`.
    :returns: The path the manifest is expected to name.
    """
    return f"{paths.NORMALIZED_FEATURES_PREFIX}{filename}"


def _step(status: str | None, *, line: int = DEFAULT_STEP_LINE) -> JsonDict:
    """Build one step object carrying ``status``.

    :param status: The step's outcome, or ``None`` for a step whose result is
        not yet known -- the shape a dry run leaves behind, and one the writer
        must not read as a failure.
    :param line: The step's line in the feature file.  For an outline row this
        is the *template's* step line, never the data row's.
    :returns: The step object.
    """
    matched = status is not None and status != UNDEFINED
    return new_step(
        keyword="Given",
        line=line,
        name=STEP_NAME,
        matched=matched,
        # An undefined step carries an empty ``match``, which is what lets the
        # JSON writer omit ``location`` exactly as the JVM does; a step with no
        # result yet has nothing resolved either.
        match={"location": STEP_LOCATION} if matched else {},
        result=None if status is None else {"status": status, "duration": 0},
    )


def _scenario(
    line: int,
    *,
    statuses: Sequence[str | None] = (PASSED,),
    hook_statuses: Sequence[str] = (),
    selected: bool = True,
    keyword: str = SCENARIO_KEYWORD,
    step_lines: Sequence[int] | None = None,
) -> JsonDict:
    """Build a scenario element.

    :param line: The element's own line -- for an outline row, the data row's
        line, which is the number the manifest must carry.
    :param statuses: One status per step, in order.
    :param hook_statuses: One status per ``after`` hook entry.  A failed hook
        counts, because the JVM's rerun formatter keys on the test-case result,
        which subsumes hooks.
    :param selected: Whether the effective tag expression selected it.
    :param keyword: ``Scenario`` or ``Scenario Outline``.
    :param step_lines: Explicit line numbers for the steps, so a test can make
        the step lines differ from the element's line the way an outline row's
        genuinely do.
    :returns: The scenario element.
    """
    if step_lines is None:
        step_lines = tuple(
            DEFAULT_STEP_LINE + offset for offset in range(len(statuses))
        )
    return new_element(
        element_type=ELEMENT_TYPE_SCENARIO,
        keyword=keyword,
        line=line,
        name=SCENARIO_NAME,
        selected=selected,
        identifier=f"synthetic-scenario-{line}",
        start_timestamp=None,
        steps=[
            _step(status, line=step_line)
            for status, step_line in zip(statuses, step_lines, strict=True)
        ],
        after=[new_hook_entry(status=status) for status in hook_statuses],
    )


def _background(
    *,
    line: int = BACKGROUND_LINE,
    statuses: Sequence[str | None] = (PASSED,),
) -> JsonDict:
    """Build a Background occurrence.

    A Background carries no ``id``, no ``start_timestamp``, no ``tags`` and no
    ``after`` by contract, and it is **not** a scenario: it never contributes a
    line number of its own.

    :param line: The Background's line, shared by every occurrence of it.
    :param statuses: One status per background step.
    :returns: The background element.
    """
    return new_element(
        element_type=ELEMENT_TYPE_BACKGROUND,
        keyword=BACKGROUND_KEYWORD_NAME,
        line=line,
        name=BACKGROUND_NAME,
        steps=[_step(status) for status in statuses],
    )


def _failed_background_occurrence() -> JsonDict:
    """Build a Background occurrence whose step failed the way a real one does.

    :func:`_background` records a status and a zero duration, which is all most
    tests need; a *genuine* failure also carries the time it took and the
    assertion text, and both are load-bearing here: the JVM baseline's failed
    Background step carries exactly ``duration``, ``error_message`` and
    ``status``, and the JSON writer omits a zero duration and an absent
    message.

    :returns: The Background occurrence, on :data:`BACKGROUND_LINE` like every
        other occurrence of one.
    """
    return new_element(
        element_type=ELEMENT_TYPE_BACKGROUND,
        keyword=BACKGROUND_KEYWORD_NAME,
        line=BACKGROUND_LINE,
        name=BACKGROUND_NAME,
        steps=[
            new_step(
                keyword="Given",
                line=DEFAULT_STEP_LINE,
                name=STEP_NAME,
                matched=True,
                match={"location": STEP_LOCATION},
                result={
                    "status": FAILED,
                    "duration": FAILED_STEP_DURATION_NS,
                    "error_message": FAILED_STEP_MESSAGE,
                },
            )
        ],
    )


def _dry_run_unmatched_scenario(line: int) -> JsonDict:
    """Build the scenario a dry run leaves behind for an unresolved step.

    The shape is behave's own and it is the whole point of the dry-run rule:
    the step carries ``matched=False`` and an empty ``match``, while the status
    behave records is :data:`DRY_RUN_RECORDED_STATUS` -- the same ``untested``
    it records for a step that *did* resolve.  Only the match state
    distinguishes them, which is why the manifest has to read it.

    :param line: The scenario element's own line, the number the manifest must
        carry.
    :returns: The scenario element.
    """
    return new_element(
        element_type=ELEMENT_TYPE_SCENARIO,
        keyword=SCENARIO_KEYWORD,
        line=line,
        name=SCENARIO_NAME,
        identifier=f"synthetic-scenario-{line}",
        steps=[
            new_step(
                keyword="Given",
                line=DEFAULT_STEP_LINE,
                name=STEP_NAME,
                matched=False,
                match={},
                result={"status": DRY_RUN_RECORDED_STATUS},
            )
        ],
    )


def _feature_at(
    path: str,
    elements: Sequence[JsonDict],
    *,
    line: int = FEATURE_LINE,
) -> JsonDict:
    """Build a feature object for an already-composed ``path``.

    Both ``uri`` and ``path`` are populated, exactly as the collector does, so
    that the writer's documented preference for ``path`` is exercised rather
    than bypassed.

    :param path: The feature's repository-relative path.
    :param elements: Its elements, in collection order.
    :param line: The ``Feature:`` line.
    :returns: The feature object.
    """
    return new_feature(
        uri=f"{paths.FILE_URI_SCHEME}{path}",
        path=path,
        identifier=path,
        line=line,
        name=f"Testinium app {Path(path).stem} Module",
        elements=list(elements),
    )


def _feature(
    filename: str,
    elements: Sequence[JsonDict],
    *,
    line: int = FEATURE_LINE,
) -> JsonDict:
    """Build a feature object for a feature *filename*.

    :param filename: The feature file's name.
    :param elements: Its elements, in collection order.
    :param line: The ``Feature:`` line.
    :returns: The feature object.
    """
    return _feature_at(_feature_path(filename), elements, line=line)


def _result_set(*features: JsonDict, dry_run: bool = False) -> ResultSet:
    """Wrap ``features`` in a merged result document.

    ``metadata`` is passed explicitly rather than left to
    :func:`app.reporting.events.run_metadata`, so that no test document depends
    on the host it was built on.

    :param features: Feature objects, in source order.
    :param dry_run: Whether the document describes a dry run, which is the
        flag the writer reads to grade a step by its match state instead of by
        the status behave recorded.
    :returns: The result set.
    """
    return new_result_set(metadata={}, dry_run=dry_run, features=list(features))


def _run(spec: RunSpec) -> ResultSet:
    """Build a result set from a compact ``(filename, [(line, status)])`` spec.

    One scenario per pair, in the order given, so a test states the run it
    means in one expression and the expected selection follows from the same
    data through :func:`_expected_locations`.

    :param spec: Features in source order, each with its scenarios.
    :returns: The result set.
    """
    return _result_set(
        *(
            _feature(
                filename,
                [_scenario(line, statuses=(status,)) for line, status in scenarios],
            )
            for filename, scenarios in spec
        )
    )


def _expected_locations(spec: RunSpec) -> list[str]:
    """Return the ``path:line`` locations a rerun of ``spec`` must execute.

    Derived from the same data the document is built from, and deliberately
    *not* from the writer's output: that is what makes the round-trip
    assertions independent of the code they test.

    :param spec: The run specification.
    :returns: One location per failing scenario, features in source order and
        line numbers ascending within a feature -- the order the grouped
        manifest imposes.
    """
    locations: list[str] = []
    for filename, scenarios in spec:
        failing = sorted(
            {line for line, status in scenarios if status == FAILED}
        )
        locations.extend(
            f"{_feature_path(filename)}{rerun_report.LINE_SEPARATOR}{line}"
            for line in failing
        )
    return locations


# =========================================================================== #
# Shared assertions
# =========================================================================== #


def _assert_manifest_shape(lines: Sequence[str]) -> None:
    """Assert the invariants every emitted manifest line must satisfy.

    Applied wherever output is produced, so that a regression in any of them is
    caught by whichever test runs first rather than only by the one test that
    names it:

    * the literal ``file:`` prefix;
    * a repository-relative path -- never absolute, never ``./``-prefixed;
    * at least one line number after the path;
    * no header, no comment and no embedded terminator.

    :param lines: The lines as :func:`build_rerun_lines` returned them.
    """
    for line in lines:
        assert line.startswith(paths.FILE_URI_SCHEME), line
        body = line[len(paths.FILE_URI_SCHEME) :]
        assert not body.startswith("/"), f"absolute path in manifest: {line}"
        assert not body.startswith("."), f"relative marker in manifest: {line}"
        assert not line.startswith(rerun_report.COMMENT_PREFIX), line
        assert BEHAVE_RERUN_HEADER not in line, line
        assert "\r" not in line and "\n" not in line, repr(line)
        head, separator, tail = body.rpartition(rerun_report.LINE_SEPARATOR)
        assert separator, f"no line number in manifest entry: {line}"
        assert head, f"no feature path in manifest entry: {line}"
        assert tail.isdigit() and int(tail) > 0, line
        for fragment in NEVER_IN_A_SELECTED_PATH:
            assert fragment not in head, f"{fragment!r} in a selected path: {line}"


def _assert_names_the_source_and_the_position(
    error: rerun_report.RerunManifestError, *, number: int
) -> None:
    """Assert a refusal is locatable and carries none of the refused entry.

    The two halves of the diagnostic contract, applied wherever a line is
    refused:

    * it names the source and the entry's one-based position, because the AAP
      0.4.1 exit table reports the problem on stderr and exits ``0``, so the
      message is the only thing an operator has to find the entry by;
    * it is data-free -- the manifest is untrusted machine input and this text
      is recorded verbatim by Jenkins, so reproducing the entry is how a
      tampered file forges a console record or leaks its payload (CWE-117,
      CWE-532).  The per-case absence of the refused text is asserted by the
      tests that own each shape, since only they know which part of their
      input was data.

    :param error: The refusal the parser raised.
    :param number: The one-based position the message must carry.
    """
    message = str(error)
    assert SOURCE_LABEL in message, message
    assert f"line {number}" in message, message


def _line_numbers_of(line: str) -> list[int]:
    """Return the trailing line numbers of one manifest line.

    :param line: One manifest line.
    :returns: The numbers in the order the line spells them, so a test can
        assert on ordering rather than only on membership.
    """
    body = line[len(paths.FILE_URI_SCHEME) :]
    segments = body.split(rerun_report.LINE_SEPARATOR)
    numbers: list[int] = []
    while len(segments) > 1 and segments[-1].isdigit():
        numbers.insert(0, int(segments.pop()))
    return numbers


def _line_numbers_as_text(line: str) -> str:
    """Return a manifest line's numeric tail as text, for a negative check.

    :param line: One manifest line.
    :returns: The line numbers joined by the separator, so that asserting a
        number's *absence* cannot be satisfied accidentally by the path.
    """
    return rerun_report.LINE_SEPARATOR.join(
        str(number) for number in _line_numbers_of(line)
    )


# =========================================================================== #
# Section 1 -- the pinned baseline, byte for byte
# =========================================================================== #


@pytest.fixture
def golden_entry(golden_rerun_normalized: str) -> rerun_report.RerunEntry:
    """The normalized baseline, parsed into its single entry.

    Reading the feature path out of the fixture rather than writing it down is
    what lets the byte-equality tests below name no prefix at all: the path the
    synthetic document is built at *is* the baseline's own path.

    :param golden_rerun_normalized: The baseline with the port's feature
        directory substituted by the production normalizer.
    :returns: The one entry the baseline holds.
    """
    entries = rerun_report.parse_rerun_text(
        golden_rerun_normalized, source="golden_rerun.txt"
    )
    assert len(entries) == 1, entries
    return entries[0]


@pytest.fixture
def golden_result_set(golden_entry: rerun_report.RerunEntry) -> ResultSet:
    """A result set describing the run the baseline records.

    One feature, the Background repeated before each scenario as the reference
    report interleaves them, and four scenarios of which exactly two fail: the
    plain scenario at the first baseline line, and the outline **data row** at
    the second, whose steps deliberately carry the outline template's lines so
    that using a step's line instead of the element's would show up.

    :param golden_entry: The parsed baseline.
    :returns: The result set the writer must turn back into the baseline.
    """
    first, second = golden_entry.lines
    return _result_set(
        _feature_at(
            golden_entry.path,
            [
                _background(),
                # Expected IN the manifest: a literal ``failed`` step, the one
                # spelling the baseline's own run carries.  The rest of the
                # failure vocabulary is exercised where it is the subject, by
                # test_every_failure_status_selects_the_scenario.
                _scenario(first, statuses=(PASSED, FAILED, SKIPPED)),
                _background(),
                # Expected OUT: nothing here is ``failed``.
                _scenario(first + 2, statuses=(PASSED, PASSED)),
                _background(),
                _scenario(second - 1, statuses=(PASSED,)),
                _background(),
                _scenario(
                    second,
                    keyword=OUTLINE_KEYWORD,
                    statuses=(FAILED,),
                    step_lines=(second - 7,),
                ),
            ],
        )
    )


def test_golden_fixture_is_the_measured_reference_artifact(
    golden_rerun: str, fixtures_dir: Path
) -> None:
    """Guard the baseline itself against being edited to match the writer.

    Were this to fail, every byte-equality assertion below would still pass
    while measuring nothing: the fixture would have become a copy of the output
    it is supposed to judge.  The properties asserted are the measured ones --
    50 bytes, one line, one trailing LF, no CR, no header.
    """
    payload = (fixtures_dir / "golden_rerun.txt").read_bytes()
    assert len(payload) == GOLDEN_RERUN_BYTES
    assert (
        hashlib.md5(payload, usedforsecurity=False).hexdigest() == GOLDEN_RERUN_MD5
    )
    assert golden_rerun.endswith(rerun_report.LINE_ENDING)
    assert golden_rerun.count(rerun_report.LINE_ENDING) == 1
    assert "\r" not in golden_rerun
    assert rerun_report.COMMENT_PREFIX not in golden_rerun
    assert golden_rerun.startswith(paths.FILE_URI_SCHEME)


def test_golden_manifest_is_one_grouped_line_for_one_feature(
    golden_entry: rerun_report.RerunEntry,
) -> None:
    """The format is one line per feature, not one line per failure.

    Were this to fail the baseline would have been misread as behave's own
    ungrouped shape, and every grouping assertion in this module would be
    measuring the wrong contract.
    """
    assert golden_entry.lines == GOLDEN_SCENARIO_LINES
    assert golden_entry.path.endswith(CRM_FEATURE)
    assert golden_entry.path == _feature_path(CRM_FEATURE)


def test_build_rerun_lines_reproduces_the_golden_line(
    golden_result_set: ResultSet, golden_rerun_normalized: str
) -> None:
    """The pure builder emits exactly the baseline's single line.

    Were this to fail, the port would be selecting a different set of scenarios
    for a rerun than the reference implementation did for the same run.
    """
    lines = rerun_report.build_rerun_lines(golden_result_set)

    assert lines == [golden_rerun_normalized.rstrip(rerun_report.LINE_ENDING)]
    _assert_manifest_shape(lines)
    assert _line_numbers_of(lines[0]) == list(GOLDEN_SCENARIO_LINES)


def test_build_rerun_text_equals_the_golden_manifest_byte_for_byte(
    golden_result_set: ResultSet, golden_rerun_normalized: str
) -> None:
    """The complete text matches the baseline exactly, terminator included.

    This is the one artifact where byte equality is legitimate -- it carries no
    timestamp, no duration and no traceback -- so a difference of a single byte
    is a real defect and not run-to-run variance.  Were this to fail, a second
    runner would be handed a file whose grammar differs from the reference's.
    """
    assert rerun_report.build_rerun_text(golden_result_set) == (
        golden_rerun_normalized
    )


def test_written_file_equals_the_golden_manifest_byte_for_byte(
    golden_result_set: ResultSet,
    golden_rerun_normalized: str,
    tmp_artifact_root: Path,
) -> None:
    """What reaches disk is the same bytes the pure builder produced.

    Were this to fail the writer would be adding something on the way out -- a
    BOM, a CRLF translation or a trailing blank line -- that the in-memory
    assertions above could not see.
    """
    written = rerun_report.write_rerun_txt(
        golden_result_set, base=tmp_artifact_root
    )

    assert written.read_bytes() == golden_rerun_normalized.encode("utf-8")


def test_this_module_names_no_feature_directory_prefix() -> None:
    """This file must reach the feature directory only through production code.

    AAP 0.4.1 requires the legacy-to-port prefix substitution to have a single
    owner, ``app/utils/paths.py``, so that the golden fixtures can stay
    verbatim.  A literal prefix typed into a test would fork that rule, and the
    fork would only surface when the directory moved again.  Were this to fail,
    this module would have acquired a second, silent copy of the substitution.
    """
    source = Path(__file__).read_text(encoding="utf-8")

    assert paths.LEGACY_FEATURES_PREFIX
    assert paths.NORMALIZED_FEATURES_PREFIX
    assert paths.LEGACY_FEATURES_PREFIX not in source
    assert paths.NORMALIZED_FEATURES_PREFIX not in source


# =========================================================================== #
# Section 2 -- the grouping rule
# =========================================================================== #


def test_two_failures_in_one_feature_yield_one_line_with_both_numbers() -> None:
    """Grouping, ordering and deduplication, in one document.

    The failures are supplied out of order and one of them twice -- the shape a
    cross-worker merge produces -- so the assertion proves the writer sorts and
    deduplicates rather than echoing what it was given.  Were this to fail the
    manifest could carry two lines for one feature, or ``:9:9``, neither of
    which the grammar allows.
    """
    result_set = _result_set(
        _feature(
            CRM_FEATURE,
            [
                _scenario(24, statuses=(FAILED,)),
                _scenario(9, statuses=(FAILED,)),
                _scenario(24, statuses=(FAILED,)),
            ],
        )
    )

    lines = rerun_report.build_rerun_lines(result_set)

    assert len(lines) == 1
    assert _line_numbers_of(lines[0]) == [9, 24]
    _assert_manifest_shape(lines)


def test_three_failures_yield_three_ascending_numbers() -> None:
    """The line grows by one number per failing scenario, without a cap.

    Were this to fail, a feature with more than two failures would have part of
    its selection dropped -- the failures would be reported and never retried.
    """
    result_set = _result_set(
        _feature(
            CRM_FEATURE,
            [
                _scenario(31, statuses=(FAILED,)),
                _scenario(9, statuses=(FAILED,)),
                _scenario(16, statuses=(FAILED,)),
            ],
        )
    )

    (line,) = rerun_report.build_rerun_lines(result_set)

    assert _line_numbers_of(line) == [9, 16, 31]


def test_each_failing_feature_contributes_one_line_in_source_order() -> None:
    """Features come out in the order the result set holds them.

    ``sortingMethod: 'ALPHABETICAL'`` in ``Jenkins:15`` is a publisher display
    option and imposes nothing on an artifact, so the subjects here are in a
    deliberately non-alphabetical order and the assertion states both halves:
    the output follows the document, and it is *not* sorted.  Were this to fail
    a rerun would still execute the right scenarios, but the manifest would no
    longer be a deterministic function of the run's structure, which AAP 0.6
    requires.
    """
    order = (SESSION_FEATURE, CRM_FEATURE, CONTACT_FEATURE)
    result_set = _result_set(
        *(
            _feature(filename, [_scenario(12, statuses=(FAILED,))])
            for filename in order
        )
    )

    lines = rerun_report.build_rerun_lines(result_set)

    assert lines == [
        f"{paths.FILE_URI_SCHEME}{_feature_path(filename)}"
        f"{rerun_report.LINE_SEPARATOR}12"
        for filename in order
    ]
    assert lines != sorted(lines)


@pytest.mark.parametrize(
    ("statuses", "description"),
    [
        ((PASSED, PASSED), "all steps passed"),
        ((SKIPPED, SKIPPED), "every step skipped"),
        ((PASSED, SKIPPED), "a step skipped after a passing one"),
        ((None, None), "no result recorded at all"),
    ],
    ids=["passed", "skipped", "passed-then-skipped", "no-result"],
)
def test_only_failures_contribute_a_line_number(
    statuses: tuple[str | None, ...], description: str
) -> None:
    """A scenario that did not fail is never selected for a rerun.

    The manifest exists to re-execute what demonstrably failed; inventing a
    selection from a passing, skipped or result-less scenario would retry work
    that either succeeded or never ran.  Only statuses whose exclusion is
    settled appear here, and :data:`NON_FAILING_STATUSES` records why they are
    the only two: the JVM's ``Status.isOk()`` is ``PASSED || SKIPPED``, so
    every other outcome -- ``undefined`` included, which
    :func:`test_every_failure_status_selects_the_scenario` pins on the other
    side of the same rule -- puts the test case in the JVM's own manifest.
    The result-less case is structural rather than status-based.  Were this to
    fail, a clean run would still produce a non-empty manifest.
    """
    result_set = _result_set(
        _feature(SALES_FEATURE, [_scenario(12, statuses=statuses)])
    )

    assert rerun_report.build_rerun_lines(result_set) == [], description
    assert rerun_report.build_rerun_text(result_set) == ""


def test_the_failure_vocabulary_is_exactly_the_measured_one() -> None:
    """The set of failing statuses is stated once and measured, not inferred.

    Compared against :data:`FAILING_STATUSES`, which carries the provenance of
    every member, so the assertion fails whether a status is added to the
    vocabulary or removed from it.  The two exclusions that settle the rule are
    asserted explicitly as well, because they are the halves of the JVM's
    ``Status.isOk()`` and everything else follows from them.
    """
    assert rerun_report.FAILURE_STATUSES == FAILING_STATUSES
    assert rerun_report.FAILED_STATUS in rerun_report.FAILURE_STATUSES
    assert not (NON_FAILING_STATUSES & rerun_report.FAILURE_STATUSES)
    for status in sorted(FAILING_STATUSES):
        assert rerun_report.is_failure_status(status), status
    for status in sorted(NON_FAILING_STATUSES):
        assert not rerun_report.is_failure_status(status), status
    # ``xpassed`` is the one alias the JSON writer folds onto ``passed``, so it
    # is excluded for the same reason ``passed`` is; and an absent status is
    # never a failure, which is what keeps a result-less scenario out.
    assert not rerun_report.is_failure_status("xpassed")
    assert not rerun_report.is_failure_status(None)


def test_the_manifests_failure_rule_is_the_shared_models_rule() -> None:
    """The manifest and the report surfaces grade one status once.

    ``is_failure_status`` no longer compares against a private vocabulary: it
    asks :func:`app.reporting.aggregation.is_failure_token`, which
    canonicalises the recorded name through the shared status table and tests
    membership of the complement of Cucumber's ``Status.isOk()``.
    :data:`FAILURE_STATUSES` therefore *states* the membership and no longer
    decides it, and this test holds the two to each other over every name
    either side knows -- the seven Cucumber statuses, the ten behave-only
    aliases, this module's three redundant spellings, a blank and an invented
    token -- so a change on either side fails here instead of silently making
    ``rerun.txt`` disagree with ``cucumber.json`` and with both HTML
    artifacts.  Were this to fail, one run would be graded twice: a scenario
    the report calls a failure would not be offered for retry, or one it calls
    a pass would be retried.
    """
    vocabulary = sorted(
        {
            *aggregation.KNOWN_STATUSES,
            *aggregation.STATUS_ALIASES,
            *rerun_report.FAILURE_STATUSES,
            *rerun_report.STATUS_SPELLINGS,
            aggregation.UNKNOWN_STATUS,
            "",
            "no-such-status",
        }
    )

    for status in vocabulary:
        stated = rerun_report.normalize_status(status) in rerun_report.FAILURE_STATUSES
        assert rerun_report.is_failure_status(status) is stated, status
        assert rerun_report.is_failure_status(status) is (
            aggregation.is_failure_token(status)
        ), status
    # The set is exactly the recorded names whose canonical token is a failure.
    assert {
        status
        for status in vocabulary
        if aggregation.canonical_status(status) in aggregation.FAILURE_TOKENS
    } >= rerun_report.FAILURE_STATUSES
    # ``normalize_status`` keeps its spelling-only contract: it folds a name,
    # never an outcome, and answers ``""`` for a node that carries none.
    assert rerun_report.normalize_status("  HOOK_ERROR ") == "hook_error"
    assert rerun_report.normalize_status(None) == ""


@pytest.mark.parametrize("status", sorted(FAILING_STATUSES))
def test_every_failure_status_selects_the_scenario(status: str) -> None:
    """Each member of the vocabulary really does reach the manifest.

    The vocabulary test above states the rule; this one exercises it through
    the writer, so a set that lists a status the roll-up does not act on still
    fails.  ``undefined`` is the member worth naming: a scenario with an
    unmatched step is a test case whose ``Status.isOk()`` is false, so the
    JVM's own rerun formatter records it, and omitting it here would have
    ``cucumber.json`` report an outcome that is not ``passed`` for a scenario
    ``rerun.txt`` never offers to retry -- the two are read as a matched pair.
    """
    result_set = _result_set(
        _feature(SALES_FEATURE, [_scenario(12, statuses=(PASSED, status))])
    )

    lines = rerun_report.build_rerun_lines(result_set)

    assert lines == [f"{paths.FILE_URI_SCHEME}{_feature_path(SALES_FEATURE)}:12"]
    _assert_manifest_shape(lines)


@pytest.mark.parametrize(
    ("spelling", "folded"),
    sorted(rerun_report.STATUS_SPELLINGS.items()),
)
def test_a_redundant_status_spelling_is_folded_before_the_rule_applies(
    spelling: str, folded: str
) -> None:
    """behave's alternative spellings select exactly what they stand for.

    ``normalize_status`` folds spelling and never outcome, so
    ``untested_undefined`` -- which is what behave records for an unmatched
    step under ``--dry-run`` -- selects the scenario because ``undefined``
    does, and not because the name contains ``untested``.  Were this to fail,
    a dry run's unmatched steps and a real run's would disagree about what the
    manifest holds.
    """
    assert rerun_report.normalize_status(spelling) == folded
    assert rerun_report.is_failure_status(spelling) is (
        folded in rerun_report.FAILURE_STATUSES
    )

    result_set = _result_set(
        _feature(SALES_FEATURE, [_scenario(12, statuses=(PASSED, spelling))])
    )
    expected = (
        [f"{paths.FILE_URI_SCHEME}{_feature_path(SALES_FEATURE)}:12"]
        if folded in rerun_report.FAILURE_STATUSES
        else []
    )

    assert rerun_report.build_rerun_lines(result_set) == expected


def test_a_dry_run_selects_the_scenario_whose_step_resolved_to_nothing() -> None:
    """The measured ``--dry-run`` rule, which behave's own statuses cannot give.

    Measured against Cucumber-JVM 7.2.3 (``io.cucumber.core.cli.Main`` with
    the ``rerun:`` and ``json:`` plugins): under ``dryRun`` a **matched** step
    is reported ``passed`` and an **unmatched** one ``undefined`` with an empty
    ``match``, and the rerun formatter then records the scenario holding the
    undefined step and no other.  behave instead records ``untested`` for
    every step of a dry run, so a writer that read the recorded status alone
    produced an **empty** manifest for a run with unresolved step definitions
    -- measured in this port before the rule was shared: one dry run wrote
    nineteen ``passed`` steps into ``cucumber.json`` while the manifest
    selected nothing.

    Both halves are asserted from one document, so the rule cannot be
    satisfied by selecting everything: the scenario whose steps all matched is
    absent, and the one holding the unmatched step is present with its own
    line.  Were this to fail, ``run-tests --dry-run --rerun`` would offer
    nothing to retry for a suite whose step definitions do not resolve.
    """
    resolved_line = 12
    unresolved_line = 24
    result_set = _result_set(
        _feature(
            SALES_FEATURE,
            [
                _scenario(resolved_line, statuses=(DRY_RUN_RECORDED_STATUS,)),
                _dry_run_unmatched_scenario(unresolved_line),
            ],
        ),
        dry_run=True,
    )

    lines = rerun_report.build_rerun_lines(result_set)

    assert lines == [
        f"{paths.FILE_URI_SCHEME}{_feature_path(SALES_FEATURE)}"
        f"{rerun_report.LINE_SEPARATOR}{unresolved_line}"
    ]
    _assert_manifest_shape(lines)
    assert str(resolved_line) not in _line_numbers_as_text(lines[0])
    # And the same document read as an ordinary run selects nothing at all:
    # ``untested`` is not a failure, which is what keeps every scenario of a
    # dry run out of the manifest when its definitions do resolve.
    assert rerun_report.build_rerun_lines(
        _result_set(
            _feature(
                SALES_FEATURE,
                [
                    _scenario(resolved_line, statuses=(DRY_RUN_RECORDED_STATUS,)),
                    _dry_run_unmatched_scenario(unresolved_line),
                ],
            )
        )
    ) == []


def test_a_feature_without_failures_contributes_no_line_at_all() -> None:
    """No blank line, and no bare ``file:<path>`` either.

    A zero-failure feature is absent from the manifest rather than present and
    empty.  Were this to fail, a runner would be handed a location it cannot
    resolve -- a path with no line number -- for a feature that passed.
    """
    result_set = _result_set(
        _feature(CONTACT_FEATURE, [_scenario(19, statuses=(PASSED,))]),
        _feature(CRM_FEATURE, [_scenario(16, statuses=(FAILED,))]),
        _feature(INVENTORY_FEATURE, [_scenario(11, statuses=(PASSED,))]),
    )

    lines = rerun_report.build_rerun_lines(result_set)
    text = rerun_report.build_rerun_text(result_set)

    assert len(lines) == 1
    assert CRM_FEATURE in lines[0]
    assert CONTACT_FEATURE not in text
    assert INVENTORY_FEATURE not in text
    assert text.count(rerun_report.LINE_ENDING) == 1
    assert not text.startswith(rerun_report.LINE_ENDING)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda document: document,
        lambda document: document | {"features": []},
        lambda document: {
            key: value for key, value in document.items() if key != "features"
        },
    ],
    ids=["all-passed", "no-features", "features-key-absent"],
)
def test_a_run_without_failures_writes_a_zero_byte_manifest(
    mutate: Any, tmp_artifact_root: Path
) -> None:
    """All four artifacts are written even when nothing failed.

    The AAP 0.4.1 exit table requires the write to happen for a run with no
    failures and for one whose tag expression selected nothing, so the empty
    state is a zero-byte file -- never an absent file, and never a placeholder
    line or comment standing in for one.  The malformed variants are included
    because a structurally incomplete document must reach the same empty state
    instead of raising: failures are data, and so is their absence.
    """
    result_set = mutate(
        _result_set(_feature(NOTES_FEATURE, [_scenario(7, statuses=(PASSED,))]))
    )

    assert rerun_report.build_rerun_text(result_set) == ""

    written = rerun_report.write_rerun_txt(result_set, base=tmp_artifact_root)

    assert written.is_file()
    assert written.stat().st_size == 0


def test_features_that_share_a_path_collapse_into_one_line() -> None:
    """A cross-worker merge cannot produce two lines for one feature.

    ``app/services/test_run_service.py`` shards by feature and merges the
    workers' documents, and a document can legitimately hold two feature
    objects with the same path.  The format allows exactly one line per
    feature, so they group.  Were this to fail, a sharded run's manifest would
    be shaped differently from a sequential run's for the same failures.
    """
    result_set = _result_set(
        _feature(CRM_FEATURE, [_scenario(24, statuses=(FAILED,))]),
        _feature(CRM_FEATURE, [_scenario(9, statuses=(FAILED,))]),
    )

    (line,) = rerun_report.build_rerun_lines(result_set)

    assert _line_numbers_of(line) == [9, 24]


def test_a_failed_after_hook_puts_its_scenario_in_the_manifest() -> None:
    """Hook failures count, because the JVM keys on the test-case result.

    The environment module's scenario teardown attaches a screenshot and quits
    the driver; a failure there means the scenario's outcome is a failure, so
    the scenario is retried.  Were this to fail, a scenario that failed only in
    teardown would be reported failed and never re-run.
    """
    result_set = _result_set(
        _feature(
            NOTES_FEATURE,
            [
                _scenario(
                    12,
                    statuses=(PASSED,),
                    # ``failed`` on purpose: the settled spelling, unaffected
                    # by the review's widening of the failure vocabulary.
                    hook_statuses=(FAILED,),
                )
            ],
        )
    )

    (line,) = rerun_report.build_rerun_lines(result_set)

    assert _line_numbers_of(line) == [12]


def test_a_failed_background_rolls_up_into_the_following_scenario() -> None:
    """A Background occurrence never contributes its own line number.

    Every Background occurrence shares the Background's own line, so emitting
    it would select the Background rather than the scenario that failed -- and
    a Background is not addressable as a test case.  The pairing rule is that
    an occurrence belongs to the scenario immediately following it.  Were this
    to fail, a background failure would either be lost from the manifest or
    would select an unrunnable location.
    """
    result_set = _result_set(
        _feature(
            CRM_FEATURE,
            [
                _background(statuses=(FAILED,)),
                _scenario(9, statuses=(PASSED,)),
                _background(statuses=(PASSED,)),
                _scenario(16, statuses=(PASSED,)),
            ],
        )
    )

    (line,) = rerun_report.build_rerun_lines(result_set)

    assert _line_numbers_of(line) == [9]
    assert str(BACKGROUND_LINE) not in _line_numbers_as_text(line)


@pytest.mark.parametrize(
    ("elements_of", "description"),
    [
        (
            lambda: [
                _scenario(9, statuses=(PASSED,)),
                _background(statuses=(FAILED,)),
            ],
            "a trailing Background occurrence follows no scenario",
        ),
        (
            lambda: [
                _background(statuses=(FAILED,)),
                _background(statuses=(PASSED,)),
                _scenario(9, statuses=(PASSED,)),
            ],
            "the earlier of two consecutive occurrences is orphaned",
        ),
    ],
    ids=["trailing-background", "consecutive-backgrounds"],
)
def test_an_orphaned_background_selects_nothing(
    elements_of: Any, description: str
) -> None:
    """A Background failure reaches forward only, and only one step.

    The collector groups an occurrence with the scenario that follows it, so a
    Background with no scenario after it has nothing to fail, and where two
    occurrences follow one another the earlier is not carried forward.  Were
    this to fail, the writer would pair a Background with a scenario the
    collector never associated it with and select an unrelated scenario for
    retry.
    """
    result_set = _result_set(_feature(CRM_FEATURE, elements_of()))

    assert rerun_report.build_rerun_lines(result_set) == [], description


def test_an_outline_row_contributes_the_data_rows_line() -> None:
    """The element's line reaches the manifest, never a step's line.

    An outline row's steps carry the *template's* line numbers while the
    element carries the data row's, and only the latter is re-selectable:
    ``--rerun`` hands ``path:line`` to the engine, which resolves it to the
    specific example row.  Were this to fail, a rerun of a failing outline row
    would either select nothing or re-run every row.
    """
    data_row_line = 24
    template_step_line = 17
    result_set = _result_set(
        _feature(
            CRM_FEATURE,
            [
                _scenario(
                    data_row_line,
                    keyword=OUTLINE_KEYWORD,
                    statuses=(FAILED,),
                    step_lines=(template_step_line,),
                )
            ],
        )
    )

    (line,) = rerun_report.build_rerun_lines(result_set)

    assert _line_numbers_of(line) == [data_row_line]
    assert str(template_step_line) not in _line_numbers_as_text(line)


def test_building_is_pure_and_repeatable(tmp_artifact_root: Path) -> None:
    """The builders read no filesystem, write none, and mutate no input.

    The pure/impure split is what lets the whole format be tested without a
    browser or a temporary directory, and it is what the ``app/reporting``
    coverage gate depends on.  Were this to fail, a writer test could pass once
    and fail on a second call, or a builder could corrupt the single document
    the other three writers are about to consume.
    """
    result_set = _run(
        [
            (CRM_FEATURE, [(9, FAILED), (16, PASSED)]),
            (SALES_FEATURE, [(12, FAILED)]),
        ]
    )
    snapshot = copy.deepcopy(result_set)

    first = rerun_report.build_rerun_lines(result_set)
    second = rerun_report.build_rerun_lines(result_set)

    assert first == second
    assert rerun_report.build_rerun_text(result_set) == (
        rerun_report.build_rerun_text(result_set)
    )
    assert result_set == snapshot
    assert list(tmp_artifact_root.iterdir()) == []


@pytest.mark.parametrize(
    "bad_line",
    [0, -3, None, "nine", True, [9]],
    ids=["zero", "negative", "none", "non-numeric", "boolean", "list"],
)
def test_an_unusable_scenario_line_is_logged_and_omitted(
    bad_line: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """An unusable element is skipped, never raised on and never guessed at.

    A location a runner cannot resolve is worse than a short manifest, and this
    is a defect in the result document rather than a test outcome, so it is
    reported through the log the command-line surface routes to stderr.  Were
    this to fail, one malformed element would either abort the whole write --
    losing the other three artifacts with it -- or emit a line that selects
    nothing.

    A *fractional* line is not among these cases: it coerces to a positive
    integer and is therefore emitted rather than skipped.  See
    :func:`test_a_fractional_scenario_line_is_truncated_and_never_rounded` for
    what that settles and why the collector's own integer check is what keeps
    such a value out of a real run.
    """
    element = _scenario(9, statuses=(FAILED,))
    element["line"] = bad_line
    result_set = _result_set(_feature(CRM_FEATURE, [element]))

    with caplog.at_level(logging.WARNING, logger=rerun_report.__name__):
        lines = rerun_report.build_rerun_lines(result_set)

    assert lines == []
    assert caplog.records, "an omitted failure must be reported"
    assert caplog.records[-1].levelno == logging.WARNING


def test_a_fractional_scenario_line_is_truncated_and_never_rounded() -> None:
    """A fractional line truncates to ``int(line)``, and that is the contract.

    ``app/reporting/rerun_report.py`` coerces an element's ``line`` with
    ``int()``, so ``9.5`` becomes ``9``: a positive integer, and therefore
    emitted rather than skipped.  Truncation is the settled outcome rather than
    one of two, because the *other* route into the writer is closed:
    ``app/reporting/events.py`` validates an element's ``line`` with an
    integer check that rejects a ``float`` outright, so a fractional value
    cannot arrive from a loaded result document at all.  What remains is a
    document built in process -- this test, or a caller holding the builders --
    and for that the coercion is what happens, so it is what is asserted.

    Truncation, not rounding, is the property worth pinning of the two: ``10``
    is a line the document never carried and may well be another scenario's, so
    a value rounded *up* would retry a scenario that passed while leaving the
    one that failed unretried -- worse than a short manifest.

    One hazard of the coercion is recorded rather than asserted, because it is
    a consequence of truncation and not a separate behaviour: where a
    neighbouring element genuinely sits at the truncated line, a fractional
    value selects **that neighbour**, indistinguishably from a manifest that
    named it.  Nothing downstream can detect it, which is why the integer check
    in the collector -- and not an assertion here -- is what keeps such a value
    out of a real run.
    """
    failing = _scenario(9, statuses=(FAILED,))
    failing["line"] = 9.5
    result_set = _result_set(_feature(CRM_FEATURE, [failing]))

    (line,) = rerun_report.build_rerun_lines(result_set)

    assert _line_numbers_of(line) == [9]
    assert rerun_report.parse_rerun_text(
        rerun_report.build_rerun_text(result_set), source=SOURCE_LABEL
    ) == [rerun_report.RerunEntry(path=_feature_path(CRM_FEATURE), lines=(9,))]
    _assert_manifest_shape([line])


def test_a_feature_uri_supplies_the_path_when_path_is_absent(
    golden_entry: rerun_report.RerunEntry, golden_rerun_normalized: str
) -> None:
    """A document carrying only ``uri`` still yields a usable manifest line.

    The collector records both ``uri`` and ``path`` precisely so no consumer
    performs string surgery; the fallback exists for a hand-built or stale
    document.  It strips the scheme through the production constant rather than
    by slicing a literal.  Were this to fail, a worker file written by an
    earlier version of the collector would silently produce an empty manifest.
    """
    feature = _feature_at(
        golden_entry.path,
        [
            _scenario(golden_entry.lines[0], statuses=(FAILED,)),
            _scenario(golden_entry.lines[1], statuses=(FAILED,)),
        ],
    )
    del feature["path"]
    assert feature["uri"].startswith(paths.FILE_URI_SCHEME)

    assert rerun_report.build_rerun_text(_result_set(feature)) == (
        golden_rerun_normalized
    )


@pytest.mark.parametrize(
    "damage",
    [
        lambda feature: feature.update(path="", uri=""),
        lambda feature: [feature.pop("path"), feature.pop("uri")],
        lambda feature: feature.update(path=None, uri=None),
    ],
    ids=["empty-strings", "keys-absent", "null-values"],
)
def test_a_feature_with_no_locatable_path_is_logged_and_omitted(
    damage: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """A feature the manifest cannot name is skipped rather than raised on.

    Same reasoning as an unusable line number: the document is unusable at that
    point, and the other three artifacts must still be written.  Both keys are
    damaged in each case, because ``uri`` is the documented fallback and a
    feature is only unnameable when neither carries a path.  Were this to fail,
    a single malformed feature object would take the whole write down.
    """
    feature = _feature(CRM_FEATURE, [_scenario(9, statuses=(FAILED,))])
    damage(feature)

    with caplog.at_level(logging.WARNING, logger=rerun_report.__name__):
        lines = rerun_report.build_rerun_lines(_result_set(feature))

    assert lines == []
    assert caplog.records
    assert caplog.records[-1].levelno == logging.WARNING


@pytest.mark.parametrize(
    "damage",
    [
        lambda document: document.update(features={"Crm": []}),
        lambda document: document.update(features="not-a-list"),
        lambda document: document.update(features=["not-a-feature", 7, None]),
        lambda document: document["features"][0].update(elements={"a": 1}),
        lambda document: document["features"][0].update(elements="steps"),
        lambda document: document["features"][0].update(
            elements=["not-an-element", None]
        ),
        lambda document: document["features"][0]["elements"][0]["steps"][
            0
        ].update(result="failed"),
        lambda document: document["features"][0]["elements"][0]["steps"][
            0
        ].update(result=None),
    ],
    ids=[
        "features-mapping",
        "features-string",
        "features-of-wrong-type",
        "elements-mapping",
        "elements-string",
        "elements-of-wrong-type",
        "step-result-not-a-mapping",
        "step-result-null",
    ],
)
def test_a_structurally_broken_document_selects_nothing(damage: Any) -> None:
    """Damage anywhere in the document degrades to an empty manifest.

    Nothing in this module raises on a test outcome, and a document the
    collector or a stale worker file left malformed is not an I/O fault either:
    the unusable part is skipped so that the run's other three artifacts are
    still written.  A result that is not a mapping is deliberately included --
    it must never be read as a failure, because inventing one would retry a
    scenario that never ran.  Were this to fail, one damaged worker file would
    abort the whole fan-out.
    """
    document = _result_set(
        _feature(CRM_FEATURE, [_scenario(9, statuses=(FAILED,))])
    )
    damage(document)

    assert rerun_report.build_rerun_lines(document) == []
    assert rerun_report.build_rerun_text(document) == ""


def test_an_elements_own_result_key_is_ignored() -> None:
    """The roll-up is over steps and hooks, never over an element's own key.

    Elements in the internal schema carry no status of their own, so a stray
    ``result`` on one -- which a hand-built or foreign document may supply --
    must neither add a selection nor remove one.  Were this to fail, the
    failure rule would have two sources of truth.
    """
    failing = _scenario(9, statuses=(FAILED,))
    failing["result"] = {"status": PASSED}
    passing = _scenario(16, statuses=(PASSED,))
    passing["result"] = {"status": FAILED}
    result_set = _result_set(_feature(CRM_FEATURE, [failing, passing]))

    (line,) = rerun_report.build_rerun_lines(result_set)

    assert _line_numbers_of(line) == [9]


def test_an_element_of_an_unknown_type_is_treated_as_a_scenario() -> None:
    """An unclassifiable element keeps its result instead of losing it.

    The collector records anything it cannot classify as a scenario, so the
    writer treats an unknown ``type`` the same way rather than dropping a
    failure on the floor -- while a Background, which *is* classified, still
    contributes no line of its own.  Were this to fail, a schema addition would
    silently remove failures from the manifest.
    """
    element = _scenario(9, statuses=(FAILED,))
    element["type"] = "scenario-outline-row"
    result_set = _result_set(_feature(CRM_FEATURE, [element]))

    (line,) = rerun_report.build_rerun_lines(result_set)

    assert _line_numbers_of(line) == [9]


def test_the_writer_emits_no_header_and_no_comment() -> None:
    """behave's own rerun format is never passed through.

    The engine writes a ``# -- RERUN:`` header and one ungrouped ``path:line``
    per failure, with no ``file:`` prefix; the JVM's manifest -- and therefore
    this port's -- is grouped, prefixed and headerless.  Were this to fail, the
    header would be handed to a runner as a feature location.
    """
    result_set = _run(
        [
            (CRM_FEATURE, [(9, FAILED), (24, FAILED)]),
            (SALES_FEATURE, [(12, FAILED)]),
        ]
    )

    text = rerun_report.build_rerun_text(result_set)
    lines = rerun_report.build_rerun_lines(result_set)

    assert BEHAVE_RERUN_HEADER not in text
    assert rerun_report.COMMENT_PREFIX not in text
    assert not any(
        line.startswith(rerun_report.COMMENT_PREFIX) for line in lines
    )
    _assert_manifest_shape(lines)


def test_the_sample_result_set_selects_exactly_its_failing_scenarios(
    sample_result_set: ResultSet, tmp_artifact_root: Path
) -> None:
    """The shared fixture drives the writer to two grouped lines.

    ``tests/fixtures/sample_results.json`` is the single input all four writer
    test modules share, so pinning what the rerun manifest makes of it ties
    this artifact to the same run the other three are asserted against.  Its
    measured non-passing outcomes are three scenarios in two features, and the
    manifest groups them into one line each, features in source order:

    * ``Crm.feature`` lines 16 and 25 -- a plain scenario carrying a failed
      step and a passing ``after`` hook, and an outline data row;
    * ``Sales.feature`` line 12 -- a scenario whose second step is
      ``undefined``.  It is here because the JVM's rerun formatter records any
      test case whose ``Status.isOk()`` is false, and because
      ``cucumber.json`` emits that step as ``undefined`` rather than as
      ``passed``: a scenario the JSON does not report as clean has to be
      re-selectable, or the two artifacts of one run disagree.

    The excluded scenario at ``Sales.feature:36`` stays out on a different
    rule: the tag expression never selected it, so it did not run.  Were this
    to fail, the four artifacts of one run would disagree about what failed.
    """
    lines = rerun_report.build_rerun_lines(sample_result_set)

    assert lines == [
        f"{paths.FILE_URI_SCHEME}{_feature_path(CRM_FEATURE)}:16:25",
        f"{paths.FILE_URI_SCHEME}{_feature_path(SALES_FEATURE)}:12",
    ]
    assert lines[0].endswith(f"{CRM_FEATURE}:16:25")
    assert lines[1].endswith(f"{SALES_FEATURE}:12")
    _assert_manifest_shape(lines)

    # The one scenario the tag expression excluded contributes nothing, so its
    # line is absent from the feature it belongs to.
    assert 36 not in _line_numbers_of(lines[1])

    written = rerun_report.write_rerun_txt(
        sample_result_set, base=tmp_artifact_root
    )

    assert written.read_bytes() == (
        rerun_report.LINE_ENDING.join(lines) + rerun_report.LINE_ENDING
    ).encode()


# =========================================================================== #
# Section 3 -- the parser, its single error channel, and the round trip
# =========================================================================== #


def test_the_parser_reads_the_writers_own_output() -> None:
    """Both halves of the grammar agree, with no file involved.

    ``parse_rerun_file`` accepts an iterable of lines precisely so that the
    round trip can be checked in memory.  Were this to fail, ``--rerun`` would
    be unable to read a manifest this port had just written.
    """
    result_set = _run(
        [
            (CRM_FEATURE, [(9, FAILED), (24, FAILED)]),
            (SESSION_FEATURE, [(5, FAILED)]),
        ]
    )

    entries = rerun_report.parse_rerun_file(
        rerun_report.build_rerun_lines(result_set)
    )

    assert [entry.path for entry in entries] == [
        _feature_path(CRM_FEATURE),
        _feature_path(SESSION_FEATURE),
    ]
    assert [entry.lines for entry in entries] == [(9, 24), (5,)]


def test_the_parser_merges_repeated_features_and_sorts_their_lines() -> None:
    """Reading is as forgiving of duplication as writing is.

    A hand-edited or concatenated manifest can name one feature twice; the
    parser returns one entry per distinct feature, in first-appearance order,
    with ascending deduplicated lines -- the same grouping the writer applies,
    so a parse of what the writer produced returns exactly one entry per line.
    Were this to fail, a rerun could execute one scenario twice.
    """
    crm = _feature_path(CRM_FEATURE)
    sales = _feature_path(SALES_FEATURE)

    entries = rerun_report.parse_rerun_lines(
        [
            f"{paths.FILE_URI_SCHEME}{crm}:24",
            f"{paths.FILE_URI_SCHEME}{sales}:12",
            f"{paths.FILE_URI_SCHEME}{crm}:9:24",
        ],
        source="hand-edited",
    )

    assert [entry.path for entry in entries] == [crm, sales]
    assert entries[0].lines == (9, 24)


@pytest.mark.parametrize(
    "render",
    [
        lambda line: line,
        lambda line: f"{line}\n",
        lambda line: f"  {line}   \n",
        lambda line: f"\n\n{line}\n\n",
        lambda line: f"{line}\r\n",
        lambda line: f"\t{line}\t",
    ],
    ids=[
        "no-terminator",
        "lf",
        "surrounding-spaces",
        "blank-lines",
        "crlf",
        "tabs",
    ],
)
def test_the_parser_tolerates_incidental_whitespace_and_line_endings(
    render: Any,
) -> None:
    """One manifest, six spellings, one result.

    The writer emits LF and a single trailing newline, but the file it reads
    may have passed through a Windows editor or a hand edit.  Splitting with
    ``str.splitlines`` and stripping each line is what makes a CRLF file parse
    identically to an LF one.  Were this to fail, a manifest that looked
    correct would select nothing, and the rerun would silently pass.
    """
    crm = _feature_path(CRM_FEATURE)

    entries = rerun_report.parse_rerun_text(
        render(f"{paths.FILE_URI_SCHEME}{crm}:9:24"), source="variant"
    )

    assert entries == [rerun_report.RerunEntry(path=crm, lines=(9, 24))]


def test_entry_locations_and_rerun_locations_flatten_in_order() -> None:
    """The grouped file expands back into one location per scenario.

    ``--rerun`` hands these strings to the engine unchanged, so the ungrouped
    shape is derived from the grouped file rather than stored twice.  Were this
    to fail, either the count or the order of the retried scenarios would
    differ from the manifest's.
    """
    crm = _feature_path(CRM_FEATURE)
    sales = _feature_path(SALES_FEATURE)
    lines = [
        f"{paths.FILE_URI_SCHEME}{crm}:9:24",
        f"{paths.FILE_URI_SCHEME}{sales}:12",
    ]

    entry = rerun_report.RerunEntry(path=crm, lines=(9, 24))

    assert entry.locations == (f"{crm}:9", f"{crm}:24")
    assert rerun_report.rerun_locations(lines) == [
        f"{crm}:9",
        f"{crm}:24",
        f"{sales}:12",
    ]


@pytest.mark.parametrize(
    "lines",
    [
        [paths.FILE_URI_SCHEME],
        [f"{paths.FILE_URI_SCHEME}Crm.feature"],
        ["Crm.feature"],
        [f"{paths.FILE_URI_SCHEME}:9"],
        [":9"],
        ["::"],
        [9],
        [None],
        [b"Crm.feature:9"],
        [f"{paths.FILE_URI_SCHEME}Crm.feature:9", 9],
    ],
    ids=[
        "scheme-only",
        "no-line-number",
        "bare-path",
        "scheme-then-number",
        "empty-path",
        "separators-only",
        "integer-line",
        "none-line",
        "bytes-line",
        "good-line-then-integer",
    ],
)
def test_every_malformed_line_raises_rerun_manifest_error(
    lines: list[Any],
) -> None:
    """``RerunManifestError`` is the single failure channel.

    The AAP 0.4.1 exit table maps *a missing or malformed rerun manifest* to
    exit ``0`` with the problem reported on stderr, so the command-line surface
    has to *recognise* the condition rather than classify it -- and no
    traceback may reach the operator.  A bare ``ValueError``, ``OSError`` or
    ``UnicodeDecodeError`` escaping from here would be caught by nothing and
    would turn a diagnosable manifest problem into a crash.  Were this to fail,
    ``--rerun`` on a damaged file would exit non-zero with a traceback.
    """
    with pytest.raises(rerun_report.RerunManifestError) as raised:
        rerun_report.parse_rerun_lines(lines, source="malformed")

    assert not isinstance(raised.value, (ValueError, OSError))
    assert "malformed" in str(raised.value)


def _write_undecodable(root: Path) -> Path:
    """Write a manifest that is not valid UTF-8 and return its path.

    The first line is a valid entry, so the failure is genuinely a decoding
    one rather than an empty file rejected for some other reason.

    :param root: Directory to write into -- always a temporary one.
    :returns: The path of the undecodable file.
    """
    destination = root / "undecodable.txt"
    valid = f"{paths.FILE_URI_SCHEME}{CRM_FEATURE}:9{rerun_report.LINE_ENDING}"
    destination.write_bytes(valid.encode("utf-8") + b"\xff\xfe\n")
    return destination


@pytest.mark.parametrize(
    ("prepare", "expected_cause"),
    [
        (lambda root: root / "absent.txt", FileNotFoundError),
        (lambda root: root, paths.ArtifactPathError),
        (lambda root: _write_undecodable(root), UnicodeDecodeError),
    ],
    ids=["absent", "directory", "invalid-utf-8"],
)
def test_an_unreadable_manifest_raises_with_the_cause_chained(
    prepare: Any, expected_cause: type[BaseException], tmp_artifact_root: Path
) -> None:
    """An I/O or decoding fault arrives as the same typed error, cause intact.

    One error type covers every case on purpose; chaining the original keeps
    the diagnosis available for the log without letting the raw exception reach
    the caller.  Were this to fail, ``--rerun`` against a deleted or corrupted
    manifest would crash instead of reporting and exiting ``0``.

    The three causes are the three distinct ways the read can fail, and the
    middle one is a *refusal* rather than a fault: a directory where the
    manifest should be is refused by ``app/utils/paths.py``'s no-follow reader,
    which raises ``ArtifactPathError`` -- an ``OSError`` subclass -- for every
    entry that is not a regular file with a single name.  Pinning that type
    rather than ``IsADirectoryError`` is pinning *where* the refusal comes
    from: a plain ``open()`` would report the platform's errno, and this read
    never reaches one.
    """
    source = prepare(tmp_artifact_root)

    with pytest.raises(rerun_report.RerunManifestError) as raised:
        rerun_report.parse_rerun_file(source)

    assert isinstance(raised.value.__cause__, expected_cause)
    assert str(source) in str(raised.value)


@pytest.mark.parametrize(
    "text", ["", "\n", "   \n\n\t\n"], ids=["empty", "newline", "whitespace"]
)
def test_an_empty_manifest_parses_to_no_selection(text: str) -> None:
    """Zero bytes is a normal state, not an error.

    A run with no failures writes an empty file, and reading that back has to
    mean "nothing to retry".  Were this to fail, ``--rerun`` after a clean run
    would report a malformed manifest.
    """
    assert rerun_report.parse_rerun_text(text, source="empty") == []
    assert rerun_report.rerun_locations(text.splitlines()) == []


def test_parse_rerun_file_resolves_the_owned_default_location(
    tmp_artifact_root: Path,
) -> None:
    """Writer and reader agree on *where* the manifest lives.

    Both resolve ``app/utils/paths.py``'s ``rerun_txt_path``, which is the port
    of ``FailedTestRunner``'s ``features = "@target/rerun.txt"``; neither holds
    a path literal.  Were this to fail, ``--rerun`` would look for the manifest
    somewhere other than where the run wrote it.

    This is the **production read**, so it is also where the filesystem tier of
    the confinement runs: with ``source`` left to default, the parser knows the
    features directory hangs off the same ``base``, and it therefore returns
    only entries that name a regular, non-symlinked file inside it.  The
    feature file is created here for that reason -- the locations this test
    asserts on are ones ``test_run_service`` may append to the engine's argv
    without a check of its own, and an entry no file backs is dropped rather
    than handed on (``test_a_default_read_drops_an_entry_no_feature_file_backs``
    is the other half of that rule).
    """
    result_set = _run([(CRM_FEATURE, [(9, FAILED), (24, FAILED)])])
    written = rerun_report.write_rerun_txt(result_set, base=tmp_artifact_root)
    assert written == paths.rerun_txt_path(tmp_artifact_root)

    feature_file = paths.features_dir(tmp_artifact_root) / CRM_FEATURE
    feature_file.parent.mkdir(parents=True, exist_ok=True)
    feature_file.write_text(GHERKIN_STANDIN, encoding="utf-8")

    entries = rerun_report.parse_rerun_file(base=tmp_artifact_root)

    assert entries == [
        rerun_report.RerunEntry(path=_feature_path(CRM_FEATURE), lines=(9, 24))
    ]
    assert rerun_report.rerun_locations(base=tmp_artifact_root) == [
        f"{_feature_path(CRM_FEATURE)}:9",
        f"{_feature_path(CRM_FEATURE)}:24",
    ]


def test_a_default_read_drops_an_entry_no_feature_file_backs(
    tmp_artifact_root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A stale manifest entry is dropped with a diagnostic, not handed on.

    The other half of the confinement, and the reason the test above creates a
    real feature file rather than asking for the lexical tier alone: a
    ``path:line`` this parser returns is appended to the engine's argv, so an
    entry that no longer names a Gherkin file inside the features directory --
    a manifest left by a run before the feature was renamed, say -- must not
    reach it (CWE-22).

    Dropping is per entry rather than per file: the feature that does still
    exist keeps its locations, because discarding a whole manifest for one
    stale line would lose the real failures of every other feature in it.  The
    problem is reported on the module logger, which ``app/logging_config.py``
    routes to stderr -- the tolerated malformed-manifest case of the AAP 0.4.1
    exit table, status ``0`` with the problem named.
    """
    result_set = _run(
        [
            (CRM_FEATURE, [(9, FAILED)]),
            (SALES_FEATURE, [(12, FAILED)]),
        ]
    )
    rerun_report.write_rerun_txt(result_set, base=tmp_artifact_root)

    # Only the CRM feature exists on disk; the Sales entry is stale.
    feature_file = paths.features_dir(tmp_artifact_root) / CRM_FEATURE
    feature_file.parent.mkdir(parents=True, exist_ok=True)
    feature_file.write_text(GHERKIN_STANDIN, encoding="utf-8")

    with caplog.at_level("WARNING", logger=rerun_report.logger.name):
        entries = rerun_report.parse_rerun_file(base=tmp_artifact_root)

    assert entries == [
        rerun_report.RerunEntry(path=_feature_path(CRM_FEATURE), lines=(9,))
    ]
    assert rerun_report.rerun_locations(base=tmp_artifact_root) == [
        f"{_feature_path(CRM_FEATURE)}:9"
    ]
    warnings = [record.getMessage() for record in caplog.records]
    # The drop is reported by the entry's one-based position in the manifest,
    # and the manifest's own path text is not reproduced: an entry is
    # untrusted machine input and this record is written to stderr and kept
    # verbatim by a CI console, so the position is the provenance a diagnostic
    # carries (CWE-532, CWE-117).  Position 2 is the Sales entry, the second
    # of the two the manifest holds.
    assert any("Dropping entry 2" in message for message in warnings), warnings
    assert not any(SALES_FEATURE in message for message in warnings), warnings

    # The lexical tier alone still accepts it: the grammar is satisfied, and it
    # is the filesystem that refuses it.  Asserting both is what distinguishes
    # "the tier ran" from "the line was never well formed".
    assert rerun_report.parse_rerun_file(
        paths.rerun_txt_path(tmp_artifact_root), confine=False
    ) == [
        rerun_report.RerunEntry(path=_feature_path(CRM_FEATURE), lines=(9,)),
        rerun_report.RerunEntry(path=_feature_path(SALES_FEATURE), lines=(12,)),
    ]


@pytest.mark.parametrize(
    "line",
    [
        BEHAVE_RERUN_HEADER,
        "# a hand-written annotation",
        rerun_report.COMMENT_PREFIX,
        "   # indented comment",
    ],
    ids=["behave-header", "annotation", "bare-marker", "indented"],
)
def test_a_comment_line_is_refused_and_selects_nothing(line: str) -> None:
    """A comment is rejected through the one error channel, not skipped.

    The grammar has no comment.  This writer emits none, and the one thing that
    realistically produces one is behave's own rerun formatter, whose
    ``# -- RERUN:`` header means the *remaining* lines are in behave's
    ungrouped, unprefixed form -- so a parser that skipped the header would go
    on to read the rest of that file as this format and select a scenario set
    nobody asked for.  Rejecting the line refuses the whole document instead,
    which is why the valid entry below is also not returned: one error channel,
    reported on stderr, and the run still exits ``0`` per the AAP 0.4.1 exit
    table.

    The offending line is placed **second** so the position in the message is
    the line's own and not the constant ``1`` every single-line case would
    produce.  Were this to fail, behave's header would be handed to a runner as
    a path -- selecting nothing at best, and an unintended file at worst.
    """
    good = f"{paths.FILE_URI_SCHEME}{_feature_path(CRM_FEATURE)}:9"

    with pytest.raises(rerun_report.RerunManifestError) as raised:
        rerun_report.parse_rerun_lines([good, line], source=SOURCE_LABEL)

    _assert_names_the_source_and_the_position(raised.value, number=2)


@pytest.mark.parametrize(
    "line",
    [
        BEHAVE_RERUN_HEADER,
        "# a hand-written annotation",
        "   # indented comment",
        f"# {paths.FILE_URI_SCHEME}/etc/shadow:9",
    ],
    ids=["behave-header", "annotation", "indented", "commented-out-entry"],
)
def test_a_refused_comment_is_not_reproduced_in_the_diagnostic(
    line: str,
) -> None:
    """The refusal says *that* it was a comment, never what the comment said.

    A security property rather than a style one, and the reason it is asserted
    separately from the rejection above: this message is written to stderr and
    recorded verbatim in a Jenkins console log, and the manifest is untrusted
    machine input.  Echoing the refused bytes is how a tampered file forges a
    console record or exfiltrates its payload into one (CWE-117, CWE-532).  An
    operator locates the entry by the position the message *does* carry.

    Every case here carries data beyond the comment marker itself -- the
    marker is the module's own fixed vocabulary and naturally appears in the
    message -- so the absence asserted is the absence of attacker-controlled
    text.  Were this to fail, a manifest line could write anything it liked
    into a CI record.
    """
    with pytest.raises(rerun_report.RerunManifestError) as raised:
        rerun_report.parse_rerun_lines([line], source=SOURCE_LABEL)

    message = str(raised.value)
    payload = line.strip().removeprefix(rerun_report.COMMENT_PREFIX).strip()
    assert payload, "this case carries no data whose absence could be asserted"
    assert payload not in message
    assert line.strip() not in message


#: The path shapes the grammar refuses, each with the part of it that is
#: **data** -- the fragment a diagnostic must not reproduce.  The first five
#: are the shapes a hostile or foreign manifest actually takes; the rest walk
#: the remaining refusals of the lexical tier, so that every branch of the
#: accepted-entry rule is exercised from the parser's own entry point rather
#: than only through the validator.
#:
#: Composed from ``paths.FILE_URI_SCHEME`` rather than typed out, for the
#: reason :func:`test_this_module_names_no_feature_directory_prefix` states.
NON_CONTRACT_LINES: Final[tuple[tuple[str, str, str], ...]] = (
    ("no-scheme-absolute", "/etc/passwd:9", "/etc/passwd"),
    ("absolute", f"{paths.FILE_URI_SCHEME}/etc/passwd:9", "/etc/passwd"),
    ("traversal", f"{paths.FILE_URI_SCHEME}../../etc/passwd:9", "etc/passwd"),
    (
        "dot-slash",
        f"{paths.FILE_URI_SCHEME}.{paths.NORMALIZED_FEATURES_PREFIX[-1]}"
        f"{CRM_FEATURE}:9",
        CRM_FEATURE,
    ),
    ("windows", "C:\\Windows\\win.ini:9", "Windows"),
    (
        "hidden-component",
        f"{paths.FILE_URI_SCHEME}{_feature_path('.' + CRM_FEATURE)}:9",
        CRM_FEATURE,
    ),
    (
        "nested",
        f"{paths.FILE_URI_SCHEME}{_feature_path('deeper')}"
        f"{paths.NORMALIZED_FEATURES_PREFIX[-1]}{CRM_FEATURE}:9",
        "deeper",
    ),
    (
        "doubled-separator",
        f"{paths.FILE_URI_SCHEME}{paths.NORMALIZED_FEATURES_PREFIX}"
        f"{paths.NORMALIZED_FEATURES_PREFIX[-1]}{CRM_FEATURE}:9",
        CRM_FEATURE,
    ),
    (
        "padded-component",
        f"{paths.FILE_URI_SCHEME}{_feature_path(' ' + CRM_FEATURE)}:9",
        CRM_FEATURE,
    ),
    (
        "not-gherkin",
        f"{paths.FILE_URI_SCHEME}{_feature_path('secrets.txt')}:9",
        "secrets.txt",
    ),
    # A name that is nothing but the suffix is refused as a *hidden* component,
    # since the suffix begins with a dot -- the earlier of the two rules that
    # would each refuse it, and the one the message therefore names.
    (
        "suffix-only-name",
        f"{paths.FILE_URI_SCHEME}{_feature_path(rerun_report.FEATURE_SUFFIX)}:9",
        rerun_report.FEATURE_SUFFIX,
    ),
    (
        "backslash",
        f"{paths.FILE_URI_SCHEME}{_feature_path('C')}\\{CRM_FEATURE}:9",
        CRM_FEATURE,
    ),
    (
        "control-character",
        f"{paths.FILE_URI_SCHEME}{_feature_path('Bell\a')}:9",
        "Bell",
    ),
    (
        "line-terminator",
        f"{paths.FILE_URI_SCHEME}{_feature_path('Split\u2028' + CRM_FEATURE)}:9",
        "Split",
    ),
)


@pytest.mark.parametrize(
    ("line", "payload"),
    [(line, payload) for _, line, payload in NON_CONTRACT_LINES],
    ids=[case_id for case_id, _, _ in NON_CONTRACT_LINES],
)
def test_a_non_contract_path_is_refused_and_never_echoed(
    line: str, payload: str
) -> None:
    """A path outside the grammar is rejected, and nothing else is returned.

    The accepted entry is exactly the scheme, one Gherkin file directly inside
    the features directory, and one or more line numbers; every other shape is
    refused through ``RerunManifestError``.  That is not tidiness: ``--rerun``
    appends these locations to the engine's argv, so an entry naming an
    absolute path or a ``..`` component would direct execution at Gherkin
    outside the authoritative feature tree (CWE-22).  Refusal is also
    *whole-document*, which is why no entry list is returned to inspect -- and
    why the sibling assertions about a *stale but well-formed* entry, which is
    dropped rather than raised on, live in
    :func:`test_a_default_read_drops_an_entry_no_feature_file_backs`.

    The diagnostic is asserted at the same time, because both halves of it are
    load-bearing: it locates the entry by source and position, and it
    reproduces no part of the refused path.  The ``payload`` of each case is
    the fragment that is *data* -- the path, or the component that provoked the
    refusal -- so its absence is the absence of attacker-controlled text and
    not merely of a punctuation mark the message may legitimately name.

    Were this to fail, a damaged manifest could steer a rerun at a file the run
    never touched, or write its own text into a CI console record.
    """
    with pytest.raises(rerun_report.RerunManifestError) as raised:
        rerun_report.parse_rerun_lines([line], source=SOURCE_LABEL)

    _assert_names_the_source_and_the_position(raised.value, number=1)
    message = str(raised.value)
    assert payload not in message
    assert line not in message


def test_the_round_trip_selects_exactly_the_scenarios_that_failed(
    tmp_artifact_root: Path,
) -> None:
    """AAP 0.6's requirement, end to end and through a real file.

    The run deliberately mixes the cases that could break the correspondence: a
    feature with two failures, a feature with one, a feature with none, and
    passing scenarios interleaved with failing ones.  The expected selection is
    computed from the same specification the document is built from, so neither
    side of the assertion is derived from the code under test.  Were this to
    fail, a rerun would execute a different set of scenarios from the set that
    failed -- silently retrying what passed, or dropping a genuine failure.
    """
    spec: RunSpec = [
        (CRM_FEATURE, [(9, FAILED), (16, PASSED), (24, FAILED)]),
        (CONTACT_FEATURE, [(19, PASSED), (31, PASSED)]),
        (SALES_FEATURE, [(12, PASSED), (32, FAILED)]),
    ]
    result_set = _run(spec)

    written = rerun_report.write_rerun_txt(result_set, base=tmp_artifact_root)
    locations = rerun_report.rerun_locations(written)

    assert locations == _expected_locations(spec)
    assert len(locations) == 3
    assert not any(CONTACT_FEATURE in location for location in locations)


def test_the_round_trip_of_a_background_only_failure(
    tmp_artifact_root: Path,
) -> None:
    """The whole chain for a Background-only failure, against a JVM baseline.

    **The baseline, measured.** Cucumber-JVM 7.2.3 (``io.cucumber.core.cli.Main``
    with the ``rerun:`` and ``json:`` plugins) was run over a feature whose
    Background step fails and whose two following scenarios hold steps that
    would pass.  It wrote the manifest as :data:`JVM_BACKGROUND_ONLY_MANIFEST`
    -- the two **scenario** lines, grouped on one line for the feature, and the
    Background's own line absent -- while its JSON carried the Background
    element with the failed step (result keys
    :data:`JVM_FAILED_STEP_RESULT_KEYS`) and each scenario's own steps as
    ``skipped`` with result keys exactly :data:`JVM_SKIPPED_STEP_RESULT_KEYS`,
    no ``duration`` among them, and **no element carrying a status field at
    all** (:data:`JVM_ELEMENT_STATUS_KEY_ABSENT`).

    So the element *shape* is settled and this port already emits it: what the
    parity question was really about is every **derived** reading of that
    scenario, and this test asserts all four of them from one document --

    1. the events document as built: the Background's step ``failed``, the
       scenario's own steps ``skipped``;
    2. the JSON this port emits, against the shape above
       (``app/reporting/cucumber_json.py`` is imported **read-only**);
    3. the rolled-up scenario reading -- ``effective_status`` and
       ``effective_verdict`` on the element, and the summary counting that
       scenario as **failed** rather than as skipped;
    4. the manifest, which selects the scenario's line and never the
       Background's, and round-trips through the parser to the same location.

    Were this to fail, one Background failure would be graded differently
    depending on which artifact a reader opened -- the disagreement this chain
    exists to rule out -- or a background failure would be unretryable, or the
    manifest would name a Background's line, which is not an addressable test
    case.
    """
    blocked_line = 9
    passing_line = 16
    result_set = _result_set(
        _feature(
            CRM_FEATURE,
            [
                _failed_background_occurrence(),
                _scenario(blocked_line, statuses=(SKIPPED, SKIPPED)),
                _background(statuses=(PASSED,)),
                _scenario(passing_line, statuses=(PASSED,)),
            ],
        )
    )

    # 1. The document as the collector builds it: no element carries a status,
    #    and the failure is recorded where it happened.
    occurrence, blocked = result_set["features"][0]["elements"][:2]
    assert occurrence["steps"][0]["result"]["status"] == FAILED
    assert [step["result"]["status"] for step in blocked["steps"]] == [
        SKIPPED,
        SKIPPED,
    ]
    assert JVM_ELEMENT_STATUS_KEY_ABSENT not in blocked

    # 2. The JSON artifact, in the JVM's measured shape.
    emitted = cucumber_json.build_cucumber_json(result_set)
    emitted_background, emitted_blocked = emitted[0]["elements"][:2]
    assert set(emitted_background["steps"][0]["result"]) == (
        JVM_FAILED_STEP_RESULT_KEYS
    )
    assert emitted_background["steps"][0]["result"]["status"] == FAILED
    for step in emitted_blocked["steps"]:
        assert tuple(step["result"]) == JVM_SKIPPED_STEP_RESULT_KEYS
        assert step["result"]["status"] == SKIPPED
    for element in emitted[0]["elements"]:
        assert JVM_ELEMENT_STATUS_KEY_ABSENT not in element

    # 3. The rolled-up scenario reading, from the shared model.
    decorated = aggregation.decorate_feature(result_set["features"][0])
    rolled_up = decorated["elements"][1]
    assert rolled_up[aggregation.EFFECTIVE_STATUS_KEY] == FAILED
    assert rolled_up[aggregation.EFFECTIVE_VERDICT_KEY] == aggregation.VERDICT_FAILED
    assert rolled_up["status"] == SKIPPED, "the element's own reading is unchanged"
    summary = aggregation.build_summary([decorated])
    assert summary["scenarios"]["by_status"] == {FAILED: 1, PASSED: 1}

    # 4. The manifest, and the round trip back out of it.
    written = rerun_report.write_rerun_txt(result_set, base=tmp_artifact_root)
    text = written.read_text(encoding="utf-8")

    assert rerun_report.rerun_locations(written) == [
        f"{_feature_path(CRM_FEATURE)}{rerun_report.LINE_SEPARATOR}{blocked_line}"
    ]
    assert str(BACKGROUND_LINE) not in text
    assert str(passing_line) not in text
    # The grammar the JVM's own manifest demonstrates: one ``file:``-prefixed
    # line for the feature, carrying scenario lines and nothing else.
    assert JVM_BACKGROUND_ONLY_MANIFEST.startswith(paths.FILE_URI_SCHEME)
    assert text.startswith(paths.FILE_URI_SCHEME)
    assert text.count(rerun_report.LINE_ENDING) == 1
    _assert_manifest_shape(text.splitlines())


def test_the_round_trip_needs_no_file_at_all() -> None:
    """``parse_rerun_file(build_rerun_lines(...))`` is a supported call.

    Documented explicitly by the module under test, and the seam that lets the
    correspondence be checked without disk I/O.  Were this to fail, the
    in-memory half of the round trip would have to be asserted against the
    grammar twice, once per direction.
    """
    spec: RunSpec = [
        (SESSION_FEATURE, [(5, FAILED)]),
        (NOTES_FEATURE, [(7, FAILED), (9, FAILED)]),
    ]

    entries = rerun_report.parse_rerun_file(
        rerun_report.build_rerun_lines(_run(spec))
    )

    assert [
        location for entry in entries for location in entry.locations
    ] == _expected_locations(spec)


# =========================================================================== #
# Section 4 -- the ``--rerun`` couplings this writer underpins
# =========================================================================== #


def test_the_writer_applies_no_tag_filtering_of_its_own() -> None:
    """Every failing feature is emitted, tagged or not.

    ``--rerun`` clears the default ``@Smoke`` filter, because
    ``FailedTestRunner`` declares no tags and applying a default to explicit
    locations would silently skip failures from the five features that carry no
    feature-level tag.  The writer therefore filters nothing: it records what
    failed, and selection is the runner's business.  Were this to fail, a
    failure in an untagged feature would never be retried.
    """
    result_set = _result_set(
        *(
            _feature(filename, [_scenario(12, statuses=(FAILED,))], line=1)
            for filename in UNTAGGED_FEATURES
        )
    )

    lines = rerun_report.build_rerun_lines(result_set)

    assert len(lines) == len(UNTAGGED_FEATURES)
    assert [line.split(rerun_report.LINE_SEPARATOR)[1] for line in lines] == [
        _feature_path(filename) for filename in UNTAGGED_FEATURES
    ]
    _assert_manifest_shape(lines)


def test_a_scenario_the_tag_expression_excluded_is_never_resurrected() -> None:
    """A non-selected scenario stays out, even carrying a failed step.

    behave announces scenarios the tag expression excluded; the JVM never
    starts them.  They did not run, so they did not fail, and a rerun must not
    bring back work the operator filtered out.  Were this to fail, ``--rerun``
    -- which clears the tag filter -- would execute scenarios that the original
    run was explicitly told to skip.
    """
    result_set = _result_set(
        _feature(
            SALES_FEATURE,
            [
                _scenario(36, statuses=(FAILED,), selected=False),
                _scenario(12, statuses=(FAILED,)),
            ],
        )
    )

    (line,) = rerun_report.build_rerun_lines(result_set)

    assert _line_numbers_of(line) == [12]


def test_reading_the_manifest_leaves_it_untouched(
    tmp_artifact_root: Path,
) -> None:
    """``--rerun`` must never delete the file it reads.

    ``--clean`` is what empties ``target/``, and it is ignored under
    ``--rerun`` for exactly this reason; nothing in the reporting module
    removes a file.  Were this to fail, the manifest would be consumed by the
    first rerun and a second one would find nothing to do.
    """
    result_set = _run([(CRM_FEATURE, [(9, FAILED), (24, FAILED)])])
    written = rerun_report.write_rerun_txt(result_set, base=tmp_artifact_root)
    before = written.read_bytes()

    rerun_report.parse_rerun_file(written)
    rerun_report.rerun_locations(written)
    rerun_report.parse_rerun_file(base=tmp_artifact_root)

    assert written.is_file()
    assert written.read_bytes() == before


def test_locations_are_the_addressable_form_handed_to_the_engine() -> None:
    """One ``path:line`` per failing scenario, in manifest order.

    This is the surface ``app/cli.py`` passes through under ``--rerun``, so its
    count and its order are the rerun's execution plan.  Were this to fail, the
    engine would be given a grouped string it cannot resolve, or the scenarios
    would be re-run in an order the manifest does not describe.
    """
    spec: RunSpec = [
        (CRM_FEATURE, [(24, FAILED), (9, FAILED)]),
        (INVENTORY_FEATURE, [(11, FAILED)]),
    ]

    locations = rerun_report.rerun_locations(
        rerun_report.build_rerun_lines(_run(spec))
    )

    assert locations == _expected_locations(spec)
    for location in locations:
        path, separator, number = location.rpartition(
            rerun_report.LINE_SEPARATOR
        )
        assert separator and number.isdigit()
        assert not path.startswith(paths.FILE_URI_SCHEME)


# =========================================================================== #
# Section 5 -- the I/O wrapper
# =========================================================================== #


def test_write_rerun_txt_owns_no_path_and_creates_its_parent(
    tmp_artifact_root: Path,
) -> None:
    """The destination comes from ``app/utils/paths.py`` and nowhere else.

    ``target/`` does not exist in the fixture's root, so this also asserts that
    the writer creates its own parent directory: the first artifact of a run
    cannot assume the directory is there.  Were this to fail, a fresh checkout's
    first run would lose the manifest to a missing directory, or would write it
    where no consumer looks.
    """
    expected = paths.rerun_txt_path(tmp_artifact_root)
    assert not expected.parent.exists()
    result_set = _run([(CRM_FEATURE, [(9, FAILED)])])

    written = rerun_report.write_rerun_txt(result_set, base=tmp_artifact_root)

    assert written == expected
    assert written.is_file()
    assert expected.parent.is_dir()


def test_write_rerun_txt_honours_an_explicit_path_override(
    tmp_artifact_root: Path,
) -> None:
    """``path=`` wins over ``base=``, and its parent is created too.

    The override is the seam a test or a tool uses to write elsewhere without
    changing the working directory.  Were this to fail, the two arguments would
    interact and a caller could not tell which location had been written.
    """
    override = tmp_artifact_root / "elsewhere" / "manifest.txt"
    result_set = _run([(CRM_FEATURE, [(9, FAILED)])])

    written = rerun_report.write_rerun_txt(
        result_set, path=override, base=tmp_artifact_root
    )

    assert written == override
    assert override.is_file()
    assert not paths.rerun_txt_path(tmp_artifact_root).exists()


def test_the_written_bytes_carry_no_carriage_return(
    tmp_artifact_root: Path,
) -> None:
    """LF unconditionally, on every platform.

    The file is machine input to a runner, and the write pins both the encoding
    and the terminator explicitly so the Windows half of the pipeline's
    ``isUnix()`` branch cannot translate LF into CRLF.  Asserting on the raw
    bytes is the only way to see it: text-mode reading would hide exactly the
    translation being guarded against.  Were this to fail, a Windows run's
    manifest would carry a stray ``\\r`` inside every parsed line number.
    """
    result_set = _run(
        [
            (CRM_FEATURE, [(9, FAILED), (24, FAILED)]),
            (SALES_FEATURE, [(12, FAILED)]),
        ]
    )

    payload = rerun_report.write_rerun_txt(
        result_set, base=tmp_artifact_root
    ).read_bytes()

    assert b"\r" not in payload
    assert payload.endswith(rerun_report.LINE_ENDING.encode())
    assert payload.count(b"\n") == 2
    assert payload.decode("utf-8") == rerun_report.build_rerun_text(result_set)


def test_write_rerun_txt_overwrites_rather_than_appends(
    tmp_artifact_root: Path,
) -> None:
    """A second run's manifest replaces the first's completely.

    The manifest describes one run.  A writer that appended would hand the next
    rerun a union of two runs' failures, growing without bound and retrying
    scenarios that have since passed.  The long-then-short order is deliberate:
    truncation is what makes the residue of the longer file impossible.
    """
    first = _run(
        [
            (CRM_FEATURE, [(9, FAILED), (16, FAILED), (24, FAILED)]),
            (SALES_FEATURE, [(12, FAILED), (32, FAILED)]),
            (SESSION_FEATURE, [(5, FAILED)]),
        ]
    )
    second = _run([(NOTES_FEATURE, [(7, FAILED)])])

    written = rerun_report.write_rerun_txt(first, base=tmp_artifact_root)
    long_payload = written.read_bytes()
    rerun_report.write_rerun_txt(second, base=tmp_artifact_root)
    short_payload = written.read_bytes()

    assert len(short_payload) < len(long_payload)
    assert short_payload == rerun_report.build_rerun_text(second).encode()
    for filename in (CRM_FEATURE, SALES_FEATURE, SESSION_FEATURE):
        assert filename not in short_payload.decode("utf-8")
    assert rerun_report.rerun_locations(written) == [
        f"{_feature_path(NOTES_FEATURE)}:7"
    ]


def test_a_genuine_io_fault_propagates_from_the_writer(
    tmp_artifact_root: Path,
) -> None:
    """An unwritable destination is an ``OSError``, not a swallowed warning.

    This is the one failure the writer lets through: a real I/O fault is the
    command's writer-failure exit class, distinct from a test outcome, which
    never reaches this function as an exception.  Were this to fail, a run that
    could not write its manifest would report success and leave ``--rerun``
    reading a stale file.
    """
    directory = tmp_artifact_root / "occupied"
    directory.mkdir()
    result_set = _run([(CRM_FEATURE, [(9, FAILED)])])

    with pytest.raises(OSError):
        rerun_report.write_rerun_txt(result_set, path=directory)


# =========================================================================== #
# Section 6 -- the published surface and the single failure rule
# =========================================================================== #


def test_the_module_surface_is_addressable_and_complete() -> None:
    """Every name ``__all__`` promises exists and is importable.

    ``app/cli.py`` reaches the manifest only through this surface -- it
    re-implements neither direction of the grammar -- so a name that vanished
    from the module while staying in ``__all__`` would break the command-line
    import rather than any assertion in this module.

    The surface is grouped by what each name is for, because the module now
    publishes four rules rather than one: the grammar's tokens and its two
    directions, the failure vocabulary stated once so the writer and any
    consumer compare the same set, the two tiers of the confinement -- public
    because ``app/services/test_run_service.py`` holds single locations as well
    as whole manifests, and because a caller about to *read* a feature needs
    the verified object rather than a name to reopen -- and the bounds, which
    are published so a caller can state the same figures rather than guess
    them: a manifest is machine input an earlier run wrote, and a tampered one
    is bounded in bytes, lines, distinct features, path length and feature
    size rather than trusted.
    """
    grammar = {
        "COMMENT_PREFIX",
        "FEATURE_SUFFIX",
        "LINE_ENDING",
        "LINE_SEPARATOR",
        "MAX_LINE_NUMBER_DIGITS",
        "PATH_SEPARATOR",
        "RerunEntry",
        "RerunManifestError",
        "build_rerun_lines",
        "build_rerun_text",
        "iter_failed_scenarios",
        "parse_rerun_file",
        "parse_rerun_lines",
        "parse_rerun_text",
        "rerun_locations",
        "write_rerun_txt",
    }
    vocabulary = {
        "FAILED_STATUS",
        "FAILURE_STATUSES",
        "STATUS_SPELLINGS",
        "is_failure_status",
        "normalize_status",
    }
    confinement = {
        "FORBIDDEN_PATH_CHARACTERS",
        "VerifiedFeature",
        "list_verified_features",
        "read_verified_feature",
        "resolve_feature_path",
        "validate_feature_path",
        "verify_feature_identity",
    }
    bounds = {
        "MAX_FEATURE_BYTES",
        "MAX_FEATURE_PATH_CHARACTERS",
        "MAX_MANIFEST_BYTES",
        "MAX_MANIFEST_ENTRIES",
        "MAX_MANIFEST_LINES",
    }
    expected = grammar | vocabulary | confinement | bounds

    assert set(rerun_report.__all__) == expected
    assert len(rerun_report.__all__) == len(set(rerun_report.__all__))
    for name in sorted(expected):
        assert getattr(rerun_report, name) is not None

    assert rerun_report.LINE_ENDING == "\n"
    assert rerun_report.LINE_SEPARATOR == ":"
    assert rerun_report.FAILED_STATUS == "failed"
    assert rerun_report.FEATURE_SUFFIX == ".feature"
    assert issubclass(rerun_report.RerunManifestError, RuntimeError)

    # Every published bound is a usable positive count.  A bound that arrived
    # as ``None`` or ``0`` would refuse every manifest ever written, and a
    # ``bool`` -- an ``int`` subclass -- would refuse everything but a
    # single-byte one; what each bound *is* is asserted by behaviour in
    # section 7 rather than by repeating its figure here.
    for name in sorted(bounds):
        bound = getattr(rerun_report, name)
        assert isinstance(bound, int) and not isinstance(bound, bool), name
        assert bound > 0, name


def test_iter_failed_scenarios_pairs_each_failure_with_its_feature() -> None:
    """The rule has one implementation, and it yields document order.

    Every ordering property of the manifest falls out of this iterator, which
    is why it is public: the writer performs no sort of its own over features.
    The document below interleaves Backgrounds and mixes selected with
    excluded scenarios, so the pairing, the roll-up and the exclusion are all
    visible in one result.  Were this to fail, the grouping tests above could
    still pass while the manifest's feature order drifted from the run's.
    """
    result_set = _result_set(
        _feature(
            CRM_FEATURE,
            [
                _background(statuses=(PASSED,)),
                _scenario(9, statuses=(FAILED,)),
                _background(statuses=(FAILED,)),
                _scenario(16, statuses=(PASSED,)),
                _scenario(24, statuses=(FAILED,), selected=False),
            ],
        ),
        _feature(SALES_FEATURE, [_scenario(12, statuses=(PASSED,))]),
    )

    pairs = list(rerun_report.iter_failed_scenarios(result_set))

    assert [
        (feature["path"], element["line"]) for feature, element in pairs
    ] == [
        (_feature_path(CRM_FEATURE), 9),
        (_feature_path(CRM_FEATURE), 16),
    ]
    assert all(
        element["type"] == ELEMENT_TYPE_SCENARIO for _, element in pairs
    )


# =========================================================================== #
# Section 7 -- the write authority: what the bytes travel through to reach disk
#
# Section 5 asserts *where* the manifest goes; this section asserts *how* it
# gets there.  The writer no longer prepares the parent directory and then
# names the pathname a second time to a builtin ``open``: between those two
# steps a symbolic or hard link put in the manifest's place redirected the
# write, and the truncation the open performed destroyed the target before any
# check could refuse it (CWE-367/CWE-59).  Every write now goes through
# ``app.utils.paths.open_artifact_write``, which creates and verifies each
# owned directory component under a *held* directory descriptor, opens the
# final entry relative to that descriptor with ``O_NOFOLLOW``, and truncates
# only once the descriptor is known to hold a lone regular file.
#
# Each hostile case below asserts *two* things: that the write was refused,
# and that the file outside the artifact root is byte-for-byte as it was.  An
# exception raised after the outside inode had already been emptied would
# satisfy ``pytest.raises`` and still be exactly the defect.
#
# The manifest's own byte contract is unchanged and is asserted where it
# already was: ``test_written_file_equals_the_golden_manifest_byte_for_byte``
# compares what reaches disk with the committed baseline.
# =========================================================================== #

#: Whether this platform can create a symbolic link.  The redirection cases
#: need a real one and there is no honest way to fake it; a platform without
#: symbolic links cannot be attacked through one either, so skipping is the
#: truthful outcome rather than a gap.
SYMLINKS_AVAILABLE: Final[bool] = hasattr(os, "symlink")

#: Whether this platform can create a hard link.  A destination that *is* a
#: file elsewhere is refused for its link count, and that destination cannot be
#: prepared where :func:`os.link` is absent.
HARD_LINKS_AVAILABLE: Final[bool] = hasattr(os, "link")

#: Whether POSIX permission bits carry meaning here.  Windows expresses
#: permissions as ACLs, where the owner-only creation policy has nothing to
#: apply and nothing to assert.
MODES_ENFORCED: Final[bool] = hasattr(os, "fchmod")

#: Name of the file planted *outside* the artifact root for a hostile link to
#: point at.  The manifest names the scenarios that failed, so redirecting this
#: writer writes the run's failure record into someone else's file as well as
#: destroying that file's content.
OUTSIDE_NAME: Final[str] = "outside.txt"

#: Content of that file: distinctive enough that finding it anywhere else -- or
#: finding it gone, or emptied -- is unambiguous.
OUTSIDE_CONTENT: Final[bytes] = b"kept\n"


def _plant_outside_file(directory: Path) -> Path:
    """Create the file a hostile link points at, outside the artifact root.

    :param directory: A temporary directory that is **not** the artifact root
        -- pytest's ``tmp_path``, of which the root is a subdirectory.
    :returns: The path of the planted file.
    """
    outside = directory / OUTSIDE_NAME
    outside.write_bytes(OUTSIDE_CONTENT)
    return outside


def _permission_bits(path: Path) -> int:
    """The permission bits of ``path``, without its file type.

    :param path: Entry to inspect.
    :returns: ``st_mode`` masked to the twelve permission and special bits.
    """
    return path.stat().st_mode & 0o7777


def _assert_owner_only(path: Path) -> None:
    """Assert that nothing but the owner can reach ``path``.

    The group and other bits are asserted absent rather than the whole mode
    asserted equal to ``0o700``: a set-group-id build directory keeps that bit
    -- it grants the group nothing once the access bits are gone -- and the
    policy is about access, not about one exact integer.

    :param path: Entry to check.
    """
    bits = _permission_bits(path)
    assert bits & paths.ARTIFACT_MODE_MASK == 0, (
        f"{path} is reachable by the group or by others: {oct(bits)}"
    )
    assert bits & 0o700, f"{path} is not reachable by its owner: {oct(bits)}"


@pytest.mark.skipif(
    not MODES_ENFORCED,
    reason="this platform expresses permissions as ACLs, so the policy does not apply",
)
def test_the_written_manifest_and_the_directory_above_it_are_owner_only(
    tmp_artifact_root: Path,
) -> None:
    """The manifest is created ``0o600`` beneath an owner-only ``target/``.

    The file names the scenarios that failed, which is run evidence, and the
    ambient ``0o644`` under an ambient ``0o755`` the review measured hands it
    to every local account (CWE-732/CWE-359).  The directory is asserted as
    well as the file: a world-readable build-output directory discloses which
    artifacts a run produced even where their contents are tight.  Were this to
    fail, a shared build agent would publish one run's failures to the next
    tenant of the workspace.
    """
    result_set = _run([(CRM_FEATURE, [(9, FAILED), (24, FAILED)])])

    written = rerun_report.write_rerun_txt(result_set, base=tmp_artifact_root)

    assert _permission_bits(written) == paths.ARTIFACT_FILE_MODE
    _assert_owner_only(written)
    _assert_owner_only(paths.target_root(tmp_artifact_root))


@pytest.mark.skipif(
    not MODES_ENFORCED,
    reason="this platform expresses permissions as ACLs, so the policy does not apply",
)
def test_a_permissive_manifest_from_an_earlier_run_is_tightened_on_rewrite(
    tmp_artifact_root: Path,
) -> None:
    """``--no-clean`` over a permissive manifest does not keep its bits.

    The case a creation mode cannot reach: the mode argument applies only to a
    file the open *creates*, so a run rewriting the manifest an earlier run
    left behind would inherit whatever that run's umask produced.  The
    tightening is made through the descriptor the write holds and before the
    truncation, so the bits are gone before the new content exists.  Were this
    to fail, the ``--no-clean`` path would silently keep publishing the failure
    record of every run after the first.
    """
    written = rerun_report.write_rerun_txt(
        _run([(CRM_FEATURE, [(9, FAILED)])]), base=tmp_artifact_root
    )
    os.chmod(written, 0o644)
    os.chmod(paths.target_root(tmp_artifact_root), 0o755)
    second = _run([(NOTES_FEATURE, [(7, FAILED)])])

    rewritten = rerun_report.write_rerun_txt(second, base=tmp_artifact_root)

    assert rewritten == written
    assert _permission_bits(written) == paths.ARTIFACT_FILE_MODE
    _assert_owner_only(paths.target_root(tmp_artifact_root))
    assert written.read_bytes() == rerun_report.build_rerun_text(second).encode(
        "utf-8"
    )


@pytest.mark.skipif(
    not SYMLINKS_AVAILABLE,
    reason="this platform cannot create a symbolic link to be redirected through",
)
def test_a_symlinked_destination_is_refused_and_the_outside_file_survives(
    prepared_artifact_root: Path, tmp_path: Path
) -> None:
    """A link standing where the manifest goes is refused, target untouched.

    The deterministic case from the security review: with the manifest's name
    occupied by a symbolic link to a writable file outside the artifact root, a
    builtin ``open`` follows it and truncates that file before it returns a
    stream.  The refusal alone is not the property -- the outside file's bytes
    are asserted afterwards, because the finding is the overwrite and not the
    exception.  Were this to fail, a run would empty an arbitrary file the
    account can write and record its failures there.
    """
    outside = _plant_outside_file(tmp_path)
    destination = paths.rerun_txt_path(prepared_artifact_root)
    os.symlink(outside, destination)
    result_set = _run([(CRM_FEATURE, [(9, FAILED)])])

    with pytest.raises(paths.ArtifactPathError) as refusal:
        rerun_report.write_rerun_txt(result_set, base=prepared_artifact_root)

    # An OSError subclass, so the writer's documented contract and the AAP
    # 0.4.1 writer-failure exit class are unchanged by the refusal.
    assert isinstance(refusal.value, OSError)
    assert outside.read_bytes() == OUTSIDE_CONTENT
    assert destination.is_symlink()


@pytest.mark.skipif(
    not HARD_LINKS_AVAILABLE,
    reason="this platform cannot create a hard link, so that destination cannot exist",
)
def test_a_hard_linked_destination_is_refused_and_the_outside_file_survives(
    prepared_artifact_root: Path, tmp_path: Path
) -> None:
    """A destination that *is* a file elsewhere is refused for its link count.

    The case no symbolic-link check can see: nothing in the path is a link, the
    entry is an ordinary regular file, and writing it in place would overwrite
    the outside inode it shares.  A ``--no-clean`` run over a tree someone else
    prepared is exactly how the manifest's name comes to carry a second link.
    Were this to fail, the write would land in a file outside the artifact root
    with no link anywhere for a link check to find.
    """
    outside = _plant_outside_file(tmp_path)
    destination = paths.rerun_txt_path(prepared_artifact_root)
    os.link(outside, destination)
    result_set = _run([(CRM_FEATURE, [(9, FAILED)])])

    with pytest.raises(paths.ArtifactPathError) as refusal:
        rerun_report.write_rerun_txt(result_set, base=prepared_artifact_root)

    assert isinstance(refusal.value, OSError)
    assert outside.read_bytes() == OUTSIDE_CONTENT
    assert destination.read_bytes() == OUTSIDE_CONTENT


@pytest.mark.skipif(
    not SYMLINKS_AVAILABLE,
    reason="this platform cannot create a symbolic link to be redirected through",
)
def test_a_symlinked_build_output_directory_is_refused_with_nothing_written(
    tmp_artifact_root: Path, tmp_path: Path
) -> None:
    """A linked ``target/`` writes nothing into the directory it points at.

    The parent half of the same race: a component of the path, rather than the
    final entry, is the link.  A verification that releases its descriptor
    before the write cannot refuse this at all -- the manifest lands wherever
    the link points -- so the opener refuses the component as it descends, and
    the directory it pointed at is asserted still empty.  Were this to fail, a
    junction or link planted on the build-output directory would relocate the
    whole artifact set.
    """
    outside_directory = tmp_path / "outside-tree"
    outside_directory.mkdir()
    os.symlink(outside_directory, paths.target_root(tmp_artifact_root))
    result_set = _run([(CRM_FEATURE, [(9, FAILED)])])

    with pytest.raises(paths.ArtifactPathError) as refusal:
        rerun_report.write_rerun_txt(result_set, base=tmp_artifact_root)

    assert isinstance(refusal.value, OSError)
    assert list(outside_directory.iterdir()) == []


@pytest.mark.skipif(
    not HARD_LINKS_AVAILABLE,
    reason="this platform cannot create a hard link, so that destination cannot exist",
)
def test_a_refused_rewrite_leaves_the_previous_manifest_whole(
    tmp_artifact_root: Path, tmp_path: Path
) -> None:
    """Nothing is emptied before the destination has been established.

    The ordering property behind the whole route: ``open(..., "w")`` truncates
    as part of the open, so by the time any check could refuse something the
    previous manifest is already gone.  The opener leaves ``O_TRUNC`` out,
    verifies the descriptor it obtained and truncates last, so a refused write
    costs the run nothing -- and ``--rerun`` still reads the manifest the last
    successful run published.  Were this to fail, a refusal would replace a
    usable failure record with an empty file.
    """
    first = _run([(CRM_FEATURE, [(9, FAILED), (24, FAILED)])])
    written = rerun_report.write_rerun_txt(first, base=tmp_artifact_root)
    published = written.read_bytes()
    # A second link to the manifest, from outside the root: the entry stays an
    # ordinary regular file, so only its link count reveals that writing it
    # would also write somewhere else.
    outside = tmp_path / OUTSIDE_NAME
    os.link(written, outside)

    with pytest.raises(paths.ArtifactPathError):
        rerun_report.write_rerun_txt(
            _run([(NOTES_FEATURE, [(7, FAILED)])]), base=tmp_artifact_root
        )

    assert written.read_bytes() == published
    assert rerun_report.parse_rerun_text(
        published.decode("utf-8"), source="published"
    ) == [rerun_report.RerunEntry(path=_feature_path(CRM_FEATURE), lines=(9, 24))]
    assert outside.read_bytes() == published


def test_the_writer_reaches_disk_only_through_the_path_authority(
    tmp_artifact_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One write route, named once, carrying the manifest's byte contract.

    Asserted two ways, because each catches what the other cannot.  The source
    of the function is read to show that the builtin opener, and the
    check-then-open pair it belonged to, are simply absent -- a hardened writer
    API with no consumer protects nothing.  The call is then intercepted to
    show that the destination handed to the authority is the one
    ``paths.rerun_txt_path`` resolved, with ``utf-8`` and ``LINE_ENDING``
    passed explicitly rather than left to the opener's defaults: the manifest's
    bytes are machine input to a second runner and must not start depending on
    a default changing elsewhere.  Were this to fail, the write would have
    drifted back onto a pathname the opener never verified.
    """
    body = inspect.getsource(rerun_report.write_rerun_txt).replace(
        rerun_report.write_rerun_txt.__doc__ or "", ""
    )
    assert "open_artifact_write(" in body
    assert "open(" not in body
    assert "ensure_parent" not in body

    calls: list[tuple[Any, dict[str, Any]]] = []

    def recording(destination: Any, **options: Any) -> Any:
        calls.append((destination, options))
        return paths.open_artifact_write(destination, **options)

    monkeypatch.setattr(
        "app.reporting.rerun_report.open_artifact_write", recording
    )
    result_set = _run([(CRM_FEATURE, [(9, FAILED)])])

    written = rerun_report.write_rerun_txt(result_set, base=tmp_artifact_root)

    assert calls == [
        (
            paths.rerun_txt_path(tmp_artifact_root),
            {"encoding": "utf-8", "newline": rerun_report.LINE_ENDING},
        )
    ]
    assert written.read_bytes() == rerun_report.build_rerun_text(
        result_set
    ).encode("utf-8")

# Section 8 -- the bounded verified read, and the verified feature resolution
#
# Everything above reads the manifest as *text*.  This section reads it as a
# **file**, which is a different problem: the manifest sits at a well-known
# location inside a CI workspace, anything with local write access can put
# something else there, and whatever it holds is what a second run executes
# (``FailedTestRunner.java:11``).  So the read is no-follow, single-link,
# regular-file-only and bounded, and every entry it returns has been opened and
# checked as an object rather than resolved as a name.
#
# The two hazards these tests exist for, both of them measurable only through
# the filesystem:
#
# * a *link* -- at the manifest's own location, at the features root, or at an
#   entry -- lets contents from outside the workspace choose the scenarios that
#   execute (CWE-59, CWE-22);
# * a *swap between the check and the use* lets an approved file be replaced by
#   another before the engine opens it (CWE-367), which is why the
#   verified-read surface hands back the content and the identity it read
#   rather than a path to reopen.
#
# The bounds are asserted from both sides -- one input at the bound is
# accepted, one input past it is refused -- because a bound asserted only from
# the refusing side would still pass if it had been tightened to zero.
# =========================================================================== #

#: Whether this platform can create a named pipe.  POSIX-only; the AAP requires
#: Windows as well (AAP 0.8), and a case that cannot be set up there is skipped
#: rather than failed.
HAS_NAMED_PIPES: Final[bool] = hasattr(os, "mkfifo")

#: Whether this platform can create a hard link.
HAS_HARD_LINKS: Final[bool] = hasattr(os, "link")

#: Whether a wall-clock deadline can be imposed on a call in this process.
#: ``SIGALRM`` is POSIX-only, which is the same platform set as the named pipe
#: the deadline exists to guard.
HAS_REFUSAL_DEADLINE: Final[bool] = hasattr(signal, "SIGALRM") and hasattr(
    signal, "setitimer"
)

#: Seconds a refusal is allowed to take.  Generous by three orders of
#: magnitude -- every refusal here is one ``open`` and one ``fstat`` -- because
#: the number is not a performance assertion: it is the difference between a
#: regression that *fails* and one that hangs the suite until the CI job is
#: killed, which is what opening a named pipe without ``O_NONBLOCK`` does.
REFUSAL_DEADLINE_SECONDS: Final[float] = 15.0

#: Body of the file a hostile manifest, or a linked features root, points at.
#: Distinct from :data:`GHERKIN_STANDIN` so that a test can assert this text
#: never reaches a caller.
OUTSIDE_GHERKIN: Final[str] = "Feature: scenarios this run never selected\n"


@contextmanager
def _refusal_deadline(seconds: float = REFUSAL_DEADLINE_SECONDS) -> Iterator[None]:
    """Fail the call inside rather than let it block for ever.

    Used for the named-pipe cases.  Opening a FIFO for reading blocks until a
    writer appears, so a reader that lost its ``O_NONBLOCK`` would not produce
    a wrong answer -- it would produce *no* answer, and a test asserting only
    the exception type would hang instead of failing.  The deadline converts
    that into a ``TimeoutError`` the test reports.

    The previous handler and any timer already running are restored, so nothing
    leaks into the next test.

    Where the platform has no ``SIGALRM`` -- Windows, which the AAP also
    requires (AAP 0.8) -- the body runs unguarded, and every case whose *hazard*
    is blocking is skipped there by :data:`HAS_REFUSAL_DEADLINE` instead.  That
    keeps this a guard rather than a second skip condition each caller has to
    repeat.

    :param seconds: The deadline.
    :yields: Nothing; the guarded call runs in the body.
    :raises TimeoutError: If the body has not returned within ``seconds``.
    """
    if not HAS_REFUSAL_DEADLINE:
        yield
        return

    def _expire(signal_number: int, frame: Any) -> None:
        raise TimeoutError(
            f"the call did not return within {seconds}s, so it blocked rather "
            "than refusing its input"
        )

    previous_handler = signal.signal(signal.SIGALRM, _expire)
    try:
        signal.setitimer(signal.ITIMER_REAL, seconds)
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, previous_handler)


def _symlink_or_skip(source: Path, destination: Path) -> None:
    """Create a symbolic link, or skip the test where that is not permitted.

    Windows can create one only with a privilege an unelevated agent does not
    hold, and the AAP requires the suite to run there (AAP 0.8).  A skip is the
    honest outcome: the case cannot be *set up*, which is different from the
    behaviour being absent, and the production code's refusal of a link is
    asserted on every platform that can plant one.

    :param source: What the link points at.
    :param destination: Where the link is created.
    """
    try:
        os.symlink(source, destination)
    except (AttributeError, NotImplementedError, OSError) as error:
        pytest.skip(f"this platform cannot create a symbolic link: {error!r}")


def _features_root(root: Path) -> Path:
    """Create and return the features directory beneath ``root``.

    The location comes from ``app/utils/paths.py``, never from a literal, for
    the reason :func:`test_this_module_names_no_feature_directory_prefix`
    states.

    :param root: A temporary checkout root.
    :returns: The existing features directory.
    """
    directory = paths.features_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _write_feature(
    root: Path, filename: str, body: str = GHERKIN_STANDIN
) -> Path:
    """Write one stand-in feature file into ``root``'s features directory.

    :param root: A temporary checkout root.
    :param filename: The feature file's name.
    :param body: Its contents.
    :returns: The path written.
    """
    location = _features_root(root) / filename
    location.write_text(body, encoding="utf-8")
    return location


def _manifest_path(root: Path) -> Path:
    """Return the manifest's own location beneath ``root``, parent created.

    The location and the parent both come from ``app/utils/paths.py``: the
    accessor the writer and the reader share, and the port's own
    parent-creating helper, so the fixture cannot drift from the production
    layout.

    :param root: A temporary checkout root.
    :returns: The manifest's location; its parent directory exists.
    """
    manifest = paths.rerun_txt_path(root)
    paths.ensure_parent(manifest)
    return manifest


def _entry_line(filename: str, line: int) -> str:
    """Return one well-formed manifest line for ``filename``.

    :param filename: A feature file's name.
    :param line: The failing scenario's line number.
    :returns: The line, terminator included.
    """
    return (
        f"{paths.FILE_URI_SCHEME}{_feature_path(filename)}"
        f"{rerun_report.LINE_SEPARATOR}{line}{rerun_report.LINE_ENDING}"
    )


def test_a_linked_manifest_is_refused_and_its_target_selects_nothing(
    tmp_artifact_root: Path, tmp_path: Path
) -> None:
    """A symbolic link at the manifest's location is refused, not followed.

    The manifest is machine input at a known path inside a CI workspace, so a
    link planted there is how contents from outside the workspace choose the
    scenarios a rerun executes (CWE-59).  The link here points at a file whose
    entry is *entirely well formed* and whose feature really exists in the
    tree, so nothing about the grammar or the confinement would have refused
    it: the read is what refuses, before the first byte reaches the parser.

    Were this to fail, ``--rerun`` would execute a scenario set chosen by
    whoever could write one file next to the workspace.
    """
    outside = tmp_path / "outside-manifest.txt"
    outside.write_text(_entry_line(CONTACT_FEATURE, 19), encoding="utf-8")
    _write_feature(tmp_artifact_root, CONTACT_FEATURE)
    manifest = _manifest_path(tmp_artifact_root)
    _symlink_or_skip(outside, manifest)

    with pytest.raises(rerun_report.RerunManifestError) as raised:
        rerun_report.parse_rerun_file(base=tmp_artifact_root)

    # The refusal comes from the path authority's no-follow reader, and the
    # cause is chained so a log keeps the diagnosis.
    assert isinstance(raised.value.__cause__, paths.ArtifactPathError)
    message = str(raised.value)
    assert str(manifest) in message
    # Nothing of what the link pointed at is disclosed: not its contents, and
    # not the feature it would have selected.
    assert CONTACT_FEATURE not in message
    with pytest.raises(rerun_report.RerunManifestError):
        rerun_report.rerun_locations(base=tmp_artifact_root)
    # And the link's target is left exactly as it was -- a read, refused, is
    # not a write.
    assert outside.read_text(encoding="utf-8") == _entry_line(
        CONTACT_FEATURE, 19
    )


@pytest.mark.skipif(
    not (HAS_NAMED_PIPES and HAS_REFUSAL_DEADLINE),
    reason="this platform has no named pipes, or no deadline to guard them with",
)
def test_a_named_pipe_manifest_is_refused_rather_than_blocking(
    tmp_artifact_root: Path,
) -> None:
    """A FIFO at the manifest's location returns a refusal, not a hang.

    The denial-of-service half of the same hazard: a named pipe opened for
    reading blocks until a writer appears, so a manifest replaced by one would
    stall the command indefinitely -- before any check could refuse it and with
    no diagnostic at all (CWE-400).  The path authority opens with
    ``O_NONBLOCK`` for exactly this, and the descriptor's ``fstat`` then refuses
    the entry as non-regular.

    The deadline is what makes this a *test* rather than a hazard of its own:
    a regression fails inside :func:`_refusal_deadline` instead of hanging the
    suite until CI kills the job.
    """
    manifest = _manifest_path(tmp_artifact_root)
    os.mkfifo(manifest)

    with _refusal_deadline():
        with pytest.raises(rerun_report.RerunManifestError) as raised:
            rerun_report.parse_rerun_file(base=tmp_artifact_root)

    assert isinstance(raised.value.__cause__, paths.ArtifactPathError)
    assert str(manifest) in str(raised.value)


def test_a_manifest_one_byte_over_the_byte_bound_is_refused(
    tmp_artifact_root: Path,
) -> None:
    """``MAX_MANIFEST_BYTES`` is enforced before the grammar is consulted.

    A tampered or foreign file at the manifest's location can be arbitrarily
    large, and reading it whole to discover that is the defect: at most the
    bound plus one byte is taken in, and one byte past the bound is a refusal
    (CWE-400).  Asserted from both sides, because a bound that had been
    tightened to nothing would still refuse the over-sized file.

    The padding is whitespace, which the grammar tolerates, so the refusal can
    only be the byte bound -- and the entry that precedes it is backed by a
    real feature file, so the accepted case returns a genuine selection rather
    than an empty list.
    """
    _write_feature(tmp_artifact_root, CRM_FEATURE)
    entry = _entry_line(CRM_FEATURE, 9).encode("utf-8")
    padding = rerun_report.MAX_MANIFEST_BYTES - len(entry) - 1
    at_bound = entry + b" " * padding + rerun_report.LINE_ENDING.encode("utf-8")
    assert len(at_bound) == rerun_report.MAX_MANIFEST_BYTES
    manifest = _manifest_path(tmp_artifact_root)

    manifest.write_bytes(at_bound)
    assert rerun_report.parse_rerun_file(base=tmp_artifact_root) == [
        rerun_report.RerunEntry(path=_feature_path(CRM_FEATURE), lines=(9,))
    ]

    manifest.write_bytes(at_bound + b" ")
    with pytest.raises(rerun_report.RerunManifestError) as raised:
        rerun_report.parse_rerun_file(base=tmp_artifact_root)

    message = str(raised.value)
    assert str(rerun_report.MAX_MANIFEST_BYTES) in message
    assert str(manifest) in message


def test_a_manifest_with_more_lines_than_the_bound_is_refused(
    tmp_artifact_root: Path,
) -> None:
    """``MAX_MANIFEST_LINES`` bounds the work a tampered file can demand.

    The byte bound alone would still admit thousands of minimal entries, each
    costing a validation pass and an entry object, so the line count is stated
    as a number of its own rather than left to follow from the bytes.  The file
    built here stays *well under* the byte bound, which is what makes the line
    bound the thing being measured; every line is the same valid entry, so the
    accepted case merges to exactly one selection and the refused case differs
    from it by one line and nothing else.
    """
    _write_feature(tmp_artifact_root, CRM_FEATURE)
    entry = _entry_line(CRM_FEATURE, 9)
    at_bound = entry * rerun_report.MAX_MANIFEST_LINES
    assert len(at_bound.encode("utf-8")) < rerun_report.MAX_MANIFEST_BYTES
    manifest = _manifest_path(tmp_artifact_root)

    manifest.write_text(at_bound, encoding="utf-8")
    assert rerun_report.parse_rerun_file(base=tmp_artifact_root) == [
        rerun_report.RerunEntry(path=_feature_path(CRM_FEATURE), lines=(9,))
    ]

    manifest.write_text(at_bound + entry, encoding="utf-8")
    with pytest.raises(rerun_report.RerunManifestError) as raised:
        rerun_report.parse_rerun_file(base=tmp_artifact_root)

    message = str(raised.value)
    assert str(rerun_report.MAX_MANIFEST_LINES) in message
    assert str(manifest) in message


def test_a_manifest_naming_more_features_than_the_bound_is_refused(
    tmp_artifact_root: Path,
) -> None:
    """``MAX_MANIFEST_ENTRIES`` bounds the fan-out of a rerun.

    One entry per distinct feature file, and the lexical tier confines every
    entry to one flat directory, so this is the number of feature files a
    manifest may direct a rerun at: this repository has ten (AAP 0.4.1), and a
    file naming more than the bound describes a tree that does not exist.

    The accepted case is read with the filesystem tier switched off, because a
    bound's worth of distinct names cannot all be backed by real files without
    the test writing a feature tree this repository does not have; the bound is
    a property of the *grammar* pass, which runs before any confinement, and
    the refused case goes through the production read to show that it is
    reached there too.
    """
    names = [
        f"Fanout{index}{rerun_report.FEATURE_SUFFIX}"
        for index in range(rerun_report.MAX_MANIFEST_ENTRIES + 1)
    ]
    at_bound = "".join(_entry_line(name, 9) for name in names[:-1])
    assert len(at_bound.encode("utf-8")) < rerun_report.MAX_MANIFEST_BYTES
    manifest = _manifest_path(tmp_artifact_root)

    manifest.write_text(at_bound, encoding="utf-8")
    accepted = rerun_report.parse_rerun_file(manifest, confine=False)
    assert len(accepted) == rerun_report.MAX_MANIFEST_ENTRIES
    assert [entry.path for entry in accepted] == [
        _feature_path(name) for name in names[:-1]
    ]

    manifest.write_text(at_bound + _entry_line(names[-1], 9), encoding="utf-8")
    with pytest.raises(rerun_report.RerunManifestError) as raised:
        rerun_report.parse_rerun_file(base=tmp_artifact_root)

    message = str(raised.value)
    assert str(rerun_report.MAX_MANIFEST_ENTRIES) in message
    assert str(manifest) in message


def test_a_feature_path_longer_than_the_bound_is_refused(
    tmp_artifact_root: Path,
) -> None:
    """``MAX_FEATURE_PATH_CHARACTERS`` bounds one entry's path.

    An entry can satisfy every other rule of the grammar and still be megabytes
    long, and the cost of that is paid twice: once by the validator walking it
    and once by the diagnostic that would otherwise interpolate it (CWE-400,
    CWE-532).  With the bound in place every accepted path -- and therefore
    every message that mentions one -- is bounded by construction.

    The accepted case is asserted in memory rather than through a file, because
    a name at the bound exceeds what any filesystem this port runs on will
    accept as a filename: the bound is deliberately above ``NAME_MAX``, so a
    path at it can be *parsed* but could never name a file, which is the whole
    reason anything longer is refused rather than looked for.
    """
    filler = "x" * (
        rerun_report.MAX_FEATURE_PATH_CHARACTERS
        - len(paths.NORMALIZED_FEATURES_PREFIX)
        - len(rerun_report.FEATURE_SUFFIX)
    )
    at_bound = _feature_path(f"{filler}{rerun_report.FEATURE_SUFFIX}")
    assert len(at_bound) == rerun_report.MAX_FEATURE_PATH_CHARACTERS

    assert rerun_report.parse_rerun_lines(
        [f"{paths.FILE_URI_SCHEME}{at_bound}{rerun_report.LINE_SEPARATOR}9"],
        source=SOURCE_LABEL,
    ) == [rerun_report.RerunEntry(path=at_bound, lines=(9,))]

    over_bound = _feature_path(f"x{filler}{rerun_report.FEATURE_SUFFIX}")
    assert len(over_bound) == rerun_report.MAX_FEATURE_PATH_CHARACTERS + 1
    manifest = _manifest_path(tmp_artifact_root)
    manifest.write_text(
        f"{paths.FILE_URI_SCHEME}{over_bound}"
        f"{rerun_report.LINE_SEPARATOR}9{rerun_report.LINE_ENDING}",
        encoding="utf-8",
    )

    with pytest.raises(rerun_report.RerunManifestError) as raised:
        rerun_report.parse_rerun_file(base=tmp_artifact_root)

    message = str(raised.value)
    assert str(rerun_report.MAX_FEATURE_PATH_CHARACTERS) in message
    assert f"line {1}" in message
    # The over-long path is not reproduced -- which is the point of bounding it.
    assert over_bound not in message
    assert filler not in message


def test_read_verified_feature_returns_the_object_it_checked(
    tmp_artifact_root: Path,
) -> None:
    """The verified read hands back content and identity, not a name.

    This is the whole point of the surface: a caller about to parse a feature
    -- ``app/services/test_run_service.py``, which expands outlines and
    evaluates tags -- receives the bytes that were read **from the descriptor
    that was checked**, so its decision is a fact about the approved object
    rather than about whatever the name resolved to a moment later (CWE-367).
    The path it also carries is the spelling every artifact uses, so no caller
    rebuilds it.

    Were this to fail, a caller would be back to reopening a name, which is the
    check-then-reopen sequence this tier exists to remove.
    """
    body = "Feature: the only content that may reach a caller\n"
    location = _write_feature(tmp_artifact_root, CRM_FEATURE, body=body)

    feature = rerun_report.read_verified_feature(
        _feature_path(CRM_FEATURE), base=tmp_artifact_root
    )

    assert feature is not None
    assert feature.text == body
    assert feature.path == _feature_path(CRM_FEATURE)
    assert feature.location == location
    # The identity is more than device-and-inode: an inode number alone is
    # recycled by the filesystem, which is the swap
    # test_verify_feature_identity_refuses_a_replaced_entry demonstrates.
    assert len(feature.identity) > 2
    assert all(isinstance(member, int) for member in feature.identity)
    assert rerun_report.verify_feature_identity(
        feature.path, feature.identity, base=tmp_artifact_root
    )


def test_an_unvalidatable_path_is_refused_before_any_filesystem_call(
    tmp_artifact_root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The lexical tier runs first, and a refusal there is reported, not raised.

    Every entry point of the filesystem tier applies
    ``validate_feature_path`` before it touches the filesystem, and turns its
    typed error into the tolerated outcome the AAP 0.4.1 exit table describes:
    ``None`` or ``False``, with the reason on the module logger that
    ``app/logging_config.py`` routes to stderr.  Were this to fail, a caller
    holding one hostile location would get an exception where the exit table
    promises a report -- or, worse, a filesystem call made on an unvalidated
    name.
    """
    _write_feature(tmp_artifact_root, CRM_FEATURE)
    outside = f"..{paths.NORMALIZED_FEATURES_PREFIX[-1]}{CRM_FEATURE}"

    with caplog.at_level(logging.WARNING, logger=rerun_report.logger.name):
        assert (
            rerun_report.read_verified_feature(outside, base=tmp_artifact_root)
            is None
        )
        assert (
            rerun_report.resolve_feature_path(outside, base=tmp_artifact_root)
            is None
        )
        assert not rerun_report.verify_feature_identity(
            outside, (0,), base=tmp_artifact_root
        )
        assert (
            rerun_report.read_verified_feature(None, base=tmp_artifact_root)
            is None
        )

    assert len(caplog.records) == 4
    for record in caplog.records:
        assert record.levelno == logging.WARNING


def test_a_linked_features_root_is_refused_by_every_entry_point(
    tmp_artifact_root: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The trust anchor is opened no-follow, so a link in its place is refused.

    The defect a pathname check cannot fix: resolving the features directory
    *follows* a link standing where it should be, and the linked-to directory
    then becomes the anchor every entry is judged inside -- so an entry naming
    one Gherkin file in the authoritative tree resolves to someone else's
    scenarios and passes every test of the grammar (CWE-22).  All four entry
    points refuse it, because all four reach the same no-follow open of the
    root, and a default manifest read therefore drops the entry rather than
    handing its locations to the engine.

    Were this to fail, one symbolic link would redirect an entire rerun.
    """
    elsewhere = tmp_path / "somebody-elses-features"
    elsewhere.mkdir()
    (elsewhere / CRM_FEATURE).write_text(OUTSIDE_GHERKIN, encoding="utf-8")
    _symlink_or_skip(elsewhere, paths.features_dir(tmp_artifact_root))
    entry = _feature_path(CRM_FEATURE)

    with caplog.at_level(logging.WARNING, logger=rerun_report.logger.name):
        assert (
            rerun_report.read_verified_feature(entry, base=tmp_artifact_root)
            is None
        )
        assert (
            rerun_report.resolve_feature_path(entry, base=tmp_artifact_root)
            is None
        )
        assert not rerun_report.verify_feature_identity(
            entry, (0,), base=tmp_artifact_root
        )
        listed, problems = rerun_report.list_verified_features(
            tmp_artifact_root
        )

    assert listed == []
    assert len(problems) == 1
    assert paths.FEATURES_DIR_NAME in problems[0]
    # The outside scenarios never reach a caller through any of them.
    for record in caplog.records:
        assert OUTSIDE_GHERKIN.strip() not in record.getMessage()

    manifest = _manifest_path(tmp_artifact_root)
    manifest.write_text(_entry_line(CRM_FEATURE, 9), encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger=rerun_report.logger.name):
        assert rerun_report.parse_rerun_file(base=tmp_artifact_root) == []
        assert rerun_report.rerun_locations(base=tmp_artifact_root) == []
    # The line was never malformed -- the lexical tier still accepts it -- so
    # what refused it is the filesystem tier and nothing else.
    assert rerun_report.parse_rerun_file(manifest, confine=False) == [
        rerun_report.RerunEntry(path=entry, lines=(9,))
    ]


def test_a_linked_entry_is_refused_by_every_entry_point(
    tmp_artifact_root: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A link *inside* the features directory is refused on the descriptor.

    The same hazard one level down, and the one a "is it under the features
    directory?" check misses entirely: the entry is inside the tree, and what
    it points at is not.  The entry is opened ``O_NOFOLLOW`` relative to the
    verified root, so the link is refused rather than followed, and the
    manifest entry naming it is dropped with a diagnostic instead of being
    handed to the engine.
    """
    outside = tmp_path / "outside.feature"
    outside.write_text(OUTSIDE_GHERKIN, encoding="utf-8")
    _features_root(tmp_artifact_root)
    _symlink_or_skip(outside, paths.features_dir(tmp_artifact_root) / CRM_FEATURE)
    entry = _feature_path(CRM_FEATURE)

    with caplog.at_level(logging.WARNING, logger=rerun_report.logger.name):
        assert (
            rerun_report.read_verified_feature(entry, base=tmp_artifact_root)
            is None
        )
        assert (
            rerun_report.resolve_feature_path(entry, base=tmp_artifact_root)
            is None
        )
        listed, problems = rerun_report.list_verified_features(
            tmp_artifact_root
        )

    assert listed == []
    assert any(CRM_FEATURE in problem for problem in problems), problems
    for record in caplog.records:
        assert OUTSIDE_GHERKIN.strip() not in record.getMessage()

    manifest = _manifest_path(tmp_artifact_root)
    manifest.write_text(_entry_line(CRM_FEATURE, 9), encoding="utf-8")
    assert rerun_report.parse_rerun_file(base=tmp_artifact_root) == []


@pytest.mark.skipif(
    not HAS_HARD_LINKS, reason="this platform cannot create a hard link"
)
def test_a_hard_linked_entry_is_refused(
    tmp_artifact_root: Path, tmp_path: Path
) -> None:
    """An entry that is also a file elsewhere is refused by its link count.

    The case no symbolic-link check can see: a hard link has no link object to
    examine -- the entry *is* the outside file, under a second name -- so the
    only evidence is ``st_nlink``, read from the descriptor that was opened.
    The port's own ten features are regular files with one name each, so
    nothing legitimate is refused by this rule.
    """
    outside = tmp_path / "outside.feature"
    outside.write_text(OUTSIDE_GHERKIN, encoding="utf-8")
    _features_root(tmp_artifact_root)
    os.link(outside, paths.features_dir(tmp_artifact_root) / CRM_FEATURE)
    entry = _feature_path(CRM_FEATURE)

    assert (
        rerun_report.read_verified_feature(entry, base=tmp_artifact_root)
        is None
    )
    assert (
        rerun_report.resolve_feature_path(entry, base=tmp_artifact_root) is None
    )
    listed, problems = rerun_report.list_verified_features(tmp_artifact_root)
    assert listed == []
    assert any(CRM_FEATURE in problem for problem in problems), problems

    manifest = _manifest_path(tmp_artifact_root)
    manifest.write_text(_entry_line(CRM_FEATURE, 9), encoding="utf-8")
    assert rerun_report.parse_rerun_file(base=tmp_artifact_root) == []


@pytest.mark.parametrize(
    "plant",
    [
        lambda location: location.mkdir(),
        pytest.param(
            lambda location: os.mkfifo(location),
            marks=pytest.mark.skipif(
                not (HAS_NAMED_PIPES and HAS_REFUSAL_DEADLINE),
                reason=(
                    "this platform has no named pipes, or no deadline to "
                    "guard them with"
                ),
            ),
        ),
    ],
    ids=["directory", "named-pipe"],
)
def test_a_non_regular_entry_is_refused(
    plant: Any, tmp_artifact_root: Path
) -> None:
    """Only a regular file is Gherkin a runner could execute.

    A directory and a named pipe both satisfy "an entry of that name exists
    inside the features directory", and neither is a feature: the pipe would
    additionally *block* the selection pass on open, which is why the open
    carries ``O_NONBLOCK`` and why the deadline guards this case too (CWE-400).
    The refusal is on the descriptor's ``fstat``, so it holds for a device node
    and anything else the filesystem can offer under a feature's name.
    """
    _features_root(tmp_artifact_root)
    plant(paths.features_dir(tmp_artifact_root) / CRM_FEATURE)
    entry = _feature_path(CRM_FEATURE)

    with _refusal_deadline():
        assert (
            rerun_report.read_verified_feature(entry, base=tmp_artifact_root)
            is None
        )
        assert (
            rerun_report.resolve_feature_path(entry, base=tmp_artifact_root)
            is None
        )
        listed, problems = rerun_report.list_verified_features(
            tmp_artifact_root
        )

    # A non-regular entry is not a *refused feature* but a directory member
    # that is not a feature at all, so it is absent from the accepted list
    # rather than named as a problem; with nothing else in the directory, the
    # empty-tree problem is what is reported.
    assert listed == []
    assert len(problems) == 1
    assert rerun_report.FEATURE_SUFFIX in problems[0]


def test_verify_feature_identity_refuses_a_replaced_entry(
    tmp_artifact_root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The hand-off check: the same name, a different object, refused.

    The engine runs in another process and opens the feature **by name**,
    because the AAP pins that spelling into the JSON ``uri`` and into this
    manifest (deviation 1, AAP 0.4.2), so a descriptor cannot be handed over
    and one window stays open by contract.  This is what closes as much of it
    as the contract allows: the identity is re-established immediately before
    the hand-off, so an entry swapped after selection is refused and named
    instead of silently executed.

    Both swaps are asserted because the second is the one an inode number alone
    would miss: the file is unlinked and recreated, which the filesystem
    routinely answers with the **same inode number**, and then overwritten in
    place at the same size -- so ``(st_dev, st_ino)`` compares equal and only
    the rest of the identity tuple can tell the objects apart.
    """
    location = _write_feature(tmp_artifact_root, CRM_FEATURE)
    feature = rerun_report.read_verified_feature(
        _feature_path(CRM_FEATURE), base=tmp_artifact_root
    )
    assert feature is not None
    assert rerun_report.verify_feature_identity(
        feature.path, feature.identity, base=tmp_artifact_root
    )

    # Swap one: unlinked and recreated with different contents.  The inode
    # number is commonly recycled here, which is exactly why the identity
    # carries the size and the timestamps as well.
    os.unlink(location)
    location.write_text(OUTSIDE_GHERKIN, encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger=rerun_report.logger.name):
        assert not rerun_report.verify_feature_identity(
            feature.path, feature.identity, base=tmp_artifact_root
        )
    assert caplog.records
    assert all(
        OUTSIDE_GHERKIN.strip() not in record.getMessage()
        for record in caplog.records
    )

    # Swap two: the same inode and the same size, contents replaced in place.
    # The timestamp is moved on explicitly rather than by waiting, because a
    # filesystem's timestamp granularity is coarser than two consecutive
    # writes and a sleep would make this assertion a race.
    replaced = _write_feature(tmp_artifact_root, CONTACT_FEATURE)
    before = rerun_report.read_verified_feature(
        _feature_path(CONTACT_FEATURE), base=tmp_artifact_root
    )
    assert before is not None
    with open(replaced, "r+b") as handle:
        handle.write(b"F" * len(GHERKIN_STANDIN.encode("utf-8")))
    information = os.stat(replaced)
    os.utime(
        replaced,
        ns=(information.st_atime_ns, information.st_mtime_ns + 10**9),
    )
    assert os.stat(replaced).st_size == information.st_size
    assert not rerun_report.verify_feature_identity(
        before.path, before.identity, base=tmp_artifact_root
    )


def test_list_verified_features_is_canonically_ordered_and_reports_refusals(
    tmp_artifact_root: Path, tmp_path: Path
) -> None:
    """The enumeration half: sorted, verified, and honest about what it dropped.

    A directory listing is as re-resolvable as a manifest entry, so every entry
    is examined relative to the verified root descriptor with links refused.
    The order is the run's **canonical feature order** -- ascending by
    repository-relative path -- imposed here rather than left to the
    filesystem, because the sharding and the merge in
    ``app/services/test_run_service.py`` both key on it: a listing that came
    back in directory order would make a merged result set depend on the order
    files happened to be created in.

    The refusals are returned rather than raised, per the AAP 0.4.1 exit table:
    fewer scenarios and status ``0``, with the problem reported.
    """
    # Created in deliberately non-alphabetical order, so a pass-through of the
    # filesystem's own order would show.
    for filename in (SESSION_FEATURE, CRM_FEATURE, NOTES_FEATURE):
        _write_feature(tmp_artifact_root, filename)
    # A file that is not Gherkin is not a feature and is not a problem either.
    (paths.features_dir(tmp_artifact_root) / "README.txt").write_text(
        GHERKIN_STANDIN, encoding="utf-8"
    )
    outside = tmp_path / "outside.feature"
    outside.write_text(OUTSIDE_GHERKIN, encoding="utf-8")
    _symlink_or_skip(
        outside, paths.features_dir(tmp_artifact_root) / SALES_FEATURE
    )

    listed, problems = rerun_report.list_verified_features(tmp_artifact_root)

    assert listed == sorted(
        _feature_path(filename)
        for filename in (SESSION_FEATURE, CRM_FEATURE, NOTES_FEATURE)
    )
    assert len(problems) == 1
    assert SALES_FEATURE in problems[0]
    assert OUTSIDE_GHERKIN.strip() not in problems[0]
    # Every accepted path is one the grammar accepts, which is what makes the
    # listing usable as a rerun location's path without a second check.
    for path in listed:
        assert rerun_report.validate_feature_path(path) == path


def test_an_absent_or_empty_features_directory_is_reported_not_raised(
    tmp_artifact_root: Path,
) -> None:
    """No features is a reported condition, never an exception.

    A checkout with no features directory, or an empty one, yields no
    scenarios; the AAP 0.4.1 exit table keeps that at status ``0`` with the
    problem named, so the caller receives an empty list and a problem rather
    than an ``OSError``.  Were this to fail, a run in a partially prepared
    workspace would crash instead of reporting.
    """
    absent_listed, absent_problems = rerun_report.list_verified_features(
        tmp_artifact_root
    )
    assert absent_listed == []
    assert len(absent_problems) == 1
    assert paths.FEATURES_DIR_NAME in absent_problems[0]

    _features_root(tmp_artifact_root)
    empty_listed, empty_problems = rerun_report.list_verified_features(
        tmp_artifact_root
    )
    assert empty_listed == []
    assert len(empty_problems) == 1
    assert rerun_report.FEATURE_SUFFIX in empty_problems[0]


def test_a_feature_file_beyond_the_byte_bound_is_refused(
    tmp_artifact_root: Path,
) -> None:
    """``MAX_FEATURE_BYTES`` bounds what one entry can cost the selection pass.

    The ten features in this repository total under twenty kilobytes, so a
    file past the bound is not one of them -- and reading it whole to find that
    out is what the bound prevents (CWE-400).  One byte over is a refusal, and
    a refusal is the tolerated outcome: reported, not raised.  Asserted from
    both sides, so that a bound tightened to nothing would fail here rather
    than silently refuse every feature in the suite.
    """
    at_bound = "F" * rerun_report.MAX_FEATURE_BYTES
    _write_feature(tmp_artifact_root, CRM_FEATURE, body=at_bound)

    accepted = rerun_report.read_verified_feature(
        _feature_path(CRM_FEATURE), base=tmp_artifact_root
    )
    assert accepted is not None
    assert accepted.text == at_bound

    _write_feature(
        tmp_artifact_root,
        CRM_FEATURE,
        body=f"{at_bound}F",
    )

    assert (
        rerun_report.read_verified_feature(
            _feature_path(CRM_FEATURE), base=tmp_artifact_root
        )
        is None
    )
    # The entry is still *resolvable* -- it is a regular, single-linked file --
    # so what the bound refuses is the read, which is where the cost is.
    assert (
        rerun_report.resolve_feature_path(
            _feature_path(CRM_FEATURE), base=tmp_artifact_root
        )
        is not None
    )


def test_a_feature_file_that_is_not_utf_8_is_refused(
    tmp_artifact_root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Undecodable contents are refused with a position and nothing else.

    A feature file is Gherkin, which is text; bytes that are not UTF-8 are not
    a feature this port can execute, and guessing an encoding would hand the
    engine a different document from the one on disk.  The diagnostic carries
    the byte offset -- enough to locate the damage -- and none of the bytes.
    """
    location = _write_feature(tmp_artifact_root, CRM_FEATURE)
    location.write_bytes(GHERKIN_STANDIN.encode("utf-8") + b"\xff\xfe")

    with caplog.at_level(logging.WARNING, logger=rerun_report.logger.name):
        assert (
            rerun_report.read_verified_feature(
                _feature_path(CRM_FEATURE), base=tmp_artifact_root
            )
            is None
        )

    assert caplog.records
    assert str(len(GHERKIN_STANDIN.encode("utf-8"))) in caplog.records[
        -1
    ].getMessage()


def test_a_listed_name_the_grammar_refuses_is_reported_not_accepted(
    tmp_artifact_root: Path,
) -> None:
    """The listing applies the grammar too, so its output needs no second check.

    A directory can hold a name the manifest grammar would never accept -- a
    hidden file whose name is nothing but the suffix, say -- and the listing is
    handed on as rerun locations, so it validates every name it accepts and
    reports the rest.  Were this to fail, ``list_verified_features`` would be a
    second, weaker statement of the accepted-entry rule.
    """
    _write_feature(tmp_artifact_root, CRM_FEATURE)
    _write_feature(tmp_artifact_root, rerun_report.FEATURE_SUFFIX)

    listed, problems = rerun_report.list_verified_features(tmp_artifact_root)

    assert listed == [_feature_path(CRM_FEATURE)]
    assert len(problems) == 1
    assert rerun_report.FEATURE_SUFFIX in problems[0]


def test_the_portable_branch_verifies_by_identity_instead(
    tmp_artifact_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Where there is no descriptor-relative open, the checks still hold.

    ``O_NOFOLLOW`` and ``dir_fd`` are POSIX-only and the AAP requires Windows
    as well (AAP 0.8), so the module carries a portable branch: the links are
    refused lexically and the descriptor is then confirmed, by ``lstat``
    identity, to hold the object the name reported -- which is what closes the
    window the lexical check on its own would leave.  Asserting it here is the
    only way it is exercised on a POSIX host, and an untested platform branch
    is a platform where the confinement is a claim rather than a behaviour.

    The refusals must be the *same* refusals: a linked features root, a linked
    entry and a non-regular entry, each rejected, and a genuine feature still
    accepted and still read from the descriptor that was checked.
    """
    monkeypatch.setattr(rerun_report, "_DESCRIPTOR_RELATIVE", False)
    _write_feature(tmp_artifact_root, CRM_FEATURE)
    entry = _feature_path(CRM_FEATURE)

    # A genuine feature is still accepted, read and listed.
    feature = rerun_report.read_verified_feature(entry, base=tmp_artifact_root)
    assert feature is not None
    assert feature.text == GHERKIN_STANDIN
    assert rerun_report.verify_feature_identity(
        entry, feature.identity, base=tmp_artifact_root
    )
    assert rerun_report.list_verified_features(tmp_artifact_root) == (
        [entry],
        [],
    )

    # The linked features root is refused lexically on this branch, which is
    # the stand-in for the no-follow open it cannot perform.
    elsewhere = tmp_path / "somebody-elses-features"
    elsewhere.mkdir()
    (elsewhere / CRM_FEATURE).write_text(OUTSIDE_GHERKIN, encoding="utf-8")
    linked_root = tmp_path / "linked-checkout"
    linked_root.mkdir()
    _symlink_or_skip(elsewhere, paths.features_dir(linked_root))
    assert rerun_report.read_verified_feature(entry, base=linked_root) is None
    assert rerun_report.resolve_feature_path(entry, base=linked_root) is None
    listed, problems = rerun_report.list_verified_features(linked_root)
    assert listed == []
    assert len(problems) == 1
    assert paths.FEATURES_DIR_NAME in problems[0]

    # A linked entry is refused, and so is a non-regular one.
    outside = tmp_path / "outside.feature"
    outside.write_text(OUTSIDE_GHERKIN, encoding="utf-8")
    location = paths.features_dir(tmp_artifact_root) / CRM_FEATURE
    os.unlink(location)
    _symlink_or_skip(outside, location)
    assert (
        rerun_report.read_verified_feature(entry, base=tmp_artifact_root)
        is None
    )
    assert (
        rerun_report.resolve_feature_path(entry, base=tmp_artifact_root) is None
    )

    os.unlink(location)
    location.mkdir()
    assert (
        rerun_report.resolve_feature_path(entry, base=tmp_artifact_root) is None
    )


@pytest.mark.parametrize(
    ("segment", "case"),
    [
        ("0", "zero"),
        ("-9", "negative"),
        ("9" * (rerun_report.MAX_LINE_NUMBER_DIGITS + 1), "oversized-digits"),
        ("٩", "non-ascii-digit"),
        ("9 9", "embedded-space"),
        ("9_9", "underscore-grouped"),
        ("0x9", "hexadecimal"),
    ],
)
def test_a_segment_that_is_not_a_line_number_is_refused(
    segment: str, case: str
) -> None:
    """Only a plain positive ASCII integer is a scenario line number.

    The narrowness is deliberate and it is what keeps the parser's error
    channel typed: a segment that is not a line number is how the parser
    recognises where the path ends, so every case here becomes *part of the
    path* and is then refused by the grammar -- the colon it still carries is
    forbidden in a path, and the digit-run bound is what keeps ``int()`` away
    from CPython's conversion limit, which raises a bare ``ValueError`` no
    caller of this module is expecting.

    Were this to fail, ``--rerun`` would either resolve a location the document
    never carried or exit with a traceback instead of a report.
    """
    line = (
        f"{paths.FILE_URI_SCHEME}{_feature_path(CRM_FEATURE)}"
        f"{rerun_report.LINE_SEPARATOR}{segment}"
    )

    with pytest.raises(rerun_report.RerunManifestError) as raised:
        rerun_report.parse_rerun_lines([line], source=SOURCE_LABEL)

    _assert_names_the_source_and_the_position(raised.value, number=1)
    assert not isinstance(raised.value, (ValueError, OSError)), case
    assert segment not in str(raised.value), case


@pytest.mark.parametrize(
    "source",
    [9, None, object()],
    ids=["integer", "none", "object"],
)
def test_a_lines_argument_that_is_not_an_iterable_is_refused(
    source: Any,
) -> None:
    """The container's type is checked before its elements'.

    ``parse_rerun_file`` accepts an iterable of lines so the round trip needs
    no file, which means the container is caller-supplied and can be anything.
    Nothing untyped may escape this module -- the AAP 0.4.1 exit table has the
    command *recognise* a manifest problem rather than classify an exception --
    so a non-iterable is the same typed error as a malformed line.
    """
    with pytest.raises(rerun_report.RerunManifestError) as raised:
        rerun_report.parse_rerun_lines(source, source=SOURCE_LABEL)

    assert not isinstance(raised.value, (ValueError, TypeError))
    assert SOURCE_LABEL in str(raised.value)
    assert type(source).__name__ in str(raised.value)


def test_whole_text_handed_to_the_line_parser_is_refused_with_a_hint() -> None:
    """A string is an iterable of characters, and that mistake is caught.

    Iterating a manifest's whole text one character at a time would report a
    nonsensical position for a nonsensical entry, so the string case is refused
    by name and pointed at the function that does take whole text.  Were this
    to fail, a caller's slip would surface as a parse error about line 47 of a
    one-line file.
    """
    text = _entry_line(CRM_FEATURE, 9)

    with pytest.raises(rerun_report.RerunManifestError) as raised:
        rerun_report.parse_rerun_lines(text, source=SOURCE_LABEL)

    assert "parse_rerun_text" in str(raised.value)
    # The correct call is accepted, which is what makes the hint actionable.
    assert rerun_report.parse_rerun_text(text, source=SOURCE_LABEL) == [
        rerun_report.RerunEntry(path=_feature_path(CRM_FEATURE), lines=(9,))
    ]


def test_a_manifest_path_that_cannot_be_used_is_the_same_typed_error() -> None:
    """An unusable *path* is refused like unusable contents, and stays typed.

    The path is part of the contract, not just the bytes behind it: a value
    carrying an embedded NUL survives ``Path()`` and then reaches ``os.open``
    as a bare ``ValueError``, which is not an ``OSError`` and would otherwise
    escape as a traceback the AAP 0.4.1 exit table has no row for.  It is
    converted here instead; the message names the *class* of failure and the
    original is chained, so the diagnosis survives for a log.  A value that is
    not a path at all takes the other branch and is refused by
    :func:`test_a_lines_argument_that_is_not_an_iterable_is_refused`.

    What the message does carry is the location it was asked for -- the
    caller's own argument, as every message in this module carries the source
    it is talking about.  That is not the manifest's contents, which is what
    the data-free rule is about, and in production the location is never
    caller-supplied at all: ``--rerun`` resolves it from
    ``app/utils/paths.py``.
    """
    with pytest.raises(rerun_report.RerunManifestError) as raised:
        rerun_report.parse_rerun_file(f"{CRM_FEATURE}\x00")

    assert not isinstance(raised.value, (ValueError, TypeError, OSError))
    assert isinstance(raised.value.__cause__, ValueError)
    assert ValueError.__name__ in str(raised.value)


def test_the_writer_refuses_a_worker_supplied_path_outside_the_grammar(
    tmp_artifact_root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The writer is held to the same grammar, and it is the harder half.

    The internal result document is assembled by worker processes, so a
    feature's ``path`` is worker-controlled input reaching a line-oriented
    artifact: a path carrying a terminator would emit a **second, forged
    entry** naming a feature that never failed, and a colon-bearing one
    fabricates a line number (CWE-93/CWE-117).  Both are refused before
    serialisation, and the refusal *propagates* -- it is the writer-failure
    exit class of the AAP 0.4.1 table, not a test outcome -- because silently
    dropping the entry would lose a real failure and emitting it would forge a
    location.

    Were this to fail, a worker could choose what a rerun executes.
    """
    forged = f"{_feature_path(CRM_FEATURE)}{rerun_report.LINE_ENDING}{_feature_path(SALES_FEATURE)}"
    result_set = _result_set(
        _feature_at(forged, [_scenario(9, statuses=(FAILED,))])
    )

    with caplog.at_level(logging.ERROR, logger=rerun_report.logger.name):
        with pytest.raises(rerun_report.RerunManifestError):
            rerun_report.write_rerun_txt(result_set, base=tmp_artifact_root)

    assert caplog.records
    assert caplog.records[-1].levelno == logging.ERROR
    # Nothing was written: a forged entry never reaches the artifact, and a
    # partial manifest is not published in its place.
    assert not paths.rerun_txt_path(tmp_artifact_root).exists()


@pytest.mark.parametrize(
    ("source", "number", "expected"),
    [
        (SOURCE_LABEL, 7, f"{SOURCE_LABEL}: line 7: "),
        (SOURCE_LABEL, None, f"{SOURCE_LABEL}: "),
        (None, 7, "line 7: "),
        (None, None, ""),
    ],
    ids=["source-and-position", "source-only", "position-only", "neither"],
)
def test_a_refusal_names_whatever_the_caller_could_tell_it(
    source: str | None, number: int | None, expected: str
) -> None:
    """The locating half of the diagnostic, in all four combinations.

    One validator serves both directions of the round trip, and they know
    different things: the parser knows the file and the entry's position, the
    writer knows neither -- so the prefix is built from what is available and
    reads correctly with any part of it missing.  The position is what an
    operator finds the entry by, given that the rest of the message
    deliberately reproduces none of it, so a prefix that silently dropped it
    would leave a refusal unlocatable.
    """
    with pytest.raises(rerun_report.RerunManifestError) as raised:
        rerun_report.validate_feature_path(
            f"..{paths.NORMALIZED_FEATURES_PREFIX[-1]}{CRM_FEATURE}",
            source=source,
            number=number,
        )

    assert str(raised.value).startswith(expected)
    assert CRM_FEATURE not in str(raised.value)


def test_a_features_directory_that_cannot_be_listed_is_reported(
    tmp_artifact_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An I/O failure while enumerating is reported, never raised.

    The listing feeds the sharding, and a workspace whose permissions or mount
    have gone wrong is not a test outcome: per the AAP 0.4.1 exit table the run
    reports the problem and stays at status ``0`` with fewer scenarios, so no
    ``OSError`` may escape this function.  The message names the ``errno``
    symbol rather than the exception, whose text carries the path the call was
    made with.

    The failure is injected because it cannot be provoked otherwise on a CI
    agent that owns its own workspace -- and injecting it is the only way the
    handler is ever executed.
    """
    _write_feature(tmp_artifact_root, CRM_FEATURE)

    def _refuse(*arguments: Any, **keywords: Any) -> list[str]:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(os, "listdir", _refuse)

    listed, problems = rerun_report.list_verified_features(tmp_artifact_root)

    assert listed == []
    assert len(problems) == 1
    assert "EACCES" in problems[0]


def test_an_entry_that_cannot_be_examined_is_reported_and_skipped(
    tmp_artifact_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One unexaminable entry costs its own scenarios and no others.

    The same tolerance per entry rather than per directory: an entry the
    operating system will not answer for is named and skipped, and the features
    beside it are still enumerated, because discarding a whole listing for one
    bad entry would drop every other feature's scenarios with it.
    """
    _write_feature(tmp_artifact_root, CRM_FEATURE)
    _write_feature(tmp_artifact_root, SALES_FEATURE)
    genuine_stat = os.stat

    def _refuse_one(path: Any, **keywords: Any) -> os.stat_result:
        if path == SALES_FEATURE or Path(str(path)).name == SALES_FEATURE:
            raise PermissionError(13, "Permission denied")
        return genuine_stat(path, **keywords)

    monkeypatch.setattr(os, "stat", _refuse_one)

    listed, problems = rerun_report.list_verified_features(tmp_artifact_root)

    assert listed == [_feature_path(CRM_FEATURE)]
    assert len(problems) == 1
    assert SALES_FEATURE in problems[0]
    assert "EACCES" in problems[0]


# =========================================================================== #
# Section 9 - what a refusal costs, and the platform branch it takes
#
# Two properties of the *refusal path* rather than of any single refusal.  A
# planted entry is refused once per call and a manifest may name many, so the
# cost of refusing has to be constant; and the portable branch - the one a
# Windows checkout takes, which AAP 0.8 puts in the support matrix - has to
# refuse the reparse points that platform offers, not only the symbolic links
# a POSIX host can plant.
# =========================================================================== #


def _next_descriptor() -> int:
    """Return the descriptor number a fresh open would be given.

    A leak detector that needs no ``/proc``: the platform hands out the lowest
    free descriptor, so the number a throwaway open receives rises by exactly
    the number of descriptors that are still held.

    :returns: The descriptor number, already closed again.
    """
    handle = os.open(os.devnull, os.O_RDONLY)
    os.close(handle)
    return handle


@pytest.mark.parametrize(
    "reader",
    [
        lambda entry, root: rerun_report.read_verified_feature(entry, base=root),
        lambda entry, root: rerun_report.resolve_feature_path(entry, base=root),
        lambda entry, root: rerun_report.verify_feature_identity(
            entry, (0, 0, 0, 0, 0), base=root
        ),
    ],
    ids=["read", "resolve", "verify-identity"],
)
def test_a_refused_entry_costs_no_descriptor(
    reader: Any, tmp_artifact_root: Path
) -> None:
    """Refusing an entry holds nothing open (CWE-772).

    The refusal happens *after* the entry is opened -- that is the whole design:
    what is checked is the descriptor, not the name -- so every refusal has a
    descriptor to dispose of, and a planted entry is refused on every call.  A
    directory named like a feature is refused for its ``fstat`` and is the
    cheapest case to repeat; forty repetitions of it held forty descriptors
    open before ownership was made conditional on the check passing, which
    exhausts the process limit and breaks the reads that follow.

    Measured by the descriptor number a throwaway open is given, which rises
    by exactly the number still held, so the assertion is on the *quantity*
    leaked rather than on a platform's table of open files.
    """
    _features_root(tmp_artifact_root)
    (paths.features_dir(tmp_artifact_root) / CRM_FEATURE).mkdir()
    entry = _feature_path(CRM_FEATURE)

    # One call first: the verified open imports nothing and caches nothing, but
    # measuring after it removes any first-call allocation from the comparison.
    assert not reader(entry, tmp_artifact_root)
    before = _next_descriptor()
    for _ in range(40):
        assert not reader(entry, tmp_artifact_root)

    assert _next_descriptor() == before


def test_the_portable_branch_refuses_a_reparse_point(
    tmp_artifact_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A junction is refused where ``O_NOFOLLOW`` is unavailable.

    The portable branch is what a Windows checkout takes, and there a
    **junction** redirects a directory exactly as a symbolic link does while
    ``Path.is_symlink()`` reports ``False`` for it: only the symlink reparse
    tag satisfies that test, and a junction carries the mount-point tag.  A
    features root replaced by a junction would therefore have passed a
    symlink-only check and become the anchor every entry is resolved against,
    which is the one thing that branch exists to prevent.

    Windows is not this host, so the platform's answer is what is simulated --
    an ``lstat`` result carrying a non-zero ``st_reparse_tag``, which is how
    CPython reports any reparse point -- while the code under test is the real
    branch, selected by turning the descriptor-relative capability off.  What
    is asserted is the refusal, not the simulation: every entry point refuses,
    and the listing reports the anchor rather than enumerating through it.
    """
    _write_feature(tmp_artifact_root, CRM_FEATURE)
    entry = _feature_path(CRM_FEATURE)
    monkeypatch.setattr(rerun_report, "_DESCRIPTOR_RELATIVE", False)

    # The features root -- and only it -- reports itself as a reparse point.
    root = paths.features_dir(tmp_artifact_root)
    genuine_lstat = os.lstat

    class _ReparsePoint:
        """An ``lstat`` result that carries Windows' reparse tag."""

        def __init__(self, source: os.stat_result) -> None:
            self.st_mode = source.st_mode
            self.st_dev = source.st_dev
            self.st_ino = source.st_ino
            self.st_nlink = source.st_nlink
            self.st_size = source.st_size
            self.st_mtime_ns = source.st_mtime_ns
            self.st_ctime_ns = source.st_ctime_ns
            self.st_reparse_tag = 0xA0000003

    def _tagged(path: Any, **keywords: Any) -> Any:
        result = genuine_lstat(path, **keywords)
        if Path(str(path)) == root:
            return _ReparsePoint(result)
        return result

    monkeypatch.setattr(os, "lstat", _tagged)

    assert rerun_report.read_verified_feature(entry, base=tmp_artifact_root) is None
    assert rerun_report.resolve_feature_path(entry, base=tmp_artifact_root) is None
    listed, problems = rerun_report.list_verified_features(tmp_artifact_root)
    assert listed == []
    assert len(problems) == 1
    assert paths.FEATURES_DIR_NAME in problems[0]
    assert "reparse" in problems[0]
