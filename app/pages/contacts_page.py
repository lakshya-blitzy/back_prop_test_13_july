r"""Contacts page object - the port of ``ContactsP.java``.

The Java anchor is
``src/main/java/com/testinium/pages/ContactsP.java`` at pinned revision
``47e9d697e4a9a85da889f94a846fdf47af28a240``, held REFERENCE by AAP 0.2.1 and
never modified.  That class is 65 lines: a constructor that calls
``PageFactory.initElements(Driver.getDriver(), this)``, sixteen ``@FindBy``
fields at lines 14-61, and **nothing else** - no method, no helper, no
constant.  This module is its whole content, so it is sixteen locator
constants and nothing else either.

AAP 0.4.1 pairs this module with ``features/Contact.feature`` and
``features/steps/contacts_steps.py``; AAP 0.1.1 goal G5 is why the
``PageFactory``/``@FindBy`` pair became locator constants - Python has no
``PageFactory`` - and :mod:`app.pages.base_page` carries the lazy-resolution
semantics for all ten pages.

The two access forms, both inherited
------------------------------------
Nothing below is a method; every accessor is installed by
:meth:`~app.pages.base_page.BasePage.__init_subclass__` when this ``class``
statement completes, and the two forms coexist because Python is
case-sensitive:

.. code-block:: python

    page = ContactsPage()                    # touches nothing at all
    page.contact_module.click()              # one find_element, right now
    ContactsPage.CONTACT_MODULE              # the (By.X, "value") tuple, for
                                             # wait_visible(locator, 20)

The element form re-resolves on **every** access, which is what the Java
proxy did; the tuple form is what a step passes to an explicit wait with the
timeout supplied at the call site.  ``ContactsPage.LOCATORS`` is the derived
``{constant name: locator}`` inventory in declaration order - derived, so it
is deliberately not written out here.

Source quirks preserved byte-for-byte (AAP 0.8, *"Preserve, do not tidy"*)
--------------------------------------------------------------------------
Three selectors are **positional**, and their hard-coded positions depend on
the state of the Odoo instance under test.  They are reproduced exactly and
must not be "improved" into attribute-based selectors, because a rewrite
would silently select a different element on the same page:

================  ==================================  ======================
Constant          Selects                             Java anchor
================  ==================================  ======================
``NEW_CONTACT``   the **12th** ``o_checkbox`` input    ``ContactsP.java:38``
``ACTION_INPUT``  the **2nd** ``o_cp_sidebar`` div     ``ContactsP.java:41``
``FIRST_USER``    the **1st** kanban record            ``ContactsP.java:47``
================  ==================================  ======================

Three further quirks, each intentional:

* :attr:`~ContactsPage.DUE_PAYMENT` keeps the redundant outer parentheses the
  Java annotation wrote, ``(//div[@class='btn-group o_dropdown open']/button)``
  - wrapped but *not* indexed, unlike the three positional selectors above.
  XPath-wise the parentheses are a no-op here; they stay because
  ``tests/test_pages.py`` compares the selector string against the Java
  original character for character.
* :attr:`~ContactsPage.PRINT_INPUT` and :attr:`~ContactsPage.DUE_PAYMENT`
  share the same base XPath, one selecting the dropdown div and the other its
  ``button`` child.  The source declares them as two fields
  (``ContactsP.java:56`` and ``:60``) and they stay two constants; folding
  either into the other would drop a locator the step module clicks.
* The Java abbreviations survive the rename: ``okBtn`` is ``OK_BTN``,
  ``editBtn`` is ``EDIT_BTN``, and ``phoneNoInput`` is ``PHONE_NO_INPUT`` -
  not ``PHONE_INPUT`` and not ``PHONE_NUMBER_INPUT``.  So does the source's
  own inconsistency: :attr:`~ContactsPage.CONTACT_MODULE` is **singular**
  while the class it belongs to is plural, exactly as ``contactModule`` sits
  in ``ContactsP``.

How the step module consumes this page
--------------------------------------
``Contacts.java`` is the step class, and two of its properties are stated
here only so that nobody moves them into this file:

* Its ``WebDriverWait`` is constructed with a **20-second** timeout
  (``Contacts.java:15``), the longest in the suite alongside ``Inventory``
  and ``Notes``.  Timeouts live at the call site, in
  ``features/steps/contacts_steps.py``, because the source fixes a different
  one per step class - never here, and never as a default.
* It contains **five** ``Thread.sleep(3000)`` calls (``Contacts.java:19``,
  ``:25``, ``:57``, ``:64``, ``:98``).  AAP 0.4.1 requires those to be
  reproduced as fixed delays at the same call sites and never converted into
  explicit waits, since converting them would change timing behaviour in a
  suite whose steps depend on Odoo's client-side rendering.

``Contacts.java`` is also the one step class that imports neither ``Keys``
nor ``Actions``, so ``contacts_steps.py`` imports nothing from
:mod:`app.automation.interactions` (AAP 0.4.1).  That changes nothing in this
file - a page object never imports those helpers - and nothing here should
suggest otherwise.

Feature context
---------------
``features/Contact.feature`` carries **no feature-level tag** and holds two
``Scenario Outline``\ s and two plain ``Scenario``\ s over a two-step
``Background``.  Its header reads ``Feature: Testinium app Inventory
feature`` - mis-titled in the source, which makes it share a JSON ``id`` slug
with ``Inventory.feature``.  AAP 0.2.2 preserves both the title and the
collision, and AAP 0.3.1 is why the HTTP report routes key on a feature's
index rather than on that ``id``.  Being untagged, the feature is not
selected by the default ``@Smoke`` filter; it is reachable by a negative
expression such as ``not @Smoke``.

Import boundary (AAP 0.4.2)
---------------------------
*"Page objects import ``app.automation`` for the current driver, nothing
else."*  So exactly two imports: :data:`~app.automation.By`, the single name
from the browser-automation library that package re-exports, and
:class:`~app.pages.base_page.BasePage`.  That library is never imported here
under any name, not even under ``typing.TYPE_CHECKING`` - a guarded import is
still an import statement and would trip the grep-based boundary check the
suite performs, which is why this module does not so much as spell the
distribution's name in prose (``app/pages/base_page.py`` does the same).
Nor is ``app.config`` (step modules read configuration, which is where the
Java classes read it), nor any service, reporting writer, path helper, web
framework or Gherkin engine.  This module never creates, quits or configures
a driver either: AAP 0.3.3 gives that lifecycle a single owner, and
``features/environment.py`` has already created the session before any step
runs.

Importing this module has no side effects whatever - it starts no browser,
provisions no driver binary, reads no ``configuration.properties`` and
touches no filesystem - so the unit suite imports it on a machine with no
browser installed.

What this module deliberately does not contain
----------------------------------------------
* **No ``create_contact(name, street, phone, email)`` helper**, however
  tempting the four ``By.NAME`` form fields make it.  ``ContactsP.java`` has
  no methods, and ``contacts_steps.py`` fills those fields step by step
  across three separate Gherkin steps (``Contacts.java:29-45``).  The name
  ``create_contact`` is already taken here by the *locator accessor* for the
  create button, which is the only thing it means.
* **No seventeenth locator**, no alias for an existing one, and no
  ``PLURAL_LOCATORS`` override - ``ContactsP.java`` declares no
  ``List<WebElement>`` field.
* **No ``__init__``, no property, no method, no branch.**  The module is
  declarative, which keeps it inside the ``--cov=app/pages
  --cov-fail-under=85`` gate (AAP 0.5.1) with nothing to cover but the class
  body.
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
    order, and ``tests/test_pages.py`` compares that inventory against the
    ``@FindBy`` order of the class this ports.

    ==  ====================  ==================  =====================
    #   Java field            Constant            Accessor
    ==  ====================  ==================  =====================
    1   ``contactModule``     ``CONTACT_MODULE``  ``contact_module``
    2   ``createContact``     ``CREATE_CONTACT``  ``create_contact``
    3   ``callList``          ``CALL_LIST``       ``call_list``
    4   ``nameInput``         ``NAME_INPUT``      ``name_input``
    5   ``streetInput``       ``STREET_INPUT``    ``street_input``
    6   ``phoneNoInput``      ``PHONE_NO_INPUT``  ``phone_no_input``
    7   ``emailInput``        ``EMAIL_INPUT``     ``email_input``
    8   ``okBtn``             ``OK_BTN``          ``ok_btn``
    9   ``newContact``        ``NEW_CONTACT``     ``new_contact``
    10  ``actionInput``       ``ACTION_INPUT``    ``action_input``
    11  ``deleteInput``       ``DELETE_INPUT``    ``delete_input``
    12  ``firstUser``         ``FIRST_USER``      ``first_user``
    13  ``editTitle``         ``EDIT_TITLE``      ``edit_title``
    14  ``editBtn``           ``EDIT_BTN``        ``edit_btn``
    15  ``printInput``        ``PRINT_INPUT``     ``print_input``
    16  ``duePayment``        ``DUE_PAYMENT``     ``due_payment``
    ==  ====================  ==================  =====================

    Every one of the sixteen is read by ``Contacts.java``, so none is dead
    weight and none may be dropped.
    """

    # -- Navigation into the module (ContactsP.java:14-21) -----------------
    #
    # Singular, while this class is plural - the source's own inconsistency,
    # preserved. Clicked by the Background's second step and again by both
    # "sees the ... details at dashboard" steps to return to the list.
    CONTACT_MODULE = (By.PARTIAL_LINK_TEXT, "Contacts")

    # Odoo's access keys, not labels: "c" is Create and "l" is the List view
    # toggle the delete flow starts from. Attribute selectors, so no position
    # is involved and none may be introduced.
    CREATE_CONTACT = (By.XPATH, "//button[@accesskey='c']")
    CALL_LIST = (By.XPATH, "//button[@accesskey='l']")

    # -- The new-contact form (ContactsP.java:23-36) -----------------------
    #
    # The only By.NAME cluster in this package, and the four fields the three
    # form-filling steps write to in order: name, then street, then phone and
    # email together (Contacts.java:29-45). PHONE_NO_INPUT keeps the Java
    # abbreviation of `phoneNoInput` - not PHONE_INPUT, not
    # PHONE_NUMBER_INPUT - even though the field it locates is named "phone".
    NAME_INPUT = (By.NAME, "name")
    STREET_INPUT = (By.NAME, "street")
    PHONE_NO_INPUT = (By.NAME, "phone")
    EMAIL_INPUT = (By.NAME, "email")

    # Text-content match on the exact label "Ok", capitalized as the source
    # writes it; `.` is the element's string value, so this matches the span
    # whose whole text is "Ok" and not one merely containing it. The Java
    # annotation is the one in the file written `xpath="..."` without spaces
    # around the `=` (ContactsP.java:35) - a formatting difference in Java
    # that carries no meaning into Python. OK_BTN keeps the `okBtn`
    # abbreviation.
    OK_BTN = (By.XPATH, "//span[.='Ok']")

    # -- The list-view delete flow (ContactsP.java:38-45) ------------------
    #
    # POSITIONAL, and load-bearing: the 12th `o_checkbox` input in document
    # order, which is a row selector in the list view rather than a
    # particular contact. The index depends on the state of the Odoo instance
    # under test and is preserved exactly (AAP 0.8) - rewriting it as an
    # attribute or text match would select a different row on the same page.
    NEW_CONTACT = (By.XPATH, "(//div[@class='o_checkbox']/input)[12]")

    # POSITIONAL: the 2nd div inside the control panel's sidebar, which is
    # the "Action" dropdown. Same rule as above - the position is the
    # locator, and it stays.
    ACTION_INPUT = (By.XPATH, "(//div[@class='o_cp_sidebar']/div/div)[2]")

    # The Delete entry of that dropdown, by its data-index. Read twice by the
    # step class: clicked to delete, then re-read with getText() and asserted
    # equal to "Deleted" (Contacts.java:61-77), so this one constant serves
    # both an action and an assertion.
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

    # The form's title block, waited on after opening a record, and the Edit
    # button of that form. EDIT_BTN keeps the `editBtn` abbreviation and the
    # full four-class attribute value, spaces included.
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
    # DUE_PAYMENT keeps its redundant outer parentheses - wrapped but with no
    # trailing index, unlike the three positional selectors above. XPath-wise
    # they are a no-op; they stay because the parity test compares this
    # string against the Java original character for character.
    PRINT_INPUT = (By.XPATH, "//div[@class='btn-group o_dropdown open']")
    DUE_PAYMENT = (By.XPATH, "(//div[@class='btn-group o_dropdown open']/button)")
