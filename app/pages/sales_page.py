r"""Sales page object - the port of ``SalesP.java``, and the port's only plural page.

The Java anchor
---------------
``src/main/java/com/testinium/pages/SalesP.java`` at pinned revision
``47e9d697e4a9a85da889f94a846fdf47af28a240``, held REFERENCE by AAP 0.2.1 and
never modified.  Seventy-eight lines, of which two things matter: the
constructor at ``:13-15``, whose entire body is
``PageFactory.initElements(Driver.getDriver(), this)``, and twenty ``@FindBy``
fields at ``:17-75``.

The class has **no other member**.  So neither does this module: the twenty
locator constants and one :attr:`~app.pages.base_page.BasePage.PLURAL_LOCATORS`
declaration are the whole of it.  There is no ``__init__``, no property, no
method and no helper - not a ``create_customer(...)`` convenience over the form
fields, not a ``count_customers()`` over the plural accessor.  Resolution is
:class:`~app.pages.base_page.BasePage`'s job and behaviour is
``features/steps/sales_steps.py``'s; a helper here would be an addition the
request never asked for.

The port's only plural locator
------------------------------
``SalesP.java:68-69`` declares one ``@FindBy`` over the Customers kanban cards -
the selector is :attr:`SalesPage.ALL_CUSTOMERS` below - and binds it to::

    public List<WebElement> allCustomers;

That is the **single** ``List<WebElement>`` field in the whole reference suite -
the other 130 of the 131 ``@FindBy`` fields across the ten page classes are
scalar ``WebElement``.  :attr:`SalesPage.PLURAL_LOCATORS` names it, and
``BasePage`` reads that set to resolve :attr:`~SalesPage.all_customers` through
``find_elements`` and hand back the list unchanged - the empty list included,
since nothing matching is not an error for a plural lookup.  Every other
accessor on this page stays singular and raises rather than returning an empty
list.  This module is therefore the only consumer of ``BasePage.find_all``, and
the reason that method exists.

It is declared even though nothing calls it
-------------------------------------------
A search across all eleven reference step classes finds **zero** references to
``allCustomers``, and it is not alone: ``warningButton`` (``:62-63``), ``link``
(``:71-72``) and ``details`` (``:74-75``) are equally unreferenced, leaving
sixteen of the twenty fields actually reached by ``Sales.java``.  All twenty are
declared here regardless.  Locator fidelity is the parity obligation - each page
module's locators match its Java original's ``@FindBy`` declarations exactly,
and ``tests/test_pages.py`` counts twenty - so a dead field is carried, not
pruned, and no comment below suggests removing one.  AAP 0.8: *"Preserve, do not
tidy."*

Declaration order is part of the contract
-----------------------------------------
``BasePage.LOCATORS`` is built from the class body in declaration order and
nothing sorts it, so the ordered key list is assertable and the inventory test
asserts it.  The constants below therefore appear in ``SalesP.java`` order, and
in particular ``allCustomers`` sits *between* ``warning`` and ``link`` in the
Java file, which is why the tail of this class reads
``WARNING`` -> ``ALL_CUSTOMERS`` -> ``LINK`` -> ``DETAILS``.

Selector quirks, preserved byte-for-byte
----------------------------------------
* :attr:`~SalesPage.DETAILS` **quotes its attribute value with double quotes.**
  ``SalesP.java:74`` escapes them - ``"//div[@class=\"oe_kanban_details\"]//span"``
  - so the selector the browser receives is
  ``//div[@class="oe_kanban_details"]//span``.  It is written below as a
  single-quoted Python string so those double quotes survive verbatim; swapping
  them for single quotes would edit the selector.  It is the only attribute
  value quoted this way on any of the ten pages.
* :attr:`~SalesPage.CREATE_AND_EDIT_STATE` has **no spaces around the ``=``**:
  ``//li[.='Create and Edit...']``.  ``NotesP.java:14`` expresses the same idea
  as ``//a[. = 'Create and Edit...']`` - with spaces, and on a different element
  - and ``app/pages/notes_page.py`` carries it exactly that way.  Neither is
  normalized towards the other and the two pages share no constant.
* :attr:`~SalesPage.SAVE_BUTTON` and :attr:`~SalesPage.WARNING_BUTTON`
  (``:50`` and ``:62``) differ **only** by a trailing ``/span``, as do
  :attr:`~SalesPage.CUSTOMERS_BUTTON` and :attr:`~SalesPage.LINK` (``:20`` and
  ``:71``).  Both pairs are declared and kept distinct; collapsing either into
  one constant would drop a locator the inventory counts.
* The two ``/web#menu_id=447&action=48`` selectors carry a **raw ``&``**.  It
  stays unescaped: this is a Python string handed to XPath, not markup, and
  ``&amp;`` would not match.
* Six selectors name generated field ids - ``o_field_input_470``, ``474``,
  ``477``, ``516``, ``517``, ``518`` - and one names a jQuery-UI id,
  ``ui-id-30``.  They are brittle by nature, they are what the source has, and
  they are reproduced unchanged.
* Java-side whitespace inside the annotations - ``@FindBy(xpath  =`` at ``:20``,
  ``@FindBy(xpath ="..." )`` at ``:26``, ``public  WebElement`` at ``:45``,
  ``:48`` and ``:51`` - is Java syntax rather than selector text and has no
  Python counterpart, so it is the one thing on this page with nothing to
  preserve.

Import boundary (AAP 0.4.2)
---------------------------
Two imports, and no others may be added.  :data:`~app.automation.By` comes from
``app.automation``, the only package in the port permitted to import the
browser-automation library at all; that library is never named in this module,
not even under ``typing.TYPE_CHECKING``, because a guarded import is still an
import statement and the suite's boundary check is textual - which is also why
the name does not appear anywhere in this file, prose included.

The **keyboard helper** of ``app/automation/interactions.py`` is deliberately
absent from those two lines.  ``Sales.java:9`` imports ``Keys`` and
``Sales.java:68`` sends ``name + Keys.ENTER`` into the search bar, but AAP 0.4.2
fixes that import site as ``features/steps/sales_steps.py``: *"Page objects
import ``app.automation`` for the current driver, nothing else."*  Nor is there
an ``Actions`` equivalent to import - ``Sales.java`` uses none.

Also absent, and each for its own reason: ``app.config``, because page objects
never read configuration and ``Sales.java`` reads none either; any service,
reporting writer or path helper, which sit downstream of this layer; Flask and
the Gherkin engine, which this module must stay importable without.  Nothing
here creates, configures or quits a driver - AAP 0.3.3 gives the session
lifecycle one owner, ``features/environment.py``.  Importing this module has no
side effect whatever: it starts no browser, reads no ``configuration.properties``
and touches no filesystem, which is what lets the unit suite import it on a
machine with no browser installed.

Where the wait and the behaviour live
-------------------------------------
``Sales.java:15-17`` builds its page object and a **4-second**
``WebDriverWait`` as instance fields, at glue construction.  In the port both
come from the behave context per scenario, and the 4 seconds is supplied at each
``features/steps/sales_steps.py`` call site, because ``app/automation/waits.py``
takes its timeout per call: the source fixes a different timeout per step class
- 2s, 3s, 4s and 20s - and no default may paper over that difference.  This
module holds no timeout, no wait and no sleep.

Feature context
---------------
``features/Sales.feature`` carries **no feature-level tag**, so the default
``@Smoke`` filter (``CukesRunner.java:18``) does not select it; it is reachable
by a negative tag expression or by naming the file.  Its title begins
``.... app Sales feature`` - a source oddity AAP 0.6 flags as a hazard for the
JSON ``id`` slug rule, and which is preserved verbatim like every other line of
Gherkin.  One Background supplying the shared ``Given User login to test other
features`` precondition, two plain scenarios and one Scenario Outline with a
single Examples row.
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

    # -- Sales dashboard and the Customers list ----------------------------
    # SalesP.java:17-30. The entry path of every scenario in the feature:
    # dashboard -> Customers -> Create.

    #: ``SalesP.java:17-18`` (``partialLinkText``, the page's only non-XPath
    #: locator).  Clicked and then waited on in
    #: ``Sales.java:21-22``.
    SALES_PARTIAL = (By.PARTIAL_LINK_TEXT, "Sales")

    #: ``SalesP.java:20-21``.  Clicked and waited on in ``Sales.java:28-29``,
    #: where the step then compares the page title, and again in
    #: ``Sales.java:60-61`` after a save.  Differs from :attr:`LINK` only by
    #: the trailing ``/span``, and carries a raw ``&``.
    CUSTOMERS_BUTTON = (By.XPATH, "//a[@href='/web#menu_id=447&action=48']/span")

    #: ``SalesP.java:23-24``.  The kanban "Create" button, clicked and waited
    #: on in ``Sales.java:42-43`` and again in ``Sales.java:82-83`` for the
    #: blank-form error scenario.
    CREATE_BUTTON = (
        By.XPATH,
        "//button[@class='btn btn-primary btn-sm o-kanban-button-new btn-default']",
    )

    # -- New-customer form -------------------------------------------------
    # SalesP.java:26-48. Six generated field ids and one jQuery-UI id, filled
    # in order by Sales.java:44-51.

    #: ``SalesP.java:26-27``.  Receives ``"Lucas"`` at ``Sales.java:44``.
    CUSTOMER_NAME = (By.XPATH, "//input[@id='o_field_input_470']")

    #: ``SalesP.java:29-30``.  Receives
    #: ``"1 boulevard auguste rodin 75000"`` at ``Sales.java:45``.
    ADDRESS = (By.XPATH, "//input[@id='o_field_input_474']")

    #: ``SalesP.java:32-33``.  The state autocomplete, clicked at
    #: ``Sales.java:46`` to open its dropdown.
    STATE_OPTIONS = (By.XPATH, "//input[@id='o_field_input_477']")

    #: ``SalesP.java:35-36``.  The dropdown's "Create and Edit..." entry,
    #: clicked at ``Sales.java:47``.  No spaces around the ``=``, unlike the
    #: ``<a>``-based near-twin on ``app/pages/notes_page.py``.
    CREATE_AND_EDIT_STATE = (By.XPATH, "//li[.='Create and Edit...']")

    #: ``SalesP.java:38-39``.  Receives ``"Albania"`` at ``Sales.java:48``.
    STATE_NAME = (By.XPATH, "//input[@id='o_field_input_516']")

    #: ``SalesP.java:41-42``.  Receives ``"78"`` at ``Sales.java:49``.
    STATE_CODE = (By.XPATH, "//input[@id='o_field_input_517']")

    #: ``SalesP.java:44-45``.  The country field of the state sub-form,
    #: clicked at ``Sales.java:50``.
    COUNTRY_STATE_BUTTON = (By.XPATH, "//input[@id='o_field_input_518']")

    #: ``SalesP.java:47-48``.  The country suggestion the flow settles on,
    #: clicked at ``Sales.java:51``.  A jQuery-UI generated id.
    COUNTRY_SELECTION = (By.XPATH, "//li[@id='ui-id-30']/a")

    # -- Saving, and verifying the saved customer --------------------------
    # SalesP.java:50-66.

    #: ``SalesP.java:50-51``.  Saves the state sub-form; clicked and waited on
    #: at ``Sales.java:56-57``.  Differs from :attr:`WARNING_BUTTON` only by
    #: the trailing ``/span``.
    SAVE_BUTTON = (By.XPATH, "//button[@class='btn btn-sm btn-primary']/span")

    #: ``SalesP.java:53-54``.  Saves the customer record; clicked and waited on
    #: at ``Sales.java:58-59``, and clicked on an empty form at
    #: ``Sales.java:84`` to provoke the validation error.
    CREATE_CUSTOMER = (
        By.XPATH,
        "//button[@class='btn btn-primary btn-sm o_form_button_save']",
    )

    #: ``SalesP.java:56-57``.  Receives the searched name followed by
    #: ``Keys.ENTER`` at ``Sales.java:68`` - the keyboard send lives in the
    #: step module, not here.
    SEARCH_BAR = (By.XPATH, "//div[@class='o_searchview']/input")

    #: ``SalesP.java:59-60``.  The kanban card heading whose text
    #: ``Sales.java:72`` reads back to check the customer was created.
    NAME_CHECK = (
        By.XPATH,
        "//strong[@class='o_kanban_record_title oe_partner_heading']/span",
    )

    #: ``SalesP.java:62-63``.  Declared and never referenced by any step
    #: class; kept for locator fidelity.  Differs from :attr:`SAVE_BUTTON`
    #: only by the absent trailing ``/span``.
    WARNING_BUTTON = (By.XPATH, "//button[@class='btn btn-sm btn-primary']")

    #: ``SalesP.java:65-66``.  The notification area whose text
    #: ``Sales.java:91`` reads for the blank-form error
    #: ``"The following fields are invalid:"``.
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
