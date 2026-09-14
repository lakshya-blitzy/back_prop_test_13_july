"""Tests for the internal result schema and the behave event collector.

This module is the gate for ``app/reporting/events.py``, the single place where
the engine's event stream is observed and the single definition of the
intermediate document all four artifact writers consume.  AAP 0.6 fixes both
the obligation -- every field those writers read is present for a passing
scenario, a failing scenario with an attachment, a skipped step, an undefined
step, a background and a two-row outline, each with its own named test so a
failure names the shape -- and the port's departures from what the engine
reports natively: ``match.location`` as the resolved step function's dotted
Python path (deviation 8), nanosecond integer durations where behave reports
float seconds, UTC timestamps in the JVM generator's millisecond form
``YYYY-MM-DDTHH:MM:SS.mmmZ`` truncated rather than rounded, and the
key-omission rules, asserted as explicit per-level key inventories so that a
field a writer reads but the collector never records fails here rather than in
an artifact.

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
  carries ``tags``, possibly empty;
* :func:`~app.reporting.events.run_metadata` obtains no value by executing a
  program, measured against this interpreter: ``platform.processor()`` runs
  ``uname -p`` through the inherited ``PATH`` on Python 3.14, while
  ``platform.machine()`` reads the ``os.uname()`` fields already held, so the
  probes are pinned to the second and every process-spawning entry point they
  could reach is replaced with something that fails the test.

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
only built-in markers are used.  ``tests/conftest.py`` owns ``sys.path`` and the
three pinned fixtures; this module reads them through their fixtures and edits
nothing.  One test does plant an executable ``uname`` -- a two-line shell script
that echoes a marker -- inside its own ``tmp_path`` and prepend that directory
to ``PATH`` for its duration, which is the only way to prove that a metadata
probe cannot be steered by ``PATH``; nothing else in the module runs a program.
"""

from __future__ import annotations

import ast
import base64
import copy
import io
import itertools
import json
import logging
import os
import platform
import re
import stat
import subprocess
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

from app.reporting import aggregation, events
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
        "MAX_FAILURE_TEXT_CHARS",
        "MAX_RUN_RESULT_BYTES",
        "MAX_RUN_RESULT_DOCUMENTS",
        "MAX_RUN_RESULT_NODES",
        "RESULT_STATUSES",
        "SCHEMA_VERSION",
        "TAG_TYPE",
        "TIMESTAMP_PATTERN",
        "ResultCollectorFormatter",
        "ResultSetError",
        "RunResultBudget",
        "attach_to_current_scenario",
        "convert_to_id",
        "dump_result_set",
        "element_units",
        "feature_tag",
        "format_timestamp",
        "iter_scenarios",
        "load_result_set",
        "merge_result_sets",
        "nanos_from_seconds",
        "parse_timestamp",
        "new_element",
        "new_feature",
        "new_hook_entry",
        "new_result_set",
        "new_step",
        "record_hook_result",
        "redact_step_text",
        "run_metadata",
        "sanitize_failure_text",
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

#: What a planted ``uname`` on ``PATH`` prints.  No probe that reads the
#: interpreter's own data can produce this string, so a metadata value carrying
#: it could only have come from executing a program.
HOSTILE_UNAME_MARKER: Final[str] = "hostile-uname-marker-4b19d7"

#: Every process-spawning entry point a metadata probe could reach, as
#: ``(module, attribute)`` pairs so that a failure names the entry point.
#: ``platform`` imports ``subprocess`` inside the function that needs it, and a
#: lazy ``import subprocess`` resolves to this very module object, so patching
#: these attributes is what such an import would find.
SPAWN_ENTRY_POINTS: Final[tuple[tuple[Any, str], ...]] = (
    (subprocess, "check_output"),
    (subprocess, "run"),
    (subprocess, "Popen"),
    (os, "popen"),
    (os, "system"),
)

#: Whether this host resolves a helper program through ``PATH`` at all, which
#: is what decides if planting one can prove anything.  True everywhere this
#: suite runs in CI.
POSIX_PATH_LOOKUP: Final[bool] = os.name == "posix"

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

# -- The redaction boundary: measured phrasings, synthetic values ---------
#
# The *phrasings* below are the suite's own, from ``Login.feature`` and
# ``Contact.feature``, because the span classifier under test keys on the
# words around a value.  The *values* are deliberately synthetic.
# Review finding SEC2-F17 is that a credential belongs in the Gherkin fixture
# data and nowhere else, so replicating the real ``Examples`` account here -
# in a file that is neither a feature file nor covered by AAP 0.8's test-data
# note - would recreate the very leak this module is asserting is closed.
# What the assertions need is a value of the right *shape*, and
# ``@example.test`` is the reserved-domain form of one.

#: ``Login.feature:15``'s phrasing, with a synthetic account substituted as
#: behave substitutes an ``Examples`` row.  The value is an address, so it is
#: classified twice over: by the ``username`` that follows it and by its own
#: shape.
LOGIN_USERNAME_STEP_NAME: Final[str] = 'User enters "account7@example.test" username'

#: ``Login.feature:16``'s phrasing, with a synthetic password.  This is the
#: case that *only* adjacency can catch: the value's own text is
#: indistinguishable from a product name, and the following ``password`` is
#: the entire reason it is a secret.
LOGIN_PASSWORD_STEP_NAME: Final[str] = 'User enters "not-a-real-secret" password'

#: ``Contact.feature:12``'s phrasing with its two values substituted: a phone
#: number, which the closed keyword set deliberately does not classify, and an
#: address, which classifies on its own terms.  It is the module's
#: one-position-sensitive case.
CONTACT_TWO_ARGUMENT_STEP_NAME: Final[str] = (
    'User enters "+99999999999" and "someone@example.test"'
)

#: The same phrasing with the two positions swapped, so that the *masked* span
#: comes first and every later offset has to move.  A synthetic ordering, and
#: labelled as one: no feature file writes it this way, and the offset shift is
#: the rule being pinned rather than the phrasing.
CONTACT_SWAPPED_ARGUMENT_STEP_NAME: Final[str] = (
    'User enters "someone@example.test" and "+99999999999"'
)

#: A two-value phrase whose *second* value is the credential and whose first
#: is ordinary business data.  Two things are measured on it: a partially
#: indexed argument list must still classify the span no entry represents, and
#: the ``password`` 18 characters to the right of ``"public"`` must not reach
#: back over the value in between.  Synthetic phrasing, and labelled as one -
#: no feature file writes a tag and a credential in one step - because what is
#: pinned is the classifier's scope rather than a phrase.
MIXED_VALUE_STEP_NAME: Final[str] = (
    'User enters "public" tag and "not-a-real-secret" password'
)

#: The business value of that phrase, which must survive byte for byte.
MIXED_VALUE_KEPT: Final[str] = '"public"'

#: What a masked quoted argument looks like: the placeholder inside the quotes
#: the JSON contract says ``val`` carries.
REDACTED_QUOTED_VALUE: Final[str] = '"[redacted]"'

#: A credential-shaped fragment for the failure-text assertions, in the
#: ``key=value`` shape a Selenium or configuration error quotes.  Synthetic,
#: for the reason given above.
FAILURE_TEXT_SECRET: Final[str] = "password=not-a-real-secret"

#: An absolute path that belongs to no checkout of this project, so the
#: relativising rule cannot reach it and the shortening rule must.
FOREIGN_ABSOLUTE_PATH: Final[str] = "/opt/toolchain/lib/python3.14/unittest/case.py"

#: Traceback frame paths whose directories carry **spaces**, which is what a
#: real CI workspace produces: a home directory holding a person's name, a
#: Windows profile, an agent directory and a numbered job.  Each is paired with
#: the frame the sanitizer must leave behind - two components behind the marker
#: that says the path was cut, in the separator the frame carried.  A rule that
#: matched a path by its characters rather than by the quotes around it stops
#: at the first space, which published the account name and the layout while
#: mangling the prefix in front of them.
SPACED_FRAME_PATHS: Final[tuple[tuple[str, str], ...]] = (
    (
        "/home/jane doe/workspace/features/steps/login_steps.py",
        ".../steps/login_steps.py",
    ),
    (
        "C:\\Users\\Jane Doe\\workspace\\features\\steps\\login_steps.py",
        "...\\steps\\login_steps.py",
    ),
    (
        "D:\\CI Agent\\job 42\\features\\steps\\login_steps.py",
        "...\\steps\\login_steps.py",
    ),
    (
        "\\\\buildhost\\share\\job 42\\features\\steps\\login_steps.py",
        "...\\steps\\login_steps.py",
    ),
)

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

#: The directory components a worker's intermediate really sits under, taken
#: from their owner rather than written out (AAP 0.4.2).  The write-route tests
#: below put their output at this depth deliberately: ``target/`` and
#: ``.workers/`` are the components the path authority creates and verifies,
#: and a ``--no-clean`` run leaves them in place for a hostile entry to be
#: planted in, which is the condition the refusal tests reproduce.
WORKER_DIR_COMPONENTS: Final[tuple[str, ...]] = (
    paths.TARGET_DIR_NAME,
    paths.WORKERS_DIR_NAME,
)

#: Bytes of the file a hostile link points at.  Asserted byte-for-byte after
#: every refusal, because the finding is not "an exception was raised" but "an
#: external file was truncated": an implementation that refused *after* opening
#: with ``O_TRUNC`` would satisfy the first and fail the second.
VICTIM_TEXT: Final[str] = "an external file no report writer may touch\n"


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


class StubUnsettableOpener:
    """Stands in for a stream opener that will not hold a stream.

    Carries the two attributes the collector reads off behave's
    ``StreamOpener`` -- ``name`` and ``encoding`` -- and exposes ``stream`` as a
    read-only property, so installing the verified handle on it raises
    ``AttributeError``.  The real ``StreamOpener`` accepts that assignment;
    this stand-in makes the branch that survives an opener which does not
    deterministic, because behave's own ``close_stream`` cannot close a stream
    the opener never held, leaving the collector to close it itself.
    """

    def __init__(self, filename: str) -> None:
        """Carry the filename and the encoding, and nothing else.

        :param filename: The ``-o`` path.
        """
        self.name = filename
        self.encoding = "utf-8"
        self.should_close_stream = False

    @property
    def stream(self) -> None:
        """Answer that there is no stream, and refuse to hold one.

        :returns: Always ``None``.
        """
        return None


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


def worker_output_path(root: Path, name: str = WORKER_FILE_NAME) -> Path:
    """Return ``root/target/.workers/<name>``, with nothing created.

    The depth a worker's intermediate really has, so that a write-route
    assertion covers the directory components the path authority creates and
    verifies rather than only a file in a directory that already existed.

    :param root: The per-test directory to build under.
    :param name: File name to use.
    :returns: The path, whose parents do not exist yet.
    """
    return root.joinpath(*WORKER_DIR_COMPONENTS, name)


def assert_owner_only(path: Path, *, directory: bool) -> None:
    """Assert ``path`` carries no group or other permission bits.

    The mode policy the path authority applies, asserted through its own
    constants rather than against octal literals: a worker's document carries
    step arguments substituted from the Examples tables and the failure text of
    every failed step, so a local reader must not be able to read it.

    Directories are asserted on the mask alone while files are asserted on the
    whole mode.  That asymmetry is measured, not a concession: the tightening
    is applied per object through ``os.fchmod`` and clears group and other bits
    while leaving the special bits as they were, so a directory created beneath
    a set-group-id parent keeps that bit and is still owner-only.

    :param path: The file or directory to check.
    :param directory: Whether ``path`` is a directory.
    """
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode & paths.ARTIFACT_MODE_MASK == 0, (
        f"{path} is readable by the group or by others: {mode:#o}"
    )
    if directory:
        assert mode & paths.ARTIFACT_DIR_MODE == paths.ARTIFACT_DIR_MODE, (
            f"{path} is not usable by its owner: {mode:#o}"
        )
    else:
        assert mode == paths.ARTIFACT_FILE_MODE, (
            f"{path} was not created {paths.ARTIFACT_FILE_MODE:#o}: {mode:#o}"
        )


def plant_victim(path: Path) -> Path:
    """Create the external file a hostile link will point at.

    :param path: Where to create it.
    :returns: ``path``, carrying :data:`VICTIM_TEXT`.
    """
    path.write_text(VICTIM_TEXT, encoding="utf-8")
    return path


def assert_victim_intact(path: Path) -> None:
    """Assert the external file was neither truncated nor rewritten.

    :param path: The file :func:`plant_victim` created.
    """
    assert path.read_text(encoding="utf-8") == VICTIM_TEXT, (
        f"{path} was written through a link the writer should have refused"
    )


def called_names(source_path: Path) -> tuple[set[str], set[tuple[str, str]]]:
    """Every function this module calls, split by how it is addressed.

    Parsed out of the source with :mod:`ast` for the same reason
    :func:`imported_module_names` is: the question is what the module's code
    *says*, and no amount of interpreter state answers that.  The split
    matters because ``open`` means two different things depending on how it is
    written -- ``open(...)`` is the builtin that resolves a pathname, while
    ``self.open()`` is behave's own formatter method -- and an assertion that
    could not tell them apart would be unable to say anything about either.

    :param source_path: The file to parse.
    :returns: Bare-name calls (``f(...)``) as a set of names, and attribute
        calls (``x.f(...)``) as a set of ``(receiver, attribute)`` pairs, where
        the receiver of anything more complex than a plain name is recorded as
        ``"?"``.
    """
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    bare: set[str] = set()
    attributes: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            bare.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            receiver = node.func.value
            name = receiver.id if isinstance(receiver, ast.Name) else "?"
            attributes.add((name, node.func.attr))
    return bare, attributes


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
    documents and the one that gives a test control of the stream: the
    encoding-failure case below needs an ASCII stream, and
    :func:`test_close_reports_a_stream_it_cannot_write_to` needs to close the
    stream under the collector's feet.  A pre-opened stream resolves no
    pathname, so the collector hands it straight back rather than opening
    anything -- which is why the *production* mode, where behave supplies a
    filename and the collector opens it through the path authority, is covered
    by the write-route tests further down rather than here.

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

    ``app.logging_config`` is the second permitted name, and it is here for one
    reason: review finding SEC2-F03 requires the substituted step text and its
    arguments to be redacted before they are serialized, and that module is the
    port's single owner of the credential vocabulary, the redaction placeholder
    and the truncation notice.  A private copy of those patterns inside
    ``app/reporting`` would be a second authority that drifts from the first.
    It costs the boundary nothing that matters: the module imports only
    ``logging``, ``re``, ``sys`` and ``typing``, so a worker still pays for no
    framework, and ``tests/test_app_factory.py`` continues to prove that
    importing ``app.reporting.events`` pulls in no Flask.
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
    assert intra_package == {"app.logging_config", "app.utils.paths"}


def test_module_writes_only_through_the_path_authority() -> None:
    """One write route, asserted against the source rather than inferred.

    This is the regression gate for the shape the review found in three
    writers at once: ``ensure_parent(path)`` -- which verifies a parent and
    then *releases* it -- followed by the builtin ``open(path, "w")``, which
    resolves the same name a second time.  Between those two calls the name can
    be replaced by a link, so the write lands on, and truncates, whatever it
    then points at (CWE-367/CWE-59/CWE-22).  A planted-link test cannot see
    that window, because the old check refused a link that was *already* there;
    what closes it is the absence of the second resolution, which is a property
    of the source and is therefore asserted here.

    Four statements, each with a reason:

    * the builtin ``open`` is never called, so no pathname is resolved for a
      write outside the path authority;
    * ``ensure_parent`` is neither imported nor called, so the released-verification
      half of the pair cannot come back;
    * ``open_artifact_write`` *is* called, so the route exists rather than
      merely being unused;
    * exactly two receivers own an ``x.open()`` call, and both are named:
      ``self.open()`` is behave's own formatter method, reached only for an
      opener carrying a pre-opened stream and no filename, and ``os.open()`` is
      the descriptor-level call ``load_result_set`` reads a shard through with
      ``O_NOFOLLOW`` -- the read route, which has its own tests below.
      ``codecs.open()``, ``Path.open()`` and any other by-name opener is a
      third receiver and fails here.
    """
    bare, attributes = called_names(EVENTS_SOURCE_PATH)

    assert "open" not in bare, "the builtin open resolves a pathname for a write"
    assert "ensure_parent" not in bare
    assert "ensure_parent" not in dir(events), (
        "ensure_parent is still imported, so the check-then-open pair can return"
    )
    assert "open_artifact_write" in bare
    openers = {receiver for receiver, attribute in attributes if attribute == "open"}
    assert openers == {"self", "os"}, f"an unexpected opener is in use: {openers}"


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


# ==========================================================================
# Run metadata: the fixed vocabulary, and probes that execute nothing
#
# Every value this mapping carries is written verbatim into
# target/cucumber-reports.html and into the target/cucumber tree, so a probe
# that obtained one by running a program would hand whatever that program
# printed to every artifact - and would resolve it through the PATH the run
# inherited.  The vocabulary assertions and the no-subprocess assertions
# therefore live together: both describe the same function's contract.
# ==========================================================================


class SpawnForbidden(BaseException):
    """Raised by a stand-in that a metadata probe must never call.

    Deliberately a :class:`BaseException` and not an :class:`Exception`:
    ``events._safe_probe`` swallows every ``Exception`` so that a report cannot
    fail a test run, and a sentinel it could swallow would turn the assertions
    below into silent passes.  This one escapes that handler and fails the test
    with the forbidden call in the traceback.
    """


def _forbidden_spawn(name: str, calls: list[str]) -> Callable[..., Any]:
    """Build a stand-in that records a forbidden call and then aborts.

    :param name: The entry point being replaced, for the recording and the
        message -- ``"subprocess.check_output"``, for instance.
    :param calls: Accumulator the stand-in appends *name* to, so a test can
        assert on the calls even if something swallowed the exception.
    :returns: A callable accepting any arguments and never returning.
    """

    def _spawn(*_args: Any, **_kwargs: Any) -> Any:
        """Record the call and abort the test.

        :returns: Never returns.
        :raises SpawnForbidden: Always.
        """
        calls.append(name)
        raise SpawnForbidden(f"run_metadata must not call {name}")

    return _spawn


def test_run_metadata_carries_the_four_fixed_keys_as_strings() -> None:
    """``artifact/metadata.html`` renders exactly this vocabulary.

    Every value is a string and a probe that yields nothing yields ``""``: a
    report must not fail a run, nor grow or lose a key, because the host
    described itself sparsely.
    """
    metadata = events.run_metadata()

    assert set(metadata) == set(METADATA_KEYS)
    for section, keys in METADATA_KEYS.items():
        assert_keys(metadata[section], keys, f"metadata[{section!r}]")
        for key in keys:
            assert isinstance(metadata[section][key], str)
    assert metadata["implementation"]["name"] == "behave"


def test_run_metadata_spawns_no_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    """No metadata value may be obtained by executing a program.

    Every entry point in :data:`SPAWN_ENTRY_POINTS` is replaced with a
    :class:`SpawnForbidden` stand-in, so a probe that shells out aborts this
    test rather than quietly returning a value.  ``platform``'s own uname cache
    is cleared first: a probe that spawns only on a cold cache would otherwise
    be indistinguishable from one that never spawns at all.

    :param monkeypatch: Replaces the spawn entry points and the uname cache.
    """
    calls: list[str] = []
    for module, attribute in SPAWN_ENTRY_POINTS:
        monkeypatch.setattr(
            module,
            attribute,
            _forbidden_spawn(f"{module.__name__}.{attribute}", calls),
        )
    monkeypatch.setattr(platform, "_uname_cache", None)

    metadata = events.run_metadata()

    assert calls == []
    assert set(metadata) == set(METADATA_KEYS)
    for section, keys in METADATA_KEYS.items():
        assert_keys(metadata[section], keys, f"metadata[{section!r}]")
        for key in keys:
            assert isinstance(metadata[section][key], str)


def test_run_metadata_never_calls_platform_processor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``platform.processor`` is not reachable from this function at all.

    It is the one probe in ``platform`` that resolves and runs ``uname -p``
    through the inherited ``PATH``, so the contract is its absence rather than
    a guard around it: the replacement records any call and aborts, and
    ``cpu.name`` still carries the machine type.

    :param monkeypatch: Replaces ``platform.processor``.
    """
    calls: list[str] = []
    monkeypatch.setattr(
        platform, "processor", _forbidden_spawn("platform.processor", calls)
    )

    metadata = events.run_metadata()

    assert calls == []
    assert metadata["cpu"]["name"] == platform.machine().strip()


@pytest.mark.skipif(
    not POSIX_PATH_LOOKUP,
    reason="only a POSIX host resolves a helper program through PATH",
)
def test_run_metadata_ignores_a_hostile_uname_on_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A planted ``uname`` earlier on ``PATH`` reaches no report value.

    The executable prints :data:`HOSTILE_UNAME_MARKER` instead of a machine
    description, which is what makes the influence visible: any metadata value
    carrying that string was produced by running it.  The closing assertion
    arms the trap -- ``platform.processor()`` *does* return the marker on this
    host -- so the test cannot pass because the plant was unreachable or not
    executable, only because ``run_metadata`` never consults it.

    :param tmp_path: Directory the executable is planted in.
    :param monkeypatch: Prepends that directory to ``PATH`` and clears
        ``platform``'s uname cache, so the probe is genuinely re-resolved.
    """
    planted = tmp_path / "uname"
    planted.write_text(f'#!/bin/sh\necho "{HOSTILE_UNAME_MARKER}"\n', encoding="utf-8")
    planted.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path), prepend=os.pathsep)
    monkeypatch.setattr(platform, "_uname_cache", None)

    metadata = events.run_metadata()

    assert HOSTILE_UNAME_MARKER not in json.dumps(metadata)
    assert metadata["cpu"]["name"] == os.uname().machine
    assert platform.processor() == HOSTILE_UNAME_MARKER


@pytest.mark.parametrize(
    ("machine", "expected"),
    [
        ("x86_64", "x86_64"),
        ("  aarch64  ", "aarch64"),
        ("\tarm64\n", "arm64"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_run_metadata_cpu_name_is_the_normalized_machine_type(
    machine: str, expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``cpu.name`` is ``platform.machine()``, trimmed, or ``""``.

    The machine type comes from the ``os.uname()`` fields the interpreter
    already holds -- on Windows, from the architecture environment values -- so
    it is the whole of what this key can be.  A host that describes itself with
    surrounding whitespace, or not at all, contributes a trimmed value or the
    module's blank placeholder; it never contributes ``None``, which
    ``artifact/metadata.html`` would render as a row reading "None".

    :param machine: What ``platform.machine`` reports.
    :param expected: The ``cpu.name`` that must reach the document.
    :param monkeypatch: Replaces ``platform.machine``.
    """
    monkeypatch.setattr(platform, "machine", lambda: machine)

    metadata = events.run_metadata()

    assert metadata["cpu"]["name"] == expected
    assert isinstance(metadata["cpu"]["name"], str)
    if not machine.strip():
        assert metadata["cpu"]["name"] == events._UNKNOWN_METADATA_VALUE


def test_run_metadata_survives_a_machine_probe_that_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A probe that raises or answers with a non-string still yields a string.

    ``_safe_probe``'s never-raise guarantee is what lets the writers treat this
    mapping as total, and normalizing the machine type must not weaken it: a
    ``platform.machine`` that raises, and one that returns something with no
    ``strip``, both contribute the blank placeholder.

    :param monkeypatch: Replaces ``platform.machine``.
    """

    def _failing_machine() -> str:
        """Fail the way a stripped-down platform module can.

        :returns: Never returns.
        :raises OSError: Always.
        """
        raise OSError("this platform does not describe its machine type")

    monkeypatch.setattr(platform, "machine", _failing_machine)
    assert events.run_metadata()["cpu"]["name"] == events._UNKNOWN_METADATA_VALUE

    monkeypatch.setattr(platform, "machine", lambda: None)
    assert events.run_metadata()["cpu"]["name"] == events._UNKNOWN_METADATA_VALUE


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
            identifier="sales;verify-that-the-user-s-search-finds-his-name",
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
            element_type="rule",
            keyword="Rule",
            line=3,
            name="odd",
            # Coerced to a scenario, so it needs a scenario's identifier: the
            # id is what every artifact keys on, and new_element refuses to
            # build a test case without one.
            identifier="odd-feature;odd",
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
    """A foreign schema version is **refused**, through one typed channel.

    A document declaring a version this build does not read is a document this
    build cannot read in full, and the only honest answer is to refuse it: a
    reader that continued past it would merge whatever fields it happened to
    recognise and publish the result as a complete run, losing the rest
    silently.  So ``load_result_set`` raises, and what it raises is
    ``ResultSetError`` and nothing else -
    ``app/services/test_run_service.py`` catches exactly that to name the
    offending shard on stderr and apply the plan's dead-worker row, and a
    failure escaping as an ``OSError`` or a ``ValueError`` would bypass that
    handling and surface as an undocumented exit.

    The message names both versions, because "this shard is dead" is only
    actionable if it says why: the version the file declared and the one this
    build reads.
    """
    foreign = tmp_path / "foreign-version.json"
    foreign.write_text(
        json.dumps(
            {"schema_version": FOREIGN_SCHEMA_VERSION, "features": []}
        ),
        encoding="utf-8",
    )
    assert events.SCHEMA_VERSION != FOREIGN_SCHEMA_VERSION

    with (
        caplog.at_level(logging.WARNING, logger=events.__name__),
        pytest.raises(events.ResultSetError) as raised,
    ):
        events.load_result_set(foreign)

    message = str(raised.value)
    assert str(foreign) in message
    assert str(FOREIGN_SCHEMA_VERSION) in message
    assert str(events.SCHEMA_VERSION) in message


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
    the directory creation inside ``open_artifact_write`` cannot succeed and the
    failure is a genuine filesystem condition rather than a patched one.
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


def test_dump_result_set_writes_the_same_bytes_through_the_path_authority(
    sample_result_set: dict[str, Any], tmp_path: Path
) -> None:
    """The write route changed; the bytes and the permissions did not.

    ``dump_result_set`` used to call ``ensure_parent`` and then the builtin
    ``open`` on the pathname it returned - a verified parent released before a
    second resolution of the same name, which is the check/open pair a link
    swap redirects (CWE-367/CWE-59) - and it inherited whatever the process
    umask allowed, measured at ``0o644``.  It now performs one
    descriptor-bound open through ``app.utils.paths.open_artifact_write``.

    Three things are asserted together because the change has to be invisible
    in two of them and visible in the third: the file holds exactly the
    serialised document plus the trailing newline, the returned path is still
    the destination the caller passed, and the file and both created directory
    components are owner-only - a worker's intermediate carries step arguments
    substituted from the Examples tables and the failure text of every failed
    step, so the mode is part of the contract and not a detail.
    """
    destination = worker_output_path(tmp_path)

    written = events.dump_result_set(sample_result_set, destination)

    assert written == destination
    assert destination.read_text(encoding="utf-8") == (
        f"{events._serialize(sample_result_set)}\n"
    )
    assert_owner_only(destination, directory=False)
    for depth in range(1, len(WORKER_DIR_COMPONENTS) + 1):
        assert_owner_only(
            tmp_path.joinpath(*WORKER_DIR_COMPONENTS[:depth]), directory=True
        )


def test_dump_result_set_refuses_a_symlinked_destination_and_keeps_the_victim(
    tmp_path: Path,
) -> None:
    """A link at the shard's name must not become a write outside the tree.

    The security report's proof of this shape was a link swapped in between
    ``ensure_parent`` and the builtin ``open``, which overwrote an external
    writable file.  ``target/.workers/`` survives a ``--no-clean`` run, so the
    name is reachable, and the merge step's own error handling relies on the
    refusal being an ``OSError``:
    ``app.utils.paths.ArtifactPathError`` is one, so a refused shard is the
    writer-failure exit class of AAP 0.4.1 rather than an unhandled crash.

    The victim's bytes are the real assertion - the exception on its own would
    also be raised by an implementation that had already truncated it.
    """
    victim = plant_victim(tmp_path / "victim.txt")
    destination = worker_output_path(tmp_path)
    destination.parent.mkdir(parents=True)
    destination.symlink_to(victim)

    with pytest.raises(paths.ArtifactPathError) as raised:
        events.dump_result_set(events.new_result_set(), destination)

    assert isinstance(raised.value, OSError)
    assert "symbolic link" in str(raised.value)
    assert_victim_intact(victim)
    assert destination.is_symlink(), "the link itself must be left alone"


def test_dump_result_set_writes_the_object_it_verified_not_the_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A name replaced *after* the open must not receive the document.

    The deterministic half of the race the finding describes.  The destination
    is turned into a link to an external file at the one moment the writer is
    holding its verified descriptor and has not yet written -- the serialiser
    is the seam that makes that instant addressable -- and the document has to
    land on the object that was opened, leaving the link's target untouched.
    Any implementation that re-resolved the pathname to write, or that reopened
    it to truncate, would empty the victim here.
    """
    victim = plant_victim(tmp_path / "victim.txt")
    destination = worker_output_path(tmp_path)
    document = events.new_result_set()
    real_serialize = events._serialize

    def swapping_serialize(candidate: Any) -> str:
        """Replace the destination with a hostile link, then serialise.

        :param candidate: The document, passed straight through.
        :returns: The serialised document.
        """
        destination.unlink()
        destination.symlink_to(victim)
        return real_serialize(candidate)

    monkeypatch.setattr(events, "_serialize", swapping_serialize)

    assert events.dump_result_set(document, destination) == destination

    assert_victim_intact(victim)
    assert destination.is_symlink(), "the swap is the point of this test"


def test_dump_result_set_refuses_a_hard_linked_destination_and_keeps_the_victim(
    tmp_path: Path,
) -> None:
    """The same refusal for the case no symlink check can see.

    A shard's name hard-linked to a file elsewhere *is* that file: every byte
    of the document would be written into it, with no link anywhere in the path
    for a link check to find.  The path authority refuses it on the link count
    of the object it opened, before the truncation, which is why both files are
    still intact afterwards.
    """
    victim = plant_victim(tmp_path / "victim.txt")
    destination = worker_output_path(tmp_path)
    destination.parent.mkdir(parents=True)
    os.link(victim, destination)

    with pytest.raises(paths.ArtifactPathError) as raised:
        events.dump_result_set(events.new_result_set(), destination)

    assert "hard link" in str(raised.value)
    assert_victim_intact(victim)
    assert_victim_intact(destination)


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
# The value rules: what a field of the right type may not say
#
# The shape rules above establish that a document has the keys and the types
# this schema documents.  These establish the rest of it: that a field of the
# right type does not carry a value the writers cannot agree on.  Each rule is
# asserted from both sides - the honest document still loads, the contradictory
# one is refused with a message naming its path - because a rule that only
# rejects is indistinguishable from a rule that rejects everything.
# ==========================================================================


def _loadable(
    tmp_path: Path,
    features: Sequence[dict[str, Any]],
    *,
    name: str = "shard.json",
    **run_fields: Any,
) -> Path:
    """Write a one-shard document to ``tmp_path`` and return its path.

    :param tmp_path: pytest's temporary directory.
    :param features: The feature objects the document carries.
    :param name: The file name, so one test can write several shards.
    :param run_fields: Run-level overrides passed to ``new_result_set``.
    :returns: The path written.
    """
    shard = tmp_path / name
    events.dump_result_set(
        events.new_result_set(metadata={}, features=list(features), **run_fields),
        shard,
    )
    return shard


def _refusal(path: Path) -> str:
    """Load ``path``, requiring it to be refused, and return the message.

    :param path: A document expected to violate the schema.
    :returns: The text of the ``ResultSetError`` raised.
    """
    with pytest.raises(events.ResultSetError) as raised:
        events.load_result_set(path)
    message = str(raised.value)
    assert str(path) in message
    return message


def _one_scenario_feature(
    *,
    element: dict[str, Any] | None = None,
    path: str = CRM_PATH,
    uri: str | None = None,
    line: int = 2,
    tags: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a feature carrying exactly one scenario element.

    :param element: The scenario element; a plain passing one by default.
    :param path: The feature's path.
    :param uri: Its URI; ``file:`` plus ``path`` by default, which is what the
        collector emits.
    :param line: The ``Feature:`` line.
    :param tags: Long-shape feature tags.
    :returns: The feature object.
    """
    return events.new_feature(
        uri=f"{paths.FILE_URI_SCHEME}{path}" if uri is None else uri,
        path=path,
        identifier=CRM_FEATURE_ID,
        line=line,
        name=CRM_FEATURE_NAME,
        tags=tags,
        elements=[_scenario(CRM_PASSING_SCENARIO_LINE) if element is None else element],
    )


def test_a_document_whose_values_are_all_honest_still_loads(tmp_path: Path) -> None:
    """The accept side of every rule below, asserted once.

    Every value rule is a way of refusing a document, so one document
    exercising all of them at their honest values is what keeps the rules from
    being vacuous: a positive line, a zero duration, a complete argument span,
    a matched step naming its definition, a scenario with an id, agreeing
    ``uri``/``path``, a long-shape feature tag beside a short-shape scenario
    tag, a Background occurrence in front of its scenario, and contract
    timestamps at both levels.
    """
    step = events.new_step(
        keyword="Then",
        line=CRM_PASSING_SCENARIO_LINE + 1,
        name='User can see "Test2" in the dashboard',
        matched=True,
        match={
            "location": "features.steps.crm_steps.user_can_see",
            "arguments": [{"val": '"Test2"', "offset": 18}],
        },
        result={"status": "skipped", "duration": 0},
    )
    scenario = events.new_element(
        element_type=events.ELEMENT_TYPE_SCENARIO,
        keyword="Scenario",
        line=CRM_PASSING_SCENARIO_LINE,
        name="User can create pipeline",
        identifier=f"{CRM_FEATURE_ID};user-can-create-pipeline",
        start_timestamp=GOLDEN_TIMESTAMP,
        tags=[events.scenario_tag("@Smoke")],
        steps=[step],
    )
    feature = _one_scenario_feature(
        element=scenario, tags=[events.feature_tag("@Smoke", 1, 1)]
    )
    feature["elements"].insert(0, _background())

    document = events.load_result_set(
        _loadable(
            tmp_path,
            [feature],
            started_at=GOLDEN_TIMESTAMP,
            generated_at=GOLDEN_TIMESTAMP,
        )
    )

    assert document["features"][0]["elements"][1]["steps"][0]["match"]["arguments"] == [
        {"val": '"Test2"', "offset": 18}
    ]


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        pytest.param(
            lambda feature: feature.update(line=0),
            "numbered from 1",
            id="feature-line-zero",
        ),
        pytest.param(
            lambda feature: feature["elements"][0].update(line=-3),
            "numbered from 1",
            id="element-line-negative",
        ),
        pytest.param(
            lambda feature: feature["elements"][0]["steps"][0].update(line=0),
            "numbered from 1",
            id="step-line-zero",
        ),
        pytest.param(
            lambda feature: feature["tags"][0]["location"].update(line=0),
            "numbered from 1",
            id="tag-line-zero",
        ),
        pytest.param(
            lambda feature: feature["tags"][0]["location"].update(column=0),
            "numbered from 1",
            id="tag-column-zero",
        ),
        pytest.param(
            lambda feature: feature["elements"][0]["steps"][0]["result"].update(
                duration=-1
            ),
            "cannot be negative",
            id="negative-step-duration",
        ),
        pytest.param(
            lambda feature: feature["elements"][0]["after"].append(
                events.new_hook_entry(status="passed", duration=0)
                | {"result": {"status": "passed", "duration": -5}}
            ),
            "cannot be negative",
            id="negative-hook-duration",
        ),
        pytest.param(
            lambda feature: feature["elements"][0]["steps"][0]["match"].update(
                arguments=[{"val": '"Test2"', "offset": -1}]
            ),
            "cannot be negative",
            id="negative-argument-offset",
        ),
    ],
)
def test_a_source_position_or_a_measured_quantity_out_of_range_is_refused(
    tmp_path: Path, mutate: Callable[[dict[str, Any]], None], expected: str
) -> None:
    """Type alone was never the rule: the range is part of the contract.

    A line of ``0`` names no position in any feature file, and every consumer
    reads these numbers for something different - the rerun manifest writes a
    feature's failing lines as machine input to the next run, the JSON artifact
    publishes them, the Pretty pages render them as source references a human
    opens - so none of them can detect the gap alone.  A negative duration is
    worse still: it is *emitted*, because the JSON writer emits the key
    whenever the value is non-zero.

    :param tmp_path: pytest's temporary directory.
    :param mutate: Applies the one out-of-range value to an otherwise honest
        feature.
    :param expected: The fragment of the rejection that names the rule.
    """
    step = events.new_step(
        keyword="Given",
        line=CRM_PASSING_SCENARIO_LINE + 1,
        name='a step taking "Test2"',
        matched=True,
        match={
            "location": "features.steps.crm_steps.a_step",
            "arguments": [{"val": '"Test2"', "offset": 14}],
        },
        result={"status": "passed", "duration": 1_000_000},
    )
    scenario = events.new_element(
        element_type=events.ELEMENT_TYPE_SCENARIO,
        keyword="Scenario",
        line=CRM_PASSING_SCENARIO_LINE,
        name="a scenario",
        identifier=f"{CRM_FEATURE_ID};a-scenario",
        start_timestamp=GOLDEN_TIMESTAMP,
        steps=[step],
    )
    feature = _one_scenario_feature(
        element=scenario, tags=[events.feature_tag("@Smoke", 1, 1)]
    )
    mutate(feature)

    assert expected in _refusal(_loadable(tmp_path, [feature]))


@pytest.mark.parametrize(
    ("matched", "match", "expected"),
    [
        pytest.param(
            True, {}, "must carry 'location'", id="matched-without-a-location"
        ),
        pytest.param(
            True,
            {"location": ""},
            "is empty for a step whose 'matched' is true",
            id="matched-with-an-empty-location",
        ),
        pytest.param(
            False,
            {"location": "features.steps.crm_steps.a_step"},
            "must be empty for a step whose 'matched' is false",
            id="unmatched-with-a-location",
        ),
        pytest.param(
            False,
            {"arguments": [{"val": "x", "offset": 0}]},
            "must be empty for a step whose 'matched' is false",
            id="unmatched-with-arguments",
        ),
    ],
)
def test_a_step_whose_match_contradicts_its_matched_flag_is_refused(
    tmp_path: Path, matched: bool, match: dict[str, Any], expected: str
) -> None:
    """``matched`` and ``match.location`` state one fact, so they must agree.

    They are read separately downstream, which is why the contradiction
    mattered: ``app/reporting/cucumber_json.py`` maps a dry run's step status
    from ``matched`` while ``app/templates/pretty/overview_steps.html`` groups
    the Steps overview by ``match.location``.  A step claiming both states at
    once was published as a passing step belonging to no definition, or as an
    undefined step on a definition the overview still counted.

    :param tmp_path: pytest's temporary directory.
    :param matched: The flag the step carries.
    :param match: The contradictory match mapping.
    :param expected: The fragment of the rejection that names the rule.
    """
    step = events.new_step(
        keyword="Given",
        line=CRM_PASSING_SCENARIO_LINE + 1,
        name="a step",
        matched=matched,
        match=match,
        result={"status": "passed", "duration": 0},
    )
    scenario = events.new_element(
        element_type=events.ELEMENT_TYPE_SCENARIO,
        keyword="Scenario",
        line=CRM_PASSING_SCENARIO_LINE,
        name="a scenario",
        identifier=f"{CRM_FEATURE_ID};a-scenario",
        start_timestamp=GOLDEN_TIMESTAMP,
        steps=[step],
    )

    message = _refusal(
        _loadable(tmp_path, [_one_scenario_feature(element=scenario)])
    )

    assert expected in message
    assert "steps[0].match" in message


@pytest.mark.parametrize(
    ("argument", "expected"),
    [
        pytest.param({"val": '"Test2"'}, "without offset", id="a-value-with-no-offset"),
        pytest.param({"offset": 3}, "without val", id="an-offset-with-no-value"),
        pytest.param(
            {"val": '"Test2"', "offset": 99},
            "past the end of the",
            id="an-offset-past-the-step-name",
        ),
        pytest.param(
            {"val": "x", "offset": len('a step taking "Test2"')},
            "spans characters",
            id="a-value-beginning-at-the-end-of-the-step-name",
        ),
        pytest.param(
            {"val": '"Test2" and more', "offset": 14},
            "spans characters",
            id="a-span-beginning-inside-and-ending-past-the-step-name",
        ),
    ],
)
def test_an_incomplete_or_unlocatable_argument_span_is_refused(
    tmp_path: Path, argument: dict[str, Any], expected: str
) -> None:
    """``val`` and ``offset`` are one datum, and the offset indexes the name.

    A ``val`` with no ``offset`` cannot be located in the step text and an
    ``offset`` with no ``val`` locates nothing, so half an argument is refused
    rather than published to a consumer that would have to guess the other
    half.  An empty mapping stays valid - it is the JVM's own shape for a
    parameter that captured no value.

    :param tmp_path: pytest's temporary directory.
    :param argument: The malformed argument mapping.
    :param expected: The fragment of the rejection that names the rule.
    """
    step = events.new_step(
        keyword="Given",
        line=CRM_PASSING_SCENARIO_LINE + 1,
        name='a step taking "Test2"',
        matched=True,
        match={
            "location": "features.steps.crm_steps.a_step",
            "arguments": [argument],
        },
        result={"status": "passed", "duration": 0},
    )
    scenario = events.new_element(
        element_type=events.ELEMENT_TYPE_SCENARIO,
        keyword="Scenario",
        line=CRM_PASSING_SCENARIO_LINE,
        name="a scenario",
        identifier=f"{CRM_FEATURE_ID};a-scenario",
        start_timestamp=GOLDEN_TIMESTAMP,
        steps=[step],
    )

    message = _refusal(
        _loadable(tmp_path, [_one_scenario_feature(element=scenario)])
    )

    assert expected in message
    assert "arguments[0]" in message


def test_a_span_ending_exactly_at_the_end_of_the_step_name_is_accepted(
    tmp_path: Path,
) -> None:
    """The span-end bound is inclusive, and the reference relies on it.

    ``"Test2"`` is the last thing in the reference's parameterized step name,
    so its span ends at exactly ``len(name)``.  The rule above refuses a span
    that ends *past* the text; a span that ends flush with it is the common
    case and must pass, or every trailing argument in the suite would be
    refused.
    """
    step_name = 'a step taking "Test2"'
    offset = step_name.index('"Test2"')
    step = events.new_step(
        keyword="Given",
        line=CRM_PASSING_SCENARIO_LINE + 1,
        name=step_name,
        matched=True,
        match={
            "location": "features.steps.crm_steps.a_step",
            "arguments": [{"val": '"Test2"', "offset": offset}],
        },
        result={"status": "passed", "duration": 0},
    )
    scenario = events.new_element(
        element_type=events.ELEMENT_TYPE_SCENARIO,
        keyword="Scenario",
        line=CRM_PASSING_SCENARIO_LINE,
        name="a scenario",
        identifier=f"{CRM_FEATURE_ID};a-scenario",
        start_timestamp=GOLDEN_TIMESTAMP,
        steps=[step],
    )

    document = events.load_result_set(
        _loadable(tmp_path, [_one_scenario_feature(element=scenario)])
    )
    argument = document["features"][0]["elements"][0]["steps"][0]["match"][
        "arguments"
    ][0]

    assert argument == {"val": '"Test2"', "offset": offset}
    assert argument["offset"] + len(argument["val"]) == len(step_name)


def test_an_empty_argument_mapping_is_the_jvm_shape_and_is_accepted(
    tmp_path: Path,
) -> None:
    """A parameter that captured no value is ``{}``, and stays ``{}``.

    ``createMatchMap`` records an empty mapping for an argument without a
    value rather than dropping the entry, so the count of arguments still
    matches the definition's parameters.  The completeness rule above must not
    refuse it.
    """
    step = events.new_step(
        keyword="Given",
        line=CRM_PASSING_SCENARIO_LINE + 1,
        name="a step with a valueless parameter",
        matched=True,
        match={
            "location": "features.steps.crm_steps.a_step",
            "arguments": [{}],
        },
        result={"status": "passed", "duration": 0},
    )
    scenario = events.new_element(
        element_type=events.ELEMENT_TYPE_SCENARIO,
        keyword="Scenario",
        line=CRM_PASSING_SCENARIO_LINE,
        name="a scenario",
        identifier=f"{CRM_FEATURE_ID};a-scenario",
        start_timestamp=GOLDEN_TIMESTAMP,
        steps=[step],
    )

    document = events.load_result_set(
        _loadable(tmp_path, [_one_scenario_feature(element=scenario)])
    )

    element = document["features"][0]["elements"][0]
    assert element["steps"][0]["match"]["arguments"] == [{}]


def test_a_scenario_with_an_empty_id_is_refused(tmp_path: Path) -> None:
    """The id is the identity every artifact keys on, so it must survive.

    An empty one used to reach the JSON writer, which rebuilt it from the
    feature and scenario names - correct for a plain scenario and **wrong for
    an Examples row**, whose id carries the Examples block's slug and the
    row's position, neither of which any other field of the element records.
    The artifact then named a test case the suite does not contain.
    """
    scenario = _scenario(CRM_PASSING_SCENARIO_LINE)
    scenario["id"] = ""

    message = _refusal(
        _loadable(tmp_path, [_one_scenario_feature(element=scenario)])
    )

    assert "elements[0].id" in message
    assert "is empty" in message


def test_new_element_refuses_to_build_a_scenario_without_an_id() -> None:
    """The build side of the same rule, where the id can still be computed.

    ``scenario_element_id`` is the one producer of a scenario id, and it always
    returns a non-empty value.  A caller that supplies none is not building a
    sparse element, it is building a test case with no identity, so the builder
    refuses rather than emitting one for a consumer to invent.
    """
    with pytest.raises(ValueError, match="non-empty identifier"):
        events.new_element(
            element_type=events.ELEMENT_TYPE_SCENARIO,
            keyword="Scenario",
            line=CRM_PASSING_SCENARIO_LINE,
            name="a scenario with no id",
        )


@pytest.mark.parametrize(
    ("uri", "path", "expected"),
    [
        pytest.param("", CRM_PATH, "is empty", id="an-empty-uri"),
        pytest.param(
            f"{paths.FILE_URI_SCHEME}{CRM_PATH}", "", "is empty", id="an-empty-path"
        ),
        pytest.param(
            f"{paths.FILE_URI_SCHEME}{CRM_PATH}",
            f"{paths.NORMALIZED_FEATURES_PREFIX}Notes.feature",
            "requires",
            id="a-uri-and-path-that-name-different-files",
        ),
        pytest.param(
            CRM_PATH, CRM_PATH, "requires", id="a-uri-with-no-scheme"
        ),
        pytest.param(
            f"{paths.FILE_URI_SCHEME}{paths.FILE_URI_SCHEME}{CRM_PATH}",
            f"{paths.FILE_URI_SCHEME}{CRM_PATH}",
            "carries the 'file:' scheme",
            id="a-scheme-on-the-path-and-two-on-the-uri",
        ),
        pytest.param(
            f"{paths.FILE_URI_SCHEME}{paths.FILE_URI_SCHEME}{CRM_PATH}",
            CRM_PATH,
            "requires",
            id="a-doubled-scheme-on-the-uri",
        ),
    ],
)
def test_a_feature_whose_uri_and_path_are_not_canonical_is_refused(
    tmp_path: Path, uri: str, path: str, expected: str
) -> None:
    """One identity in two **fixed** spellings, read by different consumers.

    The JSON writer copies ``uri``, PrettyReports hashes it to name each
    feature's detail page, and the rerun manifest and the merge use ``path``.
    So a feature carrying only one member, or two that name different files,
    is published under one name, linked under a hash of another and re-run
    from a third - and no single consumer can see it, because each reads one
    member.

    Agreement alone is not enough, which is why the last three cases are
    here.  AAP 0.6 fixes the emitted ``uri`` as exactly one ``file:`` prefix
    in front of a repository-relative path, and
    ``app/reporting/rerun_report.py`` adds that prefix itself in front of
    ``path`` - so a scheme-less ``uri`` publishes a shape the artifact's
    schema does not describe, and a scheme *on* ``path`` is written twice into
    the manifest the next ``--rerun`` reads.  A pair that agrees on the wrong
    shape passes an equality-after-strip test and is refused here.

    :param tmp_path: pytest's temporary directory.
    :param uri: The feature's URI.
    :param path: The feature's path.
    :param expected: The fragment of the rejection that names the rule.
    """
    feature = _one_scenario_feature(path=path or CRM_PATH, uri=uri)
    feature["path"] = path

    assert expected in _refusal(_loadable(tmp_path, [feature]))


def test_the_canonical_pair_is_what_the_collector_writes(tmp_path: Path) -> None:
    """The accept side: ``uri`` is the scheme followed by ``path``, exactly.

    This is what :meth:`ResultCollectorFormatter.feature` emits - it builds
    the URI as ``FILE_URI_SCHEME`` plus the normalised path - so the rule
    above refuses nothing a real shard contains.
    """
    document = events.load_result_set(
        _loadable(tmp_path, [_one_scenario_feature()])
    )
    feature = document["features"][0]

    assert feature["path"] == CRM_PATH
    assert feature["uri"] == f"{paths.FILE_URI_SCHEME}{CRM_PATH}"


@pytest.mark.parametrize(
    ("elements", "expected"),
    [
        pytest.param(
            ["background", "background", "scenario"],
            "following the one at index 0",
            id="two-backgrounds-in-a-row",
        ),
        pytest.param(
            ["scenario", "background"],
            "trailing background",
            id="a-background-with-no-scenario-after-it",
        ),
    ],
)
def test_an_element_list_that_is_not_units_is_refused(
    tmp_path: Path, elements: Sequence[str], expected: str
) -> None:
    """A Background occurrence is emitted *for* a scenario, so it precedes one.

    Four consumers group the flat list back into units - the merge to order
    them, the JSON writer to keep or drop one whole, the rerun writer to fold a
    failing Background into its scenario, the aggregate to count the unit once
    - and on a list that is not units they disagreed silently, one dropping a
    stray occurrence, one attaching it to the next scenario, one counting it as
    a test case.

    :param tmp_path: pytest's temporary directory.
    :param elements: The element kinds to lay out, in order.
    :param expected: The fragment of the rejection that names the rule.
    """
    built: list[dict[str, Any]] = []
    for index, kind in enumerate(elements):
        if kind == "background":
            built.append(_background())
        else:
            built.append(_scenario(CRM_PASSING_SCENARIO_LINE + index))
    feature = _one_scenario_feature()
    feature["elements"] = built

    assert expected in _refusal(_loadable(tmp_path, [feature]))


def test_a_background_that_does_not_share_its_scenarios_fate_is_refused(
    tmp_path: Path,
) -> None:
    """An occurrence is kept or dropped with the scenario it ran for.

    ``selected`` is what the JSON writer filters on, and it drops a unit whole:
    a Background whose flag disagreed with its scenario's was either emitted
    for a test case the artifact does not contain, or dropped from one it does.
    """
    background = _background()
    background["selected"] = False
    feature = _one_scenario_feature()
    feature["elements"].insert(0, background)

    message = _refusal(_loadable(tmp_path, [feature]))

    assert "does not share the 'selected' value" in message


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        pytest.param(
            [{"name": "@Smoke"}],
            "must carry 'location'",
            id="a-feature-tag-in-the-short-shape",
        ),
        pytest.param(
            [{"name": "@Smoke", "type": "Tag", "location": {"line": 1}}],
            "must carry 'column'",
            id="a-feature-tag-with-half-a-location",
        ),
        pytest.param(
            [{"name": "@Smoke", "type": "Annotation", "location": {"line": 1, "column": 1}}],
            "is the literal 'Tag'",
            id="a-feature-tag-with-another-type",
        ),
        pytest.param(
            [{"name": "Smoke", "type": "Tag", "location": {"line": 1, "column": 1}}],
            "carries the leading '@'",
            id="a-tag-name-without-its-at-sign",
        ),
    ],
)
def test_a_feature_tag_must_carry_the_long_shape_exactly(
    tmp_path: Path, tags: Sequence[dict[str, Any]], expected: str
) -> None:
    """The long shape is what lets the JSON writer copy rather than invent.

    A feature tag arriving without its own ``location`` left that writer
    nothing to copy but the feature's line - and the baseline's ``@Smoke`` sits
    on line 1 while its ``Feature:`` keyword sits on line 2, so the synthesised
    value was wrong for exactly the case the artifact pins.

    :param tmp_path: pytest's temporary directory.
    :param tags: The feature-level tags to carry.
    :param expected: The fragment of the rejection that names the rule.
    """
    feature = _one_scenario_feature()
    feature["tags"] = [dict(tag) for tag in tags]

    assert expected in _refusal(_loadable(tmp_path, [feature]))


@pytest.mark.parametrize(
    "tag",
    [
        pytest.param(
            {"name": "@Smoke", "type": "Tag", "location": {"line": 1, "column": 1}},
            id="a-scenario-tag-in-the-long-shape",
        ),
        pytest.param(
            {"name": "@Smoke", "location": {"line": 1, "column": 1}},
            id="a-scenario-tag-with-a-location",
        ),
    ],
)
def test_a_scenario_tag_must_carry_the_short_shape_exactly(
    tmp_path: Path, tag: dict[str, Any]
) -> None:
    """A scenario tag is ``{"name": ...}`` and nothing else.

    The asymmetry with a feature tag is measured in the reference, where the
    same ``@Smoke`` carries a type and a location at feature level and neither
    on any of the four scenario elements.  A ``location`` accepted here would
    reach an artifact whose scenario tags have never carried one.

    :param tmp_path: pytest's temporary directory.
    :param tag: The over-specified scenario tag.
    """
    scenario = _scenario(CRM_PASSING_SCENARIO_LINE)
    scenario["tags"] = [tag]

    message = _refusal(
        _loadable(tmp_path, [_one_scenario_feature(element=scenario)])
    )

    assert "carries the unknown key" in message
    assert "tags[0]" in message


@pytest.mark.parametrize(
    "spelling",
    [
        pytest.param("2022-09-07T13:37:26.297123Z", id="microsecond-precision"),
        pytest.param("2022-09-07T13:37:26.297+00:00", id="an-explicit-utc-offset"),
        pytest.param("2022-09-07 13:37:26.297Z", id="a-space-instead-of-the-t"),
        pytest.param("2022-09-07T13:37:26Z", id="no-fractional-digits"),
        pytest.param("not a timestamp", id="free-text"),
    ],
)
def test_a_timestamp_in_any_other_spelling_is_refused(
    tmp_path: Path, spelling: str
) -> None:
    """One spelling, because three surfaces read these values differently.

    ``app/templates/index.html`` and both HTML writers display the string,
    ``app/reporting/aggregation.py`` parses it and drops what it cannot read,
    and the merge orders by it - so a value only some of them understand
    became a literal on one page and a missing run start on another, with
    nothing left able to name the shard it came from.

    :param tmp_path: pytest's temporary directory.
    :param spelling: The timestamp spelling under test.
    """
    scenario = _scenario(CRM_PASSING_SCENARIO_LINE)
    scenario["start_timestamp"] = spelling

    message = _refusal(
        _loadable(tmp_path, [_one_scenario_feature(element=scenario)])
    )

    assert "start_timestamp" in message
    assert "YYYY-MM-DDTHH:MM:SS.mmmZ" in message


@pytest.mark.parametrize("key", ["started_at", "generated_at"])
def test_a_run_level_timestamp_in_any_other_spelling_is_refused(
    tmp_path: Path, key: str
) -> None:
    """The same rule at run level, where the HTML metadata block reads it.

    :param tmp_path: pytest's temporary directory.
    :param key: The run-level timestamp under test.
    """
    shard = _loadable(tmp_path, [_one_scenario_feature()], **{key: None})
    document = json.loads(shard.read_text(encoding="utf-8"))
    document[key] = "07/09/2022 13:37"
    shard.write_text(json.dumps(document), encoding="utf-8")

    message = _refusal(shard)

    assert key in message
    assert "YYYY-MM-DDTHH:MM:SS.mmmZ" in message


def test_a_null_timestamp_stays_admissible_at_both_levels(tmp_path: Path) -> None:
    """``None`` is a state this schema has, so it is not a malformed value.

    ``new_result_set`` starts both run-level stamps at ``None`` and a scenario
    the tag expression excluded is announced without one, so the timestamp
    rule refuses a *string* it cannot read rather than the absence of a value.
    """
    scenario = _scenario(CRM_PASSING_SCENARIO_LINE)
    scenario["start_timestamp"] = None
    scenario["selected"] = False

    document = events.load_result_set(
        _loadable(tmp_path, [_one_scenario_feature(element=scenario)])
    )

    assert document["started_at"] is None
    assert document["generated_at"] is None
    assert document["features"][0]["elements"][0]["start_timestamp"] is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param(
            GOLDEN_TIMESTAMP,
            GOLDEN_INSTANT.replace(microsecond=297_000),
            id="the-contract-spelling",
        ),
        pytest.param("2022-09-07T13:37:26.297123Z", None, id="microseconds"),
        pytest.param("2022-13-07T13:37:26.297Z", None, id="an-impossible-month"),
        pytest.param("2022-02-31T13:37:26.297Z", None, id="an-impossible-day"),
        pytest.param(None, None, id="none"),
        pytest.param(1662557846, None, id="an-epoch-integer"),
    ],
)
def test_parse_timestamp_accepts_the_contract_spelling_and_nothing_else(
    text: Any, expected: datetime | None
) -> None:
    """The inverse of ``format_timestamp``, exact in both directions.

    The pattern establishes the shape and ``strptime`` parses the fields, which
    is what makes an impossible date fail rather than pass a shape check.

    :param text: The value to parse.
    :param expected: The instant it names, or ``None``.
    """
    assert events.parse_timestamp(text) == expected


def test_a_run_start_ignores_a_scenario_the_filter_excluded(tmp_path: Path) -> None:
    """The run's start is the first **selected** scenario's, not the first seen.

    behave announces an excluded scenario at the point the run reached it, and
    this document records it, but the JSON artifact omits it entirely and
    ``app/reporting/aggregation.py`` computes its earliest start over what
    survives selection.  Counting it here made the two disagree, and
    ``app/reporting/html_report.py`` prefers this one - so one page showed a
    run starting before the first scenario it lists.
    """
    excluded = _scenario(CRM_PASSING_SCENARIO_LINE)
    excluded["selected"] = False
    excluded["start_timestamp"] = events.format_timestamp(GOLDEN_INSTANT)
    selected = _scenario(CRM_PASSING_SCENARIO_LINE + 10)
    selected["start_timestamp"] = events.format_timestamp(
        GOLDEN_INSTANT + timedelta(minutes=5)
    )
    feature = _one_scenario_feature()
    feature["elements"] = [excluded, selected]

    merged = events.merge_result_sets(
        [events.load_result_set(_loadable(tmp_path, [feature]))]
    )

    assert merged["started_at"] == selected["start_timestamp"]


def test_the_merge_orders_the_run_stamps_by_instant_and_keeps_the_spelling() -> None:
    """Ordering is by parsed instant; the string comes back as it arrived.

    The two agree for this fixed-width UTC spelling, which is why a string
    comparison was defensible - and they stop agreeing the moment a hand-built
    document carries anything else, which this function accepts.  A value no
    consumer can parse must not be able to become the run's start.
    """
    early = "2022-09-07T13:37:26.297Z"
    late = "2022-09-07T13:39:12.484Z"
    merged = events.merge_result_sets(
        [
            {"features": [], "started_at": late, "generated_at": early},
            {"features": [], "started_at": early, "generated_at": late},
            {"features": [], "started_at": "yesterday", "generated_at": ""},
        ]
    )

    assert merged["started_at"] == early
    assert merged["generated_at"] == late


def test_the_merge_reports_shards_recorded_under_different_tag_expressions(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``tag_expression``'s one consumer, and what it is retained for.

    Every worker of a run is launched with the same recorded expression, so two
    shards carrying different ones are shards from two different runs - a stale
    intermediate, or two runs sharing an output directory.  The merge still
    returns the first, because refusing would discard results the run did
    produce, but it says so: a merged report describing two filters with no
    trace of which scenarios came from which is the outcome the warning exists
    to prevent.
    """
    with caplog.at_level(logging.WARNING, logger=events.__name__):
        merged = events.merge_result_sets(
            [
                {"features": [], "tag_expression": "@Smoke"},
                {"features": [], "tag_expression": "not @wip"},
            ]
        )

    assert merged["tag_expression"] == "@Smoke"
    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "@Smoke" in message and "not @wip" in message for message in messages
    ), f"the disagreement was not reported: {messages}"


def test_the_merge_says_nothing_when_every_shard_agrees(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A run whose shards agree is the normal case and must stay quiet.

    A warning on every merge would be noise, and noise is what makes the real
    one invisible.
    """
    with caplog.at_level(logging.WARNING, logger=events.__name__):
        merged = events.merge_result_sets(
            [
                {"features": [], "tag_expression": "@Smoke"},
                {"features": [], "tag_expression": "@Smoke"},
                {"features": [], "tag_expression": None},
            ]
        )

    assert merged["tag_expression"] == "@Smoke"
    assert caplog.records == []


def test_element_units_is_the_grouping_every_consumer_shares() -> None:
    """One grouping, exported, so four consumers cannot disagree about it.

    A Background occurrence belongs immediately in front of its scenario, and
    the unit is what the merge orders, the JSON writer keeps or drops whole,
    the rerun writer folds together and the aggregate counts once.  A
    hand-built list that is not units keeps its odd occurrence as a unit of its
    own rather than losing it.
    """
    background = _background()
    first = _scenario(CRM_PASSING_SCENARIO_LINE)
    second = _scenario(CRM_PASSING_SCENARIO_LINE + 7)

    assert events.element_units([background, first, second]) == [
        [background, first],
        [second],
    ]
    assert events.element_units([]) == []
    assert events.element_units([background, background]) == [
        [background],
        [background],
    ]
    assert events.element_units([first, background]) == [[first], [background]]
    assert events.element_units(["not an element", first]) == [[first]]


def test_every_grouping_consumer_agrees_with_the_exported_one() -> None:
    """The seam: a second grouping must not disagree with this one.

    :func:`app.reporting.events.element_units` is the shared rule and
    ``app/reporting/cucumber_json.py`` consumes it directly.
    ``app/reporting/aggregation.py`` still carries a grouping of its own, and
    that file belongs to another work unit, so this asserts the property that
    matters rather than the import: over every shape a validated document can
    hold, the two groupings return the same units.  The ``getattr`` is
    deliberate - when ``aggregation`` is changed to import the exported
    function, this test keeps passing and compares it against itself.

    The comparison is restricted to validated shapes on purpose.  Since
    :func:`app.reporting.events._check_element_units` refuses a malformed list
    at ingress, ``[background, scenario]`` and ``[scenario]`` are the only
    unit shapes a loaded document can contain; the two implementations differ
    only on a non-mapping element, which no loaded document can carry.
    """
    theirs = getattr(aggregation, "element_units", events.element_units)
    background = _background()
    first = _scenario(CRM_PASSING_SCENARIO_LINE)
    second = _scenario(CRM_PASSING_SCENARIO_LINE + 7)
    validated_shapes: list[list[dict[str, Any]]] = [
        [],
        [first],
        [background, first],
        [background, first, background, second],
        [first, second],
        [background, first, second],
    ]

    for elements in validated_shapes:
        assert theirs(elements) == events.element_units(elements), elements


def test_a_run_budget_refuses_the_shard_that_would_exceed_the_run(
    tmp_path: Path,
) -> None:
    """Per-file limits bound a file; the parent holds every shard at once.

    ``app/services/test_run_service.py`` collects each live shard's document
    into a list before the merge reads any of it, so 87 files each just inside
    the per-file cap would have the parent hold about 21.75 GiB - every file
    individually valid, the sum fatal.  The budget refuses the shard that
    crosses the line, with the same error the caller already treats as a dead
    shard, so the run still publishes what the other shards produced.
    """
    first = _loadable(tmp_path, [_one_scenario_feature()], name="worker-0.json")
    second = _loadable(tmp_path, [_one_scenario_feature()], name="worker-1.json")
    budget = events.RunResultBudget(
        max_bytes=first.stat().st_size + second.stat().st_size // 2
    )

    loaded = events.load_result_set(first, budget=budget)

    assert budget.documents == 1
    assert budget.total_bytes == first.stat().st_size
    assert budget.total_nodes > 0
    assert loaded["features"][0]["path"] == CRM_PATH

    message = _refusal_with_budget(second, budget)

    assert "MAX_RUN_RESULT_BYTES" in message
    assert budget.documents == 1, "a refused shard must not be charged"


def _refusal_with_budget(path: Path, budget: events.RunResultBudget) -> str:
    """Load ``path`` under ``budget``, requiring it to be refused.

    :param path: A shard expected to breach the run budget.
    :param budget: The run's budget.
    :returns: The text of the ``ResultSetError`` raised.
    """
    with pytest.raises(events.ResultSetError) as raised:
        events.load_result_set(path, budget=budget)
    return str(raised.value)


def test_a_run_budget_bounds_the_shard_count_and_the_node_total(
    tmp_path: Path,
) -> None:
    """Bytes are not the only way a run of valid shards exhausts the parent.

    The node total is what the merge's ``copy.deepcopy`` of every feature
    costs, and the document count is what a worker directory holding the
    intermediates of many runs produces.  Each limit is charged separately and
    each names itself, so the reason a shard was refused is diagnosable.
    """
    shard = _loadable(tmp_path, [_one_scenario_feature()])

    assert "MAX_RUN_RESULT_DOCUMENTS" in _refusal_with_budget(
        shard, events.RunResultBudget(max_documents=0)
    )
    assert "MAX_RUN_RESULT_NODES" in _refusal_with_budget(
        shard, events.RunResultBudget(max_nodes=1)
    )


def test_the_default_run_budget_admits_the_whole_suite_in_one_shard(
    sample_result_set: dict[str, Any], tmp_path: Path
) -> None:
    """The budget must not refuse honest work, so its defaults are asserted.

    The sample is a four-feature merged document and the defaults are sized for
    a run of the whole suite, so loading it under a default budget leaves the
    run far below every limit.  A budget that rejected this would fail a real
    run rather than a hostile one.
    """
    shard = tmp_path / "whole-suite.json"
    events.dump_result_set(sample_result_set, shard)
    budget = events.RunResultBudget()

    events.load_result_set(shard, budget=budget)

    assert budget.documents == 1
    assert budget.total_bytes < events.MAX_RUN_RESULT_BYTES
    assert budget.total_nodes < events.MAX_RUN_RESULT_NODES


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


def test_match_arguments_keep_a_valueless_entry_and_locate_a_lost_span(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    crm_feature: StubFeature,
) -> None:
    """The JVM never drops an argument entry, and neither does the collector.

    Three shapes, and the *count* of arguments is the same in all three,
    because ``createMatchMap`` records an entry per parameter of the
    definition and the position of each one is what a consumer reads:

    * an argument with **no value** is the empty mapping the JVM writes;
    * an argument whose reported span does not index into the step name - a
      type-converted parameter - is **located** in the name, because ``val``
      and ``offset`` are read as the position of a value in the step text and
      the schema requires the span to fit inside it, so the reported offset
      cannot simply be carried;
    * an argument whose text does not occur in the name at all has no span to
      record, so it too becomes the empty mapping rather than a fabricated
      position.

    The middle case is the real one: behave's own offset is nonsense here and
    the value *is* in the text, so recording it at the offset it actually
    occupies is both truthful and what makes the emitted artifact usable.
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
                    Argument(500, 505, "search", "search"),
                    Argument(500, 505, "Lucas", "Lucas"),
                ],
            )
        ],
    )

    run_feature(collector, crm_feature, [scenario])
    feature = feature_by_path(read_document(collector), CRM_PATH)
    arguments = feature["elements"][-1]["steps"][0]["match"]["arguments"]

    assert len(arguments) == 3
    assert arguments[0] == {}
    assert arguments[1] == {"val": "search", "offset": step_name.index("search")}
    assert (
        step_name[
            arguments[1]["offset"] : arguments[1]["offset"]
            + len(arguments[1]["val"])
        ]
        == arguments[1]["val"]
    )
    assert arguments[2] == {}


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
    """The failure text is the assertion's own message, with LF endings.

    AAP deviation 16: the reference's failure text is a JUnit message and a
    Java stack trace with ``\\r\\n`` endings, which Python cannot produce, so
    the *subject and message* are parity and their formatting is not.  What
    this module builds is therefore pinned exactly: the assertion's own
    message text, then the traceback, with every line ending normalised to
    ``\\n``.

    behave's own prefix is **stripped**, which is the assertion this test
    exists for.  ``Step._process_error`` writes ``"ASSERT FAILED: <message>"``
    - and ``"ERROR: <Class>: <message>"`` for anything else - which is
    behave's formatting rather than the assertion's subject, and the JVM's
    text carries neither.  A prefix that survived would reach the JSON
    artifact, both HTML families and every failure overview as part of the
    failure's identity.
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
    assert message.startswith(assertion_text), (
        "behave's own ASSERT FAILED prefix reached the document: "
        f"{message!r}"
    )
    assert "ASSERT FAILED" not in message
    assert message.endswith(
        '  File "features/steps/crm_steps.py", line 61\n'
    )


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
# The redaction boundary: what a report may keep (SEC2-F03 and SEC2-F20)
#
# Two fields are worker-controlled text a report then keeps for as long as the
# build is archived: a step's ``name`` with its ``match.arguments[].val``, and
# ``result.error_message``.  The suite's login phrases substitute an account
# name and a password straight into the first pair, and a traceback puts
# workspace paths and whatever an exception quoted into the second.  This
# section pins both rules, in both directions: what must be masked, and what
# must survive untouched - an over-redacted report is as useless as a leaky
# one, and AAP 0.6 freezes the assertion subjects and the argument offsets.
# ==========================================================================


def quoted_arguments(step_name: str) -> list[Argument]:
    """behave ``Argument`` instances for every quoted run of ``step_name``.

    behave reports a ``{}``-placeholder span *inside* the quotes - the port's
    step phrases carry the quotes in the phrase literal - which is what
    :func:`app.reporting.events.widen_quoted_span` then widens.  Building the
    spans that way rather than by hand is what keeps these tests measuring the
    collector's own arithmetic.

    :param step_name: The substituted step text.
    :returns: One argument per quoted run, left to right.
    """
    return [
        Argument(
            match.start() + 1,
            match.end() - 1,
            step_name[match.start() + 1 : match.end() - 1],
            step_name[match.start() + 1 : match.end() - 1],
        )
        for match in re.finditer(r'"[^"]*"', step_name)
    ]


def collect_one_step(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    feature: StubFeature,
    step: StubStep,
) -> tuple[dict[str, Any], str]:
    """Run one step through the collector and return its record and the file.

    :param make_collector: The collector factory fixture.
    :param feature: The feature to announce it under.
    :param step: The step to announce.
    :returns: The step object from the written document, and the document's
        whole text - which is what a "this value is nowhere in the artifact"
        assertion needs.
    """
    collector = make_collector(source_lines=[])
    scenario = StubScenario(
        "User logs in", CRM_PASSING_SCENARIO_LINE, steps=[step]
    )
    run_feature(collector, feature, [scenario])
    document = read_document(collector)
    text = Path(collector.stream_opener.name).read_text(encoding="utf-8")
    return feature_by_path(document, CRM_PATH)["elements"][-1]["steps"][0], text


def assert_offsets_index_into_the_name(step: dict[str, Any]) -> None:
    """Assert the contract ``name[offset:offset + len(val)] == val``.

    Applied to a *redacted* step, this is the assertion that the masking
    preserved the one invariant ``match.arguments`` has: a consumer -- the
    Jenkins publisher among them -- slices the emitted name with the emitted
    offset, so a mask that moved the text without moving the offsets would
    hand it a different value than the one recorded.

    :param step: A step object carrying ``name`` and ``match``.
    """
    name = step["name"]
    for argument in step["match"].get("arguments", ()):
        if not argument:
            continue
        offset, value = argument["offset"], argument["val"]
        assert name[offset : offset + len(value)] == value, (
            f"{value!r} at {offset} does not index into {name!r}"
        )


def test_a_credential_bearing_step_is_stored_redacted(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    crm_feature: StubFeature,
) -> None:
    """``User enters "<username>" username`` reaches no file in the clear.

    Review finding SEC2-F03: behave hands the collector the *substituted* step
    text, so for a Login or Logout ``Examples`` row that text is an account
    name and ``match.arguments`` carries it a second time.  Both are masked
    before they are serialized, the quotes survive because the contract says
    ``val`` includes them, and the offset still indexes into the name.

    The final assertion is the one that matters operationally: the value is
    absent from the *whole written document*, not merely from the two fields
    this test knows to look at.
    """
    step = StubStep(
        "When", LOGIN_USERNAME_STEP_NAME, 15,
        status=Status.passed, duration=1.0,
        func=make_step_function(
            "features/steps/login_steps.py", "user_enters_username"
        ),
        arguments=quoted_arguments(LOGIN_USERNAME_STEP_NAME),
    )

    record, document_text = collect_one_step(make_collector, crm_feature, step)

    assert record["name"] == "User enters \"[redacted]\" username"
    assert record["match"]["arguments"] == [
        {"val": REDACTED_QUOTED_VALUE, "offset": 12}
    ]
    assert_offsets_index_into_the_name(record)
    assert "account7@example.test" not in document_text


def test_a_password_is_classified_by_the_words_around_it(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    crm_feature: StubFeature,
) -> None:
    """The half of ``Login.feature`` that only adjacency can catch.

    ``User enters "<password>" password`` [Login.feature:16] substitutes a
    value whose own text is indistinguishable from a product name -- nothing
    about it is credential-shaped -- so the following ``password`` is the whole
    of the evidence.  A rule that classified values in isolation would publish
    this one.
    """
    step = StubStep(
        "And", LOGIN_PASSWORD_STEP_NAME, 16,
        status=Status.passed, duration=1.0,
        func=make_step_function(
            "features/steps/login_steps.py", "user_enters_password"
        ),
        arguments=quoted_arguments(LOGIN_PASSWORD_STEP_NAME),
    )

    record, document_text = collect_one_step(make_collector, crm_feature, step)

    assert record["name"] == "User enters \"[redacted]\" password"
    assert record["match"]["arguments"][0]["val"] == REDACTED_QUOTED_VALUE
    assert_offsets_index_into_the_name(record)
    assert "not-a-real-secret" not in document_text


def test_only_the_classified_argument_position_is_masked(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    crm_feature: StubFeature,
) -> None:
    """``Contact.feature:12``'s two values are judged one at a time.

    The phrase substitutes a phone number and an email address.  The address
    is classified on its own terms; the phone number is not in the closed
    keyword set and no keyword sits next to it, so it survives **byte for
    byte** -- which is what keeps a contact-creation failure diagnosable.
    """
    step = StubStep(
        "And", CONTACT_TWO_ARGUMENT_STEP_NAME, 12,
        status=Status.passed, duration=1.0,
        func=make_step_function(
            "features/steps/contacts_steps.py", "user_enters_phone_and_email"
        ),
        arguments=quoted_arguments(CONTACT_TWO_ARGUMENT_STEP_NAME),
    )

    record, document_text = collect_one_step(make_collector, crm_feature, step)
    arguments = record["match"]["arguments"]

    assert arguments[0] == {"val": '"+99999999999"', "offset": 12}
    assert arguments[1]["val"] == REDACTED_QUOTED_VALUE
    assert_offsets_index_into_the_name(record)
    assert '"+99999999999"' in record["name"]
    assert "someone@example.test" not in document_text


def test_an_offset_after_a_masked_span_is_shifted(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    crm_feature: StubFeature,
) -> None:
    """The placeholder is not the length of what it replaces, so offsets move.

    With the masked value *first*, the surviving value's offset has to move by
    the length delta -- 12 characters of ``"[redacted]"`` for the 22 of the
    address it replaced.  An implementation that masked the text and left the
    offsets alone would still pass every assertion above and fail here, with
    the publisher slicing the wrong substring out of the name it was given.
    """
    step = StubStep(
        "And", CONTACT_SWAPPED_ARGUMENT_STEP_NAME, 12,
        status=Status.passed, duration=1.0,
        func=make_step_function(
            "features/steps/contacts_steps.py", "user_enters_email_and_phone"
        ),
        arguments=quoted_arguments(CONTACT_SWAPPED_ARGUMENT_STEP_NAME),
    )

    record, _ = collect_one_step(make_collector, crm_feature, step)
    arguments = record["match"]["arguments"]
    delta = len(REDACTED_QUOTED_VALUE) - len('"someone@example.test"')

    assert arguments[0] == {"val": REDACTED_QUOTED_VALUE, "offset": 12}
    assert arguments[1]["val"] == '"+99999999999"'
    assert arguments[1]["offset"] == (
        CONTACT_SWAPPED_ARGUMENT_STEP_NAME.index('"+99999999999"') + delta
    )
    assert_offsets_index_into_the_name(record)


def test_the_reference_outline_step_is_left_exactly_as_it_was(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    crm_feature: StubFeature,
) -> None:
    """The over-redaction guard, on the one parameterised step in the baseline.

    ``User can change any user's information like "Test2" , "30" and "2"``
    carries the word ``user`` twice and three quoted values, none of them a
    credential.  Its name and all three offsets are frozen by AAP 0.6 and are
    asserted here against the *measured* reference values, so a widened
    keyword set or an entropy heuristic fails this test rather than silently
    emptying the artifact the Jenkins publisher reads.
    """
    step = StubStep(
        "And", GOLDEN_STEP_NAME, 21,
        status=Status.passed, duration=GOLDEN_DURATION_SECONDS,
        func=make_step_function(
            "features/steps/crm_steps.py", "user_can_change_any_user_s_information"
        ),
        arguments=golden_arguments(),
    )

    record, _ = collect_one_step(make_collector, crm_feature, step)
    arguments = record["match"]["arguments"]

    assert record["name"] == GOLDEN_STEP_NAME
    assert [argument["val"] for argument in arguments] == list(GOLDEN_ARGUMENT_VALUES)
    assert [argument["offset"] for argument in arguments] == list(
        GOLDEN_ARGUMENT_OFFSETS
    )
    assert_offsets_index_into_the_name(record)


def test_an_undefined_steps_name_is_redacted_too(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    crm_feature: StubFeature,
) -> None:
    """``NoMatch`` means no arguments, and no reason to publish the text.

    A step behave could not resolve still had its ``Examples`` row
    substituted into it, and it reaches the document through
    :func:`app.reporting.events.new_step` with an empty ``match``.  That is
    why the boundary sits in the constructor as well as in the match handler:
    with no argument spans to preserve, the whole text is put through the
    sanitizer, which is what removes the quotes as well.
    """
    step = StubStep(
        "When", LOGIN_USERNAME_STEP_NAME, 15,
        status=Status.undefined, duration=0.0, func=None,
    )

    record, document_text = collect_one_step(make_collector, crm_feature, step)

    assert record["match"] == {}
    assert record["matched"] is False
    assert record["name"] == "User enters [redacted] username"
    assert "account7@example.test" not in document_text


def test_redact_step_text_is_idempotent() -> None:
    """The producer and the writer both apply it, so it must be a fixed point.

    ``app/reporting/cucumber_json.py`` re-applies the rule at the publish
    boundary because it treats a worker's JSON file as untrusted input.  That
    is only safe if a second application changes nothing -- otherwise every
    writer would mask the placeholder's own quotes again and the offsets would
    drift one hop at a time.
    """
    for name in (
        LOGIN_USERNAME_STEP_NAME,
        LOGIN_PASSWORD_STEP_NAME,
        CONTACT_TWO_ARGUMENT_STEP_NAME,
        CONTACT_SWAPPED_ARGUMENT_STEP_NAME,
        GOLDEN_STEP_NAME,
    ):
        arguments = [
            {"val": value, "offset": offset}
            for value, offset in (
                events.widen_quoted_span(name, argument.start, argument.end)
                for argument in quoted_arguments(name)
            )
        ]
        once = events.redact_step_text(name, arguments)
        twice = events.redact_step_text(*once)

        assert twice == once, f"redaction is not a fixed point for {name!r}"
        text_once = events.redact_step_text(name)
        assert events.redact_step_text(text_once[0]) == text_once


def test_redact_step_text_keeps_an_empty_argument_entry() -> None:
    """Arity is part of the contract, so an empty entry stays an empty entry.

    ``createMatchMap`` records ``{}`` for an argument with no value rather
    than dropping it, and the collector already reproduced that; a redaction
    that filtered the list would change a step's parameter count.
    """
    name = LOGIN_PASSWORD_STEP_NAME
    redacted, arguments = events.redact_step_text(
        name, [{}, {"val": '"not-a-real-secret"', "offset": 12}]
    )

    assert len(arguments) == 2
    assert arguments[0] == {}
    assert arguments[1] == {"val": REDACTED_QUOTED_VALUE, "offset": 12}
    assert redacted == "User enters \"[redacted]\" password"


def test_redact_step_text_masks_a_value_whose_span_was_lost() -> None:
    """An argument the collector recovered from ``original`` is classified too.

    When behave reports a span that does not index into the name -- a
    type-converted parameter -- the collector records the matched text with
    the best offset it has.  There is then no window to search, so the *step's
    own text* decides: a step that names a credential field has no
    unclassified argument values.
    """
    _, arguments = events.redact_step_text(
        LOGIN_PASSWORD_STEP_NAME, [{"val": "not-a-real-secret", "offset": 500}]
    )

    assert arguments == [{"val": "[redacted]", "offset": 500}]


def test_sanitize_failure_text_masks_a_secret_the_message_quoted() -> None:
    """Review finding SEC2-F20: an exception's own words can carry a secret.

    The assertion subject around it survives, because AAP 0.6 and 0.1.2 freeze
    the message strings and deviation 16 covers only their formatting.
    """
    sanitized = events.sanitize_failure_text(
        f"Login failed for {FAILURE_TEXT_SECRET} after 3 attempts"
    )

    assert "not-a-real-secret" not in sanitized
    assert sanitized == "Login failed for password=[redacted] after 3 attempts"


def test_sanitize_failure_text_relativises_this_repositorys_paths() -> None:
    """A traceback frame inside the port becomes repository-relative.

    An absolute frame path discloses the checkout location, the operating
    account and the workspace layout (CWE-200) and says nothing a reader of a
    test failure needs.  The frame stays identifiable, which is the whole
    point: ``File "features/steps/login_steps.py", line 61`` is what the
    reference's own failure text looks like.
    """
    repository_root = Path(events.__file__).resolve().parents[2]
    source = repository_root / "features" / "steps" / "login_steps.py"
    frame = f'  File "{source}", line 61'

    sanitized = events.sanitize_failure_text(frame)

    assert str(repository_root) not in sanitized
    assert sanitized == '  File "features/steps/login_steps.py", line 61'


def test_sanitize_failure_text_shortens_a_path_outside_the_repository() -> None:
    """An engine or interpreter frame keeps its last two components.

    Those frames live in a virtual environment or a system prefix, whose
    leading components are pure topology.  Two components keep the frame
    recognisable -- the file and the package it sits in -- behind a marker
    that says the path was cut rather than relative.
    """
    sanitized = events.sanitize_failure_text(
        f'  File "{FOREIGN_ABSOLUTE_PATH}", line 12'
    )

    assert sanitized == '  File ".../unittest/case.py", line 12'


def test_sanitize_failure_text_leaves_a_selenium_selector_alone() -> None:
    """An XPath is not a path, and a Selenium message is the diagnostic.

    ``Message: no such element: ... {"method":"xpath","selector":"//input[...]"}``
    is the commonest failure text this suite produces, and the selector is the
    only part of it that says *what* was not found.  The path rules are
    anchored so that a ``//`` selector, a URL and a relative path are all out
    of their reach.
    """
    message = (
        "Message: no such element: Unable to locate element: "
        "{\"method\":\"xpath\",\"selector\":\"//input[@id='o_field_input_125']\"}\n"
        "For documentation visit https://www.selenium.dev/exceptions/no_such_element/\n"
        "  File \"features/steps/crm_steps.py\", line 88, in user_can_change\n"
    )

    assert events.sanitize_failure_text(message) == message


def test_sanitize_failure_text_bounds_the_length_and_says_how_much_it_dropped(
) -> None:
    """A traceback cannot be allowed to be arbitrarily long, or silently cut.

    An exception whose ``str()`` is a page-source dump would otherwise be
    stored once per step, in every artifact.  The bound is
    ``MAX_FAILURE_TEXT_CHARS``; what makes a truncated report honest is the
    suffix, which carries the exact number of characters removed.
    """
    # Repeated prose rather than a repeated character: a long run of
    # base64-like characters is itself one of the shapes the sanitizer masks,
    # so it would measure redaction instead of truncation.
    line = "diagnostic line of a traceback frame\n"
    repetitions = (events.MAX_FAILURE_TEXT_CHARS // len(line)) + 10
    message = line * repetitions
    dropped = len(message) - events.MAX_FAILURE_TEXT_CHARS

    sanitized = events.sanitize_failure_text(message)

    assert dropped > 0
    assert sanitized == (
        f"{message[: events.MAX_FAILURE_TEXT_CHARS]}"
        f"...[+{dropped} char(s) truncated]"
    )
    assert len(sanitized) < len(message)


def test_sanitize_failure_text_still_normalises_line_endings() -> None:
    """LF normalisation is unchanged, and stays the same function's job.

    AAP deviation 16 makes the line endings the port's to choose and the
    message text parity; :func:`app.reporting.events._normalize_newlines`
    remains the owner of the first half, and this asserts the sanitizer did
    not take it away.
    """
    sanitized = events.sanitize_failure_text("first\r\nsecond\rthird\nfourth")

    assert sanitized == "first\nsecond\nthird\nfourth"
    assert "\r" not in sanitized


def test_sanitize_failure_text_is_idempotent_and_never_raises() -> None:
    """Both properties the callers depend on, in one place.

    The writer re-applies the rule to text the collector already sanitized, so
    it has to be a fixed point.  And failure *reporting* must not be able to
    fail a run: a value whose ``__str__`` raises yields the placeholder rather
    than an exception, which is the fail-closed answer -- nothing unsanitized
    is stored.
    """
    class Unprintable:
        """A value whose text cannot be obtained."""

        def __str__(self) -> str:
            """Fail the way a broken model attribute does.

            :raises RuntimeError: Always.
            """
            raise RuntimeError("__str__ is unavailable")

    for message in (
        f"Login failed for {FAILURE_TEXT_SECRET}",
        f'  File "{FOREIGN_ABSOLUTE_PATH}", line 12',
        "x" * (events.MAX_FAILURE_TEXT_CHARS + 1),
        "The title is not same as the expected! ",
        "",
    ):
        once = events.sanitize_failure_text(message)
        assert events.sanitize_failure_text(once) == once

    assert events.sanitize_failure_text(None) == ""
    assert events.sanitize_failure_text(Unprintable()) == "[redacted]"


def test_a_step_failures_traceback_is_stored_sanitized(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    crm_feature: StubFeature,
) -> None:
    """End to end: a real exception, a real traceback, one stored message.

    The exception is raised here so that the traceback is a genuine one with
    this file's absolute path in every frame -- which is exactly the shape a
    worker produces and the shape SEC2-F20 is about.  What the document keeps
    is the assertion's own message, a relative frame path, and no secret.
    """
    try:
        raise AssertionError(
            f"The title is not same as the expected! {FAILURE_TEXT_SECRET}"
        )
    except AssertionError as error:
        raised = error

    repository_root = str(Path(events.__file__).resolve().parents[2])
    step = StubStep(
        "And", "User click on the crm dashboard", 18,
        status=Status.failed, duration=4.211,
        func=make_step_function(
            "features/steps/crm_steps.py", "user_click_on_the_crm_dashboard"
        ),
    )
    step.exception = raised
    step.exc_traceback = raised.__traceback__

    record, document_text = collect_one_step(make_collector, crm_feature, step)
    message = record["result"]["error_message"]

    assert message.startswith(
        "The title is not same as the expected! password=[redacted]"
    )
    assert "not-a-real-secret" not in document_text
    assert repository_root not in message
    assert "tests/test_events.py" in message
    assert "\r" not in message
    assert len(message) <= events.MAX_FAILURE_TEXT_CHARS + 64


def test_a_hook_failure_is_recorded_sanitized_too(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    crm_feature: StubFeature,
) -> None:
    """The teardown hook's own failure text takes the same route.

    ``features/environment.py`` owns the scenario lifecycle and reports what
    only it knows through
    :func:`app.reporting.events.record_hook_result`, which means a caller can
    hand the document a message it built from an exception itself.  That is
    the same class of text as a step's failure -- a screenshot path, a driver
    error, whatever the exception quoted -- so it is held to the same rule
    rather than to LF normalisation alone.
    """
    collector = make_collector(source_lines=[])
    scenario = StubScenario(
        "User can change the situation in progress", CRM_FAILING_SCENARIO_LINE
    )
    collector.uri(crm_feature.filename)
    collector.feature(crm_feature)
    collector.scenario(scenario)

    collector.record_hook_result(
        status="hook_error",
        error_message=(
            f"Teardown failed: {FAILURE_TEXT_SECRET}\r\n"
            f'  File "{FOREIGN_ABSOLUTE_PATH}", line 12\r\n'
        ),
    )
    collector.eof()
    document = read_document(collector)
    entry = feature_by_path(document, CRM_PATH)["elements"][-1]["after"][0]

    assert entry["result"]["status"] == "hook_error"
    assert entry["result"]["error_message"] == (
        "Teardown failed: password=[redacted]\n"
        '  File ".../unittest/case.py", line 12\n'
    )


def test_bounding_an_already_bounded_text_changes_nothing() -> None:
    """Truncation is a fixed point, and its count is the total dropped.

    Two layers sanitize this field -- the collector when it stores the text and
    the Cucumber writer when it publishes a worker's -- so a bound that was not
    idempotent would cut an already-cut message a second time, dropping another
    notice-length of diagnostic and leaving a count that described only the
    second cut.  Asserted on a message that genuinely exceeds
    :data:`app.reporting.events.MAX_FAILURE_TEXT_CHARS` rather than one the
    redaction rules shorten, because only the former reaches the bound.
    """
    message = "Odoo page dump with words and punctuation. " * 200
    assert len(message) > events.MAX_FAILURE_TEXT_CHARS

    once = events.sanitize_failure_text(message)
    twice = events.sanitize_failure_text(once)

    assert once == twice
    assert events.sanitize_failure_text(twice) == once
    assert once.startswith(message[:64])
    dropped = len(message) - events.MAX_FAILURE_TEXT_CHARS
    assert once == (
        message[: events.MAX_FAILURE_TEXT_CHARS]
        + events.TRUNCATION_SUFFIX_TEMPLATE.format(dropped=dropped)
    )


def test_a_fabricated_truncation_notice_buys_no_exemption_from_the_bound() -> None:
    """A worker-supplied notice is read, not trusted.

    The count is taken from a trailing notice so that re-bounding can add to
    it, which means a hostile or hand-built message could arrive already
    wearing one.  It still gets bounded: the body is measured on its own, the
    declared count is carried into the new notice, and the result is inside the
    cap.
    """
    body = "x " * 6000
    text = f"{body}{events.TRUNCATION_SUFFIX_TEMPLATE.format(dropped=5)}"

    bounded = events.sanitize_failure_text(text)

    assert len(bounded) <= events.MAX_FAILURE_TEXT_CHARS + 64
    assert bounded.startswith(body[:64])
    notice = bounded[bounded.rindex("...[+") :]
    assert int(notice.split("[+")[1].split(" ")[0]) == (
        5 + len(body) - events.MAX_FAILURE_TEXT_CHARS
    )
    assert events.sanitize_failure_text(bounded) == bounded


def test_a_keyword_in_front_of_the_value_masks_it_too() -> None:
    """Adjacency is checked on both sides of the span, not only after it.

    Every credential phrase this suite owns puts the field name *after* the
    value -- ``User enters "<password>" password`` [Login.feature:16] -- so the
    following-keyword direction is the one a real run exercises.  The other
    direction is still a rule of the boundary rather than an accident of the
    corpus: a phrase added later, or an engine diagnostic quoting one, can put
    the field name in front, and a classifier that only looked forward would
    publish that value.  Asserted here so the direction cannot be dropped as
    dead code.
    """
    name = 'Sign-in uses password "not-a-real-secret" once'
    arguments = [{"val": '"not-a-real-secret"', "offset": name.index('"')}]

    redacted, built = events.redact_step_text(name, arguments)

    assert redacted == 'Sign-in uses password "[redacted]" once'
    assert [entry["val"] for entry in built] == ['"[redacted]"']
    assert redacted[built[0]["offset"] :].startswith('"[redacted]"')


def test_malformed_argument_entries_keep_their_position_and_their_arity() -> None:
    """A document the collector did not build is still redacted, not rejected.

    ``match.arguments`` arrives from a worker file or a hand-built fixture as
    well as from the collector, and its length is the step's parameter arity,
    which AAP 0.6 makes part of the contract: ``createMatchMap`` never drops an
    entry.  So an entry this boundary cannot index -- a non-mapping, a ``val``
    that is not a string, an ``offset`` that is not a usable index, and the
    empty mapping the JVM writes for a parameter with no value -- is carried
    through in place, and the one entry that *does* index the name is masked on
    its own merits.  The count is what a consumer reads; losing an entry would
    silently change the step's declared arity.
    """
    name = 'User enters "not-a-real-secret" password'
    entries: list[Any] = [
        {},
        "not a mapping",
        {"val": 17, "offset": 0},
        {"val": '"not-a-real-secret"', "offset": -1},
        {"val": '"not-a-real-secret"', "offset": True},
        {"val": '"not-a-real-secret"', "offset": 12},
    ]

    redacted, built = events.redact_step_text(name, entries)

    assert redacted == 'User enters "[redacted]" password'
    assert len(built) == len(entries)
    assert built[0] == {}
    assert built[1] == "not a mapping"
    assert built[2] == {"val": 17, "offset": 0}
    assert built[-1] == {"val": '"[redacted]"', "offset": 12}
    assert redacted[12:] == '"[redacted]" password'
    # The two entries whose offset could not be indexed keep their recorded
    # value masked rather than published - an unusable offset is a reason to
    # distrust the offset, never a reason to disclose the value - and the mask
    # keeps the quotes the contract says ``val`` carries.
    for entry in built[3:5]:
        assert entry["val"] == '"[redacted]"'


def test_overlapping_argument_spans_do_not_corrupt_the_name() -> None:
    """Two entries claiming the same characters are spliced once, not twice.

    The collector's own spans never overlap, but a worker file is untrusted
    input and a hand-built fixture is hand-built.  Splicing an overlapping span
    a second time would duplicate or drop text, so the second claim is skipped:
    the name keeps exactly one mask for those characters, and the entry that
    could not be spliced is masked rather than published.
    """
    name = 'User enters "not-a-real-secret" password'
    start = name.index('"')
    entries = [
        {"val": '"not-a-real-secret"', "offset": start},
        {"val": "not-a-real-secret", "offset": start + 1},
    ]

    redacted, built = events.redact_step_text(name, entries)

    assert redacted == 'User enters "[redacted]" password'
    assert redacted.count("[redacted]") == 1
    assert len(built) == 2
    assert "not-a-real-secret" not in redacted
    assert not any("not-a-real-secret" in str(entry) for entry in built)


def test_a_classifier_that_fails_masks_the_whole_step_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail closed: a boundary that cannot classify a value masks it anyway.

    The classification is pure regex work over strings and has no failure mode
    of its own, which is exactly why the behaviour on failure has to be pinned
    rather than assumed.  With the sanitizer replaced by one that raises, the
    span is treated as sensitive rather than as clean, so the value is still
    masked, the name keeps its shape and the argument keeps its position and
    its offset.  Masking on an unusable answer is the only safe default: the
    alternative publishes a value nothing was able to look at.
    """
    def explode(_text: str) -> str:
        raise RuntimeError("classification is unavailable")

    monkeypatch.setattr(events, "redact_sensitive", explode)

    name = 'User enters "not-a-real-secret" password'
    redacted, built = events.redact_step_text(
        name, [{"val": '"not-a-real-secret"', "offset": name.index('"')}]
    )

    assert "not-a-real-secret" not in redacted
    assert redacted == 'User enters "[redacted]" password'
    assert [entry["val"] for entry in built] == ['"[redacted]"']
    assert redacted[built[0]["offset"] :].startswith('"[redacted]"')


def test_an_exception_with_no_message_and_no_traceback_is_still_sanitized() -> None:
    """The class-name fallback goes through the same boundary as the rest.

    ``str(KeyboardInterrupt())`` is ``""``, which would leave the failure text
    starting with a bare newline, so the class name is the only message there
    is; and behave stores no traceback for an exception it never saw raised.
    Both degenerate paths return through
    :func:`app.reporting.events.sanitize_failure_text`, so no return statement
    of ``_failure_text`` is a way around it.
    """
    assert events._failure_text(KeyboardInterrupt()) == "KeyboardInterrupt"
    assert events._failure_text(AssertionError(FAILURE_TEXT_SECRET)) == (
        "password=[redacted]"
    )


def test_behaves_own_error_string_is_sanitized_on_the_fallback_path() -> None:
    """The no-exception path is a boundary too, prefixes and all.

    With no exception object stored, behave's ``error_message`` is the only
    record of the failure, and its measured shapes are ``"ASSERT FAILED: ..."``
    and ``"ERROR: <Class>: ..."``.  The prefix stripping is unchanged -- it is
    what makes the text the assertion's own message, which AAP 0.6 freezes --
    and what survives it is masked, relativised and bounded like every other
    failure text.
    """
    assert events._failure_text(
        None, None, f"ASSERT FAILED: The title is not same! {FAILURE_TEXT_SECRET}"
    ) == "The title is not same! password=[redacted]"
    assert events._failure_text(
        None, None, f"ERROR: ValueError: {FAILURE_TEXT_SECRET}"
    ) == "password=[redacted]"
    # ``"ERROR: <Class>"`` with nothing after it: the class name is the message.
    assert events._failure_text(None, None, "ERROR: ValueError: ") == "ValueError"


def test_a_partially_indexed_argument_list_still_classifies_every_span(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    crm_feature: StubFeature,
) -> None:
    """A credential no argument entry represents is still a credential.

    behave reports one argument per *captured* parameter, so a phrase can
    carry two quoted values and an argument list that indexes only one of
    them -- a definition that captured the first, a worker file written by
    another build, a hand-built fixture.  A boundary that considered only the
    indexed spans would publish the other value in the clear, which is review
    finding SEC2-F03 with one argument instead of none.  Both directions are
    asserted at once: the unrepresented credential span is masked, the
    represented business value survives **byte for byte**, and the surviving
    offset still indexes into the name the publisher is handed.
    """
    step = StubStep(
        "And", MIXED_VALUE_STEP_NAME, 17,
        status=Status.passed, duration=1.0,
        func=make_step_function(
            "features/steps/login_steps.py", "user_enters_tag_and_password"
        ),
        arguments=quoted_arguments(MIXED_VALUE_STEP_NAME)[:1],
    )

    record, document_text = collect_one_step(make_collector, crm_feature, step)
    arguments = record["match"]["arguments"]

    assert record["name"] == (
        'User enters "public" tag and "[redacted]" password'
    )
    assert arguments == [{"val": MIXED_VALUE_KEPT, "offset": 12}]
    assert_offsets_index_into_the_name(record)
    assert "not-a-real-secret" not in document_text


def test_a_keyword_classifies_the_value_it_governs_and_no_other() -> None:
    """The adjacency scope, span by span, over the phrasings that pin it.

    The classifier's reach on each side is the text between that span and its
    neighbour, capped by the window constant -- not a flat character count.
    The distinction is measurable and this is where it is measured: in
    ``User enters "public" tag and "<password>" password`` the keyword sits 18
    characters from ``"public"``, well inside the cap, and governs the value
    after it.  A window-only rule masks both, which empties business data out
    of the artifact the Jenkins publisher reads every time a phrase so much as
    mentions a credential field -- over-redaction is a failure mode of its
    own, and AAP 0.6 freezes the parameterized step at the end of this table.

    Each case names the phrase and, per quoted run left to right, whether the
    boundary must mask it.  Building the argument entries from the phrase
    keeps the assertion about the classifier rather than about hand-counted
    offsets.
    """
    cases: tuple[tuple[str, tuple[bool, ...]], ...] = (
        (LOGIN_USERNAME_STEP_NAME, (True,)),
        (LOGIN_PASSWORD_STEP_NAME, (True,)),
        ('Sign-in uses password "not-a-real-secret" once', (True,)),
        (MIXED_VALUE_STEP_NAME, (False, True)),
        (CONTACT_TWO_ARGUMENT_STEP_NAME, (False, True)),
        ("User can find his name \"Lucas\" from search bar", (False,)),
        (GOLDEN_STEP_NAME, (False, False, False)),
    )

    for name, expectations in cases:
        entries = [
            {"val": value, "offset": offset}
            for value, offset in (
                events.widen_quoted_span(name, argument.start, argument.end)
                for argument in quoted_arguments(name)
            )
        ]
        redacted, built = events.redact_step_text(name, entries)

        assert len(built) == len(expectations), name
        for entry, original, masked in zip(built, entries, expectations):
            assert (entry["val"] == REDACTED_QUOTED_VALUE) is masked, (
                f"{original['val']!r} in {name!r} was "
                f"{'published' if masked else 'masked'}"
            )
            if not masked:
                assert entry["val"] == original["val"], name
            assert redacted[entry["offset"] :].startswith(entry["val"]), name


def test_a_business_value_beside_a_credential_field_survives() -> None:
    """The over-redaction guard for the mixed phrase, stated on its own.

    ``"public"`` is a tag name and nothing else: no keyword governs it, and
    its own text is not credential-shaped.  It is asserted separately from the
    table above because it is the case a regression would most plausibly
    reintroduce -- widening the classifier to "anything near the word
    password" closes the leak this phrase also demonstrates while quietly
    emptying every phrase that names a field and a value in one breath.
    """
    redacted, built = events.redact_step_text(
        MIXED_VALUE_STEP_NAME,
        [{"val": MIXED_VALUE_KEPT, "offset": MIXED_VALUE_STEP_NAME.index('"')}],
    )

    assert built == [{"val": MIXED_VALUE_KEPT, "offset": 12}]
    assert redacted == 'User enters "public" tag and "[redacted]" password'
    assert "not-a-real-secret" not in redacted
    assert redacted[12:] == '"public" tag and "[redacted]" password'


def test_new_step_redacts_a_match_whose_arguments_are_not_a_list() -> None:
    """``match.arguments`` is a sequence, and a tuple is one.

    :func:`app.reporting.events.new_step` is the constructor a fixture, a
    loader and a hand-assembled document all reach the schema through, and any
    of them can hand it a tuple.  A type test that recognised only ``list``
    took the no-arguments path, so the name was text-redacted while the
    entries kept their raw ``val`` -- the credential published in the very
    field the joint redaction exists to mask.  The stored list is also a
    ``list`` afterwards, because that is what the document validator accepts
    when the shard is read back.
    """
    step = events.new_step(
        keyword="When",
        line=16,
        name=LOGIN_PASSWORD_STEP_NAME,
        matched=True,
        match={
            "location": "features.steps.login_steps.user_enters_password",
            "arguments": ({"val": '"not-a-real-secret"', "offset": 12},),
        },
    )

    assert step["name"] == 'User enters "[redacted]" password'
    assert isinstance(step["match"]["arguments"], list)
    assert step["match"]["arguments"] == [
        {"val": REDACTED_QUOTED_VALUE, "offset": 12}
    ]
    assert_offsets_index_into_the_name(step)
    assert "not-a-real-secret" not in json.dumps(step)


def test_new_step_sanitizes_the_failure_text_it_is_handed() -> None:
    """Review finding SEC2-F20 says *before storage*, so the builder is a gate.

    :func:`app.reporting.events.new_hook_entry` already sanitizes its own
    ``error_message``; a step's result reached the document verbatim, so a
    caller that built one from an exception -- a fixture, a merge of a shard
    another build wrote -- could put a secret, an absolute path and an
    unbounded traceback into a document :func:`dump_result_set` then
    persists.  The other keys of the result are the caller's exactly as
    before, which the status and duration assertions pin.
    """
    step = events.new_step(
        keyword="Then",
        line=18,
        name="User click on the crm dashboard",
        matched=True,
        result={
            "status": "failed",
            "duration": 4_211_000_000,
            "error_message": (
                f"Login failed: {FAILURE_TEXT_SECRET}\r\n"
                f'  File "{FOREIGN_ABSOLUTE_PATH}", line 12\r\n'
            ),
        },
    )
    result = step["result"]

    assert result["error_message"] == (
        "Login failed: password=[redacted]\n"
        '  File ".../unittest/case.py", line 12\n'
    )
    assert result["status"] == "failed"
    assert result["duration"] == 4_211_000_000
    # A result that carries no failure text is still given none, and a result
    # the builder already sanitized is a fixed point.
    assert "error_message" not in events.new_step(
        keyword="Then", line=18, name="n", result={"status": "passed"}
    )["result"]
    assert events.new_step(
        keyword="Then", line=18, name="n", result=result
    )["result"] == result


def test_sanitize_failure_text_relativises_a_frame_path_with_spaces() -> None:
    """A workspace path with a space is still a workspace path.

    The rule that shortens an absolute path in ordinary message text has to
    stop at the characters a path cannot contain, or a Selenium message would
    have its prose swallowed; a space is one of them.  A traceback frame is
    delimited instead: ``  File "<path>", line N, in <name>``, so the quoted
    run is the whole path however many spaces it holds.  Before that
    distinction existed, ``/home/jane doe/...`` kept the account name and the
    entire layout after the space while the part in front of it was reduced to
    ``.../home/jane`` -- worse than useless, since the frame was mangled *and*
    the topology was published (review finding SEC2-F20, CWE-200).  The
    Windows drive, agent-directory and UNC forms are the same rule on the
    other separator.
    """
    for path, expected in SPACED_FRAME_PATHS:
        sanitized = events.sanitize_failure_text(
            f'  File "{path}", line 61, in user_enters_password'
        )

        assert sanitized == (
            f'  File "{expected}", line 61, in user_enters_password'
        ), path
        assert "jane" not in sanitized.lower()
        assert "job 42" not in sanitized
        assert "buildhost" not in sanitized
        assert events.sanitize_failure_text(sanitized) == sanitized, path


def test_sanitize_failure_text_leaves_a_non_path_frame_position_alone() -> None:
    """Not everything between ``File "`` and its closing quote is a path.

    The interpreter writes ``File "<stdin>"`` and ``File "<string>"`` for a
    frame with no source file, a relative frame is what rule 1 has just
    produced, and a URL is not a filesystem path at all.  Each is left exactly
    as it arrived: the frame rule shortens an absolute POSIX, drive-qualified
    or UNC path and refuses everything else, which is also what keeps it clear
    of the ``//``-leading XPath selector a Selenium message carries.
    """
    untouched = (
        '  File "<stdin>", line 1, in <module>',
        '  File "<string>", line 1',
        '  File "features/steps/crm_steps.py", line 88, in user_can_change',
        '  File "https://www.selenium.dev/a/b/c.py", line 1',
        "Message: no such element: {\"method\":\"xpath\","
        "\"selector\":\"//input[@id='o_field_input_125']\"}",
    )

    for text in untouched:
        assert events.sanitize_failure_text(text) == text, text


def test_a_hand_built_hook_entrys_failure_text_is_sanitized() -> None:
    """The builder is a boundary as well, for the same reason.

    :func:`app.reporting.events.new_hook_entry` is what a fixture and a
    lifecycle caller use, and a document assembled through it is a document
    the writers publish.  Applying the rule in the builder means no caller has
    to remember it.
    """
    entry = events.new_hook_entry(
        status="cleanup_error",
        duration=412_000_000,
        error_message=f"Quitting the driver failed: {FAILURE_TEXT_SECRET}\r\n",
    )

    assert entry["result"]["error_message"] == (
        "Quitting the driver failed: password=[redacted]\n"
    )
    assert entry["result"]["status"] == "cleanup_error"
    assert entry["result"]["duration"] == 412_000_000


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


def test_an_unnamed_examples_block_doubles_the_separator(
    make_collector: Callable[..., events.ResultCollectorFormatter],
    crm_feature: StubFeature,
) -> None:
    """An unnamed ``Examples:`` block contributes an **empty segment**.

    The id of an outline row is
    ``<feature>;<outline>;<examples>;<position>``, and an unnamed block
    slugs to ``""`` - the JVM's own ``convertToId("")`` - so the id carries a
    doubled separator, ``...;;2``.  AAP 0.6 required that cell to be fixed
    against the clean Cucumber-JVM baseline before the writer was trusted,
    and it is: that baseline emits ``...;verify-that-the-user-can-create-a-
    new-contact;;2`` for ``Contact.feature``'s unnamed blocks, and
    ``tests/fixtures/sample_results.json`` carries the same ``;;2``/``;;3``
    shape for ``Sales.feature``'s.

    So the whole id is pinned here, separators included.  The JSON report is
    machine-read and this is the key every artifact and detail page uses:
    collapsing the empty segment, or substituting a placeholder for it,
    would publish a test case neither authority names.
    """
    collector = make_collector(source_lines=[])
    row = _outline_row(
        CRM_OUTLINE_FIRST_ROW_LINE, row_id="1.1", row_index=1, examples_name=""
    )

    run_feature(collector, crm_feature, [row])
    element = feature_by_path(read_document(collector), CRM_PATH)["elements"][-1]

    outline_name = "User can change information in dashboard"
    assert element["id"] == (
        f"{CRM_FEATURE_ID};{events.convert_to_id(outline_name)};;2"
    )
    assert element["id"].split(";") == [
        CRM_FEATURE_ID,
        events.convert_to_id(outline_name),
        "",
        "2",
    ]
    assert element["name"] == outline_name


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


def test_collector_opens_the_outfile_path_through_the_path_authority(
    tmp_path: Path, crm_feature: StubFeature
) -> None:
    """The production route: the ``-o`` file is opened here, eagerly, verified.

    This is the mode ``app/services/test_run_service.py`` produces -
    ``StreamOpener(outfile)``, a filename and no stream - and it used to be
    handed to behave's own opener, which calls ``codecs.open()`` on that
    pathname: the name is resolved afresh, a symbolic link standing in its
    place is followed, and the target is truncated before anything can refuse
    it.  The collector now opens it through
    ``app.utils.paths.open_artifact_write`` instead, so what is asserted here
    is the whole of that route's observable contract:

    * the file exists at the requested path after construction alone, with
      both of its directory components created, because opening in the
      constructor is what makes an unwritable ``-o`` path a startup failure
      rather than a surprise at the end of a run, and what lets the merge step
      tell an empty shard (a worker that died) from an absent one (a worker
      that never started);
    * the handle is installed on behave's stream opener with
      ``should_close_stream`` set, which is not decoration:
      ``Formatter.close_stream`` asserts ``self.stream is
      self.stream_opener.stream`` and delegates the close to the opener, so
      behave's own house-keeping only works while that identity holds;
    * the document and every directory the route created are owner-only, per
      the mode policy - the document carries substituted step arguments and
      failure text;
    * an unwritable path still raises out of the constructor.

    This test used to *suppress* ``codecs.open()``'s ``DeprecationWarning``,
    which was unavoidable on the old route.  It now turns that one warning into
    an error for the duration of the block instead - narrowly, by message, so
    no unrelated deprecation in a pinned dependency is caught up in it - which
    is what makes "behave's pathname opener is no longer reached" an assertion
    rather than a claim in a docstring.
    """
    destination = worker_output_path(tmp_path)
    opener = StreamOpener(filename=str(destination))
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "error", message="codecs.open", category=DeprecationWarning
        )
        collector = events.ResultCollectorFormatter(opener, StubConfig())
    try:
        assert destination.is_file(), "the stream must be opened eagerly"
        assert collector.stream is not None
        assert opener.stream is collector.stream, (
            "behave's close_stream asserts this identity"
        )
        assert opener.should_close_stream is True
        assert_owner_only(destination, directory=False)
        for depth in range(1, len(WORKER_DIR_COMPONENTS) + 1):
            assert_owner_only(
                tmp_path.joinpath(*WORKER_DIR_COMPONENTS[:depth]), directory=True
            )
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
    assert_owner_only(destination, directory=False)


def test_collector_refuses_a_symlinked_outfile_and_leaves_the_victim_intact(
    tmp_path: Path,
) -> None:
    """A link planted at the ``-o`` path must cost nothing outside the tree.

    The finding this closes is a truncation, not an exception: behave's
    ``codecs.open()`` on the ``-o`` pathname follows a symbolic link and empties
    whatever it points at, and ``target/.workers/`` survives a ``--no-clean``
    run, so the name is not under the worker's sole control.  Both halves are
    asserted - the refusal is typed
    ``app.utils.paths.ArtifactPathError``, which is an ``OSError`` and
    therefore the writer-failure exit class of AAP 0.4.1, and the external file
    is still byte-for-byte what it was.  An implementation that refused after
    opening with ``O_TRUNC`` would pass the first assertion and fail the
    second.

    The collector must also not have registered itself: a constructor that
    raised leaves no live formatter for ``attach_to_current_scenario`` to find.
    """
    victim = plant_victim(tmp_path / "victim.txt")
    destination = worker_output_path(tmp_path)
    destination.parent.mkdir(parents=True)
    destination.symlink_to(victim)
    live_before = list(events._ACTIVE_COLLECTORS)

    with pytest.raises(paths.ArtifactPathError) as raised:
        events.ResultCollectorFormatter(
            StreamOpener(filename=str(destination)), StubConfig()
        )

    assert isinstance(raised.value, OSError)
    assert "symbolic link" in str(raised.value)
    assert_victim_intact(victim)
    assert destination.is_symlink(), "the link itself must be left alone"
    assert events._ACTIVE_COLLECTORS == live_before


def test_collector_refuses_a_hard_linked_outfile_and_leaves_the_victim_intact(
    tmp_path: Path,
) -> None:
    """A hard-linked ``-o`` destination is the same refusal with no symlink.

    The case a symlink check cannot see: the entry at the ``-o`` path *is* the
    external file, so every byte written to the shard would be written to that
    file and nothing in the path is a link for a link check to find.  It is
    reachable exactly as the symlink case is - a prepared ``target/.workers/``
    that a ``--no-clean`` run did not empty - and the path authority refuses it
    on the link count of the object it opened, before anything is truncated.
    """
    victim = plant_victim(tmp_path / "victim.txt")
    destination = worker_output_path(tmp_path)
    destination.parent.mkdir(parents=True)
    os.link(victim, destination)

    with pytest.raises(paths.ArtifactPathError) as raised:
        events.ResultCollectorFormatter(
            StreamOpener(filename=str(destination)), StubConfig()
        )

    assert "hard link" in str(raised.value)
    assert_victim_intact(victim)
    assert_victim_intact(destination)


def test_a_pre_opened_stream_is_used_as_is_and_touches_no_path(
    tmp_path: Path, crm_feature: StubFeature
) -> None:
    """behave's other construction must not be routed through the filesystem.

    ``StreamOpener(stream=...)`` is what behave builds for a run with no
    ``-o`` - it passes ``sys.stdout`` - and what every other test in this
    module builds.  There is no pathname involved, so there is nothing for the
    path authority to verify, and the collector hands the stream back
    unchanged: the document lands in the caller's buffer and ``tmp_path`` stays
    empty, which is what proves no pathname was invented for it.

    The stream stays open afterwards, which is behave's documented rule rather
    than an oversight: ``StreamOpener.close()`` closes only a stream it opened
    itself, so a pre-opened one belongs to whoever opened it.
    """
    buffer = io.StringIO()
    opener = StreamOpener(stream=buffer)
    collector = events.ResultCollectorFormatter(opener, StubConfig())
    try:
        assert collector.stream is buffer
        assert opener.should_close_stream is False
        collector.clock = SteppedClock()
        collector.read_source_lines = lambda _filename: []  # type: ignore[method-assign]
        run_feature(collector, crm_feature, [StubScenario("scenario", 9)])
    finally:
        collector.close()

    assert list(tmp_path.iterdir()) == [], "a pre-opened stream needs no file"
    assert not buffer.closed, "a pre-opened stream is the caller's to close"
    document = json.loads(buffer.getvalue())
    assert feature_by_path(document, CRM_PATH)["elements"]
    assert collector not in events._ACTIVE_COLLECTORS


def test_close_closes_the_verified_stream_exactly_once(
    tmp_path: Path, crm_feature: StubFeature, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One close, one document, and a second ``close`` that is still harmless.

    The install-on-the-opener step is what keeps this true: behave closes the
    stream through ``StreamOpener.close()``, and a handle the opener did not
    know about would either be closed twice - once by the opener's
    house-keeping and once by the collector's own release - or never closed at
    all, with the document unflushed.  ``close`` is also called defensively by
    ``app/services/test_run_service.py``, and a second write would append a
    second JSON document and make the shard unparseable.

    The count is measured by wrapping the handle the path authority returns,
    which is the only way to observe it: closing an already-closed file object
    is silently harmless, so an assertion on the file alone would not see a
    double close.
    """
    closes: list[int] = []
    real_open = paths.open_artifact_write

    class CountingStream:
        """The verified stream, counting the closes it is asked for.

        A delegating wrapper rather than a ``TextIOWrapper`` subclass: wrapping
        the verified descriptor in a *second* text layer would leave the first
        one unreferenced, and its collection would close the buffer underneath
        this object.
        """

        def __init__(self, handle: Any) -> None:
            """Wrap ``handle``.

            :param handle: The stream ``open_artifact_write`` returned.
            """
            self._handle = handle

        def close(self) -> None:
            """Record the close and perform it.

            Recorded before the delegation, so a second close is counted even
            though the underlying object would treat it as a no-op.
            """
            closes.append(1)
            self._handle.close()

        def __getattr__(self, name: str) -> Any:
            """Forward everything else - ``write``, ``flush``, ``closed``.

            :param name: The attribute the collector or behave asked for.
            :returns: The wrapped stream's attribute.
            """
            return getattr(self._handle, name)

    def counting_open(path: Any, **kwargs: Any) -> Any:
        """Open through the real path authority, wrapped in the counter.

        :param path: Destination, passed straight through.
        :param kwargs: The keyword contract of ``open_artifact_write``.
        :returns: The counting stream over the verified descriptor.
        """
        return CountingStream(real_open(path, **kwargs))

    monkeypatch.setattr(events, "open_artifact_write", counting_open)
    destination = worker_output_path(tmp_path)
    collector = events.ResultCollectorFormatter(
        StreamOpener(filename=str(destination)), StubConfig()
    )
    collector.clock = SteppedClock()
    collector.read_source_lines = lambda _filename: []  # type: ignore[method-assign]
    run_feature(collector, crm_feature, [StubScenario("scenario", 9)])

    collector.close()

    assert closes == [1], f"the verified stream was closed {len(closes)} times"
    assert collector.stream is None
    assert collector.stream_opener.stream is None

    collector.close()

    assert closes == [1], "a second close must not reach the stream again"
    text = destination.read_text(encoding="utf-8")
    assert text.count(f'"schema_version": {events.SCHEMA_VERSION}') == 1
    assert feature_by_path(json.loads(text), CRM_PATH)["elements"]


def test_an_opener_with_no_filename_and_no_stream_is_left_to_behave(
    tmp_path: Path,
) -> None:
    """A nonsense opener must not be routed to the path authority.

    ``StreamOpener`` allows a construction that carries neither a filename nor
    a stream, and behave's own opener fails it with a ``TypeError`` from
    ``os.path.dirname(None)``.  The collector keeps exactly that outcome
    instead of passing the missing name to
    ``app.utils.paths.open_artifact_write``, which is what would turn a
    configuration mistake into a path built out of ``None``.

    ``tmp_path`` is asserted empty afterwards for the same reason it is in the
    pre-opened test: no filesystem entry may be invented for an opener that
    named none.
    """
    collector_count = len(events._ACTIVE_COLLECTORS)

    with pytest.raises(TypeError):
        events.ResultCollectorFormatter(StreamOpener(), StubConfig())

    assert list(tmp_path.iterdir()) == []
    assert len(events._ACTIVE_COLLECTORS) == collector_count


def test_an_opener_that_will_not_hold_the_stream_still_yields_a_document(
    tmp_path: Path, crm_feature: StubFeature, caplog: pytest.LogCaptureFixture
) -> None:
    """The verified stream is released even when behave's route cannot run.

    Installing the handle on the stream opener is what lets behave close it,
    and ``Formatter.close_stream`` asserts the identity before delegating.  An
    opener that will not accept the handle therefore leaves behave unable to
    close it - and an unclosed text stream means an unflushed document, which
    is the shard the merge would read as truncated.  The collector reports the
    condition and closes the stream itself, so the outcome is a complete
    document either way.

    :class:`StubUnsettableOpener` reproduces it deterministically; the real
    ``StreamOpener`` accepts the assignment, which is why the normal route is
    the one every other test here exercises.
    """
    destination = worker_output_path(tmp_path)
    collector = events.ResultCollectorFormatter(
        StubUnsettableOpener(str(destination)), StubConfig()
    )
    collector.clock = SteppedClock()
    collector.read_source_lines = lambda _filename: []  # type: ignore[method-assign]
    handle = collector.stream

    with caplog.at_level(logging.DEBUG, logger=events.__name__):
        run_feature(collector, crm_feature, [StubScenario("scenario", 9)])
        collector.close()

    messages = [record.getMessage() for record in caplog.records]
    assert any("would not accept the verified output stream" in m for m in messages)
    assert handle.closed, "the collector must close a stream behave cannot"
    document = json.loads(destination.read_text(encoding="utf-8"))
    assert feature_by_path(document, CRM_PATH)["elements"]
    assert_owner_only(destination, directory=False)
    assert collector not in events._ACTIVE_COLLECTORS


def test_a_stream_whose_close_fails_does_not_fail_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The last act of a passed run must not raise.

    ``close`` is called by behave at the very end of a run, so every one of its
    stages is guarded - and the stream release is the final stage, reached when
    behave's own house-keeping could not run.  A close that reports a failure
    there (a full disk flushing the last buffer, a stream object someone
    replaced) is logged and nothing more: an exception would fail a suite that
    had already passed, for a report that has already been written.
    """
    real_open = paths.open_artifact_write

    class FailingCloseStream:
        """A verified stream whose ``close`` performs and then complains."""

        def __init__(self, handle: Any) -> None:
            """Wrap ``handle``.

            :param handle: The stream ``open_artifact_write`` returned.
            """
            self._handle = handle

        def close(self) -> None:
            """Close the real stream, then report a failure.

            Closing first keeps the descriptor from leaking into the rest of
            the session; the raise is what the collector has to absorb.

            :raises OSError: Always, after the close has happened.
            """
            self._handle.close()
            raise OSError("the stream could not be closed")

        def __getattr__(self, name: str) -> Any:
            """Forward everything else to the wrapped stream.

            :param name: The attribute asked for.
            :returns: The wrapped stream's attribute.
            """
            return getattr(self._handle, name)

    monkeypatch.setattr(
        events,
        "open_artifact_write",
        lambda path, **kwargs: FailingCloseStream(real_open(path, **kwargs)),
    )
    destination = worker_output_path(tmp_path)
    collector = events.ResultCollectorFormatter(
        StubUnsettableOpener(str(destination)), StubConfig()
    )
    collector.clock = SteppedClock()

    with caplog.at_level(logging.DEBUG, logger=events.__name__):
        collector.close()

    assert any(
        "Closing the verified output stream failed" in record.getMessage()
        for record in caplog.records
    )
    assert_keys(
        json.loads(destination.read_text(encoding="utf-8")),
        RESULT_SET_KEYS,
        "document written before the failing close",
    )
    assert collector not in events._ACTIVE_COLLECTORS


def test_the_minimal_document_replaces_a_partial_write_on_the_verified_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """``_write_minimal_document``'s rewind has to work on the real stream.

    The fallback documents that it rewinds and truncates *"when the stream
    supports both"*, so that the parent reads a small well-formed incomplete
    document instead of half-serialised bytes.  The stream the path authority
    returns is asserted to support both here rather than assumed to: it is
    opened write-only through a directory descriptor, which is precisely the
    kind of construction that could have come back unseekable.

    A partial write is planted first, because that is what the fallback exists
    to replace - an assertion on the minimal document alone would pass even if
    the rewind had silently been skipped.
    """
    destination = worker_output_path(tmp_path)
    collector = events.ResultCollectorFormatter(
        StreamOpener(filename=str(destination)), StubConfig()
    )
    collector.clock = SteppedClock()

    def _unserialisable(_document: Any) -> str:
        """Stand in for a serialisation that cannot complete.

        :param _document: Ignored.
        :returns: Never returns.
        :raises RuntimeError: Always.
        """
        raise RuntimeError("the document could not be serialised")

    assert collector.stream.seekable(), "the minimal document needs seek(0)"
    collector.stream.write("PARTIALLY WRITTEN DOCUMENT")
    monkeypatch.setattr(events, "_serialize", _unserialisable)

    with caplog.at_level(logging.WARNING, logger=events.__name__):
        collector.close()

    text = destination.read_text(encoding="utf-8")
    assert "PARTIALLY WRITTEN" not in text, "the partial write was not replaced"
    document = json.loads(text)
    assert document["complete"] is False
    assert [entry["event"] for entry in document["collection_errors"]] == [
        "close.write"
    ]
    assert_owner_only(destination, directory=False)
    assert any(
        "minimal incomplete result document" in record.getMessage()
        for record in caplog.records
    )


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
