r"""Tests for ``app/utils/properties.py`` - the ``java.util.Properties`` reader.

This module owns the whole of the properties layer's evidence.  It is the only
test module that exercises the Java ``.properties`` grammar, the ISO-8859-1
I/O, the missing-file tolerance and the one-shot process cache, so a case that
is not covered here is not covered anywhere: ``app/config.py`` is the only
production importer of this module, and its own test module asserts the six-key
accessor surface layered *on top of* this reader rather than the reader itself.

What it is held to
------------------
``app/utils/properties.py`` is the port of
``com.testinium.utilities.ConfigurationReader``, and the fixed strings and
behaviours below are **parity**, not preference.  The reference is the pinned
Java source at commit ``47e9d697e4a9a85da889f94a846fdf47af28a240``:

* ``ConfigurationReader.java:14`` - ``new FileInputStream("configuration.properties")``,
  a bare relative name resolved against the process working directory.
* ``ConfigurationReader.java:11,17`` - the load happens once, in a static
  initializer, through ``Properties.load(InputStream)``.
* ``ConfigurationReader.java:22-23`` - an ``IOException`` prints
  ``File is not found in the ConfigurationReader class`` plus a stack trace and
  lets initialization complete.
* ``ConfigurationReader.java:27-29`` - ``getProperty`` returns ``null`` for an
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

Two standing constraints
------------------------
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
"""

from __future__ import annotations

import ast
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Final

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


def _write_properties(directory: Path, text: str) -> Path:
    """Write ``text`` to ``directory``'s ``configuration.properties``.

    The bytes are encoded with the reader's own default encoding, which is what
    makes the file one the reader will decode back to ``text`` exactly.

    :param directory: The directory to write into - always a ``tmp_path``.
    :param text: The properties text to write.
    :returns: The path written.
    """
    target = directory / PROPERTIES_FILENAME
    target.write_bytes(text.encode(DEFAULT_ENCODING))
    return target


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
# Report o000_d38bb343672e95a9.md finding F01 (MEDIUM, blocking) requires
# app/utils/properties.py to restore the JDK failure semantics for a malformed
# `\uXXXX` escape: "Restore the JDK failure semantics with a fixed,
# non-data-bearing exception."  Measured on Temurin 8u504-b01, JDK 8 raises
# java.lang.IllegalArgumentException("Malformed \uxxxx encoding.") for every
# malformed form - `\u`, `\u12`, `\u12zz`, and a malformed escape in a key -
# and ConfigurationReader's `catch (IOException)` does not catch it.
#
# The settled contract this module asserts is therefore: a fixed,
# non-data-bearing ValueError - Python's analogue of that
# IllegalArgumentException - and no partially decoded value.  The four tests in
# phase 4 that assert it do so unconditionally, against that contract rather
# than against whatever the reader does today: while the production fix is
# outstanding in the unit that owns app/utils/properties.py they fail, and they
# pass the moment it lands.
#
# Nothing here may soften that.  A condition on the reader's own behaviour, an
# expected-failure mark, a swallowed assertion or an "either raises or keeps
# the text" disjunction would all re-evaluate in every future pytest process,
# so a regression to the log-and-keep branch would pass unnoticed - and that
# branch puts up to four raw characters of a key or a value into a WARNING,
# where `password` is one of the six configuration keys.
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
# `c`.  Well-formed decoding passes against the reader as it stands; the
# malformed case is the contract finding F01 settles, and is asserted just as
# unconditionally - see the malformed-escape contract block above.
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
# The malformed `\uXXXX` escape - the contract finding F01 settles.
# The contract, the JDK 8 measurement behind it and why these four tests carry
# no condition of any kind are stated in the section comment above.
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

    This asserts the settled contract: a malformed ``\\uXXXX`` raises a fixed,
    non-data-bearing ``ValueError``, Python's analogue of the
    ``IllegalArgumentException("Malformed \\uxxxx encoding.")`` JDK 8 raises.
    Measured on Temurin 8u504-b01: every form above makes
    ``java.util.Properties.load`` throw it, and ``ConfigurationReader``'s
    ``catch (IOException)`` does not catch it, so inside its static initializer
    it is a hard start-up failure.  Keeping the text literally instead is an
    unauthorized behavioural deviation.

    Restoring that is finding ``F01`` of the review report
    ``o000_d38bb343672e95a9.md`` under
    ``/tmp/blitzy/qa/reports/078e7083-d870-4d30-b966-73c2ed5036ec/README.md/cr/review/``
    - the fix belongs to the unit that owns ``app/utils/properties.py``,
    ``paths-authority-config-and-properties``.  **A failure here means that
    production fix has not landed yet**, not that this expectation is wrong: the
    reader is still logging a fragment of the malformed text and keeping it.
    """
    with pytest.raises(ValueError):
        parse_properties(text)


def test_the_malformed_escape_message_discloses_no_configuration_text() -> None:
    """The exception message carries none of the key or the value.

    This asserts the settled contract: a malformed ``\\uXXXX`` raises a fixed,
    non-data-bearing ``ValueError``, Python's analogue of the
    ``IllegalArgumentException("Malformed \\uxxxx encoding.")`` JDK 8 raises.
    Here it is the *non-data-bearing* half that is under test - a malformed
    escape inside a password, a username or a URL must not put a fragment of it
    into a message or a log record - so the message has to be fixed text that
    names the fault and never the data, and every fragment of this synthetic
    input is asserted absent.  ``\\uxxxx`` itself is not data and is not
    asserted against.

    That is finding ``F01`` of the review report
    ``o000_d38bb343672e95a9.md`` under
    ``/tmp/blitzy/qa/reports/078e7083-d870-4d30-b966-73c2ed5036ec/README.md/cr/review/``
    - the fix belongs to the unit that owns ``app/utils/properties.py``,
    ``paths-authority-config-and-properties``.  **A failure here means that
    production fix has not landed yet**, not that this expectation is wrong.
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

    This asserts the settled contract: a malformed ``\\uXXXX`` raises a fixed,
    non-data-bearing ``ValueError``, Python's analogue of the
    ``IllegalArgumentException("Malformed \\uxxxx encoding.")`` JDK 8 raises.
    The failure therefore replaces the result rather than degrading it, so a
    caller cannot receive a value that is half-decoded and half-literal - and
    the earlier keys on the same text are not delivered either, because the load
    fails as a whole exactly as the JVM's does - and no log record carries the
    malformed text at any level.

    That is finding ``F01`` of the review report
    ``o000_d38bb343672e95a9.md`` under
    ``/tmp/blitzy/qa/reports/078e7083-d870-4d30-b966-73c2ed5036ec/README.md/cr/review/``
    - the fix belongs to the unit that owns ``app/utils/properties.py``,
    ``paths-authority-config-and-properties``.  **A failure here means that
    production fix has not landed yet**, not that this expectation is wrong.
    """
    secret = "p4ssphrase"
    returned: dict[str, str] | None = None

    with caplog.at_level(logging.DEBUG, logger=MODULE_NAME):
        with pytest.raises(ValueError):
            returned = parse_properties(f"first=kept\nblitzy.probe={secret}\\u12zz")

    # Nothing at all came back - not the half-decoded value, and not the
    # well-formed entry that preceded it on an earlier line.
    assert returned is None
    # And no record carries the data either, at any level: the pre-fix
    # behaviour put up to four raw characters of it into a WARNING.
    for record in caplog.records:
        assert secret not in record.getMessage()
        assert "12zz" not in record.getMessage()


def test_load_properties_does_not_swallow_a_malformed_escape(tmp_path: Path) -> None:
    """The failure propagates out of the I/O layer, as on the JVM.

    This asserts the settled contract: a malformed ``\\uXXXX`` raises a fixed,
    non-data-bearing ``ValueError``, Python's analogue of the
    ``IllegalArgumentException("Malformed \\uxxxx encoding.")`` JDK 8 raises.
    :func:`~app.utils.properties.load_properties` catches ``OSError`` and
    nothing else, which is the exact scope of ``ConfigurationReader``'s ``catch
    (IOException)``.  A malformed escape is not an I/O fault, so it must travel
    out to the caller rather than being absorbed into the tolerant empty-mapping
    path - otherwise a corrupt file would read as "no configuration" and the
    fault would surface as a puzzle somewhere else entirely.

    That is finding ``F01`` of the review report
    ``o000_d38bb343672e95a9.md`` under
    ``/tmp/blitzy/qa/reports/078e7083-d870-4d30-b966-73c2ed5036ec/README.md/cr/review/``
    - the fix belongs to the unit that owns ``app/utils/properties.py``,
    ``paths-authority-config-and-properties``.  **A failure here means that
    production fix has not landed yet**, not that this expectation is wrong.
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
    target = tmp_path / PROPERTIES_FILENAME
    target.write_bytes(b"k=caf\xe9\n")

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
    target = tmp_path / PROPERTIES_FILENAME
    target.write_bytes("k=caf\u00e9\n".encode("utf-8"))

    assert load_properties(target) == {"k": "caf\u00c3\u00a9"}


def test_the_encoding_argument_overrides_the_default(tmp_path: Path) -> None:
    """``encoding=`` is honoured, for a caller that knows better.

    The parameter exists for a deliberate, explicit choice; the default remains
    the Java one, and no automatic detection and no fallback is applied.
    """
    target = tmp_path / PROPERTIES_FILENAME
    target.write_bytes("k=caf\u00e9\n".encode("utf-8"))

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
    target = tmp_path / PROPERTIES_FILENAME
    # A NUL leads the value so that no byte of the payload is mistaken for the
    # leading whitespace load0() skips before a value begins.
    target.write_bytes(b"allbytes=\x00" + payload)

    loaded = load_properties(target)

    assert loaded == {"allbytes": "\x00" + payload.decode(DEFAULT_ENCODING)}
    assert len(loaded["allbytes"]) == len(payload) + 1


def test_a_crlf_file_is_parsed(tmp_path: Path) -> None:
    """A file written on Windows reads the same as one written on Unix."""
    target = tmp_path / PROPERTIES_FILENAME
    target.write_bytes(b"a=1\r\nb=2\r\n")

    assert load_properties(target) == {"a": "1", "b": "2"}


def test_an_empty_file_yields_an_empty_mapping(tmp_path: Path) -> None:
    """A present but empty file is a successful load of nothing.

    Distinct from a missing file: it is not logged, and phase 5 asserts below
    that it does not provoke a second load attempt either.
    """
    target = tmp_path / PROPERTIES_FILENAME
    target.write_bytes(b"")

    assert load_properties(target) == {}


# --------------------------------------------------------------------------- #
# Missing-file tolerance - ConfigurationReader:21-24.
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

    ``ConfigurationReader:21-24`` prints the message plus a stack trace and lets
    the static initializer complete, so the port logs at ``WARNING`` with the
    traceback attached and returns an empty mapping.  Nothing raises and nothing
    exits, which is what makes the unit suite, the viewer and ``--dry-run`` all
    work with no configuration present at all.
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

    The third cause.  It is provoked by patching the read rather than by
    ``chmod``: this suite runs as root on its host, where mode ``000`` does not
    make a file unreadable, so a permission test written that way would pass
    without ever reaching the branch.  Patching ``Path.read_bytes`` reaches it,
    and proves the tolerance is ``OSError``-wide rather than specific to
    ``FileNotFoundError``.
    """
    monkeypatch.chdir(tmp_path)
    target = _write_properties(tmp_path, f"{BROWSER_KEY}={SYNTHETIC_VALUE}")
    assert target.exists()

    def _deny(self: Path) -> bytes:
        """Stand in for ``Path.read_bytes`` and refuse the read.

        ``PermissionError`` rather than ``FileNotFoundError`` on purpose: it is
        an ``OSError`` that is *not* the absent-file case, which is what makes
        this test prove the breadth of the tolerance rather than repeat the
        first case.
        """
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "read_bytes", _deny)

    with caplog.at_level(logging.WARNING, logger=MODULE_NAME):
        assert load_properties() == {}

    assert len(_missing_file_records(caplog)) == 1


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

    The port of the static initializer at ``ConfigurationReader:11``.  Every
    call after the first returns the cached mapping **without touching the
    filesystem**, which is asserted directly by counting loads: the cache's
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
    (tmp_path / PROPERTIES_FILENAME).write_bytes(b"# only a comment\n")

    assert dict(get_properties()) == {}
    assert dict(get_properties()) == {}
    assert counted_load.calls == 1


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
# get_property - ConfigurationReader:27-29.
# --------------------------------------------------------------------------- #


def test_get_property_returns_none_for_an_absent_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``getProperty`` returns ``null`` at ``:27-29``; this returns ``None``.

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
