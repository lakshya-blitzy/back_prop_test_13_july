r"""Login page object - the port of ``LoginP.java``.

The seven ``@FindBy`` fields of ``LoginP.java:13-32``, at pinned revision
``47e9d697e4a9a85da889f94a846fdf47af28a240``, restated as the locator constants
AAP 0.1.1 goal G5 substitutes for Java's ``PageFactory``/``@FindBy`` pair.  The
Java class holds nothing but those fields and a constructor calling
``PageFactory.initElements`` (``LoginP.java:9-11``), so this module declares
constants only: :class:`~app.pages.base_page.BasePage` installs one accessor
per constant.  ``LoginPage.DASHBOARD`` is the tuple a
``wait_visible(locator, timeout)`` call takes, ``page.dashboard`` the element.

``features/steps/login_steps.py``, the port of ``LoginSD.java``, is the only
consumer, and :attr:`~LoginPage.RESET_PASS` has no reader there - source state
that AAP 0.8's *"Preserve, do not tidy"* keeps rather than an oversight here.

Two source quirks are preserved deliberately:

* :attr:`~LoginPage.INPUT_PASSWORD` and :attr:`~LoginPage.BULLET_PASS` are the
  same ``name="password"`` locator declared twice (``LoginP.java:16-17``,
  ``:31-32``), kept independent so ``login_steps.py`` can reach
  ``bullet_pass`` for the masking assertion; AAP 0.2.2 puts *"correcting the
  source's other inconsistencies"* out of scope.
* :attr:`~LoginPage.DASHBOARD` is Odoo's own element id
  ``oe_main_menu_navbar``, kept under AAP Conflict 8's *"no renaming"*, which
  preserves the Testinium, Upgenix and Odoo vocabularies where each occurs.

Per AAP 0.4.2 this module imports :data:`~app.automation.By` and
:class:`~app.pages.base_page.BasePage` and nothing else - no browser library
even under ``typing.TYPE_CHECKING``, and no configuration, since
``login_steps.py`` reads ``web.table.url`` itself (``LoginSD.java:22``).  It
holds no behaviour method and no wait: ``LoginSD.java:17``'s 3-second timeout
belongs at the step call sites.  Importing it has no side effects.

``features/Login.feature`` (tagged ``@Login``) drives five outlines here -
``@UPGN-286`` valid login, ``@UPGN-287`` invalid credentials, ``@UPGN-288`` the
browser's French required-field message, read through a locator
``LoginSD.java:56`` builds inline, ``@UPGN-289`` password bullets and
``@UPGN-290`` the Enter key - each over SalesManager and PosManager Examples.
"""

from app.automation import By
from app.pages.base_page import BasePage

__all__ = ["LoginPage"]


class LoginPage(BasePage):
    r"""The login screen's seven locators, in ``LoginP.java`` declaration order.

    A pure declaration: the class body is the seven constants and nothing
    else, mirroring ``LoginP.java:13-32`` field for field and in the same
    order, because :attr:`~app.pages.base_page.BasePage.LOCATORS` is built in
    class-body order and compared against that class's ``@FindBy`` order.

    Everything else is inherited from :class:`~app.pages.base_page.BasePage`:
    the ``driver=None`` constructor - ``LoginPage()`` takes this worker's live
    session per lookup, ``LoginPage(stub)`` injects one, and either way
    construction touches no DOM - and the seven read-only accessors
    ``input_email``, ``input_password``, ``button``, ``reset_pass``,
    ``dashboard``, ``alert_error_message`` and ``bullet_pass``, each
    re-resolving its element on every access and caching nothing.

    .. code-block:: python

        page = LoginPage()                       # touches nothing at all
        page.input_email.send_keys(username)     # LoginSD.java:28
        page.button.click()                      # LoginSD.java:38
        wait_visible(LoginPage.DASHBOARD, 3)     # LoginSD.java:17 + :43
        page.bullet_pass.get_attribute("type")   # LoginSD.java:62
    """

    #: ``LoginP.java:13-14``'s ``inputEmail``, whose control is ``name="login"``.
    INPUT_EMAIL = (By.NAME, "login")

    #: The password field: ``@FindBy(name="password")``
    #: (``LoginP.java:16-17``).  Accessor ``input_password``, typed into at
    #: ``LoginSD.java:33``.  :attr:`BULLET_PASS` below is a second, separately
    #: declared name for this same locator; both are kept, per AAP 0.2.2.
    INPUT_PASSWORD = (By.NAME, "password")

    #: The submit control: ``@FindBy(xpath = "//button[.='Log in']")``
    #: (``LoginP.java:19-20``).  Matches on the button's exact text, so the
    #: selector is locale-sensitive and is carried over verbatim rather than
    #: rewritten.  Accessor ``button``, clicked by the login-button step
    #: (``LoginSD.java:38``) and again by the ``@UPGN-290`` Enter-key step
    #: (``:67``), which the source implements as a second click.
    BUTTON = (By.XPATH, "//button[.='Log in']")

    #: The password-reset link:
    #: ``@FindBy(xpath = "//a[.='Reset Password']")``
    #: (``LoginP.java:22-23``).  Accessor ``reset_pass``.  Declared by the
    #: Java class and read by none of its step methods; retained so this
    #: inventory is the same seven fields the source declares.
    RESET_PASS = (By.XPATH, "//a[.='Reset Password']")

    #: ``LoginP.java:25-26``'s ``dashboard``, an Odoo-owned element id.
    DASHBOARD = (By.ID, "oe_main_menu_navbar")

    #: ``LoginP.java:28-29``'s ``alertErrorMessage``.
    ALERT_ERROR_MESSAGE = (By.CLASS_NAME, "alert")

    #: The password field again, under the name the masking check uses:
    #: ``@FindBy(name="password")`` (``LoginP.java:31-32``), the same locator
    #: as :attr:`INPUT_PASSWORD` and declared separately in the source.
    #: Accessor ``bullet_pass``, whose ``get_attribute("type")`` is asserted
    #: equal to ``"password"`` for ``@UPGN-289`` at ``LoginSD.java:62``.
    #: Both names resolve independently and neither delegates to the other,
    #: which is the source's shape and what ``login_steps.py`` is written
    #: against.
    BULLET_PASS = (By.NAME, "password")
