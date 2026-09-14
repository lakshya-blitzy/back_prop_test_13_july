r"""Per-method Java-parity tests for ``features/steps/notes_steps.py``.

Java anchor
-----------
``src/main/java/com/testinium/step_definitions/Notes.java`` at pinned revision
``47e9d697e4a9a85da889f94a846fdf47af28a240``, with its two page objects
``pages/NotesP.java`` and ``pages/InventoryP.java`` and its feature file
``src/main/resources/features/Notes.feature``.  All four are **REFERENCE** under
AAP 0.2.1 and are never modified; every expectation in this module is
transcribed from them into a module constant and carries the Java line it came
from in a comment, so the suite asserts the source without reading
``/opt/reference`` at run time and therefore runs on any host.

What this module discharges
---------------------------
The per-module obligation of AAP 0.4.1: *"for each of the ten step modules,
``tests/test_steps_<area>.py`` drives the module against a stubbed driver and
asserts, for every step method in the corresponding Java class, that the port
performs the same observable operations in the same order ... A step method with
no corresponding assertion in its module's test is a gap, and the module test
enumerates the Java class's methods so an omission fails rather than passes
silently."*  For ``Notes.java`` that is 11 methods, three 20-second wait sites
(``Notes.java:19`` builds the one ``WebDriverWait``), exactly one fixed
two-second delay, two action-chain pauses, three assertions and two page
objects.

Fail-closed, in three independent directions
--------------------------------------------
:data:`CENSUS` is the transcribed inventory and nothing populates it at run
time:

* :func:`test_census_enumerates_exactly_the_eleven_java_methods` pins its size
  and its internal consistency;
* :func:`test_every_census_phrase_is_claimed_by_a_test_in_this_module` proves
  each phrase names a parity test that really exists here, so a method cannot be
  quietly left unasserted;
* :func:`test_step_module_declares_exactly_the_census_definitions` compares the
  ``@step``-decorated functions **AST-parsed out of the step module** against
  the census, in source order, so a twelfth definition, a renamed function or an
  unported method fails rather than passing silently.

The headline risk: two identically-selected save buttons
--------------------------------------------------------
``NotesP.java:32-33`` and ``InventoryP.java:23-24`` declare the **same** XPath,
``//button[@class='btn btn-primary btn-sm o_form_button_save']``.  A driver log
therefore cannot say which page object a save-button click came from, and
``Notes.java:65`` is precisely the line where the source reaches across from a
Notes step into ``inventoryP`` - a copy-paste slip in the original that AAP
0.2.2 preserves.  Substituting either page object for the other would pass every
runtime check and still misrepresent the source, so this module proves the
distinction two ways, and both would break if the two sites were swapped:

* **identity-aware page spies** - the step module's ``_notes`` and
  ``_inventory`` binders are replaced by recorders that call the real binder and
  report the class of the object it returned, together with every attribute read
  off it, in order (:data:`PageAccess`);
* **AST receiver tracing** - each body's ordered
  ``(receiver_binder, attribute, operation)`` triples, with the receiver
  variable traced back to its ``_notes(context)`` / ``_inventory(context)``
  assignment (:func:`page_operations`).

:func:`test_notes_and_inventory_save_buttons_declare_the_same_xpath` states the
value identity as a declared fact, so a reader can see why the log alone is
insufficient.

How a step body is reached
--------------------------
Through the registry, never by importing the step module: ``tests/conftest.py``
loads ``features/steps/`` once per session, and a second registration of a
changed definition raises behave's ``AmbiguousStep``.  So every test resolves a
phrase with the ``resolve_step`` fixture and runs it against the
``fake_context`` / ``stub_driver`` pair.  ``match.func.__globals__`` *is* the
step module's namespace, which is where the four seams are patched with
``monkeypatch`` - always restored, so no patch outlives a test:

=========================  ===============================================
Seam                       Why it is replaced
=========================  ===============================================
``sleep``                  A real 2-second delay would be 2 seconds of test
                           time; the recorder also timestamps the delay
                           *into the driver's own log*, which is what makes
                           "first in the body, before the click" assertable
                           rather than merely countable.
``wait_visible_element``   Pins the literal timeout ``20`` and the waited-on
                           element.  Recovered tolerantly from ``*args`` and
                           ``**kwargs`` because a sibling unit may change the
                           helper's call *shape*; the timeout literal and the
                           element identity are the parity claims, not the
                           signature.  The helper takes a **locator** and
                           resolves it inside its predicate on every poll, so
                           it performs no lookup of its own - which is why the
                           recorder also marks the wait *into the driver's own
                           log*, where its position among the clicks and reads
                           around it stays assertable.
``action_chain``           The real ``ActionChains`` rejects a stub element -
                           ``move_to_element`` raises
                           ``AttributeError("move_to requires a WebElement")``
                           - so a duck-typed recording builder stands in.
``_notes`` / ``_inventory``  Optional identity spies, for the save-button
                           distinction above.
=========================  ===============================================

Standing constraints
--------------------
No network, no browser, no real driver, no real sleeping and nothing written
into the repository's ``target/``.  ``selenium.webdriver.common.keys.Keys`` *is*
imported here on purpose: ``Notes.java:35`` sends ``Keys.ENTER``, and asserting
against the binding's own constant says what ``'\ue007'`` would only encode.
The no-selenium rule the suite enforces binds ``features/steps/**``, which
:func:`test_step_module_imports_both_pages_and_no_selenium` checks from this
side of the boundary.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Mapping, Sequence
from functools import cache
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Final, NamedTuple

import pytest
from selenium.webdriver.common.keys import Keys

from app.pages.inventory_page import InventoryPage
from app.pages.notes_page import NotesPage

# A ``(strategy, value)`` pair, the shape ``app/pages/base_page.py`` unpacks
# straight into ``find_element(*locator)`` and the shape the stub driver logs.
type Locator = tuple[str, str]

# One entry of the stub driver's ordered log: an operation name and its
# arguments.  The recorders below append to that same log, which is what lets a
# single expected sequence interleave a fixed delay, an explicit wait, an
# action-chain step and a real element operation.
type Call = tuple[str, tuple[Any, ...]]


# =========================================================================== #
# Section 1 - the transcribed Java authority
#
# Every literal below is the source's, copied from the pinned revision and
# annotated with its line.  Nothing here is derived from the port, so a port
# that drifts from the source fails against this section rather than agreeing
# with itself.
# =========================================================================== #

#: Repository root, from this file's own position: ``tests/`` -> root.  Matches
#: the derivation ``tests/conftest.py`` and ``tests/test_web_routes.py`` use, so
#: all three agree without importing one another.
REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]

#: The step module under test, as a path and as the stem behave reports in
#: :attr:`StepMatch.module_name`.
STEP_MODULE_NAME: Final[str] = "notes_steps"
STEP_MODULE_PATH: Final[Path] = REPO_ROOT / "features" / "steps" / "notes_steps.py"

#: The feature this module's definitions serve.
FEATURE_PATH: Final[Path] = REPO_ROOT / "features" / "Notes.feature"

#: Source encoding for both files.  The step module is UTF-8 Python and the
#: feature file is UTF-8 Gherkin; this is deliberately not the ISO-8859-1 of
#: ``configuration.properties``.
SOURCE_ENCODING: Final[str] = "utf-8"

#: ``Notes.java:19`` - ``new WebDriverWait(Driver.getDriver(), 20)``.  One wait
#: field shared by the class's three wait sites (``:29``, ``:46``, ``:71``), so
#: every wait in this module's port carries this timeout and no other.
WAIT_TIMEOUT_SECONDS: Final[int] = 20

#: ``Notes.java:23`` - ``Thread.sleep(2000)``, the class's only fixed delay and
#: the first statement of its first method.  Seconds here, because the Python
#: delay takes seconds where ``Thread.sleep`` takes milliseconds.
FIXED_SLEEP_SECONDS: Final[int] = 2

#: ``Notes.java:79`` - ``.pause(2000)`` twice inside the one action chain.
#: Selenium's Java builder takes milliseconds and the Python one takes seconds,
#: so 2000 is expressed as 2; transcribing the number verbatim would suspend the
#: scenario for over half an hour at each pause.  This is a **chain pause**, not
#: a fixed delay - :data:`FIXED_SLEEP_SECONDS` is the only fixed delay in the
#: class, and the two are asserted separately.
CHAIN_PAUSE_SECONDS: Final[int] = 2

#: The counts the class as a whole must show, each measured off the Java file.
EXPECTED_DEFINITION_COUNT: Final[int] = 11  # Notes.java:21-88, 11 @When/@Then
EXPECTED_WAIT_SITE_COUNT: Final[int] = 3  # Notes.java:29, :46, :71
EXPECTED_FIXED_SLEEP_COUNT: Final[int] = 1  # Notes.java:23, and nowhere else
EXPECTED_CHAIN_PAUSE_COUNT: Final[int] = 2  # Notes.java:79, both in one chain
EXPECTED_ASSERTION_COUNT: Final[int] = 3  # Notes.java:53, :73, :87

#: The hard-coded values of the step bodies, byte-exact.  The "Testinium App"
#: wording of the first one is the source's and AAP Conflict 8 preserves it
#: rather than reconciling it with the Upgenix and Odoo names elsewhere; the
#: second is the source's shouted placeholder and is not tidied either.
TAG_NAME_LITERAL: Final[str] = "New Tag"  # Notes.java:35
DESCRIPTION_LITERAL: Final[str] = (
    "This note is an example for the Testinium App"  # Notes.java:41
)
NEW_DESCRIPTION_LITERAL: Final[str] = "FKASDFGASDFADSFADS"  # Notes.java:64
CREATED_MESSAGE_LITERAL: Final[str] = "Note created"  # Notes.java:52
TODAY_TABLE_LITERAL: Final[str] = "Today"  # Notes.java:85

#: The Odoo record ids hard-coded into the two kanban-column locators.  They are
#: test data baked into a selector and AAP 0.8 keeps them unparameterized; the
#: drag step's **direction** is stated in terms of them, 1193 grabbed and 1194
#: dropped onto, never the reverse.
NEW_TABLE_RECORD_ID: Final[str] = "1193"  # NotesP.java:38
TODAY_TABLE_RECORD_ID: Final[str] = "1194"  # NotesP.java:41

#: The XPath both save buttons declare - ``NotesP.java:32-33`` and
#: ``InventoryP.java:23-24``, character for character the same string.  This
#: constant exists so the collision is a stated fact of this module rather than
#: an accident a reader has to notice.
SHARED_SAVE_BUTTON_XPATH: Final[str] = (
    "//button[@class='btn btn-primary btn-sm o_form_button_save']"
)

#: ``NotesP.java``'s ten ``@FindBy`` declarations, transcribed as
#: ``{port constant name: (strategy, value)}`` in Java declaration order.  The
#: strategies are Selenium's own ``By`` values: ``"xpath"`` for
#: ``@FindBy(xpath = ...)`` and ``"partial link text"`` for
#: ``@FindBy(partialLinkText = ...)``.  Transcribing them independently is what
#: makes the port's constants checkable rather than self-consistent.
JAVA_NOTES_SELECTORS: Final[Mapping[str, tuple[str, str]]] = {
    # NotesP.java:14-15 - the spaces around the '=' are the source's.
    "TABINDEX": ("xpath", "//a[. = 'Create and Edit...']"),
    "NOTES_MODULE": ("partial link text", "Notes"),  # NotesP.java:17-18
    "CREATING_NOTES": (  # NotesP.java:20-21 - class hyphenated
        "xpath",
        "//button[@class='btn btn-primary btn-sm o-kanban-button-new']",
    ),
    "TAGS_N": ("xpath", "//input[@class='o_input ui-autocomplete-input']"),
    "DESCRIPTION": ("xpath", "//div[@class='note-editable panel-body']"),
    "CREATED_MESSAGE": ("xpath", "//p[.='Note created']"),  # no spaces here
    "SAVE_BTN": ("xpath", SHARED_SAVE_BUTTON_XPATH),  # NotesP.java:32-33
    "APP_K": ("xpath", "//span[.='BDD Approach Framework with Cucumber']"),
    "NEW_TABLE": ("xpath", f"(//div[@data-id='{NEW_TABLE_RECORD_ID}']/div)[2]"),
    "TODAY_TABLE": ("xpath", f"(//div[@data-id='{TODAY_TABLE_RECORD_ID}']/div)[1]"),
}

#: ``InventoryP.java:23-24``, the one Inventory locator ``Notes.java`` touches.
JAVA_INVENTORY_SAVE_BTN: Final[tuple[str, str]] = ("xpath", SHARED_SAVE_BUTTON_XPATH)

# The port's locator tuples, bound once for readability in the expected
# sequences below.  Taken from the page classes rather than retyped - the
# transcription above is what holds them to the Java - except that
# INVENTORY_SAVE_BTN is deliberately kept separate from SAVE_BTN even though the
# two are equal, because which page a click came from is the question this
# module answers and equal values are exactly why the log cannot answer it.
TABINDEX: Final[Locator] = NotesPage.TABINDEX
NOTES_MODULE: Final[Locator] = NotesPage.NOTES_MODULE
CREATING_NOTES: Final[Locator] = NotesPage.CREATING_NOTES
TAGS_N: Final[Locator] = NotesPage.TAGS_N
DESCRIPTION: Final[Locator] = NotesPage.DESCRIPTION
CREATED_MESSAGE: Final[Locator] = NotesPage.CREATED_MESSAGE
SAVE_BTN: Final[Locator] = NotesPage.SAVE_BTN
APP_K: Final[Locator] = NotesPage.APP_K
NEW_TABLE: Final[Locator] = NotesPage.NEW_TABLE
TODAY_TABLE: Final[Locator] = NotesPage.TODAY_TABLE
INVENTORY_SAVE_BTN: Final[Locator] = InventoryPage.SAVE_BTN

# The eleven Gherkin phrases, spelled once each.  ``Notes.java``'s annotation
# text is the contract - Cucumber-JVM matches on text alone - so these are the
# strings a rename would have to change, and every test names one of them.
CLICK_NOTES_MODULE: Final[str] = "User clicks the Notes module"
CLICK_CREATE_BUTTON: Final[str] = "User clicks create button in Notes module"
ENTER_TAG_NAME: Final[str] = "User enters a tag name"
ENTER_DESCRIPTION: Final[str] = "User enters description"
CLICK_SAVE_BUTTON: Final[str] = "User clicks save button"
SEE_CREATED_NOTES: Final[str] = "User sees the created new notes"
CLICK_EDIT_BUTTON: Final[str] = "User clicks the edit button"
ENTER_NEW_DESCRIPTION: Final[str] = "User enters new description"
SEE_NOTES_LIST: Final[str] = "User should see the Notes list"
MOVE_NEW_TO_TODAY: Final[str] = "User move element from New section to Today section"
SEE_TODAY_ELEMENT: Final[str] = "User sees Today new added element"

#: Names of the two page binders in the step module's namespace, which are also
#: the receiver identities :func:`page_operations` reports.
NOTES_BINDER: Final[str] = "_notes"
INVENTORY_BINDER: Final[str] = "_inventory"


class Definition(NamedTuple):
    """One row of the transcribed census: a Java method and who asserts it.

    The ``test`` field is what makes the census fail-closed.  It names a
    function that must exist in this module, so adding a Java method to the
    census without writing its parity test fails, and writing the census without
    the test it claims fails too.
    """

    #: Line of the ``@When``/``@Then`` annotation in ``Notes.java``.
    java_line: int

    #: The annotation's text, which is the whole of Cucumber's matching key.
    phrase: str

    #: The port's step function name, snake-cased from the Java method name.
    function: str

    #: The test in this module that pins this method's behaviour.
    test: str


#: The eleven definitions of ``Notes.java``, in source order.  Enumerated here
#: rather than discovered, so the port is compared against the source instead of
#: against itself; :func:`test_step_module_declares_exactly_the_census_definitions`
#: is what closes the loop in the other direction.
CENSUS: Final[tuple[Definition, ...]] = (
    Definition(
        21,
        CLICK_NOTES_MODULE,
        "user_clicks_the_notes_module",
        "test_definition_01_sleeps_two_seconds_then_clicks_the_notes_module",
    ),
    Definition(
        27,
        CLICK_CREATE_BUTTON,
        "user_clicks_create_button_in_notes_module",
        "test_definition_02_waits_on_then_clicks_the_kanban_create_button",
    ),
    Definition(
        33,
        ENTER_TAG_NAME,
        "user_enters_a_tag_name",
        "test_definition_03_sends_new_tag_and_enter_in_one_call_then_clicks_tabindex",
    ),
    Definition(
        39,
        ENTER_DESCRIPTION,
        "user_enters_description",
        "test_definition_04_types_the_description_then_clicks_the_notes_save_button",
    ),
    Definition(
        44,
        CLICK_SAVE_BUTTON,
        "user_clicks_save_button",
        "test_definition_05_waits_on_and_clicks_the_notes_save_button",
    ),
    Definition(
        49,
        SEE_CREATED_NOTES,
        "user_sees_the_created_new_notes",
        "test_definition_06_compares_the_created_message_with_note_created",
    ),
    Definition(
        56,
        CLICK_EDIT_BUTTON,
        "user_clicks_the_edit_button",
        "test_definition_07_only_clicks_the_existing_note_card",
    ),
    Definition(
        61,
        ENTER_NEW_DESCRIPTION,
        "user_enters_new_description",
        "test_definition_08_clears_retypes_saves_via_inventory_then_reopens_notes",
    ),
    Definition(
        69,
        SEE_NOTES_LIST,
        "user_should_see_the_notes_list",
        "test_definition_09_waits_clicks_then_asserts_the_notes_menu_is_displayed",
    ),
    Definition(
        76,
        MOVE_NEW_TO_TODAY,
        "user_move_element_from_new_section_to_today_section",
        "test_definition_10_drags_from_new_to_today_with_two_pauses_and_no_sleep",
    ),
    Definition(
        82,
        SEE_TODAY_ELEMENT,
        "user_sees_today_new_added_element",
        "test_definition_11_compares_the_today_column_text_with_today",
    ),
)

#: ``Notes.feature:1``, verbatim.  The header says "login feature" for a feature
#: about notes - a mis-title the source shares with ``Contact.feature:1`` - and
#: AAP 0.2.2 lists correcting it as explicitly out of scope, so this module
#: asserts the mistake is still there.
FEATURE_HEADER: Final[str] = "Feature: Testinium app login feature"

#: Gherkin keywords that introduce a step line, so the feature's steps can be
#: extracted without importing a parser.  ``But`` and ``*`` are included for
#: completeness of the extraction; ``Notes.feature`` uses neither.
STEP_KEYWORDS: Final[tuple[str, ...]] = ("Given ", "When ", "And ", "Then ", "But ", "* ")

#: The shared precondition, ``Notes.feature:8``.  It is **not** a Notes
#: definition: ``Session.java:12`` owns it and ``features/steps/session_steps.py``
#: ports it, and re-declaring it here would make every scenario in the feature
#: ambiguous.  Asserted for exactly that reason.
BACKGROUND_PHRASE: Final[str] = "User login to test other features"

#: The module that owns :data:`BACKGROUND_PHRASE`.
SESSION_STEP_MODULE_NAME: Final[str] = "session_steps"

#: Every step line of ``features/Notes.feature`` as ``(line, keyword, phrase)``,
#: transcribed from the pinned ``src/main/resources/features/Notes.feature``.
#: Note lines 21 and 23: the second scenario invokes
#: :data:`ENTER_NEW_DESCRIPTION` **twice**, with a save between them, so that
#: body runs twice per run of that scenario.
FEATURE_STEPS: Final[tuple[tuple[int, str, str], ...]] = (
    (8, "Given", BACKGROUND_PHRASE),
    (11, "When", CLICK_NOTES_MODULE),
    (12, "And", CLICK_CREATE_BUTTON),
    (13, "And", ENTER_TAG_NAME),
    (14, "And", ENTER_DESCRIPTION),
    (15, "And", CLICK_SAVE_BUTTON),
    (16, "Then", SEE_CREATED_NOTES),
    (19, "When", CLICK_NOTES_MODULE),
    (20, "And", CLICK_EDIT_BUTTON),
    (21, "And", ENTER_NEW_DESCRIPTION),
    (22, "And", CLICK_SAVE_BUTTON),
    (23, "And", ENTER_NEW_DESCRIPTION),
    (24, "Then", SEE_NOTES_LIST),
    (27, "When", CLICK_NOTES_MODULE),
    (28, "And", MOVE_NEW_TO_TODAY),
    (29, "Then", SEE_TODAY_ELEMENT),
)

#: The second scenario's steps, ``Notes.feature:19-24``, in order - the walk that
#: exercises the twice-invoked definition.
SECOND_SCENARIO_PHRASES: Final[tuple[str, ...]] = tuple(
    phrase for line, _keyword, phrase in FEATURE_STEPS if 19 <= line <= 24
)


# =========================================================================== #
# Section 2 - reading the step module's syntax tree
#
# The runtime log says what happened; the tree says which *name* it happened
# through.  That is the only way to tell ``notesP.saveBtn`` from
# ``inventoryP.saveBtn``, whose selectors are identical, and it is how a rename
# or a twelfth definition is caught.  Nothing here imports the step module - a
# second registration of its definitions would risk behave's ``AmbiguousStep``.
# =========================================================================== #


@cache
def step_module_tree() -> ast.Module:
    """Parse ``features/steps/notes_steps.py`` once per session.

    :returns: The parsed module.
    :raises FileNotFoundError: If the step module is absent, which would mean
        the port's Notes area had not been written at all.
    """
    source = STEP_MODULE_PATH.read_text(encoding=SOURCE_ENCODING)
    return ast.parse(source, filename=str(STEP_MODULE_PATH))


@cache
def step_function_nodes() -> Mapping[str, ast.FunctionDef]:
    """The ``@step``-decorated functions, keyed by phrase in source order.

    Only a decorator of the exact form ``@step("<phrase>")`` is recognised,
    which is also AAP deviation 7's requirement: every definition registers with
    ``@step`` and none with ``@given``, ``@when`` or ``@then``, so a
    keyword-bound definition would be missing from this mapping and fail the
    census comparison rather than pass unnoticed.

    :returns: ``{phrase: function node}``, insertion-ordered by source position.
    """
    found: dict[str, ast.FunctionDef] = {}

    for node in step_module_tree().body:
        if not isinstance(node, ast.FunctionDef):
            continue

        for decorator in node.decorator_list:
            if (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Name)
                and decorator.func.id == "step"
                and len(decorator.args) == 1
                and isinstance(decorator.args[0], ast.Constant)
                and isinstance(decorator.args[0].value, str)
            ):
                found[decorator.args[0].value] = node

    return found


def step_function_node(phrase: str) -> ast.FunctionDef:
    """The function node implementing *phrase*.

    :param phrase: One of the eleven census phrases.
    :returns: Its ``ast.FunctionDef``.
    :raises AssertionError: If no ``@step`` decorator carries that phrase, which
        is a missing or renamed definition rather than a test problem.
    """
    node = step_function_nodes().get(phrase)

    assert node is not None, (
        f"no @step definition in {STEP_MODULE_PATH} carries the phrase "
        f"{phrase!r}; the census requires it"
    )
    return node


def executable_statements(phrase: str) -> tuple[ast.stmt, ...]:
    """A body's statements with its docstring dropped.

    "The delay is the first statement of the method" (``Notes.java:23``) is a
    claim about executable code; the port's bodies open with a docstring, which
    is documentation rather than a statement and would otherwise always occupy
    first position.

    :param phrase: One of the eleven census phrases.
    :returns: The body's statements, docstring excluded, in source order.
    """
    body = list(step_function_node(phrase).body)

    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body.pop(0)

    return tuple(body)


def _binder_variables(function: ast.FunctionDef) -> Mapping[str, str]:
    """Map each local page variable to the binder that produced it.

    Recognises ``notes = _notes(context)`` and ``inventory = _inventory(context)``
    - the two forms every body in this module's port uses - and nothing else, so
    a page object obtained some other way is absent from the map and its
    operations are therefore absent from :func:`page_operations`, which fails the
    per-definition assertions instead of being silently accepted.

    :param function: A step function node.
    :returns: ``{local name: binder name}``.
    """
    bindings: dict[str, str] = {}

    for node in ast.walk(function):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id in (NOTES_BINDER, INVENTORY_BINDER)
        ):
            bindings[node.targets[0].id] = node.value.func.id

    return bindings


class PageOperation(NamedTuple):
    """One page-object access in a step body, as the tree records it.

    The unit of the Java-to-Python comparison this module performs on receivers:
    ``notesP.saveBtn.click()`` is ``("_notes", "save_btn", "click")`` and
    ``inventoryP.saveBtn.click()`` is ``("_inventory", "save_btn", "click")``,
    which are different triples even though the two produce identical driver
    logs.
    """

    #: ``"_notes"`` or ``"_inventory"`` - which binder the receiver came from.
    binder: str

    #: The page-object attribute read, as the body spells it: the lower-case
    #: accessor ``"save_btn"`` where an element is resolved and operated on, and
    #: the upper-case constant ``"SAVE_BTN"`` where the locator itself is handed
    #: to a wait helper.
    attribute: str

    #: The method or property applied to the resolved element - ``"click"``,
    #: ``"send_keys"``, ``"clear"``, ``"text"``, ``"is_displayed"`` - or ``None``
    #: when the attribute is handed to a helper instead, which is the shape of a
    #: wait site, a keyboard call and an action-chain argument.
    operation: str | None


def page_operations(phrase: str) -> tuple[PageOperation, ...]:
    """Ordered page-object accesses in the body implementing *phrase*.

    Ordered by source position rather than by tree traversal, so a chained
    expression spanning several lines - the action chain of ``Notes.java:79`` -
    reports its receivers in the order a reader sees them.

    :param phrase: One of the eleven census phrases.
    :returns: One :class:`PageOperation` per page-object attribute access.
    """
    function = step_function_node(phrase)
    bindings = _binder_variables(function)

    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(function):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    located: list[tuple[tuple[int, int], PageOperation]] = []

    for node in ast.walk(function):
        if not isinstance(node, ast.Attribute):
            continue
        if not (isinstance(node.value, ast.Name) and node.value.id in bindings):
            continue

        # ``page.attr.method(...)`` and ``page.attr.property`` both appear as an
        # outer Attribute whose value is this node; anything else - being passed
        # as an argument - leaves the operation unnamed.
        parent = parents.get(node)
        operation = (
            parent.attr
            if isinstance(parent, ast.Attribute) and parent.value is node
            else None
        )

        located.append(
            (
                (node.lineno, node.col_offset),
                PageOperation(bindings[node.value.id], node.attr, operation),
            )
        )

    located.sort(key=lambda entry: entry[0])
    return tuple(operation for _position, operation in located)


def direct_call_names(phrase: str) -> tuple[str, ...]:
    """Names of the plain function calls in a body, in source order.

    Plain means ``sleep(2)`` or ``action_chain()`` - a call whose callee is a
    bare name - so this is how the presence of the fixed delay, the wait helper,
    the keyboard helper and the chain builder is asserted structurally, and how
    the **absence** of a fixed delay from the drag step is asserted.

    :param phrase: One of the eleven census phrases.
    :returns: The callee names, in source order.
    """
    function = step_function_node(phrase)
    located: list[tuple[tuple[int, int], str]] = []

    for node in ast.walk(function):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            located.append(((node.lineno, node.col_offset), node.func.id))

    located.sort(key=lambda entry: entry[0])
    return tuple(name for _position, name in located)


def assert_nodes(phrase: str) -> tuple[ast.Assert, ...]:
    """The ``assert`` statements of a body, in source order.

    :param phrase: One of the eleven census phrases.
    :returns: Its assertion nodes.
    """
    function = step_function_node(phrase)
    nodes = [node for node in ast.walk(function) if isinstance(node, ast.Assert)]
    nodes.sort(key=lambda node: (node.lineno, node.col_offset))
    return tuple(nodes)


def local_assignments(phrase: str) -> Mapping[str, ast.expr]:
    """Map each simple local name in a body to the expression assigned to it.

    Used to trace an assertion's operands: ``Assert.assertEquals(actualMessage,
    expecgedMessage)`` at ``Notes.java:53`` puts the page read **first** and the
    expected literal second, and that order is only visible by following the two
    locals back to their assignments.

    :param phrase: One of the eleven census phrases.
    :returns: ``{local name: assigned expression}``.
    """
    function = step_function_node(phrase)
    assignments: dict[str, ast.expr] = {}

    for node in ast.walk(function):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            assignments[node.targets[0].id] = node.value

    return assignments


def import_entries() -> tuple[tuple[str | None, str], ...]:
    """Every name the step module imports, as ``(module, name)`` pairs.

    ``import x`` yields ``(None, "x")`` and ``from a import b`` yields
    ``("a", "b")``, which is enough to answer both import-boundary questions:
    that both page classes arrive from the ``app.pages`` package, and that
    nothing arrives from ``selenium``.

    :returns: The pairs, in source order.
    """
    entries: list[tuple[str | None, str]] = []

    for node in ast.walk(step_module_tree()):
        if isinstance(node, ast.Import):
            entries.extend((None, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            entries.extend((node.module, alias.name) for alias in node.names)

    return tuple(entries)


# =========================================================================== #
# Section 3 - the recording doubles
#
# Each one stands in for a seam of the step module's namespace, and each records
# into the stub driver's own ordered log as well as into its own list.  The
# shared log is the point: "the two-second delay is the first thing the body
# does, before the click" is a statement about *interleaving*, and a separate
# list of delays could never make it.
# =========================================================================== #

#: Log-entry names the recorders append.  The ``test.`` prefix cannot collide
#: with a :class:`~tests.conftest.StubDriver` operation, all of which are named
#: after real WebDriver calls.
SLEEP_MARKER: Final[str] = "test.sleep"
WAIT_MARKER: Final[str] = "test.wait_visible_element"
CHAIN_BUILT_MARKER: Final[str] = "test.action_chain"
CHAIN_OPERATION_PREFIX: Final[str] = "test.chain."

#: Namespace keys the seams live under in ``match.func.__globals__``.
SLEEP_SEAM: Final[str] = "sleep"
TIME_MODULE_SEAM: Final[str] = "time"
WAIT_SEAM: Final[str] = "wait_visible_element"
CHAIN_SEAM: Final[str] = "action_chain"

#: Keyword names the wait target may arrive under, if a sibling unit changes the
#: helper's call shape.  Positional remains the form the port uses today.
WAIT_TARGET_KEYWORDS: Final[tuple[str, ...]] = ("element", "target", "locator")

#: Keyword names the timeout may arrive under, for the same reason.
WAIT_TIMEOUT_KEYWORDS: Final[tuple[str, ...]] = ("timeout", "seconds")

#: Sentinel for "this argument was not supplied in any form".
_MISSING: Final[Any] = object()


def normalise_target(target: Any) -> Any:
    """Reduce a resolved element to the locator that produced it.

    The stub driver's elements carry ``locator``, so a wait on an element and a
    wait on a locator pair both become the same comparable value and the
    assertions stay readable whichever shape the helper is called with.

    :param target: An element, a locator pair, or anything else.
    :returns: ``target.locator`` when present, else *target* unchanged.
    """
    return getattr(target, "locator", target)


class WaitCall(NamedTuple):
    """One recorded explicit-wait call.

    Carries the raw arguments as well as the interpreted ones, so a failure
    shows what the step module actually passed rather than only what this
    module made of it.
    """

    #: The waited-on element, reduced to its locator by :func:`normalise_target`.
    target: Any

    #: The timeout, exactly as the call site supplied it.
    timeout: Any

    #: The waited-on element as it arrived, un-normalised, so the recorder can
    #: hand it back to the step body the way the real helper does.
    raw_target: Any

    #: Positional arguments as received.
    args: tuple[Any, ...]

    #: Keyword arguments as received.
    kwargs: Mapping[str, Any]


def interpret_wait_call(args: Sequence[Any], kwargs: Mapping[str, Any]) -> WaitCall:
    """Recover the waited-on element and the timeout from a call of any shape.

    Tolerant on purpose.  ``app/automation/waits.py`` is owned by another unit
    and its call *shape* may change - element first and timeout second today,
    conceivably keyword-supplied tomorrow - while the parity claims are the
    literal timeout of ``Notes.java:19`` and the identity of the element waited
    on.  So both are recovered from positional and keyword arguments alike, and
    a call that carries neither fails loudly here rather than silently
    recording ``None``.

    :param args: Positional arguments the step module passed.
    :param kwargs: Keyword arguments the step module passed.
    :returns: The interpreted call.
    :raises AssertionError: If no target or no timeout can be recovered.
    """
    positional = list(args)
    target = positional.pop(0) if positional else _MISSING
    timeout = positional.pop(0) if positional else _MISSING

    if target is _MISSING:
        for keyword in WAIT_TARGET_KEYWORDS:
            if keyword in kwargs:
                target = kwargs[keyword]
                break

    if timeout is _MISSING:
        for keyword in WAIT_TIMEOUT_KEYWORDS:
            if keyword in kwargs:
                timeout = kwargs[keyword]
                break

    assert target is not _MISSING, (
        f"the wait helper was called without a recognisable target: "
        f"args={args!r} kwargs={dict(kwargs)!r}"
    )
    assert timeout is not _MISSING, (
        f"the wait helper was called without a recognisable timeout: "
        f"args={args!r} kwargs={dict(kwargs)!r}. Notes.java:19 fixes it at "
        f"{WAIT_TIMEOUT_SECONDS}"
    )

    return WaitCall(
        target=normalise_target(target),
        timeout=timeout,
        raw_target=target,
        args=tuple(args),
        kwargs=dict(kwargs),
    )


class RecordingActionChain:
    """A duck-typed stand-in for ``ActionChains``, recording every call.

    Required rather than preferred: the real builder validates its arguments,
    and ``ActionChains.move_to_element`` raises
    ``AttributeError("move_to requires a WebElement")`` when handed a stub
    element, so a real chain cannot be driven by a stub driver at all.

    Every method returns ``self``, which is what the builder's fluent interface
    guarantees and what ``Notes.java:79``'s single chained expression relies on.
    Unknown methods are recorded too rather than rejected, so an operation the
    source never performs - a ``drag_and_drop``, say - shows up in the log and
    fails the sequence assertion instead of being absorbed.
    """

    def __init__(self, driver: Any, index: int) -> None:
        """Bind this builder to the driver whose log it shares.

        :param driver: The recorder whose ordered log the chain's operations are
            appended to, so they interleave with the element lookups the chain's
            arguments performed.
        :param index: 1-based construction number, recorded at build time so
            that "a new builder per call" (``Notes.java:78``, inside the method)
            is assertable.
        """
        self._driver = driver

        #: Every operation performed on this builder, as
        #: ``(name, normalised args)`` in call order.
        self.log: list[tuple[str, tuple[Any, ...]]] = []

        #: This builder's construction number.
        self.index = index

    def __getattr__(self, name: str) -> Callable[..., RecordingActionChain]:
        """Return a recorder for any builder method the body calls.

        :param name: The method name, recorded verbatim.
        :returns: A callable that records the call and returns this builder.
        """

        def record(*args: Any) -> RecordingActionChain:
            normalised = tuple(normalise_target(argument) for argument in args)
            self.log.append((name, normalised))
            self._driver.calls.append((f"{CHAIN_OPERATION_PREFIX}{name}", normalised))
            return self

        return record

    def __repr__(self) -> str:
        """Render the operations performed, which is what a failure needs.

        :returns: A short representation.
        """
        return f"<RecordingActionChain #{self.index} {[name for name, _ in self.log]}>"


class PageAccess(NamedTuple):
    """One attribute read off a spied page object.

    The identity evidence: the page *class* is recorded beside the attribute, so
    ``save_btn`` read from the Notes page and ``save_btn`` read from the
    Inventory page are different observations even though both resolve the same
    XPath.
    """

    #: The class name of the page object the attribute was read from.
    page_class: str

    #: The attribute name, as the step body spelled it.
    attribute: str


class PageSpy:
    """Forwards every attribute read to a real page object, recording it.

    Wraps the object the production binder returned rather than building one, so
    the recorded class name is the class the port actually binds - a binder
    rewritten to hand back the other page object is caught here.  Reads are
    forwarded unchanged, so the page's lazy resolution still reaches the stub
    driver and the ordinary driver log is unaffected.
    """

    def __init__(self, page: Any, accesses: list[PageAccess]) -> None:
        """Wrap *page* and append its reads to *accesses*.

        :param page: The page object to forward to.
        :param accesses: Shared ordered list every spy appends to, so accesses
            across both page objects of one step body stay in one sequence.
        """
        self._page = page
        self._accesses = accesses

    def __getattr__(self, name: str) -> Any:
        """Record the read, then forward it.

        :param name: The attribute the step body asked for.
        :returns: Whatever the wrapped page returns - for a locator accessor,
            a freshly resolved element.
        :raises AttributeError: If the wrapped page has no such attribute, which
            keeps a misspelled accessor a failure rather than a silent ``None``.
        """
        self._accesses.append(PageAccess(type(self._page).__name__, name))
        return getattr(self._page, name)

    def __repr__(self) -> str:
        """Name the page object being spied on.

        :returns: A short representation.
        """
        return f"<PageSpy {type(self._page).__name__}>"


def install_sleep_recorder(
    monkeypatch: pytest.MonkeyPatch,
    namespace: dict[str, Any],
    recorder: Callable[[float], None],
) -> str:
    """Replace the step module's fixed-delay seam, whichever shape it has.

    ``features/steps/notes_steps.py`` does ``from time import sleep``, so the
    seam is normally a callable in the module namespace.  A module that switched
    to ``import time`` would call ``time.sleep`` instead, and the delay would go
    unrecorded - and a real 2-second delay would silently join the suite's
    runtime - so that shape is handled too rather than assumed away.

    :param monkeypatch: The patcher, which restores either shape afterwards.
    :param namespace: The step module's global namespace.
    :param recorder: The replacement callable.
    :returns: ``"sleep"`` or ``"time.sleep"``, naming the shape that was found.
    :raises AssertionError: If neither shape is present, which would mean the
        fixed delay of ``Notes.java:23`` had lost its call site.
    """
    if callable(namespace.get(SLEEP_SEAM)):
        monkeypatch.setitem(namespace, SLEEP_SEAM, recorder)
        return SLEEP_SEAM

    module = namespace.get(TIME_MODULE_SEAM)

    if module is not None and hasattr(module, SLEEP_SEAM):
        monkeypatch.setattr(module, SLEEP_SEAM, recorder)
        return f"{TIME_MODULE_SEAM}.{SLEEP_SEAM}"

    raise AssertionError(
        f"no fixed-delay seam found in {STEP_MODULE_NAME}: expected a callable "
        f"{SLEEP_SEAM!r} or a {TIME_MODULE_SEAM!r} module in the namespace. "
        f"Notes.java:23 requires one call site"
    )


# =========================================================================== #
# Section 4 - the probe
#
# One object per test, holding the resolved phrases, the patched namespace, the
# recorders and the single interleaved log.  Every parity test below reads as
# "run this phrase, then compare the timeline with the Java method".
# =========================================================================== #


class NotesStepProbe:
    """Runs Notes step bodies with every seam recorded.

    Patching happens once, at construction, against the namespace all eleven
    definitions share; the page spies are installed per run, because most
    definitions are asserted through the driver log alone and only the
    save-button sites need the identity evidence.
    """

    def __init__(
        self,
        resolve: Callable[[str], Any],
        context: Any,
        driver: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Resolve the census, patch the seams and start with an empty log.

        :param resolve: The ``resolve_step`` fixture.
        :param context: The ``fake_context`` fixture - the behave stand-in.
        :param driver: The ``stub_driver`` fixture, reached by the context.
        :param monkeypatch: The patcher; every seam it touches is restored when
            the test ends, so nothing leaks into the next test or into a
            scenario run.
        """
        self._monkeypatch = monkeypatch
        self.context = context
        self.driver = driver

        #: Every resolved definition, keyed by phrase.
        self.matches: dict[str, Any] = {
            definition.phrase: resolve(definition.phrase) for definition in CENSUS
        }

        #: The step module's global namespace - the seam surface.  All eleven
        #: definitions come from one module, which
        #: :func:`test_all_definitions_share_one_step_module_namespace` asserts.
        self.namespace: dict[str, Any] = self.matches[CLICK_NOTES_MODULE].func.__globals__

        #: Seconds passed to each fixed delay, in order.
        self.sleeps: list[float] = []

        #: Every explicit wait, in order.
        self.waits: list[WaitCall] = []

        #: Every action builder constructed, in construction order.
        self.chains: list[RecordingActionChain] = []

        #: Every page-object attribute read while spies were installed.
        self.page_accesses: list[PageAccess] = []

        #: Which binder was called, in order, while spies were installed.
        self.binder_calls: list[str] = []

        #: The production page binders, captured **before** any spy is
        #: installed.  Captured once and reused, because a spy that wrapped
        #: whatever the namespace currently holds would wrap the previous spy on
        #: a second run and record every access twice over.
        self._original_binders: dict[str, Callable[[Any], Any]] = {
            name: self.namespace[name] for name in (NOTES_BINDER, INVENTORY_BINDER)
        }

        #: Whether the identity spies are in place; installing them is
        #: idempotent for the same reason.
        self._spies_installed = False

        #: The shape the fixed-delay seam was found in.
        self.sleep_seam_shape: str = install_sleep_recorder(
            monkeypatch, self.namespace, self._record_sleep
        )

        monkeypatch.setitem(self.namespace, WAIT_SEAM, self._record_wait)
        monkeypatch.setitem(self.namespace, CHAIN_SEAM, self._build_chain)

    # -- the seams --------------------------------------------------------- #

    def _record_sleep(self, seconds: float) -> None:
        """Record a fixed delay in the shared timeline instead of sleeping.

        :param seconds: The delay the body asked for.
        :returns: ``None``, matching ``time.sleep``.
        """
        self.sleeps.append(seconds)
        self.driver.calls.append((SLEEP_MARKER, (seconds,)))

    def _record_wait(self, *args: Any, **kwargs: Any) -> Any:
        """Record an explicit wait and return the element, as the helper does.

        :param args: Positional arguments from the call site.
        :param kwargs: Keyword arguments from the call site.
        :returns: The waited-on element, un-normalised, so the body may keep
            using it - which ``app/automation/waits.py`` also does, since
            ``visibilityOf`` resolves to the element it was given.
        """
        call = interpret_wait_call(args, kwargs)
        self.waits.append(call)
        self.driver.calls.append((WAIT_MARKER, (call.target, call.timeout)))
        return call.raw_target

    def _build_chain(self, *args: Any, **kwargs: Any) -> RecordingActionChain:
        """Return a fresh recording builder and note its construction.

        :param args: Ignored; the port passes nothing (``Notes.java:78`` passes
            the session, which the port's helper resolves itself).
        :param kwargs: Ignored, for the same reason.
        :returns: A new :class:`RecordingActionChain`.
        """
        chain = RecordingActionChain(self.driver, len(self.chains) + 1)
        self.chains.append(chain)
        self.driver.calls.append((CHAIN_BUILT_MARKER, (chain.index,)))
        return chain

    def _spy_binder(self, name: str) -> Callable[[Any], PageSpy]:
        """Wrap one page binder so its result's class is recorded.

        Wraps the **production** binder captured at construction, so the class
        name recorded is the class the port itself hands back rather than one
        this module chose - a binder rewritten to return the other page object
        is caught - and so a second run does not wrap a spy in a spy.

        :param name: ``"_notes"`` or ``"_inventory"``.
        :returns: A replacement binder returning a :class:`PageSpy`.
        """
        original = self._original_binders[name]

        def bind(context: Any) -> PageSpy:
            self.binder_calls.append(name)
            return PageSpy(original(context), self.page_accesses)

        return bind

    # -- driving a body ---------------------------------------------------- #

    def run(self, phrase: str, *, spy_pages: bool = False) -> None:
        """Run the definition matching *phrase* against the stub driver.

        :param phrase: One of the eleven census phrases.
        :param spy_pages: Install the identity-aware page spies for this run.
            Off by default, so the driver timeline of most definitions is free of
            spy indirection; on for the save-button sites, where which page
            object was used is the whole question.
        :returns: ``None``.
        """
        if spy_pages and not self._spies_installed:
            for binder in (NOTES_BINDER, INVENTORY_BINDER):
                self._monkeypatch.setitem(self.namespace, binder, self._spy_binder(binder))

            self._spies_installed = True

        self.matches[phrase].run(self.context)

    def run_all(self, phrases: Sequence[str]) -> None:
        """Run several definitions in order, as a scenario would.

        :param phrases: The phrases to run, in scenario order.
        :returns: ``None``.
        """
        for phrase in phrases:
            self.run(phrase)

    def reset(self) -> None:
        """Clear the timeline and every recorder, keeping programmed answers.

        :returns: ``None``.
        """
        self.driver.clear_calls()
        self.sleeps.clear()
        self.waits.clear()
        self.chains.clear()
        self.page_accesses.clear()
        self.binder_calls.clear()

    # -- reading the result ------------------------------------------------ #

    @property
    def timeline(self) -> tuple[Call, ...]:
        """The one interleaved log: driver operations plus every seam marker.

        :returns: The entries in the order they happened.
        """
        return tuple(self.driver.calls)

    @property
    def driver_timeline(self) -> tuple[Call, ...]:
        """The timeline with the seam markers removed.

        :returns: Only the operations a real browser would have seen.
        """
        return tuple(
            entry
            for entry in self.driver.calls
            if not entry[0].startswith("test.")
        )

    @property
    def chain_log(self) -> tuple[tuple[str, tuple[Any, ...]], ...]:
        """The single action builder's operations, in order.

        :returns: ``(name, args)`` per operation.
        :raises AssertionError: If no builder, or more than one, was constructed
            - either of which is a parity break on its own.
        """
        assert len(self.chains) == 1, (
            f"expected exactly one action builder, saw {len(self.chains)}: "
            f"{self.chains!r}"
        )
        return tuple(self.chains[0].log)


@pytest.fixture
def notes_probe(
    resolve_step: Callable[[str], Any],
    fake_context: Any,
    stub_driver: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> NotesStepProbe:
    """A :class:`NotesStepProbe` with all four seams patched for one test.

    No teardown of its own: every patch is made through *monkeypatch*, which
    restores the step module's namespace when the test ends, so a seam can never
    outlive the test that installed it - which matters here because the step
    module is loaded once per session and shared with every other step test.

    :param resolve_step: Registry-backed phrase resolution, which also proves
        each phrase resolves to exactly one definition - it raises otherwise.
    :param fake_context: behave's ``Context`` stand-in, carrying the recorder.
    :param stub_driver: The ordered recorder every assertion reads.
    :param monkeypatch: Restores every patched seam when the test ends.
    :returns: The probe.
    """
    return NotesStepProbe(resolve_step, fake_context, stub_driver, monkeypatch)


# =========================================================================== #
# Section 5 - the census, fail-closed in three directions
# =========================================================================== #


def test_census_enumerates_exactly_the_eleven_java_methods() -> None:
    """The transcribed census matches ``Notes.java``'s method count and shape.

    Pins ``Notes.java:21-88``: eleven annotated methods, every phrase distinct,
    every function name distinct, every claimed test distinct, and the lines
    strictly ascending so the census is in source order.  A twelfth row, or a
    duplicated one, fails here before any behavioural test runs.
    """
    assert len(CENSUS) == EXPECTED_DEFINITION_COUNT

    phrases = [definition.phrase for definition in CENSUS]
    functions = [definition.function for definition in CENSUS]
    tests = [definition.test for definition in CENSUS]
    lines = [definition.java_line for definition in CENSUS]

    assert len(set(phrases)) == EXPECTED_DEFINITION_COUNT
    assert len(set(functions)) == EXPECTED_DEFINITION_COUNT
    assert len(set(tests)) == EXPECTED_DEFINITION_COUNT
    assert lines == sorted(lines)
    assert lines == [21, 27, 33, 39, 44, 49, 56, 61, 69, 76, 82]


def test_every_census_phrase_is_claimed_by_a_test_in_this_module() -> None:
    """Each Java method names a parity test that exists here and is a test.

    This is the fail-closed half of the coverage obligation in AAP 0.4.1: *"a
    step method with no corresponding assertion in its module's test is a gap"*.
    The claim is checked against this module's own namespace, so a census row
    whose test was never written - or was renamed - fails immediately.  It is
    order-independent by construction: nothing populates a shared set, and this
    test passes or fails identically when run alone.
    """
    module_namespace = globals()

    for definition in CENSUS:
        claimed = module_namespace.get(definition.test)

        assert callable(claimed), (
            f"{definition.phrase!r} (Notes.java:{definition.java_line}) claims "
            f"the parity test {definition.test!r}, which does not exist in "
            f"this module"
        )
        assert definition.test.startswith("test_"), (
            f"{definition.test!r} is not a collectable test name"
        )


def test_step_module_declares_exactly_the_census_definitions() -> None:
    """The ``@step`` functions in the step module equal the census, in order.

    Parsed out of ``features/steps/notes_steps.py`` rather than imported, and
    compared both ways: an extra definition, a missing one, a renamed function
    and a reordered file all fail.  Source order is asserted too, because the
    port keeps ``Notes.java``'s method order and a reader comparing the two
    files side by side depends on it.
    """
    declared = step_function_nodes()

    assert tuple(declared) == tuple(definition.phrase for definition in CENSUS)
    assert tuple(node.name for node in declared.values()) == tuple(
        definition.function for definition in CENSUS
    )


def test_every_census_phrase_resolves_to_one_definition_in_notes_steps(
    resolve_step: Callable[[str], Any],
) -> None:
    """Every phrase resolves once, to the named function, registered with @step.

    ``resolve_step`` raises when a phrase matches nothing or matches more than
    one definition, so calling it is itself the uniqueness assertion - the
    ambiguity risk AAP deviation 7 accepts in exchange for keyword-agnostic
    matching.  The pattern is compared with the phrase as well, which is what
    holds the registered text to ``Notes.java``'s annotations.
    """
    for definition in CENSUS:
        match = resolve_step(definition.phrase)

        assert match.func.__name__ == definition.function
        assert match.module_name == STEP_MODULE_NAME
        assert match.bucket == "step"
        assert match.pattern == definition.phrase
        assert match.args == ()
        assert match.kwargs == {}


def test_all_definitions_share_one_step_module_namespace(
    resolve_step: Callable[[str], Any],
) -> None:
    """All eleven definitions come from the one module this module parses.

    Two facts in one: the registry's functions share a single ``__globals__``,
    so patching a seam there reaches every body; and that namespace's
    ``__file__`` is the file :func:`step_module_tree` parses, which is what
    makes the AST evidence evidence about the code that just ran.
    """
    namespaces = {
        id(resolve_step(definition.phrase).func.__globals__) for definition in CENSUS
    }

    assert len(namespaces) == 1

    namespace = resolve_step(CLICK_NOTES_MODULE).func.__globals__

    assert Path(namespace["__file__"]).resolve() == STEP_MODULE_PATH.resolve()


def test_page_binders_bind_the_declared_page_classes_to_the_session(
    resolve_step: Callable[[str], Any],
    fake_context: Any,
    stub_driver: Any,
) -> None:
    """``_notes`` and ``_inventory`` port ``Notes.java:16-17``'s two fields.

    The link the identity spies rest on: the recorded class name means
    "the Notes page" or "the Inventory page" only because these two binders
    really return those classes, bound to this scenario's session rather than to
    a module-level driver.  ``InventoryP inventoryP`` at ``Notes.java:16`` and
    ``NotesP notesP`` at ``:17`` are the fields being reproduced.
    """
    namespace = resolve_step(CLICK_NOTES_MODULE).func.__globals__

    notes_page = namespace[NOTES_BINDER](fake_context)
    inventory_page = namespace[INVENTORY_BINDER](fake_context)

    assert isinstance(notes_page, NotesPage)
    assert isinstance(inventory_page, InventoryPage)
    assert notes_page.driver is stub_driver
    assert inventory_page.driver is stub_driver

    # Construction touches nothing: the Java constructor's
    # ``PageFactory.initElements`` resolved no element either, and the port's
    # accessors resolve lazily per access (AAP goal G5).
    assert stub_driver.calls == []


# =========================================================================== #
# Section 6 - the transcribed locators the expected sequences are built on
# =========================================================================== #


@pytest.mark.parametrize(
    ("constant_name", "expected"),
    sorted(JAVA_NOTES_SELECTORS.items()),
)
def test_notes_page_locators_match_the_transcribed_java_selectors(
    constant_name: str, expected: tuple[str, str]
) -> None:
    """Each ``NotesPage`` constant equals its ``@FindBy`` in ``NotesP.java``.

    The foundation of every sequence assertion in this module: the expected
    timelines name locators through ``NotesPage``, so those constants have to be
    held to the Java independently or the comparisons would only prove the port
    agrees with itself.  Both quirks the source carries are covered by the
    transcription - the spaces around ``=`` in ``TABINDEX`` (``NotesP.java:14``)
    against their absence in ``CREATED_MESSAGE`` (``:29``), and the hyphenated
    ``o-kanban-button-new`` (``:20``) against the underscored
    ``o_form_button_save`` (``:32``).
    """
    assert getattr(NotesPage, constant_name) == expected


def test_notes_and_inventory_save_buttons_declare_the_same_xpath() -> None:
    """The two save buttons are one selector, which is why the log can't tell.

    ``NotesP.java:32-33`` and ``InventoryP.java:23-24`` declare the identical
    XPath.  Stated here as a fact of the source so the rest of this module's
    approach is self-explaining: a ``find_element`` entry for this selector is
    ambiguous evidence, and the page-identity spies and the AST receiver
    tracing exist precisely because of this line.
    """
    assert NotesPage.SAVE_BTN == ("xpath", SHARED_SAVE_BUTTON_XPATH)
    assert InventoryPage.SAVE_BTN == JAVA_INVENTORY_SAVE_BTN
    assert NotesPage.SAVE_BTN == InventoryPage.SAVE_BTN


def test_table_locators_carry_the_source_record_ids_and_child_indexes() -> None:
    """The drag step's two locators keep ids 1193 and 1194 and their indexes.

    ``NotesP.java:38-39`` and ``:41-42`` hard-code two different Odoo record
    ids **and** two different child indexes - ``[2]`` for the New column and
    ``[1]`` for Today.  The asymmetry is the source's and AAP 0.8 keeps it, so
    both halves are pinned: a "tidied" pair of locators would break this.
    """
    assert NEW_TABLE == ("xpath", f"(//div[@data-id='{NEW_TABLE_RECORD_ID}']/div)[2]")
    assert TODAY_TABLE == ("xpath", f"(//div[@data-id='{TODAY_TABLE_RECORD_ID}']/div)[1]")
    assert NEW_TABLE_RECORD_ID != TODAY_TABLE_RECORD_ID


# =========================================================================== #
# Section 7 - the eleven definitions, in Java order
#
# Each test states the same two things in the same order: the interleaved
# timeline the body produced, and the ordered page-object receivers the tree
# reports.  The first would catch a changed operation, locator, literal or
# timeout; the second catches a changed *name* whose behaviour is identical -
# which is the only way the two save buttons differ.
# =========================================================================== #


def test_definition_01_sleeps_two_seconds_then_clicks_the_notes_module(
    notes_probe: NotesStepProbe,
) -> None:
    """``Notes.java:21-25`` - a 2-second delay first, then the module click.

    ``:23`` ``Thread.sleep(2000)`` is the **first statement**, before ``:24``
    ``notesP.notesModule.click()``.  Position is asserted two ways: the delay is
    the first entry of the interleaved timeline, ahead of the element lookup, and
    the body's first statement is structurally a call to the delay seam.  A
    delay moved after the click, converted into an explicit wait or dropped
    entirely fails here.
    """
    notes_probe.run(CLICK_NOTES_MODULE)

    assert notes_probe.timeline == (
        (SLEEP_MARKER, (FIXED_SLEEP_SECONDS,)),
        ("find_element", NOTES_MODULE),
        ("element.click", (NOTES_MODULE,)),
    )
    assert notes_probe.sleeps == [FIXED_SLEEP_SECONDS]
    assert notes_probe.waits == []
    assert page_operations(CLICK_NOTES_MODULE) == (
        PageOperation(NOTES_BINDER, "notes_module", "click"),
    )

    # Structural confirmation of "first in the body": the first executable
    # statement of the function is the delay call, with the literal 2 and
    # nothing else.
    first_statement = executable_statements(CLICK_NOTES_MODULE)[0]

    assert isinstance(first_statement, ast.Expr)
    assert isinstance(first_statement.value, ast.Call)
    assert isinstance(first_statement.value.func, ast.Name)
    assert first_statement.value.func.id == SLEEP_SEAM
    assert [argument.value for argument in first_statement.value.args] == [
        FIXED_SLEEP_SECONDS
    ]


def test_definition_02_waits_on_then_clicks_the_kanban_create_button(
    notes_probe: NotesStepProbe,
) -> None:
    """``Notes.java:27-31`` - wait 20s on ``creatingNotes``, then click it.

    ``:29`` waits on ``visibilityOf(notesP.creatingNotes)`` through the class's
    single 20-second wait field (``:19``) and ``:30`` clicks the same element.
    The wait is handed ``CREATING_NOTES`` as a **locator** and resolves it
    inside its own predicate, once per poll and under this class's 20 seconds -
    the un-cached ``PageFactory`` proxy semantics of the Java field, which an
    element resolved once beforehand would have moved under the session's
    10-second implicit wait instead.  So it contributes no ``find_element`` of
    its own, and its marker is what fixes its position: the wait comes
    **before** the click, and reversing the two statements moves the marker
    past it and fails the comparison below.
    """
    notes_probe.run(CLICK_CREATE_BUTTON)

    assert notes_probe.timeline == (
        (WAIT_MARKER, (CREATING_NOTES, WAIT_TIMEOUT_SECONDS)),
        ("find_element", CREATING_NOTES),
        ("element.click", (CREATING_NOTES,)),
    )
    assert [(wait.target, wait.timeout) for wait in notes_probe.waits] == [
        (CREATING_NOTES, WAIT_TIMEOUT_SECONDS)
    ]
    assert notes_probe.sleeps == []
    assert page_operations(CLICK_CREATE_BUTTON) == (
        PageOperation(NOTES_BINDER, "CREATING_NOTES", None),
        PageOperation(NOTES_BINDER, "creating_notes", "click"),
    )


def test_definition_03_sends_new_tag_and_enter_in_one_call_then_clicks_tabindex(
    notes_probe: NotesStepProbe,
) -> None:
    """``Notes.java:33-37`` - one ``sendKeys("New Tag", Keys.ENTER)``, then click.

    ``:35`` is a **single** keyboard call carrying the literal followed by the
    Enter key, in that argument order, and ``:36`` clicks the "Create and
    Edit..." anchor.  Exactly one ``send_keys`` entry is therefore the parity
    claim: two separate keyboard calls would show the browser two events where
    the source produced one, so the count is asserted as well as the arguments.
    """
    notes_probe.run(ENTER_TAG_NAME)

    assert notes_probe.timeline == (
        ("find_element", TAGS_N),
        ("element.send_keys", (TAGS_N, TAG_NAME_LITERAL, Keys.ENTER)),
        ("find_element", TABINDEX),
        ("element.click", (TABINDEX,)),
    )
    assert notes_probe.driver.count_of("element.send_keys") == 1
    assert notes_probe.sleeps == []
    assert notes_probe.waits == []
    assert page_operations(ENTER_TAG_NAME) == (
        PageOperation(NOTES_BINDER, "tags_n", None),
        PageOperation(NOTES_BINDER, "tabindex", "click"),
    )


def test_definition_04_types_the_description_then_clicks_the_notes_save_button(
    notes_probe: NotesStepProbe,
) -> None:
    """``Notes.java:39-43`` - type the note body, then click the **Notes** save.

    ``:41`` sends the literal to ``notesP.description`` as a plain send with no
    key component, and ``:42`` clicks ``notesP.saveBtn`` - the Notes page's
    button, not the Inventory page's, which only ``:65`` reaches.  Because both
    pages declare the same selector, the page identity is asserted through the
    spy's recorded class and the tree's receiver, either of which would fail if
    this site were swapped with ``:65``'s.
    """
    notes_probe.run(ENTER_DESCRIPTION, spy_pages=True)

    assert notes_probe.timeline == (
        ("find_element", DESCRIPTION),
        ("element.send_keys", (DESCRIPTION, DESCRIPTION_LITERAL)),
        ("find_element", SAVE_BTN),
        ("element.click", (SAVE_BTN,)),
    )
    assert notes_probe.binder_calls == [NOTES_BINDER]
    assert notes_probe.page_accesses == [
        PageAccess("NotesPage", "description"),
        PageAccess("NotesPage", "save_btn"),
    ]
    assert page_operations(ENTER_DESCRIPTION) == (
        PageOperation(NOTES_BINDER, "description", "send_keys"),
        PageOperation(NOTES_BINDER, "save_btn", "click"),
    )
    assert notes_probe.waits == []
    assert notes_probe.sleeps == []


def test_definition_05_waits_on_and_clicks_the_notes_save_button(
    notes_probe: NotesStepProbe,
) -> None:
    """``Notes.java:44-48`` - wait 20s on the **Notes** save button, then click.

    Both statements address ``notesP.saveBtn`` (``:46``, ``:47``), so both the
    wait target and the click have to be the Notes page's button; the Inventory
    page is never involved in this method even though its selector would satisfy
    the driver log.  Invoked twice by the feature (``Notes.feature:15`` and
    ``:22``).
    """
    notes_probe.run(CLICK_SAVE_BUTTON, spy_pages=True)

    assert notes_probe.timeline == (
        (WAIT_MARKER, (SAVE_BTN, WAIT_TIMEOUT_SECONDS)),
        ("find_element", SAVE_BTN),
        ("element.click", (SAVE_BTN,)),
    )
    assert [(wait.target, wait.timeout) for wait in notes_probe.waits] == [
        (SAVE_BTN, WAIT_TIMEOUT_SECONDS)
    ]
    assert notes_probe.binder_calls == [NOTES_BINDER]

    # Both reads are off the **Notes** page: the first is the locator constant
    # the wait of ``:46`` is given, the second the accessor the click of
    # ``:47`` resolves.  Reading either off the Inventory page - whose
    # ``SAVE_BTN`` is the same XPath - changes this list and nothing else.
    assert notes_probe.page_accesses == [
        PageAccess("NotesPage", "SAVE_BTN"),
        PageAccess("NotesPage", "save_btn"),
    ]
    assert page_operations(CLICK_SAVE_BUTTON) == (
        PageOperation(NOTES_BINDER, "SAVE_BTN", None),
        PageOperation(NOTES_BINDER, "save_btn", "click"),
    )
    assert notes_probe.sleeps == []


def test_definition_06_compares_the_created_message_with_note_created(
    notes_probe: NotesStepProbe,
) -> None:
    """``Notes.java:49-54`` - read ``createdMessage``, compare with "Note created".

    One text read and a bare two-argument ``assertEquals`` with the page value
    first (``:53``).  The passing branch is asserted here; the failing branch and
    the operand order are asserted by
    :func:`test_assertion_branches_fail_on_the_java_comparison` and
    :func:`test_assertions_are_bare_comparisons_in_the_java_operand_order`.
    """
    notes_probe.driver.set_text(CREATED_MESSAGE, CREATED_MESSAGE_LITERAL)

    notes_probe.run(SEE_CREATED_NOTES)

    assert notes_probe.timeline == (
        ("find_element", CREATED_MESSAGE),
        ("element.text", (CREATED_MESSAGE,)),
    )
    assert notes_probe.sleeps == []
    assert notes_probe.waits == []
    assert page_operations(SEE_CREATED_NOTES) == (
        PageOperation(NOTES_BINDER, "created_message", "text"),
    )


def test_definition_07_only_clicks_the_existing_note_card(
    notes_probe: NotesStepProbe,
) -> None:
    """``Notes.java:56-59`` - one statement: click ``appK``, and nothing else.

    No wait, though the class has one available: the source waits at only three
    sites (``:29``, ``:46``, ``:71``) and adding a fourth here would change the
    step's timing behaviour.  The empty wait and delay lists are therefore parity
    claims in their own right, not incidental.
    """
    notes_probe.run(CLICK_EDIT_BUTTON)

    assert notes_probe.timeline == (
        ("find_element", APP_K),
        ("element.click", (APP_K,)),
    )
    assert notes_probe.waits == []
    assert notes_probe.sleeps == []
    assert page_operations(CLICK_EDIT_BUTTON) == (
        PageOperation(NOTES_BINDER, "app_k", "click"),
    )


def test_definition_08_clears_retypes_saves_via_inventory_then_reopens_notes(
    notes_probe: NotesStepProbe,
) -> None:
    """``Notes.java:61-67`` - the cross-page save, preserved exactly.

    Four statements: ``:63`` ``description.clear()``, ``:64`` the plain send of
    the shouted literal, ``:65`` **``inventoryP.saveBtn.click()``** and ``:66``
    ``notesP.notesModule.click()``.  The third is the only place in this class
    that touches the Inventory page - a copy-paste slip in the source that AAP
    0.2.2 preserves - and it is invisible to the driver log, so the spy's
    ``InventoryPage`` reading and the tree's ``_inventory`` receiver are what
    hold it.  Swapping this click to the Notes page would leave the timeline
    byte-identical and fail both of those assertions.
    """
    notes_probe.run(ENTER_NEW_DESCRIPTION, spy_pages=True)

    assert notes_probe.timeline == (
        ("find_element", DESCRIPTION),
        ("element.clear", (DESCRIPTION,)),
        ("find_element", DESCRIPTION),
        ("element.send_keys", (DESCRIPTION, NEW_DESCRIPTION_LITERAL)),
        ("find_element", INVENTORY_SAVE_BTN),
        ("element.click", (INVENTORY_SAVE_BTN,)),
        ("find_element", NOTES_MODULE),
        ("element.click", (NOTES_MODULE,)),
    )

    # Both fields are bound, in the Java declaration order of ``:16`` inventory
    # and ``:17`` notes; binding performs no browser work, so the observable
    # sequence still starts with the clear above.
    assert notes_probe.binder_calls == [INVENTORY_BINDER, NOTES_BINDER]
    assert notes_probe.page_accesses == [
        PageAccess("NotesPage", "description"),
        PageAccess("NotesPage", "description"),
        PageAccess("InventoryPage", "save_btn"),
        PageAccess("NotesPage", "notes_module"),
    ]
    assert page_operations(ENTER_NEW_DESCRIPTION) == (
        PageOperation(NOTES_BINDER, "description", "clear"),
        PageOperation(NOTES_BINDER, "description", "send_keys"),
        PageOperation(INVENTORY_BINDER, "save_btn", "click"),
        PageOperation(NOTES_BINDER, "notes_module", "click"),
    )
    assert notes_probe.waits == []
    assert notes_probe.sleeps == []


def test_definition_09_waits_clicks_then_asserts_the_notes_menu_is_displayed(
    notes_probe: NotesStepProbe,
) -> None:
    """``Notes.java:69-74`` - wait 20s, click, then ``assertTrue(isDisplayed())``.

    All three statements address the same element, ``notesP.notesModule``
    (``:71``, ``:72``, ``:73``).  Waiting for an element, clicking it and then
    asserting it is displayed is redundant on its face, and it is what the source
    does, so it is preserved unchanged (AAP 0.8) - including the third lookup the
    assertion's own read performs.
    """
    notes_probe.driver.set_displayed(NOTES_MODULE, True)

    notes_probe.run(SEE_NOTES_LIST)

    assert notes_probe.timeline == (
        (WAIT_MARKER, (NOTES_MODULE, WAIT_TIMEOUT_SECONDS)),
        ("find_element", NOTES_MODULE),
        ("element.click", (NOTES_MODULE,)),
        ("find_element", NOTES_MODULE),
        ("element.is_displayed", (NOTES_MODULE,)),
    )
    assert [(wait.target, wait.timeout) for wait in notes_probe.waits] == [
        (NOTES_MODULE, WAIT_TIMEOUT_SECONDS)
    ]
    assert notes_probe.sleeps == []
    assert page_operations(SEE_NOTES_LIST) == (
        PageOperation(NOTES_BINDER, "NOTES_MODULE", None),
        PageOperation(NOTES_BINDER, "notes_module", "click"),
        PageOperation(NOTES_BINDER, "notes_module", "is_displayed"),
    )


def test_definition_10_drags_from_new_to_today_with_two_pauses_and_no_sleep(
    notes_probe: NotesStepProbe,
) -> None:
    """``Notes.java:76-80`` - the one chained drag, and no trailing delay.

    ``:78`` builds a fresh ``Actions`` and ``:79`` performs one expression:
    grab ``newTable``, pause, move onto ``todayTable``, pause, release, perform.
    Three things are asserted that a driver log alone could not state:

    * **direction** - ``click_and_hold`` on the New column (record id 1193) and
      ``move_to_element`` on Today (1194), never the reverse;
    * **the two pauses are chain pauses of 2**, sitting between the grab and the
      move and between the move and the release.  The Java builder takes
      milliseconds and the Python one takes seconds, so ``pause(2000)`` is
      ``pause(2)`` here; transcribing 2000 would suspend the scenario for over
      half an hour at each pause;
    * **no fixed delay at all** - unlike ``Crm.java:117``, whose otherwise
      identical drag step is followed by a ``Thread.sleep``.  The absence is the
      parity claim, so it is asserted on the recorder, on the timeline and in
      the tree.
    """
    notes_probe.run(MOVE_NEW_TO_TODAY)

    assert notes_probe.timeline == (
        (CHAIN_BUILT_MARKER, (1,)),
        ("find_element", NEW_TABLE),
        (f"{CHAIN_OPERATION_PREFIX}click_and_hold", (NEW_TABLE,)),
        (f"{CHAIN_OPERATION_PREFIX}pause", (CHAIN_PAUSE_SECONDS,)),
        ("find_element", TODAY_TABLE),
        (f"{CHAIN_OPERATION_PREFIX}move_to_element", (TODAY_TABLE,)),
        (f"{CHAIN_OPERATION_PREFIX}pause", (CHAIN_PAUSE_SECONDS,)),
        (f"{CHAIN_OPERATION_PREFIX}release", ()),
        (f"{CHAIN_OPERATION_PREFIX}perform", ()),
    )
    assert notes_probe.chain_log == (
        ("click_and_hold", (NEW_TABLE,)),
        ("pause", (CHAIN_PAUSE_SECONDS,)),
        ("move_to_element", (TODAY_TABLE,)),
        ("pause", (CHAIN_PAUSE_SECONDS,)),
        ("release", ()),
        ("perform", ()),
    )

    # The absence of a fixed delay, from three directions.
    assert notes_probe.sleeps == []
    assert not any(entry[0] == SLEEP_MARKER for entry in notes_probe.timeline)
    assert SLEEP_SEAM not in direct_call_names(MOVE_NEW_TO_TODAY)

    assert notes_probe.waits == []
    assert page_operations(MOVE_NEW_TO_TODAY) == (
        PageOperation(NOTES_BINDER, "new_table", None),
        PageOperation(NOTES_BINDER, "today_table", None),
    )


def test_definition_11_compares_the_today_column_text_with_today(
    notes_probe: NotesStepProbe,
) -> None:
    """``Notes.java:82-88`` - read ``todayTable``'s text, compare with "Today".

    One read and a bare two-argument ``assertEquals`` with the page value first
    (``:87``).  The element is the same one the drag step dropped onto, and the
    assertion checks the column's label rather than the card's presence - what
    the source asserts, not strengthened here.
    """
    notes_probe.driver.set_text(TODAY_TABLE, TODAY_TABLE_LITERAL)

    notes_probe.run(SEE_TODAY_ELEMENT)

    assert notes_probe.timeline == (
        ("find_element", TODAY_TABLE),
        ("element.text", (TODAY_TABLE,)),
    )
    assert notes_probe.sleeps == []
    assert notes_probe.waits == []
    assert page_operations(SEE_TODAY_ELEMENT) == (
        PageOperation(NOTES_BINDER, "today_table", "text"),
    )


# =========================================================================== #
# Section 8 - the class-level totals: waits, the one delay, the two pauses
# =========================================================================== #


def test_the_three_wait_sites_are_the_java_ones_at_timeout_twenty(
    notes_probe: NotesStepProbe,
) -> None:
    """Walking all eleven bodies produces exactly three 20-second waits.

    ``Notes.java:19`` declares one ``WebDriverWait`` of 20 seconds and the class
    uses it at exactly three sites - ``:29`` on ``creatingNotes``, ``:46`` on
    ``saveBtn`` and ``:71`` on ``notesModule``.  A fourth wait anywhere, a
    missing one, or a retimed one fails here even if every individual
    definition's own sequence were somehow still satisfied.
    """
    notes_probe.driver.set_text(CREATED_MESSAGE, CREATED_MESSAGE_LITERAL)
    notes_probe.driver.set_text(TODAY_TABLE, TODAY_TABLE_LITERAL)

    notes_probe.run_all([definition.phrase for definition in CENSUS])

    assert len(notes_probe.waits) == EXPECTED_WAIT_SITE_COUNT
    assert [wait.target for wait in notes_probe.waits] == [
        CREATING_NOTES,
        SAVE_BTN,
        NOTES_MODULE,
    ]
    assert {wait.timeout for wait in notes_probe.waits} == {WAIT_TIMEOUT_SECONDS}

    # The structural counterpart: three call sites in the module's source, each
    # spelling the literal 20 rather than reading a shared constant.
    wait_timeouts = [
        node.args[1].value
        for node in ast.walk(step_module_tree())
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == WAIT_SEAM
        and len(node.args) == 2
        and isinstance(node.args[1], ast.Constant)
    ]

    assert wait_timeouts == [WAIT_TIMEOUT_SECONDS] * EXPECTED_WAIT_SITE_COUNT


def test_the_module_holds_one_fixed_delay_and_it_belongs_to_definition_one(
    notes_probe: NotesStepProbe,
) -> None:
    """Exactly one fixed delay exists, of 2 seconds, in the first definition.

    ``Notes.java:23`` is the class's only ``Thread.sleep`` - AAP 0.4.1 accounts
    for the suite's seventeen delays individually and this class owns one of
    them.  Walking every body once shows a single delay; the tree shows a single
    call site, in ``user_clicks_the_notes_module`` and nowhere else, which is
    what keeps the two chain pauses of ``:79`` from being miscounted as delays.
    """
    notes_probe.driver.set_text(CREATED_MESSAGE, CREATED_MESSAGE_LITERAL)
    notes_probe.driver.set_text(TODAY_TABLE, TODAY_TABLE_LITERAL)

    notes_probe.run_all([definition.phrase for definition in CENSUS])

    assert notes_probe.sleeps == [FIXED_SLEEP_SECONDS]
    assert (
        sum(1 for entry in notes_probe.timeline if entry[0] == SLEEP_MARKER)
        == EXPECTED_FIXED_SLEEP_COUNT
    )

    definitions_with_a_delay = [
        definition.phrase
        for definition in CENSUS
        if SLEEP_SEAM in direct_call_names(definition.phrase)
    ]

    assert definitions_with_a_delay == [CLICK_NOTES_MODULE]


def test_the_two_two_second_values_of_the_drag_step_are_chain_pauses(
    notes_probe: NotesStepProbe,
) -> None:
    """``Notes.java:79``'s two 2s values are pauses inside the action sequence.

    The distinction this module is obliged to draw: a pause is an operation of
    the builder, recorded between the grab and the move and between the move and
    the release, whereas the class's one fixed delay is a blocking call in a
    body (``:23``).  So the two values are asserted *through the chain* and the
    delay recorder is asserted empty for the same run.
    """
    notes_probe.run(MOVE_NEW_TO_TODAY)

    pauses = [args for name, args in notes_probe.chain_log if name == "pause"]

    assert pauses == [(CHAIN_PAUSE_SECONDS,)] * EXPECTED_CHAIN_PAUSE_COUNT
    assert [name for name, _args in notes_probe.chain_log].index("pause") == 1
    assert notes_probe.sleeps == []


def test_a_new_action_builder_is_constructed_on_every_invocation(
    notes_probe: NotesStepProbe,
) -> None:
    """``Notes.java:78`` builds the ``Actions`` inside the method, per call.

    Held at module level instead, a builder would be shared by every scenario in
    a worker process and would accumulate the previous drag's queued actions.
    Running the step twice must therefore produce two independent builders, each
    carrying the whole six-operation sequence once.
    """
    notes_probe.run(MOVE_NEW_TO_TODAY)
    notes_probe.run(MOVE_NEW_TO_TODAY)

    assert len(notes_probe.chains) == 2
    assert notes_probe.chains[0] is not notes_probe.chains[1]

    for chain in notes_probe.chains:
        assert [name for name, _args in chain.log] == [
            "click_and_hold",
            "pause",
            "move_to_element",
            "pause",
            "release",
            "perform",
        ]

    assert CHAIN_SEAM in direct_call_names(MOVE_NEW_TO_TODAY)


def test_walking_every_definition_prints_nothing(
    notes_probe: NotesStepProbe,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``Notes.java`` writes nothing to standard output, and neither does the port.

    Unlike ``Crm.java`` (eight ``System.out.println`` calls) and ``Sales.java``
    (six), this class prints nothing at all, so a diagnostic print added to any
    of the eleven bodies would be a behavioural addition rather than a
    convenience.  Asserted over a full walk of the class.
    """
    notes_probe.driver.set_text(CREATED_MESSAGE, CREATED_MESSAGE_LITERAL)
    notes_probe.driver.set_text(TODAY_TABLE, TODAY_TABLE_LITERAL)

    notes_probe.run_all([definition.phrase for definition in CENSUS])
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == ""


# =========================================================================== #
# Section 9 - the three assertions, both ways
# =========================================================================== #

# One row per assertion site: the phrase, the Java line, a callable that
# programmes the page's answer on the stub driver, a value that satisfies the
# assertion and a value that does not.  The programming callable is what lets
# one parametrized test drive two text comparisons and a visibility check.
type AssertionCase = tuple[str, int, Callable[[Any, Any], None], Any, Any]

#: The three assertion sites of ``Notes.java``, in source order.
ASSERTION_CASES: Final[tuple[AssertionCase, ...]] = (
    (
        SEE_CREATED_NOTES,
        53,
        lambda driver, value: driver.set_text(CREATED_MESSAGE, value),
        CREATED_MESSAGE_LITERAL,
        "Note Created",  # one changed letter: the comparison is case-sensitive
    ),
    (
        SEE_NOTES_LIST,
        73,
        lambda driver, value: driver.set_displayed(NOTES_MODULE, value),
        True,
        False,
    ),
    (
        SEE_TODAY_ELEMENT,
        87,
        lambda driver, value: driver.set_text(TODAY_TABLE, value),
        TODAY_TABLE_LITERAL,
        "New",  # the other kanban column's label
    ),
)


def test_the_assertion_cases_cover_every_assertion_in_the_class() -> None:
    """The three parametrized cases are the three ``Assert`` calls of the class.

    ``Notes.java:53``, ``:73`` and ``:87`` - two ``assertEquals`` and one
    ``assertTrue``.  Pinning the count here means a fourth assertion added to
    the port, or one of these three deleted, cannot slip past the branch tests
    below, which only ever exercise what this tuple names.
    """
    assert len(ASSERTION_CASES) == EXPECTED_ASSERTION_COUNT

    asserting_definitions = [
        definition.phrase for definition in CENSUS if assert_nodes(definition.phrase)
    ]

    assert asserting_definitions == [phrase for phrase, *_rest in ASSERTION_CASES]
    assert [len(assert_nodes(phrase)) for phrase, *_rest in ASSERTION_CASES] == [1, 1, 1]


@pytest.mark.parametrize(
    ("phrase", "java_line", "programme", "passing", "failing"),
    ASSERTION_CASES,
    ids=[phrase for phrase, *_rest in ASSERTION_CASES],
)
def test_assertion_branches_pass_and_fail_on_the_java_comparison(
    notes_probe: NotesStepProbe,
    phrase: str,
    java_line: int,
    programme: Callable[[Any, Any], None],
    passing: Any,
    failing: Any,
) -> None:
    """Each assertion is driven both ways, and carries no message when it fails.

    The Java calls are ``Assert.assertEquals(actual, expected)`` at ``:53`` and
    ``:87`` and ``Assert.assertTrue(...)`` at ``:73``, none of them passing a
    message string, so the port's bare ``assert`` must raise an ``AssertionError``
    with **no arguments** - and it does: the step modules are not rewritten by
    pytest, so nothing is synthesised into the failure either.  AAP deviation 16
    covers what legitimately differs: Python cannot reproduce JUnit's
    ``expected:<...> but was:<...>`` rendering, so the assertion's subject is
    parity and its formatting is not.
    """
    programme(notes_probe.driver, passing)
    notes_probe.run(phrase)  # the passing branch raises nothing

    notes_probe.reset()
    programme(notes_probe.driver, failing)

    with pytest.raises(AssertionError) as failure:
        notes_probe.run(phrase)

    assert failure.value.args == (), (
        f"Notes.java:{java_line} attaches no message to its assertion, so the "
        f"port's failure must carry none either"
    )


def test_assertions_are_bare_comparisons_in_the_java_operand_order() -> None:
    """The two ``assertEquals`` sites compare the page read against the literal.

    ``Notes.java:51-53`` reads the element's text into a local, holds the
    expected literal in a second local, and compares **actual first**; ``:84-87``
    does the same for the Today column.  Operand order survives no runtime
    observation - the comparison is symmetric and the failure carries no message
    - so it is asserted structurally: the left operand traces back to a page
    ``.text`` read and the right to the transcribed literal.  ``:73``'s
    ``assertTrue`` is checked as a single-operand truth assertion on
    ``isDisplayed()``, with no message either.
    """
    for phrase, literal, attribute in (
        (SEE_CREATED_NOTES, CREATED_MESSAGE_LITERAL, "created_message"),
        (SEE_TODAY_ELEMENT, TODAY_TABLE_LITERAL, "today_table"),
    ):
        (node,) = assert_nodes(phrase)
        assignments = local_assignments(phrase)

        assert node.msg is None
        assert isinstance(node.test, ast.Compare)
        assert len(node.test.ops) == 1
        assert isinstance(node.test.ops[0], ast.Eq)

        left, right = node.test.left, node.test.comparators[0]

        assert isinstance(left, ast.Name)
        assert isinstance(right, ast.Name)

        # Left operand: the page read, as the Java's first argument is.
        actual_source = assignments[left.id]
        assert isinstance(actual_source, ast.Attribute)
        assert actual_source.attr == "text"
        assert isinstance(actual_source.value, ast.Attribute)
        assert actual_source.value.attr == attribute

        # Right operand: the expected literal, byte-exact.
        expected_source = assignments[right.id]
        assert isinstance(expected_source, ast.Constant)
        assert expected_source.value == literal

    (truth_assertion,) = assert_nodes(SEE_NOTES_LIST)

    assert truth_assertion.msg is None
    assert isinstance(truth_assertion.test, ast.Call)
    assert isinstance(truth_assertion.test.func, ast.Attribute)
    assert truth_assertion.test.func.attr == "is_displayed"
    assert truth_assertion.test.args == []


# =========================================================================== #
# Section 10 - the two save buttons, and the import boundary
# =========================================================================== #

#: The three save-button sites of the class, with the page object the Java names
#: at each one.  ``:42`` and ``:46``/``:47`` are the Notes page's button; ``:65``
#: is the Inventory page's, and the selectors are identical.
SAVE_BUTTON_SITES: Final[tuple[tuple[str, str, str, str], ...]] = (
    (ENTER_DESCRIPTION, "Notes.java:42", NOTES_BINDER, "NotesPage"),
    (CLICK_SAVE_BUTTON, "Notes.java:46-47", NOTES_BINDER, "NotesPage"),
    (ENTER_NEW_DESCRIPTION, "Notes.java:65", INVENTORY_BINDER, "InventoryPage"),
)


@pytest.mark.parametrize(
    ("phrase", "java_site", "expected_binder", "expected_page_class"),
    SAVE_BUTTON_SITES,
    ids=[java_site for _phrase, java_site, *_rest in SAVE_BUTTON_SITES],
)
def test_each_save_button_site_uses_the_page_object_the_java_names(
    notes_probe: NotesStepProbe,
    phrase: str,
    java_site: str,
    expected_binder: str,
    expected_page_class: str,
) -> None:
    """A swap of the Notes and Inventory save buttons fails here.

    The one parity fact in this class that no driver log can carry, asserted
    from both available angles at once for each of the three sites: the spy
    reports the class of the object the production binder returned and the
    attribute read off it, and the tree reports which binder the receiver
    variable came from.  The two are independent - one is runtime, one is
    syntax - and swapping ``Notes.java:42`` with ``:65`` would break both while
    leaving the driver log untouched.
    """
    notes_probe.run(phrase, spy_pages=True)

    # Both spellings count.  ``:47``'s click resolves the element through the
    # lower-case accessor and ``:46``'s wait is handed the upper-case locator
    # constant, and the page each one is read from is the parity fact at both -
    # so a filter on the accessor alone would stop covering the wait site.
    save_button_reads = [
        access
        for access in notes_probe.page_accesses
        if access.attribute.lower() == "save_btn"
    ]

    assert save_button_reads, f"{java_site} reads a save button; none was recorded"
    assert {access.page_class for access in save_button_reads} == {expected_page_class}

    save_button_receivers = {
        operation.binder
        for operation in page_operations(phrase)
        if operation.attribute.lower() == "save_btn"
    }

    assert save_button_receivers == {expected_binder}

    # And the selector really is shared, so neither assertion above could have
    # been satisfied by the locator value.
    assert notes_probe.driver.calls_of("find_element").count(
        ("xpath", SHARED_SAVE_BUTTON_XPATH)
    ) >= 1


def test_only_one_definition_reaches_the_inventory_page() -> None:
    """``_inventory`` is used by ``Notes.java:65``'s body and by no other.

    Notes is the suite's only step module that drives two page objects
    (``Notes.java:16-17``), and the second one is reached from exactly one body.
    A second body reaching it - or the first one losing it - is a change to the
    source's cross-page behaviour, so the set is pinned rather than the count.
    """
    definitions_using_inventory = {
        definition.phrase
        for definition in CENSUS
        if any(
            operation.binder == INVENTORY_BINDER
            for operation in page_operations(definition.phrase)
        )
    }

    assert definitions_using_inventory == {ENTER_NEW_DESCRIPTION}


def test_step_module_imports_both_pages_and_no_selenium() -> None:
    """Both page classes are imported from ``app.pages``, and selenium is not.

    ``Notes.java:6-7`` imports both ``InventoryP`` and ``NotesP``, which is why
    this is the port's only step module importing two page classes - and AAP
    0.4.2 confines selenium to ``app/automation``, so the ``Keys`` and
    ``Actions`` uses of ``Notes.java:9-10`` reach the browser through
    ``press_keys`` and ``action_chain`` instead.  A direct ``Keys`` import here
    would be a boundary break even though it would behave identically.
    """
    entries = import_entries()
    imported_names = {name for _module, name in entries}

    for page_class in ("InventoryPage", "NotesPage"):
        sources = {module for module, name in entries if name == page_class}

        assert sources, f"{page_class} is not imported by {STEP_MODULE_NAME}"
        assert all(
            source is not None and source.startswith("app.pages") for source in sources
        ), f"{page_class} must come from the app.pages package, got {sources!r}"

    for module, name in entries:
        assert module is None or not module.startswith("selenium"), (
            f"{STEP_MODULE_NAME} imports {name!r} from {module!r}; AAP 0.4.2 "
            f"confines selenium to app/automation"
        )
        assert not name.startswith("selenium"), (
            f"{STEP_MODULE_NAME} imports the selenium module itself as {name!r}"
        )

    # The keyboard and action-chain helpers, and the fixed-delay seam, arrive
    # from where the port puts them rather than from the browser binding.
    assert {"press_keys", "action_chain", "wait_visible_element"} <= imported_names
    assert ("time", "sleep") in entries or (None, "time") in entries


# =========================================================================== #
# Section 11 - feature fidelity
# =========================================================================== #


def _feature_lines() -> tuple[str, ...]:
    """``features/Notes.feature``'s lines, newline-stripped.

    :returns: One entry per line, in file order.
    """
    return tuple(FEATURE_PATH.read_text(encoding=SOURCE_ENCODING).splitlines())


def _feature_step_lines() -> tuple[tuple[int, str, str], ...]:
    """Extract the feature's step lines as ``(line, keyword, phrase)``.

    Keyword-prefix extraction rather than a Gherkin parse, deliberately: the
    Background of this feature is free text spanning ``:3-7`` with a step at
    ``:8``, and the point of the comparison is the literal content of the file.

    :returns: The step lines, in file order.
    """
    found: list[tuple[int, str, str]] = []

    for number, line in enumerate(_feature_lines(), start=1):
        stripped = line.strip()

        for keyword in STEP_KEYWORDS:
            if stripped.startswith(keyword):
                found.append((number, keyword.strip(), stripped[len(keyword) :]))
                break

    return tuple(found)


def test_feature_header_keeps_the_source_mis_title() -> None:
    """``Notes.feature:1`` still reads "Feature: Testinium app login feature".

    A feature about creating, editing and dragging notes carries a login title -
    a mistake it shares with ``Contact.feature:1``, and one that makes two
    features collide on their generated JSON ``id``.  AAP 0.2.2 lists correcting
    it as **explicitly out of scope**, so this test fails if someone fixes it.
    """
    lines = _feature_lines()

    assert lines[0] == FEATURE_HEADER
    assert "Notes" not in lines[0]


def test_feature_step_lines_are_the_transcribed_ones() -> None:
    """Every step line of the feature matches the transcription, line for line.

    Sixteen step lines across a Background and three scenarios, at the exact
    line numbers the pinned ``src/main/resources/features/Notes.feature`` has
    them.  Line numbers matter beyond fidelity: the rerun manifest and the
    JSON artifact both record scenario lines, so a step inserted above shifts
    artifacts the port is held to byte-comparable on structure.
    """
    assert _feature_step_lines() == FEATURE_STEPS


def test_every_feature_step_resolves_to_exactly_one_definition(
    resolve_step: Callable[[str], Any],
) -> None:
    """Each of the feature's phrases resolves once, to the module that owns it.

    The Notes phrases resolve into ``notes_steps``; the Background's *"User
    login to test other features"* resolves into ``session_steps``, because
    ``Session.java:12`` declares it and re-declaring it here would make every
    scenario in this feature ambiguous.  ``resolve_step`` raises on both an
    undefined and an ambiguous phrase, so this is the fidelity check in both
    directions.
    """
    census_phrases = {definition.phrase for definition in CENSUS}

    for line, _keyword, phrase in FEATURE_STEPS:
        match = resolve_step(phrase)

        if phrase == BACKGROUND_PHRASE:
            assert match.module_name == SESSION_STEP_MODULE_NAME, (
                f"Notes.feature:{line} is the shared precondition and belongs "
                f"to {SESSION_STEP_MODULE_NAME}"
            )
            assert phrase not in census_phrases
        else:
            assert match.module_name == STEP_MODULE_NAME
            assert phrase in census_phrases


def test_second_scenario_runs_the_new_description_definition_twice(
    notes_probe: NotesStepProbe,
) -> None:
    """``Notes.feature:19-24`` invokes ``Notes.java:61-67`` twice, saving between.

    Walking the scenario as behave would shows what the per-definition tests
    cannot: the edit body runs twice per scenario, so the Inventory page is
    reached twice and the note body is cleared and retyped twice, with the
    Notes-page save of ``:46-47`` in between.  The scenario's single fixed delay
    is paid once, by its opening step, exactly as in the source.
    """
    notes_probe.driver.set_displayed(NOTES_MODULE, True)

    for phrase in SECOND_SCENARIO_PHRASES:
        notes_probe.run(phrase, spy_pages=True)

    assert SECOND_SCENARIO_PHRASES.count(ENTER_NEW_DESCRIPTION) == 2
    assert notes_probe.binder_calls == [
        NOTES_BINDER,  # :19 open the module
        NOTES_BINDER,  # :20 click the card
        INVENTORY_BINDER,  # :21 the first edit, both fields bound
        NOTES_BINDER,
        NOTES_BINDER,  # :22 save, Notes page
        INVENTORY_BINDER,  # :23 the second edit
        NOTES_BINDER,
        NOTES_BINDER,  # :24 the Notes list assertion
    ]
    assert [
        access
        for access in notes_probe.page_accesses
        if access.attribute.lower() == "save_btn"
    ] == [
        PageAccess("InventoryPage", "save_btn"),  # :21 -> Notes.java:65
        PageAccess("NotesPage", "SAVE_BTN"),  # :22 -> Notes.java:46, the wait
        PageAccess("NotesPage", "save_btn"),  # :22 -> Notes.java:47, the click
        PageAccess("InventoryPage", "save_btn"),  # :23 -> Notes.java:65
    ]
    assert notes_probe.driver.count_of("element.clear") == 2
    assert notes_probe.sleeps == [FIXED_SLEEP_SECONDS]
    assert [(wait.target, wait.timeout) for wait in notes_probe.waits] == [
        (SAVE_BTN, WAIT_TIMEOUT_SECONDS),
        (NOTES_MODULE, WAIT_TIMEOUT_SECONDS),
    ]


# =========================================================================== #
# Section 12 - the seam helpers this module relies on
#
# Two helpers exist to survive a change another unit may make to the automation
# layer's call shapes.  They are tested directly, so neither is an unexercised
# branch that could quietly stop recording what this module asserts on.
# =========================================================================== #


def test_sleep_seam_is_patched_in_the_shape_the_step_module_uses(
    notes_probe: NotesStepProbe,
) -> None:
    """The fixed-delay seam is found as a callable in the module namespace.

    ``features/steps/notes_steps.py`` does ``from time import sleep``
    (``Notes.java:23``'s call site), so that is the shape expected today; the
    helper reports which shape it patched, and this test pins it, so a silent
    switch to ``import time`` shows up as a changed seam rather than as a real
    two-second delay inside the unit suite.
    """
    assert notes_probe.sleep_seam_shape == SLEEP_SEAM


def test_sleep_recorder_installs_against_either_import_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """:func:`install_sleep_recorder` handles both delay-import shapes, and neither.

    Exercised against synthetic namespaces rather than the step module, so the
    third branch - no delay seam at all - can be checked without breaking the
    real one.  The ``import time`` shape is patched on the module object, which
    is why it is verified here with a stand-in object and never with the real
    ``time`` module.
    """
    recorded: list[float] = []

    def recorder(seconds: float) -> None:
        recorded.append(seconds)

    from_import: dict[str, Any] = {SLEEP_SEAM: lambda seconds: None}

    assert install_sleep_recorder(monkeypatch, from_import, recorder) == SLEEP_SEAM
    from_import[SLEEP_SEAM](1)

    module_import: dict[str, Any] = {
        TIME_MODULE_SEAM: SimpleNamespace(sleep=lambda seconds: None)
    }

    assert (
        install_sleep_recorder(monkeypatch, module_import, recorder)
        == f"{TIME_MODULE_SEAM}.{SLEEP_SEAM}"
    )
    module_import[TIME_MODULE_SEAM].sleep(2)

    assert recorded == [1, 2]

    with pytest.raises(AssertionError, match="no fixed-delay seam found"):
        install_sleep_recorder(monkeypatch, {}, recorder)


@pytest.mark.parametrize(
    ("args", "kwargs"),
    [
        ((NotesPage.SAVE_BTN, WAIT_TIMEOUT_SECONDS), {}),
        ((NotesPage.SAVE_BTN,), {"timeout": WAIT_TIMEOUT_SECONDS}),
        ((), {"element": NotesPage.SAVE_BTN, "timeout": WAIT_TIMEOUT_SECONDS}),
        ((), {"locator": NotesPage.SAVE_BTN, "seconds": WAIT_TIMEOUT_SECONDS}),
    ],
    ids=["positional", "keyword-timeout", "keyword-element", "keyword-aliases"],
)
def test_wait_interpretation_recovers_target_and_timeout_from_any_shape(
    args: tuple[Any, ...], kwargs: Mapping[str, Any]
) -> None:
    """The wait recorder reads the timeout whatever shape the helper is called in.

    ``app/automation/waits.py`` belongs to another unit and its signature may
    change; the parity claims are ``Notes.java:19``'s literal 20 and the
    identity of the element waited on, so both are recovered from positional and
    keyword forms alike.  That tolerance is load-bearing, so it is tested
    rather than assumed.
    """
    call = interpret_wait_call(args, kwargs)

    assert call.target == NotesPage.SAVE_BTN
    assert call.timeout == WAIT_TIMEOUT_SECONDS


def test_wait_interpretation_rejects_a_call_it_cannot_read() -> None:
    """An unreadable wait call fails loudly instead of recording nothing.

    Without this, a future change to the helper's signature would leave the
    recorder silently logging ``None`` for the timeout and this module would
    keep passing while asserting nothing about ``Notes.java:19``.
    """
    with pytest.raises(AssertionError, match="recognisable target"):
        interpret_wait_call((), {})

    with pytest.raises(AssertionError, match="recognisable timeout"):
        interpret_wait_call((NotesPage.SAVE_BTN,), {})


def test_target_normalisation_reduces_an_element_to_its_locator(
    stub_driver: Any,
) -> None:
    """:func:`normalise_target` turns a resolved element back into its locator.

    Both the wait recorder and the action-chain recorder compare against locator
    tuples, and what a step body hands them is an element the page object just
    resolved.  This is the one-line bridge between the two, checked on a real
    stub element and on a pair that is already a locator.
    """
    element = stub_driver.find_element(*NotesPage.NEW_TABLE)

    assert normalise_target(element) == NotesPage.NEW_TABLE
    assert normalise_target(NotesPage.NEW_TABLE) == NotesPage.NEW_TABLE
