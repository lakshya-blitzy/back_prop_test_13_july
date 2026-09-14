"""Login and form-validation steps - the port of ``LoginSD.java``.

behave discovers every module in this directory, ``features/steps/``, because
``behave.ini`` names ``features`` as its ``paths`` root; that is why this file
lives here and not under ``app``.  It carries one of the eleven Java
step-definition classes -- ``LoginSD`` -- at the pinned reference revision
``47e9d697e4a9a85da889f94a846fdf47af28a240``, which AAP 0.2.1 holds REFERENCE
and never modifies, and which is therefore the specification of every body
below.

The nine definitions
--------------------
One function per Java method, in the source's own order, each named for the
method it ports so that a failure names the same thing in both languages and
so the ``match.location`` the JSON writer records reads as a dotted Python
path onto the same step (AAP 0.1.3, deviation 8):

=================================================  =============  ===========
Phrase                                             Java           Body
=================================================  =============  ===========
``User is on the upgenix login page``              ``:19`` Given  ``:22-23``
``User enters "{username}" username``              ``:26`` When   ``:28``
``User enters "{password}" password``              ``:31`` When   ``:33``
``User clicks the login button``                   ``:36`` When   ``:38``
``User should see the dashboard``                  ``:41`` Then   ``:43-46``
``User sees error message``                        ``:49`` Then   ``:51``
``User sees "{alert_message}" message``            ``:54`` Then   ``:56-57``
``User should see the password in bullet signs``   ``:60`` Then   ``:62``
``User clicks the enter button``                   ``:65`` When   ``:67``
=================================================  =============  ===========

``features/Login.feature`` drives five outlines against them -- ``@UPGN-286``
valid login, ``@UPGN-287`` invalid credentials, ``@UPGN-288`` empty field,
``@UPGN-289`` password bullets and ``@UPGN-290`` the Enter key -- and
``features/Logout.feature`` resolves four of these phrases plus its whole
Background here, and ``features/Inventory.feature:16`` resolves the dashboard
phrase here too -- ``LoginSD`` is its only definition anywhere in the eleven
Java classes -- so a wrong phrase breaks three features rather than one.

Why every definition registers with ``@step``
---------------------------------------------
This is behavioural, not stylistic: AAP 0.5.2 deviation 7.  Cucumber-JVM
matches a step by its **text alone**, so ``Given``, ``When``, ``Then`` and
``And`` are interchangeable at match time; behave resolves by the step's
**effective** type, so a definition registered under one type does not match a
use under another.  This module is the suite's only user of Java's ``Given``
annotation, and also where the difference bites hardest:

* ``User should see the dashboard`` is declared with Java's ``Then`` annotation
  at ``LoginSD.java:41``, but ``Logout.feature:18`` and ``:42`` reach it as
  ``And User should see the dashboard``, where the ``And`` inherits the
  ``When`` opened at
  ``Logout.feature:15``/``:39``.  Its effective type there is ``When``, so
  registered under a ``Then``-only decorator both uses would go **undefined**.
* The same inheritance makes ``User enters "{password}" password`` and ``User
  clicks the login button`` effective-``When`` at ``Logout.feature:16``-``:17``
  and ``:40``-``:41`` -- consistent with their ``When`` declaration, but only
  by luck.

``@step`` matches regardless of the invoking keyword and so reproduces
text-only matching exactly.  Nothing in the source can rely on keyword
disambiguation, because Cucumber-JVM does not offer it.  The engine's three
keyword-specific decorators are therefore never imported or used here, not
even for the class's one Java ``Given`` annotation, and
``tests/test_steps_registration.py`` asserts that about every module in this
directory.

Import boundary (AAP 0.4.2)
---------------------------
Four imports, and the list is closed: the engine's ``step`` decorator, the two
names this class needs from ``app.automation``, the one configuration accessor
its ``:22`` reads, and the page object.  In particular:

* **No browser-library import of any kind.**  ``app.automation`` is the only
  package in the port permitted one, and it re-exports
  :data:`~app.automation.By` precisely so that this module can port
  ``LoginSD.java:56`` -- which builds a locator inline rather than using a page
  field -- without naming that library.  This is the only step module in the
  port that imports ``By``, and it uses it at that one site; every other
  element access goes through :class:`~app.pages.login_page.LoginPage`.
* **No driver-lifecycle helper.**  Neither of the two that ``app.automation``
  exports for creating and disposing of a session appears here, because
  ``features/environment.py`` owns the scenario lifecycle exclusively -- AAP
  0.3.3: *"no step or page ever creates or quits a driver"* -- and publishes
  the session as ``context.driver``, which is what every function below reads.
* **No keyboard or action-chain helper.**  ``LoginSD.java`` imports neither
  ``Keys`` nor ``Actions``; see the note on the Enter-key step below.
* **One wait shape only.**  ``LoginSD.java:17``'s ``WebDriverWait`` is used
  once, on ``ExpectedConditions.visibilityOf`` applied to a ``WebElement``
  (``:43``), so :func:`~app.automation.waits.wait_visible_element` is the only
  wait imported.
* **No ``app.utils.properties``.**  ``app/config.py`` is its only permitted
  consumer.

Nothing binds at import time
----------------------------
``LoginSD.java:16-17`` builds the page object and the wait as **fields**, at
glue construction.  Neither may become module-level state here: a module-level
page object or driver reference would bind whichever worker process imported
this module first, and every worker shares one process pool but not one
browser session.  So the page object is constructed inside each function from
``context.driver``, through the module-private :func:`_page` helper, and the
wait has no object at all -- ``wait_visible_element`` takes its timeout as a
required argument, so this class's ``3`` stays visible at its call site rather
than hiding in a default.  There is no module-level page instance, driver
reference, wait object, cached URL or logger.

Three quirks below are parity, not mistakes
-------------------------------------------
Each would look like a defect to tidy up, and tidying any of them would break
the behaviour this port exists to preserve:

1. The dashboard assertion's message ends in a **space**:
   ``The title is not same as the expected!`` followed by one blank character,
   exactly as ``LoginSD.java:46`` writes it.  ``LogOutSD.java:27`` shares that
   trailing space; ``Calendar.java:46`` and ``Sales.java:37`` do not.
2. The validation-message step **inverts the expected/actual roles**.  The live
   value read out of the DOM is stored in a local named ``expectedMessage`` and
   passed as ``assertEquals``'s *expected* argument, while the Gherkin
   parameter is the *actual* (``LoginSD.java:56-57``).  It is the two-argument
   form, so it carries no message string.  The local keeps its Java name so
   the inversion stays visible.
3. The Enter-key step **does not press Enter**.  Despite the phrase ``User
   clicks the enter button``, ``LoginSD.java:67`` is ``loginP.button.click()``
   -- byte-identical to the login-button step.  The two stay separate
   functions because they are two separately registered phrases.

What this module deliberately does not do
-----------------------------------------
No fixed delay: ``LoginSD.java`` calls no ``Thread.sleep``, unlike the five
classes that hold the suite's seventeen sleeps.  No print, unlike ``Crm.java``
and ``Sales.java``.  No exception handling -- a ``TimeoutException`` from the
wait, a ``NoSuchElementException`` from a lookup and an ``AttributeError`` from
a session that was never created all propagate uncaught, which is what the
Java code does and what lets the failure surface in the step that caused it.
No guard of this module's own on the configuration accessor either, and none is
needed: :func:`app.config.get_web_table_url` returns ``None`` when the key or
the whole ``configuration.properties`` file is absent -- tolerated by design
(AAP 0.6) -- and that ``None`` is passed straight to ``driver.get`` so it fails
there, exactly as today, while a ``web.table.url`` that is *set* to a
destination outside that accessor's navigation policy raises
:class:`ValueError` inside the accessor, before this module has a value to
navigate to.  And no locale key: the
French text at ``Login.feature:89`` is the browser's own required-field
message, whose language follows the browser locale, which AAP 0.6 leaves
explicitly unresolved; the string is carried through byte-for-byte and the
configuration surface stays at exactly six keys.

The Odoo, Testinium and Upgenix vocabularies all appear in this one file --
``upgenix`` in the first phrase, ``Odoo`` as the expected page title, and
*"Testinium app login feature"* as the title of the feature that drives it.
AAP Conflict 8 forbids reconciling them: *"no renaming. All three vocabularies
are preserved exactly where they occur."*
"""

from behave import step

from app.automation import By, wait_visible_element
from app.config import get_web_table_url
from app.pages import LoginPage

__all__ = [
    "user_is_on_the_upgenix_login_page",
    "user_enters_username",
    "user_enters_password",
    "user_clicks_the_login_button",
    "user_should_see_the_dashboard",
    "user_sees_error_message",
    "user_sees_please_fill_out_this_field_message",
    "user_should_see_the_password_in_bullet_signs",
    "user_clicks_the_enter_button",
]


def _page(context):
    return LoginPage(context.driver)


@step("User is on the upgenix login page")
def user_is_on_the_upgenix_login_page(context) -> None:
    """Navigate to the configured login URL -- ``LoginSD.java:19-24``.

    :param context: behave's ``Context``; ``context.driver`` is the session.
    :returns: ``None``.

    The Background step of both ``features/Login.feature`` (``:10``) and
    ``features/Logout.feature`` (``:10``), so it runs once per scenario of
    both features.

    ``:22`` reads the ``web.table.url`` property into a local and ``:23``
    navigates to it; the local is kept because the Java code has one.  The
    value may legitimately be ``None`` -- see the module docstring -- and that
    ``None`` reaches ``driver.get`` with nothing of this module's own in its
    way.  A value the accessor's navigation policy rejects never becomes this
    local at all: the :class:`ValueError` is raised on the read at ``:22``, so
    this step fails with the browser still where the previous step left it.

    ``LoginSD.java:21`` holds a commented-out expected title,
    ``//String expectedTitle = "Login | Best solution for startups";``.  It has
    no behaviour, so it is not ported as code; the only title this class
    asserts is the one at ``:44``.

    The phrase says ``upgenix`` where the local ``README.md`` excerpt says
    *"Testinium login page"*.  The implementation governs (AAP Conflict 2), and
    the feature files carry the ``upgenix`` wording.
    """
    url = get_web_table_url()
    context.driver.get(url)


@step('User enters "{username}" username')
def user_enters_username(context, username: str) -> None:
    _page(context).input_email.send_keys(username)


@step('User enters "{password}" password')
def user_enters_password(context, password: str) -> None:
    _page(context).input_password.send_keys(password)


@step("User clicks the login button")
def user_clicks_the_login_button(context) -> None:
    _page(context).button.click()


@step("User should see the dashboard")
def user_should_see_the_dashboard(context) -> None:
    """Wait for the dashboard, then assert the page title - ``:41-47``.

    The plain ``assert`` stands in for Java's three-argument ``assertEquals``
    (AAP 0.5.2, deviation 16) and keeps its message byte-exact from ``:46``,
    the single trailing space included.  Registration is ``@step`` because
    ``Logout.feature:18`` and ``:42`` reach this ``@Then``-declared phrase as
    an effective ``When`` (deviation 7).
    """
    wait_visible_element(_page(context).DASHBOARD, 3)
    expected_dashboard = "Odoo"
    actual_dashboard = context.driver.title
    assert (
        expected_dashboard == actual_dashboard
    ), "The title is not same as the expected! "


@step("User sees error message")
def user_sees_error_message(context) -> None:
    assert _page(context).alert_error_message.is_displayed()


@step('User sees "{alert_message}" message')
def user_sees_please_fill_out_this_field_message(context, alert_message: str) -> None:
    """Compare the browser's required-field message - ``:54-58``.

    The locale this message arrives in is decided nowhere in either repository
    - AAP 0.6 leaves it unresolved - so the step passes under a French-locale
    browser, against ``Veuillez renseigner ce champ.`` (``Login.feature:89``),
    and fails otherwise.  ``:56`` builds its locator inline, this module's one
    use of ``By``, and names the live DOM value ``expected_message`` while the
    Gherkin value is the actual - the source's inversion, kept visible.
    """
    expected_message = context.driver.find_element(
        By.NAME, "login"
    ).get_attribute("validationMessage")
    assert expected_message == alert_message


@step("User should see the password in bullet signs")
def user_should_see_the_password_in_bullet_signs(context) -> None:
    assert _page(context).bullet_pass.get_attribute("type") == "password"


@step("User clicks the enter button")
def user_clicks_the_enter_button(context) -> None:
    """Submit the login form again, by clicking - ``LoginSD.java:65-68``.

    **No key is pressed.**  Despite the phrase and ``@UPGN-290``'s intent
    (``Login.feature:121``), ``:67`` is ``loginP.button.click()``, and
    ``LoginSD.java`` imports neither ``Keys`` nor ``Actions``; reproducing the
    intent instead of the body would change what the scenario exercises.  The
    step stays separate from the login-button step because it is a separately
    registered phrase with its own ``match.location``.
    """
    _page(context).button.click()
