r"""Contacts page object - the port of ``ContactsP.java``.

The sixteen ``@FindBy`` fields of ``ContactsP.java:14-61``, at pinned revision
``47e9d697e4a9a85da889f94a846fdf47af28a240``, as locator constants in Java
declaration order - part of the contract, since
:attr:`~app.pages.base_page.BasePage.LOCATORS` is built from the class body in
source order and compared against that order.  The Java class declares no
method beyond its constructor, so this module declares constants only.  AAP
0.4.1 pairs it with ``features/Contact.feature`` and
``features/steps/contacts_steps.py``, whose ``Contacts.java`` original reads
all sixteen, so none is dead weight.

Source quirks carried byte-for-byte under AAP 0.2.2 and AAP 0.8's *"Preserve,
do not tidy"* - none is a mistake to correct here:

* Three selectors are **positional**, and the positions depend on the state of
  the Odoo instance under test: :attr:`~ContactsPage.NEW_CONTACT` is the 12th
  ``o_checkbox`` input, :attr:`~ContactsPage.ACTION_INPUT` the 2nd
  ``o_cp_sidebar`` div, :attr:`~ContactsPage.FIRST_USER` the 1st kanban record.
  Rewriting one as an attribute or text match would select a different element.
* :attr:`~ContactsPage.DUE_PAYMENT` keeps the redundant outer parentheses the
  annotation wrote - wrapped but not indexed, unlike the three above - and
  shares its base XPath with :attr:`~ContactsPage.PRINT_INPUT`, the dropdown div
  to its ``button`` child: two fields in the source, two constants here.
* The Java abbreviations survive the rename - ``okBtn``, ``editBtn`` and
  ``phoneNoInput`` become ``OK_BTN``, ``EDIT_BTN``, ``PHONE_NO_INPUT`` - as does
  the source's number disagreement: ``contactModule``, singular, in a plural
  class.

Per AAP 0.4.2 the imports are :data:`~app.automation.By` and
:class:`~app.pages.base_page.BasePage` and nothing else - no browser library
even under ``typing.TYPE_CHECKING``, no configuration, and no interaction helper
(``Contacts.java`` is the one step class importing neither ``Keys`` nor
``Actions``).  The 20-second wait (``Contacts.java:15``) and the five 3-second
delays (``:19``, ``:25``, ``:57``, ``:64``, ``:98``) stay at their step call
sites per AAP 0.4.1, and importing this module has no side effects: it never
creates or quits a driver (AAP 0.3.3).  ``features/Contact.feature`` is untagged
and its mis-titled ``Feature: Testinium app Inventory feature`` header is
preserved, so it shares a JSON ``id`` with ``Inventory.feature``.
"""

from app.automation import By
from app.pages.base_page import BasePage

__all__ = ["ContactsPage"]


class ContactsPage(BasePage):
    """The sixteen ``@FindBy`` fields of ``ContactsP.java``, in source order.

    Plural class name in module ``contacts_page.py``, tracking the Java
    ``ContactsP``, while the feature file it serves is the singular
    ``features/Contact.feature``; AAP 0.4.1 fixes both spellings, so neither
    is a typo to correct.

    The constants below are grouped by the flow that uses them, but their
    **order is the Java declaration order** (``ContactsP.java:14-61``) and
    must stay that way: ``__init_subclass__`` builds
    :attr:`~app.pages.base_page.BasePage.LOCATORS` from the class body in
    order, and that inventory is compared against the ``@FindBy`` order of the
    class this ports.  Each constant yields a lower-case accessor -
    ``page.contact_module`` through ``page.due_payment`` - that re-resolves its
    element on every access and caches nothing, so the two forms coexist:

    .. code-block:: python

        page = ContactsPage()                    # touches nothing at all
        page.contact_module.click()              # one find_element, right now
        wait_visible(ContactsPage.EDIT_TITLE, 20)

    ``PLURAL_LOCATORS`` is not overridden: ``ContactsP.java`` declares no
    ``List<WebElement>`` field, so all sixteen accessors are singular.
    """

    # ContactsP.java:14-15's `contactModule` - singular inside a plural class.
    CONTACT_MODULE = (By.PARTIAL_LINK_TEXT, "Contacts")

    # ContactsP.java:17-18's `createContact` and :20-21's `callList`.
    CREATE_CONTACT = (By.XPATH, "//button[@accesskey='c']")
    CALL_LIST = (By.XPATH, "//button[@accesskey='l']")

    # ContactsP.java:23-24, :26-27, :29-30 and :32-33 - `nameInput`,
    # `streetInput`, `phoneNoInput` (abbreviated as written) and `emailInput`.
    NAME_INPUT = (By.NAME, "name")
    STREET_INPUT = (By.NAME, "street")
    PHONE_NO_INPUT = (By.NAME, "phone")
    EMAIL_INPUT = (By.NAME, "email")

    # ContactsP.java:35-36's `okBtn`, abbreviated as written.
    OK_BTN = (By.XPATH, "//span[.='Ok']")

    # -- The list-view delete flow (ContactsP.java:38-45) ------------------
    #
    # POSITIONAL, and load-bearing: the 12th `o_checkbox` input in document
    # order, which is a row selector in the list view rather than a
    # particular contact. The index depends on the state of the Odoo instance
    # under test and is preserved exactly (AAP 0.8) - rewriting it as an
    # attribute or text match would select a different row on the same page.
    NEW_CONTACT = (By.XPATH, "(//div[@class='o_checkbox']/input)[12]")

    # ContactsP.java:41-42's `actionInput`. POSITIONAL: the 2nd div inside the
    # control panel's sidebar, which is the "Action" dropdown. Same rule as
    # above - the position is the locator, and it stays.
    ACTION_INPUT = (By.XPATH, "(//div[@class='o_cp_sidebar']/div/div)[2]")

    # ContactsP.java:44-45's `deleteInput`.
    DELETE_INPUT = (By.XPATH, "//a[@data-index='3']")

    # -- The edit flow (ContactsP.java:47-54) ------------------------------
    #
    # POSITIONAL: the 1st record of the kanban view, reached through the
    # view's full three-class attribute value. Both the class string and the
    # [1] index are preserved verbatim; the index is what makes "the profile"
    # in the Gherkin mean the first card rather than a named contact.
    #
    # The selector line below is the one line in this module past the 88-column
    # convention the package keeps, by a single character, and it stays that
    # way: the alternatives are to shorten the string, which breaks parity, or
    # to split it with implicit concatenation, which hides a character-exact
    # selector across two literals where a later edit could silently reshape
    # it. One long line is the safer of the three.
    FIRST_USER = (
        By.XPATH,
        "(//div[@class='o_kanban_view o_res_partner_kanban o_kanban_ungrouped']/div)[1]",
    )

    # ContactsP.java:50-51's `editTitle` and :53-54's `editBtn` (abbreviated as
    # written, with its full four-class attribute value).
    EDIT_TITLE = (By.XPATH, "//div[@class='oe_title']")
    EDIT_BTN = (
        By.XPATH,
        "//button[@class='btn btn-primary btn-sm o_form_button_edit']",
    )

    # -- The print / due-payments flow (ContactsP.java:56-61) --------------
    #
    # Two constants over the same base XPath, exactly as the source declares
    # them: PRINT_INPUT is the opened dropdown div, DUE_PAYMENT its button
    # child. Both are clicked, three seconds apart (Contacts.java:95-104), so
    # neither may be folded into the other.
    #
    # DUE_PAYMENT, `duePayment` at ContactsP.java:60-61, keeps its redundant
    # outer parentheses - wrapped but with no trailing index, unlike the three
    # positional selectors above. XPath-wise they are a no-op; they stay
    # because the parity test compares this string against the Java original
    # character for character.
    PRINT_INPUT = (By.XPATH, "//div[@class='btn-group o_dropdown open']")
    DUE_PAYMENT = (By.XPATH, "(//div[@class='btn-group o_dropdown open']/button)")
