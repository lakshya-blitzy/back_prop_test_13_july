"""Page object for the shared login precondition: port of ``SessionP.java``.

The three ``@FindBy`` fields of ``SessionP.java:14-21``, at pinned revision
``47e9d697e4a9a85da889f94a846fdf47af28a240``, as locator constants.  That class
declares no method at all, so this module is purely declarative:
:class:`~app.pages.base_page.BasePage` supplies the constructor and one
accessor per constant, re-resolving its element on every access.

``By.ID`` here is deliberate and is not to be reconciled with
``app/pages/login_page.py``.  Three reference classes address the same Odoo
login form with two strategies - ``SessionP.java:14-18`` and
``EmployeeP.java:14-18`` by ``id``, ``LoginP.java:13-17`` by ``name`` - and the
port keeps each as written: AAP 0.2.2 holds that *"beyond the deviations 0.1.3
inventories, nothing is fixed"*, and on a form whose ``id`` and ``name`` differ
switching strategy would silently resolve a different element.

So there is no ``login()`` helper, although this page exists to support a login
precondition: ``SessionP.java`` declares none, and
``features/steps/session_steps.py`` drives the three fields directly as
``Session.java:14-17`` does - reading ``web.table.url``, ``username`` and
``password`` itself, because AAP 0.4.2 assigns configuration to the step
module.  There is no wait either: ``Session.java`` constructs no
``WebDriverWait`` at all, and the session's 10-second implicit wait
(``Driver.java:34``) is what retries a lookup.

Per AAP 0.4.2 the imports are :data:`~app.automation.By` and
:class:`~app.pages.base_page.BasePage` and nothing else - no browser library
even under ``typing.TYPE_CHECKING``, no configuration module, service, writer,
path helper, web framework or Gherkin engine - and importing this module has
no side effects.

``features/Session.feature`` is titled ``Feature: Default``, carries no tag and
holds one scenario, whose single step is the shared precondition other
features' backgrounds invoke - AAP 0.4.4's reason the port cannot be split
into phases.  That step is declared ``@When`` at ``Session.java:12`` yet used
as a ``Given`` elsewhere, which is why AAP 0.5.2 has every step register with
``@step``: ``session_steps``' concern, not this file's, but why these three
locators are reached by nearly every scenario in the suite.
"""

from app.automation import By
from app.pages.base_page import BasePage

__all__ = ["SessionPage"]


class SessionPage(BasePage):
    """The Odoo login form, as ``SessionP.java`` addresses it.

    A declarative subclass: the three locator constants
    ``SessionP.java:14-21`` declares as ``@FindBy`` fields, in that order, and
    no behaviour of its own.  :class:`~app.pages.base_page.BasePage` supplies
    one read-only accessor per constant, re-resolving the element on every
    access - the ``PageFactory`` proxy's semantics (``SessionP.java:10-12``).
    ``SessionPage.INPUT_LOGIN`` is the ``(By.ID, "login")`` tuple a wait or a
    parity test takes, and ``page.input_login`` the live element.

    Usage, as ``features/steps/session_steps.py`` reaches it - the port of
    ``Session.java:13-18``, whose three configuration values it reads itself:

    .. code-block:: python

        page = SessionPage()                 # touches nothing at all
        page.input_login.send_keys(user)     # one find_element, right now
        page.input_pass.send_keys(password)
        page.login_button.click()

    Constructing it is free of side effects: ``BasePage.__init__`` stores its
    optional ``driver`` argument and does nothing else, so
    ``SessionPage(stub_driver)`` - the injection seam the parity tests use -
    touches no DOM and calls no :func:`~app.automation.driver.get_driver`.
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

    #: ``SessionP.java:20-21``'s ``loginButton``, the ``LoginP.java:19`` XPath.
    LOGIN_BUTTON = (By.XPATH, "//button[.='Log in']")
