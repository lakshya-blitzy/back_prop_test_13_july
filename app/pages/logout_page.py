r"""Logout page object - the port of ``LogOutP.java``.

Anchor
------
``src/main/java/com/testinium/pages/LogOutP.java`` at pinned revision
``47e9d697e4a9a85da889f94a846fdf47af28a240``, held REFERENCE by AAP 0.2.1 and
never modified.  Twenty-two lines, three ``@FindBy`` fields at lines 14-21, and
**no method other than the constructor** - whose whole body is
``PageFactory.initElements(Driver.getDriver(), this)``, the call
:class:`~app.pages.base_page.BasePage` replaces for all ten pages:

.. code-block:: java

    @FindBy(className = "o_user_menu")                                  // :14
    public WebElement popUpButton;                                      // :15

    @FindBy(xpath = "//a[.='Log out']")                                 // :17
    public WebElement logOutButton;                                     // :18

    @FindBy(xpath = "//div[@class= 'o_dialog_warning modal-body']")     // :20
    public WebElement warningMess;                                      // :21

The two spellings below are deliberate and are not to be "regularized": the
class is ``LogOutPage`` with a capital ``O``, mirroring the Java class
``LogOutP``, while the module is ``logout_page.py`` because AAP 0.4.1 fixes
that filename.  ``features/steps/logout_steps.py``, ``app/pages/__init__.py``
and ``tests/test_pages.py`` are coded against both as written.
``warningMess`` likewise keeps its abbreviation as ``warning_mess`` and does
not become ``warning_message``.

The preserved whitespace quirk - do not "fix" it
------------------------------------------------
:attr:`~LogOutPage.WARNING_MESS` carries **a space between** ``@class=`` **and
the opening quote**: ``//div[@class= 'o_dialog_warning modal-body']``.  That is
what ``LogOutP.java:20`` says, character for character.  XPath treats
whitespace around the ``=`` of a predicate comparison as insignificant, so the
selector matches exactly what the tidied form would match and the quirk is
harmless at runtime - which is precisely why it survives here rather than being
silently corrected:

* AAP 0.2.2 permits correcting a source quirk only when it appears in the
  nineteen-item deviation inventory of AAP 0.1.3.  This one does not, and AAP
  0.8 states the standing instruction as *"Preserve, do not tidy"*.
* AAP 0.4.1's per-module parity obligation has ``tests/test_steps_logout.py``
  assert *"the same locators as declared in the paired page object"*, and
  ``tests/test_pages.py`` compares every declared selector against its Java
  original.  A normalized space fails both, and the failure would read as a
  test defect rather than as the edit that caused it.

So: copy the string, do not reflow the XPath, do not remove the space and do
not add a matching one elsewhere for symmetry.

How the three fields are consumed
---------------------------------
``LogOutSD.java`` reaches all three directly - there is nothing between the
step class and these fields, which is why this module needs no behaviour of its
own:

* ``wait.until(ExpectedConditions.visibilityOf(logOutP.popUpButton))``
  (``:18``) becomes ``wait_visible(LogOutPage.POP_UP_BUTTON, 3)``.
* ``logOutP.popUpButton.click()`` (``:19``) becomes
  ``page.pop_up_button.click()``.
* ``logOutP.logOutButton.click()`` (``:20``) becomes
  ``page.log_out_button.click()``.
* ``Assert.assertTrue(logOutP.warningMess.isDisplayed())`` (``:33``) becomes
  an ``assert`` on ``page.warning_mess.is_displayed()``.

Both access forms come from :class:`~app.pages.base_page.BasePage`: the
upper-case constant is the ``(By.X, "value")`` tuple an explicit wait takes,
and the lower-case accessor is the live element, re-resolved on every access
and never cached.  The **3-second** timeout of ``LogOutSD.java:13`` belongs at
the ``features/steps/logout_steps.py`` call sites, not here - AAP 0.4.1 fixes a
different timeout per step class and no page object may bake one in.  The
remaining step of that class, *"User should see the login dashboard"*, asserts
a page title through the driver and touches no field of this page at all
(``LogOutSD.java:25-27``).

Feature context
---------------
``features/Logout.feature`` carries the ``@LogOut`` tag at line 1 and holds two
``Scenario Outline`` blocks, cited by AAP 0.4.1 at lines 13 and 37 - their
``@UPGN-291`` and ``@UPGN-292`` tag lines, with the ``Scenario Outline:``
keywords immediately below at 14 and 38.  Each runs against a SalesManager and
a PosManager ``Examples`` block.  Neither ``@LogOut`` nor ``@UPGN-29x`` is
``@Smoke``, so the default tag filter of AAP 0.4.1 does not select this
feature; ``--tags=@LogOut`` does.

Import boundary (AAP 0.4.2)
---------------------------
*"Page objects import ``app.automation`` for the current driver, nothing
else."*  This module therefore imports exactly two names - :data:`By` from
``app.automation``, the single browser-library re-export that section
authorizes for ``app/pages/*``, and :class:`~app.pages.base_page.BasePage` -
and nothing further: no browser-library import of any kind, not even under
``typing.TYPE_CHECKING``, because a guarded import is still an import statement
and would trip the grep-based boundary check the suite performs; no
``app.config``, since step modules read configuration and page objects never
do; and no service, reporting writer, path helper, web framework or Gherkin
engine.  Nothing here creates, configures or quits a driver either - AAP 0.3.3
gives that to ``features/environment.py`` alone: *"no step or page ever creates
or quits a driver."*

Importing this module has no side effects whatever: it starts no browser,
resolves no driver binary, reads no configuration, touches no filesystem and
performs no element lookup, so the unit suite imports it on a machine with no
browser installed.

What this module deliberately does not contain
----------------------------------------------
* **No** ``log_out()`` **or any other method.**  ``LogOutP.java`` declares
  none, and ``EmployeeP.login()`` is the only behaviour method in the whole
  reference page package.  ``features/steps/logout_steps.py`` drives the three
  fields directly, exactly as ``LogOutSD.java`` does.
* **No** ``__init__`` **and no hand-written property.**
  :class:`~app.pages.base_page.BasePage` supplies the constructor - which
  stores its argument and does nothing else, so ``LogOutPage(stub_driver)``
  touches neither the DOM nor ``get_driver()`` - and installs the three lazy
  accessors from the constants below.
* **No** :attr:`~app.pages.base_page.BasePage.PLURAL_LOCATORS`.  None of these
  three fields is a Java ``List<WebElement>``; the reference declares exactly
  one such field in the entire suite, and it belongs to
  ``app/pages/sales_page.py``.
* **No fourth locator** - no confirmation-dialog, dialog-button or
  logged-out-banner helper.  The inventory is exactly the three fields the Java
  class declares.
* **No wait, no assertion, no branch.**  The module is declarative end to end,
  which is what keeps it fully covered by the ``--cov=app/pages
  --cov-fail-under=85`` gate of AAP 0.5.1.
"""

from app.automation import By
from app.pages.base_page import BasePage

__all__ = ["LogOutPage"]


class LogOutPage(BasePage):
    r"""Page object for the Odoo logout flow: user menu, log-out link, warning.

    The port of ``LogOutP.java`` (see the module docstring for the anchor).
    Three locator constants and nothing else; every accessor, the ``LOCATORS``
    inventory and the constructor come from
    :class:`~app.pages.base_page.BasePage`.

    ======================  ==================  =============================
    Constant                Accessor            Ports, in ``LogOutP.java``
    ======================  ==================  =============================
    :attr:`POP_UP_BUTTON`   ``pop_up_button``   ``popUpButton``, lines 14-15
    :attr:`LOG_OUT_BUTTON`  ``log_out_button``  ``logOutButton``, lines 17-18
    :attr:`WARNING_MESS`    ``warning_mess``    ``warningMess``, lines 20-21
    ======================  ==================  =============================

    Declaration order is the Java declaration order, because
    :attr:`~app.pages.base_page.BasePage.LOCATORS` is built from the class body
    in source order and ``tests/test_pages.py`` compares that order against the
    ``@FindBy`` order of the class this ports.  Reordering the three statements
    below would fail that comparison even though every selector still matched.

    Usage, both forms, neither of which needs a live browser to construct::

        page = LogOutPage()                              # or LogOutPage(stub)
        wait_visible(LogOutPage.POP_UP_BUTTON, 3)        # the locator tuple
        page.pop_up_button.click()                       # the live element
        page.log_out_button.click()
        assert page.warning_mess.is_displayed()
    """

    #: The account/user-menu control that opens the drop-down holding the
    #: log-out link.  Ports ``LogOutP.java:14-15``
    #: (``@FindBy(className = "o_user_menu")`` on ``popUpButton``) - Odoo's own
    #: navbar class name, taken as written.  ``LogOutSD.java:18-19`` waits for
    #: it to become visible on a 3-second timeout, then clicks it.
    POP_UP_BUTTON = (By.CLASS_NAME, "o_user_menu")

    #: The "Log out" entry inside that drop-down.  Ports
    #: ``LogOutP.java:17-18``.  The XPath matches an anchor by its exact
    #: normalized string value - ``.='Log out'``, not ``contains(...)`` and not
    #: ``text()`` - and is reproduced as written; ``LogOutSD.java:20`` clicks it
    #: immediately after the menu control, with no wait between the two.
    LOG_OUT_BUTTON = (By.XPATH, "//a[.='Log out']")

    #: The modal warning body Odoo renders when the browser's back button is
    #: used after logging out.  Ports ``LogOutP.java:20-21``, and
    #: ``LogOutSD.java:32-33`` navigates back and asserts it is displayed.
    #:
    #: **The space between** ``@class=`` **and the opening quote is in the
    #: source and is preserved byte-for-byte** - see the module docstring for
    #: why it must not be normalized away.  It is insignificant to XPath, so
    #: the selector behaves identically to the tidied form; it is kept because
    #: AAP 0.2.2 forbids correcting source quirks outside the AAP 0.1.3
    #: deviation inventory, and because ``tests/test_pages.py`` and
    #: ``tests/test_steps_logout.py`` compare this string against the Java
    #: original character for character.
    WARNING_MESS = (By.XPATH, "//div[@class= 'o_dialog_warning modal-body']")
