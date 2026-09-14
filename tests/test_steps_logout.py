"""Behavioural parity tests for ``features/steps/logout_steps.py``.

Authority: ``step_definitions/LogOutSD.java`` at pinned revision
``47e9d697e4a9a85da889f94a846fdf47af28a240`` - three ``@Then`` methods at
``:16``, ``:23`` and ``:30``, bodies at ``:18-20``, ``:25-27`` and ``:32-33`` -
with its page object ``pages/LogOutP.java``, whose three ``@FindBy`` fields sit
at ``:14``, ``:17`` and ``:20``.  Every expectation is transcribed from those
two files into a module constant, so the port is the subject under test and
never the source of an expectation.  This discharges AAP 0.4.1's per-module
obligation - the same observable operations in the same order for every method
of the Java class - and enumerates those methods, so an omitted definition
fails rather than passing silently.  Three class facts carry most of the
assertions:

* the ``WebDriverWait`` at ``:13`` precedes the page field at ``:15``, carries a
  **3-second** timeout, and is used once: at ``:18``, on the account menu alone,
  ``:20`` clicking the log-out link with no wait of its own.  The call site
  passes the page's locator constant and that timeout, and
  ``visibility_of_element_located`` resolves the locator inside the wait;
* all three definitions are declared ``@Then`` while ``Logout.feature:19``,
  ``:43`` and ``:44`` reach two of them under an effective ``When``, so the port
  registers every definition with ``@step`` (AAP deviation 7);
* nothing in the class creates, configures or quits a WebDriver - logging out is
  not WebDriver teardown, which AAP 0.3.3 gives to ``features/environment.py``.

Waits are intercepted in the step module's own globals by
:func:`_install_wait_recorder` - ``waits.py``'s ``_until`` would otherwise
provision a browser through ``get_driver()`` - and land in the driver's ordered
log, so a wait's target, timeout and position read as one sequence with the
page operations around it.  Nothing here starts a browser, opens a socket,
reads configuration or sleeps, and per AAP deviation 16 only assertion subjects
and message text are asserted, never JUnit's ``expected:<...> but was:<...>``
framing, which Python cannot produce.
"""

from __future__ import annotations

import ast
import importlib
import re
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Final, NamedTuple

import pytest

# ``By`` comes from ``app.automation``, the port's single authorised re-export
# of a browser-library name (AAP 0.4.2).  It is imported so that the locator
# assertions below can close the chain Java ``@FindBy(className = ...)`` ->
# ``By.CLASS_NAME`` -> the wire value the driver records, without this module
# importing selenium itself.
from app.automation import By
from app.pages import LogOutPage

# --------------------------------------------------------------------------
# Locations
# --------------------------------------------------------------------------

#: Repository root, from this file's own position: tests/ -> repository root.
REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]

#: The step module under test.  Read as text for the source-inspection
#: assertions, which is the only way to see a decorator or an import statement:
#: behave injects ``given``, ``when``, ``then`` and ``step`` into every step
#: module's globals whether the file names them or not, so the runtime
#: namespace cannot answer which decorator a definition actually used.
STEP_MODULE_PATH: Final[Path] = REPO_ROOT / "features" / "steps" / "logout_steps.py"

#: The feature file the three definitions serve.
FEATURE_PATH: Final[Path] = REPO_ROOT / "features" / "Logout.feature"

#: This file, re-read by the gap detector in :func:`_phrases_exercised_here`.
THIS_MODULE_PATH: Final[Path] = Path(__file__).resolve()

#: The step module's name as behave reports it through ``StepMatch.location``.
STEP_MODULE_NAME: Final[str] = "logout_steps"

# --------------------------------------------------------------------------
# The Java authority: the census of LogOutSD.java:10-35, transcribed by line
# --------------------------------------------------------------------------


class JavaStepMethod(NamedTuple):
    """One ``@Then``-annotated method of ``LogOutSD.java:16-34``.

    The enumeration AAP 0.4.1 requires: three entries, one per method of the
    class, each carrying the line its annotation sits on, the Java method name
    and the byte-exact phrase that annotation declares.
    """

    #: Line of the ``@Then(...)`` annotation in ``LogOutSD.java``:
    #: ``:16``, ``:23`` or ``:30``.
    annotation_line: int

    #: The Java method name.  Note entry 1: the method is named
    #: ``user_clicks_the_account_icon_and_then_click_log_out_option`` while its
    #: annotation says ``User click Log out option``.  The **phrase** is the
    #: contract, so the port names its function after the phrase and the
    #: mismatch is not carried over - which is why the Python function name is
    #: not asserted against this field.
    method_name: str

    #: The Gherkin phrase, byte-exact, as the annotation declares it.
    phrase: str

    #: First and last line of the method body in ``LogOutSD.java``:
    #: ``:18`` - ``:20``, ``:25`` - ``:27`` or ``:32`` - ``:33``.
    body_lines: tuple[int, int]


#: The complete inventory of ``LogOutSD.java``, in declaration order.  Three
#: entries, because the class declares three methods and nothing else beyond
#: the two fields at ``:13`` and ``:15``.
JAVA_STEP_METHODS: Final[tuple[JavaStepMethod, ...]] = (
    JavaStepMethod(
        annotation_line=16,
        method_name="user_clicks_the_account_icon_and_then_click_log_out_option",
        phrase="User click Log out option",
        body_lines=(18, 20),
    ),
    JavaStepMethod(
        annotation_line=23,
        method_name="user_should_see_the_login_dashboard",
        phrase="User should see the login dashboard",
        body_lines=(25, 27),
    ),
    JavaStepMethod(
        annotation_line=30,
        method_name="user_can_not_click_the_step_back_button_to_go_the_home_page",
        phrase="User can not click the step back button to go the home page",
        body_lines=(32, 33),
    ),
)

#: The three phrases alone, in Java declaration order.
JAVA_PHRASES: Final[tuple[str, ...]] = tuple(
    method.phrase for method in JAVA_STEP_METHODS
)

#: Named individually, so that a behavioural test below mentions its phrase as
#: a literal and the gap detector can see it.
PHRASE_LOG_OUT: Final[str] = "User click Log out option"
PHRASE_LOGIN_DASHBOARD: Final[str] = "User should see the login dashboard"
PHRASE_STEP_BACK: Final[str] = (
    "User can not click the step back button to go the home page"
)

#: ``LogOutSD.java:13`` - ``new WebDriverWait(Driver.getDriver(),3)``.  The
#: class's single timeout, used once, at ``:18``.  AAP 0.4.1 fixes a different
#: timeout per step class (2s, 3s, 4s and 20s across the nine wait sites), so
#: this number is the one thing that distinguishes this class's wait from
#: another's and it is asserted as a value rather than as a default.
WAIT_TIMEOUT_SECONDS: Final[int] = 3

#: ``LogOutSD.java:25`` - byte-exact, literal pipe included.
EXPECTED_LOGIN_TITLE: Final[str] = "Login | Best solution for startups"

#: ``LogOutSD.java:27`` - the ``assertEquals`` message, which ends in **one
#: trailing space**.  That space is in the source (``LoginSD.java:46`` shares
#: it; ``Calendar.java:46`` and ``Sales.java:37`` do not) and AAP 0.8's
#: *"preserve, do not tidy"* keeps it, because the message text is part of the
#: parity contract.
TITLE_ASSERTION_MESSAGE: Final[str] = "The title is not same as the expected! "

#: Titles that must each fail the comparison at ``LogOutSD.java:27``.  Every
#: one is a single-character or single-token departure from
#: :data:`EXPECTED_LOGIN_TITLE`, which is what makes the comparison's
#: byte-exactness an assertion instead of a claim.
NEAR_MISS_TITLES: Final[tuple[str, ...]] = (
    "",
    "login | Best solution for startups",
    "Login - Best solution for startups",
    "Login |Best solution for startups",
    "Login | Best solution for startups ",
    "Login | Best solution for startup",
    "Odoo",
)


class LocatorAuthority(NamedTuple):
    """One ``@FindBy`` field of ``LogOutP.java:14-21`` and its port constant."""

    #: Name of the constant on :class:`app.pages.LogOutPage`.
    constant: str

    #: The ``(strategy, selector)`` pair the constant must equal, built from
    #: :data:`By` so the strategy is named rather than spelled.
    expected: tuple[str, str]

    #: The strategy's wire value - the string the driver records in its log.
    wire_strategy: str

    #: Line of the ``@FindBy`` annotation in ``LogOutP.java``: ``:14``, ``:17``
    #: or ``:20``, each immediately above the ``WebElement`` it decorates.
    java_line: int

    #: The annotation's argument, transcribed from the Java source.
    java_findby: str


#: The three ``@FindBy`` fields of ``LogOutP.java:14-21``, in declaration
#: order, which is also the order :attr:`app.pages.LogOutPage.LOCATORS` holds.
LOCATOR_AUTHORITY: Final[tuple[LocatorAuthority, ...]] = (
    LocatorAuthority(
        constant="POP_UP_BUTTON",
        expected=(By.CLASS_NAME, "o_user_menu"),
        wire_strategy="class name",
        java_line=14,
        java_findby='className = "o_user_menu"',
    ),
    LocatorAuthority(
        constant="LOG_OUT_BUTTON",
        expected=(By.XPATH, "//a[.='Log out']"),
        wire_strategy="xpath",
        java_line=17,
        java_findby="xpath = \"//a[.='Log out']\"",
    ),
    LocatorAuthority(
        constant="WARNING_MESS",
        # The space between ``@class=`` and the opening quote is in
        # ``LogOutP.java:20`` character for character.  XPath treats whitespace
        # around a predicate's ``=`` as insignificant, so the selector behaves
        # identically to the tidied form - which is precisely why it must not be
        # tidied: AAP 0.2.2 permits correcting a source quirk only when it
        # appears in the AAP 0.1.3 deviation inventory, and this one does not.
        expected=(By.XPATH, "//div[@class= 'o_dialog_warning modal-body']"),
        wire_strategy="xpath",
        java_line=20,
        java_findby="xpath = \"//div[@class= 'o_dialog_warning modal-body']\"",
    ),
)

# --------------------------------------------------------------------------
# Operation names in the ordered log
#
# ``tests/conftest.py`` logs element operations as ``element.<op>`` with the
# originating locator as the first argument (its ``ELEMENT_PREFIX``), and driver
# operations under their own names.  They are restated here as constants so an
# assertion reads as a sequence of operations rather than a wall of strings.
# --------------------------------------------------------------------------

FIND_ELEMENT: Final[str] = "find_element"
FIND_ELEMENTS: Final[str] = "find_elements"
ELEMENT_CLICK: Final[str] = "element.click"
ELEMENT_IS_DISPLAYED: Final[str] = "element.is_displayed"
TITLE_READ: Final[str] = "title"
BACK: Final[str] = "back"

#: This module's own synthetic log entry for an intercepted explicit wait,
#: appended to the driver's log so that waits and page operations share one
#: ordered sequence.  Its arguments are ``(locator, timeout)``: the locator
#: constant the call site passed, and that call site's own timeout.
WAIT: Final[str] = "wait"

#: Lookups are filtered out of a sequence assertion by :func:`_actions`,
#: because each lower-case accessor resolves its element on every access
#: (``LogOutP.java:11``'s un-cached ``PageFactory`` proxy) and the count of
#: those resolutions is mechanics rather than parity.  The intercepted wait
#: contributes none of them: it is handed a locator and
#: ``visibility_of_element_located`` would resolve it inside the wait.  Each
#: operation's own preceding lookup is asserted separately, by
#: :func:`_lookup_precedes_each`.
LOOKUP_OPERATIONS: Final[frozenset[str]] = frozenset({FIND_ELEMENT, FIND_ELEMENTS})

# --------------------------------------------------------------------------
# The wait seam
# --------------------------------------------------------------------------


class WaitCall(NamedTuple):
    """One intercepted explicit wait: what ``LogOutSD.java:18`` fixes."""

    #: The locator constant the call site passed, as the very object
    #: :attr:`app.pages.LogOutPage.POP_UP_BUTTON` holds, so an assertion on it
    #: can use ``is`` and not only ``==``.
    locator: tuple[str, str]

    #: The timeout in seconds, exactly as the call site supplied it.
    timeout: float


def _target_locator(value: Any) -> tuple[str, str]:
    """Require the wait's target to be one of ``LogOutPage``'s constants.

    ``LogOutSD.java:18`` waits on ``ExpectedConditions.visibilityOf`` applied to
    the ``PageFactory`` field declared at ``LogOutP.java:14-15``, and that field
    was a proxy that re-located on every touch: the lookup happened *inside* the
    predicate, once per poll, governed by the wait's own 3 seconds
    (``LogOutSD.java:13``).  ``app/automation/waits.py``'s
    ``wait_visible_element`` reproduces that with
    ``visibility_of_element_located``, which takes the **locator**.  A call site
    that resolved the element first - through the lower-case accessor - would
    move the lookup out of the wait and under the session's 10-second implicit
    wait, so an element-shaped target is a parity failure here rather than an
    alternative spelling, and is rejected.

    The check is by **identity** against the page class attribute, which
    :attr:`app.pages.LogOutPage.LOCATORS` exposes by name: a tuple rebuilt at
    the call site would satisfy an equality comparison while spelling the
    selector a second time, outside ``LogOutP.java``'s authority.

    :param value: The wait's target argument, exactly as it arrived.
    :returns: That same locator object, so identity survives into
        :class:`WaitCall` and can be asserted at the call site.
    :raises AssertionError: If *value* is not one of the three locator
        constants - an element, a rebuilt tuple and a locator belonging to
        another page object all fail here.
    """
    if any(value is locator for locator in LogOutPage.LOCATORS.values()):
        return value

    raise AssertionError(
        f"wait target {value!r} is not a LogOutPage locator constant: "
        f"LogOutSD.java:18 waits on a locator that "
        f"visibility_of_element_located resolves inside the wait, so the call "
        f"site must pass the upper-case constant itself, one of "
        f"{sorted(LogOutPage.LOCATORS)}"
    )


def _sole_timeout(values: Iterable[Any]) -> float:
    """Pick the one numeric argument out of a wait call's arguments.

    Read as *the numeric argument*, positional or keyword, so the timeout is
    asserted as a value and the helper's signature stays free to change.

    :param values: Every argument of the call except its target.
    :returns: The timeout in seconds.
    :raises AssertionError: If the call carried no numeric argument or more
        than one, in which case which number is the timeout is not decidable
        and guessing would make the assertion meaningless.
    """
    numbers = [
        value
        for value in values
        # ``bool`` is an ``int`` in Python and ``True == 1``; a flag passed
        # where a timeout belongs must not be read as three seconds.
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]

    if len(numbers) != 1:
        raise AssertionError(
            f"expected exactly one numeric timeout argument in the wait call, "
            f"found {numbers!r}"
        )

    return numbers[0]


class WaitRecorder:
    """Stands in for the step module's explicit-wait helper.

    Records the wait's target locator and timeout and appends the pair to the
    driver's own ordered log, so that waits and page operations interleave in
    one sequence.  The target is handed back, which keeps the substitution
    transparent to a body that used the return value; no body of
    ``LogOutSD.java`` does, since ``:19`` and ``:20`` reach their elements
    through the page object's accessors.

    The target must be a ``LogOutPage`` locator constant - the shape
    ``visibility_of_element_located`` requires - and :func:`_target_locator`
    rejects anything else.  The timeout is read as *the* numeric argument,
    positional or keyword, which are the two forms
    ``app/automation/waits.py``'s ``wait_visible_element(locator, timeout)``
    signature admits.  The name the recorder is installed under is discovered
    at run time by :func:`_install_wait_recorder`.
    """

    __slots__ = ("_driver", "calls")

    def __init__(self, driver: Any) -> None:
        """Bind the recorder to the driver whose log it appends to.

        :param driver: The suite's ``StubDriver``, whose ``calls`` list carries
            the single interleaved sequence this module asserts on.
        """
        self._driver = driver

        #: One :class:`WaitCall` per intercepted wait, in call order.
        self.calls: list[WaitCall] = []

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """Record one wait and return its target.

        :param args: Positional arguments as the call site passed them.
        :param kwargs: Keyword arguments as the call site passed them.
        :returns: The wait's target locator, unchanged.
        :raises AssertionError: If the target is not a ``LogOutPage`` locator
            constant, or if no single numeric timeout can be identified among
            the arguments.
        """
        candidates = [*args, *kwargs.values()]

        if not candidates:
            raise AssertionError("the wait helper was called with no arguments")

        target = candidates[0]
        locator = _target_locator(target)
        timeout = _sole_timeout(candidates[1:])

        self.calls.append(WaitCall(locator=locator, timeout=timeout))
        self._driver.calls.append((WAIT, (locator, timeout)))

        return target


def _install_wait_recorder(
    monkeypatch: pytest.MonkeyPatch, match: Any, driver: Any
) -> WaitRecorder:
    """Replace every wait binding in the step module's globals with a recorder.

    behave's ``load_step_modules`` execs each step file into its own globals
    dict, and a step body reaches its wait helper through that dict, so this is
    the seam that intercepts the call.  It is mandatory rather than convenient:
    left alone, ``app/automation/waits.py``'s ``_until`` would call
    ``get_driver()`` and try to provision a real browser.

    The binding's **name is discovered**, never assumed: every callable global
    whose name begins with ``wait`` is replaced, so the interception survives a
    change of wait helper at the call site.

    :param monkeypatch: pytest's patcher, which undoes the substitution after
        the test.
    :param match: The resolved ``StepMatch`` whose function owns the globals.
    :param driver: The recorder the wait entries are appended to.
    :returns: The installed :class:`WaitRecorder`.
    :raises AssertionError: If the step module binds no wait helper at all -
        which would mean the 3-second wait of ``LogOutSD.java:13`` had been
        dropped, and is reported here rather than left to fail as a browser
        launch.
    """
    recorder = WaitRecorder(driver)
    names = sorted(
        name
        for name, value in match.func.__globals__.items()
        if name.startswith("wait") and callable(value)
    )

    assert names, (
        f"{STEP_MODULE_NAME} binds no wait helper, so the 3-second wait of "
        f"LogOutSD.java:13 cannot be performed"
    )

    for name in names:
        monkeypatch.setitem(match.func.__globals__, name, recorder)

    return recorder


# --------------------------------------------------------------------------
# Reading the ordered log
# --------------------------------------------------------------------------


def _actions(driver: Any) -> tuple[tuple[str, tuple[Any, ...]], ...]:
    """The ordered log with element lookups removed.

    Lookups are mechanics: a page-object accessor resolves its element on every
    access (``LogOutP.java:11``), so their number tracks how many accessors a
    body reads rather than anything ``LogOutSD.java`` declares.  What the Java
    class fixes is the sequence of *actions* - the wait of ``:18``, the clicks
    of ``:19`` and ``:20``, the title read of ``:26``, the back navigation of
    ``:32`` - and that is what this view exposes.  The intercepted wait adds no
    lookup of its own, because it is given a locator that
    ``visibility_of_element_located`` would resolve inside the wait.

    :param driver: The suite's ``StubDriver``.
    :returns: The ``(operation, args)`` entries that are not lookups, in order.
    """
    return tuple(
        (operation, args)
        for operation, args in driver.calls
        if operation not in LOOKUP_OPERATIONS
    )


def _lookup_precedes_each(driver: Any, operation: str) -> None:
    """Assert each occurrence of *operation* is preceded by its own lookup.

    The observable form of ``PageFactory``'s un-cached proxy
    (``LogOutP.java:11``), which ``app/pages/base_page.py`` reproduces by never
    caching: the element an operation acts on is resolved at the point of use,
    against the page as it is then, and the locator resolved is the locator the
    operation is logged under.

    :param driver: The suite's ``StubDriver``.
    :param operation: The logged element operation, such as ``element.click``.
    :returns: ``None``.
    :raises AssertionError: If any occurrence is not immediately preceded by a
        ``find_element`` for the same locator.
    """
    for position, (name, args) in enumerate(driver.calls):
        if name != operation:
            continue

        assert position > 0, (
            f"{operation} at position {position} was performed without any "
            f"element lookup before it"
        )

        previous_name, previous_args = driver.calls[position - 1]

        assert (previous_name, previous_args) == (FIND_ELEMENT, args[0]), (
            f"{operation} on {args[0]!r} was not preceded by a lookup of that "
            f"locator; the entry before it is {(previous_name, previous_args)!r}"
        )


# --------------------------------------------------------------------------
# Source inspection
# --------------------------------------------------------------------------


class ImportedName(NamedTuple):
    """One name a module imports, with the module it came from."""

    #: The dotted module name, ``""`` for a plain ``import x`` of a top-level
    #: module, which is recorded under the imported name instead.
    module: str

    #: The imported name as written in the statement.
    name: str


def _imported_names(tree: ast.Module) -> tuple[ImportedName, ...]:
    """Every name *tree* imports, from every import statement it contains.

    Walks the whole tree rather than its top level, so an import hidden inside
    a function body or a ``TYPE_CHECKING`` block is found too - a guarded
    import is still an import statement.

    :param tree: The parsed module.
    :returns: One entry per imported name, in source order.
    """
    found: list[ImportedName] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(
                ImportedName(module="", name=alias.name) for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            found.extend(
                ImportedName(module=module, name=alias.name) for alias in node.names
            )

    return tuple(found)


def _decorator_names(node: ast.FunctionDef) -> tuple[str, ...]:
    """The decorator names applied to one function, called or bare.

    :param node: The function definition.
    :returns: The decorator names in source order; a dotted decorator is
        rendered as its attribute name, which is enough to recognise
        ``behave.given`` as ``given``.
    """
    names: list[str] = []

    for decorator in node.decorator_list:
        expression = decorator.func if isinstance(decorator, ast.Call) else decorator

        if isinstance(expression, ast.Name):
            names.append(expression.id)
        elif isinstance(expression, ast.Attribute):
            names.append(expression.attr)

    return tuple(names)


def _decorated_functions(tree: ast.Module) -> tuple[ast.FunctionDef, ...]:
    """Every top-level function of *tree* that carries at least one decorator.

    Top level only, and deliberately: a step definition is registered at import
    time, so a nested function could not be one.

    :param tree: The parsed module.
    :returns: The decorated top-level functions, in source order.
    """
    return tuple(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.decorator_list
    )


def _called_names(tree: ast.AST) -> frozenset[str]:
    """The name of every function called anywhere in *tree*.

    :param tree: Any parsed node - a whole module or one function body.
    :returns: Bare names for ``f()``, attribute names for ``x.f()``.
    """
    called: set[str] = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        if isinstance(node.func, ast.Name):
            called.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            called.add(node.func.attr)

    return frozenset(called)


def _reached_names(tree: ast.AST) -> frozenset[str]:
    """Every name *tree* calls or otherwise mentions as an identifier.

    Broader than :func:`_called_names` by the identifiers that are referenced
    without being called, which is what catches a name reached through an
    attribute or bound to a local before use.  String literals are not
    identifiers and are deliberately not included, so a docstring naming
    something cannot satisfy or break one of these assertions.

    :param tree: Any parsed node.
    :returns: The identifiers reached.
    """
    return _called_names(tree) | frozenset(
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    )


def _definitions_by_phrase(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    """Map each declared phrase to the function its decorator sits on.

    :param tree: The parsed step module.
    :returns: ``{phrase: function definition}`` for every decorated top-level
        function carrying a string argument in its decorator.
    """
    mapping: dict[str, ast.FunctionDef] = {}

    for node in _decorated_functions(tree):
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue

            for argument in decorator.args:
                if isinstance(argument, ast.Constant) and isinstance(
                    argument.value, str
                ):
                    mapping[argument.value] = node

    return mapping


def _phrases_exercised_here() -> dict[str, tuple[str, ...]]:
    """Map each census phrase to the tests in this file that name it.

    The gap detector AAP 0.4.1 asks for, read off this module's own source: a
    Java method whose phrase appears in no test body has no behavioural
    assertion, and :func:`test_every_census_phrase_is_exercised_by_a_test`
    turns that into a failure.  Source inspection rather than a hand-kept list,
    so the map cannot drift from the tests it describes.

    :returns: ``{phrase: (test function names, ...)}`` for every census phrase,
        with an empty tuple for one no test mentions.
    """
    tree = ast.parse(THIS_MODULE_PATH.read_text(encoding="utf-8"))
    exercised: dict[str, list[str]] = {phrase: [] for phrase in JAVA_PHRASES}

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
            continue

        literals = {
            child.value
            for child in ast.walk(node)
            if isinstance(child, ast.Constant) and isinstance(child.value, str)
        }

        for phrase in JAVA_PHRASES:
            if phrase in literals:
                exercised[phrase].append(node.name)

    return {phrase: tuple(names) for phrase, names in exercised.items()}


def _registered_patterns(registry: Any) -> dict[str, tuple[str, ...]]:
    """Group the patterns the step module registers by registry bucket.

    :param registry: behave's populated ``StepRegistry``.
    :returns: ``{bucket: (pattern, ...)}`` for every bucket in which
        ``logout_steps`` registered at least one definition.
    """
    def owner_of(matcher: Any) -> str:
        """The stem of the file a matcher was registered from."""
        filename = str(getattr(matcher.location, "filename", "") or "")
        return Path(filename).stem

    grouped: dict[str, tuple[str, ...]] = {}

    for bucket, matchers in registry.steps.items():
        patterns = tuple(
            str(matcher.pattern)
            for matcher in matchers
            if owner_of(matcher) == STEP_MODULE_NAME
        )

        if patterns:
            grouped[bucket] = patterns

    return grouped


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

#: The lifecycle entry points no step body may reach, and the modules that hold
#: a binding to them.  ``app.automation.waits`` has its own, which is the one a
#: wait would use if the interception ever failed.
LIFECYCLE_MODULES: Final[tuple[str, ...]] = (
    "app.automation",
    "app.automation.driver",
    "app.automation.waits",
)
LIFECYCLE_FUNCTIONS: Final[tuple[str, ...]] = ("get_driver", "quit_driver")


@pytest.fixture(autouse=True)
def forbid_driver_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any reach for the session lifecycle fail loudly, in every test here.

    Two jobs in one fixture.  It **guarantees no test in this module launches a
    browser**, which a wait escaping interception would otherwise do through
    ``waits._until``'s ``get_driver()``.  And it is the runtime half of the
    separation AAP 0.3.3 states - *"no step or page ever creates or quits a
    driver"* - which matters most in this area of all, because the feature under
    test is about logging out: application logout is not WebDriver teardown, and
    ``features/environment.py`` ends the session after every scenario anyway, so
    a teardown in a step body would be both a boundary violation and a double
    quit.

    :param monkeypatch: pytest's patcher; every substitution is undone after the
        test, so nothing leaks into another module's tests.
    :returns: ``None``.
    """

    def _refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(
            "a logout step reached the WebDriver lifecycle; LogOutSD.java "
            "neither creates nor quits a driver (AAP 0.3.3)"
        )

    for module_name in LIFECYCLE_MODULES:
        module = importlib.import_module(module_name)

        for function_name in LIFECYCLE_FUNCTIONS:
            if hasattr(module, function_name):
                monkeypatch.setattr(module, function_name, _refuse)


@pytest.fixture
def prepare(
    resolve_step: Callable[[str], Any],
    stub_driver: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[str], tuple[Any, WaitRecorder]]:
    """Resolve a phrase and arm the wait recorder, without running the step.

    Two steps rather than one, so a test can programme the page's answers
    between resolution and execution::

        match, waits = prepare(PHRASE_LOGIN_DASHBOARD)
        stub_driver.title = EXPECTED_LOGIN_TITLE
        match.run(fake_context)

    :param resolve_step: The suite's registry-backed resolver.
    :param stub_driver: The recorder the waits are logged into.
    :param monkeypatch: Passed through to :func:`_install_wait_recorder`.
    :returns: A callable taking a phrase and returning ``(match, recorder)``.
    """

    def _prepare(phrase: str) -> tuple[Any, WaitRecorder]:
        match = resolve_step(phrase)
        return match, _install_wait_recorder(monkeypatch, match, stub_driver)

    return _prepare


@pytest.fixture(scope="module")
def step_module_source() -> str:
    """The text of ``features/steps/logout_steps.py``.

    :returns: The file's contents, decoded as UTF-8.
    """
    return STEP_MODULE_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def step_module_tree(step_module_source: str) -> ast.Module:
    """``features/steps/logout_steps.py`` parsed once for the whole module.

    :param step_module_source: The source text.
    :returns: The parsed syntax tree.
    """
    return ast.parse(step_module_source, filename=str(STEP_MODULE_PATH))


@pytest.fixture(scope="module")
def feature_lines() -> tuple[str, ...]:
    """``features/Logout.feature`` as lines, indexed from zero.

    :returns: The file's lines with their terminators removed.
    """
    return tuple(FEATURE_PATH.read_text(encoding="utf-8").splitlines())


# ==========================================================================
# 1. The census: three methods, three definitions, three exercised phrases
# ==========================================================================


def test_java_census_is_the_three_methods_of_logoutsd() -> None:
    """Pins the census itself: three methods, unique phrases, ascending lines.

    ``LogOutSD.java`` declares exactly three ``@Then`` methods, at ``:16``,
    ``:23`` and ``:30``.  A census with a duplicated or mistyped entry would
    make every enumeration test below weaker than it reads, so the enumeration
    is checked before it is used.
    """
    assert len(JAVA_STEP_METHODS) == 3
    assert len(set(JAVA_PHRASES)) == 3
    assert [method.annotation_line for method in JAVA_STEP_METHODS] == [16, 23, 30]
    assert (PHRASE_LOG_OUT, PHRASE_LOGIN_DASHBOARD, PHRASE_STEP_BACK) == JAVA_PHRASES

    for method in JAVA_STEP_METHODS:
        first, last = method.body_lines
        assert method.annotation_line < first <= last


@pytest.mark.parametrize(
    "method",
    JAVA_STEP_METHODS,
    ids=[f"LogOutSD.java:{method.annotation_line}" for method in JAVA_STEP_METHODS],
)
def test_each_census_phrase_resolves_to_one_definition_in_logout_steps(
    method: JavaStepMethod, resolve_step: Callable[[str], Any]
) -> None:
    """Every phrase of ``LogOutSD.java:16,23,30`` resolves, once, to this module.

    ``resolve_step`` scans all four registry buckets and fails on nothing
    matching *and* on more than one matching, so this covers the undefined step
    and the ambiguous one in a single assertion.  The pattern is compared
    byte-exactly against the Java annotation's text, and the definitions take no
    parameters - none of the three Java methods declares one.
    """
    match = resolve_step(method.phrase)

    assert match.module_name == STEP_MODULE_NAME
    assert match.pattern == method.phrase
    assert match.bucket == "step"
    assert (match.args, match.kwargs) == ((), {})
    assert callable(match.func)


@pytest.mark.parametrize(
    "method",
    JAVA_STEP_METHODS,
    ids=[f"LogOutSD.java:{method.annotation_line}" for method in JAVA_STEP_METHODS],
)
def test_each_definition_is_named_after_its_phrase_not_the_java_method(
    method: JavaStepMethod, resolve_step: Callable[[str], Any]
) -> None:
    """The port names each function after the phrase, which is the contract.

    It coincides with the Java method name for entries 2 and 3, and deliberately
    does not for entry 1: ``LogOutSD.java:17`` is named
    ``user_clicks_the_account_icon_and_then_click_log_out_option`` while its
    annotation at ``:16`` says ``User click Log out option``.  Cucumber matches
    on the annotation, never on the method name, so the phrase is what carries
    over and the source's internal mismatch is not reproduced.
    """
    expected_name = method.phrase.lower().replace(" ", "_")

    assert resolve_step(method.phrase).func.__name__ == expected_name

    if method.annotation_line == 16:
        assert method.method_name != expected_name, (
            "LogOutSD.java:17's method name is the one that diverges from its "
            "annotation; if it ever matched, this census entry is wrong"
        )
    else:
        assert method.method_name == expected_name


def test_logout_steps_registers_exactly_the_three_census_phrases(
    step_registry: Any,
) -> None:
    """Three definitions, no more: the omission and the addition both fail here.

    The registry side of the AAP 0.4.1 enumeration.  A dropped definition
    shrinks this set and a fourth phrase - one the three annotations of
    ``LogOutSD.java:16``, ``:23`` and ``:30`` do not declare - grows it, and
    either way the comparison against the census fails rather than passing
    silently.
    """
    registered = _registered_patterns(step_registry)

    assert set(registered) == {"step"}
    assert sorted(registered["step"]) == sorted(JAVA_PHRASES)
    assert len(registered["step"]) == 3


def test_no_definition_is_registered_under_a_keyword_bucket(
    step_registry: Any,
) -> None:
    """All three definitions use ``@step``, so no keyword bucket holds one.

    This is behavioural, not stylistic, and this class is the clearest case of
    it in the port.  All three Java declarations are ``@Then`` (``:16``,
    ``:23``, ``:30``), yet ``Logout.feature`` invokes definition 1 at lines 19
    and 43 and definition 2 at line 44 under an effective ``When``.  Registered
    against ``then`` alone, those three lines would each report an undefined
    step - hence AAP deviation 7, ``@step`` for every definition.
    """
    registered = _registered_patterns(step_registry)

    for bucket in ("given", "when", "then"):
        assert bucket not in registered, (
            f"{STEP_MODULE_NAME} registered {registered.get(bucket)!r} in the "
            f"{bucket!r} bucket; deviation 7 requires @step for every definition"
        )


def test_every_census_phrase_is_exercised_by_a_test() -> None:
    """The gap detector: a Java method with no behavioural test fails the suite.

    AAP 0.4.1 - *"a step method with no corresponding assertion in its module's
    test is a gap, and the module test enumerates the Java class's methods so an
    omission fails rather than passes silently"*.  The set of phrases this file
    exercises is read from this file's own syntax tree, so it cannot drift from
    the tests it describes, and it must equal the census exactly.
    """
    exercised = _phrases_exercised_here()
    unexercised = sorted(phrase for phrase, tests in exercised.items() if not tests)

    assert not unexercised, (
        f"no test in this module exercises {unexercised!r}; every method of "
        f"LogOutSD.java needs a behavioural assertion"
    )
    assert set(exercised) == set(JAVA_PHRASES)


# ==========================================================================
# 2. The locators, against the @FindBy declarations of LogOutP.java:14-21
# ==========================================================================


@pytest.mark.parametrize(
    "authority",
    LOCATOR_AUTHORITY,
    ids=[f"LogOutP.java:{item.java_line}" for item in LOCATOR_AUTHORITY],
)
def test_locator_constant_matches_its_java_findby_declaration(
    authority: LocatorAuthority,
) -> None:
    """Each constant equals the ``@FindBy`` of ``LogOutP.java:14,17,20``.

    Two assertions per locator, and both are needed.  The pair comparison
    against a :data:`By` member states the strategy by name, closing Java's
    ``className``/``xpath`` onto the port's constant; the wire-value comparison
    pins the string the driver actually records, which is what the operation
    assertions in sections 3 and 5 match against.
    """
    locator = getattr(LogOutPage, authority.constant)

    assert locator == authority.expected, (
        f"LogOutPage.{authority.constant} must port "
        f"LogOutP.java:{authority.java_line} (@FindBy({authority.java_findby}))"
    )
    assert locator[0] == authority.wire_strategy


def test_warning_mess_keeps_the_space_after_the_class_predicate() -> None:
    """``LogOutP.java:20``'s selector is byte-exact, internal space included.

    ``//div[@class= 'o_dialog_warning modal-body']`` carries a space between
    ``@class=`` and the opening quote.  XPath ignores whitespace around a
    predicate's ``=``, so the quirk is harmless at run time and therefore
    invisible to any behavioural test - which is why it is asserted here
    character for character instead.  Tidying it would be a source correction
    outside the AAP 0.1.3 deviation inventory, which AAP 0.2.2 forbids.
    """
    strategy, selector = LogOutPage.WARNING_MESS

    assert selector == "//div[@class= 'o_dialog_warning modal-body']"
    assert "@class= '" in selector
    assert "@class='" not in selector
    assert strategy == By.XPATH


def test_the_three_locators_are_the_whole_inventory_in_java_order() -> None:
    """``LogOutP.java`` declares three fields; the port declares three constants.

    Order is part of it: ``BasePage.LOCATORS`` is built from the class body in
    source order, and the order here is the ``@FindBy`` order of ``:14``,
    ``:17`` and ``:20``.  A fourth locator would mean behaviour the Java class
    cannot express, and is caught by the same comparison.
    """
    assert tuple(LogOutPage.LOCATORS) == tuple(
        authority.constant for authority in LOCATOR_AUTHORITY
    )
    assert dict(LogOutPage.LOCATORS) == {
        authority.constant: authority.expected for authority in LOCATOR_AUTHORITY
    }


# ==========================================================================
# 3. Definition 1 - LogOutSD.java:16-21
#
#     wait.until(ExpectedConditions.visibilityOf(logOutP.popUpButton));  // :18
#     logOutP.popUpButton.click();                                       // :19
#     logOutP.logOutButton.click();                                      // :20
# ==========================================================================


def test_log_out_waits_then_clicks_the_menu_then_the_link(
    prepare: Callable[[str], tuple[Any, WaitRecorder]],
    fake_context: Any,
    stub_driver: Any,
) -> None:
    """The three statements of ``LogOutSD.java:18-20``, in that order.

    One interleaved sequence assertion covering all three: the wait on the
    account menu comes first, then the menu click, then the log-out link click.
    The step returns nothing, as the Java method returns ``void``.
    """
    match, waits = prepare(PHRASE_LOG_OUT)

    assert match.run(fake_context) is None
    assert _actions(stub_driver) == (
        (WAIT, (LogOutPage.POP_UP_BUTTON, WAIT_TIMEOUT_SECONDS)),
        (ELEMENT_CLICK, (LogOutPage.POP_UP_BUTTON,)),
        (ELEMENT_CLICK, (LogOutPage.LOG_OUT_BUTTON,)),
    )
    assert len(waits.calls) == 1
    # Identity, not equality: the wait's first argument is the page class
    # attribute of ``LogOutP.java:14-15``, not a tuple of the same two strings
    # rebuilt at the call site and not an element resolved before the wait.
    assert waits.calls[0].locator is LogOutPage.POP_UP_BUTTON


def test_log_out_waits_on_the_account_menu_for_three_seconds(
    prepare: Callable[[str], tuple[Any, WaitRecorder]], fake_context: Any
) -> None:
    """``:18`` waits on the account menu, for the ``:13`` timeout of 3 seconds.

    The target is ``LogOutP.java:14-15``'s ``popUpButton`` and the timeout is
    the class's own ``WebDriverWait`` value (``LogOutSD.java:13``).  AAP 0.4.1
    fixes a different timeout per step class - 2s, 3s, 4s and 20s across the
    nine wait sites - so the number is asserted as a value at the call site,
    which is the only place it exists in the port: no helper declares a default
    and no module constant hides which one this class chose.

    The target is asserted **by identity** against the page class attribute.
    ``visibility_of_element_located`` resolves the locator it is given inside
    the wait, once per poll, so the constant itself is what the call site must
    pass: an element resolved beforehand would be looked up under the session's
    implicit wait instead, and an equal tuple rebuilt at the call site would
    duplicate ``LogOutP.java``'s selector.  Neither passes ``is``.
    """
    match, waits = prepare(PHRASE_LOG_OUT)
    match.run(fake_context)

    assert waits.calls == [
        WaitCall(locator=LogOutPage.POP_UP_BUTTON, timeout=WAIT_TIMEOUT_SECONDS)
    ]
    assert waits.calls[0].locator is LogOutPage.POP_UP_BUTTON
    assert waits.calls[0].timeout == 3
    assert not isinstance(waits.calls[0].timeout, bool)


def test_log_out_clicks_the_log_out_link_with_no_wait_of_its_own(
    prepare: Callable[[str], tuple[Any, WaitRecorder]],
    fake_context: Any,
    stub_driver: Any,
) -> None:
    """The wait of ``:18`` covers the account menu only, never the link at ``:20``.

    ``LogOutSD.java`` performs one ``wait.until`` and then two clicks, so the
    log-out link is clicked with no wait between the two - parity rather than
    oversight, and the reason none is added by the port.  Asserted from both
    ends: exactly one wait in the whole step, and nothing at all recorded
    between the two clicks.
    """
    match, waits = prepare(PHRASE_LOG_OUT)
    match.run(fake_context)

    actions = _actions(stub_driver)
    clicks = [
        position
        for position, (name, _) in enumerate(actions)
        if name == ELEMENT_CLICK
    ]

    assert len(waits.calls) == 1
    assert [name for name, _ in actions].count(WAIT) == 1
    assert clicks == [1, 2], "the two clicks of :19 and :20 must be consecutive"
    assert actions[0][0] == WAIT, "the only wait precedes both clicks"


def test_log_out_resolves_each_element_at_the_point_of_use(
    prepare: Callable[[str], tuple[Any, WaitRecorder]],
    fake_context: Any,
    stub_driver: Any,
) -> None:
    """Each click resolves its own element first, as the ``@FindBy`` proxy does.

    ``LogOutP.java:11``'s ``PageFactory`` proxy re-resolves on every access and
    ``app/pages/base_page.py`` reproduces that by never caching, so the menu is
    located again for the click at ``LogOutSD.java:19`` rather than reusing
    whatever the wait at ``:18`` looked at.  The wait itself resolves nothing
    before the page operations: it is handed
    ``LogOutPage.POP_UP_BUTTON``, and ``visibility_of_element_located`` does the
    lookup inside the wait.  So the assertion is that each operation is
    immediately preceded by a lookup of the locator it is logged under.
    """
    match, _ = prepare(PHRASE_LOG_OUT)
    match.run(fake_context)

    _lookup_precedes_each(stub_driver, ELEMENT_CLICK)

    assert stub_driver.calls_of(FIND_ELEMENT)[-2:] == (
        LogOutPage.POP_UP_BUTTON,
        LogOutPage.LOG_OUT_BUTTON,
    )
    assert stub_driver.count_of(FIND_ELEMENTS) == 0


def test_log_out_asserts_nothing_and_reads_no_page_state(
    prepare: Callable[[str], tuple[Any, WaitRecorder]],
    fake_context: Any,
    stub_driver: Any,
) -> None:
    """``:18-20`` are three actions: no assertion, no read, no navigation.

    The Java method's body contains no ``Assert`` call, reads neither the title
    nor any attribute, and navigates nowhere, so a hidden or absent menu cannot
    fail this step on an assertion - only the wait or a click can fail it.  The
    account menu is programmed *hidden* here to make that concrete: the step
    still completes, because nothing in it inspects visibility.
    """
    stub_driver.set_displayed(LogOutPage.POP_UP_BUTTON, False)
    match, _ = prepare(PHRASE_LOG_OUT)

    assert match.run(fake_context) is None

    performed = set(stub_driver.operations())

    assert performed.isdisjoint(
        {TITLE_READ, BACK, ELEMENT_IS_DISPLAYED, "current_url", "element.text"}
    )
    assert performed.isdisjoint({"get", "element.send_keys", "element.clear"})


# ==========================================================================
# 4. Definition 2 - LogOutSD.java:23-28
#
#     String expectedDashboard = "Login | Best solution for startups";   // :25
#     String actualDashboard = Driver.getDriver().getTitle();            // :26
#     Assert.assertEquals("The title is not same as the expected! ",     // :27
#                         expectedDashboard, actualDashboard);
# ==========================================================================


def test_login_dashboard_reads_the_title_and_nothing_else(
    prepare: Callable[[str], tuple[Any, WaitRecorder]],
    fake_context: Any,
    stub_driver: Any,
) -> None:
    """``:26`` reads the title; ``:23-27`` waits for nothing and locates nothing.

    The Java body touches no field of the page object and constructs no wait, so
    the whole observable effect of this step is one title read.  ``getTitle()``
    becomes the ``title`` property rather than a call, which is what the log
    records here.
    """
    match, waits = prepare(PHRASE_LOGIN_DASHBOARD)
    stub_driver.title = EXPECTED_LOGIN_TITLE

    assert match.run(fake_context) is None
    assert stub_driver.operations() == (TITLE_READ,)
    assert waits.calls == [], "LogOutSD.java:23-27 constructs no wait"


def test_login_dashboard_passes_on_the_exact_expected_title(
    prepare: Callable[[str], tuple[Any, WaitRecorder]],
    fake_context: Any,
    stub_driver: Any,
) -> None:
    """``:25``'s literal, byte for byte, is what makes the assertion pass.

    ``Login | Best solution for startups`` - a literal pipe with one space
    either side, and no trailing punctuation.  The passing case is asserted so
    the failing cases below cannot pass for the wrong reason.
    """
    match, _ = prepare(PHRASE_LOGIN_DASHBOARD)
    stub_driver.title = "Login | Best solution for startups"

    assert match.run(fake_context) is None
    assert stub_driver.title == EXPECTED_LOGIN_TITLE


@pytest.mark.parametrize("title", NEAR_MISS_TITLES, ids=repr)
def test_login_dashboard_fails_on_a_near_miss_title(
    title: str,
    prepare: Callable[[str], tuple[Any, WaitRecorder]],
    fake_context: Any,
    stub_driver: Any,
) -> None:
    """``:27`` compares for equality, so one character of difference fails it.

    Each title here departs from ``:25``'s literal by a single token - case, the
    separator, a space, a final letter - and every one of them must raise, which
    is what proves the comparison is an exact equality rather than a
    containment or a normalising match.
    """
    match, _ = prepare(PHRASE_LOGIN_DASHBOARD)
    stub_driver.title = title

    with pytest.raises(AssertionError):
        match.run(fake_context)


def test_login_dashboard_failure_message_keeps_its_trailing_space(
    prepare: Callable[[str], tuple[Any, WaitRecorder]],
    fake_context: Any,
    stub_driver: Any,
) -> None:
    """``:27``'s message is ``"The title is not same as the expected! "``.

    One trailing space, preserved byte for byte.  It is in the Java source -
    ``LoginSD.java:46`` shares it, while ``Calendar.java:46`` and
    ``Sales.java:37`` do not - and AAP 0.8's *"preserve, do not tidy"* keeps it,
    because the message text is part of the parity contract and reaches
    ``target/cucumber.json``'s ``error_message``.
    """
    match, _ = prepare(PHRASE_LOGIN_DASHBOARD)
    stub_driver.title = "Odoo"

    with pytest.raises(AssertionError) as failure:
        match.run(fake_context)

    assert str(failure.value) == TITLE_ASSERTION_MESSAGE
    assert str(failure.value).endswith("! "), "the trailing space is load-bearing"


def test_login_dashboard_failure_carries_no_junit_framing(
    prepare: Callable[[str], tuple[Any, WaitRecorder]],
    fake_context: Any,
    stub_driver: Any,
) -> None:
    """Deviation 16: the subject and the message carry over, the framing does not.

    JUnit renders ``expected:<...> but was:<...>`` around an ``assertEquals``
    failure and Python cannot produce it, so the port asserts the same subject
    with the same message and nothing else.  The message must therefore be the
    Java string alone - neither title value spliced into it, and no JUnit
    wording.
    """
    match, _ = prepare(PHRASE_LOGIN_DASHBOARD)
    stub_driver.title = "Odoo"

    with pytest.raises(AssertionError) as failure:
        match.run(fake_context)

    message = str(failure.value)

    assert message == TITLE_ASSERTION_MESSAGE
    assert "expected:" not in message
    assert "but was" not in message
    assert EXPECTED_LOGIN_TITLE not in message
    assert "Odoo" not in message


#: Which Java method touches a field of ``LogOutP`` and which does not.
#: ``:18-20`` reaches ``popUpButton`` and ``logOutButton``; ``:33`` reaches
#: ``warningMess``; ``:25-27`` reaches the driver's title and nothing else.
PAGE_FIELD_USERS: Final[dict[str, bool]] = {
    PHRASE_LOG_OUT: True,
    PHRASE_LOGIN_DASHBOARD: False,
    PHRASE_STEP_BACK: True,
}

#: The names through which a body can reach the page object: the module-private
#: per-scenario factory that replaces the ``LogOutP`` field of
#: ``LogOutSD.java:15``, and the class itself.
PAGE_OBJECT_NAMES: Final[frozenset[str]] = frozenset({"_page", "LogOutPage"})


def test_only_the_bodies_that_use_a_java_field_build_a_page_object(
    step_module_tree: ast.Module,
) -> None:
    """``:25-27`` touches no field of ``LogOutP``, so it builds no page object.

    The one parity fact in this class that no ordered log can show: constructing
    a page object is inert - ``BasePage`` stores the driver and does nothing
    else, locating no element - so a body that built one it never used would
    record exactly the operations of a body that built none.  It is therefore
    asserted from the source, in both directions: definitions 1 and 3 reach the
    page object because ``:18-20`` and ``:33`` reach ``popUpButton``,
    ``logOutButton`` and ``warningMess``, and definition 2 does not because
    ``:26`` reads the driver's title and ``:23-27`` names no field at all.
    """
    definitions = _definitions_by_phrase(step_module_tree)

    assert set(definitions) == set(JAVA_PHRASES)

    for phrase, uses_a_field in PAGE_FIELD_USERS.items():
        reached = _reached_names(definitions[phrase]) & PAGE_OBJECT_NAMES

        if uses_a_field:
            assert reached, (
                f"{phrase!r} must reach the page object; its Java body uses a "
                f"LogOutP field"
            )
        else:
            assert not reached, (
                f"{phrase!r} reached {sorted(reached)!r}, but LogOutSD.java:"
                f"23-27 touches no field of the page object"
            )


# ==========================================================================
# 5. Definition 3 - LogOutSD.java:30-34
#
#     Driver.getDriver().navigate().back();                    // :32
#     Assert.assertTrue(logOutP.warningMess.isDisplayed());    // :33
# ==========================================================================


def test_step_back_navigates_before_locating_the_warning(
    prepare: Callable[[str], tuple[Any, WaitRecorder]],
    fake_context: Any,
    stub_driver: Any,
) -> None:
    """``:32`` navigates back, and only then does ``:33`` resolve the modal.

    The order is the assertion's premise: the modal is located against the page
    the back navigation produced, not the one before it, so a port that read the
    element first would be asserting about the wrong page even when it passed.
    ``navigate().back()`` collapses to ``back()``, the Python binding having no
    ``navigate()`` intermediary, and this is the port's only
    navigation-history operation.
    """
    match, waits = prepare(PHRASE_STEP_BACK)
    stub_driver.set_displayed(LogOutPage.WARNING_MESS, True)

    assert match.run(fake_context) is None
    assert stub_driver.operations() == (BACK, FIND_ELEMENT, ELEMENT_IS_DISPLAYED)
    assert stub_driver.calls_of(FIND_ELEMENT) == (LogOutPage.WARNING_MESS,)
    assert waits.calls == [], "LogOutSD.java:30-33 constructs no wait"

    _lookup_precedes_each(stub_driver, ELEMENT_IS_DISPLAYED)


def test_step_back_passes_when_the_warning_modal_is_displayed(
    prepare: Callable[[str], tuple[Any, WaitRecorder]],
    fake_context: Any,
    stub_driver: Any,
) -> None:
    """``assertTrue`` at ``:33`` passes when ``isDisplayed()`` answers ``True``.

    The subject is ``LogOutP.java:20``'s ``warningMess`` and nothing else: the
    step reads that one element's visibility and makes no other check.
    """
    match, _ = prepare(PHRASE_STEP_BACK)
    stub_driver.set_displayed(LogOutPage.WARNING_MESS, True)

    assert match.run(fake_context) is None
    assert stub_driver.count_of(ELEMENT_IS_DISPLAYED) == 1


def test_step_back_fails_with_no_message_when_the_modal_is_hidden(
    prepare: Callable[[str], tuple[Any, WaitRecorder]],
    fake_context: Any,
    stub_driver: Any,
) -> None:
    """``:33`` passes ``assertTrue`` no message, so the port's assert carries none.

    Inventing one would put text in the behave report and in
    ``target/cucumber.json``'s ``error_message`` that the source never emits, so
    the absence is asserted as strictly as definition 2's message text is: an
    empty ``args`` tuple, not merely a falsy one.  The back navigation of
    ``:32`` is still recorded, because it happened before the assertion failed.
    """
    match, _ = prepare(PHRASE_STEP_BACK)
    stub_driver.set_displayed(LogOutPage.WARNING_MESS, False)

    with pytest.raises(AssertionError) as failure:
        match.run(fake_context)

    assert failure.value.args == ()
    assert str(failure.value) == ""
    assert stub_driver.operations() == (BACK, FIND_ELEMENT, ELEMENT_IS_DISPLAYED)


def test_step_back_lets_a_failed_lookup_propagate_uncaught(
    prepare: Callable[[str], tuple[Any, WaitRecorder]],
    fake_context: Any,
    stub_driver: Any,
) -> None:
    """``:30-33`` catches nothing, so a missing modal fails the step as it is.

    ``LogOutSD.java`` has no ``try`` and no exception handling anywhere, so when
    the modal is absent the lookup's own failure - in a real session, once the
    10-second implicit wait expires - travels out of the body untranslated
    rather than becoming an ``AssertionError`` or being swallowed.  The back
    navigation of ``:32`` has already happened at that point.
    """
    match, _ = prepare(PHRASE_STEP_BACK)
    stub_driver.set_find_error(LogOutPage.WARNING_MESS, RuntimeError("no such element"))

    with pytest.raises(RuntimeError, match="no such element"):
        match.run(fake_context)

    assert stub_driver.operations() == (BACK, FIND_ELEMENT)


# ==========================================================================
# 6. Module boundaries, by source inspection
#
# behave injects ``given``, ``when``, ``then`` and ``step`` into every step
# module's globals whether the file imports them or not, so the runtime
# namespace cannot answer which decorator a definition used or which names the
# file imported.  These assertions therefore read the source.
# ==========================================================================


def test_every_definition_registers_with_step_and_no_keyword_decorator(
    step_module_tree: ast.Module,
) -> None:
    """Three decorated definitions, every one of them ``@step`` (deviation 7).

    Pointed at this module in particular because all three Java declarations are
    ``@Then`` (``:16``, ``:23``, ``:30``) and the feature invokes two of them as
    an effective ``When``: a ``@then`` here would leave ``Logout.feature``'s
    lines 19, 43 and 44 undefined and the ``@UPGN-292`` scenario would never
    reach its final assertion.
    """
    decorated = _decorated_functions(step_module_tree)
    decorators = {name for node in decorated for name in _decorator_names(node)}

    assert len(decorated) == 3
    assert decorators == {"step"}
    assert decorators.isdisjoint({"given", "when", "then", "Given", "When", "Then"})


def test_source_declares_the_three_phrases_in_java_order(
    step_module_tree: ast.Module,
) -> None:
    """The decorator literals are the Java annotations' text, in source order.

    ``@step("...")`` carries the phrase, and the phrase is the contract - so it
    is pinned in the source as well as in the registry, in the declaration order
    of ``LogOutSD.java:16``, ``:23`` and ``:30``.
    """
    declared: list[str] = []

    for node in _decorated_functions(step_module_tree):
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue

            for argument in decorator.args:
                if isinstance(argument, ast.Constant) and isinstance(
                    argument.value, str
                ):
                    declared.append(argument.value)

    assert tuple(declared) == JAVA_PHRASES


def test_module_imports_are_closed_to_behave_the_page_and_a_wait(
    step_module_tree: ast.Module,
) -> None:
    """The import list is exactly three modules, and nothing is imported twice.

    ``LogOutSD.java`` needs a registration decorator (``:16``, ``:23``,
    ``:30``), its page object (``:15``) and one wait (``:13``), and the port's
    import list is the closed equivalent: ``behave`` for ``step``, ``app.pages``
    for ``LogOutPage`` and ``app.automation`` for the wait family, admitted here
    by the ``wait`` prefix.  What the wait *does* is pinned by the behavioural
    tests above and not by this import list: its target is
    ``LogOutPage.POP_UP_BUTTON``, the constant itself, its timeout is the 3 of
    ``LogOutSD.java:13``, and its position is first among the three statements
    of ``:18`` - ``:20``.
    """
    imported = _imported_names(step_module_tree)

    assert {entry.module for entry in imported} == {
        "behave",
        "app.pages",
        "app.automation",
    }
    assert [entry for entry in imported if entry.module == "behave"] == [
        ImportedName("behave", "step")
    ]
    assert [entry for entry in imported if entry.module == "app.pages"] == [
        ImportedName("app.pages", "LogOutPage")
    ]

    from_automation = [
        entry.name for entry in imported if entry.module == "app.automation"
    ]

    assert from_automation, "the 3-second wait of LogOutSD.java:13 needs a helper"
    assert all(name.startswith("wait") for name in from_automation), (
        f"only wait helpers belong here; found {from_automation!r}"
    )
    assert len(imported) == len(set(imported))


def test_module_imports_no_browser_library_and_no_locator_factory(
    step_module_tree: ast.Module,
) -> None:
    """No selenium, and no ``By``: this class builds no locator inline.

    ``app/automation`` is the only package in the port permitted to import
    selenium (AAP 0.4.2).  ``By`` is a separate point: ``LoginSD.java:56`` is
    the one Java method that constructs a locator in a step body, so
    ``login_steps`` is the one step module that imports the locator factory.
    ``LogOutSD.java`` reaches its three elements through ``LogOutP``'s fields
    only.

    The check covers the dynamic route as well.  A name imported by string
    would be invisible to the statement walk above, so the module is also
    required to contain no dynamic-import machinery - which is what makes the
    statement walk a complete account of what this module imports.
    """
    imported = _imported_names(step_module_tree)

    for entry in imported:
        assert entry.module.split(".")[0] != "selenium"
        assert entry.name.split(".")[0] != "selenium"
        assert entry.name != "By", (
            "only login_steps imports By, for LoginSD.java:56's inline locator"
        )

    assert _called_names(step_module_tree).isdisjoint(
        {"import_module", "__import__", "exec", "eval"}
    )


def test_module_imports_no_interactions_and_no_configuration(
    step_module_tree: ast.Module,
) -> None:
    """No ``Keys``, no ``Actions``, and no configuration key.

    ``LogOutSD.java:3-8`` is the class's whole import list - ``LogOutP``,
    ``Driver``, ``Assert``, ``WebDriverWait``, ``Then`` and
    ``ExpectedConditions`` - and it names neither ``Keys`` nor ``Actions``, so
    neither the keyboard helper nor the action-chain helper is imported here;
    the three bodies at ``:18-20``, ``:25-27`` and ``:32-33`` read no property
    either, so none of the six configuration accessors of AAP 0.4.1 is reached.
    Both absences are facts about the Java class rather than preferences, which
    is why they are asserted.
    """
    imported = _imported_names(step_module_tree)
    forbidden_names = {"press_keys", "action_chain", "Keys", "ActionChains"}

    for entry in imported:
        assert entry.module != "app.config"
        assert not entry.module.startswith("app.config.")
        assert entry.module != "app.automation.interactions"
        assert entry.name not in forbidden_names
        assert not entry.name.startswith("get_"), (
            f"{entry.name!r} looks like a configuration accessor; LogOutSD.java "
            f"reads no property"
        )


def test_module_contains_no_fixed_delay(
    step_module_tree: ast.Module,
) -> None:
    """``LogOutSD.java`` has no ``Thread.sleep``, so the port has no delay.

    Seventeen fixed sleeps exist across five of the reference's step-definition
    classes - ``Calendar``, ``Contacts``, ``Crm``, ``EmployeeStage`` and
    ``Notes.java:23`` - and the port reproduces each at its own call site
    (AAP 0.4.1).  ``LogOutSD.java`` is not one of them: its thirty-five lines
    carry no ``Thread.sleep`` and none of its three methods declares
    ``throws InterruptedException``, so adding a delay here would invent timing
    behaviour the source does not have.
    """
    imported = _imported_names(step_module_tree)

    for entry in imported:
        assert entry.module != "time"
        assert entry.name not in {"time", "sleep", "asyncio"}

    assert "sleep" not in _called_names(step_module_tree)


def test_module_never_creates_or_quits_a_driver(
    prepare: Callable[[str], tuple[Any, WaitRecorder]],
    fake_context: Any,
    stub_driver: Any,
    step_module_tree: ast.Module,
) -> None:
    """Logging out of the application is not WebDriver teardown (AAP 0.3.3).

    The one separation this area must state explicitly and negatively, because
    the feature under test is *about* logging out: ``LogOutSD.java`` reaches the
    session only to read the title at ``:26`` and to navigate back at ``:32``,
    both through ``Driver.getDriver()``, and it never creates, closes or quits
    one - ``Driver.closeDriver()`` is called from ``Hooks.java:17`` alone.
    ``features/environment.py`` holds that lifecycle here and already quits
    after every scenario, so a teardown, reset or re-open in one of these bodies
    would be both a boundary violation and a double quit.

    Asserted three ways: no lifecycle name appears in the source, running all
    three definitions quits nothing and configures nothing, and the autouse
    guard :func:`forbid_driver_lifecycle` would have raised had any body reached
    ``get_driver`` or ``quit_driver``.
    """
    source_names = _reached_names(step_module_tree)

    assert source_names.isdisjoint(
        {"get_driver", "quit_driver", "quit", "close", "webdriver", "Chrome", "Firefox"}
    )

    stub_driver.title = EXPECTED_LOGIN_TITLE
    stub_driver.set_displayed(LogOutPage.WARNING_MESS, True)

    for phrase in JAVA_PHRASES:
        match, _ = prepare(phrase)
        assert match.run(fake_context) is None

    assert stub_driver.quit_count == 0
    assert set(stub_driver.operations()).isdisjoint(
        {"quit", "maximize_window", "implicitly_wait", "get"}
    )
    assert fake_context.driver is stub_driver


# ==========================================================================
# 7. features/Logout.feature
#
# The feature is REFERENCE, byte-identical to
# src/main/resources/features/Logout.feature at the pinned revision, and moves
# only by AAP deviation 1 - into features/, filename unchanged.
#
# A note on selection, not an assertion: CukesRunner.java:18 fixes
# `tags = "@Smoke"`, which occurs exactly once in the whole reference, at
# Crm.feature:1 - so the default run selects the CRM feature alone and never
# this one. Neither @LogOut nor @UPGN-29x is @Smoke, so reaching this feature
# takes an explicit `--tags=@LogOut`.
# ==========================================================================


def test_feature_file_keeps_its_original_lower_case_o_filename() -> None:
    """``Logout.feature`` - lower-case ``o``, as the reference spells it.

    Deviation 1 moves the features into ``features/`` and preserves every
    filename, because the name appears in ``target/cucumber.json``'s ``uri``, in
    the rerun manifest and in user commands.  The step module is
    ``logout_steps.py`` and the page class is ``LogOutPage`` with a capital
    ``O``: three spellings, all three deliberate, none of them to be
    regularised.
    """
    assert FEATURE_PATH.is_file()
    assert FEATURE_PATH.name == "Logout.feature"
    assert not (FEATURE_PATH.parent / "LogOut.feature").exists()
    assert sorted(
        path.name for path in FEATURE_PATH.parent.glob("Log*.feature")
    ) == ["Login.feature", "Logout.feature"]


def test_feature_carries_the_logout_tag_on_line_one(
    feature_lines: tuple[str, ...],
) -> None:
    """``@LogOut`` at ``Logout.feature:1``, above the feature title at ``:2``.

    The title says *Testinium* while the Background says *upgenix* and the page
    under test is Odoo; AAP conflict 8 forbids reconciling the three, so the
    line is asserted as written.
    """
    assert feature_lines[0] == "@LogOut"
    assert feature_lines[1] == "Feature: Testinium app logout feature"


def test_the_two_scenario_outlines_sit_where_the_plan_cites_them(
    feature_lines: tuple[str, ...],
) -> None:
    """Two outlines, cited by AAP 0.4.1 at ``Logout.feature:13`` and ``:37``.

    Those two lines are the ``@UPGN-291`` and ``@UPGN-292`` tag lines, with the
    ``Scenario Outline:`` keywords immediately below at 14 and 38.  Both
    positions are asserted, since the citation names the tag line and the
    keyword is what makes each an outline rather than a scenario.
    """
    assert feature_lines[12] == "  @UPGN-291"
    assert feature_lines[13].strip().startswith("Scenario Outline:")
    assert feature_lines[36] == "  @UPGN-292"
    assert feature_lines[37].strip().startswith("Scenario Outline:")

    keywords = [
        line for line in feature_lines if line.strip().startswith("Scenario Outline:")
    ]

    assert len(keywords) == 2
    assert not [line for line in feature_lines if line.strip().startswith("Scenario:")]


#: The two ``Examples`` blocks, transcribed from the reference feature, and the
#: line each one starts on.  Both outlines carry the same two blocks - a
#: SalesManager set and a PosManager set, three rows each - which is why the
#: same literals appear at two positions.
EXAMPLES_SALES_MANAGER: Final[tuple[str, ...]] = (
    "    @SalesManager",
    "    Examples: SalesManager's username and password",
    "      |username               |password    |",
    "      |salesmanager7@info.com |salesmanager|",
    "      |salesmanager8@info.com |salesmanager|",
    "      |salesmanager9@info.com |salesmanager|",
)
EXAMPLES_POS_MANAGER: Final[tuple[str, ...]] = (
    "    @PosManager",
    "    Examples: PosManager's username and password",
    "      |username               |password    |",
    "      |posmanager5@info.com   |posmanager  |",
    "      |posmanager6@info.com   |posmanager  |",
    "      |posmanager7@info.com   |posmanager  |",
)
EXAMPLES_BLOCKS: Final[tuple[tuple[int, tuple[str, ...]], ...]] = (
    (21, EXAMPLES_SALES_MANAGER),
    (28, EXAMPLES_POS_MANAGER),
    (47, EXAMPLES_SALES_MANAGER),
    (54, EXAMPLES_POS_MANAGER),
)


@pytest.mark.parametrize(
    ("first_line", "block"),
    EXAMPLES_BLOCKS,
    ids=[f"Logout.feature:{start}" for start, _ in EXAMPLES_BLOCKS],
)
def test_the_examples_blocks_are_unchanged(
    first_line: int, block: tuple[str, ...], feature_lines: tuple[str, ...]
) -> None:
    """Both outlines' ``Examples`` are the reference's, rows and padding included.

    Each block is compared with its indentation and its column padding intact,
    because the example values are the test data of six scenario rows per
    outline and an "obvious" tidy of the table would change what runs.  The
    ``@SalesManager`` and ``@PosManager`` tags above each block select those
    rows directly, which is the only way into this feature under a positive tag
    expression.
    """
    start = first_line - 1

    assert feature_lines[start : start + len(block)] == block


def test_the_background_invokes_the_shared_login_precondition(
    feature_lines: tuple[str, ...], resolve_step: Callable[[str], Any]
) -> None:
    """``Logout.feature:9-10`` - one Background step, owned by ``login_steps``.

    Two modules serve this one feature.  The Background and the four
    username/password/login/dashboard phrases belong to
    ``features/steps/login_steps.py``; only the three census phrases are this
    module's.  A Background phrase going undefined is a question for
    ``login_steps``, never a reason to add a fourth definition to
    ``logout_steps``.  Note the near-collision it must not resolve to:
    ``EmployeeStage.java:16`` declares ``User is on upgenix login page``,
    without the ``the``.
    """
    assert feature_lines[8].strip().startswith("Background:")
    assert feature_lines[9] == "    Given User is on the upgenix login page"

    match = resolve_step("User is on the upgenix login page")

    assert match.module_name == "login_steps"
    assert match.module_name != STEP_MODULE_NAME


#: A Gherkin step line: its keyword and the phrase the registry resolves.
STEP_LINE: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?P<keyword>Given|When|Then|And|But|\*)\s+(?P<phrase>.+?)\s*$"
)


def _feature_steps(feature_lines: tuple[str, ...]) -> tuple[tuple[int, str, str], ...]:
    """Every step line of the feature, as ``(line number, keyword, phrase)``.

    :param feature_lines: The feature file's lines.
    :returns: One entry per step line, in file order.
    """
    found: list[tuple[int, str, str]] = []

    for number, line in enumerate(feature_lines, 1):
        match = STEP_LINE.match(line)

        if match:
            found.append((number, match.group("keyword"), match.group("phrase")))

    return tuple(found)


def test_every_step_line_in_the_feature_resolves_exactly_once(
    feature_lines: tuple[str, ...], resolve_step: Callable[[str], Any]
) -> None:
    """All fourteen step lines of ``Logout.feature`` resolve, each to one body.

    Resolution is the feature's own acceptance test: ``resolve_step`` fails both
    on nothing matching and on more than one definition matching, so this covers
    the undefined step and the ambiguity that ``@step``'s keyword-wide reach
    makes possible.  The outline placeholders are resolved as written -
    ``User enters "<username>" username`` matches the parameterised pattern with
    the placeholder as its value - which is what behave itself does before
    substituting an Examples row.
    """
    steps = _feature_steps(feature_lines)

    assert len(steps) == 14

    for number, _, phrase in steps:
        match = resolve_step(phrase)

        assert match.bucket == "step", (
            f"Logout.feature:{number} resolves through the {match.bucket!r} "
            f"bucket rather than @step"
        )
        assert match.module_name in {STEP_MODULE_NAME, "login_steps"}


def test_the_feature_invokes_the_logout_phrases_across_two_keywords(
    feature_lines: tuple[str, ...], resolve_step: Callable[[str], Any]
) -> None:
    """One phrase, two effective step types, one feature file.

    The concrete reason deviation 7 exists.  ``Logout.feature:19`` and ``:43``
    invoke definition 1 as ``And``, inheriting the ``When`` opened at ``:15``
    and ``:39``; ``:44`` invokes definition 2 as an ``And`` - effective ``When``
    - while ``:20`` invokes the *same phrase* as a real ``Then``; and ``:45``
    invokes definition 3 as a ``Then``.  All three Java methods are declared
    ``@Then``, so text-only matching is what keeps all five of these
    invocations defined.
    """
    invocations = {
        (number, keyword): phrase
        for number, keyword, phrase in _feature_steps(feature_lines)
        if phrase in JAVA_PHRASES
    }

    assert invocations == {
        (19, "And"): "User click Log out option",
        (20, "Then"): "User should see the login dashboard",
        (43, "And"): "User click Log out option",
        (44, "And"): "User should see the login dashboard",
        (45, "Then"): "User can not click the step back button to go the home page",
    }

    for phrase in set(invocations.values()):
        assert resolve_step(phrase).module_name == STEP_MODULE_NAME
