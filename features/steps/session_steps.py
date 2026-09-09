"""Step module for the suite's shared login precondition: port of ``Session.java``.

The Python counterpart of
``src/main/java/com/testinium/step_definitions/Session.java`` at pinned
revision ``47e9d697e4a9a85da889f94a846fdf47af28a240``, held REFERENCE by AAP
0.2.1 and never modified.  That class is the smallest in the reference suite -
19 lines, one field, one step definition - and this module is deliberately just
as small.

The Java anchor, verbatim (``Session.java:10-18``)
--------------------------------------------------
.. code-block:: java

    SessionP session = new SessionP();

    @When("User login to test other features")
    public void user_login_to_test_other_features() {
        Driver.getDriver().get(ConfigurationReader.getProperty("web.table.url"));
        session.inputLogin.sendKeys(ConfigurationReader.getProperty("username"));
        session.inputPass.sendKeys(ConfigurationReader.getProperty("password"));
        session.loginButton.click();
    }

Four operations, in that order, and nothing else: no explicit wait (this is the
one step class of the ten that constructs none), no fixed delay (none of the
suite's seventeen ``Thread.sleep`` calls is here), no assertion (the class
declares no ``Assert`` import), no diagnostic output (only ``Crm.java`` and
``Sales.java`` print) and no exception handling.  Every one of those absences is
reproduced below, because under AAP 0.8's *"Preserve, do not tidy"* an addition
here is a parity break rather than an improvement.

Why this module gates most of the suite
---------------------------------------
Its single phrase is the shared precondition six other features invoke from
their ``Background``, so AAP 0.4.4 cites it as the reason the port cannot be
split into phases: *"the shared precondition step in ``Session.java`` is invoked
by other features' backgrounds, so porting any one feature area alone yields
undefined steps."*  The seven call sites, as measured in ``features/``:

=====================================  =========
Call site                              Keyword
=====================================  =========
``features/Calendar.feature:9``        ``Given``
``features/Contact.feature:5``         ``Given``
``features/Crm.feature:7``             ``Given``
``features/Inventory.feature:9``       ``Given``
``features/Notes.feature:8``           ``Given``
``features/Sales.feature:10``          ``Given``
``features/Session.feature:4``         ``When``
=====================================  =========

``features/EmployeeFc.feature`` is the deliberate exception: its ``Background``
declares no steps and its flow signs in through ``EmployeePage.login()``
instead (``EmployeeP.java:59-69``), so this phrase does not belong to it.

``@step`` is behavioural here, not stylistic
--------------------------------------------
Cucumber-JVM matches a step by its text alone - ``Given``, ``When``, ``Then``
and ``And`` are interchangeable at match time - while the Python engine
resolves by *effective* step type, so a definition registered under one type
does not match a use under another.  This module is the proof case for AAP
0.5.2's ruling (deviation 7 in AAP 0.1.3) that **every** step definition in the
port registers with ``@step``: the phrase is declared ``@When`` at
``Session.java:12`` yet invoked with the ``Given`` keyword at six of its seven
call sites above.  Registered under one specific step type, those six call
sites would report an undefined step immediately.  The committed baseline
corroborates the JVM's cross-type match directly - the Crm ``Background`` step
records ``"keyword": "Given "`` while its ``"match"`` object points its
``"location"`` at
``com.testinium.step_definitions.Session.user_login_to_test_other_features()``
in ``target/cucumber.json`` at the pinned revision.

Accordingly the type-agnostic decorator is the only one this module imports or
applies - none of the three step-type-specific decorators appears anywhere
below - and ``tests/test_steps_registration.py`` asserts that across every
module in this folder.

Import boundary (AAP 0.4.2)
---------------------------
Three imports, and the list is closed: the engine's type-agnostic step
decorator, the three configuration accessors this step's Java original reads
through ``ConfigurationReader``, and the one page class its Java original
declares as a field.  What is *not* imported is as load-bearing as what is:

* **No browser-automation library.**  ``app.automation`` is the only package
  permitted to import it, and ``tests/test_steps_registration.py`` asserts no
  module under ``features/steps/`` does.
* **Nothing from** ``app.automation`` **at all** - no driver accessor, no wait
  helper, no ``By``, no key or action-chain helper.  ``Session.java``
  constructs no wait and uses no ``Keys``, ``Actions`` or ``By``, so there is
  nothing to reach for; and per AAP 0.3.3 *"no step or page ever creates or
  quits a driver"*, because ``features/environment.py`` owns the scenario
  lifecycle exclusively.
* **No properties module.**  ``app/config.py`` is the only module permitted to
  read it, and it is reached here solely through the three named accessors.
* **No** ``BasePage``.  ``SessionPage`` arrives from the ``app.pages`` barrel,
  which is the surface every step module is written against.

The ``context`` parameters below carry no annotation for the same reason: the
engine's ``Context`` type and ``typing.Any`` would each add a fourth import to
a list this file's specification closes at three.

Binding the page object per scenario
------------------------------------
``Session.java:10`` builds its page object as a field at glue construction.  A
module-level equivalent in Python would bind whichever worker process imported
the module first, which is wrong under the process-pool runner of AAP 0.6, so
the page is constructed inside the step from ``context.driver`` - the session
``features/environment.py``'s ``before_scenario`` publishes.  That is cheap and
side-effect-free: ``BasePage.__init__`` stores its ``driver`` argument and does
nothing else, touching neither the DOM nor the session.  The observable
behaviour is unchanged - a page object bound to the current driver, with every
element re-resolved on access exactly as the ``PageFactory`` proxy resolved it.

Consequently this module holds **no** module-level state: no page instance, no
driver reference, no cached configuration value, and no import-time side
effects whatever.
"""

from behave import step

from app.config import get_password, get_username, get_web_table_url
from app.pages import SessionPage


def _page(context) -> SessionPage:
    """Bind a fresh :class:`~app.pages.session_page.SessionPage` to this scenario.

    :param context: The engine's ``Context`` for the running scenario, whose
        ``driver`` attribute is the session ``before_scenario`` opened.
    :returns: A page object whose three locators resolve against that session.

    The stand-in for ``Session.java:10``'s ``SessionP session = new SessionP()``
    field.  Construction is free: it stores the driver and nothing more, so
    calling this before navigation - as the step below does - is safe, and no
    element is located until an accessor is read.

    ``context.driver`` is read on every call rather than remembered, because
    ``after_scenario`` quits the session and clears the attribute after each
    scenario; a page object that had outlived its scenario would hand its
    locators to a dead session.
    """
    return SessionPage(context.driver)


@step("User login to test other features")
def user_login_to_test_other_features(context) -> None:
    """Sign in to the application under test, as the shared precondition.

    :param context: The engine's ``Context`` for the running scenario.
    :returns: ``None``.

    The port of ``Session.java:13-18``, operation for operation and in order:

    1. navigate to ``web.table.url`` (``Session.java:14``),
    2. type ``username`` into the login input (``:15``),
    3. type ``password`` into the password input (``:16``),
    4. click the submit button (``:17``).

    Nothing precedes, separates or follows those four operations.

    The three configuration values are passed straight through to the browser.
    Each accessor returns ``None`` when its key is absent - the port of
    ``ConfigurationReader.getProperty`` returning ``null``, which AAP 0.4.1
    keeps deliberately so that *"a missing key returns null so failures surface
    at the point of use"* - so no check, default, fallback, validation or
    message of this module's own stands between a missing value and the browser
    call that rejects it.  Any failure, in configuration or in the DOM,
    propagates untouched to the engine, which records the scenario as failed and
    lets ``after_scenario`` capture the screenshot.
    """
    page = _page(context)

    # Session.java:14 - navigation goes through the session the scenario
    # already owns, never through a driver accessor of this module's own.
    context.driver.get(get_web_table_url())

    # Session.java:15-16 - the snake_case accessors yield a live element
    # re-resolved on each access; the UPPER_SNAKE constants they derive from are
    # locator tuples and have no send_keys. Order matters: user name first,
    # password second, exactly as the Java reads its two keys.
    page.input_login.send_keys(get_username())
    page.input_pass.send_keys(get_password())

    # Session.java:17 - submit, with no wait before or after it, because the
    # reference class constructs none.
    page.login_button.click()
