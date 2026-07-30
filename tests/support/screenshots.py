"""Capture the ``screen shots`` and ``error shots`` a test run leaves behind.

This module **captures** image artifacts. It never lists, describes, classifies or
serves one. Its same-named counterpart in the application tree,
``app/reporting/screenshots.py``, has the exact opposite responsibility: it
**indexes** the files written here so the HTTP report surface can serve them. The
migration plan states that division of labour verbatim -- "``app/reporting/
screenshots.py`` indexes them and the report endpoint serves them" -- and the two
modules hold no common code. **Neither imports the other, in either direction.**

That split is a deployment requirement rather than a stylistic one. Capturing a
screen shot needs the browser-automation stack, which is pinned in
``requirements-test.txt``; the deployed container installs ``requirements.txt``
alone and has no browser bindings at all. Merging the two modules would drag the
test-only stack onto the runtime import path and break the image.

Source construct
================
This module ports prose rather than code -- the migrated project referenced its
step definitions and runner but never committed them, so the two sentences below
are the whole specification. They are quoted verbatim out of
``[README.md:L42-L43]``, because source text is data and is never reworded,
re-punctuated or modernised::

    It generate JSON, HTML and Txt reporters as well. It also generate `screen shots` for your tests if you enable it and
    also generate `error shots` for your failed test cases as well.

Two capabilities are promised there, with two *different* triggers, and the
distinction is load-bearing:

``screen shots``
    Produced "for your tests **if you enable it**" -- therefore **opt-in, with
    the default OFF**, and applicable to any test. :func:`capture_screen_shot`
    will not write anything unless a caller explicitly enables it. Capturing them
    unconditionally would add behaviour the original never had.
``error shots``
    Produced "for your **failed** test cases" -- therefore **failure-driven and
    automatic**, with no opt-in gate. :func:`capture_error_shot` is called by the
    failure path and needs no flag.

The two kinds keep separate destinations, separate triggers and separate public
helpers. They are never collapsed into one undifferentiated "capture" function.

Where the files go
==================
Both destination directories are obtained from ``app/utils/paths.py``, which is
the single place in this project allowed to define the ephemeral artifact-root
name. No path literal is spelled here -- not the root, not either shot directory
-- so the two spellings this project depends on (one of them a single word, the
other hyphenated) can never drift apart, and the CI publisher's report glob can
never be invalidated from this file.

That import is deliberately **lazy and guarded**. Importing anything from the
``app`` package executes the package initialiser, which is the Flask application
factory, so an ``app`` import transitively needs Flask -- a runtime distribution
that a harness-only or offline verification environment is not guaranteed to
have. Resolving the destination therefore happens inside the function that needs
it, wrapped in ``try``/``except ImportError``: on failure the capture is skipped
with a logged warning instead of taking down collection. Importing *this* module
is total: it needs nothing beyond the standard library.

Nothing here ever deletes. Wiping the artifact root is the ``Makefile`` ``clean``
target's job -- the port of ``mvn clean`` -- and directory *creation* is
delegated to ``app/utils/paths.py`` as well, so this module owns no filesystem
layout knowledge of its own.

Who calls this
==============
The capture *trigger* is a ``pytest_runtest_makereport`` hook wrapper in
``tests/step_defs/conftest.py``. It lives there, not here: this module defines no
fixture, no hook, no marker and no pytest configuration, and exposes plain
callables only. The hook decides *when* to capture; these helpers decide *how*,
and hand back a :class:`CaptureResult` the hook can attach to the report.

A capture failure never fails a test
====================================
This is the single most important behavioural rule in the file. A screen shot is
diagnostic metadata: a problem taking one must never change a test's verdict.
Every plausible failure -- an absent driver, an object without the screenshot
method, a browser session that has already been quit, a refused or read-only
destination, an unimportable path module -- is caught, logged through the module
logger and reported as a "not captured" result. Nothing propagates to the caller,
and no status this module returns can influence an exit code. Handlers, levels
and formats belong exclusively to ``app/logging_config.py``; this module obtains
a logger and never configures one, and never writes to standard output.

One boundary is drawn deliberately: the guards catch ``Exception``, not
``BaseException``, so an interruption or an interpreter exit still propagates. It
has to. A cancelled run is meant to end as an interrupted run -- which the ported
exit-code policy classifies as a hard failure, unlike an ordinary test failure --
and swallowing the interrupt inside a screenshot helper would silently defeat
that. Every failure a driver or a filesystem can actually produce is an
``Exception``, so nothing that matters escapes through this gap.

File names are safe under parallel workers
==========================================
The default invocation is parallel -- the port of Maven Surefire's method-level
parallelism with unlimited threads -- and every worker is a separate operating
system process, so several may capture at the same instant. Names are therefore
built from a deterministically sanitised test identifier plus a random unique
component; a timestamp alone is not sufficient, because two workers can land in
the same clock tick. Sanitising also keeps every name a single, harmless path
component: pytest node identifiers legitimately contain slashes, colons,
brackets, spaces, quotes and -- thanks to a preserved defect in the ported
Gherkin -- angle brackets and e-mail addresses. Anything outside a conservative
allow-list is replaced, runs are collapsed, the length is bounded, and the
resolved write path is proven to stay inside its destination directory before a
single byte is written. The artifacts are later served over HTTP, so a traversal
here would become a file-disclosure vulnerability there.

Empty directories are the normal state
======================================
Finding both shot directories empty is expected, and is not a bug. There are
three independent reasons, and all three are intentional:

1. Screen shots are opt-in and default to OFF, exactly as the source prose says.
2. The ported runner's preserved default tag expression selects **zero**
   scenarios, so nothing runs and nothing fails -- the zero-selection defect
   recorded as D2, deliberately kept rather than corrected. The related
   non-gating behaviour, D3, means a failure could not fail the build even if one
   occurred; a capture outcome is likewise never allowed to influence a verdict.
3. The application under test is external and unreachable from CI, so
   browser-driven scenarios cannot execute end to end here at all. Evidence for
   this module is therefore structural -- name safety, containment, byte-exact
   writes, never-raising behaviour -- and never an end-to-end browser assertion.

``docs/migration-parity.md`` is the authoritative register of the preserved
defects, D1 through D9, including D2 and D3 named above.

Usage
=====
::

    >>> from tests.support.screenshots import capture_error_shot
    >>> result = capture_error_shot(driver, "tests/step_defs/test_x.py::test_y")
    >>> if result:
    ...     report_attachment = result.path
"""

from __future__ import annotations

import logging
import string
import uuid
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Final, Protocol

if TYPE_CHECKING:
    # Type-only, and therefore free at run time: `from __future__ import
    # annotations` turns every annotation below into a string, so this block is
    # never executed. It is what lets the module be typed against the artifact
    # layout without importing the `app` package -- and therefore Flask -- at
    # import time. The real, guarded imports happen inside the two private
    # functions that need them.
    from app.utils.paths import StrPath

__all__ = [
    "DEFAULT_SHOT_EXTENSION",
    "FALLBACK_TEST_ID",
    "MAX_FILENAME_LENGTH",
    "MAX_SANITIZED_TEST_ID_LENGTH",
    "MAX_UNIQUE_SUFFIX_LENGTH",
    "REPLACEMENT_CHARACTER",
    "SAFE_FILENAME_CHARACTERS",
    "SCREENSHOT_METHOD_NAME",
    "SCREEN_SHOTS_ENABLED_BY_DEFAULT",
    "UNIQUE_SUFFIX_LENGTH",
    "CaptureResult",
    "CaptureStatus",
    "ShotKind",
    "SupportsScreenshotBytes",
    "build_shot_filename",
    "capture_error_shot",
    "capture_screen_shot",
    "sanitize_test_id",
]

# Structured logging only: the logger is obtained here and never configured.
# Handler, level and format belong exclusively to app/logging_config.py, and this
# module never writes to standard output.
_LOGGER: Final[logging.Logger] = logging.getLogger(__name__)


# =============================================================================
# Naming policy.
#
# Every constant below exists to make one guarantee about a generated file name:
# that it is a single, harmless, length-bounded path component which the report
# index can list and the report endpoint can serve.
# =============================================================================

SAFE_FILENAME_CHARACTERS: Final[frozenset[str]] = frozenset(
    string.ascii_letters + string.digits + "._-"
)
"""The allow-list a generated file name is built from.

Deliberately narrower than any filesystem would demand: ASCII letters, digits,
dot, underscore and hyphen. Everything else -- path separators, colons, brackets,
spaces, quotes, angle brackets, at-signs, NUL bytes, and every non-ASCII
character -- is replaced. An allow-list is used rather than a deny-list because a
deny-list has to enumerate every hostile character correctly to be safe, while an
allow-list is safe by construction.
"""

REPLACEMENT_CHARACTER: Final[str] = "_"
"""What one run of rejected characters collapses to. Itself always allowed."""

_TRIMMED_EDGE_CHARACTERS: Final[str] = "._-"
"""Characters stripped from both ends of a sanitised identifier.

A leading dot would make the artifact a hidden file that a directory listing may
skip; a leading hyphen makes a name that reads like a command-line flag; and a
trailing separator only produces a doubled one once the unique suffix is
appended. Stripping the three of them is deterministic, so the same input still
yields the same result.
"""

FALLBACK_TEST_ID: Final[str] = "unnamed-test"
"""Stem used when sanitising leaves nothing usable.

Reached by an empty identifier, by a value that is not a string at all, and by
inputs made up entirely of rejected or trimmed characters -- ``".."`` among them,
which is precisely why the fallback exists rather than an exception.
"""

DEFAULT_SHOT_EXTENSION: Final[str] = ".png"
"""Extension of every artifact this module writes.

PNG is what the browser-automation stack returns from its binary screenshot call,
and it is the first entry of the extension allow-list the report index applies,
so a captured file is guaranteed to be indexable and to be served with an image
media type.
"""

_EXTENSION_CHARACTERS: Final[frozenset[str]] = frozenset(string.ascii_lowercase + string.digits)
"""Characters permitted in an extension, after the leading dot."""

_MAX_EXTENSION_BODY_LENGTH: Final[int] = 16
"""Bound on an extension, excluding its dot.

Ample for any image format, and the reason the file-name budget arithmetic can be
proved to stay inside :data:`MAX_FILENAME_LENGTH` whatever a caller passes.
"""

MAX_FILENAME_LENGTH: Final[int] = 255
"""Hard upper bound on the whole generated file name, in characters.

255 is the per-component limit of the common Linux filesystems, and of macOS and
Windows too. Because the allow-list admits ASCII only, one character is one byte
here, so a character bound is also a byte bound.
"""

MAX_SANITIZED_TEST_ID_LENGTH: Final[int] = 160
"""Bound on the sanitised identifier that forms the readable stem of a name.

Parametrised pytest node identifiers are long -- a module path, a test function
and a bracketed parameter set -- and a name still has to stay comfortably inside
:data:`MAX_FILENAME_LENGTH` after the separator, the unique suffix and the
extension are added. 160 keeps names readable while leaving ample headroom.
"""

UNIQUE_SUFFIX_LENGTH: Final[int] = 12
"""Length of the generated unique component, in hexadecimal characters.

Twelve hexadecimal characters carry 48 bits taken from a version-4 UUID, which is
seeded from the operating system's entropy source rather than from any process
state. That matters: the suite runs one worker process per logical CPU, so a
component derived from a clock or a counter could repeat across workers, while
this one is independent of both.
"""

MAX_UNIQUE_SUFFIX_LENGTH: Final[int] = 32
"""Bound applied to a caller-supplied unique component.

Passing the component explicitly is what makes :func:`build_shot_filename`
assertable without a browser; the value is still caller input, so it is sanitised
and bounded exactly like the identifier.
"""

_FILENAME_SEPARATOR: Final[str] = "_"
"""Joins the readable stem to the unique component."""

SCREENSHOT_METHOD_NAME: Final[str] = "get_screenshot_as_png"
"""The single driver method this module calls.

The binary form is used rather than the save-to-file form on purpose. Receiving
the bytes and writing them here is what lets this module validate the destination
before anything touches the disk, and lets a test assert that the file on disk is
byte-identical to what the driver produced.
"""

SCREEN_SHOTS_ENABLED_BY_DEFAULT: Final[bool] = False
"""Default of the screen-shot opt-in: OFF.

The source prose promises screen shots "for your tests **if you enable it**".
Enabling them by default would add behaviour the migrated project never had, so
the default is off and :func:`capture_screen_shot` reports
:attr:`CaptureStatus.DISABLED` -- quietly, and not as a failure -- until a caller
opts in.
"""


# =============================================================================
# The capture contract: what is captured, how it went, and what came back.
# =============================================================================


class ShotKind(StrEnum):
    """The two artifact kinds the source prose promises, and nothing else.

    The member *values* are the wording of ``[README.md:L42-L43]`` itself, so a
    log line or a report attachment describes the artifact in the project's own
    terms rather than in an invented vocabulary.

    Singular by design: a member names one captured image, whereas the report
    index in the application tree groups whole directories. The two enumerations
    are independent on purpose -- this module and the index never import each
    other -- so this one carries no directory, media type or listing behaviour.
    """

    SCREEN_SHOT = "screen shot"
    """Opt-in, for any test: "for your tests if you enable it"."""

    ERROR_SHOT = "error shot"
    """Automatic, failure-driven: "for your failed test cases"."""


class CaptureStatus(StrEnum):
    """Exactly why a capture did or did not produce a file.

    A single boolean would be too coarse. The calling hook needs to tell a
    deliberate skip -- screen shots are off by default -- apart from a genuine
    problem such as a browser session that has already been quit, and a diagnostic
    log line is far more useful when it can name the reason. Every member is
    therefore a distinct, stable string that a test can assert on.

    No member is an error in the sense of failing anything: see
    :attr:`is_failure`, which classifies a member for logging and reporting only.
    Nothing this enumeration expresses can influence a test verdict or an exit
    code.
    """

    CAPTURED = "captured"
    """An image was written to the destination directory."""

    DISABLED = "disabled"
    """Screen shots were not enabled, so nothing was attempted.

    The default, and the quiet path: it is reported at debug level and is not a
    failure.
    """

    NO_DRIVER = "no-driver"
    """No driver was supplied, so there was no browser to photograph.

    Routine rather than exceptional: a test that never started a browser -- or one
    whose browser fixture failed during set-up -- has nothing to capture.
    """

    UNSUPPORTED_DRIVER = "unsupported-driver"
    """The object supplied cannot produce a binary screenshot.

    Raised in practice by a driver that has been closed and replaced, or by a
    stand-in that does not implement the one method this module calls.
    """

    DRIVER_ERROR = "driver-error"
    """The driver was asked for an image and refused.

    A quit session, a lost connection to the browser, or a timeout -- everything
    the browser-automation stack can raise from that one call.
    """

    EMPTY_IMAGE = "empty-image"
    """The driver returned no bytes.

    Writing a zero-byte file would leave an artifact the report index lists but no
    viewer can open, so nothing is written at all.
    """

    DESTINATION_UNAVAILABLE = "destination-unavailable"
    """The destination directory could neither be resolved nor created.

    Covers both the unimportable path module and a filesystem that refuses the
    directory.
    """

    UNSAFE_DESTINATION = "unsafe-destination"
    """The computed write path resolved outside its destination directory.

    Unreachable through :func:`build_shot_filename`, whose allow-list cannot
    produce a path separator. Retained as the enforced invariant behind that
    claim, because these artifacts are later served over HTTP.
    """

    WRITE_FAILED = "write-failed"
    """The bytes could not be written: read-only, full, or refused."""

    UNEXPECTED_ERROR = "unexpected-error"
    """Anything not covered above, caught so that nothing escapes.

    The backstop that makes "a capture failure never fails a test" true rather
    than merely intended.
    """

    @property
    def is_failure(self) -> bool:
        """Whether this status warrants attention.

        :attr:`CAPTURED` succeeded and :attr:`DISABLED` is the documented default,
        so neither is a failure. Everything else is worth a warning in the log --
        and nothing more than that.

        Returns:
            ``True`` for every status other than those two.
        """
        return self not in (CaptureStatus.CAPTURED, CaptureStatus.DISABLED)


@dataclass(frozen=True, slots=True)
class CaptureResult:
    """The outcome of one capture attempt. Returned instead of raising.

    Immutable, so a result can be logged, attached to a report and passed on
    without any chance of being edited on the way. ``bool(result)`` is the
    shorthand for "a file exists", which is the question the calling hook actually
    asks::

        result = capture_error_shot(driver, node_id)
        if result:
            attach(result.path)

    Attributes:
        kind: Which artifact was attempted.
        status: Exactly what happened -- see :class:`CaptureStatus`.
        test_id: The identifier as supplied by the caller, unmodified, so a result
            can always be traced back to its test even though the file name on
            disk is a sanitised form of it.
        path: Where the image was written, or ``None`` when nothing was written.
        byte_count: Size of the image written; ``0`` when nothing was written.
        detail: Short human-readable reason, empty on success. Never contains a
            traceback: the traceback, when there is one, goes to the log.
    """

    kind: ShotKind
    status: CaptureStatus
    test_id: str
    path: Path | None = None
    byte_count: int = 0
    detail: str = ""

    @property
    def captured(self) -> bool:
        """Whether an image file was actually written."""
        return self.status is CaptureStatus.CAPTURED

    def __bool__(self) -> bool:
        """Alias of :attr:`captured`, so a result reads as a condition."""
        return self.captured


class SupportsScreenshotBytes(Protocol):
    """The whole of what this module needs from a browser driver.

    A structural protocol rather than a concrete driver type, for two reasons.

    First, it keeps the browser-automation distribution out of this module's
    imports entirely: the calling hook already holds the driver and passes it in,
    so this module only ever calls one method on an object handed to it. The
    module therefore imports cleanly with no third-party package installed at all,
    which matters because the parity suite -- the behavioural acceptance gate of
    the whole migration -- must never be taken down by an absent optional
    dependency.

    Second, it states the real contract. The published driver satisfies this
    protocol structurally, with the identical signature, so passing one type-checks
    without any adapter; and the requirement is documented as the single method it
    genuinely is, rather than as a whole driver class.

    This is a type declaration and nothing more. It defines no behaviour, so it is
    in no sense a stand-in for a driver or for the external application under
    test, both of which are out of scope here.
    """

    def get_screenshot_as_png(self) -> bytes:
        """Return the current window as PNG bytes."""
        ...


# =============================================================================
# File naming.
#
# Split into a pure, deterministic half and a deliberately random half so the
# first can be asserted exactly, with no browser and no network, while the second
# is what keeps parallel workers from overwriting one another.
# =============================================================================


def sanitize_test_id(test_id: str, *, max_length: int = MAX_SANITIZED_TEST_ID_LENGTH) -> str:
    """Reduce *test_id* to a safe, readable, bounded file-name stem.

    Pure and deterministic: the same input always yields the same output, which is
    what lets a file on disk be correlated back to the test that produced it.

    A pytest node identifier is not a file name. It carries the module path, a
    ``::`` separator, the test function and -- for a parametrised scenario -- a
    bracketed parameter set, so a realistic one contains slashes, colons, brackets
    and spaces. The ported Gherkin makes that worse in two specific ways, both of
    them preserved defects rather than accidents: one outline receives its
    placeholders literally, so identifiers carry angle brackets, and the example
    tables supply e-mail addresses, so they carry at-signs and dots.

    Every character outside :data:`SAFE_FILENAME_CHARACTERS` is therefore replaced
    with :data:`REPLACEMENT_CHARACTER`; consecutive rejected characters collapse to
    a single one, so a ``"::"`` does not become ``"__"``; and both ends are
    trimmed of dots, underscores and hyphens.

    Traversal is impossible by construction rather than by inspection: the
    allow-list contains no path separator, no colon and no NUL byte, so no output
    of this function can address anything but a file inside the directory it is
    joined to. ``"."`` and ``".."`` reduce to :data:`FALLBACK_TEST_ID`, since
    trimming removes their every character.

    Args:
        test_id: The identifier to reduce, normally a pytest node identifier. A
            value that is not a string, or is empty, yields
            :data:`FALLBACK_TEST_ID` -- this function is on the never-raise path
            and the isinstance guard is deliberate, because the caller is a pytest
            hook handling objects the type system did not vouch for.
        max_length: Maximum number of characters to keep. Values of zero or less
            disable truncation, which is useful when a caller has already reserved
            its own budget.

    Returns:
        A non-empty string made only of characters from
        :data:`SAFE_FILENAME_CHARACTERS`, never longer than *max_length* when that
        is positive, and never one of the directory-addressing names.
    """
    if not isinstance(test_id, str) or not test_id:
        return FALLBACK_TEST_ID

    characters: list[str] = []
    previous_was_replacement = False
    for character in test_id:
        if character in SAFE_FILENAME_CHARACTERS:
            characters.append(character)
            previous_was_replacement = False
        elif not previous_was_replacement:
            # One replacement per run of rejected characters, so `::` collapses to
            # a single separator instead of doubling the name's punctuation.
            characters.append(REPLACEMENT_CHARACTER)
            previous_was_replacement = True

    sanitized = "".join(characters).strip(_TRIMMED_EDGE_CHARACTERS)
    if not sanitized:
        # Reached by an identifier made entirely of rejected characters, and by
        # "." and ".." -- every character of which trimming removes.
        return FALLBACK_TEST_ID

    if 0 < max_length < len(sanitized):
        # Trim the cut edge as well: truncation can expose a trailing separator
        # that was harmless mid-name. The fallback covers a cut that removes
        # everything, which a very small budget can do.
        sanitized = sanitized[:max_length].strip(_TRIMMED_EDGE_CHARACTERS) or FALLBACK_TEST_ID

    return sanitized


def _normalize_extension(extension: str) -> str:
    """Reduce *extension* to a safe, lower-cased suffix with a leading dot.

    Anything unusable falls back to :data:`DEFAULT_SHOT_EXTENSION` rather than
    raising, keeping this function on the never-raise path with the rest of the
    module. Bounding the length here is what makes the file-name budget arithmetic
    in :func:`build_shot_filename` provably safe for any input.

    Args:
        extension: Candidate extension, with or without its leading dot.

    Returns:
        A dot followed by one or more lower-case ASCII letters or digits.
    """
    if not isinstance(extension, str):
        return DEFAULT_SHOT_EXTENSION

    body = extension.strip().lower().lstrip(".")
    if not body or len(body) > _MAX_EXTENSION_BODY_LENGTH:
        return DEFAULT_SHOT_EXTENSION
    if any(character not in _EXTENSION_CHARACTERS for character in body):
        return DEFAULT_SHOT_EXTENSION
    return f".{body}"


def _unique_suffix() -> str:
    """Return a fresh unique file-name component.

    A version-4 UUID is used because it is drawn from the operating system's
    entropy source and is therefore independent of the clock, of the process
    identifier and of any counter. Under one worker process per logical CPU, two
    captures can genuinely occur inside the same clock tick, so a timestamp is not
    a sufficient discriminator; and a per-process counter would need each worker to
    contribute a distinct seed to be safe. This needs neither.

    Returns:
        :data:`UNIQUE_SUFFIX_LENGTH` lower-case hexadecimal characters.
    """
    return uuid.uuid4().hex[:UNIQUE_SUFFIX_LENGTH]


def build_shot_filename(
    test_id: str,
    *,
    extension: str = DEFAULT_SHOT_EXTENSION,
    unique_suffix: str | None = None,
) -> str:
    """Build the file name one captured image is written under.

    The name has three parts -- a sanitised stem, a unique component and an
    extension -- and the split is deliberate. The stem is deterministic, so a file
    can be read back to the test that produced it. The unique component makes the
    whole name collision-free across the parallel worker processes the ported suite
    runs under. The extension keeps the artifact inside the allow-list the report
    index applies, so a captured file is always listable and always served with an
    image media type.

    The result is guaranteed to be a single path component: the stem is drawn from
    :data:`SAFE_FILENAME_CHARACTERS`, the unique component is hexadecimal, and the
    extension is validated, so nothing in the output can be a path separator, a
    ``"."`` or a ``".."``. Its length is guaranteed not to exceed
    :data:`MAX_FILENAME_LENGTH` for *any* input, because both variable parts are
    independently bounded and the stem is then cut to whatever budget remains.

    This function is deliberately part of the public surface: given
    *unique_suffix*, it is fully deterministic and can be asserted exactly, with no
    browser, no network and no filesystem -- which matters because the application
    under test is unreachable from CI, so the evidence for this module has to be
    structural.

    Args:
        test_id: Identifier of the test being captured, normally a pytest node
            identifier. Sanitised by :func:`sanitize_test_id`.
        extension: Artifact extension. Defaults to
            :data:`DEFAULT_SHOT_EXTENSION`; anything unusable falls back to it.
        unique_suffix: Explicit unique component. ``None`` -- the default and the
            production path -- generates one. A supplied value is caller input and
            is sanitised and bounded exactly like *test_id*, so passing one can
            never widen the character set of the result.

    Returns:
        A file name safe to join onto a destination directory.
    """
    suffix = (
        _unique_suffix()
        if unique_suffix is None
        else sanitize_test_id(unique_suffix, max_length=MAX_UNIQUE_SUFFIX_LENGTH)
    )
    normalized_extension = _normalize_extension(extension)

    # Whatever the variable parts cost, the stem gets the remainder -- never more.
    budget = (
        MAX_FILENAME_LENGTH - len(suffix) - len(normalized_extension) - len(_FILENAME_SEPARATOR)
    )
    if budget <= 0:
        # Only reachable with an extreme caller-supplied suffix. The unique part
        # alone is still a valid, collision-free, indexable name.
        return f"{suffix}{normalized_extension}"

    stem = sanitize_test_id(test_id, max_length=min(MAX_SANITIZED_TEST_ID_LENGTH, budget))
    return f"{stem}{_FILENAME_SEPARATOR}{suffix}{normalized_extension}"


# =============================================================================
# Destination resolution.
#
# Both functions below own one guarded, function-local import of the artifact
# layout module, and both degrade to a logged warning rather than raising. The
# import is local rather than module-level on purpose: importing the application
# package executes its initialiser -- the Flask application factory -- so a
# module-level import would make merely importing this module depend on a runtime
# distribution the harness does not otherwise need. Deferring it keeps `import
# tests.support.screenshots` total, and moves any failure inside the never-raise
# guard where it belongs.
#
# Neither function spells a path literal. The artifact root and both shot
# directory names have exactly one definition in this project, and it is not here.
# =============================================================================


def _shot_directory(
    kind: ShotKind,
    base_dir: StrPath | None,
    destination_dir: StrPath | None,
) -> Path | None:
    """Resolve the directory *kind*'s artifacts are written to.

    Args:
        kind: Which artifact kind is being captured. Screen shots and error shots
            have separate destinations, and the two spellings differ from one
            another -- both are taken from the layout module, so neither can be
            mis-spelled here.
        base_dir: Optional directory the artifact root should sit inside, passed
            straight through to the layout module. ``None`` yields the
            repository-relative layout the test configuration and CI already use.
        destination_dir: Explicit destination, overriding the layout entirely.
            Exists so a test can redirect writes into a temporary directory.

    Returns:
        The destination directory, or ``None`` when the layout module cannot be
        imported -- in which case a warning has been logged and the caller must
        skip the capture. Never raises.
    """
    if destination_dir is not None:
        # An explicit override answers the question outright, and deliberately
        # without consulting the layout module: a test redirecting writes into a
        # temporary directory must work even where the application package cannot
        # be imported at all.
        return Path(destination_dir)

    try:
        from app.utils.paths import resolve_layout
    except ImportError as error:
        _LOGGER.warning(
            "Cannot resolve the %s destination: the artifact layout module is "
            "unavailable (%s: %s). Skipping the capture rather than inventing a "
            "path, because this module deliberately holds no layout knowledge of "
            "its own.",
            kind.value,
            type(error).__name__,
            error,
        )
        return None

    layout = resolve_layout(base_dir)
    if kind is ShotKind.SCREEN_SHOT:
        return layout.screenshots_dir
    return layout.error_shots_dir


def _ensure_destination_directory(destination: Path) -> bool:
    """Make sure *destination*'s parent directory exists, without ever raising.

    Creating it is not primarily this module's job: the artifact tree has exactly
    three owners -- the layout module, the ``Makefile`` test target and the BDD
    configuration module -- and in the normal flow all five directories exist
    before the first test runs. This is the defensive path for the cases that flow
    does not cover, above all a caller-supplied temporary destination.

    So it *delegates*: the layout module's own parent-directory helper does the
    work, which keeps every directory-creating call in this project behind one
    door and stops a competing layout from being created here. Nothing is ever
    removed, truncated or overwritten by this function.

    Args:
        destination: The file path about to be written.

    Returns:
        ``True`` when the parent directory exists once the call returns. ``False``
        when it does not, in which case a warning has been logged.
    """
    parent = destination.parent
    if parent.is_dir():
        return True

    try:
        from app.utils.paths import ensure_parent_directory
    except ImportError as error:
        _LOGGER.warning(
            "Destination directory %s is missing and cannot be created: the "
            "artifact layout module is unavailable (%s: %s). Skipping the capture.",
            parent.as_posix(),
            type(error).__name__,
            error,
        )
        return False

    # The helper creates the parent of the file path it is given, never raises, and
    # reports success as a boolean. `is_dir` is re-checked because a path can exist
    # as something other than a directory.
    return ensure_parent_directory(destination) and parent.is_dir()


def _is_inside(candidate: Path, directory: Path) -> bool:
    """Return whether *candidate* is a direct child of *directory*.

    The enforced invariant behind the claim that a generated name cannot escape its
    destination. :func:`build_shot_filename` already makes escape impossible -- its
    allow-list holds no path separator -- but these artifacts are later served over
    HTTP, where a traversal would become an arbitrary-file disclosure, so the
    property is proved before anything is written rather than merely argued.

    Both paths are resolved first, so a symbolic link in the destination is
    followed consistently on both sides and cannot be used to smuggle the write
    somewhere else.

    Args:
        candidate: The path about to be written.
        directory: The directory the write must stay inside.

    Returns:
        ``True`` only when *candidate* resolves to a direct child of *directory*.
        Never raises: an unresolvable path is simply refused.
    """
    try:
        resolved_directory = directory.resolve()
        resolved_candidate = candidate.resolve()
    except OSError:
        return False

    if resolved_candidate == resolved_directory:
        return False
    return resolved_candidate.parent == resolved_directory


# =============================================================================
# Capture.
#
# One private implementation, two public entry points. The entry points differ in
# exactly the way the source prose differs: the screen-shot one is gated by an
# opt-in that defaults to off, the error-shot one is not gated at all.
# =============================================================================


def _not_captured(
    kind: ShotKind,
    test_id: str,
    status: CaptureStatus,
    detail: str,
) -> CaptureResult:
    """Build the "nothing was written" result for *status*.

    A single constructor for every unsuccessful outcome, so no branch of
    :func:`_capture` can accidentally report a path or a byte count for a file that
    does not exist.

    Args:
        kind: Which artifact was attempted.
        test_id: The caller's identifier, recorded unmodified.
        status: Why nothing was written.
        detail: Short human-readable reason.

    Returns:
        A result whose path is ``None`` and whose byte count is zero.
    """
    return CaptureResult(kind=kind, status=status, test_id=test_id, detail=detail)


def _capture(
    kind: ShotKind,
    driver: SupportsScreenshotBytes | None,
    test_id: str,
    base_dir: StrPath | None,
    destination_dir: StrPath | None,
) -> CaptureResult:
    """Capture one image of *kind*, and never raise while doing so.

    The order of operations is deliberate. The destination is resolved and proved
    safe *before* the driver is asked for an image, so a misconfigured destination
    costs nothing and a browser round trip is never wasted; and the directory is
    only created once there are bytes worth writing, so a skipped capture leaves no
    empty directory behind that was not already there.

    Every step reports its own failure through a distinct :class:`CaptureStatus`,
    and the whole body sits inside a deliberately broad guard. That breadth is the
    point: a screen shot is diagnostic metadata, and the browser-automation stack,
    the filesystem and a half-dead browser session between them can raise more
    kinds of exception than any narrow list would enumerate correctly. Letting one
    of them escape would turn a passing test red and corrupt the very evidence the
    capture exists to support, so the guard catches everything, logs it once with
    enough context to diagnose, and returns a result.

    Broad, but not unbounded: it catches ``Exception``, so an interruption or an
    interpreter exit -- neither of which any driver or filesystem raises -- still
    reaches the runner and is still classified as an interrupted run.

    Args:
        kind: Which artifact to capture, selecting the destination directory.
        driver: The browser driver, or ``None``. Supplied by the caller, so this
            module needs no browser-automation import at all.
        test_id: Identifier of the test being captured.
        base_dir: Optional directory the artifact root should sit inside.
        destination_dir: Explicit destination directory, overriding the layout.

    Returns:
        A :class:`CaptureResult`. Always. Under no input does this function raise.
    """
    try:
        if driver is None:
            # Routine, not exceptional: a test that never opened a browser, or
            # whose browser fixture failed during set-up, has nothing to photograph.
            _LOGGER.debug("No driver supplied, so no %s was taken for %s", kind.value, test_id)
            return _not_captured(kind, test_id, CaptureStatus.NO_DRIVER, "no driver was supplied")

        take_screenshot = getattr(driver, SCREENSHOT_METHOD_NAME, None)
        if not callable(take_screenshot):
            _LOGGER.warning(
                "Cannot take a %s for %s: %s has no callable %s()",
                kind.value,
                test_id,
                type(driver).__name__,
                SCREENSHOT_METHOD_NAME,
            )
            return _not_captured(
                kind,
                test_id,
                CaptureStatus.UNSUPPORTED_DRIVER,
                f"{type(driver).__name__} has no callable {SCREENSHOT_METHOD_NAME}()",
            )

        directory = _shot_directory(kind, base_dir, destination_dir)
        if directory is None:
            # _shot_directory has already logged why.
            return _not_captured(
                kind,
                test_id,
                CaptureStatus.DESTINATION_UNAVAILABLE,
                "the destination directory could not be resolved",
            )

        destination = directory / build_shot_filename(test_id)
        if not _is_inside(destination, directory):
            _LOGGER.warning(
                "Refusing to write a %s for %s: %s resolves outside %s",
                kind.value,
                test_id,
                destination.as_posix(),
                directory.as_posix(),
            )
            return _not_captured(
                kind,
                test_id,
                CaptureStatus.UNSAFE_DESTINATION,
                f"{destination.as_posix()} resolves outside its destination directory",
            )

        try:
            image = take_screenshot()
        except Exception as error:
            # A quit session, a lost browser connection or a timeout. Narrowing
            # this would mean naming the browser stack's exception hierarchy, which
            # would require importing it -- the one thing this module avoids.
            _LOGGER.warning(
                "The driver could not produce a %s for %s (%s: %s)",
                kind.value,
                test_id,
                type(error).__name__,
                error,
            )
            return _not_captured(
                kind,
                test_id,
                CaptureStatus.DRIVER_ERROR,
                f"{type(error).__name__}: {error}",
            )

        if not isinstance(image, bytes | bytearray):
            _LOGGER.warning(
                "Cannot write a %s for %s: %s() returned %s, not image bytes",
                kind.value,
                test_id,
                SCREENSHOT_METHOD_NAME,
                type(image).__name__,
            )
            return _not_captured(
                kind,
                test_id,
                CaptureStatus.UNSUPPORTED_DRIVER,
                f"{SCREENSHOT_METHOD_NAME}() returned {type(image).__name__}, not image bytes",
            )

        payload = bytes(image)
        if not payload:
            # A zero-byte file would be listed by the report index and opened by no
            # viewer, so nothing is written at all.
            _LOGGER.warning(
                "Cannot write a %s for %s: the driver returned an empty image",
                kind.value,
                test_id,
            )
            return _not_captured(
                kind, test_id, CaptureStatus.EMPTY_IMAGE, "the driver returned an empty image"
            )

        if not _ensure_destination_directory(destination):
            # _ensure_destination_directory has already logged why.
            return _not_captured(
                kind,
                test_id,
                CaptureStatus.DESTINATION_UNAVAILABLE,
                f"{destination.parent.as_posix()} does not exist and could not be created",
            )

        try:
            # Binary mode, deliberately. A PNG is a byte stream, so no text
            # encoding applies or is passed here -- and passing one would corrupt
            # the image. Writing the bytes received from the driver, rather than
            # asking the driver to save the file, is what makes the file on disk
            # provably byte-identical to what the browser produced. Nothing is
            # removed or truncated: a name collision cannot occur, because every
            # name carries a unique component.
            destination.write_bytes(payload)
        except OSError as error:
            _LOGGER.warning(
                "Could not write the %s for %s to %s (%s: %s)",
                kind.value,
                test_id,
                destination.as_posix(),
                type(error).__name__,
                error,
            )
            return _not_captured(
                kind,
                test_id,
                CaptureStatus.WRITE_FAILED,
                f"{type(error).__name__}: {error}",
            )
    except Exception as error:
        # The backstop that makes "a capture failure never fails a test" true
        # rather than merely intended. Anything the steps above did not anticipate
        # ends here, is logged once with its type and message, and is reported as a
        # result. Nothing propagates to the calling hook.
        _LOGGER.warning(
            "Unexpected failure while capturing a %s for %s (%s: %s)",
            kind.value,
            test_id,
            type(error).__name__,
            error,
        )
        return _not_captured(
            kind,
            test_id,
            CaptureStatus.UNEXPECTED_ERROR,
            f"{type(error).__name__}: {error}",
        )

    _LOGGER.debug(
        "Wrote a %d-byte %s for %s to %s",
        len(payload),
        kind.value,
        test_id,
        destination.as_posix(),
    )
    return CaptureResult(
        kind=kind,
        status=CaptureStatus.CAPTURED,
        test_id=test_id,
        path=destination,
        byte_count=len(payload),
    )


def capture_screen_shot(
    driver: SupportsScreenshotBytes | None,
    test_id: str,
    *,
    enabled: bool = SCREEN_SHOTS_ENABLED_BY_DEFAULT,
    base_dir: StrPath | None = None,
    destination_dir: StrPath | None = None,
) -> CaptureResult:
    """Capture a ``screen shot`` for a test -- but only if it is enabled.

    The opt-in is this function's defining property, and it comes straight from the
    source prose: screen shots are produced "for your tests **if you enable it**".
    The gate therefore lives *here*, in the signature, and defaults to off, so a
    caller that forgets to pass it cannot accidentally start producing artifacts
    the migrated project never produced. Finding the screen-shot directory empty
    after a run is the expected outcome, not a fault.

    The counterpart for a failed test is :func:`capture_error_shot`, which writes to
    a different directory and needs no flag.

    Args:
        driver: The browser driver, or ``None`` when the test has no browser.
        test_id: Identifier of the test, normally a pytest node identifier. Used to
            build the file name and recorded verbatim on the result.
        enabled: Whether screen shots are switched on. Defaults to
            :data:`SCREEN_SHOTS_ENABLED_BY_DEFAULT`, which is ``False``; when it is
            ``False`` nothing is attempted and the driver is not touched.
        base_dir: Optional directory the artifact root should sit inside. ``None``
            uses the repository-relative layout.
        destination_dir: Explicit destination directory, overriding the layout.
            Provided so a test can redirect writes into a temporary directory.

    Returns:
        A :class:`CaptureResult`. :attr:`CaptureStatus.DISABLED` when the opt-in is
        off -- reported quietly, at debug level, and classified as a non-failure.
        Never raises, whatever the driver or the filesystem does.
    """
    if not enabled:
        _LOGGER.debug(
            "Screen shots are not enabled, so none was taken for %s. This is the "
            "documented default, not a fault.",
            test_id,
        )
        return _not_captured(
            ShotKind.SCREEN_SHOT,
            test_id,
            CaptureStatus.DISABLED,
            "screen shots are not enabled",
        )

    return _capture(
        ShotKind.SCREEN_SHOT,
        driver,
        test_id,
        base_dir=base_dir,
        destination_dir=destination_dir,
    )


def capture_error_shot(
    driver: SupportsScreenshotBytes | None,
    test_id: str,
    *,
    base_dir: StrPath | None = None,
    destination_dir: StrPath | None = None,
) -> CaptureResult:
    """Capture an ``error shot`` for a failed test.

    Deliberately **not** gated by an opt-in, because the source prose does not gate
    it: error shots are produced "for your **failed** test cases", full stop. The
    decision this function does not make is *when* a test has failed -- that belongs
    to the report hook in ``tests/step_defs/conftest.py``, which calls this once it
    knows.

    Writes to the error-shot directory, which is a different directory from the one
    :func:`capture_screen_shot` uses. The two are never merged.

    Args:
        driver: The browser driver, or ``None`` when the test has no browser.
        test_id: Identifier of the failed test, normally a pytest node identifier.
        base_dir: Optional directory the artifact root should sit inside. ``None``
            uses the repository-relative layout.
        destination_dir: Explicit destination directory, overriding the layout.

    Returns:
        A :class:`CaptureResult`. Never raises: a test has already failed by the
        time this is called, and a capture problem must not obscure that failure or
        add a second one.
    """
    return _capture(
        ShotKind.ERROR_SHOT,
        driver,
        test_id,
        base_dir=base_dir,
        destination_dir=destination_dir,
    )
