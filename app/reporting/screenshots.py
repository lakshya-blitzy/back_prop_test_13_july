"""Read-only index of the ``screen shots`` and ``error shots`` a test run leaves behind.

This module **indexes** image artifacts. It never produces one. That division of
labour is fixed by the migration plan, which assigns capture to a pytest hook
wrapper in ``tests/step_defs/conftest.py`` (helpers in
``tests/support/screenshots.py``) and states of this file, verbatim:
"``app/reporting/screenshots.py`` indexes them and the report endpoint serves
them." Capture needs the browser-automation stack, which is declared in
``requirements-test.txt`` and is deliberately absent from the deployed image; the
report surface needs only a directory listing. Splitting the two is what lets
this module sit safely on the runtime import path.

``tests/support/screenshots.py`` shares this module's file name and has the
opposite responsibility. The two hold no common code and neither imports the
other.

Source construct
----------------
Alone among the adapters in this package, this one ports prose rather than a
``@CucumberOptions`` ``plugin`` declaration. Both lines below are carried over
verbatim out of ``[README.md:L42-L43]`` -- source text is data, and is never
reworded or modernised::

    It generate JSON, HTML and Txt reporters as well. It also generate `screen shots` for your tests if you enable it and
    also generate `error shots` for your failed test cases as well.

Two capabilities are promised there, with two different triggers, and the
distinction is load-bearing: screen shots are produced "for your tests if you
enable it" -- opt-in, for every test -- while error shots are produced "for your
failed test cases" -- failure-driven. They are therefore indexed as two separate
categories with independent counts and independent existence flags, and are never
merged into one undifferentiated listing.

Directory names, and their deliberate asymmetry
-----------------------------------------------
The two directories sit immediately beneath the ephemeral artifact root
``target``, and their spellings differ from one another on purpose:

* ``screenshots`` -- one word, no hyphen and no underscore.
* ``error-shots`` -- hyphenated.

Both names are spelled out above for the reader, and neither is hard-coded
below: the values this module actually uses are imported from
``app/utils/paths.py``, which is the single place in the application that
composes an artifact path, so a name can never drift between two modules. Nor is
either directory ever created here -- see below. The artifact root keeps its
Java-flavoured name because the CI publisher selects reports with a
``fileIncludePattern`` glob rooted there ``[Jenkins:L15]``; renaming it would
silently break report publication.

An empty index is the normal state
----------------------------------
Three independent properties of this project mean that, most of the time, there
is nothing to index -- and that this is success, not an error:

1. Screen shots are opt-in. The prose says "if you enable it", and the committed
   configuration templates ship them disabled.
2. Error shots require a failed test case, and preserved defect **D2** means the
   default tag expression selects zero scenarios, so by default nothing runs and
   nothing fails.
3. Risk **R6**: the application under test is external and unreachable from CI,
   so browser-driven scenarios cannot execute end to end there.

Every absence is consequently reported, never raised: an absent directory, an
empty directory, a file where a directory was expected, and a directory that
cannot be listed all yield a well-formed result. "Absent" and "present but empty"
stay distinguishable through :class:`ShotDirectoryStatus`, because the report
index legitimately shows an artifact that has not been generated yet alongside
one that has. Nothing is ever fabricated to fill a gap.

Read-only, on the filesystem and on the verdict
-----------------------------------------------
This module only reads directory entries and their metadata. It creates no
directory, produces no file, changes nothing and removes nothing: the artifact
tree is created by ``app/utils/paths.py``, the ``Makefile`` ``test`` target and
``tests/conftest.py``, and it is wiped by the ``Makefile`` ``clean`` target -- the
port of ``mvn clean`` -- which is what keeps those artifacts ephemeral. The bytes
of an artifact are never read, decoded, resized or re-encoded; no
image-processing distribution is imported, and none is needed. A content type is
derived from the file extension alone.

Indexing likewise never influences an outcome. Preserved defect **D3** --
``<testFailureIgnore>true</testFailureIgnore>`` ``[pom.xml:L25]`` together with
the six ``-1`` publisher thresholds ``[Jenkins:L15]`` -- means the build never
fails, and the report stage runs unconditionally after the test stage, including
after failures. Error shots must therefore remain indexable *after* a failing
run, so this module returns data and never a gate, an exit code or a verdict.
Both defects are registered, with the configuration switch that opts into each
available fix, in ``docs/migration-parity.md``.

Path-traversal safety
---------------------
The consumer of this index is ``GET /api/v1/reports/<run_id>/screenshots``, served
by ``app/api/routes.py``, so a traversal defect here would become an
arbitrary-file-disclosure defect there. Every caller-supplied name is validated
by :func:`is_safe_name` before it is used -- empty names, NUL bytes, ``.``,
``..``, absolute paths and anything carrying a POSIX or Windows path separator are
rejected -- and :func:`resolve_shot` additionally returns only entries that the
directory listing itself produced. Listing in turn resolves every entry and drops
any whose real location falls outside the directory being listed, so a symbolic
link pointing elsewhere can never be served.

Layering
--------
Rule T7 fixes one dependency direction, ``api -> services -> reporting ->
utils``. This module imports the standard library and ``app.utils.paths``, and
nothing else: no ``app.services``, ``app.api``, ``app.web``, ``app.config`` or
``app`` package root, and nothing under ``tests/`` or ``scripts/``. It is
framework-agnostic by construction -- it builds no HTTP response, sets no status
code and touches no Flask object, because describing what to serve and actually
serving it are two different jobs. It obtains a logger and never configures the
logging system; that belongs to ``app/logging_config.py`` alone.

Usage
-----
::

    >>> from app.reporting import screenshots
    >>> index = screenshots.index_shots()
    >>> index.screen_shots.status
    <ShotDirectoryStatus.ABSENT: 'absent'>
    >>> index.error_shots.count
    0
    >>> index.total_count
    0
    >>> shot = screenshots.resolve_shot(screenshots.ShotCategory.ERROR_SHOTS, "login.png")
    >>> shot is None
    True
"""

import logging
import mimetypes
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from stat import S_ISREG
from types import MappingProxyType
from typing import Final

from app.utils.paths import (
    ERROR_SHOTS_DIR_NAME,
    SCREENSHOTS_DIR_NAME,
    StrPath,
    resolve_layout,
    to_posix,
)

__all__ = [
    "FALLBACK_CONTENT_TYPE",
    "IMAGE_CONTENT_TYPES",
    "IMAGE_EXTENSIONS",
    "NOT_GENERATED_DESCRIPTION",
    "ShotCategory",
    "ShotCollection",
    "ShotDirectoryStatus",
    "ShotFile",
    "ShotIndex",
    "content_type_for",
    "index_shots",
    "is_image_name",
    "is_safe_name",
    "list_error_shots",
    "list_screen_shots",
    "list_shots",
    "resolve_shot",
    "shot_directory",
]

# Structured logging only: the logger is obtained here and never configured.
# Handler, level and format belong exclusively to app/logging_config.py, and no
# message is ever written to standard output.
_LOGGER: Final[logging.Logger] = logging.getLogger(__name__)


# =============================================================================
# Content types.
#
# A shot's media type is derived from its file extension and from nothing else.
# The bytes are never read, so a renamed or truncated file is described by its
# name, exactly as a static file server would describe it.
# =============================================================================

# The allow-list, paired with the media type each extension must resolve to.
# PNG comes first because it is the format the capture side actually produces;
# the other four are accepted defensively, so a differently-configured capture
# helper still yields a browsable index. The extension allow-list published below
# is derived from this one mapping, so the two can never disagree.
IMAGE_CONTENT_TYPES: Final[Mapping[str, str]] = MappingProxyType(
    {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
)
"""Lower-cased image extensions this module indexes, mapped to their media type."""

IMAGE_EXTENSIONS: Final[tuple[str, ...]] = tuple(IMAGE_CONTENT_TYPES)
"""The allow-list of indexable extensions, in the order declared above."""

FALLBACK_CONTENT_TYPE: Final[str] = "application/octet-stream"
"""Media type reported for anything outside :data:`IMAGE_EXTENSIONS`.

The standard library's extension database is not restricted to images and is
assembled partly from system files, so it happily answers with a non-image type
for an unrelated extension. The allow-list is therefore consulted first and this
deliberately inert type is returned for everything else, rather than raising or
guessing.
"""

# Guard applied to whatever the standard library reports: only an `image/...`
# answer is trusted, and the explicit mapping above is used otherwise.
_IMAGE_TYPE_PREFIX: Final[str] = "image/"

# Path separators rejected in a caller-supplied name. Both flavours are listed
# unconditionally so that a Windows-style relative path is refused on POSIX too,
# where it would otherwise look like one perfectly ordinary file name.
_PATH_SEPARATORS: Final[tuple[str, str]] = ("/", "\\")

# Names that address a directory rather than an artifact inside it.
_RESERVED_NAMES: Final[frozenset[str]] = frozenset({".", ".."})

NOT_GENERATED_DESCRIPTION: Final[str] = "not generated yet"
"""Wording used for both "no directory" and "directory present but empty".

The report index needs a phrase for an artifact that does not exist, and the
two empty cases read identically to a human even though
:class:`ShotDirectoryStatus` keeps them apart for a program.
"""


# =============================================================================
# Categories and directory statuses.
# =============================================================================


class ShotCategory(StrEnum):
    """The two artifact categories promised by ``[README.md:L42-L43]``.

    Each member's value is the directory name imported from
    ``app/utils/paths.py``, so the enumeration can never drift away from the
    artifact layout, and the value doubles as the key a JSON response is built
    with.

    The enumeration is closed at two members because the source prose promises
    exactly two capabilities. A third would be a new feature, and the port adds
    none.
    """

    SCREEN_SHOTS = SCREENSHOTS_DIR_NAME
    """One word, no hyphen. Opt-in: produced for any test "if you enable it"."""

    ERROR_SHOTS = ERROR_SHOTS_DIR_NAME
    """Hyphenated. Failure-driven: produced "for your failed test cases"."""

    @property
    def label(self) -> str:
        """Return the human wording the README prose uses for this category.

        The prose says ``screen shots`` -- two words -- while the directory it
        describes is one word, and the same split applies to ``error shots``
        against its hyphenated directory. Both spellings are preserved rather
        than normalised into each other: the member value is the directory name
        and this is the prose name.
        """
        return _CATEGORY_LABELS[self]

    @property
    def trigger(self) -> str:
        """Return the condition under which this category is produced.

        Quoted from ``[README.md:L42-L43]``, because the difference between the
        two triggers is exactly why the categories are never merged.
        """
        return _CATEGORY_TRIGGERS[self]

    def directory(self, base_dir: StrPath | None = None) -> Path:
        """Return the directory this category's artifacts live in.

        Args:
            base_dir: Directory the artifact root should sit inside. ``None`` --
                the default -- yields the repository-relative location every
                other module and the CI publisher already use. Tests pass a
                temporary directory so the real tree stays untouched.

        Returns:
            The directory, taken from ``app/utils/paths.py``. It is *not*
            created here and may well not exist: this module never changes the
            filesystem.
        """
        layout = resolve_layout(base_dir)
        if self is ShotCategory.SCREEN_SHOTS:
            return layout.screenshots_dir
        return layout.error_shots_dir


# Prose spellings, kept beside the enumeration they belong to. Direct lookups
# (never `.get` with a default) are deliberate: the enumeration is closed, so a
# missing entry is a programming error that should surface immediately rather
# than degrade into a plausible-looking label.
_CATEGORY_LABELS: Final[Mapping[ShotCategory, str]] = MappingProxyType(
    {
        ShotCategory.SCREEN_SHOTS: "screen shots",
        ShotCategory.ERROR_SHOTS: "error shots",
    }
)

# The two triggers, quoted from [README.md:L42-L43].
_CATEGORY_TRIGGERS: Final[Mapping[ShotCategory, str]] = MappingProxyType(
    {
        ShotCategory.SCREEN_SHOTS: "produced for your tests if you enable it",
        ShotCategory.ERROR_SHOTS: "produced for your failed test cases",
    }
)


class ShotDirectoryStatus(StrEnum):
    """Outcome of inspecting one shot directory.

    Four outcomes, and not one of them is an exception. A fresh checkout has no
    artifact tree at all, so absence is the ordinary case rather than a fault,
    and the API layer turns each status into an appropriate HTTP response
    instead of handling an error.
    """

    ABSENT = "absent"
    """The directory does not exist: nothing has been generated yet."""

    EMPTY = "empty"
    """The directory exists and holds no indexable artifact."""

    AVAILABLE = "available"
    """The directory exists and holds at least one indexable artifact."""

    UNREADABLE = "unreadable"
    """The path exists but could not be listed, or is not a directory at all."""


# =============================================================================
# Immutable descriptors.
#
# Every type below is a frozen dataclass, so an index handed to a template, a
# view function or a log record cannot be edited after the fact, and two callers
# can safely share one. Sequences are tuples for the same reason.
# =============================================================================


@dataclass(frozen=True, slots=True)
class ShotFile:
    """One indexed image artifact: everything needed to serve or list it.

    Instances are produced only by this module, always from a directory entry
    that has already been checked to be a regular file lying inside the
    directory being listed.
    """

    category: ShotCategory
    """Which of the two categories this artifact belongs to."""

    name: str
    """The plain file name, with no directory part."""

    path: Path
    """Validated path of the artifact, including the artifact root.

    Spelled exactly as the layout spells it: repository-relative by default, or
    inside whatever base directory the caller supplied. Safe to hand to a static
    file server, because the entry it names was produced by a listing of
    :attr:`directory` and confirmed to resolve inside it, so it can never point
    outside the configured artifact directory.
    """

    size_bytes: int
    """Size on disk, in bytes, as reported by the filesystem."""

    modified_at: datetime
    """Modification time, always timezone-aware and always in UTC."""

    content_type: str
    """Media type derived from the file extension by :func:`content_type_for`."""

    @property
    def directory(self) -> Path:
        """Return the directory holding this artifact."""
        return self.path.parent

    @property
    def posix_path(self) -> str:
        """Return :attr:`path` with forward slashes, on every platform."""
        return to_posix(self.path)

    @property
    def modified_at_iso(self) -> str:
        """Return :attr:`modified_at` as an ISO 8601 string."""
        return self.modified_at.isoformat()

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-ready view of this artifact.

        Every value is a primitive, so the result can be serialized as it
        stands: paths become POSIX strings and the timestamp becomes ISO 8601.
        A fresh dictionary is built on each call, so a caller can never reach
        back into an indexed artifact through it.
        """
        return {
            "category": self.category.value,
            "name": self.name,
            "path": self.posix_path,
            "size_bytes": self.size_bytes,
            "modified_at": self.modified_at_iso,
            "content_type": self.content_type,
        }


@dataclass(frozen=True, slots=True)
class ShotCollection:
    """The result of indexing one category's directory.

    Carries the outcome even when there is nothing to carry: a caller reads
    :attr:`status` to tell "never generated" from "generated but empty" from
    "present but unreadable", and reads :attr:`files` for the artifacts
    themselves, in a deterministic order.
    """

    category: ShotCategory
    """The category that was indexed."""

    directory: Path
    """The directory that was inspected, whether or not it exists."""

    status: ShotDirectoryStatus
    """What was found there."""

    files: tuple[ShotFile, ...] = ()
    """The artifacts found, sorted by file name. Empty unless status is available."""

    detail: str = ""
    """Short diagnostic, populated only when :attr:`status` is unreadable."""

    @property
    def exists(self) -> bool:
        """Return whether the inspected path exists in any form."""
        return self.status is not ShotDirectoryStatus.ABSENT

    @property
    def is_readable(self) -> bool:
        """Return whether the directory could actually be listed."""
        return self.status in (ShotDirectoryStatus.EMPTY, ShotDirectoryStatus.AVAILABLE)

    @property
    def is_empty(self) -> bool:
        """Return whether no artifact was indexed, for whatever reason."""
        return not self.files

    @property
    def has_shots(self) -> bool:
        """Return whether at least one artifact was indexed."""
        return self.status is ShotDirectoryStatus.AVAILABLE

    @property
    def count(self) -> int:
        """Return how many artifacts were indexed."""
        return len(self.files)

    @property
    def names(self) -> tuple[str, ...]:
        """Return the artifact file names, in the same order as :attr:`files`."""
        return tuple(shot.name for shot in self.files)

    @property
    def total_size_bytes(self) -> int:
        """Return the combined size of every indexed artifact, in bytes."""
        return sum(shot.size_bytes for shot in self.files)

    @property
    def description(self) -> str:
        """Return a one-line human summary, suitable for the report index.

        The count follows the category name rather than preceding it, so the
        phrasing stays grammatical for one artifact as well as for many while
        the README's own plural wording is quoted unchanged.
        """
        if self.status is ShotDirectoryStatus.AVAILABLE:
            return f"{self.category.label}: {self.count} available"
        if self.status is ShotDirectoryStatus.UNREADABLE:
            return f"{self.category.label} could not be read: {self.detail or 'unknown reason'}"
        return f"{self.category.label} {NOT_GENERATED_DESCRIPTION}"

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-ready view of this collection, artifacts included."""
        return {
            "category": self.category.value,
            "label": self.category.label,
            "trigger": self.category.trigger,
            "directory": to_posix(self.directory),
            "status": self.status.value,
            "exists": self.exists,
            "count": self.count,
            "total_size_bytes": self.total_size_bytes,
            "description": self.description,
            "detail": self.detail,
            "files": [shot.as_dict() for shot in self.files],
        }


@dataclass(frozen=True, slots=True)
class ShotIndex:
    """Both categories side by side: the value the report surface renders.

    The two collections stay separate, with independent counts and independent
    existence flags, because ``[README.md:L42-L43]`` promises them separately
    and under different conditions. Aggregates are offered as well, but never
    as a substitute for the per-category detail.
    """

    screen_shots: ShotCollection
    """The opt-in category, indexed from its one-word directory."""

    error_shots: ShotCollection
    """The failure-driven category, indexed from its hyphenated directory."""

    @property
    def collections(self) -> tuple[ShotCollection, ShotCollection]:
        """Return both collections, screen shots first, error shots second.

        The order matches the order the two capabilities are described in at
        ``[README.md:L42-L43]``, so a rendered index reads like its source.
        """
        return (self.screen_shots, self.error_shots)

    @property
    def total_count(self) -> int:
        """Return how many artifacts were indexed across both categories."""
        return self.screen_shots.count + self.error_shots.count

    @property
    def total_size_bytes(self) -> int:
        """Return the combined size of every indexed artifact, in bytes."""
        return self.screen_shots.total_size_bytes + self.error_shots.total_size_bytes

    @property
    def any_available(self) -> bool:
        """Return whether either category has at least one artifact."""
        return self.screen_shots.has_shots or self.error_shots.has_shots

    def for_category(self, category: ShotCategory) -> ShotCollection:
        """Return the collection belonging to *category*.

        Args:
            category: The category to look up.

        Returns:
            The matching :class:`ShotCollection`.
        """
        if category is ShotCategory.SCREEN_SHOTS:
            return self.screen_shots
        return self.error_shots

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-ready view of the whole index.

        The two categories are nested under their own keys -- never flattened
        into one list -- so a consumer cannot accidentally lose the distinction
        between an opt-in screen shot and a shot taken because a test failed.
        """
        return {
            "screen_shots": self.screen_shots.as_dict(),
            "error_shots": self.error_shots.as_dict(),
            "total_count": self.total_count,
            "total_size_bytes": self.total_size_bytes,
            "any_available": self.any_available,
        }


# =============================================================================
# Name and media-type helpers.
# =============================================================================


def _suffix_of(name: StrPath) -> str:
    """Return the lower-cased final extension of *name*, or an empty string.

    Args:
        name: A file name or path. Only its text is examined; the filesystem is
            not consulted, so the file need not exist.

    Returns:
        The extension including its leading dot, lower-cased so that ``.PNG``
        and ``.png`` are treated alike, or ``""`` when there is no extension or
        the value cannot be read as a path at all.
    """
    try:
        return Path(name).suffix.lower()
    except (TypeError, ValueError) as error:
        # Defensive: this module's inputs arrive from HTTP and from directory
        # listings, so a value that is not usable as a path is treated as having
        # no extension rather than being allowed to raise.
        _LOGGER.debug(
            "Cannot read an extension from %r (%s: %s); treating it as having none",
            name,
            type(error).__name__,
            error,
        )
        return ""


def content_type_for(name: StrPath) -> str:
    """Return the media type to serve *name* with, derived from its extension.

    The bytes are never read. The extension is checked against
    :data:`IMAGE_CONTENT_TYPES` first, and only then is the standard library's
    extension database consulted -- and only its ``image/...`` answers are
    trusted. That order matters: the database is not restricted to images and
    answers for many unrelated extensions, so consulting it first would let a
    stray file be described as though it were an artifact.

    Args:
        name: File name, or any path whose final extension should be inspected.

    Returns:
        The media type for an allow-listed image extension, or
        :data:`FALLBACK_CONTENT_TYPE` for anything else. Never raises.
    """
    suffix = _suffix_of(name)
    expected = IMAGE_CONTENT_TYPES.get(suffix)
    if expected is None:
        return FALLBACK_CONTENT_TYPE

    # A synthetic name is probed rather than the caller's, so that a multi-part
    # name such as `shot.png.gz` cannot influence the answer for the extension
    # that was actually accepted above.
    guessed, _encoding = mimetypes.guess_type(f"artifact{suffix}", strict=False)
    if guessed is not None and guessed.startswith(_IMAGE_TYPE_PREFIX):
        return guessed

    # The database is assembled partly from system files and can be incomplete
    # or unexpected on a given host, so the pinned mapping is authoritative.
    return expected


def is_image_name(name: StrPath) -> bool:
    """Return whether *name* carries an extension this module indexes.

    Args:
        name: File name, or any path whose final extension should be inspected.

    Returns:
        ``True`` only for the extensions in :data:`IMAGE_EXTENSIONS`.
    """
    return _suffix_of(name) in IMAGE_CONTENT_TYPES


def is_safe_name(name: str) -> bool:
    """Return whether *name* is a single, harmless path component.

    Applied to every caller-supplied value that would otherwise be joined onto
    an artifact directory -- an artifact file name or a run identifier taken
    straight out of a URL. The consuming route,
    ``GET /api/v1/reports/<run_id>/screenshots``, serves what this module
    describes, so a value that escaped its directory here would become an
    arbitrary-file disclosure there.

    Rejected: the empty string, anything containing a NUL byte, ``.`` and
    ``..``, anything containing a POSIX or Windows path separator, anything
    absolute, and anything that is not exactly its own final component.

    Args:
        name: The candidate component.

    Returns:
        ``True`` only for a value safe to look up inside an artifact directory.
        Never raises, and never touches the filesystem.
    """
    if not isinstance(name, str) or not name:
        # The isinstance guard is deliberate: values reaching this function
        # originate outside the type system, in a URL or a request body.
        return False
    if "\x00" in name:
        return False
    if name in _RESERVED_NAMES:
        return False
    if any(separator in name for separator in _PATH_SEPARATORS):
        return False
    candidate = Path(name)
    if candidate.is_absolute():
        return False
    # Final guard: a safe value is identical to its own last component, which no
    # multi-part or directory-addressing value can be.
    return candidate.name == name


def shot_directory(category: ShotCategory, base_dir: StrPath | None = None) -> Path:
    """Return the directory *category*'s artifacts live in.

    A module-level spelling of :meth:`ShotCategory.directory`, so a caller does
    not have to reach through an enumeration member to locate a directory.

    Args:
        category: The category to locate.
        base_dir: Directory the artifact root should sit inside, or ``None`` for
            the repository-relative default.

    Returns:
        The directory as declared by ``app/utils/paths.py``. It is never created
        here and may not exist.
    """
    return category.directory(base_dir)


# =============================================================================
# Indexing.
# =============================================================================


def _is_contained(entry: Path, directory: Path) -> bool:
    """Return whether *entry* really lies inside *directory*.

    Both sides are fully resolved before they are compared, so a symbolic link
    is judged by where it actually leads rather than by where it sits. An entry
    resolving outside the directory being listed -- or onto the directory itself
    -- is refused.

    Args:
        entry: A directory entry produced by listing *directory*.
        directory: The artifact directory that entry must stay inside.

    Returns:
        ``True`` only when the entry's real location is strictly inside the
        directory's real location. Any resolution failure yields ``False``: an
        entry that cannot be placed is never served.
    """
    try:
        resolved_entry = entry.resolve()
        resolved_directory = directory.resolve()
    except (OSError, RuntimeError, ValueError) as error:
        # OSError: the filesystem refused. RuntimeError: a symbolic-link loop.
        # ValueError: the path is unusable on this platform.
        _LOGGER.debug(
            "Cannot place %s inside %s (%s: %s); refusing the entry",
            to_posix(entry),
            to_posix(directory),
            type(error).__name__,
            error,
        )
        return False
    if resolved_entry == resolved_directory:
        return False
    return resolved_entry.is_relative_to(resolved_directory)


def _describe(entry: Path, category: ShotCategory) -> ShotFile | None:
    """Build the descriptor for one directory entry, or ``None`` to skip it.

    Args:
        entry: The directory entry to describe.
        category: The category the entry was listed for.

    Returns:
        A :class:`ShotFile`, or ``None`` when the entry is not a regular file or
        its metadata cannot be read. Skipping is logged at debug severity and
        never raises: one unreadable entry must not cost a whole index.
    """
    try:
        # Follows symbolic links on purpose: containment was already established
        # by `_is_contained`, so the metadata wanted here is the target's.
        info = entry.stat()
    except OSError as error:
        _LOGGER.debug(
            "Skipping %s entry %s: metadata unavailable (%s: %s)",
            category.value,
            to_posix(entry),
            type(error).__name__,
            error,
        )
        return None

    if not S_ISREG(info.st_mode):
        # A nested directory, a socket, a device node: not an artifact.
        _LOGGER.debug(
            "Skipping %s entry %s: not a regular file",
            category.value,
            to_posix(entry),
        )
        return None

    try:
        # Timezone-aware and in UTC, so a rendered or serialized timestamp is
        # unambiguous regardless of the host's zone.
        modified_at = datetime.fromtimestamp(info.st_mtime, tz=UTC)
    except (OSError, OverflowError, ValueError) as error:
        _LOGGER.debug(
            "Skipping %s entry %s: modification time out of range (%s: %s)",
            category.value,
            to_posix(entry),
            type(error).__name__,
            error,
        )
        return None

    return ShotFile(
        category=category,
        name=entry.name,
        path=entry,
        size_bytes=info.st_size,
        modified_at=modified_at,
        content_type=content_type_for(entry.name),
    )


def list_shots(category: ShotCategory, base_dir: StrPath | None = None) -> ShotCollection:
    """Index one category's directory, and never raise while doing so.

    Only regular files whose extension is in :data:`IMAGE_EXTENSIONS` are
    indexed. Dot-prefixed entries, nested directories and any other stray file
    are skipped rather than listed, and an entry whose real location falls
    outside the directory is refused outright.

    Results are sorted by file name before they are returned. Directory order is
    whatever the filesystem happens to give and must never reach an HTTP
    response or a rendered page, because two identical requests would then
    disagree.

    Absence is not a failure. A missing directory reports
    :attr:`ShotDirectoryStatus.ABSENT` and an existing but artifact-free
    directory reports :attr:`ShotDirectoryStatus.EMPTY`; both are ordinary and
    neither is logged above debug severity. A path that exists but cannot be
    listed, or that is not a directory at all, reports
    :attr:`ShotDirectoryStatus.UNREADABLE` with a short diagnostic -- that one is
    a real misconfiguration and is worth a warning.

    Args:
        category: Which category to index.
        base_dir: Directory the artifact root sits inside, or ``None`` for the
            repository-relative default.

    Returns:
        A :class:`ShotCollection` describing what was found. Never ``None``, and
        no exception ever escapes.
    """
    directory = shot_directory(category, base_dir)

    if not directory.is_dir():
        if directory.exists():
            detail = "path exists but is not a directory"
            _LOGGER.warning(
                "Cannot index %s: %s is not a directory",
                category.label,
                to_posix(directory),
            )
            return ShotCollection(
                category=category,
                directory=directory,
                status=ShotDirectoryStatus.UNREADABLE,
                detail=detail,
            )
        # The ordinary case on a fresh checkout, and after the clean step: debug
        # only, because there is nothing wrong with having produced no artifact.
        _LOGGER.debug(
            "No %s directory at %s: %s",
            category.label,
            to_posix(directory),
            NOT_GENERATED_DESCRIPTION,
        )
        return ShotCollection(
            category=category,
            directory=directory,
            status=ShotDirectoryStatus.ABSENT,
        )

    try:
        entries = sorted(directory.iterdir(), key=lambda entry: entry.name)
    except OSError as error:
        # OSError covers a refused listing and every other I/O failure.
        detail = f"{type(error).__name__}: {error}"
        _LOGGER.warning(
            "Cannot list %s in %s (%s)",
            category.label,
            to_posix(directory),
            detail,
        )
        return ShotCollection(
            category=category,
            directory=directory,
            status=ShotDirectoryStatus.UNREADABLE,
            detail=detail,
        )

    files: list[ShotFile] = []
    for entry in entries:
        if entry.name.startswith("."):
            # Hidden files are never artifacts: no capture path produces one.
            continue
        if not is_image_name(entry.name):
            continue
        if not _is_contained(entry, directory):
            # Security-relevant, and never legitimate: warn rather than whisper.
            _LOGGER.warning(
                "Refusing %s entry %s: it resolves outside %s",
                category.label,
                entry.name,
                to_posix(directory),
            )
            continue
        described = _describe(entry, category)
        if described is not None:
            files.append(described)

    # The entries were already walked in name order; sorting the descriptors
    # keeps the guarantee local to the value being returned, so it holds however
    # the loop above evolves.
    files.sort(key=lambda shot: shot.name)

    status = ShotDirectoryStatus.AVAILABLE if files else ShotDirectoryStatus.EMPTY
    _LOGGER.debug(
        "Indexed %d %s in %s (%s)",
        len(files),
        category.label,
        to_posix(directory),
        status.value,
    )
    return ShotCollection(
        category=category,
        directory=directory,
        status=status,
        files=tuple(files),
    )


def list_screen_shots(base_dir: StrPath | None = None) -> ShotCollection:
    """Index the opt-in screen shots -- ``[README.md:L42]``.

    Args:
        base_dir: Directory the artifact root sits inside, or ``None`` for the
            repository-relative default.

    Returns:
        The screen-shot collection. Empty by default, because the committed
        configuration templates ship this capture disabled.
    """
    return list_shots(ShotCategory.SCREEN_SHOTS, base_dir)


def list_error_shots(base_dir: StrPath | None = None) -> ShotCollection:
    """Index the failure-driven error shots -- ``[README.md:L43]``.

    Args:
        base_dir: Directory the artifact root sits inside, or ``None`` for the
            repository-relative default.

    Returns:
        The error-shot collection. Empty until a test case has failed, and
        indexable after a failing run: the report stage runs unconditionally,
        which is what preserved defect **D3** requires.
    """
    return list_shots(ShotCategory.ERROR_SHOTS, base_dir)


def index_shots(base_dir: StrPath | None = None) -> ShotIndex:
    """Index both categories in one pass.

    This is the entry point the report surface uses: ``app/web/routes.py``
    renders it and ``app/api/routes.py`` answers
    ``GET /api/v1/reports/<run_id>/screenshots`` from it.

    Args:
        base_dir: Directory the artifact root sits inside, or ``None`` for the
            repository-relative default.

    Returns:
        A :class:`ShotIndex` holding both collections, each with its own count
        and its own existence flag. Never raises: an entirely absent artifact
        tree yields an index of two absent collections.
    """
    index = ShotIndex(
        screen_shots=list_screen_shots(base_dir),
        error_shots=list_error_shots(base_dir),
    )
    _LOGGER.debug(
        "Shot index: %d screen shots (%s), %d error shots (%s)",
        index.screen_shots.count,
        index.screen_shots.status.value,
        index.error_shots.count,
        index.error_shots.status.value,
    )
    return index


def resolve_shot(
    category: ShotCategory,
    name: str,
    base_dir: StrPath | None = None,
) -> ShotFile | None:
    """Resolve one caller-supplied artifact name, safely, or return ``None``.

    Two guards apply in order. The name must first pass
    :func:`is_safe_name`, which rejects traversal, absolute paths and NUL bytes
    without ever touching the filesystem. It is then matched against the
    directory listing itself, so the descriptor handed back is always one
    :func:`list_shots` already accepted -- same allow-list, same containment
    check, same refusal of a link leading elsewhere. A caller's string is never
    joined onto a directory and handed straight back.

    Args:
        category: Which category to look in.
        name: The artifact file name, typically taken from a URL.
        base_dir: Directory the artifact root sits inside, or ``None`` for the
            repository-relative default.

    Returns:
        The matching :class:`ShotFile`, or ``None`` when the name is unsafe, the
        directory is absent or unreadable, or no such artifact exists. The
        caller decides what that means in HTTP terms; this module raises nothing
        and sets no status code.
    """
    if not is_safe_name(name):
        # Security-relevant, so it is recorded at warning severity. The value is
        # logged with %r, which escapes control characters rather than emitting
        # them into the log.
        _LOGGER.warning(
            "Refusing unsafe %s artifact name %r",
            category.label,
            name,
        )
        return None

    collection = list_shots(category, base_dir)
    for shot in collection.files:
        if shot.name == name:
            return shot

    _LOGGER.debug(
        "No %s artifact named %r in %s (%s)",
        category.label,
        name,
        to_posix(collection.directory),
        collection.status.value,
    )
    return None
