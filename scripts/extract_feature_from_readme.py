#!/usr/bin/env python3
"""Regenerate ``tests/features/login.feature`` byte-exactly from ``README.md``.

``README.md`` embeds this project's only executable specification as a fenced
Gherkin block. That block is reproduced verbatim as ``tests/features/login.feature``,
and this script is what reproduces it -- so the copy is *reproducible* rather than a
one-off, and ``README.md`` remains the single source of truth. Run it with no
arguments and the feature file is regenerated; run it with ``--check`` and the
feature file is audited without being touched.

The script is deliberately, aggressively dumb. It copies bytes. It does not parse
Gherkin, validate it, reformat it, pretty-print it, annotate it, re-indent it or
repair it, because every one of those would change the bytes -- and every changed
byte breaks the one guarantee this file exists to provide. Its only real failure
mode would be being helpful.

The source range
================
The block occupies ``[README.md:L104-L148]`` -- 45 lines, 1-based and inclusive --
recorded below as :data:`CANONICAL_FIRST_LINE_NUMBER` and
:data:`CANONICAL_LAST_LINE_NUMBER`. Those two numbers are the documented contract
and ``.gitattributes`` cites the very same range.

The prose *around* the block is free to move, though, and it has: ``README.md``
was itself rewritten by this migration, which pushed the block further down the
file without altering a single byte of it. So the script also *locates* the block
by content -- the fenced region whose first line is ``@Login`` -- and reconciles
the two answers out loud instead of silently trusting either one. The frozen byte
contract below is what arbitrates between them. See :func:`extract_feature_block`.

The byte contract
=================
The extracted block, and therefore the written file, is exactly:

* **1864 bytes** (:data:`EXPECTED_BYTE_COUNT`)
* **45 lines** (:data:`EXPECTED_LINE_COUNT`)
* **sha256** ``112d90560569d3011bb8a2c32d9acbc65d346be8f70ad7c182105b934079504c``
  (:data:`EXPECTED_SHA256`)
* first line ``@Login``, last line ``      |posmanager6@info.com   |posmanager|``
* terminated by a single LF, with **zero** CR bytes anywhere
* **pure ASCII** -- zero bytes above 127

The purity is not luck that can be relied on: the repository's only two non-ASCII
characters are the two EN DASH characters in the report commands further down
``README.md``, comfortably outside this range. They could move. Every text stream
opened below therefore states ``encoding="utf-8"`` explicitly and never leans on
the platform locale.

Those constants are module-level and importable on purpose, so that
``tests/parity/test_feature_file_byte_parity.py`` -- which asserts validation
criterion **V1**, "the bytes of ``tests/features/login.feature`` equal the bytes of
``[README.md:L104-L148]`` exactly" -- can assert against these values rather than
restating the literals and drifting from them.

The fragile bytes
=================
Three of the 45 lines contain nothing but spaces, and they are the most fragile
bytes in the whole repository:

===============  ==================  ==============================
``README.md``    width               index in the written file
===============  ==================  ==============================
L121             2 spaces            17
L129             4 spaces            25
L143             6 spaces            39
===============  ==================  ==============================

A formatter, an editor's "trim trailing whitespace" setting, or one stray
whitespace-trimming call silently deletes them, and deleting any one of them
changes the byte count, breaks the digest and fails criterion V1. Nothing in this
file trims, normalises, re-indents, reflows or collapses anything -- there is no
whitespace-trimming call here at all -- and the three widths are asserted exactly
rather than tested for "is this line blank", because an emptiness test would
happily accept a line of the wrong width. ``pyproject.toml`` keeps the linter and
the formatter out of ``tests/features/`` for the same reason.

Reading and writing, precisely
==============================
Lines are sliced with ``splitlines(keepends=True)`` so every line keeps its own
terminator: none is invented and none is dropped, which is what preserves the
trailing-newline state exactly. The payload is then written with
``Path.write_bytes``, so no newline translation can occur -- a default text-mode
write on a platform whose native terminator is CRLF would rewrite all 45
terminators and add 45 bytes. No byte-order mark is added, and the trailing
newline is neither added nor removed.

Related files
=============
* ``tests/features/login.feature`` -- the file written here; the destination.
* ``tests/parity/test_feature_file_byte_parity.py`` -- criterion V1's assertion.
* ``docs/migration-parity.md`` -- the authoritative register of every preserved
  defect (D1 through D9), including the fixes deliberately *not* applied.
* ``.gitattributes`` -- pins ``*.feature text eol=lf``, the other half of the
  byte-stability guarantee.
* ``Makefile`` -- the ``feature`` recipe runs this script with no arguments.

Usage
=====
Run it with the interpreter this project pins, CPython 3.14.6. The virtual
environment and the container image both provide it, and the recipe below is
already wired up, so none of these needs a PYTHONPATH or an installed package::

    python3 scripts/extract_feature_from_readme.py             # regenerate
    python3 scripts/extract_feature_from_readme.py --check      # audit only
    make feature                                               # the wired-up recipe
"""

# ---------------------------------------------------------------------------
# INTENTIONALLY PRESERVED DEFECTS -- do not "fix" any of these here.
#
# The migration's governing rule is that defects are behaviour: the rewrite must
# match the behaviour and logic of the original, and the original's specification
# carries nine verifiable defects. Each is preserved as the default and recorded
# in `docs/migration-parity.md`, where the available one-line fixes are written
# down as decisions for the owners rather than applied. Fixing D1, D2, D4, D5 or
# D9 is explicitly out of scope.
#
# Because this script is a verbatim byte copier, preserving them costs nothing --
# it happens automatically, provided the script does nothing clever. They are
# catalogued here so that a future maintainer reading the produced feature file
# cannot mistake any of them for a copying bug, and so that nobody "improves"
# this script into breaking them:
#
#   D1  The `Examples` tables bind to the THIRD outline only. The first outline
#       (@UPGN-286) has four steps and zero Examples; the second (@UPGN-287) has
#       four steps and zero Examples; both tables attach to the third (@UPGN-288)
#       because Gherkin binds Examples to the immediately preceding outline.
#       Consequence: @UPGN-286 runs with the LITERAL placeholder text
#       "<username>" / "<password>" -- and passes. Do not rebind the tables.
#
#   D2  There is no `LogOut` tag. The six tags in the block are @Login at feature
#       level, @UPGN-286 / @UPGN-287 / @UPGN-288 at scenario level and
#       @SalesManager / @PosManager on the Examples tables. The documented
#       runner's tag expression selects `@LogOut`, so it selects zero scenarios.
#       Do not add the tag, rename a tag, or de-hyphenate @UPGN-286.
#
#   D4  The password value feeds the USERNAME field. README.md L133 reads
#       `When User enters "<password>" username`. The report the original system
#       published proves the consequence empirically: it records the values from
#       the password column, never the e-mail addresses from the username column.
#       Do not swap the columns and do not "correct" the placeholder.
#
#   D5  The explanatory comment is English and the assertion is French. L130
#       describes the expected text as "Please fill out this field"; L135 asserts
#       "Veuillez renseigner ce champ." -- 29 characters, 29 bytes, pure ASCII,
#       and its trailing period is significant. The assertion is the executable
#       truth. Do not translate it, do not reconcile it with the comment, and do
#       not normalise anything that would drop the period.
#
#   D8  The third outline's keyword is malformed: L132 is written
#       "Scenario Outline:Users ..." with no space after the colon. The opening
#       fence at L103 is also bare, carrying no language hint. Both are preserved;
#       the fences are excluded from the output, and neither is annotated.
#
#   D9  A duplicate scenario name silently destroys @UPGN-287's coverage. L124 and
#       L132 parse to the IDENTICAL scenario name, because Gherkin treats the
#       colon purely as a keyword separator, so D8's missing space affects the
#       keyword and not the name. The test framework derives the generated test
#       function's name from the scenario name, so the later definition overwrites
#       the earlier one and @UPGN-287 becomes unreachable. Collection yields
#       exactly six tests: one for @UPGN-286 plus five parametrisations of the
#       collided name -- three SalesManager rows and two PosManager rows. The
#       mechanism was confirmed by experiment, not inferred: an always-failing
#       assertion injected into @UPGN-287's last step never executed, and renaming
#       only that scenario in an otherwise byte-identical copy produced seven
#       tests, whereupon the injected failure fired. The one-line fix is recorded
#       in `docs/migration-parity.md` and is deliberately NOT applied.
#
# `tests/parity/test_defect_preservation.py` asserts D1, D2, D4, D5 and D9
# explicitly, which is what turns silent defects into auditable ones.
# ---------------------------------------------------------------------------

import argparse
import hashlib
import logging
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

# Diagnostics only. Logging is OBTAINED here and never CONFIGURED here: handlers,
# levels, formats and destinations are owned exclusively by `app/logging_config.py`,
# and nothing in this module installs, replaces or adjusts any of them. A
# library-style logger with no configuration of its own is silent by default,
# which is why the small number of human-facing lines this command-line tool
# prints go to stdout and stderr through the two helpers near the bottom instead.
_LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

PROGRAM_NAME: Final[str] = "extract-feature-from-readme"

FEATURE_CHARSET: Final[str] = "utf-8"
"""The character encoding the source document and the feature file are read and
written in.

Every I/O and codec call below states ``encoding="utf-8"`` **literally** rather
than reaching for this constant, matching the convention the rest of this project
follows. That is deliberate: the encoding of a call should be readable at the call
site itself -- by a human and by an audit that greps for it -- instead of hiding
behind a name. Relying on the platform default locale instead would be the real
hazard, and nothing here does. This constant exists so the diagnostics can name
the encoding in prose without re-deciding it.
"""

# A fenced code region in Markdown opens and closes with this marker. Used only to
# LOCATE the block; never written into the output, and neither fence line is ever
# part of the payload.
FENCE_MARKER: Final[str] = "```"

# Repository-relative default paths. Resolved against the repository root rather
# than the working directory (see `repository_root`) so the script behaves
# identically whether it is invoked from the root or from anywhere else.
README_RELATIVE_PATH: Final[str] = "README.md"
FEATURE_RELATIVE_PATH: Final[str] = "tests/features/login.feature"

# ---------------------------------------------------------------------------
# The source range, cited: `[README.md:L104-L148]`. 1-based and inclusive, which
# is how the migration plan, `.gitattributes` and the parity test all express it.
# Carried as explicit constants, not folded into a slice expression, so the
# numbers are auditable by inspection and importable by the parity test.
# ---------------------------------------------------------------------------
CANONICAL_FIRST_LINE_NUMBER: Final[int] = 104
CANONICAL_LAST_LINE_NUMBER: Final[int] = 148

# ---------------------------------------------------------------------------
# The frozen byte contract. Every value here was measured from the source file,
# and together they identify the payload uniquely: the digest alone would do it,
# and the rest exist so that a failure names WHICH property broke rather than
# just reporting a different hash.
# ---------------------------------------------------------------------------
EXPECTED_BYTE_COUNT: Final[int] = 1864
EXPECTED_SHA256: Final[str] = "112d90560569d3011bb8a2c32d9acbc65d346be8f70ad7c182105b934079504c"
EXPECTED_LINE_COUNT: Final[int] = 45
EXPECTED_FIRST_LINE: Final[str] = "@Login"
EXPECTED_LAST_LINE: Final[str] = "      |posmanager6@info.com   |posmanager|"

# The three space-only lines, as (index in the written file, exact width). Held as
# exact widths rather than an "is this line blank" predicate: an emptiness test
# would happily accept a line of the wrong width, which is precisely the corruption
# these entries exist to catch. Source lines L121, L129 and L143 respectively.
WHITESPACE_ONLY_LINE_INDEXES: Final[tuple[int, ...]] = (17, 25, 39)
WHITESPACE_ONLY_LINE_WIDTHS: Final[tuple[int, ...]] = (2, 4, 6)

# Process exit codes. Produced in exactly one place, at the bottom of the file.
EXIT_SUCCESS: Final[int] = 0
EXIT_FAILURE: Final[int] = 1


class FeatureBlockError(RuntimeError):
    """Raised when the Gherkin block cannot be extracted and proven correct.

    Carrying the failure as an exception, rather than exiting from wherever it was
    detected, is what keeps every helper in this module callable from a test: only
    :func:`main` turns an outcome into a process exit code. The message is written
    to be read by a human at a terminal -- it names the specific discrepancy, not
    merely that one occurred.
    """


@dataclass(frozen=True, slots=True)
class LineRange:
    """A 1-based, inclusive span of ``README.md`` lines.

    1-based and inclusive because that is how the migration plan,
    ``.gitattributes`` and the parity test all express the range, and translating
    between conventions at every use site is how off-by-one errors get in. The
    single translation to Python's 0-based, half-open slicing lives in
    :meth:`slice_of` and nowhere else.

    Frozen and hashable, so ranges can be compared and de-duplicated directly --
    which :func:`extract_feature_block` relies on when it reconciles the located
    range against the canonical one.

    Attributes:
        first: The first line of the span, 1-based and included.
        last: The last line of the span, 1-based and included.
    """

    first: int
    last: int

    @property
    def line_count(self) -> int:
        """How many lines the span covers, inclusive of both endpoints."""
        return self.last - self.first + 1

    @property
    def label(self) -> str:
        """The span rendered the way the sources cite it, e.g. ``L104-L148``."""
        return f"L{self.first}-L{self.last}"

    def is_within(self, total_lines: int) -> bool:
        """Report whether this span actually addresses lines of a file that long.

        Checked before slicing rather than after, because Python's slicing is
        forgiving: an out-of-range slice yields a short result or an empty one
        instead of raising, and a silently short payload is exactly the failure
        this module must never produce.

        Args:
            total_lines: The number of lines in the file being addressed.

        Returns:
            ``True`` when the whole span lies inside the file.
        """
        return 1 <= self.first <= self.last <= total_lines

    def slice_of(self, lines: Sequence[str]) -> Sequence[str]:
        """Return the lines this span covers, untouched.

        The one place 1-based inclusive bounds become a 0-based half-open slice.
        The elements are returned exactly as they were handed in: nothing is
        stripped, joined, re-terminated or copied per-character.

        Args:
            lines: The file's lines. Pass the ``keepends=True`` form to obtain a
                payload; pass the plain form only to inspect content.

        Returns:
            The covered sub-sequence, in order.
        """
        return lines[self.first - 1 : self.last]


# The documented source range as a single value, assembled from the two cited
# constants so neither number is ever written twice.
CANONICAL_BLOCK_RANGE: Final[LineRange] = LineRange(
    CANONICAL_FIRST_LINE_NUMBER, CANONICAL_LAST_LINE_NUMBER
)


@dataclass(frozen=True, slots=True)
class Extraction:
    """A block of Gherkin that has already been proven against the byte contract.

    Only :func:`extract_feature_block` constructs one, and it constructs one only
    after :func:`verify_payload` has returned no findings. So holding an instance
    of this class *is* the proof that the payload satisfies the contract, and no
    caller has to re-check before writing.

    Attributes:
        payload: The block's bytes, exactly as they appear in ``README.md``.
        block_range: Where the block was found. 1-based and inclusive.
        relocated: ``True`` when ``block_range`` is not the canonical documented
            range -- that is, when the surrounding prose moved the block. Callers
            use this to report the relocation rather than to change behaviour.
    """

    payload: bytes
    block_range: LineRange
    relocated: bool

    @property
    def digest(self) -> str:
        """The payload's hex sha256 digest."""
        return sha256_of(self.payload)


def repository_root() -> Path:
    """Return the repository root, derived from this file's own location.

    ``scripts/`` is deliberately not a package and deliberately not part of the
    installed distribution, so there is no import machinery to ask. Deriving the
    root from ``__file__`` rather than from the working directory is what lets the
    script be invoked as ``python3 scripts/extract_feature_from_readme.py`` from
    the root, from a recipe, or from anywhere else, with identical results.

    Returns:
        The absolute, symlink-resolved repository root.
    """
    return Path(__file__).resolve().parent.parent


def readme_path(root: Path | None = None) -> Path:
    """Return the default path of the source document.

    Args:
        root: Repository root to resolve against. Defaults to
            :func:`repository_root`.

    Returns:
        The absolute path of ``README.md``.
    """
    return (root if root is not None else repository_root()) / README_RELATIVE_PATH


def feature_path(root: Path | None = None) -> Path:
    """Return the default path of the generated feature file.

    Args:
        root: Repository root to resolve against. Defaults to
            :func:`repository_root`.

    Returns:
        The absolute path of ``tests/features/login.feature``.
    """
    return (root if root is not None else repository_root()) / FEATURE_RELATIVE_PATH


def sha256_of(data: bytes) -> str:
    """Return the hex sha256 digest of ``data``.

    Args:
        data: The bytes to digest.

    Returns:
        The lower-case hexadecimal digest.
    """
    return hashlib.sha256(data).hexdigest()


def first_difference_index(actual: bytes, expected: bytes) -> int | None:
    """Return the index of the first byte at which two payloads diverge.

    A digest tells a reader that something changed; this tells them *where*, which
    is the difference between a usable failure message and a puzzle. When one
    payload is a prefix of the other, the divergence is reported at the point the
    shorter one ends.

    Args:
        actual: The payload observed.
        expected: The payload required.

    Returns:
        The 0-based index of the first differing byte, or ``None`` when the two
        payloads are identical.
    """
    if actual == expected:
        return None
    shared = min(len(actual), len(expected))
    for index in range(shared):
        if actual[index] != expected[index]:
            return index
    # No byte in the overlap differs, so the payloads differ only in length and the
    # divergence begins exactly where the shorter one stops.
    return shared


def verify_payload(data: bytes) -> tuple[str, ...]:
    """Check a payload against the frozen byte contract.

    Every property is checked independently and every failure is collected, so one
    run reports the complete picture instead of only the first thing noticed. The
    checks are ordered from coarse to fine -- size, digest, shape, then the three
    fragile lines -- because that is the order a human debugs in.

    This function never raises for a bad payload and never writes anything. It is
    the single definition of "correct" in this module, used to validate what was
    read out of the source, to validate what was written to disk afterwards, and to
    audit an existing file in ``--check`` mode.

    Args:
        data: The candidate payload.

    Returns:
        A tuple of human-readable findings, one per violated property. An empty
        tuple means the payload satisfies the contract in full.
    """
    findings: list[str] = []

    if len(data) != EXPECTED_BYTE_COUNT:
        findings.append(f"byte count is {len(data)}, expected {EXPECTED_BYTE_COUNT}")

    digest = sha256_of(data)
    if digest != EXPECTED_SHA256:
        findings.append(f"sha256 is {digest}, expected {EXPECTED_SHA256}")

    carriage_returns = data.count(b"\r")
    if carriage_returns:
        findings.append(f"payload contains {carriage_returns} CR byte(s), expected none")

    non_ascii = sum(1 for byte in data if byte > 127)
    if non_ascii:
        findings.append(f"payload contains {non_ascii} non-ASCII byte(s), expected none")

    if not data.endswith(b"\n"):
        findings.append("payload does not end with a single LF")

    # Decoding cannot be assumed to succeed: a corrupted file is exactly the case
    # this function exists to diagnose, so a decode failure is reported as a
    # finding rather than allowed to escape as an unhandled exception.
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        findings.append(f"payload is not valid {FEATURE_CHARSET}: {error}")
        return tuple(findings)

    lines = text.splitlines()
    if len(lines) != EXPECTED_LINE_COUNT:
        findings.append(f"line count is {len(lines)}, expected {EXPECTED_LINE_COUNT}")

    if not lines:
        findings.append("payload is empty")
        return tuple(findings)

    if lines[0] != EXPECTED_FIRST_LINE:
        findings.append(f"first line is {lines[0]!r}, expected {EXPECTED_FIRST_LINE!r}")

    if lines[-1] != EXPECTED_LAST_LINE:
        findings.append(f"last line is {lines[-1]!r}, expected {EXPECTED_LAST_LINE!r}")

    # THE FRAGILE BYTES. Each of the three space-only lines is asserted at its
    # exact width, so a trimmed line, a widened line and a deleted line are all
    # caught -- and each is named individually, because "the digest changed" would
    # not tell a maintainer that a whitespace fixer had run over the file.
    for index, width in zip(WHITESPACE_ONLY_LINE_INDEXES, WHITESPACE_ONLY_LINE_WIDTHS, strict=True):
        if index >= len(lines):
            findings.append(
                f"line index {index} is missing; it must hold {width} space(s) and nothing else"
            )
            continue
        if lines[index] != " " * width:
            findings.append(
                f"line index {index} is {lines[index]!r}, expected exactly "
                f"{width} space(s) -- this line is whitespace-only by design and "
                "must not be trimmed"
            )

    return tuple(findings)


def read_readme(path: Path) -> str:
    """Read the source document as text, with the encoding stated explicitly.

    Universal newline handling is left switched ON here, deliberately, and it is
    the one place in this module where newlines are allowed to be touched at all.
    A checkout that somehow carried CRLF terminators is normalised to LF on the way
    in, so the payload written out still satisfies the contract instead of gaining
    45 stray CR bytes. Opening this stream with ``newline=""`` would preserve those
    terminators and turn a recoverable checkout quirk into a hard failure. The
    output side is the opposite and must stay that way: see
    :func:`write_feature_file`, which writes bytes so that nothing can be
    translated on the way out.

    Args:
        path: The document to read.

    Returns:
        The document's full text.

    Raises:
        FeatureBlockError: If the file is missing, unreadable, or not valid UTF-8.
    """
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise FeatureBlockError(f"source document not found: {path}") from error
    except UnicodeDecodeError as error:
        message = f"source document is not valid {FEATURE_CHARSET}: {path}: {error}"
        raise FeatureBlockError(message) from error
    except OSError as error:
        raise FeatureBlockError(f"source document could not be read: {path}: {error}") from error


def locate_feature_blocks(lines: Sequence[str]) -> tuple[LineRange, ...]:
    """Find every fenced region whose first line is the feature's opening tag.

    A defensive locator, and the reason this script keeps working after the prose
    around the block moves. It walks fenced regions pairwise and matches on the
    region's first line being exactly the feature tag, which is a property of the
    Gherkin itself rather than of its position in the document.

    Deliberately not a Markdown parser and deliberately not a Gherkin parser: it
    recognises a fence by prefix and nothing more. It also does not choose between
    multiple matches -- it returns all of them and lets the byte contract arbitrate,
    because a digest is a far better discriminator than a heuristic.

    Nothing here can affect the payload. Matching runs over the plain,
    terminator-free view of the file while extraction runs over the ``keepends``
    view; the two views are always the same length, so an index established here is
    valid there.

    Args:
        lines: The document's lines WITHOUT terminators, i.e. ``splitlines()``.

    Returns:
        Every matching region as a 1-based inclusive :class:`LineRange`, in the
        order encountered. Empty when the document holds no such region.
    """
    matches: list[LineRange] = []
    total = len(lines)
    index = 0

    while index < total:
        if not lines[index].startswith(FENCE_MARKER):
            index += 1
            continue

        # An opening fence. Walk forward to its closing partner.
        closing = index + 1
        while closing < total and not lines[closing].startswith(FENCE_MARKER):
            closing += 1
        if closing >= total:
            # Unterminated fence: there is no closing partner, so there is no
            # region to delimit and nothing further in the file can be one.
            break

        first_content = index + 1
        last_content = closing - 1
        if first_content <= last_content and lines[first_content] == EXPECTED_FIRST_LINE:
            matches.append(LineRange(first_content + 1, last_content + 1))

        # Resume after the closing fence, so a fence is never read as both the
        # close of one region and the open of the next.
        index = closing + 1

    return tuple(matches)


def extract_line_range(lines: Sequence[str], block_range: LineRange) -> bytes:
    """Join the lines a range covers and encode them, changing nothing.

    The entire payload path of this module, in one expression. Each line still
    carries its own terminator, so joining reproduces the source bytes exactly:
    no terminator is invented, none is dropped, and the trailing-newline state is
    whatever the source's last covered line said it was.

    Args:
        lines: The document's lines WITH terminators, i.e.
            ``splitlines(keepends=True)``.
        block_range: The span to extract.

    Returns:
        The covered lines as UTF-8 bytes.
    """
    return "".join(block_range.slice_of(lines)).encode("utf-8")


def extract_feature_block(
    readme_text: str,
    *,
    require_canonical_range: bool = False,
) -> Extraction:
    """Extract the Gherkin block and prove it against the byte contract.

    Two independent answers to "where is the block?" are reconciled here, and the
    reconciliation is the interesting part of this module:

    1. **The documented range**, ``[README.md:L104-L148]``. This is the contract
       the migration plan and ``.gitattributes`` both cite, so it is tried first.
    2. **The located range**, found by :func:`locate_feature_blocks`. The prose
       around the block is free to move -- and did, when ``README.md`` was itself
       rewritten by this migration -- which shifts the block's line numbers without
       altering one byte of it.

    Neither answer is silently preferred. When they disagree, the disagreement is
    reported out loud, on stderr and to the logger, naming both ranges; then the
    frozen byte contract decides, because a payload whose digest is
    :data:`EXPECTED_SHA256` is the right payload no matter which line it started on.
    A candidate that fails the contract is never returned, so this function either
    yields a proven payload or raises.

    Args:
        readme_text: The full text of the source document.
        require_canonical_range: When ``True``, only the documented range is
            considered. Use it to demand that the block sit exactly where the
            contract says it does and to have a human reconcile the difference
            otherwise -- for instance from a stricter continuous-integration gate.

    Returns:
        The proven :class:`Extraction`.

    Raises:
        FeatureBlockError: If no considered range yields a payload that satisfies
            the byte contract. The message names every candidate tried and the
            specific findings for each.
    """
    keepends_lines = readme_text.splitlines(keepends=True)
    plain_lines = readme_text.splitlines()
    total = len(keepends_lines)

    located = locate_feature_blocks(plain_lines)
    candidates: list[LineRange] = [CANONICAL_BLOCK_RANGE]

    if require_canonical_range:
        _LOGGER.debug(
            "Restricted to the documented range %s by request.", CANONICAL_BLOCK_RANGE.label
        )
    elif not located:
        # Not fatal by itself: the documented range may still be correct, and it is
        # about to be checked against the contract like any other candidate.
        _LOGGER.warning(
            "No fenced region beginning %r was found; falling back to the "
            "documented range %s alone.",
            EXPECTED_FIRST_LINE,
            CANONICAL_BLOCK_RANGE.label,
        )
    else:
        candidates.extend(found for found in located if found != CANONICAL_BLOCK_RANGE)
        if CANONICAL_BLOCK_RANGE not in located:
            _report_relocation(located)

    attempts: list[str] = []
    for candidate in candidates:
        if not candidate.is_within(total):
            attempts.append(f"{candidate.label}: outside the document, which holds {total} line(s)")
            continue

        payload = extract_line_range(keepends_lines, candidate)
        findings = verify_payload(payload)
        if not findings:
            relocated = candidate != CANONICAL_BLOCK_RANGE
            if relocated:
                _emit(
                    f"Using the located range {candidate.label}; its bytes satisfy the "
                    "contract exactly."
                )
                _LOGGER.info("Extracted the feature block from %s.", candidate.label)
            else:
                _LOGGER.debug(
                    "Extracted the feature block from the documented range %s.",
                    candidate.label,
                )
            return Extraction(payload=payload, block_range=candidate, relocated=relocated)

        attempts.append(f"{candidate.label}: " + "; ".join(findings))

    raise FeatureBlockError(
        "no candidate range yielded the documented Gherkin block. "
        + " | ".join(attempts)
        + f" | expected {EXPECTED_BYTE_COUNT} bytes with sha256 {EXPECTED_SHA256}. "
        "The source document has changed in a way this script cannot reconcile; "
        "review it against docs/migration-parity.md before regenerating."
    )


def _report_relocation(located: Sequence[LineRange]) -> None:
    """Announce, loudly, that the block is not where the contract documents it.

    Called only when the located range or ranges all differ from the documented
    one. Reported on stderr as well as to the logger, because a library-style
    logger with no configuration is silent by default and this fact must never be
    the one thing a reader misses.

    This is not treated as an error. ``README.md`` is rewritten by this migration
    and the rewrite moved the block without changing it, so a shifted range is the
    expected steady state; the byte contract is what actually guards the content.

    Args:
        located: The ranges the content locator found. Never empty.
    """
    found = ", ".join(candidate.label for candidate in located)
    message = (
        f"the Gherkin block is not at the documented range {CANONICAL_BLOCK_RANGE.label}; "
        f"the content locator found it at {found}. README.md is rewritten by this "
        "migration, so a shifted range is expected -- the byte contract, not the line "
        "numbers, is what guarantees the content. Pass --require-canonical-range to "
        "treat this as an error instead."
    )
    _emit_error(message)
    _LOGGER.warning(
        "Feature block relocated: documented %s, located %s.",
        CANONICAL_BLOCK_RANGE.label,
        found,
    )


def write_feature_file(path: Path, payload: bytes) -> None:
    """Write the payload, creating the parent directory, translating nothing.

    Written as bytes rather than as text on purpose. A text-mode write applies the
    platform's newline translation, which on a platform whose native terminator is
    CRLF would rewrite all 45 terminators and add 45 bytes -- byte parity gone,
    silently, on that platform only. Writing bytes cannot do that, adds no
    byte-order mark, and neither adds nor removes a trailing newline. It is also
    why this call site needs no ``encoding`` argument: the payload was encoded once,
    where it was extracted.

    The parent directory is created if absent, which is the ordinary case in a
    fresh checkout, and creating it is idempotent and race-safe. Nothing else on
    the filesystem is created, removed or otherwise touched.

    Args:
        path: Destination file. Its parent is created if it does not exist.
        payload: The exact bytes to write.

    Raises:
        FeatureBlockError: If the directory cannot be created or the file cannot be
            written.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise FeatureBlockError(
            f"could not create the destination directory {path.parent}: {error}"
        ) from error

    try:
        path.write_bytes(payload)
    except OSError as error:
        raise FeatureBlockError(f"could not write {path}: {error}") from error


def read_written_bytes(path: Path) -> bytes:
    """Read a generated file back as raw bytes.

    Raw bytes, never text, because the point of reading it back is to compare it
    byte for byte. Decoding first would apply universal newline handling and hide
    precisely the corruption this read exists to detect.

    Args:
        path: The file to read.

    Returns:
        The file's exact contents.

    Raises:
        FeatureBlockError: If the file is missing or unreadable.
    """
    try:
        return path.read_bytes()
    except FileNotFoundError as error:
        raise FeatureBlockError(f"feature file not found: {path}") from error
    except OSError as error:
        raise FeatureBlockError(f"feature file could not be read: {path}: {error}") from error


def audit_written_bytes(observed: bytes, expected: bytes) -> tuple[str, ...]:
    """Compare a file's bytes against the extracted payload and the contract.

    Two questions, answered together because a maintainer needs both answers at
    once: does the file still match what the source document says it should be, and
    does it still satisfy the frozen contract? A file can fail the first and pass
    the second only if the source document itself has drifted, so reporting them
    separately is what tells the two situations apart.

    Args:
        observed: The bytes found on disk.
        expected: The bytes extracted from the source document.

    Returns:
        A tuple of findings, empty when the file is correct in both respects.
    """
    findings: list[str] = list(verify_payload(observed))

    difference = first_difference_index(observed, expected)
    if difference is not None:
        findings.append(
            f"first differing byte is at index {difference} "
            f"({_byte_at(observed, difference)} on disk, "
            f"{_byte_at(expected, difference)} in the source document); "
            f"{len(observed)} byte(s) on disk against {len(expected)} extracted"
        )

    return tuple(findings)


def _byte_at(data: bytes, index: int) -> str:
    """Render one byte of a payload for a diagnostic message.

    Args:
        data: The payload.
        index: The offset to describe.

    Returns:
        A short readable rendering, or ``end of file`` when the offset is past the
        end -- which is what happens whenever one payload is a prefix of the other.
    """
    if index >= len(data):
        return "end of file"
    return repr(data[index : index + 1])


# ---------------------------------------------------------------------------
# Human-facing command-line output.
#
# Distinct from logging, and deliberately so. `_LOGGER` above carries diagnostics
# for whoever has configured logging; these two helpers carry the short summary a
# person standing at a terminal needs, which is the appropriate output channel for
# a command-line tool and is not a substitute for logging. Every line is prefixed
# with the program name so that output interleaved with a build log stays
# attributable.
# ---------------------------------------------------------------------------


def _emit(message: str) -> None:
    """Print one human-facing line to stdout.

    Args:
        message: The line to print, without the program-name prefix.
    """
    print(f"[{PROGRAM_NAME}] {message}", file=sys.stdout)


def _emit_error(message: str) -> None:
    """Print one human-facing line to stderr.

    Args:
        message: The line to print, without the program-name prefix.
    """
    print(f"[{PROGRAM_NAME}] {message}", file=sys.stderr)


def _emit_findings(findings: Sequence[str]) -> None:
    """Print every finding, one per line, so no discrepancy is summarised away.

    Args:
        findings: The findings to report. Printed in the order given.
    """
    for finding in findings:
        _emit_error(f"  - {finding}")


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser.

    The zero-argument invocation is the primary one, because that is what the
    ``feature`` recipe runs and what a developer types: with no arguments the
    script regenerates the feature file from the source document, in place, and
    verifies what it wrote. Every option narrows or relocates that behaviour rather
    than changing it.

    Returns:
        The configured parser. Its ``--help`` output is the user-facing reference
        for this script.
    """
    parser = argparse.ArgumentParser(
        prog=PROGRAM_NAME,
        description=(
            "Regenerate tests/features/login.feature byte-exactly from the fenced "
            "Gherkin block in README.md, then verify the result against the frozen "
            f"byte contract ({EXPECTED_BYTE_COUNT} bytes, {EXPECTED_LINE_COUNT} lines, "
            f"sha256 {EXPECTED_SHA256})."
        ),
        epilog=(
            "The Gherkin is copied verbatim, defects included and on purpose; see "
            "docs/migration-parity.md. Byte parity is asserted independently by "
            "tests/parity/test_feature_file_byte_parity.py."
        ),
    )
    parser.add_argument(
        "--readme",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Source document to extract from. Defaults to "
            f"{README_RELATIVE_PATH} at the repository root."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Feature file to write, or to audit under --check. Defaults to "
            f"{FEATURE_RELATIVE_PATH} at the repository root."
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Verify only: report whether the feature file already matches the source "
            "document and the byte contract, and write nothing at all. Exits non-zero "
            "on any discrepancy, which makes it usable as a gate."
        ),
    )
    parser.add_argument(
        "--require-canonical-range",
        action="store_true",
        help=(
            "Consider only the documented source range "
            f"(L{CANONICAL_FIRST_LINE_NUMBER}-L{CANONICAL_LAST_LINE_NUMBER}) and fail "
            "if the block is not there. Without this flag a relocated block is "
            "reported and then accepted, provided its bytes satisfy the contract."
        ),
    )
    return parser


def _describe(extraction: Extraction) -> str:
    """Summarise a proven extraction in one line.

    Args:
        extraction: The extraction to describe.

    Returns:
        A single line naming the range, the size and the digest.
    """
    return (
        f"{extraction.block_range.label} "
        f"({extraction.block_range.line_count} lines, "
        f"{len(extraction.payload)} bytes, sha256 {extraction.digest})"
    )


def run_check(output: Path, extraction: Extraction) -> int:
    """Audit an existing feature file without modifying anything.

    Nothing is written, no directory is created, and the file is opened read-only,
    so a working tree is exactly as clean after this call as it was before.

    Args:
        output: The feature file to audit.
        extraction: The already-proven payload to compare it against.

    Returns:
        :data:`EXIT_SUCCESS` when the file matches, :data:`EXIT_FAILURE` otherwise.
    """
    if not output.exists():
        _emit_error(f"{output} does not exist; run this script without --check to create it.")
        _LOGGER.warning("Feature file absent during check: %s", output)
        return EXIT_FAILURE

    findings = audit_written_bytes(read_written_bytes(output), extraction.payload)
    if findings:
        _emit_error(f"{output} does not match the source document. {len(findings)} finding(s):")
        _emit_findings(findings)
        _emit_error("Nothing was written. Re-run without --check to regenerate the file.")
        _LOGGER.error("Byte-parity check failed for %s: %s", output, "; ".join(findings))
        return EXIT_FAILURE

    _emit(f"{output} is byte-identical to README.md {_describe(extraction)}.")
    _emit("Checked only; nothing was written.")
    _LOGGER.info("Byte-parity check passed for %s.", output)
    return EXIT_SUCCESS


def run_generate(output: Path, extraction: Extraction) -> int:
    """Write the feature file and prove what landed on disk.

    Writing is only half the job. The file is read straight back as bytes and
    audited against both the extracted payload and the frozen contract, so this
    command is a guard as much as a generator: a truncated write, a filesystem that
    translated something, or an editor racing the write are all caught here rather
    than surfacing later as an inexplicable parity failure.

    Args:
        output: The feature file to write.
        extraction: The proven payload to write.

    Returns:
        :data:`EXIT_SUCCESS` when the written file is provably correct,
        :data:`EXIT_FAILURE` otherwise.
    """
    write_feature_file(output, extraction.payload)

    findings = audit_written_bytes(read_written_bytes(output), extraction.payload)
    if findings:
        _emit_error(f"{output} was written but does not verify. {len(findings)} finding(s):")
        _emit_findings(findings)
        _emit_error(
            "Do not treat this file as correct. Check that no formatter or "
            "trailing-whitespace fixer is processing tests/features/."
        )
        _LOGGER.error("Post-write verification failed for %s: %s", output, "; ".join(findings))
        return EXIT_FAILURE

    _emit(f"Wrote {output} from README.md {_describe(extraction)}.")
    _emit(
        "Verified: byte count, sha256, line count, first and last line, sole trailing LF, "
        "no CR bytes, pure ASCII, and all three whitespace-only lines (2, 4 and 6 spaces)."
    )
    _LOGGER.info("Regenerated %s from %s.", output, extraction.block_range.label)
    return EXIT_SUCCESS


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line interface.

    The only function here that knows about exit codes. Every helper returns a
    value or raises; none of them exits, which is what keeps all of them directly
    testable.

    Args:
        argv: Argument list to parse. ``None`` means read ``sys.argv``.

    Returns:
        :data:`EXIT_SUCCESS` or :data:`EXIT_FAILURE`.
    """
    args = build_parser().parse_args(argv)

    root = repository_root()
    source = args.readme if args.readme is not None else readme_path(root)
    output = args.output if args.output is not None else feature_path(root)

    try:
        extraction = extract_feature_block(
            read_readme(source),
            require_canonical_range=args.require_canonical_range,
        )
    except FeatureBlockError as error:
        # Fail loudly and specifically. The message already names the byte count,
        # the digest and every candidate range that was tried.
        _emit_error(f"extraction failed: {error}")
        _LOGGER.error("Extraction failed for %s: %s", source, error)
        return EXIT_FAILURE

    try:
        if args.check:
            return run_check(output, extraction)
        return run_generate(output, extraction)
    except FeatureBlockError as error:
        _emit_error(f"failed: {error}")
        _LOGGER.error("Operation failed for %s: %s", output, error)
        return EXIT_FAILURE


if __name__ == "__main__":
    # The single place a process exit code is produced.
    raise SystemExit(main())
