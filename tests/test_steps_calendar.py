"""Behavioural parity tests for ``features/steps/calendar_steps.py``.

Authority: ``step_definitions/Calendar.java`` (204 lines; thirteen definitions
annotated between ``:17`` and ``:198``, each line transcribed into
:data:`JAVA_ANNOTATION_LINES`) and ``pages/CalendarP.java`` (76 lines;
twenty-one ``@FindBy`` fields at ``:13-74``), at pinned revision
``47e9d697e4a9a85da889f94a846fdf47af28a240`` and held REFERENCE by AAP 0.2.1.
Every expectation is transcribed into a constant below and cited to its Java
line, so the suite needs no reference checkout and nothing here is derived
from the port it judges.

This module discharges AAP 0.4.1's per-module parity obligation for the
Calendar area: for every step method of the paired class, the same observable
operations in the same order - the same page-object locators, wait target and
timeout, literals and expected values, assertion subject and message text,
and the same no-ops.  A method with no assertion here is a gap, so
:data:`DEFINITIONS` enumerates all thirteen and the census fails closed on a
fourteenth, a rename or a misrouted registration.  What that fixes here:

* **Order, not membership.**  ``Calendar.java:19-20`` clicks *then* waits, so
  each definition's whole log is compared entry by entry, the wait and delay
  recorders appending into the driver's own log to give each one a position.
* **Waits carry locators.**  The ten ``visibilityOf`` sites (``:20`` through
  ``:193``, enumerated at :data:`WAIT_SITE_COUNT`) share the 2-second timeout
  of ``:15``, and each is asserted to receive the ``CalendarPage`` constant
  itself: the lookup belongs inside ``visibility_of_element_located``, per
  poll, where those 2 seconds gate it rather than the 10-second implicit wait
  ``app/automation/driver.py`` sets.
* **Two fixed delays, by position.**  ``:99`` and ``:155`` are asserted at
  their call sites, as AAP 0.4.1 requires of the preserved sleeps.
* **Duplicated selectors told apart by name.**  ``CalendarP.java:49``/``:58``
  and ``:52``/``:73`` declare one XPath twice each, so the driver log cannot
  distinguish those four fields; the AST-parsed page attributes can, and are
  asserted per definition.

The step module is never imported: ``tests/conftest.py``'s registry loaded it
and bodies run through ``resolve_step(phrase).run(fake_context)`` against its
``StubDriver``, with the wait helpers and ``time`` patched in module globals,
so nothing opens a browser, sleeps for real or writes to disk.
"""

from __future__ import annotations

import ast
import functools
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Final, Mapping, NamedTuple

import pytest

from app.pages.calendar_page import CalendarPage

# --------------------------------------------------------------------------
# Locations
# --------------------------------------------------------------------------

#: Repository root, from this file's own position: tests/ -> repository root.
REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]

#: The step module under test.  Read as *text* and parsed, never imported.
STEP_MODULE_PATH: Final[Path] = REPO_ROOT / "features" / "steps" / "calendar_steps.py"

#: The feature this module's definitions serve.
FEATURE_PATH: Final[Path] = REPO_ROOT / "features" / "Calendar.feature"

#: The step module's name as behave reports it in ``StepMatch.module_name``.
STEP_MODULE_NAME: Final[str] = "calendar_steps"

#: The module that owns ``Calendar.feature``'s Background phrase.  It is
#: ``Session.java:12``'s definition and is deliberately **not** redeclared by
#: ``calendar_steps.py``.
SESSION_MODULE_NAME: Final[str] = "session_steps"

# --------------------------------------------------------------------------
# The Java authority, transcribed.  Every literal below cites its source line.
# --------------------------------------------------------------------------

#: ``Calendar.java:15`` - ``new WebDriverWait(Driver.getDriver(), 2)``.  One
#: wait object serves the whole class, so all ten call sites share this
#: timeout and the port writes it as the literal ``2`` at each of them.
WAIT_TIMEOUT_SECONDS: Final[int] = 2

#: Number of ``wait.until(...)`` call sites in ``Calendar.java``:
#: ``:20``, ``:26``, ``:32``, ``:38``, ``:43``, ``:52``, ``:109``, ``:184``,
#: ``:190`` and ``:193``.
WAIT_SITE_COUNT: Final[int] = 10

#: ``Calendar.java:99`` and ``:155`` - ``Thread.sleep(3000)``, expressed in
#: seconds because the Python binding takes seconds.
SLEEP_SECONDS: Final[int] = 3

#: Exactly two fixed delays exist in the class, and nowhere else in it.
SLEEP_SITE_COUNT: Final[int] = 2

#: ``Calendar.java``'s seven assertions: ``:46``, ``:101``, ``:157``, ``:163``,
#: ``:174``, ``:179`` and ``:185``.
ASSERTION_COUNT: Final[int] = 7

#: ``Calendar.java:44`` - the expected page title.  Odoo vocabulary preserved
#: per AAP Conflict 8.
EXPECTED_TITLE: Final[str] = "Meetings - Odoo"

#: ``Calendar.java:46`` - the class's only assertion message, byte for byte
#: and with **no trailing space**.  ``LoginSD.java:46`` and ``LogOutSD.java:27``
#: use a variant that does end in a space; this module must not acquire theirs.
TITLE_ASSERTION_MESSAGE: Final[str] = "The title is not same as the expected!"

#: ``Calendar.java:192`` - the replacement summary text, capitalisation
#: included.
EDIT_TEXT_LITERAL: Final[str] = "Hello My Friends"

#: ``Calendar.java:98`` - ``"Meetings (" + month + " " + dayCalendar + ", " +
#: yearCalendar + ")"``.  Day view carries the day **and** a comma.
DAY_VIEW_TEMPLATE: Final[str] = "Meetings ({month} {day}, {year})"

#: ``Calendar.java:154`` - ``"Meetings (" + month + " " + yearCalendar + ")"``.
#: Month view carries **neither** the day nor a comma, because a month view
#: shows no single day.  Copying the day-view format here is the likeliest
#: single defect in the port, which is why the two formats are also compared
#: against each other directly.
MONTH_VIEW_TEMPLATE: Final[str] = "Meetings ({month} {year})"

#: The twelve month names of both switches (``Calendar.java:59-96`` and
#: ``:115-152``), in Java case order.
MONTH_NAMES: Final[tuple[str, ...]] = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

#: ``data-month`` is **zero-based** and ``Calendar.java:54``/``:110`` add one
#: before the switch, so ``data-month="0"`` is January.  Dropping that ``+ 1``
#: would shift every month by one and is the easiest way to break both big
#: definitions; this mapping is what catches it.
DATA_MONTH_TO_NAME: Final[Mapping[str, str]] = MappingProxyType(
    {str(index): name for index, name in enumerate(MONTH_NAMES)}
)

#: The switches have no ``default`` case and ``Calendar.java:57``/``:113``
#: initialise ``String month = ""`` before them, so any month number outside
#: 1 to 12 leaves the name empty.  These four ``data-month`` values all land
#: outside it: ``"-1"`` -> 0, ``"12"`` -> 13, ``"14"`` -> 15, ``"99"`` -> 100.
OUT_OF_RANGE_DATA_MONTHS: Final[tuple[str, ...]] = ("-1", "12", "14", "99")

#: The month name the missing ``default`` case yields.
NO_DEFAULT_MONTH_NAME: Final[str] = ""

#: ``CalendarP.java:49`` and ``:58`` - one XPath, two fields
#: (``createButton`` and ``editButton``).  Asserted as a literal because this
#: value's *identity across two names* is itself under test.
DUPLICATE_PRIMARY_BUTTON_XPATH: Final[str] = "//button[@class='btn btn-sm btn-primary']"

#: ``CalendarP.java:52`` and ``:73`` - one XPath, two fields (``getNote`` and
#: ``selectNote``).
DUPLICATE_NAME_FIELD_XPATH: Final[str] = "//div[@class='o_field_name o_field_type_char']"

#: ``Calendar.java:3-10`` is the class's whole import list: ``CalendarP``,
#: ``Driver``, the three Gherkin annotations, ``org.junit.Assert``,
#: ``ExpectedConditions`` and ``WebDriverWait`` - **neither**
#: ``org.openqa.selenium.Keys`` **nor**
#: ``org.openqa.selenium.interactions.Actions``, which only
#: ``Crm.java:9-10``, ``Notes.java:9-10`` and ``Sales.java:9`` import; no
#: ``org.openqa.selenium.By``, which only ``LoginSD.java:10`` imports for the
#: direct lookup at ``LoginSD.java:56``; and no ``ConfigurationReader``, which
#: only ``EmployeeStage.java:4``, ``LoginSD.java:4`` and ``Session.java:4``
#: import.  Each of these names in the port would be a capability the source
#: lacks.
FORBIDDEN_STEP_MODULE_NAMES: Final[frozenset[str]] = frozenset(
    {"Keys", "ActionChains", "press_keys", "action_chain", "By", "config"}
)

#: The two helpers of ``app/automation/waits.py`` that port
#: ``ExpectedConditions.visibilityOf`` (``Calendar.java:20`` and its nine
#: siblings).  Both wait on ``visibility_of_element_located``, so both take the
#: **locator** and resolve it inside the predicate on every poll; a wait
#: through any other name, or on anything but a locator, is not this class's
#: wait.  Which of the two a call site chooses is not parity - the target, the
#: 2-second timeout of ``Calendar.java:15`` and the position are.
VISIBILITY_WAIT_HELPERS: Final[frozenset[str]] = frozenset(
    {"wait_visible_element", "wait_visible"}
)

# --------------------------------------------------------------------------
# Feature-file authority (``features/Calendar.feature``, carried over verbatim)
# --------------------------------------------------------------------------

#: ``Calendar.feature:1`` - the feature-level tag.  It is *not* ``@Smoke``, so
#: a default run does not select this feature and ``--tags=@Calendar`` does.
FEATURE_TAG_LINE: Final[str] = "@Calendar"

#: ``Calendar.feature:2`` - the feature header, "Testinium" vocabulary intact.
FEATURE_HEADER_LINE: Final[str] = "Feature: Testinium app Calendar Module"

#: ``Calendar.feature:9`` - the Background step, owned by ``session_steps``.
BACKGROUND_PHRASE: Final[str] = "User login to test other features"

#: ``Calendar.feature:29-31`` - the outline's ``Examples`` block and its single
#: row.  The value carries an internal space and is used unquoted.
EXAMPLES_HEADER: Final[str] = "Examples: Test name"
EXAMPLES_COLUMN: Final[str] = "test"
EXAMPLES_NOTE: Final[str] = "Test test"
EXAMPLES_ROW_LINE_NUMBER: Final[int] = 31

#: ``Calendar.feature:37`` - the same definition reached with a bare literal.
LITERAL_NOTE: Final[str] = "Test"
LITERAL_NOTE_LINE_NUMBER: Final[int] = 37

#: Every line of ``Calendar.feature`` that carries a step, in file order: the
#: Background at 9, then the four scenarios at 11, 18, 23 and 33.  Pinned so
#: that a step added to or removed from the feature fails here rather than
#: silently going unported.
FEATURE_STEP_LINE_NUMBERS: Final[tuple[int, ...]] = (
    9,
    12,
    13,
    14,
    15,
    16,
    19,
    20,
    21,
    24,
    25,
    26,
    27,
    34,
    35,
    36,
    37,
    38,
    39,
    40,
    41,
)

#: Gherkin step keywords, with their trailing space so that a description line
#: beginning with one of these words cannot be mistaken for a step.
GHERKIN_KEYWORDS: Final[tuple[str, ...]] = ("Given ", "When ", "Then ", "And ", "But ", "* ")

# --------------------------------------------------------------------------
# Stub-driver log vocabulary
# --------------------------------------------------------------------------

#: ``tests/conftest.py`` prefixes every element operation with this.
ELEMENT_PREFIX: Final[str] = "element."

#: The marker the fake clock appends into the driver's own ordered log.  It is
#: not a driver operation and is named so that it cannot be mistaken for one;
#: putting it in that list is what turns "a delay happened" into "the delay
#: happened *here*".
SLEEP_MARKER: Final[str] = "time.sleep"

#: The marker the wait recorder appends into that same ordered log, named after
#: the Java construct it ports (``wait.until(...)``, ``Calendar.java:20`` and
#: its nine siblings) and likewise impossible to mistake for a driver
#: operation.
#:
#: It exists because the wait helper takes a **locator**: it resolves its
#: element inside its own predicate, so - unlike an element-based call, whose
#: argument had to be looked up first - it leaves no ``find_element`` behind to
#: mark where in the body it happened.  Without this marker "click, then wait
#: on the same element" (``Calendar.java:19-20``) and the reverse order would
#: produce identical expectations, and the reversal is precisely the parity
#: break these tests exist to catch: the wait there is settle time *after* the
#: click, not a pre-condition for it.
WAIT_MARKER: Final[str] = "wait.until"

Locator = tuple[str, str]
Call = tuple[str, tuple[Any, ...]]


def _find(locator: Locator) -> Call:
    """One ``find_element`` entry: the page object re-resolving an accessor."""
    return ("find_element", locator)


def _click(locator: Locator) -> Call:
    return (f"{ELEMENT_PREFIX}click", (locator,))


def _read_text(locator: Locator) -> Call:
    """One ``WebElement.getText()`` entry."""
    return (f"{ELEMENT_PREFIX}text", (locator,))


def _read_attribute(locator: Locator, name: str) -> Call:
    """One ``WebElement.getAttribute(name)`` entry."""
    return (f"{ELEMENT_PREFIX}get_attribute", (locator, name))


def _is_displayed(locator: Locator) -> Call:
    """One ``WebElement.isDisplayed()`` entry."""
    return (f"{ELEMENT_PREFIX}is_displayed", (locator,))


def _is_selected(locator: Locator) -> Call:
    """One ``WebElement.isSelected()`` entry."""
    return (f"{ELEMENT_PREFIX}is_selected", (locator,))


def _clear(locator: Locator) -> Call:
    return (f"{ELEMENT_PREFIX}clear", (locator,))


def _send_keys(locator: Locator, *values: Any) -> Call:
    """One ``WebElement.sendKeys(...)`` entry, arguments unflattened."""
    return (f"{ELEMENT_PREFIX}send_keys", (locator, *values))


def _read_title() -> Call:
    """One ``Driver.getDriver().getTitle()`` entry (``Calendar.java:45``)."""
    return ("title", ())


def _sleep(seconds: float = SLEEP_SECONDS) -> Call:
    """One fixed delay, interleaved in the driver's ordered log."""
    return (SLEEP_MARKER, (seconds,))


def _is_locator(value: Any) -> bool:
    """Report whether *value* has the shape ``app/pages`` declares a locator in.

    :param value: Any object.
    :returns: ``True`` for a two-element tuple of strings, which is what every
        upper-case :class:`~app.pages.calendar_page.CalendarPage` constant is
        and what ``expected_conditions`` unpacks.  A resolved web element is
        not one, which is the distinction the wait recorder enforces.
    """
    return (
        isinstance(value, tuple)
        and len(value) == 2
        and all(isinstance(part, str) for part in value)
    )


def _wait(locator: Locator, timeout: int = WAIT_TIMEOUT_SECONDS) -> Call:
    """One explicit wait, interleaved in the driver's ordered log.

    :param locator: The locator the call site handed the helper - a
        ``CalendarPage`` upper-case constant, resolved inside the predicate on
        every poll rather than once beforehand.
    :param timeout: Seconds the call site supplied.  Defaults to the 2 of
        ``Calendar.java:15``, which every one of the class's ten sites carries
        and which the helper has no default for.
    """
    return (WAIT_MARKER, (locator, timeout))


# --------------------------------------------------------------------------
# The census: all thirteen definitions of ``Calendar.java:17-201``, in source
# order - annotation at :17, :23, :29, :35, :41, :49, :106, :160, :166, :177,
# :181, :187 and :198
# --------------------------------------------------------------------------


class Definition(NamedTuple):
    """One ``Calendar.java`` step definition and everything parity fixes about it.

    Every field is transcribed from ``Calendar.java:17-201`` and is what one or
    more tests below compare the port against.  Nothing here is computed from
    the port, so the port cannot make it agree with itself.
    """

    #: Line of the Cucumber annotation in ``Calendar.java``, which is also this
    #: entry's identity: one of ``:17``, ``:23``, ``:29``, ``:35``, ``:41``,
    #: ``:49``, ``:106``, ``:160``, ``:166``, ``:177``, ``:181``, ``:187``,
    #: ``:198``.
    java_line: int

    #: The annotation itself.  Recorded because it is the reason the port
    #: registers everything with ``@step``: ``@And`` has no behave counterpart
    #: and ``@Then`` at ``:166`` is reached as an ``And`` from the feature.
    java_keyword: str

    #: The Java method name.
    java_method: str

    #: The text inside the port's ``@step`` decorator.
    pattern: str

    #: A concrete Gherkin phrase that must resolve to this definition.
    phrase: str

    #: The port's function name.
    function: str

    #: The test in this module that claims this definition.
    test_name: str

    #: Page-object attributes the body touches, in source order, spelled as the
    #: body spells them: a **lower-case accessor** where the body operates on
    #: the resolved element, and the **upper-case locator constant** where it
    #: hands the locator to a wait helper (``app/automation/waits.py`` takes the
    #: locator and resolves it inside the predicate, once per poll).  This is
    #: what tells ``createButton`` from ``editButton`` and ``getNote`` from
    #: ``selectNote``, whose XPaths are identical - at wait sites as much as at
    #: click sites.
    page_attributes: tuple[str, ...]

    #: ``wait.until(...)`` call sites in the Java method.
    waits: int

    #: ``Thread.sleep`` call sites in the Java method.
    sleeps: int

    #: One entry per assertion, in source order: the assertion's message, or
    #: ``None`` for a two-argument JUnit call that carries none.
    assertion_messages: tuple[str | None, ...]

    @property
    def assertions(self) -> int:
        """How many assertions the Java method makes."""
        return len(self.assertion_messages)

    @property
    def accessor_reads(self) -> tuple[str, ...]:
        """The lower-case accessor reads of :attr:`page_attributes`, in order.

        One driver lookup each: every accessor re-resolves on access, so this
        is exactly the ``find_element`` count the run must show.
        """
        return tuple(name for name in self.page_attributes if not name.isupper())

    @property
    def locator_reads(self) -> tuple[str, ...]:
        """The upper-case locator constants of :attr:`page_attributes`, in order.

        A class attribute read, so it performs **no** lookup of its own - the
        wait helper resolves the locator inside its predicate instead.  One per
        wait call site and nowhere else, which is what
        :func:`_assert_step_shape` cross-checks against :attr:`waits`.
        """
        return tuple(name for name in self.page_attributes if name.isupper())


DEFINITIONS: Final[tuple[Definition, ...]] = (
    Definition(
        java_line=17,
        java_keyword="@When",
        java_method="user_clicks_on_the_calendar_dashboard",
        pattern="User click on the calendar dashboard",
        phrase="User click on the calendar dashboard",
        function="user_clicks_on_the_calendar_dashboard",
        test_name="test_calendar_dashboard_clicks_then_waits_on_the_same_element",
        page_attributes=("calendar_button", "CALENDAR_BUTTON"),
        waits=1,
        sleeps=0,
        assertion_messages=(),
    ),
    Definition(
        java_line=23,
        java_keyword="@When",
        java_method="user_clicks_on_day_button",
        pattern="User click on day button",
        phrase="User click on day button",
        function="user_clicks_on_day_button",
        test_name="test_day_button_clicks_then_waits",
        page_attributes=("day", "DAY"),
        waits=1,
        sleeps=0,
        assertion_messages=(),
    ),
    Definition(
        java_line=29,
        java_keyword="@When",
        java_method="user_clicks_on_week_button",
        pattern="User click on week button",
        phrase="User click on week button",
        function="user_clicks_on_week_button",
        test_name="test_week_button_clicks_then_waits",
        page_attributes=("week", "WEEK"),
        waits=1,
        sleeps=0,
        assertion_messages=(),
    ),
    Definition(
        java_line=35,
        java_keyword="@When",
        java_method="user_clicks_on_month_button",
        pattern="User click on month button",
        phrase="User click on month button",
        function="user_clicks_on_month_button",
        test_name="test_month_button_clicks_then_waits",
        page_attributes=("month", "MONTH"),
        waits=1,
        sleeps=0,
        assertion_messages=(),
    ),
    Definition(
        java_line=41,
        java_keyword="@Then",
        java_method="user_should_see_the_last_stage_of_calendar_view",
        pattern="User should see the last stage of calendar view",
        phrase="User should see the last stage of calendar view",
        function="user_should_see_the_last_stage_of_calendar_view",
        test_name="test_last_stage_waits_on_the_module_then_compares_the_driver_title",
        page_attributes=("CALENDAR_MODULE",),
        waits=1,
        sleeps=0,
        assertion_messages=(TITLE_ASSERTION_MESSAGE,),
    ),
    Definition(
        java_line=49,
        java_keyword="@When",
        java_method="user_clicks_day_on_the_calendar_and_display_day",
        pattern="User click day on the calendar and display day",
        phrase="User click day on the calendar and display day",
        function="user_clicks_day_on_the_calendar_and_display_day",
        test_name="test_day_view_builds_the_expected_string_for_every_month",
        page_attributes=(
            "day",
            "DAY",
            "day_calendar",
            "month_and_year_calendar",
            "month_and_year_calendar",
            "date_actual",
        ),
        waits=1,
        sleeps=1,
        assertion_messages=(None,),
    ),
    Definition(
        java_line=106,
        java_keyword="@Then",
        java_method="user_click_month_on_the_calendar_and_display_month",
        pattern="User click month on the calendar and display month",
        phrase="User click month on the calendar and display month",
        function="user_click_month_on_the_calendar_and_display_month",
        test_name="test_month_view_builds_the_expected_string_for_every_month",
        page_attributes=(
            "month",
            "MONTH",
            "month_and_year_calendar",
            "month_and_year_calendar",
            "date_actual",
        ),
        waits=1,
        sleeps=1,
        assertion_messages=(None,),
    ),
    Definition(
        java_line=160,
        java_keyword="@And",
        java_method="userClickOnDesiredDateTime",
        pattern="User click on desired date time",
        phrase="User click on desired date time",
        function="user_click_on_desired_date_time",
        test_name="test_desired_date_time_clicks_the_box_then_asserts_the_modal_header",
        page_attributes=("date_box", "create_note"),
        waits=0,
        sleeps=0,
        assertion_messages=(None,),
    ),
    Definition(
        java_line=166,
        java_keyword="@Then",
        java_method="userEntersInTheBoxAndClicksTheCreateButton",
        pattern='User enters "{note}" in the box and clicks the create button',
        phrase=f'User enters "{EXAMPLES_NOTE}" in the box and clicks the create button',
        function="user_enters_in_the_box_and_clicks_the_create_button",
        test_name="test_entering_a_note_types_it_creates_it_then_reads_it_back",
        page_attributes=("summary_box", "create_button", "get_note"),
        waits=0,
        sleeps=0,
        assertion_messages=(None,),
    ),
    Definition(
        java_line=177,
        java_keyword="@When",
        java_method="user_can_see_all_the_note",
        pattern="User can see all the note",
        phrase="User can see all the note",
        function="user_can_see_all_the_note",
        test_name="test_can_see_all_the_note_only_asserts_visibility",
        page_attributes=("created_note",),
        waits=0,
        sleeps=0,
        assertion_messages=(None,),
    ),
    Definition(
        java_line=181,
        java_keyword="@When",
        java_method="user_can_select_the_note",
        pattern="User can select the note",
        phrase="User can select the note",
        function="user_can_select_the_note",
        test_name="test_can_select_the_note_clicks_waits_then_asserts_the_modal",
        page_attributes=("select_note", "SELECT_NOTE", "created_modele"),
        waits=1,
        sleeps=0,
        assertion_messages=(None,),
    ),
    Definition(
        java_line=187,
        java_keyword="@When",
        java_method="user_can_edit_the_information",
        pattern="User can edit the information",
        phrase="User can edit the information",
        function="user_can_edit_the_information",
        test_name=(
            "test_can_edit_the_information_clears_types_waits_twice_and_discards_the_checkbox"
        ),
        page_attributes=(
            "edit_button",
            "EDIT_BUTTON",
            "edit_text",
            "edit_text",
            "EDIT_TEXT",
            "tags_checkbox",
        ),
        waits=2,
        sleeps=0,
        assertion_messages=(),
    ),
    Definition(
        java_line=198,
        java_keyword="@Then",
        java_method="user_can_save_all_edit",
        pattern="User can save all edit",
        phrase="User can save all edit",
        function="user_can_save_all_edit",
        test_name="test_can_save_all_edit_clicks_save_and_nothing_else",
        page_attributes=("save_button",),
        waits=0,
        sleeps=0,
        assertion_messages=(),
    ),
)

#: The thirteen annotation lines of ``Calendar.java:17-198``, ascending -
#: source order.  Transcribed separately from :data:`DEFINITIONS` so that the
#: census's own ordering is checked against the file rather than against
#: itself.
JAVA_ANNOTATION_LINES: Final[tuple[int, ...]] = (
    17,
    23,
    29,
    35,
    41,
    49,
    106,
    160,
    166,
    177,
    181,
    187,
    198,
)

#: Census lookup by phrase, so a per-definition test names its own entry
#: without re-deriving it.
BY_PHRASE: Final[Mapping[str, Definition]] = MappingProxyType(
    {definition.phrase: definition for definition in DEFINITIONS}
)


def _definition(phrase: str) -> Definition:
    """The census entry for *phrase*.

    :param phrase: A concrete Gherkin phrase carried by the census.
    :returns: Its :class:`Definition`.
    :raises AssertionError: When the phrase is not in the census, which would
        mean a test is asserting against a definition nobody enumerated.
    """
    definition = BY_PHRASE.get(phrase)

    if definition is None:
        raise AssertionError(
            f"{phrase!r} is not in DEFINITIONS. Every driven phrase must be "
            f"enumerated in the census, or the census is not the whole class"
        )

    return definition


# --------------------------------------------------------------------------
# Source-level helpers.  The step module is read and parsed, never imported.
# --------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def _step_module_source() -> str:
    """The step module's text.

    :returns: ``features/steps/calendar_steps.py`` decoded as UTF-8.
    :raises AssertionError: When the file is absent - the port would then have
        no Calendar steps at all, and saying so plainly beats an
        ``OSError`` from deep inside a helper.
    """
    if not STEP_MODULE_PATH.is_file():
        raise AssertionError(f"step module not found: {STEP_MODULE_PATH}")

    return STEP_MODULE_PATH.read_text(encoding="utf-8")


@functools.lru_cache(maxsize=1)
def _step_module_tree() -> ast.Module:
    """The step module's parsed syntax tree.

    Cached because every structural test below walks it and parsing is the
    only filesystem-touching work this module does.

    :returns: The parsed module.
    """
    return ast.parse(_step_module_source(), filename=str(STEP_MODULE_PATH))


def _decorator_name(node: ast.expr) -> str | None:
    """The bare name of a decorator, whether it is called or not.

    Handles ``@step("...")``, ``@behave.step("...")``, ``@step`` and
    ``@behave.step``, which is every shape a Gherkin registration can take.

    :param node: The decorator expression.
    :returns: The name, or ``None`` when the shape is not a name or attribute.
    """
    target = node.func if isinstance(node, ast.Call) else node

    if isinstance(target, ast.Name):
        return target.id

    if isinstance(target, ast.Attribute):
        return target.attr

    return None


def _decorator_pattern(node: ast.expr) -> str | None:
    """The string literal a registration decorator was called with.

    :param node: The decorator expression.
    :returns: The pattern, or ``None`` when the decorator takes no string.
    """
    if not isinstance(node, ast.Call) or not node.args:
        return None

    first = node.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value

    return None


@functools.lru_cache(maxsize=1)
def _registered_functions() -> Mapping[str, tuple[str, ast.FunctionDef]]:
    """Every Gherkin-registered function in the step module.

    Keyed by function name, valued ``(decorator name, function node)``.  All
    four registration decorators are collected rather than only ``@step``, so
    a definition bound to a single keyword is *found and reported* by
    :func:`test_step_module_registers_exactly_the_census_functions` instead of
    silently disappearing from the census comparison.

    :returns: An immutable mapping of the registered definitions.
    """
    registrations: dict[str, tuple[str, ast.FunctionDef]] = {}

    for node in _step_module_tree().body:
        if not isinstance(node, ast.FunctionDef):
            continue

        for decorator in node.decorator_list:
            name = _decorator_name(decorator)

            if name in {"step", "given", "when", "then"}:
                registrations[node.name] = (name, node)
                break

    return MappingProxyType(registrations)


def _function_node(function: str) -> ast.FunctionDef:
    """The parsed body of one registered step function.

    :param function: The function's name.
    :returns: Its ``FunctionDef`` node.
    :raises AssertionError: When the step module does not register it.
    """
    registration = _registered_functions().get(function)

    if registration is None:
        raise AssertionError(
            f"{function!r} is not registered in {STEP_MODULE_PATH.name}. "
            f"Registered: {sorted(_registered_functions())}"
        )

    return registration[1]


def _page_variable(node: ast.FunctionDef) -> str:
    """The local name the body binds its page object to.

    Read from the body rather than assumed, because the name is the port's
    choice and not parity: what parity fixes is which *page fields* the body
    touches.  Falls back to ``"page"``, the name every module in this port
    uses.

    :param node: The function to inspect.
    :returns: The variable name the page object is bound to.
    """
    for statement in ast.walk(node):
        if not isinstance(statement, ast.Assign):
            continue

        value = statement.value

        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id.endswith("page")
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
        ):
            return statement.targets[0].id

    return "page"


def _page_attribute_sites(function: str) -> tuple[tuple[int, str], ...]:
    """Page-object accessor reads in one step body as ``(line, accessor)`` pairs.

    The check the driver log cannot make.  ``CalendarP.java:49`` and ``:58``
    declare ``createButton`` and ``editButton`` with one identical XPath, and
    ``:52`` and ``:73`` declare ``getNote`` and ``selectNote`` with another, so
    two different fields produce byte-identical ``find_element`` entries; only
    the source can say which name a call site used, and ``Calendar.java:172``,
    ``:174``, ``:183-:185`` and ``:189-:190`` fix that per line.  The line
    numbers come along because the fixed-delay placement assertions compare
    them against the delay's own line.

    :param function: The registered function to inspect.
    :returns: ``(line, accessor)`` in source order, with repeats kept - a
        second access is a second ``find_element`` and part of the sequence.
    """
    node = _function_node(function)
    variable = _page_variable(node)
    found: list[tuple[int, int, str]] = []

    for child in ast.walk(node):
        if (
            isinstance(child, ast.Attribute)
            and isinstance(child.value, ast.Name)
            and child.value.id == variable
        ):
            found.append((child.lineno, child.col_offset, child.attr))

    return tuple((line, attribute) for line, _, attribute in sorted(found))


def _page_attributes_used(function: str) -> tuple[str, ...]:
    """Page-object accessors one step body touches, in source order.

    :param function: The registered function to inspect.
    :returns: The accessor names, repeats kept.
    """
    return tuple(attribute for _, attribute in _page_attribute_sites(function))


def _calls_named(node: ast.AST, predicate: Callable[[str], bool]) -> tuple[ast.Call, ...]:
    """Every call in *node* whose callee name satisfies *predicate*, in source order.

    :param node: The subtree to search.
    :param predicate: Applied to the callee's bare name.
    :returns: The matching calls, ordered by position.
    """
    found: list[tuple[int, int, ast.Call]] = []

    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue

        name = _decorator_name(child)

        if name is not None and predicate(name):
            found.append((child.lineno, child.col_offset, child))

    return tuple(call for _, _, call in sorted(found, key=lambda entry: entry[:2]))


def _wait_calls(function: str) -> tuple[ast.Call, ...]:
    """The wait call sites in one step body, in source order."""
    return _calls_named(_function_node(function), lambda name: name.startswith("wait"))


def _sleep_calls(function: str) -> tuple[ast.Call, ...]:
    """The fixed-delay call sites in one step body, in source order."""
    return _calls_named(_function_node(function), lambda name: name == "sleep")


def _literal_timeout(call: ast.Call) -> Any:
    """The timeout literal a wait call site passes, positionally or by keyword.

    :param call: A wait call.
    :returns: The literal value, or ``None`` when the call supplies no timeout
        at all - which fails the assertion that reads it, as it should, since
        ``Calendar.java:15`` fixes the timeout for every site.
    """
    for keyword in call.keywords:
        if keyword.arg == "timeout" and isinstance(keyword.value, ast.Constant):
            return keyword.value.value

    if len(call.args) > 1 and isinstance(call.args[1], ast.Constant):
        return call.args[1].value

    return None


def _assert_statements(function: str) -> tuple[ast.Assert, ...]:
    """Every ``assert`` in one step body, in source order."""
    node = _function_node(function)
    found = [child for child in ast.walk(node) if isinstance(child, ast.Assert)]
    return tuple(sorted(found, key=lambda child: (child.lineno, child.col_offset)))


def _assert_messages(function: str) -> tuple[str | None, ...]:
    """The message literal of each ``assert`` in one step body, in source order.

    :param function: The registered function to inspect.
    :returns: One entry per assertion: its message, or ``None`` when it has
        none.  A non-literal message is reported as its unparsed source so a
        failure names what it found.
    """
    messages: list[str | None] = []

    for statement in _assert_statements(function):
        if statement.msg is None:
            messages.append(None)
        elif isinstance(statement.msg, ast.Constant) and isinstance(statement.msg.value, str):
            messages.append(statement.msg.value)
        else:
            messages.append(ast.unparse(statement.msg))

    return tuple(messages)


@functools.lru_cache(maxsize=1)
def _imports() -> tuple[tuple[str, str | None], ...]:
    """Every import in the step module as ``(module, imported name)`` pairs.

    ``import time`` yields ``("time", None)``; ``from app.automation import
    wait_visible_element`` yields ``("app.automation",
    "wait_visible_element")``.  Both forms are covered because the import
    boundary AAP 0.4.2 states is about the dependency, not the syntax.

    :returns: The pairs, in source order.
    """
    pairs: list[tuple[str, str | None]] = []

    for node in ast.walk(_step_module_tree()):
        if isinstance(node, ast.Import):
            pairs.extend((alias.name, None) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            pairs.extend((module, alias.name) for alias in node.names)

    return tuple(pairs)


def _expected_python_name(java_method: str) -> str:
    """The port's function name for a Java method name.

    AAP 0.4.1 keeps each Python function named after its Java method so that
    ``target/cucumber.json``'s ``match.location`` names a recognisable
    counterpart.  Eleven of the thirteen methods are already snake_case and
    carry over unchanged; ``userClickOnDesiredDateTime`` (``:161``) and
    ``userEntersInTheBoxAndClicksTheCreateButton`` (``:167``) are camelCase and
    become the snake_case forms of the same words.

    :param java_method: The Java method name.
    :returns: The expected Python function name.
    """
    characters: list[str] = []

    for position, character in enumerate(java_method):
        if character.isupper() and position:
            characters.append("_")

        characters.append(character.lower())

    return "".join(characters)


# --------------------------------------------------------------------------
# Feature-file helpers
# --------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def _feature_lines() -> tuple[str, ...]:
    """``features/Calendar.feature`` split into lines, newlines stripped.

    :returns: The lines, index 0 being file line 1.
    :raises AssertionError: When the feature file is absent.
    """
    if not FEATURE_PATH.is_file():
        raise AssertionError(f"feature file not found: {FEATURE_PATH}")

    return tuple(FEATURE_PATH.read_text(encoding="utf-8").splitlines())


@functools.lru_cache(maxsize=1)
def _feature_steps() -> tuple[tuple[int, str, str], ...]:
    """Every step in the feature as ``(line number, keyword, phrase)``.

    The outline placeholder ``<test>`` is substituted with
    :data:`EXAMPLES_NOTE`, which is what the engine does with the single
    ``Examples`` row at ``Calendar.feature:31`` - so the phrases returned here
    are the concrete ones a run has to resolve.

    :returns: The steps in file order.
    """
    steps: list[tuple[int, str, str]] = []

    for number, raw in enumerate(_feature_lines(), start=1):
        text = raw.strip()

        for keyword in GHERKIN_KEYWORDS:
            if text.startswith(keyword):
                phrase = text[len(keyword) :].strip()
                concrete = phrase.replace(f"<{EXAMPLES_COLUMN}>", EXAMPLES_NOTE)
                steps.append((number, keyword.strip(), concrete))
                break

    return tuple(steps)


# --------------------------------------------------------------------------
# The harness: ordered stubs, a tolerant wait recorder and a fake clock
# --------------------------------------------------------------------------


class _WaitRecorder:
    """Records every explicit wait a step body performs, in its own position.

    A visibility wait takes a **locator** and nothing else
    (``app/automation/waits.py``'s ``wait_visible_element``, which resolves it
    inside ``visibility_of_element_located``), so this recorder captures the
    target exactly as the call site passed it and applies no normalisation.
    An element handed to a wait instead would have been resolved *before* the
    wait existed, under the 10-second implicit wait ``app/automation/driver.py``
    sets, and the 2 seconds of ``Calendar.java:15`` would never gate that
    lookup - which is why the recorded target is compared, by identity, against
    the ``CalendarPage`` constant the site names rather than against anything
    an element could also satisfy.  The timeout is read positionally or as
    ``timeout=``, the two spellings of one argument.

    Like :class:`_FakeClock`, it appends into the **driver's own ordered log**
    as well as into its own list, because the helper the port calls takes a
    locator and therefore performs no lookup the log would otherwise show.
    That marker is what keeps each definition's whole operation *sequence*
    asserted rather than only its multiset: the ten waits of this class sit at
    fixed points among the clicks and reads around them.
    """

    #: The keyword name the wait helpers give their locator parameter, for a
    #: call site that spells it out instead of passing it first.
    TARGET_KEYWORDS: Final[tuple[str, ...]] = ("locator",)

    def __init__(self, log: list[Call]) -> None:
        """Bind the recorder to the driver log it interleaves with.

        :param log: The recorder's mutable ``calls`` list.
        """
        self._log = log

        #: ``(helper name, waited-on locator, timeout)`` per call, in order.
        self.records: list[tuple[str, Any, Any]] = []

    def bind(self, name: str) -> Callable[..., Any]:
        """A recorder standing in for the helper called *name*.

        :param name: The global name being replaced, carried into the record so
            that a test can assert the wait was a *visibility* wait.
        :returns: The replacement callable.
        """

        def _recorder(*args: Any, **kwargs: Any) -> Any:
            target: Any = args[0] if args else None

            if target is None:
                for keyword in self.TARGET_KEYWORDS:
                    if keyword in kwargs:
                        target = kwargs[keyword]
                        break

            if "timeout" in kwargs:
                timeout = kwargs["timeout"]
            elif len(args) > 1:
                timeout = args[1]
            else:
                timeout = None

            assert _is_locator(target), (
                f"{name} was called with {target!r}, which is not a locator. "
                f"Calendar.java's ten visibility waits port onto "
                f"visibility_of_element_located, so every call site passes a "
                f"CalendarPage constant and the lookup happens inside the "
                f"predicate, under the {WAIT_TIMEOUT_SECONDS} seconds of "
                f"Calendar.java:15 rather than under the driver's implicit wait"
            )

            self.records.append((name, target, timeout))
            self._log.append(_wait(target, timeout))

            # The real helper returns the element it waited on; returning the
            # locator keeps a body that chains off the result working, and no
            # body in this module does.
            return target

        return _recorder

    @property
    def helpers(self) -> tuple[str, ...]:
        """The helper names that were called, in order."""
        return tuple(name for name, _, _ in self.records)

    @property
    def targets_and_timeouts(self) -> tuple[tuple[Any, Any], ...]:
        """``(locator, timeout)`` per wait, in order - the parity projection."""
        return tuple((locator, timeout) for _, locator, timeout in self.records)


class _FakeClock:
    """Stands in for the step module's ``time``, and for a bare ``sleep``.

    Two jobs, and the second is the interesting one:

    * nothing sleeps for real, so a 3-second parity delay costs no wall time;
    * every delay is appended **into the driver's own ordered log**, so the
      expected-call assertion for the day- and month-view steps shows exactly
      where ``Thread.sleep(3000)`` falls.  Presence is easy to assert and
      almost worthless: ``Calendar.java:99`` puts the delay *after* the
      expectation is computed from the datepicker and *before* the breadcrumb
      is sampled, and that position is the substance of the step.
    """

    def __init__(self, log: list[Call]) -> None:
        """Bind the clock to the driver log it interleaves with.

        :param log: The recorder's mutable ``calls`` list.
        """
        self._log = log

        #: Seconds per call, in order.
        self.seconds: list[float] = []

    def sleep(self, seconds: float) -> None:
        """Record a fixed delay without incurring it.

        :param seconds: The delay the step body asked for.
        :returns: ``None``, matching ``time.sleep``.
        """
        self.seconds.append(seconds)
        self._log.append(_sleep(seconds))


class StepRun(NamedTuple):
    """Everything one driven step observably did."""

    #: The resolved definition, so a test can assert its identity too.
    match: Any

    #: The driver's ordered log for this run, with the sleep markers
    #: interleaved at their real positions.
    calls: tuple[Call, ...]

    #: ``(locator, timeout)`` per explicit wait, in order.
    waits: tuple[tuple[Any, Any], ...]

    #: The wait helper names that were called, in order.
    wait_helpers: tuple[str, ...]

    #: Seconds per fixed delay, in order.
    sleeps: tuple[float, ...]

    def sleep_positions(self) -> tuple[int, ...]:
        """Indices of the fixed delays within :attr:`calls`."""
        return tuple(
            index for index, (operation, _) in enumerate(self.calls) if operation == SLEEP_MARKER
        )

    def operations(self) -> tuple[str, ...]:
        """The operation names of :attr:`calls`, arguments dropped."""
        return tuple(operation for operation, _ in self.calls)

    def index_of(self, entry: Call) -> int:
        """Position of *entry* in :attr:`calls`.

        :param entry: The log entry to locate.
        :returns: Its index.
        :raises AssertionError: When it is absent, naming the log so the
            failure is readable.
        """
        if entry not in self.calls:
            raise AssertionError(f"{entry!r} not in {self.calls!r}")

        return self.calls.index(entry)


class CalendarHarness:
    """Drives one Calendar step definition and reports what it did.

    The only supported route into a step body: ``tests/conftest.py``'s session
    registry has already exec'd ``calendar_steps.py`` through behave's own
    loader, so importing it again risks ``AmbiguousStep``.  ``resolve_step``
    reaches the same function object behave would, and ``match.func.__globals__``
    *is* the step module's namespace - which is where the wait helpers and the
    clock are patched, exactly as that module's docstring designates.
    """

    def __init__(
        self,
        resolver: Callable[[str], Any],
        context: Any,
        driver: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Bind the harness to one test's fixtures.

        :param resolver: ``tests/conftest.py``'s ``resolve_step``.
        :param context: The behave context stand-in, carrying *driver*.
        :param driver: The ordered recorder standing in for the WebDriver.
        :param monkeypatch: Undoes every patch at the end of the test, so the
            step module's namespace is left exactly as the registry built it.
        """
        self._resolver = resolver
        self._context = context
        self._driver = driver
        self._monkeypatch = monkeypatch

    @property
    def driver(self) -> Any:
        """The recorder, for arranging what the page reports."""
        return self._driver

    def resolve(self, phrase: str) -> Any:
        """Resolve *phrase* to exactly one definition owned by this module.

        :param phrase: A concrete Gherkin phrase.
        :returns: The ``StepMatch``.
        :raises AssertionError: When the phrase is undefined, ambiguous, or
            owned by another step module.
        """
        match = self._resolver(phrase)

        assert match.module_name == STEP_MODULE_NAME, (
            f"{phrase!r} resolved to {match.module_name!r}, not {STEP_MODULE_NAME!r}"
        )

        return match

    def _patch_seams(self, match: Any) -> tuple[_WaitRecorder, _FakeClock]:
        """Replace the wait helpers and the clock in the step module namespace.

        Every global whose name begins with ``wait`` is replaced, not only the
        one the port imports: which helper of ``app/automation/waits.py`` a
        call site names is not parity (its locator target and its timeout
        are), and an unpatched binding would route a wait around the recorder,
        leave it unasserted and reach the real driver lifecycle.

        :param match: The resolved step.
        :returns: The wait recorder and the fake clock.
        :raises AssertionError: When the namespace offers no delay seam at all,
            which would mean the module can neither perform nor be prevented
            from performing a real fixed delay.
        """
        namespace = match.func.__globals__
        recorder = _WaitRecorder(self._driver.calls)

        for name in tuple(namespace):
            if name.startswith("wait") and callable(namespace[name]):
                self._monkeypatch.setitem(namespace, name, recorder.bind(name))

        clock = _FakeClock(self._driver.calls)
        patched = False

        # ``calendar_steps.py`` does ``import time`` and writes
        # ``time.sleep(3)``, so the ``time`` binding is the one that carries
        # its two delays.  A bare ``sleep`` binding is patched as well, since
        # ``from time import sleep`` is the other legitimate spelling of the
        # same delay and either must be intercepted rather than slept.
        if "sleep" in namespace and callable(namespace["sleep"]):
            self._monkeypatch.setitem(namespace, "sleep", clock.sleep)
            patched = True

        if "time" in namespace:
            self._monkeypatch.setitem(namespace, "time", clock)
            patched = True

        assert patched, (
            f"{STEP_MODULE_PATH.name} exposes neither a 'sleep' nor a 'time' "
            f"global, so its two fixed delays (Calendar.java:99, :155) can be "
            f"neither observed nor prevented from sleeping for real"
        )

        return recorder, clock

    def run(self, phrase: str) -> StepRun:
        """Drive one step and return everything it did.

        :param phrase: A concrete Gherkin phrase.
        :returns: The run's ordered log, waits and delays.
        """
        match = self.resolve(phrase)
        recorder, clock = self._patch_seams(match)
        start = len(self._driver.calls)

        match.run(self._context)

        return StepRun(
            match=match,
            calls=tuple(self._driver.calls[start:]),
            waits=recorder.targets_and_timeouts,
            wait_helpers=recorder.helpers,
            sleeps=tuple(clock.seconds),
        )

    def run_failing(self, phrase: str) -> AssertionError:
        """Drive one step that must fail its assertion, and return the failure.

        :param phrase: A concrete Gherkin phrase.
        :returns: The raised ``AssertionError``, so the caller can assert on
            its message - or on the deliberate absence of one.
        :raises pytest.fail.Exception: Through ``pytest.raises``, when the step
            does *not* fail, which is how "the assertion exists" is proven.
        """
        match = self.resolve(phrase)
        self._patch_seams(match)

        with pytest.raises(AssertionError) as raised:
            match.run(self._context)

        return raised.value


@pytest.fixture(name="harness")
def _harness_fixture(
    resolve_step: Callable[[str], Any],
    fake_context: Any,
    stub_driver: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> CalendarHarness:
    """A :class:`CalendarHarness` over this test's recorder and context.

    :param resolve_step: Phrase resolution against the loaded step registry.
    :param fake_context: behave context stand-in whose ``driver`` is the
        recorder.
    :param stub_driver: The ordered recorder.
    :param monkeypatch: Patch undo, per test.
    :returns: The harness.
    """
    return CalendarHarness(resolve_step, fake_context, stub_driver, monkeypatch)


def _expect_waits(definition: Definition, *locators: Locator) -> tuple[tuple[Locator, int], ...]:
    """The expected wait projection for one definition.

    :param definition: The census entry, whose ``waits`` count is cross-checked
        against the number of locators given, so a test cannot expect a
        different number of waits than the census records, and whose
        ``locator_reads`` are resolved on :class:`CalendarPage` and compared
        against those locators - which is what joins the constant *name* the
        body writes at each wait site to the locator *value* the helper was
        handed.
    :param locators: The waited-on locators, in order.
    :returns: ``(locator, 2)`` per wait - the 2 of ``Calendar.java:15``, which
        ``app/automation/waits.py`` requires at every call site because no
        helper there declares a default.
    """
    assert len(locators) == definition.waits, (
        f"{definition.function}: census records {definition.waits} wait(s) at "
        f"Calendar.java:{definition.java_line}, test expects {len(locators)}"
    )

    named = tuple(getattr(CalendarPage, name) for name in definition.locator_reads)

    assert named == tuple(locators), (
        f"{definition.function}: the census names {definition.locator_reads} at "
        f"its wait site(s), which resolve to {named!r}, but the test expects "
        f"{locators!r}"
    )

    return tuple((locator, WAIT_TIMEOUT_SECONDS) for locator in locators)


def _assert_step_shape(
    run: StepRun, definition: Definition, expected_calls: tuple[Call, ...]
) -> None:
    """Cross-checks every per-definition test shares.

    :param run: The completed run.
    :param definition: Its census entry.
    :param expected_calls: The ordered log the test asserted.
    :returns: ``None``.
    """
    assert run.match.func.__name__ == definition.function
    assert run.match.pattern == definition.pattern
    assert run.match.bucket == "step"

    # One ``find_element`` per page-*accessor* read: the port's accessors
    # re-resolve on every access (the un-cached ``@FindBy`` proxy of
    # ``CalendarP.java:9-11``), so the two counts must agree exactly.  A
    # locator constant handed to a wait is a class-attribute read and performs
    # no lookup - the wait resolves it inside its own predicate - which is why
    # the two kinds of read are counted apart and both are pinned.
    assert run.operations().count("find_element") == len(definition.accessor_reads)
    assert _page_attributes_used(definition.function) == definition.page_attributes

    # One locator constant per wait call site, and none anywhere else: a wait
    # that lost its per-call locator, or a constant read outside a wait, fails
    # here rather than passing on the accessor sequence alone.
    assert len(definition.locator_reads) == definition.waits

    assert len(run.waits) == definition.waits
    assert all(helper in VISIBILITY_WAIT_HELPERS for helper in run.wait_helpers), (
        f"{definition.function} waited through {run.wait_helpers!r}; "
        f"Calendar.java uses ExpectedConditions.visibilityOf, so only "
        f"{sorted(VISIBILITY_WAIT_HELPERS)} are parity"
    )

    # Each wait's first argument is the very ``CalendarPage`` class attribute
    # the body names at that site - the same object, not an equal-valued pair,
    # which is what joins the constant the source reads to the locator the
    # helper resolves per poll.  ``CREATE_BUTTON``/``EDIT_BUTTON``
    # (CalendarP.java:49 and :58) and ``GET_NOTE``/``SELECT_NOTE`` (:52 and
    # :73) share one XPath each, so value equality alone would let a wait on
    # either satisfy the other.
    for (target, _), name in zip(run.waits, definition.locator_reads, strict=True):
        expected = getattr(CalendarPage, name)

        assert target is expected, (
            f"{definition.function} waited on {target!r}; Calendar.java:"
            f"{definition.java_line} waits on the field CalendarPage.{name} "
            f"ports, which is {expected!r}"
        )
    assert run.sleeps == (SLEEP_SECONDS,) * definition.sleeps
    assert run.calls == expected_calls


# ==========================================================================
# The census and its fail-closed property
#
# Every test in this block is computed from the transcribed constants and from
# the step module's own source.  None of them reads state another test wrote,
# so ``pytest -k`` on any single one behaves exactly as the whole module does -
# which is the property a "covered set" built up at call time would lose.
# ==========================================================================


def test_census_holds_exactly_thirteen_definitions() -> None:
    """``Calendar.java`` declares thirteen definitions and the census carries all of them.

    Pins the annotation lines ``:17``, ``:23``, ``:29``, ``:35``, ``:41``,
    ``:49``, ``:106``, ``:160``, ``:166``, ``:177``, ``:181``, ``:187`` and
    ``:198`` in ascending source order, and the naming rule AAP 0.4.1 sets -
    each Python function named after its Java method, the two camelCase methods
    at ``:161`` and ``:167`` in snake_case.
    """
    assert len(DEFINITIONS) == 13
    assert len(JAVA_ANNOTATION_LINES) == 13

    lines = tuple(definition.java_line for definition in DEFINITIONS)
    assert lines == JAVA_ANNOTATION_LINES
    assert lines == tuple(sorted(lines)), "the census must read in Java source order"

    for field in ("phrase", "pattern", "function", "java_method", "test_name"):
        values = [getattr(definition, field) for definition in DEFINITIONS]
        assert len(set(values)) == 13, f"duplicate {field} in the census: {values}"

    # Three Cucumber annotations are in play, and the ``@And`` at :160 is the
    # reason behave's ``@step`` is mandatory rather than stylistic: behave has
    # no ``@and`` decorator at all.
    assert {definition.java_keyword for definition in DEFINITIONS} == {"@When", "@Then", "@And"}
    assert [definition.java_keyword for definition in DEFINITIONS].count("@And") == 1

    for definition in DEFINITIONS:
        assert definition.function == _expected_python_name(definition.java_method), (
            f"Calendar.java:{definition.java_line} method "
            f"{definition.java_method!r} should port to "
            f"{_expected_python_name(definition.java_method)!r}"
        )


def test_census_totals_match_the_java_class() -> None:
    """The class's ten waits, two fixed delays and seven assertions all land somewhere.

    ``Calendar.java:15`` builds one 2-second wait used at ten sites, ``:99``
    and ``:155`` are the two ``Thread.sleep(3000)`` calls, and ``:46``,
    ``:101``, ``:157``, ``:163``, ``:174``, ``:179`` and ``:185`` are the seven
    assertions - of which exactly one carries a message.
    """
    assert sum(definition.waits for definition in DEFINITIONS) == WAIT_SITE_COUNT
    assert sum(definition.sleeps for definition in DEFINITIONS) == SLEEP_SITE_COUNT
    assert sum(definition.assertions for definition in DEFINITIONS) == ASSERTION_COUNT

    messages = [
        message
        for definition in DEFINITIONS
        for message in definition.assertion_messages
        if message is not None
    ]
    assert messages == [TITLE_ASSERTION_MESSAGE], (
        "Calendar.java:46 is the class's only assertion carrying a message; "
        "the other six are two-argument JUnit calls"
    )

    # The two delays belong to the two big definitions, and to no others.
    assert tuple(
        definition.java_line for definition in DEFINITIONS if definition.sleeps
    ) == (49, 106)

    # ``Calendar.java:187-197`` is the only method with two wait sites, and it
    # is also the only method with waits and no assertion.
    assert tuple(
        definition.java_line for definition in DEFINITIONS if definition.waits == 2
    ) == (187,)
    assert tuple(
        definition.java_line for definition in DEFINITIONS if not definition.assertions
    ) == (17, 23, 29, 35, 187, 198)


def test_every_census_definition_is_claimed_by_a_test_in_this_module() -> None:
    """Each of the thirteen definitions names a parity test that exists here.

    This is the fail-closed hinge: adding a fourteenth definition to the census
    without writing its test fails here, and writing a census entry that points
    at a misspelled or deleted test fails here too.  The claim is resolved
    against this module's own namespace, so it holds whatever subset of tests
    is selected.
    """
    namespace = globals()

    for definition in DEFINITIONS:
        claimed = namespace.get(definition.test_name)

        assert claimed is not None, (
            f"Calendar.java:{definition.java_line} ({definition.function}) "
            f"claims test {definition.test_name!r}, which does not exist in "
            f"{Path(__file__).name}"
        )
        assert callable(claimed)
        assert definition.test_name.startswith("test_"), (
            f"{definition.test_name!r} would not be collected by pytest"
        )


def test_step_module_registers_exactly_the_census_functions() -> None:
    """The step module's ``@step`` functions are the census's, in the census's order.

    Parsed out of ``features/steps/calendar_steps.py`` rather than read off the
    registry, so the comparison is against the file: an unported Java method, a
    renamed function, a fourteenth definition or a definition registered with
    ``@given``/``@when``/``@then`` instead of ``@step`` (AAP deviation 7) all
    fail here.
    """
    registered = _registered_functions()

    assert set(registered) == {definition.function for definition in DEFINITIONS}, (
        f"{STEP_MODULE_PATH.name} registers {sorted(registered)}; the census "
        f"enumerates {sorted(definition.function for definition in DEFINITIONS)}"
    )
    assert tuple(registered) == tuple(definition.function for definition in DEFINITIONS), (
        "the port's definitions must appear in Calendar.java source order"
    )

    for definition in DEFINITIONS:
        decorator_name, node = registered[definition.function]

        assert decorator_name == "step", (
            f"{definition.function} registers with @{decorator_name}; every "
            f"definition in this port uses @step, which is what reproduces "
            f"Cucumber-JVM's text-only matching (AAP deviation 7)"
        )

        patterns = [
            _decorator_pattern(decorator)
            for decorator in node.decorator_list
            if _decorator_name(decorator) in {"step", "given", "when", "then"}
        ]
        assert patterns == [definition.pattern], (
            f"{definition.function} is registered as {patterns}; "
            f"Calendar.java:{definition.java_line} declares "
            f"{definition.pattern!r}"
        )


@pytest.mark.parametrize(
    "definition", DEFINITIONS, ids=[definition.function for definition in DEFINITIONS]
)
def test_every_definition_resolves_once_to_its_census_function(
    definition: Definition, resolve_step: Callable[[str], Any]
) -> None:
    """Each census phrase resolves to exactly one definition, and it is this module's.

    ``resolve_step`` raises when nothing matches and when more than one does,
    so a single call proves uniqueness across every step module in the port -
    the ambiguity risk ``@step`` introduces.
    """
    match = resolve_step(definition.phrase)

    assert match.func.__name__ == definition.function
    assert match.pattern == definition.pattern
    assert match.module_name == STEP_MODULE_NAME
    assert match.bucket == "step"


# ==========================================================================
# Source-level parity: waits, fixed delays, assertion messages
# ==========================================================================


@pytest.mark.parametrize(
    "definition", DEFINITIONS, ids=[definition.function for definition in DEFINITIONS]
)
def test_wait_sites_carry_the_two_second_timeout(definition: Definition) -> None:
    """Every wait site passes the literal ``2`` of ``Calendar.java:15``.

    The source builds one ``WebDriverWait(..., 2)`` for the whole class, so the
    port writes the timeout at each call site.  The *shape* of the call is not
    pinned - ``app/automation/waits.py`` may take the timeout positionally or
    as ``timeout=`` - but the literal is, because a drifted timeout changes how
    long a step tolerates a slow render.
    """
    calls = _wait_calls(definition.function)

    assert len(calls) == definition.waits, (
        f"{definition.function} has {len(calls)} wait site(s); "
        f"Calendar.java:{definition.java_line} has {definition.waits}"
    )

    for call in calls:
        assert _literal_timeout(call) == WAIT_TIMEOUT_SECONDS, (
            f"{definition.function} waits with timeout "
            f"{_literal_timeout(call)!r}; Calendar.java:15 fixes it at "
            f"{WAIT_TIMEOUT_SECONDS}"
        )


def test_the_module_has_exactly_ten_wait_sites_and_no_hidden_one() -> None:
    """Ten wait sites exist in the module, all of them inside step bodies.

    Counted twice: once per definition, and once over the whole file, so a wait
    hoisted into a helper - which would move behaviour out of the definition
    whose parity is being asserted - fails the second count.
    """
    per_definition = sum(len(_wait_calls(definition.function)) for definition in DEFINITIONS)
    whole_module = len(_calls_named(_step_module_tree(), lambda name: name.startswith("wait")))

    assert per_definition == WAIT_SITE_COUNT
    assert whole_module == WAIT_SITE_COUNT


@pytest.mark.parametrize(
    "definition", DEFINITIONS, ids=[definition.function for definition in DEFINITIONS]
)
def test_fixed_delay_sites_are_three_seconds(definition: Definition) -> None:
    """Only ``:49`` and ``:106`` delay, and each delays for three seconds.

    ``Thread.sleep(3000)`` at ``Calendar.java:99`` and ``:155``, reproduced as
    a fixed delay in seconds per AAP 0.4.1 - *"not replaced by explicit
    waits"*.  Eleven definitions must carry no delay at all, which is what the
    zero-expectation rows of this parameterization assert.
    """
    calls = _sleep_calls(definition.function)

    assert len(calls) == definition.sleeps, (
        f"{definition.function} has {len(calls)} fixed delay(s); "
        f"Calendar.java:{definition.java_line} has {definition.sleeps}"
    )

    for call in calls:
        assert len(call.args) == 1
        argument = call.args[0]
        assert isinstance(argument, ast.Constant)
        assert argument.value == SLEEP_SECONDS, (
            f"{definition.function} delays {argument.value!r}s; "
            f"Thread.sleep(3000) is {SLEEP_SECONDS}s"
        )


def test_the_module_has_exactly_two_fixed_delays_and_no_hidden_one() -> None:
    """Two delays in the file, both inside the two big definitions.

    The whole-file count is what closes the loophole: a delay moved into a
    helper would still slow the suite down and would still be timing
    behaviour, but it would no longer be at the call site ``Calendar.java:99``
    and ``:155`` put it.
    """
    per_definition = sum(len(_sleep_calls(definition.function)) for definition in DEFINITIONS)
    whole_module = len(_calls_named(_step_module_tree(), lambda name: name == "sleep"))

    assert per_definition == SLEEP_SITE_COUNT
    assert whole_module == SLEEP_SITE_COUNT


@pytest.mark.parametrize(
    "definition", DEFINITIONS, ids=[definition.function for definition in DEFINITIONS]
)
def test_assertion_messages_match_the_java_class(definition: Definition) -> None:
    """Each definition's assertions, in order, carry exactly the Java messages.

    ``Calendar.java:46`` is a three-argument ``assertEquals`` whose first
    argument is the message; the other six assertions are two-argument or
    single-argument JUnit calls and carry none.  A message added to one of
    those six, or dropped from ``:46``, fails here - and so does an assertion
    added to or removed from any body, since the counts must agree too.
    """
    assert _assert_messages(definition.function) == definition.assertion_messages, (
        f"{definition.function}: Calendar.java:{definition.java_line} asserts "
        f"{definition.assertion_messages!r}"
    )


def test_the_module_has_exactly_seven_assertions_and_no_hidden_one() -> None:
    """Seven assertions in the file, all inside step bodies."""
    per_definition = sum(
        len(_assert_statements(definition.function)) for definition in DEFINITIONS
    )
    whole_module = sum(
        1 for node in ast.walk(_step_module_tree()) if isinstance(node, ast.Assert)
    )

    assert per_definition == ASSERTION_COUNT
    assert whole_module == ASSERTION_COUNT


# ==========================================================================
# Import boundaries (AAP 0.4.2) - Calendar.java:3-10 is the whole import list:
# neither Keys nor Actions (only Crm.java:9-10, Notes.java:9-10 and
# Sales.java:9 import those), and no ConfigurationReader (only
# EmployeeStage.java:4, LoginSD.java:4 and Session.java:4 import it)
# ==========================================================================


def test_step_module_imports_no_selenium_and_no_key_or_action_helper() -> None:
    """The module's imports are the four its Java original justifies.

    ``Calendar.java:3-10`` imports ``CalendarP``, ``Driver``, the three
    Cucumber annotations, ``org.junit.Assert``, ``ExpectedConditions`` and
    ``WebDriverWait``: no ``Keys``, no ``Actions``, and nothing that reads
    configuration.  Its one ``sendKeys`` at ``:171`` is a plain literal send on
    a web element, so the keyboard helper has no business here, and AAP 0.4.2
    confines the browser-automation library to ``app/automation``.
    """
    imports = _imports()
    modules = {module for module, _ in imports}
    names = {name for _, name in imports if name is not None}

    assert not {
        module
        for module in modules
        if module == "selenium" or module.startswith("selenium.")
    }
    assert "selenium" not in _step_module_source(), (
        "the browser-automation library must not be named in a step module, "
        "not even in a comment a grep-based boundary check would flag"
    )

    forbidden = names & FORBIDDEN_STEP_MODULE_NAMES
    assert not forbidden, (
        f"{STEP_MODULE_PATH.name} imports {sorted(forbidden)}; Calendar.java "
        f"imports no key class, no action-chain class, no locator-strategy "
        f"constant and no configuration accessor"
    )

    assert "app.config" not in modules
    assert not any(module == "app" and name == "config" for module, name in imports)

    # The positive half: the visibility wait does come from app/automation,
    # which is the only package permitted to reach the automation library.
    assert any(
        module == "app.automation" and name is not None and name.startswith("wait")
        for module, name in imports
    ), "the module must take its visibility wait from app.automation"


def test_step_module_namespace_carries_no_key_or_action_helper(
    resolve_step: Callable[[str], Any]
) -> None:
    """The loaded module's namespace agrees with its import list.

    Checked against the live namespace as well as the source because behave
    ``exec``s these files: a name injected at load time would not appear in the
    AST, and this is the one assertion that would notice.
    """
    namespace = resolve_step(DEFINITIONS[0].phrase).func.__globals__

    for name in sorted(FORBIDDEN_STEP_MODULE_NAMES):
        assert name not in namespace, (
            f"{name!r} is reachable from {STEP_MODULE_PATH.name}'s namespace"
        )


# ==========================================================================
# Feature fidelity (``features/Calendar.feature``, carried over verbatim)
# ==========================================================================


def test_feature_header_is_carried_over_verbatim() -> None:
    """``Calendar.feature:1-2`` are the tag and the header, unchanged.

    The tag is ``@Calendar`` and deliberately not ``@Smoke``, so the default
    filter of ``CukesRunner.java:18`` does not select this feature; the header
    keeps the "Testinium" vocabulary AAP Conflict 8 forbids reconciling.
    """
    lines = _feature_lines()

    assert lines[0] == FEATURE_TAG_LINE
    assert lines[1] == FEATURE_HEADER_LINE
    assert FEATURE_TAG_LINE != "@Smoke"


def test_feature_examples_row_and_literal_both_supply_the_note_value() -> None:
    """``Calendar.feature:29-31`` and ``:37`` are the two sources of ``{note}``."""
    lines = _feature_lines()

    assert lines[28].strip() == EXAMPLES_HEADER

    header_cells = [cell.strip() for cell in lines[29].strip().strip("|").split("|")]
    row_cells = [
        cell.strip()
        for cell in lines[EXAMPLES_ROW_LINE_NUMBER - 1].strip().strip("|").split("|")
    ]

    assert header_cells == [EXAMPLES_COLUMN]
    assert row_cells == [EXAMPLES_NOTE]
    assert f'"{LITERAL_NOTE}"' in lines[LITERAL_NOTE_LINE_NUMBER - 1]


def test_every_feature_step_resolves_and_the_background_belongs_to_session_steps(
    resolve_step: Callable[[str], Any]
) -> None:
    """All twenty-one feature steps resolve, and the thirteen definitions are all used.

    Three things at once, because they are one fact: the feature's step lines
    are where ``Calendar.feature`` puts them; every phrase resolves to exactly
    one definition; and the definitions those phrases reach are precisely the
    census - so no definition in this module is dead code and no feature step
    is undefined.  The Background at ``:9`` is ``Session.java:12``'s and is
    deliberately not redeclared here.
    """
    steps = _feature_steps()

    assert tuple(number for number, _, _ in steps) == FEATURE_STEP_LINE_NUMBERS

    owners: dict[str, str] = {}
    reached: set[str] = set()

    for number, keyword, phrase in steps:
        match = resolve_step(phrase)
        owners[phrase] = match.module_name

        if match.module_name == STEP_MODULE_NAME:
            reached.add(match.func.__name__)

        assert match.bucket == "step", (
            f"Calendar.feature:{number} '{keyword} {phrase}' resolved through "
            f"bucket {match.bucket!r}; every definition in this port registers "
            f"with @step"
        )

    assert owners[BACKGROUND_PHRASE] == SESSION_MODULE_NAME
    assert reached == {definition.function for definition in DEFINITIONS}, (
        "every Calendar definition must be exercised by Calendar.feature, and "
        "every Calendar.feature step must reach a Calendar definition"
    )



# ==========================================================================
# The thirteen definitions, driven one by one against the ordered recorder
#
# Each test asserts the WHOLE ordered log, not membership: Calendar.java:19-20
# clicks and then waits, and a membership assertion would accept the reverse.
# Every locator comes from app/pages/calendar_page.py rather than being
# retyped, so a selector drift is caught in one place - except for the four
# duplicated XPath literals, which are asserted verbatim further down because
# their identity across two names is itself under test.
# ==========================================================================


def _assert_click_then_wait(
    harness: CalendarHarness, definition: Definition, locator: Locator
) -> StepRun:
    """Drive one of the four "click, then wait on the same element" definitions.

    ``Calendar.java:19-20``, ``:25-26``, ``:31-32`` and ``:37-38`` are the same
    two statements over four different elements: click, then wait 2 seconds for
    *that same element* to be visible.  The wait is not a pre-condition for the
    click - it is settle time after it - and that ordering is the parity.

    The log carries two **driver** entries for the two statements - the
    accessor resolves the element and the click uses it - followed by the wait's
    own marker.  The wait is handed the locator constant rather than a resolved
    element, so the lookup it needs happens inside its own predicate, under the
    call site's 2 seconds, and never reaches the driver log; its marker is what
    keeps it in the sequence, and :attr:`StepRun.waits` is what pins its target
    and its timeout.

    :param harness: The step harness.
    :param definition: The census entry being driven.
    :param locator: The element the Java line names.
    :returns: The completed run, for any further assertion the caller makes.
    """
    run = harness.run(definition.phrase)

    _assert_step_shape(
        run, definition, (_find(locator), _click(locator), _wait(locator))
    )
    assert run.waits == _expect_waits(definition, locator)

    # The wait is the last thing the body does, and it follows the click:
    # reversing the two statements moves the marker ahead of the click and
    # fails the ordered comparison above as well as this one.
    assert run.calls[-1] == _wait(locator)
    assert run.calls.index(_click(locator)) < run.calls.index(_wait(locator))

    return run


def test_calendar_dashboard_clicks_then_waits_on_the_same_element(
    harness: CalendarHarness,
) -> None:
    """``Calendar.java:17-21`` - click ``calendarButton``, then wait 2s on it.

    ``CALENDAR_BUTTON`` is the source's ``By.partialLinkText("Calendar")``
    (``CalendarP.java:16``), the one partial-link-text locator in the class.
    """
    definition = _definition("User click on the calendar dashboard")

    run = _assert_click_then_wait(harness, definition, CalendarPage.CALENDAR_BUTTON)

    assert run.calls[0] == ("find_element", ("partial link text", "Calendar"))
    assert run.waits == ((("partial link text", "Calendar"), WAIT_TIMEOUT_SECONDS),)


def test_day_button_clicks_then_waits(harness: CalendarHarness) -> None:
    """``Calendar.java:23-27`` - click ``day``, then wait 2s on it.  No assertion."""
    definition = _definition("User click on day button")

    _assert_click_then_wait(harness, definition, CalendarPage.DAY)


def test_week_button_clicks_then_waits(harness: CalendarHarness) -> None:
    """``Calendar.java:29-33`` - click ``week``, then wait 2s on it.

    ``week`` is touched by this definition alone: the two big date definitions
    switch to Day and Month views, never to Week.
    """
    definition = _definition("User click on week button")

    _assert_click_then_wait(harness, definition, CalendarPage.WEEK)


def test_month_button_clicks_then_waits(harness: CalendarHarness) -> None:
    """``Calendar.java:35-39`` - click ``month``, then wait 2s on it."""
    definition = _definition("User click on month button")

    _assert_click_then_wait(harness, definition, CalendarPage.MONTH)


def test_last_stage_waits_on_the_module_then_compares_the_driver_title(
    harness: CalendarHarness,
) -> None:
    """``Calendar.java:41-47`` - wait on ``calendarModule``, then compare the title.

    Three parity details live here.  The wait is on the ``o_calendar_container``
    element (``CalendarP.java:19``), for 2 seconds.  The title comes from
    ``Driver.getDriver().getTitle()`` (``:45``) and **not** from
    ``CalendarP.title``, the source's unused XPath against ``//title`` - so the
    log shows one ``title`` read and never resolves that locator.  And the
    comparison is against the whole string ``"Meetings - Odoo"``, not a search
    within it.

    This is the one definition whose *whole* body is a wait and a title read,
    so the only driver operation it performs is that single ``title`` read:
    ``CALENDAR_MODULE`` reaches the helper as a locator and is resolved inside
    the predicate, where the 2-second timeout governs it, rather than by an
    accessor beforehand.  The wait precedes the title read, which its marker
    below shows, and its target and timeout are asserted twice - once against
    the census's own name for it, once against the literal selector.
    """
    definition = _definition("User should see the last stage of calendar view")
    harness.driver.title = EXPECTED_TITLE

    run = harness.run(definition.phrase)

    _assert_step_shape(
        run, definition, (_wait(CalendarPage.CALENDAR_MODULE), _read_title())
    )
    assert run.waits == _expect_waits(definition, CalendarPage.CALENDAR_MODULE)
    assert run.waits == ((("class name", "o_calendar_container"), WAIT_TIMEOUT_SECONDS),)
    assert _find(CalendarPage.TITLE) not in run.calls
    assert _find(CalendarPage.CALENDAR_MODULE) not in run.calls


def test_last_stage_failure_carries_the_java_message_byte_for_byte(
    harness: CalendarHarness,
) -> None:
    """``Calendar.java:46`` - the class's only assertion message.

    ``Assert.assertEquals("The title is not same as the expected!", expected,
    actual)``.  The message is reproduced with no trailing space, which is what
    distinguishes it from the variant ``LoginSD.java:46`` and
    ``LogOutSD.java:27`` use.  JUnit's ``expected:<...> but was:<...>``
    rendering has no Python counterpart (AAP 0.5.2, deviation 16), so the text
    is the whole of the parity - and it is asserted exactly, not by substring.
    """
    definition = _definition("User should see the last stage of calendar view")
    harness.driver.title = "Sales - Odoo"

    failure = harness.run_failing(definition.phrase)

    assert str(failure) == TITLE_ASSERTION_MESSAGE
    assert failure.args == (TITLE_ASSERTION_MESSAGE,)
    assert not str(failure).endswith(" ")
    assert definition.assertion_messages == (TITLE_ASSERTION_MESSAGE,)


# -- The two big definitions: twelve months each, plus the missing default --

#: ``data-month`` value and the month name it must produce, ordered zero to
#: eleven so a parameterized run reads as the calendar year does.
MONTH_CASES: Final[tuple[tuple[str, str], ...]] = tuple(
    (str(index), MONTH_NAMES[index]) for index in range(len(MONTH_NAMES))
)

#: The day the mini datepicker highlights in these tests.  A **string**
#: throughout: ``Calendar.java:53`` reads ``dayCalendar`` as text and ``:98``
#: concatenates it without parsing it.
SAMPLE_DAY: Final[str] = "15"

#: The year the datepicker reports.  Parsed with ``Integer.parseInt``
#: (``Calendar.java:55``), so it renders as an integer.
SAMPLE_YEAR: Final[str] = "2024"


def _arrange_datepicker(
    harness: CalendarHarness, data_month: str, *, day: str = SAMPLE_DAY, year: str = SAMPLE_YEAR
) -> None:
    """Programme the datepicker the two big definitions read.

    :param harness: The step harness, whose recorder is programmed.
    :param data_month: The zero-based ``data-month`` attribute value.
    :param day: The highlighted day's text.
    :param year: The ``data-year`` attribute value.
    :returns: ``None``.
    """
    harness.driver.set_text(CalendarPage.DAY_CALENDAR, day)
    harness.driver.set_attribute(
        CalendarPage.MONTH_AND_YEAR_CALENDAR, "data-month", data_month
    )
    harness.driver.set_attribute(CalendarPage.MONTH_AND_YEAR_CALENDAR, "data-year", year)


def _day_view_calls() -> tuple[Call, ...]:
    """The ordered log of ``Calendar.java:49-104``, delay included in place.

    The wait of ``:52`` performs no lookup of its own - it is handed ``DAY`` as
    a locator and resolves it inside its predicate - so it appears here as its
    marker alone, carrying the locator and the 2 seconds it was called with.
    """
    return (
        # :51 click the Day button, :52 wait 2s on the same locator
        _find(CalendarPage.DAY),
        _click(CalendarPage.DAY),
        _wait(CalendarPage.DAY),
        # :53 read the highlighted day as text
        _find(CalendarPage.DAY_CALENDAR),
        _read_text(CalendarPage.DAY_CALENDAR),
        # :54-55 both attributes off the *same* element, month first
        _find(CalendarPage.MONTH_AND_YEAR_CALENDAR),
        _read_attribute(CalendarPage.MONTH_AND_YEAR_CALENDAR, "data-month"),
        _find(CalendarPage.MONTH_AND_YEAR_CALENDAR),
        _read_attribute(CalendarPage.MONTH_AND_YEAR_CALENDAR, "data-year"),
        # :99 the fixed delay, after the expectation is built
        _sleep(),
        # :100 and only now the breadcrumb is sampled
        _find(CalendarPage.DATE_ACTUAL),
        _read_text(CalendarPage.DATE_ACTUAL),
    )


def _month_view_calls() -> tuple[Call, ...]:
    """The ordered log of ``Calendar.java:106-158``.

    Identical to the day view's but for two differences, and they are the two
    the source has: the Month button replaces the Day button, and
    ``dayCalendar`` is never read at all.  As in the day view, the wait of
    ``:109`` receives ``MONTH`` as a locator and so appears as its marker
    alone, in the position the source puts it.
    """
    return (
        # :108 click the Month button, :109 wait 2s on the same locator
        _find(CalendarPage.MONTH),
        _click(CalendarPage.MONTH),
        _wait(CalendarPage.MONTH),
        # :110-111 the two attributes; no day read anywhere in this method
        _find(CalendarPage.MONTH_AND_YEAR_CALENDAR),
        _read_attribute(CalendarPage.MONTH_AND_YEAR_CALENDAR, "data-month"),
        _find(CalendarPage.MONTH_AND_YEAR_CALENDAR),
        _read_attribute(CalendarPage.MONTH_AND_YEAR_CALENDAR, "data-year"),
        # :155 the fixed delay, in the same position as :99
        _sleep(),
        # :156 the breadcrumb read
        _find(CalendarPage.DATE_ACTUAL),
        _read_text(CalendarPage.DATE_ACTUAL),
    )


@pytest.mark.parametrize(
    ("data_month", "month_name"), MONTH_CASES, ids=[name for _, name in MONTH_CASES]
)
def test_day_view_builds_the_expected_string_for_every_month(
    data_month: str, month_name: str, harness: CalendarHarness
) -> None:
    """``Calendar.java:49-104`` over all twelve switch cases (``:59-96``).

    The step is parameterized directly - not a private month helper - because
    what parity fixes is the *step's* observable behaviour: the ``+ 1`` at
    ``:54`` that makes ``data-month="0"`` January, the twelve names of the
    switch, and the expected string ``"Meetings (<Month> <day>, <year>)"`` of
    ``:98`` measured against the breadcrumb the page reports.

    Each case also re-asserts the whole ordered log, so the month value cannot
    change what the step does to the page.
    """
    definition = _definition("User click day on the calendar and display day")
    expected_breadcrumb = DAY_VIEW_TEMPLATE.format(
        month=month_name, day=SAMPLE_DAY, year=SAMPLE_YEAR
    )

    _arrange_datepicker(harness, data_month)
    harness.driver.set_text(CalendarPage.DATE_ACTUAL, expected_breadcrumb)

    run = harness.run(definition.phrase)

    _assert_step_shape(run, definition, _day_view_calls())
    assert run.waits == _expect_waits(definition, CalendarPage.DAY)
    assert DATA_MONTH_TO_NAME[data_month] == month_name
    assert expected_breadcrumb == f"Meetings ({month_name} {SAMPLE_DAY}, {SAMPLE_YEAR})"


@pytest.mark.parametrize(
    ("data_month", "month_name"), MONTH_CASES, ids=[name for _, name in MONTH_CASES]
)
def test_month_view_builds_the_expected_string_for_every_month(
    data_month: str, month_name: str, harness: CalendarHarness
) -> None:
    """``Calendar.java:106-158`` over all twelve switch cases (``:115-152``).

    The expected string at ``:154`` is ``"Meetings (" + month + " " +
    yearCalendar + ")"``: **no day and no comma**, because a month view shows
    no single day.  ``dayCalendar`` is never read here, which the ordered log
    proves by carrying no ``DAY_CALENDAR`` entry at all.
    """
    definition = _definition("User click month on the calendar and display month")
    expected_breadcrumb = MONTH_VIEW_TEMPLATE.format(month=month_name, year=SAMPLE_YEAR)

    _arrange_datepicker(harness, data_month)
    harness.driver.set_text(CalendarPage.DATE_ACTUAL, expected_breadcrumb)

    run = harness.run(definition.phrase)

    _assert_step_shape(run, definition, _month_view_calls())
    assert run.waits == _expect_waits(definition, CalendarPage.MONTH)
    assert _find(CalendarPage.DAY_CALENDAR) not in run.calls
    assert expected_breadcrumb == f"Meetings ({month_name} {SAMPLE_YEAR})"


@pytest.mark.parametrize("data_month", OUT_OF_RANGE_DATA_MONTHS)
def test_day_view_has_no_default_month_case(
    data_month: str, harness: CalendarHarness
) -> None:
    """``Calendar.java:57-96`` has no ``default``, so the month name stays empty.

    ``String month = ""`` is initialised before the switch, so a ``data-month``
    outside the twelve cases leaves the expected string malformed -
    ``"Meetings ( 15, 2024)"`` - and the assertion at ``:101`` then fails
    against a well-formed breadcrumb.  Both halves are asserted: the malformed
    string is exactly what the step computes, and a sane page then fails.

    The telltale is the separator space of ``:98`` sitting immediately after
    the opening parenthesis, with no month name before it.  (The step module's
    own ``_month_label`` docstring calls that a "double space"; the measured
    string carries one - the concatenation is ``"Meetings ("`` + ``""`` +
    ``" "`` - and this assertion pins the measured form.)

    This is also why the port must not use the standard library's month-name
    sequence, which raises ``IndexError`` out of range: an error is a different
    outcome, a different report status and a parity break.
    """
    definition = _definition("User click day on the calendar and display day")
    malformed = DAY_VIEW_TEMPLATE.format(
        month=NO_DEFAULT_MONTH_NAME, day=SAMPLE_DAY, year=SAMPLE_YEAR
    )

    assert malformed == "Meetings ( 15, 2024)"
    assert malformed.startswith("Meetings ( ")
    assert "March" not in malformed

    _arrange_datepicker(harness, data_month)
    harness.driver.set_text(CalendarPage.DATE_ACTUAL, malformed)

    passing = harness.run(definition.phrase)
    assert passing.calls == _day_view_calls()

    harness.driver.set_text(
        CalendarPage.DATE_ACTUAL,
        DAY_VIEW_TEMPLATE.format(month="March", day=SAMPLE_DAY, year=SAMPLE_YEAR),
    )
    failure = harness.run_failing(definition.phrase)
    assert failure.args == (), "Calendar.java:101 is a two-argument assertEquals"


@pytest.mark.parametrize("data_month", OUT_OF_RANGE_DATA_MONTHS)
def test_month_view_has_no_default_month_case(
    data_month: str, harness: CalendarHarness
) -> None:
    """``Calendar.java:113-152`` has no ``default`` either.

    The malformed month-view string is ``"Meetings ( 2024)"`` - the same empty
    name, without the day and comma the other format carries.
    """
    definition = _definition("User click month on the calendar and display month")
    malformed = MONTH_VIEW_TEMPLATE.format(month=NO_DEFAULT_MONTH_NAME, year=SAMPLE_YEAR)

    assert malformed == "Meetings ( 2024)"
    assert malformed.startswith("Meetings ( ")
    assert "March" not in malformed

    _arrange_datepicker(harness, data_month)
    harness.driver.set_text(CalendarPage.DATE_ACTUAL, malformed)

    passing = harness.run(definition.phrase)
    assert passing.calls == _month_view_calls()

    harness.driver.set_text(
        CalendarPage.DATE_ACTUAL, MONTH_VIEW_TEMPLATE.format(month="March", year=SAMPLE_YEAR)
    )
    failure = harness.run_failing(definition.phrase)
    assert failure.args == (), "Calendar.java:157 is a two-argument assertEquals"


def test_day_and_month_view_expected_formats_are_different() -> None:
    """``Calendar.java:98`` and ``:154`` are two different formats, and neither is the other.

    Copying the day-view format into the month-view step is the likeliest
    single defect in this module and would fail every run of
    ``Calendar.feature:21``, so the difference is asserted as a fact about the
    two templates and not only as a side effect of two passing steps: the day
    view carries the day **and** a comma, the month view carries neither.
    """
    day_string = DAY_VIEW_TEMPLATE.format(month="March", day=SAMPLE_DAY, year=SAMPLE_YEAR)
    month_string = MONTH_VIEW_TEMPLATE.format(month="March", year=SAMPLE_YEAR)

    assert day_string == "Meetings (March 15, 2024)"
    assert month_string == "Meetings (March 2024)"
    assert day_string != month_string
    assert day_string not in month_string
    assert month_string not in day_string

    assert "," in day_string
    assert "," not in month_string
    assert SAMPLE_DAY in day_string
    assert SAMPLE_DAY not in month_string
    assert "{day}" in DAY_VIEW_TEMPLATE
    assert "{day}" not in MONTH_VIEW_TEMPLATE


def test_each_view_rejects_the_other_views_breadcrumb(harness: CalendarHarness) -> None:
    """The behavioural half of the format difference.

    Each definition fails when the page shows the *other* definition's string,
    which is what makes the two formats observably distinct rather than merely
    textually so.
    """
    day_definition = _definition("User click day on the calendar and display day")
    month_definition = _definition("User click month on the calendar and display month")
    day_string = DAY_VIEW_TEMPLATE.format(month="March", day=SAMPLE_DAY, year=SAMPLE_YEAR)
    month_string = MONTH_VIEW_TEMPLATE.format(month="March", year=SAMPLE_YEAR)

    _arrange_datepicker(harness, "2")

    harness.driver.set_text(CalendarPage.DATE_ACTUAL, month_string)
    assert harness.run_failing(day_definition.phrase).args == ()

    harness.driver.set_text(CalendarPage.DATE_ACTUAL, day_string)
    assert harness.run_failing(month_definition.phrase).args == ()


@pytest.mark.parametrize(
    "phrase",
    [
        "User click day on the calendar and display day",
        "User click month on the calendar and display month",
    ],
)
def test_the_fixed_delay_falls_between_the_attribute_reads_and_the_breadcrumb_read(
    phrase: str, harness: CalendarHarness
) -> None:
    """``Calendar.java:99`` and ``:155`` - the delay's *position* is the parity.

    The fake clock appends its marker into the driver's own ordered log, so the
    delay's index can be compared against the reads around it: it must fall
    after **both** ``data-*`` reads - the expectation is computed from the
    datepicker before the delay - and before ``dateActual`` is resolved and
    read, because the delay is what gives the calendar time to re-render.
    Presence alone would pass a port that slept first and read afterwards,
    which measures something else entirely.

    The same ordering is asserted in the source, where the delay's line must
    sit between the datepicker lines and the breadcrumb line - so moving the
    statement fails even if some future refactor changed when the reads are
    logged.
    """
    definition = _definition(phrase)
    expected_breadcrumb = (
        DAY_VIEW_TEMPLATE.format(month="March", day=SAMPLE_DAY, year=SAMPLE_YEAR)
        if definition.java_line == 49
        else MONTH_VIEW_TEMPLATE.format(month="March", year=SAMPLE_YEAR)
    )

    _arrange_datepicker(harness, "2")
    harness.driver.set_text(CalendarPage.DATE_ACTUAL, expected_breadcrumb)

    run = harness.run(definition.phrase)

    positions = run.sleep_positions()
    assert len(positions) == 1, "each big definition delays exactly once"
    assert run.sleeps == (SLEEP_SECONDS,)

    last_attribute_read = max(
        index
        for index, (operation, _) in enumerate(run.calls)
        if operation == f"{ELEMENT_PREFIX}get_attribute"
    )
    breadcrumb_lookup = run.index_of(_find(CalendarPage.DATE_ACTUAL))
    breadcrumb_read = run.index_of(_read_text(CalendarPage.DATE_ACTUAL))

    assert last_attribute_read < positions[0] < breadcrumb_lookup < breadcrumb_read

    # And the same ordering in the source itself.
    delay_line = _sleep_calls(definition.function)[0].lineno
    datepicker_lines = [
        line
        for line, attribute in _page_attribute_sites(definition.function)
        if attribute == "month_and_year_calendar"
    ]
    breadcrumb_lines = [
        line
        for line, attribute in _page_attribute_sites(definition.function)
        if attribute == "date_actual"
    ]

    assert max(datepicker_lines) < delay_line < min(breadcrumb_lines)


def test_the_year_is_integer_parsed_and_the_day_is_kept_as_text(
    harness: CalendarHarness,
) -> None:
    """``Calendar.java:53-55`` - one value is parsed and the other is not.

    ``dayCalendar`` is a ``String`` read at ``:53`` and concatenated at ``:98``
    without parsing, so ``"05"`` stays ``"05"``.  ``yearCalendar`` goes through
    ``Integer.parseInt`` at ``:55``, so ``"0024"`` renders as ``24``.  Parsing
    the day, or passing the year through as text, would each change the
    expected string for exactly these inputs.
    """
    definition = _definition("User click day on the calendar and display day")

    _arrange_datepicker(harness, "2", day="05", year="0024")
    harness.driver.set_text(CalendarPage.DATE_ACTUAL, "Meetings (March 05, 24)")

    run = harness.run(definition.phrase)

    assert run.calls == _day_view_calls()


@pytest.mark.parametrize(
    "phrase",
    [
        "User click day on the calendar and display day",
        "User click month on the calendar and display month",
    ],
)
@pytest.mark.parametrize("attribute", ["data-month", "data-year"])
def test_an_unparsable_datepicker_attribute_propagates_uncaught(
    phrase: str, attribute: str, harness: CalendarHarness
) -> None:
    """``Calendar.java:54-55`` and ``:110-111`` catch nothing.

    ``Integer.parseInt`` throws ``NumberFormatException`` on a corrupted
    ``data-*`` attribute and neither Java method catches it, so the step
    errors.  The Python counterpart is an uncaught ``ValueError``: an error
    outcome, distinct from a failed assertion, and the port must not soften it
    into one or into a default value.
    """
    definition = _definition(phrase)

    _arrange_datepicker(harness, "2")
    harness.driver.set_attribute(
        CalendarPage.MONTH_AND_YEAR_CALENDAR, attribute, "not-a-number"
    )

    with pytest.raises(ValueError):
        harness.run(definition.phrase)


# -- The creation, selection and editing definitions ------------------------


def test_desired_date_time_clicks_the_box_then_asserts_the_modal_header(
    harness: CalendarHarness,
) -> None:
    """``Calendar.java:160-164`` - click ``dateBox``, assert ``createNote`` is displayed.

    Two statements and **no wait at all**: the source adds none, and the
    session's 10-second implicit wait is what covers the modal's appearance.
    ``DATE_BOX`` is the source's hard-coded positional selector
    ``(//td[@class='fc-widget-content'])[29]`` (``CalendarP.java:40``),
    preserved index and all.

    This is the definition declared ``@And`` at ``:160``, which behave cannot
    express with a keyword decorator - the reason the port registers
    everything with ``@step``.
    """
    definition = _definition("User click on desired date time")
    harness.driver.set_displayed(CalendarPage.CREATE_NOTE, True)

    run = harness.run(definition.phrase)

    _assert_step_shape(
        run,
        definition,
        (
            _find(CalendarPage.DATE_BOX),
            _click(CalendarPage.DATE_BOX),
            _find(CalendarPage.CREATE_NOTE),
            _is_displayed(CalendarPage.CREATE_NOTE),
        ),
    )
    assert run.waits == _expect_waits(definition)
    assert CalendarPage.DATE_BOX == ("xpath", "(//td[@class='fc-widget-content'])[29]")
    assert definition.java_keyword == "@And"


def test_desired_date_time_fails_when_the_modal_is_not_displayed(
    harness: CalendarHarness,
) -> None:
    """``Calendar.java:163`` - a single-argument ``assertTrue``, so no message."""
    definition = _definition("User click on desired date time")
    harness.driver.set_displayed(CalendarPage.CREATE_NOTE, False)

    failure = harness.run_failing(definition.phrase)

    assert failure.args == ()
    assert str(failure) == ""


def test_entering_a_note_types_it_creates_it_then_reads_it_back(
    harness: CalendarHarness,
) -> None:
    """``Calendar.java:166-175`` - type the name, create, then read it back.

    ``:171`` is ``summaryBox.sendKeys(note)``: a plain literal send with
    **exactly one** argument, because ``Calendar.java`` imports no key class -
    unlike ``Crm.java`` and ``Notes.java``, whose sends append ``Keys.ENTER``.
    ``:174``'s ``assertEquals(calendarP.getNote.getText(), eventName)`` keeps
    the source's operand order, page text first, and carries no message.

    The two elements this body uses are the ones its Java lines name -
    ``createButton`` and ``getNote`` - even though ``editButton`` and
    ``selectNote`` resolve to the same two XPaths; the accessor sequence is
    what tells them apart.
    """
    definition = _definition(
        f'User enters "{EXAMPLES_NOTE}" in the box and clicks the create button'
    )
    harness.driver.set_text(CalendarPage.GET_NOTE, EXAMPLES_NOTE)

    run = harness.run(definition.phrase)

    _assert_step_shape(
        run,
        definition,
        (
            _find(CalendarPage.SUMMARY_BOX),
            _send_keys(CalendarPage.SUMMARY_BOX, EXAMPLES_NOTE),
            _find(CalendarPage.CREATE_BUTTON),
            _click(CalendarPage.CREATE_BUTTON),
            _find(CalendarPage.GET_NOTE),
            _read_text(CalendarPage.GET_NOTE),
        ),
    )
    assert run.waits == _expect_waits(definition)
    assert run.match.kwargs == {"note": EXAMPLES_NOTE}

    sent = run.calls[1][1][1:]
    assert sent == (EXAMPLES_NOTE,), (
        "Calendar.java:171 sends the note and nothing else - no key literal"
    )


def test_entering_a_note_fails_when_the_name_reads_back_different(
    harness: CalendarHarness,
) -> None:
    """``Calendar.java:174`` - two-argument ``assertEquals``, message-less."""
    definition = _definition(
        f'User enters "{EXAMPLES_NOTE}" in the box and clicks the create button'
    )
    harness.driver.set_text(CalendarPage.GET_NOTE, "Something the user never typed")

    failure = harness.run_failing(definition.phrase)

    assert failure.args == ()


@pytest.mark.parametrize("note", [EXAMPLES_NOTE, LITERAL_NOTE])
def test_both_feature_note_values_reach_the_same_definition(
    note: str, harness: CalendarHarness
) -> None:
    """``Calendar.feature:31`` and ``:37`` both drive ``Calendar.java:166``.

    Cucumber's ``{string}`` placeholder matches the quotation marks and hands
    the definition the unquoted value; behave's equivalent puts the quotes in
    the pattern around a named field.  Both of the feature's values - the
    outline's ``"Test test"``, which carries an internal space, and the bare
    ``"Test"`` of the last scenario - must arrive unquoted and be typed
    verbatim.
    """
    phrase = f'User enters "{note}" in the box and clicks the create button'
    harness.driver.set_text(CalendarPage.GET_NOTE, note)

    run = harness.run(phrase)

    assert run.match.func.__name__ == "user_enters_in_the_box_and_clicks_the_create_button"
    assert run.match.kwargs == {"note": note}
    assert run.calls[1] == _send_keys(CalendarPage.SUMMARY_BOX, note)


def test_can_see_all_the_note_only_asserts_visibility(harness: CalendarHarness) -> None:
    """``Calendar.java:177-180`` - one statement, one assertion, nothing else.

    ``createdNote`` is the source's class-name locator ``o_field_name``
    (``CalendarP.java:55``), kept distinct from the two XPath forms it
    overlaps with.  No click, no wait, no message.
    """
    definition = _definition("User can see all the note")
    harness.driver.set_displayed(CalendarPage.CREATED_NOTE, True)

    run = harness.run(definition.phrase)

    _assert_step_shape(
        run,
        definition,
        (_find(CalendarPage.CREATED_NOTE), _is_displayed(CalendarPage.CREATED_NOTE)),
    )
    assert run.waits == _expect_waits(definition)
    assert CalendarPage.CREATED_NOTE == ("class name", "o_field_name")


def test_can_see_all_the_note_fails_when_it_is_not_displayed(
    harness: CalendarHarness,
) -> None:
    """``Calendar.java:179`` - ``assertTrue`` with no message."""
    definition = _definition("User can see all the note")
    harness.driver.set_displayed(CalendarPage.CREATED_NOTE, False)

    assert harness.run_failing(definition.phrase).args == ()


def test_can_select_the_note_clicks_waits_then_asserts_the_modal(
    harness: CalendarHarness,
) -> None:
    """``Calendar.java:181-186`` - click ``selectNote``, wait 2s on it, assert the modal.

    ``selectNote`` is used by name as ``:183`` and ``:184`` do, even though it
    shares its XPath with ``getNote``, and ``createdModele`` keeps the source's
    misspelling of "modal" (``CalendarP.java:65``) - renaming it would break
    the name comparison and is not this port's business.
    """
    definition = _definition("User can select the note")
    harness.driver.set_displayed(CalendarPage.CREATED_MODELE, True)

    run = harness.run(definition.phrase)

    _assert_step_shape(
        run,
        definition,
        (
            _find(CalendarPage.SELECT_NOTE),
            _click(CalendarPage.SELECT_NOTE),
            _wait(CalendarPage.SELECT_NOTE),
            _find(CalendarPage.CREATED_MODELE),
            _is_displayed(CalendarPage.CREATED_MODELE),
        ),
    )
    assert run.waits == _expect_waits(definition, CalendarPage.SELECT_NOTE)
    assert "CREATED_MODELE" in CalendarPage.LOCATORS
    assert "created_modele" in _page_attributes_used(definition.function)

    # ``:184`` waits on ``selectNote`` and not on the modal it is about to
    # assert: the wait's locator is the clicked element's, and the modal is
    # covered by the session's implicit wait alone.
    assert "SELECT_NOTE" in _page_attributes_used(definition.function)
    assert "CREATED_MODELE" not in _page_attributes_used(definition.function)


def test_can_select_the_note_fails_when_the_modal_is_not_displayed(
    harness: CalendarHarness,
) -> None:
    """``Calendar.java:185`` - ``assertTrue`` with no message."""
    definition = _definition("User can select the note")
    harness.driver.set_displayed(CalendarPage.CREATED_MODELE, False)

    assert harness.run_failing(definition.phrase).args == ()


@pytest.mark.parametrize("checkbox_selected", [True, False])
def test_can_edit_the_information_clears_types_waits_twice_and_discards_the_checkbox(
    checkbox_selected: bool, harness: CalendarHarness
) -> None:
    """``Calendar.java:187-197`` - six statements, two waits, no assertion.

    The class's only definition with two wait call sites (``:190`` and
    ``:193``) and the only one that clears a field.  The literal typed at
    ``:192`` is ``"Hello My Friends"``, capitalisation included.

    ``:194``'s ``calendarP.tagsCheckbox.isSelected();`` evaluates the checkbox
    and **throws the boolean away**, so the step's only sensitivity to it is
    whether the element can be found: it passes whether the box is ticked or
    not, which is why this test is parameterized over both.  Wrapping that
    call in an assertion would fail every scenario in which the checkbox is
    unticked - changing outcomes rather than porting them.
    """
    definition = _definition("User can edit the information")
    harness.driver.set_selected(CalendarPage.TAGS_CHECKBOX, checkbox_selected)

    run = harness.run(definition.phrase)

    _assert_step_shape(
        run,
        definition,
        (
            _find(CalendarPage.EDIT_BUTTON),
            _click(CalendarPage.EDIT_BUTTON),
            _wait(CalendarPage.EDIT_BUTTON),
            _find(CalendarPage.EDIT_TEXT),
            _clear(CalendarPage.EDIT_TEXT),
            _find(CalendarPage.EDIT_TEXT),
            _send_keys(CalendarPage.EDIT_TEXT, EDIT_TEXT_LITERAL),
            _wait(CalendarPage.EDIT_TEXT),
            _find(CalendarPage.TAGS_CHECKBOX),
            _is_selected(CalendarPage.TAGS_CHECKBOX),
        ),
    )
    assert run.waits == _expect_waits(definition, CalendarPage.EDIT_BUTTON, CalendarPage.EDIT_TEXT)

    # The two waits of ``:190`` and ``:193`` sit at different points of the
    # body and carry different locators, in that order - and both carry the
    # 2 seconds of ``Calendar.java:15``, which the helper has no default for.
    assert _page_attributes_used(definition.function).index("EDIT_BUTTON") < (
        _page_attributes_used(definition.function).index("EDIT_TEXT")
    )
    assert {timeout for _, timeout in run.waits} == {WAIT_TIMEOUT_SECONDS}
    assert definition.assertion_messages == ()
    assert not _assert_statements(definition.function), (
        "Calendar.java:187-197 makes no assertion; the discarded isSelected() "
        "result must stay discarded"
    )
    assert CalendarPage.EDIT_TEXT == ("id", "o_field_input_46")
    assert CalendarPage.TAGS_CHECKBOX == ("id", "o_field_input_59")


def test_can_save_all_edit_clicks_save_and_nothing_else(harness: CalendarHarness) -> None:
    """``Calendar.java:198-201`` - one click, no wait, no assertion.

    The scenario ends on the click (``Calendar.feature:41``), and the source
    neither waits nor asserts afterwards, so the step's only failure mode is
    the click itself.
    """
    definition = _definition("User can save all edit")

    run = harness.run(definition.phrase)

    _assert_step_shape(
        run, definition, (_find(CalendarPage.SAVE_BUTTON), _click(CalendarPage.SAVE_BUTTON))
    )
    assert run.waits == _expect_waits(definition)
    assert run.sleeps == ()
    assert CalendarPage.SAVE_BUTTON == ("xpath", "//span[.='Save']")


@pytest.mark.parametrize(
    "definition",
    [definition for definition in DEFINITIONS if not definition.assertions],
    ids=[definition.function for definition in DEFINITIONS if not definition.assertions],
)
def test_definitions_without_assertions_pass_on_a_hostile_page(
    definition: Definition, harness: CalendarHarness
) -> None:
    """Six definitions assert nothing, and that absence is behaviour.

    ``Calendar.java:17``, ``:23``, ``:29``, ``:35``, ``:187`` and ``:198``
    make no assertion at all.  Driving each of them against a page that reports
    nothing visible, nothing selected and no text proves the absence directly:
    an assertion smuggled into any of these bodies would fail here, where the
    source's own step would have passed.
    """
    harness.driver.set_displayed(None, False)
    harness.driver.set_selected(None, False)
    harness.driver.set_text(None, "")

    run = harness.run(definition.phrase)

    assert run.operations().count("find_element") == len(definition.accessor_reads)


# ==========================================================================
# Duplicated selectors: four names, two values
# ==========================================================================


def test_duplicated_selectors_are_declared_under_four_distinct_names() -> None:
    """``CalendarP.java:49``/``:58`` and ``:52``/``:73`` - two XPaths, four fields.

    The values are asserted as literals here, which is the one place this
    module retypes a selector: their *identity across two names* is the thing
    under test, so deriving both sides from the same constant would assert
    nothing.  The four names are kept unmerged and unaliased because the Java
    class declares four fields and parity compares names.
    """
    assert CalendarPage.CREATE_BUTTON == ("xpath", DUPLICATE_PRIMARY_BUTTON_XPATH)
    assert CalendarPage.EDIT_BUTTON == ("xpath", DUPLICATE_PRIMARY_BUTTON_XPATH)
    assert CalendarPage.CREATE_BUTTON == CalendarPage.EDIT_BUTTON

    assert CalendarPage.GET_NOTE == ("xpath", DUPLICATE_NAME_FIELD_XPATH)
    assert CalendarPage.SELECT_NOTE == ("xpath", DUPLICATE_NAME_FIELD_XPATH)
    assert CalendarPage.GET_NOTE == CalendarPage.SELECT_NOTE

    for name in ("CREATE_BUTTON", "EDIT_BUTTON", "GET_NOTE", "SELECT_NOTE"):
        assert name in CalendarPage.LOCATORS


def test_duplicated_selectors_are_used_under_the_names_their_java_lines_use() -> None:
    """Which of two identically-valued fields a call site used.

    The driver log cannot answer this: ``CREATE_BUTTON`` and ``EDIT_BUTTON``
    are the same tuple, so they produce the same ``find_element`` entry.  The
    source can, and ``Calendar.java`` fixes it per line - ``:172`` and ``:174``
    use ``createButton`` and ``getNote``, ``:183``-``:185`` use ``selectNote``,
    and ``:189``-``:190`` use ``editButton``.  A port that swapped any of them
    would behave identically today and diverge the moment either selector is
    corrected.
    """
    create_step = _page_attributes_used("user_enters_in_the_box_and_clicks_the_create_button")
    select_step = _page_attributes_used("user_can_select_the_note")
    edit_step = _page_attributes_used("user_can_edit_the_information")

    assert create_step == ("summary_box", "create_button", "get_note")
    assert "edit_button" not in create_step
    assert "select_note" not in create_step

    assert select_step == ("select_note", "SELECT_NOTE", "created_modele")
    assert "get_note" not in select_step
    assert "GET_NOTE" not in select_step

    assert edit_step[:2] == ("edit_button", "EDIT_BUTTON")
    assert "create_button" not in edit_step
    assert "CREATE_BUTTON" not in edit_step


def test_no_step_uses_the_unused_title_locator() -> None:
    """``CalendarP.java:13``'s ``title`` is declared and never used.

    ``Calendar.java:45`` reads ``Driver.getDriver().getTitle()`` instead, so
    the XPath against ``//title`` - an element that is not visible - is dead
    weight the port keeps because the page object is a faithful inventory.  No
    step body may reach for it, under either spelling: the accessor
    ``page.title`` resolves the element, and the constant ``page.TITLE`` would
    hand that same dead locator to a wait, which - being a locator that never
    becomes visible - would expire every run of ``Calendar.feature:21``.
    """
    assert "TITLE" in CalendarPage.LOCATORS

    for definition in DEFINITIONS:
        used = _page_attributes_used(definition.function)

        assert "title" not in used, (
            f"{definition.function} uses CalendarP.title; Calendar.java:45 "
            f"reads the driver's own title"
        )
        assert "TITLE" not in used, (
            f"{definition.function} waits on CalendarPage.TITLE; "
            f"Calendar.java:43 waits on calendarModule and :45 reads the "
            f"driver's own title"
        )


# ==========================================================================
# Class-wide totals, driven rather than counted
# ==========================================================================


def test_step_module_neither_prints_nor_catches_anything() -> None:
    """``Calendar.java:1-204`` has no ``try`` and no output.

    A wait expiry, a failed parse, a missing element and a dead session all
    propagate untouched, exactly as the Java method's uncaught exception fails
    its step - so a ``try`` here would swallow a failure the source reports.
    And only ``Crm.java:51-52``, ``:63-64``, ``:97-98``, ``:126-127`` and
    ``Sales.java:34-35`` print; this class does not, so a print statement
    would put text in the report the source never emits.
    """
    tree = _step_module_tree()

    assert not [node for node in ast.walk(tree) if isinstance(node, ast.Try)]
    assert not _calls_named(tree, lambda name: name == "print")


def test_the_whole_class_totals_ten_waits_two_delays_and_never_navigates(
    harness: CalendarHarness,
) -> None:
    """All thirteen definitions driven in Java order, and the totals that result.

    The census counts these per method; this drives them.  Ten waits, every one
    of them on the 2 seconds of ``Calendar.java:15``; exactly two 3-second
    delays, with no third one hiding in any of the other eleven definitions;
    one title read, in the one definition that reads it; and no navigation of
    any kind, because nothing in ``Calendar.java:1-204`` calls ``get``,
    ``navigate`` or a script - the Background's login is
    ``Session.java:12-18``'s job.
    """
    _arrange_datepicker(harness, "2")
    harness.driver.title = EXPECTED_TITLE
    harness.driver.set_text(CalendarPage.GET_NOTE, EXAMPLES_NOTE)
    harness.driver.set_text(
        CalendarPage.DATE_ACTUAL,
        DAY_VIEW_TEMPLATE.format(month="March", day=SAMPLE_DAY, year=SAMPLE_YEAR),
    )

    waits: list[tuple[Any, Any]] = []
    sleeps: list[float] = []
    calls: list[Call] = []

    for definition in DEFINITIONS:
        if definition.java_line == 106:
            # The month view expects its own format; everything else the two
            # big definitions read is shared.
            harness.driver.set_text(
                CalendarPage.DATE_ACTUAL,
                MONTH_VIEW_TEMPLATE.format(month="March", year=SAMPLE_YEAR),
            )

        run = harness.run(definition.phrase)

        waits.extend(run.waits)
        sleeps.extend(run.sleeps)
        calls.extend(run.calls)

    assert len(waits) == WAIT_SITE_COUNT
    assert {timeout for _, timeout in waits} == {WAIT_TIMEOUT_SECONDS}
    assert sleeps == [SLEEP_SECONDS] * SLEEP_SITE_COUNT

    operations = [operation for operation, _ in calls]
    assert operations.count("title") == 1
    assert operations.count(SLEEP_MARKER) == SLEEP_SITE_COUNT
    assert operations.count(WAIT_MARKER) == WAIT_SITE_COUNT
    assert [entry for entry in calls if entry[0] == WAIT_MARKER] == [
        _wait(locator) for locator, _ in waits
    ]
    for forbidden in ("get", "back", "execute_script", "find_elements", "quit"):
        assert forbidden not in operations, (
            f"Calendar.java performs no {forbidden!r}; something in the port does"
        )

    assert _find(CalendarPage.TITLE) not in calls

    # Every element the class touches is a declared CalendarP field, and
    # between them the thirteen definitions touch twenty of the twenty-one -
    # every field except the unused ``title`` at CalendarP.java:13.  That is
    # eighteen distinct locator *values*, because two pairs of fields share one
    # XPath each (:49/:58 and :52/:73).
    #
    # They are reached two ways, and both are counted here.  An accessor read
    # resolves its element against the driver and appears in the log; a locator
    # handed to a wait is resolved inside the predicate and does not.  Their
    # union is the class's whole locator surface, and the one field reached
    # *only* through a wait is ``calendarModule`` (Calendar.java:43), whose
    # definition makes no other page access at all.
    resolved = {args for operation, args in calls if operation == "find_element"}
    waited = {locator for locator, _ in waits}
    expected = {
        locator for name, locator in CalendarPage.LOCATORS.items() if name != "TITLE"
    }

    assert resolved == expected - {CalendarPage.CALENDAR_MODULE}
    assert CalendarPage.CALENDAR_MODULE in waited
    assert resolved | waited == expected
    assert waited <= expected
    assert len(resolved) == 17
    assert len(resolved | waited) == 18
    assert len(CalendarPage.LOCATORS) == 21
