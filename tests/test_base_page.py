r"""Tests for ``app/pages/base_page.py`` - the port's ``PageFactory`` replacement.

``BasePage`` is the whole of the mechanism standing in for Java's
``PageFactory.initElements`` / ``@FindBy`` pair (``LoginP.java:9-11``), which
AAP 0.5.2 maps onto *"locator constants plus lazy accessors in
``app/pages/base_page.py``"*.  AAP 0.3.3 fixes why laziness is the contract: an
eager ``find_element`` in ``__init__`` *"would change when elements are looked
up and break scenarios that build a page before navigating."*  All ten page
objects inherit it, so a regression here reaches every scenario.

The contract this module owns: inert construction, touching neither driver nor
DOM; resolution on every access, singular and plural, with nothing cached; the
naming transform, where ``INPUT_EMAIL`` yields a read-only ``input_email``
accessor, the constant stays readable as a ``(strategy, value)`` tuple, and a
name shadowing the mechanism or a class-body method is rejected at class
creation; ``PLURAL_LOCATORS`` routing a Java ``List<WebElement>`` field through
``find_elements``; ``LOCATORS`` complete, in declaration order, immutable and
merged ancestors-first; a driver seam resolved per access, injection first and
this worker's session otherwise, with ``find`` and ``find_all`` its only lookup
funnels; and the closed AAP 0.4.2 boundary, a page object reaching
``app.automation`` for the driver and nothing else.

Laziness is measured by call *count* on an ordered driver log, since a cached
element satisfies any result-only assertion, and every mechanism assertion is
applied a second time to a deliberately wrong page declared below.

Per-page locator inventories belong to ``tests/test_pages.py``; the locators
here are invented for the test.  Nothing launches a browser, sleeps, reads
``configuration.properties`` or writes into ``target/``, and selenium is never
imported: strategies are compared against ``app.automation.By``, the
plain-string re-export AAP 0.4.2 authorises (``By.XPATH == "xpath"``).
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

import pytest

from app.automation import By
from app.pages import LoginPage
from app.pages import base_page as base_page_module
from app.pages.base_page import BasePage

# ``tests/`` is on ``sys.path`` under pytest's prepend import mode (there is
# deliberately no ``tests/__init__.py``), so the suite's shared constants are
# importable from the conftest that owns them.  Importing them rather than
# restating them is what keeps the element-operation prefix and the repository
# root defined exactly once for the whole suite.
from conftest import ELEMENT_PREFIX, REPO_ROOT, StubDriver

# =========================================================================== #
# Locations and fixed expectations
# =========================================================================== #

#: The module under test, read as text for the source-inspection assertions.
#: Read once at import time: it is a tracked file and no test modifies it.
BASE_PAGE_PATH: Final[Path] = REPO_ROOT / "app" / "pages" / "base_page.py"
BASE_PAGE_SOURCE: Final[str] = BASE_PAGE_PATH.read_text(encoding="utf-8")

#: The exact import surface AAP 0.4.2 allows this module: three standard-library
#: names and the two ``app.automation`` re-exports.  Stated as the full set
#: rather than as a denylist, so an *addition* fails as loudly as a violation.
EXPECTED_BASE_PAGE_IMPORTS: Final[frozenset[str]] = frozenset(
    {
        "collections.abc.Mapping",
        "types.MappingProxyType",
        "typing.Any",
        "app.automation.By",
        "app.automation.get_driver",
    }
)

#: Names whose appearance anywhere in the module's *code* would contradict a
#: documented guarantee: memoization of a lookup, or a wait stacked on top of
#: the session's 10-second implicit wait (``Driver.java:34``, ``:40``).
#: Checked through the syntax tree, so the prose in the module's docstring -
#: which discusses all of them by name - cannot produce a false positive, and a
#: real use cannot hide behind a string or a comment.
FORBIDDEN_CODE_NAMES: Final[frozenset[str]] = frozenset(
    {
        "cached_property",
        "lru_cache",
        "cache",
        "sleep",
        "WebDriverWait",
        "implicitly_wait",
        "expected_conditions",
    }
)

#: The calls the module is allowed to evaluate at import time.  Both build
#: immutable constants; neither touches a driver, the DOM, the filesystem or
#: the logging configuration.
ALLOWED_IMPORT_TIME_CALLS: Final[frozenset[str]] = frozenset(
    {"frozenset", "MappingProxyType"}
)

#: Every locator strategy ``app.automation.By`` defines, with the string each
#: one is.  The mechanism must stay strategy-agnostic, so all eight are
#: exercised even though the reference declares only five of them.
ALL_BY_STRATEGIES: Final[tuple[tuple[str, str], ...]] = (
    ("ID", By.ID),
    ("XPATH", By.XPATH),
    ("LINK_TEXT", By.LINK_TEXT),
    ("PARTIAL_LINK_TEXT", By.PARTIAL_LINK_TEXT),
    ("NAME", By.NAME),
    ("TAG_NAME", By.TAG_NAME),
    ("CLASS_NAME", By.CLASS_NAME),
    ("CSS_SELECTOR", By.CSS_SELECTOR),
)

#: Log entry names, built from the conftest prefix so this module carries no
#: second copy of the ``"element."`` convention.
ELEMENT_CLICK: Final[str] = f"{ELEMENT_PREFIX}click"
ELEMENT_SEND_KEYS: Final[str] = f"{ELEMENT_PREFIX}send_keys"


# =========================================================================== #
# Probe pages
#
# Every page in this section is declared *here*, in the test module, for one
# reason: the mechanism has to be provable on inputs the test controls
# completely, including inputs no real page would ever contain.  None of them
# imports or modifies anything under ``app/pages``, and none is named ``Test*``
# so pytest collects none of them as a test class.
# =========================================================================== #


class ProbeElement:
    """A minimal stand-in element returned by the local recorders below.

    The shared :class:`~conftest.StubDriver` has a far richer element; this
    exists only for the two local drivers, which need an object whose identity
    a test can compare and which carries the locator that produced it.
    """

    __slots__ = ("locator",)

    def __init__(self, locator: tuple[str, str]) -> None:
        """Bind the element to the locator that produced it.

        :param locator: The ``(strategy, value)`` pair looked up.
        """
        self.locator = locator

    def __repr__(self) -> str:
        """Render the locator, which is what a failure needs to see.

        :returns: A short representation.
        """
        return f"<ProbeElement {self.locator!r}>"


class FalsyProbeDriver:
    """A driver that is *falsy* yet not ``None``, recording its lookups.

    ``BasePage.__init__`` stores its argument without normalising it and the
    :attr:`~app.pages.base_page.BasePage.driver` property tests ``is not
    None`` - never truthiness - so a driver object whose ``__bool__`` is
    ``False`` must still be used.  A real ``WebDriver`` is always truthy, which
    is exactly why this case needs a purpose-built stand-in: the distinction
    between ``if self._driver`` and ``if self._driver is not None`` is
    invisible without it.
    """

    def __init__(self) -> None:
        """Start with an empty lookup log."""
        self.lookups: list[tuple[str, str]] = []

    def __bool__(self) -> bool:
        """Report falsiness, which is the whole point of this class.

        :returns: ``False``, always.
        """
        return False

    def find_element(self, by: str, value: str) -> ProbeElement:
        """Record a singular lookup.

        :param by: Locator strategy.
        :param value: Locator value.
        :returns: An element carrying the locator.
        """
        self.lookups.append((by, value))
        return ProbeElement((by, value))

    def find_elements(self, by: str, value: str) -> list[ProbeElement]:
        """Record a plural lookup.

        :param by: Locator strategy.
        :param value: Locator value.
        :returns: A one-element list carrying the locator.
        """
        self.lookups.append((by, value))
        return [ProbeElement((by, value))]


class DriverSource:
    """A counting stand-in for the module-global ``get_driver``.

    Substituted with ``monkeypatch.setattr(app.pages.base_page, "get_driver",
    ...)``, which is the seam the module documents: AAP 0.4.2 fixes the import
    form as ``from app.automation import ...``, so the substitutable name is
    the binding *in ``base_page``*.  Patching ``app.automation.get_driver``
    rebinds a different name, and one of the tests below proves that
    difference rather than asserting it.

    It counts calls because "resolved per access" is a claim about *how often*
    the session is looked up, not about what comes back.
    """

    def __init__(self, driver: Any) -> None:
        """Record which driver to hand back.

        :param driver: The object to return from every call, ``None``
            included - the value ``Driver.java:45`` returns when the slot is
            empty.
        """
        self.driver = driver
        self.calls = 0

    def __call__(self) -> Any:
        """Hand back the configured driver, counting the consultation.

        :returns: The driver given to the constructor.
        """
        self.calls += 1
        return self.driver


class ProbePage(BasePage):
    """One singular locator, one plural locator, and nothing else.

    The shape of a real page module (``app/pages/login_page.py`` and its nine
    siblings declare constants and no behaviour), reduced to the smallest
    inventory that still covers both access forms.
    """

    PLURAL_LOCATORS = frozenset({"ROWS"})

    INPUT_EMAIL = (By.NAME, "login")
    BUTTON = (By.XPATH, "//button[.='Log in']")
    ROWS = (By.CLASS_NAME, "o_kanban_record")


class AncestorProbePage(BasePage):
    """A page other probe pages inherit from, to exercise the merge order."""

    FIRST = (By.ID, "first")
    SECOND = (By.NAME, "second")


class DescendantProbePage(AncestorProbePage):
    """Declares one new constant and re-declares an inherited one.

    No page under ``app/pages`` is a subclass of another, so this class is the
    sole exercise of the inheritance half of ``__init_subclass__``:
    ``app/pages/base_page.py`` merges ancestors *"so that inheritance is not
    actively broken"*, and an unexercised merge is a merge that breaks
    unnoticed.
    """

    THIRD = (By.XPATH, "//third")
    SECOND = (By.CLASS_NAME, "second-in-descendant")


class RecogniserProbePage(BasePage):
    """Every shape ``_is_locator_declaration`` must accept or reject.

    Two constants qualify - :attr:`REAL` and :attr:`DIGITS2`, the latter
    standing in for the reference's only digit-bearing field,
    ``CrmP.progressPipeline2`` - and everything else is a near miss that must
    acquire neither an accessor nor a ``LOCATORS`` entry.
    """

    #: Accepted: upper-case name, two-string tuple, recognised strategy.
    REAL = (By.ID, "real")

    #: Accepted: ``str.isupper()`` ignores digits.
    DIGITS2 = (By.ID, "digits")

    #: Rejected: two strings, but the first is not a strategy.  This is the
    #: incidental pair the recogniser exists to keep out - a page could
    #: legitimately declare a pair of expected titles.
    TITLE_PAIR = ("Odoo", "Employees")

    #: Rejected: three elements.
    TRIPLE = (By.ID, "a", "b")

    #: Rejected: a list where a tuple is required.
    AS_LIST = [By.ID, "as-list"]

    #: Rejected: not a tuple at all.
    NOT_A_TUPLE = "text"

    #: Rejected: the second element is not a string.
    MIXED_PAIR = (By.ID, 7)

    #: Rejected: a leading underscore marks a page's own internal.
    _PRIVATE = (By.ID, "private")

    #: Rejected: not upper-case.
    lower_case = (By.ID, "lower")


class PluralAncestorProbePage(BasePage):
    """Declares a plural locator that a subclass will inherit."""

    PLURAL_LOCATORS = frozenset({"ROWS"})

    ROWS = (By.CLASS_NAME, "row")


class PluralDescendantProbePage(PluralAncestorProbePage):
    """Declares nothing at all, so both the locator and its plurality inherit.

    ``__init_subclass__`` reads ``cls.PLURAL_LOCATORS`` through the class - not
    out of the class body - and this is the case that distinguishes the two:
    read from ``vars(cls)`` the inherited declaration would be missing and the
    accessor would silently become singular.
    """


class CachingProbePage(BasePage):
    """**A deliberately wrong page**: it memoizes a lookup.

    Nothing in ``app/pages`` looks like this, and nothing may: memoizing a
    found element - on the instance, in a dict, through
    ``functools.cached_property``, anywhere - is a behaviour change, because
    the Java proxy re-locates on every method invocation.

    It exists so the no-caching assertion can be shown to *detect* caching:
    :func:`test_the_no_caching_assertion_detects_a_caching_accessor` counts
    lookups through both this page and :class:`ProbePage` and asserts the two
    counts differ.  Note that the memoizing property has to be given a name no
    constant maps onto - ``cached_target``, not ``target`` - because
    ``__init_subclass__`` rejects an accessor that would shadow a class-body
    attribute, which is itself asserted below.
    """

    TARGET = (By.NAME, "login")

    def __init__(self, driver: Any = None) -> None:
        """Store the driver and an empty memo slot.

        :param driver: Passed straight to :class:`BasePage`.
        """
        super().__init__(driver)
        self._memo: Any = None

    @property
    def cached_target(self) -> Any:
        """Resolve :attr:`TARGET` once and hand back the same element after.

        :returns: The memoized element.
        """
        if self._memo is None:
            self._memo = self.find(self.TARGET)

        return self._memo


class EagerProbePage(BasePage):
    """**A deliberately wrong page**: it resolves in its constructor.

    The exact defect AAP 0.3.3 describes - construction reaching the DOM - kept
    here so the inert-construction assertion can be shown to detect it.
    """

    TARGET = (By.NAME, "login")

    def __init__(self, driver: Any = None) -> None:
        """Store the driver and immediately resolve :attr:`TARGET`.

        :param driver: Passed straight to :class:`BasePage`.
        """
        super().__init__(driver)
        self.resolved_at_construction = self.find(self.TARGET)


class ReversedProbePage(BasePage):
    """**A deliberately wrong page**: it unpacks the locator backwards.

    ``BasePage.find`` calls ``find_element(*locator)``, so the strategy arrives
    first and the value second.  A page that reversed them would still call the
    driver exactly once per access and would still satisfy every call-count
    assertion, which is why the argument-order assertion is separate - and why
    this class exists to prove that assertion can fail.
    """

    TARGET = (By.NAME, "login")

    def find(self, locator: tuple[str, str]) -> Any:
        """Look the locator up with its two elements swapped.

        :param locator: The ``(strategy, value)`` pair to mis-handle.
        :returns: Whatever the driver returns.
        """
        return self.driver.find_element(*reversed(locator))


# =========================================================================== #
# Source-inspection helpers
#
# Each one takes source *text* and answers one question about it, so the same
# helper can be pointed at the real module and at the synthetic bad sources
# further down.  That is what makes the sensitivity proof a committed test
# rather than a manual experiment: a helper that stopped detecting a violation
# would fail its own negative test.
#
# They are deliberately syntax-tree based rather than textual.  The module
# under test *discusses* ``selenium``, ``cached_property`` and ``sleep`` at
# length in its docstring, and several page modules begin a prose line with the
# word "from", so a grep-based check would produce both false positives and -
# for an import split across lines - false negatives.  ``ast`` sees exactly
# what the interpreter will execute, including an import hidden inside a
# ``TYPE_CHECKING`` guard.
#
# ``tests/test_pages.py`` carries its own copy of the three helpers it needs.
# That duplication is deliberate: each module must be runnable on its own, the
# suite has no shared test-helper module, and ``tests/conftest.py`` - which
# would be the place for one - is not this module's to extend.
# =========================================================================== #


def imported_names(source: str) -> frozenset[str]:
    """Every dotted name an import statement in *source* binds.

    :param source: Python source text.
    :returns: ``"module.name"`` for each name bound by a ``from`` import, and
        the module's own dotted path for a plain ``import``.  A relative
        import keeps its leading dots, so it cannot be mistaken for an
        absolute one.

    Walks the whole tree rather than the module body, so an import nested
    inside a function, a ``try`` or an ``if TYPE_CHECKING:`` guard is reported
    exactly like a top-level one.
    """
    names: set[str] = set()

    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = "." * node.level + (node.module or "")
            names.update(f"{module}.{alias.name}" for alias in node.names)

    return frozenset(names)


def imported_modules(source: str) -> frozenset[str]:
    """The set of modules *source* imports from, at any nesting depth.

    :param source: Python source text.
    :returns: One entry per module named by an import statement.
    """
    modules: set[str] = set()

    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add("." * node.level + (node.module or ""))

    return frozenset(modules)


def import_time_call_names(source: str) -> tuple[str, ...]:
    """Names of every call *source* evaluates when it is imported.

    :param source: Python source text.
    :returns: The called name - ``frozenset`` for ``frozenset(...)``,
        ``getLogger`` for ``logging.getLogger(...)`` - once per call site, in
        source order.

    Module-level statements and class bodies both execute at import time, so
    both are walked; function and method bodies do not, so they are skipped.
    A page module that started a browser, read a file or configured logging on
    import would be visible here as the call it makes.
    """
    names: list[str] = []

    def walk(body: list[ast.stmt]) -> None:
        for statement in body:
            if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
                continue

            if isinstance(statement, ast.ClassDef):
                walk(statement.body)
                continue

            for node in ast.walk(statement):
                if isinstance(node, ast.Call):
                    function = node.func
                    names.append(
                        function.id
                        if isinstance(function, ast.Name)
                        else getattr(function, "attr", "<expression>")
                    )

    walk(ast.parse(source).body)

    return tuple(names)


def referenced_code_names(source: str) -> frozenset[str]:
    """Every identifier *source* uses in code: bare names and attributes.

    :param source: Python source text.
    :returns: The union of every ``Name`` identifier and every attribute name.

    String literals, comments and docstrings contribute nothing, which is the
    point: this is what lets a denylist name a term the module under test
    explains in prose.
    """
    names: set[str] = set()

    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)

    return frozenset(names)


def method_call_sites(source: str, method: str) -> int:
    """How many times *source* calls ``<something>.<method>(...)``.

    :param source: Python source text.
    :param method: The attribute being called, such as ``"find_element"``.
    :returns: The number of such call sites.

    The direct expression of "one funnel": ``find`` and ``find_all`` are the
    single ``find_element`` and ``find_elements`` call sites in the package,
    and a second one anywhere in the module would be a second place where a
    cache, a wait or an exception handler could later appear.
    """
    return sum(
        1
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == method
    )


# --------------------------------------------------------------------------- #
# Synthetic bad sources, one per helper.  Each is the smallest module that
# violates exactly one guarantee, so the negative test below it fails only if
# the helper has stopped being able to see that violation.
# --------------------------------------------------------------------------- #

BAD_DIRECT_SELENIUM_IMPORT: Final[str] = '''
"""A page module that reaches past the AAP 0.4.2 boundary."""

from selenium.webdriver.common.by import By

LOCATOR = (By.NAME, "login")
'''

BAD_GUARDED_SELENIUM_IMPORT: Final[str] = '''
"""A page module hiding the same violation behind a typing guard."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from selenium.webdriver.remote.webelement import WebElement
'''

BAD_IMPORT_TIME_DRIVER_CALL: Final[str] = '''
"""A page module that creates a browser session when it is imported."""

from app.automation import get_driver

DRIVER = get_driver()
'''

BAD_CACHED_PROPERTY: Final[str] = '''
"""A page whose accessor memoizes the element it found."""

from functools import cached_property


class Page:
    """One cached accessor is one broken guarantee."""

    @cached_property
    def input_email(self):
        """Resolve once, then hand back the same element for ever."""
        return self.find(("name", "login"))
'''

BAD_SLEEPING_LOOKUP: Final[str] = '''
"""A page that stacks a fixed delay on top of the implicit wait."""

import time


class Page:
    """Sleeping here would change how long every failing step takes."""

    def find(self, locator):
        """Wait, then look up."""
        time.sleep(1)
        return self.driver.find_element(*locator)
'''

BAD_SECOND_LOOKUP_CALL_SITE: Final[str] = '''
"""A page with two ``find_element`` call sites instead of one."""


class Page:
    """The second call site is the one that will drift."""

    def find(self, locator):
        """The funnel."""
        return self.driver.find_element(*locator)

    def find_again(self, locator):
        """A second, competing funnel."""
        return self.driver.find_element(*locator)
'''


# --------------------------------------------------------------------------- #
# Small assertion helpers
# --------------------------------------------------------------------------- #


def lookup_count(driver: StubDriver, page: BasePage, accessor: str, times: int) -> int:
    """Read *accessor* *times* over and report how many lookups it caused.

    :param driver: The recorder the page resolves through; its log is cleared
        first so the count describes this reading and nothing before it.
    :param page: The page object to read from.
    :param accessor: The lower-case accessor name, such as ``"input_email"``.
    :param times: How many times to read it.
    :returns: The number of ``find_element`` entries the readings produced.

    The measurement the no-caching guarantee reduces to, extracted so that the
    same measurement can be applied to a correct page and to
    :class:`CachingProbePage` and the two results compared.
    """
    driver.clear_calls()

    for _ in range(times):
        getattr(page, accessor)

    return driver.count_of("find_element")


# =========================================================================== #
# 1. Inert construction
#
# ``PageFactory.initElements`` installs proxies and looks nothing up, so
# constructing a page object must reach neither the DOM nor a session.  The
# Python constructor goes one step further than the Java one and does not even
# ask for the driver, because ``Driver.getDriver()`` creates a session on
# demand and AAP 0.3.3 reserves that to ``features/environment.py``.
# =========================================================================== #


def test_construction_with_an_injected_driver_touches_nothing(
    stub_driver: StubDriver,
) -> None:
    """Building a page with a driver performs no operation on it."""
    page = ProbePage(stub_driver)

    assert stub_driver.calls == []
    assert page.driver is stub_driver


def test_the_driver_argument_is_accepted_positionally_and_by_keyword(
    stub_driver: StubDriver,
) -> None:
    """``LoginPage(stub)`` and ``LoginPage(driver=stub)`` are equally valid.

    Both spellings are in use: ``tests/conftest.py``'s
    ``FakeContext.attach_page`` constructs positionally, and the module under
    test documents ``LoginPage(driver=stub)`` as the injection seam the
    ``tests/test_steps_<area>.py`` parity modules drive their pages through.
    So the parameter must stay plain positional-or-keyword - neither
    positional-only nor keyword-only.
    """
    positional = ProbePage(stub_driver)
    keyword = ProbePage(driver=stub_driver)

    assert positional.driver is stub_driver
    assert keyword.driver is stub_driver
    assert stub_driver.calls == []

    signature = inspect.signature(BasePage.__init__)
    parameter = signature.parameters["driver"]
    assert parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert parameter.default is None


def test_construction_without_a_driver_does_not_consult_get_driver(
    monkeypatch: pytest.MonkeyPatch,
    stub_driver: StubDriver,
) -> None:
    """``ProbePage()`` asks for no session, so importing and building is safe.

    This is the assertion that keeps a page object constructible on a machine
    with no browser: the Java constructor's ``Driver.getDriver()`` call is
    deferred to attribute-access time, and deferring it is only observable as
    "the session was not requested".
    """
    source = DriverSource(stub_driver)
    monkeypatch.setattr(base_page_module, "get_driver", source)

    page = ProbePage()

    assert source.calls == 0
    assert stub_driver.calls == []
    assert page._driver is None


def test_construction_stores_the_argument_and_nothing_else(
    stub_driver: StubDriver,
) -> None:
    """The constructor's whole effect is one attribute.

    Asserted as the *exact* instance state, so an added cache dict, a captured
    driver or a memo slot fails here rather than at the behaviour it would
    later change.
    """
    page = ProbePage(stub_driver)

    assert set(vars(page)) == {"_driver"}
    assert vars(page)["_driver"] is stub_driver


def test_reading_a_locator_constant_performs_no_lookup(
    stub_driver: StubDriver,
) -> None:
    """The constant is data; only the lower-case accessor reaches the DOM.

    Both access forms are required by ``app/automation/waits.py``, which
    exposes ``wait_visible(locator, timeout)`` alongside
    ``wait_visible_element(element, timeout)``, and the pair coexists only
    because reading the upper-case name costs nothing.
    """
    page = ProbePage(stub_driver)

    assert ProbePage.INPUT_EMAIL == (By.NAME, "login")
    assert page.INPUT_EMAIL == (By.NAME, "login")
    assert page.ROWS == (By.CLASS_NAME, "o_kanban_record")
    assert stub_driver.calls == []


def test_declaring_a_page_class_performs_no_lookup(stub_driver: StubDriver) -> None:
    """``__init_subclass__`` installs properties; it resolves nothing.

    The class statement is the moment ``PageFactory.initElements``
    corresponds to, and it is also the moment a naive implementation would
    resolve every field.
    """
    page_class = type(
        "ClassCreationProbePage",
        (BasePage,),
        {"TARGET": (By.ID, "target"), "__doc__": "Declared inside a test."},
    )

    assert dict(page_class.LOCATORS) == {"TARGET": (By.ID, "target")}
    assert stub_driver.calls == []


def test_the_inert_construction_assertion_detects_an_eager_page(
    stub_driver: StubDriver,
) -> None:
    """Sensitivity: the same assertion fails for a page that resolves eagerly.

    :class:`EagerProbePage` is AAP 0.3.3's defect made concrete.  Constructing
    it produces exactly the ``find_element`` entry that
    :func:`test_construction_with_an_injected_driver_touches_nothing` asserts
    the absence of, so that test is measuring something real.
    """
    page = EagerProbePage(stub_driver)

    assert stub_driver.operations() == ("find_element",)
    assert stub_driver.calls_of("find_element") == ((By.NAME, "login"),)
    assert page.resolved_at_construction is not None


# =========================================================================== #
# 2. Laziness, and the absence of caching
#
# The single most load-bearing behaviour in the module: ``grep -rn CacheLookup``
# over the reference returns zero hits, so every ``@FindBy`` proxy in the suite
# re-locates on each use and so must every accessor here.
# =========================================================================== #


def test_three_accesses_produce_three_singular_lookups(
    stub_driver: StubDriver,
) -> None:
    """Each read of an accessor is its own ``find_element`` call."""
    page = ProbePage(stub_driver)

    first = page.input_email
    second = page.input_email
    third = page.input_email

    assert stub_driver.count_of("find_element") == 3
    assert stub_driver.calls_of("find_element") == (
        (By.NAME, "login"),
        (By.NAME, "login"),
        (By.NAME, "login"),
    )
    # The recorder hands out a stable element per locator, so identity says
    # nothing about caching - the call count is what does.  Asserting the
    # identity anyway records that the lookups reached the driver rather than
    # a page-held copy.
    assert first is second is third


def test_three_accesses_produce_three_plural_lookups(
    stub_driver: StubDriver,
) -> None:
    """A plural accessor is no more cached than a singular one.

    The port of ``SalesP.java:69``'s ``List<WebElement>``: the list is rebuilt
    from the live DOM on every access, which is why a scenario may read it
    before and after a filter and see different lengths.
    """
    stub_driver.set_element_count((By.CLASS_NAME, "o_kanban_record"), 2)
    page = ProbePage(stub_driver)

    for _ in range(3):
        assert len(page.rows) == 2

    assert stub_driver.count_of("find_elements") == 3
    assert stub_driver.count_of("find_element") == 0
    assert stub_driver.calls_of("find_elements") == (
        (By.CLASS_NAME, "o_kanban_record"),
        (By.CLASS_NAME, "o_kanban_record"),
        (By.CLASS_NAME, "o_kanban_record"),
    )


def test_accessing_an_accessor_stores_nothing_on_the_instance(
    stub_driver: StubDriver,
) -> None:
    """No memo appears in the instance dictionary, at any point.

    ``functools.cached_property`` and hand-rolled memoization both work by
    writing the resolved value into ``vars(instance)``, so the instance's own
    state is where such a cache would be visible.
    """
    page = ProbePage(stub_driver)

    page.input_email
    page.rows
    page.button

    assert set(vars(page)) == {"_driver"}


def test_two_instances_sharing_a_driver_each_resolve(
    stub_driver: StubDriver,
) -> None:
    """Resolution is per access, not per class and not per process.

    A class-level cache would show up here as one lookup for two reads.
    """
    first_page = ProbePage(stub_driver)
    second_page = ProbePage(stub_driver)

    first_page.input_email
    second_page.input_email

    assert stub_driver.count_of("find_element") == 2


def test_lookups_and_element_operations_interleave_in_order(
    stub_driver: StubDriver,
) -> None:
    """The ordered log shows a lookup immediately before each operation.

    The sequence a step body produces, and the shape the ten parity modules
    assert against their Java originals: resolve, act, resolve, act - never
    resolve once and act twice.
    """
    page = ProbePage(stub_driver)

    page.input_email.send_keys("posmanager50@info.com")
    page.button.click()

    assert stub_driver.operations() == (
        "find_element",
        ELEMENT_SEND_KEYS,
        "find_element",
        ELEMENT_CLICK,
    )
    assert stub_driver.calls_of(ELEMENT_SEND_KEYS) == (
        ((By.NAME, "login"), "posmanager50@info.com"),
    )
    assert stub_driver.calls_of(ELEMENT_CLICK) == (((By.XPATH, "//button[.='Log in']"),),)


def test_the_no_caching_assertion_detects_a_caching_accessor(
    stub_driver: StubDriver,
) -> None:
    """Sensitivity: three reads of a memoizing accessor produce one lookup.

    The two measurements are taken with the same helper on the same recorder,
    so the difference between 3 and 1 is the whole of the evidence that the
    counting technique used throughout this section can fail.
    """
    correct_page = ProbePage(stub_driver)
    broken_page = CachingProbePage(stub_driver)

    assert lookup_count(stub_driver, correct_page, "input_email", 3) == 3
    assert lookup_count(stub_driver, broken_page, "cached_target", 3) == 1


# =========================================================================== #
# 3. The naming transform, and what an accessor may not shadow
# =========================================================================== #


@pytest.mark.parametrize(
    "constant",
    ["INPUT_EMAIL", "BUTTON", "ROWS"],
)
def test_each_constant_installs_a_read_only_property_under_its_lower_name(
    constant: str,
    stub_driver: StubDriver,
) -> None:
    """``INPUT_EMAIL`` -> ``input_email``, as a property with no setter.

    Read-only is the contract rather than a detail: assigning to the accessor
    has to fail loudly, because silently replacing the lookup with whatever was
    assigned would turn a later failure into a mystery.
    """
    accessor_name = constant.lower()
    accessor = inspect.getattr_static(ProbePage, accessor_name)

    assert isinstance(accessor, property)
    assert accessor.fset is None
    assert accessor.fdel is None
    assert accessor.__doc__
    assert constant in accessor.__doc__

    page = ProbePage(stub_driver)
    with pytest.raises(AttributeError):
        setattr(page, accessor_name, object())


def test_the_accessor_carries_a_legible_qualified_name() -> None:
    """The property's getter names the page it belongs to.

    ``ProbePage.input_email`` rather than ``accessor``, so a traceback through
    a failed lookup says which page and which locator was being resolved.
    """
    accessor = inspect.getattr_static(ProbePage, "input_email")

    assert accessor.fget is not None
    assert accessor.fget.__name__ == "input_email"
    assert accessor.fget.__qualname__ == "ProbePage.input_email"


def test_constant_and_accessor_coexist_without_collision(
    stub_driver: StubDriver,
) -> None:
    """Case is the whole of what separates the tuple from the element.

    A Java step class reached both shapes off one field - ``loginP.inputEmail``
    for the element, ``By.name(...)`` for a locator - and the port keeps both
    reachable at once.
    """
    page = ProbePage(stub_driver)

    assert page.INPUT_EMAIL == (By.NAME, "login")
    assert isinstance(page.input_email, object)
    assert stub_driver.calls_of("find_element") == ((By.NAME, "login"),)


@pytest.mark.parametrize("protected", ["DRIVER", "FIND", "FIND_ALL"])
def test_a_constant_shadowing_the_mechanism_is_rejected(protected: str) -> None:
    """``DRIVER``, ``FIND`` and ``FIND_ALL`` cannot become accessors.

    Each would replace the very mechanism its own lookup depends on, and the
    resulting failure would surface far from its cause - so it is refused at
    class-creation time, which is the last moment before any instance exists.
    """
    with pytest.raises(ValueError, match="shadows an existing attribute") as error:
        type(
            "ShadowMechanismProbePage",
            (BasePage,),
            {protected: (By.ID, "x"), "__doc__": "Declared inside a test."},
        )

    message = str(error.value)
    assert protected in message
    assert repr(protected.lower()) in message


def test_a_constant_shadowing_a_class_body_method_is_rejected() -> None:
    """A page may not declare both ``LOGIN`` and ``login``.

    The reference's only page method is ``EmployeeP.login()``
    (``EmployeeP.java:59-69``), and accessors are installed *after* the class
    body has run, so without this guard the constant would silently delete the
    method.  ``EmployeePage`` declares ``INPUT_LOGIN`` rather than ``LOGIN``
    precisely because of it.
    """

    def login(self: Any) -> Any:
        return self.find(self.LOGIN).click()

    with pytest.raises(ValueError, match="shadows an existing attribute") as error:
        type(
            "ShadowMethodProbePage",
            (BasePage,),
            {
                "LOGIN": (By.ID, "login"),
                "login": login,
                "__doc__": "Declared inside a test.",
            },
        )

    assert "LOGIN" in str(error.value)


@pytest.mark.parametrize(
    ("name", "strategy"),
    ALL_BY_STRATEGIES,
    ids=[name for name, _ in ALL_BY_STRATEGIES],
)
def test_every_by_strategy_is_recognised(
    name: str,
    strategy: str,
    stub_driver: StubDriver,
) -> None:
    """All eight strategies work, not just the five the reference uses.

    The reference's 131 ``@FindBy`` declarations name five strategies -
    ``xpath`` 96 times, ``partial link text`` 12, ``name`` 8, ``id`` 8 and
    ``class name`` 7 - and never the remaining three.  The mechanism is
    strategy-agnostic all the same, so an implementation that special-cased
    the five in use fails here.
    """
    page_class = type(
        f"{name.title().replace('_', '')}ProbePage",
        (BasePage,),
        {"TARGET": (strategy, "value"), "__doc__": "Declared inside a test."},
    )
    page = page_class(stub_driver)

    assert dict(page_class.LOCATORS) == {"TARGET": (strategy, "value")}
    assert page.target is not None
    assert stub_driver.calls_of("find_element") == ((strategy, "value"),)


def test_the_strategy_set_is_exactly_the_eight_by_members() -> None:
    """The recogniser's strategy set is the ``By`` surface, in full."""
    assert base_page_module._LOCATOR_STRATEGIES == frozenset(
        strategy for _, strategy in ALL_BY_STRATEGIES
    )


# =========================================================================== #
# 4. PLURAL_LOCATORS
#
# The port of a Java ``List<WebElement>`` field.  The reference declares
# exactly one in the whole suite (``SalesP.java:69``), so this is the mechanism
# with a single real consumer - and the one most likely to rot unnoticed.
# =========================================================================== #


def test_base_page_declares_no_plural_locators() -> None:
    """``BasePage.PLURAL_LOCATORS`` is an empty frozenset."""
    assert BasePage.PLURAL_LOCATORS == frozenset()
    assert isinstance(BasePage.PLURAL_LOCATORS, frozenset)


def test_a_listed_constant_resolves_through_find_all(
    stub_driver: StubDriver,
) -> None:
    """A plural constant routes to ``find_elements`` and yields a list."""
    stub_driver.set_element_count((By.CLASS_NAME, "o_kanban_record"), 4)
    page = ProbePage(stub_driver)

    rows = page.rows

    assert isinstance(rows, list)
    assert len(rows) == 4
    assert stub_driver.operations() == ("find_elements",)


def test_an_unlisted_constant_stays_singular(stub_driver: StubDriver) -> None:
    """Everything not named in ``PLURAL_LOCATORS`` resolves to one element."""
    page = ProbePage(stub_driver)

    page.input_email

    assert stub_driver.operations() == ("find_element",)


def test_a_plural_lookup_matching_nothing_yields_an_empty_list(
    stub_driver: StubDriver,
) -> None:
    """Nothing matching is not an error for a plural accessor.

    The ``find_elements`` contract, and the Java ``List<WebElement>`` proxy's:
    an empty list comes back and no exception is raised.  The singular form
    behaves oppositely, which is why the two are separate call sites.
    """
    stub_driver.set_element_count((By.CLASS_NAME, "o_kanban_record"), 0)
    page = ProbePage(stub_driver)

    assert page.rows == []
    assert stub_driver.count_of("find_elements") == 1


def test_plural_locators_naming_an_undeclared_constant_is_rejected() -> None:
    """A mistyped plural name fails at class creation, naming the mistake.

    Without the check the accessor would quietly become singular and the
    failure would surface much later, in a step that expected a list.
    """
    with pytest.raises(ValueError) as error:
        type(
            "BadPluralProbePage",
            (BasePage,),
            {
                "PLURAL_LOCATORS": frozenset({"MISSING", "ROWS"}),
                "ROWS": (By.CLASS_NAME, "row"),
                "__doc__": "Declared inside a test.",
            },
        )

    message = str(error.value)
    assert "BadPluralProbePage.PLURAL_LOCATORS" in message
    assert "MISSING" in message
    # The message also lists what *is* declared, which is what makes it
    # actionable for a typo such as ROW/ROWS.
    assert "ROWS" in message


def test_plural_locators_accepts_a_plain_set_literal(
    stub_driver: StubDriver,
) -> None:
    """Any iterable of names is accepted; membership is what matters.

    ``app/pages/sales_page.py`` writes ``frozenset({"ALL_CUSTOMERS"})``, and a
    page written with a ``set`` literal must behave identically rather than
    silently losing its plurality.
    """
    page_class = type(
        "SetLiteralPluralProbePage",
        (BasePage,),
        {
            "PLURAL_LOCATORS": {"ROWS"},
            "ROWS": (By.CLASS_NAME, "row"),
            "__doc__": "Declared inside a test.",
        },
    )
    page = page_class(stub_driver)

    assert isinstance(page.rows, list)
    assert stub_driver.operations() == ("find_elements",)


def test_plural_locators_may_be_declared_after_the_constants(
    stub_driver: StubDriver,
) -> None:
    """Position in the class body is irrelevant to the plural declaration.

    ``app/pages/sales_page.py`` declares ``PLURAL_LOCATORS`` above its
    constants, but the mechanism reads it through the class *after* the body
    has run, so a page written the other way round behaves identically.
    Stated as a test because the alternative implementation - consulting the
    namespace as it is being built - would work for ``SalesPage`` and fail
    here.
    """
    page_class = type(
        "TrailingPluralProbePage",
        (BasePage,),
        {
            "ROWS": (By.CLASS_NAME, "row"),
            "PLURAL_LOCATORS": frozenset({"ROWS"}),
            "__doc__": "Declared inside a test.",
        },
    )
    page = page_class(stub_driver)

    assert isinstance(page.rows, list)
    assert stub_driver.operations() == ("find_elements",)


def test_plurality_is_inherited_along_with_the_constant(
    stub_driver: StubDriver,
) -> None:
    """A subclass declaring nothing keeps both the locator and its plurality.

    ``__init_subclass__`` reads ``cls.PLURAL_LOCATORS`` through the class
    rather than out of the class body, which is the only reading that makes
    this case work.
    """
    page = PluralDescendantProbePage(stub_driver)

    assert PluralDescendantProbePage.PLURAL_LOCATORS == frozenset({"ROWS"})
    assert isinstance(page.rows, list)
    assert stub_driver.operations() == ("find_elements",)


def test_plural_locators_is_not_mistaken_for_a_locator_constant() -> None:
    """The reserved names never appear in ``LOCATORS``.

    ``PLURAL_LOCATORS`` is upper-case and lives in a class body, so shape
    alone would admit it if it ever held a two-string tuple.
    """
    assert "PLURAL_LOCATORS" not in ProbePage.LOCATORS
    assert "LOCATORS" not in ProbePage.LOCATORS
    assert base_page_module._RESERVED_CLASS_ATTRIBUTES == frozenset(
        {"LOCATORS", "PLURAL_LOCATORS"}
    )


# =========================================================================== #
# 5. LOCATORS - complete, ordered, immutable, and merged under inheritance
# =========================================================================== #


def test_base_page_itself_declares_an_empty_immutable_inventory() -> None:
    """``BasePage`` is mechanism: it owns no locator of its own."""
    assert dict(BasePage.LOCATORS) == {}
    assert isinstance(BasePage.LOCATORS, MappingProxyType)


def test_locators_holds_every_constant_in_declaration_order() -> None:
    """Nothing is sorted, so the mapping reads in class-body order.

    This is the property ``tests/test_pages.py`` relies on to compare a page
    against the ``@FindBy`` order of the Java class it ports, so it is pinned
    here on a page whose order this module controls.
    """
    assert tuple(ProbePage.LOCATORS) == ("INPUT_EMAIL", "BUTTON", "ROWS")
    assert dict(ProbePage.LOCATORS) == {
        "INPUT_EMAIL": (By.NAME, "login"),
        "BUTTON": (By.XPATH, "//button[.='Log in']"),
        "ROWS": (By.CLASS_NAME, "o_kanban_record"),
    }


def test_locators_is_a_read_only_mapping_proxy() -> None:
    """A caller enumerating the inventory cannot alter it."""
    assert isinstance(ProbePage.LOCATORS, MappingProxyType)

    with pytest.raises(TypeError):
        ProbePage.LOCATORS["NEW"] = (By.ID, "new")  # type: ignore[index]

    with pytest.raises(TypeError):
        del ProbePage.LOCATORS["INPUT_EMAIL"]  # type: ignore[attr-defined]

    assert tuple(ProbePage.LOCATORS) == ("INPUT_EMAIL", "BUTTON", "ROWS")


def test_every_locator_value_is_a_two_string_tuple() -> None:
    """The inventory's value type is uniform, which is why lists are refused."""
    for name, locator in ProbePage.LOCATORS.items():
        assert isinstance(locator, tuple), name
        assert len(locator) == 2, name
        assert all(isinstance(part, str) for part in locator), name


@pytest.mark.parametrize(
    "rejected",
    ["TITLE_PAIR", "TRIPLE", "AS_LIST", "NOT_A_TUPLE", "MIXED_PAIR", "_PRIVATE"],
)
def test_a_near_miss_declaration_acquires_no_accessor(rejected: str) -> None:
    """Six shapes that are not locators, and gain nothing.

    The recogniser needs all four of its conditions at once, and this is what
    each one buys: an incidental pair of strings - a pair of expected titles,
    say - must not acquire an element accessor it was never meant to have.
    """
    assert rejected not in RecogniserProbePage.LOCATORS

    # ``getattr_static`` with a default reports the *declared* attribute
    # without invoking a descriptor, so the check is unconditional: whether
    # the lower-case name is absent entirely or present as the raw value, the
    # one thing it must never be is a property.
    accessor = inspect.getattr_static(RecogniserProbePage, rejected.lower(), None)

    assert not isinstance(accessor, property)


def test_the_recogniser_accepts_exactly_the_two_real_constants() -> None:
    """Nine declarations, two locators - including the digit-bearing name.

    ``DIGITS2`` stands in for ``CrmP.progressPipeline2`` ->
    ``PROGRESS_PIPELINE2``, the reference's only digit-bearing field:
    ``str.isupper()`` ignores digits and underscores, so it qualifies.
    """
    assert dict(RecogniserProbePage.LOCATORS) == {
        "REAL": (By.ID, "real"),
        "DIGITS2": (By.ID, "digits"),
    }


def test_the_lower_case_attribute_is_left_exactly_as_declared() -> None:
    """A lower-case tuple stays a tuple; it is not turned into an accessor."""
    assert RecogniserProbePage.lower_case == (By.ID, "lower")
    assert not isinstance(
        inspect.getattr_static(RecogniserProbePage, "lower_case"), property
    )


@pytest.mark.parametrize(
    ("name", "value", "expected"),
    [
        ("INPUT_EMAIL", (By.NAME, "login"), True),
        ("PROGRESS_PIPELINE2", (By.XPATH, "//div[@data-id='2']/div[2]"), True),
        ("LOCATORS", (By.ID, "x"), False),
        ("PLURAL_LOCATORS", (By.ID, "x"), False),
        ("_PRIVATE", (By.ID, "x"), False),
        ("lower", (By.ID, "x"), False),
        ("PAIR", ("not-a-strategy", "x"), False),
        ("LIST", [By.ID, "x"], False),
        ("TRIPLE", (By.ID, "x", "y"), False),
        ("SINGLE", (By.ID,), False),
        ("NUMERIC", (By.ID, 7), False),
        ("TEXT", "text", False),
    ],
    ids=[
        "upper-snake-pair",
        "digit-bearing-name",
        "reserved-LOCATORS",
        "reserved-PLURAL_LOCATORS",
        "leading-underscore",
        "lower-case-name",
        "unknown-strategy",
        "list-not-tuple",
        "three-elements",
        "one-element",
        "non-string-value",
        "not-a-tuple",
    ],
)
def test_is_locator_declaration_decides_each_shape(
    name: str,
    value: object,
    expected: bool,
) -> None:
    """The recogniser, exercised directly on every shape it must judge.

    Tested at the function as well as through class creation: the four
    conditions are what keep ``LOCATORS`` honest, and a change to any one of
    them should fail at the condition rather than three tests away.
    """
    assert base_page_module._is_locator_declaration(name, value) is expected


def test_inheritance_merges_ancestors_first_then_the_class_body() -> None:
    """Ancestors oldest-first, own constants after, re-declarations in place.

    The measured behaviour, stated exactly: ``DescendantProbePage`` declares
    ``THIRD`` then re-declares ``SECOND``, and the inventory reads
    ``FIRST``, ``SECOND``, ``THIRD`` - because merging the ancestor's mapping
    first fixes ``SECOND``'s *position*, while the class body's own entry
    replaces its *value*.  Ordinary attribute lookup gives the same winner,
    and the ancestor's own inventory is untouched.
    """
    assert tuple(AncestorProbePage.LOCATORS) == ("FIRST", "SECOND")
    assert tuple(DescendantProbePage.LOCATORS) == ("FIRST", "SECOND", "THIRD")

    assert DescendantProbePage.LOCATORS["SECOND"] == (By.CLASS_NAME, "second-in-descendant")
    assert AncestorProbePage.LOCATORS["SECOND"] == (By.NAME, "second")


def test_an_inherited_constant_resolves_through_its_own_class_value(
    stub_driver: StubDriver,
) -> None:
    """The re-declared accessor resolves the subclass's locator, not the base's.

    The locator is captured by closure when the property is built, so a
    subclass gets a *fresh* property for its own value - and this is the test
    that would fail if the closure captured the ancestor's.
    """
    page = DescendantProbePage(stub_driver)

    page.second
    page.first
    page.third

    assert stub_driver.calls_of("find_element") == (
        (By.CLASS_NAME, "second-in-descendant"),
        (By.ID, "first"),
        (By.XPATH, "//third"),
    )


def test_each_subclass_gets_its_own_inventory_object() -> None:
    """No two classes share a mapping, so one cannot leak into another."""
    assert ProbePage.LOCATORS is not BasePage.LOCATORS
    assert DescendantProbePage.LOCATORS is not AncestorProbePage.LOCATORS
    assert dict(BasePage.LOCATORS) == {}


# =========================================================================== #
# 6. The driver seam, and the two lookup funnels
# =========================================================================== #


def test_the_injected_driver_wins_over_the_current_session(
    monkeypatch: pytest.MonkeyPatch,
    stub_driver: StubDriver,
) -> None:
    """Injection is the preferred seam, and it is consulted first."""
    other = StubDriver()
    source = DriverSource(other)
    monkeypatch.setattr(base_page_module, "get_driver", source)

    page = ProbePage(stub_driver)

    assert page.driver is stub_driver
    assert source.calls == 0


def test_without_injection_the_session_is_read_on_every_access(
    monkeypatch: pytest.MonkeyPatch,
    stub_driver: StubDriver,
) -> None:
    """Per access, not once - because the session is replaced per scenario.

    ``features/environment.py`` quits the session after each scenario and
    creates a fresh one for the next, so a page object that had captured the
    driver would hand its locators to a dead session.
    """
    source = DriverSource(stub_driver)
    monkeypatch.setattr(base_page_module, "get_driver", source)

    page = ProbePage()

    assert page.driver is stub_driver
    assert page.driver is stub_driver
    assert source.calls == 2

    page.input_email

    assert source.calls == 3
    assert stub_driver.calls_of("find_element") == ((By.NAME, "login"),)


def test_a_none_driver_is_passed_straight_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``get_driver()`` may hand back ``None``, and it is not second-guessed.

    ``Driver.java:29-42`` has no default branch, so an unrecognised
    ``browser`` value leaves the slot empty and ``Driver.java:45`` returns that
    ``null``.  AAP 0.4.1 keeps the consequence - an unknown browser *"fails at
    first driver use, as today"* - so the property must not substitute a
    driver, raise a friendlier error, or read configuration.
    """
    source = DriverSource(None)
    monkeypatch.setattr(base_page_module, "get_driver", source)

    page = ProbePage()

    assert page.driver is None
    with pytest.raises(AttributeError):
        page.input_email


def test_an_explicit_none_argument_falls_back_to_the_current_session(
    monkeypatch: pytest.MonkeyPatch,
    stub_driver: StubDriver,
) -> None:
    """``ProbePage(None)`` behaves exactly like ``ProbePage()``."""
    source = DriverSource(stub_driver)
    monkeypatch.setattr(base_page_module, "get_driver", source)

    page = ProbePage(None)

    assert page.driver is stub_driver
    assert source.calls == 1


def test_a_falsy_injected_driver_is_still_used(
    monkeypatch: pytest.MonkeyPatch,
    stub_driver: StubDriver,
) -> None:
    """The property tests ``is not None``, never truthiness.

    The constructor stores its argument unnormalised precisely so this holds,
    and ``if self._driver:`` - the plausible alternative spelling - would fail
    here by falling through to the current session.
    """
    source = DriverSource(stub_driver)
    monkeypatch.setattr(base_page_module, "get_driver", source)

    falsy = FalsyProbeDriver()
    page = ProbePage(falsy)

    assert bool(falsy) is False
    assert page.driver is falsy

    element = page.input_email

    assert element.locator == (By.NAME, "login")
    assert falsy.lookups == [(By.NAME, "login")]
    assert source.calls == 0
    assert stub_driver.calls == []


def test_the_substitutable_name_is_the_binding_in_base_page(
    monkeypatch: pytest.MonkeyPatch,
    stub_driver: StubDriver,
) -> None:
    """Patching ``app.automation.get_driver`` does not reach this module.

    AAP 0.4.2 fixes the import form as ``from app.automation import ...``, so
    ``base_page`` holds its own binding and that binding is the seam.  Both
    names are substituted here - one per module - and the test asserts which
    of the two was consulted, so it can never accidentally reach the real
    ``get_driver`` and start a browser.
    """
    import app.automation as automation

    package_source = DriverSource(StubDriver())
    module_source = DriverSource(stub_driver)
    monkeypatch.setattr(automation, "get_driver", package_source)
    monkeypatch.setattr(base_page_module, "get_driver", module_source)

    page = ProbePage()

    assert page.driver is stub_driver
    assert module_source.calls == 1
    assert package_source.calls == 0


def test_find_unpacks_the_locator_strategy_first(stub_driver: StubDriver) -> None:
    """``find_element(*locator)`` - strategy, then value, in that order."""
    page = ProbePage(stub_driver)

    page.find((By.NAME, "login"))

    assert stub_driver.calls_of("find_element") == ((By.NAME, "login"),)


def test_find_all_unpacks_the_locator_strategy_first(
    stub_driver: StubDriver,
) -> None:
    """``find_elements(*locator)`` unpacks in the same order."""
    page = ProbePage(stub_driver)

    page.find_all((By.CLASS_NAME, "o_kanban_record"))

    assert stub_driver.calls_of("find_elements") == ((By.CLASS_NAME, "o_kanban_record"),)


def test_the_argument_order_assertion_detects_a_reversed_unpack(
    stub_driver: StubDriver,
) -> None:
    """Sensitivity: a page that swaps the pair is caught by the same assertion.

    :class:`ReversedProbePage` calls the driver exactly once per access, so
    every call-count assertion in this module still passes for it.  Only the
    recorded arguments give it away.
    """
    page = ReversedProbePage(stub_driver)

    page.target

    assert stub_driver.count_of("find_element") == 1
    assert stub_driver.calls_of("find_element") == (("login", By.NAME),)
    assert stub_driver.calls_of("find_element") != ((By.NAME, "login"),)


def test_find_may_be_called_with_an_ad_hoc_locator(
    stub_driver: StubDriver,
) -> None:
    """A step may look up a locator the page never declared.

    The port of a Java step that built a ``By`` inline rather than using a page
    field (``LoginSD.java:56``), which is why ``find`` is public.
    """
    page = ProbePage(stub_driver)

    page.find((By.NAME, "password"))

    assert stub_driver.calls_of("find_element") == ((By.NAME, "password"),)
    assert "PASSWORD" not in ProbePage.LOCATORS


def test_find_all_returns_the_drivers_list_unchanged(
    stub_driver: StubDriver,
) -> None:
    """No copying, no filtering, no sorting - the list comes back as it is."""
    locator = (By.CLASS_NAME, "o_kanban_record")
    stub_driver.set_element_count(locator, 3)
    page = ProbePage(stub_driver)

    elements = page.find_all(locator)

    assert isinstance(elements, list)
    assert len(elements) == 3
    assert elements == stub_driver.find_elements(*locator)


def test_a_lookup_failure_propagates_unwrapped(stub_driver: StubDriver) -> None:
    """Nothing is caught, wrapped, retried or logged.

    A missing element must surface as the driver's own exception after the
    implicit-wait window, exactly as the Java proxy raises it - and no accessor
    may return ``None`` in its place, because a caller that got ``None`` would
    fail later, somewhere that no longer names the locator.
    """

    class ProbeLookupError(Exception):
        """Stands in for ``NoSuchElementException`` without importing it."""

    failure = ProbeLookupError("no such element: (name, login)")
    stub_driver.set_find_error((By.NAME, "login"), failure)
    page = ProbePage(stub_driver)

    with pytest.raises(ProbeLookupError) as error:
        page.input_email

    assert error.value is failure
    # The attempt is logged before the failure is raised, and nothing is
    # retried: exactly one lookup for one access.
    assert stub_driver.count_of("find_element") == 1
    assert set(vars(page)) == {"_driver"}


def test_a_plural_lookup_failure_propagates_unwrapped(
    stub_driver: StubDriver,
) -> None:
    """``find_all`` is no more forgiving than ``find``.

    An empty result is not an error, but a *failed* lookup is, and the plural
    funnel must not convert one into the other by returning ``[]``.
    """

    class ProbePluralLookupError(Exception):
        """Stands in for a driver-side failure during ``find_elements``."""

    stub_driver.set_find_error((By.CLASS_NAME, "o_kanban_record"), ProbePluralLookupError)
    page = ProbePage(stub_driver)

    with pytest.raises(ProbePluralLookupError):
        page.rows

    assert stub_driver.count_of("find_elements") == 1


# =========================================================================== #
# 7. The import boundary and the source guarantees
#
# Everything in this section is an assertion about the module's *text*, made
# through its syntax tree, and each one is paired with a negative test that
# feeds the same helper source which violates it.
# =========================================================================== #


def test_the_module_exports_only_base_page() -> None:
    """``__all__`` is exactly ``["BasePage"]``.

    The ``Locator`` alias exists to make the signatures legible and is
    deliberately not exported, the same way ``app/automation/waits.py`` keeps
    its own alias out.
    """
    assert base_page_module.__all__ == ["BasePage"]
    assert "Locator" not in base_page_module.__all__


def test_the_import_surface_is_exactly_the_five_authorized_names() -> None:
    """Three standard-library names and the two ``app.automation`` re-exports.

    AAP 0.4.2: *"Page objects import ``app.automation`` for the current
    driver, nothing else."*  Asserted as equality so an addition - a service, a
    path helper, ``app.config`` - fails as loudly as a browser-library import.
    """
    assert imported_names(BASE_PAGE_SOURCE) == EXPECTED_BASE_PAGE_IMPORTS


def test_the_module_imports_no_browser_automation_library() -> None:
    """No selenium import of any kind, guarded or otherwise.

    Only ``app.automation`` may import it, which is what lets the unit suite
    import every page module on a host with no browser installed.
    """
    modules = imported_modules(BASE_PAGE_SOURCE)

    assert not any(module.split(".")[0] == "selenium" for module in modules)
    assert "selenium" not in modules


@pytest.mark.parametrize(
    ("label", "source"),
    [
        ("direct", BAD_DIRECT_SELENIUM_IMPORT),
        ("type-checking-guarded", BAD_GUARDED_SELENIUM_IMPORT),
    ],
)
def test_the_import_check_detects_a_browser_library_import(
    label: str,
    source: str,
) -> None:
    """Sensitivity: both spellings of the violation are seen.

    The guarded form matters because a ``TYPE_CHECKING`` block never executes,
    so a runtime probe would miss it - and the module under test refuses even
    that form deliberately.
    """
    modules = imported_modules(source)

    assert any(module.split(".")[0] == "selenium" for module in modules), label
    assert modules != EXPECTED_BASE_PAGE_IMPORTS


def test_the_module_imports_no_application_layer_above_it() -> None:
    """No configuration, service, reporting, web or Gherkin-engine import.

    Page objects never read configuration - step modules do, which is where
    the Java classes read it - and nothing under ``app/pages`` may import a
    service, a reporting writer or ``app.web``.
    """
    forbidden = ("app.config", "app.services", "app.reporting", "app.web", "behave", "flask")
    modules = imported_modules(BASE_PAGE_SOURCE)

    for module in forbidden:
        assert module not in modules


def test_the_module_memoizes_nothing_and_waits_nowhere() -> None:
    """None of the memoization or waiting names appears in the module's code.

    The module's docstring discusses every one of them by name, which is why
    this is a syntax-tree check: a grep would flag the prose that explains why
    they are absent.
    """
    referenced = referenced_code_names(BASE_PAGE_SOURCE)

    assert not (referenced & FORBIDDEN_CODE_NAMES)


@pytest.mark.parametrize(
    ("label", "source", "expected"),
    [
        ("cached_property", BAD_CACHED_PROPERTY, "cached_property"),
        ("time.sleep", BAD_SLEEPING_LOOKUP, "sleep"),
    ],
)
def test_the_code_name_check_detects_memoization_and_waiting(
    label: str,
    source: str,
    expected: str,
) -> None:
    """Sensitivity: the denylist catches both a cache and a sleep."""
    referenced = referenced_code_names(source)

    assert expected in referenced, label
    assert referenced & FORBIDDEN_CODE_NAMES


def test_there_is_exactly_one_lookup_call_site_of_each_kind() -> None:
    """One ``find_element`` call and one ``find_elements`` call in the module.

    "One funnel" is only a guarantee while it is one *place*: a second call
    site is a second place a cache, a wait or an exception handler could later
    appear, and no test of behaviour would notice it had.
    """
    assert method_call_sites(BASE_PAGE_SOURCE, "find_element") == 1
    assert method_call_sites(BASE_PAGE_SOURCE, "find_elements") == 1


def test_the_call_site_check_detects_a_second_funnel() -> None:
    """Sensitivity: two call sites are counted as two."""
    assert method_call_sites(BAD_SECOND_LOOKUP_CALL_SITE, "find_element") == 2


def test_importing_the_module_evaluates_only_constant_building_calls() -> None:
    """Import has no side effects: no session, no file, no logging.

    Every call the module makes at import time builds an immutable constant,
    which is what lets ``import app.pages.base_page`` succeed on a machine with
    neither a browser nor a ``configuration.properties``.
    """
    calls = set(import_time_call_names(BASE_PAGE_SOURCE))

    assert calls <= ALLOWED_IMPORT_TIME_CALLS
    assert "frozenset" in calls


def test_the_import_time_call_check_detects_a_module_level_driver_call() -> None:
    """Sensitivity: ``DRIVER = get_driver()`` at module level is seen."""
    calls = set(import_time_call_names(BAD_IMPORT_TIME_DRIVER_CALL))

    assert "get_driver" in calls
    assert not calls <= ALLOWED_IMPORT_TIME_CALLS


def test_the_import_time_call_check_ignores_calls_inside_methods() -> None:
    """A call in a method body is not an import-time call.

    Stated because the helper would be useless if it could not tell the two
    apart: ``BasePage.find`` calls the driver, and that is the behaviour, not a
    side effect of importing.
    """
    calls = import_time_call_names(BAD_SLEEPING_LOOKUP)

    assert calls == ()


# =========================================================================== #
# 8. The same mechanism, on a production declaration
#
# Every assertion above runs against a subclass declared in this file, which is
# what proves the mechanism independently of the ten real pages - a locator
# invented here cannot drift, so a failure is unambiguously the mechanism's.
# That independence has a cost worth paying off in two tests: nothing above
# would notice if ``__init_subclass__`` worked only on the shapes this file
# happens to declare.
#
# :class:`~app.pages.login_page.LoginPage` is the spot-check, chosen because it
# is the port of ``LoginP.java:13-32``, the class AAP 0.4.1 anchors
# ``app/pages/base_page.py`` itself on.  What is asserted here is still the
# *mechanism* - inert construction, a read-only accessor per constant, one
# lookup per access, the constant surviving beside its accessor.  The
# inventory - which locators, under which names, with which selector bytes -
# belongs to ``tests/test_pages.py`` and is deliberately not restated: this
# reads the names out of ``LoginPage.LOCATORS`` rather than naming seven of
# them, so the two modules cannot disagree.
# =========================================================================== #


def test_the_mechanism_works_on_a_production_page_class(
    stub_driver: StubDriver,
) -> None:
    """``LoginPage`` gets the whole contract, not a test-local approximation.

    Four properties of the mechanism, on a class this file did not declare:
    construction is inert; every constant in the class's own inventory has a
    read-only accessor under its lower-case name; the constant itself is still
    the plain ``(By.X, "value")`` tuple the automation layer's
    ``wait_visible(locator, timeout)`` needs; and three reads of one accessor
    perform three lookups with that exact pair.
    """
    page = LoginPage(stub_driver)

    assert stub_driver.calls == []

    for constant, locator in LoginPage.LOCATORS.items():
        accessor = inspect.getattr_static(LoginPage, constant.lower())

        assert isinstance(accessor, property)
        assert accessor.fset is None
        assert accessor.fdel is None
        # The class attribute is untouched by the property installed beside it.
        assert getattr(LoginPage, constant) == locator

    assert lookup_count(stub_driver, page, "input_email", 3) == 3
    assert set(stub_driver.calls_of("find_element")) == {LoginPage.INPUT_EMAIL}


def test_a_production_page_constructs_when_every_lookup_would_fail(
    stub_driver: StubDriver,
) -> None:
    """Construction cannot fail on a missing element, because it looks for none.

    The failure mode AAP 0.3.3 names - *"a Python page object that called
    ``find_element`` in ``__init__`` would change when elements are looked up
    and break scenarios that build a page before navigating"* - stated as the
    scenario that provokes it: a session on which **every** lookup raises.
    Building the page object is still safe, and the error arrives only when a
    step actually reads an element, which is the point at which the Java
    ``@FindBy`` proxy would have raised too.
    """

    class ProductionLookupError(Exception):
        """Stands in for ``NoSuchElementException`` without importing it."""

    failure = ProductionLookupError("no such element: the browser is on another page")
    # ``locator=None`` fails *every* lookup, so the page has no resolvable
    # element at all - the strongest form of the arrangement.
    stub_driver.set_find_error(None, failure)

    page = LoginPage(stub_driver)

    assert stub_driver.calls == []

    with pytest.raises(ProductionLookupError) as error:
        page.input_email

    assert error.value is failure
    assert stub_driver.calls_of("find_element") == (LoginPage.INPUT_EMAIL,)
