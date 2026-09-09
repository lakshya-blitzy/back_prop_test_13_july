r"""Employees-module page object - the port of ``EmployeeP.java``.

The Python counterpart of
``src/main/java/com/testinium/pages/EmployeeP.java`` at pinned revision
``47e9d697e4a9a85da889f94a846fdf47af28a240``, which AAP 0.2.1 holds REFERENCE
and never modifies.  AAP 0.4.1 pairs this module with
``features/EmployeeFc.feature`` and ``features/steps/employee_steps.py``, the
port of ``EmployeeStage.java``.

**This is the only page object in the package that carries behaviour.**  The
other nine are pure locator holders; ``EmployeeP.java:59-69`` declares
:meth:`EmployeePage.login`, the single method in the whole reference page
package - which is also why ``app/pages/base_page.py`` deliberately holds no
behaviour of its own: a convenience added there would be inherited by all ten
pages, whereas this one belongs to this one page.

What is ported, field for field
-------------------------------
Fifteen ``@FindBy`` fields (``EmployeeP.java:14-57``) become the fifteen
locator constants of :class:`EmployeePage`, declared in Java order, and
``PageFactory.initElements(Driver.getDriver(), this)``
(``EmployeeP.java:10-12``) becomes the subclass hook in
``app/pages/base_page.py``, which turns each constant into a read-only
accessor that re-runs ``find_element`` on every access.  Nothing is looked up
at construction, so ``EmployeePage()`` touches neither the DOM nor a browser -
the same guarantee ``initElements`` gave, since it only installed proxies.

The two names each field yields, and both are used by the Employee steps:

===============================  ==========================================
``EmployeePage.EMPL_STAGE``      the ``(By.PARTIAL_LINK_TEXT, "Employees")``
                                 tuple, for ``wait_visible(locator, 3)`` and
                                 for the parity assertions in
                                 ``tests/test_pages.py``
``page.empl_stage``              the live element, re-resolved per access,
                                 for ``.click()``, ``.send_keys()``,
                                 ``.clear()``, ``.is_displayed()`` and
                                 ``wait_visible_element(element, 3)``
===============================  ==========================================

The lower-case accessors are exactly the field names ``EmployeeStage.java``
reaches for, transliterated to snake_case: ``employeePage.emplStage.click()``
(``:30``) becomes ``page.empl_stage.click()``, and
``employeePage.nameEdit.clear()`` / ``.sendKeys("Sterling")`` (``:101-102``)
becomes ``page.name_edit.clear()`` / ``.send_keys("Sterling")``.

Feature context, all of which lives in the step module and not here
-------------------------------------------------------------------
``features/EmployeeFc.feature`` carries ``@UPGN-344`` at line 1 and is the
only consumer of the ``url`` and ``EmplTitle`` configuration keys.
``EmployeeStage.java`` reads them itself - ``url`` at ``:24``, ``:60`` and
``:93``, ``EmplTitle`` at ``:31`` - so per AAP 0.4.2 the consumer of the
configuration accessors is ``features/steps/employee_steps.py``, **not this
module**, which reads no configuration at all.  For the same reason the timing the
Employee flow depends on stays there too: the 3-second ``WebDriverWait``
built at ``EmployeeStage.java:14``, and the one 7-second plus seven 3-second
fixed sleeps AAP 0.4.1 requires be *"reproduced as equivalent fixed delays at
the same call sites"*.  None of them belongs to a page object, and none
appears below.

Import boundary (AAP 0.4.2)
---------------------------
*"Page objects import ``app.automation`` for the current driver, nothing
else."*  This module therefore imports exactly two names -
:data:`~app.automation.By` for the locator strategies and
:class:`~app.pages.base_page.BasePage` for the resolution mechanism - and
nothing further: no browser-automation-library import of any kind, not even
under ``typing.TYPE_CHECKING``, because ``app.automation`` is the only package
in the port permitted to import that library and a guarded import is still an
import statement; no configuration module; no service, reporting writer, path
helper, web framework or Gherkin engine.  Importing this module has no side
effects whatever - it starts no browser, reads no file and configures no
logging - which is what lets the unit suite import it on a machine with no
browser installed.

Driver ownership (AAP 0.3.3)
----------------------------
*"One owner for the lifecycle, so no step or page ever creates or quits a
driver."*  :meth:`EmployeePage.login` works through
:attr:`~app.pages.base_page.BasePage.driver`, which hands back the session
injected at construction or this worker's current one; it never creates,
configures or quits a session, and ``features/environment.py`` has already
created it in ``before_scenario`` before any step runs.

What this module deliberately does not contain
----------------------------------------------
Each omission is the contract rather than an oversight, because AAP 0.8
requires the port to *"preserve, do not tidy"*:

* **No sixteenth locator.**  ``EmployeeP.java`` declares fifteen; a locator
  the reference never had would be a functional addition.
* **No second login method** - no ``login_with(username, password)`` that
  actually uses its arguments.  The reference's two-argument overload ignores
  both of its parameters, and reproducing that faithfully means one method
  that ignores them, not a new one that does not.
* **No navigation.**  ``EmployeeStage.java`` navigates first, with
  ``Driver.getDriver().get(...)`` at ``:24``, ``:60`` and ``:93``, and *then*
  calls ``login()``; the navigation stays in the step module.
* **No wait, no clear-before-type, no post-login assertion, no return value
  and no logging** inside :meth:`~EmployeePage.login`.  The Java body has
  three statements and so does this one.
"""

from app.automation import By
from app.pages.base_page import BasePage

__all__ = ["EmployeePage"]


class EmployeePage(BasePage):
    r"""The Employees module's fifteen locators, plus its one behaviour method.

    Subclasses :class:`~app.pages.base_page.BasePage`, so declaring the
    constants below is the whole of the wiring: the base class installs one
    read-only, never-cached accessor per constant under the lower-case name
    and publishes :attr:`~app.pages.base_page.BasePage.LOCATORS` as an
    immutable ``{constant name: locator}`` mapping in **declaration order**,
    which is the Java ``@FindBy`` order of ``EmployeeP.java:14-57``.  There
    are consequently no hand-written accessors and no hand-written inventory
    here - either would be a second, divergible copy of what the constants
    already say.

    :attr:`~app.pages.base_page.BasePage.PLURAL_LOCATORS` is *not* overridden:
    ``EmployeeP.java`` declares no ``List<WebElement>`` field, so all fifteen
    accessors resolve to a single element through ``find_element``.  (The
    reference's only plural field in the entire suite is ``SalesP.java:69``.)

    Usage, exactly as ``features/steps/employee_steps.py`` uses it::

        page = EmployeePage()                 # touches nothing at all
        page.login()                          # three DOM operations, now
        page.empl_stage.click()               # one find_element, now
        wait_visible(EmployeePage.EMPL_STAGE, 3)

    Nothing in the class body is protected against reassignment beyond the
    accessors being read-only properties: a locator constant is a module-level
    fact, and the parity tests read it rather than write it.
    """

    # ------------------------------------------------------------------
    # The login form (EmployeeP.java:14-21).
    #
    # The Employees flow signs in through this page rather than through
    # LoginP, because EmployeeStage.java navigates straight to the `url`
    # property and calls login() there (:24-25, :60-61, :93-94). These three
    # fields are the ones login() drives, in this order.
    # ------------------------------------------------------------------

    #: ``EmployeeP.java:14-15`` - ``@FindBy(id = "login")`` ``inputLogin``.
    #: The e-mail field :meth:`login` types into first.
    INPUT_LOGIN = (By.ID, "login")

    #: ``EmployeeP.java:17-18`` - ``@FindBy(id = "password")`` ``inputPass``.
    #: The password field :meth:`login` types into second.
    INPUT_PASS = (By.ID, "password")

    #: ``EmployeeP.java:20-21`` -
    #: ``@FindBy(xpath = "//button[.='Log in']")`` ``loginButton``.
    #: The submit button :meth:`login` clicks last.  The XPath matches on the
    #: button's exact string value, so the selector is preserved verbatim
    #: including its inner single quotes.
    LOGIN_BUTTON = (By.XPATH, "//button[.='Log in']")

    # ------------------------------------------------------------------
    # Module navigation (EmployeeP.java:23-36).
    #
    # Four partial-link-text entries into the Employees module and its
    # gamification sub-menus. EmployeeStage.java clicks EMPL_STAGE in five
    # separate steps (:30, :63, :86, :96, :109), and walks the other three in
    # one step apiece (:37-42 for badges/challenges/goals, :47 for
    # departments), each click followed there by its own explicit wait.
    # ------------------------------------------------------------------

    #: ``EmployeeP.java:23-24`` -
    #: ``@FindBy(partialLinkText = "Employees")`` ``emplStage``.
    EMPL_STAGE = (By.PARTIAL_LINK_TEXT, "Employees")

    #: ``EmployeeP.java:26-27`` -
    #: ``@FindBy(partialLinkText = "Badges")`` ``badgesBtn``.
    BADGES_BTN = (By.PARTIAL_LINK_TEXT, "Badges")

    #: ``EmployeeP.java:29-30`` -
    #: ``@FindBy(partialLinkText = "Challenges")`` ``challengesBtn``.
    CHALLENGES_BTN = (By.PARTIAL_LINK_TEXT, "Challenges")

    #: ``EmployeeP.java:32-33`` -
    #: ``@FindBy(partialLinkText = "Goals History")`` ``goalsHistoryBtn``.
    #: The single embedded space is part of the link text and is preserved.
    GOALS_HISTORY_BTN = (By.PARTIAL_LINK_TEXT, "Goals History")

    #: ``EmployeeP.java:35-36`` -
    #: ``@FindBy(partialLinkText = "Departments")`` ``departmentsBtn``.
    DEPARTMENTS_BTN = (By.PARTIAL_LINK_TEXT, "Departments")

    # ------------------------------------------------------------------
    # Employee creation (EmployeeP.java:38-48).
    #
    # The Odoo kanban "Create" control, the required-name input, the form's
    # save control and the confirmation paragraph. Driven by
    # EmployeeStage.java:69, :72, :73 and asserted at :78.
    #
    # The three class-attribute XPaths below match Odoo's compiled class
    # strings exactly, whitespace included: `//button[@class='...']` is an
    # exact-value match, not a token match, so re-ordering, trimming or
    # collapsing a space would change which element is found. They are
    # therefore reproduced byte for byte from the Java source.
    # ------------------------------------------------------------------

    #: ``EmployeeP.java:38-39`` - ``createBtn``.  Odoo's kanban "Create"
    #: button, matched on its full class attribute.
    CREATE_BTN = (
        By.XPATH,
        "//button[@class='btn btn-primary btn-sm o-kanban-button-new btn-default']",
    )

    #: ``EmployeeP.java:41-42`` - ``employeesName``.  The required "Employee's
    #: Name" input, matched on the class string Odoo gives a required char
    #: widget.  ``EmployeeStage.java:72`` sends the new employee's name here.
    EMPLOYEES_NAME = (
        By.XPATH,
        "//input[@class='o_field_char o_field_widget o_input o_required_modifier']",
    )

    #: ``EmployeeP.java:44-45`` - ``savedMessage``.  Named for the outcome but
    #: locating the form's *save* button, which is what
    #: ``EmployeeStage.java:73`` and ``:104`` click.  The misleading field name
    #: is the reference's and is carried over unchanged, per AAP 0.8's
    #: "preserve, do not tidy".
    SAVED_MESSAGE = (
        By.XPATH,
        "//button[@class='btn btn-primary btn-sm o_form_button_save']",
    )

    #: ``EmployeeP.java:47-48`` - ``createdMessage``.  The "Employee created"
    #: confirmation paragraph, matched on its exact text.
    #: ``EmployeeStage.java:78`` asserts it is displayed.
    CREATED_MESSAGE = (By.XPATH, "//p[.='Employee created']")

    # ------------------------------------------------------------------
    # Employee editing (EmployeeP.java:50-57).
    #
    # Three brittle-by-construction locators - two absolute DOM paths and one
    # generated Odoo widget id - reproduced exactly as declared. They are the
    # reference's choices, not the port's, and "correcting" any of them would
    # silently retarget the edit flow that EmployeeStage.java:98-102 drives.
    # ------------------------------------------------------------------

    #: ``EmployeeP.java:50-51`` - ``chooseEmployee``.  Absolute path to the
    #: first employee card in the kanban view; clicked at
    #: ``EmployeeStage.java:98``.
    CHOOSE_EMPLOYEE = (By.XPATH, "//html/body/div[1]/div[2]/div[2]/div/div/div/div[1]")

    #: ``EmployeeP.java:53-54`` - ``editEmployee``.  Absolute path to the
    #: record's "Edit" button; clicked at ``EmployeeStage.java:99``.
    EDIT_EMPLOYEE = (
        By.XPATH,
        "//html/body/div[1]/div[2]/div[1]/div[2]/div[1]/div/div[1]/button[1]",
    )

    #: ``EmployeeP.java:56-57`` - ``nameEdit``.  The name input on the edit
    #: form, addressed by an Odoo-generated widget id.
    #: ``EmployeeStage.java:101-102`` clears it and types ``"Sterling"``.
    #:
    #: The Java source escapes the inner double quotes -
    #: ``"//*[@id=\"o_field_input_678\"]"`` - so the selector string itself
    #: contains **literal double quotes**.  Written here as a single-quoted
    #: Python string so those double quotes survive unescaped and unaltered;
    #: converting them to single quotes would change the selector, and
    #: ``tests/test_pages.py`` compares it character for character.
    NAME_EDIT = (By.XPATH, '//*[@id="o_field_input_678"]')

    # Both parameters are accepted and never read, exactly as the two-argument
    # Java overload never reads its own (EmployeeP.java:65-69). The bare noqa
    # markers sit on the two parameter lines because that is where an
    # unused-argument diagnostic is reported, and they record that the unused
    # arguments are the specification rather than an oversight - the docstring
    # below states why at length.
    def login(
        self,
        input_login: str | None = None,  # noqa: ARG002
        input_pass: str | None = None,  # noqa: ARG002
    ) -> None:
        """Sign in to the Employees module with the suite's fixed credentials.

        :param input_login: Accepted and **ignored**.  Present only so that
            the two-argument Java call shape stays expressible; see below.
        :param input_pass: Accepted and **ignored**, for the same reason.
        :returns: ``None``.  The Java methods are ``void`` and this one adds
            no result of its own - not the driver, not a page object, not a
            success flag.
        :raises Exception: Whatever the driver raises, unwrapped - typically
            ``NoSuchElementException`` once the session's 10-second implicit
            wait expires on any of the three lookups, or ``AttributeError``
            when no session exists at all.  Nothing here catches, retries or
            logs, so a failure surfaces at the operation that caused it.

        The port of ``EmployeeP.java:59-63``, called at
        ``EmployeeStage.java:25``, ``:61`` and ``:94`` - always in the
        no-argument form, always immediately after the step has navigated to
        the ``url`` property.  Three operations, in this order:

        1. ``send_keys("posmanager50@info.com")`` on :attr:`INPUT_LOGIN`
        2. ``send_keys("posmanager")`` on :attr:`INPUT_PASS`
        3. ``click()`` on :attr:`LOGIN_BUTTON`

        Each goes through its lazy accessor, so each triggers its **own**
        ``find_element`` against the live DOM.  That is not incidental: the
        Java ``PageFactory`` proxy re-locates on every method invocation, so
        three operations meant three lookups there and mean three lookups
        here.  Hoisting the elements into locals would collapse them to three
        lookups at a single earlier instant and change behaviour on a page
        that re-renders between operations.

        Why the parameters exist and do nothing
        ---------------------------------------
        ``EmployeeP.java`` declares **two** overloads, ``login()`` at
        ``:59-63`` and ``login(String inputLogin, String inputPass)`` at
        ``:65-69``, whose bodies are byte-identical: the two-argument form
        discards both of its arguments and sends the very same hard-coded
        credentials.  Python has no overloading, so the port declares one
        method with optional parameters, keeping both Java call shapes
        expressible while reproducing the discard exactly.

        ``login("someone@example.com", "secret")`` therefore performs
        precisely the calls ``login()`` performs, and
        ``tests/test_pages.py`` asserts that equivalence.  This is faithful
        reproduction of the source, **not** a defect introduced by the port
        and not an unfinished parameterization: honouring the arguments would
        change behaviour, which AAP 0.1.2 forbids, since it admits no
        functional addition and no functional loss.

        Why the credentials are inline
        ------------------------------
        Both literals are carried over verbatim from ``EmployeeP.java:60-61``.
        AAP 0.8's test-data note governs and is explicit: credentials in this
        suite *"are carried over verbatim because parity requires it ... no
        agent should redact, parameterize or rotate them, or treat their
        presence as a finding."*  They are pre-existing fixture data for an
        external test instance, not a secret this port introduces, and they
        are deliberately **not** read from ``configuration.properties`` - the
        configuration surface stays at the six keys AAP 0.4.1 inventories, and
        the Java method reads none of them.
        """
        # Exactly the three statements of EmployeeP.java:60-62, in order, and
        # deliberately nothing else: no clear() before typing (the reference
        # clears only nameEdit, at EmployeeStage.java:101), no explicit wait
        # (the session's 10-second implicit wait is what covers these lookups,
        # and the Employee flow's 3-second explicit waits live at the step
        # call sites), and no assertion that the sign-in succeeded (the
        # feature asserts that in its own steps).
        self.input_login.send_keys("posmanager50@info.com")
        self.input_pass.send_keys("posmanager")
        self.login_button.click()
