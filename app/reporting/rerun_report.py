"""The grouped rerun manifest -- ``target/rerun.txt``.

This is a small file with a load-bearing format.  ``FailedTestRunner.java:11``
declares ``features = "@target/rerun.txt"``, so the manifest is **machine input
to a runner**, not a human convenience log: whatever this module writes is what
a rerun executes, and a format error here silently changes which scenarios are
retried.  Source anchor: the reference ``target/rerun.txt``, whose AAP 0.4.1 row
reads *"Writes one ``file:<path>:<line>[:<line>...]`` line per feature"*.

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
* **No header, no comment line and no blank line is ever emitted.**
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

* A scenario is failing when its rolled-up status is ``failed``.  Elements in
  the internal schema carry no status of their own, so the roll-up is over the
  scenario's steps, the steps of the Background occurrence **immediately
  preceding** it, and its ``after`` hook entries -- the JVM's rerun formatter
  keys on the test-case result, which subsumes hooks.
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
a bare :class:`ValueError` or :class:`OSError` escape into a traceback.

Boundaries and determinism
--------------------------
* Only :mod:`app.reporting.events` and :mod:`app.utils.paths` are imported.  No
  service (the dependency edge runs ``SV --> RP``, never back), no Flask, no
  Selenium, no :mod:`app.config`.
* **No path literal appears here**: :func:`~app.utils.paths.rerun_txt_path` and
  :func:`~app.utils.paths.ensure_parent` own the location.
* Pure is split from impure.  :func:`build_rerun_lines` and
  :func:`build_rerun_text` read no clock, no working directory and no
  filesystem; :func:`write_rerun_txt` is the thin wrapper that touches disk.
* Ordering is *structural* determinism (AAP 0.6): features in source order,
  line numbers ascending.  ``sortingMethod: 'ALPHABETICAL'`` in ``Jenkins:15``
  is a publisher **display** option and imposes nothing on an artifact -- the
  features are deliberately **not** sorted alphabetically on its account.
* **Nothing here raises on a test outcome.**  ``pom.xml:25`` sets
  ``testFailureIgnore=true`` and all six ``Jenkins:15`` thresholds are ``-1``;
  failures are data.  A structurally unusable scenario is logged and skipped,
  not raised on.  Only a genuine I/O fault propagates, becoming the command's
  writer-failure exit class.
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
    FILE_URI_SCHEME,
    ensure_parent,
    normalize_feature_uri,
    rerun_txt_path,
)

__all__ = [
    "COMMENT_PREFIX",
    "FAILED_STATUS",
    "LINE_ENDING",
    "LINE_SEPARATOR",
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
]

#: Module logger.  Deliberately without a handler of its own, matching
#: :mod:`app.reporting.events`: ``app/logging_config.py`` installs the split
#: that routes WARNING-and-above to stderr, which is where the AAP 0.4.1 exit
#: table expects a manifest problem to be reported, and Python's ``lastResort``
#: handler covers a bare import in a test.  A ``NullHandler`` here would
#: silence both routes.
logger = logging.getLogger(__name__)

#: The one status that puts a scenario in the manifest.  behave's own status
#: name, and the JVM's; :mod:`app.reporting.events` normalises to it.
FAILED_STATUS: Final[str] = "failed"

#: Separator between the path and each line number, and between line numbers.
#: It is also the character inside :data:`~app.utils.paths.FILE_URI_SCHEME`,
#: which is the parsing hazard :func:`parse_rerun_lines` is built around.
LINE_SEPARATOR: Final[str] = ":"

#: Line terminator.  LF unconditionally, on every platform -- the manifest is
#: read back by a runner, not by a text editor.
LINE_ENDING: Final[str] = "\n"

#: Comment marker.  This writer never emits one; the parser tolerates one on
#: input so that a hand-annotated or behave-generated file can still be read.
COMMENT_PREFIX: Final[str] = "#"


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
            scheme -- the form that ``app/cli.py`` can hand to behave and that
            :func:`~app.utils.paths.normalize_feature_uri` has already
            normalised on the way out.
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


def _status_of(node: JsonDict) -> str:
    """Return the status recorded on a step or hook entry.

    Args:
        node: A step object or an ``after`` hook entry, either of which carries
            its outcome under ``result.status``.

    Returns:
        The status string, or ``""`` when the node carries no usable result.
        A missing result is never treated as a failure: the manifest exists to
        re-select scenarios that demonstrably failed, and inventing a failure
        from absent data would retry a scenario that never ran.
    """
    result = node.get("result")
    if not isinstance(result, dict):
        return ""
    status = result.get("status")
    return status if isinstance(status, str) else ""


def _has_failure(node: JsonDict) -> bool:
    """Report whether an element's steps or hooks record a failure.

    Args:
        node: A Background occurrence or a scenario element.

    Returns:
        ``True`` if any of its steps, or any of its ``after`` hook entries,
        carries the ``failed`` status.  Hooks take part because the JVM's rerun
        formatter keys on the test-case result, which subsumes them; a
        Background occurrence has no ``after`` list, so the second loop is
        simply empty for one.
    """
    for group in ("steps", "after"):
        for item in node.get(group) or ():
            if isinstance(item, dict) and _status_of(item) == FAILED_STATUS:
                return True
    return False


def _feature_path(feature: JsonDict) -> str:
    """Return the repository-relative path a feature's manifest line names.

    ``path`` is read in preference to ``uri`` because
    :mod:`app.reporting.events` carries both *"so that no consumer performs
    string surgery on the other"*.  The ``uri`` fallback exists only for a
    hand-built fixture that omitted ``path``, and it removes the scheme through
    :data:`~app.utils.paths.FILE_URI_SCHEME` rather than by slicing a literal.

    The result is passed through
    :func:`~app.utils.paths.normalize_feature_uri`, which is idempotent, so a
    path already in the port's ``features/`` form is returned untouched while a
    legacy ``src/main/resources/features/`` path -- from a golden fixture, or a
    stale worker file -- is rewritten.  That matters beyond tidiness: the
    manifest is machine input, and a legacy path is one no runner in this
    repository layout could resolve.

    Args:
        feature: A feature object from the internal result set.

    Returns:
        The normalised path, or ``""`` when the feature carries neither a
        ``path`` nor a ``uri``.
    """
    candidate = feature.get("path")
    if not isinstance(candidate, str) or not candidate:
        uri = feature.get("uri")
        if not isinstance(uri, str) or not uri:
            return ""
        candidate = (
            uri[len(FILE_URI_SCHEME) :] if uri.startswith(FILE_URI_SCHEME) else uri
        )
    return normalize_feature_uri(candidate)


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

    Args:
        result_set: The merged internal result document from
            :mod:`app.reporting.events`.

    Returns:
        One string per feature that has at least one failing scenario, each of
        the form ``file:<path>:<line>[:<line>...]`` and carrying no line
        terminator.  An empty list when nothing failed -- which
        :func:`build_rerun_text` turns into a zero-byte file, not into a
        comment.

    A feature whose path cannot be determined, or a failing scenario whose line
    is not a positive integer, is logged at warning level and omitted: the
    document is structurally unusable at that point, and a location a runner
    could not resolve would be worse than a short manifest.  Neither condition
    raises, because neither is an I/O fault and *"failures are data"*.
    """
    grouped: dict[str, list[int]] = {}
    for feature, element in iter_failed_scenarios(result_set):
        path = _feature_path(feature)
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
        FILE_URI_SCHEME
        + path
        + "".join(f"{LINE_SEPARATOR}{line}" for line in sorted(set(lines)))
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
    """Write ``target/rerun.txt`` and return the path written.

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
            working directory, matching how Maven resolved ``target/``.
            Ignored when ``path`` is given.

    Returns:
        The path written, so a caller can log or serve it.

    Raises:
        OSError: If the parent directory cannot be created or the file cannot
            be written.  This is the one failure that propagates: a genuine I/O
            fault is the command's writer-failure exit class, whereas a test
            outcome never reaches this function as an exception.
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
    """
    if not text or not text.isascii() or not text.isdigit():
        return None
    value = int(text)
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
    which costs nothing to survive.

    Args:
        raw: The line as read, with or without its terminator.
        number: The line's one-based position, for the error message.
        source: Where the line came from, for the error message.

    Returns:
        The entry, or ``None`` for a line that carries no data: an empty or
        whitespace-only line, or a comment.  Neither is emitted by this
        module's writer -- it emits no blank line and no comment at all -- but
        tolerating both on input means a hand-annotated file, or one produced
        by behave's own rerun formatter with its ``# -- RERUN:`` header, can
        still be read.

    Raises:
        RerunManifestError: If the line carries data but is not a manifest
            entry -- no trailing line number, or an empty path.  The message
            names the source, the position and the offending text, because
            that is what ``app/cli.py`` reports on stderr before exiting ``0``.
    """
    text = raw.strip()
    if not text or text.startswith(COMMENT_PREFIX):
        return None

    body = (
        text[len(FILE_URI_SCHEME) :] if text.startswith(FILE_URI_SCHEME) else text
    )
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
    if not path:
        raise RerunManifestError(
            f"{source}: line {number} carries no feature path: {text!r}"
        )
    return RerunEntry(path=path, lines=tuple(sorted(set(lines))))


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

        The paths are returned **exactly as the file spells them**: this parser
        performs no prefix normalisation, because the manifest it reads in
        production is the one :func:`write_rerun_txt` produced and already
        carries the port's ``features/`` prefix.  A caller reading a legacy
        manifest normalises with
        :func:`~app.utils.paths.normalize_feature_uri`, the single owner of
        that substitution.

    Raises:
        RerunManifestError: On the first line that carries data but is not a
            manifest entry, and on a non-string element.  Never
            :class:`ValueError`, and never :func:`sys.exit`.
    """
    label = source or "<rerun manifest>"
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


def parse_rerun_file(
    source: Path | str | Iterable[str] | None = None,
    base: Path | str | None = None,
) -> list[RerunEntry]:
    """Read a rerun manifest and return its entries.

    The reading half of the round trip AAP 0.6 requires -- *"the file the writer
    produces must select exactly the scenarios that failed"* -- and the single
    owner of the grammar, so ``app/cli.py``'s ``--rerun`` parses nothing itself.

    Args:
        source: What to read.  ``None`` -- the production case -- resolves
            :func:`~app.utils.paths.rerun_txt_path`, which is the port of
            ``FailedTestRunner``'s ``features = "@target/rerun.txt"``.  A
            :class:`~pathlib.Path`, a string or any :class:`os.PathLike` is
            read as a file.  Any other iterable is treated as lines already in
            hand, so ``parse_rerun_file(build_rerun_lines(result_set))``
            round-trips a document without going through disk.
        base: Directory the default path hangs off; ignored unless ``source``
            is ``None``.

    Returns:
        One entry per feature, in file order.  An **empty file yields an empty
        list**: a run with no failures writes zero bytes, and reading that back
        is a normal state rather than an error.

    Raises:
        RerunManifestError: If the file is absent or unreadable, if it is not
            valid UTF-8, or if any line carries data but is not a manifest
            entry.  One error type covers every case on purpose: per the AAP
            0.4.1 exit table *"a missing or malformed rerun manifest"* still
            exits ``0`` with the problem reported on stderr, so the caller
            needs to recognise the condition, not classify it.  The cause is
            always chained.
    """
    if source is None or isinstance(source, (str, Path, os.PathLike)):
        path = rerun_txt_path(base) if source is None else Path(source)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as error:
            raise RerunManifestError(
                f"{path}: rerun manifest cannot be read ({error})"
            ) from error
        except UnicodeDecodeError as error:
            raise RerunManifestError(
                f"{path}: rerun manifest is not valid UTF-8 ({error})"
            ) from error
        return parse_rerun_text(text, source=str(path))
    return parse_rerun_lines(source)


def rerun_locations(
    source: Path | str | Iterable[str] | None = None,
    base: Path | str | None = None,
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

    Returns:
        One ``"<path>:<line>"`` string per failing scenario; an empty list when
        the manifest is empty.

    Raises:
        RerunManifestError: As :func:`parse_rerun_file`.
    """
    return [
        location
        for entry in parse_rerun_file(source, base)
        for location in entry.locations
    ]
