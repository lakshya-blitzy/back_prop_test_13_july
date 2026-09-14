"""Console logging for the Testinium-QA Python port.

This module owns console-logging *configuration* and nothing else.  It has no
Java counterpart: specification 0.4.1 maps it as "console logging standing in
for surefire's output", and ``pom.xml`` declares no logging framework, so the
replacement uses the standard library and adds no pinned dependency.

Configuration only, never logger acquisition.  Specification 0.4.2 fixes the
invariant that ``app/utils`` imports nothing from the ``app`` package, so this
module publishes no ``get_logger()`` helper: every other module calls
``logging.getLogger(__name__)``, an import of ``logging`` and not of anything
under ``app``.  Importing this module installs no handler, sets no level and
touches no logger, so :func:`configure_logging` is safe never to have been
called - a module logging beforehand keeps ``logging.lastResort``.

Configuration is per process.  The suite runs in a process pool (deviation 4)
and each worker has its own ``logging`` state, so there is deliberately no
cross-process aggregation, no queue handler and no shared log file:
interleaved worker output is expected, as is the missing-configuration-file
warning appearing once per worker rather than once per JVM (deviation 17).

Who calls this, and who must not
--------------------------------
:func:`configure_logging` is called **once per process, by whichever module
owns that process's entry point**.  The port has three such entry points, and
the count is a consequence of the process model rather than a fact to rely on:
a run is executed by a pool of separate OS processes (see "Multi-process
behaviour" below), so each one installs the handler split for itself.

1. ``app/cli.py`` - at the start of the ``run-tests`` command, before any
   work, so the run's progress and diagnostics are routed correctly.
2. ``app/__init__.py`` - inside ``create_app()``, so the read-only HTTP viewer
   logs consistently.
3. ``app/services/test_run_service.py`` - inside the pool task, which runs in
   a *new* process that inherits no logging state.  That call is deliberately
   behind a function-scoped import, so importing the service - which every
   child does, and which selection and merging in the parent do too - stays
   free of this module.

``app/utils/properties.py``, ``app/reporting/*``, ``app/pages/*``,
``app/automation/*`` and ``features/environment.py`` must **not** call this
function.  They call ``logging.getLogger(__name__)`` and nothing else, which
is what keeps ``app/utils`` free of any ``app``-package import.

Importing this module for its *rendering* surface is a different thing and is
allowed: ``app/cli.py`` and ``app/services/test_run_service.py`` both do, for
the functions described under "The diagnostic rendering surface" below.  What
no module outside the three entry points above may do is call
:func:`configure_logging`.

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
injection surface (CWE-117) in the plain ``logger.info("%s", value)`` form.
Those two render their text explicitly, at the call site, because they also
decide the record's level and its tag.  **Every other record in the port is
covered without its author doing anything**, by the formatter described under
"The global record sanitizer" further down: nothing reaches a handler
installed here unrendered, so a record built with a plain format string and an
operating system's own error text is safe by construction rather than by
review.

The two explicit call sites:

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
the functions below are the single implementation of it:

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
:func:`relativize_paths`
    Absolute paths under this process's own workspace roots reduced to the
    repository-relative identifiers a reader actually needs, so a console log
    does not publish the workspace topology it was produced on (CWE-200).
:func:`render_path`
    One filesystem path as such an identifier, bounded and control-safe.
    ``app/cli.py`` names the artifacts a run wrote through it, so those
    records carry the relative identifier
    :data:`app.utils.paths.CUCUMBER_JSON_RELPATH` names rather than an
    absolute path.
:func:`render_worker_line`
    Sanitizing, redaction and bounding plus the child's own severity, so a
    worker's ``ERROR`` stays an ``ERROR`` in the parent log instead of
    flattening to a ``WARNING``.

**Redaction applies to log records only, never to an artifact.**
Specification 0.8's test-data note is explicit that the Gherkin ``Examples``
credentials are pre-existing fixture data for an external instance which no
agent may redact, parameterize or rotate: the feature files, the worker
argv and all four report artifacts carry them verbatim, and parity requires
it.  :func:`redact_sensitive` exists because a *log record* is not an
artifact - it is operational output that ends up in a Jenkins console and
its retention is nobody's contract - so it is reached from the two log call
sites above and from :class:`SanitizingFormatter`, which is the console
boundary, and from nowhere else.  No writer, no template and no artifact path
in the port calls it.

The global record sanitizer
---------------------------
The two call sites above are the ones that *choose* how their text is
rendered.  They are not the only records a run emits: the command line names
the artifacts it wrote and why a clean failed, the run service reports
intermediate-directory problems with the operating system's own message, the
report service names a failing writer and attaches its traceback, and
``app/reporting/screenshots.py`` logs a suppressed capture with
``logger.exception``.  Each of those carries text nobody sanitized - an
absolute workspace path, an ``OSError``'s message, a traceback whose frames
quote source lines - and a review that asks every author to remember is a
review that will eventually miss one.

So the boundary is enforced in one place instead: :class:`SanitizingFormatter`
is installed on **every** handler :func:`configure_logging` creates, and it
renders whatever a record turns out to say.  For the message: paths
relativized, control characters spelled out, credential shapes masked, length
bounded at :data:`RECORD_MESSAGE_LIMIT`.  For an exception or a stack block:
the same, line by line, with each line prefixed by
:data:`TRACEBACK_LINE_PREFIX` so that nothing inside a traceback can be read
as a record of its own, and the whole block bounded at
:data:`TRACEBACK_TEXT_LIMIT`.

It is a formatter rather than a ``logging.Filter``, and that is a correctness
choice rather than a stylistic one.  A filter can only change what is emitted
by rewriting the record, and the *same* record object is handed to the sibling
handler, to any handler an embedding application attached, and to a test
harness capturing records - so a rewriting filter would leak rendered text
into consumers that asked for the original, and would re-apply its own length
bound to text it had already truncated.  The formatter renders into the string
it returns and leaves the record as it found it, so each handler emits safe
text and no consumer's record is mutated.

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
   ``logging``, ``os``, ``re``, ``sys`` and ``typing``, besides the
   ``__future__`` annotations import every module in the port carries.  All
   five are standard library, so the pinned dependency set is unchanged.
   ``re`` is there for the rendering surface below; ``os`` is there for
   :func:`relativize_paths`, which needs the process's working directory and
   this file's own location to know what counts as a workspace path, and it
   is used for **nothing else** - in particular ``os.environ`` is never read,
   because the configuration surface is fixed at six file-backed keys.
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
    displayed'``; and step-level comparison text of the shape the reference
    Java suite wrote to standard output -
    ``"actualName = Alice Example"``, ``"totalPrice = 1200.00"``,
    ``"expectedWarning = ..."`` - which is the content of an assertion
    diagnostic, not a credential, and which masking would render useless.
    The port itself no longer emits those lines: review finding
    SEC2-F04 removed the ``System.out.println`` ports from
    ``features/steps/crm_steps.py`` and ``features/steps/sales_steps.py``
    because the values they interpolated were read live from the system
    under test.  The cases stay in this list on their own merits - engine
    and library diagnostics carry text of exactly this shape, and a
    sanitizer that masked it would be unusable - so nothing about this
    rule set changed with them.
13. *The global sanitizer* - every handler :func:`configure_logging` installs
    carries a :class:`SanitizingFormatter`, and one call to it is enough to
    prove the boundary: log ``"wrote %s"`` with an absolute path under the
    working directory and assert the emitted line carries the relative
    identifier and not the absolute one; log a line containing ``"\\r\\n"``
    and a credential shape and assert one physical line with the value
    masked; log a 20,000-character message and assert the emitted text is
    bounded with the truncation notice; and ``logger.exception`` inside an
    ``except`` block and assert every traceback line carries
    :data:`TRACEBACK_LINE_PREFIX`, so a forged ``"ERROR app.x: ..."`` inside
    an exception message cannot be read as a record.  Assert the
    non-mutation too, because it is the reason this is a formatter: after a
    record has been emitted, ``record.msg``, ``record.args`` and
    ``record.exc_text`` are exactly what the caller passed, which is what
    keeps a capture handler and an embedding application's handler seeing the
    original.
14. *Level parsing, and the never-downgrade rule* -
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
import os
import re
import sys
from typing import Any, Final, TextIO

__all__ = [
    "LOG_FORMAT",
    "PACKAGE_LOGGER_NAME",
    "PATH_RENDER_LIMIT",
    "RECORD_MESSAGE_LIMIT",
    "REDACTION_PLACEHOLDER",
    "RELAYED_LINE_LIMIT",
    "STDERR_HANDLER_NAME",
    "STDOUT_HANDLER_NAME",
    "TRACEBACK_LINE_PREFIX",
    "TRACEBACK_TEXT_LIMIT",
    "TRUNCATION_SUFFIX_TEMPLATE",
    "VALUE_RENDER_LIMIT",
    "WORKSPACE_ROOT_PLACEHOLDER",
    "SanitizingFormatter",
    "configure_logging",
    "redact_sensitive",
    "relativize_paths",
    "render_option_value",
    "render_path",
    "render_worker_line",
    "sanitize_log_text",
]

PACKAGE_LOGGER_NAME: Final[str] = "app"

STDOUT_HANDLER_NAME: Final[str] = "testinium-qa-stdout"
STDERR_HANDLER_NAME: Final[str] = "testinium-qa-stderr"

#: Concise single-line format for a CI console: level, logger name, message.
#: The logger name tells a driver message from a report-writer one.  No
#: timestamp, because a Jenkins console timestamps lines itself and leaving
#: it out keeps captured output deterministic for the unit suite.
LOG_FORMAT: Final[str] = "%(levelname)s %(name)s: %(message)s"

#: Records at this level and above go to stderr; everything below goes to
#: stdout.  This is the boundary specification 0.4.1 fixes.
_STDERR_THRESHOLD: Final[int] = logging.WARNING

#: Bound on one option value's ``repr``, so no value can fill a log line.
VALUE_RENDER_LIMIT: Final[int] = 120

#: Character bound applied by :func:`sanitize_log_text` to one relayed line
#: of worker output.  Sized for the longest legitimate engine diagnostic -
#: a Selenium stack-trace line or a ``NoSuchElementException`` message with
#: a full CSS selector - so real diagnostics arrive whole, while a worker
#: that prints a megabyte of HTML cannot bury the rest of the run's log.
RELAYED_LINE_LIMIT: Final[int] = 2000

#: Replaces credential-shaped content; fixed, so no length leaks with it.
REDACTION_PLACEHOLDER: Final[str] = "[redacted]"

#: Appended by :func:`sanitize_log_text` when it bounds a value, with
#: ``dropped`` formatted to the exact number of characters removed.  Saying
#: how much was dropped is what keeps a truncated line honest: a reader can
#: tell a complete diagnostic from a clipped one without guessing.
TRUNCATION_SUFFIX_TEMPLATE: Final[str] = "...[+{dropped} char(s) truncated]"

#: Character bound :class:`SanitizingFormatter` applies to one record's
#: message.  Larger than :data:`RELAYED_LINE_LIMIT` on purpose: a relayed
#: worker line arrives already bounded at that limit and then gains a
#: ``[shard N]`` tag, so a second bound at the same value would re-truncate
#: text a reader has never seen whole.  Generous enough for the longest
#: diagnostic the port builds itself - a writer failure naming its destination
#: and repeating an ``OSError`` - and small enough that a pathological message
#: cannot bury the rest of a Jenkins console.
RECORD_MESSAGE_LIMIT: Final[int] = 4000

#: Character bound :class:`SanitizingFormatter` applies to one record's whole
#: exception or stack block, counted after every line of it has been rendered
#: and prefixed.  Tracebacks are legitimately long - a Selenium failure inside
#: a step inside the engine is a deep stack - so this is deliberately looser
#: than :data:`RECORD_MESSAGE_LIMIT`; what it rules out is an exception whose
#: ``__str__`` returns a megabyte.
TRACEBACK_TEXT_LIMIT: Final[int] = 8000

#: Prefix :class:`SanitizingFormatter` puts on every physical line of an
#: exception or stack block.  A traceback is the one thing the port logs that
#: is legitimately several lines, and keeping those line breaks is what makes
#: it readable - but an unprefixed continuation line is exactly what a forged
#: record looks like, since :data:`LOG_FORMAT` starts a real record with its
#: level name.  The prefix marks every line of the block as belonging to the
#: record above it, so a message containing ``"ERROR app.services: run
#: failed"`` reads as ``"| ERROR app.services: run failed"`` and cannot be
#: mistaken for one (CWE-117).
TRACEBACK_LINE_PREFIX: Final[str] = "| "

#: Character bound :func:`render_path` applies to one rendered path.  Sized
#: for the longest artifact path the port produces - a PrettyReports detail
#: page under the report tree - plus room for a deeply nested checkout, and
#: bounded so that a path assembled from caller-supplied text cannot fill a
#: console line on its own.
PATH_RENDER_LIMIT: Final[int] = 512

#: What :func:`relativize_paths` leaves behind when the text names a workspace
#: root itself rather than something inside it.  ``"."`` is what the relative
#: spelling of that directory actually is, so a record saying a run "left
#: ``.``/``target`` as the clean step left it" stays true without naming the
#: absolute location it ran in.
WORKSPACE_ROOT_PLACEHOLDER: Final[str] = "."

#: Directory this package was imported from - the parent of ``app/`` - used by
#: :func:`relativize_paths` as a workspace root alongside the process's
#: working directory.  In a source checkout it is the repository root, so a
#: traceback frame in ``/…/checkout/app/reporting/events.py`` renders as
#: ``app/reporting/events.py``; in an installed copy it is the site-packages
#: directory, which reduces the same frame to the same identifier.  Computed
#: once, at import, because a module's location cannot change afterwards.
_PACKAGE_PARENT_DIR: Final[str] = os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)

#: Path separators this platform accepts.  On Windows that is both ``\`` and
#: ``/``, because the operating system and every library on it treat them
#: interchangeably, so ``C:\ws\job\target`` and ``C:/ws/job/target`` name one
#: directory and a reduction that handled only the native spelling would leave
#: the other absolute.  On POSIX it is ``/`` alone, and deliberately so: a
#: backslash is an ordinary filename character there, so treating it as a
#: separator would cut real names in half.
_PATH_SEPARATORS: Final[tuple[str, ...]] = tuple(
    separator for separator in (os.sep, os.altsep) if separator
)

#: Whether this platform's paths are case-insensitive, decided by asking
#: :func:`os.path.normcase` rather than by testing the platform name - which
#: is the same question the standard library answers for path comparison.
#: ``True`` on Windows, where ``C:\WS\Job`` and ``c:\ws\job`` are one
#: directory and a case-sensitive reduction would leave the second absolute.
_CASE_INSENSITIVE_PATHS: Final[bool] = os.path.normcase("A") != "A"

#: Characters that may follow a workspace root in a record without being part
#: of a longer name: the shapes a diagnostic actually puts after a path.  A
#: character *outside* this set - a letter, a digit, ``-``, ``@`` - means the
#: text names a **sibling** directory whose name merely begins with the root's
#: (``<workspace>-archive``, and Jenkins's own ``<workspace>@tmp``), which is
#: a different directory outside the workspace and must keep its absolute
#: spelling rather than be presented as though it were inside.
_ROOT_BOUNDARY_CHARACTERS: Final[str] = "\"'`,;:)]}>!?*|="

#: Characters that may precede a workspace root, by the same rule read the
#: other way: a root preceded by a name character is the tail of some longer
#: path that merely ends with these components, not this workspace.
_ROOT_PRECEDING_PATTERN: Final[str] = r"(?<![A-Za-z0-9_.@%+~-])"

#: Compiled root matchers, keyed by the root and the platform rules it was
#: built under.  :func:`relativize_paths` runs on every record, and building a
#: pattern per record would compile the same three expressions thousands of
#: times in a run; the working directory changes rarely and the package root
#: never, so the cache stays at a handful of entries.
_ROOT_PATTERN_CACHE: Final[dict[tuple[str, tuple[str, ...], bool], re.Pattern[str]]] = {}

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

# Every pattern below is compiled once, at import: :func:`render_worker_line`
# runs on every line of every worker's output, ``re``'s internal cache is a
# fixed-size dict a busy process can evict, and a malformed pattern then
# fails at import rather than mid-run.  Compiling installs no handler and
# touches no logger, so module import stays side-effect free.

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
#: The surrogate range is included alongside the control ranges, and it is
#: not decorative.  A lone surrogate is not a character a stream can encode:
#: Python decodes filesystem bytes with ``surrogateescape``, so a path
#: containing a byte the filesystem encoding cannot decode - which
#: :func:`relativize_paths` and :func:`render_path` both handle, and which
#: ``os.getcwd()`` itself can return - arrives as ``U+DC80``-``U+DCFF``.
#: Writing such a string to a UTF-8 console raises ``UnicodeEncodeError``
#: inside the handler, so the record is lost rather than merely ugly.
#: Spelling it out is what keeps the "printable, one line, always emitted"
#: guarantee true for text that came from the filesystem.
_CONTROL_CHARACTER_PATTERN: Final[re.Pattern[str]] = re.compile(
    "[\x00-\x1f\x7f-\x9f\u2028\u2029\ud800-\udfff]"
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
#: [Login.feature:22-36], so ``username=someone@example.invalid`` in a child
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

#: Category (g): an email address anywhere, with or without a label.  It is
#: a category of its own because this suite's account names are email
#: addresses [Login.feature:22-36] and a child diagnostic - a step name, an
#: assertion message, a page title - can carry one with no key in front of
#: it.  The local part excludes quotes and angle brackets, so surrounding
#: punctuation is preserved rather than swallowed.
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

_QUOTED_BEFORE_KEYWORD_PATTERN: Final[re.Pattern[str]] = re.compile(
    rf"(?P<value>{_QUOTED_VALUE})"
    rf"(?P<gap>\s+)"
    rf"(?P<key>\b{_ADJACENT_CREDENTIAL_KEYWORDS}\b)",
    re.IGNORECASE,
)

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
    """Render ``value`` for a diagnostic, suppressing any ``Exception``.

    ``repr`` runs user code for any object that defines ``__repr__``, so
    describing an object can itself fail, and both groups of callers need it
    not to: the guards below are already on a failure path and describe an
    object a host, a harness or a third-party library supplied, while
    :func:`render_option_value` and :func:`sanitize_log_text` render whatever
    a caller passed.  Neither a guard nor a log record may be the thing that
    fails a run.  The fallback names the type instead, itself guarded because
    a custom metaclass can make even ``type(value).__name__`` raise.

    Args:
        value: Any object, however hostile.

    Returns:
        ``repr(value)`` when that succeeds, otherwise
        ``"<unrepresentable ...>"``.

    Raises:
        Nothing derived from ``Exception``, which is suppressed.  Every other
        ``BaseException`` - a ``KeyboardInterrupt`` or ``SystemExit`` raised
        while ``repr`` runs - propagates, so an interrupt is not absorbed.
    """
    try:
        return repr(value)
    except Exception:
        try:
            return f"<unrepresentable {type(value).__name__}>"
        except Exception:
            return "<unrepresentable object>"


def _describe_exception(exc: BaseException) -> str:
    """Render ``exc`` as ``Type: message``, suppressing any ``Exception``.

    ``str(exc)`` runs user code - an exception class is free to define
    ``__str__`` - so formatting a caught exception is itself a call that can
    fail.  Guarding it here is what lets the callers below promise that no
    ordinary ``Exception`` from an advisory operation reaches
    :func:`configure_logging`'s caller.  The type name is always included,
    because for an empty-message exception such as a bare ``RuntimeError()``
    the type is the whole of the diagnostic.

    Args:
        exc: The caught exception to describe.

    Returns:
        ``"Type: message"``, or just ``"Type"`` when the exception carries no
        message.

    Raises:
        Nothing derived from ``Exception``, including one raised by the
        argument's own ``__str__``.  Every other ``BaseException``
        propagates.
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
    ``logging.raiseExceptions``, the standard library's own switch for
    whether internal logging problems are surfaced, so a caller that has
    opted out of logging noise gets none from this module either.

    The message is rendered before it is written, and that is not optional.
    Callers assemble it from a host stream's ``repr`` and an exception's
    ``str``, either of which may legally return embedded newlines, escape
    sequences or unbounded text - and a newline written straight out would
    produce a second, unprefixed physical line indistinguishable from an
    independent record.  It therefore goes through the same control-safe,
    redacted, bounded rendering the worker relay uses, which is pure text
    manipulation over compiled patterns and acquires no logger, so it cannot
    recurse back into here.

    Raises:
        Nothing derived from ``Exception``: a diagnostic that cannot be
        written is not worth failing a run over, so a failing ``write`` or
        ``flush`` is suppressed.  Every other ``BaseException`` propagates,
        an interrupt being the operator's business and not a logging problem.
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
        # This is the fallback reporter, so the catch is as wide as
        # ``Exception``: the original stderr may be closed, detached or a
        # host object whose ``write`` fails, and losing a diagnostic line
        # must not abort the configuration the caller was reporting from.
        return


def _enable_line_buffering(stream: TextIO | None) -> bool:
    """Put ``stream`` in line-buffered mode, reporting whether it took effect.

    ``StreamHandler`` flushes after every ``emit()``; what this adds is the
    *underlying* stream staying line-buffered when Jenkins captures a CLI run
    through a pipe, where Python would otherwise block-buffer stdout and hold
    progress back.  Specification 0.4.1 requires both streams line-buffered.

    The call is fully guarded and is a deliberate no-op on any stream that
    does not support it - which includes the replacement objects pytest's
    ``capsys`` installs, and equally a stream whose ``reconfigure()`` raises
    *any* ``Exception``, of any type, known to this module or not.  Returning
    a bool rather than raising makes "this stream cannot be reconfigured" an
    ordinary handled outcome: it is normal, not an error.

    The width of the guard is the point, not an oversight.  Buffering mode is
    an optimisation for pipe capture and never a correctness requirement,
    while every caller of :func:`configure_logging` is a process entry point -
    ``create_app()``, the ``run-tests`` command and the pool task a worker
    process starts in - so an exception escaping from here would stop an
    application being built, a suite being run or a shard being executed.
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
        stream: The stream to reconfigure, or ``None`` when the process has
            none (a Windows GUI host).

    Returns:
        ``True`` only when ``reconfigure(line_buffering=True)`` completed;
        advisory, since no caller gates configuration on it.

    Raises:
        Nothing derived from ``Exception``; a ``BaseException`` propagates.
    """
    if stream is None:
        return False
    reconfigure = getattr(stream, "reconfigure", None)
    if not callable(reconfigure):
        return False
    try:
        reconfigure(line_buffering=True)
    except _EXPECTED_RECONFIGURE_ERRORS:
        return False
    except Exception as exc:
        # A stream that advertises ``reconfigure`` and then fails in some
        # other way is worth one internal line: it says the host's stream is
        # not what it claimed to be.  Configuration continues regardless.
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
            # The selecting attribute is public enough for a third-party
            # handler to carry, so whatever its ``close()`` raises must not
            # abort reconfiguration: the handler is already off the logger
            # and will be collected either way.
            _report_internal_problem(
                f"could not close handler {_safe_repr(handler)}: "
                f"{_describe_exception(exc)}"
            )


class _LiveStreamHandler(logging.StreamHandler):
    """A ``StreamHandler`` that resolves ``sys.stdout``/``sys.stderr`` on use.

    The standard ``StreamHandler`` binds a stream *object* once, when it is
    constructed.  That is wrong for this module, because every entry point
    that calls :func:`configure_logging` does so long before most records are
    emitted: ``create_app()`` configures logging while building an
    application, the ``run-tests`` command configures it before the run
    starts, and a pool task configures it as its process comes up.  Any code that legitimately replaces a process stream after that
    point - a WSGI host redirecting diagnostics, a harness capturing output,
    a wrapper that re-points ``stderr`` - would find records still going to
    the object that was current at configuration time, which by then may be
    stale or discarded.  The published contract is about *streams*, not about
    whichever object happened to be installed first: specification 0.4.1
    fixes "progress to stdout, engine diagnostics to stderr", so the stream
    is looked up by name each time it is used and the contract holds however
    a host has arranged those two streams.

    The standard handler's degradation is preserved.  A process can genuinely
    have no ``sys.stdout`` - a Windows GUI host, and the port runs on Windows
    as well as Linux and macOS - and output then falls back to ``sys.stderr``,
    collapsing the split into one merged stream rather than failing the run.
    Should both be absent the stream bound at construction is used, and if
    that is absent too ``logging`` reports the emit failure through its own
    ``handleError`` path and the run continues.

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

    Called **once per process, by that process's entry point**: ``app/cli.py``
    at the start of the ``run-tests`` command, ``app/__init__.py`` inside
    ``create_app()``, and ``app/services/test_run_service.py`` inside the pool
    task, whose process inherits no logging state of its own.  Every other
    module in the port acquires a logger with
    ``logging.getLogger(__name__)`` and never calls this function; see the
    module docstring for why that split exists.

    Every handler it installs carries one :class:`SanitizingFormatter`, so a
    record reaches a console only through the rendering described under "The
    global record sanitizer" in the module docstring - relativized, one
    physical line, redacted and bounded - whether or not its author rendered
    anything.

    The function is idempotent.  Calling it repeatedly replaces the handlers
    it installed previously instead of appending to them, so a process that
    builds several Flask applications - the unit suite does exactly that -
    never emits a record twice.  It has no effect on the root logger and no
    effect on handlers this module did not install.

    Args:
        verbose: When ``True`` the threshold drops to ``DEBUG``; the default
            is ``INFO``.  This is the port's only verbosity control - no
            environment variable and no configuration key affects logging,
            because the configuration surface is fixed at six keys.
        stream_split: When ``True`` (the default) records at ``INFO`` and
            below go to ``stdout`` and ``WARNING`` and above to ``stderr``,
            the published CLI contract.  When ``False`` every record goes to
            ``stderr`` on one handler, the standard library's own default.

    Returns:
        ``None``; configuration is applied as a side effect on the ``app``
        logger.
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

    _discard_managed_handlers(logger)

    # One sanitizer, shared by every handler built below, so that no record
    # can reach a console except through it.  The class is defined in the
    # rendering section further down, beside the functions it composes; it is
    # referenced here and resolved at call time.
    formatter = SanitizingFormatter(LOG_FORMAT)

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
            rendered through :func:`_safe_repr` first, which suppresses
            any ``Exception`` a hostile ``__repr__`` raises.

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

    This is the port's answer to log injection (CWE-117): a record built with
    ``logger.info("%s", untrusted)`` is one that text gets to *shape* - a
    newline in it starts what looks like a new record, and a CSI sequence can
    recolour or erase what a console already printed.  Escape sequences are
    therefore deleted, being instructions rather than information; every
    remaining control character is spelled out printably, so no break
    survives and the text that followed one is still readable in the same
    record; and the bound is applied last, to what will really print.

    Args:
        text: The text to render.  A non-``str`` argument goes through
            ``repr`` rather than being rejected, because raising on a
            diagnostic path would lose the message being reported.
        limit: Maximum characters kept, counted after escaping; ``0`` or less
            means no bound.

    Returns:
        One physical line with no line break, escape sequence or control
        character, at most ``limit`` characters plus the truncation notice.
        Printable non-ASCII text is untouched, French assertion strings
        [Login.feature:89] included.
    """
    return _bound_text(_control_safe(text), limit)


def render_option_value(
    value: object, *, limit: int = VALUE_RENDER_LIMIT
) -> str:
    """Render one caller-supplied option value for a log record.

    Built for ``app/cli.py``'s "Starting the suite" record, whose ``browser``
    field is whatever the caller typed after ``--browser``.  ``repr`` comes
    first because it quotes a string, so an empty or whitespace-only value is
    visibly present rather than invisible mid-sentence; the sanitizing pass
    still follows, because ``repr`` runs user code for any object with a
    ``__repr__`` and a custom one can return anything at all.

    The function is value-neutral, and that is a requirement.  It neither
    validates nor rewrites ``value``, and the caller forwards the *original*
    value to its workers unchanged: ``Driver.java``'s browser switch has no
    default branch, so an unrecognised browser must still reach the driver
    and fail at first use (specification 0.4.1's ``--browser`` row).

    Args:
        value: Any object, including ``None`` for an option the caller did
            not supply.  Not mutated, not validated, not consumed.
        limit: Maximum characters to keep, defaulting to
            :data:`VALUE_RENDER_LIMIT`; ``0`` or less means no bound.

    Returns:
        A bounded, single-line, control-safe rendering, suitable as a ``%s``
        argument.  ``None`` renders as ``'None'``.
    """
    return sanitize_log_text(_safe_repr(value), limit=limit)


def _workspace_roots() -> tuple[str, ...]:
    """Return the directory prefixes :func:`relativize_paths` strips.

    Three candidates, longest first so that a nested one is never left behind
    by a shorter one matching first:

    * the process's working directory, read fresh on every call because a
      worker process - and a test - may legitimately change it, and because
      every path accessor in :mod:`app.utils.paths` resolves against it, which
      makes it the root the port's own artifact paths hang off;
    * its fully resolved form, when the two differ, since a checkout reached
      through a symlink (a temporary directory on macOS is the ordinary case)
      produces paths spelled either way; and
    * :data:`_PACKAGE_PARENT_DIR`, so a traceback frame naming a module of
      this port reduces to the module's own import path.

    Returns:
        The roots, longest first and de-duplicated.  A root of one character
        or less is dropped: stripping the filesystem root itself would turn
        every absolute path in a record into a relative-looking one, which
        would misinform a reader rather than protect them.  The working
        directory is omitted if it cannot be read at all, which is what an
        unlinked working directory looks like from here and is not an error:
        the remaining roots still apply.
    """
    candidates: list[str] = []
    try:
        working_directory = os.getcwd()
    except OSError:
        # The working directory was removed under the process.  Nothing to
        # strip for it; the package root below still applies, and a record
        # must never fail to be emitted because of this.
        working_directory = ""
    if working_directory:
        candidates.append(working_directory)
        try:
            resolved = os.path.realpath(working_directory)
        except OSError:
            resolved = ""
        if resolved and resolved != working_directory:
            candidates.append(resolved)
    candidates.append(_PACKAGE_PARENT_DIR)

    unique: list[str] = []
    for candidate in candidates:
        if len(candidate) > 1 and candidate not in unique:
            unique.append(candidate)
    return tuple(sorted(unique, key=len, reverse=True))


def _root_pattern(
    root: str,
    *,
    separators: tuple[str, ...] | None = None,
    case_insensitive: bool | None = None,
) -> re.Pattern[str]:
    """Build (and cache) the matcher for one workspace root.

    A literal ``str.replace`` is not enough, and the two reasons are both
    correctness rather than polish:

    * **A path has more than one spelling.**  On Windows ``\\`` and ``/`` are
      interchangeable and case is not significant, so ``C:\\ws\\job\\target``,
      ``C:/ws/job/target`` and ``c:\\WS\\Job\\target`` are one directory.
      Matching the native, exactly-cased spelling alone would reduce the first
      and publish the other two in full.  The root is therefore split into its
      components and rejoined with a separator *class*, and the pattern is
      compiled case-insensitively where :data:`_CASE_INSENSITIVE_PATHS` says
      the platform's paths are.
    * **A prefix is not a parent.**  ``<workspace>-archive`` and Jenkins's own
      ``<workspace>@tmp`` begin with the workspace's own path and are
      different directories outside it.  The match is therefore bounded on
      both sides - :data:`_ROOT_PRECEDING_PATTERN` before it, and the tail
      group plus :data:`_ROOT_BOUNDARY_CHARACTERS` after it, which
      :func:`relativize_paths` reads to decide between reducing and leaving
      the text alone.

    Args:
        root: The absolute directory to match.
        separators: Separator characters to treat as interchangeable, or
            ``None`` for this platform's :data:`_PATH_SEPARATORS`.  A test
            passes both Windows separators explicitly, which is what makes the
            Windows behaviour assertable on a POSIX host.
        case_insensitive: Whether to match case-insensitively, or ``None`` for
            this platform's :data:`_CASE_INSENSITIVE_PATHS`.

    Returns:
        A compiled pattern whose match covers the root and any separators
        immediately after it, with those separators captured as the ``tail``
        group.
    """
    effective_separators = (
        _PATH_SEPARATORS if separators is None else separators
    )
    effective_case = (
        _CASE_INSENSITIVE_PATHS if case_insensitive is None else case_insensitive
    )
    key = (root, effective_separators, effective_case)
    cached = _ROOT_PATTERN_CACHE.get(key)
    if cached is not None:
        return cached

    separator_class = f"[{re.escape(''.join(effective_separators))}]"
    components = [
        component
        for component in re.split(separator_class, root)
        if component != ""
    ]
    body = f"{separator_class}+".join(
        re.escape(component) for component in components
    )
    # A root that begins with a separator keeps it, so "/ws/job" cannot match
    # the tail of "other/ws/job".  A drive-letter root ("C:\\ws") has no
    # leading separator and needs none.
    if root[:1] in effective_separators:
        body = f"{separator_class}+{body}"
    pattern = re.compile(
        f"{_ROOT_PRECEDING_PATTERN}(?:{body})(?P<tail>{separator_class}*)",
        re.IGNORECASE if effective_case else 0,
    )
    _ROOT_PATTERN_CACHE[key] = pattern
    return pattern


def _reduce_root(match: re.Match[str]) -> str:
    """Decide what one matched workspace root becomes.

    Three outcomes, and each of them is a different fact about the text:

    * The root is followed by a separator and then more path - so the text
      names something *inside* the workspace, and the prefix is dropped,
      leaving the relative identifier.
    * The root is named on its own, or with a trailing separator and nothing
      after it - so the text names the workspace itself, and it becomes
      :data:`WORKSPACE_ROOT_PLACEHOLDER`.
    * The root is immediately followed by a name character with no separator -
      so the text does **not** name this workspace at all but a sibling whose
      name starts with the same characters, and it is returned exactly as it
      was found.

    Args:
        match: A match from :func:`_root_pattern`.

    Returns:
        The replacement text for the matched span.
    """
    remainder = match.string[match.end() :]
    continues = bool(remainder) and not (
        remainder[0].isspace() or remainder[0] in _ROOT_BOUNDARY_CHARACTERS
    )
    if match.group("tail"):
        # Separator present: inside the workspace when something follows it.
        return "" if continues else WORKSPACE_ROOT_PLACEHOLDER
    if continues:
        # A sibling directory sharing the root's opening characters.
        return match.group(0)
    return WORKSPACE_ROOT_PLACEHOLDER


def relativize_paths(text: object) -> str:
    """Reduce absolute workspace paths in ``text`` to relative identifiers.

    A diagnostic that names a file is useful; one that publishes the absolute
    location of the workspace it ran in is useful *and* discloses the layout
    of the machine that produced it (CWE-200).  A Jenkins console is shared,
    archived and often public within an organisation, and the identifier a
    reader acts on - the artifact identifiers
    :mod:`app.utils.paths` publishes, a feature file under the features
    directory, a module of this package - is the relative one in every
    case: it is what ``README.md`` quotes, what the publisher's
    ``fileIncludePattern`` matches (``Jenkins:15``) and what a rerun
    manifest carries.

    Each root in :func:`_workspace_roots` is matched by the pattern
    :func:`_root_pattern` builds for it, which is what makes the reduction
    hold for every spelling of a path rather than for one of them: on Windows
    the separators are interchangeable and case is not significant, so
    ``C:\\ws\\job\\target``, ``C:/ws/job/target`` and ``c:\\WS\\Job\\target``
    all reduce, where a literal replacement of the native spelling would have
    published the second and third in full.  :func:`_reduce_root` then decides
    what each match becomes - a relative identifier, the workspace itself, or
    nothing at all.

    Three things are deliberately **not** rewritten, because each of them is a
    fact the diagnostic exists to carry:

    * A path outside every root - an interpreter under ``/usr/lib``, a driver
      binary on ``PATH``.  It is not this workspace's topology, and reducing
      it would lose the one detail such a record is read for.
    * A **sibling** whose name merely begins with a root's, such as
      ``<workspace>-archive`` or Jenkins's ``<workspace>@tmp``.  Those are
      different directories outside the workspace; presenting them as though
      they were inside it would be a false statement about where a file is.
    * The filesystem root itself and a bare drive letter, which
      :func:`_workspace_roots` never offers: stripping ``/`` would turn every
      absolute path in a record into a relative-looking one.

    Args:
        text: The text to rewrite.  A non-``str`` argument is rendered through
            ``repr`` rather than rejected, for the same reason
            :func:`sanitize_log_text` does: this runs on a diagnostic path,
            where raising would lose the message being reported.

    Returns:
        The text with workspace prefixes reduced.  Unbounded and not
        control-safe - the callers apply those afterwards, in that order.

    Examples:
        >>> import os
        >>> inside = os.path.join(os.getcwd(), "build", "report.txt")
        >>> relativize_paths(inside) == os.path.join("build", "report.txt")
        True
        >>> relativize_paths(os.getcwd())
        '.'
        >>> relativize_paths(os.getcwd() + "-archive") == (
        ...     os.getcwd() + "-archive"
        ... )
        True
    """
    rendered = text if isinstance(text, str) else _safe_repr(text)
    for root in _workspace_roots():
        rendered = _root_pattern(root).sub(_reduce_root, rendered)
    return rendered


def render_path(path: object, *, limit: int = PATH_RENDER_LIMIT) -> str:
    """Render one filesystem path as an identifier fit for a log record.

    The path-shaped counterpart of :func:`render_option_value`, and the
    function a caller uses when the value it is naming *is* a path:
    ``app/cli.py`` names the artifacts a run wrote and the build output
    directory it left alone through it, so those records carry
    the identifiers :mod:`app.utils.paths` publishes rather than the
    absolute location of a CI workspace.

    Unlike :func:`render_option_value` there is no ``repr`` pass, because a
    path is read as text and quoting it would only add noise; a
    :class:`~pathlib.Path` and a ``str`` therefore render identically, which
    is what keeps one destination from appearing in two spellings across
    records.

    Args:
        path: A ``str``, an :class:`os.PathLike` or anything else.  Anything
            else is rendered through ``repr``, so a ``None`` a caller did not
            guard renders as ``'None'`` instead of raising inside logging.
        limit: Maximum characters to keep, defaulting to
            :data:`PATH_RENDER_LIMIT`.  ``0`` or less means no bound.

    Returns:
        The relativized, control-safe, bounded identifier - one physical line,
        with no escape sequence and no control character.

    Examples:
        >>> render_path("build/report.txt")
        'build/report.txt'
        >>> render_path(None)
        'None'
    """
    try:
        text = os.fspath(path)  # type: ignore[arg-type]
    except TypeError:
        text = _safe_repr(path)
    if isinstance(text, bytes):
        # ``os.fspath`` passes a bytes path straight through.  Decoded the way
        # the interpreter decodes the filesystem, so an undecodable byte
        # becomes a surrogate rather than raising here - and the surrogate is
        # then spelled out printably by :func:`sanitize_log_text`, which is
        # what keeps the control-safe guarantee true for a path no encoding
        # can round-trip.  ``surrogateescape`` and not ``replace`` because a
        # replacement character loses which byte was undecodable.
        text = text.decode(
            sys.getfilesystemencoding(), "surrogateescape"
        )
    return sanitize_log_text(relativize_paths(text), limit=limit)


def redact_sensitive(text: str) -> str:
    """Mask credential-shaped content in text bound for a log record.

    Scope limit, and a specification constraint: this is for a LOG RECORD
    only and is never applied to an artifact.  Specification 0.8 states that
    the Gherkin ``Examples`` credentials are pre-existing fixture data no
    agent may redact, parameterize or rotate, so the feature files, the
    worker argv and all four artifacts carry them verbatim for parity.  A log
    record is not an artifact: it lands in a CI console whose retention and
    audience nobody has specified.

    The rules are :data:`_REDACTION_RULES`, applied in order and
    case-insensitively, each keeping its match's identifying context and
    replacing only the secret.  Over-redaction is a failure mode too, so the
    keyword sets are closed and no entropy heuristic is applied - the CRM and
    Sales steps log business names and prices.  Its cost: a credential with
    no keyword, separator or quotes at all is left alone rather than guessed.

    Args:
        text: The text to mask, normally :func:`sanitize_log_text`'s output;
            a non-``str`` argument goes through ``repr``.

    Returns:
        The same text with each matched secret replaced by
        :data:`REDACTION_PLACEHOLDER`; other text is returned unchanged.
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
        return rendered
    for replacement in _DENOISE_REPLACEMENTS:
        probe = _ESCAPE_SPELLING_PATTERN.sub(replacement, control_safe)
        probe_redacted = redact_sensitive(probe)
        denoised_rendered = _ESCAPE_SPELLING_PATTERN.sub(
            replacement, rendered
        )
        if denoised_rendered == probe_redacted:
            continue
        return probe_redacted
    # No view exposed anything the ordinary path missed, so the spelled text
    # is returned and each escape survives: the "a control character was
    # here" signal is itself worth reading.
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
    ``stdout`` and ``stderr`` into the parent log, one record per line.

    Safety: the text is whatever the engine, a driver or a step printed, so
    it is made control-safe, then redacted, then bounded - in that order,
    since redaction lengthens text and an earlier bound would bound nothing.

    Severity: behave's default ``logging_format`` puts the child's level at
    column 0 (``LOG_ERROR:app.reporting.screenshots: …``, behave 1.3.3), and
    relaying every ``stderr`` line at ``WARNING`` would flatten the records
    that matter, so the line is emitted at ``max(parsed, default_level)`` -
    a low token can neither cross the stream split nor hide a diagnostic.

    Args:
        line: One physical line of child output, already split.
        default_level: The floor the arriving stream implies - ``INFO`` for
            ``stdout``, ``WARNING`` for ``stderr``; never a ceiling.
        limit: Maximum characters of the finished text, ``0`` for no bound.

    Returns:
        A ``(level, safe_text)`` pair to emit; the level token stays in the
        text, naming the child's own logger, and nothing here emits it.
    """
    control_safe = _control_safe(line)
    level = _worker_level(control_safe, default_level)
    return level, _bound_text(_redact_worker_text(control_safe), limit)


# --------------------------------------------------------------------------
# The global record sanitizer.
#
# Everything above is called *by* a module that knows its text is untrusted.
# The class below is called for every record regardless, because the records
# that most need it are the ones nobody thought to render: a writer failure
# repeating an ``OSError``, an intermediate directory named by its absolute
# path, a suppressed screenshot's traceback.  The module docstring's "The
# global record sanitizer" section states the design and why it is a formatter
# rather than a filter.
# --------------------------------------------------------------------------


class SanitizingFormatter(logging.Formatter):
    """A formatter that renders every record safely, whatever it says.

    Installed by :func:`configure_logging` on **every** handler it creates, so
    a record reaches a console only through this class.  Four properties hold
    for the text it returns, and they are the same four the explicit call
    sites apply by hand:

    * **Paths are relative.**  Every workspace prefix
      :func:`relativize_paths` knows is reduced, so a record names
      the identifier :mod:`app.utils.paths` publishes for it and not the
      absolute location of a CI workspace (CWE-200).
    * **The text is one physical line** - or, for a traceback, a block of
      lines each marked as one.  Control characters are spelled out and
      terminal escape sequences removed, so nothing a record quotes can forge
      a second record or reprogram a terminal (CWE-117).
    * **Credential shapes are masked**, by :func:`redact_sensitive`, because
      an exception message or a traceback's own source line can quote a
      substituted step and carry this suite's fixture account into a console
      (CWE-532).  Redaction is log-only: specification 0.8 requires the
      features and all four artifacts to carry that data verbatim, and no
      writer goes through this class.
    * **The length is bounded**, at :data:`RECORD_MESSAGE_LIMIT` for the
      message and :data:`TRACEBACK_TEXT_LIMIT` for a block, each with a
      notice naming how many characters were dropped, so no single record can
      bury the rest of a run's log (CWE-400).

    **The record is left exactly as it arrived.**  The rendering happens on
    the way out, into the returned string: ``record.msg``, ``record.args`` and
    ``record.exc_text`` are the caller's own values before and after, which is
    what lets the sibling handler, a handler an embedding application
    attached, and a test harness capturing records all see what was logged
    rather than what this class chose to show.  ``record.message``, which the
    standard formatter sets on the record as a transient, is restored for the
    same reason.

    Nothing here consults module state, opens anything or logs, so it is safe
    on any thread and in a worker process that has configured nothing else.
    """

    def __init__(
        self,
        fmt: str | None = None,
        datefmt: str | None = None,
        *,
        message_limit: int = RECORD_MESSAGE_LIMIT,
        traceback_limit: int = TRACEBACK_TEXT_LIMIT,
    ) -> None:
        """Build the formatter.

        Args:
            fmt: The format string, exactly as :class:`logging.Formatter`
                takes it.  :func:`configure_logging` passes
                :data:`LOG_FORMAT`.
            datefmt: Date format, passed through unchanged.  Unused by
                :data:`LOG_FORMAT`, which carries no timestamp.
            message_limit: Character bound for one record's message.  ``0`` or
                less means no bound, for a caller that has already bounded
                its input.
            traceback_limit: Character bound for one record's whole exception
                or stack block, counted after every line has been rendered
                and prefixed.
        """
        super().__init__(fmt, datefmt)
        self._message_limit = message_limit
        self._traceback_limit = traceback_limit

    def sanitize_message(self, text: object) -> str:
        """Render one record's message as a bounded, safe, single line.

        The order is load-bearing and is the same one
        :func:`render_worker_line` documents: relativize first, so a path is
        reduced while it is still spelled as a path; then control safety, so
        nothing downstream sees a line break; then redaction, which
        *lengthens* text; then the bound, applied last to what will actually
        be printed.

        Args:
            text: The rendered message.  A non-``str`` is rendered through
                ``repr``, so this cannot raise on a hostile ``__str__``.

        Returns:
            One physical line, at most ``message_limit`` characters plus the
            truncation notice.
        """
        return _bound_text(
            _redact_worker_text(_control_safe(relativize_paths(text))),
            self._message_limit,
        )

    def sanitize_block(self, text: object) -> str:
        """Render an exception or stack block as marked, bounded safe lines.

        A traceback is the one thing the port logs that is legitimately
        several lines, and collapsing it into one would make the diagnostic a
        failing run is read from far harder to use.  So the block keeps its
        line structure, and every line is rendered by
        :meth:`sanitize_message` and then prefixed with
        :data:`TRACEBACK_LINE_PREFIX`.  The prefix is what makes keeping the
        breaks safe: an exception message containing ``"ERROR app.services:
        run failed"`` on a line of its own is emitted as ``"| ERROR
        app.services: run failed"``, which no reader and no log scraper can
        mistake for the record :data:`LOG_FORMAT` would have produced.

        Splitting uses ``str.splitlines``, so ``CR``, ``LF``, ``NEL``,
        ``U+2028`` and ``U+2029`` all become block lines of their own rather
        than surviving as breaks inside one - and each resulting line is then
        control-safe, so no break of any kind is left anywhere in the result
        except the ones this method put there.

        Args:
            text: The block to render.  A non-``str`` is rendered through
                ``repr``.

        Returns:
            The prefixed block, bounded at ``traceback_limit``.  An empty or
            whitespace-only block renders as ``""``, so no bare prefix is
            emitted for a record that had nothing to add.
        """
        rendered = text if isinstance(text, str) else _safe_repr(text)
        lines = [line for line in rendered.splitlines() if line.strip()]
        if not lines:
            return ""
        block = "\n".join(
            TRACEBACK_LINE_PREFIX + self.sanitize_message(line)
            for line in lines
        )
        return _bound_text(block, self._traceback_limit)

    def formatMessage(self, record: logging.LogRecord) -> str:  # noqa: N802
        """Format the record's header and message from sanitized text.

        ``record.message`` is the transient the standard
        :meth:`logging.Formatter.format` sets immediately before calling this
        method.  It is replaced for the duration of the call and restored
        afterwards, so the record a caller holds is unchanged and a second
        handler starts from the same place this one did.

        Args:
            record: The record being emitted.

        Returns:
            The formatted line, with the message rendered by
            :meth:`sanitize_message`.
        """
        original = record.message
        record.message = self.sanitize_message(original)
        try:
            return super().formatMessage(record)
        finally:
            record.message = original

    def formatException(self, ei: Any) -> str:  # noqa: N802
        """Return the traceback for ``ei``, rendered by :meth:`sanitize_block`.

        Args:
            ei: The ``(type, value, traceback)`` triple the standard library
                passes, typed loosely because that is how
                :class:`logging.Formatter` declares it.

        Returns:
            The marked, bounded, redacted block.
        """
        return self.sanitize_block(super().formatException(ei))

    def formatStack(self, stack_info: str) -> str:  # noqa: N802
        """Return ``stack_info`` rendered by :meth:`sanitize_block`.

        Reached for a record logged with ``stack_info=True``.  Nothing in the
        port logs that way today; it is covered because a stack block is the
        same disclosure surface as a traceback and an embedding application
        may well use it.

        Args:
            stack_info: The pre-rendered stack text.

        Returns:
            The marked, bounded, redacted block.
        """
        return self.sanitize_block(super().formatStack(stack_info))

    def format(self, record: logging.LogRecord) -> str:
        """Format one record, leaving it exactly as it arrived.

        The standard implementation caches the rendered traceback on
        ``record.exc_text`` so that a second handler need not re-render it.
        That cache is this class's one remaining route to mutating a shared
        record, so it is saved and restored around the call: each handler
        renders the traceback itself, and a consumer that reads
        ``record.exc_text`` afterwards sees whatever it held before - normally
        ``None``.  A caller that pre-set ``exc_text`` itself gets that text
        sanitized for this emit and returned untouched on the record.

        Args:
            record: The record to format.

        Returns:
            The complete line, plus the marked exception and stack blocks when
            the record carries them.
        """
        cached = record.exc_text
        if cached:
            record.exc_text = self.sanitize_block(cached)
        try:
            return super().format(record)
        finally:
            record.exc_text = cached
