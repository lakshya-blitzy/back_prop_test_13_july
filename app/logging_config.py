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
7. *No forbidden surface* - no **code** in this file constructs a
   ``FileHandler`` or a ``RotatingFileHandler``, reads ``os.environ``,
   references ``configuration.properties``, or imports anything under
   ``app``.  Assert this over the parsed module rather than with a plain
   substring grep: the prose above names each of those things precisely in
   order to prohibit it, so a literal text search matches this docstring.
   ``ast.parse`` the file, walk it, and check the ``Import``/
   ``ImportFrom``/``Attribute``/``Name`` nodes - the only imports are
   ``logging``, ``sys`` and ``typing``.
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
"""

from __future__ import annotations

import logging
import sys
from typing import Final, TextIO

__all__ = [
    "LOG_FORMAT",
    "PACKAGE_LOGGER_NAME",
    "STDERR_HANDLER_NAME",
    "STDOUT_HANDLER_NAME",
    "configure_logging",
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

#: Attribute stamped on handlers this module installs.  The idempotency guard
#: removes handlers carrying it and leaves every other handler alone, so a
#: repeated call cannot duplicate output and cannot discard a handler that an
#: embedding application attached to the same logger.
_MANAGED_HANDLER_ATTR: Final[str] = "_testinium_qa_managed"


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
    """
    if not logging.raiseExceptions:
        return
    stream = sys.__stderr__ if sys.__stderr__ is not None else sys.stderr
    if stream is None:
        return
    try:
        stream.write(f"{__name__}: {message}\n")
        stream.flush()
    except (OSError, ValueError, AttributeError):
        # The original stderr is closed or detached (a daemonised or embedded
        # process).  There is nowhere left to report to, and losing a
        # diagnostic line must not propagate into the caller.
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
    does not support it, which includes the replacement objects pytest's
    ``capsys`` installs.  Returning a bool rather than raising makes "this
    stream cannot be reconfigured" an ordinary handled outcome: it is normal,
    not an error, so it is not reported.
    """
    if stream is None:
        return False
    reconfigure = getattr(stream, "reconfigure", None)
    if not callable(reconfigure):
        return False
    try:
        reconfigure(line_buffering=True)
    except (OSError, ValueError, TypeError, AttributeError):
        # Detached, closed, or a custom stream whose reconfigure does not
        # accept line_buffering.  Buffering is an optimisation for pipe
        # capture, never a correctness requirement, so the run continues with
        # the stream exactly as it was.
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
        except (OSError, ValueError, RuntimeError) as exc:
            # Closing a StreamHandler does not close its stream, so this is
            # very nearly unreachable; if the internal lock or registry is in
            # a bad state the handler is already detached from the logger and
            # will be collected, so the reconfiguration still succeeds.
            _report_internal_problem(
                f"could not close handler {handler!r}: {exc}"
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
