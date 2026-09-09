r"""CRM page object - the port of ``CrmP.java``.

Anchor
------
``src/main/java/com/testinium/pages/CrmP.java`` at pinned revision
``47e9d697e4a9a85da889f94a846fdf47af28a240``, held REFERENCE by AAP 0.2.1 and
never modified.  That class is 101 lines and contains a constructor calling
``PageFactory.initElements(Driver.getDriver(), this)`` plus **28**
``@FindBy`` fields at lines 13-95 - and **no other methods**.  This module is
therefore purely declarative: 28 locator constants and nothing else.  AAP
0.4.1 pairs it with ``features/Crm.feature`` and ``features/steps/crm_steps.py``.

Why this page comes first
-------------------------
``features/Crm.feature:1`` carries ``@Smoke``, the only occurrence of that tag
in the whole ten-feature suite, and ``@Smoke`` is the default tag filter
(``CukesRunner.java:18``, carried into ``behave.ini`` as ``default_tags``).  A
default ``run-tests`` invocation therefore selects **this feature alone** - one
outline and three plain scenarios - which AAP 0.6 confirms against the single
usable committed baseline artifact, a report holding exactly one feature.  A
defect in this file is a defect in the default run.

What the accessors are, and where they come from
------------------------------------------------
Nothing below installs an accessor; :class:`~app.pages.base_page.BasePage`
does, in ``__init_subclass__``, which is the port's stand-in for
``PageFactory.initElements``.  Declaring the constant is the whole of the work:

======================================  =======================================
Access form                             Yields
======================================  =======================================
``CrmPage.CREATE_BUTTON``               the ``(By.XPATH, "...")`` **tuple**,
``page.CREATE_BUTTON``                  for ``wait_visible(locator, 2)``, the
                                        keyboard-input helper and the
                                        per-module parity test
``page.create_button``                  the live ``WebElement``, re-resolved on
                                        every access, for ``.click()``,
                                        ``.send_keys()``, ``.clear()`` and
                                        ``.text``
======================================  =======================================

``CrmPage.LOCATORS`` likewise is not declared here: ``BasePage`` publishes it
as an immutable mapping in **class-body declaration order**, which is Java
``@FindBy`` order, so ``tests/test_pages.py`` can compare the two orders
directly.  ``PLURAL_LOCATORS`` is left inherited and empty because ``CrmP``
declares no ``List<WebElement>`` field - the suite's only one is
``SalesP.java:69``.

The accessor name is exactly the constant lower-cased, which makes item 19 read
``progress_pipeline2``: the digit stays attached with no underscore before it,
and ``crm_steps.py`` is coded against that spelling.

Four duplicate pairs: eight constants, four distinct selectors - intentional
---------------------------------------------------------------------------
``CrmP`` declares the same selector under two different names, four times over.
All eight constants are reproduced, each with its own literal tuple:

=================================  ==================================  ========
Selector                           Declared as                         Lines
=================================  ==================================  ========
``//button[@accesskey='c']``       ``CREATE_BUTTON``,                  16, 76
                                   ``CREATE_CUSTOMER``
``//button[@name='close_dialog']`` ``CREATE_PIPELINE``,                34, 82
                                   ``CREATE_CUSTOMER_BUTTON``
``//div[@data-id='1']/div[2]``     ``BUTTON_PIPELINE``,                43, 64
                                   ``PROGRESS_PIPELINE``
``//input[@name='name']``          ``OPPORTUNITY_TITLE_EDIT``,         49, 79
                                   ``INPUT_NAME``
=================================  ==================================  ========

Collapsing a pair, or writing one as ``X = Y``, is specifically excluded on two
grounds.  AAP 0.2.2 leaves the source's inconsistencies outside the deviation
inventory - *"Correcting the source's other inconsistencies"* is out of scope -
and AAP 0.4.1 obliges ``tests/test_steps_crm.py`` to assert that each step uses
*the name its Java original used*; a merged pair would make some of those
assertions unwritable.  Separate literals also mean a future edit to one half
cannot silently move the other.

Other quirks preserved byte-for-byte (AAP 0.8, *"Preserve, do not tidy"*)
-------------------------------------------------------------------------
* **The literal ``&CC``** in ``CUSTOMER_ID`` and ``NAME_CUSTOMER``.  A raw
  ampersand needs no escaping in a Python string and must not become ``&amp;``;
  it is test data baked into a selector and is not parameterized.
* **Raw ``&`` inside ``href`` values** in ``PIPELINE_SIDE_BUTTON``
  (``menu_id=274&action=365``) and ``CUSTOMER_SIDE_BUTTON``
  (``menu_id=272&action=48``).  Same rule.
* **``PRINT_BUTTON`` starts with a single slash**, ``/html/body/...``: an
  absolute path from the document root, not the descendant search a leading
  ``//`` would give.  ``EmployeeP.java`` writes ``//html/body/...`` for its own
  fields; the two forms differ in real XPath semantics and each is kept as its
  own source has it.  Not normalized in either direction.
* **Two strategies for the same attribute.**  ``OPPORTUNITY_TITLE`` is
  ``(By.NAME, "name")`` while ``OPPORTUNITY_TITLE_EDIT`` is
  ``//input[@name='name']``.  Both kept as declared.
* **Hard-coded generated ids** ``o_field_input_125`` and ``o_field_input_127``,
  and hard-coded ``data-id`` values ``1`` and ``2``.  Brittle by nature; the
  request is parity, not repair.
* **Unhelpful names kept**: ``FIND_TITLE_TEST`` and ``TEST_VERIFY`` are the
  ports of ``findTitleTest`` and ``testVerify``.

Import boundary (AAP 0.4.2)
---------------------------
*"Page objects import ``app.automation`` for the current driver, nothing
else."*  This module imports exactly :data:`~app.automation.By` and
:class:`~app.pages.base_page.BasePage`.  In particular:

* **No browser-library import**, not even under ``typing.TYPE_CHECKING`` - a
  guarded import is still an import statement and would trip the grep-based
  boundary check.  ``By`` reaches this module re-exported from
  ``app.automation``, the only package in the port permitted to import it.
* **Neither of ``app.automation``'s two interaction helpers** - the
  keyboard-input one nor the action-chain one.  ``Crm.java`` is the one step
  class importing *both* ``Keys`` and ``Actions``, yet AAP 0.4.2 fixes the
  import sites for both helpers as the *step* modules - ``crm_steps``,
  ``notes_steps`` and ``sales_steps`` for the first, ``crm_steps`` and
  ``notes_steps`` for the second - and never a page module.
* **No ``app.config``**, no service, no reporting writer, no path helper, no
  web framework and no Gherkin engine.
* **No driver lifecycle.**  Nothing here creates, configures or quits a
  session; AAP 0.3.3 gives that to ``features/environment.py`` alone.

What this module deliberately does not contain
----------------------------------------------
No ``__init__`` (``BasePage``'s stores the optional driver and does nothing
else), no property, no method and no locator beyond the 28 - because ``CrmP``
has none.  In particular there is no ``create_pipeline()``, no
``edit_opportunity()`` and no drag-and-drop helper: ``Crm.java:108-118`` runs
its ``clickAndHold`` / ``moveToElement`` / ``release`` sequence inline through
``Actions``, and that sequence belongs at the ``crm_steps.py`` call site,
through the action-chain helper ``app.automation`` exposes for exactly that.
The 2-second ``WebDriverWait`` (``Crm.java:18``) and the
single ``Thread.sleep(2000)`` (``Crm.java:117``) belong at their call sites
too, with the timeout supplied per call.

Importing this module has no side effects: it starts no browser, reads no
configuration and touches no filesystem, so the unit suite imports it on a
machine with no browser installed.
"""

from app.automation import By
from app.pages.base_page import BasePage

__all__ = ["CrmPage"]


class CrmPage(BasePage):
    """The CRM module's 28 locators, in ``CrmP.java`` declaration order.

    A declarative port of ``CrmP.java:13-95``.  Every constant below is a
    ``(By.X, "value")`` pair reproduced character-for-character from the
    ``@FindBy`` annotation it ports, and each carries the Java line it comes
    from and the ``Crm.java`` step that consumes it, so the parity test in
    ``tests/test_steps_crm.py`` has a stated expectation for every one.

    .. code-block:: python

        page = CrmPage()                    # touches nothing at all
        page.crm_link.click()               # one find_element, right now
        wait_visible(CrmPage.CRM_LINK, 2)   # the tuple, 2s per Crm.java:18

    Four selectors appear twice under different names; that is the source's
    own duplication, reproduced deliberately, and the module docstring gives
    the pairs and the reasons.
    """

    #: 1. ``CrmP.java:13`` ``crmLink``. The CRM entry in the Odoo main menu.
    #: Clicked, then waited on, by ``Crm.java:23-24``
    #: (*User click on the crm dashboard*) - the ``When`` step every one of
    #: the feature's four scenarios opens with.
    CRM_LINK = (By.PARTIAL_LINK_TEXT, "CRM")

    #: 2. ``CrmP.java:16`` ``createButton``. Odoo's access-key ``c`` Create
    #: button, used by ``Crm.java:29-30``
    #: (*User click on the pipeline button*). Identical selector to
    #: :attr:`CREATE_CUSTOMER` (duplicate pair 1 of 4).
    CREATE_BUTTON = (By.XPATH, "//button[@accesskey='c']")

    #: 3. ``CrmP.java:19`` ``opportunityTitle``. The new-pipeline title field,
    #: located **by name** where :attr:`OPPORTUNITY_TITLE_EDIT` uses an XPath
    #: on the same attribute; both strategies are the source's. Receives
    #: ``"test" + Keys.ENTER`` at ``Crm.java:36``.
    OPPORTUNITY_TITLE = (By.NAME, "name")

    #: 4. ``CrmP.java:22`` ``customer``. The customer autocomplete inside the
    #: inner group table; clicked at ``Crm.java:37`` to open the suggestion
    #: list that :attr:`CUSTOMER_ID` then picks from.
    CUSTOMER = (
        By.XPATH,
        "//table[@class='o_group o_inner_group o_group_col_6']//div//div//input",
    )

    #: 5. ``CrmP.java:25`` ``customerId``. The suggested customer whose link
    #: text is the literal ``&CC`` - a raw ampersand, kept unescaped and
    #: unparameterized. Clicked at ``Crm.java:38``.
    CUSTOMER_ID = (By.XPATH, "//a[.='&CC']")

    #: 6. ``CrmP.java:28`` ``expectedRevenue``. The revenue input in the
    #: dialog's ``o_row``; cleared then sent ``"8" + Keys.ENTER`` at
    #: ``Crm.java:39-40``.
    EXPECTED_REVENUE = (By.XPATH, "//div[@class='o_row']//input")

    #: 7. ``CrmP.java:31`` ``priority``. The third star in the fourth row of
    #: the inner group table, clicked at ``Crm.java:41``.
    PRIORITY = (
        By.XPATH,
        "//table[@class='o_group o_inner_group o_group_col_6']//tr[4]//a[3]",
    )

    #: 8. ``CrmP.java:34`` ``createPipeline``. The dialog's ``close_dialog``
    #: confirm button, clicked and waited on at ``Crm.java:42-43``. Identical
    #: selector to :attr:`CREATE_CUSTOMER_BUTTON` (duplicate pair 2 of 4).
    CREATE_PIPELINE = (By.XPATH, "//button[@name='close_dialog']")

    #: 9. ``CrmP.java:37`` ``findTitleTest``. The title text of the first
    #: kanban card, read at ``Crm.java:60`` (*User can see new pipeline*,
    #: expecting ``"test"``) and again at ``Crm.java:94``
    #: (*User can verify the information*, expecting ``"Test2"``). The name is
    #: the source's; kept as-is.
    FIND_TITLE_TEST = (By.XPATH, "//div[@data-id='1']/div[2]//strong//span")

    #: 10. ``CrmP.java:40`` ``totalPrice``. The first column's total, parsed as
    #: an ``int`` at ``Crm.java:48`` where ``+ 8`` is compared against ``89``.
    TOTAL_PRICE = (By.XPATH, "//div[@data-id='1']//b")

    #: 11. ``CrmP.java:43`` ``buttonPipeline``. The first kanban column's card
    #: body, clicked and waited on at ``Crm.java:72-73`` and waited on again at
    #: ``Crm.java:92``. Identical selector to :attr:`PROGRESS_PIPELINE`
    #: (duplicate pair 3 of 4).
    BUTTON_PIPELINE = (By.XPATH, "//div[@data-id='1']/div[2]")

    #: 12. ``CrmP.java:46`` ``editButton``. Odoo's access-key ``a`` Edit
    #: button, clicked at ``Crm.java:74``.
    EDIT_BUTTON = (By.XPATH, "//button[@accesskey='a']")

    #: 13. ``CrmP.java:49`` ``opportunityTitleEdit``. The title field in the
    #: edit form, cleared then sent the outline's ``opportunity`` value plus
    #: ``Keys.ENTER`` at ``Crm.java:75-76``. Identical selector to
    #: :attr:`INPUT_NAME` (duplicate pair 4 of 4).
    OPPORTUNITY_TITLE_EDIT = (By.XPATH, "//input[@name='name']")

    #: 14. ``CrmP.java:52`` ``expectedRevenueEdit``. The revenue field in the
    #: edit form, addressed by the server-generated id ``o_field_input_125``;
    #: cleared then sent the outline's ``revenue`` value plus ``Keys.ENTER`` at
    #: ``Crm.java:77-78``. The generated id is brittle and is preserved.
    EXPECTED_REVENUE_EDIT = (By.XPATH, "//input[@id='o_field_input_125']")

    #: 15. ``CrmP.java:55`` ``probabilityEdit``. The probability field, likewise
    #: addressed by a generated id, ``o_field_input_127``; cleared then sent the
    #: outline's ``probability`` value plus ``Keys.ENTER`` at ``Crm.java:79-80``
    #: and waited on at ``Crm.java:85``.
    PROBABILITY_EDIT = (By.XPATH, "//input[@id='o_field_input_127']")

    #: 16. ``CrmP.java:58`` ``saveEdit``. Odoo's access-key ``s`` Save button,
    #: clicked at ``Crm.java:86`` (*User can save information*).
    SAVE_EDIT = (By.XPATH, "//button[@accesskey='s']")

    #: 17. ``CrmP.java:61`` ``pipelineSideButton``. The Pipeline entry in the
    #: CRM sidebar, whose ``href`` carries a raw ``&`` between its two query
    #: parameters (``menu_id=274&action=365``) - kept unescaped. Clicked at
    #: ``Crm.java:91``.
    PIPELINE_SIDE_BUTTON = (By.XPATH, "//a[@href='/web#menu_id=274&action=365']/span")

    #: 18. ``CrmP.java:64`` ``progressPipeline``. The drag *source* of the
    #: ``clickAndHold`` at ``Crm.java:110``. Identical selector to
    #: :attr:`BUTTON_PIPELINE` (duplicate pair 3 of 4); the source names the
    #: same card twice, once per role, and both names are kept.
    PROGRESS_PIPELINE = (By.XPATH, "//div[@data-id='1']/div[2]")

    #: 19. ``CrmP.java:67`` ``progressPipeline2``. The drag *target* of the
    #: ``moveToElement`` at ``Crm.java:112``, in the second kanban column. Its
    #: accessor is ``progress_pipeline2`` - the trailing digit stays attached,
    #: with no underscore before it.
    PROGRESS_PIPELINE2 = (By.XPATH, "//div[@data-id='2']/div[2]")

    #: 20. ``CrmP.java:70`` ``testVerify``. The card title in the second column
    #: after the drag, read at ``Crm.java:123`` and compared against ``"test"``
    #: (*User can see the new changes in progress*). Note the ``//div[2]``
    #: descendant step where :attr:`FIND_TITLE_TEST` uses ``/div[2]``; the
    #: difference is the source's and is preserved.
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

    #: 24. ``CrmP.java:82`` ``createCustomerButton``. The customer dialog's
    #: ``close_dialog`` confirm button, clicked and waited on at
    #: ``Crm.java:139-140``. Identical selector to :attr:`CREATE_PIPELINE`
    #: (duplicate pair 2 of 4).
    CREATE_CUSTOMER_BUTTON = (By.XPATH, "//button[@name='close_dialog']")

    #: 25. ``CrmP.java:85`` ``searchingText``. The view's search input, sent
    #: ``"aa" + Keys.ENTER`` at ``Crm.java:141``.
    SEARCHING_TEXT = (By.XPATH, "//input[@class='o_searchview_input']")

    #: 26. ``CrmP.java:88`` ``nameCustomer``. The result whose span text is the
    #: literal ``&CC`` - the second raw-ampersand selector, kept unescaped -
    #: clicked and waited on at ``Crm.java:147-148``.
    NAME_CUSTOMER = (By.XPATH, "//span[.='&CC']")

    #: 27. ``CrmP.java:91`` ``duePaymentButton``. The Print section of the
    #: customer's action menu: waited on at ``Crm.java:150`` and clicked at
    #: ``Crm.java:151``, the feature's last interaction.
    DUE_PAYMENT_BUTTON = (By.XPATH, "//a[@data-section='print']")

    #: 28. ``CrmP.java:94`` ``printButton``. Clicked at ``Crm.java:149``. The
    #: only **absolute** path in this page: a single leading slash, so it is
    #: anchored at the document root rather than being the descendant search a
    #: leading ``//`` would perform. Not normalized.
    PRINT_BUTTON = (
        By.XPATH,
        "/html/body/div[1]/div[2]/div[1]/div[2]/div[2]/div/div[1]/button",
    )
