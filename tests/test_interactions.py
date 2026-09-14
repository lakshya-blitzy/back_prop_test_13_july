r"""Tests for ``app/automation/interactions.py`` - the port's only home for keys and actions.

The evidence module for the two helpers AAP 0.4.1 assigns here: *"Keyboard-key
helpers for the three classes that use ``Keys`` - Crm, Notes and Sales - and
action-chain helpers for the two that use ``Actions`` - Crm and Notes"*, with
``Contacts.java`` using neither.  The anchors are ``Crm.java:9-10`` and
``Notes.java:9-10``, importing both classes, ``Sales.java:9``, importing
``Keys`` alone, and ``Contacts.java:3-9``, whose import block names neither -
pinned revision ``47e9d697e4a9a85da889f94a846fdf47af28a240`` (AAP 0.2.1).

Three contracts are owned here: AAP 0.4.1's closed export surface, keyboard
helpers for the ``Keys`` sites and action-chain helpers for the ``Actions``
sites and nothing else; AAP 0.4.2's import sites, carried in
:data:`PRESS_KEYS_CONSUMERS` and :data:`ACTION_CHAIN_CONSUMERS` and asserted
against the tree by parsing every step module with :mod:`ast`; and AAP 0.5.2's
rule that ``Keys`` and ``Actions`` are *"reached only through
``app/automation/interactions.py``"*, asserted from both sides - the barrel
hands out neither class, and no module under ``features/steps/`` imports
``selenium`` in any form.

What the source requires: the nine ``Keys`` sites, cited line by line in
:data:`JAVA_KEY_CALL_SITES`, take two Java shapes that both reach the browser as
one character stream, so the port makes exactly one keyboard call carrying
``(text, *keys)`` in order, and the two ``Actions`` chains transcribe as a
fluent builder with no ``build()`` step and ``pause`` in seconds.

Nothing starts a browser: the seam is
``app.automation.interactions.get_driver``, which the autouse
:func:`_forbid_unpatched_session` fixture replaces with a failing callable
unless a test installs a stand-in, and the page is ``tests/conftest.py``'s
:class:`~tests.conftest.StubDriver`, whose single ordered log makes "one call,
in this order" assertable.  ``Keys.ENTER`` is the literal ``"\ue007"`` the W3C
WebDriver specification fixes, so the AAP 0.4.2 boundary holds here too and the
comparison is not circular.
"""

from __future__ import annotations

import ast
import inspect
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

import pytest

from app import automation
from app.automation import interactions

# --------------------------------------------------------------------------
# The key characters this module asserts against
#
# Values from the W3C WebDriver specification's private-use-area assignments,
# verified against the pinned selenium 4.48.0 (73 public ``Keys`` members,
# every value a one-character string).  Only the members the port's own call
# sites and these tests name are listed; the module under test derives its full
# name set from the class itself, which is what keeps it from drifting.
# --------------------------------------------------------------------------

#: ``Keys.ENTER`` - the only member the ported suite ever names.
ENTER: str = "\ue007"

#: ``Keys.TAB``, used here only to demonstrate that repeats are preserved.
TAB: str = "\ue004"

#: ``Keys.CONTROL``, the modifier half of the ordering assertion.
CONTROL: str = "\ue009"

#: ``Keys.ARROW_DOWN``, the modified half of the ordering assertion.
ARROW_DOWN: str = "\ue015"

#: Member name to character, for the case-insensitivity and ordering tests.
KEY_CHARACTERS: dict[str, str] = {
    "ENTER": ENTER,
    "TAB": TAB,
    "CONTROL": CONTROL,
    "ARROW_DOWN": ARROW_DOWN,
}

# --------------------------------------------------------------------------
# The recorder's vocabulary
#
# ``tests/conftest.py`` tags an operation performed on an element with
# ``ELEMENT_PREFIX`` ("element.") and carries the originating locator as the
# entry's first argument, so ``("element.send_keys", (locator, "New Tag",
# ENTER))`` reads as "typed that sequence into the element found by that
# locator".  Named here rather than imported, so a reader of this file can see
# what the assertions below are matching on.
# --------------------------------------------------------------------------

#: The one operation this module's subject performs on the page.
SEND_KEYS: str = "element.send_keys"

#: The driver operation a locator target - and only a locator target - causes.
FIND_ELEMENT: str = "find_element"

#: The driver operation ``ActionChains.perform()`` dispatches through.  Asserted
#: absent: ``action_chain`` builds and returns, it never performs.
EXECUTE: str = "execute"

# --------------------------------------------------------------------------
# Fixed locators and elements
# --------------------------------------------------------------------------

#: A ``(strategy, value)`` pair in the shape ``app/pages/base_page.py`` builds
#: from its ``By`` constants and unpacks straight into ``find_element(*locator)``.
#: The strategy is a plain string because every ``By`` member is one.
TAGS_LOCATOR: tuple[str, str] = ("css selector", "input.o_field_many2manytags")

#: A second locator, so that a test needing two distinct elements does not have
#: to reuse one and lose the distinction.
SEARCH_LOCATOR: tuple[str, str] = ("xpath", "//input[@id='search']")

# --------------------------------------------------------------------------
# The nine Java ``Keys`` call sites, each with the literal it carries
#
# The three outline-driven Crm sites take their value from the scenario
# outline's Examples table rather than from a Java literal, so a representative
# value stands in: what each case asserts is the call granularity and the
# argument order, which are identical whatever the value is.
# --------------------------------------------------------------------------


class KeyCallSite(NamedTuple):
    """One Java ``sendKeys`` site and the text its Python port types."""

    #: ``File.java:line`` in the pinned reference, for failure messages.
    anchor: str

    #: The literal the site types before ``Keys.ENTER``.
    text: str


#: All nine sites, in the order the module docstring of the subject lists them.
JAVA_KEY_CALL_SITES: tuple[KeyCallSite, ...] = (
    KeyCallSite("Notes.java:35", "New Tag"),
    KeyCallSite("Sales.java:68", "Odoo Sales Order"),
    KeyCallSite("Crm.java:36", "test"),
    KeyCallSite("Crm.java:40", "8"),
    KeyCallSite("Crm.java:76", "Widget Opportunity"),
    KeyCallSite("Crm.java:78", "4200"),
    KeyCallSite("Crm.java:80", "60"),
    KeyCallSite("Crm.java:138", "Test"),
    KeyCallSite("Crm.java:141", "aa"),
)

# --------------------------------------------------------------------------
# The closed surface
# --------------------------------------------------------------------------

#: The whole export surface of the subject, per AAP 0.4.1's barrel row.
EXPECTED_EXPORTS: list[str] = ["press_keys", "action_chain"]

#: The helpers the subject's docstring forbids by name - *"There is no
#: ``scroll_to``, ``drag_and_drop``, ``hover``, ``double_click``,
#: ``right_click``, ``select_all`` or ``clear_and_type``"*.  Each would be dead
#: code that no call site invokes and that also drags down the 80 % coverage
#: gate AAP 0.5.1 sets for this package.
FORBIDDEN_HELPERS: tuple[str, ...] = (
    "scroll_to",
    "drag_and_drop",
    "hover",
    "double_click",
    "right_click",
    "select_all",
    "clear_and_type",
)

#: Selenium names the barrel must never re-export.  ``Keys`` and
#: ``ActionChains`` are the two this module owns; either one handed out would
#: hollow out the AAP 0.4.2 boundary while still passing a naive "does this file
#: import selenium" check.
FORBIDDEN_REEXPORTS: tuple[str, ...] = ("Keys", "ActionChains")

# --------------------------------------------------------------------------
# The step-module census
# --------------------------------------------------------------------------

#: Repository root, from this file's own position: tests/ -> repository root.
REPO_ROOT: Path = Path(__file__).resolve().parents[1]

#: Where the behave step definitions live.
STEP_MODULES_DIR: Path = REPO_ROOT / "features" / "steps"

#: The package a step module imports the two helpers from.  Always the barrel,
#: never the defining module: AAP 0.4.2 gives ``app.automation`` as the surface.
AUTOMATION_PACKAGE: str = "app.automation"

#: The ten step modules AAP 0.3.1 names, so a census cannot pass vacuously on a
#: glob that matched nothing or on a module that silently disappeared.
EXPECTED_STEP_MODULES: frozenset[str] = frozenset(
    {
        "calendar_steps",
        "contacts_steps",
        "crm_steps",
        "employee_steps",
        "inventory_steps",
        "login_steps",
        "logout_steps",
        "notes_steps",
        "sales_steps",
        "session_steps",
    }
)

#: AAP 0.4.2's ``press_keys`` import sites, exactly.
PRESS_KEYS_CONSUMERS: frozenset[str] = frozenset({"crm_steps", "notes_steps", "sales_steps"})

#: AAP 0.4.2's ``action_chain`` import sites, exactly.
ACTION_CHAIN_CONSUMERS: frozenset[str] = frozenset({"crm_steps", "notes_steps"})

#: The distribution the ``Keys`` and ``Actions`` types come from.  Matched as a
#: name and as a dotted prefix, so ``import selenium``,
#: ``import selenium.webdriver``, ``from selenium import webdriver`` and
#: ``from selenium.webdriver.common.keys import Keys`` are all caught.
SELENIUM_ROOT: str = "selenium"

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


class RecordedGetDriver:
    """A counting stand-in for ``app.automation.driver.get_driver``.

    The seam both helpers read: they call ``get_driver()`` only when they
    actually need this worker's session, and *how many times* is as much a part
    of the contract as *what it returns* - a locator target must consult it
    once, an element target not at all.  Counting is therefore the assertion,
    which a plain lambda could not support.

    :param session: The object to hand back, which may legitimately be ``None``
        - ``app/automation/driver.py`` returns ``None`` when the configured
        browser name matches neither branch it constructs, and AAP 0.4.1 rules
        that such a run fails at first driver use.
    """

    __slots__ = ("call_count", "session")

    def __init__(self, session: Any) -> None:
        """Bind the session this stand-in serves, with the counter at zero.

        :param session: The object every call hands back.
        """
        #: The session handed back on every call.
        self.session = session

        #: How many times the subject asked for a session.
        self.call_count = 0

    def __call__(self) -> Any:
        """Record one request for this worker's session.

        :returns: :attr:`session`, unchanged and un-copied.
        """
        self.call_count += 1
        return self.session


def queued_action_counts(chain: Any) -> tuple[int, ...]:
    """How many actions are queued on each of a chain's input devices.

    An ``ActionChains`` accumulates queued actions on three devices - pointer,
    key and wheel - until ``perform()`` flushes them, so this is the direct
    measurement of "nothing queued yet" and of "the caller's ``pause`` landed".

    :param chain: The chain returned by
        :func:`~app.automation.interactions.action_chain`.
    :returns: One count per device, in the chain's own device order.
    """
    return tuple(len(device.actions) for device in chain.w3c_actions.devices)


def wire_pause_durations(chain: Any) -> tuple[int, ...]:
    """Every queued pause duration as it would go on the wire, in milliseconds.

    The measurement behind the second parity trap.  Selenium accepts
    ``pause(seconds)`` and encodes the action in milliseconds, so a correctly
    ported ``pause(2)`` appears here as ``2000`` - numerically identical to the
    Java literal - and a literally transcribed ``pause(2000)`` appears as
    ``2000000``, which is 2000 seconds of stalled scenario.

    Both action representations selenium uses are handled: the pointer device
    queues a plain mapping, the key device queues an object that encodes to one.

    :param chain: The chain to inspect.
    :returns: The ``duration`` of every queued pause, in device order.
    """
    durations: list[int] = []

    for device in chain.w3c_actions.devices:
        for action in device.actions:
            encoded = action if isinstance(action, dict) else action.encode()
            if encoded.get("type") == "pause":
                durations.append(encoded["duration"])

    return tuple(durations)


class StepModuleImports(NamedTuple):
    """Every import one step module declares, as :mod:`ast` read them."""

    #: The module's name without its ``.py`` suffix, such as ``crm_steps``.
    name: str

    #: ``from X import a, b`` as ``{"X": frozenset({"a", "b"})}``.  A relative
    #: import - of which the step modules have none - is keyed by the dots that
    #: introduce it, so it can never be mistaken for an absolute module.
    from_imports: dict[str, frozenset[str]]

    #: ``import X`` and ``import X as y`` as ``{"X"}``, the dotted name intact.
    plain_imports: frozenset[str]

    def names_from(self, module: str) -> frozenset[str]:
        """The names this module imports from one package.

        :param module: The dotted module name to look up.
        :returns: The imported names, or an empty set when this module imports
            nothing from there.
        """
        return self.from_imports.get(module, frozenset())

    def imports_distribution(self, root: str) -> bool:
        """Whether this module imports a package in any form.

        :param root: The distribution's top-level package name.
        :returns: ``True`` when the name is imported directly, imported as a
            submodule, or named as the source of a ``from`` import.
        """
        prefix = f"{root}."
        candidates = (*self.plain_imports, *self.from_imports)
        return any(name == root or name.startswith(prefix) for name in candidates)


def parse_step_module(path: Path) -> StepModuleImports:
    """Read one step module's import statements without importing it.

    Parsed with :mod:`ast` rather than matched with a regular expression: the
    grammar is what the statement means, so an aliased import, a
    multi-name import and a name that merely appears inside a docstring are all
    classified correctly, and no pattern has to be maintained against them.
    Parsing also means the census runs without importing behave, without
    registering a single step and without any import side effect at all.

    :param path: The ``.py`` file to read.
    :returns: Its declared imports.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    from_imports: dict[str, frozenset[str]] = {}
    plain_imports: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            # ``node.module`` is None for ``from . import x``; the leading dots
            # are kept so a relative import stays distinguishable.
            key = "." * node.level + (node.module or "")
            names = frozenset(alias.name for alias in node.names)
            from_imports[key] = from_imports.get(key, frozenset()) | names
        elif isinstance(node, ast.Import):
            plain_imports.update(alias.name for alias in node.names)

    return StepModuleImports(
        name=path.stem,
        from_imports=from_imports,
        plain_imports=frozenset(plain_imports),
    )


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _forbid_unpatched_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the real session lookup unreachable for every test in this module.

    ``app/automation/driver.py``'s ``get_driver`` provisions a driver binary and
    launches a browser on first use.  Nothing in a unit suite may do either, and
    "no test happens to hit it" is a weaker guarantee than "no test can".  This
    replaces the name the subject reads with a callable that fails the test, so
    an unpatched session lookup is reported as the mistake it is rather than as
    a hung run.  A test that needs a session installs its own stand-in over this
    one through :func:`get_driver_source`.

    :param monkeypatch: pytest's patcher, used for its guaranteed teardown.
    :returns: ``None`` - the fixture is entirely about the state around the test.
    """

    def _unexpected_session_lookup() -> Any:
        """Fail the test rather than provision a driver.

        :raises AssertionError: Always - reaching this call is the defect.
        """
        raise AssertionError(
            "app.automation.interactions.get_driver() was called by a test that "
            "installed no session stand-in; no unit test may provision a driver."
        )

    monkeypatch.setattr(interactions, "get_driver", _unexpected_session_lookup)


@pytest.fixture
def get_driver_source(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[Any], RecordedGetDriver]:
    """Install a counting session stand-in, and hand back the recorder.

    Patches ``app.automation.interactions.get_driver`` - the name the subject
    binds at import time, which is the only seam that intercepts its lookup.
    Returning the installer rather than a ready-made recorder lets a test choose
    what the session is: the driver it also asserts against, a decoy it expects
    to stay untouched, or ``None`` for the unmatched-browser path.

    No teardown of its own: both fixtures patch through the same function-scoped
    *monkeypatch*, whose finalizer undoes them last-in-first-out - this
    recorder first, then the autouse guard - so the subject's own
    ``get_driver`` is back in place after every test however the test ended.

    :param monkeypatch: pytest's patcher, used for its guaranteed teardown.
    :returns: A callable taking the session to serve and returning the
        :class:`RecordedGetDriver` that serves it.
    """

    def install(session: Any) -> RecordedGetDriver:
        """Serve *session* from the subject's ``get_driver`` for this test.

        :param session: The object the subject's session lookup returns.
        :returns: The recorder now installed, for its call count.
        """
        source = RecordedGetDriver(session)
        monkeypatch.setattr(interactions, "get_driver", source)
        return source

    return install


# --------------------------------------------------------------------------
# The export surface, and its closure - AAP 0.4.1
# --------------------------------------------------------------------------


def test_module_exports_exactly_the_two_documented_helpers() -> None:
    """``__all__`` is the two helpers, in the documented order, both callable."""
    assert interactions.__all__ == EXPECTED_EXPORTS
    assert callable(interactions.press_keys)
    assert callable(interactions.action_chain)


def test_barrel_re_exports_the_same_two_objects() -> None:
    """The barrel publishes both names, bound to the very same objects.

    Step modules import from ``app.automation``, never from the defining
    module, so an assertion about the subject's ``__all__`` alone would not
    establish that the name a consumer imports is the function tested here.
    Identity is what closes that gap; equality would not, since a wrapper would
    satisfy it.
    """
    for name in EXPECTED_EXPORTS:
        assert name in automation.__all__, f"{name} is missing from the barrel"

    assert automation.press_keys is interactions.press_keys
    assert automation.action_chain is interactions.action_chain


@pytest.mark.parametrize("name", FORBIDDEN_HELPERS)
def test_interaction_surface_is_closed(name: str) -> None:
    """No speculative helper exists, on the module or on the barrel.

    The subject's docstring forbids exactly these seven by name.  Asserting
    their absence is what makes the surface *closed* rather than merely correct:
    a later "while I am here" addition fails this test instead of arriving as
    dead code that no call site invokes.
    """
    assert not hasattr(interactions, name), f"{name} was added to the subject"
    assert not hasattr(automation, name), f"{name} was added to the barrel"
    assert name not in interactions.__all__
    assert name not in automation.__all__


@pytest.mark.parametrize("name", FORBIDDEN_REEXPORTS)
def test_keys_is_not_exported_by_the_barrel(name: str) -> None:
    """``Keys`` and ``ActionChains`` are reachable through neither barrel path.

    The reason this module exists, per AAP 0.5.2: the two Selenium types are
    *"reached only through ``app/automation/interactions.py``"*.  A re-export
    would let a step module obtain the class **through** ``app.automation``,
    hollowing out the AAP 0.4.2 boundary while still passing a naive check for
    a literal ``import selenium`` in that file.  Both halves are asserted -
    absent from ``__all__``, and not reachable as an attribute either, since
    ``from app.automation import Keys`` consults the attribute and not the list.
    """
    assert name not in automation.__all__
    assert not hasattr(automation, name)


def test_by_is_the_only_authorized_selenium_re_export() -> None:
    """``By`` is exported by name, and it is the only Selenium name that is.

    AAP 0.4.2 authorizes it explicitly - *"``By``, re-exported so locator
    construction needs no Selenium import elsewhere"* - with its import sites
    given as ``app/pages/*`` and ``login_steps``.  Stated here as the positive
    counterpart to the prohibition above, so the prohibition cannot be read as
    "no Selenium name may be exported".
    """
    assert "By" in automation.__all__
    assert hasattr(automation, "By")

    selenium_names = {"By", *FORBIDDEN_REEXPORTS}
    exported_selenium_names = selenium_names.intersection(automation.__all__)
    assert exported_selenium_names == {"By"}


# --------------------------------------------------------------------------
# press_keys: one keyboard call, in the order it was given
# --------------------------------------------------------------------------


def test_text_then_key_reaches_the_page_as_one_ordered_call(stub_driver: Any) -> None:
    """The port of ``Notes.java:35``: one call carrying ``("New Tag", ENTER)``.

    The single most important assertion about this helper.  Both Java shapes -
    ``sendKeys("New Tag", Keys.ENTER)`` at ``Notes.java:35`` and
    ``sendKeys(name + Keys.ENTER)`` at ``Sales.java:68`` -
    reach the browser as one character stream, because selenium's
    ``keys_to_typing`` flattens every argument into one character list.  So the
    port makes exactly one keyboard call, and this test pins all three ways that
    could go wrong: the *count* (never two calls), the *shape* (never a joined
    string) and the *order* (text first, key after).
    """
    interactions.press_keys(TAGS_LOCATOR, "ENTER", text="New Tag", driver=stub_driver)

    assert stub_driver.count_of(SEND_KEYS) == 1
    assert stub_driver.calls_of(SEND_KEYS) == ((TAGS_LOCATOR, "New Tag", ENTER),)

    # Explicitly not the concatenated form: two arguments, not one joined one.
    typed = stub_driver.calls_of(SEND_KEYS)[0][1:]
    assert typed == ("New Tag", ENTER)
    assert "New Tag" + ENTER not in typed


@pytest.mark.parametrize("site", JAVA_KEY_CALL_SITES, ids=lambda site: site.anchor)
def test_the_nine_java_call_sites_each_port_to_one_ordered_call(
    site: KeyCallSite,
    stub_driver: Any,
) -> None:
    """Every one of the nine ``Keys`` sites in the source, driven individually.

    The three outline-driven Crm sites take their literal from the Examples
    table rather than from the Java source, so a representative value stands in;
    what each case asserts is the call granularity and the argument order, which
    do not depend on the value.  Driving all nine rather than one is what makes
    this module evidence for the whole census in AAP 0.4.1's ``interactions``
    row rather than for a single example of it.
    """
    interactions.press_keys(TAGS_LOCATOR, "ENTER", text=site.text, driver=stub_driver)

    assert stub_driver.count_of(SEND_KEYS) == 1, f"{site.anchor} did not make one call"
    assert stub_driver.calls_of(SEND_KEYS) == ((TAGS_LOCATOR, site.text, ENTER),)


def test_keys_alone_are_sent_when_no_text_is_supplied(stub_driver: Any) -> None:
    """Omitting ``text`` sends the resolved keys and nothing else.

    No empty string is inserted in front of them: the sequence is the keys
    alone, which is what ``text=None`` means.
    """
    interactions.press_keys(TAGS_LOCATOR, "ENTER", driver=stub_driver)

    assert stub_driver.calls_of(SEND_KEYS) == ((TAGS_LOCATOR, ENTER),)


def test_several_keys_keep_the_order_they_were_given(stub_driver: Any) -> None:
    """``"CONTROL", "ARROW_DOWN"`` stays in that order - never sorted.

    ``Keys.CONTROL`` before a key is not the same input as the reverse, so a
    helper that sorted or normalised its arguments would change what the browser
    receives.  The pair is chosen so the mistake would be visible: sorting these
    two names alphabetically inverts them, which the premise guard below states
    before the sequence itself is asserted in the order it was given.
    """
    given = ("CONTROL", "ARROW_DOWN")
    assert given != tuple(sorted(given)), "the case must be order-sensitive to mean anything"

    interactions.press_keys(TAGS_LOCATOR, *given, driver=stub_driver)

    expected = tuple(KEY_CHARACTERS[name] for name in given)
    assert stub_driver.calls_of(SEND_KEYS) == ((TAGS_LOCATOR, *expected),)
    assert expected == (CONTROL, ARROW_DOWN)


def test_repeated_keys_are_not_deduplicated(stub_driver: Any) -> None:
    """The same key named twice is typed twice, in one call.

    Two tabs move two fields; collapsing them to one - which any set-based
    implementation would do silently - would move one.
    """
    interactions.press_keys(TAGS_LOCATOR, "TAB", "TAB", driver=stub_driver)

    assert stub_driver.calls_of(SEND_KEYS) == ((TAGS_LOCATOR, TAB, TAB),)


def test_empty_text_is_a_supplied_literal_and_appears_in_the_sequence(
    stub_driver: Any,
) -> None:
    """``text=""`` is a value, not an omission, and it keeps its position.

    The subject checks ``text is None`` rather than falsiness precisely so this
    holds: a caller that computed an empty literal - a cleared search term, an
    outline column that is blank - gets it typed where it asked for it, and the
    call still carries two arguments rather than one.
    """
    interactions.press_keys(TAGS_LOCATOR, "ENTER", text="", driver=stub_driver)

    assert stub_driver.calls_of(SEND_KEYS) == ((TAGS_LOCATOR, "", ENTER),)


@pytest.mark.parametrize(
    ("given", "expected_name"),
    [
        ("ENTER", "ENTER"),
        ("enter", "ENTER"),
        ("Enter", "ENTER"),
        ("arrow_down", "ARROW_DOWN"),
        ("ArRoW_DoWn", "ARROW_DOWN"),
    ],
)
def test_key_names_resolve_case_insensitively(
    given: str,
    expected_name: str,
    stub_driver: Any,
) -> None:
    """``"enter"`` and ``"ENTER"`` both resolve to the same character.

    A **helper-API convenience**, and deliberately not the same thing as the
    browser-name matching in ``app/automation/driver.py``, which compares
    exactly on purpose because the Java ``switch`` over a ``String`` does
    (``Driver.java:29-42``, and AAP 0.4.1's "no default branch").  This
    leniency must not migrate there: a mistyped browser value has to fall
    through to the no-session path the source produces, while a mistyped key
    name is internal-API misuse that this helper is free to accept or reject on
    its own terms.
    """
    interactions.press_keys(TAGS_LOCATOR, given, driver=stub_driver)

    assert stub_driver.calls_of(SEND_KEYS) == ((TAGS_LOCATOR, KEY_CHARACTERS[expected_name]),)


# --------------------------------------------------------------------------
# press_keys: locator versus element, and the session seam
# --------------------------------------------------------------------------


def test_locator_tuple_is_resolved_with_find_element(stub_driver: Any) -> None:
    """A 2-tuple is a locator: looked up once, then typed into.

    The order matters as much as the fact - the lookup precedes the keyboard
    call - and the locator is unpacked into ``find_element(by, value)`` rather
    than passed whole.
    """
    interactions.press_keys(TAGS_LOCATOR, "ENTER", text="New Tag", driver=stub_driver)

    assert stub_driver.operations() == (FIND_ELEMENT, SEND_KEYS)
    assert stub_driver.calls_of(FIND_ELEMENT) == (TAGS_LOCATOR,)


def test_locator_list_is_resolved_with_find_element(stub_driver: Any) -> None:
    """A 2-element list is a locator too - the check is structural.

    The subject detects a locator as *"a ``tuple`` or ``list`` of length two"*,
    deliberately rather than by importing ``WebElement`` for an ``isinstance``
    check, which keeps its import list at the three entries AAP 0.4.2 allows.
    A list therefore has to work, and this is the case that proves the check is
    the documented structural one and not a ``tuple``-only accident.
    """
    interactions.press_keys(list(SEARCH_LOCATOR), "ENTER", text="chair", driver=stub_driver)

    assert stub_driver.operations() == (FIND_ELEMENT, SEND_KEYS)
    assert stub_driver.calls_of(FIND_ELEMENT) == (SEARCH_LOCATOR,)
    assert stub_driver.calls_of(SEND_KEYS) == ((SEARCH_LOCATOR, "chair", ENTER),)


def test_resolved_element_is_typed_into_without_any_lookup(stub_driver: Any) -> None:
    """An element target is used untouched: no ``find_element`` at all.

    The common path.  Every one of the nine ported call sites passes a page
    object's accessor result - already a ``WebElement`` - so this is the shape
    the suite actually runs, and it must reach the page in exactly one
    operation.
    """
    element = stub_driver.find_element(*TAGS_LOCATOR)
    stub_driver.clear_calls()

    interactions.press_keys(element, "ENTER", text="New Tag")

    assert stub_driver.operations() == (SEND_KEYS,)
    assert stub_driver.count_of(FIND_ELEMENT) == 0
    assert stub_driver.calls_of(SEND_KEYS) == ((TAGS_LOCATOR, "New Tag", ENTER),)


@pytest.mark.parametrize(
    "target",
    [
        ("css selector",),
        ("css selector", "input", "extra"),
        (),
        ["xpath", "//input", "//div"],
    ],
    ids=["one-element-tuple", "three-element-tuple", "empty-tuple", "three-element-list"],
)
def test_a_sequence_that_is_not_two_long_is_not_a_locator(
    target: Any,
    stub_driver: Any,
) -> None:
    """Only length two means locator; anything else is taken as the element.

    The structural check is ``len(target) == 2`` and nothing else is inspected,
    so a malformed pair is *not* quietly repaired into a lookup: it is treated
    as the element the caller said it was, and the failure surfaces on the
    keyboard call as a plain ``AttributeError`` naming the type that could not
    take it.  Nothing reaches the page first - the recorder's log is empty -
    and no session is consulted, which the autouse fixture would otherwise fail.
    """
    with pytest.raises(AttributeError, match="send_keys"):
        interactions.press_keys(target, "ENTER", text="New Tag", driver=stub_driver)

    assert stub_driver.calls == []


def test_a_locator_consults_the_worker_session_exactly_once(
    stub_driver: Any,
    get_driver_source: Callable[[Any], RecordedGetDriver],
) -> None:
    """With a locator and no ``driver=``, the session is fetched once.

    Once, not per argument and not per call to the lookup helper: the session is
    a process-global the driver module owns, and a helper that re-fetched it
    would multiply that cost across the nine call sites for nothing.
    """
    source = get_driver_source(stub_driver)

    interactions.press_keys(TAGS_LOCATOR, "ENTER", text="New Tag")

    assert source.call_count == 1
    assert stub_driver.calls_of(SEND_KEYS) == ((TAGS_LOCATOR, "New Tag", ENTER),)


def test_a_supplied_driver_bypasses_the_session_lookup(
    stub_driver: Any,
    get_driver_source: Callable[[Any], RecordedGetDriver],
) -> None:
    """``driver=`` is used as given, and ``get_driver`` is never called.

    The seam every test in this module leans on, so it is asserted directly
    rather than assumed.  The stand-in serves a *decoy* recorder: were the
    supplied driver ignored, the interaction would land on the decoy and both
    assertions below would fail rather than one of them passing by coincidence.
    """
    decoy = type(stub_driver)()
    source = get_driver_source(decoy)

    interactions.press_keys(TAGS_LOCATOR, "ENTER", text="New Tag", driver=stub_driver)

    assert source.call_count == 0
    assert decoy.calls == []
    assert stub_driver.calls_of(SEND_KEYS) == ((TAGS_LOCATOR, "New Tag", ENTER),)


def test_an_element_target_consults_no_session_even_without_a_driver(
    stub_driver: Any,
    get_driver_source: Callable[[Any], RecordedGetDriver],
) -> None:
    """An element target needs no session, so none is fetched.

    *"A locator is the only reason this module needs a session at all"* - which
    is what keeps ``get_driver`` out of the common path, and therefore what lets
    a page object's element be typed into inside a worker whose session slot the
    caller already holds.
    """
    element = stub_driver.find_element(*SEARCH_LOCATOR)
    stub_driver.clear_calls()
    source = get_driver_source(stub_driver)

    interactions.press_keys(element, "ENTER", text="chair")

    assert source.call_count == 0
    assert stub_driver.operations() == (SEND_KEYS,)


# --------------------------------------------------------------------------
# press_keys: misuse is rejected before the page is touched
# --------------------------------------------------------------------------


def test_unknown_key_name_is_rejected_before_the_page_is_touched(stub_driver: Any) -> None:
    """A misspelled member name raises ``ValueError`` and types nothing.

    The only way to reach this is a mistake in a step module, and the fix is a
    spelling correction, so the message quotes the offending value and lists the
    valid names.  What the empty log adds is the part that matters at runtime:
    the rejection happens *before* the element is resolved, so a broken step
    leaves no partial input in a form that a later step would then act on.
    """
    with pytest.raises(ValueError, match=r"Unknown Keys member name 'ENTRE'"):
        interactions.press_keys(TAGS_LOCATOR, "ENTRE", text="New Tag", driver=stub_driver)

    assert stub_driver.calls == []


def test_a_literal_character_is_not_a_key_name(stub_driver: Any) -> None:
    """``"a"`` names no member and is rejected rather than typed.

    The distinction the two parameters draw: named keys go in the positional
    arguments, literal characters go in ``text=``.  A helper that fell back to
    typing an unrecognised name verbatim would erase it, and a misspelled
    ``"ENTRE"`` would then be typed into the page as five characters.  The raw
    ``ENTER`` character is the same mistake from the other direction - it *is* a
    string, so it takes the name-lookup path and is rejected there - and both
    messages point the caller at ``text=``.
    """
    for literal in ("a", ENTER):
        with pytest.raises(ValueError, match="Unknown Keys member name") as excinfo:
            interactions.press_keys(TAGS_LOCATOR, literal, driver=stub_driver)

        assert "text=" in str(excinfo.value)

    assert stub_driver.calls == []


@pytest.mark.parametrize(
    "key_name",
    ["mro", "__class__", "__doc__", "__init__"],
    ids=["type-method", "dunder-class", "dunder-doc", "dunder-init"],
)
def test_a_class_or_inherited_attribute_name_is_not_a_key(
    key_name: str,
    stub_driver: Any,
) -> None:
    """Membership is checked against the derived name set, never by ``getattr``.

    ``getattr(Keys, "mro")`` succeeds - it is ``type``'s own method - and every
    dunder resolves through ``object``, so a helper that reached straight for
    the attribute would hand a bound method or a docstring to ``send_keys``.
    The subject tests membership of a name set built once from the class's
    public members instead, which rejects all of these.
    """
    with pytest.raises(ValueError, match="Unknown Keys member name"):
        interactions.press_keys(TAGS_LOCATOR, key_name, driver=stub_driver)

    assert stub_driver.calls == []


@pytest.mark.parametrize(
    ("key_name", "type_name"),
    [
        (5, "int"),
        (None, "NoneType"),
        (("ENTER",), "tuple"),
        (["ENTER"], "list"),
        (b"ENTER", "bytes"),
    ],
    ids=["int", "none", "tuple", "list", "bytes"],
)
def test_a_non_string_key_is_rejected_and_points_at_the_text_parameter(
    key_name: Any,
    type_name: str,
    stub_driver: Any,
) -> None:
    """A non-string key raises ``ValueError``, naming the value, its type and ``text=``.

    The failure mode this guards is a caller passing the *value* where the
    *name* belongs, so the message says where literal characters go - the
    keyword-only ``text=`` - rather than only that the type was wrong.
    ``ValueError`` rather than ``TypeError`` is the subject's deliberate choice:
    every misuse of this helper raises one kind of error, so a step module's
    author sees one message shape whatever the mistake was.
    """
    with pytest.raises(ValueError, match="expects Keys member names as strings") as excinfo:
        interactions.press_keys(TAGS_LOCATOR, key_name, driver=stub_driver)

    message = str(excinfo.value)
    assert f"of type {type_name}" in message
    assert "text=" in message
    assert stub_driver.calls == []


def test_a_call_with_nothing_to_type_is_a_misuse(stub_driver: Any) -> None:
    """Neither ``text`` nor a key name raises rather than typing nothing.

    A no-op keyboard call would let a step that lost its argument pass silently,
    reporting success for input the page never received.  The empty log is the
    other half: the element is not even resolved.
    """
    with pytest.raises(ValueError, match="needs something to type"):
        interactions.press_keys(TAGS_LOCATOR, driver=stub_driver)

    assert stub_driver.calls == []


def test_the_first_invalid_key_stops_the_whole_call(stub_driver: Any) -> None:
    """One bad name among several types none of them.

    Every key is resolved before the element is touched, so a partially valid
    call is rejected whole rather than sending its valid prefix - which would
    leave the page in a state no step described.
    """
    with pytest.raises(ValueError, match=r"Unknown Keys member name 'ARROW_SIDEWAYS'"):
        interactions.press_keys(
            TAGS_LOCATOR,
            "CONTROL",
            "ARROW_SIDEWAYS",
            text="New Tag",
            driver=stub_driver,
        )

    assert stub_driver.calls == []


# --------------------------------------------------------------------------
# action_chain: a fresh builder every call
# --------------------------------------------------------------------------


def test_action_chain_returns_a_fresh_builder_every_call(stub_driver: Any) -> None:
    """Two calls yield two distinct objects, each with nothing queued.

    **The assertion that matters most in this module.**  An ``ActionChains``
    accumulates queued actions until ``perform()`` flushes them, so a shared or
    cached instance would replay an earlier step's click-and-hold inside a later
    step - a failure that presents as a flaky browser rather than as a bug here.
    A fresh builder per call is also exactly what the Java code does:
    ``Crm.java:108`` and ``Notes.java:78`` both evaluate ``new
    Actions(Driver.getDriver())`` inside the step method that uses it.
    """
    first = interactions.action_chain(driver=stub_driver)
    second = interactions.action_chain(driver=stub_driver)

    assert first is not second
    assert first._driver is stub_driver
    assert second._driver is stub_driver
    assert queued_action_counts(first) == (0, 0, 0)
    assert queued_action_counts(second) == (0, 0, 0)


def test_a_chain_is_bound_to_the_supplied_driver_and_performs_nothing(
    stub_driver: Any,
) -> None:
    """The builder holds the given session, and the helper never performs.

    ``perform()`` is the caller's to make: the ported step bodies reproduce
    their Java chain verbatim, in the same order, and that chain ends in the
    step.  Measured rather than assumed - ``perform()`` dispatches through the
    driver's command channel, which the recorder logs, so an empty log is
    positive evidence that nothing was flushed.
    """
    chain = interactions.action_chain(driver=stub_driver)

    assert chain._driver is stub_driver
    assert stub_driver.count_of(EXECUTE) == 0
    assert stub_driver.calls == []


def test_queueing_on_one_chain_leaves_a_later_chain_empty(stub_driver: Any) -> None:
    """The freshness guarantee, demonstrated rather than restated.

    The previous test establishes that two objects differ; this establishes that
    the difference is the one that matters - actions queued on the first chain
    are invisible to the second, which is what stops a later step from replaying
    an earlier one's pause.
    """
    first = interactions.action_chain(driver=stub_driver)
    first.pause(2)

    second = interactions.action_chain(driver=stub_driver)

    assert queued_action_counts(first) != (0, 0, 0)
    assert queued_action_counts(second) == (0, 0, 0)


def test_action_chain_consults_the_worker_session_exactly_once(
    stub_driver: Any,
    get_driver_source: Callable[[Any], RecordedGetDriver],
) -> None:
    """With no ``driver=``, the session is fetched once and bound to the chain.

    The form both ported call sites use: neither ``crm_steps`` nor
    ``notes_steps`` passes a driver, so this is the path the suite runs.
    """
    source = get_driver_source(stub_driver)

    chain = interactions.action_chain()

    assert source.call_count == 1
    assert chain._driver is stub_driver


def test_a_supplied_driver_bypasses_the_chain_session_lookup(
    stub_driver: Any,
    get_driver_source: Callable[[Any], RecordedGetDriver],
) -> None:
    """``driver=`` is bound as given, with no session lookup at all."""
    decoy = type(stub_driver)()
    source = get_driver_source(decoy)

    chain = interactions.action_chain(driver=stub_driver)

    assert source.call_count == 0
    assert chain._driver is stub_driver


def test_a_none_session_is_passed_through_untouched(
    get_driver_source: Callable[[Any], RecordedGetDriver],
) -> None:
    """A ``None`` session builds a chain rather than raising here.

    ``app/automation/driver.py`` returns ``None`` when the configured browser
    name matches neither branch it constructs - there is no default branch, by
    AAP 0.4.1 - and that AAP row rules such a run *"fails at first driver use,
    as today"*.  So this helper validates nothing: the ``None`` is carried into
    the builder and the failure surfaces at the caller's first interaction,
    exactly where the Java code's would.  Validating here would move the failure
    earlier and change the message a failing scenario reports.
    """
    source = get_driver_source(None)

    chain = interactions.action_chain()

    assert source.call_count == 1
    assert chain._driver is None
    assert queued_action_counts(chain) == (0, 0, 0)


# --------------------------------------------------------------------------
# The two parity traps, measured
# --------------------------------------------------------------------------


def test_returned_builder_has_no_build_step(stub_driver: Any) -> None:
    """There is no ``build()``: ``.perform()`` alone ends the chain.

    Java 8 ``Actions`` code conventionally ends ``.build().perform()``, and a
    transcription that kept the ``build()`` would fail with an
    ``AttributeError`` at scenario runtime.  Measured on the object the helper
    actually hands out, not on an imported class, so the statement is about what
    a step body receives: ``perform`` is there, ``build`` is not.
    """
    chain = interactions.action_chain(driver=stub_driver)

    assert not hasattr(chain, "build")
    assert not hasattr(type(chain), "build")
    assert callable(chain.perform)


def test_pause_takes_seconds_where_the_java_literal_was_milliseconds(
    stub_driver: Any,
) -> None:
    """``pause(2)`` is the port of Java's ``pause(2000)``, and the units prove it.

    Both Java chains pause twice with ``pause(2000)`` -
    ``Crm.java:108-115`` and ``Notes.java:78-79`` - in milliseconds.  The Python
    builder's parameter is ``seconds`` and it converts to milliseconds only on
    the wire, so the faithful port ``pause(2)`` encodes to the same 2000 ms the
    Java code asked for, while a literal transcription of ``pause(2000)``
    encodes to 2,000,000 ms - 2000 seconds, over half an hour of stalled
    scenario per pause.  Both are measured below rather than described.
    """
    chain = interactions.action_chain(driver=stub_driver)
    pause_parameters = inspect.signature(type(chain).pause).parameters
    assert "seconds" in pause_parameters

    ported = interactions.action_chain(driver=stub_driver).pause(2)
    transcribed = interactions.action_chain(driver=stub_driver).pause(2000)

    assert set(wire_pause_durations(ported)) == {2000}
    assert set(wire_pause_durations(transcribed)) == {2_000_000}

    # One pause is the whole of what the chain carries: selenium queues it on
    # the pointer and key devices and on neither wheel nor anything else, so a
    # count per device is also evidence that nothing extra was added on the way.
    assert queued_action_counts(ported) == (1, 1, 0)


def test_pause_returns_the_same_chain_so_a_java_chain_transcribes_directly(
    stub_driver: Any,
) -> None:
    """The builder is fluent, which is what lets a step reproduce its Java chain.

    ``clickAndHold(...).pause(2000).moveToElement(...).pause(2000).release()``
    is one expression in Java (``Crm.java:110-115`` over six lines,
    ``Notes.java:79`` on one); the port is one expression in Python only if each
    step returns the builder.  Asserted on ``pause`` because it is the operation
    the two chains use that carries no element, and an element-targeted action
    cannot be built against a duck-typed driver - selenium's pointer actions
    type-check their target with ``isinstance(..., WebElement)``.
    """
    chain = interactions.action_chain(driver=stub_driver)

    assert chain.pause(2) is chain


# --------------------------------------------------------------------------
# The closed consumer set, and the import boundary - AAP 0.4.2
# --------------------------------------------------------------------------


@pytest.fixture(scope="session")
def step_module_imports() -> dict[str, StepModuleImports]:
    """Every step module's declared imports, parsed once for the session.

    Session-scoped because the parse is read-only and the tree does not change
    under a test run, and because six tests below share it.

    :returns: Module name - without its ``.py`` suffix - to its imports.
    """
    return {
        parsed.name: parsed
        for parsed in (
            parse_step_module(path) for path in sorted(STEP_MODULES_DIR.glob("*.py"))
        )
    }


def test_the_step_module_census_is_complete(
    step_module_imports: dict[str, StepModuleImports],
) -> None:
    """All ten step modules were found, so no census below can pass vacuously.

    A glob that matched nothing, a renamed module or a module that disappeared
    would otherwise turn every "imported by exactly these" assertion into a
    comparison of two empty sets.
    """
    assert set(step_module_imports) == EXPECTED_STEP_MODULES


def test_press_keys_import_sites_match_the_specification(
    step_module_imports: dict[str, StepModuleImports],
) -> None:
    """``press_keys`` is imported by ``crm``, ``notes`` and ``sales`` - and no other.

    AAP 0.4.2 fixes the sites, and the asymmetry is measured behaviour rather
    than a convention: ``Crm.java:9-10``, ``Notes.java:9-10`` and
    ``Sales.java:9`` import ``org.openqa.selenium.Keys`` and no other Java step
    class does.  An exact set is asserted, so a module gaining the import is as
    much a failure as a module losing it.
    """
    consumers = {
        name
        for name, parsed in step_module_imports.items()
        if "press_keys" in parsed.names_from(AUTOMATION_PACKAGE)
    }

    assert consumers == PRESS_KEYS_CONSUMERS


def test_action_chain_import_sites_match_the_specification(
    step_module_imports: dict[str, StepModuleImports],
) -> None:
    """``action_chain`` is imported by ``crm`` and ``notes`` - and no other.

    ``Crm.java:9-10`` and ``Notes.java:9-10`` import
    ``org.openqa.selenium.interactions.Actions``; ``Sales.java:3-11`` is the
    whole of that class's import block and names ``org.openqa.selenium.Keys``
    at ``Sales.java:9`` and no ``Actions`` at all, which is why its module
    takes the keyboard helper alone.
    """
    consumers = {
        name
        for name, parsed in step_module_imports.items()
        if "action_chain" in parsed.names_from(AUTOMATION_PACKAGE)
    }

    assert consumers == ACTION_CHAIN_CONSUMERS


def test_contacts_steps_imports_neither_helper(
    step_module_imports: dict[str, StepModuleImports],
) -> None:
    """``contacts_steps`` takes nothing from this module, which is the point.

    ``Contacts.java:3-9`` is that class's entire import block - ``ContactsP``,
    ``Driver``, the two Cucumber annotations, JUnit's ``Assert`` and, at
    ``Contacts.java:8-9``, ``ExpectedConditions`` and ``WebDriverWait`` - so it
    imports neither ``Keys`` nor ``Actions``, and importing either helper here
    would be a divergence rather than a tidy-up; the AAP 0.4.1 row says so in
    as many words.  ``contacts_steps`` does import from the barrel, for its
    waits, so the assertion is specifically about the two names and not about
    the package.
    """
    contacts = step_module_imports["contacts_steps"]
    imported = contacts.names_from(AUTOMATION_PACKAGE)

    assert "press_keys" not in imported
    assert "action_chain" not in imported
    assert imported, "contacts_steps should still import its wait helper"


def test_session_steps_imports_nothing_from_the_automation_package(
    step_module_imports: dict[str, StepModuleImports],
) -> None:
    """``session_steps`` reaches the automation package not at all.

    The strongest form of the closed-set claim: one step module needs neither a
    wait, a key nor a chain, and the port leaves it that way instead of adding
    an import for symmetry.
    """
    session = step_module_imports["session_steps"]

    assert session.names_from(AUTOMATION_PACKAGE) == frozenset()
    assert not session.imports_distribution(AUTOMATION_PACKAGE)


def test_no_step_module_imports_selenium(
    step_module_imports: dict[str, StepModuleImports],
) -> None:
    """The AAP 0.4.2 boundary, from the consumers' side.

    ``app/automation`` is the only package permitted to import ``selenium``, and
    this module is why a step module never has to: the key constants arrive by
    name and the builder arrives ready-made.  Every import form is covered -
    ``import selenium``, ``import selenium.webdriver as w``, ``from selenium
    import webdriver`` and ``from selenium.webdriver.common.keys import Keys`` -
    because the check is over the parsed grammar rather than over the text, and
    a bare reference without an import could not resolve at runtime at all.

    Receiving a Selenium *instance* is not a violation and is not asserted
    against: the ``ActionChains`` that ``action_chain`` hands back is held by two
    of these modules by design, which is exactly why AAP 0.4.2 names the helper
    in the export list.
    """
    offenders = sorted(
        name
        for name, parsed in step_module_imports.items()
        if parsed.imports_distribution(SELENIUM_ROOT)
    )

    assert offenders == [], f"step modules importing selenium directly: {offenders}"
