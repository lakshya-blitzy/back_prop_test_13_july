"""Step definitions for ``features/Calendar.feature`` - the port of ``Calendar.java``.

``Calendar.java:14`` and ``:15`` build the page object and the 2-second wait as
fields at glue construction.  Neither becomes module-level state here, and the
reason is lifetime rather than sharing: each worker process imports this module
into its own address space, but an object built at import would predate the
scenario's session and outlive it, since ``features/environment.py`` opens a
driver in ``before_scenario`` and quits it in ``after_scenario``.  So the page
is built per call from ``context.driver``, and ``wait_visible_element`` takes
its timeout - this class's ``2`` - per call site.
"""

import time

from behave import step

from app.automation import wait_visible_element
from app.pages import CalendarPage

#: The month names of ``Calendar.java``'s two switch statements, keyed by the
#: one-based month number the source computes.  No fallback of its own:
#: :func:`_month_label` supplies the empty-string default that the switch's
#: missing ``default`` case produces.
_MONTH_NAMES = {
    1: "January",
    2: "February",
    3: "March",
    4: "April",
    5: "May",
    6: "June",
    7: "July",
    8: "August",
    9: "September",
    10: "October",
    11: "November",
    12: "December",
}


def _month_label(month_number: int) -> str:
    """Map a one-based month number to its English name, or to ``""``.

    The ``switch`` at ``Calendar.java:59-96`` and ``:115-152`` has no
    ``default``, and ``:57`` and ``:113`` initialise ``month = ""`` ahead of it,
    so a number outside 1 to 12 leaves the expected string malformed and fails
    the assertion that follows.  The standard library's month-name sequence
    would raise ``IndexError`` there and turn that failure into an error.
    """
    return _MONTH_NAMES.get(month_number, "")


def _page(context) -> CalendarPage:
    return CalendarPage(context.driver)


@step("User click on the calendar dashboard")
def user_clicks_on_the_calendar_dashboard(context) -> None:
    page = _page(context)
    page.calendar_button.click()
    wait_visible_element(page.CALENDAR_BUTTON, 2)


@step("User click on day button")
def user_clicks_on_day_button(context) -> None:
    page = _page(context)
    page.day.click()
    wait_visible_element(page.DAY, 2)


@step("User click on week button")
def user_clicks_on_week_button(context) -> None:
    page = _page(context)
    page.week.click()
    wait_visible_element(page.WEEK, 2)


@step("User click on month button")
def user_clicks_on_month_button(context) -> None:
    page = _page(context)
    page.month.click()
    wait_visible_element(page.MONTH, 2)


@step("User should see the last stage of calendar view")
def user_should_see_the_last_stage_of_calendar_view(context) -> None:
    """Assert the browser is showing the Odoo Meetings page.

    The only assertion in this class that carries a message, reproduced byte
    for byte from ``Calendar.java:46`` with **no trailing space** - unlike
    ``LoginSD.java:46`` and ``LogOutSD.java:27``, which do end in one, so this
    module must not acquire theirs.  The title is read from the session, as
    ``:45`` reads it, which is why ``CalendarPage.TITLE`` is left unused.
    """
    page = _page(context)
    wait_visible_element(page.CALENDAR_MODULE, 2)
    expected_dashboard = "Meetings - Odoo"
    actual_dashboard = context.driver.title
    assert expected_dashboard == actual_dashboard, "The title is not same as the expected!"



@step("User click day on the calendar and display day")
def user_clicks_day_on_the_calendar_and_display_day(context) -> None:
    """Switch to the Day view and assert the displayed date matches today's.

    The ``data-month`` attribute is zero-based, hence the ``+ 1`` of
    ``Calendar.java:54``.  ``:99``'s ``Thread.sleep(3000)`` becomes a 3-second
    delay in the same position - after the expected string is built, before the
    breadcrumb is read - and that is what lets the calendar finish re-rendering
    before the comparison samples it, so moving the delay, shortening it or
    turning it into a wait would change what this step measures (AAP 0.4.1).
    """
    page = _page(context)
    page.day.click()
    wait_visible_element(page.DAY, 2)
    day_calendar = page.day_calendar.text
    month_calendar = int(page.month_and_year_calendar.get_attribute("data-month")) + 1
    year_calendar = int(page.month_and_year_calendar.get_attribute("data-year"))

    month = _month_label(month_calendar)

    expected_result = f"Meetings ({month} {day_calendar}, {year_calendar})"
    time.sleep(3)
    actual_result = page.date_actual.text
    assert expected_result == actual_result


@step("User click month on the calendar and display month")
def user_click_month_on_the_calendar_and_display_month(context) -> None:
    """Switch to the Month view and assert the displayed month matches today's.

    ``Calendar.java:154`` builds month, one space, year - **no day and no
    comma**, unlike the day view's string, and ``dayCalendar`` is never read
    because a month view shows no single day.  ``:155``'s
    ``Thread.sleep(3000)`` is the same 3-second delay in the same position,
    between building the expected string and reading the breadcrumb.
    """
    page = _page(context)
    page.month.click()
    wait_visible_element(page.MONTH, 2)
    month_calendar = int(page.month_and_year_calendar.get_attribute("data-month")) + 1
    year_calendar = int(page.month_and_year_calendar.get_attribute("data-year"))

    month = _month_label(month_calendar)

    expected_result = f"Meetings ({month} {year_calendar})"
    time.sleep(3)
    actual_result = page.date_actual.text
    assert expected_result == actual_result



@step("User click on desired date time")
def user_click_on_desired_date_time(context) -> None:
    page = _page(context)
    page.date_box.click()
    assert page.create_note.is_displayed()


@step('User enters "{note}" in the box and clicks the create button')
def user_enters_in_the_box_and_clicks_the_create_button(context, note) -> None:
    page = _page(context)

    event_name = note

    page.summary_box.send_keys(note)
    page.create_button.click()

    assert page.get_note.text == event_name


@step("User can see all the note")
def user_can_see_all_the_note(context) -> None:
    page = _page(context)
    assert page.created_note.is_displayed()


@step("User can select the note")
def user_can_select_the_note(context) -> None:
    page = _page(context)
    page.select_note.click()
    wait_visible_element(page.SELECT_NOTE, 2)
    assert page.created_modele.is_displayed()


@step("User can edit the information")
def user_can_edit_the_information(context) -> None:
    """Replace the meeting's summary text and read the tags checkbox.

    ``Calendar.java:194`` evaluates ``tagsCheckbox.isSelected()`` and throws
    the boolean away, so this step is sensitive only to whether the element can
    be found: it passes whether the box is ticked or not, and it asserts
    nothing.  Wrapping that call in an ``assert`` would fail every scenario
    with an unticked box, changing the outcome rather than porting it.
    """
    page = _page(context)
    page.edit_button.click()
    wait_visible_element(page.EDIT_BUTTON, 2)
    page.edit_text.clear()
    page.edit_text.send_keys("Hello My Friends")
    wait_visible_element(page.EDIT_TEXT, 2)
    page.tags_checkbox.is_selected()


@step("User can save all edit")
def user_can_save_all_edit(context) -> None:
    page = _page(context)
    page.save_button.click()
