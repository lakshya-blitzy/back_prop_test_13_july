"""Java parity tests for ``features/steps/session_steps.py``.

The per-module parity obligation AAP 0.4.1 places on every step module, for the
smallest and most load-bearing of the ten: ``Session.java`` at pinned revision
``47e9d697e4a9a85da889f94a846fdf47af28a240``.  That class is 19 lines long and
declares exactly one step method, whose four statements are the shared
sign-in precondition six other features invoke from their ``Background`` - the
reason AAP 0.4.4 states the port could not be split into phases: *"the shared
precondition step in ``Session.java`` is invoked by other features'
backgrounds, so porting any one feature area alone yields undefined steps."*

The authority, and the direction of every assertion
---------------------------------------------------
Every expectation below is cross-referenced to the Java source and the feature
files, never to the Python implementation - a test written from the port would
agree with it by construction and prove nothing.  The anchor, verbatim
(``Session.java:10-18``)::

    SessionP session = new SessionP();                                  // :10

    @When("User login to test other features")                          // :12
    public void user_login_to_test_other_features() {                   // :13
        Driver.getDriver().get(ConfigurationReader.getProperty("web.table.url"));
        session.inputLogin.sendKeys(ConfigurationReader.getProperty("username"));
        session.inputPass.sendKeys(ConfigurationReader.getProperty("password"));
        session.loginButton.click();                                    // :17
    }

paired with ``SessionP.java:14-21``::

    @FindBy(id = "login")     public WebElement inputLogin;         // :14-15
    @FindBy(id = "password")  public WebElement inputPass;          // :17-18
    @FindBy(xpath = "//button[.='Log in']") public WebElement loginButton;  // :20-21

Four operations, in that order, and nothing else.  The class constructs no
``WebDriverWait``, declares no assertion, calls no ``Thread.sleep``, touches no
``Keys`` or ``Actions``, prints nothing and catches nothing - so the negative
half of this module carries as much weight as the positive half, and is
asserted just as directly.  ``ConfigurationReader.getProperty`` returns
``null`` for an absent key, which AAP 0.4.1 keeps deliberately so that
*"failures surface at the point of use"*; the port must therefore invent no
default, no validation and no message, and a missing value must reach the
browser call unchanged.

What is pinned here, in the order the tests appear
--------------------------------------------------
1. **The census.** The Java class's one method, against the definitions the
   module registers - so an omitted or an invented definition fails rather
   than passing silently, which is the gap detector AAP 0.4.1 requires.
2. **Module boundaries, by source inspection.** ``@step`` and not ``@when``
   (AAP deviation 7, and decisive here: registered under one keyword, the six
   ``Given`` call sites would report an undefined step); the three imports the
   module's contract closes at; no browser library, no ``By``, nothing from
   ``app.automation``, no properties module; and no branch, assertion, delay
   or error path.
3. **The four operations**, their order, their three configuration sources and
   their key names, read through ``app/config.py``.
4. **The locators**, as ``SessionP.java``'s ``@FindBy`` annotations declare
   them - ``By.ID`` for both inputs, which is *not* ``LoginP.java:13``'s
   ``By.NAME`` for the same field, and the discrimination is asserted
   explicitly because conflating the two is the standing trap on this form.
5. **The absences**: no wait, no fixed delay, no assertion, no extra driver
   operation, no session created or quit, nothing attached, nothing stored.
6. **The cross-keyword reach**: seven invocation sites parsed out of
   ``features/*.feature`` - six ``Given`` against a ``@When`` declaration -
   each resolving to the one definition, corroborated by the committed
   baseline artifact.
7. **``features/Session.feature``** itself: ``Feature: Default``, one
   scenario, no tag, and the pinned digest of a file that carries no
   terminating newline.

Constraints this module observes
--------------------------------
No network, no browser, no sleep and no live ``configuration.properties``.
The session is :fixture:`stub_driver`, a recorder; the context is
:fixture:`fake_context`; configuration is driven through ``app/config.py``'s
own public override API or through the step module's own accessor bindings,
per test and never process-wide.  Nothing here writes to disk.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Final, NamedTuple

import pytest

from app import automation, config
from app.pages import SessionPage, base_page

# --------------------------------------------------------------------------
# Locations
#
# Derived from this file's own position rather than from the working
# directory: the suite must read the same sources whatever directory pytest
# was started from.
# --------------------------------------------------------------------------

#: Repository root, from this file's position: tests/ -> repository root.
REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]

FEATURES_DIR: Final[Path] = REPO_ROOT / "features"
STEP_MODULE_PATH: Final[Path] = FEATURES_DIR / "steps" / "session_steps.py"
SESSION_FEATURE_PATH: Final[Path] = FEATURES_DIR / "Session.feature"

#: The committed Cucumber-JVM baseline, held as a fixture by another part of
#: the suite and read here by path for the one fact it carries about this
#: step: the keyword the JVM recorded against it.
GOLDEN_CUCUMBER_PATH: Final[Path] = (
    REPO_ROOT / "tests" / "fixtures" / "golden_cucumber.json"
)

#: The module name behave reports for a definition loaded from
#: :data:`STEP_MODULE_PATH` - the file stem, because ``load_step_modules``
#: execs the file rather than importing it.
STEP_MODULE_NAME: Final[str] = "session_steps"

#: The locator strategies, reached through ``app/automation/__init__.py``'s
#: single authorized re-export of the browser library's ``By`` - the same door
#: ``app/pages/session_page.py`` uses, so this module never imports selenium
#: itself.
By: Final[Any] = automation.By


# --------------------------------------------------------------------------
# The Java authority
# --------------------------------------------------------------------------


class JavaStepMethod(NamedTuple):
    """One step method of ``Session.java``, as the reference declares it."""

    #: The Java method name, which the port's function name must equal.
    method: str

    #: The Gherkin phrase inside the method's annotation.
    phrase: str

    #: Line of the ``@When`` annotation in ``Session.java``.
    annotation_line: int

    #: Lines of the method's statements, in execution order.
    body_lines: tuple[int, ...]


#: The whole of ``Session.java``'s step surface: one method, four statements.
#: This tuple is the census every registration test is measured against, so a
#: definition the module registers without an entry here - or an entry with no
#: definition - is a failure rather than an omission nobody notices.
JAVA_STEP_METHODS: Final[tuple[JavaStepMethod, ...]] = (
    JavaStepMethod(
        method="user_login_to_test_other_features",
        phrase="User login to test other features",
        annotation_line=12,
        body_lines=(14, 15, 16, 17),
    ),
)

#: The one phrase, named once so no test re-types it.
PHRASE: Final[str] = JAVA_STEP_METHODS[0].phrase

#: ``match.location`` as the committed baseline records it for this step.
JAVA_MATCH_LOCATION: Final[str] = (
    "com.testinium.step_definitions.Session.user_login_to_test_other_features()"
)

#: The keyword the JVM recorded at the six cross-keyword call sites, trailing
#: space included exactly as the artifact carries it.
JVM_GIVEN_KEYWORD: Final[str] = "Given "

#: ``SessionP.java``'s three ``@FindBy`` annotations, by the constant name
#: ``app/pages/session_page.py`` gives each one.  ``id`` for both inputs is the
#: annotation's own strategy and is deliberately *not* reconciled with
#: ``LoginP.java:13``'s ``name`` for the same field (AAP 0.2.2, AAP 0.8).
JAVA_FIND_BY: Final[dict[str, tuple[str, str]]] = {
    "INPUT_LOGIN": ("id", "login"),
    "INPUT_PASS": ("id", "password"),
    "LOGIN_BUTTON": ("xpath", "//button[.='Log in']"),
}

#: The operation names, in the order ``Session.java:14-17`` performs them.
#: One ``find_element`` precedes each element operation because the port's
#: accessors re-resolve on every access, exactly as the ``PageFactory`` proxy
#: of ``SessionP.java:10-12`` did.
EXPECTED_OPERATIONS: Final[tuple[str, ...]] = (
    "get",  # :14
    "find_element",  # :15, the inputLogin lookup
    "element.send_keys",  # :15
    "find_element",  # :16, the inputPass lookup
    "element.send_keys",  # :16
    "find_element",  # :17, the loginButton lookup
    "element.click",  # :17
)

#: Every operation the suite's recorder can log, under the names it logs them
#: - the driver surface first, then the element surface with its prefix.  It is
#: spelled out in full so that the set of operations this step must *not*
#: perform can be derived rather than guessed at, which is what keeps a
#: misspelled operation name from becoming an assertion that can never fail.
RECORDABLE_OPERATIONS: Final[tuple[str, ...]] = (
    "get",
    "find_element",
    "find_elements",
    "title",
    "current_url",
    "get_screenshot_as_png",
    "maximize_window",
    "implicitly_wait",
    "quit",
    "execute_script",
    "back",
    "execute",
    "element.click",
    "element.send_keys",
    "element.clear",
    "element.get_attribute",
    "element.is_displayed",
    "element.is_selected",
    "element.text",
    "element.tag_name",
)

#: Everything the recorder could show that ``Session.java:14-17`` does not do.
#: A visibility wait would surface here as ``element.is_displayed`` or
#: ``execute_script``, a title assertion as ``title``, and a teardown as
#: ``quit``.
UNPERFORMED_OPERATIONS: Final[tuple[str, ...]] = tuple(
    operation
    for operation in RECORDABLE_OPERATIONS
    if operation not in set(EXPECTED_OPERATIONS)
)


# --------------------------------------------------------------------------
# The configuration authority
#
# Three keys, three accessors, three distinguishable values.  The values are
# distinguishable on purpose: with a shared placeholder, a port that typed the
# password into the login field would still pass.
# --------------------------------------------------------------------------

#: ``Session.java:14``.  The sign-in page - *not* the ``url`` key, which
#: ``EmployeeStage.java`` reads for the Employee module, a different page.
KEY_WEB_TABLE_URL: Final[str] = "web.table.url"

#: ``Session.java:15``.
KEY_USERNAME: Final[str] = "username"

#: ``Session.java:16``.
KEY_PASSWORD: Final[str] = "password"

#: The three keys in the order the Java reads them.
JAVA_KEY_ORDER: Final[tuple[str, ...]] = (
    KEY_WEB_TABLE_URL,
    KEY_USERNAME,
    KEY_PASSWORD,
)

#: The ``app/config.py`` accessor that serves each key, and therefore the three
#: names the step module must resolve.  Patching these in the module's own
#: globals is the precise seam for pairing a value with its operation.
ACCESSOR_FOR_KEY: Final[dict[str, str]] = {
    KEY_WEB_TABLE_URL: "get_web_table_url",
    KEY_USERNAME: "get_username",
    KEY_PASSWORD: "get_password",
}

URL_VALUE: Final[str] = "https://sut.example/web/login"
USERNAME_VALUE: Final[str] = "the-username"
PASSWORD_VALUE: Final[str] = "the-password"

#: The three values as configuration, keyed by the Java key names.
SESSION_CONFIGURATION: Final[dict[str, str]] = {
    KEY_WEB_TABLE_URL: URL_VALUE,
    KEY_USERNAME: USERNAME_VALUE,
    KEY_PASSWORD: PASSWORD_VALUE,
}


# --------------------------------------------------------------------------
# The module-boundary authority (AAP 0.4.2)
# --------------------------------------------------------------------------

#: Every ``(module, imported name)`` pair the step module's contract allows -
#: the engine's type-agnostic decorator, the three configuration accessors its
#: Java original reads through ``ConfigurationReader``, and the one page class
#: its Java original declares as a field.  The list is closed: the Java
#: imports ``SessionP``, ``ConfigurationReader``, ``Driver`` and ``When``, and
#: ``Driver`` is the one the port deliberately does not reproduce, because AAP
#: 0.3.3 reserves the session lifecycle to ``features/environment.py``.
ALLOWED_IMPORTS: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        ("behave", "step"),
        ("app.config", "get_web_table_url"),
        ("app.config", "get_username"),
        ("app.config", "get_password"),
        ("app.pages", "SessionPage"),
    }
)

#: Import roots the step module may not reach, each for a stated reason:
#: the browser library and its driver manager (only ``app/automation`` may),
#: ``app.automation`` itself (no wait, no ``By``, no driver accessor, no
#: interaction helper - ``Session.java`` uses none of them), ``app.utils``
#: (``app/config.py`` is the only permitted reader of the properties module),
#: ``time`` (none of the suite's seventeen fixed sleeps is in this class),
#: ``parse`` (this phrase takes no parameter and registers no type converter)
#: and the application's own web, service and reporting layers.
FORBIDDEN_IMPORT_ROOTS: Final[tuple[str, ...]] = (
    "selenium",
    "webdriver_manager",
    "app.automation",
    "app.utils",
    "time",
    "parse",
    "app.reporting",
    "app.services",
    "app.web",
    "flask",
)

#: Decorator names that would bind the definition to one keyword.  Both cases
#: are listed because behave injects the capitalized aliases into every step
#: module's namespace alongside the lower-case ones.
KEYWORD_DECORATORS: Final[frozenset[str]] = frozenset(
    {"given", "when", "then", "Given", "When", "Then"}
)

#: The type-agnostic decorator AAP 0.5.2 requires, and its capitalized alias.
STEP_DECORATORS: Final[frozenset[str]] = frozenset({"step", "Step"})

#: Names whose presence in the module's namespace would mean a wait, a delay,
#: a key dispatch, an action chain or a driver lifecycle call was reachable
#: from this body.  The automation surface is taken from the package's own
#: ``__all__`` rather than hand-listed, so a helper renamed or added there is
#: covered here without this file being edited - and no helper's signature or
#: argument order is asserted anywhere in this module.
FORBIDDEN_BINDINGS: Final[frozenset[str]] = frozenset(automation.__all__) | frozenset(
    {
        "sleep",
        "time",
        "WebDriverWait",
        "expected_conditions",
        "Keys",
        "ActionChains",
        "register_type",
        "with_pattern",
        "assert_that",
    }
)

#: Substrings that betray a wait or a delay in a called expression.  Matched
#: against the *called* expression only, never against a signature, so the
#: sibling work that is changing visibility-wait call sites elsewhere cannot
#: make this assertion wrong.
FORBIDDEN_CALL_MARKERS: Final[tuple[str, ...]] = ("wait", "sleep", "delay")


# --------------------------------------------------------------------------
# The feature-file authority
# --------------------------------------------------------------------------


class FeatureSite(NamedTuple):
    """One place a feature file invokes a phrase."""

    #: File name within ``features/``.
    feature: str

    #: 1-based line number.
    line: int

    #: The Gherkin keyword the site uses.
    keyword: str

    #: The step text as the file carries it, so resolution can be driven from
    #: the file's own bytes rather than from a phrase typed again here.
    text: str


def _site(feature: str, line: int, keyword: str) -> FeatureSite:
    """One measured invocation site, whose text is :data:`PHRASE`.

    :param feature: File name within ``features/``.
    :param line: 1-based line number.
    :param keyword: The Gherkin keyword at that line.
    :returns: The site.
    """
    return FeatureSite(feature, line, keyword, PHRASE)


#: The invocation census as measured against the pinned reference's own
#: ``src/main/resources/features/``, where every line number below is
#: identical.  The tests derive the same census from ``features/*.feature`` on
#: disk and compare the two, so neither a feature edit nor a stale expectation
#: can pass unnoticed.
REFERENCE_SITES: Final[tuple[FeatureSite, ...]] = (
    _site("Calendar.feature", 9, "Given"),
    _site("Contact.feature", 5, "Given"),
    _site("Crm.feature", 7, "Given"),
    _site("Inventory.feature", 9, "Given"),
    _site("Notes.feature", 8, "Given"),
    _site("Sales.feature", 10, "Given"),
    _site("Session.feature", 4, "When"),
)

#: The one feature whose ``Background`` declares no step and which therefore
#: does not invoke this phrase: its flow signs in through
#: ``EmployeePage.login()`` (``EmployeeP.java:59-69``) instead.
FEATURE_WITHOUT_THE_PRECONDITION: Final[str] = "EmployeeFc.feature"

#: SHA-256 of ``features/Session.feature`` as committed - measured with
#: ``sha256sum``.  The file carries **no** terminating newline, which is part
#: of what this digest pins.
SESSION_FEATURE_SHA256: Final[str] = (
    "8cd9a9ee02dab1e0b72bc7df49a31f6c466dce3c205668b005e75e6ebc538b98"
)

#: A Gherkin step line, with its keyword and its text.  ``*`` is included
#: because Gherkin accepts it as a keyword, so a step written that way would
#: still be counted rather than silently missed.
STEP_LINE: Final[re.Pattern[str]] = re.compile(
    r"^(?P<keyword>Given|When|Then|And|But|\*)\s+(?P<text>\S.*?)\s*$"
)

#: Encoding of every source file read here.  The feature files and the step
#: module are UTF-8; this is deliberately not the ISO-8859-1 of
#: ``configuration.properties``, which nothing in this module reads.
SOURCE_ENCODING: Final[str] = "utf-8"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _step_module_tree() -> ast.Module:
    """Parse the step module, for the assertions that read its source.

    Source inspection rather than runtime inspection, because the questions it
    answers are about what the file *says*: which decorator registered the
    definition, which imports it holds, and that it contains no branch,
    assertion or delay.  The module's own docstring quotes the Java
    ``@When(...)`` annotation verbatim, so a text search would find that quote
    and conclude the wrong thing; a parse tree cannot be fooled by a docstring.

    :returns: The parsed module.
    """
    source = STEP_MODULE_PATH.read_text(encoding=SOURCE_ENCODING)
    return ast.parse(source, filename=str(STEP_MODULE_PATH))


def _imported_pairs(tree: ast.Module) -> set[tuple[str, str]]:
    """Every ``(module, imported name)`` pair the module imports.

    :param tree: The parsed step module.
    :returns: One pair per imported name.  A plain ``import x`` yields
        ``("x", "x")`` so that both forms are comparable against
        :data:`ALLOWED_IMPORTS`.
    """
    pairs: set[tuple[str, str]] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            pairs.update((alias.name, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            # ``level`` is non-zero for a relative import, which has no
            # absolute module name; recording the dots keeps such an import
            # visible to the comparison instead of collapsing it to "".
            module = "." * node.level + (node.module or "")
            pairs.update((module, alias.name) for alias in node.names)

    return pairs


def _decorated_functions(tree: ast.Module) -> dict[str, list[str]]:
    """Map each top-level function to the names of its decorators.

    :param tree: The parsed step module.
    :returns: Function name -> decorator names, with a call decorator reduced
        to the name being called, so ``@step("...")`` reads as ``step``.
    """
    decorated: dict[str, list[str]] = {}

    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        names: list[str] = []

        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            names.append(ast.unparse(target))

        decorated[node.name] = names

    return decorated


def _step_decorator_call(tree: ast.Module, function: str) -> ast.Call:
    """The decorator call that registered *function*.

    :param tree: The parsed step module.
    :param function: Name of the step function.
    :returns: The single decorator, as a call node.
    :raises AssertionError: If the function is missing, or is not decorated
        with exactly one call - either of which would mean the registration is
        not what this module is written to inspect.
    """
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == function:
            assert len(node.decorator_list) == 1, (
                f"{function} carries {len(node.decorator_list)} decorators; "
                f"Session.java:12 declares exactly one annotation"
            )
            decorator = node.decorator_list[0]
            assert isinstance(decorator, ast.Call), (
                f"{function}'s decorator is not a call, so it registers no "
                f"phrase"
            )
            return decorator

    raise AssertionError(f"{STEP_MODULE_PATH} declares no function {function!r}")


def _called_expressions(tree: ast.Module) -> set[str]:
    """Every called expression in the module, rendered as source.

    :param tree: The parsed step module.
    :returns: The unparsed callee of each call, such as
        ``context.driver.get``.
    """
    return {
        ast.unparse(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)
    }


def _matcher_module_name(matcher: Any) -> str:
    """The file stem of the step module a registered matcher came from.

    :param matcher: A behave step matcher from the registry.
    :returns: The stem, such as ``session_steps``.  behave renders the
        location relative to the working directory, so the stem - not the
        path - is what identifies the owning module.
    """
    location = getattr(matcher, "location", None)
    filename = getattr(location, "filename", None) or location or ""
    return Path(str(filename)).stem


def _owned_matchers(registry: Any) -> dict[str, tuple[Any, ...]]:
    """Group the definitions registered by the step module, by bucket.

    Every bucket behave keeps is scanned, not just ``step``, so a definition
    accidentally bound to a keyword is *found and reported* rather than missed.

    :param registry: behave's populated step registry.
    :returns: Bucket name -> the matchers this module registered in it.
        Buckets the module registered nothing in are omitted.
    """
    owned: dict[str, tuple[Any, ...]] = {}

    for bucket, matchers in registry.steps.items():
        found = tuple(
            matcher
            for matcher in matchers
            if _matcher_module_name(matcher) == STEP_MODULE_NAME
        )

        if found:
            owned[bucket] = found

    return owned


def _invocation_sites() -> tuple[FeatureSite, ...]:
    """Every feature-file site that invokes :data:`PHRASE`, parsed from disk.

    Derived from the files rather than from a copied list, which is the point:
    a feature edit that added, moved or removed an invocation changes this
    result and is caught by the comparison against :data:`REFERENCE_SITES`.

    :returns: The sites, in file-name then line order.
    """
    sites: list[FeatureSite] = []

    for path in sorted(FEATURES_DIR.glob("*.feature")):
        text = path.read_text(encoding=SOURCE_ENCODING)

        for number, line in enumerate(text.splitlines(), start=1):
            matched = STEP_LINE.match(line.strip())

            if matched is not None and matched.group("text") == PHRASE:
                sites.append(
                    FeatureSite(
                        path.name,
                        number,
                        matched.group("keyword"),
                        matched.group("text"),
                    )
                )

    return tuple(sites)


def _expected_calls(
    url: Any,
    username: Any,
    password: Any,
) -> tuple[tuple[str, tuple[Any, ...]], ...]:
    """The complete ordered call log ``Session.java:14-17`` prescribes.

    The locators are the page class's own constants, so the assertion closes
    the chain the parity obligation asks for: the Java ``@FindBy`` annotation,
    the constant derived from it, and the operation performed through it.

    :param url: Value the navigation is expected to carry (``:14``).
    :param username: Value expected in the login field (``:15``).
    :param password: Value expected in the password field (``:16``).
    :returns: The ``(operation, args)`` pairs, in order.
    """
    return (
        ("get", (url,)),
        ("find_element", SessionPage.INPUT_LOGIN),
        ("element.send_keys", (SessionPage.INPUT_LOGIN, username)),
        ("find_element", SessionPage.INPUT_PASS),
        ("element.send_keys", (SessionPage.INPUT_PASS, password)),
        ("find_element", SessionPage.LOGIN_BUTTON),
        ("element.click", (SessionPage.LOGIN_BUTTON,)),
    )


# --------------------------------------------------------------------------
# Fixtures local to this module
# --------------------------------------------------------------------------


@pytest.fixture
def step_match(resolve_step: Callable[[str], Any]) -> Any:
    """The one definition ``Session.java:12`` declares, resolved by text alone.

    :param resolve_step: The suite's resolver, which consults no keyword and
        fails when a phrase matches none or more than one definition.
    :returns: The resolved ``StepMatch``.
    """
    return resolve_step(PHRASE)


@pytest.fixture
def installed_configuration(request: pytest.FixtureRequest) -> dict[str, str]:
    """Install :data:`SESSION_CONFIGURATION` through ``app/config.py``.

    Uses the module's own public override channel - behave userdata, which
    ``app/config.py`` reads *ahead of* the properties file - so the values
    travel the real accessor path and no properties file is needed.  It is
    also the sharpest available proof that the step reads its configuration
    through ``app/config.py``: the properties module knows nothing of
    userdata, so a step that had reached the file layer directly would see
    none of these values.

    The finalizer is registered *before* the install, as ``app/config.py`` and
    the suite's isolation fixture both document, so the slot is cleared even
    if the test fails.

    :param request: The test request, used for its finalizer.
    :returns: The installed mapping, for a test that wants to assert against
        the values it supplied.
    """
    request.addfinalizer(lambda: config.set_userdata(None))
    config.set_userdata(dict(SESSION_CONFIGURATION))
    return dict(SESSION_CONFIGURATION)


@pytest.fixture
def recorded_keys(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every configuration key the step asks ``app/config.py`` for.

    ``app/config.py``'s six accessors all delegate to its module-level
    ``get_property``, so substituting that one function captures the key names
    the accessors pass - which is what turns "reads the username" into "reads
    the key ``username``".

    :param monkeypatch: pytest's patcher, for guaranteed restoration.
    :returns: The list the keys are appended to, in request order.
    """
    requested: list[str] = []

    def _recording_get_property(key: str) -> str | None:
        requested.append(key)
        return SESSION_CONFIGURATION.get(key)

    monkeypatch.setattr(config, "get_property", _recording_get_property)
    return requested


# --------------------------------------------------------------------------
# The census: one Java method, one registered definition
# --------------------------------------------------------------------------


def test_module_registers_exactly_the_javas_one_step_method(
    step_registry: Any,
) -> None:
    """``Session.java:8-19`` declares one step method, so the port registers one.

    The count is compared against :data:`JAVA_STEP_METHODS` rather than
    against a literal, so the census and the assertion cannot drift apart.
    """
    owned = _owned_matchers(step_registry)
    registered = tuple(
        matcher for matchers in owned.values() for matcher in matchers
    )

    assert len(registered) == len(JAVA_STEP_METHODS) == 1, (
        f"{STEP_MODULE_NAME} registers {len(registered)} definitions; "
        f"Session.java declares {len(JAVA_STEP_METHODS)}"
    )


def test_covered_phrase_set_equals_the_java_census(step_registry: Any) -> None:
    """Every ``Session.java`` phrase is covered, and no phrase is invented.

    The gap detector AAP 0.4.1 requires, asserted as a set equality in both
    directions: a missing definition and a surplus one each fail here.
    """
    owned = _owned_matchers(step_registry)
    patterns = {
        matcher.pattern for matchers in owned.values() for matcher in matchers
    }

    assert patterns == {method.phrase for method in JAVA_STEP_METHODS}


def test_function_name_matches_the_java_method_name(step_registry: Any) -> None:
    """The port keeps ``Session.java:13``'s method name verbatim.

    The name is the link the committed baseline artifact records in
    ``match.location``, which is why it is parity rather than cosmetics.
    """
    owned = _owned_matchers(step_registry)
    names = {
        matcher.func.__name__ for matchers in owned.values() for matcher in matchers
    }

    assert names == {method.method for method in JAVA_STEP_METHODS}


def test_definition_is_registered_in_the_keyword_agnostic_bucket(
    step_registry: Any,
    step_match: Any,
) -> None:
    """``Session.java:12``'s ``@When`` must not become a keyword-bound registration.

    The phrase is declared ``@When`` yet invoked as ``Given`` at six of its
    seven call sites, so a registration in behave's ``when`` bucket would
    leave those six undefined.  Both directions are asserted: the definition
    is in the type-agnostic bucket, and this module registered nothing in any
    keyword bucket.
    """
    owned = _owned_matchers(step_registry)

    assert step_match.bucket == "step"
    assert step_match.module_name == STEP_MODULE_NAME
    assert set(owned) == {"step"}, (
        f"{STEP_MODULE_NAME} registered definitions in {sorted(owned)}; AAP "
        f"deviation 7 allows the type-agnostic bucket alone"
    )


def test_phrase_resolves_to_exactly_one_definition(step_match: Any) -> None:
    """One implementation for the phrase, and it carries no parameter.

    Resolution itself is the assertion for uniqueness - the resolver fails on
    a phrase that matches none or more than one definition - and
    ``Session.java:13``'s empty parameter list is asserted on top of it, from
    the resolved arguments and from the signature.
    """
    assert step_match.pattern == PHRASE
    assert step_match.args == ()
    assert step_match.kwargs == {}
    assert list(inspect.signature(step_match.func).parameters) == ["context"]


# --------------------------------------------------------------------------
# Module boundaries, by source inspection
# --------------------------------------------------------------------------


def test_definition_is_registered_with_the_type_agnostic_decorator() -> None:
    """The decorator at the definition is ``step``, never ``when``.

    Read from the parse tree, because the module's docstring quotes
    ``Session.java:12``'s ``@When("User login to test other features")``
    verbatim and a text search would match that quote.  The phrase inside the
    decorator is asserted here too, so the registration and the Java
    annotation are compared at their source.
    """
    tree = _step_module_tree()
    method = JAVA_STEP_METHODS[0]
    decorator = _step_decorator_call(tree, method.method)

    assert ast.unparse(decorator.func) in STEP_DECORATORS, (
        f"the definition is registered with {ast.unparse(decorator.func)!r}; "
        f"Session.java:{method.annotation_line}'s @When becomes @step in the "
        f"port, because six of its seven call sites say Given"
    )
    assert [ast.literal_eval(argument) for argument in decorator.args] == [
        method.phrase
    ]
    assert decorator.keywords == []


def test_no_function_in_the_module_is_bound_to_a_keyword() -> None:
    """No ``@given``, ``@when`` or ``@then`` anywhere in the module.

    Stated over every top-level function rather than only the step, so a
    second definition added later cannot slip in keyword-bound.
    """
    decorated = _decorated_functions(_step_module_tree())
    used = {name for names in decorated.values() for name in names}

    assert used.isdisjoint(KEYWORD_DECORATORS), (
        f"keyword-bound decorators found: {sorted(used & KEYWORD_DECORATORS)}"
    )
    assert used <= STEP_DECORATORS


def test_module_imports_are_the_three_its_contract_allows() -> None:
    """Exactly the engine decorator, the three accessors and the page class.

    ``Session.java:3-6`` imports ``SessionP``, ``ConfigurationReader``,
    ``Driver`` and ``When``; the port reproduces three of those four and
    deliberately not ``Driver``, whose lifecycle AAP 0.3.3 assigns to
    ``features/environment.py``.
    """
    tree = _step_module_tree()
    statements = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]

    assert _imported_pairs(tree) == set(ALLOWED_IMPORTS)
    assert len(statements) == 3


def test_module_imports_no_browser_library_waits_or_properties_module() -> None:
    """None of ``Session.java``'s absent collaborators is imported.

    One assertion per reason it is absent: the browser library belongs to
    ``app/automation`` alone; the class constructs no wait and uses no ``By``,
    ``Keys`` or ``Actions``; ``app/config.py`` is the only permitted reader of
    the properties module; and no fixed delay of the suite's seventeen is
    here.
    """
    imported = _imported_pairs(_step_module_tree())
    modules = {module for module, _ in imported}
    names = {name for _, name in imported}

    for root in FORBIDDEN_IMPORT_ROOTS:
        offenders = {
            module
            for module in modules
            if module == root or module.startswith(f"{root}.")
        }
        assert not offenders, f"{STEP_MODULE_NAME} imports {sorted(offenders)}"

    assert "By" not in names
    assert ("app.config", "get_web_table_url") in imported
    assert not any(module.startswith(".") for module in modules)


def test_module_namespace_binds_no_wait_delay_or_driver_helper(
    step_match: Any,
) -> None:
    """Nothing a wait, a delay or a session call could be reached through.

    Asserted against the namespace the step body actually resolves its names
    in, and against the automation package's own published surface rather than
    a hand-listed one - so no helper's name or argument shape is pinned here,
    only the fact that none of them is reachable.  ``Session.java`` builds no
    ``WebDriverWait``, which makes it the one step class of the ten with no
    wait at all.
    """
    namespace = set(step_match.func.__globals__)

    assert namespace.isdisjoint(FORBIDDEN_BINDINGS), (
        f"reachable from {STEP_MODULE_NAME}: "
        f"{sorted(namespace & FORBIDDEN_BINDINGS)}"
    )
    # The three accessors the Java's three ``getProperty`` calls become, and
    # the page class its field becomes, are present - so the disjointness
    # above is a real result rather than an empty namespace.
    assert set(ACCESSOR_FOR_KEY.values()) <= namespace
    assert "SessionPage" in namespace


def test_module_calls_no_wait_or_delay() -> None:
    """No called expression in the module names a wait, a sleep or a delay.

    Matched against callees only.  A call site's arguments are never inspected
    here, so the concurrent work on the port's visibility-wait signatures
    cannot make this assertion say anything it does not mean.
    """
    called = _called_expressions(_step_module_tree())
    offenders = {
        expression
        for expression in called
        for marker in FORBIDDEN_CALL_MARKERS
        if marker in expression.lower()
    }

    assert not offenders, f"{STEP_MODULE_NAME} calls {sorted(offenders)}"


def test_module_declares_no_assertion_branch_or_error_path() -> None:
    """``Session.java`` asserts nothing, branches nowhere and catches nothing.

    Each node class below stands for an addition that would be a parity break
    rather than an improvement (AAP 0.8, *"Preserve, do not tidy"*): an
    ``assert`` the Java class has no ``Assert`` import for; an ``if`` or a
    conditional expression, which is how a default for a missing configuration
    value would be invented; a ``BoolOp``, which is how ``value or "default"``
    would be spelled; and ``try``/``raise``, which is the error handling the
    class does not perform - ``ConfigurationReader`` returning ``null`` is
    meant to surface at the point of use.
    """
    tree = _step_module_tree()
    forbidden = (ast.Assert, ast.If, ast.IfExp, ast.BoolOp, ast.Try, ast.Raise)
    found = sorted(
        {
            type(node).__name__
            for node in ast.walk(tree)
            if isinstance(node, forbidden)
        }
    )

    assert found == [], f"{STEP_MODULE_NAME} contains {found}"


# --------------------------------------------------------------------------
# The locators, against SessionP.java's @FindBy annotations
# --------------------------------------------------------------------------


def test_locator_constants_match_the_java_findby_annotations() -> None:
    """``SessionP.java:14-21``, constant by constant.

    Each constant is compared against the annotation's own strategy and
    selector, including the single quotes inside the button XPath, which are
    character-identical to ``LoginP.java:19``'s.
    """
    for name, expected in JAVA_FIND_BY.items():
        assert getattr(SessionPage, name) == expected, (
            f"SessionPage.{name} does not match its @FindBy annotation"
        )

    assert SessionPage.INPUT_LOGIN == (By.ID, "login")
    assert SessionPage.INPUT_PASS == (By.ID, "password")
    assert SessionPage.LOGIN_BUTTON == (By.XPATH, "//button[.='Log in']")


def test_the_three_locators_are_the_whole_inventory_in_java_order() -> None:
    """``SessionP.java:14-21`` declares three fields and the page declares three.

    The loop above proves each Java field arrived; this proves nothing else
    did.  ``BasePage.__init_subclass__`` builds ``LOCATORS`` from the class
    body in source order, so one equality pins the count, the names, the order
    and the values at once - a fourth constant, a dropped one, a renamed one
    or a reordered pair each fail here and none of them fail the loop.
    """
    assert tuple(SessionPage.LOCATORS.items()) == tuple(JAVA_FIND_BY.items())


def test_login_and_password_fields_are_located_by_id_not_by_name() -> None:
    """``SessionP.java:14`` and ``:17`` say ``id``; ``LoginP.java:13`` says ``name``.

    Three page objects address the same Odoo form with two strategies, and AAP
    0.2.2 keeps the divergence rather than reconciling it.  The discrimination
    is asserted explicitly because ``(By.ID, "login")`` and
    ``(By.NAME, "login")`` differ by one word and would resolve a different
    element on a form whose ``id`` and ``name`` attributes disagree.
    """
    assert By.ID != By.NAME

    assert SessionPage.INPUT_LOGIN[0] == By.ID
    assert SessionPage.INPUT_LOGIN != (By.NAME, "login")
    assert SessionPage.INPUT_PASS[0] == By.ID
    assert SessionPage.INPUT_PASS != (By.NAME, "password")


# --------------------------------------------------------------------------
# The four operations of Session.java:14-17
# --------------------------------------------------------------------------


def test_step_performs_the_javas_four_operations_in_order(
    step_match: Any,
    fake_context: Any,
    stub_driver: Any,
    installed_configuration: dict[str, str],
) -> None:
    """``Session.java:14-17``: navigate, type, type, click - and nothing else.

    One assertion over the whole ordered log, which is what makes it a
    sequence test rather than three unordered presence tests: a port that
    clicked before typing, or that typed the password first, produces a
    different tuple.  The three values are mutually distinguishable, so a
    swapped pair fails here.
    """
    assert len(set(installed_configuration.values())) == 3

    step_match.run(fake_context)

    assert tuple(stub_driver.calls) == _expected_calls(
        URL_VALUE, USERNAME_VALUE, PASSWORD_VALUE
    )
    assert stub_driver.operations() == EXPECTED_OPERATIONS

    # One driver-visible operation per statement of the Java body: the three
    # lookups are ``PageFactory`` dereferences rather than statements of their
    # own, so they are excluded from this count and pinned by the log above.
    performed = tuple(
        operation
        for operation in stub_driver.operations()
        if operation != "find_element"
    )
    assert len(performed) == len(JAVA_STEP_METHODS[0].body_lines) == 4


def test_each_value_reaches_the_operation_its_java_line_pairs_it_with(
    monkeypatch: pytest.MonkeyPatch,
    step_match: Any,
    fake_context: Any,
    stub_driver: Any,
) -> None:
    """``:14`` the URL, ``:15`` the username, ``:16`` the password - never crossed.

    Driven through the accessor bindings in the step module's own namespace,
    which is the narrowest seam available: each accessor returns a sentinel
    naming itself, so the pairing of value to operation is read straight off
    the log and cannot be satisfied by luck.
    """
    sentinels = {
        accessor: f"<{accessor}-sentinel>" for accessor in ACCESSOR_FOR_KEY.values()
    }

    for accessor, value in sentinels.items():
        # Asserted present before it is replaced: patching in a name the
        # module does not resolve would make this test pass vacuously.
        assert accessor in step_match.func.__globals__
        monkeypatch.setitem(
            step_match.func.__globals__, accessor, lambda value=value: value
        )

    step_match.run(fake_context)

    assert tuple(stub_driver.calls) == _expected_calls(
        sentinels["get_web_table_url"],
        sentinels["get_username"],
        sentinels["get_password"],
    )


def test_configuration_is_read_through_the_three_java_key_names_in_order(
    step_match: Any,
    fake_context: Any,
    stub_driver: Any,
    recorded_keys: list[str],
) -> None:
    """``:14`` ``web.table.url``, ``:15`` ``username``, ``:16`` ``password``.

    The key names are the parity subject here, not only the values:
    ``web.table.url`` is the sign-in page while ``url`` - which
    ``EmployeeStage.java`` reads - is the Employee module, a different page,
    so a step reading the wrong dotted name would navigate somewhere the Java
    never goes.  The order is asserted as well, because the Java reads the
    three in exactly this sequence.
    """
    step_match.run(fake_context)

    assert tuple(recorded_keys) == JAVA_KEY_ORDER
    assert set(JAVA_KEY_ORDER) <= set(config.CONFIG_KEYS)
    assert "url" not in recorded_keys
    assert tuple(stub_driver.calls) == _expected_calls(
        URL_VALUE, USERNAME_VALUE, PASSWORD_VALUE
    )


def test_missing_configuration_reaches_the_browser_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    step_match: Any,
    fake_context: Any,
    stub_driver: Any,
) -> None:
    """An unset key is ``null`` in Java, and must stay ``None`` all the way down.

    ``ConfigurationReader.getProperty`` returns ``null`` for a missing key and
    AAP 0.4.1 keeps that so *"failures surface at the point of use"*.  So the
    port invents no default, raises nothing of its own and skips no operation:
    the navigation is attempted with ``None`` and both fields are typed with
    ``None``, leaving the browser to reject them exactly as the Java does.
    """
    monkeypatch.setattr(config, "get_property", lambda key: None)
    config.set_userdata(None)

    step_match.run(fake_context)

    assert tuple(stub_driver.calls) == _expected_calls(None, None, None)
    assert stub_driver.operations() == EXPECTED_OPERATIONS


def test_step_returns_none(
    step_match: Any,
    fake_context: Any,
    installed_configuration: dict[str, str],
) -> None:
    """``Session.java:13`` is ``void``, so the port returns nothing."""
    assert step_match.run(fake_context) is None


# --------------------------------------------------------------------------
# The absences, which are half of this class's behaviour
# --------------------------------------------------------------------------


def test_step_performs_no_driver_operation_beyond_the_javas_four(
    step_match: Any,
    fake_context: Any,
    stub_driver: Any,
    installed_configuration: dict[str, str],
) -> None:
    """Nothing precedes, separates or follows the four operations.

    The complete-log assertion above already forbids an extra operation; this
    one is exhaustive over the recorder's whole vocabulary and names the
    intruder when it fails.  Every operation the Java body does not perform is
    asserted absent - a wait's visibility read or script execution, a title or
    URL read, a plural lookup, a field clear, an attribute read, a browser-back
    and a teardown among them - and the three lookups are counted, because
    three are exactly what re-resolving each of the three fields once requires.
    """
    step_match.run(fake_context)

    assert set(EXPECTED_OPERATIONS) <= set(RECORDABLE_OPERATIONS)
    assert len(UNPERFORMED_OPERATIONS) == len(RECORDABLE_OPERATIONS) - 4
    assert stub_driver.count_of("find_element") == 3

    for operation in UNPERFORMED_OPERATIONS:
        assert stub_driver.count_of(operation) == 0, (
            f"the step performed {operation!r}, which Session.java:14-17 "
            f"does not"
        )


def test_step_performs_no_fixed_delay(
    monkeypatch: pytest.MonkeyPatch,
    step_match: Any,
    fake_context: Any,
    stub_driver: Any,
    installed_configuration: dict[str, str],
) -> None:
    """None of the suite's seventeen ``Thread.sleep`` calls is in this class.

    The refusal is installed on the standard library's own sleep, so a delay
    taken anywhere in the call chain - in the step, in the page object or in
    anything either reaches - fails the test rather than merely slowing it.
    """

    def _refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError(
            "the step slept; Session.java contains no Thread.sleep"
        )

    monkeypatch.setattr(time, "sleep", _refuse)

    step_match.run(fake_context)

    assert stub_driver.operations() == EXPECTED_OPERATIONS


def test_step_neither_creates_nor_quits_a_session(
    monkeypatch: pytest.MonkeyPatch,
    step_match: Any,
    fake_context: Any,
    stub_driver: Any,
    installed_configuration: dict[str, str],
) -> None:
    """AAP 0.3.3: *"no step or page ever creates or quits a driver"*.

    ``SessionP.java:10-12`` calls ``Driver.getDriver()`` in its constructor,
    which *creates a session on demand*; the port instead takes the session
    ``features/environment.py`` already opened from the context.  The
    worker-session accessor is replaced with one that refuses, through the
    binding ``app/pages/base_page.py`` documents as the substitution seam, so
    any attempt to reach it is a failure rather than a silent fallback.
    """

    def _refuse_get_driver() -> Any:
        raise AssertionError(
            "the step reached the worker session accessor; the scenario's "
            "session arrives on the context"
        )

    monkeypatch.setattr(base_page, "get_driver", _refuse_get_driver)

    step_match.run(fake_context)

    assert stub_driver.quit_count == 0
    assert {"quit", "maximize_window", "implicitly_wait"}.isdisjoint(
        stub_driver.operations()
    )


def test_step_attaches_nothing_and_stores_nothing_on_the_context(
    step_match: Any,
    fake_context: Any,
    installed_configuration: dict[str, str],
) -> None:
    """The Java records nothing and keeps no state, so neither does the port.

    ``Session.java`` prints nothing - only ``Crm.java`` and ``Sales.java`` do
    - and attaches nothing; screenshots belong to the scenario-failure hook.
    The context's attribute set is compared before and after the call, which
    also demonstrates the module holds no cross-step state.
    """
    before = set(vars(fake_context))

    step_match.run(fake_context)

    assert fake_context.attachments == []
    assert set(vars(fake_context)) == before


def test_repeated_runs_re_resolve_every_element(
    step_match: Any,
    fake_context: Any,
    stub_driver: Any,
    installed_configuration: dict[str, str],
) -> None:
    """The page object caches nothing, as the ``PageFactory`` proxy cached nothing.

    ``SessionP.java:10-12``'s proxied fields resolve on every dereference, so
    a second invocation of the precondition - the normal case, since it runs
    once per scenario - must produce the same three lookups again rather than
    reusing elements from the first.
    """
    step_match.run(fake_context)
    stub_driver.clear_calls()
    step_match.run(fake_context)

    assert tuple(stub_driver.calls) == _expected_calls(
        URL_VALUE, USERNAME_VALUE, PASSWORD_VALUE
    )


# --------------------------------------------------------------------------
# The cross-keyword reach: seven sites, six of them Given
# --------------------------------------------------------------------------


def test_phrase_is_invoked_at_the_seven_measured_feature_sites() -> None:
    """Six ``Given`` sites and one ``When``, parsed out of ``features/*.feature``.

    The census is derived from the files on disk and then compared against the
    sites measured in the pinned reference, so an edit to any feature file
    fails this test instead of quietly invalidating the count that the rest of
    this module's cross-keyword reasoning rests on.
    """
    sites = _invocation_sites()

    assert sites == REFERENCE_SITES
    assert len(sites) == 7
    assert [site for site in sites if site.keyword == "When"] == [
        _site("Session.feature", 4, "When")
    ]
    assert len([site for site in sites if site.keyword == "Given"]) == 6
    assert FEATURE_WITHOUT_THE_PRECONDITION not in {site.feature for site in sites}


def test_every_invocation_site_resolves_to_the_one_definition(
    resolve_step: Callable[[str], Any],
    step_match: Any,
) -> None:
    """Resolution is by text alone, so the keyword at the site cannot matter.

    Each of the seven sites is resolved from the text the feature file
    actually carries, and every one must reach the same function in the same
    bucket - which is the whole of what Cucumber-JVM did and what AAP
    deviation 7 reproduces.
    """
    sites = _invocation_sites()

    assert sites, "no invocation site was found to resolve"

    for site in sites:
        resolved = resolve_step(site.text)

        assert resolved.func is step_match.func, (
            f"{site.feature}:{site.line} ({site.keyword}) does not reach the "
            f"one definition"
        )
        assert resolved.bucket == "step"
        assert resolved.module_name == STEP_MODULE_NAME


def test_every_invocation_site_runs_the_four_operations(
    step_match: Any,
    fake_context: Any,
    stub_driver: Any,
    installed_configuration: dict[str, str],
) -> None:
    """Reachability is not enough: each site must also *do* the four operations.

    The six ``Given`` sites are backgrounds, so this body runs once per
    scenario of six features; running it once per site and asserting the log
    each time is the precondition's guarantee to those features.
    """
    for site in _invocation_sites():
        stub_driver.clear_calls()

        step_match.run(fake_context)

        assert tuple(stub_driver.calls) == _expected_calls(
            URL_VALUE, USERNAME_VALUE, PASSWORD_VALUE
        ), f"{site.feature}:{site.line} did not perform Session.java:14-17"


def test_baseline_artifact_records_the_phrase_as_a_given_against_the_java_method(
    step_match: Any,
) -> None:
    """The JVM's own record of the cross-keyword match, read from the baseline.

    ``target/cucumber.json`` at the pinned revision carries this step with
    ``keyword: "Given "`` while its ``match.location`` names
    ``Session.user_login_to_test_other_features()`` - direct evidence that the
    JVM matched a ``@When`` declaration to a ``Given`` use, which is the
    measurement AAP 0.5.2 rests deviation 7 on.

    The fixture is a **committed** part of the suite's deliverable -- AAP 0.4.1
    lists ``tests/fixtures/golden_cucumber.json`` by name and ``git ls-files``
    tracks it -- so its absence is a defect in the tree rather than a variation
    of the environment, and this fails with that stated rather than skipping.
    Skipping would let the one piece of evidence that comes from the JVM itself
    disappear silently.
    """
    assert GOLDEN_CUCUMBER_PATH.is_file(), (
        f"{GOLDEN_CUCUMBER_PATH} is absent. It is a committed fixture named in "
        f"AAP 0.4.1 and is the baseline this test reads the JVM's own "
        f"cross-keyword record from"
    )

    document = json.loads(GOLDEN_CUCUMBER_PATH.read_text(encoding=SOURCE_ENCODING))
    recorded = [
        step
        for feature in document
        for element in feature.get("elements", ())
        for step in element.get("steps", ())
        if step.get("name") == PHRASE
    ]

    assert recorded, f"the baseline records no step named {PHRASE!r}"

    for step in recorded:
        assert step.get("keyword") == JVM_GIVEN_KEYWORD
        assert step.get("match", {}).get("location") == JAVA_MATCH_LOCATION

    assert step_match.func.__name__ in JAVA_MATCH_LOCATION


# --------------------------------------------------------------------------
# features/Session.feature
# --------------------------------------------------------------------------


def test_session_feature_is_feature_default_with_one_untagged_scenario() -> None:
    """AAP 0.4.1: *"'Feature: Default', one scenario, no tag"*.

    The untagged part is behavioural rather than cosmetic: ``behave.ini``
    defaults to ``@Smoke``, so this feature is unreachable by a positive tag
    expression and reachable by a negative one, and AAP 0.2.2 keeps that as it
    is.  Its one step is the ``When`` declaration site.
    """
    lines = SESSION_FEATURE_PATH.read_text(encoding=SOURCE_ENCODING).splitlines()
    stripped = [line.strip() for line in lines]

    assert stripped[0] == "Feature: Default"
    assert [line for line in stripped if line.startswith("Scenario")] == [
        "Scenario: Users log in to access additional feature"
    ]
    assert [line for line in stripped if line.startswith("@")] == []
    assert [line for line in stripped if STEP_LINE.match(line)] == [
        f"When {PHRASE}"
    ]


def test_session_feature_matches_its_pinned_digest_and_has_no_final_newline() -> None:
    """The file is carried over byte for byte, terminating newline included.

    It has none, and that is not an oversight to tidy: AAP 0.4.1 ports the
    feature files verbatim, and the digest below - which the absence of that
    byte is part of - is the check that no editor silently added one.
    """
    payload = SESSION_FEATURE_PATH.read_bytes()

    assert hashlib.sha256(payload).hexdigest() == SESSION_FEATURE_SHA256
    assert not payload.endswith(b"\n")
    assert payload.endswith(PHRASE.encode(SOURCE_ENCODING))
