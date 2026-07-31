"""Dashboard assertion surface of the ported Page Object Model layer.

This module answers exactly one question -- "is the dashboard displayed?" -- for exactly one
step of the migrated Gherkin specification. It is the third and last member of the Page Object
Model layer this migration adopts, whose purpose the transformation plan states in a single
line: "Encapsulates locators so step definitions contain no selectors." The dashboard's
locator therefore lives here and nowhere else, and the step definition that consumes it holds
no selector of its own.

The one step this module serves
==============================
::

    Then User should see the dashboard

That is the fourth and final step of the first documented scenario outline, and it is the ONLY
mention of a dashboard anywhere in the 45-line specification: there is no dashboard navigation
step, no heading check, no widget, menu, table or tile, and no log-out step. The published
surface below is sized to match and deliberately stops there, because the migration's
instruction is that no feature may be dropped and none may be added.

A corrected line citation: the step is at L120, not L121
=======================================================
The transformation plan records this module's source as ``[README.md:L121]``, and
``docs/testing.md`` repeats that citation. BOTH ARE OFF BY ONE. This note exists so that no
future reader spends time hunting for a step on line 121.

``[README.md:L120]`` is the dashboard step, indented by four spaces. Line 121 is
WHITESPACE-ONLY: it holds exactly two space characters and nothing else. Nor is it stray
formatting -- it is one of three whitespace-only lines inside the quoted specification block,
the others holding four and six spaces, and all three are load-bearing for a different file.
``tests/features/login.feature`` is a byte-exact copy of that block, so its 1864 bytes depend
on those lines surviving untouched. They are mentioned here only to explain the off-by-one.

Every citation in this file names ``[README.md:L120]``. Two secondary locations are worth
recording, because either one is easy to mistake for a contradiction of that citation: it
indexes the PRE-MIGRATION ``README.md``, and the rewritten one carries the same verbatim block
further down the file; and in the materialised feature file the same step is line 17.
``docs/testing.md`` is a sibling deliverable and is deliberately left alone -- correcting the
record here is the part of the fix that belongs to this file.

The locator is an unverified best-effort derivation (risk R6)
============================================================
The application under test is a third-party, French-language web application. It is external,
unmodifiable and unreachable from this project's verification environment, carried as risk R6
rather than papered over. Its dashboard has never been loaded from here, so the locator below
cannot be, and is not claimed to be, verified against the live page.

It is derived from the only evidence that exists: the noun in the step text. The owners must
confirm it against the running application, and when they do, exactly one line of this file
changes -- which is precisely why the selector belongs inside a page object. No claim of any
kind is made here about the real page's structure. A plausible identifier is not a finding,
and presenting one as though it were would be worse than admitting the gap.

This check is not expected to succeed here, and that is correct (defect D1)
==========================================================================
The dashboard is reachable from one scenario only: the outline tagged ``@UPGN-286``. That
outline has four steps and NO ``Examples`` table of its own, because the Gherkin grammar
attaches both tables in the block to a later outline. Its credential steps therefore run with
the LITERAL placeholder text of the step -- angle brackets included -- and, being typed into
fields that accept any characters, they pass while doing so; the generated report records
those literal step names verbatim.

So the flow preceding this check submits placeholder text rather than a real credential, and
against the live application a dashboard would never appear. Combined with R6 above, the
honest statement is this: the check below is NOT expected to succeed in this environment.

That is deliberate, and it is preserved. The catalogued defect is D1; the migration reads
"fully matches the behavior and logic of the current implementation" literally, and "defects
are behavior" is a stated rule of the port. ``docs/migration-parity.md`` is the authoritative
register of D1 and of every other preserved defect -- consult it before changing anything
here. Nothing below weakens the check to make it green: there is no hard-coded answer, no
zero-length budget used as a shortcut, no broadened exception handling and no switch that
relaxes the comparison. Neither is there a test double or a stand-in for the application under
test, because such a thing would turn a genuinely unrunnable scenario green and prove nothing.

A second preserved defect, D2, is worth naming for context: the runner's preserved tag
expression selects a tag no scenario carries, so a default run deselects all six collected
tests and this check never executes at all. Run exactly as documented, the suite executes
nothing -- and the runner reports that as success, which is what the pre-migration pipeline
did.

This module returns a boolean; it never asserts
==============================================
:meth:`DashboardPage.is_displayed` answers ``True`` or ``False`` and leaves the verdict to its
caller. The Java build being replaced declared ``junit:junit`` 4.13.2 ``[pom.xml:L71-L75]``,
and this migration maps that library's assertion calls onto a bare ``assert`` statement in
``tests/step_defs/login_sd.py``, relying on the test runner's assertion rewriting for the
failure message rather than introducing an assertion library. The step definition therefore
writes the check as a one-liner over the value returned here.

Consequently this module raises nothing of its own, and it never skips either: turning an
unbuildable browser session into a skipped scenario is the job of the harness fixture in
``tests/step_defs/conftest.py``. A page object reports; it does not decide the outcome of a
run.

Selenium is imported lazily, and that is a correctness requirement
=================================================================
Not one Selenium name is imported when this module is imported. The single strategy constant
it needs is imported inside :func:`dashboard_locators`, the only function that references it,
and the locator value is built on first USE rather than at import time -- which is why that
function exists at all instead of a module-level constant.

The reason is availability rather than micro-optimisation. Selenium is pinned in
``requirements-test.txt``, but a verification environment is not guaranteed to have network
access, so a successful installation cannot be assumed -- and this migration's acceptance gate
is a parity suite that needs no third-party distribution at all. With the import at module
scope, or with the locator built eagerly, one absent distribution would become a collection
error for the entire harness. Importing this module must therefore never fail. Asking it to
build a locator or drive a browser without Selenium installed does fail, loudly and at the
point of use, which is the right place for that failure.

Only the Selenium 4 API appears below. The source build pinned
``org.seleniumhq.selenium:selenium-java`` 3.141.59 ``[pom.xml:L36-L40]`` and this port pins
``selenium`` 4.46.0 -- a deliberate major-version uplift carried as risk R2, whose mitigation
is a boundary rather than a workaround: browser interaction stays inside ``tests/pages/*`` and
``tests/support/driver_factory.py``. Selenium 3's per-locator accessor methods and its
driver-constructor keywords were deleted in Selenium 4 and appear nowhere here.

What this module does NOT own
============================
Waiting belongs to :class:`~tests.pages.base_page.BasePage`, which owns all of it and uses
explicit waits only; nothing here builds a wait, pauses for a fixed interval or configures an
implicit budget. Building and disposing of a browser session belongs to
``tests/support/driver_factory.py``: the driver arrives already constructed through the
inherited constructor, which is not overridden. The login locators, the exactly-compared
French validation message and the error-message surface belong to
``tests/pages/login_page.py`` -- the specification's other two ``Then`` steps,
``[README.md:L128]`` and ``[README.md:L135]``, are its steps and not this module's, and not
one of its values is duplicated here. Screen shots and error shots have their own owners under
``tests/support`` and ``app/reporting``. And this module reads no configuration whatsoever: no
process environment, no environment file and no properties file.

Usage
=====
::

    from tests.pages.dashboard_page import DashboardPage

    dashboard = DashboardPage(driver)
    displayed = dashboard.is_displayed()        # the instance's default wait budget
    quick = dashboard.is_displayed(2.0)         # a short budget when "no" is expected
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Final

from tests.pages.base_page import BasePage, Locator

__all__ = [
    "DashboardLocators",
    "DashboardPage",
    "dashboard_locators",
    "logger",
]

# Structured logging only. The logger is obtained here and never configured: handler, level
# and format belong exclusively to ``app/logging_config.py``, and nothing in this module
# writes to a stream directly or mutates the logging hierarchy.
logger: Final[logging.Logger] = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DashboardLocators:
    """The dashboard's locators: one strategy/value pair, because one question is asked.

    Frozen and slotted on purpose. A locator set is a value rather than a mutable
    configuration object, and the harness parallelises across worker processes that each build
    their own copy; freezing it means no page object can rewrite a selector another scenario
    depends on, and slotting keeps it cheap.

    A typed container rather than a loose module constant, so that the accessor below can
    build the whole set in one place, lazily, and hand callers something self-describing.

    Attributes:
        dashboard_root: Identifies the element whose visibility answers
            ``Then User should see the dashboard`` ``[README.md:L120]``. UNVERIFIED: derived
            from the noun in that step text, not from the live application, which is external
            and unreachable (risk R6), so the owners must confirm it. It is the only locator
            this module declares -- there is no fallback pair and no candidate list, because a
            second guess would be a second unverified claim rather than extra safety.
    """

    dashboard_root: Locator


@lru_cache(maxsize=1)
def dashboard_locators() -> DashboardLocators:
    """Build the dashboard's locator set once, on first use, and cache it.

    A function rather than a module constant, and cached rather than rebuilt, for one reason:
    the strategy vocabulary lives in Selenium, and importing it at module scope would make
    this module unimportable wherever Selenium is absent. The import below therefore runs on
    the first call and never at import time. See the module docstring for why that matters.

    ``maxsize=1`` because the result is a singleton value with no arguments to vary: the first
    call builds it and every later call returns the identical object.

    Returns:
        The frozen, single-locator set serving ``[README.md:L120]``.

    Raises:
        ModuleNotFoundError: If Selenium is not installed. Raised at the point of use by the
            import below, rather than being caught and answered with a substitute locator: a
            locator this module cannot build is not one it should invent.
    """
    # Run-time import, inside the only function that needs the strategy vocabulary. After the
    # first call this is a module-cache lookup. Selenium 4's ``By`` members are plain strings,
    # so the pair below is exactly the ``tuple[str, str]`` shape the base class's wait
    # predicates expect and needs no conversion at the call site.
    from selenium.webdriver.common.by import By

    # UNVERIFIED best-effort derivation from the step text at ``[README.md:L120]``: that step
    # names exactly one thing, the dashboard, so the landing page's root element is looked up
    # by that identifier. ``By.ID`` is chosen as the most stable of Selenium's strategies; no
    # absolute path expression is used, and nothing is keyed on the application's French
    # wording, which is outside this project's control and belongs to another page in any
    # case. If the live application anchors its dashboard differently, this one line changes.
    return DashboardLocators(dashboard_root=(By.ID, "dashboard"))


class DashboardPage(BasePage):
    """Page object for the dashboard a user lands on after a successful login.

    Inherits everything it needs from :class:`~tests.pages.base_page.BasePage`: the injected
    driver, the default wait budget and the explicit-wait helpers. The inherited constructor is
    correct as it stands -- it validates the budget, stores two attributes and performs no
    Selenium call of any kind -- so it is deliberately NOT overridden, and instantiating this
    class stays side-effect free even in a process where Selenium is absent.

    One public method, because the specification asks one question. Everything a richer
    dashboard page might offer -- reading a heading or a welcome line, opening a menu, logging
    out, reaching a widget or a table, capturing an image -- is absent on purpose: no
    documented step needs any of it, and this migration forbids adding a feature as firmly as
    dropping one.

    Usage::

        DashboardPage(driver).is_displayed()
    """

    def is_displayed(self, timeout: float | None = None) -> bool:
        """Report whether the dashboard became visible within the budget.

        This is the whole of ``Then User should see the dashboard`` ``[README.md:L120]``
        expressed in Python. It delegates to the base class's visibility query, the one
        inherited helper that converts an expired wait into a value instead of an exception: a
        caller asking "is this displayed?" needs to be told "no" rather than to be interrupted.
        That query catches a single, named timeout class and nothing wider, so a malformed
        locator or a lost session still propagates -- and this method neither re-implements nor
        broadens it.

        The verdict is returned, never enforced. ``tests/step_defs/login_sd.py`` turns the
        value into a one-line check, which is how this migration ports the Java build's
        assertion calls ``[pom.xml:L71-L75]``.

        Not expected to succeed in this project's verification environment, for two documented
        reasons that are preserved rather than repaired: the only scenario reaching the
        dashboard submits the literal placeholder text of its own steps (defect D1), and the
        application under test is external and unreachable (risk R6). See the module docstring
        and ``docs/migration-parity.md``.

        Args:
            timeout: Per-call budget in seconds, or ``None`` to use the instance default the
                constructor was given. Because a negative answer costs the full budget, pass a
                short value whenever "no" is the expected answer.

        Returns:
            ``True`` if the dashboard's root element became visible inside the budget,
            ``False`` if the wait expired.

        Raises:
            ValueError: If ``timeout`` is supplied and is not a positive number of seconds.
                Propagated from the base class: a nonsensical budget is a defect in the caller
                rather than an invisible dashboard, so reporting it as a negative answer would
                hide it.
            ModuleNotFoundError: If Selenium is not installed, raised while the locator is
                built on first use.
        """
        locator = dashboard_locators().dashboard_root
        displayed = self.is_visible(locator, timeout)
        logger.debug("Dashboard root located by %r visible: %s", locator, displayed)
        return displayed
