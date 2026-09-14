r"""Inventory step definitions - the port of ``Inventory.java``.

Nothing here is asserted: four bodies compute a boolean and drop it, and their
failure modes differ.  ``Inventory.java:28`` compares the page title with no
element lookup, so it cannot raise ``NoSuchElementException``: it fails only
when the session cannot answer a title request, and any title passes.
``:44``, ``:54`` and ``:59`` do look an element up and discard its
``isDisplayed()``, so there an absent element raises and fails the step while
a present-but-hidden one passes.  AAP 0.2.2 keeps all four as they are.

``Contact.feature:1`` reuses this feature's own correct header title and is the
mis-titled member of the pair; that shared title is what gives the two a
duplicate JSON ``id``, which AAP 0.6 records and preserves.
"""

from behave import step

from app.automation import wait_visible_element
from app.pages import InventoryPage


def _page(context) -> InventoryPage:
    return InventoryPage(context.driver)


@step("Logged user clicks on Inventory Module")
def logged_user_clicks_on_inventory_module(context) -> None:
    _page(context).inventory_module.click()


@step("User clicks on Product module")
def user_clicks_on_product_module(context) -> None:
    page = _page(context)
    wait_visible_element(page.PRODUCTS, 20)
    page.products.click()


@step("User see the products")
def user_see_the_products(context) -> None:
    """Compare the page title with ``"Products - Odoo"``, then drop the answer.

    ``Inventory.java:28`` computes that boolean and ignores it, so this step
    passes whatever the title is; it fails only when the session cannot report
    a title.
    """
    # A pointless-looking comparison, and the point is that it is pointless:
    # the source computes it and drops it. Never turn this into a truth check,
    # a raise or a branch.
    context.driver.title == "Products - Odoo"


@step("User clicks create button")
def user_clicks_create_button(context) -> None:
    _page(context).create_btn.click()


@step("User clicks the save button")
def user_clicks_the_save_button(context) -> None:
    page = _page(context)
    wait_visible_element(page.SAVE_BTN, 20)
    page.save_btn.click()


@step("User should see the error")
def user_should_see_the_error(context) -> None:
    """Ask the notification container whether it is displayed, and drop that.

    ``Inventory.java:44`` discards the boolean, so the step passes when the
    container exists but is hidden and fails only when it is absent from the
    DOM once the implicit wait has elapsed.
    """
    _page(context).field_error.is_displayed()


@step("User enters Product Name")
def user_enters_product_name(context) -> None:
    _page(context).product_name.send_keys("IBM")


@step("User should see the title includes the Product Name")
def user_should_see_the_title_includes_the_product_name(context) -> None:
    """Ask the product-list entry whether it is displayed, and drop that.

    Despite the phrase, ``Inventory.java:54`` reads no title and compares no
    name: it looks up ``//span[.='EY']`` (``InventoryP.java:32``) - test data
    baked into a selector, not the ``"IBM"`` this module types - and discards
    the boolean, so a hidden span passes and only an absent one fails.  Both
    the phrase and the mismatch are preserved (AAP 0.8).
    """
    _page(context).products_list.is_displayed()


@step("User sees the created Product")
def user_sees_the_created_product(context) -> None:
    """Ask the saved record's name field whether it is displayed, and drop it.

    ``Inventory.java:59`` discards the boolean, so this closing step looks like
    a verification and verifies nothing: a hidden field passes and only an
    absent one fails.
    """
    _page(context).created_product.is_displayed()
