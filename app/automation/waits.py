r"""Explicit waits - the port of the nine per-class ``WebDriverWait`` fields.

Every Java step class constructed a wait of its own for each gated interaction,
nine fields in all.  AAP 0.4.1 maps them onto this module - *"Explicit waits
with the timeout supplied per call site"* - and 0.4.2 fixes the surface: nine
thin wrappers over :func:`_until`, the only place in the port that builds one.

The nine construction sites, whose timeouts AAP 0.8 freezes: 2 s in
``Calendar.java:15`` and ``Crm.java:18``; 3 s in ``LoginSD.java:17``,
``LogOutSD.java:13`` and ``EmployeeStage.java:14``; 4 s in ``Sales.java:17``;
20 s in ``Contacts.java:15``, ``Inventory.java:13`` and ``Notes.java:19``.
``Session.java`` constructs none, which is why
``features/steps/session_steps.py`` imports nothing from here.

That list is the only place those numbers appear in this file.  ``timeout`` is
a required parameter of every helper - no constant, no default, so omitting it
is a ``TypeError`` from Python itself - and each step module passes its own
Java class's number at every call site, since a default here would let a call
site silently acquire another class's timeout.  The suite's seventeen fixed
delays stay at their own call sites per AAP 0.4.1, so nothing here pauses.

Nothing else is embellished: no retry, no stale-element recovery, no capture on
expiry, no logging.  An expiry propagates as the binding's ``TimeoutException``
and fails the step as it fails the Java one; the resolved element, list or
boolean comes back untouched; timeouts are seconds on both sides; and the
ten-second implicit wait ``driver.py`` sets underneath can inflate the effective
timeout, which is the source's timing and is not corrected here.

Every helper resolves its session through :func:`get_driver`; the keyword-only
``driver`` parameter is a test seam no step module or page object passes.  Two
helpers share ``visibility_of_element_located``: :func:`wait_visible_element`
ports ``visibilityOf`` over a ``PageFactory`` field, whose proxy re-located
inside the predicate on every poll, so it takes a locator, not a resolved
element; :func:`wait_visible` ports the locator form AAP 0.4.2 names.

Exclusion 1 - no default timeout, and no timeout constant
---------------------------------------------------------
The table above is reproduced so the numbers stay auditable from the code, and
that table is the *only* place in this file they appear.  None of them is a
constant here and none is attached to a parameter default: ``timeout`` is a
required parameter of every helper, so omitting it is a ``TypeError`` raised
by Python itself rather than a silent fallback.

That is the point of the module.  Each ported step module passes its own
class's number at each of its call sites - 2 s in ``calendar_steps`` and
``crm_steps``, 3 s in ``login_steps``, ``logout_steps`` and
``employee_steps``, 4 s in ``sales_steps``, 20 s in ``contacts_steps``,
``inventory_steps`` and ``notes_steps`` - which keeps each Java class's
timeout visible exactly where its original declared it, and lets the
per-module parity tests assert the same wait target and timeout as the Java
method.  A module-level default here would let a call site silently acquire
some other class's timeout, which is precisely the parity failure this design
exists to prevent.

Exclusion 2 - the seventeen fixed delays are not this module's business
----------------------------------------------------------------------
The suite also takes seventeen fixed delays, through the Java ``Thread`` pause
primitive: two of 3 s in ``Calendar.java``, five of 3 s in ``Contacts.java``,
one of 2 s in ``Crm.java``, one of 7 s and seven of 3 s in
``EmployeeStage.java``, and one of 2 s in ``Notes.java``.  AAP 0.4.1 leaves
every one of them where it is: *"They are reproduced as equivalent fixed
delays at the same call sites, not replaced by explicit waits: converting them
would change timing behaviour and, in a suite whose steps depend on Odoo's
client-side rendering, could change outcomes."*

So this module offers no fixed-delay helper of any kind - nothing that pauses,
nothing that defers, no wait-for-N-seconds function - and no standard-library
``time`` import appears below.  A later contributor tempted to "helpfully"
centralize those delays here should read this paragraph as the reason not to:
they are step-body behaviour, each parity test asserts the delay is present at
its own call site, and replacing them with waits is a follow-up the user can
request rather than something this port may decide.

Where the driver comes from
---------------------------
Every helper resolves its session through :func:`get_driver`, the sibling
module that owns the browser lifecycle, so a call site passes a locator and a
timeout and nothing else.  The keyword-only ``driver`` parameter is a test
injection seam and nothing more: ``tests/test_waits.py`` drives these helpers
against a stubbed driver, which is what makes this package's coverage gate
reachable with no browser, no live system under test and no populated
properties file.  It is not part of the ported behaviour, and no step module
and no page object passes it.

Explicit over implicit - a layering inherited, not introduced
-------------------------------------------------------------
``app/automation/driver.py`` applies a ten-second implicit wait to every
session it creates (``Driver.java:33-34`` and ``:39-40``), and the Java step
classes then used explicit waits on top of it.  An implicit wait underneath an
explicit one can inflate the effective timeout, and that is exactly the timing
the suite actually has, because the source sets both.  Nothing here zeroes
that setting before a wait or restores it afterwards; that "improvement" would
change timing behaviour, which AAP 0.8 freezes.

What propagates, and what is left alone
---------------------------------------
* **A timeout fails the step.**  ``wait.until(...)`` raises
  ``TimeoutException`` in the Java binding and in the Python one alike, and no
  Java step class catches it.  No helper here catches it either: this file
  installs no error handler at all, wraps nothing in a project-specific error,
  and never answers ``None`` or ``False`` because a wait ran out.  AAP
  deviation 16 permits the *formatting* of Python's error text to differ from
  the JVM's; the failure itself still has to happen.
* **The resolved value is handed back untouched.**  Java's
  ``wait.until(ExpectedConditions.visibilityOf(el))`` returns the element and
  the step body goes on to use it, so every helper returns whatever the wait
  resolved to - an element, a list of elements, or a boolean - and never
  ``None`` on success.
* **Seconds, never milliseconds.**  ``new WebDriverWait(driver, 3)`` counted
  seconds in the Selenium 3 binding that ``pom.xml:36-40`` pins, and the
  Python binding counts seconds too, so the number is passed straight through.
* **The binding's own defaults stay put.**  The polling interval is 500 ms on
  both sides and the tolerated-error set already holds
  ``NoSuchElementException`` on both, so neither is stated here: restating
  either would invite drift from the source without changing today's
  behaviour.
* **No embellishment.**  No retry loop, no ``WebDriverWait`` subclass, no
  stale-element recovery, no capture on expiry, no timing instrumentation.
  There is no logging either, and that is deliberate: a helper invoked at
  every gated interaction in the suite would flood the CI console, and the one
  event worth reporting - the expiry - reports itself by raising.

Import boundary (AAP 0.4.2)
---------------------------
This module and its two siblings are the only place ``selenium`` is imported,
which is exactly why ``WebDriverWait`` and ``expected_conditions`` must not
leak outward from here: re-exporting either would let a step module build its
own predicate and hollow that invariant out.  ``By`` is the single authorized
Selenium re-export, and the package barrel is where it is re-exported, not
this module.  The two Selenium types named in the annotations below are
imported under ``TYPE_CHECKING`` and referenced as strings, so they exist for
a type checker and are absent from this module at run time - the strongest
available form of "no Selenium type is exported from here".

Those quotation marks are load-bearing rather than stylistic, and a linter
that offers to remove them is wrong here: under the deferred-annotation
semantics of Python 3.14, an *unquoted* annotation naming a
``TYPE_CHECKING``-only import raises ``NameError`` the moment anything
resolves it, and ``inspect.signature`` resolves annotations by default.  That
call is exactly how ``tests/test_waits.py`` proves ``timeout`` carries no
default, so unquoting these would trade a working test seam for tidier
punctuation.  Keep the quotes, or move the imports out of the guarded block -
never one without the other.

The one internal import is :func:`get_driver`, taken relatively from the
sibling driver module so that the package's own import order is respected.
Nothing else is imported: no configuration accessor, no path helper, no
service, no reporting writer, no page object, no web framework and no Gherkin
engine.

What ``tests/test_waits.py`` asserts
------------------------------------
Recorded here so the suite can be written against this module without
rereading the Java source.  Every item is reachable with a stubbed driver.

1. **The timeout reaches the wait unchanged** - for each helper, with 2, 3, 4
   and 20 injected in turn so that all four Java values are exercised, the
   constructed wait carries exactly the number that was passed.
2. **The predicate reaches ``until``** - each helper hands over the condition
   its row of the surface table names, built from the arguments it was given.
3. **The resolved value comes back** - element, list or boolean, never
   ``None``.
4. **Omitting the timeout is a ``TypeError``** - raised by Python itself,
   because no helper declares a default for it.
5. **An expiry propagates unchanged** - when the stub raises
   ``TimeoutException``, the helper lets it through untouched.
6. **The seam works as documented** - with no ``driver`` argument,
   :func:`get_driver` is called exactly once; with one, it is not called at
   all.
7. **A visibility wait is handed a locator** - :func:`wait_visible_element`
   names ``visibility_of_element_located`` by that exact name, over the
   locator it was given and with nothing else, and looks nothing up itself:
   the stubbed driver records no operation at all.  Those two together are
   what place the lookup inside the predicate, under the call site's own
   timeout, rather than before the wait exists.  Asserted by name rather than
   by counting polls, because no test in that module performs a real wait.

The public surface
------------------
============================  =====================================
Helper                        Predicate it waits on
============================  =====================================
:func:`wait_visible`          ``visibility_of_element_located``
:func:`wait_visible_element`  ``visibility_of_element_located``
:func:`wait_present`          ``presence_of_element_located``
:func:`wait_clickable`        ``element_to_be_clickable``
:func:`wait_invisible`        ``invisibility_of_element_located``
:func:`wait_all_visible`      ``visibility_of_all_elements_located``
:func:`wait_text_present`     ``text_to_be_present_in_element``
:func:`wait_title_is`         ``title_is``
:func:`wait_url_contains`     ``url_contains``
============================  =====================================

The two visibility rows name the same predicate on purpose, because the two
helpers port *different* Java predicates onto it and only one of those two is
ever used by the reference:

* :func:`wait_visible_element` ports
  ``ExpectedConditions.visibilityOf(<annotated field>)``, which is the suite's
  **only** visibility predicate - all 42 of its visibility waits, of which
  ``Calendar.java:20`` is the shape.  The field handed to it is a
  ``PageFactory`` proxy, so the element lookup re-runs *inside* the predicate
  on every poll, and a locator that has not appeared yet raises the not-found
  error both bindings' wait already tolerates, which simply repeats the poll.
  Reproducing that means taking the **locator** the proxy would have
  re-located and resolving it per poll, which is why this helper's row names
  the located form.  Accepting an already-resolved element instead would move
  the lookup out of the wait and under the ten-second implicit wait
  ``driver.py`` sets, and the call site's 2, 3, 4 or 20 seconds would never
  gate that lookup at all - the parity defect this shape exists to avoid.
* :func:`wait_visible` ports
  ``ExpectedConditions.visibilityOfElementLocated``, which the reference calls
  nowhere (``grep -rn visibilityOfElementLocated`` over its sources returns
  zero hits).  It is named in the export surface AAP 0.4.2 fixes and is the
  locator-shaped helper all ten page modules name when they document what
  their upper-case constants are for, so it keeps its own name and its own row
  rather than being folded away.

Having arrived at one predicate, they share one body: the element-named helper
delegates to the locator-named one, so a future change to how visibility is
awaited has a single site to change and the two cannot drift apart.

The membership is fixed and closed.  It covers what the ported step classes
actually do - gating on visibility before an interaction, clickability before
a click, presence, the disappearance of an overlay, a list of elements, a text
assertion, the page-title assertion of the login and employee flows
(``LoginSD.java:44-46``, ``EmployeeStage.java:31``), and a URL check - and
page objects, step modules and the unit suite all import from this list.  A
call site needing a predicate outside it gets exactly one new helper, named
``wait_`` plus the condition's name in snake case, with the same signature
shape, added to :data:`__all__` here and to the package barrel's.  Nothing
that no call site uses is added.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal

from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from .driver import get_driver

if TYPE_CHECKING:  # pragma: no cover - resolved by a type checker, never at run time
    from selenium.webdriver.remote.webdriver import WebDriver
    from selenium.webdriver.remote.webelement import WebElement

__all__ = [
    "wait_visible",
    "wait_visible_element",
    "wait_present",
    "wait_clickable",
    "wait_invisible",
    "wait_all_visible",
    "wait_text_present",
    "wait_title_is",
    "wait_url_contains",
]

type Locator = tuple[str, str]


def _until[T](
    condition: Callable[[Any], T | Literal[False]],
    timeout: int | float,
    driver: "WebDriver | None",
) -> T:
    """Resolve the session, run ``condition`` under a wait and return its result.

    The single core every public helper delegates to, and the only place in the
    port that builds a wait object.  Private and staying private: a caller
    reaching it directly would be constructing an ``expected_conditions``
    predicate outside this package, which the import boundary forbids.

    :param condition: A predicate of the kind ``expected_conditions`` builds.
        The annotation mirrors the binding's own: a predicate may answer
        ``False`` for "not yet", which the wait absorbs, so ``until`` itself
        never returns that value.
    :param timeout: Seconds to keep polling for, passed through exactly as the
        call site supplied it - no conversion, clamping, floor or default.
    :param driver: The session to wait on, or ``None`` to resolve this worker's
        through :func:`get_driver`, which is then called exactly once.
    :returns: Whatever ``condition`` finally resolved to - a web element, a
        list of them, or a boolean, according to the predicate.
    :raises selenium.common.exceptions.TimeoutException: When the condition is
        still unmet once ``timeout`` has elapsed.  Nothing is caught here, so
        this and anything else ``until`` raises reach the step body and fail it
        as the Java original fails it.
    """
    target = get_driver() if driver is None else driver
    return WebDriverWait(target, timeout).until(condition)


def wait_visible(
    locator: Locator,
    timeout: int | float,
    *,
    driver: "WebDriver | None" = None,
) -> "WebElement":
    """Wait on ``visibility_of_element_located(locator)``; returns the web element."""
    return _until(EC.visibility_of_element_located(locator), timeout, driver)


def wait_visible_element(
    locator: Locator,
    timeout: int | float,
    *,
    driver: "WebDriver | None" = None,
) -> "WebElement":
    """Wait on ``visibility_of_element_located(locator)``; returns the element.

    The port of ``ExpectedConditions.visibilityOf`` applied to a
    ``PageFactory`` field, which is every one of the reference's 42 visibility
    waits.  That field is a proxy that re-locates on each touch, so the lookup
    happened inside the predicate, once per poll, and the wait's own timeout
    governed it; this helper therefore takes the locator - a page object's
    upper-case constant, ``page.CALENDAR_BUTTON`` - and not an element already
    resolved through the lower-case accessor, which would be looked up before
    the wait exists.  The module docstring explains why the two visibility
    helpers share one predicate and why both names stay.
    """
    return wait_visible(locator, timeout, driver=driver)


def wait_present(
    locator: Locator,
    timeout: int | float,
    *,
    driver: "WebDriver | None" = None,
) -> "WebElement":
    """Wait on ``presence_of_element_located(locator)``; returns the web element."""
    return _until(EC.presence_of_element_located(locator), timeout, driver)


def wait_clickable(
    locator: Locator,
    timeout: int | float,
    *,
    driver: "WebDriver | None" = None,
) -> "WebElement":
    """Wait on ``element_to_be_clickable(locator)``; returns the web element."""
    return _until(EC.element_to_be_clickable(locator), timeout, driver)


def wait_invisible(
    locator: Locator,
    timeout: int | float,
    *,
    driver: "WebDriver | None" = None,
) -> "WebElement | bool":
    """Wait on ``invisibility_of_element_located(locator)``; returns element or bool."""
    return _until(EC.invisibility_of_element_located(locator), timeout, driver)


def wait_all_visible(
    locator: Locator,
    timeout: int | float,
    *,
    driver: "WebDriver | None" = None,
) -> "list[WebElement]":
    """Wait on ``visibility_of_all_elements_located(locator)``; returns the list."""
    return _until(EC.visibility_of_all_elements_located(locator), timeout, driver)


def wait_text_present(
    locator: Locator,
    text: str,
    timeout: int | float,
    *,
    driver: "WebDriver | None" = None,
) -> bool:
    """Wait on ``text_to_be_present_in_element(locator, text)``; returns a bool."""
    return _until(EC.text_to_be_present_in_element(locator, text), timeout, driver)


def wait_title_is(
    title: str,
    timeout: int | float,
    *,
    driver: "WebDriver | None" = None,
) -> bool:
    """Wait on ``title_is(title)`` for an exact page title; returns a bool."""
    return _until(EC.title_is(title), timeout, driver)


def wait_url_contains(
    fragment: str,
    timeout: int | float,
    *,
    driver: "WebDriver | None" = None,
) -> bool:
    """Wait on ``url_contains(fragment)`` for a substring of the URL; returns a bool."""
    return _until(EC.url_contains(fragment), timeout, driver)
