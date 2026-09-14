r"""Behavioural parity tests for ``features/steps/contacts_steps.py``.

The authority for every expectation below is the Java source at pinned
revision ``47e9d697e4a9a85da889f94a846fdf47af28a240``, which AAP 0.2.1 holds
REFERENCE and never modifies:
``src/main/java/com/testinium/step_definitions/Contacts.java`` (105 lines, 14
live step definitions) and its page object
``src/main/java/com/testinium/pages/ContactsP.java`` (65 lines, 16
``@FindBy`` fields).  Nothing here is derived from the Python implementation:
each table states the Java line it pins, so a port that drifts from the source
fails even when it stays internally consistent.

What this module discharges
---------------------------
AAP 0.4.1's **per-module parity obligation** for the Contacts area: *"for each
of the ten step modules, ``tests/test_steps_<area>.py`` drives the module
against a stubbed driver and asserts, for every step method in the
corresponding Java class, that the port performs the same observable
operations in the same order - the same navigation targets ..., the same
locators as declared in the paired page object, the same wait target and
timeout, ... the same hard-coded literals and expected values, the same
assertion subject and message text, and the same no-ops where a Java method's
body is empty.  A step method with no corresponding assertion in its module's
test is a gap, and the module test enumerates the Java class's methods so an
omission fails rather than passes silently."*

Two tables make that enumeration real, and they are deliberately **separate**:

``JAVA_METHODS``
    The 14-row inventory of the Java class - line, annotation keyword,
    annotation text, method name.  The authority.
``BEHAVIOUR_CASES``
    The 14 driven bodies, each with its ordered call log.

:func:`test_every_java_method_has_a_behavioural_case` asserts the two agree,
in both directions, which is the gap detector: deleting a behavioural case
turns the suite red instead of quietly shrinking the coverage, and a
fifteenth definition appearing in the registry fails
:func:`test_contacts_steps_registers_exactly_fourteen_definitions`.

Contacts is the largest of the ten step classes and carries the suite's three
sharpest parity risks, so each gets its own section:

1. **Three near-collision phrases** (``Contacts.java:29``, ``:36``, ``:41``).
   ``User enters name "X"``, ``User enters "X"`` and ``User enters "X" and
   "Y"`` overlap, and the middle one will swallow the third under behave's
   default field matcher.  Every one is asserted to resolve to *exactly one*
   definition through the real registry.
2. **Five fixed delays** (``:19``, ``:25``, ``:57``, ``:64``, ``:98``), whose
   *positions* are three distinct shapes - before the only click, between two
   clicks, after the only click.  AAP 0.4.1 requires them reproduced as fixed
   delays at the same call sites and never converted into explicit waits.
3. **Three explicit waits** (``:31``, ``:50``, ``:82``), all on the
   20-second timeout of ``Contacts.java:15``, two of them *after* the click
   they follow and one of them on an element other than the one clicked.

How a step body is reached, and why nothing here sleeps or opens a browser
-------------------------------------------------------------------------
``tests/conftest.py`` owns the whole seam and this module adds nothing to it:
:fixture:`resolve_step` resolves a phrase through behave's own registry,
:class:`~conftest.StepMatch` runs the body, :fixture:`fake_context` supplies
the ``context`` it reads and :fixture:`stub_driver` records every element
operation in one ordered log.

Two bindings are additionally intercepted in the step module's own globals -
behave's loader execs each step file, so each has a private globals dict and
patching it reaches that module alone:

* the **fixed-delay binding**, because the five delays total fifteen seconds
  of real time and a unit suite may not spend it;
* the **wait helper**, because it would otherwise resolve this worker's
  session through the driver lifecycle and try to launch a browser.

Both recorders append to the *same* ordered log as the element operations, as
:data:`DELAY_OPERATION` and :data:`WAIT_OPERATION`, so a delay's or a wait's
position among the clicks is directly assertable rather than merely its
occurrence.  The wait helper is discovered **by prefix** rather than by name
(:data:`WAIT_BINDING_PREFIX`), and its target and timeout are read
shape-agnostically (:func:`_wait_subject`, :func:`_wait_timeout`), because the
helper's name and argument shape are an implementation choice of
``app/automation/waits.py`` while the wait's *target locator*, its *timeout*
and its *order* are the Java contract.  For the same reason the expected call
logs are stated without the lookup a wait performs on its own target, and
:func:`_collapse_wait_lookups` removes it from the recorded log: a helper
taking a located element resolves the accessor at the call site and one taking
a locator resolves it inside the wait, and ``visibilityOf(contactP.nameInput)``
is neither - it dereferences a lazy proxy.  Everything else in the log is
compared for equality, entry by entry.

``conftest.find_all_step_matches`` is imported directly rather than reached
through a fixture, because proving a phrase resolves to *exactly one*
definition needs every match rather than the first.  It loads the registry on
first use through the same one-per-session cache the :fixture:`step_registry`
fixture populates - ``tests/conftest.py`` is a real module here, registered
under that name by pytest before this file is imported - so no test pays for a
second load and none relies on an import-time side effect.  The fixture itself
is requested by the tests that need the registry *object*.

The module under test is production code and is never modified from here.
Where a test states an expectation the implementation does not meet, the
implementation is what changes.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Final, NamedTuple

import pytest
from conftest import find_all_step_matches

from app.automation import By
from app.pages import ContactsPage

# --------------------------------------------------------------------------- #
# Fixed names and values
# --------------------------------------------------------------------------- #

#: The step module under test, as behave's registry reports it - the ``stem``
#: of the file the definition was loaded from.  Used to prove that a phrase
#: resolved *here* and not into a sibling area's module.
STEP_MODULE_NAME: Final[str] = "contacts_steps"

#: Repository-relative path of that module, for the source-inspection tests.
STEP_MODULE_RELATIVE: Final[Path] = Path("features") / "steps" / "contacts_steps.py"

#: Repository-relative path of the feature file AAP 0.4.1 pairs with it.  Note
#: the singular ``Contact``, against the plural Java ``Contacts``: both
#: spellings are the source's and neither is a typo to correct.
FEATURE_RELATIVE: Final[Path] = Path("features") / "Contact.feature"

#: The explicit-wait timeout of ``Contacts.java:15``
#: (``new WebDriverWait(Driver.getDriver(), 20)``) - the longest in the suite,
#: shared with ``Inventory.java:13`` and ``Notes.java:19``.
WAIT_TIMEOUT_SECONDS: Final[int] = 20

#: Each of the five ``Thread.sleep(3000)`` calls at ``Contacts.java:19``,
#: ``:25``, ``:57``, ``:64`` and ``:98``, in the seconds a Python fixed delay
#: takes.
FIXED_DELAY_SECONDS: Final[int] = 3

#: The expected text of the sole assertion, ``Contacts.java:75``.
DELETED_MESSAGE: Final[str] = "Deleted"

#: The Examples row of ``Contact.feature:17`` and ``:36``, which is the only
#: data either outline supplies and therefore the only values these three
#: parameterized steps are ever invoked with.  ``&Dustin`` carries a leading
#: ampersand and ``+99999999999`` a leading plus; both are preserved because
#: the feature data is preserved (AAP 0.2.2).
NAME_VALUE: Final[str] = "&Dustin"
STREET_VALUE: Final[str] = "Haussman"
PHONE_VALUE: Final[str] = "+99999999999"
EMAIL_VALUE: Final[str] = "abcd@info.com"

# --------------------------------------------------------------------------- #
# The vocabulary of the ordered call log
#
# The first five names are ``tests/conftest.py``'s StubDriver vocabulary: a
# driver operation is logged under its own name with its arguments, and an
# element operation is logged as ``element.<op>`` with the locator that
# produced the element as its first argument.  The last two are this module's
# synthetic markers, appended to the same log by the two recorders so that a
# delay or a wait has a position among the element operations.
# --------------------------------------------------------------------------- #

FIND_OPERATION: Final[str] = "find_element"
CLICK_OPERATION: Final[str] = "element.click"
CLEAR_OPERATION: Final[str] = "element.clear"
SEND_KEYS_OPERATION: Final[str] = "element.send_keys"
READ_TEXT_OPERATION: Final[str] = "element.text"
DELAY_OPERATION: Final[str] = "sleep"
WAIT_OPERATION: Final[str] = "wait"

#: The name the step module binds its fixed-delay primitive under.  The port
#: writes ``from time import sleep``, so this is both the binding to intercept
#: and the name :func:`test_module_binds_the_standard_library_fixed_delay`
#: pins structurally.
DELAY_BINDING: Final[str] = "sleep"

#: Prefix every wait helper of ``app/automation/waits.py`` shares.  Discovery
#: is by prefix so that this module asserts the wait's target, timeout and
#: order - the Java contract - without pinning which helper the call site
#: chose or what argument shape it has.
WAIT_BINDING_PREFIX: Final[str] = "wait"

#: The parameterless phrase used only to reach the step module's globals dict,
#: which every one of its 14 definitions shares.
SEAM_PROBE_PHRASE: Final[str] = "User can see the downloaded file"

#: behave's keyword-agnostic registry bucket.  ``@step`` registers here, and a
#: definition bound to ``@given``/``@when``/``@then`` would land elsewhere and
#: fail :func:`test_every_definition_registers_in_the_keyword_agnostic_bucket`.
STEP_BUCKET: Final[str] = "step"

#: Every bucket behave keeps, so that a keyword-bound registration is *found*
#: and reported rather than silently missed.
STEP_BUCKETS: Final[tuple[str, ...]] = ("step", "given", "when", "then")


# --------------------------------------------------------------------------- #
# ContactsP.java:14-61 - the 16 @FindBy declarations
#
# Stated as the Java annotation's own strategy and selector, so the chain
# Java field -> page constant -> logged operation is closed inside this file.
# tests/test_pages.py owns the ten-page inventory; this table is the Contacts
# link of that chain and is what every expected call log below is built from.
# --------------------------------------------------------------------------- #


class FindBy(NamedTuple):
    """One ``@FindBy`` field of ``ContactsP.java``, and the constant porting it."""

    #: The ``app/pages/contacts_page.py`` constant name.
    constant: str

    #: Declaration line in ``ContactsP.java``.
    line: int

    #: The ``(strategy, selector)`` pair the annotation declares.
    locator: tuple[str, str]


JAVA_FINDBY: Final[tuple[FindBy, ...]] = (
    FindBy("CONTACT_MODULE", 14, (By.PARTIAL_LINK_TEXT, "Contacts")),
    FindBy("CREATE_CONTACT", 17, (By.XPATH, "//button[@accesskey='c']")),
    FindBy("CALL_LIST", 20, (By.XPATH, "//button[@accesskey='l']")),
    FindBy("NAME_INPUT", 23, (By.NAME, "name")),
    FindBy("STREET_INPUT", 26, (By.NAME, "street")),
    FindBy("PHONE_NO_INPUT", 29, (By.NAME, "phone")),
    FindBy("EMAIL_INPUT", 32, (By.NAME, "email")),
    FindBy("OK_BTN", 35, (By.XPATH, "//span[.='Ok']")),
    FindBy("NEW_CONTACT", 38, (By.XPATH, "(//div[@class='o_checkbox']/input)[12]")),
    FindBy("ACTION_INPUT", 41, (By.XPATH, "(//div[@class='o_cp_sidebar']/div/div)[2]")),
    FindBy("DELETE_INPUT", 44, (By.XPATH, "//a[@data-index='3']")),
    # One line past the 88-column convention by a single character, for the
    # reason app/pages/contacts_page.py gives of the same selector: splitting a
    # character-exact XPath across two literals hides it from review, and
    # shortening it breaks parity.
    FindBy("FIRST_USER", 47, (By.XPATH, "(//div[@class='o_kanban_view o_res_partner_kanban o_kanban_ungrouped']/div)[1]")),
    FindBy("EDIT_TITLE", 50, (By.XPATH, "//div[@class='oe_title']")),
    FindBy(
        "EDIT_BTN",
        53,
        (By.XPATH, "//button[@class='btn btn-primary btn-sm o_form_button_edit']"),
    ),
    FindBy("PRINT_INPUT", 56, (By.XPATH, "//div[@class='btn-group o_dropdown open']")),
    FindBy(
        "DUE_PAYMENT",
        60,
        (By.XPATH, "(//div[@class='btn-group o_dropdown open']/button)"),
    ),
)

#: The three selectors whose position *is* the locator (AAP 0.8, *"Preserve, do
#: not tidy"*).  A "cleaned up" index targets a different DOM node on the same
#: page, so each index is pinned on its own as well as inside
#: :data:`JAVA_FINDBY`.
POSITIONAL_SELECTORS: Final[tuple[tuple[str, int, str], ...]] = (
    ("NEW_CONTACT", 38, "[12]"),
    ("ACTION_INPUT", 41, "[2]"),
    ("FIRST_USER", 47, "[1]"),
)


# --------------------------------------------------------------------------- #
# Contacts.java:17-104 - the 14 live step definitions
# --------------------------------------------------------------------------- #


class JavaMethod(NamedTuple):
    """One live step definition of ``Contacts.java``."""

    #: Line of the annotation in ``Contacts.java``.
    line: int

    #: The annotation itself - ``When`` or ``Then``.  Cucumber-JVM matches on
    #: text alone, so this is recorded to be proved *irrelevant* to resolution
    #: rather than to constrain it.
    keyword: str

    #: The annotation's text, byte-exact, ``{string}`` placeholders included.
    annotation: str

    #: The Java method name, which the port reproduces verbatim as its Python
    #: function name.
    method: str

    #: A concrete phrase that must resolve to this definition.  For the three
    #: parameterized methods it is the real outline data of
    #: ``Contact.feature:17`` and ``:36``.
    phrase: str


JAVA_METHODS: Final[tuple[JavaMethod, ...]] = (
    JavaMethod(
        17,
        "When",
        "User is at Contact dashboard",
        "user_is_at_contact_dashboard",
        "User is at Contact dashboard",
    ),
    JavaMethod(
        23,
        "When",
        "User clicks the create button",
        "user_clicks_the_create_button",
        "User clicks the create button",
    ),
    JavaMethod(
        29,
        "When",
        "User enters name {string}",
        "user_enters_name",
        f'User enters name "{NAME_VALUE}"',
    ),
    JavaMethod(
        36,
        "When",
        "User enters {string}",
        "user_enters",
        f'User enters "{STREET_VALUE}"',
    ),
    JavaMethod(
        41,
        "When",
        "User enters {string} and {string}",
        "user_enters_and",
        f'User enters "{PHONE_VALUE}" and "{EMAIL_VALUE}"',
    ),
    JavaMethod(
        47,
        "Then",
        "User sees the created new contact details at dashboard",
        "user_sees_the_created_new_contact_details_at_dashboard",
        "User sees the created new contact details at dashboard",
    ),
    JavaMethod(
        54,
        "When",
        "User clicks list section and choose the profile",
        "user_clicks_list_section_and_choose_the_profile",
        "User clicks list section and choose the profile",
    ),
    JavaMethod(
        61,
        "When",
        "User clicks Action to choose delete button",
        "user_clicks_action_to_choose_delete_button",
        "User clicks Action to choose delete button",
    ),
    JavaMethod(
        67,
        "When",
        "User clicks for editing button",
        "user_clicks_for_editing_button",
        "User clicks for editing button",
    ),
    JavaMethod(
        72,
        "Then",
        "User sees deleted profile",
        "user_sees_deleted_profile",
        "User sees deleted profile",
    ),
    JavaMethod(
        79,
        "When",
        "User selects the profile",
        "user_selects_the_profile",
        "User selects the profile",
    ),
    JavaMethod(
        85,
        "Then",
        "User sees the updated contact details at dashboard",
        "user_sees_the_updated_contact_details_at_dashboard",
        "User sees the updated contact details at dashboard",
    ),
    JavaMethod(
        95,
        "When",
        "User clicks the print button and then select due payments",
        "user_clicks_the_print_button_and_then_select_due_payments",
        "User clicks the print button and then select due payments",
    ),
    JavaMethod(
        101,
        "Then",
        "User can see the downloaded file",
        "user_can_see_the_downloaded_file",
        "User can see the downloaded file",
    ),
)

#: The definition ``Contacts.java:90-93`` holds **fully commented out**, with
#: an empty body, matched by the two commented-out step lines at
#: ``Contact.feature:22-23``.  AAP 0.2.2 preserves the source's
#: inconsistencies rather than tidying them, so it is not ported and must
#: resolve to nothing at all.
DEAD_PHRASE: Final[str] = "User clicks and goes directly to the profile"

#: Two phrases ``Contact.feature`` uses that ``Contacts.java`` does **not**
#: declare, because the source declares them in another class.  A second
#: declaration here would make either ambiguous, so each is asserted to
#: resolve outside this module.
FOREIGN_PHRASES: Final[tuple[tuple[str, str, str], ...]] = (
    ("User login to test other features", "session_steps", "Session.java:12"),
    ("User clicks save button", "notes_steps", "Notes.java:44"),
)


# --------------------------------------------------------------------------- #
# Expected call logs, built from JAVA_FINDBY's constants
# --------------------------------------------------------------------------- #

#: One entry of the ordered log: an operation name and its argument tuple.
Call = tuple[str, tuple[Any, ...]]

#: A ``(strategy, selector)`` pair.
Locator = tuple[str, str]


def _find(locator: Locator) -> Call:
    """The lookup a page accessor performs on every access, never cached.

    :param locator: The constant being resolved.
    :returns: The expected ``find_element`` log entry, whose arguments are the
        locator's own two members because the driver is called as
        ``find_element(*locator)``.
    """
    return (FIND_OPERATION, locator)


def _click(locator: Locator) -> Call:
    """Build the expected entry for a ``click()``.

    :param locator: The element clicked.
    :returns: The expected click entry.
    """
    return (CLICK_OPERATION, (locator,))


def _clear(locator: Locator) -> Call:
    """Build the expected entry for a ``clear()``.

    :param locator: The field cleared.
    :returns: The expected clear entry - the only one in this class
        (``Contacts.java:32``).
    """
    return (CLEAR_OPERATION, (locator,))


def _send(locator: Locator, value: str) -> Call:
    """Build the expected entry for a ``sendKeys(...)``.

    :param locator: The field typed into.
    :param value: The text sent, exactly as the step received it.
    :returns: The expected ``send_keys`` entry.
    """
    return (SEND_KEYS_OPERATION, (locator, value))


def _read_text(locator: Locator) -> Call:
    """Build the expected entry for a ``getText()``.

    :param locator: The element read.
    :returns: The expected text-read entry - the port of ``getText()``
        (``Contacts.java:74``), which the binding exposes as a property.
    """
    return (READ_TEXT_OPERATION, (locator,))


def _delay() -> Call:
    """Build the expected entry for one ``Thread.sleep(3000)`` call site.

    :returns: The expected fixed-delay entry.
    """
    return (DELAY_OPERATION, (FIXED_DELAY_SECONDS,))


def _wait(locator: Locator) -> Call:
    """Build the expected entry for one ``wait.until(visibilityOf(...))``.

    :param locator: The element the wait is applied to.
    :returns: The expected wait entry, on the 20-second timeout of
        ``Contacts.java:15``.
    """
    return (WAIT_OPERATION, (locator, WAIT_TIMEOUT_SECONDS))


def _collapse_wait_lookups(calls: tuple[Call, ...]) -> tuple[Call, ...]:
    """Fold away the lookup a wait performs on its own target.

    The one normalization applied to an otherwise byte-for-byte log
    comparison, and it exists because *where* the wait's target is located is
    the helper's shape rather than the source's behaviour.  A helper taking an
    already located element makes the call site resolve the page accessor
    first, which the log shows as a ``find_element`` immediately before the
    wait; a helper taking a locator has the wait resolve it internally, which
    the log does not show at all.  ``Contacts.java`` does neither explicitly -
    ``visibilityOf(contactP.nameInput)`` dereferences a lazy proxy - so the
    lookup is dropped when it sits directly in front of a wait on the same
    locator, and *what* is waited on, for how long, and in what order relative
    to every other operation all stay asserted exactly.

    :param calls: The log as recorded.
    :returns: The log with each wait's own preceding lookup removed.
    """
    collapsed: list[Call] = []

    for entry in calls:
        operation, args = entry

        if (
            operation == WAIT_OPERATION
            and collapsed
            and collapsed[-1] == (FIND_OPERATION, args[0])
        ):
            collapsed.pop()

        collapsed.append(entry)

    return tuple(collapsed)


class Case(NamedTuple):
    """One driven step body and the whole of what it must do."""

    #: Line of the Java method this drives, joining the case to
    #: :data:`JAVA_METHODS`.
    line: int

    #: The concrete phrase resolved through the registry.
    phrase: str

    #: The Python function name, which is the Java method name verbatim.
    func: str

    #: The arguments the pattern must extract, by name.
    kwargs: Mapping[str, str]

    #: The complete ordered log the body must produce - every entry, in order,
    #: and nothing besides.
    calls: tuple[Call, ...]

    #: Page answers to programme before running, as ``(locator, text)``.
    texts: tuple[tuple[Locator, str], ...] = ()


BEHAVIOUR_CASES: Final[tuple[Case, ...]] = (
    # :17-21  Thread.sleep(3000) FIRST, then contactModule.click().
    Case(
        17,
        "User is at Contact dashboard",
        "user_is_at_contact_dashboard",
        {},
        (
            _delay(),
            _find(ContactsPage.CONTACT_MODULE),
            _click(ContactsPage.CONTACT_MODULE),
        ),
    ),
    # :23-27  Thread.sleep(3000) FIRST, then createContact.click().
    Case(
        23,
        "User clicks the create button",
        "user_clicks_the_create_button",
        {},
        (
            _delay(),
            _find(ContactsPage.CREATE_CONTACT),
            _click(ContactsPage.CREATE_CONTACT),
        ),
    ),
    # :29-34  wait on nameInput, then clear(), then sendKeys(string).  The
    # only clear() in the class, and the only wait that precedes every
    # operation on its own target.
    Case(
        29,
        f'User enters name "{NAME_VALUE}"',
        "user_enters_name",
        {"name": NAME_VALUE},
        (
            _wait(ContactsPage.NAME_INPUT),
            _find(ContactsPage.NAME_INPUT),
            _clear(ContactsPage.NAME_INPUT),
            _find(ContactsPage.NAME_INPUT),
            _send(ContactsPage.NAME_INPUT, NAME_VALUE),
        ),
    ),
    # :36-39  streetInput.sendKeys(streetName).  No wait, no clear.
    Case(
        36,
        f'User enters "{STREET_VALUE}"',
        "user_enters",
        {"street_name": STREET_VALUE},
        (
            _find(ContactsPage.STREET_INPUT),
            _send(ContactsPage.STREET_INPUT, STREET_VALUE),
        ),
    ),
    # :41-45  phoneNo into phoneNoInput first (:43), then eMail into
    # emailInput (:44).  Parameter routing is the whole point: both fields
    # accept any text, so a transposition fails silently at the browser.
    Case(
        41,
        f'User enters "{PHONE_VALUE}" and "{EMAIL_VALUE}"',
        "user_enters_and",
        {"phone_no": PHONE_VALUE, "e_mail": EMAIL_VALUE},
        (
            _find(ContactsPage.PHONE_NO_INPUT),
            _send(ContactsPage.PHONE_NO_INPUT, PHONE_VALUE),
            _find(ContactsPage.EMAIL_INPUT),
            _send(ContactsPage.EMAIL_INPUT, EMAIL_VALUE),
        ),
    ),
    # :47-52  contactModule.click() FIRST (:49), THEN the wait on that same
    # element (:50), THEN okBtn.click() (:51).  Clicking before waiting is
    # source behaviour and the order is not to be swapped.  Despite the
    # phrase, nothing is asserted.
    Case(
        47,
        "User sees the created new contact details at dashboard",
        "user_sees_the_created_new_contact_details_at_dashboard",
        {},
        (
            _find(ContactsPage.CONTACT_MODULE),
            _click(ContactsPage.CONTACT_MODULE),
            _wait(ContactsPage.CONTACT_MODULE),
            _find(ContactsPage.OK_BTN),
            _click(ContactsPage.OK_BTN),
        ),
    ),
    # :54-59  callList.click(), delay BETWEEN, newContact.click() - the
    # positional [12] checkbox.
    Case(
        54,
        "User clicks list section and choose the profile",
        "user_clicks_list_section_and_choose_the_profile",
        {},
        (
            _find(ContactsPage.CALL_LIST),
            _click(ContactsPage.CALL_LIST),
            _delay(),
            _find(ContactsPage.NEW_CONTACT),
            _click(ContactsPage.NEW_CONTACT),
        ),
    ),
    # :61-66  actionInput.click() - the positional [2] sidebar div - delay
    # BETWEEN, deleteInput.click().
    Case(
        61,
        "User clicks Action to choose delete button",
        "user_clicks_action_to_choose_delete_button",
        {},
        (
            _find(ContactsPage.ACTION_INPUT),
            _click(ContactsPage.ACTION_INPUT),
            _delay(),
            _find(ContactsPage.DELETE_INPUT),
            _click(ContactsPage.DELETE_INPUT),
        ),
    ),
    # :67-70  editBtn.click(), and that is the whole body.
    Case(
        67,
        "User clicks for editing button",
        "user_clicks_for_editing_button",
        {},
        (
            _find(ContactsPage.EDIT_BTN),
            _click(ContactsPage.EDIT_BTN),
        ),
    ),
    # :72-77  read deleteInput.getText() and assert it equals "Deleted" - the
    # sole assertion in 105 lines.  The same locator :61-66 clicked.
    Case(
        72,
        "User sees deleted profile",
        "user_sees_deleted_profile",
        {},
        (
            _find(ContactsPage.DELETE_INPUT),
            _read_text(ContactsPage.DELETE_INPUT),
        ),
        ((ContactsPage.DELETE_INPUT, DELETED_MESSAGE),),
    ),
    # :79-83  firstUser.click() - the positional [1] kanban record - then a
    # wait on editTitle, a DIFFERENT element from the one clicked.
    Case(
        79,
        "User selects the profile",
        "user_selects_the_profile",
        {},
        (
            _find(ContactsPage.FIRST_USER),
            _click(ContactsPage.FIRST_USER),
            _wait(ContactsPage.EDIT_TITLE),
        ),
    ),
    # :85-88  contactModule.click() only.  The phrase says "sees the updated
    # contact details" and the source checks nothing whatever.
    Case(
        85,
        "User sees the updated contact details at dashboard",
        "user_sees_the_updated_contact_details_at_dashboard",
        {},
        (
            _find(ContactsPage.CONTACT_MODULE),
            _click(ContactsPage.CONTACT_MODULE),
        ),
    ),
    # :95-99  printInput.click(), then the delay LAST.  Follows the
    # commented-out block at :90-93, which is not ported.
    Case(
        95,
        "User clicks the print button and then select due payments",
        "user_clicks_the_print_button_and_then_select_due_payments",
        {},
        (
            _find(ContactsPage.PRINT_INPUT),
            _click(ContactsPage.PRINT_INPUT),
            _delay(),
        ),
    ),
    # :101-104  duePayment.click().  Despite the phrase, no download is
    # verified.
    Case(
        101,
        "User can see the downloaded file",
        "user_can_see_the_downloaded_file",
        {},
        (
            _find(ContactsPage.DUE_PAYMENT),
            _click(ContactsPage.DUE_PAYMENT),
        ),
    ),
)


# --------------------------------------------------------------------------- #
# The five fixed delays and the three explicit waits, by position
# --------------------------------------------------------------------------- #

#: The delay is the body's first observable act and one click follows it.
SHAPE_BEFORE: Final[str] = "before-the-only-click"

#: The delay separates two clicks.
SHAPE_BETWEEN: Final[str] = "between-two-clicks"

#: The delay is the body's last act, after its only click.
SHAPE_AFTER: Final[str] = "after-the-only-click"


class DelaySite(NamedTuple):
    """One ``Thread.sleep(3000)`` call site and its position in the body."""

    #: Line of the ``Thread.sleep`` call in ``Contacts.java``.
    line: int

    #: The phrase whose body holds it.
    phrase: str

    #: One of :data:`SHAPE_BEFORE`, :data:`SHAPE_BETWEEN`,
    #: :data:`SHAPE_AFTER`.
    shape: str


DELAY_SITES: Final[tuple[DelaySite, ...]] = (
    DelaySite(19, "User is at Contact dashboard", SHAPE_BEFORE),
    DelaySite(25, "User clicks the create button", SHAPE_BEFORE),
    DelaySite(57, "User clicks list section and choose the profile", SHAPE_BETWEEN),
    DelaySite(64, "User clicks Action to choose delete button", SHAPE_BETWEEN),
    DelaySite(
        98,
        "User clicks the print button and then select due payments",
        SHAPE_AFTER,
    ),
)


class WaitSite(NamedTuple):
    """One ``wait.until(visibilityOf(...))`` call site and its position."""

    #: Line of the ``wait.until`` call in ``Contacts.java``.
    line: int

    #: The phrase whose body holds it.
    phrase: str

    #: The element the wait is applied to.
    locator: Locator

    #: Clicks that precede the wait in the same body.
    clicks_before: int

    #: Clicks that follow it.
    clicks_after: int


WAIT_SITES: Final[tuple[WaitSite, ...]] = (
    WaitSite(31, f'User enters name "{NAME_VALUE}"', ContactsPage.NAME_INPUT, 0, 0),
    WaitSite(
        50,
        "User sees the created new contact details at dashboard",
        ContactsPage.CONTACT_MODULE,
        1,
        1,
    ),
    WaitSite(82, "User selects the profile", ContactsPage.EDIT_TITLE, 1, 0),
)

#: How many delays and waits each of the 14 bodies takes.  The census that
#: makes "five fixed delays, three explicit waits, and no delay converted into
#: a wait" a single assertion over the whole module.
DELAY_AND_WAIT_CENSUS: Final[Mapping[int, tuple[int, int]]] = {
    17: (1, 0),
    23: (1, 0),
    29: (0, 1),
    36: (0, 0),
    41: (0, 0),
    47: (0, 1),
    54: (1, 0),
    61: (1, 0),
    67: (0, 0),
    72: (0, 0),
    79: (0, 1),
    85: (0, 0),
    95: (1, 0),
    101: (0, 0),
}


# --------------------------------------------------------------------------- #
# The seam: the step module's own globals, intercepted
# --------------------------------------------------------------------------- #


class DelayRecord(NamedTuple):
    """One intercepted fixed delay."""

    #: Seconds the call site asked for.
    seconds: float

    #: Index of its entry in the ordered log.
    position: int


class WaitRecord(NamedTuple):
    """One intercepted explicit wait."""

    #: The globals binding the call site used, for a failure message.
    helper: str

    #: The locator of whatever the wait was applied to.
    locator: Locator

    #: The timeout the call site supplied.
    timeout: float

    #: Index of its entry in the ordered log.
    position: int


def _is_locator(value: Any) -> bool:
    """Report whether *value* has the shape of a locator.

    :param value: Any object.
    :returns: ``True`` for a two-element tuple of strings, which is the shape
        ``app/pages/*`` declares and every element lookup unpacks.
    """
    return (
        isinstance(value, tuple)
        and len(value) == 2
        and all(isinstance(part, str) for part in value)
    )


def _locator_of(value: Any) -> Locator | None:
    """Extract the locator a wait argument identifies, accepting either shape.

    :param value: One argument the call site passed.
    :returns: The ``(strategy, selector)`` pair it identifies - from a located
        element's ``locator`` attribute, or the value itself when the call
        site passed a raw pair - or ``None`` when it identifies no element.

    Both shapes are accepted deliberately: whether the helper takes an already
    located element or a locator belongs to ``app/automation/waits.py``, while
    *which element* is waited on belongs to ``Contacts.java``.
    """
    attached = getattr(value, "locator", None)

    if _is_locator(attached):
        return attached

    if _is_locator(value):
        return value

    return None


def _wait_subject(
    args: Sequence[Any], kwargs: Mapping[str, Any]
) -> tuple[Any, Locator]:
    """Return the argument identifying the element waited on, and its locator.

    :param args: Positional arguments the call site passed.
    :param kwargs: Keyword arguments the call site passed.
    :returns: That argument unchanged - so the recorder can hand it back as
        the helper's own result - paired with the locator it identifies.
    :raises AssertionError: When no argument identifies an element, which
        means the call site's target cannot be checked against the Java one.
    """
    for value in (*args, *kwargs.values()):
        locator = _locator_of(value)

        if locator is not None:
            return value, locator

    raise AssertionError(
        f"an explicit wait was called with no identifiable element: "
        f"args={args!r} kwargs={kwargs!r}. Contacts.java:31, :50 and :82 each "
        f"wait on a named page element, so the call site must pass one"
    )


def _wait_timeout(args: Sequence[Any], kwargs: Mapping[str, Any]) -> float:
    """Return the timeout a wait call site supplied, positionally or by name.

    :param args: Positional arguments the call site passed.
    :param kwargs: Keyword arguments the call site passed.
    :returns: The single numeric argument.
    :raises AssertionError: When the call carries no number or more than one,
        so that "the timeout is 20" can never be asserted against a guess.
    """
    numbers = [
        value
        for value in (*args, *kwargs.values())
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]

    if len(numbers) != 1:
        raise AssertionError(
            f"expected exactly one numeric timeout argument, found "
            f"{numbers!r} in args={args!r} kwargs={kwargs!r}. "
            f"Contacts.java:15 fixes this class's timeout at "
            f"{WAIT_TIMEOUT_SECONDS} seconds and the call site states it"
        )

    return numbers[0]


def _refuse_session(*args: Any, **kwargs: Any) -> Any:
    """Fail loudly instead of resolving a real browser session.

    The safety net behind the globals interception below: if a wait call site
    ever reaches ``app/automation/waits.py``'s session lookup unpatched, this
    turns what would be an attempt to launch a browser into an explained
    failure.

    :param args: Ignored.
    :param kwargs: Ignored.
    :returns: Never returns.
    :raises AssertionError: Always.
    """
    raise AssertionError(
        "a wait reached the real driver lifecycle: this module intercepts "
        f"every globals binding of features/steps/{STEP_MODULE_NAME}.py "
        f"beginning with {WAIT_BINDING_PREFIX!r}, so a call site using some "
        "other binding is a parity test that would have opened a browser"
    )


class ContactsSteps:
    """The Contacts step module, driven with its delays and waits recorded.

    One instance per test.  Construction installs the interception; every
    attribute below is read after :meth:`run`.
    """

    def __init__(
        self,
        *,
        driver: Any,
        context: Any,
        resolve: Callable[[str], Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Intercept the step module's fixed delay and wait bindings.

        :param driver: ``tests/conftest.py``'s ``StubDriver``, which owns the
            ordered log both recorders append to.
        :param context: ``tests/conftest.py``'s ``FakeContext``, carrying that
            driver as ``context.driver``.
        :param resolve: ``tests/conftest.py``'s ``resolve_step``.
        :param monkeypatch: pytest's patcher, so every binding is restored
            when the test ends and no interception outlives it.
        :raises AssertionError: When the step module binds no fixed delay, or
            no wait helper - either would leave this seam blind, and a blind
            seam would sleep for fifteen real seconds or try to open a
            browser.
        """
        self._driver = driver
        self._context = context
        self._resolve = resolve

        #: Every intercepted delay, in call order.
        self.delays: list[DelayRecord] = []

        #: Every intercepted wait, in call order.
        self.waits: list[WaitRecord] = []

        namespace = resolve(SEAM_PROBE_PHRASE).func.__globals__

        if DELAY_BINDING not in namespace:
            raise AssertionError(
                f"features/steps/{STEP_MODULE_NAME}.py binds no "
                f"{DELAY_BINDING!r}: AAP 0.4.1 requires the five "
                f"Thread.sleep(3000) calls of Contacts.java:19, :25, :57, "
                f":64 and :98 reproduced as fixed delays at the same call "
                f"sites"
            )

        monkeypatch.setitem(namespace, DELAY_BINDING, self._delay)

        #: The wait bindings found in the module's globals, discovered by
        #: prefix rather than by name.
        self.helpers: tuple[str, ...] = tuple(
            sorted(
                name
                for name, value in namespace.items()
                if name.startswith(WAIT_BINDING_PREFIX) and callable(value)
            )
        )

        if not self.helpers:
            raise AssertionError(
                f"features/steps/{STEP_MODULE_NAME}.py binds no name "
                f"beginning with {WAIT_BINDING_PREFIX!r}, so the three "
                f"explicit waits of Contacts.java:31, :50 and :82 cannot be "
                f"intercepted and would reach a real browser"
            )

        for name in self.helpers:
            monkeypatch.setitem(namespace, name, self._wait_recorder(name))

        # The safety net, applied where the helpers themselves resolve a
        # session.  ``raising=False`` on purpose: this is a belt and must never
        # become a reason this module fails if that module is refactored.
        monkeypatch.setattr(
            "app.automation.waits.get_driver", _refuse_session, raising=False
        )

    # -- the two recorders -------------------------------------------------- #

    def _delay(self, seconds: float) -> None:
        """Record a fixed delay instead of taking it.

        :param seconds: What the call site asked to pause for.
        :returns: ``None``, as the real primitive does.
        """
        self.delays.append(DelayRecord(seconds, len(self._driver.calls)))
        self._driver.calls.append((DELAY_OPERATION, (seconds,)))

    def _wait_recorder(self, helper: str) -> Callable[..., Any]:
        """Build the stand-in for one wait binding.

        :param helper: The binding's name, carried into the record so a
            failure names the call site's own helper.
        :returns: A callable accepting any argument shape, which records the
            wait and returns its subject - what every helper in
            ``app/automation/waits.py`` resolves to for a visibility
            condition, so a call site that uses the result keeps working.
        """

        def recorder(*args: Any, **kwargs: Any) -> Any:
            subject, locator = _wait_subject(args, kwargs)
            timeout = _wait_timeout(args, kwargs)

            self.waits.append(
                WaitRecord(helper, locator, timeout, len(self._driver.calls))
            )
            self._driver.calls.append((WAIT_OPERATION, (locator, timeout)))
            return subject

        return recorder

    # -- driving a step ----------------------------------------------------- #

    def run(self, phrase: str) -> Any:
        """Resolve *phrase* through the real registry and run its body.

        :param phrase: A concrete Gherkin phrase, without its keyword.
        :returns: The ``StepMatch`` that was run, so a test can assert the
            function reached and the arguments extracted.
        """
        match = self._resolve(phrase)
        match.run(self._context)
        return match

    def reset(self) -> None:
        """Empty the log and both recorders, keeping programmed answers.

        :returns: ``None``.
        """
        self._driver.clear_calls()
        self.delays.clear()
        self.waits.clear()

    # -- reading what happened ---------------------------------------------- #

    @property
    def calls(self) -> tuple[Call, ...]:
        """The whole ordered log, delays and waits interleaved in position."""
        return tuple(self._driver.calls)

    def positions_of(self, operation: str) -> tuple[int, ...]:
        """Locate one operation in the log, which is how order is asserted.

        :param operation: An operation name, element operations included with
            their ``element.`` prefix.
        :returns: Every index in the log at which it occurs.
        """
        return tuple(
            index
            for index, (name, _) in enumerate(self._driver.calls)
            if name == operation
        )


@pytest.fixture
def contacts(
    monkeypatch: pytest.MonkeyPatch,
    stub_driver: Any,
    fake_context: Any,
    resolve_step: Callable[[str], Any],
) -> ContactsSteps:
    """The intercepted Contacts step module, ready to run a phrase.

    :param monkeypatch: pytest's patcher.
    :param stub_driver: ``tests/conftest.py``'s ``StubDriver`` recorder.
    :param fake_context: ``tests/conftest.py``'s ``FakeContext``, carrying it.
    :param resolve_step: ``tests/conftest.py``'s registry-backed resolver.
    :returns: A fresh :class:`ContactsSteps`.
    """
    return ContactsSteps(
        driver=stub_driver,
        context=fake_context,
        resolve=resolve_step,
        monkeypatch=monkeypatch,
    )


# --------------------------------------------------------------------------- #
# Source and registry helpers
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="session")
def step_module_path(repo_root: Path) -> Path:
    """Locate the step module under test.

    :param repo_root: The repository root.
    :returns: Absolute path of the step module under test.
    """
    return repo_root / STEP_MODULE_RELATIVE


@pytest.fixture(scope="session")
def step_module_tree(step_module_path: Path) -> ast.Module:
    """The step module parsed, which is how its boundary is inspected.

    Parsed rather than grepped, deliberately: the module's docstring names the
    very things it must not import - the interaction helpers, the
    configuration module - in prose, so a textual search would report a
    violation that is not there, and the parse tree carries only the code.

    :param step_module_path: Path of the module.
    :returns: Its abstract syntax tree.
    """
    return ast.parse(
        step_module_path.read_text(encoding="utf-8"),
        filename=str(step_module_path),
    )


@pytest.fixture(scope="session")
def feature_lines(repo_root: Path) -> tuple[str, ...]:
    """``features/Contact.feature`` as lines, indexed from zero.

    :param repo_root: The repository root.
    :returns: Every line, newline stripped.
    """
    text = (repo_root / FEATURE_RELATIVE).read_text(encoding="utf-8")
    return tuple(text.splitlines())


def _imports(tree: ast.Module) -> tuple[tuple[str, str], ...]:
    """Every name the module imports, as ``(module, name)`` pairs.

    :param tree: The parsed module.
    :returns: One pair per imported name; a plain ``import x.y`` is reported
        as ``("x.y", "x.y")``, and ``from x import y`` as ``("x", "y")``.
    """
    pairs: list[tuple[str, str]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            pairs.extend((alias.name, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            pairs.extend((module, alias.name) for alias in node.names)

    return tuple(pairs)


def _referenced_names(tree: ast.Module) -> frozenset[str]:
    """Every identifier the module's *code* mentions.

    :param tree: The parsed module.
    :returns: Bare names and attribute names, so both ``press_keys(...)`` and
        ``interactions.press_keys(...)`` are visible.  String literals and
        docstrings contribute nothing.
    """
    names: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)

    return frozenset(names)


def _decorator_names(function: ast.FunctionDef) -> frozenset[str]:
    """The names a function is decorated with, calls unwrapped.

    :param function: A function definition.
    :returns: ``{"step"}`` for ``@step("...")``, and the bare name for a
        decorator applied without a call.
    """
    names: set[str] = set()

    for decorator in function.decorator_list:
        expression = decorator.func if isinstance(decorator, ast.Call) else decorator

        if isinstance(expression, ast.Name):
            names.add(expression.id)
        elif isinstance(expression, ast.Attribute):
            names.add(expression.attr)

    return frozenset(names)


def _functions(tree: ast.Module) -> tuple[ast.FunctionDef, ...]:
    """Collect the module's function definitions.

    :param tree: The parsed module.
    :returns: Every function it defines, in source order.
    """
    return tuple(
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    )


def _contacts_definitions(registry: Any) -> tuple[tuple[str, Any], ...]:
    """Every definition behave loaded from the Contacts step module.

    :param registry: behave's populated ``StepRegistry``.
    :returns: One ``(bucket, matcher)`` pair per definition whose source file
        is the module under test.  All four buckets are scanned, not just the
        keyword-agnostic one, so a definition accidentally bound to a keyword
        is *found and reported* rather than silently missed - which is also
        what makes the count of fourteen meaningful.
    """
    found: list[tuple[str, Any]] = []

    for bucket in STEP_BUCKETS:
        for matcher in registry.steps.get(bucket, ()):
            filename = str(getattr(matcher.location, "filename", "") or "")

            if Path(filename).stem == STEP_MODULE_NAME:
                found.append((bucket, matcher))

    return tuple(found)


def _as_java_annotation(pattern: str) -> str:
    """Reduce a behave pattern to the Java annotation text it ports.

    Cucumber's ``{string}`` includes its own quotes; behave's parse matcher
    does not, so the port writes the quotes into the pattern and the field
    between them.  Collapsing ``"{field[:Type]}"`` back to ``{string}`` is
    what lets the 14 registered patterns be compared against the 14 Java
    annotations byte-exactly, while leaving the field's name and converter -
    which are the port's own choice - unpinned.

    :param pattern: A registered step pattern.
    :returns: The equivalent Cucumber annotation text.
    """
    return re.sub(r'"\{[^{}]*\}"', "{string}", pattern)


def _java_ids(rows: Sequence[Any]) -> list[str]:
    """Name each parametrized case after the Java line it pins.

    :param rows: Table rows carrying a ``line``.
    :returns: One test id per row.
    """
    return [f"Contacts.java:{row.line}" for row in rows]


# =========================================================================== #
# The 14-method census: Contacts.java's inventory against behave's registry
# =========================================================================== #


def test_contacts_steps_registers_exactly_fourteen_definitions(
    step_registry: Any,
) -> None:
    """14 live definitions, and no fifteenth - ``Contacts.java:17-104``.

    The class declares fourteen; ``:90-93`` holds a commented-out definition
    that is not one of them and is not ported (AAP 0.2.2).
    """
    definitions = _contacts_definitions(step_registry)

    assert len(definitions) == len(JAVA_METHODS) == 14, (
        f"expected {len(JAVA_METHODS)} definitions from "
        f"{STEP_MODULE_RELATIVE}, found {len(definitions)}: "
        f"{[matcher.pattern for _, matcher in definitions]}"
    )


def test_registered_patterns_are_the_java_annotations(step_registry: Any) -> None:
    """Every pattern is its Java annotation text - ``Contacts.java:17-104``.

    Compared in both directions, so a phrase reworded in the port and a Java
    method left unported both fail here.  The three parameterized patterns are
    reduced to Cucumber's ``{string}`` first, since the quotes and the field
    name are behave's requirement rather than the source's text.
    """
    registered = {
        _as_java_annotation(matcher.pattern)
        for _, matcher in _contacts_definitions(step_registry)
    }
    expected = {method.annotation for method in JAVA_METHODS}

    assert registered == expected, (
        f"patterns not in Contacts.java: {sorted(registered - expected)}; "
        f"Java annotations not registered: {sorted(expected - registered)}"
    )


@pytest.mark.parametrize("method", JAVA_METHODS, ids=_java_ids(JAVA_METHODS))
def test_java_annotation_resolves_to_exactly_one_definition(
    method: JavaMethod,
) -> None:
    """Each of the 14 phrases reaches its own method - the phrase table.

    Resolution goes through behave's real registry, and the function reached
    must be the one named after the Java method it ports
    (``Contacts.java:<line>``).  Exactly one match, always: ``@step`` reaches
    every keyword, so two overlapping patterns can both match one phrase
    without either being a duplicate registration behave would have rejected.
    """
    matches = find_all_step_matches(method.phrase)

    assert len(matches) == 1, (
        f"{method.phrase!r} (Contacts.java:{method.line}) resolved to "
        f"{len(matches)} definitions: "
        f"{[(match.pattern, match.location) for match in matches]}"
    )

    match = matches[0]

    assert match.module_name == STEP_MODULE_NAME
    assert match.func.__name__ == method.method
    assert _as_java_annotation(match.pattern) == method.annotation


def test_every_definition_registers_in_the_keyword_agnostic_bucket(
    step_registry: Any,
) -> None:
    """All 14 register with ``@step`` - ``Contacts.java``'s 10 When + 4 Then.

    Cucumber-JVM matches a step by its text alone, so the annotation keyword
    is irrelevant at match time; behave resolves by the effective step type,
    and only ``@step`` reproduces that (AAP 0.5.2, deviation 7).  This module
    is where the rule is load-bearing rather than stylistic:
    ``Contacts.java:17`` carries ``When`` and ``Contact.feature:6`` invokes it
    as ``Given``.
    """
    keywords = [method.keyword for method in JAVA_METHODS]

    assert keywords.count("When") == 10
    assert keywords.count("Then") == 4

    buckets = {bucket for bucket, _ in _contacts_definitions(step_registry)}

    assert buckets == {STEP_BUCKET}, (
        f"definitions of {STEP_MODULE_RELATIVE} are bound to keywords "
        f"{sorted(buckets - {STEP_BUCKET})}: a keyword-bound definition does "
        f"not match a use under another keyword, which Cucumber-JVM does"
    )


def test_commented_out_fifteenth_definition_stays_dead() -> None:
    """``Contacts.java:90-93`` is not ported and must match nothing.

    A fully commented-out definition with an empty body.  Porting it would add
    a fifteenth step the source does not have, and it would resolve the two
    commented-out lines at ``Contact.feature:22-23`` if they were ever
    uncommented - which is a decision for the user, not this port.
    """
    matches = find_all_step_matches(DEAD_PHRASE)

    assert matches == (), (
        f"{DEAD_PHRASE!r} resolved to "
        f"{[(match.pattern, match.location) for match in matches]}, but "
        f"Contacts.java:90-93 holds it commented out"
    )


def test_every_java_method_has_a_behavioural_case() -> None:
    """The gap detector required by AAP 0.4.1's per-module obligation.

    ``JAVA_METHODS`` is the inventory of ``Contacts.java:17-104`` and
    ``BEHAVIOUR_CASES`` is what this module actually drives.  Comparing them
    in both directions is what makes *"a step method with no corresponding
    assertion in its module's test is a gap"* fail rather than pass silently:
    delete a case and this test reports the Java line that lost its coverage.
    """
    covered = {(case.line, case.phrase) for case in BEHAVIOUR_CASES}
    inventory = {(method.line, method.phrase) for method in JAVA_METHODS}

    assert covered == inventory, (
        f"Java methods with no behavioural case: {sorted(inventory - covered)}; "
        f"cases with no Java method: {sorted(covered - inventory)}"
    )
    assert len(BEHAVIOUR_CASES) == 14
    assert {case.func for case in BEHAVIOUR_CASES} == {
        method.method for method in JAVA_METHODS
    }


def test_delay_and_wait_census_covers_every_java_method() -> None:
    """Every method is accounted for in the delay/wait census - ``:17-104``.

    Keeps :func:`test_module_takes_five_fixed_delays_and_three_waits` honest:
    the census it asserts against has to name all fourteen bodies, including
    the six that take neither.
    """
    assert set(DELAY_AND_WAIT_CENSUS) == {method.line for method in JAVA_METHODS}
    assert sum(delays for delays, _ in DELAY_AND_WAIT_CENSUS.values()) == len(
        DELAY_SITES
    )
    assert sum(waits for _, waits in DELAY_AND_WAIT_CENSUS.values()) == len(WAIT_SITES)


# =========================================================================== #
# ContactsP.java:14-61 - the locators every expectation is built from
# =========================================================================== #


def test_page_locators_are_the_java_findby_declarations() -> None:
    """All 16 ``@FindBy`` fields, in declaration order - ``ContactsP.java:14-61``.

    Strategy, selector and order together: ``LOCATORS`` is built from the class
    body in order, and the Java class declares its fields in the order this
    table lists them.  Byte-exact selectors, so the redundant outer
    parentheses of ``duePayment`` (``:60``) and the four-class attribute value
    of ``editBtn`` (``:53``) survive.
    """
    expected = tuple((entry.constant, entry.locator) for entry in JAVA_FINDBY)

    assert tuple(ContactsPage.LOCATORS.items()) == expected


@pytest.mark.parametrize(
    ("constant", "line", "index"),
    POSITIONAL_SELECTORS,
    ids=[f"ContactsP.java:{line}" for _, line, _ in POSITIONAL_SELECTORS],
)
def test_positional_selector_keeps_its_java_index(
    constant: str, line: int, index: str
) -> None:
    """The three positional selectors keep their indices - ``ContactsP.java``.

    ``newContact`` is the 12th ``o_checkbox`` input (``:38``), ``actionInput``
    the 2nd sidebar div (``:41``) and ``firstUser`` the 1st kanban record
    (``:47``).  The index depends on the state of the Odoo instance under
    test, so a tidied index selects a different node on the same page
    (AAP 0.8).
    """
    _, selector = getattr(ContactsPage, constant)

    assert selector.endswith(index), (
        f"ContactsPage.{constant} is {selector!r}; ContactsP.java:{line} ends "
        f"it {index}"
    )


# =========================================================================== #
# The 14 bodies, each against its Java operation sequence
# =========================================================================== #


@pytest.mark.parametrize("case", BEHAVIOUR_CASES, ids=_java_ids(BEHAVIOUR_CASES))
def test_step_performs_the_java_operations_in_order(
    case: Case, contacts: ContactsSteps, stub_driver: Any
) -> None:
    """One Java method, its whole observable sequence - ``Contacts.java``.

    The log is compared for equality rather than containment, so an extra
    lookup, a missing clear, a transposed pair of sends, a dropped delay or an
    added assertion all fail.  Each expected entry names the page constant the
    Java body targets, so the chain ``@FindBy`` -> constant -> operation is
    closed.  :func:`_collapse_wait_lookups` is the single normalization, and
    it removes nothing the source specifies.
    """
    for locator, value in case.texts:
        stub_driver.set_text(locator, value)

    match = contacts.run(case.phrase)

    assert match.module_name == STEP_MODULE_NAME
    assert match.func.__name__ == case.func
    assert match.args == ()
    assert match.kwargs == dict(case.kwargs)
    assert _collapse_wait_lookups(contacts.calls) == case.calls


# =========================================================================== #
# Contacts.java:29, :36, :41 - the three near-collision phrases
#
# Cucumber's {string} cannot match a bare double quote; behave's default field
# matcher can, and backtracks across one, so the single-parameter pattern of
# :36 will consume a :41 step use and capture both values as one.  The port
# registers a quote-excluding field type at module scope to prevent it, and
# the tests below are what hold that property - one per phrase, one for the
# two Login phrases that would otherwise be caught by the bare pattern, one
# for the empty argument, and one for the registration itself.
# =========================================================================== #


def test_name_phrase_resolves_only_to_the_name_definition(
    contacts: ContactsSteps,
) -> None:
    """``User enters name "X"`` is ``Contacts.java:29``, not ``:36``.

    The longer phrase is a superstring of the bare one only after ``name ``,
    so this is the easier of the two collisions - and it is asserted anyway,
    because the value has to land on ``nameInput`` (``ContactsP.java:23``) and
    not on ``streetInput``.
    """
    phrase = f'User enters name "{NAME_VALUE}"'
    matches = find_all_step_matches(phrase)

    assert len(matches) == 1
    assert matches[0].func.__name__ == "user_enters_name"
    assert matches[0].kwargs == {"name": NAME_VALUE}

    contacts.run(phrase)

    assert contacts.calls[-1] == _send(ContactsPage.NAME_INPUT, NAME_VALUE)


def test_street_phrase_resolves_only_to_the_street_definition(
    contacts: ContactsSteps,
) -> None:
    """``User enters "X"`` is ``Contacts.java:36`` - one send, no wait.

    The bare pattern, and the one whose matcher must refuse to span a closing
    quote.  Its value lands on ``streetInput`` (``ContactsP.java:26``).
    """
    phrase = f'User enters "{STREET_VALUE}"'
    matches = find_all_step_matches(phrase)

    assert len(matches) == 1
    assert matches[0].func.__name__ == "user_enters"
    assert matches[0].kwargs == {"street_name": STREET_VALUE}

    contacts.run(phrase)

    assert contacts.calls == (
        _find(ContactsPage.STREET_INPUT),
        _send(ContactsPage.STREET_INPUT, STREET_VALUE),
    )


def test_two_parameter_phrase_resolves_only_to_the_phone_and_email_definition(
    contacts: ContactsSteps,
) -> None:
    """``User enters "X" and "Y"`` is ``Contacts.java:41`` - the collision.

    The step use ``Contact.feature:12`` and ``:31`` make, and the one the bare
    pattern of ``:36`` would otherwise also match - capturing
    ``+99999999999" and "abcd@info.com`` into a single field and shipping as a
    non-deterministic ambiguity.  Both parameters must be extracted, in order,
    and routed phone-then-email (``Contacts.java:43-44``).
    """
    phrase = f'User enters "{PHONE_VALUE}" and "{EMAIL_VALUE}"'
    matches = find_all_step_matches(phrase)

    assert len(matches) == 1, (
        f"{phrase!r} resolved to {len(matches)} definitions: "
        f"{[(match.pattern, match.location) for match in matches]}. The bare "
        f"'User enters {{string}}' pattern of Contacts.java:36 must not match "
        f"a two-parameter use"
    )
    assert matches[0].func.__name__ == "user_enters_and"
    assert matches[0].kwargs == {"phone_no": PHONE_VALUE, "e_mail": EMAIL_VALUE}

    contacts.run(phrase)

    assert contacts.calls == (
        _find(ContactsPage.PHONE_NO_INPUT),
        _send(ContactsPage.PHONE_NO_INPUT, PHONE_VALUE),
        _find(ContactsPage.EMAIL_INPUT),
        _send(ContactsPage.EMAIL_INPUT, EMAIL_VALUE),
    )


@pytest.mark.parametrize(
    ("phrase", "function"),
    (
        ('User enters "Testinium" username', "user_enters_username"),
        ('User enters "Selenium" password', "user_enters_password"),
    ),
)
def test_login_field_phrases_are_not_captured_by_the_bare_pattern(
    phrase: str, function: str
) -> None:
    """Login's own ``User enters ...`` phrases stay Login's - ``LoginSD.java``.

    ``Contacts.java:36``'s bare pattern ends at the closing quote, so a phrase
    that continues past it belongs to whichever class declares it.  Were the
    quote-excluding field type dropped, these two would become ambiguous
    across two step modules, which is a failure no feature file would
    localize.
    """
    matches = find_all_step_matches(phrase)

    assert len(matches) == 1
    assert matches[0].func.__name__ == function
    assert matches[0].module_name != STEP_MODULE_NAME


def test_empty_quoted_argument_still_resolves(contacts: ContactsSteps) -> None:
    """An empty ``{string}`` is accepted, as Cucumber's own field type is.

    ``Contacts.java:36`` takes any string, the empty one included, so the
    port's field type must match ``""`` rather than requiring a character.
    Nothing in the ten feature files supplies an empty value today; the
    property is asserted so that narrowing the matcher is a visible change
    rather than a silent one.
    """
    matches = find_all_step_matches('User enters ""')

    assert len(matches) == 1
    assert matches[0].func.__name__ == "user_enters"
    assert matches[0].kwargs == {"street_name": ""}

    contacts.run('User enters ""')

    assert contacts.calls == (
        _find(ContactsPage.STREET_INPUT),
        _send(ContactsPage.STREET_INPUT, ""),
    )


def test_quoted_field_type_is_registered_at_module_scope(
    step_module_tree: ast.Module,
) -> None:
    """The field type is installed above the decorators - the ambiguity fix.

    behave's parse matcher compiles each pattern at **decoration** time, so a
    registration that ran later - in ``before_all``, say - would leave the
    three patterns already compiled with a plain field and change nothing at
    all.  The call therefore has to be a module-level statement that precedes
    the first decorated definition, which is what this asserts structurally;
    the tests above assert the behaviour it buys.
    """
    registrations = [
        node.value.lineno
        for node in step_module_tree.body
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "register_type"
    ]

    assert len(registrations) == 1, (
        "expected exactly one module-level register_type call in "
        f"{STEP_MODULE_RELATIVE}, found {len(registrations)}"
    )

    first_definition = min(
        node.decorator_list[0].lineno
        for node in step_module_tree.body
        if isinstance(node, ast.FunctionDef) and "step" in _decorator_names(node)
    )

    assert registrations[0] < first_definition


# =========================================================================== #
# The five fixed delays - Contacts.java:19, :25, :57, :64, :98
# =========================================================================== #


@pytest.mark.parametrize("site", DELAY_SITES, ids=_java_ids(DELAY_SITES))
def test_fixed_delay_is_taken_at_its_java_position(
    site: DelaySite, contacts: ContactsSteps
) -> None:
    """One ``Thread.sleep(3000)``, three seconds, at its own position.

    The five sites take three distinct shapes and the shape is part of the
    contract: the delay comes **first** at ``Contacts.java:19`` and ``:25``,
    sits **between the two clicks** at ``:57`` and ``:64``, and comes **last**
    at ``:98``.  A delay that moved would still be a delay, and the step's
    timing behaviour would still change.
    """
    contacts.run(site.phrase)

    assert len(contacts.delays) == 1, (
        f"expected one fixed delay in {site.phrase!r} "
        f"(Contacts.java:{site.line}), recorded {contacts.delays!r}"
    )
    assert contacts.delays[0].seconds == FIXED_DELAY_SECONDS

    position = contacts.delays[0].position
    clicks = contacts.positions_of(CLICK_OPERATION)
    before = [index for index in clicks if index < position]
    after = [index for index in clicks if index > position]

    if site.shape == SHAPE_BEFORE:
        assert (len(before), len(after)) == (0, 1)
        assert position == 0
    elif site.shape == SHAPE_BETWEEN:
        assert (len(before), len(after)) == (1, 1)
    else:
        assert site.shape == SHAPE_AFTER
        assert (len(before), len(after)) == (1, 0)
        assert position == len(contacts.calls) - 1


def test_module_takes_five_fixed_delays_and_three_waits(
    contacts: ContactsSteps, stub_driver: Any
) -> None:
    """The whole-module census - ``Contacts.java:19-98`` and ``:31-82``.

    Five fixed delays and three explicit waits, distributed exactly as the
    source distributes them.  The two counts together are what pins AAP
    0.4.1's rule that the delays are *not* converted into explicit waits: no
    body that delays also waits, so a conversion would move a count and fail
    here as well as at the call site.
    """
    stub_driver.set_text(ContactsPage.DELETE_INPUT, DELETED_MESSAGE)
    measured: dict[int, tuple[int, int]] = {}

    for method in JAVA_METHODS:
        contacts.reset()
        contacts.run(method.phrase)
        measured[method.line] = (len(contacts.delays), len(contacts.waits))

    assert measured == dict(DELAY_AND_WAIT_CENSUS)


def test_every_fixed_delay_is_three_seconds(
    contacts: ContactsSteps, stub_driver: Any
) -> None:
    """All five delays are 3000 ms - ``Contacts.java:19, :25, :57, :64, :98``.

    Asserted across the whole module rather than per site, so a single delay
    given the 20-second wait timeout of ``:15`` - the mistake the coexistence
    of the two numbers invites - fails here.
    """
    stub_driver.set_text(ContactsPage.DELETE_INPUT, DELETED_MESSAGE)
    durations: list[float] = []

    for method in JAVA_METHODS:
        contacts.reset()
        contacts.run(method.phrase)
        durations.extend(record.seconds for record in contacts.delays)

    assert durations == [FIXED_DELAY_SECONDS] * len(DELAY_SITES)


# =========================================================================== #
# The three explicit waits - Contacts.java:31, :50, :82, all 20 seconds
# =========================================================================== #


@pytest.mark.parametrize("site", WAIT_SITES, ids=_java_ids(WAIT_SITES))
def test_explicit_wait_target_timeout_and_order(
    site: WaitSite, contacts: ContactsSteps
) -> None:
    """One wait: its element, its 20 seconds, and its place among the clicks.

    ``Contacts.java:15`` fixes this class's timeout at 20 seconds - the
    longest in the suite - and each of the three sites applies it to a named
    element.  Two of them wait **after** the click they follow (``:50``,
    ``:82``), and ``:82`` waits on ``editTitle`` rather than on the
    ``firstUser`` it clicked, which is how the source establishes that the
    record's form opened.
    """
    contacts.run(site.phrase)

    assert len(contacts.waits) == 1, (
        f"expected one explicit wait in {site.phrase!r} "
        f"(Contacts.java:{site.line}), recorded {contacts.waits!r}"
    )

    record = contacts.waits[0]

    assert record.locator == site.locator
    assert record.timeout == WAIT_TIMEOUT_SECONDS

    clicks = contacts.positions_of(CLICK_OPERATION)

    assert len([index for index in clicks if index < record.position]) == (
        site.clicks_before
    )
    assert len([index for index in clicks if index > record.position]) == (
        site.clicks_after
    )


def test_name_field_is_waited_on_before_it_is_cleared_or_typed_into(
    contacts: ContactsSteps,
) -> None:
    """``Contacts.java:31-33`` waits, *then* clears, *then* types.

    The one wait in the class that precedes every operation on its own target,
    and the only ``clear()`` in the class.  Clearing an invisible field is
    what the wait exists to prevent, so the order is the behaviour.
    """
    contacts.run(f'User enters name "{NAME_VALUE}"')

    wait_position = contacts.waits[0].position
    clear_positions = contacts.positions_of(CLEAR_OPERATION)
    send_positions = contacts.positions_of(SEND_KEYS_OPERATION)

    assert len(clear_positions) == 1
    assert len(send_positions) == 1
    assert wait_position < clear_positions[0] < send_positions[0]


def test_every_wait_call_site_is_reached_through_the_module_globals(
    contacts: ContactsSteps, stub_driver: Any
) -> None:
    """The seam's own precondition - ``Contacts.java:31``, ``:50``, ``:82``.

    Stated as a test so that a call site this module cannot intercept fails
    with an explanation rather than as an attempt to open a browser somewhere
    else in the suite.  Two things hold: the step module binds at least one
    name beginning with the wait prefix, and every wait the three bodies take
    came through one of the bindings that were replaced - so all three sites
    are observed and none reached the real driver lifecycle.
    """
    assert contacts.helpers, "no wait binding was found to intercept"

    stub_driver.set_text(ContactsPage.DELETE_INPUT, DELETED_MESSAGE)
    observed: list[str] = []

    for method in JAVA_METHODS:
        contacts.reset()
        contacts.run(method.phrase)
        observed.extend(record.helper for record in contacts.waits)

    assert len(observed) == len(WAIT_SITES)
    assert set(observed) <= set(contacts.helpers)


# =========================================================================== #
# Contacts.java:72-77 - the sole assertion, driven both ways
# =========================================================================== #


def test_deleted_profile_passes_when_the_entry_reads_deleted(
    contacts: ContactsSteps, stub_driver: Any
) -> None:
    """``Contacts.java:72-77`` accepts exactly ``"Deleted"``.

    ``Assert.assertEquals(actualMsg, expectedMsg)`` at ``:76`` is the
    two-argument form with no message string, so the port is a bare
    ``assert``; the subject is ``deleteInput``'s own text, read through the
    same locator ``:61-66`` clicked.
    """
    stub_driver.set_text(ContactsPage.DELETE_INPUT, DELETED_MESSAGE)

    contacts.run("User sees deleted profile")

    assert contacts.calls == (
        _find(ContactsPage.DELETE_INPUT),
        _read_text(ContactsPage.DELETE_INPUT),
    )


@pytest.mark.parametrize(
    "reported",
    ("", "Draft", "deleted", "DELETED", " Deleted", "Deleted ", "Deleted record"),
    ids=(
        "empty",
        "other-word",
        "lower-case",
        "upper-case",
        "leading-space",
        "trailing-space",
        "superstring",
    ),
)
def test_deleted_profile_fails_for_any_other_text(
    reported: str, contacts: ContactsSteps, stub_driver: Any
) -> None:
    """The comparison is exact, and carries no message - ``Contacts.java:76``.

    ``assertEquals`` is equality, not containment and not a
    case-insensitive or trimmed comparison, so every value here must fail.
    The two-argument form supplies no message, which the port preserves: the
    raised error carries none either (AAP 0.5.2, deviation 16 - the assertion
    subject and message are parity, their formatting is not).
    """
    stub_driver.set_text(ContactsPage.DELETE_INPUT, reported)

    with pytest.raises(AssertionError) as raised:
        contacts.run("User sees deleted profile")

    assert str(raised.value) == "", (
        f"the assertion of Contacts.java:76 carries no message string, but "
        f"the failure reported {str(raised.value)!r}"
    )
    assert contacts.calls == (
        _find(ContactsPage.DELETE_INPUT),
        _read_text(ContactsPage.DELETE_INPUT),
    )


def test_module_holds_exactly_one_assertion(step_module_tree: ast.Module) -> None:
    """One ``assert`` in the module, in ``user_sees_deleted_profile``.

    ``Contacts.java`` asserts once in 105 lines (``:76``).  Definition ``:85``
    in particular reads "sees the updated contact details" and checks nothing
    whatever, and ``:47`` reads "sees the created new contact details" and
    checks nothing either - so a second assertion anywhere in this module
    would be an addition to the source's behaviour, not a fix to it.
    """
    owners = [
        function.name
        for function in _functions(step_module_tree)
        if any(isinstance(node, ast.Assert) for node in ast.walk(function))
    ]

    assert owners == ["user_sees_deleted_profile"], (
        f"expected the only assertion of {STEP_MODULE_RELATIVE} in "
        f"user_sees_deleted_profile (Contacts.java:76), found assertions in "
        f"{owners}"
    )

    asserts = [
        node for node in ast.walk(step_module_tree) if isinstance(node, ast.Assert)
    ]

    assert len(asserts) == 1
    assert asserts[0].msg is None, (
        "Assert.assertEquals(actualMsg, expectedMsg) at Contacts.java:76 is "
        "the two-argument form with no message string"
    )


# =========================================================================== #
# Module boundary, by source inspection (AAP 0.4.2)
# =========================================================================== #


def test_every_definition_is_registered_with_step_and_none_with_a_keyword(
    step_module_tree: ast.Module,
) -> None:
    """14 ``@step`` decorators and no ``@given``/``@when``/``@then``.

    The structural half of the keyword-agnostic rule the registry test asserts
    behaviourally, and the reason ``Contact.feature:5-6`` can invoke two
    ``When``-annotated definitions as ``Given``.
    """
    decorated = [
        function
        for function in _functions(step_module_tree)
        if "step" in _decorator_names(function)
    ]

    assert len(decorated) == 14

    keyword_bound = {
        function.name: sorted(_decorator_names(function) & {"given", "when", "then"})
        for function in _functions(step_module_tree)
        if _decorator_names(function) & {"given", "when", "then"}
    }

    assert keyword_bound == {}


def test_module_imports_no_browser_library_and_no_locator_strategy(
    step_module_tree: ast.Module,
) -> None:
    """No browser binding and no ``By`` - AAP 0.4.2's import boundary.

    ``app/automation`` is the only package that may reach the browser library,
    and of the surface it re-exports the locator-strategy constant is imported
    by ``login_steps`` alone, for the port of ``LoginSD.java:56`` - the one
    Java step that built a locator inline instead of using a page field.
    ``Contacts.java`` has no such step: all 16 of its locators come from
    ``ContactsP``.
    """
    imports = _imports(step_module_tree)
    browser = [
        (module, name)
        for module, name in imports
        if module.split(".")[0] == "selenium" or name.split(".")[0] == "selenium"
    ]

    assert browser == []
    assert "By" not in {name for _, name in imports}
    assert "By" not in _referenced_names(step_module_tree)


def test_module_imports_nothing_from_the_interaction_helpers(
    step_module_tree: ast.Module,
) -> None:
    """No keyboard or action-chain helper - AAP 0.4.1 names this module for it.

    ``Contacts.java`` is the one step class of the eleven that imports neither
    the keyboard-key class nor the action-builder class.  Its five
    ``sendKeys`` calls are plain sends on a located element, so importing a
    key helper here would give the port a capability the source lacks - a
    positive requirement, not an omission.
    """
    imports = _imports(step_module_tree)
    interaction_names = {"press_keys", "action_chain", "Keys", "ActionChains"}

    assert [
        (module, name)
        for module, name in imports
        if module.endswith("interactions") or name in interaction_names
    ] == []
    assert _referenced_names(step_module_tree) & interaction_names == frozenset()


def test_module_reads_no_configuration(step_module_tree: ast.Module) -> None:
    """No configuration key - ``Contacts.java`` reads none.

    ``ConfigurationReader`` is touched by ``Session.java``, ``LoginSD.java``
    and ``EmployeeStage.java`` only; the Contacts class reads no property, so
    its port imports nothing from the configuration module.  A default
    injected here would be a value the source never supplies.
    """
    imports = _imports(step_module_tree)
    config_names = {
        "get_browser",
        "get_empl_title",
        "get_password",
        "get_property",
        "get_url",
        "get_username",
        "get_web_table_url",
        "set_userdata",
        "CONFIG_KEYS",
    }

    assert [
        (module, name)
        for module, name in imports
        if module == "app.config" or module.startswith("app.config.")
    ] == []
    assert {name for _, name in imports} & config_names == set()
    assert _referenced_names(step_module_tree) & config_names == frozenset()


def test_module_never_creates_or_quits_a_session(step_module_tree: ast.Module) -> None:
    """No driver lifecycle - AAP 0.3.3 gives it a single owner.

    ``features/environment.py`` has already created the session before any
    step runs and quits it afterwards, porting ``Hooks.java:11-18``.  No
    ``Contacts.java`` method touches the lifecycle: each reads the session
    only through its page object.
    """
    lifecycle = {"get_driver", "quit_driver"}
    imports = _imports(step_module_tree)

    assert {name for _, name in imports} & lifecycle == set()
    assert _referenced_names(step_module_tree) & lifecycle == frozenset()


def test_module_binds_the_standard_library_fixed_delay(
    step_module_tree: ast.Module,
) -> None:
    """The five delays are fixed delays - AAP 0.4.1, and ``Contacts.java:19``.

    The delay primitive comes from the standard library's time module, taken
    into this module's own globals - which is both what makes it a *fixed*
    delay rather than an explicit wait and what makes the seam above able to
    intercept it.  Converting any of the five into a wait would change timing
    behaviour in a suite whose steps depend on Odoo's client-side rendering,
    and AAP 0.4.1 declines to do it.
    """
    assert ("time", DELAY_BINDING) in _imports(step_module_tree)


def test_module_declares_no_module_level_state(step_module_tree: ast.Module) -> None:
    """No page instance, driver reference or wait object at module scope.

    ``Contacts.java`` builds its page object and its 20-second wait as fields,
    at glue construction.  Constructing either at module scope in Python would
    bind whichever worker process imported the module first, so both move
    inside the step bodies (AAP 0.4.2) - the page per call, the timeout as a
    literal argument.  The field-type registration is a call, not an
    assignment, so no module-level binding remains.
    """
    assignments = [
        node
        for node in step_module_tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign))
    ]

    assert assignments == []


# =========================================================================== #
# features/Contact.feature - the file AAP 0.4.1 pairs with this module
# =========================================================================== #


def test_feature_header_keeps_the_sources_mis_title(
    feature_lines: tuple[str, ...],
) -> None:
    """``Contact.feature:1`` reads "Inventory feature", and stays that way.

    Mis-titled in the source, which makes it share a JSON ``id`` slug with
    ``Inventory.feature``.  AAP 0.2.2 preserves both the title and the
    collision, and AAP 0.3.1 is why the HTTP report routes key on a feature's
    index rather than on that ``id``.
    """
    assert feature_lines[0] == "Feature: Testinium app Inventory feature"


def test_feature_carries_no_tag(feature_lines: tuple[str, ...]) -> None:
    """``Contact.feature`` is untagged - one of the source's five such files.

    Being untagged, it is not selected by the default ``@Smoke`` filter and is
    reachable only by a negative expression such as ``not @Smoke``.  A tag
    added here would change which scenarios the default run selects.
    """
    tagged = [
        (number, line)
        for number, line in enumerate(feature_lines, start=1)
        if line.strip().startswith("@")
    ]

    assert tagged == []


def test_feature_holds_two_outlines_and_two_scenarios(
    feature_lines: tuple[str, ...],
) -> None:
    """Two ``Scenario Outline``\\ s and two plain ``Scenario``\\ s.

    Over a two-step ``Background``, and the outlines carry one Examples row
    each - the data ``Contact.feature:17`` and ``:36`` supply, which is what
    the three parameterized steps above are driven with.
    """
    stripped = [line.strip() for line in feature_lines]
    outlines = [line for line in stripped if line.startswith("Scenario Outline:")]
    scenarios = [line for line in stripped if line.startswith("Scenario:")]
    examples = [line for line in stripped if line.startswith("Examples:")]

    assert len(outlines) == 2
    assert len(scenarios) == 2
    assert len(examples) == 2


def test_commented_out_steps_stay_commented(feature_lines: tuple[str, ...]) -> None:
    """``Contact.feature:22-23`` stay dead, with ``Contacts.java:90-93``.

    The first is the step the commented-out fifteenth definition would have
    implemented; the second is a repeat of the Action-delete step.
    Uncommenting either would need that definition, and AAP 0.2.2 preserves
    the source's inconsistencies rather than tidying them.
    """
    for number in (22, 23):
        line = feature_lines[number - 1]

        assert line.startswith("#"), (
            f"Contact.feature:{number} is {line!r}; the source holds it "
            f"commented out"
        )

    assert DEAD_PHRASE in feature_lines[21]
    assert "User clicks Action to choose delete button" in feature_lines[22]


def test_background_invokes_two_when_definitions_as_given(
    feature_lines: tuple[str, ...],
) -> None:
    """``Contact.feature:5-6`` - two consecutive cross-keyword uses.

    Line 5 invokes ``User login to test other features``, declared ``@When``
    at ``Session.java:12``, and line 6 invokes ``User is at Contact
    dashboard``, declared ``@When`` at ``Contacts.java:17``.  Both resolve
    only because every definition in the port registers with ``@step``: bound
    to their declaring keyword they would go undefined and every scenario in
    the file would fail before its first step.
    """
    assert feature_lines[4].strip() == "Given User login to test other features"
    assert feature_lines[5].strip() == "Given User is at Contact dashboard"

    session_matches = find_all_step_matches("User login to test other features")
    contacts_matches = find_all_step_matches("User is at Contact dashboard")

    assert len(session_matches) == 1
    assert session_matches[0].bucket == STEP_BUCKET
    assert len(contacts_matches) == 1
    assert contacts_matches[0].bucket == STEP_BUCKET
    assert contacts_matches[0].module_name == STEP_MODULE_NAME


@pytest.mark.parametrize(
    ("phrase", "owner", "anchor"),
    FOREIGN_PHRASES,
    ids=[anchor for _, _, anchor in FOREIGN_PHRASES],
)
def test_foreign_phrase_is_declared_by_its_own_module(
    phrase: str, owner: str, anchor: str
) -> None:
    """Two phrases the feature uses and ``Contacts.java`` does not declare.

    ``User login to test other features`` belongs to ``Session.java:12`` and
    ``User clicks save button`` to ``Notes.java:44``.  Declaring either here
    as a fifteenth definition would make it ambiguous across two step modules
    - which is the other way the count of fourteen can go wrong.
    """
    matches = find_all_step_matches(phrase)

    assert len(matches) == 1, (
        f"{phrase!r} ({anchor}) resolved to "
        f"{[(match.pattern, match.location) for match in matches]}"
    )
    assert matches[0].module_name == owner
