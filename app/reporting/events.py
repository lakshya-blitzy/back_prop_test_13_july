"""The internal result schema and the behave event collector.

This module owns the intermediate result document every writer in
``app/reporting`` consumes, and it is the only place behave's event stream is
observed.  behave's native JSON cannot be that source (AAP 0.6): it carries no
per-scenario ``start_timestamp`` and no embeddings, records ``match.location``
as ``"features/steps/x.py:LINE"`` rather than a callable path, and reports
float-second durations.  No mapping recovers a field that was never captured,
so :class:`ResultCollectorFormatter` records a superset of what the writers
need and writes this document itself.  Source anchor: ``CukesRunner.java``,
whose Cucumber plugin list named the four artifacts this document feeds.

``behave.ini`` declares no formatter and no outfile, because a static file
cannot give each worker of the process pool its own output path:
``app/services/test_run_service.py`` passes ``-f``
:data:`FORMATTER_SCOPED_NAME` and ``-o <path>`` once per worker, with the path
from :mod:`app.utils.paths` -- the only intra-package import here, and the
owner of every path this module touches.

The document is plain JSON-serialisable ``dict``/``list`` data at every level,
built by :func:`new_result_set`, :func:`new_feature`, :func:`new_element`,
:func:`new_step` and :func:`new_hook_entry`, which are the single owners of
its key-presence rules and document them individually.  The conventions the
writers depend on: ``duration`` is always an integer of nanoseconds and ``0``
is recorded faithfully, whether to emit the key being the writer's decision;
``result.status`` is behave's own normalised name, with the dry-run and
tag-filter mappings left to the writers; a step ``keyword`` keeps the JVM's
trailing space, while a Background occurrence carries no ``id``, ``tags``,
``start_timestamp`` or ``after``; and every timestamp is
:func:`format_timestamp`'s ``YYYY-MM-DDTHH:MM:SS.mmmZ``.

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
      "tag_expression": "@Smoke" | None,  # diagnostic; see below
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

``tag_expression`` decides nothing -- the filter has already been applied by
the time a shard exists, the JSON artifact has no field for it and neither HTML
family prints it.  It is retained for **one** consumer,
:func:`_merged_tag_expression`, which answers a question no other field can:
every worker of a run is launched with the same expression, so two shards
recording different ones are shards from two different runs, and that merge is
reported at ``WARNING`` instead of silently publishing one artifact set
describing two filters.

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
surgery on the other -- and both are required to be present and to agree,
``uri`` being ``path`` behind the ``file:`` scheme.  They are two spellings of
one identity read by different consumers (JSON copies the URI, PrettyReports
hashes it, the rerun manifest and the merge use the path), so a document
carrying only one, or two that disagree, publishes one feature under three
different names.

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
non-selected scenarios; a Background occurrence inherits its scenario's value,
and the run-level ``started_at`` is the earliest **selected** scenario's stamp,
so that it names the same run the artifacts describe.

The element list is a list of **units**, not of independent elements: each
Background occurrence is emitted for the scenario that immediately follows it
and shares its ``selected`` value.  :func:`element_units` is the one grouping
every consumer uses and :func:`_check_element_units` refuses a list that is
not units, because four consumers grouping it themselves disagreed about a
malformed one -- one dropped a stray occurrence, one attached it to the next
scenario, one counted it as a test case.

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
  to fix.  The substituted text is stored **redacted** -- see "The redaction
  boundary" below -- because for a Login or Logout row the substitution *is*
  the credential.
* ``match.location`` is the resolved step function's dotted Python path with no
  parentheses and no parameter types (plan deviation 8: no analogue of Java's
  ``com.testinium...Crm.method(java.lang.String)`` exists, so the field's shape
  and role are preserved rather than its content).  ``match`` is ``{}`` for an
  undefined step -- the JVM emits ``location`` only when the status is not
  undefined.
* ``matched`` and ``match.location`` state **one** fact and are required to
  agree: a step is matched if and only if it carries a non-empty location, and
  an unmatched step's ``match`` is empty of both location and arguments.  They
  are read separately downstream -- the JSON writer's dry-run status rule
  trusts ``matched`` while the Steps overview groups by ``location`` -- so a
  document allowed to disagree published two different runs.
* ``match.arguments`` appears only when the step took at least one parameter.
  An argument is ``{}`` -- the JVM's shape for a parameter with no value -- or
  carries **both** ``val`` and ``offset``, with the offset a position inside
  the step ``name``.
* ``duration`` is always an **integer of nanoseconds** (behave reports float
  seconds) and never negative; ``0`` is recorded faithfully, and whether to
  emit the key is the writer's decision -- the JVM emits it only when
  non-zero, whatever the status.
* ``result.status`` is behave's normalised status name.  The collector applies
  no dry-run and no tag-filter mapping; the writers own those rules.
* ``result.error_message`` is the assertion's own message followed by the
  Python traceback (AAP deviation 16), stored through
  :func:`sanitize_failure_text`: LF-normalised, with classified values masked,
  frame paths relativised and the whole text bounded to
  :data:`MAX_FAILURE_TEXT_CHARS`.  The key is absent when there is no failure.

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
passing hook that attached nothing contributes **no** entry, which is what
keeps the emitted document identical in shape to the reference, where the
teardown hook never ran and no element carries an ``after`` array at all --
inventing entries would change a frozen artifact.

The converse matters just as much, and it is a rule about the *writer*: an
entry that records a **non-passing** outcome is published whether or not it
carries an attachment.  ``cucumber_json._build_after`` used to emit only
entries carrying an embedding, so a teardown that failed without managing a
screenshot -- a dead session, which is precisely when a teardown fails -- was
recorded here, rendered by both HTML writers, and silently absent from the
JSON the Jenkins publisher reads.  A hook failure is a scenario whose driver
may not have been quit and whose evidence may not have been captured, so the
artifact that machines read is the last place it may go missing.
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

The redaction boundary
----------------------
Two of the fields above are worker-controlled text that a report then keeps
for as long as the build is archived, and both are classified **on the way
in** rather than on the way out.  The rule is the review's, findings SEC2-F03
(CWE-532/359/200) and SEC2-F20 (CWE-532/200):

* **A step's ``name`` and its ``match.arguments[].val``.**  The suite's login
  phrases are ``User enters "<username>" username`` and ``User enters
  "<password>" password`` [Login.feature:15-16], so for a Login or Logout
  ``Examples`` row the substituted step text is an account name or a password
  and ``match.arguments`` carries the same substring a second time.
  :func:`redact_step_text` masks a credential-bearing span in both at once,
  keeping the ``name[offset:offset + len(val)] == val`` contract exact by
  shifting every later offset.  It considers the argument spans **and** every
  quoted run no argument indexes, so a value no entry represents is classified
  too, and it scopes each span's adjacency search to the text between that
  span and its neighbours, so a keyword classifies the value it governs and
  not the one beside it.  It is applied in :func:`new_step`, which every
  stored step passes through, and again in
  :meth:`ResultCollectorFormatter._apply_match` once behave's own argument
  spans are known.
* **``result.error_message``.**  :func:`sanitize_failure_text` relativises the
  frame paths - a frame's quoted path as a unit, so a workspace directory
  containing a space is relativised rather than half-recognised - masks
  classified values and bounds the length.  :func:`_failure_text` returns
  through it on every path, and :func:`new_step` and :func:`new_hook_entry`
  store a caller's own text through it.

Both rules are **idempotent**, which is what lets
``app/reporting/cucumber_json.py`` apply them again at the publish boundary
without changing a value a producer already masked -- the writer treats worker
JSON as untrusted input, and re-classifying is cheaper than trusting it.

What is deliberately **not** redacted: the ten ``features/*.feature`` files,
whose ``Examples`` credentials AAP 0.8 states are pre-existing fixture data
for an external test instance that no agent may redact, parameterize or
rotate; an embedding's ``data``, which is the screenshot itself; and
``match.location``, an element's ``name``, ``description``, ``tags``, ``id``,
a feature's ``uri``, every keyword, every status and every duration, none of
which carries a credential and all of which are frozen parity values.

Timestamps
----------
``start_timestamp``, ``started_at`` and ``generated_at`` all use
:func:`format_timestamp`, which emits exactly ``YYYY-MM-DDTHH:MM:SS.mmmZ``.
That is the JVM generator's ``yyyy-MM-dd'T'HH:mm:ss.SSSXXX`` pattern with
``withZone(ZoneOffset.UTC)``, where ``XXX`` renders UTC as a literal ``Z``, and
it is also the format ``app/templates/index.html`` expects for its
``modified_iso``.

That spelling is **enforced, not assumed**: :data:`TIMESTAMP_PATTERN` fixes it,
:func:`parse_timestamp` reads it back, and :func:`_check_timestamp` refuses
any other string at ingress -- ``None`` alone remains admissible, meaning "no
scenario ran" or "not stamped yet".  The enforcement is what lets the merge
order by **parsed instant** while returning the spelling it was given, so a
page shows the value the JSON artifact carries; without it a malformed stamp
became a literal on one surface, a dropped value in
``app/reporting/aggregation.py``, and a string comparison in the merge.

Writing a document
------------------
Both of this module's writes - the formatter's own output and
:func:`dump_result_set` - go through
:func:`app.utils.paths.open_artifact_write`, and nothing here calls the
builtin ``open`` or lets behave open a file by name.  The reason is what a
pathname is worth here: a worker's output sits under
:func:`app.utils.paths.workers_dir`, which a ``--no-clean`` run leaves in
place, so the name is not under this process's sole control - and any route
that verifies a name, releases what it verified, and then re-resolves the same
name in a second call (behave's ``StreamOpener`` among them) truncates
whatever that name means by then.  A symbolic link or a junction planted there
therefore redirects the write outside the artifact root and destroys the file
it points at (CWE-367/CWE-59/CWE-22).  The path authority creates and
verifies every owned directory component under a held directory descriptor,
refuses a symlinked or hard-linked destination, and truncates only after the
object it opened is established.  It also applies the mode policy -
``0o600`` files under ``0o700`` directories - which these documents need in
their own right: they carry step arguments substituted from the Examples
tables, and failure text (CWE-732/CWE-359).  Its refusals are
:class:`app.utils.paths.ArtifactPathError`, an :class:`OSError`, so they land
in the writer-failure exit class AAP 0.4.1 already defines and no caller
learns a new exception type.

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
at every level**.

Types are not the whole of it, because a field of the right type and the wrong
*value* splits the artifacts just as effectively.  So the rules the validator
also applies are the ones the writers rely on and none of them can check
alone: every source line and tag column is at least 1 (Gherkin numbers from
one, and a rerun entry pointing at line 0 selects nothing while still looking
like a manifest); every duration and argument offset is at least 0; a
scenario carries a non-empty ``id``; a feature's ``uri`` and ``path`` agree; a
step's ``matched`` and ``match.location`` agree; the element list is a
sequence of Background/scenario units; a feature tag carries the long shape
and a scenario tag the short one, exactly; and the three timestamps carry the
one spelling :data:`TIMESTAMP_PATTERN` fixes.  The rule is not pedantry: a step object of ``{}`` used to
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

* **the file cap**, :data:`MAX_RESULT_FILE_BYTES`, which is the whole-file
  bound and the only limit applied before parsing;
* **the run budget**, :class:`RunResultBudget`, which is the same three
  questions asked of a *merge* rather than a file -- total bytes, total nodes
  and how many shards -- because the parent holds every shard at once and 87
  individually valid files can still sum to tens of gibibytes;
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
from typing import IO, Any, Final

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

# The credential vocabulary has exactly one owner in this port, and it is
# ``app/logging_config.py``: it compiles the patterns, fixes the placeholder
# text and fixes the truncation notice.  This module classifies *report*
# values rather than log lines, but a second copy of those patterns would
# drift from the first on the day one of them gained a keyword, so the
# authority is imported rather than reimplemented.  The import is safe in
# both directions that matter: ``app/logging_config.py`` imports only
# ``logging``, ``re``, ``sys`` and ``typing``, so it adds no dependency to a
# worker process and cannot close an import cycle back onto this package.
from app.logging_config import (
    REDACTION_PLACEHOLDER,
    TRUNCATION_SUFFIX_TEMPLATE,
    redact_sensitive,
)
from app.utils.paths import (
    FILE_URI_SCHEME,
    normalize_feature_uri,
    open_artifact_write,
)

__all__ = [
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
    "new_element",
    "new_feature",
    "new_hook_entry",
    "new_result_set",
    "new_step",
    "parse_timestamp",
    "record_hook_result",
    "redact_step_text",
    "run_metadata",
    "sanitize_failure_text",
    "scenario_element_id",
    "scenario_tag",
    "step_keyword",
    "widen_quoted_span",
]

logger = logging.getLogger(__name__)

JsonDict = dict[str, Any]

#: The whole intermediate document -- what :func:`new_result_set` returns and
#: what every writer consumes.
ResultSet = dict[str, Any]

#: Version of the document shape this module defines.  Bump it only for a
#: change an existing reader could not survive; adding an optional key is not
#: such a change, which is why ``complete`` and ``collection_errors`` are
#: optional at this version.  :func:`load_result_set`
#: **rejects** any other version: a document this build cannot read in full is
#: a dead shard the parent must name, not a warning it can continue past,
#: because continuing past it loses results silently.
SCHEMA_VERSION: Final[int] = 1

FORMATTER_NAME: Final[str] = "resultcollector"

#: The ``module:Class`` string behave resolves through
#: ``behave.formatter._registry.load_formatter_class`` / ``parse_scoped_name``.
#: ``app/services/test_run_service.py`` passes this verbatim to ``-f`` so that
#: the scoped name has exactly one definition.
FORMATTER_SCOPED_NAME: Final[str] = "app.reporting.events:ResultCollectorFormatter"

#: Encoding every document this module writes is encoded in, and the fallback
#: :meth:`ResultCollectorFormatter._open_secure_stream` uses when behave's
#: stream opener reports none.  behave's own ``StreamOpener`` computes the same
#: value through ``select_best_encoding()``, so the fallback is reached only
#: for an opener that carries no ``encoding`` attribute at all.
_OUTPUT_ENCODING: Final[str] = "utf-8"

#: Newline translation every document this module writes uses: ``"\n"`` written
#: through unchanged on every platform, so a shard written on Windows is
#: byte-identical to one written on Linux.  Durations and timestamps vary by
#: construction; the document's structure must not.
_OUTPUT_NEWLINE: Final[str] = "\n"

#: Dotted path recorded for an attachment that arrives through behave's own
#: ``context.attach()`` route, which carries no reference to the hook that
#: called it.  ``features/environment.py`` owns the scenario lifecycle and is
#: where the port's port of ``Hooks.teardownScenario`` lives, so this is the
#: hook that produced the attachment.  A caller that knows better can pass its
#: own location to :func:`attach_to_current_scenario`.
DEFAULT_AFTER_HOOK_LOCATION: Final[str] = "features.environment.after_scenario"

ELEMENT_TYPE_BACKGROUND: Final[str] = "background"
ELEMENT_TYPE_SCENARIO: Final[str] = "scenario"

FEATURE_KEYWORD: Final[str] = "Feature"

BACKGROUND_KEYWORD: Final[str] = "Background"

#: The ``type`` every feature-level tag carries, as the reference report
#: spells it.  A single owner for the literal, because :func:`feature_tag`
#: writes it, :func:`_validate_tag` requires exactly it at feature level, and
#: ``app/reporting/cucumber_json.py`` copies it into the published artifact.
TAG_TYPE: Final[str] = "Tag"

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

# The parent must survive a file that is *not* what this module writes -- a
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

#: Largest total number of shard **bytes** one merge will read, across every
#: worker file it loads.  :data:`MAX_RESULT_FILE_BYTES` bounds a single file
#: and says nothing about their sum, and the sum is what the parent actually
#: holds: ``app/services/test_run_service.py`` loads every live shard into a
#: list before :func:`merge_result_sets` sees any of it, so 87 files each just
#: inside the per-file cap would have the parent hold about 21.75 GiB of
#: parsed documents (CWE-400).
#:
#: The aggregate is the *same* number as the per-file cap, and that is the
#: point rather than an oversight: sharding divides one run's results across
#: its workers, so the sum over every shard of a run is the size of that one
#: run - and the per-file arithmetic above already sized 256 MiB at roughly
#: twice the largest legitimate single-worker run (~120 MB with every one of
#: the 87 scenarios carrying a 1 MiB screenshot).  A run whose shards total
#: more than one worst-case run did not come from one run.
MAX_RUN_RESULT_BYTES: Final[int] = MAX_RESULT_FILE_BYTES

#: Largest total number of parsed JSON **nodes** one merge will accept, across
#: every shard.  The counterpart of :data:`MAX_DOCUMENT_NODES` for the same
#: reason the byte cap has one: the per-document limit bounds each walk, and
#: the merge's ``copy.deepcopy`` of every feature costs the sum.  The whole
#: suite collected into one shard measures 13,981 nodes and a sharded run adds
#: only each shard's own envelope, so 250,000 is roughly eighteen times the
#: largest legitimate run.
MAX_RUN_RESULT_NODES: Final[int] = MAX_DOCUMENT_NODES

#: Most shard files one merge will load.  A run's shard count is its worker
#: count, which ``--workers`` bounds at the scenario count, so the honest
#: ceiling is the suite's 87 scenarios; 1,000 leaves room for a much larger
#: suite while refusing a worker directory that has accumulated the
#: intermediates of many runs.
MAX_RUN_RESULT_DOCUMENTS: Final[int] = 1000

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

#: The one spelling this schema's three timestamps are allowed to carry:
#: ``YYYY-MM-DDTHH:MM:SS.mmmZ``, millisecond precision, three fractional
#: digits always, a literal ``Z``.  It is what :func:`format_timestamp`
#: produces, what :func:`parse_timestamp` accepts, and what
#: :func:`_check_timestamp` enforces at ingress on ``started_at``,
#: ``generated_at`` and every scenario's ``start_timestamp``.
#:
#: Pinned as a pattern rather than left to a parser's tolerance because three
#: surfaces read these values and each one fails differently on a value it
#: cannot understand: ``app/templates/index.html`` and the two HTML writers
#: *display* the string, ``app/reporting/aggregation.py`` *parses* it and
#: drops what it cannot read - so a malformed value silently becomes "no run
#: start" on one page and a literal on another - and the merge *orders* by it.
#: Refusing it here is the only place the shard can still be named.
TIMESTAMP_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z"
)

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

_UNKNOWN_METADATA_VALUE: Final[str] = ""

# --------------------------------------------------------------------------- #
# The redaction boundary (review findings SEC2-F03 and SEC2-F20)
#
# Two fields of the document defined above are worker-controlled text that a
# report then keeps forever: a step's ``name`` together with its
# ``match.arguments[].val``, and ``result.error_message``.  Both are
# classified *on the way in*, by :func:`redact_step_text` and
# :func:`sanitize_failure_text`, and both classifications are idempotent so
# that the writer in ``app/reporting/cucumber_json.py`` can apply them again
# at the publish boundary without changing what a producer already masked.
#
# The keyword sets, the placeholder and the truncation notice all come from
# ``app/logging_config.py``; what lives here is the *span-level* rule, which
# a text-level sanitizer cannot express - see :func:`redact_step_text`.
# --------------------------------------------------------------------------- #

#: Credential *field* names, for the span classifier in
#: :func:`redact_step_text`.  The vocabulary mirrors
#: ``app/logging_config.py``'s adjacency set - the one its category (f) rule
#: uses - plus the two spellings of "email", because this suite's account
#: names *are* email addresses [Login.feature:22-36] and a step phrase can
#: name the field either way.  Three properties are deliberate:
#:
#: * every alternative is anchored with ``\b``, so ``auth`` does not match
#:   inside "author" and ``pwd`` does not match inside a longer identifier;
#: * bare ``user`` is **excluded**, exactly as it is in the sanitizer, because
#:   ``"Pipeline" should be displayed to user`` is ordinary business phrasing
#:   and ``User can change any user's information like "Test2"`` must keep its
#:   values - over-redaction is a failure mode too;
#: * the separators inside a compound name are permissive (``api key``,
#:   ``api_key``, ``api-key``) because a step phrase is prose rather than an
#:   identifier.
_SPAN_CREDENTIAL_KEYWORD_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:user\s?names?|logins?|passwords?|passwd|pwd|passphrases?"
    r"|secrets?|tokens?|api[\s_-]?keys?|authorizations?|auth"
    r"|credentials?|session[\s_-]?ids?|cookies?|e[\s_-]?mails?)\b",
    re.IGNORECASE,
)

#: How much text on each side of a span :func:`redact_step_text` searches for
#: one of those keywords.  32 characters is measured against the phrases that
#: actually carry a credential in this suite: ``User enters "<username>"
#: username`` puts the keyword 1 character after the span and 12 characters
#: before it [Login.feature:15-16], and the longest such gap in the ten
#: feature files is well inside 32.  Widening it would start to classify a
#: span by a keyword belonging to a different clause of the same sentence,
#: which is the over-redaction this window exists to bound.
#:
#: The window is a **cap** on the search and not the search itself: the
#: adjacency scan is bounded first by the neighbouring spans, so a keyword
#: that governs a *different* value cannot classify this one however close the
#: two sit.  ``User enters "public" tag and "<password>" password`` is the
#: measured case - ``password`` is 18 characters from ``"public"``, well inside
#: 32, and governs the span after it - and a classifier keyed on the raw
#: distance alone masks business data in any phrase that merely mentions a
#: credential field.  See :func:`_span_is_sensitive`.
_SPAN_KEYWORD_WINDOW: Final[int] = 32

#: A quoted run in either quote style, the candidate-span source that stands
#: beside a step's ``match.arguments`` rather than behind it: it covers every
#: value no argument entry indexes - an undefined step, a never-executed step,
#: a hand-built document that recorded no arguments, and the partially indexed
#: list a worker file or a fixture can carry.  Non-greedy by construction (the
#: character classes exclude the closing quote), so two quoted values on one
#: line stay two spans.
_QUOTED_RUN_RE: Final[re.Pattern[str]] = re.compile(r"\"[^\"]*\"|'[^']*'")

#: Longest ``result.error_message`` this module stores, in characters.  The
#: arithmetic: a Python traceback for a failing step in this suite is five to
#: eight frames of two lines each at roughly 100 characters, about 1 KB, and
#: the longest legitimate failure text measured in the reference - a Selenium
#: ``NoSuchElementException`` message with its capability dump plus a
#: traceback - is under 2.5 KB.  8 KiB therefore leaves more than three times
#: the headroom a real diagnostic needs while capping the pathological case,
#: an exception whose ``str()`` is a page-source dump: 469 steps at 8 KiB is
#: about 3.7 MiB, which keeps a shard three orders of magnitude below
#: :data:`MAX_RESULT_FILE_BYTES` and every string inside
#: :data:`MAX_STRING_LENGTH`.  What is dropped is never silently dropped -
#: :data:`TRUNCATION_SUFFIX_TEMPLATE` records the exact character count.
MAX_FAILURE_TEXT_CHARS: Final[int] = 8 * 1024

#: The truncation notice, recognised at the end of a text that has already been
#: bounded.  Derived from :data:`TRUNCATION_SUFFIX_TEMPLATE` rather than spelled
#: out a second time, so the two cannot drift: the template's own text is
#: escaped and its ``{dropped}`` field becomes the capture group.  Anchored at
#: the end, so a notice quoted in the middle of a message is body text and not
#: a claim about this text.  :func:`_bound_failure_text` reads the count back
#: out of it, which is what makes bounding idempotent across the collector and
#: the writer.
_TRUNCATION_NOTICE_RE: Final[re.Pattern[str]] = re.compile(
    re.escape(TRUNCATION_SUFFIX_TEMPLATE).replace(
        re.escape("{dropped}"), r"(?P<dropped>\d+)"
    )
    + r"\Z"
)

#: What a shortened absolute path is rebuilt behind in
#: :func:`sanitize_failure_text`, so a reader can tell "this path was cut" from
#: "this path was relative".
_SHORTENED_PATH_MARKER: Final[str] = "..."

#: How many trailing components of a shortened absolute path are kept.  Two is
#: what makes a frame identifiable - ``.../steps/login_steps.py`` names the
#: file and the package it sits in - without disclosing the account name,
#: workspace layout or virtual-environment location the leading components
#: carry (CWE-200).
_SHORTENED_PATH_COMPONENTS: Final[int] = 2

#: An absolute POSIX path of at least two components.  The lookbehind is the
#: whole reason this can run over Selenium text safely: it refuses a ``/``
#: preceded by a word character, another ``/``, a colon, a dot or a dash, so
#: an XPath (``//input[@id='x']``, ``//div/span``), a URL
#: (``https://host/path``) and a relative path (``features/steps/x.py``) are
#: all left alone, while ``File "/home/ci/work/app.py"`` - preceded by a
#: quote - is matched.  The component class excludes every bracket, quote and
#: space, so a match stops at the end of the path rather than running into the
#: sentence around it.
_ABSOLUTE_POSIX_PATH_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![\w/:.\-])/(?:[\w.+-]+/)+[\w.+-]+"
)

#: An absolute Windows path of at least two components below its drive.  The
#: suite runs on Windows as well as Linux (AAP 0.8), so a worker's traceback
#: can carry either shape, and a drive-qualified path discloses the same
#: account name and workspace layout the POSIX form does.
_ABSOLUTE_WINDOWS_PATH_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![\w\\])[A-Za-z]:\\(?:[\w.+-]+\\)+[\w.+-]+"
)

#: The path of a CPython traceback frame, matched as a *unit*.  A frame line is
#: ``  File "<path>", line N, in <name>``, so the quoted run after ``File `` is
#: one complete path however many spaces, brackets or other characters it
#: contains -- which is the case the two unquoted patterns above cannot cover,
#: because their component classes exclude a space so that a match stops at the
#: end of a path rather than running into the prose around it.  A checkout
#: under ``/home/jane doe/`` or ``C:\\CI Agent\\job 42\\`` therefore had its
#: workspace topology published while the shortening rule mangled the prefix in
#: front of it (review finding SEC2-F20): only the leading ``/home/jane`` was
#: recognised as a path, leaving the account name and the rest of the layout in
#: the text.  The lookbehind is fixed-width and the class excludes the closing
#: quote and the newline, so the match is exactly one frame's path and cannot
#: run past it; the group is the path alone, which is what
#: :func:`_relativize_frame_path` rewrites.
_TRACEBACK_FRAME_PATH_RE: Final[re.Pattern[str]] = re.compile(
    r'(?<=\bFile ")[^"\n]+(?=")'
)

#: A drive-qualified Windows path prefix -- ``C:\\`` or ``C:/``.  Used to
#: recognise an absolute frame path rather than to rewrite one, so unlike
#: :data:`_ABSOLUTE_WINDOWS_PATH_RE` it says nothing about the components that
#: follow: inside a ``File "..."`` frame the quoted run has already delimited
#: them.
_WINDOWS_DRIVE_PREFIX_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z]:[\\/]")

#: The prefix of a Windows UNC path, ``\\\\host\\share\\...``.  A UNC frame
#: discloses a file server and a share name as well as the layout below them,
#: so it is shortened exactly as a drive-qualified one is.
_WINDOWS_UNC_PREFIX: Final[str] = "\\\\"

#: This repository's root, derived from this module's own location -
#: ``<root>/app/reporting/events.py`` - rather than from the process's working
#: directory, so that a traceback frame inside the port reads ``File
#: "features/steps/login_steps.py"`` whatever directory a worker was launched
#: from.  Resolved once, at import: the value cannot change during a run and
#: :func:`sanitize_failure_text` must not touch the filesystem, because
#: ``app/reporting/cucumber_json.py``'s ``build_cucumber_json`` is documented
#: as reading none of it.
_REPOSITORY_ROOT: Final[str] = str(Path(__file__).resolve().parents[2])


def _initial_working_directory() -> str:
    """Read the working directory once, for the path-relativising rule.

    A worker is launched from the repository root by
    ``app/services/test_run_service.py``, so this is normally
    :data:`_REPOSITORY_ROOT` again; it is captured separately for the case
    where it is not - an editable install run from elsewhere, or a checkout
    reached through a symlinked parent - because a frame under the working
    directory carries the same workspace topology a frame under the root does.

    Returns:
        The absolute working directory, or ``""`` when it cannot be read - a
        deleted or unreadable current directory raises :class:`OSError` from
        :meth:`Path.cwd`, and a failure to *improve* a diagnostic must not
        cost the diagnostic.
    """
    try:
        return str(Path.cwd().resolve())
    except OSError:  # pragma: no cover - an unreadable working directory
        return ""


#: The working directory as it was when this module was imported.  See
#: :func:`_initial_working_directory` for why it is read once and why it is
#: read at all.
_INITIAL_WORKING_DIRECTORY: Final[str] = _initial_working_directory()


class ResultSetError(RuntimeError):
    """Raised when a result-set file is absent, unreadable or not this schema.

    :func:`load_result_set` raises this and nothing else, so that
    ``app/services/test_run_service.py`` can name the offending shard on stderr
    and apply the plan's exit table instead of inferring intent from a bare
    :class:`OSError` or :class:`json.JSONDecodeError`.  The originating
    exception is always chained, so the cause survives for a log.
    """


class RunResultBudget:
    """The resource budget for **one merge**, charged shard by shard.

    :data:`MAX_RESULT_FILE_BYTES`, :data:`MAX_DOCUMENT_NODES` and the rest
    bound one file.  Their product does not bound a run:
    ``app/services/test_run_service.py`` loads every live shard into a list
    before the merge reads any of it, so a run of 87 shards each just inside
    the per-file cap would require the parent to hold about 21.75 GiB - every
    file individually valid, the aggregate fatal (CWE-400, CWE-502).  One
    instance of this class is created per merge and passed to each
    :func:`load_result_set` call, which charges it the bytes it read and the
    nodes it parsed.

    The first shard that would take the run past a limit is refused with a
    :class:`ResultSetError`, exactly as an oversized single file is, so the
    caller's existing handling applies unchanged: that shard is named dead
    with the reason, the run's exit status follows the plan's dead-worker row,
    and the artifacts are still written from the shards that did load.  The
    alternative - failing the whole merge - would discard results the run
    genuinely produced.

    Charging happens in two steps because they cost differently: the bytes are
    charged **after the read and before the parse**, so a shard that would
    breach the run's byte budget is refused without paying for its
    :func:`json.loads`; the nodes are charged during the budget walk the
    validator already performs, so the run's node total costs no extra pass.

    Not thread-safe, and deliberately not: a merge collects its shards in one
    sequence in one process.  A caller that loads shards concurrently needs one
    budget per thread, and would then be bounding nothing run-wide.

    Args:
        max_bytes: Total shard bytes allowed, defaulting to
            :data:`MAX_RUN_RESULT_BYTES`.
        max_nodes: Total parsed nodes allowed, defaulting to
            :data:`MAX_RUN_RESULT_NODES`.
        max_documents: Most shard files allowed, defaulting to
            :data:`MAX_RUN_RESULT_DOCUMENTS`.

    Attributes:
        documents: How many shards have been charged so far.
        total_bytes: How many shard bytes have been charged so far.
        total_nodes: How many parsed nodes have been charged so far.
    """

    __slots__ = (
        "documents",
        "max_bytes",
        "max_documents",
        "max_nodes",
        "total_bytes",
        "total_nodes",
    )

    def __init__(
        self,
        *,
        max_bytes: int = MAX_RUN_RESULT_BYTES,
        max_nodes: int = MAX_RUN_RESULT_NODES,
        max_documents: int = MAX_RUN_RESULT_DOCUMENTS,
    ) -> None:
        self.max_bytes = max_bytes
        self.max_nodes = max_nodes
        self.max_documents = max_documents
        self.documents = 0
        self.total_bytes = 0
        self.total_nodes = 0

    def charge_document(self, source: str, byte_count: int) -> None:
        """Charge one shard's file size, and the shard itself.

        Args:
            source: The shard's path, for the rejection message.
            byte_count: How many bytes were read from it.

        Raises:
            ResultSetError: If this shard would take the run past
                :attr:`max_documents` or :attr:`max_bytes`.  The counters are
                left unchanged when it does, so a caller that treats the shard
                as dead and continues has an accurate total for the ones that
                did load.
        """
        if self.documents + 1 > self.max_documents:
            raise ResultSetError(
                f"{source}: is result file {self.documents + 1} of this merge, "
                f"over the MAX_RUN_RESULT_DOCUMENTS limit of "
                f"{self.max_documents}; it was not read"
            )
        if self.total_bytes + byte_count > self.max_bytes:
            raise ResultSetError(
                f"{source}: its {byte_count} bytes would bring this merge to "
                f"{self.total_bytes + byte_count} bytes, over the "
                f"MAX_RUN_RESULT_BYTES limit of {self.max_bytes}; it was not "
                "parsed"
            )
        self.documents += 1
        self.total_bytes += byte_count

    def charge_nodes(self, source: str, node_count: int) -> None:
        """Charge one shard's parsed node count.

        Args:
            source: The shard's path, for the rejection message.
            node_count: How many JSON nodes it parsed to.

        Raises:
            ResultSetError: If this shard would take the run past
                :attr:`max_nodes`.
        """
        if self.total_nodes + node_count > self.max_nodes:
            raise ResultSetError(
                f"{source}: its {node_count} JSON nodes would bring this "
                f"merge to {self.total_nodes + node_count}, over the "
                f"MAX_RUN_RESULT_NODES limit of {self.max_nodes}; it was not "
                "merged"
            )
        self.total_nodes += node_count


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


def parse_timestamp(text: Any) -> datetime | None:
    """Parse a contract timestamp back to the instant it names.

    The inverse of :func:`format_timestamp`, and deliberately **exact**: it
    accepts only the spelling that function emits,
    ``YYYY-MM-DDTHH:MM:SS.mmmZ``, which :data:`TIMESTAMP_PATTERN` fixes.  A
    value with microsecond precision, a ``+00:00`` offset, a space instead of
    the ``T``, a local time or a different field order is **not** this
    contract's timestamp and yields ``None`` rather than a guess.

    Two callers need it, for two reasons.  :func:`_check_timestamp` uses it at
    ingress so that a malformed value is refused where the shard can still be
    named, rather than reaching a writer that displays it and an aggregate
    that silently drops it.  The merge uses it so that ``started_at`` and
    ``generated_at`` are selected by **parsed instant** instead of by string
    order - the two agree for this fixed-width UTC spelling, which is why
    ordering by string was defensible, but they stop agreeing the moment a
    hand-built document carries anything else, and the merge accepts
    hand-built documents.

    Args:
        text: The value to parse.  Anything that is not a string yields
            ``None``.

    Returns:
        The instant as a timezone-aware UTC :class:`~datetime.datetime`, or
        ``None`` when the value is not exactly this contract's spelling.
        Never raises.

    Examples:
        >>> parse_timestamp("2022-09-07T13:37:26.297Z")
        datetime.datetime(2022, 9, 7, 13, 37, 26, 297000, tzinfo=datetime.timezone.utc)
        >>> parse_timestamp("2022-09-07T13:37:26.297123Z") is None
        True
        >>> parse_timestamp("2022-09-07T13:37:26.297+00:00") is None
        True
        >>> parse_timestamp(None) is None
        True
    """
    if not isinstance(text, str) or not TIMESTAMP_PATTERN.fullmatch(text):
        return None
    try:
        # The pattern has established the shape, so the only failures left are
        # impossible dates such as month 13 or 31 February, which ``strptime``
        # rejects as a ``ValueError``.  The format mirrors
        # :data:`TIMESTAMP_PATTERN` field for field, with ``%z`` where the
        # pattern has a literal ``Z``: ``%z`` reads that ``Z`` as UTC and
        # returns an aware instant directly, and the other offset spellings it
        # would accept have already been refused above.
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%f%z")
    except ValueError:
        return None


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

    The algorithm is ``TestSourcesModel.convertToId``, read from the
    ``cucumber-core`` 7.2.3 bytecode:
    ``text.replaceAll("[\\s'_,!]", "-").toLowerCase()``.  Whitespace,
    apostrophes, underscores, commas and exclamation marks each become a
    single ``-``; nothing else is touched, so periods, colons, quotation marks
    and parentheses survive verbatim and an unnamed ``Examples:`` block
    contributes an empty segment to a row id.

    Args:
        text: A feature, scenario or Examples name.  ``None`` is treated as an
            empty name, which is what an unnamed Examples block yields.

    Returns:
        The slug, lower-cased with :meth:`str.lower`, which is
        locale-independent and agrees with the JVM's default-locale
        ``toLowerCase()`` for the suite's ASCII names.

    Examples:
        >>> convert_to_id("User can change any user's information")
        'user-can-change-any-user-s-information'
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
    scenario's id is ``<feature>;<scenario>`` and an Examples row's is
    ``<feature>;<outline>;<examples>;<position>``.  An **unnamed**
    ``Examples:`` block contributes an empty segment, so rows carry a doubled
    (``<feature>;<outline>;;2``): that is the JVM's own output, measured in the
    clean baseline and pinned in ``tests/fixtures/sample_results.json``.

    Args:
        feature_name: The feature's name.
        scenario_name: The scenario's name -- for an outline row, the
            outline's name without behave's ``" -- @1.1 Examples"`` suffix.
        examples_name: The Examples block's name, or ``None``/``""`` for an
            unnamed block.
        row_index: behave's **one-based** row index within the Examples
            block's body, or ``None`` for a plain scenario.  The JVM appends
            ``bodyRowIndex + 2`` from a zero-based index -- the header row
            counts as 1 -- so this becomes ``row_index + 1``.

    Returns:
        The id.  Two features that share a name legitimately produce equal
        ids; the collision is preserved, which is why the HTTP report routes
        key on list position instead.
    """
    parts = [convert_to_id(feature_name), convert_to_id(scenario_name)]
    if row_index is not None:
        # The Examples segment, then the row's position.  For an unnamed block
        # the segment is the empty string -- the JVM's ``convertToId("")`` --
        # and it is appended rather than skipped, because skipping it would
        # emit ``...;2`` where the baseline emits ``...;;2``.
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

    The JVM records a step argument as the raw matched substring **including
    the surrounding double quotes**, with a zero-based offset into the step
    name: for the reference's ``... like "Test2" , "30" and "2"`` step it
    records ``("\\"Test2\\"", 44)``, ``("\\"30\\"", 54)`` and ``("\\"2\\"", 63)``.
    This port's step phrases put the quotes in the phrase literal and let the
    placeholder capture the inner text, so behave reports one character inside
    each quote.

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
        ``name[offset:offset + len(val)] == val`` holds, and a span that does
        not index into ``name`` yields ``("", max(start, 0))`` rather than
        raising.
    """
    if not isinstance(start, int) or not isinstance(end, int):
        return "", 0
    if not 0 <= start <= end <= len(name):
        return "", max(start, 0)
    if start > 0 and end < len(name) and name[start - 1] == '"' and name[end] == '"':
        return name[start - 1 : end + 1], start - 1
    return name[start:end], start


def _text_is_sensitive(text: str) -> bool:
    """Answer whether ``text`` is credential-shaped on its own terms.

    The question the span classifier cannot answer from context: an email
    address, a ``scheme://user:pass@host`` URL, a ``Bearer`` token or a long
    opaque blob is a credential wherever it appears, with no field name in
    front of it.  ``app/logging_config.py``'s :func:`redact_sensitive` already
    encodes exactly that judgement, so it is asked rather than re-derived -
    "would the sanitizer change this text" is the same question as "does this
    text carry a secret".

    Args:
        text: The candidate text, normally one span of a step name.

    Returns:
        ``True`` when the sanitizer would rewrite ``text``.  A classifier that
        itself fails answers ``True``: masking a value that might be a
        credential costs a diagnostic, and publishing one that is costs a
        credential.  Never raises.
    """
    try:
        return redact_sensitive(text) != text
    except Exception:  # pragma: no cover - a pure-regex sanitizer cannot fail
        logger.debug("A value could not be classified; it was masked", exc_info=True)
        return True


def _span_is_sensitive(
    name: str,
    start: int,
    end: int,
    previous_end: int = 0,
    next_start: int | None = None,
) -> bool:
    """Answer whether the span ``name[start:end]`` must be masked.

    Two independent grounds, either of which is enough:

    * **Adjacency.** A credential field name occurs in the text *this* span
      owns, before or after it.  This is the case that matters for this suite:
      ``User enters "<password>" password`` [Login.feature:16] substitutes a
      value whose *own* text is indistinguishable from a product name, and
      only the neighbouring ``password`` says what it is.
    * **The value itself**, via :func:`_text_is_sensitive`, which catches the
      other half of the same phrase - ``User enters "<username>" username``,
      whose substituted value is an address [Login.feature:22-36] - and any
      value that is a secret with no field name near it at all.

    **What "the text this span owns" means, and why it is not a character
    count.**  The adjacency scan is bounded by the *neighbouring spans* and
    only then capped by :data:`_SPAN_KEYWORD_WINDOW`: the leading segment runs
    back to the previous span's end (or the start of the name) and the
    trailing segment runs forward to the next span's start (or the end of the
    name).  A keyword between two values therefore classifies the value it
    follows and not the one before it, which is what keeps ``User enters
    "public" tag and "<password>" password`` masking the second span alone.
    Scanning a raw window instead - 32 characters each way, whatever else it
    crossed - pulls a keyword governing a different clause onto an innocent
    value, and over-redaction is a failure mode in its own right: AAP 0.6
    freezes the parameterized step ``User can change any user's information
    like "Test2" , "30" and "2"`` and its three offsets, and an emptied
    artifact is as useless to the Jenkins publisher as a leaky one.

    Every example in this module's prose uses the feature files' own
    ``<placeholder>`` form or a reserved-domain address: a sanctioned fixture
    credential belongs in the Gherkin data and nowhere else, which is review
    finding SEC2-F17 and applies to a comment as much as to a literal.

    Args:
        name: The step name the span indexes into.
        start: Start of the span, inclusive.
        end: End of the span, exclusive.
        previous_end: End of the preceding candidate span, or ``0`` when this
            is the first one - the floor of the leading segment.
        next_start: Start of the following candidate span, or ``None`` when
            this is the last one, in which case the trailing segment runs to
            the end of ``name``.

    Returns:
        Whether the span carries a classified value.
    """
    leading_floor = max(start - _SPAN_KEYWORD_WINDOW, previous_end)
    trailing_ceiling = min(
        end + _SPAN_KEYWORD_WINDOW,
        len(name) if next_start is None else next_start,
    )
    before = name[leading_floor:start]
    after = name[end:trailing_ceiling]
    if _SPAN_CREDENTIAL_KEYWORD_RE.search(before) is not None:
        return True
    if _SPAN_CREDENTIAL_KEYWORD_RE.search(after) is not None:
        return True
    return _text_is_sensitive(name[start:end])


def _mask_span(span: str) -> str:
    """Replace one span's content with the redaction placeholder.

    A quoted span keeps its two quote characters and loses only what they
    surround, so ``"someone@example.invalid"`` becomes ``"[redacted]"``.  That
    is not cosmetic: the JSON contract documents ``val`` as *the raw matched
    substring including its surrounding quotes*
    (:func:`widen_quoted_span`), and a consumer that strips the quotes off an
    argument would otherwise be handed a value with none.

    Args:
        span: The span's text, quotes included when it has them.

    Returns:
        The masked text: the placeholder inside the original quote
        characters, or the placeholder alone for an unquoted span.
    """
    if len(span) >= 2 and span[0] == span[-1] and span[0] in "\"'":
        return f"{span[0]}{REDACTION_PLACEHOLDER}{span[-1]}"
    return REDACTION_PLACEHOLDER


def _candidate_spans(
    name: str,
    entries: Sequence[Any],
) -> list[tuple[int, int, int | None]]:
    """Find the spans of ``name`` a redaction may consider.

    Args:
        name: The step name.
        entries: The step's ``match.arguments`` entries, possibly empty and
            possibly malformed - a hand-built document is not trusted here.

    Returns:
        ``(start, end, entry_index)`` triples in ascending order, from **two**
        sources at once:

        * every argument entry whose ``val``/``offset`` pair genuinely indexes
          into ``name``, carrying its index, because that pair has to keep
          agreeing with the name after splicing; and
        * every quoted run of ``name`` that no such span already covers,
          carrying ``entry_index`` ``None``.

        The second source is not a fallback.  An argument list can index some
        of a name's values and not others - a hand-built document, a
        worker-authored file, a step whose definition captured one parameter
        of a two-value phrase - and a span no entry represents is still a span
        a credential can sit in: ``User enters "public" tag and "<password>"
        password`` with one indexed argument would otherwise publish the
        password phrase in the clear (review finding SEC2-F03).  It also
        remains what covers an undefined step, whose text behave substituted
        but for which it reported no arguments at all.

        Overlap is resolved in favour of the argument spans, because theirs
        are the offsets the contract pins; a quoted run that intersects one is
        dropped rather than spliced a second time.
    """
    indexed: list[tuple[int, int, int | None]] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        value = entry.get("val")
        offset = entry.get("offset")
        if not isinstance(value, str) or not value:
            continue
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            continue
        if name[offset : offset + len(value)] != value:
            continue
        indexed.append((offset, offset + len(value), index))
    indexed.sort(key=lambda span: span[0])
    spans = list(indexed)
    for match in _QUOTED_RUN_RE.finditer(name):
        start, end = match.start(), match.end()
        if any(
            start < covered_end and covered_start < end
            for covered_start, covered_end, _ in indexed
        ):
            continue
        spans.append((start, end, None))
    spans.sort(key=lambda span: span[0])
    return spans


def _splice_spans(
    name: str,
    spans: Sequence[tuple[int, int, int | None]],
) -> tuple[str, dict[int, tuple[str, int]]]:
    """Rebuild ``name`` with every sensitive span masked, tracking the shift.

    The offset bookkeeping is the whole point.  ``[redacted]`` is not the
    length of what it replaces, so every span after a masked one moves, and an
    argument entry whose ``offset`` was not moved with it would point at the
    wrong characters -- silently, because the value would still be a plausible
    substring.  The cumulative delta is therefore carried forward and applied
    to each span in turn.

    Every span is classified and spliced in this one left-to-right pass,
    whichever source :func:`_candidate_spans` drew it from, so the argument
    entries and the quoted runs no entry represents move the cursor and the
    delta by the same arithmetic.  Each span is also handed its neighbours'
    bounds, which is what scopes the adjacency search to the text the span
    itself owns (:func:`_span_is_sensitive`); the neighbour on the right is
    the next span that can still be spliced, so an overlapping span that this
    pass will skip does not truncate the segment its predecessor owns.

    Args:
        name: The step name.
        spans: The candidate spans, ascending, from :func:`_candidate_spans`.

    Returns:
        The rebuilt name, and a mapping from argument index to the
        ``(val, offset)`` pair that indexes into *that* name.  A span that
        overlaps one already applied is skipped rather than spliced twice,
        which keeps the rebuild well-defined for a hand-built document whose
        arguments overlap.
    """
    pieces: list[str] = []
    replaced: dict[int, tuple[str, int]] = {}
    cursor = 0
    delta = 0
    for position, (start, end, index) in enumerate(spans):
        if start < cursor or end > len(name):
            continue
        original = name[start:end]
        masked = original
        # The next span this pass can still splice, found by index rather than
        # over a slice so that a step with many parameters copies nothing.
        next_start = next(
            (
                spans[later][0]
                for later in range(position + 1, len(spans))
                if spans[later][0] >= end
            ),
            None,
        )
        if _span_is_sensitive(
            name, start, end, previous_end=cursor, next_start=next_start
        ):
            masked = _mask_span(original)
        pieces.append(name[cursor:start])
        pieces.append(masked)
        if index is not None:
            replaced[index] = (masked, start + delta)
        delta += len(masked) - len(original)
        cursor = end
    pieces.append(name[cursor:])
    return "".join(pieces), replaced


def _redact_arguments(
    name: str,
    entries: Sequence[Any],
    replaced: dict[int, tuple[str, int]],
) -> list[JsonDict]:
    """Rebuild a step's ``match.arguments`` around the spliced name.

    Args:
        name: The step's *original* name, used only to decide the fallback
            case below.
        entries: The step's argument entries, in order.
        replaced: The ``(val, offset)`` pairs :func:`_splice_spans` produced,
            by argument index.

    Returns:
        A new list of the same length, because the length is the arity of the
        step's parameters and the JVM's ``createMatchMap`` never drops an
        entry.  Three cases: an entry whose span was spliced takes the new
        ``val`` and ``offset``; an empty entry stays empty; and an entry whose
        span never indexed the name -- a type-converted parameter, whose
        ``val`` the collector recovered from ``argument.original`` -- keeps its
        offset and is masked when either the value itself is classified or the
        step's name names a credential field anywhere.  That last disjunct is
        deliberately wider than the span rule: with no position in the name
        there is no window to search, and a login step's converted argument is
        exactly as much of a credential as a positioned one.
    """
    names_a_credential = _SPAN_CREDENTIAL_KEYWORD_RE.search(name) is not None
    built: list[JsonDict] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            # Not a mapping, so not something this function can rebuild; the
            # validator refuses such a document on the way back in.
            built.append(entry)
            continue
        rebuilt = dict(entry)
        if index in replaced:
            rebuilt["val"], rebuilt["offset"] = replaced[index]
        else:
            value = rebuilt.get("val")
            if isinstance(value, str) and value:
                if names_a_credential or _text_is_sensitive(value):
                    rebuilt["val"] = _mask_span(value)
        built.append(rebuilt)
    return built


def redact_step_text(
    name: Any,
    arguments: Sequence[Any] | None = None,
) -> tuple[str, list[JsonDict]]:
    """Mask credential-bearing values in a step's text and its arguments.

    **Why this exists** (review finding SEC2-F03, CWE-532/359/200).  The
    suite's login phrases are ``User enters "<username>" username`` and
    ``User enters "<password>" password`` [Login.feature:15-16, 60-61, 87,
    107, 124-125; Logout.feature:15-16, 39-40], so for a Login or Logout
    Examples row the *substituted* step name **is** the credential, and
    ``match.arguments[].val`` carries the same substring a second time.  Those
    two fields are copied verbatim into a worker's JSON file and from there
    into the published Cucumber JSON artifact -- whose path
    :mod:`app.utils.paths` owns and is deliberately not spelled here -- which
    the Jenkins publisher reads and the build archives, giving an account name
    and its password a durable, widely-readable home.  This function is the
    boundary at which that stops.

    **Why it is not just** :func:`redact_sensitive`.  The sanitizer rewrites
    *text*; this schema needs a decision about a *span*, because
    ``match.arguments`` pins each value by offset and the contract is
    ``name[offset:offset + len(val)] == val``.  A text-level rewrite changes
    lengths without telling anyone which spans moved, so the offsets it leaves
    behind index the wrong characters.  The classifier here therefore works
    span by span, mirroring the sanitizer's own adjacency vocabulary in
    :data:`_SPAN_CREDENTIAL_KEYWORD_RE` and delegating the "is this value a
    secret in itself" half straight back to it.

    **The invariant.** Every returned entry still satisfies
    ``redacted_name[offset:offset + len(val)] == val``.  The name is spliced
    and each later offset is shifted by the cumulative length delta, so the
    pair agrees exactly rather than approximately.

    **Idempotency.** Applying this to its own output changes nothing, which is
    what lets both the producer (:class:`ResultCollectorFormatter`) and the
    writer (``app/reporting/cucumber_json.py``) apply it -- the writer treats
    worker JSON as untrusted input and re-applies the rule, and a document
    that was already masked comes back byte-identical.

    **What it does not touch.** The ten ``features/*.feature`` files keep
    their ``Examples`` credentials verbatim: AAP 0.8 states that they are
    pre-existing fixture data for an external test instance which no agent may
    redact, parameterize or rotate.  This function does not change what a
    scenario *sends to the browser*; it changes only what a report keeps.

    Args:
        name: The step's substituted text.  A non-``str`` is coerced, because
            a behave model attribute is not guaranteed to be one.
        arguments: The step's ``match.arguments`` entries, or ``None`` for a
            step that took no parameters.  Passing them is what makes the
            redaction offset-preserving; passing ``None`` makes it text-level,
            which is the right answer when there are no offsets to protect.

    Returns:
        ``(redacted_name, redacted_arguments)``.  When no returned entry pins
        an offset -- a step that took no parameters, or one whose entries are
        all the JVM's value-less ``{}`` -- the whole spliced name is put
        through :func:`redact_sensitive` as well, so a sensitive run that is
        not a quoted argument, such as a bare email address in a step phrase,
        is masked too.  Where an offset *is* pinned that pass is withheld,
        because a text-level rewrite moves characters without saying which
        spans moved and would leave the pinned offsets indexing the wrong
        ones; the span pass covers those names, over both the argument spans
        and the quoted runs no argument represents.  Never raises: a failure
        to classify degrades to the text-level redaction with the argument
        list dropped, so nothing unclassified is published.

    Examples:
        >>> redact_step_text('User enters "not-a-real-secret" password',
        ...                  [{"val": '"not-a-real-secret"', "offset": 12}])
        ('User enters "[redacted]" password', [{'val': '"[redacted]"', 'offset': 12}])
        >>> redact_step_text('User can find his name "Lucas" from search bar',
        ...                  [{"val": '"Lucas"', "offset": 23}])
        ('User can find his name "Lucas" from search bar', [{'val': '"Lucas"', 'offset': 23}])
        >>> redact_step_text('User enters "someone@example.invalid" username')[0]
        'User enters [redacted] username'

        A partially indexed argument list still classifies the span no entry
        represents, and the represented business value survives intact:

        >>> redact_step_text('User enters "public" tag and "x" password',
        ...                  [{"val": '"public"', "offset": 12}])[0]
        'User enters "public" tag and "[redacted]" password'
    """
    try:
        text = name if isinstance(name, str) else str(name)
    except Exception:  # pragma: no cover - a __str__ that raises
        text = ""
    try:
        entries = list(arguments or ())
        redacted, replaced = _splice_spans(text, _candidate_spans(text, entries))
        built = _redact_arguments(text, entries, replaced)
        pins_an_offset = any(
            isinstance(entry, dict) and "offset" in entry for entry in built
        )
        if not pins_an_offset:
            # No offsets to protect, so the text-level sanitizer can have the
            # whole name: it catches the shapes a quoted-span scan cannot see.
            redacted = redact_sensitive(redacted)
        return redacted, built
    except Exception:
        logger.warning(
            "A step's text could not be redacted span by span; the whole text "
            "was masked and its arguments were dropped",
            exc_info=True,
        )
        try:
            return redact_sensitive(text), []
        except Exception:  # pragma: no cover - defence in depth
            return REDACTION_PLACEHOLDER, []


def _locate_span(name: str, value: str, start: int | None) -> int | None:
    """Find where ``value`` sits inside ``name``.

    The companion of :func:`widen_quoted_span` for the one case that function
    cannot serve: a converted step argument, whose text behave reports
    without a span that indexes into the step name.  The schema requires an
    argument's ``val`` and ``offset`` to describe a span that fits inside the
    name, so the offset has to be established rather than assumed.

    Args:
        name: The step's substituted name.
        value: The argument's matched text.
        start: The offset behave reported, or ``None`` when it reported none.

    Returns:
        ``start`` when the value really is at that offset - preferred, because
        it is what the matcher said and two identical parameter values would
        otherwise both resolve to the first one - else the offset of the first
        occurrence, else ``None`` when the value does not occur in the name at
        all and therefore has no span.
    """
    if not value or len(value) > len(name):
        return None
    if (
        start is not None
        and 0 <= start
        and name[start : start + len(value)] == value
    ):
        return start
    found = name.find(value)
    return found if found >= 0 else None


def _safe_probe(probe: Callable[[], Any]) -> str:
    """Run a metadata probe, tolerating an ordinary failure.

    Args:
        probe: A zero-argument callable returning a value to describe.

    Returns:
        ``str(value)`` when the probe returns something truthy, otherwise
        :data:`_UNKNOWN_METADATA_VALUE`.  An ordinary :class:`Exception` is
        logged at debug level and swallowed -- a report is not worth a failed
        test run -- while :class:`KeyboardInterrupt` and :class:`SystemExit`
        propagate.
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
    """Describe the engine, interpreter, operating system and machine type.

    The four keys and their sub-keys are fixed vocabulary:
    ``app/templates/artifact/metadata.html`` renders exactly
    ``implementation{name,version}``, ``runtime{name,version}``, ``os{name}``
    and ``cpu{name}``.

    Every probe here reads data the interpreter already holds -- the version
    strings it was built with, and the ``os.uname()`` fields (on Windows, the
    architecture environment values) that :func:`platform.machine` returns --
    so ``cpu.name`` is the machine type and **no value in this mapping is ever
    obtained by executing a program**.  That is deliberate and load-bearing: a
    metadata probe must not be a command-execution or content-injection
    surface, and one that resolved a helper binary through the inherited
    ``PATH`` would be both, since whatever it printed would be written
    verbatim into every artifact this document feeds.

    Returns:
        The metadata mapping.  Every value is a string; whitespace around the
        machine type is stripped, and a probe that yields nothing yields ``""``
        (:data:`_UNKNOWN_METADATA_VALUE`).  This function never raises.
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
            "name": _safe_probe(lambda: platform.machine().strip()),
        },
    }


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
        "type": TAG_TYPE,
        # Floored at one, because Gherkin numbers lines and columns from one
        # and the schema refuses anything below it: a model that reported no
        # line would otherwise build a tag no shard could carry.
        "location": {"line": max(int(line), 1), "column": max(int(column), 1)},
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

        ``name`` and ``match.arguments`` are stored **redacted**, by
        :func:`redact_step_text` -- this is the one constructor every stored
        step passes through, so putting the boundary here is what covers a
        step whose definition is never resolved as well as one whose is.  When
        the caller supplies arguments the two are redacted *jointly*, so the
        contract ``name[offset:offset + len(val)] == val`` still holds; with
        no arguments the text-level rule applies, and
        :meth:`ResultCollectorFormatter._apply_match` recomputes both from
        behave's raw step name as soon as the match arrives.

        The joint path is taken for **any** non-string sequence of entries,
        not only a ``list``: a fixture, a worker file read back through a
        loader that produced tuples, or a caller that built the mapping by
        hand is exactly the untrusted shape this boundary exists for, and a
        type test that only recognised ``list`` left such a mapping's ``val``
        raw beside a text-redacted name (review finding SEC2-F03).  Whatever
        sequence arrives, the stored ``arguments`` is a ``list``, which is
        what :func:`_validate_match` accepts when the document is read back.

        ``result.error_message`` is stored through
        :func:`sanitize_failure_text` for the same reason
        :func:`new_hook_entry` does it -- review finding SEC2-F20 is that the
        text is classified *before* storage, and a document assembled through
        this builder is a document :func:`dump_result_set` persists and the
        writers publish.  Every other key of ``result`` is copied through
        untouched, so ``status``, ``duration`` and their omission rules are
        the caller's exactly as before, and a result carrying no
        ``error_message`` still gets none.
    """
    match_map = dict(match) if match else {}
    arguments = match_map.get("arguments")
    if isinstance(arguments, Sequence) and not isinstance(
        arguments, (str, bytes, bytearray)
    ):
        if arguments:
            redacted_name, redacted_arguments = redact_step_text(name, arguments)
            match_map["arguments"] = redacted_arguments
        else:
            # No entries to redact against, so the text-level rule applies;
            # the empty list is still normalised to this schema's type.
            redacted_name, _ = redact_step_text(name)
            match_map["arguments"] = []
    else:
        redacted_name, _ = redact_step_text(name)
    built_result = dict(result) if result else {}
    if "error_message" in built_result:
        built_result["error_message"] = sanitize_failure_text(
            built_result["error_message"]
        )
    return {
        "keyword": step_keyword(keyword),
        # Floored at one, as every source position in this schema is.
        "line": max(int(line), 1),
        "name": redacted_name,
        "matched": bool(matched),
        "match": match_map,
        "result": built_result,
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

    The port of the JVM's hook step map, which is where a screenshot lands
    (``Hooks.java:11-18``): the JSON generator hangs the attachment off the
    hook rather than off a step.  The defaults describe an entry whose outcome
    is *not yet known*: the formatter's ``_finalize_hook_entries`` overwrites
    ``status`` and ``duration`` once behave has run the after-hooks.

    Args:
        location: Dotted path of the hook function.  ``None`` yields an empty
            ``match``, mirroring the JVM's omission of ``location``.
        status: The hook's own outcome in behave's vocabulary (``passed``,
            ``hook_error``, ``cleanup_error``); folding it into Cucumber's
            narrower set belongs to the writers.
        duration: The hook's duration in nanoseconds.
        error_message: The hook's failure text, already in the canonical
            message-then-traceback shape.  ``None`` or ``""`` omits the key,
            exactly as a step's ``result`` omits it when the step passed.  It
            is stored through :func:`sanitize_failure_text`, so a caller that
            builds the text from an exception cannot put a secret, an absolute
            path or an unbounded traceback into the document by hand (review
            finding SEC2-F20).
        embeddings: Attachment mappings, each with ``mime_type``, ``data`` and
            optionally ``name``, exactly as ``app/reporting/screenshots.py``
            produces them.

    Returns:
        The hook entry.
    """
    # ``max(..., 0)`` because a duration is a measured quantity the schema
    # refuses below zero, and this builder takes one from a caller.
    result: JsonDict = {"status": status, "duration": max(int(duration), 0)}
    if error_message:
        result["error_message"] = sanitize_failure_text(error_message)
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

    The only implementation of the measured key-presence rules: both kinds
    carry ``type``, ``keyword``, ``line``, ``name``, ``description``,
    ``selected`` and ``steps``; a **scenario** also carries ``id``,
    ``start_timestamp``, ``after`` and -- only when it has some -- ``tags``
    (the JVM adds it under ``if (!testCase.getTags().isEmpty())``), of which a
    **background** carries none; an unknown type warns and becomes a scenario.

    Args:
        element_type: :data:`ELEMENT_TYPE_BACKGROUND` or
            :data:`ELEMENT_TYPE_SCENARIO`.
        keyword: ``"Background"``, ``"Scenario"`` or ``"Scenario Outline"``.
        line: The element's line -- the data row's line for an outline row.
        name: The name, without behave's outline annotation suffix.
        description: As :func:`new_feature`'s -- indentation preserved.
        selected: Whether the tag expression chose it; a background inherits
            its scenario's value.
        identifier, start_timestamp, tags, after: The scenario-only values
            from :func:`scenario_element_id`, :func:`format_timestamp`,
            :func:`scenario_tag`, :func:`new_hook_entry`.

    Returns:
        The element object.
    """
    if element_type not in (ELEMENT_TYPE_BACKGROUND, ELEMENT_TYPE_SCENARIO):
        logger.warning(
            "Unknown element type %r recorded as a scenario", element_type
        )
        element_type = ELEMENT_TYPE_SCENARIO

    if element_type == ELEMENT_TYPE_SCENARIO and not identifier:
        # Refused at the point of construction, because this is the last place
        # the id can still be computed correctly.  Downstream it cannot be:
        # an Examples row's id carries the block's slug and the row's
        # one-based position, neither of which any other field of the element
        # records, so a consumer that rebuilt one from the feature and
        # scenario names would publish a plausibly shaped identifier for a
        # test case the suite does not contain - and the publisher, both HTML
        # families and every detail page key off it.
        # :func:`scenario_element_id` is the one producer, and it always
        # yields a non-empty value.
        raise ValueError(
            "A scenario element requires a non-empty identifier from "
            "scenario_element_id(); it is the identity every artifact keys "
            "on and no consumer may reconstruct it"
        )

    element: JsonDict = {
        "type": element_type,
        "keyword": (keyword or "").strip(),
        # Floored at one: Gherkin numbers lines from one and the schema
        # refuses anything below it, so a model that reported no line builds
        # an element no shard could carry rather than one silently pointing at
        # line zero in the rerun manifest.
        "line": max(int(line), 1),
        "name": name,
        "description": description or "",
        "selected": bool(selected),
        "steps": list(steps or []),
    }
    if element_type == ELEMENT_TYPE_BACKGROUND:
        return element

    element["id"] = identifier
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
        path: The same path without the scheme, which is what
            ``rerun_report.py`` and human-facing output use; carrying both
            means no consumer performs string surgery.
        identifier: The feature ``id`` from :func:`convert_to_id`.
        line: The ``Feature:`` line, which is 2 in ``Crm.feature``, whose
            first line is the ``@Smoke`` tag.
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
        # Floored at one, as every source position in this schema is.
        "line": max(int(line), 1),
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

    Every run-level key is present from the outset, so no consumer -- writer,
    merge or hand-written fixture -- has to test for membership.

    Args:
        dry_run: Mirrored from behave's config, which
            ``app/reporting/cucumber_json.py`` needs for the dry-run status
            mapping (under ``dryRun`` the JVM emits matched steps ``passed``
            and unmatched ``undefined``, where behave reports ``untested``).
        tag_expression: The effective tag expression, or ``None`` when no
            filter applies.
        metadata: Override for :func:`run_metadata`, so a test can pin values.
        started_at: The earliest scenario ``start_timestamp``, or ``None``.
        generated_at: When the document was written, or ``None`` until it is.
        complete: Whether the collector observed the whole run.  ``True`` by
            default; the collector sets it to ``False`` the moment it drops an
            event, and :func:`load_result_set` then refuses the document.
        collection_errors: One ``{"event": ..., "error": ...}`` mapping per
            dropped event, capped at :data:`MAX_COLLECTION_ERRORS`.
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


def _serialize(document: ResultSet) -> str:
    """Render ``document`` as JSON text using the shared options.

    Args:
        document: The result document.

    Returns:
        The JSON text, without a trailing newline.  A value the encoder cannot
        handle -- which the builders do not produce, though a hook could store
        one -- is coerced with :func:`str` after a warning, because a
        diagnosable document beats no document at all.
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
    interpolates into a :class:`ResultSetError` message, a ``logger`` call or
    a ``collection_errors`` entry passes through here, because those fragments
    are *worker-controlled*: a bare ``LF`` in one would forge records in the
    rejection message ``app/services/test_run_service.py`` logs, and an
    ``ESC`` would reach a terminal as an escape sequence.  The raw detail
    stays in the shard file, and ``result.error_message`` is deliberately
    **not** routed here: it keeps real newlines for the HTML writers, and
    :func:`_normalize_newlines` owns it.

    Args:
        value: The fragment, of any type: it is converted with :func:`str`
            first, since a ``__str__`` that raises must not cost a diagnostic.
        limit: Longest result in characters before an ellipsis is appended,
            defaulting to :data:`_COLLECTION_ERROR_TEXT_LIMIT`.

    Returns:
        The text with every C0 control, ``DEL`` and C1 control replaced by a
        readable escape (``\\n``, ``\\t``, ``\\x1b``) and truncated to
        ``limit`` characters with a trailing ``...``.  An ordinary
        :class:`Exception` from ``str`` is absorbed as ``"<unprintable T>"``,
        while :class:`KeyboardInterrupt` and :class:`SystemExit` propagate.
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
        is returned escaped and truncated, unchanged otherwise.  An ordinary
        :class:`Exception` from the exception's own ``__str__`` yields the
        class name alone; interrupts propagate.
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


def _shorten_absolute_path(match: re.Match[str]) -> str:
    """Reduce one matched absolute path to its last two components.

    Args:
        match: A match of :data:`_ABSOLUTE_POSIX_PATH_RE` or
            :data:`_ABSOLUTE_WINDOWS_PATH_RE`.

    Returns:
        The path's own separator-joined tail behind
        :data:`_SHORTENED_PATH_MARKER`, e.g. ``.../steps/login_steps.py`` for
        a POSIX match and ``...\\steps\\login_steps.py`` for a Windows one.
        The separator is the one the match carried, so a Windows frame still
        reads like a Windows frame.
    """
    path = match.group(0)
    separator = "\\" if "\\" in path else "/"
    components = [component for component in path.split(separator) if component]
    tail = separator.join(components[-_SHORTENED_PATH_COMPONENTS:])
    return f"{_SHORTENED_PATH_MARKER}{separator}{tail}"


def _strip_known_roots(text: str) -> str:
    """Remove this checkout's own prefixes wherever they appear in ``text``.

    The repository root and the working directory are the two prefixes this
    process can name, and a path under either of them is the port's own file:
    dropping the prefix leaves ``features/steps/login_steps.py``, which is
    both shorter and exactly the form ``app/reporting/rerun_report.py`` and
    the HTML writers already use for a feature path.

    Args:
        text: Text that may contain either prefix - a whole failure message,
            or one frame's path.

    Returns:
        The text with each known root, followed by either separator, removed.
        Text containing neither is returned unchanged.
    """
    for root in (_REPOSITORY_ROOT, _INITIAL_WORKING_DIRECTORY):
        if not root:
            continue
        for separator in ("/", "\\"):
            text = text.replace(f"{root}{separator}", "")
    return text


def _is_absolute_frame_path(path: str) -> bool:
    """Answer whether a traceback frame's quoted path is an absolute one.

    Three shapes count, because the suite runs on Windows as well as Linux
    (AAP 0.8) and a worker's traceback carries whichever the platform
    produced: a POSIX path, a drive-qualified Windows path with either
    separator, and a Windows UNC share.

    Args:
        path: The quoted run after ``File `` in a frame line.

    Returns:
        ``True`` for an absolute filesystem path.  ``False`` for a relative
        one -- which rule 1 has already produced, or which the frame always
        carried -- and for the shapes a frame position can hold that are not
        paths at all: ``<stdin>``, ``<string>``, a URL, and a ``//``-leading
        run such as an XPath selector, which is why the POSIX test refuses a
        doubled leading separator rather than accepting any leading ``/``.
    """
    if path.startswith(_WINDOWS_UNC_PREFIX):
        return True
    if _WINDOWS_DRIVE_PREFIX_RE.match(path) is not None:
        return True
    return path.startswith("/") and not path.startswith("//")


def _relativize_frame_path(match: re.Match[str]) -> str:
    """Rewrite one traceback frame's path, spaces and all.

    Args:
        match: A match of :data:`_TRACEBACK_FRAME_PATH_RE`, whose group is the
            path between the frame's quotes.

    Returns:
        The path made repository-relative when it lies under a known root,
        otherwise its last :data:`_SHORTENED_PATH_COMPONENTS` components
        behind :data:`_SHORTENED_PATH_MARKER`, in the separator the path
        itself carried -- so a Windows frame still reads like a Windows frame
        and a drive letter, a UNC host or an account name in a leading
        component is gone.  A path that is neither is returned unchanged,
        which is what keeps an already-relative frame byte-identical.
    """
    path = match.group(0)
    stripped = _strip_known_roots(path)
    if stripped != path:
        return stripped
    if not _is_absolute_frame_path(path):
        return path
    separator = "\\" if "\\" in path else "/"
    components = [component for component in path.split(separator) if component]
    tail = separator.join(components[-_SHORTENED_PATH_COMPONENTS:])
    return f"{_SHORTENED_PATH_MARKER}{separator}{tail}"


def _relativize_paths(text: str) -> str:
    """Remove workspace topology from failure text without losing the frames.

    Three rules, in this order, because each one makes the next unnecessary
    for the paths it has already handled:

    1. **The port's own files become repository-relative.** The repository
       root and the working directory are stripped where they appear as a
       prefix, with either separator (:func:`_strip_known_roots`), so a frame
       reads ``File "features/steps/login_steps.py", line 61``.
    2. **A traceback frame's path is rewritten as a unit**
       (:func:`_relativize_frame_path`).  A frame line is ``  File "<path>",
       line N, in <name>``, so the quoted run is the whole path however many
       spaces it contains -- which is the case rule 3 cannot express, and the
       case a real CI workspace produces: ``/home/jane doe/...``,
       ``C:\\Users\\Jane Doe\\...``, ``D:\\CI Agent\\job 42\\...``.  Before
       this rule those frames kept their account name and their layout while
       the prefix in front of them was mangled into ``.../home/jane`` (review
       finding SEC2-F20).
    3. **Every remaining absolute path keeps only its last two components.**
       That is a path quoted in an exception's *message* rather than in a
       frame -- a Selenium capability dump's profile directory, a
       configuration error naming a file -- and the engine's and the
       interpreter's own frames if any escaped rule 2.  Their leading
       components name the operating account, the virtual environment and the
       workspace layout and nothing a reader of a test failure needs
       (CWE-200).

    What rule 3 deliberately does not touch, and the lookbehinds in
    :data:`_ABSOLUTE_POSIX_PATH_RE` are what guarantee it: an XPath selector
    (``//input[@id='x']``), a URL (``https://host/path``) and a relative path.
    A Selenium ``NoSuchElementException`` message is the commonest failure
    text this suite produces and its selector is the whole diagnostic.  Rule 2
    is held to the same standard by :func:`_is_absolute_frame_path`, and it
    runs *before* rule 3 so that a frame is classified by the quotes around it
    rather than by whichever prefix of it a component class happened to
    match.

    Args:
        text: LF-normalised failure text.

    Returns:
        The same text with absolute paths relativised or shortened.
    """
    text = _strip_known_roots(text)
    text = _TRACEBACK_FRAME_PATH_RE.sub(_relativize_frame_path, text)
    text = _ABSOLUTE_POSIX_PATH_RE.sub(_shorten_absolute_path, text)
    return _ABSOLUTE_WINDOWS_PATH_RE.sub(_shorten_absolute_path, text)


def _bound_failure_text(text: str) -> str:
    """Apply :data:`MAX_FAILURE_TEXT_CHARS` so that applying it twice is a no-op.

    The naive bound is not idempotent, and that matters here because **two**
    layers sanitize the same field: the collector stores the text and the
    Cucumber writer sanitizes again at publication (the writer's input is a
    worker file, which it does not trust).  Cutting at the cap and appending
    the notice yields a string of ``cap + len(notice)`` characters, which is
    over the cap, so a second pass would cut a message that was already cut -
    losing another notice-length of text and, worse, reporting a dropped count
    that describes the second cut rather than the total.  Measured on an
    8547-character message: two passes left 8218 characters claiming 27 were
    dropped, when 329 had been.

    So the notice is read back rather than treated as body.  A text that
    already carries one is split into the body and the count it declares; the
    body is re-bounded if it is still over the cap - a fabricated notice on a
    long body must not buy an exemption from the bound - and the counts are
    added, so the number a report shows is always the total dropped from the
    original.

    Args:
        text: The sanitized failure text, before bounding.

    Returns:
        ``text`` when it fits, otherwise the first :data:`MAX_FAILURE_TEXT_CHARS`
        characters followed by :data:`TRUNCATION_SUFFIX_TEMPLATE` carrying the
        cumulative dropped count.  A fixed point in both cases.
    """
    body = text
    already_dropped = 0
    notice = _TRUNCATION_NOTICE_RE.search(text)
    if notice is not None:
        body = text[: notice.start()]
        already_dropped = int(notice.group("dropped"))

    if len(body) <= MAX_FAILURE_TEXT_CHARS:
        if already_dropped:
            # Already bounded by an earlier pass: return it unchanged, which is
            # what makes this function a fixed point.
            return text
        return body

    dropped = already_dropped + len(body) - MAX_FAILURE_TEXT_CHARS
    suffix = TRUNCATION_SUFFIX_TEMPLATE.format(dropped=dropped)
    return f"{body[:MAX_FAILURE_TEXT_CHARS]}{suffix}"


def sanitize_failure_text(text: Any) -> str:
    """Make failure text safe to keep in a report, without losing the failure.

    **Why this exists** (review finding SEC2-F20, CWE-532/200).
    :func:`_failure_text` builds ``result.error_message`` from an exception's
    ``str()`` and the *full* Python traceback, and
    ``app/reporting/cucumber_json.py`` publishes that string as
    ``error_message`` in the artifact the Jenkins publisher reads and the
    build archives.  Three things therefore became durable and widely
    readable: any secret an exception message quotes -- a step argument, a
    page dump, a URL with userinfo -- the absolute path of every frame, which
    maps the workspace and names the operating account, and the frames' own
    source lines, at whatever length the exception happened to have.

    Four transformations, in this order, because each depends on the one
    before it:

    1. :func:`_normalize_newlines`, which remains the single owner of that
       rule (AAP deviation 16) -- applied first so that the patterns below see
       one line ending.
    2. :func:`_relativize_paths`, before redaction, because a path is not a
       secret and must not be replaced by a placeholder: a frame has to stay
       identifiable.
    3. :func:`redact_sensitive`, which masks the classified shapes.
    4. The bound, :data:`MAX_FAILURE_TEXT_CHARS`, with
       :data:`TRUNCATION_SUFFIX_TEMPLATE` recording exactly how many
       characters were dropped.  Last, so that the bound is on the text a
       report actually keeps.

    **What survives verbatim: the assertion's own message.** AAP 0.6 and
    0.1.2 freeze the assertion subjects and the message strings, and deviation
    16 covers only their surrounding formatting, so nothing here touches
    ordinary prose -- ``The title is not same as the expected!`` and
    ``Veuillez renseigner ce champ.`` come through unchanged, as does a
    Selenium message with its XPath selector.

    Args:
        text: The failure text, or any value; a non-``str`` is coerced,
            because behave's ``error_message`` is not guaranteed to be one.

    Returns:
        The sanitized text, or ``""`` when there was nothing to sanitize.
        Idempotent, so the writer can apply it again to a message a producer
        already sanitized.  **Never raises**: failure *reporting* must not be
        able to fail a run, so an unexpected error yields the bounded
        placeholder rather than propagating or returning unsanitized text.
    """
    try:
        if text is None:
            return ""
        rendered = text if isinstance(text, str) else str(text)
        if not rendered:
            return ""
        rendered = redact_sensitive(_relativize_paths(_normalize_newlines(rendered)))
        return _bound_failure_text(rendered)
    except Exception:
        logger.warning(
            "Failure text could not be sanitized; the placeholder was stored "
            "in its place so that nothing unsanitized is reported",
            exc_info=True,
        )
        return REDACTION_PLACEHOLDER


def _failure_text(
    exception: BaseException | None,
    traceback_object: TracebackType | None = None,
    error_message: Any = None,
) -> str:
    """Build the contract's failure text for a step or a hook.

    The single owner of that shape, which is the AAP's (§0.6) and byte-pinned
    in ``tests/fixtures/sample_results.json``: ``str(exception)``, one ``\\n``,
    then :func:`traceback.format_exception`'s traceback, LF endings throughout.
    behave's own ``error_message`` is *not* it -- ``Step._process_error``
    prefixes an assertion with ``"ASSERT FAILED: "`` and anything else with
    ``"ERROR: <Class>: "``, and attaches a traceback only under ``--verbose``,
    so the exception on the model is the source of record.

    Args:
        exception: The exception behave stored on the step or scenario model,
            or ``None`` when it stored none.
        traceback_object: behave's ``exc_traceback``; ``None`` falls back to
            the exception's ``__traceback__``, and with none the message is
            returned alone.
        error_message: behave's own ``error_message``, used only when there is
            no exception object.  Only its measured prefixes are stripped, so
            ``"HOOK-ERROR in after_scenario: ..."`` survives verbatim.

    Returns:
        The failure text with LF line endings, or ``""`` when there is nothing
        to report.  Never raises: failure *reporting* must not be able to fail
        a run, so a traceback that cannot be formatted degrades to the message
        alone.

        Every path that returns text returns it through
        :func:`sanitize_failure_text`, so the classified values, the absolute
        paths and the length of what is stored are bounded at the *one* point
        the text is built rather than at each of the places it is read (review
        finding SEC2-F20).  That includes the ``error_message`` fallback path,
        which is how a hook failure behave recorded on the scenario model
        reaches the document.
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
                return sanitize_failure_text(head)
            try:
                rendered = "".join(
                    traceback.format_exception(type(exception), exception, frames)
                )
            except Exception:  # pragma: no cover - malformed traceback object
                logger.debug("A traceback could not be formatted", exc_info=True)
                return sanitize_failure_text(head)
            return sanitize_failure_text(f"{head}\n{rendered}")

        if not error_message:
            return ""
        text = _normalize_newlines(str(error_message))
        if text.startswith(_ASSERT_FAILED_PREFIX):
            return sanitize_failure_text(text[len(_ASSERT_FAILED_PREFIX) :])
        prefix = _ERROR_PREFIX_RE.match(text)
        if prefix is not None:
            remainder = text[prefix.end() :]
            # ``"ERROR: <Class>"`` with nothing after it is behave's shape for
            # an exception with no args; the class name is then the message.
            return sanitize_failure_text(remainder or prefix.group("classname"))
        return sanitize_failure_text(text)
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


def _behave_step_name(step: Any) -> str:
    """Read behave's own step text off a step model.

    The single reader of that attribute, because the text is needed in three
    places and each of them needs it *unredacted*: :meth:`step` stores the
    step, and both :meth:`result` and :meth:`_resolve_match_from_registry`
    have to hand it to :meth:`_apply_match`, whose argument offsets index into
    it.  Once it is in a record it is redacted, and a caller reading it back
    from there would compute offsets against the wrong string.

    Args:
        step: behave's step object.

    Returns:
        The substituted step text, or ``""`` when the model carries none - a
        step with no text is still a step, and a missing attribute must not
        cost the record.
    """
    return str(getattr(step, "name", "") or "")


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
    """Wrap a formatter hook so an ordinary failure cannot reach behave.

    A formatter exception propagates out of the model's run loop and takes the
    worker down mid-run, turning a green suite into a non-zero exit and losing
    every result collected so far.  Every hook body is therefore guarded: an
    unexpected shape is logged with a traceback and that single event is
    skipped, leaving the rest of the run - and the document
    :meth:`ResultCollectorFormatter.close` writes - intact.

    Swallowing the exception is **not** hiding it, and that difference is what
    makes the swallow safe.  A skipped event means the document no longer
    describes the whole run, so the failure is recorded on the document itself
    - ``collection_errors`` gains the event's name and the exception's text,
    and ``complete`` becomes ``False`` - and :func:`load_result_set` refuses
    it, so ``app/services/test_run_service.py`` names that shard dead.

    Args:
        method: The hook method to wrap.

    Returns:
        The wrapped method, which returns ``None`` if the body failed.  Only
        an ordinary :class:`Exception` is caught, so an interrupt
        (:class:`KeyboardInterrupt`, :class:`SystemExit`) still stops the
        worker instead of being recorded.
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

    The companion of :func:`attach_to_current_scenario` and the seam for the
    scenario lifecycle in ``features/environment.py``, which the plan's
    dependency graph keeps free of any import from this package: it can report
    what only it knows -- the teardown hook's own duration, or a failure it
    handled rather than re-raising.  Without a call nothing is lost and
    nothing is invented: the collector still records a hook failure behave
    saw, still measures the after-hook window, and still emits no entry for a
    hook that passed silently.

    Args:
        location: Dotted path of the hook; defaults to
            :data:`DEFAULT_AFTER_HOOK_LOCATION`.
        status: The hook's outcome in behave's vocabulary (``passed``,
            ``hook_error``, ``cleanup_error``), or ``None`` to leave it to the
            scenario model.
        duration: The hook's duration in nanoseconds, or ``None`` to leave the
            measured window standing.
        error_message: The hook's failure text, or ``None``.  Recorded through
            :func:`sanitize_failure_text`, so a caller that builds it from an
            exception and its traceback is held to the same bound and the same
            masking as a step's failure text (review finding SEC2-F20).
        embeddings: Attachment mappings to record under the same hook entry,
            exactly as ``screenshots.build_embedding`` returns them.

    Returns:
        ``True`` when a collector accepted it, ``False`` when none is active
        or no scenario is current -- not an error, since the suite may run
        under another formatter and this must not change a test outcome.
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
    worker from :data:`FORMATTER_SCOPED_NAME` and :mod:`app.utils.paths`.

    The lifecycle, as measured against behave 1.3.3 rather than assumed:

    * :meth:`uri`, :meth:`feature`, then :meth:`background` **once per
      feature** -- behave announces the definition, not an occurrence.
    * :meth:`scenario` per scenario, where this formatter appends the
      Background *occurrence* and then the scenario element -- the JVM's own
      ``handleTestCaseStarted`` interleaving, which is why the reference
      report's eight elements are four backgrounds and four scenarios.
    * :meth:`step` for every step, background steps first in one flat
      sequence; the first ``len(scenario.background_steps)`` land in the
      Background occurrence.
    * :meth:`match` then :meth:`result` per *executed* step; a step that never
      executes gets neither, so its outcome and definition are recovered from
      behave's step object and registry when the scenario is finalised.
    * :meth:`eof` per feature file, then :meth:`close`, which writes.
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

        Raises:
            OSError: If the ``-o`` path cannot be opened for writing, including
                the :class:`app.utils.paths.ArtifactPathError` subclass a
                symlinked, junctioned or hard-linked destination raises.  The
                failure is deliberately not caught: see the eager-open comment
                below, and AAP 0.4.1, which makes an artifact that cannot be
                written a non-zero exit rather than a silent one.
        """
        super().__init__(stream_opener, config)
        self.result_set: ResultSet = new_result_set(
            dry_run=_config_flag(config, "dry_run"),
            tag_expression=_config_tag_expression(config),
        )
        self._closed = False
        self._source_line_cache: dict[str, list[str]] = {}
        self._current_source: str = ""
        #: The stream this formatter opened itself, as opposed to one handed to
        #: it pre-opened.  Held so that :meth:`close` can guarantee it is
        #: released even if behave's own house-keeping could not run.
        self._secure_stream: IO[Any] | None = None
        self._reset_feature_state()
        # Open eagerly, as behave's own JSON formatter does: an unwritable
        # ``-o`` path is then a startup failure rather than a surprise at the
        # end of a run, and the merge step can tell an empty shard file (a
        # worker that died) from an absent one (a worker that never started).
        self.stream = self._open_secure_stream()
        _ACTIVE_COLLECTORS.append(self)

    # -- output stream ------------------------------------------------------

    def _open_secure_stream(self) -> IO[Any]:
        """Open this formatter's output stream through the path authority.

        The **only** opener in this module, called from the constructor and
        from both writers, because a second route would be the one an attacker
        gets to use.  behave's own :meth:`~behave.formatter.base.Formatter.open`
        delegates to ``StreamOpener.open()``, which calls ``codecs.open()`` on
        the ``-o`` *pathname*: it resolves that name afresh, follows a symbolic
        link or a junction standing in its place, and truncates whatever it
        lands on before anything can refuse it (CWE-367/CWE-59/CWE-22).  A
        worker's output name lives under
        :func:`app.utils.paths.workers_dir`, which survives a ``--no-clean``
        run, so that name is not under this process's sole control.  :func:`app.utils.paths.open_artifact_write` creates and
        verifies every owned directory component under a held directory
        descriptor with ``O_NOFOLLOW``, refuses a symlinked or hard-linked
        destination, creates the file :data:`~app.utils.paths.ARTIFACT_FILE_MODE`
        - ``0o600``, because the document carries step arguments substituted
        from the Examples tables and failure text (CWE-732/CWE-359) - and
        truncates only *after* the object is established, so a refusal destroys
        nothing.

        The handle is then installed on behave's stream opener, which is not
        decoration: :meth:`~behave.formatter.base.Formatter.close_stream`
        asserts ``self.stream is self.stream_opener.stream`` and delegates the
        actual close to the opener, so installing it keeps behave's own
        house-keeping - one close, at the end of the run - working exactly as
        it does for a stream behave opened itself.

        A **pre-opened** stream is handed back unchanged through behave's route:
        it was opened by whoever constructed the opener, no pathname is
        resolved, and there is therefore no window for the path authority to
        close.  That is how a stdout-driven run (``-o -``, or no ``-o`` at all,
        where behave supplies ``StreamOpener(stream=sys.stdout)`` and no
        filename) and this module's own tests construct it.

        Returns:
            The stream to write the document to, also stored on
            :attr:`stream` so that the next caller finds it open.

        Raises:
            OSError: If the destination cannot be opened, including the
                :class:`app.utils.paths.ArtifactPathError` subclass a refused
                path raises.  Both are the writer-failure class of AAP 0.4.1,
                which is why neither is translated into anything narrower.
        """
        opener = self.stream_opener
        existing = getattr(opener, "stream", None)
        if existing is not None and not getattr(existing, "closed", False):
            # Pre-opened and still usable: behave's ``open()`` returns it
            # without touching the filesystem, which is the whole of what this
            # branch needs to be.
            return self.open()
        name = getattr(opener, "name", None)
        if not name:
            # No filename to verify - a stdout-driven run.  behave owns the
            # stream it built the opener around, and nothing is opened here.
            return self.open()
        # A previous handle can only still be open if something replaced
        # ``self.stream`` without closing it; release it rather than leak the
        # descriptor for the rest of the worker's life.
        self._release_secure_stream()
        encoding = getattr(opener, "encoding", None) or _OUTPUT_ENCODING
        handle = open_artifact_write(
            name, encoding=encoding, newline=_OUTPUT_NEWLINE
        )
        self._secure_stream = handle
        try:
            opener.stream = handle
            opener.should_close_stream = True
        except (AttributeError, TypeError):
            # An opener that will not hold the handle cannot close it either,
            # so behave's house-keeping is out; :meth:`close` still releases it
            # through :attr:`_secure_stream`.
            logger.warning(
                "behave's stream opener would not accept the verified output "
                "stream; the collector will close it itself"
            )
        self.stream = handle
        return handle

    def _release_secure_stream(self) -> None:
        """Close the stream this formatter opened, if it is still open.

        Belt-and-braces behind :meth:`~behave.formatter.base.Formatter.close_stream`,
        which is what normally closes it: that method asserts the stream is
        still the opener's and delegates to the opener, so a stream whose
        opener no longer recognises it - or one whose installation on the
        opener failed - would otherwise stay open with the document unflushed.
        Idempotent, because ``close_stream`` has usually closed the handle
        already by the time this runs, and it never raises: it is called from
        :meth:`close`, at the very end of a run.
        """
        handle = self._secure_stream
        self._secure_stream = None
        if handle is None or getattr(handle, "closed", False):
            return
        try:
            handle.close()
        except Exception:
            # ``Exception`` rather than ``OSError``: this runs in
            # :meth:`close`'s ``finally``, where behave is already finishing a
            # run that may have passed, so nothing raises out of here - not an
            # unflushed buffer's write error, and not a replaced stream object
            # whose ``close`` misbehaves.
            logger.debug("Closing the verified output stream failed", exc_info=True)

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

    def _record_collection_error(
        self,
        event: str,
        error: BaseException | str,
    ) -> None:
        """Mark the document incomplete and record why.

        The whole of the port's answer to *"a formatter may not raise, but a
        report may not lie either"*.  Anything that loses an event -- a
        guarded hook that failed, a scenario announced with no feature open, a
        step announced with no element current, a result that matched no
        announced step, or a failed stage of :meth:`close` -- lands here, and
        the document then carries ``complete: false`` and the reason.
        :func:`load_result_set` refuses such a document, which is how the
        parent process comes to name the shard dead rather than merging valid
        partial JSON as if it were a complete run.

        Args:
            event: Name of the event, hook or stage that lost data.
            error: The exception that caused it, or a description when the
                loss was a decision rather than a raise.

        Note:
            ``complete`` is set first and the capped list append -- the part a
            strange ``result_set`` could refuse -- happens afterwards, so the
            document is marked even when recording the detail fails.  An
            ordinary :class:`Exception` here is logged at debug level and
            suppressed; :class:`KeyboardInterrupt` and :class:`SystemExit`
            propagate.
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
            The lines, or an empty list when the file cannot be read - in
            which case the callers fall back to behave's parsed values.  An
            :class:`OSError` is logged at debug level and suppressed, and each
            file is read at most once per formatter.
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
            nothing is available.  This runs on the path where something has
            already gone wrong, so an ordinary :class:`Exception` degrades to
            ``"<unknown source>"``; interrupts propagate.
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
                into.  behave's *raw* name, never the record's: the record's
                is already redacted and an offset taken against it would index
                the wrong characters.  :meth:`_apply_match` redacts the pair
                afterwards, together.
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
                # known, so it is *located* in the name rather than dropped or
                # recorded at an offset that does not hold it: the schema
                # requires the pair to describe a span that fits inside the
                # step text (``_validate_match``), because a consumer reads
                # ``val`` and ``offset`` as the position of a value in that
                # text.  The reported start is preferred when the text really
                # is there, and the first occurrence is used otherwise.
                value = str(getattr(argument, "original", "") or argument.value)
                offset = _locate_span(
                    step_name,
                    value,
                    start if isinstance(start, int) else None,
                )
                if offset is None:
                    # The converted value does not occur in the step text at
                    # all, so there is no span to record.  The JVM's own shape
                    # for an argument without a locatable value is the empty
                    # mapping, which keeps the argument *count* matching the
                    # definition's parameters instead of inventing a position.
                    logger.debug(
                        "A converted step argument does not occur in the step "
                        "text; recording the JVM's empty-argument shape"
                    )
                    built.append({})
                    continue
            built.append({"val": value, "offset": offset})
        return built

    def _apply_match(self, record: JsonDict, match: Any, raw_name: str) -> None:
        """Record the outcome of resolving a step definition.

        ``raw_name`` is behave's *unredacted* step text and is not optional:
        behave's argument spans index into that text, so the argument list has
        to be built against it and the redaction applied to the name and the
        arguments **together** (:func:`redact_step_text`).  ``record["name"]``
        cannot serve, because :func:`new_step` has already redacted it and an
        offset computed against a masked name would be silently wrong.  The
        joint result then overwrites ``record["name"]``, which is what makes
        the offset contract exact rather than approximate.

        Args:
            record: The step object to fill in.
            match: behave's ``Match`` for a resolved step, or ``NoMatch`` for
                an undefined one.  ``NoMatch`` carries ``func=None``, which is
                the only reliable discriminator, and yields ``match == {}`` -
                the JVM likewise writes no ``location`` for an undefined step.
            raw_name: behave's step name, as substituted and before redaction.
        """
        func = getattr(match, "func", None)
        if func is None:
            record["matched"] = False
            record["match"] = {}
            return
        match_map: JsonDict = {"location": _dotted_path(func)}
        arguments = self._build_arguments(
            raw_name, getattr(match, "arguments", None)
        )
        redacted_name, redacted_arguments = redact_step_text(raw_name, arguments)
        record["name"] = redacted_name
        if redacted_arguments:
            match_map["arguments"] = redacted_arguments
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
            return
        self._apply_match(record, match, _behave_step_name(step))

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
            reports ``0`` rather than a duration measured against nothing.  An
            ordinary :class:`Exception` from the clock yields ``None``, since a
            clock is not worth a lost scenario; interrupts propagate.
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
            taken.  Never negative, and a monotonic source that raises an
            ordinary :class:`Exception` yields ``0`` rather than a nonsense
            duration; interrupts propagate.
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
        entry, because the reference report has no ``after`` array at all and
        inventing one would change a frozen artifact.  An entry that records a
        **failure** is emitted here with or without an attachment, and
        ``cucumber_json._build_after`` publishes it on the same terms: a
        teardown that failed without capturing a screenshot is exactly the
        case a screenshot-gated writer dropped.

        A finalised status other than ``passed`` is also announced once per
        scenario at ``WARNING``, because a teardown that failed may have left
        a driver running or a screenshot untaken.  The record carries only the
        scenario's *source* identity from :meth:`_safe_identity`, the status
        and the hook's dotted location -- never the scenario name, which
        ``Login.feature``'s Examples fill with plaintext credentials, and
        never the error body, which can be an arbitrary page dump.

        Note:
            An ordinary :class:`Exception` is recorded as a collection error
            like any other lost event; interrupts propagate.
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
                    # Both sources are already sanitized - ``message`` comes
                    # from :func:`_failure_text` and ``reported`` from
                    # :meth:`record_hook_result` - and the rule is idempotent,
                    # so applying it again costs nothing and closes the gap if
                    # a third source is ever added.
                    result["error_message"] = sanitize_failure_text(text)
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

    def add_attachment(
        self,
        embedding: JsonDict,
        hook_location: str | None = None,
    ) -> bool:
        """Attach an embedding to the current scenario's after-hook entry.

        An arriving attachment is one of the three things that make a hook
        entry real, so the entry is created here if the location has none yet.
        Its ``status`` and ``duration`` are *not* decided here: they stay
        placeholders until :meth:`_finalize_hook_entries` reads the scenario
        model's real outcome, which is the only correct moment, because behave
        has not run the after-hooks yet when an attachment made from inside
        one arrives.

        Args:
            embedding: A mapping with ``mime_type``, ``data`` and optionally
                ``name``, as ``app/reporting/screenshots.py`` builds it.
            hook_location: Dotted path of the recording hook; defaults to
                :data:`DEFAULT_AFTER_HOOK_LOCATION`.  Embeddings recorded
                under one location share a hook entry, as the JVM groups them.

        Returns:
            ``True`` when it was recorded, ``False`` when no scenario is
            current or an ordinary :class:`Exception` stopped the recording
            (interrupts propagate).  ``False`` does **not** mark the document
            incomplete: AAP deviation 19 makes suppressed failure evidence a
            tolerated outcome, and a missing screenshot loses no scenario,
            step or status.
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

        The seam for a lifecycle producer that knows what this collector
        cannot observe: a hook's real duration or a failure it handled
        itself.  ``features/environment.py`` owns the scenario lifecycle and
        imports nothing from this package, so it reaches this through the
        module-level :func:`record_hook_result`.  Reported values are
        authoritative; :meth:`_finalize_hook_entries` fills in the model's
        measured values only where nothing was reported.

        Args:
            location: Dotted path of the hook; defaults to
                :data:`DEFAULT_AFTER_HOOK_LOCATION`.
            status: The hook's outcome in behave's vocabulary, or ``None`` to
                let the scenario model decide it.
            duration: The hook's duration in nanoseconds, or ``None`` to let
                the measured window stand.  A negative value is recorded as
                ``0``, as every duration in this schema is.
            error_message: The hook's failure text, or ``None``.  It is
                recorded through :func:`sanitize_failure_text` - LF-normalised,
                with classified values masked, absolute paths relativised and
                the length bounded - and otherwise as given: a caller with an
                exception in hand should build the text with the same
                message-then-traceback shape the rest of the document uses.
            embeddings: Attachments to record under the same entry.

        Returns:
            ``True`` when it was recorded, ``False`` when no scenario is
            current or an ordinary :class:`Exception` stopped the call (which
            must not fail a run); interrupts propagate.
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
                reported["error_message"] = sanitize_failure_text(error_message)
            for embedding in embeddings or ():
                entry.setdefault("embeddings", []).append(dict(embedding))
            return True
        except Exception:
            logger.exception(
                "A hook result could not be recorded; the scenario's step "
                "results are unaffected"
            )
            return False

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
            name=_behave_step_name(step),
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
            self._apply_match(
                record, self._pending_match, _behave_step_name(step)
            )
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
        defaults.  No ordinary :class:`Exception` leaves this method - behave
        calls it at the very end of a run, and one would fail a suite that had
        already passed - while an interrupt still propagates.

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
                logger.debug("Collector was already de-registered")
            try:
                self.close_stream()
            except Exception:
                logger.debug("Closing the output stream failed", exc_info=True)
            # behave's ``close_stream`` closes the stream through the opener it
            # was installed on, so this is normally a no-op; it is what makes
            # the descriptor release unconditional when that route could not
            # run - an opener that no longer recognises the stream, or one that
            # would not hold it in the first place.
            self._release_secure_stream()

    def _earliest_start_timestamp(self) -> str | None:
        """Return the earliest **selected** scenario ``start_timestamp``.

        Returns:
            The earliest timestamp among the scenarios the tag expression
            selected, or ``None`` when none of them carries one.

        Note:
            Scenarios the tag expression *excluded* are deliberately not
            considered, even though behave announces them and this document
            records them.  The run-level ``started_at`` is what both HTML
            writers print as the run's start, and it has to be the same
            instant the rest of the page describes: the JSON artifact omits an
            unselected scenario entirely (the JVM never starts one), and
            ``app/reporting/aggregation.py`` computes its own earliest start
            over the scenarios that survive selection.  Counting an excluded
            scenario here made those two values differ, and
            ``app/reporting/html_report.py`` prefers this one - so a single
            page showed a run starting before the first scenario it lists.
            Selection is the rule the artifacts agree on, so it is the rule
            this value follows.
        """
        stamps = [
            element["start_timestamp"]
            for _, element in iter_scenarios(self.result_set)
            if element.get("start_timestamp") and element.get("selected", True)
        ]
        return _min_timestamp(stamps)

    def _write_document(self) -> None:
        """Serialise the document to the formatter's stream.

        The stream is opened by the constructor; it is re-opened here only if
        something closed it early - and through :meth:`_open_secure_stream`,
        never through behave's pathname opener, because the path that matters
        most is exactly the one taken when the stream went away mid-run.  A
        stream whose encoding cannot represent the document - possible when the
        process locale is ASCII and a scenario name or failure message is not -
        is retried with escaped non-ASCII rather than left without a document
        at all.
        """
        stream = self.stream or self._open_secure_stream()
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
            and truncated first when it supports both - the verified stream
            :meth:`_open_secure_stream` returns does, which is asserted in
            ``tests/test_events.py`` rather than assumed of it - so the minimal
            document replaces any partial write rather than being appended to
            it; when it does not, the file is left for the caller's parse check
            to reject.  Only the run-level envelope is reproduced: the features
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
            stream = self.stream or self._open_secure_stream()
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
        path: Destination file.  Its parent directory is created - and every
            component of it from ``target`` inward verified - by
            :func:`app.utils.paths.open_artifact_write`, so a worker never
            fails merely because the ``--clean`` step emptied the build-output
            directory, and the file that is opened is the one that was
            verified.  The path itself always comes from
            :mod:`app.utils.paths` - this module contains no path literal.

    Returns:
        The path written.

    Raises:
        OSError: If the file or its parent directory cannot be created or
            written, or if the destination or a directory above it is refused
            as a symbolic link, a junction or a hard link to a file elsewhere
            (:class:`app.utils.paths.ArtifactPathError`).  Deliberately not
            swallowed: producing an artifact is the caller's contract with the
            exit table, and a silent failure would leave the merge reading a
            file that is not there.
    """
    destination = Path(path)
    # One descriptor-bound open rather than ``ensure_parent`` plus the builtin:
    # that pair verified the parent, released it, and then re-resolved the same
    # name, so a link swapped in between the two redirected the write and
    # truncated whatever it pointed at (CWE-367/CWE-59).  newline="\n" so a
    # document written on Windows is byte-identical to one written on Linux:
    # durations and timestamps vary by construction, the document's structure
    # must not.
    with open_artifact_write(
        destination, encoding=_OUTPUT_ENCODING, newline=_OUTPUT_NEWLINE
    ) as stream:
        stream.write(f"{_serialize(result_set)}\n")
    return destination


# The key sets below express one rule: a document this build will merge is a
# document this build's own builders could have written.  Every key
# :func:`new_feature`, :func:`new_element`, :func:`new_step` and
# :func:`new_hook_entry` always emits is REQUIRED and an unknown key is
# refused, because the merge and the writers are tolerant - they skip what
# they cannot read, so a structurally invalid shard would otherwise be
# silently reduced and published as a complete run.  The exceptions are the
# ones the schema itself has: a scenario's ``tags`` (omitted when it has
# none), an empty ``match`` (an undefined step), ``result.duration`` and
# ``result.error_message``, every run-level key except ``features`` (defaulted
# by :func:`_coerce_result_set`, which is what lets the hand-written
# ``tests/fixtures/sample_results.json`` omit ``complete`` and
# ``collection_errors``), and ``metadata``, whose probe-driven vocabulary is
# validated structurally by :func:`_validate_metadata`.  ``bool`` is rejected
# where an ``int`` is meant, since ``bool`` is an ``int`` subclass in Python
# and ``True`` would otherwise pass as a duration.

_RUN_REQUIRED_KEYS: Final[frozenset[str]] = frozenset({"features"})
_RUN_OPTIONAL_KEYS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "started_at",
        "generated_at",
        "dry_run",
        "tag_expression",
        "metadata",
        "complete",
        "collection_errors",
    }
)

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

_SCENARIO_REQUIRED_KEYS: Final[frozenset[str]] = frozenset(
    {"id", "start_timestamp", "after"}
)

_SCENARIO_OPTIONAL_KEYS: Final[frozenset[str]] = frozenset({"tags"})

_BACKGROUND_FORBIDDEN_KEYS: Final[frozenset[str]] = (
    _SCENARIO_REQUIRED_KEYS | _SCENARIO_OPTIONAL_KEYS
)

_STEP_REQUIRED_KEYS: Final[frozenset[str]] = frozenset(
    {"keyword", "line", "name", "matched", "match", "result"}
)

_MATCH_OPTIONAL_KEYS: Final[frozenset[str]] = frozenset({"location", "arguments"})

#: A **hook** entry's ``match`` keys.  Only ``location``, and optional: a hook
#: takes no parameters, so ``arguments`` names nothing there, and
#: :func:`new_hook_entry` writes ``{}`` when it cannot name the hook function.
_HOOK_MATCH_OPTIONAL_KEYS: Final[frozenset[str]] = frozenset({"location"})

#: ``match.arguments`` entry keys.  Both optional: the JVM emits an empty
#: object for a parameter that has no value.
_ARGUMENT_OPTIONAL_KEYS: Final[frozenset[str]] = frozenset({"val", "offset"})

_RESULT_REQUIRED_KEYS: Final[frozenset[str]] = frozenset({"status"})
_RESULT_OPTIONAL_KEYS: Final[frozenset[str]] = frozenset(
    {"duration", "error_message"}
)

_HOOK_REQUIRED_KEYS: Final[frozenset[str]] = frozenset(
    {"match", "result", "embeddings"}
)

_EMBEDDING_REQUIRED_KEYS: Final[frozenset[str]] = frozenset({"mime_type", "data"})
_EMBEDDING_OPTIONAL_KEYS: Final[frozenset[str]] = frozenset({"name"})

#: Tag keys.  ``name`` is the only key both shapes carry; ``type`` and
#: ``location`` are the feature-level long shape and are **required there and
#: refused at scenario level**, which is what :func:`_validate_tag` enforces
#: per level.  The pair is not a tolerance: a feature tag without its own
#: ``location`` leaves the JSON writer nothing to copy but the feature's line,
#: and the baseline's tag sits on line 1 while its ``Feature:`` keyword sits
#: on line 2.
_TAG_REQUIRED_KEYS: Final[frozenset[str]] = frozenset({"name"})
_TAG_OPTIONAL_KEYS: Final[frozenset[str]] = frozenset({"type", "location"})

#: ``tag.location`` members, both **required** whenever a ``location`` is
#: present - which at feature level is always.  A half-populated location is
#: refused rather than completed, because completing it means inventing the
#: missing half.
_TAG_LOCATION_REQUIRED_KEYS: Final[frozenset[str]] = frozenset({"line", "column"})

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


def _check_timestamp(value: Any, source: str, path: str) -> None:
    """Require ``value`` to be a contract timestamp, or ``None``.

    The ingress rule for ``started_at``, ``generated_at`` and a scenario's
    ``start_timestamp``: ``None`` - meaning "no scenario ran" or "not stamped
    yet" - or exactly the spelling :data:`TIMESTAMP_PATTERN` fixes and
    :func:`format_timestamp` emits.

    ``None`` stays admissible because it is a real state this schema has:
    :func:`new_result_set` starts both run-level stamps at ``None``, and a
    scenario the tag expression excluded is announced without one.  What is
    refused is a *string* that is not this contract's timestamp, because the
    surfaces downstream split on it - the templates display whatever string
    they are given, ``app/reporting/aggregation.py`` parses it and drops what
    it cannot read, and the merge orders by it - so a malformed value becomes
    a literal on one page and a missing run start on another, with nothing
    left able to name the shard it came from.

    Args:
        value: The value to check.
        source: Where the document came from.
        path: JSON-pointer-style path of the value.

    Raises:
        ResultSetError: If it is neither ``None`` nor a string, or is a string
            that :func:`parse_timestamp` cannot read.
    """
    if value is None:
        return
    _check_string(value, source, path, MAX_STRING_LENGTH)
    if parse_timestamp(value) is None:
        _reject(
            source,
            path,
            f"is '{_log_safe_text(value)}', which is not a "
            "YYYY-MM-DDTHH:MM:SS.mmmZ UTC timestamp",
        )


def _check_int(value: Any, source: str, path: str) -> None:
    """Require ``value`` to be a real integer.

    The type half of the rule only.  Every integer in this schema also has a
    *range*, and the two callers below - :func:`_check_positive_int` for a
    source position and :func:`_check_nonnegative_int` for a duration or an
    offset - are what apply it.  Nothing calls this function on its own: a
    field validated for type alone is the hole the range rules exist to close,
    because each consumer then applies its own range assumption and the
    artifacts disagree.

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


def _check_positive_int(value: Any, source: str, path: str) -> None:
    """Require ``value`` to be an integer of at least one.

    The rule for every **source position** in the schema: a feature's line, an
    element's line, a step's line, and a tag's ``location`` line and column.
    Gherkin numbers all of them from one, so zero and below name no position
    in any feature file, and a document carrying one is not a document this
    build's collector wrote.

    Rejected rather than tolerated, because each consumer reads these numbers
    for something different and none of them can detect the gap on its own:
    ``app/reporting/rerun_report.py`` writes a feature's failing lines into
    the rerun manifest, which is machine input to the next ``--rerun`` run,
    and an entry pointing at line ``0`` selects nothing while still looking
    like a manifest;  ``app/reporting/cucumber_json.py`` copies the line into
    the published artifact the Jenkins publisher reads; and
    ``app/reporting/pretty_reports.py`` renders it as a source reference a
    human is expected to open.

    Args:
        value: The value to check.
        source: Where the document came from.
        path: JSON-pointer-style path of the value.

    Raises:
        ResultSetError: If it is not an integer, or is below one.
    """
    _check_int(value, source, path)
    if value < 1:
        _reject(
            source,
            path,
            f"is {value}; a source line or column is numbered from 1, so it "
            "cannot be zero or negative",
        )


def _check_nonnegative_int(value: Any, source: str, path: str) -> None:
    """Require ``value`` to be an integer of at least zero.

    The rule for every **measured quantity**: a step's or hook's ``duration``
    in nanoseconds and an argument's ``offset`` into its step name.  Zero is
    legitimate for both - a skipped step measures zero and a parameter can
    start at the first character - and both are produced clamped
    (:func:`nanos_from_seconds`, :meth:`ResultCollectorFormatter._measured_hook_duration`
    and :func:`new_hook_entry` all floor at zero), so a negative value means
    the document was not written by this build.

    A negative duration is refused rather than carried because the writers
    disagree about it in a way no consumer can see: ``cucumber_json`` emits
    ``duration`` whenever the value is non-zero, so ``-1`` would be published
    to the publisher as a negative interval, while the HTML writers format the
    same value for display and the aggregate sums it.

    Args:
        value: The value to check.
        source: Where the document came from.
        path: JSON-pointer-style path of the value.

    Raises:
        ResultSetError: If it is not an integer, or is below zero.
    """
    _check_int(value, source, path)
    if value < 0:
        _reject(
            source,
            path,
            f"is {value}; a duration or an offset is a measured quantity and "
            "cannot be negative",
        )


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


def _check_budget(
    document: Any, source: str, budget: RunResultBudget | None = None
) -> int:
    """Bound a parsed document's depth, node count and string lengths.

    The generic half of the resource boundary (CWE-400), and it runs **before**
    any schema rule for two reasons.  First, a schema rule only reaches the
    keys it knows, so an unknown key's value has no per-field limit: a
    500-deep object or a megabyte string inside one would reach the merge and
    exhaust the parent in :func:`copy.deepcopy`.  Second, a limit breach is
    the cheapest rejection there is, so it comes first.

    The walk is **iterative**, over an explicit stack, and that is not a style
    choice: a recursive walk would hit CPython's own recursion limit on
    precisely the input this function exists to refuse, raising
    :class:`RecursionError` out of the validator instead of
    :class:`ResultSetError` and bypassing the parent's dead-worker handling.
    Nodes are counted as they are *pushed*, bounding the stack as well.

    Args:
        document: The parsed JSON value, of any shape.
        source: Where it came from, for the error messages.
        budget: The **run-wide** budget of the merge this document is being
            loaded into, or ``None`` for a one-off read.  Supplied, the walk's
            node total is charged to it once the document's own limit has been
            satisfied, so a run of individually valid shards cannot exhaust
            the parent by their sum.

    Returns:
        How many JSON nodes the document carries - the number this walk had to
        count anyway, returned so that the run-wide total costs no second
        pass.

    Raises:
        ResultSetError: If the document nests deeper than
            :data:`MAX_DOCUMENT_DEPTH`, carries more than
            :data:`MAX_DOCUMENT_NODES` nodes, carries a string longer than
            :data:`MAX_ANY_STRING_LENGTH`, carries a non-string object key
            (JSON has none, so one means the value did not come from
            :func:`json.loads`), or would take ``budget`` past
            :data:`MAX_RUN_RESULT_NODES`.  Every message names the limit that
            was breached and the path of the value that breached it.
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
    if budget is not None:
        budget.charge_nodes(source, nodes)
    return nodes


def _validate_tag(tag: Any, source: str, path: str, *, long_shape: bool) -> None:
    """Validate one tag against the exact shape its level carries.

    The two shapes are **not interchangeable**, and this function is where
    that stops being a convention.  A feature tag is the JVM's long shape -
    ``name``, ``type`` and a ``location`` carrying a ``line`` and a ``column``
    - and a scenario tag is the short one, ``name`` alone.  The asymmetry is
    measured in the reference report, where ``@Smoke`` appears at feature
    level as ``{"name": "@Smoke", "type": "Tag", "location": {"line": 1,
    "column": 1}}`` and on each of the four scenario elements as ``{"name":
    "@Smoke"}``.

    Accepting either shape at either level was not a tolerance, it was a hole:
    a feature tag arriving short carries no declaration site, and the only
    thing the JSON writer could then do was **invent** one from the feature's
    own line - which is wrong for exactly the case the baseline pins, where
    the tag sits on line 1 and the ``Feature:`` keyword on line 2.  Requiring
    the long shape here is what lets that writer copy the canonical value
    instead of synthesising a plausible one, and requiring the short shape at
    scenario level is what stops a ``location`` reaching an artifact that has
    never carried one.

    ``name`` must be non-empty and keep its leading ``@``: it is the only
    reason a tag exists, every consumer reads it, and
    ``app/reporting/pretty_reports.py`` builds a tag detail page per distinct
    name, so a nameless tag is a page about nothing.  :func:`feature_tag` and
    :func:`scenario_tag` restore the ``@`` behave strips, so a name without
    one did not come from them.

    Args:
        tag: The tag value.
        source: Where the document came from.
        path: JSON-pointer-style path of the tag.
        long_shape: ``True`` at feature level, where ``type`` and ``location``
            are required; ``False`` at scenario level, where both are refused.

    Raises:
        ResultSetError: If the tag is not an object; if its keys are not
            exactly the ones its level carries; if ``name`` is empty or lacks
            its leading ``@``; if ``type`` is anything but ``"Tag"``; or if
            ``location`` omits either member or gives one a value that is not
            a source position.
    """
    mapping = _check_mapping(tag, source, path)
    _check_object_keys(
        mapping,
        source,
        path,
        _TAG_REQUIRED_KEYS | _TAG_OPTIONAL_KEYS if long_shape
        else _TAG_REQUIRED_KEYS,
        frozenset(),
    )
    _check_string(mapping["name"], source, f"{path}.name", MAX_STRING_LENGTH)
    name = mapping["name"]
    if not name.startswith("@"):
        _reject(
            source,
            f"{path}.name",
            f"is '{_log_safe_text(name)}'; a tag name carries the leading '@' "
            "the JVM emits, which feature_tag() and scenario_tag() restore",
        )
    if not long_shape:
        return
    _check_string(mapping["type"], source, f"{path}.type", MAX_STRING_LENGTH)
    if mapping["type"] != TAG_TYPE:
        _reject(
            source,
            f"{path}.type",
            f"is '{_log_safe_text(mapping['type'])}'; a feature tag's type is "
            f"the literal {TAG_TYPE!r}",
        )
    location = _check_mapping(mapping["location"], source, f"{path}.location")
    _check_object_keys(
        location,
        source,
        f"{path}.location",
        _TAG_LOCATION_REQUIRED_KEYS,
        frozenset(),
    )
    for key in sorted(_TAG_LOCATION_REQUIRED_KEYS):
        _check_positive_int(location[key], source, f"{path}.location.{key}")


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
        _check_nonnegative_int(mapping["duration"], source, f"{path}.duration")
    if "error_message" in mapping:
        _check_string(
            mapping["error_message"],
            source,
            f"{path}.error_message",
            MAX_STRING_LENGTH,
        )


def _validate_match(
    match: Any,
    source: str,
    path: str,
    *,
    matched: bool,
    step_name: str,
) -> None:
    """Validate a step's ``match`` mapping against the step's own match state.

    The three parts of a match - the ``matched`` flag, the ``location`` and
    the ``arguments`` - describe **one** fact, and validating them separately
    let a document assert three different ones.  That split reached the
    artifacts: ``app/reporting/cucumber_json.py`` decides a dry run's step
    status from ``matched`` (matched steps ``passed``, unmatched
    ``undefined``), while ``app/templates/pretty/overview_steps.html`` groups
    the Steps overview by ``match.location`` - so a step claiming
    ``matched: true`` with no location was published as a passing step that
    belongs to no step definition, and one claiming ``matched: false`` while
    carrying a location was published as undefined on a definition the
    overview still counted.

    So the contract is stated once, here, and it is biconditional: **a step is
    matched if and only if it carries a non-empty ``location``.** An unmatched
    step's ``match`` is ``{}`` exactly - no location, and no arguments either,
    since arguments are the values a definition captured and an undefined step
    resolved to no definition.  That is what :meth:`ResultCollectorFormatter._apply_match`
    already writes for behave's ``NoMatch``; this makes it a rule rather than
    a habit.

    An argument is ``{}`` - which the JVM emits for a parameter that has no
    value - or carries **both** ``val`` and ``offset``.  Half an argument is
    refused because the pair is one datum: ``val`` is the raw matched
    substring of the step name and ``offset`` is where it starts, so a ``val``
    without an ``offset`` cannot be located and an ``offset`` without a
    ``val`` locates nothing.

    The pair must describe a span that **fits inside the step name**: a
    non-negative ``offset`` no greater than the name's length, and
    ``offset + len(val)`` no greater than it either.  Both endpoints are
    checked, because a span that starts inside the text and ends past it is
    exactly as unusable as one that starts past it - a consumer highlighting
    the parameter in the step text would read off the end of the string, and
    the field is published to the Jenkins publisher as the *location of a
    value in that text*.

    The rule stops one step short of ``name[offset:offset + len(val)] == val``,
    and that boundary is deliberate.  :func:`widen_quoted_span` slices every
    span it returns out of the name, so equality holds for it by
    construction; the collector's one fallback -
    :meth:`ResultCollectorFormatter._build_arguments` handling a converted
    argument - records the matched text and then *locates it in the name*,
    falling back to the JVM's empty-argument shape when it genuinely does not
    occur there, so it too satisfies the fit rule by construction.  Requiring
    byte equality on top of that would refuse a document over a diagnostic
    field's formatting and cost a whole worker's results, which is a worse
    outcome than a span that is in bounds but not a slice.

    Args:
        match: The ``match`` value.
        source: Where the document came from.
        path: JSON-pointer-style path of the mapping.
        matched: The owning step's ``matched`` flag, already type-checked.
        step_name: The owning step's ``name``, which the argument offsets
            index into.

    Raises:
        ResultSetError: If it is not an object; if it carries a key this level
            does not define; if its ``location`` is absent, empty or not a
            string while ``matched`` is true; if it carries anything at all
            while ``matched`` is false; if ``arguments`` is not a list within
            :data:`MAX_ARGUMENTS_PER_STEP`; or if an argument is neither empty
            nor a complete, in-bounds span.
    """
    mapping = _check_mapping(match, source, path)
    _check_object_keys(
        mapping, source, path, frozenset(), _MATCH_OPTIONAL_KEYS
    )
    if not matched:
        if mapping:
            _reject(
                source,
                path,
                "must be empty for a step whose 'matched' is false: an "
                "undefined step resolved to no definition, so it has neither "
                f"a location nor arguments, yet it carries "
                f"{', '.join(sorted(mapping))}",
            )
        return

    if "location" not in mapping:
        _reject(
            source,
            path,
            "must carry 'location' for a step whose 'matched' is true: the "
            "flag and the location state the same fact, and the artifacts "
            "read them separately",
        )
    _check_string(
        mapping["location"], source, f"{path}.location", MAX_STRING_LENGTH
    )
    if not mapping["location"]:
        _reject(
            source,
            f"{path}.location",
            "is empty for a step whose 'matched' is true; a matched step's "
            "location is the resolved step function's dotted path",
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
        if not entry:
            # The JVM's own shape for a parameter that captured no value.
            continue
        missing = sorted(_ARGUMENT_OPTIONAL_KEYS - set(entry))
        if missing:
            _reject(
                source,
                argument_path,
                f"carries {', '.join(sorted(entry))} without "
                f"{', '.join(missing)}: an argument is either empty or a "
                "complete span of a matched substring and its offset",
            )
        _check_string(
            entry["val"], source, f"{argument_path}.val", MAX_STRING_LENGTH
        )
        _check_nonnegative_int(
            entry["offset"], source, f"{argument_path}.offset"
        )
        if entry["offset"] > len(step_name):
            _reject(
                source,
                f"{argument_path}.offset",
                f"is {entry['offset']}, past the end of the "
                f"{len(step_name)}-character step name it indexes into",
            )
        end = entry["offset"] + len(entry["val"])
        if end > len(step_name):
            _reject(
                source,
                argument_path,
                f"spans characters {entry['offset']} to {end} of a "
                f"{len(step_name)}-character step name: the value and its "
                "offset locate a substring of the step text, so a span that "
                "ends past the text locates nothing",
            )


def _validate_hook_match(match: Any, source: str, path: str) -> None:
    """Validate a hook entry's ``match`` mapping.

    A hook's match is not a step's, which is why it is not
    :func:`_validate_match`: there is no ``matched`` flag to agree with - a
    hook entry exists because the hook ran - and a hook takes no parameters,
    so ``arguments`` names nothing.  What remains is the dotted path of the
    hook function, which :func:`new_hook_entry` omits entirely when it has
    none, mirroring the JVM's omission of ``location`` when it cannot name the
    hook.

    Args:
        match: The ``match`` value.
        source: Where the document came from.
        path: JSON-pointer-style path of the mapping.

    Raises:
        ResultSetError: If it is not an object, carries ``arguments`` or any
            other key this level does not define, or carries a ``location``
            that is not a non-empty string.  An **empty** mapping is valid and
            means the hook could not be named.
    """
    mapping = _check_mapping(match, source, path)
    _check_object_keys(
        mapping, source, path, frozenset(), _HOOK_MATCH_OPTIONAL_KEYS
    )
    if "location" not in mapping:
        return
    _check_string(
        mapping["location"], source, f"{path}.location", MAX_STRING_LENGTH
    )
    if not mapping["location"]:
        _reject(
            source,
            f"{path}.location",
            "is empty; a hook entry either names the hook function or omits "
            "the key, and an empty name reaches the report as a hook nothing "
            "can be attributed to",
        )


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
    _check_positive_int(mapping["line"], source, f"{path}.line")
    _check_string(mapping["name"], source, f"{path}.name", MAX_STRING_LENGTH)
    _check_bool(mapping["matched"], source, f"{path}.matched")
    # ``matched`` and ``name`` travel into the match rule: the flag because it
    # and the location state one fact, and the name because the arguments'
    # offsets are positions inside it.
    _validate_match(
        mapping["match"],
        source,
        f"{path}.match",
        matched=mapping["matched"],
        step_name=mapping["name"],
    )
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
    _validate_hook_match(mapping["match"], source, f"{path}.match")
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
        if not mapping["id"]:
            # An empty id is not a sparse field, it is a lost identity.  The
            # JSON writer used to rebuild one from the feature and scenario
            # names, which is correct for a plain scenario and *wrong for an
            # Examples row* - the row's id carries two further segments, the
            # block's slug and the row's position, which no other field of
            # the element records.  So the artifact received a plausibly
            # shaped identifier that named a test case the suite does not
            # contain, and the publisher, both HTML families and every detail
            # page keyed off it.  The id is computed once, by
            # :func:`scenario_element_id` with the JVM's own recursion, and is
            # required to have survived to here.
            _reject(
                source,
                f"{path}.id",
                "is empty: a scenario's id is the identity every artifact "
                "keys on, an Examples row's cannot be reconstructed from the "
                "element's other fields, and no consumer may invent one",
            )
        _check_timestamp(
            mapping["start_timestamp"], source, f"{path}.start_timestamp"
        )
    for key in ("keyword", "name", "description"):
        _check_string(mapping[key], source, f"{path}.{key}", MAX_STRING_LENGTH)
    _check_positive_int(mapping["line"], source, f"{path}.line")
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
            # Scenario level, so the short shape exactly: a ``location`` here
            # would reach an artifact whose scenario tags have never carried
            # one.
            _validate_tag(
                tag, source, f"{path}.tags[{index}]", long_shape=False
            )
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
    steps = _check_list(
        mapping["steps"],
        source,
        f"{path}.steps",
        MAX_STEPS_PER_ELEMENT,
        "MAX_STEPS_PER_ELEMENT",
    )
    for index, step in enumerate(steps):
        _validate_step(step, source, f"{path}.steps[{index}]")


def _check_feature_identity(feature: JsonDict, source: str, path: str) -> None:
    """Require a feature's ``uri`` and ``path`` to be present and to agree.

    The schema carries both spellings of one identity so that no consumer has
    to perform string surgery on the other - and three consumers duly read
    different ones.  ``app/reporting/cucumber_json.py`` copies ``uri`` into
    the published artifact; ``app/reporting/pretty_reports.py`` hashes ``uri``
    to name each feature's detail page; ``app/reporting/rerun_report.py``
    writes ``path`` into the rerun manifest the next ``--rerun`` run reads;
    and :func:`_feature_key` merges on ``path``.

    Accepting one member empty, or the two in disagreement, therefore split
    the run three ways: a feature could be published under one file name,
    linked under a hash of another, and re-run from a third - and a feature
    with an empty ``uri`` got a detail page named from an empty string, which
    the Pretty writer then declines to link at all.  None of the consumers can
    detect that on its own, because each one sees only the member it reads.

    Agreement is the **canonical** relationship, not merely equality after a
    strip: ``uri`` is exactly ``file:`` followed by ``path``
    (:data:`app.utils.paths.FILE_URI_SCHEME`), and ``path`` carries no scheme
    of its own.  Both halves are required because the artifacts are
    machine-read and each member has a fixed shape there: AAP 0.6 fixes the
    JSON ``uri`` as one ``file:`` prefix in front of a repository-relative
    path, and the rerun manifest's own ``file:`` prefix is added by
    ``app/reporting/rerun_report.py`` in front of ``path`` -- so a ``path``
    that already carried one would emit ``file:file:...`` into the manifest
    the next ``--rerun`` reads, and a scheme-less ``uri`` would publish a
    ``uri`` the publisher's schema does not describe.  A pair agreeing on the
    wrong shape - ``uri="file:file:X"`` with ``path="file:X"`` - passes an
    equality-after-strip test and is refused here.

    Args:
        feature: The feature object, whose ``uri`` and ``path`` are already
            known to be strings.
        source: Where the document came from.
        path: JSON-pointer-style path of the feature.

    Raises:
        ResultSetError: If either member is empty, if ``path`` carries the
            ``file:`` scheme, or if ``uri`` is not exactly the scheme followed
            by ``path``.
    """
    uri = feature["uri"]
    file_path = feature["path"]
    for key, value in (("uri", uri), ("path", file_path)):
        if not value:
            _reject(
                source,
                f"{path}.{key}",
                "is empty: a feature is identified by both its 'uri' and its "
                "'path', the JSON and Pretty writers read the first while the "
                "rerun manifest and the merge read the second, and an empty "
                "member sends them to different files",
            )
    if file_path.startswith(FILE_URI_SCHEME):
        _reject(
            source,
            f"{path}.path",
            f"is '{_log_safe_text(file_path)}', which carries the "
            f"{FILE_URI_SCHEME!r} scheme: 'path' is the repository-relative "
            "path alone, and the rerun manifest adds the scheme in front of "
            "it, so a scheme here is written twice",
        )
    if uri != f"{FILE_URI_SCHEME}{file_path}":
        _reject(
            source,
            path,
            f"carries a 'uri' of '{_log_safe_text(uri)}' where its 'path' of "
            f"'{_log_safe_text(file_path)}' requires "
            f"'{_log_safe_text(FILE_URI_SCHEME + file_path)}': the two are one "
            "identity in two fixed spellings, and the writers read them "
            "separately",
        )


def _check_element_units(
    elements: Sequence[Any], source: str, path: str
) -> None:
    """Require a feature's elements to be Background/scenario units.

    Validating each element on its own established that every element is
    well-formed; this establishes that the **list** is, which is a separate
    fact and the one every consumer depends on.  A Background occurrence is
    emitted *for* a scenario -
    :meth:`ResultCollectorFormatter.scenario` appends one immediately in front
    of each scenario, carrying that scenario's ``selected`` value - so the
    legitimate shapes are ``[background, scenario]`` and ``[scenario]``, and
    nothing else.

    Three consumers group the flat list back into those units, each for its
    own purpose and each with its own implementation: :func:`element_units`
    orders them for the merge, ``app/reporting/cucumber_json.py`` drops a
    unit whose scenario the tag filter excluded, and
    ``app/reporting/rerun_report.py`` folds a failing Background into the
    scenario it preceded.  On a list that is *not* units they disagree
    silently: a stray background is dropped by one, attached to the following
    scenario by another, and counted as a test case by a third, so one run is
    published as three different runs.

    So the malformed shapes are refused here, where the shard can still be
    named, rather than interpreted three ways downstream:

    * two backgrounds in a row - the second was emitted for no scenario;
    * a background at the end of the list, with no scenario after it;
    * a background whose ``selected`` differs from its scenario's, which would
      make the JSON writer emit a background for a test case it dropped, or
      drop the background of one it kept.

    Args:
        elements: The feature's ``elements`` list, already validated
            element by element.
        source: Where the document came from.
        path: JSON-pointer-style path of the feature.

    Raises:
        ResultSetError: On the first malformed unit, naming the element's
            index.
    """
    previous_index: int | None = None
    for index, element in enumerate(elements):
        is_background = element.get("type") == ELEMENT_TYPE_BACKGROUND
        if previous_index is not None:
            if is_background:
                _reject(
                    source,
                    f"{path}.elements[{index}]",
                    f"is a {ELEMENT_TYPE_BACKGROUND} following the one at "
                    f"index {previous_index}: an occurrence is emitted for a "
                    "scenario, so each one is immediately followed by the "
                    "scenario it ran for",
                )
            if element.get("selected") != elements[previous_index].get("selected"):
                _reject(
                    source,
                    f"{path}.elements[{index}]",
                    "does not share the 'selected' value of the "
                    f"{ELEMENT_TYPE_BACKGROUND} at index {previous_index} that "
                    "was emitted for it: a Background occurrence is part of "
                    "its scenario's unit and is kept or dropped with it",
                )
            previous_index = None
            continue
        if is_background:
            previous_index = index
    if previous_index is not None:
        _reject(
            source,
            f"{path}.elements[{previous_index}]",
            f"is a trailing {ELEMENT_TYPE_BACKGROUND} with no scenario after "
            "it: an occurrence is emitted for a scenario, and one without its "
            "scenario represents no test case at all",
        )


def _validate_feature(feature: Any, source: str, path: str) -> None:
    """Validate one feature object, including its whole element tree.

    Args:
        feature: The feature value.
        source: Where the document came from.
        path: JSON-pointer-style path of the feature.

    Raises:
        ResultSetError: If it is not an object; if it omits any of the nine
            keys :func:`new_feature` always emits; if it carries a key this
            level does not define; if ``uri`` and ``path`` are not both
            non-empty and in agreement (:func:`_check_feature_identity`); if
            ``elements`` is not a list within
            :data:`MAX_ELEMENTS_PER_FEATURE`; if any key, tag or element is
            malformed; or if the element list is not a sequence of
            Background/scenario units (:func:`_check_element_units`).  All
            nine keys are required, ``tags`` included: unlike a scenario's, a
            feature's ``tags`` is emitted unconditionally and is an empty list
            when the feature declares none, so an absent key means the list
            was lost rather than empty.
    """
    mapping = _check_mapping(feature, source, path)
    _check_object_keys(
        mapping, source, path, _FEATURE_REQUIRED_KEYS, frozenset()
    )
    for key in ("uri", "path", "id", "keyword", "name", "description"):
        _check_string(mapping[key], source, f"{path}.{key}", MAX_STRING_LENGTH)
    _check_feature_identity(mapping, source, path)
    _check_positive_int(mapping["line"], source, f"{path}.line")
    tags = _check_list(
        mapping["tags"],
        source,
        f"{path}.tags",
        MAX_TAGS_PER_LEVEL,
        "MAX_TAGS_PER_LEVEL",
    )
    for index, tag in enumerate(tags):
        # Feature level, so the long shape exactly, location included: it is
        # what the JSON writer copies instead of synthesising one.
        _validate_tag(tag, source, f"{path}.tags[{index}]", long_shape=True)

    elements = _check_list(
        mapping["elements"],
        source,
        f"{path}.elements",
        MAX_ELEMENTS_PER_FEATURE,
        "MAX_ELEMENTS_PER_FEATURE",
    )
    for index, element in enumerate(elements):
        _validate_element(element, source, f"{path}.elements[{index}]")
    # The list *as a list*, after each element has proved well-formed on its
    # own: the structure between them is a rule of its own, and it is the one
    # every consumer groups by.
    _check_element_units(elements, source, path)


def _validate_metadata(metadata: Any, source: str, path: str) -> None:
    """Validate the run-level ``metadata`` block as a bounded structure.

    The one level with no key list, and deliberately so: the block is whatever
    :func:`run_metadata`'s probes yielded -- today four groups of a ``name``
    and a ``version`` -- and ``app/templates/artifact/metadata.html`` renders
    it by iterating rather than by field, so a key list would have to be
    updated for every probe added and an unknown-key rejection would refuse a
    document a later build wrote.  The rule is therefore structural and
    strict: mappings and strings only, no list and no number, bounded depth
    and bounded total entries.  The walk is iterative for the same reason
    :func:`_check_budget`'s is.

    Args:
        metadata: The ``metadata`` value.
        source: Where the document came from.
        path: JSON-pointer-style path of the block.

    Raises:
        ResultSetError: If it is not an object; if it nests deeper than
            :data:`MAX_METADATA_DEPTH`; if it carries more than
            :data:`MAX_METADATA_ENTRIES` entries in total; or if any value is
            neither a mapping nor a string within :data:`MAX_STRING_LENGTH`.
            No group is required: a probe that yields nothing yields ``""``
            and a hand-built document may carry ``{}``.
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


def _validate_result_set(
    document: Any, source: str, budget: RunResultBudget | None = None
) -> ResultSet:
    """Validate a whole parsed document against this module's schema.

    Args:
        document: The parsed JSON value.
        source: Where it came from, for the error messages.
        budget: The run-wide budget this document is being loaded into, or
            ``None``.  Forwarded to :func:`_check_budget`, which charges it
            the document's node count.

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
    _check_budget(document, source, budget)
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

    for key in ("started_at", "generated_at"):
        if key in mapping:
            # The two run-level stamps get the timestamp rule rather than the
            # string rule: the merge orders by them and both HTML writers
            # display them, so a value neither can read has to stop here.
            _check_timestamp(mapping[key], source, key)
    if "tag_expression" in mapping:
        # Not a timestamp: a tag expression is free text in the grammar
        # ``cucumber-tag-expressions`` parses, and it is checked as a bounded
        # optional string.
        _check_optional_string(mapping["tag_expression"], source, "tag_expression")
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


def _coerce_result_set(
    document: Any, source: str, budget: RunResultBudget | None = None
) -> ResultSet:
    """Validate a parsed document strictly and fill in absent run-level keys.

    Args:
        document: The parsed JSON value.
        source: Where it came from, for the error message.
        budget: The run-wide budget this document is being loaded into, or
            ``None``.  Forwarded to :func:`_validate_result_set`.

    Returns:
        The document, with every run-level key present so that consumers never
        need a membership test.  Feature data is never invented or repaired:
        only the run-level envelope is completed, which is what lets a
        hand-written fixture omit boilerplate.  ``complete`` defaults to
        ``True`` and ``collection_errors`` to ``[]``, so a hand-written
        document that omits both reads as a complete one.

    Raises:
        ResultSetError: If the document violates the schema anywhere
            (:func:`_validate_result_set`), or if it declares itself
            incomplete.  A collector that dropped an event says so in the file
            it writes, and the whole point of saying so is that the document is
            then refused here: the parent names that shard dead, with the
            recorded reason, instead of merging partial results as complete.
    """
    validated = _validate_result_set(document, source, budget)

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


def load_result_set(
    path: Path | str, *, budget: RunResultBudget | None = None
) -> ResultSet:
    """Read one result document back from disk, validating it in full.

    The **only** gate between a worker's intermediate file and the merge, so a
    strict one: the whole schema is validated, every ``MAX_*`` resource limit
    is enforced and a document the collector marked incomplete is refused.
    Nothing downstream re-checks: what this returns can be merged, and what it
    rejects is a dead shard the caller names.  The read uses **one** descriptor
    -- opened with :data:`_RESULT_FILE_OPEN_FLAGS`, ``fstat``-ed, required to
    be a regular file, then read for at most one byte past the cap -- so the
    file measured is the file read, a symlink or FIFO in a shard's place is
    refused rather than followed, and a file that grew meanwhile is refused.

    The per-file limits it enforces bound **this** file.  They do not bound a
    run, and the caller holds every shard at once:
    ``app/services/test_run_service.py`` collects each live shard's document
    into a list before the merge reads any of it, so 87 files each just inside
    the per-file cap would have the parent hold about 21.75 GiB - every file
    individually valid, the sum fatal.  ``budget`` is what closes that: one
    :class:`RunResultBudget` per merge, charged the bytes this read consumed
    and the nodes it parsed, so the shard that would take the *run* past a
    limit is refused with the same :class:`ResultSetError` an oversized single
    file raises, and the caller's dead-shard handling applies to it unchanged.

    Args:
        path: The file to read - a per-worker intermediate from
            :func:`app.utils.paths.worker_result_path`, or a fixture.
        budget: The run-wide budget of the merge this shard belongs to.
            ``None`` - the default, for a one-off read such as a fixture in a
            test - applies the per-file limits only.

    Returns:
        The document, its envelope completed by :func:`_coerce_result_set`.

    Raises:
        ResultSetError: If the file is absent, not a regular file, too large,
            unreadable, not valid UTF-8, not valid JSON, nested too deeply for
            the parser, not this schema, over a per-file or run-wide resource
            limit, or declared incomplete by the collector that wrote it.  **One exception type
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
        # ``OSError`` also covers ``ELOOP`` from ``O_NOFOLLOW``, which is what
        # a symlink in a shard's place produces.  All of them mean "this shard
        # cannot be read", which is the one thing this function may say.
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
        # the parent.  Named ``remaining`` rather than ``budget`` because the
        # run-wide :class:`RunResultBudget` is this function's parameter: this
        # counter bounds *this read*, that one bounds the whole merge.
        remaining = MAX_RESULT_FILE_BYTES + 1
        chunks: list[bytes] = []
        try:
            while remaining > 0:
                chunk = os.read(descriptor, min(remaining, _READ_CHUNK_BYTES))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
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
    if budget is not None:
        # Charged before the parse, not after: a shard that would breach the
        # run's byte budget is refused without the parent paying for its
        # ``json.loads``, which is the allocation the budget exists to bound.
        budget.charge_document(source, len(raw))
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        # Decoded explicitly, and caught: a ``UnicodeDecodeError`` *is* a
        # ``ValueError``, so it reaches neither the ``OSError`` clauses above
        # nor the JSON one below, and only this clause keeps a corrupt shard
        # inside the parent's dead-worker handling.
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
    return _coerce_result_set(document, source, budget)


def element_units(elements: Sequence[JsonDict]) -> list[list[JsonDict]]:
    """Group a feature's elements into Background-plus-scenario units.

    **The one unit grouping for every consumer**, exported for that reason.  A
    Background occurrence is emitted *for* a scenario and belongs immediately
    in front of it, so the flat ``elements`` list is really a list of units,
    and four different things need them: the merge orders by the unit's test
    case (ordering the flat list by ``line`` would collect every background at
    the front, since all of a feature's occurrences share the Background's own
    line), ``app/reporting/cucumber_json.py`` keeps or drops a unit whole
    according to its scenario's selection, ``app/reporting/rerun_report.py``
    folds a failing Background into the scenario it preceded, and
    ``app/reporting/aggregation.py`` counts the unit once.

    Each of those used to group the list itself.  Four implementations of one
    rule is four chances to disagree about a list that is not units - and they
    did: a stray occurrence was dropped by one, attached to the next scenario
    by another, and counted as a test case by a third.  Two things fix that
    together: :func:`_check_element_units` refuses a malformed list at ingress,
    and this function is the single grouping the consumers share.

    Args:
        elements: A feature's elements, in document order.  Anything that is
            not a mapping is skipped, so the function is safe on a hand-built
            document that never passed :func:`load_result_set`.

    Returns:
        The units, in input order.  A unit is ``[background, scenario]``, or
        ``[scenario]`` for a feature with no Background.  A document that
        passed validation yields only those two shapes; on a hand-built one, a
        consecutive or trailing occurrence is kept as a ``[background]`` unit
        of its own rather than dropped, because losing a result silently is
        worse than carrying an odd one.
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
        unit: A unit from :func:`element_units`.

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
        ``"<unidentified feature>"`` when it carries neither, which is also
        what an ordinary :class:`Exception` from a hostile mapping yields;
        interrupts propagate.
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


def _ordered_timestamps(values: Iterable[Any]) -> list[tuple[datetime, str]]:
    """Pair each usable timestamp with the instant it names.

    Args:
        values: Candidate values, which may include ``None`` and non-strings.

    Returns:
        One ``(instant, spelling)`` pair per value :func:`parse_timestamp` can
        read, in input order.  A value it cannot read contributes nothing:
        every document that reached here through :func:`load_result_set` has
        already had its timestamps checked (:func:`_check_timestamp`), so an
        unreadable one belongs to a hand-built document, and a value no
        consumer can parse must not be able to become the run's start or its
        generation time.
    """
    paired: list[tuple[datetime, str]] = []
    for value in values:
        moment = parse_timestamp(value)
        if moment is not None:
            paired.append((moment, value))
    return paired


def _min_timestamp(values: Iterable[Any]) -> str | None:
    """Return the earliest of some timestamps, spelled as it arrived.

    Ordering is by **parsed instant**, with the spelling breaking a tie, so
    two shards that stamped the same instant give the same answer whatever
    order they merged in.  The string is returned verbatim rather than
    reformatted, so the value on a report page is the value the JSON artifact
    carries - which is the same rule ``app/reporting/aggregation.py`` follows.

    Args:
        values: Candidate values, which may include ``None`` and non-strings.

    Returns:
        The earliest usable timestamp exactly as it was written, or ``None``
        when none of the values is one.
    """
    paired = _ordered_timestamps(values)
    return min(paired)[1] if paired else None


def _max_timestamp(values: Iterable[Any]) -> str | None:
    """Return the latest of some timestamps, spelled as it arrived.

    The counterpart of :func:`_min_timestamp`, for ``generated_at``, which the
    merge takes as the latest of its inputs' rather than by reading a clock.

    Args:
        values: Candidate values, which may include ``None`` and non-strings.

    Returns:
        The latest usable timestamp exactly as it was written, or ``None``
        when none of the values is one.
    """
    paired = _ordered_timestamps(values)
    return max(paired)[1] if paired else None


def _merged_tag_expression(documents: Sequence[ResultSet]) -> str | None:
    """Return the run's tag expression, reporting shards that disagree.

    This is ``tag_expression``'s **one consumer**, and the reason the field is
    persisted at all.  Nothing reads it to decide anything - the filter has
    already been applied by the time a shard exists, the JSON artifact has no
    field for it, and neither HTML family prints it - so carried alone it was
    a value the schema validated and merged for no reader.  What it *can*
    answer is a question nothing else in this module can: **did these shards
    come from one run?**

    Every worker of a run is launched by ``app/services/test_run_service.py``
    with the same recorded expression, so two non-empty values that differ
    mean the merge is combining shards from two different runs - a stale
    intermediate left in the worker directory, or two runs sharing an
    output directory.  A merge that quietly took the first of them would
    publish one artifact set describing two filters, with no trace of which
    scenarios came from which.  So the disagreement is logged at ``WARNING``,
    naming both expressions, while the first non-empty value is still
    returned: naming the condition is diagnosis, and refusing to merge would
    discard results the run did produce.

    Args:
        documents: The documents being merged, in input order.

    Returns:
        The first non-empty ``tag_expression``, or ``None`` when no shard
        carries one - which is what ``--rerun`` produces, since it clears the
        default filter.
    """
    expressions = [
        document["tag_expression"]
        for document in documents
        if isinstance(document.get("tag_expression"), str)
        and document["tag_expression"]
    ]
    if not expressions:
        return None
    first = expressions[0]
    disagreeing = [value for value in expressions if value != first]
    if disagreeing:
        # Escaped and bounded like every other document-sourced fragment that
        # reaches a log line: this function accepts hand-built documents that
        # never passed the validator.
        logger.warning(
            "Merging shards recorded under different tag expressions (%s and "
            "%s); they are not from one run, and the merged report describes "
            "both",
            _log_safe_text(first),
            _log_safe_text(disagreeing[0]),
        )
    return first


def merge_result_sets(sets: Iterable[ResultSet]) -> ResultSet:
    """Merge per-worker documents into the one document the writers consume.

    The rules are the plan's (AAP 0.4.1): *one file per worker, merged by
    feature path, features in source order*.  A feature sharded across two
    workers yields one object whose elements are the union of the shards',
    while two features that merely share an ``id`` stay separate (see
    :func:`_feature_key`).  Source order is ascending feature path -- behave's
    own discovery order, and the only ordering independent of the sharding --
    and elements sort by test-case line, each Background occurrence kept in
    front of its scenario.  Nothing else is unioned: ``started_at`` is the
    earliest non-null value, ``generated_at`` the latest, ``dry_run`` true if
    any shard ran dry, ``tag_expression`` and ``metadata`` the first
    non-empty, ``complete`` true only if every input says so.

    Args:
        sets: The documents to merge, in any order.

    Returns:
        A new document; the inputs are never mutated.  The function is pure,
        so a fixed set of shards merges identically whatever the worker count,
        and an empty input yields an empty document.  A feature whose shape
        defeats :func:`copy.deepcopy` is skipped, recorded in
        ``collection_errors`` and marked incomplete rather than raising, which
        absorbs an ordinary :class:`Exception` only: interrupts propagate.
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
                    # The feature's own metadata, copied *without* its
                    # element tree: the elements are copied once, above, so
                    # copying them again here would double the peak memory of
                    # every merge for nothing.
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
        units = sorted(element_units(feature["elements"]), key=_unit_sort_key)
        feature["elements"] = [element for unit in units for element in unit]
        ordered_features.append(feature)

    started_at = _min_timestamp(document.get("started_at") for document in documents)
    if started_at is None:
        # A hand-built shard may carry scenarios without a run-level stamp.
        # Selected scenarios only, which is the rule
        # :meth:`ResultCollectorFormatter._earliest_start_timestamp` applies
        # when it stamps one: an excluded scenario is absent from the JSON
        # artifact and from the aggregate, so it cannot be what the run's
        # start refers to.
        started_at = _min_timestamp(
            element.get("start_timestamp")
            for _, element in iter_scenarios({"features": ordered_features})
            if element.get("selected", True)
        )
    tag_expression = _merged_tag_expression(documents)
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
        "generated_at": _max_timestamp(
            document.get("generated_at") for document in documents
        ),
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
