"""BDD harness wiring: the browser fixture, the page-object fixtures and the shot hook.

This is one of exactly TWO configuration modules in the whole tree, and the two divide the
harness cleanly between them. The other one is ``tests/conftest.py``.

===========================================================  ==========================
Concern                                                      Owner
===========================================================  ==========================
The application, client, configuration and repository-root    ``tests/conftest.py``
fixtures
Creating the artifact tree before collection starts, from a   ``tests/conftest.py``,
start-up hook rather than a fixture -- a run that selects      ``app/utils/paths.py``
zero scenarios never instantiates a fixture, so a fixture      and the ``Makefile``
could not guarantee it                                        test recipe
The browser fixture, the page-object fixtures and the         THIS MODULE
report hook that captures shots
===========================================================  ==========================

Nothing here defines, shadows or overrides anything in the first two rows, and nothing here
creates the artifact tree: the layout module is asked to guarantee it, which keeps the
three owners above at three.

The two shot artifacts, and why they are NOT the same thing
==========================================================
The source project promised two kinds of image, and the promise is two sentences long. Both
are reproduced verbatim from ``[README.md:L42-L43]``, because a source value is data and
never a decision::

    It generate JSON, HTML and Txt reporters as well. It also generate `screen shots` for
    your tests if you enable it and
    also generate `error shots` for your failed test cases as well.

Quoted from the source revision, whose subject-verb disagreement is left exactly as written
rather than modernized. The rewritten README carries the same two sentences at the same two
lines with ``generates`` in place of ``generate``, so the difference between that file and
this quotation is a deliberate one and not a transcription slip.

Read closely, those two sentences specify two different triggers, and collapsing them would
either add behaviour the project never had or drop behaviour it did:

``screen shots``
    "for your tests **if you enable it**" -- OPT-IN, and therefore OFF unless something
    switches them on. They apply to a test whatever its outcome. The switch is
    :data:`SCREEN_SHOTS_ENABLED_ENV_VAR`, whose default comes from
    ``tests/support/screenshots.py`` and is ``False``.
``error shots``
    "for your **failed** test cases" -- AUTOMATIC, keyed on the outcome and gated by
    nothing in the prose. They are produced for a failing test and for no other.

The two destinations are equally distinct, and both are obtained from ``app/utils/paths.py``
rather than spelled out here: one is named with a single word and the other is hyphenated.
That asymmetry is a cross-module contract -- the report index and the artifact-serving route
key on those exact names -- so this module never re-spells either of them. It holds no
layout knowledge of its own at all.

Empty shot directories are the NORMAL state, three times over
=============================================================
Finding both directories empty after a run is the expected outcome, not a fault, and this
module says so at debug severity or says nothing at all. There are three independent reasons:

1. Screen shots are opt-in and default to off, exactly as the prose above specifies.
2. Preserved defect **D2**: the default selector is the port of ``tags = "@LogOut"``
   ``[README.md:L87]``, and no scenario carries that tag -- the feature file holds only
   ``@Login``, ``@UPGN-286``, ``@UPGN-287``, ``@UPGN-288``, ``@SalesManager`` and
   ``@PosManager``. A default run therefore selects zero scenarios and exits 5, which is a
   SUCCESS. Nothing runs, so nothing can be photographed.
3. Risk **R6**: the application under test is external and unreachable from this project's
   verification environment, so the browser-driven scenarios skip rather than run.

Emptiness is silent and successful. It never produces a warning and never raises.

A capture problem can never change a verdict (preserved defect D3)
=================================================================
Every capture failure is logged and absorbed. A shot is diagnostic metadata: turning a
passing test red because an image could not be written would corrupt the very evidence the
capture exists to support, and it would also breach the migration's non-gating property --
``<testFailureIgnore>true</testFailureIgnore>`` ``[pom.xml:L25]`` together with the six
all-``-1`` publisher thresholds ``[Jenkins:L15]`` mean nothing about this run may gate.
Defect **D3**, preserved deliberately.

The guard is therefore placed around the capture body and catches ``Exception``. It stops
there on purpose. A skip and a failure are both signalled by classes outside that hierarchy,
so a broader guard would swallow the outcome under test; a narrower one would let a
filesystem or browser error escape from a report hook, where it would surface as an internal
error rather than as the diagnostic it is.

The browser fixture skips; it never manufactures a driver
=========================================================
When Selenium is absent, when the provisioning distribution is absent, when no browser
binary can be reached or when the factory reports that it could not build a driver, the
fixture converts that into a skip carrying the factory's own one-line diagnosis. It never
substitutes an object for the browser and never substitutes anything for the external
application under test: either would manufacture green tests that prove nothing, and the
application under test is explicitly outside this migration's scope.

The skip arrives LATER than you would expect, and that is correct
=================================================================
Scenario steps resolve their fixtures at step-execution time rather than during test set-up,
so a skip raised in the browser fixture surfaces from inside the scenario runner on the
first step that asks for a browser -- in practice the ``Background`` step. The run reports
every scenario as skipped and exits 0: clean, green and honest under risk R6. This is the
expected behaviour and no attempt is made to make it eager.

It has one consequence worth stating openly rather than papering over. Because the skip
fires on that first step, NO step values are recorded when a browser is unavailable, which
is the normal state of this project's own verification environment. Any recording the step
definitions perform is therefore evidence about a local, browser-available run only. The
parity suite must ground defects **D1** and **D4** in structural evidence -- the collection
identity contract plus a structural parse of the byte-exact feature file -- and not in a
live run.

No address for the application under test is invented
=====================================================
``tests/pages/login_page.py`` resolves the login address and returns ``None`` when none is
configured, deliberately leaving the decision about what that means to its caller. This
module is that caller and the decision is made here: an unresolvable address becomes a skip
that names the configuration keys to set. No default address is supplied, because none
exists anywhere in the source project. The only two addresses in evidence are the two git
remotes named in ``[Jenkins:L3]`` and ``[README.md:L59]``, they contradict each other, and
neither is the application under test -- so neither may be substituted for it.

What this module deliberately does NOT do
=========================================
* It registers no marker and touches no test-configuration setting. ``pytest.ini`` is the
  single source of test configuration, and ``pyproject.toml`` deliberately carries no
  pytest table so it cannot silently supersede it. All seven marks are declared there.
* It defines no step. Steps live in ``tests/step_defs/login_sd.py``, which the ported runner
  module imports outright -- that import is what replaces Cucumber's glue option.
* It holds no locator, no wait and no assertion. Locators belong to ``tests/pages/*``, which
  owns every element lookup and the whole of the waiting policy.
* It performs no capture itself. Image bytes, file naming, sanitisation and the write all
  belong to ``tests/support/screenshots.py`` -- one source construct, one module.
* It creates no artifact directory of its own and it removes nothing. Wiping the artifact
  tree belongs to the ``Makefile`` clean recipe, the port of ``mvn clean``.

Every intentionally preserved defect referenced above is catalogued, with the fix its owners
may elect, in ``docs/migration-parity.md`` -- the authoritative D1 to D9 register.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Final, cast

import pytest

if TYPE_CHECKING:
    # Type-only imports: read by the type checker and never executed. Paired with the
    # deferred-annotation future import above, so every annotation naming one of these is a
    # string as far as the interpreter is concerned. That is what lets this module be
    # imported successfully in an environment where Selenium, the application package or
    # Flask are absent -- and a configuration module that fails to import aborts the entire
    # session, which makes this the highest-risk file in the directory for import errors.
    from collections.abc import Generator, Iterator

    from selenium.webdriver.remote.webdriver import WebDriver

    from app.utils.paths import TargetLayout
    from tests.pages.dashboard_page import DashboardPage
    from tests.pages.login_page import LoginPage
    from tests.support.screenshots import CaptureResult, SupportsScreenshotBytes

__all__ = [
    "CALL_PHASE",
    "DRIVER_FIXTURE_NAME",
    "ERROR_SHOTS_ENABLED_BY_DEFAULT",
    "ERROR_SHOTS_ENABLED_ENV_VAR",
    "LOGIN_URL_ENV_VAR",
    "SCREEN_SHOTS_ENABLED_ENV_VAR",
    "dashboard_page",
    "driver",
    "login_page",
    "login_url",
    "pytest_runtest_makereport",
]

# Structured logging only: the logger is obtained here and never configured. Handler, level
# and format belong exclusively to app/logging_config.py, and a configuration module that
# reconfigured them would silently outrank the application's own settings for every run of
# the suite. Nothing in this module writes to a stream directly.
_LOGGER: Final[logging.Logger] = logging.getLogger(__name__)


# =============================================================================
# The configuration surface this module reads.
#
# Every name below is an ALREADY-COMMITTED key, not an invented one. Each is
# documented in `.env.example` with the source-derived default repeated in this
# module, and each has a counterpart in `configuration.properties.example`. The
# migration's precedence chain runs: explicit argument, then environment
# variable, then environment file, then the optional properties file, then a
# hard-coded default.
#
# This module implements the environment rung and consumes the properties rung
# through the modules that own it. It loads no environment file: that rung
# belongs to `app/config.py`, whose own settings surface carries these very same
# keys so there is one vocabulary rather than two.
# =============================================================================

SCREEN_SHOTS_ENABLED_ENV_VAR: Final[str] = "SCREENSHOTS_ENABLED"
"""Environment switch for ``screen shots``. Source: ``[README.md:L42]``.

The opt-in the prose asks for: screen shots happen "for your tests **if you enable it**".
The default is NOT declared here -- it is read from ``tests/support/screenshots.py``, which
owns it and defaults it to off, so the two can never disagree. The matching properties key
is ``screenshots.enabled`` and both committed templates ship it switched off.
"""

ERROR_SHOTS_ENABLED_ENV_VAR: Final[str] = "ERROR_SHOTS_ENABLED"
"""Environment switch for ``error shots``. Source: ``[README.md:L43]``.

An opt-OUT rather than an opt-in, and the distinction matters. The prose gates error shots
on nothing at all -- they are produced "for your **failed** test cases", full stop -- so the
default below is on and the out-of-the-box behaviour is exactly the ungated behaviour the
source specified. The switch exists because the committed templates document it as the
control over this very hook, and a key documented as controlling something it does not
control would be worse than no key at all. The matching properties key is
``error.shots.enabled``.
"""

ERROR_SHOTS_ENABLED_BY_DEFAULT: Final[bool] = True
"""Default of :data:`ERROR_SHOTS_ENABLED_ENV_VAR`: ON. Source: ``[README.md:L43]``.

Declared here because no module this one is permitted to import declares it: the
screen-shot default lives with the capture helpers, but the error-shot default has no such
home -- reaching the application's settings surface would drag in the web framework, which
a verification environment is not guaranteed to have. The value is the literal reading of
the source sentence, and it matches the application-side default exactly.
"""

LOGIN_URL_ENV_VAR: Final[str] = "BASE_URL"
"""Environment override for the address of the external application under test.

The address resolver in ``tests/pages/login_page.py`` implements the explicit-argument and
properties-file rungs of the precedence chain and deliberately consults neither the
environment nor an environment file. This module supplies the environment rung by reading
this variable and passing it to that resolver as the explicit argument, which is the hook
the resolver publishes for exactly this purpose.

An unset or blank value is treated as "not configured" so that the committed, empty template
entry falls through to the properties rung instead of overriding it with emptiness -- the
same reading the application's settings surface applies to the identical key.
"""

# Boolean vocabulary, matched case-insensitively against a stripped copy of the value. It is
# the same vocabulary the harness's properties reader applies, restated here rather than
# imported because that reader is outside this module's declared dependencies; keeping the
# two identical is what stops `SCREENSHOTS_ENABLED=yes` from meaning one thing in a
# properties file and another in the environment.
_TRUE_LITERALS: Final[frozenset[str]] = frozenset({"true", "yes", "on", "1"})
_FALSE_LITERALS: Final[frozenset[str]] = frozenset({"false", "no", "off", "0"})

# The fixture whose value is a browser, looked up on the report hook's item. Published as
# data so the fixture below and the lookup that finds it cannot drift apart.
DRIVER_FIXTURE_NAME: Final[str] = "driver"

# The only run phase a shot is taken in. A test produces three reports -- set-up, call and
# tear-down -- and photographing the browser three times would triple every artifact while
# saying nothing new. The call phase is also the only one whose outcome answers the question
# the source prose asks, namely whether this is a "failed test case".
CALL_PHASE: Final[str] = "call"

# Memo for the guarded layout lookup below, and the key it is stored under. A dictionary
# rather than a decorator so that module scope stays free of imports beyond the four this
# module is allowed, and so that the "layout is unreachable" answer is cached too -- without
# that, the warning explaining it would be repeated for every single test in the session.
# Process-local by design: every parallel worker is a separate process with its own memo.
_LAYOUT_MEMO_KEY: Final[str] = "layout"
_LAYOUT_MEMO: Final[dict[str, TargetLayout | None]] = {}


# =============================================================================
# Configuration helpers.
#
# All three read at most one environment variable, none of them opens a file,
# none of them raises, and none of them ever logs a configured VALUE -- only its
# key name and, where it helps, its length. A value under one of these keys is
# not a credential today, but the surrounding configuration surface does carry
# credentials and a helper that logs values is one copy-and-paste away from
# leaking one.
# =============================================================================


def _environment_value(name: str) -> str | None:
    """Return the stripped value of environment variable *name*, or ``None``.

    Blank is deliberately indistinguishable from unset. The committed templates ship the
    address key present but empty, and reading that as a configured empty address would
    override the properties rung with nothing at all -- which is precisely the failure mode
    the precedence chain exists to avoid. The same reading is applied by the application's
    own settings surface to the identical key.

    Args:
        name: The variable to read, spelled exactly as the committed template spells it.

    Returns:
        The value with surrounding whitespace removed, or ``None`` when the variable is
        unset, empty or entirely whitespace.
    """
    # Imported inside the function rather than at module scope. Module scope is kept to the
    # four imports this module is allowed, because anything else there is evaluated the
    # instant pytest loads this file -- and a failure at that moment aborts the session
    # before a single test is collected.
    import os

    raw = os.environ.get(name)
    if raw is None:
        return None

    value = raw.strip()
    return value or None


def _environment_flag(name: str, default: bool) -> bool:
    """Return environment variable *name* read as a boolean, falling back to *default*.

    An unrecognised value falls back to the caller's documented default and says so at debug
    severity. Reading it as ``False`` instead would silently switch a feature off on a
    typo -- and reading it as ``True`` would silently switch one on, which for the opt-in
    switch would add behaviour the source project never had.

    Args:
        name: The variable to read.
        default: The documented default, used when the variable is unset or unrecognised.

    Returns:
        The configured boolean, or *default*.
    """
    value = _environment_value(name)
    if value is None:
        return default

    candidate = value.lower()
    if candidate in _TRUE_LITERALS:
        return True
    if candidate in _FALSE_LITERALS:
        return False

    # Key name and value length only, never the value itself.
    _LOGGER.debug(
        "Environment variable %s holds a %d-character value that is not a recognised "
        "boolean; using the documented default %r",
        name,
        len(value),
        default,
    )
    return default


def _screen_shots_enabled() -> bool:
    """Report whether ``screen shots`` are switched on for this run.

    The default is fetched from ``tests/support/screenshots.py`` rather than restated here,
    so the opt-in that the capture helper enforces in its own signature and the opt-in this
    hook consults are one value and not two that could drift apart.

    Returns:
        ``True`` only when something has switched screen shots on. ``False`` whenever the
        capture helpers cannot be reached at all, which is the safe direction: an
        unreachable helper cannot capture anything, and defaulting the other way would
        promise an artifact that could never appear.
    """
    try:
        from tests.support.screenshots import SCREEN_SHOTS_ENABLED_BY_DEFAULT
    except ImportError as error:  # pragma: no cover - depends on the installed environment
        # Narrow on purpose, and ModuleNotFoundError is a subclass of it. Any other
        # exception raised by that module is a genuine defect there and stays loud.
        _LOGGER.debug(
            "Capture helpers are unavailable (%s: %s); treating screen shots as switched off",
            type(error).__name__,
            error,
        )
        return False

    return _environment_flag(SCREEN_SHOTS_ENABLED_ENV_VAR, SCREEN_SHOTS_ENABLED_BY_DEFAULT)


def _error_shots_enabled() -> bool:
    """Report whether ``error shots`` are switched on for this run.

    On unless something switches them off, which reproduces the ungated wording of
    ``[README.md:L43]`` out of the box while still honouring the switch the committed
    templates document against this hook.

    Returns:
        ``True`` unless the switch has been explicitly turned off.
    """
    return _environment_flag(ERROR_SHOTS_ENABLED_ENV_VAR, ERROR_SHOTS_ENABLED_BY_DEFAULT)


# =============================================================================
# The artifact layout.
#
# This module holds NO layout knowledge: it neither spells a directory name nor
# composes a path. Both destinations, and the guarantee that they exist, come
# from `app/utils/paths.py` -- which is also what keeps the single-word and the
# hyphenated spelling correct without this module ever repeating either.
# =============================================================================


def _artifact_layout() -> TargetLayout | None:
    """Return the resolved artifact layout, guaranteeing its directories exist first.

    Reaching that layout means importing from the application package, and importing
    anything from it executes the package initialiser, which is the web-framework
    application factory. So the import genuinely needs the framework installed, and a
    verification environment is not guaranteed to have it -- hence the guard. The
    ``tests/conftest.py`` start-up hook guards the identical import the identical way.

    Existence is guaranteed by delegating to the layout module's own helper. That is a
    delegation and not a fourth owner of directory creation: the helper is idempotent,
    absorbs a refusing filesystem into a single warning, is safe against the several
    parallel workers that may reach it at the same instant, and removes nothing.

    The answer is memoised, including the negative answer, so an environment without the
    framework produces one warning for the session rather than one per test.

    Returns:
        The layout, or ``None`` when the layout module cannot be imported -- in which case
        a warning has been logged and the caller must abandon the capture rather than
        invent a destination. Never raises.
    """
    if _LAYOUT_MEMO_KEY in _LAYOUT_MEMO:
        return _LAYOUT_MEMO[_LAYOUT_MEMO_KEY]

    try:
        from app.utils.paths import ensure_layout, resolve_layout, to_posix
    except ImportError as error:  # pragma: no cover - depends on the installed environment
        _LOGGER.warning(
            "The artifact layout module is unavailable (%s: %s), so shot capture is "
            "switched off for this session. No destination is invented here: both shot "
            "directories are that module's to publish, and duplicating either spelling "
            "would break the route that serves them",
            type(error).__name__,
            error,
        )
        _LAYOUT_MEMO[_LAYOUT_MEMO_KEY] = None
        return None

    ensure_layout()
    layout = resolve_layout()
    _LOGGER.debug(
        "Shot destinations resolved from the layout module: screen shots -> %s, "
        "error shots -> %s",
        to_posix(layout.screenshots_dir),
        to_posix(layout.error_shots_dir),
    )
    _LAYOUT_MEMO[_LAYOUT_MEMO_KEY] = layout
    return layout


def _driver_from_item(item: pytest.Item) -> SupportsScreenshotBytes | None:
    """Return the browser this test was given, or ``None`` when it has none.

    Defensive on both counts, because both are ordinary. A test that is not a scenario has
    no such fixture at all; and a scenario whose browser fixture skipped never received a
    value, which is exactly what happens in this project's own verification environment
    (risk R6). Neither is a fault, so neither is reported above debug severity.

    Args:
        item: The test the report belongs to.

    Returns:
        The browser, or ``None``. Never raises.
    """
    resolved = getattr(item, "funcargs", None)
    if not isinstance(resolved, dict):
        _LOGGER.debug(
            "%s exposes no resolved fixtures, so there is no browser to photograph",
            item.nodeid,
        )
        return None

    candidate: object | None = resolved.get(DRIVER_FIXTURE_NAME)
    if candidate is None:
        _LOGGER.debug(
            "%s has no %r fixture value, so there is no browser to photograph",
            item.nodeid,
            DRIVER_FIXTURE_NAME,
        )
        return None

    # A structural contract, and the capture helper re-checks it: it verifies that the
    # object actually offers a callable image method and reports an unusable one as a
    # result rather than an exception. Casting here keeps the browser-automation type out
    # of this module's run-time surface entirely, which is the boundary that contains the
    # major-version uplift the migration performed on the automation library.
    return cast("SupportsScreenshotBytes", candidate)


def _log_capture_result(result: CaptureResult, destination: str, *, notable: bool) -> None:
    """Record the outcome of one capture attempt, without ever re-raising it.

    Severity is chosen for the reader. A written error shot is notable and reported at
    informational severity, because a test has just failed and whoever reads that failure
    wants the image's location alongside it. Everything else is debug: the capture helper has
    already logged any genuine problem once, with the test identifier and the reason, and
    repeating it here would only double the noise -- with one worker per logical processor
    that is expensive and tells nobody anything new.

    Args:
        result: The outcome returned by the capture helper.
        destination: The destination directory, already rendered for logging.
        notable: Whether a successful capture deserves informational rather than debug
            severity. Decided by the caller, which is what keeps the artifact-kind
            enumeration out of this function's imports.
    """
    if result.path is not None:
        record = _LOGGER.info if notable else _LOGGER.debug
        record(
            "Wrote a %s for %s to %s (%d bytes)",
            result.kind.value,
            result.test_id,
            result.path.as_posix(),
            result.byte_count,
        )
        return

    _LOGGER.debug(
        "No %s was written for %s in %s (%s: %s)",
        result.kind.value,
        result.test_id,
        destination,
        result.status.value,
        result.detail or "no further detail",
    )


def _record_shots(item: pytest.Item, report: pytest.TestReport) -> None:
    """Capture whichever shots this report calls for. Both kinds are independent.

    The order of the checks is chosen so that the common case costs nothing. A run in which
    screen shots are off and the test passed -- overwhelmingly the common case, and the only
    case a zero-scenario default run can even produce -- returns before the browser is
    looked up and before the filesystem is touched at all.

    A failing test with screen shots switched on legitimately produces BOTH artifacts, in
    two different directories. That is not duplication: the source prose promises the two
    independently, one keyed on a switch and one keyed on the outcome, so suppressing either
    because the other happened would drop specified behaviour.

    Args:
        item: The test the report belongs to.
        report: The call-phase report, whose outcome decides the error shot.
    """
    screen_shots = _screen_shots_enabled()
    error_shots = report.failed and _error_shots_enabled()

    if not screen_shots and not error_shots:
        _LOGGER.debug(
            "No shot is called for by %s: screen shots are switched off and the test did "
            "not fail. An empty shot directory is the documented normal state",
            item.nodeid,
        )
        return

    browser = _driver_from_item(item)
    if browser is None:
        # Already reported at debug severity by the lookup. A quiet no-op, deliberately:
        # a scenario that skipped before it acquired a browser is the normal outcome here.
        return

    layout = _artifact_layout()
    if layout is None:
        # Already reported at warning severity, once for the session.
        return

    from tests.support.screenshots import capture_error_shot, capture_screen_shot

    if screen_shots:
        # `enabled` is passed the resolved switch rather than a literal, so the gate the
        # capture helper enforces in its signature is driven by the configured value and
        # this call can never accidentally start producing artifacts on its own.
        _log_capture_result(
            capture_screen_shot(browser, item.nodeid, enabled=screen_shots),
            layout.screenshots_dir.as_posix(),
            notable=False,
        )

    if error_shots:
        # No `enabled` argument exists on this one, and none should: the prose gates it on
        # the outcome alone, which the caller has already established.
        _log_capture_result(
            capture_error_shot(browser, item.nodeid),
            layout.error_shots_dir.as_posix(),
            notable=True,
        )


# =============================================================================
# Fixtures -- dependency injection, replacing the field injection the Cucumber
# container performed for the Java runner this harness was ported from.
#
# Every one is function-scoped: one browser and one set of page objects per
# scenario. A wider scope would hand two scenarios the same browser session, and
# with one worker per logical processor it would also hand two processes the
# same expectation about a session neither of them owns.
# =============================================================================


@pytest.fixture
def driver() -> Iterator[WebDriver]:
    """Provide one browser for one scenario, and always dispose of it afterwards.

    Construction is delegated in full to ``tests/support/driver_factory.py``, the module
    that ports the driver-provisioning dependency declared at ``[pom.xml:L42-L46]``. Called
    with no arguments the factory resolves everything from configuration and defaults to a
    headless browser, because the environments this suite runs in have no display; the
    explicit opt-out is a configured setting rather than an argument spelled here, so one
    fixture serves every environment.

    Disposal runs in the tear-down half, inside a ``finally``, through the factory's own
    disposal helper. Three properties of that arrangement matter: it runs even when the
    scenario failed, it is safe to call more than once, and it absorbs the error a session
    that has already gone away raises. Nothing here relies on garbage collection or on an
    object finaliser -- a finaliser runs too late and in an unspecified order, possibly after
    the report for the very test that owned the browser has been written.

    An unbuildable browser becomes a SKIP, never a failure and never an error. The
    application under test is external, unmodifiable and unreachable from this project's
    verification environment (risk **R6**), so "no browser here" is a statement about the
    environment and not about the code under test. The factory funnels every unbuildable
    condition -- automation library absent, provisioning distribution absent, unsupported
    browser, no browser binary, no display, a provisioning attempt that failed -- into one
    error type carrying a one-line diagnosis, and that diagnosis becomes the skip reason so
    the report says which routes were tried and why each one did not work.

    No object is ever substituted for the browser. A substitute would report green scenarios
    that exercised nothing, and it would also be a stand-in for the external application
    under test, which this migration places outside its scope.

    Scenario steps resolve this fixture at step-execution time rather than during set-up, so
    the skip surfaces from inside the scenario runner on the first step that asks for a
    browser. Every scenario is then reported as skipped and the process exits 0. That is the
    correct, honest outcome, and the module docstring records its one consequence: no step
    values are recorded in that situation, so the parity suite's evidence for the recording
    defects is structural rather than live.

    Yields:
        A live browser, owned by this fixture for the duration of one scenario.

    Raises:
        Skipped: Signalled through :func:`pytest.skip` when no browser can be provisioned.
            Deliberately not caught by the capture guard elsewhere in this module, which
            catches ``Exception`` and so cannot intercept an outcome signal.
    """
    # Imported here, not at module scope: reaching these names needs the browser-automation
    # library installed, and a module-scope import would turn one absent distribution into a
    # session-wide collection error for every test in the tree -- including the parity suite,
    # which is this migration's acceptance gate and needs no third-party package at all.
    from tests.support.driver_factory import (
        WebDriverProvisioningError,
        create_web_driver,
        quit_web_driver,
    )

    try:
        browser = create_web_driver()
    except WebDriverProvisioningError as error:
        # Caught narrowly and converted, exactly as that module's own documentation
        # prescribes. Any other exception from the factory is a genuine defect there and
        # stays loud rather than being disguised as an absent browser.
        _LOGGER.debug(
            "No browser could be provisioned, so this scenario is skipped: %s",
            error.skip_reason,
        )
        pytest.skip(error.skip_reason)

    try:
        yield browser
    finally:
        # Unconditional, and its boolean result is diagnostic rather than actionable: the
        # helper has already logged whatever happened, and there is nothing a test could
        # usefully do about a browser that refused to close.
        quit_web_driver(browser)


@pytest.fixture
def login_url() -> str:
    """Provide the address of the external application under test, or skip the scenario.

    This fixture exists to make one decision, and the decision is deliberately placed here.
    The address resolver in ``tests/pages/login_page.py`` returns ``None`` when nothing is
    configured and documents that turning that into a skipped scenario belongs to the harness
    fixture rather than to a page object -- keeping it out of there is what stops that module
    from importing the test framework at all.

    Two rungs of the migration's precedence chain are combined. The environment variable is
    read here and handed to the resolver as its explicit argument, which is the hook the
    resolver publishes for precisely this purpose; the resolver then consults the optional
    runtime properties file. A blank environment value is treated as unset so the committed,
    empty template entry falls through to the properties rung instead of overriding it.

    NO default address is supplied, and none may be. None exists anywhere in the source
    project: the only two addresses in evidence are the two git remotes named at
    ``[Jenkins:L3]`` and ``[README.md:L59]``, they name different repositories, and neither
    is the application under test. Substituting either would be a fabrication, and inventing
    a path such as a login suffix would be another.

    Returns:
        A non-blank address, either the environment override or the configured value.

    Raises:
        Skipped: Signalled through :func:`pytest.skip`, naming both configuration keys, when
            neither rung yields an address. This is the NORMAL state of a fresh checkout: the
            runtime properties file is git-ignored ``[.gitignore:L3]`` and both committed
            templates leave the key commented out.
    """
    from tests.pages.login_page import BASE_URL_PROPERTY_KEY, resolve_login_url

    resolved = resolve_login_url(_environment_value(LOGIN_URL_ENV_VAR))
    if not resolved:
        pytest.skip(
            "No address is configured for the external application under test, so this "
            f"scenario cannot run. Set the {LOGIN_URL_ENV_VAR} environment variable, or "
            f"the {BASE_URL_PROPERTY_KEY} key of the optional runtime properties file. "
            "Both are shipped commented out and empty in their committed templates because "
            "no address for that application exists anywhere in the source project, and "
            "none is invented here."
        )

    _LOGGER.debug("An address for the application under test was resolved for this scenario")
    return resolved


@pytest.fixture
def login_page(driver: WebDriver) -> LoginPage:
    """Provide the login page object, bound to this scenario's own browser.

    Injecting it means a step definition never constructs a page object, never holds a
    locator and never decides how long to wait -- the page object owns all three, so a change
    to the application's markup is a change in one file. This is the replacement for the
    field injection the Cucumber container performed for the Java runner.

    Deliberately side-effect free: constructing it stores the browser and the default wait
    budget and performs no automation call, so nothing is navigated and nothing is looked up
    until a step asks for it. Navigation is a step's job -- the ``Background`` step of the
    ported feature is what opens the page -- and the address it needs comes from
    :func:`login_url`, which is a separate fixture precisely so that a step needing the page
    without needing an address does not skip for want of one.

    Args:
        driver: This scenario's browser, provided by :func:`driver`.

    Returns:
        A login page object bound to that browser.
    """
    # Imported from the concrete module rather than from the package: the package initialiser
    # is inert by design and re-exports nothing, so there is no shortcut to take here.
    from tests.pages.login_page import LoginPage

    return LoginPage(driver)


@pytest.fixture
def dashboard_page(driver: WebDriver) -> DashboardPage:
    """Provide the dashboard page object, bound to this scenario's own browser.

    The counterpart of :func:`login_page` for the page a successful login lands on. It
    exposes exactly one question -- whether the dashboard became visible -- because exactly
    one documented step asks it. Construction is side-effect free for the same reason and in
    the same way.

    Args:
        driver: This scenario's browser, provided by :func:`driver`.

    Returns:
        A dashboard page object bound to that browser.
    """
    from tests.pages.dashboard_page import DashboardPage

    return DashboardPage(driver)


# =============================================================================
# The report hook -- the screen-shot and error-shot lifecycle.
# =============================================================================


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item,
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    """Capture shots once a report exists, and hand that report back unaltered.

    A wrapper rather than a plain implementation, because the decision needs the finished
    report: only the report knows whether this is a "failed test case" in the sense
    ``[README.md:L43]`` uses the phrase. The wrapper therefore receives the report the
    ordinary implementations produced, acts on it, and returns it -- the same object,
    unmodified. A wrapper that failed to return it would erase the run's own report.

    Only the call phase is acted on. A test produces three reports -- set-up, call and
    tear-down -- and photographing the browser three times would triple every artifact while
    saying nothing new. It is also the only phase whose outcome answers the question the
    source prose asks.

    A skipped test is not a failed test case, so it gets no error shot. That is the
    behaviour in this project's own verification environment, where the browser is
    unreachable and every scenario skips: the hook still runs, still finds nothing to do, and
    still says nothing.

    Nothing escapes. The capture body sits inside a guard that catches ``Exception``, logs it
    once with the test identifier and the exception type, and continues -- because a shot is
    diagnostic metadata and must never change a verdict or influence an exit code (defect
    **D3**, preserved). The guard is placed after the yield so that it covers only this
    module's own work: an exception raised by an inner implementation propagates untouched,
    as it must. And it stops at ``Exception`` on purpose, so the classes that signal a skip
    or a failure -- which sit outside that hierarchy -- reach the runner intact.

    Nothing is deleted, nothing is created that the layout module does not create, and no
    path is composed here.

    Args:
        item: The test whose report has just been produced. The tear-down argument the
            specification also offers is not requested: the report already carries the phase
            and the outcome, and requesting an argument this hook does not read would suggest
            it does.

    Yields:
        Control to the remaining implementations, which produce the report.

    Returns:
        That same report, unaltered.
    """
    report = yield

    try:
        if report.when == CALL_PHASE:
            _record_shots(item, report)
    except Exception as error:
        # Broad, and deliberately so: a browser session in an unknown state, an automation
        # library spanning a major-version uplift and a filesystem can between them raise
        # more kinds of exception than any list here would enumerate correctly. Logged once,
        # with enough context to diagnose, and absorbed.
        _LOGGER.warning(
            "Shot capture raised %s for %s and was absorbed, so the reported outcome is "
            "unchanged (%s)",
            type(error).__name__,
            item.nodeid,
            error,
        )

    return report
