"""The grouped rerun manifest -- the machine input a retry run selects from.

A small file with a load-bearing format: ``FailedTestRunner.java:11`` declares
the manifest as its feature source, so what this module writes is what a rerun
executes and a format error silently changes which scenarios are retried.  The
50-byte reference manifest is one line, which under AAP deviation 1's feature
directory reads ``file:features/Crm.feature:9:24``.

* **One line per feature** with at least one failing scenario: the ``file:``
  prefix, the repository-relative path, then each failing scenario's line
  appended colon-separated.  Features in source order, line numbers ascending
  and deduplicated, LF endings and one trailing newline.
* A run with **no failures writes a zero-byte file**: the AAP 0.4.1 exit
  contract requires all four artifacts even then, so the write is never
  skipped and no placeholder line stands in for it.
* behave's own rerun formatter is never enabled or post-processed -- its shape
  is a ``# -- RERUN:`` header plus one ungrouped, unprefixed ``path:line`` per
  scenario -- so this module **reformats** (AAP 0.6) and emits no comment
  line, which :func:`_parse_manifest_line` rejects rather than tolerates.
* A scenario is failing when its status, rolled up over its steps, the
  preceding Background occurrence and both hook groups, is in
  :data:`FAILURE_STATUSES` -- wider than the literal ``failed``.  A background
  contributes no line, an outline row the data row's, non-selected none.
* An accepted entry is ``file:`` + ``features/<name>.feature`` + one or more
  ``:<line>``, enforced on **both** sides of the round trip by
  :func:`validate_feature_path` (lexical) and :func:`resolve_feature_path`
  (filesystem: no symlink, no non-regular file, no escape from the tree,
  CWE-22), which :func:`parse_rerun_file` applies on every production read.
* ``--rerun`` **clears the default tag filter** (that runner declares none),
  is a usage error together with ``--tags``, **writes no artifacts**, and must
  never clean, since ``--clean`` would delete the manifest it reads.

    file:src/main/resources/features/Crm.feature:9:24\\n

That is *two* failing scenarios, at lines 9 and 24, on **one line for the
feature**, behind the literal prefix ``file:``.  It agrees with the JSON
baseline's ``HEAD`` side -- same feature, same two failing scenarios, which are
precisely the two scenario elements carrying a failed step -- which is what
makes the pair usable as matched fixtures.  Under AAP deviation 1 the features
move to ``features/`` with their filenames preserved, so the port's line for
the same failure set is::

    file:features/Crm.feature:9:24\\n

The format, exactly
-------------------
* **One line per feature that has at least one failing scenario.**  A feature
  with no failures contributes no line at all.
* Each line is the literal prefix ``file:``, then the feature's
  repository-relative path, then **each failing scenario's line number appended
  colon-separated**.
* **Features in source order**; **line numbers ascending** within a line, and
  deduplicated, so a doubly-reported scenario can never yield ``:9:9``.
* LF line endings, never CRLF, and a **single** trailing newline -- a 50-byte
  baseline holding one line means exactly one ``\\n`` at the end and no blank
  final line.
* UTF-8, written with an explicit ``encoding="utf-8"`` and ``newline="\\n"`` so
  a Windows run cannot translate the terminator.  The ``.gitattributes`` update
  normalises ``*.py`` and ``*.sh``, not generated output, so this writer is
  explicit itself.
* A run with **no failures writes an empty file** -- zero bytes.  The AAP 0.4.1
  exit contract requires all four artifacts to be written even when nothing
  failed and when the tag expression selected nothing, so the write is never
  skipped and no placeholder line and no comment is emitted in its place.

This module reformats; it never passes behave's output through
-------------------------------------------------------------
behave ships a rerun formatter, and its shape is **not** the JVM's.  Measured in
``behave/formatter/rerun.py``, it writes a header comment,
``"# -- RERUN: %d failing scenarios during last test run.\\n"``, followed by one
``path:line`` per scenario: one line per *failure*, ungrouped, and with no
``file:`` prefix.  The JVM's manifest is grouped, prefixed and headerless.  AAP
0.6 is explicit that *"``app/reporting/rerun_report.py`` reformats"*.

Therefore:

* behave's rerun formatter is never enabled, wrapped or post-processed.  The
  lines are built from the internal result set produced by
  :mod:`app.reporting.events`, the single document every writer consumes.
* **No header, no comment line and no blank line is ever emitted.**  Nor is
  one *read*: because the two grammars differ in shape rather than in
  decoration, a file carrying behave's header is a file whose remaining lines
  mean something else, so :func:`_parse_manifest_line` rejects a comment line
  and requires the ``file:`` scheme rather than tolerating either.
* The feature's ``path`` field -- the bare relative path
  :mod:`app.reporting.events` carries *alongside* ``uri`` precisely so that no
  consumer has to strip a scheme by hand -- supplies the path, and this module
  prepends :data:`~app.utils.paths.FILE_URI_SCHEME` itself.  Shifting between
  the legacy and the port feature-directory prefixes goes through
  :func:`~app.utils.paths.normalize_feature_uri`, the single owner of that
  substitution.

What counts as a failing scenario
---------------------------------
:func:`iter_failed_scenarios` is the only implementation of the rule, and every
part of it is measured against the reference report:

* A scenario is failing when its rolled-up status is a failure token -- **not**
  when it equals the single literal ``failed``.  behave's failure vocabulary is
  wider than Cucumber's: ``error``, ``hook_error`` and ``cleanup_error`` are
  all folded onto ``failed``, and the JVM's rerun formatter records every test
  case whose ``Status.isOk()`` is false, which is everything but ``PASSED`` and
  ``SKIPPED``.  Comparing against one literal would therefore have the JSON
  report carry a failure the rerun manifest omitted, and the pair is read as a
  matched set.
* **That grading is not this module's own.**  The fold, the status vocabulary
  and the failure set live in :mod:`app.reporting.aggregation`, the one
  normalised result model AAP 0.3.4 requires, and this module consumes them:
  :func:`~app.reporting.aggregation.unit_status` for the roll-up and
  :func:`~app.reporting.aggregation.is_failure_token` for the decision.  A
  manifest graded by rules of its own is a manifest that can disagree with the
  report it is read beside, which is exactly what happened while both existed.
  :data:`FAILURE_STATUSES` still states the membership in recorded-name form,
  because it is a published constant, and ``tests/test_rerun_report.py``
  asserts that the set and the shared predicate agree across the whole
  vocabulary.
* Elements in the internal schema carry no status of their own, so the roll-up
  is over the scenario's steps, the steps of the Background occurrence
  **immediately preceding** it, and both its ``before`` and ``after`` hook
  entries -- the JVM's rerun formatter keys on the test-case result, which
  subsumes hooks, so a scenario whose setup hook failed and whose steps never
  ran is still re-selected.  That is the *effective scenario-unit status* the
  report surfaces now badge and count the same scenario by.
* **Under ``--dry-run`` the recorded status is not consulted.**  Measured: the
  JVM marks a matched step ``passed`` and an unmatched one ``undefined`` under
  ``dryRun``, and its rerun formatter then selects the scenario holding the
  undefined step and no other, while behave records ``untested`` for every
  step of a dry run.  The document's ``dry_run`` flag and each step's own
  ``matched`` flag therefore decide the status, through the same shared
  canonicalisation, so an unresolved step definition reaches the manifest
  instead of leaving it empty.
* **Backgrounds are not scenarios** and never contribute a line number of their
  own.  A background step failure fails the scenario it precedes, and it is the
  *scenario* element's ``line`` that reaches the manifest.  (Every Background
  occurrence in the reference shares the Background's own line, 6, which is
  exactly why emitting it would be wrong.)
* For an **outline row** the line is the **data row's** line, which is what the
  scenario element's ``line`` already carries: measured as element ``line`` 24
  for the row at ``Crm.feature:24``, even though its steps carry the outline
  template's lines 17-20.  That asymmetry is what makes the manifest
  re-selectable, because ``--rerun`` hands these locations to behave, which
  resolves ``path:line`` to the specific example row.
* **Non-selected scenarios never appear.**  behave announces scenarios the tag
  expression excluded; the JVM never starts them.  They did not run, so they
  did not fail.

Round trip, and the ``--rerun`` couplings
-----------------------------------------
AAP 0.6 requires that *"``--rerun`` round-trips: the file the writer produces
must select exactly the scenarios that failed"*, so both directions of the
grammar live here -- :func:`build_rerun_lines` writes it and
:func:`parse_rerun_file` reads it -- and ``app/cli.py`` re-implements neither.
Four couplings of that option follow from what ``FailedTestRunner.java:9-12``
*omits*, and are recorded here so the command-line surface cannot miss them:

* ``--rerun`` **clears the default tag filter.**  That runner declares no
  ``tags``, and applying ``default_tags`` to explicit locations would silently
  skip failures that came from the nine features without an ``@Smoke`` tag.
* ``--rerun --tags`` together are a **usage error** (a non-zero exit that
  executes nothing), because the two select scenarios by contradictory means.
* ``--rerun`` **writes no artifacts**, matching that runner's empty plugin
  list, and leaves the existing artifacts untouched.
* ``--rerun`` **must never clean**, because ``--clean`` would delete the very
  manifest it is about to read.

A missing or malformed manifest is *not* an execution failure: per the AAP 0.4.1
exit table it still exits ``0`` with the problem reported on stderr.  So the
parser raises :class:`RerunManifestError` -- a typed error the command-line
surface can catch and report -- and never calls :func:`sys.exit`, and never lets
a bare :class:`ValueError` or :class:`OSError` escape into a traceback.  That
is a whole-module guarantee and not a best effort: an embedded NUL byte in the
path handed to :func:`parse_rerun_file` and an oversized digit run in a line
segment both reach :func:`os.open` and :func:`int` as :class:`ValueError`, so
both are bounded and converted here (:data:`MAX_LINE_NUMBER_DIGITS`).

Reading the manifest is bounded, and its diagnostics carry no data
-----------------------------------------------------------------
Two properties of the *read*, both of them consequences of the file being
machine input at a known location inside a CI workspace rather than something
this process wrote a moment ago.

Every read goes through :func:`_read_manifest_text`, never
:meth:`~pathlib.Path.read_text`: :func:`~app.utils.paths.open_artifact_read`
opens a **no-follow, regular-file, single-link descriptor**, so a symbolic link
planted at the manifest's own location, a hard link to a file elsewhere, a
directory, a device and a FIFO are each refused rather than read -- and at most
:data:`MAX_MANIFEST_BYTES` + 1 bytes are taken in, so the grammar is never
reached by way of a file large enough to exhaust memory first (CWE-59,
CWE-400).  :data:`MAX_MANIFEST_LINES`, :data:`MAX_MANIFEST_ENTRIES` and
:data:`MAX_FEATURE_PATH_CHARACTERS` bound the rest of the work a tampered file
can demand, each stated as a number with the figure the real suite produces
beside it.

And no diagnostic this module writes reproduces what it refused.  A message
carries the source, the entry's one-based position and a reason drawn from a
fixed vocabulary -- :data:`_FORBIDDEN_CHARACTER_LABELS` is that vocabulary for
the character cases -- because these messages are written to stderr and
recorded verbatim by Jenkins, and echoing an untrusted line is how a tampered
manifest forges a console record or discloses its payload (CWE-117, CWE-532).
The position is what an operator locates the entry by.

That holds for a **feature path** as much as for a line: an entry's path is
text the manifest supplied, so the filesystem tier's refusals name the
features directory and the reason, the confinement tier names the entry's
position and how many locations it cost, and neither names the path.  The one
place a feature *is* named is the listing in :func:`list_verified_features`,
whose names come from this port's own verified read of the repository's
features directory rather than from a manifest -- the subject the run reports
on, which every artifact it writes names anyway.  A caller that knows a path's
provenance may therefore name it; this module, which is handed paths from both
sources and cannot tell them apart, does not.

The accepted entry, and where confinement is enforced
-----------------------------------------------------
The manifest is **machine input to a runner**: whatever a line names is what a
rerun executes.  ``--rerun`` therefore hands these locations to behave's argv,
and an entry naming ``../../etc/evil.feature`` or ``/etc/passwd`` would direct
execution at Gherkin outside the authoritative feature tree (CWE-22).  One
grammar answers that, and this module owns it on **both** sides of the round
trip, because a writer and a parser that state the rule separately are a rule
that drifts:

    an accepted entry is ``file:`` + ``features/<name>.feature`` + one or more
    ``:<line>``

and nothing else.  :func:`validate_feature_path` is the single statement of it
-- the **lexical tier** -- and it rejects a non-string, an empty value, CR, LF,
NUL, any other C0 control or DEL, a backslash, a colon, an absolute or UNC
path, a drive form, an empty or ``.``/``..``/hidden/whitespace-padded
component, a nested path, and a name that is not ``*.feature``.  It normalises
through :func:`~app.utils.paths.normalize_feature_uri` first, so the legacy
``src/main/resources/features/`` prefix of the committed fixture and of a stale
worker document is accepted and rewritten rather than refused.

The lexical tier alone is not the whole answer, because a name can be
well-formed and still not denote a Gherkin file in the tree: ``features/`` can
hold a **symlink** pointing outside it, a directory, a device node, or nothing
at all -- and ``features`` itself can be a link to a directory of someone
else's scenarios.  :func:`_open_verified_feature` is the **filesystem tier**
that settles that, reached through :func:`read_verified_feature`,
:func:`verify_feature_identity`, :func:`list_verified_features` and
:func:`resolve_feature_path`; :func:`parse_rerun_file` applies it itself
whenever it resolves the manifest's own location from :mod:`app.utils.paths`
-- which is every production read.  A tier that some caller has to remember to
invoke is a tier that is one forgotten call site away from absent, so the
confinement is in the production read path rather than beside it:

* ``app/services/test_run_service.py`` -- which appends a rerun location to the
  behave argv -- calls ``parse_rerun_file(base=base)``, so every entry it
  receives has passed **both** tiers and names a regular, non-symlinked file
  directly inside :func:`~app.utils.paths.features_dir`.  It therefore needs no
  confinement logic of its own, and its own resolution of an entry is a
  convenience rather than a control.  An entry that fails the filesystem tier
  is **dropped with a diagnostic on stderr** instead of being returned: that is
  the tolerated malformed-manifest case of the AAP 0.4.1 exit table -- status
  ``0``, the problem reported -- and dropping only the offending entry is what
  keeps the other features' real failures in the rerun.
* :mod:`app.utils.paths` owns the prefix substitution and the directory
  location, and nothing more: :func:`~app.utils.paths.normalize_feature_uri`
  deliberately returns an unknown path unchanged, because normalisation is not
  validation.  The validation is here.

Handing a **path** rather than an open file to another process leaves a window
no in-process check can close, so the filesystem tier does not ask where a name
resolves -- it **opens what it checks and hands back what it opened**.  The
features root is opened ``O_NOFOLLOW`` relative to its own parent, so a link in
its place is refused instead of becoming the trust anchor every entry is then
judged against; the entry is opened ``O_NOFOLLOW`` relative to *that*
descriptor; the descriptor is ``fstat``-verified to hold a regular file with a
single hard link; and :func:`read_verified_feature` reads the **contents from
that same descriptor**, so a caller parses the object that was approved rather
than reopening a name that may since have been replaced (CWE-22, CWE-367).

One residual window is named rather than papered over.  behave runs in a worker
process and opens ``features/<name>.feature`` **by name**, and that spelling is
fixed by the AAP -- deviation 1 and AAP 0.4.2 pin the feature URI carried by
the JSON report and by this manifest to it -- so executing from a snapshot
under another path would break both pinned contracts.  What closes as
much of it as the contract allows is :func:`verify_feature_identity`: the
caller re-establishes the object identity immediately before the hand-off, so
an entry replaced after selection is refused and named instead of silently
executed.  The ten feature files this port owns are regular files with one
name each, so nothing legitimate is refused by any of these rules.

The writer is held to the same grammar, and it is the harder half: the internal
result document is assembled by worker processes, so a feature ``path`` is
worker-controlled input.  A path carrying LF would emit **two** valid entries
from one feature, a colon-bearing path fabricates a line number, and both are
refused before serialisation.  :func:`_format_entry_line` is the one place an
entry is concatenated and re-checks the finished line, so "one line per
feature" is enforced where it is produced.

Boundaries and determinism
--------------------------
* Only :mod:`app.reporting.aggregation` -- the normalised result model, for the
  status rules above -- :mod:`app.reporting.events`, which owns the document's
  schema, and :mod:`app.utils.paths`, which owns the location, are imported.
  All three are inside the ``RP``/``UT`` edge of the AAP 0.4.2 graph: no
  service (the edge runs ``SV --> RP``, never back), no Flask, no Selenium, no
  :mod:`app.config`.  The model imports nothing from here, so the direction is
  one-way and the module stays importable on its own.
* **No path literal appears here**: :func:`~app.utils.paths.rerun_txt_path`
  owns the location and :func:`~app.utils.paths.open_artifact_write` owns the
  write -- it creates and verifies every owned directory component under a held
  directory descriptor, refuses a symlinked or hard-linked destination and
  creates the manifest owner-only.  The accepted path's
  directory component is :data:`~app.utils.paths.FEATURES_DIR_NAME`, and
  :data:`PATH_SEPARATOR` is derived from
  :data:`~app.utils.paths.NORMALIZED_FEATURES_PREFIX` rather than written out.
  :data:`FEATURE_SUFFIX` is a grammar token and not a location -- it names no
  directory and resolves nothing -- which is why
  ``app/services/test_run_service.py`` declares the same token locally when it
  discovers feature files.
* Pure is split from impure.  :func:`build_rerun_lines` and
  :func:`build_rerun_text` read no clock, no working directory and no
  filesystem; :func:`write_rerun_txt` is the thin wrapper that touches disk.
* Ordering is *structural* determinism (AAP 0.6): features in source order,
  line numbers ascending.  ``sortingMethod: 'ALPHABETICAL'`` in ``Jenkins:15``
  is a publisher **display** option and imposes nothing on an artifact -- the
  features are deliberately **not** sorted alphabetically on its account.
* **Nothing here raises on a test outcome.**  ``pom.xml:25`` sets
  ``testFailureIgnore=true`` and all six ``Jenkins:15`` thresholds are ``-1``;
  failures are data.  A structurally unusable scenario -- no path at all, or a
  line that is not a positive integer -- is logged and skipped, not raised on.
  Two things do propagate, and neither is an outcome: a genuine I/O fault, and
  a feature path outside the grammar above.  Both become the command's
  writer-failure exit class, which the AAP 0.4.1 exit table already specifies
  as *"a writer fails after earlier writers succeeded"* -- non-zero, earlier
  artifacts retained, the failing writer named on stderr -- because
  ``app/services/report_service.py`` wraps each writer in ``except
  Exception``.  A forged rerun location is not an acceptable alternative to
  that, and neither is silently dropping a real failure.
* Nothing is deleted: this writer opens one stream in place, and the only
  entries :mod:`app.utils.paths` ever removes are the dot-prefixed temporary
  and staging entries its own publication API created -- which an in-place
  write does not create.  Emptying the build output directory belongs to
  ``app/cli.py``'s ``--clean`` step, and the per-worker intermediate directory
  beneath it to ``app/services/test_run_service.py``.
"""

from __future__ import annotations

import errno
import logging
import os
import stat
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path
from typing import Final, NamedTuple

from app.reporting.aggregation import (
    element_units,
    is_background,
    is_dry_run,
    is_failure_token,
    mappings,
    unit_status,
)
from app.reporting.events import (
    ELEMENT_TYPE_SCENARIO,
    JsonDict,
    ResultSet,
)
from app.utils.paths import (
    FEATURES_DIR_NAME,
    FILE_URI_SCHEME,
    NORMALIZED_FEATURES_PREFIX,
    ArtifactPathError,
    features_dir,
    normalize_feature_uri,
    open_artifact_read,
    open_artifact_write,
    rerun_txt_path,
)

__all__ = [
    "COMMENT_PREFIX",
    "FAILED_STATUS",
    "FAILURE_STATUSES",
    "FEATURE_SUFFIX",
    "FORBIDDEN_PATH_CHARACTERS",
    "LINE_ENDING",
    "LINE_SEPARATOR",
    "MAX_FEATURE_BYTES",
    "MAX_FEATURE_PATH_CHARACTERS",
    "MAX_LINE_NUMBER_DIGITS",
    "MAX_MANIFEST_BYTES",
    "MAX_MANIFEST_ENTRIES",
    "MAX_MANIFEST_LINES",
    "PATH_SEPARATOR",
    "RerunEntry",
    "RerunManifestError",
    "STATUS_SPELLINGS",
    "VerifiedFeature",
    "build_rerun_lines",
    "build_rerun_text",
    "is_failure_status",
    "iter_failed_scenarios",
    "list_verified_features",
    "normalize_status",
    "parse_rerun_file",
    "parse_rerun_lines",
    "parse_rerun_text",
    "read_verified_feature",
    "rerun_locations",
    "resolve_feature_path",
    "validate_feature_path",
    "verify_feature_identity",
    "write_rerun_txt",
]

logger = logging.getLogger(__name__)

#: The canonical failure status.  behave's own status name, and the JVM's;
#: :mod:`app.reporting.events` normalises to it and
#: ``app/reporting/cucumber_json.py`` emits it.  It is a *member* of
#: :data:`FAILURE_STATUSES` rather than the whole of it -- see that set for
#: why the manifest's vocabulary is wider.
FAILED_STATUS: Final[str] = "failed"

#: Every recorded status that puts a scenario in the manifest.  The literal
#: ``failed`` alone is *not* the rule: the manifest and ``cucumber.json`` are
#: read as a matched pair, so a scenario the JSON reports as failed must be
#: re-selectable, and the shared status table folds a wider behave vocabulary
#: onto ``failed``.
#:
#: **This set states the membership; it does not decide it.**
#: :func:`is_failure_status` asks
#: :func:`app.reporting.aggregation.is_failure_token`, so the manifest and the
#: report surfaces grade one status once.  The two are equivalent by
#: construction -- every name below canonicalises to a member of
#: :data:`~app.reporting.aggregation.FAILURE_TOKENS` and every name absent from
#: it does not -- and ``tests/test_rerun_report.py`` asserts that equivalence
#: over the whole vocabulary rather than leaving it to inspection.
#:
#: Each member's provenance, all of it measured:
#:
#: * ``failed``, ``error``, ``hook_error``, ``cleanup_error`` -- behave 1.3.3's
#:   ``Status.has_failed()`` is ``is_failure() or is_error() or
#:   is_hook_error()``, and ``cucumber_json.STATUS_ALIASES`` folds the last
#:   three onto ``failed``.  ``error`` is an *exception* in a step (a browser
#:   that died mid-scenario, say) as against a failed assertion, and the two
#:   hook spellings are the same distinction for hook code.
#: * ``undefined``, ``pending`` -- behave's ``is_error()`` covers both, and
#:   Cucumber-JVM 7.2.3's ``RerunFormatter.handleTestCaseFinished`` records a
#:   test case whenever ``Status.isOk()`` is false, where ``isOk()`` is
#:   ``PASSED || SKIPPED`` only.  Both are therefore in the JVM's manifest.
#: * ``ambiguous`` -- ``isOk()`` false for the same reason, and a member of
#:   ``cucumber_json.CUCUMBER_STATUSES``.
#: * ``xfailed`` -- ``cucumber_json.STATUS_ALIASES`` folds it onto ``failed``,
#:   so including it is what keeps ``rerun.txt`` and ``cucumber.json``
#:   agreeing about the same scenario.
#:
#: Deliberately excluded, and each for a reason:
#:
#: * ``passed``, and ``xpassed`` which the JSON writer folds onto it.
#: * ``skipped`` -- the JVM's ``isOk()`` is **true** for SKIPPED, so the steps
#:   behave skips *after* a failure never put a scenario in the manifest on
#:   their own account; the failing step already did.
#: * ``untested``, ``unknown``, ``executing`` -- no outcome was established.
#:   Under ``--dry-run`` behave marks every step ``untested``, so treating
#:   these as failures would name every scenario in the manifest.  An
#:   unmatched dry-run step is ``untested_undefined``, which
#:   :func:`normalize_status` folds to ``undefined`` and which therefore *is*
#:   included -- matching the JVM's own dry-run behaviour.
FAILURE_STATUSES: Final[frozenset[str]] = frozenset(
    {
        FAILED_STATUS,
        "error",
        "hook_error",
        "cleanup_error",
        "xfailed",
        "undefined",
        "pending",
        "ambiguous",
    }
)

#: behave's redundant status spellings, folded onto the name its own
#: ``Status.normalized_name`` reports.  :mod:`app.reporting.events` records
#: that normalised name already, so these three are reached only from a
#: hand-built document or one merged from a foreign writer -- which is exactly
#: the input the parser and the writer have to survive.  The mapping is
#: deliberately the same three entries ``cucumber_json.STATUS_ALIASES`` folds
#: for the same reason, and no more: this module normalises spelling, never
#: outcome.
STATUS_SPELLINGS: Final[dict[str, str]] = {
    "untested_undefined": "undefined",
    "untested_pending": "pending",
    "pending_warn": "pending",
}

#: Separator between the path and each line number, and between line numbers.
#: It is also the character inside :data:`~app.utils.paths.FILE_URI_SCHEME`,
#: which is the parsing hazard :func:`parse_rerun_lines` is built around.
LINE_SEPARATOR: Final[str] = ":"

#: Line terminator.  LF unconditionally, on every platform -- the manifest is
#: read back by a runner, not by a text editor.
LINE_ENDING: Final[str] = "\n"

#: Comment marker.  This writer never emits one, and the parser recognises it
#: **in order to reject it**: behave's native rerun formatter opens its file
#: with ``# -- RERUN: %d failing scenarios during last test run.``, and a file
#: carrying that header holds behave's ungrouped, unprefixed grammar rather
#: than this one.  Reading it would silently select the wrong scenarios, so
#: :func:`_parse_manifest_line` raises on a comment line instead of skipping
#: it.
COMMENT_PREFIX: Final[str] = "#"

#: Separator between the components of an accepted feature path.  Taken from
#: :data:`~app.utils.paths.NORMALIZED_FEATURES_PREFIX` -- which
#: :mod:`app.utils.paths` defines as the features directory name followed by
#: the POSIX separator -- rather than written out, because that module owns
#: every path location in this port and this module holds no path literal.
PATH_SEPARATOR: Final[str] = NORMALIZED_FEATURES_PREFIX[
    len(FEATURES_DIR_NAME) :
]

#: Extension every Gherkin file in this repository carries.  A grammar token,
#: not a location: :mod:`app.utils.paths` owns *where* the features live, and
#: ``app/services/test_run_service.py`` declares the same token locally for
#: the same reason when it discovers feature files.
FEATURE_SUFFIX: Final[str] = ".feature"

#: Characters an accepted feature path may never contain, at any position.
#: Each one is a concrete attack or corruption on a **line-oriented artifact
#: that a second runner executes**:
#:
#: * LF and CR would end the line early, so one worker-supplied path could
#:   emit two valid manifest entries and direct a rerun at a feature that
#:   never failed (CWE-93/CWE-117).
#: * NUL and the other C0 controls, and DEL, cannot occur in a feature path
#:   this port writes; they reach the filesystem layer as a raw
#:   :class:`ValueError` rather than a typed manifest error.
#: * :data:`LINE_SEPARATOR` -- a colon in the path is indistinguishable from
#:   the line-number delimiter, which is how ``features/A.feature:99``
#:   fabricated the entry ``features/A.feature:99:1``.  Rejecting it also
#:   refuses a path that still carries the ``file:`` scheme.
#: * The backslash, which is a path separator on the Windows half of the
#:   ``isUnix()`` branch and would otherwise smuggle ``C:\\win\\evil.feature``
#:   past a POSIX-only component check.
#: * NEL, LINE SEPARATOR and PARAGRAPH SEPARATOR -- the three code points
#:   outside the C0 range that :meth:`str.splitlines` also treats as line
#:   boundaries.  :func:`parse_rerun_text` splits with that method, so a path
#:   carrying one of them would be read back as two entries exactly as LF
#:   would; they are rejected for the same reason and not for tidiness.
FORBIDDEN_PATH_CHARACTERS: Final[frozenset[str]] = frozenset(
    {LINE_SEPARATOR, "\\", "\x7f", "\x85", "\u2028", "\u2029"}
    | {chr(code) for code in range(0x20)}
)

#: Digits accepted in one line-number segment.  A Gherkin line number is
#: orders of magnitude below ``10**9``, and the bound is what keeps
#: :func:`int` away from CPython's 4300-digit integer-conversion limit, which
#: raises a bare :class:`ValueError` -- an untyped escape from the
#: :class:`RerunManifestError` contract the AAP 0.4.1 exit table depends on.
MAX_LINE_NUMBER_DIGITS: Final[int] = 9

#: Characters accepted in one feature path, at most.  A manifest is machine
#: input written by an earlier run, and a *tampered* one can spell an entry
#: whose path passes every grammar test above and is still megabytes long --
#: measured on this parser before the bound existed, a 1 MiB path was accepted
#: and then interpolated into a log record.  The bound is not arbitrary: POSIX
#: ``NAME_MAX`` is 255 bytes and Windows' ``MAX_PATH`` component limit is the
#: same order, so a longer entry cannot name a real file on any platform this
#: port runs on (AAP 0.8 lists Windows, Linux and macOS).  ``features/`` plus
#: 255 is 264; the headroom to 512 covers a multi-byte name whose character
#: count exceeds its byte count without admitting anything that could name a
#: file.  With the bound in place every accepted path -- and therefore every
#: diagnostic that names one -- is bounded by construction (CWE-400/CWE-532).
MAX_FEATURE_PATH_CHARACTERS: Final[int] = 512

#: Bytes read from a manifest, at most, before the read is refused.  The
#: largest manifest this suite can produce is ten lines -- one per feature,
#: each a path of at most 32 characters plus one ``:<line>`` per failing
#: scenario -- so a real file is measured in hundreds of bytes and this bound
#: is two orders of magnitude above it.  What it stops is the file that is not
#: a real one: :func:`parse_rerun_file` reads at most ``MAX_MANIFEST_BYTES + 1``
#: bytes and refuses anything longer, so neither a multi-gigabyte regular file
#: nor an endless stream can be pulled into memory before the grammar is ever
#: consulted (CWE-400).
MAX_MANIFEST_BYTES: Final[int] = 64 * 1024

#: Lines parsed from a manifest, at most.  The byte bound alone would still
#: admit roughly two thousand minimal entries, and each one costs a validation
#: pass and an entry object; this states the work the parser will do for a
#: tampered file as a number rather than leaving it to follow from the byte
#: bound.  Ten is the real figure for this suite.
MAX_MANIFEST_LINES: Final[int] = 2 * 1024

#: Distinct features a manifest may name, at most.  One entry per feature file,
#: and :func:`validate_feature_path` confines every entry to one flat
#: directory, so this bounds the *fan-out* of a rerun: the suite has ten
#: feature files (AAP 0.4.1), and a manifest naming more than 256 distinct ones
#: describes a tree this repository does not have.
MAX_MANIFEST_ENTRIES: Final[int] = 256

#: Bytes read from one feature file, at most.  The ten features in this
#: repository total 17,978 bytes, the largest being a few kilobytes, so a
#: megabyte is far above anything the suite contains while still bounding what
#: a planted file can cost the selection pass that reads it
#: (:func:`read_verified_feature`).
MAX_FEATURE_BYTES: Final[int] = 1024 * 1024

#: The fixed vocabulary a diagnostic uses to say *which* forbidden character an
#: entry carried, so that the character itself never reaches a log record.  A
#: manifest is untrusted input and a diagnostic about it is written to stderr,
#: which Jenkins records verbatim: echoing the offending bytes is how a
#: tampered file forges a console line or discloses its payload (CWE-117,
#: CWE-532).  Naming the *class* of character keeps the diagnostic useful --
#: an operator can tell a stray colon from a smuggled newline -- and carries no
#: attacker-controlled data at all.  Every member of
#: :data:`FORBIDDEN_PATH_CHARACTERS` resolves through this table or through the
#: default in :func:`_forbidden_character_label`.
_FORBIDDEN_CHARACTER_LABELS: Final[dict[str, str]] = {
    LINE_SEPARATOR: "the line-number delimiter",
    "\\": "a backslash, the Windows path separator",
    "\n": "a line feed",
    "\r": "a carriage return",
    "\x00": "a NUL byte",
    "\x7f": "the DEL control character",
    "\x85": "the NEL line terminator",
    "\u2028": "the LINE SEPARATOR terminator",
    "\u2029": "the PARAGRAPH SEPARATOR terminator",
}


def _printable(text: str) -> str:
    """Spell every control character in ``text``, leaving the rest alone.

    For the one diagnostic that has to name a *location* rather than a reason:
    the manifest path a caller supplied, which the caller chose and this module
    therefore reports, and which can still carry a character that would end a
    log record early or drive a terminal (CWE-117).  Reasons need no such
    helper -- they are drawn from fixed vocabularies and carry no input at all.

    Args:
        text: The location to render.

    Returns:
        The same text with every C0 control, DEL, C1 control and the three
        non-C0 line terminators replaced by their ``\\xNN``-style spelling, so
        the result is exactly one printable physical line.

    Examples:
        >>> _printable("features/A.feature\\x00")
        'features/A.feature\\\\x00'
    """
    return "".join(
        character
        if not (
            character in FORBIDDEN_PATH_CHARACTERS
            and (ord(character) < 0x20 or ord(character) >= 0x7F)
        )
        else f"\\x{ord(character):02x}"
        if ord(character) <= 0xFF
        else f"\\u{ord(character):04x}"
        for character in text
    )


def _forbidden_character_label(character: str) -> str:
    """Name a forbidden character without reproducing it.

    Args:
        character: The character an entry carried, from
            :data:`FORBIDDEN_PATH_CHARACTERS`.

    Returns:
        A fixed phrase from :data:`_FORBIDDEN_CHARACTER_LABELS`, or -- for the
        remaining C0 controls, which have no individual name worth carrying --
        the generic phrase.  The return value is one of a closed set of
        literals, so no caller can leak input by interpolating it.
    """
    return _FORBIDDEN_CHARACTER_LABELS.get(character, "a control character")


class RerunManifestError(RuntimeError):
    """Raised when a rerun manifest is absent, unreadable or malformed.

    Typed on purpose, and mirroring :class:`app.reporting.events.ResultSetError`
    so that ``app/reporting`` presents one error-reporting idiom.  Per the AAP
    0.4.1 exit table a *"missing or malformed rerun manifest"* still exits
    ``0`` with the problem reported on stderr, so ``app/cli.py`` needs to
    recognise exactly this condition without having to tell an :class:`OSError`
    from a :class:`ValueError`, and without a traceback reaching the operator.

    Nothing in this module calls :func:`sys.exit`; the decision belongs to the
    caller.  The originating exception, where there is one, is always chained,
    so the cause survives for a log.
    """


class RerunEntry(NamedTuple):
    """One manifest line: a feature path and its failing scenario lines.

    Attributes:
        path: The feature's repository-relative path, without the ``file:``
            scheme -- the form that ``app/cli.py`` can hand to behave.  It has
            passed :func:`validate_feature_path`, so it is always
            ``features/<name>.feature``: normalised through
            :func:`~app.utils.paths.normalize_feature_uri` and confined to the
            feature tree, whichever direction of the round trip produced it.
        lines: The failing scenarios' line numbers, ascending and deduplicated.
            Never empty: a feature with no failures contributes no entry.
    """

    path: str
    lines: tuple[int, ...]

    @property
    def locations(self) -> tuple[str, ...]:
        """Return this entry's ``path:line`` locations, one per scenario.

        This is the form a runner selects by, which is why the entry owns it:
        ``--rerun`` passes these strings straight through, so the ungrouped
        shape is derived from the grouped file rather than stored twice.

        Returns:
            One ``"<path>:<line>"`` string per line number, in ascending order.
        """
        return tuple(f"{self.path}{LINE_SEPARATOR}{line}" for line in self.lines)


def _error_context(source: str | None, number: int | None) -> str:
    """Build the ``source: line N:`` prefix a manifest error message carries.

    The message is what ``app/cli.py`` prints on stderr before exiting ``0``
    (AAP 0.4.1), so it has to say *where* the offending entry came from.  Both
    parts are optional because the same validator serves the parser, which
    knows the file and the position, and the writer, which knows neither.

    Args:
        source: Where the entry came from -- a filename, normally.
        number: The entry's one-based position in that source.

    Returns:
        The prefix, ending in ``": "`` when either part is known and ``""``
        when neither is, so a message reads correctly in every combination.
    """
    if source is not None and number is not None:
        return f"{source}: line {number}: "
    if source is not None:
        return f"{source}: "
    if number is not None:
        return f"line {number}: "
    return ""


def validate_feature_path(
    path: object,
    *,
    source: str | None = None,
    number: int | None = None,
) -> str:
    """Validate one feature path against the manifest's grammar.

    An accepted manifest entry is exactly
    :data:`~app.utils.paths.FILE_URI_SCHEME` followed by
    ``features/<name>.feature`` followed by one or more ``:<line>``.  This
    function owns the middle part -- the path -- and is the **lexical tier** of
    the confinement:

    * A path that survives it is, by construction, one Gherkin file directly
      inside the features directory this port owns.  There is no form it can
      take that names anything else, which is what makes it safe for
      ``app/services/test_run_service.py`` to append the resulting
      ``path:line`` location to the behave argv (CWE-22).
    * The features directory in this repository is **flat** -- exactly ten
      ``*.feature`` files, their filenames preserved from the Java layout by
      AAP deviation 1 -- so a nested path is not merely unusual but
      unresolvable: the filesystem tier opens an entry by its **final
      component**, relative to the verified features root, and a directory
      part could therefore never take part in what is opened.  Accepting one
      would mean an entry naming one file while a different file executed.
    * :func:`read_verified_feature` is the **filesystem tier** on top of it,
      for the caller that is about to read or execute a location, and
      :func:`resolve_feature_path` is the same tier answering the narrower
      question of whether the entry is executable at all.

    The legacy prefix is normalised **first**, through
    :func:`~app.utils.paths.normalize_feature_uri`, which is the single owner
    of that substitution.  That is load-bearing rather than cosmetic: the
    committed baseline ``tests/fixtures/golden_rerun.txt`` is stored verbatim
    with its ``src/main/resources/features/`` prefix, and a worker document
    left behind by the Java layout carries the same, so both have to be
    accepted -- and both become the port's ``features/`` form on the way
    through.

    Args:
        path: The candidate path, typed :class:`object` because checking that
            it *is* text is part of the validation.
        source: Where the path came from, for the error message.
        number: Its one-based position there, for the error message.

    Returns:
        The validated path, without the ``file:`` scheme, which the caller
        adds on the writer side and strips on the parser side.

    Raises:
        RerunManifestError: If the path is not text, is empty, is longer than
            :data:`MAX_FEATURE_PATH_CHARACTERS`, carries a forbidden character
            (see :data:`FORBIDDEN_PATH_CHARACTERS`), is absolute or a UNC path,
            contains an empty, ``.``, ``..``, hidden or whitespace-padded
            component, or is not exactly one ``*.feature`` file directly under
            the features directory.  Only this type is raised -- never
            :class:`ValueError`, never :class:`OSError` -- because the AAP
            0.4.1 exit table has a *"missing or malformed rerun manifest"* exit
            ``0`` with the problem reported on stderr, and the caller has to
            recognise that condition rather than classify an exception.

            **The message never reproduces the rejected value.**  It carries
            the source, the entry's position and a reason drawn from a fixed
            vocabulary, and nothing else: a manifest is untrusted machine
            input, the message is written to stderr and recorded verbatim by
            Jenkins, and echoing the value is how a tampered entry forges a
            console record or discloses its payload (CWE-117, CWE-532).  An
            operator locates the offending entry by its line number, which is
            what :func:`_error_context` is for.

    Examples:
        >>> validate_feature_path("features/Crm.feature")
        'features/Crm.feature'
        >>> validate_feature_path("src/main/resources/features/Crm.feature")
        'features/Crm.feature'
    """
    context = _error_context(source, number)
    if not isinstance(path, str):
        raise RerunManifestError(
            f"{context}a feature path must be text, not {type(path).__name__}"
        )
    if len(path) > MAX_FEATURE_PATH_CHARACTERS:
        # Checked before anything walks the string, so a megabyte-long entry
        # costs one comparison rather than a full character scan.  The test is
        # on the raw value: normalisation below only ever replaces the longer
        # legacy prefix with the shorter port one, so the normalised form is
        # never longer than what is measured here.
        raise RerunManifestError(
            f"{context}the feature path is longer than the "
            f"{MAX_FEATURE_PATH_CHARACTERS}-character bound, so it cannot "
            "name a file this port could execute"
        )
    if not path.strip():
        raise RerunManifestError(f"{context}the entry names no feature path")

    candidate = normalize_feature_uri(path)

    for character in candidate:
        if character in FORBIDDEN_PATH_CHARACTERS:
            raise RerunManifestError(
                f"{context}the feature path carries "
                f"{_forbidden_character_label(character)}, which a manifest "
                "entry may never contain"
            )
    if candidate.startswith(PATH_SEPARATOR):
        # Catches the POSIX absolute form and the UNC form `//host/share`
        # alike.  The Windows drive forms need no test of their own: `C:/...`
        # is refused by the colon in FORBIDDEN_PATH_CHARACTERS and `C:\...`
        # by both that colon and the backslash.
        raise RerunManifestError(
            f"{context}the feature path is absolute; a manifest entry is "
            "relative to the repository root"
        )

    components = candidate.split(PATH_SEPARATOR)
    for component in components:
        if not component:
            raise RerunManifestError(
                f"{context}the feature path has an empty component -- a "
                "doubled separator"
            )
        if component in (os.curdir, os.pardir):
            raise RerunManifestError(
                f"{context}the feature path traverses with a relative "
                f"{os.curdir!r} or {os.pardir!r} component"
            )
        if component.startswith(os.curdir):
            # Refuses every hidden entry, `target/.workers/` included, whose
            # intermediate documents must never be reachable (AAP 0.4.2).
            raise RerunManifestError(
                f"{context}the feature path names a hidden component -- one "
                f"beginning with {os.curdir!r}"
            )
        if component != component.strip():
            raise RerunManifestError(
                f"{context}a component of the feature path is padded with "
                "whitespace"
            )

    required = f"{NORMALIZED_FEATURES_PREFIX}<name>{FEATURE_SUFFIX}"
    if len(components) != 2 or components[0] != FEATURES_DIR_NAME:
        raise RerunManifestError(
            f"{context}the feature path must be {required}, one Gherkin file "
            f"directly under the {FEATURES_DIR_NAME} directory"
        )
    name = components[1]
    if not name.endswith(FEATURE_SUFFIX) or len(name) == len(FEATURE_SUFFIX):
        raise RerunManifestError(
            f"{context}the feature path must be {required} with a non-empty "
            "file name"
        )
    return candidate


# --------------------------------------------------------------------------- #
# The verified feature file -- the filesystem tier, and why it hands back an
# object rather than a name.
#
# A feature path arrives from a manifest an earlier run wrote, or from a
# directory listing, and both are *untrusted*: the manifest is machine input
# (``FailedTestRunner.java:11``) and the features directory is an ordinary
# checkout directory anything with local write access can alter.  The tier
# that used to sit here answered the question by *pathname* -- it resolved the
# features directory, stat'ed the entry, and returned a path -- and that has
# two defects a pathname check cannot fix:
#
# * ``Path.resolve()`` on the features directory **follows a link in its
#   place**, so a linked features root became the trust anchor and every entry
#   under it was judged inside "the" features directory.
# * the object it checked was discarded and only its name survived, so the
#   consumer opened the name again.  Between the two opens the entry can be
#   replaced, and the caller then parses -- and hands the engine -- a file
#   nothing ever approved (CWE-22, CWE-367).
#
# So this tier opens what it checks and hands back **what it opened**: the
# features root is opened ``O_NOFOLLOW`` relative to its own parent, so a link
# in its place is refused rather than followed; the entry is opened
# ``O_NOFOLLOW`` *relative to that root descriptor*, so no component can be
# re-resolved; the open descriptor is then ``fstat``-verified to hold a regular
# file with a single link; and the file's **contents are read from that same
# descriptor**.  What the caller receives is the content it will act on plus
# the object identity it came from, so every decision the port makes about a
# feature is made about the verified object and not about a name.
#
# The one thing this cannot do is hand the engine a descriptor: behave opens
# ``features/<name>.feature`` by name in the worker, and the AAP fixes that
# spelling -- deviation 1 and AAP 0.4.2 pin the feature URI carried by the
# JSON report and by this manifest to ``features/<name>``, so executing from a
# snapshot under another path would break both pinned contracts.  :func:`verify_feature_identity` is what the caller uses to
# re-establish the identity immediately before that hand-off, which is where
# ``app/services/test_run_service.py`` calls it.
# --------------------------------------------------------------------------- #

#: ``O_NOFOLLOW`` where the platform has it.  Absent on Windows, which is why
#: :data:`_DESCRIPTOR_RELATIVE` exists and why the fallback below is written.
_O_NOFOLLOW: Final[int] = getattr(os, "O_NOFOLLOW", 0)

#: ``O_DIRECTORY`` where the platform has it, for the features-root open.
_O_DIRECTORY: Final[int] = getattr(os, "O_DIRECTORY", 0)

#: ``O_NONBLOCK`` where the platform has it.  Load-bearing rather than an
#: optimisation: opening a FIFO without it blocks until a writer appears, so a
#: named pipe planted in the features directory would hang the selection pass
#: before any check could refuse it (CWE-400).  With it the open returns and
#: the ``fstat`` below refuses the entry as non-regular.
_O_NONBLOCK: Final[int] = getattr(os, "O_NONBLOCK", 0)

#: Whether descriptor-relative opening is available -- both ``O_NOFOLLOW`` and
#: ``dir_fd`` support on :func:`os.open`.  True on Linux and macOS, false on
#: Windows, which has neither; the fallback path is verified by identity
#: instead, exactly as ``app/utils/paths.py`` does for the same reason.
_DESCRIPTOR_RELATIVE: Final[bool] = bool(_O_NOFOLLOW) and (
    os.open in getattr(os, "supports_dir_fd", frozenset())
)


class VerifiedFeature(NamedTuple):
    """One feature file, opened and checked, with the content that was read.

    Attributes:
        path: The feature's repository-relative path, ``features/<name>``,
            as :func:`validate_feature_path` accepted it.  This is the
            spelling every artifact carries and the spelling the engine is
            given, so a caller never has to rebuild it.
        location: The path that was opened, for a diagnostic.  Deliberately
            **not** the thing callers act on: a path is a name, and reopening
            it is the defect this type exists to remove.
        identity: The object identity of the descriptor that was read, as
            :func:`_identity_of` composes it -- device, inode, size and the
            modification and change timestamps.  A later
            :func:`verify_feature_identity` compares against this, which is
            how a swap between selection and execution is detected, including
            the swap that recycles the inode number.
        text: The file's decoded contents, read from the verified descriptor
            and bounded by :data:`MAX_FEATURE_BYTES`.  Parsing this is what
            makes a selection a fact about the checked object rather than
            about whatever the name resolved to a moment later.
    """

    path: str
    location: Path
    identity: tuple[int, ...]
    text: str


class _FeatureRefused(Exception):
    """Internal: one refusal, carrying a reason fit to log as it stands.

    Never raised out of this module.  The public functions catch it, log the
    reason at ``WARNING`` -- which ``app/logging_config.py`` routes to stderr,
    the tolerated-manifest row of the AAP 0.4.1 exit table -- and return
    ``None`` or ``False``.  The reason is written data-free at the point it is
    constructed, for the same rule :func:`validate_feature_path` documents.
    """


def _identity_of(info: os.stat_result) -> tuple[int, ...]:
    """Return the object identity a :class:`os.stat_result` reports.

    ``(st_dev, st_ino)`` is the obvious identity and **it is not sufficient**,
    which was established by measurement rather than assumed: a file unlinked
    and immediately recreated in the same directory was handed the same inode
    number by the filesystem, so a swap of exactly that shape compared equal
    to the object that had been verified.  The size and the two timestamps are
    therefore part of the identity -- a recreated file has a new
    ``st_ctime_ns`` whether or not its inode number was recycled, and on
    Windows, where ``st_ctime_ns`` is the creation time, the same holds.

    Reading a file changes none of these five: neither :func:`os.fstat` nor a
    read alters ``st_mtime_ns`` or ``st_ctime_ns``, and access time is
    deliberately excluded because it does.

    Args:
        info: The result of an :func:`os.fstat` or :func:`os.lstat`.

    Returns:
        ``(st_dev, st_ino, st_size, st_mtime_ns, st_ctime_ns)``, coerced to
        plain integers so the tuple compares equal across the two calls that
        produce it.
    """
    return (
        int(info.st_dev),
        int(info.st_ino),
        int(info.st_size),
        int(info.st_mtime_ns),
        int(info.st_ctime_ns),
    )


def _refused_by_os(error: OSError, subject: str) -> _FeatureRefused:
    """Turn a failed filesystem call into a data-free refusal.

    Args:
        error: The failure.
        subject: What was being opened, in a fixed phrase.

    Returns:
        The refusal.  It names the ``errno`` **symbol** rather than
        interpolating the exception, whose text carries the filename the call
        was made with and, on some platforms, locale-dependent message text
        (CWE-532).
    """
    code = errno.errorcode.get(error.errno or 0, "unknown")
    return _FeatureRefused(f"{subject} could not be opened ({code})")


def _open_features_root(directory: Path) -> int:
    """Open the features directory, refusing a symbolic link in its place.

    The trust anchor, and the correction of the defect that made this tier
    worth rewriting: the directory is opened ``O_NOFOLLOW`` **relative to its
    own parent**, so a link standing where ``features`` should be is refused
    rather than followed.  The parent is the operator's own layout -- the
    repository root, or a test's temporary directory -- and is followed
    normally, exactly as ``app/utils/paths.py`` treats its trusted anchor.

    Args:
        directory: :func:`~app.utils.paths.features_dir`'s answer.

    Returns:
        A read-only directory descriptor the caller must close.

    Raises:
        _FeatureRefused: If the entry is a link, is not a directory, or cannot
            be opened.
    """
    try:
        parent_fd = os.open(directory.parent, os.O_RDONLY | _O_DIRECTORY)
    except OSError as error:
        raise _refused_by_os(
            error, f"the parent of the {FEATURES_DIR_NAME} directory"
        ) from error
    try:
        root_fd = os.open(
            directory.name,
            os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW,
            dir_fd=parent_fd,
        )
    except OSError as error:
        if error.errno in (errno.ELOOP, errno.EMLINK, errno.ENOTDIR):
            raise _FeatureRefused(
                f"the {FEATURES_DIR_NAME} directory is a symbolic link or not "
                "a directory, and it is the anchor every feature path is "
                "resolved against"
            ) from error
        raise _refused_by_os(
            error, f"the {FEATURES_DIR_NAME} directory"
        ) from error
    finally:
        os.close(parent_fd)
    return root_fd


def _is_link_like(candidate: Path) -> bool:
    """Report whether an entry stands in for something else.

    ``O_NOFOLLOW`` answers this on the platforms that have it; this is for the
    platform that does not.  A Windows checkout is in AAP 0.8's support matrix,
    and there a **junction** redirects a directory exactly as a symbolic link
    does while :meth:`~pathlib.Path.is_symlink` reports ``False`` for it: only
    the symlink reparse tag satisfies that test, and a junction carries the
    mount-point tag instead.  A features root replaced by a junction would
    therefore have passed a symlink-only check and become the anchor every
    entry was resolved against, which is the defect this exists to close.  So
    every reparse point is refused, by three tests that cost nothing where
    they do not apply.

    Args:
        candidate: The entry to examine.

    Returns:
        ``True`` if it is a symbolic link, a junction, or any other reparse
        point; ``False`` if it is absent or an ordinary entry.

    Raises:
        OSError: If it cannot be examined for any reason other than absence.
    """
    if candidate.is_symlink():
        return True
    # isjunction() is present from Python 3.12 and returns False on POSIX; the
    # interpreter is pinned at 3.14 (AAP 0.3.1), so the attribute is there.
    if os.path.isjunction(candidate):
        return True
    try:
        info = os.lstat(candidate)
    except FileNotFoundError:
        return False
    # st_reparse_tag exists on Windows only, and a non-zero value is any
    # reparse point -- including tags neither test above recognises.
    return bool(getattr(info, "st_reparse_tag", 0))


def _refuse_linked_chain(directory: Path, location: Path) -> None:
    """Portable stand-in for the no-follow opens, where they are unavailable.

    Refuses the features directory and the entry if either is already a
    symbolic link.  On its own this would be a check-then-open race, and it is
    not left on its own: the caller opens the entry and then confirms, through
    :func:`_verify_opened_by_name`, that the descriptor holds the object the
    name reported -- so a link planted between this walk and that open is
    caught before anything is read.  The same discipline, and the same
    reasoning, as ``app/utils/paths.py``'s fallback.

    Args:
        directory: The features directory.
        location: The candidate feature file beneath it.

    Raises:
        _FeatureRefused: If either is a symbolic link, or cannot be examined.
    """
    for subject, candidate in (
        (f"the {FEATURES_DIR_NAME} directory", directory),
        ("the feature entry", location),
    ):
        try:
            linked = _is_link_like(candidate)
        except OSError as error:
            raise _refused_by_os(error, subject) from error
        if linked:
            raise _FeatureRefused(
                f"{subject} is a symbolic link, a junction or another reparse "
                "point, so it stands in for something else"
            )


def _verify_opened_by_name(location: Path, info: os.stat_result) -> None:
    """Confirm the descriptor holds the object the name reports.

    Args:
        location: The name that was opened.
        info: :func:`os.fstat` of the descriptor obtained for it.

    Raises:
        _FeatureRefused: If the name now reports a different object, or cannot
            be examined -- which is what a link planted during the open looks
            like from here.
    """
    try:
        named = os.lstat(location)
    except OSError as error:
        raise _refused_by_os(error, "the feature entry") from error
    if _identity_of(named) != _identity_of(info):
        raise _FeatureRefused(
            "the feature entry changed while it was being opened, so what was "
            "opened is not what the name reports"
        )


def _verify_feature_descriptor(fd: int) -> os.stat_result:
    """Establish what an open feature descriptor actually holds.

    Args:
        fd: The descriptor, already open.

    Returns:
        Its :func:`os.fstat`.

    Raises:
        _FeatureRefused: If it is not a regular file -- a directory, a FIFO, a
            device -- or is a regular file with more than one hard link, which
            means the entry *is* a file elsewhere as well, with no symbolic
            link anywhere for a link check to find.
    """
    try:
        info = os.fstat(fd)
    except OSError as error:
        raise _refused_by_os(error, "the feature entry") from error
    if not stat.S_ISREG(info.st_mode):
        raise _FeatureRefused(
            "the feature entry is not a regular file, so it is not Gherkin a "
            "runner could execute"
        )
    if info.st_nlink > 1:
        raise _FeatureRefused(
            "the feature entry has more than one hard link, so it is also a "
            "file outside the features directory"
        )
    return info


def _open_verified_feature(
    validated: str, directory: Path
) -> tuple[int, os.stat_result, Path]:
    """Open one validated feature path with every component verified.

    Args:
        validated: A path :func:`validate_feature_path` accepted, so exactly
            ``features/<name>.feature``.
        directory: The features directory the entry hangs off.

    Returns:
        The open descriptor -- the caller's to close -- its :func:`os.fstat`,
        and the path that was opened, for diagnostics only.

    Raises:
        _FeatureRefused: For every refusal the helpers above state.
    """
    # The lexical tier has already established that the entry is one component
    # below the features directory; taking `.name` is belt and braces, and is
    # what makes the descriptor-relative open below unambiguous.
    name = Path(validated).name
    location = directory / name
    flags = os.O_RDONLY | _O_NOFOLLOW | _O_NONBLOCK

    if _DESCRIPTOR_RELATIVE:
        root_fd = _open_features_root(directory)
        try:
            fd = os.open(name, flags, dir_fd=root_fd)
        except OSError as error:
            if error.errno in (errno.ELOOP, errno.EMLINK):
                raise _FeatureRefused(
                    "the feature entry is a symbolic link, and a location "
                    "handed to the engine has to be the file it was checked as"
                ) from error
            raise _refused_by_os(error, "the feature entry") from error
        finally:
            os.close(root_fd)
        return fd, _verified_or_closed(fd, location, by_name=False), location

    _refuse_linked_chain(directory, location)
    try:
        fd = os.open(location, flags)
    except OSError as error:
        raise _refused_by_os(error, "the feature entry") from error
    return fd, _verified_or_closed(fd, location, by_name=True), location


def _verified_or_closed(
    fd: int, location: Path, *, by_name: bool
) -> os.stat_result:
    """Verify an open feature descriptor, closing it if the verification fails.

    Ownership of the descriptor passes to the caller only when this returns.
    Written as its own function because the alternative -- verifying inline in
    the ``return`` expression -- leaks the descriptor on every refusal, and a
    refusal is the *expected* outcome for a planted entry: a directory named
    ``<something>.feature``, a FIFO, or a hard link is refused once per call,
    and a manifest may name many.  Measured before this existed, forty
    refusals left forty descriptors open, which exhausts the process limit and
    breaks the reads that follow it (CWE-772).  ``BaseException`` rather than
    :exc:`Exception`, because a cancellation arriving between the open and the
    check would leak the descriptor just as surely.

    Args:
        fd: The freshly opened descriptor.
        location: The path it was opened by, for the by-name check.
        by_name: Whether to confirm that the name still reports this object,
            which the portable branch needs and the descriptor-relative
            branch does not -- there the open was made relative to a verified
            directory descriptor with no name resolved a second time.

    Returns:
        The descriptor's :func:`os.fstat`.

    Raises:
        _FeatureRefused: As :func:`_verify_feature_descriptor` and
            :func:`_verify_opened_by_name`.  The descriptor is closed first.
        BaseException: Re-raised after closing the descriptor.
    """
    try:
        info = _verify_feature_descriptor(fd)
        if by_name:
            _verify_opened_by_name(location, info)
    except BaseException:
        os.close(fd)
        raise
    return info


def _read_verified_bytes(fd: int) -> bytes:
    """Read a verified feature descriptor, bounded.

    Args:
        fd: The verified descriptor.  Not closed here.

    Returns:
        The file's bytes.

    Raises:
        _FeatureRefused: If the file exceeds :data:`MAX_FEATURE_BYTES`, or the
            read fails.  One byte beyond the bound is requested so that the
            excess is detected without the whole file being taken in.
    """
    chunks: list[bytes] = []
    remaining = MAX_FEATURE_BYTES + 1
    try:
        while remaining > 0:
            chunk = os.read(fd, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    except OSError as error:
        raise _refused_by_os(error, "the feature entry") from error
    data = b"".join(chunks)
    if len(data) > MAX_FEATURE_BYTES:
        raise _FeatureRefused(
            f"the feature file is larger than the {MAX_FEATURE_BYTES}-byte "
            "bound, so it is not one of this suite's features"
        )
    return data


def _verified_open_or_none(
    validated: str, directory: Path
) -> tuple[int, os.stat_result, Path] | None:
    """Open a validated feature, reporting a refusal rather than raising it.

    The one place the three public entry points -- :func:`read_verified_feature`,
    :func:`verify_feature_identity` and :func:`resolve_feature_path` -- turn a
    refusal into the tolerated outcome the AAP 0.4.1 exit table describes, so
    all three refuse for the same reasons and report them the same way.

    Args:
        validated: A path :func:`validate_feature_path` accepted.
        directory: The features directory.

    Returns:
        The open descriptor -- the caller's to close -- its :func:`os.fstat`
        and the path that was opened, or ``None`` with the reason logged at
        ``WARNING``.
    """
    try:
        return _open_verified_feature(validated, directory)
    except _FeatureRefused as refusal:
        logger.warning("Refusing a feature under %r: %s", str(directory), refusal)
        return None
    except (OSError, ValueError) as error:
        # Nothing untyped escapes this module: a path carrying an embedded NUL
        # raises ValueError from the open, and neither that nor an I/O failure
        # is a reason to abandon a run whose failures are already recorded.
        code = errno.errorcode.get(getattr(error, "errno", 0) or 0, "unknown")
        logger.warning(
            "Refusing a feature under %r: it could not be opened (%s)",
            str(directory),
            code,
        )
        return None


def read_verified_feature(
    path: object,
    *,
    base: Path | str | None = None,
) -> VerifiedFeature | None:
    """Open, verify and read one feature file, or refuse it.

    The function every caller that is about to *act* on a feature's contents
    uses -- selection, outline expansion, tag evaluation -- because it returns
    the content that was read from the verified descriptor rather than a name
    to open again.  ``app/services/test_run_service.py`` parses
    :attr:`VerifiedFeature.text` with behave's own
    :func:`behave.parser.parse_feature`, which is exactly what behave's
    :func:`~behave.parser.parse_file` does after reading and decoding a file,
    so nothing about the engine's own view of the feature changes.

    A refusal is a **tolerated condition**, not an exception: per the AAP 0.4.1
    exit table a malformed manifest or an unusable feature still leaves the run
    at status ``0`` with the problem reported on stderr, which is where the
    module logger sends a ``WARNING``.

    Args:
        path: A feature path, validated or not.
        base: Directory the features directory hangs off; ``None`` means the
            process working directory, as everywhere in
            :mod:`app.utils.paths`.

    Returns:
        The :class:`VerifiedFeature`, or ``None`` when the entry is refused:
        an invalid or over-long path, a linked features root, a linked or
        hard-linked entry, a non-regular entry, an absent file, a file beyond
        :data:`MAX_FEATURE_BYTES`, contents that are not UTF-8, or a
        filesystem failure.
    """
    validated = _validated_or_none(path)
    if validated is None:
        return None

    opened = _verified_open_or_none(validated, features_dir(base))
    if opened is None:
        return None
    fd, info, location = opened
    try:
        data = _read_verified_bytes(fd)
        text = data.decode("utf-8")
    except _FeatureRefused as refusal:
        logger.warning(
            "Refusing a feature under %r: %s", str(features_dir(base)), refusal
        )
        return None
    except UnicodeDecodeError as error:
        logger.warning(
            "Refusing a feature under %r: it is not valid UTF-8 (at byte "
            "offset %d)",
            str(features_dir(base)),
            error.start,
        )
        return None
    finally:
        os.close(fd)
    return VerifiedFeature(
        path=validated,
        location=location,
        identity=_identity_of(info),
        text=text,
    )


def verify_feature_identity(
    path: object,
    identity: tuple[int, ...],
    *,
    base: Path | str | None = None,
) -> bool:
    """Confirm a feature path still names the object it was verified as.

    The hand-off check.  The engine runs in another process and opens
    ``features/<name>.feature`` **by name**, because the AAP pins that spelling
    into every artifact (deviation 1, AAP 0.4.2) and a snapshot under another
    path would break the ``uri`` and rerun contracts.  What is available
    instead is this: re-open the entry with the same verification and compare
    the object identity, so an entry replaced since selection is refused and
    named rather than silently executed.

    Args:
        path: The feature path, as :attr:`VerifiedFeature.path` carries it.
        identity: The identity recorded when the feature was read.
        base: Directory the features directory hangs off.

    Returns:
        ``True`` when the entry is still the same regular, single-linked file
        inside the verified features root; ``False`` for every refusal, each
        reported on the module logger.
    """
    validated = _validated_or_none(path)
    if validated is None:
        return False

    opened = _verified_open_or_none(validated, features_dir(base))
    if opened is None:
        return False
    fd, info, _ = opened
    os.close(fd)

    if _identity_of(info) != identity:
        logger.warning(
            "Refusing a feature under %r: it is no longer the file that was "
            "checked -- the entry was replaced after selection",
            str(features_dir(base)),
        )
        return False
    return True


def list_verified_features(
    base: Path | str | None = None,
) -> tuple[list[str], list[str]]:
    """List the suite's feature files through the verified features root.

    The enumeration half of the same rule: a directory listing is as
    re-resolvable as a manifest entry, so the directory is opened
    ``O_NOFOLLOW`` and every entry is examined **relative to that descriptor**
    with links refused, rather than by walking pathnames that a concurrent
    swap can redirect.  The order is the run's **canonical feature order** --
    ascending by repository-relative path -- which the sharding and the merge
    both rely on, so it is imposed here and not left to the filesystem.

    Args:
        base: Directory the features directory hangs off, or ``None`` for the
            process working directory.

    Returns:
        A ``(paths, problems)`` pair.  ``paths`` holds the accepted
        ``features/<name>.feature`` paths, sorted.  ``problems`` names a
        missing, unreadable or empty directory and every refused entry -- each
        a tolerated condition that yields fewer scenarios and status ``0``,
        never an exception.  A refused entry's *name* is reported with ``%r``
        so that a planted filename carrying a terminator cannot forge a record.
    """
    directory = features_dir(base)
    problems: list[str] = []
    accepted: list[str] = []

    try:
        if _DESCRIPTOR_RELATIVE:
            root_fd: int | None = _open_features_root(directory)
        else:
            # The portable branch cannot hold a directory descriptor -- opening
            # a directory is not permitted on Windows -- so the root is refused
            # lexically here and each entry is examined with `os.lstat` below.
            # Same stand-in, same reasoning, as `_refuse_linked_chain`.
            root_fd = None
            if _is_link_like(directory):
                raise _FeatureRefused(
                    f"the {FEATURES_DIR_NAME} directory is a symbolic link, a "
                    "junction or another reparse point, and it is the anchor "
                    "every feature path is resolved against"
                )
    except _FeatureRefused as refusal:
        return [], [f"{directory}: {refusal}"]
    except OSError as error:
        code = errno.errorcode.get(error.errno or 0, "unknown")
        return [], [f"{directory}: feature directory cannot be examined ({code})"]

    try:
        try:
            names = os.listdir(root_fd if root_fd is not None else directory)
        except OSError as error:
            code = errno.errorcode.get(error.errno or 0, "unknown")
            return [], [f"{directory}: feature directory cannot be listed ({code})"]

        for name in sorted(names):
            if not name.endswith(FEATURE_SUFFIX):
                continue
            try:
                if root_fd is not None:
                    info = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
                else:
                    info = os.lstat(directory / name)
            except OSError as error:
                code = errno.errorcode.get(error.errno or 0, "unknown")
                problems.append(
                    f"{directory}: the entry {name!r} cannot be examined ({code})"
                )
                continue
            if stat.S_ISLNK(info.st_mode) or (
                root_fd is None and _is_link_like(directory / name)
            ):
                # The second test is the portable branch's business: a Windows
                # junction carries the mount-point reparse tag rather than the
                # symlink one, so S_ISLNK alone would list it as an ordinary
                # entry.  See :func:`_is_link_like`.
                problems.append(
                    f"{directory}: the entry {name!r} is a link and is "
                    "refused; a feature has to be the file it was checked as"
                )
                continue
            if not stat.S_ISREG(info.st_mode):
                continue
            if info.st_nlink > 1:
                problems.append(
                    f"{directory}: the entry {name!r} has more than one hard "
                    "link, so it is also a file outside the features directory"
                )
                continue
            try:
                accepted.append(
                    validate_feature_path(f"{NORMALIZED_FEATURES_PREFIX}{name}")
                )
            except RerunManifestError as error:
                problems.append(f"{directory}: the entry {name!r} is refused ({error})")
    finally:
        if root_fd is not None:
            os.close(root_fd)

    if not accepted and not problems:
        problems.append(f"{directory}: no {FEATURE_SUFFIX} files found")
    return sorted(accepted), problems


def _validated_or_none(path: object) -> str | None:
    """Apply the lexical tier, reporting a refusal rather than raising it.

    Args:
        path: A feature path, validated or not.

    Returns:
        The validated path, or ``None`` with the reason logged -- the shape
        every filesystem-tier entry point needs, since a refusal there is
        tolerated at status ``0``.
    """
    try:
        return validate_feature_path(path)
    except RerunManifestError as error:
        logger.warning("Refusing a rerun location: %s", error)
        return None


def resolve_feature_path(
    path: object,
    *,
    base: Path | str | None = None,
) -> Path | None:
    """Answer whether a manifest path names a feature that may be executed.

    The **filesystem tier** of the confinement, and a thin front on
    :func:`_open_verified_feature`: the entry is opened ``O_NOFOLLOW`` beneath
    a features root that was itself opened ``O_NOFOLLOW``, and the descriptor
    is ``fstat``-verified to hold a regular file with a single hard link.  So
    a linked features root, a linked entry, a hard-linked entry, a directory,
    a FIFO, a device and an absent file are each refused -- and refused on the
    object, not on a name that could be re-resolved a moment later.

    :func:`parse_rerun_file` applies this on every production read, so no
    caller has to remember to; it is public because a caller holding a single
    location -- rather than a manifest -- needs the same answer.  The lexical
    tier, :func:`validate_feature_path`, which this applies first, has already
    confined every accepted entry to ``features/<name>.feature``.

    **A caller about to read or parse the feature wants**
    :func:`read_verified_feature` **instead.**  What this returns is a
    *pathname*, and a pathname is re-resolvable: opening it again is exactly
    the check-then-reopen sequence this tier was rewritten to remove, and the
    verified object it was checked as is not recoverable from it.  This
    function answers one question -- *is this entry executable?* -- which is
    all :func:`_confined_entries` needs to decide whether a manifest line is
    worth keeping.

    It is deliberately **not** part of :func:`parse_rerun_lines` or
    :func:`parse_rerun_text`, which stay pure -- no filesystem, no working
    directory -- which is what lets the round trip AAP 0.6 requires be checked
    in memory and keeps the AAP 0.5.1 coverage gate on ``app/reporting``
    reachable without a temporary feature tree.

    A violation is a tolerated condition, not an exception: per the AAP 0.4.1
    exit table a malformed manifest still exits ``0`` with the problem
    reported on stderr, and the module logger routes WARNING and above there.
    Every message names the validated path with ``%r`` and carries no other
    input, so neither a manifest entry nor an operating-system message can
    forge a log line (CWE-117, CWE-532).

    Args:
        path: A manifest path, validated or not.
        base: Directory the features directory hangs off; ``None`` means the
            process working directory, as in :mod:`app.utils.paths`.

    Returns:
        The path that was opened, when the entry is a regular, single-linked,
        non-symlinked file inside a non-symlinked
        :func:`~app.utils.paths.features_dir`; ``None`` for every other
        outcome -- an invalid or over-long path, a linked features root, a
        symbolic or hard link, a directory or other non-regular object, an
        absent file, or an open the operating system refused.
    """
    validated = _validated_or_none(path)
    if validated is None:
        return None

    opened = _verified_open_or_none(validated, features_dir(base))
    if opened is None:
        return None
    fd, _info, location = opened
    # The contents are not read here on purpose: this answers a question about
    # the entry, and the caller that needs the bytes asks for them -- and for
    # the identity they came from -- through read_verified_feature.
    os.close(fd)
    return location


def normalize_status(status: str | None) -> str:
    """Return a recorded status in the one spelling this module compares.

    **Spelling only, and deliberately so.**  Three operations and no more:
    surrounding whitespace is dropped, the name is lower-cased, and the
    redundant spellings in :data:`STATUS_SPELLINGS` are folded exactly as
    behave's own ``Status.normalized_name`` folds them.  The *outcome* is never
    changed -- ``error`` stays ``error`` -- because grading an outcome belongs
    to the shared model: :func:`is_failure_status` asks
    :func:`app.reporting.aggregation.is_failure_token`, and this function
    exists so that a caller comparing a recorded name against
    :data:`FAILURE_STATUSES` -- the set that *states* the manifest's
    vocabulary -- spells it the way that set does.

    Args:
        status: A status name as recorded in the internal result document, or
            ``None`` for a node that carries none.

    Returns:
        The normalised name, or ``""`` for ``None`` and for a blank name.
        ``""`` is never a member of :data:`FAILURE_STATUSES`, so an absent
        status cannot select a scenario.

    Examples:
        >>> normalize_status("untested_undefined")
        'undefined'
    """
    if not isinstance(status, str):
        return ""
    name = status.strip().lower()
    return STATUS_SPELLINGS.get(name, name)


def is_failure_status(status: str | None) -> bool:
    """Report whether a recorded status means the scenario has to be rerun.

    The predicate half of :data:`FAILURE_STATUSES`, and **one rule rather than
    a second copy of one**: the decision is
    :func:`app.reporting.aggregation.is_failure_token`, which canonicalises the
    recorded name through the shared status table and tests membership of
    :data:`~app.reporting.aggregation.FAILURE_TOKENS` -- the complement of
    Cucumber's ``Status.isOk()``.  :data:`FAILURE_STATUSES` states the same
    membership in recorded-name form, for documentation and for the surface
    ``app/cli.py`` can read, and
    ``tests/test_rerun_report.py`` asserts that the two agree across the whole
    vocabulary so neither can drift.

    Args:
        status: A status name, or ``None``.

    Returns:
        ``True`` when the status canonicalises to a failure token, which is
        exactly membership of :data:`FAILURE_STATUSES` after spelling
        normalisation.

    Examples:
        >>> is_failure_status("error")
        True
        >>> is_failure_status("skipped")
        False
        >>> is_failure_status(None)
        False
    """
    return is_failure_token(status)


def _feature_path(feature: JsonDict) -> str:
    """Return the repository-relative path a feature's manifest line names.

    ``path`` is read in preference to ``uri`` -- :mod:`app.reporting.events`
    carries both so that no consumer performs string surgery on the other --
    and the ``uri`` fallback serves a hand-built fixture, stripping the scheme
    through :data:`~app.utils.paths.FILE_URI_SCHEME` rather than a literal.

    A path that is present goes through :func:`validate_feature_path`, which
    is not tidiness: the internal document is assembled by worker processes
    and this artifact is line-oriented machine input, so a path carrying a
    line terminator would emit a **second, forged entry**.

    Args:
        feature: A feature object from the internal result set.

    Returns:
        The validated, normalised path, or ``""`` when the feature carries
        neither a ``path`` nor a ``uri``; the caller logs and skips that.

    Raises:
        RerunManifestError: If a path is present but outside the grammar --
            see :func:`validate_feature_path` for the cases and
            :func:`build_rerun_lines` for why the writer fails rather than
            emitting a shortened manifest.
    """
    candidate = feature.get("path")
    if not isinstance(candidate, str) or not candidate:
        uri = feature.get("uri")
        if not isinstance(uri, str) or not uri:
            return ""
        candidate = (
            uri[len(FILE_URI_SCHEME) :] if uri.startswith(FILE_URI_SCHEME) else uri
        )
    return validate_feature_path(candidate)


def _scenario_line(element: JsonDict) -> int:
    """Return the line number an element contributes to the manifest.

    Args:
        element: A scenario element.  For an outline row its ``line`` is the
            data row's line, which is what makes the entry re-selectable.

    Returns:
        The line number, or ``0`` when the element carries none that could be
        interpreted as a positive integer.  ``0`` is the caller's signal to
        skip: a location a runner cannot resolve is worse than a short
        manifest, and this is a structural defect in the result document rather
        than a test outcome, so it is never raised on.
    """
    raw = element.get("line")
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        return 0
    try:
        line = int(raw)
    except (TypeError, ValueError):
        return 0
    return line if line > 0 else 0


def iter_failed_scenarios(
    result_set: ResultSet,
) -> Iterator[tuple[JsonDict, JsonDict]]:
    """Yield every ``(feature, scenario element)`` pair that failed.

    The single owner of the rule stated in this module's docstring, and a pure
    function: it reads no clock, no working directory and no filesystem.  It
    cannot use :func:`app.reporting.events.iter_scenarios`, which skips
    Background occurrences, because a background step failure has to reach the
    scenario it precedes.

    Backgrounds are therefore paired here by
    :func:`app.reporting.aggregation.element_units` -- the shared grouping the
    JSON writer and the report surfaces use, so "which scenario does this
    occurrence belong to" has one answer: an occurrence belongs to the scenario
    immediately following it, and where two occurrences follow one another the
    earlier one is orphaned rather than carried forward.  The **decision** is
    equally shared: :func:`app.reporting.aggregation.unit_status` folds the
    unit's steps and both hook groups, and
    :func:`app.reporting.aggregation.is_failure_token` grades the fold.  The
    two are one predicate rather than two, because the failure tokens are a
    prefix of the severity order, so "any node of the unit failed" and "the
    unit's fold is a failure" cannot disagree.

    **Dry runs are read as the JVM reads them.**  The document's ``dry_run``
    flag decides each step's status from its own ``matched`` flag -- measured:
    under ``dryRun`` the JVM marks a matched step ``passed`` and an unmatched
    one ``undefined``, and its rerun formatter then selects the scenario
    holding the undefined step and no other.  behave records ``untested`` for
    both, so without this the manifest of a dry run was always empty and an
    unresolved step definition reached no rerun.

    Args:
        result_set: The merged internal result document.

    Yields:
        ``(feature, element)`` in document order -- features in source order,
        scenarios in the order they were collected -- so the manifest's own
        ordering falls out of iteration and needs no sort.  Non-selected
        scenarios, Background occurrences and anything that did not fail are
        skipped; a malformed feature or element is skipped rather than raised
        on.
    """
    dry_run = is_dry_run(result_set)
    for feature in mappings(result_set.get("features")):
        for unit in element_units(mappings(feature.get("elements"))):
            element = unit[-1]
            if is_background(element):
                # A trailing occurrence follows no scenario, so it has nothing
                # to fail: a Background is not addressable as a test case and
                # every occurrence shares the Background's own line.
                continue
            if element.get("type") != ELEMENT_TYPE_SCENARIO:
                # The collector records anything it cannot classify as a
                # scenario, so an unknown type is treated as one here too
                # rather than dropping a result on the floor.
                logger.debug(
                    "Treating element of type %r as a scenario", element.get("type")
                )
            # behave announces scenarios the tag expression excluded; the JVM
            # never starts them.  A key-absent fixture defaults to selected.
            if not element.get("selected", True):
                continue
            if is_failure_token(unit_status(unit, dry_run=dry_run)):
                yield feature, element


def _format_entry_line(path: str, lines: Sequence[int]) -> str:
    """Serialise one feature's entry as the manifest's single line.

    The **only** place this module concatenates a path into the artifact, so
    "one line per feature" is enforced at the point of emission rather than
    assumed by every caller.  :func:`validate_feature_path` has already
    refused every character that could break the line; this re-checks the
    finished string, because the invariant that matters is a property of the
    *output* and a defence that reads the output cannot be bypassed by a route
    into it that forgot to validate.

    Args:
        path: A path :func:`validate_feature_path` has accepted.
        lines: The failing scenarios' line numbers, ascending and
            deduplicated by the caller, which owns the ordering rule.

    Returns:
        ``file:<path>:<line>[:<line>...]``, carrying no line terminator.

    Raises:
        RerunManifestError: If the finished line would span more than one
            line.  Unreachable through :func:`build_rerun_lines`, and kept
            because the cost of the check is one comparison while the cost of
            the assumption is a forged rerun location (CWE-93).
    """
    entry = (
        FILE_URI_SCHEME
        + path
        + "".join(f"{LINE_SEPARATOR}{line}" for line in lines)
    )
    if entry.splitlines() != [entry]:
        raise RerunManifestError(
            f"the manifest entry for {path!r} spans more than one line, "
            f"which the format can never carry: {entry!r}"
        )
    return entry


def build_rerun_lines(result_set: ResultSet) -> list[str]:
    """Build the manifest's lines from a merged result set.

    The pure half of this writer -- no clock, no working directory, no
    filesystem -- so the format is testable without a browser (AAP 0.5.1).
    Grouping is by feature path in **first-appearance order**, so features
    come out in source order without a sort and two feature objects sharing a
    path contribute to one line; line numbers are deduplicated and ascending,
    so a doubly-reported scenario cannot produce ``:9:9``.

    A feature whose path cannot be determined, or a failing scenario whose
    line is not a positive integer, is logged at WARNING and omitted: a
    location a runner could not resolve is worse than a short manifest.

    Args:
        result_set: The merged internal result document.

    Returns:
        One ``file:<path>:<line>[:<line>...]`` string per feature with a
        failing scenario, no line terminator; empty when nothing failed.

    Raises:
        RerunManifestError: If a feature carries a path the grammar refuses;
            emitting it would forge a rerun location and dropping it would
            hide a real failure, so the writer fails -- the AAP 0.4.1 row.
    """
    grouped: dict[str, list[int]] = {}
    for feature, element in iter_failed_scenarios(result_set):
        try:
            path = _feature_path(feature)
        except RerunManifestError as error:
            logger.error(
                "Refusing to write a rerun entry for the feature at %r: %s",
                feature.get("path") or feature.get("uri"),
                error,
            )
            raise
        if not path:
            logger.warning(
                "Skipping a failed scenario on line %r: its feature carries "
                "neither a 'path' nor a 'uri'",
                element.get("line"),
            )
            continue
        line = _scenario_line(element)
        if not line:
            logger.warning(
                "Skipping a failed scenario of %s: its line %r is not a "
                "positive integer",
                path,
                element.get("line"),
            )
            continue
        grouped.setdefault(path, []).append(line)

    return [
        _format_entry_line(path, sorted(set(lines)))
        for path, lines in grouped.items()
    ]


def build_rerun_text(result_set: ResultSet) -> str:
    """Build the manifest's complete text, terminator included.

    Pure, like :func:`build_rerun_lines`, and the single place the file's byte
    shape is decided: every line is terminated with a single LF, so a one-line
    manifest ends with exactly one ``\\n`` and there is no blank final line.

    Args:
        result_set: The merged internal result document.

    Returns:
        The file's text.  ``""`` when nothing failed, which is what makes the
        no-failure artifact zero bytes rather than absent.

    Raises:
        RerunManifestError: As :func:`build_rerun_lines`, for a feature path
            outside the manifest's grammar.  Nothing is written by this
            function, so the caller decides what a refused document means.
    """
    lines = build_rerun_lines(result_set)
    if not lines:
        return ""
    return LINE_ENDING.join(lines) + LINE_ENDING


def write_rerun_txt(
    result_set: ResultSet,
    path: Path | str | None = None,
    base: Path | str | None = None,
) -> Path:
    """Write the rerun manifest and return the path written.

    A thin wrapper over :func:`build_rerun_text`: it decides where to write
    and how the bytes reach disk, and nothing else.  The file is always
    created -- the AAP 0.4.1 exit contract requires all four artifacts even
    for a run with no failures -- and is then zero bytes.  ``newline="\\n"``
    keeps LF on every platform and ``encoding="utf-8"`` pins the bytes.

    *How* those bytes reach disk is :func:`app.utils.paths.open_artifact_write`
    and nothing else: it creates and verifies every owned directory component
    under a *held* directory descriptor with ``O_NOFOLLOW``, opens the manifest
    relative to that descriptor, and truncates it only once the descriptor is
    known to hold a lone regular file -- so what is emptied and rewritten is
    the object that was verified.  Preparing the parent and then reopening the
    *pathname* is what this writer no longer does: in the window between the
    two, a symbolic or hard link put in the manifest's place would have
    redirected the write, or truncated a file outside the build-output
    directory, before any check could refuse it (CWE-367/CWE-59).  The manifest
    is created owner-only (:data:`app.utils.paths.ARTIFACT_FILE_MODE`), and one
    an earlier ``--no-clean`` run left group- or world-readable is tightened
    through that same descriptor before the new content exists, because it
    names the scenarios that failed (CWE-732/CWE-359).

    Args:
        result_set: The merged internal result document.
        path: Explicit destination; ``None`` -- the production case --
            resolves :func:`~app.utils.paths.rerun_txt_path`.
        base: Directory the default path hangs off; ignored with ``path``.

    Returns:
        The path written, so a caller can log or serve it.

    Raises:
        OSError: If the parent directory cannot be created or the file cannot
            be written.  A genuine I/O fault is the command's writer-failure
            exit class, whereas a test outcome never reaches this function as
            an exception.  :exc:`app.utils.paths.ArtifactPathError` -- a
            refused link, a destination that is not a lone regular file, a
            manifest that cannot be restricted to its owner -- is an
            :exc:`OSError` subclass, so it arrives through that same exit class
            with nothing written and the previous manifest intact.
        RerunManifestError: If the result document carries a feature path the
            manifest's grammar refuses, raised by :func:`build_rerun_lines`
            before anything is opened -- so a document that could only produce
            a forged entry leaves the previous artifact untouched rather than
            overwriting it with one.  See :func:`build_rerun_lines` for why the
            writer fails rather than emitting a shortened manifest.
    """
    destination = rerun_txt_path(base) if path is None else Path(path)
    # The grammar check runs first and on its own: the text is built before the
    # destination is created or opened, so a document that could only produce a
    # forged entry leaves both the directory and the previous manifest as they
    # were.
    text = build_rerun_text(result_set)
    with open_artifact_write(
        destination, encoding="utf-8", newline=LINE_ENDING
    ) as handle:
        handle.write(text)
    logger.debug(
        "Wrote %s (%d feature line(s), %d byte(s))",
        destination,
        text.count(LINE_ENDING),
        len(text.encode("utf-8")),
    )
    return destination


def _positive_line_number(text: str) -> int | None:
    """Interpret one segment as a scenario line number.

    Args:
        text: A single colon-delimited segment, already stripped.

    Returns:
        The line number, or ``None`` when the segment is not a plain positive
        decimal integer.  The check is deliberately narrow -- ASCII digits
        only, so no sign, no whitespace-embedded value, no underscore grouping
        and no non-ASCII digit is accepted -- because a segment that is not a
        line number is how the parser recognises where the path ends.

        A segment longer than :data:`MAX_LINE_NUMBER_DIGITS` is ``None`` for
        the same reason rather than an error: ``None`` means *"this segment is
        not a line number"*, so the segment flows on into the path, where
        :func:`validate_feature_path` refuses it as a typed
        :class:`RerunManifestError`.  The bound exists because :func:`int`
        raises a bare :class:`ValueError` above CPython's 4300-digit
        conversion limit, which would escape this module's error contract, and
        because converting a 5,000-digit run is work done on behalf of a
        tampered file.
    """
    if not text or len(text) > MAX_LINE_NUMBER_DIGITS:
        return None
    if not text.isascii() or not text.isdigit():
        return None
    try:
        value = int(text)
    except ValueError:
        # Belt and braces behind the length bound and the digit test: no input
        # is known to reach here, and the cost of being wrong about that is an
        # untyped escape from the AAP 0.4.1 exit contract.
        return None
    return value if value > 0 else None


def _parse_manifest_line(raw: str, number: int, source: str) -> RerunEntry | None:
    """Parse one manifest line.

    The ``file:`` scheme contains a colon, so it is removed before the line
    numbers are collected from the **right**, stopping at the first segment
    that is not a positive integer; everything to its left is rejoined as the
    path, and a colon surviving there is refused by
    :func:`validate_feature_path`.  A blank line yields ``None``, but a
    comment line is **rejected** and :data:`~app.utils.paths.FILE_URI_SCHEME`
    is **required**: behave's ``# -- RERUN:`` header means the rest of the
    file is in its ungrouped, unprefixed grammar, which read as this one
    would select the wrong scenarios (CWE-22).

    Args:
        raw: The line as read, with or without its terminator.
        number: The line's one-based position, for the error message.
        source: Where the line came from, for the error message.

    Returns:
        The entry, path normalised to the ``features/`` prefix, or ``None``
        for an empty or whitespace-only line.

    Raises:
        RerunManifestError: If the line carries data but is not a manifest
            entry: a comment or native header, a missing ``file:`` scheme, no
            trailing line number, or a path outside the grammar
            :func:`validate_feature_path` states.  The message names the
            source, the position and a fixed reason -- and **not** the line's
            text, which is untrusted input bound for stderr and a Jenkins
            console record (CWE-117, CWE-532).  The position is what lets an
            operator find the entry.
    """
    text = raw.strip()
    if not text:
        return None
    if text.startswith(COMMENT_PREFIX):
        raise RerunManifestError(
            f"{source}: line {number} is a comment, which this manifest "
            f"format never carries -- most likely behave's own "
            f"{COMMENT_PREFIX} -- RERUN: header, whose file holds the "
            "ungrouped path:line grammar this parser does not read"
        )
    if not text.startswith(FILE_URI_SCHEME):
        raise RerunManifestError(
            f"{source}: line {number} does not begin with the required "
            f"{FILE_URI_SCHEME!r} scheme -- most likely behave's ungrouped "
            f"path{LINE_SEPARATOR}line form rather than this manifest's"
        )

    body = text[len(FILE_URI_SCHEME) :]
    segments = body.split(LINE_SEPARATOR)
    lines: list[int] = []
    while len(segments) > 1:
        candidate = _positive_line_number(segments[-1].strip())
        if candidate is None:
            break
        lines.append(candidate)
        segments.pop()

    path = LINE_SEPARATOR.join(segments).strip()
    if not lines:
        raise RerunManifestError(
            f"{source}: line {number} carries no scenario line number"
        )
    validated = validate_feature_path(path, source=source, number=number)
    return RerunEntry(path=validated, lines=tuple(sorted(set(lines))))


def _merge_entries(entries: Iterable[RerunEntry]) -> list[RerunEntry]:
    """Collapse entries that name the same feature into one.

    Args:
        entries: Parsed entries, in file order.

    Returns:
        One entry per distinct path, in first-appearance order, each with its
        line numbers deduplicated and ascending.  This mirrors the writer's own
        grouping, so a parse of what the writer produced returns exactly one
        entry per line of it.
    """
    grouped: dict[str, list[int]] = {}
    for entry in entries:
        grouped.setdefault(entry.path, []).extend(entry.lines)
    return [
        RerunEntry(path=path, lines=tuple(sorted(set(lines))))
        for path, lines in grouped.items()
    ]


def parse_rerun_lines(
    lines: Iterable[str],
    source: str | None = None,
) -> list[RerunEntry]:
    """Parse manifest lines into entries.

    Args:
        lines: The lines, with or without terminators.  Accepting an iterable
            rather than a path is what lets the round-trip check read back
            :func:`build_rerun_lines` output with no file involved.
        source: Label used in error messages -- a filename, normally.

    Returns:
        One entry per feature, in first-appearance order; an empty iterable
        yields an empty list.  Every path is validated and normalised by
        :func:`validate_feature_path`, so a caller receives only
        ``features/<name>.feature`` -- no scheme, no legacy prefix, nothing
        that could name a file outside the feature tree.  The manifest read in
        production is the one :func:`write_rerun_txt` produced and already
        carries the port's prefix; a legacy manifest is rewritten through
        :func:`~app.utils.paths.normalize_feature_uri`, the single owner of
        that substitution, so the caller needs no normalisation step of its
        own.

        Parsing is **pure**: no filesystem and no working directory are
        touched, which is what lets the round trip be checked in memory.  A
        caller about to *execute* a location adds the filesystem tier with
        :func:`resolve_feature_path`, and a caller about to *read* the feature
        uses :func:`read_verified_feature`, which returns the contents of the
        object it verified.

    Raises:
        RerunManifestError: On the first line that carries data but is not a
            manifest entry -- a comment or behave-native header, a missing
            ``file:`` scheme, no trailing line number, or a path outside the
            grammar -- on a non-string element, on a ``lines`` argument that is
            not an iterable of text at all, on more than
            :data:`MAX_MANIFEST_LINES` lines, and on more than
            :data:`MAX_MANIFEST_ENTRIES` distinct features.  Never
            :class:`ValueError`, never :class:`TypeError`, and never
            :func:`sys.exit`: the whole module presents one error type so that
            ``app/cli.py`` can report the AAP 0.4.1 *"missing or malformed
            rerun manifest"* condition without classifying an exception.  No
            message reproduces the offending line; each carries the source,
            the position and a fixed reason, for the rule
            :func:`validate_feature_path` states.
    """
    label = source or "<rerun manifest>"
    whole_text = isinstance(lines, (str, bytes, bytearray))
    if whole_text or not isinstance(lines, Iterable):
        # The container is checked before the elements, for the same reason
        # each element's type is checked below: this module's contract is that
        # nothing untyped escapes it, and iterating a string would otherwise
        # parse it one character at a time and report a nonsensical position.
        hint = " -- pass whole text to parse_rerun_text()" if whole_text else ""
        raise RerunManifestError(
            f"{label}: manifest lines must be an iterable of text, not "
            f"{type(lines).__name__}{hint}"
        )
    parsed: list[RerunEntry] = []
    for number, raw in enumerate(lines, start=1):
        if number > MAX_MANIFEST_LINES:
            # Refused rather than truncated: a manifest with more lines than
            # this is not one this port wrote, and silently reading the first
            # few thousand lines of it would select a scenario set nobody
            # asked for.  The bound is stated as a number so the work a
            # tampered file can demand is a documented figure (CWE-400).
            raise RerunManifestError(
                f"{label}: the manifest carries more than "
                f"{MAX_MANIFEST_LINES} lines, so it is not a manifest this "
                "port wrote"
            )
        if not isinstance(raw, str):
            raise RerunManifestError(
                f"{label}: line {number} is {type(raw).__name__}, not text"
            )
        entry = _parse_manifest_line(raw, number, label)
        if entry is not None:
            parsed.append(entry)
    merged = _merge_entries(parsed)
    if len(merged) > MAX_MANIFEST_ENTRIES:
        # One entry per distinct feature file, and the lexical tier confines
        # every entry to one flat directory, so this bounds the fan-out of a
        # rerun: ten feature files exist (AAP 0.4.1).
        raise RerunManifestError(
            f"{label}: the manifest names more than {MAX_MANIFEST_ENTRIES} "
            "distinct features, which is more than this repository has"
        )
    return merged


def parse_rerun_text(text: str, source: str | None = None) -> list[RerunEntry]:
    """Parse a manifest held in memory.

    Args:
        text: The whole file's text.  Splitting is done with
            :meth:`str.splitlines`, so a CRLF file -- which this writer never
            produces but a Windows editor may leave behind -- parses
            identically to an LF one.
        source: Label used in error messages.

    Returns:
        One entry per feature, in first-appearance order; an empty list for
        empty or whitespace-only text.

    Raises:
        RerunManifestError: As :func:`parse_rerun_lines`.
    """
    return parse_rerun_lines(text.splitlines(), source=source)


def _confined_entries(
    entries: Iterable[RerunEntry],
    *,
    base: Path | str | None,
    source: str,
) -> list[RerunEntry]:
    """Keep the entries that survive the filesystem tier, and report the rest.

    The second half of the confinement, applied where the manifest's own
    location came from :mod:`app.utils.paths` and the features directory is
    therefore known to hang off the same ``base``.

    A violation is **dropped, not raised on**.  Raising would discard the whole
    manifest, and with it the real failures of every other feature in it, for
    one stale or tampered line; per the AAP 0.4.1 exit table a malformed
    manifest is tolerated at status ``0`` with the problem reported on stderr,
    which is exactly where :func:`resolve_feature_path`'s warning goes.

    Args:
        entries: Entries that have already passed the lexical tier.
        base: Directory the features directory hangs off.
        source: The manifest's path, for the diagnostic.

    What survives is decided by an **open descriptor**, not by a pathname:
    :func:`resolve_feature_path` opens the entry no-follow beneath a no-follow
    features root and ``fstat``-verifies it.  The descriptor is then released,
    because what this function produces is a *manifest* -- so a caller that
    goes on to read a feature opens and verifies it again, through
    :func:`read_verified_feature`, and acts on the contents that open returned
    rather than on this one's verdict about a name.  That is the division the
    two functions exist for, and it is why nothing here hands a caller a path
    to reopen.

    Returns:
        The entries whose feature file is a regular, single-linked,
        non-symlinked file directly inside a non-symlinked
        :func:`~app.utils.paths.features_dir`, in the order given.
    """
    kept: list[RerunEntry] = []
    for position, entry in enumerate(entries, start=1):
        if resolve_feature_path(entry.path, base=base) is None:
            logger.warning(
                "Dropping entry %d of %r: it does not name a feature file "
                "inside the %s directory, so its %d location(s) are not "
                "executable",
                position,
                source,
                FEATURES_DIR_NAME,
                len(entry.lines),
            )
            continue
        kept.append(entry)
    return kept


def _read_manifest_text(path: Path) -> str:
    """Read a rerun manifest through a verified descriptor, bounded.

    The only read of a manifest file in this port, and deliberately not
    :meth:`pathlib.Path.read_text`.  A manifest is **machine input to a
    runner** (``FailedTestRunner.java:11``) sitting at a well-known location
    inside a CI workspace, so three properties have to be established before
    its first byte reaches the grammar:

    * **It is the file it claims to be.**  The read goes through
      :func:`~app.utils.paths.open_artifact_read`, the path authority's
      no-follow reader, which opens every component from ``target`` inward
      ``O_NOFOLLOW`` under its verified parent and ``fstat``-checks the
      descriptor for a regular file with a single hard link.  A symbolic link
      planted at the manifest's own location, a hard link to a file
      elsewhere, a directory, a device and a FIFO are each refused -- a named
      pipe without
      that reader's ``O_NONBLOCK`` would simply hang the command (CWE-59,
      CWE-22, CWE-400).
    * **It is bounded.**  At most :data:`MAX_MANIFEST_BYTES` + 1 bytes are
      read, and one byte over the bound is a refusal, so neither a huge
      regular file nor an endless one is taken into memory before the grammar
      is consulted.
    * **Its refusal says nothing about its contents.**  Every message carries
      the manifest's own location, a fixed reason and -- where the platform
      supplies one -- the ``errno`` symbol.  Not the exception text, which
      carries the filename the call was made with, and never the bytes read.

    Args:
        path: The manifest's location, from
            :func:`~app.utils.paths.rerun_txt_path` or from an explicit
            caller-supplied source.  Both go through this function: a caller
            cannot opt into an unverified read, because there is no second
            code path to opt into.

    Returns:
        The decoded text, newline translation left to
        :meth:`str.splitlines` in :func:`parse_rerun_text`, which treats
        ``\\r\\n`` as one break exactly as universal-newline decoding would.

    Raises:
        RerunManifestError: If the entry is refused, is absent, cannot be
            read, exceeds :data:`MAX_MANIFEST_BYTES`, or is not valid UTF-8.
            The cause is always chained, so a log keeps the diagnosis.
    """
    try:
        with open_artifact_read(path) as handle:
            data = handle.read(MAX_MANIFEST_BYTES + 1)
    except ArtifactPathError as error:
        raise RerunManifestError(
            f"{path}: rerun manifest is refused -- it is a link, or not a "
            "regular file with a single name, and a runner's feature source "
            "has to be the file it was checked as"
        ) from error
    except OSError as error:
        code = errno.errorcode.get(error.errno or 0, "unknown")
        raise RerunManifestError(
            f"{path}: rerun manifest cannot be read ({code})"
        ) from error

    if len(data) > MAX_MANIFEST_BYTES:
        raise RerunManifestError(
            f"{path}: rerun manifest is larger than the "
            f"{MAX_MANIFEST_BYTES}-byte bound, so it is not a manifest this "
            "port wrote"
        )
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RerunManifestError(
            f"{path}: rerun manifest is not valid UTF-8 (at byte offset "
            f"{error.start})"
        ) from error


def parse_rerun_file(
    source: Path | str | Iterable[str] | None = None,
    base: Path | str | None = None,
    *,
    confine: bool | None = None,
) -> list[RerunEntry]:
    """Read a rerun manifest and return its entries.

    The reading half of the AAP 0.6 round trip and the owner of the grammar,
    so ``app/cli.py``'s ``--rerun`` parses nothing itself.  **The confinement
    is enforced here**: in the production mode (``source`` left ``None``, as
    ``app/services/test_run_service.py`` calls it) every entry has passed both
    tiers, so its locations go on the engine's argv unchecked (CWE-22).

    Every file read -- the production one and any explicit ``source`` --
    goes through :func:`_read_manifest_text`, which opens a **verified
    no-follow regular-file descriptor** through the path authority and reads
    at most :data:`MAX_MANIFEST_BYTES` + 1 bytes.  There is no second,
    unverified read path for a caller to reach, which is the point: the file
    is a runner's feature source, and a link or a stream planted at its
    location would otherwise let contents from outside the workspace choose
    the scenarios that execute (CWE-59, CWE-400).

    Args:
        source: What to read.  ``None`` -- the production case -- resolves
            :func:`~app.utils.paths.rerun_txt_path`, the port of the manifest
            ``FailedTestRunner.java:11`` declares as its feature source.  A
            :class:`~pathlib.Path`, a string or any :class:`os.PathLike` is
            read as a file, through the same verified bounded reader.  Any
            other iterable is treated as lines already in hand, so
            ``parse_rerun_file(build_rerun_lines(result_set))`` round-trips a
            document without going through disk.
        base: Directory the default path hangs off, and the directory the
            features are resolved under when the filesystem tier runs;
            otherwise ignored.
        confine: Whether to apply the filesystem tier.  ``None``, the default,
            means *"whenever this function resolved the manifest's location
            itself"* -- that is, whenever ``source`` is ``None``, because only
            then is the features directory known to hang off the same
            ``base``.  Given an explicit ``source`` the tree it belongs to is
            the caller's knowledge, so the default is lexical validation
            alone and ``confine=True`` asks for both tiers against ``base``.
            ``confine=False`` asks for the lexical tier only.  The value is
            ignored when ``source`` is an iterable of lines, which touches no
            filesystem at all.

    Returns:
        One entry per feature, in file order.  An empty file yields an empty
        list, as does one whose every entry was dropped by the second tier.

    Raises:
        RerunManifestError: If the file is absent, refused as a link or a
            non-regular entry, unreadable, larger than
            :data:`MAX_MANIFEST_BYTES`, not valid UTF-8, or if its own path is
            unusable, or if any line carries data but is not a manifest entry,
            or if it exceeds :data:`MAX_MANIFEST_LINES` or
            :data:`MAX_MANIFEST_ENTRIES`.  One error type covers every case on
            purpose: per the AAP 0.4.1 exit table *"a missing or malformed
            rerun manifest"* still exits ``0`` with the problem reported on
            stderr, so the caller needs to recognise the condition, not
            classify it.  The cause is always chained.

            The *path* is part of that contract, not just the contents.
            Constructing a :class:`~pathlib.Path` can raise
            :class:`TypeError` for a non-path object and :class:`ValueError`
            for one carrying an embedded NUL byte -- neither an
            :class:`OSError` -- so the resolution is guarded here and the read
            itself, including its :class:`UnicodeDecodeError` case, is guarded
            inside :func:`_read_manifest_text`.
    """
    if source is None or isinstance(source, (str, Path, os.PathLike)):
        path: Path | None = None
        try:
            path = rerun_txt_path(base) if source is None else Path(source)
            text = _read_manifest_text(path)
        except (TypeError, ValueError) as error:
            # ``path`` is still None when the failure was in constructing it,
            # and where it is not, the location is spelled printably: an
            # unusable path is precisely the kind that carries a control
            # character -- an embedded NUL is what makes `os.open` raise
            # ValueError here -- and this message reaches stderr and a CI
            # console record (CWE-117, CWE-532).  The offending *input* is
            # never echoed beyond the location the caller named.
            location = (
                _printable(str(path)) if path is not None else "<rerun manifest>"
            )
            raise RerunManifestError(
                f"{location}: rerun manifest path is unusable "
                f"({type(error).__name__})"
            ) from error
        entries = parse_rerun_text(text, source=str(path))
        # ``None`` means "confine when this function resolved the manifest's
        # location itself", which is the production mode and the only one in
        # which the features directory is known to hang off the same ``base``.
        apply_filesystem_tier = source is None if confine is None else confine
        if apply_filesystem_tier:
            return _confined_entries(entries, base=base, source=str(path))
        return entries
    return parse_rerun_lines(source)


def rerun_locations(
    source: Path | str | Iterable[str] | None = None,
    base: Path | str | None = None,
    *,
    confine: bool | None = None,
) -> list[str]:
    """Return the ``path:line`` locations a rerun should execute.

    The form ``app/cli.py`` hands to the engine under ``--rerun``: the grouped
    file expanded back into one location per failing scenario, in feature and
    ascending line order.  Four couplings follow from what
    ``FailedTestRunner.java:9-12`` omits: passing these locations clears the
    default tag filter, ``--rerun --tags`` is a usage error, the rerun writes
    no artifacts, and it must not clean -- ``--clean`` would delete the
    manifest being read.

    Args:
        source: As :func:`parse_rerun_file`.
        base: As :func:`parse_rerun_file`.
        confine: As :func:`parse_rerun_file`; both tiers by default in the
            production mode, so these locations are executable as given.

    Returns:
        One ``"<path>:<line>"`` string per failing scenario, empty when the
        manifest is empty or nothing survived confinement.  Every path passed
        :func:`validate_feature_path`, and in production
        :func:`resolve_feature_path` too, so each is safe as argv (CWE-22).

    Raises:
        RerunManifestError: As :func:`parse_rerun_file`.
    """
    return [
        location
        for entry in parse_rerun_file(source, base, confine=confine)
        for location in entry.locations
    ]
