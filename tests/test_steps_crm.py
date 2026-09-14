"""Per-method Java-parity tests for ``features/steps/crm_steps.py``.

Authority
---------
``Crm.java`` (twelve annotated methods in ``Crm.java:14-154``) and ``CrmP.java``
(twenty-eight ``@FindBy`` fields in ``CrmP.java:13-95``) at the pinned commit
:data:`REFERENCE_COMMIT`, transcribed into module constants that each carry the
Java line they come from: nothing reads the reference checkout at runtime.

The contract this module owns
-----------------------------
AAP 0.4.1's per-module obligation, for the one feature a default run executes:
``Crm.feature:1`` holds the suite's only ``@Smoke`` tag, ``behave.ini``'s
``default_tags``.

* **An exact, fail-closed census.**  :data:`CENSUS` is the transcribed
  twelve-method inventory.  Three tests close it: it must hold exactly twelve
  entries; every entry must be claimed by a parity test that exists in this
  module and that names its Java line; and the ``@step``-decorated functions
  AST-parsed out of the step module must be *exactly* the census's, in the same
  order.  A thirteenth definition, a renamed phrase, a re-keyworded decorator
  or an unported method therefore fails here - it cannot pass silently.  The
  census tests read only transcribed constants and the step module's source, so
  they are order-independent: ``pytest -k`` on any single test still passes.
* **Full ordered operations.**  Each per-definition test asserts the *entire*
  ``stub_driver.calls`` list, never membership, and additionally asserts a
  merged timeline in which the wait, action-chain and fixed-delay seams are
  interleaved with the driver operations at the positions they actually
  occurred.  That is what makes "click, then wait on the element just clicked"
  - the shape ``Crm.java`` uses at :23-24, :29-30, :134-135, :136-137,
  :139-140 and :147-148, and inverts exactly once at :85-86 - checkable.
* **Name-aware locator use.**  ``CrmP.java`` declares four selectors twice
  under different names (:data:`DUPLICATE_SELECTORS`).  The driver log records
  ``(strategy, value)`` and so *cannot* tell ``CREATE_BUTTON`` from
  ``CREATE_CUSTOMER``; a test that only asserted the log would pass if a step
  used the wrong twin.  So the page-attribute *name* at every call site is
  checked against the name its own Java line uses by parsing the step module's
  AST (:data:`SOURCE_SHAPES`), and the four value identities are asserted
  separately as declared facts of the page object.  A wait site names the
  upper-case locator constant and an element operation the lower-case
  accessor, because ``app/automation/waits.py`` takes the locator and resolves
  it inside its own predicate, once per poll, under the call site's own
  timeout; :data:`SOURCE_SHAPES` records whichever spelling the site uses, so
  the eleven waits are held to their named element exactly as the clicks are -
  which matters most at :92 and :150, where the wait's element is *not* the one
  just clicked, and at :135/:137/:140, whose twins are used elsewhere in the
  class.
* **Every keyboard site.**  Seven ``sendKeys`` sites, each of which Java writes
  as one call carrying ``literal + Keys.ENTER``.  The port's ``press_keys``
  makes exactly one ``element.send_keys`` carrying ``(text, Keys.ENTER)``, and
  both the runtime log and the source-level call shape are asserted, including
  the three sites whose text arrives as a step parameter.
* **The drag-and-drop chain.**  ``click_and_hold -> pause(2) ->
  move_to_element -> pause(2) -> release -> perform``, on those two elements in
  that direction, followed by a *separate* ``sleep(2)`` after ``perform``.  The
  two pauses and the delay are recorded through different seams, so the test
  can prove the pauses are chain pauses and the delay is not one, and a second
  run proves a fresh chain is built per call rather than reused.
* **The price computation.**  ``int(TOTAL_PRICE.text) + 8`` against the literal
  ``89`` (``Crm.java:48-49``), with a passing case, a failing case, and the
  non-numeric case, which must raise ``ValueError`` **uncaught** because
  ``Integer.parseInt`` throws and the Java method catches nothing.
* **Silence on stdout, from every definition.**  ``Crm.java:51-52``, ``:63-64``,
  ``:97-98`` and ``:126-127`` print a parsed column total and three live
  pipeline card titles beside their expected literals, and the port reproduces
  none of them: the worker's stdout is relayed into the parent logger and the
  Jenkins console, so those lines were a durable record of live customer and
  pricing data (CWE-532/359, the security review's F04), while diagnostic
  stdout appears in neither AAP 0.1.2's frozen list nor AAP 0.4.1's per-step
  enumeration.  Every run in this module therefore asserts ``run.printed ==
  ()`` - on the passing path, on the failing path and on the raising path - and
  three module-wide tests close it: no definition emits a line when all twelve
  are driven in order, no census function contains a ``print`` call, and the
  step module's syntax tree holds no ``print`` call at all, at any scope.  That
  last one is what a print smuggled back into a helper or into module scope
  fails on.
* **Every assertion, both ways.**  All four are two-argument
  ``Assert.assertEquals``: each is exercised passing and failing, the operand
  order the source uses is asserted at source level, and so is the absence of
  an assertion message.
* **Pattern and feature fidelity.**  ``Crm.java:70``'s phrase carries an
  apostrophe in ``user's`` and a space *before* the comma between its first two
  parameters; the registered pattern is asserted character for character, and
  the concrete step at ``Crm.feature:18`` is resolved with the Examples row at
  ``Crm.feature:24`` (``Test2`` / ``30`` / ``2``) and its bound arguments
  checked.  ``Crm.feature:1`` is ``@Smoke`` - the suite's only occurrence, so
  ``behave.ini``'s ``default_tags`` selects this feature alone and these twelve
  steps are what a bare ``run-tests`` executes - and every step phrase in the
  feature resolves to exactly one definition, the Background belonging to
  ``session_steps`` rather than here.

Through ``tests/conftest.py``'s fixtures, never by importing the step module:
behave's registry has already exec'd it and re-registering risks
``AmbiguousStep``.  The visibility wait, the action-chain factory and the fixed
delay are replaced in ``StepMatch.func.__globals__`` for one run and restored at
teardown - the chain factory necessarily, since a real ``ActionChains`` rejects a
stub element.  ``Keys`` is imported deliberately: AAP 0.4.2 closes the import
boundary for ``features/steps/**``, not for this suite.  Nothing here touches a
network, a browser, a driver, the clock or ``target/``.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from types import MappingProxyType, ModuleType
from typing import Any, Final, NamedTuple

import pytest
from selenium.webdriver.common.keys import Keys

from app.pages import CrmPage

# =========================================================================== #
# Locations and transcribed authority
# =========================================================================== #

#: The reference commit every expectation below was transcribed from.  Quoted
#: in failure-adjacent messages so a reader knows which revision to diff
#: against, and never opened: see the module docstring.
REFERENCE_COMMIT: Final[str] = "47e9d697e4a9a85da889f94a846fdf47af28a240"

#: The two authoritative Java files, by their path in the reference project.
JAVA_STEP_CLASS: Final[str] = "src/main/java/com/testinium/step_definitions/Crm.java"
JAVA_PAGE_CLASS: Final[str] = "src/main/java/com/testinium/pages/CrmP.java"

#: Repository root, from this file's own position: ``tests/`` -> root.  The
#: same derivation ``tests/test_web_routes.py`` uses, and for the same reason -
#: the suite must not depend on the directory pytest was started from.
REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]

#: The step module under test.  Parsed, never imported (module docstring).
STEP_MODULE_PATH: Final[Path] = REPO_ROOT / "features" / "steps" / "crm_steps.py"

#: The feature that exercises these twelve definitions, and the directory that
#: holds all ten feature files - scanned to prove ``@Smoke`` occurs once.
FEATURE_PATH: Final[Path] = REPO_ROOT / "features" / "Crm.feature"
FEATURES_DIR: Final[Path] = REPO_ROOT / "features"

#: The step module's basename without extension, as ``StepMatch.module_name``
#: reports it.  Asserted per definition so a phrase that drifted into another
#: module would fail here rather than resolve happily.
STEP_MODULE_NAME: Final[str] = "crm_steps"

#: Every wait in the class is the one ``WebDriverWait`` built at
#: ``Crm.java:18`` with a 2-second timeout, so all eleven call sites carry this
#: literal.  Classes elsewhere in the suite use 3, 4 and 20 seconds; that this
#: one is 2 is the parity fact.
WAIT_TIMEOUT: Final[int] = 2

#: ``Crm.java:111`` and ``:113`` are ``.pause(2000)``.  Java's
#: ``Actions.pause`` takes **milliseconds** and Python's ``ActionChains.pause``
#: takes **seconds**, so the port writes ``.pause(2)``.  Transcribed verbatim
#: it would be 2000 seconds - over half an hour per pause - and the scenario
#: would hang instead of failing, which is why this conversion is asserted
#: rather than assumed.
CHAIN_PAUSE_SECONDS: Final[int] = 2

#: ``Crm.java:117`` is ``Thread.sleep(2000)``, likewise milliseconds, and is
#: the class's only fixed delay - one of the seventeen AAP 0.4.1 requires be
#: preserved at their call sites rather than converted into explicit waits.
FIXED_SLEEP_SECONDS: Final[int] = 2

#: ``Crm.java:48-49``: the parsed column total plus ``8``, compared against the
#: literal ``89``.  Both numbers are load-bearing.
PRICE_ADDEND: Final[int] = 8
EXPECTED_PRICE: Final[int] = 89

#: The text that makes ``Crm.java:48``'s arithmetic satisfy ``:54``:
#: ``81 + 8 == 89``.
PASSING_TOTAL_TEXT: Final[str] = "81"

#: Literals typed or expected by the class.  ``"test"`` is typed at ``:36`` and
#: expected at ``:61`` and ``:124``; ``"Test2"`` is expected at ``:95`` and is
#: also the Examples value at ``Crm.feature:24``.  The three are deliberately
#: *not* unified - the case difference is the source's.
TITLE_TEXT: Final[str] = "test"
REVENUE_TEXT: Final[str] = "8"
EDITED_TITLE_TEXT: Final[str] = "Test2"
CUSTOMER_NAME_TEXT: Final[str] = "Test"
SEARCH_TEXT: Final[str] = "aa"

#: The single Examples row at ``Crm.feature:22-24``, bound to the three
#: parameters of ``Crm.java:70``'s phrase.
EXAMPLES_HEADER: Final[tuple[str, ...]] = ("opportunity", "revenue", "probability")
EXAMPLES_ROW: Final[MappingProxyType[str, str]] = MappingProxyType(
    {"opportunity": "Test2", "revenue": "30", "probability": "2"}
)

#: The concrete form of ``Crm.feature:18`` once that row is substituted.  Note
#: the apostrophe in ``user's`` and the space **before** the comma: both are
#: the source's and neither may be tidied, since the pattern has to keep
#: matching this exact text.
OUTLINE_STEP_PHRASE: Final[str] = (
    "User can change any user's information like "
    '"Test2" , "30" and "2"'
)

#: ``Crm.feature:1-2``.  Line 1 is the suite's only ``@Smoke`` tag, which is
#: ``behave.ini``'s ``default_tags`` (from ``CukesRunner.java:18``), so a bare
#: run selects this feature alone.
FEATURE_TAG_LINE: Final[str] = "@Smoke"
FEATURE_HEADER_LINE: Final[str] = "Feature: Testinium app CRM Module"

#: ``Crm.feature:7``.  Declared at ``Session.java:12``, nowhere in
#: ``Crm.java:21-145``, and therefore owned by
#: ``features/steps/session_steps.py``.
BACKGROUND_PHRASE: Final[str] = "User login to test other features"
SESSION_MODULE_NAME: Final[str] = "session_steps"


# =========================================================================== #
# The census: the twelve definitions of Crm.java:21-145, in declaration order
# =========================================================================== #


class Definition(NamedTuple):
    """One row of the ``Crm.java:14-154`` method inventory, transcribed.

    Independently enumerated from the Java class rather than derived from the
    port, which is the whole point: if the two disagree, the census tests fail.
    """

    #: 1-based position in ``Crm.java``'s declaration order, which runs from
    #: ``Crm.java:21`` to ``Crm.java:145``.
    index: int

    #: Line of the Cucumber annotation in ``Crm.java``: one of :21, :27, :34,
    #: :46, :58, :70, :83, :89, :106, :121, :132 and :145.
    java_line: int

    #: The annotation itself - ``@When``, ``@And`` or ``@Then``.  Recorded
    #: because seven of these twelve are ``@And``, which behave has no
    #: decorator for, and that is what forces ``@step`` everywhere (AAP
    #: deviation 7).
    java_keyword: str

    #: The Java method name, as declared.
    java_method: str

    #: The annotation's phrase, exactly as ``Crm.java`` writes it on the line
    #: *java_line* names - with ``{string}`` placeholders where Cucumber uses
    #: them, which in this class is ``Crm.java:70`` and no other line.
    java_phrase: str

    #: The pattern the port registers with ``@step``, which differs from
    #: *java_phrase* only in that Cucumber's positional ``{string}`` becomes a
    #: named behave field.
    pattern: str

    #: The concrete phrase this suite resolves to reach the body: the pattern
    #: itself for the eleven parameterless definitions, and the
    #: Examples-substituted text for ``Crm.java:70``.
    concrete_phrase: str

    #: The ported function's name in ``features/steps/crm_steps.py``.
    function: str

    #: The parity test in *this* module that covers this definition.  Checked
    #: to exist, to be a test function, and to name *java_line* in its
    #: docstring - a definition whose test is deleted or renamed fails the
    #: census instead of quietly losing its coverage.
    claimed_by: str


#: The twelve definitions of ``Crm.java:21-145``, transcribed in declaration
#: order.
CENSUS: Final[tuple[Definition, ...]] = (
    Definition(
        index=1,
        java_line=21,
        java_keyword="@When",
        java_method="user_click_on_the_crm_dashboard",
        java_phrase="User click on the crm dashboard",
        pattern="User click on the crm dashboard",
        concrete_phrase="User click on the crm dashboard",
        function="user_click_on_the_crm_dashboard",
        claimed_by="test_crm_dashboard_clicks_the_menu_then_waits_on_it",
    ),
    Definition(
        index=2,
        java_line=27,
        java_keyword="@And",
        java_method="userClickOnThePipelineButton",
        java_phrase="User click on the pipeline button",
        pattern="User click on the pipeline button",
        concrete_phrase="User click on the pipeline button",
        function="user_click_on_the_pipeline_button",
        claimed_by="test_pipeline_button_clicks_create_then_waits_on_it",
    ),
    Definition(
        index=3,
        java_line=34,
        java_keyword="@And",
        java_method="userCanCreateTheNewPipeline",
        java_phrase="User can create the new pipeline",
        pattern="User can create the new pipeline",
        concrete_phrase="User can create the new pipeline",
        function="user_can_create_the_new_pipeline",
        claimed_by="test_create_new_pipeline_fills_the_dialog_in_java_order",
    ),
    Definition(
        index=4,
        java_line=46,
        java_keyword="@And",
        java_method="userCanSeeTheTotalPrice",
        java_phrase="User can see the total price",
        pattern="User can see the total price",
        concrete_phrase="User can see the total price",
        function="user_can_see_the_total_price",
        claimed_by="test_total_price_adds_eight_and_compares_against_eighty_nine",
    ),
    Definition(
        index=5,
        java_line=58,
        java_keyword="@Then",
        java_method="userCanSeeNewPipeline",
        java_phrase="User can see new pipeline",
        pattern="User can see new pipeline",
        concrete_phrase="User can see new pipeline",
        function="user_can_see_new_pipeline",
        claimed_by="test_see_new_pipeline_expects_the_lower_case_title",
    ),
    Definition(
        index=6,
        java_line=70,
        java_keyword="@And",
        java_method="userCanChangeAnyUserSInformationLikeAnd",
        java_phrase=(
            "User can change any user's information like "
            "{string} , {string} and {string}"
        ),
        pattern=(
            "User can change any user's information like "
            '"{opportunity}" , "{revenue}" and "{probability}"'
        ),
        concrete_phrase=OUTLINE_STEP_PHRASE,
        function="user_can_change_any_user_s_information_like_and",
        claimed_by="test_change_information_edits_three_fields_in_java_order",
    ),
    Definition(
        index=7,
        java_line=83,
        java_keyword="@And",
        java_method="userCanSaveInformation",
        java_phrase="User can save information",
        pattern="User can save information",
        concrete_phrase="User can save information",
        function="user_can_save_information",
        claimed_by="test_save_information_waits_before_clicking_save",
    ),
    Definition(
        index=8,
        java_line=89,
        java_keyword="@Then",
        java_method="userCanVerifyTheInformation",
        java_phrase="User can verify the information",
        pattern="User can verify the information",
        concrete_phrase="User can verify the information",
        function="user_can_verify_the_information",
        claimed_by="test_verify_information_waits_on_a_different_element",
    ),
    Definition(
        index=9,
        java_line=106,
        java_keyword="@And",
        java_method="userCanDragAndDropThePipeline",
        java_phrase="User can drag and drop the pipeline",
        pattern="User can drag and drop the pipeline",
        concrete_phrase="User can drag and drop the pipeline",
        function="user_can_drag_and_drop_the_pipeline",
        claimed_by="test_drag_and_drop_performs_the_java_chain_then_sleeps",
    ),
    Definition(
        index=10,
        java_line=121,
        java_keyword="@Then",
        java_method="userCanSeeTheNewChangesInProgress",
        java_phrase="User can see the new changes in progress",
        pattern="User can see the new changes in progress",
        concrete_phrase="User can see the new changes in progress",
        function="user_can_see_the_new_changes_in_progress",
        claimed_by="test_new_changes_in_progress_reads_the_second_column",
    ),
    Definition(
        index=11,
        java_line=132,
        java_keyword="@And",
        java_method="userCanRegisterNewCustomer",
        java_phrase="User can register new customer",
        pattern="User can register new customer",
        concrete_phrase="User can register new customer",
        function="user_can_register_new_customer",
        claimed_by="test_register_new_customer_creates_then_searches",
    ),
    Definition(
        index=12,
        java_line=145,
        java_keyword="@Then",
        java_method="userCanPrintTheProfile",
        java_phrase="User can print the profile",
        pattern="User can print the profile",
        concrete_phrase="User can print the profile",
        function="user_can_print_the_profile",
        claimed_by="test_print_profile_waits_on_the_due_payment_entry",
    ),
)

#: The size of the census, stated once and asserted rather than counted from
#: the tuple at every use site.  ``Crm.java:14-154`` declares twelve annotated
#: methods, at :21, :27, :34, :46, :58, :70, :83, :89, :106, :121, :132 and
#: :145, and nothing else that Cucumber can reach.
DEFINITION_COUNT: Final[int] = 12

#: ``Crm.java:154``, the class's closing brace, so that the last definition
#: (``Crm.java:145``) has an upper bound and an assertion's line can be
#: attributed to the method it sits in.
JAVA_CLASS_LAST_LINE: Final[int] = 154

#: ``Crm.java``'s annotation mix: one ``@When`` (``Crm.java:21``), seven
#: ``@And`` (:27, :34, :46, :70, :83, :106, :132) and four ``@Then`` (:58, :89,
#: :121, :145).  Seven of the suite's nine ``@And`` annotations are in this one
#: class - the other two are ``Calendar.java:160`` and ``Sales.java:80`` - and
#: behave has no ``@and`` decorator, which is why every definition here
#: registers with ``@step``.
KEYWORD_COUNTS: Final[MappingProxyType[str, int]] = MappingProxyType(
    {"@When": 1, "@And": 7, "@Then": 4}
)

#: The four selectors ``CrmP.java`` declares twice under different names, as
#: ``(first name, second name, shared value)``.  Retyped from the ``@FindBy``
#: annotations of ``CrmP.java:13-95`` on purpose: here the duplicated *value* is
#: the assertion, so taking it from the page object would make the test
#: tautological.
DUPLICATE_SELECTORS: Final[tuple[tuple[str, str, str], ...]] = (
    # CrmP.java:16 createButton / CrmP.java:76 createCustomer
    ("CREATE_BUTTON", "CREATE_CUSTOMER", "//button[@accesskey='c']"),
    # CrmP.java:49 opportunityTitleEdit / CrmP.java:79 inputName
    ("OPPORTUNITY_TITLE_EDIT", "INPUT_NAME", "//input[@name='name']"),
    # CrmP.java:34 createPipeline / CrmP.java:82 createCustomerButton
    ("CREATE_PIPELINE", "CREATE_CUSTOMER_BUTTON", "//button[@name='close_dialog']"),
    # CrmP.java:43 buttonPipeline / CrmP.java:64 progressPipeline
    ("BUTTON_PIPELINE", "PROGRESS_PIPELINE", "//div[@data-id='1']/div[2]"),
)

#: The eleven wait sites in Java order, by the element each one waits on:
#: ``Crm.java:24``, :30, :43, :73, :85, :92, :135, :137, :140, :148, :150.
#: Two of them - :92 and :150 - wait on an element *other* than the one just
#: clicked, which is preserved rather than corrected.
WAIT_TARGETS_IN_ORDER: Final[tuple[tuple[str, str], ...]] = (
    CrmPage.CRM_LINK,  # Crm.java:24
    CrmPage.CREATE_BUTTON,  # Crm.java:30
    CrmPage.CREATE_PIPELINE,  # Crm.java:43
    CrmPage.BUTTON_PIPELINE,  # Crm.java:73
    CrmPage.PROBABILITY_EDIT,  # Crm.java:85
    CrmPage.BUTTON_PIPELINE,  # Crm.java:92 - clicked PIPELINE_SIDE_BUTTON
    CrmPage.CUSTOMER_SIDE_BUTTON,  # Crm.java:135
    CrmPage.CREATE_CUSTOMER,  # Crm.java:137
    CrmPage.CREATE_CUSTOMER_BUTTON,  # Crm.java:140
    CrmPage.NAME_CUSTOMER,  # Crm.java:148
    CrmPage.DUE_PAYMENT_BUTTON,  # Crm.java:150 - clicked PRINT_BUTTON
)

#: The seven ``sendKeys`` sites in Java order, as ``(element, literal)``:
#: ``Crm.java:36``, :40, :76, :78, :80, :138, :141.  The middle three carry the
#: Examples row's values, which is why they appear here as the bound strings.
KEY_SITES_IN_ORDER: Final[tuple[tuple[tuple[str, str], str], ...]] = (
    (CrmPage.OPPORTUNITY_TITLE, TITLE_TEXT),  # Crm.java:36
    (CrmPage.EXPECTED_REVENUE, REVENUE_TEXT),  # Crm.java:40
    (CrmPage.OPPORTUNITY_TITLE_EDIT, EXAMPLES_ROW["opportunity"]),  # Crm.java:76
    (CrmPage.EXPECTED_REVENUE_EDIT, EXAMPLES_ROW["revenue"]),  # Crm.java:78
    (CrmPage.PROBABILITY_EDIT, EXAMPLES_ROW["probability"]),  # Crm.java:80
    (CrmPage.INPUT_NAME, CUSTOMER_NAME_TEXT),  # Crm.java:138
    (CrmPage.SEARCHING_TEXT, SEARCH_TEXT),  # Crm.java:141
)

#: The four ``Crm.java`` methods whose bodies carry a ``System.out.println``
#: pair - ``:51-52``, ``:63-64``, ``:97-98``, ``:126-127`` - and whose ports
#: reproduce **neither** line of the pair.  Half of the eight values are read
#: live from the system under test, and ``app/services/test_run_service.py``
#: relays every worker stdout line into the parent logger and from there into
#: the Jenkins console, so each line was a durable record of live customer and
#: pricing data (CWE-532/359, the security review's F04).  Diagnostic stdout is
#: in neither AAP 0.1.2's list of what the port must not change nor AAP 0.4.1's
#: enumeration of what each step body must reproduce, so removing the eight
#: statements costs no parity - which is why the expectation below is silence
#: rather than eight transcribed lines.
#:
#: The tuple is kept because it is still load-bearing in the negative
#: direction: these four are the bodies a print would be smuggled back into,
#: and :func:`test_the_four_formerly_printing_definitions_are_silent` drives
#: each of them, on the passing path and on the failing path, asserting nothing
#: reaches stdout.
FORMERLY_PRINTING_FUNCTIONS: Final[tuple[str, ...]] = (
    "user_can_see_the_total_price",  # Crm.java:51-52
    "user_can_see_new_pipeline",  # Crm.java:63-64
    "user_can_verify_the_information",  # Crm.java:97-98
    "user_can_see_the_new_changes_in_progress",  # Crm.java:126-127
)

#: The four ``Assert.assertEquals`` calls, as the ``(left, right)`` operand
#: names the port compares.  All four are the **two-argument** form, so the
#: order is JUnit's ``(expected, actual)`` and no message is attached:
#: ``:54`` compares the parsed total against the literal - the baseline
#: artifact's ``expected:<8> but was:<89>`` confirms which operand came first -
#: and ``:66``, ``:100`` and ``:129`` put the expected literal on the left.
ASSERT_OPERANDS: Final[MappingProxyType[str, tuple[str, str]]] = MappingProxyType(
    {
        "user_can_see_the_total_price": ("total_price", "price"),  # Crm.java:54
        "user_can_see_new_pipeline": ("expected_name", "actual_name"),  # :66
        "user_can_verify_the_information": ("expected_name", "actual_name"),  # :100
        "user_can_see_the_new_changes_in_progress": (
            "expected_name",
            "actual_name",
        ),  # Crm.java:129
    }
)

#: The eight definitions that assert nothing: ``Crm.java:21``, :27, :34, :70,
#: :83, :106, :132 and :145.  Run with nothing programmed they must complete
#: without raising and without emitting a line - an assertion smuggled into one
#: of them fails here, as does a print, which no definition in this module may
#: make at all.  The other four assert, which is why they are driven with the
#: page programmed instead.
SILENT_FUNCTIONS: Final[tuple[str, ...]] = (
    "user_click_on_the_crm_dashboard",
    "user_click_on_the_pipeline_button",
    "user_can_create_the_new_pipeline",
    "user_can_change_any_user_s_information_like_and",
    "user_can_save_information",
    "user_can_drag_and_drop_the_pipeline",
    "user_can_register_new_customer",
    "user_can_print_the_profile",
)

# --------------------------------------------------------------------------- #
# Per-call-site page-attribute names, transcribed from the Java line of each
# site (Crm.java:23-151).
#
# This is the name-aware half of the locator check and it exists because of
# DUPLICATE_SELECTORS: the driver log records ``(strategy, value)``, so a step
# that used CREATE_CUSTOMER (CrmP.java:76) where Java uses CREATE_BUTTON
# (CrmP.java:16) would produce a byte-identical log - and a wait, which resolves
# its locator inside EC.visibility_of_element_located, leaves no log entry to
# compare in the first place.  The names below are read out of the step module's
# AST, in source order, with each usage classified as one of:
#
#   "click" / "clear" / "text"  an operation on the resolved element, named
#                               through the lower-case accessor
#   "keys"                      a single keyboard call (press_keys), likewise
#                               on the resolved element, as sendKeys is applied
#                               to the field at Crm.java:36
#   "wait"                      a visibility wait, which names the UPPER_CASE
#                               locator constant: the spelling below is the
#                               spelling the site must use, so a lower-case
#                               accessor at a wait site is a diff here
#   "click_and_hold" /          an action-chain method taking the element, as
#   "move_to_element"           Crm.java:110 and :112 do
# --------------------------------------------------------------------------- #
SOURCE_SHAPES: Final[MappingProxyType[str, tuple[tuple[str, str], ...]]] = (
    MappingProxyType(
        {
            # Crm.java:23-24
            "user_click_on_the_crm_dashboard": (
                ("click", "crm_link"),
                ("wait", "CRM_LINK"),
            ),
            # Crm.java:29-30
            "user_click_on_the_pipeline_button": (
                ("click", "create_button"),
                ("wait", "CREATE_BUTTON"),
            ),
            # Crm.java:36-43
            "user_can_create_the_new_pipeline": (
                ("keys", "opportunity_title"),
                ("click", "customer"),
                ("click", "customer_id"),
                ("clear", "expected_revenue"),
                ("keys", "expected_revenue"),
                ("click", "priority"),
                ("click", "create_pipeline"),
                ("wait", "CREATE_PIPELINE"),
            ),
            # Crm.java:48
            "user_can_see_the_total_price": (("text", "total_price"),),
            # Crm.java:60
            "user_can_see_new_pipeline": (("text", "find_title_test"),),
            # Crm.java:72-80
            "user_can_change_any_user_s_information_like_and": (
                ("click", "button_pipeline"),
                ("wait", "BUTTON_PIPELINE"),
                ("click", "edit_button"),
                ("clear", "opportunity_title_edit"),
                ("keys", "opportunity_title_edit"),
                ("clear", "expected_revenue_edit"),
                ("keys", "expected_revenue_edit"),
                ("clear", "probability_edit"),
                ("keys", "probability_edit"),
            ),
            # Crm.java:85-86 - the class's one wait-then-click pairing
            "user_can_save_information": (
                ("wait", "PROBABILITY_EDIT"),
                ("click", "save_edit"),
            ),
            # Crm.java:91-94 - clicks PIPELINE_SIDE_BUTTON, waits on
            # BUTTON_PIPELINE
            "user_can_verify_the_information": (
                ("click", "pipeline_side_button"),
                ("wait", "BUTTON_PIPELINE"),
                ("text", "find_title_test"),
            ),
            # Crm.java:110-112 - PROGRESS_PIPELINE is BUTTON_PIPELINE's twin
            "user_can_drag_and_drop_the_pipeline": (
                ("click_and_hold", "progress_pipeline"),
                ("move_to_element", "progress_pipeline2"),
            ),
            # Crm.java:123
            "user_can_see_the_new_changes_in_progress": (("text", "test_verify"),),
            # Crm.java:134-141 - CREATE_CUSTOMER, CREATE_CUSTOMER_BUTTON and
            # INPUT_NAME are all twins of names used earlier in the class
            "user_can_register_new_customer": (
                ("click", "customer_side_button"),
                ("wait", "CUSTOMER_SIDE_BUTTON"),
                ("click", "create_customer"),
                ("wait", "CREATE_CUSTOMER"),
                ("keys", "input_name"),
                ("click", "create_customer_button"),
                ("wait", "CREATE_CUSTOMER_BUTTON"),
                ("keys", "searching_text"),
            ),
            # Crm.java:147-151 - clicks PRINT_BUTTON, waits on
            # DUE_PAYMENT_BUTTON, then clicks it
            "user_can_print_the_profile": (
                ("click", "name_customer"),
                ("wait", "NAME_CUSTOMER"),
                ("click", "print_button"),
                ("wait", "DUE_PAYMENT_BUTTON"),
                ("click", "due_payment_button"),
            ),
        }
    )
)

#: Each keyboard site as the source writes it: ``(accessor, key names, text
#: source)``, where the text source is ``("literal", value)`` for a hard-coded
#: string and ``("parameter", name)`` for a step argument.  Java concatenates
#: ``literal + Keys.ENTER`` into one ``sendKeys`` argument, so each site here
#: must name exactly one key - ``ENTER`` - and supply its text in the same
#: call.
KEY_CALL_SHAPES: Final[
    MappingProxyType[str, tuple[tuple[str, tuple[str, ...], tuple[str, str]], ...]]
] = MappingProxyType(
    {
        "user_can_create_the_new_pipeline": (
            # Crm.java:36
            ("opportunity_title", ("ENTER",), ("literal", TITLE_TEXT)),
            # Crm.java:40
            ("expected_revenue", ("ENTER",), ("literal", REVENUE_TEXT)),
        ),
        "user_can_change_any_user_s_information_like_and": (
            # Crm.java:76, :78, :80 - the values arrive as step parameters
            ("opportunity_title_edit", ("ENTER",), ("parameter", "opportunity")),
            ("expected_revenue_edit", ("ENTER",), ("parameter", "revenue")),
            ("probability_edit", ("ENTER",), ("parameter", "probability")),
        ),
        "user_can_register_new_customer": (
            # Crm.java:138
            ("input_name", ("ENTER",), ("literal", CUSTOMER_NAME_TEXT)),
            # Crm.java:141
            ("searching_text", ("ENTER",), ("literal", SEARCH_TEXT)),
        ),
    }
)

#: The action-chain method names of ``Crm.java:110-115``, in order.  ``pause``
#: appears twice, and ``perform`` closes the chain - Python's builder has no
#: ``build()`` step, so ``.perform()`` alone stands in for Java's
#: ``.build().perform()``.
CHAIN_METHODS_IN_ORDER: Final[tuple[str, ...]] = (
    "click_and_hold",
    "pause",
    "move_to_element",
    "pause",
    "release",
    "perform",
)

#: Accessor names installed by ``BasePage.__init_subclass__`` - one per locator
#: constant, lower-cased.  Reading one resolves an element against the driver.
ACCESSOR_NAMES: Final[frozenset[str]] = frozenset(
    name.lower() for name in CrmPage.LOCATORS
)

#: The locator constants themselves, upper-case, as the 28 ``@FindBy`` field
#: names of ``CrmP.java:13-95`` become them.  Reading one yields the
#: ``(strategy, value)`` pair and touches no driver: it is what a wait call site
#: passes, because ``app/automation/waits.py`` takes the locator and resolves it
#: inside ``EC.visibility_of_element_located`` on every poll - the
#: ``PageFactory`` proxy dereference ``Crm.java:24``'s ``visibilityOf`` argument
#: performed, under the 2 seconds of ``Crm.java:18``.
LOCATOR_NAMES: Final[frozenset[str]] = frozenset(CrmPage.LOCATORS)

#: Both spellings together, which recognises a page access in the AST without
#: assuming what the local variable holding the page is called, and - because
#: it admits the constants as well as the accessors - lets a wait site's *name*
#: be read out of the source exactly as a click site's is.  That is what keeps
#: :data:`DUPLICATE_SELECTORS` distinguishable at wait sites, where the driver
#: log records nothing at all.
PAGE_ATTRIBUTE_NAMES: Final[frozenset[str]] = ACCESSOR_NAMES | LOCATOR_NAMES


# =========================================================================== #
# Recording seams
#
# Three of the step module's globals are replaced for the duration of one run.
# Each records into a single ordered event list together with the *position* it
# occurred at - the number of driver operations logged so far - which is what
# lets StepRun.timeline() interleave seam events with driver calls and assert
# one fully ordered sequence per step.
# =========================================================================== #

#: Sentinel distinguishing "argument absent" from a legitimate ``None``.
_UNSET: Final[Any] = object()


class WaitTargetError(Exception):
    """A visibility wait received something other than a locator constant.

    Deliberately **not** an :class:`AssertionError`.  Three tests here run a
    step body inside ``pytest.raises(AssertionError)`` to exercise a Java
    assertion's failing branch (``Crm.java:66``, ``:100``, ``:129``), and a
    wait-shape failure inside one of those has to surface rather than be
    mistaken for the expected failure.
    """


def _locator_constant_name(target: Any) -> str:
    """The name of the ``CrmPage`` locator constant *target* is, by identity.

    ``Crm.java:18`` builds one 2-second ``WebDriverWait`` and applies it to a
    ``PageFactory`` proxy field at all eleven sites (``:24``, :30, :43, :73,
    :85, :92, :135, :137, :140, :148, :150), so the lookup happened *inside*
    ``ExpectedConditions.visibilityOf``, once per poll, governed by those 2
    seconds.  ``app/automation/waits.py`` reproduces that by taking the
    upper-case locator constant and resolving it inside
    ``EC.visibility_of_element_located``.  An element resolved through the
    lower-case accessor *before* the call would instead be looked up under
    ``app/automation/driver.py``'s 10-second implicit wait, leaving
    ``Crm.java:18``'s timeout governing nothing - a difference the driver log
    cannot show, which is why the target is checked here.

    Identity and not equality, because ``CrmP.java`` declares four selectors
    twice (:data:`DUPLICATE_SELECTORS`): ``CREATE_BUTTON`` (``CrmP.java:16``)
    and ``CREATE_CUSTOMER`` (``CrmP.java:76``) are equal tuples, so only the
    constant object itself names the field the Java line used.

    :param target: The first argument a visibility wait received.
    :returns: The upper-case ``CrmPage`` attribute name of *target*.
    :raises WaitTargetError: If *target* is not one of those constants - an
        already-resolved element, a retyped tuple, or anything else.
    """
    for name, locator in CrmPage.LOCATORS.items():
        if locator is target:
            return name

    raise WaitTargetError(
        f"a visibility wait was passed {target!r}, which is not a CrmPage "
        f"locator constant; each of the eleven sites of Crm.java:24-150 must "
        f"pass the upper-case constant so that app/automation/waits.py "
        f"resolves it inside EC.visibility_of_element_located under the 2 "
        f"seconds of Crm.java:18"
    )


def _locator_of(value: Any) -> Any:
    """Reduce a recorded action-chain argument to the locator that produced it.

    ``Crm.java:110`` and ``:112`` hand ``clickAndHold`` and ``moveToElement`` a
    resolved ``WebElement``, which the port reproduces, so a chain event records
    ``conftest``'s ``StubElement``.  Each stub carries the ``(strategy, value)``
    pair it was found by, so reducing it here lets a chain assertion name the
    locator rather than an opaque stub.  A visibility wait does **not** go
    through this: it is handed the locator constant itself, and normalising an
    element into one there would conceal a lookup made before the wait existed.

    :param value: One argument of a chain method - an element, a pause in
        seconds, or anything else such a method receives.
    :returns: ``value.locator`` when present, otherwise *value* unchanged.
    """
    return getattr(value, "locator", value)


class WaitEvent(NamedTuple):
    """One visibility-wait call: which helper, on what locator, for how long."""

    helper: str

    #: The upper-case ``CrmPage`` attribute name *target* is, by identity, so
    #: that a wait on the wrong twin of a duplicated selector is a diff.
    constant: str

    target: Any
    timeout: Any
    position: int

    def as_entry(self) -> tuple[Any, ...]:
        """Timeline form: the named constant, its locator pair, the timeout.

        The helper name is excluded because ``Crm.java:24`` names a condition
        rather than a helper; the constant, the ``(strategy, value)`` pair it
        yields and the 2-second literal of ``Crm.java:18`` are the parity facts.
        """
        return ("wait", self.constant, self.target, self.timeout)


class ChainFactoryEvent(NamedTuple):
    """One ``action_chain()`` call - the port of ``new Actions(...)``."""

    #: Ordinal of the chain this call produced, counted across the whole
    #: harness rather than per run, so two runs of the drag step can be shown
    #: to have received *different* builders.
    chain: int

    args: tuple[Any, ...]
    kwargs: tuple[tuple[str, Any], ...]
    position: int

    def as_entry(self) -> tuple[Any, ...]:
        """Timeline form, carrying the arguments the step passed."""
        return ("action_chain", self.args)


class ChainEvent(NamedTuple):
    """One method call on a recording action chain."""

    chain: int
    name: str
    args: tuple[Any, ...]
    position: int

    def as_entry(self) -> tuple[Any, ...]:
        """Timeline form: the method name and its locator-normalised args."""
        return ("chain", self.name, self.args)


class SleepEvent(NamedTuple):
    """One fixed delay - ``Thread.sleep`` in Java, never a chain pause."""

    seconds: Any
    position: int

    def as_entry(self) -> tuple[Any, ...]:
        """Timeline form."""
        return ("sleep", self.seconds)


class RecordingChain:
    """A duck-typed stand-in for ``ActionChains`` that records its calls.

    Substituting the chain factory is not a convenience.  A real
    ``ActionChains`` validates its arguments: ``move_to_element`` raises
    ``AttributeError("move_to requires a WebElement")`` when handed
    ``conftest``'s stub element, so the chain of ``Crm.java:110-115`` cannot be
    driven against the recorder without this.

    Every method returns ``self``, mirroring the builder, and any public method
    name is accepted rather than allowlisted - a chain that called something
    else is then reported as a *diff* against
    :data:`CHAIN_METHODS_IN_ORDER` rather than as an ``AttributeError`` with no
    context.
    """

    def __init__(self, index: int, events: list[Any], position: Any) -> None:
        """Bind this chain to the shared event log.

        :param index: 0-based ordinal of the ``action_chain()`` call that
            produced this object, so a test can prove a *fresh* chain per call.
        :param events: The run's ordered event list.
        :param position: Callable returning the number of driver operations
            logged so far, recorded with each method call.
        """
        self._index = index
        self._events = events
        self._position = position

    def __getattr__(self, name: str) -> Any:
        """Return a recorder for any public builder method.

        :param name: The method being reached for.
        :returns: A callable that logs ``(name, args)`` and returns ``self``.
        :raises AttributeError: For a private or dunder name, so that
            ``copy``, ``pickle`` and pytest's own introspection see a plain
            object rather than an infinitely attributed one.
        """
        if name.startswith("_"):
            raise AttributeError(name)

        def method(*args: Any, **kwargs: Any) -> RecordingChain:
            self._events.append(
                ChainEvent(
                    chain=self._index,
                    name=name,
                    args=tuple(_locator_of(argument) for argument in args)
                    + tuple(sorted(kwargs.items())),
                    position=self._position(),
                )
            )
            return self

        return method

    def __repr__(self) -> str:
        """Name the chain's ordinal, which is what a failure needs to see."""
        return f"<RecordingChain index={self._index}>"


class _TimeShim:
    """A ``time`` module stand-in whose ``sleep`` records instead of sleeping.

    ``Crm.java:117``'s ``Thread.sleep(2000)`` is ported as a fixed delay at its
    own call site, which Python spells either as ``from time import sleep`` -
    patched directly, without this class - or as ``import time`` plus
    ``time.sleep(2)``, which needs the module attribute intercepted.  Both
    record the same :class:`SleepEvent`, so the delay is observable whichever
    form the step module declares.  Every other attribute is delegated, so
    nothing else about ``time`` changes.
    """

    def __init__(self, module: ModuleType, recorder: Any) -> None:
        """Wrap *module*, routing ``sleep`` to *recorder*.

        :param module: The real ``time`` module found in the step namespace.
        :param recorder: Callable invoked with the requested delay.
        """
        #: The wrapped module, exposed so that a second run in the same test
        #: can rewrap it rather than nesting shims - a nested one would keep
        #: recording into the *first* run's event list.
        self.wrapped = module
        self._recorder = recorder

    def sleep(self, seconds: Any) -> None:
        """Record a fixed delay without performing one.

        :param seconds: The delay the step asked for.
        :returns: ``None``, as ``time.sleep`` does.
        """
        self._recorder(seconds)

    def __getattr__(self, name: str) -> Any:
        """Delegate every other attribute to the real module.

        :param name: Attribute being read.
        :returns: The real module's attribute.
        """
        return getattr(self.wrapped, name)


@dataclass(frozen=True)
class StepRun:
    """Everything one step body did, in order.

    ``calls`` is the raw ``stub_driver.calls`` slice for this run - asserted
    whole, never by membership - and ``timeline()`` is that same list with the
    wait, chain and delay events merged in at the positions they happened.
    """

    #: The phrase that was resolved and run.
    phrase: str

    #: The resolved definition, so a test can assert ``pattern``, ``kwargs``,
    #: ``module_name`` and the function's ``__name__``.
    match: Any

    #: Driver operations recorded during this run, in order.
    calls: tuple[tuple[str, tuple[Any, ...]], ...]

    #: Seam events recorded during this run, in order.
    events: tuple[Any, ...]

    #: Lines the step wrote to stdout, in order, with line endings stripped.
    #: Empty for every definition of this module: the eight
    #: ``System.out.println`` calls of ``Crm.java`` are deliberately not
    #: reproduced (module docstring), so the field exists to *prove* the
    #: silence and to fail if a print is smuggled back in.
    printed: tuple[str, ...]

    #: The exception the body raised when one was expected, else ``None``.
    error: BaseException | None

    #: Which delay seam was found - ``"sleep"``, ``"time.sleep"`` or ``None``
    #: when the step module exposes neither.
    sleep_seam: str | None

    @property
    def waits(self) -> tuple[WaitEvent, ...]:
        """Every wait call of this run, in order."""
        return tuple(event for event in self.events if isinstance(event, WaitEvent))

    @property
    def sleeps(self) -> tuple[Any, ...]:
        """The delays this run asked for - one per ``Thread.sleep`` site."""
        return tuple(
            event.seconds for event in self.events if isinstance(event, SleepEvent)
        )

    @property
    def chain_factory_calls(self) -> tuple[ChainFactoryEvent, ...]:
        """Every ``action_chain()`` call, so freshness per call is provable."""
        return tuple(
            event for event in self.events if isinstance(event, ChainFactoryEvent)
        )

    @property
    def chains(self) -> tuple[tuple[ChainEvent, ...], ...]:
        """Chain method calls grouped by the chain they were made on."""
        grouped: dict[int, list[ChainEvent]] = {}

        for event in self.events:
            if isinstance(event, ChainEvent):
                grouped.setdefault(event.chain, []).append(event)

        return tuple(tuple(calls) for _, calls in sorted(grouped.items()))

    def timeline(self) -> tuple[tuple[Any, ...], ...]:
        """Driver operations and seam events merged into one ordered sequence.

        :returns: The run's complete observable behaviour, in order.  Driver
            entries keep the exact ``(operation, args)`` shape
            ``conftest.StubDriver`` logs; seam entries are the ``as_entry()``
            forms.  An event recorded at position *p* is emitted before the
            driver call at index *p*, which is where it happened.
        """
        merged: list[tuple[Any, ...]] = []
        pending = 0

        for index, call in enumerate(self.calls):
            while pending < len(self.events) and self.events[pending].position <= index:
                merged.append(self.events[pending].as_entry())
                pending += 1

            merged.append(call)

        for event in self.events[pending:]:
            merged.append(event.as_entry())

        return tuple(merged)


class StepHarness:
    """Runs one CRM step against the recorder with the three seams replaced.

    Holds the conftest fixtures so a test reads as the scenario reads - a
    phrase, then assertions about what it did.  Reusable within a test: each
    :meth:`run` records its own slice of the driver log, which is what the
    whole-class aggregate tests use to total the eleven waits, the seven
    keyboard calls, the absence of any stdout line and the single fixed delay.
    """

    def __init__(
        self,
        resolve: Any,
        context: Any,
        driver: Any,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Bind the fixtures one run needs.

        :param resolve: ``conftest``'s ``resolve_step``.
        :param context: The behave context stand-in carrying *driver*.
        :param driver: The ``StubDriver`` published as ``context.driver``.
        :param monkeypatch: Used with ``setitem`` on the step module's own
            namespace, so every patch is undone at test teardown.
        :param capsys: Captures stdout, so each run can prove it wrote
            nothing - the ports of ``Crm.java``'s eight ``System.out.println``
            calls are deliberately absent (module docstring).
        """
        self._resolve = resolve
        self._context = context
        self._driver = driver
        self._monkeypatch = monkeypatch
        self._capsys = capsys
        # Counted across the harness, not per run: the ordinal is what proves
        # a second run of the drag step received a builder of its own.
        self._chains_built = 0

    @property
    def driver(self) -> Any:
        """The recorder this harness drives, for programming page answers."""
        return self._driver

    def run(
        self,
        phrase: str,
        *,
        expect: type[BaseException] | None = None,
    ) -> StepRun:
        """Resolve *phrase*, run its body, and return everything it did.

        :param phrase: A concrete Gherkin phrase, without its keyword.  For the
            parameterized definition this is the Examples-substituted text, so
            behave binds the arguments itself.
        :param expect: The exception type the body is expected to raise.
            ``None`` - the usual case - lets anything raised propagate, which
            is how an unexpected failure stays visible.
        :returns: The :class:`StepRun` for this call.

        The step module's namespace is ``StepMatch.func.__globals__``; the
        seams are replaced there and nowhere else, so no other module's view of
        ``app.automation`` or of ``time`` changes.
        """
        match = self._resolve(phrase)
        namespace = match.func.__globals__
        events: list[Any] = []
        start = len(self._driver.calls)

        def position() -> int:
            """Driver operations logged by this run so far."""
            return len(self._driver.calls) - start

        self._patch_waits(namespace, events, position)
        self._patch_action_chain(namespace, events, position)
        seam = self._patch_sleep(namespace, events, position)

        # Drop anything captured before this run so ``printed`` is this step's
        # output alone: that is what lets one test assert the silence of one
        # body and another assert it across all twelve.
        self._capsys.readouterr()

        error: BaseException | None = None

        if expect is None:
            match.run(self._context)
        else:
            with pytest.raises(expect) as raised:
                match.run(self._context)
            error = raised.value

        printed = tuple(self._capsys.readouterr().out.splitlines())

        return StepRun(
            phrase=phrase,
            match=match,
            calls=tuple(self._driver.calls[start:]),
            events=tuple(events),
            printed=printed,
            error=error,
            sleep_seam=seam,
        )

    def _patch_waits(
        self, namespace: dict[str, Any], events: list[Any], position: Any
    ) -> None:
        """Replace every ``wait*`` helper in *namespace* with a recorder.

        Every one of them rather than the single name this module imports, so
        that a visibility call routed through any other helper of
        ``app/automation/waits.py`` is recorded - and then fails the
        ``"visible"`` check of
        :func:`test_the_class_waits_eleven_times_and_always_for_two_seconds` -
        instead of reaching a real ``WebDriverWait`` and a real browser.
        """
        for name, value in list(namespace.items()):
            if name.startswith("wait") and callable(value):
                self._monkeypatch.setitem(
                    namespace, name, self._wait_recorder(name, events, position)
                )

    @staticmethod
    def _wait_recorder(helper: str, events: list[Any], position: Any) -> Any:
        """Build the stand-in for one wait helper.

        The target and the timeout are read exactly as
        ``wait_visible_element(locator, timeout, *, driver=None)`` declares
        them - positionally, with each parameter's own name as the fallback -
        and the target must then *be* a ``CrmPage`` locator constant.  A call
        carrying an already-resolved element raises
        :class:`WaitTargetError` here rather than being normalised into a
        locator, because that resolution would have happened before the wait
        existed (see :func:`_locator_constant_name`).
        """

        def recorder(*args: Any, **kwargs: Any) -> Any:
            target: Any = args[0] if args else kwargs.get("locator", _UNSET)
            timeout: Any = (
                args[1] if len(args) > 1 else kwargs.get("timeout", _UNSET)
            )

            if target is _UNSET:
                raise WaitTargetError(
                    f"{helper} was called without a locator: args={args!r}, "
                    f"kwargs={kwargs!r}"
                )

            events.append(
                WaitEvent(
                    helper=helper,
                    constant=_locator_constant_name(target),
                    target=target,
                    timeout=None if timeout is _UNSET else timeout,
                    position=position(),
                )
            )
            # The real helpers return the element they waited on; no step body
            # in this class uses the value, and returning the locator keeps the
            # seam total rather than silently substituting ``None``.
            return target

        return recorder

    def _patch_action_chain(
        self, namespace: dict[str, Any], events: list[Any], position: Any
    ) -> None:
        """Replace ``action_chain`` with a factory of recording chains."""

        def factory(*args: Any, **kwargs: Any) -> RecordingChain:
            index = self._chains_built
            self._chains_built += 1
            events.append(
                ChainFactoryEvent(
                    chain=index,
                    args=tuple(_locator_of(argument) for argument in args),
                    kwargs=tuple(sorted(kwargs.items())),
                    position=position(),
                )
            )
            # A NEW recorder per call, exactly as ``action_chain()`` returns a
            # new builder per call: a shared one would replay the previous
            # scenario's queued actions.
            return RecordingChain(index, events, position)

        if "action_chain" in namespace:
            self._monkeypatch.setitem(namespace, "action_chain", factory)

    def _patch_sleep(
        self, namespace: dict[str, Any], events: list[Any], position: Any
    ) -> str | None:
        """Replace the fixed-delay seam, whichever shape the module uses.

        :returns: ``"sleep"`` for ``from time import sleep``, ``"time.sleep"``
            for ``import time``, or ``None`` when neither is present - which
            the drag-and-drop test reports as a missing fixed delay rather than
            silently passing.
        """

        def recorder(seconds: Any) -> None:
            events.append(SleepEvent(seconds=seconds, position=position()))

        if callable(namespace.get("sleep")):
            self._monkeypatch.setitem(namespace, "sleep", recorder)
            return "sleep"

        module = namespace.get("time")

        # A shim left in place by an earlier run of the same test is unwrapped
        # rather than wrapped again: nesting would leave the innermost
        # recorder - the first run's - the one that actually fires.
        if isinstance(module, _TimeShim):
            module = module.wrapped

        if isinstance(module, ModuleType):
            self._monkeypatch.setitem(namespace, "time", _TimeShim(module, recorder))
            return "time.sleep"

        return None


@pytest.fixture(name="crm_step")
def _crm_step(
    resolve_step: Any,
    fake_context: Any,
    stub_driver: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> StepHarness:
    """A :class:`StepHarness` over this test's recorder and context.

    :param resolve_step: conftest's keyword-agnostic step resolver.
    :param fake_context: The behave context stand-in, carrying *stub_driver*.
    :param stub_driver: The ordered-log recorder.
    :param monkeypatch: Seam replacement, undone at teardown.
    :param capsys: stdout capture, read by every run to assert silence.
    :returns: The harness, ready to run any CRM phrase.
    """
    return StepHarness(resolve_step, fake_context, stub_driver, monkeypatch, capsys)


# =========================================================================== #
# Expected-call helpers
#
# The five log shapes ``conftest.StubDriver`` produces, plus the wait seam's,
# written as builders so a per-step expectation reads like the Java method it
# ports.  ``find_element`` logs ``(by, value)`` - which *is* the locator tuple -
# and every element operation logs the originating locator first.
# =========================================================================== #


def _find(locator: tuple[str, str]) -> tuple[Any, ...]:
    """A ``find_element`` entry.

    Every accessor access re-resolves - ``BasePage`` caches nothing, mirroring
    the un-cached ``@FindBy`` proxies - so a Java line touching one field twice
    produces two of these.
    """
    return ("find_element", locator)


def _click(locator: tuple[str, str]) -> tuple[Any, ...]:
    return ("element.click", (locator,))


def _clear(locator: tuple[str, str]) -> tuple[Any, ...]:
    return ("element.clear", (locator,))


def _keys(locator: tuple[str, str], text: str) -> tuple[Any, ...]:
    """One keyboard call carrying *text* then ``Keys.ENTER``.

    Java writes ``sendKeys(text + Keys.ENTER)``: one argument, one call.  The
    port's ``press_keys`` sends ``(text, Keys.ENTER)`` in a single
    ``send_keys``, so the browser sees the same one call - which is why this
    builder produces one entry and not two.
    """
    return ("element.send_keys", (locator, text, Keys.ENTER))


def _text(locator: tuple[str, str]) -> tuple[Any, ...]:
    """An ``element.text`` read - ``getText()`` in Java, and an operation."""
    return ("element.text", (locator,))


def _wait(locator: tuple[str, str]) -> tuple[Any, ...]:
    """A 2-second visibility wait on *locator* (``Crm.java:18``).

    *locator* is passed as the ``CrmPage`` constant, and the entry carries that
    constant's name alongside its value, so a wait on the wrong twin of a
    duplicated selector - or on an element resolved before the call - is a diff
    here instead of a byte-identical pass.
    """
    return ("wait", _locator_constant_name(locator), locator, WAIT_TIMEOUT)


# =========================================================================== #
# Source inspection
#
# The step module is parsed, never imported: behave's registry already exec'd
# it, and a second registration risks AmbiguousStep.  Parsing is also the only
# way to see the things the driver log cannot - which accessor *name* a call
# site used, the operand order of an assertion, and whether a delay is a chain
# pause or a fixed sleep.
# =========================================================================== #


@cache
def _module_source() -> str:
    return STEP_MODULE_PATH.read_text(encoding="utf-8")


@cache
def _module_ast() -> ast.Module:
    return ast.parse(_module_source(), filename=str(STEP_MODULE_PATH))


def _decorator_name(node: ast.expr) -> str:
    """The bare name of a decorator expression, called or not."""
    target = node.func if isinstance(node, ast.Call) else node

    if isinstance(target, ast.Name):
        return target.id

    if isinstance(target, ast.Attribute):
        return target.attr

    return ""


@cache
def _registered_definitions() -> tuple[tuple[str, str], ...]:
    """``(pattern, function name)`` for every ``@step`` definition, in order.

    :returns: The step module's own declaration of its surface, in source
        order.
    :raises AssertionError: If a ``@step`` decorator's pattern is not a plain
        string literal.  behave would accept a computed pattern, but a census
        cannot be checked against one, and nothing in this port needs it.

    A definition registered with ``@given``, ``@when`` or ``@then`` is *not*
    collected here, which is deliberate: it would then be missing from this
    list and the census test would fail, which is the correct outcome - AAP
    deviation 7 requires ``@step`` for every definition so that behave
    reproduces Cucumber-JVM's text-only matching.
    """
    found: list[tuple[str, str]] = []

    for node in _module_ast().body:
        if not isinstance(node, ast.FunctionDef):
            continue

        for decorator in node.decorator_list:
            if _decorator_name(decorator) != "step":
                continue

            if not isinstance(decorator, ast.Call) or not decorator.args:
                raise AssertionError(
                    f"{node.name} is decorated with a bare @step; every "
                    f"definition in {STEP_MODULE_PATH.name} must register a "
                    f"literal phrase"
                )

            pattern = decorator.args[0]

            if not isinstance(pattern, ast.Constant) or not isinstance(
                pattern.value, str
            ):
                raise AssertionError(
                    f"{node.name}'s @step pattern is not a string literal: "
                    f"{ast.dump(pattern)}"
                )

            found.append((pattern.value, node.name))

    return tuple(found)


@cache
def _declared_all() -> tuple[str, ...]:
    """The module's ``__all__``, in declaration order.

    The step module keeps one so its surface is greppable; it is asserted
    against the census too, so a definition added without being exported - or
    exported without existing - fails.
    """
    for node in _module_ast().body:
        # Both spellings, because an annotated declaration - ``__all__:
        # list[str] = [...]`` - is a different node type and is equally valid.
        if isinstance(node, ast.Assign):
            targets = [
                target.id for target in node.targets if isinstance(target, ast.Name)
            ]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target.id]
        else:
            continue

        if "__all__" not in targets:
            continue

        if not isinstance(node.value, ast.List):
            raise AssertionError(
                f"__all__ in {STEP_MODULE_PATH.name} is not a list literal"
            )

        return tuple(
            element.value
            for element in node.value.elts
            if isinstance(element, ast.Constant)
        )

    raise AssertionError(f"{STEP_MODULE_PATH.name} declares no __all__")


@cache
def _function_nodes() -> MappingProxyType[str, ast.FunctionDef]:
    """Every top-level function of the step module, by name."""
    return MappingProxyType(
        {
            node.name: node
            for node in _module_ast().body
            if isinstance(node, ast.FunctionDef)
        }
    )


def _function_node(name: str) -> ast.FunctionDef:
    """One function node, with a message naming the module when absent."""
    nodes = _function_nodes()

    if name not in nodes:
        raise AssertionError(
            f"{STEP_MODULE_PATH.name} declares no function {name!r}; it "
            f"declares {sorted(nodes)}"
        )

    return nodes[name]


def _parents(node: ast.AST) -> dict[ast.AST, ast.AST]:
    """A child-to-parent map for one subtree, keyed by node identity."""
    return {
        child: parent
        for parent in ast.walk(node)
        for child in ast.iter_child_nodes(parent)
    }


def _normalise_usage(name: str) -> str:
    """Reduce a callee name to the operation it performs.

    Any ``wait*`` helper becomes ``"wait"`` and the keyboard helper becomes
    ``"keys"``, because the parity fact at such a site is *which page attribute*
    it names: ``Crm.java:24`` names a condition, ``:36`` a ``sendKeys``, and
    neither names a Python helper.  For a wait that attribute is the upper-case
    locator constant, which :data:`SOURCE_SHAPES` spells out site by site, so
    this reduction cannot blur a locator into an element.  Everything else -
    ``click``, ``clear``, ``text`` and the action-chain methods - is kept as
    written, because those names are the behaviour.
    """
    lowered = name.lower()

    if lowered.startswith("wait"):
        return "wait"

    if lowered == "press_keys" or lowered.endswith("send_keys"):
        return "keys"

    return lowered


def _enclosing(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> ast.AST | None:
    """The node that *uses* this one, looking through argument wrappers.

    A page attribute passed as ``wait_visible_element(locator=page.CRM_LINK, 2)``
    sits under an ``ast.keyword`` rather than directly under the call, and one
    passed as ``helper(*pair)`` under an ``ast.Starred``.  Climbing through both
    means the usage is classified from the call itself, so the *name* recorded
    for the site is the one the source writes however the argument is spelled -
    and an upper-case constant stays distinguishable from a lower-case accessor
    in either spelling.
    """
    parent = parents.get(node)

    while isinstance(parent, (ast.keyword, ast.Starred)):
        node = parent
        parent = parents.get(node)

    return parent


def _classify_usage(node: ast.Attribute, parents: dict[ast.AST, ast.AST]) -> str:
    """What is done with one page accessor at its call site."""
    parent = _enclosing(node, parents)

    if isinstance(parent, ast.Attribute):
        # ``page.x.click()`` and ``page.x.text`` - the operation is the outer
        # attribute's name.
        return _normalise_usage(parent.attr)

    if isinstance(parent, ast.Call):
        callee = parent.func

        if isinstance(callee, ast.Name):
            return _normalise_usage(callee.id)

        if isinstance(callee, ast.Attribute):
            return _normalise_usage(callee.attr)

    return "read"


@cache
def _accessor_usages(function_name: str) -> tuple[tuple[str, str], ...]:
    """``(usage, accessor name)`` for every page access, in source order.

    A page access is recognised as ``<name>.<attribute>`` where the attribute is
    one of :data:`PAGE_ATTRIBUTE_NAMES`, rather than by assuming the local
    variable is called ``page``: that set is exactly the 28 ``@FindBy`` fields of
    ``CrmP.java:13-95`` under both spellings the port uses - the lower-case
    accessor ``BasePage.__init_subclass__`` installs, and the upper-case locator
    constant a wait site hands the helper - so the recognition cannot drift and
    cannot produce a false positive on an unrelated attribute.  The name is
    reported as the source spells it, which is how a wait on the wrong twin of a
    duplicated selector, or on an element resolved before the wait, is caught.
    """
    node = _function_node(function_name)
    parents = _parents(node)
    located: list[tuple[tuple[int, int], tuple[str, str]]] = []

    for candidate in ast.walk(node):
        if not isinstance(candidate, ast.Attribute):
            continue

        if not isinstance(candidate.value, ast.Name):
            continue

        if candidate.attr not in PAGE_ATTRIBUTE_NAMES:
            continue

        located.append(
            (
                (candidate.lineno, candidate.col_offset),
                (_classify_usage(candidate, parents), candidate.attr),
            )
        )

    return tuple(usage for _, usage in sorted(located))


def _text_source(node: ast.expr) -> tuple[str, str]:
    """Where a keyboard call's text comes from: a literal or a parameter."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return ("literal", node.value)

    if isinstance(node, ast.Name):
        return ("parameter", node.id)

    return ("expression", ast.dump(node))


@cache
def _key_call_shapes(
    function_name: str,
) -> tuple[tuple[str, tuple[str, ...], tuple[str, str]], ...]:
    """``(accessor, key names, text source)`` per keyboard site, in order."""
    node = _function_node(function_name)
    located: list[
        tuple[tuple[int, int], tuple[str, tuple[str, ...], tuple[str, str]]]
    ] = []

    for candidate in ast.walk(node):
        if not isinstance(candidate, ast.Call):
            continue

        callee = candidate.func
        name = (
            callee.id
            if isinstance(callee, ast.Name)
            else callee.attr
            if isinstance(callee, ast.Attribute)
            else ""
        )

        if _normalise_usage(name) != "keys":
            continue

        # Positional first, then the parameter's own name, as
        # ``press_keys(target, *key_names, text=..., driver=...)`` declares it.
        # Java applies sendKeys to the field itself (Crm.java:36), so a keyboard
        # site is named through the lower-case accessor - unlike a wait site.
        target: ast.expr | None = candidate.args[0] if candidate.args else None

        if target is None:
            for keyword in candidate.keywords:
                if keyword.arg == "target":
                    target = keyword.value
                    break

        accessor = (
            target.attr
            if isinstance(target, ast.Attribute)
            else f"<{ast.dump(target) if target else 'missing'}>"
        )
        key_names = tuple(
            argument.value
            for argument in candidate.args[1:]
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str)
        )
        text: tuple[str, str] = ("absent", "")

        for keyword in candidate.keywords:
            if keyword.arg == "text":
                text = _text_source(keyword.value)

        located.append(
            ((candidate.lineno, candidate.col_offset), (accessor, key_names, text))
        )

    return tuple(shape for _, shape in sorted(located))


def _operand_name(node: ast.expr) -> str:
    """A readable name for one side of an ``==`` comparison."""
    if isinstance(node, ast.Name):
        return node.id

    if isinstance(node, ast.Constant):
        return repr(node.value)

    return ast.dump(node)


@cache
def _assert_shapes(function_name: str) -> tuple[tuple[str, str, bool], ...]:
    """``(left operand, right operand, has message)`` per ``assert``."""
    node = _function_node(function_name)
    shapes: list[tuple[str, str, bool]] = []

    for candidate in ast.walk(node):
        if not isinstance(candidate, ast.Assert):
            continue

        test = candidate.test

        if (
            isinstance(test, ast.Compare)
            and len(test.ops) == 1
            and isinstance(test.ops[0], ast.Eq)
        ):
            shapes.append(
                (
                    _operand_name(test.left),
                    _operand_name(test.comparators[0]),
                    candidate.msg is not None,
                )
            )
        else:
            # Not an equality comparison at all: reported verbatim so the
            # failure names what the source actually says.
            shapes.append(("<not an equality>", ast.dump(test), candidate.msg is not None))

    return tuple(shapes)


@cache
def _print_count(function_name: str) -> int:
    """How many ``print`` calls one function makes - nought, for all twelve.

    Kept, and now asserted at nought everywhere: ``Crm.java``'s eight
    ``System.out.println`` calls are deliberately not reproduced (F04), and a
    source-level count is what catches a print on a branch no test drives.
    :func:`test_the_step_module_contains_no_print_call_at_all` extends the same
    count to the whole file, since a print outside the twelve bodies would
    belong to no census entry.
    """
    node = _function_node(function_name)

    return sum(
        1
        for candidate in ast.walk(node)
        if isinstance(candidate, ast.Call)
        and isinstance(candidate.func, ast.Name)
        and candidate.func.id == "print"
    )


def _is_sleep_call(node: ast.AST) -> bool:
    """Whether *node* is a fixed-delay call in either supported shape."""
    if not isinstance(node, ast.Call):
        return False

    callee = node.func

    if isinstance(callee, ast.Name):
        return callee.id == "sleep"

    return isinstance(callee, ast.Attribute) and callee.attr == "sleep"


@cache
def _sleep_sites() -> tuple[tuple[str, Any], ...]:
    """``(function name, delay literal)`` for every fixed delay in the module."""
    sites: list[tuple[str, Any]] = []

    for name, node in _function_nodes().items():
        for candidate in ast.walk(node):
            if not _is_sleep_call(candidate):
                continue

            argument = candidate.args[0] if candidate.args else None
            sites.append(
                (
                    name,
                    argument.value
                    if isinstance(argument, ast.Constant)
                    else ast.dump(argument)
                    if argument
                    else None,
                )
            )

    return tuple(sites)


def _statement_index(function_name: str, predicate: Any) -> int:
    """Index of the first top-level statement whose subtree satisfies *predicate*.

    :returns: The statement's position in the function body, or ``-1``.
    """
    node = _function_node(function_name)

    for index, statement in enumerate(node.body):
        if any(predicate(child) for child in ast.walk(statement)):
            return index

    return -1


def _java_line_range(entry: Definition) -> tuple[int, int]:
    """The ``Crm.java`` lines one definition spans.

    From its own annotation line to the next definition's - or to
    ``Crm.java:154``, the class's closing brace, for the last one.  This
    attributes a line a test quotes - an ``assertEquals`` at ``Crm.java:54``,
    for instance - to the method that contains it.
    """
    following = CENSUS[entry.index] if entry.index < len(CENSUS) else None

    return (
        entry.java_line,
        following.java_line if following else JAVA_CLASS_LAST_LINE,
    )


def _camel_to_snake(name: str) -> str:
    """Convert a Java method name to the port's function name.

    ``userCanSeeTheTotalPrice`` becomes ``user_can_see_the_total_price``, and a
    name already in snake case - ``Crm.java:22`` is the class's one such
    method - is returned unchanged.
    """
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


# =========================================================================== #
# Feature inspection
# =========================================================================== #

#: Gherkin step keywords, with their trailing space so that a line reading
#: ``Given User ...`` is split correctly and a scenario title is not mistaken
#: for a step.
GHERKIN_KEYWORDS: Final[tuple[str, ...]] = (
    "Given ",
    "When ",
    "Then ",
    "And ",
    "But ",
)


@cache
def _feature_lines() -> tuple[str, ...]:
    """``features/Crm.feature``, line by line, exactly as written."""
    return tuple(FEATURE_PATH.read_text(encoding="utf-8").splitlines())


def _substitute_examples(phrase: str) -> str:
    """Bind the single Examples row into an outline placeholder."""
    for name, value in EXAMPLES_ROW.items():
        phrase = phrase.replace(f"<{name}>", value)

    return phrase


@cache
def _feature_steps() -> tuple[tuple[int, str], ...]:
    """``(line number, concrete phrase)`` for every step use in the feature."""
    steps: list[tuple[int, str]] = []

    for number, line in enumerate(_feature_lines(), start=1):
        stripped = line.strip()

        for keyword in GHERKIN_KEYWORDS:
            if stripped.startswith(keyword):
                steps.append(
                    (number, _substitute_examples(stripped[len(keyword) :].strip()))
                )
                break

    return tuple(steps)


def _table_cells(line: str) -> tuple[str, ...]:
    """The cells of one Gherkin table row."""
    return tuple(cell.strip() for cell in line.strip().strip("|").split("|"))


# =========================================================================== #
# The census, and its three fail-closed gates
# =========================================================================== #


def test_census_transcribes_exactly_twelve_java_definitions() -> None:
    """The inventory of ``Crm.java:21-153`` is twelve methods and no more.

    Order-independent by construction: every value compared here is a module
    constant or derived from one, so this passes under ``pytest -k`` on its
    own.  A thirteenth row, a duplicated phrase or a reordered census fails.
    """
    assert len(CENSUS) == DEFINITION_COUNT

    # Declaration order, as ``Crm.java:21-145`` has them.
    assert tuple(entry.index for entry in CENSUS) == tuple(
        range(1, DEFINITION_COUNT + 1)
    )
    assert tuple(entry.java_line for entry in CENSUS) == (
        21,
        27,
        34,
        46,
        58,
        70,
        83,
        89,
        106,
        121,
        132,
        145,
    )

    # Strictly increasing: two rows cannot name the same annotation line.
    lines = [entry.java_line for entry in CENSUS]
    assert lines == sorted(set(lines))

    # No phrase and no function appears twice - the census is a set of twelve
    # distinct definitions, not twelve rows that might overlap.
    assert len({entry.pattern for entry in CENSUS}) == DEFINITION_COUNT
    assert len({entry.function for entry in CENSUS}) == DEFINITION_COUNT
    assert len({entry.java_method for entry in CENSUS}) == DEFINITION_COUNT

    # One @When, seven @And, four @Then (Crm.java:21, :27-:132, :58-:145).
    counted = {keyword: 0 for keyword in KEYWORD_COUNTS}

    for entry in CENSUS:
        assert entry.java_keyword in KEYWORD_COUNTS
        counted[entry.java_keyword] += 1

    assert counted == dict(KEYWORD_COUNTS)


def test_every_census_definition_is_claimed_by_a_test_in_this_module() -> None:
    """Each of the twelve methods of ``Crm.java:21-145`` has a parity test here.

    The fail-closed half of the census: a definition whose test is deleted,
    renamed, or left without its Java line in the docstring fails *this* test.
    Nothing is accumulated at run time - the claims are looked up statically in
    this module's namespace - so no test ordering can make it pass vacuously.
    """
    namespace = globals()

    for entry in CENSUS:
        claim = namespace.get(entry.claimed_by)

        assert callable(claim), (
            f"Crm.java:{entry.java_line} ({entry.java_method}) names "
            f"{entry.claimed_by!r} as its parity test, and this module does "
            f"not define it"
        )
        assert entry.claimed_by.startswith("test_")

        docstring = claim.__doc__ or ""
        assert f"Crm.java:{entry.java_line}" in docstring, (
            f"{entry.claimed_by} must name Crm.java:{entry.java_line} in its "
            f"docstring, so the line it pins is readable at the failure"
        )

    # Every claimed test is claimed once: two definitions sharing one test
    # would mean a definition with no coverage of its own.
    claims = [entry.claimed_by for entry in CENSUS]
    assert len(set(claims)) == DEFINITION_COUNT


def test_step_module_registers_exactly_the_census_definitions() -> None:
    """The port declares the twelve ``Crm.java:21-145`` definitions, in Java order.

    Read out of ``features/steps/crm_steps.py``'s AST, so an added, removed,
    renamed or re-keyworded definition fails here rather than passing
    silently - which is the omission guard AAP 0.4.1 requires of a parity
    module.  Registration with ``@given``/``@when``/``@then`` instead of
    ``@step`` also fails, because such a definition is not collected at all.
    """
    declared = _registered_definitions()
    expected = tuple((entry.pattern, entry.function) for entry in CENSUS)

    assert len(declared) == DEFINITION_COUNT, (
        f"{STEP_MODULE_PATH.name} declares {len(declared)} @step definitions; "
        f"{JAVA_STEP_CLASS} at {REFERENCE_COMMIT[:7]} declares "
        f"{DEFINITION_COUNT}"
    )

    # Sets first, so an unported or extra definition is named plainly, then
    # order, which is the Java declaration order the module claims to keep.
    assert {function for _, function in declared} == {
        entry.function for entry in CENSUS
    }
    assert {pattern for pattern, _ in declared} == {entry.pattern for entry in CENSUS}
    assert declared == expected

    # The module's own advertised surface has to agree with both.
    assert _declared_all() == tuple(entry.function for entry in CENSUS)


@pytest.mark.parametrize("entry", CENSUS, ids=lambda entry: entry.java_method)
def test_each_definition_resolves_once_from_crm_steps(
    entry: Definition, resolve_step: Any
) -> None:
    """Every ``Crm.java:21-145`` phrase resolves to its ported body, and only it.

    ``resolve_step`` raises when a phrase matches no definition or more than
    one, so resolving all twelve proves the module's patterns are unambiguous
    under ``@step``'s keyword-agnostic matching - the price of reproducing
    Cucumber-JVM's text-only match (AAP deviation 7).
    """
    match = resolve_step(entry.concrete_phrase)

    assert match.func.__name__ == entry.function
    assert match.pattern == entry.pattern
    assert match.module_name == STEP_MODULE_NAME
    assert match.bucket == "step"


@pytest.mark.parametrize("entry", CENSUS, ids=lambda entry: entry.java_method)
def test_pattern_transcribes_the_java_annotation_phrase(entry: Definition) -> None:
    """Each registered pattern is its Java annotation phrase, unedited.

    Cucumber's positional ``{string}`` becomes a *named* behave field, which is
    the only licensed difference; the surrounding text - spacing and
    punctuation included - must be identical.
    """
    generalised = re.sub(r'"\{\w+\}"', "{string}", entry.pattern)

    assert generalised == entry.java_phrase


@pytest.mark.parametrize("entry", CENSUS, ids=lambda entry: entry.java_method)
def test_function_name_is_the_java_method_name_in_snake_case(
    entry: Definition,
) -> None:
    """The ported function keeps its Java method's name, snake-cased."""
    assert entry.function == _camel_to_snake(entry.java_method)


# =========================================================================== #
# The four duplicate selectors of CrmP.java:13-95, as declared facts
# =========================================================================== #


def test_the_four_duplicated_selectors_are_declared_under_both_names() -> None:
    """``CrmP.java`` declares four selectors twice, and the port keeps both.

    The reason every call site is additionally checked by *name*: these four
    pairs - ``CrmP.java:16``/:76, :34/:82, :43/:64 and :49/:79 - are
    indistinguishable in the driver log, so a step using the wrong twin would
    produce a byte-identical sequence.  The values are retyped from
    the ``@FindBy`` annotations rather than read from the page object, since
    here the shared value is the assertion.
    """
    for first, second, value in DUPLICATE_SELECTORS:
        assert first != second
        assert first in CrmPage.LOCATORS, f"{first} is declared in {JAVA_PAGE_CLASS}"
        assert second in CrmPage.LOCATORS, f"{second} is declared in {JAVA_PAGE_CLASS}"
        assert CrmPage.LOCATORS[first] == CrmPage.LOCATORS[second]
        assert CrmPage.LOCATORS[first][1] == value

    # And there are exactly four such pairs: a fifth duplicate would be a
    # locator whose name the driver log cannot distinguish and which no
    # name-aware assertion covers yet.
    by_value: dict[tuple[str, str], list[str]] = {}

    for name, locator in CrmPage.LOCATORS.items():
        by_value.setdefault(locator, []).append(name)

    shared = {
        locator: sorted(names) for locator, names in by_value.items() if len(names) > 1
    }

    assert len(shared) == len(DUPLICATE_SELECTORS)
    assert all(len(names) == 2 for names in shared.values())
    assert {tuple(sorted((first, second))) for first, second, _ in DUPLICATE_SELECTORS} == {
        tuple(names) for names in shared.values()
    }


# =========================================================================== #
# Definition 1 - Crm.java:21-25
# =========================================================================== #


def test_crm_dashboard_clicks_the_menu_then_waits_on_it(crm_step: StepHarness) -> None:
    """Crm.java:21-25 - click ``crmLink``, then wait 2s on it.

    Click-then-wait is the source's order at ``:23-24`` and is kept rather than
    tidied into wait-then-click.  One accessor read, so one lookup: the wait of
    ``:24`` is handed ``CRM_LINK`` as a locator and re-resolves it inside its
    own predicate on every poll - the dereference the Java proxy performed,
    now governed by the 2 seconds of ``:18`` rather than by the session's
    implicit wait.  The merged timeline is what places it after the click.
    """
    run = crm_step.run("User click on the crm dashboard")

    assert run.calls == (
        _find(CrmPage.CRM_LINK),
        _click(CrmPage.CRM_LINK),
    )
    assert run.timeline() == (
        _find(CrmPage.CRM_LINK),
        _click(CrmPage.CRM_LINK),
        _wait(CrmPage.CRM_LINK),
    )
    assert run.printed == ()
    assert run.sleeps == ()


# =========================================================================== #
# Definition 2 - Crm.java:27-32
# =========================================================================== #


def test_pipeline_button_clicks_create_then_waits_on_it(
    crm_step: StepHarness,
) -> None:
    """Crm.java:27-32 - click ``createButton``, then wait 2s on it.

    ``CREATE_BUTTON`` is the name ``:29`` uses even though ``CREATE_CUSTOMER``
    carries the identical selector; the name is asserted by the source-shape
    test, which the driver log alone could not do.
    """
    run = crm_step.run("User click on the pipeline button")

    assert run.calls == (
        _find(CrmPage.CREATE_BUTTON),
        _click(CrmPage.CREATE_BUTTON),
    )
    assert run.timeline() == (
        _find(CrmPage.CREATE_BUTTON),
        _click(CrmPage.CREATE_BUTTON),
        _wait(CrmPage.CREATE_BUTTON),
    )
    assert run.printed == ()


# =========================================================================== #
# Definition 3 - Crm.java:34-44
# =========================================================================== #


def test_create_new_pipeline_fills_the_dialog_in_java_order(
    crm_step: StepHarness,
) -> None:
    """Crm.java:34-44 - eight operations, then a 2s wait on the confirm button.

    In source order: ``"test"`` and Enter into the title (``:36``), the
    customer autocomplete (``:37``) and its suggestion (``:38``), clear then
    ``"8"`` and Enter into the revenue (``:39-40``), the priority star
    (``:41``), confirm (``:42``) and wait (``:43``).  Each ``sendKeys`` is one
    keyboard call carrying the literal *and* ``Keys.ENTER``, as the Java
    concatenation is.
    """
    run = crm_step.run("User can create the new pipeline")

    assert run.calls == (
        _find(CrmPage.OPPORTUNITY_TITLE),
        _keys(CrmPage.OPPORTUNITY_TITLE, TITLE_TEXT),
        _find(CrmPage.CUSTOMER),
        _click(CrmPage.CUSTOMER),
        _find(CrmPage.CUSTOMER_ID),
        _click(CrmPage.CUSTOMER_ID),
        _find(CrmPage.EXPECTED_REVENUE),
        _clear(CrmPage.EXPECTED_REVENUE),
        _find(CrmPage.EXPECTED_REVENUE),
        _keys(CrmPage.EXPECTED_REVENUE, REVENUE_TEXT),
        _find(CrmPage.PRIORITY),
        _click(CrmPage.PRIORITY),
        _find(CrmPage.CREATE_PIPELINE),
        _click(CrmPage.CREATE_PIPELINE),
    )

    # One wait, at the end, on the element just clicked.
    assert tuple(event.as_entry() for event in run.waits) == (
        _wait(CrmPage.CREATE_PIPELINE),
    )
    assert run.timeline()[-1] == _wait(CrmPage.CREATE_PIPELINE)

    # Exactly two keyboard calls, each one call carrying text then ENTER.
    assert run.calls.count(_keys(CrmPage.OPPORTUNITY_TITLE, TITLE_TEXT)) == 1
    assert (
        sum(1 for operation, _ in run.calls if operation == "element.send_keys") == 2
    )
    assert run.printed == ()


# =========================================================================== #
# Definition 4 - Crm.java:46-56
# =========================================================================== #


def test_total_price_adds_eight_and_compares_against_eighty_nine(
    crm_step: StepHarness,
) -> None:
    """Crm.java:46-56 - ``int(totalPrice) + 8`` against the literal ``89``.

    ``81`` is the one column total that satisfies ``:54``.  Both numbers of
    ``:48-49`` are load-bearing and neither is derived from the page.
    """
    crm_step.driver.set_text(CrmPage.TOTAL_PRICE, PASSING_TOTAL_TEXT)

    run = crm_step.run("User can see the total price")

    assert run.calls == (
        _find(CrmPage.TOTAL_PRICE),
        _text(CrmPage.TOTAL_PRICE),
    )
    assert run.printed == (), (
        "Crm.java:51-52's two prints are not reproduced: the parsed total is "
        "read live from the system under test and the worker's stdout reaches "
        "the Jenkins console (F04)"
    )
    assert int(PASSING_TOTAL_TEXT) + PRICE_ADDEND == EXPECTED_PRICE
    assert run.waits == ()
    assert run.sleeps == ()


def test_total_price_fails_when_the_column_total_is_not_eighty_one(
    crm_step: StepHarness,
) -> None:
    """Crm.java:54 - the assertion fails for any other total.

    The failing path is where ``:51-52``'s prints would have carried the live
    total furthest, since a failed step is the one a reader goes to the CI log
    for; the port emits nothing there either, and the ``AssertionError`` alone
    is what the engine records.  That failure is the observable behaviour the
    baseline artifact recorded for this step.
    """
    crm_step.driver.set_text(CrmPage.TOTAL_PRICE, "10")

    run = crm_step.run("User can see the total price", expect=AssertionError)

    assert isinstance(run.error, AssertionError)
    assert run.printed == (), (
        "the failing path must not print the live total either; Crm.java:51-52 "
        "are not reproduced (F04)"
    )


@pytest.mark.parametrize("total_text", ["", "eighty-one", "8.5", "81 kr"])
def test_total_price_raises_value_error_on_non_numeric_text(
    crm_step: StepHarness, total_text: str
) -> None:
    """Crm.java:48 - ``Integer.parseInt`` throws, and nothing catches it.

    The Java method declares no ``try`` and no fallback, so the port lets
    ``int()``'s ``ValueError`` propagate and the scenario fails exactly where
    the Java scenario failed.  Nothing reaches stdout on this path either -
    nothing does on any path in this module.
    """
    crm_step.driver.set_text(CrmPage.TOTAL_PRICE, total_text)

    run = crm_step.run("User can see the total price", expect=ValueError)

    assert isinstance(run.error, ValueError)
    assert run.printed == ()
    assert run.calls == (
        _find(CrmPage.TOTAL_PRICE),
        _text(CrmPage.TOTAL_PRICE),
    )


# =========================================================================== #
# Definition 5 - Crm.java:58-68
# =========================================================================== #


def test_see_new_pipeline_expects_the_lower_case_title(
    crm_step: StepHarness,
) -> None:
    """Crm.java:58-68 - the first card's title must read ``"test"``.

    Lower-case, and deliberately not the ``"Test2"`` that
    *User can verify the information* expects at ``:95``; the three expected
    literals of this class are not unified.
    """
    crm_step.driver.set_text(CrmPage.FIND_TITLE_TEST, TITLE_TEXT)

    run = crm_step.run("User can see new pipeline")

    assert run.calls == (
        _find(CrmPage.FIND_TITLE_TEST),
        _text(CrmPage.FIND_TITLE_TEST),
    )
    assert run.printed == (), (
        "Crm.java:63-64's two prints are not reproduced: the card title is a "
        "live SUT value and the worker's stdout reaches the Jenkins console "
        "(F04)"
    )
    assert run.waits == ()


# =========================================================================== #
# Definition 6 - Crm.java:70-81
# =========================================================================== #


def test_change_information_edits_three_fields_in_java_order(
    crm_step: StepHarness,
) -> None:
    """Crm.java:70-81 - open the card, wait, edit, then clear/retype three fields.

    The class's only parameterized definition, and the only one that neither
    waits at the end, prints, nor asserts.  Each of the three fields is cleared
    and then sent its value *and* Enter in one keyboard call (``:76``, ``:78``,
    ``:80``), the values arriving from the Examples row at ``Crm.feature:24``.
    ``OPPORTUNITY_TITLE_EDIT`` is ``:75``'s name even though ``INPUT_NAME``
    shares its selector.
    """
    run = crm_step.run(OUTLINE_STEP_PHRASE)

    # behave binds the three arguments; the values are the Examples row's.
    assert run.match.kwargs == dict(EXAMPLES_ROW)
    assert run.match.args == ()

    assert run.calls == (
        _find(CrmPage.BUTTON_PIPELINE),
        _click(CrmPage.BUTTON_PIPELINE),
        _find(CrmPage.EDIT_BUTTON),
        _click(CrmPage.EDIT_BUTTON),
        _find(CrmPage.OPPORTUNITY_TITLE_EDIT),
        _clear(CrmPage.OPPORTUNITY_TITLE_EDIT),
        _find(CrmPage.OPPORTUNITY_TITLE_EDIT),
        _keys(CrmPage.OPPORTUNITY_TITLE_EDIT, EXAMPLES_ROW["opportunity"]),
        _find(CrmPage.EXPECTED_REVENUE_EDIT),
        _clear(CrmPage.EXPECTED_REVENUE_EDIT),
        _find(CrmPage.EXPECTED_REVENUE_EDIT),
        _keys(CrmPage.EXPECTED_REVENUE_EDIT, EXAMPLES_ROW["revenue"]),
        _find(CrmPage.PROBABILITY_EDIT),
        _clear(CrmPage.PROBABILITY_EDIT),
        _find(CrmPage.PROBABILITY_EDIT),
        _keys(CrmPage.PROBABILITY_EDIT, EXAMPLES_ROW["probability"]),
    )

    # The single wait is at :73, on the card just clicked - not at the end.
    assert tuple(event.as_entry() for event in run.waits) == (
        _wait(CrmPage.BUTTON_PIPELINE),
    )
    assert run.timeline()[2] == _wait(CrmPage.BUTTON_PIPELINE)
    assert run.timeline()[:4] == (
        _find(CrmPage.BUTTON_PIPELINE),
        _click(CrmPage.BUTTON_PIPELINE),
        _wait(CrmPage.BUTTON_PIPELINE),
        _find(CrmPage.EDIT_BUTTON),
    )
    assert run.printed == ()
    assert run.sleeps == ()


def test_change_information_pattern_keeps_the_apostrophe_and_the_spaced_comma(
    resolve_step: Any,
) -> None:
    """Crm.java:70 - the phrase is transcribed character for character.

    Two oddities carry behaviour: the apostrophe in ``user's``, and a space
    *before* the comma separating the first two parameters.  Normalising either
    would stop the pattern matching ``Crm.feature:18``, which is written the
    same way.
    """
    entry = CENSUS[5]
    match = resolve_step(OUTLINE_STEP_PHRASE)

    assert entry.java_line == 70
    assert match.pattern == entry.pattern
    assert match.pattern == (
        "User can change any user's information like "
        '"{opportunity}" , "{revenue}" and "{probability}"'
    )

    # Character-level facts, stated so a failure says which one broke.
    assert "user's" in match.pattern
    assert '" , "' in match.pattern
    assert '" and "' in match.pattern
    assert match.pattern.count('"') == 6

    # And the feature is written the same way, placeholders aside.
    assert _feature_lines()[17].strip() == (
        "And User can change any user's information like "
        '"<opportunity>" , "<revenue>" and "<probability>"'
    )


# =========================================================================== #
# Definition 7 - Crm.java:83-87
# =========================================================================== #


def test_save_information_waits_before_clicking_save(crm_step: StepHarness) -> None:
    """Crm.java:83-87 - wait 2s on ``probabilityEdit``, *then* click Save.

    The class's one genuine wait-then-click pairing: every other wait follows
    the click it guards.  The order is the source's and is not normalised, so
    the first lookup in the log is the wait's target, not Save.
    """
    run = crm_step.run("User can save information")

    assert run.calls == (
        _find(CrmPage.SAVE_EDIT),
        _click(CrmPage.SAVE_EDIT),
    )
    assert run.timeline() == (
        _wait(CrmPage.PROBABILITY_EDIT),
        _find(CrmPage.SAVE_EDIT),
        _click(CrmPage.SAVE_EDIT),
    )

    # The wait precedes every driver operation of this body, which is what
    # "wait, then click" means once the helper resolves its own locator: the
    # reversed order would put the Save lookup and click ahead of the marker.
    assert run.timeline()[0] == _wait(CrmPage.PROBABILITY_EDIT)
    assert run.waits[0].target is CrmPage.PROBABILITY_EDIT
    assert run.waits[0].target != CrmPage.SAVE_EDIT
    assert run.printed == ()


# =========================================================================== #
# Definition 8 - Crm.java:89-104
# =========================================================================== #


def test_verify_information_waits_on_a_different_element(
    crm_step: StepHarness,
) -> None:
    """Crm.java:89-104 - click the sidebar, wait on the *card*, expect ``"Test2"``.

    ``:91`` clicks ``pipelineSideButton`` and ``:92`` waits on
    ``buttonPipeline`` - a different element, and the parity detail of this
    method.  It reads like a copy-paste slip and is preserved: waiting on the
    sidebar entry would wait on something already known to be present and stop
    waiting for the card the next read depends on.
    """
    crm_step.driver.set_text(CrmPage.FIND_TITLE_TEST, EDITED_TITLE_TEXT)

    run = crm_step.run("User can verify the information")

    assert run.calls == (
        _find(CrmPage.PIPELINE_SIDE_BUTTON),
        _click(CrmPage.PIPELINE_SIDE_BUTTON),
        _find(CrmPage.FIND_TITLE_TEST),
        _text(CrmPage.FIND_TITLE_TEST),
    )
    assert run.timeline() == (
        _find(CrmPage.PIPELINE_SIDE_BUTTON),
        _click(CrmPage.PIPELINE_SIDE_BUTTON),
        _wait(CrmPage.BUTTON_PIPELINE),
        _find(CrmPage.FIND_TITLE_TEST),
        _text(CrmPage.FIND_TITLE_TEST),
    )

    # The waited-on element is emphatically not the clicked one - and the
    # constant is BUTTON_PIPELINE (CrmP.java:43) rather than its twin
    # PROGRESS_PIPELINE (CrmP.java:64), which carries the same selector.
    assert run.waits[0].target is CrmPage.BUTTON_PIPELINE
    assert run.waits[0].constant == "BUTTON_PIPELINE"
    assert run.waits[0].target != CrmPage.PIPELINE_SIDE_BUTTON

    assert run.printed == (), (
        "Crm.java:97-98's two prints are not reproduced (F04)"
    )


# =========================================================================== #
# Definition 9 - Crm.java:106-119
# =========================================================================== #


def test_drag_and_drop_performs_the_java_chain_then_sleeps(
    crm_step: StepHarness,
) -> None:
    """Crm.java:106-119 - the exact chain, then a separate fixed 2s delay.

    ``clickAndHold(progressPipeline) -> pause -> moveToElement(progressPipeline2)
    -> pause -> release -> perform`` (``:110-115``), and then
    ``Thread.sleep(2000)`` at ``:117`` - *after* ``perform``, and not a chain
    pause.  Both pauses are 2 seconds because Java's ``Actions.pause`` takes
    milliseconds while Python's takes seconds; transcribed verbatim each would
    be over half an hour and the scenario would hang instead of failing.
    """
    run = crm_step.run("User can drag and drop the pipeline")

    # The two cards are resolved once each, and nothing else touches them.
    assert run.calls == (
        _find(CrmPage.PROGRESS_PIPELINE),
        _find(CrmPage.PROGRESS_PIPELINE2),
    )

    assert run.timeline() == (
        ("action_chain", ()),
        _find(CrmPage.PROGRESS_PIPELINE),
        ("chain", "click_and_hold", (CrmPage.PROGRESS_PIPELINE,)),
        ("chain", "pause", (CHAIN_PAUSE_SECONDS,)),
        _find(CrmPage.PROGRESS_PIPELINE2),
        ("chain", "move_to_element", (CrmPage.PROGRESS_PIPELINE2,)),
        ("chain", "pause", (CHAIN_PAUSE_SECONDS,)),
        ("chain", "release", ()),
        ("chain", "perform", ()),
        ("sleep", FIXED_SLEEP_SECONDS),
    )

    # The chain is built over this worker's session, not one the step chose:
    # ``action_chain()`` takes no argument, exactly as ``new
    # Actions(Driver.getDriver())`` reads for its own lifecycle owner.
    assert len(run.chain_factory_calls) == 1
    assert run.chain_factory_calls[0].args == ()
    assert run.chain_factory_calls[0].kwargs == ()

    # The chain's methods, in order, on one chain.
    assert len(run.chains) == 1
    assert tuple(event.name for event in run.chains[0]) == CHAIN_METHODS_IN_ORDER

    # Two chain pauses and one fixed delay: different mechanisms, and the
    # delay is the one that is not part of the chain.
    assert sum(1 for event in run.chains[0] if event.name == "pause") == 2
    assert run.sleep_seam is not None, (
        "features/steps/crm_steps.py exposes no fixed-delay seam, so "
        "Crm.java:117's Thread.sleep(2000) cannot be observed"
    )
    assert run.sleeps == (FIXED_SLEEP_SECONDS,)
    assert not any(event.name == "sleep" for event in run.chains[0])

    # ... and the delay comes last, after perform() has flushed the chain.
    assert run.timeline()[-1] == ("sleep", FIXED_SLEEP_SECONDS)
    assert run.timeline()[-2] == ("chain", "perform", ())
    assert run.printed == ()
    assert run.waits == ()


def test_drag_and_drop_builds_a_fresh_chain_for_every_call(
    crm_step: StepHarness,
) -> None:
    """Crm.java:108 - ``new Actions(...)`` per call, never a shared builder.

    An ``ActionChains`` accumulates queued actions until ``perform()`` flushes
    them, so a cached builder would replay the previous scenario's drag inside
    the next one - a failure that would look like a flaky browser.  Two runs
    must therefore produce two chains, each carrying the six calls once.
    """
    first = crm_step.run("User can drag and drop the pipeline")
    second = crm_step.run("User can drag and drop the pipeline")

    assert len(first.chain_factory_calls) == 1
    assert len(second.chain_factory_calls) == 1

    for run in (first, second):
        assert len(run.chains) == 1
        assert tuple(event.name for event in run.chains[0]) == CHAIN_METHODS_IN_ORDER
        assert run.sleeps == (FIXED_SLEEP_SECONDS,)

    # The second run received a builder of its own - a different ordinal, and
    # therefore a different object - so nothing queued by the first run could
    # have been replayed inside it.  Six calls on each, never twelve on one.
    assert first.chain_factory_calls[0].chain == 0
    assert second.chain_factory_calls[0].chain == 1
    assert second.chains[0][0].chain != first.chains[0][0].chain
    assert len(first.chains[0]) == len(second.chains[0]) == len(CHAIN_METHODS_IN_ORDER)


def test_the_module_has_one_fixed_delay_and_it_follows_perform() -> None:
    """Crm.java:117 - one ``Thread.sleep``, in the drag step, after ``perform``.

    Asserted on the source as well as at run time: the delay has to stay a
    fixed delay at its own call site rather than becoming an explicit wait
    (AAP 0.4.1), and it has to stay *after* the chain is flushed, because it
    exists to let Odoo re-render the dropped card.
    """
    assert _sleep_sites() == (
        ("user_can_drag_and_drop_the_pipeline", FIXED_SLEEP_SECONDS),
    )

    perform_at = _statement_index(
        "user_can_drag_and_drop_the_pipeline",
        lambda node: isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "perform",
    )
    sleep_at = _statement_index("user_can_drag_and_drop_the_pipeline", _is_sleep_call)

    assert perform_at >= 0
    assert sleep_at > perform_at


# =========================================================================== #
# Definition 10 - Crm.java:121-130
# =========================================================================== #


def test_new_changes_in_progress_reads_the_second_column(
    crm_step: StepHarness,
) -> None:
    """Crm.java:121-130 - the dragged card's title in column two is ``"test"``.

    ``TEST_VERIFY`` reaches that title through a descendant step
    (``//div[2]``) where ``FIND_TITLE_TEST`` uses a child step (``/div[2]``);
    the difference is the source's and both names are kept.
    """
    crm_step.driver.set_text(CrmPage.TEST_VERIFY, TITLE_TEXT)

    run = crm_step.run("User can see the new changes in progress")

    assert run.calls == (
        _find(CrmPage.TEST_VERIFY),
        _text(CrmPage.TEST_VERIFY),
    )
    assert run.printed == (), (
        "Crm.java:126-127's two prints are not reproduced (F04)"
    )
    assert CrmPage.TEST_VERIFY != CrmPage.FIND_TITLE_TEST
    assert run.waits == ()


# =========================================================================== #
# Definition 11 - Crm.java:132-143
# =========================================================================== #


def test_register_new_customer_creates_then_searches(crm_step: StepHarness) -> None:
    """Crm.java:132-143 - three waits, two keyboard calls, no wait at the end.

    In order: open Customers (``:134``) and wait (``:135``); Create (``:136``)
    and wait (``:137``); ``"Test"`` and Enter into the name field (``:138``);
    confirm (``:139``) and wait (``:140``); then ``"aa"`` and Enter into the
    search input (``:141``) with **no** wait after it.

    Three of this step's five accessors are twins of names used earlier -
    ``CREATE_CUSTOMER``, ``CREATE_CUSTOMER_BUTTON`` and ``INPUT_NAME`` - so the
    log below is byte-identical whichever twin were used; the source-shape test
    is what pins the names ``:136``, ``:139`` and ``:138`` actually write.
    """
    run = crm_step.run("User can register new customer")

    assert run.calls == (
        _find(CrmPage.CUSTOMER_SIDE_BUTTON),
        _click(CrmPage.CUSTOMER_SIDE_BUTTON),
        _find(CrmPage.CREATE_CUSTOMER),
        _click(CrmPage.CREATE_CUSTOMER),
        _find(CrmPage.INPUT_NAME),
        _keys(CrmPage.INPUT_NAME, CUSTOMER_NAME_TEXT),
        _find(CrmPage.CREATE_CUSTOMER_BUTTON),
        _click(CrmPage.CREATE_CUSTOMER_BUTTON),
        _find(CrmPage.SEARCHING_TEXT),
        _keys(CrmPage.SEARCHING_TEXT, SEARCH_TEXT),
    )
    assert run.timeline() == (
        _find(CrmPage.CUSTOMER_SIDE_BUTTON),
        _click(CrmPage.CUSTOMER_SIDE_BUTTON),
        _wait(CrmPage.CUSTOMER_SIDE_BUTTON),
        _find(CrmPage.CREATE_CUSTOMER),
        _click(CrmPage.CREATE_CUSTOMER),
        _wait(CrmPage.CREATE_CUSTOMER),
        _find(CrmPage.INPUT_NAME),
        _keys(CrmPage.INPUT_NAME, CUSTOMER_NAME_TEXT),
        _find(CrmPage.CREATE_CUSTOMER_BUTTON),
        _click(CrmPage.CREATE_CUSTOMER_BUTTON),
        _wait(CrmPage.CREATE_CUSTOMER_BUTTON),
        _find(CrmPage.SEARCHING_TEXT),
        _keys(CrmPage.SEARCHING_TEXT, SEARCH_TEXT),
    )

    # The last operation is the search keystroke: nothing waits after it.
    assert run.timeline()[-1] == _keys(CrmPage.SEARCHING_TEXT, SEARCH_TEXT)
    assert len(run.waits) == 3
    assert run.printed == ()


# =========================================================================== #
# Definition 12 - Crm.java:145-153
# =========================================================================== #


def test_print_profile_waits_on_the_due_payment_entry(crm_step: StepHarness) -> None:
    """Crm.java:145-153 - the second wait guards an element not yet clicked.

    ``:149`` clicks ``printButton`` and ``:150`` waits on
    ``duePaymentButton``, the menu entry that click reveals and which ``:151``
    then clicks - the feature's last interaction.  Waiting on ``PRINT_BUTTON``
    instead would drop the only guard on that final click.  This step asserts
    nothing and prints nothing, exactly as the Java method does.
    """
    run = crm_step.run("User can print the profile")

    assert run.calls == (
        _find(CrmPage.NAME_CUSTOMER),
        _click(CrmPage.NAME_CUSTOMER),
        _find(CrmPage.PRINT_BUTTON),
        _click(CrmPage.PRINT_BUTTON),
        _find(CrmPage.DUE_PAYMENT_BUTTON),
        _click(CrmPage.DUE_PAYMENT_BUTTON),
    )
    assert run.timeline() == (
        _find(CrmPage.NAME_CUSTOMER),
        _click(CrmPage.NAME_CUSTOMER),
        _wait(CrmPage.NAME_CUSTOMER),
        _find(CrmPage.PRINT_BUTTON),
        _click(CrmPage.PRINT_BUTTON),
        _wait(CrmPage.DUE_PAYMENT_BUTTON),
        _find(CrmPage.DUE_PAYMENT_BUTTON),
        _click(CrmPage.DUE_PAYMENT_BUTTON),
    )

    # The second wait guards the entry the previous click revealed, and it sits
    # between that click and the final click of the feature.
    assert run.timeline().index(_wait(CrmPage.DUE_PAYMENT_BUTTON)) < (
        run.timeline().index(_click(CrmPage.DUE_PAYMENT_BUTTON))
    )

    assert tuple(event.target for event in run.waits) == (
        CrmPage.NAME_CUSTOMER,
        CrmPage.DUE_PAYMENT_BUTTON,
    )
    assert tuple(event.constant for event in run.waits) == (
        "NAME_CUSTOMER",
        "DUE_PAYMENT_BUTTON",
    )
    assert run.printed == ()
    assert run.sleeps == ()


# =========================================================================== #
# Whole-class totals
#
# The counts AAP 0.4.1 fixes for this class - eleven 2-second waits, seven
# keyboard sites, eight printed lines, one action chain and one fixed delay -
# measured by running all twelve definitions once in Crm.java:21-145 order.
# =========================================================================== #


def _run_every_definition(crm_step: StepHarness) -> tuple[StepRun, ...]:
    """Run all twelve definitions in ``Crm.java:21-145`` order, and return them.

    The page's answers are programmed first so the four assertions pass:
    ``81`` for the column total (``81 + 8 == 89``) and ``"test"`` for the two
    titles compared against it.  ``FIND_TITLE_TEST`` is re-programmed to
    ``"Test2"`` immediately before *User can verify the information*, because
    ``Crm.java`` reads that one element against two different expected values -
    ``"test"`` at ``:61`` and ``"Test2"`` at ``:95``.
    """
    driver = crm_step.driver
    driver.set_text(CrmPage.TOTAL_PRICE, PASSING_TOTAL_TEXT)
    driver.set_text(CrmPage.FIND_TITLE_TEST, TITLE_TEXT)
    driver.set_text(CrmPage.TEST_VERIFY, TITLE_TEXT)

    runs: list[StepRun] = []

    for entry in CENSUS:
        if entry.function == "user_can_verify_the_information":
            driver.set_text(CrmPage.FIND_TITLE_TEST, EDITED_TITLE_TEXT)

        runs.append(crm_step.run(entry.concrete_phrase))

    return tuple(runs)


def test_the_class_waits_eleven_times_and_always_for_two_seconds(
    crm_step: StepHarness,
) -> None:
    """Crm.java:18 - one 2-second wait, used at eleven call sites.

    ``Crm.java`` builds a single ``WebDriverWait`` with a 2-second timeout as a
    field and calls it at ``:24``, :30, :43, :73, :85, :92, :135, :137, :140,
    :148 and :150.  The port passes that literal at each site rather than
    hiding it behind a default, because the per-class timeouts across the suite
    differ - 3s, 4s and 20s elsewhere.
    """
    runs = _run_every_definition(crm_step)
    waits = [event for run in runs for event in run.waits]

    assert len(waits) == len(WAIT_TARGETS_IN_ORDER) == 11
    assert tuple(event.target for event in waits) == WAIT_TARGETS_IN_ORDER
    assert {event.timeout for event in waits} == {WAIT_TIMEOUT}

    # Each site's first argument *is* the page's upper-case locator constant,
    # not an equal tuple and not an element resolved before the call: four of
    # CrmP.java's selectors are declared twice, so equality alone would accept
    # the wrong twin, and a pre-resolved element would be looked up under
    # driver.py's 10-second implicit wait instead of Crm.java:18's 2 seconds.
    assert all(
        event.target is expected
        for event, expected in zip(waits, WAIT_TARGETS_IN_ORDER, strict=True)
    )
    assert tuple(event.constant for event in waits) == tuple(
        _locator_constant_name(locator) for locator in WAIT_TARGETS_IN_ORDER
    )

    # Java's condition is ExpectedConditions.visibilityOf at every one of the
    # eleven sites, so each has to go through a *visibility* helper - a switch
    # to a presence or clickability wait would be a different condition and
    # fails here.
    helpers = {event.helper for event in waits}

    assert helpers, "no wait helper call was recorded for any of the eleven sites"
    assert all("visible" in helper for helper in helpers), helpers


def test_the_class_makes_seven_single_call_keyboard_sites(
    crm_step: StepHarness,
) -> None:
    """Crm.java:36, :40, :76, :78, :80, :138, :141 - seven ``sendKeys`` sites.

    Java concatenates ``literal + Keys.ENTER`` into one argument, so each site
    is **one** keyboard call carrying both - selenium's ``keys_to_typing``
    flattens the arguments into a single character stream either way, and
    splitting it in two would change the call granularity the browser sees.
    """
    runs = _run_every_definition(crm_step)
    keyboard = [
        arguments
        for run in runs
        for operation, arguments in run.calls
        if operation == "element.send_keys"
    ]

    assert len(keyboard) == len(KEY_SITES_IN_ORDER) == 7
    assert tuple(keyboard) == tuple(
        (locator, text, Keys.ENTER) for locator, text in KEY_SITES_IN_ORDER
    )

    # Every site carries exactly the literal and one key, in that order.
    for arguments in keyboard:
        assert len(arguments) == 3
        assert arguments[-1] == Keys.ENTER


def test_no_definition_writes_anything_to_standard_output(
    crm_step: StepHarness,
) -> None:
    """Crm.java:51-52, :63-64, :97-98, :126-127 - eight prints, none reproduced.

    The whole-module invariant, and the strongest form of it: all twelve
    definitions are driven in Java order against a programmed page, and not one
    line reaches standard output.  ``Crm.java`` printed a parsed column total
    and three live pipeline card titles beside their expected literals, and
    reproducing that put live customer and pricing data into durable worker and
    Jenkins logs, because ``app/services/test_run_service.py`` relays every
    worker stdout line into the parent logger (CWE-532/359, the security
    review's F04).  Nothing is routed elsewhere in their place either - no
    logger call, no attachment, no file - which
    :func:`test_no_definition_calls_print` and
    :func:`test_the_step_module_contains_no_print_call_at_all` check at the
    source level.

    No parity is lost by asserting silence here: diagnostic stdout is in
    neither AAP 0.1.2's list of what the port must not change nor AAP 0.4.1's
    enumeration of what each step body must reproduce, and every item those two
    *do* cover is asserted by the per-definition tests above, which pass
    unchanged.
    """
    runs = _run_every_definition(crm_step)
    printed = tuple(line for run in runs for line in run.printed)

    assert printed == (), (
        f"the class wrote {printed!r} to standard output; Crm.java's eight "
        f"System.out.println calls are deliberately not reproduced, and the "
        f"worker's stdout is relayed into the Jenkins console"
    )
    assert len(runs) == DEFINITION_COUNT

    # Stated per definition as well, so a failure names the body that spoke
    # rather than only the total.
    assert {run.match.func.__name__: run.printed for run in runs} == {
        entry.function: () for entry in CENSUS
    }


def test_the_class_performs_exactly_one_fixed_delay_and_one_chain(
    crm_step: StepHarness,
) -> None:
    """Crm.java:108-117 - one action chain and one ``Thread.sleep`` in twelve steps.

    ``Crm.java`` is one of the five classes carrying a fixed delay and accounts
    for exactly one of the suite's seventeen; no other step in the class sleeps
    or builds a chain.
    """
    runs = _run_every_definition(crm_step)

    assert tuple(delay for run in runs for delay in run.sleeps) == (
        FIXED_SLEEP_SECONDS,
    )
    assert sum(len(run.chain_factory_calls) for run in runs) == 1
    assert sum(len(run.chains) for run in runs) == 1


@pytest.mark.parametrize("function", SILENT_FUNCTIONS)
def test_the_eight_silent_definitions_neither_print_nor_assert(
    crm_step: StepHarness, function: str
) -> None:
    """Crm.java:21, :27, :34, :70, :83, :106, :132, :145 print and assert nothing.

    Run against a page that answers nothing at all, each completes without
    raising and without emitting a line.  The absence of an assertion is
    behaviour: these eight steps drive the browser, and the four that check
    anything do so on their own lines.  The absence of output is not peculiar
    to these eight any more - no definition in the module prints - but it is
    still asserted per body here, which is what catches a print added to one of
    them.
    """
    entry = next(item for item in CENSUS if item.function == function)

    run = crm_step.run(entry.concrete_phrase)

    assert run.printed == ()
    assert run.error is None
    assert _print_count(function) == 0
    assert _assert_shapes(function) == ()


@pytest.mark.parametrize(
    ("function", "locator", "passing_text", "failing_text"),
    [
        (
            "user_can_see_the_total_price",  # Crm.java:51-52
            CrmPage.TOTAL_PRICE,
            PASSING_TOTAL_TEXT,
            "10",
        ),
        (
            "user_can_see_new_pipeline",  # Crm.java:63-64
            CrmPage.FIND_TITLE_TEST,
            TITLE_TEXT,
            EDITED_TITLE_TEXT,
        ),
        (
            "user_can_verify_the_information",  # Crm.java:97-98
            CrmPage.FIND_TITLE_TEST,
            EDITED_TITLE_TEXT,
            TITLE_TEXT,
        ),
        (
            "user_can_see_the_new_changes_in_progress",  # Crm.java:126-127
            CrmPage.TEST_VERIFY,
            TITLE_TEXT,
            EDITED_TITLE_TEXT,
        ),
    ],
    ids=FORMERLY_PRINTING_FUNCTIONS,
)
def test_the_four_formerly_printing_definitions_are_silent(
    crm_step: StepHarness,
    function: str,
    locator: tuple[str, str],
    passing_text: str,
    failing_text: str,
) -> None:
    """Crm.java:51-52, :63-64, :97-98, :126-127 - silent on both paths.

    The sensitivity half of the invariant, and the one that would catch a print
    reintroduced where the source had one.  Each of the four bodies is driven
    twice - once with the page programmed so its assertion passes, once so its
    assertion fails - because the value the removed prints carried was read
    live from the system under test and a failing step is exactly where a
    reader would be tempted to print it again (CWE-532/359, F04).  Neither path
    may emit a line, and the source-level count of the body must stay at nought
    so that a print on a branch no test drives is caught as well.
    """
    entry = next(item for item in CENSUS if item.function == function)

    # The four that printed are the four that assert - a property of
    # Crm.java, not of this test's ordering, and the reason each case below
    # has a failing half at all.
    assert tuple(ASSERT_OPERANDS) == FORMERLY_PRINTING_FUNCTIONS
    assert function in FORMERLY_PRINTING_FUNCTIONS

    crm_step.driver.set_text(locator, passing_text)
    passing = crm_step.run(entry.concrete_phrase)

    assert passing.error is None
    assert passing.printed == (), (
        f"{function} wrote {passing.printed!r} on the passing path; "
        f"Crm.java:{entry.java_line}'s method printed two lines and the port "
        f"reproduces neither"
    )

    crm_step.driver.set_text(locator, failing_text)
    failing = crm_step.run(entry.concrete_phrase, expect=AssertionError)

    assert isinstance(failing.error, AssertionError)
    assert failing.printed == (), (
        f"{function} wrote {failing.printed!r} on the failing path, which is "
        f"the path a live SUT value would reach the Jenkins console by"
    )
    assert _print_count(function) == 0


# =========================================================================== #
# Assertions: operand order, absence of a message, and both branches
# =========================================================================== #


def test_the_four_assertions_keep_the_java_operand_order_and_no_message() -> None:
    """Crm.java:54, :66, :100, :129 - four two-argument ``assertEquals`` calls.

    All four are the two-argument form, so JUnit's ``(expected, actual)`` order
    is the source's and there is no message to reproduce (AAP deviation 16:
    Python cannot reproduce JUnit's ``expected:<...> but was:<...>`` framing, so
    the assertion *subject* is the parity, not its formatting).  ``:54`` puts
    the parsed total first - the baseline artifact's ``expected:<8> but
    was:<89>`` is what says so - and the other three put the expected literal
    first.
    """
    for function, operands in ASSERT_OPERANDS.items():
        shapes = _assert_shapes(function)

        assert len(shapes) == 1, (
            f"{function} must make exactly one assertion, as its Java method "
            f"does; found {len(shapes)}"
        )

        left, right, has_message = shapes[0]

        assert (left, right) == operands
        assert has_message is False

    # Four assertions in the class, and only four.
    assert sum(len(_assert_shapes(entry.function)) for entry in CENSUS) == 4
    assert len(ASSERT_OPERANDS) == 4


@pytest.mark.parametrize(
    ("phrase", "locator", "expected_text", "wrong_text", "java_line"),
    [
        (
            "User can see new pipeline",
            CrmPage.FIND_TITLE_TEST,
            TITLE_TEXT,
            "Test2",
            66,
        ),
        (
            "User can verify the information",
            CrmPage.FIND_TITLE_TEST,
            EDITED_TITLE_TEXT,
            "test",
            100,
        ),
        (
            "User can see the new changes in progress",
            CrmPage.TEST_VERIFY,
            TITLE_TEXT,
            "Test2",
            129,
        ),
    ],
    ids=["see_new_pipeline", "verify_information", "new_changes_in_progress"],
)
def test_each_title_assertion_fails_when_the_page_disagrees(
    crm_step: StepHarness,
    phrase: str,
    locator: tuple[str, str],
    expected_text: str,
    wrong_text: str,
    java_line: int,
) -> None:
    """Crm.java:66, :100 and :129 fail when the card's title is anything else.

    The wrong values are each other's expected values, which is the point of
    keeping the three literals distinct: a step that compared against the wrong
    one of them would pass its own case and fail here.  The failing path emits
    nothing: ``:63-64``, ``:97-98`` and ``:126-127`` printed the live card
    title beside its expected literal and the port reproduces none of them
    (F04), so the ``AssertionError`` is the whole of what a failing comparison
    produces.
    """
    # The quoted assertEquals line belongs to this phrase's own method, and
    # that method makes exactly one assertion - so the failure below is the
    # Java line named in the docstring and not some other comparison.
    entry = next(item for item in CENSUS if item.concrete_phrase == phrase)
    lower, upper = _java_line_range(entry)

    assert lower < java_line < upper
    assert len(_assert_shapes(entry.function)) == 1

    crm_step.driver.set_text(locator, wrong_text)

    run = crm_step.run(phrase, expect=AssertionError)

    assert isinstance(run.error, AssertionError)
    assert run.printed == (), (
        f"the failing comparison of {phrase!r} wrote {run.printed!r} to "
        f"standard output; the live title {wrong_text!r} must not reach the "
        f"worker or Jenkins log"
    )
    assert expected_text != wrong_text


# =========================================================================== #
# Source-level parity: accessor names, and how each keyboard call is written
# =========================================================================== #


@pytest.mark.parametrize("entry", CENSUS, ids=lambda entry: entry.java_method)
def test_every_call_site_uses_the_locator_name_its_java_line_uses(
    entry: Definition,
) -> None:
    """Each step touches the named locators of its Java lines, in that order.

    The name-aware half of the locator obligation.  Four selectors are declared
    twice in ``CrmP.java``, so the driver log cannot tell ``CREATE_BUTTON``
    (:16) from ``CREATE_CUSTOMER`` (:76), ``CREATE_PIPELINE`` (:34) from
    ``CREATE_CUSTOMER_BUTTON`` (:82), ``OPPORTUNITY_TITLE_EDIT`` (:49) from
    ``INPUT_NAME`` (:79) or ``BUTTON_PIPELINE`` (:43) from ``PROGRESS_PIPELINE``
    (:64); this reads the attribute names out of the step module's AST instead,
    in source order, so a step that used the wrong twin fails even though its
    log would be identical.
    """
    assert _accessor_usages(entry.function) == SOURCE_SHAPES[entry.function]


@pytest.mark.parametrize(
    "function",
    sorted(KEY_CALL_SHAPES),
    ids=lambda function: function,
)
def test_every_keyboard_site_sends_its_text_and_enter_in_one_call(
    function: str,
) -> None:
    """Crm.java:36, :40, :76, :78, :80, :138, :141 - one call, text then ENTER.

    Read from the source so the three sites whose text is a *step parameter*
    are covered as precisely as the four literal ones: the parameter name at
    each site is asserted, which the runtime log - where the bound value has
    already replaced it - cannot show.
    """
    assert _key_call_shapes(function) == KEY_CALL_SHAPES[function]

    for _accessor, key_names, _text in _key_call_shapes(function):
        # Exactly one key per site: Java concatenates one Keys member.
        assert key_names == ("ENTER",)


def test_the_seven_keyboard_sites_are_the_only_ones_in_the_module() -> None:
    """``Crm.java:36``, :40, :76, :78, :80, :138 and :141 - seven ``sendKeys``.

    The seven sites sit in the three methods at ``Crm.java:34``, :70 and :132.
    A keyboard call added to any other definition - or removed from one of the
    three - fails here, which keeps the runtime total of seven honest.
    """
    per_function = {
        entry.function: _key_call_shapes(entry.function) for entry in CENSUS
    }
    populated = {
        function: shapes for function, shapes in per_function.items() if shapes
    }

    assert populated == dict(KEY_CALL_SHAPES)
    assert sum(len(shapes) for shapes in populated.values()) == 7


@pytest.mark.parametrize("entry", CENSUS, ids=lambda entry: entry.java_method)
def test_no_definition_calls_print(entry: Definition) -> None:
    """No ``print`` call in any of the twelve bodies, at any nesting.

    The per-body source-level half of the stdout invariant: ``Crm.java``'s
    eight ``System.out.println`` calls are not reproduced (F04), and this is
    asserted on the syntax tree rather than only at run time so that a print
    inside a branch no scenario happens to take is caught too.
    """
    assert _print_count(entry.function) == 0, (
        f"{entry.function} calls print(); Crm.java:{entry.java_line}'s output "
        f"is deliberately not reproduced, and the worker's stdout is relayed "
        f"into the parent logger and the Jenkins console"
    )


def test_the_step_module_contains_no_print_call_at_all() -> None:
    """``features/steps/crm_steps.py`` holds no ``print`` call, in any scope.

    The whole-file form of the same fact, and the one that closes the gap the
    per-definition census leaves: a print in ``_page``, in a helper added
    later, or at module scope belongs to no census entry and would satisfy
    :func:`test_no_definition_calls_print` while still writing a line into
    every worker and Jenkins log (F04).  Counted over the module's entire
    syntax tree, so the prose in the docstrings - which discusses the removed
    prints by name - cannot satisfy or break it.
    """
    calls = [
        node
        for node in ast.walk(_module_ast())
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "print"
    ]

    assert calls == [], (
        f"crm_steps.py calls print() at line(s) "
        f"{[node.lineno for node in calls]}; the module writes nothing to "
        f"standard output"
    )

    # The named alternatives are absent as well: the finding is about the
    # values reaching a durable log, so rerouting them to a logger, an
    # attachment or a file would not resolve it.
    referenced = {
        node.id for node in ast.walk(_module_ast()) if isinstance(node, ast.Name)
    } | {
        node.attr
        for node in ast.walk(_module_ast())
        if isinstance(node, ast.Attribute)
    }

    for name in (
        "print",
        "logging",
        "logger",
        "getLogger",
        "stdout",
        "stderr",
        "write",
        "open",
    ):
        assert name not in referenced, (
            f"crm_steps.py references {name!r}; the eight removed prints are "
            f"not to be re-routed to another sink - the finding is the values "
            f"reaching a durable record, whichever one it is"
        )


# =========================================================================== #
# Feature fidelity - Crm.feature
# =========================================================================== #


def test_the_feature_is_tagged_smoke_and_titled_for_the_crm_module() -> None:
    """Crm.feature:1-2, and the suite's only ``@Smoke`` tag.

    ``behave.ini``'s ``default_tags`` is ``@Smoke``, carried from
    ``CukesRunner.java:18``, and this is the only feature that declares it - so
    a bare ``run-tests`` selects this feature alone and these twelve steps are
    what CI executes on every build.  That makes the tag behaviour, not
    decoration.
    """
    lines = _feature_lines()

    assert lines[0] == FEATURE_TAG_LINE
    assert lines[1] == FEATURE_HEADER_LINE

    tagged = [
        path.name
        for path in sorted(FEATURES_DIR.glob("*.feature"))
        if any(
            line.strip() == FEATURE_TAG_LINE
            for line in path.read_text(encoding="utf-8").splitlines()
        )
    ]

    assert tagged == [FEATURE_PATH.name]


def test_every_feature_step_resolves_to_exactly_one_definition(
    resolve_step: Any,
) -> None:
    """Crm.feature:7-34 - sixteen step uses, each resolving once.

    ``resolve_step`` raises on an undefined or ambiguous phrase, so this covers
    both.  The Background's *Given User login to test other features* resolves
    into ``session_steps`` - it is declared at ``Session.java:12``, shared by
    every feature needing a logged-in session, and deliberately not redeclared
    here - while every other phrase resolves into ``crm_steps``.  The set of
    ``crm_steps`` functions the feature reaches is exactly the census, so an
    unported definition or a phrase drifting out of the feature fails.
    """
    steps = _feature_steps()

    assert len(steps) == 16

    reached: set[str] = set()

    for line_number, phrase in steps:
        match = resolve_step(phrase)

        if phrase == BACKGROUND_PHRASE:
            assert line_number == 7
            assert match.module_name == SESSION_MODULE_NAME
            continue

        assert match.module_name == STEP_MODULE_NAME, (
            f"Crm.feature:{line_number} resolves into "
            f"{match.module_name!r}, not {STEP_MODULE_NAME!r}"
        )
        reached.add(match.func.__name__)

    assert reached == {entry.function for entry in CENSUS}


def test_the_outline_binds_the_examples_row_to_the_three_parameters(
    resolve_step: Any,
) -> None:
    """Crm.feature:18 and :22-24 - ``Test2`` / ``30`` / ``2``, in that order.

    The single Examples row is what makes ``Crm.java:95``'s expected
    ``"Test2"`` the value the edit step types, so the binding is behaviour: a
    reordered header row would silently type the revenue into the title field.
    """
    lines = _feature_lines()

    assert _table_cells(lines[22]) == EXAMPLES_HEADER
    assert _table_cells(lines[23]) == tuple(
        EXAMPLES_ROW[name] for name in EXAMPLES_HEADER
    )
    assert lines[21].strip() == "Examples: Expected name"

    match = resolve_step(OUTLINE_STEP_PHRASE)

    assert match.kwargs == dict(EXAMPLES_ROW)
    assert match.kwargs["opportunity"] == EDITED_TITLE_TEXT
    assert match.kwargs["revenue"] == "30"
    assert match.kwargs["probability"] == "2"
