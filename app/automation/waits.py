r"""Explicit waits - the port of the nine per-class ``WebDriverWait`` fields.

Every Java step class of the reference suite constructed a ``WebDriverWait``
of its own and used it for each gated interaction in that class.  Nine such
fields exist across the eleven step classes at pinned revision
``47e9d697e4a9a85da889f94a846fdf47af28a240``, which AAP 0.2.1 holds as
REFERENCE and this port never modifies.  AAP 0.4.1 maps all nine onto this one
module - *"Explicit waits with the timeout supplied per call site"* - and
0.4.2 fixes the shape of the surface it exposes: *"the wait helpers from
waits.py, each taking an explicit timeout."*

Nine thin wrappers over a single private core is the whole of the design.
Each wrapper names one ``expected_conditions`` predicate, hands it to
:func:`_until`, and returns what the wait resolved to.  The core is the only
place in this port where a ``WebDriverWait`` is ever constructed.

The nine construction sites and their timeouts
----------------------------------------------
These are frozen: AAP 0.8 lists the nine explicit waits, together with the
seventeen fixed delays covered by exclusion 2 below, among the observable
contracts that must not change.  (Paraphrased rather than quoted, so that the
name of the Java delay primitive appears nowhere in this file - see exclusion
2 for why that matters.)

========  ===================================================================
Timeout   Java construction site
========  ===================================================================
2 s       ``Calendar.java:15``, ``Crm.java:18``
3 s       ``LoginSD.java:17``, ``LogOutSD.java:13``, ``EmployeeStage.java:14``
4 s       ``Sales.java:17``
20 s      ``Contacts.java:15``, ``Inventory.java:13``, ``Notes.java:19``
========  ===================================================================

``Session.java`` constructs no wait whatever, which is why
``features/steps/session_steps.py`` imports nothing from this module.  That
asymmetry is real and survives the port: a wait helper reaching that one step
module would be a divergence, not a tidy-up.

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

The public surface
------------------
============================  =====================================
Helper                        Predicate it waits on
============================  =====================================
:func:`wait_visible`          ``visibility_of_element_located``
:func:`wait_visible_element`  ``visibility_of``
:func:`wait_present`          ``presence_of_element_located``
:func:`wait_clickable`        ``element_to_be_clickable``
:func:`wait_invisible`        ``invisibility_of_element_located``
:func:`wait_all_visible`      ``visibility_of_all_elements_located``
:func:`wait_text_present`     ``text_to_be_present_in_element``
:func:`wait_title_is`         ``title_is``
:func:`wait_url_contains`     ``url_contains``
============================  =====================================

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

#: The locator shape every ``expected_conditions`` predicate expects: a
#: ``(By.X, "value")`` pair, which ``app/pages/*`` builds from the ``By`` that
#: the package barrel re-exports.  Deliberately absent from :data:`__all__` -
#: that list is exactly the helpers - because this alias exists to keep their
#: signatures readable rather than to widen the surface.
type Locator = tuple[str, str]


def _until[T](
    condition: Callable[[Any], T | Literal[False]],
    timeout: int | float,
    driver: "WebDriver | None",
) -> T:
    """Resolve the session, run ``condition`` under a wait and return its result.

    The single core every public helper delegates to, and the only place in
    the port that constructs a ``WebDriverWait``.  Three lines of behaviour,
    each of them contractual:

    * **The session is resolved once.**  :func:`get_driver` is called only
      when no ``driver`` was supplied, so a caller using the test seam never
      reaches the real lifecycle owner, and a caller that does not reach it
      exactly once.
    * **The timeout is passed through as given**, in seconds, with no
      conversion, no clamping, no floor and no default of any kind.
    * **Nothing is caught.**  Whatever ``until`` resolves to is returned as it
      is, and whatever it raises - an expiry above all - travels straight out
      to the step body, where it fails the step exactly as the Java original
      fails it.

    Private, and staying private: the public helpers name their predicate for
    the reader, whereas a caller reaching this function directly would be
    building an ``expected_conditions`` predicate outside this package, which
    is what the import boundary in the module docstring forbids.

    :param condition: A predicate of the kind ``expected_conditions`` builds.
        The annotation mirrors the binding's own: a predicate may answer
        ``False`` to mean "not yet", which the wait absorbs, so ``until``
        itself never returns that value.
    :param timeout: Seconds to keep polling for, as supplied by the call site.
    :param driver: The session to wait on, or ``None`` to use this worker's.
    :returns: Whatever ``condition`` finally resolved to - a web element, a
        list of them, or a boolean, according to the predicate.
    :raises selenium.common.exceptions.TimeoutException: When the condition is
        still unmet once ``timeout`` has elapsed.  Propagated deliberately.
    """
    target = get_driver() if driver is None else driver
    return WebDriverWait(target, timeout).until(condition)


# ---------------------------------------------------------------------------
# The public surface - one wrapper per predicate, in the order the module
# docstring's surface table lists them.  Each is a single delegation, so that
# the wrapper adds a name and a type and no behaviour of its own, and each
# takes its timeout from the call site because no wrapper declares a default.
# ---------------------------------------------------------------------------


def wait_visible(
    locator: Locator,
    timeout: int | float,
    *,
    driver: "WebDriver | None" = None,
) -> "WebElement":
    """Wait on ``visibility_of_element_located(locator)``; returns the web element."""
    return _until(EC.visibility_of_element_located(locator), timeout, driver)


def wait_visible_element(
    element: "WebElement",
    timeout: int | float,
    *,
    driver: "WebDriver | None" = None,
) -> "WebElement":
    """Wait on ``visibility_of(element)``; returns that web element."""
    return _until(EC.visibility_of(element), timeout, driver)


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

