"""Browser-automation layer of the Testinium-QA Python port - and its public barrel.

This package holds the three modules that drive a real browser, and it is the
**only** package in the port that imports ``selenium``.  Specification section
0.4.2 states that boundary and this file is what makes it satisfiable:

``driver``
    The worker-local WebDriver lifecycle - the port of the Java ``Driver``
    utility (``Driver.java:17-55``).  The single owner of every browser
    session: one slot per worker process, created on demand, maximized, given
    a 10-second implicit wait, and quit-and-cleared at the scenario boundary
    by ``features/environment.py``.
``waits``
    Explicit waits - the port of the nine ``WebDriverWait`` construction sites
    across the step classes.  Each helper takes its **timeout at the call
    site**, because the source fixes a different timeout per step class (2s,
    3s, 4s and 20s) and no default may paper over that difference.
``interactions``
    Keyboard and action-chain helpers - the port of the ``Keys`` call sites in
    ``Crm``, ``Notes`` and ``Sales`` and the ``Actions`` call sites in ``Crm``
    and ``Notes``.  ``Contacts`` uses neither, and its step module imports
    nothing from here.

The Java step classes reached Selenium directly: eleven imported ``Driver``,
nine imported ``WebDriverWait`` and ``ExpectedConditions``, three imported
``Keys``, two imported ``Actions`` and one imported ``By``.  This barrel
absorbs all of it, so a step module or a page object writes one import of
``app.automation`` and never names ``selenium`` - which is precisely what
``tests/test_steps_registration.py`` asserts about every module under
``features/steps/``.

The export surface
------------------
This table is the package's contract.  ``app/pages/*``, ``features/steps/*``,
``features/environment.py`` and ``tests/*`` are all written against it, so a
name may be added here only when a consumer actually imports it, and renamed
only together with its defining module.

======================  =============  ====================================
Name                    Defined in     Consumers
======================  =============  ====================================
``get_driver``          ``driver``     ``features/environment.py``,
                                       ``app/pages/base_page.py``
``quit_driver``         ``driver``     ``features/environment.py``
``wait_visible``        ``waits``      the nine step modules that construct
                                       a ``WebDriverWait``, and
                                       ``app/pages/*``
``wait_visible_element``  ``waits``    same
``wait_present``        ``waits``      same
``wait_clickable``      ``waits``      same
``wait_invisible``      ``waits``      same
``wait_all_visible``    ``waits``      same
``wait_text_present``   ``waits``      same
``wait_title_is``       ``waits``      same
``wait_url_contains``   ``waits``      same
``press_keys``          ``interactions``  ``crm_steps``, ``notes_steps``,
                                          ``sales_steps``
``action_chain``        ``interactions``  ``crm_steps``, ``notes_steps``
``By``                  ``selenium``   ``app/pages/*`` (all ten pages and
                                       ``base_page``) and
                                       ``features/steps/login_steps.py``
======================  =============  ====================================

The intended call-site shape, which specification section 0.4.2 gives as its
worked example, is one line with the timeout supplied per call:

.. code-block:: python

    from app.automation import wait_visible   # timeout passed at the call site

``By`` is the one Selenium name re-exported, and it is authorized by name:
specification section 0.4.2 lists *"``By``, re-exported so locator
construction needs no Selenium import elsewhere"*, with its import sites given
as ``app/pages/*`` and ``login_steps``.  The step site is the port of
``LoginSD.java:56``, which reads the login input's ``validationMessage``
attribute through a ``By`` locator built inline rather than through the page
object; the page sites are the locator constants that replace Selenium's
``PageFactory`` and ``@FindBy``, which have no Python equivalent.

What this barrel must never export
----------------------------------
The prohibition is the load-bearing half of the contract, because the
invariant it protects fails quietly rather than loudly.  **Never re-export**
``WebDriverWait``, ``expected_conditions`` (or ``EC``), ``ActionChains``,
``Keys``, ``selenium.webdriver``, ``webdriver_manager``, ``WebElement``,
``Service``, ``ChromeDriverManager``, ``GeckoDriverManager``, or any driver
class.  Any one of them would let a step module obtain Selenium machinery
*through this package* - hollowing out the section 0.4.2 boundary while still
passing a naive "does this file import selenium" check.  ``By`` is the single
authorized Selenium re-export; nothing else is.

**Receiving a Selenium object from this package breaks nothing.**  The
invariant is import-level and textual, which is exactly how
``tests/test_steps_registration.py`` checks it.  A step module that takes the
``ActionChains`` returned by :func:`~app.automation.interactions.action_chain`
and chains ``.move_to_element(...).click().perform()`` onto it, or that
operates on the ``WebElement`` returned by
:func:`~app.automation.waits.wait_visible`, is using this package exactly as
intended.  Handing out an *instance* is the design; handing out the *class* is
what is forbidden.  Nobody should later "fix" this by wrapping those return
values in facades - that would add a translation layer the Java source never
had, for no gain in parity.

Import order is pinned, and it is a correctness requirement
-----------------------------------------------------------
``driver`` first, then ``waits``, then ``interactions``, then ``By``.  Both
``waits`` and ``interactions`` execute ``from .driver import get_driver``, and
importing a submodule such as ``app.automation.waits`` runs this package's
``__init__`` to completion first.  Importing the siblings before ``driver``
would therefore begin initializing them while this module is still partially
initialized, which surfaces as an import error on some entry points and not on
others.  Keeping ``driver`` at the top removes that class of failure entirely,
so the ordering below is not a style preference and must not be re-sorted.

Contract of this file
---------------------
It marks the package and re-exports the surface above.  It contains **no
logic** - no function, no class, no branch, no computed value, no state, no
error handling around the imports - and no import-time side effects: importing
``app.automation`` starts no browser, provisions no driver binary, reads no
``configuration.properties``, touches no network, configures no logging and
resolves no filesystem path (``app/utils/paths.py`` owns every path in the
port).  It imports nothing from the wider application either: not the
configuration module at ``app/config.py`` - ``driver`` is the only module in
this package permitted to read it - and none of the sibling packages
``utils``, ``services``, ``reporting``, ``pages`` or ``web``, nor Flask, nor
the Gherkin engine.  That keeps the package importable inside a run worker
that never builds a web application, and it is why this package declares no
sibling-folder dependency.
"""

# Named individually rather than star-imported so the surface is greppable and
# a typo fails loudly at import time instead of silently thinning the API.
# The order of the four statements is pinned - see the docstring: ``driver``
# must be fully initialized before the two siblings that relative-import it.
# An import sorter would put the third-party ``By`` first and interleave the
# three siblings alphabetically, which is why the block opts out of sorting
# rather than being re-ordered to satisfy one.
from .driver import get_driver, quit_driver  # noqa: I001
from .waits import (
    wait_all_visible,
    wait_clickable,
    wait_invisible,
    wait_present,
    wait_text_present,
    wait_title_is,
    wait_url_contains,
    wait_visible,
    wait_visible_element,
)
from .interactions import action_chain, press_keys
from selenium.webdriver.common.by import By

# Grouped by owning module, in the order the docstring's table lists them, so
# the barrel reads as documentation of the package's surface; a single sequence
# sorted across the whole list would interleave the modules and lose that.
# Exactly these fourteen names - every one defined by a sibling module or, for
# ``By``, authorized by name in specification section 0.4.2, and every one with
# a real consumer.
__all__ = [  # noqa: RUF022
    # -- app/automation/driver.py: the session lifecycle --------------------
    "get_driver",
    "quit_driver",
    # -- app/automation/waits.py: explicit waits, timeout per call site -----
    "wait_visible",
    "wait_visible_element",
    "wait_present",
    "wait_clickable",
    "wait_invisible",
    "wait_all_visible",
    "wait_text_present",
    "wait_title_is",
    "wait_url_contains",
    # -- app/automation/interactions.py: keyboard and action chains ---------
    "press_keys",
    "action_chain",
    # -- selenium: the single authorized re-export, for locator constants --
    "By",
]
