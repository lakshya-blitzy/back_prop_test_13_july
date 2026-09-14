"""Per-method parity tests for ``features/steps/login_steps.py``.

The module that discharges AAP 0.4.1's per-module obligation for the Login
area: *"for each of the ten step modules, ``tests/test_steps_<area>.py`` drives
the module against a stubbed driver and asserts, for every step method in the
corresponding Java class, that the port performs the same observable
operations in the same order"*.  The corresponding Java class is
``LoginSD.java`` at the pinned reference revision
``47e9d697e4a9a85da889f94a846fdf47af28a240``, which AAP 0.2.1 holds REFERENCE
and never modifies, and whose 68 lines are therefore the specification every
expectation below is written against.

Where the expectations come from
--------------------------------
**The Java source, never the Python implementation.**  A parity test that
restates the port proves only that the port equals itself.  Every assertion
here names the ``LoginSD.java`` or ``LoginP.java`` line it pins, and the nine
entries of :data:`JAVA_METHODS` are an independent census of that class -
transcribed from it, not derived from the registry - so that a definition the
port loses, gains or renames fails a test rather than quietly agreeing with a
shorter list.

=====  ==========  ====================================================
Java   Annotation  Body
=====  ==========  ====================================================
``19`` ``Given``   ``:22`` reads ``web.table.url``; ``:23`` navigates
``26`` ``When``    ``:28`` ``inputEmail.sendKeys``
``31`` ``When``    ``:33`` ``inputPassword.sendKeys``
``36`` ``When``    ``:38`` ``button.click``
``41`` ``Then``    ``:43`` wait 3s on ``dashboard``, ``:44-46`` title
``49`` ``Then``    ``:51`` ``assertTrue(alertErrorMessage.isDisplayed())``
``54`` ``Then``    ``:56`` direct ``By.name("login")``, ``:57`` 2-arg equals
``60`` ``Then``    ``:62`` ``bulletPass.getAttribute("type")``
``65`` ``When``    ``:67`` ``button.click`` again - no key is pressed
=====  ==========  ====================================================

How a step body is reached
--------------------------
Through ``tests/conftest.py`` alone, which owns every fixture this suite has:
:fixture:`resolve_step` hands back the registered definition behind a Gherkin
phrase, :fixture:`fake_context` stands in for behave's ``Context``, and
:fixture:`stub_driver` - the same recorder, published as ``context.driver`` -
keeps one ordered log of every navigation, lookup, element operation and page
read.  Nothing here adds a fixture of its own to that file, launches a
browser, touches the network, sleeps, or writes anything anywhere.

Two techniques are load-bearing and are documented where they are used
----------------------------------------------------------------------
1. **The explicit wait is intercepted in the step module's own globals.**
   behave's ``load_step_modules`` execs each step file with a private globals
   dict, and a step body resolves ``wait...`` through that dict, so replacing
   the entry is what stops ``app/automation/waits.py`` reaching for a real
   session.  The name is *discovered* rather than hard-coded, and every
   assertion is about the wait's **target locator, its timeout value and its
   position in the ordered log** - never about which helper was called, how
   many arguments it took, or whether the timeout arrived positionally.  That
   is deliberate: the visibility-wait call sites are being revised elsewhere in
   this checkpoint, and the parity fact ``LoginSD.java:17`` fixes is the
   3-second timeout on ``loginP.dashboard``, not a Python function signature.
2. **Locator expectations close the chain Java -> constant -> operation.**
   Each locator is stated as the ``LoginPage`` constant, *and* that constant's
   own ``(strategy, selector)`` pair is asserted against the ``@FindBy`` value
   in ``LoginP.java``.  ``INPUT_PASSWORD`` and ``BULLET_PASS`` are the same
   selector under two names (``LoginP.java:16-17`` and ``:31-32``), so the two
   steps that read them are additionally discriminated by *accessor*, which a
   selector comparison cannot do.

What this module deliberately does not assert
---------------------------------------------
JUnit's ``expected:<...> but was:<...>`` framing: AAP deviation 16 preserves
assertion **subjects and message text** and nothing around them, so the
failure-path tests pin ``AssertionError.args`` - the byte-exact message where
Java supplies one, and its absence where Java does not.  Nor does it assert
anything about a browser locale: the French required-field string at
``Login.feature:89`` is carried byte-for-byte and AAP 0.6 leaves the locale
unresolved, so the string is pinned and no locale key is invented.

Everything this module reads is read-only to it: ``features/steps/``,
``app/**``, ``features/Login.feature`` and ``tests/conftest.py`` are all
authored elsewhere, and a parity defect found here is reported rather than
accommodated.
"""

from __future__ import annotations

import ast
import functools
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any, Final, NamedTuple

import pytest

from app import config
from app.pages import LoginPage
from app.utils import properties

# --------------------------------------------------------------------------
# Locations.  Derived from this file's own position rather than from the
# working directory, because the step functions behave hands back carry a
# *relative* ``co_filename`` - ``inspect.getsource`` on them would therefore
# succeed or fail according to where pytest was started from, which is no
# basis for an assertion.
# --------------------------------------------------------------------------

#: Repository root, from this file's own position: ``tests/`` -> root.
REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]

#: The module under test, read as text and parsed for the source-level checks.
STEP_MODULE_PATH: Final[Path] = REPO_ROOT / "features" / "steps" / "login_steps.py"

#: The feature file whose text AAP 0.4.1 requires preserved verbatim.
FEATURE_PATH: Final[Path] = REPO_ROOT / "features" / "Login.feature"

#: The module name every definition of this area must be registered from,
#: asserted through :attr:`StepMatch.module_name` rather than through the
#: rendered location, which behave writes relative to the working directory.
STEP_MODULE_NAME: Final[str] = "login_steps"

#: The one registry bucket AAP deviation 7 permits: every definition registers
#: with ``@step``, because Cucumber-JVM matches a step by its text alone.
STEP_BUCKET: Final[str] = "step"

#: Every bucket behave keeps, scanned so that a definition accidentally bound
#: to a keyword is found and reported rather than silently missed.
STEP_BUCKETS: Final[tuple[str, ...]] = ("step", "given", "when", "then")

#: Encoding for both files read here.  The feature file carries the French
#: required-field string and typographic quotation marks in its comments.
SOURCE_ENCODING: Final[str] = "utf-8"


# --------------------------------------------------------------------------
# The Java authority: locators, literals and messages
#
# Locator strategies are stated as the literal strings the driver receives -
# which is also what the recorder logs - with the Java ``@FindBy`` attribute
# that maps to each named beside it.  Pinned selenium 4.48.0 renders
# ``By.CLASS_NAME`` as ``"class name"``; the pair below is the Java authority
# and the mapping is asserted once, in
# ``test_locator_constants_match_the_java_find_by``.
# --------------------------------------------------------------------------

#: ``@FindBy(name = "login")`` - ``LoginP.java:13-14``, field ``inputEmail``.
LOCATOR_INPUT_EMAIL: Final[tuple[str, str]] = ("name", "login")

#: ``@FindBy(name="password")`` - ``LoginP.java:16-17``, ``inputPassword``.
LOCATOR_INPUT_PASSWORD: Final[tuple[str, str]] = ("name", "password")

#: ``@FindBy(xpath = "//button[.='Log in']")`` - ``LoginP.java:19-20``.
LOCATOR_BUTTON: Final[tuple[str, str]] = ("xpath", "//button[.='Log in']")

#: ``@FindBy(xpath = "//a[.='Reset Password']")`` - ``LoginP.java:22-23``.
#: Declared by the Java class and read by none of its nine step methods.
LOCATOR_RESET_PASS: Final[tuple[str, str]] = ("xpath", "//a[.='Reset Password']")

#: ``@FindBy(id = "oe_main_menu_navbar")`` - ``LoginP.java:25-26``.  Odoo's own
#: top menu bar, preserved under AAP Conflict 8's *"no renaming"*.
LOCATOR_DASHBOARD: Final[tuple[str, str]] = ("id", "oe_main_menu_navbar")

#: ``@FindBy(className = "alert")`` - ``LoginP.java:28-29``.
LOCATOR_ALERT_ERROR_MESSAGE: Final[tuple[str, str]] = ("class name", "alert")

#: ``@FindBy(name="password")`` - ``LoginP.java:31-32``, field ``bulletPass``:
#: the same selector as :data:`LOCATOR_INPUT_PASSWORD`, declared separately.
LOCATOR_BULLET_PASS: Final[tuple[str, str]] = ("name", "password")

#: The expected page title of ``LoginSD.java:44``.
EXPECTED_TITLE: Final[str] = "Odoo"

#: The assertion message of ``LoginSD.java:46``, byte-exact.  **It ends in one
#: trailing space**, which ``LogOutSD.java:27`` shares and which
#: ``Calendar.java:46`` and ``Sales.java:37`` do not; the literal below is the
#: only place this module spells it out.
TITLE_ASSERTION_MESSAGE: Final[str] = "The title is not same as the expected! "

#: The DOM property ``LoginSD.java:56`` reads, camelCase as the DOM spells it.
VALIDATION_ATTRIBUTE: Final[str] = "validationMessage"

#: The attribute ``LoginSD.java:62`` reads, and the value it compares against.
TYPE_ATTRIBUTE: Final[str] = "type"
PASSWORD_INPUT_TYPE: Final[str] = "password"

#: The browser's own French required-field message, ``Login.feature:89``.
#: Carried byte-for-byte; AAP 0.6 leaves the browser locale unresolved and no
#: locale key is invented anywhere in this port.
FRENCH_REQUIRED_FIELD_MESSAGE: Final[str] = "Veuillez renseigner ce champ."

#: The commented-out expected title at ``LoginSD.java:21``.  It has no
#: behaviour, so no executable statement of the port may carry it.
COMMENTED_OUT_TITLE: Final[str] = "Login | Best solution for startups"

#: The configuration key ``LoginSD.java:22`` reads.
WEB_TABLE_URL_KEY: Final[str] = "web.table.url"

#: All six configuration keys with six distinguishable values, so that a step
#: reading the wrong one is caught by the value it navigated to rather than
#: merely by the key it happened to name.  AAP 0.8 forbids redacting the
#: Gherkin credentials, and the username and password below are the
#: ``Login.feature:23`` pair: neither may be mistaken for the login URL.
CONFIGURATION: Final[Mapping[str, str]] = {
    "browser": "chrome",
    WEB_TABLE_URL_KEY: "https://login.invalid/web/login",
    "url": "https://employee.invalid/web",
    "username": "salesmanager7@info.com",
    "password": "salesmanager",
    "EmplTitle": "Employees",
}


# --------------------------------------------------------------------------
# The nine-method census
#
# Transcribed from ``LoginSD.java`` - method name, annotation, declaration
# line and body lines - and paired with the behave pattern that must carry it
# and one concrete use taken from ``features/Login.feature``.  This table, not
# the registry, is what "nine" means in this module.
# --------------------------------------------------------------------------

PATTERN_NAVIGATE: Final[str] = "User is on the upgenix login page"
PATTERN_USERNAME: Final[str] = 'User enters "{username}" username'
PATTERN_PASSWORD: Final[str] = 'User enters "{password}" password'
PATTERN_LOGIN_BUTTON: Final[str] = "User clicks the login button"
PATTERN_DASHBOARD: Final[str] = "User should see the dashboard"
PATTERN_ERROR_MESSAGE: Final[str] = "User sees error message"
PATTERN_VALIDATION: Final[str] = 'User sees "{alert_message}" message'
PATTERN_BULLET_SIGNS: Final[str] = "User should see the password in bullet signs"
PATTERN_ENTER_BUTTON: Final[str] = "User clicks the enter button"

#: The ``<username>`` value of ``Login.feature:23``, carried verbatim.
SAMPLE_USERNAME: Final[str] = "salesmanager7@info.com"

#: The ``<password>`` value of ``Login.feature:23``, carried verbatim.
SAMPLE_PASSWORD: Final[str] = "salesmanager"

USE_NAVIGATE: Final[str] = PATTERN_NAVIGATE
USE_USERNAME: Final[str] = f'User enters "{SAMPLE_USERNAME}" username'
USE_PASSWORD: Final[str] = f'User enters "{SAMPLE_PASSWORD}" password'
USE_LOGIN_BUTTON: Final[str] = PATTERN_LOGIN_BUTTON
USE_DASHBOARD: Final[str] = PATTERN_DASHBOARD
USE_ERROR_MESSAGE: Final[str] = PATTERN_ERROR_MESSAGE
USE_VALIDATION: Final[str] = f'User sees "{FRENCH_REQUIRED_FIELD_MESSAGE}" message'
USE_BULLET_SIGNS: Final[str] = PATTERN_BULLET_SIGNS
USE_ENTER_BUTTON: Final[str] = PATTERN_ENTER_BUTTON


class JavaMethod(NamedTuple):
    """One ``LoginSD.java`` step method, as the census records it."""

    #: The Java method name, which the ported function must carry unchanged so
    #: that a failure names the same thing in both languages.
    name: str

    #: The Cucumber annotation on it - ``Given``, ``When`` or ``Then``.  Every
    #: one of them registers with ``@step`` in the port (AAP deviation 7), so
    #: this column is what that claim is checked against.
    annotation: str

    #: Declaration line in ``LoginSD.java``.
    declared_at: int

    #: Body lines in ``LoginSD.java``.
    body_at: str

    #: The behave pattern - the text inside the ``@step`` decorator.
    pattern: str

    #: One concrete phrase from ``features/Login.feature`` that must resolve
    #: to this definition.
    use: str


#: The census.  Nine entries, in ``LoginSD.java`` declaration order.
JAVA_METHODS: Final[tuple[JavaMethod, ...]] = (
    JavaMethod(
        "user_is_on_the_upgenix_login_page",
        "Given",
        19,
        ":22-23",
        PATTERN_NAVIGATE,
        USE_NAVIGATE,
    ),
    JavaMethod(
        "user_enters_username", "When", 26, ":28", PATTERN_USERNAME, USE_USERNAME
    ),
    JavaMethod(
        "user_enters_password", "When", 31, ":33", PATTERN_PASSWORD, USE_PASSWORD
    ),
    JavaMethod(
        "user_clicks_the_login_button",
        "When",
        36,
        ":38",
        PATTERN_LOGIN_BUTTON,
        USE_LOGIN_BUTTON,
    ),
    JavaMethod(
        "user_should_see_the_dashboard",
        "Then",
        41,
        ":43-46",
        PATTERN_DASHBOARD,
        USE_DASHBOARD,
    ),
    JavaMethod(
        "user_sees_error_message",
        "Then",
        49,
        ":51",
        PATTERN_ERROR_MESSAGE,
        USE_ERROR_MESSAGE,
    ),
    JavaMethod(
        "user_sees_please_fill_out_this_field_message",
        "Then",
        54,
        ":56-57",
        PATTERN_VALIDATION,
        USE_VALIDATION,
    ),
    JavaMethod(
        "user_should_see_the_password_in_bullet_signs",
        "Then",
        60,
        ":62",
        PATTERN_BULLET_SIGNS,
        USE_BULLET_SIGNS,
    ),
    JavaMethod(
        "user_clicks_the_enter_button",
        "When",
        65,
        ":67",
        PATTERN_ENTER_BUTTON,
        USE_ENTER_BUTTON,
    ),
)

#: The census keyed by pattern, for the tests that look one entry up.
JAVA_METHODS_BY_PATTERN: Final[Mapping[str, JavaMethod]] = {
    method.pattern: method for method in JAVA_METHODS
}


# --------------------------------------------------------------------------
# The gap detector
#
# AAP 0.4.1: *"A step method with no corresponding assertion in its module's
# test is a gap, and the module test enumerates the Java class's methods so an
# omission fails rather than passes silently."*
#
# Each behavioural test below declares which census entry it pins with
# :func:`pins`, which records the claim at **import** time.  The detector then
# compares three sets - the census, what this file pins, and what the registry
# actually carries - so that deleting a test, deleting a definition or adding
# an unpinned tenth definition each turn the suite red.  Recording at import
# rather than at call time is what makes the detector independent of test
# order and of any ``-k`` selection.
#
# One refinement, measured rather than assumed: a test that claims several
# methods at once - the wait census below claims all nine - would otherwise
# keep the ledger complete while a single method's own test was deleted, which
# is exactly the gap this detector exists to catch.  So a **single-pattern**
# claim is recorded separately, and the detector requires one per method: a
# sweeping test supplements a method's own test and never substitutes for it.
# --------------------------------------------------------------------------

#: Pattern -> the names of every test that pins its behaviour.
_PINNED: Final[dict[str, set[str]]] = {}

#: Pattern -> the names of the tests that pin it and nothing else.  Each Java
#: method must have at least one, which is what makes deleting that method's
#: own behavioural test a failure rather than a silent loss of coverage.
_PINNED_EXCLUSIVELY: Final[dict[str, set[str]]] = {}


def pins(*patterns: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Record that the decorated test pins the behaviour of *patterns*.

    :param patterns: One or more census patterns, each of which must be a key
        of :data:`JAVA_METHODS_BY_PATTERN`.  A claim naming exactly one pattern
        is additionally recorded as that method's own behavioural pin.
    :returns: A decorator that registers the claim and returns the test
        function completely unchanged, so pytest still sees its own signature
        and its fixtures resolve normally.
    :raises AssertionError: At import time, if a pattern is not in the census -
        a typo in a claim would otherwise leave a Java method looking covered
        when nothing exercises it.
    """

    def decorate(function: Callable[..., Any]) -> Callable[..., Any]:
        for pattern in patterns:
            assert pattern in JAVA_METHODS_BY_PATTERN, (
                f"{function.__name__} claims to pin {pattern!r}, which is not "
                f"one of the nine LoginSD.java methods in JAVA_METHODS"
            )
            _PINNED.setdefault(pattern, set()).add(function.__name__)

            if len(patterns) == 1:
                _PINNED_EXCLUSIVELY.setdefault(pattern, set()).add(
                    function.__name__
                )

        return function

    return decorate


# --------------------------------------------------------------------------
# Source inspection helpers
#
# The module is read from :data:`STEP_MODULE_PATH` and parsed once.  Body
# comparisons go through :func:`ast.unparse`, which normalises formatting, so
# these assertions pin *what the port does* and not how its lines happen to be
# wrapped.
# --------------------------------------------------------------------------


@functools.cache
def _module_source() -> str:
    """The text of ``features/steps/login_steps.py``.

    :returns: The whole file, decoded as UTF-8.
    """
    return STEP_MODULE_PATH.read_text(encoding=SOURCE_ENCODING)


@functools.cache
def _module_tree() -> ast.Module:
    """The parsed syntax tree of the module under test.

    :returns: The ``ast.Module`` for :func:`_module_source`.
    """
    return ast.parse(_module_source(), filename=str(STEP_MODULE_PATH))


@functools.cache
def _function_defs() -> Mapping[str, ast.FunctionDef]:
    """Every top-level function of the module, by name.

    :returns: A mapping from function name to its definition node.
    """
    return {
        node.name: node
        for node in _module_tree().body
        if isinstance(node, ast.FunctionDef)
    }


def _is_docstring(statement: ast.stmt) -> bool:
    """Whether *statement* is a bare string expression - a docstring.

    :param statement: Any statement node.
    :returns: ``True`` for a docstring, ``False`` otherwise.
    """
    return (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, str)
    )


def _body_statements(name: str) -> tuple[str, ...]:
    """The executable statements of one step function, normalised.

    :param name: The function's name, which is also its Java method name.
    :returns: One normalised source line per statement, docstring excluded.
    :raises AssertionError: If the module declares no such function, which
        means the definition this module is asserting about is gone.
    """
    definitions = _function_defs()
    assert name in definitions, (
        f"{STEP_MODULE_PATH.name} declares no function named {name!r}; "
        f"LoginSD.java declares that step method"
    )

    return tuple(
        ast.unparse(statement)
        for statement in definitions[name].body
        if not _is_docstring(statement)
    )


def _decorators(name: str) -> tuple[str, ...]:
    """The normalised decorator expressions of one function.

    :param name: The function's name.
    :returns: One normalised expression per decorator, in source order.
    """
    return tuple(ast.unparse(node) for node in _function_defs()[name].decorator_list)


@functools.cache
def _imported_modules() -> tuple[str, ...]:
    """Every module named by an import statement anywhere in the file.

    ``import a.b`` and ``from a.b import c`` both contribute ``"a.b"``, and a
    relative import contributes its written module or the empty string, so a
    boundary check can look for a prefix without caring which form was used.

    :returns: The module names, in source order.
    """
    names: list[str] = []

    for node in ast.walk(_module_tree()):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")

    return tuple(names)


@functools.cache
def _imported_names() -> tuple[str, ...]:
    """Every name bound by an import statement anywhere in the file.

    :returns: The bound names - the alias where one is given, otherwise the
        imported name - in source order.
    """
    names: list[str] = []

    for node in ast.walk(_module_tree()):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names.extend(alias.asname or alias.name for alias in node.names)

    return tuple(names)


@functools.cache
def _called_attribute_names() -> tuple[str, ...]:
    """The final name of every call target in the file.

    ``time.sleep(3)`` contributes ``"sleep"`` and ``get_driver()`` contributes
    ``"get_driver"``, so a prohibited operation is found however it was
    reached.

    :returns: The callee names, in traversal order.
    """
    names: list[str] = []

    for node in ast.walk(_module_tree()):
        if not isinstance(node, ast.Call):
            continue

        target = node.func

        if isinstance(target, ast.Name):
            names.append(target.id)
        elif isinstance(target, ast.Attribute):
            names.append(target.attr)

    return tuple(names)


@functools.cache
def _string_constants() -> tuple[str, ...]:
    """Every string literal in the file that is not a docstring.

    Docstrings are excluded deliberately: the module documents the
    commented-out title of ``LoginSD.java:21`` and the French feature string in
    prose, and prose is not behaviour.

    :returns: The executable string literals, in traversal order.
    """
    docstrings = {
        id(statement.value)
        for node in ast.walk(_module_tree())
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef))
        for statement in node.body[:1]
        if _is_docstring(statement)
    }

    return tuple(
        node.value
        for node in ast.walk(_module_tree())
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    )


# --------------------------------------------------------------------------
# Wait interception
#
# Discovery by prefix, and by prefix only: the helper the dashboard step
# reaches may be renamed or re-shaped elsewhere in this checkpoint, and
# ``LoginSD.java:17`` fixes a 3-second timeout on a target - not a signature.
# --------------------------------------------------------------------------

#: Prefix every wait helper ``app/automation`` exports begins with.
WAIT_NAME_PREFIX: Final[str] = "wait"

#: The operation name the recorder appends to the driver's log, so that the
#: wait takes its place in the same ordered sequence as the lookups and reads
#: around it.  Chosen to collide with no ``StubDriver`` operation name.
WAIT_OPERATION: Final[str] = "wait"


def _is_locator(value: Any) -> bool:
    """Whether *value* is a ``(strategy, selector)`` pair.

    :param value: Any value.
    :returns: ``True`` for a two-element tuple of strings.
    """
    return (
        isinstance(value, tuple)
        and len(value) == 2
        and all(isinstance(part, str) for part in value)
    )


def _wait_target(arguments: Iterable[Any]) -> Any:
    """The single wait argument that names an element or a locator.

    Accepts either call shape: a located element, which carries the
    ``locator`` attribute ``StubElement`` publishes, or a raw
    ``(strategy, selector)`` pair.

    :param arguments: Every argument the wait received, positional and keyword
        values alike.
    :returns: The one argument that identifies the wait's target.
    :raises AssertionError: If none or several do, which means the call shape
        changed in a way this module cannot read - report it rather than guess.
    """
    candidates = [
        value
        for value in arguments
        if getattr(value, "locator", None) is not None or _is_locator(value)
    ]

    assert len(candidates) == 1, (
        f"expected exactly one element-or-locator argument in the wait call, "
        f"found {candidates!r}"
    )

    return candidates[0]


def _target_locator(target: Any) -> tuple[str, str]:
    """The ``(strategy, selector)`` pair a wait target resolves to.

    :param target: An element carrying ``locator``, or a locator pair.
    :returns: The pair, normalised to a tuple.
    """
    locator = getattr(target, "locator", None)

    if locator is None:
        locator = target

    pair = tuple(locator)
    assert _is_locator(pair), f"not a (strategy, selector) pair: {pair!r}"
    return pair


def _wait_timeout(arguments: Iterable[Any]) -> float:
    """The timeout a wait was given, positionally or by keyword.

    :param arguments: Every argument the wait received.
    :returns: The single numeric argument.
    :raises AssertionError: If there is not exactly one.  None at all is the
        case that matters: it would mean the call site stopped supplying the
        timeout and fell back on a helper default, which AAP 0.4.1 forbids
        because the reference fixes a different timeout per step class.
    """
    numbers = [
        value
        for value in arguments
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]

    assert len(numbers) == 1, (
        f"expected exactly one numeric timeout argument in the wait call, "
        f"found {numbers!r}: LoginSD.java:17 fixes 3 seconds at the call site"
    )

    return numbers[0]


def _install_wait_recorder(
    monkeypatch: pytest.MonkeyPatch, step_function: Any, driver: Any
) -> tuple[str, ...]:
    """Replace every wait helper in the step module's globals with a recorder.

    The recorder appends ``(WAIT_OPERATION, (args, kwargs))`` to the driver's
    own log, which is what puts the wait in sequence with the lookups and page
    reads around it, and returns the wait's target - the value
    ``visibility_of`` resolves to - so a call site that used the result would
    still work.

    :param monkeypatch: pytest's patcher; ``setitem`` restores the real helper
        after the test, so nothing leaks into the session-scoped registry.
    :param step_function: Any function of the step module, read only for its
        ``__globals__`` - behave execs each step file with a private globals
        dict, so this is the dict the step bodies resolve their helpers in.
    :param driver: The recorder the step will drive.
    :returns: The names that were replaced.
    :raises AssertionError: If the module exposes no wait helper at all.
    """
    names = tuple(
        sorted(
            name
            for name in step_function.__globals__
            if name.startswith(WAIT_NAME_PREFIX)
        )
    )

    assert names, (
        f"{STEP_MODULE_PATH.name} binds no name starting with "
        f"{WAIT_NAME_PREFIX!r}, so LoginSD.java:43's explicit wait cannot be "
        f"reached at all"
    )

    def recorder(*args: Any, **kwargs: Any) -> Any:
        driver.calls.append((WAIT_OPERATION, (args, kwargs)))
        return _wait_target((*args, *kwargs.values()))

    for name in names:
        monkeypatch.setitem(step_function.__globals__, name, recorder)

    return names


def _wait_calls(driver: Any) -> tuple[tuple[tuple[str, str], float], ...]:
    """The target locator and timeout of every wait the driver recorded.

    :param driver: The recorder.
    :returns: One ``((strategy, selector), timeout)`` pair per wait, in call
        order.
    """
    calls: list[tuple[tuple[str, str], float]] = []

    for operation, entry in driver.calls:
        if operation != WAIT_OPERATION:
            continue

        args, kwargs = entry
        arguments = (*args, *kwargs.values())
        calls.append(
            (_target_locator(_wait_target(arguments)), _wait_timeout(arguments))
        )

    return tuple(calls)


def _operations_except(driver: Any, *ignored: str) -> tuple[str, ...]:
    """The ordered operation names, with *ignored* operations dropped.

    Used to state a sequence that must hold whichever way the wait is called:
    an element-form wait resolves its element first and logs a
    ``find_element``, a locator-form wait does not, and neither shape changes
    the parity fact that the wait precedes the title read.

    :param driver: The recorder.
    :param ignored: Operation names to leave out.
    :returns: The remaining operation names, in order.
    """
    return tuple(
        operation for operation, _ in driver.calls if operation not in ignored
    )


# --------------------------------------------------------------------------
# Registry and configuration helpers
# --------------------------------------------------------------------------


class Definition(NamedTuple):
    """One registered step definition, as the registry scan records it."""

    #: The registry bucket it landed in - ``"step"`` for all nine.
    bucket: str

    #: The text inside its ``@step`` decorator.
    pattern: str

    #: The name of the function it registered, which is the Java method name.
    function_name: str


def _registered_definitions(registry: Any) -> tuple[Definition, ...]:
    """Every definition ``login_steps`` registered, in registration order.

    All four buckets are scanned rather than only ``step``, so a definition
    accidentally bound to a keyword is found and reported instead of quietly
    vanishing from the count.

    :param registry: behave's populated ``StepRegistry``.
    :returns: One entry per definition this module owns.
    """
    found: list[Definition] = []

    for bucket in STEP_BUCKETS:
        for matcher in registry.steps.get(bucket, ()):
            filename = str(getattr(matcher.location, "filename", "") or "")

            if Path(filename).stem == STEP_MODULE_NAME:
                found.append(
                    Definition(bucket, matcher.pattern, matcher.func.__name__)
                )

    return tuple(found)


def _install_configuration(
    request: pytest.FixtureRequest, values: Mapping[str, str]
) -> None:
    """Install *values* as behave userdata for the duration of one test.

    ``app/config.py`` reads userdata ahead of the properties file, so this is
    how a test states what the six keys are without depending on a
    ``configuration.properties`` that AAP 0.6 says may legitimately be absent.
    The finalizer is registered *before* the install, so it runs even if the
    test fails, and it clears the slot exactly as that module documents.

    :param request: The test's request object, for the finalizer.
    :param values: The keys to install.
    :returns: ``None``.
    """
    request.addfinalizer(lambda: config.set_userdata(None))
    config.set_userdata(values)


def _programme_page(driver: Any) -> None:
    """Programme every answer the nine steps read, so all of them pass.

    :param driver: The recorder to programme.
    :returns: ``None``.
    """
    driver.title = EXPECTED_TITLE
    driver.set_attribute(
        LOCATOR_INPUT_EMAIL, VALIDATION_ATTRIBUTE, FRENCH_REQUIRED_FIELD_MESSAGE
    )
    driver.set_attribute(LOCATOR_BULLET_PASS, TYPE_ATTRIBUTE, PASSWORD_INPUT_TYPE)
    driver.set_displayed(LOCATOR_ALERT_ERROR_MESSAGE, True)


#: Probe locators for the two steps whose real locators are indistinguishable.
#: ``INPUT_PASSWORD`` and ``BULLET_PASS`` are both ``(name, password)``, so a
#: test keyed on the selector cannot tell which accessor a step reached.  The
#: probes replace the two accessors with distinct selectors, and the recorder
#: then reports which one the step actually used.
PROBE_INPUT_PASSWORD: Final[tuple[str, str]] = ("name", "probe-input-password")
PROBE_BULLET_PASS: Final[tuple[str, str]] = ("name", "probe-bullet-pass")


def _install_accessor_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give ``input_password`` and ``bullet_pass`` distinguishable selectors.

    ``app/pages/base_page.py`` captures each locator **by closure** when the
    accessor is installed, so rebinding the constant would not redirect the
    lookup; the accessor itself is what has to be replaced.  Both replacements
    resolve through ``BasePage.find``, so the lookup still lands in the
    recorder's log exactly as the real accessor's would.

    :param monkeypatch: pytest's patcher, which restores both properties.
    :returns: ``None``.
    """
    monkeypatch.setattr(
        LoginPage,
        "input_password",
        property(lambda self: self.find(PROBE_INPUT_PASSWORD)),
    )
    monkeypatch.setattr(
        LoginPage,
        "bullet_pass",
        property(lambda self: self.find(PROBE_BULLET_PASS)),
    )


# --------------------------------------------------------------------------
# Feature-file helpers
# --------------------------------------------------------------------------

#: The Gherkin step keywords a step line may open with.
STEP_KEYWORDS: Final[tuple[str, ...]] = ("Given ", "When ", "Then ", "And ", "But ")


@functools.cache
def _feature_lines() -> tuple[str, ...]:
    """``features/Login.feature`` as lines, without their endings.

    :returns: One entry per line, in file order.
    """
    return tuple(FEATURE_PATH.read_text(encoding=SOURCE_ENCODING).splitlines())


@functools.cache
def _feature_step_uses() -> tuple[tuple[int, str], ...]:
    """Every step line of the feature, as ``(line number, phrase)``.

    The keyword is stripped because the port resolves by text alone - which is
    the whole point of AAP deviation 7 - so a phrase reached as ``And`` must
    resolve to the same definition as one reached as ``Then``.

    :returns: One entry per step line, in file order.
    """
    uses: list[tuple[int, str]] = []

    for number, line in enumerate(_feature_lines(), start=1):
        text = line.strip()

        for keyword in STEP_KEYWORDS:
            if text.startswith(keyword):
                uses.append((number, text[len(keyword) :].strip()))
                break

    return tuple(uses)


# ==========================================================================
# Phase 1 - the nine-method census and its registration
# ==========================================================================


def test_census_describes_nine_distinct_java_methods() -> None:
    """The census itself is nine unique methods - ``LoginSD.java:19-68``.

    A guard on this module's own table before anything is asserted against
    it: a duplicated pattern or a duplicated method name would make the
    detector below pass while a Java method went unpinned.
    """
    assert len(JAVA_METHODS) == 9
    assert len({method.name for method in JAVA_METHODS}) == 9
    assert len({method.pattern for method in JAVA_METHODS}) == 9
    assert len({method.declared_at for method in JAVA_METHODS}) == 9
    assert [method.declared_at for method in JAVA_METHODS] == sorted(
        method.declared_at for method in JAVA_METHODS
    ), "the census must stay in LoginSD.java declaration order"
    assert {method.annotation for method in JAVA_METHODS} == {"Given", "When", "Then"}


def test_login_steps_registers_exactly_nine_definitions(step_registry: Any) -> None:
    """Nine definitions, in Java declaration order - ``LoginSD.java:19-68``.

    ``LoginSD`` declares nine annotated methods and nothing else that behave
    could register, so a tenth definition here would be behaviour the
    reference does not have, and a missing one would be behaviour it does.
    The order is asserted too, because the JSON writer records a distinct
    ``match.location`` per definition (AAP deviation 8).
    """
    definitions = _registered_definitions(step_registry)

    assert len(definitions) == 9, (
        f"expected the nine LoginSD.java methods, found "
        f"{[definition.pattern for definition in definitions]}"
    )
    assert [definition.pattern for definition in definitions] == [
        method.pattern for method in JAVA_METHODS
    ]
    assert [definition.function_name for definition in definitions] == [
        method.name for method in JAVA_METHODS
    ]


def test_every_definition_registers_in_the_step_bucket(step_registry: Any) -> None:
    """All nine register with ``@step`` - AAP deviation 7.

    ``LoginSD.java`` is the suite's only user of Java's ``Given`` annotation
    (``:19``) and the class whose ``Then``-annotated dashboard step
    ``Logout.feature:18`` and ``:42`` reach as an effective ``When``.
    Cucumber-JVM matches on text alone, so a keyword-bound registration would
    leave those uses undefined; the registry must therefore show ``step`` for
    every one of the nine and nothing in the three keyword buckets - whichever
    of the three keywords the Java class annotated the method with.
    """
    definitions = _registered_definitions(step_registry)
    buckets_by_annotation = {
        JAVA_METHODS_BY_PATTERN[definition.pattern].annotation: definition.bucket
        for definition in definitions
    }

    assert {definition.bucket for definition in definitions} == {STEP_BUCKET}
    assert [
        definition.pattern
        for definition in definitions
        if definition.bucket != STEP_BUCKET
    ] == []
    assert buckets_by_annotation == {
        "Given": STEP_BUCKET,
        "When": STEP_BUCKET,
        "Then": STEP_BUCKET,
    }


@pytest.mark.parametrize("method", JAVA_METHODS, ids=[m.name for m in JAVA_METHODS])
def test_each_census_phrase_resolves_to_its_java_method(
    resolve_step: Callable[[str], Any], method: JavaMethod
) -> None:
    """Each phrase reaches the function that ports its Java method.

    The nine-phrase table, resolved through behave's own registry: the pattern
    must be the one the census records, the owning module must be
    ``login_steps``, and the function must carry the Java method's name
    unchanged - ``LoginSD.java:20``, ``:27``, ``:32``, ``:37``, ``:42``,
    ``:50``, ``:55``, ``:61`` and ``:66``.  ``resolve_step`` raises when a
    phrase is undefined or ambiguous, so one call covers both.
    """
    match = resolve_step(method.use)

    assert match.pattern == method.pattern
    assert match.module_name == STEP_MODULE_NAME
    assert match.func.__name__ == method.name
    assert match.bucket == STEP_BUCKET


def test_parameterised_phrases_hand_over_the_quoted_value(
    resolve_step: Callable[[str], Any],
) -> None:
    """The three ``{string}`` parameters arrive unquoted - ``:26``, ``:31``, ``:54``.

    Cucumber's ``{string}`` matches the surrounding quotes and passes the
    unquoted value; the port carries the quotes in the pattern, so the step
    receives exactly what the Examples cell held - ``Login.feature:23`` for
    the credentials and ``:89`` for the French message.
    """
    assert resolve_step(USE_USERNAME).kwargs == {"username": SAMPLE_USERNAME}
    assert resolve_step(USE_PASSWORD).kwargs == {"password": SAMPLE_PASSWORD}
    assert resolve_step(USE_VALIDATION).kwargs == {
        "alert_message": FRENCH_REQUIRED_FIELD_MESSAGE
    }


@pytest.mark.parametrize(
    ("phrase", "expected_module", "expected_function"),
    (
        (USE_USERNAME, STEP_MODULE_NAME, "user_enters_username"),
        (USE_PASSWORD, STEP_MODULE_NAME, "user_enters_password"),
        ('User enters "Haussman"', "contacts_steps", None),
        ('User enters name "&Dustin"', "contacts_steps", None),
    ),
)
def test_login_phrases_do_not_collide_with_the_contacts_bare_phrase(
    resolve_step: Callable[[str], Any],
    phrase: str,
    expected_module: str,
    expected_function: str | None,
) -> None:
    """The trailing literals keep ``:26`` and ``:31`` off Contacts' phrases.

    ``features/steps/contacts_steps.py`` registers a bare
    ``User enters "{...}"``, which is one non-greedy field away from swallowing
    a Login use.  What separates them is the trailing literal ``username`` or
    ``password`` in the Login patterns - ``LoginSD.java:26`` and ``:31`` - so
    each of the four phrases below must resolve to exactly one definition, and
    to the module that owns it.  ``resolve_step`` fails on ambiguity, which is
    the failure mode this guards.
    """
    match = resolve_step(phrase)

    assert match.module_name == expected_module

    if expected_function is not None:
        assert match.func.__name__ == expected_function


def test_every_login_feature_step_line_resolves_to_login_steps(
    resolve_step: Callable[[str], Any],
) -> None:
    """Every step line of ``Login.feature`` reaches this module, exactly once.

    The Background ``Given`` at ``:10`` and the seventeen outline steps, each
    resolved by text alone: ``Login.feature`` uses ``When``, ``And`` and
    ``Then`` over definitions ``LoginSD.java`` annotates ``Given``, ``When``
    and ``Then``, and none of that may affect resolution.  The patterns they
    reach are the nine of the census and no others, so the feature exercises
    every method of the Java class and the class carries nothing the feature
    cannot reach.
    """
    uses = _feature_step_uses()

    assert len(uses) == 18, f"unexpected step-line inventory: {uses!r}"

    reached: set[str] = set()

    for number, phrase in uses:
        match = resolve_step(phrase)
        assert match.module_name == STEP_MODULE_NAME, (
            f"Login.feature:{number} resolves to {match.module_name}, not "
            f"{STEP_MODULE_NAME}"
        )
        reached.add(match.pattern)

    assert reached == {method.pattern for method in JAVA_METHODS}


def test_covered_phrase_set_equals_all_nine_definitions(step_registry: Any) -> None:
    """No Java method is left without a behavioural test - AAP 0.4.1.

    The gap detector, and the reason the decorator records at import time: the
    set of patterns this file claims to pin, the census, and the set the
    registry actually carries must be one and the same set.  Deleting a test
    below, deleting a definition from the port, or adding an unpinned one all
    turn this red.

    The third assertion is what makes it sensitive to one test at a time: each
    method needs a claim of its own, so the nine-method wait census cannot
    stand in for the eight bodies it merely walks through.
    """
    census = {method.pattern for method in JAVA_METHODS}
    registered = {
        definition.pattern for definition in _registered_definitions(step_registry)
    }

    assert set(_PINNED) == census, (
        f"unpinned LoginSD.java methods: {sorted(census - set(_PINNED))}; "
        f"pinned but not in the census: {sorted(set(_PINNED) - census)}"
    )
    assert registered == census
    assert set(_PINNED_EXCLUSIVELY) == census, (
        f"LoginSD.java methods with no behavioural test of their own: "
        f"{sorted(census - set(_PINNED_EXCLUSIVELY))}"
    )


# ==========================================================================
# Phase 2 - the locator inventory the step bodies read
# ==========================================================================


@pytest.mark.parametrize(
    ("constant", "java_field", "locator"),
    (
        ("INPUT_EMAIL", "inputEmail", LOCATOR_INPUT_EMAIL),
        ("INPUT_PASSWORD", "inputPassword", LOCATOR_INPUT_PASSWORD),
        ("BUTTON", "button", LOCATOR_BUTTON),
        ("RESET_PASS", "resetPass", LOCATOR_RESET_PASS),
        ("DASHBOARD", "dashboard", LOCATOR_DASHBOARD),
        ("ALERT_ERROR_MESSAGE", "alertErrorMessage", LOCATOR_ALERT_ERROR_MESSAGE),
        ("BULLET_PASS", "bulletPass", LOCATOR_BULLET_PASS),
    ),
)
def test_locator_constants_match_the_java_find_by(
    constant: str, java_field: str, locator: tuple[str, str]
) -> None:
    """Each constant carries its ``@FindBy`` value - ``LoginP.java:13-32``.

    This is what closes the chain the body tests then walk: Java ``@FindBy``
    -> ``LoginPage`` constant -> the accessor a step reads -> the locator the
    driver receives.  The strategies are the literal strings the driver is
    given, which is also what the recorder logs, so a step asserted against a
    constant is asserted against the Java declaration too.
    """
    assert getattr(LoginPage, constant) == locator
    assert LoginPage.LOCATORS[constant] == locator
    assert isinstance(vars(LoginPage).get(constant.lower()), property), (
        f"LoginP.java's {java_field} must be reachable as the accessor "
        f"{constant.lower()!r}"
    )


def test_the_seven_locators_are_the_whole_inventory_in_java_order() -> None:
    """``LoginP.java:13-32`` declares seven fields and the page declares seven.

    The row-by-row test above proves each Java field arrived; this proves
    nothing else did.  ``BasePage.__init_subclass__`` builds ``LOCATORS`` from
    the class body in source order, so one equality pins the count, the names,
    the order and the values together: an eighth constant, a dropped one, a
    renamed one or a reordered pair all fail here, and none of them would fail
    the row-by-row test.
    """
    assert tuple(LoginPage.LOCATORS.items()) == (
        ("INPUT_EMAIL", LOCATOR_INPUT_EMAIL),
        ("INPUT_PASSWORD", LOCATOR_INPUT_PASSWORD),
        ("BUTTON", LOCATOR_BUTTON),
        ("RESET_PASS", LOCATOR_RESET_PASS),
        ("DASHBOARD", LOCATOR_DASHBOARD),
        ("ALERT_ERROR_MESSAGE", LOCATOR_ALERT_ERROR_MESSAGE),
        ("BULLET_PASS", LOCATOR_BULLET_PASS),
    )


def test_input_password_and_bullet_pass_are_one_selector_under_two_names() -> None:
    """``LoginP.java:16-17`` and ``:31-32`` declare the same selector twice.

    Source state, preserved rather than collapsed (AAP 0.2.2): the two
    constants are equal and the two accessors are distinct, which is exactly
    why the two steps that read them are discriminated by accessor below and
    not by locator.
    """
    assert LoginPage.INPUT_PASSWORD == LoginPage.BULLET_PASS == LOCATOR_INPUT_PASSWORD
    assert vars(LoginPage)["input_password"] is not vars(LoginPage)["bullet_pass"]


# ==========================================================================
# Phase 3 - the nine step bodies
# ==========================================================================


@pins(PATTERN_NAVIGATE)
def test_navigation_step_reads_web_table_url_and_navigates(
    request: pytest.FixtureRequest,
    resolve_step: Callable[[str], Any],
    fake_context: Any,
    stub_driver: Any,
) -> None:
    """``:22`` reads ``web.table.url``; ``:23`` navigates to it, and stops.

    All six configuration keys are installed with distinct values, so the
    assertion is about the **key** the step read and not merely about a URL
    arriving somewhere: reading ``url`` - the Employee module's key, whose
    only consumer is ``EmployeeStage.java:24`` - or either credential would
    navigate to a different value and fail here.  Navigation is the whole
    body: ``LoginSD.java:21``'s expected title is commented out, so nothing
    reads the title and no element is located.
    """
    _install_configuration(request, CONFIGURATION)

    resolve_step(USE_NAVIGATE).run(fake_context)

    assert stub_driver.calls == [("get", (CONFIGURATION[WEB_TABLE_URL_KEY],))]


@pins(PATTERN_NAVIGATE)
def test_navigation_step_passes_a_missing_url_through_unguarded(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    resolve_step: Callable[[str], Any],
    fake_context: Any,
    stub_driver: Any,
) -> None:
    """An absent ``web.table.url`` reaches ``:23`` as it is - AAP 0.6.

    A missing ``configuration.properties`` is tolerated by design and every
    accessor then answers ``None``.  ``LoginSD.java:22-23`` has no guard, no
    default and no validation, so the port must pass that ``None`` straight to
    the navigation and fail there rather than substituting a value or raising
    early.  Userdata is cleared and the properties layer is made to answer
    ``None``, so the outcome does not depend on whether this clone happens to
    carry a properties file.
    """
    _install_configuration(request, {})
    monkeypatch.setattr(properties, "get_property", lambda key: None)

    resolve_step(USE_NAVIGATE).run(fake_context)

    assert stub_driver.calls == [("get", (None,))]


@pins(PATTERN_NAVIGATE)
def test_navigation_step_body_matches_java() -> None:
    """``:22-23`` is a local and a navigation - and ``:21`` stays unported.

    The local is kept because the Java code has one, and the commented-out
    ``expectedTitle`` of ``LoginSD.java:21`` has no behaviour, so it may
    appear in prose but never in an executable statement.
    """
    assert _body_statements("user_is_on_the_upgenix_login_page") == (
        "url = get_web_table_url()",
        "context.driver.get(url)",
    )
    assert COMMENTED_OUT_TITLE not in _string_constants()


@pins(PATTERN_USERNAME)
def test_username_step_types_into_the_login_field(
    resolve_step: Callable[[str], Any], fake_context: Any, stub_driver: Any
) -> None:
    """``:28`` sends the keys to ``inputEmail`` - ``LoginP.java:13-14``.

    One lookup of ``(name, login)`` and one ``send_keys`` carrying the
    Examples value verbatim.  Nothing clears the field first and nothing
    validates or trims the value, because ``:28`` does neither - the
    ``@UPGN-288`` outline depends on the field being reachable in whatever
    state the previous step left it.
    """
    resolve_step(USE_USERNAME).run(fake_context)

    assert stub_driver.calls == [
        ("find_element", LOCATOR_INPUT_EMAIL),
        ("element.send_keys", (LOCATOR_INPUT_EMAIL, SAMPLE_USERNAME)),
    ]


@pins(PATTERN_PASSWORD)
def test_password_step_types_into_the_password_field(
    resolve_step: Callable[[str], Any], fake_context: Any, stub_driver: Any
) -> None:
    """``:33`` sends the keys to ``inputPassword`` - ``LoginP.java:16-17``.

    Reached as an effective ``When`` from ``Logout.feature:16`` and ``:40``,
    where the ``And`` inherits the preceding ``When``.
    """
    resolve_step(USE_PASSWORD).run(fake_context)

    assert stub_driver.calls == [
        ("find_element", LOCATOR_INPUT_PASSWORD),
        ("element.send_keys", (LOCATOR_INPUT_PASSWORD, SAMPLE_PASSWORD)),
    ]


@pins(PATTERN_PASSWORD)
def test_password_step_reads_the_input_password_accessor(
    monkeypatch: pytest.MonkeyPatch,
    resolve_step: Callable[[str], Any],
    fake_context: Any,
    stub_driver: Any,
) -> None:
    """``:33`` reads ``inputPassword``, not its twin ``bulletPass``.

    ``LoginP.java`` declares ``(name, password)`` twice, so the previous test
    cannot tell the two fields apart.  With the two accessors given distinct
    probe selectors, the lookup the recorder logs names which one the step
    read - and ``LoginSD.java:33`` reads ``inputPassword``.
    """
    _install_accessor_probes(monkeypatch)

    resolve_step(USE_PASSWORD).run(fake_context)

    assert stub_driver.calls_of("find_element") == (PROBE_INPUT_PASSWORD,)
    assert stub_driver.calls_of("element.send_keys") == (
        (PROBE_INPUT_PASSWORD, SAMPLE_PASSWORD),
    )
    assert _body_statements("user_enters_password") == (
        "_page(context).input_password.send_keys(password)",
    )


@pins(PATTERN_LOGIN_BUTTON)
def test_login_button_step_clicks_the_button(
    resolve_step: Callable[[str], Any], fake_context: Any, stub_driver: Any
) -> None:
    """``:38`` clicks ``button`` - ``LoginP.java:19-20``, and waits for nothing.

    No wait for clickability and no wait for the page that follows: the source
    relies on the session's 10-second implicit wait (``Driver.java:44``) and,
    where a scenario needs the dashboard, on the explicit wait in the
    dashboard step.
    """
    resolve_step(USE_LOGIN_BUTTON).run(fake_context)

    assert stub_driver.calls == [
        ("find_element", LOCATOR_BUTTON),
        ("element.click", (LOCATOR_BUTTON,)),
    ]


@pins(PATTERN_ENTER_BUTTON)
def test_enter_button_step_clicks_the_same_button_and_presses_no_key(
    resolve_step: Callable[[str], Any], fake_context: Any, stub_driver: Any
) -> None:
    """``:67`` is ``button.click()`` again - no key is pressed.

    The phrase says *enter button* and ``@UPGN-290`` intends the keyboard
    (``Login.feature:121``), but ``LoginSD.java`` imports neither ``Keys`` nor
    ``Actions`` and ``:67`` is byte-identical to ``:38``.  The two bodies are
    therefore asserted equal, and the step must send nothing: reproducing the
    phrase's intent instead of its body would change what the scenario
    exercises.  They stay two registered phrases because ``Login.feature:17``
    reaches one and ``:126`` the other.
    """
    resolve_step(USE_ENTER_BUTTON).run(fake_context)

    assert stub_driver.calls == [
        ("find_element", LOCATOR_BUTTON),
        ("element.click", (LOCATOR_BUTTON,)),
    ]
    assert stub_driver.calls_of("element.send_keys") == ()
    assert _body_statements("user_clicks_the_enter_button") == _body_statements(
        "user_clicks_the_login_button"
    )
    assert "press_keys" not in _imported_names()
    assert "action_chain" not in _imported_names()


@pins(PATTERN_DASHBOARD)
def test_dashboard_step_waits_three_seconds_then_reads_the_title(
    monkeypatch: pytest.MonkeyPatch,
    resolve_step: Callable[[str], Any],
    fake_context: Any,
    stub_driver: Any,
) -> None:
    """``:43-46``: wait 3s on the dashboard, then compare the title to ``Odoo``.

    Three parity facts, in the source's order:

    * the wait's target is ``loginP.dashboard`` - ``LoginP.java:25-26``,
      Odoo's ``oe_main_menu_navbar`` - and its timeout is the ``3`` that
      ``LoginSD.java:17`` fixes for the whole class, supplied at the call site
      because the reference sets a different one per class;
    * the title is read **after** the wait, from the session, once;
    * nothing else happens - no click, no typing, no second wait.

    Any element lookup is dropped from the sequence assertion, because whether
    one occurs depends on whether the wait is given an element or a locator,
    and neither shape changes the order of the wait and the title read.
    """
    match = resolve_step(USE_DASHBOARD)
    _install_wait_recorder(monkeypatch, match.func, stub_driver)
    stub_driver.title = EXPECTED_TITLE

    match.run(fake_context)

    assert _wait_calls(stub_driver) == ((LOCATOR_DASHBOARD, 3),)
    assert _operations_except(stub_driver, "find_element") == (
        WAIT_OPERATION,
        "title",
    )
    assert stub_driver.count_of("title") == 1
    assert set(stub_driver.calls_of("find_element")) <= {LOCATOR_DASHBOARD}
    assert [
        operation
        for operation, _ in stub_driver.calls
        if operation.startswith("element.")
    ] == []


@pins(PATTERN_DASHBOARD)
def test_dashboard_step_discards_the_wait_result() -> None:
    """``:43`` throws the wait's return value away, as Java does.

    The statement is a bare call, so a timeout propagates as itself and
    nothing downstream depends on what the wait resolved to.  The callee is
    identified by prefix only: which visibility helper the call site names is
    not a parity fact, whereas discarding its result is.
    """
    statements = _body_statements("user_should_see_the_dashboard")

    assert len(statements) == 4
    assert statements[0].startswith(WAIT_NAME_PREFIX)
    assert statements[1:] == (
        f"expected_dashboard = {EXPECTED_TITLE!r}",
        "actual_dashboard = context.driver.title",
        "assert expected_dashboard == actual_dashboard, "
        f"{TITLE_ASSERTION_MESSAGE!r}",
    )


@pins(
    PATTERN_NAVIGATE,
    PATTERN_USERNAME,
    PATTERN_PASSWORD,
    PATTERN_LOGIN_BUTTON,
    PATTERN_DASHBOARD,
    PATTERN_ERROR_MESSAGE,
    PATTERN_VALIDATION,
    PATTERN_BULLET_SIGNS,
    PATTERN_ENTER_BUTTON,
)
def test_the_dashboard_step_is_the_only_one_that_waits(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    resolve_step: Callable[[str], Any],
    fake_context: Any,
    stub_driver: Any,
) -> None:
    """``LoginSD.java`` builds one wait and uses it once - at ``:43``.

    The class's only ``wait.until`` is in the dashboard step, and no method
    sleeps: ``LoginSD.java`` holds none of the suite's seventeen
    ``Thread.sleep`` calls.  Every one of the nine bodies is driven here and
    the wait count is counted per step, so a wait added to any other step -
    or removed from the dashboard step - fails.  It also demonstrates that the
    nine bodies run to completion against a stub with no browser: each
    assertion below is satisfied by the programmed answers alone.
    """
    _install_configuration(request, CONFIGURATION)
    _programme_page(stub_driver)
    _install_wait_recorder(
        monkeypatch, resolve_step(USE_DASHBOARD).func, stub_driver
    )

    waits_by_pattern: dict[str, int] = {}

    for method in JAVA_METHODS:
        stub_driver.clear_calls()
        resolve_step(method.use).run(fake_context)
        waits_by_pattern[method.pattern] = len(_wait_calls(stub_driver))

    assert waits_by_pattern == {
        method.pattern: (1 if method.pattern == PATTERN_DASHBOARD else 0)
        for method in JAVA_METHODS
    }


@pins(PATTERN_ERROR_MESSAGE)
def test_error_message_step_reads_is_displayed_on_the_alert(
    resolve_step: Callable[[str], Any], fake_context: Any, stub_driver: Any
) -> None:
    """``:51`` asserts ``alertErrorMessage.isDisplayed()`` - ``LoginP.java:28-29``.

    One lookup of the ``alert`` class and one visibility read, which is the
    whole body: the ``@UPGN-287`` outline is its only consumer and the lookup
    itself is what fails when the banner is absent altogether.
    """
    stub_driver.set_displayed(LOCATOR_ALERT_ERROR_MESSAGE, True)

    resolve_step(USE_ERROR_MESSAGE).run(fake_context)

    assert stub_driver.calls == [
        ("find_element", LOCATOR_ALERT_ERROR_MESSAGE),
        ("element.is_displayed", (LOCATOR_ALERT_ERROR_MESSAGE,)),
    ]


@pins(PATTERN_VALIDATION)
def test_validation_message_step_reads_the_attribute_through_a_direct_by(
    resolve_step: Callable[[str], Any], fake_context: Any, stub_driver: Any
) -> None:
    """``:56`` builds ``By.name("login")`` on the driver itself.

    The one place in the suite where a step reaches ``By`` directly instead of
    using a page field - which is why ``login_steps`` is the only step module
    that imports it - and the reason the lookup goes to ``context.driver``
    rather than through the page object.  The observable half is the log: one
    ``(name, login)`` lookup and one read of the camelCase
    ``validationMessage`` property.  The half a log cannot show - that the
    locator was built inline - is pinned from the source, because the page's
    ``INPUT_EMAIL`` carries the same selector.
    """
    stub_driver.set_attribute(
        LOCATOR_INPUT_EMAIL, VALIDATION_ATTRIBUTE, FRENCH_REQUIRED_FIELD_MESSAGE
    )

    resolve_step(USE_VALIDATION).run(fake_context)

    assert stub_driver.calls == [
        ("find_element", LOCATOR_INPUT_EMAIL),
        ("element.get_attribute", (LOCATOR_INPUT_EMAIL, VALIDATION_ATTRIBUTE)),
    ]
    assert _body_statements("user_sees_please_fill_out_this_field_message")[0] == (
        "expected_message = context.driver.find_element(By.NAME, 'login')"
        f".get_attribute({VALIDATION_ATTRIBUTE!r})"
    )
    assert "By" in _imported_names()


@pins(PATTERN_VALIDATION)
def test_validation_message_step_keeps_the_inverted_naming(
    resolve_step: Callable[[str], Any], fake_context: Any
) -> None:
    """``:56-57`` name the live DOM value ``expected`` and the Gherkin value ``actual``.

    The roles are inverted with respect to what the names suggest, and Java's
    two-argument ``assertEquals`` makes that invisible at run time because
    equality is symmetric.  It is preserved rather than quietly corrected, so
    it is pinned where it is observable - in the source - alongside the fact
    that the comparison carries no message string.
    """
    statements = _body_statements("user_sees_please_fill_out_this_field_message")

    assert len(statements) == 2
    assert statements[0].startswith("expected_message = context.driver")
    assert statements[1] == "assert expected_message == alert_message"
    assert resolve_step(USE_VALIDATION).kwargs == {
        "alert_message": FRENCH_REQUIRED_FIELD_MESSAGE
    }


@pins(PATTERN_BULLET_SIGNS)
def test_masking_step_reads_the_type_attribute(
    resolve_step: Callable[[str], Any], fake_context: Any, stub_driver: Any
) -> None:
    """``:62`` reads ``getAttribute("type")`` off the password control.

    Drives ``@UPGN-289``, the one outline that never submits the form: one
    lookup, one attribute read, and a comparison against the literal
    ``"password"``.
    """
    stub_driver.set_attribute(LOCATOR_BULLET_PASS, TYPE_ATTRIBUTE, PASSWORD_INPUT_TYPE)

    resolve_step(USE_BULLET_SIGNS).run(fake_context)

    assert stub_driver.calls == [
        ("find_element", LOCATOR_BULLET_PASS),
        ("element.get_attribute", (LOCATOR_BULLET_PASS, TYPE_ATTRIBUTE)),
    ]


@pins(PATTERN_BULLET_SIGNS)
def test_masking_step_reads_the_bullet_pass_accessor(
    monkeypatch: pytest.MonkeyPatch,
    resolve_step: Callable[[str], Any],
    fake_context: Any,
    stub_driver: Any,
) -> None:
    """``:62`` reads ``bulletPass`` - ``LoginP.java:31-32`` - not ``inputPassword``.

    The choice of field is the parity fact here, and a selector comparison
    cannot see it: ``LoginP.java`` declares ``(name, password)`` under both
    names.  With the two accessors given distinct probe selectors, the log
    reports which constant the step's accessor derives from.
    """
    _install_accessor_probes(monkeypatch)
    stub_driver.set_attribute(PROBE_BULLET_PASS, TYPE_ATTRIBUTE, PASSWORD_INPUT_TYPE)

    resolve_step(USE_BULLET_SIGNS).run(fake_context)

    assert stub_driver.calls_of("find_element") == (PROBE_BULLET_PASS,)
    assert stub_driver.calls_of("element.get_attribute") == (
        (PROBE_BULLET_PASS, TYPE_ATTRIBUTE),
    )
    assert _body_statements("user_should_see_the_password_in_bullet_signs") == (
        f"assert _page(context).bullet_pass.get_attribute({TYPE_ATTRIBUTE!r}) "
        f"== {PASSWORD_INPUT_TYPE!r}",
    )


def test_no_step_reads_the_reset_password_link(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    resolve_step: Callable[[str], Any],
    fake_context: Any,
    stub_driver: Any,
) -> None:
    """``resetPass`` is declared and never read - ``LoginP.java:22-23``.

    Source state, kept rather than tidied: the Java class declares seven
    fields and its nine methods read six of them, and ``Login.feature:102``
    leaves the reset-password scenario as a comment.  Driving all nine bodies
    must therefore never look the link up.
    """
    _install_configuration(request, CONFIGURATION)
    _programme_page(stub_driver)
    _install_wait_recorder(
        monkeypatch, resolve_step(USE_DASHBOARD).func, stub_driver
    )

    for method in JAVA_METHODS:
        resolve_step(method.use).run(fake_context)

    assert LOCATOR_RESET_PASS not in stub_driver.calls_of("find_element")
    assert "reset_pass" not in _module_source()


# ==========================================================================
# Phase 4 - the four assertions, driven both ways
#
# AAP deviation 16: the subject and the message text are the parity, and
# Python cannot produce JUnit's ``expected:<...> but was:<...>`` framing.  So
# each test below drives its assertion to failure through the stub's
# programmable answers and pins ``AssertionError.args`` - the byte-exact
# message where Java's three-argument ``assertEquals`` supplies one, and its
# absence where the one-argument ``assertTrue`` and the two-argument
# ``assertEquals`` do not.
# ==========================================================================


@pins(PATTERN_DASHBOARD)
def test_dashboard_assertion_fails_with_the_trailing_space_message(
    monkeypatch: pytest.MonkeyPatch,
    resolve_step: Callable[[str], Any],
    fake_context: Any,
    stub_driver: Any,
) -> None:
    """``:46``'s message ends in exactly one space, byte for byte.

    ``Assert.assertEquals("The title is not same as the expected! ", ...)``
    carries a trailing space that ``LogOutSD.java:27`` shares and that
    ``Calendar.java:46`` and ``Sales.java:37`` do not.  The message is
    asserted as the assertion's single argument, so a stripped or reworded
    one fails, and the wait still has to have happened first.
    """
    match = resolve_step(USE_DASHBOARD)
    _install_wait_recorder(monkeypatch, match.func, stub_driver)
    stub_driver.title = "Odoo - Sales"

    with pytest.raises(AssertionError) as failure:
        match.run(fake_context)

    assert failure.value.args == (TITLE_ASSERTION_MESSAGE,)
    assert TITLE_ASSERTION_MESSAGE.endswith("! ")
    assert TITLE_ASSERTION_MESSAGE != TITLE_ASSERTION_MESSAGE.rstrip()
    assert _wait_calls(stub_driver) == ((LOCATOR_DASHBOARD, 3),)


@pins(PATTERN_DASHBOARD)
def test_dashboard_assertion_passes_for_the_exact_odoo_title(
    monkeypatch: pytest.MonkeyPatch,
    resolve_step: Callable[[str], Any],
    fake_context: Any,
    stub_driver: Any,
) -> None:
    """``:44-46`` compares the title to ``"Odoo"`` with ``equals`` semantics.

    The passing half of the assertion, and the one title that satisfies it -
    the literal of ``LoginSD.java:44``, which AAP Conflict 8 preserves as
    written rather than reconciling with the *Testinium* of the feature title.
    """
    match = resolve_step(USE_DASHBOARD)
    _install_wait_recorder(monkeypatch, match.func, stub_driver)
    stub_driver.title = EXPECTED_TITLE

    match.run(fake_context)

    assert stub_driver.count_of("title") == 1


@pins(PATTERN_DASHBOARD)
@pytest.mark.parametrize("title", ("odoo", "ODOO", "Odoo ", " Odoo", "Odoo - Home", ""))
def test_dashboard_assertion_rejects_every_near_miss_title(
    monkeypatch: pytest.MonkeyPatch,
    resolve_step: Callable[[str], Any],
    fake_context: Any,
    stub_driver: Any,
    title: str,
) -> None:
    """``:46`` is an exact comparison: no trimming, no case folding.

    Java's ``assertEquals`` on two strings compares them character for
    character, so each near miss below must fail with the same message.
    """
    match = resolve_step(USE_DASHBOARD)
    _install_wait_recorder(monkeypatch, match.func, stub_driver)
    stub_driver.title = title

    with pytest.raises(AssertionError) as failure:
        match.run(fake_context)

    assert failure.value.args == (TITLE_ASSERTION_MESSAGE,)


@pins(PATTERN_ERROR_MESSAGE)
def test_error_message_assertion_carries_no_message(
    resolve_step: Callable[[str], Any], fake_context: Any, stub_driver: Any
) -> None:
    """``:51``'s ``assertTrue`` is given no message, so neither is the port's.

    A located-but-hidden banner is what reaches the assertion, and it must
    fail with no message text at all.
    """
    stub_driver.set_displayed(LOCATOR_ALERT_ERROR_MESSAGE, False)

    with pytest.raises(AssertionError) as failure:
        resolve_step(USE_ERROR_MESSAGE).run(fake_context)

    assert failure.value.args == ()
    assert stub_driver.calls_of("element.is_displayed") == (
        (LOCATOR_ALERT_ERROR_MESSAGE,),
    )


@pins(PATTERN_ERROR_MESSAGE)
def test_error_message_assertion_propagates_a_missing_banner(
    resolve_step: Callable[[str], Any], fake_context: Any, stub_driver: Any
) -> None:
    """``:51`` catches nothing: an absent banner fails at the lookup.

    ``LoginSD.java`` has no exception handling, so a lookup failure - what the
    real driver raises once the session's 10-second implicit wait expires -
    travels out of the step uncaught and is not converted into an assertion
    failure.
    """
    stub_driver.set_find_error(
        LOCATOR_ALERT_ERROR_MESSAGE, LookupError("no such element: .alert")
    )

    with pytest.raises(LookupError):
        resolve_step(USE_ERROR_MESSAGE).run(fake_context)

    assert stub_driver.calls_of("find_element") == (LOCATOR_ALERT_ERROR_MESSAGE,)


@pins(PATTERN_VALIDATION)
def test_validation_message_assertion_passes_on_the_french_string(
    resolve_step: Callable[[str], Any], fake_context: Any, stub_driver: Any
) -> None:
    """``:57`` compares the DOM message to ``Login.feature:89``'s text.

    The ``@UPGN-288`` round trip: the browser's own required-field message,
    programmed onto the login input, must equal the string the feature file
    supplies - byte for byte, accents included.  Which locale a browser
    reports it in is left unresolved by AAP 0.6, so nothing here sets, reads
    or infers one.
    """
    stub_driver.set_attribute(
        LOCATOR_INPUT_EMAIL, VALIDATION_ATTRIBUTE, FRENCH_REQUIRED_FIELD_MESSAGE
    )

    resolve_step(USE_VALIDATION).run(fake_context)

    assert stub_driver.count_of("element.get_attribute") == 1


@pins(PATTERN_VALIDATION)
@pytest.mark.parametrize(
    "reported",
    (
        "Please fill out this field.",
        "Veuillez renseigner ce champ",
        "veuillez renseigner ce champ.",
        None,
    ),
)
def test_validation_message_assertion_fails_without_a_message(
    resolve_step: Callable[[str], Any],
    fake_context: Any,
    stub_driver: Any,
    reported: str | None,
) -> None:
    """``:57`` is the two-argument ``assertEquals``, so it has no message.

    Each value below is what a browser in another locale, or with the field
    already filled, would report - ``None`` being Selenium's answer for an
    attribute that is not there.  All of them must fail, and none of them may
    produce message text the source does not supply.
    """
    stub_driver.set_attribute(LOCATOR_INPUT_EMAIL, VALIDATION_ATTRIBUTE, reported)

    with pytest.raises(AssertionError) as failure:
        resolve_step(USE_VALIDATION).run(fake_context)

    assert failure.value.args == ()


@pins(PATTERN_BULLET_SIGNS)
@pytest.mark.parametrize("reported", ("text", "Password", "", None))
def test_masking_assertion_fails_without_a_message(
    resolve_step: Callable[[str], Any],
    fake_context: Any,
    stub_driver: Any,
    reported: str | None,
) -> None:
    """``:62`` wraps ``String.equals`` in a one-argument ``assertTrue``.

    An unmasked field - or one reporting the type in another case, or not
    reporting it at all - must fail, with no message text, because the Java
    call supplies none.
    """
    stub_driver.set_attribute(LOCATOR_BULLET_PASS, TYPE_ATTRIBUTE, reported)

    with pytest.raises(AssertionError) as failure:
        resolve_step(USE_BULLET_SIGNS).run(fake_context)

    assert failure.value.args == ()


# ==========================================================================
# Phase 5 - module boundaries and feature-text preservation
# ==========================================================================


def test_every_definition_is_registered_with_the_step_decorator() -> None:
    """Nine ``@step`` decorators and no keyword decorator - AAP deviation 7.

    The registry check above proves where the definitions landed; this proves
    how they got there, which is what stops a later edit reintroducing a
    keyword-bound decorator that would leave ``Logout.feature:18``'s effective
    ``When`` use of the ``Then``-annotated dashboard step undefined.
    """
    decorated = [
        name
        for name in _function_defs()
        if any(
            decorator.startswith("step(") for decorator in _decorators(name)
        )
    ]

    assert len(decorated) == 9
    assert set(decorated) == {method.name for method in JAVA_METHODS}

    for name in _function_defs():
        for decorator in _decorators(name):
            assert not decorator.startswith(("given(", "when(", "then(")), (
                f"{name} is registered with {decorator}, which resolves only "
                f"for that keyword"
            )

    assert {"given", "when", "then", "Given", "When", "Then"}.isdisjoint(
        _imported_names()
    )


def test_module_import_boundary_is_closed() -> None:
    """AAP 0.4.2's import list, asserted from the source - ``LoginSD.java:3-12``.

    Five facts, each of which the specification states outright:

    * **the import list is closed**: the engine and the ``app`` package are
      the only roots, so no third-party library - a browser binding above all
      - can be reached from a step body;
    * **no browser-library import of any kind**, because ``app.automation`` is
      the only package permitted one and it re-exports ``By`` precisely so
      that ``:56`` can be ported without naming that library;
    * every ``app.automation`` name comes from the package barrel rather than
      a submodule, and ``By`` is among them;
    * ``app.config`` is the configuration surface and
      ``app/utils/properties.py`` is never imported here - that module has one
      permitted consumer;
    * the page object comes from ``app.pages``.
    """
    modules = _imported_modules()

    assert {name.split(".")[0] for name in modules} == {"behave", "app"}
    assert not [name for name in modules if name.split(".")[0] == "selenium"]
    assert "app.automation" in modules
    assert not [name for name in modules if name.startswith("app.automation.")]
    assert "By" in _imported_names()
    assert "app.config" in modules
    assert not [name for name in modules if name.startswith("app.utils")]
    assert "app.pages" in modules
    assert "LoginPage" in _imported_names()


def test_module_holds_no_fixed_delay() -> None:
    """``LoginSD.java`` calls no ``Thread.sleep`` - AAP 0.4.1.

    Seventeen fixed delays exist across five step classes and none of them is
    here, so neither a delay nor the means to one may appear: the five classes
    that do have them are Calendar, Contacts, Crm, EmployeeStage and Notes.
    """
    assert "sleep" not in _called_attribute_names()
    assert not [
        name
        for name in _imported_modules()
        if name.split(".")[0] in {"time", "asyncio"}
    ]
    assert "sleep" not in _imported_names()


def test_module_never_creates_or_quits_a_driver() -> None:
    """No step or page ever creates or quits a driver - AAP 0.3.3.

    ``features/environment.py`` owns the scenario lifecycle exclusively and
    publishes the session as ``context.driver``, which is what every body
    reads.  ``LoginSD.java:17`` does reach ``Driver.getDriver()`` at glue
    construction, and that is exactly what must not become a module-level
    binding here - a session captured at import would belong to whichever
    worker imported the module first.
    """
    assert {"get_driver", "quit_driver"}.isdisjoint(_imported_names())
    assert {"get_driver", "quit_driver", "quit"}.isdisjoint(_called_attribute_names())


def test_nothing_binds_at_import_time() -> None:
    """``LoginSD.java:16-17``'s fields become per-call locals, not module state.

    The module's top level must be imports, ``__all__`` and function
    definitions and nothing else: no page object, no driver reference, no wait
    object, no cached URL and no logger, because a process pool shares one
    module import but not one browser session.
    """
    assignments: list[str] = []

    for statement in _module_tree().body:
        if _is_docstring(statement):
            continue

        if isinstance(statement, (ast.Import, ast.ImportFrom, ast.FunctionDef)):
            continue

        assert isinstance(statement, (ast.Assign, ast.AnnAssign)), (
            f"unexpected module-level statement: {ast.unparse(statement)}"
        )
        assignments.append(ast.unparse(statement))

    assert len(assignments) == 1
    assert assignments[0].startswith("__all__ = [")


def test_login_feature_header_and_tag_are_preserved() -> None:
    """``Login.feature:1-10`` is carried verbatim - AAP 0.4.1.

    The feature-level ``@Login`` tag, the title that says *Testinium* where
    the step text says *upgenix* and the assertion says *Odoo* - all three
    vocabularies preserved where they occur, per AAP Conflict 8's *"no
    renaming"* - and the Background whose one step is the navigation step.
    """
    lines = _feature_lines()

    assert lines[0] == "@Login"
    assert lines[1] == "Feature: Testinium app login feature"
    assert lines[8] == (
        "  Background: For the scenarios in the feature file, user is "
        "expected to be on login page"
    )
    assert lines[9] == f"    Given {PATTERN_NAVIGATE}"


def test_five_outline_tags_appear_once_each() -> None:
    """The five Jira tags of ``Login.feature:13-122``, each exactly once.

    ``@UPGN-286`` valid login, ``@UPGN-287`` invalid credentials,
    ``@UPGN-288`` the empty field, ``@UPGN-289`` the password bullets and
    ``@UPGN-290`` the Enter key: five outlines, each reachable by its own tag
    expression.  The two Examples-level tags are counted too, because they
    select the SalesManager and PosManager blocks separately.
    """
    tags = [line.strip() for line in _feature_lines() if line.strip().startswith("@")]

    for number in range(286, 291):
        assert tags.count(f"@UPGN-{number}") == 1

    assert tags.count("@SalesManager") == 5
    assert tags.count("@PosManager") == 5
    assert tags.count("@Login") == 1
    assert len(tags) == 16


def test_examples_block_names_keep_their_apostrophes() -> None:
    """The ten ``Examples`` names of ``Login.feature``, character for character.

    Eight carry a possessive apostrophe and the last two read differently
    again; all ten reach the Cucumber JSON as element-name suffixes, so a
    tidied apostrophe would change the artifacts.
    """
    names = [
        line.strip()
        for line in _feature_lines()
        if line.strip().startswith("Examples:")
    ]

    assert names == [
        "Examples: SalesManager's username and password",
        "Examples: PosManager's username and password",
        "Examples: SalesManager's username and password",
        "Examples: PosManager's username and password",
        "Examples: SalesManager's username and password",
        "Examples: PosManager's username and password",
        "Examples: SalesManager's username and password",
        "Examples: PosManager's username and password",
        "Examples: SalesManager enter the button after mail and password",
        "Examples: PosManager enter the button after mail and password",
    ]


def test_third_outline_header_has_no_space_after_its_colon() -> None:
    """``Login.feature:86`` reads ``Scenario Outline:Users``, unspaced.

    A typo in the source, preserved: the name a scenario carries into
    ``cucumber.json`` and both HTML reports is the text after the colon, so
    inserting the space would change the artifacts.  The two outlines that do
    have the space are asserted beside it, so the difference is what is
    pinned rather than the absence of a space anywhere.
    """
    lines = _feature_lines()

    assert lines[85] == (
        "  Scenario Outline:Users log in with invalid email or invalid "
        "password credentials"
    )
    assert lines[58] == (
        "  Scenario Outline: Users log in with invalid email or invalid "
        "password credentials"
    )
    assert lines[13] == "  Scenario Outline: Users log in with valid credentials"


def test_french_required_field_string_is_byte_exact() -> None:
    """``Login.feature:89`` carries the French message verbatim - AAP 0.6.

    The browser's own required-field text, whose language follows a locale
    neither repository sets.  It is carried through byte for byte - accents,
    the trailing full stop and the quotes that make it a step parameter - and
    the port adds no locale key, so the configuration surface stays at exactly
    six keys.
    """
    lines = _feature_lines()

    assert lines[88] == f'    Then User sees "{FRENCH_REQUIRED_FIELD_MESSAGE}" message'
    assert FRENCH_REQUIRED_FIELD_MESSAGE == "Veuillez renseigner ce champ."
    assert FRENCH_REQUIRED_FIELD_MESSAGE in _module_source(), (
        "the French string must survive in the step module's documentation "
        "of Login.feature:89"
    )


def test_examples_credentials_are_carried_verbatim() -> None:
    """The 48 Examples rows stay as they are - AAP 0.8.

    *"The Gherkin Examples tables carry plaintext credentials for the system
    under test and are carried over verbatim because parity requires it ...
    no agent should redact, parameterize or rotate them."*  The row counts are
    the ones AAP 0.4.1 records for the valid-login outline - 13 SalesManager
    and 15 PosManager - and the four rows spelled out below are the ones an
    over-eager cleanup would alter: two whose cell padding carries a
    significant trailing space, and two whose passwords hold punctuation.
    """
    lines = _feature_lines()
    rows = [line for line in lines if line.strip().startswith("|")]

    assert len(rows) == 58, "ten header rows plus the 48 data rows"

    for row in (
        "      |salesmanager7@info.com |salesmanager|",
        "      |posmanagerr6@info.com  |posmanager |",
        "      |salesmanager8@info.com |sale@g@0fz8r|",
        "      |posmanager9@info.com   |posFkc@ma#$|",
    ):
        assert row in lines, f"missing Examples row: {row!r}"

    assert rows.index("      |salesmanager7@info.com |salesmanager|") == 1
    assert lines[21] == "      |username               |password    |"
