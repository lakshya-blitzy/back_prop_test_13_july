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
      "complete": True,                # did the collector observe the whole
                                       # run?  absent means True
      "collection_errors": [           # what it failed to observe; absent
        {"event": "result",            # means []
         "error": "TypeError: ..."},
      ],
      "features": [ <feature>, ... ],  # source order
    }

The four ``metadata`` keys and their sub-keys are fixed vocabulary:
``app/templates/artifact/metadata.html`` renders exactly those names.  A probe
that returns nothing yields ``""`` -- a metadata lookup never fails a run.

``complete`` and ``collection_errors`` are the collector's own integrity
statement, and they are the reason a shard can be *named* rather than silently
trusted.  A formatter hook that fails is still swallowed -- an exception raised
into behave takes the worker down mid-run and loses every result it had
collected -- but the failure is now recorded: the event's name and the
exception's text land in ``collection_errors`` and ``complete`` becomes
``False``.  :func:`load_result_set` refuses such a document, so
``app/services/test_run_service.py`` marks that shard dead with the reason
attached instead of publishing valid-looking partial output as a complete run.
Both keys are **optional** on input -- absent ``complete`` means ``True`` and
absent ``collection_errors`` means ``[]`` -- which is what lets the hand-built
``tests/fixtures/sample_results.json`` keep loading unchanged, and is why
adding them did not bump :data:`SCHEMA_VERSION`.

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
      "result": {"status": "passed", "duration": 412000000,
                 "error_message": "..."},   # only when the hook failed
      "embeddings": [{"mime_type": "image/png", "data": "<base64>",
                      "name": "<scenario name>"}],
    }

A hook entry exists only when there is something real to record: an attachment
arrived, behave's scenario model shows a hook or context-cleanup failure, or a
caller reported an outcome through :func:`record_hook_result`.  A silently
passing hook contributes **no** entry, because ``cucumber_json._build_after``
emits only entries that carry an embedding and the reference report carries no
``after`` array at all -- inventing entries would change a frozen artifact.
``result.status`` keeps behave's own vocabulary (``passed``, ``hook_error``,
``cleanup_error``); folding it into Cucumber's narrower set belongs to the
writers, and ``cucumber_json``'s ``STATUS_ALIASES`` already maps both error
names to ``failed``.  ``result.duration`` is a measured nanosecond interval,
not a placeholder zero; what it measures is stated on
:meth:`ResultCollectorFormatter._measured_hook_duration`, because behave
exposes no hook duration of its own.

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

Reading a document back
-----------------------
:func:`load_result_set` is the **only** gate between a worker's file and the
merge, so it validates the whole schema rather than its envelope:
:func:`_validate_result_set` walks every documented level -- run, feature,
element, step, ``match``, ``match.arguments``, ``result``, hook entry,
``embeddings`` and both tag shapes -- and raises :class:`ResultSetError`
naming a JSON-pointer-style path such as
``features[3].elements[2].steps[1].result.status``.  Nothing downstream
re-validates: ``app/services/test_run_service.py`` treats a
:class:`ResultSetError` as *this shard is dead*, and
:func:`merge_result_sets` is written so that it cannot raise on a malformed
hand-built document either -- including one whose nesting defeats
:func:`copy.deepcopy`, which is skipped, recorded and marked incomplete
rather than propagated.

The validation is **exact**, not merely type-checking: a document this build
will merge is a document this build's own builders could have written.  Every
key :func:`new_feature`, :func:`new_element`, :func:`new_step` and
:func:`new_hook_entry` always emit is required to be present, ``result.status``
must be a member of :data:`RESULT_STATUSES`, and an **unknown key is refused
at every level**.  The rule is not pedantry: a step object of ``{}`` used to
be accepted as a step whose fields had defaulted, when what it really is is a
step whose keyword, text, match and outcome were lost -- and each writer would
have invented a different replacement for them while the parent published the
run as complete.  The two exceptions are the ones the schema itself has: a
scenario's ``tags`` (omitted when it has none), and ``metadata``, whose
sub-vocabulary is probe-driven and is therefore validated as a bounded nested
structure of mappings and strings instead of against a key list.

The same function is where a hostile or corrupt worker file stops being able
to exhaust the parent process (CWE-400).  A real shard sits orders of
magnitude below every limit in the block of ``MAX_*`` constants below -- the
whole suite collected into one shard is ten features, 87 scenarios, 469 steps,
about 311 KB of JSON, and one ~1 MB screenshot per failed scenario -- so a
breach means the file is not a shard this build produced, and the read is
refused rather than completed.  Three mechanisms enforce that, and they answer
different questions:

* **the file cap**, :data:`MAX_RESULT_FILE_BYTES`, which is the *aggregate*
  bound on the whole file and the only limit applied before parsing;
* **the generic budget**, :func:`_check_budget`, which bounds the parsed
  document's nesting depth, total node count and longest string *whatever the
  key* -- the hole that unknown keys used to ride through -- and walks
  iteratively, because a recursive walk would raise the very
  :class:`RecursionError` it exists to prevent;
* **the per-field limits**, which bound each individual collection and string
  the schema names.

The read itself uses **one descriptor**: :func:`os.open` with ``O_NOFOLLOW``
and ``O_NONBLOCK``, :func:`os.fstat` on that descriptor, a regular-file
requirement, the size check, and then a read of at most one byte more than the
cap.  ``stat()``-then-``read_text()`` was a time-of-check/time-of-use pair
that also followed symlinks and read without a bound.

Boundaries
----------
* The only intra-package import is :mod:`app.utils.paths` (the plan's ``RP -->
  UT`` edge).  Nothing in ``app/reporting`` imports a service; the services
  import this module.
* Flask, ``app.config``, ``app.automation``, ``app.web``, ``app.pages`` and
  Selenium are **not** imported, so this module is usable inside a worker
  process that never builds a Flask application.
* No path literal appears here: every path comes from :mod:`app.utils.paths`.
* Nothing is deleted.  Emptying the build-output directory and tearing down
  the per-worker intermediate directory belong to ``app/cli.py``.
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
import stat
import time
import traceback
from collections.abc import Callable, Iterable, Iterator, Sequence
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import Any, Final

import behave
from behave.formatter.base import Formatter
from behave.step_registry import registry as behave_step_registry

try:
    # The status vocabulary the validator accepts is behave's own enum rather
    # than a list maintained here, because the schema documents
    # ``result.status`` as "behave's normalised status name" and a second list
    # would drift from it on an engine upgrade.  The import is guarded because
    # a missing or moved enum must not stop this module from importing inside a
    # worker: :data:`_FALLBACK_STATUS_NAMES` then supplies the same vocabulary
    # as a literal, measured against behave 1.3.3.
    from behave.model_core import Status as _BehaveStatus
except ImportError:  # pragma: no cover - behave 1.3.3 always provides it
    _BehaveStatus = None  # type: ignore[assignment]

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
#: optional key is not such a change, which is why ``complete`` and
#: ``collection_errors`` arrived without a bump.  :func:`load_result_set`
#: **rejects** any other version: a document this build cannot read in full is
#: a dead shard the parent must name, not a warning it can continue past,
#: because continuing past it loses results silently.
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
    "Result collector hook %s failed; the affected event was skipped, the "
    "shard document is marked incomplete and the run continues"
)

#: Logged at WARNING when a scenario's finalised after-hook result is not
#: ``passed``.  The outcome is recorded in the document either way, but a
#: recorded failure nobody sees during the run is a failure discovered hours
#: later in an artifact, so it is also announced on stderr.  The arguments are
#: the scenario's *safe* source identity, the status and the hook's dotted
#: location -- never a scenario or step name and never the failure body, since
#: ``Login.feature``'s Examples substitute plaintext credentials into names and
#: an error body can carry an arbitrary page dump.
_HOOK_RESULT_FAILURE_MESSAGE: Final[str] = (
    "The after-hook of the scenario at %r reported status %r from %r; the "
    "outcome is recorded on the scenario's hook entry"
)

#: Logged at ERROR when :func:`merge_result_sets` cannot copy a feature out of
#: an input document.  The argument is the feature's source identity - its
#: ``path`` or ``uri``, both of which are written in the repository and carry
#: no runtime data - for the same reason as above.
_MERGE_FEATURE_FAILURE_MESSAGE: Final[str] = (
    "Merging the feature %s failed; it is omitted from the merged document, "
    "which is marked incomplete"
)

#: Logged at ERROR when the run metadata of an input document cannot be copied
#: into the merged one.  Carries no argument: the block is machine-generated
#: probe output and naming its contents would say nothing useful.
_MERGE_METADATA_FAILURE_MESSAGE: Final[str] = (
    "Copying the run metadata into the merged document failed; the merged "
    "document carries none and is marked incomplete"
)

#: behave 1.3.3's status names, as a literal, used only if the guarded import
#: of ``behave.model_core.Status`` above failed.  Written out rather than
#: shortened because the fallback's whole purpose is to be exactly as wide as
#: the enum: a vocabulary narrower than the engine's would reject a document
#: the collector legitimately wrote.
_FALLBACK_STATUS_NAMES: Final[tuple[str, ...]] = (
    "unknown",
    "untested",
    "executing",
    "skipped",
    "passed",
    "xfailed",
    "xpassed",
    "failed",
    "error",
    "hook_error",
    "cleanup_error",
    "undefined",
    "pending",
    "pending_warn",
    "untested_pending",
    "untested_undefined",
)

#: Status names that are not behave's but may legitimately appear in a
#: hand-built document: ``ambiguous`` is a Cucumber status
#: (``cucumber_json.CUCUMBER_STATUSES`` carries it) with no behave counterpart,
#: so a fixture written against the JVM contract can use it.
_EXTRA_STATUS_NAMES: Final[tuple[str, ...]] = ("ambiguous",)


def _build_status_vocabulary() -> frozenset[str]:
    """Build the set of ``result.status`` values the validator accepts.

    Written as a function rather than an expression so the derivation is
    readable and stated once: both the enum member's ``name`` and its
    ``normalized_name`` are taken, because behave's own folding
    (``pending_warn`` and ``untested_pending`` to ``pending``,
    ``untested_undefined`` to ``undefined``) means a document may carry either
    spelling depending on which attribute produced it - :func:`_status_name`
    prefers the normalised one, and ``cucumber_json.STATUS_ALIASES`` folds the
    raw ones.

    Returns:
        The vocabulary, frozen so no caller can widen it at runtime.  Derived
        from :class:`behave.model_core.Status` when that import succeeded and
        from :data:`_FALLBACK_STATUS_NAMES` when it did not, unioned with
        :data:`_EXTRA_STATUS_NAMES` either way.
    """
    names: list[str] = list(_EXTRA_STATUS_NAMES)
    if _BehaveStatus is None:  # pragma: no cover - the import above succeeds
        names.extend(_FALLBACK_STATUS_NAMES)
        return frozenset(names)
    for member in _BehaveStatus:
        for attribute in ("name", "normalized_name"):
            value = getattr(member, attribute, None)
            if isinstance(value, str) and value:
                names.append(value)
    return frozenset(names)


#: The vocabulary ``result.status`` is validated against, at both the step and
#: the hook level.  A status outside it is refused rather than passed through:
#: every writer maps this field by name, and an unknown name is silently
#: mis-rendered rather than reported - ``cucumber_json`` would fold it to
#: ``untested`` and publish a step that ran as one that did not.  Exposed as a
#: module constant so that the vocabulary has exactly one definition to assert
#: against.
RESULT_STATUSES: Final[frozenset[str]] = _build_status_vocabulary()

#: Translation table that makes a worker-controlled string safe to put in a
#: log line or a parent-facing exception message (CWE-117).  Every C0 control,
#: ``DEL`` and every C1 control becomes a visible escape, so an embedded ``\n``
#: cannot forge two extra log records and an embedded ``ESC`` cannot reach a
#: terminal as an escape sequence.  The three familiar ones keep their readable
#: spellings, which is why they are re-stated after the ranges.
_CONTROL_ESCAPES: Final[dict[int, str]] = {
    **{code: f"\\x{code:02x}" for code in range(0x00, 0x20)},
    **{code: f"\\x{code:02x}" for code in range(0x7F, 0xA0)},
    ord("\t"): "\\t",
    ord("\n"): "\\n",
    ord("\r"): "\\r",
}

# --------------------------------------------------------------------------- #
# Resource limits for reading a worker document back (CWE-400).
#
# A worker file is written by this module and read by the parent process, so
# the parent must survive a file that is *not* what this module writes -- a
# worker corrupted mid-write, a leftover file from another tool, or a
# deliberately hostile one.  Every limit below is derived from the suite's own
# measured size and left above it by a stated factor, so no honest shard can
# breach one and a breach therefore means "this is not a shard this build
# produced": :func:`load_result_set` refuses it by raising
# :class:`ResultSetError` naming both the limit and the offending path.
#
# The limits fall into two groups that answer different questions, and reading
# them as competing numbers is a mistake:
#
#   * :data:`MAX_RESULT_FILE_BYTES` is the **aggregate** bound.  It is the one
#     limit applied before anything is parsed, and it bounds the whole file --
#     every scenario, every step and every screenshot in it, added together.
#   * every other limit bounds an **individual** allocation or an individual
#     structure: one collection's length, one string's length, the document's
#     nesting depth, its total node count.  Each is much smaller than the file
#     cap, which is exactly right: a legitimate 120 MB shard is 120 MB because
#     it carries eighty-seven ~1.4 MB screenshots, not because any one of its
#     values is large.
#
# The suite's measured size, which every number below is derived from: ten
# feature files; eighty-seven selectable scenarios; four hundred and
# sixty-nine steps including Background repetitions; and one screenshot of
# roughly 1 MB per failed scenario.  The whole suite collected into one shard
# measures about 311 KB of JSON with no embeddings and just under fourteen
# thousand JSON nodes.
# --------------------------------------------------------------------------- #

#: Largest worker file that will be read at all, checked with :func:`os.fstat`
#: on the one descriptor the read uses, before a single byte is decoded, so
#: that an oversized file costs one ``fstat`` rather than its own size in
#: parent memory.
#:
#: The arithmetic, in full, because an under-sized cap refuses honest work:
#: the worst legitimate case is one sequential ``--workers 1`` run in which
#: *every* scenario fails, so every scenario carries a screenshot.  That is
#: 87 scenarios x 1 MiB of PNG = 87 MiB of image bytes; base64 expands by four
#: thirds, giving 116 MiB; plus the ~311 KB of structural JSON and its quoting
#: overhead, call it ~120 MB in one file.  256 MiB is therefore roughly twice
#: the largest legitimate single-worker shard -- and a sharded run divides that
#: total across its workers, so every shard of a parallel run is smaller still.
MAX_RESULT_FILE_BYTES: Final[int] = 256 * 1024 * 1024

#: Flags :func:`load_result_set` opens a shard with.  Assembled here so the
#: reasoning sits with the constant rather than inside the read:
#:
#: * ``O_RDONLY`` because nothing in this module writes through this path;
#: * ``O_NOFOLLOW`` so a symlink left where a shard should be is refused with
#:   ``ELOOP`` instead of followed to whatever it points at - the worker
#:   directory is a place the parent trusts, and a link into it is not
#:   something this build ever creates;
#: * ``O_NONBLOCK`` so a FIFO left in a shard's place cannot park the merge
#:   forever inside ``open()`` waiting for a writer.  It has no effect on a
#:   regular file, which is the only thing this function goes on to read;
#: * ``O_BINARY`` on Windows, where the default text mode would translate line
#:   endings underneath a byte count the size check just established.
#:
#: Each optional flag is read with :func:`getattr` because Windows defines
#: neither of the first two and POSIX defines no ``O_BINARY``; a missing flag
#: contributes ``0``, which changes nothing.
_RESULT_FILE_OPEN_FLAGS: Final[int] = (
    os.O_RDONLY
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_NONBLOCK", 0)
    | getattr(os, "O_BINARY", 0)
)

#: Read size per :func:`os.read` call.  One mebibyte keeps the number of
#: system calls small for a shard carrying screenshots while bounding the
#: transient allocation of each read, and the accumulated total is bounded by
#: :data:`MAX_RESULT_FILE_BYTES` regardless.
_READ_CHUNK_BYTES: Final[int] = 1024 * 1024

#: Deepest nesting accepted anywhere in the parsed document, enforced by the
#: generic budget pass in :func:`_check_budget` before any schema rule is
#: applied.  The schema's own deepest legitimate path is eleven levels -- run,
#: ``features``, feature, ``elements``, element, ``steps``, step, ``match``,
#: ``arguments``, argument, ``val`` -- so 32 is about three times the deepest
#: honest nesting while staying far below CPython's recursion limit, which is
#: what a value nested deeply enough to matter would otherwise exhaust inside
#: :func:`copy.deepcopy` during the merge.
MAX_DOCUMENT_DEPTH: Final[int] = 32

#: Most JSON nodes accepted in one document, counting every mapping, list,
#: scalar and object key.  The whole suite collected into a single shard
#: measures 13,981 nodes, so 250,000 is about eighteen times the largest
#: legitimate shard.  The cap exists because the per-collection limits bound
#: each list separately and their *product* does not: a file of small tokens
#: within every other limit could still cost the parent a multi-million-node
#: walk.  Counted as nodes are pushed rather than visited, which bounds the
#: walk's own stack by the same number.
MAX_DOCUMENT_NODES: Final[int] = 250_000

#: Largest ``features`` list accepted.  The suite has ten; a shard has at most
#: ten.
MAX_FEATURES: Final[int] = 1000

#: Largest ``elements`` list accepted per feature.  The largest feature
#: contributes at most two elements per scenario (its Background occurrence and
#: the scenario), so tens in practice.
MAX_ELEMENTS_PER_FEATURE: Final[int] = 10_000

#: Largest ``steps`` list accepted per element.  The longest scenario in the
#: suite has well under fifty.
MAX_STEPS_PER_ELEMENT: Final[int] = 1000

#: Largest ``after`` list accepted per element.  One teardown hook runs per
#: scenario, so the honest value is one.
MAX_HOOKS_PER_ELEMENT: Final[int] = 100

#: Largest ``embeddings`` list accepted per hook entry.  One screenshot is
#: captured per failed scenario, so the honest value is one.
MAX_EMBEDDINGS_PER_HOOK: Final[int] = 100

#: Largest ``tags`` list accepted at either level.  No feature or scenario in
#: the suite declares more than one tag.
MAX_TAGS_PER_LEVEL: Final[int] = 100

#: Largest ``match.arguments`` list accepted per step.  The most heavily
#: parameterised step in the suite takes three.
MAX_ARGUMENTS_PER_STEP: Final[int] = 100

#: Longest single string value accepted anywhere except an embedding's
#: ``data``.  The longest honest string is a failure message carrying a Python
#: traceback -- kilobytes, not megabytes.
MAX_STRING_LENGTH: Final[int] = 1024 * 1024

#: Longest embedding ``data`` accepted.  A 1 MB PNG is about 1.4 MB of
#: base64, so 16 MiB leaves room for a full-page screenshot of a very large
#: viewport while still bounding the parent's memory per attachment.
MAX_EMBEDDING_DATA_LENGTH: Final[int] = 16 * 1024 * 1024

#: Longest string accepted anywhere by the generic budget pass in
#: :func:`_check_budget`, which runs before any schema rule and therefore
#: cannot know which field it is looking at.  It is deliberately defined as
#: the *widest* of the per-field string limits -- an embedding's base64
#: ``data`` is the one field in the schema legitimately measured in megabytes
#: -- so that the generic pass can never refuse a value the schema allows.
#: Every string the schema names is then checked again against its own,
#: tighter limit: :data:`MAX_STRING_LENGTH` for all of them except that
#: ``data``.  Written as a reference rather than a second literal so the two
#: cannot drift apart.
MAX_ANY_STRING_LENGTH: Final[int] = MAX_EMBEDDING_DATA_LENGTH

#: Deepest nesting accepted inside the run-level ``metadata`` block, and the
#: most entries it may carry in total.  ``metadata`` is the one level whose
#: sub-vocabulary is not fixed -- it is whatever :func:`run_metadata`'s probes
#: yielded -- so it is validated as a bounded nested structure of mappings and
#: strings instead of against a key list.  The real block is four groups of at
#: most two string entries, so eight entries nested two levels deep; the
#: limits leave room for a probe block that grows without letting an unbounded
#: one through.
MAX_METADATA_DEPTH: Final[int] = 4
MAX_METADATA_ENTRIES: Final[int] = 100

#: Largest ``collection_errors`` list the collector records and the load path
#: accepts.  The list is diagnostic: the first failures are the informative
#: ones, and a document that reports any at all is refused, so a cap costs no
#: information and bounds a pathological run that fails on every event.
MAX_COLLECTION_ERRORS: Final[int] = 100

#: How many recorded collection errors a rejection message quotes before it
#: says how many more there are.  A stderr line has to stay readable to be
#: diagnostic, and the file itself carries the whole list.
_REJECTION_REASONS_SHOWN: Final[int] = 3

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

#: behave's prefix on a failed assertion's message.  ``Step._process_error``
#: (``behave/model.py:1886-1921``) formats an :class:`AssertionError` as
#: ``"ASSERT FAILED: {e}"``, and the measured text of a real failing scenario
#: in this suite is exactly ``"ASSERT FAILED: The title is not same as the
#: expected!"``.  The contract wants the assertion's own message, so the prefix
#: is removed -- see :func:`_failure_text`.
_ASSERT_FAILED_PREFIX: Final[str] = "ASSERT FAILED: "

#: behave's prefix on any other exception's message, from the same function:
#: ``"ERROR: {e_classname}: {e}"``, or ``"ERROR: {e_classname}"`` alone when
#: the exception carries no args.  The class name is kept as the message when
#: it is all there is, because a bare ``"ERROR: "`` says nothing.
_ERROR_PREFIX_RE: Final[re.Pattern[str]] = re.compile(
    r"^ERROR: (?P<classname>[A-Za-z_][A-Za-z0-9_.]*)(?:: |$)"
)

#: behave's hook-failure text, from ``runner.py:794-796``:
#: ``"HOOK-ERROR in {hook_name}{extra}: {error_text}"``, stored on the
#: *scenario* model.  The hook's name is the one thing the message carries that
#: the model does not, so it is read back out to name the hook entry's
#: location -- see :func:`_hook_location_from_message`.
_HOOK_ERROR_RE: Final[re.Pattern[str]] = re.compile(
    r"HOOK-ERROR in (?P<hook>[A-Za-z_][A-Za-z0-9_]*)"
)

#: Longest ``collection_errors`` ``error`` text recorded.  An exception's
#: ``str()`` can be a whole Selenium page dump; the entry exists to name what
#: went wrong, and the file it lands in is refused anyway, so it is truncated
#: rather than allowed to dominate the document.
_COLLECTION_ERROR_TEXT_LIMIT: Final[int] = 500

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

    **An unnamed ``Examples:`` block contributes an empty segment**, so its
    rows' ids carry a doubled separator -- ``<feature>;<outline>;;2``.  That is
    deliberate and it is the JVM's own output, not an artefact of
    ``convert_to_id(None) == ""`` leaking through: the clean Cucumber-JVM
    baseline generated by the AAP 0.3.4 procedure (reference revision,
    ``dryRun`` runner, no tag filter, ``mvn -B -o clean test``, 87 scenarios
    over all ten features) emits
    ``testinium-app-inventory-feature;verify-that-the-user-can-create-a-new-contact;;2``
    for ``Contact.feature``'s unnamed blocks, and
    ``tests/fixtures/sample_results.json`` pins the same ``;;2``/``;;3`` shape
    for ``Sales.feature``'s.  The JSON report is machine-read, so
    collapsing the separator or substituting a placeholder would break the
    artifact's identity against both authorities; the branch below is written
    out by name, with the ids pinned in the examples, precisely so that the
    empty segment reads as a decision rather than an oversight.

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

        An unnamed ``Examples:`` block, first and second data rows, exactly as
        the clean JVM baseline emits them for ``Contact.feature``:

        >>> scenario_element_id("Testinium app Inventory feature",
        ...                     "Verify that the user can create a new contact",
        ...                     None, 1)
        'testinium-app-inventory-feature;verify-that-the-user-can-create-a-new-contact;;2'
        >>> scenario_element_id("Testinium app Inventory feature",
        ...                     "Verify that the user can create a new contact",
        ...                     "", 2)
        'testinium-app-inventory-feature;verify-that-the-user-can-create-a-new-contact;;3'
    """
    parts = [convert_to_id(feature_name), convert_to_id(scenario_name)]
    if row_index is not None:
        # The Examples segment, then the row's position.  For an unnamed block
        # the segment is the empty string -- the JVM's ``convertToId("")`` --
        # and it is appended rather than skipped, because skipping it would
        # emit ``...;2`` where every authority for this artifact emits
        # ``...;;2``.  See the note above for the three that were checked.
        examples_segment = convert_to_id(examples_name)
        parts.append(examples_segment)
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
    error_message: str | None = None,
    embeddings: Sequence[JsonDict] | None = None,
) -> JsonDict:
    """Build an after-hook entry for a scenario's ``after`` list.

    This is the port of the JVM's hook step map, which is where a screenshot
    lands: ``Hooks.java:11-18`` captures on failure and attaches, and the JSON
    generator hangs the attachment off the hook rather than off a step.

    The defaults describe an entry whose outcome is *not yet known*:
    :meth:`ResultCollectorFormatter._finalize_hook_entries` overwrites
    ``status`` and ``duration`` with the scenario model's real ones when the
    scenario is finalised, which is the only point at which behave has run the
    after-hooks.  A caller that already knows the outcome -- a lifecycle hook
    reporting through :func:`record_hook_result`, or a hand-built fixture --
    passes it here instead.

    Args:
        location: Dotted path of the hook function.  ``None`` yields an empty
            ``match``, mirroring the JVM's omission of ``location`` when it has
            none.
        status: The hook's own outcome, in behave's vocabulary: ``passed``,
            ``hook_error`` or ``cleanup_error``.  The writers own the folding
            into Cucumber's narrower set.
        duration: The hook's duration in nanoseconds.
        error_message: The hook's failure text, already in the canonical
            message-then-traceback shape.  ``None`` or ``""`` omits the key,
            exactly as a step's ``result`` omits it when the step passed.
        embeddings: Attachment mappings, each with ``mime_type``, ``data`` and
            optionally ``name``, exactly as ``app/reporting/screenshots.py``
            produces them.

    Returns:
        The hook entry.
    """
    result: JsonDict = {"status": status, "duration": int(duration)}
    if error_message:
        result["error_message"] = _normalize_newlines(str(error_message))
    return {
        "match": {"location": location} if location else {},
        "result": result,
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
    complete: bool = True,
    collection_errors: Sequence[JsonDict] | None = None,
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
        complete: Whether the collector observed the whole run.  ``True`` by
            default, because a document nobody reported a collection failure
            against is complete by construction; the collector sets it to
            ``False`` the moment it drops an event, and
            :func:`load_result_set` then refuses the document so the parent can
            name that shard dead.
        collection_errors: What the collector failed to observe -- one
            ``{"event": ..., "error": ...}`` mapping per dropped event, capped
            at :data:`MAX_COLLECTION_ERRORS`.  Empty by default.
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
        "complete": bool(complete),
        "collection_errors": [
            dict(entry) for entry in (collection_errors or ())
        ][:MAX_COLLECTION_ERRORS],
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


def _log_safe_text(value: Any, limit: int = _COLLECTION_ERROR_TEXT_LIMIT) -> str:
    """Make a value safe to put in a log line or a parent-facing message.

    The single owner of that escaping (CWE-117).  Every fragment this module
    interpolates into a :class:`ResultSetError` message, a ``logger`` call or a
    ``collection_errors`` entry passes through here, because those fragments
    are *worker-controlled*: a shard's recorded event name, its recorded error
    text, an unknown key a hostile document carries, a status value it invented.
    ``app/services/test_run_service.py`` logs the rejection message, so a
    fragment carrying a bare ``LF`` would become three physical log records --
    two of them forged and attributable to nothing - and one carrying ``ESC``
    would reach a terminal as an escape sequence.  Both are prevented here
    rather than at each of the call sites, so there is one rule and one place
    to change it.

    The raw detail is not lost by this: it stays in the shard file, where a
    human can read it with its real newlines, and only the *summary* that
    reaches a parent process is escaped.  ``result.error_message`` is
    deliberately **not** routed through here - it keeps real newlines, because
    both HTML writers render it in a monospace block - and
    :func:`_normalize_newlines` remains its owner.

    Args:
        value: The fragment.  Any type: it is converted with :func:`str`
            first, defensively, because a ``__str__`` that raises must not
            cost a diagnostic.
        limit: Longest result in characters, before an ellipsis is appended.
            Defaults to :data:`_COLLECTION_ERROR_TEXT_LIMIT`, the bound this
            module already applied to recorded failure text, so that a single
            fragment cannot dominate a log line whatever its source.

    Returns:
        The text with every C0 control, ``DEL`` and C1 control replaced by a
        readable escape (``\\n``, ``\\t``, ``\\x1b``) and truncated to
        ``limit`` characters with a trailing ``...``.  Escaping rather than
        deleting is deliberate: a message that says ``\\n`` where the data had
        a newline is still diagnosable, whereas one with the character removed
        silently changes what the worker reported.  Never raises.
    """
    try:
        text = value if isinstance(value, str) else str(value)
    except Exception:  # pragma: no cover - a __str__ that raises
        text = f"<unprintable {type(value).__name__}>"
    text = text.translate(_CONTROL_ESCAPES)
    if len(text) > limit:
        text = f"{text[:limit]}..."
    return text


def _describe_exception(error: BaseException | str) -> str:
    """Describe a failure in one line, for a log or a ``collection_errors`` entry.

    Args:
        error: The exception to describe, or an already-formatted description
            for a failure that has no exception object -- a dropped event, for
            instance, which is a decision rather than a raise.

    Returns:
        ``"<ExcType>: <message>"``, or ``"<ExcType>"`` when the exception
        carries no message, put through :func:`_log_safe_text` - so it is one
        line, carries no control character that could forge a log record or
        reach a terminal, and is truncated to
        :data:`_COLLECTION_ERROR_TEXT_LIMIT` characters with an ellipsis.  An
        exception's text is worker-controlled (a Selenium page dump, a
        message built from a step argument), and this text is what lands in
        ``collection_errors`` and is quoted back to the parent by
        :func:`_describe_collection_errors`, which is why the escaping is
        applied at the source rather than at each reader.  A string argument
        is returned escaped and truncated, unchanged otherwise.  Never raises:
        a ``__str__`` that itself fails yields the class name alone.
    """
    if isinstance(error, str):
        text = error
    else:
        classname = type(error).__name__
        try:
            message = str(error)
        except Exception:  # pragma: no cover - a __str__ that raises
            message = ""
        text = f"{classname}: {message}" if message else classname
    return _log_safe_text(text)


def _failure_text(
    exception: BaseException | None,
    traceback_object: TracebackType | None = None,
    error_message: Any = None,
) -> str:
    """Build the contract's failure text for a step or a hook.

    The required shape is the AAP's (§0.6: *"the assertion's own message text
    ... followed by the Python traceback, with line endings normalized to
    ``\\n``"*) and it is byte-pinned in ``tests/fixtures/sample_results.json``:
    ``str(exception)``, then one ``\\n``, then the full standard Python
    traceback as :func:`traceback.format_exception` renders it -- so the text
    ends with the traceback's own newline and begins with the exception's own
    message, which for a Selenium error is ``"Message: no such element: ..."``
    rather than a class name.

    This is the single owner of that shape.  behave's own ``error_message`` is
    *not* it: ``Step._process_error`` prefixes an assertion with
    ``"ASSERT FAILED: "`` and any other exception with ``"ERROR: <Class>: "``,
    and it attaches a traceback only under ``--verbose`` -- measured on a real
    failing scenario, whose text was exactly
    ``"ASSERT FAILED: The title is not same as the expected!"`` with no
    traceback at all.  ``store_exception_context`` always stores the exception
    and its traceback on the model, so the exception is the source of record
    and behave's string is only a fallback.

    Args:
        exception: The exception behave stored on the step or scenario model,
            or ``None`` when it stored none.
        traceback_object: The traceback behave stored alongside it
            (``exc_traceback``).  ``None`` falls back to the exception's own
            ``__traceback__``, and with no traceback at all the message is
            returned alone rather than padded with an empty one.
        error_message: behave's own ``error_message``, used only when there is
            no exception object.  Its measured prefixes are stripped -- and
            nothing else is: a ``"HOOK-ERROR in after_scenario: ..."`` text
            survives verbatim, because it is the only record of which hook
            failed.

    Returns:
        The failure text with LF line endings, or ``""`` when there is nothing
        to report.  Never raises: failure *reporting* must not be able to fail
        a run, so a traceback that cannot be formatted degrades to the message
        alone.
    """
    try:
        if exception is not None:
            head = ""
            try:
                head = str(exception)
            except Exception:  # pragma: no cover - a __str__ that raises
                head = ""
            if not head:
                # An exception with empty args - ``KeyboardInterrupt``, say -
                # stringifies to "", which would leave the text starting with
                # a bare newline.  Its class name is the only message there is.
                head = type(exception).__name__
            frames = traceback_object or getattr(exception, "__traceback__", None)
            if frames is None:
                return _normalize_newlines(head)
            try:
                rendered = "".join(
                    traceback.format_exception(type(exception), exception, frames)
                )
            except Exception:  # pragma: no cover - malformed traceback object
                logger.debug("A traceback could not be formatted", exc_info=True)
                return _normalize_newlines(head)
            return _normalize_newlines(f"{head}\n{rendered}")

        if not error_message:
            return ""
        text = _normalize_newlines(str(error_message))
        if text.startswith(_ASSERT_FAILED_PREFIX):
            return text[len(_ASSERT_FAILED_PREFIX) :]
        prefix = _ERROR_PREFIX_RE.match(text)
        if prefix is not None:
            remainder = text[prefix.end() :]
            # ``"ERROR: <Class>"`` with nothing after it is behave's shape for
            # an exception with no args; the class name is then the message.
            return remainder or prefix.group("classname")
        return text
    except Exception:  # pragma: no cover - defence in depth
        logger.debug("Failure text could not be built", exc_info=True)
        return ""


def _hook_location_from_message(error_message: Any) -> str:
    """Name the hook a behave hook-failure message blames.

    behave records a hook failure on the *scenario* as
    ``"HOOK-ERROR in <hook_name>[(tag=...)]: <text>"`` (``runner.py:794-796``),
    which is the only place the failing hook's name appears -- the model
    carries ``hook_failed`` but not which hook set it.  The name is turned into
    a dotted location by substituting it for the last segment of
    :data:`DEFAULT_AFTER_HOOK_LOCATION`, so ``before_scenario`` is reported as
    the hook it was rather than mislabelled as the teardown, and no path or
    module literal is introduced.

    Args:
        error_message: behave's ``scenario.error_message``, or ``None``.

    Returns:
        The dotted hook location, or :data:`DEFAULT_AFTER_HOOK_LOCATION` when
        the message names no hook -- the teardown hook is where the port's
        ``Hooks.teardownScenario`` lives and is the only hook that produces an
        attachment, so it is the right default.
    """
    if not error_message:
        return DEFAULT_AFTER_HOOK_LOCATION
    found = _HOOK_ERROR_RE.search(str(error_message))
    if found is None:
        return DEFAULT_AFTER_HOOK_LOCATION
    module, _, _ = DEFAULT_AFTER_HOOK_LOCATION.rpartition(".")
    hook = found.group("hook")
    return f"{module}.{hook}" if module else hook


def _source_identity(path: str, line: Any, keyword: Any = None) -> str:
    """Identify a scenario or step by its **source coordinates only**.

    Every diagnostic in this module that has to name a scenario or a step uses
    this, and none of them uses a name.  A scenario or step *name* is the
    substituted Gherkin text, and ``Login.feature``'s Examples table
    substitutes plaintext usernames and passwords into it, so a WARNING record
    carrying a name would publish credentials into whatever collects the
    engine's stderr.  A feature path, a line number and a Gherkin keyword are
    all written in the feature file and carry no runtime data, which makes them
    safe to log and enough to find the step.

    Args:
        path: The feature file's repository-relative path.
        line: The one-based line, or anything non-numeric when it is unknown.
        keyword: The Gherkin keyword (``"When"``, ``"Scenario"``), or ``None``.
            Included parenthesised when present, because the line alone does
            not say what kind of statement it is.

    Returns:
        ``"features/Login.feature:31 (When)"``, ``"features/Login.feature:31"``
        without a keyword, or ``"<unknown source>"`` when there is no path and
        no line at all.  The result is always a plain string, so the caller can
        log it with ``%r`` and have control characters escaped for it.
    """
    location = str(path or "").strip()
    try:
        numeric = int(line)
    except (TypeError, ValueError):
        numeric = 0
    if numeric > 0:
        location = f"{location}:{numeric}" if location else f"line {numeric}"
    if not location:
        location = "<unknown source>"
    text = str(keyword or "").strip()
    return f"{location} ({text})" if text else location


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


def _record_collector_error(
    collector: Any,
    event: str,
    error: BaseException | str,
) -> None:
    """Record a collection failure on ``collector``, whatever state it is in.

    The indirection exists because :func:`_guarded` decorates *methods* and
    therefore runs with whatever ``self`` behave passed - including a
    half-constructed instance, if the failure happened inside the constructor's
    own call chain, or an object that is not a collector at all if the
    decorator is ever reused.  Losing the record of a failure because recording
    it failed would defeat the whole point, so the lookup is by attribute and
    every outcome is tolerated.

    Args:
        collector: The formatter instance the failing hook was called on.
        event: Name of the event or stage that failed.
        error: The exception, or a description for a failure that was a
            decision rather than a raise.
    """
    try:
        recorder = getattr(collector, "_record_collection_error", None)
        if callable(recorder):
            recorder(event, error)
            return
    except Exception:
        logger.debug(
            "A collection error could not be recorded on the document",
            exc_info=True,
        )
        return
    logger.debug(
        "Collection failure in %r could not be attributed to a result document",
        event,
    )


def _guarded(method: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a formatter hook so that it can never raise into behave.

    A formatter exception propagates out of the model's run loop and takes the
    worker down mid-run, turning a green suite into a non-zero exit and losing
    every result the worker had collected.  Every hook body is therefore
    guarded: an unexpected shape is logged with a traceback and that single
    event is skipped, leaving the rest of the run - and the document
    :meth:`ResultCollectorFormatter.close` writes - intact.

    Swallowing the exception is **not** the same as hiding it, and the
    difference is what makes the swallow safe.  A skipped event means the
    document no longer describes the whole run: a scenario, a step or a result
    is missing from it.  So the failure is recorded on the document itself -
    ``collection_errors`` gains the event's name and the exception's text, and
    ``complete`` becomes ``False`` - and :func:`load_result_set` refuses a
    document that says so.  ``app/services/test_run_service.py`` then names
    that shard dead, with the recorded reason, instead of publishing partial
    output as a complete run.

    Args:
        method: The hook method to wrap.

    Returns:
        The wrapped method, which returns ``None`` if the body failed.
    """

    @functools.wraps(method)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return method(self, *args, **kwargs)
        except Exception as error:
            logger.exception(HOOK_FAILURE_MESSAGE, method.__name__)
            _record_collector_error(self, method.__name__, error)
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


def record_hook_result(
    *,
    location: str | None = None,
    status: str | None = None,
    duration: int | None = None,
    error_message: str | None = None,
    embeddings: Sequence[JsonDict] | None = None,
) -> bool:
    """Record a hook's own outcome on the scenario currently being collected.

    The companion of :func:`attach_to_current_scenario`, and the authoritative
    seam for the scenario lifecycle in ``features/environment.py``, which the
    plan's dependency graph keeps free of any import from this package: it can
    report what only it knows - the teardown hook's own duration, or a failure
    it handled rather than re-raising - without importing this module's class
    or touching the document.

    Without a call, nothing is lost and nothing is invented:
    :meth:`ResultCollectorFormatter._finalize_hook_entries` still records a
    hook failure behave itself saw, still measures the window containing the
    after-hooks, and still emits no entry at all for a hook that passed
    silently and attached nothing.  This function only lets a caller that
    knows better say so.

    Args:
        location: Dotted path of the hook; defaults to
            :data:`DEFAULT_AFTER_HOOK_LOCATION`.
        status: The hook's outcome in behave's vocabulary (``passed``,
            ``hook_error``, ``cleanup_error``), or ``None`` to leave it to the
            scenario model.
        duration: The hook's duration in nanoseconds, or ``None`` to leave the
            measured window standing.
        error_message: The hook's failure text, or ``None``.
        embeddings: Attachment mappings to record under the same hook entry,
            exactly as ``screenshots.build_embedding`` returns them.

    Returns:
        ``True`` when a collector accepted it, ``False`` when no collector is
        active or no scenario is current.  As with an attachment, ``False`` is
        not an error: the suite may be running under a different formatter, and
        recording a hook result must never change a test outcome.
    """
    accepted = False
    for collector in reversed(_ACTIVE_COLLECTORS):
        if collector.record_hook_result(
            location=location,
            status=status,
            duration=duration,
            error_message=error_message,
            embeddings=embeddings,
        ):
            accepted = True
    if not accepted:
        logger.debug(
            "No active result collector accepted a hook result; it was dropped"
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

    #: Monotonic nanosecond source used for the one duration behave does not
    #: report: an after-hook's.  Separate from :attr:`clock` because the two
    #: measure different things - a wall-clock instant that goes into the
    #: document as text, and an interval that must not move if the system
    #: clock is stepped mid-run.  Overridable on the class or the instance for
    #: the same reason :attr:`clock` is.
    monotonic: Callable[[], int] = staticmethod(time.monotonic_ns)

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
        """Clear all per-scenario state, including the step bookkeeping.

        ``_scenario_model``, ``_hook_window_start`` and
        ``_reported_hook_results`` are part of that state: they exist to
        finalise the scenario's after-hook entry, and
        :meth:`_finalize_hook_entries` has already consumed them by the time
        this runs.
        """
        self._scenario_element: JsonDict | None = None
        self._scenario_model: Any | None = None
        self._background_element: JsonDict | None = None
        self._background_step_count: int = 0
        self._announced_steps: int = 0
        self._step_records: list[tuple[JsonDict, Any]] = []
        self._records_by_step_id: dict[int, JsonDict] = {}
        self._pending_match: Any | None = None
        self._hook_window_start: int | None = None
        self._reported_hook_results: dict[str, JsonDict] = {}

    # -- collector integrity ------------------------------------------------

    def _record_collection_error(
        self,
        event: str,
        error: BaseException | str,
    ) -> None:
        """Mark the document incomplete and record why.

        This is the whole of the port's answer to *"a formatter may not raise,
        but a report may not lie either"*.  Anything that loses an event - a
        guarded hook that failed, a scenario announced with no feature open, a
        step announced with no element current, a result that matched no
        announced step, or a failed stage of :meth:`close` - lands here, and
        the document it writes then carries ``complete: false`` and the reason.
        :func:`load_result_set` refuses such a document, which is how the
        parent process comes to name the shard dead rather than merging valid
        partial JSON as if it were a complete run.

        Args:
            event: Name of the event, hook or stage that lost data.
            error: The exception that caused it, or a description when the loss
                was a decision rather than a raise.

        Note:
            Never raises, and never fails to mark the document even if
            recording the detail fails: ``complete`` is set first, and the
            list append - which is what a cap or a strange ``result_set``
            could refuse - happens afterwards.  The list is capped at
            :data:`MAX_COLLECTION_ERRORS` entries; beyond it the document is
            already refused and the cap only bounds a run that fails on every
            single event.
        """
        try:
            document = getattr(self, "result_set", None)
            if not isinstance(document, dict):
                logger.debug(
                    "Collection failure in %r had no document to mark", event
                )
                return
            document["complete"] = False
            errors = document.get("collection_errors")
            if not isinstance(errors, list):
                errors = []
                document["collection_errors"] = errors
            if len(errors) < MAX_COLLECTION_ERRORS:
                errors.append(
                    {"event": str(event), "error": _describe_exception(error)}
                )
        except Exception:  # pragma: no cover - defence in depth
            logger.debug(
                "Recording a collection failure in %r failed", event, exc_info=True
            )

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

    def _safe_identity(self, entity: Any, *, with_keyword: bool = True) -> str:
        """Identify a behave scenario or step for a log line, safely.

        behave's ``BasicStatement`` - the base of both ``Scenario`` and
        ``Step`` - carries ``filename``, ``line`` and ``keyword``, all three
        read straight from the feature file.  Its ``name``, by contrast, is the
        *substituted* Gherkin text, and ``Login.feature``'s Examples table
        substitutes plaintext credentials into it, so no diagnostic in this
        class logs a name; see :func:`_source_identity`.

        Args:
            entity: behave's scenario or step.
            with_keyword: Whether to include the Gherkin keyword, which
                distinguishes a ``When`` from a ``Then`` on an adjacent line.

        Returns:
            An identity such as ``"features/Login.feature:31 (When)"``, falling
            back to the feature file currently being read when the model
            carries no filename of its own, and to ``"<unknown source>"`` when
            nothing is available.  Never raises: this runs on the path where
            something has already gone wrong.
        """
        try:
            filename = str(getattr(entity, "filename", "") or "")
            if not filename:
                filename = str(getattr(self, "_current_source", "") or "")
            keyword = getattr(entity, "keyword", None) if with_keyword else None
            return _source_identity(
                self._normalized_path(filename),
                getattr(entity, "line", None),
                keyword,
            )
        except Exception:  # pragma: no cover - defence in depth
            logger.debug("A source identity could not be built", exc_info=True)
            return _source_identity("", None)

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

        The failure text is **not** behave's ``error_message``.  behave
        formats an assertion as ``"ASSERT FAILED: <message>"`` and attaches a
        traceback only under ``--verbose``, whereas the contract wants the
        assertion's own message followed by the Python traceback; the step's
        ``exception`` and ``exc_traceback``, which behave's
        ``store_exception_context`` always stores, are what
        :func:`_failure_text` builds that from.  behave's string remains the
        fallback for the case where no exception object was stored.

        Args:
            step: behave's step object, which carries ``status``, ``duration``,
                ``error_message``, ``exception`` and ``exc_traceback``.

        Returns:
            ``status`` and ``duration`` always, and ``error_message`` only when
            there is one.  The duration is nanoseconds and may legitimately be
            ``0``; the writers decide whether to emit the key.
        """
        result: JsonDict = {
            "status": _status_name(getattr(step, "status", None)),
            "duration": nanos_from_seconds(getattr(step, "duration", None)),
        }
        message = _failure_text(
            getattr(step, "exception", None),
            getattr(step, "exc_traceback", None),
            getattr(step, "error_message", None),
        )
        if message:
            result["error_message"] = message
        return result

    def _finish_scenario(self) -> None:
        """Complete the current scenario's steps and clear the scenario state.

        Called before each new scenario, at :meth:`eof` and again at
        :meth:`close`, so a scenario is finalised exactly once whichever event
        follows it.  Three gaps in behave's formatter protocol are closed here,
        all measured: a step that never executed gets no ``result`` callback,
        it gets no ``match`` callback either - yet the JVM reports both for
        such a step - and a hook gets no callback at all.  The step outcome is
        read from behave's own step object, the definition from behave's step
        registry, and the hook outcome from the scenario model, which behave
        has finished mutating by the time this runs (see
        :meth:`_finalize_hook_entries`).
        """
        for record, step in self._step_records:
            if not record["result"]:
                record["result"] = self._build_result(step)
            if not record["matched"] and not record["match"]:
                self._resolve_match_from_registry(record, step)
        self._finalize_hook_entries()
        self._reset_scenario_state()

    # -- after-hook results -------------------------------------------------

    def _hook_outcome(self, scenario: Any) -> tuple[str, str]:
        """Read the current scenario's hook outcome off behave's model.

        behave has no formatter callback for a hook, so the model is the only
        source.  It is an exact one: ``runner.run_hook_with_capture`` sets
        ``scenario.hook_failed``, stores the exception with
        ``store_exception_context`` and writes
        ``"HOOK-ERROR in <hook>: ..."`` into ``scenario.error_message``
        (``runner.py:781-818``), and ``Scenario.run`` then sets
        ``Status.hook_error``, or ``Status.cleanup_error`` when it is the
        context cleanup that raised (``model.py:1241-1257``).

        Args:
            scenario: behave's scenario model for the scenario being finalised,
                or ``None`` when the collector never saw one.

        Returns:
            A ``(status, error_message)`` pair.  The status keeps behave's
            vocabulary - ``passed``, ``hook_error`` or ``cleanup_error`` - and
            the message is empty unless the hook failed.  A step failure is
            *not* a hook failure: ``failed`` and ``error`` on the scenario
            describe its steps, which already carry their own results, so this
            reports ``passed`` for them.
        """
        if scenario is None:
            return "passed", ""
        status = _status_name(getattr(scenario, "status", None))
        if status not in ("hook_error", "cleanup_error"):
            if not getattr(scenario, "hook_failed", False):
                return "passed", ""
            # behave recorded a hook failure without folding it into the
            # status - a failing ``before_scenario``, whose scenario is then
            # skipped.  The failure is real, so it is reported as one.
            status = "hook_error"
        message = _failure_text(
            getattr(scenario, "exception", None),
            getattr(scenario, "exc_traceback", None),
            getattr(scenario, "error_message", None),
        )
        return status, message

    def _hook_bookmark(self) -> int | None:
        """Take a monotonic bookmark for the after-hook window.

        Returns:
            The current monotonic nanosecond reading, or ``None`` when the
            source is unusable - in which case :meth:`_measured_hook_duration`
            reports ``0`` rather than a duration measured against nothing.
            Never raises: a clock is not worth a lost scenario.
        """
        try:
            return int(self.monotonic())
        except Exception:
            logger.debug(
                "The monotonic clock was unusable; hook durations will be 0",
                exc_info=True,
            )
            return None

    def _measured_hook_duration(self) -> int:
        """Return the measured duration of the window containing the after-hooks.

        behave reports no hook duration, so this is measured rather than read,
        and what it measures is stated plainly: the interval from the last
        step's ``result`` callback - or from the scenario's announcement, for a
        scenario with no steps - to the moment the scenario is finalised.
        behave emits ``formatter.scenario()`` *before* ``before_scenario``
        (``model.py:1160-1163``) and runs ``after_scenario``, ``after_tag`` and
        the context cleanup before the next scenario is announced
        (``model.py:1241-1257``), and finalisation happens on that next
        announcement, at ``eof`` or at ``close``.  So the window always
        *contains* the scenario's after-hooks, plus behave's own bookkeeping
        between them - it is an upper bound on the hook's own time, not a
        measurement of it, and it is recorded because a real bound is worth
        more downstream than a hard-coded zero.

        Returns:
            The interval in whole nanoseconds, or ``0`` when no bookmark was
            taken.  Never negative, and never raises - a monotonic source that
            misbehaves yields ``0`` rather than a nonsense duration.
        """
        start = self._hook_window_start
        if start is None:
            return 0
        try:
            elapsed = int(self.monotonic()) - int(start)
        except Exception:
            logger.debug("The monotonic clock was unusable", exc_info=True)
            return 0
        return elapsed if elapsed > 0 else 0

    def _finalize_hook_entries(self) -> None:
        """Give the current scenario's hook entries their real results.

        A hook entry is created only when there is something real to record -
        an attachment arrived, behave's model shows a hook or cleanup failure,
        or a caller reported an outcome through :meth:`record_hook_result` -
        and this is where the ``passed``/``0`` placeholders
        :func:`new_hook_entry` starts an entry with are replaced by the
        scenario's real status, real failure text and measured duration.  A
        silently passing hook that produced nothing still contributes **no**
        entry: ``cucumber_json._build_after`` emits only entries carrying an
        embedding and the reference report has no ``after`` array at all, so
        inventing one would change a frozen artifact.

        An explicitly reported value always wins over the measured one, because
        a lifecycle hook that timed itself knows better than this window does.

        A finalised status other than ``passed`` is also **announced**, once
        per scenario, at ``WARNING``.  Recording it in the document is not
        enough on its own: a teardown that failed is a scenario whose driver
        may not have been quit and whose screenshot may not have been taken,
        and a run that says nothing about it on stderr defers the discovery to
        whoever opens the artifact hours later.  What the record carries is
        deliberately narrow -- the scenario's *source* identity from
        :meth:`_safe_identity` (path, line and Gherkin keyword), the status,
        and the hook's dotted location.  Not the scenario's name, because
        ``Login.feature``'s Examples table substitutes plaintext credentials
        into it, and not the error body, which can be an arbitrary page dump;
        both are in the shard document, which is where a human reads them.

        Note:
            Never raises.  It runs inside :meth:`_finish_scenario`, which runs
            inside :meth:`close`, and a hook result is not worth a lost
            document; a failure here is recorded as a collection error like
            any other lost event.
        """
        try:
            element = self._scenario_element
            if element is None:
                return
            status, message = self._hook_outcome(self._scenario_model)
            entries = element.get("after")
            if not isinstance(entries, list):
                entries = []
                element["after"] = entries
            if status != "passed" and not entries:
                # A hook failed and left no attachment behind: the failure is
                # the thing worth recording, so the entry exists for it, named
                # after the hook behave blamed.
                entries.append(
                    new_hook_entry(
                        location=_hook_location_from_message(
                            getattr(self._scenario_model, "error_message", None)
                        )
                    )
                )
            if not entries:
                return
            measured = self._measured_hook_duration()
            # The first non-passing entry, kept for one WARNING after the
            # loop: a scenario has one teardown hook, so reporting per entry
            # would repeat the same failure once per attachment it left.
            announced: tuple[str, str] | None = None
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                match = entry.get("match")
                location = ""
                if isinstance(match, dict):
                    location = str(match.get("location") or "")
                reported = self._reported_hook_results.get(location, {})
                result = entry.get("result")
                if not isinstance(result, dict):
                    result = {}
                    entry["result"] = result
                result["status"] = reported.get("status") or status
                if announced is None and result["status"] != "passed":
                    announced = (
                        str(result["status"]),
                        location or DEFAULT_AFTER_HOOK_LOCATION,
                    )
                duration = reported.get("duration")
                result["duration"] = (
                    int(duration)
                    if isinstance(duration, int) and not isinstance(duration, bool)
                    else measured
                )
                text = reported.get("error_message") or message
                if text:
                    result["error_message"] = _normalize_newlines(str(text))
                else:
                    result.pop("error_message", None)
            if announced is not None:
                hook_status, hook_location = announced
                logger.warning(
                    _HOOK_RESULT_FAILURE_MESSAGE,
                    self._safe_identity(self._scenario_model),
                    hook_status,
                    _log_safe_text(hook_location),
                )
        except Exception as error:
            logger.exception("Finalising the scenario's hook results failed")
            self._record_collection_error("hook_result", error)

    # -- attachments --------------------------------------------------------

    def add_attachment(
        self,
        embedding: JsonDict,
        hook_location: str | None = None,
    ) -> bool:
        """Attach an embedding to the current scenario's after-hook entry.

        An arriving attachment is one of the three things that make a hook
        entry real, so the entry is created here if the location has none yet.
        Its ``status`` and ``duration`` are *not* decided here, though - they
        are placeholders until :meth:`_finalize_hook_entries` reads the
        scenario model's real outcome, which is the only correct moment,
        because behave has not run the after-hooks yet when an attachment made
        from inside one arrives.

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
            Never raises, and a ``False`` return does **not** mark the document
            incomplete: AAP deviation 19 makes suppressed failure evidence a
            tolerated outcome that must not change a test result, and a missing
            screenshot loses no scenario, step or status.
        """
        try:
            if self._scenario_element is None:
                return False
            location = hook_location or DEFAULT_AFTER_HOOK_LOCATION
            entry = self._hook_entry_for(location)
            entry.setdefault("embeddings", []).append(dict(embedding))
            return True
        except Exception:
            logger.exception(
                "An attachment could not be recorded; the scenario's result is "
                "unaffected"
            )
            return False

    def _hook_entry_for(self, location: str) -> JsonDict:
        """Return the current scenario's hook entry for ``location``, creating it.

        Args:
            location: Dotted path of the hook.  One entry exists per location,
                so several attachments from one hook - or an attachment and a
                reported result - share it, which is how the JVM groups them.

        Returns:
            The entry, already appended to the scenario's ``after`` list.

        Raises:
            AttributeError: If no scenario is current.  Both callers check
                first; the exception exists so that a future caller that
                forgets cannot silently invent an entry on nothing.
        """
        element = self._scenario_element
        if element is None:
            raise AttributeError("no scenario is current")
        hooks = element.setdefault("after", [])
        if not isinstance(hooks, list):
            hooks = []
            element["after"] = hooks
        for candidate in hooks:
            if not isinstance(candidate, dict):
                continue
            match = candidate.get("match")
            if isinstance(match, dict) and match.get("location") == location:
                return candidate
        entry = new_hook_entry(location=location)
        hooks.append(entry)
        return entry

    def record_hook_result(
        self,
        *,
        location: str | None = None,
        status: str | None = None,
        duration: int | None = None,
        error_message: str | None = None,
        embeddings: Sequence[JsonDict] | None = None,
    ) -> bool:
        """Record a hook's own outcome on the current scenario.

        The seam for a lifecycle producer that knows something this collector
        cannot observe: a hook's real duration, or a failure it handled itself
        rather than letting behave see.  ``features/environment.py`` owns the
        scenario lifecycle and the plan's dependency graph keeps it free of any
        import from this package, so it reaches this through the module-level
        :func:`record_hook_result` rather than through the class.

        Reported values are authoritative: :meth:`_finalize_hook_entries`
        applies the scenario model's measured status, text and duration only
        where nothing was reported, and never overwrites what was.

        Args:
            location: Dotted path of the hook; defaults to
                :data:`DEFAULT_AFTER_HOOK_LOCATION`.
            status: The hook's outcome in behave's vocabulary, or ``None`` to
                let the scenario model decide it.
            duration: The hook's duration in nanoseconds, or ``None`` to let
                the measured window stand.  A negative value is recorded as
                ``0``, as every duration in this schema is.
            error_message: The hook's failure text, or ``None``.  It is
                normalised to LF but otherwise recorded as given - a caller
                with an exception in hand should build the text with the same
                message-then-traceback shape the rest of the document uses.
            embeddings: Attachments to record under the same entry.

        Returns:
            ``True`` when it was recorded, ``False`` when no scenario is
            current or the call could not be applied.  Never raises: reporting
            a hook result must not be able to fail a run.
        """
        try:
            if self._scenario_element is None:
                return False
            key = location or DEFAULT_AFTER_HOOK_LOCATION
            entry = self._hook_entry_for(key)
            reported = self._reported_hook_results.setdefault(key, {})
            if status:
                reported["status"] = str(status)
            if duration is not None:
                reported["duration"] = max(int(duration), 0)
            if error_message:
                reported["error_message"] = _normalize_newlines(str(error_message))
            for embedding in embeddings or ():
                entry.setdefault("embeddings", []).append(dict(embedding))
            return True
        except Exception:
            logger.exception(
                "A hook result could not be recorded; the scenario's step "
                "results are unaffected"
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
            # A whole scenario is about to be lost, so the document stops
            # claiming to describe the whole run; the shard is then named dead
            # rather than merged as complete.
            identity = self._safe_identity(scenario)
            self._record_collection_error(
                "scenario",
                f"ScenarioNotRecorded: announced before any feature at {identity}",
            )
            logger.warning(
                "Scenario at %r announced before any feature; it was not "
                "recorded and the shard document is marked incomplete",
                identity,
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
        # The model is kept because it is the only source of a hook's outcome,
        # and behave has finished mutating it by the time this scenario is
        # finalised; the bookmark opens the window that will contain the
        # after-hooks, and moves to the last step's result if there are steps.
        self._scenario_model = scenario
        self._hook_window_start = self._hook_bookmark()

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
            identity = self._safe_identity(step)
            self._record_collection_error(
                "step",
                f"StepNotRecorded: announced outside a scenario at {identity}",
            )
            logger.warning(
                "Step at %r announced outside a scenario; it was not recorded "
                "and the shard document is marked incomplete",
                identity,
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
            identity = self._safe_identity(step)
            self._record_collection_error(
                "result",
                f"ResultDiscarded: no announced step to attribute it to at "
                f"{identity}",
            )
            logger.warning(
                "Result for an unannounced step at %r was discarded and the "
                "shard document is marked incomplete",
                identity,
            )
            self._pending_match = None
            return

        if self._pending_match is not None:
            self._apply_match(record, self._pending_match)
            self._pending_match = None
        record["result"] = self._build_result(step)
        # The last step to report is where the after-hook window opens: every
        # step of the scenario has run by then, and behave runs the hooks next.
        self._hook_window_start = self._hook_bookmark()

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

        Each stage's failure is **recorded before the document is written**, so
        the reason travels inside the file the parent will read rather than
        only in a worker's stderr, and the file is then refused by
        :func:`load_result_set` instead of being merged as a complete run.  If
        the write itself fails, a minimal document saying exactly that is
        attempted in its place: a diagnosable ``complete: false`` file is worth
        more to the parent than a truncated one, and if even that fails the
        caller's own absent-file and empty-file checks cover the case.
        """
        if self._closed:
            return
        self._closed = True
        try:
            self._finish_scenario()
        except Exception as error:
            logger.exception("Finalising the last scenario failed")
            self._record_collection_error("close.finalize", error)
        try:
            self.result_set["started_at"] = self._earliest_start_timestamp()
            self.result_set["generated_at"] = format_timestamp(self.clock())
        except Exception as error:
            logger.exception("Stamping the result document failed")
            self._record_collection_error("close.stamp", error)
        try:
            self._write_document()
        except Exception as error:
            logger.exception("Writing the result document failed")
            self._record_collection_error("close.write", error)
            self._write_minimal_document(error)
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

    def _write_minimal_document(self, error: BaseException) -> None:
        """Write a document that says only that the real one could not be written.

        Called from :meth:`close` when :meth:`_write_document` failed - a
        payload the encoder rejected, a stream that went away, a full disk.
        The parent then reads a small, well-formed, explicitly incomplete
        document carrying the reason instead of whatever half-serialised bytes
        the failed write left behind, which is the difference between a shard
        named dead *with a cause* and one reported as unparseable.

        Args:
            error: The exception that stopped the real write.

        Note:
            Best effort by design, and it never raises.  The stream is rewound
            and truncated first when it supports both, so the minimal document
            replaces any partial write rather than being appended to it; when
            it does not, the file is left for the caller's parse check to
            reject.  Only the run-level envelope is reproduced: the features
            are precisely what could not be serialised.
        """
        try:
            document = self.result_set if isinstance(self.result_set, dict) else {}
            recorded = document.get("collection_errors")
            errors: list[JsonDict] = [
                dict(entry)
                for entry in (recorded if isinstance(recorded, list) else ())
                if isinstance(entry, dict)
            ]
            if not any(entry.get("event") == "close.write" for entry in errors):
                errors.append(
                    {"event": "close.write", "error": _describe_exception(error)}
                )
            minimal = new_result_set(
                dry_run=bool(document.get("dry_run")),
                tag_expression=document.get("tag_expression"),
                metadata=document.get("metadata")
                if isinstance(document.get("metadata"), dict)
                else {},
                started_at=document.get("started_at"),
                generated_at=document.get("generated_at"),
                complete=False,
                collection_errors=errors,
            )
            stream = self.stream or self.open()
            for operation, argument in (("seek", 0), ("truncate", None)):
                action = getattr(stream, operation, None)
                if callable(action):
                    try:
                        action() if argument is None else action(argument)
                    except (OSError, ValueError):
                        logger.debug(
                            "The output stream does not support %s; the "
                            "minimal document is appended instead",
                            operation,
                        )
                        break
            stream.write(
                json.dumps(minimal, default=str, ensure_ascii=True, indent=2) + "\n"
            )
            flush = getattr(stream, "flush", None)
            if callable(flush):
                flush()
            logger.warning(
                "A minimal incomplete result document was written in place of "
                "the full one; this shard will be reported as dead"
            )
        except Exception:
            logger.exception(
                "A minimal result document could not be written either; the "
                "shard will be reported as absent or unparseable"
            )



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
            merely because the ``--clean`` step emptied the build-output
            directory.  The path itself always comes from
            :mod:`app.utils.paths` - this module contains no path literal.

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


# --------------------------------------------------------------------------- #
# The validator.
#
# One strict, recursive pass over the whole documented schema, and the only
# gate between a worker's file and the merge.  It exists because the merge and
# the writers are *tolerant*: they skip what they cannot read, so a
# structurally invalid shard would be silently reduced - a scenario here, a
# step there - and published as a complete run.  Everything it rejects, it
# rejects by raising :class:`ResultSetError` naming a JSON-pointer-style path,
# because "this shard is unusable" is only actionable with the reason attached.
#
# The rule the required-key sets below express is a single sentence: a
# document this build will merge is a document this build's own builders could
# have written.  So every key :func:`new_feature`, :func:`new_element`,
# :func:`new_step` and :func:`new_hook_entry` *always* emit is REQUIRED, and
# nothing else is accepted at all.
#
# Requiring presence rather than merely type-checking what is present is what
# makes an incomplete shard nameable.  A step object of ``{}`` is not a step
# whose fields defaulted harmlessly: it is a step whose keyword, text, match
# and outcome were lost, and a tolerant validator hands it to writers that
# each render their own idea of it -- one shows a blank row, the next counts
# it as untested, the rerun manifest omits it -- and the parent publishes the
# result as a complete run.  The same argument applies to a run-level document
# of ``{}``, which used to be defaulted into a perfectly valid description of a
# run in which nothing happened.
#
# Rejecting UNKNOWN keys matters for a second reason: an unknown key's value is
# not described by any schema rule, so before this pass existed it was the one
# place in a document that no limit applied to -- a 500-deep nesting or a
# megabyte string could ride into the merge inside one.  The generic budget in
# :func:`_check_budget` now bounds every value whatever its key, and refusing
# unknown keys closes the hole at the schema level as well.
#
# The exceptions, each measured against what the builders emit:
#
#   * a **scenario**'s ``tags`` is optional, because :func:`new_element` omits
#     the key entirely for an untagged scenario (the JVM's test-case map adds
#     it under ``if (!testCase.getTags().isEmpty())``);
#   * a **background** must carry NONE of ``id``, ``start_timestamp``, ``tags``
#     or ``after``; carrying one is rejected by name, because the templates and
#     ``iter_scenarios`` treat the absence of those keys as what makes an
#     element not a test case;
#   * ``match`` may be the empty object -- that is an undefined step, and the
#     emptiness is what makes ``cucumber_json`` omit ``location`` as the JVM
#     does -- so its two keys are optional;
#   * ``result.duration`` and ``result.error_message`` are optional (a skipped
#     step carries status only, and a passing step has no message) but
#     ``result.status`` is required, and is checked against
#     :data:`RESULT_STATUSES` rather than being accepted as any string;
#   * every run-level key except ``features`` is optional and defaulted by
#     :func:`_coerce_result_set`, which is what lets the hand-written
#     ``tests/fixtures/sample_results.json`` omit ``complete`` and
#     ``collection_errors`` and still read as a complete run;
#   * ``metadata`` is the one level with no key list: its sub-vocabulary is
#     whatever :func:`run_metadata`'s probes yielded, so it is validated as a
#     bounded nested structure of mappings and strings instead
#     (:func:`_validate_metadata`).
#
# ``bool`` is rejected where an ``int`` is meant, since ``bool`` is a subclass
# of ``int`` in Python and ``True`` would otherwise pass as a duration.
# --------------------------------------------------------------------------- #

#: Run-level keys.  Only ``features`` is required: a document that carries no
#: features list is not a run this build recorded, and defaulting it to ``[]``
#: is what turned a truncated file into a valid description of an empty run.
_RUN_REQUIRED_KEYS: Final[frozenset[str]] = frozenset({"features"})
_RUN_OPTIONAL_KEYS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "started_at",
        "generated_at",
        "dry_run",
        "tag_expression",
        "metadata",
        # Backward-compatible additions: absent ``complete`` means True and
        # absent ``collection_errors`` means [], which is why adding them did
        # not bump SCHEMA_VERSION and why they are optional here.
        "complete",
        "collection_errors",
    }
)

#: Feature keys.  All nine, because :func:`new_feature` emits all nine
#: unconditionally - including ``tags``, which is an empty list rather than an
#: absent key at this level.
_FEATURE_REQUIRED_KEYS: Final[frozenset[str]] = frozenset(
    {
        "uri",
        "path",
        "id",
        "keyword",
        "line",
        "name",
        "description",
        "tags",
        "elements",
    }
)

#: Element keys both kinds carry.
_ELEMENT_COMMON_KEYS: Final[frozenset[str]] = frozenset(
    {
        "type",
        "keyword",
        "line",
        "name",
        "description",
        "selected",
        "steps",
    }
)

#: Keys a scenario element carries in addition, all required.
_SCENARIO_REQUIRED_KEYS: Final[frozenset[str]] = frozenset(
    {"id", "start_timestamp", "after"}
)

#: The one scenario key that is optional, because an untagged scenario omits
#: it entirely rather than carrying an empty list.
_SCENARIO_OPTIONAL_KEYS: Final[frozenset[str]] = frozenset({"tags"})

#: Keys a Background occurrence must NOT carry.  Named in the rejection so the
#: message says which one, not merely that there was one.
_BACKGROUND_FORBIDDEN_KEYS: Final[frozenset[str]] = (
    _SCENARIO_REQUIRED_KEYS | _SCENARIO_OPTIONAL_KEYS
)

#: Step keys.  All six, because :func:`new_step` emits all six - the
#: optionality lives in the *contents* of ``match`` and ``result``, which is
#: exactly why their presence can be required.
_STEP_REQUIRED_KEYS: Final[frozenset[str]] = frozenset(
    {"keyword", "line", "name", "matched", "match", "result"}
)

#: ``match`` keys, all optional: ``{}`` is a legitimate match (an undefined
#: step) and ``arguments`` appears only for a parameterised step.
_MATCH_OPTIONAL_KEYS: Final[frozenset[str]] = frozenset({"location", "arguments"})

#: ``match.arguments`` entry keys.  Both optional: the JVM emits an empty
#: object for a parameter that has no value.
_ARGUMENT_OPTIONAL_KEYS: Final[frozenset[str]] = frozenset({"val", "offset"})

#: ``result`` keys.  ``status`` is required - a result without one says
#: nothing and every writer maps on it - while ``duration`` is omitted for a
#: skipped step and ``error_message`` only appears on a failure.
_RESULT_REQUIRED_KEYS: Final[frozenset[str]] = frozenset({"status"})
_RESULT_OPTIONAL_KEYS: Final[frozenset[str]] = frozenset(
    {"duration", "error_message"}
)

#: Hook-entry keys.  All three, because :func:`new_hook_entry` emits all three
#: - ``embeddings`` as an empty list when the hook attached nothing.
_HOOK_REQUIRED_KEYS: Final[frozenset[str]] = frozenset(
    {"match", "result", "embeddings"}
)

#: Embedding keys.  ``mime_type`` and ``data`` are what make an attachment
#: renderable as ``data:<mime_type>;base64,<data>``; ``name`` is optional
#: because behave's own ``context.attach()`` route carries none.
_EMBEDDING_REQUIRED_KEYS: Final[frozenset[str]] = frozenset({"mime_type", "data"})
_EMBEDDING_OPTIONAL_KEYS: Final[frozenset[str]] = frozenset({"name"})

#: Tag keys.  ``name`` is the only reason a tag exists; ``type`` and
#: ``location`` belong to the feature-level long shape, and a scenario tag
#: carries ``name`` alone, so both are optional at either level.
_TAG_REQUIRED_KEYS: Final[frozenset[str]] = frozenset({"name"})
_TAG_OPTIONAL_KEYS: Final[frozenset[str]] = frozenset({"type", "location"})

#: ``tag.location`` keys, both optional so that the long shape may be partial
#: in a hand-built document, and both typed when present.
_TAG_LOCATION_OPTIONAL_KEYS: Final[frozenset[str]] = frozenset({"line", "column"})

#: ``collection_errors`` entry keys.  Both optional, because
#: :func:`_describe_collection_errors` runs on a document already known to be
#: bad and reports ``?`` for a missing one rather than adding a second reason
#: to refuse a file that is refused anyway.
_COLLECTION_ERROR_OPTIONAL_KEYS: Final[frozenset[str]] = frozenset(
    {"event", "error"}
)


def _type_name(value: Any) -> str:
    """Name a value's type for a validation message.

    Args:
        value: The offending value.

    Returns:
        The type's name, with ``bool`` distinguished from ``int`` because the
        distinction is exactly what several of the checks are about.
    """
    return type(value).__name__


def _reject(source: str, path: str, problem: str) -> None:
    """Raise the schema violation at ``path``.

    Args:
        source: Where the document came from - a shard path or a fixture path.
        path: JSON-pointer-style path of the offending value, e.g.
            ``features[3].elements[2].steps[1].result.status``.
        problem: What is wrong with it, as a sentence fragment.

    Raises:
        ResultSetError: Always.  This function exists so that every message in
            the validator has one shape and one owner.
    """
    raise ResultSetError(f"{source}: {path} {problem}")


def _check_string(value: Any, source: str, path: str, limit: int) -> None:
    """Require ``value`` to be a string no longer than ``limit``.

    Args:
        value: The value to check.
        source: Where the document came from.
        path: JSON-pointer-style path of the value.
        limit: Longest accepted length in characters.

    Raises:
        ResultSetError: If it is not a string, or is longer than ``limit``.
    """
    if not isinstance(value, str):
        _reject(source, path, f"must be a string, found {_type_name(value)}")
    if len(value) > limit:
        _reject(
            source,
            path,
            f"is {len(value)} characters, over the {limit}-character limit",
        )


def _check_optional_string(value: Any, source: str, path: str) -> None:
    """Require ``value`` to be a string or ``None``.

    Args:
        value: The value to check.
        source: Where the document came from.
        path: JSON-pointer-style path of the value.

    Raises:
        ResultSetError: If it is neither a string nor ``None``, or is over
            :data:`MAX_STRING_LENGTH`.
    """
    if value is None:
        return
    _check_string(value, source, path, MAX_STRING_LENGTH)


def _check_int(value: Any, source: str, path: str) -> None:
    """Require ``value`` to be a real integer.

    Args:
        value: The value to check.
        source: Where the document came from.
        path: JSON-pointer-style path of the value.

    Raises:
        ResultSetError: If it is not an :class:`int`, or is a :class:`bool` -
            which is an ``int`` subclass, and would otherwise pass as a line
            number or a duration.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        _reject(source, path, f"must be an integer, found {_type_name(value)}")


def _check_bool(value: Any, source: str, path: str) -> None:
    """Require ``value`` to be a boolean.

    Args:
        value: The value to check.
        source: Where the document came from.
        path: JSON-pointer-style path of the value.

    Raises:
        ResultSetError: If it is not a :class:`bool`.  ``0`` and ``1`` are
            rejected too: the collector writes real booleans, and accepting
            their integer look-alikes would let a reader's ``is True`` test
            quietly disagree with its ``if`` test.
    """
    if not isinstance(value, bool):
        _reject(source, path, f"must be a boolean, found {_type_name(value)}")


def _check_mapping(value: Any, source: str, path: str) -> JsonDict:
    """Require ``value`` to be a JSON object.

    Args:
        value: The value to check.
        source: Where the document came from.
        path: JSON-pointer-style path of the value.

    Returns:
        The mapping, so a caller can keep walking it.

    Raises:
        ResultSetError: If it is not a mapping.
    """
    if not isinstance(value, dict):
        _reject(source, path, f"must be an object, found {_type_name(value)}")
    return value


def _check_object_keys(
    mapping: JsonDict,
    source: str,
    path: str,
    required: frozenset[str],
    optional: frozenset[str],
) -> None:
    """Require exactly the keys the schema documents at this level.

    The single owner of both halves of that rule, so that "which keys" is
    answered once per level by a constant rather than by a sequence of
    membership tests that can silently omit one.

    Args:
        mapping: The object to check.
        source: Where the document came from.
        path: JSON-pointer-style path of the object.
        required: Keys that must be present.  Absence is rejected because the
            builders in this module always emit them, so a document missing
            one is not a document this build wrote - it is a truncated or
            fabricated one, and every writer would fill the gap differently.
        optional: Keys that may be present.

    Raises:
        ResultSetError: On the first missing required key, in sorted order so
            the message is deterministic, or on the first unrecognised key.
            An unknown key is refused rather than ignored: its value is
            described by no rule, and tolerating it is what let a hostile
            document smuggle unbounded data past the schema.  Both messages
            name the key and the path.
    """
    for key in sorted(required):
        if key not in mapping:
            _reject(source, path, f"must carry {key!r}")
    allowed = required | optional
    for key in mapping:
        if key not in allowed:
            _reject(
                source,
                path,
                f"carries the unknown key '{_log_safe_text(key)}'; this level "
                f"accepts only {', '.join(sorted(allowed))}",
            )


def _check_list(
    value: Any,
    source: str,
    path: str,
    limit: int,
    limit_name: str,
) -> list[Any]:
    """Require ``value`` to be a list within its documented limit.

    Args:
        value: The value to check.
        source: Where the document came from.
        path: JSON-pointer-style path of the value.
        limit: Largest accepted length.
        limit_name: Name of the constant that set it, so the message says
            which limit was breached and not merely that one was.

    Returns:
        The list, so a caller can keep walking it.

    Raises:
        ResultSetError: If it is not a list, or is longer than ``limit``.
    """
    if not isinstance(value, list):
        _reject(source, path, f"must be a list, found {_type_name(value)}")
    if len(value) > limit:
        _reject(
            source,
            path,
            f"has {len(value)} entries, over the {limit_name} limit of {limit}",
        )
    return value


#: Longest object key reproduced inside a budget-violation path.  A path is a
#: diagnostic, and a hostile document can carry a key as long as the string
#: limit allows, so the segment is trimmed to something a log line can hold.
_PATH_SEGMENT_LIMIT: Final[int] = 60

#: Path of the document itself, for a violation found at the root.  Named
#: because the budget walk also uses it to recognise the root and start its
#: child paths at ``features[0]`` rather than at ``the document.features[0]``,
#: which is the shape every schema-level message uses.
_DOCUMENT_PATH: Final[str] = "the document"


def _check_budget(document: Any, source: str) -> None:
    """Bound a parsed document's depth, node count and string lengths.

    The generic half of the resource boundary (CWE-400), and it runs **before**
    any schema rule for two reasons.  First, the schema rules only reach the
    keys they know: before this pass existed, an unknown key's value was
    subject to no limit at all, and a 500-deep object or a megabyte string
    could ride into the merge inside one and exhaust the parent in
    :func:`copy.deepcopy`.  Second, a limit breach is the cheapest possible
    rejection, so establishing it first means the expensive walk never runs on
    a document that is going to be refused anyway.

    The walk is **iterative**, over an explicit stack, and that is not a style
    choice: a recursive walk would hit CPython's own recursion limit on
    precisely the input this function exists to refuse, raising
    :class:`RecursionError` out of the validator instead of
    :class:`ResultSetError` - which is the exact failure mode that let a
    malformed shard bypass the parent's dead-worker handling.  Nodes are
    counted as they are *pushed* rather than as they are visited, so the
    stack itself is bounded by :data:`MAX_DOCUMENT_NODES` too.

    Args:
        document: The parsed JSON value, of any shape.
        source: Where it came from, for the error messages.

    Raises:
        ResultSetError: If the document nests deeper than
            :data:`MAX_DOCUMENT_DEPTH`, carries more than
            :data:`MAX_DOCUMENT_NODES` nodes, carries a string longer than
            :data:`MAX_ANY_STRING_LENGTH`, or carries a non-string object key
            (JSON has none, so one means the value did not come from
            :func:`json.loads`).  Every message names the limit that was
            breached and the path of the value that breached it.
    """
    # The root counts as the first node; every child is counted as it is
    # pushed, and a mapping entry counts twice - once for its key, once for
    # its value - because both cost the parent memory.
    nodes = 1
    stack: list[tuple[Any, int, str]] = [(document, 1, _DOCUMENT_PATH)]
    while stack:
        value, depth, path = stack.pop()
        # The path is truncated in every message below: it is built from the
        # document's own keys, so a hostile file could otherwise stretch one
        # diagnostic line across every level it nests.
        if depth > MAX_DOCUMENT_DEPTH:
            _reject(
                source,
                _log_safe_text(path),
                f"is nested {depth} levels deep, over the "
                f"MAX_DOCUMENT_DEPTH limit of {MAX_DOCUMENT_DEPTH}",
            )
        if isinstance(value, str):
            if len(value) > MAX_ANY_STRING_LENGTH:
                _reject(
                    source,
                    _log_safe_text(path),
                    f"is {len(value)} characters, over the "
                    f"MAX_ANY_STRING_LENGTH limit of {MAX_ANY_STRING_LENGTH}",
                )
            continue
        # Child paths read ``features[0].elements[1]``, matching every
        # schema-level message, so the root contributes no segment of its own.
        prefix = "" if path == _DOCUMENT_PATH else path
        if isinstance(value, dict):
            children = []
            for key, item in value.items():
                if not isinstance(key, str):
                    _reject(
                        source,
                        _log_safe_text(path),
                        f"carries a key of type {_type_name(key)}; a JSON "
                        "object's keys are strings",
                    )
                if len(key) > MAX_ANY_STRING_LENGTH:
                    _reject(
                        source,
                        _log_safe_text(path),
                        f"carries a key of {len(key)} characters, over the "
                        f"MAX_ANY_STRING_LENGTH limit of "
                        f"{MAX_ANY_STRING_LENGTH}",
                    )
                segment = _log_safe_text(key, _PATH_SEGMENT_LIMIT)
                children.append(
                    (item, depth + 1, f"{prefix}.{segment}" if prefix else segment)
                )
            # Two nodes per entry: the key and the value.
            nodes += 2 * len(children)
        elif isinstance(value, (list, tuple)):
            children = [
                (item, depth + 1, f"{prefix}[{index}]" if prefix else f"[{index}]")
                for index, item in enumerate(value)
            ]
            nodes += len(children)
        else:
            # A scalar: already counted when it was pushed, and it has no
            # children to walk.
            continue
        if nodes > MAX_DOCUMENT_NODES:
            _reject(
                source,
                _log_safe_text(path),
                f"brings the document to more than {MAX_DOCUMENT_NODES} "
                "nodes, the MAX_DOCUMENT_NODES limit",
            )
        stack.extend(children)


def _validate_tag(tag: Any, source: str, path: str) -> None:
    """Validate one tag, in either of the two shapes the schema carries.

    A feature tag is the JVM's long shape - ``name``, ``type`` and a
    ``location`` with a ``line`` and a ``column`` - and a scenario tag is the
    short one, ``name`` alone.  The asymmetry is measured, so both are accepted
    at either level rather than enforced per level; ``name`` is required
    because it is the only reason a tag exists, and every consumer reads it.

    Args:
        tag: The tag value.
        source: Where the document came from.
        path: JSON-pointer-style path of the tag.

    Raises:
        ResultSetError: If the tag is not an object, carries no ``name``,
            carries a key this level does not define, or carries a key of the
            wrong type.
    """
    mapping = _check_mapping(tag, source, path)
    _check_object_keys(
        mapping, source, path, _TAG_REQUIRED_KEYS, _TAG_OPTIONAL_KEYS
    )
    _check_string(mapping["name"], source, f"{path}.name", MAX_STRING_LENGTH)
    if "type" in mapping:
        _check_string(mapping["type"], source, f"{path}.type", MAX_STRING_LENGTH)
    if "location" in mapping:
        location = _check_mapping(
            mapping["location"], source, f"{path}.location"
        )
        _check_object_keys(
            location,
            source,
            f"{path}.location",
            frozenset(),
            _TAG_LOCATION_OPTIONAL_KEYS,
        )
        for key in sorted(_TAG_LOCATION_OPTIONAL_KEYS):
            if key in location:
                _check_int(location[key], source, f"{path}.location.{key}")


def _validate_result(result: Any, source: str, path: str) -> None:
    """Validate a step's or a hook's ``result`` mapping.

    Args:
        result: The ``result`` value.
        source: Where the document came from.
        path: JSON-pointer-style path of the mapping.

    Raises:
        ResultSetError: If it is not an object, carries a key this level does
            not define, omits ``status``, carries a ``status`` outside
            :data:`RESULT_STATUSES`, or gives ``duration`` or
            ``error_message`` the wrong type.  ``status`` is required because a
            result is the *record of an outcome* and one without a status
            records nothing, while every writer maps the field by name - an
            absent or invented name is folded to ``untested`` by
            ``cucumber_json`` and published as a step that never ran.
    """
    mapping = _check_mapping(result, source, path)
    _check_object_keys(
        mapping, source, path, _RESULT_REQUIRED_KEYS, _RESULT_OPTIONAL_KEYS
    )
    _check_string(mapping["status"], source, f"{path}.status", MAX_STRING_LENGTH)
    if mapping["status"] not in RESULT_STATUSES:
        _reject(
            source,
            f"{path}.status",
            f"is '{_log_safe_text(mapping['status'])}', which is not one of "
            f"the accepted status names: {', '.join(sorted(RESULT_STATUSES))}",
        )
    if "duration" in mapping:
        _check_int(mapping["duration"], source, f"{path}.duration")
    if "error_message" in mapping:
        _check_string(
            mapping["error_message"],
            source,
            f"{path}.error_message",
            MAX_STRING_LENGTH,
        )


def _validate_match(match: Any, source: str, path: str) -> None:
    """Validate a step's ``match`` mapping and its arguments.

    Args:
        match: The ``match`` value.
        source: Where the document came from.
        path: JSON-pointer-style path of the mapping.

    Raises:
        ResultSetError: If it is not an object, carries a key this level does
            not define, its ``location`` is not a string, its ``arguments`` is
            not a list or breaches :data:`MAX_ARGUMENTS_PER_STEP`, or an
            argument is malformed.  An **empty** ``match`` is valid and means
            an undefined step, and an empty argument mapping is valid too - the
            JVM emits one for a parameter that has no value.
    """
    mapping = _check_mapping(match, source, path)
    _check_object_keys(
        mapping, source, path, frozenset(), _MATCH_OPTIONAL_KEYS
    )
    if "location" in mapping:
        _check_string(
            mapping["location"], source, f"{path}.location", MAX_STRING_LENGTH
        )
    if "arguments" not in mapping:
        return
    arguments = _check_list(
        mapping["arguments"],
        source,
        f"{path}.arguments",
        MAX_ARGUMENTS_PER_STEP,
        "MAX_ARGUMENTS_PER_STEP",
    )
    for index, argument in enumerate(arguments):
        argument_path = f"{path}.arguments[{index}]"
        entry = _check_mapping(argument, source, argument_path)
        _check_object_keys(
            entry,
            source,
            argument_path,
            frozenset(),
            _ARGUMENT_OPTIONAL_KEYS,
        )
        if "val" in entry:
            _check_string(
                entry["val"], source, f"{argument_path}.val", MAX_STRING_LENGTH
            )
        if "offset" in entry:
            _check_int(entry["offset"], source, f"{argument_path}.offset")


def _validate_step(step: Any, source: str, path: str) -> None:
    """Validate one step object.

    Args:
        step: The step value.
        source: Where the document came from.
        path: JSON-pointer-style path of the step.

    Raises:
        ResultSetError: If it is not an object, omits any of the six keys
            :func:`new_step` always emits, carries a key this level does not
            define, or gives any of them the wrong type.  All six are required
            and none of them is defaulted: a step object of ``{}`` is not a
            harmlessly sparse step, it is a step whose keyword, text, match and
            outcome were lost, and each writer would invent a different
            replacement for them.  The optionality the schema does have lives
            *inside* ``match`` (empty for an undefined step) and ``result``,
            which is exactly why their presence can be insisted on.
    """
    mapping = _check_mapping(step, source, path)
    _check_object_keys(
        mapping, source, path, _STEP_REQUIRED_KEYS, frozenset()
    )
    _check_string(mapping["keyword"], source, f"{path}.keyword", MAX_STRING_LENGTH)
    _check_int(mapping["line"], source, f"{path}.line")
    _check_string(mapping["name"], source, f"{path}.name", MAX_STRING_LENGTH)
    _check_bool(mapping["matched"], source, f"{path}.matched")
    _validate_match(mapping["match"], source, f"{path}.match")
    _validate_result(mapping["result"], source, f"{path}.result")


def _validate_embedding(embedding: Any, source: str, path: str) -> None:
    """Validate one attachment mapping.

    Args:
        embedding: The embedding value.
        source: Where the document came from.
        path: JSON-pointer-style path of the embedding.

    Raises:
        ResultSetError: If it is not an object, omits ``mime_type`` or
            ``data``, carries a key this level does not define, or gives one of
            them the wrong type, or its ``data`` is over
            :data:`MAX_EMBEDDING_DATA_LENGTH`.  The two are required because an
            embedding exists to be rendered as
            ``data:<mime_type>;base64,<data>`` and one missing either cannot
            be; ``name`` is optional because behave's own ``context.attach()``
            route carries none.  ``data`` gets its own, much larger limit: it
            is base64 image bytes, and it is the one field in the schema that
            is legitimately measured in megabytes.
    """
    mapping = _check_mapping(embedding, source, path)
    _check_object_keys(
        mapping,
        source,
        path,
        _EMBEDDING_REQUIRED_KEYS,
        _EMBEDDING_OPTIONAL_KEYS,
    )
    _check_string(
        mapping["mime_type"], source, f"{path}.mime_type", MAX_STRING_LENGTH
    )
    _check_string(
        mapping["data"], source, f"{path}.data", MAX_EMBEDDING_DATA_LENGTH
    )
    if "name" in mapping:
        _check_string(mapping["name"], source, f"{path}.name", MAX_STRING_LENGTH)


def _validate_hook_entry(entry: Any, source: str, path: str) -> None:
    """Validate one ``after`` hook entry.

    Args:
        entry: The hook entry value.
        source: Where the document came from.
        path: JSON-pointer-style path of the entry.

    Raises:
        ResultSetError: If it is not an object, omits any of the three keys
            :func:`new_hook_entry` always emits, carries a key this level does
            not define, has a malformed ``match`` or ``result``, or has an
            ``embeddings`` that is not a list within
            :data:`MAX_EMBEDDINGS_PER_HOOK`.  All three are required for the
            same reason a step's six are: a hook entry exists only when there
            was something real to record, so one that records no outcome is a
            lost outcome rather than an empty one.  ``embeddings`` is a list
            that is legitimately empty - a hook that failed without attaching
            anything still gets an entry.
    """
    mapping = _check_mapping(entry, source, path)
    _check_object_keys(
        mapping, source, path, _HOOK_REQUIRED_KEYS, frozenset()
    )
    _validate_match(mapping["match"], source, f"{path}.match")
    _validate_result(mapping["result"], source, f"{path}.result")
    embeddings = _check_list(
        mapping["embeddings"],
        source,
        f"{path}.embeddings",
        MAX_EMBEDDINGS_PER_HOOK,
        "MAX_EMBEDDINGS_PER_HOOK",
    )
    for index, embedding in enumerate(embeddings):
        _validate_embedding(embedding, source, f"{path}.embeddings[{index}]")


def _validate_element(element: Any, source: str, path: str) -> None:
    """Validate one Background occurrence or scenario element.

    The two element types are validated as the two different shapes they are,
    because that asymmetry is the schema's own and :func:`new_element` is its
    only producer: a scenario is a test case and carries an ``id``, a
    ``start_timestamp`` and an ``after`` list, while a Background occurrence is
    deliberately poorer and carries none of them.  The templates and
    :func:`iter_scenarios` rely on that difference, so a background carrying a
    scenario-only key is refused by name rather than tolerated - it would
    otherwise reach ``pretty/_element_tree.html`` as a test case with no
    outcome.

    Args:
        element: The element value.
        source: Where the document came from.
        path: JSON-pointer-style path of the element.

    Raises:
        ResultSetError: If it is not an object; if ``type`` is absent or is
            neither ``background`` nor ``scenario``; if any key the element's
            type requires is absent; if a background carries ``id``,
            ``start_timestamp``, ``tags`` or ``after``; if the element carries
            a key this level does not define; or if any key has the wrong type.
    """
    mapping = _check_mapping(element, source, path)
    if "type" not in mapping:
        _reject(source, path, "must carry a 'type'")
    element_type = mapping["type"]
    if element_type not in (ELEMENT_TYPE_BACKGROUND, ELEMENT_TYPE_SCENARIO):
        _reject(
            source,
            f"{path}.type",
            f"must be {ELEMENT_TYPE_BACKGROUND!r} or {ELEMENT_TYPE_SCENARIO!r}, "
            f"found '{_log_safe_text(element_type)}'",
        )
    if element_type == ELEMENT_TYPE_BACKGROUND:
        for key in sorted(_BACKGROUND_FORBIDDEN_KEYS):
            if key in mapping:
                _reject(
                    source,
                    path,
                    f"is a {ELEMENT_TYPE_BACKGROUND} and must not carry "
                    f"{key!r}: that key marks an element as a test case, and "
                    "a Background occurrence is not one",
                )
        _check_object_keys(
            mapping, source, path, _ELEMENT_COMMON_KEYS, frozenset()
        )
    else:
        _check_object_keys(
            mapping,
            source,
            path,
            _ELEMENT_COMMON_KEYS | _SCENARIO_REQUIRED_KEYS,
            _SCENARIO_OPTIONAL_KEYS,
        )
        _check_string(mapping["id"], source, f"{path}.id", MAX_STRING_LENGTH)
        _check_optional_string(
            mapping["start_timestamp"], source, f"{path}.start_timestamp"
        )
    for key in ("keyword", "name", "description"):
        _check_string(mapping[key], source, f"{path}.{key}", MAX_STRING_LENGTH)
    _check_int(mapping["line"], source, f"{path}.line")
    _check_bool(mapping["selected"], source, f"{path}.selected")
    if "tags" in mapping:
        tags = _check_list(
            mapping["tags"],
            source,
            f"{path}.tags",
            MAX_TAGS_PER_LEVEL,
            "MAX_TAGS_PER_LEVEL",
        )
        for index, tag in enumerate(tags):
            _validate_tag(tag, source, f"{path}.tags[{index}]")
    if "after" in mapping:
        hooks = _check_list(
            mapping["after"],
            source,
            f"{path}.after",
            MAX_HOOKS_PER_ELEMENT,
            "MAX_HOOKS_PER_ELEMENT",
        )
        for index, entry in enumerate(hooks):
            _validate_hook_entry(entry, source, f"{path}.after[{index}]")

    # ``steps`` last, so that a message about the element's own shape is
    # reached before one about a step inside it.  Its presence is already
    # required by the key check above, whichever type the element is.
    steps = _check_list(
        mapping["steps"],
        source,
        f"{path}.steps",
        MAX_STEPS_PER_ELEMENT,
        "MAX_STEPS_PER_ELEMENT",
    )
    for index, step in enumerate(steps):
        _validate_step(step, source, f"{path}.steps[{index}]")


def _validate_feature(feature: Any, source: str, path: str) -> None:
    """Validate one feature object, including its whole element tree.

    Args:
        feature: The feature value.
        source: Where the document came from.
        path: JSON-pointer-style path of the feature.

    Raises:
        ResultSetError: If it is not an object; if it omits any of the nine
            keys :func:`new_feature` always emits; if it carries a key this
            level does not define; if it carries neither a non-empty ``uri``
            nor a non-empty ``path``, which is the identity
            :func:`_feature_key` merges on; if ``elements`` is not a list
            within :data:`MAX_ELEMENTS_PER_FEATURE`; or if any key, tag or
            element is malformed.  All nine are required, ``tags`` included:
            unlike a scenario's, a feature's ``tags`` is emitted
            unconditionally and is an empty list when the feature declares
            none, so an absent key means the list was lost rather than empty.
    """
    mapping = _check_mapping(feature, source, path)
    _check_object_keys(
        mapping, source, path, _FEATURE_REQUIRED_KEYS, frozenset()
    )
    for key in ("uri", "path", "id", "keyword", "name", "description"):
        _check_string(mapping[key], source, f"{path}.{key}", MAX_STRING_LENGTH)
    if not (mapping.get("uri") or mapping.get("path")):
        _reject(
            source,
            path,
            "must carry a non-empty 'uri' or 'path': it is the identity the "
            "merge groups features by, and features without one would fuse",
        )
    _check_int(mapping["line"], source, f"{path}.line")
    tags = _check_list(
        mapping["tags"],
        source,
        f"{path}.tags",
        MAX_TAGS_PER_LEVEL,
        "MAX_TAGS_PER_LEVEL",
    )
    for index, tag in enumerate(tags):
        _validate_tag(tag, source, f"{path}.tags[{index}]")

    elements = _check_list(
        mapping["elements"],
        source,
        f"{path}.elements",
        MAX_ELEMENTS_PER_FEATURE,
        "MAX_ELEMENTS_PER_FEATURE",
    )
    for index, element in enumerate(elements):
        _validate_element(element, source, f"{path}.elements[{index}]")


def _validate_metadata(metadata: Any, source: str, path: str) -> None:
    """Validate the run-level ``metadata`` block as a bounded structure.

    This is the one level with no key list, and deliberately so: the block is
    whatever :func:`run_metadata`'s probes yielded - today four groups of a
    ``name`` and a ``version`` - and ``app/templates/artifact/metadata.html``
    renders it by iterating rather than by field.  A key list here would have
    to be updated for every probe added, and an unknown-key rejection would
    refuse a document a later build wrote.

    So the rule is structural instead of nominal, and it is enforced strictly
    enough that the exemption costs nothing: mappings and strings only, no
    list and no number, bounded depth, bounded total entries, and every string
    within :data:`MAX_STRING_LENGTH`.  The walk is iterative for the same
    reason :func:`_check_budget`'s is - a hostile block must not be able to
    turn a validator into a :class:`RecursionError`.

    Args:
        metadata: The ``metadata`` value.
        source: Where the document came from.
        path: JSON-pointer-style path of the block.

    Raises:
        ResultSetError: If it is not an object; if it nests deeper than
            :data:`MAX_METADATA_DEPTH`; if it carries more than
            :data:`MAX_METADATA_ENTRIES` entries in total; or if any value is
            neither a mapping nor a string within :data:`MAX_STRING_LENGTH`.
            The group names themselves are not checked: their *presence* is
            not required either, because a probe that yields nothing yields
            ``""`` and a hand-built document may carry ``{}``.
    """
    mapping = _check_mapping(metadata, source, path)
    entries = 0
    stack: list[tuple[JsonDict, int, str]] = [(mapping, 1, path)]
    while stack:
        group, depth, group_path = stack.pop()
        if depth > MAX_METADATA_DEPTH:
            _reject(
                source,
                group_path,
                f"is nested {depth} levels deep, over the "
                f"MAX_METADATA_DEPTH limit of {MAX_METADATA_DEPTH}",
            )
        entries += len(group)
        if entries > MAX_METADATA_ENTRIES:
            _reject(
                source,
                group_path,
                f"brings the metadata block to more than "
                f"{MAX_METADATA_ENTRIES} entries, the MAX_METADATA_ENTRIES "
                "limit",
            )
        for name, value in group.items():
            entry_path = f"{group_path}.{_log_safe_text(name, _PATH_SEGMENT_LIMIT)}"
            if isinstance(value, dict):
                stack.append((value, depth + 1, entry_path))
                continue
            _check_string(value, source, entry_path, MAX_STRING_LENGTH)


def _validate_collection_errors(errors: Any, source: str, path: str) -> None:
    """Validate the run-level ``collection_errors`` list.

    Args:
        errors: The ``collection_errors`` value.
        source: Where the document came from.
        path: JSON-pointer-style path of the list.

    Raises:
        ResultSetError: If it is not a list within
            :data:`MAX_COLLECTION_ERRORS`, an entry is not an object, an entry
            carries a key this level does not define, or an entry's ``event``
            or ``error`` is not a string.  Neither key is *required*: this list
            is diagnostic, the document carrying it is refused anyway, and
            :func:`_describe_collection_errors` already reports ``?`` for a
            missing one - adding a second reason to refuse the same file would
            only replace a useful message with a less useful one.
    """
    entries = _check_list(
        errors, source, path, MAX_COLLECTION_ERRORS, "MAX_COLLECTION_ERRORS"
    )
    for index, entry in enumerate(entries):
        entry_path = f"{path}[{index}]"
        mapping = _check_mapping(entry, source, entry_path)
        _check_object_keys(
            mapping,
            source,
            entry_path,
            frozenset(),
            _COLLECTION_ERROR_OPTIONAL_KEYS,
        )
        for key in sorted(_COLLECTION_ERROR_OPTIONAL_KEYS):
            if key in mapping:
                _check_string(
                    mapping[key], source, f"{entry_path}.{key}", MAX_STRING_LENGTH
                )


def _validate_result_set(document: Any, source: str) -> ResultSet:
    """Validate a whole parsed document against this module's schema.

    Args:
        document: The parsed JSON value.
        source: Where it came from, for the error messages.

    Returns:
        The same object, unmodified.  Validation never repairs: a document
        either is this schema or is refused, because a repaired shard is
        indistinguishable from a correct one downstream and that is precisely
        how results get lost quietly.

    Raises:
        ResultSetError: On the first violation found, naming its path.  The
            generic resource budget (:func:`_check_budget`) is applied first,
            so a document that is too deep, too large or too numerous to walk
            safely is refused before any schema rule touches it; after that the
            walk is depth-first in document order, so the reported path is the
            first thing a human reading the file would reach.
    """
    _check_budget(document, source)
    mapping = _check_mapping(document, source, _DOCUMENT_PATH)
    _check_object_keys(
        mapping, source, _DOCUMENT_PATH, _RUN_REQUIRED_KEYS, _RUN_OPTIONAL_KEYS
    )

    version = mapping.get("schema_version")
    if version is not None:
        if isinstance(version, bool) or not isinstance(version, int):
            _reject(
                source,
                "schema_version",
                f"must be an integer, found {_type_name(version)}",
            )
        if version != SCHEMA_VERSION:
            _reject(
                source,
                "schema_version",
                f"is {version}; this build reads version {SCHEMA_VERSION}",
            )

    for key in ("started_at", "generated_at", "tag_expression"):
        if key in mapping:
            _check_optional_string(mapping[key], source, key)
    for key in ("dry_run", "complete"):
        if key in mapping:
            _check_bool(mapping[key], source, key)
    if "metadata" in mapping:
        _validate_metadata(mapping["metadata"], source, "metadata")
    if "collection_errors" in mapping:
        _validate_collection_errors(
            mapping["collection_errors"], source, "collection_errors"
        )

    features = _check_list(
        mapping["features"], source, "features", MAX_FEATURES, "MAX_FEATURES"
    )
    for index, feature in enumerate(features):
        _validate_feature(feature, source, f"features[{index}]")
    return mapping


def _describe_collection_errors(errors: Any) -> str:
    """Summarise a document's ``collection_errors`` for a rejection message.

    Args:
        errors: The document's ``collection_errors`` value, of any shape - this
            runs on the path where the document is already known to be bad.

    Returns:
        A **one-line** summary of the first few entries, each as
        ``event: error``, with a count of the remainder.  ``"no reason
        recorded"`` when the list is empty or unusable, because a document that
        declares itself incomplete without saying why is still refused - it
        just cannot be diagnosed from the file alone.

        Every fragment goes through :func:`_log_safe_text`, and that is not
        cosmetic: this text is interpolated into the :class:`ResultSetError`
        message that ``app/services/test_run_service.py`` logs, the entries
        come from the worker's own file, and a ``LF`` inside one would turn a
        single rejection into several forged log records while an ``ESC``
        would reach a terminal as an escape sequence (CWE-117).  The escaping
        is applied here, at the one place a recorded entry becomes
        parent-facing text, so the file itself keeps the raw detail.
    """
    if not isinstance(errors, list) or not errors:
        return "no reason recorded"
    shown: list[str] = []
    for entry in errors[:_REJECTION_REASONS_SHOWN]:
        if isinstance(entry, dict):
            event = _log_safe_text(entry.get("event", "?"))
            error = _log_safe_text(entry.get("error", "?"))
            shown.append(f"{event}: {error}")
        else:
            shown.append(_describe_exception(str(entry)))
    remaining = len(errors) - len(shown)
    summary = "; ".join(shown)
    return f"{summary} (and {remaining} more)" if remaining > 0 else summary


def _coerce_result_set(document: Any, source: str) -> ResultSet:
    """Validate a parsed document strictly and fill in absent run-level keys.

    Args:
        document: The parsed JSON value.
        source: Where it came from, for the error message.

    Returns:
        The document, with every run-level key present so that consumers never
        need a membership test.  Feature data is never invented or repaired:
        only the run-level envelope is completed, which is what lets a
        hand-written fixture omit boilerplate.  ``complete`` defaults to
        ``True`` and ``collection_errors`` to ``[]``, so a document written
        before those keys existed - or by hand - reads as a complete one.

    Raises:
        ResultSetError: If the document violates the schema anywhere
            (:func:`_validate_result_set`), or if it declares itself
            incomplete.  A collector that dropped an event says so in the file
            it writes, and the whole point of saying so is that the document is
            then refused here: the parent names that shard dead, with the
            recorded reason, instead of merging partial results as complete.
    """
    validated = _validate_result_set(document, source)

    if validated.get("complete", True) is False:
        raise ResultSetError(
            f"{source}: the collector reported an incomplete run "
            f"({_describe_collection_errors(validated.get('collection_errors'))})"
        )

    validated.setdefault("schema_version", SCHEMA_VERSION)
    validated.setdefault("started_at", None)
    validated.setdefault("generated_at", None)
    validated.setdefault("dry_run", False)
    validated.setdefault("tag_expression", None)
    validated.setdefault("metadata", {})
    validated.setdefault("complete", True)
    validated.setdefault("collection_errors", [])
    validated.setdefault("features", [])
    return validated


def load_result_set(path: Path | str) -> ResultSet:
    """Read one result document back from disk, validating it in full.

    This is the **only** gate between a worker's intermediate file and the
    merge, so it is a strict one: the whole schema is validated
    (:func:`_validate_result_set`), every documented resource limit is enforced
    (the ``MAX_*`` constants), and a document the collector marked incomplete
    is refused.  Nothing downstream re-checks, and nothing downstream has to:
    what this function returns can be merged and written, and what it rejects
    is a dead shard the caller names.

    It is also the only function in this module that reads a shard, and it
    does so through **one** descriptor.  ``stat()`` followed by ``open()`` was
    a time-of-check/time-of-use pair: the path could be replaced between the
    two, so the size the check approved was not necessarily the size the read
    got.  The descriptor is opened once with :data:`_RESULT_FILE_OPEN_FLAGS`,
    interrogated with :func:`os.fstat`, required to be a regular file, and
    then read for at most one byte more than the cap allows - so the file that
    was measured is the file that is read, a symlink or a FIFO in a shard's
    place is refused rather than followed or waited on, and a file that grows
    between the ``fstat`` and the read is refused by the byte count rather
    than loaded whole.

    Args:
        path: The file to read - a per-worker intermediate from
            :func:`app.utils.paths.worker_result_path`, or a fixture.

    Returns:
        The document, with its run-level envelope completed by
        :func:`_coerce_result_set`.

    Raises:
        ResultSetError: If the file is absent, not a regular file, too large,
            unreadable, not valid UTF-8, not valid JSON, nested too deeply for
            the parser, not this schema, over a resource limit, or declared
            incomplete by the collector that wrote it.  **One exception type
            for every failure is the point**:
            ``app/services/test_run_service.py`` catches exactly this to mark
            a shard dead with a named reason and apply the plan's exit table,
            and a failure that escaped as something else - an
            :class:`OSError`, a :class:`UnicodeDecodeError`, a
            :class:`RecursionError` - would bypass that handling and surface as
            an undocumented exit instead.  The cause is chained wherever there
            was one: the non-regular-file refusal is this function's own
            check on an open descriptor, so that one carries none.
    """
    source = str(path)
    try:
        descriptor = os.open(Path(path), _RESULT_FILE_OPEN_FLAGS)
    except (OSError, TypeError, ValueError) as error:
        # ``TypeError`` and ``ValueError`` are here because a path is not
        # always a path: ``Path(5)`` raises the first and a name carrying a
        # null byte raises the second, neither of them an ``OSError``.  The
        # ``OSError`` itself now also covers ``ELOOP`` from ``O_NOFOLLOW``,
        # which is what a symlink in a shard's place produces.  All of them
        # mean "this shard cannot be read", which is the one thing this
        # function is allowed to say.
        raise ResultSetError(f"{source}: cannot be read ({error})") from error
    try:
        try:
            info = os.fstat(descriptor)
        except OSError as error:
            raise ResultSetError(
                f"{source}: cannot be read ({error})"
            ) from error
        if not stat.S_ISREG(info.st_mode):
            raise ResultSetError(
                f"{source}: is not a regular file; it was not read"
            )
        if info.st_size > MAX_RESULT_FILE_BYTES:
            raise ResultSetError(
                f"{source}: is {info.st_size} bytes, over the "
                f"MAX_RESULT_FILE_BYTES limit of {MAX_RESULT_FILE_BYTES}; it "
                "was not read"
            )
        # One byte more than the cap, so that a file which grew after the
        # ``fstat`` - or a device that reports a size of zero and then yields
        # data forever - is detected by the count rather than by exhausting
        # the parent.
        budget = MAX_RESULT_FILE_BYTES + 1
        chunks: list[bytes] = []
        try:
            while budget > 0:
                chunk = os.read(descriptor, min(budget, _READ_CHUNK_BYTES))
                if not chunk:
                    break
                chunks.append(chunk)
                budget -= len(chunk)
        except OSError as error:
            raise ResultSetError(
                f"{source}: cannot be read ({error})"
            ) from error
    finally:
        os.close(descriptor)
    raw = b"".join(chunks)
    if len(raw) > MAX_RESULT_FILE_BYTES:
        raise ResultSetError(
            f"{source}: yielded more than the MAX_RESULT_FILE_BYTES limit of "
            f"{MAX_RESULT_FILE_BYTES} bytes; it was not read in full"
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        # Decoded explicitly, and caught: a ``UnicodeDecodeError`` *is* a
        # ``ValueError``, so it reaches neither the ``OSError`` clauses above
        # nor the JSON one below, and it used to escape this function uncaught
        # - which let a corrupt shard bypass dead-worker handling entirely.
        raise ResultSetError(f"{source}: is not valid UTF-8 ({error})") from error
    try:
        document = json.loads(text)
    except ValueError as error:
        raise ResultSetError(f"{source}: is not valid JSON ({error})") from error
    except RecursionError as error:
        # json.loads is recursive, so deeply nested input fails here rather
        # than in any validator.  This is the depth limit; a counter in the
        # validator would never be reached.
        raise ResultSetError(
            f"{source}: is nested too deeply to parse ({error})"
        ) from error
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


def _mapping_sequence(value: Any) -> list[JsonDict]:
    """Return the mappings in ``value``, tolerating anything else.

    The merge must not raise on a malformed document - it has no way to name
    the shard responsible, and an exception escaping it surfaces as an
    undocumented exit instead of the dead-shard outcome the exit table
    specifies.  So a ``features`` or ``elements`` value that is not a list
    contributes nothing rather than raising a :class:`TypeError`, and a
    non-mapping entry inside one is skipped.  A document that reached here
    through :func:`load_result_set` cannot be malformed at all; this keeps the
    hand-built path safe too.

    Args:
        value: A value that should be a list of JSON objects.

    Returns:
        Its mappings, in order, or ``[]`` when it is not a list or tuple.
    """
    if not isinstance(value, (list, tuple)):
        return []
    return [entry for entry in value if isinstance(entry, dict)]


def _element_line(element: JsonDict) -> int:
    """Return an element's line as a sortable integer.

    Args:
        element: A Background occurrence or a scenario element.

    Returns:
        Its ``line``, or ``0`` when the value is absent or is not a number -
        ``int("twelve")`` raises, and the merge's sort must not.  A shard that
        reached the merge through :func:`load_result_set` always carries a real
        integer here; this only guards the hand-built path.
    """
    line = element.get("line")
    if isinstance(line, bool) or not isinstance(line, (int, float)):
        return 0
    return int(line)


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
            return _element_line(element)
    return _element_line(unit[0]) if unit else 0


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


def _feature_identity(feature: Any) -> str:
    """Name a feature for a merge diagnostic, using its source path only.

    The merge's counterpart of :func:`_source_identity`, and it exists for the
    same reason: a diagnostic must be able to say *which* feature failed
    without quoting anything that carries runtime data.  A feature's ``path``
    and ``uri`` are written in the repository and name a file; a scenario or
    step name inside it is substituted Gherkin text, and ``Login.feature``'s
    Examples table substitutes plaintext credentials into it.  So only the two
    path keys are ever read here.

    Args:
        feature: A feature object, or anything at all - this runs on the path
            where the feature has already proved unusable.

    Returns:
        The feature's ``path``, falling back to its ``uri``, escaped by
        :func:`_log_safe_text` so the result is one line and safe to log, or
        ``"<unidentified feature>"`` when it carries neither.  Never raises.
    """
    try:
        if isinstance(feature, dict):
            for key in ("path", "uri"):
                value = feature.get(key)
                if isinstance(value, str) and value.strip():
                    return _log_safe_text(value)
    except Exception:  # pragma: no cover - defence in depth
        logger.debug("A feature identity could not be built", exc_info=True)
    return "<unidentified feature>"


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
    * **Integrity is conjunctive.** ``complete`` is true only if every input
      says so, and ``collection_errors`` is their concatenation in input order,
      capped at :data:`MAX_COLLECTION_ERRORS`.  A merged document is therefore
      never more complete than the least complete shard that went into it.
      Through :func:`load_result_set` this cannot arise - an incomplete shard
      is refused before it reaches here - so the rule exists for the
      hand-built path and for a caller that merges documents it built itself.

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

        **It also never raises.** A malformed document contributes whatever
        parts of it are well-formed and nothing else: a non-list ``features``
        or ``elements``, a non-mapping entry in either, or an unusable ``line``
        is skipped rather than propagated as a :class:`TypeError` or
        :class:`ValueError`.  That matters because the caller's ``finally`` is
        a cleanup block, not a handler: an exception escaping this function
        produced an undocumented exit instead of the exit table's dead-shard or
        empty-merge outcome.

        The promise extends to the **copy itself**, which is the one operation
        here that reads a value's whole shape rather than its type.
        :func:`copy.deepcopy` is recursive, so a feature carrying a deeply
        nested value raises :class:`RecursionError` from inside it - and a
        hand-built document reaches this function without passing
        :func:`load_result_set`'s budget, so it can carry one.  Such a feature
        is therefore **skipped**: the merged document records it in
        ``collection_errors``, its ``complete`` becomes ``False`` so no
        consumer reads the result as a whole run, and the failure is logged at
        ``ERROR`` naming the feature's *path* only.  The run-level ``metadata``
        block is copied under the same guard, and for the same reason.  Nothing
        about the function's purity or determinism changes: the guard reads no
        clock and the outcome depends only on the inputs.
    """
    documents = [document for document in sets if isinstance(document, dict)]

    # Failures this merge had to skip.  They are concatenated onto the inputs'
    # own ``collection_errors`` below and make the merged document incomplete,
    # which is the only honest description of a merge that dropped a feature.
    merge_errors: list[JsonDict] = []

    merged_features: dict[str, JsonDict] = {}
    for document in documents:
        version = document.get("schema_version")
        if version is not None and version != SCHEMA_VERSION:
            # The version is a value out of the input document, and this
            # function accepts hand-built ones that never passed the
            # validator, so it is escaped and bounded like every other
            # document-sourced fragment that reaches a log line.
            logger.warning(
                "Merging a result set of schema version %r into version %d",
                _log_safe_text(version),
                SCHEMA_VERSION,
            )
        for feature in _mapping_sequence(document.get("features")):
            try:
                key = _feature_key(feature)
                elements = [
                    copy.deepcopy(element)
                    for element in _mapping_sequence(feature.get("elements"))
                ]
                existing = merged_features.get(key)
                if existing is None:
                    # The feature's own metadata, copied *without* its element
                    # tree: the elements were already copied above, and copying
                    # them again here only to overwrite the copy on the next
                    # line doubled the peak memory of every merge for nothing.
                    merged = {
                        name: copy.deepcopy(value)
                        for name, value in feature.items()
                        if name != "elements"
                    }
                    merged["elements"] = elements
                    merged_features[key] = merged
                else:
                    existing["elements"].extend(elements)
            except Exception as error:
                # Every statement above is a copy or a dictionary read, so the
                # only way in here is a value whose *shape* defeats
                # copy.deepcopy - a deeply nested one, which raises
                # RecursionError - or a mapping whose iteration itself fails.
                # The feature is dropped rather than the merge, because this
                # function's caller has a cleanup ``finally`` and no handler:
                # raising would produce an undocumented exit instead of the
                # exit table's outcome.
                identity = _feature_identity(feature)
                logger.error(
                    _MERGE_FEATURE_FAILURE_MESSAGE, identity, exc_info=True
                )
                merge_errors.append(
                    {
                        "event": f"merge:{identity}",
                        "error": _describe_exception(error),
                    }
                )

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
        (
            document["metadata"]
            for document in documents
            if isinstance(document.get("metadata"), dict) and document["metadata"]
        ),
        None,
    )
    try:
        metadata_copy = copy.deepcopy(metadata) if metadata else {}
    except Exception as error:
        # The same vector as a feature's copy, and the same answer: the block
        # is diagnostic, so losing it costs a metadata table on two HTML pages
        # rather than the whole merge.
        logger.error(_MERGE_METADATA_FAILURE_MESSAGE, exc_info=True)
        merge_errors.append(
            {"event": "merge:metadata", "error": _describe_exception(error)}
        )
        metadata_copy = {}

    collection_errors = [
        dict(entry)
        for document in documents
        for entry in _mapping_sequence(document.get("collection_errors"))
    ]
    collection_errors.extend(merge_errors)
    collection_errors = collection_errors[:MAX_COLLECTION_ERRORS]

    return {
        "schema_version": SCHEMA_VERSION,
        "started_at": started_at,
        # The latest shard's stamp, rather than a fresh reading: this function
        # must not touch a clock, or it could not be verified deterministic.
        "generated_at": max(generated_stamps) if generated_stamps else None,
        "dry_run": any(bool(document.get("dry_run")) for document in documents),
        "tag_expression": tag_expression,
        "metadata": metadata_copy,
        # Conjunctive over the inputs *and* over this merge's own failures: a
        # merge that skipped a feature produced less than its inputs held, and
        # saying otherwise is exactly the silent loss the marker exists to
        # prevent.
        "complete": all(
            bool(document.get("complete", True)) for document in documents
        )
        and not merge_errors,
        "collection_errors": collection_errors,
        "features": ordered_features,
    }
