r"""Tests for the worker-local WebDriver lifecycle - ``app/automation/driver.py``.

The module under test is the Python port of the Java utility class
``com.testinium.utilities.Driver`` - cited as ``Driver.java`` - at pinned
revision ``47e9d697e4a9a85da889f94a846fdf47af28a240``, which AAP 0.2.1 holds as
REFERENCE.  This suite is the third statement of the lifecycle contract AAP
0.3.3 requires ``features/environment.py``, ``app/automation/driver.py`` and
this module to state identically:

    Each worker process holds one slot for a driver.  ``before_scenario``
    creates a driver into that slot if it is empty; ``after_scenario`` captures
    failure evidence, calls ``quit()``, and clears the slot.  Exactly one live
    session exists per worker at any moment, every scenario gets a fresh
    session, and no code ever touches a driver after ``quit()``.

What is asserted, and where it comes from
-----------------------------------------
The module under test enumerates the obligations in its own docstring ("What
``tests/test_driver.py`` asserts"), and each is anchored in a Java line by its
parity map.  Every one of them has an asserting test below:

* **One slot per worker** (``Driver.java:17``) - the session lives in a
  ``threading.local()``, so a second thread starts from an empty slot.
* **Create on demand and reuse** (``:22``) - one construction, and a second
  call returns the identical object having provisioned nothing further.
* **Provision, then construct** (``:31-32``, ``:38``) - the manager installs
  the binary, its path reaches the ``Service``, and the ``Service`` reaches the
  browser constructor, in that order.
* **Set-up order** (``:32-34``, ``:39-40``) - the slot is populated first, then
  ``maximize_window()``, then ``implicitly_wait(10)`` - seconds, never
  milliseconds.
* **Firefox provisions with Gecko** (AAP deviation 5 / Conflict 6) -
  ``Driver.java:37`` provisions the *Chrome* binary in the Firefox branch, and
  the port corrects it; the Chrome manager's call count is asserted to be zero
  so that a regression to the source's defect fails here.
* **No default branch** (``:29-42``) - ``"Chrome"``, ``"CHROME"``,
  ``" chrome"``, ``"safari"``, ``"edge"``, ``""`` and an unset key each yield
  ``None``, construct nothing, provision nothing and raise nothing, per AAP
  0.4.1: *"Any other value fails at first driver use, as today."*  There is no
  "already failed" state, so a later call tries again.
* **Guarded teardown, quit once, clear the slot** (``:51-53``).

Nothing here starts a browser, downloads a driver binary or touches the
network.  Every seam the module under test reaches outside itself - the two
managers, the two ``Service`` factories and the ``webdriver`` namespace - is
replaced by a recording double installed **on the module under test**, which is
the only patch point that works: ``get_browser`` is imported by name, so
patching ``app.config.get_browser`` would not be seen, and the same reasoning
applies to every other name the module binds at import time.

Two contracts are deliberately asserted loosely
-----------------------------------------------
Both are under active correction by the review findings against
``app/automation/driver.py``, and this suite must pass against the current form
and the corrected one alike:

**Finding F01 - the unrecognised-browser branch.**  The ``else:`` branch
currently emits a DEBUG record and F01 orders the branch deleted.  No test here
asserts that any record is emitted for an unrecognised browser; the assertions
are confined to the observable contract - ``None`` returned, nothing
constructed, nothing raised.

**Finding F02 - a ``quit()`` that fails.**  The current implementation logs at
WARNING and suppresses; F02 requires that the failure stop being converted into
success, either by propagating or by being reported structurally.
:func:`test_a_failing_quit_is_not_converted_into_success` therefore asserts only
the invariants true under both forms: ``quit()`` was attempted exactly once, the
slot is empty afterwards because the clear happens in a ``finally``, and the
failure was not silently discarded - it either propagated or was logged at
WARNING or above, carrying the exception with it.

Asserting loosely is not the same as asserting nothing
------------------------------------------------------
Both contracts above have a part that no single call can show, and that part is
asserted over the *syntax tree* of the module under test rather than over its
behaviour - see "Reading the source itself" below.  What "no default branch"
substantively forbids is a **fallback**, not a log line, so the source-level
tests pin the absence of every fallback: only ``"chrome"`` and ``"firefox"`` are
ever compared against, only ``webdriver.Chrome`` and ``webdriver.Firefox`` are
ever constructed, a session is adopted only from inside those two branches, and
the unmatched path constructs nothing, provisions nothing, adopts nothing,
raises nothing and rebinds nothing.  The behavioural half adds that the
unmatched path emits no record at WARNING or above, so it can never masquerade
as a warning that something was substituted or retried.  For ``quit_driver`` the
source-level tests pin that the clear happens in a ``finally``, that the handler
catches neither everything nor ``BaseException``, and that its body genuinely
reports what it caught.  Every one of these holds both before and after the
corrections F01 and F02 order, which is exactly why they may be asserted here.

The private seam
----------------
``driver.py`` documents ``_holder`` and ``_session()`` as the intended test
seam, private so that production code cannot orphan a live browser process with
them.  ``tests/conftest.py``'s autouse ``isolate_process_state`` fixture
replaces ``_holder`` with a fresh ``threading.local()`` around every test, so
each test below begins with an empty slot; this module reads ``_session()`` to
assert what the slot holds, and never writes to the holder.
"""

from __future__ import annotations

import ast
import logging
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, Final, NamedTuple

import pytest
from conftest import StubDriver

from app import automation, config
from app.automation import driver as driver_module

# --------------------------------------------------------------------------
# Fixed names and values
#
# The provisioned paths are obviously synthetic: nothing may execute them, and
# a test failure naming one of these strings is unambiguous about which double
# produced it.
# --------------------------------------------------------------------------

#: Path the fake ``ChromeDriverManager`` reports having installed.
CHROME_BINARY_PATH: Final[str] = "/stub/provisioned/chromedriver"

#: Path the fake ``GeckoDriverManager`` reports having installed.
GECKO_BINARY_PATH: Final[str] = "/stub/provisioned/geckodriver"

#: The implicit wait ``Driver.java:34`` sets, in **seconds**.  The Python
#: binding takes seconds, so a millisecond literal would fail the assertion in
#: :func:`test_new_session_is_maximized_then_given_a_ten_second_implicit_wait`.
IMPLICIT_WAIT_SECONDS: Final[int] = 10

# Recorded event names.  One constant per observable interaction, so that a
# sequence assertion reads as the Java branch it ports.
CHROME_MANAGER_CONSTRUCT: Final[str] = "ChromeDriverManager()"
CHROME_MANAGER_INSTALL: Final[str] = "ChromeDriverManager.install"
GECKO_MANAGER_CONSTRUCT: Final[str] = "GeckoDriverManager()"
GECKO_MANAGER_INSTALL: Final[str] = "GeckoDriverManager.install"
CHROME_SERVICE: Final[str] = "ChromeService"
FIREFOX_SERVICE: Final[str] = "FirefoxService"
WEBDRIVER_CHROME: Final[str] = "webdriver.Chrome"
WEBDRIVER_FIREFOX: Final[str] = "webdriver.Firefox"

#: The whole of the Chrome branch, in order - ``Driver.java:31-32``.
CHROME_BRANCH_SEQUENCE: Final[tuple[str, ...]] = (
    CHROME_MANAGER_CONSTRUCT,
    CHROME_MANAGER_INSTALL,
    CHROME_SERVICE,
    WEBDRIVER_CHROME,
)

#: The whole of the Firefox branch, in order - ``Driver.java:37-38`` with the
#: manager corrected to Gecko (AAP deviation 5).
FIREFOX_BRANCH_SEQUENCE: Final[tuple[str, ...]] = (
    GECKO_MANAGER_CONSTRUCT,
    GECKO_MANAGER_INSTALL,
    FIREFOX_SERVICE,
    WEBDRIVER_FIREFOX,
)

#: The two settings ``_adopt_session`` applies, in the Java order ``:33`` then
#: ``:34``, as :meth:`conftest.StubDriver.operations` reports them.
SETUP_OPERATIONS: Final[tuple[str, ...]] = ("maximize_window", "implicitly_wait")

#: Browser values that match neither branch.  The first three are the Java
#: ``switch``'s case sensitivity and lack of trimming - ``equals`` comparison,
#: so normalizing them would accept input the source rejects; the next three
#: are plain unrecognised values; ``None`` is the key being unset, which
#: ``app/config.py`` returns unchanged.
UNRECOGNISED_BROWSERS: Final[tuple[str | None, ...]] = (
    "Chrome",
    "CHROME",
    " chrome",
    "safari",
    "edge",
    "",
    None,
)

#: Logger under which a WARNING from the module under test arrives.  Used only
#: by the F02 disjunction, to keep an unrelated record from satisfying it.
DRIVER_LOGGER_PREFIX: Final[str] = "app.automation"

#: The package logger records propagate to; its level is raised for the F02
#: capture so that a WARNING cannot be filtered before ``caplog`` sees it.
APP_LOGGER_NAME: Final[str] = "app"


# --------------------------------------------------------------------------
# The recording doubles
#
# Deliberately not ``unittest.mock``: the assertions below are about the
# *order* of events across four collaborators, which one shared ordered log
# expresses directly and four independent mock call lists do not.
# --------------------------------------------------------------------------


class RecordedCall:
    """One entry of :class:`CallRecorder`'s ordered log.

    :param target: The event name, one of the module constants above.
    :param args: Positional arguments exactly as they were passed.
    :param kwargs: Keyword arguments exactly as they were passed.
    """

    __slots__ = ("args", "kwargs", "target")

    def __init__(self, target: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        self.target = target
        self.args = args
        self.kwargs = kwargs

    def values(self) -> tuple[Any, ...]:
        """Every argument value, positional first.

        Used where the contract is *what* was handed over rather than how: the
        provisioned path has to reach the ``Service`` factory, and whether the
        port passes it positionally or by keyword is not part of the contract.

        :returns: Positional arguments followed by keyword argument values.
        """
        return self.args + tuple(self.kwargs.values())

    def __repr__(self) -> str:
        """Render the call as it was made, which is what a failure needs.

        :returns: A short representation.
        """
        return f"<RecordedCall {self.target} args={self.args!r} kwargs={self.kwargs!r}>"


class CallRecorder:
    """One ordered log shared by every double in a :class:`DriverHarness`.

    A single log is what makes "provision before construct" assertable: the
    events of both collaborators appear in one sequence, so their relative
    order is a property of the log rather than something a test has to infer.
    """

    __slots__ = ("calls",)

    def __init__(self) -> None:
        #: Every recorded call, in the order it happened.
        self.calls: list[RecordedCall] = []

    def record(self, target: str, *args: Any, **kwargs: Any) -> None:
        """Append one event to the log.

        :param target: The event name.
        :param args: Positional arguments, stored unmodified.
        :param kwargs: Keyword arguments, stored unmodified.
        :returns: ``None``.
        """
        self.calls.append(RecordedCall(target, args, dict(kwargs)))

    def targets(self) -> tuple[str, ...]:
        """The event names in order, with arguments dropped.

        :returns: The event names in call order.
        """
        return tuple(call.target for call in self.calls)

    def calls_to(self, target: str) -> tuple[RecordedCall, ...]:
        """Every recorded call to one event name, in order.

        :param target: The event name.
        :returns: The matching calls, in call order.
        """
        return tuple(call for call in self.calls if call.target == target)

    def count_of(self, target: str) -> int:
        """How many times one event was recorded.

        The direct expression of the two "never touched" obligations: the
        Chrome manager's count in the Firefox branch, and every count in the
        unrecognised-browser case.

        :param target: The event name.
        :returns: The number of occurrences.
        """
        return sum(1 for call in self.calls if call.target == target)

    def __repr__(self) -> str:
        """Summarise the log as its event sequence.

        :returns: A short representation.
        """
        return f"<CallRecorder {self.targets()!r}>"


class FakeDriverManager:
    """Stand-in for ``ChromeDriverManager`` / ``GeckoDriverManager``.

    Callable, because the module under test calls the *class* -
    ``ChromeDriverManager()`` - and then ``.install()`` on the result.  The call
    returns ``self``, so one object records both events and a test can read the
    construction and the installation as two entries of one log.

    Nothing is downloaded and no network call is made: :meth:`install` returns
    a fixed synthetic path.

    :param recorder: The shared log.
    :param construct_target: Event name recorded when the class is called.
    :param install_target: Event name recorded by :meth:`install`.
    :param binary_path: The path :meth:`install` reports.
    """

    __slots__ = ("_binary_path", "_construct_target", "_install_target", "_recorder")

    def __init__(
        self,
        recorder: CallRecorder,
        construct_target: str,
        install_target: str,
        binary_path: str,
    ) -> None:
        self._recorder = recorder
        self._construct_target = construct_target
        self._install_target = install_target
        self._binary_path = binary_path

    def __call__(self, *args: Any, **kwargs: Any) -> FakeDriverManager:
        """Record the manager's construction and return the recorder itself.

        :param args: Constructor positional arguments, recorded as passed.
        :param kwargs: Constructor keyword arguments, recorded as passed.
        :returns: ``self``, so that ``.install()`` lands in the same log.
        """
        self._recorder.record(self._construct_target, *args, **kwargs)
        return self

    def install(self) -> str:
        """Record the provisioning and return the synthetic binary path.

        :returns: :attr:`_binary_path`.
        """
        self._recorder.record(self._install_target)
        return self._binary_path


class ServiceDouble:
    """Opaque stand-in for a selenium ``Service`` object.

    Its only jobs are to be identifiable - so a test can assert that the object
    the ``Service`` factory produced is the object the browser constructor
    received - and to carry what it was built from.

    :param kind: Which factory produced it, for readable failures.
    :param carried: Every argument value the factory was given.
    """

    __slots__ = ("carried", "kind")

    def __init__(self, kind: str, carried: tuple[Any, ...]) -> None:
        self.kind = kind
        self.carried = carried

    def __repr__(self) -> str:
        """Render the kind and the carried path.

        :returns: A short representation.
        """
        return f"<ServiceDouble {self.kind} carrying {self.carried!r}>"


class FakeServiceFactory:
    """Stand-in for ``ChromeService`` / ``FirefoxService``.

    :param recorder: The shared log.
    :param target: Event name recorded on each call.
    :param kind: Passed through to the :class:`ServiceDouble` it produces.
    """

    __slots__ = ("_kind", "_recorder", "_target", "produced")

    def __init__(self, recorder: CallRecorder, target: str, kind: str) -> None:
        self._recorder = recorder
        self._target = target
        self._kind = kind
        #: Every service object handed out, in order.
        self.produced: list[ServiceDouble] = []

    def __call__(self, *args: Any, **kwargs: Any) -> ServiceDouble:
        """Record the call and produce a fresh, identifiable service object.

        :param args: Positional arguments, recorded as passed.
        :param kwargs: Keyword arguments, recorded as passed.
        :returns: A new :class:`ServiceDouble` carrying those values.
        """
        self._recorder.record(self._target, *args, **kwargs)
        service = ServiceDouble(self._kind, args + tuple(kwargs.values()))
        self.produced.append(service)
        return service


class FakeBrowserConstructor:
    """Stand-in for ``webdriver.Chrome`` / ``webdriver.Firefox``.

    No browser process is started: the call is recorded and a stubbed session
    is handed back, which is what makes the maximize, the implicit wait and the
    ``quit()`` observable.

    :param recorder: The shared log.
    :param target: Event name recorded on each call.
    :param session_source: Callable producing the session to return.
    """

    __slots__ = ("_recorder", "_session_source", "_target")

    def __init__(
        self,
        recorder: CallRecorder,
        target: str,
        session_source: Callable[[], Any],
    ) -> None:
        self._recorder = recorder
        self._target = target
        self._session_source = session_source

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """Record the construction and return a stubbed session.

        :param args: Positional arguments, recorded as passed - the "bare
            construction" assertions read them to prove there were none.
        :param kwargs: Keyword arguments, recorded as passed.
        :returns: The next session from :attr:`_session_source`.
        """
        self._recorder.record(self._target, *args, **kwargs)
        return self._session_source()


class FakeWebDriverNamespace:
    """Stand-in for the ``webdriver`` name ``driver.py`` binds at import.

    Patching the whole namespace rather than attributes of the real
    ``selenium.webdriver`` module keeps the replacement local to the module
    under test, so no other importer of selenium can see it.

    :param recorder: The shared log.
    :param session_source: Callable producing the session each constructor
        returns.
    """

    __slots__ = ("Chrome", "Firefox")

    def __init__(self, recorder: CallRecorder, session_source: Callable[[], Any]) -> None:
        # Capitalised to match the selenium names the module under test calls.
        self.Chrome = FakeBrowserConstructor(recorder, WEBDRIVER_CHROME, session_source)
        self.Firefox = FakeBrowserConstructor(recorder, WEBDRIVER_FIREFOX, session_source)


class FailingQuitSession:
    """A session whose ``quit()`` always raises, counting its attempts.

    ``conftest.StubDriver`` declares ``__slots__`` and offers no failing-quit
    seam - deliberately, since it counts ``quit()`` rather than judging it - so
    the F02 disjunction needs this local double.  It implements exactly the
    three methods ``driver.py`` calls on a session.

    :param error: The exception ``quit()`` raises.
    """

    __slots__ = ("_error", "implicit_waits", "maximize_count", "quit_count")

    def __init__(self, error: BaseException) -> None:
        self._error = error
        #: How many times ``quit()`` was attempted, failure included.
        self.quit_count = 0
        self.maximize_count = 0
        #: Every implicit wait applied, in order.
        self.implicit_waits: list[float] = []

    def maximize_window(self) -> None:
        """Accept the maximize of ``Driver.java:33``.

        :returns: ``None``.
        """
        self.maximize_count += 1

    def implicitly_wait(self, seconds: float) -> None:
        """Accept the implicit wait of ``Driver.java:34``.

        :param seconds: Timeout in seconds, recorded unconverted.
        :returns: ``None``.
        """
        self.implicit_waits.append(seconds)

    def quit(self) -> None:
        """Count the attempt, then fail it.

        :returns: Never returns.
        :raises BaseException: The error this double was built with.
        """
        self.quit_count += 1
        raise self._error


class DriverHarness:
    """Every collaborator of ``driver.py``, replaced by a recording double.

    Installed by the :func:`driver_harness` fixture.  Sessions are handed out
    one per browser construction: a queued session when a test has programmed
    one, otherwise a fresh :class:`conftest.StubDriver`.  A new object per
    construction is what makes "the next ``get_driver()`` builds a *new*
    session" assertable by identity.
    """

    __slots__ = (
        "chrome_manager",
        "chrome_service",
        "firefox_service",
        "gecko_manager",
        "queued",
        "recorder",
        "sessions",
        "webdriver",
    )

    def __init__(self) -> None:
        self.recorder = CallRecorder()

        #: Sessions a test has programmed, consumed in order.
        self.queued: list[Any] = []

        #: Every session handed out, in construction order.
        self.sessions: list[Any] = []

        self.chrome_manager = FakeDriverManager(
            self.recorder,
            CHROME_MANAGER_CONSTRUCT,
            CHROME_MANAGER_INSTALL,
            CHROME_BINARY_PATH,
        )
        self.gecko_manager = FakeDriverManager(
            self.recorder,
            GECKO_MANAGER_CONSTRUCT,
            GECKO_MANAGER_INSTALL,
            GECKO_BINARY_PATH,
        )
        self.chrome_service = FakeServiceFactory(self.recorder, CHROME_SERVICE, "chrome")
        self.firefox_service = FakeServiceFactory(self.recorder, FIREFOX_SERVICE, "firefox")
        self.webdriver = FakeWebDriverNamespace(self.recorder, self._next_session)

    def queue_session(self, session: Any) -> Any:
        """Program the session the next browser construction returns.

        :param session: Any object implementing the three methods
            ``driver.py`` calls - ``maximize_window``, ``implicitly_wait`` and
            ``quit``.
        :returns: ``session``, so a test can queue and keep it in one line.
        """
        self.queued.append(session)
        return session

    def _next_session(self) -> Any:
        """Hand out the next session and remember it.

        :returns: The oldest queued session, or a fresh
            :class:`conftest.StubDriver` when none is queued.
        """
        session = self.queued.pop(0) if self.queued else StubDriver()
        self.sessions.append(session)
        return session


def one_call(recorder: CallRecorder, target: str) -> RecordedCall:
    """Return the single recorded call to ``target``.

    :param recorder: The log to read.
    :param target: The event name that must have happened exactly once.
    :returns: That one call.
    """
    calls = recorder.calls_to(target)
    assert len(calls) == 1, f"expected exactly one {target}, recorded {calls!r}"
    return calls[0]


# --------------------------------------------------------------------------
# Reading the source itself
#
# Three obligations of the module under test are properties of its *code*
# rather than of any one call, and no stubbed call can demonstrate them: that
# no third browser name can be compared against, that no third browser can be
# constructed, and that a session which could not be closed is forgotten and
# its failure reported anyway.  Each is asserted over the syntax tree of
# ``app/automation/driver.py``.
#
# Parsed with :mod:`ast` and never matched with a regular expression.  The
# module under test discusses its absent default branch, both browser names and
# its suppression at length in prose, and one of its log messages quotes both
# names, so a text search over the file is answered by the documentation and
# says nothing whatever about the code.  Parsing also classifies a comparison
# buried in a nested expression, an aliased call and a name that appears only
# inside a docstring correctly, with no pattern to maintain against them.
# --------------------------------------------------------------------------

#: The two browser names the port matches - ``Driver.java:30`` and ``:36``.
#: These are the *only* string literals ``get_driver`` may compare the
#: configured value against; a third member here would be a fallback.
BROWSER_NAMES_IN_SOURCE: Final[frozenset[str]] = frozenset({"chrome", "firefox"})

#: The two browser constructors the module may reference, as attributes of the
#: ``webdriver`` namespace it binds at import - ``Driver.java:32`` and ``:38``.
BROWSER_CONSTRUCTORS_IN_SOURCE: Final[frozenset[str]] = frozenset({"Chrome", "Firefox"})

#: The name the module under test binds the selenium namespace to.
WEBDRIVER_NAMESPACE: Final[str] = "webdriver"

#: The private helper that takes ownership of a freshly constructed session.
#: Every call to it is a session reaching the slot, so where those calls sit is
#: where the slot can be written from.
ADOPT_SESSION: Final[str] = "_adopt_session"

#: The private helper that empties the slot - the port of ``driverPool.remove()``.
CLEAR_SESSION: Final[str] = "_clear_session"

#: The module-private holder itself.  No assignment to an attribute of it may
#: appear in ``get_driver``.
HOLDER_NAME: Final[str] = "_holder"

#: The name whose appearance in an ``except`` clause would let a
#: ``KeyboardInterrupt`` or a worker shutdown be swallowed by teardown.
BASE_EXCEPTION_NAME: Final[str] = "BaseException"

#: The suffixes that identify a driver-binary manager and a selenium service
#: factory by name, used by :func:`is_fallback_call` so that the check does not
#: depend on a hand-maintained list of vendor class names.
MANAGER_SUFFIX: Final[str] = "DriverManager"
SERVICE_SUFFIX: Final[str] = "Service"

#: Logger used once per unrecognised-browser test to prove the log capture is
#: live, so that a negative assertion about records cannot pass vacuously.
PROBE_LOGGER_NAME: Final[str] = f"{DRIVER_LOGGER_PREFIX}.capture_liveness_probe"


class BrowserDispatch(NamedTuple):
    """The three paths of ``get_driver``'s browser dispatch, located in the tree.

    :param chrome_body: The statements of the ``"chrome"`` branch.
    :param firefox_body: The statements of the ``"firefox"`` branch.
    :param unmatched: The statements an unrecognised name falls through to -
        empty once review finding F01 deletes the ``else:``.
    """

    chrome_body: tuple[ast.stmt, ...]
    firefox_body: tuple[ast.stmt, ...]
    unmatched: tuple[ast.stmt, ...]


def function_named(tree: ast.Module, name: str) -> ast.FunctionDef:
    """Return the top-level function definition called ``name``.

    :param tree: The parsed module.
    :param name: The function's name.
    :returns: Its definition node.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node

    pytest.fail(f"{name}() is not defined in the module under test")


def constant_strings(node: ast.AST) -> frozenset[str]:
    """Every string literal appearing anywhere inside one subtree.

    :param node: The subtree to read.
    :returns: The distinct string literal values it contains.
    """
    return frozenset(
        inner.value
        for inner in ast.walk(node)
        if isinstance(inner, ast.Constant) and isinstance(inner.value, str)
    )


def decision_strings(node: ast.AST) -> frozenset[str]:
    """Every string literal a subtree *compares against*.

    Only literals reached from a comparison count - the operands of a
    ``Compare`` node, and the pattern of a ``match`` case, so a rewrite of the
    dispatch from ``==`` to ``match`` would be read the same way.  A literal in
    a docstring, a log message or a comment is not a decision and is therefore
    not collected, which is the whole reason this is a tree walk rather than a
    text search.

    :param node: The subtree to read.
    :returns: The distinct literals it decides on.
    """
    found: set[str] = set()

    for inner in ast.walk(node):
        if isinstance(inner, ast.Compare):
            for side in (inner.left, *inner.comparators):
                found |= constant_strings(side)
        elif isinstance(inner, ast.MatchValue):
            found |= constant_strings(inner.value)

    return frozenset(found)


def subtree_ids(*nodes: ast.AST) -> frozenset[int]:
    """Identities of every node inside the given subtrees, roots included.

    Membership of this set is what "lexically inside" means below: a node is
    inside a branch, or inside a ``finally``, exactly when its identity appears
    in that region's set.  Identity rather than line number, so a reformatting
    of the module under test cannot change the answer.

    :param nodes: The subtree roots.
    :returns: ``id()`` of every node they contain.
    """
    return frozenset(id(inner) for node in nodes for inner in ast.walk(node))


def annotation_ids(tree: ast.AST) -> frozenset[int]:
    """Identities of every node that appears inside a type annotation.

    The module under test annotates with ``webdriver.Remote | None``, so the
    ``webdriver`` namespace is legitimately named outside a call.  Exempting
    annotations *by position* rather than exempting ``Remote`` by name is what
    keeps :func:`test_only_chrome_and_firefox_are_ever_constructed` honest: a
    constructor call can never be in annotation position, so nothing a
    re-annotation does can create a hole in that test.

    :param tree: The subtree to read.
    :returns: ``id()`` of every node reachable from an annotation.
    """
    annotations: list[ast.AST] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.arg) and node.annotation is not None:
            annotations.append(node.annotation)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.returns is not None:
            annotations.append(node.returns)
        elif isinstance(node, ast.AnnAssign):
            annotations.append(node.annotation)

    return subtree_ids(*annotations)


def callee_name(call: ast.Call) -> str | None:
    """The simple name being called, for a direct or an attribute call.

    :param call: The call node.
    :returns: ``f`` for ``f(...)``, ``attr`` for ``obj.attr(...)``, and
        ``None`` for anything else - a call of a subscript or of another call.
    """
    func = call.func

    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr

    return None


def calls_named(name: str, *nodes: ast.AST) -> tuple[ast.Call, ...]:
    """Every call to ``name`` anywhere inside the given subtrees.

    :param name: The callee name to look for.
    :param nodes: The subtrees to search.
    :returns: The matching call nodes, in tree order.
    """
    return tuple(
        inner
        for node in nodes
        for inner in ast.walk(node)
        if isinstance(inner, ast.Call) and callee_name(inner) == name
    )


def is_fallback_call(call: ast.Call) -> bool:
    """Whether one call would turn the unmatched browser path into a fallback.

    Four kinds of call are disqualifying, and between them they cover every
    fallback AAP 0.4.1 forbids: constructing any browser through the
    ``webdriver`` namespace, constructing one through a directly imported
    constructor, provisioning a driver binary through any ``*DriverManager``,
    building any ``*Service`` for one, and adopting a session.  Named by shape
    rather than by a list of vendor class names, so a manager or a constructor
    this suite has never heard of is caught too.

    :param call: The call node.
    :returns: ``True`` when the call constructs, provisions or adopts.
    """
    func = call.func

    if (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == WEBDRIVER_NAMESPACE
    ):
        return True

    name = callee_name(call)

    if name is None:
        return False

    return (
        name == ADOPT_SESSION
        or name in BROWSER_CONSTRUCTORS_IN_SOURCE
        or name.endswith(MANAGER_SUFFIX)
        or name.endswith(SERVICE_SUFFIX)
    )


def browser_dispatch(function: ast.FunctionDef) -> BrowserDispatch:
    """Locate the two browser branches and the path an unmatched name takes.

    A branch is identified by what it *decides on* rather than by its position,
    so the chain may be written as ``if``/``elif`` or as two separate ``if``
    statements and this still finds it.  The unmatched path is what the chain
    falls through to: the ``else`` of its last link, plus anything the first
    link's ``else`` holds besides the link itself.  With review finding F01
    applied the ``else`` is gone and that path is empty, which every assertion
    over it tolerates.

    :param function: The ``get_driver`` definition.
    :returns: The three paths.
    """
    chrome_if: ast.If | None = None
    firefox_if: ast.If | None = None

    for node in ast.walk(function):
        if not isinstance(node, ast.If):
            continue

        decided = decision_strings(node.test)

        if decided == frozenset({"chrome"}):
            chrome_if = node
        elif decided == frozenset({"firefox"}):
            firefox_if = node

    if chrome_if is None or firefox_if is None:
        pytest.fail(
            "get_driver() must dispatch on 'chrome' and on 'firefox', one "
            "branch each; no such pair of branches was found in the tree"
        )

    unmatched = [statement for statement in chrome_if.orelse if statement is not firefox_if]
    unmatched.extend(firefox_if.orelse)

    return BrowserDispatch(
        chrome_body=tuple(chrome_if.body),
        firefox_body=tuple(firefox_if.body),
        unmatched=tuple(unmatched),
    )


def except_handlers(function: ast.FunctionDef) -> tuple[ast.ExceptHandler, ...]:
    """Every ``except`` clause inside one function.

    :param function: The definition to read.
    :returns: The handler nodes, in tree order.
    """
    return tuple(node for node in ast.walk(function) if isinstance(node, ast.ExceptHandler))


def caught_exception_names(handler: ast.ExceptHandler) -> frozenset[str]:
    """The exception names one handler catches.

    Tuples are flattened and dotted names are reduced to their final
    attribute, so ``except (ValueError, builtins.BaseException)`` reports both.

    :param handler: The handler node.
    :returns: The names it catches; empty for a bare ``except:``.
    """
    if handler.type is None:
        return frozenset()

    names: set[str] = set()

    for node in ast.walk(handler.type):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)

    return frozenset(names)


def carries_exception_information(call: ast.Call) -> bool:
    """Whether one logging call reports the exception rather than only a message.

    Two shapes carry it: ``logger.exception(...)``, which attaches the active
    exception by definition, and any call passing ``exc_info``.  An explicit
    ``exc_info=False`` or ``exc_info=None`` carries nothing and is rejected, so
    the disabling form cannot satisfy the check the enabling form exists for.

    :param call: The call node.
    :returns: ``True`` when the call reports the exception.
    """
    if callee_name(call) == "exception":
        return True

    for keyword in call.keywords:
        if keyword.arg != "exc_info":
            continue

        disabled = isinstance(keyword.value, ast.Constant) and not keyword.value.value

        if not disabled:
            return True

    return False


def handler_reports_failure(handler: ast.ExceptHandler) -> bool:
    """Whether a handler's own body reports what it caught.

    Read over the handler's body and nothing else, because the question is what
    *this* handler does: a ``raise`` elsewhere in the file, or a logging call in
    a neighbouring function, says nothing about it.  Three shapes report, and
    they are exactly the ones review finding F02's resolution leaves open -
    re-raising, logging with the exception attached, or returning a value that
    describes the failure.  A handler that only ``pass``es, or that logs a
    message without the exception, reports nothing and fails this.

    :param handler: The handler node.
    :returns: ``True`` when the failure leaves the handler visible.
    """
    for statement in handler.body:
        for node in ast.walk(statement):
            if isinstance(node, ast.Raise):
                return True

            if isinstance(node, ast.Return) and not (
                node.value is None
                or (isinstance(node.value, ast.Constant) and node.value.value is None)
            ):
                return True

            if isinstance(node, ast.Call) and carries_exception_information(node):
                return True

    return False


class AutomationLogCapture(logging.Handler):
    """Every record emitted anywhere under the ``app.automation`` logger.

    ``caplog`` observes records that reach the root logger, and
    ``app/logging_config.py``'s ``configure_logging()`` sets
    ``propagate = False`` on the ``app`` logger - so in a full-suite run, where
    another module may have configured logging, a root-level capture can miss a
    record that was genuinely emitted.  A handler installed directly on
    ``app.automation`` cannot: records from ``app.automation.driver`` reach it
    before any ancestor's propagation flag is consulted.  That matters most for
    the negative assertion, where a capture that silently sees nothing would
    look exactly like a module that emitted nothing.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.NOTSET)

        #: Every record seen, in emission order.
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        """Store one record.

        :param record: The record being emitted.
        :returns: ``None``.
        """
        self.records.append(record)

    def at_or_above(self, level: int) -> tuple[logging.LogRecord, ...]:
        """Every captured record at or above one level.

        :param level: The threshold, as a :mod:`logging` level number.
        :returns: The matching records, in emission order.
        """
        return tuple(record for record in self.records if record.levelno >= level)


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def driver_harness(monkeypatch: pytest.MonkeyPatch) -> DriverHarness:
    """Install a :class:`DriverHarness` over every seam of ``driver.py``.

    Patched **on the module under test**, which is the only patch point that
    works for names bound at import time, and the only one that leaves other
    importers of selenium untouched.  ``monkeypatch.setattr`` is used in its
    default raising mode on purpose: if the module under test stops binding one
    of these five names, this fixture fails loudly instead of quietly leaving a
    real manager or a real browser constructor in place.

    After this fixture runs, nothing reachable from ``get_driver()`` can start
    a browser, download a binary or open a socket.

    :param monkeypatch: pytest's patcher, for its guaranteed teardown.
    :returns: The installed harness.
    """
    harness = DriverHarness()

    monkeypatch.setattr(driver_module, "ChromeDriverManager", harness.chrome_manager)
    monkeypatch.setattr(driver_module, "GeckoDriverManager", harness.gecko_manager)
    monkeypatch.setattr(driver_module, "ChromeService", harness.chrome_service)
    monkeypatch.setattr(driver_module, "FirefoxService", harness.firefox_service)
    monkeypatch.setattr(driver_module, "webdriver", harness.webdriver)

    return harness


@pytest.fixture
def browser_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[str | None], None]]:
    """Yield a setter for the ``browser`` configuration key.

    A configured value is installed through the public API
    ``app.config.set_userdata``, the mechanism ``tests/conftest.py`` documents:
    userdata takes precedence over the properties file, and membership decides,
    so ``""`` is installed as an empty value rather than falling through to the
    file.  The cleanup below runs in a ``finally``, so a failing assertion
    cannot leak userdata into a later test - the same guarantee conftest's
    "register the finalizer before installing" note asks for.

    An **unset** key is arranged by patching ``get_browser`` on the module under
    test instead.  That is deliberate: clearing userdata would fall through to
    ``configuration.properties`` in the process working directory, and whether
    that file exists is not something this suite may depend on.

    :param monkeypatch: pytest's patcher, used for the unset case.
    :yields: A callable taking the browser value, or ``None`` for an unset key.
    """

    def install(value: str | None) -> None:
        if value is None:
            monkeypatch.setattr(driver_module, "get_browser", lambda: None)
            return

        config.set_userdata({"browser": value})

    try:
        yield install
    finally:
        # ``None`` clears the slot and restores the file-only path, which is
        # the whole of the cleanup app/config.py requires.
        config.set_userdata(None)


@pytest.fixture(scope="module")
def driver_source() -> ast.Module:
    """The parsed syntax tree of ``app/automation/driver.py``.

    Parsed from ``driver_module.__file__`` rather than from a path spelled out
    here, so the source-level assertions cannot drift onto a stale copy of the
    file: they read the very module the behavioural tests import.

    Module-scoped because a syntax tree is an immutable derived value - no test
    below mutates it, so there is nothing one test could hand to the next, and
    the file is read once instead of once per assertion.

    :returns: The module's AST.
    """
    source_path = Path(driver_module.__file__)

    return ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))


@pytest.fixture
def automation_log() -> Iterator[AutomationLogCapture]:
    """Capture every record emitted under the ``app.automation`` logger.

    The handler is installed on ``app.automation`` itself and the logger's level
    is lowered to DEBUG for the duration, so nothing a record's own level or an
    ancestor's propagation flag does can hide it - see
    :class:`AutomationLogCapture` for why a root-level capture is not enough
    here.  Both the level and the handler are restored in a ``finally``, so a
    failing assertion cannot leave the logger reconfigured for a later test.

    :yields: The installed capture.
    """
    automation_logger = logging.getLogger(DRIVER_LOGGER_PREFIX)
    capture = AutomationLogCapture()
    previous_level = automation_logger.level

    automation_logger.addHandler(capture)
    automation_logger.setLevel(logging.DEBUG)

    try:
        yield capture
    finally:
        automation_logger.setLevel(previous_level)
        automation_logger.removeHandler(capture)


# --------------------------------------------------------------------------
# Creation, reuse and the worker-local slot
# --------------------------------------------------------------------------


def test_session_is_created_on_demand_and_then_reused(
    driver_harness: DriverHarness,
    browser_key: Callable[[str | None], None],
) -> None:
    """Pin create-on-demand and reuse - ``Driver.java:22`` and ``:45``.

    The first call constructs exactly one session; the second returns the
    identical object, having provisioned nothing and constructed nothing
    further, which is what the Java comment means by "will return same driver
    instance when we call it".  The log is asserted whole after the second
    call, so any extra event - a second install, a second constructor call -
    fails here.
    """
    browser_key("chrome")

    first = driver_module.get_driver()

    assert first is not None
    assert driver_harness.sessions == [first]
    assert driver_harness.recorder.targets() == CHROME_BRANCH_SEQUENCE

    second = driver_module.get_driver()

    assert second is first
    assert driver_harness.recorder.targets() == CHROME_BRANCH_SEQUENCE
    assert driver_harness.recorder.count_of(CHROME_MANAGER_INSTALL) == 1
    assert driver_harness.recorder.count_of(WEBDRIVER_CHROME) == 1
    assert driver_harness.sessions == [first]


def test_the_session_lives_in_the_workers_own_slot(
    driver_harness: DriverHarness,
    browser_key: Callable[[str | None], None],
) -> None:
    """Pin one slot per worker - ``Driver.java:17``'s ``InheritableThreadLocal``.

    ``driver.py`` states that a plain module global is deliberately not used.
    A second thread therefore starts from an empty slot and builds its own
    session, which is exactly the storage semantics of the Java field and the
    reason AAP 0.3.3's "exactly one live session exists per worker" holds
    whatever else shares the process.
    """
    browser_key("chrome")

    first = driver_module.get_driver()

    assert isinstance(driver_module._holder, threading.local)
    assert driver_module._session() is first

    from_other_thread: list[Any] = []
    thread = threading.Thread(
        target=lambda: from_other_thread.append(driver_module.get_driver()),
        name="driver-slot-probe",
    )
    thread.start()
    thread.join(timeout=30)

    assert not thread.is_alive()
    assert len(from_other_thread) == 1
    assert from_other_thread[0] is not first
    assert driver_module._session() is first
    assert driver_harness.recorder.count_of(WEBDRIVER_CHROME) == 2


def test_new_session_is_maximized_then_given_a_ten_second_implicit_wait(
    driver_harness: DriverHarness,
    browser_key: Callable[[str | None], None],
    stub_driver: StubDriver,
) -> None:
    """Pin the set-up order - ``Driver.java:33`` before ``:34``.

    Two things at once: the window is maximized *before* the implicit wait is
    applied, and the wait receives exactly ``10``.  The Python binding takes
    seconds, so a millisecond literal - ``10000``, as a Java-to-Python
    transcription might produce - fails the second assertion.
    """
    browser_key("chrome")
    driver_harness.queue_session(stub_driver)

    session = driver_module.get_driver()

    assert session is stub_driver
    assert stub_driver.operations() == SETUP_OPERATIONS
    assert stub_driver.calls_of("implicitly_wait") == ((IMPLICIT_WAIT_SECONDS,),)


# --------------------------------------------------------------------------
# The two branches of the switch
# --------------------------------------------------------------------------


def test_chrome_branch_provisions_before_constructing_bare(
    driver_harness: DriverHarness,
    browser_key: Callable[[str | None], None],
) -> None:
    """Pin the Chrome branch - ``Driver.java:30-35``.

    The full event sequence establishes the ordering the Selenium 3-to-4 note
    in ``driver.py`` describes: the manager installs the binary, the returned
    path is handed to ``ChromeService``, and that service object - by identity,
    not by shape - reaches ``webdriver.Chrome``.  Provisioning happens strictly
    before construction.

    The browser is constructed **bare**: no positional argument and no keyword
    other than ``service``.  ``Driver.java:32`` passes no capability object,
    and AAP 0.6 reserves the browser-locale question, so a capabilities or
    options argument appearing here would silently settle it.
    """
    browser_key("chrome")

    session = driver_module.get_driver()

    assert driver_harness.recorder.targets() == CHROME_BRANCH_SEQUENCE

    service_call = one_call(driver_harness.recorder, CHROME_SERVICE)
    assert CHROME_BINARY_PATH in service_call.values()

    assert len(driver_harness.chrome_service.produced) == 1
    service = driver_harness.chrome_service.produced[0]

    browser_call = one_call(driver_harness.recorder, WEBDRIVER_CHROME)
    assert browser_call.args == ()
    assert set(browser_call.kwargs) == {"service"}
    assert browser_call.kwargs["service"] is service

    assert session is driver_harness.sessions[0]
    assert driver_harness.recorder.count_of(GECKO_MANAGER_CONSTRUCT) == 0
    assert driver_harness.recorder.count_of(GECKO_MANAGER_INSTALL) == 0
    assert driver_harness.recorder.count_of(WEBDRIVER_FIREFOX) == 0


def test_firefox_branch_provisions_with_gecko_and_never_with_chrome(
    driver_harness: DriverHarness,
    browser_key: Callable[[str | None], None],
) -> None:
    """Pin AAP deviation 5 / Conflict 6 - the corrected defect.

    ``Driver.java:37`` calls ``WebDriverManager.chromedriver().setup()`` inside
    the *Firefox* branch, immediately before constructing a ``FirefoxDriver``.
    AAP Conflict 6 corrects it because reproducing it would provision the wrong
    binary, and AAP 0.8 requires the correction to stay individually
    reversible.  The Chrome manager's call counts are asserted to be **zero**
    explicitly: a test that merely checked that Gecko was used would pass while
    the source's defect was reintroduced alongside it.

    As in the Chrome branch, the browser is constructed bare and the
    provisioned path travels through the ``Service`` object by identity.
    """
    browser_key("firefox")

    session = driver_module.get_driver()

    assert driver_harness.recorder.targets() == FIREFOX_BRANCH_SEQUENCE

    service_call = one_call(driver_harness.recorder, FIREFOX_SERVICE)
    assert GECKO_BINARY_PATH in service_call.values()

    assert len(driver_harness.firefox_service.produced) == 1
    service = driver_harness.firefox_service.produced[0]

    browser_call = one_call(driver_harness.recorder, WEBDRIVER_FIREFOX)
    assert browser_call.args == ()
    assert set(browser_call.kwargs) == {"service"}
    assert browser_call.kwargs["service"] is service

    assert session is driver_harness.sessions[0]

    # The whole point of deviation 5: the Chrome binary is never provisioned
    # while constructing Firefox.
    assert driver_harness.recorder.count_of(CHROME_MANAGER_CONSTRUCT) == 0
    assert driver_harness.recorder.count_of(CHROME_MANAGER_INSTALL) == 0
    assert driver_harness.recorder.count_of(WEBDRIVER_CHROME) == 0


def test_firefox_session_is_maximized_then_given_the_same_implicit_wait(
    driver_harness: DriverHarness,
    browser_key: Callable[[str | None], None],
    stub_driver: StubDriver,
) -> None:
    """Pin the set-up order on the second branch - ``Driver.java:39-40``.

    The Java branches apply identical treatment by copy-paste, so the port
    factors it into one function.  Asserting it on Firefox as well as on Chrome
    is what keeps that factoring honest: a branch that skipped the shared tail
    would pass the Chrome test alone.
    """
    browser_key("firefox")
    driver_harness.queue_session(stub_driver)

    session = driver_module.get_driver()

    assert session is stub_driver
    assert stub_driver.operations() == SETUP_OPERATIONS
    assert stub_driver.calls_of("implicitly_wait") == ((IMPLICIT_WAIT_SECONDS,),)


# --------------------------------------------------------------------------
# No default branch
# --------------------------------------------------------------------------


@pytest.mark.parametrize("browser", UNRECOGNISED_BROWSERS)
def test_unrecognised_browser_yields_no_session_and_no_side_effect(
    driver_harness: DriverHarness,
    browser_key: Callable[[str | None], None],
    browser: str | None,
) -> None:
    """Pin the absent ``default:`` label - ``Driver.java:29-42``.

    ``"Chrome"``, ``"CHROME"`` and ``" chrome"`` are the case sensitivity and
    the absent trimming of a Java ``switch`` over a ``String``, which compares
    with ``equals``; ``"safari"`` and ``"edge"`` are values the source never
    handled; ``""`` is an installed empty value, which userdata membership
    resolves rather than treating as unset; ``None`` is the key being unset,
    which ``app/config.py`` returns unchanged.

    Each yields ``None`` with nothing provisioned, nothing constructed, nothing
    raised and an empty slot - AAP 0.4.1: *"Any other value fails at first
    driver use, as today."*  No assertion is made about any log record: review
    finding F01 orders the ``else:`` branch and its DEBUG record deleted, and
    the observable contract asserted here is invariant under that deletion.
    """
    browser_key(browser)

    session = driver_module.get_driver()

    assert session is None
    assert driver_module._session() is None
    assert driver_harness.recorder.calls == []
    assert driver_harness.sessions == []


def test_unrecognised_browser_leaves_a_later_call_free_to_try_again(
    driver_harness: DriverHarness,
    browser_key: Callable[[str | None], None],
) -> None:
    """Pin the absence of an "already failed" state - ``Driver.java:22``.

    The create-on-demand guard is only "the slot is empty", and the Java method
    keeps no record of a previous miss, so a call after an unrecognised browser
    name tries again in full.  This is what makes a mid-run correction of the
    ``browser`` value take effect rather than being permanently poisoned by the
    first attempt.
    """
    browser_key("safari")

    assert driver_module.get_driver() is None
    assert driver_harness.recorder.calls == []

    browser_key("chrome")

    session = driver_module.get_driver()

    assert session is not None
    assert session is driver_harness.sessions[0]
    assert driver_harness.recorder.targets() == CHROME_BRANCH_SEQUENCE


@pytest.mark.parametrize("browser", UNRECOGNISED_BROWSERS)
def test_unrecognised_browser_emits_nothing_at_warning_or_above(
    driver_harness: DriverHarness,
    browser_key: Callable[[str | None], None],
    automation_log: AutomationLogCapture,
    browser: str | None,
) -> None:
    """Pin what the unmatched path may *say* - ``Driver.java:29-42``.

    An unrecognised browser is not a warning: nothing was substituted, nothing
    was retried and nothing is being deprecated, so a record at WARNING or
    above on the ``app.automation`` tree would tell an operator the exact
    opposite of what happened - AAP 0.4.1's ruling is that the value simply
    "fails at first driver use, as today".  This is also what keeps the port
    honest about the branch's purpose: a fallback that announced itself as a
    warning would read as sanctioned behaviour rather than as the defect it is.

    The **total** absence of records is deliberately not asserted.  The current
    source emits one at DEBUG from the ``else:`` branch and review finding F01
    deletes that branch outright, so "no records at all" would be red against
    the source as it stands today, and neither form is observable to any
    caller: a DEBUG diagnostic is not part of this function's contract.  What
    is invariant under both forms - and the whole of what a caller could ever
    be misled by - is that nothing reaches WARNING.

    The liveness probe at the end is what makes the negative assertion worth
    something: it proves the capture would have seen a WARNING had one been
    emitted, so a broken capture fails this test instead of passing it.
    """
    browser_key(browser)

    assert driver_module.get_driver() is None

    emitted = automation_log.at_or_above(logging.WARNING)

    logging.getLogger(PROBE_LOGGER_NAME).warning("capture liveness probe")

    probed = automation_log.at_or_above(logging.WARNING)

    assert emitted == ()
    assert len(probed) == 1
    assert driver_harness.recorder.calls == []


# --------------------------------------------------------------------------
# No fallback, asserted over the source
#
# The four tests below are the substance of "no default branch": not the
# absence of a log line, but the absence of any fallback.  Each holds against
# the source as it stands and against the source with review finding F01
# applied, and together they forbid every third browser name, every third
# constructor, every session written from outside the two branches and every
# substitution or retry hidden on the unmatched path.
# --------------------------------------------------------------------------


def test_get_driver_compares_the_browser_value_against_only_two_names(
    driver_source: ast.Module,
) -> None:
    """Pin the two case labels of ``Driver.java:29-42`` - and only those two.

    Every string literal ``get_driver`` decides on is collected from the tree,
    and the set has to be exactly ``"chrome"`` and ``"firefox"``.  A third
    browser name, an alias, or the target of a normalisation - a lowercased
    ``"CHROME"``, say - each adds a member here, and each is a fallback: AAP
    0.4.1 fixes the behaviour of every other value as failing at first driver
    use, and ``driver.py`` states that normalizing "would make this port accept
    input the source rejects".

    Literals are counted only where they are compared, so the module's prose
    and its log message - which quotes both names - are correctly ignored.
    """
    get_driver = function_named(driver_source, "get_driver")

    assert decision_strings(get_driver) == BROWSER_NAMES_IN_SOURCE


def test_only_chrome_and_firefox_are_ever_constructed(driver_source: ast.Module) -> None:
    """Pin the two browsers of ``Driver.java:29-41`` at module scope.

    Exactly two constructors are reachable in the whole file, and they are
    ``webdriver.Chrome`` and ``webdriver.Firefox``.  ``driver.py``'s "No third
    browser" note is the rule being enforced: the specification mentions
    Internet Explorer, AAP 0.2.2 puts it out of scope, and "Add no branch for
    it" is only meaningful if no other constructor exists anywhere - including
    in a helper that the dispatch could later call.

    The one legitimate mention of the namespace outside a call is the
    ``webdriver.Remote | None`` annotation, and it is exempted **by position**:
    every non-call reference must sit inside an annotation, where no browser can
    be constructed.  Exempting the name ``Remote`` instead would leave a hole
    the moment the annotations changed.
    """
    call_func_ids = frozenset(
        id(node.func) for node in ast.walk(driver_source) if isinstance(node, ast.Call)
    )
    annotated = annotation_ids(driver_source)
    constructed: set[str] = set()
    outside_annotations: list[tuple[str, int]] = []

    for node in ast.walk(driver_source):
        if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
            continue
        if node.value.id != WEBDRIVER_NAMESPACE:
            continue

        if id(node) in call_func_ids:
            constructed.add(node.attr)
        elif id(node) not in annotated:
            outside_annotations.append((node.attr, node.lineno))

    assert constructed == BROWSER_CONSTRUCTORS_IN_SOURCE
    assert outside_annotations == []


def test_a_session_is_adopted_only_inside_the_two_branches(driver_source: ast.Module) -> None:
    """Pin where the slot may be written from - ``Driver.java:32`` and ``:38``.

    The Java method stores a session with ``driverPool.set(...)`` from inside
    each of its two cases and nowhere else.  Here, adoption is
    ``_adopt_session`` - the one function that writes the holder - so "the slot
    is written only from the two branches" is exactly: one adoption in the
    ``"chrome"`` branch, one in the ``"firefox"`` branch, and none anywhere
    else in ``get_driver``.

    The second half closes the way around that helper: no ``setattr`` call and
    no assignment to an attribute of the holder may appear in ``get_driver``
    outside those two branches either, so a fallback cannot install a session
    by reaching past the helper that the first half constrains.
    """
    get_driver = function_named(driver_source, "get_driver")
    dispatch = browser_dispatch(get_driver)
    matched = subtree_ids(*dispatch.chrome_body) | subtree_ids(*dispatch.firefox_body)

    adoptions = calls_named(ADOPT_SESSION, get_driver)

    assert len(calls_named(ADOPT_SESSION, *dispatch.chrome_body)) == 1
    assert len(calls_named(ADOPT_SESSION, *dispatch.firefox_body)) == 1
    assert len(adoptions) == 2
    assert [call.lineno for call in adoptions if id(call) not in matched] == []

    stray_setattr = [
        call.lineno for call in calls_named("setattr", get_driver) if id(call) not in matched
    ]
    holder_writes = [
        node.lineno
        for node in ast.walk(get_driver)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == HOLDER_NAME
        and not isinstance(node.ctx, ast.Load)
        and id(node) not in matched
    ]

    assert stray_setattr == []
    assert holder_writes == []


def test_the_unmatched_browser_path_is_not_a_fallback(driver_source: ast.Module) -> None:
    """Pin what the unmatched path may not do - ``Driver.java:29-42``.

    Whatever else that path may contain, it constructs no browser, provisions
    no driver binary, adopts no session, raises nothing and substitutes no
    browser name.  ``driver.py`` states the same thing in words - "There is no
    fallback browser, no substitution and nothing raised from here" - and AAP
    0.4.1 requires the value to fail at first *use* rather than here, which a
    ``raise`` from this path would change.

    "Substitutes no browser name" is asserted structurally, twice over: the
    path binds no name at all, so the browser value cannot be replaced and no
    computed name can reach a constructor, and no literal on it *equals* a
    browser name.  Equality and never containment, because the current DEBUG
    message quotes both names inside a longer sentence and that is prose rather
    than substitution.

    With F01 applied the path is empty and every assertion here is vacuously
    true, which is correct: a path that does not exist cannot fall back.
    """
    get_driver = function_named(driver_source, "get_driver")
    dispatch = browser_dispatch(get_driver)

    fallback_calls: list[tuple[str | None, int]] = []
    raises: list[int] = []
    bindings: list[int] = []
    literals: set[str] = set()

    for statement in dispatch.unmatched:
        literals |= constant_strings(statement)

        for node in ast.walk(statement):
            if isinstance(node, ast.Call) and is_fallback_call(node):
                fallback_calls.append((callee_name(node), node.lineno))
            elif isinstance(node, ast.Raise):
                raises.append(node.lineno)
            elif isinstance(node, ast.Assign | ast.AnnAssign | ast.AugAssign | ast.NamedExpr):
                bindings.append(node.lineno)

    assert fallback_calls == []
    assert raises == []
    assert bindings == []
    assert literals & BROWSER_NAMES_IN_SOURCE == set()


# --------------------------------------------------------------------------
# Ownership before settings, and teardown
# --------------------------------------------------------------------------


def test_session_is_owned_before_its_settings_are_applied(
    driver_harness: DriverHarness,
    browser_key: Callable[[str | None], None],
    stub_driver: StubDriver,
) -> None:
    """Pin slot-before-settings - ``Driver.java:32`` before ``:33``.

    Java stores the session with ``driverPool.set(...)`` and only then
    dereferences ``driverPool.get().manage()``, and ``driver.py`` states the
    consequence as a guarantee: if ``maximize_window()`` raises, the session is
    already this worker's, so :func:`quit_driver` can still close the browser
    process instead of leaking it.  The failure propagates - nothing here
    swallows it - and the implicit wait is never reached.
    """
    browser_key("chrome")
    stub_driver.maximize_error = RuntimeError("maximize refused by the window manager")
    driver_harness.queue_session(stub_driver)

    with pytest.raises(RuntimeError, match="maximize refused"):
        driver_module.get_driver()

    # Ownership, despite the failure: the slot holds the session, so the
    # browser process is reachable rather than orphaned.
    assert driver_module._session() is stub_driver
    assert stub_driver.operations() == ("maximize_window",)

    driver_module.quit_driver()

    assert stub_driver.quit_count == 1
    assert driver_module._session() is None


def test_quit_driver_on_an_empty_slot_does_nothing(driver_harness: DriverHarness) -> None:
    """Pin the teardown guard - ``Driver.java:51``.

    With an empty slot this is a no-op: nothing is called and nothing is
    raised, so a scenario that never built a session - one whose configured
    browser matched neither branch - tears down as quietly as one that did.
    Called twice, because ``after_scenario`` runs for every scenario and a
    second guarded call must be as quiet as the first.
    """
    assert driver_module._session() is None

    driver_module.quit_driver()
    driver_module.quit_driver()

    assert driver_module._session() is None
    assert driver_harness.recorder.calls == []
    assert driver_harness.sessions == []


def test_quit_driver_quits_once_and_clears_the_slot(
    driver_harness: DriverHarness,
    browser_key: Callable[[str | None], None],
) -> None:
    """Pin quit-then-clear - ``Driver.java:52-53``.

    ``quit()`` is called exactly once and the slot is emptied, so the next
    ``get_driver()`` builds a genuinely new session rather than handing back
    the quit one.  That is the half of AAP 0.3.3's contract which guarantees
    "every scenario gets a fresh session, and no code ever touches a driver
    after ``quit()``", and identity is what proves it: the second session is a
    different object, freshly provisioned and freshly configured.
    """
    browser_key("chrome")

    first = driver_module.get_driver()

    driver_module.quit_driver()

    assert first.quit_count == 1
    assert driver_module._session() is None

    second = driver_module.get_driver()

    assert second is not first
    assert second is driver_harness.sessions[1]
    assert first.quit_count == 1
    assert second.quit_count == 0
    assert second.operations() == SETUP_OPERATIONS
    assert second.calls_of("implicitly_wait") == ((IMPLICIT_WAIT_SECONDS,),)
    assert driver_harness.recorder.count_of(CHROME_MANAGER_INSTALL) == 2
    assert driver_harness.recorder.count_of(WEBDRIVER_CHROME) == 2


def test_a_failing_quit_is_not_converted_into_success(
    driver_harness: DriverHarness,
    browser_key: Callable[[str | None], None],
    automation_log: AutomationLogCapture,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Pin what a failing ``quit()`` must do, under both forms of the contract.

    The disjunction below is here because of review finding **F02**: the
    current implementation logs the failure at WARNING and suppresses it, and
    F02 requires that the failure stop being converted into success - it will
    either propagate or be reported structurally.  This test therefore asserts
    only what is true either way, and deliberately asserts neither suppression
    nor propagation:

    * ``quit()`` was attempted exactly once - the failure is not retried and
      not skipped;
    * the slot is empty afterwards, because the clear happens in a ``finally``
      - a session that could not be closed must never reach a later scenario;
    * the failure was not silently discarded - it either propagated to the
      caller or was logged at WARNING or above by the automation package, and
      where it was logged rather than propagated the record carries the
      exception itself (``record.exc_info is not None``).  A WARNING that names
      a problem without carrying it is not a report a reader can act on, and it
      is indistinguishable in a log from an unrelated complaint.

    What this test deliberately does **not** forbid, and why: the log-and-return
    form.  That is what the current source does, so pinning propagation here
    would make this module red against the very tree it ships in.  F02's own
    resolution sanctions two shapes - "propagate or return a structured cleanup
    failure to the worker outcome" - so propagation is not the single correct
    answer and is deliberately not pinned; what F02 forbids absolutely is
    converting the failure into success, which the disjunction above is what
    catches.  Once F02 lands, whoever reconciles it must tighten this test to
    the shape that landed: assert propagation, or assert the structured failure
    value the caller now receives.  Until then the disjunction, the exception
    requirement, the single attempt and the empty slot are the whole of what is
    true under both forms.
    """
    browser_key("chrome")
    session = driver_harness.queue_session(FailingQuitSession(RuntimeError("session is gone")))

    assert driver_module.get_driver() is session

    # The double is a session like any other: it received the same set-up, so
    # what follows is about the teardown alone.
    assert session.maximize_count == 1
    assert session.implicit_waits == [IMPLICIT_WAIT_SECONDS]

    failure: BaseException | None
    with caplog.at_level(logging.WARNING, logger=APP_LOGGER_NAME):
        try:
            driver_module.quit_driver()
        except Exception as exc:
            # Permitted by F02, so captured rather than asserted against.
            failure = exc
        else:
            failure = None

    # Both capture sources, so that a record emitted below a logger whose
    # propagation another module switched off still counts as reported -
    # see :class:`AutomationLogCapture`.  A record seen by both appears twice,
    # which none of the assertions below is sensitive to.
    reported = tuple(
        record
        for record in (*caplog.records, *automation_log.records)
        if record.levelno >= logging.WARNING and record.name.startswith(DRIVER_LOGGER_PREFIX)
    )
    reported_with_exception = tuple(record for record in reported if record.exc_info is not None)

    assert session.quit_count == 1
    assert driver_module._session() is None
    assert failure is not None or reported, (
        "a quit() failure must not be silently discarded: it has to propagate "
        "or be logged at WARNING or above"
    )
    assert failure is not None or reported_with_exception, (
        "a quit() failure reported by logging rather than by propagating must "
        "carry the exception: log with exc_info, or use logger.exception()"
    )


# --------------------------------------------------------------------------
# No silent swallow, asserted over the source
#
# The three tests below forbid a failing ``quit()`` being discarded, while
# accepting both shapes review finding F02 sanctions - propagating, or
# returning a structured cleanup failure.  Propagation is therefore never
# pinned: what is pinned is that the slot is forgotten on every path, that
# teardown cannot swallow a shutdown, and that a handler which does catch the
# failure reports it.  A function with no handler at all satisfies the last two
# vacuously, and correctly so: with nothing catching it, the failure propagates.
# --------------------------------------------------------------------------


def test_quit_driver_clears_the_slot_from_inside_a_finally(driver_source: ast.Module) -> None:
    """Pin the clear as unconditional - ``Driver.java:53`` and AAP 0.3.3.

    ``driver.py`` states the requirement as a consequence of the lifecycle
    contract: "A session that could not be closed must never be handed to a
    later scenario, which is what the contract's 'no code ever touches a driver
    after ``quit()``' forbids, so the clear happens in a ``finally``."  A clear
    placed after the ``try`` instead is skipped on exactly the path that needs
    it most - a ``quit()`` that failed - leaving a dead session in the slot for
    the next scenario to be handed.

    Every call that clears the slot is required to be lexically inside a
    ``finally``, not merely one of them, so a second clear added on the happy
    path cannot satisfy this while the failing path stays uncovered.
    """
    quit_driver = function_named(driver_source, "quit_driver")
    final_statements = [
        statement
        for node in ast.walk(quit_driver)
        if isinstance(node, ast.Try | ast.TryStar)
        for statement in node.finalbody
    ]
    inside_finally = subtree_ids(*final_statements)

    clears = calls_named(CLEAR_SESSION, quit_driver)

    assert len(clears) >= 1
    assert [call.lineno for call in clears if id(call) not in inside_finally] == []


def test_quit_driver_catches_neither_everything_nor_base_exception(
    driver_source: ast.Module,
) -> None:
    """Pin what teardown may not catch - the run has to stay interruptible.

    ``driver.py`` says it in as many words: "Never a bare ``except``: this
    catches failures of the browser session, and deliberately not
    ``BaseException``, so a ``KeyboardInterrupt`` or a worker shutdown still
    propagates."  Either of those would make teardown able to absorb the signal
    that is trying to stop the run - a worker that refuses to die, which is a
    far worse failure than the browser-cleanup nuisance the handler exists for.

    Asserted over every handler in the function, and vacuously true when there
    is none: with nothing catching the failure it propagates, which is the
    stronger of the two shapes F02 permits.
    """
    quit_driver = function_named(driver_source, "quit_driver")
    handlers = except_handlers(quit_driver)

    bare = [handler.lineno for handler in handlers if handler.type is None]
    too_broad = [
        handler.lineno
        for handler in handlers
        if BASE_EXCEPTION_NAME in caught_exception_names(handler)
    ]

    assert bare == []
    assert too_broad == []


def test_quit_driver_reports_every_failure_it_catches(driver_source: ast.Module) -> None:
    """Pin the absence of a silent swallow - review finding F02.

    F02's objection is that "the browser process can remain alive while
    teardown reports success".  A handler that swallows the failure - one whose
    body only ``pass``es, or that logs a message without the exception - is
    exactly that, and it is what this forbids.  What it accepts is the full set
    of shapes F02's resolution leaves open: re-raising, logging with the
    exception attached (``exc_info=...`` or ``logger.exception``), or returning
    a value that describes the failure to the caller.

    Judged from each handler's own body, never from a text search over the
    file: a ``raise`` in a neighbouring function, and the prose about
    suppression this module already carries, would both answer such a search
    while saying nothing about what a handler does with what it caught.
    Vacuously true when the function catches nothing, which is propagation.
    """
    quit_driver = function_named(driver_source, "quit_driver")
    handlers = except_handlers(quit_driver)

    silent = [handler.lineno for handler in handlers if not handler_reports_failure(handler)]

    assert silent == []


# --------------------------------------------------------------------------
# The package barrel
# --------------------------------------------------------------------------


def test_barrel_reexports_the_two_lifecycle_functions() -> None:
    """Pin the re-export ``app/automation/__init__.py`` promises.

    ``driver.py``'s docstring states that the package re-exports both functions
    "under exactly these names", and every step and page reaches the lifecycle
    through the barrel.  Identity rather than mere presence is what matters: a
    wrapper re-exported under the same name would defeat both the single-owner
    rule of AAP 0.3.3 and every patch point this suite relies on.
    """
    assert automation.get_driver is driver_module.get_driver
    assert automation.quit_driver is driver_module.quit_driver
    assert "get_driver" in automation.__all__
    assert "quit_driver" in automation.__all__
