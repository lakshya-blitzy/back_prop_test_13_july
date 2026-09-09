"""The internal result schema and the behave event collector.

This module is the foundation of ``app/reporting``: it defines the intermediate
result document that all four artifact writers consume, and it is the only
place where behave's event stream is observed.  Whatever is not captured here
is unavailable downstream -- as the plan puts it, *"No mapping can recover a
field that was never captured."*

Why a custom formatter rather than behave's own JSON
----------------------------------------------------
behave ships a JSON formatter, and it was measured against the Cucumber-JVM
contract that the Jenkins publisher reads.  It is unusable as the artifact
source, because it

* omits the per-scenario ``start_timestamp`` entirely,
* records ``match.location`` as ``"features/steps/x.py:LINE"`` rather than as a
  callable path,
* shapes step arguments differently (``value``/``name``/``original`` instead of
  ``val``/``offset``),
* positions Background results differently (one result-less element per
  feature instead of one repetition before every scenario), and
* attaches embeddings to the *current step* without a name, instead of to an
  after-hook entry.

So the port does not post-process behave's JSON.  :class:`ResultCollectorFormatter`
registers on behave's event stream, records a superset of what the writers
need, and writes this module's own document.  ``behave.ini`` therefore declares
**no formatter and no outfile**: scenarios are sharded across a process pool
and a static configuration file cannot give each worker a distinct output path,
so ``app/services/test_run_service.py`` passes
``-f app.reporting.events:ResultCollectorFormatter -o <path>`` on the command
line once per worker, the path coming from :mod:`app.utils.paths` and unique by
process id and shard index.  :data:`FORMATTER_SCOPED_NAME` exists so that no
caller has to spell the scoped name out.

Source anchor: ``CukesRunner.java``, whose Cucumber plugin list named the four
artifacts this document feeds.

The document (schema version :data:`SCHEMA_VERSION`)
----------------------------------------------------
Plain JSON-serialisable ``dict``/``list`` structures throughout -- deliberately
not dataclasses -- so that ``tests/fixtures/sample_results.json`` can be
hand-built and round-tripped through :func:`json.load` / :func:`json.dump`
without shape loss.

**Run level** (the object :func:`new_result_set` returns)::

    {
      "schema_version": 1,             # int; bumped only on a breaking change
      "started_at": "...Z" | None,     # earliest scenario start_timestamp
      "generated_at": "...Z" | None,   # when the document was written
      "dry_run": False,                # mirrored from behave's config
      "tag_expression": "@Smoke" | None,
      "metadata": {
        "implementation": {"name": "behave",  "version": "1.3.3"},
        "runtime":        {"name": "CPython", "version": "3.14.6"},
        "os":             {"name": "Linux"},
        "cpu":            {"name": "x86_64"},
      },
      "features": [ <feature>, ... ],  # source order
    }

The four ``metadata`` keys and their sub-keys are fixed vocabulary:
``app/templates/artifact/metadata.html`` renders exactly those names.  A probe
that returns nothing yields ``""`` -- a metadata lookup never fails a run.

**Feature object**::

    {
      "uri": "file:features/Crm.feature",   # copied through by cucumber_json,
                                            # hashed by pretty_reports
      "path": "features/Crm.feature",       # the same path without the scheme,
                                            # used by rerun_report
      "id": "testinium-app-crm-module",     # see convert_to_id()
      "keyword": "Feature",
      "line": 2,
      "name": "Testinium app CRM Module",
      "description": "  Account is: PosManager",   # "" when empty; leading
                                                   # indentation verbatim
      "tags": [ {"name": "@Smoke", "type": "Tag",
                 "location": {"line": 1, "column": 1}} ],   # always present
      "elements": [ <element>, ... ],
    }

Both ``uri`` and ``path`` are carried so that no consumer performs string
surgery on the other.

**Element object** -- one per Background *occurrence* and one per scenario::

    {
      "type": "background" | "scenario",
      "keyword": "Background" | "Scenario" | "Scenario Outline",
      "line": 24,                 # the data row's line for an outline row
      "name": "...",
      "description": "",
      "selected": True,           # did the tag expression select it?
      "steps": [ <step>, ... ],
      # scenario only, and never present on a background:
      "id": "testinium-app-crm-module;user-can-change-information-in-dashboard;expected-name;2",
      "start_timestamp": "2022-09-07T13:38:05.703Z",
      "tags": [ {"name": "@Smoke"} ],   # short shape; key omitted when empty
      "after": [ <hook entry>, ... ],
    }

A Background element carries **no** ``id``, ``tags``, ``start_timestamp`` or
``after``.  That is measured, not stylistic: the reference report's eight
elements are four backgrounds and four scenarios interleaved, and every
background lacks those keys.  ``app/templates/partials/step_row.html`` reads
only ``step.*`` so that it renders unchanged inside a background, and
``app/templates/pretty/_element_tree.html`` tolerates a background with no
id/tags/start_timestamp; :func:`new_element` is what makes that true.

``selected`` exists because behave announces scenarios the tag expression
excluded (``show_skipped`` defaults to true) while the JVM never starts them
and therefore never emits them.  ``app/reporting/cucumber_json.py`` drops
non-selected scenarios; a Background inherits its scenario's value.

**Step object**::

    {
      "keyword": "Given ",        # trailing space, as the JVM emits it
      "line": 17,                 # the outline TEMPLATE's line, not the row's
      "name": 'User can change any user\\'s information like "Test2" , "30" and "2"',
      "matched": True,
      "match": {"location": "features.steps.crm_steps.user_can_change_information",
                "arguments": [{"val": "\\"Test2\\"", "offset": 44}]},
      "result": {"status": "passed", "duration": 30202000000,
                 "error_message": "..."},
    }

* ``keyword`` keeps its trailing space; ``step_row.html``'s ``step_keyword``
  macro is the single place it is trimmed for display.
* ``name`` is the *substituted* text for an outline row, and ``line`` is the
  outline template's step line even though the element's own ``line`` is the
  data row.  Both asymmetries are measured in the reference; neither is a bug
  to fix.
* ``match.location`` is the resolved step function's dotted Python path with no
  parentheses and no parameter types (plan deviation 8: no analogue of Java's
  ``com.testinium...Crm.method(java.lang.String)`` exists, so the field's shape
  and role are preserved rather than its content).  ``match`` is ``{}`` for an
  undefined step -- the JVM emits ``location`` only when the status is not
  undefined.
* ``match.arguments`` appears only when the step took at least one parameter.
* ``duration`` is always an **integer of nanoseconds** (behave reports float
  seconds); ``0`` is recorded faithfully, and whether to emit the key is the
  writer's decision -- the JVM emits it only when non-zero.
* ``result.status`` is behave's normalised status name.  The collector applies
  no dry-run and no tag-filter mapping; the writers own those rules.

**Hook entry** -- an element of a scenario's ``after`` list::

    {
      "match": {"location": "features.environment.after_scenario"},
      "result": {"status": "passed", "duration": 0},
      "embeddings": [{"mime_type": "image/png", "data": "<base64>",
                      "name": "<scenario name>"}],
    }

The embedding shape is not invented here: it comes from ``Hooks.java:15``'s
``scenario.attach(screenshot, "image/png", scenario.getName())``, it is what
``app/reporting/screenshots.py`` produces, and
``app/templates/partials/lightbox.html`` renders it as
``data:<mime_type>;base64,<data>``.

Timestamps
----------
``start_timestamp``, ``started_at`` and ``generated_at`` all use
:func:`format_timestamp`, which emits exactly ``YYYY-MM-DDTHH:MM:SS.mmmZ``.
That is the JVM generator's ``yyyy-MM-dd'T'HH:mm:ss.SSSXXX`` pattern with
``withZone(ZoneOffset.UTC)``, where ``XXX`` renders UTC as a literal ``Z``, and
it is also the format ``app/templates/index.html`` expects for its
``modified_iso``.

Boundaries
----------
* The only intra-package import is :mod:`app.utils.paths` (the plan's ``RP -->
  UT`` edge).  Nothing in ``app/reporting`` imports a service; the services
  import this module.
* Flask, ``app.config``, ``app.automation``, ``app.web``, ``app.pages`` and
  Selenium are **not** imported, so this module is usable inside a worker
  process that never builds a Flask application.
* No path literal appears here: every path comes from :mod:`app.utils.paths`.
* Nothing is deleted.  Emptying ``target/`` and tearing down
  ``target/.workers/`` belong to ``app/cli.py``.
* No presentation is computed.  Status tokens for templates belong to
  ``status_badge.html`` and duration formatting to ``pretty/_macros.html``;
  this module supplies raw values.
"""

from __future__ import annotations

import base64
import copy
import functools
import json
import logging
import os
import platform
import re
from collections.abc import Callable, Iterable, Iterator, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

import behave
from behave.formatter.base import Formatter
from behave.step_registry import registry as behave_step_registry

from app.utils.paths import FILE_URI_SCHEME, ensure_parent, normalize_feature_uri

__all__ = [
    "BACKGROUND_KEYWORD",
    "DEFAULT_AFTER_HOOK_LOCATION",
    "ELEMENT_TYPE_BACKGROUND",
    "ELEMENT_TYPE_SCENARIO",
    "FEATURE_KEYWORD",
    "FORMATTER_NAME",
    "FORMATTER_SCOPED_NAME",
    "HOOK_FAILURE_MESSAGE",
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
    "run_metadata",
    "scenario_element_id",
    "scenario_tag",
    "step_keyword",
    "widen_quoted_span",
]

#: Module logger.  Deliberately without a handler of its own: the command-line
#: entry point installs the handler split that routes WARNING-and-above to
#: stderr, and Python's ``lastResort`` handler covers a bare import in a test.
#: A ``NullHandler`` here would silence both routes.
logger = logging.getLogger(__name__)

#: A JSON object in the internal schema.  The document is intentionally plain
#: data, so every level shares this alias.
JsonDict = dict[str, Any]

#: The whole intermediate document -- what :func:`new_result_set` returns and
#: what every writer consumes.
ResultSet = dict[str, Any]

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

#: Version of the document shape defined in this module's docstring.  Bump it
#: only for a change that an existing reader could not survive; adding an
#: optional key is not such a change.  :func:`load_result_set` warns - and does
#: not fail - when it reads a different version, because a stale worker file is
#: a diagnosable condition rather than a crash.
SCHEMA_VERSION: Final[int] = 1

#: Short formatter name, for ``behave -f`` once the class has been registered
#: under it.  The port never registers it, and uses the scoped name instead.
FORMATTER_NAME: Final[str] = "resultcollector"

#: The ``module:Class`` string behave resolves through
#: ``behave.formatter._registry.load_formatter_class`` / ``parse_scoped_name``.
#: ``app/services/test_run_service.py`` passes this verbatim to ``-f`` so that
#: the scoped name has exactly one definition.
FORMATTER_SCOPED_NAME: Final[str] = "app.reporting.events:ResultCollectorFormatter"

#: Dotted path recorded for an attachment that arrives through behave's own
#: ``context.attach()`` route, which carries no reference to the hook that
#: called it.  ``features/environment.py`` owns the scenario lifecycle and is
#: where the port's port of ``Hooks.teardownScenario`` lives, so this is the
#: hook that produced the attachment.  A caller that knows better can pass its
#: own location to :func:`attach_to_current_scenario`.
DEFAULT_AFTER_HOOK_LOCATION: Final[str] = "features.environment.after_scenario"

#: ``type`` values of an element.  The JVM emits exactly these two.
ELEMENT_TYPE_BACKGROUND: Final[str] = "background"
ELEMENT_TYPE_SCENARIO: Final[str] = "scenario"

#: Feature-level ``keyword``.  Constant because the JVM writes the Gherkin
#: keyword and every feature in the suite is English.
FEATURE_KEYWORD: Final[str] = "Feature"

#: Fallback Background ``keyword``, used only if behave's model does not carry
#: one; behave normally supplies the localised keyword from the source.
BACKGROUND_KEYWORD: Final[str] = "Background"

#: Logged when a formatter hook receives something it cannot interpret.  A
#: formatter exception would take down a worker mid-run and turn a green suite
#: into a non-zero exit, so every hook body is guarded and the failure is
#: reported rather than raised.  Exposed so tests assert against a constant.
HOOK_FAILURE_MESSAGE: Final[str] = (
    "Result collector hook %s failed; the affected event was skipped and the "
    "run continues"
)

#: Serialisation options, shared by the formatter and :func:`dump_result_set`
#: so that a worker file and a hand-written fixture have the same shape.
#: ``ensure_ascii=False`` keeps text such as the French validation message
#: ``Veuillez renseigner ce champ.`` readable instead of escaping it; the
#: indentation is for the human who has to read a failing shard.
_JSON_DUMP_KWARGS: Final[dict[str, Any]] = {
    "ensure_ascii": False,
    "indent": 2,
    "sort_keys": False,
}

#: Characters the JVM's ``TestSourcesModel.convertToId`` replaces with ``-``.
#: Read from the ``cucumber-core`` 7.2.3 bytecode, which applies the Java
#: regular expression ``[\s'_,!]``; Java's ``\s`` is exactly
#: ``[ \t\n\x0B\f\r]``, so the class is spelled out here rather than written as
#: Python's ``\s``, which is Unicode-aware and would also fold characters such
#: as a non-breaking space that the JVM leaves alone.
_ID_REPLACED_CHARS: Final[str] = " \t\n\x0b\f\r'_,!"

_ID_TRANSLATION: Final[dict[int, str]] = {
    ord(character): "-" for character in _ID_REPLACED_CHARS
}

#: Separator between the segments of a scenario ``id``.
_ID_SEPARATOR: Final[str] = ";"

#: Marker behave's ``ScenarioOutlineBuilder.annotation_schema`` inserts into a
#: generated row scenario's name: ``"{name} -- @{row.id} {examples.name}"``.
#: The JVM's element name carries no such suffix, so it is removed using the
#: row id behave itself reports, which makes the removal exact rather than a
#: guess.
_OUTLINE_ANNOTATION_PREFIX: Final[str] = " -- @"

#: Fallback for the same removal, used only when behave's model does not carry
#: the row id.  The block and row numbers are captured because the annotation
#: is then the only remaining source of the row's position, which the scenario
#: id needs; the trailing group is optional because an unnamed ``Examples:``
#: block renders the annotation with a trailing space and nothing after it.
_OUTLINE_ANNOTATION_RE: Final[re.Pattern[str]] = re.compile(
    r" -- @(?P<block>\d+)\.(?P<row>\d+)(?: (?P<examples>.*))?$"
)

#: Placeholder used whenever a metadata probe yields nothing.
_UNKNOWN_METADATA_VALUE: Final[str] = ""


class ResultSetError(RuntimeError):
    """Raised when a result-set file is absent, unreadable or not this schema.

    :func:`load_result_set` raises this and nothing else, so that
    ``app/services/test_run_service.py`` can name the offending shard on stderr
    and apply the plan's exit table instead of inferring intent from a bare
    :class:`OSError` or :class:`json.JSONDecodeError`.  The originating
    exception is always chained, so the cause survives for a log.
    """


# --------------------------------------------------------------------------- #
# Value helpers.  Each one is the single owner of its conversion, because
# every one of these rules is measured against the reference report and a
# second implementation of any of them would drift.
# --------------------------------------------------------------------------- #


def format_timestamp(moment: datetime) -> str:
    """Format ``moment`` as the report contract's UTC timestamp.

    The output is exactly ``YYYY-MM-DDTHH:MM:SS.mmmZ`` -- millisecond
    precision, three fractional digits always, and a literal ``Z``.  This
    reproduces the JVM generator's ``yyyy-MM-dd'T'HH:mm:ss.SSSXXX`` pattern
    applied ``withZone(ZoneOffset.UTC)``, as measured in the reference report's
    ``"2022-09-07T13:37:26.297Z"``.  :meth:`datetime.datetime.isoformat` is not
    usable unmodified: it emits microseconds and ``+00:00``.

    Args:
        moment: The instant to format.  A timezone-aware value is converted to
            UTC; a naive value is *assumed* to be UTC, which is what
            :func:`datetime.datetime.utcnow`-style callers supply.

    Returns:
        The formatted timestamp.

    Examples:
        >>> format_timestamp(datetime(2022, 9, 7, 13, 37, 26, 297123,
        ...                           tzinfo=timezone.utc))
        '2022-09-07T13:37:26.297Z'
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    else:
        moment = moment.astimezone(timezone.utc)
    # Truncation, not rounding: Java's SSS field prints the millisecond part of
    # the instant, so 297_999 microseconds is ".297" there too.
    milliseconds = moment.microsecond // 1000
    return f"{moment:%Y-%m-%dT%H:%M:%S}.{milliseconds:03d}Z"


def _utc_now() -> datetime:
    """Return the current instant in UTC.

    Sole clock reading in this module, so that
    :class:`ResultCollectorFormatter` can be pointed at a fixed clock in a test
    by overriding :attr:`ResultCollectorFormatter.clock`, and so that
    :func:`merge_result_sets` can be verified to read no clock at all.
    """
    return datetime.now(timezone.utc)


def nanos_from_seconds(seconds: float | int | None) -> int:
    """Convert behave's float-second duration to the contract's nanoseconds.

    The Cucumber JSON contract carries durations as integer nanoseconds -- the
    reference report's ``30202000000`` is 30.202 seconds -- while behave
    reports float seconds and always reports one.

    Args:
        seconds: A duration in seconds, or ``None`` for a step that has none.

    Returns:
        The duration in whole nanoseconds.  ``None``, a non-numeric value and a
        negative value all yield ``0``: ``0`` is recorded faithfully rather
        than dropped, because whether to emit the key is the writer's decision
        (the JVM emits ``duration`` only when it is non-zero, which is why the
        reference contains both a bare ``{"status": "skipped"}`` and a
        ``{"duration": 1000000, "status": "skipped"}``).

    Examples:
        >>> nanos_from_seconds(30.202)
        30202000000
        >>> nanos_from_seconds(0.001)
        1000000
        >>> nanos_from_seconds(None)
        0
    """
    if seconds is None or isinstance(seconds, bool):
        return 0
    if not isinstance(seconds, (int, float)):
        return 0
    try:
        nanoseconds = int(round(float(seconds) * 1_000_000_000))
    except (OverflowError, ValueError):
        return 0
    return nanoseconds if nanoseconds > 0 else 0


def convert_to_id(text: str | None) -> str:
    """Slugify ``text`` exactly as the JVM report generator does.

    Read from the ``cucumber-core`` 7.2.3 bytecode of
    ``TestSourcesModel.convertToId``, which is
    ``text.replaceAll("[\\s'_,!]", "-").toLowerCase()``: whitespace,
    apostrophes, underscores, commas and exclamation marks each become a single
    ``-``, and the result is lower-cased.  Nothing else is touched, so periods,
    colons, quotation marks and parentheses survive verbatim -- which is why
    ``Sales.feature``'s ``".... app Sales feature"`` keeps its leading dots and
    why an unnamed ``Examples:`` block contributes an empty segment and a
    doubled separator to a row id.  Those are source behaviours the port
    preserves rather than tidies.

    Args:
        text: A feature, scenario or Examples name.  ``None`` is treated as an
            empty name, which is what an unnamed Examples block yields.

    Returns:
        The slug.  Lower-casing uses Python's locale-independent
        :meth:`str.lower`, which agrees with the JVM's default-locale
        ``toLowerCase()`` for the suite's ASCII names and, unlike it, cannot
        vary with the machine's locale.

    Examples:
        >>> convert_to_id("Testinium app CRM Module")
        'testinium-app-crm-module'
        >>> convert_to_id("User can change any user's information")
        'user-can-change-any-user-s-information'
        >>> convert_to_id(None)
        ''
    """
    if not text:
        return ""
    return text.translate(_ID_TRANSLATION).lower()


def scenario_element_id(
    feature_name: str | None,
    scenario_name: str | None,
    examples_name: str | None = None,
    row_index: int | None = None,
) -> str:
    """Build a scenario element's ``id``, JVM-compatibly.

    ``TestSourcesModel.calculateId`` walks the Gherkin AST upwards, so a plain
    scenario's id is ``<feature>;<scenario>`` while an Examples row's is
    ``<feature>;<outline>;<examples>;<position>``.  The reference report's
    ``"testinium-app-crm-module;user-can-change-information-in-dashboard;expected-name;2"``
    is the second form, for the first data row of ``Examples: Expected name``.

    Args:
        feature_name: The feature's name.
        scenario_name: The scenario's name -- for an outline row, the outline's
            name without behave's ``" -- @1.1 Examples"`` annotation.
        examples_name: The Examples block's name, or ``None``/``""`` for an
            unnamed block, which contributes an empty segment exactly as the
            JVM's ``convertToId("")`` does.
        row_index: behave's **one-based** index of the row within the Examples
            block's body, or ``None`` for a plain scenario.  The JVM appends
            ``bodyRowIndex + 2`` with a zero-based index, i.e. the header row
            counts as 1, so this becomes ``row_index + 1``.

    Returns:
        The id.  Two features that share a name legitimately produce equal
        ids -- ``Contact``/``Inventory`` and ``Login``/``Notes`` each share a
        title -- and that collision is preserved, never disambiguated, which is
        why the HTTP report routes key on list position instead.

    Examples:
        >>> scenario_element_id("Testinium app CRM Module",
        ...                     "User can change the situation in progress")
        'testinium-app-crm-module;user-can-change-the-situation-in-progress'
        >>> scenario_element_id("Testinium app CRM Module",
        ...                     "User can change information in dashboard",
        ...                     "Expected name", 1)
        'testinium-app-crm-module;user-can-change-information-in-dashboard;expected-name;2'
    """
    parts = [convert_to_id(feature_name), convert_to_id(scenario_name)]
    if row_index is not None:
        parts.append(convert_to_id(examples_name))
        parts.append(str(row_index + 1))
    return _ID_SEPARATOR.join(parts)


def step_keyword(keyword: str | None) -> str:
    """Return a step ``keyword`` with the single trailing space the JVM emits.

    Measured in the reference for all four of ``"Given "``, ``"When "``,
    ``"And "`` and ``"Then "``.  behave's ``step.keyword`` carries no trailing
    space, so one is appended here -- once, in this function, because
    ``app/templates/partials/step_row.html``'s ``step_keyword`` macro is the
    single place it is trimmed again for display.

    Args:
        keyword: behave's step keyword, or ``None``.

    Returns:
        The keyword plus one trailing space, or ``""`` when there is no
        keyword -- never a lone space, which would be neither the JVM's output
        nor a usable display value.
    """
    text = (keyword or "").strip()
    return f"{text} " if text else ""


def widen_quoted_span(name: str, start: int, end: int) -> tuple[str, int]:
    """Widen a parameter span over its surrounding quotes, JVM-compatibly.

    The JVM records a step argument as the raw matched substring of the step
    text **including the surrounding double quotes**, with a zero-based offset
    into the step name.  The reference's outline step, whose name is
    ``User can change any user's information like "Test2" , "30" and "2"``,
    records ``("\\"Test2\\"", 44)``, ``("\\"30\\"", 54)`` and ``("\\"2\\"", 63)``.
    Cucumber's ``{string}`` placeholder produced those spans; this port's step
    phrases put the quotes in the phrase literal and let the placeholder
    capture the inner text, so behave reports 45, 55 and 64 -- one character
    inside each quote.

    Args:
        name: The step name the span indexes into.
        start: behave's ``Argument.start``.
        end: behave's ``Argument.end`` (exclusive).

    Returns:
        A ``(val, offset)`` pair.  When the characters immediately before and
        after the span are both double quotes the span is widened by one
        character on each side, reproducing the JVM shape; otherwise -- an
        unquoted placeholder such as ``{count:d}`` -- it is recorded as behave
        gave it.  ``val`` is always sliced out of ``name``, so
        ``name[offset:offset + len(val)] == val`` holds by construction.  A
        span that does not index into ``name`` yields ``("", max(start, 0))``
        rather than raising.

    Examples:
        >>> step_name = 'User can change any user\\'s information like "Test2" , "30" and "2"'
        >>> widen_quoted_span(step_name, 45, 50)
        ('"Test2"', 44)
        >>> widen_quoted_span('unquoted param 42 here', 15, 17)
        ('42', 15)
    """
    if not isinstance(start, int) or not isinstance(end, int):
        return "", 0
    if not 0 <= start <= end <= len(name):
        return "", max(start, 0)
    if start > 0 and end < len(name) and name[start - 1] == '"' and name[end] == '"':
        return name[start - 1 : end + 1], start - 1
    return name[start:end], start


def _safe_probe(probe: Callable[[], Any]) -> str:
    """Run a metadata probe and never let it fail the run.

    Args:
        probe: A zero-argument callable returning a value to describe.

    Returns:
        ``str(value)`` when the probe returns something truthy, otherwise
        :data:`_UNKNOWN_METADATA_VALUE`.  Any exception is swallowed after
        being logged at debug level: a report is not worth a failed test run.
    """
    try:
        value = probe()
    except Exception:  # pragma: no cover - platform probes do not raise here
        logger.debug("Metadata probe failed", exc_info=True)
        return _UNKNOWN_METADATA_VALUE
    if not value:
        return _UNKNOWN_METADATA_VALUE
    return str(value)


def run_metadata() -> JsonDict:
    """Describe the engine, interpreter, operating system and processor.

    The four keys and their sub-keys are fixed vocabulary:
    ``app/templates/artifact/metadata.html`` renders exactly
    ``implementation{name,version}``, ``runtime{name,version}``, ``os{name}``
    and ``cpu{name}``.

    Returns:
        The metadata mapping.  Every value is a string, and a probe that yields
        nothing yields ``""`` -- ``platform.processor()`` is empty on many
        Linux builds, for instance, so the machine type is used instead.  This
        function never raises.
    """
    return {
        "implementation": {
            "name": "behave",
            "version": _safe_probe(lambda: getattr(behave, "__version__", "")),
        },
        "runtime": {
            "name": _safe_probe(platform.python_implementation),
            "version": _safe_probe(platform.python_version),
        },
        "os": {"name": _safe_probe(platform.system)},
        "cpu": {
            "name": _safe_probe(platform.processor) or _safe_probe(platform.machine),
        },
    }


# --------------------------------------------------------------------------- #
# Document builders.  These are public because the writer tests and
# ``tests/fixtures/sample_results.json`` build documents too, and the
# key-presence rules below - a background without an id, a scenario without an
# empty tag list - must have exactly one implementation.
# --------------------------------------------------------------------------- #


def feature_tag(name: str, line: int, column: int = 1) -> JsonDict:
    """Build a feature-level tag in the JVM's long shape.

    A feature tag is ``{"name": "@Smoke", "type": "Tag", "location": {"line":
    1, "column": 1}}`` -- measured in the reference, where ``@Smoke`` sits at
    line 1 while the feature itself is at line 2, which is why the tag's own
    location is recorded and not the feature's.

    Args:
        name: The tag name, with or without its leading ``@``; the ``@`` is
            added when missing, because the JVM keeps it and behave strips it.
        line: The line the tag was declared on.
        column: The one-based column of the tag's ``@``.

    Returns:
        The tag mapping.
    """
    return {
        "name": name if name.startswith("@") else f"@{name}",
        "type": "Tag",
        "location": {"line": int(line), "column": int(column)},
    }


def scenario_tag(name: str) -> JsonDict:
    """Build a scenario-level tag in the JVM's short shape.

    A scenario tag is ``{"name": "@Smoke"}`` and nothing else -- the asymmetry
    with :func:`feature_tag` is measured, not stylistic.

    Args:
        name: The tag name, with or without its leading ``@``.

    Returns:
        The tag mapping.
    """
    return {"name": name if name.startswith("@") else f"@{name}"}


def new_step(
    *,
    keyword: str | None,
    line: int,
    name: str,
    matched: bool = False,
    match: JsonDict | None = None,
    result: JsonDict | None = None,
) -> JsonDict:
    """Build a step object.

    Args:
        keyword: behave's step keyword; the trailing space the contract
            requires is added by :func:`step_keyword`.
        line: The step's line in the feature file.  For an outline row this is
            the outline template's step line, not the data row's -- measured,
            and deliberately asymmetric with the element's own ``line``.
        name: The step text, with outline placeholders already substituted.
        matched: Whether a step definition was resolved for this step.
        match: The match mapping; ``{}`` for an undefined step, which is what
            makes the writer able to omit ``location`` exactly as the JVM does.
        result: The result mapping; empty until the step's outcome is known.

    Returns:
        The step object.  All five keys are always present, so no consumer
        needs a membership test; the *contents* of ``match`` and ``result``
        carry the optionality.
    """
    return {
        "keyword": step_keyword(keyword),
        "line": int(line),
        "name": name,
        "matched": bool(matched),
        "match": dict(match) if match else {},
        "result": dict(result) if result else {},
    }


def new_hook_entry(
    *,
    location: str | None = DEFAULT_AFTER_HOOK_LOCATION,
    status: str = "passed",
    duration: int = 0,
    embeddings: Sequence[JsonDict] | None = None,
) -> JsonDict:
    """Build an after-hook entry for a scenario's ``after`` list.

    This is the port of the JVM's hook step map, which is where a screenshot
    lands: ``Hooks.java:11-18`` captures on failure and attaches, and the JSON
    generator hangs the attachment off the hook rather than off a step.

    Args:
        location: Dotted path of the hook function.  ``None`` yields an empty
            ``match``, mirroring the JVM's omission of ``location`` when it has
            none.
        status: The hook's own outcome.
        duration: The hook's duration in nanoseconds.
        embeddings: Attachment mappings, each with ``mime_type``, ``data`` and
            optionally ``name``, exactly as ``app/reporting/screenshots.py``
            produces them.

    Returns:
        The hook entry.
    """
    return {
        "match": {"location": location} if location else {},
        "result": {"status": status, "duration": int(duration)},
        "embeddings": [dict(embedding) for embedding in (embeddings or ())],
    }


def new_element(
    *,
    element_type: str,
    keyword: str | None,
    line: int,
    name: str,
    description: str = "",
    selected: bool = True,
    identifier: str | None = None,
    start_timestamp: str | None = None,
    tags: Sequence[JsonDict] | None = None,
    steps: Sequence[JsonDict] | None = None,
    after: Sequence[JsonDict] | None = None,
) -> JsonDict:
    """Build a Background occurrence or a scenario element.

    The key-presence rules are the measured ones, and this function is their
    only implementation:

    * both kinds always carry ``type``, ``keyword``, ``line``, ``name``,
      ``description``, ``selected`` and ``steps``;
    * a **scenario** additionally carries ``id``, ``start_timestamp`` and
      ``after``, and carries ``tags`` *only when it has some* -- an untagged
      scenario omits the key rather than carrying ``[]``, because the JVM's
      test-case map adds it under ``if (!testCase.getTags().isEmpty())``.  Five
      of the ten features declare no feature-level tag (Contact, Inventory,
      Notes, Sales and Session), so omission is the common case;
    * a **background** carries none of ``id``, ``start_timestamp``, ``tags`` or
      ``after``, whatever is passed for them.

    Args:
        element_type: :data:`ELEMENT_TYPE_BACKGROUND` or
            :data:`ELEMENT_TYPE_SCENARIO`.  Any other value is treated as a
            scenario, after a warning, because dropping the element outright
            would lose results.
        keyword: ``"Background"``, ``"Scenario"`` or ``"Scenario Outline"``.
        line: The element's line -- the data row's line for an outline row.
        name: The element's name, without behave's outline annotation suffix.
        description: Description text, ``""`` when empty, with leading
            indentation preserved.
        selected: Whether the effective tag expression selected the scenario;
            a background inherits its scenario's value.
        identifier: The scenario ``id`` from :func:`scenario_element_id`;
            ignored for a background.
        start_timestamp: The scenario's start timestamp from
            :func:`format_timestamp`; ignored for a background.
        tags: Short-shape tags from :func:`scenario_tag`; ignored for a
            background, and omitted entirely when empty.
        steps: Step objects from :func:`new_step`.
        after: Hook entries from :func:`new_hook_entry`; ignored for a
            background.

    Returns:
        The element object.
    """
    if element_type not in (ELEMENT_TYPE_BACKGROUND, ELEMENT_TYPE_SCENARIO):
        logger.warning(
            "Unknown element type %r recorded as a scenario", element_type
        )
        element_type = ELEMENT_TYPE_SCENARIO

    element: JsonDict = {
        "type": element_type,
        "keyword": (keyword or "").strip(),
        "line": int(line),
        "name": name,
        "description": description or "",
        "selected": bool(selected),
        "steps": list(steps or []),
    }
    if element_type == ELEMENT_TYPE_BACKGROUND:
        # Nothing more, by contract.  A Background occurrence is deliberately
        # poorer than a scenario: the templates rely on it.
        return element

    element["id"] = identifier or ""
    element["start_timestamp"] = start_timestamp
    if tags:
        element["tags"] = [dict(tag) for tag in tags]
    element["after"] = list(after or [])
    return element


def new_feature(
    *,
    uri: str,
    path: str,
    identifier: str,
    line: int,
    name: str,
    description: str = "",
    keyword: str = FEATURE_KEYWORD,
    tags: Sequence[JsonDict] | None = None,
    elements: Sequence[JsonDict] | None = None,
) -> JsonDict:
    """Build a feature object.

    Args:
        uri: The ``file:``-prefixed, repository-relative feature URI, e.g.
            ``"file:features/Crm.feature"``.  ``cucumber_json.py`` copies it
            through and ``pretty_reports.py`` hashes it for its detail-page
            filenames, so the shape is load-bearing.
        path: The same path without the scheme, e.g.
            ``"features/Crm.feature"``, which is what ``rerun_report.py`` and
            human-facing output use.  Carrying both means no consumer performs
            string surgery.
        identifier: The feature ``id`` from :func:`convert_to_id`.
        line: The ``Feature:`` line -- 2 in ``Crm.feature``, whose first line
            is the ``@Smoke`` tag.
        name: The feature's name.
        description: Description text, ``""`` when empty, with leading
            indentation preserved.
        keyword: The Gherkin keyword, ``"Feature"``.
        tags: Long-shape tags from :func:`feature_tag`.  Unlike a scenario's,
            the key is **always present** and may be an empty list, because the
            JVM's feature map adds it unconditionally.
        elements: Element objects from :func:`new_element`.

    Returns:
        The feature object.
    """
    return {
        "uri": uri,
        "path": path,
        "id": identifier,
        "keyword": keyword,
        "line": int(line),
        "name": name,
        "description": description or "",
        "tags": [dict(tag) for tag in (tags or ())],
        "elements": list(elements or []),
    }


def new_result_set(
    *,
    dry_run: bool = False,
    tag_expression: str | None = None,
    metadata: JsonDict | None = None,
    started_at: str | None = None,
    generated_at: str | None = None,
    features: Sequence[JsonDict] | None = None,
) -> ResultSet:
    """Build a fully-formed, empty-by-default result document.

    Every run-level key is present from the outset, so that a consumer -- a
    writer, the merge, or a hand-written fixture -- never has to test for
    membership.

    Args:
        dry_run: Mirrored from behave's config, because
            ``app/reporting/cucumber_json.py`` needs it to apply the dry-run
            status mapping (the JVM emits matched steps ``passed`` and
            unmatched ``undefined`` under ``dryRun``, where behave reports
            ``untested``).
        tag_expression: The effective tag expression, or ``None`` when no
            filter applies.
        metadata: Override for :func:`run_metadata`, chiefly so a test can pin
            the values.
        started_at: The earliest scenario ``start_timestamp``, or ``None`` when
            no scenario ran.
        generated_at: When the document was written, or ``None`` until it is.
        features: Feature objects; empty by default.

    Returns:
        The document.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "started_at": started_at,
        "generated_at": generated_at,
        "dry_run": bool(dry_run),
        "tag_expression": tag_expression,
        "metadata": run_metadata() if metadata is None else dict(metadata),
        "features": list(features or []),
    }



# --------------------------------------------------------------------------- #
# Internal helpers shared by the formatter and the load/merge primitives
# --------------------------------------------------------------------------- #


def _serialize(document: ResultSet) -> str:
    """Render ``document`` as JSON text using the shared options.

    Args:
        document: The result document.

    Returns:
        The JSON text, without a trailing newline.  A value the encoder cannot
        handle -- which the builders make impossible, but a hook cannot be
        proven never to store one -- is coerced with :func:`str` after a
        warning, because a diagnosable document beats no document at all.
    """
    try:
        return json.dumps(document, **_JSON_DUMP_KWARGS)
    except TypeError:
        logger.warning(
            "Result document contains a value JSON cannot encode; it was "
            "coerced to text so the document is still written"
        )
        return json.dumps(document, default=str, **_JSON_DUMP_KWARGS)


def _normalize_newlines(text: str) -> str:
    """Normalise CRLF and CR line endings to LF.

    Failure text is the one field whose formatting cannot be preserved (plan
    deviation 16: the reference carries JUnit assertion messages and Java stack
    traces with ``\\r\\n``, which Python cannot produce).  Normalising here
    keeps the intermediate document identical whatever platform a worker ran
    on, and is idempotent with the same normalisation in the writer.

    Args:
        text: The raw message.

    Returns:
        The message with LF line endings.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _status_name(status: Any) -> str:
    """Return behave's normalised status name for ``status``.

    behave's :class:`~behave.model_type.Status` exposes ``normalized_name``,
    which folds ``untested_undefined`` to ``undefined`` and the two pending
    variants to ``pending``.  That is exactly the vocabulary the writers map
    from, so it is preferred over the raw enum name.  No dry-run or tag-filter
    rule is applied here: those belong to the writers, which have ``dry_run``
    and ``selected`` to work from.

    Args:
        status: A behave status enum value, a string, or ``None``.

    Returns:
        The status name.  ``None`` yields ``"untested"``, behave's own initial
        status, so the field is never absent or null.
    """
    if status is None:
        return "untested"
    for attribute in ("normalized_name", "name"):
        value = getattr(status, attribute, None)
        if isinstance(value, str) and value:
            return value
    return str(status)


def _module_name_from_code(func: Any) -> str:
    """Derive a dotted module name from a function's own source filename.

    Step functions have **no** ``__module__``: behave does not import a step
    module, it ``exec``s it with a globals dict that carries no ``__name__``
    (``runner_util.load_step_modules``).  The code object still knows where it
    came from, and behave compiles it with a filename made relative to the
    working directory, so ``features/steps/crm_steps.py`` becomes
    ``features.steps.crm_steps`` -- the dotted path the report contract wants.

    This is not a path literal and not path *ownership*: no location is chosen
    here, an existing function's own ``co_filename`` is merely translated into
    a module name, exactly as :mod:`importlib` would.  The artifact and feature
    directories remain owned by :mod:`app.utils.paths`.

    Args:
        func: The function to describe.

    Returns:
        The dotted module name, or ``""`` when the function has no Python
        source file (a builtin, or a callable defined in a string).
    """
    code = getattr(func, "__code__", None)
    filename = str(getattr(code, "co_filename", "") or "").replace("\\", "/")
    if not filename.endswith(".py"):
        return ""
    filename = filename[: -len(".py")]
    if filename.endswith("/__init__"):
        filename = filename[: -len("/__init__")]
    if filename.startswith("/") or (len(filename) > 1 and filename[1] == ":"):
        # An absolute filename: express it relative to the working directory,
        # which is what behave itself does when it compiles a step module.
        try:
            filename = os.path.relpath(filename, os.getcwd()).replace("\\", "/")
        except (OSError, ValueError):
            return ""
    parts = [part for part in filename.split("/") if part not in ("", ".", "..")]
    return ".".join(parts)


def _dotted_path(func: Any) -> str:
    """Return ``func``'s dotted Python path, e.g. ``features.steps.crm_steps.f``.

    This is the port's ``match.location`` (plan deviation 8).  Java's location
    was ``com.testinium.step_definitions.Crm.method(java.lang.String)``; no
    analogue exists in Python, so the field's shape and role are preserved -
    a stable identifier of the code that ran - while its content is Pythonic.
    **No parentheses and no parameter types are emitted.**

    Args:
        func: The resolved step or hook function.

    Returns:
        ``"<module>.<qualname>"``.  ``__module__`` is used when the function
        belongs to a real module, and the module name is derived from the
        function's source file otherwise, because behave's step modules are
        never imported and therefore have no ``__module__`` (see
        :func:`_module_name_from_code`).  Whichever part is available is
        returned when the other is not, and ``""`` when neither is.
    """
    module = getattr(func, "__module__", "") or ""
    if not module:
        module = _module_name_from_code(func)
    qualname = getattr(func, "__qualname__", "") or getattr(func, "__name__", "") or ""
    if module and qualname:
        return f"{module}.{qualname}"
    return qualname or module


def _config_flag(config: Any, attribute: str) -> bool:
    """Read a boolean off behave's config without ever raising.

    Args:
        config: behave's configuration object.
        attribute: The attribute to read.

    Returns:
        The value coerced to :class:`bool`, or ``False`` when the attribute is
        absent.  behave's configuration surface has moved between releases, and
        a formatter that raised in its constructor would abort the worker.
    """
    return bool(getattr(config, attribute, False))


def _config_tag_expression(config: Any) -> str | None:
    """Derive the effective tag-expression string from behave's config.

    ``--tags=@Smoke`` arrives as ``config.tags == ["@Smoke"]``; the
    ``default_tags`` key in ``behave.ini`` arrives as ``config.default_tags``
    and applies only when the command line supplied no filter, which is
    exactly the precedence checked here.  ``config.tag_expression`` is
    deliberately not used: it is a parsed object whose ``str()`` drops the
    ``@`` sigils.

    Args:
        config: behave's configuration object.

    Returns:
        The expression as written, several ``--tags`` occurrences joined by a
        space (behave ANDs them), or ``None`` when no filter applies.
    """
    for attribute in ("tags", "default_tags"):
        value = getattr(config, attribute, None)
        if not value:
            continue
        if isinstance(value, str):
            return value
        if isinstance(value, (list, tuple, set, frozenset)):
            return " ".join(str(item) for item in value)
        return str(value)
    return None


def _guarded(method: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a formatter hook so that it can never raise into behave.

    A formatter exception propagates out of the model's run loop and takes the
    worker down mid-run, turning a green suite into a non-zero exit and losing
    every result the worker had collected.  Every hook body is therefore
    guarded: an unexpected shape is logged with a traceback and that single
    event is skipped, leaving the rest of the run - and the document
    :meth:`ResultCollectorFormatter.close` writes - intact.

    Args:
        method: The hook method to wrap.

    Returns:
        The wrapped method, which returns ``None`` if the body failed.
    """

    @functools.wraps(method)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return method(self, *args, **kwargs)
        except Exception:
            logger.exception(HOOK_FAILURE_MESSAGE, method.__name__)
            return None

    return wrapper


#: Formatter instances that have been constructed and not yet closed.  behave
#: builds its formatters on the main thread and runs its default runner
#: single-threaded - the port's concurrency is process-based (plan deviation
#: 4), one behave run per worker process - so a plain list needs no lock.
_ACTIVE_COLLECTORS: list["ResultCollectorFormatter"] = []


def attach_to_current_scenario(
    embedding: JsonDict,
    hook_location: str | None = None,
) -> bool:
    """Record an attachment on the scenario currently being collected.

    Two routes reach a scenario's ``after`` entry, and both are supported
    because the scenario lifecycle lives in ``features/environment.py``, which
    the plan's dependency graph keeps free of any import from this package:

    1. behave's own ``context.attach(mime_type, data)``, which the runner
       forwards to :meth:`ResultCollectorFormatter.embedding`.  This is the
       route that needs no cooperation at all.
    2. This function, for a caller that already holds the embedding mapping
       built by ``app/reporting/screenshots.py`` and wants it recorded verbatim,
       with its own hook location.

    Args:
        embedding: A mapping with ``mime_type``, ``data`` and optionally
            ``name`` - exactly what ``screenshots.build_embedding`` returns.
        hook_location: Dotted path of the hook recording it; defaults to
            :data:`DEFAULT_AFTER_HOOK_LOCATION`.

    Returns:
        ``True`` when a collector accepted it, ``False`` when no collector is
        active or no scenario is current.  A ``False`` return is not an error:
        the suite may be running under a different formatter, and screenshot
        evidence must never change a test outcome.
    """
    accepted = False
    for collector in reversed(_ACTIVE_COLLECTORS):
        if collector.add_attachment(embedding, hook_location=hook_location):
            accepted = True
    if not accepted:
        logger.debug(
            "No active result collector accepted an attachment; it was dropped"
        )
    return accepted


class ResultCollectorFormatter(Formatter):
    """behave formatter that collects this module's intermediate document.

    Registered by scoped name, so no plugin registration step is needed::

        behave -f app.reporting.events:ResultCollectorFormatter -o <path>

    ``app/services/test_run_service.py`` builds that command line once per
    worker, taking the name from :data:`FORMATTER_SCOPED_NAME` and the path
    from :mod:`app.utils.paths`.

    Lifecycle, as measured against behave 1.3.3 rather than assumed:

    * :meth:`uri` then :meth:`feature`, then :meth:`background` **once per
      feature** - behave announces the definition, not an occurrence.
    * :meth:`scenario` per scenario, at which point this formatter appends the
      Background *occurrence* and then the scenario element, in that order.
      That interleaving is the JVM's: its ``handleTestCaseStarted`` adds a
      fresh background map before every test-case map when the feature has a
      background, which is why the reference report's eight elements are four
      backgrounds and four scenarios rather than one background and four
      scenarios.
    * :meth:`step` for every step of the scenario, background steps first, in
      one flat sequence; the first ``len(scenario.background_steps)`` of them
      land in the Background occurrence and the rest in the scenario.
    * :meth:`match` then :meth:`result` per *executed* step.  Steps that never
      execute - everything after a failure, and undefined steps under
      ``--dry-run`` - get **neither** callback, so their outcome is recovered
      from behave's own step objects when the scenario is finalised, and their
      step definition is resolved from behave's step registry.  That is what
      reproduces the reference's skipped steps, which carry a status *and* a
      ``match.location``.
    * :meth:`eof` per feature file, then :meth:`close` once, which is where the
      document is written.

    No hook raises; see :func:`_guarded`.
    """

    name = FORMATTER_NAME
    description = "Collects the intermediate result document the report writers consume"

    #: Clock used for scenario start timestamps and for ``generated_at``.
    #: Overridable on the class or the instance so a test can pin time without
    #: monkey-patching the module; behave fixes the constructor signature, so
    #: this attribute is the injection seam.
    clock: Callable[[], datetime] = staticmethod(_utc_now)

    def __init__(self, stream_opener: Any, config: Any) -> None:
        """Build the collector and open its output stream.

        Args:
            stream_opener: behave's stream opener, carrying the ``-o`` path.
            config: behave's configuration object.  ``dry_run`` and the tag
                expression are read defensively, because a formatter that
                raised here would abort the worker before a single scenario
                ran.
        """
        super().__init__(stream_opener, config)
        self.result_set: ResultSet = new_result_set(
            dry_run=_config_flag(config, "dry_run"),
            tag_expression=_config_tag_expression(config),
        )
        self._closed = False
        self._source_line_cache: dict[str, list[str]] = {}
        self._current_source: str = ""
        self._reset_feature_state()
        # Open eagerly, as behave's own JSON formatter does: an unwritable
        # ``-o`` path is then a startup failure rather than a surprise at the
        # end of a run, and the merge step can tell an empty shard file (a
        # worker that died) from an absent one (a worker that never started).
        self.stream = self.open()
        _ACTIVE_COLLECTORS.append(self)

    # -- state management ---------------------------------------------------

    def _reset_feature_state(self) -> None:
        """Clear all per-feature and per-scenario state.

        ``_current_source`` deliberately survives, because :meth:`uri` is
        called before :meth:`feature` and its value is needed there.
        """
        self._feature: JsonDict | None = None
        self._feature_name: str = ""
        self._feature_source: str = ""
        self._feature_tag_names: list[str] = []
        self._background_model: Any | None = None
        self._reset_scenario_state()

    def _reset_scenario_state(self) -> None:
        """Clear all per-scenario state, including the step bookkeeping."""
        self._scenario_element: JsonDict | None = None
        self._background_element: JsonDict | None = None
        self._background_step_count: int = 0
        self._announced_steps: int = 0
        self._step_records: list[tuple[JsonDict, Any]] = []
        self._records_by_step_id: dict[int, JsonDict] = {}
        self._pending_match: Any | None = None

    # -- source-file access (the module's only read seam) -------------------

    def read_source_lines(self, filename: str) -> list[str]:
        """Return the feature file's lines, without line terminators.

        Two measured details need the raw source, because behave's parsed model
        discards both: a description's leading indentation, which the JVM
        preserves verbatim (``"  Account is: PosManager"``), and a tag's
        column, which the JVM records alongside its line.

        Overriding this method replaces all file access this class performs,
        which is what lets the unit suite exercise every branch without a
        feature file on disk.

        Args:
            filename: Path of the feature file, as behave reported it.

        Returns:
            The lines, or an empty list when the file cannot be read - in which
            case the callers fall back to behave's parsed values.  Never
            raises, and reads each file at most once per formatter.
        """
        cached = self._source_line_cache.get(filename)
        if cached is not None:
            return cached
        lines: list[str] = []
        if filename:
            try:
                lines = Path(filename).read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()
            except OSError:
                logger.debug(
                    "Feature source %r is unreadable; descriptions and tag "
                    "columns fall back to the parsed model",
                    filename,
                    exc_info=True,
                )
        self._source_line_cache[filename] = lines
        return lines

    def _find_source_line(
        self,
        lines: Sequence[str],
        from_line: int,
        text: str,
    ) -> int | None:
        """Find the first line at or after ``from_line`` whose content is ``text``.

        Args:
            lines: The file's lines.
            from_line: One-based line to start at.
            text: The stripped text to look for.

        Returns:
            The one-based line number, or ``None`` when it is not there.
        """
        for index in range(max(from_line, 1) - 1, len(lines)):
            if lines[index].strip() == text:
                return index + 1
        return None

    def _description_text(self, source: str, entity: Any) -> str:
        """Recover an entity's description with its indentation intact.

        behave exposes a description as a list of *stripped* lines, while the
        JVM emits one string that preserves the source's leading indentation
        and any blank line between description lines.  The stripped lines are
        therefore used to locate the block's first and last lines in the source
        and the raw slice between them is returned, which trims the surrounding
        blank lines exactly as the JVM's parser does while keeping everything
        inside the block verbatim.

        Args:
            source: Path of the feature file.
            entity: A behave feature, background or scenario.

        Returns:
            The description, ``""`` when there is none.  If the source cannot
            be read or a line cannot be located, behave's stripped lines joined
            with newlines are returned - correct content, lost indentation,
            which is strictly better than losing the description.
        """
        description = getattr(entity, "description", None)
        if isinstance(description, str):
            return description
        lines = [str(line) for line in description or ()]
        if not lines:
            return ""

        source_lines = self.read_source_lines(source)
        if not source_lines:
            return "\n".join(lines)

        entity_line = int(getattr(entity, "line", 0) or 0)
        first = self._find_source_line(source_lines, entity_line + 1, lines[0])
        if first is None:
            return "\n".join(lines)
        if len(lines) == 1:
            last: int | None = first
        else:
            last = self._find_source_line(source_lines, first + 1, lines[-1])
        if last is None or last < first:
            return "\n".join(lines)
        return "\n".join(source_lines[first - 1 : last])

    def _tag_column(
        self,
        source: str,
        line: int,
        name: str,
        cursors: dict[int, int],
    ) -> int:
        """Return the one-based column of a tag's ``@`` in the source.

        Args:
            source: Path of the feature file.
            line: The tag's line, as behave reported it.
            name: The tag name, with its ``@``.
            cursors: Per-line search offsets, carried across the tags of one
                entity so that several tags on one line - and a tag that is a
                prefix of another, such as ``@Smoke`` and ``@SmokeTest`` - each
                resolve to their own column.  behave reports tags in source
                order, which is what makes the sequential scan exact.

        Returns:
            The column, or ``1`` when the source is unavailable or the tag text
            cannot be found, which is the column of a tag that starts its line.
        """
        source_lines = self.read_source_lines(source)
        if not 1 <= line <= len(source_lines):
            return 1
        text = source_lines[line - 1]
        position = text.find(name, cursors.get(line, 0))
        if position < 0:
            position = text.find(name)
        if position < 0:
            return 1
        cursors[line] = position + len(name)
        return position + 1

    # -- value derivation ---------------------------------------------------

    def _normalized_path(self, source: str) -> str:
        """Turn behave's feature filename into the contract's relative path.

        Args:
            source: behave's ``feature.filename``, e.g.
                ``"features/Crm.feature"``.

        Returns:
            A forward-slashed, repository-relative path with no ``file:``
            scheme.  Backslashes are normalised so a Windows worker emits the
            same URI as a POSIX one, and
            :func:`app.utils.paths.normalize_feature_uri` rewrites the Java
            layout's ``src/main/resources/features/`` prefix if it is ever seen,
            so the prefix has exactly one owner.
        """
        raw = str(source or "").replace("\\", "/")
        normalized = normalize_feature_uri(raw)
        if normalized.startswith(FILE_URI_SCHEME):
            return normalized[len(FILE_URI_SCHEME) :]
        return normalized

    def _feature_tags(self, feature: Any, source: str) -> list[JsonDict]:
        """Build a feature's long-shape tags.

        Args:
            feature: behave's feature.
            source: Path of the feature file, for column recovery.

        Returns:
            The tags in source order.  behave's tags are plain strings without
            the leading ``@`` and carry only a line, so the ``@`` is restored
            by :func:`feature_tag` and the column is recovered from the source.
        """
        cursors: dict[int, int] = {}
        tags: list[JsonDict] = []
        for tag in getattr(feature, "tags", None) or ():
            name = str(tag)
            name = name if name.startswith("@") else f"@{name}"
            line = int(getattr(tag, "line", 0) or 0)
            column = self._tag_column(source, line, name, cursors)
            tags.append(feature_tag(name, line, column))
        return tags

    def _scenario_tags(self, scenario: Any) -> list[JsonDict]:
        """Build a scenario's short-shape tags, with the feature's propagated.

        A feature-level tag propagates onto every scenario element: ``@Smoke``
        is declared once at ``Crm.feature:1`` and all four scenario elements
        carry it.  behave's ``scenario.effective_tags`` is the same set of
        names but it *is* a set, so its iteration order is not stable; the
        ordered union of the feature's tags followed by the scenario's own is
        used instead, which is both deterministic and the order Cucumber's own
        pickle carries.

        Args:
            scenario: behave's scenario.

        Returns:
            The tags, deduplicated first-occurrence-wins.  An empty list makes
            :func:`new_element` omit the key entirely.
        """
        names = list(self._feature_tag_names)
        seen = set(names)
        for tag in getattr(scenario, "tags", None) or ():
            name = str(tag)
            name = name if name.startswith("@") else f"@{name}"
            if name not in seen:
                seen.add(name)
                names.append(name)
        return [scenario_tag(name) for name in names]

    def _is_selected(self, scenario: Any) -> bool:
        """Report whether the effective tag expression selected ``scenario``.

        behave announces excluded scenarios to formatters, because
        ``show_skipped`` defaults to true, whereas the JVM never starts them and
        so never emits them.  Recording the answer here is what lets
        ``app/reporting/cucumber_json.py`` drop them.

        Args:
            scenario: behave's scenario.

        Returns:
            ``True`` when the scenario is selected, and ``True`` as well when
            behave cannot answer - over-reporting a scenario is recoverable,
            silently dropping a real result is not.
        """
        try:
            return bool(scenario.should_run(self.config))
        except Exception:
            logger.debug(
                "Selection state unavailable; assuming selected", exc_info=True
            )
            return True

    def _examples_name(self, scenario: Any, row_id: str) -> str | None:
        """Return the Examples block's name for an outline row scenario.

        The JVM slugs the *AST's* ``Examples.getName()`` into the row id, so
        the model is preferred over the annotation suffix behave writes into
        the scenario name.

        Args:
            scenario: behave's generated row scenario, whose ``parent`` is the
                originating scenario outline.
            row_id: behave's ``row.id``, of the form ``"<block>.<row>"``.

        Returns:
            The block's name, ``""`` for an unnamed block, or ``None`` when the
            model does not expose it.
        """
        examples = getattr(getattr(scenario, "parent", None), "examples", None)
        if not examples:
            return None
        block: int | None = None
        head = row_id.split(".", 1)[0] if row_id else ""
        if head.isdigit():
            block = int(head)
        if block is not None:
            for example in examples:
                if getattr(example, "index", None) == block:
                    return str(getattr(example, "name", "") or "")
            if 1 <= block <= len(examples):
                return str(getattr(examples[block - 1], "name", "") or "")
        return None

    def _element_identity(self, scenario: Any) -> tuple[str, str]:
        """Derive a scenario element's name and id.

        For a plain scenario both come straight from the model.  For an outline
        row, behave's name carries the annotation
        ``"{name} -- @{row.id} {examples.name}"`` while the JVM's element name
        is the plain outline name and its id gains two segments - the Examples
        block's slug and the row's position, counting the header row as 1.  The
        annotation is removed at the exact marker behave's own ``row.id``
        produces, which makes the removal precise rather than a guess, with a
        regular expression as the fallback.

        Args:
            scenario: behave's scenario.

        Returns:
            A ``(name, identifier)`` pair.
        """
        raw_name = str(getattr(scenario, "name", "") or "")
        row = getattr(scenario, "_row", None)
        if row is None:
            return raw_name, scenario_element_id(self._feature_name, raw_name)

        row_id = str(getattr(row, "id", "") or "")
        name = raw_name
        examples_from_name: str | None = None
        index_from_name: int | None = None
        marker = f"{_OUTLINE_ANNOTATION_PREFIX}{row_id} " if row_id else ""
        cut = raw_name.rfind(marker) if marker else -1
        if cut >= 0:
            name = raw_name[:cut]
            examples_from_name = raw_name[cut + len(marker) :]
        else:
            annotation = _OUTLINE_ANNOTATION_RE.search(raw_name)
            if annotation is not None:
                name = raw_name[: annotation.start()]
                examples_from_name = annotation.group("examples") or ""
                # With no row id on the model, the annotation is the only
                # remaining source of the row's position, and the id needs it.
                index_from_name = int(annotation.group("row"))
                if not row_id:
                    row_id = f"{annotation.group('block')}.{annotation.group('row')}"

        row_index = getattr(row, "index", None)
        if not isinstance(row_index, int):
            tail = row_id.split(".", 1)[-1] if "." in row_id else ""
            row_index = int(tail) if tail.isdigit() else index_from_name

        examples_name = self._examples_name(scenario, row_id)
        if examples_name is None:
            examples_name = examples_from_name or ""
        return name, scenario_element_id(
            self._feature_name, name, examples_name, row_index
        )


    # -- step matching and results ------------------------------------------

    def _build_arguments(
        self,
        step_name: str,
        arguments: Iterable[Any] | None,
    ) -> list[JsonDict]:
        """Build a step's ``match.arguments`` list.

        The JVM's ``createMatchMap`` iterates the definition's arguments and,
        for each one, records ``val`` and ``offset`` when the argument has a
        value and an **empty mapping** when it does not - the entry is never
        dropped.  ``val`` is the raw matched substring of the step text
        *including* its surrounding quotes, which is what
        :func:`widen_quoted_span` reproduces.

        Args:
            step_name: The step's substituted name, which the offsets index
                into.
            arguments: behave's ``Match.arguments``.

        Returns:
            The argument mappings, left to right.  An empty list makes
            :meth:`_apply_match` omit the key, matching the JVM's
            ``if (!getDefinitionArgument().isEmpty())``.
        """
        built: list[JsonDict] = []
        for argument in arguments or ():
            if getattr(argument, "value", None) is None:
                built.append({})
                continue
            start = getattr(argument, "start", None)
            end = getattr(argument, "end", None)
            value, offset = widen_quoted_span(
                step_name,
                start if isinstance(start, int) else -1,
                end if isinstance(end, int) else -1,
            )
            if not value:
                # The span does not index into the name - a converted argument
                # or a matcher that reports no span.  The matched text is still
                # known, so it is recorded with the best offset available
                # rather than dropped.
                value = str(getattr(argument, "original", "") or argument.value)
                offset = start if isinstance(start, int) and start >= 0 else 0
            built.append({"val": value, "offset": offset})
        return built

    def _apply_match(self, record: JsonDict, match: Any) -> None:
        """Record the outcome of resolving a step definition.

        Args:
            record: The step object to fill in.
            match: behave's ``Match`` for a resolved step, or ``NoMatch`` for
                an undefined one.  ``NoMatch`` carries ``func=None``, which is
                the only reliable discriminator, and yields ``match == {}`` -
                the JVM likewise writes no ``location`` for an undefined step.
        """
        func = getattr(match, "func", None)
        if func is None:
            record["matched"] = False
            record["match"] = {}
            return
        match_map: JsonDict = {"location": _dotted_path(func)}
        arguments = self._build_arguments(
            record.get("name", ""), getattr(match, "arguments", None)
        )
        if arguments:
            match_map["arguments"] = arguments
        record["matched"] = True
        record["match"] = match_map

    def _resolve_match_from_registry(self, record: JsonDict, step: Any) -> None:
        """Resolve a never-executed step's definition from behave's registry.

        The reference report carries ``match.location`` for steps that were
        *skipped* after an earlier failure, because the JVM matched every step
        of the test case before running any of it.  behave gives a formatter no
        ``match`` callback for a step it never executes, so the lookup is
        repeated here against the same global registry the runner uses.

        Args:
            record: The step object to fill in.
            step: behave's step object.
        """
        try:
            match = behave_step_registry.find_match(step)
        except Exception:
            logger.debug("Step registry lookup failed", exc_info=True)
            return
        if match is None:
            # Genuinely undefined: leave ``match`` empty, as the JVM does.
            return
        self._apply_match(record, match)

    def _build_result(self, step: Any) -> JsonDict:
        """Build a step's ``result`` mapping from behave's step object.

        Args:
            step: behave's step object, which carries ``status``, ``duration``
                and ``error_message``.

        Returns:
            ``status`` and ``duration`` always, and ``error_message`` only when
            there is one.  The duration is nanoseconds and may legitimately be
            ``0``; the writers decide whether to emit the key.
        """
        result: JsonDict = {
            "status": _status_name(getattr(step, "status", None)),
            "duration": nanos_from_seconds(getattr(step, "duration", None)),
        }
        message = getattr(step, "error_message", None)
        if message:
            result["error_message"] = _normalize_newlines(str(message))
        return result

    def _finish_scenario(self) -> None:
        """Complete the current scenario's steps and clear the scenario state.

        Called before each new scenario, at :meth:`eof` and again at
        :meth:`close`, so a scenario is finalised exactly once whichever event
        follows it.  Two gaps in behave's formatter protocol are closed here,
        both measured: a step that never executed gets no ``result`` callback,
        and it gets no ``match`` callback either, yet the JVM reports both for
        such a step.  The outcome is read from behave's own step object and the
        definition from behave's step registry.
        """
        for record, step in self._step_records:
            if not record["result"]:
                record["result"] = self._build_result(step)
            if not record["matched"] and not record["match"]:
                self._resolve_match_from_registry(record, step)
        self._reset_scenario_state()

    # -- attachments --------------------------------------------------------

    def add_attachment(
        self,
        embedding: JsonDict,
        hook_location: str | None = None,
    ) -> bool:
        """Attach an embedding to the current scenario's after-hook entry.

        Args:
            embedding: A mapping with ``mime_type``, ``data`` and optionally
                ``name``, as ``app/reporting/screenshots.py`` builds it.
            hook_location: Dotted path of the recording hook; defaults to
                :data:`DEFAULT_AFTER_HOOK_LOCATION`.  Embeddings recorded under
                the same location share one hook entry, which is how the JVM
                groups several attachments from one hook.

        Returns:
            ``True`` when it was recorded, ``False`` when no scenario is
            current - in which case there is nothing to attach it to and the
            attachment is dropped rather than invented into the document.
            Never raises: failure evidence must not change a test outcome.
        """
        try:
            if self._scenario_element is None:
                return False
            location = hook_location or DEFAULT_AFTER_HOOK_LOCATION
            hooks = self._scenario_element.setdefault("after", [])
            entry = next(
                (
                    candidate
                    for candidate in hooks
                    if candidate.get("match", {}).get("location") == location
                ),
                None,
            )
            if entry is None:
                entry = new_hook_entry(location=location)
                hooks.append(entry)
            entry.setdefault("embeddings", []).append(dict(embedding))
            return True
        except Exception:
            logger.exception(
                "An attachment could not be recorded; the scenario's result is "
                "unaffected"
            )
            return False

    # -- behave formatter protocol ------------------------------------------

    @_guarded
    def uri(self, uri: str) -> None:
        """Remember the feature file about to be processed.

        Args:
            uri: behave's feature filename, relative to the working directory.
        """
        self._current_source = str(uri or "")

    @_guarded
    def feature(self, feature: Any) -> None:
        """Start a feature object and append it to the document.

        Args:
            feature: behave's feature.  Its ``background`` is remembered here
                as well as in :meth:`background`, so a Background occurrence is
                still emitted if behave ever announces the feature without it.
        """
        self._finish_scenario()
        source = self._current_source or str(getattr(feature, "filename", "") or "")
        self._reset_feature_state()

        name = str(getattr(feature, "name", "") or "")
        path = self._normalized_path(source)
        tags = self._feature_tags(feature, source)
        self._feature = new_feature(
            uri=f"{FILE_URI_SCHEME}{path}",
            path=path,
            identifier=convert_to_id(name),
            line=int(getattr(feature, "line", 0) or 0),
            name=name,
            description=self._description_text(source, feature),
            keyword=str(getattr(feature, "keyword", "") or FEATURE_KEYWORD),
            tags=tags,
        )
        self._feature_name = name
        self._feature_source = source
        self._feature_tag_names = [tag["name"] for tag in tags]
        self._background_model = getattr(feature, "background", None)
        self.result_set["features"].append(self._feature)

    @_guarded
    def background(self, background: Any) -> None:
        """Remember the feature's Background definition.

        behave announces it once per feature, not once per scenario, so no
        element is created here: :meth:`scenario` appends one occurrence before
        every scenario, which is what the JVM emits.

        Args:
            background: behave's background.
        """
        self._background_model = background

    @_guarded
    def scenario(self, scenario: Any) -> None:
        """Append the Background occurrence and then the scenario element.

        Args:
            scenario: behave's scenario - for an outline, one already-built row
                scenario per Examples row.
        """
        self._finish_scenario()
        if self._feature is None:
            logger.warning(
                "Scenario %r announced before any feature; it was not recorded",
                getattr(scenario, "name", None),
            )
            return

        source = self._feature_source
        selected = self._is_selected(scenario)
        name, identifier = self._element_identity(scenario)

        background = getattr(scenario, "background", None) or self._background_model
        if background is not None:
            # One fresh occurrence per scenario, appended first.  The JVM emits
            # it whenever the feature has a background, even when it has no
            # steps - EmployeeFc.feature's background is exactly that case.
            self._background_element = new_element(
                element_type=ELEMENT_TYPE_BACKGROUND,
                keyword=str(getattr(background, "keyword", "") or BACKGROUND_KEYWORD),
                line=int(getattr(background, "line", 0) or 0),
                name=str(getattr(background, "name", "") or ""),
                description=self._description_text(source, background),
                selected=selected,
            )
            self._feature["elements"].append(self._background_element)
            try:
                self._background_step_count = len(scenario.background_steps)
            except Exception:
                logger.debug("Background step count unavailable", exc_info=True)
                self._background_step_count = 0

        self._scenario_element = new_element(
            element_type=ELEMENT_TYPE_SCENARIO,
            keyword=str(getattr(scenario, "keyword", "") or ""),
            line=int(getattr(scenario, "line", 0) or 0),
            name=name,
            description=self._description_text(source, scenario),
            selected=selected,
            identifier=identifier,
            start_timestamp=format_timestamp(self.clock()),
            tags=self._scenario_tags(scenario),
        )
        self._feature["elements"].append(self._scenario_element)

    @_guarded
    def step(self, step: Any) -> None:
        """Record a step, in the element it belongs to.

        behave announces every step of the scenario in one flat sequence,
        background steps first, so the first ``len(scenario.background_steps)``
        announcements land in the Background occurrence and the rest in the
        scenario - the switch happens at the first non-background step.

        Args:
            step: behave's step object, kept by reference so that a step which
                never executes can still be finalised from its own status.
        """
        target = self._scenario_element
        if (
            self._background_element is not None
            and self._announced_steps < self._background_step_count
        ):
            target = self._background_element
        if target is None:
            logger.warning(
                "Step %r announced outside a scenario; it was not recorded",
                getattr(step, "name", None),
            )
            return

        record = new_step(
            keyword=getattr(step, "keyword", ""),
            line=int(getattr(step, "line", 0) or 0),
            name=str(getattr(step, "name", "") or ""),
        )
        target["steps"].append(record)
        self._step_records.append((record, step))
        self._records_by_step_id[id(step)] = record
        self._announced_steps += 1

    @_guarded
    def match(self, match: Any) -> None:
        """Buffer the step definition behave just resolved.

        The callback carries no reference to the step it belongs to, and it is
        always followed immediately by :meth:`result` for that same step, so it
        is buffered here and applied there.  Buffering rather than tracking a
        position is deliberate: under ``--dry-run`` behave emits the pair only
        for steps it could match, so a positional cursor would attribute a
        match to the wrong step as soon as one step is undefined.

        Args:
            match: behave's ``Match``, or ``NoMatch`` for an undefined step.
        """
        self._pending_match = match

    @_guarded
    def result(self, step: Any) -> None:
        """Record a step's outcome, and the match buffered for it.

        Args:
            step: behave's step object - in behave 1.3.3 this callback receives
                the step, not a separate result object, and the step carries
                ``status``, ``duration``, ``error_message`` and ``exception``.
        """
        record = self._records_by_step_id.get(id(step))
        if record is None:
            # Identity lookup failed, which means this step was never
            # announced.  Fall back to the first step still awaiting an
            # outcome, so a result is recorded rather than lost.
            record = next(
                (
                    candidate
                    for candidate, _ in self._step_records
                    if not candidate["result"]
                ),
                None,
            )
        if record is None:
            logger.warning(
                "Result for unannounced step %r was discarded",
                getattr(step, "name", None),
            )
            self._pending_match = None
            return

        if self._pending_match is not None:
            self._apply_match(record, self._pending_match)
            self._pending_match = None
        record["result"] = self._build_result(step)

    @_guarded
    def embedding(self, mime_type: str, data: Any) -> None:
        """Record an attachment made through behave's ``context.attach()``.

        This is behave's own embedding protocol: ``Context.attach(mime_type,
        data)`` forwards to every formatter that defines this method, which is
        how ``features/environment.py`` gets a failure screenshot into the
        document without importing this module.  The attachment is named after
        the current scenario, reproducing ``Hooks.java:15``'s
        ``scenario.attach(screenshot, "image/png", scenario.getName())``.

        Args:
            mime_type: The attachment's MIME type.
            data: The attachment payload - raw bytes, which are base64-encoded
                here, or a string, which is taken to be base64 already, as
                ``app/reporting/screenshots.py`` returns it.
        """
        if isinstance(data, (bytes, bytearray, memoryview)):
            encoded = base64.b64encode(bytes(data)).decode("ascii")
        else:
            encoded = str(data)
        embedding: JsonDict = {
            "mime_type": str(mime_type or "application/octet-stream"),
            "data": encoded,
        }
        if self._scenario_element is not None:
            name = self._scenario_element.get("name")
            if name:
                embedding["name"] = name
        self.add_attachment(embedding)

    @_guarded
    def eof(self) -> None:
        """Finish the feature file: finalise its last scenario, clear state."""
        self._finish_scenario()
        self._reset_feature_state()

    def close(self) -> None:
        """Finalise the document, write it, and close the stream.

        This is the one method that must always produce a well-formed document,
        so each stage is guarded separately: a failure while finalising a
        scenario still leaves the features collected so far, and a failure
        while stamping the run-level fields still leaves them at their
        defaults.  Nothing raises out of here - behave calls it at the very end
        of a run, and an exception would fail a suite that had already passed.
        """
        if self._closed:
            return
        self._closed = True
        try:
            self._finish_scenario()
        except Exception:
            logger.exception("Finalising the last scenario failed")
        try:
            self.result_set["started_at"] = self._earliest_start_timestamp()
            self.result_set["generated_at"] = format_timestamp(self.clock())
        except Exception:
            logger.exception("Stamping the result document failed")
        try:
            self._write_document()
        except Exception:
            logger.exception("Writing the result document failed")
        finally:
            try:
                _ACTIVE_COLLECTORS.remove(self)
            except ValueError:
                # Already de-registered: nothing to undo, and this is the last
                # chance to release the reference, so the absence is fine.
                logger.debug("Collector was already de-registered")
            try:
                self.close_stream()
            except Exception:
                logger.debug("Closing the output stream failed", exc_info=True)

    # -- output -------------------------------------------------------------

    def _earliest_start_timestamp(self) -> str | None:
        """Return the earliest scenario ``start_timestamp`` in the document.

        Returns:
            The earliest timestamp, or ``None`` when no scenario was announced.
            The comparison is lexicographic, which is chronological for this
            fixed-width UTC format.  Every announced scenario counts, including
            one the tag expression excluded: behave announces it at the point
            the run reached it, so the earliest value is when the run started -
            which is what a report's metadata block means by it.
        """
        stamps = [
            element["start_timestamp"]
            for _, element in iter_scenarios(self.result_set)
            if element.get("start_timestamp")
        ]
        return min(stamps) if stamps else None

    def _write_document(self) -> None:
        """Serialise the document to the formatter's stream.

        The stream is opened by the constructor; it is re-opened here only if
        something closed it early.  A stream whose encoding cannot represent
        the document - possible when the process locale is ASCII and a
        scenario name or failure message is not - is retried with escaped
        non-ASCII rather than left without a document at all.
        """
        stream = self.stream or self.open()
        text = _serialize(self.result_set)
        try:
            stream.write(f"{text}\n")
        except UnicodeEncodeError:
            logger.warning(
                "The output stream cannot encode the result document; "
                "non-ASCII characters were escaped"
            )
            stream.write(
                json.dumps(
                    self.result_set,
                    default=str,
                    ensure_ascii=True,
                    indent=_JSON_DUMP_KWARGS["indent"],
                    sort_keys=False,
                )
                + "\n"
            )
        flush = getattr(stream, "flush", None)
        if callable(flush):
            flush()



# --------------------------------------------------------------------------- #
# Load, merge and traversal primitives.
#
# The schema is defined here, so reading it back and merging it belong here
# too: one owner, one set of rules, no writer that has to guess.
# --------------------------------------------------------------------------- #


def iter_scenarios(result_set: ResultSet) -> Iterator[tuple[JsonDict, JsonDict]]:
    """Yield every ``(feature, scenario element)`` pair in the document.

    Backgrounds are skipped, because a Background occurrence is not a test
    case: it has no id, no timestamp and no tags, and every consumer that wants
    one wants it as part of its scenario.  Used by all four writers, by the
    services and by the HTTP report routes' data preparation.

    Args:
        result_set: The document.

    Yields:
        ``(feature, element)`` in document order - features in source order,
        elements in the order the collector or :func:`merge_result_sets` put
        them, so the position of a scenario is stable and can be used as a key
        (which is what the report routes do, since two features may share an
        ``id``).
    """
    for feature in result_set.get("features") or ():
        if not isinstance(feature, dict):
            continue
        for element in feature.get("elements") or ():
            if isinstance(element, dict) and element.get("type") == (
                ELEMENT_TYPE_SCENARIO
            ):
                yield feature, element


def dump_result_set(result_set: ResultSet, path: Path | str) -> Path:
    """Write ``result_set`` to ``path`` as UTF-8 JSON.

    Args:
        result_set: The document to write.
        path: Destination file.  Its parent directory is created through
            :func:`app.utils.paths.ensure_parent`, so a worker never fails
            merely because ``target/`` was emptied by the ``--clean`` step.
            The path itself always comes from :mod:`app.utils.paths` - this
            module contains no path literal.

    Returns:
        The path written.

    Raises:
        OSError: If the file or its parent directory cannot be created or
            written.  Deliberately not swallowed: producing an artifact is the
            caller's contract with the exit table, and a silent failure would
            leave the merge reading a file that is not there.
    """
    destination = ensure_parent(path)
    # newline="\n" so a document written on Windows is byte-identical to one
    # written on Linux: durations and timestamps vary by construction, the
    # document's structure must not.
    with open(destination, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(f"{_serialize(result_set)}\n")
    return destination


def _coerce_result_set(document: Any, source: str) -> ResultSet:
    """Validate a parsed document and fill in absent run-level keys.

    Args:
        document: The parsed JSON value.
        source: Where it came from, for the error message.

    Returns:
        The document, with every run-level key present so that consumers never
        need a membership test.  Feature data is never invented or repaired:
        only the run-level envelope is completed, which is what lets a
        hand-written fixture omit boilerplate.

    Raises:
        ResultSetError: If the value is not an object, or its ``features`` is
            not a list.
    """
    if not isinstance(document, dict):
        raise ResultSetError(
            f"{source}: expected a JSON object, found {type(document).__name__}"
        )
    features = document.get("features", [])
    if not isinstance(features, list):
        raise ResultSetError(
            f"{source}: 'features' must be a list, found {type(features).__name__}"
        )

    version = document.get("schema_version")
    if version is not None and version != SCHEMA_VERSION:
        logger.warning(
            "%s declares result schema version %r; this build reads version %d",
            source,
            version,
            SCHEMA_VERSION,
        )

    document.setdefault("schema_version", SCHEMA_VERSION)
    document.setdefault("started_at", None)
    document.setdefault("generated_at", None)
    document.setdefault("dry_run", False)
    document.setdefault("tag_expression", None)
    document.setdefault("metadata", {})
    document["features"] = features
    return document


def load_result_set(path: Path | str) -> ResultSet:
    """Read one result document back from disk.

    Args:
        path: The file to read - a per-worker intermediate from
            :func:`app.utils.paths.worker_result_path`, or a fixture.

    Returns:
        The document, with its run-level envelope completed by
        :func:`_coerce_result_set`.

    Raises:
        ResultSetError: If the file is absent, unreadable, not valid JSON, or
            not this schema.  One exception type for every failure is the
            point: ``app/services/test_run_service.py`` names the offending
            shard on stderr and applies the plan's exit table, and it should
            not have to distinguish an :class:`OSError` from a
            :class:`json.JSONDecodeError` to do so.  The cause is always
            chained.
    """
    source = str(path)
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as error:
        raise ResultSetError(f"{source}: cannot be read ({error})") from error
    try:
        document = json.loads(text)
    except ValueError as error:
        raise ResultSetError(f"{source}: is not valid JSON ({error})") from error
    return _coerce_result_set(document, source)


def _element_units(elements: Sequence[JsonDict]) -> list[list[JsonDict]]:
    """Group elements into Background-plus-scenario units.

    A Background occurrence belongs immediately before the scenario it was
    emitted for, and the two must never be separated by a sort.  Grouping them
    first is what makes ordering by the scenario's line correct - ordering the
    flat list by ``line`` would collect every background at the front, because
    all of a feature's occurrences share the Background's own line.

    Args:
        elements: A feature's elements, in collection order.

    Returns:
        The units, in input order.  A unit is ``[background, scenario]``,
        ``[scenario]`` for a feature without a background, or ``[background]``
        for the pathological case of a trailing occurrence with no scenario,
        which is kept rather than dropped.
    """
    units: list[list[JsonDict]] = []
    for element in elements:
        if not isinstance(element, dict):
            continue
        if element.get("type") == ELEMENT_TYPE_BACKGROUND:
            units.append([element])
            continue
        if units and len(units[-1]) == 1 and units[-1][0].get("type") == (
            ELEMENT_TYPE_BACKGROUND
        ):
            units[-1].append(element)
        else:
            units.append([element])
    return units


def _unit_sort_key(unit: Sequence[JsonDict]) -> int:
    """Return the line a unit sorts by.

    Args:
        unit: A unit from :func:`_element_units`.

    Returns:
        The scenario's line when the unit has one, otherwise the background's,
        so a unit sorts by the position of the test case it represents.
    """
    for element in unit:
        if element.get("type") == ELEMENT_TYPE_SCENARIO:
            return int(element.get("line") or 0)
    return int(unit[0].get("line") or 0) if unit else 0


def _feature_key(feature: JsonDict) -> str:
    """Return the identity a feature is merged on.

    Args:
        feature: A feature object.

    Returns:
        Its ``path``, falling back to its ``uri`` and then its ``name``.  The
        ``id`` is deliberately **not** used: two features with the same title
        share an ``id`` - ``Contact``/``Inventory`` and ``Login``/``Notes``
        each do - and merging on it would fuse two distinct features into one.
        That collision is source behaviour the port preserves.
    """
    for key in ("path", "uri", "name"):
        value = feature.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _min_timestamp(values: Iterable[Any]) -> str | None:
    """Return the earliest of some timestamp strings.

    Args:
        values: Candidate values, which may include ``None`` and non-strings.

    Returns:
        The lexicographically smallest non-empty string, which for this
        fixed-width UTC format is the earliest instant, or ``None``.
    """
    stamps = [value for value in values if isinstance(value, str) and value]
    return min(stamps) if stamps else None


def merge_result_sets(sets: Iterable[ResultSet]) -> ResultSet:
    """Merge per-worker documents into the one document the writers consume.

    The rules are the plan's: *one file per worker, merged by feature path,
    features in source order*.

    * **Grouped by path.** A feature sharded across two workers yields exactly
      one feature object, whose elements are the union of the shards'.  Two
      features that merely share an ``id`` remain two objects - see
      :func:`_feature_key`.
    * **Features in source order**, reproduced as ascending feature path.  That
      is behave's own discovery order (it sorts directory entries), and it is
      the only ordering that is independent of how the scenarios were sharded:
      ordering by first appearance across the inputs would make the merged
      document depend on the worker count, which the plan forbids.
    * **Elements by test-case line**, with each Background occurrence kept
      immediately in front of its scenario, and ties left in input order by a
      stable sort.
    * **Nothing else is unioned.** ``started_at`` is the earliest non-null
      value, ``generated_at`` the latest, ``dry_run`` true if any shard ran
      dry, and ``tag_expression`` and ``metadata`` the first non-empty - all
      shards share a configuration, so these agree in practice and the rules
      only settle the pathological case.

    Args:
        sets: The documents to merge, in any order.

    Returns:
        A new document.  The inputs are never mutated: elements are deep-copied
        into the result, so a writer that annotates what it reads cannot reach
        back into a shard.  The function is pure - no clock, no randomness, no
        filesystem, no set iteration - so for a fixed set of shard inputs the
        merged structure is identical whatever the worker count, which is
        precisely what ``tests/test_test_run_service.py`` asserts.  An empty
        input yields an empty document rather than an error; deciding that a
        run produced no results at all belongs to the caller and its exit
        table.
    """
    documents = [document for document in sets if isinstance(document, dict)]

    merged_features: dict[str, JsonDict] = {}
    for document in documents:
        version = document.get("schema_version")
        if version is not None and version != SCHEMA_VERSION:
            logger.warning(
                "Merging a result set of schema version %r into version %d",
                version,
                SCHEMA_VERSION,
            )
        for feature in document.get("features") or ():
            if not isinstance(feature, dict):
                continue
            key = _feature_key(feature)
            elements = [
                copy.deepcopy(element)
                for element in feature.get("elements") or ()
                if isinstance(element, dict)
            ]
            existing = merged_features.get(key)
            if existing is None:
                merged = copy.deepcopy(feature)
                merged["elements"] = elements
                merged_features[key] = merged
            else:
                existing["elements"].extend(elements)

    ordered_features: list[JsonDict] = []
    for key in sorted(merged_features):
        feature = merged_features[key]
        units = sorted(_element_units(feature["elements"]), key=_unit_sort_key)
        feature["elements"] = [element for unit in units for element in unit]
        ordered_features.append(feature)

    started_at = _min_timestamp(document.get("started_at") for document in documents)
    if started_at is None:
        # A hand-built shard may carry scenarios without a run-level stamp.
        started_at = _min_timestamp(
            element.get("start_timestamp")
            for _, element in iter_scenarios({"features": ordered_features})
        )

    generated_stamps = [
        document.get("generated_at")
        for document in documents
        if isinstance(document.get("generated_at"), str) and document["generated_at"]
    ]
    tag_expression = next(
        (
            document["tag_expression"]
            for document in documents
            if document.get("tag_expression")
        ),
        None,
    )
    metadata = next(
        (document["metadata"] for document in documents if document.get("metadata")),
        None,
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "started_at": started_at,
        # The latest shard's stamp, rather than a fresh reading: this function
        # must not touch a clock, or it could not be verified deterministic.
        "generated_at": max(generated_stamps) if generated_stamps else None,
        "dry_run": any(bool(document.get("dry_run")) for document in documents),
        "tag_expression": tag_expression,
        "metadata": copy.deepcopy(metadata) if metadata else {},
        "features": ordered_features,
    }

