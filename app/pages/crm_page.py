r"""CRM page object - the port of ``CrmP.java``.

The 28 ``@FindBy`` fields of ``CrmP.java:13-95``, at pinned revision
``47e9d697e4a9a85da889f94a846fdf47af28a240``, as locator constants in Java
declaration order - part of the contract, since
:attr:`~app.pages.base_page.BasePage.LOCATORS` is built from the class body in
source order and compared against it.  The Java class declares no method beyond
its constructor, so this module declares constants only.  AAP 0.4.1 pairs it
with ``features/Crm.feature`` and ``crm_steps.py``.

``features/Crm.feature:1`` carries ``@Smoke``, the suite's only occurrence of
the default tag filter (``CukesRunner.java:18``, ``behave.ini``'s
``default_tags``), so a default ``run-tests`` run selects this feature alone.

``CrmP`` declares the same selector under two names four times over; all eight
constants are reproduced, each with its own literal tuple: ``createButton``
(``:16``) with ``createCustomer`` (``:76``), ``createPipeline`` (``:34``) with
``createCustomerButton`` (``:82``), ``buttonPipeline`` (``:43``) with
``progressPipeline`` (``:64``), and ``opportunityTitleEdit`` (``:49``) with
``inputName`` (``:79``).  Collapsing a pair, or writing one as ``X = Y``, is
excluded: AAP 0.2.2 leaves the source's inconsistencies alone and each name is
reached from a different step.

Other quirks preserved byte-for-byte (AAP 0.8, *"Preserve, do not tidy"*): the
literal ``&CC`` in :attr:`CUSTOMER_ID` and :attr:`NAME_CUSTOMER`, and the raw
``&`` in the ``href`` values of :attr:`PIPELINE_SIDE_BUTTON` and
:attr:`CUSTOMER_SIDE_BUTTON`, stay unescaped, ``&amp;`` matching nothing in
XPath; :attr:`PRINT_BUTTON` keeps its single leading slash, an absolute path
rather than a descendant search; :attr:`OPPORTUNITY_TITLE` is ``(By.NAME,
"name")`` where :attr:`OPPORTUNITY_TITLE_EDIT` XPaths the same attribute; the
generated ids ``o_field_input_125`` and ``o_field_input_127`` and the
``data-id`` values ``1`` and ``2`` are kept; and ``findTitleTest`` and
``testVerify`` keep their names.  Per AAP 0.4.2 the imports are
:data:`~app.automation.By` and :class:`~app.pages.base_page.BasePage` alone -
no browser library even under ``typing.TYPE_CHECKING``, no configuration and
neither interaction helper, so the ``Keys`` sends and the drag sequence
(``Crm.java:108-118``) belong to ``crm_steps.py``, as do the 2-second wait
(``:18``) and the one 2-second delay (``:117``).  Importing this module has no
side effects and it never creates or quits a driver (AAP 0.3.3).
"""

from app.automation import By
from app.pages.base_page import BasePage

__all__ = ["CrmPage"]


class CrmPage(BasePage):
    """The CRM module's 28 locators, in ``CrmP.java`` declaration order.

    A declarative port of ``CrmP.java:13-95``.  Every constant below is a
    ``(By.X, "value")`` pair reproduced character-for-character from the
    ``@FindBy`` annotation it ports, and each cites the Java line and field
    name it comes from.

    .. code-block:: python

        page = CrmPage()                    # touches nothing at all
        page.crm_link.click()               # one find_element, right now
        wait_visible(CrmPage.CRM_LINK, 2)   # the tuple, 2s per Crm.java:18

    Four selectors appear twice under different names; that is the source's
    own duplication, reproduced deliberately, and the module docstring gives
    the pairs and the reasons.
    """

    #: 1. ``CrmP.java:13`` ``crmLink``.
    CRM_LINK = (By.PARTIAL_LINK_TEXT, "CRM")

    #: 2. ``CrmP.java:16`` ``createButton``. Odoo's access-key ``c`` Create
    #: button, used by ``Crm.java:29-30``
    #: (*User click on the pipeline button*). Identical selector to
    #: :attr:`CREATE_CUSTOMER` (duplicate pair 1 of 4).
    CREATE_BUTTON = (By.XPATH, "//button[@accesskey='c']")

    #: 3. ``CrmP.java:19`` ``opportunityTitle``, by name where
    #: :attr:`OPPORTUNITY_TITLE_EDIT` XPaths the same attribute.
    OPPORTUNITY_TITLE = (By.NAME, "name")

    #: 4. ``CrmP.java:22`` ``customer``.
    CUSTOMER = (
        By.XPATH,
        "//table[@class='o_group o_inner_group o_group_col_6']//div//div//input",
    )

    #: 5. ``CrmP.java:25`` ``customerId``, link text ``&CC`` with the raw
    #: ampersand kept unescaped.
    CUSTOMER_ID = (By.XPATH, "//a[.='&CC']")

    #: 6. ``CrmP.java:28`` ``expectedRevenue``.
    EXPECTED_REVENUE = (By.XPATH, "//div[@class='o_row']//input")

    #: 7. ``CrmP.java:31`` ``priority``.
    PRIORITY = (
        By.XPATH,
        "//table[@class='o_group o_inner_group o_group_col_6']//tr[4]//a[3]",
    )

    #: 8. ``CrmP.java:34`` ``createPipeline``. The dialog's ``close_dialog``
    #: confirm button, clicked and waited on at ``Crm.java:42-43``. Identical
    #: selector to :attr:`CREATE_CUSTOMER_BUTTON` (duplicate pair 2 of 4).
    CREATE_PIPELINE = (By.XPATH, "//button[@name='close_dialog']")

    #: 9. ``CrmP.java:37`` ``findTitleTest``, the source's name kept as written.
    FIND_TITLE_TEST = (By.XPATH, "//div[@data-id='1']/div[2]//strong//span")

    #: 10. ``CrmP.java:40`` ``totalPrice``.
    TOTAL_PRICE = (By.XPATH, "//div[@data-id='1']//b")

    #: 11. ``CrmP.java:43`` ``buttonPipeline``. The first kanban column's card
    #: body, clicked and waited on at ``Crm.java:72-73`` and waited on again at
    #: ``Crm.java:92``. Identical selector to :attr:`PROGRESS_PIPELINE`
    #: (duplicate pair 3 of 4).
    BUTTON_PIPELINE = (By.XPATH, "//div[@data-id='1']/div[2]")

    #: 12. ``CrmP.java:46`` ``editButton``.
    EDIT_BUTTON = (By.XPATH, "//button[@accesskey='a']")

    #: 13. ``CrmP.java:49`` ``opportunityTitleEdit``. The title field in the
    #: edit form, cleared then sent the outline's ``opportunity`` value plus
    #: ``Keys.ENTER`` at ``Crm.java:75-76``. Identical selector to
    #: :attr:`INPUT_NAME` (duplicate pair 4 of 4).
    OPPORTUNITY_TITLE_EDIT = (By.XPATH, "//input[@name='name']")

    #: 14. ``CrmP.java:52`` ``expectedRevenueEdit``, the server-generated id
    #: ``o_field_input_125`` preserved as declared.
    EXPECTED_REVENUE_EDIT = (By.XPATH, "//input[@id='o_field_input_125']")

    #: 15. ``CrmP.java:55`` ``probabilityEdit``, the generated id
    #: ``o_field_input_127``.
    PROBABILITY_EDIT = (By.XPATH, "//input[@id='o_field_input_127']")

    #: 16. ``CrmP.java:58`` ``saveEdit``.
    SAVE_EDIT = (By.XPATH, "//button[@accesskey='s']")

    #: 17. ``CrmP.java:61`` ``pipelineSideButton``, whose ``href`` carries a raw
    #: ``&`` (``menu_id=274&action=365``), kept unescaped.
    PIPELINE_SIDE_BUTTON = (By.XPATH, "//a[@href='/web#menu_id=274&action=365']/span")

    #: 18. ``CrmP.java:64`` ``progressPipeline``. The drag *source* of the
    #: ``clickAndHold`` at ``Crm.java:110``. Identical selector to
    #: :attr:`BUTTON_PIPELINE` (duplicate pair 3 of 4); the source names the
    #: same card twice, once per role, and both names are kept.
    PROGRESS_PIPELINE = (By.XPATH, "//div[@data-id='1']/div[2]")

    #: 19. ``CrmP.java:67`` ``progressPipeline2``; the accessor is
    #: ``progress_pipeline2``, the trailing digit attached with no underscore.
    PROGRESS_PIPELINE2 = (By.XPATH, "//div[@data-id='2']/div[2]")

    #: 20. ``CrmP.java:70`` ``testVerify``, the source's name kept as written,
    #: with the ``//div[2]`` step where :attr:`FIND_TITLE_TEST` has ``/div[2]``.
    TEST_VERIFY = (By.XPATH, "//div[@data-id='2']//div[2]//strong//span")

    #: 21. ``CrmP.java:73`` ``customerSideButton``. The Customers entry in the
    #: CRM sidebar, again with a raw ``&`` in its ``href``
    #: (``menu_id=272&action=48``). Clicked and waited on at
    #: ``Crm.java:134-135``.
    CUSTOMER_SIDE_BUTTON = (By.XPATH, "//a[@href='/web#menu_id=272&action=48']")

    #: 22. ``CrmP.java:76`` ``createCustomer``. The Create button on the
    #: Customers view, clicked and waited on at ``Crm.java:136-137``. Identical
    #: selector to :attr:`CREATE_BUTTON` (duplicate pair 1 of 4).
    CREATE_CUSTOMER = (By.XPATH, "//button[@accesskey='c']")

    #: 23. ``CrmP.java:79`` ``inputName``. The new customer's name field, sent
    #: ``"Test" + Keys.ENTER`` at ``Crm.java:138``. Identical selector to
    #: :attr:`OPPORTUNITY_TITLE_EDIT` (duplicate pair 4 of 4).
    INPUT_NAME = (By.XPATH, "//input[@name='name']")

    #: 24. ``CrmP.java:82`` ``createCustomerButton``. Identical selector to
    #: :attr:`CREATE_PIPELINE` (duplicate pair 2 of 4).
    CREATE_CUSTOMER_BUTTON = (By.XPATH, "//button[@name='close_dialog']")

    #: 25. ``CrmP.java:85`` ``searchingText``.
    SEARCHING_TEXT = (By.XPATH, "//input[@class='o_searchview_input']")

    #: 26. ``CrmP.java:88`` ``nameCustomer``, span text ``&CC`` - the second
    #: raw-ampersand selector, kept unescaped.
    NAME_CUSTOMER = (By.XPATH, "//span[.='&CC']")

    #: 27. ``CrmP.java:91`` ``duePaymentButton``.
    DUE_PAYMENT_BUTTON = (By.XPATH, "//a[@data-section='print']")

    #: 28. ``CrmP.java:94`` ``printButton``. Clicked at ``Crm.java:149``. The
    #: only **absolute** path in this page: a single leading slash, so it is
    #: anchored at the document root rather than being the descendant search a
    #: leading ``//`` would perform. Not normalized.
    PRINT_BUTTON = (
        By.XPATH,
        "/html/body/div[1]/div[2]/div[1]/div[2]/div[2]/div/div[1]/button",
    )
