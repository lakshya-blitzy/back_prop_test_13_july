"""Step definitions for the logout flow - the port of ``LogOutSD.java``.

Anchor
------
``src/main/java/com/testinium/step_definitions/LogOutSD.java`` at pinned
revision ``47e9d697e4a9a85da889f94a846fdf47af28a240``, held REFERENCE by AAP
0.2.1 and never modified.  Thirty-five lines, three step definitions, no
parameters, and the paired page object ``LogOutP.java`` - ported to
:class:`app.pages.LogOutPage`.  That Java class *is* the specification of every
body below, per the per-module parity obligation of AAP 0.4.1, which has
``tests/test_steps_logout.py`` enumerate its methods so an omission fails
rather than passes silently.

=  ==========  ==============================================  ===============
#  Java        Phrase                                          Java body
=  ==========  ==============================================  ===============
1  ``:16``     ``User click Log out option``                   ``:18`` - ``:20``
2  ``:23``     ``User should see the login dashboard``         ``:25`` - ``:27``
3  ``:30``     ``User can not click the step back button       ``:32`` - ``:33``
               to go the home page``
=  ==========  ==============================================  ===============

The Java class declares two fields ahead of them - a ``WebDriverWait`` on a
**3-second** timeout (``:13``) and a ``LogOutP`` (``:15``), in that order, the
reverse of ``LoginSD.java``'s.  Both are rebuilt per scenario here rather than
once per module; see *Per-scenario binding* below.

Why every definition registers with ``@step``
---------------------------------------------
This is behavioural, not stylistic - AAP 0.5.2 and deviation 7 of AAP 0.1.3 -
and this module is the clearest case of it in the port.  Cucumber-JVM matches a
step by its **text alone**, so ``Given``, ``When``, ``Then`` and ``And`` are
interchangeable at match time.  behave instead resolves by *effective* step
type, where ``And`` inherits the keyword of the step above it.  All three
definitions below are declared with Cucumber's ``Then`` annotation in Java,
yet ``features/Logout.feature`` reaches two of them under an effective
``When``:

* ``Logout.feature:19`` and ``Logout.feature:43`` invoke definition 1 as
  ``And User click Log out option``, where the ``And`` inherits the ``When``
  opened at ``Logout.feature:15`` and ``Logout.feature:39``.
* ``Logout.feature:44`` invokes definition 2 as an ``And`` - effective ``When``
  - while ``Logout.feature:20`` invokes the *same phrase* as a real ``Then``.
  One phrase, two effective types, one feature file.

Registered against the ``Then`` keyword alone, lines 19, 43 and 44 would every
one of them report an **undefined step**, and the scenario at ``@UPGN-292``
would never reach its final assertion.  :func:`behave.step` registers a
definition for every keyword and therefore reproduces Cucumber's text-only
matching exactly, which is why the three keyword-specific decorators are
neither imported nor used here - a rule
``tests/test_steps_registration.py`` asserts for every module in this folder.

Import boundary (AAP 0.4.2)
---------------------------
The three imports below are the complete and closed list.  No browser-library
module is named here and none may be: ``app.automation`` is the only package in
the port permitted to hold one, and the same registration test greps this
folder for it.  Nothing else is imported either, and each omission is a fact
about ``LogOutSD.java`` rather than a preference:

* **No** ``By``, ``Keys``, ``Actions``, ``WebDriverWait`` or
  ``ExpectedConditions`` equivalent - the Java class builds no locator inline
  (only ``LoginSD.java:56`` does, which is why only ``login_steps`` needs the
  locator factory) and uses neither ``Keys`` nor ``Actions``, so neither the
  keyboard helper nor the action-chain helper is imported.
* **Only** :func:`~app.automation.wait_visible_element` from the wait family.
  ``LogOutSD.java:18`` waits on ``ExpectedConditions.visibilityOf`` applied to
  a ``WebElement``, and that helper is the one wrapper over
  ``expected_conditions.visibility_of``; the locator-shaped helper beside it
  resolves a different predicate.
* **Nothing from the configuration accessors** - this class reads no property,
  so the six-key configuration surface of AAP 0.4.1 is untouched here.
* **No driver-lifecycle call of any kind**, neither creation nor teardown.
  AAP 0.3.3 gives the session lifecycle to ``features/environment.py`` alone -
  *"no step or page ever creates or quits a driver"* - and that matters more
  here than anywhere else in the port, because the feature under test is
  *about* logging out: ``after_scenario`` already ends the session
  unconditionally after every scenario, so a teardown, reset or re-open in a
  step body would be both a boundary violation and a double quit.
* **No login page object.**  ``features/Logout.feature`` does use
  login steps - its Background ``Given User is on the upgenix login page``
  (``:10``) and the username, password, login-button and dashboard phrases at
  ``:15`` - ``:18`` and ``:39`` - ``:42`` - but those four phrases and the
  Background belong to ``features/steps/login_steps.py``.  Two modules serve
  this one feature; only the three phrases below are this module's, and a
  phrase of that feature going undefined is a question for ``login_steps``,
  never a reason to add a definition here.

Per-scenario binding
--------------------
``LogOutSD.java:13,15`` builds the wait and the page object as **fields, at
glue construction**.  Neither is reproduced at module level: under the
process-pool runner of AAP 0.4.1 a module-level page object or driver reference
would bind whichever worker process imported this module first and then hand
its locators to a session belonging to another scenario, or to a dead one.  So

* the page object is built inside each body that needs one, from
  ``context.driver`` - the session ``before_scenario`` publishes - through the
  module-private :func:`_page`, whose construction touches neither the DOM nor
  the driver, and
* the wait is **a timeout argument rather than an object**, so the 3 seconds of
  ``LogOutSD.java:13`` stay legible at the call site that uses them.  AAP 0.4.1
  fixes a different timeout per step class - 2s, 3s, 4s and 20s across the nine
  ``WebDriverWait`` sites - and a module constant or a helper default would hide
  which one this class chose.

The one observable difference from the Java field is when the driver is
resolved, and it resolves *later* rather than differently: a fresh page object
per scenario reads ``context.driver`` for that scenario, where the Java field
captured whatever ``Driver.getDriver()`` returned when the glue was
constructed.  Both then re-resolve every element on every access - the Java
``PageFactory`` proxy by design, :class:`app.pages.base_page.BasePage` by
never caching - so no step below ever operates on a stale element.

Translation notes
-----------------
* ``Driver.getDriver().getTitle()`` (``:26``) becomes the ``title``
  **property**, and ``Driver.getDriver().navigate().back()`` (``:32``) becomes
  ``context.driver.back()``: the Python binding has no ``navigate()``
  intermediary.  That call is the only navigation-history operation in the
  whole port.
* ``Assert.assertEquals(message, expected, actual)`` and
  ``Assert.assertTrue(condition)`` become plain ``assert`` statements carrying
  the same message text, per AAP 0.5.2.  Deviation 16 of AAP 0.1.3 covers what
  cannot carry over: Python produces neither JUnit's ``expected:<...> but
  was:<...>`` rendering nor a Java stack trace.  The assertion *subject* and
  *message string* are parity; their formatting is not - so definition 2 keeps
  its message byte-for-byte, **trailing space included**, and definition 3
  carries no message at all, because ``:33`` passes none.
* No fixed delay, no console output and no exception handling appears here,
  because ``LogOutSD.java`` has none.  A wait expiry, a missing element or a dead
  session propagates out of the body uncaught and fails the step, exactly as
  the Java original fails it; the after-scenario hook then captures the
  screenshot.
* The Testinium, Upgenix and Odoo vocabularies are left exactly as the sources
  spell them - the feature title says Testinium, its Background says upgenix
  and the page under test is Odoo.  Conflict 8 of AAP 0.1.3 forbids reconciling
  the three.

The class name :class:`~app.pages.LogOutPage` keeps the internal capital ``O``
of the Java ``LogOutP`` while this module is all-lowercase ``logout_steps.py``.
Both spellings are the AAP's; neither is to be normalized.

Return types are annotated below, as they are throughout the port, but the
``context`` parameters are not: the closed import list admits no ``typing``
import, and behave's ``Context`` is not a name this module may reach for.
Each is documented in its docstring instead.  Importing this module has no
side effect beyond registering the three definitions: it starts no browser,
reads no configuration and touches no filesystem.
"""

from behave import step

from app.automation import wait_visible_element
from app.pages import LogOutPage

# The three definitions, and nothing else. `_page` is deliberately private:
# it is this module's replacement for a Java field, not part of any contract.
__all__ = [
    "user_can_not_click_the_step_back_button_to_go_the_home_page",
    "user_click_log_out_option",
    "user_should_see_the_login_dashboard",
]


def _page(context) -> LogOutPage:
    """Build a page object bound to this scenario's session.

    :param context: behave's ``Context``.  Only ``context.driver`` is read -
        the session ``features/environment.py`` publishes in
        ``before_scenario``.
    :returns: A fresh :class:`~app.pages.LogOutPage` for the current scenario.

    The port of the ``LogOutP logOutP = new LogOutP();`` field at
    ``LogOutSD.java:15``, called per body rather than held per module for the
    reason the module docstring gives.  Construction is free of side effects -
    :class:`app.pages.base_page.BasePage` stores the driver and does nothing
    else - so this locates no element, starts no session and is safe to call
    before the browser has reached the page.  The driver is passed explicitly
    rather than left to default, so a step never reaches the lifecycle owner
    on its own behalf.
    """
    return LogOutPage(context.driver)


@step("User click Log out option")
def user_click_log_out_option(context) -> None:
    """Open the account menu and click ``Log out``.

    :param context: behave's ``Context``; supplies this scenario's session.
    :returns: ``None``.
    :raises Exception: Whatever the wait or either click raises - a wait expiry
        if the account menu is still not visible after 3 seconds above all.
        Nothing is caught, so the step fails exactly as the Java original
        fails.

    Ports ``LogOutSD.java:18-20``, three statements in this order:

    .. code-block:: java

        wait.until(ExpectedConditions.visibilityOf(logOutP.popUpButton));  // :18
        logOutP.popUpButton.click();                                       // :19
        logOutP.logOutButton.click();                                      // :20

    The **3** is the timeout of the ``WebDriverWait`` field at
    ``LogOutSD.java:13``, written at the call site because that is where the
    per-class timeout belongs.

    Two details are parity rather than oversight.  The wait covers **only** the
    account menu: ``:20`` clicks the log-out link with no wait of its own, so
    none is added here.  And the Java method is named
    ``user_clicks_the_account_icon_and_then_click_log_out_option`` while the
    text of its annotation is ``User click Log out option`` - the *phrase* is
    the contract and is reproduced byte-exactly, so this function is named
    after the phrase and the mismatch is not carried over.

    Each accessor below re-resolves its element against the live DOM, so the
    menu is located once for the wait and again for the click, mirroring the
    ``PageFactory`` proxy the Java field held, which re-resolves on every
    method call.
    """
    page = _page(context)

    wait_visible_element(page.pop_up_button, 3)
    page.pop_up_button.click()
    page.log_out_button.click()


@step("User should see the login dashboard")
def user_should_see_the_login_dashboard(context) -> None:
    """Assert the browser is back on the login page, by page title.

    :param context: behave's ``Context``; ``context.driver`` supplies the
        title.
    :returns: ``None``.
    :raises AssertionError: If the title is anything other than the expected
        one, carrying ``LogOutSD.java:27``'s message text.

    Ports ``LogOutSD.java:25-27``:

    .. code-block:: java

        String expectedDashboard = "Login | Best solution for startups";     // :25
        String actualDashboard = Driver.getDriver().getTitle();              // :26
        Assert.assertEquals("The title is not same as the expected! ",       // :27
                            expectedDashboard, actualDashboard);

    Both literals are byte-exact and neither is to be tidied: the expected
    title contains a literal ``|``, and the assertion message ends in a
    **trailing space**.  That space is in the source - ``LoginSD.java:46``
    shares it, while ``Calendar.java:46`` and ``Sales.java:37`` do not - and
    AAP 0.8's *"preserve, do not tidy"* keeps it, since the message text is
    part of the parity contract.

    This body touches no field of the page object, exactly as the Java method
    does not, so no page object is built here.  ``getTitle()`` becomes the
    ``title`` property, not a call.
    """
    expected_dashboard = "Login | Best solution for startups"
    actual_dashboard = context.driver.title

    assert expected_dashboard == actual_dashboard, (
        "The title is not same as the expected! "
    )


@step("User can not click the step back button to go the home page")
def user_can_not_click_the_step_back_button_to_go_the_home_page(
    context,
) -> None:
    """Navigate back after logging out and assert the warning modal is shown.

    :param context: behave's ``Context``; supplies this scenario's session.
    :returns: ``None``.
    :raises AssertionError: If the warning modal is present but not displayed.
    :raises Exception: Whatever the lookup raises when the modal is absent once
        the session's 10-second implicit wait expires - uncaught, as in Java.

    Ports ``LogOutSD.java:32-33``:

    .. code-block:: java

        Driver.getDriver().navigate().back();                    // :32
        Assert.assertTrue(logOutP.warningMess.isDisplayed());    // :33

    The order is the assertion's premise: the back navigation happens first,
    and the modal is located only afterwards, so the element is resolved
    against the page the navigation produced.  ``navigate().back()`` collapses
    to ``back()``, the Python binding having no ``navigate()`` intermediary.

    ``:33`` passes no message to ``assertTrue``, so this ``assert`` carries
    none either - inventing one would put text in the report and in
    ``target/cucumber.json``'s ``error_message`` that the source never emits.
    """
    context.driver.back()

    page = _page(context)
    assert page.warning_mess.is_displayed()
