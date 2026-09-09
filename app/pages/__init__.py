"""Page-object layer of the Testinium-QA Python port - and its public barrel.

This package holds the ten page objects that stand in for Selenium's
``PageFactory`` and its ``@FindBy`` fields - a construct with no Python
equivalent, which is why AAP 0.1.2 maps ``PageFactory.initElements`` plus
``@FindBy`` onto "locator constants plus lazy accessors" - together with the
base class that supplies those lazy semantics.

This file re-exports **the ten page classes and nothing else**.  That surface
is fixed by AAP 0.4.1, whose package-markers row covers the ``__init__.py`` of
``app/services``, ``app/reporting``, ``app/pages`` and ``app/utils`` in one
sentence: they are *"package markers.  Each re-exports its package's public
names - the two services, the writer entry points and the event collector, the
ten page classes, and the path and properties helpers - and contains no
logic."*

The exported surface
--------------------
One class per Java page object, one module per class, one locator constant per
``@FindBy`` field.  This table is the package's contract: every step module and
``tests/test_pages.py`` are written against it, so a name may be added here
only when a consumer actually imports it, and renamed only together with both
its defining module and its Java anchor.

=================  ====================  ================  ========
Name               Defined in            Java anchor       Locators
=================  ====================  ================  ========
``CalendarPage``   ``calendar_page``     ``CalendarP``           21
``ContactsPage``   ``contacts_page``     ``ContactsP``           16
``CrmPage``        ``crm_page``          ``CrmP``                28
``EmployeePage``   ``employee_page``     ``EmployeeP``           15
``InventoryPage``  ``inventory_page``    ``InventoryP``           8
``LoginPage``      ``login_page``        ``LoginP``               7
``LogOutPage``     ``logout_page``       ``LogOutP``              3
``NotesPage``      ``notes_page``        ``NotesP``              10
``SalesPage``      ``sales_page``        ``SalesP``              20
``SessionPage``    ``session_page``      ``SessionP``             3
=================  ====================  ================  ========

The locator column totals **131**, the ``@FindBy`` count across the ten Java
page classes at pinned revision ``47e9d697e4a9a85da889f94a846fdf47af28a240``.
That is a parity invariant rather than a decoration: ``tests/test_pages.py``
enumerates :attr:`~app.pages.base_page.BasePage.LOCATORS` through this barrel
and asserts each per-page count, so a dropped or invented field fails there.

Two name shapes are deliberate and must not be regularized
----------------------------------------------------------
* **``LogOutPage``** carries a capital ``O``, mirroring the Java ``LogOutP``,
  while its module is **``logout_page``** with no underscore, because AAP 0.4.1
  fixes that filename.  The class and the module disagree on purpose, and
  ``logout_page.py`` records the same pairing from the other side.
* **``ContactsPage``** is plural, mirroring ``ContactsP``, while the feature it
  serves is the singular ``features/Contact.feature``.

``BasePage`` is deliberately absent
-----------------------------------
:class:`~app.pages.base_page.BasePage` is neither imported here nor listed in
:data:`__all__`, so ``app.pages.BasePage`` does not resolve.  It stays
reachable at its defining module, ``app.pages.base_page``, which is already
where all ten page modules and ``tests/test_base_page.py`` import it from.

The omission is a contract, not an oversight.  AAP 0.4.1 enumerates exactly
"the ten page classes" for this package and nothing more, and no step module
needs the base class, because a step drives a page object rather than the
machinery underneath it.  The sibling precedent is explicit:
``app/utils/__init__.py`` withholds ``properties.reset_cache`` from its barrel
for the same reason - test support belongs to its defining module.  The port's
suite pins this omission with ``not hasattr(app.pages, "BasePage")``, so a
later addition "for convenience" fails loudly instead of quietly widening a
surface other modules are coded against.

What this file must never contain
---------------------------------
No logic: no functions, no classes, no branches, no computed values, no error
handling around the imports, no ``__version__`` - ``pyproject.toml`` owns the
version, mapping the source build's ``1.0-SNAPSHOT`` to ``1.0.0.dev0`` - and no
constant other than :data:`__all__`.  No import-time side effects either:
importing ``app.pages`` configures no logging, touches no filesystem path,
reads no ``configuration.properties`` and starts no browser, so the package
imports cleanly on a host with neither that file nor a browser installed.  The
ten imports are unconditional - no ``__getattr__`` deferral and no lazy
importing, because a ten-module package has no import-cost problem worth that
complexity.

Import boundary, and the graph this barrel triggers
---------------------------------------------------
Importing any name below executes, in order, ``app.pages.<module>`` ->
``app.pages.base_page`` -> ``app.automation`` -> ``app.automation.driver`` ->
``app.config`` -> ``app.utils.properties``.  That chain is acyclic and must
stay so: per AAP 0.4.2 a page object imports ``app.automation`` for the current
driver and nothing else, and nothing under ``app/pages`` may import a service,
a reporting module or ``app.web``.  No page module imports another page module,
so the ten imports cannot form a cycle among themselves.

The chain is also why this file stays as thin as it is.  All of it executes in
every behave worker process, since all ten step modules import from this
barrel, and ``app/__init__.py`` defers its Flask, blueprint and CLI imports
into ``create_app()`` precisely so a worker can reach a leaf module without a
web framework being dragged in behind it.  One heavy import added here would
undo that for every worker and every step module at once.

Consumers
---------
``features/steps/<area>_steps.py`` imports the one page class its Java original
instantiated as a field.  ``features/steps/notes_steps.py`` is the sole
exception and imports **two** - :class:`NotesPage` and :class:`InventoryPage` -
because ``Notes.java:16-17`` declares ``InventoryP inventoryP`` and ``NotesP
notesP`` side by side; this barrel is what makes that one import line rather
than two.  ``tests/test_pages.py`` imports the barrel itself, to enumerate all
ten classes and their locator mappings.
"""

# Absolute imports, spelled module by module.  Two properties of this block are
# load-bearing.  The names are imported individually rather than star-imported,
# so the package's surface is greppable and a typo fails at import time instead
# of silently thinning the API.  And the block is ordered by module name, which
# is both isort's ordering and the order ``__all__`` repeats below, so the two
# can be checked against each other line by line.
from app.pages.calendar_page import CalendarPage
from app.pages.contacts_page import ContactsPage
from app.pages.crm_page import CrmPage
from app.pages.employee_page import EmployeePage
from app.pages.inventory_page import InventoryPage
from app.pages.login_page import LoginPage
from app.pages.logout_page import LogOutPage
from app.pages.notes_page import NotesPage
from app.pages.sales_page import SalesPage
from app.pages.session_page import SessionPage

# Exactly the ten page classes, in the order of the import block above, so the
# two read as one list.  The ``__all__`` ordering rule is suppressed for that
# reason alone: an isort-style sort of these names is case-sensitive and would
# put ``LogOutPage`` before ``LoginPage``, breaking the correspondence with the
# imports, which follow the module names (``login_page`` before
# ``logout_page``).  ``app/utils`` and ``app/automation`` suppress it for the
# same kind of reason - a deliberate, documented order beats a mechanical one.
__all__ = [  # noqa: RUF022
    "CalendarPage",
    "ContactsPage",
    "CrmPage",
    "EmployeePage",
    "InventoryPage",
    "LoginPage",
    "LogOutPage",
    "NotesPage",
    "SalesPage",
    "SessionPage",
]
