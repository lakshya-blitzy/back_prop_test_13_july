r"""Login page object - the port of ``LoginP.java``.

The locator inventory of the login screen: the seven ``@FindBy`` fields
declared at ``LoginP.java:13-32`` in the reference implementation, at pinned
revision ``47e9d697e4a9a85da889f94a846fdf47af28a240`` - held REFERENCE by AAP
0.2.1 and never modified - restated as the locator constants that AAP 0.1.1
goal G5 substitutes for Java's ``PageFactory``/``@FindBy`` pair, which has no
Python equivalent.

That Java class is 33 lines long and its only member beyond the seven fields
is the constructor, whose whole body is
``PageFactory.initElements(Driver.getDriver(), this)`` (``LoginP.java:9-11``).
So this module declares constants and nothing else:
:class:`~app.pages.base_page.BasePage` carries that constructor's
lazy-resolution semantics for all ten page objects and installs, for each
constant below, a read-only accessor under the lower-case name that re-runs
``find_element`` on **every** access.  Two access forms therefore exist and
both are used:

* ``LoginPage.DASHBOARD`` - the ``(By.X, "value")`` tuple, for
  ``wait_visible(locator, timeout)`` and for the parity tests.
* ``page.dashboard`` - the live element, for ``.click()``, ``.send_keys()``,
  ``.is_displayed()`` and ``.get_attribute()``.

What consumes each constant
---------------------------
``features/steps/login_steps.py`` is the port of ``LoginSD.java`` and the only
consumer.  The Java line numbers are the read sites, and the parity obligation
in AAP 0.4.1 requires ``tests/test_steps_login.py`` to assert *"the same
locators as declared in the paired page object"* for every step method there,
so a drifted selector below fails that test rather than merely misbehaving in
a browser:

========================  ==================================================
Constant                  Read at
========================  ==================================================
:attr:`~LoginPage.INPUT_EMAIL`  ``LoginSD.java:28`` - the ``<username>``
                          Examples value is typed into it
:attr:`~LoginPage.INPUT_PASSWORD`  ``LoginSD.java:33`` - the ``<password>``
                          Examples value is typed into it
:attr:`~LoginPage.BUTTON`  ``LoginSD.java:38`` and ``:67`` - clicked by both
                          the login-button step and the ``@UPGN-290``
                          Enter-key step
:attr:`~LoginPage.RESET_PASS`  nowhere - declared at ``LoginP.java:22-23``
                          and read by no step method
:attr:`~LoginPage.DASHBOARD`  ``LoginSD.java:43`` - the target of the only
                          explicit wait in the class, before the page title
                          is asserted equal to ``"Odoo"`` (``:44-46``)
:attr:`~LoginPage.ALERT_ERROR_MESSAGE`  ``LoginSD.java:51`` -
                          ``isDisplayed()`` for the ``@UPGN-287`` assertion
:attr:`~LoginPage.BULLET_PASS`  ``LoginSD.java:62`` -
                          ``getAttribute("type")`` for the ``@UPGN-289``
                          masking assertion
========================  ==================================================

``RESET_PASS`` having no reader is source state, not an oversight here:
``LoginP.java`` declares seven fields, this module declares the same seven,
and AAP 0.8's *"Preserve, do not tidy"* keeps the unused one.

Two source quirks are preserved deliberately
--------------------------------------------
* :attr:`~LoginPage.INPUT_PASSWORD` and :attr:`~LoginPage.BULLET_PASS` are the
  same locator under two names, exactly as ``LoginP.java:16-17`` and ``:31-32``
  declare them.  They are kept independent - not collapsed, not aliased - so
  both accessors resolve on their own, which is what lets
  ``login_steps.py`` reach ``bullet_pass`` specifically for the
  password-masking assertion.  AAP 0.2.2 puts *"correcting the source's other
  inconsistencies"* out of scope.
* :attr:`~LoginPage.DASHBOARD` is the Odoo element id ``oe_main_menu_navbar``
  and stays that way.  AAP Conflict 8 records that the specification says
  Testinium, the feature titles say *"Testinium app login feature"*, the
  pipeline's original checkout URL said Upgenix, and the implementation
  asserts Odoo - and resolves it with *"no renaming. All three vocabularies
  are preserved exactly where they occur."*

Import boundary (AAP 0.4.2)
---------------------------
*"Page objects import ``app.automation`` for the current driver, nothing
else"*, and that package is the only one in the port permitted to import the
browser-automation library at all.  This module therefore imports exactly two
names - :data:`~app.automation.By`, which that package re-exports precisely so
locator construction needs no browser-library import elsewhere, and
:class:`~app.pages.base_page.BasePage` - and nothing further: no
browser-library import of any kind, not even under ``typing.TYPE_CHECKING``,
since a guarded import is still an import statement and would trip the
grep-based boundary check the suite performs; no ``app.config``, because page
objects never read configuration - ``login_steps.py`` reads ``web.table.url``
itself, as ``LoginSD.java:22`` does; and no service, no reporting writer, no
path helper, no web framework and no Gherkin engine.

What this module deliberately does not contain
----------------------------------------------
* **No behaviour method**, and in particular no ``login()`` convenience.  The
  only behaviour method in the whole reference page package is
  ``EmployeeP.login()`` (``EmployeeP.java:59-69``), which belongs to
  ``app/pages/employee_page.py``; ``LoginP.java`` has none and the step module
  drives these fields directly.
* **No page URL and no navigation helper.**  The login URL is the
  ``web.table.url`` configuration key, read by the step module
  (``LoginSD.java:22``), not a page-object concern.
* **No wait and no assertion.**  ``LoginSD.java:17`` builds its
  ``WebDriverWait`` with a 3-second timeout, and that timeout belongs at the
  ``login_steps.py`` call sites, because the source fixes a different timeout
  per step class and no page-level default may paper over the difference.
* **No driver creation, configuration or disposal.**  AAP 0.3.3: *"no step or
  page ever creates or quits a driver."*
* **No ``PLURAL_LOCATORS``.**  None of these fields is a Java
  ``List<WebElement>``; the reference declares exactly one such field
  (``SalesP.java:69``), reached from ``app/pages/sales_page.py``.

Importing this module has no side effects: it starts no browser, reads no
``configuration.properties``, touches no filesystem and locates no element,
which is what lets the unit suite import it on a machine with no browser.

Feature context
---------------
``features/Login.feature`` - tagged ``@Login``, titled *"Testinium app login
feature"*, with the background *"Given User is on the upgenix login page"* -
drives five outlines against this screen, each with a SalesManager and a
PosManager ``Examples`` block: ``@UPGN-286`` valid login, ``@UPGN-287``
invalid credentials, ``@UPGN-288`` empty field (whose expected text is the
browser's own French required-field message, read off
:attr:`~LoginPage.INPUT_EMAIL` through a locator the step builds inline at
``LoginSD.java:56``), ``@UPGN-289`` password bullets and ``@UPGN-290`` the
Enter key.
"""

from app.automation import By
from app.pages.base_page import BasePage

__all__ = ["LoginPage"]


class LoginPage(BasePage):
    r"""The login screen's seven locators, in ``LoginP.java`` declaration order.

    A pure declaration: the class body is the seven constants below and
    nothing else, mirroring ``LoginP.java:13-32`` field for field and in the
    same order, because :meth:`BasePage.__init_subclass__
    <app.pages.base_page.BasePage.__init_subclass__>` builds
    :attr:`~app.pages.base_page.BasePage.LOCATORS` in class-body order and
    ``tests/test_pages.py`` compares that order against the Java class's
    ``@FindBy`` order.

    Inherited from :class:`~app.pages.base_page.BasePage`, so not restated
    here: the ``driver=None`` constructor - ``LoginPage()`` takes this
    worker's live session per lookup, ``LoginPage(stub)`` or
    ``LoginPage(driver=stub)`` injects one, and either way construction
    touches no DOM and requests no session; the seven read-only accessors
    ``input_email``, ``input_password``, ``button``, ``reset_pass``,
    ``dashboard``, ``alert_error_message`` and ``bullet_pass``, each
    re-resolving its element on every access and caching nothing; and the
    ``find``/``find_all`` funnel those accessors go through.

    .. code-block:: python

        page = LoginPage()                       # touches nothing at all
        page.input_email.send_keys(username)     # LoginSD.java:28
        page.button.click()                      # LoginSD.java:38
        wait_visible(LoginPage.DASHBOARD, 3)     # LoginSD.java:17 + :43
        page.bullet_pass.get_attribute("type")   # LoginSD.java:62
    """

    #: The username field: ``@FindBy(name = "login")``
    #: (``LoginP.java:13-14``).  Named for the Java field ``inputEmail``
    #: though the underlying control is ``name="login"``; the Examples
    #: columns are headed ``<username>``.  Accessor ``input_email``, typed
    #: into at ``LoginSD.java:28``.  ``LoginSD.java:56`` reaches the same
    #: control a second way, building ``By.name("login")`` inline to read its
    #: ``validationMessage`` attribute for ``@UPGN-288``, which is why
    #: ``login_steps.py`` is the one step module that imports ``By``.
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

    #: The post-login landing marker: ``@FindBy(id = "oe_main_menu_navbar")``
    #: (``LoginP.java:25-26``).  Accessor ``dashboard``, and the target of the
    #: 3-second explicit wait at ``LoginSD.java:43`` that precedes the
    #: ``"Odoo"`` page-title assertion (``:44-46``).  The id is Odoo's own
    #: top menu bar and is preserved exactly, per AAP Conflict 8's *"no
    #: renaming"*.
    DASHBOARD = (By.ID, "oe_main_menu_navbar")

    #: The failed-login banner: ``@FindBy(className = "alert")``
    #: (``LoginP.java:28-29``).  Accessor ``alert_error_message``, asserted
    #: displayed for ``@UPGN-287`` at ``LoginSD.java:51``.
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
