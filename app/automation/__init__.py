"""Browser-automation layer of the Testinium-QA Python port - and its public barrel.

This package holds the three modules that drive a real browser and is the
**only** one importing ``selenium`` (AAP 0.4.2).  This file marks the package
and re-exports the surface below; it holds no logic and no import-time side
effect - importing it starts no browser, reads no file and touches no network.

``driver`` owns the worker-local session lifecycle (``Driver.java:17-55``) -
one slot per worker, created on demand, maximized, given a 10-second implicit
wait, quit at the boundary ``features/environment.py`` owns.  ``waits`` ports
the nine ``WebDriverWait`` sites, each helper taking its timeout **at the call
site** because the source fixes a different one per step class; and
``interactions`` the ``Keys`` sites of ``Crm``, ``Notes`` and ``Sales`` and the
``Actions`` sites of ``Crm`` and ``Notes``, ``Contacts`` using neither.

Fourteen names are exported, and a name is added only when a consumer imports
it: ``get_driver`` (``features/environment.py``, ``app/pages/base_page.py``),
``quit_driver`` (``features/environment.py``), the nine ``wait_*`` helpers (the
step modules whose Java class built a wait, and ``app/pages/*``),
``press_keys`` (``crm_steps``, ``notes_steps``, ``sales_steps``),
``action_chain`` (``crm_steps``, ``notes_steps``) and ``By``.

``By`` is the single authorized Selenium re-export (AAP 0.4.2): its step site
ports ``LoginSD.java:56``'s inline locator and its page sites the constants
that replace ``PageFactory`` and ``@FindBy``.

**Never re-export** ``WebDriverWait``, ``expected_conditions``,
``ActionChains``, ``Keys``, ``selenium.webdriver``, ``webdriver_manager``,
``WebElement``, ``Service``, a driver manager or a driver class: each would let
a step module reach Selenium machinery *through* this package while still
passing a naive import check, which is how the boundary fails quietly.  Handing
out an *instance* is the design; the *class* is what is forbidden.

The import order below is pinned, as a correctness requirement: ``driver``
before ``waits`` and ``interactions``, which both run
``from .driver import get_driver`` while importing a submodule runs this file
first.  The ``noqa`` directives keep a sorter off that block and ``__all__``.
Nothing else is imported - ``app/config.py`` is ``driver``'s alone - so the
package stays importable in a worker that builds no web application.
"""

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

__all__ = [  # noqa: RUF022
    "get_driver",
    "quit_driver",
    "wait_visible",
    "wait_visible_element",
    "wait_present",
    "wait_clickable",
    "wait_invisible",
    "wait_all_visible",
    "wait_text_present",
    "wait_title_is",
    "wait_url_contains",
    "press_keys",
    "action_chain",
    "By",
]
