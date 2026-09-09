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
No guard on the configuration accessor either: :func:`app.config.get_web_table_url`
returns ``None`` when the key or the whole ``configuration.properties`` file is
absent -- tolerated by design (AAP 0.6) -- and that ``None`` is passed straight
to ``driver.get`` so it fails there, exactly as today.  And no locale key: the
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

# The nine step functions, in ``LoginSD.java`` declaration order rather than
# alphabetically, so this list and the module read as one sequence; the
# ordering rule is suppressed for that reason alone.  ``_page`` is private and
# so is absent.  Nothing imports this module -- behave loads it for its
# decorator side effects -- so the list is documentation of the surface the
# feature files reach, and the reason no tenth name may appear on it.
__all__ = [  # noqa: RUF022
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
    """Bind a fresh login page object to this scenario's session.

    :param context: behave's ``Context``.  Only ``context.driver`` is read --
        the session ``features/environment.py``'s ``before_scenario``
        published, which is the single owner of the lifecycle.
    :returns: A :class:`~app.pages.login_page.LoginPage` bound to that session.

    The stand-in for ``LoginSD.java:16``'s ``LoginP loginP = new LoginP()``
    field.  Constructing per call rather than per module is what stops a worker
    serving elements out of another worker's -- or a previous scenario's --
    browser: ``features/environment.py`` quits the session after every
    scenario, so a page object cached at import would hand its locators to a
    dead one.  It costs nothing, because
    :meth:`BasePage.__init__ <app.pages.base_page.BasePage.__init__>` stores
    the driver and does nothing else -- no element is located, no page is
    navigated to and no session is created or requested.

    ``context.driver`` is passed through exactly as it is, including when it is
    ``None``: ``Driver.java:29-42`` switches on the ``browser`` property with
    cases for Chrome and Firefox and no default branch, so an unrecognised
    value yields no session, and AAP 0.4.1 requires that to *"fail at first
    driver use, as today"* rather than be validated here.
    """
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
    value may legitimately be ``None`` -- see the module docstring -- and
    reaches ``driver.get`` unguarded.

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
    """Type the username into the login field -- ``LoginSD.java:26-29``.

    :param context: behave's ``Context``; ``context.driver`` is the session.
    :param username: The value the feature file supplies inside the quotes.
        Cucumber's ``{string}`` matches the surrounding quotes and hands the
        step the unquoted value, so the behave pattern carries the quotes
        literally around the named field; the feature text has real quotes,
        for instance ``Login.feature:15`` and ``Logout.feature:15``.
    :returns: ``None``.

    ``:28`` sends the keys to ``loginP.inputEmail`` --
    :attr:`LoginPage.INPUT_EMAIL <app.pages.login_page.LoginPage.INPUT_EMAIL>`,
    the control named ``login``.  The field is not cleared first and no value
    is trimmed or validated, because the source does neither: the
    ``@UPGN-288`` outline depends on the field being reachable in whatever
    state the previous step left it.

    The pattern's trailing literal ``username`` is what keeps it distinct from
    the password step's ``password``, so the two never compete for a step; it
    must not be widened.
    """
    _page(context).input_email.send_keys(username)


@step('User enters "{password}" password')
def user_enters_password(context, password: str) -> None:
    """Type the password into the password field -- ``LoginSD.java:31-34``.

    :param context: behave's ``Context``; ``context.driver`` is the session.
    :param password: The value the feature file supplies inside the quotes.
    :returns: ``None``.

    ``:33`` sends the keys to ``loginP.inputPassword`` --
    :attr:`LoginPage.INPUT_PASSWORD
    <app.pages.login_page.LoginPage.INPUT_PASSWORD>`, the control named
    ``password``.  :attr:`LoginPage.BULLET_PASS
    <app.pages.login_page.LoginPage.BULLET_PASS>` is that same locator under a
    second name, declared separately at ``LoginP.java:31-32`` and read by the
    masking step below.  Each is used where its Java original uses it and
    neither is collapsed into the other (AAP 0.2.2).

    Reached as an effective ``When`` from ``Logout.feature:16`` and ``:40``,
    where the ``And`` inherits the preceding ``When`` -- see the module
    docstring on ``@step``.
    """
    _page(context).input_password.send_keys(password)


@step("User clicks the login button")
def user_clicks_the_login_button(context) -> None:
    """Submit the login form -- ``LoginSD.java:36-39``.

    :param context: behave's ``Context``; ``context.driver`` is the session.
    :returns: ``None``.

    ``:38`` clicks ``loginP.button`` --
    :attr:`LoginPage.BUTTON <app.pages.login_page.LoginPage.BUTTON>`, matched
    by the XPath ``//button[.='Log in']``.  Nothing waits for the click to be
    clickable first and nothing waits for the page that follows: the source
    relies on the session's 10-second implicit wait (``Driver.java:44``) and,
    where a scenario needs the dashboard, on the explicit wait in the
    dashboard step.
    """
    _page(context).button.click()


@step("User should see the dashboard")
def user_should_see_the_dashboard(context) -> None:
    """Wait for the dashboard, then assert the page title -- ``:41-47``.

    :param context: behave's ``Context``; ``context.driver`` is the session.
    :returns: ``None``.
    :raises AssertionError: If the title is not ``"Odoo"``.  The message is
        ``The title is not same as the expected!`` followed by a single
        trailing space, byte-exact from ``LoginSD.java:46``.

    Three operations, in the source's order:

    1. ``:43`` waits for the visibility of ``loginP.dashboard`` --
       :attr:`LoginPage.DASHBOARD
       <app.pages.login_page.LoginPage.DASHBOARD>`, the Odoo element id
       ``oe_main_menu_navbar`` -- with the **3-second** timeout that
       ``LoginSD.java:17`` fixes for this class.  The timeout is passed as a
       literal at this one call site because the source sets a different one
       per step class (2s, 3s, 4s and 20s elsewhere), and a shared default
       would erase that difference.  The return value is discarded, as in
       Java, and a timeout propagates as itself.
    2. ``:44-45`` name the expected and actual titles in locals, and the
       actual is read from the session's ``title`` **property**, which is the
       Python spelling of ``Driver.getDriver().getTitle()``.
    3. ``:46`` asserts them equal.  Java's three-argument
       ``assertEquals(message, expected, actual)`` becomes a plain ``assert``
       carrying the same message text (AAP 0.5.2, deviation 16): Python cannot
       produce JUnit's ``expected:<...> but was:<...>`` framing, so the
       assertion's subject and message are the parity, not the formatting
       around them.

    Registered with ``@step`` rather than under a ``Then``-only decorator for
    the reason the module docstring measures: ``Logout.feature:18`` and ``:42``
    reach this phrase as an effective ``When``.
    """
    wait_visible_element(_page(context).dashboard, 3)
    expected_dashboard = "Odoo"
    actual_dashboard = context.driver.title
    assert (
        expected_dashboard == actual_dashboard
    ), "The title is not same as the expected! "


@step("User sees error message")
def user_sees_error_message(context) -> None:
    """Assert the failed-login banner is displayed -- ``LoginSD.java:49-52``.

    :param context: behave's ``Context``; ``context.driver`` is the session.
    :returns: ``None``.
    :raises AssertionError: If the banner is present but not displayed.  Java's
        ``assertTrue`` is given no message at ``:51``, so neither is this
        assertion.

    ``:51`` reads ``isDisplayed()`` off ``loginP.alertErrorMessage`` --
    :attr:`LoginPage.ALERT_ERROR_MESSAGE
    <app.pages.login_page.LoginPage.ALERT_ERROR_MESSAGE>`, class ``alert``.
    The lookup itself raises when the banner is absent altogether, once the
    implicit wait expires, and that exception propagates uncaught; only a
    located-but-hidden banner reaches the assertion.  The ``@UPGN-287`` outline
    is the only consumer.
    """
    assert _page(context).alert_error_message.is_displayed()


@step('User sees "{alert_message}" message')
def user_sees_please_fill_out_this_field_message(context, alert_message: str) -> None:
    """Compare the browser's required-field message -- ``:54-58``.

    :param context: behave's ``Context``; ``context.driver`` is the session.
    :param alert_message: The text the feature file supplies inside the quotes
        -- ``Veuillez renseigner ce champ.`` at ``Login.feature:89``, the
        browser's own French validation message, carried byte-for-byte.
    :returns: ``None``.
    :raises AssertionError: If the two values differ.  Java uses the
        **two-argument** ``assertEquals`` at ``:57``, so this assertion carries
        no message.

    ``:56`` reads the ``validationMessage`` attribute -- camelCase, as the DOM
    property is spelled -- off the login input, which it reaches by building
    ``By.name("login")`` **inline** rather than through the page object.  That
    single line is why this module imports :data:`~app.automation.By`, and the
    Python bindings take the strategy and the value as two arguments where Java
    takes one factory call.

    The naming is the source's and is deliberately preserved: the live value
    read out of the DOM is the local ``expected_message``, and the value the
    feature file supplied is the actual.  The roles are inverted with respect
    to what the names suggest; keeping ``expected_message`` is what keeps that
    visible instead of quietly correcting it.

    Which locale the browser reports this message in is not decided anywhere in
    either repository -- AAP 0.6 leaves it explicitly unresolved -- so this
    step behaves exactly as it does today: passing under a French-locale
    browser and failing otherwise.  Nothing here sets, reads or infers a
    locale.
    """
    expected_message = context.driver.find_element(
        By.NAME, "login"
    ).get_attribute("validationMessage")
    assert expected_message == alert_message


@step("User should see the password in bullet signs")
def user_should_see_the_password_in_bullet_signs(context) -> None:
    """Assert the password field masks its input -- ``LoginSD.java:60-63``.

    :param context: behave's ``Context``; ``context.driver`` is the session.
    :returns: ``None``.
    :raises AssertionError: If the input's ``type`` attribute is not
        ``"password"``.  ``:62`` wraps a ``String.equals`` comparison in a
        one-argument ``assertTrue``, so there is no message.

    ``:62`` reads ``getAttribute("type")`` off ``loginP.bulletPass`` --
    :attr:`LoginPage.BULLET_PASS
    <app.pages.login_page.LoginPage.BULLET_PASS>`, the second name
    ``LoginP.java`` gives the ``password`` control -- and compares it to
    ``"password"``.  Java's ``assertTrue(a.equals(b))`` collapses to a single
    equality assertion here, which is the same subject and the same outcome.
    Drives ``@UPGN-289``, the one outline that never submits the form.
    """
    assert _page(context).bullet_pass.get_attribute("type") == "password"


@step("User clicks the enter button")
def user_clicks_the_enter_button(context) -> None:
    """Submit the login form again, by clicking -- ``LoginSD.java:65-68``.

    :param context: behave's ``Context``; ``context.driver`` is the session.
    :returns: ``None``.

    ``:67`` is ``loginP.button.click()`` -- byte-identical to the login-button
    step above.  **No key is pressed.**  Despite the phrase and despite
    ``@UPGN-290``'s intent (*"Verify if the 'Enter' key of the keyboard is
    working correctly on the login page"*, ``Login.feature:121``),
    ``LoginSD.java`` imports neither ``Keys`` nor ``Actions`` and sends
    nothing; reproducing the phrase's intent instead of its body would change
    what the scenario exercises.

    Kept as its own function rather than merged with the login-button step:
    they are two separately registered phrases, ``Login.feature:126`` reaches
    this one and ``:17`` reaches the other, and the JSON writer records a
    distinct ``match.location`` for each.
    """
    _page(context).button.click()
