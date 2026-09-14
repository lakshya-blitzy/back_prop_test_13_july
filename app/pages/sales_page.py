r"""Sales page object - the port of ``SalesP.java``, and the port's only plural page.

The twenty ``@FindBy`` fields of ``SalesP.java:17-75``, at pinned revision
``47e9d697e4a9a85da889f94a846fdf47af28a240``, as locator constants in Java
declaration order - part of the contract, since
:attr:`~app.pages.base_page.BasePage.LOCATORS` is built from the class body in
source order and compared against that order.  The Java class declares no
method beyond its constructor, so this module declares those constants and one
:attr:`~app.pages.base_page.BasePage.PLURAL_LOCATORS` set and nothing else.

``SalesP.java:68-69`` binds its selector to ``public List<WebElement>
allCustomers``, the single ``List<WebElement>`` field among the reference's 131
``@FindBy`` declarations; :attr:`SalesPage.PLURAL_LOCATORS` names it, so
:attr:`~SalesPage.all_customers` resolves through ``find_elements`` and hands
back the list unchanged, empty list included; the other nineteen stay singular.
This module is the only consumer of ``BasePage.find_all``.

Four fields - ``allCustomers``, ``warningButton`` (``:62-63``), ``link``
(``:71-72``) and ``details`` (``:74-75``) - are read by no step class in the
reference, leaving sixteen reached by ``Sales.java``.  All twenty are declared
anyway: a dead field is carried, not pruned (AAP 0.8).

Selector quirks preserved byte-for-byte: :attr:`~SalesPage.DETAILS` is the only
attribute value in the suite quoted with double quotes, written single-quoted
here; :attr:`~SalesPage.CREATE_AND_EDIT_STATE` has no spaces around its ``=``
where ``NotesP.java:14`` writes the same affordance with them on a different
element; :attr:`~SalesPage.SAVE_BUTTON`/:attr:`~SalesPage.WARNING_BUTTON` and
:attr:`~SalesPage.CUSTOMERS_BUTTON`/:attr:`~SalesPage.LINK` differ only by a
trailing ``/span``; the two ``/web#menu_id=447&action=48`` hrefs carry a raw,
unescaped ``&``; and six generated field ids plus one jQuery-UI id are kept.

Per AAP 0.4.2 the imports are :data:`~app.automation.By` and
:class:`~app.pages.base_page.BasePage` and nothing else - no browser library
even under ``typing.TYPE_CHECKING``, no configuration, and no keyboard helper
(``sales_steps.py`` owns the ``Keys.ENTER`` send at ``Sales.java:68``).  The
4-second wait (``Sales.java:15-17``) stays at its step call sites per AAP 0.4.1
and importing this module has no side effects.  ``features/Sales.feature``
carries no tag, and its title begins ``.... app Sales feature``, an oddity AAP
0.6 flags for the JSON ``id`` slug rule.
"""

from app.automation import By
from app.pages.base_page import BasePage

__all__ = ["SalesPage"]


class SalesPage(BasePage):
    """Locator inventory of the Odoo Sales module's customer flow.

    The port of ``SalesP.java:17-75``: twenty locator constants in Java
    declaration order and nothing else.  Each constant yields its
    ``(By.X, "value")`` tuple when read off the class or an instance - the shape
    the explicit-wait and keyboard helpers of ``app.automation`` expect, each of
    them taking its timeout at the call site - and each also has a lower-case
    accessor, installed by :class:`~app.pages.base_page.BasePage`, that
    re-resolves the element against the live DOM on every access:

    .. code-block:: python

        page = SalesPage()                     # touches nothing at all
        page.sales_partial.click()             # one find_element, right now
        page.search_bar.send_keys("Lucas")     # another, resolved again
        len(page.all_customers)                # one find_elements, right now
        SalesPage.SEARCH_BAR                   # the tuple, for wait_visible()

    :attr:`~SalesPage.ALL_CUSTOMERS` is the one plural constant in the port; the
    module docstring gives its origin and why it is declared unused.
    """

    #: The names of the locator constants that resolve to a **list** of
    #: elements rather than one element.  Exactly one, and the only such
    #: declaration in the port: the port of ``SalesP.java:68-69``, the single
    #: ``List<WebElement>`` field among the reference's 131 ``@FindBy``
    #: declarations.  ``BasePage.__init_subclass__`` reads this set to route
    #: :attr:`all_customers` through ``find_elements``, leaves the other
    #: nineteen accessors on ``find_element``, and rejects at class-creation
    #: time any name here that is not a declared locator constant below.
    PLURAL_LOCATORS = frozenset({"ALL_CUSTOMERS"})

    #: ``SalesP.java:17-18``'s ``salesPartial``, the page's only non-XPath
    #: locator.
    SALES_PARTIAL = (By.PARTIAL_LINK_TEXT, "Sales")

    #: ``SalesP.java:20-21``'s ``customersButton``, carrying a raw ``&`` and
    #: differing from :attr:`LINK` only by the trailing ``/span``.
    CUSTOMERS_BUTTON = (By.XPATH, "//a[@href='/web#menu_id=447&action=48']/span")

    #: ``SalesP.java:23-24``'s ``createButton``.
    CREATE_BUTTON = (
        By.XPATH,
        "//button[@class='btn btn-primary btn-sm o-kanban-button-new btn-default']",
    )

    #: ``SalesP.java:26-27``'s ``customerName``, a generated field id.
    CUSTOMER_NAME = (By.XPATH, "//input[@id='o_field_input_470']")

    #: ``SalesP.java:29-30``'s ``address``, a generated field id.
    ADDRESS = (By.XPATH, "//input[@id='o_field_input_474']")

    #: ``SalesP.java:32-33``'s ``stateOptions``, a generated field id.
    STATE_OPTIONS = (By.XPATH, "//input[@id='o_field_input_477']")

    #: ``SalesP.java:35-36``'s ``createAndEditState``, with no spaces around
    #: its ``=`` where ``NotesP.java:14``'s near-twin has them.
    CREATE_AND_EDIT_STATE = (By.XPATH, "//li[.='Create and Edit...']")

    #: ``SalesP.java:38-39``'s ``stateName``, a generated field id.
    STATE_NAME = (By.XPATH, "//input[@id='o_field_input_516']")

    #: ``SalesP.java:41-42``'s ``stateCode``, a generated field id.
    STATE_CODE = (By.XPATH, "//input[@id='o_field_input_517']")

    #: ``SalesP.java:44-45``'s ``countryStateButton``, a generated field id.
    COUNTRY_STATE_BUTTON = (By.XPATH, "//input[@id='o_field_input_518']")

    #: ``SalesP.java:47-48``'s ``countrySelection``, a jQuery-UI generated id.
    COUNTRY_SELECTION = (By.XPATH, "//li[@id='ui-id-30']/a")

    #: ``SalesP.java:50-51``'s ``saveButton``, differing from
    #: :attr:`WARNING_BUTTON` only by the trailing ``/span``.
    SAVE_BUTTON = (By.XPATH, "//button[@class='btn btn-sm btn-primary']/span")

    #: ``SalesP.java:53-54``'s ``createCustomer``.
    CREATE_CUSTOMER = (
        By.XPATH,
        "//button[@class='btn btn-primary btn-sm o_form_button_save']",
    )

    #: ``SalesP.java:56-57``'s ``searchBar``.
    SEARCH_BAR = (By.XPATH, "//div[@class='o_searchview']/input")

    #: ``SalesP.java:59-60``'s ``nameCheck``.
    NAME_CHECK = (
        By.XPATH,
        "//strong[@class='o_kanban_record_title oe_partner_heading']/span",
    )

    #: ``SalesP.java:62-63``.  Declared and never referenced by any step
    #: class; kept for locator fidelity.  Differs from :attr:`SAVE_BUTTON`
    #: only by the absent trailing ``/span``.
    WARNING_BUTTON = (By.XPATH, "//button[@class='btn btn-sm btn-primary']")

    #: ``SalesP.java:65-66``'s ``warning``.
    WARNING = (By.XPATH, "//div[@class='o_notification_manager']")

    #: ``SalesP.java:68-69``, the reference's only ``List<WebElement>`` field
    #: and this page's only plural locator - hence
    #: :attr:`PLURAL_LOCATORS`, hence ``BasePage.find_all``.  Every kanban
    #: customer card on the Customers list.  Declared and never referenced by
    #: any step class; kept for locator fidelity, and positioned here because
    #: the Java file declares it between ``warning`` and ``link``.
    ALL_CUSTOMERS = (
        By.XPATH,
        "//div[@class='oe_kanban_global_click o_res_partner_kanban o_kanban_record']",
    )

    #: ``SalesP.java:71-72``.  Declared and never referenced by any step
    #: class; kept for locator fidelity.  Differs from
    #: :attr:`CUSTOMERS_BUTTON` only by the absent trailing ``/span``, and
    #: carries the same raw ``&``.
    LINK = (By.XPATH, "//a[@href='/web#menu_id=447&action=48']")

    #: ``SalesP.java:74-75``.  Declared and never referenced by any step
    #: class; kept for locator fidelity.  The one selector in the suite whose
    #: attribute value is double-quoted, written single-quoted in Python so
    #: those double quotes reach the browser intact.
    DETAILS = (By.XPATH, '//div[@class="oe_kanban_details"]//span')
