"""Shared login precondition - the port of ``Session.java``.

The one phrase six other features invoke from their ``Background``: navigate to
``web.table.url``, type ``username``, type ``password``, click submit, in that
order, with no wait, delay, assertion or guard, since the Java class has none.

``Session.java:10`` builds the page object as a glue-construction field; here
it is built per call from ``context.driver``, for lifetime rather than sharing.
Each worker process imports this module into its own address space, but an
import-time object would predate and outlive the session ``before_scenario``
opens and ``after_scenario`` quits in ``features/environment.py``.
"""

from behave import step

from app.config import get_password, get_username, get_web_table_url
from app.pages import SessionPage


def _page(context) -> SessionPage:
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

    The three configuration values are passed straight through to the browser,
    and this module adds no check, default, fallback or message of its own to
    any of them.  Each accessor returns ``None`` when its key is absent - the
    port of ``ConfigurationReader.getProperty`` returning ``null``, which AAP
    0.4.1 keeps deliberately so that *"a missing key returns null so failures
    surface at the point of use"* - and that ``None`` still travels into the
    browser call that rejects it.

    The destination is the one value that is checked, and the check belongs to
    :func:`app.config.get_web_table_url` rather than to this step: a
    ``web.table.url`` that is *set* to a value outside that accessor's
    navigation policy - a ``file:`` URL, a host carrying user information, a
    value bearing a control character, the cloud instance-metadata address -
    raises :class:`ValueError` out of the accessor on the line below, so the
    browser is never asked for it and the two credentials on the lines after
    it are never typed.  That ordering is why it matters here: this step is
    the one place in the port that sends the configured user name and password
    to whatever the configured address answers with.

    Any failure, in configuration or in the DOM, propagates untouched to the
    engine, which records the scenario as failed and lets ``after_scenario``
    capture the screenshot.
    """
    page = _page(context)

    # Session.java:14 - navigation goes through the session the scenario
    # already owns, never through a driver accessor of this module's own.  The
    # accessor has already held a configured destination to the navigation
    # policy, so the value arriving here is either one the policy permits or
    # the ``None`` of an unset key.
    context.driver.get(get_web_table_url())

    page.input_login.send_keys(get_username())
    page.input_pass.send_keys(get_password())

    page.login_button.click()
