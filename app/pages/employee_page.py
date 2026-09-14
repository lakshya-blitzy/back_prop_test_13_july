r"""Employees-module page object - the port of ``EmployeeP.java``.

The fifteen ``@FindBy`` fields of ``EmployeeP.java:14-57``, at pinned revision
``47e9d697e4a9a85da889f94a846fdf47af28a240``, as locator constants in Java
declaration order - part of the contract, since
:attr:`~app.pages.base_page.BasePage.LOCATORS` is built from the class body in
source order and compared against that order - plus the one behaviour method
the reference page package has.  AAP 0.4.1 pairs the module with
``features/EmployeeFc.feature`` and ``features/steps/employee_steps.py``, the
port of ``EmployeeStage.java``.

**This is the only page object in the package that carries behaviour.**  The
other nine are pure locator holders; ``EmployeeP.java:59-69`` declares
:meth:`EmployeePage.login`, which is why :class:`~app.pages.base_page.BasePage`
holds no behaviour of its own - a convenience added there would be inherited by
all ten pages, whereas this one belongs to this page.  The Employees flow signs
in here rather than through ``LoginP`` because ``EmployeeStage.java`` navigates
straight to the ``url`` property and calls ``login()`` there (``:24-25``,
``:60-61``, ``:93-94``).  The accessors are the Java field names in snake_case,
which is how ``EmployeeStage.java`` reaches them: ``emplStage.click()``
(``:30``) is ``page.empl_stage.click()`` and ``nameEdit`` (``:101``) is
``page.name_edit``.

Three selector shapes are carried verbatim under AAP 0.8's *"Preserve, do not
tidy"*: the exact-``@class`` XPaths of :attr:`~EmployeePage.CREATE_BTN`,
:attr:`~EmployeePage.EMPLOYEES_NAME` and :attr:`~EmployeePage.SAVED_MESSAGE`,
where ``@class=`` matches the whole attribute rather than a token, so no space
may be trimmed, re-ordered or collapsed; the two absolute DOM paths of
:attr:`~EmployeePage.CHOOSE_EMPLOYEE` and :attr:`~EmployeePage.EDIT_EMPLOYEE`;
and :attr:`~EmployeePage.NAME_EDIT`'s generated widget id.

The two names each field yields, and both are used by the Employee steps:

===============================  ==========================================
``EmployeePage.EMPL_STAGE``      the ``(By.PARTIAL_LINK_TEXT, "Employees")``
                                 tuple, for either visibility wait -
                                 ``wait_visible(locator, 3)`` and
                                 ``wait_visible_element(locator, 3)`` both
                                 take it - and for the parity assertions in
                                 ``tests/test_pages.py``
``page.empl_stage``              the live element, re-resolved per access,
                                 for ``.click()``, ``.send_keys()``,
                                 ``.clear()`` and ``.is_displayed()``
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
module**, which reads no configuration at all.  That holds for the
``username`` and ``password`` properties too: the two values
:meth:`EmployeePage.login` types arrive as its two arguments, read by the step
module at each of its three call sites, and :meth:`EmployeePage.login`
documents why.  For the same reason the timing the Employee flow depends on
stays there too: the 3-second ``WebDriverWait`` built at
``EmployeeStage.java:14``, and the one 7-second plus seven 3-second fixed
sleeps AAP 0.4.1 requires be *"reproduced as equivalent fixed delays at the
same call sites"*.  None of them belongs to a page object, and none appears
below.

Import boundary (AAP 0.4.2)
---------------------------
*"Page objects import ``app.automation`` for the current driver, nothing
else."*  This module therefore imports exactly two names -
:data:`~app.automation.By` for the locator strategies and
:class:`~app.pages.base_page.BasePage` for the resolution mechanism - and
nothing further: no browser-automation-library import of any kind, not even
under ``typing.TYPE_CHECKING``, because ``app.automation`` is the only package
in the port permitted to import that library and a guarded import is still an
import statement; **no configuration module** - AAP 0.4.2 enumerates
``app.config``'s consumers exhaustively and names no page module among them,
which is why the credentials are parameters here rather than a read; no
service, reporting writer, path helper, web framework or Gherkin engine.
Importing this module has no side effects whatever - it starts no browser,
reads no file and configures no logging - which is what lets the unit suite
import it on a machine with no browser installed.

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
* **No second login method** - no ``login_with(username, password)`` beside
  :meth:`~EmployeePage.login`.  ``EmployeeP.java`` declares two overloads with
  byte-identical bodies, so the port declares one method whose two optional
  parameters express both call shapes; a second method would be a surface the
  reference does not have.
* **No navigation.**  ``EmployeeStage.java`` navigates first, with
  ``Driver.getDriver().get(...)`` at ``:24``, ``:60`` and ``:93``, and *then*
  calls ``login()``; the navigation stays in the step module.
* **No wait, no clear-before-type, no post-login assertion, no return value
  and no logging** inside :meth:`~EmployeePage.login`.  The Java body has
  three statements and so does this one.
* **No credential, and no configuration read.**  Neither value appears in this
  file, as a literal or as a default, and neither is fetched here: both arrive
  as arguments.  The configuration surface is untouched by this module - it
  stays at the six keys AAP 0.4.1 inventories, read by the consumers AAP 0.4.2
  names.
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
    which is the ``@FindBy`` order of ``EmployeeP.java:14-57``.  Each constant
    cites the Java line and field name it ports.

    :attr:`~app.pages.base_page.BasePage.PLURAL_LOCATORS` is *not* overridden:
    ``EmployeeP.java`` declares no ``List<WebElement>`` field, so all fifteen
    accessors resolve to a single element through ``find_element``.  (The
    reference's only plural field in the entire suite is ``SalesP.java:69``.)

    Usage, exactly as ``features/steps/employee_steps.py`` uses it - the two
    credentials come from that module's own configuration reads, because this
    one performs none::

        page = EmployeePage()                 # touches nothing at all
        page.login(get_username(), get_password())   # three DOM operations
        page.empl_stage.click()               # one find_element, now
        wait_visible(EmployeePage.EMPL_STAGE, 3)
    """

    #: ``EmployeeP.java:14-15``'s ``inputLogin``, typed into first by
    #: :meth:`login`.
    INPUT_LOGIN = (By.ID, "login")

    #: ``EmployeeP.java:17-18``'s ``inputPass``, typed into second by
    #: :meth:`login`.
    INPUT_PASS = (By.ID, "password")

    #: ``EmployeeP.java:20-21``'s ``loginButton``, clicked last by :meth:`login`.
    LOGIN_BUTTON = (By.XPATH, "//button[.='Log in']")

    #: ``EmployeeP.java:23-24``'s ``emplStage``.
    EMPL_STAGE = (By.PARTIAL_LINK_TEXT, "Employees")

    #: ``EmployeeP.java:26-27``'s ``badgesBtn``.
    BADGES_BTN = (By.PARTIAL_LINK_TEXT, "Badges")

    #: ``EmployeeP.java:29-30``'s ``challengesBtn``.
    CHALLENGES_BTN = (By.PARTIAL_LINK_TEXT, "Challenges")

    #: ``EmployeeP.java:32-33``'s ``goalsHistoryBtn``, whose embedded space is
    #: part of the link text.
    GOALS_HISTORY_BTN = (By.PARTIAL_LINK_TEXT, "Goals History")

    #: ``EmployeeP.java:35-36``'s ``departmentsBtn``.
    DEPARTMENTS_BTN = (By.PARTIAL_LINK_TEXT, "Departments")

    #: ``EmployeeP.java:38-39``'s ``createBtn``, on its full class attribute.
    CREATE_BTN = (
        By.XPATH,
        "//button[@class='btn btn-primary btn-sm o-kanban-button-new btn-default']",
    )

    #: ``EmployeeP.java:41-42``'s ``employeesName``, on the class string Odoo
    #: gives a required char widget.
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

    #: ``EmployeeP.java:47-48``'s ``createdMessage``, on its exact text.
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

    def login(
        self,
        input_login: str | None = None,
        input_pass: str | None = None,
    ) -> None:
        """Type the two supplied credentials into the form and submit it.

        :param input_login: The value typed into :attr:`INPUT_LOGIN`.  The
            caller supplies it - ``features/steps/employee_steps.py`` reads the
            ``username`` property at each of its three ``login()`` call sites -
            and this module neither defaults it to a value of its own nor
            fetches one; see below.
        :param input_pass: The value typed into :attr:`INPUT_PASS`, supplied by
            the same caller from the ``password`` property.
        :returns: ``None``.  The Java methods are ``void`` and this one adds
            no result of its own - not the driver, not a page object, not a
            success flag.
        :raises Exception: Whatever the driver raises, unwrapped - typically
            ``NoSuchElementException`` once the session's 10-second implicit
            wait expires on any of the three lookups, or ``AttributeError``
            when no session exists at all.  Nothing here catches, retries or
            logs, so a failure surfaces at the operation that caused it.  An
            omitted argument is not pre-empted either: it arrives as ``None``
            and the driver rejects it at the ``send_keys`` that received it,
            which is the tolerant configuration behaviour AAP 0.6 fixes for
            every unset property in the port - the same way a ``None`` from an
            undefined key reaches ``driver.get(...)`` in the step module and
            ``send_keys`` in the shared sign-in precondition
            (``features/steps/session_steps.py``).

        The port of ``EmployeeP.java:59-63``, called at
        ``EmployeeStage.java:25``, ``:61`` and ``:94`` - always in the
        no-argument form there, and always immediately after the step has
        navigated to the ``url`` property.  Three operations, in this order:

        1. ``send_keys(input_login)`` on :attr:`INPUT_LOGIN`
        2. ``send_keys(input_pass)`` on :attr:`INPUT_PASS`
        3. ``click()`` on :attr:`LOGIN_BUTTON`

        Each goes through its lazy accessor, so each triggers its **own**
        ``find_element`` against the live DOM.  That is not incidental: the
        Java ``PageFactory`` proxy re-locates on every method invocation, so
        three operations meant three lookups there and mean three lookups
        here.  Hoisting the elements into locals would collapse them to three
        lookups at a single earlier instant and change behaviour on a page
        that re-renders between operations.

        Why the parameters exist, and the one parity departure they cost
        ----------------------------------------------------------------
        ``EmployeeP.java`` declares **two** overloads, ``login()`` at
        ``:59-63`` and ``login(String inputLogin, String inputPass)`` at
        ``:65-69``, whose bodies are byte-identical: each sends two hard-coded
        credentials, so the two-argument form **discards both of its
        arguments**.  Python has no overloading, so the port declares one
        method with two optional parameters, and that two-argument shape is
        what it expresses.

        **The discard is not reproduced, and that is deliberate.**  It is the
        single parity departure this method makes, and it is the smallest one
        available.  What the Java discarded its arguments in favour of are the
        two account literals at ``:60-61``, and a credential in executable
        Python source is durable in the repository, in every clone of it and in
        every artifact built from it - the security review raised exactly that
        as a blocking finding (CWE-798/200).  Removing the literals leaves the
        two values needing a source, and AAP 0.4.2 rules out the obvious one
        *here*: *"Page objects import ``app.automation`` for the current
        driver, nothing else"*, and its exhaustive enumeration of
        ``app.config``'s consumers - ``employee_steps``, ``session_steps``,
        ``login_steps`` and ``driver.py`` - names no page module.  Every route
        that keeps this module free of ``app.config`` therefore needs the
        caller to supply the values, which means honouring the parameters.
        AAP 0.1.3's precedence rule settles the residue: an explicit AAP rule
        outranks a finding's suggested resolution, so the boundary is kept and
        the discard is given up.

        What survives unchanged is everything observable about the sign-in
        itself: the two fields in the Java order, the third click, three
        separate lookups, no ``clear()``, no wait, no assertion and no logging.
        ``login(a, b)`` types ``a`` then ``b``; ``login()`` types ``None``
        twice and fails at the driver, exactly as an undefined property does
        everywhere else in the port.  ``tests/test_pages.py`` pins both.

        Where the credentials come from
        -------------------------------
        From the caller, and from nowhere in this file.  Neither value is a
        literal here, neither is a parameter default, and this module performs
        no configuration read of any kind - ``features/steps/employee_steps.py``
        reads the ``username`` and ``password`` properties through
        ``app.config`` at each of its three call sites, which is the layer AAP
        0.4.2 puts configuration reads in.

        No key was added for it: ``username`` and ``password`` are two of the
        six keys AAP 0.4.1 inventories, already read by the shared sign-in
        precondition at ``Session.java:15-16``, so the configuration surface is
        unchanged and a deployment that has a ``configuration.properties`` at
        all already has the two values.

        AAP 0.8's test-data note is not a licence to keep the literals either.
        Its subject is the Gherkin Examples tables, which *"carry plaintext
        credentials for the system under test and are carried over verbatim
        because parity requires it"* - those tables are step arguments a
        scenario supplies, and they stay exactly as they are in the ten
        ``.feature`` files.  Two hard-coded Python literals were never that:
        sanctioned fixture data may stay where it is sanctioned, but new code
        must not log, copy or replicate it.
        """
        # Exactly the three statements of EmployeeP.java:60-62, in order, and
        # deliberately nothing else: no clear() before typing (the reference
        # clears only nameEdit, at EmployeeStage.java:101), no explicit wait
        # (the session's 10-second implicit wait is what covers these lookups,
        # and the Employee flow's 3-second explicit waits live at the step
        # call sites), and no assertion that the sign-in succeeded (the
        # feature asserts that in its own steps).
        #
        # The arguments are passed straight through, unguarded and not
        # normalized: a None must surface at the operation that received it
        # (AAP 0.6), the same way an undefined property does at every other
        # point of use in the port. And each self.<accessor> must stay inside
        # its own statement so the three find_element lookups happen at the
        # three instants the Java PageFactory proxy performed them.
        self.input_login.send_keys(input_login)
        self.input_pass.send_keys(input_pass)
        self.login_button.click()
