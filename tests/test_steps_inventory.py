r"""Behavioural parity tests for ``features/steps/inventory_steps.py``.

The per-module obligation AAP 0.4.1 places on this file: drive the Inventory
step module against the stubbed driver and assert, **for every step method of
``Inventory.java``**, that the port performs the same observable operations in
the same order - the same locators as the paired page object declares, the same
wait target and timeout, the same hard-coded literals, and the same no-ops
where the Java body computes a value and throws it away.  A step method with no
corresponding assertion here is a gap, so the nine-row census below is compared
as a *set* against the registry: an omitted method turns this module red rather
than passing silently.

Java authority
--------------
``src/main/java/com/testinium/step_definitions/Inventory.java`` and
``src/main/java/com/testinium/pages/InventoryP.java`` at pinned revision
``47e9d697e4a9a85da889f94a846fdf47af28a240``.  Every expectation in this file is
cross-referenced to a line of those two files and to nothing else - in
particular, never to the Python implementation, which is the thing under test.

::

    :12  InventoryP inventory = new InventoryP();
    :13  WebDriverWait wait = new WebDriverWait(Driver.getDriver(), 20);
    :15  "Logged user clicks on Inventory Module"  inventoryModule.click()
    :20  "User clicks on Product module"           wait 20s visibilityOf, click
    :26  "User see the products"                   getTitle().equals(...) dropped
    :31  "User clicks create button"                createBtn.click()
    :36  "User clicks the save button"              wait 20s visibilityOf, click
    :42  "User should see the error"                fieldError.isDisplayed() dropped
    :47  "User enters Product Name"                 productName.sendKeys("IBM")
    :52  "User should see the title includes ..."    productsList.isDisplayed() dropped
    :57  "User sees the created Product"            createdProduct.isDisplayed() dropped

What makes this class different from every other one in the suite
-----------------------------------------------------------------
``Inventory.java`` contains **no assertion at all** - nine methods, not one
``Assert`` call - and **four** of its statements are boolean-returning calls
whose results are discarded: the title comparison at ``:28`` and the three
``isDisplayed()`` reads at ``:44``, ``:54`` and ``:59``.  Those four are the
headline obligation of this module and are pinned from both directions:

*structurally*
    :func:`test_no_step_body_contains_an_assert_statement` and
    :func:`test_discarded_result_is_a_bare_expression_statement` parse the
    module with :mod:`ast` and prove that no ``assert`` exists in any of the
    nine bodies and that each discarded call really is a bare expression
    statement - not assigned, not asserted, not branched on;

*behaviourally*
    :func:`test_discarded_result_does_not_fail_the_step` programmes each of the
    four calls to answer ``False`` (or, for the title, to report a different
    page) and asserts the step still completes and still returns ``None`` -
    while the call itself is shown in the ordered log, so "no assertion" is
    never confused with "no operation".

It also has **zero fixed delays** (the seventeen ``Thread.sleep`` calls of this
suite belong to Calendar, Contacts, Crm, EmployeeStage and Notes), reads **no
configuration key**, uses neither ``Keys`` nor ``Actions``, and never
navigates: ``Driver.getDriver()`` appears once, at ``:28``, only to read a
title.

Four techniques, and why each is the one used
---------------------------------------------
**The wait is intercepted in the step module's own globals.**  behave's
``load_step_modules`` execs each step file with a private globals dict, so the
name a step body reaches the wait helper through lives there rather than in
``app.automation``.  :func:`_install_wait_recorder` discovers that name
dynamically - any callable module global whose name begins with ``wait`` - and
replaces it with a recorder through ``monkeypatch.setitem``.  This is mandatory
twice over: without it ``app/automation/waits.py``'s ``_until`` would call
``get_driver()`` and this suite would try to start a browser, and the wait call
sites themselves are being changed by another work unit.  So what is asserted
here is the wait's **target locator**, its **timeout value** and its **position
relative to the click** - never the helper's name, arity or argument shape.  The
recorder accepts either target shape: an element carrying ``.locator`` or a raw
``(by, value)`` pair, and reads the timeout as "the numeric argument", whether
positional or keyword.

**The wait lands in the driver's single ordered log.**  The recorder appends a
synthetic :data:`WAIT_OP` entry to ``stub_driver.calls``, so "waited, then
clicked" is one sequence assertion rather than two assertions and a comparison
of indices.  :func:`_ordered_calls` then removes the one difference between the
two possible call shapes - an element-shaped wait resolves its target with a
lookup of its own, a locator-shaped one does not - and removes nothing else.
The click's *own* lookup is never removed, because re-resolving the element for
the click is itself parity: the Java field is a ``PageFactory`` proxy that
re-resolves on every use (``InventoryP.java:10-12``).

**Locator expectations close the chain Java -> constant -> operation inside this
file.**  :data:`JAVA_FIND_BY` restates all eight ``@FindBy`` annotations of
``InventoryP.java:14-36``, including the server-generated id and the two
exact-``@class`` XPaths; :func:`test_locator_constant_matches_java_findby`
compares each against its :class:`~app.pages.inventory_page.InventoryPage`
constant, and every per-step test asserts the operation reached the driver with
that same constant.

**Operation order is the parity.**  Because the class asserts nothing, the
ordered log *is* the contract: each of the nine tests states the complete
sequence of lookups, clicks, reads and waits its step produced, so an added,
removed or reordered operation fails.  Nothing here sleeps, touches the network
or creates a browser.

Fixtures come from ``tests/conftest.py`` and nothing is added to it: this module
uses :fixture:`stub_driver`, :fixture:`fake_context`, :fixture:`resolve_step`
and :fixture:`step_registry` as they are defined there.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Final, NamedTuple

import pytest

from app.automation import By
from app.pages import InventoryPage

# --------------------------------------------------------------------------- #
# Locations
#
# Computed from this file's own position rather than taken from a fixture, so
# that the module-level census constants below can be built at import time and
# used as parametrize arguments.
# --------------------------------------------------------------------------- #

#: Repository root: ``tests/`` -> the checkout root.
REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]

#: The step module under test.
STEP_MODULE_PATH: Final[Path] = REPO_ROOT / "features" / "steps" / "inventory_steps.py"

#: The feature the nine definitions serve.
FEATURE_PATH: Final[Path] = REPO_ROOT / "features" / "Inventory.feature"

#: Name the registry reports for a definition owned by that module, which is
#: how :attr:`StepMatch.module_name` and the matcher locations are filtered.
STEP_MODULE_NAME: Final[str] = "inventory_steps"

#: The step modules that own the two phrases ``Inventory.feature`` uses but
#: ``Inventory.java`` does not declare.  Both must resolve - the feature cannot
#: run otherwise - and neither may be redeclared here, which would make the
#: phrase ambiguous.
FOREIGN_PHRASE_OWNERS: Final[tuple[tuple[str, str], ...]] = (
    ("User login to test other features", "session_steps"),
    ("User should see the dashboard", "login_steps"),
)

# --------------------------------------------------------------------------- #
# Fixed values of the Java class
# --------------------------------------------------------------------------- #

#: ``new WebDriverWait(Driver.getDriver(), 20)`` - ``Inventory.java:13``.  One
#: timeout for the whole class, used at both of its two wait call sites.
WAIT_TIMEOUT: Final[int] = 20

#: ``inventory.productName.sendKeys("IBM")`` - ``Inventory.java:49``.  The only
#: text this module types, and it is typed exactly once.
PRODUCT_NAME_VALUE: Final[str] = "IBM"

#: ``Driver.getDriver().getTitle().equals("Products - Odoo")`` -
#: ``Inventory.java:28``.  Carried byte-exact; the *comparison* is contractual
#: even though its result is not.
PRODUCTS_TITLE: Final[str] = "Products - Odoo"

#: A page title that is **not** :data:`PRODUCTS_TITLE`, used to drive the
#: discarded comparison of ``:28`` to ``False``.
OTHER_TITLE: Final[str] = "Sales - Odoo"

# --------------------------------------------------------------------------- #
# Log vocabulary
#
# The operation names ``tests/conftest.py``'s recorder writes, restated here as
# constants so that a sequence assertion reads as a sequence rather than as a
# tuple of string literals.  ``WAIT_OP`` is this file's own synthetic entry.
# --------------------------------------------------------------------------- #

#: A singular element lookup: ``("find_element", (by, value))``.
FIND: Final[str] = "find_element"

#: A plural lookup.  Named only so that its **absence** can be asserted:
#: ``InventoryP.java`` declares no ``List<WebElement>`` field.
FIND_ALL: Final[str] = "find_elements"

#: ``WebElement.click()``, logged with its originating locator first.
CLICK: Final[str] = "element.click"

#: ``WebElement.sendKeys(...)``, logged with locator then arguments.
SEND_KEYS: Final[str] = "element.send_keys"

#: ``WebElement.isDisplayed()``, logged with its originating locator.
IS_DISPLAYED: Final[str] = "element.is_displayed"

#: ``WebDriver.getTitle()``, logged with no arguments.
TITLE: Final[str] = "title"

#: This file's synthetic wait entry, appended by :class:`WaitRecorder` as
#: ``(WAIT_OP, (locator, timeout))``.  Deliberately carries neither the helper's
#: name nor its argument shape - see the module docstring.
WAIT_OP: Final[str] = "wait"

#: Driver operations no Inventory step may perform.  ``get`` heads the list
#: because the whole class navigates nowhere: the Background logs in and every
#: scenario proceeds by clicking.
FORBIDDEN_DRIVER_OPS: Final[tuple[str, ...]] = (
    "get",
    "back",
    "current_url",
    "execute_script",
    "execute",
    "quit",
    "maximize_window",
    "implicitly_wait",
    "get_screenshot_as_png",
    FIND_ALL,
)

#: Prefix by which a wait binding is recognised in the step module's globals.
WAIT_PREFIX: Final[str] = "wait"

#: The four registry buckets behave keeps.  Only ``step`` may be populated by
#: this port (AAP deviation 7: every definition registers with ``@step`` so that
#: matching cannot depend on the invoking keyword); the other three are scanned
#: so that a keyword-bound registration is *found and reported* rather than
#: missed.
STEP_BUCKETS: Final[tuple[str, ...]] = ("step", "given", "when", "then")

#: Gherkin step keywords, used to extract the feature file's phrases.
GHERKIN_KEYWORDS: Final[tuple[str, ...]] = ("Given ", "When ", "Then ", "And ", "But ")


# --------------------------------------------------------------------------- #
# The census: one row per method of Inventory.java
# --------------------------------------------------------------------------- #


class StepSpec(NamedTuple):
    """One method of ``Inventory.java``, as the registry must expose it."""

    #: The text inside the Java annotation, which is the ``@step`` pattern and,
    #: these definitions carrying no parameters, also the Gherkin phrase.
    phrase: str

    #: The port's function name, which is the Java method name unchanged.
    function: str

    #: The method's line range in ``Inventory.java``.
    java_lines: str


#: All nine methods, in ``Inventory.java`` source order.  This tuple is the
#: gap detector AAP 0.4.1 requires: it is compared as a set against the
#: definitions the registry attributes to ``inventory_steps``, so a method
#: dropped from either side fails.
STEP_TABLE: Final[tuple[StepSpec, ...]] = (
    StepSpec(
        "Logged user clicks on Inventory Module",
        "logged_user_clicks_on_inventory_module",
        "Inventory.java:15-18",
    ),
    StepSpec(
        "User clicks on Product module",
        "user_clicks_on_product_module",
        "Inventory.java:20-24",
    ),
    StepSpec(
        "User see the products",
        "user_see_the_products",
        "Inventory.java:26-29",
    ),
    StepSpec(
        "User clicks create button",
        "user_clicks_create_button",
        "Inventory.java:31-34",
    ),
    StepSpec(
        "User clicks the save button",
        "user_clicks_the_save_button",
        "Inventory.java:36-40",
    ),
    StepSpec(
        "User should see the error",
        "user_should_see_the_error",
        "Inventory.java:42-45",
    ),
    StepSpec(
        "User enters Product Name",
        "user_enters_product_name",
        "Inventory.java:47-50",
    ),
    StepSpec(
        "User should see the title includes the Product Name",
        "user_should_see_the_title_includes_the_product_name",
        "Inventory.java:52-55",
    ),
    StepSpec(
        "User sees the created Product",
        "user_sees_the_created_product",
        "Inventory.java:57-60",
    ),
)

#: Every phrase of the census, for the parametrized resolution tests.
ALL_PHRASES: Final[tuple[str, ...]] = tuple(spec.phrase for spec in STEP_TABLE)

#: The two phrases that wait before acting, and nothing else does
#: (``Inventory.java:22`` and ``:38``).
WAITING_PHRASES: Final[tuple[str, ...]] = (
    "User clicks on Product module",
    "User clicks the save button",
)


class LocatorSpec(NamedTuple):
    """One ``@FindBy`` field of ``InventoryP.java``."""

    #: The constant on :class:`~app.pages.inventory_page.InventoryPage`.
    constant: str

    #: The strategy the annotation selects.
    strategy: str

    #: The selector, carried character-for-character from the annotation.
    selector: str

    #: The field's line range in ``InventoryP.java``.
    java_lines: str


#: All eight ``@FindBy`` annotations of ``InventoryP.java:14-36``, restated from
#: the Java rather than read back from the page object.  The awkward ones are
#: here in full deliberately: the server-generated id ``o_field_input_479``, the
#: two exact-``@class`` XPaths, the hyphenated ``o-kanban-button-new`` beside the
#: underscored ``o_notification_manager``, and ``//span[.='EY']`` - test data
#: baked into a selector, which does not match the ``IBM`` the form step types.
#: AAP 0.8 preserves every one of them, so this table pins them rather than
#: tidying them.
JAVA_FIND_BY: Final[tuple[LocatorSpec, ...]] = (
    LocatorSpec(
        "INVENTORY_MODULE", By.PARTIAL_LINK_TEXT, "Inventory", "InventoryP.java:14-15"
    ),
    LocatorSpec("PRODUCTS", By.PARTIAL_LINK_TEXT, "Products", "InventoryP.java:17-18"),
    LocatorSpec(
        "CREATE_BTN", By.CLASS_NAME, "o-kanban-button-new", "InventoryP.java:20-21"
    ),
    LocatorSpec(
        "SAVE_BTN",
        By.XPATH,
        "//button[@class='btn btn-primary btn-sm o_form_button_save']",
        "InventoryP.java:23-24",
    ),
    LocatorSpec(
        "FIELD_ERROR", By.CLASS_NAME, "o_notification_manager", "InventoryP.java:26-27"
    ),
    LocatorSpec("PRODUCT_NAME", By.ID, "o_field_input_479", "InventoryP.java:29-30"),
    LocatorSpec("PRODUCTS_LIST", By.XPATH, "//span[.='EY']", "InventoryP.java:32-33"),
    LocatorSpec(
        "CREATED_PRODUCT",
        By.XPATH,
        "//span[@class='o_field_char o_field_widget o_required_modifier']",
        "InventoryP.java:35-36",
    ),
)

#: Convenience aliases, so a sequence assertion names the element it acts on.
INVENTORY_MODULE: Final[tuple[str, str]] = InventoryPage.INVENTORY_MODULE
PRODUCTS: Final[tuple[str, str]] = InventoryPage.PRODUCTS
CREATE_BTN: Final[tuple[str, str]] = InventoryPage.CREATE_BTN
SAVE_BTN: Final[tuple[str, str]] = InventoryPage.SAVE_BTN
FIELD_ERROR: Final[tuple[str, str]] = InventoryPage.FIELD_ERROR
PRODUCT_NAME: Final[tuple[str, str]] = InventoryPage.PRODUCT_NAME
PRODUCTS_LIST: Final[tuple[str, str]] = InventoryPage.PRODUCTS_LIST
CREATED_PRODUCT: Final[tuple[str, str]] = InventoryPage.CREATED_PRODUCT


class DiscardedSpec(NamedTuple):
    """One boolean-returning call whose result ``Inventory.java`` throws away."""

    #: The phrase whose body performs it.
    phrase: str

    #: The statement's line in ``Inventory.java``.
    java_line: str

    #: The element whose ``isDisplayed()`` is read, or ``None`` for the title
    #: comparison of ``:28``, which reads the driver rather than an element.
    locator: tuple[str, str] | None

    #: The log entry the call must still produce even when its answer is the
    #: one that would fail an assertion, had the Java made one.
    operation: str


#: The four discarded results.  ``Inventory.java`` computes each of these and
#: drops it on the floor: the class has no ``Assert`` call anywhere, so none of
#: the four can fail a scenario however the page answers.
DISCARDED: Final[tuple[DiscardedSpec, ...]] = (
    DiscardedSpec("User see the products", "Inventory.java:28", None, TITLE),
    DiscardedSpec(
        "User should see the error", "Inventory.java:44", FIELD_ERROR, IS_DISPLAYED
    ),
    DiscardedSpec(
        "User should see the title includes the Product Name",
        "Inventory.java:54",
        PRODUCTS_LIST,
        IS_DISPLAYED,
    ),
    DiscardedSpec(
        "User sees the created Product",
        "Inventory.java:59",
        CREATED_PRODUCT,
        IS_DISPLAYED,
    ),
)


# --------------------------------------------------------------------------- #
# Source inspection
#
# The module is parsed once, at import, and every structural test reads that
# one tree.  Parsing rather than grepping is what keeps a prose mention in a
# docstring - and this module's docstrings mention sleeps, assertions and
# configuration in order to explain their absence - from being mistaken for
# code.
# --------------------------------------------------------------------------- #

#: The step module's source text.
MODULE_SOURCE: Final[str] = STEP_MODULE_PATH.read_text(encoding="utf-8")

#: Its parsed form.
MODULE_TREE: Final[ast.Module] = ast.parse(
    MODULE_SOURCE, filename=str(STEP_MODULE_PATH)
)


def _step_definitions() -> dict[str, ast.FunctionDef]:
    """The module's ``@step``-decorated functions, keyed by phrase.

    :returns: One entry per definition, mapping the pattern text inside the
        decorator to the function that carries it.
    :raises AssertionError: If a decorator is not a call of a bare name, or its
        first argument is not a string literal - either would mean the pattern
        is computed rather than declared, which no definition in this port does
        and which would make the census unreadable.
    """
    found: dict[str, ast.FunctionDef] = {}

    for node in MODULE_TREE.body:
        if not isinstance(node, ast.FunctionDef):
            continue

        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                raise AssertionError(
                    f"{node.name} carries a non-call decorator "
                    f"{ast.unparse(decorator)!r}; every definition in this port "
                    f"is registered as @step(\"<phrase>\")"
                )

            if not isinstance(decorator.func, ast.Name):
                raise AssertionError(
                    f"{node.name} is registered through "
                    f"{ast.unparse(decorator.func)!r} rather than a bare name"
                )

            if not decorator.args or not isinstance(decorator.args[0], ast.Constant):
                raise AssertionError(
                    f"{node.name}'s decorator does not carry a literal pattern"
                )

            found[str(decorator.args[0].value)] = node

    return found


#: The nine definitions as declared in source, keyed by phrase.
SOURCE_DEFINITIONS: Final[dict[str, ast.FunctionDef]] = _step_definitions()


def _decorator_names(function: ast.FunctionDef) -> tuple[str, ...]:
    """The names a definition is registered through.

    :param function: A parsed definition.
    :returns: One name per decorator, such as ``("step",)``.
    """
    return tuple(
        decorator.func.id
        for decorator in function.decorator_list
        if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Name)
    )


def _body_statements(function: ast.FunctionDef) -> list[ast.stmt]:
    """A definition's statements with its docstring removed.

    :param function: A parsed definition.
    :returns: The executable statements, in source order.
    """
    statements = list(function.body)

    if (
        statements
        and isinstance(statements[0], ast.Expr)
        and isinstance(statements[0].value, ast.Constant)
        and isinstance(statements[0].value.value, str)
    ):
        return statements[1:]

    return statements


def _imports() -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Every module the step module imports, with the names taken from it.

    ``__future__`` is excluded: it is a compiler directive rather than a
    dependency, so it says nothing about the import boundary this module's tests
    police.

    :returns: One ``(module, names)`` pair per import statement, in source
        order.  A plain ``import x`` contributes ``("x", ())``.
    """
    collected: list[tuple[str, tuple[str, ...]]] = []

    for node in ast.walk(MODULE_TREE):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""

            if module == "__future__":
                continue

            collected.append((module, tuple(alias.name for alias in node.names)))
        elif isinstance(node, ast.Import):
            collected.extend((alias.name, ()) for alias in node.names)

    return tuple(collected)


def _called_names() -> tuple[str, ...]:
    """Every callee the module invokes, rendered as source.

    :returns: One rendering per call site, such as ``"wait_visible_element"`` or
        ``"page.products.click"``.
    """
    return tuple(
        ast.unparse(node.func)
        for node in ast.walk(MODULE_TREE)
        if isinstance(node, ast.Call)
    )


def _wait_call_sites() -> tuple[tuple[str, str, tuple[Any, ...]], ...]:
    """Every wait call the nine step bodies make, in source order.

    A wait call is recognised by the root name of its callee beginning with
    :data:`WAIT_PREFIX` - the same rule :func:`_install_wait_recorder` applies to
    the module globals, so the two cannot drift apart.  The helper's identity is
    deliberately not part of the rule: another work unit is changing these call
    sites, and this module asserts the target, the timeout and the order rather
    than the helper.

    :returns: One ``(function name, callee, numeric arguments)`` triple per wait
        call site.
    :raises AssertionError: If a wait is reached through an attribute rather
        than a plain name.  AAP 0.4.2 has step modules import the helpers by
        name from ``app.automation``, and the globals-level interception this
        module depends on cannot reach a helper held behind a module object, so
        the unsupported shape is reported explicitly instead of producing a
        confusing failure elsewhere.
    """
    sites: list[tuple[str, str, tuple[Any, ...]]] = []

    for function in SOURCE_DEFINITIONS.values():
        for node in ast.walk(function):
            if not isinstance(node, ast.Call):
                continue

            callee = ast.unparse(node.func)

            if not callee.split(".")[0].startswith(WAIT_PREFIX):
                continue

            if not isinstance(node.func, ast.Name):
                raise AssertionError(
                    f"{function.name} reaches its wait through {callee!r}; this "
                    f"module can only intercept a helper bound as a plain name "
                    f"in the step module's globals"
                )

            values: list[ast.expr] = list(node.args)
            values.extend(keyword.value for keyword in node.keywords)
            numbers = tuple(
                value.value
                for value in values
                if isinstance(value, ast.Constant)
                and isinstance(value.value, (int, float))
                and not isinstance(value.value, bool)
            )
            sites.append((function.name, callee, numbers))

    return tuple(sites)


# --------------------------------------------------------------------------- #
# The wait recorder
# --------------------------------------------------------------------------- #


class WaitRecord(NamedTuple):
    """One intercepted wait call."""

    #: The globals name it was reached through, kept for failure messages only -
    #: no assertion in this module reads it.
    binding: str

    #: The target's locator, whichever shape the target arrived in.
    locator: tuple[str, str]

    #: The numeric argument, positional or keyword; ``None`` when the call
    #: carried no single unambiguous number, which a test then reports.
    timeout: Any

    #: The positional arguments after the target, verbatim.
    args: tuple[Any, ...]

    #: The keyword arguments, verbatim.
    kwargs: dict[str, Any]


def _target_locator(target: Any) -> tuple[str, str]:
    """The ``(by, value)`` pair a wait was asked to wait on.

    Accepts both shapes a call site may use, because which one it uses is not
    this module's business: an element already resolved - the stub element of
    ``tests/conftest.py``, which carries ``.locator`` - or a raw locator pair.

    :param target: The wait's first argument.
    :returns: The locator as a two-tuple.
    :raises AssertionError: If the target is neither shape, which would mean the
        call site waits on something this module cannot attribute to a locator.
    """
    locator = getattr(target, "locator", None)

    if locator is not None:
        return tuple(locator)  # type: ignore[return-value]

    if (
        isinstance(target, tuple)
        and len(target) == 2
        and all(isinstance(part, str) for part in target)
    ):
        return (target[0], target[1])

    raise AssertionError(
        f"wait target {target!r} carries no locator and is not a (by, value) "
        f"pair, so the wait's target cannot be asserted"
    )


class WaitRecorder:
    """Stands in for the wait helper, recording target, timeout and position.

    Installed into the *step module's* globals, which is where a step body looks
    the helper up.  Two effects, and they are the whole of it:

    * every call is appended to :attr:`records`, for the timeout and target
      assertions;
    * every call is appended to the driver's single ordered log as
      ``(WAIT_OP, (locator, timeout))``, so "waited, then clicked" is one
      sequence assertion.

    The target is returned unchanged.  Both Inventory call sites discard the
    result, exactly as ``Inventory.java:22`` and ``:38`` discard
    ``wait.until(...)``'s, so nothing depends on the value; a call site that
    began using it would fail here rather than silently pass.
    """

    __slots__ = ("_binding", "_driver", "records")

    def __init__(self, binding: str, driver: Any) -> None:
        """Bind the recorder to one globals name and one driver.

        :param binding: The globals name being replaced, kept for diagnostics.
        :param driver: The recorder whose ordered log the wait is appended to.
        """
        self._binding = binding
        self._driver = driver

        #: Every intercepted call, in order.
        self.records: list[WaitRecord] = []

    def __call__(self, target: Any, *args: Any, **kwargs: Any) -> Any:
        """Record one wait and return its target.

        :param target: The element or locator being waited on.
        :param args: Remaining positional arguments; the timeout is the numeric
            one.
        :param kwargs: Keyword arguments; a numeric one is equally accepted as
            the timeout.
        :returns: *target*, unchanged.
        """
        numbers = [
            value
            for value in (*args, *kwargs.values())
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        ]
        timeout = numbers[0] if len(numbers) == 1 else None
        locator = _target_locator(target)

        self.records.append(
            WaitRecord(
                binding=self._binding,
                locator=locator,
                timeout=timeout,
                args=args,
                kwargs=dict(kwargs),
            )
        )
        self._driver.calls.append((WAIT_OP, (locator, timeout)))
        return target

    def __repr__(self) -> str:
        """Summarise what has been recorded, which is what a failure needs.

        :returns: A short representation.
        """
        return f"<WaitRecorder {self._binding!r} calls={len(self.records)}>"


def _wait_bindings(step_globals: dict[str, Any]) -> tuple[str, ...]:
    """The globals names through which the step module can reach a wait.

    Discovered rather than hard-coded, so that a call site renamed from one
    helper of ``app/automation/waits.py`` to another is intercepted just the
    same.  behave injects ``step``, ``given``, ``when``, ``then`` and the matcher
    helpers into these globals and none of them begins with :data:`WAIT_PREFIX`.

    :param step_globals: The step module's globals dict.
    :returns: The matching names, sorted for a stable failure message.
    """
    return tuple(
        sorted(
            name
            for name, value in step_globals.items()
            if name.startswith(WAIT_PREFIX) and callable(value)
        )
    )


def _install_wait_recorder(
    step_globals: dict[str, Any], driver: Any, monkeypatch: pytest.MonkeyPatch
) -> WaitRecorder:
    """Replace every wait binding in *step_globals* with one recorder.

    ``monkeypatch.setitem`` is what makes this safe in a session that loads the
    step modules exactly once: the real bindings are restored when the test ends,
    so no interception leaks into the next test or into another parity module.

    :param step_globals: The step module's globals dict, reached through a
        resolved step function's ``__globals__``.
    :param driver: The recorder whose ordered log waits are appended to.
    :param monkeypatch: pytest's patcher.
    :returns: The installed recorder.
    :raises AssertionError: If the module exposes no wait binding at all while
        its source still contains a wait call site - the one combination that
        would let a wait reach the real helper, and through it ``get_driver()``
        and a real browser.
    """
    bindings = _wait_bindings(step_globals)

    if not bindings and _wait_call_sites():
        raise AssertionError(
            f"{STEP_MODULE_NAME} makes wait calls but exposes no "
            f"{WAIT_PREFIX}* binding in its globals, so the wait cannot be "
            f"intercepted and would reach the real driver"
        )

    recorder = WaitRecorder(binding=", ".join(bindings), driver=driver)

    for name in bindings:
        monkeypatch.setitem(step_globals, name, recorder)

    return recorder


# --------------------------------------------------------------------------- #
# Driving a step
# --------------------------------------------------------------------------- #


def _ordered_calls(driver: Any) -> tuple[tuple[str, tuple[Any, ...]], ...]:
    """The driver's ordered log, with the wait's own target lookup removed.

    The single normalization this module performs, and it exists for one
    measured reason: an element-shaped wait call has its target resolved by a
    lookup immediately before the wait, and a locator-shaped one does not, so
    keeping that lookup would pin the helper's argument shape - which is being
    changed by another work unit - rather than the behaviour.  Everything else
    is left exactly as it was recorded, including the click's *own* lookup:
    re-resolving the element for the click is parity with the ``PageFactory``
    proxy of ``InventoryP.java:10-12``, not an artefact of the wait.

    :param driver: The :fixture:`stub_driver`.
    :returns: The normalized ``(operation, args)`` sequence.
    """
    calls = list(driver.calls)
    kept: list[tuple[str, tuple[Any, ...]]] = []

    for index, entry in enumerate(calls):
        operation, args = entry
        following = calls[index + 1] if index + 1 < len(calls) else None

        if (
            operation == FIND
            and following is not None
            and following[0] == WAIT_OP
            and following[1][0] == args
        ):
            continue

        kept.append((operation, args))

    return tuple(kept)


def _operations(calls: tuple[tuple[str, tuple[Any, ...]], ...]) -> tuple[str, ...]:
    """The operation names of a normalized log, arguments dropped.

    :param calls: A normalized log, as :func:`_ordered_calls` returns.
    :returns: The operation names in order.
    """
    return tuple(operation for operation, _ in calls)


def _drive(
    phrase: str,
    resolve_step: Any,
    context: Any,
    driver: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Any, WaitRecorder, Any]:
    """Resolve *phrase*, intercept its waits, and run its body once.

    The entry point every behavioural test uses, so that "resolve, intercept,
    run" happens identically in all of them.

    :param phrase: The Gherkin phrase to resolve.
    :param resolve_step: The :fixture:`resolve_step` fixture.
    :param context: The :fixture:`fake_context` fixture.
    :param driver: The :fixture:`stub_driver` fixture, which is that context's
        session.
    :param monkeypatch: pytest's patcher.
    :returns: The resolved match, the installed recorder, and the step's return
        value - ``None`` for every step in this port.
    """
    match = resolve_step(phrase)
    recorder = _install_wait_recorder(match.func.__globals__, driver, monkeypatch)
    return match, recorder, match.run(context)


# =========================================================================== #
# The registry census - nine methods, nine definitions, no more and no fewer
# =========================================================================== #


@pytest.mark.parametrize("spec", STEP_TABLE, ids=lambda spec: spec.function)
def test_every_java_method_resolves_through_the_registry(
    spec: StepSpec, resolve_step: Any
) -> None:
    """Each of the nine methods of ``Inventory.java:15-60`` has one definition.

    Resolved through behave's own registry, so what is asserted is what the
    engine would match at scenario runtime: the phrase resolves to exactly one
    implementation (``resolve_step`` fails on none and on more than one), that
    implementation is owned by ``inventory_steps``, its pattern is the Java
    annotation text unchanged, and its function name is the Java method name
    unchanged.  These definitions declare no parameters, so no argument is
    extracted.
    """
    match = resolve_step(spec.phrase)

    assert match.module_name == STEP_MODULE_NAME, (
        f"{spec.phrase!r} ({spec.java_lines}) is owned by {match.module_name!r}"
    )
    assert match.func.__name__ == spec.function
    assert match.pattern == spec.phrase
    assert match.args == ()
    assert match.kwargs == {}


def test_module_registers_exactly_nine_definitions(step_registry: Any) -> None:
    """``Inventory.java`` declares nine step methods and no tenth.

    The count is taken from the registry rather than from the source, so an
    extra definition - a phrase this module has no business owning, or a
    resurrected tenth method - is caught as the engine would see it.  The class
    ends at ``Inventory.java:60`` with a closing brace and two blank lines: there
    is no commented-out definition to revive, unlike ``Contacts.java``.
    """
    owned = [
        matcher
        for bucket in STEP_BUCKETS
        for matcher in step_registry.steps.get(bucket, ())
        if Path(str(getattr(matcher.location, "filename", "") or "")).stem
        == STEP_MODULE_NAME
    ]

    assert len(owned) == len(STEP_TABLE) == 9, (
        f"{STEP_MODULE_NAME} registers {len(owned)} definitions; "
        f"Inventory.java declares nine: "
        f"{sorted(matcher.pattern for matcher in owned)}"
    )


def test_covered_phrase_set_equals_the_java_method_census(step_registry: Any) -> None:
    """The nine registered phrases are exactly the nine of ``Inventory.java``.

    AAP 0.4.1's gap detector: "a step method with no corresponding assertion in
    its module's test is a gap, and the module test enumerates the Java class's
    methods so an omission fails rather than passes silently".  Comparing the
    two sets is what makes that true in both directions - a method dropped from
    :data:`STEP_TABLE` fails here, and so does a definition that exists in the
    module but is pinned by no test in this file.
    """
    registered = {
        matcher.pattern
        for bucket in STEP_BUCKETS
        for matcher in step_registry.steps.get(bucket, ())
        if Path(str(getattr(matcher.location, "filename", "") or "")).stem
        == STEP_MODULE_NAME
    }

    assert registered == set(ALL_PHRASES), (
        f"unpinned definitions: {sorted(registered - set(ALL_PHRASES))}; "
        f"missing definitions: {sorted(set(ALL_PHRASES) - registered)}"
    )


def test_every_definition_registers_in_the_step_bucket(step_registry: Any) -> None:
    """All nine register with ``@step``, none with a keyword-bound decorator.

    ``Inventory.java`` mixes ``@When`` (``:15``, ``:20``, ``:26``, ``:31``,
    ``:36``, ``:47``) and ``@Then`` (``:42``, ``:52``, ``:57``), and
    Cucumber-JVM matches on text alone, so the keyword carries no meaning.  AAP
    deviation 7 reproduces that with ``@step`` everywhere; a definition landing
    in behave's ``given``/``when``/``then`` buckets would make resolution
    keyword-dependent and is reported here rather than tolerated.
    """
    per_bucket = {
        bucket: [
            matcher.pattern
            for matcher in step_registry.steps.get(bucket, ())
            if Path(str(getattr(matcher.location, "filename", "") or "")).stem
            == STEP_MODULE_NAME
        ]
        for bucket in STEP_BUCKETS
    }

    assert set(per_bucket["step"]) == set(ALL_PHRASES)
    assert per_bucket["given"] == [], f"keyword-bound: {per_bucket['given']}"
    assert per_bucket["when"] == [], f"keyword-bound: {per_bucket['when']}"
    assert per_bucket["then"] == [], f"keyword-bound: {per_bucket['then']}"


def test_all_nine_definitions_share_one_module(resolve_step: Any) -> None:
    """The nine methods of ``Inventory.java`` port to one module, not several.

    Asserted through the globals dict behave gives each step file: all nine
    resolved functions share one, which is also the dict the wait interception
    of this module patches.  One shared globals dict is what makes a single
    ``monkeypatch.setitem`` cover every wait call site in the class.
    """
    globals_ids = {id(resolve_step(phrase).func.__globals__) for phrase in ALL_PHRASES}

    assert len(globals_ids) == 1


@pytest.mark.parametrize(
    ("phrase", "owner"), FOREIGN_PHRASE_OWNERS, ids=lambda value: str(value)
)
def test_foreign_phrase_resolves_elsewhere(
    phrase: str, owner: str, resolve_step: Any
) -> None:
    """``Inventory.feature``'s two borrowed phrases belong to other modules.

    ``Inventory.feature:9``'s Background precondition is ``Session.java:12-17``
    and ``Inventory.feature:16``'s ``User should see the dashboard`` is
    ``LoginSD.java``; neither appears in ``Inventory.java``.  Both must resolve -
    the feature cannot run otherwise - and neither may be redeclared by
    ``inventory_steps``, which would make the phrase ambiguous under ``@step``.
    """
    match = resolve_step(phrase)

    assert match.module_name == owner
    assert match.module_name != STEP_MODULE_NAME


# =========================================================================== #
# Locators - Java @FindBy -> page constant -> the operation the step performs
# =========================================================================== #


@pytest.mark.parametrize("spec", JAVA_FIND_BY, ids=lambda spec: spec.constant)
def test_locator_constant_matches_java_findby(spec: LocatorSpec) -> None:
    """Each constant carries its ``@FindBy`` strategy and selector verbatim.

    All eight annotations of ``InventoryP.java:14-36``, restated from the Java
    in :data:`JAVA_FIND_BY` and compared here, so the chain from annotation to
    constant is closed inside this file: the per-step tests below then assert
    that the same constant is what reaches the driver.  The awkward ones are
    included rather than excused - the server-generated id ``o_field_input_479``
    (``:29``), both exact-``@class`` XPaths (``:23``, ``:35``), the hyphenated
    ``o-kanban-button-new`` beside the underscored ``o_notification_manager``,
    and ``//span[.='EY']`` (``:32``), whose baked-in test data does not match the
    ``IBM`` that ``Inventory.java:49`` types.  AAP 0.8 preserves every one.
    """
    constant = getattr(InventoryPage, spec.constant)

    assert constant == (spec.strategy, spec.selector), (
        f"{spec.constant} does not match {spec.java_lines}"
    )


def test_the_eight_locators_are_the_whole_inventory_in_java_order() -> None:
    """``InventoryP.java:14-36`` declares eight fields and the page declares eight.

    The row-by-row test above proves every Java annotation arrived; this proves
    nothing else did.  ``BasePage.__init_subclass__`` builds ``LOCATORS`` from
    the class body in source order, so a single equality pins the count, the
    names, the order and the values together - a ninth constant, a dropped one,
    a renamed one or a reordered pair each fail here, and none of them fail the
    row-by-row test.
    """
    assert tuple(InventoryPage.LOCATORS.items()) == tuple(
        (spec.constant, (spec.strategy, spec.selector)) for spec in JAVA_FIND_BY
    )


# =========================================================================== #
# The nine bodies, one test each - the ordered log IS the parity
# =========================================================================== #


def test_clicks_inventory_module(
    resolve_step: Any,
    fake_context: Any,
    stub_driver: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``Inventory.java:15-18``: one click on the Inventory menu, no wait.

    The body is the single statement ``inventory.inventoryModule.click()`` at
    ``:17``.  Deliberately no explicit wait in front of it - the session's
    10-second implicit wait (``Driver.java:34``) is what makes the lookup retry -
    so the log is exactly one lookup and one click, and the recorder saw no wait.
    """
    _, recorder, result = _drive(
        "Logged user clicks on Inventory Module",
        resolve_step,
        fake_context,
        stub_driver,
        monkeypatch,
    )

    assert _ordered_calls(stub_driver) == (
        (FIND, INVENTORY_MODULE),
        (CLICK, (INVENTORY_MODULE,)),
    )
    assert recorder.records == []
    assert result is None


def test_product_module_waits_twenty_seconds_then_clicks(
    resolve_step: Any,
    fake_context: Any,
    stub_driver: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``Inventory.java:20-24``: wait-before-click sequence #1, on ``PRODUCTS``.

    Two statements in the source's order - ``wait.until(visibilityOf(products))``
    at ``:22`` and ``products.click()`` at ``:23`` - with the 20 seconds
    ``:13`` constructs.  Order is the assertion: the wait entry precedes the
    click in the driver's single ordered log, and the click is preceded by its
    own lookup, because the Java field is a proxy that re-resolves on each use
    (``InventoryP.java:10-12``).
    """
    _, recorder, result = _drive(
        "User clicks on Product module",
        resolve_step,
        fake_context,
        stub_driver,
        monkeypatch,
    )

    assert _ordered_calls(stub_driver) == (
        (WAIT_OP, (PRODUCTS, WAIT_TIMEOUT)),
        (FIND, PRODUCTS),
        (CLICK, (PRODUCTS,)),
    )
    assert [(record.locator, record.timeout) for record in recorder.records] == [
        (PRODUCTS, WAIT_TIMEOUT)
    ]
    assert result is None


def test_see_the_products_reads_the_title_and_drops_the_answer(
    resolve_step: Any,
    fake_context: Any,
    stub_driver: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``Inventory.java:26-29``: the title is read, compared, and discarded.

    The body is ``Driver.getDriver().getTitle().equals("Products - Odoo")`` at
    ``:28``, which computes a boolean and ignores it.  The read really happens -
    it is the only operation this step performs, and the only place in the class
    that touches the driver rather than an element - and no element is looked up,
    because this body needs no page object.
    """
    stub_driver.title = PRODUCTS_TITLE

    _, recorder, result = _drive(
        "User see the products", resolve_step, fake_context, stub_driver, monkeypatch
    )

    assert _ordered_calls(stub_driver) == ((TITLE, ()),)
    assert recorder.records == []
    assert result is None


def test_clicks_create_button(
    resolve_step: Any,
    fake_context: Any,
    stub_driver: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``Inventory.java:31-34``: one click on the Kanban Create button, no wait.

    The body is ``inventory.createBtn.click()`` at ``:33``.  The source waits
    before the *save* button and not before this one (``:36-40`` versus
    ``:31-34``), and that asymmetry is preserved: three of the feature's four
    scenarios click Create immediately after the Products menu.
    """
    _, recorder, result = _drive(
        "User clicks create button",
        resolve_step,
        fake_context,
        stub_driver,
        monkeypatch,
    )

    assert _ordered_calls(stub_driver) == (
        (FIND, CREATE_BTN),
        (CLICK, (CREATE_BTN,)),
    )
    assert recorder.records == []
    assert result is None


def test_save_button_waits_twenty_seconds_then_clicks(
    resolve_step: Any,
    fake_context: Any,
    stub_driver: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``Inventory.java:36-40``: wait-before-click sequence #2, on ``SAVE_BTN``.

    ``wait.until(visibilityOf(saveBtn))`` at ``:38`` then ``saveBtn.click()`` at
    ``:39``, again at 20 seconds and again wait-first.  The class's second and
    last wait call site.
    """
    _, recorder, result = _drive(
        "User clicks the save button",
        resolve_step,
        fake_context,
        stub_driver,
        monkeypatch,
    )

    assert _ordered_calls(stub_driver) == (
        (WAIT_OP, (SAVE_BTN, WAIT_TIMEOUT)),
        (FIND, SAVE_BTN),
        (CLICK, (SAVE_BTN,)),
    )
    assert [(record.locator, record.timeout) for record in recorder.records] == [
        (SAVE_BTN, WAIT_TIMEOUT)
    ]
    assert result is None


def test_should_see_the_error_reads_is_displayed_and_drops_the_answer(
    resolve_step: Any,
    fake_context: Any,
    stub_driver: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``Inventory.java:42-45``: ``fieldError.isDisplayed()``, result discarded.

    The body is one statement at ``:44``, and there is no ``Assert`` around it -
    so a step whose phrase promises a visible error message verifies nothing.
    The notification container is looked up and asked; the answer goes nowhere.
    """
    _, recorder, result = _drive(
        "User should see the error",
        resolve_step,
        fake_context,
        stub_driver,
        monkeypatch,
    )

    assert _ordered_calls(stub_driver) == (
        (FIND, FIELD_ERROR),
        (IS_DISPLAYED, (FIELD_ERROR,)),
    )
    assert recorder.records == []
    assert result is None


def test_enters_product_name_types_the_ibm_literal(
    resolve_step: Any,
    fake_context: Any,
    stub_driver: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``Inventory.java:47-50``: ``productName.sendKeys("IBM")``, once.

    The literal is ``IBM`` and the field is the server-generated id
    ``o_field_input_479`` (``InventoryP.java:29``).  No wait, no clear before
    typing, no ``Keys`` member after it - the whole body is the one call at
    ``:49``, and the arguments are logged unflattened so that a stray extra
    argument would show.
    """
    _, recorder, result = _drive(
        "User enters Product Name", resolve_step, fake_context, stub_driver, monkeypatch
    )

    assert _ordered_calls(stub_driver) == (
        (FIND, PRODUCT_NAME),
        (SEND_KEYS, (PRODUCT_NAME, PRODUCT_NAME_VALUE)),
    )
    assert recorder.records == []
    assert result is None


def test_title_includes_product_name_reads_is_displayed_and_drops_the_answer(
    resolve_step: Any,
    fake_context: Any,
    stub_driver: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``Inventory.java:52-55``: ``productsList.isDisplayed()``, result discarded.

    The phrase says the page title includes the product name; the body at ``:54``
    reads neither the title nor the name.  It asks ``//span[.='EY']``
    (``InventoryP.java:32``) whether it is displayed and drops the answer - a
    selector whose baked-in ``EY`` is not the ``IBM`` of ``:49``.  Both the
    mismatch and the missing check are the source's and are preserved (AAP 0.8).
    """
    _, recorder, result = _drive(
        "User should see the title includes the Product Name",
        resolve_step,
        fake_context,
        stub_driver,
        monkeypatch,
    )

    assert _ordered_calls(stub_driver) == (
        (FIND, PRODUCTS_LIST),
        (IS_DISPLAYED, (PRODUCTS_LIST,)),
    )
    assert recorder.records == []
    assert result is None


def test_sees_the_created_product_reads_is_displayed_and_drops_the_answer(
    resolve_step: Any,
    fake_context: Any,
    stub_driver: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``Inventory.java:57-60``: ``createdProduct.isDisplayed()``, discarded.

    The last of the four discarded results and the last definition of the class -
    ``Inventory.java`` ends at ``:60``, so the module defines no tenth step.
    """
    _, recorder, result = _drive(
        "User sees the created Product",
        resolve_step,
        fake_context,
        stub_driver,
        monkeypatch,
    )

    assert _ordered_calls(stub_driver) == (
        (FIND, CREATED_PRODUCT),
        (IS_DISPLAYED, (CREATED_PRODUCT,)),
    )
    assert recorder.records == []
    assert result is None


# =========================================================================== #
# Zero assertions, four discarded results - the headline obligation
# =========================================================================== #


@pytest.mark.parametrize("spec", STEP_TABLE, ids=lambda spec: spec.function)
def test_no_step_body_contains_an_assert_statement(spec: StepSpec) -> None:
    """``Inventory.java`` makes no ``Assert`` call in any of its nine methods.

    Proved structurally, by parsing the module: a body that grew an ``assert``
    would be checking something the Java does not check, and a scenario would
    start failing where the original passed.  Parsing rather than grepping
    matters here, because the module's docstrings discuss assertions at length in
    order to explain their absence.
    """
    function = SOURCE_DEFINITIONS[spec.phrase]
    asserts = [node for node in ast.walk(function) if isinstance(node, ast.Assert)]

    assert asserts == [], (
        f"{spec.function} ({spec.java_lines}) contains an assert; the Java class "
        f"has no Assert call anywhere"
    )


def test_module_contains_no_assert_statement_at_all() -> None:
    """Nine methods, zero ``Assert`` calls - ``Inventory.java:10-64`` entire.

    The per-body test above covers the nine definitions; this one covers the
    whole file, so a helper such as ``_page`` cannot carry a check the step
    bodies are forbidden.
    """
    asserts = [
        f"line {node.lineno}"
        for node in ast.walk(MODULE_TREE)
        if isinstance(node, ast.Assert)
    ]

    assert asserts == [], (
        f"{STEP_MODULE_NAME} contains {len(asserts)} assert statement(s) at "
        f"{asserts}; Inventory.java has no Assert call in any of its nine "
        f"methods"
    )


@pytest.mark.parametrize("spec", DISCARDED, ids=lambda spec: spec.java_line)
def test_discarded_result_is_a_bare_expression_statement(spec: DiscardedSpec) -> None:
    """The four results of ``:28``, ``:44``, ``:54`` and ``:59`` are dropped.

    Structural half of the obligation: the statement performing each call is a
    bare expression - not an assignment, not a return, not an ``assert``, not
    the test of a branch - which is exactly what "the result is discarded" means
    in the Java and what makes these steps unable to fail on the page's answer.
    """
    function = SOURCE_DEFINITIONS[spec.phrase]
    statements = _body_statements(function)

    assert statements, f"{spec.phrase!r} has an empty body"

    final = statements[-1]

    assert isinstance(final, ast.Expr), (
        f"{spec.java_line}'s result is consumed by "
        f"{type(final).__name__} rather than discarded"
    )

    # The statement still has to *compute* something: a call for the three
    # isDisplayed() reads, a comparison for the title equality of :28.  An
    # expression statement that were neither would mean the operation had been
    # deleted rather than merely left unchecked, which is the opposite mistake
    # and equally a parity break.
    assert isinstance(final.value, (ast.Call, ast.Compare)), (
        f"{spec.java_line} no longer computes a value: "
        f"{ast.unparse(final)!r}"
    )


@pytest.mark.parametrize("spec", DISCARDED, ids=lambda spec: spec.java_line)
def test_discarded_result_does_not_fail_the_step(
    spec: DiscardedSpec,
    resolve_step: Any,
    fake_context: Any,
    stub_driver: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Behavioural half: a false answer still leaves the step passing.

    Each of the four calls is programmed to answer the way that *would* fail an
    assertion, had ``Inventory.java`` made one - the title reports a different
    page than ``:28`` expects, and the three ``isDisplayed()`` reads of ``:44``,
    ``:54`` and ``:59`` answer ``False``.  Every step still completes and still
    returns ``None``, and the call is still visible in the ordered log: "no
    assertion" is not "no operation".
    """
    if spec.locator is None:
        stub_driver.title = OTHER_TITLE
    else:
        stub_driver.set_displayed(spec.locator, False)

    _, _, result = _drive(
        spec.phrase, resolve_step, fake_context, stub_driver, monkeypatch
    )

    assert result is None
    assert spec.operation in _operations(_ordered_calls(stub_driver)), (
        f"{spec.java_line}'s call did not reach the driver"
    )


def test_discarded_title_comparison_uses_the_exact_literal() -> None:
    """``Inventory.java:28`` compares against ``"Products - Odoo"``, byte-exact.

    The comparison is contractual even though its result is not: the step reads
    the title and compares it, so the literal is part of the port's surface and
    a change to it would be a change to the source's behaviour, not a tidy-up.
    Asserted on the parsed statement, which also shows that the comparison is a
    comparison - neither a truth check nor a branch.
    """
    statements = _body_statements(SOURCE_DEFINITIONS["User see the products"])

    assert len(statements) == 1

    statement = statements[0]

    assert isinstance(statement, ast.Expr)
    assert isinstance(statement.value, ast.Compare), (
        "Inventory.java:28 is an equality comparison whose value is dropped"
    )

    literals = [
        node.value
        for node in ast.walk(statement.value)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]

    assert literals == [PRODUCTS_TITLE]


@pytest.mark.parametrize("spec", STEP_TABLE, ids=lambda spec: spec.function)
def test_step_body_is_straight_line_code(spec: StepSpec) -> None:
    """Every method of ``Inventory.java:15-60`` is a straight-line body.

    None of the nine branches, loops, raises or catches: the longest is three
    statements (``:20-24`` and ``:36-40``, wait then click) and the rest are one.
    A branch here would be a decision the source does not take - the natural way
    a "discarded result" quietly becomes a check - so the shape is pinned rather
    than left to review.
    """
    function = SOURCE_DEFINITIONS[spec.phrase]
    control_flow = [
        type(node).__name__
        for node in ast.walk(function)
        if isinstance(
            node,
            (
                ast.If,
                ast.IfExp,
                ast.For,
                ast.While,
                ast.Try,
                ast.Raise,
                ast.Assert,
                ast.With,
                ast.Match,
            ),
        )
    ]

    assert control_flow == [], (
        f"{spec.function} ({spec.java_lines}) is not straight-line code: "
        f"{control_flow}"
    )


# =========================================================================== #
# The two wait call sites, and the seven steps that wait for nothing
# =========================================================================== #


def test_module_has_exactly_two_wait_call_sites() -> None:
    """``Inventory.java`` waits twice: at ``:22`` and at ``:38``.

    Counted from the parsed source, and each site's numeric argument is the 20
    of ``:13``.  The count is as contractual as the value: a third wait would
    change the timing of a step the source leaves to the implicit wait, and a
    missing one would remove a 20-second grace period the Odoo UI needs.
    """
    sites = _wait_call_sites()

    assert len(sites) == 2, f"expected two wait call sites, found {sites}"
    assert [site[2] for site in sites] == [(WAIT_TIMEOUT,), (WAIT_TIMEOUT,)]


def test_only_the_two_documented_steps_wait(
    resolve_step: Any,
    fake_context: Any,
    stub_driver: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two of the nine steps wait; the other seven wait for nothing.

    The behavioural counterpart of the source count above: all nine bodies are
    run against the same recorder and only ``:20-24`` and ``:36-40`` register a
    wait, each once, each on its own locator at 20 seconds.  The seven others
    rely on the session's 10-second implicit wait exactly as the Java does.
    """
    match = resolve_step(ALL_PHRASES[0])
    recorder = _install_wait_recorder(
        match.func.__globals__, stub_driver, monkeypatch
    )
    waited: dict[str, list[tuple[tuple[str, str], Any]]] = {}

    for phrase in ALL_PHRASES:
        before = len(recorder.records)
        resolve_step(phrase).run(fake_context)
        waited[phrase] = [
            (record.locator, record.timeout) for record in recorder.records[before:]
        ]

    assert waited["User clicks on Product module"] == [(PRODUCTS, WAIT_TIMEOUT)]
    assert waited["User clicks the save button"] == [(SAVE_BTN, WAIT_TIMEOUT)]
    assert [phrase for phrase, records in waited.items() if records] == list(
        WAITING_PHRASES
    )


def test_both_wait_sites_use_the_twenty_second_timeout(
    resolve_step: Any,
    fake_context: Any,
    stub_driver: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One timeout for the whole class: 20 seconds, ``Inventory.java:13``.

    The field is constructed once and shared by both call sites, so the two
    intercepted waits must report the same number - and it must be 20, not the
    3 of ``LoginSD.java:17``, the 2 of ``Calendar.java:15`` or the 4 of
    ``Sales.java:17``.  Read as "the numeric argument, positional or keyword", so
    the assertion is about the value and not about the helper's signature.
    """
    match = resolve_step(WAITING_PHRASES[0])
    recorder = _install_wait_recorder(
        match.func.__globals__, stub_driver, monkeypatch
    )

    for phrase in WAITING_PHRASES:
        resolve_step(phrase).run(fake_context)

    timeouts = [record.timeout for record in recorder.records]

    assert timeouts == [WAIT_TIMEOUT, WAIT_TIMEOUT], (
        f"wait timeouts {timeouts} do not both equal Inventory.java:13's 20; "
        f"recorded calls: {recorder.records}"
    )


@pytest.mark.parametrize("phrase", WAITING_PHRASES)
def test_wait_precedes_the_click_it_guards(
    phrase: str,
    resolve_step: Any,
    fake_context: Any,
    stub_driver: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``Inventory.java:22-23`` and ``:38-39`` wait **first**, then click.

    Other classes in this suite click first and wait afterwards; this one does
    not, at either of its two sites.  Order is asserted from the single ordered
    log - the wait entry and the click are in one sequence, so this is one
    comparison rather than two observations - and the click is the last operation
    of each body.
    """
    _, _, _ = _drive(phrase, resolve_step, fake_context, stub_driver, monkeypatch)

    operations = _operations(_ordered_calls(stub_driver))

    assert operations == (WAIT_OP, FIND, CLICK)
    assert operations.index(WAIT_OP) < operations.index(CLICK)


# =========================================================================== #
# Whole-module behaviour: what no Inventory step ever does
# =========================================================================== #


def test_no_step_navigates_or_touches_the_session_lifecycle(
    resolve_step: Any,
    fake_context: Any,
    stub_driver: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``Inventory.java`` navigates nowhere and manages no session.

    ``Driver.getDriver()`` appears exactly once in the class, at ``:28``, and
    only to read a title: there is no ``driver.get(...)``, no ``navigate().back()``
    and no ``quit()`` - the Background has already logged in, every scenario
    proceeds by clicking, and the session's lifecycle belongs to ``Hooks.java``
    (``features/environment.py`` in the port).  All nine bodies are run and the
    whole log is checked, so a navigation added to any one of them fails.
    """
    match = resolve_step(ALL_PHRASES[0])
    _install_wait_recorder(match.func.__globals__, stub_driver, monkeypatch)

    for phrase in ALL_PHRASES:
        resolve_step(phrase).run(fake_context)

    performed = set(_operations(_ordered_calls(stub_driver)))

    assert performed.isdisjoint(FORBIDDEN_DRIVER_OPS), (
        f"forbidden operations reached the driver: "
        f"{sorted(performed.intersection(FORBIDDEN_DRIVER_OPS))}"
    )
    assert stub_driver.quit_count == 0
    assert performed == {FIND, CLICK, SEND_KEYS, IS_DISPLAYED, TITLE, WAIT_OP}


def test_nine_bodies_perform_exactly_the_java_operation_census(
    resolve_step: Any,
    fake_context: Any,
    stub_driver: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The class's whole observable surface, counted: ``Inventory.java:15-60``.

    Four clicks (``:17``, ``:23``, ``:33``, ``:39``), one ``sendKeys`` (``:49``),
    three ``isDisplayed()`` reads (``:44``, ``:54``, ``:59``), one title read
    (``:28``) and two waits (``:22``, ``:38``).  Asserting the totals as well as
    the per-step sequences is what catches an operation moved from one body to
    another, which the per-step tests alone would report as two failures without
    naming the whole.
    """
    match = resolve_step(ALL_PHRASES[0])
    _install_wait_recorder(match.func.__globals__, stub_driver, monkeypatch)

    for phrase in ALL_PHRASES:
        resolve_step(phrase).run(fake_context)

    operations = _operations(_ordered_calls(stub_driver))
    counted = {name: operations.count(name) for name in set(operations)}

    assert counted == {
        FIND: 8,
        CLICK: 4,
        SEND_KEYS: 1,
        IS_DISPLAYED: 3,
        TITLE: 1,
        WAIT_OP: 2,
    }, f"operation census drifted: {counted}"


def test_send_keys_is_used_once_in_the_module_with_ibm(
    resolve_step: Any,
    fake_context: Any,
    stub_driver: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``Inventory.java:49`` is the class's only ``sendKeys``, and it types ``IBM``.

    Asserted twice over: the parsed source contains exactly one ``send_keys``
    call site whose only literal is ``IBM``, and running all nine bodies produces
    exactly one logged ``send_keys``, against ``PRODUCT_NAME``, with that one
    argument.  No ``Keys`` member follows it - ``Inventory.java`` imports neither
    ``Keys`` nor ``Actions``.
    """
    send_keys_calls = [
        node
        for node in ast.walk(MODULE_TREE)
        if isinstance(node, ast.Call) and ast.unparse(node.func).endswith(".send_keys")
    ]

    assert len(send_keys_calls) == 1

    arguments = [
        argument.value
        for argument in send_keys_calls[0].args
        if isinstance(argument, ast.Constant)
    ]

    assert arguments == [PRODUCT_NAME_VALUE]
    assert send_keys_calls[0].keywords == []

    match = resolve_step(ALL_PHRASES[0])
    _install_wait_recorder(match.func.__globals__, stub_driver, monkeypatch)

    for phrase in ALL_PHRASES:
        resolve_step(phrase).run(fake_context)

    assert stub_driver.calls_of(SEND_KEYS) == ((PRODUCT_NAME, PRODUCT_NAME_VALUE),)


# =========================================================================== #
# Module boundary, by source inspection
# =========================================================================== #


def test_imports_are_exactly_the_three_the_boundary_allows() -> None:
    """AAP 0.4.2's import boundary, as ``Inventory.java:3-8`` translates.

    The Java imports the two Cucumber annotations, its page object, ``Driver``,
    and ``ExpectedConditions``/``WebDriverWait``.  In the port that is three
    imports and no more: the Gherkin decorator from ``behave``, the wait helper
    from ``app.automation`` - the only package permitted to import the browser
    binding - and the page object from ``app.pages``.  The wait helper is
    asserted by prefix rather than by name, because which helper of
    ``app/automation/waits.py`` a call site uses is not this module's contract.
    """
    imports = dict(_imports())

    assert set(imports) == {"behave", "app.automation", "app.pages"}
    assert imports["behave"] == ("step",)
    assert imports["app.pages"] == ("InventoryPage",)
    assert imports["app.automation"], "no wait helper is imported"
    assert all(name.startswith(WAIT_PREFIX) for name in imports["app.automation"]), (
        f"app.automation names {imports['app.automation']} reach beyond the wait "
        f"family this class uses"
    )


def test_module_does_not_import_selenium() -> None:
    """Only ``app/automation`` may import the browser binding (AAP 0.4.2).

    ``Inventory.java:7-8`` imports ``ExpectedConditions`` and ``WebDriverWait``
    directly, and the port replaces both with the wait helper, so no Selenium
    name reaches this module - not under ``TYPE_CHECKING`` either, a guarded
    import being an import statement all the same.
    """
    modules = [module for module, _ in _imports()]

    assert not [module for module in modules if module.split(".")[0] == "selenium"]


def test_module_does_not_import_by() -> None:
    """``Inventory.java`` never constructs a locator, so the port imports no ``By``.

    ``LoginSD.java:56`` is the suite's single ``By`` user - a direct
    ``By.name("login")`` lookup - and ``login_steps`` is therefore the only step
    module that may import it.  Every locator this class touches is declared by
    ``InventoryP.java:14-36`` and reached through the page object.
    """
    imported = {name for _, names in _imports() for name in names}

    assert "By" not in imported


def test_module_imports_no_interaction_helpers() -> None:
    """``Inventory.java`` uses neither ``Keys`` nor ``Actions``.

    ``app/automation/interactions.py`` exists for Crm, Notes and Sales; nothing
    from it belongs here, and neither does a raw key literal.
    """
    modules = [module for module, _ in _imports()]
    imported = {name for _, names in _imports() for name in names}

    assert "app.automation.interactions" not in modules
    assert imported.isdisjoint({"press_keys", "action_chain", "Keys", "ActionChains"})


def test_module_reads_no_configuration() -> None:
    """``Inventory.java`` reads none of the six configuration keys.

    ``ConfigurationReader.getProperty`` is called by ``LoginSD``, ``Session``,
    ``EmployeeStage`` and ``Driver`` only.  An Inventory step that reached
    ``app.config`` would be reading a key the source never reads, so neither the
    module nor any helper of it may import it.
    """
    modules = [module for module, _ in _imports()]
    called = _called_names()

    assert not [module for module in modules if module.startswith("app.config")]
    assert not [name for name in called if "config" in name.lower()]


def test_module_contains_no_fixed_delay() -> None:
    """``Inventory.java`` contains no ``Thread.sleep``.

    The suite's seventeen fixed delays belong to Calendar (two of 3s), Contacts
    (five of 3s), Crm (one of 2s), EmployeeStage (one of 7s and seven of 3s) and
    Notes (one of 2s), and AAP 0.4.1 keeps each at its own call site.  None is
    in this class, so neither a ``time`` import nor a ``sleep`` call may appear -
    checked on the parsed source, since this module's docstrings discuss delays
    in order to explain their absence.
    """
    modules = [module for module, _ in _imports()]
    imported = {name for _, names in _imports() for name in names}
    called = _called_names()

    assert "time" not in modules
    assert "sleep" not in imported
    assert not [name for name in called if name.split(".")[-1] == "sleep"]


def test_module_never_creates_or_quits_a_driver() -> None:
    """The session lifecycle has one owner, and it is not a step module.

    ``Inventory.java`` calls ``Driver.getDriver()`` once (``:28``) and never
    ``closeDriver()``; in the port the session arrives on the behave context,
    published by ``features/environment.py``, so no step module may call
    ``get_driver`` or ``quit_driver`` or construct a browser.
    """
    imported = {name for _, names in _imports() for name in names}
    called = _called_names()

    assert imported.isdisjoint({"get_driver", "quit_driver", "webdriver"})
    assert not [
        name
        for name in called
        if name.split(".")[-1] in {"get_driver", "quit_driver", "quit", "close"}
    ]


@pytest.mark.parametrize("spec", STEP_TABLE, ids=lambda spec: spec.function)
def test_definition_registers_with_step_in_source(spec: StepSpec) -> None:
    """Source-level half of AAP deviation 7: every decorator is ``@step``.

    The registry test above proves where the definitions landed; this proves how
    they were written, so a ``@when`` that happened to land in a reachable bucket
    would still be caught.  ``Inventory.java``'s own mix of ``@When`` and
    ``@Then`` carries no meaning - Cucumber-JVM matches on text alone - which is
    why the port uses one decorator for all nine.
    """
    decorators = _decorator_names(SOURCE_DEFINITIONS[spec.phrase])

    assert decorators == ("step",), (
        f"{spec.function} ({spec.java_lines}) is registered with {decorators}"
    )


# =========================================================================== #
# features/Inventory.feature
# =========================================================================== #

#: The feature file's text, read once.
FEATURE_SOURCE: Final[str] = FEATURE_PATH.read_text(encoding="utf-8")

#: Its lines, for the header and tag assertions.
FEATURE_LINES: Final[tuple[str, ...]] = tuple(FEATURE_SOURCE.splitlines())


def _feature_phrases() -> tuple[str, ...]:
    """Every step phrase in ``Inventory.feature``, in file order, deduplicated.

    :returns: The phrases, with the Gherkin keyword stripped.
    """
    phrases: list[str] = []

    for line in FEATURE_LINES:
        stripped = line.strip()

        for keyword in GHERKIN_KEYWORDS:
            if stripped.startswith(keyword):
                phrases.append(stripped[len(keyword) :].strip())
                break

    return tuple(dict.fromkeys(phrases))


#: The distinct phrases of the feature - the nine this module owns plus the two
#: it borrows.
FEATURE_PHRASES: Final[tuple[str, ...]] = _feature_phrases()


def _scenario_headers() -> tuple[str, ...]:
    """Every ``Scenario`` header line of the feature, stripped, in file order.

    :returns: The header lines.  A ``Scenario Outline`` header would be included
        too, which is deliberate: the count assertion below would then fail,
        which is the outcome required - ``Inventory.feature`` has four plain
        scenarios and no outline.
    """
    return tuple(
        line.strip() for line in FEATURE_LINES if line.strip().startswith("Scenario")
    )


def test_feature_carries_no_feature_level_tag() -> None:
    """``Inventory.feature:1`` has no tag, so ``@Smoke`` does not select it.

    AAP 0.4.1: "No feature-level tag; four plain scenarios".  The default filter
    is ``@Smoke`` (``CukesRunner.java:18``), which matches ``Crm.feature:1``
    alone, so this feature is reachable only by a negative expression such as
    ``not @Smoke``.  Preserved rather than corrected, and the mis-titled header
    it shares with ``Contact.feature:1`` is preserved with it.
    """
    assert FEATURE_LINES[0] == "Feature: Testinium app Inventory feature"
    assert [line for line in FEATURE_LINES if line.strip().startswith("@")] == []


def test_feature_declares_four_plain_scenarios() -> None:
    """Four ``Scenario:`` blocks, no outline and no ``Examples`` table.

    ``Inventory.feature:11``, ``:18``, ``:26`` and ``:33``.  This is the shape
    AAP 0.4.1 records for the feature, and it is why the nine definitions take no
    parameters: nothing here is data-driven.
    """
    headers = _scenario_headers()

    assert len(headers) == 4
    assert "Scenario Outline" not in FEATURE_SOURCE
    assert "Examples" not in FEATURE_SOURCE


def test_third_scenario_header_has_no_space_after_the_colon() -> None:
    """``Inventory.feature:26`` reads ``Scenario:Verify`` - no space, preserved.

    A typographical quirk with a real consequence: the scenario name reaches
    ``target/cucumber.json`` and every report page, so normalizing it would
    change artifact content that AAP 0.8 keeps byte-identical to the source's.
    The other three headers do have the space, and the contrast is the point.
    """
    headers = _scenario_headers()
    without_space = [header for header in headers if header.startswith("Scenario:V")]

    assert without_space == [
        "Scenario:Verify that if Product name field leaves blank, an error message "
        "'The following fields are invalid:' is appeared"
    ]
    assert len([header for header in headers if header.startswith("Scenario: ")]) == 3


def test_background_supplies_the_shared_login_precondition(resolve_step: Any) -> None:
    """``Inventory.feature:9`` is the shared precondition of ``Session.java:12-17``.

    One of its seven usages, and one of the six where a step Java declares
    ``@When`` is invoked as a ``Given`` - which resolves only because every
    definition in this port registers with ``@step`` (AAP deviation 7).  The
    phrase is owned by ``session_steps`` and logs in before any Inventory step
    runs, which is why none of the nine navigates.
    """
    background_steps = [
        line.strip()
        for line in FEATURE_LINES
        if line.strip().startswith("Given ")
    ]

    assert background_steps == ["Given User login to test other features"]

    match = resolve_step("User login to test other features")

    assert match.module_name == "session_steps"
    assert match.bucket == "step"


@pytest.mark.parametrize("phrase", FEATURE_PHRASES)
def test_every_feature_phrase_resolves(phrase: str, resolve_step: Any) -> None:
    """Every phrase in ``Inventory.feature`` resolves to exactly one definition.

    Including the two this module does not own - the Background's precondition
    and ``User should see the dashboard`` at ``:16`` - because an undefined step
    anywhere in the file makes a scenario unrunnable whoever owns the phrase.
    ``resolve_step`` fails both on no match and on more than one, so this also
    proves none of the eleven is ambiguous under ``@step``.
    """
    match = resolve_step(phrase)

    assert match.bucket == "step"
    assert match.module_name.endswith("_steps")


def test_feature_phrases_cover_every_owned_definition() -> None:
    """All nine definitions are used by ``Inventory.feature``, and none is dead.

    The other direction of the coverage question: a definition no scenario
    reaches would be dead glue, and a scenario step the module does not own
    would be someone else's.  The nine phrases of :data:`STEP_TABLE` all appear
    in the file, and what remains is exactly the two borrowed ones.
    """
    owned_in_feature = [phrase for phrase in FEATURE_PHRASES if phrase in ALL_PHRASES]
    borrowed = [phrase for phrase in FEATURE_PHRASES if phrase not in ALL_PHRASES]

    assert set(owned_in_feature) == set(ALL_PHRASES)
    assert borrowed == [phrase for phrase, _ in FOREIGN_PHRASE_OWNERS]
