"""Page object for the shared login precondition: port of ``SessionP.java``.

The Python counterpart of
``src/main/java/com/testinium/pages/SessionP.java`` at pinned revision
``47e9d697e4a9a85da889f94a846fdf47af28a240``, held REFERENCE by AAP 0.2.1 and
never modified.  That class is 23 lines long: a constructor calling
``PageFactory.initElements(Driver.getDriver(), this)`` and three ``@FindBy``
fields at lines 14-21.  It declares **no methods at all**, so this module is
purely declarative - three locator constants and nothing else, with every
accessor supplied by :class:`~app.pages.base_page.BasePage`.

The Java anchor, verbatim (``SessionP.java:14-21``)
--------------------------------------------------
.. code-block:: java

    @FindBy(id = "login")
    public WebElement inputLogin;

    @FindBy(id = "password")
    public WebElement inputPass;

    @FindBy(xpath = "//button[.='Log in']")
    public WebElement loginButton;

Each field becomes one constant here, in the same declaration order, so
:attr:`SessionPage.LOCATORS` enumerates as ``INPUT_LOGIN``, ``INPUT_PASS``,
``LOGIN_BUTTON`` - the order ``tests/test_pages.py`` compares against the
``@FindBy`` order of the Java class.

``By.ID`` here is deliberate, and is not to be reconciled with ``LoginPage``
--------------------------------------------------------------------------
Three page objects address the same Odoo login form with two different
strategies, and the port keeps all three exactly as written:

===========================  ===============================  ==============
Reference class              Login and password fields        Strategy
===========================  ===============================  ==============
``SessionP.java:14-18``      ``inputLogin``, ``inputPass``    ``id``
``EmployeeP.java:14-18``     ``inputLogin``, ``inputPass``    ``id``
``LoginP.java:13-17``        ``inputEmail``, ``inputPassword``  ``name``
===========================  ===============================  ==============

So this module declares ``(By.ID, "login")`` while
``app/pages/login_page.py`` declares ``(By.NAME, "login")``, and that is the
intended end state rather than an oversight to tidy up later.  Two reasons,
both binding:

* AAP 0.2.2 keeps the source's inconsistencies outside the nineteen
  deviations AAP 0.1.3 inventories - *"Beyond the deviations 0.1.3
  inventories, nothing is fixed"* - and AAP 0.8 restates it as *"Preserve, do
  not tidy"*.  A locator-strategy change is not among those nineteen.
* AAP 0.4.1's per-module parity obligation has ``tests/test_steps_session.py``
  assert *"the same locators as declared in the paired page object"*.
  Switching to ``By.NAME`` for symmetry with ``LoginPage`` would fail that
  assertion, and on a form where the ``id`` and ``name`` attributes of an
  input differ it would silently resolve a different element - the class of
  change this port exists to avoid.

What this module deliberately does not contain
----------------------------------------------
Every omission mirrors something ``SessionP.java`` does not declare:

* **No** ``login()`` **helper**, despite this page existing to support a login
  precondition.  ``SessionP.java`` declares no method; the only behaviour
  method in the whole reference page package is ``EmployeeP.login()``
  (``EmployeeP.java:59-69``), which belongs to ``app/pages/employee_page.py``.
  ``features/steps/session_steps.py`` drives the three fields directly, as
  ``Session.java:14-17`` does.
* **No URL constant and no configuration access.**  ``Session.java:14-17``
  reads ``web.table.url``, ``username`` and ``password`` through
  ``ConfigurationReader``, but AAP 0.4.2 assigns configuration to the step
  module - it lists ``session_steps`` among the consumers of the
  configuration module and holds that *"Page objects import
  ``app.automation`` for the current driver, nothing else."*  So nothing here
  imports that module, and no credential or URL literal appears below.
* **No wait and no timeout.**  ``Session.java`` constructs no
  ``WebDriverWait`` at all - it is the one step class in the suite that does
  not - so no timeout belongs anywhere near this page.  The session's
  10-second implicit wait (``Driver.java:34``) is what makes a lookup retry.
* **No** ``__init__``\\ **, no properties, no methods, no extra locators.**
  Exactly three constants; the accessors, the inventory and the driver seam
  all arrive by inheritance.

Import boundary (AAP 0.4.2)
---------------------------
Two imports, both from the whitelist: :data:`~app.automation.By` - the single
authorized re-export of the browser-automation library's locator strategies,
so that library is never imported here, not even under
``typing.TYPE_CHECKING`` - and :class:`~app.pages.base_page.BasePage`.
Nothing else: no configuration module, no service, no reporting writer, no
path helper, no web framework and no Gherkin engine.  A grep of this file for
either the browser library's package name or the configuration module's dotted
path is expected to come back empty, and does - which is why both are named in
prose here by role rather than spelled out.  This module never creates,
configures or quits a driver either (AAP 0.3.3 reserves that to
``features/environment.py``), and importing it has no side effects whatever -
no browser, no driver binary, no ``configuration.properties`` read - which is
what lets the unit suite import it on a machine with no browser installed.

Why this small page is on the critical path
-------------------------------------------
``features/Session.feature`` is titled ``Feature: Default``, carries no tag
and holds one scenario, whose single step - ``When User login to test other
features`` - is the shared precondition other features' backgrounds invoke.
AAP 0.4.4 cites exactly that as the reason the port cannot be split into
phases: *"the shared precondition step in ``Session.java`` is invoked by other
features' backgrounds, so porting any one feature area alone yields undefined
steps."*  The step is declared ``@When`` at ``Session.java:12`` yet used as a
``Given`` elsewhere, which is the primary evidence behind AAP 0.5.2's ruling
that every step registers with ``@step``; that is
``features/steps/session_steps.py``'s concern rather than this file's, but it
is why the three locators below are reached by nearly every scenario in the
suite.
"""

from app.automation import By
from app.pages.base_page import BasePage

__all__ = ["SessionPage"]


class SessionPage(BasePage):
    """The Odoo login form, as ``SessionP.java`` addresses it.

    A declarative subclass: it declares the three locator constants that
    ``SessionP.java:14-21`` declares as ``@FindBy`` fields and adds no
    behaviour of its own.  :class:`~app.pages.base_page.BasePage` supplies, for
    each constant, a read-only accessor under the lower-case name that
    re-resolves the element on every access - the semantics of the
    ``PageFactory`` proxy this replaces (``SessionP.java:10-12``).

    ==========================  =========================================
    Access form                 Yields
    ==========================  =========================================
    ``SessionPage.INPUT_LOGIN``  the ``(By.ID, "login")`` **tuple**, for
                                ``wait_visible(locator, timeout)`` and the
                                per-module parity tests
    ``page.input_login``        the live ``WebElement``, re-resolved per
                                access, for ``.send_keys()`` and
                                ``.click()``
    ==========================  =========================================

    Usage, as ``features/steps/session_steps.py`` reaches it - the port of
    ``Session.java:13-18``, with the three configuration values read by the
    step module and never by this class:

    .. code-block:: python

        page = SessionPage()                 # touches nothing at all
        page.input_login.send_keys(user)     # one find_element, right now
        page.input_pass.send_keys(password)
        page.login_button.click()

    Constructing a page object is free of side effects and safe before
    navigation: ``BasePage.__init__`` stores its optional ``driver`` argument
    and does nothing else, so ``SessionPage(stub_driver)`` - the injection seam
    the parity tests use - never touches the DOM and never calls
    :func:`~app.automation.driver.get_driver`.
    """

    #: The login input, ``SessionP.java:14-15``'s ``inputLogin``.
    #:
    #: ``By.ID`` is what the Java says and what stays:
    #: ``app/pages/login_page.py`` locates this same field by ``name``
    #: (``LoginP.java:13``), and the divergence is preserved rather than
    #: reconciled - see the module docstring for the two reasons.
    INPUT_LOGIN = (By.ID, "login")

    #: The password input, ``SessionP.java:17-18``'s ``inputPass``.  ``By.ID``
    #: here against ``LoginP.java:16``'s ``name``, preserved for the same
    #: reason as :attr:`INPUT_LOGIN`.
    INPUT_PASS = (By.ID, "password")

    #: The submit button, ``SessionP.java:20-21``'s ``loginButton``.  The XPath
    #: is character-identical to ``LoginP.java:19``'s and
    #: ``EmployeeP.java:20``'s, including the single quotes around
    #: ``Log in``; all three reference the same button.
    LOGIN_BUTTON = (By.XPATH, "//button[.='Log in']")
