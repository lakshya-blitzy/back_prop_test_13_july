r"""Contacts step module - the port of ``Contacts.java``.

Fourteen definitions for ``features/Contact.feature``, in Java declaration
order.  Five ``Thread.sleep(3000)`` calls (``:19``, ``:25``, ``:57``, ``:64``,
``:98``) stay 3-second delays where they sit, per AAP 0.4.1, and ``:90-93``'s
commented-out fifteenth definition stays unported, per AAP 0.2.2.

``Contacts.java:13`` and ``:15`` build the page object and the 20-second wait
as glue fields; neither is module state here, and the reason is lifetime rather
than sharing.  Each worker process imports this module into its own address
space, so module scope is never shared between workers; what an object built at
import would do is predate and outlive the session ``features/environment.py``
opens per scenario.  So the page binds per call from ``context.driver``.
"""

from time import sleep

from behave import register_type, step
from parse import with_pattern

from app.automation import wait_visible_element
from app.pages import ContactsPage


@with_pattern(r'[^"]*')
def parse_cuke_str(text: str) -> str:
    r"""Return a quoted step argument unchanged - the identity converter.

    Stands in for Cucumber's ``{string}``: the attached ``[^"]*`` cannot span
    a closing quote, so the one-argument ``User enters`` pattern no longer
    swallows the two-argument step use.  Captured text arrives unquoted.
    """
    return text


# Module scope, and above every decorator below, because behave's parse
# matcher compiles a step pattern at DECORATION time: a registration that ran
# later - in environment.py's before_all, for instance - would leave the three
# patterns already compiled with a plain field and change nothing at all.
# Moving this statement below the decorators is expected to break the dry run,
# and that failure is the proof the ordering matters.
register_type(CukeStr=parse_cuke_str)


def _page(context) -> ContactsPage:
    return ContactsPage(context.driver)


@step("User is at Contact dashboard")
def user_is_at_contact_dashboard(context) -> None:
    page = _page(context)
    sleep(3)
    page.contact_module.click()


@step("User clicks the create button")
def user_clicks_the_create_button(context) -> None:
    page = _page(context)
    sleep(3)
    page.create_contact.click()


@step('User enters name "{name:CukeStr}"')
def user_enters_name(context, name: str) -> None:
    page = _page(context)
    wait_visible_element(page.NAME_INPUT, 20)
    page.name_input.clear()
    page.name_input.send_keys(name)


@step('User enters "{street_name:CukeStr}"')
def user_enters(context, street_name: str) -> None:
    _page(context).street_input.send_keys(street_name)


@step('User enters "{phone_no:CukeStr}" and "{e_mail:CukeStr}"')
def user_enters_and(context, phone_no: str, e_mail: str) -> None:
    page = _page(context)
    page.phone_no_input.send_keys(phone_no)
    page.email_input.send_keys(e_mail)


@step("User sees the created new contact details at dashboard")
def user_sees_the_created_new_contact_details_at_dashboard(context) -> None:
    page = _page(context)
    page.contact_module.click()
    wait_visible_element(page.CONTACT_MODULE, 20)
    page.ok_btn.click()


@step("User clicks list section and choose the profile")
def user_clicks_list_section_and_choose_the_profile(context) -> None:
    page = _page(context)
    page.call_list.click()
    sleep(3)
    page.new_contact.click()


@step("User clicks Action to choose delete button")
def user_clicks_action_to_choose_delete_button(context) -> None:
    page = _page(context)
    page.action_input.click()
    sleep(3)
    page.delete_input.click()


@step("User clicks for editing button")
def user_clicks_for_editing_button(context) -> None:
    _page(context).edit_btn.click()


@step("User sees deleted profile")
def user_sees_deleted_profile(context) -> None:
    page = _page(context)
    actual_msg = page.delete_input.text
    expected_msg = "Deleted"
    assert actual_msg == expected_msg


@step("User selects the profile")
def user_selects_the_profile(context) -> None:
    page = _page(context)
    page.first_user.click()
    wait_visible_element(page.EDIT_TITLE, 20)


@step("User sees the updated contact details at dashboard")
def user_sees_the_updated_contact_details_at_dashboard(context) -> None:
    _page(context).contact_module.click()


@step("User clicks the print button and then select due payments")
def user_clicks_the_print_button_and_then_select_due_payments(context) -> None:
    page = _page(context)
    page.print_input.click()
    sleep(3)


@step("User can see the downloaded file")
def user_can_see_the_downloaded_file(context) -> None:
    _page(context).due_payment.click()
