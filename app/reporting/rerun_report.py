"""The grouped rerun manifest -- the machine input a retry run selects from.

This is a small file with a load-bearing format.  ``FailedTestRunner.java:11``
declares the manifest as its feature source, so what it holds is **machine
input to a runner**, not a human convenience log: whatever this module writes
is what a rerun executes, and a format error here silently changes which
scenarios are retried.  Source anchor: the reference build's own manifest,
whose AAP 0.4.1 row reads *"Writes one ``file:<path>:<line>[:<line>...]`` line
per feature"*.

The measured baseline
---------------------
Unlike the JSON and the two HTML artifacts, the reference rerun manifest is
**clean** -- no merge-conflict markers.  ``od -c`` reports exactly 50 bytes: one
line plus one trailing newline::

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

* A scenario is failing when its rolled-up status is a member of
  :data:`FAILURE_STATUSES` -- **not** when it equals the single literal
  ``failed``.  behave's failure vocabulary is wider than Cucumber's:
  ``error``, ``hook_error`` and ``cleanup_error`` are all folded onto
  ``failed`` by ``app/reporting/cucumber_json.py``, and the JVM's rerun
  formatter records every test case whose ``Status.isOk()`` is false, which is
  everything but ``PASSED`` and ``SKIPPED``.  Comparing against one literal
  would therefore have the JSON report carry a failure the rerun manifest
  omitted, and the pair is read as a matched set.  :data:`FAILURE_STATUSES`
  carries each member's provenance and each exclusion's reason.
* Elements in the internal schema carry no status of their own, so the roll-up
  is over the scenario's steps, the steps of the Background occurrence
  **immediately preceding** it, and both its ``before`` and ``after`` hook
  entries -- the JVM's rerun formatter keys on the test-case result, which
  subsumes hooks, so a scenario whose setup hook failed and whose steps never
  ran is still re-selected.
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
segment both reach :func:`int` and :meth:`~pathlib.Path.read_text` as
:class:`ValueError`, so both are bounded and converted here
(:data:`MAX_LINE_NUMBER_DIGITS`).

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
at all.  :func:`resolve_feature_path` is the **filesystem tier** that settles
that, and :func:`parse_rerun_file` applies it itself whenever it resolves the
manifest's own location from :mod:`app.utils.paths` -- which is every
production read.  A tier that some caller has to remember to invoke is a tier
that is one forgotten call site away from absent, so the confinement is in the
production read path rather than beside it:

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
no in-process check can close, so the filesystem tier does not merely ask where
a name resolves: it **refuses a symlinked entry outright**.  Once the final
component is known not to be a link, the name the engine is given and the file
that was checked denote the same object, and redirecting it would take a fresh
write into ``features/`` rather than a swap of an existing link.  The ten
feature files this port owns are regular files, so nothing legitimate is
refused by that rule.

The writer is held to the same grammar, and it is the harder half: the internal
result document is assembled by worker processes, so a feature ``path`` is
worker-controlled input.  A path carrying LF would emit **two** valid entries
from one feature, a colon-bearing path fabricates a line number, and both are
refused before serialisation.  :func:`_format_entry_line` is the one place an
entry is concatenated and re-checks the finished line, so "one line per
feature" is enforced where it is produced.

Boundaries and determinism
--------------------------
* Only :mod:`app.reporting.events` and :mod:`app.utils.paths` are imported.  No
  service (the dependency edge runs ``SV --> RP``, never back), no Flask, no
  Selenium, no :mod:`app.config`.
* **No path literal appears here**: :func:`~app.utils.paths.rerun_txt_path` and
  :func:`~app.utils.paths.ensure_parent` own the location, the accepted path's
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
* Nothing is deleted: :mod:`app.utils.paths` creates but never removes, and
  ``--clean`` belongs to ``app/cli.py``.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path
from typing import Final, NamedTuple

from app.reporting.events import (
    ELEMENT_TYPE_BACKGROUND,
    ELEMENT_TYPE_SCENARIO,
    JsonDict,
    ResultSet,
)
from app.utils.paths import (
    FEATURES_DIR_NAME,
    FILE_URI_SCHEME,
    NORMALIZED_FEATURES_PREFIX,
    ensure_parent,
    features_dir,
    normalize_feature_uri,
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
    "MAX_LINE_NUMBER_DIGITS",
    "PATH_SEPARATOR",
    "RerunEntry",
    "RerunManifestError",
    "STATUS_SPELLINGS",
    "build_rerun_lines",
    "build_rerun_text",
    "is_failure_status",
    "iter_failed_scenarios",
    "normalize_status",
    "parse_rerun_file",
    "parse_rerun_lines",
    "parse_rerun_text",
    "rerun_locations",
    "resolve_feature_path",
    "validate_feature_path",
    "write_rerun_txt",
]

#: Module logger.  Deliberately without a handler of its own, matching
#: :mod:`app.reporting.events`: ``app/logging_config.py`` installs the split
#: that routes WARNING-and-above to stderr, which is where the AAP 0.4.1 exit
#: table expects a manifest problem to be reported, and Python's ``lastResort``
#: handler covers a bare import in a test.  A ``NullHandler`` here would
#: silence both routes.
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
#: re-selectable, and the JSON writer folds a wider behave vocabulary onto
#: ``failed``.  Each member's provenance, all of it measured:
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


# --------------------------------------------------------------------------- #
# The accepted feature URI.
#
# One grammar with one owner, applied on both sides of the round trip: the
# writer validates before it serialises and the parser validates after it
# splits, so no unvalidated path can enter the artifact or leave it.  Stating
# it twice -- once per side -- is exactly how the two drifted apart, which is
# what let a worker-controlled path forge a second manifest entry and a
# tampered file direct a rerun outside the feature tree.
# --------------------------------------------------------------------------- #


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
      unresolvable: ``select_rerun_scenarios`` resolves a manifest entry as
      ``features_dir(base) / Path(path).name``, discarding any directory part.
      Accepting one would silently execute a different file from the one the
      entry names.
    * :func:`resolve_feature_path` is the **filesystem tier** on top of it,
      for the caller that is about to execute a location.

    The legacy prefix is normalised **first**, through
    :func:`~app.utils.paths.normalize_feature_uri`, which is the single owner
    of that substitution.  That is load-bearing rather than cosmetic: the
    committed baseline ``tests/fixtures/golden_rerun.txt`` is stored verbatim
    with its ``src/main/resources/features/`` prefix, and a worker document
    left behind by the Java layout carries the same, so both have to be
    accepted -- and both become the port's ``features/`` form on the way
    through.

    Args:
        path: The candidate path, as read from a result document or parsed
            from a manifest line.  Typed as :class:`object` because checking
            that it *is* text is part of the validation: a JSON document can
            carry any type under ``path``.
        source: Where the path came from, for the error message.
        number: The entry's one-based position in that source, for the error
            message.

    Returns:
        The validated path, normalised to the port's ``features/`` prefix.
        Never carries the ``file:`` scheme -- the scheme is the caller's to
        add on the writer side and to strip on the parser side.

    Raises:
        RerunManifestError: If the path is not text, is empty, carries a
            forbidden character (see :data:`FORBIDDEN_PATH_CHARACTERS`), is
            absolute or a UNC path, contains an empty, ``.``, ``..``,
            hidden or whitespace-padded component, or is not exactly one
            ``*.feature`` file directly under the features directory.  Only
            this type is raised -- never :class:`ValueError`, never
            :class:`OSError` -- because the AAP 0.4.1 exit table has a
            *"missing or malformed rerun manifest"* exit ``0`` with the problem
            reported on stderr, and the caller has to recognise that condition
            rather than classify an exception.

    Examples:
        >>> validate_feature_path("features/Crm.feature")
        'features/Crm.feature'
        >>> validate_feature_path("src/main/resources/features/Crm.feature")
        'features/Crm.feature'
    """
    context = _error_context(source, number)
    if not isinstance(path, str):
        raise RerunManifestError(
            f"{context}a feature path must be text, not "
            f"{type(path).__name__}: {path!r}"
        )
    if not path.strip():
        raise RerunManifestError(
            f"{context}the entry names no feature path: {path!r}"
        )

    candidate = normalize_feature_uri(path)

    for character in candidate:
        if character in FORBIDDEN_PATH_CHARACTERS:
            raise RerunManifestError(
                f"{context}the feature path carries {character!r}, which a "
                f"manifest entry may never contain: {candidate!r}"
            )
    if candidate.startswith(PATH_SEPARATOR):
        # Catches the POSIX absolute form and the UNC form `//host/share`
        # alike.  The Windows drive forms need no test of their own: `C:/...`
        # is refused by the colon in FORBIDDEN_PATH_CHARACTERS and `C:\...`
        # by both that colon and the backslash.
        raise RerunManifestError(
            f"{context}the feature path is absolute; a manifest entry is "
            f"relative to the repository root: {candidate!r}"
        )

    components = candidate.split(PATH_SEPARATOR)
    for component in components:
        if not component:
            raise RerunManifestError(
                f"{context}the feature path has an empty component -- a "
                f"doubled separator: {candidate!r}"
            )
        if component in (os.curdir, os.pardir):
            raise RerunManifestError(
                f"{context}the feature path traverses with {component!r}: "
                f"{candidate!r}"
            )
        if component.startswith(os.curdir):
            # Refuses every hidden entry, `target/.workers/` included, whose
            # intermediate documents must never be reachable (AAP 0.4.2).
            raise RerunManifestError(
                f"{context}the feature path names the hidden component "
                f"{component!r}: {candidate!r}"
            )
        if component != component.strip():
            raise RerunManifestError(
                f"{context}the feature path component {component!r} is "
                f"padded with whitespace: {candidate!r}"
            )

    required = f"{NORMALIZED_FEATURES_PREFIX}<name>{FEATURE_SUFFIX}"
    if len(components) != 2 or components[0] != FEATURES_DIR_NAME:
        raise RerunManifestError(
            f"{context}the feature path must be {required}, one Gherkin file "
            f"directly under the {FEATURES_DIR_NAME} directory: {candidate!r}"
        )
    name = components[1]
    if not name.endswith(FEATURE_SUFFIX) or len(name) == len(FEATURE_SUFFIX):
        raise RerunManifestError(
            f"{context}the feature path must be {required} with a non-empty "
            f"file name: {candidate!r}"
        )
    return candidate


def resolve_feature_path(
    path: object,
    *,
    base: Path | str | None = None,
) -> Path | None:
    """Resolve a manifest path to the feature file it may execute, or ``None``.

    The **filesystem tier** of the confinement.  :func:`parse_rerun_file`
    applies it on every production read, so no caller has to remember to; it
    is public because a caller holding a single location -- rather than a
    manifest -- needs the same answer.  The lexical tier,
    :func:`validate_feature_path`, which this function applies first, already
    confines every accepted entry to ``features/<name>.feature``; this adds
    what only the filesystem can answer, in three refusals:

    #. **A symlinked entry is refused outright**, by a no-follow
       :meth:`~pathlib.Path.is_symlink` test on the final component.  This is
       the one that matters for a location handed to *another process*: a
       resolved-destination check alone would leave the engine opening the
       name again later, through whatever the link points at by then.  With
       links refused, the checked object and the name the engine opens are the
       same file.
    #. **A non-regular entry is refused** -- a directory, a device node, a
       FIFO, or an absent path -- because none of them is Gherkin a runner can
       execute.
    #. **An entry resolving outside the features directory is refused**, which
       is what catches a link or a component swap the first two tests would
       not see on their own.

    It is deliberately **not** part of :func:`parse_rerun_lines` or
    :func:`parse_rerun_text`, which stay pure -- no filesystem, no working
    directory -- which is what lets the round trip AAP 0.6 requires be checked
    in memory and keeps the AAP 0.5.1 coverage gate on ``app/reporting``
    reachable without a temporary feature tree.

    A violation is a tolerated condition, not an exception: per the AAP 0.4.1
    exit table a malformed manifest still exits ``0`` with the problem
    reported on stderr, and the module logger routes WARNING and above there.
    Every message interpolates the offending value with ``%r`` so that a path
    which somehow still carried a terminator could not forge a log line
    (CWE-117).

    Args:
        path: A manifest path, validated or not.
        base: Directory the features directory hangs off; ``None`` means the
            process working directory, as everywhere in
            :mod:`app.utils.paths`.

    Returns:
        The resolved :class:`~pathlib.Path` when the entry names a regular,
        non-symlinked file strictly inside
        :func:`~app.utils.paths.features_dir`, and ``None`` for every other
        outcome -- an invalid path, a symlink, a directory or other
        non-regular object, one that escapes the directory once symlinks are
        followed, an absent file, or a resolution the operating system
        refused.
    """
    try:
        validated = validate_feature_path(path)
    except RerunManifestError as error:
        logger.warning("Refusing a rerun location: %s", error)
        return None

    directory = features_dir(base)
    try:
        # ``Path(validated).name`` is the whole of the entry below the
        # features directory -- the lexical tier has already established that
        # -- so this is the file that would really execute.
        root = directory.resolve()
        entry = root / Path(validated).name
        # No-follow first: `is_symlink` stats the link itself, so a link is
        # refused as a link rather than judged by where it currently points.
        linked = entry.is_symlink()
        resolved = entry.resolve()
        contained = not linked and resolved.parent == root
        regular = contained and resolved.is_file()
    except (OSError, ValueError) as error:
        # Guarded so that nothing untyped escapes this module: a resolution
        # can fail on a symlink loop or a permission error, and neither is a
        # reason to abandon a run whose failures are already recorded.
        logger.warning(
            "The rerun location %r could not be resolved under %r: %s",
            validated,
            str(directory),
            error,
        )
        return None

    if linked:
        logger.warning(
            "Refusing the rerun location %r: %r is a symbolic link, and a "
            "location handed to the engine has to be the file it was checked "
            "as -- it resolves to %r",
            validated,
            str(entry),
            str(resolved),
        )
        return None
    if not contained:
        logger.warning(
            "Refusing the rerun location %r: it resolves to %r, outside the "
            "%s directory %r",
            validated,
            str(resolved),
            FEATURES_DIR_NAME,
            str(root),
        )
        return None
    if not regular:
        logger.warning(
            "Refusing the rerun location %r: %r is not a regular file",
            validated,
            str(resolved),
        )
        return None
    return resolved


def normalize_status(status: str | None) -> str:
    """Return a recorded status in the one spelling this module compares.

    The single owner of status normalisation for the manifest, so that the
    failure vocabulary is stated once rather than re-derived at each
    comparison.  Three operations, and no more: surrounding whitespace is
    dropped, the name is lower-cased, and the redundant spellings in
    :data:`STATUS_SPELLINGS` are folded exactly as behave's own
    ``Status.normalized_name`` folds them.  The *outcome* is never changed --
    ``error`` stays ``error``, and it is :data:`FAILURE_STATUSES` that decides
    what an outcome means.

    Args:
        status: A status name as recorded in the internal result document, or
            ``None`` for a node that carries none.

    Returns:
        The normalised name, or ``""`` for ``None`` and for a blank name.
        ``""`` is never a member of :data:`FAILURE_STATUSES`, so an absent
        status can never select a scenario.

    Examples:
        >>> normalize_status("  HOOK_ERROR ")
        'hook_error'
        >>> normalize_status("untested_undefined")
        'undefined'
        >>> normalize_status(None)
        ''
    """
    if not isinstance(status, str):
        return ""
    name = status.strip().lower()
    return STATUS_SPELLINGS.get(name, name)


def is_failure_status(status: str | None) -> bool:
    """Report whether a recorded status means the scenario has to be rerun.

    The predicate half of :data:`FAILURE_STATUSES`, which documents why each
    member is in the set and why the excluded ones are out.  Used by
    :func:`_has_failure` for steps and for both hook groups, so one rule covers
    every node in the document.

    Args:
        status: A status name, or ``None``.

    Returns:
        ``True`` when the normalised name is a member of
        :data:`FAILURE_STATUSES`.

    Examples:
        >>> is_failure_status("error")
        True
        >>> is_failure_status("skipped")
        False
    """
    return normalize_status(status) in FAILURE_STATUSES


def _status_of(node: JsonDict) -> str:
    """Return the normalised status recorded on a step or hook entry.

    Args:
        node: A step object, or a ``before``/``after`` hook entry, each of
            which carries its outcome under ``result.status``.

    Returns:
        The status as :func:`normalize_status` spells it, or ``""`` when the
        node carries no usable result.  A missing result is never treated as a
        failure: the manifest exists to re-select scenarios that demonstrably
        failed, and inventing a failure from absent data would retry a
        scenario that never ran.
    """
    result = node.get("result")
    if not isinstance(result, dict):
        return ""
    return normalize_status(result.get("status"))


def _has_failure(node: JsonDict) -> bool:
    """Report whether an element's steps or hooks record a failure.

    The rule is membership of :data:`FAILURE_STATUSES`, not equality with the
    literal ``failed``: behave records an exception in a step as ``error`` and
    a hook fault as ``hook_error``/``cleanup_error``, and
    ``app/reporting/cucumber_json.py`` folds all three onto ``failed``, so
    comparing against one literal would have the JSON report a failure that
    the manifest omitted.  ``undefined``, ``pending`` and ``ambiguous`` join
    them because Cucumber-JVM's rerun formatter records every test case whose
    ``Status.isOk()`` is false, and ``isOk()`` is ``PASSED || SKIPPED`` alone.

    Both hook groups are consulted.  ``after`` carries the teardown and the
    screenshot hook; ``before`` carries setup, so a scenario whose browser
    never started is re-selected rather than silently dropped.  A Background
    occurrence carries neither key by contract
    (:func:`app.reporting.events.new_element`), so both loops are simply empty
    for one, and a document that does carry them is handled without a special
    case.

    Args:
        node: A Background occurrence or a scenario element.

    Returns:
        ``True`` if any of its steps, or any of its ``before`` or ``after``
        hook entries, records a status :func:`is_failure_status` accepts.  A
        node whose ``result`` is absent or unusable never counts as a failure.
    """
    for group in ("steps", "before", "after"):
        for item in node.get(group) or ():
            if isinstance(item, dict) and is_failure_status(_status_of(item)):
                return True
    return False


def _feature_path(feature: JsonDict) -> str:
    """Return the repository-relative path a feature's manifest line names.

    ``path`` is read in preference to ``uri`` because
    :mod:`app.reporting.events` carries both *"so that no consumer performs
    string surgery on the other"*.  The ``uri`` fallback exists only for a
    hand-built fixture that omitted ``path``, and it removes the scheme through
    :data:`~app.utils.paths.FILE_URI_SCHEME` rather than by slicing a literal.

    A path that is **present** is validated by
    :func:`validate_feature_path`, which normalises it through
    :func:`~app.utils.paths.normalize_feature_uri` -- so a legacy
    ``src/main/resources/features/`` path from a golden fixture or a stale
    worker file is rewritten -- and then holds it to the manifest's grammar.
    The validation is not tidiness: the internal document is assembled by
    worker processes, this artifact is line-oriented machine input, and a path
    carrying a line terminator would emit a **second, forged entry** directing
    a rerun at a feature that never failed.

    Args:
        feature: A feature object from the internal result set.

    Returns:
        The validated, normalised path, or ``""`` when the feature carries
        neither a ``path`` nor a ``uri``.  ``""`` cannot inject anything, and
        the caller's logged skip for it is this module's documented behaviour.

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

    Backgrounds are therefore paired here, in the order
    :mod:`app.reporting.events` recorded them: an occurrence belongs to the
    scenario immediately following it, and where two occurrences follow one
    another the earlier one is orphaned rather than carried forward -- matching
    how the collector groups its elements.

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
    features = result_set.get("features")
    if not isinstance(features, Sequence) or isinstance(features, (str, bytes)):
        return
    for feature in features:
        if not isinstance(feature, dict):
            continue
        elements = feature.get("elements")
        if not isinstance(elements, Sequence) or isinstance(elements, (str, bytes)):
            continue
        pending_background: JsonDict | None = None
        for element in elements:
            if not isinstance(element, dict):
                continue
            if element.get("type") == ELEMENT_TYPE_BACKGROUND:
                pending_background = element
                continue
            background, pending_background = pending_background, None
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
            failed = _has_failure(element) or (
                background is not None and _has_failure(background)
            )
            if failed:
                yield feature, element


# --------------------------------------------------------------------------- #
# Building the manifest.  Pure: no clock, no working directory, no filesystem.
# --------------------------------------------------------------------------- #


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

    The pure half of this writer, which is what lets the whole format be tested
    without a browser, a network or a temporary directory -- the AAP 0.5.1
    coverage gate on ``app/reporting`` depends on that.  Calling it twice with
    the same document returns equal output and touches nothing.

    Grouping is by feature path, in **first-appearance order**, so features come
    out in source order without a sort.  Two feature objects that share a path
    -- which a merge across workers can produce -- contribute to a single line,
    because the format allows exactly one line per feature.  Line numbers are
    deduplicated and sorted ascending, so a retried or doubly-reported scenario
    cannot produce ``:9:9``.

    ``sortingMethod: 'ALPHABETICAL'`` in ``Jenkins:15`` is a publisher display
    option: the features here are deliberately *not* sorted alphabetically.

    A feature whose path cannot be determined at all, or a failing scenario
    whose line is not a positive integer, is logged at warning level and
    omitted: the document is structurally unusable at that point, a location a
    runner could not resolve would be worse than a short manifest, and neither
    condition is an I/O fault -- *"failures are data"*.  A path that is present
    but **invalid** is the one case that does not merely skip; see Raises.

    Args:
        result_set: The merged internal result document from
            :mod:`app.reporting.events`.

    Returns:
        One string per feature that has at least one failing scenario, each of
        the form ``file:<path>:<line>[:<line>...]`` and carrying no line
        terminator -- :func:`_format_entry_line` is the single point that
        decides that shape and the single point that enforces it.  An empty
        list when nothing failed, which :func:`build_rerun_text` turns into a
        zero-byte file rather than into a comment.

    Raises:
        RerunManifestError: If a feature carries a path the manifest's grammar
            refuses (:func:`validate_feature_path`).  Failing is deliberate
            and it is safe.  Emitting the entry would forge a rerun location
            -- a path carrying a line terminator produces two valid entries --
            and silently dropping it would hide a real failure from the rerun,
            which is the one thing a rerun must not do.  Raising instead lands
            on a row the AAP 0.4.1 exit contract already specifies:
            ``app/services/report_service.py`` wraps every writer in
            ``except Exception``, so this becomes *"a writer fails after
            earlier writers succeeded"* -- non-zero status, the artifacts
            written before it retained, the failing writer named on stderr --
            and never an unhandled traceback.  The offending path is logged
            with ``%r`` so that a terminator inside it cannot forge a log line
            (CWE-117).
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


# --------------------------------------------------------------------------- #
# Writing the manifest.  The only impure function here.
# --------------------------------------------------------------------------- #


def write_rerun_txt(
    result_set: ResultSet,
    path: Path | str | None = None,
    base: Path | str | None = None,
) -> Path:
    """Write the rerun manifest and return the path written.

    A thin wrapper over :func:`build_rerun_text`: it decides where to write and
    how the bytes reach disk, and nothing else.  The file is always created,
    including when nothing failed -- the AAP 0.4.1 exit contract requires all
    four artifacts even for a run with no failures and for one whose tag
    expression selected nothing -- and in that case it is zero bytes.

    The encoding and the line terminator are both explicit.  ``newline="\\n"``
    disables the platform translation that would otherwise turn every
    terminator into CRLF on the Windows half of the ``isUnix()`` branch, and
    ``encoding="utf-8"`` pins the bytes independently of the locale.

    Args:
        result_set: The merged internal result document.
        path: Explicit destination.  ``None`` -- the production case -- resolves
            :func:`~app.utils.paths.rerun_txt_path`, so this module holds no
            path literal.  A test drives it against a temporary directory
            either through this parameter or through ``base``.
        base: Directory the default path hangs off; ``None`` means the process
            working directory, matching how Maven resolved its build output.
            Ignored when ``path`` is given.

    Returns:
        The path written, so a caller can log or serve it.

    Raises:
        OSError: If the parent directory cannot be created or the file cannot
            be written.  A genuine I/O fault is the command's writer-failure
            exit class, whereas a test outcome never reaches this function as
            an exception.
        RerunManifestError: If the result document carries a feature path the
            manifest's grammar refuses, raised by :func:`build_rerun_lines`
            before anything is opened -- so a document that could only produce
            a forged entry leaves the previous artifact untouched rather than
            overwriting it with one.  See :func:`build_rerun_lines` for why the
            writer fails rather than emitting a shortened manifest.
    """
    destination = rerun_txt_path(base) if path is None else Path(path)
    ensure_parent(destination)
    text = build_rerun_text(result_set)
    with open(destination, "w", encoding="utf-8", newline=LINE_ENDING) as handle:
        handle.write(text)
    logger.debug(
        "Wrote %s (%d feature line(s), %d byte(s))",
        destination,
        text.count(LINE_ENDING),
        len(text.encode("utf-8")),
    )
    return destination


# --------------------------------------------------------------------------- #
# Reading the manifest back.  The other half of the round trip: one owner for
# the grammar, so ``app/cli.py``'s ``--rerun`` re-implements none of it.
# --------------------------------------------------------------------------- #


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

    The parsing hazard this function exists for: the ``file:`` scheme contains
    a colon, so splitting the whole line on ``":"`` would mistake the scheme
    for a path segment.  The scheme is therefore removed first, and the line
    numbers are then collected from the **right**, stopping at the first
    segment that is not a positive integer.  Everything to the left of that is
    rejoined as the path, which keeps the parser correct for any path that
    itself contains a colon -- something the manifests this port writes never
    contain, since the paths are repository-relative and POSIX-shaped, but
    which costs nothing to survive.  A colon that survives into the path is
    then rejected outright by :func:`validate_feature_path`, so the tolerance
    costs no confinement.

    Three rules make the grammar the writer's exactly, rather than a superset
    of it, which is what stops a tampered or foreign manifest from selecting
    something the run never produced (CWE-22):

    #. An empty or whitespace-only line yields ``None`` and is still
       tolerated.  This writer emits none, and a blank line carries no grammar
       signal at all -- nothing about it suggests the rest of the file is in a
       different format -- so skipping it discards no information.
    #. A line beginning with :data:`COMMENT_PREFIX` is **rejected**.  This
       writer emits no comment, and the one thing that realistically produces
       one is behave's own rerun formatter, whose header is ``# -- RERUN: %d
       failing scenarios during last test run.``.  That header means the
       remaining lines are in behave's *ungrouped, unprefixed* grammar, so
       reading the file as this one would silently select the wrong scenarios.
    #. The literal :data:`~app.utils.paths.FILE_URI_SCHEME` is **required**,
       for the same reason: an entry without it is behave's ``path:line``
       form, not this module's.

    Leading and trailing whitespace is stripped from the line and from the
    path, so a hand-edited file with a padded line still reads; whitespace
    *inside* the path -- a padded component -- is rejected by
    :func:`validate_feature_path`, because the writer could never produce it.

    Args:
        raw: The line as read, with or without its terminator.
        number: The line's one-based position, for the error message.
        source: Where the line came from, for the error message.

    Returns:
        The entry, or ``None`` for an empty or whitespace-only line.  The path
        is normalised to the port's ``features/`` prefix, so a legacy manifest
        -- the committed ``tests/fixtures/golden_rerun.txt``, or one left by
        the Java layout -- reads as the port's own.

    Raises:
        RerunManifestError: If the line carries data but is not a manifest
            entry: a comment or native header, a missing ``file:`` scheme, no
            trailing line number, or a path outside the grammar
            :func:`validate_feature_path` states.  The message names the
            source, the position and the offending text, because that is what
            ``app/cli.py`` reports on stderr before exiting ``0``.
    """
    text = raw.strip()
    if not text:
        return None
    if text.startswith(COMMENT_PREFIX):
        raise RerunManifestError(
            f"{source}: line {number} is a comment, which this manifest "
            f"format never carries -- most likely behave's own "
            f"{COMMENT_PREFIX} -- RERUN: header, whose file holds the "
            f"ungrouped path:line grammar this parser does not read: {text!r}"
        )
    if not text.startswith(FILE_URI_SCHEME):
        raise RerunManifestError(
            f"{source}: line {number} does not begin with the required "
            f"{FILE_URI_SCHEME!r} scheme -- most likely behave's ungrouped "
            f"path{LINE_SEPARATOR}line form rather than this manifest's: "
            f"{text!r}"
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
            f"{source}: line {number} carries no scenario line number: {text!r}"
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
            :func:`build_rerun_lines` output directly, with no file involved.
        source: Label used in error messages -- a filename, normally.

    Returns:
        One entry per feature, in first-appearance order.  An empty iterable
        yields an empty list, which is the normal state of a run with no
        failures and never an error.

        Every path is **validated and normalised** by
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
        :func:`resolve_feature_path`.

    Raises:
        RerunManifestError: On the first line that carries data but is not a
            manifest entry -- a comment or behave-native header, a missing
            ``file:`` scheme, no trailing line number, or a path outside the
            grammar -- on a non-string element, and on a ``lines`` argument
            that is not an iterable of text at all.  Never
            :class:`ValueError`, never :class:`TypeError`, and never
            :func:`sys.exit`: the whole module presents one error type so that
            ``app/cli.py`` can report the AAP 0.4.1 *"missing or malformed
            rerun manifest"* condition without classifying an exception.
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
        if not isinstance(raw, str):
            raise RerunManifestError(
                f"{label}: line {number} is {type(raw).__name__}, not text"
            )
        entry = _parse_manifest_line(raw, number, label)
        if entry is not None:
            parsed.append(entry)
    return _merge_entries(parsed)


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

    Returns:
        The entries whose feature file is a regular, non-symlinked file
        directly inside :func:`~app.utils.paths.features_dir`, in the order
        given.
    """
    kept: list[RerunEntry] = []
    for entry in entries:
        if resolve_feature_path(entry.path, base=base) is None:
            logger.warning(
                "Dropping the rerun entry %r from %r: it does not name a "
                "feature file inside the %s directory, so its %d location(s) "
                "are not executable",
                entry.path,
                source,
                FEATURES_DIR_NAME,
                len(entry.lines),
            )
            continue
        kept.append(entry)
    return kept


def parse_rerun_file(
    source: Path | str | Iterable[str] | None = None,
    base: Path | str | None = None,
    *,
    confine: bool | None = None,
) -> list[RerunEntry]:
    """Read a rerun manifest and return its entries.

    The reading half of the round trip AAP 0.6 requires -- *"the file the writer
    produces must select exactly the scenarios that failed"* -- and the single
    owner of the grammar, so ``app/cli.py``'s ``--rerun`` parses nothing itself.

    **This is where the confinement is enforced, not merely offered.**  In the
    production mode -- ``source`` left ``None``, which is how
    ``app/services/test_run_service.py`` calls it -- both tiers run: every
    entry has passed :func:`validate_feature_path` *and*
    :func:`resolve_feature_path`, so a caller can append the resulting
    ``path:line`` locations to the engine's argv without a check of its own
    (CWE-22).  An entry that fails the second tier is dropped with a
    diagnostic on stderr rather than returned or raised on.

    Args:
        source: What to read.  ``None`` -- the production case -- resolves
            :func:`~app.utils.paths.rerun_txt_path`, the port of the manifest
            ``FailedTestRunner.java:11`` declares as its feature source.  A
            :class:`~pathlib.Path`, a string or any :class:`os.PathLike` is
            read as a file.  Any other iterable is treated as lines already in
            hand, so ``parse_rerun_file(build_rerun_lines(result_set))``
            round-trips a document without going through disk.
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
        One entry per feature, in file order.  An **empty file yields an empty
        list**: a run with no failures writes zero bytes, and reading that back
        is a normal state rather than an error.  With the filesystem tier
        applied, a file whose every entry is unresolvable yields an empty list
        too, each drop reported on stderr.

    Raises:
        RerunManifestError: If the file is absent or unreadable, if its own
            path is unusable, if it is not valid UTF-8, or if any line carries
            data but is not a manifest entry.  One error type covers every
            case on purpose: per the AAP 0.4.1 exit table *"a missing or
            malformed rerun manifest"* still exits ``0`` with the problem
            reported on stderr, so the caller needs to recognise the
            condition, not classify it.  The cause is always chained.

            The *path* is part of that contract, not just the contents.
            Constructing a :class:`~pathlib.Path` and reading it can raise
            :class:`TypeError` for a non-path object and :class:`ValueError`
            for one carrying an embedded NUL byte -- neither an
            :class:`OSError` -- so the resolution and the read are guarded
            together.  :class:`UnicodeDecodeError` is a :class:`ValueError`
            subclass and keeps its own clause *first*, so an encoding problem
            still reports as one.
    """
    if source is None or isinstance(source, (str, Path, os.PathLike)):
        path: Path | None = None
        try:
            path = rerun_txt_path(base) if source is None else Path(source)
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            raise RerunManifestError(
                f"{path}: rerun manifest is not valid UTF-8 ({error})"
            ) from error
        except OSError as error:
            raise RerunManifestError(
                f"{path}: rerun manifest cannot be read ({error})"
            ) from error
        except (TypeError, ValueError) as error:
            # ``path`` is still None when the failure was in constructing it,
            # so the offending input is reported instead, and with ``%r`` --
            # an unusable path is precisely the kind that carries a control
            # character (CWE-117).
            location = repr(str(path)) if path is not None else repr(source)
            raise RerunManifestError(
                f"{location}: rerun manifest path is unusable ({error})"
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
    file is expanded back into one location per failing scenario, in feature
    order and ascending line order within a feature.

    Recall the couplings this module's docstring records, all of them consequences
    of what ``FailedTestRunner.java:9-12`` omits: passing these locations
    **clears the default tag filter**, ``--rerun --tags`` is a usage error,
    the rerun **writes no artifacts**, and it **must not clean**, since
    ``--clean`` would delete the manifest being read.

    Args:
        source: As :func:`parse_rerun_file`.
        base: As :func:`parse_rerun_file`.
        confine: As :func:`parse_rerun_file` -- both tiers by default in the
            production mode, so these locations are executable as they stand.

    Returns:
        One ``"<path>:<line>"`` string per failing scenario; an empty list when
        the manifest is empty or when no entry survived the confinement.
        Every path is one :func:`validate_feature_path` accepted, and in the
        production mode one :func:`resolve_feature_path` accepted too, so each
        location names a regular Gherkin file inside the features directory
        and nothing else -- the property that makes these strings safe to
        append to an engine command line (CWE-22).

    Raises:
        RerunManifestError: As :func:`parse_rerun_file`.
    """
    return [
        location
        for entry in parse_rerun_file(source, base, confine=confine)
        for location in entry.locations
    ]
