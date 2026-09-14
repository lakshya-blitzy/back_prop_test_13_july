"""behave step definitions for the Odoo Employees module - the port of
``com.testinium.step_definitions.EmployeeStage``, one function per Java
method, in source order, under the Java method's own name.

Registration is ``@step`` throughout, never a keyword decorator (AAP 0.5.2,
deviation 7): Cucumber-JVM matches a step by its text alone, behave by
effective step type.  ``Session.java:12`` declares the shared precondition
``@When`` while six Backgrounds invoke it as ``Given``, and three step classes
import ``io.cucumber.java.en.And``, for which behave has no decorator.

``EmployeeStage.java:13-14`` builds the page object and the wait as fields at
glue construction, and neither becomes module-level state here for reasons of
lifetime rather than sharing: each worker process imports this module into its
own address space, but an object built at import would predate the scenario's
session and outlive it, since ``features/environment.py`` opens a driver in
``before_scenario`` and quits it in ``after_scenario``.  So :func:`_page`
builds one per call, and each wait takes its timeout per call site.

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

Configuration: five of the six keys, read at eleven sites
---------------------------------------------------------
This is the port's heaviest configuration consumer, and every read goes
through ``app/config.py``:

======================  =================  ==============  ===============
Key                     Accessor           Java line(s)    Used by
======================  =================  ==============  ===============
``web.table.url``       get_web_table_url  :18             #1
``url``                 get_url            :24, :60, :93   #2, #7, #11
``EmplTitle``           get_empl_title     :31             #3
``username``            get_username       :25, :61, :94   #2, #7, #11
``password``            get_password       :25, :61, :94   #2, #7, #11
======================  =================  ==============  ===============

``url`` is read **three times, at three separate call sites**, because
``ConfigurationReader.getProperty`` is called afresh at each of those three
Java lines.  It is deliberately not hoisted into a module constant or a shared
local: hoisting would collapse three reads into one and change what a
mid-run edit of the properties file could affect.

``username`` and ``password`` are read the same way, once per ``login()`` call
site, and they are here rather than in the page object for two reasons that
point the same way.  ``EmployeeP.java:60-61`` typed two account literals into
the page; review finding SEC2-F17 (CWE-798/200) established that AAP 0.8's
test-data note sanctions those values in **the Gherkin Examples tables** and
nowhere else, so they cannot stay in Python source.  AAP 0.4.2's frozen rule
then decides where the replacement read lives: *"Page objects import
``app.automation`` for the current driver, nothing else"*, and the same section
already lists this module as an ``app.config`` consumer.  So the step reads the
two properties and passes them to ``EmployeePage.login(input_login,
input_pass)`` - the two-argument shape ``EmployeeP.java:65-69`` declares.  No
configuration key was added: both are among the six AAP 0.4.1 inventories and
``session_steps`` already reads them for the shared precondition.  The one
parity cost is stated where it happens: the Java overload discards its two
arguments and this port honours them, which is the smallest departure that
leaves the page object free of configuration.

**None is unguarded, and that is the behaviour.**  All five accessors return
``str | None``, and a key the properties file does not define reads as
``None`` - AAP 0.4.1: *"a missing key returns null so failures surface at the
point of use"*.  So this module adds no check, no default, no fallback, no
validation and no error message of its own: a ``None`` travels into
``driver.get(...)`` or ``wait_title_is(...)`` and fails there, which is
exactly where the Java fails.  Nor is any key added - the surface is six keys,
and AAP 0.6 in particular adds none for the browser's language or region.

**A destination that is set, however, is checked before it is returned.**  The
two URL accessors hold a present value to the navigation policy documented in
``app/config.py`` and raise :class:`ValueError` out of the accessor when it
falls outside it - a ``file:`` or ``data:`` URL, a host carrying user
information or a control character, the cloud instance-metadata address - so
the three navigations below are reached only with a destination the policy
permits, or with the ``None`` of an unset key.  The check therefore costs this
module nothing: no import, no branch and no statement of its own, and the
failure still lands in the step that reads the key.  It also runs three times
for ``url``, once per call site, for the same reason the three reads are not
hoisted: each navigation is judged by what the configuration holds when it
happens.  ``EmplTitle`` is not a destination and is returned unchecked.

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
one came from**, the three ``login()`` calls - each immediately after that
step's ``get(...)``, each passing the ``username`` and ``password`` properties
in that order - the locators (against ``EmployeePage``'s own
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
from app.config import (
    get_empl_title,
    get_password,
    get_url,
    get_username,
    get_web_table_url,
)
from app.pages import EmployeePage


def _page(context) -> EmployeePage:
    return EmployeePage(context.driver)


# EmployeeStage.java:16-20, the orphan: registered here, invoked by no feature
# file, and left uncalled because parity is with the class.
@step("User is on upgenix login page")
def user_is_on_upgenix_login_page(context) -> None:
    """Navigate to the sign-in page named by ``web.table.url``.

    :param context: behave's ``Context``; ``context.driver`` is the session.
    :returns: ``None``.

    Two statements, from ``EmployeeStage.java:18-19``.  The local variable is
    kept because the Java keeps one, and ``None`` reaches ``get()`` unguarded
    when the key is undefined; a ``web.table.url`` set outside the accessor's
    navigation policy raises out of the read instead, so the local is never
    bound and ``get()`` is never called.

    The phrase is byte-exact and says ``upgenix login page``, without the
    "the" that ``LoginSD.java:19``'s ``User is on the upgenix login page``
    carries.  The two are separate registrations and neither resolves the
    other's text; the near-duplication is the source's and is preserved.
    """
    url = get_web_table_url()
    context.driver.get(url)


@step("User is on the dashboard")
def user_is_on_the_dashboard(context) -> None:
    """Navigate to the Employee module and sign in.

    :param context: behave's ``Context``; ``context.driver`` is the session.
    :returns: ``None``.

    ``:24`` navigates to the ``url`` property - a different page from
    ``web.table.url`` - and ``:25`` signs in through the page object's own
    ``login()``.  The two credentials are read here, from the ``username`` and
    ``password`` properties, and handed to it: ``EmployeeP.java:60-61`` typed
    them as literals, review finding SEC2-F17 took them out of Python source,
    and AAP 0.4.2 keeps a page object free of configuration (module docstring).
    ``login()`` types what it is given and clicks the button; its three
    operations are not reimplemented here.

    The read of ``url`` is the first of the three, and the accessor holds a
    configured destination to its navigation policy before returning it: a
    value outside that policy raises here, before the navigation and therefore
    before the sign-in.
    """
    context.driver.get(get_url())  # :24
    # The two credentials are read HERE, not in the page object: AAP 0.4.2
    # gives page objects `app.automation` and nothing else, and names this
    # module among `app.config`'s consumers. They are read rather than typed as
    # literals because review finding SEC2-F17 (CWE-798/200) removed those two
    # account literals from `EmployeeP.java:60-61`'s port. Read at the call
    # site, never hoisted or cached, for the reason the module docstring gives.
    _page(context).login(get_username(), get_password())  # :25


# EmployeeStage.java:28-33 waits on the configured ``EmplTitle`` (:31), then
# asserts the hard-coded "Employees - Odoo" (:32).
@step("User clicks Employees stage")
def user_clicks_employees_stage(context) -> None:
    """Open the Employees stage and confirm the page title.

    The two title sources are the source's own inconsistency and are kept:
    unifying them would change which value governs the step.  The
    listed-employees step uses the literal for both (``:87``, ``:88``).
    """
    _page(context).empl_stage.click()
    wait_title_is(get_empl_title(), 3)
    assert context.driver.title == "Employees - Odoo"


@step("User clicks Challenges stage")
def user_clicks_challenges_stage(context) -> None:
    page = _page(context)
    page.badges_btn.click()
    wait_visible_element(page.BADGES_BTN, 3)
    page.challenges_btn.click()
    wait_visible_element(page.CHALLENGES_BTN, 3)
    page.goals_history_btn.click()
    wait_visible_element(page.GOALS_HISTORY_BTN, 3)


# EmployeeStage.java:45-49 carries the suite's only 7-second sleep, and it is
# this step's only synchronisation of any kind.
@step("User clicks Departments stage")
def user_clicks_departments_stage(context) -> None:
    """Open the Departments stage and wait out a fixed 7 seconds.

    The delay is not converted into an explicit wait, though the class has a
    wait available: AAP 0.4.1 holds that converting a fixed delay would change
    timing behaviour and could change outcomes.
    """
    _page(context).departments_btn.click()
    sleep(7)


@step("User should see the last stage title")
def user_should_see_the_last_stage_title(context) -> None:
    assert context.driver.title == "Departments - Odoo"


@step("User is on the employees dashboard")
def user_is_on_the_employees_dashboard(context) -> None:
    """Navigate to the Employee module, sign in, settle, open the stage.

    :param context: behave's ``Context``; ``context.driver`` is the session.
    :returns: ``None``.

    Four statements, ``:60`` to ``:63``, and the ``url`` property is read
    here for the second of its three times - so the navigation policy behind
    that accessor is applied a second time, to whatever the configuration
    holds now.  The fixed delay sits between the sign-in and the click, which
    is where the source puts it; moving it or replacing it with a wait on the
    stage link would change the timing this step depends on.

    Note the difference from ``#2``: the same navigation and sign-in, then a
    delay and a click.  The source declares both steps and this module keeps
    both, rather than expressing one in terms of the other.
    """
    page = _page(context)
    context.driver.get(get_url())  # :60
    page.login(get_username(), get_password())  # :61
    sleep(3)  # :62 - Thread.sleep(3000)
    page.empl_stage.click()  # :63


@step('User creates new employees "{name}" in the Employees stage')
def user_creates_new_employees_in_the_employees_stage(context, name: str) -> None:
    page = _page(context)
    sleep(3)
    page.create_btn.click()
    sleep(3)
    wait_title_is("New - Odoo", 3)
    page.employees_name.send_keys(name)
    page.saved_message.click()


@step("User should see the Employee created message under full profile")
def user_should_see_the_message_under_full_profile(context) -> None:
    assert _page(context).created_message.is_displayed()


@step("User should see listed employees in the Employees stage")
def user_should_see_listed_employees_in_the_employees_stage(context) -> None:
    _page(context).empl_stage.click()
    wait_title_is("Employees - Odoo", 3)
    assert context.driver.title == "Employees - Odoo"


@step("User edits created employees in the Employees module")
def user_edits_created_employees_in_the_employees_module(context) -> None:
    """Rename an existing employee record to "Sterling".

    :param context: behave's ``Context``; ``context.driver`` is the session.
    :returns: ``None``.

    Twelve statements, ``:93`` to ``:104``, and the third and last read of
    the ``url`` property - checked against the navigation policy like the
    other two, on the value the configuration holds at this read.  The four
    delays fall between the sign-in and the
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
    page.login(get_username(), get_password())  # :94
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


# EmployeeStage.java:107-110 is a single click and asserts nothing, so the step
# can only fail if that click fails.
@step("User should see the edited name in the Employees module")
def user_should_see_the_edited_name_in_the_employees_module(context) -> None:
    """Reopen the Employees stage, and check nothing.

    No check is added for the missing assertion: inventing one would add a way
    for the scenario to fail that the source does not have, which is the
    functional addition AAP 0.1.2 forbids.
    """
    _page(context).empl_stage.click()
