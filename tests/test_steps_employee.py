"""Per-method behaviour-parity tests for ``features/steps/employee_steps.py``.

This module discharges AAP 0.4.1's per-module parity obligation for one step
class: *"for each of the ten step modules, ``tests/test_steps_<area>.py`` drives
the module against a stubbed driver and asserts, for every step method in the
corresponding Java class, that the port performs the same observable operations
in the same order ... and the module test enumerates the Java class's methods so
an omission fails rather than passes silently."*

The specification is the pinned Java source, not this port's own docstrings:
``src/main/java/com/testinium/step_definitions/EmployeeStage.java`` (112 lines,
12 methods) and its page object ``.../pages/EmployeeP.java`` (15 ``@FindBy``
fields and two ``login`` overloads).  Every test below names the Java line or
lines it pins, and the census the whole module is built around is::

    12 step methods            EmployeeStage.java:16, 22, 28, 35, 45, 50,
                               58, 66, 76, 84, 91, 107
    5 configuration reads      web.table.url :18; url :24, :60, :93;
       over 3 keys             mixed-case EmplTitle :31
    3 login() invocations      :25, :61, :94 - each three DOM operations
                               through three separate lookups
    6 explicit waits, all 3s   :31 (configured EmplTitle), :38, :40, :42
                               (visibility of the element just clicked),
                               :71 ("New - Odoo"), :87 ("Employees - Odoo")
    8 fixed delays             one of 7s at :48 - the suite's only one - and
                               seven of 3s at :62, :68, :70, :95, :97, :100,
                               :103
    4 live assertions          :32, :52, :78, :88, every one message-less
    2 commented-out asserts    :53-55 and :79-81, which must stay ABSENT
    1 assertion-free body      :107-110, a single click and no check

How a step body is reached
--------------------------
behave's ``load_step_modules`` **execs** the step modules, so they never enter
``sys.modules`` and cannot be imported by name.  ``tests/conftest.py``'s
``resolve_step`` fixture is the supported handle: it returns a ``StepMatch``
carrying the step function, the arguments parsed out of the phrase, the pattern
and the registry bucket, and ``StepMatch.run(context)`` invokes the body.  The
module namespace is reachable only through ``match.func.__globals__``, which is
where :class:`StepProbe` installs its recorders - always through
``monkeypatch.setitem``, never by direct mutation, because the step registry is
session-scoped and shared with every other step-related test module.

Why the assertions are shaped the way they are
----------------------------------------------
*Nothing sleeps and nothing waits.*  :class:`StepProbe` replaces the module's
``sleep`` and **every** wait helper bound in its namespace, so the whole module
runs in milliseconds with no browser, no network and no real clock.

*The wait assertions survive a change of call-site shape.*  The port currently
calls ``wait_visible_element(element, 3)``, and ``app/automation`` is free to be
refactored so that the same parity fact is expressed as ``wait_visible(locator,
3)``.  The recorder therefore installs over every ``wait_*`` name in the
namespace and normalises each call to ``(kind, target locator, timeout)`` -
reading ``element.locator`` for an element target, the pair itself for a locator
target and the string for a title wait - so what is asserted is the parity fact
(same target, same timeout, same order) rather than one particular signature.

*Lookups and effects are asserted separately.*  Evaluating a page accessor to
pass it to a wait performs its own ``find_element``, which a call-site change
would remove.  So the ordered comparisons run over the log with lookups
filtered out, and the no-caching guarantee - one lookup per accessor access, the
un-cached ``PageFactory`` proxy of ``LoginP.java:9-11`` - is asserted as a
separate count derived from what was recorded.

*Locators are compared by page-class constant.*  ``EmployeePage.EMPL_STAGE``
rather than ``("partial link text", "Employees")``, so a same-valued locator
belonging to another page object cannot satisfy a test here.

*The credentials are asserted verbatim.*  ``posmanager50@info.com`` and
``posmanager`` are carried over from ``EmployeeP.java:60-61`` as AAP 0.8
requires ("no agent should redact, parameterize or rotate them, or treat their
presence as a finding"), so they are pinned exactly as the reference has them.

Coverage is enumerated, not hoped for
-------------------------------------
:data:`PARITY_TESTS` maps each of the twelve registered patterns to the tests
that pin it, and the final test in the file checks both halves of the
obligation: every pattern the module registers has a declared test, and every
pattern whose tests actually ran was recorded as covered at run time.  Adding a
thirteenth definition, deleting one, or leaving one untested fails there.
"""

from __future__ import annotations

import ast
import functools
from pathlib import Path
from typing import Any, Final, NamedTuple

import pytest

from app import config
from app.pages import EmployeePage

# =========================================================================== #
# Locations, names and the literals the Java source fixes
# =========================================================================== #

#: Repository root, from this file's own position: ``tests/`` -> root.  Resolved
#: from ``__file__`` rather than from the working directory, so the AST and
#: feature-file assertions below do not depend on where pytest was started.
REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]

#: The step module under test - the port of ``EmployeeStage.java``.
STEP_MODULE_PATH: Final[Path] = REPO_ROOT / "features" / "steps" / "employee_steps.py"

#: The module name behave's registry reports for every definition it owns,
#: which is the step file's stem because ``load_step_modules`` execs the file.
STEP_MODULE_NAME: Final[str] = "employee_steps"

#: The one registry bucket every definition in this port must land in - AAP
#: deviation 7, ``@step`` for text-only matching.  ``given``, ``when`` and
#: ``then`` must stay empty of this module's definitions.
STEP_BUCKET: Final[str] = "step"

#: The Gherkin keyword buckets that must hold nothing from this module.
KEYWORD_BUCKETS: Final[tuple[str, ...]] = ("given", "when", "then")

#: The feature file this module serves.  Its name and its feature-level tag are
#: both asserted: ``EmployeeFc.feature`` is the reference's filename and
#: ``@UPGN-344`` its only feature-level tag.
FEATURE_PATH: Final[Path] = REPO_ROOT / "features" / "EmployeeFc.feature"

#: ``EmployeeFc.feature:1``.
FEATURE_TAG: Final[str] = "@UPGN-344"

#: ``EmployeeFc.feature:2``.
FEATURE_HEADER: Final[str] = "Feature: Testinium app Employees module"

#: Where every feature file lives, for the orphan-step census.
FEATURES_DIR: Final[Path] = REPO_ROOT / "features"

# --------------------------------------------------------------------------- #
# The twelve phrases, in EmployeeStage.java declaration order
# --------------------------------------------------------------------------- #

#: ``EmployeeStage.java:16`` - the orphan.  Registered by the port and invoked
#: by no feature file in the suite; near-duplicate of ``LoginSD.java:19``'s
#: ``User is on the upgenix login page``, which carries a "the" this one does
#: not.  Tested directly, since no scenario reaches it.
PHRASE_ORPHAN_LOGIN_PAGE: Final[str] = "User is on upgenix login page"

#: ``EmployeeStage.java:22``.
PHRASE_DASHBOARD: Final[str] = "User is on the dashboard"

#: ``EmployeeStage.java:28``.
PHRASE_EMPLOYEES_STAGE: Final[str] = "User clicks Employees stage"

#: ``EmployeeStage.java:35``.
PHRASE_CHALLENGES_STAGE: Final[str] = "User clicks Challenges stage"

#: ``EmployeeStage.java:45``.
PHRASE_DEPARTMENTS_STAGE: Final[str] = "User clicks Departments stage"

#: ``EmployeeStage.java:50``.
PHRASE_LAST_STAGE_TITLE: Final[str] = "User should see the last stage title"

#: ``EmployeeStage.java:58``.
PHRASE_EMPLOYEES_DASHBOARD: Final[str] = "User is on the employees dashboard"

#: ``EmployeeStage.java:66`` - the class's only parameterized step.  The
#: registered *pattern*; the concrete phrases the feature invokes are
#: :data:`FEATURE_EMPLOYEE_NAMES` substituted into it.
PATTERN_CREATE_EMPLOYEE: Final[str] = (
    'User creates new employees "{name}" in the Employees stage'
)

#: ``EmployeeStage.java:76``.
PHRASE_CREATED_MESSAGE: Final[str] = (
    "User should see the Employee created message under full profile"
)

#: ``EmployeeStage.java:84``.
PHRASE_LISTED_EMPLOYEES: Final[str] = "User should see listed employees in the Employees stage"

#: ``EmployeeStage.java:91``.
PHRASE_EDIT_EMPLOYEE: Final[str] = "User edits created employees in the Employees module"

#: ``EmployeeStage.java:107``.
PHRASE_EDITED_NAME: Final[str] = "User should see the edited name in the Employees module"

#: All twelve registered patterns, in ``EmployeeStage.java`` declaration order.
#: The enumeration the parity obligation requires: this tuple is compared
#: against what the registry actually holds, so a thirteenth definition or a
#: deleted one fails rather than passes silently.
ALL_PATTERNS: Final[tuple[str, ...]] = (
    PHRASE_ORPHAN_LOGIN_PAGE,
    PHRASE_DASHBOARD,
    PHRASE_EMPLOYEES_STAGE,
    PHRASE_CHALLENGES_STAGE,
    PHRASE_DEPARTMENTS_STAGE,
    PHRASE_LAST_STAGE_TITLE,
    PHRASE_EMPLOYEES_DASHBOARD,
    PATTERN_CREATE_EMPLOYEE,
    PHRASE_CREATED_MESSAGE,
    PHRASE_LISTED_EMPLOYEES,
    PHRASE_EDIT_EMPLOYEE,
    PHRASE_EDITED_NAME,
)

#: The ported function name per pattern, in the same order.  ``#9`` keeps the
#: Java method's own shorter name - ``user_should_see_the_message_under_full_
#: profile`` for the phrase that says "Employee created message" - and that
#: mapping is part of the parity, so it is pinned rather than normalised.
STEP_FUNCTION_NAMES: Final[tuple[str, ...]] = (
    "user_is_on_upgenix_login_page",
    "user_is_on_the_dashboard",
    "user_clicks_employees_stage",
    "user_clicks_challenges_stage",
    "user_clicks_departments_stage",
    "user_should_see_the_last_stage_title",
    "user_is_on_the_employees_dashboard",
    "user_creates_new_employees_in_the_employees_stage",
    "user_should_see_the_message_under_full_profile",
    "user_should_see_listed_employees_in_the_employees_stage",
    "user_edits_created_employees_in_the_employees_module",
    "user_should_see_the_edited_name_in_the_employees_module",
)

# --------------------------------------------------------------------------- #
# Literals, timings and expected values
# --------------------------------------------------------------------------- #

#: ``EmployeeP.java:60`` - carried over verbatim per AAP 0.8.
LOGIN_EMAIL: Final[str] = "posmanager50@info.com"

#: ``EmployeeP.java:61`` - carried over verbatim per AAP 0.8.
LOGIN_PASSWORD: Final[str] = "posmanager"

#: ``EmployeeStage.java:32``, ``:87`` and ``:88``.
TITLE_EMPLOYEES: Final[str] = "Employees - Odoo"

#: ``EmployeeStage.java:52``.
TITLE_DEPARTMENTS: Final[str] = "Departments - Odoo"

#: ``EmployeeStage.java:71``.
TITLE_NEW: Final[str] = "New - Odoo"

#: ``EmployeeStage.java:102``.
EDITED_NAME: Final[str] = "Sterling"

#: The two ``Examples: Employee's name`` values, ``EmployeeFc.feature:23``
#: and ``:33``, which the one parameterized step receives.
FEATURE_EMPLOYEE_NAMES: Final[tuple[str, ...]] = ("Cristiano Ronaldo", "Lionel Messi")

#: Every wait in the class is built from ``EmployeeStage.java:14``'s
#: ``new WebDriverWait(Driver.getDriver(), 3)``, so every timeout is 3.
WAIT_TIMEOUT: Final[int] = 3

#: ``Thread.sleep(3000)`` - seven of the eight fixed delays.
SLEEP_SHORT: Final[int] = 3

#: ``Thread.sleep(7000)`` at ``EmployeeStage.java:48`` - the suite's only one.
SLEEP_LONG: Final[int] = 7

#: The complete fixed-delay census of the class: one 7-second delay and seven
#: 3-second ones, asserted as a multiset here and positionally per step.
ALL_SLEEP_SECONDS: Final[tuple[int, ...]] = (SLEEP_LONG,) + (SLEEP_SHORT,) * 7

#: A title no step expects, for driving an assertion's failing arrangement.
#: Deliberately close to the real ones: a comparison weakened to a substring or
#: a case-insensitive match would accept it.
UNEXPECTED_TITLE: Final[str] = "employees - odoo (Employees - Odoo mirror)"

# --------------------------------------------------------------------------- #
# The configuration seam
# --------------------------------------------------------------------------- #

#: The three ``app.config`` accessors this module is allowed to read, mapped to
#: the ``configuration.properties`` key each one resolves.  ``EmplTitle`` is
#: mixed case in the reference and stays mixed case here.
CONFIG_ACCESSOR_KEYS: Final[dict[str, str]] = {
    "get_web_table_url": "web.table.url",
    "get_url": "url",
    "get_empl_title": "EmplTitle",
}

#: Distinguishable values the recorder returns for each accessor.  Distinct
#: strings are the whole point: a step that read ``web.table.url`` where the
#: Java reads ``url`` would navigate to the wrong sentinel and fail here.
CONFIG_SENTINELS: Final[dict[str, str]] = {
    "get_web_table_url": "https://sentinel.invalid/web-table-url",
    "get_url": "https://sentinel.invalid/module-url",
    "get_empl_title": "Sentinel EmplTitle - not the hard-coded one",
}

#: Values installed through ``app.config.set_userdata`` for the end-to-end
#: configuration test, which drives the real accessors rather than recorders.
USERDATA_VALUES: Final[dict[str, str]] = {
    "web.table.url": "https://userdata.invalid/sign-in",
    "url": "https://userdata.invalid/employees",
    "EmplTitle": "Userdata EmplTitle",
}

# --------------------------------------------------------------------------- #
# Log vocabulary
# --------------------------------------------------------------------------- #

#: Prefix ``tests/conftest.py``'s recorder gives element operations.
ELEMENT_PREFIX: Final[str] = "element."

#: Operations dropped from every ordered comparison.  A lookup is not an effect
#: on the page, and whether one happens depends on the shape of a wait call
#: site, which is the one thing these tests deliberately do not fix.
LOOKUP_OPERATIONS: Final[frozenset[str]] = frozenset({"find_element", "find_elements"})

#: The two entries :class:`StepProbe` adds to the driver's log.  They are
#: excluded from the "effectful" view and included in the full timeline, which
#: is how a delay's *position* inside a step is asserted.
PROBE_OPERATIONS: Final[frozenset[str]] = frozenset({"sleep", "wait"})

#: Canonical wait kinds, keyed by helper name.  ``visibility_of(element)`` and
#: ``visibility_of_element_located(locator)`` are the same parity fact reached
#: two ways, so both normalise to ``"visible"``; anything else keeps its own
#: name, because a different predicate is a behaviour change and must fail.
WAIT_KIND_ALIASES: Final[dict[str, str]] = {
    "wait_visible": "visible",
    "wait_visible_element": "visible",
}

#: Keyword names a wait helper may carry its target under, consulted only when
#: the target was not passed positionally.
WAIT_TARGET_KEYWORDS: Final[tuple[str, ...]] = ("element", "locator", "title")

#: Sentinel for "no value found", distinguishable from a legitimate ``None``.
_UNSET: Final[Any] = object()

#: Snapshots of each step module namespace as it was *before* any recorder was
#: installed, keyed by the namespace's identity and holding a strong reference
#: to it so the key cannot be reused.  Taken once and consulted by every
#: install: a test that drives several steps installs several times, and what
#: names the module genuinely binds - which wait helpers, which configuration
#: accessors - has to be read from the original bindings rather than from a
#: namespace another probe has already patched.  ``monkeypatch`` restores those
#: originals after every test, so one snapshot stays correct for the session.
_PRISTINE_NAMESPACES: dict[int, tuple[dict[str, Any], dict[str, Any]]] = {}


def pristine_namespace(namespace: dict[str, Any]) -> dict[str, Any]:
    """The step module's namespace as it was before instrumentation.

    :param namespace: The live ``func.__globals__`` mapping.
    :returns: A snapshot of its bindings, taken the first time this namespace
        was seen and returned unchanged afterwards.
    """
    stored = _PRISTINE_NAMESPACES.get(id(namespace))

    if stored is not None and stored[0] is namespace:
        return stored[1]

    snapshot = dict(namespace)
    _PRISTINE_NAMESPACES[id(namespace)] = (namespace, snapshot)
    return snapshot


class WaitCall(NamedTuple):
    """One explicit wait, normalised so the assertion outlives the call shape.

    :param kind: Canonical predicate name - ``"visible"`` or ``"title_is"``
        for this module - resolved through :data:`WAIT_KIND_ALIASES`.
    :param target: What was waited on: the locator pair for an element or
        locator target, the expected title string for a title wait.
    :param timeout: The timeout exactly as the call site passed it, with no
        normalisation whatever - the parity fact is the number 3.
    :param from_element: Whether the target arrived as a located element,
        which is what makes the call site perform its own extra lookup.
    """

    kind: str
    target: Any
    timeout: Any
    from_element: bool


class StepProbe:
    """Recorder for a step module's fixed delays, waits and configuration reads.

    Installed over ``match.func.__globals__`` - the only handle on an exec'd
    step module's namespace - through ``monkeypatch.setitem``, so pytest
    restores every name at teardown and the session-scoped registry is left
    exactly as it was found.

    Three things are intercepted:

    ``sleep``
        Recorded and **not** performed.  The module's eight fixed delays are
        parity facts about position and duration, not about elapsed time, so
        the suite runs in milliseconds.

    every ``wait_*`` helper bound in the namespace
        Recorded as a :class:`WaitCall`.  Installing over all of them, rather
        than over the one name the port happens to use today, is what lets
        ``app/automation`` change the call-site shape without silently
        disabling these assertions: an un-intercepted helper would run a real
        ``WebDriverWait`` against the stub, and the import-boundary test
        guarantees the helpers are bound as module globals so there is nothing
        to miss.

    the ``app.config`` accessors bound in the namespace
        Recorded in call order and answered from :data:`CONFIG_SENTINELS`, so
        that which key a step reads is directly observable.

    Entries land in the *driver's own* ordered log as ``("sleep", (seconds,))``
    and ``("wait", (kind, target, timeout))``, which is what makes a delay's
    position among the DOM operations assertable as one sequence.
    """

    def __init__(self, driver: Any) -> None:
        """Bind the probe to the recorder whose log it appends to.

        :param driver: The ``StubDriver`` published as ``context.driver``.
        """
        self.driver = driver

        #: Every recorded delay, in call order, in seconds as passed.
        self.sleeps: list[Any] = []

        #: Every recorded wait, in call order.
        self.waits: list[WaitCall] = []

        #: The ``app.config`` accessor names the step called, in call order.
        self.config_reads: list[str] = []

        #: The wait helper names found bound in the module namespace.
        self.wait_helper_names: tuple[str, ...] = ()

        #: The ``app.config`` accessor names found bound in the namespace.
        self.config_accessor_names: tuple[str, ...] = ()

    # -- installation ------------------------------------------------------ #

    def install(
        self,
        match: Any,
        monkeypatch: pytest.MonkeyPatch,
        *,
        config_values: dict[str, str] | None = None,
    ) -> StepProbe:
        """Install every recorder over the resolved step's module namespace.

        :param match: The ``StepMatch`` whose ``func.__globals__`` is patched.
        :param monkeypatch: pytest's patcher, which restores each name at
            teardown - the reason nothing here mutates the namespace directly.
        :param config_values: Values the configuration recorders answer with,
            keyed by accessor name.  Defaults to :data:`CONFIG_SENTINELS`;
            pass a mapping of ``None`` values to drive the unset-key path, and
            ``{}`` to leave the real accessors in place for an end-to-end test.
        :returns: This probe, so a caller can install and bind in one line.
        """
        namespace = match.func.__globals__
        pristine = pristine_namespace(namespace)
        answers = CONFIG_SENTINELS if config_values is None else config_values

        assert "sleep" in pristine, (
            f"{STEP_MODULE_NAME} binds no `sleep`; EmployeeStage.java's eight "
            f"Thread.sleep calls have no seam to be recorded at"
        )
        monkeypatch.setitem(namespace, "sleep", self._record_sleep)

        self.wait_helper_names = tuple(
            sorted(
                name
                for name, value in pristine.items()
                if name.startswith("wait_") and callable(value)
            )
        )
        assert self.wait_helper_names, (
            f"{STEP_MODULE_NAME} binds no wait helper; EmployeeStage.java:14 "
            f"builds a 3-second WebDriverWait used at six call sites"
        )

        for name in self.wait_helper_names:
            monkeypatch.setitem(namespace, name, self._make_wait_recorder(name))

        # Identity against ``app.config``'s own attributes, so an accessor the
        # module should not be reading is detected as well as the three it
        # should.  Dunder names are excluded because every module shares one
        # ``__builtins__``, which would otherwise match by identity.
        self.config_accessor_names = tuple(
            sorted(
                name
                for name, value in pristine.items()
                if not name.startswith("_") and getattr(config, name, _UNSET) is value
            )
        )

        if answers:
            for name in self.config_accessor_names:
                monkeypatch.setitem(namespace, name, self._make_config_recorder(name, answers))

        return self

    # -- the recorders ----------------------------------------------------- #

    def _record_sleep(self, seconds: Any) -> None:
        """Stand in for ``time.sleep``: record the delay and return at once.

        :param seconds: The delay as the call site passed it, kept unconverted
            so that ``sleep(3)`` and ``sleep(3.0)`` stay distinguishable.
        :returns: ``None``, as ``time.sleep`` does.
        """
        self.sleeps.append(seconds)
        self.driver.calls.append(("sleep", (seconds,)))

    def _make_wait_recorder(self, name: str) -> Any:
        """Build the stand-in for one wait helper.

        :param name: The helper's name in the module namespace, which decides
            the canonical kind the call is recorded under.
        :returns: A callable with the helper's calling convention.
        """

        def recorder(*args: Any, **kwargs: Any) -> Any:
            """Record one wait and resolve it at once, without polling.

            :param args: Positional arguments the call site passed.
            :param kwargs: Keyword arguments the call site passed.
            :returns: The element the wait was given, or ``True`` for a
                locator or title target - the contract of the helper it
                stands in for, and discarded by every call site in this
                module either way.
            """
            target, timeout = _wait_arguments(name, args, kwargs)
            resolved, from_element = _normalise_wait_target(name, target)
            call = WaitCall(_wait_kind(name), resolved, timeout, from_element)
            self.waits.append(call)
            self.driver.calls.append(("wait", (call.kind, call.target, call.timeout)))

            # ``wait_visible_element`` resolves to the element it was given and
            # ``wait_title_is`` to a boolean.  Every call site in this module
            # discards the result, so returning the argument (or ``True``)
            # reproduces the contract without performing a further lookup that
            # would show up in the log as an effect the port never caused.
            return target if from_element else True

        return recorder

    def _make_config_recorder(self, name: str, answers: dict[str, str]) -> Any:
        """Build the stand-in for one ``app.config`` accessor.

        :param name: The accessor's name, recorded on every call.
        :param answers: Mapping of accessor name to the value to return.
        :returns: A zero-argument callable, matching every accessor in
            ``app/config.py``.
        :raises AssertionError: If the accessor is called with arguments, which
            none of the six accessors accepts.
        """

        def recorder(*args: Any, **kwargs: Any) -> Any:
            """Record one configuration read and answer it from *answers*.

            :param args: Must be empty; present only to detect a call that
                passes arguments no accessor accepts.
            :param kwargs: Must be empty, for the same reason.
            :returns: The value programmed for this accessor, or ``None``
                when none was - which is what an unset key resolves to.
            :raises AssertionError: If any argument was passed.
            """
            assert not args and not kwargs, (
                f"{name} was called with {args!r}/{kwargs!r}; every app.config "
                f"accessor takes no arguments"
            )
            self.config_reads.append(name)
            return answers.get(name)

        return recorder


def _wait_kind(name: str) -> str:
    """Canonical wait kind for a helper name.

    :param name: The helper's name, such as ``wait_visible_element``.
    :returns: The alias from :data:`WAIT_KIND_ALIASES`, or the name with its
        ``wait_`` prefix removed.
    """
    return WAIT_KIND_ALIASES.get(name, name.removeprefix("wait_"))


def _wait_arguments(
    name: str, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> tuple[Any, Any]:
    """Extract the target and the timeout from one wait call.

    Every helper in ``app/automation/waits.py`` takes its target first and its
    timeout second, with ``driver`` keyword-only; both forms - positional and
    ``timeout=`` - are accepted here so that a call-site refactor is not
    mistaken for a parity failure.

    :param name: The helper's name, for the failure messages.
    :param args: Positional arguments as passed.
    :param kwargs: Keyword arguments as passed.
    :returns: The ``(target, timeout)`` pair.
    :raises AssertionError: If no target, no timeout, or an unexpected extra
        positional argument is present - each of which means the port is
        calling a differently shaped predicate, which is a behaviour change
        this module must report rather than absorb.
    """
    positional = list(args)
    target = positional.pop(0) if positional else _keyword_target(name, kwargs)

    if positional:
        timeout = positional.pop(0)
    else:
        timeout = kwargs.get("timeout", _UNSET)

    assert timeout is not _UNSET, (
        f"{name} was called without a timeout ({args!r}, {kwargs!r}); "
        f"EmployeeStage.java:14 fixes every wait in the class at "
        f"{WAIT_TIMEOUT} seconds and no helper supplies a default"
    )
    assert not positional, (
        f"{name} was called with unexpected extra positional arguments "
        f"{tuple(positional)!r}; EmployeeStage.java waits only on "
        f"visibilityOf(element) and titleIs(title)"
    )

    return target, timeout


def _keyword_target(name: str, kwargs: dict[str, Any]) -> Any:
    """Find a wait's target among its keyword arguments.

    :param name: The helper's name, for the failure message.
    :param kwargs: Keyword arguments as passed.
    :returns: The first value found under :data:`WAIT_TARGET_KEYWORDS`.
    :raises AssertionError: When none of them is present.
    """
    for keyword in WAIT_TARGET_KEYWORDS:
        if keyword in kwargs:
            return kwargs[keyword]

    raise AssertionError(
        f"{name} was called with no target: {kwargs!r}. Expected one "
        f"positionally or under one of {WAIT_TARGET_KEYWORDS}"
    )


def _normalise_wait_target(name: str, target: Any) -> tuple[Any, bool]:
    """Reduce a wait target to the locator or title it identifies.

    :param name: The helper's name, for the failure message.
    :param target: A located element, a ``(strategy, value)`` locator pair or
        an expected-title string.
    :returns: ``(normalised target, whether it arrived as an element)``.
    :raises AssertionError: For any other kind of target, which would mean the
        port is waiting on something neither Java call site waits on.
    """
    locator = getattr(target, "locator", None)

    if locator is not None:
        return tuple(locator), True

    if isinstance(target, (tuple, list)) and len(target) == 2:
        return tuple(target), False

    if isinstance(target, str):
        return target, False

    raise AssertionError(
        f"{name} was called with an unrecognised target {target!r}; "
        f"EmployeeStage.java waits on a located element or a title string"
    )


# =========================================================================== #
# Views over the recorded log
# =========================================================================== #


def _flatten(entries: Any) -> tuple[tuple[Any, ...], ...]:
    """Render log entries as flat tuples, which read better in a failure diff.

    :param entries: ``(operation, args)`` pairs from the recorder's log.
    :returns: One ``(operation, *args)`` tuple per entry, in order.
    """
    return tuple((operation, *arguments) for operation, arguments in entries)


def timeline(driver: Any) -> tuple[tuple[Any, ...], ...]:
    """Everything the step did, in order, with lookups filtered out.

    The view that asserts *position*: DOM effects, fixed delays and explicit
    waits interleaved exactly as the step performed them.  Lookups are dropped
    because whether a wait argument causes one depends on the call site's
    shape, which these tests deliberately do not fix.

    :param driver: The ``StubDriver`` the step ran against.
    :returns: The ordered timeline.
    """
    return _flatten(
        (operation, arguments)
        for operation, arguments in driver.calls
        if operation not in LOOKUP_OPERATIONS
    )


def effectful(driver: Any) -> tuple[tuple[Any, ...], ...]:
    """The step's effects on the browser, in order: navigation, DOM and reads.

    Delays and waits are excluded as well as lookups, so this is the sequence
    of things the step *did to the page* - the half of the parity fact that a
    change in ``app/automation`` cannot legitimately alter.

    :param driver: The ``StubDriver`` the step ran against.
    :returns: The ordered effect sequence.
    """
    return _flatten(
        (operation, arguments)
        for operation, arguments in driver.calls
        if operation not in LOOKUP_OPERATIONS and operation not in PROBE_OPERATIONS
    )


def assert_one_lookup_per_access(driver: Any, probe: StepProbe) -> None:
    """Assert the un-cached accessor contract: one ``find_element`` per access.

    ``PageFactory``'s proxy re-located on every invocation
    (``LoginP.java:9-11``), so the port's accessors must too.  The expected
    count is *derived from what was recorded* - one lookup per element
    operation, plus one per wait that was handed an element - which keeps the
    assertion exact while surviving a wait call site that takes a locator
    instead of an element.

    :param driver: The ``StubDriver`` the step ran against.
    :param probe: The probe whose waits were recorded.
    :returns: ``None``.
    """
    element_operations = sum(
        1 for operation, _ in driver.calls if operation.startswith(ELEMENT_PREFIX)
    )
    element_waits = sum(1 for wait in probe.waits if wait.from_element)
    expected = element_operations + element_waits

    assert driver.count_of("find_element") == expected, (
        f"expected {expected} find_element calls - one per accessor access, "
        f"{element_operations} for element operations and {element_waits} for "
        f"element-target waits - but the log holds "
        f"{driver.count_of('find_element')}: {driver.operations()}"
    )
    assert driver.count_of("find_elements") == 0, (
        f"EmployeeP.java declares no List<WebElement> field, so no plural "
        f"lookup may happen: {driver.calls}"
    )


def assert_message_less(excinfo: pytest.ExceptionInfo[AssertionError]) -> None:
    """Assert a raised ``AssertionError`` carries no message.

    All four live assertions - ``EmployeeStage.java:32``, ``:52``, ``:78`` and
    ``:88`` - are single-argument ``Assert.assertTrue`` calls with no message
    overload, so the port's asserts must stay message-less too.  A step module
    is not rewritten by pytest, so a bare ``assert`` there produces an
    ``AssertionError`` with empty ``args``.

    :param excinfo: pytest's captured exception information.
    :returns: ``None``.
    """
    assert excinfo.value.args == (), (
        f"EmployeeStage.java asserts without a message; the port raised "
        f"AssertionError{excinfo.value.args!r}"
    )
    assert str(excinfo.value) == ""


# =========================================================================== #
# Driving a step
# =========================================================================== #


def prepare(
    resolve_step: Any,
    monkeypatch: pytest.MonkeyPatch,
    context: Any,
    phrase: str,
    *,
    config_values: dict[str, str] | None = None,
) -> tuple[Any, StepProbe]:
    """Resolve one phrase and install the recorders over its module namespace.

    Separate from running it, because half of these tests run the body inside
    ``pytest.raises`` to drive an assertion's failing arrangement.

    :param resolve_step: The ``resolve_step`` fixture.
    :param monkeypatch: pytest's patcher.
    :param context: The ``fake_context`` fixture, carrying the ``StubDriver``.
    :param phrase: The concrete Gherkin phrase to resolve.
    :param config_values: Passed through to :meth:`StepProbe.install`.
    :returns: The ``(StepMatch, StepProbe)`` pair.
    """
    match = resolve_step(phrase)
    probe = StepProbe(context.driver).install(match, monkeypatch, config_values=config_values)
    return match, probe


def drive(
    resolve_step: Any,
    monkeypatch: pytest.MonkeyPatch,
    context: Any,
    phrase: str,
    *,
    config_values: dict[str, str] | None = None,
) -> tuple[Any, StepProbe]:
    """Resolve, instrument and run one step, then hand back what it did.

    :param resolve_step: The ``resolve_step`` fixture.
    :param monkeypatch: pytest's patcher.
    :param context: The ``fake_context`` fixture.
    :param phrase: The concrete Gherkin phrase to run.
    :param config_values: Passed through to :meth:`StepProbe.install`.
    :returns: The ``(StepMatch, StepProbe)`` pair, after the body has run.
    """
    match, probe = prepare(
        resolve_step, monkeypatch, context, phrase, config_values=config_values
    )
    match.run(context)
    return match, probe


def create_employee_phrase(name: str) -> str:
    """The concrete phrase for the one parameterized step.

    :param name: The employee name to embed, quoted exactly as
        ``EmployeeFc.feature:18`` and ``:28`` quote their ``<name>`` value.
    :returns: The phrase behave resolves against the registered pattern.
    """
    return f'User creates new employees "{name}" in the Employees stage'


# =========================================================================== #
# Coverage bookkeeping
#
# The parity obligation is that no step method goes untested, and the only way
# to hold that is to enumerate.  Two registers do it, and the last test in the
# file reconciles them:
#
#   * :data:`PARITY_TESTS` is the static declaration - pattern to the tests
#     that pin it - and is compared against the registry, so adding or
#     deleting a definition fails.
#   * :data:`COVERED_PATTERNS` is the run-time record, populated by
#     :func:`covers` from the pattern of each step actually resolved, so a test
#     that no longer reaches its step fails too.
#
# ``EXECUTED_TESTS`` is what keeps the reconciliation honest under a partial
# selection: a pattern may be uncovered only when none of its tests ran.
# =========================================================================== #

#: Patterns recorded as covered while the tests ran.
COVERED_PATTERNS: set[str] = set()

#: Names of the tests in this module that pytest actually executed.
EXECUTED_TESTS: set[str] = set()


def covers(match: Any) -> Any:
    """Record that the resolved step's pattern has been exercised.

    Keyed on ``match.pattern`` rather than on the phrase, so the one
    parameterized step is recorded once however many concrete names drive it.

    :param match: The ``StepMatch`` that was run or inspected.
    :returns: The same match, so a caller can wrap an expression in it.
    """
    COVERED_PATTERNS.add(match.pattern)
    return match


@pytest.fixture(autouse=True)
def _record_executed_test(request: pytest.FixtureRequest) -> None:
    """Record every test in this module that pytest runs.

    Module-local and autouse, so the final reconciliation can tell "this step
    has no test" - a parity gap - from "this test was not selected", which a
    ``-k`` run makes routine and which must not be reported as a gap.

    :param request: pytest's request object, for the running node's name.
    :returns: ``None``.
    """
    EXECUTED_TESTS.add(request.node.name)
    original = getattr(request.node, "originalname", None)

    if original:
        EXECUTED_TESTS.add(original)


# =========================================================================== #
# The step module's source, parsed once
# =========================================================================== #


@functools.cache
def module_source() -> str:
    """The step module's source text.

    :returns: ``features/steps/employee_steps.py`` decoded as UTF-8.
    """
    return STEP_MODULE_PATH.read_text(encoding="utf-8")


@functools.cache
def module_tree() -> ast.Module:
    """The step module's parsed syntax tree.

    Parsing the file is the only way to make a statement about what the module
    *does not* contain - an import it must not carry, a decorator it must not
    use, an assertion that must stay absent - because behave execs the module
    and the resulting namespace records none of that.

    :returns: The parsed module.
    """
    return ast.parse(module_source(), filename=str(STEP_MODULE_PATH))


@functools.cache
def step_function_nodes() -> dict[str, ast.FunctionDef]:
    """The module's decorated step functions, by name.

    :returns: Mapping of function name to its ``FunctionDef`` node, for every
        top-level function carrying at least one decorator.
    """
    return {
        node.name: node
        for node in module_tree().body
        if isinstance(node, ast.FunctionDef) and node.decorator_list
    }


def decorated_patterns() -> tuple[tuple[tuple[str, str], ...], tuple[str, ...]]:
    """The ``@step`` patterns and decorator names found in the source.

    :returns: A tuple of ``(function name, pattern)`` pairs in source order and
        the tuple of distinct decorator names used, so a test can assert both
        the order of the definitions and that ``step`` is the only decorator.
    """
    pairs: list[tuple[str, str]] = []
    names: list[str] = []

    for node in module_tree().body:
        if not isinstance(node, ast.FunctionDef):
            continue

        for decorator in node.decorator_list:
            call = decorator if isinstance(decorator, ast.Call) else None
            target = call.func if call is not None else decorator
            names.append(target.id if isinstance(target, ast.Name) else ast.unparse(target))

            if call is not None and call.args and isinstance(call.args[0], ast.Constant):
                pairs.append((node.name, call.args[0].value))

    return tuple(pairs), tuple(dict.fromkeys(names))


def imported_names() -> dict[str, tuple[str, ...]]:
    """Every ``from X import a, b`` in the module, as ``{module: names}``.

    :returns: Mapping of module to the names imported from it, in source order.
    :raises AssertionError: If a plain ``import X`` statement is present.
        The module's whole import surface is a handful of names, and the
        module-object form would let a wait helper be reached as
        ``app.automation.wait_visible`` - past the namespace seam every
        recorder in this file installs over.
    """
    found: dict[str, list[str]] = {}

    for node in ast.walk(module_tree()):
        if isinstance(node, ast.Import):
            raise AssertionError(
                f"{STEP_MODULE_NAME} uses a plain `import "
                f"{node.names[0].name}` statement; every import in the port's "
                f"step modules is a from-import of the names it uses"
            )

        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            found.setdefault(module, []).extend(alias.name for alias in node.names)

    return {module: tuple(names) for module, names in found.items()}


def module_matchers(registry: Any) -> dict[str, tuple[Any, ...]]:
    """The registry entries this step module owns, grouped by bucket.

    :param registry: behave's ``StepRegistry``, from the ``step_registry``
        fixture.
    :returns: Mapping of bucket name to the matchers whose source file is
        ``employee_steps.py``, in registration order - which is source order,
        because ``load_step_modules`` execs the file top to bottom.  Buckets
        holding none of this module's definitions are omitted.
    """
    owned: dict[str, tuple[Any, ...]] = {}

    for bucket, matchers in registry.steps.items():
        entries = tuple(
            matcher
            for matcher in matchers
            if Path(str(getattr(matcher.location, "filename", ""))).stem == STEP_MODULE_NAME
        )

        if entries:
            owned[bucket] = entries

    return owned


# =========================================================================== #
# The expected shape of each step, kept beside the tests that walk all twelve
# =========================================================================== #

#: ``EmployeeP.java:59-63`` - the three operations ``login()`` performs, in
#: order, with the credentials ``:60-61`` hard-codes.  Appears at three call
#: sites: ``EmployeeStage.java:25``, ``:61`` and ``:94``.
LOGIN_OPERATIONS: Final[tuple[tuple[Any, ...], ...]] = (
    ("element.send_keys", EmployeePage.INPUT_LOGIN, LOGIN_EMAIL),
    ("element.send_keys", EmployeePage.INPUT_PASS, LOGIN_PASSWORD),
    ("element.click", EmployeePage.LOGIN_BUTTON),
)

#: The title the driver must report for the three steps that assert one, so a
#: whole-module walk does not trip over an assertion it is not testing.  Every
#: other step gets :data:`UNEXPECTED_TITLE`, which also demonstrates that those
#: steps do not read the title at all.
TITLE_BY_PATTERN: Final[dict[str, str]] = {
    PHRASE_EMPLOYEES_STAGE: TITLE_EMPLOYEES,
    PHRASE_LAST_STAGE_TITLE: TITLE_DEPARTMENTS,
    PHRASE_LISTED_EMPLOYEES: TITLE_EMPLOYEES,
}

#: The fixed delays each step performs, in order: the eight ``Thread.sleep``
#: calls of ``EmployeeStage.java`` distributed across the four methods that
#: make them, and an empty tuple for the eight methods that make none.
SLEEPS_BY_PATTERN: Final[dict[str, tuple[int, ...]]] = {
    PHRASE_ORPHAN_LOGIN_PAGE: (),
    PHRASE_DASHBOARD: (),
    PHRASE_EMPLOYEES_STAGE: (),
    PHRASE_CHALLENGES_STAGE: (),
    PHRASE_DEPARTMENTS_STAGE: (SLEEP_LONG,),
    PHRASE_LAST_STAGE_TITLE: (),
    PHRASE_EMPLOYEES_DASHBOARD: (SLEEP_SHORT,),
    PATTERN_CREATE_EMPLOYEE: (SLEEP_SHORT, SLEEP_SHORT),
    PHRASE_CREATED_MESSAGE: (),
    PHRASE_LISTED_EMPLOYEES: (),
    PHRASE_EDIT_EMPLOYEE: (SLEEP_SHORT,) * 4,
    PHRASE_EDITED_NAME: (),
}

#: The explicit waits each step performs, in order, normalised to
#: ``(kind, target, timeout)``.  The configured-title wait at ``:31`` is the
#: only one whose target comes from configuration, which is why it is the
#: sentinel rather than a literal.
WAITS_BY_PATTERN: Final[dict[str, tuple[tuple[str, Any, int], ...]]] = {
    PHRASE_ORPHAN_LOGIN_PAGE: (),
    PHRASE_DASHBOARD: (),
    PHRASE_EMPLOYEES_STAGE: (
        ("title_is", CONFIG_SENTINELS["get_empl_title"], WAIT_TIMEOUT),
    ),
    PHRASE_CHALLENGES_STAGE: (
        ("visible", EmployeePage.BADGES_BTN, WAIT_TIMEOUT),
        ("visible", EmployeePage.CHALLENGES_BTN, WAIT_TIMEOUT),
        ("visible", EmployeePage.GOALS_HISTORY_BTN, WAIT_TIMEOUT),
    ),
    PHRASE_DEPARTMENTS_STAGE: (),
    PHRASE_LAST_STAGE_TITLE: (),
    PHRASE_EMPLOYEES_DASHBOARD: (),
    PATTERN_CREATE_EMPLOYEE: (("title_is", TITLE_NEW, WAIT_TIMEOUT),),
    PHRASE_CREATED_MESSAGE: (),
    PHRASE_LISTED_EMPLOYEES: (("title_is", TITLE_EMPLOYEES, WAIT_TIMEOUT),),
    PHRASE_EDIT_EMPLOYEE: (),
    PHRASE_EDITED_NAME: (),
}

#: The ``app.config`` accessors each step calls, in order.  Five reads over
#: three keys: ``web.table.url`` once, ``url`` at three separate call sites and
#: ``EmplTitle`` once.
CONFIG_READS_BY_PATTERN: Final[dict[str, tuple[str, ...]]] = {
    PHRASE_ORPHAN_LOGIN_PAGE: ("get_web_table_url",),
    PHRASE_DASHBOARD: ("get_url",),
    PHRASE_EMPLOYEES_STAGE: ("get_empl_title",),
    PHRASE_CHALLENGES_STAGE: (),
    PHRASE_DEPARTMENTS_STAGE: (),
    PHRASE_LAST_STAGE_TITLE: (),
    PHRASE_EMPLOYEES_DASHBOARD: ("get_url",),
    PATTERN_CREATE_EMPLOYEE: (),
    PHRASE_CREATED_MESSAGE: (),
    PHRASE_LISTED_EMPLOYEES: (),
    PHRASE_EDIT_EMPLOYEE: ("get_url",),
    PHRASE_EDITED_NAME: (),
}

#: How many ``assert`` statements each ported method's body must contain.  The
#: four live assertions of ``EmployeeStage.java:32``, ``:52``, ``:78`` and
#: ``:88`` and nothing else: the commented-out ``assertEquals`` blocks at
#: ``:53-55`` and ``:79-81`` must stay absent, and ``:107-110`` must stay a
#: click with no check.
ASSERT_COUNT_BY_FUNCTION: Final[dict[str, int]] = {
    "user_is_on_upgenix_login_page": 0,
    "user_is_on_the_dashboard": 0,
    "user_clicks_employees_stage": 1,
    "user_clicks_challenges_stage": 0,
    "user_clicks_departments_stage": 0,
    "user_should_see_the_last_stage_title": 1,
    "user_is_on_the_employees_dashboard": 0,
    "user_creates_new_employees_in_the_employees_stage": 0,
    "user_should_see_the_message_under_full_profile": 1,
    "user_should_see_listed_employees_in_the_employees_stage": 1,
    "user_edits_created_employees_in_the_employees_module": 0,
    "user_should_see_the_edited_name_in_the_employees_module": 0,
}

#: Names that must not appear anywhere in the step module.  ``print`` and the
#: keyboard/action-chain helpers are the negatives the Employee class has -
#: unlike ``Sales.java`` and ``Notes.java`` it prints nothing, sends no key and
#: builds no action chain - and the rest would mean the module had reached past
#: ``app.automation`` for a driver or a wait it must not own.
FORBIDDEN_NAMES: Final[tuple[str, ...]] = (
    "print",
    "Keys",
    "Actions",
    "ActionChains",
    "send_key",
    "build_chain",
    "WebDriverWait",
    "ExpectedConditions",
    "EC",
    "webdriver",
    "get_driver",
    "create_driver",
    "quit_driver",
    "get_property",
)

#: Driver methods no step may call.  Lifecycle belongs to
#: ``app/automation/driver.py`` and ``features/environment.py`` alone (AAP
#: 0.3.3: "no step or page ever creates or quits a driver"), and none of the
#: twelve Java bodies touches the window, the history or the script engine.
FORBIDDEN_DRIVER_CALLS: Final[tuple[str, ...]] = (
    "quit",
    "close",
    "maximize_window",
    "implicitly_wait",
    "execute_script",
    "get_screenshot_as_png",
    "back",
)

#: Log operations that would betray one of :data:`FORBIDDEN_DRIVER_CALLS`.
FORBIDDEN_OPERATIONS: Final[tuple[str, ...]] = (
    "quit",
    "maximize_window",
    "implicitly_wait",
    "execute_script",
    "execute",
    "back",
    "get_screenshot_as_png",
)

#: Modules the step module must not import, each for its own reason: the
#: browser library and its manager belong to ``app.automation`` alone, the
#: interaction helpers are for the modules that send keys, the properties
#: reader is reached only through ``app.config``, and the reporting, service
#: and web layers have no business in glue.
FORBIDDEN_IMPORTS: Final[tuple[str, ...]] = (
    "selenium",
    "webdriver_manager",
    "app.automation.interactions",
    "app.automation.driver",
    "app.automation.waits",
    "app.utils.properties",
    "app.utils",
    "app.utils.paths",
    "app.reporting",
    "app.services",
    "app.web",
    "app.logging_config",
)


def concrete_phrases() -> tuple[tuple[str, str], ...]:
    """Each registered pattern paired with a phrase that resolves it.

    :returns: ``(pattern, phrase)`` pairs in ``EmployeeStage.java`` order.  The
        eleven fixed patterns are their own phrases; the parameterized one is
        instantiated with the first ``Examples`` value the feature supplies.
    """
    return tuple(
        (
            pattern,
            create_employee_phrase(FEATURE_EMPLOYEE_NAMES[0])
            if pattern == PATTERN_CREATE_EMPLOYEE
            else pattern,
        )
        for pattern in ALL_PATTERNS
    )


def walk_every_step(
    resolve_step: Any,
    monkeypatch: pytest.MonkeyPatch,
    context: Any,
) -> dict[str, tuple[Any, StepProbe, tuple[tuple[Any, ...], ...]]]:
    """Run all twelve steps once each, against one recorder, and collect them.

    The log is cleared between steps so every collected timeline describes one
    step alone, and the title each assertion-carrying step expects is arranged
    first, so the walk exercises the passing arrangement of all four live
    assertions in passing.

    :param resolve_step: The ``resolve_step`` fixture.
    :param monkeypatch: pytest's patcher.
    :param context: The ``fake_context`` fixture.
    :returns: Mapping of pattern to ``(StepMatch, StepProbe, timeline)``.
    """
    walked: dict[str, tuple[Any, StepProbe, tuple[tuple[Any, ...], ...]]] = {}

    for pattern, phrase in concrete_phrases():
        context.driver.clear_calls()
        context.driver.title = TITLE_BY_PATTERN.get(pattern, UNEXPECTED_TITLE)
        match, probe = drive(resolve_step, monkeypatch, context, phrase)
        walked[pattern] = (match, probe, timeline(context.driver))

    return walked


# =========================================================================== #
# Registration: twelve definitions, one bucket, the Java order
# =========================================================================== #


def test_module_registers_exactly_the_twelve_java_methods(step_registry: Any) -> None:
    """Pins the class inventory: ``EmployeeStage.java`` declares twelve methods.

    ``EmployeeStage.java:16``, ``:22``, ``:28``, ``:35``, ``:45``, ``:50``,
    ``:58``, ``:66``, ``:76``, ``:84``, ``:91`` and ``:107``.  The comparison
    is against the ordered tuple, so a thirteenth definition, a deleted one, a
    reordered one or a re-worded phrase all fail here rather than reducing the
    parity suite's reach silently.
    """
    owned = module_matchers(step_registry)
    patterns = tuple(matcher.pattern for matcher in owned.get(STEP_BUCKET, ()))

    assert patterns == ALL_PATTERNS
    assert len(patterns) == len(ALL_PATTERNS) == 12
    assert len(set(patterns)) == 12, f"a phrase is registered twice: {patterns}"


def test_no_definition_is_bound_to_a_gherkin_keyword_bucket(step_registry: Any) -> None:
    """Pins AAP deviation 7: every definition registers with ``@step``.

    Cucumber-JVM matches on step text alone, so ``EmployeeStage.java``'s mix of
    ``@When`` (``:16``, ``:22``, ``:28``, ``:35``, ``:45``, ``:58``, ``:66``,
    ``:91``) and ``@Then`` (``:50``, ``:76``, ``:84``, ``:107``) carries no
    matching significance.  The port reproduces that with ``@step`` alone; a
    definition in a keyword bucket would stop resolving when a feature invoked
    it under a different keyword.
    """
    owned = module_matchers(step_registry)

    assert set(owned) == {STEP_BUCKET}

    for bucket in KEYWORD_BUCKETS:
        assert bucket not in owned, (
            f"{STEP_MODULE_NAME} registered "
            f"{[matcher.pattern for matcher in owned[bucket]]} under @{bucket}"
        )


def test_every_phrase_resolves_once_to_its_ported_java_method(resolve_step: Any) -> None:
    """Pins the phrase-to-method mapping for all twelve, ``:16`` to ``:107``.

    Each concrete phrase must resolve to exactly one definition - ``resolve_step``
    raises otherwise - owned by this module, registered in the ``step`` bucket,
    and implemented by the function that ports that Java method.  ``#9`` keeps
    the Java method's own shorter name, which is asserted rather than tidied.
    """
    for index, (pattern, phrase) in enumerate(concrete_phrases()):
        match = resolve_step(phrase)

        assert match.module_name == STEP_MODULE_NAME
        assert match.bucket == STEP_BUCKET
        assert match.pattern == pattern
        assert match.func.__name__ == STEP_FUNCTION_NAMES[index]


def test_step_decorators_are_step_only_and_declared_in_java_order() -> None:
    """Pins the source-level decorator surface of all twelve definitions.

    The registry says which bucket a definition landed in; the source says what
    was written.  Both are checked, because ``@step`` is the only decorator the
    port may use and the twelve patterns must appear in ``EmployeeStage.java``
    declaration order.
    """
    pairs, decorators = decorated_patterns()

    assert decorators == ("step",)
    assert pairs == tuple(zip(STEP_FUNCTION_NAMES, ALL_PATTERNS, strict=True))


def test_import_boundary_is_the_five_permitted_modules() -> None:
    """Pins AAP 0.4.2's import boundary for ``employee_steps``.

    Five from-imports and nothing else: the fixed-delay primitive, behave's
    ``@step``, the wait helpers from ``app.automation``, the three ``app.config``
    accessors and ``EmployeePage``.  The names taken from ``app.automation`` are
    checked by shape rather than by identity, because which wait helper
    expresses ``visibilityOf`` is that package's business; what may not change
    is that waits are the only thing imported from it.
    """
    imports = imported_names()

    assert set(imports) == {"time", "behave", "app.automation", "app.config", "app.pages"}
    assert imports["time"] == ("sleep",)
    assert imports["behave"] == ("step",)
    assert imports["app.pages"] == ("EmployeePage",)
    assert sorted(imports["app.config"]) == sorted(CONFIG_ACCESSOR_KEYS)
    assert imports["app.automation"], "no wait helper is imported"
    assert all(name.startswith("wait_") for name in imports["app.automation"]), (
        f"app.automation supplies waits to this module and nothing else: "
        f"{imports['app.automation']}"
    )


def test_module_reaches_past_no_layer_and_names_no_forbidden_helper() -> None:
    """Pins the negatives: no browser library, no properties reader, no print.

    ``EmployeeStage.java`` sends no key, builds no action chain and prints
    nothing, and the port may not acquire any of those.  Nor may it import the
    browser library, the interaction helpers, the properties reader or any
    reporting, service or web module - the configuration surface it is allowed
    is the three ``app.config`` accessors.
    """
    imports = imported_names()

    for module in FORBIDDEN_IMPORTS:
        assert module not in imports, f"{STEP_MODULE_NAME} imports {module}"

    used_names = {node.id for node in ast.walk(module_tree()) if isinstance(node, ast.Name)}
    used_attributes = {
        node.attr for node in ast.walk(module_tree()) if isinstance(node, ast.Attribute)
    }

    for name in FORBIDDEN_NAMES:
        assert name not in used_names, f"{STEP_MODULE_NAME} references {name}"

    for call in FORBIDDEN_DRIVER_CALLS:
        assert call not in used_attributes, f"{STEP_MODULE_NAME} calls driver.{call}()"


def test_no_step_creates_configures_or_quits_a_session(
    resolve_step: Any,
    monkeypatch: pytest.MonkeyPatch,
    fake_context: Any,
    stub_driver: Any,
) -> None:
    """Pins AAP 0.3.3 behaviourally across all twelve bodies.

    ``features/environment.py`` owns the session: it is created in
    ``before_scenario`` and quit in ``after_scenario``, exactly as
    ``Hooks.java:11-18`` has it.  No step body may quit it, maximize it, set an
    implicit wait, run a script or navigate the history - and the twelve Java
    bodies do none of those either.
    """
    walked = walk_every_step(resolve_step, monkeypatch, fake_context)

    assert len(walked) == 12

    for pattern, (_, _, recorded) in walked.items():
        operations = {entry[0] for entry in recorded}

        for forbidden in FORBIDDEN_OPERATIONS:
            assert forbidden not in operations, f"{pattern!r} performed {forbidden}"

    assert stub_driver.quit_count == 0


# =========================================================================== #
# The feature file, and the orphan that no feature file invokes
# =========================================================================== #


def test_feature_file_keeps_its_name_tag_and_header() -> None:
    """Pins ``EmployeeFc.feature:1-2`` and the file's own name.

    The AAP fixes the filename - the reference's ``EmployeeFc.feature``, not a
    tidied ``Employee.feature`` - and ``@UPGN-344`` is the only feature-level
    tag in the file.  Both are load-bearing: ``behave.ini``'s tag default and
    the Jenkins publisher select on them.
    """
    assert FEATURE_PATH.is_file(), f"{FEATURE_PATH} is missing"

    lines = FEATURE_PATH.read_text(encoding="utf-8").splitlines()

    assert lines[0].strip() == FEATURE_TAG
    assert lines[1].strip() == FEATURE_HEADER


def test_orphan_step_is_invoked_by_no_feature_file() -> None:
    """Pins the orphan of ``EmployeeStage.java:16-20``.

    ``User is on upgenix login page`` is registered and reached by no scenario
    in any of the ten feature files - it is a near-duplicate of
    ``LoginSD.java:19``'s ``User is on the upgenix login page``, which carries a
    "the" it lacks.  The port keeps it because the reference has it, which is
    why this module drives it directly instead.  The other eleven phrases are
    all invoked by ``EmployeeFc.feature``.
    """
    feature_files = sorted(FEATURES_DIR.glob("*.feature"))

    assert len(feature_files) == 10, f"expected ten feature files, found {feature_files}"

    corpus = {path: path.read_text(encoding="utf-8") for path in feature_files}
    invoking = [path.name for path, text in corpus.items() if PHRASE_ORPHAN_LOGIN_PAGE in text]

    assert invoking == [], (
        f"{PHRASE_ORPHAN_LOGIN_PAGE!r} is expected to be unreachable from every "
        f"feature file, but {invoking} invokes it"
    )

    employee_text = corpus[FEATURE_PATH]

    for pattern in ALL_PATTERNS:
        if pattern == PHRASE_ORPHAN_LOGIN_PAGE:
            continue

        expected = (
            'User creates new employees "'
            if pattern == PATTERN_CREATE_EMPLOYEE
            else pattern
        )
        assert expected in employee_text, f"{FEATURE_PATH.name} never invokes {pattern!r}"


def test_page_class_exposes_the_fifteen_findby_constants_this_module_asserts() -> None:
    """Pins the constant names every locator assertion below is written against.

    ``EmployeeP.java:14-57`` in ``@FindBy`` order.  The *values* are
    ``tests/test_pages.py``'s subject; what this module needs is that the names
    it compares through still exist and still mean the same field, so that a
    renamed constant fails here instead of quietly turning a locator assertion
    into a comparison against a missing attribute.
    """
    assert tuple(EmployeePage.LOCATORS) == (
        "INPUT_LOGIN",
        "INPUT_PASS",
        "LOGIN_BUTTON",
        "EMPL_STAGE",
        "BADGES_BTN",
        "CHALLENGES_BTN",
        "GOALS_HISTORY_BTN",
        "DEPARTMENTS_BTN",
        "CREATE_BTN",
        "EMPLOYEES_NAME",
        "SAVED_MESSAGE",
        "CREATED_MESSAGE",
        "CHOOSE_EMPLOYEE",
        "EDIT_EMPLOYEE",
        "NAME_EDIT",
    )
    assert EmployeePage.PLURAL_LOCATORS == frozenset(), (
        "EmployeeP.java declares no List<WebElement> field; the suite's only "
        "plural locator is SalesP.java:69"
    )


# =========================================================================== #
# 1 - EmployeeStage.java:16-20.  The orphan.
# =========================================================================== #


def test_orphan_step_navigates_to_the_web_table_url(
    resolve_step: Any,
    monkeypatch: pytest.MonkeyPatch,
    fake_context: Any,
    stub_driver: Any,
) -> None:
    """Pins ``EmployeeStage.java:18-19``: read ``web.table.url``, then ``get``.

    The only read of ``web.table.url`` in the class, and the reason the key is
    asserted by its own sentinel: reading ``url`` here instead - the key the
    other three navigations use - would navigate to the wrong page and is
    exactly the confusion this test exists to catch.  One navigation, no
    lookup, no wait and no delay.
    """
    match, probe = drive(resolve_step, monkeypatch, fake_context, PHRASE_ORPHAN_LOGIN_PAGE)
    covers(match)

    expected = (("get", CONFIG_SENTINELS["get_web_table_url"]),)

    assert effectful(stub_driver) == expected
    assert timeline(stub_driver) == expected
    assert probe.config_reads == ["get_web_table_url"]
    assert probe.sleeps == []
    assert probe.waits == []
    assert_one_lookup_per_access(stub_driver, probe)


def test_orphan_step_passes_an_unset_url_through_unguarded(
    resolve_step: Any,
    monkeypatch: pytest.MonkeyPatch,
    fake_context: Any,
    stub_driver: Any,
) -> None:
    """Pins the None-tolerance of ``EmployeeStage.java:18-19``.

    ``ConfigurationReader.getProperty`` returns ``null`` for an undefined key
    and ``:19`` hands it straight to ``get()``; the reference validates
    nothing, so the port must not either.  A pre-emptive check here would fail
    a scenario the reference lets reach the driver.
    """
    unset = dict.fromkeys(CONFIG_SENTINELS, None)
    match, probe = drive(
        resolve_step,
        monkeypatch,
        fake_context,
        PHRASE_ORPHAN_LOGIN_PAGE,
        config_values=unset,
    )
    covers(match)

    assert effectful(stub_driver) == (("get", None),)
    assert probe.config_reads == ["get_web_table_url"]


# =========================================================================== #
# 2 - EmployeeStage.java:22-26
# =========================================================================== #


def test_dashboard_step_navigates_to_url_then_signs_in(
    resolve_step: Any,
    monkeypatch: pytest.MonkeyPatch,
    fake_context: Any,
    stub_driver: Any,
) -> None:
    """Pins ``EmployeeStage.java:24-25``: navigate to ``url``, then ``login()``.

    Navigation first and sign-in second, with the credentials
    ``EmployeeP.java:60-61`` hard-codes, carried over verbatim per AAP 0.8.
    Three separate lookups for the three login operations, because the Java
    ``PageFactory`` proxy re-located on every invocation.  No wait, no delay,
    and ``web.table.url`` is not read here.
    """
    match, probe = drive(resolve_step, monkeypatch, fake_context, PHRASE_DASHBOARD)
    covers(match)

    expected = (("get", CONFIG_SENTINELS["get_url"]), *LOGIN_OPERATIONS)

    assert effectful(stub_driver) == expected
    assert timeline(stub_driver) == expected
    assert probe.config_reads == ["get_url"]
    assert probe.sleeps == []
    assert probe.waits == []
    assert_one_lookup_per_access(stub_driver, probe)
    assert stub_driver.count_of("find_element") == 3


# =========================================================================== #
# 3 - EmployeeStage.java:28-33.  Waits on the configured title, asserts the
#     hard-coded one.
# =========================================================================== #


def test_employees_stage_waits_on_the_configured_title_and_asserts_the_literal(
    resolve_step: Any,
    monkeypatch: pytest.MonkeyPatch,
    fake_context: Any,
    stub_driver: Any,
) -> None:
    """Pins ``EmployeeStage.java:30-32`` and its two title sources.

    ``:31`` waits for the title configured under the mixed-case ``EmplTitle``
    key while ``:32`` asserts the hard-coded ``"Employees - Odoo"``.  The
    sentinel the recorder returns is deliberately *not* that literal, so
    collapsing the two sources into one - waiting on the literal, or asserting
    the configured value - fails here.
    """
    stub_driver.title = TITLE_EMPLOYEES
    match, probe = drive(resolve_step, monkeypatch, fake_context, PHRASE_EMPLOYEES_STAGE)
    covers(match)

    assert timeline(stub_driver) == (
        ("element.click", EmployeePage.EMPL_STAGE),
        ("wait", "title_is", CONFIG_SENTINELS["get_empl_title"], WAIT_TIMEOUT),
        ("title",),
    )
    assert effectful(stub_driver) == (
        ("element.click", EmployeePage.EMPL_STAGE),
        ("title",),
    )
    assert probe.waits == [
        WaitCall("title_is", CONFIG_SENTINELS["get_empl_title"], WAIT_TIMEOUT, False)
    ]
    assert probe.waits[0].target != TITLE_EMPLOYEES, (
        "the 3-second wait of EmployeeStage.java:31 takes the configured "
        "EmplTitle, not the literal the next line asserts"
    )
    assert probe.config_reads == ["get_empl_title"]
    assert probe.sleeps == []
    assert_one_lookup_per_access(stub_driver, probe)


def test_employees_stage_assertion_rejects_any_other_title(
    resolve_step: Any,
    monkeypatch: pytest.MonkeyPatch,
    fake_context: Any,
    stub_driver: Any,
) -> None:
    """Pins the failing arrangement of ``EmployeeStage.java:32``.

    ``Assert.assertTrue(getTitle().equals("Employees - Odoo"))`` is an exact
    equality with no message, so a near-miss title must fail and the
    ``AssertionError`` must carry nothing.  The wait still ran first, on the
    configured title, which is asserted here too because the failure must come
    from the comparison rather than from a skipped wait.
    """
    stub_driver.title = UNEXPECTED_TITLE
    match, probe = prepare(resolve_step, monkeypatch, fake_context, PHRASE_EMPLOYEES_STAGE)
    covers(match)

    with pytest.raises(AssertionError) as excinfo:
        match.run(fake_context)

    assert_message_less(excinfo)
    assert timeline(stub_driver) == (
        ("element.click", EmployeePage.EMPL_STAGE),
        ("wait", "title_is", CONFIG_SENTINELS["get_empl_title"], WAIT_TIMEOUT),
        ("title",),
    )
    assert probe.waits == [
        WaitCall("title_is", CONFIG_SENTINELS["get_empl_title"], WAIT_TIMEOUT, False)
    ]


# =========================================================================== #
# 4 - EmployeeStage.java:35-43.  Three click/wait pairs.
# =========================================================================== #


def test_challenges_stage_walks_three_click_then_wait_pairs(
    resolve_step: Any,
    monkeypatch: pytest.MonkeyPatch,
    fake_context: Any,
    stub_driver: Any,
) -> None:
    """Pins ``EmployeeStage.java:37-42``, waits included, in strict order.

    Badges, then Challenges, then Goals History, each click followed by a
    3-second visibility wait **on the element just clicked** rather than on the
    one about to be clicked.  That is what the source does and it is preserved,
    so the wait targets are asserted against the preceding click's locator
    explicitly - a "corrected" pairing would pass an order-only check.
    """
    match, probe = drive(resolve_step, monkeypatch, fake_context, PHRASE_CHALLENGES_STAGE)
    covers(match)

    assert timeline(stub_driver) == (
        ("element.click", EmployeePage.BADGES_BTN),
        ("wait", "visible", EmployeePage.BADGES_BTN, WAIT_TIMEOUT),
        ("element.click", EmployeePage.CHALLENGES_BTN),
        ("wait", "visible", EmployeePage.CHALLENGES_BTN, WAIT_TIMEOUT),
        ("element.click", EmployeePage.GOALS_HISTORY_BTN),
        ("wait", "visible", EmployeePage.GOALS_HISTORY_BTN, WAIT_TIMEOUT),
    )
    assert effectful(stub_driver) == (
        ("element.click", EmployeePage.BADGES_BTN),
        ("element.click", EmployeePage.CHALLENGES_BTN),
        ("element.click", EmployeePage.GOALS_HISTORY_BTN),
    )

    clicked = [entry[1] for entry in effectful(stub_driver)]

    assert [wait.kind for wait in probe.waits] == ["visible"] * 3
    assert [wait.target for wait in probe.waits] == clicked, (
        "each wait of EmployeeStage.java:38, :40 and :42 is visibilityOf the "
        "element the line before it clicked"
    )
    assert [wait.timeout for wait in probe.waits] == [WAIT_TIMEOUT] * 3
    assert probe.sleeps == []
    assert probe.config_reads == []
    assert_one_lookup_per_access(stub_driver, probe)


# =========================================================================== #
# 5 - EmployeeStage.java:45-49.  The suite's only 7-second delay.
# =========================================================================== #


def test_departments_stage_clicks_then_waits_out_seven_fixed_seconds(
    resolve_step: Any,
    monkeypatch: pytest.MonkeyPatch,
    fake_context: Any,
    stub_driver: Any,
) -> None:
    """Pins ``EmployeeStage.java:47-48``: one click, then ``Thread.sleep(7000)``.

    The only 7-second delay in the whole suite, and this step's only
    synchronization of any kind: the class has a ``WebDriverWait`` available at
    ``:14`` and this method does not use it.  The duration is asserted as 7 and
    not 3 - the value every other delay in the class carries - and the delay's
    position, after the click, is asserted through the timeline.
    """
    match, probe = drive(resolve_step, monkeypatch, fake_context, PHRASE_DEPARTMENTS_STAGE)
    covers(match)

    assert timeline(stub_driver) == (
        ("element.click", EmployeePage.DEPARTMENTS_BTN),
        ("sleep", SLEEP_LONG),
    )
    assert probe.sleeps == [SLEEP_LONG]
    assert probe.sleeps != [SLEEP_SHORT]
    assert probe.waits == [], (
        "EmployeeStage.java:45-49 builds no explicit wait; the fixed delay is "
        "the whole of its synchronization and must not be converted to one"
    )
    assert probe.config_reads == []
    assert_one_lookup_per_access(stub_driver, probe)


# =========================================================================== #
# 6 - EmployeeStage.java:50-56.  One assertion; :53-55 stay commented out.
# =========================================================================== #


def test_last_stage_title_asserts_the_departments_title(
    resolve_step: Any,
    monkeypatch: pytest.MonkeyPatch,
    fake_context: Any,
    stub_driver: Any,
) -> None:
    """Pins ``EmployeeStage.java:52``: one title read, one equality, nothing else.

    No click, no lookup, no wait and no delay - the step observes and compares.
    """
    stub_driver.title = TITLE_DEPARTMENTS
    match, probe = drive(resolve_step, monkeypatch, fake_context, PHRASE_LAST_STAGE_TITLE)
    covers(match)

    assert timeline(stub_driver) == (("title",),)
    assert effectful(stub_driver) == (("title",),)
    assert probe.sleeps == []
    assert probe.waits == []
    assert probe.config_reads == []
    assert_one_lookup_per_access(stub_driver, probe)


def test_last_stage_title_assertion_rejects_any_other_title(
    resolve_step: Any,
    monkeypatch: pytest.MonkeyPatch,
    fake_context: Any,
    stub_driver: Any,
) -> None:
    """Pins the failing arrangement of ``EmployeeStage.java:52``, message-less."""
    stub_driver.title = UNEXPECTED_TITLE
    match, _ = prepare(resolve_step, monkeypatch, fake_context, PHRASE_LAST_STAGE_TITLE)
    covers(match)

    with pytest.raises(AssertionError) as excinfo:
        match.run(fake_context)

    assert_message_less(excinfo)
    assert timeline(stub_driver) == (("title",),)


def test_last_stage_title_keeps_the_commented_assert_equals_absent(
    resolve_step: Any,
    monkeypatch: pytest.MonkeyPatch,
    fake_context: Any,
    stub_driver: Any,
) -> None:
    """Pins the *absence* of ``EmployeeStage.java:53-55``.

    Those three lines are a commented-out ``assertEquals`` formulation of the
    same check, holding an ``act`` and an ``exp`` local.  They carry no
    behaviour, so the port must not reproduce them, and three independent facts
    show they were not: the body holds exactly one ``assert``, it binds no
    local variable, and it reads the title exactly once - a ported ``:53``
    would read it a second time.
    """
    node = step_function_nodes()["user_should_see_the_last_stage_title"]
    assignments = [child for child in ast.walk(node) if isinstance(child, ast.Assign)]

    assert sum(isinstance(child, ast.Assert) for child in ast.walk(node)) == 1
    assert assignments == [], (
        f"EmployeeStage.java:53-54's act/exp locals are commented out and must "
        f"stay absent: {[ast.unparse(item) for item in assignments]}"
    )

    stub_driver.title = TITLE_DEPARTMENTS
    match, _ = drive(resolve_step, monkeypatch, fake_context, PHRASE_LAST_STAGE_TITLE)
    covers(match)

    assert stub_driver.count_of("title") == 1


# =========================================================================== #
# 7 - EmployeeStage.java:58-64
# =========================================================================== #


def test_employees_dashboard_navigates_signs_in_delays_then_opens_the_stage(
    resolve_step: Any,
    monkeypatch: pytest.MonkeyPatch,
    fake_context: Any,
    stub_driver: Any,
) -> None:
    """Pins ``EmployeeStage.java:60-63``, the second of the three ``url`` reads.

    Four statements, and the delay's position is the behaviour: it sits between
    the sign-in and the stage click, where ``:62`` puts it.  Lifting it, or
    replacing it with a wait on the stage link, would change the timing this
    step depends on.
    """
    match, probe = drive(resolve_step, monkeypatch, fake_context, PHRASE_EMPLOYEES_DASHBOARD)
    covers(match)

    assert timeline(stub_driver) == (
        ("get", CONFIG_SENTINELS["get_url"]),
        *LOGIN_OPERATIONS,
        ("sleep", SLEEP_SHORT),
        ("element.click", EmployeePage.EMPL_STAGE),
    )
    assert effectful(stub_driver) == (
        ("get", CONFIG_SENTINELS["get_url"]),
        *LOGIN_OPERATIONS,
        ("element.click", EmployeePage.EMPL_STAGE),
    )
    assert probe.sleeps == [SLEEP_SHORT]
    assert probe.waits == []
    assert probe.config_reads == ["get_url"]
    assert_one_lookup_per_access(stub_driver, probe)
    assert stub_driver.count_of("find_element") == 4


# =========================================================================== #
# 8 - EmployeeStage.java:66-74.  The one parameterized step.
# =========================================================================== #


def test_create_employee_delays_clicks_delays_waits_then_types_the_name(
    resolve_step: Any,
    monkeypatch: pytest.MonkeyPatch,
    fake_context: Any,
    stub_driver: Any,
) -> None:
    """Pins ``EmployeeStage.java:68-73``, both delays at their own positions.

    Six statements: a delay *before* the create button is clicked, a second one
    *between* that click and the title wait, then the 3-second wait for
    ``"New - Odoo"``, the name typed into the required-name input and the save
    control clicked.  The two delay positions are asserted through the
    timeline, because collapsing them into one - or hoisting either - is the
    change this step cannot survive.  The name arrives through the pattern's
    named field and is typed verbatim.
    """
    name = FEATURE_EMPLOYEE_NAMES[0]
    match, probe = drive(
        resolve_step, monkeypatch, fake_context, create_employee_phrase(name)
    )
    covers(match)

    assert match.kwargs == {"name": name}
    assert match.args == ()
    assert timeline(stub_driver) == (
        ("sleep", SLEEP_SHORT),
        ("element.click", EmployeePage.CREATE_BTN),
        ("sleep", SLEEP_SHORT),
        ("wait", "title_is", TITLE_NEW, WAIT_TIMEOUT),
        ("element.send_keys", EmployeePage.EMPLOYEES_NAME, name),
        ("element.click", EmployeePage.SAVED_MESSAGE),
    )
    assert probe.sleeps == [SLEEP_SHORT, SLEEP_SHORT]
    assert probe.waits == [WaitCall("title_is", TITLE_NEW, WAIT_TIMEOUT, False)]
    assert probe.config_reads == [], (
        "EmployeeStage.java:66-74 reads no property; the title it waits on is "
        "the hard-coded 'New - Odoo'"
    )
    assert_one_lookup_per_access(stub_driver, probe)


def test_create_employee_pattern_takes_the_quoted_examples_values(
    resolve_step: Any,
    monkeypatch: pytest.MonkeyPatch,
    fake_context: Any,
    stub_driver: Any,
) -> None:
    """Pins the ``{string}`` port of ``EmployeeStage.java:66``.

    Cucumber's ``{string}`` matches the quotation marks and passes the unquoted
    text; behave's ``parse`` matcher does not, so the port writes the quotes
    into the pattern around a named field.  Both ``Examples: Employee's name``
    values - ``EmployeeFc.feature:23`` and ``:33`` - must therefore reach the
    body unquoted, and the unquoted phrase must **not** resolve, which is what
    proves the quotes are part of the pattern rather than decoration.
    """
    for name in FEATURE_EMPLOYEE_NAMES:
        stub_driver.clear_calls()
        match, _ = drive(
            resolve_step, monkeypatch, fake_context, create_employee_phrase(name)
        )
        covers(match)

        assert match.pattern == PATTERN_CREATE_EMPLOYEE
        assert match.kwargs == {"name": name}
        assert stub_driver.calls_of("element.send_keys") == (
            (EmployeePage.EMPLOYEES_NAME, name),
        )

    with pytest.raises(AssertionError):
        resolve_step(
            f"User creates new employees {FEATURE_EMPLOYEE_NAMES[0]} in the Employees stage"
        )


# =========================================================================== #
# 9 - EmployeeStage.java:76-82.  One assertion; :79-81 stay commented out.
# =========================================================================== #


def test_created_message_asserts_the_confirmation_is_displayed(
    resolve_step: Any,
    monkeypatch: pytest.MonkeyPatch,
    fake_context: Any,
    stub_driver: Any,
) -> None:
    """Pins ``EmployeeStage.java:78``: one ``isDisplayed`` on the confirmation.

    The assertion subject is visibility, not text, and the element is the
    ``"Employee created"`` paragraph located by ``CREATED_MESSAGE``.  One
    lookup, one read, no wait and no delay.
    """
    stub_driver.set_displayed(EmployeePage.CREATED_MESSAGE, True)
    match, probe = drive(resolve_step, monkeypatch, fake_context, PHRASE_CREATED_MESSAGE)
    covers(match)

    assert timeline(stub_driver) == (
        ("element.is_displayed", EmployeePage.CREATED_MESSAGE),
    )
    assert stub_driver.calls_of("find_element") == (EmployeePage.CREATED_MESSAGE,)
    assert probe.sleeps == []
    assert probe.waits == []
    assert probe.config_reads == []
    assert_one_lookup_per_access(stub_driver, probe)


def test_created_message_assertion_fails_when_the_confirmation_is_hidden(
    resolve_step: Any,
    monkeypatch: pytest.MonkeyPatch,
    fake_context: Any,
    stub_driver: Any,
) -> None:
    """Pins the failing arrangement of ``EmployeeStage.java:78``, message-less."""
    stub_driver.set_displayed(EmployeePage.CREATED_MESSAGE, False)
    match, _ = prepare(resolve_step, monkeypatch, fake_context, PHRASE_CREATED_MESSAGE)
    covers(match)

    with pytest.raises(AssertionError) as excinfo:
        match.run(fake_context)

    assert_message_less(excinfo)
    assert timeline(stub_driver) == (
        ("element.is_displayed", EmployeePage.CREATED_MESSAGE),
    )


def test_created_message_keeps_the_commented_text_comparison_absent(
    resolve_step: Any,
    monkeypatch: pytest.MonkeyPatch,
    fake_context: Any,
    stub_driver: Any,
) -> None:
    """Pins the *absence* of ``EmployeeStage.java:79-81``.

    Those three lines read the element's text and compare it to
    ``"Employee created"``; they are commented out, so the port must not read
    the text at all.  The element's text is programmed to the value the
    commented code expected, which makes the omission observable: a ported
    ``:80`` would show up as a text read in the log.
    """
    node = step_function_nodes()["user_should_see_the_message_under_full_profile"]

    assert sum(isinstance(child, ast.Assert) for child in ast.walk(node)) == 1
    assert [child for child in ast.walk(node) if isinstance(child, ast.Assign)] == []

    stub_driver.set_text(EmployeePage.CREATED_MESSAGE, "Employee created")
    stub_driver.set_displayed(EmployeePage.CREATED_MESSAGE, True)
    match, _ = drive(resolve_step, monkeypatch, fake_context, PHRASE_CREATED_MESSAGE)
    covers(match)

    assert stub_driver.count_of("element.text") == 0, (
        "EmployeeStage.java:80's getText() is commented out; asserting on the "
        "text would add a failure mode the reference does not have"
    )
    assert stub_driver.count_of("element.is_displayed") == 1


# =========================================================================== #
# 10 - EmployeeStage.java:84-89.  Wait and assertion share one literal.
# =========================================================================== #


def test_listed_employees_waits_on_and_asserts_the_same_literal_title(
    resolve_step: Any,
    monkeypatch: pytest.MonkeyPatch,
    fake_context: Any,
    stub_driver: Any,
) -> None:
    """Pins ``EmployeeStage.java:86-88``, and the contrast with ``#3``.

    Here both the wait and the assertion take the hard-coded
    ``"Employees - Odoo"``: the source writes the literal twice and reads no
    property, which is the opposite of ``:31``.  ``EmplTitle`` must therefore
    not be read in this step - asserted directly, because a step that read it
    would still pass an order-only check.
    """
    stub_driver.title = TITLE_EMPLOYEES
    match, probe = drive(resolve_step, monkeypatch, fake_context, PHRASE_LISTED_EMPLOYEES)
    covers(match)

    assert timeline(stub_driver) == (
        ("element.click", EmployeePage.EMPL_STAGE),
        ("wait", "title_is", TITLE_EMPLOYEES, WAIT_TIMEOUT),
        ("title",),
    )
    assert probe.waits == [WaitCall("title_is", TITLE_EMPLOYEES, WAIT_TIMEOUT, False)]
    assert probe.config_reads == [], (
        "EmployeeStage.java:87 waits on the literal 'Employees - Odoo'; only "
        ":31 reads the EmplTitle property"
    )
    assert probe.sleeps == []
    assert_one_lookup_per_access(stub_driver, probe)


def test_listed_employees_assertion_rejects_any_other_title(
    resolve_step: Any,
    monkeypatch: pytest.MonkeyPatch,
    fake_context: Any,
    stub_driver: Any,
) -> None:
    """Pins the failing arrangement of ``EmployeeStage.java:88``, message-less."""
    stub_driver.title = UNEXPECTED_TITLE
    match, _ = prepare(resolve_step, monkeypatch, fake_context, PHRASE_LISTED_EMPLOYEES)
    covers(match)

    with pytest.raises(AssertionError) as excinfo:
        match.run(fake_context)

    assert_message_less(excinfo)
    assert timeline(stub_driver) == (
        ("element.click", EmployeePage.EMPL_STAGE),
        ("wait", "title_is", TITLE_EMPLOYEES, WAIT_TIMEOUT),
        ("title",),
    )


# =========================================================================== #
# 11 - EmployeeStage.java:91-105.  The longest body, four of the eight delays.
# =========================================================================== #


def test_edit_employee_walks_the_whole_twelve_statement_edit_flow(
    resolve_step: Any,
    monkeypatch: pytest.MonkeyPatch,
    fake_context: Any,
    stub_driver: Any,
) -> None:
    """Pins ``EmployeeStage.java:93-104``, the third ``url`` read included.

    Twelve statements in one order, with the four delays interleaved exactly
    where the source puts them: after the sign-in, after the stage click, after
    the edit click, and after the new name is typed.  ``"Sterling"`` is typed
    verbatim, the name field is reached twice - once to clear and once to type,
    so it is located twice - and nothing in the step asserts the rename took.
    """
    match, probe = drive(resolve_step, monkeypatch, fake_context, PHRASE_EDIT_EMPLOYEE)
    covers(match)

    assert timeline(stub_driver) == (
        ("get", CONFIG_SENTINELS["get_url"]),
        *LOGIN_OPERATIONS,
        ("sleep", SLEEP_SHORT),
        ("element.click", EmployeePage.EMPL_STAGE),
        ("sleep", SLEEP_SHORT),
        ("element.click", EmployeePage.CHOOSE_EMPLOYEE),
        ("element.click", EmployeePage.EDIT_EMPLOYEE),
        ("sleep", SLEEP_SHORT),
        ("element.clear", EmployeePage.NAME_EDIT),
        ("element.send_keys", EmployeePage.NAME_EDIT, EDITED_NAME),
        ("sleep", SLEEP_SHORT),
        ("element.click", EmployeePage.SAVED_MESSAGE),
    )
    assert probe.sleeps == [SLEEP_SHORT] * 4
    assert probe.waits == [], (
        "EmployeeStage.java:91-105 builds no explicit wait; its four fixed "
        "delays are the whole of its synchronization"
    )
    assert probe.config_reads == ["get_url"]
    assert stub_driver.calls_of("find_element").count(EmployeePage.NAME_EDIT) == 2, (
        "EmployeeStage.java:101-102 reach the name field twice, so the "
        "un-cached accessor must locate it twice"
    )
    assert_one_lookup_per_access(stub_driver, probe)


# =========================================================================== #
# 12 - EmployeeStage.java:107-110.  A click, and no assertion.
# =========================================================================== #


def test_edited_name_step_clicks_and_checks_nothing(
    resolve_step: Any,
    monkeypatch: pytest.MonkeyPatch,
    fake_context: Any,
    stub_driver: Any,
) -> None:
    """Pins ``EmployeeStage.java:109`` and the absence of any check after it.

    The phrase says the user "should see the edited name" and the source
    asserts nothing at all: it neither reads the title nor looks for the name,
    so the step can only fail if the click fails.  Inventing a check would add
    a way for the scenario to fail that the reference does not have, which AAP
    0.1.2 forbids - so the body must hold no ``assert``, read no title, and
    pass while the browser is showing an unrelated page.
    """
    node = step_function_nodes()["user_should_see_the_edited_name_in_the_employees_module"]

    assert sum(isinstance(child, ast.Assert) for child in ast.walk(node)) == 0

    stub_driver.title = UNEXPECTED_TITLE
    match, probe = drive(resolve_step, monkeypatch, fake_context, PHRASE_EDITED_NAME)
    covers(match)

    assert timeline(stub_driver) == (("element.click", EmployeePage.EMPL_STAGE),)
    assert stub_driver.count_of("title") == 0
    assert probe.sleeps == []
    assert probe.waits == []
    assert probe.config_reads == []
    assert_one_lookup_per_access(stub_driver, probe)


# =========================================================================== #
# Cross-cutting censuses: the counts the class as a whole fixes
# =========================================================================== #


def test_the_three_login_call_sites_each_perform_the_same_three_operations(
    resolve_step: Any,
    monkeypatch: pytest.MonkeyPatch,
    fake_context: Any,
    stub_driver: Any,
) -> None:
    """Pins ``EmployeeStage.java:25``, ``:61`` and ``:94`` as three separate sites.

    Each navigates to the ``url`` property and then calls ``login()`` with no
    arguments, and each ``login()`` performs the three operations of
    ``EmployeeP.java:60-62`` in order through three separate lookups.  The
    ``url`` read is counted here too: three reads at three call sites, which
    must not be collapsed into one hoisted read.
    """
    reads: list[str] = []

    for phrase in (PHRASE_DASHBOARD, PHRASE_EMPLOYEES_DASHBOARD, PHRASE_EDIT_EMPLOYEE):
        stub_driver.clear_calls()
        match, probe = drive(resolve_step, monkeypatch, fake_context, phrase)
        covers(match)
        reads.extend(probe.config_reads)

        recorded = effectful(stub_driver)

        assert recorded[0] == ("get", CONFIG_SENTINELS["get_url"])
        assert recorded[1:4] == LOGIN_OPERATIONS, (
            f"{phrase!r} must sign in with the three operations of "
            f"EmployeeP.java:60-62 immediately after navigating: {recorded}"
        )
        assert stub_driver.calls_of("find_element")[:3] == (
            EmployeePage.INPUT_LOGIN,
            EmployeePage.INPUT_PASS,
            EmployeePage.LOGIN_BUTTON,
        )

    assert reads == ["get_url"] * 3


def test_configuration_keys_resolve_through_app_config_end_to_end(
    request: pytest.FixtureRequest,
    resolve_step: Any,
    monkeypatch: pytest.MonkeyPatch,
    fake_context: Any,
    stub_driver: Any,
) -> None:
    """Pins the three key *names* - ``web.table.url``, ``url``, ``EmplTitle``.

    The other configuration assertions in this module replace the accessors
    with recorders, which proves *which accessor* a step calls.  This one
    leaves the real accessors in place and installs values through
    ``app.config.set_userdata`` - the behave-userdata channel AAP 0.4.1 puts
    ahead of the properties file - so it proves the keys themselves: swapping
    ``url`` for ``web.table.url`` anywhere in the chain lands the wrong value
    on the driver, and the mixed-case ``EmplTitle`` of ``EmployeeStage.java:31``
    must stay mixed case to resolve at all.
    """
    request.addfinalizer(lambda: config.set_userdata(None))
    config.set_userdata(USERDATA_VALUES)

    assert config.get_web_table_url() == USERDATA_VALUES["web.table.url"]
    assert config.get_url() == USERDATA_VALUES["url"]
    assert config.get_empl_title() == USERDATA_VALUES["EmplTitle"]

    stub_driver.clear_calls()
    match, _ = drive(
        resolve_step, monkeypatch, fake_context, PHRASE_ORPHAN_LOGIN_PAGE, config_values={}
    )
    covers(match)

    assert stub_driver.calls_of("get") == ((USERDATA_VALUES["web.table.url"],),)

    stub_driver.clear_calls()
    match, _ = drive(
        resolve_step, monkeypatch, fake_context, PHRASE_DASHBOARD, config_values={}
    )
    covers(match)

    assert stub_driver.calls_of("get") == ((USERDATA_VALUES["url"],),)

    stub_driver.clear_calls()
    stub_driver.title = TITLE_EMPLOYEES
    match, probe = drive(
        resolve_step, monkeypatch, fake_context, PHRASE_EMPLOYEES_STAGE, config_values={}
    )
    covers(match)

    assert probe.waits == [
        WaitCall("title_is", USERDATA_VALUES["EmplTitle"], WAIT_TIMEOUT, False)
    ]


def test_the_eight_fixed_delays_are_one_of_seven_seconds_and_seven_of_three(
    resolve_step: Any,
    monkeypatch: pytest.MonkeyPatch,
    fake_context: Any,
) -> None:
    """Pins the class's whole fixed-delay census, per step and in total.

    ``EmployeeStage.java`` sleeps eight times: 7 seconds at ``:48`` and 3
    seconds at ``:62``, ``:68``, ``:70``, ``:95``, ``:97``, ``:100`` and
    ``:103``.  Per-step counts and orders are asserted here; each delay's
    *position* inside its step is asserted by that step's own test.  The eight
    other methods sleep not at all, which is asserted too - an added delay is
    as much a behaviour change as a removed one.
    """
    walked = walk_every_step(resolve_step, monkeypatch, fake_context)
    recorded = {pattern: tuple(probe.sleeps) for pattern, (_, probe, _) in walked.items()}
    every_delay = [seconds for delays in recorded.values() for seconds in delays]

    assert recorded == SLEEPS_BY_PATTERN
    assert sorted(every_delay) == sorted(ALL_SLEEP_SECONDS)
    assert len(every_delay) == 8
    assert every_delay.count(SLEEP_LONG) == 1
    assert every_delay.count(SLEEP_SHORT) == 7


def test_the_six_explicit_waits_all_carry_the_three_second_timeout(
    resolve_step: Any,
    monkeypatch: pytest.MonkeyPatch,
    fake_context: Any,
) -> None:
    """Pins ``EmployeeStage.java:14``'s single 3-second wait across six sites.

    One ``WebDriverWait(Driver.getDriver(), 3)`` is built for the whole class
    and used at ``:31``, ``:38``, ``:40``, ``:42``, ``:71`` and ``:87``, so
    every timeout in the module is 3 - three ``titleIs`` waits and three
    ``visibilityOf`` waits, on the targets each line names.  The six other
    methods wait not at all.
    """
    walked = walk_every_step(resolve_step, monkeypatch, fake_context)
    recorded = {
        pattern: tuple((wait.kind, wait.target, wait.timeout) for wait in probe.waits)
        for pattern, (_, probe, _) in walked.items()
    }
    every_wait = [wait for waits in recorded.values() for wait in waits]

    assert recorded == WAITS_BY_PATTERN
    assert len(every_wait) == 6
    assert {wait[2] for wait in every_wait} == {WAIT_TIMEOUT}
    assert [wait[0] for wait in every_wait].count("title_is") == 3
    assert [wait[0] for wait in every_wait].count("visible") == 3


def test_every_configuration_read_in_the_class_is_one_of_the_five(
    resolve_step: Any,
    monkeypatch: pytest.MonkeyPatch,
    fake_context: Any,
) -> None:
    """Pins the five configuration reads over three keys, per step.

    ``web.table.url`` at ``:18``; ``url`` at ``:24``, ``:60`` and ``:93``;
    ``EmplTitle`` at ``:31``.  The remaining seven methods read nothing, and
    the accessors bound in the module namespace are exactly the three the port
    is allowed - a fourth would mean the configuration surface had grown past
    the six keys AAP 0.4.1 inventories.
    """
    walked = walk_every_step(resolve_step, monkeypatch, fake_context)
    recorded = {
        pattern: tuple(probe.config_reads) for pattern, (_, probe, _) in walked.items()
    }
    every_read = [read for reads in recorded.values() for read in reads]

    assert recorded == CONFIG_READS_BY_PATTERN
    assert len(every_read) == 5
    assert every_read.count("get_url") == 3
    assert every_read.count("get_web_table_url") == 1
    assert every_read.count("get_empl_title") == 1

    for _, (_, probe, _) in walked.items():
        assert probe.config_accessor_names == tuple(sorted(CONFIG_ACCESSOR_KEYS))


def test_the_module_asserts_at_exactly_the_four_live_java_sites() -> None:
    """Pins the four live assertions and the absence of every other one.

    ``EmployeeStage.java`` asserts at ``:32``, ``:52``, ``:78`` and ``:88`` and
    nowhere else.  The two commented-out ``assertEquals`` blocks at ``:53-55``
    and ``:79-81`` must stay absent and ``:107-110`` must stay a bare click, so
    the per-body counts are pinned as well as the total: an assertion added
    anywhere would give a scenario a way to fail that the reference does not
    have.
    """
    per_function = {
        name: sum(isinstance(child, ast.Assert) for child in ast.walk(node))
        for name, node in step_function_nodes().items()
    }
    module_total = sum(
        isinstance(node, ast.Assert) for node in ast.walk(module_tree())
    )

    assert per_function == ASSERT_COUNT_BY_FUNCTION
    assert sum(per_function.values()) == 4
    assert module_total == 4, (
        "the four asserts of EmployeeStage.java:32, :52, :78 and :88 are the "
        "whole of the module's checking, helpers included"
    )


def test_every_step_runs_without_sleeping_waiting_or_reaching_the_network(
    resolve_step: Any,
    monkeypatch: pytest.MonkeyPatch,
    fake_context: Any,
) -> None:
    """Pins the seam itself: all twelve bodies are reachable through recorders.

    Not a parity fact about the Java but the precondition for every parity fact
    above: every fixed delay and every wait in the module is bound as a module
    global, so :class:`StepProbe` intercepts all of them and no test in this
    file sleeps, polls or touches a browser.  A helper reached some other way -
    through a module object, say - would run for real against the stub, and
    this is what would notice.
    """
    walked = walk_every_step(resolve_step, monkeypatch, fake_context)

    assert set(walked) == set(ALL_PATTERNS)

    for pattern, (_, probe, recorded) in walked.items():
        assert probe.wait_helper_names, f"{pattern!r} ran with no wait helper installed"
        assert all(name.startswith("wait_") for name in probe.wait_helper_names)

        delays = [entry[1] for entry in recorded if entry[0] == "sleep"]

        assert delays == list(SLEEPS_BY_PATTERN[pattern])
        assert all(isinstance(seconds, (int, float)) for seconds in delays)


# =========================================================================== #
# The coverage gate
# =========================================================================== #

#: Every registered pattern mapped to the tests in this module that pin its
#: behaviour.  The static half of the parity obligation: a definition with no
#: test named here fails the gate below, as does a test named here that has
#: been renamed or removed.
PARITY_TESTS: Final[dict[str, tuple[str, ...]]] = {
    PHRASE_ORPHAN_LOGIN_PAGE: (
        "test_orphan_step_navigates_to_the_web_table_url",
        "test_orphan_step_passes_an_unset_url_through_unguarded",
        "test_configuration_keys_resolve_through_app_config_end_to_end",
    ),
    PHRASE_DASHBOARD: (
        "test_dashboard_step_navigates_to_url_then_signs_in",
        "test_the_three_login_call_sites_each_perform_the_same_three_operations",
        "test_configuration_keys_resolve_through_app_config_end_to_end",
    ),
    PHRASE_EMPLOYEES_STAGE: (
        "test_employees_stage_waits_on_the_configured_title_and_asserts_the_literal",
        "test_employees_stage_assertion_rejects_any_other_title",
        "test_configuration_keys_resolve_through_app_config_end_to_end",
    ),
    PHRASE_CHALLENGES_STAGE: ("test_challenges_stage_walks_three_click_then_wait_pairs",),
    PHRASE_DEPARTMENTS_STAGE: (
        "test_departments_stage_clicks_then_waits_out_seven_fixed_seconds",
    ),
    PHRASE_LAST_STAGE_TITLE: (
        "test_last_stage_title_asserts_the_departments_title",
        "test_last_stage_title_assertion_rejects_any_other_title",
        "test_last_stage_title_keeps_the_commented_assert_equals_absent",
    ),
    PHRASE_EMPLOYEES_DASHBOARD: (
        "test_employees_dashboard_navigates_signs_in_delays_then_opens_the_stage",
        "test_the_three_login_call_sites_each_perform_the_same_three_operations",
    ),
    PATTERN_CREATE_EMPLOYEE: (
        "test_create_employee_delays_clicks_delays_waits_then_types_the_name",
        "test_create_employee_pattern_takes_the_quoted_examples_values",
    ),
    PHRASE_CREATED_MESSAGE: (
        "test_created_message_asserts_the_confirmation_is_displayed",
        "test_created_message_assertion_fails_when_the_confirmation_is_hidden",
        "test_created_message_keeps_the_commented_text_comparison_absent",
    ),
    PHRASE_LISTED_EMPLOYEES: (
        "test_listed_employees_waits_on_and_asserts_the_same_literal_title",
        "test_listed_employees_assertion_rejects_any_other_title",
    ),
    PHRASE_EDIT_EMPLOYEE: (
        "test_edit_employee_walks_the_whole_twelve_statement_edit_flow",
        "test_the_three_login_call_sites_each_perform_the_same_three_operations",
    ),
    PHRASE_EDITED_NAME: ("test_edited_name_step_clicks_and_checks_nothing",),
}


def test_every_registered_step_definition_has_a_parity_test(step_registry: Any) -> None:
    """The gate AAP 0.4.1 asks for: no step method may go untested.

    *"A step method with no corresponding assertion in its module's test is a
    gap, and the module test enumerates the Java class's methods so an omission
    fails rather than passes silently."*  Three things are reconciled, and the
    last is what makes the enumeration real rather than declarative:

    * every pattern the registry holds for this module has declared tests, and
      every declared test exists in this module under that name;
    * no pattern is declared that the registry does not hold, so a deleted or
      re-worded definition fails here;
    * every pattern whose declared tests actually ran was recorded as covered
      at run time.  Under a whole-module run that is all twelve; under a ``-k``
      selection it is whatever ran, so a partial selection reports a gap only
      where one genuinely exists.
    """
    registered = tuple(
        matcher.pattern for matcher in module_matchers(step_registry)[STEP_BUCKET]
    )

    assert set(PARITY_TESTS) == set(registered), (
        f"declared {sorted(set(PARITY_TESTS) - set(registered))} and missing "
        f"{sorted(set(registered) - set(PARITY_TESTS))}"
    )

    for pattern, test_names in PARITY_TESTS.items():
        assert test_names, f"{pattern!r} declares no parity test"

        for name in test_names:
            candidate = globals().get(name)

            assert callable(candidate), f"{pattern!r} names a missing test {name!r}"
            assert name.startswith("test_")

    assert COVERED_PATTERNS <= set(registered), (
        f"coverage was recorded for patterns this module does not own: "
        f"{sorted(COVERED_PATTERNS - set(registered))}"
    )

    for pattern in sorted(set(registered) - COVERED_PATTERNS):
        executed = [name for name in PARITY_TESTS[pattern] if name in EXECUTED_TESTS]

        assert not executed, (
            f"{pattern!r} is untested: {executed} ran without ever resolving "
            f"it, so the step body it names is unasserted"
        )
