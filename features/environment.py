"""Scenario lifecycle for the Gherkin suite - the port of ``Hooks.java``.

behave discovers this module at the root of the ``features`` directory, and it
owns the driver lifecycle alone: no step and no page object creates or quits a
session.  ``Hooks.java`` imports JUnit's ``@After``, so the Java teardown never
ran; deviation 6 registers these as real hooks.  Constraints the code omits:

1. One session per scenario.  Each worker process holds one driver slot;
   ``before_scenario`` fills it, ``after_scenario`` quits it and clears it, so
   nothing reaches a driver after ``quit()``.
2. The failure test covers exceptions as well as assertion failures, hence
   ``status.has_failed()`` -- which unions ``error``, ``hook_error`` and the
   rest -- and not a comparison against a single status member.
3. Capture precedes teardown, and teardown is unconditional: a screenshot
   needs a live session, and ``Hooks.java:17`` quits outside the ``if``, so the
   quit runs from a ``finally`` that no capture failure can skip.
4. Capture failures are suppressed in ``capture_png`` and nowhere else
   (deviation 19); an attach or quit failure is left to escape instead, which
   behave reports as ``HOOK-ERROR`` and records as a ``hook_error`` scenario.
"""

from __future__ import annotations

import logging
from typing import Any

from app.automation import get_driver, quit_driver
from app.config import set_userdata
from app.reporting.screenshots import DEFAULT_MIME_TYPE, capture_png

# Every name this module binds is used below, and it binds nothing else: no
# state, because the one piece of per-scenario state - the session - lives on
# behave's context and in the driver holder's slot.
#
# One record is emitted from this module, and only from one place: a failed
# teardown, where the scenario's identity is the one thing this boundary knows
# and the driver module does not.  Nothing on the evidence-gathering path logs,
# because the screenshot module owns that diagnostic already.
logger = logging.getLogger(__name__)

__all__ = ["after_scenario", "before_all", "before_scenario"]


def _scenario_identity(scenario: Any) -> str | None:
    """Describe ``scenario`` as ``file:line 'name'``, or ``None``.

    Cannot raise: an exception here would turn a finished scenario into a hook
    error.  Reads only ``filename``, ``line`` and ``name``, never tags, step
    text or example rows, where ``Login.feature`` keeps literal credentials.
    """
    try:
        filename = getattr(scenario, "filename", None)
        line = getattr(scenario, "line", None)
        name = getattr(scenario, "name", None)

        parts: list[str] = []

        if filename:
            parts.append(f"{filename}:{line}" if line else str(filename))

        if name:
            parts.append(f"'{name}'")

        return " ".join(parts) if parts else None
    except Exception:
        # A model object whose attribute access itself fails must not turn a
        # finished scenario into a hook error over a log message.  Losing the
        # identity is the correct trade here; the suppression record still
        # reaches stderr without it.
        return None


def before_all(context: Any) -> None:
    """Install the run's behave userdata as this process's configuration overrides.

    Runs once per worker process, before the first scenario executes.  It
    installs the port's only override path - userdata ahead of
    ``configuration.properties`` - which is what makes ``--browser`` effective.
    """
    set_userdata(context.config.userdata)


def before_scenario(context: Any, scenario: Any) -> None:
    """Open this worker's browser session and publish it on ``context.driver``.

    ``Hooks.java`` has no ``@Before``: the Java driver is created lazily in the
    first step, so this only fixes where a session begins.  An unrecognised
    ``browser`` yields ``None``, published unvalidated to fail at first use.
    """
    context.driver = get_driver()


def after_scenario(context: Any, scenario: Any) -> None:
    """Capture a failed scenario's screenshot, then always quit the session.

    ``Hooks.java:12-18``: photograph the live session, attach the PNG, then quit
    unconditionally from the ``finally``.  Suppression is ``capture_png``'s alone
    (deviation 19); an attach failure, like a quit failure, deliberately escapes.
    """
    try:
        # Hooks.java:13.  ``has_failed()`` rather than a comparison against a
        # single status member: see the module docstring's point 2.
        if scenario.status.has_failed():
            # Hooks.java:14.  The session is the one ``before_scenario``
            # published, not a fresh ``get_driver()`` that would open a browser
            # during teardown; unchecked, because ``capture_png`` suppresses.
            png = capture_png(
                context.driver,
                scenario_id=_scenario_identity(scenario),
            )

            if png is not None:
                # Hooks.java:15.  Raw bytes, not the base64 that
                # ``capture_failure_embedding`` returns: the collector encodes
                # once and supplies the name ``Context.attach`` has no slot for.
                context.attach(DEFAULT_MIME_TYPE, png)
    finally:
        # Hooks.java:17 - outside the ``if``, and here outside the ``try``:
        # every scenario tears down, whatever happened above.
        try:
            # ``quit_driver`` is the single owner of the session lifecycle and
            # of the OS-level containment behind it: it asks the browser to
            # close, then reclaims the driver executable's whole process tree
            # and *verifies* that nothing of it is left running.  A tree it
            # cannot confirm stopped is raised rather than returned, which is
            # why this call is the whole of this hook's teardown - there is no
            # second step here that could disagree with it, and nothing in this
            # module reaches a process.
            quit_driver()
        except BaseException:
            # Re-raised immediately; the handler exists only to add the one
            # thing this boundary knows and ``quit_driver`` does not - which
            # scenario the failure belongs to. A teardown that failed may have
            # left a browser running with the system under test's
            # authenticated session in it, and an operator reading a
            # ``HOOK-ERROR`` needs to know which scenario to look under. The
            # record carries the exception, so the reclamation diagnostic that
            # preceded it can be read alongside this line.
            logger.warning(
                "Scenario teardown failed for %s; a browser session may still "
                "be running",
                _scenario_identity(scenario) or "an unidentified scenario",
                exc_info=True,
            )
            raise
        finally:
            # Nested in its own ``finally`` because ``quit_driver`` can raise:
            # the reference to the session it just closed is cleared on every
            # path, and the failure still travels out of this hook.
            context.driver = None
