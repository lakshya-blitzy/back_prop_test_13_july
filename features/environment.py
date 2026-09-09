"""Scenario lifecycle for the Gherkin suite - the port of ``Hooks.java``.

behave discovers this module automatically at the root of the directory named
by ``behave.ini``'s ``paths`` key, which is ``features``; that is why the file
lives at ``features/environment.py`` and nowhere else.  The eleven Java
step-definition classes become ten step modules under ``features/steps/`` plus
this file, which carries the eleventh -- ``Hooks`` -- whose role is the
scenario lifecycle rather than step matching.

The behavioural source is eight lines long::

    @After                                          // Hooks.java:11
    public void teardownScenario(Scenario scenario){
        if(scenario.isFailed()){                    // :13
            byte [] screenshot = ((TakesScreenshot) Driver.getDriver())
                    .getScreenshotAs(OutputType.BYTES);          // :14
            scenario.attach(screenshot, "image/png", scenario.getName()); // :15
        }
        Driver.closeDriver();                       // :17 - OUTSIDE the if
    }

Three properties of those lines are load-bearing here:

1. **Teardown is unconditional.**  ``Driver.closeDriver()`` sits outside the
   ``if``, so it runs for every scenario, passed or failed.  Only the
   screenshot is conditional.  :func:`after_scenario` therefore calls
   :func:`~app.automation.quit_driver` from a ``finally`` block, so no failure
   while gathering evidence can skip it.  Getting this wrong leaks one browser
   process per failing scenario and, across a process pool, exhausts the host.
2. **The failure test covers exceptions as well as assertion failures.**
   Cucumber's ``Scenario.isFailed()`` is true for both, so the analogue is
   ``scenario.status.has_failed()`` -- behave's own helper, which unions
   ``failed``, ``error``, ``hook_error``, ``cleanup_error``, ``undefined`` and
   ``pending``.  An
   equality test against a single status member would silently drop the
   screenshot for every scenario that died on an exception rather than an
   assertion, which in a Selenium suite is most of them.
3. **The attachment carries bytes, a MIME type and the scenario's name.**
   behave's own ``context.attach(mime_type, data)`` takes only the first two;
   the result collector in ``app/reporting/events.py`` supplies the name from
   the scenario it is already tracking.  That split is why this module needs no
   import from the reporting package beyond the screenshot helper.

Why these are registered as real hooks
--------------------------------------
``Hooks.java:5`` imports ``@After`` from ``org.junit.After`` instead of
``io.cucumber.java.After``, so Cucumber never registers the hook and the Java
teardown **never runs** -- the reference ``target/cucumber.json`` corroborates
it by containing no ``embeddings`` and no ``"after"`` key anywhere.  The plan's
Conflict 6 corrects that defect rather than reproducing it (deviation 6:
*"the teardown hook is registered as a real hook, enabling failure screenshots
and per-scenario driver teardown"*), because reproducing it would ship the
framework's screenshot capability dead on arrival and leak a browser per
scenario.  **The canonical hook names below are therefore deliberate and must
not be renamed or disguised in the name of literal parity.**

The lifecycle contract, stated once
-----------------------------------
Each worker process holds one slot for a driver.  :func:`before_scenario`
creates a session into that slot if it is empty; :func:`after_scenario`
captures failure evidence, quits, and clears the slot.  So exactly one live
session exists per worker at any moment, every scenario gets a fresh session,
and no code ever touches a driver after ``quit()``.  This module,
``app/automation/driver.py`` and ``tests/test_driver.py`` all describe that
same contract.  It is the reason no step and no page object may create or quit
a driver: the lifecycle has exactly one owner, and this is it.

Hooks do not run under ``--dry-run``
------------------------------------
behave guards the whole scenario-hook block with ``if not
runner.config.dry_run``, so a dry run creates no driver and captures no
screenshot.  That is the faithful analogue of the Java runner's ``dryRun``
option, which also launches no browser, so nothing here compensates for it: no
driver is created outside a hook and ``before_all`` performs no warm-up.

Import boundary
---------------
Permitted: :mod:`app.automation` for the session lifecycle, the screenshot
module of the reporting package for the capture, :mod:`app.config` for the
userdata handshake, and the standard library.  Deliberately absent, each for a
reason: the Selenium bindings, which only ``app/automation`` may import - the
driver arrives here as an opaque object and nothing is called on it; the
port's logging-configuration module, whose only callers are the command-line
entry point and the application factory (and which this file would have no use
for in any case, since it emits no diagnostics of its own - every message this
lifecycle produces is logged by the module that owns the behaviour, the driver
holder or the screenshot helper, and duplicating them here would double every
line an operator reads); the page-object package, because step modules bind
their own pages per scenario; the run and report services, which sit above this
module in the
dependency graph; the properties reader, reached only through
:mod:`app.config`; the reporting event stream, since the result collector is
reached through behave's own ``context.attach`` and needs no cooperation from
this file; the web framework, because these hooks run in worker processes that
build no web application; and ``behave`` itself, whose model objects are passed
in rather than imported - which keeps this module importable, and
unit-testable, without the engine.

Nothing happens at import time beyond binding those names, and only the three
hooks below are defined: no ``after_all``, no feature-, step- or tag-scoped
hook, and no direct-execution entry point, because ``Hooks.java`` defines
exactly one hook and behave only ever imports this file.
"""

from __future__ import annotations

from typing import Any

from app.automation import get_driver, quit_driver
from app.config import set_userdata
from app.reporting.screenshots import DEFAULT_MIME_TYPE, capture_png

# Every name this module binds is used below, and it binds nothing else: no
# module logger, because nothing here logs (see the docstring's import-boundary
# note), and no state, because the one piece of per-scenario state - the
# session - lives on behave's context and in the driver holder's slot.
__all__ = ["after_scenario", "before_all", "before_scenario"]


def before_all(context: Any) -> None:
    """Install the run's behave userdata as this process's configuration overrides.

    Called once per worker process, before any feature is read.  Its whole job
    is the handshake that makes ``run-tests --browser <name>`` effective: the
    port's run service passes each worker the chosen browser
    as ``-D browser=<name>``, behave collects that into
    ``context.config.userdata``, and :func:`app.config.set_userdata` installs
    it in front of the properties file -- userdata first, then
    ``configuration.properties``.  That is the only override path in the port;
    there is no environment-variable layer to fall back on, so omitting this
    call would make the command-line option silently inert and send every
    worker to the properties file instead.

    Nothing else belongs here.  In particular no driver is created: behave
    skips the scenario hooks under ``--dry-run`` but still runs this one, and a
    session started here would survive as a browser nobody quits.  No logging
    is configured, no directory is created and no artifact is written -- the
    command-line entry point owns the first, and ``app/utils/paths.py`` owns
    every path in the port.

    :param context: behave's ``Context``.  Only ``context.config.userdata`` is
        read, and it is passed through as it is: an empty mapping simply leaves
        the file-only path in place, and no key is validated here.  An
        unrecognised browser name has to reach the driver and fail at first
        use, exactly as the source's missing ``default:`` branch arranges.
    :returns: ``None``.
    """
    set_userdata(context.config.userdata)


def before_scenario(context: Any, scenario: Any) -> None:
    """Open this worker's browser session and publish it on the context.

    ``Hooks.java`` has no ``@Before`` hook: the Java driver is created lazily by
    the first ``Driver.getDriver()`` call inside a step.  Creating it here
    changes nothing observable, because :func:`~app.automation.get_driver` is
    itself create-on-demand and every scenario in this suite navigates in its
    Background or its first step; what it does buy is a single, explicit place
    where a scenario's session begins, which is the half of the lifecycle
    contract that guarantees a fresh session per scenario once
    :func:`after_scenario` has cleared the slot.

    ``context.driver`` is set at behave's scenario scope, so it is discarded
    automatically when the scenario ends; :func:`after_scenario` clears it
    explicitly all the same, so that nothing can read a stale session even
    within the teardown itself.

    :param context: behave's ``Context``.  Receives the ``driver`` attribute
        that steps and page objects read.
    :param scenario: The scenario about to run.  Not inspected: no tag, name or
        status changes what happens here, because the Java lifecycle draws no
        such distinction.
    :returns: ``None``.

    .. note::
       ``get_driver()`` legitimately returns ``None`` -- the source switches on
       the ``browser`` property with cases ``"chrome"`` and ``"firefox"`` only
       and no default branch, so an unrecognised value yields no session.  That
       value is published as it is: **nothing here validates the browser name,
       raises on ``None`` or substitutes a default.**  The failure has to
       surface at the point of use, which is what makes a configuration
       mistake diagnosable in the step that needed the browser rather than in
       a hook that hid it.
    """
    context.driver = get_driver()


def after_scenario(context: Any, scenario: Any) -> None:
    """Capture a failed scenario's screenshot, then always quit the session.

    The port of ``Hooks.java:12-18``, in the order the source fixes:

    1. When the scenario failed, photograph the still-live session and attach
       the PNG to the report.
    2. **Unconditionally** quit the driver and empty the slot.

    Step 2 runs from a ``finally`` block, so a problem in step 1 cannot leak a
    browser -- the single most important property of this function.  Step 1
    necessarily precedes it: a screenshot needs a live session, and once
    :func:`~app.automation.quit_driver` returns there is nothing left to
    photograph.

    Capture happens on failure only and exactly once, with no enable flag.
    ``README.md`` claims screenshots for passing tests "if you enable it", but
    no setting and no branch in ``Hooks`` provides one, so under the plan's
    precedence rule that claim is aspirational and the configuration surface
    stays at its six keys.

    :param context: behave's ``Context``.  ``context.driver`` supplies the
        session to photograph and is cleared before this returns.
    :param scenario: The finished scenario.  Its ``status`` decides whether
        evidence is gathered and its ``name`` is what the result collector
        records as the attachment's name.
    :returns: ``None``.

    .. note::
       **Nothing here changes a scenario's outcome.**  The status is read and
       never written, and no result is marked failed, passed or skipped.  A
       screenshot is evidence about a result, never part of one.
    """
    try:
        # Hooks.java:13.  ``has_failed()`` rather than a comparison against a
        # single status member: see the module docstring's point 2.
        if scenario.status.has_failed():
            # Hooks.java:14.  The session comes from the context - the one
            # ``before_scenario`` published - and deliberately NOT from a fresh
            # ``get_driver()`` call, which is create-on-demand and would start
            # a browser during teardown just to photograph a blank page.
            #
            # It is passed on without a ``None`` check, and without a
            # ``try``/``except`` of any kind.  Suppression has exactly one
            # owner: ``capture_png`` logs and returns ``None`` for a dead
            # session, for an object that cannot be photographed at all (the
            # ``None`` a mis-configured browser leaves behind) and for an
            # unusable payload.  Duplicating that here would hide real
            # defects, and the plan's deviation 19 places the behaviour there
            # rather than in the caller.
            png = capture_png(context.driver)

            if png is not None:
                # Hooks.java:15.  behave's own embedding protocol: the runner
                # forwards this to every formatter exposing ``embedding``,
                # which is how the result collector receives it without this
                # module importing the reporting event stream.
                #
                # Raw PNG bytes, not the base64 string that
                # ``capture_failure_embedding`` returns: ``Context.attach``
                # documents its payload as a bytes-like object and behave's own
                # JSON formatter base64-encodes whatever it is handed, so a
                # pre-encoded string would be encoded twice there.  The
                # collector accepts the bytes, encodes them once, and supplies
                # the third argument of the Java ``attach`` call -- the
                # scenario's name -- from the scenario it is already tracking,
                # since ``Context.attach`` has no name parameter.  The MIME
                # type is the screenshot module's constant, which is
                # ``"image/png"`` verbatim from the source.
                context.attach(DEFAULT_MIME_TYPE, png)
    finally:
        # Hooks.java:17 - outside the ``if``, and here outside the ``try``:
        # every scenario tears down, whatever happened above.
        try:
            quit_driver()
        finally:
            # The slot is emptied by ``quit_driver`` itself; this clears the
            # context's reference to the session it just closed, so no later
            # reader can reach a quit driver. Nested in its own ``finally`` so
            # that it happens even in the impossible case of ``quit_driver``
            # raising -- which it does not, by design -- while still letting
            # such an error propagate rather than swallowing it.
            context.driver = None
