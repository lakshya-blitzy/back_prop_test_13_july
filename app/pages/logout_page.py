r"""Logout page object - the port of ``LogOutP.java``.

The three ``@FindBy`` fields of ``LogOutP.java:14-21``, at pinned revision
``47e9d697e4a9a85da889f94a846fdf47af28a240``, as locator constants.  The Java
class declares no method beyond a constructor calling
``PageFactory.initElements``, so this module declares constants only:
:class:`~app.pages.base_page.BasePage` supplies the constructor and the three
accessors, each re-resolving its element on every access.

Three spellings are deliberate and must not be regularized: the class keeps
the capital ``O`` of Java's ``LogOutP`` while AAP 0.4.1 fixes its module name
as ``logout_page``; ``warningMess`` stays abbreviated as ``warning_mess``; and
:attr:`~LogOutPage.WARNING_MESS` carries the space between ``@class=`` and the
opening quote that ``LogOutP.java:20`` writes.  XPath treats that whitespace
as insignificant, so the selector matches what a tidied one would - which is
why it survives: AAP 0.2.2 permits correcting a source quirk only inside the
AAP 0.1.3 deviation inventory, and this one is not in it.

``features/steps/logout_steps.py``, the port of ``LogOutSD.java``, drives all
three fields directly - the menu control waited on then clicked (``:18-19``),
the log-out link clicked (``:20``), the warning body asserted displayed
(``:33``) - so this module needs no behaviour of its own, and the 3-second
timeout of ``LogOutSD.java:13`` belongs at those call sites, not here.

Per AAP 0.4.2 it imports :data:`~app.automation.By` and
:class:`~app.pages.base_page.BasePage` and nothing else - no browser library
even under ``typing.TYPE_CHECKING``, no configuration, service, writer, path
helper, web framework or Gherkin engine.  It creates and quits no driver, which
AAP 0.3.3 reserves to ``features/environment.py``, and importing it has no
side effects.

``features/Logout.feature`` carries ``@LogOut`` at line 1 and holds the
``@UPGN-291`` and ``@UPGN-292`` outlines (tag lines 13 and 37), each over a
SalesManager and a PosManager ``Examples`` block.  Neither tag is ``@Smoke``,
so the default filter does not select this feature; ``--tags=@LogOut`` does.
"""

from app.automation import By
from app.pages.base_page import BasePage

__all__ = ["LogOutPage"]


class LogOutPage(BasePage):
    r"""Page object for the Odoo logout flow: user menu, log-out link, warning.

    The port of ``LogOutP.java`` (the module docstring holds the anchor).
    Three locator constants and nothing else; the accessors, the ``LOCATORS``
    inventory and the constructor come from
    :class:`~app.pages.base_page.BasePage`.

    ======================  ==================  =============================
    Constant                Accessor            Ports, in ``LogOutP.java``
    ======================  ==================  =============================
    :attr:`POP_UP_BUTTON`   ``pop_up_button``   ``popUpButton``, lines 14-15
    :attr:`LOG_OUT_BUTTON`  ``log_out_button``  ``logOutButton``, lines 17-18
    :attr:`WARNING_MESS`    ``warning_mess``    ``warningMess``, lines 20-21
    ======================  ==================  =============================

    The three statements below are in Java declaration order, which
    :attr:`~app.pages.base_page.BasePage.LOCATORS` preserves and the parity
    tests compare against the ``@FindBy`` order.

    Usage, neither form needing a live browser to construct::

        page = LogOutPage()                              # or LogOutPage(stub)
        wait_visible(LogOutPage.POP_UP_BUTTON, 3)        # the locator tuple
        page.pop_up_button.click()                       # the live element
    """

    POP_UP_BUTTON = (By.CLASS_NAME, "o_user_menu")

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
