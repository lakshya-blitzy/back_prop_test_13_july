r"""The evidence module for ``app/automation/waits.py`` - explicit waits.

What is under test, and what it is held to
------------------------------------------
``app/automation/waits.py`` is the port of the **nine per-class
``WebDriverWait`` fields** of the Java reference suite at pinned revision
``47e9d697e4a9a85da889f94a846fdf47af28a240``: ``Calendar.java:15`` and
``Crm.java:18`` (2 s), ``LoginSD.java:17``, ``LogOutSD.java:13`` and
``EmployeeStage.java:14`` (3 s), ``Sales.java:17`` (4 s), and
``Contacts.java:15``, ``Inventory.java:13`` and ``Notes.java:19`` (20 s).
``Session.java`` constructs no wait at all, and that asymmetry is part of the
contract rather than an oversight.

Two AAP statements fix what this module may assert.  AAP 0.4.1's row for the
file requires *"Explicit waits with the timeout supplied per call site"* and
enumerates exactly those nine sites and their four timeout values; AAP 0.4.2
fixes the shape of the surface the package barrel exposes - *"the wait helpers
from waits.py, each taking an explicit timeout."*  Everything below is one of
those two sentences made executable, plus the six items the module under test
records for this file in its own docstring ("What ``tests/test_waits.py``
asserts").

The standing rule: no test here performs a real wait
----------------------------------------------------
Not one assertion in this module may depend on elapsed time, and nothing here
polls, sleeps, launches a browser or touches a network.  Three autouse seams
enforce that rather than trusting it:

* :fixture:`wait_recorder` replaces ``app.automation.waits.WebDriverWait`` - the
  name the module imports, which is why the patch lands there and not on
  ``selenium.webdriver.support.ui`` - with a recorder that answers a programmed
  value or raises a programmed exception the instant ``until`` is called.  No
  polling loop exists while it is installed.
* :fixture:`condition_spy` wraps every predicate factory the
  ``expected_conditions`` module defines with a recorder that logs the call and
  returns a :class:`ConditionSentinel`.  The module under test imports that
  module as ``EC`` and resolves ``EC.<name>`` per call, so patching the module's
  attributes is visible to it.  A sentinel raises if anything ever *evaluates*
  it, so an accidental real poll fails loudly instead of quietly waiting.
* :fixture:`driver_seam` replaces ``app.automation.waits.get_driver``, so no
  test can reach the real session lifecycle owner.

The module therefore runs in milliseconds, and its runtime is itself part of
the evidence: a wall-clock cost anywhere near a timeout value would mean one of
these seams had been bypassed.

``wait_visible_element`` is in flux, and is asserted differently
----------------------------------------------------------------
Review finding **F12 (HIGH, Timing Semantic Parity)** requires
``wait_visible_element`` to take a locator and resolve it *inside* the
predicate: today every one of the 42 visibility call sites pre-resolves its
element through a page property under the driver's ten-second implicit wait, so
the element lookup happens **before** the explicit wait is constructed and the
intended 2/3/4/20-second polling window is never applied to it.  The corrected
helper waits on a locator-based visibility condition instead.

So for that one helper this module asserts only what holds under both the
current and the corrected form - one predicate drawn from the closed pair
``visibility_of`` / ``visibility_of_element_located``, the locator it was
handed, the identity of the predicate that reached ``until``, and a single
resolved element coming back - and never the factory's exact name.  The pair is
closed rather than a family prefix because the plural visibility predicates
return a list, and a helper that returned one would break all 42 call sites
(see :data:`SINGLE_ELEMENT_VISIBILITY_PREDICATES`).  The invariant that
actually matters, and the one asserted for every locator-taking helper, is that
**the helper performs no lookup of its own**: the recorder handed to it records
nothing at all.  The eight settled helpers are pinned to the exact factory
their surface-table row names.

Reading conventions worth stating once
--------------------------------------
* ``inspect.signature`` is called **without** ``eval_str``/annotation
  evaluation.  ``waits.py`` quotes its ``TYPE_CHECKING``-only annotations
  deliberately (see its docstring, "Those quotation marks are load-bearing"),
  and resolving them would raise ``NameError`` and destroy the very seam that
  proves ``timeout`` carries no default.
* Importing selenium here is legitimate.  The no-selenium-import rule of AAP
  0.4.2 binds ``features/steps/`` and is asserted by
  ``tests/test_steps_registration.py``; this module needs the binding's real
  ``TimeoutException`` to prove an expiry propagates unaltered, and the real
  ``expected_conditions`` module because it is the spy's subject.
* The single-construction-site assertion counts **code**, not prose: the module
  docstring of ``waits.py`` quotes the Java form ``new WebDriverWait(driver,
  3)`` while explaining that seconds are not milliseconds, so a raw textual
  count over the whole file is two and would be red for a documented reason.
  :func:`_code_lines` drops every line inside a string literal before counting,
  and the authoritative count is an AST one over the port's own sources -
  ``tests/`` is excluded from that scan because this module names the class in
  its patch target and its own prose.
"""

from __future__ import annotations

import ast
import inspect
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any, Final, NamedTuple

import pytest
from conftest import StubDriver, step_modules_dir
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.support import expected_conditions as EC

from app import automation
from app.automation import waits

# =========================================================================== #
# The contract, as data
#
# Every expectation this module holds the production module to is a constant
# here, so that a reader can audit the contract without reading the assertions
# and so that no expectation is stated twice.
# =========================================================================== #

#: The nine helpers of the surface table in ``waits.py``, in the order that
#: table lists them.  The membership is closed: a tenth public ``wait_``
#: attribute is a surface change, not an addition, and the surface tests below
#: assert set equality rather than containment for exactly that reason.
HELPER_NAMES: Final[tuple[str, ...]] = (
    "wait_visible",
    "wait_visible_element",
    "wait_present",
    "wait_clickable",
    "wait_invisible",
    "wait_all_visible",
    "wait_text_present",
    "wait_title_is",
    "wait_url_contains",
)

#: The one helper whose predicate is being changed by review finding F12, and
#: which is therefore asserted against the closed two-member set
#: :data:`SINGLE_ELEMENT_VISIBILITY_PREDICATES` rather than one exact name.
IN_FLUX_HELPER: Final[str] = "wait_visible_element"

#: The prefix every member of the ``expected_conditions`` visibility family
#: shares - ``visibility_of``, ``visibility_of_element_located``,
#: ``visibility_of_all_elements_located``, ``visibility_of_any_elements_located``
#: - used to locate that family in the binding and so to prove the names below
#: are real factories.  It is deliberately *not* what :data:`IN_FLUX_HELPER` is
#: pinned to: the family spans two different return cardinalities, so a prefix
#: is too wide a target.  See :data:`SINGLE_ELEMENT_VISIBILITY_PREDICATES`.
VISIBILITY_FAMILY_PREFIX: Final[str] = "visibility_of"

#: The closed set of ``expected_conditions`` factories :data:`IN_FLUX_HELPER`
#: may name - the two visibility predicates that resolve to a **single**
#: element.  ``visibility_of`` takes an already-resolved element and is the form
#: the helper calls today; ``visibility_of_element_located`` takes a locator and
#: resolves it inside the predicate, which is the form review finding F12 moves
#: the helper to.  The family's plural members -
#: ``visibility_of_all_elements_located`` and
#: ``visibility_of_any_elements_located`` - are excluded because they resolve to
#: a *list* of elements: admitting one would silently change the return
#: cardinality all 42 visibility call sites depend on.
SINGLE_ELEMENT_VISIBILITY_PREDICATES: Final[frozenset[str]] = frozenset(
    {"visibility_of", "visibility_of_element_located"}
)

#: The four timeout values the nine Java construction sites declare.  Injected
#: one at a time so that every value a real call site passes is exercised
#: against every helper, and deliberately *not* imported from the module under
#: test, which declares no such constant (see
#: :func:`test_the_module_declares_no_timeout_constant_of_its_own`).
JAVA_TIMEOUTS: Final[tuple[int, ...]] = (2, 3, 4, 20)

#: A locator of the shape every predicate expects: a ``(By.X, "value")`` pair.
#: Built from plain strings rather than ``By`` so that the assertions depend on
#: the tuple that was passed and on nothing else - ``By.XPATH`` *is* ``"xpath"``.
LOCATOR: Final[tuple[str, str]] = ("xpath", "//button[@id='save-record']")

#: The text argument of ``wait_text_present``.
EXPECTED_TEXT: Final[str] = "Saved"

#: The title argument of ``wait_title_is``, in the shape the Employee flow uses.
EXPECTED_TITLE: Final[str] = "Employees - Odoo"

#: The fragment argument of ``wait_url_contains``.
EXPECTED_URL_FRAGMENT: Final[str] = "/web#action="

#: The class name the port constructs a wait with, kept as a constant so that
#: the source-level tests state it once.
WAIT_CLASS_NAME: Final[str] = "WebDriverWait"

#: The module under test, as a repository-root-relative POSIX path.  The
#: source-level tests locate it through this rather than through a line number:
#: ``waits.py`` is under active change for review finding F12, and a test that
#: pinned a line would fail on an edit that preserved every behaviour it claims
#: to protect.
WAITS_MODULE_PATH: Final[str] = "app/automation/waits.py"

#: Where ``timeout`` sits among a helper's positional parameters.  One for every
#: helper whose first parameter is its subject, two for the one helper that
#: takes a subject *and* a text argument ahead of it.  Used only by the
#: source-level census, which reads call sites rather than calling them.
TIMEOUT_POSITION: Final[Mapping[str, int]] = {
    **dict.fromkeys(HELPER_NAMES, 1),
    "wait_text_present": 2,
}

# --------------------------------------------------------------------------- #
# The consumer census (AAP 0.4.1: "the timeout supplied per call site")
#
# Measured from the tree with the ``ast`` module - never with a regular
# expression, which cannot tell a call from the Java source quoted in a
# docstring, and every step module quotes its Java original at length.
# --------------------------------------------------------------------------- #

#: Wait calls per step module.  The counts are the Java classes' own: ten in
#: ``Calendar``, eleven in ``Crm``, six in ``EmployeeStage`` (three title waits
#: and three visibility waits), eight in ``Sales``, three each in ``Contacts``
#: and ``Notes``, two in ``Inventory``, one each in ``LoginSD`` and
#: ``LogOutSD``, and none whatever in ``Session``.
EXPECTED_WAIT_CALLS: Final[Mapping[str, int]] = {
    "calendar_steps": 10,
    "contacts_steps": 3,
    "crm_steps": 11,
    "employee_steps": 6,
    "inventory_steps": 2,
    "login_steps": 1,
    "logout_steps": 1,
    "notes_steps": 3,
    "sales_steps": 8,
    "session_steps": 0,
}

#: The single timeout literal each step module passes at every one of its call
#: sites - the timeout of the ``WebDriverWait`` field its Java class declared.
#: ``session_steps`` is absent because it makes no call.
EXPECTED_MODULE_TIMEOUT: Final[Mapping[str, int]] = {
    "calendar_steps": 2,
    "crm_steps": 2,
    "login_steps": 3,
    "logout_steps": 3,
    "employee_steps": 3,
    "sales_steps": 4,
    "contacts_steps": 20,
    "inventory_steps": 20,
    "notes_steps": 20,
}

#: The same fact read the other way round: which modules share each timeout.
#: Asserted independently so that a module silently acquiring another class's
#: number fails on the grouping as well as on its own row.
EXPECTED_TIMEOUT_GROUPING: Final[Mapping[int, frozenset[str]]] = {
    2: frozenset({"calendar_steps", "crm_steps"}),
    3: frozenset({"login_steps", "logout_steps", "employee_steps"}),
    4: frozenset({"sales_steps"}),
    20: frozenset({"contacts_steps", "inventory_steps", "notes_steps"}),
}

#: Total wait calls across the ten step modules.
EXPECTED_TOTAL_WAIT_CALLS: Final[int] = 45

#: Title waits, all three of them in the Employee flow
#: (``EmployeeStage.java:31``, ``:71`` and ``:87``).
EXPECTED_TITLE_WAIT_CALLS: Final[int] = 3

#: Visibility-family waits - the remainder, and the population review finding
#: F12 rewrites ("All 42 visibility calls are affected").  Asserted as the
#: total minus the title waits rather than by helper name, because F12 changes
#: which visibility helper each site calls while preserving every timeout.
EXPECTED_VISIBILITY_WAIT_CALLS: Final[int] = 42

#: The helper the three title waits use.
TITLE_HELPER: Final[str] = "wait_title_is"

#: The only step module that waits on a title.
TITLE_WAIT_MODULE: Final[str] = "employee_steps"

#: The one step module that calls no wait helper and imports nothing from
#: ``app.automation`` - the port of ``Session.java``, which declares no wait.
SILENT_STEP_MODULE: Final[str] = "session_steps"

#: The package whose names a step module may import wait helpers from.  Used to
#: assert the *absence* of such an import in :data:`SILENT_STEP_MODULE`.
AUTOMATION_PACKAGE: Final[str] = "app.automation"

#: Globs covering every Python source file of the port itself, relative to the
#: repository root.  ``tests/`` is excluded deliberately: this module patches
#: and discusses :data:`WAIT_CLASS_NAME` by name, and a scan that included it
#: would be measuring the test suite rather than the port.
PORT_SOURCE_GLOBS: Final[tuple[str, ...]] = ("*.py", "app/**/*.py", "features/**/*.py")


# =========================================================================== #
# Mechanism 1 - the fake ``WebDriverWait``
#
# This is what guarantees no test waits for real: the recorder answers
# immediately, and the class the module under test would otherwise construct is
# never reached while it is installed.
# =========================================================================== #


class ConditionSentinel:
    """The object a spied predicate factory returns in place of a predicate.

    Unique per factory call, which is what lets an assertion say "the object
    this factory returned is the object that reached ``until``" without naming
    any predicate's internals.

    Callable, and raises when called: a real ``WebDriverWait`` would *evaluate*
    a predicate once per polling interval, so an evaluation here means a real
    wait has started and the failure should be immediate and explicit rather
    than a test that quietly takes four seconds.
    """

    __slots__ = ("factory",)

    def __init__(self, factory: str) -> None:
        """Record which factory produced this sentinel.

        :param factory: Name of the ``expected_conditions`` factory called.
        """
        self.factory = factory

    def __call__(self, target: Any) -> Any:
        """Fail: nothing in this module may evaluate a predicate.

        :param target: The driver or element a real wait would pass in.
        :raises AssertionError: Always.
        """
        raise AssertionError(
            f"the predicate from EC.{self.factory} was evaluated against {target!r}; "
            "no test in this module may perform a real wait"
        )

    def __repr__(self) -> str:
        """Name the originating factory, which is what a failure needs first.

        :returns: A short representation.
        """
        return f"<condition EC.{self.factory}>"


class WaitConstruction:
    """One recorded ``WebDriverWait(driver, timeout)`` construction.

    Stands in for the wait object itself, so :attr:`driver`, :attr:`timeout`
    and :attr:`conditions` describe exactly what the module under test built and
    what it then asked that wait to resolve.  ``until_not`` is deliberately
    absent: the port's core calls ``until`` and nothing else, and an
    ``AttributeError`` is the right answer if that ever changes silently.
    """

    __slots__ = ("_recorder", "conditions", "driver", "extra_args", "extra_kwargs", "timeout")

    def __init__(
        self,
        recorder: WaitRecorder,
        driver: Any,
        timeout: Any,
        extra_args: tuple[Any, ...],
        extra_kwargs: Mapping[str, Any],
    ) -> None:
        """Record the construction arguments exactly as they were passed.

        :param recorder: The recorder holding the programmed outcome.
        :param driver: First constructor argument - the session waited on.
        :param timeout: Second constructor argument, stored without conversion
            so that a test can assert its type as well as its value.
        :param extra_args: Any further positional arguments, so that a poll
            interval or ignored-exception list appearing later is visible rather
            than swallowed.
        :param extra_kwargs: Any keyword arguments, for the same reason.
        """
        self._recorder = recorder
        self.driver = driver
        self.timeout = timeout
        self.extra_args = extra_args
        self.extra_kwargs = dict(extra_kwargs)

        #: Every condition handed to :meth:`until`, in order.
        self.conditions: list[Any] = []

    def until(self, condition: Any) -> Any:
        """Record ``condition`` and answer the programmed outcome immediately.

        No polling, no sleeping and no evaluation of ``condition``: this is the
        whole of the no-real-wait guarantee.

        :param condition: The predicate the helper built.
        :returns: The recorder's programmed result.
        :raises BaseException: The recorder's programmed error, when one is set.
        """
        self.conditions.append(condition)
        if self._recorder.error is not None:
            raise self._recorder.error
        return self._recorder.result

    @property
    def condition(self) -> Any:
        """The single condition this wait was asked to resolve.

        :returns: The one recorded condition.
        :raises AssertionError: When the wait was asked for none, or for more
            than one - either of which is a behaviour change in the core.
        """
        assert len(self.conditions) == 1, (
            f"expected exactly one until() call, recorded {self.conditions!r}"
        )
        return self.conditions[0]

    def __repr__(self) -> str:
        """Summarise the construction.

        :returns: A short representation.
        """
        return f"<WaitConstruction driver={self.driver!r} timeout={self.timeout!r}>"


class WaitRecorder:
    """The stand-in installed in place of ``WebDriverWait``, and its log.

    Callable, because that is the shape the module under test uses: it calls
    ``WebDriverWait(target, timeout)`` and then ``.until(...)`` on the result.
    A test programmes the outcome before invoking a helper - :attr:`result` for
    a resolution, :attr:`error` for an expiry - and reads :attr:`constructions`
    afterwards.
    """

    __slots__ = ("constructions", "error", "result")

    def __init__(self) -> None:
        """Start with an empty log and a neutral programmed resolution."""
        #: Every construction, in order.
        self.constructions: list[WaitConstruction] = []

        #: What ``until`` answers.  A distinguishable default, so that a test
        #: which forgets to programme one still cannot pass by accident on a
        #: ``None`` that the production module might have substituted itself.
        self.result: Any = ConditionSentinel("unprogrammed-result")

        #: An exception instance or class for ``until`` to raise instead.
        self.error: BaseException | type[BaseException] | None = None

    def __call__(self, driver: Any, timeout: Any, *args: Any, **kwargs: Any) -> WaitConstruction:
        """Record a construction and return the object standing in for the wait.

        :param driver: The session the core resolved.
        :param timeout: The timeout the call site passed.
        :param args: Further positional arguments, recorded verbatim.
        :param kwargs: Keyword arguments, recorded verbatim.
        :returns: The :class:`WaitConstruction` that records ``until``.
        """
        construction = WaitConstruction(self, driver, timeout, args, kwargs)
        self.constructions.append(construction)
        return construction

    @property
    def only(self) -> WaitConstruction:
        """The single wait construction this test caused.

        :returns: The one recorded construction.
        :raises AssertionError: When zero or several were recorded - a helper
            builds exactly one wait per call.
        """
        assert len(self.constructions) == 1, (
            f"expected exactly one WebDriverWait construction, recorded "
            f"{self.constructions!r}"
        )
        return self.constructions[0]


# =========================================================================== #
# Mechanism 2 - the expected-conditions spy
# =========================================================================== #


class ConditionCall(NamedTuple):
    """One recorded call to an ``expected_conditions`` factory."""

    #: The factory's attribute name on the module.
    name: str

    #: Positional arguments, exactly as the helper passed them.
    args: tuple[Any, ...]

    #: Keyword arguments, exactly as the helper passed them.
    kwargs: Mapping[str, Any]

    #: The unique sentinel this call returned, for an identity assertion.
    predicate: ConditionSentinel


def _predicate_factories() -> Mapping[str, Callable[..., Any]]:
    """Every predicate factory the ``expected_conditions`` module itself defines.

    Filtered by ``__module__`` as well as by callability: the module also
    re-exports exception classes and typing constructs, none of which is a
    predicate factory and none of which the spy has any business replacing.

    :returns: Factory name mapped to the factory, as imported.
    """
    return {
        name: value
        for name, value in vars(EC).items()
        if not name.startswith("_")
        and inspect.isfunction(value)
        and getattr(value, "__module__", None) == EC.__name__
    }


#: The factories as they are *before* any spy is installed, frozen at import
#: time.  Load-bearing: a spy's wrapper is a function of this module, so
#: recomputing the census while the spy is installed would find none of them.
PREDICATE_FACTORIES: Final[Mapping[str, Callable[..., Any]]] = _predicate_factories()


class ConditionSpy:
    """Records which predicate factory a helper called, with what arguments.

    Installed over every member of :data:`PREDICATE_FACTORIES`, so "exactly one
    factory was called" is a statement about the whole module and not merely
    about the one factory a test expected.
    """

    __slots__ = ("calls",)

    def __init__(self) -> None:
        """Start with an empty log."""
        #: Every factory call, in order.
        self.calls: list[ConditionCall] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Replace every predicate factory with a recording wrapper.

        :param monkeypatch: pytest's patcher, whose teardown restores the real
            factories whatever the test's outcome.
        :returns: ``None``.
        """
        for name in sorted(PREDICATE_FACTORIES):
            monkeypatch.setattr(EC, name, self._wrapper(name))

    def _wrapper(self, name: str) -> Callable[..., ConditionSentinel]:
        """Build the recording stand-in for one factory.

        :param name: The factory's attribute name.
        :returns: A callable that logs its arguments and answers a sentinel.
        """

        def spy(*args: Any, **kwargs: Any) -> ConditionSentinel:
            predicate = ConditionSentinel(name)
            self.calls.append(ConditionCall(name, args, dict(kwargs), predicate))
            return predicate

        return spy

    @property
    def only(self) -> ConditionCall:
        """The single factory call this test caused.

        :returns: The one recorded call.
        :raises AssertionError: When zero or several were recorded - a helper
            names exactly one predicate.
        """
        assert len(self.calls) == 1, (
            f"expected exactly one expected-conditions call, recorded {self.calls!r}"
        )
        return self.calls[0]


# =========================================================================== #
# Mechanism 3 - the driver seam
# =========================================================================== #


class DriverSeam:
    """Stands in for ``get_driver``, counting the calls the core makes.

    The module under test resolves its session through ``get_driver`` only when
    no ``driver`` was injected, and that "only" is a documented contract with
    two halves - called exactly once, or not called at all - which is why this
    counts rather than merely answering.
    """

    __slots__ = ("call_count", "session")

    def __init__(self, session: Any) -> None:
        """Hold the session this seam answers with.

        :param session: The object a real ``get_driver`` would return.
        """
        self.session = session
        self.call_count = 0

    def __call__(self) -> Any:
        """Answer the worker's session, counting the call.

        :returns: The held session.
        """
        self.call_count += 1
        return self.session


# =========================================================================== #
# Helper specifications - the surface table as callable data
# =========================================================================== #


class HelperSpec(NamedTuple):
    """One row of the surface table, with the arguments needed to drive it."""

    #: The helper's name in :data:`HELPER_NAMES`.
    name: str

    #: Positional arguments the helper takes *ahead* of ``timeout``.
    leading_args: tuple[Any, ...]

    #: The ``expected_conditions`` factory the helper's row names, or ``None``
    #: for the helper review finding F12 is changing.
    factory: str | None

    #: The arguments that factory must receive.
    factory_args: tuple[Any, ...]

    #: Whether the helper's subject is a locator, and so whether the no-lookup
    #: invariant of F12 applies to it.
    takes_locator: bool

    @property
    def helper(self) -> Callable[..., Any]:
        """The live helper, fetched by name so a rebind cannot be missed.

        :returns: The callable from the module under test.
        """
        return getattr(waits, self.name)

    def invoke(self, timeout: Any, **kwargs: Any) -> Any:
        """Call the helper with its subject, ``timeout`` positionally, and ``kwargs``.

        ``timeout`` is passed positionally on purpose: that is how every ported
        call site passes it, and it keeps the call independent of the parameter's
        name.

        :param timeout: The timeout to pass.
        :param kwargs: Keyword arguments, in practice ``driver=``.
        :returns: Whatever the helper returns.
        """
        return self.helper(*self.leading_args, timeout, **kwargs)

    def invoke_without_timeout(self, **kwargs: Any) -> Any:
        """Call the helper with its subject alone, omitting ``timeout``.

        :param kwargs: Keyword arguments, in practice ``driver=``.
        :returns: Whatever the helper returns - in practice nothing, because
            Python refuses to bind the call.
        """
        return self.helper(*self.leading_args, **kwargs)


#: The surface table, in its documented order.  ``wait_visible_element`` carries
#: ``factory=None`` - see the module docstring's F12 section - and is handed a
#: locator, which is both what the corrected helper takes and something the
#: current helper passes through untouched.
HELPER_SPECS: Final[tuple[HelperSpec, ...]] = (
    HelperSpec("wait_visible", (LOCATOR,), "visibility_of_element_located", (LOCATOR,), True),
    HelperSpec(IN_FLUX_HELPER, (LOCATOR,), None, (LOCATOR,), True),
    HelperSpec("wait_present", (LOCATOR,), "presence_of_element_located", (LOCATOR,), True),
    HelperSpec("wait_clickable", (LOCATOR,), "element_to_be_clickable", (LOCATOR,), True),
    HelperSpec("wait_invisible", (LOCATOR,), "invisibility_of_element_located", (LOCATOR,), True),
    HelperSpec(
        "wait_all_visible",
        (LOCATOR,),
        "visibility_of_all_elements_located",
        (LOCATOR,),
        True,
    ),
    HelperSpec(
        "wait_text_present",
        (LOCATOR, EXPECTED_TEXT),
        "text_to_be_present_in_element",
        (LOCATOR, EXPECTED_TEXT),
        True,
    ),
    HelperSpec("wait_title_is", (EXPECTED_TITLE,), "title_is", (EXPECTED_TITLE,), False),
    HelperSpec(
        "wait_url_contains",
        (EXPECTED_URL_FRAGMENT,),
        "url_contains",
        (EXPECTED_URL_FRAGMENT,),
        False,
    ),
)

#: The eight helpers whose predicate is settled and is therefore pinned by name.
SETTLED_SPECS: Final[tuple[HelperSpec, ...]] = tuple(
    spec for spec in HELPER_SPECS if spec.factory is not None
)

#: The one in-flux helper's specification.
IN_FLUX_SPEC: Final[HelperSpec] = next(
    spec for spec in HELPER_SPECS if spec.name == IN_FLUX_HELPER
)


def _specs(specs: Iterable[HelperSpec]) -> list[Any]:
    """Parametrize rows of one helper specification each, named by the helper.

    :param specs: The specifications to turn into rows.
    :returns: A list of ``pytest.param`` rows.
    """
    return [pytest.param(spec, id=spec.name) for spec in specs]


# --------------------------------------------------------------------------- #
# Resolved-value stand-ins, for the "what comes back" assertions
# --------------------------------------------------------------------------- #


class ResolvedValue:
    """A stand-in for whatever a predicate resolved to, identified by label."""

    __slots__ = ("label",)

    def __init__(self, label: str) -> None:
        """Label the value so a failure names it.

        :param label: Human-readable identity.
        """
        self.label = label

    def __repr__(self) -> str:
        """Show the label.

        :returns: A short representation.
        """
        return f"<resolved {self.label}>"


#: The three shapes ``until`` resolves to in this suite: a single element, a
#: list of elements, and a boolean.  Every one of the nine helpers is asserted
#: against all three, because the core returns what the wait resolved to and
#: knows nothing about which shape a given predicate produces.
RESOLVED_VALUES: Final[list[Any]] = [
    pytest.param(ResolvedValue("element"), id="element"),
    pytest.param([ResolvedValue("element-0"), ResolvedValue("element-1")], id="element-list"),
    pytest.param(True, id="boolean"),
]


class SentinelError(Exception):
    """An exception no production code knows about.

    Used to prove that *nothing at all* is caught, not merely that the
    binding's own ``TimeoutException`` is allowed through.
    """


# =========================================================================== #
# The three autouse seams
# =========================================================================== #


@pytest.fixture(autouse=True)
def wait_recorder(monkeypatch: pytest.MonkeyPatch) -> WaitRecorder:
    """Install the fake ``WebDriverWait`` for every test in this module.

    Autouse and requestable: autouse because no test here may be *able* to
    construct a real wait, and requestable by name because most tests then read
    the log it collects.  Patched on ``app.automation.waits`` - the module
    imports the class by name, so that is where the binding it uses lives.

    :param monkeypatch: pytest's patcher, whose teardown restores the class.
    :returns: The recorder now standing in for ``WebDriverWait``.
    """
    recorder = WaitRecorder()
    monkeypatch.setattr(waits, WAIT_CLASS_NAME, recorder)
    return recorder


@pytest.fixture(autouse=True)
def condition_spy(monkeypatch: pytest.MonkeyPatch) -> ConditionSpy:
    """Install the expected-conditions spy for every test in this module.

    Patching attributes of ``selenium.webdriver.support.expected_conditions``
    is visible to the module under test because it imports that module as ``EC``
    and resolves ``EC.<factory>`` at call time.

    :param monkeypatch: pytest's patcher, whose teardown restores the factories.
    :returns: The spy now standing in for every predicate factory.
    """
    spy = ConditionSpy()
    spy.install(monkeypatch)
    return spy


@pytest.fixture(autouse=True)
def driver_seam(monkeypatch: pytest.MonkeyPatch) -> DriverSeam:
    """Install a counting stand-in for ``get_driver``.

    Autouse, so that no test can reach the real session lifecycle owner even by
    omitting the injection seam, which is what makes this module runnable with
    no browser and no populated ``configuration.properties``.

    :param monkeypatch: pytest's patcher, whose teardown restores the function.
    :returns: The seam standing in for ``get_driver``, answering a recorder.
    """
    seam = DriverSeam(StubDriver())
    monkeypatch.setattr(waits, "get_driver", seam)
    return seam


# =========================================================================== #
# Source-level census fixtures
# =========================================================================== #


class WaitCallSite(NamedTuple):
    """One wait call found in a step module's source."""

    #: The step module's file stem, ``calendar_steps`` and so on.
    module: str

    #: The helper called.
    helper: str

    #: The timeout literal passed at that site.
    timeout: int | float

    #: The line the call starts on, so a failure is locatable.
    lineno: int


class ModuleCensus(NamedTuple):
    """What one step module's source says about waits."""

    #: The module's file stem.
    name: str

    #: Its wait calls, in source order.
    calls: tuple[WaitCallSite, ...]

    #: What it imports from ``app.automation`` or any of its submodules, sorted:
    #: the imported names for a ``from`` import, the dotted module name for a
    #: plain ``import``.
    automation_imports: tuple[str, ...]

    @property
    def timeouts(self) -> frozenset[int | float]:
        """The distinct timeout literals this module passes.

        :returns: The set of literals, empty when the module makes no call.
        """
        return frozenset(call.timeout for call in self.calls)


def _called_name(node: ast.Call) -> str | None:
    """The bare name of whatever ``node`` calls.

    Handles both the direct form the step modules use - ``wait_visible(...)``
    after a ``from app.automation import ...`` - and an attribute form
    (``automation.wait_visible(...)``), so the census cannot be defeated by a
    change of import style.

    :param node: The call node.
    :returns: The callee's name, or ``None`` for a call on an expression.
    """
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _is_automation_module(module: str | None) -> bool:
    """Whether ``module`` is the automation package or one of its submodules.

    Both forms are checked so that the absence of an automation import in
    ``session_steps`` is a statement about the package rather than about one
    spelling of it: ``from app.automation import wait_visible``,
    ``from app.automation.waits import wait_visible`` and
    ``import app.automation`` all count.

    :param module: The dotted module name, or ``None`` for a relative import.
    :returns: ``True`` when the name reaches the automation package.
    """
    if module is None:
        return False
    return module == AUTOMATION_PACKAGE or module.startswith(f"{AUTOMATION_PACKAGE}.")


def _numeric_literal(node: ast.expr, where: str) -> int | float:
    """The numeric value of ``node``, which must be a literal.

    :param node: The argument node.
    :param where: Human-readable location, used in the failure message.
    :returns: The literal's value.
    :raises AssertionError: When the argument is not a plain numeric literal -
        a computed or named timeout would hide the call site's number, which is
        exactly what AAP 0.4.1 requires to stay visible.
    """
    assert isinstance(node, ast.Constant), f"{where}: timeout is not a literal"
    value = node.value
    assert isinstance(value, int | float), f"{where}: timeout literal is {value!r}"
    assert not isinstance(value, bool), f"{where}: timeout literal is {value!r}"
    return value


def _timeout_of(node: ast.Call, helper: str, where: str) -> int | float:
    """The timeout literal one wait call passes.

    :param node: The call node.
    :param helper: The helper being called, which fixes the argument's position.
    :param where: Human-readable location, used in the failure message.
    :returns: The timeout literal.
    :raises AssertionError: When no timeout argument is present at all.
    """
    for keyword in node.keywords:
        if keyword.arg == "timeout":
            return _numeric_literal(keyword.value, where)

    position = TIMEOUT_POSITION[helper]
    assert len(node.args) > position, f"{where}: {helper} was called without a timeout"
    return _numeric_literal(node.args[position], where)


def _census_of(path: Path) -> ModuleCensus:
    """Parse one step module and report its wait calls and automation imports.

    :param path: The step module's path.
    :returns: That module's census.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    module = path.stem
    calls: list[WaitCallSite] = []
    imports: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and _is_automation_module(node.module):
            imports.update(alias.name for alias in node.names)
        if isinstance(node, ast.Import):
            imports.update(
                alias.name for alias in node.names if _is_automation_module(alias.name)
            )
        if isinstance(node, ast.Call):
            helper = _called_name(node)
            if helper in HELPER_NAMES:
                where = f"{module}:{node.lineno}"
                calls.append(
                    WaitCallSite(module, helper, _timeout_of(node, helper, where), node.lineno)
                )

    calls.sort(key=lambda call: call.lineno)
    return ModuleCensus(module, tuple(calls), tuple(sorted(imports)))


@pytest.fixture(scope="session")
def wait_census() -> Mapping[str, ModuleCensus]:
    """The wait calls of every step module, parsed once per session.

    Session-scoped because it is a pure reading of tracked files: there is
    nothing here for one test to hand to the next, and parsing ten modules once
    keeps this module's runtime in the milliseconds.

    :returns: File stem mapped to that module's census.
    """
    return {
        path.stem: _census_of(path) for path in sorted(step_modules_dir().glob("*_steps.py"))
    }


def _code_lines(source: str) -> tuple[str, ...]:
    """``source``'s lines with every line inside a string literal removed.

    The module under test quotes the Java ``new WebDriverWait(driver, 3)`` in
    its docstring while explaining that the binding counts seconds, so a raw
    textual count over the file would find two occurrences for a documented
    reason.  Dropping string-literal lines leaves the code, which is what the
    single-construction-site claim is about.

    :param source: The file's text.
    :returns: The lines that are not part of a string literal.
    """
    tree = ast.parse(source)
    literal_lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str | bytes):
            literal_lines.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))

    return tuple(
        line
        for number, line in enumerate(source.splitlines(), start=1)
        if number not in literal_lines
    )


def _port_sources(repo_root: Path) -> tuple[Path, ...]:
    """Every Python source file of the port itself, in a stable order.

    :param repo_root: The repository root.
    :returns: The matched paths, sorted and de-duplicated.
    """
    matched: set[Path] = set()
    for pattern in PORT_SOURCE_GLOBS:
        matched.update(repo_root.glob(pattern))
    return tuple(sorted(matched))


# =========================================================================== #
# 1 - The public surface, and its closure
# =========================================================================== #


def test_waits_exports_exactly_the_nine_helpers() -> None:
    """``waits.__all__`` is the nine helpers of the surface table, and no more.

    Set equality rather than containment, because AAP 0.4.2 fixes the surface:
    a tenth export would widen what step modules and page objects may reach for,
    and a missing one would break a ported call site.
    """
    assert set(waits.__all__) == set(HELPER_NAMES)
    assert len(waits.__all__) == len(HELPER_NAMES)

    for name in HELPER_NAMES:
        helper = getattr(waits, name)
        assert inspect.isfunction(helper), f"{name} is not a function"
        assert helper.__module__ == waits.__name__, f"{name} is not defined in waits.py"

    exported = {name for name in dir(waits) if name.startswith("wait")}
    assert exported == set(HELPER_NAMES)


def test_the_package_barrel_exports_the_same_nine_objects() -> None:
    """``app.automation`` re-exports the nine helpers, and they are the same objects.

    AAP 0.4.2 names the barrel as the surface others import from, so "exported"
    has to mean the identical function object rather than a same-named wrapper.
    """
    assert set(HELPER_NAMES) <= set(automation.__all__)

    barrel_waits = {name for name in automation.__all__ if name.startswith("wait_")}
    assert barrel_waits == set(HELPER_NAMES)

    for name in HELPER_NAMES:
        assert getattr(automation, name) is getattr(waits, name), f"{name} is not the same object"


def test_every_named_expected_condition_exists_in_the_binding() -> None:
    """Each factory this module names is a real ``expected_conditions`` factory.

    Without this, a typo in an expected factory name would make the mapping
    assertions compare one wrong string against another and pass.  The subset
    check extends that guard to
    :data:`SINGLE_ELEMENT_VISIBILITY_PREDICATES`: both names the in-flux helper
    may take are real members of the binding's visibility family, so pinning
    that helper to them cannot pass on a name selenium does not define.
    """
    for spec in SETTLED_SPECS:
        assert spec.factory in PREDICATE_FACTORIES, f"{spec.factory} is not a predicate factory"

    family = {name for name in PREDICATE_FACTORIES if name.startswith(VISIBILITY_FAMILY_PREFIX)}
    assert "visibility_of" in family
    assert "visibility_of_element_located" in family
    assert SINGLE_ELEMENT_VISIBILITY_PREDICATES.issubset(family)


# =========================================================================== #
# 2 - ``timeout`` is required, everywhere, with no constant behind it
# =========================================================================== #


@pytest.mark.parametrize("spec", _specs(HELPER_SPECS))
def test_timeout_is_a_required_parameter_of_every_helper(spec: HelperSpec) -> None:
    """No helper declares a default for ``timeout``, and no other parameter but ``driver`` does.

    ``inspect.signature`` is called without annotation evaluation on purpose:
    ``waits.py`` quotes its ``TYPE_CHECKING``-only annotations, and resolving
    them would raise ``NameError`` instead of reporting the defaults.

    :param spec: The helper under assertion.
    """
    signature = inspect.signature(spec.helper)

    timeout = signature.parameters["timeout"]
    assert timeout.default is inspect.Parameter.empty
    assert timeout.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD

    for name, parameter in signature.parameters.items():
        if name == "driver":
            assert parameter.default is None
            assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        else:
            assert parameter.default is inspect.Parameter.empty, f"{name} carries a default"


@pytest.mark.parametrize("spec", _specs(HELPER_SPECS))
def test_omitting_the_timeout_is_a_type_error_from_python_itself(
    spec: HelperSpec,
    wait_recorder: WaitRecorder,
    condition_spy: ConditionSpy,
) -> None:
    """Calling a helper without a timeout fails at argument binding, never falls back.

    Python itself refuses the call, which is the whole of the module's
    "Exclusion 1 - no default timeout": there is no silent fallback to some
    other Java class's number.  The message naming ``timeout`` is what
    distinguishes that binding error from a ``TypeError`` a body might raise,
    and the two empty logs prove no wait and no predicate were built first.

    :param spec: The helper under assertion.
    :param wait_recorder: The fake wait, asserted to have recorded nothing.
    :param condition_spy: The predicate spy, asserted to have recorded nothing.
    """
    with pytest.raises(TypeError) as binding_error:
        spec.invoke_without_timeout()

    assert "timeout" in str(binding_error.value)
    assert wait_recorder.constructions == []
    assert condition_spy.calls == []


def test_the_module_declares_no_timeout_constant_of_its_own(repo_root: Path) -> None:
    """No timeout value is a module attribute, a default or a code literal in ``waits.py``.

    AAP 0.4.1 requires the timeout to be supplied per call site, and the module
    documents why: a module-level default or shared constant would let a ported
    call site silently acquire another Java class's number, which is precisely
    the parity failure the design exists to prevent.  The four numbers appear in
    ``waits.py`` only inside its docstring table, which the AST reads as one
    string constant rather than as four numbers, so a timeout that reached the
    code - as a constant, a parameter default or an inline literal - appears
    here as a numeric literal and fails.  The module also exposes no numeric
    attribute of any kind, which is the same claim checked after import.

    :param repo_root: The repository root, for reading the module's source.
    """
    numeric_attributes = {
        name: value
        for name, value in vars(waits).items()
        if isinstance(value, int | float) and not isinstance(value, bool)
    }
    assert numeric_attributes == {}

    source = (repo_root / WAITS_MODULE_PATH).read_text(encoding="utf-8")
    literals = {
        node.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant)
        and isinstance(node.value, int | float)
        and not isinstance(node.value, bool)
    }
    assert literals.isdisjoint(JAVA_TIMEOUTS), f"a timeout literal is declared: {literals}"


# =========================================================================== #
# 3 - The timeout, and the resolved session, reach the wait unchanged
# =========================================================================== #


@pytest.mark.parametrize("timeout", JAVA_TIMEOUTS)
@pytest.mark.parametrize("spec", _specs(HELPER_SPECS))
def test_the_timeout_reaches_the_wait_unchanged(
    spec: HelperSpec,
    timeout: int,
    wait_recorder: WaitRecorder,
    stub_driver: StubDriver,
) -> None:
    """Each helper constructs one wait carrying exactly the number and the driver it was given.

    All four Java values are injected into all nine helpers: the module exists
    so that ``Calendar``'s two seconds can never become ``Contacts``' twenty,
    and the type assertion is what rules out a conversion or a clamp on the way
    through.

    :param spec: The helper under assertion.
    :param timeout: One of the four Java timeouts.
    :param wait_recorder: The fake wait.
    :param stub_driver: The session injected through the test seam.
    """
    spec.invoke(timeout, driver=stub_driver)

    construction = wait_recorder.only
    assert construction.timeout == timeout
    assert type(construction.timeout) is type(timeout)
    assert construction.driver is stub_driver
    assert construction.extra_args == ()
    assert construction.extra_kwargs == {}


@pytest.mark.parametrize("spec", _specs(HELPER_SPECS))
def test_a_fractional_timeout_is_passed_through_without_conversion(
    spec: HelperSpec,
    wait_recorder: WaitRecorder,
    stub_driver: StubDriver,
) -> None:
    """A ``float`` timeout arrives as that same float, neither rounded nor floored.

    The module's annotation is ``int | float`` and its core documents "no
    conversion, no clamping, no floor": a sub-second value is the case that
    would expose any of the three, since rounding it would change the wait from
    fractional to zero or one second.

    :param spec: The helper under assertion.
    :param wait_recorder: The fake wait.
    :param stub_driver: The session injected through the test seam.
    """
    spec.invoke(0.5, driver=stub_driver)

    construction = wait_recorder.only
    assert construction.timeout == 0.5
    assert type(construction.timeout) is float


# =========================================================================== #
# 4 - The predicate each settled helper names
# =========================================================================== #


@pytest.mark.parametrize("spec", _specs(SETTLED_SPECS))
def test_each_settled_helper_waits_on_the_condition_its_row_names(
    spec: HelperSpec,
    wait_recorder: WaitRecorder,
    condition_spy: ConditionSpy,
    stub_driver: StubDriver,
) -> None:
    """One factory is called - the row's - with the helper's arguments, and its result awaited.

    The identity assertion is the load-bearing one: it says the object the
    factory produced is the object handed to ``until``, so the helper adds a
    name and a type and no behaviour, without this test knowing anything about
    a predicate's internals.

    :param spec: The helper under assertion.
    :param wait_recorder: The fake wait.
    :param condition_spy: The predicate spy.
    :param stub_driver: The session injected through the test seam.
    """
    spec.invoke(JAVA_TIMEOUTS[0], driver=stub_driver)

    call = condition_spy.only
    assert call.name == spec.factory
    assert call.args == spec.factory_args
    assert call.kwargs == {}
    assert wait_recorder.only.condition is call.predicate


# =========================================================================== #
# 5 - ``wait_visible_element``, the one in-flux helper
# =========================================================================== #


def test_wait_visible_element_waits_on_a_visibility_condition_over_its_locator(
    wait_recorder: WaitRecorder,
    condition_spy: ConditionSpy,
    stub_driver: StubDriver,
) -> None:
    """The helper builds one single-element visibility predicate from the locator it was handed.

    The factory's exact name is deliberately **not** pinned to one string.
    Review finding F12 (HIGH) requires this helper to accept a locator and
    resolve it inside the predicate, because every caller today pre-resolves its
    element through a page property under the driver's ten-second implicit wait
    and therefore never receives the 2/3/4/20-second polling window its Java
    original intended.  Pinning today's ``visibility_of`` would ship a red test
    the moment that fix lands, while the contract that matters survives both
    forms: one visibility predicate, built from the locator, and awaited - with
    the locator resolved lazily inside it, which
    :func:`test_no_helper_performs_a_lookup_of_its_own` asserts.

    What is pinned instead is :data:`SINGLE_ELEMENT_VISIBILITY_PREDICATES`, a
    closed set of exactly two names rather than the visibility family:
    ``visibility_of``, the element-based form the helper calls today, and
    ``visibility_of_element_located``, the locator-based form F12 moves it to.
    The family's plural members - ``visibility_of_all_elements_located`` and
    ``visibility_of_any_elements_located`` - are refused because they resolve to
    a *list* of elements instead of one element, which would change what every
    one of the 42 visibility call sites receives from this helper.  A test that
    accepted the whole family by prefix could not tell those two contracts
    apart, and so could not fail on a plural predicate.

    The return assertions state the same requirement from the other end: what
    comes back is the single object the wait resolved, by identity, and is not a
    sequence of objects - so a plural predicate cannot satisfy this test even
    were its name somehow admitted.

    :param wait_recorder: The fake wait, programmed to resolve one element.
    :param condition_spy: The predicate spy.
    :param stub_driver: The session injected through the test seam.
    """
    resolved = ResolvedValue("visible-element")
    wait_recorder.result = resolved

    returned = IN_FLUX_SPEC.invoke(JAVA_TIMEOUTS[0], driver=stub_driver)

    call = condition_spy.only
    assert call.name in SINGLE_ELEMENT_VISIBILITY_PREDICATES, (
        f"{call.name} is not one of the single-element visibility predicates "
        f"{sorted(SINGLE_ELEMENT_VISIBILITY_PREDICATES)}"
    )
    assert call.args == (LOCATOR,)
    assert call.kwargs == {}
    assert wait_recorder.only.condition is call.predicate

    assert returned is resolved
    assert not isinstance(returned, list | tuple), (
        f"{IN_FLUX_HELPER} returned {returned!r} - a sequence, not one resolved element"
    )


# =========================================================================== #
# 6 - The F12 invariant: the helper itself looks nothing up
# =========================================================================== #


@pytest.mark.parametrize("spec", _specs(HELPER_SPECS))
def test_no_helper_performs_a_lookup_of_its_own(
    spec: HelperSpec,
    stub_driver: StubDriver,
    wait_recorder: WaitRecorder,
    condition_spy: ConditionSpy,
) -> None:
    """A helper touches the driver for nothing: resolution belongs inside the predicate.

    This is the invariant behind review finding F12.  An element located by the
    helper is located *before* the wait exists, so it is found under the
    ten-second implicit wait and the explicit timeout never governs it; an
    element located inside the predicate is re-tried on every polling interval
    of the explicit wait, which is the Java behaviour.  The recorder's log being
    empty is the direct evidence: the helper found nothing, read no title and no
    URL, and merely built a condition and handed it over.

    For a locator-taking helper the locator itself is asserted to be what
    reached the predicate, which is the other half of the same statement - the
    tuple was deferred into the condition rather than resolved into an element
    on the way.  The two helpers whose subject is a title or a URL fragment have
    no locator to defer, and for them the empty log is the whole claim.

    :param spec: The helper under assertion.
    :param stub_driver: The recorder injected as the session.
    :param wait_recorder: The fake wait, asserted to have been built once.
    :param condition_spy: The predicate spy, read for the deferred locator.
    """
    spec.invoke(JAVA_TIMEOUTS[0], driver=stub_driver)

    assert stub_driver.calls == []
    assert stub_driver.operations() == ()
    assert wait_recorder.only.driver is stub_driver

    if spec.takes_locator:
        assert condition_spy.only.args[0] == LOCATOR
    else:
        assert condition_spy.only.args == spec.leading_args


# =========================================================================== #
# 7 - What the wait resolved to is what comes back
# =========================================================================== #


@pytest.mark.parametrize("resolved", RESOLVED_VALUES)
@pytest.mark.parametrize("spec", _specs(HELPER_SPECS))
def test_the_resolved_value_comes_back_untouched(
    spec: HelperSpec,
    resolved: Any,
    wait_recorder: WaitRecorder,
    stub_driver: StubDriver,
) -> None:
    """An element, a list of elements or a boolean returns as the identical object.

    Java's step bodies go on to use what ``wait.until(...)`` answered, so every
    helper returns it unchanged: nothing substituted, nothing wrapped, and never
    ``None`` on success.

    :param spec: The helper under assertion.
    :param resolved: The value the wait is programmed to resolve to.
    :param wait_recorder: The fake wait, programmed with ``resolved``.
    :param stub_driver: The session injected through the test seam.
    """
    wait_recorder.result = resolved

    result = spec.invoke(JAVA_TIMEOUTS[0], driver=stub_driver)

    assert result is resolved
    assert result is not None


# =========================================================================== #
# 8 - Nothing is caught
# =========================================================================== #


@pytest.mark.parametrize("spec", _specs(HELPER_SPECS))
def test_an_expiry_propagates_as_the_same_exception_instance(
    spec: HelperSpec,
    wait_recorder: WaitRecorder,
    stub_driver: StubDriver,
) -> None:
    """A ``TimeoutException`` travels out of the helper untouched, as the very instance raised.

    No Java step class catches an expiry and neither does this port: instance
    identity rules out a re-raise, a project-specific wrapper and a chained
    replacement alike, and the absence of a returned value rules out answering
    ``None`` or ``False`` because a wait ran out.

    :param spec: The helper under assertion.
    :param wait_recorder: The fake wait, programmed to expire.
    :param stub_driver: The session injected through the test seam.
    """
    expiry = TimeoutException("the element was still not visible")
    wait_recorder.error = expiry

    with pytest.raises(TimeoutException) as raised:
        spec.invoke(JAVA_TIMEOUTS[0], driver=stub_driver)

    assert raised.value is expiry
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__ is False


@pytest.mark.parametrize("spec", _specs(HELPER_SPECS))
def test_no_exception_at_all_is_caught(
    spec: HelperSpec,
    wait_recorder: WaitRecorder,
    stub_driver: StubDriver,
) -> None:
    """An exception the port has never heard of also travels straight out.

    The expiry test alone would pass against a helper that caught everything
    and re-raised only ``TimeoutException``.  A type no production module can
    name closes that gap: the module installs no error handler of any kind.

    :param spec: The helper under assertion.
    :param wait_recorder: The fake wait, programmed to fail.
    :param stub_driver: The session injected through the test seam.
    """
    failure = SentinelError("a fault from below the helper")
    wait_recorder.error = failure

    with pytest.raises(SentinelError) as raised:
        spec.invoke(JAVA_TIMEOUTS[0], driver=stub_driver)

    assert raised.value is failure


# =========================================================================== #
# 9 - The driver seam
# =========================================================================== #


@pytest.mark.parametrize("spec", _specs(HELPER_SPECS))
def test_the_session_is_resolved_once_when_no_driver_is_injected(
    spec: HelperSpec,
    driver_seam: DriverSeam,
    wait_recorder: WaitRecorder,
) -> None:
    """With no ``driver`` argument, ``get_driver`` is called once and its answer is waited on.

    Exactly once, not merely at least once: a helper resolving the session twice
    would double the lifecycle owner's work at every gated interaction in the
    suite, and a helper resolving it zero times would wait on the wrong session.

    :param spec: The helper under assertion.
    :param driver_seam: The stand-in for ``get_driver``.
    :param wait_recorder: The fake wait.
    """
    spec.invoke(JAVA_TIMEOUTS[0])

    assert driver_seam.call_count == 1
    assert wait_recorder.only.driver is driver_seam.session


@pytest.mark.parametrize("spec", _specs(HELPER_SPECS))
def test_an_injected_driver_never_reaches_the_lifecycle_owner(
    spec: HelperSpec,
    driver_seam: DriverSeam,
    wait_recorder: WaitRecorder,
    stub_driver: StubDriver,
) -> None:
    """With a ``driver`` supplied, ``get_driver`` is not called at all.

    That is the whole of the test seam's contract, and the reason this module
    needs no browser: the injected session is waited on and the worker-local
    holder is never consulted.

    :param spec: The helper under assertion.
    :param driver_seam: The stand-in for ``get_driver``.
    :param wait_recorder: The fake wait.
    :param stub_driver: The session injected through the test seam.
    """
    spec.invoke(JAVA_TIMEOUTS[0], driver=stub_driver)

    assert driver_seam.call_count == 0
    assert wait_recorder.only.driver is stub_driver


# =========================================================================== #
# 10 - One construction site in the whole port
# =========================================================================== #


def test_the_wait_is_constructed_in_exactly_one_place(repo_root: Path) -> None:
    """``app/automation/waits.py`` is the only place in the port that builds a wait.

    Asserted three ways, from the strongest to the most direct: no other source
    file of the port calls the class; no other source file imports it; and the
    module's own code - its string literals dropped, because its docstring
    quotes the Java ``new WebDriverWait(driver, 3)`` while explaining that the
    binding counts seconds - mentions the constructor once.  ``tests/`` is
    outside the scan: this module names the class in its patch target.

    :param repo_root: The repository root, for locating the port's sources.
    """
    constructions: list[str] = []
    importers: list[str] = []
    located: list[str] = []
    for path in _port_sources(repo_root):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        relative = path.relative_to(repo_root).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _called_name(node) == WAIT_CLASS_NAME:
                constructions.append(relative)
                located.append(f"{relative}:{node.lineno}")
            if isinstance(node, ast.ImportFrom) and any(
                alias.name == WAIT_CLASS_NAME for alias in node.names
            ):
                importers.append(relative)

    assert constructions == [WAITS_MODULE_PATH], f"waits are constructed at {located}"
    assert importers == [WAITS_MODULE_PATH]

    source = (repo_root / WAITS_MODULE_PATH).read_text(encoding="utf-8")
    code = "\n".join(_code_lines(source))
    assert code.count(f"{WAIT_CLASS_NAME}(") == 1


# =========================================================================== #
# 11 - The call-site census (AAP 0.4.1's nine timeouts, as consumed)
#
# Parsed with ``ast``.  A regular expression cannot distinguish a call from the
# Java source the step modules quote in their docstrings - ``calendar_steps``
# reproduces ``new WebDriverWait(Driver.getDriver(), 2);`` verbatim - so the
# census would be wrong in both directions.
#
# No test below pins which visibility helper a call site uses: review finding
# F12 rewrites those call sites while preserving every timeout literal, so the
# literals are the invariant and the helper names are not.
# =========================================================================== #


def test_the_census_covers_every_step_module(wait_census: Mapping[str, ModuleCensus]) -> None:
    """The census reads exactly the ten step modules the port defines.

    Stated first, because every count below would be vacuous for a module the
    census never found - a renamed or deleted step module has to fail here
    rather than pass by absence.

    :param wait_census: The parsed census.
    """
    assert set(wait_census) == set(EXPECTED_WAIT_CALLS)


def test_each_step_module_makes_the_wait_calls_its_java_class_made(
    wait_census: Mapping[str, ModuleCensus],
) -> None:
    """Every step module's wait-call count is its Java class's.

    Ten in ``Calendar``, eleven in ``Crm``, six in ``EmployeeStage``, eight in
    ``Sales``, three each in ``Contacts`` and ``Notes``, two in ``Inventory``,
    one each in ``LoginSD`` and ``LogOutSD``, and none in ``Session``.

    :param wait_census: The parsed census.
    """
    counts = {name: len(census.calls) for name, census in wait_census.items()}
    assert counts == dict(EXPECTED_WAIT_CALLS)


def test_each_step_module_passes_one_timeout_at_every_call_site(
    wait_census: Mapping[str, ModuleCensus],
) -> None:
    """Within a step module, every wait call carries that module's single timeout literal.

    This is the parity failure the module under test exists to prevent, read
    from the consumers: a call site that acquired another Java class's number -
    from a helper default, a shared constant or a copy-paste - shows up here as
    a second literal in a module that must have exactly one.

    :param wait_census: The parsed census.
    """
    for name, expected in EXPECTED_MODULE_TIMEOUT.items():
        census = wait_census[name]
        assert census.timeouts == frozenset({expected}), f"{name} passes {census.timeouts}"
        for call in census.calls:
            assert call.timeout == expected, f"{name}:{call.lineno} passes {call.timeout}"
            assert type(call.timeout) is int, f"{name}:{call.lineno} passes a non-integer"


def test_the_timeout_grouping_is_the_one_the_java_classes_declare(
    wait_census: Mapping[str, ModuleCensus],
) -> None:
    """The four timeouts group the step modules exactly as AAP 0.4.1 lists them.

    2 s for ``calendar`` and ``crm``; 3 s for ``login``, ``logout`` and
    ``employee``; 4 s for ``sales``; 20 s for ``contacts``, ``inventory`` and
    ``notes`` - the same nine construction sites, grouped.

    :param wait_census: The parsed census.
    """
    grouping: dict[int | float, set[str]] = {}
    for census in wait_census.values():
        for call in census.calls:
            grouping.setdefault(call.timeout, set()).add(census.name)

    assert {timeout: frozenset(modules) for timeout, modules in grouping.items()} == dict(
        EXPECTED_TIMEOUT_GROUPING
    )


def test_the_title_wait_occurs_three_times_and_only_in_the_employee_module(
    wait_census: Mapping[str, ModuleCensus],
) -> None:
    """``wait_title_is`` is called three times in the port, all of them in the Employee flow.

    ``EmployeeStage.java:31``, ``:71`` and ``:87`` are the three, and
    ``LoginSD.java:44-46`` asserts its title without a wait, so the title helper
    appears in exactly one step module.

    :param wait_census: The parsed census.
    """
    sites = [
        call
        for census in wait_census.values()
        for call in census.calls
        if call.helper == TITLE_HELPER
    ]

    assert len(sites) == EXPECTED_TITLE_WAIT_CALLS
    assert {call.module for call in sites} == {TITLE_WAIT_MODULE}
    assert {call.timeout for call in sites} == {EXPECTED_MODULE_TIMEOUT[TITLE_WAIT_MODULE]}


def test_the_session_module_calls_no_wait_helper(
    wait_census: Mapping[str, ModuleCensus],
) -> None:
    """``session_steps`` calls no wait helper and imports nothing from ``app.automation``.

    ``Session.java`` constructs no ``WebDriverWait``, so a wait helper reaching
    that one module would be a divergence rather than a tidy-up.  The absent
    import is the stronger half: it cannot call what it has not imported.

    :param wait_census: The parsed census.
    """
    census = wait_census[SILENT_STEP_MODULE]
    assert census.calls == ()
    assert census.automation_imports == ()


def test_the_census_totals_are_forty_five_waits_of_which_forty_two_are_visibility(
    wait_census: Mapping[str, ModuleCensus],
) -> None:
    """45 wait calls across the port, 3 on a title and the remaining 42 on visibility.

    The visibility total is computed as "every wait call minus the title waits"
    rather than by helper name, because review finding F12 changes which
    visibility helper each of those 42 sites calls - the review's own count,
    "All 42 visibility calls are affected", is this number - while leaving every
    timeout literal in place.

    :param wait_census: The parsed census.
    """
    total = sum(len(census.calls) for census in wait_census.values())
    titles = sum(
        1 for census in wait_census.values() for call in census.calls if call.helper == TITLE_HELPER
    )

    assert total == EXPECTED_TOTAL_WAIT_CALLS
    assert titles == EXPECTED_TITLE_WAIT_CALLS
    assert total - titles == EXPECTED_VISIBILITY_WAIT_CALLS
