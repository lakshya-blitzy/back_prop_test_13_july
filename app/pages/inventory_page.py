r"""Inventory page object - the port of ``InventoryP.java``.

Java anchor
-----------
``src/main/java/com/testinium/pages/InventoryP.java`` at pinned revision
``47e9d697e4a9a85da889f94a846fdf47af28a240``, held REFERENCE by AAP 0.2.1 and
never modified.  38 lines: a constructor whose whole body is
``PageFactory.initElements(Driver.getDriver(), this)`` (``InventoryP.java:10-12``)
and eight ``@FindBy`` fields (``InventoryP.java:14-36``).  **No methods beyond
that constructor**, so this module declares locator constants and nothing else -
the accessors, the :attr:`~app.pages.base_page.BasePage.LOCATORS` inventory and
the lazy resolution all come from :class:`~app.pages.base_page.BasePage`, which
AAP 0.4.1 makes the single carrier of ``PageFactory.initElements``'s
lazy-resolution semantics for all ten page objects.

The eight locators, in Java declaration order
---------------------------------------------
Order is part of the contract, not presentation: ``BasePage.__init_subclass__``
builds ``LOCATORS`` from the class body in source order, and
``tests/test_pages.py`` compares that inventory against the ``@FindBy`` order of
the Java class this module ports.

=====================  ===========================  ====================  =====
Constant               Accessor                     Java field            Line
=====================  ===========================  ====================  =====
``INVENTORY_MODULE``   ``inventory_module``         ``inventoryModule``   14-15
``PRODUCTS``           ``products``                 ``products``          17-18
``CREATE_BTN``         ``create_btn``               ``createBtn``         20-21
``SAVE_BTN``           ``save_btn``                 ``saveBtn``           23-24
``FIELD_ERROR``        ``field_error``              ``fieldError``        26-27
``PRODUCT_NAME``       ``product_name``             ``productName``       29-30
``PRODUCTS_LIST``      ``products_list``            ``productsList``      32-33
``CREATED_PRODUCT``    ``created_product``          ``createdProduct``    35-36
=====================  ===========================  ====================  =====

Two consumers, which is unique in this package
----------------------------------------------
Every other page in ``app/pages`` is imported by exactly one step module.  This
one is imported by two, because two Java step classes instantiate ``InventoryP``:

* ``features/steps/inventory_steps.py``, the port of
  ``step_definitions/Inventory.java`` (``InventoryP inventory = new InventoryP()``
  at ``Inventory.java:12``), which drives ``features/Inventory.feature``.  Its
  nine steps use all eight fields: ``inventoryModule.click()``,
  ``visibilityOf(products)`` then ``click()``, a bare
  ``getTitle().equals("Products - Odoo")``, ``createBtn.click()``,
  ``visibilityOf(saveBtn)`` then ``click()``, ``fieldError.isDisplayed()``,
  ``productName.sendKeys("IBM")``, ``productsList.isDisplayed()`` and
  ``createdProduct.isDisplayed()``.
* ``features/steps/notes_steps.py``, the port of ``step_definitions/Notes.java``,
  which declares ``InventoryP inventoryP`` and ``NotesP notesP`` side by side at
  ``Notes.java:16-17``.

That split is exactly as the Java has it and AAP 0.8's *"Preserve, do not tidy"*
keeps it: no locator moves between this page and ``app/pages/notes_page.py``, and
no Inventory locator is duplicated into the Notes page.  ``app/pages/__init__.py``
re-exports both classes, so one step module imports both from one place.

**No timeout appears in this file.**  Both consumers construct a 20-second
``WebDriverWait`` (``Inventory.java:13``, ``Notes.java:19``), and AAP 0.4.1 keeps
each explicit-wait timeout at its call site: the step modules pass ``20`` to
``app.automation``'s wait helpers.  A timeout constant here would be a second
home for a value that has exactly one.

Source quirks preserved verbatim
--------------------------------
Three of the eight selectors are fragile or odd, and each is carried
character-for-character under AAP 0.8:

* ``PRODUCT_NAME`` is the server-generated id ``o_field_input_479``.  Odoo mints
  those per render, so the selector is brittle by nature - it is **not** replaced
  with a name- or label-based locator and **no fallback is added**.
* ``PRODUCTS_LIST`` is ``//span[.='EY']``: a two-letter literal that is really
  test data baked into a selector.  Not parameterized, not lifted to a constant
  argument.
* ``CREATE_BTN`` selects the **hyphenated** class ``o-kanban-button-new`` while
  ``FIELD_ERROR`` selects the **underscored** ``o_notification_manager``.  Both
  are exactly as ``InventoryP.java:20`` and ``:26`` write them; the inconsistency
  is Odoo's markup and the source's, and normalizing either would break the
  lookup.

The accessor names keep the source's abbreviations too - ``createBtn`` becomes
``create_btn`` and ``saveBtn`` becomes ``save_btn``, not ``create_button`` or
``save_button``.  Other pages in this package spell the same concept
``CREATE_BUTTON``; that inconsistency is the source's and stays.

Import boundary (AAP 0.4.2)
---------------------------
*"Page objects import ``app.automation`` for the current driver, nothing else"*.
So this module imports exactly two names - :data:`~app.automation.By` for the
locator strategies and :class:`~app.pages.base_page.BasePage` for the mechanism -
and nothing further: no browser-library import of any kind, not even under
``typing.TYPE_CHECKING``, because ``app.automation`` is the only package in the
port permitted to import that library and a guarded import is still an import
statement - it would trip the grep-based boundary check the suite performs; no
``app.config``, because step modules read configuration and no
Inventory step reads any; and no service, reporting writer, path helper, web
framework or Gherkin engine.  Nothing here creates, configures or quits a driver
(AAP 0.3.3 gives that lifecycle one owner, ``features/environment.py``), and
importing this module has no side effects at all, which is what lets the unit
suite import it on a machine with no browser installed.

Feature context
---------------
``features/Inventory.feature`` carries **no feature-level tag** and holds four
plain scenarios, so the default ``@Smoke`` filter (``CukesRunner.java:18``) does
not select it - it is reachable only by a negative tag expression such as
``not @Smoke`` or by a scenario-level tag.  Its header is also mis-titled,
``Feature: Testinium app Inventory feature``, a title it shares with
``Contact.feature:1`` and which therefore produces a duplicate JSON ``id``; AAP
0.2.2 preserves both the title and the collision, and neither is this file's
concern.
"""

from app.automation import By
from app.pages.base_page import BasePage

__all__ = ["InventoryPage"]


class InventoryPage(BasePage):
    r"""Locators for the Odoo Inventory module: navigation, product form, results.

    The port of ``InventoryP.java``.  Declarative by design - eight locator
    constants, no ``__init__``, no properties written by hand and no methods,
    because the Java class has none either (AAP 0.4.1's *"no methods beyond the
    constructor"*).  ``features/steps/inventory_steps.py`` drives the fields
    directly, so there is deliberately no ``create_product()`` and no navigation
    helper here.

    :class:`~app.pages.base_page.BasePage` supplies everything else at class
    creation:

    * ``InventoryPage.LOCATORS`` - the eight constants below as an immutable
      ``{name: (By.X, "value")}`` mapping, in this class body's order, which is
      ``InventoryP.java``'s ``@FindBy`` order.
    * ``page.inventory_module`` ... ``page.created_product`` - one read-only
      accessor per constant, re-resolving through ``find_element`` on **every**
      access and caching nothing, exactly as the ``PageFactory`` proxy it
      replaces.
    * ``InventoryPage.CREATE_BTN`` and friends - the raw locator tuple, for
      ``wait_visible(locator, timeout)`` and the per-module parity tests.

    ``PLURAL_LOCATORS`` is not overridden: ``InventoryP.java`` declares no
    ``List<WebElement>`` field, so all eight accessors resolve to a single
    element.

    Usage, with the 20-second timeout its two step modules supply at the call
    site::

        from app.automation import wait_visible
        from app.pages import InventoryPage

        inventory = InventoryPage()            # touches nothing at all
        inventory.inventory_module.click()     # one find_element, right now
        wait_visible(InventoryPage.PRODUCTS, 20)
        inventory.products.click()

    Constructing it is free and side-effect-free: ``InventoryPage()`` takes this
    worker's live session at each lookup, and ``InventoryPage(stub)`` injects one
    - the seam the parity tests use to assert these eight tuples reach
    ``find_element`` unchanged.
    """

    #: The Inventory entry in Odoo's top-level menu bar.
    #: ``@FindBy(partialLinkText = "Inventory")`` - ``InventoryP.java:14-15``.
    #: Clicked by the "Logged user clicks on Inventory Module" step of both
    #: consuming modules, with no wait in front of it: the session's 10-second
    #: implicit wait (``Driver.java:34``) is what makes the lookup retry.
    INVENTORY_MODULE = (By.PARTIAL_LINK_TEXT, "Inventory")

    #: The Products entry of the Inventory module's own menu.
    #: ``@FindBy(partialLinkText = "Products")`` - ``InventoryP.java:17-18``.
    #: Waited on for visibility at 20 seconds before it is clicked
    #: (``Inventory.java:22-23``); the last scenario of the feature clicks it
    #: twice, returning to the list to confirm a saved product appears there.
    PRODUCTS = (By.PARTIAL_LINK_TEXT, "Products")

    #: The Kanban "Create" button on the Products list.
    #: ``@FindBy(className = "o-kanban-button-new")`` - ``InventoryP.java:20-21``.
    #: The class name is **hyphenated**, unlike Odoo's usual ``o_`` prefix and
    #: unlike :attr:`FIELD_ERROR` below; preserved exactly as the source writes
    #: it, per AAP 0.8.
    CREATE_BTN = (By.CLASS_NAME, "o-kanban-button-new")

    #: The product form's Save button, matched on its full class attribute.
    #: ``@FindBy(xpath = "//button[@class='btn btn-primary btn-sm o_form_button_save']")``
    #: - ``InventoryP.java:23-24``.  An exact-``@class`` match, so it is order-
    #: and whitespace-sensitive by construction; the expression is carried
    #: verbatim rather than relaxed to ``contains(@class, ...)``, which would
    #: match a different element set.
    SAVE_BTN = (By.XPATH, "//button[@class='btn btn-primary btn-sm o_form_button_save']")

    #: Odoo's notification container, which carries the validation error raised
    #: when the product form is saved with the name field left blank.
    #: ``@FindBy(className = "o_notification_manager")`` - ``InventoryP.java:26-27``.
    #: **Underscored**, in contrast to :attr:`CREATE_BTN`; both spellings are the
    #: source's and stay.
    FIELD_ERROR = (By.CLASS_NAME, "o_notification_manager")

    #: The product form's Name input.
    #: ``@FindBy(id = "o_field_input_479")`` - ``InventoryP.java:29-30``.
    #: A server-generated id: Odoo mints these per render, so this selector is
    #: brittle by nature.  Preserved exactly under AAP 0.8's *"Preserve, do not
    #: tidy"* - no label- or name-based replacement and no fallback locator,
    #: because either would change which element the step types "IBM" into
    #: (``Inventory.java:49``).
    PRODUCT_NAME = (By.ID, "o_field_input_479")

    #: The saved product's entry in the Products list, matched by its exact text.
    #: ``@FindBy(xpath = "//span[.='EY']")`` - ``InventoryP.java:32-33``.
    #: ``EY`` is test data baked into a selector, and it does not match the
    #: ``IBM`` the form step enters, so the "title includes the Product Name"
    #: step depends on a product already present in the instance.  Carried
    #: verbatim and deliberately not parameterized (AAP 0.8).
    PRODUCTS_LIST = (By.XPATH, "//span[.='EY']")

    #: The name field of the product record shown after a successful save.
    #: ``@FindBy(xpath = "//span[@class='o_field_char o_field_widget o_required_modifier']")``
    #: - ``InventoryP.java:35-36``.  Another exact-``@class`` match, kept as
    #: written for the same reason as :attr:`SAVE_BTN`.
    CREATED_PRODUCT = (
        By.XPATH,
        "//span[@class='o_field_char o_field_widget o_required_modifier']",
    )
