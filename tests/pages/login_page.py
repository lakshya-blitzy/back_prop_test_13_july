"""Login page object: every locator and action the documented login steps need.

Source construct
================
The Gherkin block published in the source project's documentation ``[README.md:L104-L148]``,
materialised byte-for-byte as ``tests/features/login.feature``. That block is the only
executable specification the pre-migration project ever committed, and this module is the
Page Object Model realisation of its login half. The pattern's purpose is stated in one line
by the plan that commissioned it: "Encapsulates locators so step definitions contain no
selectors." Not one selector may therefore appear in ``tests/step_defs/login_sd.py``; all of
them live here.

ADDITIVE, and honest about it
============================
Nothing in the pre-migration project corresponds to this module. Its Java sources were never
committed, so there is no page-object class to translate and none is claimed here. This
module is a MATERIALISATION of a documented specification rather than a translation of
committed code: every locator below is derived from the STEP TEXT alone, and nothing is
supplied from imagination.

Step-to-method map
==================
Each published method serves named steps of the specification, and only those:

``open_login_page`` and ``is_loaded``
    ``[README.md:L112]``, ``Given User is on the Testinium login page`` -- the Background
    step, split into the navigation half and the queryable is-it-loaded half.
``enter_username``
    ``[README.md:L117]``, ``[README.md:L125]`` and ``[README.md:L133]``,
    ``When User enters "<username>" username``.
``enter_password``
    ``[README.md:L118]`` and ``[README.md:L126]``,
    ``And User enters "<password>" password``.
``click_login_button``
    ``[README.md:L119]``, ``[README.md:L127]`` and ``[README.md:L134]``,
    ``And User clicks the login button``.
``is_error_message_displayed`` and ``error_message_text``
    ``[README.md:L128]``, ``Then User sees error message`` -- a GENERIC message quoted with
    no expected text, so presence is the whole question; the text reader is offered beside it
    for a step definition that wants to report what was shown.
``field_validation_message_text``
    ``[README.md:L135]``, ``Then User sees "Veuillez renseigner ce champ." message``.

That is the whole surface: eight methods over five locators. ``[README.md:L120]``,
``Then User should see the dashboard``, is deliberately absent -- the dashboard assertion
surface belongs to ``tests/pages/dashboard_page.py``. So is any log-out capability: the
specification contains no log-out step at all, which is the same fact that makes the
preserved default tag filter select nothing (defect D2).

Three preserved defects reach this module
=========================================
This migration preserves the catalogued defects of the system it ports, deliberately: they
are behaviour, and the instruction to "fully match the behavior and logic of the current
implementation" is read literally. Repairing any of them is explicitly out of scope, and
``docs/migration-parity.md`` is the authoritative register of all nine.

Defect D1 -- the setters must accept LITERAL placeholder text.
    The Gherkin grammar attaches an ``Examples`` table to the outline immediately preceding
    it, so BOTH tables in the feature file bind to the THIRD outline only. The first outline
    ``[README.md:L116]`` therefore runs with the literal text ``<username>`` and
    ``<password>`` -- angle brackets included -- and PASSES while doing so. :meth:`
    LoginPage.enter_username` and :meth:`LoginPage.enter_password` consequently pass their
    argument through completely untouched: no trimming, no unquoting, no emptiness check, no
    pattern check, no address check, no coercion, and no "did you mean to substitute a table
    value?" warning. Any such guard would turn a passing scenario into a failing one.

Defect D4 -- the password value is fed into the USERNAME field, and stays that way.
    ``[README.md:L133]`` reads ``When User enters "<password>" username``, while the comment
    above it ``[README.md:L130]`` describes an empty-field case instead. The published
    Cucumber JSON of the source system is the direct evidence: its username step records the
    values from the PASSWORD column, never the addresses from the username column. This
    module therefore exposes two entirely independent setters and NO convenience wrapper --
    a ``login(username, password)`` helper is precisely how this defect would get silently
    repaired -- and it never inspects a value to guess which field was intended.

Defect D5 -- the French literal is the executable truth, and this module owns the constant.
    See :data:`EXPECTED_FIELD_VALIDATION_MESSAGE`, whose adjacent comment reproduces the
    disagreeing English comment verbatim so the defect is auditable in code rather than
    accidental.

:data:`EXPECTED_FIELD_VALIDATION_MESSAGE` is a cross-module contract
====================================================================
``tests/parity/test_defect_preservation.py`` -- this migration's behavioural acceptance gate
-- imports that name from this module to prove defect D5 survived. It is published at module
level, under exactly that spelling, for that reason: it is not an implementation detail, and
it is not a setting. It has no environment override and no locale switch.

The locators are UNVERIFIED best-effort derivations (risk R6)
=============================================================
The application these page objects drive is a third-party, French-language web application.
It is external, unmodifiable and unreachable from this project's verification environment --
recorded as risk R6 rather than papered over -- so the browser-driven scenarios cannot run
end to end here and NO locator below has been checked against a real document. Each one is
derived from the step text it serves, using a conventional strategy, and each is annotated
with the steps it serves and with its citation. The owners must confirm every one of them
against the live application.

Two consequences follow, and both are stated plainly rather than implied. Nothing here
manufactures a session it could not open: there is no test double, no simulated browser and
no stand-in for the application under test, because such a thing would turn a genuinely
unrunnable scenario green and prove nothing. And this project's parity evidence is
structural -- collection counts, artifact production, report-schema conformance, constant
equality and exit-code mapping -- never an end-to-end browser assertion.

Selenium is imported lazily, and that is a correctness requirement
==================================================================
Not one Selenium name is imported when this module is imported. The single run-time Selenium
import in the file lives inside :func:`login_locators`, and the names needed only by the type
checker live in the ``TYPE_CHECKING`` block below, paired with the deferred-annotation future
import so that every annotation naming a Selenium class is a string as far as the interpreter
is concerned.

The reason is availability rather than micro-optimisation. Selenium is pinned in
``requirements-test.txt``, but a verification environment is not guaranteed to have network
access, so a successful installation cannot be assumed -- and the parity suite that imports
:data:`EXPECTED_FIELD_VALIDATION_MESSAGE` needs no third-party distribution at all. With the
import at module scope, one absent distribution would break the acceptance gate. This is also
why :func:`login_locators` is never called at module scope: doing so would drag the import
back to import time and defeat the whole arrangement.

Selenium 3 to Selenium 4 is a major-version uplift (risk R2)
============================================================
The source build pinned ``org.seleniumhq.selenium:selenium-java`` 3.141.59
``[pom.xml:L36-L40]``; this port pins ``selenium`` 4.46.0. That is a deliberate, documented
major-version uplift and not a like-for-like swap. Only the Selenium 4 API appears below:
locators are ``(strategy, value)`` pairs handed to the base class, and the one DOM read goes
through the Selenium 4 property accessor. ``selenium`` ships a type marker and is deliberately
absent from the type checker's missing-import allowances, so every Selenium call here is
checked with no escape hatch and a Selenium-3-shaped call fails the quality gate rather than
merely failing at run time.

What this module deliberately does NOT do
=========================================
``tests/pages/base_page.py``
    Owns ALL waiting, explicit waits only. No method here builds a wait, sleeps, or
    configures an implicit budget; every one delegates.
``tests/support/driver_factory.py``
    Owns driver construction and disposal. The driver arrives already built, by constructor
    injection through the inherited constructor, and is never created, reconfigured or
    disposed of here.
``tests/support/config_reader.py``
    Owns every read of the optional runtime properties file ``[.gitignore:L3]``.
    :func:`resolve_login_url` delegates to it; this module reads no file, no process
    environment and no environment file of its own.
``tests/step_defs/login_sd.py``
    Owns the Given/When/Then bindings and every assertion. This module exposes queryable
    state and asserts nothing: the source build's JUnit assertions ``[pom.xml:L71-L75]`` port
    to plain statements there, with pytest's assertion rewriting, so no assertion library is
    introduced anywhere.
``tests/step_defs/conftest.py``
    Owns turning an unbuildable driver into a skipped scenario, and owns capturing screen
    shots and error shots. :meth:`LoginPage.open_login_page` therefore REQUIRES an explicit
    address and never decides what to do when none is configured.

Usage
=====
The harness fixture resolves the address and injects the driver; the step definitions call
the methods::

    from tests.pages.login_page import LoginPage, resolve_login_url

    page = LoginPage(driver)
    page.open_login_page(url)
    page.enter_username(username)      # verbatim, defect D1
    page.enter_password(password)      # verbatim, defect D1
    page.click_login_button()

See ``docs/testing.md`` for authoring and running scenarios, and ``docs/migration-parity.md``
for the authoritative register of preserved defects.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Final

from tests.pages.base_page import BasePage, Locator

if TYPE_CHECKING:
    # Type-only import: resolved by the type checker, never executed. Paired with the
    # deferred-annotation future import above, so the annotation naming ``WebElement`` below
    # stays a string at run time and therefore costs nothing and raises nothing when Selenium
    # is absent.
    from selenium.webdriver.remote.webelement import WebElement

__all__ = [
    "BASE_URL_PROPERTY_KEY",
    "EXPECTED_FIELD_VALIDATION_MESSAGE",
    "VALIDATION_MESSAGE_DOM_PROPERTY",
    "LoginLocators",
    "LoginPage",
    "logger",
    "login_locators",
    "resolve_login_url",
]

# Structured logging only. The logger is obtained here and never configured: handler, level
# and format belong exclusively to ``app/logging_config.py``, and nothing in this module
# writes to a stream directly or mutates the logging hierarchy. Every record below names an
# action and a locator and NEVER a typed-in value: these fields carry credentials.
logger: Final[logging.Logger] = logging.getLogger(__name__)

# Preserved verbatim from the source comment at [README.md:L130]:
#   #3- "Please fill out this field" message should be displayed if the password or username is empty
#
# D5: that comment says English, but the executable assertion at [README.md:L135] expects
# French. The assertion is the executable truth, so the French string below is the constant.
# It is 29 characters / 29 bytes and pure ASCII. It must NOT be translated to match the
# comment, must NOT be canonicalised, and must NOT lose its trailing period.
# Preserved deliberately - see docs/migration-parity.md for the authoritative D1-D9 register.
EXPECTED_FIELD_VALIDATION_MESSAGE = "Veuillez renseigner ce champ."

# The key consulted by :func:`resolve_login_url`, published as data so a test and a
# diagnostic message can both name it without spelling it twice.
#
# This is the REAL committed key, not an invented one: it is the address of the external
# application under test, documented in the committed ``.example`` template that accompanies
# the git-ignored runtime file ``[.gitignore:L3]``, mirrored as ``BASE_URL`` in
# ``.env.example``, and consumed on the application side by ``app/config.py``. In BOTH
# committed templates it is deliberately COMMENTED OUT and unset, because no address for that
# application exists anywhere in the source project and inventing one is forbidden; the only
# two addresses in evidence are the two git remotes, and neither is the application under
# test.
#
# No separate login-page key exists, so no path is appended to the configured value and none
# is guessed: a ``/login`` suffix would be exactly the fabrication the rule above forbids.
BASE_URL_PROPERTY_KEY: Final[str] = "base.url"

# The DOM property read by :meth:`LoginPage.field_validation_message_text`, published as data
# for the same reason. Spelled in camel case because that is the DOM's own spelling of it;
# the value is a property name sent to the browser, not a Python identifier.
VALIDATION_MESSAGE_DOM_PROPERTY: Final[str] = "validationMessage"


@dataclass(frozen=True)
class LoginLocators:
    """The five locators the login steps need, as an immutable, typed container.

    Frozen so a page object cannot rewrite the page's structure at run time, and typed so a
    misspelt field is a static error rather than an ``AttributeError`` inside a scenario.
    Built once, lazily, by :func:`login_locators`; never instantiated at import time.

    EVERY VALUE IS AN UNVERIFIED BEST-EFFORT DERIVATION from the step text it serves. The
    application under test is external and unreachable from this project's verification
    environment (risk R6), so none of them has been checked against a real document, and the
    owners must confirm each against the live application. Strategies were chosen for
    stability -- identifier and name first, a structural CSS selector second -- and two rules
    were applied throughout: no absolute path expression, and no selector keyed on the
    application's French user-interface copy, because that copy is outside this project's
    control.

    Attributes:
        username_field: The username input. Serves ``[README.md:L112]`` as the is-it-loaded
            probe, ``[README.md:L117]``, ``[README.md:L125]`` and -- defect D4 --
            ``[README.md:L133]``. It is also the element whose constraint-validation property
            :meth:`LoginPage.field_validation_message_text` reads.
        password_field: The password input. Serves ``[README.md:L118]`` and
            ``[README.md:L126]``.
        login_button: The submit control. Serves ``[README.md:L119]``,
            ``[README.md:L127]`` and ``[README.md:L134]``. Derived structurally, from the
            control's submit type rather than from its caption, so the French caption cannot
            break it.
        error_message: The GENERIC error message of ``[README.md:L128]``. That step quotes no
            expected text, so only presence matters. Keyed on the standard alert role, which
            is language-independent for the same reason.
        field_validation_message: The documented FALLBACK for ``[README.md:L135]``. The
            message that step expects was CONFIRMED, against Chromium 151 in this project's
            own environment, to be a browser-native constraint-validation string that never
            enters the document at all -- see
            :meth:`LoginPage.field_validation_message_text` for the evidence. This locator is
            therefore not the primary path; it is retained for the one possibility that could
            not be checked, an application that renders its own inline message beside the
            field instead of relying on native validation. Swap the method's implementation to
            :meth:`BasePage.read_text` over this locator if the owners find that to be the
            case.
    """

    username_field: Locator
    password_field: Locator
    login_button: Locator
    error_message: Locator
    field_validation_message: Locator


@lru_cache(maxsize=1)
def login_locators() -> LoginLocators:
    """Build -- once, lazily -- the locator set the login steps use.

    This function is the ONLY place in this file that touches Selenium at run time, and the
    laziness is a correctness requirement rather than a style choice: the strategy vocabulary
    lives in Selenium, the parity suite that imports
    :data:`EXPECTED_FIELD_VALIDATION_MESSAGE` must keep working when Selenium is not
    installed, and a module-scope import would make one absent distribution break this
    migration's acceptance gate. For the same reason NOTHING at module scope calls this
    function; the cache below makes the first CALL pay the cost, not the first import.

    ``maxsize=1`` because the function takes no argument and there is exactly one login page:
    the second call and every call after it returns the same instance, so the frozen container
    can be treated as a constant by every caller without any of them re-deriving it.

    Returns:
        The five locators of :class:`LoginLocators`, all of them unverified best-effort
        derivations from the step text -- see that class for the per-locator citations and for
        the honest statement of what has and has not been checked.

    Raises:
        ModuleNotFoundError: If Selenium is not installed. Raised HERE, at the point of use,
            rather than at import time, which is the entire point of the arrangement: driving
            a browser without Selenium must fail loudly, while reading the parity constant
            must not fail at all.
    """
    # The one run-time Selenium import in this file. ``By``'s members are plain strings --
    # ``By.ID`` is "id", ``By.CSS_SELECTOR`` is "css selector" -- which is why a locator is a
    # pair of ``str`` and needs no conversion at the call site.
    from selenium.webdriver.common.by import By

    return LoginLocators(
        # [README.md:L112], [L117], [L125] and [L133]. Named for the step's own noun and for
        # the data table's own column heading, which is the closest thing to evidence this
        # project has about the field's identity.
        username_field=(By.ID, "username"),
        # [README.md:L118] and [L126]. Same derivation, from the other noun and column.
        password_field=(By.ID, "password"),
        # [README.md:L119], [L127] and [L134]. Structural on purpose: the control is
        # identified by being the form's submit control, never by its French caption.
        login_button=(By.CSS_SELECTOR, "button[type=submit]"),
        # [README.md:L128]. The standard alert role is the language-independent container for
        # an error announcement, so this survives a change of interface language.
        error_message=(By.CSS_SELECTOR, "[role=alert]"),
        # [README.md:L135]. FALLBACK ONLY -- see LoginPage.field_validation_message_text for
        # why the property read is preferred. The conventional shape for an inline validation
        # message bound to a field is an identifier derived from the field's own name.
        field_validation_message=(By.ID, "username-error"),
    )


def resolve_login_url(override: str | None = None) -> str | None:
    """Resolve the address of the login page, or report that none is configured.

    A thin resolver, and deliberately nothing more. It implements two of the five rungs of
    this migration's configuration precedence -- an explicit argument, then the optional
    runtime properties file ``[.gitignore:L3]`` -- and it declares no default of its own,
    because no address for the external application under test exists anywhere in the source
    project and inventing one is forbidden. See :data:`BASE_URL_PROPERTY_KEY` for the key
    consulted and for why both committed templates leave it unset.

    Reading the file is delegated in full to ``tests/support/config_reader.py``, the harness's
    single cached reader; this function opens nothing, parses nothing, and consults neither
    the process environment nor an environment file. That import is performed HERE rather than
    at module scope because the reader delegates in turn to the application package, whose
    own package initialiser is the Flask application factory -- so importing it transitively
    wants Flask, which a network-less verification environment may not have. The import is
    guarded narrowly, and an unavailable reader degrades to "nothing is configured".

    An empty configured value is reported as "not configured", matching the semantics
    ``app/config.py`` applies to the very same key: an empty string is not a usable address,
    and both committed templates document the key empty or commented out.

    Args:
        override: An address supplied by the caller, which wins outright when it is not
            ``None``. Returned verbatim: it is neither validated nor rewritten, because the
            address of an external application is the caller's to decide and this project has
            nothing to validate it against. An empty override is honoured as an override, so
            a caller can express "" deliberately.

    Returns:
        The resolved address, or ``None`` when neither an override nor a configured value is
        available -- which is the NORMAL state of a fresh checkout, since the runtime
        properties file is git-ignored and the key is commented out in the template.

    Note:
        This function never raises, and it never decides what an absent address means. Turning
        ``None`` into a skipped scenario is the harness fixture's job in
        ``tests/step_defs/conftest.py``; :meth:`LoginPage.open_login_page` correspondingly
        demands a real address. Keeping that decision out of here is what stops this module
        from importing the test framework at all.
    """
    if override is not None:
        logger.debug("Login address supplied by the caller; configuration not consulted")
        return override

    try:
        from tests.support import config_reader
    except ImportError as exc:  # pragma: no cover - depends on the installed environment
        # Narrow on purpose: this covers exactly the failure the arrangement above is designed
        # to survive, and ``ModuleNotFoundError`` is a subclass of it. Any other exception
        # raised by the reader is a genuine defect there and must stay loud rather than being
        # silently downgraded to "no configuration".
        logger.debug(
            "Harness configuration reader is unavailable (%s: %s); no login address resolved",
            type(exc).__name__,
            exc,
        )
        return None

    # ``get_property`` is the reader's exact-match accessor: case-sensitive, verbatim, and
    # documented never to raise. An absent key, an absent file, an unreadable file and a
    # malformed file all arrive here as ``None``.
    configured = config_reader.get_property(BASE_URL_PROPERTY_KEY)

    if not configured:
        logger.debug(
            "No login address configured under %r; the caller must decide what that means",
            BASE_URL_PROPERTY_KEY,
        )
        return None

    logger.debug("Resolved the login address from configuration key %r", BASE_URL_PROPERTY_KEY)
    return configured


class LoginPage(BasePage):
    """The login page of the application under test, as the documented steps see it.

    Eight methods over the five locators of :class:`LoginLocators`, one group per step of the
    specification, and nothing else. There is no ``login(username, password)`` convenience
    wrapper, no log-out, no forgotten-password flow, no role selector and no screen-shot
    helper: the specification drives the steps individually, contains no step for any of
    those, and the migration's instruction is that no feature may be dropped and none may be
    added. The wrapper in particular is excluded on purpose -- see defect D4 on
    :meth:`enter_username`.

    Inherited wholesale, and deliberately not overridden:

    * The constructor. A page object receives an ALREADY-CONSTRUCTED driver by injection and
      an optional explicit-wait budget; construction is side-effect free, performs no Selenium
      call, and is therefore cheap even where Selenium is absent.
    * All waiting. Every method below delegates to the base class, which owns explicit waits
      exclusively. Nothing here sleeps, polls, or configures an implicit budget.

    This class asserts nothing. It exposes queryable state -- two predicates and two text
    readers -- and ``tests/step_defs/login_sd.py`` does the deciding with plain statements,
    which is how the source build's JUnit assertions ``[pom.xml:L71-L75]`` are ported without
    introducing an assertion library.

    Every locator it uses is an unverified best-effort derivation from the step text, because
    the application under test is external and unreachable from this project's verification
    environment (risk R6). See :class:`LoginLocators`.

    Thread safety:
        The class adds no state of its own to the two attributes the base class sets once at
        construction, and :func:`login_locators` returns a frozen, cached container, so an
        instance is safe to share for reading. A browser session is single-threaded by nature,
        which is why the harness parallelises across worker PROCESSES -- each with its own
        session and its own page objects -- rather than across threads.
    """

    # -------------------------------------------------------------------------------------
    # The Background step: [README.md:L112] "Given User is on the Testinium login page".
    # -------------------------------------------------------------------------------------

    def open_login_page(self, url: str) -> None:
        """Load the login page at ``url``.

        Serves the navigation half of ``[README.md:L112]``.

        The address is REQUIRED and is not defaulted here. :func:`resolve_login_url` is the
        resolver, and deciding what to do when it yields ``None`` -- skip the scenario, or fail
        it -- belongs to the harness fixture in ``tests/step_defs/conftest.py``. Pushing that
        decision outwards is what keeps this module free of any dependency on the test
        framework, so the parity suite can import it under a bare interpreter.

        Args:
            url: The address to load, passed through to the base class verbatim. It is neither
                validated nor rewritten: the address of an external application is
                configuration the caller owns, and no address for this one exists in the
                source project to validate against.
        """
        logger.debug("Opening the login page [README.md:L112]")
        self.navigate_to(url)

    def is_loaded(self, timeout: float | None = None) -> bool:
        """Report whether the login page has finished loading.

        Serves the queryable half of ``[README.md:L112]``, so a step definition can decide with
        a plain statement rather than being interrupted by an exception. Visibility of the
        username field is the probe: it is the first field the very next step types into, so
        "that field is visible" is precisely the condition the Background step promises.

        Args:
            timeout: Per-call explicit-wait budget in seconds, or ``None`` for the instance
                default. Pass a short budget when the expected answer is "no", because a
                negative answer costs the whole budget.

        Returns:
            True if the username field became visible inside the budget, False if it did not.
        """
        return self.is_visible(login_locators().username_field, timeout)

    # -------------------------------------------------------------------------------------
    # Credential entry. Two INDEPENDENT setters -- see defect D4 -- and both pass their
    # argument through verbatim -- see defect D1.
    # -------------------------------------------------------------------------------------

    def enter_username(self, value: str) -> None:
        """Type ``value`` into the USERNAME field, exactly as given.

        Serves ``[README.md:L117]``, ``[README.md:L125]`` and ``[README.md:L133]``.

        PRESERVED DEFECT D1 -- read before changing this method. ``value`` reaches the field
        untouched. It is not trimmed, unquoted, case-adjusted, length-checked, pattern-checked,
        address-checked, canonicalised or coerced, an empty string is typed rather than
        rejected, and text that still contains its angle-bracketed placeholder is typed as-is
        without so much as a warning. The first outline of the specification has no data table
        of its own -- the Gherkin grammar attaches both tables in the feature file to the third
        outline -- so it reaches this method with the LITERAL text ``<username>`` and it PASSES
        while doing so. Any guard here would turn that passing scenario into a failing one.

        PRESERVED DEFECT D4 -- read before changing this method, and before adding any other.
        ``[README.md:L133]`` reads ``When User enters "<password>" username``: the third
        outline feeds the value from its PASSWORD column into this, the username field, while
        the comment above it ``[README.md:L130]`` describes an empty-field case instead. That
        is not merely visible in the feature text -- it is observable in the report the source
        system published, whose username step records the password-column values and never the
        addresses from the username column. This method therefore writes to the username field
        and to nothing else: it does not consult, cross-check, warn about, log a warning about
        or repair the mapping, and it does not inspect ``value`` to guess which field was
        intended. Adding a ``login(username, password)`` wrapper would silently repair the
        defect by construction, which is why no such method exists on this class.

        Both defects are preserved deliberately; ``docs/migration-parity.md`` is the
        authoritative register and records the fix for each as a follow-up the owners may
        elect. Neither is applied here.

        Args:
            value: The exact characters to type. Handed to the base class verbatim (defect
                D1), which in turn hands them to Selenium verbatim.
        """
        # The value is deliberately absent from the log record: this field carries a
        # credential, and a credential must never reach a log.
        logger.debug("Entering the username [README.md:L117, L125, L133]")

        # Defect D4: the username locator, unconditionally. No branch, no inspection, no
        # cross-check against what the password setter was given.
        # Defect D1: ``value`` is forwarded as received, with nothing between it and the base
        # class. ``type_into`` clears the field first, which is field hygiene rather than a
        # transformation of the value.
        self.type_into(login_locators().username_field, value)

    def enter_password(self, value: str) -> None:
        """Type ``value`` into the PASSWORD field, exactly as given.

        Serves ``[README.md:L118]`` and ``[README.md:L126]``.

        PRESERVED DEFECT D1 applies here exactly as it does to :meth:`enter_username`: the
        argument is forwarded untouched, an empty string is typed rather than rejected, and the
        literal text ``<password>`` is typed as-is for the outline that has no data table of
        its own -- which passes while doing so. See that method for the full statement, and
        ``docs/migration-parity.md`` for the register entry.

        This setter is completely independent of :meth:`enter_username`, which is what
        preserves defect D4: the third outline of the specification never calls this method at
        all, feeding its password-column value into the username field instead
        ``[README.md:L133]``, and nothing here notices or compensates.

        Args:
            value: The exact characters to type. Handed to the base class verbatim (defect D1).
        """
        # As above: the action and nothing else is logged. A password must never reach a log.
        logger.debug("Entering the password [README.md:L118, L126]")

        # Defect D4: the password locator, unconditionally. Defect D1: verbatim.
        self.type_into(login_locators().password_field, value)

    # -------------------------------------------------------------------------------------
    # Submission: [README.md:L119], [L127] and [L134] "And User clicks the login button".
    # -------------------------------------------------------------------------------------

    def click_login_button(self) -> None:
        """Click the login button.

        Serves ``[README.md:L119]``, ``[README.md:L127]`` and ``[README.md:L134]``.

        Takes no budget argument, because the step text offers none: the base class waits for
        the control to become clickable -- enabled as well as visible -- using the instance
        default, which is what makes the click reliable without a fixed pause anywhere in the
        harness.
        """
        logger.debug("Clicking the login button [README.md:L119, L127, L134]")
        self.click(login_locators().login_button)

    # -------------------------------------------------------------------------------------
    # Outcome queries: [README.md:L128] and [README.md:L135].
    # -------------------------------------------------------------------------------------

    def is_error_message_displayed(self, timeout: float | None = None) -> bool:
        """Report whether the generic error message is displayed.

        Serves ``[README.md:L128]``, ``Then User sees error message``. That step quotes NO
        expected text -- unlike ``[README.md:L135]`` -- so presence is the whole question and
        this predicate answers exactly it. A step definition can therefore decide with a plain
        statement instead of catching an exception.

        Args:
            timeout: Per-call explicit-wait budget in seconds, or ``None`` for the instance
                default.

        Returns:
            True if the error message became visible inside the budget, False if it did not.
        """
        return self.is_visible(login_locators().error_message, timeout)

    def error_message_text(self, timeout: float | None = None) -> str:
        """Return the generic error message's text, unaltered.

        Offered beside :meth:`is_error_message_displayed` so a step definition that wants to
        report WHAT was shown can, even though ``[README.md:L128]`` quotes no expected text.

        The value is returned exactly as the base class reports it: not trimmed, not
        whitespace-collapsed, not case-folded, not canonicalised and not tidied of trailing
        punctuation. That discipline is defect D5's, and it applies to every text reader in
        this layer rather than only to the one whose expected value is a French literal.

        Args:
            timeout: Per-call explicit-wait budget in seconds, or ``None`` for the instance
                default.

        Returns:
            The error message exactly as Selenium reported it.

        Raises:
            TimeoutException: If the error message does not become visible within the budget.
                Propagated rather than converted into an empty string, so a failing step fails
                for real and the generated report records a real failure. Call
                :meth:`is_error_message_displayed` when "no" is an acceptable answer.
        """
        return self.read_text(login_locators().error_message, timeout)

    def field_validation_message_text(self, timeout: float | None = None) -> str:
        """Return the username field's constraint-validation message, unaltered.

        Serves ``[README.md:L135]``,
        ``Then User sees "Veuillez renseigner ce champ." message``. The expected value is
        published as :data:`EXPECTED_FIELD_VALIDATION_MESSAGE`; comparing the two is the step
        definition's job, not this method's.

        Why a DOM PROPERTY rather than element text -- the browser half is CHECKED:
            The string that step expects is what a Chromium-family browser itself reports, in
            French, for a required input left empty. Such a message is painted as an internal
            overlay rather than as document content, so it is NOT reachable as an element's
            text; it is exposed only as the input's constraint-validation DOM property, which
            is what this method reads through the Selenium 4 property accessor.

            That was confirmed empirically in this project's own environment, against Chromium
            151, rather than taken on trust. Three findings are worth recording because they
            shape this implementation:

            * With an empty required input, the property yields the message while the document
              carries no trace of it -- the text is absent from the body's rendered text, from
              the serialised markup, from every node of the tree and from the element's own
              closed shadow root, and displaying the message adds no element to the document.
            * Under a French interface locale the property yields exactly the 29-character
              pure-ASCII value published as :data:`EXPECTED_FIELD_VALIDATION_MESSAGE`,
              trailing period included, so that constant needs no adjustment. The
              corresponding English value carries a trailing period too, which the source
              comment at ``[README.md:L130]`` omits -- one more reason that comment is not the
              executable truth (defect D5).
            * The property accessor is the ONLY one of the obvious three that returns anything
              at all: an element's text yields an empty string and the DOM-attribute accessor
              yields nothing, because this is a read-only IDL property and never an HTML
              attribute.

            One consequence for callers: the property is populated on a freshly loaded page
            with no interaction at all, and it empties the instant the field stops being
            empty. Read it while the field is still empty; submitting first only makes the
            browser PAINT the message and is not required to make it readable.

        What remains UNVERIFIED, and why the fallback locator is retained (risk R6):
            Only the browser mechanism and the string were checkable here. Whether the LIVE
            application actually relies on native validation for this field cannot be checked
            at all, because that application is external, unmodifiable and unreachable from
            this project's verification environment. If it renders its own inline message
            instead, or if the field carries a stricter constraint than "required" -- an
            address type, a pattern, a length bound, or a message set by the page itself --
            then the value reported here will differ, and the owners must confirm which case
            holds. The documented fallback path is already in place for the first of those:
            read :attr:`LoginLocators.field_validation_message` with
            :meth:`BasePage.read_text` and change nothing else.

        PRESERVED DEFECT D5. The value is returned EXACTLY as the browser reports it: not
        trimmed, not whitespace-collapsed, not case-folded, not canonicalised, and above all
        not tidied of its trailing period, which is part of the expected 29-character value.
        See :data:`EXPECTED_FIELD_VALIDATION_MESSAGE` and ``docs/migration-parity.md``.

        Args:
            timeout: Per-call explicit-wait budget in seconds applied while locating the
                username field, or ``None`` for the instance default.

        Returns:
            The constraint-validation message exactly as the browser reported it. An input
            that is currently valid reports an empty string, and that empty string is returned
            as the honest answer rather than being disguised.

        Raises:
            TimeoutException: If the username field does not become visible within the budget.
            TypeError: If the browser reports a non-string for the property. The accessor is
                loosely typed by nature -- a DOM property can be any JSON value -- so the
                result is checked rather than assumed. Failing loudly here is deliberate: a
                sentinel or an empty-string fallback would let a broken read masquerade as a
                valid field and quietly pass the step.
        """
        element: WebElement = self.find_visible(login_locators().username_field, timeout)
        message = element.get_property(VALIDATION_MESSAGE_DOM_PROPERTY)

        if not isinstance(message, str):
            raise TypeError(
                f"Expected the {VALIDATION_MESSAGE_DOM_PROPERTY!r} DOM property of the "
                f"username field to be a string, got {type(message).__name__}",
            )

        # Only the length is logged, never the content: the caller receives the value in full
        # anyway, and a message echoed from a field could carry whatever was typed into it.
        logger.debug(
            "Read a %d character validation message from the username field [README.md:L135]",
            len(message),
        )

        # Defect D5: returned exactly as the browser reported it.
        return message
