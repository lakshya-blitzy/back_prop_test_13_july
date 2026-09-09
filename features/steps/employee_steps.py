"""behave step definitions for the Odoo Employees module.

The Python port of the Java class
``com.testinium.step_definitions.EmployeeStage`` at reference commit
``47e9d697e4a9a85da889f94a846fdf47af28a240``, one Python function per Java
method, in the source's own order.  That class **is** the specification of
every body below: each statement here corresponds to exactly one statement
there, and the Java line is cited beside it, so a reader can diff the two side
by side without leaving this file.

The twelve definitions
----------------------
Every phrase is byte-exact, copied from its Java annotation.  ``AAP`` below
refers to the Agent Action Plan; ``:NN`` to a line of ``EmployeeStage.java``.

===  =====  ===============================================================
 #   Java   Phrase, and what the body does
===  =====  ===============================================================
 1   :16    ``User is on upgenix login page`` - reads ``web.table.url`` and
            navigates.  **Never invoked by any feature**; see "The orphan".
 2   :22    ``User is on the dashboard`` - navigates to ``url``, signs in.
 3   :28    ``User clicks Employees stage`` - click, wait on the *configured*
            title, assert the *hard-coded* one.  See "Two title sources".
 4   :35    ``User clicks Challenges stage`` - three click/wait pairs.
 5   :45    ``User clicks Departments stage`` - click, then sleep 7 seconds.
 6   :50    ``User should see the last stage title`` - one assertion.
 7   :58    ``User is on the employees dashboard`` - navigate, sign in,
            sleep, click.
 8   :66    ``User creates new employees "{name}" in the Employees stage`` -
            the only parameterized step in the class.
 9   :76    ``User should see the Employee created message under full
            profile`` - one assertion.
10   :84    ``User should see listed employees in the Employees stage`` -
            click, wait, assert.
11   :91    ``User edits created employees in the Employees module`` - the
            longest body: navigate, sign in and four interleaved sleeps.
12   :107   ``User should see the edited name in the Employees module`` - a
            single click, and **no assertion at all**.
===  =====  ===============================================================

Each function keeps the *Java method's* name, snake_cased exactly as the
source wrote it - which is why :func:`user_should_see_the_message_under_full_profile`
is shorter than the phrase it registers, and why nothing here carries a
``step_impl`` name.  That mapping is deliberate: AAP 0.1.3 deviation 8 has the
report's ``match.location`` field carry a dotted Python path, so a location
reading ``employee_steps.user_clicks_employees_stage`` sits directly against
the JVM's ``EmployeeStage.user_clicks_employees_stage()``, and
``tests/test_steps_employee.py`` can enumerate the Java class's methods and
find each one here by name.

Registered with ``@step``, and only ``@step``
---------------------------------------------
AAP 0.1.3 deviation 7 and AAP 0.5.2: **every** definition in the port
registers with ``@step``, and no step module imports ``given``, ``when`` or
``then``.  This is behavioural rather than stylistic.  Cucumber-JVM matches a
step by its *text* alone - ``Given``, ``When``, ``Then`` and ``And`` are
interchangeable at match time - whereas behave resolves by *effective* step
type, where ``And`` inherits the keyword of the step above it.  A definition
registered under one type therefore would not match a use under another, and
the suite depends on the difference: ``Session.java:12`` declares the shared
precondition ``@When`` and six Backgrounds invoke it as ``Given``, and three
step classes import ``io.cucumber.java.en.And``, for which behave has no
decorator at all.  ``@step`` matches whatever keyword invokes it, which
reproduces text-only matching exactly.

It matters in this module specifically.  ``EmployeeFc.feature`` reaches
``:10``, ``:11``, ``:12``, ``:18``, ``:28`` and ``:38`` through ``And``, whose
effective type is ``When``; three of those - and both ``Then`` uses at ``:19``
and ``:29`` - land on definitions the Java class annotates ``@Then``.  The
keywords happen to line up today, so registering by keyword would pass now and
break the moment a feature's keyword changed.  ``@step`` removes that latency.

Import boundary (AAP 0.4.2)
---------------------------
The five imports below are the **complete and closed** list for this module.
In particular:

* **No browser-binding import, in any form.**  ``app.automation`` is the only
  package permitted to import the browser binding, and
  ``tests/test_steps_registration.py`` asserts that no module under
  ``features/steps/`` does.  So no locator-strategy constant either - the
  login step module needs one, for ``LoginSD.java:56``, and this class does
  not, because it names no locator of its own - and no keyboard-key or
  action-chain helper, since ``EmployeeStage.java`` imports neither of the
  corresponding Java classes: its two ``sendKeys`` calls at ``:72`` and
  ``:102`` are plain sends on an already-located element.
* **No session-lifecycle import and no session-lifecycle call.**
  ``features/environment.py`` owns that lifecycle exclusively - AAP 0.3.3:
  *"no step or page ever creates or quits a driver"*.  Worth stating twice
  here, because three definitions navigate and sign in (``#2``, ``#7``,
  ``#11``): that is page navigation, not session management, and this module
  neither opens a session nor closes one.
* **No** ``app.utils.properties``.  ``app/config.py`` is its only permitted
  consumer, and the three accessors below are how this module reaches a key.
* **Nothing from another step module.**  There are no imports between step
  modules, which is why ``#1`` implements its two statements itself rather
  than borrowing the near-identical body in the login step module.
* Exactly the **two** wait shapes this class uses, and no others - the two
  named below, taken from the automation package's public surface.

The page object and the wait bind per scenario, not per module
--------------------------------------------------------------
``EmployeeStage.java:13-14`` builds both as instance fields, at glue
construction::

    EmployeeP employeePage = new EmployeeP();
    WebDriverWait wait = new WebDriverWait(Driver.getDriver(), 3);

Neither has a module-level counterpart here.  A page object built at import
time would bind whichever worker process imported the module first - scenarios
are sharded across a process pool (AAP 0.4.1) and ``features/environment.py``
quits its session after every scenario, so an import-time binding would hand
its locators to a session belonging to another scenario, or to a dead one.
Instead:

* :func:`_page` builds an :class:`~app.pages.employee_page.EmployeePage`
  around ``context.driver`` inside each step, which costs nothing because the
  constructor stores the driver and touches neither the DOM nor a session.
* The wait becomes a **timeout argument** rather than an object.  Both
  ``wait_title_is`` and ``wait_visible_element`` take ``timeout`` as a
  required parameter with no default, so this class's ``3`` stays visible at
  all six of its call sites - and no call site passes a ``driver``, because
  the helpers resolve this worker's session themselves, which is precisely
  what ``new WebDriverWait(Driver.getDriver(), 3)`` did.

``context.driver`` is the session ``features/environment.py``'s
``before_scenario`` published.  It is read directly for the three navigations
and the three title reads, exactly where the Java wrote
``Driver.getDriver()``.

Configuration: three of the six keys, read at five sites
--------------------------------------------------------
This is the port's heaviest configuration consumer, and every read goes
through ``app/config.py``:

======================  =================  ==============  ===============
Key                     Accessor           Java line(s)    Used by
======================  =================  ==============  ===============
``web.table.url``       get_web_table_url  :18             #1
``url``                 get_url            :24, :60, :93   #2, #7, #11
``EmplTitle``           get_empl_title     :31             #3
======================  =================  ==============  ===============

``url`` is read **three times, at three separate call sites**, because
``ConfigurationReader.getProperty`` is called afresh at each of those three
Java lines.  It is deliberately not hoisted into a module constant or a shared
local: hoisting would collapse three reads into one and change what a
mid-run edit of the properties file could affect.

**None is guarded, and that is the behaviour.**  All three accessors return
``str | None``, and a key the properties file does not define reads as
``None`` - AAP 0.4.1: *"a missing key returns null so failures surface at the
point of use"*.  So this module adds no check, no default, no fallback, no
validation and no error message of its own: a ``None`` travels into
``driver.get(...)`` or ``wait_title_is(...)`` and fails there, which is
exactly where the Java fails.  Nor is any key added - the surface is six keys,
and AAP 0.6 in particular adds none for the browser's language or region.

Two title sources, deliberately not unified
-------------------------------------------
``#3`` waits for the title to equal the **configured** ``EmplTitle`` (``:31``)
and then asserts against the **hard-coded** ``"Employees - Odoo"`` (``:32``) -
two different sources for what is nominally the same expectation.  ``#10``
uses the literal for both (``:87``, ``:88``).  The inconsistency is the
source's and is preserved as written; unifying them would change which value
governs.  For the same reason ``"Employees - Odoo"`` is written out at each of
its three occurrences instead of being lifted into a constant: the Java
repeats the literal, and a constant would imply a single source it does not
have.

The orphan (``#1``)
-------------------
``EmployeeStage.java:16`` declares ``User is on upgenix login page`` and **no
feature file invokes it** - the suite's single orphan definition, confirmed by
grepping all ten feature files at the reference commit.  It differs from
``LoginSD.java:19``'s ``User is on the upgenix login page`` by the one word
"the", and both are registered, both distinct, and neither matches the other's
text.  It is ported because parity is with the class, not with the subset of
it the features happen to reach, and it is left uncalled: no phrase here is
tidied, merged or renamed, and no feature step is added to give it a caller.

The eight fixed sleeps
----------------------
Eight of the suite's seventeen fixed delays live in this class - the largest
concentration, 28 seconds in total - and each is reproduced at the same call
site, in the same position: ``:48`` 7s in ``#5`` after the click, the suite's
only 7-second sleep; ``:62`` in ``#7``; ``:68`` and ``:70`` in ``#8``; and
``:95``, ``:97``, ``:100`` and ``:103`` in ``#11``.  Their interleaving with
the clicks is the behaviour, not decoration around it.

None is converted to an explicit wait.  AAP 0.4.1 is explicit that converting
them *"would change timing behaviour and, in a suite whose steps depend on
Odoo's client-side rendering, could change outcomes"*, and that replacing them
is a follow-up the user can request rather than part of this port.  The delays
come from the standard library because ``app.automation`` exposes no sleep
helper and must not gain one.  Java's milliseconds become seconds:
``Thread.sleep(7000)`` is ``sleep(7)``.

Assertions
----------
All four assertions in the class are ``Assert.assertTrue`` **with no message**,
so all four become a bare ``assert`` with no message string (AAP 0.5.2,
deviation 16: Python cannot produce JUnit's ``expected:<...> but was:<...>``
or a Java stack trace, so the assertion *subject* is parity and its formatting
is not).  Nothing is caught: a failed assertion, an expired wait or a missing
element propagates uncaught and fails the step, exactly as in the Java, and
there is no ``try``, no ``except``, no logging and no ``print`` anywhere in
this module - the source has none either.

Deliberately absent, and load-bearing
-------------------------------------
Please do not "helpfully" add any of these:

* No ``__all__``.  Nothing imports from a step module; behave imports it for
  the registrations its decorators perform, and the twelve phrases are the
  interface.
* No thirteenth definition, and no ``User login to test other features``
  phrase.  ``EmployeeFc.feature``'s Background declares **no step**
  (``:5``), so this is the one feature area that does not use the shared
  ``Session`` precondition - it signs in through ``#2``, ``#7`` and ``#11``,
  making it the only area that logs in via a page-object method.
* No assertion in ``#12``, whose phrase claims the user "should see the edited
  name" while its body (``:109``) is a single click.  No wait in ``#5``
  besides its sleep, though the class has one available.
* No port of the commented-out lines at ``:53-55`` and ``:79-81``.  Each is an
  alternative ``assertEquals`` formulation, commented out in the source, and
  has no behaviour.
* No ``__init__.py`` and no ``conftest.py`` beside this file.  behave puts its
  own steps directory on the path, and this module's verifying test lives in
  ``tests/test_steps_employee.py``.
* No renaming across the Odoo / Testinium / Upgenix vocabularies, which AAP
  conflict 8 settles: the lower-case ``upgenix`` in ``#1``'s phrase and the
  three ``- Odoo`` titles stay exactly as the source wrote them.

What ``tests/test_steps_employee.py`` asserts
---------------------------------------------
Stated here so the suite can be implemented faithfully.  It drives all twelve
against a stubbed driver and checks, per definition, the same observable
operations in the same order: the navigation targets **and the accessor each
one came from**, the three no-argument ``login()`` calls each immediately
after that step's ``get(...)``, the locators (against ``EmployeePage``'s own
declarations), the wait shape, its target and the timeout ``3`` at all six
sites, the literals ``"Employees - Odoo"``, ``"Departments - Odoo"``,
``"New - Odoo"`` and ``"Sterling"``, the four message-less assertion subjects,
``#3``'s two title sources, the single-click no-op of ``#12``, and the eight
delays with their durations and positions - plus the negatives: no print, no
key sent, no action chain, no driver created or quit, and no step sleeping
that should not.
"""

from time import sleep

from behave import step

from app.automation import wait_title_is, wait_visible_element
from app.config import get_empl_title, get_url, get_web_table_url
from app.pages import EmployeePage


def _page(context) -> EmployeePage:
    """Build this scenario's page object around the live session.

    :param context: behave's ``Context``.  Only ``context.driver`` is read -
        the session ``features/environment.py``'s ``before_scenario``
        published for the scenario now running.
    :returns: A fresh :class:`~app.pages.employee_page.EmployeePage` bound to
        that session.

    The stand-in for ``EmployeeStage.java:13``'s ``employeePage`` field, which
    moves out of glue construction and into each step for the reason the
    module docstring gives.  Constructing one is free: it stores the driver
    and does nothing else, so no element is located and no session is created,
    configured or quit here.  Every locator on the returned object resolves
    lazily, at the moment it is used, which is what ``PageFactory``'s proxies
    did in the Java.

    ``context.driver`` is passed through exactly as published, ``None``
    included: ``Driver.java:29-42`` switches on the ``browser`` property with
    no default branch, so an unrecognised value yields no session and the
    failure has to surface at the first use rather than here.
    """
    return EmployeePage(context.driver)


# ---------------------------------------------------------------------------
# 1 - EmployeeStage.java:16-20.  The orphan: registered, and invoked by no
#     feature file in the suite.  See "The orphan" in the module docstring.
# ---------------------------------------------------------------------------
@step("User is on upgenix login page")
def user_is_on_upgenix_login_page(context) -> None:
    """Navigate to the sign-in page named by ``web.table.url``.

    :param context: behave's ``Context``; ``context.driver`` is the session.
    :returns: ``None``.

    Two statements, from ``EmployeeStage.java:18-19``.  The local variable is
    kept because the Java keeps one, and ``None`` reaches ``get()`` unguarded
    when the key is undefined.

    The phrase is byte-exact and says ``upgenix login page``, without the
    "the" that ``LoginSD.java:19``'s ``User is on the upgenix login page``
    carries.  The two are separate registrations and neither resolves the
    other's text; the near-duplication is the source's and is preserved.
    """
    url = get_web_table_url()  # :18 - ConfigurationReader.getProperty
    context.driver.get(url)  # :19


# ---------------------------------------------------------------------------
# 2 - EmployeeStage.java:22-26
# ---------------------------------------------------------------------------
@step("User is on the dashboard")
def user_is_on_the_dashboard(context) -> None:
    """Navigate to the Employee module and sign in.

    :param context: behave's ``Context``; ``context.driver`` is the session.
    :returns: ``None``.

    ``:24`` navigates to the ``url`` property - a different page from
    ``web.table.url`` - and ``:25`` signs in through the page object's own
    ``login()``, with no arguments, exactly as the Java calls it.  That
    method sends the two credentials ``EmployeeP.java:60-61`` hard-codes and
    clicks the button; its three operations are not reimplemented here.
    """
    context.driver.get(get_url())  # :24
    _page(context).login()  # :25


# ---------------------------------------------------------------------------
# 3 - EmployeeStage.java:28-33.  Waits on the CONFIGURED title, asserts the
#     HARD-CODED one; see "Two title sources" in the module docstring.
# ---------------------------------------------------------------------------
@step("User clicks Employees stage")
def user_clicks_employees_stage(context) -> None:
    """Open the Employees stage and confirm the page title.

    :param context: behave's ``Context``; ``context.driver`` is the session.
    :returns: ``None``.
    :raises AssertionError: When the page title is not
        ``"Employees - Odoo"``, message-less exactly as ``:32`` is.
    :raises TimeoutException: When the title has not become the configured
        ``EmplTitle`` within 3 seconds.  Propagated uncaught, as in the Java.

    Three statements, ``:30`` to ``:32``.  The wait's expected title is the
    configured ``EmplTitle`` while the assertion's is the literal below - two
    sources, unchanged.
    """
    _page(context).empl_stage.click()  # :30
    wait_title_is(get_empl_title(), 3)  # :31 - titleIs(getProperty("EmplTitle"))
    assert context.driver.title == "Employees - Odoo"  # :32


# ---------------------------------------------------------------------------
# 4 - EmployeeStage.java:35-43.  Three click/wait pairs, each waiting on the
#     element it has just clicked.
# ---------------------------------------------------------------------------
@step("User clicks Challenges stage")
def user_clicks_challenges_stage(context) -> None:
    """Walk Badges, then Challenges, then Goals History.

    :param context: behave's ``Context``; ``context.driver`` is the session.
    :returns: ``None``.
    :raises TimeoutException: When any of the three elements is not visible
        within 3 seconds.  Propagated uncaught.

    Six statements, ``:37`` to ``:42``, in strict click-then-wait order.  The
    page object is built once for the whole body, as the Java field was, so
    the pairs read as they do there - and because every accessor re-locates
    on access, each ``click`` and each wait still resolves its element
    against the live DOM at the moment it runs.

    Each wait is ``visibilityOf`` on the element just clicked, not on the
    element about to be clicked.  That is what the source does and it is not
    corrected here.
    """
    page = _page(context)
    page.badges_btn.click()  # :37
    wait_visible_element(page.badges_btn, 3)  # :38
    page.challenges_btn.click()  # :39
    wait_visible_element(page.challenges_btn, 3)  # :40
    page.goals_history_btn.click()  # :41
    wait_visible_element(page.goals_history_btn, 3)  # :42


# ---------------------------------------------------------------------------
# 5 - EmployeeStage.java:45-49.  The suite's only 7-second sleep, and this
#     step's only wait of any kind.
# ---------------------------------------------------------------------------
@step("User clicks Departments stage")
def user_clicks_departments_stage(context) -> None:
    """Open the Departments stage and wait out a fixed 7 seconds.

    :param context: behave's ``Context``; ``context.driver`` is the session.
    :returns: ``None``.

    Two statements, ``:47`` and ``:48``.  ``Thread.sleep(7000)`` is the whole
    of this step's synchronization: it builds no explicit wait even though
    the class has one available, and none is added here.  The delay stays a
    fixed delay for the reason the module docstring gives.
    """
    _page(context).departments_btn.click()  # :47
    sleep(7)  # :48 - Thread.sleep(7000)


# ---------------------------------------------------------------------------
# 6 - EmployeeStage.java:50-56.  Its :53-55 are commented out in the source
#     and are not ported.
# ---------------------------------------------------------------------------
@step("User should see the last stage title")
def user_should_see_the_last_stage_title(context) -> None:
    """Assert the browser is showing the Departments page.

    :param context: behave's ``Context``; ``context.driver`` is the session.
    :returns: ``None``.
    :raises AssertionError: When the page title is not
        ``"Departments - Odoo"``, message-less exactly as ``:52`` is.

    One statement, ``:52``.  The three lines below it in the source are a
    commented-out ``assertEquals`` formulation of the same check; they carry
    no behaviour and are not reproduced.
    """
    assert context.driver.title == "Departments - Odoo"  # :52


# ---------------------------------------------------------------------------
# 7 - EmployeeStage.java:58-64
# ---------------------------------------------------------------------------
@step("User is on the employees dashboard")
def user_is_on_the_employees_dashboard(context) -> None:
    """Navigate to the Employee module, sign in, settle, open the stage.

    :param context: behave's ``Context``; ``context.driver`` is the session.
    :returns: ``None``.

    Four statements, ``:60`` to ``:63``, and the ``url`` property is read
    here for the second of its three times.  The fixed delay sits between the
    sign-in and the click, which is where the source puts it; moving it or
    replacing it with a wait on the stage link would change the timing this
    step depends on.

    Note the difference from ``#2``: the same navigation and sign-in, then a
    delay and a click.  The source declares both steps and this module keeps
    both, rather than expressing one in terms of the other.
    """
    page = _page(context)
    context.driver.get(get_url())  # :60
    page.login()  # :61
    sleep(3)  # :62 - Thread.sleep(3000)
    page.empl_stage.click()  # :63


# ---------------------------------------------------------------------------
# 8 - EmployeeStage.java:66-74.  The class's only parameterized step.
# ---------------------------------------------------------------------------
@step('User creates new employees "{name}" in the Employees stage')
def user_creates_new_employees_in_the_employees_stage(context, name: str) -> None:
    """Create an employee record under the given name.

    :param context: behave's ``Context``; ``context.driver`` is the session.
    :param name: The employee's name, taken from the quoted value in the
        feature step.  ``EmployeeFc.feature`` supplies ``Cristiano Ronaldo``
        (``:23``) and ``Lionel Messi`` (``:33``) through it, from two
        ``Examples: Employee's name`` blocks.
    :returns: ``None``.
    :raises TimeoutException: When the title has not become
        ``"New - Odoo"`` within 3 seconds.  Propagated uncaught.

    Six statements, ``:68`` to ``:73``, including **two** of the eight fixed
    delays: one before the create button is clicked and one between that
    click and the title wait.  Both positions are the behaviour.

    Cucumber's ``{string}`` placeholder matches the quotation marks around
    the value and passes the unquoted text; behave's ``parse`` matcher does
    not, so the quotes are written literally into the pattern around a named
    field.  The value the step body receives is identical either way.  The
    pattern is deliberately no wider than this - a bare ``{name}`` would
    stop matching the quotes and could collide with another phrase.

    ``:72`` is a plain send on the located input, which is why this module
    needs no keyboard helper.
    """
    page = _page(context)
    sleep(3)  # :68 - Thread.sleep(3000)
    page.create_btn.click()  # :69
    sleep(3)  # :70 - Thread.sleep(3000)
    wait_title_is("New - Odoo", 3)  # :71
    page.employees_name.send_keys(name)  # :72
    page.saved_message.click()  # :73


# ---------------------------------------------------------------------------
# 9 - EmployeeStage.java:76-82.  Its :79-81 are commented out in the source
#     and are not ported.  The function keeps the Java method's own name,
#     which is shorter than the phrase it registers.
# ---------------------------------------------------------------------------
@step("User should see the Employee created message under full profile")
def user_should_see_the_message_under_full_profile(context) -> None:
    """Assert the "Employee created" confirmation is on screen.

    :param context: behave's ``Context``; ``context.driver`` is the session.
    :returns: ``None``.
    :raises AssertionError: When the confirmation element is not displayed,
        message-less exactly as ``:78`` is.

    One statement, ``:78``.  The assertion subject is the element's
    ``is_displayed()``, not its text: the three lines below it in the source
    read the text and compare it to ``"Employee created"``, but they are
    commented out and carry no behaviour, so this step asserts visibility
    alone.
    """
    assert _page(context).created_message.is_displayed()  # :78


# ---------------------------------------------------------------------------
# 10 - EmployeeStage.java:84-89.  Unlike #3, both the wait and the assertion
#      take the same hard-coded title.
# ---------------------------------------------------------------------------
@step("User should see listed employees in the Employees stage")
def user_should_see_listed_employees_in_the_employees_stage(context) -> None:
    """Reopen the Employees stage and confirm the listing page.

    :param context: behave's ``Context``; ``context.driver`` is the session.
    :returns: ``None``.
    :raises AssertionError: When the page title is not
        ``"Employees - Odoo"``, message-less exactly as ``:88`` is.
    :raises TimeoutException: When the title has not become
        ``"Employees - Odoo"`` within 3 seconds.  Propagated uncaught.

    Three statements, ``:86`` to ``:88``.  The literal appears twice because
    the source writes it twice - once for the wait and once for the
    assertion - and it is the same literal ``#3`` asserts against while
    waiting on the configured ``EmplTitle`` instead.
    """
    _page(context).empl_stage.click()  # :86
    wait_title_is("Employees - Odoo", 3)  # :87
    assert context.driver.title == "Employees - Odoo"  # :88


# ---------------------------------------------------------------------------
# 11 - EmployeeStage.java:91-105.  The longest body in the class, and four of
#      the eight fixed delays.
# ---------------------------------------------------------------------------
@step("User edits created employees in the Employees module")
def user_edits_created_employees_in_the_employees_module(context) -> None:
    """Rename an existing employee record to "Sterling".

    :param context: behave's ``Context``; ``context.driver`` is the session.
    :returns: ``None``.

    Twelve statements, ``:93`` to ``:104``, and the third and last read of
    the ``url`` property.  The four delays fall between the sign-in and the
    stage click, between the stage click and the record selection, between
    the edit click and the field being cleared, and between the new name
    being typed and the record being saved.  Those four positions are the
    behaviour of the step, and the interleaving is why they are not lifted
    to the top or collapsed into one.

    ``:101`` and ``:102`` reach the name field twice, so it is located
    twice - once to clear it and once to type into it - which is what the
    Java's ``PageFactory`` proxy did on each invocation.  ``"Sterling"`` is
    carried over byte-exact, and nothing here asserts the rename took: the
    checking step is ``#12``, which does not check either.
    """
    page = _page(context)
    context.driver.get(get_url())  # :93
    page.login()  # :94
    sleep(3)  # :95 - Thread.sleep(3000)
    page.empl_stage.click()  # :96
    sleep(3)  # :97 - Thread.sleep(3000)
    page.choose_employee.click()  # :98
    page.edit_employee.click()  # :99
    sleep(3)  # :100 - Thread.sleep(3000)
    page.name_edit.clear()  # :101
    page.name_edit.send_keys("Sterling")  # :102
    sleep(3)  # :103 - Thread.sleep(3000)
    page.saved_message.click()  # :104


# ---------------------------------------------------------------------------
# 12 - EmployeeStage.java:107-110.  A single click, and no assertion.
# ---------------------------------------------------------------------------
@step("User should see the edited name in the Employees module")
def user_should_see_the_edited_name_in_the_employees_module(context) -> None:
    """Reopen the Employees stage, and check nothing.

    :param context: behave's ``Context``; ``context.driver`` is the session.
    :returns: ``None``.

    One statement, ``:109``, and it is the whole body.  The phrase says the
    user "should see the edited name" but the source asserts nothing - it
    neither reads the title nor looks for the name - so the step can only
    fail if the click itself fails.  No check is added: inventing one would
    add a way for the scenario to fail that the source does not have, which
    is the functional addition AAP 0.1.2 forbids.
    """
    _page(context).empl_stage.click()  # :109
