"""Page-object layer of the Testinium-QA Python port - and its public barrel.

The ten page objects that replace Selenium's ``PageFactory``/``@FindBy`` pair,
which AAP 0.1.2 maps onto "locator constants plus lazy accessors", plus the
base class supplying those semantics.  This file re-exports those ten classes
and nothing else: AAP 0.4.1 makes it a marker that *"contains no logic"*.

=================  ==================  ==============  ========
Name               Module              Java anchor     Locators
=================  ==================  ==============  ========
``CalendarPage``   ``calendar_page``   ``CalendarP``         21
``ContactsPage``   ``contacts_page``   ``ContactsP``         16
``CrmPage``        ``crm_page``        ``CrmP``              28
``EmployeePage``   ``employee_page``   ``EmployeeP``         15
``InventoryPage``  ``inventory_page``  ``InventoryP``         8
``LoginPage``      ``login_page``      ``LoginP``             7
``LogOutPage``     ``logout_page``     ``LogOutP``            3
``NotesPage``      ``notes_page``      ``NotesP``            10
``SalesPage``      ``sales_page``      ``SalesP``            20
``SessionPage``    ``session_page``    ``SessionP``           3
=================  ==================  ==============  ========

The locator column totals 131, the ``@FindBy`` count across the ten Java page
classes at pinned revision ``47e9d697e4a9a85da889f94a846fdf47af28a240``, and
each per-page count is asserted through this barrel, so a dropped or invented
field fails.  Two name shapes are deliberate: ``LogOutPage`` keeps the capital
``O`` of Java's ``LogOutP`` while AAP 0.4.1 fixes its module as
``logout_page``, and ``ContactsPage`` is plural after ``ContactsP``.

:class:`~app.pages.base_page.BasePage` is absent by contract: AAP 0.4.1
enumerates "the ten page classes" and no more, and a step drives a page object
rather than the machinery underneath it.  It stays at its defining module,
``app.pages.base_page``, so ``app.pages.BasePage`` does not resolve.

Importing any name below runs ``app.pages.<module>`` -> ``base_page`` ->
``app.automation`` -> ``...driver`` -> ``app.config`` ->
``app.utils.properties``: acyclic as AAP 0.4.2 requires, executed in every
behave worker, and the reason this file carries no logic, no ``__version__``
and no import-time side effect.
"""

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

# Ordered by module name, as the imports are; a case-sensitive sort would put
# ``LogOutPage`` before ``LoginPage``, hence the suppression.
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
