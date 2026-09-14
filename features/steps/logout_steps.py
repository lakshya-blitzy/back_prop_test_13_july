"""Step definitions for the logout flow - the port of ``LogOutSD.java``.

All three definitions are declared ``@Then`` in Java, but
``features/Logout.feature`` reaches two of them as ``And`` steps whose
effective keyword is ``When`` (``:19``, ``:43`` and ``:44``, against the real
``Then`` at ``:20``).  Cucumber-JVM matches on step text alone, so ``@step`` -
which registers for every keyword - is what keeps those three lines from
reporting as undefined (AAP 0.5.2, deviation 7).

``LogOutSD.java:13`` and ``:15`` build the 3-second wait and the page object as
fields at glue construction.  Neither becomes module-level state here, and the
reason is lifetime rather than sharing: each worker process imports this module
into its own address space, but an object built at import would predate the
scenario's session and outlive it, since ``features/environment.py`` opens a
driver in ``before_scenario`` and quits it in ``after_scenario``.  So the page
is built per call from ``context.driver``, and ``wait_visible_element`` takes
its timeout - this class's ``3`` - per call site.
"""

from behave import step

from app.automation import wait_visible_element
from app.pages import LogOutPage

__all__ = [
    "user_can_not_click_the_step_back_button_to_go_the_home_page",
    "user_click_log_out_option",
    "user_should_see_the_login_dashboard",
]


def _page(context) -> LogOutPage:
    return LogOutPage(context.driver)


@step("User click Log out option")
def user_click_log_out_option(context) -> None:
    page = _page(context)

    wait_visible_element(page.POP_UP_BUTTON, 3)
    page.pop_up_button.click()
    page.log_out_button.click()


@step("User should see the login dashboard")
def user_should_see_the_login_dashboard(context) -> None:
    """Assert the browser is back on the login page, by page title.

    Both literals are byte-exact and neither is to be tidied: the expected
    title carries a literal ``|``, and ``LogOutSD.java:27``'s assertion message
    ends in a **trailing space**, which AAP 0.8's *"preserve, do not tidy"*
    keeps because the message text is part of the parity contract.
    ``LoginSD.java:46`` shares that space; ``Calendar.java:46`` does not.
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
    context.driver.back()

    page = _page(context)
    assert page.warning_mess.is_displayed()
