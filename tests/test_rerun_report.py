"""Tests for the grouped rerun manifest -- ``app/reporting/rerun_report.py``.

This module is the gate on a **machine input**, not on a log.
``FailedTestRunner.java:11`` declares ``features = "@target/rerun.txt"``, and in
this port ``run-tests --rerun`` reads the same file, so whatever the writer
emits is what a second run executes: a format error here silently changes which
scenarios are retried, and nothing downstream would notice.  That is why this
module asserts the manifest's *bytes* and not merely its meaning.

The measured baseline
---------------------
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
   implementation of the failure rule every ordering property falls out of.

A note on two settled cells
---------------------------
Two behaviours of the module under test were under revision while most of this
file was written, and both have since landed.  The assertions were deliberately
kept at a level either outcome satisfied, so nothing here had to be undone;
what the two now say is:

*The failure vocabulary is wider than the literal ``failed``.*
``FAILURE_STATUSES`` is the single statement of it, and :data:`FAILING_STATUSES`
below restates it as a literal so the two can be compared in both directions.
``failed`` is still what every scenario expected **in** the manifest carries
unless the vocabulary itself is the subject, and ``passed``/``skipped`` what
every scenario expected **out** carries -- those two are the only statuses whose
exclusion is settled, because the JVM's ``Status.isOk()`` is ``PASSED ||
SKIPPED`` and nothing else.

*The parser rejects rather than tolerates* a comment, an absolute path or a
traversal component, and a production read confines every entry to a real file
inside the features directory.  The contested input shapes are still asserted
through the *integrity property* -- such a line never becomes a selection --
which is what makes those assertions indifferent to skip-versus-reject.
"""

from __future__ import annotations

import copy
import hashlib
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

import pytest

from app.reporting import rerun_report
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

#: behave's own rerun formatter header, measured in ``behave/formatter/rerun.py``
#: and the shape this writer must never pass through.  The port's manifest is
#: grouped, ``file:``-prefixed and headerless.
BEHAVE_RERUN_HEADER: Final[str] = (
    "# -- RERUN: 2 failing scenarios during last test run."
)

#: Substrings that must never appear inside a *selected path*.  A parser that
#: turned behave's header, or a hand-written comment, into a selection would
#: produce one of these.
NEVER_IN_A_SELECTED_PATH: Final[tuple[str, ...]] = ("#", "RERUN", " ")

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


def _result_set(*features: JsonDict) -> ResultSet:
    """Wrap ``features`` in a merged result document.

    ``metadata`` is passed explicitly rather than left to
    :func:`app.reporting.events.run_metadata`, so that no test document depends
    on the host it was built on.

    :param features: Feature objects, in source order.
    :returns: The result set.
    """
    return new_result_set(metadata={}, features=list(features))


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
                # Expected IN the manifest: a literal ``failed`` step, which
                # both today's rule and the widened failure vocabulary the
                # review mandates treat as a failure.
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

    A *fractional* line is deliberately not among these cases; see
    :func:`test_a_fractional_scenario_line_never_selects_another_elements_line`
    for what is asserted about it and why.
    """
    element = _scenario(9, statuses=(FAILED,))
    element["line"] = bad_line
    result_set = _result_set(_feature(CRM_FEATURE, [element]))

    with caplog.at_level(logging.WARNING, logger=rerun_report.__name__):
        lines = rerun_report.build_rerun_lines(result_set)

    assert lines == []
    assert caplog.records, "an omitted failure must be reported"
    assert caplog.records[-1].levelno == logging.WARNING


def test_a_fractional_scenario_line_is_dropped_or_rendered_truthfully() -> None:
    """A fractional line is either refused or truncated -- never rounded.

    ``app/reporting/rerun_report.py`` coerces an element's ``line`` with
    ``int()``, so a fractional ``9.5`` becomes ``9``: a positive integer, and
    therefore emitted rather than skipped.  Whether it should instead be
    refused belongs to that module's owner, whose input-validation findings
    move malformed values from tolerated to rejected, so this module pins
    neither outcome -- only one of them is in the tree today and both are
    defensible.

    What is asserted is the whole of what both settlements share: the
    selection is either empty, or exactly ``int(line)``.  A value rounded *up*
    would be the dangerous outcome, because ``10`` is a line the document never
    carried and may well be another scenario's -- the manifest would then retry
    a scenario that passed while leaving the one that failed unretried, which
    is worse than a short manifest.

    One hazard is recorded here rather than asserted, because no assertion can
    hold under both settlements: truncation can *coincide* with a neighbouring
    element's line, and while it does, that neighbour is what a rerun selects.
    That is a property of the coercion in the module under test, not of this
    manifest format, and refusing a fractional line is what removes it.
    """
    failing = _scenario(9, statuses=(FAILED,))
    failing["line"] = 9.5
    result_set = _result_set(_feature(CRM_FEATURE, [failing]))

    lines = rerun_report.build_rerun_lines(result_set)

    selected = _line_numbers_of(lines[0]) if lines else []
    assert selected in ([], [9]), (
        "a fractional element line produced a selection that is neither empty "
        f"nor the truncation of it: {selected}"
    )
    assert 10 not in selected, (
        "a fractional line was rounded up to a line the document never carried"
    )


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
        (lambda root: root, IsADirectoryError),
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
    assert any(SALES_FEATURE in message for message in warnings), warnings

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
        "#",
        "   # indented comment",
    ],
    ids=["behave-header", "annotation", "bare-marker", "indented"],
)
def test_a_comment_line_never_becomes_a_selection(line: str) -> None:
    """Integrity property, asserted so that it holds either way.

    The module's docstring today says the parser *tolerates* a comment so that
    a hand-annotated file, or one left by behave's own rerun formatter, can
    still be read; a review finding wants such a line *rejected* instead.  Both
    are acceptable outcomes and the property that matters is the same under
    each: a comment never turns into a feature location.  Were this to fail,
    behave's header would be handed to a runner as a path -- selecting nothing
    at best, and an unintended file at worst.
    """
    good = f"{paths.FILE_URI_SCHEME}{_feature_path(CRM_FEATURE)}:9"

    try:
        entries = rerun_report.parse_rerun_lines([line, good], source="mixed")
    except rerun_report.RerunManifestError as error:
        # Rejection is the other acceptable outcome, and it is pinned as
        # tightly as acceptance: the error has to name the source and the text
        # it refused, because the exit table reports this on stderr and an
        # operator has to be able to find the line.
        assert "mixed" in str(error)
        assert line.strip() in str(error) or repr(line.strip()) in str(error)
        return

    # Acceptance, pinned exactly: the comment contributes nothing at all, and
    # the only selection is the one the valid line asked for.
    for entry in entries:
        for fragment in NEVER_IN_A_SELECTED_PATH:
            assert fragment not in entry.path
    assert [entry.path for entry in entries] == [_feature_path(CRM_FEATURE)]
    assert [entry.lines for entry in entries] == [(9,)]


@pytest.mark.parametrize(
    "line",
    [
        "/etc/passwd:9",
        "file:/etc/passwd:9",
        "file:../../etc/passwd:9",
        "file:./Crm.feature:9",
        "C:\\Windows\\win.ini:9",
    ],
    ids=["absolute", "absolute-scheme", "traversal", "dot-slash", "windows"],
)
def test_a_non_contract_path_never_invents_a_selection(line: str) -> None:
    """Integrity property for path shapes the parser may start rejecting.

    Acceptance of a line without the ``file:`` prefix, of an absolute path or
    of a ``..`` component is deliberately *not* pinned here -- a review finding
    moves those from tolerated to rejected.  What is pinned is what neither
    behaviour may violate: the parser either rejects the line through its one
    error channel, or it returns only locations spelled out in the input, never
    a path it composed itself.  Were this to fail, a damaged manifest could
    steer a rerun at a file the run never touched.
    """
    try:
        entries = rerun_report.parse_rerun_lines([line], source="hostile")
    except rerun_report.RerunManifestError as error:
        # Rejection, pinned: one error channel, naming the source, so the
        # command-line surface can report it and still exit 0 per the exit
        # table.
        assert "hostile" in str(error)
        return

    # Acceptance, pinned: the parser is transparent and never creative.  Every
    # path and every line number it returns is spelled out in the input, and
    # the addressable form it hands a runner reproduces the input rather than
    # composing a new location.
    for entry in entries:
        assert entry.path in line
        for number in entry.lines:
            assert str(number) in line
        for location in entry.locations:
            assert location.rsplit(rerun_report.LINE_SEPARATOR, 1)[0] in line
            assert paths.FILE_URI_SCHEME not in location


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
    """Observed behaviour, recorded rather than assumed.

    When the only failing step lies in a Background occurrence, this port's
    writer rolls the failure up into the scenario that immediately follows it
    and the manifest selects **that scenario's** line; the round trip therefore
    returns exactly one location per scenario preceded by a failed Background.
    That is what was measured here, and it is what this test pins.

    The wider parity question is noted and deliberately not asserted: the port
    and Cucumber-JVM may differ in how a failed Background surfaces in a
    scenario's rolled-up *status* elsewhere in the pipeline, and no live JVM run
    was available to settle it.  Were this test to fail, a background failure
    would either be unretryable or would select a Background's own line, which
    is not an addressable test case.
    """
    result_set = _result_set(
        _feature(
            CRM_FEATURE,
            [
                _background(statuses=(FAILED,)),
                _scenario(9, statuses=(SKIPPED, SKIPPED)),
                _background(statuses=(PASSED,)),
                _scenario(16, statuses=(PASSED,)),
            ],
        )
    )

    written = rerun_report.write_rerun_txt(result_set, base=tmp_artifact_root)

    assert rerun_report.rerun_locations(written) == [
        f"{_feature_path(CRM_FEATURE)}:9"
    ]
    assert str(BACKGROUND_LINE) not in written.read_text(encoding="utf-8")


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
    publishes three rules rather than one: the grammar's tokens and its two
    directions, the failure vocabulary stated once so the writer and any
    consumer compare the same set, and the two tiers of the confinement, which
    are public because ``app/services/test_run_service.py`` holds single
    locations as well as whole manifests.
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
        "resolve_feature_path",
        "validate_feature_path",
    }
    expected = grammar | vocabulary | confinement

    assert set(rerun_report.__all__) == expected
    assert len(rerun_report.__all__) == len(set(rerun_report.__all__))
    for name in sorted(expected):
        assert getattr(rerun_report, name) is not None

    assert rerun_report.LINE_ENDING == "\n"
    assert rerun_report.LINE_SEPARATOR == ":"
    assert rerun_report.FAILED_STATUS == "failed"
    assert rerun_report.FEATURE_SUFFIX == ".feature"
    assert issubclass(rerun_report.RerunManifestError, RuntimeError)


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
