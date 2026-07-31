"""Page Object base class -- the single owner of waiting in the ported BDD harness.

:class:`BasePage` is the root of the Page Object Model layer this migration adopts, and its
job is stated in one line by the transformation plan that commissioned it: "Page Object base
with explicit waits". The pattern's purpose is equally explicit -- "Encapsulates locators so
step definitions contain no selectors" -- so a page's structural knowledge stays in one
place and the step definitions read as prose.

ADDITIVE, and honest about it
=============================
Nothing in the pre-migration project corresponds to this module. The Java build it was
ported from declared a browser-automation dependency and nothing more; its Java sources were
never committed, so there is no page-object class to read and none is claimed here. This
module is a MATERIALISATION of a documented specification rather than a translation of
committed code. Every value it defaults to is either derived from a cited source construct
or flagged ADDITIVE at its definition, and nothing is supplied from imagination.

This module owns 100% of waiting, and only explicit waits
=========================================================
The harness's driver factory, ``tests/support/driver_factory.py``, is contractually
forbidden from configuring an implicit wait or a page-load budget, and states as much in its
own documentation. Waiting therefore lives here, in full, and it is always EXPLICIT: a
``WebDriverWait`` paired with an ``expected_conditions`` predicate, evaluated against a
budget the caller may override per call.

That division is not a stylistic preference. Selenium's implicit wait and its explicit waits
interact badly: with an implicit wait configured, a single element lookup performed inside an
explicit wait's predicate blocks for the implicit budget before reporting failure, so the
explicit budget silently becomes some multiple of the number it advertises. Blending the two
would change the semantics of every wait downstream of this class. For the same reason no
method here sleeps for a fixed interval, and no method consults a clock directly: the wait
object owns the polling loop and the deadline.

Selenium is imported lazily, and that is a correctness requirement
=================================================================
Not one Selenium name is imported when this module is imported. Names needed at run time are
imported inside the method that uses them; names needed only by the type checker live in the
``TYPE_CHECKING`` block below, paired with the deferred-annotation future import so that
every annotation naming a Selenium class is a string as far as the interpreter is concerned.

The reason is availability rather than micro-optimisation. Selenium is pinned in
``requirements-test.txt``, but a verification environment is not guaranteed to have network
access, so a successful installation cannot be assumed -- and this migration's acceptance
gate is a parity suite that needs no third-party distribution at all. With the import at
module scope, one absent distribution would become a collection error for the entire
harness. Importing this module must therefore never fail. Asking it to drive a browser
without Selenium installed may fail, and does, loudly and at the point of use. A repeated
in-method import costs one dictionary lookup after the first, which is the right price for
that guarantee.

The driver is injected; it is never built and never disposed of here
===================================================================
Every instance receives an ALREADY-CONSTRUCTED driver through its constructor. This module
provisions no browser, builds no session, disposes of nothing and defines no finaliser hook.
Construction and disposal belong to ``tests/support/driver_factory.py``, invoked from the
harness's fixtures; turning an unbuildable driver into a skipped scenario belongs to
``tests/step_defs/conftest.py``. Construction here is side-effect free -- no Selenium call of
any kind happens until a method is invoked -- so a subclass can be instantiated cheaply,
including inside a process where Selenium is absent.

No selectors, no assertions, no configuration
=============================================
Not one locator value is defined in this file. Locators are data owned by the concrete pages,
``tests/pages/login_page.py`` and ``tests/pages/dashboard_page.py``, and they arrive here as
ready-made :data:`Locator` pairs. Neither does this class assert: it exposes queryable state
and ``tests/step_defs/login_sd.py`` does the asserting with a bare ``assert`` statement,
which is how the migration ports the Java build's JUnit assertions rather than introducing
an assertion library. And it reads no configuration at all -- no process environment, no
environment file, no properties file -- because the harness has a single cached reader for
that and a second reader here would create a competing source of truth. The wait budget
arrives as a constructor argument; see :data:`DEFAULT_TIMEOUT_SECONDS`.

The application under test is external and unreachable (risk R6)
================================================================
The application these page objects drive is a third-party, French-language web application.
It is external, unmodifiable and unreachable from this project's verification environment,
recorded as risk R6 rather than papered over: the browser-driven scenarios cannot run end to
end here. Two consequences follow, and both are stated plainly because a future maintainer
deserves the truth rather than an implication.

First, the locators in the concrete page classes are unverified best-effort derivations from
the documented step text, and the owners must confirm them against the live application.
Second, this project's parity evidence is structural -- collection counts, artifact
production, report-schema conformance, constant equality and exit-code mapping -- and never
an end-to-end browser assertion. Nothing here manufactures a session it could not open:
there is no test double, no simulated browser and no stand-in for the application under
test, because such a thing would turn a genuinely unrunnable scenario green and prove
nothing.

Selenium 3 to Selenium 4 is a major-version uplift (risk R2)
============================================================
The source build pinned ``org.seleniumhq.selenium:selenium-java`` 3.141.59
``[pom.xml:L36-L40]``; this port pins ``selenium`` 4.46.0. That is a deliberate, documented
major-version uplift and not a like-for-like swap: Selenium 4 changed the API surface
materially and no Python binding equivalent to the Selenium 3 Java API is in current
support. The migration carries it as risk R2, whose mitigation is a boundary rather than a
workaround -- browser interaction is confined to ``tests/pages/*`` and
``tests/support/driver_factory.py``, so the surface exposed to the API difference stays small
and auditable.

Only the Selenium 4 API appears below. Elements are located through the two-argument lookup
that takes a strategy and a value; Selenium 3's per-locator accessor methods and its
driver-constructor keywords were deleted in Selenium 4 and appear nowhere in this file. That
is enforced by more than review: ``selenium`` ships a type marker and is deliberately absent
from the type checker's missing-import allowances, so every Selenium call here is checked
with no escape hatch and a Selenium-3-shaped call fails the quality gate rather than merely
failing at run time.

Preserved behaviour reaches exactly two methods of this class
============================================================
This migration preserves the catalogued defects of the system it ports, deliberately: they
are behaviour, and the instruction to "fully match the behavior and logic of the current
implementation" is read literally. Two of those defects constrain methods below, and each
carries a comment at the exact line that honours it:

* :meth:`BasePage.type_into` passes its ``text`` argument through completely untouched,
  because one documented scenario has no data table of its own and therefore types the
  literal placeholder text -- and passes while doing so (defect D1).
* :meth:`BasePage.read_text` returns what Selenium hands it, verbatim, because the message a
  later scenario asserts has to compare exactly, down to its trailing full stop (defect D5).

``docs/migration-parity.md`` is the authoritative register of every preserved defect and of
the full source-to-Python mapping. Consult it before changing either method: making them
"helpful" would silently repair behaviour this project exists to reproduce.

Usage
=====
A subclass owns its locators -- see :data:`Locator` for the shape, and ``login_page.py`` for
real values -- and expresses page behaviour in terms of the methods below. It never
re-implements a wait, and it never touches the driver directly::

    from tests.pages.base_page import BasePage, Locator


    class SomePage(BasePage):
        # The strategy/value pair is declared, and given its real value, by the concrete
        # page itself -- never by this base class. See login_page.py for actual values.
        HEADING: Locator

        def read_heading(self) -> str:
            return self.read_text(self.HEADING)

        def is_loaded(self) -> bool:
            return self.is_visible(self.HEADING)

The published surface, and why it is exactly this size
======================================================
The documented scenarios require six capabilities -- navigate, set a username, set a
password, click, read an error message and read a validation message -- plus the dashboard's
is-displayed check. The methods below cover all of them and stop there:

``navigate_to``
    Load a URL.
``current_url``
    Read the browser's current address.
``find`` / ``find_visible``
    Wait for presence / for visibility, and return the element.
``click``
    Wait for clickability, then click.
``type_into``
    Wait for visibility, optionally clear, then send text through unchanged.
``read_text``
    Wait for visibility, then return the element's text unchanged.
``is_visible``
    Answer whether an element became visible inside the budget.
``wait_until``
    Wait on any caller-supplied predicate, so a subclass never re-implements the wait.

Nothing else belongs here. Dropdown selection, scrolling, frame and alert handling, hovering,
screen-shot capture, retry decorators and reporting decorators are all absent on purpose: no
documented capability needs them, and the migration's instruction is that no feature may be
dropped and none may be added. Screen shots and error shots in particular have their own
owners in ``tests/support/screenshots.py`` and ``app/reporting/screenshots.py``.

Errors are honest. Every method other than :meth:`BasePage.is_visible` lets the Selenium
exception propagate, so a failing step fails for real and the generated report records a
real failure. No method swallows a failure into a ``None`` return or a sentinel string, and
the one place an exception IS caught catches a single, named exception class and nothing
wider.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Final, TypeVar

if TYPE_CHECKING:
    # Type-only imports: resolved by the type checker, never executed. Paired with the
    # deferred-annotation future import above, so every annotation naming one of these
    # classes stays a string at run time and costs nothing when Selenium is absent.
    from collections.abc import Callable

    from selenium.webdriver.remote.webdriver import WebDriver
    from selenium.webdriver.remote.webelement import WebElement
    from selenium.webdriver.support.wait import WebDriverWait

__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "BasePage",
    "Locator",
    "logger",
]

# A Selenium locator: the (strategy, value) pair every lookup and every wait predicate takes.
#
# Both members are plain strings because Selenium's locator strategies are string constants
# rather than an enumeration -- By.ID is the string "id", By.NAME is "name" and
# By.CSS_SELECTOR is "css selector" -- which is why the alias is a pair of ``str`` and not a
# pair of some richer type. Selenium's own wait predicates annotate their argument as exactly
# ``tuple[str, str]``, so this alias is structurally identical to what they expect and
# needs no conversion at the call site.
#
# The strategy vocabulary itself is deliberately NOT imported here: this base class defines
# no locator, so it has no use for the constants. The concrete pages import them, pair them
# with their own values and hand the finished pairs to the methods below. Declaring the alias
# here rather than in each page keeps one spelling of the shape for the whole layer.
Locator = tuple[str, str]

# The default explicit-wait budget, in seconds.
#
# ADDITIVE: the pre-migration project specifies no timeout anywhere -- not in its build
# definition, not in its pipeline definition and not in its documentation -- so there is no
# source value to preserve and none is invented. Ten seconds is a conservative default for a
# page-object base, and it exists to be overridden rather than to be authoritative: the
# constructor takes it as an argument, and every wait-bearing method takes a per-call
# override.
#
# A configurable explicit-wait budget IS part of the migration's documented configuration
# surface -- the committed environment and properties templates and ``docs/configuration.md``
# publish a key for it, whose documented value is larger than this fallback. Reading that key
# is the CALLER's job, performed by the harness fixture that builds a page object; this module
# reads no configuration whatsoever, so the two never disagree about ownership. Pass the
# resolved value to the constructor and this default never comes into play.
DEFAULT_TIMEOUT_SECONDS: Final[float] = 10.0

# Structured logging only. The logger is obtained here and never configured: handler, level
# and format belong exclusively to ``app/logging_config.py``, and nothing in this module
# writes to a stream directly or mutates the logging hierarchy.
logger: Final[logging.Logger] = logging.getLogger(__name__)

# The value a caller-supplied wait predicate resolves to. Module-private: it is an
# implementation detail of :meth:`BasePage.wait_until`'s signature and not part of the
# published surface.
_T = TypeVar("_T")


class BasePage:
    """Base class for every page object in the harness; owner of all explicit waiting.

    A page object holds the knowledge of ONE page: where its elements are and what a caller
    can do with them. Subclasses declare their locators as :data:`Locator` constants and
    express behaviour through the methods below, so that the step definitions that drive them
    contain no selector and no wait of their own.

    The driver is injected. An instance never builds, configures, restarts or disposes of a
    browser session; it borrows one for the duration of a scenario, and the harness fixture
    that created the session is the only thing that ends it.

    Construction is side-effect free: it validates its wait budget, stores two attributes and
    performs no Selenium call whatsoever. That is what makes a subclass cheap to instantiate,
    and what lets this module be imported and exercised in a process where Selenium is not
    installed at all.

    Attributes:
        driver: The already-constructed Selenium WebDriver this page drives. Public because
            subclasses legitimately need it for the rare interaction the published surface
            does not cover, and because :meth:`wait_until` hands it to caller predicates.
        timeout: The default explicit-wait budget in seconds, applied whenever a method is
            called without a per-call ``timeout``.

    Thread safety:
        An instance carries no mutable state beyond the two attributes above, which are set
        once at construction and never reassigned, so it is safe to share for reading.
        A Selenium session, however, is single-threaded by nature: one browser cannot serve
        two callers at once. The harness therefore parallelises across worker PROCESSES, each
        with its own session and its own page objects, which is exactly the execution model
        the source build's method-level parallelism maps onto.
    """

    def __init__(self, driver: WebDriver, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        """Bind this page object to an already-constructed driver.

        Args:
            driver: A live Selenium WebDriver, created and owned by
                ``tests/support/driver_factory.py`` and handed over by the harness fixture.
                It is stored as-is: no capability is altered, no wait is configured on it and
                no navigation is performed.
            timeout: Default explicit-wait budget in seconds for every wait-bearing method.
                Defaults to :data:`DEFAULT_TIMEOUT_SECONDS`.

        Raises:
            ValueError: If ``timeout`` is not a positive number of seconds. A zero, negative
                or not-a-number budget cannot express "wait for this", and failing at
                construction is far cheaper to diagnose than a scenario that mysteriously
                never waits.
        """
        # ``not timeout > 0`` rather than ``timeout <= 0``, so that a not-a-number budget is
        # rejected too: NaN compares False against every bound, so ``<= 0`` would wave it
        # through and the wait loop would then treat its deadline as already past.
        if not timeout > 0:
            raise ValueError(
                f"timeout must be a positive number of seconds, got {timeout!r}",
            )

        self.driver: WebDriver = driver
        self.timeout: float = timeout

    # -------------------------------------------------------------------------------------
    # Internal helpers. Both are deliberately private: they are the seam that keeps every
    # public method free of duplicated budget arithmetic and duplicated wait construction.
    # -------------------------------------------------------------------------------------

    def _resolve_timeout(self, timeout: float | None) -> float:
        """Return the budget a call should use, falling back to the instance default.

        Args:
            timeout: A per-call override in seconds, or ``None`` to use :attr:`timeout`.

        Returns:
            The number of seconds the wait should be given.

        Raises:
            ValueError: If ``timeout`` is supplied and is not a positive number of seconds.
                The same reasoning as in the constructor applies, and applying it here too
                means a nonsensical per-call override cannot quietly degrade into an
                immediate timeout that looks like a missing element.
        """
        if timeout is None:
            return self.timeout

        if not timeout > 0:
            raise ValueError(
                f"timeout override must be a positive number of seconds, got {timeout!r}",
            )

        return timeout

    def _wait(self, timeout: float) -> WebDriverWait[WebDriver]:
        """Build a fresh explicit wait bound to the injected driver.

        A new wait object per call is intentional: the deadline is measured from the moment
        the wait is created, so a shared instance would hand later calls a budget that had
        already partly elapsed. Constructing one is trivially cheap.

        Args:
            timeout: An already-resolved, already-validated budget in seconds, as returned by
                :meth:`_resolve_timeout`.

        Returns:
            A wait object that polls the injected driver until its predicate is satisfied or
            the budget expires.
        """
        # Run-time import, inside the method that needs it: see the module docstring. After
        # the first call this is a module-cache lookup, and it is what allows this file to be
        # imported in an environment where Selenium is not installed.
        from selenium.webdriver.support.wait import WebDriverWait

        return WebDriverWait(self.driver, timeout)

    # -------------------------------------------------------------------------------------
    # Navigation and browser state.
    # -------------------------------------------------------------------------------------

    def navigate_to(self, url: str) -> None:
        """Load ``url`` in the injected driver.

        Named ``navigate_to`` rather than the shorter verb ``open``, which would shadow a
        builtin of that name for every reader of a subclass.

        No wait is applied and none is needed: the driver's own load semantics block until the
        document is ready, and layering a budget on top of that would duplicate the
        page-load timeout this harness deliberately leaves unset. Callers that need a specific
        element before proceeding follow this with :meth:`find_visible` or :meth:`is_visible`.

        Args:
            url: The address to load. Passed through unchanged; this method neither validates
                nor rewrites it, because the address of the external application under test is
                configuration the caller owns and no address for it exists in the source
                project to validate against.
        """
        logger.debug("Navigating to %s", url)
        self.driver.get(url)

    @property
    def current_url(self) -> str:
        """The address currently loaded in the browser.

        Exposed as a read-only property so a subclass can assert on where a flow ended up --
        a successful login landing on a dashboard, for instance -- without reaching into the
        driver itself.

        Returns:
            The current address, exactly as the driver reports it.
        """
        return self.driver.current_url

    # -------------------------------------------------------------------------------------
    # Element lookup. Every one of these waits explicitly; none of them ever sleeps.
    # -------------------------------------------------------------------------------------

    def find(self, locator: Locator, timeout: float | None = None) -> WebElement:
        """Wait for an element to be PRESENT in the DOM and return it.

        Presence is the weaker of the two conditions this class offers: the element exists in
        the document but may be hidden, zero-sized or still animating in. Use it when the
        element's existence is the question; use :meth:`find_visible` when the caller intends
        to read or interact with it.

        Args:
            locator: The (strategy, value) pair identifying the element.
            timeout: Per-call budget in seconds, or ``None`` for :attr:`timeout`.

        Returns:
            The located element.

        Raises:
            TimeoutException: If no matching element appears within the budget. Deliberately
                propagated rather than converted into ``None``, so a step definition fails for
                real and the generated report records a real failure.
            ValueError: If ``timeout`` is supplied and is not positive.
        """
        from selenium.webdriver.support import expected_conditions

        budget = self._resolve_timeout(timeout)
        logger.debug("Waiting up to %s s for presence of element located by %r", budget, locator)
        return self._wait(budget).until(expected_conditions.presence_of_element_located(locator))

    def find_visible(self, locator: Locator, timeout: float | None = None) -> WebElement:
        """Wait for an element to be VISIBLE and return it.

        Visibility means present in the DOM and displayed with a non-zero size, which is the
        precondition for reading text from an element or typing into one. This is the lookup
        the other interaction methods build on.

        Args:
            locator: The (strategy, value) pair identifying the element.
            timeout: Per-call budget in seconds, or ``None`` for :attr:`timeout`.

        Returns:
            The visible element.

        Raises:
            TimeoutException: If no matching element becomes visible within the budget.
                Propagated on purpose; :meth:`is_visible` is the method to call when "no" is
                an acceptable answer.
            ValueError: If ``timeout`` is supplied and is not positive.
        """
        from selenium.webdriver.support import expected_conditions

        budget = self._resolve_timeout(timeout)
        logger.debug("Waiting up to %s s for visibility of element located by %r", budget, locator)
        return self._wait(budget).until(expected_conditions.visibility_of_element_located(locator))

    # -------------------------------------------------------------------------------------
    # Interaction.
    # -------------------------------------------------------------------------------------

    def click(self, locator: Locator, timeout: float | None = None) -> None:
        """Wait for an element to be CLICKABLE, then click it.

        Clickability is stricter than visibility: the element must also be enabled. Waiting
        for it is what makes a click on a button that is still being wired up reliable without
        a fixed pause anywhere in the harness.

        Args:
            locator: The (strategy, value) pair identifying the element.
            timeout: Per-call budget in seconds, or ``None`` for :attr:`timeout`.

        Raises:
            TimeoutException: If the element does not become clickable within the budget.
            ValueError: If ``timeout`` is supplied and is not positive.
        """
        from selenium.webdriver.support import expected_conditions

        budget = self._resolve_timeout(timeout)
        logger.debug("Waiting up to %s s to click element located by %r", budget, locator)
        element = self._wait(budget).until(expected_conditions.element_to_be_clickable(locator))
        element.click()

    def type_into(
        self,
        locator: Locator,
        text: str,
        *,
        clear_first: bool = True,
        timeout: float | None = None,
    ) -> None:
        """Wait for a field to be visible, optionally clear it, then type ``text`` into it.

        PRESERVED DEFECT D1 -- read before changing this method. ``text`` is handed to
        Selenium EXACTLY as received. It is not trimmed, case-folded, quote-stripped,
        length-checked, pattern-checked, coerced or canonicalised in any way, and an empty
        string is typed rather than rejected. One of the documented scenarios has no data
        table of its own, because the Gherkin grammar attaches both tables in the feature file
        to a later outline; that scenario therefore reaches this method with the LITERAL
        placeholder text of its step -- angle brackets included -- and it passes while doing
        so. Any guard, warning or "did you mean to substitute a data-table value?" repair here
        would change behaviour this migration exists to reproduce. See
        ``docs/migration-parity.md`` for the register entry.

        Args:
            locator: The (strategy, value) pair identifying the field.
            text: The exact characters to type. Passed through verbatim (defect D1).
            clear_first: Clear the field before typing. True by default, because a field
                pre-filled by the browser or by a previous scenario step would otherwise
                concatenate. Pass ``False`` to append to whatever the field already holds.
            timeout: Per-call budget in seconds, or ``None`` for :attr:`timeout`.

        Raises:
            TimeoutException: If the field does not become visible within the budget.
            ValueError: If ``timeout`` is supplied and is not positive.
        """
        # The value is deliberately absent from the log record: these fields carry
        # credentials, and a credential must never reach a log. The locator and whether the
        # field was cleared are what a reader needs to diagnose a failure here.
        logger.debug(
            "Typing into element located by %r (clear_first=%s)",
            locator,
            clear_first,
        )
        element = self.find_visible(locator, timeout)

        if clear_first:
            element.clear()

        # Defect D1: verbatim, unconditionally. No transformation of any kind sits between
        # the argument and Selenium.
        element.send_keys(text)

    # -------------------------------------------------------------------------------------
    # Queries.
    # -------------------------------------------------------------------------------------

    def read_text(self, locator: Locator, timeout: float | None = None) -> str:
        """Wait for an element to be visible and return its text, unaltered.

        PRESERVED DEFECT D5 -- read before changing this method. The value Selenium reports is
        returned as-is: no trimming, no whitespace collapsing, no case folding, no Unicode
        canonicalisation and, above all, no trailing-punctuation tidying. One documented
        scenario asserts a French validation message whose trailing full stop is part of the
        expected value, and the comment above that scenario in the source documentation
        describes a DIFFERENT, English message; the assertion is the executable truth and it
        must compare exactly. Selenium already applies its own rendering rules to an element's
        text, so a second layer of tidying here would silently break the exact comparison the
        concrete login page's constant exists to support. See ``docs/migration-parity.md`` for
        the register entry.

        Args:
            locator: The (strategy, value) pair identifying the element.
            timeout: Per-call budget in seconds, or ``None`` for :attr:`timeout`.

        Returns:
            The element's text exactly as Selenium reports it (defect D5).

        Raises:
            TimeoutException: If the element does not become visible within the budget.
            ValueError: If ``timeout`` is supplied and is not positive.
        """
        element = self.find_visible(locator, timeout)
        text = element.text

        # Only the length is logged, never the content: an element's text can echo a
        # credential, and the caller receives the value in full anyway. Computing a length
        # reads the string and changes nothing about the value returned below.
        logger.debug("Read %d character(s) from element located by %r", len(text), locator)

        # Defect D5: returned exactly as received from Selenium.
        return text

    def is_visible(self, locator: Locator, timeout: float | None = None) -> bool:
        """Report whether an element becomes visible within the budget.

        This is the ONE deliberate exception to this class's fail-honestly rule. Every other
        method lets its Selenium exception propagate, because a step that cannot find what it
        needs must fail for real. Here the timeout IS the answer: a caller asking "is this
        visible?" -- the dashboard check of the documented scenarios, for instance -- needs to
        be told "no" rather than to be interrupted. Only ``TimeoutException`` is caught, and
        it is caught narrowly: every other failure, including a malformed locator or a lost
        session, still propagates.

        Because a negative answer costs the full budget, pass a short ``timeout`` when the
        expected answer is "no"; the default is sized for waiting on something that should
        appear.

        Args:
            locator: The (strategy, value) pair identifying the element.
            timeout: Per-call budget in seconds, or ``None`` for :attr:`timeout`.

        Returns:
            True if the element became visible inside the budget, False if the wait expired.

        Raises:
            ValueError: If ``timeout`` is supplied and is not positive. A nonsensical budget
                is a defect in the caller, not an invisible element, so it is not reported as
                False.
        """
        from selenium.common.exceptions import TimeoutException
        from selenium.webdriver.support import expected_conditions

        budget = self._resolve_timeout(timeout)
        logger.debug(
            "Checking up to %s s whether element located by %r is visible", budget, locator
        )

        try:
            self._wait(budget).until(expected_conditions.visibility_of_element_located(locator))
        except TimeoutException:
            logger.debug("Element located by %r was not visible within %s s", locator, budget)
            return False

        return True

    # -------------------------------------------------------------------------------------
    # Generic escape hatch.
    # -------------------------------------------------------------------------------------

    def wait_until(
        self,
        condition: Callable[[WebDriver], _T],
        timeout: float | None = None,
    ) -> _T:
        """Wait on any caller-supplied predicate and return whatever it resolved to.

        The published methods above cover every capability the documented scenarios need. This
        one exists so that a subclass needing a different condition -- any of Selenium's
        expected conditions, or a plain callable of its own -- can express it without
        re-implementing the wait, and therefore without reaching for a fixed pause or building
        a second waiting mechanism beside this one.

        Args:
            condition: A callable given the injected driver and polled until it returns a
                value that is not falsy. Selenium's expected-condition factories return
                exactly this shape.
            timeout: Per-call budget in seconds, or ``None`` for :attr:`timeout`.

        Returns:
            The predicate's first truthy result.

        Raises:
            TimeoutException: If the predicate never returns a truthy value within the budget.
            ValueError: If ``timeout`` is supplied and is not positive.
        """
        budget = self._resolve_timeout(timeout)
        logger.debug("Waiting up to %s s for caller-supplied condition %r", budget, condition)
        return self._wait(budget).until(condition)
