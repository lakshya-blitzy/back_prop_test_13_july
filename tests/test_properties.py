r"""Tests for ``app/utils/properties.py`` - the ``java.util.Properties`` reader.

This module owns the properties layer's whole evidence - the Java
``.properties`` grammar, the ISO-8859-1 I/O, the missing-file tolerance and the
one-shot process cache.  ``app/config.py`` is the reader's only production
importer, and ``tests/test_config.py`` covers the six-key accessor surface
layered over it rather than the reader itself.

The fixed strings and behaviours asserted below are parity, not preference.
Their authority is the pinned Java source at commit
``47e9d697e4a9a85da889f94a846fdf47af28a240``, AAP 0.6 ("Configuration: Java
properties semantics, initialization, and a real trap"), and JDK 8
``java.util.Properties`` measured on Temurin ``8u504-b01`` rather than read off
the port:

* ``ConfigurationReader.java:14`` - ``new FileInputStream("configuration.properties")``,
  a bare relative name resolved against the process working directory.
* ``ConfigurationReader.java:11,17`` - one load, in a static initializer,
  through ``Properties.load(InputStream)``; AAP deviation 17 scopes that to once
  per worker process.
* ``ConfigurationReader.java:21-24`` - an ``IOException`` prints ``File is not
  found in the ConfigurationReader class`` plus a stack trace and lets
  initialization complete.
* ``ConfigurationReader.java:27-29`` - ``getProperty`` answers ``null`` for an
  absent key.

AAP section 0.6 names three assertions for this module explicitly, and each is a
distinct test below: :func:`test_the_load_happens_exactly_once`,
:func:`test_a_missing_file_is_logged_exactly_once_per_process` and
:func:`test_a_later_file_change_does_not_alter_an_initialized_reader`.

The five phases, and the Java construct each mirrors
----------------------------------------------------
The production module implements the format by hand because ``configparser``
cannot read it - it rejects a section-less file outright and the real key set
includes dotted names such as ``web.table.url`` - and its three private helpers
mirror three JDK 8 state machines one for one.  The phases follow that seam:

=======  ==============================  =========================================
Phase    Subject                         Java construct mirrored
=======  ==============================  =========================================
1        The parity surface              ``ConfigurationReader``'s fixed strings
2        ``_read_logical_lines``         ``Properties$LineReader.readLine()``
3        ``parse_properties``            ``Properties.load0()``
4        ``_decode_escapes``             ``Properties.loadConvert()``
5        I/O and the one-shot cache      ``load(InputStream)`` + the static block
=======  ==============================  =========================================

Phases 2 and 4 address the two private helpers directly as well as through
:func:`~app.utils.properties.parse_properties`.  That is deliberate: each is a
documented state machine with branches that are either unobservable in the
parser's output (which logical lines were produced, as opposed to what they
parsed to) or unreachable through it at all - the guard at
``properties.py:274-281`` exists precisely so that a *direct* caller cannot
trip an ``IndexError`` - and this is the helpers' own unit-test module.

Every expectation in phases 2, 3 and 4 was verified against real JDK 8
``java.util.Properties.load`` before it was written here, on Temurin
``8u504-b01``, so these are measurements of the reference rather than readings
of the port's docstrings.

Three standing constraints
--------------------------
**No configuration value is ever invented.**  No ``configuration.properties``
exists at either revision, so no key's real value is known and none may be
guessed; the shipped template carries the six keys with empty values.  Every
value below is obviously synthetic, and the fixed names that *are* asserted -
``browser``, ``EmplTitle``, ``web.table.url`` - are key names drawn from the
AAP's frozen six-key inventory, never values.  There is no seventh key and no
locale key, and nothing here tests for one.

**Nothing is written outside ``tmp_path``.**  The reader resolves its filename
against the working directory at call time, so every filesystem test moves the
working directory with ``monkeypatch.chdir(tmp_path)`` first.  No test may leave
a ``configuration.properties`` in the repository root, where it would silently
become the configuration of every later test in the session.

**Every fixture file is written 0600**, through :func:`_write_properties` or
:func:`_write_properties_bytes`.  The reader refuses a configuration file with
any group or other permission bit set - review finding ``SEC2-F33``, since the
file carries ``username`` and ``password`` - and this host's umask produces
``0644``, so a new test that writes the file by hand would read an empty
mapping and pass or fail for the wrong reason.  The refusal classes themselves
are the one deliberate exception: each of them builds the unsafe file it is
about.

One simulated platform, and it says so
--------------------------------------
Everything above is measured on the host the suite runs on.  The exception is
the Windows object-bound read - the reader's own branch for the platform AAP
section 0.8 supports alongside Linux and macOS, where ``O_NOFOLLOW`` does not
exist and ``st_uid``/``st_mode`` carry no ownership or ACL information.  Its
tests live in their own block below, which opens by stating exactly what a
POSIX host can and cannot establish about it: the policy and the Win32
constants are verified directly, the sequencing and the refusal contract are
verified against a stand-in for the Win32 layer, and no test here claims
anything about how ``CreateFileW`` or ``GetSecurityInfo`` themselves behave.
"""

from __future__ import annotations

import ast
import ctypes
import errno
import logging
import os
import stat
import sys
import threading
import time
import types
from ctypes import wintypes
from pathlib import Path
from typing import Any, Callable, Final, NamedTuple

import pytest

from app.utils import properties
from app.utils.properties import (
    DEFAULT_ENCODING,
    MISSING_FILE_MESSAGE,
    PROPERTIES_FILENAME,
    get_properties,
    get_property,
    load_properties,
    parse_properties,
)

# ``reset_cache`` is imported from its defining module rather than from the
# ``app.utils`` barrel on purpose, and ``tests/conftest.py`` does the same: it is
# the module's declared test-support entry point, deliberately absent from
# ``properties.__all__`` and not re-exported by ``app/utils/__init__.py``.
# Phase 1 asserts that non-export as a contract.
from app.utils.properties import reset_cache

# =========================================================================== #
# Fixed values and helpers
# =========================================================================== #

#: The module under test, by its dotted name.  This is the logger name the
#: production module acquires through ``logging.getLogger(__name__)``, so it is
#: both the import target and the capture target.
MODULE_NAME: Final[str] = "app.utils.properties"

#: Repository root, from this file's own position: ``tests/`` -> root.  Used only
#: to read ``app/utils/properties.py`` as *text* for the import-boundary
#: assertion in phase 1.
REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]

#: The production module's own source file.
MODULE_SOURCE: Final[Path] = REPO_ROOT / "app" / "utils" / "properties.py"

#: A synthetic value used wherever a test needs *some* value.  It is not a URL,
#: a username, a password or a page title, and it is distinctive enough that
#: finding it anywhere it should not be is unambiguous.
SYNTHETIC_VALUE: Final[str] = "synthetic-test-value"

#: Key names from the AAP's frozen six-key inventory, used where a test needs a
#: *realistic* key.  ``EmplTitle`` earns its place: it is the one key whose
#: mixed case a ``configparser``-based reader would silently lower-case, which
#: is the concrete hazard the hand-written parser exists to avoid.
BROWSER_KEY: Final[str] = "browser"
EMPL_TITLE_KEY: Final[str] = "EmplTitle"
DOTTED_KEY: Final[str] = "web.table.url"

#: The names ``app/utils/properties.py`` publishes.  Asserted as a whole so that
#: an addition or a removal is a test failure rather than a silent change of
#: surface, and so that ``reset_cache``'s absence from it stays deliberate.
EXPECTED_ALL: Final[tuple[str, ...]] = (
    "DEFAULT_ENCODING",
    "MISSING_FILE_MESSAGE",
    "PROPERTIES_FILENAME",
    "get_properties",
    "get_property",
    "load_properties",
    "parse_properties",
)


#: The mode a configuration file must carry to be read at all: owner-only, no
#: group and no other permission bit.  The reader refuses anything wider
#: (``properties._FORBIDDEN_MODE_BITS``), because the file carries ``username``
#: and ``password``, so every fixture below has to be written with it.  The
#: process umask cannot be relied on to produce it: this host's is ``0022``,
#: which yields ``0644``.
OWNER_ONLY_MODE: Final[int] = 0o600


def _write_properties_bytes(directory: Path, data: bytes) -> Path:
    """Write ``data`` to ``directory``'s ``configuration.properties``, 0600.

    The byte-level fixture writer, for the tests that are *about* bytes -
    encoding, mojibake, CRLF and the all-256-byte case - where encoding text
    would defeat the point.

    The ``chmod`` is what makes the file readable *by the reader*: the secure
    read refuses a file with any group or other permission bit set, which is
    findings ``SEC2-F33`` and ``SEC2-F10``, so a fixture left at the umask's
    ``0644`` would be refused and every content assertion in this module would
    read an empty mapping instead.  It is applied here and in
    :func:`_write_properties` rather than in each test so that the requirement
    lives in one place per fixture shape.

    :param directory: The directory to write into - always a ``tmp_path``.
    :param data: The exact bytes to write.
    :returns: The path written.
    """
    target = directory / PROPERTIES_FILENAME
    target.write_bytes(data)
    os.chmod(target, OWNER_ONLY_MODE)
    return target


def _write_properties(directory: Path, text: str) -> Path:
    """Write ``text`` to ``directory``'s ``configuration.properties``, 0600.

    The bytes are encoded with the reader's own default encoding, which is what
    makes the file one the reader will decode back to ``text`` exactly, and the
    file is left owner-only for the reason :func:`_write_properties_bytes`
    gives.

    :param directory: The directory to write into - always a ``tmp_path``.
    :param text: The properties text to write.
    :returns: The path written.
    """
    return _write_properties_bytes(directory, text.encode(DEFAULT_ENCODING))


def _missing_file_records(
    caplog: pytest.LogCaptureFixture,
) -> list[logging.LogRecord]:
    """Return the reader's missing-file warnings captured so far.

    Filtered by logger name *and* message so that an unrelated warning from
    another logger cannot inflate a count, and so that a count assertion is
    about this module's records specifically.

    On capture: ``caplog`` is used with an explicit ``logger=`` argument
    throughout this module, and it is order-independent here for a reason worth
    recording, because it is not obvious.  ``configure_logging()`` sets
    ``propagate = False`` on the ``app`` logger permanently
    (``app/logging_config.py:472``) and ``create_app()`` calls it
    (``app/__init__.py:244``), so a session that built the viewer leaves that
    flag set.  pytest's ``catching_logs.__enter__`` attaches its capture handler
    to the root logger *and* to every logger already non-propagating when the
    test's capture phase begins, so a flag set by an earlier test is handled and
    capture still works.  What it cannot handle - its own source says so - is a
    logger that *becomes* non-propagating during the test.  No test in this
    module calls ``configure_logging``, which is what keeps the seam intact;
    nothing else here depends on logging configuration.

    A capture failure would drive every count assertion to ``0``, so none of
    them can pass vacuously.

    :param caplog: pytest's log-capture fixture.
    :returns: The matching records, in the order they were emitted.
    """
    return [
        record
        for record in caplog.records
        if record.name == MODULE_NAME and record.getMessage() == MISSING_FILE_MESSAGE
    ]


def _refusal_records(
    caplog: pytest.LogCaptureFixture,
) -> list[logging.LogRecord]:
    """Return the reader's *refusal* warnings captured so far.

    The counterpart of :func:`_missing_file_records`, and deliberately a
    separate filter: a refused file is not a missing file.  The production
    module's refusal message is a private constant precisely so that it cannot
    be confused with the parity string ``ConfigurationReader:22`` prints -
    :func:`_missing_file_records` matches that string exactly, so a refusal
    never inflates a missing-file count and vice versa.

    Matched on the message *prefix*, because a refusal record interpolates the
    fixed reason phrase and the filename after it.

    :param caplog: pytest's log-capture fixture.
    :returns: The matching records, in the order they were emitted.
    """
    return [
        record
        for record in caplog.records
        if record.name == MODULE_NAME
        and record.getMessage().startswith(properties._REFUSED_FILE_MESSAGE)
    ]


def _assert_refused(
    caplog: pytest.LogCaptureFixture,
    reason: str,
    *,
    target: Path,
) -> logging.LogRecord:
    """Assert the log holds exactly one refusal, for ``reason``, and no more.

    The whole refusal contract in one place, asserted the same way for every
    refusal class so that no class is proven more weakly than another:

    * exactly one refusal ``WARNING`` - a refusal is logged once, not once per
      check and not once per access;
    * that record names the expected fixed reason phrase, so a test cannot pass
      because the file was refused for some *other* reason;
    * it names the file by its final component and **not** by an absolute path;
    * no traceback, because an ``OSError``'s traceback embeds the absolute path
      the refusal record is not allowed to carry;
    * and **no missing-file record at all**, because the two outcomes are
      distinct in the log even though they are identical to a caller.

    :param caplog: pytest's log-capture fixture.
    :param reason: The expected ``properties._REASON_*`` phrase.
    :param target: The refused path, whose parent must not appear in the record.
    :returns: The single refusal record, for a caller with further assertions.
    """
    records = _refusal_records(caplog)
    assert len(records) == 1, [record.getMessage() for record in records]
    record = records[0]
    message = record.getMessage()

    assert record.levelno == logging.WARNING
    assert reason in message
    assert target.name in message
    assert str(target) not in message
    assert str(target.parent) not in message
    assert record.exc_info is None
    assert _missing_file_records(caplog) == []

    return record


class _LoadCounter:
    """A counting stand-in for :func:`~app.utils.properties.load_properties`.

    Installed over the module attribute with ``monkeypatch.setattr`` so that
    :func:`~app.utils.properties.get_properties` - which resolves
    ``load_properties`` as a module global at call time - goes through it.
    Counting the calls is the only direct instrument for "the load happened
    exactly once": the cache's own state cannot distinguish one load from two
    identical ones.

    The real function is still called, so the behaviour under test is the real
    behaviour and the missing-file log record is still emitted by the real code
    path.
    """

    def __init__(self, delay: float = 0.0) -> None:
        """Wrap the module's current ``load_properties``.

        :param delay: Seconds to sleep before delegating.  Used only by the
            concurrency test, to widen the window in which a second thread
            could start a second load if the lock did not hold.
        """
        self._real: Callable[..., dict[str, str]] = properties.load_properties
        self._delay = delay
        self._lock = threading.Lock()
        self.calls = 0

    def __call__(self, *args: Any, **kwargs: Any) -> dict[str, str]:
        """Record the call and delegate to the real loader."""
        with self._lock:
            self.calls += 1
        if self._delay:
            # Not a synchronisation device: the Barrier in the concurrency test
            # does that.  This only holds the first loader inside the critical
            # section long enough that a missing lock would be observable.
            time.sleep(self._delay)
        return self._real(*args, **kwargs)


@pytest.fixture
def counted_load(monkeypatch: pytest.MonkeyPatch) -> _LoadCounter:
    """Install a :class:`_LoadCounter` over the module's ``load_properties``.

    :param monkeypatch: pytest's patcher, which restores the real attribute at
        teardown whether the test passes or fails.
    :returns: The counter, for its ``calls`` attribute.
    """
    counter = _LoadCounter()
    monkeypatch.setattr(properties, "load_properties", counter)
    return counter


# =========================================================================== #
# The malformed-escape contract
#
# Measured on Temurin 8u504-b01, JDK 8 java.util.Properties.load raises
# java.lang.IllegalArgumentException("Malformed \uxxxx encoding.") for every
# malformed form - `\u`, `\u12`, `\u12zz`, and a malformed escape in a key -
# and ConfigurationReader's `catch (IOException)` (ConfigurationReader.java:21)
# does not catch it, so inside its static initializer it is a hard start-up
# failure.  AAP 0.6 holds the port to those semantics.
#
# The reader therefore raises a fixed, non-data-bearing ValueError - Python's
# analogue of that IllegalArgumentException - and returns no partially decoded
# value.  "Non-data-bearing" is half of the contract, not a nicety: `password`
# is one of the six configuration keys, so a message or a log record that
# echoed a fragment of the offending text would disclose configuration data.
#
# The four tests in phase 4 assert that unconditionally.  A condition on the
# reader's own behaviour, an expected-failure mark, a swallowed assertion or an
# "either raises or keeps the text" disjunction would all re-evaluate in every
# future pytest process, so a regression to a log-and-keep branch would pass
# unnoticed.
# =========================================================================== #


# =========================================================================== #
# Phase 1 - The parity surface
#
# ConfigurationReader's fixed strings and this module's public shape.  Each
# constant is asserted against its literal value as well as through the module
# attribute, so that a silent reword of a parity string is caught rather than
# travelling into every assertion that references the constant.
# =========================================================================== #


def test_properties_filename_is_the_bare_relative_name_java_opens() -> None:
    """``ConfigurationReader.java:14`` opens ``configuration.properties``.

    The name is bare and relative - not absolute, and not resolved against the
    package directory - because the *process working directory* decides which
    file is read.  Asserted literally so the parity value cannot drift.
    """
    assert PROPERTIES_FILENAME == "configuration.properties"
    assert not Path(PROPERTIES_FILENAME).is_absolute()
    assert Path(PROPERTIES_FILENAME).name == PROPERTIES_FILENAME


def test_missing_file_message_is_the_java_line_verbatim() -> None:
    """``ConfigurationReader.java:22`` prints this exact line.

    Parity means verbatim: no prefix, no suffix, no added punctuation and no
    interpolated path.  The literal is repeated here deliberately - asserting
    only ``MISSING_FILE_MESSAGE == MISSING_FILE_MESSAGE`` elsewhere in this
    module would let a reword pass unnoticed.
    """
    assert MISSING_FILE_MESSAGE == "File is not found in the ConfigurationReader class"
    assert not MISSING_FILE_MESSAGE.endswith(".")
    assert PROPERTIES_FILENAME not in MISSING_FILE_MESSAGE


def test_default_encoding_is_the_java_8_properties_default() -> None:
    """``Properties.load(InputStream)`` decodes ISO-8859-1 on Java 8.

    ``pom.xml:12-13`` pins Java 8 as both source and target level, so this is
    the encoding the reference applies.  The codec is resolved here as well as
    compared, because a name that no longer resolves would be a latent failure
    at the first read rather than at import.
    """
    assert DEFAULT_ENCODING == "iso-8859-1"
    assert bytes(range(256)).decode(DEFAULT_ENCODING)  # every byte maps


def test_public_surface_is_exactly_the_declared_all() -> None:
    """``__all__`` is the module's whole published surface.

    Asserted as a set equality rather than a containment so that an addition is
    a failure too: this module is imported by ``app/config.py`` alone, and its
    surface is meant to stay a generic reader's.
    """
    assert tuple(sorted(properties.__all__)) == tuple(sorted(EXPECTED_ALL))
    for name in EXPECTED_ALL:
        assert hasattr(properties, name), name


def test_reset_cache_is_test_support_and_not_public() -> None:
    """``reset_cache`` is reachable but unpublished, by design.

    Its docstring states why: production code calling it would reintroduce the
    mid-run re-read that parity with the JVM rules out.  It is therefore absent
    from ``__all__`` and not re-exported by the ``app.utils`` barrel, which is
    why this module - like ``tests/conftest.py`` - imports it from its defining
    module.
    """
    from app import utils

    assert callable(reset_cache)
    assert "reset_cache" not in properties.__all__
    assert not hasattr(utils, "reset_cache")


def test_module_imports_only_the_standard_library() -> None:
    """The bottom of the import graph imports nothing but the stdlib.

    AAP section 0.4.2 makes this an invariant rather than a preference: the
    reader has to be importable in a worker process that never builds a Flask
    application, so it may not import ``app.config``, ``app.logging_config``,
    ``app.utils.paths``, Flask, behave or selenium.  Asserted over the parsed
    module rather than by a text search, because the module's own docstring
    names every one of those things in order to rule it out and a substring grep
    would match the prose.
    """
    tree = ast.parse(MODULE_SOURCE.read_text(encoding="utf-8"))
    imported: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])

    assert imported, "the module must import something; an empty set means a bad parse"
    assert imported <= sys.stdlib_module_names, imported - sys.stdlib_module_names
    assert "app" not in imported


# =========================================================================== #
# Phase 2 - _read_logical_lines, mirroring Properties$LineReader.readLine()
#
# The splitter's job is to turn physical lines into logical ones: terminators,
# comments, blanks and backslash continuation.  It decodes nothing - the
# separator scan in phase 3 has to run against raw characters so that `\=`,
# `\:` and `\ ` do not read as separators - so these cases are about *which*
# logical lines come out, and phase 3 is about what they parse to.
# =========================================================================== #


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("", (), id="empty-input"),
        pytest.param("a=1", ("a=1",), id="single-line-no-terminator"),
        pytest.param("a=1\n", ("a=1",), id="single-line-lf"),
        pytest.param("a=1\nb=2", ("a=1", "b=2"), id="two-lines-lf"),
        pytest.param("a=1\r\nb=2", ("a=1", "b=2"), id="two-lines-crlf"),
        pytest.param("a=1\rb=2", ("a=1", "b=2"), id="two-lines-bare-cr"),
        pytest.param("\n\n\na=1", ("a=1",), id="leading-blank-lines"),
        pytest.param("a=1\n\n\nb=2", ("a=1", "b=2"), id="interior-blank-lines"),
        pytest.param("   \t\f  a=1", ("a=1",), id="leading-whitespace-skipped"),
        pytest.param("#comment\na=1", ("a=1",), id="hash-comment-dropped"),
        pytest.param("!comment\na=1", ("a=1",), id="bang-comment-dropped"),
        pytest.param("   # indented\na=1", ("a=1",), id="indented-comment-dropped"),
        pytest.param("a=1#not-a-comment", ("a=1#not-a-comment",), id="interior-hash"),
        pytest.param("a=1!not-a-comment", ("a=1!not-a-comment",), id="interior-bang"),
        pytest.param("a=one\\\ntwo", ("a=onetwo",), id="continuation-joined"),
        pytest.param("a=one\\\n   two", ("a=onetwo",), id="continuation-skips-indent"),
        pytest.param("a=one\\\r\ntwo", ("a=onetwo",), id="continuation-crlf"),
        pytest.param("a=one\\\rtwo", ("a=onetwo",), id="continuation-bare-cr"),
        pytest.param("a=one\\\ntwo\\\nthree", ("a=onetwothree",), id="continuation-x2"),
    ],
)
def test_logical_lines(text: str, expected: tuple[str, ...]) -> None:
    """``readLine()`` produces these logical lines for these inputs.

    Terminator forms, leading-whitespace skipping, comment and blank removal and
    backslash continuation, each as ``Properties$LineReader.readLine()``
    implements it and as JDK 8 was measured to behave.
    """
    assert tuple(properties._read_logical_lines(text)) == expected


def test_comment_line_never_continues_on_a_trailing_backslash() -> None:
    """A comment is a comment even when it ends in a backslash.

    ``readLine()`` tests "is this a comment" before "was there a trailing
    backslash", so the following line is *not* swallowed into the comment.  This
    is the trap that makes a commented-out continuation line safe, and JDK 8 was
    measured to agree: ``# c=1\\`` then ``k=v`` yields ``{"k": "v"}``.
    """
    text = "# commented\\\n" + f"{BROWSER_KEY}={SYNTHETIC_VALUE}"

    assert tuple(properties._read_logical_lines(text)) == (
        f"{BROWSER_KEY}={SYNTHETIC_VALUE}",
    )
    assert parse_properties(text) == {BROWSER_KEY: SYNTHETIC_VALUE}


def test_even_trailing_backslashes_are_not_a_continuation() -> None:
    """An even count of trailing backslashes ends the line.

    The final pair is an escaped backslash, not a continuation marker, so the
    next physical line stands on its own.  ``readLine()`` tracks parity rather
    than presence for exactly this case.
    """
    text = "a=one\\\\\nb=two"

    assert tuple(properties._read_logical_lines(text)) == ("a=one\\\\", "b=two")
    # Phase 4 decodes that trailing pair to a single backslash.
    assert parse_properties(text) == {"a": "one\\", "b": "two"}


def test_odd_trailing_backslash_at_end_of_input_is_dropped() -> None:
    """A continuation with nothing to continue onto loses its backslash.

    The JDK refills its buffer, sees end of input and returns the line without
    the marker; there is no error and no dangling backslash in the value.
    """
    assert tuple(properties._read_logical_lines("a=one\\")) == ("a=one",)
    assert parse_properties("a=one\\") == {"a": "one"}


def test_odd_trailing_backslash_before_a_final_terminator_is_dropped() -> None:
    """The same, when the terminator is the file's last character.

    This is the second of the two end-of-input paths in ``readLine()``: the
    terminator was read, the refill finds nothing, and the pending continuation
    backslash is discarded rather than joined to anything.
    """
    assert tuple(properties._read_logical_lines("a=one\\\n")) == ("a=one",)
    assert parse_properties("a=one\\\n") == {"a": "one"}


def test_empty_continuation_line_ends_the_logical_line() -> None:
    """A continuation onto a blank line terminates instead of continuing.

    The flag that suppresses blank-line skipping applies only to the *first*
    character of the continuation line, so an immediate terminator ends the
    logical line.  JDK 8 was measured to yield the same two entries.
    """
    text = "a=one\\\n\nb=two"

    assert tuple(properties._read_logical_lines(text)) == ("a=one", "b=two")
    assert parse_properties(text) == {"a": "one", "b": "two"}


def test_a_comment_at_end_of_input_yields_nothing() -> None:
    """A trailing comment with no terminator produces no logical line.

    The end-of-input branch discards a comment buffer rather than yielding it,
    which is the JDK's ``return -1``.
    """
    assert tuple(properties._read_logical_lines("#trailing comment")) == ()
    assert parse_properties("#trailing comment") == {}


def test_comments_and_blank_lines_alone_yield_no_entries() -> None:
    """A file of nothing but comments and blanks is an empty mapping.

    This is the shape of the shipped ``configuration.properties.example`` read
    through this parser if its six keys were commented out, and it must be
    indistinguishable in outcome from an empty file.
    """
    text = "# a comment\n! another\n\n   \n\t\n"

    assert tuple(properties._read_logical_lines(text)) == ()
    assert parse_properties(text) == {}


def test_continuation_preserves_interior_whitespace_but_skips_the_indent() -> None:
    """Only *leading* whitespace on a continuation line is dropped.

    Whitespace already inside the value survives the join, so a wrapped value
    reassembles exactly as the JDK reassembles it.
    """
    assert parse_properties("a=one two \\\n    three") == {"a": "one two three"}


# =========================================================================== #
# Phase 3 - parse_properties, mirroring Properties.load0()
#
# Where the key ends, where the value begins, and what happens to the entries.
# Every expectation here was measured against JDK 8 first.
# =========================================================================== #


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("k=v", id="equals"),
        pytest.param("k = v", id="equals-spaced"),
        pytest.param("k:v", id="colon"),
        pytest.param("k : v", id="colon-spaced"),
        pytest.param("k v", id="whitespace-only"),
        pytest.param("k\tv", id="tab-only"),
        pytest.param("k\fv", id="form-feed-only"),
        pytest.param("k   =   v", id="equals-wide"),
        pytest.param("k\t:\tv", id="colon-tabbed"),
    ],
)
def test_separator_forms_are_equivalent(text: str) -> None:
    """``=``, ``:`` and whitespace all separate a key from its value.

    ``load0()`` ends the key at the first unescaped ``=``, ``:``, space, tab or
    form feed, skips whitespace around it, and - when the key was terminated by
    whitespace - absorbs a following ``=`` or ``:`` as the separator.  So all
    nine forms above are one pair, which is what makes the format tolerant of
    however the file was hand-edited.
    """
    assert parse_properties(text) == {"k": "v"}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("k=", {"k": ""}, id="separator-then-nothing"),
        pytest.param("k=   ", {"k": ""}, id="separator-then-whitespace"),
        pytest.param("=v", {"": "v"}, id="no-key"),
        pytest.param("k==v", {"k": "=v"}, id="second-equals-is-value"),
        pytest.param("k:=v", {"k": "=v"}, id="colon-then-equals"),
        pytest.param("k=:v", {"k": ":v"}, id="equals-then-colon"),
        pytest.param("k v=w", {"k": "v=w"}, id="whitespace-wins-over-later-equals"),
        pytest.param("k=v w", {"k": "v w"}, id="value-keeps-interior-whitespace"),
        pytest.param(
            f"{DOTTED_KEY}={SYNTHETIC_VALUE}",
            {DOTTED_KEY: SYNTHETIC_VALUE},
            id="dotted-key",
        ),
    ],
)
def test_key_and_value_slicing(text: str, expected: dict[str, str]) -> None:
    """The key/value split lands exactly where ``load0()`` puts it.

    The dotted-key case is the one that rules ``configparser`` out on its own
    alongside the absent sections: ``web.table.url`` is a single key, not a
    path into a namespace.
    """
    assert parse_properties(text) == expected


def test_key_without_a_separator_maps_to_the_empty_string() -> None:
    """A lone key is present with an empty value, never absent.

    ``load0()`` yields ``{"browser": ""}`` for a bare ``browser`` line, and the
    distinction matters: ``get_property`` returns ``None`` only for a key that
    is *absent*, so an empty value and a missing key are different
    configurations and phase 5 asserts that they stay so.
    """
    parsed = parse_properties(BROWSER_KEY)

    assert parsed == {BROWSER_KEY: ""}
    assert parsed[BROWSER_KEY] is not None


def test_trailing_whitespace_in_a_value_is_preserved() -> None:
    """Java skips whitespace *before* a value and keeps everything after.

    This is the asymmetry people mistake for a bug.  The value runs to the end
    of the logical line, trailing spaces and tabs included, so a file edited
    with a trailing space carries it into the configuration.
    """
    assert parse_properties("k=v   ") == {"k": "v   "}
    assert parse_properties("k=  v \t ") == {"k": "v \t "}


@pytest.mark.parametrize(
    ("text", "expected_key"),
    [
        pytest.param("a\\=b=v", "a=b", id="escaped-equals"),
        pytest.param("a\\:b=v", "a:b", id="escaped-colon"),
        pytest.param("a\\ b=v", "a b", id="escaped-space"),
        pytest.param("a\\\tb=v", "a\tb", id="escaped-tab"),
    ],
)
def test_an_escaped_separator_stays_inside_the_key(
    text: str, expected_key: str
) -> None:
    """A backslash-escaped separator does not end the key.

    This is why the separator scan runs against *raw* characters and decoding
    happens afterwards per slice: decoding first would turn ``\\=`` into ``=``
    and split the key in the wrong place.
    """
    assert parse_properties(text) == {expected_key: "v"}


def test_duplicate_keys_keep_the_last_occurrence() -> None:
    """The last assignment wins, as it does for ``Properties.put``.

    A file that sets a key twice is not an error and is not a merge; the later
    line simply replaces the earlier one.
    """
    assert parse_properties(f"{BROWSER_KEY}=first\n{BROWSER_KEY}=second") == {
        BROWSER_KEY: "second"
    }


def test_a_bracketed_line_is_a_key_not_a_section() -> None:
    """This format has no section headers at all.

    ``[general]`` is simply a key whose name contains brackets and whose value
    is empty, and the keys that follow it are not scoped by it.  A reader that
    treated it as a section would silently reshape the whole configuration -
    and ``configparser`` would instead reject the section-less real file
    outright, which is the other half of why this parser is hand-written.
    """
    parsed = parse_properties(f"[general]\n{BROWSER_KEY}={SYNTHETIC_VALUE}")

    assert parsed == {"[general]": "", BROWSER_KEY: SYNTHETIC_VALUE}


def test_a_utf8_bom_is_not_stripped() -> None:
    """A BOM decoded as ISO-8859-1 becomes part of the first key.

    Java behaves identically - ``load(InputStream)`` has no BOM handling - so
    no stripping is done here either.  The consequence is worth pinning: the
    first key of a BOM-prefixed file does not match its own name, which is a
    real diagnosis a maintainer may need.
    """
    bom = "\ufeff".encode("utf-8").decode(DEFAULT_ENCODING)
    parsed = parse_properties(f"{bom}{BROWSER_KEY}={SYNTHETIC_VALUE}")

    assert parsed == {f"{bom}{BROWSER_KEY}": SYNTHETIC_VALUE}
    assert BROWSER_KEY not in parsed


def test_key_case_is_preserved_exactly() -> None:
    """``EmplTitle`` keeps its mixed case, which is the parser's whole point.

    ``configparser`` lower-cases option names by default, which would turn this
    key of the frozen six into ``empltitle`` and break every lookup against it.
    The hand-written parser exists partly to avoid exactly that.
    """
    parsed = parse_properties(f"{EMPL_TITLE_KEY}={SYNTHETIC_VALUE}")

    assert parsed == {EMPL_TITLE_KEY: SYNTHETIC_VALUE}
    assert EMPL_TITLE_KEY.lower() not in parsed


def test_an_interior_hash_or_bang_belongs_to_the_value() -> None:
    """Only a line's *first* non-whitespace character starts a comment.

    A ``#`` further along stays in the value, so a value containing a fragment
    identifier survives intact.
    """
    assert parse_properties("k=v#frag") == {"k": "v#frag"}
    assert parse_properties("k=v!frag") == {"k": "v!frag"}


def test_empty_text_yields_an_empty_mapping() -> None:
    """Nothing in, nothing out - and never ``None``."""
    assert parse_properties("") == {}


def test_parse_properties_returns_a_fresh_mutable_dict() -> None:
    """Each call returns a new plain ``dict`` the caller owns.

    The immutable view belongs to the cache in phase 5; the parser itself hands
    back an ordinary dict, and two calls must not share one.
    """
    first = parse_properties("k=v")
    second = parse_properties("k=v")

    assert first == second
    assert first is not second
    first["k"] = "mutated"
    assert second["k"] == "v"


def test_a_realistic_file_parses_as_a_whole() -> None:
    """Every phase-3 rule at once, on a file shaped like the real one.

    Comments, a blank line, the three separator styles, a dotted key, the
    mixed-case key, a wrapped value and a preserved trailing space - read
    together, because the rules interact and a per-rule pass does not prove the
    combination.
    """
    text = (
        "# Synthetic configuration - no real value appears in this suite.\n"
        "! bang comments are comments too\n"
        "\n"
        f"{BROWSER_KEY}=chrome\n"
        f"{DOTTED_KEY} : http://localhost/synthetic\n"
        "url http://localhost/synthetic/module\n"
        "username=synthetic-user\n"
        "password=synthetic-\\u0070ass \n"
        f"{EMPL_TITLE_KEY}=Synthetic \\\n"
        "    Title\n"
    )

    assert parse_properties(text) == {
        BROWSER_KEY: "chrome",
        DOTTED_KEY: "http://localhost/synthetic",
        "url": "http://localhost/synthetic/module",
        "username": "synthetic-user",
        "password": "synthetic-pass ",
        EMPL_TITLE_KEY: "Synthetic Title",
    }


# =========================================================================== #
# Phase 4 - _decode_escapes, mirroring Properties.loadConvert()
#
# Four control escapes, `\uXXXX`, and the rule that every other `\c` is simply
# `c`.  Well-formed decoding and the malformed-escape failure are asserted with
# equal unconditionality; the malformed half is stated in the malformed-escape
# contract block above.
# =========================================================================== #


@pytest.mark.parametrize(
    ("escape", "decoded"),
    [
        pytest.param("\\t", "\t", id="tab"),
        pytest.param("\\r", "\r", id="carriage-return"),
        pytest.param("\\n", "\n", id="newline"),
        pytest.param("\\f", "\f", id="form-feed"),
    ],
)
def test_control_escapes_decode(escape: str, decoded: str) -> None:
    """``\\t``, ``\\r``, ``\\n`` and ``\\f`` become their control characters.

    These four and no others: ``loadConvert()`` has exactly this table, which is
    why ``\\b`` is ``b`` rather than a backspace.
    """
    assert parse_properties(f"k=a{escape}b") == {"k": f"a{decoded}b"}


@pytest.mark.parametrize(
    ("escape", "decoded"),
    [
        pytest.param("\\u0041", "A", id="ascii-upper"),
        pytest.param("\\u00e9", "\u00e9", id="latin-small-e-acute-lowercase-hex"),
        pytest.param("\\u00E9", "\u00e9", id="latin-small-e-acute-uppercase-hex"),
        pytest.param("\\u0000", "\u0000", id="nul"),
        pytest.param("\\uffff", "\uffff", id="max-code-unit"),
        pytest.param("\\u20ac", "\u20ac", id="euro-sign-outside-latin-1"),
    ],
)
def test_well_formed_unicode_escape_decodes(escape: str, decoded: str) -> None:
    """``\\uXXXX`` with exactly four hex digits becomes that code unit.

    Both hex cases are accepted, the full ``0000``-``ffff`` range is in scope,
    and the euro sign matters specifically: it cannot be written as an
    ISO-8859-1 byte, so the escape is the only way a properties file can carry
    it and this is the mechanism that makes the six-key surface able to hold
    non-Latin-1 text at all.
    """
    assert parse_properties(f"k={escape}") == {"k": decoded}


def test_a_supplementary_character_is_written_as_a_surrogate_pair() -> None:
    """Two escapes, two code units - as Java writes and reads them.

    ``loadConvert()`` decodes each escape to one UTF-16 code unit, so a
    character above ``U+FFFF`` arrives as its surrogate pair.  JDK 8 was
    measured to produce the same two lone surrogates, so the pair is *not*
    recombined here: doing so would be a deviation.
    """
    assert parse_properties("k=\\ud83d\\ude00") == {"k": "\ud83d\ude00"}
    assert len(parse_properties("k=\\ud83d\\ude00")["k"]) == 2


@pytest.mark.parametrize(
    ("escape", "decoded"),
    [
        pytest.param("\\\\", "\\", id="backslash"),
        pytest.param("\\=", "=", id="equals"),
        pytest.param("\\:", ":", id="colon"),
        pytest.param("\\#", "#", id="hash"),
        pytest.param("\\!", "!", id="bang"),
        pytest.param("\\q", "q", id="unknown-letter"),
        pytest.param("\\b", "b", id="not-a-backspace"),
        pytest.param("\\0", "0", id="not-an-octal-escape"),
    ],
)
def test_any_other_escape_just_drops_the_backslash(escape: str, decoded: str) -> None:
    """``\\c`` is ``c`` for every ``c`` outside the table - not an error.

    This is Java's rule rather than a tolerance added here, and it is what makes
    ``\\q`` legal.  ``\\0`` and ``\\b`` are included to pin the two escapes a
    reader arriving from C or from Python would expect to mean something else.
    """
    assert parse_properties(f"k=a{escape}b") == {"k": f"a{decoded}b"}


def test_escapes_are_decoded_in_keys_as_well_as_values() -> None:
    """``loadConvert()`` runs over both slices of the line.

    So a key may carry an escape, and the decoded form is the key the
    configuration is looked up by.
    """
    assert parse_properties("\\u006bey=v") == {"key": "v"}
    assert parse_properties("a\\tb=v") == {"a\tb": "v"}


def test_decode_escapes_on_an_empty_slice_returns_empty_string() -> None:
    """An empty slice decodes to ``""``.

    Reached on every ``k=`` line - the value slice is empty - and asserted on
    the helper directly so the boundary is pinned where it is implemented.
    """
    assert properties._decode_escapes("abc", 1, 1) == ""
    assert properties._decode_escapes("", 0, 0) == ""


def test_decode_escapes_tolerates_a_slice_ending_in_a_lone_backslash() -> None:
    """A direct caller cannot trip an ``IndexError`` on a trailing backslash.

    Unreachable through :func:`~app.utils.properties.parse_properties`, whose
    slices can only end on an even number of backslashes, and guarded anyway at
    ``properties.py:274-281``.  That guard is only reachable from a direct call,
    which is what this test is.
    """
    assert properties._decode_escapes("a\\", 0, 2) == "a\\"
    assert properties._decode_escapes("\\", 0, 1) == "\\"


# --------------------------------------------------------------------------- #
# The malformed `\uXXXX` escape.  The contract, the JDK 8 measurement behind it
# and why these four tests carry no condition of any kind are stated in the
# malformed-escape contract block above.
# --------------------------------------------------------------------------- #

#: The malformed forms JDK 8 was measured to reject, each with
#: ``IllegalArgumentException("Malformed \uxxxx encoding.")``.  All synthetic.
MALFORMED_ESCAPE_CASES: Final[tuple[tuple[str, str], ...]] = (
    ("blitzy.probe=\\u", "no-digits"),
    ("blitzy.probe=\\u1", "one-digit"),
    ("blitzy.probe=\\u12", "two-digits"),
    ("blitzy.probe=\\u123", "three-digits"),
    ("blitzy.probe=\\u12zz", "non-hex-digits"),
    ("blitzy.probe=\\uzzzz", "all-non-hex"),
    ("blitzy.probe=\\u12zztail", "non-hex-then-more-text"),
    ("\\u12zz=synthetic", "malformed-in-the-key"),
)


@pytest.mark.parametrize(
    "text",
    [pytest.param(text, id=case_id) for text, case_id in MALFORMED_ESCAPE_CASES],
)
def test_a_malformed_unicode_escape_raises(text: str) -> None:
    """A malformed ``\\uXXXX`` fails the load, as it does on the JVM.

    The contract: a malformed ``\\uXXXX`` raises a fixed, non-data-bearing
    ``ValueError``, Python's analogue of the
    ``IllegalArgumentException("Malformed \\uxxxx encoding.")`` JDK 8 raises.
    Measured on Temurin 8u504-b01: every form above makes
    ``java.util.Properties.load`` throw it, and ``ConfigurationReader``'s
    ``catch (IOException)`` at ``ConfigurationReader.java:21`` does not catch
    it, so inside its static initializer it is a hard start-up failure.  AAP
    0.6 requires the port to reproduce those semantics, so keeping the text
    literally - logging a fragment of it and carrying on - would be an
    unauthorized behavioural deviation rather than a lenient reading.
    """
    with pytest.raises(ValueError):
        parse_properties(text)


def test_the_malformed_escape_message_discloses_no_configuration_text() -> None:
    """The exception message carries none of the key or the value.

    The *non-data-bearing* half of the contract is what is under test here: a
    malformed escape inside a password, a username or a URL must not put a
    fragment of it into a message or a log record, so the ``ValueError`` the
    reader raises is fixed text that names the fault and never the data - the
    same shape as JDK 8's ``IllegalArgumentException("Malformed \\uxxxx
    encoding.")``, whose message also carries none of the input.  Every
    fragment of this synthetic input is asserted absent; ``\\uxxxx`` itself is
    not data and is not asserted against.
    """
    key = "blitzy.probe.secret.key"
    secret = "p4ssphrase"
    tail = "TAILFRAGMENT"

    with pytest.raises(ValueError) as raised:
        parse_properties(f"{key}={secret}\\u12zz{tail}")

    # ``repr`` as well as ``str``: a fixed first argument would still disclose
    # the data if the exception carried a second one, and the repr is what a
    # traceback and a logged ``exc_info`` both render.
    rendered = f"{raised.value!s}\n{raised.value!r}"
    for fragment in (key, secret, tail, "12zz", f"{secret}\\u12zz"):
        assert fragment not in rendered, f"{fragment!r} disclosed in {rendered!r}"


def test_a_malformed_escape_yields_no_partially_decoded_value(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Nothing is returned and nothing is logged from the malformed value.

    The failure replaces the result rather than degrading it, so a caller
    cannot receive a value that is half-decoded and half-literal - and the
    earlier keys on the same text are not delivered either, because the load
    fails as a whole exactly as JDK 8's ``Properties.load`` does.  No log
    record carries the malformed text at any level either, which is the
    disclosure half of the same contract: a ``WARNING`` naming the fragment
    would put configuration data into the log that the exception message is
    careful to keep out of it.
    """
    secret = "p4ssphrase"
    returned: dict[str, str] | None = None

    with caplog.at_level(logging.DEBUG, logger=MODULE_NAME):
        with pytest.raises(ValueError):
            returned = parse_properties(f"first=kept\nblitzy.probe={secret}\\u12zz")

    # Nothing at all came back - not the half-decoded value, and not the
    # well-formed entry that preceded it on an earlier line.
    assert returned is None
    # And no record carries the data either, at any level: a log line naming
    # even the four characters of the escape would disclose configuration text.
    for record in caplog.records:
        assert secret not in record.getMessage()
        assert "12zz" not in record.getMessage()


def test_load_properties_does_not_swallow_a_malformed_escape(tmp_path: Path) -> None:
    """The failure propagates out of the I/O layer, as on the JVM.

    :func:`~app.utils.properties.load_properties` catches ``OSError`` and
    nothing else, which is the exact scope of ``ConfigurationReader``'s ``catch
    (IOException)`` at ``ConfigurationReader.java:21-24``.  A malformed escape
    is not an I/O fault - on the JVM it is an ``IllegalArgumentException``,
    which that handler does not cover - so the ``ValueError`` must travel out
    to the caller rather than being absorbed into the tolerant empty-mapping
    path; otherwise a corrupt file would read as "no configuration" and the
    fault would surface as a puzzle somewhere else entirely.
    """
    target = _write_properties(tmp_path, "blitzy.probe=\\u12zz")

    with pytest.raises(ValueError):
        load_properties(target)


# =========================================================================== #
# Phase 5 - load_properties, get_properties, get_property
#
# The I/O half of ConfigurationReader's static initializer, and the one-shot
# cache.  Every test here moves the working directory into tmp_path first, so
# no test can read - or create - a configuration.properties in the repository
# root.  The properties cache is reset before and after every test by the
# autouse `isolate_process_state` fixture in tests/conftest.py, which is what
# makes the cache-transition assertions possible in a single process; the tests
# that need a *second* transition inside one test call reset_cache() themselves.
# =========================================================================== #


def test_a_file_is_read_and_parsed_from_an_explicit_path(tmp_path: Path) -> None:
    """``load_properties(path)`` reads that path, uncached.

    The uncached entry point exists so a specific file can be loaded repeatedly
    and so the grammar is reachable without the process cache in the way.
    """
    target = _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")

    assert load_properties(target) == {BROWSER_KEY: SYNTHETIC_VALUE}
    assert load_properties(target) == {BROWSER_KEY: SYNTHETIC_VALUE}


def test_load_properties_accepts_a_string_path(tmp_path: Path) -> None:
    """``str`` and ``os.PathLike`` are both accepted, as the signature says."""
    target = _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")

    assert load_properties(str(target)) == {BROWSER_KEY: SYNTHETIC_VALUE}


def test_the_filename_is_resolved_against_the_cwd_at_call_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The *process working directory* decides the file, as at ``:14``.

    Not this package's location, not an absolute path, and not a value captured
    at import time - which is why two directories holding different files give
    two different results in one process.
    """
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    _write_properties(first, f"{BROWSER_KEY}=from-first")
    _write_properties(second, f"{BROWSER_KEY}=from-second")

    monkeypatch.chdir(first)
    assert load_properties() == {BROWSER_KEY: "from-first"}

    monkeypatch.chdir(second)
    assert load_properties() == {BROWSER_KEY: "from-second"}


def test_the_file_is_decoded_as_iso_8859_1_by_default(tmp_path: Path) -> None:
    """A high byte decodes as its Latin-1 character, as on Java 8."""
    target = _write_properties_bytes(tmp_path, b"k=caf\xe9\n")

    assert load_properties(target) == {"k": "caf\u00e9"}


def test_utf8_bytes_read_as_iso_8859_1_produce_java_s_mojibake(
    tmp_path: Path,
) -> None:
    """A UTF-8 file read with the Java 8 default is mis-decoded identically.

    This is not a defect to fix here: reading it as UTF-8 instead would be a
    deviation from ``Properties.load(InputStream)``, and the escape mechanism in
    phase 4 is how a properties file is meant to carry non-Latin-1 text.  Pinned
    so that the behaviour is a decision on record rather than a surprise.
    """
    target = _write_properties_bytes(tmp_path, "k=caf\u00e9\n".encode("utf-8"))

    assert load_properties(target) == {"k": "caf\u00c3\u00a9"}


def test_the_encoding_argument_overrides_the_default(tmp_path: Path) -> None:
    """``encoding=`` is honoured, for a caller that knows better.

    The parameter exists for a deliberate, explicit choice; the default remains
    the Java one, and no automatic detection and no fallback is applied.
    """
    target = _write_properties_bytes(tmp_path, "k=caf\u00e9\n".encode("utf-8"))

    assert load_properties(target, encoding="utf-8") == {"k": "caf\u00e9"}


def test_every_byte_value_decodes_rather_than_failing(tmp_path: Path) -> None:
    """ISO-8859-1 maps all 256 bytes, so a read cannot fail on content.

    No decoding fallback is needed or wanted: a file of arbitrary bytes decodes
    instead of raising, which is why the tolerance in this module is only ever
    about I/O faults.  The three bytes excluded from the value are structural
    rather than undecodable - ``\\n`` and ``\\r`` terminate the line and ``\\\\``
    starts an escape - so this covers every byte whose *decoding* is in
    question.
    """
    structural = {0x0A, 0x0D, 0x5C}
    payload = bytes(byte for byte in range(256) if byte not in structural)
    # A NUL leads the value so that no byte of the payload is mistaken for the
    # leading whitespace load0() skips before a value begins.
    target = _write_properties_bytes(tmp_path, b"allbytes=\x00" + payload)

    loaded = load_properties(target)

    assert loaded == {"allbytes": "\x00" + payload.decode(DEFAULT_ENCODING)}
    assert len(loaded["allbytes"]) == len(payload) + 1


def test_a_crlf_file_is_parsed(tmp_path: Path) -> None:
    """A file written on Windows reads the same as one written on Unix."""
    target = _write_properties_bytes(tmp_path, b"a=1\r\nb=2\r\n")

    assert load_properties(target) == {"a": "1", "b": "2"}


def test_an_empty_file_yields_an_empty_mapping(tmp_path: Path) -> None:
    """A present but empty file is a successful load of nothing.

    Distinct from a missing file: it is not logged, and phase 5 asserts below
    that it does not provoke a second load attempt either.
    """
    target = _write_properties_bytes(tmp_path, b"")

    assert load_properties(target) == {}


# --------------------------------------------------------------------------- #
# Missing-file tolerance - ConfigurationReader.java:21-24.
#
# `catch (IOException)` covers three realistic causes, and load_properties
# catches OSError for exactly the same three: the file is absent, a directory
# sits in its place, and it cannot be read.  All three must be tolerated
# identically - message verbatim, traceback attached, empty mapping returned,
# nothing raised.
# --------------------------------------------------------------------------- #


def test_an_absent_file_is_tolerated_and_logged_with_the_java_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The common case: no ``configuration.properties`` anywhere.

    ``ConfigurationReader.java:21-24`` prints the message plus a stack trace
    and lets the static initializer complete, so the port logs at ``WARNING``
    with the traceback attached and returns an empty mapping.  Nothing raises
    and nothing exits, which is what makes the unit suite, the viewer and
    ``--dry-run`` all work with no configuration present at all.
    """
    monkeypatch.chdir(tmp_path)
    assert not (tmp_path / PROPERTIES_FILENAME).exists()

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        assert load_properties() == {}

    records = _missing_file_records(caplog)
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    assert records[0].getMessage() == MISSING_FILE_MESSAGE


def test_a_directory_in_place_of_the_file_is_tolerated_identically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A directory named ``configuration.properties`` is an I/O fault, not a crash.

    The second of ``IOException``'s three realistic causes.  It raises
    ``IsADirectoryError``, a subclass of ``OSError``, and must be absorbed on
    the same path as an absent file.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / PROPERTIES_FILENAME).mkdir()

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        assert load_properties() == {}

    assert len(_missing_file_records(caplog)) == 1


def test_an_unreadable_file_is_tolerated_identically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A permission fault is absorbed exactly like a missing file.

    The third cause.  It is provoked by patching the open rather than by
    ``chmod``: this suite runs as root on its host, where mode ``000`` does not
    make a file unreadable, so a permission test written that way would pass
    without ever reaching the branch.

    The patched seam is ``os.open``, which is what the secure read of findings
    ``SEC2-F10`` and ``SEC2-F33`` calls - the reader no longer goes through
    ``Path.read_bytes``, so patching *that* would leave this test passing
    without exercising anything.  The stand-in refuses only the properties
    target and delegates every other path to the real ``os.open``, because
    pytest's own log capture and ``tmp_path`` machinery open files during the
    same window.

    ``PermissionError`` rather than ``FileNotFoundError`` on purpose: it is an
    ``OSError`` that is neither the absent-file case nor one of the errnos the
    reader routes to its refusal path, which is what makes this test prove the
    breadth of the tolerance rather than repeat the first case.
    """
    monkeypatch.chdir(tmp_path)
    target = _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")
    assert target.exists()

    real_open = os.open

    def _deny(path: Any, flags: int, mode: int = 0o777, **kwargs: Any) -> int:
        """Stand in for ``os.open`` and refuse the properties file only."""
        if str(path).endswith(PROPERTIES_FILENAME):
            raise PermissionError(13, "Permission denied")
        return real_open(path, flags, mode, **kwargs)

    monkeypatch.setattr(os, "open", _deny)

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        assert load_properties() == {}

    monkeypatch.undo()
    records = _missing_file_records(caplog)
    assert len(records) == 1
    assert records[0].exc_info is not None
    assert isinstance(records[0].exc_info[1], PermissionError)
    assert _refusal_records(caplog) == []


def test_the_missing_file_record_carries_the_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """``exc_info`` is the port of ``e.printStackTrace()`` at ``:23``.

    The message alone would lose the diagnosis - which of the three causes it
    was, and for which path - so the record carries the exception with it.
    """
    monkeypatch.chdir(tmp_path)

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        load_properties()

    record = _missing_file_records(caplog)[0]
    assert record.exc_info is not None
    assert isinstance(record.exc_info[1], OSError)


def test_the_missing_file_record_names_no_configuration_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The warning is the Java line and nothing else.

    This layer embeds no configuration value in any log record.  Here there is
    no content to leak - the file does not exist - so what is pinned is that the
    message is not extended with an interpolated path or any other detail beyond
    the traceback, which is where a path belongs.
    """
    monkeypatch.chdir(tmp_path)

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        load_properties()

    record = _missing_file_records(caplog)[0]
    assert record.getMessage() == MISSING_FILE_MESSAGE
    assert str(tmp_path) not in record.getMessage()


# --------------------------------------------------------------------------- #
# The secure read - review findings SEC2-F10 (MEDIUM, blocking, CWE-400/22)
# and SEC2-F33 (HIGH, blocking, CWE-732/522), both filed against the single
# `target.read_bytes()` call `load_properties` used to make.
#
# That call followed a symlink at the final component, opened FIFOs and
# devices, read an unlimited number of bytes once per worker process, and
# inspected neither ownership nor mode - for the file that carries the
# `username` and `password` of the system under test, in a checkout other local
# accounts may be able to traverse. The reader now opens one descriptor with
# O_NOFOLLOW and verifies the object through it.
#
# Every refusal class below is asserted the same way, through `_assert_refused`:
# an empty mapping, exactly one refusal WARNING naming the expected fixed
# reason and the filename only, no traceback, no missing-file record and
# nothing raised. That last part is the AAP 0.1.1 tolerance
# [ConfigurationReader.java:21-24]: a refused file behaves exactly like an
# absent one for every caller, so every one of the six configured keys reads as
# None at its point of use and no scenario, no viewer request and no --dry-run
# is stopped by it.
#
# These tests create their fixtures as the account running pytest - root on
# this host - so `st_uid == os.geteuid()` holds for a file they write and the
# permission bits are the check that actually bites. The one case that cannot
# be produced that way, a file owned by another account, is produced by
# patching `os.geteuid`.
# --------------------------------------------------------------------------- #


def test_an_owner_only_regular_file_is_accepted(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The positive control for the whole block: 0600 is read normally.

    Every refusal test below would also pass against a reader that refused
    *everything*, so this asserts the other side of the contract first: a
    regular, single-link, owner-only file belonging to the current account is
    read and parsed, with no refusal record at all.
    """
    target = _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")
    assert target.stat().st_mode & 0o777 == OWNER_ONLY_MODE

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        assert load_properties(target) == {BROWSER_KEY: SYNTHETIC_VALUE}

    assert _refusal_records(caplog) == []
    assert _missing_file_records(caplog) == []


def test_the_refusal_message_is_not_the_missing_file_message() -> None:
    """A refusal and a missing file are distinguishable in the log.

    ``MISSING_FILE_MESSAGE`` is parity with ``ConfigurationReader:22`` and is
    reserved for the three I/O faults the Java ``catch (IOException)`` covers.
    A refusal is a different event with a different cause, so it carries its
    own message - and that message must not *contain* the parity string
    either, or a reader filtering the log for it would count refusals as
    missing files.
    """
    assert properties._REFUSED_FILE_MESSAGE != MISSING_FILE_MESSAGE
    assert MISSING_FILE_MESSAGE not in properties._REFUSED_FILE_MESSAGE
    assert properties._REFUSED_FILE_MESSAGE not in MISSING_FILE_MESSAGE
    # Private, like every other name this hardening added: the published
    # surface is frozen at ``EXPECTED_ALL``.
    assert "_REFUSED_FILE_MESSAGE" not in properties.__all__


def test_a_symlink_in_place_of_the_file_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A link at the final component is refused, not followed.

    The first half of ``SEC2-F10``: the probe in the report planted a symlink
    where ``configuration.properties`` belongs and it was accepted, so any file
    the account can read could be redirected into the reader - and, through the
    six accessors, into a browser session or a report.  ``O_NOFOLLOW`` makes
    the *kernel* refuse the open, which is why this is race-free rather than a
    check-then-use.

    The link points at a perfectly valid, owner-only properties file, so
    nothing but the link itself can be the reason it is refused.
    """
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    source = _write_properties(elsewhere, f"{BROWSER_KEY}=linked")
    target = tmp_path / PROPERTIES_FILENAME
    target.symlink_to(source)
    monkeypatch.chdir(tmp_path)

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        assert load_properties() == {}

    _assert_refused(caplog, properties._REASON_LINK, target=target)


@pytest.mark.skipif(
    not hasattr(os, "mkfifo"), reason="os.mkfifo is POSIX-only"
)
def test_a_fifo_in_place_of_the_file_is_refused_promptly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A FIFO is refused, and the refusal does not block.

    The other half of the non-regular-object problem, and the reason
    ``O_NONBLOCK`` is in the open flags rather than being decoration: without
    it ``os.open`` on a FIFO blocks forever waiting for a writer, and the
    ``fstat`` rejection is never reached - a hung worker rather than a refused
    file.

    The load runs in a daemon thread joined with a timeout, so that a
    regression to a blocking open fails this test in ten seconds instead of
    hanging the suite with no diagnosis.
    """
    target = tmp_path / PROPERTIES_FILENAME
    os.mkfifo(target, OWNER_ONLY_MODE)
    monkeypatch.chdir(tmp_path)
    outcome: list[object] = []

    def _load() -> None:
        """Load in a thread, recording whatever came back."""
        outcome.append(load_properties())

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        worker = threading.Thread(target=_load, name="fifo-probe", daemon=True)
        worker.start()
        worker.join(timeout=10)

    assert not worker.is_alive(), "the open blocked on the FIFO"
    assert outcome == [{}]
    _assert_refused(caplog, properties._REASON_NOT_A_REGULAR_FILE, target=target)


def test_the_fallback_for_a_platform_without_o_nofollow_refuses_a_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Windows has no ``O_NOFOLLOW``, so the reader checks before opening.

    That branch is unreachable on this host - the flag exists here and the
    kernel does the work - so the capability flag the reader consults is
    patched to ``False`` to reach it.  What is proven is the *logic*: with no
    ``O_NOFOLLOW`` to rely on, the pre-open ``lstat`` refuses a link with the
    same reason phrase and the same empty mapping.

    The reader's docstring records what this test cannot prove, because no test
    can: the pre-open check leaves a TOCTOU window a link created between the
    ``lstat`` and the ``open`` would slip through, which is why it is the
    fallback and ``O_NOFOLLOW`` is the primary defence.
    """
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    source = _write_properties(elsewhere, f"{BROWSER_KEY}=linked")
    target = tmp_path / PROPERTIES_FILENAME
    target.symlink_to(source)
    monkeypatch.chdir(tmp_path)
    # Both halves of the platform are simulated: the capability flag *and* the
    # open flags, since leaving ``O_NOFOLLOW`` in the latter would let the
    # kernel refuse the link and this test would pass without the fallback
    # having run at all.
    monkeypatch.setattr(properties, "_O_NOFOLLOW_AVAILABLE", False)
    monkeypatch.setattr(
        properties, "_OPEN_FLAGS", properties._OPEN_FLAGS & ~os.O_NOFOLLOW
    )

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        assert load_properties() == {}

    _assert_refused(caplog, properties._REASON_LINK, target=target)


def test_the_fallback_for_a_platform_without_o_nofollow_refuses_a_reparse_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A Windows junction is a reparse point rather than a symlink.

    ``S_ISLNK`` is false for one, so the fallback tests the
    ``FILE_ATTRIBUTE_REPARSE_POINT`` bit of ``st_file_attributes`` as well -
    and a junction is precisely how the same redirection attack is written on
    Windows.  Neither the attribute nor the constant exists on POSIX, so both
    are emulated here: ``os.lstat`` is replaced with a stand-in reporting the
    attribute, and the constant the reader masks with is given its Windows
    value.  The subject is the reader's branch, which is real; only the
    platform around it is simulated.
    """
    monkeypatch.chdir(tmp_path)
    target = _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")
    windows_reparse_point = 0x400

    class _WindowsLinkStatus:
        """An ``lstat`` result as Windows would report for a junction."""

        st_mode = target.stat().st_mode
        st_file_attributes = windows_reparse_point

    def _lstat(path: Any, **kwargs: Any) -> Any:
        """Report the junction attribute for the properties target only."""
        if str(path).endswith(PROPERTIES_FILENAME):
            return _WindowsLinkStatus()
        return os.stat(path, follow_symlinks=False)

    monkeypatch.setattr(properties, "_O_NOFOLLOW_AVAILABLE", False)
    monkeypatch.setattr(
        properties, "_FILE_ATTRIBUTE_REPARSE_POINT", windows_reparse_point
    )
    monkeypatch.setattr(os, "lstat", _lstat)

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        assert load_properties() == {}

    monkeypatch.undo()
    _assert_refused(caplog, properties._REASON_LINK, target=target)


def test_a_file_that_grows_after_the_size_check_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The bounded read is a second line of defence, not a formality.

    ``st_size`` is read once, and a file can be appended to between that
    ``fstat`` and the read that follows - the cheap check would then pass and an
    unbounded read would proceed, which is exactly the resource exposure
    ``SEC2-F10`` reports.  The read therefore asks for one byte more than the
    cap and refuses if it gets it.

    The race is produced deterministically by understating the size: ``os.fstat``
    is patched to report ``0`` for the properties target while the file on disk
    is a byte over the cap, which is indistinguishable, from the reader's point
    of view, from a file that grew.  Refusing rather than truncating matters:
    a half-read configuration parses into a plausible mapping with keys
    missing, which would surface as a puzzling scenario failure instead of a
    refusal.
    """
    monkeypatch.chdir(tmp_path)
    # Padding comment lines rather than one huge line, so that the *only*
    # thing wrong with the file is its total size: were the growth check
    # removed, this file would parse cleanly to its single entry instead of
    # tripping one of the parse caps, and this test would still catch it.
    entry = f"{BROWSER_KEY}={SYNTHETIC_VALUE}\n"
    padding = "#" + "g" * 98 + "\n"
    target = _write_properties(
        tmp_path, entry + padding * (properties._MAX_FILE_BYTES // 100 + 1)
    )
    assert target.stat().st_size > properties._MAX_FILE_BYTES

    real_fstat = os.fstat

    def _fstat(descriptor: int) -> os.stat_result:
        """Report a size of zero for a regular file, as a stale stat would."""
        status = real_fstat(descriptor)
        if stat.S_ISREG(status.st_mode) and status.st_size > 0:
            fields = list(status)
            fields[stat.ST_SIZE] = 0
            return os.stat_result(tuple(fields))
        return status

    monkeypatch.setattr(os, "fstat", _fstat)

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        assert load_properties() == {}

    monkeypatch.undo()
    _assert_refused(caplog, properties._REASON_TOO_LARGE, target=target)


def test_a_hard_linked_file_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A second hard link to the credentials is refused.

    The case the mode check cannot see: the link count is a property of the
    inode, so another account holding a link keeps its own name for the file
    and keeps reading it after the checkout's copy is replaced or removed, with
    the permission bits on the inode saying nothing about it.  ``st_nlink > 1``
    is the only signal available, and it is checked through the same descriptor
    as everything else.
    """
    target = _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")
    os.link(target, tmp_path / "second-name")
    assert target.stat().st_nlink == 2
    monkeypatch.chdir(tmp_path)

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        assert load_properties() == {}

    _assert_refused(caplog, properties._REASON_MULTIPLE_HARD_LINKS, target=target)


def test_a_group_or_world_accessible_file_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """``SEC2-F33`` itself: a 0644 credential file is not read.

    The finding is precise about the shape of the exposure - "a 0644 credential
    file in a traversable checkout is read" - and 0644 is exactly what the
    default umask on a developer machine or a CI agent produces, so this is the
    likely state of a real ``configuration.properties`` rather than an exotic
    one.  Refusing it is what makes the 0600-equivalent requirement enforced
    rather than documented.

    Each bit outside the owner triad is checked separately, because a mask
    written as ``0o007`` or ``0o070`` would pass a test that only ever tried
    ``0o644``.
    """
    monkeypatch.chdir(tmp_path)
    target = _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")

    for mode in (0o644, 0o604, 0o640, 0o601, 0o610, 0o606, 0o660):
        caplog.clear()
        os.chmod(target, mode)

        with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
            assert load_properties() == {}, oct(mode)

        _assert_refused(caplog, properties._REASON_OPEN_PERMISSIONS, target=target)

    # And the owner-only modes are still read, so the mask rejects group and
    # other permissions without rejecting the owner's own.
    for mode in (0o600, 0o400):
        caplog.clear()
        os.chmod(target, mode)

        with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
            assert load_properties() == {BROWSER_KEY: SYNTHETIC_VALUE}, oct(mode)

        assert _refusal_records(caplog) == []


def test_a_file_owned_by_another_account_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A credential file planted by another local account is refused.

    The ownership half of ``SEC2-F33``.  A real uid change is not available -
    this suite runs as root, and dropping privileges mid-process would break
    every later test in the session - so the *comparison* is moved instead:
    ``os.geteuid`` is patched to report a different account, which is
    indistinguishable, from the check's point of view, from a file whose
    ``st_uid`` belongs to someone else.

    The file is otherwise perfect - regular, single-link, 0600, small - so
    ownership is the only thing that can refuse it.
    """
    monkeypatch.chdir(tmp_path)
    target = _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")
    foreign_uid = target.stat().st_uid + 1
    monkeypatch.setattr(os, "geteuid", lambda: foreign_uid)

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        assert load_properties() == {}

    _assert_refused(caplog, properties._REASON_FOREIGN_OWNER, target=target)


def test_a_file_at_the_size_cap_is_read_and_one_byte_over_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The total-size bound, asserted at the boundary rather than near it.

    ``SEC2-F10``'s resource half: the previous read was unlimited and happened
    once per worker process, so an oversized file multiplied by the worker
    count.  A file of exactly :data:`properties._MAX_FILE_BYTES` is still read,
    and one byte more is refused, which is what makes the cap a boundary rather
    than an approximation.

    The padding is comment lines: they are discarded by the logical-line
    splitter, so the file is large without tripping the entry, key, value or
    line caps, and the one real entry proves the file was parsed rather than
    merely accepted.
    """
    monkeypatch.chdir(tmp_path)
    entry = f"{BROWSER_KEY}={SYNTHETIC_VALUE}\n"
    padding = "#" + "p" * 98 + "\n"
    assert len(padding) == 100

    body = entry + padding * ((properties._MAX_FILE_BYTES - len(entry)) // 100)
    body += "#" + "q" * (properties._MAX_FILE_BYTES - len(body) - 2) + "\n"
    target = _write_properties(tmp_path, body)
    assert target.stat().st_size == properties._MAX_FILE_BYTES

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        assert load_properties() == {BROWSER_KEY: SYNTHETIC_VALUE}

    assert _refusal_records(caplog) == []

    caplog.clear()
    target = _write_properties(tmp_path, body + "!")
    assert target.stat().st_size == properties._MAX_FILE_BYTES + 1

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        assert load_properties() == {}

    _assert_refused(caplog, properties._REASON_TOO_LARGE, target=target)


def test_the_reported_two_mebibyte_value_probe_is_refused_and_leaks_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """``SEC2-F10``'s own probe, reproduced: a 2 MiB single value.

    The report states that probe "was accepted".  It is refused here, and this
    is also the natural vehicle for the other half of the contract: **the
    refusal record carries nothing read from the file**.  The value is written
    under the ``password`` key - one of the six configured keys, and the reason
    every message in this module is fixed and data-free - with a distinctive
    marker repeated through it, and no record at any level may contain that
    marker, the key, or the absolute path.
    """
    monkeypatch.chdir(tmp_path)
    marker = "PROBEMARKER"
    oversized = marker * ((2 * 1024 * 1024) // len(marker))
    target = _write_properties(tmp_path, f"password={oversized}\n")
    assert target.stat().st_size > 2 * 1024 * 1024

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        assert load_properties() == {}

    record = _assert_refused(caplog, properties._REASON_TOO_LARGE, target=target)
    assert marker not in record.getMessage()
    for captured in caplog.records:
        message = captured.getMessage()
        assert marker not in message
        assert "password" not in message
        assert str(tmp_path) not in message
    # Nothing of the value reaches the formatted arguments either, which a
    # message-only assertion would miss for a lazily interpolated record.
    assert marker not in str(record.args)


def test_more_entries_than_the_cap_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The entry-count cap, inside a file well under the size cap.

    Six keys are configured, so 512 is generous by two orders of magnitude; a
    file with more than that is not a configuration file, and parsing it builds
    a dict an untrusted file chose the size of.  The cap counts *entries* rather
    than lines, so a file repeating one key stays acceptable - which the second
    half of this test asserts, because a cap on lines would be a different and
    wrong contract.
    """
    monkeypatch.chdir(tmp_path)
    over = "".join(f"key{index}=v\n" for index in range(properties._MAX_ENTRIES + 1))
    target = _write_properties(tmp_path, over)
    assert target.stat().st_size < properties._MAX_FILE_BYTES

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        assert load_properties() == {}

    _assert_refused(caplog, properties._REASON_TOO_MANY_ENTRIES, target=target)

    caplog.clear()
    repeated = f"{BROWSER_KEY}=v\n" * (properties._MAX_ENTRIES + 10)
    target = _write_properties(
        tmp_path, repeated + f"{BROWSER_KEY}={SYNTHETIC_VALUE}\n"
    )

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        assert load_properties() == {BROWSER_KEY: SYNTHETIC_VALUE}

    assert _refusal_records(caplog) == []


def test_an_over_long_key_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A key longer than the cap is refused, and one at the cap is not.

    The longest configured key is ``web.table.url`` at thirteen characters, so
    512 bounds the shape without bounding any real file.  Asserted at the
    boundary so the comparison cannot be off by one in the permissive
    direction.
    """
    monkeypatch.chdir(tmp_path)
    target = _write_properties(
        tmp_path, "k" * (properties._MAX_KEY_CHARACTERS + 1) + "=v\n"
    )

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        assert load_properties() == {}

    _assert_refused(caplog, properties._REASON_KEY_TOO_LONG, target=target)

    caplog.clear()
    at_cap = "k" * properties._MAX_KEY_CHARACTERS
    _write_properties(tmp_path, f"{at_cap}=v\n")

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        assert load_properties() == {at_cap: "v"}

    assert _refusal_records(caplog) == []


def test_an_over_long_value_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A value longer than the cap is refused, and one at the cap is not.

    The cap the report's 2 MiB probe is aimed at, here in the form that
    reaches the parser: a file small enough to pass the size check carrying one
    value far larger than any URL, page title or credential.  The longest
    plausible real value is a URL with query parameters, well inside 4096.
    """
    monkeypatch.chdir(tmp_path)
    target = _write_properties(
        tmp_path, "k=" + "v" * (properties._MAX_VALUE_CHARACTERS + 1) + "\n"
    )
    assert target.stat().st_size < properties._MAX_FILE_BYTES

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        assert load_properties() == {}

    _assert_refused(caplog, properties._REASON_VALUE_TOO_LONG, target=target)

    caplog.clear()
    at_cap = "v" * properties._MAX_VALUE_CHARACTERS
    _write_properties(tmp_path, f"k={at_cap}\n")

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        assert load_properties() == {"k": at_cap}

    assert _refusal_records(caplog) == []


def test_an_over_long_logical_line_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A logical line longer than the cap is refused before it is scanned.

    The cap that bounds the *assembled* line rather than either of its halves,
    which matters because backslash continuation lets a file build one logical
    line out of arbitrarily many physical ones - each of them short enough to
    look unremarkable.  The line is assembled from continuations here for
    exactly that reason, and it is refused for the line cap rather than the
    value cap because the length check precedes the key scan.
    """
    monkeypatch.chdir(tmp_path)
    continued = "k=" + "".join(
        "c" * 80 + "\\\n" for _ in range(properties._MAX_LINE_CHARACTERS // 80 + 2)
    )
    target = _write_properties(tmp_path, continued + "end\n")
    assert target.stat().st_size < properties._MAX_FILE_BYTES

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        assert load_properties() == {}

    _assert_refused(caplog, properties._REASON_LINE_TOO_LONG, target=target)


def test_a_directory_takes_the_missing_file_path_and_not_a_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The three tolerated I/O faults keep their own message, unchanged.

    The seam between the two paths, asserted from the refusal side.  A
    directory is one of ``catch (IOException)``'s three realistic causes and
    ``O_RDONLY`` on a directory *succeeds* on Linux, so the reader converts the
    ``fstat`` result into ``IsADirectoryError`` rather than refusing it - which
    is what keeps ``MISSING_FILE_MESSAGE`` and its traceback byte for byte what
    ``ConfigurationReader:21-24`` prints.  A refusal record here instead would
    be a regression in parity, not an improvement in security.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / PROPERTIES_FILENAME).mkdir()

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        assert load_properties() == {}

    records = _missing_file_records(caplog)
    assert len(records) == 1
    assert records[0].exc_info is not None
    assert isinstance(records[0].exc_info[1], IsADirectoryError)
    assert _refusal_records(caplog) == []


def test_a_refused_file_is_not_retried_and_never_logs_twice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    counted_load: _LoadCounter,
) -> None:
    """A refusal is as one-shot as a successful load or a missing file.

    The refusal path returns an empty mapping rather than raising, so it goes
    through ``get_properties``' normal completion: the attempt flag is set, the
    cache is the empty mapping, and the file is never opened again - not even
    if it is made safe a moment later, which is the same never-re-read
    guarantee AAP section 0.6 names and the JVM enforces.  One record per
    process, and it stays a refusal record rather than becoming a missing-file
    one on the second access.
    """
    monkeypatch.chdir(tmp_path)
    target = _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")
    os.chmod(target, 0o644)

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        assert dict(get_properties()) == {}
        assert dict(get_properties()) == {}
        # Made safe after the attempt: still not re-read.
        os.chmod(target, OWNER_ONLY_MODE)
        assert dict(get_properties()) == {}
        assert get_property(BROWSER_KEY) is None

    assert counted_load.calls == 1
    _assert_refused(caplog, properties._REASON_OPEN_PERMISSIONS, target=target)


def test_get_property_on_a_refused_file_returns_none_for_every_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The tolerance, stated as the six accessors' consumers see it.

    This is the assertion that makes refusing a file safe to do at all.  AAP
    section 0.4.2's configuration surface says none of the six keys is required
    at start-up: a missing file is logged and execution continues, and a
    missing key returns ``null`` so failures surface at the point of use.  A
    refused file must be indistinguishable from that - ``None`` for every one
    of the six, nothing raised, and the empty view still a read-only mapping -
    or the hardening would have turned a warning into an outage.

    The six names below are key names from the AAP's frozen inventory, never
    values; the file's contents are synthetic.
    """
    monkeypatch.chdir(tmp_path)
    configured_keys = (
        BROWSER_KEY,
        DOTTED_KEY,
        "url",
        "username",
        "password",
        EMPL_TITLE_KEY,
    )
    target = _write_properties(
        tmp_path, "".join(f"{key}={SYNTHETIC_VALUE}\n" for key in configured_keys)
    )
    os.chmod(target, 0o644)

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        for key in configured_keys:
            assert get_property(key) is None, key
        assert get_property(BROWSER_KEY, "fallback") == "fallback"
        assert dict(get_properties()) == {}

    assert len(_refusal_records(caplog)) == 1
    for record in caplog.records:
        assert SYNTHETIC_VALUE not in record.getMessage()


# --------------------------------------------------------------------------- #
# The Windows object-bound read - the non-POSIX branch of review findings
# SEC2-F33 (HIGH, blocking, CWE-732/522) and SEC2-F10 (MEDIUM, blocking,
# CWE-400/22).
#
# The POSIX half of both findings is asserted by the block above. This block is
# about the other supported platform, and about a re-review that found the
# non-POSIX branch hollow on both counts:
#
#   * `_POSIX_IDENTITY_AVAILABLE` is `hasattr(os, "geteuid")` and it gated BOTH
#     the ownership and the permission check, so on Windows a credential-bearing
#     configuration.properties was read with no ownership and no ACL check at
#     all - SEC2-F33 unfixed on that platform.
#   * the anti-link defence was an `lstat` BEFORE the open, so a junction
#     swapped in between the check and the open was followed and the later
#     `fstat` described the target rather than the link - SEC2-F10's no-follow
#     half unfixed on that platform.
#
# AAP section 0.8 states "Platform support is Windows, Linux and macOS" and
# README.md documents the PowerShell runner, so both findings have to hold
# there. The reader now opens Windows files with CreateFileW and
# FILE_FLAG_OPEN_REPARSE_POINT, classifies and verifies that HANDLE, and only
# then converts it to the descriptor the shared bounded read uses.
#
# WHAT CAN AND CANNOT BE EXERCISED ON THIS HOST. This is a Linux container:
# ctypes.WinDLL, CreateFileW, GetSecurityInfo and msvcrt do not exist, so
# `properties._WindowsObjectBinding` is never instantiated here and no test
# below proves that a real Win32 call behaves as documented. What the tests do
# prove, and what each one is careful to state:
#
#   1. the POLICY - "is the owner the running account, and does any ACE name a
#      trustee outside the owner-equivalent set" - directly, as a pure function
#      over fabricated binary SIDs;
#   2. the CONSTANTS every Win32 call passes, against their WinNT.h values,
#      because a constant that silently became 0 would disable the check that
#      masks with it;
#   3. the SEQUENCING, the refusal classes, the fail-closed conversion, the
#      handle lifecycle and the shared bounded read, by driving the platform
#      marker and replacing `properties._windows_object_binding` - the seam the
#      production module exposes for exactly this - with a stand-in that offers
#      the same four operations over an ordinary POSIX descriptor.
#
# `test_the_stand_in_offers_the_real_binding_surface` is what keeps (3) honest:
# a stand-in whose method names drifted from the real class would exercise a
# sequence nothing on Windows performs.
#
# One consequence of running on a POSIX host worth stating once: with the
# marker driven true, `_POSIX_IDENTITY_AVAILABLE` is still true here, so the
# POSIX `fstat` checks run as well. Every fixture below is 0600 and owned by
# the account running pytest, so those checks always pass, and every Windows
# refusal asserted below is raised before a descriptor exists at all - which is
# why none of them can be the POSIX check in disguise.
# --------------------------------------------------------------------------- #


def _binary_sid(authority: int, *subauthorities: int) -> bytes:
    """Build the binary form of a SID, as Win32 structures carry it.

    The layout ``CreateWellKnownSid`` and ``GetSecurityInfo`` produce, so the
    fabricated facts below are the shape of the real thing rather than opaque
    markers: one revision byte (always 1), one subauthority count, a six-byte
    big-endian identifier authority, then the subauthorities little-endian.

    The production policy compares these values with ``==``, which is the
    predicate ``EqualSid`` computes over the same bytes, so byte-built SIDs are
    a faithful input to it.

    :param authority: The identifier authority - 5 for ``NT AUTHORITY``.
    :param subauthorities: The subauthority chain.
    :returns: The binary SID.
    """
    return b"".join(
        (
            bytes((1, len(subauthorities))),
            authority.to_bytes(6, "big"),
            *(value.to_bytes(4, "little") for value in subauthorities),
        )
    )


#: ``LocalSystem``, ``S-1-5-18``, and ``BUILTIN\\Administrators``,
#: ``S-1-5-32-544``.  The two principals the production policy treats as
#: owner-equivalent, because each already holds administrative access to every
#: file on the machine, so an ACE naming one grants nothing new.  The reader
#: obtains them through ``CreateWellKnownSid``; these are the same two SIDs in
#: the binary form that call returns.
LOCAL_SYSTEM_SID: Final[bytes] = _binary_sid(5, 18)
ADMINISTRATORS_SID: Final[bytes] = _binary_sid(5, 32, 544)

#: A synthetic local account and a second, different one.  ``S-1-5-21-<domain
#: identifier>-<relative identifier>`` is the shape of a real machine account;
#: the domain identifier here is obviously synthetic.
ACCOUNT_SID: Final[bytes] = _binary_sid(5, 21, 1, 2, 3, 1001)
FOREIGN_ACCOUNT_SID: Final[bytes] = _binary_sid(5, 21, 1, 2, 3, 1002)

#: ``BUILTIN\\Users``, ``S-1-5-32-545``, and ``Everyone``, ``S-1-1-0``.  The two
#: trustees that make a credential file "accessible beyond its owner" on
#: Windows - ``Users`` is the one a checkout directory contributes by
#: inheritance, and it is the direct analogue of the 0644 file ``SEC2-F33``
#: reports.
USERS_SID: Final[bytes] = _binary_sid(5, 32, 545)
EVERYONE_SID: Final[bytes] = _binary_sid(1, 0)


def _accepted_windows_facts(
    **overrides: Any,
) -> properties._WindowsSecurityFacts:
    """Build facts the production policy accepts, with optional overrides.

    The positive control for every fabricated case: owner is the running
    account, and the DACL grants that account alone.  A test that wants a
    refusal overrides exactly the one field it is about, so nothing else can be
    the reason.

    :param overrides: Field values to replace.
    :returns: The facts.
    """
    accepted = properties._WindowsSecurityFacts(
        owner=ACCOUNT_SID,
        account=ACCOUNT_SID,
        owner_equivalent=(LOCAL_SYSTEM_SID, ADMINISTRATORS_SID),
        allowed_trustees=(ACCOUNT_SID,),
    )
    return accepted._replace(**overrides)


class _WindowsBindingStandIn:
    """A stand-in for the Win32 layer, over an ordinary POSIX descriptor.

    Offers the four operations ``properties._WindowsObjectBinding`` offers -
    ``open_handle``, ``file_attributes``, ``security_facts``,
    ``descriptor_from_handle``, ``close_handle`` - so that
    ``properties._open_windows_verified_descriptor`` can be driven end to end
    on a host that has no Win32 API.  What it stands in for and what it does
    not:

    * the "handle" it returns is a real file descriptor from ``os.open``.  On
      Windows ``open_osfhandle`` wraps a handle in a descriptor and the handle
      is thereafter owned by that descriptor, so here the transfer is the
      identity - and closing the descriptor still closes the object, which is
      exactly the ownership property the production comment marks.
    * the attributes and the security facts are fabricated.  No Win32 query
      runs, so these tests say nothing about whether
      ``GetFileInformationByHandleEx`` or ``GetSecurityInfo`` behave as
      documented; they are about what the reader does with the answers.
    * any of the three operations can be made to raise, which is how the
      fail-closed conversion is reached: a real ``ctypes`` fault is not
      producible here either.

    Every handle it hands out and every one it closes is recorded, so a leaked
    handle on a refusal path is a visible failure rather than an invisible one.
    """

    def __init__(
        self,
        *,
        attributes: int = 0,
        facts: properties._WindowsSecurityFacts | None = None,
        open_error: BaseException | None = None,
        attribute_error: BaseException | None = None,
        security_error: BaseException | None = None,
    ) -> None:
        """Configure what the stand-in reports, or raises.

        :param attributes: The Win32 file attribute bits to report.
        :param facts: The security facts to report.  Defaults to the accepted
            ones, so a test that is not about them cannot be refused for them.
        :param open_error: Raised by :meth:`open_handle` instead of opening.
        :param attribute_error: Raised by :meth:`file_attributes`.
        :param security_error: Raised by :meth:`security_facts`.
        """
        self.attributes = attributes
        self.facts = _accepted_windows_facts() if facts is None else facts
        self.open_error = open_error
        self.attribute_error = attribute_error
        self.security_error = security_error
        self.handles: list[int] = []
        self.closed_handles: list[int] = []
        self.descriptors: list[int] = []

    def open_handle(self, target: Path) -> int:
        """Open ``target`` and return the descriptor as the "handle"."""
        if self.open_error is not None:
            raise self.open_error
        handle = os.open(target, os.O_RDONLY)
        self.handles.append(handle)
        return handle

    def file_attributes(self, handle: int) -> int:
        """Report the configured attribute bits for a handle it issued."""
        assert handle in self.handles, "classified a handle it never issued"
        if self.attribute_error is not None:
            raise self.attribute_error
        return self.attributes

    def security_facts(self, handle: int) -> properties._WindowsSecurityFacts:
        """Report the configured facts for a handle it issued."""
        assert handle in self.handles, "queried a handle it never issued"
        if self.security_error is not None:
            raise self.security_error
        return self.facts

    def descriptor_from_handle(self, handle: int) -> int:
        """Transfer the handle to a descriptor - the identity, here."""
        assert handle in self.handles, "converted a handle it never issued"
        self.descriptors.append(handle)
        return handle

    def close_handle(self, handle: int) -> None:
        """Close a handle that never reached a descriptor."""
        self.closed_handles.append(handle)
        os.close(handle)


def _install_windows_binding(
    monkeypatch: pytest.MonkeyPatch, binding: object
) -> None:
    """Select the Windows branch and give it ``binding``.

    Both halves are needed: the marker decides the dispatch in
    ``properties._open_object_bound_descriptor``, and the factory is the seam
    that decides which Win32 layer that branch talks to.

    :param monkeypatch: pytest's patcher, which restores both at teardown.
    :param binding: The stand-in, or a callable-free object that raises.
    """
    monkeypatch.setattr(properties, "_WINDOWS_PLATFORM", True)
    monkeypatch.setattr(properties, "_windows_object_binding", lambda: binding)


def _assert_handle_closed(descriptor: int) -> None:
    """Assert ``descriptor`` is no longer open.

    The evidence that a refusal path did not leak a handle on the credential
    file.  A closed descriptor answers ``EBADF``; an open one answers.

    :param descriptor: The descriptor the stand-in issued.
    """
    with pytest.raises(OSError) as raised:
        os.fstat(descriptor)
    assert raised.value.errno == errno.EBADF


# --- The policy, as a pure function ---------------------------------------- #


def test_the_windows_policy_accepts_an_owner_only_object() -> None:
    """The positive control: owner-owned, and granted to nobody else.

    Asserted first for the reason the POSIX block's own positive control
    states - every refusal case below would also pass against a policy that
    refused everything. Three accepted shapes, because all three occur on a
    real machine: the owner alone, and the owner with each of the two
    principals that already administer every file on it.
    """
    assert _classify(_accepted_windows_facts()) is None
    assert (
        _classify(
            _accepted_windows_facts(
                allowed_trustees=(ACCOUNT_SID, LOCAL_SYSTEM_SID)
            )
        )
        is None
    )
    assert (
        _classify(
            _accepted_windows_facts(
                allowed_trustees=(
                    ADMINISTRATORS_SID,
                    ACCOUNT_SID,
                    LOCAL_SYSTEM_SID,
                )
            )
        )
        is None
    )


def test_the_windows_policy_refuses_a_foreign_owner() -> None:
    """``SEC2-F33``'s ownership half, on the platform that had no check at all.

    A file planted by another local account is refused however its DACL reads -
    the ownership test is applied before the DACL walk, so a hostile file that
    grants only its own owner is still refused.
    """
    assert (
        _classify(_accepted_windows_facts(owner=FOREIGN_ACCOUNT_SID))
        == properties._REASON_FOREIGN_OWNER
    )
    assert (
        _classify(
            properties._WindowsSecurityFacts(
                owner=FOREIGN_ACCOUNT_SID,
                account=ACCOUNT_SID,
                owner_equivalent=(LOCAL_SYSTEM_SID, ADMINISTRATORS_SID),
                allowed_trustees=(FOREIGN_ACCOUNT_SID,),
            )
        )
        == properties._REASON_FOREIGN_OWNER
    )


@pytest.mark.parametrize(
    ("trustee", "label"),
    [
        pytest.param(USERS_SID, "BUILTIN\\Users", id="users"),
        pytest.param(EVERYONE_SID, "Everyone", id="everyone"),
        pytest.param(FOREIGN_ACCOUNT_SID, "another account", id="another-account"),
    ],
)
def test_the_windows_policy_refuses_an_ace_for_any_other_trustee(
    trustee: bytes, label: str
) -> None:
    """``SEC2-F33``'s 0600-equivalent half, as Windows expresses it.

    An access-allowed ACE naming anyone outside ``{owner, LocalSystem,
    BUILTIN\\Administrators}`` makes the credential file readable beyond its
    owner, which is the same exposure as the 0644 file the finding reports -
    and ``Users`` is not a contrived case: it is what a checkout directory
    contributes by inheritance.

    The ACE is added *alongside* the owner's own, which is how a real DACL
    reads, so the policy has to reject on the extra entry rather than on the
    absence of the owner's.
    """
    facts = _accepted_windows_facts(allowed_trustees=(ACCOUNT_SID, trustee))

    assert _classify(facts) == properties._REASON_OPEN_PERMISSIONS, label


def test_the_windows_policy_refuses_an_absent_or_empty_dacl() -> None:
    """Neither a NULL DACL nor an empty one is evidence of protection.

    Two different facts with one verdict. A NULL DACL (``None``) grants every
    account full access, which is strictly worse than 0644. An empty DACL
    (``()``) grants nobody - and yet a readable handle was obtained, so the
    access came from somewhere the DACL does not describe, such as a backup
    privilege; the DACL then says nothing about who else could do the same.
    Accepting either would be accepting by omission.
    """
    assert (
        _classify(_accepted_windows_facts(allowed_trustees=None))
        == properties._REASON_OPEN_PERMISSIONS
    )
    assert (
        _classify(_accepted_windows_facts(allowed_trustees=()))
        == properties._REASON_OPEN_PERMISSIONS
    )


def test_the_windows_policy_refuses_facts_it_could_not_establish() -> None:
    """Fail closed: an unknown owner is not a matching owner.

    The reason phrase matters here and is asserted rather than assumed: "this
    could not be determined" is a different operational fact from "this was
    determined and it was wrong", and an operator reading one refusal record
    has to be able to tell which happened.

    The default-constructed facts are included deliberately - a partially built
    instance must be refused, so that a future extraction path that forgets a
    field cannot pass a check by leaving it unset.
    """
    unverifiable = properties._REASON_UNVERIFIABLE_OBJECT

    assert _classify(_accepted_windows_facts(owner=None)) == unverifiable
    assert _classify(_accepted_windows_facts(account=None)) == unverifiable
    assert _classify(properties._WindowsSecurityFacts()) == unverifiable


def test_the_windows_policy_is_stricter_without_the_well_known_sids() -> None:
    """An empty owner-equivalent set narrows the policy, never widens it.

    ``CreateWellKnownSid`` failing is a refusal in the production extraction,
    so this state should not arise - but the policy is a public-facing pure
    function within this module and its behaviour on it must be the safe one:
    with no owner-equivalent SIDs, only the owner's own ACE is accepted and a
    ``LocalSystem`` ACE is refused rather than waved through by a set that
    happens to be empty.
    """
    narrowed = _accepted_windows_facts(owner_equivalent=())

    assert _classify(narrowed) is None
    assert (
        _classify(
            narrowed._replace(allowed_trustees=(ACCOUNT_SID, LOCAL_SYSTEM_SID))
        )
        == properties._REASON_OPEN_PERMISSIONS
    )


def _classify(facts: properties._WindowsSecurityFacts) -> str | None:
    """Apply the production policy to ``facts``.

    A one-line alias, so that the assertions above read as the decision they
    are rather than as a long dotted call.

    :param facts: The fabricated facts.
    :returns: The refusal reason, or ``None``.
    """
    return properties._classify_windows_protection(facts)


# --- The constants every Win32 call passes --------------------------------- #


def test_the_windows_constants_match_their_documented_values() -> None:
    """Each Win32 constant, against its WinNT.h / winbase.h value.

    The only part of the Win32 layer that *can* be verified on a POSIX host,
    and it is worth verifying: a mask constant that silently became 0 - which
    is what the ``getattr(stat, ..., 0)`` form used elsewhere in the module
    would yield on a platform that stopped exporting these names, and why the
    Windows branch uses its own literals - would disable the check that masks
    with it while every other test still passed.

    The values are the documented ones:
    ``GENERIC_READ`` 0x80000000, ``FILE_SHARE_READ`` 1, ``OPEN_EXISTING`` 3,
    ``FILE_FLAG_OPEN_REPARSE_POINT`` 0x00200000,
    ``FILE_FLAG_BACKUP_SEMANTICS`` 0x02000000,
    ``FileAttributeTagInfo`` 9 with an 8-byte structure,
    ``FILE_ATTRIBUTE_REPARSE_POINT`` 0x400, ``FILE_ATTRIBUTE_DIRECTORY`` 0x10,
    ``SE_FILE_OBJECT`` 1, ``OWNER_SECURITY_INFORMATION`` 1,
    ``DACL_SECURITY_INFORMATION`` 4, ``TOKEN_QUERY`` 8, ``TokenUser`` 1,
    ``AclSizeInformation`` 2 with a 12-byte structure, a 4-byte ``ACE_HEADER``
    with the trustee SID 8 bytes in, ``ACCESS_ALLOWED_ACE_TYPE`` 0,
    ``SECURITY_MAX_SID_SIZE`` 68, and ``WinLocalSystemSid`` 22 /
    ``WinBuiltinAdministratorsSid`` 26 for the two well-known SIDs.
    """
    assert properties._WINDOWS_GENERIC_READ == 0x80000000
    assert properties._WINDOWS_FILE_SHARE_READ == 0x00000001
    assert properties._WINDOWS_OPEN_EXISTING == 3
    assert properties._WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT == 0x00200000
    assert properties._WINDOWS_FILE_FLAG_BACKUP_SEMANTICS == 0x02000000

    assert properties._WINDOWS_FILE_ATTRIBUTE_TAG_INFO == 9
    assert properties._WINDOWS_FILE_ATTRIBUTE_TAG_INFO_BYTES == 8
    assert properties._WINDOWS_ATTRIBUTE_REPARSE_POINT == 0x00000400
    assert properties._WINDOWS_ATTRIBUTE_DIRECTORY == 0x00000010

    assert properties._WINDOWS_SE_FILE_OBJECT == 1
    assert properties._WINDOWS_OWNER_SECURITY_INFORMATION == 0x00000001
    assert properties._WINDOWS_DACL_SECURITY_INFORMATION == 0x00000004
    assert properties._WINDOWS_TOKEN_QUERY == 0x0008
    assert properties._WINDOWS_TOKEN_USER == 1

    assert properties._WINDOWS_ACL_SIZE_INFORMATION == 2
    assert properties._WINDOWS_ACL_SIZE_INFORMATION_BYTES == 12
    assert properties._WINDOWS_ACE_HEADER_BYTES == 4
    assert properties._WINDOWS_SIMPLE_ACE_SID_OFFSET == 8
    assert properties._WINDOWS_ACCESS_ALLOWED_ACE_TYPE == 0
    assert properties._WINDOWS_SIMPLE_ACE_TYPES == frozenset({0, 1, 2, 3})

    assert properties._WINDOWS_SECURITY_MAX_SID_SIZE == 68
    assert properties._WINDOWS_OWNER_EQUIVALENT_SID_TYPES == (22, 26)

    # The two bounds this module imposes on values it reads back from Win32
    # structures rather than on Win32 itself. Both are fixed, like every cap in
    # the module: a limit a caller can raise is a limit an attacker-controlled
    # environment can raise. An ACL is at most 64 KiB and the smallest ACE is
    # 12 bytes, so the ACE bound cannot be reached by a valid DACL, and a
    # TOKEN_USER is a pointer, a DWORD and one SID, so 1 KiB is ample.
    assert properties._WINDOWS_MAX_TOKEN_USER_BYTES == 1024
    assert properties._WINDOWS_MAX_ACE_COUNT == 8192
    assert properties._WINDOWS_MAX_ACE_COUNT * 12 > 65536


def test_the_windows_hardening_published_no_new_name() -> None:
    """Every name this branch added is private, and ``__all__`` is unchanged.

    ``app/utils/__init__.py`` re-exports this module's ``__all__`` statically
    and :func:`test_public_surface_is_exactly_the_declared_all` asserts the
    tuple exactly, so a platform branch may not widen the surface.  Asserted
    here as well, over the names themselves, because that test would still pass
    if a public helper were added without being listed.
    """
    windows_names = [
        name
        for name in vars(properties)
        if "windows" in name.lower() or "WINDOWS" in name
    ]

    assert windows_names, "the Windows branch must contribute some name"
    for name in windows_names:
        assert name.startswith("_"), name
        assert name not in properties.__all__, name


def test_the_stand_in_offers_the_real_binding_surface() -> None:
    """The stand-in speaks the same four operations as the Win32 layer.

    What keeps every dispatch test below honest.  The real
    ``_WindowsObjectBinding`` cannot be instantiated here - ``ctypes.WinDLL``
    needs Windows - so the tests drive a stand-in instead; if the real class
    renamed or dropped one of the operations the reader calls, the stand-in
    would keep exercising a sequence Windows no longer performs and every one
    of those tests would keep passing.  Comparing the names is the cheap guard
    against that.
    """
    operations = (
        "open_handle",
        "file_attributes",
        "security_facts",
        "descriptor_from_handle",
        "close_handle",
    )

    for operation in operations:
        assert callable(
            getattr(properties._WindowsObjectBinding, operation)
        ), operation
        assert callable(
            getattr(_WindowsBindingStandIn, operation)
        ), operation


# --- The dispatch, the sequence and the handle lifecycle ------------------- #


def test_the_windows_branch_is_selected_by_the_platform_and_only_then(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The marker is ``os.name == "nt"``, and it is what dispatches.

    Two assertions in one place because they are one contract.  First, the
    marker is the platform and nothing else - on this host it is false, so
    production here takes the POSIX path and every test in the block above
    exercises real behaviour rather than a simulated platform.  Second, the
    dispatch honours it: with the marker true the Windows opener is called, and
    with it false ``os.open`` is, which is what makes driving the marker a
    legitimate way to reach the Windows code at all.
    """
    assert properties._WINDOWS_PLATFORM == (os.name == "nt")
    assert properties._WINDOWS_PLATFORM is False, "this host is not Windows"

    target = _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")
    calls: list[Path] = []

    def _windows_opener(path: Path) -> int:
        """Record the call and return a real descriptor."""
        calls.append(path)
        return os.open(path, os.O_RDONLY)

    monkeypatch.setattr(
        properties, "_open_windows_verified_descriptor", _windows_opener
    )

    descriptor = properties._open_object_bound_descriptor(target)
    os.close(descriptor)
    assert calls == []

    monkeypatch.setattr(properties, "_WINDOWS_PLATFORM", True)
    descriptor = properties._open_object_bound_descriptor(target)
    os.close(descriptor)
    assert calls == [target]


def test_the_windows_reader_reads_an_owner_only_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The positive control for the Windows branch: a clean file is read.

    AAP section 0.8 supports Windows, so the branch may not be fail-closed
    unconditionally: a pipeline that can never read any configuration is not a
    supported platform.  A file whose owner is the running account and whose
    DACL grants that account alone is read and parsed exactly as on POSIX.

    It also asserts the handle lifecycle across the ownership boundary: the
    descriptor the stand-in transferred is closed by the time the load returns,
    and ``close_handle`` was *not* called for it - on Windows that would be a
    double close of a handle the descriptor already owns.
    """
    monkeypatch.chdir(tmp_path)
    target = _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")
    binding = _WindowsBindingStandIn()
    _install_windows_binding(monkeypatch, binding)

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        assert load_properties() == {BROWSER_KEY: SYNTHETIC_VALUE}

    assert _refusal_records(caplog) == []
    assert _missing_file_records(caplog) == []
    assert binding.descriptors == binding.handles
    assert binding.closed_handles == []
    _assert_handle_closed(binding.descriptors[0])


def test_the_windows_reader_refuses_a_reparse_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """``SEC2-F10``'s non-POSIX half: a junction is refused, not followed.

    The replaced check was an ``lstat`` before the open, whose own docstring
    admitted the window: a junction created between that ``lstat`` and the
    ``os.open`` was followed, and the ``fstat`` that followed described the
    target.  The reparse point is now classified *through the handle*
    ``CreateFileW`` bound to the object with
    ``FILE_FLAG_OPEN_REPARSE_POINT``, so nothing swapped at the path afterwards
    can change what is read - which is the property this test cannot itself
    observe and the production docstring records.

    What it does observe is that the attribute bit refuses the file with the
    same reason phrase and the same empty mapping as the POSIX symlink case,
    and that the handle is closed rather than leaked.
    """
    monkeypatch.chdir(tmp_path)
    target = _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")
    binding = _WindowsBindingStandIn(
        attributes=properties._WINDOWS_ATTRIBUTE_REPARSE_POINT
    )
    _install_windows_binding(monkeypatch, binding)

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        assert load_properties() == {}

    _assert_refused(caplog, properties._REASON_LINK, target=target)
    assert binding.descriptors == []
    assert binding.closed_handles == binding.handles
    _assert_handle_closed(binding.handles[0])


def test_the_windows_reader_refuses_a_foreign_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """``SEC2-F33``'s ownership half, through the whole reader.

    The policy test above proves the decision; this proves it is *reached* on
    the Windows path and that its verdict becomes the ordinary refusal
    contract - one ``WARNING`` naming the reason and the filename only, an
    empty mapping, nothing raised, and no handle left open.
    """
    monkeypatch.chdir(tmp_path)
    target = _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")
    binding = _WindowsBindingStandIn(
        facts=_accepted_windows_facts(owner=FOREIGN_ACCOUNT_SID)
    )
    _install_windows_binding(monkeypatch, binding)

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        assert load_properties() == {}

    _assert_refused(caplog, properties._REASON_FOREIGN_OWNER, target=target)
    assert binding.descriptors == []
    assert binding.closed_handles == binding.handles


def test_the_windows_reader_refuses_a_file_a_group_can_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """``SEC2-F33`` itself, in its Windows form: the 0644 analogue is refused.

    A DACL carrying the inherited ``BUILTIN\\Users`` ACE a checkout directory
    contributes is the Windows shape of the "0644 credential file in a
    traversable checkout" the finding reports, and it is refused with the same
    ``accessible beyond its owner`` reason the POSIX mode check uses - one
    reason phrase for one exposure, whichever platform expresses it.

    The operator's remedy for this refusal is in
    ``_verify_windows_owner_and_dacl``'s docstring: ``icacls
    configuration.properties /inheritance:r /grant:r "%USERNAME%":R``.
    """
    monkeypatch.chdir(tmp_path)
    target = _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")
    binding = _WindowsBindingStandIn(
        facts=_accepted_windows_facts(
            allowed_trustees=(ACCOUNT_SID, USERS_SID)
        )
    )
    _install_windows_binding(monkeypatch, binding)

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        assert load_properties() == {}

    _assert_refused(caplog, properties._REASON_OPEN_PERMISSIONS, target=target)
    assert binding.descriptors == []
    assert binding.closed_handles == binding.handles


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(
            {"attribute_error": RuntimeError("query failed")}, id="attributes"
        ),
        pytest.param(
            {"security_error": RuntimeError("query failed")}, id="security"
        ),
        pytest.param(
            {"attribute_error": ValueError("a structure that does not parse")},
            id="unparsed-structure",
        ),
        pytest.param(
            {"security_error": OSError("a ctypes fault")}, id="ctypes-fault"
        ),
    ],
)
def test_the_windows_reader_fails_closed_when_a_query_fails(
    failure: dict[str, BaseException],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A query that cannot be completed refuses the file - never accepts, never
    raises.

    The third outcome a security check can have, and the one that decides
    whether the other two are worth anything.  An unexpected return code, a
    structure that does not parse or a ``ctypes`` fault must become a refusal:
    accepting would read a credential file whose protection was never
    established, and raising would take down a worker over a configuration file
    AAP section 0.1.1 says is optional.

    The ``OSError`` case is the pointed one: an ``OSError`` raised *inside* the
    verification must not be mistaken for the tolerated I/O faults of the open,
    because those mean "there is no readable file" while this means "the file
    is there and its protection is unknown".
    """
    monkeypatch.chdir(tmp_path)
    target = _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")
    binding = _WindowsBindingStandIn(**failure)
    _install_windows_binding(monkeypatch, binding)

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        assert load_properties() == {}

    _assert_refused(
        caplog, properties._REASON_UNVERIFIABLE_OBJECT, target=target
    )
    assert binding.descriptors == []
    assert binding.closed_handles == binding.handles


def test_the_windows_reader_fails_closed_on_facts_it_cannot_establish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A query that *succeeds* but answers nothing is also a refusal.

    The complement of the case above: the Win32 layer returned rather than
    raised, but with no owner SID.  The policy refuses it, so a future
    extraction path that returns partial facts cannot produce an acceptance.
    """
    monkeypatch.chdir(tmp_path)
    target = _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")
    binding = _WindowsBindingStandIn(
        facts=properties._WindowsSecurityFacts()
    )
    _install_windows_binding(monkeypatch, binding)

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        assert load_properties() == {}

    _assert_refused(
        caplog, properties._REASON_UNVERIFIABLE_OBJECT, target=target
    )
    assert binding.closed_handles == binding.handles


def test_a_broken_windows_binding_never_escapes_load_properties(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Building the Win32 layer can fail two ways, and neither raises out.

    ``ctypes.WinDLL`` failing to load ``kernel32`` or ``advapi32`` is an
    ``OSError`` and is treated as the I/O fault it is - the tolerated
    missing-file path, message and traceback unchanged.  Anything else - a
    ``ctypes`` fault, a missing export resolved at prototype-declaration time -
    is a refusal.  The distinction matters because the first means "this
    platform cannot be queried at all" and the second means "the query broke",
    and neither may reach the caller as an exception.
    """
    monkeypatch.chdir(tmp_path)
    target = _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")
    monkeypatch.setattr(properties, "_WINDOWS_PLATFORM", True)

    def _unloadable() -> object:
        """Fail as a missing system library does."""
        raise OSError("kernel32 is not loadable here")

    monkeypatch.setattr(properties, "_windows_object_binding", _unloadable)

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        assert load_properties() == {}

    records = _missing_file_records(caplog)
    assert len(records) == 1
    assert records[0].exc_info is not None
    assert _refusal_records(caplog) == []

    caplog.clear()

    def _broken() -> object:
        """Fail as a missing export does."""
        raise AttributeError("CreateFileW")

    monkeypatch.setattr(properties, "_windows_object_binding", _broken)

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        assert load_properties() == {}

    _assert_refused(
        caplog, properties._REASON_UNVERIFIABLE_OBJECT, target=target
    )


def test_a_failed_windows_open_takes_the_missing_file_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """``CreateFileW`` failing keeps ``ConfigurationReader``'s own message.

    The parity seam on the Windows path.  A failed open is exactly the
    ``catch (IOException)`` case - absent, unreadable, or locked by a writer -
    so it must log :data:`MISSING_FILE_MESSAGE` verbatim with its traceback and
    must not become a refusal, because a reader counting either record has to
    count the same events on both platforms.  The production code raises it
    through ``ctypes.WinError``, which produces exactly this ``OSError`` shape.
    """
    monkeypatch.chdir(tmp_path)
    _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")
    binding = _WindowsBindingStandIn(
        open_error=OSError(errno.ENOENT, "The system cannot find the file")
    )
    _install_windows_binding(monkeypatch, binding)

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        assert load_properties() == {}

    records = _missing_file_records(caplog)
    assert len(records) == 1
    assert records[0].getMessage() == MISSING_FILE_MESSAGE
    assert records[0].exc_info is not None
    assert _refusal_records(caplog) == []
    assert binding.handles == []


def test_a_ctypes_fault_in_the_windows_open_becomes_a_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A non-``OSError`` failure of the open is a refusal, not a crash.

    ``ctypes.ArgumentError`` is not an ``OSError``, so without the conversion
    it would propagate out of :func:`load_properties` past the narrowed
    ``except OSError`` and abort a worker over an optional file.  It is
    converted instead, and the reason says the protection could not be
    verified - which is true: no handle was ever obtained.
    """
    monkeypatch.chdir(tmp_path)
    target = _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")
    binding = _WindowsBindingStandIn(
        open_error=TypeError("a bad argument conversion")
    )
    _install_windows_binding(monkeypatch, binding)

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        assert load_properties() == {}

    _assert_refused(
        caplog, properties._REASON_UNVERIFIABLE_OBJECT, target=target
    )


def test_a_directory_on_windows_takes_the_missing_file_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A directory behaves identically on both platforms, by construction.

    ``FILE_FLAG_BACKUP_SEMANTICS`` is in the open flags precisely so that a
    directory in the file's place yields a handle that can be *classified* as
    one, rather than an ``ERROR_ACCESS_DENIED`` indistinguishable from a
    permission fault.  It is then converted to ``IsADirectoryError`` exactly as
    the POSIX branch converts its ``fstat`` result, which is what keeps
    :data:`MISSING_FILE_MESSAGE` and its traceback byte for byte what
    ``ConfigurationReader:21-24`` prints - the same assertion
    :func:`test_a_directory_takes_the_missing_file_path_and_not_a_refusal`
    makes for POSIX.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / PROPERTIES_FILENAME).mkdir()
    binding = _WindowsBindingStandIn(
        attributes=properties._WINDOWS_ATTRIBUTE_DIRECTORY
    )
    _install_windows_binding(monkeypatch, binding)

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        assert load_properties() == {}

    records = _missing_file_records(caplog)
    assert len(records) == 1
    assert records[0].getMessage() == MISSING_FILE_MESSAGE
    assert isinstance(records[0].exc_info[1], IsADirectoryError)
    assert _refusal_records(caplog) == []
    assert binding.closed_handles == binding.handles


def test_the_windows_read_is_bounded_by_the_same_cap_as_the_posix_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """One read path, so ``SEC2-F10``'s resource bound holds on both platforms.

    The verified handle is converted with ``open_osfhandle`` and handed to the
    *same* ``fstat`` checks and the *same* bounded read the POSIX branch uses,
    rather than to a second Windows-only reader that would have to re-implement
    the caps and could drift from them.  This is the evidence for that claim:
    with the Windows branch selected, a file one byte over
    ``_MAX_FILE_BYTES`` is refused with the shared ``larger than the permitted
    maximum`` reason, and a file exactly at the cap is still read.

    The padding is comment lines, so the only thing wrong with the oversized
    file is its total size - it would otherwise parse to its single entry
    rather than tripping an entry, key, value or line cap.
    """
    monkeypatch.chdir(tmp_path)
    entry = f"{BROWSER_KEY}={SYNTHETIC_VALUE}\n"
    padding = "#" + "w" * 98 + "\n"
    filler = padding * (properties._MAX_FILE_BYTES // 100 + 1)
    oversized = (entry + filler)[: properties._MAX_FILE_BYTES + 1]
    target = _write_properties(tmp_path, oversized)
    assert target.stat().st_size == properties._MAX_FILE_BYTES + 1
    _install_windows_binding(monkeypatch, _WindowsBindingStandIn())

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        assert load_properties() == {}

    _assert_refused(caplog, properties._REASON_TOO_LARGE, target=target)

    caplog.clear()
    _write_properties(tmp_path, oversized[: properties._MAX_FILE_BYTES])
    _install_windows_binding(monkeypatch, _WindowsBindingStandIn())

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        assert load_properties() == {BROWSER_KEY: SYNTHETIC_VALUE}

    assert _refusal_records(caplog) == []


def test_a_platform_with_neither_posix_identity_nor_win32_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The capability gate is an alternative, never an opt-out.

    This is the shape of ``SEC2-F33``'s non-POSIX branch as it was filed:
    ``_POSIX_IDENTITY_AVAILABLE`` gated both the ownership and the permission
    check, so a platform where it is false read the credential file with
    neither.  Windows is now covered by the Win32 branch, and a platform
    covered by *neither* mechanism is refused rather than read unchecked - so
    there is no longer any configuration of these flags under which an
    unverified credential file is accepted.

    The file is otherwise perfect, so the capability state is the only thing
    that can refuse it.
    """
    monkeypatch.chdir(tmp_path)
    target = _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")
    monkeypatch.setattr(properties, "_POSIX_IDENTITY_AVAILABLE", False)
    monkeypatch.setattr(properties, "_WINDOWS_PLATFORM", False)

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        assert load_properties() == {}

    _assert_refused(
        caplog, properties._REASON_UNVERIFIABLE_OBJECT, target=target
    )


def test_a_windows_refusal_is_one_warning_and_is_never_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    counted_load: _LoadCounter,
) -> None:
    """A Windows refusal is as tolerant and as one-shot as a POSIX one.

    The whole point of refusing rather than raising, asserted on the Windows
    path: every one of the six configured keys reads as ``None``, nothing is
    raised, the load happened once, and exactly one ``WARNING`` was emitted -
    so a Windows worker whose configuration is protected wrongly behaves
    exactly like one with no configuration at all, which is the tolerance AAP
    section 0.1.1 requires, and it says why once rather than per access.

    The key names are from the AAP's frozen six-key inventory; the file's
    contents are synthetic.
    """
    monkeypatch.chdir(tmp_path)
    configured_keys = (
        BROWSER_KEY,
        DOTTED_KEY,
        "url",
        "username",
        "password",
        EMPL_TITLE_KEY,
    )
    target = _write_properties(
        tmp_path, "".join(f"{key}={SYNTHETIC_VALUE}\n" for key in configured_keys)
    )
    _install_windows_binding(
        monkeypatch,
        _WindowsBindingStandIn(
            facts=_accepted_windows_facts(
                allowed_trustees=(ACCOUNT_SID, EVERYONE_SID)
            )
        ),
    )

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        for key in configured_keys:
            assert get_property(key) is None, key
        assert dict(get_properties()) == {}

    assert counted_load.calls == 1
    _assert_refused(caplog, properties._REASON_OPEN_PERMISSIONS, target=target)
    for record in caplog.records:
        assert SYNTHETIC_VALUE not in record.getMessage()


# --------------------------------------------------------------------------- #
# The Win32 layer itself - properties._WindowsObjectBinding, driven against a
# fabricated Win32 world.
#
# WHY THIS BLOCK EXISTS, AND WHAT THE BLOCK ABOVE LEAVES OUT. Every test above
# reaches the Windows branch through `properties._windows_object_binding` - the
# factory seam - and replaces it, so the sequencing, the refusal classes and the
# handle lifecycle are exercised while the Win32 layer that *performs* them is
# never entered at all. What that leaves unmeasured is the code that does the
# arithmetic: the bounded SID copies, the ACL walk, the TOKEN_USER pointer
# indirection, the little-endian field reads and the fourteen prototype
# declarations. That code would otherwise first execute on a Windows agent, and
# a wrong offset there fails closed on a credential file (or, worse, reads the
# wrong four bytes and passes), so this block enters the class on this host.
#
# WHAT IS REAL AND WHAT IS FAKE - the same division in every test below, and
# each test's docstring repeats the part of it that test depends on:
#
#   * REAL: the class under test, unmodified; the ctypes machinery it uses -
#     create_string_buffer, byref, string_at, cast, memmove, addressof; and
#     every structure it parses, built here as genuine little-endian byte
#     buffers in the layout WinNT.h documents - SID, ACL, ACE, TOKEN_USER,
#     FILE_ATTRIBUTE_TAG_INFO, ACL_SIZE_INFORMATION, and one allocation the
#     owner SID and the DACL both point *into*, as GetSecurityInfo's does. A
#     wrong offset, a wrong width or a wrong endianness fails a test here.
#
#   * FAKE: the fourteen kernel32/advapi32 entry points and msvcrt, which do
#     not exist on this host - `ctypes.WinDLL`, `ctypes.WinError` and
#     `ctypes.get_last_error` are absent and msvcrt has no module spec. They are
#     Python callables honouring the documented contracts: out-parameters
#     written through byref, BOOL returns, GetSecurityInfo's error code returned
#     directly rather than through the last-error value, GetTokenInformation's
#     two-call form, and a CreateFileW that hands back INVALID_HANDLE_VALUE with
#     a last-error code. So NO test below proves that a real CreateFileW,
#     GetSecurityInfo or open_osfhandle behaves as Microsoft documents, and none
#     proves anything about the operating system's enforcement of a DACL. What
#     they prove is what the reader computes from answers of that shape, and
#     that it releases every handle, token and allocation it obtains.
#
# HOW THE BRANCH IS REACHED. `_install_fake_win32_world` drives
# `properties._WINDOWS_PLATFORM` and installs the three absent ctypes
# attributes plus `sys.modules["msvcrt"]`, all through monkeypatch so the
# process is left exactly as it was. It deliberately does *not* replace
# `properties._windows_object_binding`: the real class is constructed by the
# real factory and its real methods run.
# --------------------------------------------------------------------------- #

#: Win32 error codes the fake world reports.  ``ERROR_FILE_NOT_FOUND`` is the
#: absent-file case CreateFileW gives, ``ERROR_ACCESS_DENIED`` the unreadable
#: one, and ``ERROR_INSUFFICIENT_BUFFER`` is what the first of
#: GetTokenInformation's two calls is *expected* to fail with.
ERROR_FILE_NOT_FOUND: Final[int] = 2
ERROR_ACCESS_DENIED: Final[int] = 5
ERROR_INSUFFICIENT_BUFFER: Final[int] = 122

#: How the fake ``ctypes.WinError`` maps a Win32 code to an ``errno``, which is
#: what the real one does through its own table.  Deliberately free of ``ELOOP``
#: and ``EMLINK``: those two are the errnos ``load_properties`` routes to the
#: *refusal* path, so a mapping that produced one would make a failed open look
#: like a link and the missing-file assertions would pass for the wrong reason.
WIN32_ERRNO: Final[dict[int, int]] = {
    ERROR_FILE_NOT_FOUND: errno.ENOENT,
    ERROR_ACCESS_DENIED: errno.EACCES,
    ERROR_INSUFFICIENT_BUFFER: errno.ENOMEM,
}

#: ``FILE_ATTRIBUTE_NORMAL``: what an ordinary configuration file reports, and
#: the positive control for the attribute classification - it is neither the
#: reparse-point bit nor the directory bit, so a classifier that masked with the
#: wrong constant would misread it.
FILE_ATTRIBUTE_NORMAL: Final[int] = 0x00000080

#: ``IO_REPARSE_TAG_SYMLINK``.  Packed into the second DWORD of every
#: ``FILE_ATTRIBUTE_TAG_INFO`` the fake world writes, precisely because the
#: production code must *not* read it: it is a distinctive non-zero value in the
#: four bytes immediately after the attributes, so a read of the wrong DWORD -
#: or a read of eight bytes where four were meant - is visible as a test
#: failure rather than as a plausible-looking attribute set.
SYMLINK_REPARSE_TAG: Final[int] = 0xA000000C

#: ``FILE_GENERIC_READ``, used as the ``ACCESS_MASK`` of every fabricated ACE.
#: The production code reads the ACE *type* and the trustee SID and never the
#: mask, so this value is realism rather than input - and an ACE whose mask were
#: read would have to be read from these four bytes, which sit between the
#: header and the SID the code does locate.
FILE_GENERIC_READ: Final[int] = 0x00120089

#: The handle values the fake world issues.  Distinct, non-zero, and distinct
#: from each other so that a record of "what was closed" is unambiguous about
#: *which* object was closed.
FAKE_FILE_HANDLE: Final[int] = 0x2A0
FAKE_TOKEN_HANDLE: Final[int] = 0x2A8

#: ``INVALID_HANDLE_VALUE`` and the ``GetCurrentProcess`` pseudo-handle, both
#: ``(HANDLE)-1``.  Computed the way the production module computes its own copy
#: - ``ctypes.c_void_p(-1).value`` - so the comparison being tested is the one
#: that runs on a 64-bit Windows agent rather than a 32-bit literal.
INVALID_HANDLE_VALUE: Final[int] = ctypes.c_void_p(-1).value
CURRENT_PROCESS_HANDLE: Final[int] = ctypes.c_void_p(-1).value

#: ``ACL_REVISION`` and the size of a self-relative ``SECURITY_DESCRIPTOR``
#: header - revision, Sbz1, control, and the four member offsets.  The fake
#: world reserves that header at the front of its one allocation and puts the
#: owner SID and the ACL after it, so both really do point into the single
#: block LocalFree is called on.
ACL_REVISION: Final[int] = 2
SECURITY_DESCRIPTOR_HEADER_BYTES: Final[int] = 20

#: The ACE types that are not ``ACCESS_ALLOWED_ACE_TYPE``.  The first three
#: share the simple layout and grant nothing, so the walk must read past them;
#: ``ACCESS_ALLOWED_CALLBACK_ACE_TYPE`` (9) has a *different* layout, so its
#: trustee cannot be located and the file must be refused rather than accepted
#: on an ACE nobody parsed.
ACCESS_DENIED_ACE_TYPE: Final[int] = 1
SYSTEM_AUDIT_ACE_TYPE: Final[int] = 2
SYSTEM_ALARM_ACE_TYPE: Final[int] = 3
ACCESS_ALLOWED_CALLBACK_ACE_TYPE: Final[int] = 9

#: The entry points ``_WindowsObjectBinding.__init__`` declares a prototype on,
#: by library.  Asserted as a whole by
#: :func:`test_the_win32_layer_declares_every_prototype_it_calls_through`, so an
#: entry point added to the production class without a prototype - the way a
#: 64-bit HANDLE gets silently truncated to a C ``int`` - fails that test.
KERNEL32_ENTRY_POINTS: Final[tuple[str, ...]] = (
    "CreateFileW",
    "GetFileInformationByHandleEx",
    "GetCurrentProcess",
    "CloseHandle",
    "LocalFree",
)
ADVAPI32_ENTRY_POINTS: Final[tuple[str, ...]] = (
    "GetSecurityInfo",
    "GetLengthSid",
    "IsValidSid",
    "EqualSid",
    "GetAclInformation",
    "GetAce",
    "OpenProcessToken",
    "GetTokenInformation",
    "CreateWellKnownSid",
)


class _TokenUser(ctypes.Structure):
    """``TOKEN_USER``: a ``SID_AND_ATTRIBUTES``, which is a ``PSID`` then a
    ``DWORD``.

    Declared as a real :class:`ctypes.Structure` rather than assembled by hand
    so that the pointer width and the member alignment are the platform's own.
    That is what makes the production line under test - ``ctypes.cast(buffer,
    ctypes.POINTER(ctypes.c_void_p))[0]``, which reads the ``Sid`` member out of
    the buffer ``GetTokenInformation`` filled - a real pointer read of a real
    structure rather than an agreement between two hand-written offsets.
    """

    _fields_ = (("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD))


def _file_attribute_tag_info(attributes: int, reparse_tag: int) -> bytes:
    """Build a ``FILE_ATTRIBUTE_TAG_INFO``: two little-endian ``DWORD``s.

    ``FileAttributes`` first, ``ReparseTag`` second - the layout
    ``GetFileInformationByHandleEx(FileAttributeTagInfo)`` fills and the one
    ``_WindowsObjectBinding.file_attributes`` parses by reading
    ``buffer.raw[:4]`` little-endian.

    :param attributes: The ``FileAttributes`` value.
    :param reparse_tag: The ``ReparseTag`` value, which the production code must
        not read.
    :returns: The eight bytes of the structure.
    """
    return attributes.to_bytes(4, "little") + reparse_tag.to_bytes(4, "little")


def _acl_size_information(ace_count: int, bytes_in_use: int) -> bytes:
    """Build an ``ACL_SIZE_INFORMATION``: three little-endian ``DWORD``s.

    ``AceCount`` first, then ``AclBytesInUse`` and ``AclBytesFree`` - the
    layout ``GetAclInformation(AclSizeInformation)`` fills.  Only the first is
    read by the production code, and the other two carry real values here so
    that reading the wrong one yields an obviously wrong count.

    :param ace_count: The number of ACEs in the ACL.
    :param bytes_in_use: The ACL's size in bytes.
    :returns: The twelve bytes of the structure.
    """
    return b"".join(
        (
            ace_count.to_bytes(4, "little"),
            bytes_in_use.to_bytes(4, "little"),
            (0).to_bytes(4, "little"),
        )
    )


def _simple_ace(
    ace_type: int, sid: bytes, *, mask: int = FILE_GENERIC_READ, flags: int = 0
) -> bytes:
    """Build one ACE with the simple ``{ACE_HEADER, ACCESS_MASK, SidStart}``
    layout.

    ``AceType``, ``AceFlags``, ``AceSize`` (little-endian ``WORD``), the access
    mask, then the trustee SID - so the SID really does begin
    ``properties._WINDOWS_SIMPLE_ACE_SID_OFFSET`` bytes in, which is the offset
    the production walk adds to the ACE address.  Every SID is a multiple of
    four bytes long, so an ACE built this way is ``DWORD``-aligned exactly as a
    real one is.

    :param ace_type: The ``AceType`` byte.
    :param sid: The trustee SID, in binary form.
    :param mask: The ``ACCESS_MASK``.  Never read by the code under test.
    :param flags: The ``AceFlags`` byte.  Never read by the code under test -
        the policy deliberately does not consult ACE flags.
    :returns: The ACE's bytes.
    """
    size = properties._WINDOWS_SIMPLE_ACE_SID_OFFSET + len(sid)
    return b"".join(
        (
            bytes((ace_type, flags)),
            size.to_bytes(2, "little"),
            mask.to_bytes(4, "little"),
            sid,
        )
    )


def _access_control_list(aces: tuple[bytes, ...]) -> bytes:
    """Build an ``ACL``: an eight-byte header, then the ACEs end to end.

    ``AclRevision``, ``Sbz1``, ``AclSize``, ``AceCount``, ``Sbz2``.  The fake
    ``GetAclInformation`` reads ``AceCount`` back out of these bytes and the
    fake ``GetAce`` walks the ACEs by their own ``AceSize`` fields, so an
    inconsistent ACL would be inconsistent to the code under test too.

    :param aces: The ACEs, in DACL order.
    :returns: The ACL's bytes.
    """
    body = b"".join(aces)
    size = 8 + len(body)
    return b"".join(
        (
            bytes((ACL_REVISION, 0)),
            size.to_bytes(2, "little"),
            len(aces).to_bytes(2, "little"),
            (0).to_bytes(2, "little"),
            body,
        )
    )


def _pointer_value(argument: Any) -> int | None:
    """Return the address an argument of a Win32 call refers to.

    The production code passes a pointer in each of the three forms ``ctypes``
    produces - a :class:`ctypes.c_void_p`, the result of ``ctypes.byref``, or a
    plain integer handle - and a fake entry point has to read all three to
    behave like the real one.  An argument that is not a pointer at all - the
    path string, a flag word - answers ``None``, so this is also usable as a
    filter over a whole recorded argument list.

    :param argument: The argument as the fake entry point received it.
    :returns: The address, or ``None`` for a NULL or non-pointer argument.
    """
    if argument is None:
        return None
    if isinstance(argument, ctypes.c_void_p):
        return argument.value
    if isinstance(argument, bool) or not isinstance(argument, int):
        # A byref() argument: ctypes hands the callee a CArgObject whose `_obj`
        # is the object being pointed at, which is how the fakes below write to
        # an out-parameter the way a real Win32 call writes through the pointer.
        # Anything else - a str, a bytes - refers to no address.
        referent = getattr(argument, "_obj", None)
        return None if referent is None else ctypes.addressof(referent)
    return argument


def _write_through(argument: Any, data: bytes) -> None:
    """Fill a ``byref``-passed buffer, as a real Win32 out-parameter does.

    :param argument: The ``byref`` argument the fake entry point received.
    :param data: The bytes to write into it.
    """
    ctypes.memmove(argument._obj, data, len(data))


def _sid_length_at(address: int | None) -> int:
    """Return ``GetLengthSid``'s answer for the SID at ``address``.

    Computed from the SID's own second byte - its subauthority count - exactly
    as the real call does: one revision byte, one count byte, a six-byte
    identifier authority, then four bytes per subauthority.  Reading it out of
    the fabricated bytes rather than returning a remembered length is what makes
    the bounded copy in ``_sid_token`` a real measurement of a real structure.

    :param address: The SID's address, or ``None``.
    :returns: The SID's length in bytes, or 0 for a NULL address.
    """
    if not address:
        return 0
    return 8 + 4 * ctypes.string_at(address, 2)[1]


class _RecordedWin32Call(NamedTuple):
    """One call a fake entry point received.

    :param entry: The entry point's name.
    :param arguments: The arguments, exactly as passed.
    :param argtypes: The ``argtypes`` in force *at the moment of the call*,
        which is how "the prototype was declared before anything was called" is
        asserted rather than assumed.
    :param restype: The ``restype`` in force at the moment of the call.
    """

    entry: str
    arguments: tuple[Any, ...]
    argtypes: Any
    restype: Any


class _FakeWin32Entry:
    """One fake exported function, with the attributes ``ctypes`` gives a real
    one.

    ``_WindowsObjectBinding.__init__`` assigns ``argtypes`` and ``restype`` on
    every entry point it uses, so a stand-in has to tolerate both assignments;
    recording what they were when the call arrived is what turns that tolerance
    into evidence.

    The implementation is replaceable after construction - that is how a test
    injects one failing Win32 call into an otherwise consistent world, which is
    the only way to reach a fail-closed branch without also breaking the calls
    around it.
    """

    def __init__(
        self,
        name: str,
        log: list[_RecordedWin32Call],
        implementation: Callable[..., Any],
    ) -> None:
        """Bind the entry point to the world's call log.

        :param name: The exported name.
        :param log: The world's shared, ordered call log.
        :param implementation: What the call does.
        """
        self.name = name
        self.implementation = implementation
        self.argtypes: Any = None
        self.restype: Any = None
        self._log = log

    def __call__(self, *arguments: Any) -> Any:
        """Record the call, with the prototype in force, then perform it."""
        self._log.append(
            _RecordedWin32Call(self.name, arguments, self.argtypes, self.restype)
        )
        return self.implementation(*arguments)


class _FakeWin32Library:
    """A fake ``ctypes.WinDLL`` result: a named bag of entry points.

    An attribute that is not an entry point raises :class:`AttributeError`,
    which is what a real ``WinDLL`` does for an export the library does not
    have - the failure the production factory converts into a refusal rather
    than letting it abort a worker.
    """

    def __init__(
        self, name: str, use_last_error: bool, entries: dict[str, _FakeWin32Entry]
    ) -> None:
        """Hold the library's name, its last-error mode and its exports.

        :param name: The library name that was requested.
        :param use_last_error: Whether ``use_last_error=True`` was passed, which
            is what makes ``ctypes.get_last_error`` meaningful for it.
        :param entries: The exports, by name.
        """
        self.name = name
        self.use_last_error = use_last_error
        self.entries = entries

    def __getattr__(self, name: str) -> _FakeWin32Entry:
        """Resolve an export, or raise ``AttributeError`` as ``WinDLL`` does."""
        try:
            return self.entries[name]
        except KeyError:
            raise AttributeError(name) from None


class _FakeWin32World:
    r"""A consistent Win32 world for one configuration read.

    Fabricates every structure the Win32 layer reads - as real little-endian
    byte buffers, at real addresses - and exposes the entry points that hand
    them out.  One instance describes one object: its attributes, its owner, its
    DACL, and the account the process runs as.  A test that wants a refusal
    changes exactly one of those, or injects one failing call with :meth:`fail`
    or :meth:`override`, so nothing else can be the reason.

    **The allocation is shared, as GetSecurityInfo's is.**  The owner SID and
    the ACL live inside one buffer behind a reserved ``SECURITY_DESCRIPTOR``
    header, and ``LocalFree`` is recorded against that buffer's address, so the
    production requirement - copy everything out *before* the ``finally`` frees
    it - is checkable by comparing call order.  The buffer itself outlives the
    free, because this is a fake: no test here proves anything about
    use-after-free, only about the order the calls were made in.

    Every handle issued, closed, transferred and freed is recorded, and any
    misuse the fakes can detect - a query on a handle that is not open, a second
    close of one already closed or transferred - is appended to
    :attr:`misuse` for :func:`_assert_win32_discipline` to assert away, rather
    than raised: an exception raised inside a fake would be swallowed by the
    production fail-closed conversion and the test would pass for the wrong
    reason.
    """

    def __init__(
        self,
        target: Path | None = None,
        *,
        attributes: int = FILE_ATTRIBUTE_NORMAL,
        reparse_tag: int = SYMLINK_REPARSE_TAG,
        owner: bytes = ACCOUNT_SID,
        account: bytes | None = ACCOUNT_SID,
        allowed: tuple[bytes, ...] = (ACCOUNT_SID,),
        extra_aces: tuple[bytes, ...] = (),
        null_dacl: bool = False,
        well_known: dict[int, bytes] | None = None,
        missing: tuple[str, ...] = (),
        library_error: BaseException | None = None,
        last_error: int = ERROR_FILE_NOT_FOUND,
        transfer_error: BaseException | None = None,
    ) -> None:
        """Fabricate the world.

        :param target: The file the object stands for.  ``open_osfhandle``
            returns a real descriptor on it, which is what lets the shared
            bounded read continue past the Win32 layer.  ``None`` for the tests
            that never convert a handle.
        :param attributes: The ``FileAttributes`` the object reports.
        :param reparse_tag: The ``ReparseTag`` it reports, which the production
            code must not read.
        :param owner: The object's owner SID.
        :param account: The SID of the account the process token names, or
            ``None`` to make ``TOKEN_USER.Sid`` NULL.
        :param allowed: One trustee SID per access-allowed ACE, in DACL order.
        :param extra_aces: Whole ACEs appended after the allowed ones, for the
            types the walk must read past or refuse.
        :param null_dacl: Whether the object has no DACL at all.
        :param well_known: The ``WELL_KNOWN_SID_TYPE`` to SID mapping
            ``CreateWellKnownSid`` answers from.  Defaults to the two the
            production module asks for.
        :param missing: Entry points to omit, as a library missing an export.
        :param library_error: Raised by the fake ``WinDLL`` instead of returning
            a library.
        :param last_error: What ``ctypes.get_last_error`` reports.
        :param transfer_error: Raised by ``open_osfhandle`` instead of
            converting the handle.
        """
        self.target = target
        self.attributes = attributes
        self.reparse_tag = reparse_tag
        self.account = account
        self.last_error = last_error
        self.library_error = library_error
        self.transfer_error = transfer_error
        self.well_known = (
            {22: LOCAL_SYSTEM_SID, 26: ADMINISTRATORS_SID}
            if well_known is None
            else well_known
        )

        self.calls: list[_RecordedWin32Call] = []
        self.libraries: list[tuple[str, bool]] = []
        self.opened_handles: list[int] = []
        self.closed_handles: list[int | None] = []
        self.token_handles: list[int] = []
        self.transferred_handles: list[int] = []
        self.descriptors: list[int] = []
        self.freed: list[int | None] = []
        self.transfer_flags: list[int] = []
        self.win_errors: list[int] = []
        self.last_error_reads = 0
        self.misuse: list[str] = []
        self._live_handles: set[int] = set()

        # One allocation, a reserved SECURITY_DESCRIPTOR header, then the owner
        # SID and the ACL - so both pointers handed out really do point into the
        # block LocalFree is called on, which is the production module's stated
        # reason for copying before the free.
        acl = _access_control_list(
            tuple(
                _simple_ace(properties._WINDOWS_ACCESS_ALLOWED_ACE_TYPE, sid)
                for sid in allowed
            )
            + extra_aces
        )
        block = bytearray(b"\x00" * SECURITY_DESCRIPTOR_HEADER_BYTES)
        owner_offset = len(block)
        block += owner
        dacl_offset = len(block)
        block += acl
        self._descriptor = ctypes.create_string_buffer(bytes(block), len(block))
        self.descriptor_address = ctypes.addressof(self._descriptor)
        self.descriptor_bytes = len(block)
        self.owner_address = self.descriptor_address + owner_offset
        self.dacl_address = (
            None if null_dacl else self.descriptor_address + dacl_offset
        )
        self.ace_count = len(allowed) + len(extra_aces)

        # TOKEN_USER, and the SID it points at, both ctypes-owned: the
        # production code reads the pointer out of the buffer the fake
        # GetTokenInformation fills and must not free either.
        self._account_sid = ctypes.create_string_buffer(
            account or b"", max(len(account or b""), 1)
        )
        self._token_user = _TokenUser(
            ctypes.addressof(self._account_sid) if account else None, 0
        )
        self.token_user_bytes = ctypes.string_at(
            ctypes.addressof(self._token_user), ctypes.sizeof(self._token_user)
        )

        # The two libraries' exports, as {name: implementation}, wrapped into
        # recording entry points below.  Locals rather than attributes: the
        # entry points themselves are reached through ``entry()``, and a second
        # way to reach them would be a second thing to keep consistent.
        kernel32 = {
            "CreateFileW": self._create_file_w,
            "GetFileInformationByHandleEx": self._get_file_information_by_handle_ex,
            "GetCurrentProcess": self._get_current_process,
            "CloseHandle": self._close_handle,
            "LocalFree": self._local_free,
        }
        advapi32 = {
            "GetSecurityInfo": self._get_security_info,
            "GetLengthSid": self._get_length_sid,
            "IsValidSid": self._is_valid_sid,
            "EqualSid": self._equal_sid,
            "GetAclInformation": self._get_acl_information,
            "GetAce": self._get_ace,
            "OpenProcessToken": self._open_process_token,
            "GetTokenInformation": self._get_token_information,
            "CreateWellKnownSid": self._create_well_known_sid,
        }
        self._entries = {
            name: {
                entry: _FakeWin32Entry(entry, self.calls, implementation)
                for entry, implementation in table.items()
                if entry not in missing
            }
            for name, table in (("kernel32", kernel32), ("advapi32", advapi32))
        }

        self.msvcrt = types.ModuleType("msvcrt")
        self.msvcrt.open_osfhandle = self._open_osfhandle

    # --- The three ctypes attributes this host does not have --------------- #

    def win_dll(self, name: str, use_last_error: bool = False) -> _FakeWin32Library:
        """Stand in for ``ctypes.WinDLL``.

        :param name: The library requested - only the two the production module
            loads are known here, and anything else is a test defect rather than
            a production one.
        :param use_last_error: Recorded, because ``ctypes.get_last_error`` means
            nothing unless the library was loaded with it.
        :returns: The fake library.
        :raises BaseException: ``library_error``, when one was configured.
        """
        self.libraries.append((name, use_last_error))
        if self.library_error is not None:
            raise self.library_error
        assert name in self._entries, name
        return _FakeWin32Library(name, use_last_error, self._entries[name])

    def win_error(self, code: int | None = None) -> OSError:
        """Stand in for ``ctypes.WinError``.

        Returns - never raises - an ``OSError`` carrying the Win32 code in
        ``winerror`` and the mapped value in ``errno``, which is the shape the
        real function produces and the shape ``load_properties``' ``except
        OSError`` is written against.

        :param code: The Win32 error code.
        :returns: The exception for the caller to raise.
        """
        resolved = self.last_error if code is None else code
        self.win_errors.append(resolved)
        error = OSError(
            WIN32_ERRNO.get(resolved, errno.EIO), f"Win32 error {resolved}"
        )
        error.winerror = resolved
        return error

    def get_last_error(self) -> int:
        """Stand in for ``ctypes.get_last_error``."""
        self.last_error_reads += 1
        return self.last_error

    # --- kernel32 ---------------------------------------------------------- #

    def _create_file_w(self, *arguments: Any) -> int:
        """Open the object and return a handle, as ``CreateFileW`` does."""
        self._live_handles.add(FAKE_FILE_HANDLE)
        self.opened_handles.append(FAKE_FILE_HANDLE)
        return FAKE_FILE_HANDLE

    def _get_file_information_by_handle_ex(
        self, handle: Any, information_class: int, buffer: Any, size: int
    ) -> int:
        """Fill a ``FILE_ATTRIBUTE_TAG_INFO`` for a live handle."""
        self._require_live(handle, "GetFileInformationByHandleEx")
        data = _file_attribute_tag_info(self.attributes, self.reparse_tag)
        _write_through(buffer, data[:size])
        return 1

    def _get_current_process(self) -> int:
        """Return the ``(HANDLE)-1`` pseudo-handle ``GetCurrentProcess`` does."""
        return CURRENT_PROCESS_HANDLE

    def _close_handle(self, handle: Any) -> int:
        """Close a live handle, and record a close of anything else."""
        value = _pointer_value(handle)
        self.closed_handles.append(value)
        if value in self._live_handles:
            self._live_handles.discard(value)
        else:
            self.misuse.append(f"closed a handle that was not live: {value!r}")
        return 1

    def _local_free(self, pointer: Any) -> None:
        """Record the freed allocation, as ``LocalFree`` releases it."""
        self.freed.append(_pointer_value(pointer))
        return None

    # --- advapi32 ---------------------------------------------------------- #

    def _get_security_info(
        self,
        handle: Any,
        object_type: int,
        information: int,
        owner: Any,
        group: Any,
        dacl: Any,
        sacl: Any,
        descriptor: Any,
    ) -> int:
        """Hand out the owner and DACL pointers into the one allocation."""
        self._require_live(handle, "GetSecurityInfo")
        owner._obj.value = self.owner_address
        dacl._obj.value = self.dacl_address
        descriptor._obj.value = self.descriptor_address
        return properties._WINDOWS_ERROR_SUCCESS

    def _get_length_sid(self, pointer: Any) -> int:
        """Measure the SID at ``pointer`` from its own subauthority count."""
        return _sid_length_at(_pointer_value(pointer))

    def _is_valid_sid(self, pointer: Any) -> int:
        """Apply ``IsValidSid``'s rule: revision 1, at most 15 subauthorities."""
        address = _pointer_value(pointer)
        if not address:
            return 0
        revision, subauthorities = ctypes.string_at(address, 2)
        return 1 if revision == 1 and subauthorities <= 15 else 0

    def _equal_sid(self, first: Any, second: Any) -> int:
        """Compare two SIDs byte for byte, which is what ``EqualSid`` does."""
        left = _pointer_value(first)
        right = _pointer_value(second)
        if not left or not right:
            return 0
        return (
            1
            if ctypes.string_at(left, _sid_length_at(left))
            == ctypes.string_at(right, _sid_length_at(right))
            else 0
        )

    def _get_acl_information(
        self, acl: Any, buffer: Any, size: int, information_class: int
    ) -> int:
        """Report the ACE count read back out of the fabricated ACL header."""
        address = _pointer_value(acl)
        if not address:
            return 0
        header = ctypes.string_at(address, 8)
        _write_through(
            buffer,
            _acl_size_information(
                int.from_bytes(header[4:6], "little"),
                int.from_bytes(header[2:4], "little"),
            )[:size],
        )
        return 1

    def _get_ace(self, acl: Any, index: int, ace: Any) -> int:
        """Walk the real ACL by its ``AceSize`` fields and hand out an address."""
        address = _pointer_value(acl)
        if not address:
            return 0
        offset = 8
        for _ in range(index):
            header = ctypes.string_at(address + offset, 4)
            offset += int.from_bytes(header[2:4], "little")
        ace._obj.value = address + offset
        return 1

    def _open_process_token(self, process: Any, access: int, token: Any) -> int:
        """Issue a token handle for the current-process pseudo-handle."""
        if _pointer_value(process) != CURRENT_PROCESS_HANDLE:
            self.misuse.append("opened a token on something other than this process")
        token._obj.value = FAKE_TOKEN_HANDLE
        self._live_handles.add(FAKE_TOKEN_HANDLE)
        self.token_handles.append(FAKE_TOKEN_HANDLE)
        return 1

    def _get_token_information(
        self,
        token: Any,
        information_class: int,
        buffer: Any,
        size: int,
        needed: Any,
    ) -> int:
        """Answer both halves of ``GetTokenInformation``'s two-call form."""
        self._require_live(token, "GetTokenInformation")
        if buffer is None:
            # The sizing call: it is documented to FAIL with
            # ERROR_INSUFFICIENT_BUFFER while reporting the size it wants.
            needed._obj.value = len(self.token_user_bytes)
            self.last_error = ERROR_INSUFFICIENT_BUFFER
            return 0
        _write_through(buffer, self.token_user_bytes[:size])
        needed._obj.value = len(self.token_user_bytes)
        return 1

    def _create_well_known_sid(
        self, kind: int, domain: Any, buffer: Any, size: Any
    ) -> int:
        """Build one well-known SID into the caller's bounded buffer."""
        sid = self.well_known.get(kind)
        if sid is None:
            return 0
        _write_through(buffer, sid)
        size._obj.value = len(sid)
        return 1

    # --- msvcrt ------------------------------------------------------------ #

    def _open_osfhandle(self, handle: int, flags: int) -> int:
        """Stand in for ``msvcrt.open_osfhandle``: transfer, or fail.

        A real descriptor on the real fixture file, because everything after
        this point - the ``fstat`` checks and the bounded read - is the shared
        code path and has to run for real.  The handle is dropped from the live
        set at the same moment: from here on the descriptor owns it, so a
        ``CloseHandle`` on it afterwards is the double close
        :func:`_assert_win32_discipline` reports.
        """
        self.transfer_flags.append(flags)
        if self.transfer_error is not None:
            raise self.transfer_error
        if handle not in self._live_handles:
            self.misuse.append("converted a handle that was not live")
        assert self.target is not None, "this world has no file to convert to"
        descriptor = os.open(self.target, os.O_RDONLY)
        self.transferred_handles.append(handle)
        self._live_handles.discard(handle)
        self.descriptors.append(descriptor)
        return descriptor

    # --- Failure injection, and inspection --------------------------------- #

    def entry(self, name: str) -> _FakeWin32Entry:
        """Return one entry point, from whichever library exports it.

        :param name: The exported name.
        :returns: The fake entry point.
        """
        for table in self._entries.values():
            if name in table:
                return table[name]
        raise AssertionError(f"no such entry point: {name}")

    def override(self, name: str, implementation: Callable[..., Any]) -> None:
        """Replace one entry point's behaviour, leaving the world consistent.

        :param name: The exported name.
        :param implementation: What the call should do instead.
        """
        self.entry(name).implementation = implementation

    def fail(self, name: str, result: int = 0) -> None:
        """Make one entry point report failure without doing anything.

        ``0`` is ``FALSE`` for every ``BOOL``-returning call here; the two
        callers that read a different kind of answer - ``GetSecurityInfo``,
        which returns a Win32 error code directly, and ``GetLengthSid`` - pass
        the value they need.

        :param name: The exported name.
        :param result: The value the call should return.
        """
        self.override(name, lambda *arguments: result)

    def calls_to(self, name: str) -> list[_RecordedWin32Call]:
        """Return the calls made to one entry point, in order.

        :param name: The exported name.
        :returns: The recorded calls.
        """
        return [call for call in self.calls if call.entry == name]

    def call_order(self) -> list[str]:
        """Return the names of every call made, in order."""
        return [call.entry for call in self.calls]

    def _require_live(self, handle: Any, operation: str) -> None:
        """Record a query made against a handle that is not open."""
        if _pointer_value(handle) not in self._live_handles:
            self.misuse.append(f"{operation} on a handle that was not live")


def _install_fake_win32_world(
    monkeypatch: pytest.MonkeyPatch, world: _FakeWin32World
) -> None:
    """Select the Windows branch and put ``world`` behind ``ctypes``.

    Four installations, all reversed by ``monkeypatch`` at teardown whether the
    test passes or fails: the platform marker the dispatch reads, and the three
    names this host does not have - ``ctypes.WinDLL``, ``ctypes.WinError`` and
    ``ctypes.get_last_error`` - plus a stub ``msvcrt`` in ``sys.modules``, which
    has no module spec here at all.

    ``properties._windows_object_binding`` is deliberately left alone: the point
    of this block is that the real :class:`~app.utils.properties._WindowsObjectBinding`
    is constructed and its own code runs.

    :param monkeypatch: pytest's patcher.
    :param world: The fabricated Win32 world.
    """
    monkeypatch.setattr(properties, "_WINDOWS_PLATFORM", True)
    monkeypatch.setattr(ctypes, "WinDLL", world.win_dll, raising=False)
    monkeypatch.setattr(ctypes, "WinError", world.win_error, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", world.get_last_error, raising=False)
    monkeypatch.setitem(sys.modules, "msvcrt", world.msvcrt)


def _win32_binding(
    monkeypatch: pytest.MonkeyPatch, world: _FakeWin32World
) -> properties._WindowsObjectBinding:
    """Install ``world`` and build the real Win32 layer over it.

    :param monkeypatch: pytest's patcher.
    :param world: The fabricated Win32 world.
    :returns: A real ``_WindowsObjectBinding``, bound to the fake libraries.
    """
    _install_fake_win32_world(monkeypatch, world)
    return properties._WindowsObjectBinding()


def _assert_win32_discipline(world: _FakeWin32World) -> None:
    """Assert every Win32 object the read obtained was released exactly once.

    A handle leak here is a locked credential file and a token leak is a leaked
    reference to the process token, so this is asserted on every path - the
    success path and each refusal path - rather than only where a leak seemed
    likely:

    * every handle ``CreateFileW`` issued was either closed or transferred to a
      descriptor, and never both - the double close the production ownership
      comment is about;
    * every token handle was closed;
    * every security descriptor handed out was ``LocalFree``-d exactly once;
    * and no fake was called on a handle that was not live at the time.

    :param world: The world the read ran against.
    """
    assert world.misuse == [], world.misuse

    accounted = set(world.closed_handles) | set(world.transferred_handles)
    for handle in world.opened_handles:
        assert handle in accounted, (
            f"handle {handle!r} was neither closed nor transferred"
        )
    for handle in world.transferred_handles:
        assert handle not in world.closed_handles, "a transferred handle was closed too"
    for handle in world.token_handles:
        assert handle in world.closed_handles, "a token handle was left open"

    assert len(world.closed_handles) == len(set(world.closed_handles)), (
        world.closed_handles
    )
    assert len(world.freed) == len(set(world.freed)), world.freed


# --- Construction: the libraries and the fourteen prototypes --------------- #


def test_the_win32_layer_declares_every_prototype_it_calls_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every entry point is prototyped before it is called, and correctly.

    The Win32 calls here are fakes, so this proves nothing about ``kernel32``
    or ``advapi32`` themselves.  What it proves is the thing that decides
    whether the real calls can work at all: ``ctypes`` marshals an
    un-prototyped argument as a C ``int``, so a ``HANDLE`` - 64 bits on every
    Windows agent this port supports - would be silently truncated and the call
    would fail or, worse, act on a different object.  Asserted three ways over
    one complete read: both libraries were loaded with ``use_last_error=True``
    (without which ``ctypes.get_last_error`` reports nothing), every entry point
    the class uses carries both ``argtypes`` and ``restype``, and not one call
    was made before its own prototype was in force.

    The declared types are then checked against the Win32 signatures for the
    arguments where the width or the indirection is what matters - the handles,
    the ``PSID``s, the out-pointers and ``LocalFree``'s ``HLOCAL`` - rather than
    for every argument, because that is where a truncation would occur.
    """
    monkeypatch.chdir(tmp_path)
    target = _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")
    world = _FakeWin32World(target)
    _install_fake_win32_world(monkeypatch, world)

    assert load_properties() == {BROWSER_KEY: SYNTHETIC_VALUE}

    assert world.libraries == [("kernel32", True), ("advapi32", True)]
    assert set(world.call_order()) == set(
        KERNEL32_ENTRY_POINTS + ADVAPI32_ENTRY_POINTS
    ), "one clean read must exercise every entry point the class declares"

    for call in world.calls:
        assert call.argtypes is not None, f"{call.entry} was called un-prototyped"
        assert call.restype is not None, f"{call.entry} was called un-prototyped"

    pointer_to_void_pointer = ctypes.POINTER(ctypes.c_void_p)

    assert world.entry("CreateFileW").argtypes == (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    assert world.entry("CreateFileW").restype is wintypes.HANDLE
    assert world.entry("GetFileInformationByHandleEx").argtypes == (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    )
    assert world.entry("GetFileInformationByHandleEx").restype is wintypes.BOOL
    assert world.entry("GetCurrentProcess").argtypes == ()
    assert world.entry("GetCurrentProcess").restype is wintypes.HANDLE
    assert world.entry("CloseHandle").argtypes == (wintypes.HANDLE,)
    assert world.entry("CloseHandle").restype is wintypes.BOOL
    assert world.entry("LocalFree").argtypes == (wintypes.HLOCAL,)
    assert world.entry("LocalFree").restype is wintypes.HLOCAL

    assert world.entry("GetSecurityInfo").argtypes == (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.DWORD,
        pointer_to_void_pointer,
        pointer_to_void_pointer,
        pointer_to_void_pointer,
        pointer_to_void_pointer,
        pointer_to_void_pointer,
    )
    assert world.entry("GetSecurityInfo").restype is wintypes.DWORD
    assert world.entry("GetLengthSid").argtypes == (ctypes.c_void_p,)
    assert world.entry("IsValidSid").argtypes == (ctypes.c_void_p,)
    assert world.entry("EqualSid").argtypes == (ctypes.c_void_p, ctypes.c_void_p)
    assert world.entry("GetAclInformation").argtypes == (
        ctypes.c_void_p,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.c_int,
    )
    assert world.entry("GetAce").argtypes == (
        ctypes.c_void_p,
        wintypes.DWORD,
        pointer_to_void_pointer,
    )
    assert world.entry("OpenProcessToken").argtypes == (
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    )
    assert world.entry("GetTokenInformation").argtypes == (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    )
    assert world.entry("CreateWellKnownSid").argtypes == (
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.DWORD),
    )
    for name in ("GetFileInformationByHandleEx", "IsValidSid", "EqualSid"):
        assert world.entry(name).restype is wintypes.BOOL, name

    assert target.exists()


def test_the_win32_layer_computes_invalid_handle_value_for_this_word_size(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """``INVALID_HANDLE_VALUE`` is computed, not written as a literal.

    The fake libraries make the construction possible on this host; the value
    itself is real arithmetic.  ``CreateFileW``'s failure return is
    ``(HANDLE)-1``, which through a ``c_void_p`` ``restype`` arrives as the
    *unsigned* pointer-sized value - ``0xFFFFFFFF`` on a 32-bit agent and
    ``0xFFFFFFFFFFFFFFFF`` on a 64-bit one.  A 32-bit literal would therefore
    never compare equal on a 64-bit agent and every failed open would be read as
    a successful one, which is why the production code derives it.
    """
    binding = _win32_binding(monkeypatch, _FakeWin32World())

    assert binding._invalid_handle == ctypes.c_void_p(-1).value
    assert binding._invalid_handle == 2 ** (8 * ctypes.sizeof(ctypes.c_void_p)) - 1


def test_a_win32_library_that_cannot_be_loaded_reaches_the_missing_file_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """``WinDLL`` raising ``OSError`` is an I/O fault, through the real factory.

    The stand-in version of this case above patches the factory; this one lets
    the real factory run and fails the real ``ctypes.WinDLL`` call, which is the
    only place the ``OSError`` can come from on a Windows agent.  It must take
    the tolerant missing-file path - message verbatim, traceback attached, empty
    mapping - because a platform whose security libraries cannot be loaded
    cannot be queried at all, and the file reads as absent rather than as
    refused.

    The library load is a fake; that a real ``kernel32`` would be loadable on
    Windows is not something this host can establish.
    """
    monkeypatch.chdir(tmp_path)
    _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")
    world = _FakeWin32World(
        library_error=OSError(errno.ENOENT, "kernel32 could not be loaded")
    )
    _install_fake_win32_world(monkeypatch, world)

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        assert load_properties() == {}

    records = _missing_file_records(caplog)
    assert len(records) == 1
    assert records[0].getMessage() == MISSING_FILE_MESSAGE
    assert records[0].exc_info is not None
    assert _refusal_records(caplog) == []
    assert world.libraries == [("kernel32", True)]
    assert world.opened_handles == []


def test_a_missing_win32_export_is_a_refusal_rather_than_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """An export the library does not have is refused, not raised.

    The prototype declarations in ``__init__`` resolve every export eagerly, so
    a library without one - an older Windows, or a redirected DLL - raises
    ``AttributeError`` there rather than at the call site.  That is not an
    ``OSError``, so without the production conversion it would propagate past
    ``load_properties``' narrowed ``except OSError`` and abort a worker over an
    optional file.  It becomes a refusal whose reason says the protection could
    not be verified, which is true: no handle was ever opened.

    The missing export is simulated by omitting it from the fake library, so
    this says nothing about which exports a real ``advapi32`` has.
    """
    monkeypatch.chdir(tmp_path)
    target = _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")
    world = _FakeWin32World(target, missing=("GetSecurityInfo",))
    _install_fake_win32_world(monkeypatch, world)

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        assert load_properties() == {}

    _assert_refused(caplog, properties._REASON_UNVERIFIABLE_OBJECT, target=target)
    assert world.opened_handles == []


# --- open_handle ----------------------------------------------------------- #


def test_open_handle_passes_exactly_the_documented_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one call both findings rest on, argument by argument.

    ``CreateFileW`` itself is a fake, so this does not prove that Windows
    honours these flags - it proves that the flags the production code passes
    are the documented ones, which is the part a POSIX host can settle and the
    part a typo would silently change:

    * ``GENERIC_READ`` alone, which already implies the ``READ_CONTROL`` the
      security query needs, so no extra access is requested;
    * ``FILE_SHARE_READ`` alone, so a concurrent writer fails the open rather
      than being read mid-write;
    * ``OPEN_EXISTING``, so the call never creates the credential file;
    * ``FILE_FLAG_OPEN_REPARSE_POINT``, which is the whole of the race-free
      anti-link defence - ``SEC2-F10``'s non-POSIX half - and
      ``FILE_FLAG_BACKUP_SEMANTICS``, which is what lets a directory in the
      file's place be classified instead of failing indistinguishably from a
      permission fault;
    * a NULL ``lpSecurityAttributes`` and a NULL ``hTemplateFile``, neither of
      which has any meaning for an existing file opened read-only;
    * and the path as a string, because ``LPCWSTR`` marshalling takes ``str``
      and not :class:`~pathlib.Path`.
    """
    target = _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")
    world = _FakeWin32World(target)
    binding = _win32_binding(monkeypatch, world)

    handle = binding.open_handle(target)

    assert handle == FAKE_FILE_HANDLE
    (call,) = world.calls_to("CreateFileW")
    assert call.arguments == (
        str(target),
        properties._WINDOWS_GENERIC_READ,
        properties._WINDOWS_FILE_SHARE_READ,
        None,
        properties._WINDOWS_OPEN_EXISTING,
        properties._WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT
        | properties._WINDOWS_FILE_FLAG_BACKUP_SEMANTICS,
        None,
    )
    assert isinstance(call.arguments[0], str)

    binding.close_handle(handle)
    assert world.closed_handles == [FAKE_FILE_HANDLE]
    _assert_win32_discipline(world)


@pytest.mark.parametrize(
    ("returned", "code", "expected_errno"),
    [
        pytest.param(
            INVALID_HANDLE_VALUE, ERROR_FILE_NOT_FOUND, errno.ENOENT, id="invalid"
        ),
        pytest.param(
            INVALID_HANDLE_VALUE, ERROR_ACCESS_DENIED, errno.EACCES, id="denied"
        ),
        pytest.param(0, ERROR_ACCESS_DENIED, errno.EACCES, id="null"),
    ],
)
def test_open_handle_turns_a_failed_open_into_a_win32_oserror(
    returned: int,
    code: int,
    expected_errno: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed ``CreateFileW`` becomes the ``OSError`` the tolerance needs.

    ``CreateFileW`` and ``ctypes.WinError`` are both fakes here - the real
    ``WinError`` maps a Win32 code to an ``errno`` from a table this host does
    not have - so what is established is the *conversion*: both documented
    failure returns are recognised, the last-error value is read and carried,
    and the result is an ``OSError`` rather than a silently-accepted handle.
    That conversion is what routes an absent or unreadable configuration to
    ``ConfigurationReader``'s tolerated missing-file path instead of to a
    refusal record, and a zero return has to be recognised as well as
    ``INVALID_HANDLE_VALUE`` because a ``c_void_p`` ``restype`` yields ``None``
    - not ``-1`` - for a NULL return, which ``ctypes`` then presents as
    falsy.
    """
    target = _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")
    world = _FakeWin32World(target, last_error=code)
    binding = _win32_binding(monkeypatch, world)
    world.fail("CreateFileW", result=returned)

    with pytest.raises(OSError) as raised:
        binding.open_handle(target)

    assert raised.value.errno == expected_errno
    assert raised.value.winerror == code
    assert world.win_errors == [code]
    assert world.last_error_reads == 1
    assert world.opened_handles == []
    _assert_win32_discipline(world)


# --- file_attributes ------------------------------------------------------- #


@pytest.mark.parametrize(
    ("attributes", "is_reparse_point", "is_directory"),
    [
        pytest.param(FILE_ATTRIBUTE_NORMAL, False, False, id="ordinary-file"),
        pytest.param(
            properties._WINDOWS_ATTRIBUTE_REPARSE_POINT, True, False, id="reparse-point"
        ),
        pytest.param(
            properties._WINDOWS_ATTRIBUTE_DIRECTORY, False, True, id="directory"
        ),
        pytest.param(
            properties._WINDOWS_ATTRIBUTE_REPARSE_POINT
            | properties._WINDOWS_ATTRIBUTE_DIRECTORY,
            True,
            True,
            id="directory-junction",
        ),
        pytest.param(
            FILE_ATTRIBUTE_NORMAL | 0x00000001 | 0x00000002,
            False,
            False,
            id="read-only-and-hidden",
        ),
    ],
)
def test_file_attributes_reads_the_attribute_and_tag_structure_back(
    attributes: int,
    is_reparse_point: bool,
    is_directory: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The structure parse, over real bytes at a real address.

    ``GetFileInformationByHandleEx`` is a fake, but the buffer it fills is a
    genuine ``FILE_ATTRIBUTE_TAG_INFO`` - two little-endian ``DWORD``s - and the
    production code's ``int.from_bytes(buffer.raw[:4], "little")`` really parses
    it.  So this is the test that fails if the first field is read at the wrong
    offset, with the wrong width, or big-endian: the second ``DWORD`` always
    carries ``IO_REPARSE_TAG_SYMLINK``, a large non-zero value, so a read that
    slid by four bytes or took all eight would be visible rather than
    plausible.

    The five cases cover both bits the reader masks with, a file that carries
    neither, and an ordinary file wearing unrelated attributes - the last of
    which is what a mask written as ``==`` rather than ``&`` would misclassify.
    A directory junction carries both bits and must be seen as a reparse point,
    which is why the production order tests that bit first.
    """
    target = _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")
    world = _FakeWin32World(target, attributes=attributes)
    binding = _win32_binding(monkeypatch, world)
    handle = binding.open_handle(target)

    reported = binding.file_attributes(handle)

    assert reported == attributes
    assert bool(reported & properties._WINDOWS_ATTRIBUTE_REPARSE_POINT) is (
        is_reparse_point
    )
    assert bool(reported & properties._WINDOWS_ATTRIBUTE_DIRECTORY) is is_directory

    (call,) = world.calls_to("GetFileInformationByHandleEx")
    assert call.arguments[0] == handle
    assert call.arguments[1] == properties._WINDOWS_FILE_ATTRIBUTE_TAG_INFO
    assert call.arguments[3] == properties._WINDOWS_FILE_ATTRIBUTE_TAG_INFO_BYTES

    binding.close_handle(handle)
    _assert_win32_discipline(world)


@pytest.mark.parametrize(
    ("attributes", "expected"),
    [
        pytest.param(FILE_ATTRIBUTE_NORMAL, None, id="ordinary-file"),
        pytest.param(
            properties._WINDOWS_ATTRIBUTE_REPARSE_POINT,
            properties._REASON_LINK,
            id="reparse-point",
        ),
        pytest.param(
            properties._WINDOWS_ATTRIBUTE_REPARSE_POINT
            | properties._WINDOWS_ATTRIBUTE_DIRECTORY,
            properties._REASON_LINK,
            id="directory-junction",
        ),
    ],
)
def test_the_classification_of_a_real_binding_s_attributes(
    attributes: int,
    expected: str | None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reparse-point decision, taken on what the real layer returned.

    The classification step above the Win32 layer, driven here by the layer
    itself rather than by a stand-in: the attributes travel from a fabricated
    ``FILE_ATTRIBUTE_TAG_INFO``, through the real parse, into the real refusal.
    A reparse point - whether it is a file symlink or a directory junction - is
    ``SEC2-F10``'s non-POSIX half and must be refused with the link reason;
    an ordinary file must pass silently.

    The Win32 query is a fake, so nothing here says Windows would report those
    attributes for a junction; it says what the reader does when they are
    reported.
    """
    target = _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")
    world = _FakeWin32World(target, attributes=attributes)
    binding = _win32_binding(monkeypatch, world)
    handle = binding.open_handle(target)

    try:
        if expected is None:
            assert (
                properties._refuse_a_windows_reparse_point_or_directory(
                    binding, handle
                )
                is None
            )
        else:
            with pytest.raises(properties._RefusedConfigurationError) as raised:
                properties._refuse_a_windows_reparse_point_or_directory(
                    binding, handle
                )
            assert raised.value.reason == expected
    finally:
        binding.close_handle(handle)

    _assert_win32_discipline(world)


def test_file_attributes_refuses_a_query_it_cannot_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed attribute query is a refusal, and stays one.

    Fail closed: an object whose attributes cannot be read cannot be *shown*
    not to be a link, so it may not be accepted.  Asserted twice over, because
    two different pieces of code have to agree about it - the layer raises the
    refusal, and the classification step above it re-raises that refusal
    unchanged rather than re-wrapping it as an unverifiable-object fault it
    would then be indistinguishable from.

    ``GetFileInformationByHandleEx`` returning ``FALSE`` is simulated; that a
    real one ever does is not established here.
    """
    target = _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")
    world = _FakeWin32World(target)
    binding = _win32_binding(monkeypatch, world)
    handle = binding.open_handle(target)
    world.fail("GetFileInformationByHandleEx")

    with pytest.raises(properties._RefusedConfigurationError) as raised:
        binding.file_attributes(handle)
    assert raised.value.reason == properties._REASON_UNVERIFIABLE_OBJECT

    with pytest.raises(properties._RefusedConfigurationError) as reraised:
        properties._refuse_a_windows_reparse_point_or_directory(binding, handle)
    assert reraised.value.reason == properties._REASON_UNVERIFIABLE_OBJECT
    assert reraised.value.__cause__ is None, (
        "a refusal from the layer is re-raised, not re-wrapped"
    )

    binding.close_handle(handle)
    _assert_win32_discipline(world)


# --- descriptor_from_handle and close_handle ------------------------------- #


def test_descriptor_from_handle_transfers_the_handle_and_reads_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The seam where the Windows layer hands over to the shared read path.

    ``msvcrt.open_osfhandle`` is a stub that returns a real POSIX descriptor on
    the real fixture file, which is what lets the rest of the read be real: the
    descriptor is ``fstat``-able and readable here exactly as the converted
    handle is on Windows.  What is asserted is the flag set - ``O_RDONLY``, plus
    ``O_BINARY`` where the platform defines it, which is the difference between
    reading the credential file's bytes and reading them with CRLF translated -
    and the ownership transfer the production comment marks: after the call the
    handle belongs to the descriptor, so ``CloseHandle`` must not be called for
    it and closing the descriptor is what closes it.
    """
    target = _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")
    world = _FakeWin32World(target)
    binding = _win32_binding(monkeypatch, world)
    handle = binding.open_handle(target)

    descriptor = binding.descriptor_from_handle(handle)

    assert world.transfer_flags == [os.O_RDONLY | getattr(os, "O_BINARY", 0)]
    assert world.transferred_handles == [handle]
    assert os.read(descriptor, 4096).decode(DEFAULT_ENCODING).startswith(BROWSER_KEY)
    os.close(descriptor)

    assert world.closed_handles == [], "the descriptor owns the handle now"
    _assert_win32_discipline(world)


def test_close_handle_closes_a_handle_that_never_reached_a_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal paths' release call, asserted on its own.

    Every verification step runs before the transfer, so every refusal reaches
    a handle that is still the reader's own; ``close_handle`` is what releases
    it.  On Windows a leaked handle here is a *locked* credential file - the
    open requests ``FILE_SHARE_READ`` only - so the release is not a
    housekeeping detail.

    ``CloseHandle`` is a fake, so this records that the call was made with the
    right handle rather than proving the operating system released anything.
    """
    target = _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")
    world = _FakeWin32World(target)
    binding = _win32_binding(monkeypatch, world)
    handle = binding.open_handle(target)

    assert binding.close_handle(handle) is None

    assert world.closed_handles == [FAKE_FILE_HANDLE]
    assert world.transferred_handles == []
    (call,) = world.calls_to("CloseHandle")
    assert call.arguments == (handle,)
    _assert_win32_discipline(world)


def test_a_failed_handle_transfer_closes_the_handle_and_stays_tolerated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """If the transfer raises, the handle is still the reader's - and is closed.

    The other side of the ownership boundary.  ``open_osfhandle`` can fail -
    the process descriptor table is full - and then no transfer happened, so the
    handle must be closed by the code that still owns it rather than leaked or
    double-closed.  The failure is an ``OSError``, which makes it one of the I/O
    faults ``ConfigurationReader`` tolerates, so the load reports the
    missing-file message and returns an empty mapping.

    The transfer failure is injected into the stub ``open_osfhandle``; the real
    one's failure modes are not exercised on this host.
    """
    monkeypatch.chdir(tmp_path)
    _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")
    world = _FakeWin32World(
        tmp_path / PROPERTIES_FILENAME,
        transfer_error=OSError(errno.EMFILE, "too many open files"),
    )
    _install_fake_win32_world(monkeypatch, world)

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        assert load_properties() == {}

    records = _missing_file_records(caplog)
    assert len(records) == 1
    assert records[0].exc_info is not None
    assert _refusal_records(caplog) == []
    # The token handle the owner check opened, closed by that method's own
    # ``finally``, and then the file handle, closed by the ownership boundary's
    # ``except`` because the transfer never happened.
    assert world.closed_handles == [FAKE_TOKEN_HANDLE, FAKE_FILE_HANDLE]
    assert world.transferred_handles == []
    assert world.descriptors == []
    _assert_win32_discipline(world)


# --- security_facts: the extraction the policy consumes -------------------- #


@pytest.mark.parametrize(
    ("world_arguments", "expected_owner", "expected_trustees", "expected_reason"),
    [
        pytest.param({}, ACCOUNT_SID, (ACCOUNT_SID,), None, id="owner-only"),
        pytest.param(
            {"allowed": (ACCOUNT_SID, LOCAL_SYSTEM_SID, ADMINISTRATORS_SID)},
            ACCOUNT_SID,
            (ACCOUNT_SID, LOCAL_SYSTEM_SID, ADMINISTRATORS_SID),
            None,
            id="owner-system-administrators",
        ),
        pytest.param(
            {"owner": FOREIGN_ACCOUNT_SID},
            FOREIGN_ACCOUNT_SID,
            (ACCOUNT_SID,),
            properties._REASON_FOREIGN_OWNER,
            id="foreign-owner",
        ),
        pytest.param(
            {"allowed": (ACCOUNT_SID, USERS_SID)},
            ACCOUNT_SID,
            (ACCOUNT_SID, USERS_SID),
            properties._REASON_OPEN_PERMISSIONS,
            id="third-trustee",
        ),
        pytest.param(
            {"allowed": (EVERYONE_SID,)},
            ACCOUNT_SID,
            (EVERYONE_SID,),
            properties._REASON_OPEN_PERMISSIONS,
            id="everyone",
        ),
        pytest.param(
            {"null_dacl": True},
            ACCOUNT_SID,
            None,
            properties._REASON_OPEN_PERMISSIONS,
            id="null-dacl",
        ),
        pytest.param(
            {"allowed": ()},
            ACCOUNT_SID,
            (),
            properties._REASON_OPEN_PERMISSIONS,
            id="empty-dacl",
        ),
    ],
)
def test_security_facts_reads_the_owner_and_the_dacl_off_the_handle(
    world_arguments: dict[str, Any],
    expected_owner: bytes,
    expected_trustees: tuple[bytes, ...] | None,
    expected_reason: str | None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The extraction, end to end, over real SID bytes at real addresses.

    The policy above is a pure function and is tested as one; this is the part
    that *feeds* it, and it is where a wrong offset would produce facts that are
    internally consistent and wrong.  Every SID here is fabricated in its binary
    form, placed inside the one allocation the fake ``GetSecurityInfo`` hands
    out pointers into, and then located, measured, copied and compared by the
    real code through ``IsValidSid``, ``GetLengthSid``, ``string_at`` and
    ``EqualSid`` - so ``S-1-5-21-1-2-3-1001`` has to come back out of the bytes
    as itself, and a trustee has to be read from eight bytes into its own ACE.

    The seven cases are the seven shapes the decision has to tell apart: the
    owner alone, the owner plus the two machine-administrative principals that
    change nothing, an owner that is not this account, a third trustee, the
    ``Everyone`` ACE, a NULL DACL - which grants every account access and is
    reported as ``None`` rather than as an empty tuple - and a DACL that grants
    nobody, which is not evidence of an owner-only grant either.

    The Win32 calls are fakes: no real ``GetSecurityInfo`` runs and nothing here
    establishes how Windows would report a real file's owner.
    """
    target = _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")
    world = _FakeWin32World(target, **world_arguments)
    binding = _win32_binding(monkeypatch, world)
    handle = binding.open_handle(target)

    facts = binding.security_facts(handle)

    assert facts.owner == expected_owner
    assert facts.account == ACCOUNT_SID
    assert facts.owner_equivalent == (LOCAL_SYSTEM_SID, ADMINISTRATORS_SID)
    assert facts.allowed_trustees == expected_trustees
    assert properties._classify_windows_protection(facts) == expected_reason

    # The ACL walk asked for every ACE the fabricated DACL declares, and no
    # more - a count read out of the structure, not assumed.
    assert len(world.calls_to("GetAce")) == (
        0 if world_arguments.get("null_dacl") else world.ace_count
    )
    assert world.freed == [world.descriptor_address]

    binding.close_handle(handle)
    _assert_win32_discipline(world)


def test_security_facts_requests_the_owner_and_the_dacl_and_nothing_else(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The security query asks for exactly two things, of one handle.

    ``GetSecurityInfo`` is a fake, so this is about the request rather than the
    answer.  It matters for two reasons the production comment states: the group
    SID and the SACL take no part in the decision, and *reading* a SACL needs
    ``SeSecurityPrivilege`` - a privilege this process has no reason to hold, so
    asking for one would turn a readable file into an access-denied refusal on a
    correctly-configured machine.  The two out-parameters for them are therefore
    NULL, the object type is ``SE_FILE_OBJECT``, and the query is made against
    the handle the open returned rather than against the path.
    """
    target = _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")
    world = _FakeWin32World(target)
    binding = _win32_binding(monkeypatch, world)
    handle = binding.open_handle(target)

    binding.security_facts(handle)

    (call,) = world.calls_to("GetSecurityInfo")
    handle_argument, object_type, information = call.arguments[:3]
    owner, group, dacl, sacl, descriptor = call.arguments[3:]

    assert handle_argument == handle
    assert object_type == properties._WINDOWS_SE_FILE_OBJECT
    assert information == (
        properties._WINDOWS_OWNER_SECURITY_INFORMATION
        | properties._WINDOWS_DACL_SECURITY_INFORMATION
    )
    # GROUP_SECURITY_INFORMATION (2) and SACL_SECURITY_INFORMATION (8).
    assert information & 0x00000002 == 0
    assert information & 0x00000008 == 0
    assert group is None and sacl is None
    for out_parameter in (owner, dacl, descriptor):
        assert out_parameter is not None

    binding.close_handle(handle)
    _assert_win32_discipline(world)


def test_security_facts_copies_every_sid_before_the_descriptor_is_freed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing is read out of the allocation after ``LocalFree``.

    The owner SID and the DACL both point *into* the single allocation
    ``GetSecurityInfo`` returns, so every value the facts carry has to be copied
    out before the ``finally`` frees it - and the facts are what the policy then
    decides on, after the memory is gone.  The fabricated world puts both behind
    a reserved ``SECURITY_DESCRIPTOR`` header inside one buffer at one address,
    so this is checkable by call order: every call that reads an address inside
    that block precedes the ``LocalFree`` of it.

    The allocation is a ctypes buffer that outlives the free, so a use after
    free would *not* fault here - which is exactly why the ordering is asserted
    rather than assumed to be enforced by a crash.  Whether Windows really
    invalidates the memory is not something this host can show.
    """
    target = _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")
    world = _FakeWin32World(
        target, allowed=(ACCOUNT_SID, LOCAL_SYSTEM_SID, ADMINISTRATORS_SID)
    )
    binding = _win32_binding(monkeypatch, world)
    handle = binding.open_handle(target)

    facts = binding.security_facts(handle)

    block = range(
        world.descriptor_address, world.descriptor_address + world.descriptor_bytes
    )
    free_index = world.call_order().index("LocalFree")
    reads_into_the_block = [
        index
        for index, call in enumerate(world.calls)
        # LocalFree's own argument is that address, which is the call being
        # ordered against rather than one of the reads being ordered.
        if call.entry != "LocalFree"
        and any(_pointer_value(argument) in block for argument in call.arguments)
    ]

    assert reads_into_the_block, "the fabricated block must actually be read"
    assert max(reads_into_the_block) < free_index
    assert world.freed == [world.descriptor_address]
    assert facts.owner == ACCOUNT_SID
    assert facts.allowed_trustees == (
        ACCOUNT_SID,
        LOCAL_SYSTEM_SID,
        ADMINISTRATORS_SID,
    )

    binding.close_handle(handle)
    _assert_win32_discipline(world)


def test_security_facts_refuses_a_failed_query_and_frees_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-``ERROR_SUCCESS`` status is a refusal, with nothing to release.

    ``GetSecurityInfo`` reports failure by *returning* a Win32 error code rather
    than by setting the last-error value, which is why the production code
    compares it instead of calling ``ctypes.WinError`` - and a failed call
    allocates no security descriptor, so the refusal path must not free one.
    Fail closed: a file whose owner and DACL could not be read is refused, never
    accepted.

    The status is injected; that a real ``GetSecurityInfo`` returns
    ``ERROR_ACCESS_DENIED`` in any particular circumstance is not established
    here.
    """
    target = _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")
    world = _FakeWin32World(target)
    binding = _win32_binding(monkeypatch, world)
    handle = binding.open_handle(target)
    world.fail("GetSecurityInfo", result=ERROR_ACCESS_DENIED)

    with pytest.raises(properties._RefusedConfigurationError) as raised:
        binding.security_facts(handle)

    assert raised.value.reason == properties._REASON_UNVERIFIABLE_OBJECT
    assert world.freed == []
    assert world.calls_to("LocalFree") == []

    binding.close_handle(handle)
    _assert_win32_discipline(world)


@pytest.mark.parametrize(
    ("world_arguments", "expected_reason"),
    [
        pytest.param({}, None, id="accepted"),
        pytest.param(
            {"owner": FOREIGN_ACCOUNT_SID},
            properties._REASON_FOREIGN_OWNER,
            id="foreign-owner",
        ),
        pytest.param(
            {"allowed": (ACCOUNT_SID, USERS_SID)},
            properties._REASON_OPEN_PERMISSIONS,
            id="wide-ace",
        ),
    ],
)
def test_the_owner_and_dacl_verification_over_the_real_extraction(
    world_arguments: dict[str, Any],
    expected_reason: str | None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 0600-equivalent decision, taken on facts the real layer produced.

    The policy has its own pure-function tests above; what this adds is the
    join between them and the Win32 layer, with nothing fabricated in between:
    the SIDs travel from byte buffers, through ``GetSecurityInfo``'s pointers,
    through the bounded copies, into the classifier, and out as the fixed reason
    an operator reads in the log.  A refusal that came from the extraction
    rather than from the policy is also asserted to pass through unchanged
    rather than being re-wrapped, because "this was determined and it was wrong"
    and "this could not be determined" are different operational facts and the
    one refusal record has to say which happened.

    The Win32 calls are fakes; no DACL on this host is consulted.
    """
    target = _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")
    world = _FakeWin32World(target, **world_arguments)
    binding = _win32_binding(monkeypatch, world)
    handle = binding.open_handle(target)

    try:
        if expected_reason is None:
            assert (
                properties._verify_windows_owner_and_dacl(binding, handle) is None
            )
        else:
            with pytest.raises(properties._RefusedConfigurationError) as raised:
                properties._verify_windows_owner_and_dacl(binding, handle)
            assert raised.value.reason == expected_reason
            assert raised.value.__cause__ is None
    finally:
        binding.close_handle(handle)

    _assert_win32_discipline(world)


def test_a_refusal_from_the_security_layer_is_re_raised_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refusal raised *inside* the extraction reaches the caller as itself.

    The verification step wraps anything unexpected - a ``ctypes`` fault, a
    structure that does not parse - into an unverifiable-object refusal, and it
    must not wrap a refusal the layer already raised: doing so would lose the
    distinction the reason phrases exist to carry, and would attach a
    ``__cause__`` chain to a record that is deliberately traceback-free.  Here
    the layer refuses because ``GetSecurityInfo`` failed, and the same reason
    arrives at the caller with no cause attached.
    """
    target = _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")
    world = _FakeWin32World(target)
    binding = _win32_binding(monkeypatch, world)
    handle = binding.open_handle(target)
    world.fail("GetSecurityInfo", result=ERROR_ACCESS_DENIED)

    with pytest.raises(properties._RefusedConfigurationError) as raised:
        properties._verify_windows_owner_and_dacl(binding, handle)

    assert raised.value.reason == properties._REASON_UNVERIFIABLE_OBJECT
    assert raised.value.__cause__ is None
    assert world.freed == []

    binding.close_handle(handle)
    _assert_win32_discipline(world)


def test_security_facts_frees_the_descriptor_on_a_refusal_as_well(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refusal after a *successful* query still releases the allocation.

    The path a leak would hide on: the query succeeded, so an allocation exists,
    and the refusal comes from the structure parse that follows it.  The
    production ``finally`` covers that, and this is the assertion that it does -
    once, for the one allocation, with the refusal still reaching the caller.

    ``IsValidSid`` is made to reject the owner SID, which is a fake rejection of
    a real byte buffer; the fabricated SID is in fact well formed.
    """
    target = _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")
    world = _FakeWin32World(target)
    binding = _win32_binding(monkeypatch, world)
    handle = binding.open_handle(target)
    world.fail("IsValidSid")

    with pytest.raises(properties._RefusedConfigurationError) as raised:
        binding.security_facts(handle)

    assert raised.value.reason == properties._REASON_UNVERIFIABLE_OBJECT
    assert world.freed == [world.descriptor_address]

    binding.close_handle(handle)
    _assert_win32_discipline(world)


def test_a_null_owner_sid_is_a_refusal_rather_than_an_unowned_acceptance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An out-parameter left NULL refuses, and the allocation is still freed.

    ``GetSecurityInfo`` can report success and hand back no owner - an object
    with no owner in its descriptor.  The classifier would refuse facts with no
    owner anyway, but the extraction refuses first and for a stronger reason: a
    NULL ``PSID`` must never be measured or copied, because ``GetLengthSid`` on
    one reads whatever is at address zero.  This is the NULL-pointer failure
    mode of the SID path, asserted where it is deterministic.
    """
    target = _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")
    world = _FakeWin32World(target)
    binding = _win32_binding(monkeypatch, world)
    handle = binding.open_handle(target)

    def _no_owner(
        handle_argument: Any,
        object_type: int,
        information: int,
        owner: Any,
        group: Any,
        dacl: Any,
        sacl: Any,
        descriptor: Any,
    ) -> int:
        """Succeed, with a NULL owner and a live descriptor to free."""
        owner._obj.value = None
        dacl._obj.value = world.dacl_address
        descriptor._obj.value = world.descriptor_address
        return properties._WINDOWS_ERROR_SUCCESS

    world.override("GetSecurityInfo", _no_owner)

    with pytest.raises(properties._RefusedConfigurationError) as raised:
        binding.security_facts(handle)

    assert raised.value.reason == properties._REASON_UNVERIFIABLE_OBJECT
    assert world.calls_to("GetLengthSid") == [], "a NULL SID was measured"
    assert world.freed == [world.descriptor_address]

    binding.close_handle(handle)
    _assert_win32_discipline(world)


# --- _sid_token: the bounded copy every SID goes through ------------------- #


def test_sid_token_copies_a_maximum_length_sid_faithfully(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """The copy is exact at ``SECURITY_MAX_SID_SIZE``, the largest SID there is.

    ``SECURITY_MAX_SID_SIZE`` is 68 bytes because the largest legal SID has 15
    subauthorities - two bytes of revision and count, six of identifier
    authority, then 60 - so a SID of exactly that size is the boundary the
    bounded copy has to admit rather than reject.  The measurement is real: the
    fake ``GetLengthSid`` computes the length from the buffer's own subauthority
    count, the production code copies that many bytes with ``string_at`` and
    re-compares the copy through ``EqualSid``, and the returned bytes are
    asserted equal to the fabricated ones, so an off-by-one in either direction
    fails here.

    ``IsValidSid``, ``GetLengthSid`` and ``EqualSid`` are faithful
    reimplementations over these bytes, not the real calls.
    """
    binding = _win32_binding(monkeypatch, _FakeWin32World())
    largest = _binary_sid(5, *range(21, 36))
    assert len(largest) == properties._WINDOWS_SECURITY_MAX_SID_SIZE
    buffer = ctypes.create_string_buffer(largest, len(largest))

    token = binding._sid_token(ctypes.cast(buffer, ctypes.c_void_p))

    assert token == largest
    assert len(token) == properties._WINDOWS_SECURITY_MAX_SID_SIZE


@pytest.mark.parametrize(
    ("fault", "entry", "result"),
    [
        pytest.param("invalid", "IsValidSid", 0, id="invalid-sid"),
        pytest.param("length", "GetLengthSid", 0, id="zero-length"),
        pytest.param(
            "length",
            "GetLengthSid",
            properties._WINDOWS_SECURITY_MAX_SID_SIZE + 1,
            id="over-long",
        ),
        pytest.param("copy", "EqualSid", 0, id="copy-not-equal"),
    ],
)
def test_sid_token_refuses_a_sid_it_cannot_establish(
    fault: str,
    entry: str,
    result: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every way a SID can fail its three checks is a refusal.

    The three things the production code establishes before a SID becomes an
    inert value the policy may compare - validity, a length within
    ``SECURITY_MAX_SID_SIZE``, and a copy that is still ``EqualSid`` to the
    original - and the fourth case, a length of zero, which would otherwise
    produce an empty ``bytes`` that compares equal to nothing and refuses
    everything for the wrong reason.

    The over-long case is the one that matters most and is the reason the bound
    exists: the length arrives *from an API call*, and ``string_at`` would
    happily copy whatever number of bytes it reported, so a corrupt or hostile
    length is rejected before the read rather than after it. A SID that large
    cannot be built legally, so it is injected as a reported length over a real
    68-byte buffer - which also proves the check is on the reported value rather
    than on the buffer.

    Every call here is a fake, so these are refusals of answers of that shape
    rather than evidence about ``advapi32``.
    """
    world = _FakeWin32World()
    binding = _win32_binding(monkeypatch, world)
    sid = _binary_sid(5, 21, 1, 2, 3, 1001)
    buffer = ctypes.create_string_buffer(sid, len(sid))
    world.fail(entry, result=result)

    with pytest.raises(properties._RefusedConfigurationError) as raised:
        binding._sid_token(ctypes.cast(buffer, ctypes.c_void_p))

    assert raised.value.reason == properties._REASON_UNVERIFIABLE_OBJECT
    if fault == "invalid":
        assert world.calls_to("GetLengthSid") == [], "an invalid SID was measured"
    if fault == "length":
        assert world.calls_to("EqualSid") == [], "an unbounded SID was copied"


def test_sid_token_refuses_a_null_pointer_without_touching_it(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """A NULL ``PSID`` is refused before any call is made on it.

    The first of the three checks, and the one that has to come first: neither
    ``IsValidSid`` nor ``GetLengthSid`` may be handed address zero.
    """
    world = _FakeWin32World()
    binding = _win32_binding(monkeypatch, world)

    with pytest.raises(properties._RefusedConfigurationError) as raised:
        binding._sid_token(ctypes.c_void_p())

    assert raised.value.reason == properties._REASON_UNVERIFIABLE_OBJECT
    assert world.calls == [], "a NULL SID reached advapi32"


# --- _allowed_trustees: the DACL walk -------------------------------------- #


def test_the_dacl_walk_reads_past_the_ace_types_that_grant_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Denied, audit and alarm ACEs contribute nothing and stop nothing.

    Only ``ACCESS_ALLOWED_ACE_TYPE`` grants access, so the other three simple
    types must be read *past* rather than counted as trustees - and the walk
    must continue afterwards, which is what the trailing allowed ACE proves: a
    walk that stopped at the first non-allowed ACE would silently accept a file
    whose later ACEs grant the world.

    The ACEs are genuine byte structures: each one's ``AceSize`` is what the
    fake ``GetAce`` advances by, so the walk is over a real ACL layout, and the
    two trustees that come back have been located eight bytes into their own
    ACEs and copied out.
    """
    target = _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")
    world = _FakeWin32World(
        target,
        allowed=(ACCOUNT_SID,),
        extra_aces=(
            _simple_ace(ACCESS_DENIED_ACE_TYPE, USERS_SID),
            _simple_ace(SYSTEM_AUDIT_ACE_TYPE, EVERYONE_SID),
            _simple_ace(SYSTEM_ALARM_ACE_TYPE, FOREIGN_ACCOUNT_SID),
            _simple_ace(
                properties._WINDOWS_ACCESS_ALLOWED_ACE_TYPE, LOCAL_SYSTEM_SID
            ),
        ),
    )
    binding = _win32_binding(monkeypatch, world)
    handle = binding.open_handle(target)

    facts = binding.security_facts(handle)

    assert facts.allowed_trustees == (ACCOUNT_SID, LOCAL_SYSTEM_SID)
    assert len(world.calls_to("GetAce")) == 5
    assert properties._classify_windows_protection(facts) is None

    binding.close_handle(handle)
    _assert_win32_discipline(world)


def test_the_dacl_walk_refuses_an_ace_whose_trustee_it_cannot_locate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An ACE layout this module does not parse is a refusal, never a skip.

    A callback ACE (type 9) carries its trustee at the same offset but is
    followed by a conditional expression, and an object ACE (types 5 to 8) puts
    two GUIDs before the SID - so their trustees are not where the simple layout
    says.  Skipping one would let a file be accepted on the strength of ACEs
    nobody read, which is the acceptance-by-omission the whole check exists to
    prevent, so an unparsable ACE refuses the file even though the ACE *before*
    it was a perfectly good owner-only grant.

    The ACE is fabricated, so this is about the walk's own rule rather than
    about how Windows lays out a callback ACE.
    """
    target = _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")
    world = _FakeWin32World(
        target,
        allowed=(ACCOUNT_SID,),
        extra_aces=(
            _simple_ace(ACCESS_ALLOWED_CALLBACK_ACE_TYPE, EVERYONE_SID),
        ),
    )
    binding = _win32_binding(monkeypatch, world)
    handle = binding.open_handle(target)

    with pytest.raises(properties._RefusedConfigurationError) as raised:
        binding.security_facts(handle)

    assert raised.value.reason == properties._REASON_UNVERIFIABLE_OBJECT
    assert len(world.calls_to("GetAce")) == 2, (
        "the walk stopped at the ACE it cannot read"
    )
    assert world.freed == [world.descriptor_address]

    binding.close_handle(handle)
    _assert_win32_discipline(world)


@pytest.mark.parametrize("entry", ["GetAclInformation", "GetAce"])
def test_the_dacl_walk_refuses_a_query_that_fails(
    entry: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``FALSE`` from either ACL call refuses the file.

    The two calls the walk is made of - the ACE count, then each ACE - and
    neither may be allowed to fail quietly: a DACL that cannot be walked is a
    DACL that cannot be shown to grant nobody but the owner.  Fail closed, and
    release the allocation on the way out.
    """
    target = _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")
    world = _FakeWin32World(target)
    binding = _win32_binding(monkeypatch, world)
    handle = binding.open_handle(target)
    world.fail(entry)

    with pytest.raises(properties._RefusedConfigurationError) as raised:
        binding.security_facts(handle)

    assert raised.value.reason == properties._REASON_UNVERIFIABLE_OBJECT
    assert world.freed == [world.descriptor_address]

    binding.close_handle(handle)
    _assert_win32_discipline(world)


def test_the_dacl_walk_refuses_an_implausible_ace_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The walk is bounded by this module's own limit, not by the structure.

    ``AceCount`` is read out of the very structure being inspected, so a corrupt
    or hostile one could drive an arbitrarily long loop of ``GetAce`` calls -
    the same class of unbounded work ``SEC2-F10`` is about, arriving through an
    API answer rather than through the file's bytes.  An ``ACL`` is at most 64
    KiB and the smallest ACE is 12 bytes, so no valid DACL can reach
    ``_WINDOWS_MAX_ACE_COUNT``; one that claims to is refused before the first
    ``GetAce``.
    """
    target = _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")
    world = _FakeWin32World(target)
    binding = _win32_binding(monkeypatch, world)
    handle = binding.open_handle(target)

    def _implausible_count(
        acl: Any, buffer: Any, size: int, information_class: int
    ) -> int:
        """Report one ACE more than the walk will ever accept."""
        _write_through(
            buffer,
            _acl_size_information(properties._WINDOWS_MAX_ACE_COUNT + 1, 65536)[
                :size
            ],
        )
        return 1

    world.override("GetAclInformation", _implausible_count)

    with pytest.raises(properties._RefusedConfigurationError) as raised:
        binding.security_facts(handle)

    assert raised.value.reason == properties._REASON_UNVERIFIABLE_OBJECT
    assert world.calls_to("GetAce") == [], "the count was not checked before the walk"
    assert world.freed == [world.descriptor_address]

    binding.close_handle(handle)
    _assert_win32_discipline(world)


def test_an_ace_pointer_left_null_never_contributes_a_trustee(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``GetAce`` reporting success without writing refuses the file.

    The NULL out-parameter failure mode of the ACL walk.  The guard is placed
    before the ACE header is read precisely so that the outcome is *this* and
    not something that depends on memory the reader was never given: on this
    host ``ctypes.string_at`` on a NULL pointer does not raise - it returns
    that many uninitialised bytes - so a header read first would take the ACE's
    type from them, and a byte that happened to read as denied, audit or alarm
    would make the walk skip the entry and accept, by omission, an ACE that may
    grant access to anyone.  Reading the pointer first makes the answer
    deterministic on every platform: an ACE the reader could not read is an
    unverifiable object.

    The single ACE in the fabricated DACL is the only one, so no other trustee
    could satisfy the assertion either way.
    """
    target = _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")
    world = _FakeWin32World(target, allowed=(ACCOUNT_SID,))
    binding = _win32_binding(monkeypatch, world)
    handle = binding.open_handle(target)
    world.fail("GetAce", result=1)

    with pytest.raises(properties._RefusedConfigurationError) as refusal:
        binding._allowed_trustees(ctypes.c_void_p(world.dacl_address))

    assert refusal.value.reason == properties._REASON_UNVERIFIABLE_OBJECT

    binding.close_handle(handle)
    _assert_win32_discipline(world)


# --- _running_account_sid, and the well-known SIDs ------------------------- #


def test_the_running_account_sid_comes_from_the_process_token(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """"Owned by me" is decided against the process token, in two calls.

    Reading the account SID from the token rather than from a user name is what
    makes the ownership comparison one the kernel enforces: a name can be
    re-pointed, a SID cannot.  What is asserted is the whole documented
    sequence - the token is opened on the ``GetCurrentProcess`` pseudo-handle
    with ``TOKEN_QUERY`` and nothing more, ``GetTokenInformation`` is called
    twice with ``TokenUser`` (the first to learn the size, the second to fill a
    buffer of exactly that size), the ``Sid`` member is followed out of the
    returned ``TOKEN_USER``, and the token handle is closed.

    The ``TOKEN_USER`` is a real :class:`ctypes.Structure` holding a real
    pointer to a real SID buffer, so the production ``cast`` to
    ``POINTER(c_void_p)`` performs a genuine pointer read at the platform's own
    alignment - but ``OpenProcessToken`` itself is a fake, and this host's real
    process token is never consulted.
    """
    world = _FakeWin32World()
    binding = _win32_binding(monkeypatch, world)

    assert binding._running_account_sid() == ACCOUNT_SID

    (token_call,) = world.calls_to("OpenProcessToken")
    assert _pointer_value(token_call.arguments[0]) == CURRENT_PROCESS_HANDLE
    assert token_call.arguments[1] == properties._WINDOWS_TOKEN_QUERY

    sizing, filling = world.calls_to("GetTokenInformation")
    assert sizing.arguments[1] == properties._WINDOWS_TOKEN_USER
    assert sizing.arguments[2] is None and sizing.arguments[3] == 0
    assert filling.arguments[1] == properties._WINDOWS_TOKEN_USER
    assert filling.arguments[3] == len(world.token_user_bytes)
    assert filling.arguments[3] <= properties._WINDOWS_MAX_TOKEN_USER_BYTES

    assert world.closed_handles == [FAKE_TOKEN_HANDLE]
    _assert_win32_discipline(world)


@pytest.mark.parametrize(
    "reported",
    [
        pytest.param(0, id="nothing-needed"),
        pytest.param(
            properties._WINDOWS_MAX_TOKEN_USER_BYTES + 1, id="beyond-the-bound"
        ),
    ],
)
def test_the_token_buffer_size_is_bounded_before_it_is_allocated(
    reported: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An implausible reported size refuses, before anything is allocated.

    The size arrives from an API call, and an allocation driven by an unbounded
    reported length is the same exposure ``SEC2-F10`` is about - so it is
    bounded by a fixed value of this module's own.  A ``TOKEN_USER`` is a
    pointer, a ``DWORD`` and one SID, so a kilobyte is orders of magnitude of
    headroom and anything beyond it is a fault rather than a large token.  Zero
    is refused for the same reason from the other direction: a zero-length
    buffer cannot hold a ``PSID``, and reading one would read whatever followed
    it.

    Both sizes are injected into the fake sizing call; no real
    ``GetTokenInformation`` is involved.
    """
    world = _FakeWin32World()
    binding = _win32_binding(monkeypatch, world)

    def _implausible_size(
        token: Any,
        information_class: int,
        buffer: Any,
        size: int,
        needed: Any,
    ) -> int:
        """Report a size the caller must not honour."""
        needed._obj.value = reported
        return 0

    world.override("GetTokenInformation", _implausible_size)

    with pytest.raises(properties._RefusedConfigurationError) as raised:
        binding._running_account_sid()

    assert raised.value.reason == properties._REASON_UNVERIFIABLE_OBJECT
    assert len(world.calls_to("GetTokenInformation")) == 1
    assert world.closed_handles == [FAKE_TOKEN_HANDLE], "the token leaked"
    _assert_win32_discipline(world)


@pytest.mark.parametrize("failing", ["the-token", "the-filling-query"])
def test_a_token_query_that_fails_refuses_and_closes_the_token(
    failing: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Neither token call may fail quietly, and neither may leak the handle.

    Without the account SID there is nothing to compare the owner against, so a
    failed token query is a refusal rather than a comparison skipped - the
    classifier would refuse the facts anyway, and the extraction refuses first.

    The two cases are the two calls that can fail, and they release differently
    on purpose: a failed ``OpenProcessToken`` produced no token, so there is
    nothing to close and the refusal is raised before the ``try`` that would
    close one; a failed *second* ``GetTokenInformation`` - the one that fills the
    buffer, after the first reported a plausible size - happens inside that
    ``try``, so the method's own ``finally`` closes the token handle on the way
    out.  Mixing the two up is how a handle on the process token leaks.
    """
    world = _FakeWin32World()
    binding = _win32_binding(monkeypatch, world)

    if failing == "the-token":
        world.fail("OpenProcessToken")
    else:

        def _sizes_then_fails(
            token: Any,
            information_class: int,
            buffer: Any,
            size: int,
            needed: Any,
        ) -> int:
            """Report a plausible size on both calls, and fill nothing."""
            needed._obj.value = len(world.token_user_bytes)
            return 0

        world.override("GetTokenInformation", _sizes_then_fails)

    with pytest.raises(properties._RefusedConfigurationError) as raised:
        binding._running_account_sid()

    assert raised.value.reason == properties._REASON_UNVERIFIABLE_OBJECT
    if failing == "the-token":
        # No token was obtained, so there is nothing to close and the refusal is
        # raised before the ``try`` that would close one - closing the
        # uninitialised HANDLE would be a close of a NULL handle.
        assert world.token_handles == []
        assert world.closed_handles == []
        assert world.calls_to("GetTokenInformation") == []
    else:
        assert len(world.calls_to("GetTokenInformation")) == 2
        assert world.closed_handles == [FAKE_TOKEN_HANDLE], "the token leaked"
    _assert_win32_discipline(world)


def test_a_token_user_with_no_sid_is_refused(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``TOKEN_USER`` whose ``Sid`` member is NULL refuses the read.

    The pointer inside the structure is followed, so a NULL there has to be
    caught by the same bounded copy every other SID goes through rather than by
    a check the caller repeats. This is the NULL-pointer failure mode of the
    token path: the world's ``TOKEN_USER`` really does carry a NULL ``Sid``, and
    the production ``cast`` really does read it.
    """
    world = _FakeWin32World(account=None)
    binding = _win32_binding(monkeypatch, world)

    with pytest.raises(properties._RefusedConfigurationError) as raised:
        binding._running_account_sid()

    assert raised.value.reason == properties._REASON_UNVERIFIABLE_OBJECT
    assert world.calls_to("IsValidSid") == [], "a NULL SID was validated"
    assert world.closed_handles == [FAKE_TOKEN_HANDLE]
    _assert_win32_discipline(world)


def test_the_owner_equivalent_sids_are_built_from_their_well_known_types(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """``LocalSystem`` and ``BUILTIN\\Administrators``, by type rather than by
    text.

    The two principals that already administer every file on the machine, so an
    ACE naming either grants nothing new and neither makes a credential file
    "accessible beyond its owner".  They are obtained from their
    ``WELL_KNOWN_SID_TYPE`` values - 22 and 26 - into a buffer of exactly
    ``SECURITY_MAX_SID_SIZE`` bytes, which is what the call documents as
    sufficient, and never parsed from ``S-1-5-18``/``S-1-5-32-544`` text, so no
    textual SID has to be trusted or converted.  Each result then goes through
    the same bounded copy every other SID does, which is why the returned bytes
    are the fabricated ones exactly.

    ``CreateWellKnownSid`` is a fake that answers from a table; the real call's
    output for those two types is not established here, and the values asserted
    are this test module's own fabrications of them.
    """
    world = _FakeWin32World()
    binding = _win32_binding(monkeypatch, world)

    assert binding._owner_equivalent_sids() == (LOCAL_SYSTEM_SID, ADMINISTRATORS_SID)

    first, second = world.calls_to("CreateWellKnownSid")
    assert (first.arguments[0], second.arguments[0]) == (
        properties._WINDOWS_OWNER_EQUIVALENT_SID_TYPES
    )
    for call in (first, second):
        # A NULL domain SID - these two are machine-wide, not domain-relative -
        # and a size out-parameter carrying the documented maximum on the way
        # in, which is what bounds what the call may write.
        assert call.arguments[1] is None
        assert (
            call.arguments[3]._obj.value
            <= properties._WINDOWS_SECURITY_MAX_SID_SIZE
        )
    _assert_win32_discipline(world)


def test_a_well_known_sid_that_cannot_be_built_is_a_refusal(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failing to build either owner-equivalent SID refuses the file.

    Fail closed, and deliberately so even though the failure makes the policy
    *stricter* rather than weaker: without those two SIDs a file administered by
    the machine would be refused with a reason naming its permissions, which is
    a refusal the operator cannot act on, so the honest outcome is the one that
    says the protection could not be verified.
    """
    world = _FakeWin32World()
    binding = _win32_binding(monkeypatch, world)
    world.fail("CreateWellKnownSid")

    with pytest.raises(properties._RefusedConfigurationError) as raised:
        binding._owner_equivalent_sids()

    assert raised.value.reason == properties._REASON_UNVERIFIABLE_OBJECT
    assert len(world.calls_to("CreateWellKnownSid")) == 1, (
        "the first failure refuses; the second type is not attempted"
    )
    _assert_win32_discipline(world)


# --- The whole read, through load_properties, on the real Win32 layer ------ #


def test_the_real_win32_layer_reads_an_owner_only_file_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The positive control for the layer itself, through the public surface.

    AAP section 0.8 supports Windows, so the branch may not be fail-closed
    unconditionally, and this is that requirement asserted against the real
    class rather than against a stand-in: the open, the classification, the
    owner and DACL verification, the handle-to-descriptor transfer and the
    shared bounded read all run, and the file parses to its entry with no
    refusal and no missing-file record.

    It is also where the handle lifecycle is asserted across the ownership
    boundary: exactly one handle was opened, it was transferred rather than
    closed - closing it as well would be a double close of a handle the
    descriptor owns - the one security descriptor was freed once, the token
    handle was closed, and the descriptor itself is closed by the time the load
    returns, which is what stops a worker from holding the credential file open.

    The Win32 calls are fakes, so this establishes the reader's sequencing and
    arithmetic rather than the behaviour of ``kernel32`` or ``advapi32``.
    """
    monkeypatch.chdir(tmp_path)
    target = _write_properties(
        tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}\n{EMPL_TITLE_KEY}=Employees\n"
    )
    world = _FakeWin32World(target)
    _install_fake_win32_world(monkeypatch, world)

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        assert load_properties() == {
            BROWSER_KEY: SYNTHETIC_VALUE,
            EMPL_TITLE_KEY: "Employees",
        }

    assert _refusal_records(caplog) == []
    assert _missing_file_records(caplog) == []
    assert world.opened_handles == [FAKE_FILE_HANDLE]
    assert world.transferred_handles == [FAKE_FILE_HANDLE]
    assert world.closed_handles == [FAKE_TOKEN_HANDLE]
    assert world.freed == [world.descriptor_address]
    (descriptor,) = world.descriptors
    _assert_handle_closed(descriptor)
    _assert_win32_discipline(world)


@pytest.mark.parametrize(
    ("world_arguments", "expected_reason"),
    [
        pytest.param(
            {"attributes": properties._WINDOWS_ATTRIBUTE_REPARSE_POINT},
            properties._REASON_LINK,
            id="reparse-point",
        ),
        pytest.param(
            {"owner": FOREIGN_ACCOUNT_SID},
            properties._REASON_FOREIGN_OWNER,
            id="foreign-owner",
        ),
        pytest.param(
            {"allowed": (ACCOUNT_SID, USERS_SID)},
            properties._REASON_OPEN_PERMISSIONS,
            id="inherited-users-ace",
        ),
        pytest.param(
            {"allowed": (EVERYONE_SID,)},
            properties._REASON_OPEN_PERMISSIONS,
            id="everyone-ace",
        ),
        pytest.param(
            {"null_dacl": True},
            properties._REASON_OPEN_PERMISSIONS,
            id="null-dacl",
        ),
    ],
)
def test_the_real_win32_layer_refuses_what_the_findings_describe(
    world_arguments: dict[str, Any],
    expected_reason: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The four shapes the two findings are about, refused through the real
    layer.

    A junction or symlink in the credential file's place is ``SEC2-F10``'s
    non-POSIX half; a file owned by another local account and a DACL that grants
    ``Users`` or ``Everyone`` are ``SEC2-F33``'s - the ``Users`` ACE being the
    one a checkout directory contributes by inheritance and the direct Windows
    analogue of the 0644 file the finding reports.  A NULL DACL is worse than
    either, since it grants every account full access.

    Each is refused identically and tolerantly: an empty mapping, exactly one
    ``WARNING`` naming the fixed reason and the filename only - no absolute
    path, no traceback, no missing-file record - and nothing raised, so every
    configured key reads as ``None`` at its point of use exactly as it does when
    there is no file at all.  No descriptor is ever created, which is what shows
    the refusal happened on the handle rather than after the read began, and the
    handle is closed on the way out.

    The attributes, the owner SID and the DACL are all fabricated; the refusals
    are real.
    """
    monkeypatch.chdir(tmp_path)
    target = _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")
    world = _FakeWin32World(target, **world_arguments)
    _install_fake_win32_world(monkeypatch, world)

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        assert load_properties() == {}

    record = _assert_refused(caplog, expected_reason, target=target)
    assert SYNTHETIC_VALUE not in record.getMessage()
    assert world.descriptors == []
    assert world.transferred_handles == []
    assert world.closed_handles[-1] == FAKE_FILE_HANDLE
    _assert_win32_discipline(world)


@pytest.mark.parametrize(
    ("entry", "result"),
    [
        pytest.param("GetFileInformationByHandleEx", 0, id="attribute-query"),
        pytest.param("GetSecurityInfo", ERROR_ACCESS_DENIED, id="security-query"),
        pytest.param("IsValidSid", 0, id="invalid-sid"),
        pytest.param("GetLengthSid", 0, id="zero-length-sid"),
        pytest.param(
            "GetLengthSid",
            properties._WINDOWS_SECURITY_MAX_SID_SIZE + 1,
            id="over-long-sid",
        ),
        pytest.param("EqualSid", 0, id="sid-copy-not-equal"),
        pytest.param("GetAclInformation", 0, id="acl-query"),
        pytest.param("GetAce", 0, id="ace-query"),
        pytest.param("OpenProcessToken", 0, id="token-open"),
        pytest.param("GetTokenInformation", 0, id="token-query"),
        pytest.param("CreateWellKnownSid", 0, id="well-known-sid"),
    ],
)
def test_every_win32_failure_mode_is_one_refusal_and_never_an_exception(
    entry: str,
    result: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Every call in the layer, failed one at a time, through the public
    surface.

    The third outcome a security check can have, enumerated rather than
    sampled: for each of the eleven calls the verification is made of, a
    ``FALSE`` return - or, for ``GetSecurityInfo``, a non-success status, and for
    ``GetLengthSid``, an implausible length - must produce a refusal.  Accepting
    would read a credential file whose protection was never established; raising
    would take down a behave worker over a file AAP section 0.1.1 says is
    optional, and neither is allowed to be the outcome of a single fake
    returning zero.

    The refusal reason is the same phrase in every case, and it is the honest
    one: not "this was determined and it was wrong" but "this could not be
    determined".  The handle is released on every one of these paths and any
    security descriptor that had been allocated is freed, which is asserted as
    well, because a refusal that leaks a handle leaves the credential file locked
    on Windows.

    One fake at a time returns failure; nothing here claims that a real call
    ever does.
    """
    monkeypatch.chdir(tmp_path)
    target = _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")
    world = _FakeWin32World(target)
    _install_fake_win32_world(monkeypatch, world)
    world.fail(entry, result=result)

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        assert load_properties() == {}

    _assert_refused(caplog, properties._REASON_UNVERIFIABLE_OBJECT, target=target)
    assert world.descriptors == []
    assert world.closed_handles[-1] == FAKE_FILE_HANDLE
    _assert_win32_discipline(world)


def test_a_failed_open_on_the_real_win32_layer_keeps_the_missing_file_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """``CreateFileW`` failing is the tolerated I/O fault, not a refusal.

    The parity seam, asserted through the real layer: a failed open is exactly
    ``ConfigurationReader``'s ``catch (IOException)`` case - absent, unreadable,
    or locked by a writer - so it logs :data:`MISSING_FILE_MESSAGE` verbatim with
    its traceback and must not be counted as a refusal, because an operator
    counting either record has to count the same events on both platforms.  The
    ``OSError`` that carries it is the one ``ctypes.WinError`` built from the
    last-error value, and its ``errno`` is deliberately not one of the two the
    reader routes to the refusal path.
    """
    monkeypatch.chdir(tmp_path)
    _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")
    world = _FakeWin32World(last_error=ERROR_FILE_NOT_FOUND)
    _install_fake_win32_world(monkeypatch, world)
    world.fail("CreateFileW", result=INVALID_HANDLE_VALUE)

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        assert load_properties() == {}

    records = _missing_file_records(caplog)
    assert len(records) == 1
    assert records[0].getMessage() == MISSING_FILE_MESSAGE
    assert records[0].exc_info is not None
    assert records[0].exc_info[1].errno == errno.ENOENT
    assert records[0].exc_info[1].errno not in properties._LINK_ERRNOS
    assert _refusal_records(caplog) == []
    assert world.win_errors == [ERROR_FILE_NOT_FOUND]
    assert world.opened_handles == []
    _assert_win32_discipline(world)


def test_a_directory_on_the_real_win32_layer_keeps_the_missing_file_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A directory reaches the missing-file path, byte for byte with POSIX.

    ``FILE_FLAG_BACKUP_SEMANTICS`` is in the open flags precisely so that a
    directory in the file's place yields a handle that can be *classified* as
    one rather than an ``ERROR_ACCESS_DENIED`` indistinguishable from a
    permission fault, and the classification then converts it to
    ``IsADirectoryError`` - which is one of the three faults the Java ``catch``
    tolerates, so the record is the parity message with its traceback rather
    than a refusal.  The handle is closed on that path too.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / PROPERTIES_FILENAME).mkdir()
    world = _FakeWin32World(attributes=properties._WINDOWS_ATTRIBUTE_DIRECTORY)
    _install_fake_win32_world(monkeypatch, world)

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        assert load_properties() == {}

    records = _missing_file_records(caplog)
    assert len(records) == 1
    assert records[0].getMessage() == MISSING_FILE_MESSAGE
    assert isinstance(records[0].exc_info[1], IsADirectoryError)
    assert _refusal_records(caplog) == []
    assert world.closed_handles == [FAKE_FILE_HANDLE]
    assert world.descriptors == []
    _assert_win32_discipline(world)


def test_a_real_win32_refusal_is_one_warning_and_every_key_reads_as_none(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    counted_load: _LoadCounter,
) -> None:
    """The tolerance contract, over the real layer and the six configured keys.

    What refusing rather than raising is *for*: a Windows worker whose
    configuration file is protected wrongly behaves exactly like one with no
    configuration at all.  Every one of the six keys reads as ``None`` at its
    point of use, the cached load happened once for all of them, and the reason
    is stated once rather than per access - and no value from the file appears
    in any record, which matters because ``password`` is one of those six keys.

    The key names are from the AAP's frozen six-key inventory; the values in the
    fixture are synthetic, and the Win32 answers are fabricated.
    """
    monkeypatch.chdir(tmp_path)
    configured_keys = (
        BROWSER_KEY,
        DOTTED_KEY,
        "url",
        "username",
        "password",
        EMPL_TITLE_KEY,
    )
    target = _write_properties(
        tmp_path, "".join(f"{key}={SYNTHETIC_VALUE}\n" for key in configured_keys)
    )
    world = _FakeWin32World(target, allowed=(ACCOUNT_SID, USERS_SID))
    _install_fake_win32_world(monkeypatch, world)

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        for key in configured_keys:
            assert get_property(key) is None, key
        assert dict(get_properties()) == {}

    assert counted_load.calls == 1
    _assert_refused(caplog, properties._REASON_OPEN_PERMISSIONS, target=target)
    for record in caplog.records:
        assert SYNTHETIC_VALUE not in record.getMessage()
    _assert_win32_discipline(world)


# --------------------------------------------------------------------------- #
# The one-shot cache - ConfigurationReader:11, and the three assertions AAP
# section 0.6 names for this module.
# --------------------------------------------------------------------------- #


def test_get_properties_loads_from_the_working_directory_on_first_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first access performs the load, resolving the cwd at that moment.

    Not at import time: the working directory in force when configuration is
    first needed is the one that counts.
    """
    monkeypatch.chdir(tmp_path)
    _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")

    assert dict(get_properties()) == {BROWSER_KEY: SYNTHETIC_VALUE}


def test_get_properties_returns_a_read_only_view(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No caller can corrupt the shared cache through the mapping it is given.

    The value returned is a live ``MappingProxyType`` over the module's dict, so
    mutation raises ``TypeError`` instead of quietly reconfiguring the process.
    """
    monkeypatch.chdir(tmp_path)
    _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")

    view = get_properties()
    # Bound through a deliberately untyped name: the mutations below are the
    # point of the test and must reach the proxy at run time, which an
    # annotation of ``Mapping`` would otherwise be read as forbidding.
    mutable_alias: Any = view

    with pytest.raises(TypeError):
        mutable_alias[BROWSER_KEY] = "mutated"
    with pytest.raises(TypeError):
        del mutable_alias[BROWSER_KEY]
    with pytest.raises(AttributeError):
        mutable_alias.clear()
    assert view[BROWSER_KEY] == SYNTHETIC_VALUE


def test_the_load_happens_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, counted_load: _LoadCounter
) -> None:
    """AAP 0.6, first named assertion: one load per process.

    The port of the static initializer at ``ConfigurationReader.java:11,17``.
    Every call after the first returns the cached mapping **without touching
    the filesystem**, which is asserted directly by counting loads: the cache's
    contents alone could not tell one load from two identical ones.
    """
    monkeypatch.chdir(tmp_path)
    _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")

    for _ in range(5):
        assert get_properties()[BROWSER_KEY] == SYNTHETIC_VALUE
    assert get_property(BROWSER_KEY) == SYNTHETIC_VALUE

    assert counted_load.calls == 1


def test_a_missing_file_is_logged_exactly_once_per_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """AAP 0.6, second named assertion: one warning, however many accesses.

    The Java class prints once because its static initializer runs once.  The
    port must not turn a missing file into a warning per lookup - that would
    bury every other diagnostic in a suite that reads configuration in every
    step - so the "a load was attempted" flag is tracked separately from "the
    cache is non-empty".
    """
    monkeypatch.chdir(tmp_path)

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        for _ in range(5):
            assert dict(get_properties()) == {}
        assert get_property(BROWSER_KEY) is None

    assert len(_missing_file_records(caplog)) == 1


def test_a_later_file_change_does_not_alter_an_initialized_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, counted_load: _LoadCounter
) -> None:
    """AAP 0.6, third named assertion: an initialized reader never re-reads.

    This is parity with the JVM rather than a caching optimisation, and it is
    load-bearing for a run's coherence: a configuration edited while a suite is
    running must not take effect halfway through it, or two scenarios in one run
    would disagree about the configuration they ran under.
    """
    monkeypatch.chdir(tmp_path)
    _write_properties(tmp_path, f"{BROWSER_KEY}=before-the-change")

    assert get_property(BROWSER_KEY) == "before-the-change"

    _write_properties(tmp_path, f"{BROWSER_KEY}=after-the-change\nadded=new-key")

    assert get_property(BROWSER_KEY) == "before-the-change"
    assert get_property("added") is None
    assert counted_load.calls == 1


def test_a_file_appearing_after_the_first_attempt_does_not_alter_the_reader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    counted_load: _LoadCounter,
) -> None:
    """A file that appears late is not picked up, and logs nothing further.

    The same one-shot rule seen from the other side: the *attempt* is what is
    recorded, so a failed first attempt is never retried and the single warning
    stays single.
    """
    monkeypatch.chdir(tmp_path)

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        assert dict(get_properties()) == {}
        _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")
        assert dict(get_properties()) == {}
        assert get_property(BROWSER_KEY) is None

    assert counted_load.calls == 1
    assert len(_missing_file_records(caplog)) == 1


def test_a_present_but_empty_file_does_not_trigger_a_second_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, counted_load: _LoadCounter
) -> None:
    """An empty cache is not an uninitialized one.

    A file that is present and empty loads successfully to nothing, and that
    outcome is indistinguishable from a missing file in the cache's *contents* -
    which is exactly why the module tracks the attempt with its own flag instead
    of testing the mapping for emptiness.
    """
    monkeypatch.chdir(tmp_path)
    _write_properties_bytes(tmp_path, b"# only a comment\n")

    assert dict(get_properties()) == {}
    assert dict(get_properties()) == {}
    assert counted_load.calls == 1


def test_a_malformed_file_fails_the_one_time_load_permanently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    counted_load: _LoadCounter,
) -> None:
    """A failed load is remembered, and every later access fails the same way.

    The cache's half of the malformed-escape contract, and the only outcome the
    one-shot cache has to record besides success: the JVM's static initializer
    fails once and leaves ``ConfigurationReader`` permanently unusable, so the
    port's reader must not retry the file, must not fall back to an empty
    mapping - which would read as "no configuration" and move the fault
    somewhere else entirely - and must raise the same fixed, data-free message
    on every later call.

    Asserted across three accesses and both public entry points, with the load
    counted so that "never re-read" is measured rather than inferred, and with
    the log checked for the digits of the offending escape, because the message
    is fixed precisely so that a fragment of a value - ``password`` is one of
    the six keys - cannot reach a record.  ``reset_cache`` then clears the
    failure, which is what keeps one malformed-file test from failing every
    later test in the session.
    """
    monkeypatch.chdir(tmp_path)
    _write_properties(tmp_path, f"{BROWSER_KEY}=\\u12zz")

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        with pytest.raises(ValueError) as first:
            get_properties()
        with pytest.raises(ValueError) as second:
            get_properties()
        with pytest.raises(ValueError) as third:
            get_property(BROWSER_KEY)

    for raised in (first, second, third):
        assert str(raised.value) == properties._MALFORMED_ESCAPE_MESSAGE
    assert counted_load.calls == 1, "the failed file was read again"
    assert _missing_file_records(caplog) == []
    assert _refusal_records(caplog) == []
    for record in caplog.records:
        assert "12zz" not in record.getMessage()

    reset_cache()
    _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")
    assert dict(get_properties()) == {BROWSER_KEY: SYNTHETIC_VALUE}
    assert counted_load.calls == 2


def test_concurrent_first_access_performs_one_load_and_one_log_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Threads racing the first access still produce one load and one warning.

    behave may run threads inside a worker process, so the first access is not
    reliably single-threaded.  The lock with its double-checked flag is what
    makes the race safe, and the flag being set *after* the dict is populated is
    what makes the lock-free fast path safe; both are asserted here by racing
    eight threads through a barrier onto a missing file, with the loader held
    inside the critical section long enough that a second load would happen if
    nothing prevented it.
    """
    monkeypatch.chdir(tmp_path)
    counter = _LoadCounter(delay=0.05)
    monkeypatch.setattr(properties, "load_properties", counter)

    thread_count = 8
    barrier = threading.Barrier(thread_count)
    seen: list[object] = []
    failures: list[BaseException] = []
    seen_lock = threading.Lock()

    def _access() -> None:
        """Race onto the first access and record what came back."""
        try:
            barrier.wait(timeout=10)
            view = get_properties()
            with seen_lock:
                seen.append(view)
        except BaseException as exc:
            # Collected rather than raised: an exception raised in a worker
            # thread does not fail the test, it is printed and lost.  The
            # assertion on ``failures`` below is what surfaces it, and
            # ``BaseException`` is deliberate so that a barrier timeout or a
            # KeyboardInterrupt is reported too instead of vanishing.
            with seen_lock:
                failures.append(exc)

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        threads = [
            threading.Thread(target=_access, name=f"properties-race-{index}")
            for index in range(thread_count)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
            assert not thread.is_alive(), "a racing thread did not finish"

    assert not failures, failures
    assert len(seen) == thread_count
    assert counter.calls == 1
    assert len(_missing_file_records(caplog)) == 1
    # One view object, handed to every thread: the proxy is built once over the
    # module dict, so no thread can hold a snapshot that diverges.
    assert all(view is seen[0] for view in seen)


def test_reset_cache_returns_the_reader_to_its_pre_load_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    counted_load: _LoadCounter,
) -> None:
    """The declared test-support seam, asserted as the seam it is.

    Without it the three assertions above could each be made only once per
    process, and the suite could not prove the one-shot behaviour at all.  What
    it restores is both halves of the state: a later access loads again, and a
    missing file is logged again because the attempt is being made afresh.
    """
    monkeypatch.chdir(tmp_path)

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        assert dict(get_properties()) == {}
        assert counted_load.calls == 1

        reset_cache()
        _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")

        assert dict(get_properties()) == {BROWSER_KEY: SYNTHETIC_VALUE}
        assert counted_load.calls == 2

    assert len(_missing_file_records(caplog)) == 1


def test_a_view_handed_out_before_a_reset_observes_the_reloaded_contents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Earlier views stay valid across a reset, because the dict is reused.

    The cache is a dict mutated in place behind one proxy, never rebound, which
    is what makes the hot path allocation-free and what makes a view a live view
    rather than a snapshot.
    """
    monkeypatch.chdir(tmp_path)
    _write_properties(tmp_path, f"{BROWSER_KEY}=before-the-reset")

    view = get_properties()
    assert view[BROWSER_KEY] == "before-the-reset"

    reset_cache()
    assert dict(view) == {}

    _write_properties(tmp_path, f"{BROWSER_KEY}=after-the-reset")
    assert get_properties() is view
    assert view[BROWSER_KEY] == "after-the-reset"


# --------------------------------------------------------------------------- #
# get_property - ConfigurationReader.java:27-29.
# --------------------------------------------------------------------------- #


def test_get_property_returns_none_for_an_absent_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``getProperty`` returns ``null``; this returns ``None``.

    ``ConfigurationReader.java:27-29`` hands ``Properties.getProperty`` through
    untouched, so an absent key answers ``null`` there and ``None`` here.
    Deliberate, and the reason the whole layer is tolerant: a configuration
    problem surfaces at the point of use, not at start-up, which is the
    behaviour the rest of the suite is written against.
    """
    monkeypatch.chdir(tmp_path)
    _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")

    assert get_property("no.such.key") is None


def test_get_property_returns_the_supplied_default_for_an_absent_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The two-argument form answers with the caller's default.

    The one-argument form's ``None`` is the parity behaviour; the default is for
    a caller that has a sensible fallback of its own.
    """
    monkeypatch.chdir(tmp_path)
    _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")

    assert get_property("no.such.key", "fallback") == "fallback"


def test_get_property_distinguishes_an_absent_key_from_an_empty_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A key present with no value is ``""``; only an absent key is the default.

    The distinction is the difference between "this file says the value is
    nothing" and "this file does not mention it", and the shipped template makes
    it the common case: it carries all six keys with empty values.
    """
    monkeypatch.chdir(tmp_path)
    _write_properties(tmp_path, f"{BROWSER_KEY}=\n{EMPL_TITLE_KEY}")

    assert get_property(BROWSER_KEY) == ""
    assert get_property(EMPL_TITLE_KEY) == ""
    assert get_property("absent") is None
    assert get_property(BROWSER_KEY, "fallback") == ""


def test_get_property_is_case_sensitive_and_normalises_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keys and values are neither case-folded nor stripped.

    A mixed-case key must be looked up exactly as written - ``EmplTitle``, one
    of the frozen six, is the live example - and an unrecognised value is
    returned as written so that it reaches its consumer and fails at first use,
    because the Java ``Driver`` switch has no default branch.  Validating or
    normalising here would change behaviour.
    """
    monkeypatch.chdir(tmp_path)
    _write_properties(
        tmp_path, f"{EMPL_TITLE_KEY}=  Padded Title  \n{BROWSER_KEY}=NotABrowser"
    )

    assert get_property(EMPL_TITLE_KEY) == "Padded Title  "
    assert get_property(EMPL_TITLE_KEY.lower()) is None
    assert get_property(EMPL_TITLE_KEY.upper()) is None
    assert get_property(BROWSER_KEY) == "NotABrowser"


def test_get_property_triggers_the_one_time_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, counted_load: _LoadCounter
) -> None:
    """Either accessor initializes the reader, and only one of them loads.

    ``get_property`` delegates to ``get_properties``, so reaching the cache
    through the single-key accessor first must leave the process in the same
    one-load state.
    """
    monkeypatch.chdir(tmp_path)
    _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")

    assert get_property(BROWSER_KEY) == SYNTHETIC_VALUE
    assert get_properties()[BROWSER_KEY] == SYNTHETIC_VALUE
    assert counted_load.calls == 1


def test_no_test_in_this_module_leaves_a_configuration_file_in_the_repository() -> None:
    """A guard on this module's own hygiene, not on the production code.

    A ``configuration.properties`` left in the repository root would silently
    become the configuration of every later test in the session and of every
    subsequent run in the clone.  Every filesystem test above writes only under
    ``tmp_path``; this asserts the outcome rather than trusting the intent.
    Collection order puts it last within this module, and it is equally valid
    run on its own.
    """
    assert not (REPO_ROOT / PROPERTIES_FILENAME).exists()
