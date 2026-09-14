r"""Java ``.properties`` reader - the Python port of ``ConfigurationReader``.

The only reader of ``configuration.properties`` in the port (AAP 0.4.2),
reproducing the read surface of ``com.testinium.utilities.ConfigurationReader``
(``ConfigurationReader.java:11-25``):

* **The file is named by a bare relative filename.**  ``ConfigurationReader:14``
  opens ``new FileInputStream("configuration.properties")``, so the *process
  working directory* - not this package's location, and not an absolute path -
  decides which file is read.  ``.gitignore`` keeps that file untracked; the
  committed template is ``configuration.properties.example``.
* **The load happens once.**  ``ConfigurationReader:11`` performs it in a
  static initializer.  In the port the load happens once *per worker process*
  rather than once per JVM, because a process pool has no shared static
  initializer; that difference is deviation 17 of the AAP section 0.1.3
  inventory, and it changes only the *number* of load events.  Everything else
  about the one-shot behaviour matches the JVM exactly: once a reader is
  initialized it never re-reads, so a file that changes - or appears - mid-run
  does not affect it.
* **A missing file is tolerated, never fatal.**  ``ConfigurationReader:21-24``
  prints ``File is not found in the ConfigurationReader class`` plus a stack
  trace and lets the static initializer complete.  The port logs that exact
  message once per process at ``WARNING`` with the traceback attached, and
  carries on with an empty mapping.  A file that is absent, is a directory or
  cannot be read therefore never raises here.
* **A malformed file is fatal, exactly as in Java.**  That tolerance is
  ``ConfigurationReader``'s ``catch (IOException)`` and nothing wider.  JDK 8
  ``Properties.loadConvert`` throws
  ``IllegalArgumentException("Malformed \uxxxx encoding.")`` on a bad
  ``\uXXXX`` escape (``Properties.java:575``), which that ``catch`` does not
  intercept, so the static initializer fails and the class stays permanently
  unusable.  The port raises :class:`ValueError` - Python's analogue of that
  exception - carrying the same message, and once a load has failed every
  later :func:`get_properties` call raises it again without re-reading the
  file.  The message is **fixed and data-free**: no key, no value and no digit
  from the file reaches it or any log record, ``password`` being one of the six
  configured keys.
* **An absent key reads as "no value".**  ``ConfigurationReader:27-29`` returns
  ``null``; :func:`get_property` returns ``None``.  Configuration problems
  therefore surface at the point of use rather than at start-up, which is the
  behaviour the rest of the suite is written against.
* **The read itself is object-bound, bounded and owner-only.**  The file this
  module opens carries the ``username`` and ``password`` of the system under
  test, and it lives in a checkout other local accounts may be able to
  traverse, so ``new FileInputStream`` is *not* ported literally.
  :func:`load_properties` opens one descriptor with ``O_NOFOLLOW`` and
  verifies the object *through that descriptor* - regular file, a single hard
  link, owned by the current account, no group or other permission bits, and
  within :data:`_MAX_FILE_BYTES` - then reads a bounded number of bytes and
  parses under the entry, key, value and line caps below.  A file that fails
  any of those checks is **refused**: one ``WARNING`` naming the reason and the
  filename, and an empty mapping, which is behaviourally identical to the
  absent-file case the paragraph above describes, so the tolerance survives.
  That is review findings ``SEC2-F10`` (CWE-400/22: the previous
  ``Path.read_bytes()`` followed a final-component symlink, opened FIFOs and
  devices, and read an unlimited number of bytes once per worker) and
  ``SEC2-F33`` (CWE-732/522: it inspected neither ownership nor mode).
* **Windows gets the same guarantee, through Win32 rather than through
  ``O_NOFOLLOW`` and the mode bits.**  AAP section 0.8 supports Windows,
  Linux and macOS, and ``scripts/run_tests.ps1`` is a first-class runner, so
  both findings have to hold there too - and they did not while the anti-link
  defence was an ``lstat`` before the open and the ownership and permission
  checks sat behind a POSIX capability flag.  On Windows
  :func:`_open_windows_verified_descriptor` opens the file with
  ``CreateFileW`` and ``FILE_FLAG_OPEN_REPARSE_POINT``, refuses a reparse
  point, verifies through ``GetSecurityInfo`` that the owner SID is the
  running account's and that no access-allowed ACE names a trustee outside
  ``{owner, LocalSystem, BUILTIN\Administrators}`` - the 0600 equivalent -
  and only then converts the handle to a descriptor, so the regular-file,
  hard-link, size and bounded-read checks are literally the same code on both
  platforms.  Every verification is asked of the handle, never of the path, so
  there is no check-then-use window on either.

The grammar is implemented by hand because ``configparser`` cannot parse this
format (AAP 0.6): it rejects a section-less file outright, while the real key
set includes dotted names such as ``web.table.url``.  Separators are ``=``,
``:`` or whitespace, a trailing backslash continues a line, ``#`` and ``!``
start a comment, ``\uXXXX`` escapes are decoded, and the default encoding is
ISO-8859-1.  The three private helpers mirror the JDK 8 ``LineReader``,
``loadConvert`` and ``load0`` state machines one for one.

Only the standard library is imported (AAP 0.4.2), so a worker that never
builds a Flask application can import this.  ``app/config.py`` is the only
permitted importer, and owns the key names, defaults, accessors and the
behave-userdata precedence that do not belong in a generic reader.
"""

import errno
import logging
import os
import stat
import threading
from collections.abc import Iterator, Mapping
from pathlib import Path
from types import MappingProxyType
from typing import NamedTuple

__all__ = [
    "DEFAULT_ENCODING",
    "MISSING_FILE_MESSAGE",
    "PROPERTIES_FILENAME",
    "get_properties",
    "get_property",
    "load_properties",
    "parse_properties",
]

logger = logging.getLogger(__name__)

#: The bare relative filename opened by ``ConfigurationReader:14``.  Resolved
#: against the process working directory every time it is used, never captured
#: at import time.
PROPERTIES_FILENAME = "configuration.properties"

#: The missing-file message from ``ConfigurationReader:22``, preserved verbatim.
#: This string is parity: no prefix, no suffix, no added punctuation.
MISSING_FILE_MESSAGE = "File is not found in the ConfigurationReader class"

# The message JDK 8 ``Properties.loadConvert`` gives its
# ``IllegalArgumentException`` (``Properties.java:575``), preserved verbatim -
# lower-case ``uxxxx``, trailing period, one leading backslash.  Deliberately
# *fixed and data-free*: it names neither the offending key, nor its value, nor
# the digits that were read, nor the file, because one of the six configured
# keys is ``password`` and a malformed line could otherwise put a fragment of a
# credential into an exception message or a log record.  Private, because it is
# an implementation detail of the parity rather than a name callers compose
# with; the raise sites below are the only users.
_MALFORMED_ESCAPE_MESSAGE = r"Malformed \uxxxx encoding."

#: The encoding ``java.util.Properties.load(InputStream)`` applies on Java 8,
#: which ``pom.xml:12-13`` pins as both source and target level.  ISO-8859-1
#: maps all 256 byte values, so decoding a properties file cannot fail.
DEFAULT_ENCODING = "iso-8859-1"

# The whitespace class the JDK's own scanners use.  Deliberately *not*
# ``str.isspace()``: Java treats exactly space, tab and form feed as
# key/value whitespace, and handles CR and LF separately as line terminators.
_WHITESPACE = (" ", "\t", "\f")

# Single-character escapes that map to a control character.  Every *other*
# ``\c`` sequence yields ``c`` with the backslash dropped, which is Java's rule
# rather than an error - see :func:`_decode_escapes`.
_CONTROL_ESCAPES = {
    "t": "\t",
    "r": "\r",
    "n": "\n",
    "f": "\f",
}

_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")

# A ``\uXXXX`` escape takes exactly this many hexadecimal digits.  Fewer than
# this before the end of the slice, or a non-hexadecimal digit among them, is
# the malformed case :func:`_decode_escapes` documents.
_UNICODE_ESCAPE_DIGITS = 4


# --------------------------------------------------------------------------- #
# The secure-read contract - review findings SEC2-F10 and SEC2-F33.
#
# Everything in this block is private.  ``__all__`` above is the module's whole
# published surface and stays frozen: ``app/utils/__init__.py`` re-exports those
# names statically and ``tests/test_properties.py`` asserts the tuple exactly,
# so a hardening measure may not widen it.  None of these values is
# configurable, deliberately - a limit a caller can raise is a limit an
# attacker-controlled environment can raise.
# --------------------------------------------------------------------------- #

# The message a *refused* file logs.  Deliberately **not equal** to
# :data:`MISSING_FILE_MESSAGE`: that string is parity with
# ``ConfigurationReader:22`` and is reserved for the three I/O faults the Java
# ``catch (IOException)`` covers, so a refusal must be distinguishable from an
# absent file in the log and in any record count taken over it.  Private,
# because it is not a parity string a caller composes with.
_REFUSED_FILE_MESSAGE = (
    "Configuration file refused as unsafe in the ConfigurationReader class"
)

# The refusal reasons, each a fixed, data-free phrase.  Same hygiene as
# :data:`_MALFORMED_ESCAPE_MESSAGE` and for the same reason: ``password`` is one
# of the six configured keys, so no key, no value, no byte count and no measured
# length from the file may reach a message or a log record.  The reason names the
# *class* of refusal and nothing about the data that provoked it.
_REASON_NOT_A_REGULAR_FILE = "not a regular file"
_REASON_LINK = "a symbolic link or reparse point"
_REASON_MULTIPLE_HARD_LINKS = "more than one hard link"
_REASON_FOREIGN_OWNER = "not owned by the account running this process"
_REASON_OPEN_PERMISSIONS = "accessible beyond its owner"
# The fail-closed reason.  Used where a platform's own security query cannot be
# completed or cannot be decoded - a Win32 call that reports failure, an ACE
# whose layout this module does not parse, a ``ctypes`` fault - so that neither
# ownership nor protection could be *established*.  A distinct phrase rather
# than one of the two above, because "this could not be determined" is a
# different operational fact from "this was determined, and it was wrong", and
# the one refusal record has to say which of the two happened.
_REASON_UNVERIFIABLE_OBJECT = "ownership and protection that could not be verified"
_REASON_TOO_LARGE = "larger than the permitted maximum"
_REASON_TOO_MANY_ENTRIES = "more entries than the permitted maximum"
_REASON_KEY_TOO_LONG = "a key longer than the permitted maximum"
_REASON_VALUE_TOO_LONG = "a value longer than the permitted maximum"
_REASON_LINE_TOO_LONG = "a logical line longer than the permitted maximum"

# Stands in for the filename in a refusal record when the target has no final
# component at all (``Path("/")``).  A fixed placeholder rather than the path
# itself, because a refusal record must never carry an absolute path.
_UNNAMED_TARGET = "<unnamed>"

# The total size accepted, in bytes.  A six-key properties file is a few hundred
# bytes; 64 KiB leaves room for comments and a long URL while keeping the worst
# case per worker process trivial, which matters because the load happens once
# per worker rather than once per run (AAP deviation 17).
_MAX_FILE_BYTES = 65536

# The parse caps.  The value cap is the one that bites on the 2 MiB
# single-value probe SEC2-F10 reports when that probe arrives inside a file
# small enough to pass :data:`_MAX_FILE_BYTES`; the others bound the remaining
# shapes a hostile file can take within the same total size.
_MAX_ENTRIES = 512
_MAX_KEY_CHARACTERS = 512
_MAX_VALUE_CHARACTERS = 4096
_MAX_LINE_CHARACTERS = 8192

# The mode bits that must be clear: every group and other permission, which is
# the "0600-equivalent" ``SEC2-F33`` requires for a credential-bearing file.
_FORBIDDEN_MODE_BITS = 0o077

# One read syscall's request size.  Equal to the total cap so a conforming file
# is read in one call, while the loop in :func:`_read_bounded` still terminates
# correctly on a short read.
_READ_CHUNK_BYTES = _MAX_FILE_BYTES

# ``open(2)`` refuses a final-component symlink with ``ELOOP`` on Linux and
# ``EMLINK`` on the BSDs.  Both are routed to the refusal path rather than to
# the missing-file path, because a link in place of the configuration is a
# deliberate act rather than an I/O fault.
_LINK_ERRNOS = frozenset(
    value
    for value in (getattr(errno, name, None) for name in ("ELOOP", "EMLINK"))
    if value is not None
)

# The flags the one descriptor is opened with, assembled once.
#
# ``O_NOFOLLOW`` is the whole of the anti-symlink defence on POSIX and it is
# race-free: the *kernel* refuses the open, so there is no window between a
# check and a use.  ``O_NONBLOCK`` is load-bearing rather than decoration -
# without it ``os.open`` on a FIFO blocks forever waiting for a writer and the
# ``fstat`` rejection in :func:`_open_verified_descriptor` is never reached; it
# has no effect on a regular file, which is the only kind that survives that
# rejection.  ``O_CLOEXEC`` keeps the credential descriptor out of the behave
# worker processes this module's caller spawns.  ``O_BINARY`` is a Windows-only
# flag and absent elsewhere, hence the ``getattr`` guards - each one is 0 where
# the platform does not define it.
_OPEN_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_NONBLOCK", 0)
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_BINARY", 0)
)

# Whether the kernel enforces the no-follow rule for us.  Where it does not
# (Windows), :func:`_refuse_a_link_before_opening` does a best-effort
# pre-open check instead.
_O_NOFOLLOW_AVAILABLE = hasattr(os, "O_NOFOLLOW")

# Whether POSIX ownership and permission bits are meaningful here.  ``geteuid``
# is absent on Windows, where ``st_uid`` is always 0 and ``st_mode`` carries no
# ACL information, so these two checks are skipped there rather than faked -
# and the Windows branch selected by :data:`_WINDOWS_PLATFORM` below performs
# the *equivalent* checks against the object's real owner SID and DACL instead.
# The two are alternatives, never an opt-out: a platform that offers neither is
# refused in :func:`_open_verified_descriptor`.
_POSIX_IDENTITY_AVAILABLE = hasattr(os, "geteuid")

# The Windows file attribute marking a reparse point (a junction or a symlink),
# and the ``os.stat_result`` field carrying it.  Both are absent on POSIX, so
# both are resolved through ``getattr`` at the point of use.  This one feeds the
# ``lstat`` fallback in :func:`_refuse_a_link_before_opening` only; the Windows
# branch masks with the literal :data:`_WINDOWS_ATTRIBUTE_REPARSE_POINT`
# instead.  Two reasons, neither cosmetic: this name's ``getattr`` default is 0
# and a mask of 0 detects nothing, and this name is *driven by the tests* that
# simulate the fallback on a POSIX host - so a value a Win32 call is judged by
# may not share it.  (CPython in fact defines ``stat.FILE_ATTRIBUTE_*`` on every
# platform even though the documentation presents them as Windows-only, which is
# why the fallback's own tests can exercise it here at all.)
_FILE_ATTRIBUTE_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)


# --------------------------------------------------------------------------- #
# The Windows half of the secure-read contract - review findings SEC2-F33 (the
# non-POSIX branch) and SEC2-F10 (the non-POSIX no-follow half).
#
# AAP section 0.8 states that platform support is Windows, Linux and macOS, and
# ``scripts/run_tests.ps1`` is a first-class runner, so both findings have to
# hold on Windows and not merely on POSIX.  They did not: the two ``fstat``
# checks that implement ownership and 0600-equivalence sit behind
# ``_POSIX_IDENTITY_AVAILABLE``, which is false there, and the anti-link
# defence was an ``lstat`` *before* the open, with a TOCTOU window a junction
# swapped in afterwards would pass straight through.
#
# What replaces them is one object-bound path: ``CreateFileW`` with
# ``FILE_FLAG_OPEN_REPARSE_POINT`` binds a handle to the object itself, the
# reparse classification and the owner/DACL verification are then asked of
# *that handle*, and only afterwards is the handle turned into a descriptor and
# handed to the same ``fstat`` checks and the same bounded read the POSIX path
# uses.  Nothing re-resolves the path after the open, so there is no window.
#
# Every value below is a WinNT.h / winbase.h literal.  They are written out
# here rather than borrowed from the ``stat`` or ``msvcrt`` re-exports for two
# reasons: most of them have no re-export at all, and the two that do -
# ``FILE_ATTRIBUTE_REPARSE_POINT`` and ``FILE_ATTRIBUTE_DIRECTORY`` - are read
# elsewhere in this module through a ``getattr`` whose default is 0, and a mask
# that silently became 0 would disable the check that masks with it while every
# other test still passed.  ``tests/test_properties.py`` asserts each value,
# which is the one part of this Win32 layer a POSIX host can verify.
# --------------------------------------------------------------------------- #

# The platform marker the dispatch in :func:`_open_object_bound_descriptor`
# reads.  ``os.name`` rather than ``sys.platform`` because it is the coarse
# "is this the Win32 API" question being asked, and a module-level name rather
# than an inline test so that ``tests/test_properties.py`` can select the
# branch on a POSIX host, where the Win32 calls themselves cannot run.
_WINDOWS_PLATFORM = os.name == "nt"

# ``dwDesiredAccess``/``dwShareMode``/``dwCreationDisposition`` for the open.
# ``GENERIC_READ`` implies ``READ_CONTROL`` (it maps to ``FILE_GENERIC_READ``,
# which includes ``STANDARD_RIGHTS_READ``), which is the access right
# ``GetSecurityInfo`` needs to return the owner SID and the DACL - so no extra
# access is requested for the security query.  ``FILE_SHARE_READ`` alone: a
# concurrent writer is a reason to fail the open, not to read a file mid-write.
_WINDOWS_GENERIC_READ = 0x80000000
_WINDOWS_FILE_SHARE_READ = 0x00000001
_WINDOWS_OPEN_EXISTING = 3

# ``dwFlagsAndAttributes``.  ``FILE_FLAG_OPEN_REPARSE_POINT`` is the whole of
# the anti-link defence and it is race-free in the same sense ``O_NOFOLLOW`` is:
# the handle comes back bound to the reparse point itself rather than to its
# target, so the classification that follows cannot be raced.
# ``FILE_FLAG_BACKUP_SEMANTICS`` is what makes a *directory* in the file's place
# yield a handle that can be classified, instead of an opaque
# ``ERROR_ACCESS_DENIED`` that would be indistinguishable from a permission
# fault - the directory case has to reach the missing-file path byte for byte.
_WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_WINDOWS_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000

# ``FILE_INFO_BY_HANDLE_CLASS.FileAttributeTagInfo`` and the size of the
# ``FILE_ATTRIBUTE_TAG_INFO`` it fills: two ``DWORD``s, ``FileAttributes`` then
# ``ReparseTag``.  Only the first is read, and only two of its bits.
_WINDOWS_FILE_ATTRIBUTE_TAG_INFO = 9
_WINDOWS_FILE_ATTRIBUTE_TAG_INFO_BYTES = 8

# The two attribute bits that decide what the handle is bound to.  Literals for
# the reason the block comment above gives.
_WINDOWS_ATTRIBUTE_REPARSE_POINT = 0x00000400
_WINDOWS_ATTRIBUTE_DIRECTORY = 0x00000010

# ``SE_OBJECT_TYPE.SE_FILE_OBJECT`` and the two ``SECURITY_INFORMATION`` bits
# asked for: the owner, to compare against the running account, and the DACL,
# to establish that nobody else is granted access.  The group SID and the SACL
# are deliberately not requested - neither participates in the decision, and
# reading the SACL needs a privilege this process has no reason to hold.
_WINDOWS_SE_FILE_OBJECT = 1
_WINDOWS_OWNER_SECURITY_INFORMATION = 0x00000001
_WINDOWS_DACL_SECURITY_INFORMATION = 0x00000004

# ``TOKEN_QUERY`` and ``TOKEN_INFORMATION_CLASS.TokenUser``: how the running
# account's own SID is obtained, so that "owned by me" is decided against the
# process token rather than against a name that could be re-pointed.
_WINDOWS_TOKEN_QUERY = 0x0008
_WINDOWS_TOKEN_USER = 1

# An upper bound on the ``TOKEN_USER`` buffer.  ``GetTokenInformation`` reports
# the size it wants and that size is honoured, but only within this bound: the
# value arrives from an API call and an allocation driven by an unbounded
# reported length is the same class of exposure SEC2-F10 is about.  A
# ``TOKEN_USER`` is a pointer plus a ``DWORD`` plus one SID, so 1 KiB is orders
# of magnitude of headroom.
_WINDOWS_MAX_TOKEN_USER_BYTES = 1024

# ``ACL_INFORMATION_CLASS.AclSizeInformation`` and the size of the
# ``ACL_SIZE_INFORMATION`` it fills: three ``DWORD``s, ``AceCount`` first.
_WINDOWS_ACL_SIZE_INFORMATION = 2
_WINDOWS_ACL_SIZE_INFORMATION_BYTES = 12

# The ``ACE_HEADER`` prefix every ACE starts with - ``AceType``, ``AceFlags``,
# ``AceSize`` - and, for the four ACE types that share the simple
# ``{ ACE_HEADER, ACCESS_MASK, DWORD SidStart }`` layout, the offset at which
# that trustee SID begins: 4 header bytes plus a 4-byte access mask.
_WINDOWS_ACE_HEADER_BYTES = 4
_WINDOWS_SIMPLE_ACE_SID_OFFSET = 8

# An upper bound on the number of ACEs walked.  An ``ACL`` is at most 64 KiB
# and the smallest ACE is 12 bytes, so no valid DACL can reach this - it exists
# so that the walk is bounded by a fixed value of this module's own rather than
# by a count read out of the structure being inspected, which is the same
# discipline the read caps above apply to the file's own bytes.  Exceeding it is
# a refusal, like every other thing that cannot be decoded.
_WINDOWS_MAX_ACE_COUNT = 8192

# ``ACCESS_ALLOWED_ACE_TYPE`` and the set of ACE types whose trustee SID this
# module knows how to locate: allowed (0), denied (1), audit (2) and alarm (3).
# Only type 0 grants anything, so only type 0 contributes a trustee to the
# decision; 1 to 3 are read past.  Anything *else* - an object ACE (5 to 8),
# a compound ACE (4), a callback or conditional ACE (9 upwards) - has a
# different layout, so its trustee cannot be located here and the file is
# refused rather than being accepted on an ACE nobody parsed.
_WINDOWS_ACCESS_ALLOWED_ACE_TYPE = 0
_WINDOWS_SIMPLE_ACE_TYPES = frozenset({0, 1, 2, 3})

# ``WELL_KNOWN_SID_TYPE`` values for the two principals that already hold
# administrative access to every file on the machine: ``WinLocalSystemSid``
# (``LocalSystem``, S-1-5-18) and ``WinBuiltinAdministratorsSid``
# (``BUILTIN\Administrators``, S-1-5-32-544).  An ACE naming either of them
# grants nothing that account does not already have, so both are
# owner-equivalent for this decision and neither makes a file "accessible
# beyond its owner".  Obtained through ``CreateWellKnownSid`` rather than by
# parsing a string form, so no textual SID ever has to be trusted.
_WINDOWS_OWNER_EQUIVALENT_SID_TYPES = (22, 26)

# ``SECURITY_MAX_SID_SIZE``.  The bound on every SID copied out of a Win32
# structure, and the size of the buffer ``CreateWellKnownSid`` fills.
_WINDOWS_SECURITY_MAX_SID_SIZE = 68

# ``ERROR_SUCCESS``: the only ``GetSecurityInfo`` return value that is not a
# refusal.  ``GetSecurityInfo`` returns a Win32 error code directly rather than
# setting the last-error value, which is why it is compared rather than
# routed through ``ctypes.WinError``.
_WINDOWS_ERROR_SUCCESS = 0


class _RefusedConfigurationError(ValueError):
    r"""A configuration file was rejected by the secure-read contract.

    **Private, and narrower than ``ValueError`` on purpose.**  The one other
    ``ValueError`` this module raises is the malformed-``\uXXXX`` failure
    carrying :data:`_MALFORMED_ESCAPE_MESSAGE`, which is parity with JDK 8 and
    **must keep propagating** out of :func:`load_properties` and
    :func:`get_properties`.  A refusal must not: it is the tolerant path, and
    :func:`load_properties` absorbs it into an empty mapping.  Catching a
    dedicated subclass is what keeps those two outcomes separable - an
    ``except ValueError`` there would swallow the parity failure as well.

    ``ValueError`` rather than a fresh exception hierarchy because a refusal
    *is* a rejection of the file's value, and because a direct caller of
    :func:`parse_properties` - the only way this class is observable outside
    this module - already handles ``ValueError`` from that function.

    :param reason: One of the fixed ``_REASON_*`` phrases.  It is the whole of
        the exception's payload: no key, no value and no length measured from
        the file is carried, because ``password`` is one of the six configured
        keys.
    """

    def __init__(self, reason: str) -> None:
        """Record the fixed reason phrase as both message and attribute."""
        super().__init__(reason)
        #: The fixed ``_REASON_*`` phrase, read by :func:`_log_refusal`.
        self.reason = reason


def _log_refusal(target: Path, reason: str) -> None:
    """Emit the single ``WARNING`` that a refusal produces.

    Exactly one record, carrying the fixed reason phrase and the target's
    **final path component** - never an absolute path, never a byte of the
    file's content, and deliberately without ``exc_info``: the traceback of an
    ``OSError`` embeds the full path it was raised for, which is the workspace
    topology disclosure this module is not permitted to make.

    The message is :data:`_REFUSED_FILE_MESSAGE`, which is not
    :data:`MISSING_FILE_MESSAGE`, so a refusal is never counted as a missing
    file by a reader of the log.

    :param target: The path that was refused.
    :param reason: One of the fixed ``_REASON_*`` phrases.
    """
    logger.warning(
        "%s (reason: %s; file: %s)",
        _REFUSED_FILE_MESSAGE,
        reason,
        target.name or _UNNAMED_TARGET,
    )


def _refuse_a_link_before_opening(target: Path) -> None:
    """Refuse a link on a platform whose ``open`` cannot.

    A no-op wherever ``O_NOFOLLOW`` exists - which is every POSIX platform this
    port runs on - because there the kernel enforces the rule during the open
    itself and a pre-open check would be both redundant and weaker.

    **Windows does not reach this function.**  It used to, and that was review
    finding ``SEC2-F10``'s non-POSIX half: an ``lstat`` before an open is a
    check-then-use, and a junction swapped in between the two was followed,
    with the later ``fstat`` then describing the target rather than the link.
    Windows now goes through :func:`_open_windows_verified_descriptor`, where
    ``FILE_FLAG_OPEN_REPARSE_POINT`` binds the handle to the object itself and
    the classification is asked of the handle, so there is no window at all.

    What is left here is the fallback for a platform that has *neither* -
    neither ``O_NOFOLLOW`` nor the Win32 API - which is no platform AAP
    section 0.8 supports, and it is kept for exactly that reason: were such a
    platform to appear, a best-effort refusal of a link is better than none.
    Its residual TOCTOU window is documented rather than hidden, and no
    supported platform depends on it: ``tests/test_properties.py`` reaches it
    by driving the capability flags, which is the only way it runs at all.

    :param target: The path about to be opened.
    :raises _RefusedConfigurationError: If ``target`` is a link or a reparse
        point.
    :raises OSError: If ``target`` cannot be ``lstat``-ed at all - absent, or
        with an unreadable parent.  Left to propagate so that it reaches
        :func:`load_properties`' missing-file path unchanged.
    """
    if _O_NOFOLLOW_AVAILABLE:
        return

    status = os.lstat(target)
    if stat.S_ISLNK(status.st_mode):
        raise _RefusedConfigurationError(_REASON_LINK)
    if getattr(status, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT:
        raise _RefusedConfigurationError(_REASON_LINK)


class _WindowsSecurityFacts(NamedTuple):
    r"""What was read off a Windows handle, before any decision is taken.

    A deliberately inert carrier: four already-extracted facts, no handle, no
    pointer and no Win32 call.  It exists so that the *policy* -
    :func:`_classify_windows_protection` - is a pure function of facts and can
    be tested on a POSIX host, where none of the Win32 calls that produce them
    will run.  :class:`_WindowsObjectBinding` is the only producer.

    Every SID is carried as the **bytes of its binary form**, copied out of the
    structure it was read from under :data:`_WINDOWS_SECURITY_MAX_SID_SIZE`.
    Comparing those copies with ``==`` is the same predicate ``EqualSid``
    computes - it compares the revision, the identifier authority and every
    subauthority, which is the whole of what ``GetLengthSid`` measures - and
    :meth:`_WindowsObjectBinding._sid_token` proves each copy faithful by
    calling ``EqualSid`` on the copy against the pointer it came from before
    handing it over.  So byte equality here is an ``EqualSid`` comparison that
    has been moved off the Win32 layer, which is what makes the policy pure.

    The defaults describe "nothing was established" and are refused by the
    classifier, so a partially built instance fails closed rather than passing
    a check by omission.

    :param owner: The object's owner SID, or ``None`` if it could not be read.
    :param account: The running account's own SID, from the process token, or
        ``None`` if it could not be read.
    :param owner_equivalent: The SIDs that are owner-equivalent for this
        decision - ``LocalSystem`` and ``BUILTIN\Administrators``, which
        already hold administrative access to every file on the machine.  An
        empty tuple is not a fault: it only makes the decision stricter.
    :param allowed_trustees: One entry per access-allowed ACE in the DACL, in
        DACL order, or ``None`` where the object has **no** DACL at all - a
        NULL DACL, which grants every account full access.
    """

    owner: bytes | None = None
    account: bytes | None = None
    owner_equivalent: tuple[bytes, ...] = ()
    allowed_trustees: tuple[bytes, ...] | None = None


def _classify_windows_protection(facts: _WindowsSecurityFacts) -> str | None:
    r"""Decide whether a Windows object's owner and DACL are acceptable.

    **The 0600 equivalent, and the whole of the Windows policy.**  A pure
    function of :class:`_WindowsSecurityFacts`: no handle, no pointer, no
    platform call, so it is exercised directly by ``tests/test_properties.py``
    on this POSIX host with fabricated SIDs, which is the only place the
    decision *can* be tested here.  What cannot be tested on a POSIX host is
    the extraction that feeds it - see :class:`_WindowsObjectBinding`.

    The rules, in the order they are applied:

    * Neither the owner nor the running account established - refuse as
      :data:`_REASON_UNVERIFIABLE_OBJECT`.  Fail closed: an unknown owner is
      not a matching owner.
    * Owner is not the running account - refuse as
      :data:`_REASON_FOREIGN_OWNER`.  This is ``SEC2-F33``'s ownership half,
      and it is the Windows counterpart of the POSIX ``st_uid`` check.
    * No DACL, or a DACL with no access-allowed ACE - refuse as
      :data:`_REASON_OPEN_PERMISSIONS`.  A NULL DACL grants every account full
      access, which is strictly worse than 0644.  A DACL that grants nobody and
      yet yielded a readable handle means the access came from somewhere the
      DACL does not describe - a privilege such as ``SeBackupPrivilege`` - so
      the DACL is no evidence of an owner-only grant either.  Neither can be
      accepted.
    * Any access-allowed ACE naming a trustee outside the owner-equivalent set
      - refuse as :data:`_REASON_OPEN_PERMISSIONS`.  That set is the owner
      itself plus :data:`_WINDOWS_OWNER_EQUIVALENT_SID_TYPES`, so the accepted
      shape is exactly "the owner, and the two principals that already
      administer every file on the machine" - the Windows analogue of a 0600
      file, and a ``Users`` or ``Everyone`` ACE is the analogue of the 0644 one
      the finding reports.

    ACE *flags* are not consulted, deliberately.  An inherit-only ACE grants
    nothing on the file itself, so exempting it would be defensible - but a
    false refusal costs an operator one ``icacls`` command, while a false
    acceptance costs the credentials, so the stricter reading is taken.

    The remedy for a refusal, which the docstring of
    :func:`_verify_windows_owner_and_dacl` repeats where an operator reading a
    traceback will find it::

        icacls configuration.properties /inheritance:r /grant:r "%USERNAME%":R

    :param facts: The extracted owner, account, owner-equivalent and
        access-allowed-trustee SIDs.
    :returns: One of the fixed ``_REASON_*`` phrases, or ``None`` if the object
        is acceptable.
    """
    if not facts.owner or not facts.account:
        return _REASON_UNVERIFIABLE_OBJECT

    if facts.owner != facts.account:
        return _REASON_FOREIGN_OWNER

    # ``None`` (no DACL) and ``()`` (a DACL granting nobody) are both refused,
    # for the two different reasons the docstring gives.
    if not facts.allowed_trustees:
        return _REASON_OPEN_PERMISSIONS

    permitted = {facts.owner, *facts.owner_equivalent}
    for trustee in facts.allowed_trustees:
        if trustee not in permitted:
            return _REASON_OPEN_PERMISSIONS

    return None


class _WindowsObjectBinding:
    r"""The Win32 layer of the Windows object-bound configuration read.

    Four operations, each one asked of a **handle** rather than of a path:
    open the object without following a link, classify what the handle is bound
    to, read the owner SID and the DACL off it, and finally turn it into a file
    descriptor.  :func:`_open_windows_verified_descriptor` sequences them and
    owns every decision; this class performs no policy of its own beyond
    refusing what it cannot parse.

    **Not exercisable on a POSIX host.**  ``ctypes.WinDLL``, ``CreateFileW``,
    ``GetSecurityInfo`` and ``msvcrt`` do not exist here, so no test in
    ``tests/test_properties.py`` instantiates this class.  What the tests do
    instead is replace :func:`_windows_object_binding` with a stand-in that
    offers the same four operations over an ordinary POSIX descriptor, which
    exercises the sequencing, the classification, the fail-closed conversion and
    the read - everything except the calls themselves.  The calls are held to
    the documented Win32 contracts, and every constant they pass is asserted
    against its WinNT.h value by a test that *can* run here.

    ``ctypes`` and ``msvcrt`` are imported inside the methods rather than at
    module scope for two reasons: ``msvcrt`` does not exist off Windows, so a
    module-level import would break every POSIX run outright, and both names
    are in ``sys.stdlib_module_names``, so importing them at all keeps the
    standard-library-only invariant AAP section 0.4.2 sets for ``app/utils``.

    Resource discipline, since a handle leak here is a locked credential file:
    every method that obtains a handle, a token or a ``LocalAlloc``-ed security
    descriptor releases it in a ``finally``, including on the refusal paths, and
    the one handle that is *not* released here is the one
    :meth:`descriptor_from_handle` transfers to a descriptor - after which
    closing the descriptor closes it.
    """

    def __init__(self) -> None:
        """Bind ``kernel32`` and ``advapi32`` and declare every prototype.

        Every signature is declared explicitly rather than left to ``ctypes``'
        default ``int`` marshalling, which is what makes the pointer and handle
        arguments correct on 64-bit Windows: a ``HANDLE`` passed as a C ``int``
        would be truncated.  ``use_last_error=True`` on both libraries is what
        lets :meth:`open_handle` recover the real Win32 error code for
        ``ctypes.WinError``.

        :raises OSError: If either library cannot be loaded.  Treated by
            :func:`_open_windows_verified_descriptor` as an I/O fault, which
            takes the tolerant missing-file path.
        """
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self._wintypes = wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        self._kernel32 = kernel32
        self._advapi32 = advapi32

        # ``INVALID_HANDLE_VALUE`` as the unsigned value a ``c_void_p`` restype
        # yields, computed rather than written as a literal so that it is
        # correct on both 32- and 64-bit Windows.
        self._invalid_handle = ctypes.c_void_p(-1).value

        pointer_to_void_pointer = ctypes.POINTER(ctypes.c_void_p)

        kernel32.CreateFileW.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        )
        kernel32.CreateFileW.restype = wintypes.HANDLE

        kernel32.GetFileInformationByHandleEx.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        )
        kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL

        kernel32.GetCurrentProcess.argtypes = ()
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE

        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL

        kernel32.LocalFree.argtypes = (wintypes.HLOCAL,)
        kernel32.LocalFree.restype = wintypes.HLOCAL

        advapi32.GetSecurityInfo.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.DWORD,
            pointer_to_void_pointer,
            pointer_to_void_pointer,
            pointer_to_void_pointer,
            pointer_to_void_pointer,
            pointer_to_void_pointer,
        )
        advapi32.GetSecurityInfo.restype = wintypes.DWORD

        advapi32.GetLengthSid.argtypes = (ctypes.c_void_p,)
        advapi32.GetLengthSid.restype = wintypes.DWORD

        advapi32.IsValidSid.argtypes = (ctypes.c_void_p,)
        advapi32.IsValidSid.restype = wintypes.BOOL

        advapi32.EqualSid.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
        advapi32.EqualSid.restype = wintypes.BOOL

        advapi32.GetAclInformation.argtypes = (
            ctypes.c_void_p,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.c_int,
        )
        advapi32.GetAclInformation.restype = wintypes.BOOL

        advapi32.GetAce.argtypes = (
            ctypes.c_void_p,
            wintypes.DWORD,
            pointer_to_void_pointer,
        )
        advapi32.GetAce.restype = wintypes.BOOL

        advapi32.OpenProcessToken.argtypes = (
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.HANDLE),
        )
        advapi32.OpenProcessToken.restype = wintypes.BOOL

        advapi32.GetTokenInformation.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        )
        advapi32.GetTokenInformation.restype = wintypes.BOOL

        advapi32.CreateWellKnownSid.argtypes = (
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.LPVOID,
            ctypes.POINTER(wintypes.DWORD),
        )
        advapi32.CreateWellKnownSid.restype = wintypes.BOOL

    def open_handle(self, target: Path) -> int:
        """Open ``target`` without following a link, and return the handle.

        ``FILE_FLAG_OPEN_REPARSE_POINT`` is what makes this the race-free
        replacement for the ``lstat`` pre-check ``SEC2-F10`` was filed against:
        the handle is bound to the reparse point itself, so a junction or a
        symlink is *obtained* rather than traversed and can then be refused,
        and nothing swapped at the path afterwards changes what is read.
        ``FILE_FLAG_BACKUP_SEMANTICS`` lets a directory in the file's place
        open too, so that it can be classified as one instead of failing with
        an error indistinguishable from a permission fault.

        :param target: The file to open.
        :returns: An open Win32 handle, owned by the caller.
        :raises OSError: If ``CreateFileW`` fails - absent file, unreadable
            file, sharing violation.  Raised through ``ctypes.WinError`` so
            that it carries the real Win32 code and its ``errno`` mapping, and
            left to reach :func:`load_properties`' missing-file path, which is
            the tolerance AAP section 0.1.1 requires.
        """
        ctypes = self._ctypes
        handle = self._kernel32.CreateFileW(
            str(target),
            _WINDOWS_GENERIC_READ,
            _WINDOWS_FILE_SHARE_READ,
            None,
            _WINDOWS_OPEN_EXISTING,
            _WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT
            | _WINDOWS_FILE_FLAG_BACKUP_SEMANTICS,
            None,
        )
        if not handle or handle == self._invalid_handle:
            raise ctypes.WinError(ctypes.get_last_error())
        return handle

    def file_attributes(self, handle: int) -> int:
        """Return the ``FileAttributes`` of the object ``handle`` is bound to.

        ``GetFileInformationByHandleEx`` with ``FileAttributeTagInfo``, which
        answers for the handle rather than for a path - the property that makes
        the reparse-point classification unraceable.  Only ``FileAttributes``
        is read; ``ReparseTag`` distinguishes *kinds* of reparse point and no
        kind of one is acceptable here, so it is not consulted.

        :param handle: The handle from :meth:`open_handle`.
        :returns: The Win32 file attribute bits.
        :raises _RefusedConfigurationError: If the query fails.  Fail closed:
            an object whose attributes cannot be read cannot be shown not to be
            a link.
        """
        ctypes = self._ctypes
        buffer = ctypes.create_string_buffer(_WINDOWS_FILE_ATTRIBUTE_TAG_INFO_BYTES)
        if not self._kernel32.GetFileInformationByHandleEx(
            handle,
            _WINDOWS_FILE_ATTRIBUTE_TAG_INFO,
            ctypes.byref(buffer),
            _WINDOWS_FILE_ATTRIBUTE_TAG_INFO_BYTES,
        ):
            raise _RefusedConfigurationError(_REASON_UNVERIFIABLE_OBJECT)
        # ``FILE_ATTRIBUTE_TAG_INFO.FileAttributes`` is the first of the two
        # DWORDs.  Windows is little-endian on every architecture it ships on.
        return int.from_bytes(buffer.raw[:4], "little")

    def security_facts(self, handle: int) -> _WindowsSecurityFacts:
        """Read the owner SID and the DACL trustees off ``handle``.

        The whole extraction, in one place and on one handle, so that the owner
        and the DACL describe the *same object* the read will return - the
        property a path-based query cannot offer.

        :param handle: The handle from :meth:`open_handle`.
        :returns: The facts :func:`_classify_windows_protection` decides on.
        :raises _RefusedConfigurationError: If any query fails or any structure
            cannot be decoded.  Fail closed throughout.
        """
        ctypes = self._ctypes
        owner_pointer = ctypes.c_void_p()
        dacl_pointer = ctypes.c_void_p()
        security_descriptor = ctypes.c_void_p()

        status = self._advapi32.GetSecurityInfo(
            handle,
            _WINDOWS_SE_FILE_OBJECT,
            _WINDOWS_OWNER_SECURITY_INFORMATION
            | _WINDOWS_DACL_SECURITY_INFORMATION,
            ctypes.byref(owner_pointer),
            None,
            ctypes.byref(dacl_pointer),
            None,
            ctypes.byref(security_descriptor),
        )
        if status != _WINDOWS_ERROR_SUCCESS:
            # A failed ``GetSecurityInfo`` allocates nothing, so there is
            # nothing to free on this path.
            raise _RefusedConfigurationError(_REASON_UNVERIFIABLE_OBJECT)

        try:
            # Both the owner SID and the DACL point *into* the one
            # ``LocalAlloc``-ed security descriptor, so everything needed is
            # copied out before the ``finally`` frees it - including on the
            # refusal paths these two calls can take.
            owner = self._sid_token(owner_pointer)
            trustees = self._allowed_trustees(dacl_pointer)
        finally:
            if security_descriptor:
                self._kernel32.LocalFree(security_descriptor)

        return _WindowsSecurityFacts(
            owner=owner,
            account=self._running_account_sid(),
            owner_equivalent=self._owner_equivalent_sids(),
            allowed_trustees=trustees,
        )

    def descriptor_from_handle(self, handle: int) -> int:
        """Turn ``handle`` into a file descriptor that owns it.

        The seam between the Windows-specific verification and the one shared
        read path: from here on the descriptor is read by the same
        ``fstat`` checks and the same :func:`_read_bounded` loop the POSIX
        branch uses, so the resource bounds ``SEC2-F10`` requires are literally
        the same code on both platforms rather than two implementations of one
        rule.

        **Ownership transfers here.**  Once ``open_osfhandle`` returns, the
        descriptor owns the handle and ``os.close`` on the descriptor closes it;
        calling ``CloseHandle`` as well would be a double close.  If it *raises*
        the transfer did not happen and the handle is still the caller's -
        :func:`_open_windows_verified_descriptor` closes it in that case, and
        the comment there marks the exact line the ownership changes at.

        :param handle: The verified handle from :meth:`open_handle`.
        :returns: A descriptor open for reading in binary mode.
        """
        import msvcrt

        # ``O_BINARY`` through ``getattr`` for the same reason ``_OPEN_FLAGS``
        # does it: the flag is Windows-only, and this line has to remain
        # importable and readable on a POSIX host even though it never runs
        # there.
        return msvcrt.open_osfhandle(handle, os.O_RDONLY | getattr(os, "O_BINARY", 0))

    def close_handle(self, handle: int) -> None:
        """Close ``handle``, for the paths that never reached a descriptor.

        :param handle: A handle still owned by the caller.
        """
        self._kernel32.CloseHandle(handle)

    def _sid_token(self, pointer: object) -> bytes:
        """Copy the SID at ``pointer`` into comparable bytes.

        The copy is what lets the policy be a pure function over inert values
        rather than over live pointers into a buffer that is about to be freed.
        Three things are established before it is handed over: the SID is valid
        (``IsValidSid``), its length is within ``SECURITY_MAX_SID_SIZE`` - a
        bound on the copy, so a corrupt length cannot drive a large read - and
        the copy is ``EqualSid`` to the original, which proves the bytes name
        the same security principal the pointer did and is what makes the
        byte comparison in :func:`_classify_windows_protection` sound.

        :param pointer: A ``PSID``, as a ``ctypes`` pointer or an address.
        :returns: The binary SID.
        :raises _RefusedConfigurationError: If the SID is absent, invalid,
            over-long, or does not survive the copy.  Fail closed.
        """
        ctypes = self._ctypes
        if not pointer:
            raise _RefusedConfigurationError(_REASON_UNVERIFIABLE_OBJECT)
        if not self._advapi32.IsValidSid(pointer):
            raise _RefusedConfigurationError(_REASON_UNVERIFIABLE_OBJECT)

        length = int(self._advapi32.GetLengthSid(pointer))
        if length <= 0 or length > _WINDOWS_SECURITY_MAX_SID_SIZE:
            raise _RefusedConfigurationError(_REASON_UNVERIFIABLE_OBJECT)

        token = ctypes.string_at(pointer, length)
        copy = ctypes.create_string_buffer(token, length)
        if not self._advapi32.EqualSid(ctypes.cast(copy, ctypes.c_void_p), pointer):
            raise _RefusedConfigurationError(_REASON_UNVERIFIABLE_OBJECT)
        return token

    def _allowed_trustees(self, dacl_pointer: object) -> tuple[bytes, ...] | None:
        """Return one SID per access-allowed ACE in the DACL.

        ``GetAclInformation`` for the ACE count, then ``GetAce`` per index -
        the documented way to walk an ACL, rather than arithmetic over the
        ``ACL`` structure.  An ACE type whose trustee this module cannot locate
        is a refusal and never a skip: skipping one would let a file be accepted
        on the strength of ACEs nobody read, which is the acceptance-by-omission
        the whole check exists to prevent.

        :param dacl_pointer: The ``PACL`` from ``GetSecurityInfo``, which is
            NULL when the object has no DACL.
        :returns: The trustee SIDs of the access-allowed ACEs, in DACL order,
            or ``None`` for a NULL DACL.
        :raises _RefusedConfigurationError: If the ACL cannot be walked or an
            ACE cannot be decoded.  Fail closed.
        """
        ctypes = self._ctypes
        if not dacl_pointer:
            # A NULL DACL.  Returned as ``None`` so that the classifier - not
            # this layer - decides what it means; it grants every account
            # access, so it is refused there.
            return None

        information = ctypes.create_string_buffer(
            _WINDOWS_ACL_SIZE_INFORMATION_BYTES
        )
        if not self._advapi32.GetAclInformation(
            dacl_pointer,
            ctypes.byref(information),
            _WINDOWS_ACL_SIZE_INFORMATION_BYTES,
            _WINDOWS_ACL_SIZE_INFORMATION,
        ):
            raise _RefusedConfigurationError(_REASON_UNVERIFIABLE_OBJECT)

        # ``ACL_SIZE_INFORMATION.AceCount`` is the first of its three DWORDs.
        count = int.from_bytes(information.raw[:4], "little")
        if count > _WINDOWS_MAX_ACE_COUNT:
            raise _RefusedConfigurationError(_REASON_UNVERIFIABLE_OBJECT)
        trustees: list[bytes] = []

        for index in range(count):
            ace_pointer = ctypes.c_void_p()
            if not self._advapi32.GetAce(
                dacl_pointer, index, ctypes.byref(ace_pointer)
            ):
                raise _RefusedConfigurationError(_REASON_UNVERIFIABLE_OBJECT)

            address = ace_pointer.value
            if address is None:
                # ``GetAce`` reported success without writing a pointer.  The
                # check sits *before* the header read on purpose: reading an
                # ACE the reader was never given would take its type byte from
                # a null address, and a type byte that happened to read as
                # denied, audit or alarm would make the loop below *skip* the
                # entry - accepting, by omission, an ACE that may grant access
                # to anyone.  Nothing is known about this ACE, so the file is
                # refused.
                raise _RefusedConfigurationError(_REASON_UNVERIFIABLE_OBJECT)

            header = ctypes.string_at(address, _WINDOWS_ACE_HEADER_BYTES)
            ace_type = header[0]
            if ace_type not in _WINDOWS_SIMPLE_ACE_TYPES:
                # An object, compound, callback or conditional ACE: a different
                # layout, so its trustee is not at the offset below.
                raise _RefusedConfigurationError(_REASON_UNVERIFIABLE_OBJECT)
            if ace_type != _WINDOWS_ACCESS_ALLOWED_ACE_TYPE:
                # Denied, audit and alarm ACEs grant nothing.
                continue

            trustees.append(
                self._sid_token(
                    ctypes.c_void_p(address + _WINDOWS_SIMPLE_ACE_SID_OFFSET)
                )
            )

        return tuple(trustees)

    def _running_account_sid(self) -> bytes:
        """Return the SID of the account this process runs as.

        Read from the process token rather than from a user name, so that
        "owned by me" is decided against the identity the kernel enforces.

        :returns: The binary SID of the token's user.
        :raises _RefusedConfigurationError: If the token cannot be opened or
            queried, or the reported buffer size is implausible.  Fail closed.
        """
        ctypes = self._ctypes
        wintypes = self._wintypes

        token = wintypes.HANDLE()
        if not self._advapi32.OpenProcessToken(
            self._kernel32.GetCurrentProcess(),
            _WINDOWS_TOKEN_QUERY,
            ctypes.byref(token),
        ):
            raise _RefusedConfigurationError(_REASON_UNVERIFIABLE_OBJECT)

        try:
            needed = wintypes.DWORD(0)
            # The documented two-call form: the first call is expected to fail
            # with ``ERROR_INSUFFICIENT_BUFFER`` and to report the size, so its
            # return value is deliberately not tested - the size it reports is.
            self._advapi32.GetTokenInformation(
                token, _WINDOWS_TOKEN_USER, None, 0, ctypes.byref(needed)
            )
            if not 0 < needed.value <= _WINDOWS_MAX_TOKEN_USER_BYTES:
                raise _RefusedConfigurationError(_REASON_UNVERIFIABLE_OBJECT)

            buffer = ctypes.create_string_buffer(needed.value)
            if not self._advapi32.GetTokenInformation(
                token,
                _WINDOWS_TOKEN_USER,
                ctypes.byref(buffer),
                needed.value,
                ctypes.byref(needed),
            ):
                raise _RefusedConfigurationError(_REASON_UNVERIFIABLE_OBJECT)

            # ``TOKEN_USER``'s first member is ``SID_AND_ATTRIBUTES.Sid``, a
            # ``PSID`` pointing into this buffer.  The buffer itself is
            # ``ctypes``-owned and must *not* be ``LocalFree``-d; Python
            # releases it, and the SID is copied out before it goes.
            sid_address = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
            return self._sid_token(ctypes.c_void_p(sid_address))
        finally:
            # The token handle is this method's own, on every path.
            self._kernel32.CloseHandle(token)

    def _owner_equivalent_sids(self) -> tuple[bytes, ...]:
        """Return the SIDs that are owner-equivalent for the DACL decision.

        ``LocalSystem`` and ``BUILTIN\\Administrators``, built by
        ``CreateWellKnownSid`` from their ``WELL_KNOWN_SID_TYPE`` values rather
        than parsed from ``S-1-5-18``/``S-1-5-32-544`` strings, so that no
        textual SID has to be trusted or converted.

        :returns: The two binary SIDs.
        :raises _RefusedConfigurationError: If either cannot be built.  Fail
            closed: without them a machine-administered file would be refused,
            which is a refusal the operator cannot act on.
        """
        return tuple(
            self._well_known_sid(kind)
            for kind in _WINDOWS_OWNER_EQUIVALENT_SID_TYPES
        )

    def _well_known_sid(self, kind: int) -> bytes:
        """Build one well-known SID.

        :param kind: A ``WELL_KNOWN_SID_TYPE`` value.
        :returns: The binary SID.
        :raises _RefusedConfigurationError: If ``CreateWellKnownSid`` fails or
            the result does not survive :meth:`_sid_token`'s checks.
        """
        ctypes = self._ctypes
        wintypes = self._wintypes

        size = wintypes.DWORD(_WINDOWS_SECURITY_MAX_SID_SIZE)
        buffer = ctypes.create_string_buffer(_WINDOWS_SECURITY_MAX_SID_SIZE)
        if not self._advapi32.CreateWellKnownSid(
            kind, None, ctypes.byref(buffer), ctypes.byref(size)
        ):
            raise _RefusedConfigurationError(_REASON_UNVERIFIABLE_OBJECT)
        return self._sid_token(ctypes.cast(buffer, ctypes.c_void_p))


def _windows_object_binding() -> _WindowsObjectBinding:
    """Build the Win32 layer for one configuration read.

    A one-line factory with a purpose: it is the **seam**
    ``tests/test_properties.py`` replaces, so that the sequencing, the
    classification, the fail-closed conversion and the shared read can all be
    exercised on a POSIX host with a stand-in that speaks the same four
    operations over an ordinary descriptor.  The real class cannot be
    instantiated here - ``ctypes.WinDLL`` needs Windows.

    One instance per read, not a module-level singleton: the read happens once
    per worker process (AAP deviation 17), so caching it would buy nothing and
    would keep a reference to two loaded libraries for the life of a process
    that may never read configuration again.

    :returns: A fresh :class:`_WindowsObjectBinding`.
    :raises OSError: If ``kernel32`` or ``advapi32`` cannot be loaded.
    """
    return _WindowsObjectBinding()


def _refuse_a_windows_reparse_point_or_directory(
    binding: _WindowsObjectBinding, handle: int
) -> None:
    """Classify what ``handle`` is bound to, before anything is read from it.

    The race-free replacement for the ``lstat`` pre-check, and
    ``SEC2-F10``'s non-POSIX half: the handle is already bound to the object,
    so a junction or a symlink swapped in at the path afterwards cannot change
    what this classification describes or what the read returns.

    A directory is converted to ``IsADirectoryError(EISDIR)`` rather than
    refused, exactly as the POSIX branch does in
    :func:`_open_verified_descriptor`, so that a directory in the file's place
    takes the :data:`MISSING_FILE_MESSAGE` path byte for byte on both
    platforms - one of the three I/O faults ``ConfigurationReader``'s
    ``catch (IOException)`` tolerates.

    :param binding: The Win32 layer, or the test stand-in for it.
    :param handle: The handle from ``binding.open_handle``.
    :raises _RefusedConfigurationError: If the object is a reparse point, or if
        the attribute query fails or faults - the fail-closed conversion, which
        is what keeps an unexpected return code or a ``ctypes`` error from
        either crashing the caller or being read as an acceptance.
    :raises IsADirectoryError: If the object is a directory.
    """
    try:
        attributes = binding.file_attributes(handle)
    except _RefusedConfigurationError:
        raise
    except Exception as error:
        raise _RefusedConfigurationError(_REASON_UNVERIFIABLE_OBJECT) from error

    if attributes & _WINDOWS_ATTRIBUTE_REPARSE_POINT:
        raise _RefusedConfigurationError(_REASON_LINK)
    if attributes & _WINDOWS_ATTRIBUTE_DIRECTORY:
        raise IsADirectoryError(errno.EISDIR, os.strerror(errno.EISDIR))


def _verify_windows_owner_and_dacl(
    binding: _WindowsObjectBinding, handle: int
) -> None:
    r"""Establish that ``handle``'s object is owner-owned and owner-only.

    ``SEC2-F33``'s non-POSIX half.  The finding is exact about what was wrong:
    the ownership and permission checks sat behind ``_POSIX_IDENTITY_AVAILABLE``
    and so did not run on Windows at all, leaving a credential-bearing
    ``configuration.properties`` read with no ownership and no ACL check in a
    checkout other accounts may be able to traverse.  This is the check that
    now runs there, against the owner SID and the DACL of the object the
    handle is bound to, and :func:`_classify_windows_protection` holds the
    policy it applies.

    **The refusal is actionable.**  A file this refuses is made acceptable by
    reducing it to the owner's own read access, which is the Windows equivalent
    of ``chmod 0600``::

        icacls configuration.properties /inheritance:r /grant:r "%USERNAME%":R

    ``/inheritance:r`` removes the inherited ACEs a checkout directory usually
    contributes - typically ``Users``, which is exactly the "accessible beyond
    its owner" shape - and ``/grant:r`` replaces the rest with one read ACE for
    the running account.

    :param binding: The Win32 layer, or the test stand-in for it.
    :param handle: The handle from ``binding.open_handle``.
    :raises _RefusedConfigurationError: If the owner is not the running
        account, if any access-allowed ACE names another trustee, or if the
        facts could not be established at all.  The last case is the fail-closed
        conversion: a query that fails, a structure that does not parse or a
        ``ctypes`` error becomes a refusal, so the configuration reads as absent
        - the AAP section 0.1.1 tolerance - rather than being accepted unchecked
        or crashing the worker.
    """
    try:
        facts = binding.security_facts(handle)
    except _RefusedConfigurationError:
        raise
    except Exception as error:
        raise _RefusedConfigurationError(_REASON_UNVERIFIABLE_OBJECT) from error

    reason = _classify_windows_protection(facts)
    if reason is not None:
        raise _RefusedConfigurationError(reason)


def _open_windows_verified_descriptor(target: Path) -> int:
    """Open ``target`` on Windows, object-bound from the first call.

    The Windows counterpart of the POSIX ``os.open(target, _OPEN_FLAGS)``, and
    the sequence both halves of this hardening rest on:

    1. ``CreateFileW`` with ``FILE_FLAG_OPEN_REPARSE_POINT`` binds a handle to
       the object itself - no link is traversed, and no later path swap can
       change what the handle refers to.
    2. The handle is classified: a reparse point is refused, a directory is
       converted to the missing-file path.
    3. The owner and the DACL of that same handle are verified against the
       running account.
    4. Only then does the handle become a descriptor, which the shared
       ``fstat`` checks and the shared bounded read take over.

    Steps 2 and 3 fail closed - see the two functions that implement them - so
    a Win32 query that cannot be completed refuses the file rather than
    accepting it or raising out of :func:`load_properties`.  What is *not* done
    is refusing unconditionally on Windows: that would leave the Windows
    pipeline unable to read any configuration at all, which AAP section 0.8's
    platform support forbids.

    :param target: The file to open.
    :returns: An open file descriptor, owned by the caller, which **must**
        close it.
    :raises _RefusedConfigurationError: If the object is a link, if its owner or
        protection is wrong, or if either could not be established.
    :raises OSError: From the open itself, and ``IsADirectoryError`` for a
        directory - the faults :func:`load_properties` tolerates.
    """
    try:
        binding = _windows_object_binding()
    except OSError:
        # ``kernel32`` or ``advapi32`` could not be loaded.  An I/O fault in
        # the same sense an unreadable file is: it takes the tolerant
        # missing-file path rather than becoming a refusal.
        raise
    except Exception as error:
        raise _RefusedConfigurationError(_REASON_UNVERIFIABLE_OBJECT) from error

    try:
        handle = binding.open_handle(target)
    except (OSError, _RefusedConfigurationError):
        raise
    except Exception as error:
        # A ``ctypes`` fault rather than a Win32 failure - a bad argument
        # conversion, a missing export.  Fail closed; never a crash.
        raise _RefusedConfigurationError(_REASON_UNVERIFIABLE_OBJECT) from error

    try:
        _refuse_a_windows_reparse_point_or_directory(binding, handle)
        _verify_windows_owner_and_dacl(binding, handle)
        # THE OWNERSHIP BOUNDARY.  Up to this line the handle is ours and the
        # ``except`` below must close it - which is what every refusal above
        # reaches.  Once this call returns, the descriptor owns the handle and
        # closing the descriptor closes it, so nothing here may close the
        # handle again; the caller's own ``except`` closes the *descriptor*
        # instead.  If this call raises, the transfer did not happen and the
        # handle is still ours, so the ``except`` below is correct for that
        # case too.
        return binding.descriptor_from_handle(handle)
    except BaseException:
        # ``BaseException`` for the reason the POSIX branch gives: a
        # ``KeyboardInterrupt`` arriving mid-verification must not leak a handle
        # on the credential file.
        binding.close_handle(handle)
        raise


def _open_object_bound_descriptor(target: Path) -> int:
    """Open ``target`` so that the *object*, not the path, is what is read.

    The one dispatch point between the two platform implementations of the same
    contract, and the only place :data:`_WINDOWS_PLATFORM` is consulted:

    * Windows - :func:`_open_windows_verified_descriptor`, which additionally
      verifies the owner SID and the DACL, because ``st_uid`` and ``st_mode``
      carry neither there.
    * Everywhere else - ``os.open`` with :data:`_OPEN_FLAGS`, whose
      ``O_NOFOLLOW`` the kernel enforces, preceded by the
      :func:`_refuse_a_link_before_opening` fallback that does nothing on any
      platform that has the flag.

    Windows is tested first because the Win32 branch subsumes both jobs the
    POSIX one splits between the kernel and the ``fstat`` checks.

    :param target: The file to open.
    :returns: An open file descriptor, owned by the caller.
    :raises _RefusedConfigurationError: If the object is refused before a
        descriptor exists.
    :raises OSError: From the open, including ``IsADirectoryError``.
    """
    if _WINDOWS_PLATFORM:
        return _open_windows_verified_descriptor(target)

    _refuse_a_link_before_opening(target)
    return os.open(target, _OPEN_FLAGS)


def _open_verified_descriptor(target: Path) -> int:
    """Open ``target`` and verify the object the descriptor is bound to.

    Every check runs against ``os.fstat`` of the **descriptor that will be
    read**, not against the path, so nothing swapped at the path afterwards can
    change what is read - the file object is pinned by the descriptor for as
    long as it is held.

    The checks, and what each one exists to stop:

    * ``S_ISDIR`` - a directory in the file's place.  Converted to
      ``IsADirectoryError(EISDIR)`` rather than refused, because that is one of
      the three I/O faults ``ConfigurationReader``'s ``catch (IOException)``
      tolerates and it must keep taking the :data:`MISSING_FILE_MESSAGE` path
      byte for byte.  The conversion is needed because ``O_RDONLY`` on a
      directory *succeeds* on Linux; only the read would fail.
    * ``S_ISREG`` - a FIFO (an unbounded, blocking source), a character or
      block device (``/dev/zero`` is infinite), or a socket.
    * ``st_nlink > 1`` - a hard link, which lets another account keep a handle
      on the credentials after the checkout's own copy is replaced or removed,
      and which the mode check alone cannot detect.
    * ``st_uid`` - a file planted by another local account.  **POSIX only**:
      ``st_uid`` is always 0 on Windows, where the equivalent check is the
      owner-SID comparison :func:`_verify_windows_owner_and_dacl` has already
      made against the same handle before this function sees a descriptor.
    * ``st_mode & 0o077`` - the 0600-equivalent ``SEC2-F33`` requires: a 0644
      credential file in a traversable checkout is readable by every local
      account and is refused here.  **POSIX only**, for the same reason:
      ``st_mode`` carries no ACL, and the Windows equivalent is the DACL walk
      in :func:`_classify_windows_protection`.
    * ``st_size`` - the cheap bound, taken before a single byte is read.
      :func:`_read_bounded` re-checks the actual length, because a file can
      grow between this ``fstat`` and that read.

    The two POSIX-only checks are gated by :data:`_POSIX_IDENTITY_AVAILABLE`,
    and that gate is an **alternative, not an opt-out** - which is what review
    finding ``SEC2-F33``'s non-POSIX branch was about.  Windows satisfies the
    same two obligations through the Win32 security query on the handle, and a
    platform that offers *neither* mechanism is refused outright rather than
    read with no ownership check at all.  No platform AAP section 0.8 supports
    lands in that third case.

    :param target: The file to open.
    :returns: An open file descriptor, owned by the caller, which **must**
        close it.
    :raises _RefusedConfigurationError: If any check above rejects the object.
        The descriptor is closed before the exception leaves.
    :raises OSError: From the open itself - absent file, unreadable file, a
        link where ``O_NOFOLLOW`` or ``FILE_FLAG_OPEN_REPARSE_POINT`` catches
        it - and ``IsADirectoryError`` for a directory.  The descriptor, if one
        was obtained, is closed first.
    """
    descriptor = _open_object_bound_descriptor(target)
    try:
        status = os.fstat(descriptor)

        if stat.S_ISDIR(status.st_mode):
            raise IsADirectoryError(errno.EISDIR, os.strerror(errno.EISDIR))
        if not stat.S_ISREG(status.st_mode):
            raise _RefusedConfigurationError(_REASON_NOT_A_REGULAR_FILE)
        if status.st_nlink > 1:
            raise _RefusedConfigurationError(_REASON_MULTIPLE_HARD_LINKS)
        if _POSIX_IDENTITY_AVAILABLE:
            if status.st_uid != os.geteuid():
                raise _RefusedConfigurationError(_REASON_FOREIGN_OWNER)
            if status.st_mode & _FORBIDDEN_MODE_BITS:
                raise _RefusedConfigurationError(_REASON_OPEN_PERMISSIONS)
        elif not _WINDOWS_PLATFORM:
            # Neither POSIX identity nor the Win32 security layer: nothing on
            # this platform can establish who owns the credentials or who else
            # can read them, so the file is refused.  Failing closed here is
            # what stops the gate above from being a way to read an unchecked
            # credential file, which is what it was when Windows fell through
            # it.  ``_WINDOWS_PLATFORM`` files have already been verified by
            # ``_open_windows_verified_descriptor``.
            raise _RefusedConfigurationError(_REASON_UNVERIFIABLE_OBJECT)
        if status.st_size > _MAX_FILE_BYTES:
            raise _RefusedConfigurationError(_REASON_TOO_LARGE)
    except BaseException:
        # The descriptor is the caller's only on the success path.  Anything
        # raised here - a refusal, the converted ``EISDIR``, or an ``OSError``
        # from ``fstat`` itself - must not leak it, and ``BaseException`` covers
        # a ``KeyboardInterrupt`` arriving between the open and the checks.  On
        # Windows this descriptor owns the handle
        # ``_open_windows_verified_descriptor`` transferred to it, so closing
        # the descriptor is what closes that handle - the refusal paths *inside*
        # that function close the handle directly instead, because no descriptor
        # existed yet.
        os.close(descriptor)
        raise

    return descriptor


def _read_bounded(descriptor: int) -> bytes:
    """Read at most :data:`_MAX_FILE_BYTES` bytes from ``descriptor``.

    The loop requests one byte more than the cap in total, so a file that grew
    between the ``fstat`` in :func:`_open_verified_descriptor` and this read is
    *detected* rather than truncated silently: reading a truncated
    configuration would be worse than refusing it, because a half-read file
    parses into a plausible-looking mapping with keys missing.

    :param descriptor: An open descriptor on a verified regular file.
    :returns: The file's bytes.
    :raises _RefusedConfigurationError: If more than :data:`_MAX_FILE_BYTES`
        bytes are available.
    :raises OSError: From the read itself.
    """
    limit = _MAX_FILE_BYTES + 1
    chunks: list[bytes] = []
    total = 0

    while total < limit:
        chunk = os.read(descriptor, min(_READ_CHUNK_BYTES, limit - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)

    if total > _MAX_FILE_BYTES:
        raise _RefusedConfigurationError(_REASON_TOO_LARGE)

    return b"".join(chunks)


def _read_configuration_bytes(target: Path) -> bytes:
    """Read ``target`` under the whole secure-read contract.

    The single replacement for the ``Path.read_bytes()`` call both
    ``SEC2-F10`` and ``SEC2-F33`` were filed against.  One descriptor is
    opened, verified through ``fstat`` and read bounded, and the ``finally``
    closes it on every path - including the refusal paths, which raise from
    inside the ``try``.

    :param target: The file to read.
    :returns: The file's bytes, at most :data:`_MAX_FILE_BYTES` of them.
    :raises _RefusedConfigurationError: If the object or its size is refused.
    :raises OSError: For the I/O faults :func:`load_properties` tolerates.
    """
    descriptor = _open_verified_descriptor(target)
    try:
        return _read_bounded(descriptor)
    finally:
        os.close(descriptor)


def _read_logical_lines(text: str) -> Iterator[str]:
    r"""Split ``text`` into logical properties lines.

    A faithful mirror of ``java.util.Properties$LineReader.readLine()``,
    including the parts of it that surprise people:

    * ``\n``, ``\r\n`` and a bare ``\r`` all terminate a physical line, and
      leading space, tab and form feed are skipped.
    * A line whose first non-whitespace character is ``#`` or ``!`` is a
      comment and is discarded whole, and never continues even if it ends in a
      backslash.  A ``#`` further along a line stays in the value.
    * Blank lines are skipped, except at the start of a continuation line,
      where an empty line ends the logical line.
    * A line ending in an **odd** number of backslashes continues onto the
      next, dropping the backslash, the terminator and the continuation line's
      leading whitespace; an **even** number is an escaped backslash, not a
      continuation.  A trailing odd backslash at end of input is dropped.

    Escapes are *not* decoded here: the separator scan in
    :func:`parse_properties` has to run against the raw characters so that
    ``\=``, ``\:`` and ``\ `` do not read as separators.

    :param text: The already-decoded content of a properties file.
    :returns: An iterator of logical lines, comments and blanks removed.
    """
    buffer: list[str] = []
    length = len(text)
    position = 0

    skip_whitespace = True
    is_comment_line = False
    is_new_line = True
    appended_line_begin = False
    preceding_backslash = False
    skip_lf = False

    while True:
        if position >= length:
            if not buffer or is_comment_line:
                return
            if preceding_backslash:
                buffer.pop()
            yield "".join(buffer)
            return

        char = text[position]
        position += 1

        if skip_lf:
            skip_lf = False
            if char == "\n":
                continue

        if skip_whitespace:
            if char in _WHITESPACE:
                continue
            # Blank lines are skipped - but not at the start of a continuation
            # line, where an immediate terminator ends the logical line below.
            if not appended_line_begin and char in ("\r", "\n"):
                continue
            skip_whitespace = False
            appended_line_begin = False

        if is_new_line:
            is_new_line = False
            if char in ("#", "!"):
                is_comment_line = True
                continue

        if char not in ("\r", "\n"):
            buffer.append(char)
            # Flip on backslash, clear on anything else, so the flag tracks
            # "an odd number of backslashes immediately precedes here".
            preceding_backslash = not preceding_backslash if char == "\\" else False
            continue

        if is_comment_line or not buffer:
            is_comment_line = False
            is_new_line = True
            skip_whitespace = True
            buffer.clear()
            continue

        if position >= length:
            # The terminator was the last character: the JDK refills, sees end
            # of input and returns, dropping a trailing continuation backslash.
            if preceding_backslash:
                buffer.pop()
            yield "".join(buffer)
            return

        if preceding_backslash:
            buffer.pop()
            skip_whitespace = True
            appended_line_begin = True
            preceding_backslash = False
            if char == "\r":
                skip_lf = True
            continue

        yield "".join(buffer)
        buffer = []
        skip_whitespace = True
        is_comment_line = False
        is_new_line = True
        appended_line_begin = False
        preceding_backslash = False
        # ``readLine()`` starts with ``skipLF`` false; a CRLF's ``\n`` is
        # discarded by the blank-line branch above instead.
        skip_lf = False


def _decode_escapes(line: str, start: int, end: int) -> str:
    r"""Decode the properties escapes in ``line[start:end]``.

    A mirror of ``Properties.loadConvert()``:

    * ``\t``, ``\r``, ``\n``, ``\f`` become the matching control character.
    * ``\uXXXX`` with **exactly four** hexadecimal digits becomes that code
      unit; a value above ``U+FFFF`` is two such escapes, as in Java.
    * **Any other** ``\c`` becomes ``c``: ``\\``, ``\=``, ``\:``, ``\#``, ``\!``,
      an escaped space, and ``\q`` giving ``q``.  Java's rule, not an error.

    A malformed ``\uXXXX`` - fewer than four digits before the end of the slice,
    or a non-hexadecimal digit - **raises** rather than recovering, because
    ``Properties.loadConvert`` throws and ``ConfigurationReader`` catches only
    ``IOException`` (``ConfigurationReader.java:17,21-24``), so the tolerance
    shown a *missing* file does not extend to a malformed one.  Nothing is
    logged and no digit, key, value or filename reaches the message: the four
    characters after a ``\u`` may be part of a credential.

    :param line: The logical line to read from.
    :param start: Index of the first character of the slice, inclusive.
    :param end: Index one past the last character of the slice.
    :returns: The decoded text; an empty slice decodes to ``""``.
    :raises ValueError: If the slice holds a malformed ``\uXXXX`` escape, always
        with the message ``Malformed \uxxxx encoding.`` whatever the input.
    """
    decoded: list[str] = []
    index = start

    while index < end:
        char = line[index]
        index += 1

        if char != "\\":
            decoded.append(char)
            continue

        if index >= end:
            # Unreachable through :func:`parse_properties`: a slice can only end
            # on an even number of backslashes, because the separator scan
            # breaks on an unescaped character and ``_read_logical_lines`` has
            # already disposed of an odd trailing one.  Kept explicit so a
            # direct caller cannot trip an IndexError.
            decoded.append("\\")
            break

        char = line[index]
        index += 1

        if char != "u":
            decoded.append(_CONTROL_ESCAPES.get(char, char))
            continue

        digits = line[index:min(index + _UNICODE_ESCAPE_DIGITS, end)]
        if len(digits) == _UNICODE_ESCAPE_DIGITS and all(
            digit in _HEX_DIGITS for digit in digits
        ):
            decoded.append(chr(int(digits, 16)))
            index += _UNICODE_ESCAPE_DIGITS
            continue

        # Malformed, and Java raises here rather than recovering: there is no
        # partially decoded result to return and no further line to read.  The
        # digits just examined are deliberately *not* carried into the message
        # or into a log record - see :data:`_MALFORMED_ESCAPE_MESSAGE`.
        raise ValueError(_MALFORMED_ESCAPE_MESSAGE)

    return "".join(decoded)


def parse_properties(text: str) -> dict[str, str]:
    r"""Parse properties ``text`` into a plain ``dict``.

    A mirror of ``Properties.load0()`` over already-decoded text, so the whole
    grammar is exercisable without a filesystem.  The rules, all Java's:

    * The key ends at the first **unescaped** ``=``, ``:``, space, tab or form
      feed; whitespace around the separator is skipped and whitespace followed
      by ``=`` or ``:`` consumes it, so ``k=v``, ``k : v`` and ``k v`` all
      yield the same pair.
    * A key with no separator and no value maps to the **empty string**; only
      an *absent* key reads as ``None``, which :func:`get_property` decides.
    * **Trailing whitespace on a value is preserved**, whitespace between the
      separator and the value is not, and escapes decode in keys and values.
    * Duplicate keys: the last occurrence wins.
    * **This format has no section headers**: a ``[section]`` line is a key
      whose name contains brackets.  A UTF-8 byte-order mark decoded as
      ISO-8859-1 prefixes junk to the first key, as in Java, so none is
      stripped.

    **The caps are the one deliberate departure from ``load0``**, and they are
    review finding ``SEC2-F10``: a properties file is untrusted input, and the
    probe in that finding was a single 2 MiB value.  At most
    :data:`_MAX_ENTRIES` entries, :data:`_MAX_KEY_CHARACTERS` characters of
    key, :data:`_MAX_VALUE_CHARACTERS` characters of value and
    :data:`_MAX_LINE_CHARACTERS` characters of logical line are accepted; over
    any of them the parse aborts with :class:`_RefusedConfigurationError`,
    whose fixed reason phrase carries nothing read from the file.  Because that
    class is a ``ValueError`` subclass, a caller who already handles the
    malformed-escape failure handles this too; :func:`load_properties`
    distinguishes them and tolerates only the refusal.

    :param text: The already-decoded content of a properties file.
    :returns: A new ``dict`` of decoded keys to decoded values; text holding
        only comments and blanks yields an empty ``dict``.
    :raises ValueError: If any key or value holds a malformed ``\uXXXX``
        escape.  The message is exactly ``Malformed \uxxxx encoding.``.  A
        :class:`_RefusedConfigurationError` - also a ``ValueError`` - is raised
        instead when a cap above is exceeded.
    """
    properties: dict[str, str] = {}

    for line in _read_logical_lines(text):
        # Checked before the key scan, so an oversized line costs one length
        # comparison rather than a character-by-character walk.
        if len(line) > _MAX_LINE_CHARACTERS:
            raise _RefusedConfigurationError(_REASON_LINE_TOO_LONG)

        limit = len(line)
        key_length = 0
        value_start = limit
        has_separator = False
        preceding_backslash = False

        while key_length < limit:
            char = line[key_length]
            if char in ("=", ":") and not preceding_backslash:
                value_start = key_length + 1
                has_separator = True
                break
            if char in _WHITESPACE and not preceding_backslash:
                value_start = key_length + 1
                break
            preceding_backslash = not preceding_backslash if char == "\\" else False
            key_length += 1

        # Skip whitespace before the value, absorbing one ``=`` or ``:`` if the
        # key was in fact terminated by whitespace rather than by a separator.
        while value_start < limit:
            char = line[value_start]
            if char not in _WHITESPACE:
                if not has_separator and char in ("=", ":"):
                    has_separator = True
                else:
                    break
            value_start += 1

        key = _decode_escapes(line, 0, key_length)
        value = _decode_escapes(line, value_start, limit)

        # The caps are applied to the *decoded* slices, which is what a caller
        # ends up holding, and after the decode so that a malformed ``\uXXXX``
        # escape still raises its own parity failure rather than being masked
        # by a refusal.
        if len(key) > _MAX_KEY_CHARACTERS:
            raise _RefusedConfigurationError(_REASON_KEY_TOO_LONG)
        if len(value) > _MAX_VALUE_CHARACTERS:
            raise _RefusedConfigurationError(_REASON_VALUE_TOO_LONG)

        properties[key] = value

        # After the insert, so that a file repeating one key stays acceptable -
        # duplicates collapse, and it is the number of *entries* that is
        # bounded rather than the number of lines.
        if len(properties) > _MAX_ENTRIES:
            raise _RefusedConfigurationError(_REASON_TOO_MANY_ENTRIES)

    return properties


def load_properties(
    path: str | os.PathLike[str] | None = None,
    *,
    encoding: str = DEFAULT_ENCODING,
) -> dict[str, str]:
    r"""Read and parse one properties file, without caching.

    The I/O half of ``ConfigurationReader``'s static initializer, uncached and
    separate from :func:`get_properties` so one file can be loaded repeatedly
    and the grammar stays reachable with no filesystem at all.

    A file that cannot be read is **tolerated, exactly as at
    ``ConfigurationReader:21-24``**: :data:`MISSING_FILE_MESSAGE` is logged at
    ``WARNING`` with the traceback attached - the analogue of that block's
    message plus ``printStackTrace`` - and an empty mapping is returned.  No
    ``OSError`` is ever re-raised, and ``OSError`` covers the realistic causes:
    the file is absent, is a directory, or is not readable.

    A file that *reads* but does not *parse* is a different matter and is not
    tolerated: the ``OSError`` clause below is narrowed on purpose, and the
    only ``ValueError`` clause catches the private
    :class:`_RefusedConfigurationError` subclass, so the :class:`ValueError` a
    malformed ``\uXXXX`` escape raises inside :func:`parse_properties`
    propagates to the caller.  That is parity - the Java ``catch`` is
    ``IOException`` and the malformed-escape exception is not one (see
    :func:`_decode_escapes`) - and it is why a *bare* ``ValueError`` handler
    must not be added here.  Decoding itself cannot fail; see ``encoding``.

    **An unsafe file is refused, and a refusal behaves exactly like an absent
    file.**  The read goes through :func:`_read_configuration_bytes`, which
    opens one ``O_NOFOLLOW`` descriptor - or, on Windows, one
    ``FILE_FLAG_OPEN_REPARSE_POINT`` handle whose owner SID and DACL are
    verified before it becomes a descriptor - and accepts only a regular,
    single-link, owner-only, owner-readable file within
    :data:`_MAX_FILE_BYTES`, parsed under the caps :func:`parse_properties`
    documents.  Anything else - a symlink, a junction, a FIFO, a hard-linked
    or 0644 credential file, a file whose DACL grants ``Users`` or
    ``Everyone``, a file owned by another account, an oversized file or an
    oversized entry - produces one ``WARNING`` carrying
    :data:`_REFUSED_FILE_MESSAGE`, a fixed reason phrase and the filename, and
    then an empty mapping.  Nothing raises, nothing exits, every one of the six
    configured keys reads as ``None`` at its point of use, and because
    :func:`get_properties` records the *attempt* the file is never retried.
    That is findings ``SEC2-F10`` and ``SEC2-F33``, fixed without touching the
    tolerance AAP section 0.1.1 requires.

    The refusal message is deliberately **not**
    :data:`MISSING_FILE_MESSAGE`, and no traceback is attached to it: an
    ``OSError`` traceback embeds the absolute path it was raised for.  The
    three I/O faults the Java ``catch`` covers - absent, a directory in its
    place (``EISDIR``), and unreadable - keep the missing-file message with its
    traceback, unchanged.

    :param path: The file to read.  Defaults to :data:`PROPERTIES_FILENAME`
        resolved against the current working directory **at call time**, which
        is what makes the process working directory decide the file, as in
        ``ConfigurationReader:14``.
    :param encoding: The encoding to decode the bytes with.  Defaults to
        :data:`DEFAULT_ENCODING`, the Java 8 default.  ISO-8859-1 maps every
        one of the 256 byte values, so no decoding fallback is needed or
        wanted; a file of arbitrary bytes decodes rather than failing.
    :returns: A new ``dict`` of the file's contents, or an empty ``dict`` if it
        could not be read or was refused.
    :raises ValueError: If the file reads but holds a malformed ``\uXXXX``
        escape; see :func:`_decode_escapes`.
    """
    target = Path(path) if path is not None else Path.cwd() / PROPERTIES_FILENAME

    try:
        raw = _read_configuration_bytes(target)
        parsed = parse_properties(raw.decode(encoding))
    except _RefusedConfigurationError as refusal:
        # The subclass only - a malformed ``\uXXXX`` escape is a plain
        # ``ValueError`` and is not caught here, which the parity tests pin.
        _log_refusal(target, refusal.reason)
        return {}
    except OSError as error:
        if error.errno in _LINK_ERRNOS:
            # ``O_NOFOLLOW`` rejected a link at the final component.  A
            # refusal, not an I/O fault: the traceback would name the absolute
            # path, and the log must stay free of it.
            _log_refusal(target, _REASON_LINK)
            return {}
        # Message verbatim, traceback attached, execution continues.
        logger.warning(MISSING_FILE_MESSAGE, exc_info=True)
        return {}

    return parsed


# The process-wide cache.  The dict object is created once and mutated in place
# so that every :class:`~types.MappingProxyType` handed out stays a live view of
# it, and so the hot path allocates nothing at all.
_properties: dict[str, str] = {}

_properties_view: Mapping[str, str] = MappingProxyType(_properties)

# "A load has been attempted", tracked separately from "the cache is non-empty".
# A present-but-empty file must not trigger a second attempt, and a missing file
# must not produce a second log record.  An ``Event`` rather than a plain bool,
# so that the flag is a thread-safe object in its own right and neither accessor
# has to rebind module-level state to update it.
_load_attempted = threading.Event()

# "That one attempt failed."  Set only when the attempt raised - which, given
# that :func:`load_properties` absorbs every ``OSError``, means a malformed
# ``\uXXXX`` escape and nothing else.  It is what makes a *failed* load as
# final as a successful one: ``_load_attempted`` alone would let the next call
# retry, so a file edited mid-run could turn a failure into a success, which
# neither this module's never-re-read guarantee nor the JVM allows - a class
# whose static initializer threw stays unusable for the life of the loader.
# No exception object is stored: the message is fixed, so re-raising it costs
# nothing and nothing from the file is retained in module state.
_load_failed = threading.Event()

# Guards the one-time load and :func:`reset_cache`.  behave may run threads
# inside a worker process, so two threads racing the first access must not
# produce two loads or two log records.
_lock = threading.Lock()


def get_properties() -> Mapping[str, str]:
    r"""Return the process's properties, loading them on first call.

    The port of ``ConfigurationReader``'s static-initializer semantics
    (``ConfigurationReader.java:11-25``).  The first call reads
    :data:`PROPERTIES_FILENAME` from the working directory, resolved at that
    moment rather than at import time; every later call returns the cached
    mapping **without touching the filesystem**.  An initialized reader
    **never re-reads**, matching the JVM, and a missing file is logged once.

    **A failed attempt is as final as a successful one.**  The malformed-escape
    :class:`ValueError` propagates, and the failure is recorded under the same
    lock, so every later call raises that fixed message without touching the
    filesystem: retrying would let a mid-run edit turn the failure into a
    success, which the never-re-read guarantee excludes.  Nothing is logged,
    because the malformed text may be part of a credential.

    A lock with a double-checked flag guards the load, so concurrent first
    access from several threads still performs one load and emits one log
    record.  The cache is per process, one load per worker (AAP deviation 17).

    :returns: A read-only view of the cached mapping.  Mutating it raises
        ``TypeError``, so no caller can corrupt the shared cache.
    :raises ValueError: If the file holds a malformed ``\uXXXX`` escape, on
        this call and on every later one.
    """
    if not _load_attempted.is_set():
        with _lock:
            # Re-checked under the lock: another thread may have loaded while
            # this one waited.
            if not _load_attempted.is_set():
                try:
                    loaded = load_properties()
                except ValueError:
                    # The malformed-escape failure, and the only exception
                    # ``load_properties`` lets through.  Both flags are set
                    # before the lock is released - failure first, so a reader
                    # that sees ``_load_attempted`` set also sees why - and the
                    # original exception, with its traceback, reaches this
                    # caller unchanged.  The cache is left as it was: empty.
                    _load_failed.set()
                    _load_attempted.set()
                    raise
                _properties.clear()
                _properties.update(loaded)
                # Set last, so a reader on the lock-free path above never sees a
                # partially populated cache.
                _load_attempted.set()

    if _load_failed.is_set():
        # A later call after a failed attempt: same error, same fixed message,
        # no file read.
        raise ValueError(_MALFORMED_ESCAPE_MESSAGE)

    return _properties_view


def get_property(key: str, default: str | None = None) -> str | None:
    r"""Return the value configured for ``key``, or ``default`` if absent.

    The analogue of ``ConfigurationReader.getProperty``
    (``ConfigurationReader.java:27-29``), whose single-argument form returns
    ``null`` for a key the file does not define.  ``default`` is ``None`` for
    the same reason: a configuration problem must surface at the point of use,
    not at start-up.

    No validation, normalization or type coercion is performed, by design.  An
    unrecognised ``browser`` value is returned as written so it reaches the
    driver and fails at first use, because the Java ``Driver`` switch has no
    default branch.  Keys are neither case-folded nor stripped.

    A key present with no value yields ``""``; only an *absent* key yields
    ``default``.  An unreadable file yields ``default`` for every key, since
    :func:`get_properties` reports it as an empty mapping; a *malformed* file
    is the one case that raises rather than answering.

    :param key: The property name, matched exactly.
    :param default: The value to return when ``key`` is not present.
    :returns: The configured value, or ``default``.
    :raises ValueError: If the properties file holds a malformed ``\uXXXX``
        escape, propagated from :func:`get_properties`.  The message is exactly
        ``Malformed \uxxxx encoding.``.
    """
    return get_properties().get(key, default)


def reset_cache() -> None:
    r"""Discard the cached properties and both one-shot load flags.

    **Test support.**  The cache is process-global while pytest runs every test
    in one process, and the behaviour this module has to prove includes the
    one-time load, the single missing-file log record, the guarantee that an
    initialized reader never re-reads, and the permanence of a failed load.
    Those assertions are impossible in a single process without a way to return
    to the pre-load state, so this function exists for
    ``tests/test_properties.py`` to call.

    Both flags are cleared, ``_load_failed`` included: a test that proved the
    malformed-file failure would otherwise leave every later
    :func:`get_properties` call raising for the rest of the session.

    It is not part of the package's public surface: it is absent from
    :data:`__all__` and is not re-exported by ``app/utils/__init__.py``.
    Production code has no reason to call it - doing so would reintroduce the
    mid-run re-read that parity with the JVM rules out.

    The same lock as :func:`get_properties` is taken, so a reset cannot
    interleave with a load.  Views handed out earlier remain valid and observe
    the cleared - and then reloaded - contents.
    """
    with _lock:
        _properties.clear()
        _load_failed.clear()
        _load_attempted.clear()
