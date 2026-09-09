"""Step definitions for the Odoo Notes module - the port of ``Notes.java``.

Anchored on ``src/main/java/com/testinium/step_definitions/Notes.java`` at
pinned revision ``47e9d697e4a9a85da889f94a846fdf47af28a240``, held REFERENCE by
AAP 0.2.1 and never modified.  That class is 89 lines: two page-object fields
and one explicit-wait field (``Notes.java:16-19``), then **eleven** step
methods - eight ``@When`` and three ``@Then`` - serving the three scenarios of
``features/Notes.feature``: create a note, edit a note, and drag a card from
the New column to Today.

Each function below carries its Java line range, and the bodies are
statement-for-statement translations in the source's order.  AAP 0.4.1 states
the obligation this file is measured against: for every step method of the Java
class the port must perform *"the same observable operations in the same order
- the same navigation targets and their property sources, the same locators as
declared in the paired page object, the same wait target and timeout, the same
keyboard keys or action-chain sequence, the same hard-coded literals and
expected values, the same assertion subject and message text"*.
``tests/test_steps_notes.py`` enumerates the Java class's methods, so an
omission fails there rather than passing silently.

Two properties make this module unlike its nine siblings
--------------------------------------------------------
**It binds two page objects.**  ``Notes.java:16-17`` declares ``InventoryP
inventoryP`` and ``NotesP notesP`` side by side, and ``Notes.java:65`` clicks
``inventoryP.saveBtn`` from inside the "User enters new description" step.
Every other body in the class reaches ``notesP``.  Both pages expose a save
button, and the two Java ``@FindBy`` selectors are character-for-character
identical - ``NotesP.java:32-33`` and ``InventoryP.java:23-24`` both match
``//button[@class='btn btn-primary btn-sm o_form_button_save']`` - so nothing
observable on a live page distinguishes them.  The source nonetheless names two
different page objects at three different lines: ``:42`` and ``:47`` reach the
Notes page, ``:65`` reaches the Inventory page.  This module does the same, per
AAP 0.2.2's refusal to tidy the source - what looks like a copy-paste slip in
``Notes.java`` is authoritative here, and it is the entire reason
:class:`~app.pages.inventory_page.InventoryPage` has a second consumer.  One
consequence is worth stating for whoever verifies this file: because the two
selectors are equal, comparing locator tuples cannot tell the pages apart, so
the discriminator is the page class the click is issued through.

**Its drag-and-drop chain needed a unit conversion.**  ``Notes.java:79`` pauses
twice with a millisecond argument of 2000, because Java's action builder takes
**milliseconds** - while Python's takes **seconds**.  Transcribing that number
verbatim would pause for 2000 seconds, over half an hour per pause, and hang
the scenario; each pause is therefore expressed in seconds, as ``2``.  That is
a measured property of the two APIs rather than a departure from the source:
the wall-clock behaviour is preserved exactly and only the unit of the argument
changes.  The identical trap exists in ``crm_steps.py``, for ``Crm.java:111``
and ``:113``.

The fixed delay is preserved, and it is not a wait
--------------------------------------------------
``Notes.java:23`` is a genuine ``Thread.sleep(2000)`` - one of the suite's
seventeen fixed delays and the only one in this class - and it survives as a
2-second delay at the same call site: first in
:func:`user_clicks_the_notes_module`, before the click.  AAP 0.4.1 forbids
converting it to an explicit wait, because doing so *"would change timing
behaviour and, in a suite whose steps depend on Odoo's client-side rendering,
could change outcomes"*.  So this module opens a step with a blind 2-second
delay while also carrying a 20-second explicit-wait timeout.  That tension is
the source's own - ``Notes.java:19`` against ``:23`` - and it is reproduced
rather than resolved.  Replacing the fixed delays is a follow-up the user can
request; it is not part of this port.

Every definition registers keyword-agnostically
-----------------------------------------------
AAP 0.5.2 (deviation 7) mandates the generic decorator for the whole port, and
the reason is behavioural rather than stylistic.  Cucumber-JVM matches a step by
its text alone, so ``@Given``, ``@When`` and ``@Then`` are interchangeable at
match time; the Gherkin engine used here resolves by a step's *effective* type,
where an ``And`` inherits the keyword of the step above it.  A keyword-agnostic
registration matches whatever keyword invokes the phrase, and so reproduces
text-only matching exactly.

This file depends on that.  Its three ``@Then`` definitions close
``And``/``Then`` chains at ``Notes.feature:16``, ``:24`` and ``:29``, and eight
of the ten in-scenario invocations in that feature arrive as ``And``, whose
effective type is the ``When`` that opened the scenario.  Registered under one
specific keyword, those phrases would go undefined.
``tests/test_steps_registration.py`` asserts that no module here imports a
keyword-specific decorator.

The import boundary
-------------------
AAP 0.4.2 closes the import list, and the four statements below are all of it:
the standard-library delay, the registration decorator, three helpers from
``app.automation`` and the two page classes.  The browser-automation library is
imported by ``app.automation`` and by nothing else in the port - the keyboard
and action-chain helpers exist precisely so that a step module never names it -
and this module is one of only two importing both of those helpers, a set
AAP 0.4.2 fixes and which must not grow.  Deliberately absent: anything from
the configuration module, because ``Notes.java`` reads no property; any
locator-strategy name, because every locator lives in the paired page object;
and any session-lifecycle function, because ``features/environment.py`` owns
the session exclusively, per AAP 0.3.3 - *"no step or page ever creates or
quits a driver"*.

Nothing binds at module level
-----------------------------
``Notes.java:16-19`` builds both page objects and the wait as fields, at glue
construction.  In Python a module-level instance would bind whichever worker
process imported the module first, so both pages are constructed inside each
step from the session ``features/environment.py`` publishes on the context, and
the wait becomes a timeout argument rather than an object - which keeps this
class's 20 visible at each of its three call sites.  Construction is cheap:
``BasePage.__init__`` stores the session and touches neither the DOM nor the
driver.  The action builder is likewise created inside the step that uses it,
mirroring the source, which evaluates ``new Actions(...)`` at
``Notes.java:78``.

Feature context
---------------
``features/Notes.feature`` carries **no feature-level tag**, so it is
unreachable by a positive feature-tag expression - including the default
``@Smoke`` - and is selected with an explicit path or a negative expression such
as ``not @Smoke``.  Its header reads "Feature: Testinium app login feature",
mis-titled in the source and preserved anyway, which is why it collides with
``Login.feature`` on the report ``id``; AAP 0.4.1 names the mis-title and
AAP 0.2.2 declines to correct it.  The Background's ``Given User login to test
other features`` (``Notes.feature:8``) is **not** defined here - it belongs to
``session_steps.py``, and re-declaring it would make every scenario in this
feature ambiguous.  The Testinium vocabulary in the literals below stays
exactly as the source spells it, per AAP Conflict 8, which forbids reconciling
the Testinium/Upgenix/Odoo naming.

There is no error handling anywhere in this module, matching ``Notes.java``,
which catches nothing: a timeout, a stale element or a missing element
propagates and fails the scenario, at which point ``features/environment.py``
captures the failure screenshot and quits the session.
"""

# The standard library supplies the fixed delay of ``Notes.java:23``.
# ``app.automation`` exposes no delay helper and must not gain one: a fixed
# sleep is not an automation concern, it is a preserved property of one step
# body (AAP 0.4.1).
from time import sleep

from behave import step

from app.automation import action_chain, press_keys, wait_visible_element
from app.pages import InventoryPage, NotesPage

# This module declares no constant for its explicit-wait timeout, and that is
# deliberate.  ``Notes.java:19`` builds one ``wait`` field of 20 seconds, shared
# by the class's three wait sites (``:29``, ``:46``, ``:71``); here the literal
# ``20`` is spelled at each of those three call sites instead.  AAP 0.4.1
# accounts for the suite's nine explicit waits individually and the per-class
# timeouts genuinely differ - 2s, 3s, 4s and this 20s - so the timeout belongs
# to the call site, where a reader and a parity test can both see it.  A shared
# module constant would read as tidier and would invite a single future edit
# that silently retimed all three sites at once.


def _notes(context) -> NotesPage:
    """Bind the Notes page object to this scenario's session.

    The Python stand-in for the ``NotesP notesP`` field of ``Notes.java:17``,
    which the Java runtime built once per glue instance.  Called at the top of
    every step body here, because a module-level instance would bind whichever
    worker process imported this module first (AAP 0.4.2).

    :param context: behave's ``Context``.  ``context.driver`` is the session
        ``features/environment.py`` publishes in ``before_scenario`` and
        discards in ``after_scenario``.
    :returns: A page object bound to that session.  Construction locates no
        element and touches neither the DOM nor the driver, so calling this per
        step is as cheap as reading a field.
    """
    return NotesPage(context.driver)


def _inventory(context) -> InventoryPage:
    """Bind the Inventory page object to this scenario's session.

    The Python stand-in for the ``InventoryP inventoryP`` field of
    ``Notes.java:16``.  Reached from exactly one step body in this module -
    :func:`user_enters_new_description`, the port of ``Notes.java:61-67``,
    whose third statement clicks ``inventoryP.saveBtn`` at ``:65``.  Every other
    body in this module uses :func:`_notes` instead, and the two are never
    interchangeable even though both pages expose an identically-selected save
    button; see the module docstring.

    :param context: behave's ``Context``, carrying this scenario's session on
        ``context.driver``.
    :returns: A page object bound to that session.
    """
    return InventoryPage(context.driver)


@step("User clicks the Notes module")
def user_clicks_the_notes_module(context) -> None:
    """Open the Notes module from Odoo's main menu.

    The port of ``Notes.java:21-25``.  Two statements, in this order:

    * ``:23`` ``Thread.sleep(2000)`` - reproduced as a 2-second fixed delay
      **first in the body and before the click**, per AAP 0.4.1, which
      preserves each of the suite's seventeen fixed delays at its own call
      site.  It is deliberately not an explicit wait, even though this class
      declares a 20-second one at ``:19``; see the module docstring.
    * ``:24`` ``notesP.notesModule.click()`` - the partial-link-text locator
      ``NotesPage.NOTES_MODULE``, from ``NotesP.java:17-18``.

    Invoked as the opening ``When`` of all three scenarios in
    ``features/Notes.feature`` (``:11``, ``:19`` and ``:27``), so the delay is
    paid once per scenario exactly as in the source.

    :param context: behave's ``Context``, carrying this scenario's session.
    :returns: ``None``, matching the void Java method.
    """
    # The delay precedes the page binding as well as the click, so the ordering
    # of the two Java statements is literal; binding a page object performs no
    # browser work, so nothing observable happens before the delay either way.
    sleep(2)
    notes = _notes(context)
    notes.notes_module.click()


@step("User clicks create button in Notes module")
def user_clicks_create_button_in_notes_module(context) -> None:
    """Wait for the kanban "Create" button, then click it.

    The port of ``Notes.java:27-31``: ``:29`` waits on
    ``visibilityOf(notesP.creatingNotes)`` through the class's 20-second wait
    field, then ``:30`` clicks the same element.  The locator is
    ``NotesPage.CREATING_NOTES`` from ``NotesP.java:20-21``.

    The element is resolved twice, once for the wait and once for the click,
    because the page accessor re-runs its lookup on every access - which is
    what the ``PageFactory`` proxy did for the Java field it replaces
    (AAP goal G5).

    :param context: behave's ``Context``, carrying this scenario's session.
    :returns: ``None``, matching the void Java method.
    """
    notes = _notes(context)
    wait_visible_element(notes.creating_notes, 20)
    notes.creating_notes.click()


@step("User enters a tag name")
def user_enters_a_tag_name(context) -> None:
    """Type a tag into the autocomplete and commit it.

    The port of ``Notes.java:33-37``:

    * ``:35`` ``notesP.tagsN.sendKeys("New Tag", <ENTER>)`` - one keyboard call
      carrying the literal followed by the Enter key, in that argument order.
      It is expressed as a single helper call rather than two sends, because
      the Java call is one ``sendKeys`` invocation and splitting it would show
      the browser two keyboard events where the source produced one.  Locator:
      ``NotesPage.TAGS_N``, from ``NotesP.java:23-24``.
    * ``:36`` ``notesP.tabindex.click()`` - the "Create and Edit..." anchor the
      autocomplete offers, ``NotesPage.TABINDEX`` from ``NotesP.java:14-15``.
      The Java field name describes nothing about that anchor and is kept
      regardless (AAP 0.2.2).

    The phrase says "a tag name" while the value is hard-coded in the body, so
    this definition takes no parameter - none of the eleven here does.

    :param context: behave's ``Context``, carrying this scenario's session.
    :returns: ``None``, matching the void Java method.
    """
    notes = _notes(context)
    press_keys(notes.tags_n, "ENTER", text="New Tag")
    notes.tabindex.click()


@step("User enters description")
def user_enters_description(context) -> None:
    """Type the note body, then click save.

    The port of ``Notes.java:39-43``:

    * ``:41`` types the literal into ``NotesPage.DESCRIPTION``
      (``NotesP.java:26-27``).  A plain send with no key component, so it is
      **not** a keyboard-helper call site - that helper serves the one
      literal-plus-key send at ``:35``.
    * ``:42`` ``notesP.saveBtn.click()`` - the **Notes** page's save button,
      ``NotesPage.SAVE_BTN`` from ``NotesP.java:32-33``.  Not the Inventory
      page's; only ``Notes.java:65`` reaches that one.

    The literal keeps the source's "Testinium App" wording, which AAP
    Conflict 8 preserves rather than reconciling with the Upgenix and Odoo
    names that appear elsewhere in the project.

    :param context: behave's ``Context``, carrying this scenario's session.
    :returns: ``None``, matching the void Java method.
    """
    notes = _notes(context)
    notes.description.send_keys("This note is an example for the Testinium App")
    notes.save_btn.click()


@step("User clicks save button")
def user_clicks_save_button(context) -> None:
    """Wait for the save button, then click it.

    The port of ``Notes.java:44-48``: ``:46`` waits on
    ``visibilityOf(notesP.saveBtn)`` with this class's 20-second timeout, then
    ``:47`` clicks it.  Both statements address the **Notes** page's
    ``SAVE_BTN`` (``NotesP.java:32-33``).

    Invoked twice within the feature - ``Notes.feature:15`` and ``:22`` - once
    to save a new note and once to save an edited one.

    :param context: behave's ``Context``, carrying this scenario's session.
    :returns: ``None``, matching the void Java method.
    """
    notes = _notes(context)
    wait_visible_element(notes.save_btn, 20)
    notes.save_btn.click()


@step("User sees the created new notes")
def user_sees_the_created_new_notes(context) -> None:
    """Assert the confirmation message reads "Note created".

    The port of ``Notes.java:49-54``, statement for statement: read the text of
    ``notesP.createdMessage`` (``NotesPage.CREATED_MESSAGE``, from
    ``NotesP.java:29-30``), hold the expected literal in a local, then compare
    the two.

    ``Assert.assertEquals(actualMessage, expecgedMessage)`` at ``:53`` passes
    two arguments and no message string, so this is a bare comparison with no
    assertion message, and the operand order is the Java one.  Deviation 16 of
    AAP 0.1.3 covers what changes: Python cannot produce JUnit's
    ``expected:<...> but was:<...>`` rendering, so the assertion's *subject*
    is parity and its failure formatting is not.

    The Java local at ``:52`` is misspelled ``expecgedMessage``.  A local
    variable name is not part of the observable contract, so the spelling is
    corrected here; the literal ``"Note created"`` **is** observable and is
    byte-exact.

    :param context: behave's ``Context``, carrying this scenario's session.
    :returns: ``None``, matching the void Java method.
    """
    notes = _notes(context)
    actual_message = notes.created_message.text
    expected_message = "Note created"
    assert actual_message == expected_message


@step("User clicks the edit button")
def user_clicks_the_edit_button(context) -> None:
    """Open the existing note for editing.

    The port of ``Notes.java:56-59``, a single statement: ``:58`` clicks
    ``notesP.appK``, the card titled "BDD Approach Framework with Cucumber"
    (``NotesPage.APP_K``, from ``NotesP.java:35-36``).  The Java field name is
    the meaningless abbreviation ``appK`` and is kept (AAP 0.2.2).

    There is no wait here, though the class has one available.  The source
    waits at only three sites - ``:29``, ``:46`` and ``:71`` - and adding a
    fourth would change this step's timing behaviour.

    :param context: behave's ``Context``, carrying this scenario's session.
    :returns: ``None``, matching the void Java method.
    """
    notes = _notes(context)
    notes.app_k.click()


@step("User enters new description")
def user_enters_new_description(context) -> None:
    """Replace the note body, save through the Inventory page, reopen Notes.

    The port of ``Notes.java:61-67`` - the only body in this module that
    reaches two page objects, and the reason
    :class:`~app.pages.inventory_page.InventoryPage` is imported here.  Four
    statements, in this order:

    * ``:63`` ``notesP.description.clear()``
    * ``:64`` ``notesP.description.sendKeys("FKASDFGASDFADSFADS")`` - a plain
      send with no key component
    * ``:65`` **``inventoryP.saveBtn.click()``** - the **Inventory** page's
      ``SAVE_BTN`` (``InventoryP.java:23-24``), not the Notes page's
    * ``:66`` ``notesP.notesModule.click()``

    The third statement reaching into the Inventory page from a Notes step
    looks like a copy-paste slip in the source, and it is preserved anyway per
    AAP 0.2.2.  It is not observable on a live page - ``InventoryP.java:23-24``
    and ``NotesP.java:32-33`` declare identical selectors - which is precisely
    why it must be transcribed from the Java line rather than inferred from
    behaviour: substituting either page object for the other here would pass
    every runtime check and still misrepresent the source.

    ``Notes.feature:21`` and ``:23`` invoke this phrase twice in one scenario,
    with a save in between, so the body runs exactly twice per run of that
    scenario.

    :param context: behave's ``Context``, carrying this scenario's session.
    :returns: ``None``, matching the void Java method.
    """
    # Both pages are bound up front, in the declaration order of the Java
    # fields (``:16`` inventory, ``:17`` notes); neither binding performs
    # browser work, so the observable sequence starts with ``clear()`` below.
    inventory = _inventory(context)
    notes = _notes(context)
    notes.description.clear()
    notes.description.send_keys("FKASDFGASDFADSFADS")
    inventory.save_btn.click()
    notes.notes_module.click()


@step("User should see the Notes list")
def user_should_see_the_notes_list(context) -> None:
    """Wait for the Notes menu entry, click it, and assert it is displayed.

    The port of ``Notes.java:69-74``.  All three statements address the **same**
    element, ``notesP.notesModule`` (``NotesPage.NOTES_MODULE``, from
    ``NotesP.java:17-18``), in this order:

    * ``:71`` wait on its visibility, with this class's 20-second timeout
    * ``:72`` click it
    * ``:73`` ``Assert.assertTrue(notesP.notesModule.isDisplayed())``

    Waiting for an element, clicking it and then asserting that it is displayed
    is redundant on its face - and it is what the source does, so it is
    preserved unchanged (AAP 0.8, "Preserve, do not tidy").  The assertion is
    single-argument ``assertTrue`` with no message string, so it becomes a bare
    truth assertion with no message.

    :param context: behave's ``Context``, carrying this scenario's session.
    :returns: ``None``, matching the void Java method.
    """
    notes = _notes(context)
    wait_visible_element(notes.notes_module, 20)
    notes.notes_module.click()
    assert notes.notes_module.is_displayed()


@step("User move element from New section to Today section")
def user_move_element_from_new_section_to_today_section(context) -> None:
    """Drag the card from the New column onto the Today column.

    The port of ``Notes.java:76-80``.  ``:78`` builds a fresh action builder -
    ``new Actions(Driver.getDriver())`` - and ``:79`` performs one chain:
    grab ``notesP.newTable``, pause, move onto ``notesP.todayTable``, pause,
    release, perform.  Same operations, same order.  The two locators are
    ``NotesPage.NEW_TABLE`` and ``NotesPage.TODAY_TABLE``
    (``NotesP.java:38-39`` and ``:41-42``), which hard-code Odoo record ids
    ``1193`` and ``1194`` and index different children of each; that is the
    page object's business.

    **The pause arguments are converted, and they must be.**  The Java builder
    takes milliseconds and the Python one takes seconds, so the source's 2000
    is expressed here as ``2``.  Transcribing the number verbatim would suspend
    the scenario for 2000 seconds at each of the two pauses - over half an hour
    apiece - which is why the module docstring calls this out and why
    ``tests/test_steps_notes.py`` pins the chain's exact argument sequence.

    The builder is created inside this body rather than held at module level,
    matching a source that constructs it in the method (AAP 0.4.2), and the
    chain is assembled and performed in one expression, as at ``:79``.

    :param context: behave's ``Context``, carrying this scenario's session.
    :returns: ``None``, matching the void Java method.
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
    """Assert the Today column's text reads "Today".

    The port of ``Notes.java:82-88``: read the text of ``notesP.todayTable``
    (``NotesPage.TODAY_TABLE``, from ``NotesP.java:41-42``), hold the expected
    literal in a local, then compare the two.  ``:87`` is a two-argument
    ``Assert.assertEquals`` with no message string, so this is a bare
    comparison with no assertion message, in the Java operand order.

    The element asserted here is the same one the preceding drag-and-drop step
    dropped the card onto, and the assertion checks the column's label rather
    than the card's presence.  That is what the source asserts, and it is not
    strengthened here.

    :param context: behave's ``Context``, carrying this scenario's session.
    :returns: ``None``, matching the void Java method.
    """
    notes = _notes(context)
    actual_table = notes.today_table.text
    expected_table = "Today"
    assert actual_table == expected_table
