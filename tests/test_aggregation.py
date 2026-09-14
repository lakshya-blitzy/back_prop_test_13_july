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

from typing import Any

from app.reporting import aggregation as ag

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
    """A status is spelled one way, and an unrecognised one is not a pass."""
    assert ag.status_token("Passed") == "passed"
    assert ag.status_token("  FAILED  ") == "failed"
    assert ag.status_token("executing") == ag.UNKNOWN_STATUS
    assert ag.status_token(None) == ag.UNKNOWN_STATUS
    assert ag.status_token(7) == ag.UNKNOWN_STATUS


def test_roll_up_status_follows_the_declared_precedence() -> None:
    """One failure is never averaged away by the passes around it."""
    assert ag.roll_up_status(["passed", "skipped", "failed"]) == "failed"
    assert ag.roll_up_status(["passed", "undefined", "pending"]) == "undefined"
    assert ag.roll_up_status(["passed", "ambiguous", "pending"]) == "ambiguous"
    assert ag.roll_up_status(["passed", "untested"]) == "untested"
    assert ag.roll_up_status(["passed", "passed"]) == "passed"


def test_roll_up_status_keeps_the_two_empty_cases_apart() -> None:
    """An empty collection and an unrecognised one answer differently."""
    assert ag.roll_up_status([]) == ag.EMPTY_ELEMENT_STATUS
    assert ag.roll_up_status([], empty=ag.EMPTY_AGGREGATE_STATUS) == ag.UNKNOWN_STATUS
    assert ag.roll_up_status(["executing"], empty=ag.UNKNOWN_STATUS) == ag.UNKNOWN_STATUS


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
