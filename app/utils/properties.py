r"""Java ``.properties`` reader - the Python port of ``ConfigurationReader``.

This module is the *only* reader of ``configuration.properties`` in the port
(AAP section 0.4.2), and it reproduces the read surface of the Java class it
replaces, ``com.testinium.utilities.ConfigurationReader``:

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

The grammar is implemented by hand.  ``configparser`` is unusable for this
format: it rejects a section-less file outright, and the real key set includes
dotted names such as ``web.table.url``.  The three private helpers mirror the
three JDK 8 state machines one for one - :func:`_read_logical_lines` mirrors
``java.util.Properties$LineReader.readLine()``, :func:`_decode_escapes` mirrors
``Properties.loadConvert()``, and :func:`parse_properties` mirrors
``Properties.load0()``.

Import boundary (AAP section 0.4.2): ``app/utils`` sits at the bottom of the
import graph, so this module imports **only the standard library** - never
``app.config``, ``app.logging_config``, ``app.utils.paths``, Flask, behave or
Selenium.  That is what makes it importable in a worker process that never
builds a Flask application.  ``app/config.py`` is the only module permitted to
import it, which is what keeps ``configuration.properties`` read in exactly one
place; that module owns the named accessors for the configured keys and the
behave-userdata precedence layered on top of this file.  No key name, no
default value and no validation belongs here: this is a generic reader.
"""

import logging
import os
import threading
from collections.abc import Iterator, Mapping
from pathlib import Path
from types import MappingProxyType

__all__ = [
    "DEFAULT_ENCODING",
    "MISSING_FILE_MESSAGE",
    "PROPERTIES_FILENAME",
    "get_properties",
    "get_property",
    "load_properties",
    "parse_properties",
]

# Records propagate to the ``app`` package logger, where ``configure_logging()``
# installs the split that sends WARNING and above to stderr - which is where the
# Java ``printStackTrace`` went.  ``app/logging_config.py`` is deliberately not
# imported: it exposes no ``get_logger()`` helper precisely so that this
# module's standard-library-only invariant can hold.
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


def _read_logical_lines(text: str) -> Iterator[str]:
    r"""Split ``text`` into logical properties lines.

    A faithful mirror of ``java.util.Properties$LineReader.readLine()``,
    including the parts of it that surprise people:

    * ``\n``, ``\r\n`` and a bare ``\r`` all terminate a physical line.
    * Leading space, tab and form feed on a line are skipped.
    * A line whose first non-whitespace character is ``#`` or ``!`` is a
      comment and is discarded whole.  A ``#`` further along a line is *not* a
      comment - it stays in the value.
    * A comment line never continues, even if it ends in a backslash: the JDK
      tests "is this a comment" before "was there a trailing backslash".
    * Blank lines are skipped.
    * A line ending in an **odd** number of backslashes continues onto the next
      line; the terminating backslash and the line terminator are dropped and
      the continuation line's own leading whitespace is skipped.  An **even**
      number is not a continuation - the final pair is an escaped backslash.
    * A trailing odd backslash at end of input is dropped rather than joined to
      anything.
    * An empty continuation line ends the logical line, because the flag that
      suppresses blank-line skipping applies only to the first character.

    Escapes are *not* decoded here.  The separator scan in
    :func:`parse_properties` has to run against the raw characters so that
    ``\=``, ``\:`` and ``\ `` do not read as separators; decoding happens
    afterwards, per slice.

    :param text: The already-decoded content of a properties file.
    :returns: An iterator of logical lines, comments and blanks removed.
    """
    buffer: list[str] = []
    length = len(text)
    position = 0

    # The six flags ``readLine()`` keeps as locals.  They are reset after every
    # yielded line, because the JDK re-enters ``readLine()`` with fresh locals.
    skip_whitespace = True
    is_comment_line = False
    is_new_line = True
    appended_line_begin = False
    preceding_backslash = False
    skip_lf = False

    while True:
        if position >= length:
            # End of input.  A comment or an empty buffer yields nothing, which
            # is the JDK's ``return -1``.
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

        # End of a physical line.
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

    * ``\t``, ``\r``, ``\n`` and ``\f`` become the matching control character.
    * ``\uXXXX`` with **exactly four** hexadecimal digits becomes that code
      unit.  Values above ``U+FFFF`` are written in Java as a surrogate pair of
      two such escapes, and are decoded here as the same two code units.
    * **Any other** ``\c`` becomes ``c``, dropping the backslash.  That covers
      ``\\``, ``\=``, ``\:``, ``\#``, ``\!`` and an escaped space, and it is
      Java's rule rather than an error - ``\q`` yields ``q``.

    A malformed ``\uXXXX`` - fewer than four digits before the end of the
    slice, or a non-hexadecimal digit - **raises**, which is what Java does:
    ``Properties.loadConvert`` throws
    ``IllegalArgumentException("Malformed \uxxxx encoding.")`` (JDK 8
    ``Properties.java:575``), ``ConfigurationReader`` catches only
    ``IOException`` (``ConfigurationReader:17,21-24``), and the static
    initializer therefore fails.  The tolerance this module shows a *missing*
    file is that ``catch`` and nothing wider, so it does not extend to a
    malformed one.  The raise carries the Java message verbatim and nothing
    else - no digits, no key, no value, no filename - and nothing is logged on
    the way out, because the four characters that follow a ``\u`` may be part
    of a credential.

    :param line: The logical line to read from.
    :param start: Index of the first character of the slice, inclusive.
    :param end: Index one past the last character of the slice.
    :returns: The decoded text.  An empty slice decodes to ``""``.
    :raises ValueError: If the slice holds a malformed ``\uXXXX`` escape.  The
        message is exactly ``Malformed \uxxxx encoding.``, whatever the input.
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

    A mirror of ``Properties.load0()``, operating on already-decoded text so
    that the whole grammar is exercisable without touching the filesystem.

    The rules that matter, all of them Java's:

    * The key ends at the first **unescaped** ``=``, ``:``, space, tab or form
      feed.  Whitespace around the separator is skipped, and whitespace
      *followed by* ``=`` or ``:`` consumes that character as the separator, so
      ``k=v``, ``k = v``, ``k:v``, ``k : v`` and ``k v`` all yield the same
      pair.
    * A key with no separator and no value maps to the **empty string**, so a
      lone ``browser`` line yields ``{"browser": ""}``.  Only an *absent* key
      reads as ``None``, and that distinction is made by :func:`get_property`.
    * **Trailing whitespace on a value is preserved.**  Java skips whitespace
      after the separator but keeps everything through to the end of the
      logical line.
    * Escapes are decoded in keys as well as values.
    * Duplicate keys: the last occurrence wins.
    * **This format has no section headers.**  A ``[section]`` line is simply a
      key whose name contains brackets, and is not treated specially.
    * A UTF-8 byte-order mark decoded as ISO-8859-1 prefixes junk characters to
      the first key.  Java behaves identically, so no BOM stripping is done.
    * A malformed ``\uXXXX`` escape - in a key or in a value - **aborts the
      parse** with the :class:`ValueError` :func:`_decode_escapes` raises, and
      no partial mapping is returned.  ``Properties.load0`` behaves the same
      way, because the ``loadConvert`` it calls per slice throws; nothing here
      catches it.

    :param text: The already-decoded content of a properties file.
    :returns: A new ``dict`` mapping decoded keys to decoded values.  Text that
        holds only comments and blank lines yields an empty ``dict``.
    :raises ValueError: If any key or value holds a malformed ``\uXXXX``
        escape.  The message is exactly ``Malformed \uxxxx encoding.``.
    """
    properties: dict[str, str] = {}

    for line in _read_logical_lines(text):
        limit = len(line)
        key_length = 0
        value_start = limit
        has_separator = False
        preceding_backslash = False

        # Find the end of the key: the first unescaped separator or whitespace.
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
        properties[key] = value

    return properties


def load_properties(
    path: str | os.PathLike[str] | None = None,
    *,
    encoding: str = DEFAULT_ENCODING,
) -> dict[str, str]:
    r"""Read and parse one properties file, without caching.

    The I/O half of the port of ``ConfigurationReader``'s static initializer.
    Kept uncached and separate from :func:`get_properties` so that a specific
    file can be loaded repeatedly, and so that the grammar in
    :func:`parse_properties` is reachable without any filesystem at all.

    A file that cannot be read is **tolerated, exactly as at
    ``ConfigurationReader:21-24``**: :data:`MISSING_FILE_MESSAGE` is logged at
    ``WARNING`` with the traceback attached - the analogue of that block's
    message plus ``printStackTrace`` - and an empty mapping is returned.  No
    ``OSError`` is ever re-raised, and ``OSError`` covers the realistic causes:
    the file is absent, is a directory, or is not readable.

    A file that *reads* but does not *parse* is a different matter and is not
    tolerated: the ``except`` below is narrowed to ``OSError`` on purpose, so
    the :class:`ValueError` a malformed ``\uXXXX`` escape raises inside
    :func:`parse_properties` propagates to the caller.  That is parity - the
    Java ``catch`` is ``IOException`` and the malformed-escape exception is not
    one (see :func:`_decode_escapes`) - and it is why a ``ValueError`` handler
    must not be added here.  Decoding itself cannot fail; see ``encoding``.

    :param path: The file to read.  Defaults to :data:`PROPERTIES_FILENAME`
        resolved against the current working directory **at call time**, which
        is what makes the process working directory decide the file, as in
        ``ConfigurationReader:14``.
    :param encoding: The encoding to decode the bytes with.  Defaults to
        :data:`DEFAULT_ENCODING`, the Java 8 default.  ISO-8859-1 maps every
        one of the 256 byte values, so no decoding fallback is needed or
        wanted; a file of arbitrary bytes decodes rather than failing.
    :returns: A new ``dict`` of the file's contents, or an empty ``dict`` if it
        could not be read.
    :raises ValueError: If the file reads but holds a malformed ``\uXXXX``
        escape.  The message is exactly ``Malformed \uxxxx encoding.``.
    """
    target = Path(path) if path is not None else Path.cwd() / PROPERTIES_FILENAME

    try:
        raw = target.read_bytes()
    except OSError:
        # Message verbatim, traceback attached, execution continues.
        logger.warning(MISSING_FILE_MESSAGE, exc_info=True)
        return {}

    return parse_properties(raw.decode(encoding))


# The process-wide cache.  The dict object is created once and mutated in place
# so that every :class:`~types.MappingProxyType` handed out stays a live view of
# it, and so the hot path allocates nothing at all.
_properties: dict[str, str] = {}

# The immutable view callers receive.  Built once, over the dict above.
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

    The port of ``ConfigurationReader``'s static initializer semantics
    (``ConfigurationReader:11``).  The first call reads
    :data:`PROPERTIES_FILENAME` from the current working directory - resolved
    at that moment, not at import time, so that the working directory in force
    when configuration is first needed is the one that counts.  Every later
    call returns the cached mapping **without touching the filesystem**.

    Once initialized the reader **never re-reads**, so a file that changes, or
    that appears after the first attempt, does not affect it.  That matches the
    JVM.  A missing file is logged exactly once per process.

    **A failed attempt is as final as a successful one.**  A malformed
    ``\uXXXX`` escape propagates out of this call as the :class:`ValueError`
    :func:`_decode_escapes` raises, and the failure is recorded under the same
    lock, so every later call raises that same fixed message *without touching
    the filesystem*.  Retrying would re-read the file and let an edit made
    mid-run turn the failure into a success, which is exactly what the
    never-re-read guarantee excludes; in the JVM a class whose static
    initializer threw is unusable from then on.  Nothing is logged on either
    path, because the malformed text may be part of a credential.

    The load is guarded by a lock with a double-checked flag, so concurrent
    first access from several threads still performs one load and emits one log
    record.  The cache is **per process**: under the process pool in
    ``app/services/test_run_service.py`` each worker performs its own load,
    which is AAP deviation 17.  No cross-process sharing is attempted.

    :returns: A read-only view of the cached mapping.  Attempting to mutate it
        raises ``TypeError``, so no caller can corrupt the shared cache.
    :raises ValueError: If the properties file holds a malformed ``\uXXXX``
        escape, on that call and on every later one.  The message is exactly
        ``Malformed \uxxxx encoding.``.
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

    The analogue of ``ConfigurationReader.getProperty`` at
    ``ConfigurationReader:27-29``, whose single-argument form returns ``null``
    for a key the file does not define.  ``default`` is ``None`` for the same
    reason: a configuration problem must surface at the point of use, not at
    start-up.

    No validation, no normalization and no type coercion is performed, by
    design.  In particular an unrecognised ``browser`` value is returned as
    written so that it reaches the driver and fails at first use, because the
    Java ``Driver`` switch has no default branch; rejecting it here would
    change behaviour.  Keys and values are neither case-folded nor stripped, so
    a mixed-case key must be looked up exactly as it is written in the file.

    Note the difference between an absent key and an empty one: a key present
    with no value yields ``""``, and only an absent key yields ``default``.

    An unreadable file still yields ``default`` for every key, since
    :func:`get_properties` reports it as an empty mapping.  A *malformed* file
    is the one case that raises rather than answering, because the load it
    triggers fails - see :func:`get_properties`.

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
