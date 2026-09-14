"""Console-logging configuration for the Testinium-QA Python port.

This module is the single owner of console-logging *configuration* for the
port.  It has no Java counterpart: the technical specification (0.4.1) maps it
as "No source: ... console logging standing in for surefire's output".  In the
Java implementation diagnostic output reached the console two ways -
``System.out.println`` in ``ConfigurationReader``'s static initializer
(``ConfigurationReader.java:22``, which prints exactly ``File is not found in
the ConfigurationReader class``) and maven-surefire's own test output.  The
source ``pom.xml`` declares no logging framework whatsoever, so this module
replaces that surface with configured handlers from the Python standard
library and adds no logging distribution to the pinned dependency set.

Contract: configuration only, never logger acquisition
------------------------------------------------------
Specification 0.4.2 fixes the invariant that ``app/utils`` imports nothing
from the ``app`` package, so ``app/utils/paths.py`` stays importable in a
worker process that never builds a Flask application.  ``app/utils/
properties.py`` nonetheless has to log the missing-configuration-file event
and ``app/reporting/screenshots.py`` has to log suppressed screenshot
failures.  Both obligations are met by keeping this module free of any
``get_logger()`` helper:

* This module installs handlers and levels; that is its whole job.
* **Every other module acquires its logger from the standard library
  directly** - ``logger = logging.getLogger(__name__)``.  That is an import of
  ``logging``, not an import of anything under ``app``, so the invariant
  holds.  A ``get_logger()`` helper here would force ``app/utils/
  properties.py`` to import ``app.logging_config`` and break it.
* Consequently :func:`configure_logging` **is safe to have never been
  called**.  Importing this module has no side effect at all: it installs no
  handler, sets no level and touches no logger.  A module that logs before
  configuration therefore keeps the standard library's own graceful
  degradation - ``logging.lastResort`` emits WARNING and above to ``stderr``
  and drops anything lower, with no "no handlers could be found" complaint.
  Nothing here raises and nothing here imposes an initialization order.

Who calls this, and who must not
--------------------------------
Exactly two process entry points call :func:`configure_logging`:

1. ``app/cli.py`` - at the start of the ``run-tests`` command, before any
   work, so the run's progress and diagnostics are routed correctly.
2. ``app/__init__.py`` - inside ``create_app()``, so the read-only HTTP viewer
   logs consistently.

``app/utils/properties.py``, ``app/reporting/*``, ``app/pages/*``,
``app/automation/*`` and ``features/environment.py`` must **not** call or
import this module.  They call ``logging.getLogger(__name__)`` and nothing
else.

The stream split
----------------
Specification 0.4.1 closes its CLI contract with "Both streams are
line-buffered: progress to stdout, engine diagnostics to stderr", so:

* records at ``INFO`` and below  -> ``stdout``
* records at ``WARNING`` and above -> ``stderr``

That is not the standard library default - a plain ``basicConfig`` sends
everything to ``stderr`` - so it is implemented with two handlers, the stdout
one carrying a filter that excludes ``WARNING`` and above.  The filter is what
guarantees a record is never emitted on both streams.

The handlers name their stream rather than holding it.  Both entry points
configure logging early - ``create_app()`` while an application is being
built, the ``run-tests`` command before the run starts - and a stream object
bound at that moment can be stale by the time a record is emitted, if a host
or a harness has since replaced ``sys.stdout`` or ``sys.stderr``.  The two
handlers therefore look their stream up on :mod:`sys` each time they use it,
so the contract above is about the process's streams as they stand rather than
about whichever objects were installed first.  ``_LiveStreamHandler`` states
the reasoning and keeps the standard handler's degradation for a process that
has no ``sys.stdout`` at all.

Messages whose routing this design exists to guarantee:

===================================================== ======== ========
Event                                                 Level    Stream
===================================================== ======== ========
``configuration.properties`` not found, run continues WARNING  stderr
Screenshot capture failed and was suppressed          ERROR    stderr
Run progress: shard started, scenarios selected,      INFO     stdout
artifacts written
Feature failed to parse; rerun manifest missing or    ERROR    stderr
malformed
Worker process died; incomplete shard named           ERROR    stderr
===================================================== ======== ========

**Logging and the exit contract are independent.**  Exit status is never
derived from whether anything was logged: ``pom.xml:25`` sets
``testFailureIgnore=true`` and all six Jenkins publisher thresholds are
``-1``, so a run that logs errors still exits ``0`` unless it hits one of the
three non-zero classes in specification 0.4.1's exit table.

Multi-process behaviour
-----------------------
``app/services/test_run_service.py`` runs the suite in a process pool
(specification deviation 4).  Each worker is a separate OS process with its
own ``logging`` state, so configuration is per-process: there is deliberately
no cross-process aggregation, no queue handler and no shared log file.
Interleaved worker output on a shared console is expected and is not locked
against.  In particular the missing-configuration-file warning appears **once
per worker process** rather than once per JVM - that is deviation 17
("Configuration is loaded once per worker process rather than once per JVM")
surfacing in the log stream, and it is expected output rather than a defect.

The diagnostic rendering surface
--------------------------------
Owning the console contract means owning what is *allowed onto* the console,
not only which stream it lands on.  Two call sites in the port build log
records out of text the port did not author, and both of them are a log
injection surface (CWE-117) in the plain ``logger.info("%s", value)`` form:

* ``app/cli.py`` renders the caller-supplied ``--browser`` value in the
  "Starting the suite" record.  The value is deliberately unvalidated - an
  unrecognised browser must still fail at first driver use, exactly as
  ``Driver.java``'s missing default branch does (specification 0.4.1's
  ``--browser`` row and 0.6's "Browsers, and the Firefox defect") - so the
  value cannot be rejected, only rendered safely.
* ``app/services/test_run_service.py`` relays each line of a worker
  process's ``stdout`` and ``stderr`` into the parent log.  That text is
  whatever the engine, the driver, a page object or a step printed, and the
  suite's own step phrasing carries substituted ``Examples`` values -
  ``User enters "<username>" username`` [Login.feature:15] - so a child
  diagnostic quoting a step name can carry credentials into the parent log.

Those two modules own their control flow; this module owns the rendering, so
the four functions below are the single implementation of it:

:func:`render_option_value`
    A bounded, control-safe rendering of one option value.  Value-neutral:
    it never rejects or rewrites the value the caller forwards on.
:func:`sanitize_log_text`
    One physical line, with ANSI escapes removed, every control character
    spelled out printably, and the length bounded.  A record built from its
    result cannot be forged, split or used to reprogram a terminal.
:func:`redact_sensitive`
    Credential-shaped content masked, the surrounding diagnostic left
    readable.
:func:`render_worker_line`
    Both of the above plus the child's own severity, so a worker's ``ERROR``
    stays an ``ERROR`` in the parent log instead of flattening to a
    ``WARNING``.

**Redaction applies to log records only, never to an artifact.**
Specification 0.8's test-data note is explicit that the Gherkin ``Examples``
credentials are pre-existing fixture data for an external instance which no
agent may redact, parameterize or rotate: the feature files, the worker
argv and all four report artifacts carry them verbatim, and parity requires
it.  :func:`redact_sensitive` exists because a *log record* is not an
artifact - it is operational output that ends up in a Jenkins console and
its retention is nobody's contract - and it is called from nowhere but the
two log call sites above.

Deliberately absent
-------------------
No file handler, no rotating handler, no JSON or otherwise structured
formatter, no logging-configuration file, and no environment variable or
``configuration.properties`` key that controls logging.  The port's
configuration surface is exactly the six keys of specification 0.4.1 and 0.6
confirms it carries "no additional keys", so a seventh key for a log level is
excluded.  Verbosity is reachable only through the ``verbose`` argument.

Validation assertions for the ``tests/`` agent
----------------------------------------------
This module owns no test file; the assertions its behaviour must satisfy are
stated here so they can be implemented faithfully under ``tests/``:

1. *Import purity* - after a fresh ``import app.logging_config``, the ``app``
   logger has no handlers, its level is ``NOTSET`` and nothing was printed.
2. *Stream split* - with :func:`configure_logging` active, an ``INFO`` record
   lands on stdout and not stderr; ``WARNING`` and ``ERROR`` records land on
   stderr and not stdout.  No record appears on both.
3. *Idempotency* - three calls leave the handler count unchanged after the
   second, and one record produces exactly one line per stream.
4. *Never-configured path* - in a fresh interpreter,
   ``logging.getLogger("app.utils.properties").warning("x")`` with no prior
   call neither raises nor prints a "no handlers" style error.
5. *Verbose* - ``DEBUG`` is suppressed by default and emitted with
   ``verbose=True``.
6. *Hostile streams* - calling :func:`configure_logging` under pytest's
   ``capsys``, where ``sys.stdout`` has no ``reconfigure``, raises nothing.
   The guarantee is stronger than "no ``reconfigure``" and must be asserted
   as such: a stream whose ``reconfigure()`` raises an **arbitrary**
   ``Exception`` - ``RuntimeError``, or a caller-defined subclass the module
   has never heard of - is tolerated too.  :func:`configure_logging` still
   returns ``None``, still installs both handlers, and still leaves the
   stream split intact, because the advisory buffering tweak can never gate
   configuration (specification 0.4.1's exit contract: logging must not
   influence startup or exit status).  The same holds when the internal
   fallback reporter's own stream raises on ``write()``, which is the second
   frame the guarantee has to cover.  ``BaseException`` deliberately still
   propagates: a ``KeyboardInterrupt`` or ``SystemExit`` raised while
   reconfiguring a stream is the operator interrupting the process, not a
   logging problem to absorb.
7. *No forbidden surface* - no **code** in this file constructs a
   ``FileHandler`` or a ``RotatingFileHandler``, reads ``os.environ``,
   references ``configuration.properties``, or imports anything under
   ``app``.  Assert this over the parsed module rather than with a plain
   substring grep: the prose above names each of those things precisely in
   order to prohibit it, so a literal text search matches this docstring.
   ``ast.parse`` the file, walk it, and check the ``Import``/
   ``ImportFrom``/``Attribute``/``Name`` nodes - the only imports are
   ``logging``, ``re``, ``sys`` and ``typing``, besides the ``__future__``
   annotations import every module in the port carries.  ``re`` is there for
   the rendering surface below and is standard library, so the pinned
   dependency set is unchanged.
8. *Branch coverage* - the filter, the idempotency guard, the ``verbose``
   switch, the ``stream_split=False`` path and the guarded reconfigure.
9. *Streams resolved on use* - call :func:`configure_logging`, then replace
   ``sys.stdout`` and ``sys.stderr``, then log: the records must reach the
   *replacements*, on the same split.  This is what keeps the split intact for
   a caller that configures logging early and captures output afterwards -
   including a file-descriptor level capture - and it is asserted for both the
   stream-split and merged configurations.  Cover the degradation too: with
   ``sys.stdout`` set to ``None`` the stdout handler writes to ``sys.stderr``
   rather than raising.
10. *Control stripping* - :func:`sanitize_log_text` returns exactly one
    physical line for an input containing ``\\r\\n``, ``\\n``, NEL
    (``U+0085``), ``U+2028`` and ``U+2029``: ``"\\n" not in result`` and
    ``"\\r" not in result``, and the forged text that followed the break is
    still present as ordinary message text.  ANSI sequences are gone -
    ``"\\x1b[31mred\\x1b[0m"`` renders as ``red`` with no ``\\x1b`` byte
    anywhere - and so are OSC sequences such as ``\\x1b]0;title\\x07``.  Every
    character of the result outside the input's own printable non-ASCII text
    is printable ASCII.
11. *Bounding, and that the bound is the last thing applied* - a
    5,000-character input comes back with its first
    :data:`RELAYED_LINE_LIMIT` characters followed by
    :data:`TRUNCATION_SUFFIX_TEMPLATE` naming the exact number dropped; an
    input at or under the limit is returned unchanged with no suffix;
    ``limit=0`` and a negative limit bound nothing.
    :func:`render_option_value` bounds at :data:`VALUE_RENDER_LIMIT`,
    renders through ``repr`` (so ``render_option_value("chrome")`` is
    ``"'chrome'"``) and is value-neutral - ``render_option_value(None)`` is
    ``"None"``, and no call anywhere mutates or rejects the value that the
    CLI forwards to its workers.  The load-bearing case is a line that
    *grows* under redaction: assert that ``"pwd=a " * 400`` - 2,400
    characters that become far more once every value is replaced by
    :data:`REDACTION_PLACEHOLDER` - comes back from
    :func:`render_worker_line` no longer than
    ``RELAYED_LINE_LIMIT`` plus the suffix.  Bounding before redacting
    satisfies neither this assertion nor the requirement behind it.
12. *Redaction categories* - one assertion each for the seven shapes
    :func:`redact_sensitive` covers: ``key=value``/``key: value`` for every
    keyword in :data:`_CREDENTIAL_KEYWORDS` (which includes ``username``,
    ``user``, ``login``, ``account`` and ``email``, because in this suite an
    account name is half of a credential), URL userinfo, ``Bearer``,
    ``data:`` base64, a 200-plus-character opaque run, a quoted value
    adjacent to a credential keyword in either order, and a bare email
    address with no key in front of it.  Each asserts both halves: the secret
    is gone *and* the surrounding text survives.  Assert **both** evasion
    shapes too, through :func:`render_worker_line`, because a spelled control
    is made of word characters and can therefore defeat a keyword boundary
    from either side: ``"pass\x00word=hunter2"``, where the control splits
    the keyword, and ``"progress\r\npassword=hunter2"``, where the spelling
    welds itself onto the front of one.  Both come back masked.  Assert the
    converse as well - ``"line\twith\ttabs"``, and a line carrying an
    unrelated opaque blob, keep their escape spellings - because the
    de-noised views are consulted for detection and replace the text only
    when they reveal something the ordinary pass missed.

    The negative cases matter as much, and they are a requirement rather
    than a courtesy - AAP 0.8 states that this suite's fixture data is not
    secret and that its presence is not a finding, and the port's own
    diagnostics are what a failing run is read from.  Assert unchanged:
    ``"password not found"`` (no separator); ``'"1,200.00" should be
    displayed'``; and the step-level comparison values the suite prints -
    ``"actualName = Alice Example"``, ``"totalPrice = 1200.00"``,
    ``"expectedWarning = ..."`` - which are the content of an assertion
    diagnostic, not credentials, and which masking would render useless.
13. *Level parsing, and the never-downgrade rule* -
    :func:`render_worker_line` returns ``logging.ERROR`` for
    ``"LOG_ERROR:app.reporting.screenshots: ..."`` with
    ``default_level=logging.WARNING``; ``logging.WARNING`` for an ordinary
    engine line with that same default; ``logging.WARNING`` - never
    ``DEBUG`` - for ``"LOG_DEBUG:..."`` on the stderr stream, because a
    downgrade would move a stderr diagnostic onto stdout and break the
    split asserted in 2; and ``logging.ERROR`` for a ``LOG_ERROR:`` line
    arriving on stdout with ``default_level=logging.INFO``.  ``WARN`` maps to
    ``WARNING`` and ``FATAL`` to ``CRITICAL``.  The token stays in the
    returned text, and the text is sanitized and redacted - a line whose
    level token sits behind an escaped control is *not* honoured, which is
    what stops a child from forging a severity.
"""

from __future__ import annotations

import logging
import re
import sys
from typing import Final, TextIO

__all__ = [
    "LOG_FORMAT",
    "PACKAGE_LOGGER_NAME",
    "REDACTION_PLACEHOLDER",
    "RELAYED_LINE_LIMIT",
    "STDERR_HANDLER_NAME",
    "STDOUT_HANDLER_NAME",
    "TRUNCATION_SUFFIX_TEMPLATE",
    "VALUE_RENDER_LIMIT",
    "configure_logging",
    "redact_sensitive",
    "render_option_value",
    "render_worker_line",
    "sanitize_log_text",
]

#: The logger this module configures.  The ``app`` package logger is used
#: rather than the root logger so that configuring the port never hijacks
#: logging for an application that embeds it.  Exposed as a constant so tests
#: and other modules never need the string literal.
PACKAGE_LOGGER_NAME: Final[str] = "app"

#: Deterministic names for the handlers this module installs, so a test can
#: identify them without reaching for a literal.  ``logging`` also registers
#: named handlers, making them retrievable via ``logging.getHandlerByName``.
STDOUT_HANDLER_NAME: Final[str] = "testinium-qa-stdout"
STDERR_HANDLER_NAME: Final[str] = "testinium-qa-stderr"

#: Concise single-line format for a CI console: level, logger name, message.
#: The logger name is included so a reader can tell a driver message from a
#: report-writer message.  No timestamp is included - a Jenkins console
#: timestamps lines itself, and leaving it out keeps captured output
#: deterministic for the unit suite.  No traceback-expanding formatter is
#: used; exception text arrives from callers via ``exc_info``, which the
#: standard formatter already appends.
LOG_FORMAT: Final[str] = "%(levelname)s %(name)s: %(message)s"

#: Records at this level and above go to stderr; everything below goes to
#: stdout.  This is the boundary specification 0.4.1 fixes.
_STDERR_THRESHOLD: Final[int] = logging.WARNING

#: Character bound applied by :func:`render_option_value` to one CLI option
#: value.  120 characters is generous for the values the port's options
#: actually take - ``chrome``, ``firefox``, a tag expression - while keeping
#: a pathological value from filling a Jenkins console line.  The bound is on
#: the ``repr``, so it counts escape spellings rather than source characters.
VALUE_RENDER_LIMIT: Final[int] = 120

#: Character bound applied by :func:`sanitize_log_text` to one relayed line
#: of worker output.  Sized for the longest legitimate engine diagnostic -
#: a Selenium stack-trace line or a ``NoSuchElementException`` message with
#: a full CSS selector - so real diagnostics arrive whole, while a worker
#: that prints a megabyte of HTML cannot bury the rest of the run's log.
RELAYED_LINE_LIMIT: Final[int] = 2000

#: What replaces credential-shaped content in :func:`redact_sensitive`.  A
#: fixed, obviously-not-a-value marker, chosen so that a reader can tell the
#: difference between "the field was empty" and "the field was masked", and
#: so that no length information about the original leaks.
REDACTION_PLACEHOLDER: Final[str] = "[redacted]"

#: Appended by :func:`sanitize_log_text` when it bounds a value, with
#: ``dropped`` formatted to the exact number of characters removed.  Saying
#: how much was dropped is what keeps a truncated line honest: a reader can
#: tell a complete diagnostic from a clipped one without guessing.
TRUNCATION_SUFFIX_TEMPLATE: Final[str] = "...[+{dropped} char(s) truncated]"

#: Bound for the internal diagnostics :func:`_report_internal_problem` writes
#: to the original stderr.  Tighter than :data:`RELAYED_LINE_LIMIT` because
#: such a line reports that a *stream* is not what it claimed to be, which a
#: short line says as well as a long one, and because the values it renders -
#: a host object's ``repr``, an exception's ``str`` - are the least
#: predictable text this module handles.
_INTERNAL_REPORT_LIMIT: Final[int] = 500

#: Attribute stamped on handlers this module installs.  The idempotency guard
#: removes handlers carrying it and leaves every other handler alone, so a
#: repeated call cannot duplicate output and cannot discard a handler that an
#: embedding application attached to the same logger.
_MANAGED_HANDLER_ATTR: Final[str] = "_testinium_qa_managed"

#: Failures of the advisory ``stream.reconfigure()`` call that are ordinary
#: rather than notable, and are therefore absorbed without a diagnostic:
#: ``OSError`` and ``ValueError`` for a stream already closed or detached,
#: ``TypeError`` for a ``reconfigure`` that does not accept the
#: ``line_buffering`` keyword, and ``AttributeError`` for a wrapper whose
#: ``reconfigure`` reaches through to an object that has none.  Every *other*
#: ``Exception`` is absorbed as well - see :func:`_enable_line_buffering` -
#: but is worth one internal line, so the two cases are distinguished here
#: rather than by widening this tuple.
_EXPECTED_RECONFIGURE_ERRORS: Final[tuple[type[Exception], ...]] = (
    OSError,
    ValueError,
    TypeError,
    AttributeError,
)

# --------------------------------------------------------------------------
# Patterns behind the diagnostic rendering surface.
#
# All of them are compiled once, at import, because :func:`render_worker_line`
# runs on every line of every worker's output - a full suite run relays
# thousands - and ``re``'s internal cache is a fixed-size dict that a busy
# process can evict.  Compiling here also means a malformed pattern fails at
# import rather than in the middle of a run.  Module import stays side-effect
# free: compiling a pattern installs no handler and touches no logger.
# --------------------------------------------------------------------------

#: Terminal escape sequences, removed outright rather than escaped, because
#: their payload is control instructions and not information.  The branches,
#: in match order: CSI (``ESC [`` parameters, intermediates, final byte) which
#: covers colour, cursor movement and line erasure; OSC (``ESC ]`` ... BEL or
#: ST) which can retitle a window; the DCS/SOS/PM/APC string forms; and last
#: the two-character and nF forms such as ``ESC c`` (full terminal reset) and
#: ``ESC ( B``.  A lone ``ESC`` matching none of these is left for
#: :data:`_CONTROL_CHARACTER_PATTERN` to spell out.
_ANSI_ESCAPE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"""
    \x1b \[ [0-?]* [ -/]* [@-~]                 # CSI ... final byte
    | \x1b \] [^\x07\x1b]* (?: \x07 | \x1b\\ )?  # OSC ... BEL or ST
    | \x1b [PX^_] [^\x1b]* (?: \x1b\\ )?         # DCS / SOS / PM / APC
    | \x1b [ -/]* [0-~]                          # two-character and nF forms
    """,
    re.VERBOSE,
)

#: Every character that must not survive into a log record verbatim: the C0
#: controls (``NUL``-``US``, which includes ``CR``, ``LF``, ``TAB`` and a
#: surviving ``ESC``), ``DEL``, the C1 range ``U+0080``-``U+009F`` (whose
#: ``U+0085`` NEL is a line break to ``str.splitlines`` and to some log
#: viewers), and the Unicode line and paragraph separators ``U+2028`` and
#: ``U+2029`` (likewise line breaks to ``str.splitlines``).  Anything else -
#: including the suite's accented French text - is left exactly as it is.
_CONTROL_CHARACTER_PATTERN: Final[re.Pattern[str]] = re.compile(
    "[\x00-\x1f\x7f-\x9f\u2028\u2029]"
)

#: Printable spellings for the control characters that have a conventional
#: one, so a relayed line reads ``a\n b`` rather than ``a\x0a b``.  Every
#: replacement is printable ASCII and none contains a control character, so
#: applying them cannot reintroduce what they replace.  Characters absent
#: from this mapping are spelled ``\xNN``, or ``\uNNNN`` for the two
#: separators, by :func:`_escape_control_character`.
_CONTROL_ESCAPE_SPELLINGS: Final[dict[str, str]] = {
    "\a": r"\a",
    "\b": r"\b",
    "\t": r"\t",
    "\n": r"\n",
    "\v": r"\v",
    "\f": r"\f",
    "\r": r"\r",
    "\x1b": r"\x1b",
    "\x7f": r"\x7f",
    "\u2028": r"\u2028",
    "\u2029": r"\u2029",
}

#: Keys whose *value* is credential-shaped wherever it appears next to an
#: explicit separator.  The set is deliberately closed and spelled out: a
#: heuristic wide enough to catch "anything secret-looking" would redact the
#: diagnostics this port exists to produce.  ``session_id`` and ``cookie``
#: are included because a session identifier is a bearer credential for as
#: long as it lives.
#: ``username`` and its synonyms are here as well as in the adjacency set
#: below, and for the same reason: in this suite an account name *is* half of
#: a credential.  The Examples tables supply email addresses as usernames
#: [Login.feature:22-36], so ``username=salesmanager7@info.com`` in a child
#: diagnostic discloses exactly what ``password=`` would - the pair is the
#: credential, not the password alone.  Masking one and not the other was an
#: inconsistency in this rule set rather than a decision.  Bare ``user`` is
#: still excluded from the *adjacency* form, where "displayed to user" is
#: ordinary prose, but as the left side of an explicit ``=`` or ``:`` it is a
#: field name and is included.
_CREDENTIAL_KEYWORDS: Final[str] = (
    r"(?:passwords?|passwd|pwd|secrets?|tokens?|api[_-]?keys?"
    r"|authorizations?|auth|credentials?|session[_-]?ids?|cookies?"
    r"|usernames?|users?|logins?|accounts?|e?mails?)"
)

#: The same keys plus ``username``, for the adjacency form only.  The suite's
#: own step phrasing is ``User enters "<username>" username``
#: [Login.feature:15], so a substituted step name reaching the parent log
#: carries the account name immediately before the keyword.  Bare ``user`` is
#: deliberately *not* here: ``"Pipeline" should be displayed to user`` is
#: ordinary business phrasing and must stay readable.
_ADJACENT_CREDENTIAL_KEYWORDS: Final[str] = (
    r"(?:usernames?|passwords?|passwd|pwd|secrets?|tokens?|api[_-]?keys?"
    r"|authorizations?|auth|credentials?|session[_-]?ids?|cookies?)"
)

#: A quoted run, in either quote style.  Used as the value shape for both the
#: separator and the adjacency forms.
_QUOTED_VALUE: Final[str] = r"\"[^\"]*\"|'[^']*'"

#: Category (a): ``key=value`` and ``key: value``.  The separator must be an
#: explicit ``=`` or ``:`` - a bare space is *not* accepted, because
#: "password not found" is a diagnostic and not a leak.  The value runs to
#: whitespace, a comma or a semicolon, so ``token=abc, workers=4`` keeps its
#: structure and both values are considered on their own.  An optional
#: ``Bearer`` prefix is part of the value: without it,
#: ``Authorization: Bearer abc`` would redact the word ``Bearer`` and leave
#: the token in the clear.
_KEY_VALUE_CREDENTIAL_PATTERN: Final[re.Pattern[str]] = re.compile(
    rf"(?P<key>\b{_CREDENTIAL_KEYWORDS}\b)"
    rf"(?P<separator>\s*[:=]\s*)"
    rf"(?P<value>{_QUOTED_VALUE}|(?:Bearer\s+)?[^\s,;]+)",
    re.IGNORECASE,
)

#: Category (b): URL userinfo, ``scheme://user:pass@host``.  The whole
#: userinfo is replaced rather than just the password half: the account name
#: is a credential in that position too, and the host - the part an operator
#: needs in order to diagnose a connection - is kept.
_URL_USERINFO_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?P<scheme>[A-Za-z][A-Za-z0-9+.\-]*://)(?P<userinfo>[^/\s:@]+:[^/\s@]*)@"
)

#: Category (c): an ``Authorization: Bearer <token>`` value, which reaches a
#: log through a WebDriver or HTTP client diagnostic rather than through the
#: port's own code.  The scheme word is kept so the line still says what kind
#: of credential was present.
_BEARER_TOKEN_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?P<scheme>\bBearer)\s+(?P<value>[^\s,;]+)", re.IGNORECASE
)

#: Category (d): a base64 ``data:`` URI.  This is the exact shape
#: ``app/reporting/screenshots.py`` produces for an embedded PNG, and a
#: screenshot of a logged-in application is both enormous and confidential,
#: so its payload must never reach a console.  The MIME prefix is kept: "a
#: PNG was here" is the useful half of the diagnostic.
_DATA_URI_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?P<prefix>\bdata:[\w.+-]+/[\w.+-]+;base64,)"
    r"(?P<payload>[A-Za-z0-9+/=]+)",
    re.IGNORECASE,
)

#: Category (e): a long opaque base64-like run with no delimiter of any kind.
#: 200 characters is above anything the suite legitimately logs - the longest
#: real tokens in its output are CSS selectors and XPaths, which carry
#: punctuation and spaces - so the threshold separates "encoded payload" from
#: "long identifier" without needing to know what the payload is.
_OPAQUE_BLOB_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z0-9+/=]{200,}"
)

#: Category (g): an email address anywhere, with or without a label.  It is a
#: category of its own because this suite's account names are email addresses
#: [Login.feature:22-36] and a child diagnostic can carry one with no key in
#: front of it - a step name, an assertion message, a page title read back
#: from the application under test.  Masking it costs a reader nothing that
#: matters: the shape "an address was here" survives, and which address it
#: was is never the reason an engine diagnostic is being read.  The local part
#: deliberately excludes quotes and angle brackets so surrounding punctuation
#: is preserved rather than swallowed.
_EMAIL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[^\s<>\"'()\[\],;:@]+@[A-Za-z0-9]"
    r"(?:[A-Za-z0-9-]*[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+"
)

#: The printable spellings :func:`_control_safe` produces, as *text*.  Deleting
#: them yields the de-noised view :func:`_redact_worker_text` tests, which is
#: what closes the evasion a control character inside a keyword would
#: otherwise open: ``pass\x00word=hunter2`` control-spells to text no
#: credential pattern matches, because the escape sits between the letters of
#: the keyword.  Removing the spellings restores ``password=hunter2``, and the
#: masked de-noised line is what gets relayed.
_ESCAPE_SPELLING_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\\(?:x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|[abefnrtv])"
)

#: What an escape spelling is replaced by to build a de-noised view, and both
#: entries are needed because a spelling is made of *word* characters
#: (``\\n`` is a backslash and a letter), so it can defeat the ``\\b`` anchors
#: in the credential patterns from either side:
#:
#: * ``" "`` restores a boundary the spelling swallowed - in
#:   ``"progress\\r\\npassword=hunter2"`` the keyword is preceded by the ``n``
#:   of ``\\n``, so ``\\bpassword`` does not match until the spelling becomes
#:   a space.
#: * ``""`` rejoins a keyword the spelling split - ``"pass\\x00word=hunter2"``
#:   only reads as ``password=`` once the spelling is gone entirely.
#:
#: Neither view alone covers both shapes, so :func:`_redact_worker_text`
#: tries them in this order: the separating view first, because a line that
#: merely contains a control is far commoner than one whose keyword a control
#: splits.
_DENOISE_REPLACEMENTS: Final[tuple[str, ...]] = (" ", "")

#: Category (f), value first: ``"someone@example.com" username``.
_QUOTED_BEFORE_KEYWORD_PATTERN: Final[re.Pattern[str]] = re.compile(
    rf"(?P<value>{_QUOTED_VALUE})"
    rf"(?P<gap>\s+)"
    rf"(?P<key>\b{_ADJACENT_CREDENTIAL_KEYWORDS}\b)",
    re.IGNORECASE,
)

#: Category (f), keyword first: ``password "hunter2"``.
_KEYWORD_BEFORE_QUOTED_PATTERN: Final[re.Pattern[str]] = re.compile(
    rf"(?P<key>\b{_ADJACENT_CREDENTIAL_KEYWORDS}\b)"
    rf"(?P<gap>\s+)"
    rf"(?P<value>{_QUOTED_VALUE})",
    re.IGNORECASE,
)

#: The redaction rules of :func:`redact_sensitive`, as ``(pattern,
#: replacement)`` pairs applied in sequence.  **The order is load-bearing**
#: and is the reason they are a tuple here rather than a set of separate
#: calls inside the function:
#:
#: * the ``data:`` URI and URL-userinfo forms come first, because both embed
#:   a ``:`` that the ``key: value`` rule would otherwise cut through,
#:   redacting a fragment and leaving the payload;
#: * the ``key=value`` rule comes next and consumes an ``Authorization:
#:   Bearer …`` value whole, with the standalone ``Bearer`` rule following to
#:   catch a token that no key introduced;
#: * the two adjacency rules come after all of those, by which point a value
#:   already masked is the unquoted placeholder and no longer looks like a
#:   quoted value to them;
#: * the opaque-blob rule comes last, so it sees only runs that none of the
#:   structural rules claimed.
#:
#: Every replacement keeps its match's identifying context - the key, the
#: scheme, the MIME prefix - and replaces only the secret, which is what
#: leaves the surrounding diagnostic readable.
_REDACTION_RULES: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    (_DATA_URI_PATTERN, rf"\g<prefix>{REDACTION_PLACEHOLDER}"),
    (_URL_USERINFO_PATTERN, rf"\g<scheme>{REDACTION_PLACEHOLDER}@"),
    (
        _KEY_VALUE_CREDENTIAL_PATTERN,
        rf"\g<key>\g<separator>{REDACTION_PLACEHOLDER}",
    ),
    (_BEARER_TOKEN_PATTERN, rf"\g<scheme> {REDACTION_PLACEHOLDER}"),
    (
        _QUOTED_BEFORE_KEYWORD_PATTERN,
        rf"{REDACTION_PLACEHOLDER}\g<gap>\g<key>",
    ),
    (
        _KEYWORD_BEFORE_QUOTED_PATTERN,
        rf"\g<key>\g<gap>{REDACTION_PLACEHOLDER}",
    ),
    (_OPAQUE_BLOB_PATTERN, REDACTION_PLACEHOLDER),
    (_EMAIL_PATTERN, REDACTION_PLACEHOLDER),
)

#: The optional level token :func:`render_worker_line` parses off the front of
#: a relayed line.  Shaped for what behave actually emits: its default
#: ``logging_format`` is ``"LOG_%(levelname)s:%(name)s: %(message)s"``
#: (measured against behave 1.3.3), installed through ``basicConfig``, so a
#: child record arrives as ``LOG_ERROR:app.reporting.screenshots: ...`` at
#: column 0.  The ``LOG_`` prefix is optional and the terminator may be a
#: colon or whitespace, which also accepts this module's own
#: :data:`LOG_FORMAT` (``"%(levelname)s %(name)s: %(message)s"``) - the shape
#: a worker's records take once it has called :func:`configure_logging`.
#: Anchored at the start, so a token further along a line is message text and
#: cannot influence severity.
_WORKER_LEVEL_TOKEN_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^(?:LOG_)?(?P<level>DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL|FATAL)"
    r"(?=:|\s|$)",
    re.IGNORECASE,
)

#: Level names accepted in that token, mapped to their numeric level.
#: ``WARN`` and ``FATAL`` are the aliases the standard library still accepts
#: from ``getLevelName`` and that third-party emitters use; they collapse onto
#: ``WARNING`` and ``CRITICAL`` so the parent never invents a level number
#: the stream split does not know how to route.
_WORKER_LEVEL_NAMES: Final[dict[str, int]] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARN": logging.WARNING,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
    "FATAL": logging.CRITICAL,
}


class _MaxLevelFilter(logging.Filter):
    """Reject records at or above ``exclusive_maximum``.

    Attached to the stdout handler with the ``WARNING`` boundary, this is what
    keeps a warning or an error off stdout when it has already been routed to
    stderr.  Without it both handlers would accept the record and every
    warning would appear twice.
    """

    def __init__(self, exclusive_maximum: int) -> None:
        super().__init__()
        self.exclusive_maximum = exclusive_maximum

    def filter(self, record: logging.LogRecord) -> bool:
        """Return ``True`` when ``record`` belongs on the lower stream."""
        return record.levelno < self.exclusive_maximum


def _safe_repr(value: object) -> str:
    """Render ``value`` for a diagnostic without ever raising.

    ``repr`` runs user code for any object that defines ``__repr__``, so
    describing an object can itself fail.  Both groups of callers need that
    impossible:

    * The guards below are already on a failure path, and the object they
      describe is by definition one that has just misbehaved - a stream or a
      handler supplied by a host, a harness or a third-party library.  An
      exception escaping from the diagnostic would reinstate exactly the
      defect those guards exist to remove: configuration aborting because
      something advisory went wrong.
    * :func:`render_option_value` renders a caller-supplied value, and
      :func:`sanitize_log_text` falls back here for a non-``str`` argument.
      A log record must not be the thing that fails a run.

    The fallback names the type instead, and naming the type is itself
    guarded because a custom metaclass can make even
    ``type(value).__name__`` raise.

    Args:
        value: Any object, however hostile.

    Returns:
        ``repr(value)`` when that succeeds, otherwise
        ``"<unrepresentable ...>"``.  Never raises for any ``Exception``;
        ``BaseException`` still propagates, so an interrupt is not absorbed.
    """
    try:
        return repr(value)
    except Exception:
        try:
            return f"<unrepresentable {type(value).__name__}>"
        except Exception:
            return "<unrepresentable object>"


def _describe_exception(exc: BaseException) -> str:
    """Render ``exc`` as ``Type: message`` without ever raising.

    ``str(exc)`` runs user code - an exception class is free to define
    ``__str__`` - so formatting a caught exception is itself a call that can
    fail.  Guarding it here keeps the reporting path total, which is what
    lets the callers below promise that no exception from an advisory
    operation reaches :func:`configure_logging`'s caller.  The type name is
    always included, because for an empty-message exception such as a bare
    ``RuntimeError()`` the type is the whole of the diagnostic.

    Args:
        exc: The caught exception to describe.

    Returns:
        ``"Type: message"``, or just ``"Type"`` when the exception carries no
        message.  Never raises for any ``Exception`` raised by the
        exception's own ``__str__``.
    """
    try:
        name = type(exc).__name__
    except Exception:
        name = "exception"
    try:
        detail = str(exc)
    except Exception:
        detail = "<unprintable>"
    return f"{name}: {detail}" if detail else name


def _report_internal_problem(message: str) -> None:
    """Surface a problem raised by the logging machinery itself.

    A failure *inside* logging configuration cannot be reported through a
    logger without risking recursion, so it is written straight to the
    interpreter's original stderr.  The report is gated on
    ``logging.raiseExceptions``, which is the standard library's own switch
    for whether internal logging problems are surfaced or silently absorbed;
    honouring it means a caller that has already opted out of logging noise
    does not get any from this module either.  This function never raises: a
    diagnostic that cannot be written is not worth failing a test run over.

    **The message is rendered before it is written, and that is not
    optional.**  Its callers assemble it from values this module does not
    control - a host stream's ``repr``, an exception's ``str`` - both of
    which run code supplied by whoever installed that stream and may legally
    return embedded newlines, terminal escape sequences or unbounded text.
    Writing such a value straight to the console is the log-forging defect
    this module exists to prevent one layer further out: a newline in an
    exception message would produce a second, unprefixed physical line
    indistinguishable from an independent record.  The whole assembled line
    therefore goes through the same control-safe, redacted, bounded rendering
    the relay uses, so this path cannot forge a record either.  The rendering
    is pure text manipulation over compiled patterns, logs nothing and
    acquires no logger, so it cannot recurse back into here.
    """
    if not logging.raiseExceptions:
        return
    stream = sys.__stderr__ if sys.__stderr__ is not None else sys.stderr
    if stream is None:
        return
    try:
        safe_message = _bound_text(
            _redact_worker_text(_control_safe(message)),
            _INTERNAL_REPORT_LIMIT,
        )
        stream.write(f"{__name__}: {safe_message}\n")
        stream.flush()
    except Exception:
        # The original stderr is closed, detached, or a host-supplied object
        # whose ``write``/``flush`` fails in a way this module cannot
        # enumerate - a daemonised or embedded process, or a capture stream
        # that raises ``RuntimeError`` once its test has finished.  The catch
        # is deliberately as wide as ``Exception`` and not a tuple of
        # implementation-specific types: this is the *fallback* reporter, so
        # an exception escaping here would abort the very configuration its
        # callers are reporting a tolerated failure from, moving the defect
        # one frame out instead of fixing it.  ``BaseException`` still
        # propagates, so an interrupt is never swallowed.  There is nowhere
        # left to report to, and losing a diagnostic line must not propagate
        # into the caller.
        return


def _enable_line_buffering(stream: TextIO | None) -> bool:
    """Put ``stream`` in line-buffered mode, reporting whether it took effect.

    ``StreamHandler`` flushes after every ``emit()``, so records themselves
    already reach the console one line at a time.  What this adds is that the
    *underlying* stream stays line-buffered when the process is a CLI run
    whose output Jenkins captures through a pipe - the case where Python would
    otherwise switch stdout to block buffering and hold progress lines back
    until the buffer filled.

    The call is fully guarded and is a deliberate no-op on any stream that
    does not support it - which includes the replacement objects pytest's
    ``capsys`` installs, and equally a stream whose ``reconfigure()`` raises
    *any* ``Exception``, of any type, known to this module or not.  Returning
    a bool rather than raising makes "this stream cannot be reconfigured" an
    ordinary handled outcome: it is normal, not an error.

    The width of the guard is the point, not an oversight.  Buffering mode is
    an optimisation for pipe capture and never a correctness requirement,
    while both callers of :func:`configure_logging` are process entry points -
    ``create_app()`` and the ``run-tests`` command - so an exception escaping
    from here would stop an application being built or a suite being run.
    Specification 0.4.1's exit contract derives status from the run, never
    from logging, and the module docstring's promise that "nothing here
    raises" has to hold against streams this module did not create: a host, a
    WSGI server, a CI wrapper or a test harness can install anything that
    answers to ``write``.  ``Exception`` is therefore caught and
    ``BaseException`` is not, so ``KeyboardInterrupt`` and ``SystemExit``
    still reach the caller as the interruption they are.

    Two outcomes are distinguished, because a guard that reports nothing
    hides host misconfiguration while a guard that reports everything makes
    pytest runs noisy:

    * **Documented-normal** - no ``reconfigure`` attribute at all, or a
      ``reconfigure`` that rejects the ``line_buffering`` keyword, or a
      stream already closed or detached.  Silent; these are expected on
      ordinary hosts and under ``capsys``.
    * **Unexpected** - anything else, ``RuntimeError`` being the case the
      review found.  Reported once through :func:`_report_internal_problem`,
      which is non-raising and gated on ``logging.raiseExceptions``, and then
      treated exactly like the normal case.

    Args:
        stream: The stream to switch into line-buffered mode, or ``None``
            when the process has no such stream (a Windows GUI host).

    Returns:
        ``True`` only when ``reconfigure(line_buffering=True)`` completed;
        ``False`` for a missing, absent-method or failing stream.  The result
        is advisory - no caller gates configuration on it.
    """
    if stream is None:
        return False
    reconfigure = getattr(stream, "reconfigure", None)
    if not callable(reconfigure):
        return False
    try:
        reconfigure(line_buffering=True)
    except _EXPECTED_RECONFIGURE_ERRORS:
        # Detached, closed, or a custom stream whose reconfigure does not
        # accept line_buffering.  Buffering is an optimisation for pipe
        # capture, never a correctness requirement, so the run continues with
        # the stream exactly as it was.
        return False
    except Exception as exc:
        # A stream that advertises ``reconfigure`` and then fails in some
        # other way - the reviewed defect, where a ``RuntimeError`` escaped
        # and aborted startup.  Worth one internal line because it says the
        # host's stream is not what it claimed to be; never worth failing the
        # run over, so configuration continues with the stream untouched.
        _report_internal_problem(
            f"could not enable line buffering on {_safe_repr(stream)}: "
            f"{_describe_exception(exc)}"
        )
        return False
    return True


def _discard_managed_handlers(logger: logging.Logger) -> None:
    """Remove the handlers a previous :func:`configure_logging` installed.

    This is the idempotency guard.  Only handlers stamped with
    :data:`_MANAGED_HANDLER_ATTR` are touched, so handlers belonging to an
    embedding application survive.  Removal happens *before* the replacements
    are built: ``Handler.close()`` unregisters a handler from the module-level
    name registry, so closing an old handler after creating its same-named
    replacement would evict the new one from that registry.
    """
    for handler in [
        candidate
        for candidate in logger.handlers
        if getattr(candidate, _MANAGED_HANDLER_ATTR, False)
    ]:
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception as exc:
            # Closing a StreamHandler does not close its stream, so this is
            # very nearly unreachable for the handlers this module builds; if
            # the internal lock or registry is in a bad state the handler is
            # already detached from the logger and will be collected, so the
            # reconfiguration still succeeds.  The catch is ``Exception``
            # rather than a tuple of expected types because the attribute
            # that selects these handlers is public enough for a third-party
            # or hostile handler to carry: whatever such a handler's
            # ``close()`` raises, it must not be able to abort
            # reconfiguration, and the handler is off the logger by the time
            # it runs.  ``BaseException`` still propagates.
            _report_internal_problem(
                f"could not close handler {_safe_repr(handler)}: "
                f"{_describe_exception(exc)}"
            )


class _LiveStreamHandler(logging.StreamHandler):
    """A ``StreamHandler`` that resolves ``sys.stdout``/``sys.stderr`` on use.

    The standard ``StreamHandler`` binds a stream *object* once, when it is
    constructed.  That is wrong for this module, because the two entry points
    that call :func:`configure_logging` do so long before most records are
    emitted: ``create_app()`` configures logging while building an
    application, and the ``run-tests`` command configures it before the run
    starts.  Any code that legitimately replaces a process stream after that
    point - a WSGI host redirecting diagnostics, a harness capturing output,
    a wrapper that re-points ``stderr`` - would find records still going to
    the object that was current at configuration time, which by then may be
    stale or discarded.  The published contract is about *streams*, not about
    whichever object happened to be installed first: specification 0.4.1
    fixes "progress to stdout, engine diagnostics to stderr", so the stream
    is looked up by name each time it is used and the contract holds however
    a host has arranged those two streams.

    Resolution is by attribute name on :mod:`sys`, and the degradation path
    of the standard handler is preserved: a process can genuinely have no
    ``sys.stdout`` - a Windows GUI host is the usual case, and the port must
    run on Windows as well as Linux and macOS - and in that case output falls
    back to ``sys.stderr``, collapsing the split into one merged stream rather
    than failing the run.  Should both be absent, the stream bound at
    construction is used, and if that is absent too the handler behaves
    exactly as the standard one does: ``logging`` reports the emit failure
    through its own ``handleError`` path and the run continues.

    Nothing in the port calls ``setStream()``; it remains functional and its
    argument becomes the last-resort stream described above.
    """

    def __init__(self, stream_attribute: str) -> None:
        # Set before the base constructor, which assigns ``self.stream`` and
        # therefore reaches the property setter below.
        self._stream_attribute = stream_attribute
        self._fallback_stream: TextIO | None = None
        super().__init__(getattr(sys, stream_attribute, None))

    @property
    def stream(self) -> TextIO | None:
        """The stream to write to, resolved now rather than at construction."""
        live: TextIO | None = getattr(sys, self._stream_attribute, None)
        if live is None:
            # The documented degradation: merge onto the error stream.
            live = sys.stderr
        if live is None:
            live = self._fallback_stream
        return live

    @stream.setter
    def stream(self, value: TextIO | None) -> None:
        """Record ``value`` as the last-resort stream.

        Invoked by the base constructor and by ``setStream()``.  The value is
        deliberately not made authoritative: doing so would restore exactly
        the early-binding behaviour this class exists to avoid.
        """
        self._fallback_stream = value


def _build_handler(
    stream_attribute: str,
    name: str,
    level: int,
    formatter: logging.Formatter,
) -> logging.StreamHandler:
    """Build one tagged, formatted stream handler.

    ``stream_attribute`` names the attribute of :mod:`sys` to write to -
    ``"stdout"`` or ``"stderr"`` - rather than supplying a stream object, so
    the handler resolves it on use.  :class:`_LiveStreamHandler` explains why
    that matters and how an absent stream degrades.
    """
    handler = _LiveStreamHandler(stream_attribute)
    handler.set_name(name)
    handler.setLevel(level)
    handler.setFormatter(formatter)
    setattr(handler, _MANAGED_HANDLER_ATTR, True)
    return handler


def configure_logging(
    *,
    verbose: bool = False,
    stream_split: bool = True,
) -> None:
    """Install console logging for the ``app`` package logger.

    Called by exactly two entry points - ``app/cli.py`` at the start of the
    ``run-tests`` command and ``app/__init__.py`` inside ``create_app()``.
    Every other module in the port acquires a logger with
    ``logging.getLogger(__name__)`` and never calls this function; see the
    module docstring for why that split exists.

    The function is idempotent.  Calling it repeatedly replaces the handlers
    it installed previously instead of appending to them, so a process that
    builds several Flask applications - the unit suite does exactly that -
    never emits a record twice.  It has no effect on the root logger and no
    effect on handlers this module did not install.

    Args:
        verbose: When ``True`` the threshold drops to ``DEBUG``; the default
            threshold is ``INFO``.  This is the only verbosity control in the
            port - no environment variable and no configuration key affects
            logging, because the configuration surface is fixed at six keys.
        stream_split: When ``True`` (the default) records at ``INFO`` and
            below are written to ``stdout`` and records at ``WARNING`` and
            above to ``stderr``, which is the published CLI contract.  When
            ``False`` every record is written to ``stderr`` on a single
            handler, matching the standard library's own default for a caller
            that wants one merged stream.

    Returns:
        ``None``.  Configuration is applied as a side effect on the ``app``
        logger; nothing is returned for a caller to hold or to tear down.

    """
    logger = logging.getLogger(PACKAGE_LOGGER_NAME)

    # Both process streams are line-buffered regardless of stream_split: the
    # contract in specification 0.4.1 covers both streams, and progress
    # written by click alongside these handlers must not be held in a block
    # buffer either.  Applied before the handlers are built so they attach to
    # streams already in their final mode; both calls are fully guarded and
    # their outcome is advisory, so neither result gates configuration.
    _enable_line_buffering(sys.stdout)
    _enable_line_buffering(sys.stderr)

    # Discard first, build second - see _discard_managed_handlers.
    _discard_managed_handlers(logger)

    formatter = logging.Formatter(LOG_FORMAT)

    if stream_split:
        # The stdout handler takes every record the logger admits and then
        # rejects WARNING and above through the filter, which is what makes
        # the two handlers partition the records rather than overlap.
        stdout_handler = _build_handler(
            "stdout", STDOUT_HANDLER_NAME, logging.NOTSET, formatter
        )
        stdout_handler.addFilter(_MaxLevelFilter(_STDERR_THRESHOLD))
        stderr_handler = _build_handler(
            "stderr", STDERR_HANDLER_NAME, _STDERR_THRESHOLD, formatter
        )
        handlers: tuple[logging.StreamHandler, ...] = (
            stdout_handler,
            stderr_handler,
        )
    else:
        handlers = (
            _build_handler(
                "stderr", STDERR_HANDLER_NAME, logging.NOTSET, formatter
            ),
        )

    for handler in handlers:
        logger.addHandler(handler)

    logger.setLevel(logging.DEBUG if verbose else logging.INFO)

    # Stop records reaching the root logger's handlers as well.  Without this
    # a host application that called basicConfig() would print every record
    # from the port a second time, and on stderr, breaking the stream split.
    logger.propagate = False


# --------------------------------------------------------------------------
# The diagnostic rendering surface.
#
# Everything below is pure: it reads no module state, installs nothing, and
# is safe to call from a worker process that never configured logging.  The
# module docstring's "The diagnostic rendering surface" section names the two
# consumer call sites and explains why the rendering lives here rather than
# in either of them.
# --------------------------------------------------------------------------


def _escape_control_character(match: re.Match[str]) -> str:
    """Return a printable spelling for one matched control character.

    The substitution callback for :data:`_CONTROL_CHARACTER_PATTERN`.
    Conventional spellings come from :data:`_CONTROL_ESCAPE_SPELLINGS`;
    anything else is rendered numerically - ``\\xNN`` for a single-byte code
    point, ``\\uNNNN`` for the two Unicode separators - which is the same
    notation Python's own ``repr`` uses, so a reader needs no new convention
    to decode it.

    Args:
        match: The match produced by :data:`_CONTROL_CHARACTER_PATTERN`; its
            group 0 is always exactly one character.

    Returns:
        Printable ASCII text containing no control character, so the result
        cannot reintroduce what it replaced.
    """
    character = match.group(0)
    spelled = _CONTROL_ESCAPE_SPELLINGS.get(character)
    if spelled is not None:
        return spelled
    code_point = ord(character)
    if code_point <= 0xFF:
        return f"\\x{code_point:02x}"
    return f"\\u{code_point:04x}"


def _control_safe(text: object) -> str:
    """Render ``text`` as one physical line with no control character left.

    The first of the two halves :func:`sanitize_log_text` is built from, and
    separated from the second so that callers which transform the text
    further - :func:`render_worker_line` redacts after this runs - can apply
    the length bound *last*, once nothing can grow the result any more.

    Args:
        text: Any object.  A ``str`` is used as it is; anything else is
            rendered through :func:`_safe_repr` first, so the function cannot
            raise on a hostile ``__str__``.

    Returns:
        Printable text of unbounded length: terminal escape sequences
        deleted, every control character, ``DEL``, C1 code point and Unicode
        line or paragraph separator replaced by a printable spelling.  The
        result contains no line break, so it can never become two log lines.
    """
    rendered = text if isinstance(text, str) else _safe_repr(text)
    rendered = _ANSI_ESCAPE_PATTERN.sub("", rendered)
    return _CONTROL_CHARACTER_PATTERN.sub(_escape_control_character, rendered)


def _bound_text(text: str, limit: int) -> str:
    """Cut ``text`` to ``limit`` characters, naming what was dropped.

    The second half of :func:`sanitize_log_text`, and deliberately the *last*
    transformation any caller applies: a bound taken before a substitution
    that can lengthen the text is not a bound at all.  That ordering is why
    this is a function rather than three lines inside the sanitizer -
    :func:`render_worker_line` redacts between the two halves, and redaction
    replaces a short secret with a longer placeholder, so a run of them can
    grow a line well past a bound applied earlier.

    Args:
        text: Already control-safe text.
        limit: Maximum number of characters to keep, or ``0`` or less for no
            bound at all.

    Returns:
        ``text`` unchanged when it fits or when ``limit`` is not positive;
        otherwise its first ``limit`` characters followed by
        :data:`TRUNCATION_SUFFIX_TEMPLATE` naming the exact number of
        characters dropped, so a reader can tell truncation from a line that
        merely ended.
    """
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit] + TRUNCATION_SUFFIX_TEMPLATE.format(
        dropped=len(text) - limit
    )


def sanitize_log_text(text: str, *, limit: int = RELAYED_LINE_LIMIT) -> str:
    """Render ``text`` as one bounded, control-safe physical line.

    This is the port's answer to log injection (CWE-117).  A log record built
    with ``logger.info("%s", untrusted)`` is a record the untrusted text gets
    to *shape*: a newline inside it starts what looks like a new record, so a
    worker or a caller can forge ``ERROR app.services: run failed`` on a line
    of its own, and a CSI sequence inside it can recolour, erase or overwrite
    what a console has already printed.  Both are removed here, once, rather
    than trusted to each call site.

    Three transformations, in this order:

    1. **Terminal escape sequences are deleted** - see
       :data:`_ANSI_ESCAPE_PATTERN`.  They are removed rather than spelled
       out because their content is instructions to a terminal, not
       information about the run; keeping ``\\x1b[31m`` in the text would only
       make a diagnostic harder to read.
    2. **Every remaining control character is spelled out printably** - the
       C0 range, ``DEL``, the C1 range (whose ``U+0085`` NEL is a line break
       to ``str.splitlines`` and to many log viewers) and ``U+2028``/
       ``U+2029``.  ``CR``, ``LF``, NEL, LS and PS therefore leave this
       function as the *text* ``\\r``, ``\\n``, ``\\x85``, ``\\u2028`` and
       ``\\u2029``, which is what makes the "one physical line" guarantee
       hold: no break of any kind survives, and the text that followed a
       break is still readable as part of the same record.
    3. **The result is bounded** to ``limit`` characters, with
       :data:`TRUNCATION_SUFFIX_TEMPLATE` naming exactly how many characters
       were dropped.  Escaping happens first so the bound applies to what
       will actually be printed.

    Printable non-ASCII text is untouched - the suite asserts on French
    strings such as ``Veuillez renseigner ce champ.`` [Login.feature:89], and
    a sanitizer that mangled them would make those diagnostics useless.

    Args:
        text: The text to render.  A non-``str`` argument is rendered through
            ``repr`` rather than rejected: this function is called from
            diagnostic paths, where raising on unexpected input would lose
            the very message being reported.
        limit: Maximum number of characters to keep, counted after escaping.
            The default is :data:`RELAYED_LINE_LIMIT`.  ``0`` or a negative
            value means no bound, for a caller that has already bounded its
            input or genuinely needs the whole of it.

    Returns:
        One physical line of text.  It contains no line break of any kind, no
        escape sequence and no control character, and its length is at most
        ``limit`` plus the length of the truncation notice.

    Examples:
        >>> sanitize_log_text("a\\r\\nFORGED ERROR: x")
        'a\\\\r\\\\nFORGED ERROR: x'
        >>> sanitize_log_text("\\x1b[31mred\\x1b[0m")
        'red'
        >>> sanitize_log_text("abcdef", limit=3)
        'abc...[+3 char(s) truncated]'
    """
    return _bound_text(_control_safe(text), limit)


def render_option_value(
    value: object, *, limit: int = VALUE_RENDER_LIMIT
) -> str:
    """Render one caller-supplied option value for a log record.

    Built for ``app/cli.py``'s "Starting the suite" record, whose ``browser``
    field is whatever the caller typed after ``--browser``.  ``repr`` comes
    first for two reasons: it quotes a string, so an empty or whitespace-only
    value is visibly present rather than invisible in the middle of a
    sentence, and it already escapes a string's control characters, so the
    sanitizing pass that follows is a second line of defence rather than the
    only one.  The sanitizing pass is still applied, because ``repr`` runs
    user code for any object that defines ``__repr__`` and a custom one can
    return anything at all.

    **The function is value-neutral, and that is a requirement rather than a
    convenience.** It neither validates nor rewrites ``value``, and the
    caller forwards the *original* value to its workers unchanged.
    ``Driver.java``'s browser switch has no default branch, so an
    unrecognised browser must still reach the driver and fail at first use
    exactly as it does today (specification 0.4.1's ``--browser`` row, and
    0.6 under "Browsers, and the Firefox defect").  Rendering a value safely
    and rejecting it are different jobs; only the first belongs here.

    Args:
        value: Any object, including ``None`` for an option the caller did
            not supply.  Not mutated, not validated, not consumed.
        limit: Maximum number of characters to keep, defaulting to
            :data:`VALUE_RENDER_LIMIT`.  Passed through to
            :func:`sanitize_log_text`, so ``0`` or less means no bound.

    Returns:
        A bounded, single-line, control-safe rendering, suitable as a ``%s``
        argument.  ``None`` renders as ``'None'`` - the option's absence is
        itself information, and the caller does not need a special case for
        it.

    Examples:
        >>> render_option_value("chrome")
        "'chrome'"
        >>> render_option_value(None)
        'None'
        >>> render_option_value("chrome\\nERROR forged")
        "'chrome\\\\nERROR forged'"
    """
    return sanitize_log_text(_safe_repr(value), limit=limit)


def redact_sensitive(text: str) -> str:
    """Mask credential-shaped content in text bound for a log record.

    **Scope limit, which is a specification constraint and not a preference:
    this function is for text destined for a LOG RECORD only, and it is never
    applied to an artifact.** Specification 0.8's test-data note states that
    the Gherkin ``Examples`` credentials are pre-existing fixture data for an
    external test instance, that no agent may redact, parameterize or rotate
    them, and that their presence is not a finding.  The feature files, the
    ``-D browser=…`` worker argv and all four report artifacts -
    the JSON report, the rerun manifest, the self-contained page and the
    report tree, each named by :mod:`app.utils.paths` - therefore
    carry them verbatim, because parity requires it.  A log record is a
    different thing: it is operational output that lands in a CI console
    whose retention and audience nobody has specified, and the only reason
    credentials reach it at all is that a *diagnostic about* a step quotes
    that step's substituted name.  Masking there loses nothing a reader
    needs.

    Six shapes are covered, applied in this order and case-insensitively:

    a. ``key=value`` and ``key: value`` for the keywords in
       :data:`_CREDENTIAL_KEYWORDS`.  The key stays visible and the value is
       replaced, so the line still says *which* field was present.  The
       separator must be an explicit ``=`` or ``:``; a bare space is not
       accepted, which is what keeps ordinary prose such as "password not
       found" intact.  An ``Authorization: Bearer <token>`` value is
       consumed whole, so the scheme word cannot be mistaken for the value.
    b. URL userinfo - ``scheme://user:pass@host`` becomes
       ``scheme://[redacted]@host``, keeping the scheme and host an operator
       needs to diagnose a connection.
    c. A standalone ``Bearer <token>``, for the case where no key preceded
       it.
    d. A base64 ``data:`` URI - the shape
       ``app/reporting/screenshots.py`` produces for an embedded PNG.  The
       MIME prefix is kept and the payload is replaced: image bytes must
       never reach a log, and "a PNG was here" is the useful half.
    e. A run of 200 or more base64-like characters with no delimiter, which
       is an encoded payload rather than an identifier at that length.
    f. A quoted value immediately adjacent to a credential keyword, in
       either order - ``"someone@example.com" username`` and ``password
       "hunter2"``.  This is the exact shape of this suite's own step
       phrasing, ``User enters "<username>" username``
       [Login.feature:15-16], whose substituted ``Examples`` values would
       otherwise reach the parent log through a child diagnostic that quotes
       the step name.

    Over-redaction is a failure mode too, and is guarded against
    deliberately: the keyword sets are closed, the separator forms are
    explicit, bare ``user`` is excluded from the adjacency set, and no
    entropy or length heuristic is applied below 200 characters.  A
    diagnostic that says nothing is as useless as one that leaks - the CRM
    and Sales steps log business values such as names and prices, and those
    stay readable.

    The documented limit of that trade-off: a credential carrying **no
    delimiter at all** is not recognisable.  A bare ``Examples`` table row
    such as ``|manager@sales.com |P@ssw0rd |`` has no keyword, no separator
    and no quotes, and nothing distinguishes its cells from the prices and
    names the Sales steps print, so it is left alone rather than guessed at.
    That shape does not reach a log through the port's own code - the worker
    runs with the event-collecting formatter and ``-o <path>``, not with a
    table-printing one (specification 0.4.1, "How the engine and the writers
    divide the work") - and the substituted forms that *do* reach it, the
    outline's scenario name and its step names, carry the quotes and keywords
    that category (f) matches.

    Args:
        text: The text to mask.  Normally the output of
            :func:`sanitize_log_text`, so that no control character can hide
            a keyword from these patterns.  A non-``str`` argument is
            rendered through ``repr`` rather than rejected.

    Returns:
        The same text with each matched secret replaced by
        :data:`REDACTION_PLACEHOLDER`.  Text containing nothing
        credential-shaped is returned unchanged.

    Examples:
        >>> redact_sensitive("browser=chrome password=hunter2 workers=4")
        'browser=chrome password=[redacted] workers=4'
        >>> redact_sensitive('User enters "someone@example.com" username')
        'User enters [redacted] username'
        >>> redact_sensitive("password not found")
        'password not found'
    """
    rendered = text if isinstance(text, str) else _safe_repr(text)
    for pattern, replacement in _REDACTION_RULES:
        rendered = pattern.sub(replacement, rendered)
    return rendered


def _redact_worker_text(control_safe: str) -> str:
    """Redact ``control_safe``, including a keyword split by a control.

    :func:`redact_sensitive` matches keywords as written, which a control
    character placed inside one defeats: ``pass\\x00word=hunter2`` reaches
    this function as text whose keyword has a printable escape spelling in
    the middle of it, so ``password`` does not match and the value would be
    relayed in the clear.  Spelling the control rather than deleting it is
    the right default - a reader needs to know a control was there - but it
    must not be a way to smuggle a secret past the rules.

    So the de-noised view is tested too: delete every escape spelling
    :func:`_control_safe` can produce and redact that.  When the de-noised
    text *is* changed by redaction, a credential was hiding behind a control
    and the masked de-noised line is what gets returned - the escapes are
    lost, which is the right trade when the alternative is disclosure.  When
    it is not, the ordinary spelled-and-redacted line is returned unchanged,
    so the common case keeps every escape it had.

    Args:
        control_safe: Output of :func:`_control_safe` - printable, one line.

    Returns:
        The redacted text, from whichever of the two views actually needed
        masking.
    """
    rendered = redact_sensitive(control_safe)
    if not _ESCAPE_SPELLING_PATTERN.search(control_safe):
        # No escape spelling in the line, so there is no second view and
        # nothing could have been hiding behind one.  This is the path almost
        # every relayed line takes.
        return rendered
    for replacement in _DENOISE_REPLACEMENTS:
        probe = _ESCAPE_SPELLING_PATTERN.sub(replacement, control_safe)
        probe_redacted = redact_sensitive(probe)
        denoised_rendered = _ESCAPE_SPELLING_PATTERN.sub(
            replacement, rendered
        )
        if denoised_rendered == probe_redacted:
            # This view exposes nothing the ordinary path missed - the two
            # agree once the spellings are discounted the same way - so it is
            # not a reason to discard the spellings.
            continue
        return probe_redacted
    # Every view agreed with the ordinary path, so the spelled text is
    # returned and each escape it carried survives.  Testing for that is what
    # keeps the fallback rare: discarding escapes whenever any rule fired
    # would quietly lose the "a control character was here" signal, which is
    # itself worth reading.
    return rendered


def _worker_level(control_safe: str, default_level: int) -> int:
    """Return the level one relayed line belongs at.

    Args:
        control_safe: Output of :func:`_control_safe`.  The token is read from
            this rather than from the redacted or de-noised text, so hiding a
            token behind a control character can never *raise* a line's
            severity: the escape spelling displaces the token from column 0
            and the anchored pattern stops matching.
        default_level: The floor supplied by the stream the line arrived on.

    Returns:
        ``max(parsed, default_level)`` when a level token is present, and
        ``default_level`` otherwise.
    """
    match = _WORKER_LEVEL_TOKEN_PATTERN.match(control_safe)
    if match is None:
        return default_level
    parsed_level = _WORKER_LEVEL_NAMES.get(match.group("level").upper())
    if parsed_level is None:
        # Unreachable while the pattern and the mapping agree; keeping the
        # branch means a future edit to one of them degrades to the stream's
        # own level instead of raising inside the relay loop.
        return default_level
    return max(parsed_level, default_level)


def render_worker_line(
    line: str,
    *,
    default_level: int,
    limit: int = RELAYED_LINE_LIMIT,
) -> tuple[int, str]:
    """Render one line of worker output, preserving the child's severity.

    ``app/services/test_run_service.py`` relays a finished worker's captured
    ``stdout`` and ``stderr`` into the parent log, one record per physical
    line.  Two things have to survive that hop.

    **Safety.** The text is whatever the engine, a driver, a page object or a
    step printed, so it is made control-safe, then redacted, then bounded -
    in that order, and the order is load-bearing.  Control safety has to come
    first so that nothing downstream can see a line break or an escape
    sequence.  The bound has to come **last** because redaction lengthens
    text: every secret it masks becomes :data:`REDACTION_PLACEHOLDER`, so a
    line of many short secrets grows, and a bound applied before that bounds
    a string no reader ever sees.  A keyword that a control character splits
    is handled by :func:`_redact_worker_text`, which tests the de-noised view
    as well so that spelling a control cannot smuggle a value past the rules.

    **Severity.** The child is a separate process with its own logging state,
    and behave's default ``logging_format`` is
    ``"LOG_%(levelname)s:%(name)s: %(message)s"`` installed through
    ``basicConfig`` (measured against behave 1.3.3), so a child record
    arrives as ``LOG_ERROR:app.reporting.screenshots: …`` at column 0.
    Relaying every ``stderr`` line at ``WARNING`` - the obvious
    implementation - flattens exactly the records that matter: the screenshot
    helper's ``logger.exception``, a formatter failure, a system error.  The
    leading token is therefore parsed and the record is emitted at
    ``max(parsed_level, default_level)``.

    The ``max`` is the whole rule, in both directions:

    * **Never downgrade.** A ``LOG_DEBUG:`` line arriving on ``stderr`` stays
      at the ``stderr`` default of ``WARNING``.  Downgrading it would route a
      stderr diagnostic onto stdout through the filter installed by
      :func:`configure_logging` and break the published stream split, and it
      would also let a child suppress its own diagnostics by printing a low
      token - a forgery this function must not honour.
    * **Do upgrade.** A ``LOG_ERROR:`` line is emitted at ``ERROR`` whichever
      stream it arrived on, which is what makes a child failure visible as a
      failure.  behave prints a record to ``stderr`` and also repeats it in
      the captured-log block on ``stdout``, so the stdout copy is upgraded
      the same way rather than being silently demoted to progress.

    The token is left in the returned text: it names the child's own logger -
    ``app.reporting.screenshots``, ``app.automation.driver`` - and that
    identity is most of the diagnostic's value.  Nothing here emits a record;
    the caller does, which is what keeps ``[shard N]`` tagging and stream
    choice with the module that owns the relay.

    Args:
        line: One physical line of child output, already split by the caller.
        default_level: The level the stream itself implies - ``logging.INFO``
            for a worker's ``stdout`` and ``logging.WARNING`` for its
            ``stderr``.  It is a floor, never a ceiling.
        limit: Maximum characters of the finished text to keep, applied after
            every transformation.  Defaults to :data:`RELAYED_LINE_LIMIT`,
            which is what the relay uses; ``0`` or less means no bound.

    Returns:
        A ``(level, safe_text)`` pair: the level to emit at, and the
        control-safe, redacted, bounded single-line text to emit.  With no
        recognisable token the level is ``default_level`` unchanged.

    Examples:
        >>> render_worker_line(
        ...     "LOG_ERROR:app.reporting.screenshots: boom",
        ...     default_level=logging.WARNING,
        ... ) == (logging.ERROR,
        ...       "LOG_ERROR:app.reporting.screenshots: boom")
        True
        >>> render_worker_line(
        ...     "LOG_DEBUG:behave: selecting features",
        ...     default_level=logging.WARNING,
        ... )[0] == logging.WARNING
        True
    """
    # Three steps, in this order and for stated reasons.  Control safety
    # first, so nothing downstream can see a line break or an escape
    # sequence.  Then redaction, which *lengthens* text - every secret it
    # masks becomes ``[redacted]`` - which is why the bound comes last and is
    # applied to the finished text: bounding before redacting bounds a string
    # nobody ever reads.
    control_safe = _control_safe(line)
    level = _worker_level(control_safe, default_level)
    return level, _bound_text(_redact_worker_text(control_safe), limit)
