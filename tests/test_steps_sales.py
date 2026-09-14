"""Behaviour-parity tests for ``features/steps/sales_steps.py``.

What this module owns
---------------------
AAP 0.4.1's per-module obligation, for one of the ten step modules: drive the
module against a stubbed driver and assert, for every step method of
``Sales.java``, the same observable operations in the same order - the same
locators, the same wait locator and timeout, the same keyboard key, the same
literals, the same assertion subject and message, and the same absence of a
comparison where a Java body makes none.  Suite-wide phrase resolution belongs
to ``tests/test_steps_registration.py``, which does not discharge this.

The authority
-------------
``src/main/java/com/testinium/step_definitions/Sales.java`` at pinned revision
``47e9d697e4a9a85da889f94a846fdf47af28a240``: 99 lines, seven methods declared
at ``:19 :25 :40 :54 :66 :80 :87``, with its page object ``SalesP.java`` -
twenty ``@FindBy`` fields at ``:17-75``, built by ``PageFactory.initElements``
at ``:14``.  Every test below names the Java lines it pins.  The class's four
awkward facts each carry a test of their own, so that losing one fails loudly:
the near-colliding phrases at ``:40`` and ``:80``, the single 4-second
``WebDriverWait`` at ``:17`` serving eight sites
(``:22 :29 :43 :57 :59 :61 :69 :83``), the ``throws InterruptedException`` at
``:20``, ``:26`` and ``:41`` that no body justifies with a sleep, and the one
assertion at ``:37`` - inside a ``@When``, its message without the trailing
space ``LoginSD.java:46`` and ``LogOutSD.java:27`` carry.

What makes this module the awkward one
--------------------------------------
Five facts about ``Sales.java`` shape everything here, and each has a test of
its own so that losing one fails loudly rather than quietly:

``Sales.java:40`` and ``Sales.java:80``
    ``User can create the customer`` and ``User can create new customer`` are
    two **distinct** steps whose phrases nearly collide and whose bodies differ
    by seven statements.  Every definition in the port registers with ``@step``
    (AAP deviation 7), which reaches every Gherkin keyword, so an un-anchored
    pattern would silently run the wrong body -
    :func:`test_the_two_create_customer_phrases_resolve_to_distinct_bodies`
    exists for exactly that risk.

``Sales.java:17``
    One ``WebDriverWait`` of **4 seconds**, used at eight sites.  Four seconds
    is this class's timeout and no other class's, so every one of the eight is
    asserted, individually and again in one ordered sequence.

``Sales.java:20``, ``:26``, ``:41``
    Three of the seven methods declare ``throws InterruptedException`` and
    **not one sleeps**.  That declaration without a delay is a source quirk;
    the absence of any delay is asserted (three ways) and no delay is ever
    added to justify the declaration.

``Sales.java:37``
    The class's **only** assertion, and it sits inside the ``@When`` step
    ``User click customers button`` rather than in a ``@Then``.  Its subject
    order and its message - which, unlike ``LoginSD.java:46`` and
    ``LogOutSD.java:27``, carries **no trailing space** - are pinned
    byte-for-byte.  Per AAP deviation 16 only the subject and the message text
    are parity; JUnit's ``expected:<...> but was:<...>`` formatting is not and
    is not asserted.

``Sales.java:66`` and ``Sales.java:87``
    Two bodies build an *actual* and an *expected* string, print both, and
    compare nothing.  Those steps are proven **assertion-free**: a page value
    that contradicts the literal must not fail them.  What they must still do
    is *read*: ``:72`` reads the kanban heading and ``:91`` the notification
    area, and with no comparison anywhere those two reads are the only way
    either step can fail, so each is asserted through the effect log.  What
    they must still *hold* is the literal each binds - ``:71``'s ``"Lucas"``
    and ``:90``'s ``"The following fields are invalid:"`` - which AAP 0.4.1
    counts as parity under "the same hard-coded literals and expected values";
    a binding is not an output, so it survives the removal of the prints that
    were its only readers, and two tests here pin its value, its position
    ahead of the read, and the fact that nothing consumes it.

Standard output, and why silence is the expectation
---------------------------------------------------
``Sales.java:34-35``, ``:74-75`` and ``:93-94`` print six lines - the Customers
page title, a customer's name read from the kanban heading, the notification
area's text and the literals each is paired with - and the port reproduces none
of them.  Three of the six values are read live from the system under test, and
``app/services/test_run_service.py`` relays every worker stdout line into the
parent logger and from there into the Jenkins console, so each line was a
durable record of live customer data and PII (CWE-532/359, the security
review's F04).  Diagnostic stdout appears in neither AAP 0.1.2's list of what
the port must not change nor AAP 0.4.1's enumeration of what each step body
must reproduce, so silence costs no parity - every other observable operation
of the three bodies is still asserted here, item by item, and so is every
hard-coded value those six lines carried: the title constant of ``:31``
through the assertion that reads it, and the two unread literals of ``:71``
and ``:90`` through the two literal-binding tests below.  What the remediation
removed is the *writing*, not the values.

Every per-step test therefore asserts ``capsys.readouterr().out == ""``, the
whole-flow test asserts the same across all seven bodies, and two source-level
tests close the hole a run-time check leaves: the module's syntax tree must
contain no ``print`` call at any scope, and must name no logger, stream or
file-write sink the removed values could have been re-routed to instead.

How a step body is reached and observed
---------------------------------------
``behave``'s ``load_step_modules`` *execs* the step modules, so they never
appear in ``sys.modules`` and cannot be imported by name.  The only handle on
the module namespace is ``match.func.__globals__``, and the registry is
process-wide, session-scoped state shared with every other step test - so
every substitution here goes through ``monkeypatch.setitem``, which restores
the namespace when the test ends.  Nothing in this module mutates that
namespace permanently.

The collaborators all come from ``tests/conftest.py``: :fixture:`resolve_step`
for the registry, :fixture:`fake_context` for behave's context and
:fixture:`stub_driver` for the recorder both of them share.  Explicit waits are
the one thing the stub cannot answer - the real helpers build a
``WebDriverWait`` around a session the stub is not - so
:func:`install_wait_recorder` substitutes **every** ``wait_*`` name found in the
module namespace and normalises what it records to
``(helper, locator, timeout, from_element, after)``.  That is deliberate
robustness: ``app/automation`` is being changed concurrently, and a change of
wait *call shape* - ``wait_visible_element(element, 4)`` becoming
``wait_visible(LOCATOR, 4)`` - must not be read as a parity break, while a
change of wait *target*, *timeout* or *order* must be.

Each step is therefore asserted through three narrow views rather than one log:
:func:`effect_log` for the effectful operations and their locators,
:func:`wait_sequence` for each wait's target and timeout, and
:func:`wait_positions` for where the waits sat among those operations - which
is what pins ``Sales.java``'s click-*then*-wait order.  The raw driver log is
never asserted whole, because evaluating a page accessor as a wait argument
produces a ``find_element`` entry of its own and one rigid interleaved log
would freeze that call shape into every assertion;
:func:`assert_lookup_invariant` pins the lookup *count* instead, which is the
part of it that is parity.

Standing constraints
--------------------
No browser, no network, no sleep, no writing anywhere, and no test here is
skipped or expected to fail.  ``pytest`` runs with ``--strict-markers`` and
``--strict-config``, so no custom marker is declared; the only marker used is
pytest's own ``parametrize``.  The plaintext data below - the searched name,
the address, the state and its code - is pre-existing fixture data of the
reference suite, carried over verbatim for parity under AAP 0.8 and asserted as
it stands.
"""

from __future__ import annotations

import ast
import functools
import time
from pathlib import Path
from typing import Any, Final, NamedTuple

import pytest

from app.pages import SalesPage

# --------------------------------------------------------------------------- #
# Locations and fixed names
# --------------------------------------------------------------------------- #

#: Repository root, from this file's own position: ``tests/`` -> root.  The
#: same derivation ``tests/conftest.py`` uses, and for the same reason: a unit
#: suite that resolved these paths against the working directory would pass or
#: fail according to where pytest happened to be started.
REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]

#: The module under test.  Read as text for the source-level assertions, and
#: never imported: ``load_step_modules`` execs it, and a second import under a
#: different mechanism would register its seven definitions twice.
STEP_MODULE_PATH: Final[Path] = REPO_ROOT / "features" / "steps" / "sales_steps.py"

#: The feature the seven definitions serve.
FEATURE_PATH: Final[Path] = REPO_ROOT / "features" / "Sales.feature"

#: Value of ``StepMatch.module_name`` for every definition of this module -
#: the stem of the file behave loaded the definition from.
STEP_MODULE_NAME: Final[str] = "sales_steps"

#: The registry bucket ``@step`` populates, and the only bucket any definition
#: of this port may appear in (AAP deviation 7).
STEP_BUCKET: Final[str] = "step"

#: Every bucket behave keeps.  All four are scanned so that a definition
#: accidentally bound to a keyword - ``@when`` instead of ``@step`` - is found
#: and named rather than silently missed.  One of the seven Java definitions is
#: declared ``@And``, for which behave has no decorator at all, which is part
#: of why ``@step`` is the port's answer.
STEP_BUCKETS: Final[tuple[str, ...]] = ("step", "given", "when", "then")

#: ``Sales.java:17``'s ``new WebDriverWait(Driver.getDriver(), 4)``.  Four
#: seconds is this class's timeout; the reference uses 2, 3, 4 and 20 across
#: its step classes and ``app/automation/waits.py`` declares no default, so the
#: value has to arrive from each call site and each call site is checked.
WAIT_TIMEOUT: Final[int] = 4

#: ``Keys.ENTER`` as the character selenium sends - U+E007 of the Unicode
#: private-use area, per the WebDriver specification's key table.  Written as
#: the literal so that this module needs no selenium import of its own; the
#: port's single keyboard site is asserted against it.
ENTER: Final[str] = "\ue007"

#: The wait helpers of ``app/automation/waits.py`` that reproduce
#: ``ExpectedConditions.visibilityOf`` - the only predicate ``Sales.java``
#: waits on, at all eight of its wait sites (``:22 :29 :43 :57 :59 :61 :69
#: :83``).  ``wait_visible`` and ``wait_visible_element`` both wrap
#: ``visibility_of_element_located``, so both take the locator and resolve it
#: inside the predicate; any other helper would change the predicate and is
#: rejected.
VISIBILITY_WAIT_HELPERS: Final[frozenset[str]] = frozenset(
    {"wait_visible", "wait_visible_element"}
)

#: Everything ``features/steps/sales_steps.py`` may import from
#: ``app.automation``, given ``Sales.java``'s own imports: one keyboard helper
#: for ``:68``'s ``Keys.ENTER`` and one visibility wait for ``:17``'s
#: ``WebDriverWait``.  ``By`` is absent because no body here builds a locator,
#: ``action_chain`` because the class uses no ``Actions``, and the driver
#: lifecycle because ``features/environment.py`` owns it (AAP 0.3.3).
PERMITTED_AUTOMATION_NAMES: Final[frozenset[str]] = (
    frozenset({"press_keys"}) | VISIBILITY_WAIT_HELPERS
)

#: Page-object locator constants that no step class of the reference reaches -
#: zero references across the eleven classes of
#: ``src/main/java/com/testinium/step_definitions/``.  ``SalesP.java`` declares
#: them at ``:62`` (``warningButton``), ``:68`` (``allCustomers``), ``:71``
#: (``link``) and ``:74`` (``details``); locator fidelity keeps them, but
#: wiring one up here would invent behaviour the reference does not have.
#: ``SalesP.java:69`` types ``allCustomers`` as a ``List<WebElement>``, which
#: makes ``ALL_CUSTOMERS`` the port's only plural locator.
UNREFERENCED_LOCATORS: Final[tuple[str, ...]] = (
    "WARNING_BUTTON",
    "ALL_CUSTOMERS",
    "LINK",
    "DETAILS",
)

#: The lower-case accessors ``BasePage`` installs for those four constants.
#: Checked at the source level as well as at runtime, because a step that
#: reaches one of them would otherwise only be caught by a scenario nobody
#: runs.
UNREFERENCED_ACCESSORS: Final[tuple[str, ...]] = (
    "warning_button",
    "all_customers",
    "link",
    "details",
)


class Definition(NamedTuple):
    """One of the seven step definitions ``Sales.java:19-96`` declares."""

    #: The text inside the ``@step`` decorator - identical to the Java
    #: annotation's text, with ``{string}`` rewritten as behave's ``{name}``
    #: field for the one parameterized phrase.
    pattern: str

    #: The port's function name.
    function: str

    #: The line of ``Sales.java`` carrying the annotation - one of
    #: ``:19 :25 :40 :54 :66 :80 :87``.
    java_line: int


#: The seven definitions in Java declaration order.  ``Sales.java`` declares
#: four ``@When`` (``:19 :25 :40 :54``), two ``@Then`` (``:66 :87``) and one
#: ``@And`` (``:80``); the port registers all seven with ``@step``, so the Java
#: keyword survives only as a comment and resolution never consults it.
DEFINITIONS: Final[tuple[Definition, ...]] = (
    Definition("User click on the sales dashboard", "user_click_on_the_sales_dashboard", 19),
    Definition("User click customers button", "user_click_customers_button", 25),
    Definition("User can create the customer", "user_can_create_the_customer", 40),
    Definition("User can save the customer", "user_can_save_the_customer", 54),
    Definition(
        'User can find his name "{name}" from search bar',
        "user_can_find_his_name_from_search_bar",
        66,
    ),
    Definition("User can create new customer", "user_can_create_new_customer", 80),
    Definition("User can get the error", "user_can_get_the_error", 87),
)

#: Concrete phrase per definition, in the same order, as a feature file writes
#: it.  ``Sales.feature:17`` supplies ``"Lucas"`` to the parameterized phrase;
#: the behavioural tests below use their own distinctive values instead, so a
#: body that ignored its parameter and hard-coded the literal could not pass.
PHRASE_DASHBOARD: Final[str] = "User click on the sales dashboard"
PHRASE_CUSTOMERS: Final[str] = "User click customers button"
PHRASE_CREATE_THE: Final[str] = "User can create the customer"
PHRASE_SAVE: Final[str] = "User can save the customer"
PHRASE_SEARCH: Final[str] = 'User can find his name "Lucas" from search bar'
PHRASE_CREATE_NEW: Final[str] = "User can create new customer"
PHRASE_ERROR: Final[str] = "User can get the error"

#: The full flow, in Java declaration order, for the whole-module assertions.
FLOW_PHRASES: Final[tuple[str, ...]] = (
    PHRASE_DASHBOARD,
    PHRASE_CUSTOMERS,
    PHRASE_CREATE_THE,
    PHRASE_SAVE,
    PHRASE_SEARCH,
    PHRASE_CREATE_NEW,
    PHRASE_ERROR,
)

#: The live page title that makes ``Sales.java:37``'s comparison succeed.  The
#: step prefixes it with ``"Customers - "`` (``:32``) and weighs the result
#: against the constant ``"Customers - Odoo"`` (``:31``), so the only title
#: that passes is the bare product name.
PASSING_TITLE: Final[str] = "Odoo"

#: ``Sales.java:37``'s message, byte for byte.  No trailing space: the JUnit
#: calls at ``LoginSD.java:46`` and ``LogOutSD.java:27`` carry one and this one
#: does not, and normalising the three would edit the source.
TITLE_ASSERTION_MESSAGE: Final[str] = "The title is not same as the expected!"

#: ``Sales.java:71``'s ``String actualName = "Lucas";`` - the customer name the
#: search step binds and compares with nothing.  AAP 0.4.1 counts "the same
#: hard-coded literals and expected values" as parity per module, so the
#: literal is asserted here byte for byte even though no behaviour reads it:
#: what the source *binds* is as much a parity fact as what it compares.  It is
#: deliberately not the value the behavioural tests search for - those use
#: their own distinctive name, so a body typing the literal instead of its
#: parameter still fails.
SEARCH_NAME_LITERAL: Final[str] = "Lucas"

#: ``Sales.java:90``'s ``String actualWarning = "The following fields are
#: invalid:";``, trailing colon included, bound by the warning step and
#: likewise compared with nothing.  Asserted for the same reason as
#: :data:`SEARCH_NAME_LITERAL`, and byte for byte because the colon is the only
#: thing distinguishing it from the sentence Odoo renders.
WARNING_LITERAL: Final[str] = "The following fields are invalid:"

#: The step bodies that bind those two literals, against the page accessor each
#: one dereferences immediately afterwards.  ``Sales.java`` binds the literal
#: *first* and reads *second* (``:71`` before ``:72``, ``:90`` before ``:91``),
#: which is the statement order the two tests below pin.
LITERAL_BINDINGS: Final[tuple[tuple[str, str, str], ...]] = (
    ("user_can_find_his_name_from_search_bar", SEARCH_NAME_LITERAL, "name_check"),
    ("user_can_get_the_error", WARNING_LITERAL, "warning"),
)

#: The assignment target a body may bind an unread literal to.  ``Sales.java``
#: uses a named local (``actualName``, ``actualWarning``) and reads it only
#: from a print this port does not reproduce; a named local with no reader is a
#: ``pyflakes`` F841, and this tree carries none, so the port binds the literal
#: to the discard target instead.  That is the one shape both faithful to the
#: source - a binding, at the source's statement position - and clean under the
#: linter, and pinning it here is what stops the literal quietly moving into a
#: local something later reads, or into a value that reaches an output sink.
DISCARD_TARGET: Final[str] = "_"


# --------------------------------------------------------------------------- #
# Coverage bookkeeping
#
# AAP 0.4.1: "the module test enumerates the Java class's methods so an
# omission fails rather than passes silently".  Each per-step behavioural test
# records the pattern it exercised and
# test_every_java_definition_has_a_behaviour_test compares the record against
# all seven.  Deleting a per-step test therefore fails that test rather than
# quietly shrinking the suite.
#
# Only the per-step tests record.  The whole-flow tests run all seven bodies
# and deliberately record nothing, because a set they filled would mask exactly
# the omission this bookkeeping exists to catch.
# --------------------------------------------------------------------------- #

#: Patterns whose body a per-step test has driven, filled during the run.
_COVERED_PATTERNS: set[str] = set()

#: Names of the tests in this module that pytest actually executed.  The
#: run-time record alone cannot tell "this definition has no test" - a parity
#: gap - from "this test was not selected", which a ``-k`` run, a node id and a
#: distributed run all make routine.  Reconciling the two in the final gate
#: keeps the enumeration strict without turning a partial selection into a
#: false gap report.
_EXECUTED_TESTS: set[str] = set()


def cover(match: Any) -> Any:
    """Record that *match*'s body was exercised, and hand the match back.

    :param match: The ``StepMatch`` a per-step test is about to run.
    :returns: *match* unchanged, so a test reads
        ``match = cover(resolve_step(PHRASE))``.
    """
    _COVERED_PATTERNS.add(match.pattern)
    return match


@pytest.fixture(autouse=True)
def _record_executed_test(request: pytest.FixtureRequest) -> None:
    """Record every test of this module that pytest runs.

    Module-local and autouse, so it observes this module's tests and no
    others.  Both the node name and, for a parameterized test, its original
    function name are recorded, so a declaration in :data:`PARITY_TESTS` can
    name the function as it is written in the source.

    :param request: pytest's request object, for the running node's name.
    :returns: ``None``.
    """
    _EXECUTED_TESTS.add(request.node.name)
    original = getattr(request.node, "originalname", None)

    if original:
        _EXECUTED_TESTS.add(original)


# --------------------------------------------------------------------------- #
# The wait seam
# --------------------------------------------------------------------------- #

#: Sentinel distinguishing "no timeout passed" from a passed ``None``.
_MISSING: Final[Any] = object()


class WaitCall(NamedTuple):
    """One explicit wait, as the call site made it.

    The parity facts are *what* was waited on, *for how long* and *in what
    order*: ``Sales.java:22`` and its seven siblings each apply
    ``ExpectedConditions.visibilityOf`` to a ``PageFactory`` proxy, which
    re-located the element inside the predicate on every poll
    (``SalesP.java:14``), so the wait's own 4 seconds governed the lookup.
    ``app/automation/waits.py``'s ``wait_visible_element`` reproduces that by
    taking the page object's locator constant and resolving it inside
    ``visibility_of_element_located``, and :attr:`locator` therefore holds the
    constant the body passed - the same object, not a copy of its value.
    """

    #: Name of the helper the step called.
    helper: str

    #: The locator constant the call site passed, recorded as that very object
    #: so :func:`assert_visibility_waits` can weigh it against ``SalesPage``'s
    #: own attribute by identity.
    locator: tuple[str, str]

    #: Seconds, exactly as the call site supplied them.
    timeout: Any

    #: How many effectful operations had already happened when this wait was
    #: made - so ``1`` means "after the first click, before whatever comes
    #: next".  This is what pins ``Sales.java``'s click-*then*-wait order
    #: without interleaving the two sequences into one assertion: the click at
    #: ``:21`` and the wait at ``:22`` reversed would move this from ``1`` to
    #: ``0``.
    after: int


def page_constant_name(target: Any) -> str | None:
    """The name of the ``SalesPage`` locator constant that *is* *target*.

    Compared by identity across :attr:`SalesPage.LOCATORS`, not by value: two
    pages of the reference do carry same-valued selectors, and ``SalesP.java``
    itself declares near-twins (``:50`` and ``:62`` differ only by a trailing
    ``/span``), so a same-valued tuple must not be able to stand in for the
    constant a body is required to pass.

    :param target: A wait target, or any other candidate locator.
    :returns: The constant's upper-case name, or ``None`` when *target* is not
        one of the page class's twenty locator objects.
    """
    for name in SalesPage.LOCATORS:
        if getattr(SalesPage, name) is target:
            return name

    return None


def _wait_locator(target: Any) -> tuple[str, str]:
    """Check a wait target is a ``SalesPage`` locator constant, and hand it back.

    :param target: What the step passed as its wait target.
    :returns: *target* unchanged, so the recorded value keeps the call site's
        own object.
    :raises AssertionError: If *target* is not one of the page class's locator
        constants - a resolved element, a locator built in the body, or a
        same-valued copy - because ``wait_visible_element`` resolves the
        locator inside ``visibility_of_element_located`` and a target looked up
        before the call would move the lookup out from under
        ``Sales.java:17``'s four seconds and behind the session's ten-second
        implicit wait (``Driver.java:34``).
    """
    name = page_constant_name(target)

    if name is None:
        raise AssertionError(
            f"wait target {target!r} is not one of SalesPage's locator "
            f"constants. Sales.java's eight waits each apply "
            f"ExpectedConditions.visibilityOf to a PageFactory proxy that "
            f"re-locates inside the predicate (SalesP.java:14), so every call "
            f"site must pass page.<CONSTANT> itself"
        )

    return target


def _wait_recorder(helper: str, recorded: list[WaitCall], driver: Any) -> Any:
    """Build the stand-in installed over one wait helper.

    :param helper: The name being substituted, carried into each record.
    :param recorded: The list every call appends to.
    :param driver: The recorder, read to timestamp each wait against the
        effectful operations that preceded it.
    :returns: A callable with the helpers' ``(locator, timeout, *, driver=None)``
        signature that records and returns the locator it was given.
    """

    def recorder(target: Any, timeout: Any = _MISSING, **kwargs: Any) -> Any:
        assert timeout is not _MISSING, (
            f"{helper}() was called with no timeout. Sales.java:17 fixes 4 "
            f"seconds for this class and app/automation/waits.py declares no "
            f"default, so every call site must supply it"
        )
        assert set(kwargs) <= {"driver"}, (
            f"{helper}() was called with unexpected keyword arguments "
            f"{sorted(set(kwargs) - {'driver'})!r}"
        )

        recorded.append(
            WaitCall(
                helper=helper,
                locator=_wait_locator(target),
                timeout=timeout,
                after=len(effect_log(driver)),
            )
        )
        # The real visibility helpers return the element the predicate
        # resolved. No body in this module reads that result - every one of
        # Sales.java's eight wait.until() calls discards its return too
        # (``:22 :29 :43 :57 :59 :61 :69 :83``), which
        # test_the_eight_wait_sites... asserts at the source level - so the
        # locator is handed straight back and never read.
        return target

    return recorder


def install_wait_recorder(
    monkeypatch: pytest.MonkeyPatch, match: Any, driver: Any
) -> list[WaitCall]:
    """Substitute every wait helper in the step module's namespace.

    Every ``wait_*`` name present is replaced, not just the one the port
    calls, so a wait taken through any other helper is recorded and judged
    rather than reaching a real ``WebDriverWait`` built around a session the
    recorder is not.  ``monkeypatch.setitem`` is used rather than assignment
    because the step registry is session-scoped state shared with every other
    step test.

    :param monkeypatch: The test's patcher, which restores the namespace.
    :param match: Any ``StepMatch`` of the module - all seven share one
        namespace, as :func:`test_every_definition_resolves_to_its_own_body`
        asserts.
    :param driver: The recorder the step will run against, so that each wait
        can be positioned among the effectful operations.
    :returns: The list each substituted helper appends to, in call order.
    :raises AssertionError: If the module imports no wait helper at all, or
        none that waits on visibility.
    """
    namespace = match.func.__globals__
    helpers = sorted(
        name for name, value in namespace.items() if name.startswith("wait_") and callable(value)
    )

    assert helpers, (
        "features/steps/sales_steps.py imports no wait helper, but "
        "Sales.java has eight wait sites"
    )
    assert VISIBILITY_WAIT_HELPERS.intersection(helpers), (
        f"features/steps/sales_steps.py imports {helpers!r} but none of "
        f"{sorted(VISIBILITY_WAIT_HELPERS)!r}; every wait in Sales.java is an "
        f"ExpectedConditions.visibilityOf wait"
    )

    recorded: list[WaitCall] = []

    for name in helpers:
        monkeypatch.setitem(namespace, name, _wait_recorder(name, recorded, driver))

    return recorded


def wait_sequence(waits: list[WaitCall]) -> tuple[tuple[tuple[str, str], Any], ...]:
    """Reduce recorded waits to the ``(locator, timeout)`` pairs to assert.

    The locator in each pair is the ``SalesPage`` constant the body passed, so
    a test compares it against ``SalesPage.<CONSTANT>`` directly.

    :param waits: What :func:`install_wait_recorder` collected.
    :returns: One pair per wait, in call order.
    """
    return tuple((wait.locator, wait.timeout) for wait in waits)


def wait_positions(waits: list[WaitCall]) -> tuple[int, ...]:
    """Where each wait sat among the step's effectful operations.

    The companion of :func:`wait_sequence` and :func:`effect_log`: those two
    pin *what* happened, this pins *when* the waits happened relative to it.
    Counting positions rather than merging the two logs keeps the ordering
    fact on its own: a wait takes a locator and performs no operation the
    recorder logs, so its position is the number of operations before it, and
    ``Sales.java``'s click-then-wait order - the click at ``:21`` before the
    wait at ``:22``, and so on through ``:96`` - becoming wait-then-click moves
    every one of them.

    :param waits: What :func:`install_wait_recorder` collected.
    :returns: One count per wait, in call order: the number of effectful
        operations that had already happened when it was made.
    """
    return tuple(wait.after for wait in waits)


def assert_visibility_waits(waits: list[WaitCall]) -> None:
    """Check every recorded wait against ``Sales.java:17`` and ``:22``.

    Three facts, all class-wide: the predicate is visibility, the timeout is
    four seconds at every site, and the target is one of ``SalesPage``'s
    locator constants - the same object the page class holds, so the lookup
    happens inside ``visibility_of_element_located`` under those four seconds
    rather than before the call.

    :param waits: What :func:`install_wait_recorder` collected.
    :returns: ``None``.
    """
    for wait in waits:
        assert wait.helper in VISIBILITY_WAIT_HELPERS, (
            f"wait on {wait.locator!r} used {wait.helper!r}; Sales.java:22 and "
            f"its seven siblings all wait on ExpectedConditions.visibilityOf"
        )
        assert wait.timeout == WAIT_TIMEOUT, (
            f"wait on {wait.locator!r} used a {wait.timeout!r}-second timeout; "
            f"Sales.java:17 fixes {WAIT_TIMEOUT} for every wait in this class"
        )
        assert page_constant_name(wait.locator) is not None, (
            f"wait target {wait.locator!r} is not a SalesPage locator "
            f"constant; SalesP.java:14's proxies re-located inside the "
            f"predicate, so the wait receives page.<CONSTANT> and resolves it "
            f"itself"
        )


# --------------------------------------------------------------------------- #
# Reading the recorder
# --------------------------------------------------------------------------- #


def effect_log(driver: Any) -> tuple[tuple[Any, ...], ...]:
    """The step's effectful operations, in order, with their locators.

    Every entry of the recorder's log except ``find_element``, flattened to
    ``(operation, locator, *arguments)``.  Resolution is excluded on purpose:
    each entry already names the locator its operation acted on, so listing
    the lookup that preceded it would say the same thing twice and leave the
    behavioural sequence twice as long as the Java body it mirrors.
    :func:`assert_lookup_invariant` pins the lookups separately, as a count.

    ``find_elements`` is deliberately *not* excluded: none of the seven
    ``Sales.java`` bodies at ``:20-96`` reads a plural locator, so a plural
    lookup appearing here is a parity break and must show up in the sequence.

    :param driver: The :fixture:`stub_driver` the step ran against.
    :returns: One flattened entry per effectful operation, in call order.
    """
    return tuple(
        (operation, *arguments)
        for operation, arguments in driver.calls
        if operation != "find_element"
    )


def assert_lookup_invariant(driver: Any, waits: list[WaitCall]) -> None:
    """Check that every element reference cost exactly one fresh lookup.

    ``PageFactory.initElements`` (``SalesP.java:14``) hands out proxies that
    re-resolve on every dereference and cache nothing, and
    ``app/pages/base_page.py`` reproduces that.  Each statement of a
    ``Sales.java`` body that touches an element therefore costs one
    ``findElement`` and the lookups must number exactly as many as the
    operations performed on elements.

    The waits add none: each one is passed a locator constant and resolves it
    inside ``visibility_of_element_located``, where the real helper - not the
    body - performs the lookup, so a wait that showed up here as a lookup
    would mean the body had resolved its target before calling.

    :param driver: The recorder the step ran against.
    :param waits: What :func:`install_wait_recorder` collected, for the wait
        targets named in the failure message.
    :returns: ``None``.
    """
    element_operations = sum(
        1 for operation, _ in driver.calls if operation.startswith("element.")
    )

    assert driver.count_of("find_element") == element_operations, (
        f"{driver.count_of('find_element')} lookups for "
        f"{element_operations} element operations, with "
        f"{len(waits)} locator-targeted waits costing none; the port's "
        f"accessors cache nothing, so each element reference must cost "
        f"exactly one and a wait must cost none"
    )
    assert driver.count_of("find_elements") == 0, (
        "a plural lookup was made, but no Sales.java body reads "
        "SalesP.java:69's allCustomers"
    )


# --------------------------------------------------------------------------- #
# Reading the step module's source
#
# The negative facts - no sleep, no action chain, no Keys import, no
# configuration read, no driver lifecycle, no use of the four unreferenced
# locators - are asserted against the module's syntax tree rather than its
# text, because its docstring discusses every one of them by name and a textual
# search would match the prose.
# --------------------------------------------------------------------------- #


@functools.cache
def step_module_tree() -> ast.Module:
    """Parse ``features/steps/sales_steps.py`` once per session.

    :returns: Its syntax tree.
    """
    return ast.parse(
        STEP_MODULE_PATH.read_text(encoding="utf-8"), filename=str(STEP_MODULE_PATH)
    )


@functools.cache
def referenced_names() -> frozenset[str]:
    """Every bare name the module references.

    :returns: The set of ``ast.Name`` identifiers, load and store alike.
    """
    return frozenset(
        node.id for node in ast.walk(step_module_tree()) if isinstance(node, ast.Name)
    )


@functools.cache
def referenced_attributes() -> frozenset[str]:
    """Every attribute name the module reads or writes.

    :returns: The set of ``ast.Attribute`` attribute names.
    """
    return frozenset(
        node.attr for node in ast.walk(step_module_tree()) if isinstance(node, ast.Attribute)
    )


@functools.cache
def imports() -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Every import in the module as ``(module, imported names)``.

    :returns: One entry per ``import``/``from ... import`` statement; the name
        tuple is empty for a plain ``import x``.
    """
    entries: list[tuple[str, tuple[str, ...]]] = []

    for node in ast.walk(step_module_tree()):
        if isinstance(node, ast.Import):
            entries.extend((alias.name, ()) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            entries.append(
                (node.module or "", tuple(alias.name for alias in node.names))
            )

    return tuple(entries)


def imported_modules() -> frozenset[str]:
    """The modules the step module imports from.

    :returns: The set of module names.
    """
    return frozenset(module for module, _ in imports())


def imported_names() -> frozenset[str]:
    """The names the step module imports.

    :returns: The set of imported names across every ``from`` import.
    """
    return frozenset(name for _, names in imports() for name in names)


def names_imported_from(module: str) -> frozenset[str]:
    """The names the step module imports from one module.

    :param module: The module to look at, such as ``"app.pages"``.
    :returns: The names imported from it, empty when it is not imported.
    """
    return frozenset(
        name for imported, names in imports() if imported == module for name in names
    )


def call_count(name: str) -> int:
    """How many times the module calls *name*, as a bare name or an attribute.

    :param name: The callee to count.
    :returns: The number of call sites.
    """
    total = 0

    for node in ast.walk(step_module_tree()):
        if not isinstance(node, ast.Call):
            continue

        callee = node.func
        if (isinstance(callee, ast.Name) and callee.id == name) or (
            isinstance(callee, ast.Attribute) and callee.attr == name
        ):
            total += 1

    return total


@functools.cache
def step_body(function: str) -> tuple[ast.stmt, ...]:
    """The top-level statements of one step body, in source order.

    The instrument for the two parity facts a run cannot observe: that a body
    *binds* a literal, and that it binds it before the read that follows it.
    Neither leaves a trace in the effect log - a binding touches no browser and
    writes nothing - so the syntax tree is the only place they can be asserted,
    and the docstring is not, because prose can agree with a body that no
    longer matches it.

    :param function: The port's function name, as ``DEFINITIONS`` records it.
    :returns: The function's own statements, excluding its docstring.
    :raises AssertionError: If the module declares no such function, which is
        what a rename or a deletion looks like from here.
    """
    for node in step_module_tree().body:
        if isinstance(node, ast.FunctionDef) and node.name == function:
            body = tuple(node.body)

            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                return body[1:]

            return body

    raise AssertionError(
        f"features/steps/sales_steps.py declares no function {function!r}, so "
        f"the Java body it ports is unasserted"
    )


class LiteralBinding(NamedTuple):
    """One string literal a step body binds, as the syntax tree shows it."""

    #: Position among the body's own statements, so that "bound before the
    #: read" - ``Sales.java:71`` before ``:72`` - is an assertable fact.
    index: int

    #: The assignment target, rendered back to source.
    target: str

    #: The bound value.
    literal: str


def bound_string_literals(function: str) -> tuple[LiteralBinding, ...]:
    """Every string literal an assignment in *function* binds, in body order.

    :param function: The port's function name.
    :returns: One :class:`LiteralBinding` per assignment whose right-hand side
        is a plain string literal.  A computed right-hand side -
        ``"Customers - " + context.driver.title`` at ``Sales.java:32`` - is not
        a hard-coded literal and is not reported.
    """
    bindings: list[LiteralBinding] = []

    for index, statement in enumerate(step_body(function)):
        if isinstance(statement, ast.Assign):
            targets = statement.targets
            value = statement.value
        elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
            targets = [statement.target]
            value = statement.value
        else:
            continue

        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            bindings.extend(
                LiteralBinding(index, ast.unparse(target), value.value)
                for target in targets
            )

    return tuple(bindings)


def statement_index_reading(function: str, accessor: str) -> int:
    """Where *function* dereferences ``page.<accessor>``, among its statements.

    Located by the accessor rather than by the helper that performs the read,
    so the position assertion survives :func:`_read_element_text` being renamed
    or inlined while still failing if the read moves, disappears, or overtakes
    the literal binding that must precede it.

    :param function: The port's function name.
    :param accessor: The lower-case page accessor, such as ``"name_check"``.
    :returns: The index of the first statement that reads it.
    :raises AssertionError: If no statement does, which means the step lost the
        only failure mode ``Sales.java`` gives it.
    """
    for index, statement in enumerate(step_body(function)):
        for node in ast.walk(statement):
            if isinstance(node, ast.Attribute) and node.attr == accessor:
                return index

    raise AssertionError(
        f"{function}() never dereferences page.{accessor}; that read is the "
        f"step's only way to fail and must not be dropped"
    )


def name_loads(function: str, name: str) -> int:
    """How many times *function* reads the bare name *name*.

    :param function: The port's function name.
    :param name: The identifier to count loads of.
    :returns: The number of ``ast.Name`` nodes in load context, so ``0`` means
        the name is bound and never consumed.
    """
    return sum(
        1
        for statement in step_body(function)
        for node in ast.walk(statement)
        if isinstance(node, ast.Name)
        and node.id == name
        and isinstance(node.ctx, ast.Load)
    )


def wait_call_sites() -> tuple[int, int]:
    """Count the module's wait call sites, and how many discard their result.

    :returns: ``(total call sites, sites that are bare expression statements)``.
    """
    total = 0

    for node in ast.walk(step_module_tree()):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id.startswith("wait_")
        ):
            total += 1

    discarded = sum(
        1
        for node in ast.walk(step_module_tree())
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id.startswith("wait_")
    )

    return total, discarded


# --------------------------------------------------------------------------- #
# Reading the feature file
# --------------------------------------------------------------------------- #

#: The Gherkin step keywords a phrase can be introduced by.  ``*`` included
#: because behave accepts it; ``Sales.feature`` uses the other five.
STEP_KEYWORDS: Final[tuple[str, ...]] = ("Given", "When", "Then", "And", "But", "*")


@functools.cache
def feature_lines() -> tuple[str, ...]:
    """``features/Sales.feature`` as lines, with no line endings.

    :returns: The file's lines in order.
    """
    return tuple(FEATURE_PATH.read_text(encoding="utf-8").splitlines())


def feature_phrases() -> tuple[str, ...]:
    """Every step phrase the feature invokes, keyword stripped, in file order.

    :returns: One phrase per step line, duplicates preserved - the same phrase
        invoked by two scenarios is two invocations.
    """
    phrases: list[str] = []

    for line in feature_lines():
        stripped = line.strip()

        for keyword in STEP_KEYWORDS:
            if stripped.startswith(f"{keyword} "):
                phrases.append(stripped[len(keyword) + 1 :].strip())
                break

    return tuple(phrases)


# =========================================================================== #
# Registration: the seven definitions, and the phrases that reach them
# =========================================================================== #


def test_module_registers_exactly_the_seven_java_definitions(step_registry: Any) -> None:
    """Pin the module's whole registry footprint: ``Sales.java:19-96``.

    Seven definitions, no more and no fewer, all in the ``step`` bucket
    (AAP deviation 7 - and ``Sales.java:80`` is an ``@And``, for which behave
    has no decorator), carrying the Java annotation texts in Java declaration
    order.
    """
    registered: list[tuple[str, str]] = []

    for bucket in STEP_BUCKETS:
        for matcher in step_registry.steps.get(bucket, ()):
            filename = str(getattr(matcher.location, "filename", "") or "")

            if Path(filename).stem == STEP_MODULE_NAME:
                registered.append((bucket, str(matcher.pattern)))

    assert [pattern for _, pattern in registered] == [
        definition.pattern for definition in DEFINITIONS
    ], (
        "features/steps/sales_steps.py must register Sales.java's seven "
        "annotation texts, in Sales.java declaration order"
    )
    assert {bucket for bucket, _ in registered} == {STEP_BUCKET}, (
        f"every definition must register in the {STEP_BUCKET!r} bucket; found "
        f"{sorted({bucket for bucket, _ in registered})!r}"
    )


def test_every_definition_resolves_to_its_own_body(resolve_step: Any) -> None:
    """Pin phrase -> function for all seven, and the single namespace.

    Each concrete phrase of ``Sales.feature`` resolves to exactly one
    definition (``resolve_step`` raises on nought or two), in this module, in
    the ``step`` bucket, and the seven functions are seven distinct objects
    sharing one module namespace - the namespace
    :func:`install_wait_recorder` patches.
    """
    matches = [resolve_step(phrase) for phrase in FLOW_PHRASES]

    assert [match.func.__name__ for match in matches] == [
        definition.function for definition in DEFINITIONS
    ]
    assert [match.pattern for match in matches] == [
        definition.pattern for definition in DEFINITIONS
    ]
    assert {match.module_name for match in matches} == {STEP_MODULE_NAME}
    assert {match.bucket for match in matches} == {STEP_BUCKET}
    assert len({id(match.func) for match in matches}) == len(DEFINITIONS)
    assert len({id(match.func.__globals__) for match in matches}) == 1, (
        "the seven definitions must share one module namespace; behave execs "
        "the module once and the wait seam patches that one namespace"
    )


def test_only_the_search_phrase_takes_a_parameter(resolve_step: Any) -> None:
    """``Sales.java:66-67`` is the class's one parameterized step.

    Cucumber's ``{string}`` consumes the quotation marks and hands over the
    text between them, so behave's pattern carries the marks literally around
    a named field and the value arrives without them.  The other six take
    ``context`` alone.
    """
    search = resolve_step(PHRASE_SEARCH)

    assert search.kwargs == {"name": "Lucas"}
    assert search.args == ()

    for phrase in (phrase for phrase in FLOW_PHRASES if phrase != PHRASE_SEARCH):
        match = resolve_step(phrase)
        assert match.args == ()
        assert match.kwargs == {}


def test_the_two_create_customer_phrases_resolve_to_distinct_bodies(
    resolve_step: Any,
    fake_context: Any,
    stub_driver: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``Sales.java:40`` and ``:80`` must never be confused for one another.

    Their phrases differ by one word and their bodies by seven statements,
    and under a single ``@step`` registry - which reaches every Gherkin
    keyword - an un-anchored pattern would resolve both to one body and run the
    wrong one.  Both patterns are therefore exact, both resolve unambiguously,
    and the two bodies are driven back to back to show the ten-statement form
    and the three-statement form are not the same code.
    """
    create_the = resolve_step(PHRASE_CREATE_THE)
    create_new = resolve_step(PHRASE_CREATE_NEW)

    assert create_the.pattern == PHRASE_CREATE_THE
    assert create_new.pattern == PHRASE_CREATE_NEW
    assert create_the.func.__name__ == "user_can_create_the_customer"
    assert create_new.func.__name__ == "user_can_create_new_customer"
    assert create_the.func is not create_new.func

    waits = install_wait_recorder(monkeypatch, create_the, stub_driver)

    create_the.run(fake_context)
    first = effect_log(stub_driver)
    first_waits = wait_sequence(waits)
    first_positions = wait_positions(waits)

    stub_driver.clear_calls()
    waits.clear()

    create_new.run(fake_context)
    second = effect_log(stub_driver)
    second_waits = wait_sequence(waits)
    second_positions = wait_positions(waits)

    assert len(first) == 9, "Sales.java:42-51 is nine effectful statements"
    assert len(second) == 2, "Sales.java:82-84 is two effectful statements"
    assert first != second
    assert first_waits == ((SalesPage.CREATE_BUTTON, WAIT_TIMEOUT),)
    assert second_waits == ((SalesPage.CREATE_BUTTON, WAIT_TIMEOUT),)
    assert first_positions == second_positions == (1,)


# =========================================================================== #
# Sales.java:19-23 - the dashboard step
# =========================================================================== #


def test_dashboard_step_clicks_then_waits_on_the_same_element(
    resolve_step: Any,
    fake_context: Any,
    stub_driver: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``Sales.java:21-22``: click ``salesPartial``, then wait 4s on it.

    The order is the source's and stays that way - the click first, the wait
    second, so what is waited on is an element the click has already acted
    upon.  Both statements name the same field: the click dereferences the
    accessor, which costs the body's one lookup, and the wait is handed
    ``SalesPage.SALES_PARTIAL`` and resolves it inside the predicate.  Nothing
    is printed and nothing is asserted.
    """
    match = cover(resolve_step(PHRASE_DASHBOARD))
    waits = install_wait_recorder(monkeypatch, match, stub_driver)

    match.run(fake_context)

    assert effect_log(stub_driver) == (("element.click", SalesPage.SALES_PARTIAL),)
    assert wait_sequence(waits) == ((SalesPage.SALES_PARTIAL, WAIT_TIMEOUT),)
    assert wait_positions(waits) == (1,), (
        "Sales.java:21 clicks and :22 then waits; reversing the pair would "
        "wait on an element nothing had acted upon yet"
    )
    assert_visibility_waits(waits)
    assert_lookup_invariant(stub_driver, waits)
    assert capsys.readouterr().out == ""


# =========================================================================== #
# Sales.java:25-38 - the Customers step, and the class's only assertion
# =========================================================================== #


def test_customers_step_clicks_waits_and_passes_on_the_bare_title(
    resolve_step: Any,
    fake_context: Any,
    stub_driver: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``Sales.java:28-37``, the passing half of the one assertion.

    ``:28`` clicks the Customers entry, ``:29`` waits 4s on it, ``:31`` binds
    the literal ``"Customers - Odoo"`` to *actualTitle*, ``:32`` binds
    ``"Customers - "`` followed by the live title to *expectedTitle* - so the
    names are the wrong way round and the prefix is doubled on any real
    Customers page, both of which are the source's and are preserved.  With the
    live title ``"Odoo"`` the two strings match and ``:37`` passes.

    ``:34-35`` print both values and neither line is reproduced: the live
    browser title is data read from the system under test and the worker's
    stdout is relayed into the parent logger and the Jenkins console
    (CWE-532/359, F04).  Silence is asserted here, and the two locals - which
    ``:37`` compares, under the source's inverted names - are what the
    assertion in the failing-path test still pins.
    """
    stub_driver.title = PASSING_TITLE
    match = cover(resolve_step(PHRASE_CUSTOMERS))
    waits = install_wait_recorder(monkeypatch, match, stub_driver)

    match.run(fake_context)

    assert effect_log(stub_driver) == (
        ("element.click", SalesPage.CUSTOMERS_BUTTON),
        ("title",),
    )
    assert wait_sequence(waits) == ((SalesPage.CUSTOMERS_BUTTON, WAIT_TIMEOUT),)
    assert wait_positions(waits) == (1,)
    assert_visibility_waits(waits)
    assert_lookup_invariant(stub_driver, waits)
    assert capsys.readouterr().out == "", (
        "Sales.java:34-35's two prints are not reproduced; the live browser "
        "title must not reach the worker or Jenkins log"
    )


@pytest.mark.parametrize(
    ("live_title", "doubled_title"),
    [
        ("Customers - Odoo", "Customers - Customers - Odoo"),
        ("Odoo - Customers", "Customers - Odoo - Customers"),
        ("", "Customers - "),
    ],
)
def test_customers_step_fails_with_the_exact_java_message(
    resolve_step: Any,
    fake_context: Any,
    stub_driver: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    live_title: str,
    doubled_title: str,
) -> None:
    """``Sales.java:37``: the message, byte for byte, with no trailing space.

    The three titles include the one a real Odoo Customers page reports, which
    is why this step fails in production - the doubled prefix can never equal
    the constant.  That outcome is the faithful one and is not repaired.  Per
    AAP deviation 16 only the subject and the message text are parity, so the
    message is compared with ``==`` - which is what catches the trailing space
    ``LoginSD.java:46`` and ``LogOutSD.java:27`` carry and this call does not -
    while JUnit's ``expected:<...> but was:<...>`` rendering is neither
    reproduced nor asserted.

    The failing path is where ``:34-35``'s prints would have carried the live
    title furthest, a failing step being the one a reader opens the CI log for,
    and it is silent as well: the ``AssertionError`` and its message are the
    whole of what this step produces (CWE-532/359, F04).  *doubled_title* is
    the string ``:32`` builds from each live title, transcribed so the doubled
    prefix stays a stated fact of each case rather than an implicit one.
    """
    stub_driver.title = live_title
    match = cover(resolve_step(PHRASE_CUSTOMERS))
    waits = install_wait_recorder(monkeypatch, match, stub_driver)

    with pytest.raises(AssertionError) as failure:
        match.run(fake_context)

    assert str(failure.value) == TITLE_ASSERTION_MESSAGE
    assert failure.value.args == (TITLE_ASSERTION_MESSAGE,)
    assert not str(failure.value).endswith(" ")
    assert "Customers - " + live_title == doubled_title
    assert capsys.readouterr().out == "", (
        f"the failing comparison wrote to standard output; neither the live "
        f"title {live_title!r} nor the doubled {doubled_title!r} may reach the "
        f"worker or Jenkins log"
    )
    assert wait_sequence(waits) == ((SalesPage.CUSTOMERS_BUTTON, WAIT_TIMEOUT),)
    assert wait_positions(waits) == (1,)


# =========================================================================== #
# Sales.java:40-52 - the new-customer form
# =========================================================================== #


def test_create_the_customer_step_fills_the_form_in_java_order(
    resolve_step: Any,
    fake_context: Any,
    stub_driver: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``Sales.java:42-51``: ten statements whose order is load-bearing.

    Click ``Create`` and wait 4s on it (``:42-43``); type the name and the
    address (``:44-45``); open the state autocomplete and its
    ``Create and Edit...`` entry, which is what opens the state sub-form
    (``:46-47``); type the state name and code (``:48-49``); click the
    sub-form's country field and the suggestion it offers (``:50-51``).

    The four literals are the reference suite's own fixture data, carried over
    verbatim under AAP 0.8 and asserted as they stand.  The wait at ``:43`` is
    the only one here: the eight statements after it run unguarded in Java,
    behind nothing but the session's ten-second implicit wait, and they run
    unguarded in the port.
    """
    match = cover(resolve_step(PHRASE_CREATE_THE))
    waits = install_wait_recorder(monkeypatch, match, stub_driver)

    match.run(fake_context)

    assert effect_log(stub_driver) == (
        ("element.click", SalesPage.CREATE_BUTTON),
        ("element.send_keys", SalesPage.CUSTOMER_NAME, "Lucas"),
        ("element.send_keys", SalesPage.ADDRESS, "1 boulevard auguste rodin 75000"),
        ("element.click", SalesPage.STATE_OPTIONS),
        ("element.click", SalesPage.CREATE_AND_EDIT_STATE),
        ("element.send_keys", SalesPage.STATE_NAME, "Albania"),
        ("element.send_keys", SalesPage.STATE_CODE, "78"),
        ("element.click", SalesPage.COUNTRY_STATE_BUTTON),
        ("element.click", SalesPage.COUNTRY_SELECTION),
    )
    assert wait_sequence(waits) == ((SalesPage.CREATE_BUTTON, WAIT_TIMEOUT),)
    assert wait_positions(waits) == (1,), (
        "Sales.java:43's wait follows the click at :42 and precedes the "
        "eight unguarded statements"
    )
    assert_visibility_waits(waits)
    assert_lookup_invariant(stub_driver, waits)
    assert capsys.readouterr().out == ""


def test_create_and_edit_state_uses_the_no_space_xpath(
    resolve_step: Any,
    fake_context: Any,
    stub_driver: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``SalesP.java:35``, reached by ``Sales.java:47``, unchanged.

    The selector has no spaces around its ``=``.  ``NotesP.java:14`` expresses
    the same idea with spaces and on an ``<a>`` element, and neither is
    normalised towards the other, so the byte-exact value is pinned here as
    well as its identity with the page constant the step reaches.
    """
    match = resolve_step(PHRASE_CREATE_THE)
    install_wait_recorder(monkeypatch, match, stub_driver)

    match.run(fake_context)

    assert SalesPage.CREATE_AND_EDIT_STATE[1] == "//li[.='Create and Edit...']"
    assert ("element.click", SalesPage.CREATE_AND_EDIT_STATE) in effect_log(stub_driver)


# =========================================================================== #
# Sales.java:54-63 - saving
# =========================================================================== #


def test_save_the_customer_step_pairs_click_and_wait_three_times(
    resolve_step: Any,
    fake_context: Any,
    stub_driver: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``Sales.java:56-61``: click-then-wait, three times, never reversed.

    ``:56-57`` the state sub-form's save button, ``:58-59`` the customer
    form's, ``:60-61`` the Customers menu entry - which lands the browser back
    on the list the search step then searches.  The third pair repeats the
    Customers step's pair on the same locator **minus its title check**, so
    that no title is read here is part of the parity: the two functions share a
    locator and nothing else, and neither delegates to the other.
    """
    match = cover(resolve_step(PHRASE_SAVE))
    waits = install_wait_recorder(monkeypatch, match, stub_driver)

    match.run(fake_context)

    assert effect_log(stub_driver) == (
        ("element.click", SalesPage.SAVE_BUTTON),
        ("element.click", SalesPage.CREATE_CUSTOMER),
        ("element.click", SalesPage.CUSTOMERS_BUTTON),
    )
    assert wait_sequence(waits) == (
        (SalesPage.SAVE_BUTTON, WAIT_TIMEOUT),
        (SalesPage.CREATE_CUSTOMER, WAIT_TIMEOUT),
        (SalesPage.CUSTOMERS_BUTTON, WAIT_TIMEOUT),
    )
    assert wait_positions(waits) == (1, 2, 3), (
        "each of Sales.java:56-61's three waits follows its own click, never "
        "the other way round"
    )
    assert_visibility_waits(waits)
    assert_lookup_invariant(stub_driver, waits)
    assert "title" not in stub_driver.operations(), (
        "Sales.java:54-63 reads no title; the title comparison belongs to "
        "Sales.java:31-37 alone"
    )
    assert capsys.readouterr().out == ""


# =========================================================================== #
# Sales.java:66-77 - the search step: one parameter, one key, no comparison
# =========================================================================== #


def test_search_step_sends_the_name_then_enter_in_one_keyboard_call(
    resolve_step: Any,
    fake_context: Any,
    stub_driver: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``Sales.java:68``: ``searchBar.sendKeys(name + Keys.ENTER)``.

    The Java form concatenates the parameter and the key into one argument, so
    the browser sees a single keyboard call carrying one character stream.  The
    port reproduces that as exactly one ``send_keys`` with the step's own
    parameter first and ``Keys.ENTER`` second - not two calls, and not the key
    first.  The value searched for is deliberately not ``"Lucas"``: the body
    also holds ``"Lucas"`` as a literal at ``:71``, and a body that typed the
    literal instead of its parameter would pass against any phrase that used
    it.
    """
    searched = "Zephyrine-Q7"
    match = resolve_step(f'User can find his name "{searched}" from search bar')
    waits = install_wait_recorder(monkeypatch, match, stub_driver)

    assert match.kwargs == {"name": searched}

    match.run(fake_context)

    assert stub_driver.calls_of("element.send_keys") == (
        (SalesPage.SEARCH_BAR, searched, ENTER),
    )
    assert stub_driver.count_of("element.send_keys") == 1
    assert wait_sequence(waits) == ((SalesPage.SEARCH_BAR, WAIT_TIMEOUT),)
    assert wait_positions(waits) == (1,)


def test_search_step_reads_the_heading_and_compares_nothing(
    resolve_step: Any,
    fake_context: Any,
    stub_driver: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``Sales.java:71-77``: one read, no comparison, and no output.

    ``:71`` binds the literal ``"Lucas"`` to *actualName* - a constant even
    when the phrase supplies another name, as it does here - and ``:72`` reads
    the kanban heading into *expectedName*.  ``:74-75`` write both out and the
    body ends.  The comparison the naming plainly intends is **absent from the
    Java source**, so a heading that contradicts the literal must not fail this
    step; repairing it would change which scenarios pass.

    Two facts are therefore pinned together here.  The read at ``:72`` must
    still happen - it is the step's only failure mode besides the wait, so a
    port that dropped it would turn a step that can fail into one that cannot -
    and it must happen *silently*: ``:74-75`` printed the heading, which is a
    customer's name, and the worker's stdout is relayed into the parent logger
    and the Jenkins console, so reproducing those lines would put PII into
    durable logs (CWE-532/359, F04).  The effect log pins the read; ``capsys``
    pins the silence.  ``:71``'s literal is a separate parity fact, pinned by
    :func:`test_search_step_binds_the_java_name_literal_and_reads_it_nowhere`:
    it survives the print's removal because AAP 0.4.1 counts a body's
    hard-coded values as parity, and it is *bound*, never written anywhere.
    """
    searched = "Zephyrine-Q7"
    heading = "not-the-name-this-step-was-given"
    stub_driver.set_text(SalesPage.NAME_CHECK, heading)

    match = cover(resolve_step(f'User can find his name "{searched}" from search bar'))
    waits = install_wait_recorder(monkeypatch, match, stub_driver)

    match.run(fake_context)

    assert effect_log(stub_driver) == (
        ("element.send_keys", SalesPage.SEARCH_BAR, searched, ENTER),
        ("element.text", SalesPage.NAME_CHECK),
    )
    assert wait_sequence(waits) == ((SalesPage.SEARCH_BAR, WAIT_TIMEOUT),)
    assert wait_positions(waits) == (1,)
    assert_visibility_waits(waits)
    assert_lookup_invariant(stub_driver, waits)

    captured = capsys.readouterr().out

    assert captured == "", (
        "Sales.java:74-75's two prints are not reproduced; the kanban heading "
        "is a customer's name and must not reach the worker or Jenkins log"
    )
    assert heading not in captured
    assert "Lucas" not in captured


def test_search_step_binds_the_java_name_literal_and_reads_it_nowhere(
    resolve_step: Any,
    fake_context: Any,
    stub_driver: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``Sales.java:71``: ``String actualName = "Lucas";``, bound and unread.

    AAP 0.4.1's per-module obligation names "the same hard-coded literals and
    expected values", and this is one of the two the class carries that no
    comparison consumes.  Its print at ``:74`` is not reproduced - it wrote a
    customer's name into every worker and Jenkins log (CWE-532/359, F04) - but
    the *binding* is not the print: removing the output does not remove the
    value the Java body holds, and a port that dropped the literal would have
    no record of the name this step exists to look for.

    Four facts, and it takes all four to say "kept, and kept inert":

    * the literal is exactly ``"Lucas"``, byte for byte;
    * the body binds it, at its own statement;
    * the binding sits *before* the heading read, which is the order ``:71``
      and ``:72`` are written in;
    * nothing reads the binding - no name load of the discard target anywhere
      in the body - so the value cannot reach a comparison the source does not
      make, and the run stays silent, which ``capsys`` re-checks here beside
      the module-wide sink assertions of
      :func:`test_the_step_module_contains_no_print_call_at_all`.
    """
    searched = "Zephyrine-Q7"
    stub_driver.set_text(SalesPage.NAME_CHECK, "kanban-heading-value")

    match = cover(resolve_step(f'User can find his name "{searched}" from search bar'))
    install_wait_recorder(monkeypatch, match, stub_driver)

    assert searched != SEARCH_NAME_LITERAL, (
        "the phrase must supply a name other than the literal, so that a body "
        "binding the literal in place of its parameter cannot pass"
    )

    function, literal, accessor = LITERAL_BINDINGS[0]

    assert literal == "Lucas"
    assert literal == SEARCH_NAME_LITERAL

    bindings = [
        binding
        for binding in bound_string_literals(function)
        if binding.literal == literal
    ]

    assert len(bindings) == 1, (
        f"{function}() binds the Sales.java:71 literal {literal!r} "
        f"{len(bindings)} times; the source binds it exactly once"
    )
    assert bindings[0].target == DISCARD_TARGET, (
        f"Sales.java:71's literal is bound to {bindings[0].target!r}; the port "
        f"binds it to {DISCARD_TARGET!r}, which is what states that nothing "
        f"consumes it and keeps the binding clear of pyflakes F841"
    )
    assert bindings[0].index < statement_index_reading(function, accessor), (
        "Sales.java binds actualName at :71 and reads the kanban heading at "
        ":72; the binding must stay ahead of the read"
    )
    assert name_loads(function, DISCARD_TARGET) == 0, (
        "something in the search step reads the discarded binding; "
        "Sales.java:66-77 compares nothing and writes nothing, so the literal "
        "must stay inert"
    )

    match.run(fake_context)

    assert capsys.readouterr().out == "", (
        "the search step wrote to standard output; Sales.java:74-75 are not "
        "reproduced and the literal is bound, never written"
    )


# =========================================================================== #
# Sales.java:80-85 - the @And step, ending on an unwaited click
# =========================================================================== #


def test_create_new_customer_step_ends_on_an_unwaited_click(
    resolve_step: Any,
    fake_context: Any,
    stub_driver: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``Sales.java:82-84``: the blank-form path, and the one click with no wait.

    ``:82`` clicks ``Create`` and ``:83`` waits 4s on it; ``:84`` clicks the
    form's save button on an untouched form, which is what provokes the
    validation notice the next step reads.  ``:84`` is the class's only click
    with no wait after it and the body ends on it, so exactly one wait is
    expected here - a second would be an addition the source does not make.
    The first two statements repeat those of ``Sales.java:42-43`` on the same
    locator and are written out again rather than shared, as in Java.
    """
    match = cover(resolve_step(PHRASE_CREATE_NEW))
    waits = install_wait_recorder(monkeypatch, match, stub_driver)

    match.run(fake_context)

    assert effect_log(stub_driver) == (
        ("element.click", SalesPage.CREATE_BUTTON),
        ("element.click", SalesPage.CREATE_CUSTOMER),
    )
    assert wait_sequence(waits) == ((SalesPage.CREATE_BUTTON, WAIT_TIMEOUT),)
    assert wait_positions(waits) == (1,), (
        "Sales.java:83's wait follows the click at :82; the click at :84 has "
        "no wait after it"
    )
    assert_visibility_waits(waits)
    assert_lookup_invariant(stub_driver, waits)
    assert capsys.readouterr().out == ""


# =========================================================================== #
# Sales.java:87-96 - the warning step: the shortest body, and no comparison
# =========================================================================== #


def test_error_step_reads_the_warning_area_and_compares_nothing(
    resolve_step: Any,
    fake_context: Any,
    stub_driver: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``Sales.java:90-94``: one read, no click, no wait, no assert, no output.

    ``:90`` binds the literal ``"The following fields are invalid:"`` - the
    trailing colon included - and ``:91`` reads the notification area's text.
    ``:93-94`` print both and the body ends.  Whatever the notification area
    says, this step passes, because nothing weighs the two strings against each
    other; the programmed text here contradicts the literal precisely to prove
    that.  The read is the only thing this step does to the browser and its
    only way to fail, so it is pinned through the effect log.

    Neither printed line is reproduced: the notification text is read live from
    the system under test and the worker's stdout reaches the parent logger and
    the Jenkins console (CWE-532/359, F04).  ``:90``'s literal is kept, because
    AAP 0.4.1 counts a body's hard-coded values as parity and a binding is not
    an output;
    :func:`test_error_step_binds_the_java_warning_literal_and_reads_it_nowhere`
    is what pins it, and this test is what pins that keeping it changed nothing
    observable.
    """
    notice = "some other Odoo notification entirely"
    stub_driver.set_text(SalesPage.WARNING, notice)

    match = cover(resolve_step(PHRASE_ERROR))
    waits = install_wait_recorder(monkeypatch, match, stub_driver)

    match.run(fake_context)

    assert effect_log(stub_driver) == (("element.text", SalesPage.WARNING),)
    assert waits == [], "Sales.java:87-96 contains no wait.until call"
    assert_lookup_invariant(stub_driver, waits)

    captured = capsys.readouterr().out

    assert captured == "", (
        "Sales.java:93-94's two prints are not reproduced; the notification "
        "text is read live from the system under test"
    )
    assert notice not in captured


def test_error_step_binds_the_java_warning_literal_and_reads_it_nowhere(
    resolve_step: Any,
    fake_context: Any,
    stub_driver: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``Sales.java:90``: the validation sentence, bound and unread.

    The warning step's half of the parity fact its search-step counterpart
    carries: ``String actualWarning = "The following fields are invalid:";`` is
    a hard-coded expected value, AAP 0.4.1 enumerates those per module, and
    ``:93``'s print - which wrote it beside live notification text into worker
    and Jenkins logs (CWE-532/359, F04) - was the only thing that ever read it.
    Dropping the print is the remediation; dropping the sentence would be a
    parity loss, because it is the one record in the port of what a blank Odoo
    customer form is expected to complain about.

    The same four facts are pinned as for ``:71``, with the colon of the
    literal included byte for byte, and the binding required to sit ahead of
    the notification read exactly as ``:90`` sits ahead of ``:91`` - which in
    this body means first, since ``Sales.java:87-96`` has nothing else.
    """
    stub_driver.set_text(SalesPage.WARNING, "notification-area-value")

    match = cover(resolve_step(PHRASE_ERROR))
    install_wait_recorder(monkeypatch, match, stub_driver)

    function, literal, accessor = LITERAL_BINDINGS[1]

    assert literal == "The following fields are invalid:"
    assert literal == WARNING_LITERAL
    assert literal.endswith(":"), "Sales.java:90's literal ends in a colon"

    bindings = [
        binding
        for binding in bound_string_literals(function)
        if binding.literal == literal
    ]

    assert len(bindings) == 1, (
        f"{function}() binds the Sales.java:90 literal {literal!r} "
        f"{len(bindings)} times; the source binds it exactly once"
    )
    assert bindings[0].target == DISCARD_TARGET, (
        f"Sales.java:90's literal is bound to {bindings[0].target!r}; the port "
        f"binds it to {DISCARD_TARGET!r}, which is what states that nothing "
        f"consumes it and keeps the binding clear of pyflakes F841"
    )
    assert bindings[0].index < statement_index_reading(function, accessor), (
        "Sales.java binds actualWarning at :90 and reads the notification "
        "area at :91; the binding must stay ahead of the read"
    )
    assert name_loads(function, DISCARD_TARGET) == 0, (
        "something in the warning step reads the discarded binding; "
        "Sales.java:87-96 compares nothing and writes nothing, so the literal "
        "must stay inert"
    )

    match.run(fake_context)

    assert capsys.readouterr().out == "", (
        "the warning step wrote to standard output; Sales.java:93-94 are not "
        "reproduced and the literal is bound, never written"
    )


def test_error_step_reads_warning_and_not_warning_button(
    resolve_step: Any,
    fake_context: Any,
    stub_driver: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``Sales.java:91`` reads ``warning``, never ``warningButton``.

    ``SalesP.java:62`` declares ``warningButton`` and ``:65`` declares
    ``warning``; they are two different fields and only ``warning`` is
    referenced by any step class in the reference.  ``warningButton`` is in
    turn a near-twin of ``saveButton`` (``:50``), differing only by the absent
    trailing ``/span``, so all three are easy to confuse - which is why the
    assertion names the page class's own constants, and why the import
    boundary test keeps :class:`~app.pages.sales_page.SalesPage` the only page
    class this module can reach.
    """
    match = resolve_step(PHRASE_ERROR)
    install_wait_recorder(monkeypatch, match, stub_driver)

    match.run(fake_context)

    looked_up = stub_driver.calls_of("find_element")

    assert looked_up == (SalesPage.WARNING,)
    assert SalesPage.WARNING_BUTTON not in looked_up
    assert SalesPage.WARNING != SalesPage.WARNING_BUTTON


# =========================================================================== #
# Whole-module facts: the eight wait sites, and the negatives
#
# These tests run all seven bodies and deliberately do NOT record coverage -
# see the bookkeeping note above.
# =========================================================================== #


def run_whole_flow(
    resolve_step: Any,
    fake_context: Any,
    stub_driver: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> list[WaitCall]:
    """Drive all seven bodies once, in ``Sales.java`` declaration order.

    The live title is arranged so that ``Sales.java:37`` passes; every other
    page answer is left at the recorder's default, which is what the source's
    two assertion-free bodies tolerate.

    :param resolve_step: The registry fixture.
    :param fake_context: The behave context stand-in.
    :param stub_driver: The recorder it carries.
    :param monkeypatch: The patcher the wait seam is installed through.
    :returns: Every wait recorded across the seven bodies, in call order.
    """
    stub_driver.title = PASSING_TITLE
    matches = [resolve_step(phrase) for phrase in FLOW_PHRASES]
    waits = install_wait_recorder(monkeypatch, matches[0], stub_driver)

    for match in matches:
        match.run(fake_context)

    return waits


def test_the_eight_wait_sites_are_four_seconds_each_in_java_order(
    resolve_step: Any,
    fake_context: Any,
    stub_driver: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``Sales.java:17`` once, at eight sites: ``:22 :29 :43 :57 :59 :61 :69 :83``.

    Four seconds is the only timeout this class uses and the only 4-second
    timeout in the reference suite, so the whole ordered sequence is pinned in
    one place as well as per step.  The source-level count is asserted
    alongside it, which catches a wait deleted from a body whose test was
    deleted too, and every wait call site is a bare statement because Java
    discards every ``wait.until`` result.
    """
    waits = run_whole_flow(resolve_step, fake_context, stub_driver, monkeypatch)

    assert wait_sequence(waits) == (
        (SalesPage.SALES_PARTIAL, WAIT_TIMEOUT),
        (SalesPage.CUSTOMERS_BUTTON, WAIT_TIMEOUT),
        (SalesPage.CREATE_BUTTON, WAIT_TIMEOUT),
        (SalesPage.SAVE_BUTTON, WAIT_TIMEOUT),
        (SalesPage.CREATE_CUSTOMER, WAIT_TIMEOUT),
        (SalesPage.CUSTOMERS_BUTTON, WAIT_TIMEOUT),
        (SalesPage.SEARCH_BAR, WAIT_TIMEOUT),
        (SalesPage.CREATE_BUTTON, WAIT_TIMEOUT),
    )
    assert wait_positions(waits) == (1, 2, 4, 13, 14, 15, 16, 18), (
        "across the seven bodies every wait follows the statement it guards"
    )
    assert len(waits) == 8
    assert len(effect_log(stub_driver)) == 20, (
        "the seven bodies perform twenty effectful operations between them: "
        "nine clicks, four form sends, one keyboard send, two text reads and "
        "one title read"
    )
    assert {wait.timeout for wait in waits} == {WAIT_TIMEOUT}
    assert_visibility_waits(waits)

    total, discarded = wait_call_sites()
    assert total == 8, "Sales.java has eight wait.until sites"
    assert discarded == total, (
        "every wait.until result is discarded in Sales.java, so no wait call "
        "site in the port may bind one"
    )


def test_no_step_delays_the_run(
    resolve_step: Any,
    fake_context: Any,
    stub_driver: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``Sales.java`` declares ``throws InterruptedException`` and never sleeps.

    Three of the seven methods carry that declaration (``:20 :26 :41``) and
    not one contains a ``Thread.sleep``; the seventeen fixed sleeps of the
    reference all live in five *other* step classes.  The declaration without a
    delay is a source quirk, so the absence is asserted three ways rather than
    "justified" by adding one: the module namespace carries no sleeping name,
    its syntax tree contains no sleep call and no ``time`` import, and
    ``time.sleep`` itself is substituted for the whole flow and must never be
    reached.
    """
    calls: list[Any] = []
    monkeypatch.setattr(time, "sleep", lambda seconds: calls.append(seconds))

    matches = [resolve_step(phrase) for phrase in FLOW_PHRASES]
    namespace = matches[0].func.__globals__

    assert "sleep" not in namespace
    assert "time" not in namespace
    assert "sleep" not in referenced_names()
    assert "sleep" not in referenced_attributes()
    assert "time" not in imported_modules()
    assert call_count("sleep") == 0

    run_whole_flow(resolve_step, fake_context, stub_driver, monkeypatch)

    assert calls == [], f"a step slept for {calls!r} seconds; Sales.java sleeps never"


def test_module_makes_one_keyboard_call_and_builds_no_action_chain() -> None:
    """``Sales.java:9`` imports ``Keys`` and nothing imports ``Actions``.

    One keyboard site exists in the class, at ``:68``, so the port calls its
    keyboard helper exactly once; and because the class uses no ``Actions``,
    the action-chain helper must not even be imported - a negative worth
    asserting explicitly, since two of the port's other step modules do import
    it.  ``Keys`` itself stays out of this module: AAP 0.4.2 confines the
    selenium import to ``app/automation``, which is what resolves the key name.
    """
    assert call_count("press_keys") == 1
    assert "press_keys" in imported_names()
    assert "action_chain" not in imported_names()
    assert "action_chain" not in referenced_names()
    assert "Keys" not in imported_names()
    assert "Keys" not in referenced_names()


def test_module_import_boundary_is_the_three_java_dependencies() -> None:
    """AAP 0.4.2, against ``Sales.java:3-11``'s nine imports.

    The Java class imports its page object, the driver utility, three Cucumber
    annotations, JUnit's ``Assert``, ``Keys`` and two wait classes.  In the port
    that collapses to three: behave's ``@step``, the page object, and the
    ``app.automation`` helpers - which is the only package permitted to import
    selenium, so neither selenium nor ``By`` may be named here.  ``app.config``
    is absent because ``Sales.java`` reads no configuration key at all, unlike
    the Employee, Login and Session classes.
    """
    assert imported_modules() <= {"behave", "app.automation", "app.pages"}, (
        f"unexpected imports: {sorted(imported_modules())!r}"
    )
    assert "behave" in imported_modules()
    assert "app.pages" in imported_modules()
    assert "app.automation" in imported_modules()
    assert "step" in imported_names()
    assert "SalesPage" in imported_names()

    # One page class and no other. This is also what makes every locator
    # comparison in this file identity-safe: locators are compared by value,
    # and two pages of the reference do carry same-valued selectors, so a
    # second page class in scope here would be a way for the wrong constant to
    # satisfy an assertion. Notes is the only step module that legitimately
    # reaches two pages; this one reaches SalesP alone (Sales.java:3).
    assert names_imported_from("app.pages") == {"SalesPage"}

    # The automation surface Sales.java's imports reduce to: one keyboard
    # helper for Sales.java:9's org.openqa.selenium.Keys, used once at :68,
    # and one visibility wait for Sales.java:10-11's ExpectedConditions and
    # WebDriverWait, constructed at :17. Any other helper would change the
    # predicate, the lifecycle or the import boundary.
    assert names_imported_from("app.automation") <= PERMITTED_AUTOMATION_NAMES, (
        f"sales_steps.py imports "
        f"{sorted(names_imported_from('app.automation') - PERMITTED_AUTOMATION_NAMES)!r} "
        f"from app.automation; Sales.java needs only a keyboard helper and a "
        f"visibility wait"
    )

    for module in imported_modules():
        assert not module.startswith("selenium"), (
            f"sales_steps.py imports {module!r}; only app/automation may "
            f"import the browser-automation library"
        )

    assert "app.config" not in imported_modules()
    assert not any(name.startswith("get_") for name in imported_names()), (
        "Sales.java reads no configuration key, so no app.config accessor "
        "belongs in this module"
    )
    assert "By" not in imported_names()
    assert "By" not in referenced_names()


def test_no_step_reads_configuration_or_touches_the_driver_lifecycle(
    resolve_step: Any,
    fake_context: Any,
    stub_driver: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``Sales.java`` reads no property and owns no session.

    Its only driver contact is ``Driver.getDriver().getTitle()`` at ``:32``,
    which in the port is ``context.driver.title``; the session itself is
    created and quit by ``features/environment.py`` alone (AAP 0.3.3).  So no
    body may read ``context.config.userdata`` and none may create or quit a
    driver - asserted at the source level and again over the whole flow, where
    the recorder's quit counter must stay at nought.
    """
    assert "config" not in referenced_attributes()
    assert "userdata" not in referenced_attributes()
    assert "get_driver" not in referenced_names()
    assert "quit_driver" not in referenced_names()
    assert "quit" not in referenced_attributes()

    run_whole_flow(resolve_step, fake_context, stub_driver, monkeypatch)

    assert stub_driver.quit_count == 0
    assert fake_context.config.userdata == {}
    assert "get" not in stub_driver.operations(), (
        "no Sales.java body navigates; the Customers list is reached by "
        "clicking, at :28"
    )


def test_no_step_touches_the_four_unreferenced_locators(
    resolve_step: Any,
    fake_context: Any,
    stub_driver: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``SalesP.java``'s four dead fields stay dead: ``:62 :68 :71 :74``.

    ``warningButton``, ``allCustomers``, ``link`` and ``details`` have zero
    references across all eleven reference step classes, and sixteen of the
    twenty fields are what ``Sales.java`` actually reaches.  The page object
    declares all twenty for locator fidelity (AAP 0.8, "preserve, do not
    tidy"), and this test is what stops a later reader from "wiring up" one of
    them: no lookup in the whole flow may name one, and the module's syntax
    tree may not name their accessors.  The check is by page-class constant, so
    a same-valued locator from another page could not satisfy it either.
    """
    dead_locators = {getattr(SalesPage, name) for name in UNREFERENCED_LOCATORS}

    run_whole_flow(resolve_step, fake_context, stub_driver, monkeypatch)

    for locator in stub_driver.calls_of("find_element"):
        assert locator not in dead_locators, (
            f"the flow looked up {locator!r}, one of SalesP.java's four "
            f"fields that no reference step class touches"
        )

    for accessor in UNREFERENCED_ACCESSORS:
        assert accessor not in referenced_attributes(), (
            f"sales_steps.py reads page.{accessor}; no Sales.java body does"
        )

    assert len(dead_locators) == len(UNREFERENCED_LOCATORS), (
        "the four unreferenced locators must stay four distinct declarations"
    )


def test_module_reads_exactly_the_sixteen_accessors_java_reaches() -> None:
    """Sixteen of ``SalesP.java``'s twenty fields, and no twenty-first name.

    ``app/pages/sales_page.py`` declares all twenty locators
    (``SalesP.java:17-75``) in Java declaration order and ``BasePage`` installs
    a lower-case accessor for each; ``Sales.java:21-91`` dereferences sixteen
    of them, leaving the four at ``SalesP.java:62``, ``:68``, ``:71`` and
    ``:74``.  Checking the module's syntax tree against the page class's own
    locator inventory is the source-level counterpart of the runtime assertions
    above: it catches an accessor read on a path no test happens to drive, and
    it fails if a locator is renamed on one side only.
    """
    accessors = {name.lower() for name in SalesPage.LOCATORS}
    reached = accessors & referenced_attributes()

    assert len(accessors) == 20, (
        f"SalesPage declares {len(accessors)} locators; SalesP.java:17-75 "
        f"declares twenty"
    )
    assert reached == accessors - set(UNREFERENCED_ACCESSORS)
    assert len(reached) == 16


def test_the_flow_writes_nothing_to_standard_output(
    resolve_step: Any,
    fake_context: Any,
    stub_driver: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``Sales.java:34-35``, ``:74-75`` and ``:93-94``: six prints, none ported.

    The whole-module invariant, driven across all seven bodies in Java
    declaration order: not one line reaches standard output.  Three of the six
    values those prints carried are read live from the system under test - the
    Customers page title, a customer's name from the kanban heading and the
    notification area's text - and ``app/services/test_run_service.py`` relays
    every worker stdout line into the parent logger and from there into the
    Jenkins console, so each line was a durable record of live customer data
    and PII (CWE-532/359, F04).

    The page is programmed with distinctive values precisely so a reintroduced
    print would show up here by name.  The source-level count is asserted
    beside the run-time silence, which is what catches a print on a path this
    flow does not take; no parity is lost, because diagnostic stdout is in
    neither AAP 0.1.2's frozen list nor AAP 0.4.1's per-step enumeration, and
    every enumerated operation of the seven bodies is asserted by the tests
    above - including the two literals of ``:71`` and ``:90``, which the
    bodies still bind and which this silence proves they do not write.
    """
    heading = "kanban-heading-value"
    notice = "notification-area-value"
    stub_driver.set_text(SalesPage.NAME_CHECK, heading)
    stub_driver.set_text(SalesPage.WARNING, notice)

    run_whole_flow(resolve_step, fake_context, stub_driver, monkeypatch)

    printed = capsys.readouterr().out

    assert printed == "", (
        f"the flow wrote {printed!r} to standard output; Sales.java's six "
        f"System.out.println calls are deliberately not reproduced"
    )
    assert heading not in printed
    assert notice not in printed
    assert call_count("print") == 0, (
        "sales_steps.py calls print(); no body in this module writes to "
        "standard output"
    )


def test_the_step_module_contains_no_print_call_at_all() -> None:
    """``features/steps/sales_steps.py`` holds no ``print`` call, in any scope.

    The source-level companion of the flow's silence, and what closes the two
    gaps a run-time check leaves.  A print inside ``_page``,
    :func:`_read_element_text` or any helper added later is reached by no step
    phrase, and a print on a branch this flow does not take is reached by no
    run, yet either would write a line into every worker and Jenkins log
    (CWE-532/359, F04).

    The second half is the alternative-sink check.  The finding is about live
    SUT values becoming a durable record, so re-routing them to a logger, to
    ``sys.stdout``/``stderr`` directly or to a file would not resolve it - and
    ``Sales.java`` gives the port no reason to name any of those.  Both halves
    read the syntax tree rather than the text, because the module's docstring
    discusses the removed prints by name and a textual search would match the
    prose.
    """
    calls = [
        node
        for node in ast.walk(step_module_tree())
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "print"
    ]

    assert calls == [], (
        f"sales_steps.py calls print() at line(s) "
        f"{[node.lineno for node in calls]}; the module writes nothing to "
        f"standard output"
    )
    assert "print" not in referenced_names()

    for name in (
        "logging",
        "logger",
        "getLogger",
        "stdout",
        "stderr",
        "write",
        "open",
    ):
        assert name not in referenced_names(), (
            f"sales_steps.py references {name!r}; the six removed prints are "
            f"not to be re-routed to another sink"
        )
        assert name not in referenced_attributes(), (
            f"sales_steps.py reads or writes the attribute {name!r}; the six "
            f"removed prints are not to be re-routed to another sink"
        )


# =========================================================================== #
# features/Sales.feature - the Gherkin the seven definitions serve
# =========================================================================== #


def test_sales_feature_keeps_its_name_title_and_absent_tag() -> None:
    """``Sales.feature:1``, preserved verbatim, and its missing tag.

    The title begins with **four** literal dots - ``Feature: .... app Sales
    feature`` - which AAP 0.6 flags as an edge case for the JSON scenario-id
    slug rule and which is preserved like every other line of Gherkin.  The
    file carries no tag at all, so the default ``@Smoke`` filter
    (``CukesRunner.java:18``) does not select it: reaching it means naming the
    file or using a negative tag expression.  Adding a tag here would change
    which scenarios a default run executes.
    """
    assert FEATURE_PATH.name == "Sales.feature"
    assert FEATURE_PATH.is_file()

    lines = feature_lines()

    assert lines[0] == "Feature: .... app Sales feature"
    assert lines[0].startswith("Feature: .... ")
    assert not lines[0].startswith("Feature: ..... ")

    for line in lines:
        assert not line.strip().startswith("@"), (
            f"Sales.feature declares the tag {line.strip()!r}; the source file "
            f"carries none, and the @Smoke default therefore skips it"
        )


def test_sales_feature_background_supplies_the_shared_precondition(
    resolve_step: Any,
) -> None:
    """``Sales.feature:5-10``: one Background, one shared ``Given`` at ``:10``.

    The precondition is ``Session.java``'s only definition, declared
    ``@When("User login to test other features")`` at ``Session.java:12`` and
    invoked as a ``Given`` here - which is exactly why every definition in the
    port registers with ``@step`` (AAP deviation 7).  It resolves to
    ``session_steps``, not to this module, and every other phrase in the file
    resolves into this module.
    """
    precondition = "User login to test other features"
    lines = feature_lines()

    assert any(line.strip().startswith("Background:") for line in lines)
    assert f"Given {precondition}" in [line.strip() for line in lines]
    assert resolve_step(precondition).module_name == "session_steps"


def test_every_sales_feature_phrase_resolves_into_this_module(
    resolve_step: Any,
) -> None:
    """``Sales.feature:13-28``: twelve invocations, all of them ours.

    Five step lines in the first scenario (``:13-17``), four in the second
    (``:20-23``) and three in the outline (``:26-28``).  The Background's
    precondition aside, every one of them - including the outline's
    ``"<name>"``, which the parameterized pattern matches as the literal text
    it is - resolves to a definition of ``sales_steps``, and between them the
    twelve
    invocations reach all seven definitions: the six unparameterized patterns
    plus the parameterized one.
    """
    precondition = "User login to test other features"
    phrases = [phrase for phrase in feature_phrases() if phrase != precondition]

    assert len(phrases) == 12

    for phrase in phrases:
        match = resolve_step(phrase)
        assert match.module_name == STEP_MODULE_NAME, (
            f"Sales.feature invokes {phrase!r}, which resolves to "
            f"{match.module_name!r}"
        )
        assert match.bucket == STEP_BUCKET

    assert {resolve_step(phrase).pattern for phrase in phrases} == {
        definition.pattern for definition in DEFINITIONS
    }, "every one of the seven definitions must be invoked by the feature"


# =========================================================================== #
# The enumeration AAP 0.4.1 requires, last so that it sees the whole module
#
# Two records are reconciled, and it takes both to make the enumeration real
# rather than declarative:
#
#   * :data:`PARITY_TESTS` is the static declaration - each definition's
#     pattern against the tests that drive its body - and it is compared
#     against the Java census, so adding, deleting or re-wording a definition
#     fails, and a declared test that no longer exists fails by name;
#   * :data:`_COVERED_PATTERNS` is the run-time record, so a test that still
#     exists but no longer reaches its step fails too.
#
# :data:`_EXECUTED_TESTS` is what keeps the reconciliation honest under a
# partial selection: a pattern may be uncovered only when none of its declared
# tests ran.
# =========================================================================== #

#: Each definition's pattern against the tests that drive its body and record
#: the coverage.  Only the recording tests are named: the whole-flow tests run
#: all seven bodies without recording, deliberately, because a set they filled
#: would mask the omission this bookkeeping exists to catch.
PARITY_TESTS: Final[dict[str, tuple[str, ...]]] = {
    "User click on the sales dashboard": (
        "test_dashboard_step_clicks_then_waits_on_the_same_element",
    ),
    "User click customers button": (
        "test_customers_step_clicks_waits_and_passes_on_the_bare_title",
        "test_customers_step_fails_with_the_exact_java_message",
    ),
    "User can create the customer": (
        "test_create_the_customer_step_fills_the_form_in_java_order",
    ),
    "User can save the customer": (
        "test_save_the_customer_step_pairs_click_and_wait_three_times",
    ),
    'User can find his name "{name}" from search bar': (
        "test_search_step_reads_the_heading_and_compares_nothing",
        "test_search_step_binds_the_java_name_literal_and_reads_it_nowhere",
    ),
    "User can create new customer": (
        "test_create_new_customer_step_ends_on_an_unwaited_click",
    ),
    "User can get the error": (
        "test_error_step_reads_the_warning_area_and_compares_nothing",
        "test_error_step_binds_the_java_warning_literal_and_reads_it_nowhere",
    ),
}


def test_every_java_definition_has_a_behaviour_test() -> None:
    """All seven ``Sales.java`` methods must be driven by this module.

    AAP 0.4.1: "a step method with no corresponding assertion in its module's
    test is a gap, and the module test enumerates the Java class's methods so
    an omission fails rather than passes silently".  This is that enumeration,
    taken against the seven annotations at
    ``Sales.java:19 :25 :40 :54 :66 :80 :87``, and it fails three ways: a
    definition with nothing declared against it, or a declaration naming a
    pattern the class does not declare; a declared test this module does not
    define under that name, which is what deleting a per-step test does; and a
    declared test that ran without ever resolving its step.  The first two hold
    however few tests were selected, and the third is asked only of the
    patterns whose declared tests actually executed, so a ``-k`` run, a node id
    or a distributed run reports a gap exactly where one genuinely exists.
    Written last, so a whole-module run reaches it after every recording test.
    """
    expected = {definition.pattern for definition in DEFINITIONS}

    assert set(PARITY_TESTS) == expected, (
        f"declared {sorted(set(PARITY_TESTS) - expected)} and missing "
        f"{sorted(expected - set(PARITY_TESTS))}. Every method of "
        f"Sales.java needs a behaviour test; add it rather than relaxing this "
        f"assertion"
    )
    assert len(expected) == 7

    for pattern, test_names in PARITY_TESTS.items():
        assert test_names, f"{pattern!r} declares no parity test"

        for name in test_names:
            candidate = globals().get(name)

            assert callable(candidate), (
                f"{pattern!r} names a parity test {name!r} that this module "
                f"does not define, so the step body it names is unasserted"
            )
            assert name.startswith("test_")

    unexpected = sorted(_COVERED_PATTERNS - expected)
    assert not unexpected, f"coverage recorded for unknown patterns: {unexpected!r}"

    for definition in DEFINITIONS:
        if definition.pattern in _COVERED_PATTERNS:
            continue

        executed = [
            name for name in PARITY_TESTS[definition.pattern] if name in _EXECUTED_TESTS
        ]

        assert not executed, (
            f"Sales.java:{definition.java_line} {definition.pattern!r} is "
            f"untested: {executed} ran without ever resolving it, so the step "
            f"body it names is unasserted"
        )
