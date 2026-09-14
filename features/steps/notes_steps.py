"""Step definitions for the Odoo Notes module - the port of ``Notes.java``.

Registration is keyword-agnostic throughout (AAP 0.5.2, deviation 7) because
Cucumber-JVM matches on step text alone: ``Session.java:12`` declares the
shared precondition ``@When`` while six Backgrounds invoke it as ``Given``,
and behave has no decorator for the ``And`` that three step classes import.

``Notes.java:16-19`` builds both page objects and the 20-second wait as glue
fields; neither is module state here, and the reason is lifetime rather than
sharing.  Each worker process imports this module into its own address space,
so module scope is never shared between workers; what an object built at import
would do is predate and outlive the session ``features/environment.py`` opens
per scenario.  So each page binds per call from ``context.driver``.
"""

from time import sleep

from behave import step

from app.automation import action_chain, press_keys, wait_visible_element
from app.pages import InventoryPage, NotesPage


def _notes(context) -> NotesPage:
    return NotesPage(context.driver)


def _inventory(context) -> InventoryPage:
    """Bind the Inventory page object to this scenario's session.

    Exists for one call site: ``Notes.java:65`` clicks ``inventoryP.saveBtn``
    inside "User enters new description".  Both save selectors are identical,
    so only the page a click goes through records which one the source named.
    """
    return InventoryPage(context.driver)


@step("User clicks the Notes module")
def user_clicks_the_notes_module(context) -> None:
    """Open the Notes module from Odoo's main menu.

    ``Notes.java:23`` is a fixed 2-second delay and stays one, first in the
    body and before the click: AAP 0.4.1 forbids converting the suite's fixed
    delays into explicit waits, even where a 20-second wait exists at ``:19``.
    """
    sleep(2)
    notes = _notes(context)
    notes.notes_module.click()


@step("User clicks create button in Notes module")
def user_clicks_create_button_in_notes_module(context) -> None:
    notes = _notes(context)
    wait_visible_element(notes.CREATING_NOTES, 20)
    notes.creating_notes.click()


@step("User enters a tag name")
def user_enters_a_tag_name(context) -> None:
    notes = _notes(context)
    press_keys(notes.tags_n, "ENTER", text="New Tag")
    notes.tabindex.click()


@step("User enters description")
def user_enters_description(context) -> None:
    notes = _notes(context)
    notes.description.send_keys("This note is an example for the Testinium App")
    notes.save_btn.click()


@step("User clicks save button")
def user_clicks_save_button(context) -> None:
    notes = _notes(context)
    wait_visible_element(notes.SAVE_BTN, 20)
    notes.save_btn.click()


@step("User sees the created new notes")
def user_sees_the_created_new_notes(context) -> None:
    notes = _notes(context)
    actual_message = notes.created_message.text
    expected_message = "Note created"
    assert actual_message == expected_message


@step("User clicks the edit button")
def user_clicks_the_edit_button(context) -> None:
    notes = _notes(context)
    notes.app_k.click()


@step("User enters new description")
def user_enters_new_description(context) -> None:
    """Replace the note body, save through the Inventory page, reopen Notes.

    ``Notes.java:65`` clicks the **Inventory** page's save button between the
    two Notes calls at ``:64`` and ``:66``, preserved per AAP 0.2.2.  Both save
    selectors are identical, so that choice is unobservable at runtime.
    """
    inventory = _inventory(context)
    notes = _notes(context)
    notes.description.clear()
    notes.description.send_keys("FKASDFGASDFADSFADS")
    inventory.save_btn.click()
    notes.notes_module.click()


@step("User should see the Notes list")
def user_should_see_the_notes_list(context) -> None:
    notes = _notes(context)
    wait_visible_element(notes.NOTES_MODULE, 20)
    notes.notes_module.click()
    assert notes.notes_module.is_displayed()


@step("User move element from New section to Today section")
def user_move_element_from_new_section_to_today_section(context) -> None:
    """Drag the card from the New column onto the Today column.

    One chain in the order of ``Notes.java:79``: hold ``new_table``, pause,
    move onto ``today_table``, pause, release, perform.  Java's builder takes
    milliseconds and this one seconds, so the source's 2000 pause is ``2``.
    """
    notes = _notes(context)
    actions = action_chain()
    (
        actions.click_and_hold(notes.new_table)
        .pause(2)
        .move_to_element(notes.today_table)
        .pause(2)
        .release()
        .perform()
    )


@step("User sees Today new added element")
def user_sees_today_new_added_element(context) -> None:
    notes = _notes(context)
    actual_table = notes.today_table.text
    expected_table = "Today"
    assert actual_table == expected_table
