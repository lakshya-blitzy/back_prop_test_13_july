r"""Inventory page object - the port of ``InventoryP.java``.

The eight ``@FindBy`` fields of ``InventoryP.java:14-36``, at pinned revision
``47e9d697e4a9a85da889f94a846fdf47af28a240``, as locator constants in Java
declaration order - part of the contract, since
:attr:`~app.pages.base_page.BasePage.LOCATORS` is built from the class body in
source order and compared against that order.  The Java class declares no
method beyond its constructor, so this module declares constants only.

Two step modules import this page, which is unique in the package, because two
Java step classes instantiate ``InventoryP``: ``inventory_steps`` (the port of
``Inventory.java``, whose nine steps use all eight fields) and ``notes_steps``
(``Notes.java:16-17`` declares it beside ``NotesP``).  AAP 0.8 keeps that
split: no locator moves between this page and ``app/pages/notes_page.py``.

Three selectors are fragile or odd and are carried character for character
under AAP 0.8: :attr:`~InventoryPage.PRODUCT_NAME` is the server-generated id
``o_field_input_479`` Odoo mints per render, with no label-based replacement
and no fallback; :attr:`~InventoryPage.PRODUCTS_LIST` is ``//span[.='EY']``,
test data baked into a selector; and :attr:`~InventoryPage.CREATE_BTN` is
hyphenated where :attr:`~InventoryPage.FIELD_ERROR` is underscored.  The
abbreviated ``create_btn`` and ``save_btn`` are the source's spellings too.

No timeout appears here - both consumers construct a 20-second wait
(``Inventory.java:13``, ``Notes.java:19``) and AAP 0.4.1 keeps each at its call
site - and per AAP 0.4.2 the imports are :data:`~app.automation.By` and
:class:`~app.pages.base_page.BasePage` and nothing else, with no browser
library even under ``typing.TYPE_CHECKING``, no configuration and no driver
lifecycle (AAP 0.3.3).  Importing this module has no side effects.

``features/Inventory.feature`` carries **no tag at all** - none at feature
level and none on any of its four plain scenarios - so the default ``@Smoke``
filter (``CukesRunner.java:18``) does not select it.  What does reach it is a
tag expression that does not require ``@Smoke``, such as
``--tags="not @Smoke"``; the rerun manifest, which ``--rerun`` replays with no
tag filter at all; or naming the feature or one scenario location in a direct
engine invocation.  Its header is also mis-titled ``Feature: Testinium app
Inventory feature``, a title it shares with ``Contact.feature:1`` and which
therefore produces a duplicate JSON ``id``; AAP 0.2.2 preserves both.
"""

from app.automation import By
from app.pages.base_page import BasePage

__all__ = ["InventoryPage"]


class InventoryPage(BasePage):
    r"""Locators for the Odoo Inventory module: navigation, product form, results.

    The port of ``InventoryP.java``: eight locator constants, no ``__init__``,
    no hand-written property and no method, because the Java class has none
    either.  ``features/steps/inventory_steps.py`` drives the fields directly,
    so there is deliberately no ``create_product()`` and no navigation helper.

    :class:`~app.pages.base_page.BasePage` supplies the rest at class creation:
    ``InventoryPage.LOCATORS``, the eight constants as an immutable
    ``{name: (By.X, "value")}`` mapping in class-body order; one read-only
    accessor per constant, ``page.inventory_module`` through
    ``page.created_product``, re-resolving through ``find_element`` on every
    access and caching nothing; and the raw tuple,
    ``InventoryPage.CREATE_BTN`` and friends, for
    ``wait_visible(locator, timeout)``.  ``PLURAL_LOCATORS`` is not overridden:
    ``InventoryP.java`` declares no ``List<WebElement>`` field, so all eight
    accessors resolve to a single element.

    Usage, with the 20-second timeout its two step modules supply::

        inventory = InventoryPage()            # touches nothing at all
        inventory.inventory_module.click()     # one find_element, right now
        wait_visible(InventoryPage.PRODUCTS, 20)
        inventory.products.click()
    """

    #: ``InventoryP.java:14-15``'s ``inventoryModule``.
    INVENTORY_MODULE = (By.PARTIAL_LINK_TEXT, "Inventory")

    #: ``InventoryP.java:17-18``'s ``products``.
    PRODUCTS = (By.PARTIAL_LINK_TEXT, "Products")

    #: ``InventoryP.java:20-21``'s ``createBtn``, hyphenated as written.
    CREATE_BTN = (By.CLASS_NAME, "o-kanban-button-new")

    #: The product form's Save button, matched on its full class attribute.
    #: ``@FindBy(xpath = "//button[@class='btn btn-primary btn-sm o_form_button_save']")``
    #: - ``InventoryP.java:23-24``.  An exact-``@class`` match, so it is order-
    #: and whitespace-sensitive by construction; the expression is carried
    #: verbatim rather than relaxed to ``contains(@class, ...)``, which would
    #: match a different element set.
    SAVE_BTN = (By.XPATH, "//button[@class='btn btn-primary btn-sm o_form_button_save']")

    #: ``InventoryP.java:26-27``'s ``fieldError``, underscored as written.
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
