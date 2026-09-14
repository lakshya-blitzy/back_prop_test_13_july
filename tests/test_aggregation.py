"""Tests for the one normalised result model, :mod:`app.reporting.aggregation`.

This module exists because six surfaces used to compute the same numbers for
themselves and disagreed -- a step-less Background counted as passed in the
single-page writer and unknown in ``GET /reports/summary``; hook statuses
reached some PrettyReports surfaces and not others; ``ambiguous`` folded onto
Undefined on the features overview and nowhere else; and failed scenarios were
derived as *total minus passed* on one page and as *a literal failed token* on
the next.  The aggregation module is the single authority that replaced them,
so every rule it settles is asserted here, once, in the place a later change
will trip over it.

Each rule below carries the evidence it rests on, and none of it is invention:

* the **severity fold** is the conventional Cucumber precedence, declared once
  in :data:`app.reporting.aggregation.STATUS_PRECEDENCE` -- nothing in the Java
  sources pins an ordering, because the step classes compute no aggregate
  status at all;
* the **binary verdict**, the ``ambiguous`` fold and the *total minus passed*
  arithmetic are measured from ``net.masterthought:cucumber-reporting:5.6.1``,
  the generator ``me.jvt.cucumber:reporting-plugin`` pulls in: its
  ``StatusCounter`` starts at ``PASSED`` and moves to ``FAILED`` on the first
  constituent that is not passed, so an **empty** counter answers ``PASSED``;
  its ``Element.setMetaData`` folds ``stepsStatus`` with ``beforeStatus`` and
  ``afterStatus``; its ``StatusDeserializer`` holds
  ``UNKNOWN_STATUSES = ["ambiguous"]`` and answers ``UNDEFINED`` for it; and
  its ``TagObject.getFailedScenarios()`` counts the elements the counter did
  not record as passed;
* the **selection rule** is the one ``app/reporting/cucumber_json.py`` applies,
  which is what makes the JSON artifact, both HTML artifacts and the viewer
  describe the same run.

The module under test imports the standard library and
:mod:`app.reporting.events` and nothing else, so these tests need no Flask
application, no browser, no network and no populated
``configuration.properties``.  ``tests/conftest.py`` owns ``sys.path`` for the
suite, as ``pytest.ini`` records, so nothing here manipulates the import path.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from app.reporting import aggregation as ag
from app.reporting import cucumber_json as json_writer

# --------------------------------------------------------------------------- #
# Builders.  Small and local: every test below states the one thing it is about
# and inherits nothing else, so a failure names a rule rather than a fixture.
# --------------------------------------------------------------------------- #


def step(status: str, duration: int | None = None) -> dict[str, Any]:
    """One step object carrying ``status`` and, when given, ``duration``."""
    result: dict[str, Any] = {"status": status}
    if duration is not None:
        result["duration"] = duration
    return {"keyword": "Given ", "name": "a step", "line": 3, "result": result}


def hook(status: str, duration: int = 0) -> dict[str, Any]:
    """One after-hook entry, the shape ``app/reporting/events.py`` records."""
    return {
        "match": {"location": "features.environment.after_scenario"},
        "result": {"status": status, "duration": duration},
        "embeddings": [],
    }


def scenario(
    name: str,
    steps: list[dict[str, Any]],
    *,
    selected: bool = True,
    after: list[dict[str, Any]] | None = None,
    started: str | None = None,
) -> dict[str, Any]:
    """One scenario element."""
    return {
        "type": "scenario",
        "keyword": "Scenario",
        "line": 5,
        "name": name,
        "description": "",
        "selected": selected,
        "id": f"f;{name}",
        "start_timestamp": started,
        "steps": steps,
        "after": after or [],
    }


def background(steps: list[dict[str, Any]], *, selected: bool = True) -> dict[str, Any]:
    """One Background occurrence, which carries no id, tags or hooks."""
    return {
        "type": "background",
        "keyword": "Background",
        "line": 2,
        "name": "",
        "description": "",
        "selected": selected,
        "steps": steps,
    }


def feature(name: str, elements: list[dict[str, Any]]) -> dict[str, Any]:
    """One feature object with a URI derived from ``name``."""
    return {
        "uri": f"file:features/{name}.feature",
        "path": f"features/{name}.feature",
        "id": name.lower(),
        "keyword": "Feature",
        "line": 1,
        "name": name,
        "description": "",
        "tags": [],
        "elements": elements,
    }


# --------------------------------------------------------------------------- #
# Normalisation and the severity fold
# --------------------------------------------------------------------------- #


def test_status_token_normalises_case_and_rejects_the_unknown() -> None:
    """A status is spelled one way, and an unrecognised one is not a pass.

    ``executing`` is the case that changed and the contract is now the wider
    one: it is a **behave** status name, so it is canonicalised through
    :data:`app.reporting.aggregation.STATUS_ALIASES` to ``untested`` -- the
    same fold ``app/reporting/cucumber_json.py`` applies -- rather than
    reported as a token nobody recognises.  While that table lived in the JSON
    writer alone, one run was graded differently depending on which file a
    reader opened: ``cucumber.json`` said ``untested`` and both HTML artifacts
    said *Unknown*.  A status genuinely outside the vocabulary, and a value
    that is not text at all, still answer :data:`UNKNOWN_STATUS`.
    """
    assert ag.status_token("Passed") == "passed"
    assert ag.status_token("  FAILED  ") == "failed"
    assert ag.status_token("executing") == ag.UNTESTED_STATUS
    assert ag.status_token("no-such-status") == ag.UNKNOWN_STATUS
    assert ag.status_token(None) == ag.UNKNOWN_STATUS
    assert ag.status_token(7) == ag.UNKNOWN_STATUS
    # One fold, not two: this name is the canonical normaliser under its
    # re-read reading, and it holds for every input rather than for the
    # examples above.  The one difference is the provenance of the word
    # ``unknown``, which the next assertion states.
    vocabulary: list[Any] = [
        *ag.KNOWN_STATUSES,
        *ag.STATUS_ALIASES,
        ag.UNKNOWN_STATUS,
        "  PASSED  ",
        "no-such-status",
        "",
        None,
        7,
    ]
    for status in vocabulary:
        assert ag.status_token(status) == ag.canonical_status(
            status, recorded=False
        ), status
    # A recorded ``unknown`` is behave's own status name and folds like the
    # other nine; the same word read back as a token this model produced is
    # echoed, which is what keeps a fold of a fold exact.
    assert ag.canonical_status(ag.UNKNOWN_STATUS) == ag.UNTESTED_STATUS
    assert ag.status_token(ag.UNKNOWN_STATUS) == ag.UNKNOWN_STATUS


def test_roll_up_status_follows_the_declared_precedence() -> None:
    """One failure is never averaged away by the passes around it."""
    assert ag.roll_up_status(["passed", "skipped", "failed"]) == "failed"
    assert ag.roll_up_status(["passed", "undefined", "pending"]) == "undefined"
    assert ag.roll_up_status(["passed", "ambiguous", "pending"]) == "ambiguous"
    assert ag.roll_up_status(["passed", "untested"]) == "untested"
    assert ag.roll_up_status(["passed", "passed"]) == "passed"


def test_roll_up_status_keeps_the_two_empty_cases_apart() -> None:
    """An empty collection and an unrecognised one answer differently.

    The unrecognised member is now spelled ``no-such-status``: ``executing``
    was used here before the alias table was shared, and it is no longer
    unrecognised -- it folds to ``untested``, which
    :func:`test_status_token_normalises_case_and_rejects_the_unknown` pins.
    """
    assert ag.roll_up_status([]) == ag.EMPTY_ELEMENT_STATUS
    assert ag.roll_up_status([], empty=ag.EMPTY_AGGREGATE_STATUS) == ag.UNKNOWN_STATUS
    assert (
        ag.roll_up_status(["no-such-status"], empty=ag.UNKNOWN_STATUS)
        == ag.UNKNOWN_STATUS
    )


def test_worst_status_is_the_same_function_not_a_second_fold() -> None:
    """The Pretty writer's historical name must not be a second implementation."""
    assert ag.worst_status is ag.roll_up_status


def test_counter_token_folds_ambiguous_onto_undefined_only_for_counting() -> None:
    """``StatusDeserializer`` rewrites ambiguous; the precedence does not."""
    assert ag.counter_token("ambiguous") == ag.UNDEFINED_STATUS
    assert ag.counter_token("Skipped") == "skipped"
    assert ag.counter_token(None) == ag.UNKNOWN_STATUS
    assert ag.AMBIGUOUS_STATUS in ag.STATUS_PRECEDENCE


# --------------------------------------------------------------------------- #
# The shared canonicalisation
#
# The alias table used to live in ``app/reporting/cucumber_json.py`` alone, so
# a step behave recorded as ``hook_error`` was published ``failed`` in the JSON
# artifact and rendered *Unknown* on both HTML artifacts: one run, two grades,
# decided by which file a reader opened.  The table is now this model's, every
# surface reads it here, and the tests below hold the two to each other.
# --------------------------------------------------------------------------- #


class StubStatus:
    """A behave-style status enum stand-in.

    behave's ``Status`` exposes both ``name`` and ``normalized_name``, and the
    normalised one folds ``untested_pending`` onto ``pending`` and
    ``untested_undefined`` onto ``undefined``.  A stub rather than the real
    enum, because importing the engine here would load it at collection time
    for a test that needs two attributes, and ``tests/conftest.py`` keeps
    behave out of collection deliberately.

    :param name: The member's raw name.
    :param normalized_name: The folded name, omitted for a member that has
        none -- which is what a simpler stand-in carries.
    """

    def __init__(self, name: str, normalized_name: str | None = None) -> None:
        self.name = name
        if normalized_name is not None:
            self.normalized_name = normalized_name

    def __str__(self) -> str:
        """Return the raw name, so a fallback coercion has something to read."""
        return self.name


@pytest.mark.parametrize(
    ("alias", "expected"),
    sorted(ag.STATUS_ALIASES.items()),
    ids=sorted(ag.STATUS_ALIASES),
)
def test_every_behave_only_status_is_canonicalised_on_every_surface(
    alias: str, expected: str
) -> None:
    """Each entry of the shared table folds where the JSON writer folds it.

    The expectation is the table's own value and the *point* of the assertion
    is the pair of tests around it: one holds the table against the writer's
    (:func:`test_the_alias_table_is_the_json_writers_table`), the other holds
    every consumer to this function.  ``hook_error`` is the member worth
    naming: were it to fold to ``passed``, a scenario whose teardown blew up
    would be badged green on both HTML artifacts while ``cucumber.json``
    reported a failure.

    Each entry is asserted under the JSON writer's own fallback, because that
    is the contract the table states.  Nine of the ten then fold identically
    whatever the fallback; the tenth is ``unknown``, which is a name this
    model's vocabulary *has* -- its eighth token, for an outcome nothing
    established -- so under the model's own fallback it is answered as itself.
    That is the single documented boundary between the two surfaces and
    :func:`test_the_fallback_parameter_reproduces_the_json_writers_answer`
    pins it from the other side.
    """
    assert ag.canonical_status(alias, fallback=ag.UNTESTED_STATUS) == expected
    assert expected in ag.KNOWN_STATUSES
    # Case and padding are folded before the table is consulted, and the fold
    # is reached through the historical name every consumer imports.
    assert ag.status_token(f"  {alias.upper()} ", fallback=ag.UNTESTED_STATUS) == (
        expected
    )
    if alias == ag.UNKNOWN_STATUS:
        assert ag.status_token(alias) == ag.UNKNOWN_STATUS
    else:
        assert ag.status_token(alias) == expected


def test_one_run_is_graded_the_same_by_the_model_and_by_the_json_writer() -> None:
    """The end-to-end form of the rule, which is the form that matters.

    The two surfaces each fold a recorded status **once, at their own
    ingress**: this model where :func:`app.reporting.aggregation.decorate_element`
    writes its canonical copy, and ``app/reporting/cucumber_json.py`` where it
    builds the report.  So the contract is not that two helper functions agree
    in isolation but that one document, published both ways, names the same
    status for the same step -- for every status either vocabulary can name,
    behave's own ``unknown`` included.

    Were this to fail, a reader opening ``cucumber.json`` and a reader opening
    either HTML artifact would be told different things about the same step,
    which is the defect the shared table was introduced to remove.
    """
    for recorded in (*ag.KNOWN_STATUSES, *ag.STATUS_ALIASES):
        document = {
            "features": [feature("F", [scenario("s", [step(recorded, duration=5)])])],
            "dry_run": False,
        }
        graded = ag.normalize_run(document).features[0]["elements"][0]
        published = json_writer.build_cucumber_json(document)[0]["elements"][0]
        emitted = published["steps"][0]["result"]["status"]
        assert graded["steps"][0]["result"]["status"] == emitted, recorded
        assert graded["status"] == emitted, recorded
        assert graded[ag.EFFECTIVE_STATUS_KEY] == emitted, recorded

    # The one case that is not a recorded name: a result that named no status
    # at all.  The model keeps its own eighth token, which the statistics pages
    # and the steps overview render as Unknown and which no step column counts,
    # while the artifact carries the publisher-parseable fallback.  The model
    # stays internally consistent about it, which is the property that matters.
    nameless = {
        "features": [
            feature("F", [scenario("s", [{"keyword": "Given ", "result": {}}])])
        ],
        "dry_run": False,
    }
    run = ag.normalize_run(nameless)
    element = run.features[0]["elements"][0]
    assert element["steps"][0]["result"]["status"] == ag.UNKNOWN_STATUS
    assert element["status"] == ag.UNKNOWN_STATUS
    assert run.summary["steps"][ag.SUMMARY_BY_STATUS_KEY] == {ag.UNKNOWN_STATUS: 1}
    assert json_writer.build_cucumber_json(nameless)[0]["elements"][0]["steps"][0][
        "result"
    ]["status"] == ag.UNTESTED_STATUS


def test_the_alias_table_is_the_json_writers_table() -> None:
    """The two tables are one table, so the surfaces cannot drift apart.

    ``app/reporting/cucumber_json.py`` is imported **read-only** here: this
    module asserts the equality rather than the writer importing the model or
    the model importing the writer, because the writer's vocabulary
    legitimately differs in its *fallback* and only in that.  Were this to
    fail, a behave status would grade one way in the machine-readable artifact
    and another on both report pages -- the defect the shared table removed.
    """
    assert ag.STATUS_ALIASES == json_writer.STATUS_ALIASES
    assert len(ag.STATUS_ALIASES) == 10
    assert set(ag.STATUS_ALIASES.values()) <= set(ag.KNOWN_STATUSES)


def test_a_behave_status_enum_is_read_through_its_normalized_name() -> None:
    """An enum handed over directly is read the way the engine reads it.

    behave's own enum folds its two pending spellings and its undefined
    spelling, so ``normalized_name`` wins over ``name``; an object carrying
    only ``name`` is still understood, and one carrying neither is coerced to
    text and then refused rather than reported as a pass.
    """
    assert ag.canonical_status(StubStatus("pending_warn", "pending")) == "pending"
    assert ag.canonical_status(StubStatus("untested_undefined", "undefined")) == (
        ag.UNDEFINED_STATUS
    )
    assert ag.canonical_status(StubStatus("hook_error")) == "failed"
    assert ag.canonical_status(StubStatus("no-such-status")) == ag.UNKNOWN_STATUS
    assert ag.status_name(StubStatus("failed", "failed")) == "failed"
    assert ag.status_name(None) == ""


@pytest.mark.parametrize(
    "recorded",
    [
        pytest.param("untested", id="untested"),
        pytest.param("skipped", id="skipped"),
        pytest.param(None, id="no-status-at-all"),
        pytest.param("failed", id="even-a-failure-behave-never-ran"),
    ],
)
def test_the_dry_run_mapping_follows_the_match_state_and_nothing_else(
    recorded: Any,
) -> None:
    """Under ``--dry-run`` the recorded status is not consulted at all.

    Measured: the JVM emits a matched step ``passed`` and an unmatched one
    ``undefined`` under ``dryRun``, while behave records ``untested`` for
    both.  The rule existed in the JSON writer alone, so one dry run published
    19 ``passed`` steps in ``cucumber.json`` and 60 ``untested`` badges on the
    single-page HTML artifact from the same document, and the rerun manifest
    -- which selects on ``undefined`` -- was empty.  Were this to fail, those
    three readings would disagree again.
    """
    assert ag.canonical_status(recorded, matched=True, dry_run=True) == "passed"
    assert ag.canonical_status(recorded, matched=False, dry_run=True) == (
        ag.UNDEFINED_STATUS
    )
    # Never ``untested``, whichever way the match state falls.
    for matched in (True, False):
        assert ag.canonical_status(
            recorded, matched=matched, dry_run=True
        ) != ag.UNTESTED_STATUS


def test_a_steps_own_match_state_decides_its_dry_run_status() -> None:
    """The step-shaped entry point reads ``matched`` off the step.

    A step with no boolean flag is read as matched, which is this module's
    convention for an absent flag and the conservative answer: grading it
    ``undefined`` would invent a failure and put a scenario nobody reported as
    failing into the rerun manifest.
    """
    matched = {"matched": True, "result": {"status": "untested"}}
    unmatched = {"matched": False, "result": {"status": "untested"}}
    assert ag.canonical_step_status(matched, dry_run=True) == "passed"
    assert ag.canonical_step_status(unmatched, dry_run=True) == ag.UNDEFINED_STATUS
    assert ag.canonical_step_status({"result": {"status": "untested"}}, dry_run=True) == (
        "passed"
    )
    # Outside a dry run the flag is not read at all.
    assert ag.canonical_step_status(unmatched) == ag.UNTESTED_STATUS
    assert ag.canonical_step_status({"result": {"status": "hook_error"}}) == "failed"


def test_the_fallback_parameter_reproduces_the_json_writers_answer() -> None:
    """The one residual difference between the two surfaces, stated as a rule.

    This model has an eighth token for "nobody established an outcome"; the
    machine-readable artifact has no such name, because the publisher parses
    the seven Cucumber names, so its fallback is ``untested``.  A caller
    holding to that contract passes ``fallback="untested"`` and gets that
    writer's answer for **every** input from this one implementation, which is
    what makes the handover exact rather than approximate.
    """
    vocabulary: list[Any] = [
        *ag.KNOWN_STATUSES,
        *ag.STATUS_ALIASES,
        ag.UNKNOWN_STATUS,
        "  PASSED  ",
        "no-such-status",
        "",
        None,
        7,
    ]
    for status in vocabulary:
        assert ag.canonical_status(
            status, fallback=ag.UNTESTED_STATUS
        ) == json_writer.map_step_status(status), status
    # The default fallback is the model's own token, and it differs from the
    # writer's answer in exactly one case: a value that named no status at all.
    # Every status either surface can *name* -- behave's ``unknown`` included,
    # which folds through the shared table on both -- grades identically.
    assert ag.canonical_status(None) == ag.UNKNOWN_STATUS
    assert json_writer.map_step_status(None) == ag.UNTESTED_STATUS
    assert ag.canonical_status(ag.UNKNOWN_STATUS) == json_writer.map_step_status(
        ag.UNKNOWN_STATUS
    )
    assert ag.canonical_status("no-such-status") == ag.UNKNOWN_STATUS
    assert json_writer.map_step_status("no-such-status") == ag.UNTESTED_STATUS


def test_canonicalisation_is_idempotent_on_every_token_it_answers_with() -> None:
    """Decoration writes a canonical status that a fold then re-reads.

    Every one of the eight tokens has to survive that second reading, or an
    element would be graded from a status its own steps no longer carry.  The
    re-read goes through :func:`app.reporting.aggregation.status_token`, which
    is the reading :func:`roll_up_status` and :func:`counter_token` use for
    exactly this reason: the seven Cucumber names are fixed points of either
    reading, and the eighth -- this model's own ``unknown`` -- is a fixed point
    of that one.
    """
    for token in (*ag.KNOWN_STATUSES, ag.UNKNOWN_STATUS):
        assert ag.status_token(token) == token, token
        assert ag.roll_up_status([token], empty=ag.UNKNOWN_STATUS) == token, token
    for token in ag.KNOWN_STATUSES:
        assert ag.canonical_status(token) == token, token


def test_an_unknown_outcome_can_no_longer_be_folded_away_as_a_pass() -> None:
    """The model must not contradict its own binary reading.

    :data:`UNKNOWN_STATUS` was absent from the severity order, so a fold over
    a pass and an unestablished outcome answered ``passed`` while
    :func:`element_verdict` of the same element answered ``failed``.  It now
    ranks between ``untested`` and ``passed``: below a status behave recorded,
    above a pass it never recorded.
    """
    assert ag.roll_up_status(["passed", ag.UNKNOWN_STATUS]) == ag.UNKNOWN_STATUS
    assert ag.roll_up_status(["passed", "no-such-status"]) == ag.UNKNOWN_STATUS
    assert ag.UNKNOWN_STATUS in ag.STATUS_PRECEDENCE
    assert ag.STATUS_PRECEDENCE.index(ag.UNTESTED_STATUS) < ag.STATUS_PRECEDENCE.index(
        ag.UNKNOWN_STATUS
    ) < ag.STATUS_PRECEDENCE.index("passed")
    # And the element's two readings now agree about it.
    element = scenario("s", [step("passed"), step("no-such-status")])
    assert ag.element_status(element) == ag.UNKNOWN_STATUS
    assert ag.element_verdict(element) == ag.VERDICT_FAILED


def test_the_failure_tokens_are_the_complement_of_cucumbers_status_is_ok() -> None:
    """``isOk()`` is ``PASSED || SKIPPED``; everything else selects a rerun.

    The set is also a **prefix** of the severity order, which is what makes
    "any member of a unit failed" and "the unit's fold is a failure" one
    predicate -- the property ``app/reporting/rerun_report.py`` relies on to
    express its selection through this model.
    """
    assert ag.FAILURE_TOKENS == {"failed", "undefined", "ambiguous", "pending"}
    assert ag.STATUS_PRECEDENCE[: len(ag.FAILURE_TOKENS)] == (
        "failed",
        "undefined",
        "ambiguous",
        "pending",
    )
    for status in ("failed", "error", "hook_error", "cleanup_error", "xfailed"):
        assert ag.is_failure_token(status), status
    for status in ("passed", "xpassed", "skipped", "untested", "executing", None):
        assert not ag.is_failure_token(status), status
    assert ag.is_passed_token("xpassed")
    assert not ag.is_passed_token("skipped")


def test_is_dry_run_reads_the_flag_the_collector_records() -> None:
    """One reader of the key, so no consumer spells it."""
    assert ag.is_dry_run({"dry_run": True})
    assert not ag.is_dry_run({"dry_run": False})
    assert not ag.is_dry_run({})
    assert not ag.is_dry_run(None)


# --------------------------------------------------------------------------- #
# The element: two readings of one element, and the empty case both settle
# --------------------------------------------------------------------------- #


def test_a_step_less_element_is_passed_in_both_readings() -> None:
    """``EmployeeFc.feature``'s empty Background, and an empty StatusCounter."""
    empty = background([])
    assert ag.element_status(empty) == "passed"
    assert ag.element_verdict(empty) == ag.VERDICT_PASSED
    assert ag.EMPTY_ELEMENT_STATUS == "passed"


def test_an_element_whose_statuses_are_all_unrecognised_is_not_passed() -> None:
    """A step that arrived without a status is a step nobody knows about."""
    assert ag.element_status(scenario("s", [step("")])) == ag.UNKNOWN_STATUS
    assert ag.element_verdict(scenario("s", [step("")])) == ag.VERDICT_FAILED


def test_hook_statuses_fold_into_the_element_on_every_surface() -> None:
    """``Element.calculateElementStatus`` folds steps with before and after."""
    hooked = scenario("s", [step("passed", 10)], after=[hook("failed", 99)])
    assert ag.element_status(hooked) == "failed"
    assert ag.element_verdict(hooked) == ag.VERDICT_FAILED
    # And the steps-only reading stays available, unchanged by the hook.
    assert ag.element_steps_status(hooked) == "passed"
    assert ag.hook_statuses(hooked) == ["failed"]


def test_a_before_hook_is_read_as_well_as_an_after_hook() -> None:
    """Both groups take part, ``before`` first, as ``setMetaData`` reads them."""
    element = scenario("s", [step("passed")], after=[hook("passed")])
    element["before"] = [hook("failed")]
    assert ag.hook_statuses(element) == ["failed", "passed"]
    assert ag.element_verdict(element) == ag.VERDICT_FAILED


def test_the_verdict_is_binary_for_every_non_passing_status() -> None:
    """5.6.1 has no third answer: anything not passed is a failed element."""
    for status in ("failed", "skipped", "pending", "undefined", "ambiguous", "untested"):
        element = scenario("s", [step(status)])
        assert ag.element_verdict(element) == ag.VERDICT_FAILED, status
        # while the severity reading keeps the status it was given
        assert ag.element_status(element) == status, status


def test_an_element_is_not_coloured_by_its_neighbours() -> None:
    """Each element's badge answers for that element alone."""
    passing = scenario("ok", [step("passed")])
    failing = scenario("bad", [step("failed")])
    assert ag.element_status(passing) == "passed"
    assert ag.element_status(failing) == "failed"


# --------------------------------------------------------------------------- #
# The scenario unit: one test case, one effective status
#
# A Background occurrence and the scenario it precedes are one test case.  The
# JSON element shape keeps them apart -- measured: a Cucumber-JVM 7.2.3 probe
# emits the Background's failed step and the scenario's own steps as
# ``skipped`` -- so what has to agree is every *derived* reading, which is what
# these functions are.
# --------------------------------------------------------------------------- #


def test_a_background_failure_is_the_units_effective_status() -> None:
    """One test case, one grade, whichever surface asks.

    The element-level readings are deliberately unchanged -- the Background
    reads ``failed`` for itself and the scenario reads ``skipped`` for itself,
    which is what the JSON artifact carries and what each badge answers for --
    while the *unit* reads ``failed``, which is what a count, a failures
    overview and the rerun manifest consult.  Were this to fail, one run would
    be graded four ways again: ``skipped`` in the summary, ``failed`` on the
    Pretty pages, ``skipped`` in the JSON and *selected* by the manifest.
    """
    failed_background = background([step("failed", 5)])
    skipped_scenario = scenario("s", [step("skipped"), step("skipped")])
    unit = [failed_background, skipped_scenario]

    assert ag.element_status(failed_background) == "failed"
    assert ag.element_status(skipped_scenario) == "skipped"
    assert ag.unit_status(unit) == "failed"
    assert ag.unit_verdict(unit) == ag.VERDICT_FAILED
    # The unit is exactly what ``element_units`` groups.
    assert ag.element_units([failed_background, skipped_scenario]) == [unit]


def test_a_units_reading_covers_every_members_steps_and_hooks() -> None:
    """Hooks take part, and a lone element is its own unit.

    A scenario whose setup hook failed and whose steps never ran is a failed
    test case, which is why the JVM's rerun formatter keys on the test-case
    result rather than on a step.
    """
    hooked = scenario("s", [step("passed")], after=[hook("failed")])
    assert ag.unit_status([hooked]) == "failed"
    assert ag.unit_verdict([hooked]) == ag.VERDICT_FAILED
    # A unit with nothing at all to count is passed: ``StatusCounter``'s
    # initial value, and the reference tree's empty Background.
    assert ag.unit_status([background([])]) == ag.EMPTY_ELEMENT_STATUS
    assert ag.unit_verdict([background([]), scenario("s", [])]) == ag.VERDICT_PASSED
    # And a passing unit stays a pass, so the fold is not one-way.
    passing = [background([step("passed")]), scenario("s", [step("passed")])]
    assert ag.unit_status(passing) == "passed"
    assert ag.unit_verdict(passing) == ag.VERDICT_PASSED


def test_a_units_reading_applies_the_dry_run_rule_to_steps_only() -> None:
    """The unit reading is where the rerun manifest reads a dry run.

    A hook resolves no step definition, so the dry-run rule does not touch
    one: the JSON writer builds a hook's result with ``dry_run=False`` for the
    same reason, and grading an ``untested`` teardown as ``undefined`` would
    select every scenario of a dry run.
    """
    unmatched = {"matched": False, "result": {"status": "untested"}}
    matched = {"matched": True, "result": {"status": "untested"}}
    hooked = {"steps": [matched], "after": [{"result": {"status": "untested"}}]}

    assert ag.unit_status([{"steps": [matched]}], dry_run=True) == "passed"
    assert ag.unit_status([{"steps": [unmatched]}], dry_run=True) == (
        ag.UNDEFINED_STATUS
    )
    assert ag.is_failure_token(ag.unit_status([{"steps": [unmatched]}], dry_run=True))
    # The hook keeps the status behave recorded, so the unit reads ``untested``
    # rather than ``passed`` -- and ``untested`` is not a failure, which is
    # what keeps a dry run's teardown from selecting every scenario.
    assert ag.unit_status([hooked], dry_run=True) == ag.UNTESTED_STATUS
    assert not ag.is_failure_token(ag.unit_status([hooked], dry_run=True))


# --------------------------------------------------------------------------- #
# Durations: steps only, with the sample count
# --------------------------------------------------------------------------- #


def test_duration_sample_rejects_what_the_step_row_rejects() -> None:
    """One predicate, so a value a cell declines cannot still reach a total."""
    assert ag.duration_sample(5) == 5
    assert ag.duration_sample(0) == 0
    assert ag.duration_sample(True) is None
    assert ag.duration_sample(1.5) is None
    assert ag.duration_sample(-1) is None
    assert ag.duration_sample("7") is None


def test_element_duration_sums_steps_only_and_counts_its_samples() -> None:
    """A hook never lengthens its scenario; a skipped step is no sample."""
    element = scenario(
        "s",
        [step("passed", 5), step("skipped"), step("passed", 7)],
        after=[hook("passed", 999)],
    )
    assert ag.element_duration(element) == (12, 2)
    assert ag.element_duration_ns(element) == 12
    assert ag.element_duration({}) == (0, 0)


def test_format_duration_seconds_separates_a_real_zero_from_no_sample() -> None:
    """Three decimals of seconds, or nothing to render at all."""
    assert ag.format_duration_seconds(1_500_000_000, 1) == "1.500"
    assert ag.format_duration_seconds(0, 1) == "0.000"
    assert ag.format_duration_seconds(0, 0) is None
    assert ag.format_duration_seconds("nonsense") is None


# --------------------------------------------------------------------------- #
# Timestamps
# --------------------------------------------------------------------------- #


def test_parse_timestamp_accepts_the_collectors_shape_and_rejects_nonsense() -> None:
    """Every instant returned is aware, so any two of them compare."""
    parsed = ag.parse_timestamp("2026-01-02T03:04:05.678Z")
    assert parsed is not None
    assert parsed.tzinfo is not None
    assert ag.parse_timestamp("not a timestamp") is None
    assert ag.parse_timestamp(None) is None


def test_earliest_start_is_verbatim_and_ignores_backgrounds() -> None:
    """The value on a page is the value the JSON artifact carries."""
    features = [
        feature(
            "A",
            [
                background([step("passed")]),
                scenario("late", [step("passed")], started="2026-01-02T03:04:09.000Z"),
                scenario("early", [step("passed")], started="2026-01-02T03:04:01.500Z"),
                scenario("broken", [step("passed")], started="not a timestamp"),
            ],
        )
    ]
    assert ag.earliest_start(features) == "2026-01-02T03:04:01.500Z"
    assert ag.earliest_start([]) is None


# --------------------------------------------------------------------------- #
# Selection, applied once
# --------------------------------------------------------------------------- #


def test_selected_features_drops_a_unit_whole_and_leaves_the_input_alone() -> None:
    """A Background occurrence shares its scenario's fate."""
    document = {
        "features": [
            feature(
                "A",
                [
                    background([step("passed")], selected=False),
                    scenario("gone", [step("passed")], selected=False),
                    background([step("passed")]),
                    scenario("kept", [step("failed")]),
                ],
            )
        ]
    }
    kept = ag.selected_features(document)
    assert [element["name"] for element in kept[0]["elements"]] == ["", "kept"]
    # the caller's document is untouched
    assert len(document["features"][0]["elements"]) == 4


def test_selected_features_drops_a_feature_with_no_surviving_test_case() -> None:
    """An occurrence is emitted *for* a test case, so one alone represents none."""
    document = {
        "features": [
            feature("NoneSelected", [scenario("x", [step("passed")], selected=False)]),
            feature("BackgroundOnly", [background([step("passed")])]),
            feature("Kept", [scenario("y", [step("passed")])]),
        ]
    }
    assert [f["name"] for f in ag.selected_features(document)] == ["Kept"]


def test_selected_features_tolerates_a_run_that_produced_nothing() -> None:
    """``None`` is the zero-scenario row of the exit contract, not an error."""
    assert ag.selected_features(None) == []
    assert ag.selected_features({"features": "nonsense"}) == []


# --------------------------------------------------------------------------- #
# Counting: the statistics row every table reads
# --------------------------------------------------------------------------- #


def test_count_steps_counts_every_column_and_the_total() -> None:
    """A token with no column of its own still counts towards Total."""
    counts = ag.count_steps(
        ["passed", "failed", "skipped", "pending", "ambiguous", "untested", None]
    )
    assert counts["steps_passed"] == 1
    assert counts["steps_failed"] == 1
    assert counts["steps_skipped"] == 1
    assert counts["steps_pending"] == 1
    assert counts["steps_undefined"] == 1  # the ambiguous one
    assert counts["steps_total"] == 7
    assert set(ag.COUNT_KEYS) - set(counts) == {
        "scenarios_passed",
        "scenarios_failed",
        "scenarios_total",
    }


def test_stats_of_reproduces_the_generators_row_arithmetic() -> None:
    """Steps by column, step durations only, and failed = total minus passed."""
    stats = ag.stats_of(
        [
            background([]),
            scenario("ambiguous one", [step("ambiguous", 7)], after=[hook("failed", 99)]),
            scenario("passing one", [step("passed", 5)]),
        ]
    )
    assert stats["steps_undefined"] == 1
    assert stats["steps_passed"] == 1
    assert stats["steps_total"] == 2
    assert stats["scenarios_total"] == 2
    assert stats["scenarios_passed"] == 1
    assert stats["scenarios_failed"] == 1
    assert stats["duration_ns"] == 12
    assert stats["duration_samples"] == 2
    assert stats["status"] == ag.VERDICT_FAILED


def test_stats_of_never_counts_a_background_as_a_scenario() -> None:
    """A Background occurs once per scenario, so counting it would double up."""
    stats = ag.stats_of([background([step("failed")]), scenario("s", [step("passed")])])
    assert stats["scenarios_total"] == 1
    assert stats["scenarios_passed"] == 1
    assert stats["scenarios_failed"] == 0
    # the Background's failure still fails the row, as the feature overview shows
    assert stats["status"] == ag.VERDICT_FAILED


def test_stats_of_counts_a_scenario_by_its_effective_verdict() -> None:
    """A tag row sees the scenario without its Background, and still grades it.

    A tag's subjects are scenario elements lifted away from the occurrences
    that precede them, so the unit reading has to travel *on* the element --
    which is what ``effective_verdict`` is for.  An undecorated element keeps
    the old behaviour and is graded from its own body, which is what lets a
    hand-built row count identically.
    """
    decorated = ag.decorate_feature(
        feature(
            "A",
            [background([step("failed")]), scenario("blocked", [step("skipped")])],
        )
    )
    blocked = decorated["elements"][1]

    # The scenario alone, as a tag row holds it.
    row = ag.tag_row("@Smoke", [blocked])
    assert row["scenarios_total"] == 1
    assert row["scenarios_failed"] == 1
    assert row["scenarios_passed"] == 0
    assert row["status"] == ag.VERDICT_FAILED
    # And an undecorated element is graded from its own body.
    undecorated = ag.stats_of([scenario("plain", [step("passed")])])
    assert undecorated["scenarios_passed"] == 1
    assert undecorated["status"] == ag.VERDICT_PASSED


def test_stats_of_ignores_an_unselected_element() -> None:
    """A scenario that never ran cannot inflate a row."""
    stats = ag.stats_of([scenario("gone", [step("passed")], selected=False)])
    assert stats["steps_total"] == 0
    assert stats["scenarios_total"] == 0
    assert stats["status"] == ag.VERDICT_PASSED


def test_stats_row_and_tag_row_are_the_same_arithmetic() -> None:
    """A tag row and a feature row can no longer grade one scenario differently."""
    subjects = [scenario("s", [step("failed", 3)])]
    row = ag.stats_row("X", subjects, "report-feature_1.html")
    tag = ag.tag_row("@X", subjects, "report-tag_1.html")
    assert row["name"] == "X"
    assert row["href"] == "report-feature_1.html"
    assert tag["name"] == "@X"
    figures = {key: value for key, value in row.items() if key not in ("name", "href")}
    assert figures == {
        key: value for key, value in tag.items() if key not in ("name", "href")
    }


def test_build_tag_rows_keeps_the_collection_order_and_links_each_row() -> None:
    """The page set must not reshuffle between two runs over one input."""
    first = scenario("a", [step("passed")])
    second = scenario("b", [step("failed")])
    rows = ag.build_tag_rows(
        {"@One": [first, second], "@Two": [second]},
        {"@One": "report-tag_1.html"},
    )
    assert [row["name"] for row in rows] == ["@One", "@Two"]
    assert rows[0]["href"] == "report-tag_1.html"
    assert rows[1]["href"] == ""
    assert rows[0]["scenarios_total"] == 2
    assert rows[0]["scenarios_failed"] == 1


def test_build_row_totals_sums_the_rows_and_counts_its_subjects() -> None:
    """The footer's last two cells are "how many subjects, how many passed"."""
    rows = [
        ag.stats_row("A", [scenario("a", [step("passed", 1)])]),
        ag.stats_row("B", [scenario("b", [step("failed", 2)])]),
    ]
    totals = ag.build_row_totals(rows)
    assert totals["steps_total"] == 2
    assert totals["steps_passed"] == 1
    assert totals["steps_failed"] == 1
    assert totals["duration_ns"] == 3
    assert totals["features"] == 2
    assert totals["features_passed"] == 1
    assert ag.build_row_totals([])["features"] == 0


# --------------------------------------------------------------------------- #
# The summary: the body ``GET /reports/summary`` answers with
# --------------------------------------------------------------------------- #


def test_build_summary_counts_steps_scenarios_and_features_by_their_own_rules() -> None:
    """Backgrounds count towards steps and features, never towards scenarios."""
    features = [
        feature(
            "A",
            [
                background([step("passed")]),
                scenario("ok", [step("passed")], started="2026-01-02T03:04:05.678Z"),
                background([]),
                scenario("bad", [step("undefined")], started="2026-01-02T03:04:00.000Z"),
            ],
        )
    ]
    summary = ag.build_summary(features)
    assert summary["steps"]["total"] == 3
    assert summary["scenarios"]["total"] == 2
    assert summary["scenarios"]["by_status"] == {"passed": 1, "undefined": 1}
    assert summary["features"]["by_status"] == {"undefined": 1}
    assert summary["start_timestamp"] == "2026-01-02T03:04:00.000Z"


def test_build_summary_emits_each_group_in_both_shapes() -> None:
    """The route reads the nested map and the metadata block reads the flat one."""
    summary = ag.build_summary([feature("A", [scenario("s", [step("passed")])])])
    group = summary["scenarios"]
    assert group[ag.SUMMARY_TOTAL_KEY] == 1
    assert group[ag.SUMMARY_BY_STATUS_KEY] == {"passed": 1}
    assert group["passed"] == 1


def test_build_summary_answers_zeros_for_an_empty_run() -> None:
    """A run that selected nothing still has all four artifacts written."""
    summary = ag.build_summary([])
    for name in ag.SUMMARY_GROUPS:
        assert summary[name] == {ag.SUMMARY_TOTAL_KEY: 0, ag.SUMMARY_BY_STATUS_KEY: {}}
    assert summary[ag.SUMMARY_START_KEY] is None


def test_a_step_less_scenario_counts_as_passed_rather_than_unknown() -> None:
    """The divergence between the writer and the summary route, settled once."""
    summary = ag.build_summary([feature("A", [scenario("empty", [])])])
    assert summary["scenarios"]["by_status"] == {"passed": 1}
    assert summary["features"]["by_status"] == {"passed": 1}


def test_build_summary_counts_a_background_only_failure_as_a_failed_scenario() -> None:
    """The "skipped in summary" half of the four-way disagreement, settled.

    The scenario's own body holds nothing but skipped steps -- behave skips a
    scenario's steps once its Background has failed -- so counting the element
    alone reported this run as one *skipped* scenario while the rerun manifest
    offered it for retry and the Pretty pages badged it failed.  The scenario
    is now counted by its effective status, the unit's.  The step tally and
    the feature tally are unchanged: a Background genuinely runs once per
    scenario, so its steps count, and a feature's badge folds every element
    beneath it anyway.
    """
    features = [
        feature(
            "A",
            [
                background([step("failed")]),
                scenario("blocked", [step("skipped")]),
                background([step("passed")]),
                scenario("ok", [step("passed")]),
            ],
        )
    ]

    summary = ag.build_summary(features)

    assert summary["scenarios"]["total"] == 2
    assert summary["scenarios"]["by_status"] == {"passed": 1, "failed": 1}
    assert summary["steps"]["total"] == 4
    assert summary["features"]["by_status"] == {"failed": 1}
    # The same figures come out of the decorated document, which is what the
    # writers hand it, and they equal the ``effective_status`` on the element.
    decorated = ag.decorate_feature(features[0])
    assert decorated["elements"][1][ag.EFFECTIVE_STATUS_KEY] == "failed"
    assert ag.build_summary([decorated])["scenarios"] == summary["scenarios"]


def test_a_failed_hook_reaches_the_summary_too() -> None:
    """Hooks are counted on every surface or on none; they are counted."""
    features = [
        feature("A", [scenario("s", [step("passed")], after=[hook("failed")])])
    ]
    summary = ag.build_summary(features)
    assert summary["scenarios"]["by_status"] == {"failed": 1}
    assert summary["steps"]["by_status"] == {"passed": 1}


# --------------------------------------------------------------------------- #
# Decoration and the run aggregate
# --------------------------------------------------------------------------- #


def test_decoration_copies_and_fills_in_every_level() -> None:
    """The writers share one document, so nothing may be edited in place."""
    element = scenario("s", [step("failed", 4)])
    source = feature("A", [element])
    decorated = ag.decorate_feature(source)
    assert decorated["status"] == "failed"
    assert decorated["verdict"] == ag.VERDICT_FAILED
    assert decorated["duration_ns"] == 4
    assert decorated["duration_samples"] == 1
    assert decorated["stats"]["scenarios_failed"] == 1
    assert decorated["elements"][0]["status"] == "failed"
    assert decorated["elements"][0]["stats"]["steps_failed"] == 1
    # the originals gained nothing
    assert "status" not in source
    assert "status" not in element


def test_decoration_writes_every_key_a_surface_reads() -> None:
    """A template formats decorated values; it never derives one.

    The five status keys are asserted together because a surface that finds one
    missing goes back to deriving it from the steps, which is how the same run
    came to be graded differently on different pages.  ``steps_status`` is the
    steps-only reading, ``status``/``verdict`` the element's own two readings,
    and ``effective_status``/``effective_verdict`` the unit's.
    """
    decorated = ag.decorate_feature(
        feature(
            "A",
            [
                background([step("failed", 3)]),
                scenario("blocked", [step("skipped")], after=[hook("passed")]),
            ],
        )
    )
    occurrence, blocked = decorated["elements"]

    for element in (occurrence, blocked):
        for key in (
            "status",
            ag.STEPS_STATUS_KEY,
            "verdict",
            ag.EFFECTIVE_STATUS_KEY,
            ag.EFFECTIVE_VERDICT_KEY,
            "duration_ns",
            "duration_samples",
            "stats",
        ):
            assert key in element, key

    # A Background occurrence carries its own reading in the same keys, so a
    # consumer never has to ask which kind of element it is holding.
    assert occurrence[ag.EFFECTIVE_STATUS_KEY] == occurrence["status"] == "failed"
    assert blocked["status"] == blocked[ag.STEPS_STATUS_KEY] == "skipped"
    assert blocked[ag.EFFECTIVE_STATUS_KEY] == "failed"
    assert blocked[ag.EFFECTIVE_VERDICT_KEY] == ag.VERDICT_FAILED
    # Decorated on its own, an element is its own unit.
    alone = ag.decorate_element(scenario("s", [step("skipped")]))
    assert alone[ag.EFFECTIVE_STATUS_KEY] == alone["status"] == "skipped"


def test_an_elements_own_figures_agree_with_its_effective_verdict() -> None:
    """A scenario's own statistics block cannot contradict its badge.

    The case only a foreign or hand-built document produces -- behave skips a
    scenario's steps once its Background has failed -- and the one the model
    has to be coherent about anyway: a scenario whose own body passed behind a
    failed Background counts as a **failed** scenario in its own figures, in
    its feature's, and in a tag row's, because all three read the effective
    verdict.
    """
    decorated = ag.decorate_feature(
        feature(
            "A",
            [background([step("failed")]), scenario("odd", [step("passed")])],
        )
    )
    odd = decorated["elements"][1]

    assert odd["status"] == "passed", "the element's own reading is unchanged"
    assert odd[ag.EFFECTIVE_VERDICT_KEY] == ag.VERDICT_FAILED
    assert odd["stats"]["scenarios_passed"] == 0
    assert odd["stats"]["scenarios_failed"] == 1
    assert decorated["stats"]["scenarios_failed"] == 1
    assert ag.tag_row("@Smoke", [odd])["scenarios_failed"] == 1


def test_decoration_canonicalises_statuses_in_the_copy_only() -> None:
    """The mapped status reaches the rendered page, and the input is untouched.

    Both HTML writers read ``step.result.status`` in their templates, so a
    behave-only status had to be folded in the copy or the page would badge
    *Unknown* what ``cucumber.json`` published as ``failed``.  Two properties
    are asserted with it: no key is *added* to a result -- a skipped step
    carries no ``duration`` in the artifact and must not acquire one here --
    and every other key survives in place.
    """
    element = scenario("s", [step("hook_error", 7), step("skipped")])
    element["after"] = [hook("cleanup_error")]
    source = feature("A", [element])
    before = copy.deepcopy(source)

    decorated = ag.decorate_feature(source)
    steps = decorated["elements"][0]["steps"]

    assert steps[0]["result"] == {"status": "failed", "duration": 7}
    assert steps[1]["result"] == {"status": "skipped"}
    assert "duration" not in steps[1]["result"]
    assert steps[0]["name"] == "a step" and steps[0]["line"] == 3
    assert decorated["elements"][0]["after"][0]["result"]["status"] == "failed"
    assert decorated["elements"][0]["status"] == "failed"
    # Nothing under the input document moved, including the raw statuses the
    # JSON writer still has to read for itself.
    assert source == before


def test_a_dry_run_is_read_once_and_reaches_every_decorated_value() -> None:
    """``normalize_run`` reads the flag; nothing downstream needs it.

    Measured: under ``dryRun`` the JVM marks a matched step ``passed`` and an
    unmatched one ``undefined``, while behave records ``untested`` for both --
    so one dry run published nineteen ``passed`` steps in ``cucumber.json``
    and sixty ``untested`` badges on the HTML artifact from the same document.
    The flag is read here, once, and written into the copies, which is what
    makes both artifacts and the manifest read the same run.
    """
    matched = {
        "keyword": "Given ",
        "name": "a matched step",
        "line": 3,
        "matched": True,
        "result": {"status": "untested"},
    }
    unmatched = {**matched, "name": "an unmatched step", "matched": False}
    document = {
        "dry_run": True,
        "features": [feature("A", [scenario("s", [matched, unmatched])])],
    }

    run = ag.normalize_run(document)
    steps = run.features[0]["elements"][0]["steps"]

    assert [step_["result"]["status"] for step_ in steps] == [
        "passed",
        ag.UNDEFINED_STATUS,
    ]
    assert run.features[0]["elements"][0][ag.EFFECTIVE_STATUS_KEY] == (
        ag.UNDEFINED_STATUS
    )
    assert run.summary["steps"]["by_status"] == {"passed": 1, "undefined": 1}
    assert run.summary["scenarios"]["by_status"] == {"undefined": 1}
    # Without the flag the same document reads as the engine recorded it.
    plain = ag.normalize_run({**document, "dry_run": False})
    assert plain.summary["steps"]["by_status"] == {"untested": 2}


def test_a_feature_with_no_element_did_not_pass_it_did_not_run() -> None:
    """The run-level empty answer is deliberately not a pass."""
    assert ag.feature_status(feature("A", [])) == ag.EMPTY_AGGREGATE_STATUS
    assert ag.decorate_feature(feature("A", []))["status"] == ag.UNKNOWN_STATUS


def test_feature_status_and_feature_verdict_fold_over_every_element() -> None:
    """Both feature readings include the Background occurrences."""
    populated = feature(
        "A",
        [background([step("passed")]), scenario("s", [step("skipped")])],
    )
    assert ag.feature_status(populated) == "skipped"
    assert ag.feature_verdict(populated) == ag.VERDICT_FAILED
    passing = feature("B", [background([]), scenario("s", [step("passed")])])
    assert ag.feature_status(passing) == "passed"
    assert ag.feature_verdict(passing) == ag.VERDICT_PASSED


def test_an_element_without_a_type_is_read_by_its_keyword() -> None:
    """A hand-built document that carries no type still discriminates."""
    assert ag.is_background({"keyword": "Background", "steps": []}) is True
    assert ag.is_background({"keyword": "Scenario", "steps": []}) is False
    # ... and an element of an unexpected type is rendered but never counted
    assert ag.is_scenario_element({"type": "example", "steps": []}) is False


def test_display_timestamp_renders_the_document_verbatim() -> None:
    """Reformatting it would let the two forms on a page disagree."""
    assert ag.display_timestamp("  2026-01-02T03:04:05.678Z ") == (
        "2026-01-02T03:04:05.678Z"
    )
    assert ag.display_timestamp(None) == ""


def test_normalize_run_produces_one_aggregate_every_surface_can_read() -> None:
    """Selection, decoration, the tally and the rows, from one call."""
    document = {
        "features": [
            feature(
                "Crm",
                [
                    background([step("passed", 1)]),
                    scenario("ok", [step("passed", 2)], started="2026-01-02T03:04:05.678Z"),
                    scenario("gone", [step("passed", 4)], selected=False),
                ],
            )
        ]
    }
    aggregate = ag.normalize_run(
        document, {"file:features/Crm.feature": "report-feature_1.html"}
    )
    assert len(aggregate.features) == 1
    assert [element["name"] for element in aggregate.features[0]["elements"]] == [
        "",
        "ok",
    ]
    row = aggregate.feature_rows[0]
    assert row["name"] == "Crm"
    assert row["href"] == "report-feature_1.html"
    assert row["steps_total"] == 2
    assert row["scenarios_total"] == 1
    assert row["duration_ns"] == 3
    assert aggregate.totals["features"] == 1
    assert aggregate.totals["features_passed"] == 1
    assert aggregate.summary["scenarios"]["total"] == 1
    assert aggregate.start_timestamp == "2026-01-02T03:04:05.678Z"


def test_normalize_run_links_a_feature_by_path_when_it_has_no_uri_entry() -> None:
    """Both keys the surfaces hold resolve to one page name."""
    document = {"features": [feature("Crm", [scenario("s", [step("passed")])])]}
    aggregate = ag.normalize_run(
        document, {"features/Crm.feature": "report-feature_1.html"}
    )
    assert aggregate.feature_rows[0]["href"] == "report-feature_1.html"


def test_normalize_run_answers_an_empty_aggregate_for_a_run_with_no_results() -> None:
    """All four artifacts are written even then, so this cannot raise."""
    aggregate = ag.normalize_run(None)
    assert aggregate.features == ()
    assert aggregate.feature_rows == ()
    assert aggregate.start_timestamp is None
    assert aggregate.totals["steps_total"] == 0
    assert aggregate.summary["features"][ag.SUMMARY_TOTAL_KEY] == 0


def test_the_aggregate_is_immutable() -> None:
    """It describes a run that has already happened."""
    aggregate = ag.normalize_run(None)
    try:
        aggregate.summary = {}  # type: ignore[misc]
    except Exception as exc:  # noqa: BLE001 - the exception type is the assertion
        assert type(exc).__name__ == "FrozenInstanceError"
    else:  # pragma: no cover - a mutable aggregate is the failure being guarded
        raise AssertionError("RunAggregate accepted an assignment")


# --------------------------------------------------------------------------- #
# The real fixture: the authority counts exactly what the surfaces show
# --------------------------------------------------------------------------- #


def test_the_tally_counts_what_the_pages_render(sample_result_set: Any) -> None:
    """Over the committed merged result set, not a hand-built one."""
    aggregate = ag.normalize_run(sample_result_set)
    rendered_steps = sum(
        len(ag.mappings(element.get("steps")))
        for feature_map in aggregate.features
        for element in ag.mappings(feature_map.get("elements"))
    )
    assert aggregate.summary["steps"]["total"] == rendered_steps
    assert aggregate.summary["scenarios"]["total"] == aggregate.totals["scenarios_total"]
    assert len(aggregate.feature_rows) == len(aggregate.features)
    for row in aggregate.feature_rows:
        assert row["scenarios_passed"] + row["scenarios_failed"] == row["scenarios_total"]


def test_folding_in_two_stages_equals_folding_at_once(sample_result_set: Any) -> None:
    """A maximum over a total order is associative, and the figures rely on it."""
    for feature_map in ag.normalize_run(sample_result_set).features:
        elements = ag.mappings(feature_map.get("elements"))
        at_once = ag.roll_up_status(
            [
                token
                for element in elements
                for token in ag.step_statuses(element) + ag.hook_statuses(element)
            ],
            empty=ag.UNKNOWN_STATUS,
        )
        assert feature_map["status"] == at_once
