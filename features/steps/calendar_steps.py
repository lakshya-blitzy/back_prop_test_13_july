"""Step definitions for ``features/Calendar.feature`` - the port of ``Calendar.java``.

Java anchor
-----------
``src/main/java/com/testinium/step_definitions/Calendar.java`` at pinned
revision ``47e9d697e4a9a85da889f94a846fdf47af28a240``, held REFERENCE by AAP
0.2.1 and never modified.  At 204 lines it is the largest step class in the
suite: **thirteen** step definitions, of which two - ``Calendar.java:49`` and
``:106`` - carry real logic and the remaining eleven are two to six statements
each.  Every observable operation below is that file's, in that file's order.

The class it ports declares two fields and nothing else (``Calendar.java:14-15``)::

    CalendarP calendarP = new CalendarP();
    WebDriverWait wait   = new WebDriverWait(Driver.getDriver(), 2);

Both are re-expressed per call rather than per module - see *Per-scenario
binding* below - so this module holds **no** module-level page object, driver
reference or wait object, and the class's 2-second timeout is written as the
literal ``2`` at each of its ten wait call sites.

The thirteen definitions
------------------------
============  ==============  =============================================
Java line     Java keyword    Phrase
============  ==============  =============================================
``:17``       ``@When``       User click on the calendar dashboard
``:23``       ``@When``       User click on day button
``:29``       ``@When``       User click on week button
``:35``       ``@When``       User click on month button
``:41``       ``@Then``       User should see the last stage of calendar view
``:49``       ``@When``       User click day on the calendar and display day
``:106``      ``@Then``       User click month on the calendar and display month
``:160``      ``@And``        User click on desired date time
``:166``      ``@Then``       User enters {string} in the box and clicks the create button
``:177``      ``@When``       User can see all the note
``:181``      ``@When``       User can select the note
``:187``      ``@When``       User can edit the information
``:198``      ``@Then``       User can save all edit
============  ==============  =============================================

Each phrase is carried over byte for byte, and each Python function keeps its
Java method's name so that the ``match.location`` field of
``target/cucumber.json`` names a recognisable counterpart.  The two Java
methods written in camelCase (``userClickOnDesiredDateTime`` at ``:161`` and
``userEntersInTheBoxAndClicksTheCreateButton`` at ``:167``) become the
snake_case forms of the same words; the other eleven were already snake_case
and are unchanged.

Why every definition registers with ``@step``
---------------------------------------------
This is behaviour, not house style (AAP 0.5.2, deviation 7).  Cucumber-JVM
matches a step by its **text alone**, so ``Given``, ``When``, ``Then`` and
``And`` are interchangeable at match time; behave resolves by the step's
*effective* type, where an ``And`` inherits the keyword of the step above it.
``@step`` registers a definition for every type at once and so reproduces
text-only matching exactly.  Two cases in this module make it mandatory rather
than merely convenient:

* ``Calendar.java:160`` declares its definition with ``@And``, and behave has
  no ``@and`` decorator at all.  There is nothing else to map it onto.
* ``Calendar.java:166`` declares ``@Then``, but ``Calendar.feature:37``
  reaches it as ``And User enters "Test" in the box and clicks the create
  button``, inside the ``And`` chain opened by the ``When`` at
  ``Calendar.feature:34``.  Its effective type there is ``When``, so a
  ``Then``-only registration would report that use **undefined**.  The three
  definitions after it (``:177``, ``:181``, ``:187``) are reached through the
  same chain and agree with their ``@When`` declarations only by luck.

Nothing in the source can rely on keyword disambiguation, because Cucumber-JVM
gives it no way to, so registering everything with ``@step`` loses nothing.
The same cross-type situation covers this feature's Background step,
``Calendar.feature:9`` - ``Given User login to test other features`` - whose
definition is declared ``@When`` in ``Session.java:12`` and lives in
``features/steps/session_steps.py``.  It is **not** redeclared here.

Import boundary (AAP 0.4.2)
---------------------------
Four imports, and the list is closed: the ``@step`` decorator, the one wait
helper this class needs, its page object, and the standard library's sleep.
This module never imports the browser-automation library - ``app.automation``
is the only package permitted to, and ``tests/test_steps_registration.py``
asserts that absence for every module under ``features/steps/``.  It also
imports no locator-strategy constant (only ``login_steps`` needs one, for
``LoginSD.java:56``), and neither the keyboard-key helper nor the
action-chain helper, because ``Calendar.java`` imports neither of the classes
they wrap and its one ``sendKeys`` call at ``:192`` is a plain literal send on
a web element.  It imports nothing from the configuration accessor at
``app/config.py``, since ``Calendar.java`` reads no configuration property,
and neither of the two session-lifecycle helpers: AAP 0.3.3 gives that
lifecycle a single owner in ``features/environment.py``, so *"no step or page
ever creates or quits a driver"*.

Of the wait family only :func:`~app.automation.waits.wait_visible_element` is
imported, because all ten waits in ``Calendar.java`` are
``ExpectedConditions.visibilityOf`` applied to a **web element** rather than
to a locator.

Per-scenario binding
--------------------
``features/environment.py`` opens the session in ``before_scenario`` and
publishes it as ``context.driver``; :func:`_page` wraps it in a fresh
:class:`~app.pages.calendar_page.CalendarPage` inside each step body.
Constructing the page touches neither the DOM nor the driver
(``BasePage.__init__`` only stores its argument), and every accessor
re-resolves its element on access, which is the ``PageFactory`` proxy
behaviour ``CalendarP.java:9-11`` provides.  A module-level page object would
instead bind whichever worker process imported this module first and would
outlive the session it was built against, since ``after_scenario`` quits and
clears it.

The wait is a timeout **argument**, not an object: the source fixes a
different timeout per step class - 2s here (``Calendar.java:15``), 3s, 4s and
20s elsewhere - so ``wait_visible_element`` declares no default and this
module's ``2`` is visible at all ten call sites.

The two fixed delays
--------------------
``Calendar.java:99`` and ``:155`` are ``Thread.sleep(3000)``, two of the
suite's seventeen fixed sleeps.  AAP 0.4.1 keeps them *"reproduced as
equivalent fixed delays at the same call sites, not replaced by explicit
waits"*, because converting them *"would change timing behaviour and, in a
suite whose steps depend on Odoo's client-side rendering, could change
outcomes"*.  Each therefore appears as ``time.sleep(3)`` in the same position
as its original: **after** the expected string is built and **before**
``date_actual`` is read.  That position is the whole point - it is what gives
the calendar time to re-render before the assertion samples the page - so
neither delay may be moved, shortened, made conditional or turned into a wait
on ``DATE_ACTUAL``.  Replacing them is a follow-up the user can request; it is
not part of this port.

``time`` is imported as a module and the calls are written ``time.sleep(3)``,
which keeps them patchable both globally and through this module's ``time``
attribute.  ``app.automation`` exposes no sleep helper and must not gain one.
Java's ``InterruptedException``, declared by three of these methods, has no
Python counterpart and so leaves no trace here.

What this module deliberately does not contain
----------------------------------------------
* **No default month.**  See :func:`_month_label`: the Java switch has no
  ``default`` case, and reproducing that is a parity requirement.
* **No exception handling.**  A wait expiry, a failed integer parse, a missing
  element and a dead session all propagate untouched, exactly as the Java
  method's uncaught exception fails its step.  There is no ``try``, no retry
  and no defensive guard anywhere below.
* **No logging and no printing.**  Only ``Crm.java`` and ``Sales.java`` print;
  this class does not.
* **No fourteenth definition**, no redeclaration of the Background phrase, and
  no assertion wrapped around the discarded ``isSelected()`` call at
  ``Calendar.java:194``.
* **No package marker and no test module beside it.**  behave discovers
  ``features/steps/*.py`` by convention, without ``__init__.py``; this
  module's verifying test is ``tests/test_steps_calendar.py``.

Feature context
---------------
``features/Calendar.feature`` carries ``@Calendar`` at line 1 - so a default
run, whose filter is ``@Smoke`` (``CukesRunner.java:18``), does not select it,
and ``--tags=@Calendar`` does.  It holds a Background (line 9), three plain
scenarios (lines 11, 18 and 33) and one Scenario Outline (line 23) whose
``Examples: Test name`` block at line 29 supplies a single row at line 31.
"Meetings - Odoo" and the rest of the Odoo vocabulary are kept exactly as
written: AAP Conflict 8 forbids reconciling the "Testinium", "Upgenix" and
"Odoo" namings.
"""

import time

from behave import step

from app.automation import wait_visible_element
from app.pages import CalendarPage

#: The twelve month names of ``Calendar.java``'s switch statements, keyed by
#: the **one-based** month number the source computes.  Both switches
#: (``Calendar.java:59-96`` and ``:115-152``) list exactly these names in
#: exactly this order, and they are English-language literals in the source
#: rather than locale-derived text, so they are literals here too - the
#: browser's locale, which ``features/Login.feature`` does depend on, has no
#: bearing on this mapping.
#:
#: Deliberately a mapping with no fallback of its own; :func:`_month_label`
#: supplies the empty-string default that the switch's missing ``default``
#: case produces.
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

    :param month_number: The month, one-based - that is, the value
        ``Calendar.java:54`` and ``:110`` compute as
        ``Integer.parseInt(...getAttribute("data-month")) + 1``.
    :returns: The month's English name for 1 to 12, and the **empty string**
        for every other value.

    The port of the twelve-case ``switch`` that both big definitions run
    (``Calendar.java:59-96``, repeated verbatim at ``:115-152``).  The two
    switches are character-for-character identical, so they share one helper
    here rather than being duplicated; it is a pure mapping with no
    behavioural surface of its own, and it stays inside this module because
    the AAP 0.3.1 target tree lists exactly ten step modules under
    ``features/steps/`` and no helper module.

    .. important::

       **The switch has no ``default`` case, and that is behaviour to
       preserve.**  ``Calendar.java:57`` and ``:113`` initialise
       ``String month = ""`` *before* the switch, so a month number outside 1
       to 12 - which a corrupted or unexpected ``data-month`` attribute can
       produce - leaves ``month`` empty and the expected string malformed, for
       instance ``"Meetings ( 15, 2024)"`` with its telltale double space.
       The assertion that follows then fails on that string.

       This is why the standard library's ``calendar`` module and its
       month-name sequence are **not** used: that sequence raises
       ``IndexError`` for an out-of-range index, which would
       replace a failed assertion with an error - a different outcome, a
       different report status and a parity break.  ``dict.get`` with an
       empty-string default reproduces the Java outcome precisely, and the
       default must not be changed to a placeholder, an exception or a guess.

    Note that no range check is needed and none is written: any integer, zero
    and negative values included, simply misses the mapping and yields ``""``.
    """
    return _MONTH_NAMES.get(month_number, "")


def _page(context) -> CalendarPage:
    """Bind a Calendar page object to this scenario's session.

    :param context: behave's ``Context``.  Only ``context.driver`` is read -
        the session ``features/environment.py`` opened in ``before_scenario``.
    :returns: A fresh :class:`~app.pages.calendar_page.CalendarPage` bound to
        that session.

    The per-call replacement for ``Calendar.java:14``'s ``CalendarP calendarP =
    new CalendarP()`` field.  Construction is free of side effects - it stores
    the driver and does nothing else - and every locator accessor re-resolves
    against the live DOM on access, so building the page here rather than when
    this module is first loaded changes nothing observable while keeping each
    scenario's page object bound to that scenario's own session.

    ``context.driver`` is passed through exactly as published, ``None``
    included: the source's driver factory switches on the ``browser`` property
    with cases for Chrome and Firefox and no default branch, so an
    unrecognised value yields no session and the failure has to surface at the
    point of use.  Nothing here validates it or substitutes a default.
    """
    return CalendarPage(context.driver)


# ---------------------------------------------------------------------------
# The thirteen step definitions, in ``Calendar.java`` order.  Every one of them
# registers with ``@step`` - see the module docstring for why that is a
# behavioural requirement rather than a style choice - and every one builds its
# page object from the context rather than from module state.
# ---------------------------------------------------------------------------


@step("User click on the calendar dashboard")
def user_clicks_on_the_calendar_dashboard(context) -> None:
    """Open the Calendar module from the Odoo main menu.

    :param context: behave's ``Context``; supplies this scenario's session.
    :returns: ``None``.

    The port of ``Calendar.java:17-21``.  Two statements, in this order:

    * ``:19`` ``calendarP.calendarButton.click()``
    * ``:20`` ``wait.until(ExpectedConditions.visibilityOf(calendarP.calendarButton))``

    Clicking an element and *then* waiting for that same element to be visible
    is the source's own ordering, and it is kept: the wait is not a
    pre-condition for the click here, it is a settle-time after it.  The
    2-second timeout is ``Calendar.java:15``'s.  The Java method declares
    ``throws InterruptedException`` although its body cannot raise one; that
    declaration has no Python counterpart.
    """
    page = _page(context)
    page.calendar_button.click()
    wait_visible_element(page.calendar_button, 2)


@step("User click on day button")
def user_clicks_on_day_button(context) -> None:
    """Switch the calendar to its Day view.

    :param context: behave's ``Context``; supplies this scenario's session.
    :returns: ``None``.

    The port of ``Calendar.java:23-27``: ``:25`` clicks ``day``, ``:26`` waits
    2 seconds for it to be visible.  No assertion - the source makes none, and
    the view is verified later by
    :func:`user_should_see_the_last_stage_of_calendar_view`.
    """
    page = _page(context)
    page.day.click()
    wait_visible_element(page.day, 2)


@step("User click on week button")
def user_clicks_on_week_button(context) -> None:
    """Switch the calendar to its Week view.

    :param context: behave's ``Context``; supplies this scenario's session.
    :returns: ``None``.

    The port of ``Calendar.java:29-33``: ``:31`` clicks ``week``, ``:32``
    waits 2 seconds for it to be visible.
    """
    page = _page(context)
    page.week.click()
    wait_visible_element(page.week, 2)


@step("User click on month button")
def user_clicks_on_month_button(context) -> None:
    """Switch the calendar to its Month view.

    :param context: behave's ``Context``; supplies this scenario's session.
    :returns: ``None``.

    The port of ``Calendar.java:35-39``: ``:37`` clicks ``month``, ``:38``
    waits 2 seconds for it to be visible.
    """
    page = _page(context)
    page.month.click()
    wait_visible_element(page.month, 2)


@step("User should see the last stage of calendar view")
def user_should_see_the_last_stage_of_calendar_view(context) -> None:
    """Assert the browser is showing the Odoo Meetings page.

    :param context: behave's ``Context``; supplies this scenario's session and
        the page title read below.
    :returns: ``None``.
    :raises AssertionError: When the page title is not exactly
        ``"Meetings - Odoo"``.

    The port of ``Calendar.java:41-47``, statement for statement:

    * ``:43`` wait 2 seconds for ``calendarModule`` - the
      ``o_calendar_container`` element - to be visible
    * ``:44`` the expected title, the literal ``"Meetings - Odoo"``
    * ``:45`` the actual title, from ``Driver.getDriver().getTitle()``, which
      is ``context.driver.title`` here
    * ``:46`` ``Assert.assertEquals("The title is not same as the expected!",
      expected, actual)``

    **This is the only assertion in the class that carries a message**, and the
    message is reproduced byte for byte - ``The title is not same as the
    expected!`` - with **no trailing space**.  ``Sales.java:37`` uses the same
    string; ``LoginSD.java:46`` and ``LogOutSD.java:27`` use a variant that
    does end in a space, and this module must not acquire theirs.  Only the
    text is parity: JUnit's ``expected:<...> but was:<...>`` rendering has no
    Python equivalent (AAP 0.5.2, deviation 16).

    The title is compared against the whole string, not searched within it, and
    ``CalendarPage.TITLE`` - the source's XPath against ``//title`` - is
    deliberately unused here, because ``Calendar.java:45`` reads the driver's
    title rather than that element.
    """
    page = _page(context)
    wait_visible_element(page.calendar_module, 2)
    expected_dashboard = "Meetings - Odoo"
    actual_dashboard = context.driver.title
    assert expected_dashboard == actual_dashboard, "The title is not same as the expected!"



@step("User click day on the calendar and display day")
def user_clicks_day_on_the_calendar_and_display_day(context) -> None:
    """Switch to the Day view and assert the displayed date matches today's.

    :param context: behave's ``Context``; supplies this scenario's session.
    :returns: ``None``.
    :raises ValueError: When ``data-month`` or ``data-year`` does not parse as
        an integer.  ``Integer.parseInt`` throws in the same situation and the
        Java method does not catch it, so nothing is caught here either.
    :raises AssertionError: When the control-panel breadcrumb does not read
        exactly ``"Meetings (<Month> <day>, <year>)"``.

    The port of ``Calendar.java:49-104``, the first of the class's two big
    definitions.  Its six stages, in the source's order:

    1. ``:51-52`` click ``day``, then wait 2 seconds for it to be visible.
    2. ``:53`` read the highlighted day's text from ``dayCalendar`` - a
       **string**, and used as one.
    3. ``:54-55`` read ``data-month`` and ``data-year`` from
       ``monthAndYearCalendar``, the *same* element for both, parsing each with
       ``Integer.parseInt``.  **The month is one-based only after ``+ 1``**:
       the attribute is zero-based, so ``data-month="2"`` is March.  Dropping
       that increment would shift every month by one and is the single easiest
       way to break this step.
    4. ``:57-96`` map the number to an English month name through the
       twelve-case switch, here :func:`_month_label`, whose missing ``default``
       yields ``""``.
    5. ``:98-99`` build the expected string, then sleep 3 seconds.
    6. ``:100-101`` read ``dateActual`` and assert equality with a two-argument
       ``assertEquals``, so **no message** (AAP 0.5.2, deviation 16).

    The expected string is ``"Meetings (" + month + " " + dayCalendar + ", " +
    yearCalendar + ")"`` (``:98``): a space between month and day, then a comma
    and a space, then the year.  Java renders the string ``dayCalendar`` and
    the int ``yearCalendar`` into the same concatenation; an f-string renders
    both identically, so ``"Meetings (March 15, 2024)"`` comes out of either.
    With a month number outside 1 to 12 the empty name leaves
    ``"Meetings ( 15, 2024)"``, which is the source's behaviour and is what the
    assertion then fails on.

    .. warning::

       The 3-second delay sits **between** building the expectation and reading
       the page, exactly where ``Calendar.java:99`` puts it.  That ordering is
       the substance of the step: the expectation is computed from the
       datepicker before the delay, and the breadcrumb is sampled after it, so
       the delay is what lets the calendar finish re-rendering.  Moving it,
       shortening it or replacing it with a wait on ``DATE_ACTUAL`` would
       change what this step measures.

    This definition and
    :func:`user_click_month_on_the_calendar_and_display_month` differ in
    exactly two ways: this one reads ``dayCalendar`` and includes the day and a
    comma in its expected string, and that one does neither.
    """
    page = _page(context)
    page.day.click()
    wait_visible_element(page.day, 2)
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

    :param context: behave's ``Context``; supplies this scenario's session.
    :returns: ``None``.
    :raises ValueError: When ``data-month`` or ``data-year`` does not parse as
        an integer; uncaught, as in the source.
    :raises AssertionError: When the control-panel breadcrumb does not read
        exactly ``"Meetings (<Month> <year>)"``.

    The port of ``Calendar.java:106-158``, the second big definition.  It
    repeats the previous one's structure with the ``month`` button in place of
    ``day``:

    1. ``:108-109`` click ``month``, then wait 2 seconds for it to be visible.
    2. ``:110-111`` read and parse ``data-month`` - again ``+ 1`` - and
       ``data-year`` from ``monthAndYearCalendar``.
    3. ``:113-152`` map the number through the same twelve-case switch, again
       with no ``default``.
    4. ``:154-155`` build the expected string, then sleep 3 seconds.
    5. ``:156-157`` read ``dateActual`` and assert equality, two-argument and
       therefore message-less.

    .. important::

       **The expected string here is not the other one.**
       ``Calendar.java:154`` is ``"Meetings (" + month + " " + yearCalendar +
       ")"``: month, one space, year - **no day and no comma**, and
       ``dayCalendar`` is never read, because a month view shows no single day.
       Copying the day-view format is the most likely single defect in this
       module, and it would fail every run of ``Calendar.feature:21``.  For
       March 2024 the string is exactly ``"Meetings (March 2024)"``.

    As above, the 3-second delay stays between the expectation and the page
    read (``Calendar.java:155``), and an out-of-range month leaves the name
    empty rather than raising.
    """
    page = _page(context)
    page.month.click()
    wait_visible_element(page.month, 2)
    month_calendar = int(page.month_and_year_calendar.get_attribute("data-month")) + 1
    year_calendar = int(page.month_and_year_calendar.get_attribute("data-year"))

    month = _month_label(month_calendar)

    expected_result = f"Meetings ({month} {year_calendar})"
    time.sleep(3)
    actual_result = page.date_actual.text
    assert expected_result == actual_result



@step("User click on desired date time")
def user_click_on_desired_date_time(context) -> None:
    """Click a time box in the calendar grid and assert the new-meeting modal opens.

    :param context: behave's ``Context``; supplies this scenario's session.
    :returns: ``None``.
    :raises AssertionError: When the modal header is not displayed.

    The port of ``Calendar.java:160-164``:

    * ``:162`` ``calendarP.dateBox.click()`` - ``DATE_BOX`` is the source's
      hard-coded positional selector ``(//td[@class='fc-widget-content'])[29]``
    * ``:163`` ``Assert.assertTrue(calendarP.createNote.isDisplayed())``

    A single-argument ``assertTrue``, so the ``assert`` carries no message.
    There is no wait between the click and the assertion: the source adds none,
    and the session's 10-second implicit wait is what covers the modal's
    appearance.

    This is the definition ``Calendar.java:160`` declares with ``@And``.  behave
    has no ``@and`` decorator, so ``@step`` is the only possible registration -
    one of the two reasons the module docstring gives for using it throughout.
    """
    page = _page(context)
    page.date_box.click()
    assert page.create_note.is_displayed()


@step('User enters "{note}" in the box and clicks the create button')
def user_enters_in_the_box_and_clicks_the_create_button(context, note) -> None:
    """Name the new meeting, create it, and assert the name was saved.

    :param context: behave's ``Context``; supplies this scenario's session.
    :param note: The meeting name, taken from the quoted text in the step -
        ``"Test test"`` through the outline's ``Examples`` row at
        ``Calendar.feature:31``, and the literal ``"Test"`` at
        ``Calendar.feature:37``.
    :returns: ``None``.
    :raises AssertionError: When the created meeting's name field does not read
        back exactly what was typed.

    The port of ``Calendar.java:166-175``:

    * ``:169`` ``String eventName = note`` - a redundant alias, copied here as
      ``event_name`` so the assertion reads against it exactly as the Java one
      does.  It is not folded away: the parity test compares this body's
      operations against the Java method's.
    * ``:171`` ``calendarP.summaryBox.sendKeys(note)`` - a plain literal send
      on a web element, needing no keyboard helper, since ``Calendar.java``
      imports neither the keyboard-key class nor the action-chain class
    * ``:172`` ``calendarP.createButton.click()``
    * ``:174`` ``Assert.assertEquals(calendarP.getNote.getText(), eventName)``

    The assertion is two-argument and so message-less, and its operand order is
    the source's: the page's text first, the expected name second.  ``getNote``
    and ``createButton`` are used by name even though ``SELECT_NOTE`` and
    ``EDIT_BUTTON`` resolve to the same two selectors - ``CalendarP.java``
    declares four distinct fields and the parity test compares names, so each
    call site uses the name its Java line uses.

    Cucumber's ``{string}`` placeholder (``Calendar.java:166``) matches the
    surrounding quotation marks and hands the step the unquoted value; behave's
    equivalent puts the quotes in the pattern literally around a named field,
    which is why the decorator reads ``'User enters "{note}" in the box and
    clicks the create button'``.  The pattern is deliberately no wider than
    that - it is verified unambiguous against every step use in the suite, and
    widening it would risk matching another module's phrase.
    """
    page = _page(context)

    event_name = note

    page.summary_box.send_keys(note)
    page.create_button.click()

    assert page.get_note.text == event_name


@step("User can see all the note")
def user_can_see_all_the_note(context) -> None:
    """Assert the created meeting is visible in the calendar.

    :param context: behave's ``Context``; supplies this scenario's session.
    :returns: ``None``.
    :raises AssertionError: When the meeting's name element is not displayed.

    The port of ``Calendar.java:177-180``, a single statement: ``:179``
    ``Assert.assertTrue(calendarP.createdNote.isDisplayed())``.  ``CREATED_NOTE``
    is the source's class-name locator ``o_field_name``, kept distinct from the
    XPath forms it overlaps with.  No click, no wait and no message.
    """
    page = _page(context)
    assert page.created_note.is_displayed()


@step("User can select the note")
def user_can_select_the_note(context) -> None:
    """Open the created meeting and assert its modal is displayed.

    :param context: behave's ``Context``; supplies this scenario's session.
    :returns: ``None``.
    :raises AssertionError: When the modal body is not displayed.

    The port of ``Calendar.java:181-186``, in order:

    * ``:183`` ``calendarP.selectNote.click()``
    * ``:184`` wait 2 seconds for ``selectNote`` to be visible
    * ``:185`` ``Assert.assertTrue(calendarP.createdModele.isDisplayed())``

    ``selectNote`` is used by name here, as the Java line does, even though it
    shares its XPath with ``getNote``.  ``created_modele`` keeps the source's
    misspelling of "modal" (``CalendarP.java:65``); renaming it would break the
    name comparison and is not this port's business.
    """
    page = _page(context)
    page.select_note.click()
    wait_visible_element(page.select_note, 2)
    assert page.created_modele.is_displayed()


@step("User can edit the information")
def user_can_edit_the_information(context) -> None:
    """Replace the meeting's summary text and read the tags checkbox.

    :param context: behave's ``Context``; supplies this scenario's session.
    :returns: ``None``.

    The port of ``Calendar.java:187-197``, six statements in order:

    * ``:189`` ``calendarP.editButton.click()``
    * ``:190`` wait 2 seconds for ``editButton`` to be visible
    * ``:191`` ``calendarP.editText.clear()``
    * ``:192`` ``calendarP.editText.sendKeys("Hello My Friends")`` - the
      literal is reproduced exactly, capitalisation included
    * ``:193`` wait 2 seconds for ``editText`` to be visible
    * ``:194`` ``calendarP.tagsCheckbox.isSelected();``

    This is the class's only definition with two wait call sites, and it makes
    no assertion at all.

    .. important::

       **The final call's result is discarded, and it must stay discarded.**
       ``Calendar.java:194`` evaluates ``isSelected()`` and throws the boolean
       away, so the step's only sensitivity to the checkbox is whether the
       element can be found - it passes whether the box is ticked or not.
       Wrapping it in an ``assert`` would fail every scenario in which the
       checkbox is unticked, changing outcomes rather than porting them.  It is
       one of five such result-discarded calls in the suite (the other four are
       in ``Inventory.java``), and every one of them stays a bare statement.

    ``editButton`` is used by name as the Java line does, even though it shares
    its XPath with ``createButton``; ``EDIT_TEXT`` and ``TAGS_CHECKBOX`` are the
    source's hard-coded Odoo element ids, brittle by nature and preserved.
    """
    page = _page(context)
    page.edit_button.click()
    wait_visible_element(page.edit_button, 2)
    page.edit_text.clear()
    page.edit_text.send_keys("Hello My Friends")
    wait_visible_element(page.edit_text, 2)
    page.tags_checkbox.is_selected()


@step("User can save all edit")
def user_can_save_all_edit(context) -> None:
    """Save the edited meeting.

    :param context: behave's ``Context``; supplies this scenario's session.
    :returns: ``None``.

    The port of ``Calendar.java:198-201``, a single statement: ``:200``
    ``calendarP.saveButton.click()``.  The source neither waits nor asserts
    afterwards - the scenario ends on the click - so neither does this, and the
    step's only failure mode is the click itself.
    """
    page = _page(context)
    page.save_button.click()

