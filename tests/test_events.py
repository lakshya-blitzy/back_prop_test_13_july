"""Tests for the internal result schema and the behave event collector.

This module is the gate for ``app/reporting/events.py``, which is the single
place where behave's event stream is observed and the single definition of the
intermediate document all four artifact writers consume.  AAP 0.6 states the
consequence plainly -- *"No mapping can recover a field that was never
captured"* -- and names what this module has to prove: that *every field the
four writers read is present for each of a passing scenario, a failing scenario
with an attachment, a skipped step, an undefined step, a background and a
two-row outline*.  Each of those six shapes therefore gets its own separately
named test, so a failure names the shape rather than the suite.

What is asserted here, and where each value comes from
-----------------------------------------------------
Every pinned value is either measured from the committed reference artifact
(through :fixture:`sample_result_set`, the hand-built document in the port's
internal schema) or measured from the installed behave 1.3.3, never assumed:

* the ``match.location`` shape is AAP deviation 8 -- the resolved step
  function's dotted Python path, module plus qualified name, **no parentheses
  and no parameter types**, and never behave's own ``features/steps/x.py:LINE``;
* ``match.arguments`` offsets are the reference's 44, 54 and 63 for the outline
  step ``User can change any user's information like "Test2" , "30" and "2"``,
  which behave reports one character inside each quote;
* durations are nanosecond integers -- the reference's ``30202000000`` is
  30.202 seconds -- while behave reports float seconds;
* timestamps are the JVM generator's ``yyyy-MM-dd'T'HH:mm:ss.SSSXXX`` under
  UTC, i.e. exactly ``YYYY-MM-DDTHH:MM:SS.mmmZ``, truncated not rounded;
* the key-presence rules are the measured ones: a Background occurrence carries
  none of ``id``, ``start_timestamp``, ``tags`` or ``after``; an untagged
  scenario **omits** ``tags`` rather than carrying ``[]``; a feature always
  carries ``tags``, possibly empty.

The per-level key inventories are asserted as a loop over an explicit list of
keys, so a field a writer starts reading that the collector never records fails
here rather than in an artifact.

How the collector is driven
---------------------------
The real :class:`~app.reporting.events.ResultCollectorFormatter` is exercised
through behave's own callback protocol -- ``uri``, ``feature``, ``background``,
``scenario``, ``step``, ``match``, ``result``, ``eof``, ``close`` -- with the
lightweight model stand-ins defined below.  No browser, no feature file and no
behave command line is involved, and the callback order the helper
:func:`run_feature` reproduces was read out of behave's own ``model.py``:
``feature``, then ``background`` once per feature, then per scenario
``scenario``, every step of the scenario announced first (background steps
leading), then ``match`` immediately followed by ``result`` for each step that
actually executed.

Two seams the production module publishes are used instead of monkey-patching
it: :attr:`ResultCollectorFormatter.clock` pins time, and
:meth:`ResultCollectorFormatter.read_source_lines` supplies feature-file source
for description indentation and tag columns without touching disk.

Boundaries this module keeps
----------------------------
No network, no browser, no ``time.sleep`` and nothing written outside
``tmp_path``.  ``pytest.ini`` runs with ``--strict-config --strict-markers``, so
no custom marker is used.  ``tests/conftest.py`` owns ``sys.path`` and the three
pinned fixtures; this module reads them through their fixtures and edits
nothing.
"""

from __future__ import annotations

import ast
import base64
import copy
import itertools
import json
import logging
import re
import warnings
from collections.abc import Callable, Iterator, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Final

import pytest
from behave.formatter import _registry as behave_formatter_registry
from behave.formatter.api import IFormatter
from behave.formatter.base import Formatter, StreamOpener
from behave.matchers import Argument, Match, NoMatch
from behave.model import Tag
from behave.model_type import Status

from app.reporting import events
from app.utils import paths

# --------------------------------------------------------------------------
# Fixed names and measured values
#
# Everything a test compares against is named once here, so that a value which
# is measured from the reference is visibly distinct from one a test invented.
# --------------------------------------------------------------------------

#: The module under test, read as text by the import-hygiene assertion.
EVENTS_SOURCE_PATH: Final[Path] = Path(events.__file__)

#: The whole public surface, spelled out so that removing a name from
#: ``events.__all__`` -- every one of which has at least one consumer in
#: ``app/reporting``, ``app/services`` or ``features/environment.py`` -- fails
#: here instead of at the consumer's import.
PUBLIC_SURFACE: Final[frozenset[str]] = frozenset(
    {
        "BACKGROUND_KEYWORD",
        "DEFAULT_AFTER_HOOK_LOCATION",
        "ELEMENT_TYPE_BACKGROUND",
        "ELEMENT_TYPE_SCENARIO",
        "FEATURE_KEYWORD",
        "FORMATTER_NAME",
        "FORMATTER_SCOPED_NAME",
        "HOOK_FAILURE_MESSAGE",
        "RESULT_STATUSES",
        "SCHEMA_VERSION",
        "ResultCollectorFormatter",
        "ResultSetError",
        "attach_to_current_scenario",
        "convert_to_id",
        "dump_result_set",
        "feature_tag",
        "format_timestamp",
        "iter_scenarios",
        "load_result_set",
        "merge_result_sets",
        "nanos_from_seconds",
        "new_element",
        "new_feature",
        "new_hook_entry",
        "new_result_set",
        "new_step",
        "record_hook_result",
        "run_metadata",
        "scenario_element_id",
        "scenario_tag",
        "step_keyword",
        "widen_quoted_span",
    }
)

#: Every callback behave 1.3.3 itself invokes on a formatter, measured from
#: ``behave.formatter.api.IFormatter`` plus the two extras behave calls without
#: declaring them on that interface: ``close_stream`` (from
#: ``behave.formatter.base.Formatter``) and ``embedding``, which
#: ``behave.runner.Context.attach`` forwards to every formatter that has it.
#: The collector must define nothing outside this set, because a method behave
#: never calls would be dead weight pretending to be a hook.
BEHAVE_CALLBACKS: Final[frozenset[str]] = frozenset(
    {name for name, _ in IFormatter.__dict__.items() if not name.startswith("_")}
    | {"close_stream", "embedding", "description", "open"}
)

# -- Run-level, feature-level and element-level key inventories -------------
#
# Each list is what a consumer may read without a membership test.  They are
# looped over rather than compared as whole dictionaries so that a failure
# names the missing key.

RESULT_SET_KEYS: Final[tuple[str, ...]] = (
    "schema_version",
    "started_at",
    "generated_at",
    "dry_run",
    "tag_expression",
    "metadata",
    "features",
)

FEATURE_KEYS: Final[tuple[str, ...]] = (
    "uri",
    "path",
    "id",
    "keyword",
    "line",
    "name",
    "description",
    "tags",
    "elements",
)

#: The keys both kinds of element carry.
ELEMENT_SHARED_KEYS: Final[tuple[str, ...]] = (
    "type",
    "keyword",
    "line",
    "name",
    "description",
    "selected",
    "steps",
)

#: What a scenario element adds.  ``tags`` is deliberately absent: it is
#: present only when the scenario has tags, which is the measured rule.
SCENARIO_ONLY_KEYS: Final[tuple[str, ...]] = ("id", "start_timestamp", "after")

STEP_KEYS: Final[tuple[str, ...]] = (
    "keyword",
    "line",
    "name",
    "matched",
    "match",
    "result",
)

HOOK_ENTRY_KEYS: Final[tuple[str, ...]] = ("match", "result", "embeddings")

#: ``app/templates/artifact/metadata.html`` renders exactly these names, so the
#: vocabulary is fixed rather than descriptive.
METADATA_KEYS: Final[dict[str, tuple[str, ...]]] = {
    "implementation": ("name", "version"),
    "runtime": ("name", "version"),
    "os": ("name",),
    "cpu": ("name",),
}

# -- Measured values from the reference artifact ---------------------------

#: Feature paths carried by :fixture:`sample_result_set`.  The directory
#: component is taken from ``app.utils.paths.NORMALIZED_FEATURES_PREFIX``
#: rather than written out, because AAP 0.4.2 gives the feature-directory
#: prefix exactly one owner and requires that no test hard-code either form of
#: it -- only the filenames, which AAP deviation 1 preserved, are literals.
CONTACT_PATH: Final[str] = f"{paths.NORMALIZED_FEATURES_PREFIX}Contact.feature"
CRM_PATH: Final[str] = f"{paths.NORMALIZED_FEATURES_PREFIX}Crm.feature"
INVENTORY_PATH: Final[str] = f"{paths.NORMALIZED_FEATURES_PREFIX}Inventory.feature"
SALES_PATH: Final[str] = f"{paths.NORMALIZED_FEATURES_PREFIX}Sales.feature"

#: The fixture's measured census: 4 features, 10 scenarios, 4 Background
#: occurrences and 30 steps.
SAMPLE_FEATURE_COUNT: Final[int] = 4
SAMPLE_SCENARIO_COUNT: Final[int] = 10
SAMPLE_BACKGROUND_COUNT: Final[int] = 4
SAMPLE_STEP_COUNT: Final[int] = 30

CRM_FEATURE_NAME: Final[str] = "Testinium app CRM Module"
CRM_FEATURE_ID: Final[str] = "testinium-app-crm-module"

#: Element lines inside ``Crm.feature``, from the reference report.
CRM_PASSING_SCENARIO_LINE: Final[int] = 9
CRM_FAILING_SCENARIO_LINE: Final[int] = 16
CRM_OUTLINE_FIRST_ROW_LINE: Final[int] = 24
CRM_OUTLINE_SECOND_ROW_LINE: Final[int] = 25
CRM_BACKGROUND_LINE: Final[int] = 6

#: Element lines inside ``Sales.feature``.
SALES_UNDEFINED_SCENARIO_LINE: Final[int] = 12
SALES_EXCLUDED_SCENARIO_LINE: Final[int] = 36

#: The golden outline-row id: ``Examples: Expected name``'s first data row,
#: whose position counts the header row as 1 (AAP 0.6).
GOLDEN_ROW_ELEMENT_ID: Final[str] = (
    "testinium-app-crm-module;user-can-change-information-in-dashboard;"
    "expected-name;2"
)

#: The reference's parameterized step name and the offsets it records.  behave
#: reports the spans one character inside each quote; the recorded offsets are
#: widened over the quotes, which is what the JVM emits.
GOLDEN_STEP_NAME: Final[str] = (
    "User can change any user's information like \"Test2\" , \"30\" and \"2\""
)
GOLDEN_BEHAVE_SPANS: Final[tuple[tuple[int, int], ...]] = ((45, 50), (55, 57), (64, 65))
GOLDEN_ARGUMENT_VALUES: Final[tuple[str, ...]] = ('"Test2"', '"30"', '"2"')
GOLDEN_ARGUMENT_OFFSETS: Final[tuple[int, ...]] = (44, 54, 63)

#: 30.202 seconds, as the reference carries it.
GOLDEN_DURATION_NANOS: Final[int] = 30202000000
GOLDEN_DURATION_SECONDS: Final[float] = 30.202

#: The instant the reference report's first scenario started.
GOLDEN_TIMESTAMP: Final[str] = "2022-09-07T13:37:26.297Z"

#: Its source instant, carrying 297_999 microseconds so that a formatter which
#: rounded rather than truncated would emit ``.298`` and fail.
GOLDEN_INSTANT: Final[datetime] = datetime(
    2022, 9, 7, 13, 37, 26, 297_999, tzinfo=timezone.utc
)

#: A schema version this build does not speak, used by the load and merge
#: tests.  Chosen far from :data:`app.reporting.events.SCHEMA_VERSION` so that
#: the assertions below cannot pass by coincidence if the real version is
#: bumped.
FOREIGN_SCHEMA_VERSION: Final[int] = 99

#: The ten characters ``TestSourcesModel.convertToId`` replaces with ``-``:
#: Java's ``[\s'_,!]``, where ``\s`` is exactly ``[ \t\n\x0B\f\r]``.
ID_REPLACED_CHARACTERS: Final[tuple[str, ...]] = (
    " ",
    "\t",
    "\n",
    "\x0b",
    "\f",
    "\r",
    "'",
    "_",
    ",",
    "!",
)

#: Characters the JVM leaves alone, which is why ``Sales.feature`` keeps its
#: leading dots and a quoted scenario name keeps its quotation marks.
ID_SURVIVING_CHARACTERS: Final[str] = '.:"()-'

#: A feature source path in the Java layout, which AAP deviation 1 moved.  It
#: is the *input* to a normalization assertion, so the legacy directory is read
#: from ``app.utils.paths.LEGACY_FEATURES_PREFIX`` rather than written out: the
#: substitution rule belongs to ``app.utils.paths.normalize_feature_uri``, this
#: module neither reimplements it nor names either prefix (AAP 0.4.2).
LEGACY_FEATURE_SOURCE: Final[str] = f"{paths.LEGACY_FEATURES_PREFIX}Crm.feature"

#: A 1x1 PNG, decoded from base64 so the bytes are real image bytes rather
#: than arbitrary noise, and fixed so the encoding assertion is exact.
SCREENSHOT_PNG: Final[bytes] = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/"
    "q842iQAAAABJRU5ErkJggg=="
)

#: MIME type of the port's only attachment (``Hooks.java:15``).
PNG_MIME_TYPE: Final[str] = "image/png"

#: Name of the file each collector in this module writes.  A worker's
#: intermediate is named by ``app.utils.paths.worker_result_path`` in
#: production; this module only needs *a* path under ``tmp_path``.
WORKER_FILE_NAME: Final[str] = "worker-results.json"


# --------------------------------------------------------------------------
# behave model stand-ins
#
# Duck-typed recorders carrying exactly the attributes the collector reads,
# named after the behave classes they stand in for.  Reading an attribute that
# was never set raises AttributeError, as it does on behave's own model, so a
# test cannot pass by misspelling one.
# --------------------------------------------------------------------------


class StubConfig:
    """Stands in for behave's configuration object.

    The collector reads ``dry_run`` and derives the effective tag expression
    from ``tags`` then ``default_tags``, each defensively, because behave's
    configuration surface has moved between releases.
    """

    def __init__(
        self,
        *,
        dry_run: bool = False,
        tags: Sequence[str] | str | None = None,
        default_tags: Sequence[str] | str | None = None,
    ) -> None:
        """Build a configuration.

        :param dry_run: Mirrored into the document's ``dry_run``.
        :param tags: ``--tags`` as behave delivers it: a list of expressions.
        :param default_tags: ``behave.ini``'s ``default_tags``, which applies
            only when the command line supplied no filter.
        """
        self.dry_run = dry_run
        self.tags = [] if tags is None else tags
        self.default_tags = [] if default_tags is None else default_tags


class StubStep:
    """Stands in for a behave step, before and after execution.

    ``executed`` is the one attribute behave has no analogue for: it records
    whether the runner reached this step at all, which decides whether the
    ``match``/``result`` callback pair is emitted for it.  A step behave never
    executes -- everything after a failure in the same scenario, and every step
    of a scenario the tag expression excluded -- gets neither callback, and the
    collector has to recover its outcome from the step object itself.
    """

    def __init__(
        self,
        keyword: str,
        name: str,
        line: int,
        *,
        status: Any = None,
        duration: float | None = None,
        error_message: str | None = None,
        func: Callable[..., Any] | None = None,
        arguments: Sequence[Argument] = (),
        executed: bool = True,
        step_type: str | None = None,
    ) -> None:
        """Build a step.

        :param keyword: Gherkin keyword without its trailing space.
        :param name: The step text, outline placeholders already substituted.
        :param line: The step's line -- for an outline row, the template's.
        :param status: behave's status at the point the collector reads it.
        :param duration: behave's float-second duration.
        :param error_message: behave's failure text, if any.
        :param func: The resolved step function, or ``None`` for an undefined
            step, which behave reports with ``NoMatch``.
        :param arguments: behave ``Argument`` instances for a parameterized
            step.
        :param executed: Whether the runner ran the step.
        :param step_type: behave's effective step type, used only by the step
            registry lookup for a never-executed step.
        """
        self.keyword = keyword
        self.name = name
        self.line = line
        self.status = status
        self.duration = duration
        self.error_message = error_message
        self.func = func
        self.arguments = list(arguments)
        self.executed = executed
        self.step_type = step_type or keyword.strip().lower()


class StubBackground:
    """Stands in for behave's Background, which is announced once per feature."""

    def __init__(
        self,
        *,
        keyword: str = "Background",
        line: int = CRM_BACKGROUND_LINE,
        name: str = "",
        description: Sequence[str] = (),
        steps: Sequence[StubStep] = (),
    ) -> None:
        """Build a Background definition.

        :param keyword: The localised Gherkin keyword.
        :param line: The ``Background:`` line.
        :param name: Its name, commonly empty.
        :param description: behave's stripped description lines.
        :param steps: Its steps, repeated into every scenario's step sequence.
        """
        self.keyword = keyword
        self.line = line
        self.name = name
        self.description = list(description)
        self.steps = list(steps)


class StubRow:
    """Stands in for an Examples table row of a scenario outline.

    behave sets ``index`` to the one-based position of the row within the
    block's body and ``id`` to ``"<block>.<row>"``; both are measured from
    ``behave/model.py``, where the row id is built as ``"%d.%d"``.
    """

    def __init__(self, index: int | None, identifier: str | None) -> None:
        """Build a row.

        :param index: behave's one-based body index, or ``None`` to force the
            collector down its annotation-parsing fallback.
        :param identifier: behave's ``row.id``, or ``None`` for the same
            reason.
        """
        self.index = index
        self.id = identifier


class StubExamples:
    """Stands in for an ``Examples:`` block."""

    def __init__(self, index: int, name: str) -> None:
        """Build an Examples block.

        :param index: behave's one-based block index.
        :param name: The block's name; ``""`` for an unnamed block.
        """
        self.index = index
        self.name = name


class StubOutline:
    """Stands in for the scenario outline a generated row scenario points at."""

    def __init__(self, examples: Sequence[StubExamples]) -> None:
        """Build an outline.

        :param examples: Its Examples blocks, in source order.
        """
        self.examples = list(examples)


class StubScenario:
    """Stands in for a behave scenario, plain or generated from an outline."""

    def __init__(
        self,
        name: str,
        line: int,
        *,
        keyword: str = "Scenario",
        tags: Sequence[Tag] = (),
        steps: Sequence[StubStep] = (),
        background_steps: Sequence[StubStep] = (),
        description: Sequence[str] = (),
        selected: bool = True,
        selection_error: BaseException | None = None,
        row: StubRow | None = None,
        parent: StubOutline | None = None,
        background: StubBackground | None = None,
    ) -> None:
        """Build a scenario.

        :param name: Its name -- for a generated row, carrying behave's
            ``" -- @<row.id> <examples.name>"`` annotation.
        :param line: Its line; the data row's line for a generated row.
        :param keyword: ``"Scenario"`` or ``"Scenario Outline"``.
        :param tags: Its own tags; the feature's are propagated by the
            collector rather than repeated here.
        :param steps: Its own steps.
        :param background_steps: The Background's steps as behave repeats them
            into this scenario, which is what decides where the announced
            steps are split.
        :param description: behave's stripped description lines.
        :param selected: What ``should_run`` answers.
        :param selection_error: Raised by ``should_run`` instead of answering,
            to drive the collector's "assume selected" fallback.
        :param row: The Examples row, for a generated row scenario.
        :param parent: The originating outline, whose ``examples`` the
            collector prefers over the annotation suffix.
        :param background: A per-scenario Background, which behave sets on a
            generated row scenario.
        """
        self.name = name
        self.line = line
        self.keyword = keyword
        self.tags = list(tags)
        self.steps = list(steps)
        self.description = list(description)
        self.background = background
        self._background_steps = list(background_steps)
        self._selected = selected
        self._selection_error = selection_error
        self._row = row
        self.parent = parent

    @property
    def background_steps(self) -> list[StubStep]:
        """The Background's steps as behave repeats them into this scenario.

        A property rather than a plain attribute so that a subclass can make
        reading it fail, which is the one way to reach the collector's
        "background step count unavailable" branch.

        :returns: The steps.
        """
        return list(self._background_steps)

    def should_run(self, config: Any) -> bool:
        """Answer whether the effective tag expression selected this scenario.

        :param config: behave's configuration, which the real model consults.
        :returns: The programmed answer.
        :raises BaseException: The programmed ``selection_error``, if any.
        """
        if self._selection_error is not None:
            raise self._selection_error
        return self._selected

    @property
    def all_steps(self) -> list[StubStep]:
        """Background steps then the scenario's own, as behave announces them.

        :returns: The flat announcement sequence.
        """
        return [*self._background_steps, *self.steps]


class StubFeature:
    """Stands in for a behave feature."""

    def __init__(
        self,
        name: str,
        filename: str,
        *,
        line: int = 2,
        keyword: str = "Feature",
        tags: Sequence[Tag] = (),
        description: Sequence[str] | str = (),
        background: StubBackground | None = None,
    ) -> None:
        """Build a feature.

        :param name: Its name, which the feature id is slugged from.
        :param filename: behave's feature filename, relative to the working
            directory.
        :param line: The ``Feature:`` line -- 2 in ``Crm.feature``, whose
            first line is the ``@Smoke`` tag.
        :param keyword: The Gherkin keyword.
        :param tags: behave ``Tag`` instances, each carrying its line.
        :param description: behave's stripped description lines, or a plain
            string, which behave also permits.
        :param background: Its Background definition, or ``None``.
        """
        self.name = name
        self.filename = filename
        self.line = line
        self.keyword = keyword
        self.tags = list(tags)
        self.description = description if isinstance(description, str) else list(
            description
        )
        self.background = background


class StubStepRegistry:
    """Stands in for behave's process-wide step registry.

    Substituted for the real registry with ``monkeypatch`` wherever a
    never-executed step's definition has to be resolved, so that the assertion
    does not depend on whether some other test module has loaded the port's 91
    step definitions into the real one.
    """

    def __init__(
        self,
        *,
        match: Match | None = None,
        error: BaseException | None = None,
    ) -> None:
        """Build a registry stand-in.

        :param match: What ``find_match`` returns.
        :param error: Raised by ``find_match`` instead of returning, to drive
            the collector's guarded lookup.
        """
        self._match = match
        self._error = error
        #: Every step ``find_match`` was asked about, in order.
        self.queried: list[StubStep] = []

    def find_match(self, step: StubStep) -> Match | None:
        """Resolve ``step`` the way behave's registry does.

        :param step: The step to resolve.
        :returns: The programmed match, or ``None`` for a genuinely undefined
            step.
        :raises BaseException: The programmed error, if any.
        """
        self.queried.append(step)
        if self._error is not None:
            raise self._error
        return self._match


class SteppedClock:
    """A deterministic clock, advancing by a fixed step on every reading.

    The collector reads a clock twice per scenario-bearing run -- once per
    scenario for ``start_timestamp`` and once at ``close`` for
    ``generated_at`` -- so a clock that advances makes both the per-scenario
    value and the run-level minimum assertable, which a frozen instant could
    not distinguish.
    """

    def __init__(
        self,
        start: datetime = GOLDEN_INSTANT,
        step: timedelta = timedelta(seconds=1),
    ) -> None:
        """Build a clock.

        :param start: The instant the first reading returns.
        :param step: How far each subsequent reading advances.
        """
        self._start = start
        self._step = step
        #: Every instant handed out, in order.
        self.readings: list[datetime] = []

    def __call__(self) -> datetime:
        """Return the next instant.

        :returns: ``start + step * readings_so_far``.
        """
        moment = self._start + self._step * len(self.readings)
        self.readings.append(moment)
        return moment


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def make_step_function(module_path: str, name: str) -> Callable[..., Any]:
    """Build a function that looks exactly like one of behave's step functions.

    This is not a convenience: behave does not *import* a step module, it
    ``exec``s it with a globals mapping carrying no ``__name__``, so a real step
    function has **no** ``__module__`` and the only record of where it came
    from is its code object's filename.  Reproducing that is the whole point --
    a function defined normally in this test module would exercise the easy
    half of ``match.location`` and leave the half that actually runs in
    production untested.

    :param module_path: The source path to compile under, e.g.
        ``"features/steps/crm_steps.py"``.
    :param name: The function's name.
    :returns: The function, whose ``__module__`` is ``None``.
    """
    namespace: dict[str, Any] = {}
    exec(  # noqa: S102 - reproducing behave's own step-module loading exactly
        compile(f"def {name}(context):\n    return None\n", module_path, "exec"),
        namespace,
    )
    return namespace[name]


def golden_arguments() -> list[Argument]:
    """behave ``Argument`` instances for the reference's parameterized step.

    The spans are behave's own -- one character inside each quotation mark --
    so that the offsets the collector records are produced by the widening
    rule rather than handed to it.

    :returns: Three arguments, left to right.
    """
    return [
        Argument(start, end, GOLDEN_STEP_NAME[start:end], GOLDEN_STEP_NAME[start:end])
        for start, end in GOLDEN_BEHAVE_SPANS
    ]


def run_feature(
    collector: events.ResultCollectorFormatter,
    feature: StubFeature,
    scenarios: Sequence[StubScenario],
    *,
    call_eof: bool = True,
) -> None:
    """Drive one feature through behave's callback protocol, in behave's order.

    The order is measured from ``behave/model.py`` rather than assumed:
    ``formatter.uri`` (``runner.py``), then ``formatter.feature``, then
    ``formatter.background`` once per feature when the feature has one, then
    per scenario ``formatter.scenario``, then **every** step of the scenario
    announced through ``formatter.step`` -- background steps first, as
    ``Scenario.all_steps`` orders them -- and only then ``formatter.match``
    immediately followed by ``formatter.result`` for each step that executed.
    A step that did not execute gets neither callback, which is exactly the
    gap in behave's protocol the collector closes at scenario end.

    :param collector: The formatter under test.
    :param feature: The feature to announce.
    :param scenarios: Its scenarios, in source order.
    :param call_eof: Whether to end the feature file with ``eof``; ``False``
        leaves the last scenario unfinalised, so that ``close`` has to finish
        it.
    """
    collector.uri(feature.filename)
    collector.feature(feature)
    if feature.background is not None:
        collector.background(feature.background)

    for scenario in scenarios:
        collector.scenario(scenario)
        for step in scenario.all_steps:
            collector.step(step)
        for step in scenario.all_steps:
            if not step.executed:
                continue
            if step.func is None:
                collector.match(NoMatch())
            else:
                collector.match(Match(step.func, step.arguments))
            collector.result(step)

    if call_eof:
        collector.eof()


def read_document(collector: events.ResultCollectorFormatter) -> dict[str, Any]:
    """Close the collector and read back the document it wrote.

    Closing here rather than in the test is deliberate: ``close`` is where the
    document is stamped and written, so reading the file is only meaningful
    after it, and every collector this module builds therefore ends up
    de-registered.

    :param collector: The formatter under test.
    :returns: The parsed document.
    """
    collector.close()
    path = Path(collector.stream_opener.name)
    return json.loads(path.read_text(encoding="utf-8"))


def feature_by_path(document: dict[str, Any], path: str) -> dict[str, Any]:
    """Return the one feature in ``document`` carrying ``path``.

    :param document: A result document.
    :param path: The feature's ``path``, which is its merge identity.
    :returns: The feature object.
    """
    matches = [
        feature for feature in document["features"] if feature.get("path") == path
    ]
    assert len(matches) == 1, f"expected exactly one feature at {path}"
    return matches[0]


def element_at_line(feature: dict[str, Any], line: int, kind: str) -> dict[str, Any]:
    """Return the one element of ``feature`` of ``kind`` at ``line``.

    :param feature: A feature object.
    :param line: The element's line.
    :param kind: ``"scenario"`` or ``"background"``.
    :returns: The element object.
    """
    matches = [
        element
        for element in feature["elements"]
        if element.get("line") == line and element.get("type") == kind
    ]
    assert len(matches) == 1, f"expected exactly one {kind} at line {line}"
    return matches[0]


def assert_keys(mapping: dict[str, Any], keys: Sequence[str], label: str) -> None:
    """Assert every key in ``keys`` is present in ``mapping``.

    A loop rather than a set comparison so that the failure names the one key
    that is missing, and a *presence* assertion rather than an equality one so
    that a key added later is not a failure in itself -- what the writers
    cannot survive is a key that disappears.

    :param mapping: The object to check.
    :param keys: The required keys.
    :param label: What the object is, for the failure message.
    """
    for key in keys:
        assert key in mapping, f"{label} is missing the required key {key!r}"


def imported_module_names(source_path: Path) -> set[str]:
    """Every module name imported by the Python file at ``source_path``.

    Parsed out of the source with :mod:`ast` rather than read off
    ``sys.modules``: by the time this suite runs, other test modules have
    imported Flask, selenium and the services into the same interpreter, so an
    interpreter-state check would prove nothing about *this* module's imports.

    :param source_path: The file to parse.
    :returns: Dotted names from ``import x.y`` and ``from x.y import z``,
        including the relative-import case, which resolves to no module name
        and is therefore recorded as written.
    """
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
    return names


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture(name="make_collector")
def _make_collector(
    tmp_path: Path,
) -> Iterator[Callable[..., events.ResultCollectorFormatter]]:
    """A factory for collectors writing under ``tmp_path``.

    Every collector it builds is closed at teardown, whether or not the test
    closed it.  That is not tidiness: ``app/reporting/events.py`` keeps a
    module-level list of live collectors so that
    :func:`~app.reporting.events.attach_to_current_scenario` can find them, and
    a collector left open would leak into the next test's attachment
    assertions.  The teardown also asserts the list came back empty, which is
    the de-registration contract ``close`` documents.

    The output stream is opened here and handed to the stream opener
    pre-opened, which is one of the two modes ``behave.formatter.base``
    documents.  The reason is specific: behave 1.3.3's ``StreamOpener.open()``
    calls ``codecs.open()``, which Python 3.13 deprecated, so letting behave
    open the file would raise a ``DeprecationWarning`` from behave's code in
    every test here.  One test -
    :func:`test_collector_opens_its_stream_through_behaves_own_opener` -
    exercises that route deliberately and contains the warning locally, so the
    filename mode is still covered exactly once rather than suppressed
    project-wide.

    :param tmp_path: pytest's per-test directory - the only place this module
        writes.
    :yields: The factory.
    """
    built: list[events.ResultCollectorFormatter] = []
    streams: list[Any] = []

    def factory(
        *,
        dry_run: bool = False,
        tags: Sequence[str] | str | None = None,
        default_tags: Sequence[str] | str | None = None,
        name: str = WORKER_FILE_NAME,
        clock: Callable[[], datetime] | None = None,
        source_lines: Sequence[str] | None = None,
        encoding: str = "utf-8",
    ) -> events.ResultCollectorFormatter:
        """Build one collector.

        :param dry_run: Mirrored into the document.
        :param tags: ``--tags`` as behave delivers it.
        :param default_tags: ``behave.ini``'s ``default_tags``.
        :param name: File name under ``tmp_path`` to write to.
        :param clock: Instance clock, the documented time-injection seam.
            Defaults to a :class:`SteppedClock`, so no test depends on the
            wall clock.
        :param source_lines: Lines :meth:`read_source_lines` returns, which
            replaces every file read the collector would perform.
        :param encoding: Encoding of the output stream; ``"ascii"`` drives the
            collector's encoding-failure fallback.
        :returns: The collector, already registered and with its stream open.
        """
        destination = tmp_path / name
        # newline="\n" so the document is byte-identical whatever platform
        # wrote it, which is the same convention ``dump_result_set`` applies.
        stream = destination.open("w", encoding=encoding, newline="\n")
        streams.append(stream)
        opener = StreamOpener(filename=str(destination), stream=stream)
        collector = events.ResultCollectorFormatter(
            opener,
            StubConfig(dry_run=dry_run, tags=tags, default_tags=default_tags),
        )
        built.append(collector)
        collector.clock = clock if clock is not None else SteppedClock()
        if source_lines is not None:
            lines = list(source_lines)
            collector.read_source_lines = lambda _filename: lines  # type: ignore[method-assign]
        return collector

    try:
        yield factory
    finally:
        for collector in built:
            collector.close()
        # A pre-opened stream is the caller's to close: StreamOpener skips
        # closing one it did not open, and an unclosed file would surface as a
        # ResourceWarning attributed to whichever test ran next.
        for stream in streams:
            if not stream.closed:
                stream.close()
        for collector in built:
            assert collector not in events._ACTIVE_COLLECTORS, (
                "close() must de-register the collector"
            )


@pytest.fixture(name="crm_feature")
def _crm_feature() -> StubFeature:
    """``Crm.feature`` as behave would announce it: tagged, with a Background.

    The reference report's own feature, reproduced because every measured value
    in this module - the feature id, the tag location, the element
    interleaving, the outline row id - was taken from its artifact.

    :returns: The feature stand-in.
    """
    return StubFeature(
        CRM_FEATURE_NAME,
        CRM_PATH,
        line=2,
        tags=[Tag("Smoke", 1)],
        description=["Account is: PosManager"],
        background=StubBackground(name="As a Posmanager"),
    )


# ==========================================================================
# Formatter identity and addressability
# ==========================================================================


def test_scoped_name_resolves_to_the_collector_class() -> None:
    """behave must be able to load the formatter from its scoped name alone.

    ``app/services/test_run_service.py`` passes
    ``-f app.reporting.events:ResultCollectorFormatter`` on every worker's
    command line, because ``behave.ini`` can name no per-worker output path.
    If this failed, no worker would produce a document at all - and it would
    fail at run time in a subprocess, where the traceback is hardest to see.
    """
    module_name, class_name = behave_formatter_registry.parse_scoped_name(
        events.FORMATTER_SCOPED_NAME
    )

    assert module_name == events.__name__
    assert class_name == events.ResultCollectorFormatter.__name__
    loaded = behave_formatter_registry.load_formatter_class(
        events.FORMATTER_SCOPED_NAME
    )
    assert loaded is events.ResultCollectorFormatter


def test_collector_declares_its_short_name_and_description() -> None:
    """The class carries behave's two registration attributes.

    ``name`` is what a ``-f resultcollector`` invocation would resolve once
    registered, and behave lists ``description`` in ``--format-help``; an empty
    description would make the formatter undiscoverable there.
    """
    assert events.ResultCollectorFormatter.name == events.FORMATTER_NAME
    assert events.FORMATTER_NAME == "resultcollector"
    description = events.ResultCollectorFormatter.description
    assert isinstance(description, str)
    assert description.strip()
    assert issubclass(events.ResultCollectorFormatter, Formatter)


def test_collector_defines_only_callbacks_behave_invokes() -> None:
    """Every hook the class defines must be one behave actually calls.

    behave 1.3.3's callback set is fixed: ``uri``, ``feature``, ``background``,
    ``scenario``, ``step``, ``match``, ``result``, ``eof``, ``rule``,
    ``close``, ``close_stream``, ``description`` and - through
    ``Context.attach`` - ``embedding``.  A public method outside that set would
    never be invoked, so a document depending on it would silently lose data.

    The class does carry public members that are *not* callbacks, and they are
    enumerated below rather than tolerated by a subset test: a seam the class
    publishes on purpose has a caller in this port, and one that appeared by
    accident has none.  Naming them is what keeps this assertion able to fail
    in both directions - an unannounced hook is caught, and a seam that
    vanished from under its callers is caught too.
    """
    defined = {
        name
        for name, value in vars(events.ResultCollectorFormatter).items()
        if not name.startswith("_") and callable(value)
    }
    # Five members are the class's own published seams rather than behave
    # hooks, and each is named here with the reason it is not a callback:
    #
    # * ``add_attachment`` is the entry point ``attach_to_current_scenario``
    #   calls, and ``record_hook_result`` the one the module-level
    #   :func:`app.reporting.events.record_hook_result` calls.  Both exist
    #   because ``features/environment.py`` owns the scenario lifecycle and the
    #   plan's dependency graph keeps it free of any import from this package,
    #   so a hook's own status, duration and error text -- which behave never
    #   reports to a formatter -- reach the document through a deliberate seam
    #   instead of through a hook behave would have to invoke;
    # * ``read_source_lines`` is the single file-read seam a test overrides;
    # * ``clock`` and ``monotonic`` are the two documented time-injection
    #   seams, both staticmethods and hence callable.  They are separate
    #   because they measure different things: a wall-clock instant that goes
    #   into the document as text, and an interval that must not move if the
    #   system clock is stepped mid-run.
    #
    # The rule the assertion below states is therefore "every public member is
    # either a behave callback or a named seam", which is what keeps a method
    # behave never calls from arriving unannounced.
    published_seams = {
        "add_attachment",
        "record_hook_result",
        "read_source_lines",
        "clock",
        "monotonic",
    }
    assert published_seams <= defined, (
        f"a published seam disappeared: {sorted(published_seams - defined)}"
    )
    protocol_methods = defined - published_seams

    assert protocol_methods <= BEHAVE_CALLBACKS, (
        f"not a behave callback: {sorted(protocol_methods - BEHAVE_CALLBACKS)}"
    )
    # behave.runner.Context.attach forwards to every formatter that has an
    # ``embedding`` attribute, which is the whole of how features/environment.py
    # gets a screenshot into the document without importing this module.
    assert hasattr(events.ResultCollectorFormatter, "embedding")


def test_module_exports_exactly_its_documented_surface() -> None:
    """``__all__`` is the contract every consumer imports through.

    Complete and duplicate-free: a name dropped from it stays importable by
    accident today and breaks a consumer the day the module is tidied.  The
    surface is spelled out in :data:`PUBLIC_SURFACE` rather than derived from
    the module, so that this assertion can fail in both directions.
    """
    assert set(events.__all__) == PUBLIC_SURFACE
    assert len(events.__all__) == len(set(events.__all__))
    for name in events.__all__:
        assert hasattr(events, name), f"{name} is exported but does not exist"


def test_schema_version_matches_the_sample_fixture(
    sample_result_set: dict[str, Any],
) -> None:
    """The constant and the pinned fixture must not drift apart.

    Every writer test reads ``tests/fixtures/sample_results.json``.  If the
    schema version were bumped without the fixture, those tests would be
    asserting against a document shape no longer produced - and the fixture is
    pinned, so the constant is what a bump has to reckon with.
    """
    assert isinstance(events.SCHEMA_VERSION, int)
    assert events.SCHEMA_VERSION == sample_result_set["schema_version"]


def test_fixed_vocabulary_constants_are_the_measured_values() -> None:
    """The element types, keywords and hook location the JVM emits.

    These are the strings the writers and templates compare against; any one of
    them drifting would silently change an artifact.
    """
    assert events.ELEMENT_TYPE_BACKGROUND == "background"
    assert events.ELEMENT_TYPE_SCENARIO == "scenario"
    assert events.FEATURE_KEYWORD == "Feature"
    assert events.BACKGROUND_KEYWORD == "Background"
    assert events.DEFAULT_AFTER_HOOK_LOCATION == "features.environment.after_scenario"
    # A %-style format string with exactly one placeholder: the guard logs it
    # with the failing callback's name.
    assert events.HOOK_FAILURE_MESSAGE.count("%s") == 1
    assert issubclass(events.ResultSetError, RuntimeError)


def test_module_imports_no_flask_no_selenium_and_no_service() -> None:
    """AAP 0.4.2's import boundary, asserted against the source itself.

    The plan's dependency graph gives ``app/reporting`` exactly one intra-package
    edge - ``RP --> UT`` - and states that nothing in ``app/reporting`` imports a
    service.  The boundary is what lets this module be imported inside a worker
    process that never builds a Flask application; a stray import would make
    every worker pay for Flask and selenium, and would make the ordering of the
    port's layers unenforceable.
    """
    imported = imported_module_names(EVENTS_SOURCE_PATH)

    forbidden_prefixes = (
        "flask",
        "selenium",
        "webdriver_manager",
        "app.services",
        "app.web",
        "app.pages",
        "app.automation",
        "app.config",
    )
    for name in sorted(imported):
        for prefix in forbidden_prefixes:
            assert not (name == prefix or name.startswith(f"{prefix}.")), (
                f"{EVENTS_SOURCE_PATH.name} must not import {name}"
            )

    intra_package = {name for name in imported if name.split(".")[0] == "app"}
    assert intra_package == {"app.utils.paths"}


# ==========================================================================
# Value helpers: timestamps and durations
# ==========================================================================


def test_format_timestamp_emits_three_fractional_digits_and_a_literal_z() -> None:
    """The JVM generator's format, exactly: ``YYYY-MM-DDTHH:MM:SS.mmmZ``.

    ``datetime.isoformat()`` emits microseconds and ``+00:00``, so a report
    written with it would carry a timestamp the reference never shows and which
    ``app/templates/index.html`` does not parse.
    """
    assert events.format_timestamp(GOLDEN_INSTANT) == GOLDEN_TIMESTAMP
    assert len(events.format_timestamp(GOLDEN_INSTANT)) == len(GOLDEN_TIMESTAMP)


def test_format_timestamp_truncates_rather_than_rounds() -> None:
    """297_999 microseconds is ``.297``, as Java's ``SSS`` field prints it.

    Rounding would emit ``.298`` - a millisecond the run never reached, and a
    value that would not reproduce the reference's ``13:37:26.297Z``.
    """
    assert events.format_timestamp(GOLDEN_INSTANT).endswith(".297Z")
    assert events.format_timestamp(
        GOLDEN_INSTANT.replace(microsecond=999_999)
    ).endswith(".999Z")


def test_format_timestamp_treats_a_naive_instant_as_utc() -> None:
    """A naive value is assumed to be UTC rather than localised.

    Localising it would make a worker's timestamps depend on the machine's zone,
    and two workers in different zones would then produce a document whose
    ``started_at`` is not the earliest scenario at all.
    """
    naive = GOLDEN_INSTANT.replace(tzinfo=None)
    assert events.format_timestamp(naive) == GOLDEN_TIMESTAMP


def test_format_timestamp_converts_an_aware_instant_to_utc() -> None:
    """An offset instant is converted, not truncated to its local wall time."""
    eastern = GOLDEN_INSTANT.astimezone(timezone(timedelta(hours=3)))
    assert eastern.hour != GOLDEN_INSTANT.hour
    assert events.format_timestamp(eastern) == GOLDEN_TIMESTAMP


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (GOLDEN_DURATION_SECONDS, GOLDEN_DURATION_NANOS),
        (0.001, 1_000_000),
        (0.067, 67_000_000),
        (0, 0),
        (None, 0),
        (True, 0),
        (False, 0),
        (-1.5, 0),
        ("30.202", 0),
        (float("nan"), 0),
        (float("inf"), 0),
    ],
)
def test_nanos_from_seconds_table(seconds: Any, expected: int) -> None:
    """behave's float seconds become the contract's nanosecond integers.

    The reference's ``30202000000`` is 30.202 seconds; a float would break
    every JSON consumer that expects an integer, and a booleaning or negative
    value - which behave has been seen to report for a step it never ran -
    must become ``0`` rather than a nonsense duration.  ``bool`` is called out
    because it is an ``int`` subclass and would otherwise convert to 1e9.
    """
    result = events.nanos_from_seconds(seconds)

    assert result == expected
    assert isinstance(result, int)
    # ``bool`` is an ``int`` subclass, and a JSON duration of ``true`` would be
    # accepted by the type check while breaking every consumer.
    assert not isinstance(result, bool)


# ==========================================================================
# Value helpers: ids, keywords and argument spans
# ==========================================================================


def test_convert_to_id_replaces_the_java_character_class() -> None:
    """Each of ``[\\s'_,!]`` becomes exactly one ``-``, then the text lowers.

    Spelled out rather than written as Python's ``\\s``, which is Unicode-aware:
    folding a non-breaking space the JVM leaves alone would produce an id no
    JVM report ever contained, and the id is what the HTML reports key on.
    """
    for character in ID_REPLACED_CHARACTERS:
        assert events.convert_to_id(f"A{character}B") == "a-b", (
            f"{character!r} was not replaced"
        )


def test_convert_to_id_leaves_every_other_character_alone() -> None:
    """Periods, colons, quotes, parentheses and hyphens survive verbatim.

    This is why ``Sales.feature``'s ``".... app Sales feature"`` keeps its
    leading dots and why a scenario name carrying quotation marks keeps them.
    """
    assert events.convert_to_id(ID_SURVIVING_CHARACTERS) == ID_SURVIVING_CHARACTERS
    assert events.convert_to_id(".... app Sales feature") == "....-app-sales-feature"


def test_convert_to_id_golden_values_and_empty_input() -> None:
    """The three slugs the reference and the sample fixture carry."""
    assert events.convert_to_id(CRM_FEATURE_NAME) == CRM_FEATURE_ID
    assert (
        events.convert_to_id("User can change any user's information")
        == "user-can-change-any-user-s-information"
    )
    # An unnamed Examples block contributes an empty segment, which is source
    # behaviour the port preserves rather than tidies.
    assert events.convert_to_id(None) == ""
    assert events.convert_to_id("") == ""


def test_scenario_element_id_for_a_plain_scenario() -> None:
    """A plain scenario's id is ``<feature-slug>;<scenario-slug>``."""
    assert (
        events.scenario_element_id(
            CRM_FEATURE_NAME, "User can change the situation in progress"
        )
        == f"{CRM_FEATURE_ID};user-can-change-the-situation-in-progress"
    )


def test_scenario_element_id_for_an_examples_row_is_the_golden_value() -> None:
    """The reference's four-segment row id, reproduced exactly.

    The last segment is the row's one-based position **counting the header row
    as 1**, so behave's first body row (index 1) is ``2``.  Getting that off by
    one would produce ids no JVM report contains, and the PrettyReports pages
    key on them.
    """
    assert (
        events.scenario_element_id(
            CRM_FEATURE_NAME,
            "User can change information in dashboard",
            "Expected name",
            1,
        )
        == GOLDEN_ROW_ELEMENT_ID
    )


def test_scenario_element_id_for_an_unnamed_examples_block() -> None:
    """An unnamed block contributes an empty segment, not a dropped one.

    AAP 0.6 flags this as a case the reference artifact cannot show and which
    must be fixed against a clean generated baseline.  What is settled, and all
    that is asserted here, is that the feature and scenario slugs lead and the
    row position trails: the function never silently renumbers the row because
    the block had no name.
    """
    identifier = events.scenario_element_id("F", "S", "", 1)

    assert identifier.startswith("f;s;")
    assert identifier.endswith(";2")
    assert events.scenario_element_id("F", "S", None, 2).endswith(";3")


@pytest.mark.parametrize(
    ("keyword", "expected"),
    [
        ("Given", "Given "),
        ("When", "When "),
        ("And", "And "),
        ("Then", "Then "),
        ("Given ", "Given "),
        ("  And  ", "And "),
        ("", ""),
        (None, ""),
    ],
)
def test_step_keyword_appends_exactly_one_trailing_space(
    keyword: str | None, expected: str
) -> None:
    """The contract's trailing space, added once and never doubled.

    Measured in the reference for all four keywords.  An empty keyword yields
    ``""`` rather than a lone space, which would be neither the JVM's output
    nor a usable display value in ``step_row.html``.
    """
    assert events.step_keyword(keyword) == expected


def test_widen_quoted_span_reproduces_the_reference_offsets() -> None:
    """behave's inner spans become the JVM's quote-inclusive values.

    Cucumber's ``{string}`` placeholder matched the quotes too, so the
    reference records ``"Test2"`` *with* them at offset 44, while this port's
    step phrases put the quotes in the phrase literal and behave reports 45.
    Without the widening every parameterized step's ``match.arguments`` would
    be off by one and one character short.
    """
    widened = [
        events.widen_quoted_span(GOLDEN_STEP_NAME, start, end)
        for start, end in GOLDEN_BEHAVE_SPANS
    ]

    assert [value for value, _ in widened] == list(GOLDEN_ARGUMENT_VALUES)
    assert [offset for _, offset in widened] == list(GOLDEN_ARGUMENT_OFFSETS)


def test_widen_quoted_span_values_always_index_back_into_the_name() -> None:
    """``name[offset:offset + len(val)] == val`` holds for every span.

    The JVM's ``offset`` is what an HTML report uses to highlight the argument
    inside the step text, so a value that does not sit at its own offset would
    highlight the wrong characters.
    """
    for start, end in GOLDEN_BEHAVE_SPANS:
        value, offset = events.widen_quoted_span(GOLDEN_STEP_NAME, start, end)
        assert GOLDEN_STEP_NAME[offset : offset + len(value)] == value


def test_widen_quoted_span_leaves_an_unquoted_span_as_behave_gave_it() -> None:
    """A ``{count:d}``-style placeholder has no quotes to widen over."""
    assert events.widen_quoted_span("unquoted param 42 here", 15, 17) == ("42", 15)


def test_widen_quoted_span_tolerates_a_span_outside_the_name() -> None:
    """An out-of-range or non-integer span yields ``("", max(start, 0))``.

    A converted argument, or a matcher reporting no span at all, must not raise
    inside a formatter callback: the collector falls back to the argument's own
    text, and it can only do that if this function returns rather than throws.
    """
    assert events.widen_quoted_span("short", 10, 12) == ("", 10)
    assert events.widen_quoted_span("short", -3, 2) == ("", 0)
    assert events.widen_quoted_span("short", "a", 2) == ("", 0)  # type: ignore[arg-type]


def test_run_metadata_carries_the_four_fixed_keys_as_strings() -> None:
    """``artifact/metadata.html`` renders exactly this vocabulary.

    Every value is a string and a probe that yields nothing yields ``""``:
    ``platform.processor()`` is empty on many Linux builds, and a report must
    not fail a run because the machine would not describe its own CPU.
    """
    metadata = events.run_metadata()

    assert set(metadata) == set(METADATA_KEYS)
    for section, keys in METADATA_KEYS.items():
        assert_keys(metadata[section], keys, f"metadata[{section!r}]")
        for key in keys:
            assert isinstance(metadata[section][key], str)
    assert metadata["implementation"]["name"] == "behave"


# ==========================================================================
# Document builders: the key-presence rules
# ==========================================================================


def test_new_result_set_carries_every_run_level_key() -> None:
    """A consumer never has to test a run-level key for membership.

    All seven keys are present from the outset - that is what lets a writer,
    the merge and a hand-written fixture share one reader.
    """
    document = events.new_result_set()

    assert_keys(document, RESULT_SET_KEYS, "result set")
    assert document["features"] == []
    assert document["schema_version"] == events.SCHEMA_VERSION
    assert document["dry_run"] is False
    assert document["started_at"] is None
    assert document["generated_at"] is None
    assert document["tag_expression"] is None
    assert set(document["metadata"]) == set(METADATA_KEYS)


def test_new_result_set_accepts_pinned_metadata_and_run_fields() -> None:
    """The overrides a worker and a test both need.

    ``metadata`` is copied rather than aliased, so a caller that keeps its own
    mapping cannot later mutate a document it already handed off.
    """
    metadata = {"implementation": {"name": "behave", "version": "1.3.3"}}
    document = events.new_result_set(
        dry_run=True,
        tag_expression="@Smoke",
        metadata=metadata,
        started_at=GOLDEN_TIMESTAMP,
        generated_at=GOLDEN_TIMESTAMP,
    )

    assert document["dry_run"] is True
    assert document["tag_expression"] == "@Smoke"
    assert document["started_at"] == GOLDEN_TIMESTAMP
    assert document["generated_at"] == GOLDEN_TIMESTAMP
    metadata["implementation"] = {"name": "tampered", "version": "0"}
    assert document["metadata"]["implementation"]["name"] == "behave"


def test_new_feature_always_carries_tags_even_when_empty() -> None:
    """The JVM's feature map adds ``tags`` unconditionally.

    Asymmetric with a scenario on purpose, and the asymmetry is measured: five
    of the ten features declare no tag at all, so a writer reading
    ``feature["tags"]`` without a membership test is relying on this.
    """
    feature = events.new_feature(
        uri=f"file:{CRM_PATH}", path=CRM_PATH, identifier=CRM_FEATURE_ID, line=2,
        name=CRM_FEATURE_NAME,
    )

    assert_keys(feature, FEATURE_KEYS, "feature")
    assert feature["tags"] == []
    assert feature["elements"] == []
    assert feature["description"] == ""
    assert feature["keyword"] == events.FEATURE_KEYWORD


def test_new_element_background_carries_only_the_shared_keys() -> None:
    """A Background occurrence is deliberately poorer than a scenario.

    It carries none of ``id``, ``start_timestamp``, ``tags`` or ``after``
    *whatever is passed for them* - measured against the reference, whose eight
    elements are four backgrounds and four scenarios and whose every background
    lacks all four.  ``pretty/_element_tree.html`` tolerates their absence
    because this function guarantees it.
    """
    element = events.new_element(
        element_type=events.ELEMENT_TYPE_BACKGROUND,
        keyword="Background",
        line=CRM_BACKGROUND_LINE,
        name="As a Posmanager",
        identifier="must-be-ignored",
        start_timestamp=GOLDEN_TIMESTAMP,
        tags=[events.scenario_tag("@Smoke")],
        after=[events.new_hook_entry()],
    )

    assert set(element) == set(ELEMENT_SHARED_KEYS)
    assert element["type"] == events.ELEMENT_TYPE_BACKGROUND
    for key in (*SCENARIO_ONLY_KEYS, "tags"):
        assert key not in element, f"a background must not carry {key!r}"


def test_new_element_scenario_adds_id_start_timestamp_and_after() -> None:
    """A scenario carries the shared keys plus its own three."""
    element = events.new_element(
        element_type=events.ELEMENT_TYPE_SCENARIO,
        keyword="Scenario",
        line=CRM_PASSING_SCENARIO_LINE,
        name="User can create pipeline in the displayed dashboard",
        identifier=f"{CRM_FEATURE_ID};user-can-create-pipeline",
        start_timestamp=GOLDEN_TIMESTAMP,
        tags=[events.scenario_tag("@Smoke")],
    )

    assert_keys(element, (*ELEMENT_SHARED_KEYS, *SCENARIO_ONLY_KEYS), "scenario")
    assert element["id"] == f"{CRM_FEATURE_ID};user-can-create-pipeline"
    assert element["start_timestamp"] == GOLDEN_TIMESTAMP
    assert element["tags"] == [{"name": "@Smoke"}]
    assert element["after"] == []


def test_new_element_scenario_omits_tags_when_it_has_none() -> None:
    """An untagged scenario omits the key rather than carrying ``[]``.

    The JVM adds it under ``if (!testCase.getTags().isEmpty())``, and with five
    untagged features omission is the common case.  Emitting ``[]`` would put a
    key in every artifact that no JVM report ever carried.
    """
    for tags in (None, []):
        element = events.new_element(
            element_type=events.ELEMENT_TYPE_SCENARIO,
            keyword="Scenario",
            line=SALES_UNDEFINED_SCENARIO_LINE,
            name="Verify that the user's search finds his name",
            tags=tags,
        )
        assert "tags" not in element


def test_new_element_records_an_unknown_type_as_a_scenario(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unrecognised type is recorded and reported, never dropped.

    Dropping the element would lose real results; the warning is what makes the
    condition diagnosable instead of silent.
    """
    with caplog.at_level(logging.WARNING, logger=events.__name__):
        element = events.new_element(
            element_type="rule", keyword="Rule", line=3, name="odd"
        )

    assert element["type"] == events.ELEMENT_TYPE_SCENARIO
    assert_keys(element, SCENARIO_ONLY_KEYS, "coerced element")
    assert any(
        "Unknown element type" in record.getMessage() for record in caplog.records
    )


def test_new_step_always_carries_all_five_keys() -> None:
    """Optionality lives inside ``match`` and ``result``, never in the step.

    Every consumer reads ``step["match"]`` and ``step["result"]`` directly, so
    an absent key would be an AttributeError in a template rather than an empty
    render.
    """
    step = events.new_step(keyword="Given", line=7, name="User login")

    assert set(step) == set(STEP_KEYS)
    assert step["keyword"] == "Given "
    assert step["matched"] is False
    assert step["match"] == {}
    assert step["result"] == {}


def test_new_step_copies_the_mappings_it_is_given() -> None:
    """A caller's mapping is snapshotted, not aliased.

    The collector fills a step's ``match`` in one callback and its ``result``
    in the next; aliasing would let a later mutation of the caller's dict reach
    back into a document already written.
    """
    match = {"location": "features.steps.crm_steps.f"}
    result = {"status": "passed", "duration": GOLDEN_DURATION_NANOS}
    step = events.new_step(
        keyword="Then", line=12, name="n", matched=True, match=match, result=result
    )

    match["location"] = "tampered"
    result["status"] = "failed"
    assert step["match"] == {"location": "features.steps.crm_steps.f"}
    assert step["result"]["status"] == "passed"
    assert step["matched"] is True


def test_new_hook_entry_shape_and_omitted_location() -> None:
    """A hook entry is ``match``/``result``/``embeddings``, and nothing else.

    ``location=None`` yields an empty ``match``, mirroring the JVM's omission
    of the field when it has none.  Asserted key-wise rather than as a whole
    dictionary, because the hook's own status and duration are a live part of
    the schema and an added key must not break this.
    """
    entry = events.new_hook_entry()

    assert set(entry) == set(HOOK_ENTRY_KEYS)
    assert entry["match"] == {"location": events.DEFAULT_AFTER_HOOK_LOCATION}
    assert entry["result"]["status"] == "passed"
    assert entry["result"]["duration"] == 0
    assert entry["embeddings"] == []

    anonymous = events.new_hook_entry(
        location=None,
        status="failed",
        duration=412_000_000,
        embeddings=[{"mime_type": PNG_MIME_TYPE, "data": "x"}],
    )
    assert anonymous["match"] == {}
    assert anonymous["result"]["status"] == "failed"
    assert anonymous["result"]["duration"] == 412_000_000
    assert anonymous["embeddings"] == [{"mime_type": PNG_MIME_TYPE, "data": "x"}]


def test_feature_tag_is_the_long_shape_and_scenario_tag_the_short_one() -> None:
    """The two tag shapes differ, and the difference is measured.

    A feature tag carries ``type`` and its own ``location`` - ``@Smoke`` sits at
    ``Crm.feature:1`` while the feature is at line 2 - and a scenario tag
    carries nothing but ``name``.  Both restore the leading ``@`` behave strips.
    """
    assert events.feature_tag("Smoke", 1) == {
        "name": "@Smoke",
        "type": "Tag",
        "location": {"line": 1, "column": 1},
    }
    assert events.feature_tag("@Smoke", 2, 5)["location"] == {"line": 2, "column": 5}
    assert events.scenario_tag("wip") == {"name": "@wip"}
    assert events.scenario_tag("@wip") == {"name": "@wip"}


# ==========================================================================
# The six required shapes, over the pinned sample fixture
#
# AAP 0.6: "tests/test_events.py asserts every field the four writers read is
# present for each of a passing scenario, a failing scenario with an
# attachment, a skipped step, an undefined step, a background and a two-row
# outline."  One test per shape, so a failure names the shape.
# ==========================================================================


def test_sample_fixture_census_is_the_measured_one(
    sample_result_set: dict[str, Any],
) -> None:
    """The fixture every writer test shares: 4 features, 10 scenarios, 4
    backgrounds, 30 steps.

    Asserted here because this module is where the schema is defined: if the
    fixture lost a shape, four writer suites would go quietly less thorough
    while still passing.
    """
    assert_keys(sample_result_set, RESULT_SET_KEYS, "sample result set")
    features = sample_result_set["features"]
    elements = [element for feature in features for element in feature["elements"]]
    backgrounds = [
        element
        for element in elements
        if element["type"] == events.ELEMENT_TYPE_BACKGROUND
    ]
    scenarios = list(events.iter_scenarios(sample_result_set))
    steps = [step for element in elements for step in element["steps"]]

    assert len(features) == SAMPLE_FEATURE_COUNT
    assert len(scenarios) == SAMPLE_SCENARIO_COUNT
    assert len(backgrounds) == SAMPLE_BACKGROUND_COUNT
    assert len(steps) == SAMPLE_STEP_COUNT
    assert {feature["path"] for feature in features} == {
        CONTACT_PATH,
        CRM_PATH,
        INVENTORY_PATH,
        SALES_PATH,
    }


def test_shape_passing_scenario_carries_every_required_key(
    sample_result_set: dict[str, Any],
) -> None:
    """A passing scenario: every level complete, every step resolved.

    This is the shape the HTML writers render and the JSON writer copies
    through; a missing ``start_timestamp`` or ``match.location`` here would
    surface as an empty cell in the published report.
    """
    feature = feature_by_path(sample_result_set, CRM_PATH)
    assert_keys(feature, FEATURE_KEYS, "Crm feature")
    element = element_at_line(
        feature, CRM_PASSING_SCENARIO_LINE, events.ELEMENT_TYPE_SCENARIO
    )

    assert_keys(
        element, (*ELEMENT_SHARED_KEYS, *SCENARIO_ONLY_KEYS), "passing scenario"
    )
    assert element["keyword"] == "Scenario"
    assert element["selected"] is True
    assert element["id"] == (
        f"{CRM_FEATURE_ID};user-can-create-pipeline-in-the-displayed-dashboard"
    )
    assert element["start_timestamp"].endswith("Z")
    assert element["tags"] == [{"name": "@Smoke"}]
    assert element["after"] == []

    for step in element["steps"]:
        assert_keys(step, STEP_KEYS, "passing step")
        assert step["keyword"].endswith(" ")
        assert step["matched"] is True
        assert step["match"]["location"].startswith("features.steps.")
        assert step["result"]["status"] == "passed"
        assert isinstance(step["result"]["duration"], int)
        assert step["result"]["duration"] > 0
        assert "error_message" not in step["result"]


def test_shape_failing_scenario_carries_error_text_and_a_named_attachment(
    sample_result_set: dict[str, Any],
) -> None:
    """A failing scenario, with the screenshot hanging off the after hook.

    The embedding shape is not invented: ``Hooks.java:15`` attaches with
    ``(screenshot, "image/png", scenario.getName())``, which is why the
    embedding is *named after the scenario* rather than after the step, and
    ``partials/lightbox.html`` renders it as
    ``data:<mime_type>;base64,<data>``.  Hook keys are asserted individually so
    that the hook gaining its own status or duration does not break this.
    """
    feature = feature_by_path(sample_result_set, CRM_PATH)
    element = element_at_line(
        feature, CRM_FAILING_SCENARIO_LINE, events.ELEMENT_TYPE_SCENARIO
    )

    failed = [
        step for step in element["steps"] if step["result"]["status"] == "failed"
    ]
    assert len(failed) == 1
    message = failed[0]["result"]["error_message"]
    assert message
    assert "\r" not in message
    assert isinstance(failed[0]["result"]["duration"], int)

    assert len(element["after"]) == 1
    entry = element["after"][0]
    assert_keys(entry, HOOK_ENTRY_KEYS, "after-hook entry")
    assert entry["match"]["location"] == events.DEFAULT_AFTER_HOOK_LOCATION
    assert entry["result"]["status"] == "passed"

    assert len(entry["embeddings"]) == 1
    embedding = entry["embeddings"][0]
    assert embedding["mime_type"] == PNG_MIME_TYPE
    assert embedding["name"] == element["name"]
    assert base64.b64decode(embedding["data"], validate=True).startswith(b"\x89PNG")


def test_shape_skipped_step_keeps_its_status_and_its_location(
    sample_result_set: dict[str, Any],
) -> None:
    """A step skipped after a failure still reports a definition.

    The JVM matched every step of a test case before running any of it, so the
    reference carries ``match.location`` for skipped steps.  behave gives a
    formatter no ``match`` callback for a step it never executes, which is why
    the collector repeats the registry lookup - and why this assertion is the
    one that would catch that being dropped.
    """
    feature = feature_by_path(sample_result_set, CRM_PATH)
    element = element_at_line(
        feature, CRM_FAILING_SCENARIO_LINE, events.ELEMENT_TYPE_SCENARIO
    )

    skipped = [
        step for step in element["steps"] if step["result"]["status"] == "skipped"
    ]
    assert len(skipped) == 1
    step = skipped[0]
    assert_keys(step, STEP_KEYS, "skipped step")
    assert step["matched"] is True
    assert step["match"]["location"].startswith("features.steps.")
    # Recorded faithfully as 0 rather than dropped: whether to emit the key is
    # the writer's decision, because the JVM emits it only when non-zero.
    assert step["result"]["duration"] == 0
    assert "error_message" not in step["result"]


def test_shape_undefined_step_is_unmatched_with_an_empty_match(
    sample_result_set: dict[str, Any],
) -> None:
    """An undefined step carries ``matched: false`` and ``match: {}``.

    That empty mapping is what lets the JSON writer omit ``location`` exactly
    as the JVM does; a location invented here would claim code ran that does
    not exist.
    """
    feature = feature_by_path(sample_result_set, SALES_PATH)
    element = element_at_line(
        feature, SALES_UNDEFINED_SCENARIO_LINE, events.ELEMENT_TYPE_SCENARIO
    )

    undefined = [
        step for step in element["steps"] if step["result"]["status"] == "undefined"
    ]
    assert len(undefined) == 1
    step = undefined[0]
    assert_keys(step, STEP_KEYS, "undefined step")
    assert step["matched"] is False
    assert step["match"] == {}
    assert step["result"]["duration"] == 0


def test_shape_background_occurrence_omits_the_scenario_only_keys(
    sample_result_set: dict[str, Any],
) -> None:
    """Every Background occurrence is the poorer element, once per scenario.

    Four occurrences for four Crm scenarios, each immediately before its own
    scenario - the positional invariant the whole suite pins - and each
    carrying only the seven shared keys.  ``partials/step_row.html`` reads only
    ``step.*`` so that it renders unchanged inside one.
    """
    feature = feature_by_path(sample_result_set, CRM_PATH)
    types = [element["type"] for element in feature["elements"]]

    assert types == [
        events.ELEMENT_TYPE_BACKGROUND,
        events.ELEMENT_TYPE_SCENARIO,
    ] * SAMPLE_BACKGROUND_COUNT

    for element in feature["elements"]:
        if element["type"] != events.ELEMENT_TYPE_BACKGROUND:
            continue
        assert set(element) == set(ELEMENT_SHARED_KEYS)
        assert element["line"] == CRM_BACKGROUND_LINE
        assert element["steps"], "a background occurrence carries its own steps"
        for step in element["steps"]:
            assert_keys(step, STEP_KEYS, "background step")
            assert step["result"]["status"] == "passed"


def test_shape_outline_rows_carry_row_ids_and_the_template_step_line(
    sample_result_set: dict[str, Any],
) -> None:
    """Two rows of one outline: plain name, row line, template step line.

    Three measured asymmetries live here and every one of them is deliberate:
    the element name is the *outline's* name with behave's
    ``" -- @1.1 Expected name"`` annotation removed, the element line is the
    *data row's*, and the step line is the *template's* - which is why the step
    line is lower than the element line.  The ids differ only in their trailing
    position, counting the header row as 1.
    """
    feature = feature_by_path(sample_result_set, CRM_PATH)
    first = element_at_line(
        feature, CRM_OUTLINE_FIRST_ROW_LINE, events.ELEMENT_TYPE_SCENARIO
    )
    second = element_at_line(
        feature, CRM_OUTLINE_SECOND_ROW_LINE, events.ELEMENT_TYPE_SCENARIO
    )

    for element in (first, second):
        assert_keys(element, (*ELEMENT_SHARED_KEYS, *SCENARIO_ONLY_KEYS), "outline row")
        assert element["keyword"] == "Scenario Outline"
        assert " -- @" not in element["name"]
        assert element["steps"][0]["line"] < element["line"]

    assert first["name"] == second["name"]
    assert first["id"] == GOLDEN_ROW_ELEMENT_ID
    assert first["id"].endswith(";2")
    assert second["id"].endswith(";3")

    # The substituted row values reach the step name and its arguments.
    step = first["steps"][0]
    assert step["name"] == GOLDEN_STEP_NAME
    assert [argument["val"] for argument in step["match"]["arguments"]] == list(
        GOLDEN_ARGUMENT_VALUES
    )
    assert [argument["offset"] for argument in step["match"]["arguments"]] == list(
        GOLDEN_ARGUMENT_OFFSETS
    )


def test_unselected_scenario_is_recorded_with_selected_false(
    sample_result_set: dict[str, Any],
) -> None:
    """A scenario the tag expression excluded is recorded, and flagged.

    behave announces it because ``show_skipped`` defaults to true, while the
    JVM never starts it and never emits it.  Recording the answer is what lets
    ``app/reporting/cucumber_json.py`` drop it; dropping it here instead would
    leave the viewer unable to show what the filter excluded.
    """
    feature = feature_by_path(sample_result_set, SALES_PATH)
    element = element_at_line(
        feature, SALES_EXCLUDED_SCENARIO_LINE, events.ELEMENT_TYPE_SCENARIO
    )

    assert element["selected"] is False
    assert element["tags"] == [{"name": "@wip"}]
    assert [step["result"]["status"] for step in element["steps"]] == [
        "skipped",
        "skipped",
    ]


def test_iter_scenarios_yields_scenarios_only_in_document_order(
    sample_result_set: dict[str, Any],
) -> None:
    """Backgrounds are skipped and position is stable.

    Position is load-bearing rather than cosmetic: two features in this suite
    share an ``id`` - ``Contact``/``Inventory`` and ``Login``/``Notes`` - so the
    HTTP report routes key on list position, and they get that position from
    here.
    """
    pairs = list(events.iter_scenarios(sample_result_set))

    assert all(
        element["type"] == events.ELEMENT_TYPE_SCENARIO for _, element in pairs
    )
    assert [feature["path"] for feature, _ in pairs] == [
        CONTACT_PATH,
        *[CRM_PATH] * 4,
        INVENTORY_PATH,
        *[SALES_PATH] * 4,
    ]
    assert [element["line"] for _, element in pairs] == [
        19,
        CRM_PASSING_SCENARIO_LINE,
        CRM_FAILING_SCENARIO_LINE,
        CRM_OUTLINE_FIRST_ROW_LINE,
        CRM_OUTLINE_SECOND_ROW_LINE,
        11,
        SALES_UNDEFINED_SCENARIO_LINE,
        32,
        33,
        SALES_EXCLUDED_SCENARIO_LINE,
    ]


def test_iter_scenarios_tolerates_a_malformed_document() -> None:
    """Non-mapping features and elements are skipped, not fatal.

    The traversal runs over a document that may have come off disk from a
    worker that died mid-write, and every writer calls it; raising here would
    turn a damaged shard into a lost report.
    """
    document = {
        "features": [
            "not a feature",
            None,
            {"elements": ["not an element", {"type": events.ELEMENT_TYPE_SCENARIO}]},
            {},
        ]
    }

    pairs = list(events.iter_scenarios(document))

    assert len(pairs) == 1
    assert pairs[0][1]["type"] == events.ELEMENT_TYPE_SCENARIO
    assert list(events.iter_scenarios({})) == []


# ==========================================================================
# Writing, reading back, and the single failure channel
# ==========================================================================


def test_dump_and_load_round_trip_is_lossless_for_all_six_shapes(
    sample_result_set: dict[str, Any], tmp_path: Path
) -> None:
    """The document survives a write and a read unchanged.

    The pair is what a worker and the merge step use across a process boundary,
    so a key reordered, a duration coerced to float or a non-ASCII message
    mangled would corrupt every artifact downstream of it.  The destination is
    two directories deep on purpose: ``dump_result_set`` creates its parents,
    which is what keeps a worker from failing merely because ``target/`` was
    emptied by ``--clean``.

    Losslessness is stated as "nothing the writer put in came back different",
    not as raw equality, because the document now **declares its own
    completeness**: ``load_result_set`` completes the run-level envelope with
    ``complete`` and ``collection_errors``, which is what lets a hand-written
    fixture -- this one -- omit them and still read as a complete run rather
    than as a shard the collector abandoned.  So the read-back document is
    asserted to carry every key of the original with its exact value, and the
    additions are asserted to be exactly that completeness declaration and
    nothing else.  A key silently *changed* still fails on the first half, and
    a key silently *invented* still fails on the second.
    """
    destination = tmp_path / "target" / ".workers" / WORKER_FILE_NAME
    written = events.dump_result_set(sample_result_set, destination)

    assert written == destination
    assert destination.read_text(encoding="utf-8").endswith("\n")

    read_back = events.load_result_set(destination)

    for key, value in sample_result_set.items():
        assert read_back[key] == value, key
    assert set(read_back) - set(sample_result_set) == {
        "complete",
        "collection_errors",
    }
    assert read_back["complete"] is True
    assert read_back["collection_errors"] == []


def test_load_result_set_raises_result_set_error_for_a_missing_file(
    tmp_path: Path,
) -> None:
    """One exception type for every failure, with the cause chained.

    ``app/services/test_run_service.py`` names the offending shard on stderr
    and applies the plan's exit table; it must not have to tell an
    ``OSError`` from a ``JSONDecodeError`` to do so.
    """
    missing = tmp_path / "absent.json"

    with pytest.raises(events.ResultSetError) as raised:
        events.load_result_set(missing)

    assert str(missing) in str(raised.value)
    assert isinstance(raised.value.__cause__, OSError)


def test_load_result_set_raises_for_an_unreadable_path(tmp_path: Path) -> None:
    """A path that exists but cannot be read as a file is the same channel.

    A directory is the reproducible case: a worker that crashed between
    creating its output and writing it leaves exactly this kind of surprise.

    What is asserted is the *channel and the reason*, not a chained cause.  The
    loader no longer reaches an :class:`OSError` here at all: it opens one
    descriptor with ``O_NOFOLLOW``, interrogates it with :func:`os.fstat` and
    refuses anything that is not a regular file itself -- which is the whole
    point of that tier, because it is what refuses a symlink or a FIFO left in
    a shard's place instead of following or blocking on it.  A refusal it
    reached on its own has no underlying exception to chain, so the contract
    that matters is the one exception type, the offending path, and a stated
    reason the parent can put on stderr.
    """
    with pytest.raises(events.ResultSetError) as raised:
        events.load_result_set(tmp_path)

    message = str(raised.value)
    assert str(tmp_path) in message
    assert "not a regular file" in message
    assert "was not read" in message


def test_load_result_set_raises_for_invalid_json(tmp_path: Path) -> None:
    """A truncated shard - a killed worker - is reported, not re-raised raw."""
    broken = tmp_path / "broken.json"
    broken.write_text('{"features": [', encoding="utf-8")

    with pytest.raises(events.ResultSetError) as raised:
        events.load_result_set(broken)

    assert isinstance(raised.value.__cause__, ValueError)
    assert "not valid JSON" in str(raised.value)


def test_load_result_set_raises_for_a_non_object_document(tmp_path: Path) -> None:
    """A JSON list is valid JSON and an invalid result document.

    Worth its own case because behave's *own* JSON formatter writes a list at
    the top level, so this is precisely the file someone will hand it by
    mistake.

    The message is asserted by its *parts* rather than as a sentence, because
    the parts are the contract the strict validator added and the sentence is
    not: every rejection now names the shard it came from, the JSON-pointer
    path of the offending value -- ``the document`` for one found at the root
    -- and what was found there instead.  That is what makes a rejection
    actionable for the parent, which logs it verbatim; pinning the wording
    would fail the day the phrasing is improved without a rule changing.
    """
    listed = tmp_path / "list.json"
    listed.write_text("[]", encoding="utf-8")

    with pytest.raises(events.ResultSetError) as raised:
        events.load_result_set(listed)

    message = str(raised.value)
    assert str(listed) in message
    assert "the document" in message
    assert "must be an object" in message
    assert "found list" in message


def test_load_result_set_raises_when_features_is_not_a_list(tmp_path: Path) -> None:
    """``features`` is the one member every consumer iterates.

    Asserted by its parts for the same reason as the case above, and with the
    path segment included: a document can carry a bad ``features`` at the root
    only, so the path is the bare key, and a rejection that named no path at
    all would leave the parent unable to say *where* the shard is wrong.
    """
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({"features": {"Crm": []}}), encoding="utf-8")

    with pytest.raises(events.ResultSetError) as raised:
        events.load_result_set(wrong)

    message = str(raised.value)
    assert str(wrong) in message
    assert "features" in message
    assert "must be a list" in message
    assert "found dict" in message


def test_load_result_set_completes_an_abbreviated_envelope(tmp_path: Path) -> None:
    """A hand-written fixture may omit run-level boilerplate.

    Only the envelope is completed - feature data is never invented or
    repaired - so a consumer gets all seven keys without the loader pretending
    to know anything about the run.
    """
    minimal = tmp_path / "minimal.json"
    minimal.write_text(json.dumps({"features": []}), encoding="utf-8")

    document = events.load_result_set(minimal)

    assert_keys(document, RESULT_SET_KEYS, "completed envelope")
    assert document["schema_version"] == events.SCHEMA_VERSION
    assert document["dry_run"] is False
    assert document["metadata"] == {}
    assert document["features"] == []


def test_load_result_set_reports_a_version_mismatch_through_one_channel(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A foreign schema version is reported, and each outcome is exact.

    Two outcomes are admissible and **both are fully pinned here**, because
    the module's published contract and the revision it is under disagree about
    which one applies.  The module documents today's: ``load_result_set``
    *"warns - and does not fail - when it reads a different version, because a
    stale worker file is a diagnosable condition rather than a crash"*.  Its
    owner is making that validation stricter and versioned, which would turn
    the same input into a typed rejection.  Neither the AAP nor the baseline
    settles it, so this test asserts the whole of each branch rather than
    choosing between them:

    * **Accepted** -- the returned envelope is complete, the version the file
      declared is *not* silently adopted as this build's, and a warning naming
      both versions was emitted.  A diagnosable condition that is not
      diagnosed is the failure mode this branch has to exclude.
    * **Rejected** -- the error is ``ResultSetError``, naming the source and
      the offending version, and nothing untyped escapes.  That single failure
      channel is what ``app/services/test_run_service.py`` needs in order to
      name the offending shard on stderr and apply the exit table.

    So a silent acceptance fails here whichever way the owner settles it.
    """
    foreign = tmp_path / "foreign-version.json"
    foreign.write_text(
        json.dumps(
            {"schema_version": FOREIGN_SCHEMA_VERSION, "features": []}
        ),
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING, logger=events.__name__):
        try:
            document = events.load_result_set(foreign)
        except events.ResultSetError as error:
            assert str(foreign) in str(error)
            assert str(FOREIGN_SCHEMA_VERSION) in str(error)
            return
        except (ValueError, OSError, KeyError) as error:  # pragma: no cover
            pytest.fail(f"an untyped error escaped load_result_set: {error!r}")

    assert isinstance(document, dict)
    assert_keys(document, RESULT_SET_KEYS, "foreign-version document")
    assert events.SCHEMA_VERSION != FOREIGN_SCHEMA_VERSION
    messages = [record.getMessage() for record in caplog.records]
    assert any(
        str(FOREIGN_SCHEMA_VERSION) in message
        and str(events.SCHEMA_VERSION) in message
        for message in messages
    ), f"the version mismatch was accepted without being reported: {messages}"


def test_dump_result_set_reports_a_destination_it_cannot_write(
    tmp_path: Path,
) -> None:
    """An unwritable destination raises ``OSError`` rather than losing a shard.

    ``dump_result_set`` documents this explicitly -- *"Deliberately not
    swallowed: producing an artifact is the caller's contract with the exit
    table, and a silent failure would leave the merge reading a file that is
    not there"* -- and it is the write half of the load/dump error contract
    that ``app/services/test_run_service.py`` depends on: a worker whose
    intermediate never reached disk has to surface as a dead shard, which is a
    non-zero exit class, rather than as a shard that merges to nothing.

    A regular file is put where the destination's parent directory would go, so
    ``ensure_parent`` cannot succeed and the failure is a genuine filesystem
    condition rather than a patched one.
    """
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory", encoding="utf-8")

    with pytest.raises(OSError):
        events.dump_result_set(events.new_result_set(), blocker / "shard.json")


def test_dump_result_set_reports_an_unserialisable_document(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A value JSON cannot encode is coerced and reported, never dropped.

    The serialiser's documented fallback: a document holding a value the
    encoder cannot handle -- which the builders make impossible, but which a
    hook cannot be proven never to store -- is written with the value coerced
    to text after a warning, *"because a diagnosable document beats no document
    at all"*.  Were this to fail, one unexpected value in one scenario would
    cost the whole shard, and the merge would report a dead worker for a run
    that actually completed.
    """
    destination = tmp_path / "coerced.json"
    document = events.new_result_set()
    document["metadata"] = {"unencodable": object()}

    with caplog.at_level(logging.WARNING, logger=events.__name__):
        written = events.dump_result_set(document, destination)

    reloaded = json.loads(written.read_text(encoding="utf-8"))
    assert isinstance(reloaded["metadata"]["unencodable"], str)
    assert any(
        "coerced" in record.getMessage() for record in caplog.records
    ), "the coercion was applied without being reported"


def test_a_malformed_worker_file_is_named_and_the_others_still_merge(
    sample_result_set: dict[str, Any], tmp_path: Path
) -> None:
    """One damaged shard must cost one shard, not the whole run.

    This is the merge step's real failure mode under the process pool: each
    worker writes its own file, and the load of each is individually typed, so
    the caller can name the shard it lost and still publish the rest.
    """
    features = sample_result_set["features"]
    good_paths: list[Path] = []
    for index, feature in enumerate((features[0], features[2])):
        shard = tmp_path / f"worker-{index}.json"
        events.dump_result_set(
            events.new_result_set(metadata={}, features=[feature]), shard
        )
        good_paths.append(shard)
    damaged = tmp_path / "worker-2.json"
    damaged.write_text("{not json", encoding="utf-8")

    loaded: list[dict[str, Any]] = []
    failures: list[str] = []
    for shard in (*good_paths, damaged):
        try:
            loaded.append(events.load_result_set(shard))
        except events.ResultSetError as error:
            failures.append(str(error))

    assert len(loaded) == 2
    assert len(failures) == 1
    assert str(damaged) in failures[0]
    merged = events.merge_result_sets(loaded)
    assert [feature["path"] for feature in merged["features"]] == [
        CONTACT_PATH,
        INVENTORY_PATH,
    ]


# ==========================================================================
# Merging the per-worker documents
# ==========================================================================


def _feature_with(
    path: str,
    elements: Sequence[dict[str, Any]],
    *,
    name: str = CRM_FEATURE_NAME,
) -> dict[str, Any]:
    """Build a feature object for a merge test.

    :param path: The feature's path, which is its merge identity.
    :param elements: Its elements, in collection order.
    :param name: Its name, from which the id is slugged.
    :returns: The feature object.
    """
    return events.new_feature(
        uri=f"file:{path}",
        path=path,
        identifier=events.convert_to_id(name),
        line=1,
        name=name,
        elements=elements,
    )


def _scenario(line: int, *, name: str | None = None) -> dict[str, Any]:
    """Build a scenario element at ``line``.

    :param line: The element's line, which is what a unit sorts by.
    :param name: Its name; defaults to one derived from the line.
    :returns: The element object.
    """
    return events.new_element(
        element_type=events.ELEMENT_TYPE_SCENARIO,
        keyword="Scenario",
        line=line,
        name=name or f"scenario at {line}",
        identifier=f"{CRM_FEATURE_ID};scenario-at-{line}",
        start_timestamp=events.format_timestamp(
            GOLDEN_INSTANT + timedelta(seconds=line)
        ),
    )


def _background() -> dict[str, Any]:
    """Build a Background occurrence.

    :returns: The element object, at the Background's own line - which every
        occurrence shares, and which is why a flat sort by line would collect
        them all at the front.
    """
    return events.new_element(
        element_type=events.ELEMENT_TYPE_BACKGROUND,
        keyword="Background",
        line=CRM_BACKGROUND_LINE,
        name="As a Posmanager",
    )


def test_merge_groups_the_shards_of_one_feature_by_path(
    sample_result_set: dict[str, Any],
) -> None:
    """A feature sharded across two workers yields exactly one feature.

    The plan's rule - *one file per worker, merged by feature path* - and the
    reason the JSON writer can assume one object per feature file.
    """
    crm = feature_by_path(sample_result_set, CRM_PATH)
    first_half = copy.deepcopy(crm)
    second_half = copy.deepcopy(crm)
    first_half["elements"] = crm["elements"][:4]
    second_half["elements"] = crm["elements"][4:]

    merged = events.merge_result_sets(
        [
            events.new_result_set(metadata={}, features=[first_half]),
            events.new_result_set(metadata={}, features=[second_half]),
        ]
    )

    assert len(merged["features"]) == 1
    assert merged["features"][0]["elements"] == crm["elements"]


def test_merge_keeps_every_background_immediately_before_its_scenario() -> None:
    """The Background/scenario unit survives the sort that orders scenarios.

    Ordering the flat element list by line would collect all four occurrences
    at the front, because they share the Background's own line - which is
    exactly the bug this assertion exists to catch.
    """
    late = events.new_result_set(
        metadata={},
        features=[_feature_with(CRM_PATH, [_background(), _scenario(16)])],
    )
    early = events.new_result_set(
        metadata={},
        features=[_feature_with(CRM_PATH, [_background(), _scenario(9)])],
    )

    merged = events.merge_result_sets([late, early])

    elements = merged["features"][0]["elements"]
    assert [element["type"] for element in elements] == [
        events.ELEMENT_TYPE_BACKGROUND,
        events.ELEMENT_TYPE_SCENARIO,
        events.ELEMENT_TYPE_BACKGROUND,
        events.ELEMENT_TYPE_SCENARIO,
    ]
    assert [element["line"] for element in elements] == [
        CRM_BACKGROUND_LINE,
        9,
        CRM_BACKGROUND_LINE,
        16,
    ]


def test_merge_is_identical_whatever_order_the_shards_arrive_in() -> None:
    """The merged structure may not depend on how the work was sharded.

    A process pool hands its results back in completion order, which is not
    reproducible, so every permutation of the same shards must merge to the
    same document.  All six orderings of three shards are checked rather than a
    sampled shuffle, which makes the assertion exhaustive instead of likely.
    """
    shards = [
        events.new_result_set(
            metadata={}, features=[_feature_with(SALES_PATH, [_scenario(36)])]
        ),
        events.new_result_set(
            metadata={},
            features=[_feature_with(CRM_PATH, [_background(), _scenario(16)])],
        ),
        events.new_result_set(
            metadata={},
            features=[
                _feature_with(CRM_PATH, [_background(), _scenario(9)]),
                _feature_with(CONTACT_PATH, [_scenario(19)], name="Contact"),
            ],
        ),
    ]

    merged = [
        events.merge_result_sets(list(order))
        for order in itertools.permutations(shards)
    ]

    assert all(document == merged[0] for document in merged)
    assert [feature["path"] for feature in merged[0]["features"]] == [
        CONTACT_PATH,
        CRM_PATH,
        SALES_PATH,
    ]


def test_merge_combines_the_run_level_fields_by_their_own_rules() -> None:
    """``dry_run`` ors, ``started_at`` minimises, ``generated_at`` maximises.

    All shards share a configuration in practice, so these rules only settle
    the pathological case - but ``started_at`` is what a report prints as the
    run's start, and taking the wrong shard's value would misreport it.
    """
    early = events.format_timestamp(GOLDEN_INSTANT)
    late = events.format_timestamp(GOLDEN_INSTANT + timedelta(minutes=5))

    merged = events.merge_result_sets(
        [
            events.new_result_set(
                dry_run=False,
                metadata={},
                started_at=late,
                generated_at=early,
                tag_expression=None,
            ),
            events.new_result_set(
                dry_run=True,
                metadata={"os": {"name": "Linux"}},
                started_at=early,
                generated_at=late,
                tag_expression="@Smoke",
            ),
        ]
    )

    assert merged["schema_version"] == events.SCHEMA_VERSION
    assert merged["dry_run"] is True
    assert merged["started_at"] == early
    assert merged["generated_at"] == late
    assert merged["tag_expression"] == "@Smoke"
    assert merged["metadata"] == {"os": {"name": "Linux"}}


def test_merge_falls_back_to_the_scenarios_own_start_timestamps() -> None:
    """A shard with no run-level stamp still yields a ``started_at``.

    A hand-built shard - and this suite writes several - carries scenarios
    without the envelope's timestamp, and a report whose start time was ``null``
    would render an empty metadata block.
    """
    merged = events.merge_result_sets(
        [
            events.new_result_set(
                metadata={},
                features=[_feature_with(CRM_PATH, [_scenario(16), _scenario(9)])],
            )
        ]
    )

    assert merged["started_at"] == events.format_timestamp(
        GOLDEN_INSTANT + timedelta(seconds=9)
    )


def test_merge_of_no_documents_is_an_empty_document() -> None:
    """Deciding that a run produced nothing belongs to the caller.

    ``app/cli.py`` owns the exit table; the merge returning a well-formed empty
    document is what lets the writers produce the empty-state artifacts rather
    than crash.
    """
    merged = events.merge_result_sets([])

    assert_keys(merged, RESULT_SET_KEYS, "empty merge")
    assert merged["features"] == []
    assert merged["started_at"] is None
    assert merged["generated_at"] is None
    assert merged["dry_run"] is False
    assert merged["metadata"] == {}


def test_merge_reads_no_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """The merge must be pure, or it could not be verified deterministic.

    ``generated_at`` comes from the latest shard's own stamp rather than a
    fresh reading.  The module's sole clock reading is replaced with something
    that raises, so a reading added later fails here loudly.
    """

    def _forbidden() -> datetime:  # pragma: no cover - must never be called
        """Fail loudly instead of returning an instant.

        :returns: Never returns.
        :raises AssertionError: Always.
        """
        raise AssertionError("merge_result_sets must not read a clock")

    monkeypatch.setattr(events, "_utc_now", _forbidden)
    stamp = events.format_timestamp(GOLDEN_INSTANT)

    merged = events.merge_result_sets(
        [
            events.new_result_set(
                metadata={},
                generated_at=stamp,
                features=[_feature_with(CRM_PATH, [_scenario(9)])],
            )
        ]
    )

    assert merged["generated_at"] == stamp


def test_merge_never_mutates_its_inputs() -> None:
    """A writer that annotates what it reads must not reach into a shard.

    Elements are deep-copied into the result, so the two documents share no
    mutable state at all.
    """
    shard = events.new_result_set(
        metadata={}, features=[_feature_with(CRM_PATH, [_background(), _scenario(9)])]
    )
    before = copy.deepcopy(shard)

    merged = events.merge_result_sets([shard])
    merged["features"][0]["elements"][1]["name"] = "annotated by a writer"

    assert shard == before


def test_merge_keeps_two_features_that_merely_share_an_id_apart(
    sample_result_set: dict[str, Any],
) -> None:
    """``Contact`` and ``Inventory`` share a title, and must stay two features.

    The collision is source behaviour the port preserves; merging on ``id``
    would fuse two unrelated feature files into one and silently lose a whole
    feature's worth of results.
    """
    contact = feature_by_path(sample_result_set, CONTACT_PATH)
    inventory = feature_by_path(sample_result_set, INVENTORY_PATH)
    assert contact["id"] == inventory["id"]

    merged = events.merge_result_sets(
        [
            events.new_result_set(metadata={}, features=[contact]),
            events.new_result_set(metadata={}, features=[inventory]),
        ]
    )

    assert [feature["path"] for feature in merged["features"]] == [
        CONTACT_PATH,
        INVENTORY_PATH,
    ]


def test_merge_keys_on_path_then_uri_then_name() -> None:
    """The merge identity falls back the way a damaged shard needs it to.

    A shard written by an older build, or by hand, may carry only a ``uri`` or
    only a ``name``; keying on what is there beats dropping the feature.
    """
    by_uri = [
        {"uri": f"file:{SALES_PATH}", "elements": [_scenario(36)]},
        {"uri": f"file:{SALES_PATH}", "elements": [_scenario(12)]},
    ]
    by_name = [
        {"name": CRM_FEATURE_NAME, "elements": [_scenario(9)]},
        {"name": CRM_FEATURE_NAME, "elements": [_scenario(16)]},
    ]

    merged = events.merge_result_sets(
        [
            events.new_result_set(metadata={}, features=[by_uri[0], by_name[0]]),
            events.new_result_set(metadata={}, features=[by_uri[1], by_name[1]]),
        ]
    )

    assert len(merged["features"]) == 2
    assert all(len(feature["elements"]) == 2 for feature in merged["features"])


def test_merge_ignores_documents_and_features_that_are_not_mappings() -> None:
    """A shard file may contain anything; the merge survives all of it.

    The merge runs after every worker has exited, when a failed worker's file
    is the only evidence left, so it tolerates junk rather than raising and
    losing the workers that succeeded.
    """
    merged = events.merge_result_sets(
        [
            "not a document",  # type: ignore[list-item]
            None,  # type: ignore[list-item]
            {"features": ["not a feature", None]},
            events.new_result_set(
                metadata={}, features=[_feature_with(CRM_PATH, [_scenario(9)])]
            ),
        ]
    )

    assert [feature["path"] for feature in merged["features"]] == [CRM_PATH]


def test_merge_keeps_a_trailing_background_without_a_scenario() -> None:
    """A background with no scenario after it is kept, not dropped.

    Pathological - a worker killed between the two elements - and the reason
    the grouping helper treats a lone occurrence as a unit of its own: a lost
    element would silently shorten a feature.
    """
    merged = events.merge_result_sets(
        [
            events.new_result_set(
                metadata={},
                features=[_feature_with(CRM_PATH, [_scenario(9), _background()])],
            )
        ]
    )

    assert [element["type"] for element in merged["features"][0]["elements"]] == [
        events.ELEMENT_TYPE_BACKGROUND,
        events.ELEMENT_TYPE_SCENARIO,
    ]


def test_merge_warns_when_a_shard_declares_a_foreign_schema_version(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A stale shard is diagnosable rather than fatal.

    The merge runs at the end of a run that has already cost minutes; refusing
    the whole document over a version field would throw that away, so it is
    reported and merged.
    """
    shard = events.new_result_set(
        metadata={}, features=[_feature_with(CRM_PATH, [_scenario(9)])]
    )
    shard["schema_version"] = FOREIGN_SCHEMA_VERSION

    with caplog.at_level(logging.WARNING, logger=events.__name__):
        merged = events.merge_result_sets([shard])

    assert merged["schema_version"] == events.SCHEMA_VERSION
    assert any("schema version" in record.getMessage() for record in caplog.records)


# ==========================================================================
# Driving the real formatter through behave's callback protocol
#
# Everything below exercises ResultCollectorFormatter itself.  Nothing here
# needs a browser, a feature file or the behave command line: the stand-ins
# above carry exactly the attributes the collector reads, and the two
# published seams - the ``clock`` attribute and ``read_source_lines`` - replace
# the wall clock and every file read.
# ==========================================================================


def test_collector_writes_a_complete_document_at_the_opener_path(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    crm_feature: StubFeature,
) -> None:
    """One full pass: the document lands at ``-o``'s path, fully stamped.

    ``app/services/test_run_service.py`` gives every worker a distinct ``-o``
    path and then reads that exact file back, so a document written anywhere
    else - or written without its run-level stamps - is a shard the merge never
    sees.
    """
    clock = SteppedClock()
    collector = make_collector(clock=clock, tags=["@Smoke"], source_lines=[])
    scenario = StubScenario(
        "User can create pipeline in the displayed dashboard",
        CRM_PASSING_SCENARIO_LINE,
        tags=[Tag("Smoke", 1)],
        steps=[
            StubStep(
                "When",
                "User click on the crm dashboard",
                10,
                status=Status.passed,
                duration=2.618,
                func=make_step_function(
                    "features/steps/crm_steps.py", "user_click_on_the_crm_dashboard"
                ),
            )
        ],
        background_steps=[
            StubStep(
                "Given",
                "User login to test other features",
                7,
                status=Status.passed,
                duration=GOLDEN_DURATION_SECONDS,
                func=make_step_function(
                    "features/steps/session_steps.py",
                    "user_login_to_test_other_features",
                ),
            )
        ],
    )

    run_feature(collector, crm_feature, [scenario])
    document = read_document(collector)

    assert_keys(document, RESULT_SET_KEYS, "collected document")
    assert document["schema_version"] == events.SCHEMA_VERSION
    assert document["dry_run"] is False
    assert document["tag_expression"] == "@Smoke"
    assert set(document["metadata"]) == set(METADATA_KEYS)
    # Two readings: the scenario's start, then the stamp at close.
    assert document["started_at"] == events.format_timestamp(clock.readings[0])
    assert document["generated_at"] == events.format_timestamp(clock.readings[-1])

    feature = feature_by_path(document, CRM_PATH)
    assert_keys(feature, FEATURE_KEYS, "collected feature")
    assert feature["uri"] == f"file:{CRM_PATH}"
    assert feature["id"] == CRM_FEATURE_ID
    assert feature["tags"] == [events.feature_tag("@Smoke", 1, 1)]


def test_collector_interleaves_a_background_occurrence_before_every_scenario(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    crm_feature: StubFeature,
) -> None:
    """behave announces the Background once; the document carries one per
    scenario.

    The JVM's ``handleTestCaseStarted`` adds a fresh background map before every
    test case, which is why the reference report's eight elements are four
    backgrounds and four scenarios.  Emitting behave's single announcement
    instead would leave every scenario but the first without its precondition.
    """
    collector = make_collector(source_lines=[])
    scenarios = [
        StubScenario(
            f"scenario {line}",
            line,
            background_steps=[
                StubStep("Given", "User login to test other features", 7)
            ],
        )
        for line in (CRM_PASSING_SCENARIO_LINE, CRM_FAILING_SCENARIO_LINE)
    ]

    run_feature(collector, crm_feature, scenarios)
    feature = feature_by_path(read_document(collector), CRM_PATH)

    assert [element["type"] for element in feature["elements"]] == [
        events.ELEMENT_TYPE_BACKGROUND,
        events.ELEMENT_TYPE_SCENARIO,
        events.ELEMENT_TYPE_BACKGROUND,
        events.ELEMENT_TYPE_SCENARIO,
    ]
    for element in feature["elements"]:
        if element["type"] == events.ELEMENT_TYPE_BACKGROUND:
            assert set(element) == set(ELEMENT_SHARED_KEYS)
            assert element["keyword"] == "Background"
            assert element["line"] == CRM_BACKGROUND_LINE


def test_collector_splits_announced_steps_between_background_and_scenario(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    crm_feature: StubFeature,
) -> None:
    """behave announces one flat sequence; the split is by background count.

    The first ``len(scenario.background_steps)`` announcements belong to the
    Background occurrence and the rest to the scenario.  Getting the boundary
    wrong would move a precondition step into the test case, which is visible
    in every artifact.
    """
    collector = make_collector(source_lines=[])
    background_steps = [
        StubStep("Given", "first precondition", 7, status=Status.passed, duration=1.0),
        StubStep("And", "second precondition", 8, status=Status.passed, duration=1.0),
    ]
    own_steps = [
        StubStep("When", "the action", 10, status=Status.passed, duration=1.0),
        StubStep("Then", "the outcome", 11, status=Status.passed, duration=1.0),
    ]
    scenario = StubScenario(
        "User can create pipeline in the displayed dashboard",
        CRM_PASSING_SCENARIO_LINE,
        steps=own_steps,
        background_steps=background_steps,
    )

    run_feature(collector, crm_feature, [scenario])
    feature = feature_by_path(read_document(collector), CRM_PATH)
    background, element = feature["elements"]

    assert [step["name"] for step in background["steps"]] == [
        "first precondition",
        "second precondition",
    ]
    assert [step["name"] for step in element["steps"]] == ["the action", "the outcome"]
    assert [step["keyword"] for step in element["steps"]] == ["When ", "Then "]
    assert all(step["result"]["status"] == "passed" for step in background["steps"])


def test_all_steps_land_in_the_scenario_when_the_background_count_is_unavailable(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    crm_feature: StubFeature,
) -> None:
    """A model that cannot report its background steps loses no step.

    Defensive, and the choice matters: attributing an unknown number of steps
    to the Background would put scenario steps in the wrong element, whereas
    attributing all of them to the scenario keeps every result in the test case
    that produced it.
    """

    class UncooperativeScenario(StubScenario):
        """A scenario whose ``background_steps`` cannot be read."""

        @property
        def background_steps(self) -> list[StubStep]:
            """Raise instead of answering, as a broken model would.

            :returns: Never returns.
            :raises RuntimeError: Always.
            """
            raise RuntimeError("background steps unavailable")

    collector = make_collector(source_lines=[])
    scenario = UncooperativeScenario(
        "scenario", CRM_PASSING_SCENARIO_LINE,
        steps=[StubStep("When", "the action", 10, status=Status.passed, duration=1.0)],
    )

    run_feature(collector, crm_feature, [scenario])
    feature = feature_by_path(read_document(collector), CRM_PATH)
    background, element = feature["elements"]

    assert background["steps"] == []
    assert [step["name"] for step in element["steps"]] == ["the action"]


def test_match_location_is_the_dotted_step_function_path(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    crm_feature: StubFeature,
) -> None:
    """AAP deviation 8: module plus qualified name, and nothing else.

    Java recorded
    ``com.testinium.step_definitions.Crm.method(java.lang.String)``; no
    analogue exists in Python, so the field's *shape and role* are preserved -
    a stable identifier of the code that ran - with no parentheses and no
    parameter types.  It must never be behave's own
    ``features/steps/crm_steps.py:61``, which is a source location rather than
    an identifier and which the HTML reports would render as a broken link.

    The function is built the way behave builds one - ``exec``'d with a globals
    mapping carrying no ``__name__`` - so this exercises the derivation from
    the code object's filename, which is the only route production ever takes.
    """
    collector = make_collector(source_lines=[])
    func = make_step_function(
        "features/steps/crm_steps.py", "user_click_on_the_crm_dashboard"
    )
    assert func.__module__ is None, "behave's step functions carry no __module__"
    scenario = StubScenario(
        "scenario",
        CRM_PASSING_SCENARIO_LINE,
        steps=[
            StubStep(
                "When", "User click on the crm dashboard", 10,
                status=Status.passed, duration=2.618, func=func,
            )
        ],
    )

    run_feature(collector, crm_feature, [scenario])
    feature = feature_by_path(read_document(collector), CRM_PATH)
    step = feature["elements"][-1]["steps"][0]

    assert step["matched"] is True
    assert (
        step["match"]["location"]
        == "features.steps.crm_steps.user_click_on_the_crm_dashboard"
    )
    assert "(" not in step["match"]["location"]
    assert ":" not in step["match"]["location"]
    assert ".py" not in step["match"]["location"]
    assert "arguments" not in step["match"], (
        "a parameterless step carries no arguments key"
    )


def test_match_location_uses_a_real_module_name_when_there_is_one(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    crm_feature: StubFeature,
) -> None:
    """A hook or helper that *was* imported is described by ``__module__``.

    Both routes have to work: ``features/environment.py``'s hooks are imported
    normally, while step modules are ``exec``'d, and the field means the same
    thing for both.
    """
    collector = make_collector(source_lines=[])

    def imported_step(context: Any) -> None:
        """A step function defined the ordinary way, with a ``__module__``.

        :param context: behave's context, unused.
        """

    scenario = StubScenario(
        "scenario",
        CRM_PASSING_SCENARIO_LINE,
        steps=[
            StubStep(
                "Then", "the outcome", 11,
                status=Status.passed, duration=1.0, func=imported_step,
            )
        ],
    )

    run_feature(collector, crm_feature, [scenario])
    feature = feature_by_path(read_document(collector), CRM_PATH)

    location = feature["elements"][-1]["steps"][0]["match"]["location"]
    assert location == f"{__name__}.{imported_step.__qualname__}"


@pytest.mark.parametrize(
    ("source_path", "expected_prefix"),
    [
        ("features/steps/__init__.py", "features.steps."),
        ("<string>", ""),
    ],
)
def test_match_location_handles_unusual_step_sources(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    crm_feature: StubFeature,
    source_path: str,
    expected_prefix: str,
) -> None:
    """A package marker loses its ``__init__``; a source-less callable keeps
    only its name.

    Neither is common, and neither may produce a location containing a path
    separator or a ``.py`` suffix - a report field that sometimes holds a
    filename and sometimes an identifier is unusable to any consumer.
    """
    collector = make_collector(source_lines=[])
    func = make_step_function(source_path, "shared_step")
    scenario = StubScenario(
        "scenario",
        CRM_PASSING_SCENARIO_LINE,
        steps=[
            StubStep(
                "Given", "a step", 7, status=Status.passed, duration=1.0, func=func
            )
        ],
    )

    run_feature(collector, crm_feature, [scenario])
    feature = feature_by_path(read_document(collector), CRM_PATH)

    location = feature["elements"][-1]["steps"][0]["match"]["location"]
    assert location == f"{expected_prefix}shared_step"
    assert "/" not in location
    assert ".py" not in location


def test_match_arguments_carry_the_reference_offsets(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    crm_feature: StubFeature,
) -> None:
    """The reference's three arguments, produced by the collector itself.

    behave reports the spans one character inside each quote; the recorded
    values are widened over the quotes and the offsets moved back by one, which
    is the JVM's shape.  ``match.arguments`` is the field AAP 0.6 flags as
    derived from the schema rather than observed, so this is where its shape is
    pinned.
    """
    collector = make_collector(source_lines=[])
    scenario = StubScenario(
        "User can change information in dashboard",
        CRM_OUTLINE_FIRST_ROW_LINE,
        keyword="Scenario Outline",
        steps=[
            StubStep(
                "And", GOLDEN_STEP_NAME, 21,
                status=Status.passed, duration=1.45,
                func=make_step_function(
                    "features/steps/crm_steps.py",
                    "user_can_change_any_user_s_information",
                ),
                arguments=golden_arguments(),
            )
        ],
    )

    run_feature(collector, crm_feature, [scenario])
    feature = feature_by_path(read_document(collector), CRM_PATH)
    arguments = feature["elements"][-1]["steps"][0]["match"]["arguments"]

    assert [argument["val"] for argument in arguments] == list(GOLDEN_ARGUMENT_VALUES)
    assert [argument["offset"] for argument in arguments] == list(
        GOLDEN_ARGUMENT_OFFSETS
    )
    for argument in arguments:
        assert set(argument) == {"val", "offset"}
        assert (
            GOLDEN_STEP_NAME[
                argument["offset"] : argument["offset"] + len(argument["val"])
            ]
            == argument["val"]
        )


def test_match_arguments_keep_a_valueless_entry_and_recover_a_lost_span(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    crm_feature: StubFeature,
) -> None:
    """The JVM never drops an argument entry, and neither does the collector.

    An argument with no value is recorded as an empty mapping - the JVM's
    ``createMatchMap`` does the same - so the *position* of each parameter
    survives.  An argument whose span does not index into the step name (a
    type-converted parameter) is recorded from its original text rather than
    discarded, because the matched text is still known.
    """
    collector = make_collector(source_lines=[])
    step_name = "User can find his name from search bar"
    scenario = StubScenario(
        "scenario",
        SALES_UNDEFINED_SCENARIO_LINE,
        steps=[
            StubStep(
                "Then", step_name,
                14, status=Status.passed, duration=1.0,
                func=make_step_function(
                    "features/steps/sales_steps.py", "user_can_find_his_name"
                ),
                arguments=[
                    Argument(0, 0, None, None),
                    Argument(500, 505, "Lucas", "Lucas"),
                ],
            )
        ],
    )

    run_feature(collector, crm_feature, [scenario])
    feature = feature_by_path(read_document(collector), CRM_PATH)
    arguments = feature["elements"][-1]["steps"][0]["match"]["arguments"]

    assert arguments[0] == {}
    assert arguments[1] == {"val": "Lucas", "offset": 500}


def test_undefined_step_records_no_location_at_all(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    crm_feature: StubFeature,
) -> None:
    """behave's ``NoMatch`` carries ``func=None``, and yields ``match == {}``.

    ``func`` is the only reliable discriminator - ``NoMatch`` is a ``Match``
    subclass - and the empty mapping is what lets the JSON writer omit
    ``location`` exactly as the JVM does for an undefined step.
    """
    collector = make_collector(source_lines=[])
    scenario = StubScenario(
        "Verify that the user's search finds his name",
        SALES_UNDEFINED_SCENARIO_LINE,
        steps=[
            StubStep(
                "Then", "User can search the customer from the search bar", 14,
                status=Status.undefined, duration=0.0, func=None,
            )
        ],
    )

    run_feature(collector, crm_feature, [scenario])
    feature = feature_by_path(read_document(collector), CRM_PATH)
    step = feature["elements"][-1]["steps"][0]

    assert step["matched"] is False
    assert step["match"] == {}
    assert step["result"]["status"] == "undefined"


def test_a_buffered_match_is_applied_to_its_own_step(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    crm_feature: StubFeature,
) -> None:
    """The match/result pair is attributed by pairing, not by position.

    behave's callback carries no reference to the step it belongs to, and under
    ``--dry-run`` it emits the pair only for steps it could match.  A
    positional cursor would therefore hand the second step's definition to the
    first as soon as one step is undefined - which is exactly the arrangement
    below.
    """
    collector = make_collector(source_lines=[])
    resolved = make_step_function(
        "features/steps/crm_steps.py", "user_click_on_the_pipeline_button"
    )
    scenario = StubScenario(
        "scenario",
        CRM_PASSING_SCENARIO_LINE,
        steps=[
            StubStep(
                "When", "an undefined step", 10, status=Status.undefined,
                duration=0.0, func=None,
            ),
            StubStep(
                "Then", "User click on the pipeline button", 11,
                status=Status.passed, duration=2.975, func=resolved,
            ),
        ],
    )

    run_feature(collector, crm_feature, [scenario])
    feature = feature_by_path(read_document(collector), CRM_PATH)
    first, second = feature["elements"][-1]["steps"]

    assert first["match"] == {}
    assert first["matched"] is False
    assert (
        second["match"]["location"]
        == "features.steps.crm_steps.user_click_on_the_pipeline_button"
    )


def test_never_executed_steps_are_finalised_from_the_step_and_the_registry(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    crm_feature: StubFeature,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A skipped step gets its status from behave and its location from the
    registry.

    behave emits neither callback for a step it never runs, yet the reference
    reports both for a step skipped after a failure.  The registry is
    substituted rather than loaded, so this assertion does not depend on
    whether another test module has populated the real one.
    """
    resolved = make_step_function(
        "features/steps/crm_steps.py", "user_can_see_the_new_changes_in_progress"
    )
    registry = StubStepRegistry(match=Match(resolved))
    monkeypatch.setattr(events, "behave_step_registry", registry)

    collector = make_collector(source_lines=[])
    failing = StubStep(
        "And", "User click on the crm dashboard", 18,
        status=Status.failed, duration=4.211,
        error_message="The title is not same as the expected!",
        func=make_step_function(
            "features/steps/crm_steps.py", "user_click_on_the_crm_dashboard"
        ),
    )
    never_run = StubStep(
        "Then", "User can see the new changes in progress", 19,
        status=Status.skipped, duration=None, executed=False,
    )
    scenario = StubScenario(
        "User can change the situation in progress",
        CRM_FAILING_SCENARIO_LINE,
        steps=[failing, never_run],
    )

    run_feature(collector, crm_feature, [scenario])
    feature = feature_by_path(read_document(collector), CRM_PATH)
    _, skipped = feature["elements"][-1]["steps"]

    assert registry.queried == [never_run]
    assert skipped["result"] == {"status": "skipped", "duration": 0}
    assert skipped["matched"] is True
    assert (
        skipped["match"]["location"]
        == "features.steps.crm_steps.user_can_see_the_new_changes_in_progress"
    )


@pytest.mark.parametrize(
    "registry",
    [
        StubStepRegistry(match=None),
        StubStepRegistry(error=RuntimeError("registry unavailable")),
    ],
    ids=["no-definition", "lookup-failed"],
)
def test_an_unresolvable_never_executed_step_keeps_an_empty_match(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    crm_feature: StubFeature,
    monkeypatch: pytest.MonkeyPatch,
    registry: StubStepRegistry,
) -> None:
    """No definition, or a failed lookup, leaves ``match`` empty.

    A location must never be invented: the JSON writer omits the field for an
    unmatched step, and a guessed value would claim a function ran that the
    run never reached.  A registry that raises must not take the document with
    it either - the outcome is still recorded.
    """
    monkeypatch.setattr(events, "behave_step_registry", registry)
    collector = make_collector(source_lines=[])
    scenario = StubScenario(
        "scenario",
        CRM_PASSING_SCENARIO_LINE,
        steps=[
            StubStep(
                "Then", "a phrase with no definition in this port", 19,
                status=Status.skipped, executed=False,
            )
        ],
    )

    run_feature(collector, crm_feature, [scenario])
    feature = feature_by_path(read_document(collector), CRM_PATH)
    step = feature["elements"][-1]["steps"][0]

    assert step["matched"] is False
    assert step["match"] == {}
    assert step["result"]["status"] == "skipped"


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (Status.passed, "passed"),
        (Status.failed, "failed"),
        (Status.skipped, "skipped"),
        (Status.undefined, "undefined"),
        (Status.untested, "untested"),
        (Status.untested_undefined, "undefined"),
        (Status.untested_pending, "pending"),
        (Status.pending, "pending"),
        (Status.error, "error"),
        ("ambiguous", "ambiguous"),
        (None, "untested"),
    ],
)
def test_step_status_vocabulary_is_behaves_normalised_name(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    crm_feature: StubFeature,
    status: Any,
    expected: str,
) -> None:
    """Every status a step can reach, including the ones the fixture cannot
    show.

    behave's ``normalized_name`` folds ``untested_undefined`` to ``undefined``
    and both pending variants to ``pending``, which is the vocabulary the
    writers map from.  ``pending``, ``untested`` and an ``ambiguous`` reported
    as a plain string are not present in ``sample_results.json``, so this is
    their only coverage.  No dry-run and no tag-filter mapping is applied here:
    those belong to the writers, which have ``dry_run`` and ``selected`` to
    work from.  ``None`` yields behave's own initial status rather than a null,
    so the field is never absent.
    """
    collector = make_collector(source_lines=[])
    scenario = StubScenario(
        "scenario",
        CRM_PASSING_SCENARIO_LINE,
        steps=[
            StubStep(
                "Given", "a step", 7, status=status, duration=0.067,
                func=make_step_function("features/steps/session_steps.py", "a_step"),
            )
        ],
    )

    run_feature(collector, crm_feature, [scenario])
    feature = feature_by_path(read_document(collector), CRM_PATH)

    assert feature["elements"][-1]["steps"][0]["result"]["status"] == expected


def test_durations_are_nanosecond_integers_through_the_formatter(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    crm_feature: StubFeature,
) -> None:
    """behave's float seconds never reach the document.

    The reference's ``30202000000`` is 30.202 seconds.  A float here would
    reach ``target/cucumber.json`` and the Jenkins publisher reads that file.
    """
    collector = make_collector(source_lines=[])
    scenario = StubScenario(
        "scenario",
        CRM_PASSING_SCENARIO_LINE,
        steps=[
            StubStep(
                "Given", "User login to test other features", 7,
                status=Status.passed, duration=GOLDEN_DURATION_SECONDS,
                func=make_step_function(
                    "features/steps/session_steps.py",
                    "user_login_to_test_other_features",
                ),
            ),
            StubStep(
                "When", "a step behave timed as zero", 8,
                status=Status.passed, duration=0.0,
                func=make_step_function("features/steps/session_steps.py", "zero"),
            ),
        ],
    )

    run_feature(collector, crm_feature, [scenario])
    feature = feature_by_path(read_document(collector), CRM_PATH)
    first, second = feature["elements"][-1]["steps"]

    assert first["result"]["duration"] == GOLDEN_DURATION_NANOS
    assert isinstance(first["result"]["duration"], int)
    # Zero is recorded faithfully; whether to emit the key is the writer's
    # decision, because the JVM emits it only when non-zero.
    assert second["result"]["duration"] == 0


def test_error_message_is_normalised_to_lf_and_keeps_the_assertion_text(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    crm_feature: StubFeature,
) -> None:
    """The failure text carries the assertion's own message, with LF endings.

    AAP deviation 16: the reference's failure text is a JUnit message and a
    Java stack trace with ``\\r\\n`` endings, which Python cannot produce, so
    the *subject and message* are parity and their formatting is not.  This
    asserts only what is settled - the assertion's own text survives and no
    carriage return does - and deliberately does not pin behave's
    ``ASSERT FAILED:`` prefix either way, because the schema owner is changing
    how the message is rebuilt from the exception and its traceback.
    """
    collector = make_collector(source_lines=[])
    assertion_text = "The title is not same as the expected!"
    scenario = StubScenario(
        "User can change the situation in progress",
        CRM_FAILING_SCENARIO_LINE,
        steps=[
            StubStep(
                "And", "User click on the crm dashboard", 18,
                status=Status.failed, duration=4.211,
                error_message=(
                    f"ASSERT FAILED: {assertion_text}\r\n"
                    "Traceback (most recent call last):\r\n"
                    '  File "features/steps/crm_steps.py", line 61\r'
                ),
                func=make_step_function(
                    "features/steps/crm_steps.py", "user_click_on_the_crm_dashboard"
                ),
            )
        ],
    )

    run_feature(collector, crm_feature, [scenario])
    feature = feature_by_path(read_document(collector), CRM_PATH)
    message = feature["elements"][-1]["steps"][0]["result"]["error_message"]

    assert "\r" not in message
    assert message.count("\n") == 3
    assert assertion_text in message


def test_a_step_without_a_failure_message_carries_no_error_key(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    crm_feature: StubFeature,
) -> None:
    """``error_message`` is present only when there is one.

    The writers decide how to render a failure; a key carrying an empty string
    would make every passing step look like a failure with no detail.
    """
    collector = make_collector(source_lines=[])
    scenario = StubScenario(
        "scenario",
        CRM_PASSING_SCENARIO_LINE,
        steps=[
            StubStep(
                "Given", "a step", 7, status=Status.passed, duration=1.0,
                error_message="",
                func=make_step_function("features/steps/session_steps.py", "a_step"),
            )
        ],
    )

    run_feature(collector, crm_feature, [scenario])
    feature = feature_by_path(read_document(collector), CRM_PATH)

    assert "error_message" not in feature["elements"][-1]["steps"][0]["result"]


# ==========================================================================
# Attachments: the after-hook entry a screenshot lands in
# ==========================================================================


def _scenario_under_way(
    collector: events.ResultCollectorFormatter,
    feature: StubFeature,
    scenario: StubScenario | None = None,
) -> StubScenario:
    """Announce ``feature`` and one scenario, leaving the scenario current.

    An attachment is only meaningful while a scenario is being collected, so
    every attachment test needs this state and none of them needs any steps.

    :param collector: The formatter under test.
    :param feature: The feature to announce.
    :param scenario: The scenario to announce; a plain one by default.
    :returns: The announced scenario.
    """
    announced = scenario or StubScenario(
        "User can change the situation in progress", CRM_FAILING_SCENARIO_LINE
    )
    collector.uri(feature.filename)
    collector.feature(feature)
    collector.scenario(announced)
    return announced


def test_embedding_base64_encodes_bytes_and_names_it_after_the_scenario(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    crm_feature: StubFeature,
) -> None:
    """behave's ``context.attach`` route, end to end.

    ``features/environment.py`` calls ``context.attach(mime_type, data)`` and
    behave forwards it to every formatter carrying an ``embedding`` method.
    behave has **no** name parameter, so the collector supplies the third
    argument of ``Hooks.java:15``'s ``scenario.attach(screenshot, "image/png",
    scenario.getName())`` from the scenario it is already tracking - which is
    why the embedding is named after the scenario and not after the step.
    """
    collector = make_collector(source_lines=[])
    scenario = _scenario_under_way(collector, crm_feature)

    collector.embedding(PNG_MIME_TYPE, SCREENSHOT_PNG)
    document = read_document(collector)

    element = feature_by_path(document, CRM_PATH)["elements"][-1]
    embedding = element["after"][0]["embeddings"][0]
    assert embedding["mime_type"] == PNG_MIME_TYPE
    assert embedding["name"] == scenario.name
    assert base64.b64decode(embedding["data"], validate=True) == SCREENSHOT_PNG


@pytest.mark.parametrize(
    "payload",
    [SCREENSHOT_PNG, bytearray(SCREENSHOT_PNG), memoryview(SCREENSHOT_PNG)],
    ids=["bytes", "bytearray", "memoryview"],
)
def test_embedding_accepts_every_bytes_like_payload(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    crm_feature: StubFeature,
    payload: Any,
) -> None:
    """Any bytes-like screenshot encodes identically.

    ``driver.get_screenshot_as_png()`` returns ``bytes``, but a caller that
    sliced or buffered the payload must not end up with ``"bytearray(b'...')"``
    in the document - which is what a bare ``str()`` would produce.
    """
    collector = make_collector(source_lines=[])
    _scenario_under_way(collector, crm_feature)

    collector.embedding(PNG_MIME_TYPE, payload)
    document = read_document(collector)

    element = feature_by_path(document, CRM_PATH)["elements"][-1]
    assert element["after"][0]["embeddings"][0]["data"] == base64.b64encode(
        SCREENSHOT_PNG
    ).decode("ascii")


def test_embedding_passes_an_already_encoded_payload_through(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    crm_feature: StubFeature,
) -> None:
    """A string payload is taken to be base64 already, and a missing MIME type
    gets the generic one.

    ``app/reporting/screenshots.py`` returns base64 text, so re-encoding it
    would double-encode the image and ``lightbox.html`` would render nothing.
    """
    collector = make_collector(source_lines=[])
    _scenario_under_way(collector, crm_feature)
    encoded = base64.b64encode(SCREENSHOT_PNG).decode("ascii")

    collector.embedding("", encoded)
    document = read_document(collector)

    embedding = feature_by_path(document, CRM_PATH)["elements"][-1]["after"][0][
        "embeddings"
    ][0]
    assert embedding["data"] == encoded
    assert embedding["mime_type"] == "application/octet-stream"


def test_attachments_from_one_hook_share_a_single_entry(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    crm_feature: StubFeature,
) -> None:
    """Two attachments from the same hook are two embeddings, not two entries.

    That grouping is the JVM's: one hook step map carries every attachment the
    hook made.  A second entry for the same location would make the report
    claim the teardown hook ran twice.  The entry's keys are asserted
    individually so that the hook gaining its own status, duration or error
    does not break this.
    """
    collector = make_collector(source_lines=[])
    _scenario_under_way(collector, crm_feature)

    assert collector.add_attachment({"mime_type": PNG_MIME_TYPE, "data": "one"})
    assert collector.add_attachment({"mime_type": PNG_MIME_TYPE, "data": "two"})
    assert events.attach_to_current_scenario(
        {"mime_type": PNG_MIME_TYPE, "data": "three"},
        hook_location="features.environment.after_all",
    )
    document = read_document(collector)

    hooks = feature_by_path(document, CRM_PATH)["elements"][-1]["after"]
    assert len(hooks) == 2
    default_entry, other_entry = hooks
    assert_keys(default_entry, HOOK_ENTRY_KEYS, "default hook entry")
    assert default_entry["match"] == {
        "location": events.DEFAULT_AFTER_HOOK_LOCATION
    }
    assert default_entry["result"]["status"] == "passed"
    assert [embedding["data"] for embedding in default_entry["embeddings"]] == [
        "one",
        "two",
    ]
    assert other_entry["match"] == {"location": "features.environment.after_all"}
    assert [embedding["data"] for embedding in other_entry["embeddings"]] == ["three"]


def test_attach_to_current_scenario_is_false_with_no_active_collector(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Screenshot evidence must never change a test outcome.

    The suite may be running under a different formatter entirely, so a
    ``False`` return is a normal condition rather than an error, and it must
    not raise: ``features/environment.py`` calls this from a failure path that
    is already handling a failure.
    """
    monkeypatch.setattr(events, "_ACTIVE_COLLECTORS", [])

    with caplog.at_level(logging.DEBUG, logger=events.__name__):
        accepted = events.attach_to_current_scenario(
            {"mime_type": PNG_MIME_TYPE, "data": "ignored"}
        )

    assert accepted is False
    assert any("attachment" in record.getMessage() for record in caplog.records)


def test_attach_to_current_scenario_is_false_when_no_scenario_is_current(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    crm_feature: StubFeature,
) -> None:
    """An attachment with nothing to attach to is dropped, not invented.

    Recording it against the feature or a previous scenario would put a
    screenshot next to results it has nothing to do with.
    """
    collector = make_collector(source_lines=[])
    collector.uri(crm_feature.filename)
    collector.feature(crm_feature)

    assert (
        events.attach_to_current_scenario({"mime_type": PNG_MIME_TYPE, "data": "x"})
        is False
    )
    collector.embedding(PNG_MIME_TYPE, SCREENSHOT_PNG)
    document = read_document(collector)

    assert feature_by_path(document, CRM_PATH)["elements"] == []


def test_add_attachment_never_raises_on_a_malformed_embedding(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    crm_feature: StubFeature,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A bad embedding costs the attachment, never the scenario's result.

    The scenario's own outcome is already decided by the time a teardown hook
    attaches anything, so an exception here would turn a recorded failure into
    a lost one.
    """
    collector = make_collector(source_lines=[])
    _scenario_under_way(collector, crm_feature)

    with caplog.at_level(logging.ERROR, logger=events.__name__):
        accepted = collector.add_attachment("not a mapping")  # type: ignore[arg-type]

    assert accepted is False
    assert any(
        "attachment could not be recorded" in record.getMessage()
        for record in caplog.records
    )
    document = read_document(collector)
    element = feature_by_path(document, CRM_PATH)["elements"][-1]
    # No embedding is recorded anywhere.  The hook entry the failed call had
    # already opened is left in place and empty, which is accurate - the hook
    # did run - and is asserted rather than assumed away, because a *partial*
    # embedding reaching the document is the failure mode that would matter:
    # ``lightbox.html`` renders ``data:<mime_type>;base64,<data>``.
    assert all(entry["embeddings"] == [] for entry in element["after"])
    assert element["steps"] == []


# ==========================================================================
# Configuration, selection and source recovery
# ==========================================================================


@pytest.mark.parametrize(
    ("tags", "default_tags", "expected"),
    [
        (["@Smoke"], None, "@Smoke"),
        (["@Smoke", "not @wip"], None, "@Smoke not @wip"),
        ("@Smoke", None, "@Smoke"),
        (("@Smoke",), None, "@Smoke"),
        (None, ["not @wip"], "not @wip"),
        (["@Smoke"], ["not @wip"], "@Smoke"),
        (None, None, None),
    ],
    ids=[
        "single",
        "several-anded",
        "string",
        "tuple",
        "default-only",
        "command-line-wins",
        "no-filter",
    ],
)
def test_tag_expression_is_derived_from_the_config_as_written(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    tags: Any,
    default_tags: Any,
    expected: str | None,
) -> None:
    """The expression is recorded as written, with the command line winning.

    ``behave.ini``'s ``default_tags`` applies only when the command line
    supplied no filter, which is behave's own precedence.  behave's parsed
    ``config.tag_expression`` is deliberately not used: its ``str()`` drops the
    ``@`` sigils, and the report has to show the operator what was actually
    asked for.
    """
    collector = make_collector(tags=tags, default_tags=default_tags)

    assert read_document(collector)["tag_expression"] == expected


def test_dry_run_is_mirrored_and_the_collector_applies_no_status_mapping(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    crm_feature: StubFeature,
) -> None:
    """``dry_run`` reaches the document; the dry-run *mapping* does not.

    Under ``--dry-run`` the JVM emits matched steps ``passed`` and unmatched
    ``undefined`` while behave reports ``untested``.  That mapping belongs to
    ``app/reporting/cucumber_json.py``, which needs this flag to apply it; the
    collector recording ``passed`` here instead would make the intermediate
    document disagree with what behave actually did.
    """
    collector = make_collector(dry_run=True, source_lines=[])
    scenario = StubScenario(
        "scenario",
        CRM_PASSING_SCENARIO_LINE,
        steps=[
            StubStep(
                "Given", "a step", 7, status=Status.untested, duration=None,
                func=make_step_function("features/steps/session_steps.py", "a_step"),
            )
        ],
    )

    run_feature(collector, crm_feature, [scenario])
    document = read_document(collector)

    assert document["dry_run"] is True
    step = feature_by_path(document, CRM_PATH)["elements"][-1]["steps"][0]
    assert step["result"]["status"] == "untested"


def test_an_excluded_scenario_is_recorded_and_its_background_inherits(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    crm_feature: StubFeature,
) -> None:
    """``selected`` is false on the scenario *and* on its Background.

    The Background occurrence exists only for the scenario it precedes, so it
    has to be dropped with it; a background left ``selected`` would appear in
    ``target/cucumber.json`` with no test case to belong to.
    """
    collector = make_collector(source_lines=[])
    scenario = StubScenario(
        "Verify that the user can export the customer list",
        SALES_EXCLUDED_SCENARIO_LINE,
        tags=[Tag("wip", 35)],
        selected=False,
        background_steps=[StubStep("Given", "User login", 7)],
    )

    run_feature(collector, crm_feature, [scenario])
    elements = feature_by_path(read_document(collector), CRM_PATH)["elements"]

    assert all(element["selected"] is False for element in elements)
    assert elements[-1]["tags"] == [{"name": "@Smoke"}, {"name": "@wip"}]


def test_selection_that_cannot_be_answered_assumes_selected(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    crm_feature: StubFeature,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When behave cannot answer, the scenario is recorded as selected.

    Over-reporting a scenario is recoverable - a writer may still drop it -
    while silently dropping a real result is not.
    """
    collector = make_collector(source_lines=[])
    scenario = StubScenario(
        "scenario",
        CRM_PASSING_SCENARIO_LINE,
        selection_error=RuntimeError("tag expression unavailable"),
    )

    with caplog.at_level(logging.DEBUG, logger=events.__name__):
        run_feature(collector, crm_feature, [scenario])
    elements = feature_by_path(read_document(collector), CRM_PATH)["elements"]

    assert elements[-1]["selected"] is True
    assert any(
        "Selection state unavailable" in record.getMessage()
        for record in caplog.records
    )


#: A feature file's source, as :meth:`read_source_lines` would return it:
#: two tags on one line, a two-line indented feature description, and an
#: indented scenario description.  Indentation is the point - behave's parsed
#: model strips it and the JVM preserves it verbatim.
FEATURE_SOURCE: Final[tuple[str, ...]] = (
    "@Smoke @SmokeTest",
    "Feature: Testinium app CRM Module",
    "  Account is: PosManager",
    "  Second description line",
    "",
    "  Background: As a Posmanager",
    "    Given User login to test other features",
    "",
    "  Scenario: User can create pipeline in the displayed dashboard",
    "      indented scenario description",
)


def test_descriptions_and_tag_columns_are_recovered_from_the_source(
    make_collector: Callable[..., events.ResultCollectorFormatter],
) -> None:
    """Indentation and tag columns come from the file, not from the model.

    Two measured details need the raw source, because behave's parsed model
    discards both: a description's leading indentation, which the JVM preserves
    verbatim (``"  Account is: PosManager"``), and a tag's column, which the
    JVM records beside its line.  ``@Smoke`` and ``@SmokeTest`` share line 1
    and the second is a superstring of the first, which is exactly the case a
    naive search would resolve to the wrong column.
    """
    collector = make_collector(source_lines=FEATURE_SOURCE)
    feature = StubFeature(
        CRM_FEATURE_NAME,
        CRM_PATH,
        line=2,
        tags=[Tag("Smoke", 1), Tag("SmokeTest", 1)],
        description=["Account is: PosManager", "Second description line"],
    )
    scenario = StubScenario(
        "User can create pipeline in the displayed dashboard",
        9,
        description=["indented scenario description"],
    )

    run_feature(collector, feature, [scenario])
    collected = feature_by_path(read_document(collector), CRM_PATH)

    assert collected["description"] == (
        "  Account is: PosManager\n  Second description line"
    )
    assert collected["tags"] == [
        events.feature_tag("@Smoke", 1, 1),
        events.feature_tag("@SmokeTest", 1, 8),
    ]
    assert collected["elements"][0]["description"] == (
        "      indented scenario description"
    )


def test_a_description_behave_reports_as_a_string_is_taken_verbatim(
    make_collector: Callable[..., events.ResultCollectorFormatter],
) -> None:
    """behave permits either shape, and a string needs no recovery at all."""
    collector = make_collector(source_lines=FEATURE_SOURCE)
    feature = StubFeature(
        CRM_FEATURE_NAME, CRM_PATH, description="  already one string"
    )

    run_feature(collector, feature, [])

    assert feature_by_path(read_document(collector), CRM_PATH)["description"] == (
        "  already one string"
    )


@pytest.mark.parametrize(
    ("source_lines", "description_lines", "expected"),
    [
        ((), ["Account is: PosManager"], "Account is: PosManager"),
        (
            FEATURE_SOURCE,
            ["a line that is not in the source"],
            "a line that is not in the source",
        ),
        (
            FEATURE_SOURCE,
            ["Account is: PosManager", "also not in the source"],
            "Account is: PosManager\nalso not in the source",
        ),
        (FEATURE_SOURCE, [], ""),
    ],
    ids=["no-source", "first-line-missing", "last-line-missing", "no-description"],
)
def test_description_falls_back_to_behaves_stripped_lines(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    source_lines: Sequence[str],
    description_lines: list[str],
    expected: str,
) -> None:
    """Correct content with lost indentation beats a lost description.

    A worker runs with the working directory the launcher chose, so the feature
    file is not always readable from where the collector runs; the description
    still has to reach the report.
    """
    collector = make_collector(source_lines=source_lines)
    feature = StubFeature(
        CRM_FEATURE_NAME, CRM_PATH, description=description_lines
    )

    run_feature(collector, feature, [])

    collected = feature_by_path(read_document(collector), CRM_PATH)
    assert collected["description"] == expected


def test_a_tag_that_cannot_be_located_is_recorded_at_column_one(
    make_collector: Callable[..., events.ResultCollectorFormatter],
) -> None:
    """An unlocatable tag keeps its line and gets the column of a line start.

    Both cases are recoverable rather than fatal: the tag itself - which is what
    the filter and the tag pages key on - is never in doubt, only its column.
    """
    collector = make_collector(source_lines=FEATURE_SOURCE)
    feature = StubFeature(
        CRM_FEATURE_NAME,
        CRM_PATH,
        tags=[Tag("BeyondTheFile", 99), Tag("NotOnThatLine", 2)],
    )

    run_feature(collector, feature, [])

    assert feature_by_path(read_document(collector), CRM_PATH)["tags"] == [
        events.feature_tag("@BeyondTheFile", 99, 1),
        events.feature_tag("@NotOnThatLine", 2, 1),
    ]


def test_read_source_lines_tolerates_an_unreadable_file_and_reads_once(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The module's only file read: never raises, never repeats.

    Reading a feature file per description and per tag would turn one run into
    thousands of reads; and a formatter that raised on an unreadable source
    would take the worker down over a cosmetic field.
    """
    collector = make_collector()
    absent = str(tmp_path / "absent.feature")

    with caplog.at_level(logging.DEBUG, logger=events.__name__):
        first = collector.read_source_lines(absent)
    second = collector.read_source_lines(absent)

    assert first == []
    assert second is first, "the second read must come from the cache"
    assert collector.read_source_lines("") == []
    assert any("unreadable" in record.getMessage() for record in caplog.records)


def test_the_legacy_feature_directory_prefix_is_normalized(
    make_collector: Callable[..., events.ResultCollectorFormatter],
) -> None:
    """AAP deviation 1, applied by the path module and nowhere else.

    A feature source under ``paths.LEGACY_FEATURES_PREFIX`` is rewritten to
    one under ``paths.NORMALIZED_FEATURES_PREFIX`` by
    ``app.utils.paths.normalize_feature_uri``, so the prefix has exactly one
    owner and no writer performs string surgery.  Both directories are named
    here as those constants rather than spelled out, for the reason
    :func:`test_this_module_names_no_feature_directory_prefix` enforces over
    this whole file.  A Windows worker's backslashes are folded too, so the
    same run produces the same URI on either platform.
    """
    collector = make_collector(source_lines=[])

    run_feature(collector, StubFeature(CRM_FEATURE_NAME, LEGACY_FEATURE_SOURCE), [])
    run_feature(
        collector, StubFeature("Sales", "features\\Sales.feature"), []
    )
    document = read_document(collector)

    crm = feature_by_path(document, CRM_PATH)
    assert crm["uri"] == f"file:{CRM_PATH}"
    sales = feature_by_path(document, SALES_PATH)
    assert sales["uri"] == f"file:{SALES_PATH}"
    assert "\\" not in sales["path"]


def test_this_module_names_no_feature_file_directory_of_its_own() -> None:
    """This file reaches the *feature* directory only through production code.

    AAP 0.4.1 gives the legacy-to-port prefix substitution a single owner,
    ``app/utils/paths.py``, so that the golden fixtures can stay verbatim and
    no test carries a second copy of the rule; AAP 0.4.2 assigns the same
    ownership.  A prefix typed into this file -- in a constant, an assertion or
    a docstring -- would fork that rule, and the fork would surface only when
    the directory moved again.  So the guard reads this file's whole source,
    docstrings included, rather than its executable lines alone.

    Two things are asserted, and the difference between them is deliberate:

    * the **legacy** directory must not appear at all.  Its only purpose is to
      be substituted, so a literal one here could only be a second copy of the
      substitution.
    * no **feature-file path** may be spelled out under the port's own feature
      directory.  Every such path in this module is composed from
      ``paths.NORMALIZED_FEATURES_PREFIX``, which is what keeps the rule in one
      place.

    What is *not* forbidden, because it is not this rule: references to the
    step-definition directory and to the environment module -- ``.../steps/``
    paths and ``.../environment.py`` -- which this module quotes because behave
    reports a step function's ``co_filename`` verbatim and the collector
    derives a dotted module name from it.  Those name the engine's glue layout,
    which AAP 0.3.1 fixes independently and which no normalization touches.
    ``tests/test_rerun_report.py`` carries the stricter whole-prefix form of
    this guard, because it never has cause to mention the glue at all.
    """
    source = Path(__file__).read_text(encoding="utf-8")
    feature_file_paths = re.findall(
        rf"{re.escape(paths.NORMALIZED_FEATURES_PREFIX)}[\w.-]+\.feature",
        source,
    )

    assert paths.LEGACY_FEATURES_PREFIX
    assert paths.NORMALIZED_FEATURES_PREFIX
    assert paths.LEGACY_FEATURES_PREFIX not in source
    assert not feature_file_paths, (
        "a feature-file path is spelled out rather than composed from "
        f"paths.NORMALIZED_FEATURES_PREFIX: {feature_file_paths}"
    )


# ==========================================================================
# Outline rows: name, id and the seams around an unnamed Examples block
# ==========================================================================


def _outline_row(
    line: int,
    *,
    row_id: str | None,
    row_index: int | None,
    examples_name: str = "Expected name",
    examples_index: int = 1,
    with_parent: bool = True,
) -> StubScenario:
    """Build a generated row scenario the way behave builds one.

    behave renders the row scenario's name through
    ``"{name} -- @{row.id} {examples.name}"``, which the collector has to undo
    because the JVM's element name carries no such suffix.

    :param line: The data row's line, which is the element's line.
    :param row_id: behave's ``row.id``, or ``None`` to force the fallback.
    :param row_index: behave's one-based body index, or ``None``.
    :param examples_name: The Examples block's name.
    :param examples_index: The block's one-based index.
    :param with_parent: Whether the outline is reachable through ``parent``.
    :returns: The scenario stand-in.
    """
    annotation = f" -- @{row_id or f'{examples_index}.{row_index}'} {examples_name}"
    return StubScenario(
        f"User can change information in dashboard{annotation}",
        line,
        keyword="Scenario Outline",
        row=StubRow(row_index, row_id),
        parent=StubOutline([StubExamples(examples_index, examples_name)])
        if with_parent
        else None,
        steps=[
            StubStep(
                "And", GOLDEN_STEP_NAME, 21, status=Status.passed, duration=1.45,
                func=make_step_function(
                    "features/steps/crm_steps.py",
                    "user_can_change_any_user_s_information",
                ),
                arguments=golden_arguments(),
            )
        ],
    )


def test_outline_rows_drop_behaves_annotation_and_number_from_the_header(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    crm_feature: StubFeature,
) -> None:
    """The reference's two row ids, produced from behave's own row metadata.

    The annotation is cut at the exact marker behave's ``row.id`` produces,
    which makes the removal precise rather than a guess, and the trailing id
    segment counts the header row as 1 - so behave's first body row is ``2``.
    No element name in the reference contains ``" -- "``, which is the check
    that would catch the suffix surviving.
    """
    collector = make_collector(source_lines=[])
    rows = [
        _outline_row(CRM_OUTLINE_FIRST_ROW_LINE, row_id="1.1", row_index=1),
        _outline_row(CRM_OUTLINE_SECOND_ROW_LINE, row_id="1.2", row_index=2),
    ]

    run_feature(collector, crm_feature, rows)
    elements = [
        element
        for element in feature_by_path(read_document(collector), CRM_PATH)["elements"]
        if element["type"] == events.ELEMENT_TYPE_SCENARIO
    ]

    assert [element["name"] for element in elements] == [
        "User can change information in dashboard"
    ] * 2
    assert [element["id"] for element in elements] == [
        GOLDEN_ROW_ELEMENT_ID,
        GOLDEN_ROW_ELEMENT_ID[: -len(";2")] + ";3",
    ]
    assert [element["line"] for element in elements] == [
        CRM_OUTLINE_FIRST_ROW_LINE,
        CRM_OUTLINE_SECOND_ROW_LINE,
    ]
    # The element's line is the data row's while its step keeps the outline
    # template's line: measured, and deliberately asymmetric.
    assert elements[0]["steps"][0]["line"] == 21


def test_an_outline_row_without_model_metadata_is_recovered_from_its_name(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    crm_feature: StubFeature,
) -> None:
    """With no ``row.id`` and no ``row.index``, the annotation is the only
    source.

    The regular-expression fallback captures the block and row numbers because
    the id needs the row's position, and losing it would renumber the row.
    """
    collector = make_collector(source_lines=[])
    row = _outline_row(
        CRM_OUTLINE_FIRST_ROW_LINE, row_id=None, row_index=None, with_parent=False
    )
    row.name = "User can change information in dashboard -- @1.1 Expected name"

    run_feature(collector, crm_feature, [row])
    element = feature_by_path(read_document(collector), CRM_PATH)["elements"][-1]

    assert element["name"] == "User can change information in dashboard"
    assert element["id"] == GOLDEN_ROW_ELEMENT_ID


def test_an_outline_row_whose_examples_block_is_not_in_the_model(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    crm_feature: StubFeature,
) -> None:
    """A block the model does not expose falls back to the annotation's name.

    The AST is preferred because that is what the JVM slugs, but the
    annotation carries the same text and is better than an empty segment.
    """
    collector = make_collector(source_lines=[])
    # Row id names block 3, while the parent exposes only block 1.
    row = _outline_row(
        CRM_OUTLINE_FIRST_ROW_LINE, row_id="3.1", row_index=1, examples_index=1
    )
    row.name = (
        "User can change information in dashboard -- @3.1 Expected name"
    )

    run_feature(collector, crm_feature, [row])
    element = feature_by_path(read_document(collector), CRM_PATH)["elements"][-1]

    assert element["id"] == GOLDEN_ROW_ELEMENT_ID


def test_an_unnamed_examples_block_keeps_the_settled_id_segments(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    crm_feature: StubFeature,
) -> None:
    """What is settled about an unnamed block's id, and nothing more.

    AAP 0.6 states that an unnamed ``Examples:`` block "would produce an empty
    segment and a doubled separator", that the slug rule "is not safe to
    generalize", and that this cell must be fixed against the clean generated
    baseline before the writer is trusted - and the schema owner is changing
    it.  So this test pins only the parts both the current and the mandated
    behaviour share: the feature slug leads, the outline slug follows, and the
    row's one-based position - counting the header as 1 - trails.  The number
    of separators is deliberately **not** pinned.
    """
    collector = make_collector(source_lines=[])
    row = _outline_row(
        CRM_OUTLINE_FIRST_ROW_LINE, row_id="1.1", row_index=1, examples_name=""
    )

    run_feature(collector, crm_feature, [row])
    element = feature_by_path(read_document(collector), CRM_PATH)["elements"][-1]

    segments = element["id"].split(";")
    assert segments[0] == CRM_FEATURE_ID
    assert segments[1] == events.convert_to_id(
        "User can change information in dashboard"
    )
    assert element["id"].endswith(";2")
    assert element["name"] == "User can change information in dashboard"


# ==========================================================================
# The guard: a formatter may never raise into behave
# ==========================================================================


def _hook_failures(
    records: Sequence[logging.LogRecord],
) -> list[tuple[Any, ...]]:
    """The arguments of every guard failure among ``records``.

    Matched on the message *template* rather than on formatted text, so that
    the assertion is against :data:`events.HOOK_FAILURE_MESSAGE` itself and
    cannot pass on a coincidentally similar log line.

    :param records: Captured log records.
    :returns: One entry per guard failure, holding its ``%s`` arguments.
    """
    return [
        record.args if isinstance(record.args, tuple) else (record.args,)
        for record in records
        if record.msg == events.HOOK_FAILURE_MESSAGE
    ]


def test_a_malformed_feature_event_is_reported_and_the_run_continues(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A formatter exception would take the worker down mid-run.

    It would propagate out of behave's run loop, turn a green suite into a
    non-zero exit and lose every result the worker had collected.  So the event
    is skipped, the failure is logged with the callback's name and a traceback,
    and the rest of the document - including the features announced after it -
    is written intact.
    """
    collector = make_collector(source_lines=[])
    broken = StubFeature(CRM_FEATURE_NAME, CRM_PATH)
    broken.tags = 42  # type: ignore[assignment]

    with caplog.at_level(logging.ERROR, logger=events.__name__):
        collector.uri(broken.filename)
        collector.feature(broken)
        collector.eof()
        run_feature(
            collector,
            StubFeature("Sales", SALES_PATH),
            [StubScenario("a scenario that still gets recorded", 12)],
        )
    document = read_document(collector)

    failures = _hook_failures(caplog.records)
    assert failures == [("feature",)]
    assert any(record.exc_info for record in caplog.records)
    assert [feature["path"] for feature in document["features"]] == [SALES_PATH]
    assert len(feature_by_path(document, SALES_PATH)["elements"]) == 1


def test_a_malformed_scenario_event_is_reported_and_the_feature_survives(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    crm_feature: StubFeature,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One unusable scenario costs that scenario, not the feature.

    The guard is per callback, so the feature stays in the document and the
    next scenario is still collected - which is what makes a damaged event
    survivable rather than fatal.
    """
    collector = make_collector(source_lines=[])
    broken = StubScenario("broken", CRM_PASSING_SCENARIO_LINE)
    broken.tags = 42  # type: ignore[assignment]

    with caplog.at_level(logging.ERROR, logger=events.__name__):
        collector.uri(crm_feature.filename)
        collector.feature(crm_feature)
        collector.background(crm_feature.background)
        collector.scenario(broken)
        collector.scenario(StubScenario("sound", CRM_FAILING_SCENARIO_LINE))
        collector.eof()
    document = read_document(collector)

    assert _hook_failures(caplog.records) == [("scenario",)]
    elements = feature_by_path(document, CRM_PATH)["elements"]
    scenarios = [
        element
        for element in elements
        if element["type"] == events.ELEMENT_TYPE_SCENARIO
    ]
    assert [element["name"] for element in scenarios] == ["sound"]


def test_a_scenario_announced_before_any_feature_is_not_recorded(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """There is nowhere to put it, so it is reported rather than invented.

    Inventing a feature to hold it would put a scenario under a URI no feature
    file has, and every writer keys on the feature.
    """
    collector = make_collector(source_lines=[])

    with caplog.at_level(logging.WARNING, logger=events.__name__):
        collector.scenario(StubScenario("orphan", 9))
    document = read_document(collector)

    assert document["features"] == []
    assert any(
        "announced before any feature" in record.getMessage()
        for record in caplog.records
    )


def test_a_step_announced_outside_a_scenario_is_not_recorded(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    crm_feature: StubFeature,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A step with no element to belong to is reported, not attached anywhere.

    Attaching it to the previous scenario would credit one test case with
    another's step.
    """
    collector = make_collector(source_lines=[])
    collector.uri(crm_feature.filename)
    collector.feature(crm_feature)

    with caplog.at_level(logging.WARNING, logger=events.__name__):
        collector.step(StubStep("Given", "a homeless step", 7))
    document = read_document(collector)

    assert feature_by_path(document, CRM_PATH)["elements"] == []
    assert any(
        "announced outside a scenario" in record.getMessage()
        for record in caplog.records
    )


def test_a_result_for_an_unannounced_step_is_recorded_then_discarded(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    crm_feature: StubFeature,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An outcome whose step was never announced is placed, not dropped.

    behave's ``result`` callback is matched to its step by object identity.
    When that lookup fails - a model that copied the step between callbacks -
    the outcome goes to the first step still awaiting one, because a recorded
    result in the right scenario beats a lost result.  Once every step has an
    outcome there is nowhere left to put it, and it is discarded with a
    warning rather than overwriting a real result.
    """
    collector = make_collector(source_lines=[])
    announced = StubStep("Given", "an announced step", 7)
    unannounced = StubStep(
        "Given", "never announced", 7, status=Status.passed, duration=1.0
    )
    collector.uri(crm_feature.filename)
    collector.feature(crm_feature)
    collector.scenario(StubScenario("scenario", CRM_PASSING_SCENARIO_LINE))
    collector.step(announced)

    with caplog.at_level(logging.WARNING, logger=events.__name__):
        collector.result(unannounced)
        collector.result(unannounced)
    document = read_document(collector)

    step = feature_by_path(document, CRM_PATH)["elements"][-1]["steps"][0]
    assert step["name"] == "an announced step"
    assert step["result"]["status"] == "passed"
    assert any(
        "unannounced step" in record.getMessage() for record in caplog.records
    )


# ==========================================================================
# Closing: stamping, writing and de-registration
# ==========================================================================


def test_close_finalises_a_scenario_that_never_saw_eof(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    crm_feature: StubFeature,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An aborted run still yields a document with complete steps.

    ``close`` finalises whatever ``eof`` did not, so a worker killed between
    the last scenario and the end of the file still reports that scenario's
    outcomes instead of a step with an empty ``result``.
    """
    monkeypatch.setattr(events, "behave_step_registry", StubStepRegistry(match=None))
    collector = make_collector(source_lines=[])
    scenario = StubScenario(
        "scenario",
        CRM_PASSING_SCENARIO_LINE,
        steps=[
            StubStep(
                "Then", "a step the run never reached", 11,
                status=Status.skipped, executed=False,
            )
        ],
    )

    run_feature(collector, crm_feature, [scenario], call_eof=False)
    document = read_document(collector)

    step = feature_by_path(document, CRM_PATH)["elements"][-1]["steps"][0]
    assert step["result"] == {"status": "skipped", "duration": 0}


def test_close_is_idempotent_and_de_registers_the_collector(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    crm_feature: StubFeature,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """behave closes its formatters once; a second call must be harmless.

    ``app/services/test_run_service.py`` may also close a collector defensively,
    and a second write would append a second JSON document to the file and make
    it unparseable.
    """
    collector = make_collector(source_lines=[])
    run_feature(collector, crm_feature, [StubScenario("scenario", 9)])

    with caplog.at_level(logging.DEBUG, logger=events.__name__):
        first = read_document(collector)
        collector.close()
    path = Path(collector.stream_opener.name)

    assert json.loads(path.read_text(encoding="utf-8")) == first
    assert collector not in events._ACTIVE_COLLECTORS
    assert (
        events.attach_to_current_scenario({"mime_type": PNG_MIME_TYPE, "data": "x"})
        is False
    )


def test_a_run_with_no_scenarios_still_yields_a_stamped_document(
    make_collector: Callable[..., events.ResultCollectorFormatter],
) -> None:
    """Zero selected scenarios is a real outcome, not an error.

    ``--tags`` may select nothing, and the writers have to be able to produce
    their empty-state artifacts from a document that exists.
    """
    clock = SteppedClock()
    collector = make_collector(clock=clock)

    document = read_document(collector)

    assert document["features"] == []
    assert document["started_at"] is None
    assert document["generated_at"] == events.format_timestamp(clock.readings[0])


def test_a_value_json_cannot_encode_is_coerced_rather_than_lost(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A diagnosable document beats no document at all.

    The builders make this impossible, but a hook cannot be proven never to
    store an arbitrary object, and losing the whole run's results to one
    unencodable value would be the worse outcome by far.
    """
    collector = make_collector()
    collector.result_set["metadata"] = {"probe": object()}

    with caplog.at_level(logging.WARNING, logger=events.__name__):
        document = read_document(collector)

    assert isinstance(document["metadata"]["probe"], str)
    assert any(
        "JSON cannot encode" in record.getMessage() for record in caplog.records
    )


def test_a_stream_that_cannot_encode_the_document_escapes_it(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An ASCII process locale must not cost the document.

    Scenario names and failure messages in this suite are not all ASCII - the
    French validation message is asserted verbatim in ``Login.feature`` - so a
    worker started under ``LC_ALL=C`` would otherwise write nothing at all.
    The retry escapes the non-ASCII characters, which still parses back to the
    original text.
    """
    collector = make_collector(encoding="ascii")
    collector.result_set["tag_expression"] = "accentué"

    with caplog.at_level(logging.WARNING, logger=events.__name__):
        collector.close()
    text = Path(collector.stream_opener.name).read_text(encoding="ascii")

    assert json.loads(text)["tag_expression"] == "accentué"
    assert any(
        "cannot encode the result document" in record.getMessage()
        for record in caplog.records
    )


def test_collector_opens_its_stream_through_behaves_own_opener(
    tmp_path: Path, crm_feature: StubFeature
) -> None:
    """The production route: behave opens the ``-o`` file, eagerly.

    Opening in the constructor is deliberate - an unwritable ``-o`` path is
    then a startup failure rather than a surprise at the end of a run, and the
    merge step can tell an empty shard (a worker that died) from an absent one
    (a worker that never started).  Both halves are asserted here.

    behave 1.3.3's ``StreamOpener.open()`` calls ``codecs.open()``, which
    Python 3.13 deprecated, so this test - the only one that lets behave open
    the file - contains that warning locally.  The suppression is scoped to
    this block on purpose: ``pytest.ini`` deliberately carries no blanket
    ``filterwarnings``, precisely so a deprecation in a pinned dependency stays
    visible everywhere else.
    """
    destination = tmp_path / WORKER_FILE_NAME
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", message="codecs.open", category=DeprecationWarning
        )
        collector = events.ResultCollectorFormatter(
            StreamOpener(filename=str(destination)), StubConfig()
        )
        try:
            assert destination.exists(), "the stream must be opened eagerly"
            assert collector.stream is not None
            collector.clock = SteppedClock()
            collector.read_source_lines = lambda _filename: []  # type: ignore[method-assign]
            run_feature(collector, crm_feature, [StubScenario("scenario", 9)])
        finally:
            collector.close()

        with pytest.raises(OSError):
            # A directory is not a writable output file, and the failure has to
            # happen here rather than after a run has already been paid for.
            events.ResultCollectorFormatter(
                StreamOpener(filename=str(tmp_path)), StubConfig()
            )

    document = json.loads(destination.read_text(encoding="utf-8"))
    assert collector not in events._ACTIVE_COLLECTORS
    assert feature_by_path(document, CRM_PATH)["elements"]


def test_the_collected_document_round_trips_through_the_load_primitive(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    crm_feature: StubFeature,
    tmp_path: Path,
) -> None:
    """What the collector writes is what the merge step reads.

    The two halves are written in the same module and have to agree: the
    formatter serialises through the shared options, and ``load_result_set``
    completes the envelope without touching feature data.  This is the seam a
    worker and the merge meet at, so it is asserted directly rather than
    inferred from the two halves passing separately.
    """
    collector = make_collector(source_lines=[], tags=["@Smoke"])
    scenario = StubScenario(
        "User can change the situation in progress",
        CRM_FAILING_SCENARIO_LINE,
        tags=[Tag("Smoke", 1)],
        steps=[
            StubStep(
                "And", "User click on the crm dashboard", 18,
                status=Status.failed, duration=4.211,
                error_message="The title is not same as the expected!",
                func=make_step_function(
                    "features/steps/crm_steps.py", "user_click_on_the_crm_dashboard"
                ),
            )
        ],
        background_steps=[
            StubStep(
                "Given", "User login to test other features", 7,
                status=Status.passed, duration=GOLDEN_DURATION_SECONDS,
                func=make_step_function(
                    "features/steps/session_steps.py",
                    "user_login_to_test_other_features",
                ),
            )
        ],
    )

    # The attachment is made while the scenario is still current, which is
    # where features/environment.py's teardown makes it; close() then
    # finalises the scenario that never saw eof.
    run_feature(collector, crm_feature, [scenario], call_eof=False)
    collector.embedding(PNG_MIME_TYPE, SCREENSHOT_PNG)
    written = read_document(collector)
    loaded = events.load_result_set(Path(collector.stream_opener.name))

    assert loaded == written
    element = feature_by_path(loaded, CRM_PATH)["elements"][-1]
    assert element["after"][0]["embeddings"][0]["name"] == scenario.name
    merged = events.merge_result_sets([loaded])
    assert merged["features"] == written["features"]
    assert merged["tag_expression"] == "@Smoke"
    # And it survives a second write, which is what the merge step performs.
    destination = tmp_path / "merged.json"
    events.dump_result_set(merged, destination)
    assert events.load_result_set(destination) == merged


# ==========================================================================
# The remaining defensive paths
#
# Each one is a branch the production module documents as "must not fail the
# run".  They are exercised here because an untested guard is indistinguishable
# from a guard that does not work, and every one of them stands between a
# damaged event and a lost document.
# ==========================================================================


def test_the_default_clock_reads_an_aware_utc_instant() -> None:
    """The class's default clock, which every real worker uses.

    ``format_timestamp`` treats a naive instant as UTC, so a clock returning
    local time would silently shift every timestamp in every artifact by the
    machine's offset.  The window is wide enough that no schedule can make this
    flaky and narrow enough to catch a clock reading the wrong epoch.
    """
    moment = events.ResultCollectorFormatter.clock()

    assert moment.tzinfo is not None
    assert moment.utcoffset() == timedelta(0)
    assert abs((moment - datetime.now(timezone.utc)).total_seconds()) < 60


def test_match_location_from_an_absolute_step_source_is_still_dotted(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    crm_feature: StubFeature,
    tmp_path: Path,
) -> None:
    """An absolutely-named step module still yields an identifier, not a path.

    behave compiles a step module with a filename made relative to the working
    directory, but a runner launched differently can hand it an absolute one.
    The value is expressed relative to the working directory, so its exact text
    depends on where the suite runs and only its shape is pinned - what must
    never happen is a ``match.location`` carrying a filesystem path.
    """
    collector = make_collector(source_lines=[])
    func = make_step_function(str(tmp_path / "steps.py"), "shared_step")
    scenario = StubScenario(
        "scenario",
        CRM_PASSING_SCENARIO_LINE,
        steps=[
            StubStep(
                "Given", "a step", 7, status=Status.passed, duration=1.0, func=func
            )
        ],
    )

    run_feature(collector, crm_feature, [scenario])
    feature = feature_by_path(read_document(collector), CRM_PATH)

    location = feature["elements"][-1]["steps"][0]["match"]["location"]
    assert location.endswith(".shared_step")
    assert "/" not in location
    assert not location.startswith(".")
    # No segment is a file name: the ``.py`` suffix is stripped and the
    # ``..`` of the relative path is dropped rather than emitted as a segment.
    segments = location.split(".")
    assert "py" not in segments
    assert "" not in segments


def test_a_tag_expression_of_an_unexpected_type_is_recorded_as_text(
    make_collector: Callable[..., events.ResultCollectorFormatter],
) -> None:
    """behave's configuration surface has moved before, and may again.

    A value that is neither a string nor a sequence is described rather than
    dropped: the field is diagnostic, and a formatter that raised in its
    constructor would abort the worker before a single scenario ran.
    """
    collector = make_collector(tags=42)  # type: ignore[arg-type]

    assert read_document(collector)["tag_expression"] == "42"


def test_a_source_that_already_carries_the_file_scheme_is_not_doubled(
    make_collector: Callable[..., events.ResultCollectorFormatter],
) -> None:
    """``path`` never carries the scheme and ``uri`` never carries it twice.

    ``rerun_report.py`` writes ``path`` behind its own ``file:`` prefix, so a
    doubled scheme would produce a manifest line behave cannot read - and the
    manifest is machine input (``FailedTestRunner.java:11``).
    """
    collector = make_collector(source_lines=[])

    run_feature(
        collector, StubFeature(CRM_FEATURE_NAME, f"file:{CRM_PATH}"), []
    )
    feature = feature_by_path(read_document(collector), CRM_PATH)

    assert feature["path"] == CRM_PATH
    assert feature["uri"] == f"file:{CRM_PATH}"


def test_an_examples_block_is_found_by_position_when_it_has_no_index(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    crm_feature: StubFeature,
) -> None:
    """A model that does not number its Examples blocks still names them.

    The block's name reaches the row id either way, and the id is what the
    PrettyReports detail pages and the JSON artifact key the row on.
    """
    collector = make_collector(source_lines=[])
    row = _outline_row(
        CRM_OUTLINE_FIRST_ROW_LINE, row_id="1.1", row_index=1, examples_index=7
    )

    run_feature(collector, crm_feature, [row])
    element = feature_by_path(read_document(collector), CRM_PATH)["elements"][-1]

    assert element["id"] == GOLDEN_ROW_ELEMENT_ID


def test_merge_keeps_a_feature_that_carries_no_identifying_key() -> None:
    """A feature with no path, uri or name is still merged in.

    The pathological shard again: dropping the feature would lose every result
    in it, while keying it on the empty string loses nothing that was there.
    """
    merged = events.merge_result_sets(
        [{"features": [{"elements": [_scenario(9)]}]}]
    )

    assert len(merged["features"]) == 1
    assert len(merged["features"][0]["elements"]) == 1


def test_close_survives_a_step_model_that_cannot_be_read(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    crm_feature: StubFeature,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Finalising one broken step must not cost the whole document.

    ``close`` guards each stage separately for exactly this reason: a failure
    while finalising a scenario still leaves every feature collected so far in
    the written document.
    """

    class UnreadableStep(StubStep):
        """A step whose status cannot be read at all."""

        @property
        def status(self) -> Any:
            """Raise rather than answer, as a broken model would.

            :returns: Never returns.
            :raises RuntimeError: Always.
            """
            raise RuntimeError("status unavailable")

        @status.setter
        def status(self, value: Any) -> None:
            """Swallow the constructor's assignment.

            :param value: Ignored.
            """

    collector = make_collector(source_lines=[])
    scenario = StubScenario(
        "scenario",
        CRM_PASSING_SCENARIO_LINE,
        steps=[UnreadableStep("Given", "a step", 7, executed=False)],
    )

    with caplog.at_level(logging.ERROR, logger=events.__name__):
        run_feature(collector, crm_feature, [scenario], call_eof=False)
        document = read_document(collector)

    assert any(
        "Finalising the last scenario failed" in record.getMessage()
        for record in caplog.records
    )
    element = feature_by_path(document, CRM_PATH)["elements"][-1]
    assert element["name"] == "scenario"
    assert element["steps"][0]["name"] == "a step"


def test_close_survives_a_clock_that_fails(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A clock failure costs the stamps, not the document.

    The run-level fields stay at their defaults and the features are still
    written, which is the difference between a report with no timestamp and no
    report at all.
    """

    def _broken_clock() -> datetime:
        """Stand in for a clock reading that fails.

        :returns: Never returns.
        :raises RuntimeError: Always.
        """
        raise RuntimeError("no clock available")

    collector = make_collector(clock=_broken_clock)

    with caplog.at_level(logging.ERROR, logger=events.__name__):
        document = read_document(collector)

    assert document["started_at"] is None
    assert document["generated_at"] is None
    assert any(
        "Stamping the result document failed" in record.getMessage()
        for record in caplog.records
    )


def test_close_reports_a_stream_it_cannot_write_to(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    crm_feature: StubFeature,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A write failure is logged, never raised into behave.

    behave calls ``close`` at the very end of a run, so an exception here would
    fail a suite that had already passed - the worst possible trade for a
    report.
    """
    collector = make_collector(source_lines=[])
    run_feature(collector, crm_feature, [StubScenario("scenario", 9)])
    collector.stream.close()

    with caplog.at_level(logging.ERROR, logger=events.__name__):
        collector.close()

    assert any(
        "Writing the result document failed" in record.getMessage()
        for record in caplog.records
    )


def test_close_tolerates_a_collector_it_no_longer_owns(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """De-registration and stream closing are both belt-and-braces.

    ``close`` is the last chance to release the collector's registration, so an
    already-removed entry is a condition to note and continue from rather than
    an error; and a stream the opener no longer recognises must not turn the
    end of a run into a failure.  The document is still produced in both cases,
    which is what the assertion on the captured text establishes.
    """

    class CapturingStream:
        """A writable stand-in the formatter's stream opener does not know."""

        def __init__(self) -> None:
            """Start with no captured text."""
            self.text = ""

        def write(self, text: str) -> None:
            """Record ``text``.

            :param text: The written text.
            """
            self.text += text

        def flush(self) -> None:
            """Accept the formatter's flush, which has nothing to do."""

    collector = make_collector()
    monkeypatch.setattr(events, "_ACTIVE_COLLECTORS", [])
    capture = CapturingStream()
    # Truthy, writable, and *not* the opener's stream - which is exactly what
    # behave's ``close_stream`` asserts against.
    collector.stream = capture

    with caplog.at_level(logging.DEBUG, logger=events.__name__):
        collector.close()

    messages = [record.getMessage() for record in caplog.records]
    assert any("already de-registered" in message for message in messages)
    assert any("Closing the output stream failed" in message for message in messages)
    assert_keys(json.loads(capture.text), RESULT_SET_KEYS, "document written anyway")
