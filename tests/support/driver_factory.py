"""Factory -- builds and tears down the Selenium WebDriver the ported harness drives.

This module is the Python counterpart of exactly one construct of the Java build this
project was migrated from: the driver-provisioning dependency declared at
``[pom.xml:L42-L46]``. Reproduced verbatim, because source values are data and never
decisions (the migration's Rule T1)::

    <dependency>
        <groupId>io.github.bonigarcia</groupId>
        <artifactId>webdrivermanager</artifactId>
        <version>5.1.0</version>
    </dependency>

The library that provisioning exists to serve is declared four lines above it, at
``[pom.xml:L36-L40]``::

    <dependency>
        <groupId>org.seleniumhq.selenium</groupId>
        <artifactId>selenium-java</artifactId>
        <version>3.141.59</version>
    </dependency>

Their Python counterparts are pinned in ``requirements-test.txt`` as
``webdriver-manager==4.1.2`` and ``selenium==4.46.0``. Both coordinates are also published
below as module constants so the dependency-parity suite can trace the mapping by
introspection instead of by reading prose.

One source construct, one module -- Rule T2. Nothing else is ported here, and nothing
beyond those two declarations is invented, because the original project's Java sources were
never committed: there is no driver-factory class to read, only the coordinates above and
one line of README prose asking for "Browser driver (make sure you have your desired
browser driver and class path is set)" ``[README.md:L53]``. That line names no browser, no
address and no credential, and Rule T6 ("No fabrication.") forbids supplying any of them
from imagination. Every value this module defaults to is either published in the committed
configuration templates or is an environment flag documented in place below.

Selenium 3 to Selenium 4 is a major-version uplift (risk R2)
============================================================
The source build pinned ``selenium-java`` 3.141.59; the port pins ``selenium`` 4.46.0. That
is a deliberate, documented major-version uplift rather than a like-for-like swap: Selenium
4 changed the API surface materially and no Python binding equivalent to the Selenium 3
Java API is in current support. It is carried in the migration's risk register as R2, whose
mitigation is a boundary rather than a workaround -- browser interaction is confined to
this module and to ``tests/pages/*``, so the surface exposed to the API difference stays
small and auditable. Nothing else under ``tests/`` may reference Selenium.

Only the Selenium 4 API appears here. Driver construction goes through an ``Options``
object and a ``Service`` object, and the driver executable is handed to ``Service`` as its
first positional argument. Selenium 3's per-locator element accessors and its
driver-constructor keywords for the driver executable and for the browser options were all
deleted in Selenium 4 and appear nowhere in this file. This is enforced by more than
review: ``selenium`` ships a type marker and is deliberately absent from the type
checker's missing-import allowances, so every Selenium call in this module is checked with
no escape hatch and a Selenium-3-shaped call fails the quality gate rather than merely
failing at run time.

No element is ever located here, so the locator vocabulary Selenium 4 uses for lookups is
not imported: ``tests/pages/*`` owns every locator and every interaction. This module
builds a driver and disposes of it; nothing more.

Two provisioning paths, and an honest statement about why both exist
====================================================================
Driver provisioning is attempted through one of two strategies, and BOTH are wired:

``webdriver-manager``
    The primary, default strategy, and the one-to-one Python counterpart of
    ``[pom.xml:L42-L46]``. It resolves a driver executable, which is then handed to a
    ``Service``. Keeping it exercised is what preserves the source project's declared
    driver-provisioning feature rather than quietly dropping it.
``selenium-manager``
    Selenium Manager, built into Selenium 4. When a ``Service`` is constructed with no
    executable, Selenium resolves and provisions the driver itself, with zero extra
    dependencies.

Stated plainly, because a future maintainer deserves the truth rather than an implication:
the ``webdriver-manager`` distribution is retained for parity with ``[pom.xml:L42-L46]``,
NOT out of technical necessity. Selenium Manager alone would do the job. Deleting the
distribution would therefore look harmless and would in fact break the dependency-parity
criterion that asserts every declared Maven coordinate still has a declared Python
counterpart; assuming it is technically required would be equally wrong.

Whichever strategy is selected is attempted first, and the other is the documented
fallback: a failure of the first is caught, logged at warning severity and followed by an
attempt at the second, so an unavailable distribution or an unreachable download endpoint
degrades instead of ending the session. Only when both strategies fail does this module
raise.

Headless by default, with an explicit opt-out
=============================================
Operation is headless unless something asks otherwise, because the environments this suite
runs in have no display. The opt-out is explicit and available at two rungs: a keyword
argument to the factory, or the ``headless`` key of the runtime properties file. A small,
fixed set of container-safe browser flags is always applied -- inert on a developer
desktop, required inside a container -- and each is documented at its definition.

Configuration, and why an absent file is the ordinary case
==========================================================
Settings are read exclusively through ``tests/support/config_reader.py``, the harness's
single point of access to the git-ignored ``configuration.properties`` runtime contract
``[.gitignore:L3]``. Because that file is git-ignored, a fresh clone, a CI run and a
container build all begin without it: absence is the COMMON path, not an error, and this
module treats it as one. Nothing is logged above debug severity for a missing file, and no
exception is raised for one.

The migration's precedence chain is, in order: an explicit argument, then the process
environment, then an environment file, then ``configuration.properties``, then a hard-coded
default. This module owns rung one (its own keyword arguments), consumes rung four through
the reader above, and declares rung-five defaults for the browser, the headless flag and
the provisioning strategy only. It reads NO environment variable and loads NO environment
file -- rungs two and three belong to ``app/config.py``. It re-declares none of the
migration's parity constants: the report thresholds, the sorting method, the report include
pattern and the artifact root have their own owners in ``app/reporting/thresholds.py`` and
``app/utils/paths.py``, and a second declaration here would create a competing source of
truth. No credential, token, password or application address is hard-coded here, and none
may be.

Failure is loud but skippable, and no driver is ever manufactured
=================================================================
The application under test is external, unmodifiable and unreachable from CI, so the
browser-driven scenarios cannot run end to end in this project's own verification
environment. That limitation is recorded as risk R6 and is stated here rather than papered
over: in CI this factory will normally NOT succeed, and that is expected.

Every condition that makes a driver unbuildable -- Selenium absent, the provisioning
distribution absent, an unsupported browser, no browser binary, no display, a failed
download -- raises :class:`WebDriverProvisioningError` carrying a diagnostic that names the
cause. The pytest wiring in ``tests/step_defs/conftest.py`` converts that into a skip in
one line, using :attr:`WebDriverProvisioningError.skip_reason`.

What this module never does is manufacture a driver it could not build. There is no test
double here, no simulated session, no in-memory substitute and no recorded-response stand-in
for the external application. Such a thing would turn a genuinely unrunnable scenario green
and prove nothing, which is the exact opposite of the structural parity evidence this
migration relies on -- collection counts, artifact production, schema conformance, constant
equality and exit-code mapping.

Teardown is explicit, and waiting belongs elsewhere
===================================================
:func:`quit_web_driver` is the disposal path and it must be called deterministically, from
the pytest wiring that built the driver. There is no finaliser hook in this module and
disposal never relies on garbage collection or on interpreter shutdown, both of which run
too late and in an unspecified order. The helper is safe to call more than once and never
raises: a teardown that raises would mask the failure the test was reporting.

No implicit wait and no page-load budget are configured here, deliberately. Waiting is
owned by ``tests/pages/base_page.py``, which uses explicit waits; blending an implicit wait
into the driver would silently change the semantics of every explicit wait downstream.

Boundaries this module keeps
============================
* No pytest wiring of any kind. The ``driver`` provider lives in
  ``tests/step_defs/conftest.py`` and this module exposes plain callables only.
* No locators, no page actions, no assertions -- ``tests/pages/*`` owns all three.
* No screen-shot or error-shot capture; ``tests/support/screenshots.py`` owns capture and
  ``app/reporting/screenshots.py`` owns indexing.
* No synthetic data. The harness's data builder is declared but deliberately unexercised,
  and calling it from here would exercise it.
* No cross-browser or cross-OS matrix. The browser setting is single-valued by design.
* No operating-system command is invoked and no external process is spawned by this module;
  the browser is launched through the Selenium API alone.
* Nothing is deleted, moved or written -- not even a stale driver cache -- and no file is
  read or written at all, so this module performs no I/O of its own.
* No remote-grid support. The address of a Selenium Grid is an environment-only setting
  with no key in the properties template, so honouring it here would be a rung-two read
  this module must not perform.
* Nothing under ``app/`` may import this module. The dependency edge runs one way, and
  this module's only first-party import is its sibling configuration reader.

Preserved behaviour, and where it is registered
===============================================
This module compensates for none of the migration's catalogued defects and must never be
made to: they are behaviour, preserved on purpose. The one parity artifact it does carry is
the retained ``webdriver-manager`` distribution described above.
``docs/migration-parity.md`` is the authoritative register of every preserved defect and of
the full Maven-to-Python mapping; consult it before changing anything here.

Usage
=====
::

    from tests.support import driver_factory

    driver = driver_factory.create_web_driver()      # headless Chrome, default strategy
    try:
        ...                                          # tests/pages/* drive it from here
    finally:
        driver_factory.quit_web_driver(driver)       # idempotent, never raises

The one-line conversion the pytest wiring performs when no driver can be built::

    try:
        driver = driver_factory.create_web_driver()
    except driver_factory.WebDriverProvisioningError as exc:
        pytest.skip(exc.skip_reason)

Resolution can be inspected on its own, without a browser and without a network, which is
what makes the configuration logic testable in isolation::

    configuration = driver_factory.resolve_driver_configuration(headless=False)
    driver = driver_factory.create_web_driver_from_configuration(configuration)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final, Protocol

from tests.support import config_reader

if TYPE_CHECKING:
    from pathlib import Path

    # Type-only import: evaluated by the type checker, never at run time. Paired with the
    # deferred-annotation future import above, so every annotation naming ``WebDriver``
    # below is a string as far as the interpreter is concerned and therefore costs nothing
    # and raises nothing when Selenium is absent.
    from selenium.webdriver.remote.webdriver import WebDriver

# The guarded run-time imports. Declared ahead of the try so the sentinel carries a precise
# static type in both environments, which is what keeps this block free of suppressions.
#
# Selenium and the provisioning distribution are pinned in ``requirements-test.txt`` but may
# be entirely absent when this module is imported: a verification environment is not
# guaranteed to have network access, so a successful installation cannot be assumed. The
# parity suite is this migration's acceptance gate and needs no third-party package at all,
# so importing this module must never fail. `ImportError` is caught deliberately narrowly --
# it covers exactly the failure this module is designed to survive, and
# `ModuleNotFoundError` is a subclass of it -- while any other exception raised by those
# distributions is a genuine defect there and stays loud.
_SELENIUM_IMPORT_ERROR: ImportError | None
try:
    from selenium import webdriver
    from selenium.common.exceptions import WebDriverException
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium.webdriver.chrome.service import Service as ChromeService
except ImportError as exc:  # pragma: no cover - depends on the installed environment
    _SELENIUM_IMPORT_ERROR = exc
else:
    _SELENIUM_IMPORT_ERROR = None

_WEBDRIVER_MANAGER_IMPORT_ERROR: ImportError | None
try:
    from webdriver_manager.chrome import ChromeDriverManager
except ImportError as exc:  # pragma: no cover - depends on the installed environment
    _WEBDRIVER_MANAGER_IMPORT_ERROR = exc
else:
    _WEBDRIVER_MANAGER_IMPORT_ERROR = None

__all__ = [
    "BROWSER_PROPERTY_KEY",
    "CHROME",
    "CONTAINER_SAFE_ARGUMENTS",
    "DEFAULT_BROWSER",
    "DEFAULT_PROVISIONING_STRATEGY",
    "DRIVER_MANAGER_PROPERTY_KEY",
    "HEADLESS_ARGUMENT",
    "HEADLESS_BY_DEFAULT",
    "HEADLESS_PROPERTY_KEY",
    "MAX_ATTEMPT_DIAGNOSTIC_LENGTH",
    "PYTHON_BROWSER_AUTOMATION_COUNTERPART",
    "PYTHON_DRIVER_PROVISIONING_COUNTERPART",
    "SOURCE_BROWSER_AUTOMATION_COORDINATE",
    "SOURCE_DRIVER_PROVISIONING_COORDINATE",
    "SUPPORTED_BROWSERS",
    "TRUNCATION_MARKER",
    "DriverConfiguration",
    "DriverProvisioningStrategy",
    "SupportsQuit",
    "WebDriverProvisioningError",
    "create_web_driver",
    "create_web_driver_from_configuration",
    "is_selenium_available",
    "is_webdriver_manager_available",
    "quit_web_driver",
    "resolve_driver_configuration",
]

# Structured logging only: the logger is obtained here, never configured. Handler, level and
# format belong exclusively to app/logging_config.py, and this module never writes to a
# stream directly.
_LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

if _SELENIUM_IMPORT_ERROR is not None:
    # Debug, not warning: a missing optional distribution is an expected state in a
    # verification environment, and this module is required to stay quiet about it. The
    # diagnostic reappears, in full, in the error raised when a driver is actually asked for.
    _LOGGER.debug(
        "Selenium is not importable (%s: %s); %s stays importable but cannot build a driver "
        "in this environment",
        type(_SELENIUM_IMPORT_ERROR).__name__,
        _SELENIUM_IMPORT_ERROR,
        __name__,
    )

# The exception family a disposal call is EXPECTED to raise when the session it owned has
# already gone away -- a browser that crashed, or a driver disposed of twice. Kept as a tuple
# so one `isinstance` check covers both environments: with Selenium absent the tuple is empty
# and the check is simply false, which needs no special case at the call site. A tuple of
# classes is immutable, so this is shared read-only state and not mutable module state, which
# matters because the suite runs one process per worker.
_EXPECTED_DISPOSAL_ERRORS: tuple[type[Exception], ...]
if _SELENIUM_IMPORT_ERROR is None:
    _EXPECTED_DISPOSAL_ERRORS = (WebDriverException,)
else:  # pragma: no cover - depends on the installed environment
    _EXPECTED_DISPOSAL_ERRORS = ()

# -----------------------------------------------------------------------------------------
# Source coordinates and their pinned Python counterparts.
#
# Held as data, not merely as prose, so the dependency-parity suite can assert the mapping
# by introspection and so the diagnostics below can name the exact pin a reader must install.
# -----------------------------------------------------------------------------------------

# The primary source construct this module ports, verbatim from `[pom.xml:L42-L46]`
# (Rule T1: source values are data, never renamed, rounded or modernised):
#
#     <dependency>
#         <groupId>io.github.bonigarcia</groupId>
#         <artifactId>webdrivermanager</artifactId>
#         <version>5.1.0</version>
#     </dependency>
#
# The line above that block, `[pom.xml:L41]`, is a repository-browser comment rather than
# part of the declaration, so it is deliberately not reproduced.
SOURCE_DRIVER_PROVISIONING_COORDINATE: Final[str] = "io.github.bonigarcia:webdrivermanager:5.1.0"

# The browser-automation library that provisioning exists to serve, verbatim from
# `[pom.xml:L36-L40]`:
#
#     <dependency>
#         <groupId>org.seleniumhq.selenium</groupId>
#         <artifactId>selenium-java</artifactId>
#         <version>3.141.59</version>
#     </dependency>
SOURCE_BROWSER_AUTOMATION_COORDINATE: Final[str] = "org.seleniumhq.selenium:selenium-java:3.141.59"

# The Python counterparts, spelled exactly as `requirements-test.txt` pins them. The
# distribution name is hyphenated while the importable module is underscored; neither
# spelling is normalised to the other anywhere in this file.
PYTHON_DRIVER_PROVISIONING_COUNTERPART: Final[str] = "webdriver-manager==4.1.2"
PYTHON_BROWSER_AUTOMATION_COUNTERPART: Final[str] = "selenium==4.46.0"

# -----------------------------------------------------------------------------------------
# Configuration keys and defaults.
#
# The three keys below are the ONLY settings this module reads, and each is spelled exactly
# as the committed `configuration.properties.example` template publishes it. Key lookup in
# the properties contract is case-sensitive and exact, so these strings are literal.
# -----------------------------------------------------------------------------------------

BROWSER_PROPERTY_KEY: Final[str] = "browser"
HEADLESS_PROPERTY_KEY: Final[str] = "headless"
DRIVER_MANAGER_PROPERTY_KEY: Final[str] = "driver.manager"

# The one browser this factory builds. Named as a constant because it is simultaneously the
# documented default, the sole supported value and the value the committed templates publish.
CHROME: Final[str] = "chrome"

# Rung-five default for `browser`. Not invented: the committed templates publish
# `browser=chrome`, and the provisioning entry point the migration prescribes is Chrome's.
# The source README asked only for "your desired browser driver" `[README.md:L53]` and named
# none, so the templates are the nearest thing to a stated choice that exists.
DEFAULT_BROWSER: Final[str] = CHROME

# Deliberately single-valued: a cross-browser and cross-OS test matrix is out of scope for
# this migration, so this is one browser and not a browser list. Published as a frozen set
# rather than a bare string so a test can assert the boundary, and so the diagnostic for an
# unsupported browser can enumerate what is actually available.
SUPPORTED_BROWSERS: Final[frozenset[str]] = frozenset({CHROME})

# Rung-five default for `headless`. True because the environments this suite runs in have no
# display; the committed templates publish `headless=true` for the same reason. The opt-out
# is explicit -- a keyword argument, or the `headless` key set to a recognised false literal.
HEADLESS_BY_DEFAULT: Final[bool] = True

# Chrome's modern headless switch. The `=new` form is the mode Chrome kept after retiring its
# original headless implementation, and it is the one the runtime environment of this project
# was verified against.
HEADLESS_ARGUMENT: Final[str] = "--headless=new"

# Environment flags, not behaviour. Chrome cannot start inside an unprivileged container
# without the first, exhausts the default shared-memory allocation without the second, and
# has no usable hardware acceleration without the third. All three are inert on a developer
# desktop and required in a container or CI executor, so they are applied unconditionally
# rather than guessed at from the surroundings. They configure the browser process only and
# say nothing about the application under test.
CONTAINER_SAFE_ARGUMENTS: Final[tuple[str, ...]] = (
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
)

# How much of one failed attempt's text is kept in the raised error. Selenium's messages put
# the human-readable cause on the first line and then append a stack trace of raw addresses,
# which measures in kilobytes and carries nothing a reader can act on. A skip message is
# rendered on a single line, so pasting that trace into it would bury the actionable cause
# rather than reveal it. The complete, untruncated exception is still emitted by the
# warning-level log entry for the same attempt, so nothing is lost -- the verbose detail goes
# where verbose detail belongs, and the message stays readable. The budget is generous enough
# to keep a typical first line whole, including any documentation link it carries.
MAX_ATTEMPT_DIAGNOSTIC_LENGTH: Final[int] = 300

# Marker appended when a diagnostic is shortened, so a truncated message is never mistaken
# for the whole story.
TRUNCATION_MARKER: Final[str] = " [...]"


class DriverProvisioningStrategy(StrEnum):
    """How the driver executable is obtained.

    The two member values are spelled exactly as the committed configuration templates
    publish them for the ``driver.manager`` key, whose documented vocabulary is
    ``selenium-manager | webdriver-manager``. Being a string enumeration, a member compares
    equal to its own spelling, so a value read from the properties file needs no translation
    table to reach this type.

    Attributes:
        WEBDRIVER_MANAGER: Resolve the executable with the ``webdriver-manager``
            distribution, the one-to-one Python counterpart of
            :data:`SOURCE_DRIVER_PROVISIONING_COORDINATE`. This is the default.
        SELENIUM_MANAGER: Let Selenium 4 resolve and provision the executable itself. The
            modern zero-dependency route, and the reason the distribution above is a parity
            artifact rather than a technical necessity.
    """

    WEBDRIVER_MANAGER = "webdriver-manager"
    SELENIUM_MANAGER = "selenium-manager"


# Rung-five default for `driver.manager`. WEBDRIVER_MANAGER, so the Python counterpart of
# `[pom.xml:L42-L46]` is genuinely exercised and the source project's driver-provisioning
# feature is preserved rather than quietly dropped. A `driver.manager` value in the
# properties file outranks this default, which is why the committed template's own
# `selenium-manager` value is honoured wherever that file is actually deployed. Either way
# both strategies stay reachable: the selected one is attempted first and the other is the
# documented fallback.
DEFAULT_PROVISIONING_STRATEGY: Final[DriverProvisioningStrategy] = (
    DriverProvisioningStrategy.WEBDRIVER_MANAGER
)


@dataclass(frozen=True, slots=True)
class DriverConfiguration:
    """The fully resolved answer to "which browser, headed or not, provisioned how".

    Produced by :func:`resolve_driver_configuration` and consumed by
    :func:`create_web_driver_from_configuration`. Separating resolution from construction is
    what makes the configuration logic assertable without a browser and without a network:
    the interesting decisions all happen before any process is started.

    Frozen and slotted on purpose. A resolved configuration is a value, and the suite runs
    under worker-process parallelism where each worker resolves and builds independently, so
    a mutable shared object would be both meaningless and hazardous.

    Attributes:
        browser: The resolved browser name, lower-cased and stripped of surrounding
            whitespace so membership of :data:`SUPPORTED_BROWSERS` is a straight comparison.
            Held verbatim otherwise, including a value this factory cannot build -- the
            unsupported case is reported by the constructor, not silently rewritten here.
        headless: ``True`` to run without a display, which is the default.
        strategy: Which provisioning strategy is attempted first.
    """

    browser: str
    headless: bool
    strategy: DriverProvisioningStrategy


class SupportsQuit(Protocol):
    """Structural contract :func:`quit_web_driver` needs from the object it disposes of.

    A real WebDriver satisfies this without inheriting anything, which is the point: the
    disposal path can be exercised with a plain call-recording object, so idempotence is
    provable without a browser, without a network and without Selenium installed. This is a
    type declaration and nothing more -- it constructs no object, substitutes for nothing,
    and is not a stand-in for the external application under test.
    """

    def quit(self) -> None:
        """Release the browser session and every process behind it."""


class WebDriverProvisioningError(RuntimeError):
    """Raised when a WebDriver genuinely cannot be built.

    Every unbuildable condition funnels through this one type: Selenium not importable, the
    provisioning distribution not importable, an unsupported browser, no browser binary, no
    display, or a provisioning attempt that failed outright. The message names the cause, and
    the per-strategy diagnoses are kept separately in :attr:`failures` so a caller can report
    exactly which routes were tried and why each one did not work.

    The application under test is external and unreachable from this project's verification
    environment (risk R6), so a caller that cannot build a driver should SKIP rather than
    fail. :attr:`skip_reason` exists to make that a one-liner::

        try:
            driver = create_web_driver()
        except WebDriverProvisioningError as exc:
            pytest.skip(exc.skip_reason)

    Deriving from :class:`RuntimeError` rather than from a Selenium exception is deliberate:
    this type must be importable, catchable and raisable when Selenium is absent, which is
    precisely the environment in which it is raised most often.

    Attributes:
        reason: The headline cause, as a single sentence.
        browser: The browser that was requested, or ``None`` when the failure happened before
            a browser was resolved.
        failures: One diagnostic string per attempted strategy, in attempt order. Empty when
            the failure preceded any attempt.
    """

    def __init__(
        self,
        reason: str,
        *,
        browser: str | None = None,
        failures: tuple[str, ...] = (),
    ) -> None:
        """Build the error and its human-readable message.

        Args:
            reason: The headline cause, phrased as a complete sentence.
            browser: The browser that was requested, when one had been resolved.
            failures: One diagnostic per attempted strategy, in the order attempted.
        """
        self.reason = reason
        self.browser = browser
        self.failures = failures
        super().__init__(self._describe())

    def _describe(self) -> str:
        """Assemble the full, possibly multi-line message carried by the exception."""
        parts = [self.reason]
        if self.browser is not None:
            parts.append(f"Requested browser: {self.browser}.")
        if self.failures:
            attempts = "".join(f"\n  - {failure}" for failure in self.failures)
            parts.append(f"Provisioning attempts, in order:{attempts}")
        return " ".join(parts)

    @property
    def skip_reason(self) -> str:
        """Return a single-line summary suitable for a skip message.

        Newlines are collapsed so the text survives a test report that renders a skip reason
        on one line, and the per-strategy diagnoses are folded into a parenthesised list so
        no information is lost in the process.

        Returns:
            A one-line diagnostic naming the cause and every attempted strategy.
        """
        summary = self.reason
        if self.browser is not None:
            summary = f"{summary} Requested browser: {self.browser}."
        if self.failures:
            summary = f"{summary} Attempted: {'; '.join(self.failures)}."
        return " ".join(summary.split())


def is_selenium_available() -> bool:
    """Report whether Selenium could be imported in this environment.

    Purely diagnostic. No caller needs to consult it in order to be correct, because
    :func:`create_web_driver` raises a fully diagnosed
    :class:`WebDriverProvisioningError` when Selenium is missing. It exists so a caller can
    distinguish "no browser automation is installed here" from "a browser automation attempt
    failed" without provoking the failure first.

    Returns:
        ``True`` when a driver can be attempted, ``False`` when the distribution is absent.
    """
    return _SELENIUM_IMPORT_ERROR is None


def is_webdriver_manager_available() -> bool:
    """Report whether the ``webdriver-manager`` distribution could be imported.

    Also diagnostic. A ``False`` result does not mean no driver can be built: Selenium
    Manager is the documented fallback and needs no third-party distribution at all. What it
    does mean is that the Python counterpart of
    :data:`SOURCE_DRIVER_PROVISIONING_COORDINATE` is not installed in this environment.

    Returns:
        ``True`` when the primary provisioning strategy can be attempted.
    """
    return _WEBDRIVER_MANAGER_IMPORT_ERROR is None


def _summarize_attempt_failure(strategy: DriverProvisioningStrategy, exc: Exception) -> str:
    """Condense one failed provisioning attempt into a readable single-line diagnostic.

    Only the first line of the exception text is kept, whitespace-collapsed and capped at
    :data:`MAX_ATTEMPT_DIAGNOSTIC_LENGTH`. That first line is where the libraries involved put
    the cause; everything after it is a stack trace of raw addresses. The full exception is
    logged at warning severity by the caller, so this shortening loses nothing that a reader
    could act on and keeps the resulting skip message legible.

    Args:
        strategy: The strategy whose attempt failed.
        exc: The exception that attempt raised.

    Returns:
        A single-line diagnostic naming the strategy, the exception type and the cause.
    """
    first_line = str(exc).split("\n", maxsplit=1)[0]
    detail = " ".join(first_line.split())
    if len(detail) > MAX_ATTEMPT_DIAGNOSTIC_LENGTH:
        detail = detail[:MAX_ATTEMPT_DIAGNOSTIC_LENGTH].rstrip() + TRUNCATION_MARKER
    if not detail:
        # Some exception types carry no message at all; the type name still identifies them.
        return f"{strategy.value}: {type(exc).__name__}"
    return f"{strategy.value}: {type(exc).__name__}: {detail}"


def _known_browsers() -> str:
    """Return the supported browser names as a stable, comma-separated list."""
    return ", ".join(sorted(SUPPORTED_BROWSERS))


def _known_strategies() -> str:
    """Return the provisioning strategy names as a stable, comma-separated list."""
    return ", ".join(sorted(member.value for member in DriverProvisioningStrategy))


def _normalize_browser(value: str) -> str:
    """Fold a browser name for comparison, without ever rewriting stored configuration.

    Surrounding whitespace and letter case are the two differences that must not decide
    whether a browser is recognised, so both are folded away here. This transforms only this
    module's own reading of its own key: the configuration reader keeps handing the stored
    value back to every other caller byte for byte, which is what preserves the
    byte-sensitive literals elsewhere in the harness.

    Args:
        value: A browser name as supplied by a caller or read from configuration.

    Returns:
        The folded name, which may be empty if the input held only whitespace.
    """
    return value.strip().lower()


def _coerce_strategy(value: DriverProvisioningStrategy | str) -> DriverProvisioningStrategy | None:
    """Turn an enumeration member or a configured spelling into a strategy.

    Args:
        value: An already-typed member, or a name such as ``webdriver-manager``.

    Returns:
        The matching member, or ``None`` when the spelling names no known strategy.
    """
    if isinstance(value, DriverProvisioningStrategy):
        return value
    try:
        return DriverProvisioningStrategy(value.strip().lower())
    except ValueError:
        return None


def _resolve_browser(explicit: str | None, properties_path: str | Path | None) -> str:
    """Resolve the browser from rung one, then rung four, then the documented default.

    An explicit argument is honoured exactly as given, even when it names something this
    factory cannot build: a caller's mistake must surface as a diagnosed error at build time
    rather than be silently replaced by the default. A configured value that is present but
    blank is treated as unset, matching how the configuration reader treats an unusable
    value, and the substitution is logged at debug only.

    Args:
        explicit: A browser supplied directly by the caller, or ``None``.
        properties_path: Properties file to read, or ``None`` for the default location.

    Returns:
        The resolved, folded browser name.
    """
    if explicit is not None:
        return _normalize_browser(explicit)

    configured = config_reader.get_property(BROWSER_PROPERTY_KEY, path=properties_path)
    if configured is None:
        return DEFAULT_BROWSER

    resolved = _normalize_browser(configured)
    if not resolved:
        _LOGGER.debug(
            "Property %r is present but blank; using the default browser %r",
            BROWSER_PROPERTY_KEY,
            DEFAULT_BROWSER,
        )
        return DEFAULT_BROWSER
    return resolved


def _resolve_headless(explicit: bool | None, properties_path: str | Path | None) -> bool:
    """Resolve the headless flag from rung one, then rung four, then the documented default.

    The recognised true and false spellings, and the debug-level fallback for anything
    outside them, are the configuration reader's own published vocabulary; nothing is
    re-implemented here.

    Args:
        explicit: An opt-in or opt-out supplied directly by the caller, or ``None``.
        properties_path: Properties file to read, or ``None`` for the default location.

    Returns:
        ``True`` to run without a display, which is the default.
    """
    if explicit is not None:
        return explicit
    return config_reader.get_boolean_property(
        HEADLESS_PROPERTY_KEY,
        HEADLESS_BY_DEFAULT,
        path=properties_path,
    )


def _resolve_strategy(
    explicit: DriverProvisioningStrategy | str | None,
    properties_path: str | Path | None,
) -> DriverProvisioningStrategy:
    """Resolve the provisioning strategy from rung one, then rung four, then the default.

    An unrecognised EXPLICIT spelling is a caller mistake, so it is logged at warning
    severity and reported with its own text -- a strategy name is a vocabulary item, never a
    credential, so quoting it is safe. An unrecognised CONFIGURED spelling is logged at debug
    severity and reported by length only, matching the configuration reader's rule that a
    file value may hold anything and must never reach the log. Both fall back to
    :data:`DEFAULT_PROVISIONING_STRATEGY` rather than raising, because resolution is total by
    contract and the other strategy remains reachable as the documented fallback anyway.

    Args:
        explicit: A strategy supplied directly by the caller, or ``None``.
        properties_path: Properties file to read, or ``None`` for the default location.

    Returns:
        The strategy to attempt first.
    """
    if explicit is not None:
        coerced = _coerce_strategy(explicit)
        if coerced is not None:
            return coerced
        _LOGGER.warning(
            "Provisioning strategy %r names none of %s; using %r",
            explicit,
            _known_strategies(),
            DEFAULT_PROVISIONING_STRATEGY.value,
        )
        return DEFAULT_PROVISIONING_STRATEGY

    configured = config_reader.get_property(DRIVER_MANAGER_PROPERTY_KEY, path=properties_path)
    if configured is None:
        return DEFAULT_PROVISIONING_STRATEGY

    coerced = _coerce_strategy(configured)
    if coerced is not None:
        return coerced
    _LOGGER.debug(
        "Property %r holds a %d-character value naming none of %s; using %r",
        DRIVER_MANAGER_PROPERTY_KEY,
        len(configured),
        _known_strategies(),
        DEFAULT_PROVISIONING_STRATEGY.value,
    )
    return DEFAULT_PROVISIONING_STRATEGY


def resolve_driver_configuration(
    *,
    browser: str | None = None,
    headless: bool | None = None,
    strategy: DriverProvisioningStrategy | str | None = None,
    properties_path: str | Path | None = None,
) -> DriverConfiguration:
    """Resolve every driver setting without touching a browser, a driver or a network.

    This is the whole of the configuration decision, separated from construction so it can be
    asserted on its own: no distribution needs to be installed, no executable needs to exist
    and nothing needs to be reachable for this function to return. It never raises. A browser
    it cannot build is reported as such by the constructor, not rejected here.

    Precedence is an explicit argument first, then the ``configuration.properties`` runtime
    file through ``tests/support/config_reader.py``, then this module's documented defaults.
    The process environment and any environment file are two further rungs that sit BETWEEN
    those, and they belong to ``app/config.py``: this module reads neither, so a caller
    wanting environment precedence passes the value in as an explicit argument.

    That file is git-ignored, so it is normally absent, and absence is silent by design:
    nothing above debug severity is logged for it and no exception is raised.

    Args:
        browser: Browser to drive. Defaults to the ``browser`` property, then
            :data:`DEFAULT_BROWSER`.
        headless: Whether to run without a display. Pass ``False`` for the explicit
            opt-out. Defaults to the ``headless`` property, then
            :data:`HEADLESS_BY_DEFAULT`.
        strategy: Which provisioning strategy to attempt first, as a member or as a name.
            Defaults to the ``driver.manager`` property, then
            :data:`DEFAULT_PROVISIONING_STRATEGY`.
        properties_path: Properties file to read, for tests that point it at a temporary
            file. Defaults to the repository-root location the reader resolves.

    Returns:
        The fully resolved :class:`DriverConfiguration`.
    """
    configuration = DriverConfiguration(
        browser=_resolve_browser(browser, properties_path),
        headless=_resolve_headless(headless, properties_path),
        strategy=_resolve_strategy(strategy, properties_path),
    )
    _LOGGER.debug(
        "Resolved driver configuration: browser=%r headless=%r strategy=%r",
        configuration.browser,
        configuration.headless,
        configuration.strategy.value,
    )
    return configuration


def _build_chrome_options(configuration: DriverConfiguration) -> ChromeOptions:
    """Build a Selenium 4 options object for one driver construction.

    A FRESH instance is built for every attempt rather than shared between them: Selenium
    takes ownership of the options object it is handed and may record state on it, so reusing
    one across a failed attempt and its fallback would carry that state along.

    Only two things are configured -- the headless switch when headless operation is resolved,
    and the fixed container-safe flags. No implicit wait and no page-load budget are set here:
    waiting belongs to the page-object base, which uses explicit waits, and mixing the two
    would silently change the semantics of every explicit wait downstream.

    Args:
        configuration: The resolved configuration whose headless flag is applied.

    Returns:
        A Chrome options object ready to be handed to the driver constructor.
    """
    options = ChromeOptions()
    if configuration.headless:
        options.add_argument(HEADLESS_ARGUMENT)
    for argument in CONTAINER_SAFE_ARGUMENTS:
        options.add_argument(argument)
    return options


def _provisioning_order(
    preferred: DriverProvisioningStrategy,
) -> tuple[DriverProvisioningStrategy, ...]:
    """Return the strategies to attempt, most preferred first.

    The preferred strategy leads and the other one follows as the documented fallback, so both
    routes stay reachable whichever way the configuration points. Returning a plain tuple
    keeps the attempt order inspectable by a test without a browser.

    Args:
        preferred: The strategy resolved from configuration.

    Returns:
        Both strategies, preferred first.
    """
    if preferred is DriverProvisioningStrategy.WEBDRIVER_MANAGER:
        return (
            DriverProvisioningStrategy.WEBDRIVER_MANAGER,
            DriverProvisioningStrategy.SELENIUM_MANAGER,
        )
    return (
        DriverProvisioningStrategy.SELENIUM_MANAGER,
        DriverProvisioningStrategy.WEBDRIVER_MANAGER,
    )


def _strategy_unavailable_reason(strategy: DriverProvisioningStrategy) -> str | None:
    """Explain why a strategy cannot even be attempted, if it cannot.

    Checked before the attempt so a missing distribution reads as a clear diagnosis rather
    than as an unresolved name deep inside a call. Selenium Manager is never unavailable on
    its own account: it ships inside Selenium, whose absence is diagnosed once, earlier, for
    every strategy at the same time.

    Args:
        strategy: The strategy about to be attempted.

    Returns:
        A diagnostic naming the missing piece, or ``None`` when the strategy is attemptable.
    """
    if (
        strategy is DriverProvisioningStrategy.WEBDRIVER_MANAGER
        and _WEBDRIVER_MANAGER_IMPORT_ERROR is not None
    ):
        return (
            f"the {PYTHON_DRIVER_PROVISIONING_COUNTERPART} distribution is not importable "
            f"({type(_WEBDRIVER_MANAGER_IMPORT_ERROR).__name__}: "
            f"{_WEBDRIVER_MANAGER_IMPORT_ERROR}); it is the Python counterpart of "
            f"{SOURCE_DRIVER_PROVISIONING_COORDINATE}"
        )
    return None


def _build_chrome_driver(
    strategy: DriverProvisioningStrategy,
    configuration: DriverConfiguration,
) -> WebDriver:
    """Construct one Chrome WebDriver through one provisioning strategy.

    Both strategies converge on the same Selenium 4 construction: an options object and a
    service object are handed to the driver constructor. They differ in one respect only --
    whether the service is given a driver executable that was resolved beforehand, or is left
    empty so that Selenium resolves one itself. The executable is passed as the service's
    first positional argument, which is also why no removed Selenium 3 constructor keyword
    appears anywhere in this module.

    The browser process is started by Selenium. This module invokes no operating-system
    command and interpolates nothing into one.

    Args:
        strategy: The strategy to use for this single attempt.
        configuration: The resolved configuration to apply.

    Returns:
        A live WebDriver.

    Raises:
        Exception: Whatever the provisioning library or Selenium raises. The caller catches
            it, records it and moves on to the documented fallback.
    """
    options = _build_chrome_options(configuration)

    if strategy is DriverProvisioningStrategy.WEBDRIVER_MANAGER:
        executable = ChromeDriverManager().install()
        _LOGGER.debug("Provisioned a driver executable at %s", executable)
        service = ChromeService(executable)
    else:
        # No executable: Selenium Manager resolves and provisions one itself.
        service = ChromeService()

    return webdriver.Chrome(options=options, service=service)


def create_web_driver_from_configuration(configuration: DriverConfiguration) -> WebDriver:
    """Build a WebDriver from an already-resolved configuration.

    Use this when the configuration was resolved earlier -- to log it, to assert on it, or to
    build several drivers from one decision. :func:`create_web_driver` is the shorthand that
    resolves and builds in a single call.

    The preferred provisioning strategy is attempted first. A failure is caught, logged at
    warning severity and followed by an attempt at the documented fallback, so an absent
    distribution or an unreachable download endpoint degrades rather than ending the session.
    Only when every strategy has failed does this function raise, and the error then carries
    one diagnosis per attempt.

    No driver is ever manufactured to paper over a failure: there is no test double here and
    no simulated session. The application under test is external and unreachable from this
    project's verification environment (risk R6), so failing loudly and skippably is the
    correct outcome, and a caller converts the error into a skip in one line.

    Args:
        configuration: The resolved configuration to build from.

    Returns:
        A live WebDriver the caller owns and must dispose of with :func:`quit_web_driver`.

    Raises:
        WebDriverProvisioningError: When Selenium is absent, the browser is unsupported, or
            every provisioning strategy failed. :attr:`WebDriverProvisioningError.skip_reason`
            renders the cause as a one-line skip message.
    """
    if _SELENIUM_IMPORT_ERROR is not None:
        raise WebDriverProvisioningError(
            "Selenium is not installed, so no WebDriver can be built. It is pinned as "
            f"{PYTHON_BROWSER_AUTOMATION_COUNTERPART} in requirements-test.txt, as the Python "
            f"counterpart of {SOURCE_BROWSER_AUTOMATION_COORDINATE}; install the harness "
            "dependencies to make browser automation available.",
            browser=configuration.browser,
        ) from _SELENIUM_IMPORT_ERROR

    if configuration.browser not in SUPPORTED_BROWSERS:
        raise WebDriverProvisioningError(
            f"Browser {configuration.browser!r} is not supported by this factory; the "
            f"supported value is {_known_browsers()}. A cross-browser and cross-OS matrix is "
            "out of scope for this migration, so the browser setting is single-valued by "
            "design.",
            browser=configuration.browser,
        )

    failures: list[str] = []
    for strategy in _provisioning_order(configuration.strategy):
        unavailable = _strategy_unavailable_reason(strategy)
        if unavailable is not None:
            failures.append(f"{strategy.value}: {unavailable}")
            _LOGGER.warning(
                "Provisioning strategy %r cannot be attempted: %s",
                strategy.value,
                unavailable,
            )
            continue

        try:
            driver = _build_chrome_driver(strategy, configuration)
        except Exception as exc:
            # Deliberately broad. Provisioning reaches a download endpoint, a filesystem cache
            # and an operating-system process, so the failure modes are open-ended, and every
            # one of them must become a diagnosis on the way to the fallback rather than an
            # unhandled error that ends the session. `BaseException` is NOT caught, so an
            # interrupt still stops the run immediately.
            failures.append(_summarize_attempt_failure(strategy, exc))
            # The FULL exception text, stack trace included, goes to the log; only the raised
            # error's message is condensed.
            _LOGGER.warning(
                "Provisioning strategy %r failed (%s: %s); continuing to the fallback",
                strategy.value,
                type(exc).__name__,
                exc,
            )
            continue

        _LOGGER.info(
            "Built a %s WebDriver through the %r strategy (headless=%s)",
            configuration.browser,
            strategy.value,
            configuration.headless,
        )
        return driver

    raise WebDriverProvisioningError(
        "No WebDriver could be built: every provisioning strategy failed. The application "
        "under test is external and unreachable from this project's verification environment "
        "(risk R6), so a caller should treat this as a skip rather than a failure.",
        browser=configuration.browser,
        failures=tuple(failures),
    )


def create_web_driver(
    *,
    browser: str | None = None,
    headless: bool | None = None,
    strategy: DriverProvisioningStrategy | str | None = None,
    properties_path: str | Path | None = None,
) -> WebDriver:
    """Resolve configuration and build a WebDriver -- the factory entry point.

    Called with no arguments this builds a headless Chrome driver, provisioned through the
    strategy the configuration selects, defaulting to the Python counterpart of
    :data:`SOURCE_DRIVER_PROVISIONING_COORDINATE` with Selenium Manager as the documented
    fallback. Every argument is keyword-only, so a call site always says which setting it is
    overriding.

    The returned driver is owned by the caller and MUST be disposed of with
    :func:`quit_web_driver` -- from the pytest wiring that built it, deterministically. This
    module installs no finaliser hook and never relies on garbage collection.

    Args:
        browser: Browser to drive. Defaults to configuration, then :data:`DEFAULT_BROWSER`.
        headless: Whether to run without a display. Pass ``False`` for the explicit opt-out.
            Defaults to configuration, then :data:`HEADLESS_BY_DEFAULT`.
        strategy: Provisioning strategy to attempt first, as a member or a name.
        properties_path: Properties file to read, for tests pointing at a temporary file.

    Returns:
        A live WebDriver.

    Raises:
        WebDriverProvisioningError: When no driver can be built. Convert it to a skip with
            :attr:`WebDriverProvisioningError.skip_reason`.
    """
    return create_web_driver_from_configuration(
        resolve_driver_configuration(
            browser=browser,
            headless=headless,
            strategy=strategy,
            properties_path=properties_path,
        )
    )


def quit_web_driver(driver: SupportsQuit | None) -> bool:
    """Dispose of a WebDriver. Safe to call more than once, and never raises.

    This is the module's only teardown path and it is explicit by design: it must be called
    from the pytest wiring that built the driver, in the teardown half of the provider. There
    is no finaliser hook in this module, because a finaliser runs too late and in an
    unspecified order -- after the report for the test that owned the browser may already have
    been written, and possibly during interpreter shutdown.

    Nothing escapes this function. Disposal runs while a test outcome is being reported, and
    an exception raised here would replace the failure the test was reporting with a teardown
    error, hiding the real defect. Every exception is therefore absorbed and logged: at debug
    severity for the Selenium error family, which is what a session that has already gone away
    raises and is the expected outcome of a second call, and at warning severity for anything
    else, which is unexpected and worth seeing. ``None`` is accepted so a caller whose driver
    was never built can call this unconditionally.

    Args:
        driver: The driver to dispose of, or ``None`` when none was built. Any object exposing
            a no-argument disposal method is accepted, which is what lets this be exercised
            without a browser.

    Returns:
        ``True`` when disposal completed without raising. ``False`` when there was nothing to
        dispose of, or when the call raised and was absorbed -- both of which leave nothing
        for the caller to do, so the result is diagnostic rather than something to branch on.
    """
    if driver is None:
        _LOGGER.debug("No WebDriver to dispose of; disposal is a no-op")
        return False

    try:
        driver.quit()
    except Exception as exc:
        if isinstance(exc, _EXPECTED_DISPOSAL_ERRORS):
            _LOGGER.debug(
                "Disposal reported an already-unusable session (%s: %s); treating it as done",
                type(exc).__name__,
                exc,
            )
        else:
            _LOGGER.warning(
                "Disposal raised an unexpected %s (%s); absorbing it so teardown cannot mask "
                "the outcome under test",
                type(exc).__name__,
                exc,
            )
        return False

    _LOGGER.debug("Disposed of the WebDriver")
    return True
