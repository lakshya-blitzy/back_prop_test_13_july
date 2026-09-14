r"""The step-registration contract: phrase resolution, and nothing besides.

This module is the executable form of the three directions AAP 0.5.2 states for
the port's one behavioural adaptation of the step decorator - **deviation 7**,
``@step`` and only ``@step``:

1. **Every step phrase across all ten feature files resolves to exactly one
   implementation.**  Cucumber-JVM matches a step by its *text alone*, so a
   definition annotated ``@When`` in Java is reached by a ``Given`` in Gherkin.
   The engine used here resolves by *effective step type* instead, so a
   definition bound to one keyword would not match a use under another.  ``@step``
   restores text-only matching; these tests prove it restores it completely.
2. **No phrase resolves to more than one implementation.**  That is the price of
   ``@step``'s reach: two patterns can overlap on a concrete phrase without
   either being a duplicate registration the engine would have rejected at load
   time.  Ambiguity in this suite is therefore a defect in the step *text*, and
   the port already carries one fix for it - the ``CukeStr`` field type in
   ``features/steps/contacts_steps.py``, whose necessity is proved below with a
   throwaway matcher rather than asserted on trust.
3. **No module under ``features/steps/`` imports ``given``, ``when``, ``then``
   or the browser-automation library.**  AAP 0.4.2 names this module as the
   owner of both checks: the keyword-decorator boundary, and the invariant that
   the automation library is imported only inside ``app/automation``.

What this module deliberately does **not** cover
------------------------------------------------
AAP 0.4.1 is explicit, and the division is worth stating in the file that would
otherwise be mistaken for discharging it:

    ``tests/test_steps_registration.py`` covers only phrase resolution and does
    not discharge this obligation.

The obligation in question is **per-module behavioural parity** - that for every
step method of the paired Java class the port performs the same observable
operations in the same order: the same navigation targets and their property
sources, the same locators, the same wait target and timeout, the same keys or
action-chain sequence, the same literals, the same assertion subject and message,
and the same no-ops where a Java body is empty.  That belongs to the ten
``tests/test_steps_<area>.py`` modules, one per step module, each enumerating its
Java class's methods so an omission fails rather than passes silently.

Consequently **nothing here executes a step body**.  No test calls
``StepMatch.run``, no test drives a stub driver, and no assertion in this file is
about what a step *does*.  What is asserted is which definition a phrase reaches,
how many definitions exist and where, and what the ten modules are allowed to
import.  A green run of this module says the glue is wired; it says nothing about
whether the glue is correct.

The two corpora, and which assertion uses which
-----------------------------------------------
The counts differ by a factor of nearly three depending on how the Gherkin is
enumerated, so every assertion below names its corpus.

``AS_WRITTEN_USAGES`` - **158 usages, 93 distinct phrases**
    Each feature's ``Background`` steps counted **once**, plus every
    scenario's and outline's steps exactly as the file writes them, placeholders
    included.  This is the corpus of *authored* step lines, and it is the only
    one that contains a ``Background``: all nine ``Given`` usages in the suite
    are background lines, so an enumeration that skipped backgrounds would both
    miss them and wrongly report two definitions as unexercised.
``EXPANDED_USAGES`` - **443 usages, 143 distinct phrases**
    ``Feature.walk_scenarios(with_outlines=True)``: every ``Examples`` row
    rendered into a concrete step line, *plus* the outline templates themselves.
    This is the corpus for the outline-expansion checkbox - it is what a real run
    executes - and it **omits backgrounds entirely**, which is why it cannot
    serve for the 158 count.  :func:`test_expanded_corpus_omits_backgrounds`
    pins that difference rather than leaving it as folklore.

Mutation sensitivity, without touching a production file
--------------------------------------------------------
A test that passes today and would still pass with a definition deleted is
worthless, so each direction is paired with a proof that it can fail:

* **Counts are re-derived, never read back.**  The per-module census comes from
  the loaded registry grouped by ``location.filename``; the usage counts come
  from the engine's own parser.  Both are compared against tables derived from
  the Java authority (``src/main/java/com/testinium/step_definitions/``, pinned
  revision ``47e9d697e4a9a85da889f94a846fdf47af28a240``) and cited line by line.
  Dropping, adding or duplicating a definition moves a number this file names.
* **The source-inspection checks are helpers over *text*.**  Each takes source
  code as a string, so the same helper that scans the ten real modules is fed
  synthetic **bad** sources - a module importing ``given``, one importing the
  automation library outright, one importing it through a submodule, one hiding
  that import inside a function body, and one constructing a page object at
  module scope - and asserted to reject each.  No production file is edited,
  even temporarily: sibling work is in flight on several of these modules, and a
  committed rejection test is permanent proof where a temporary edit is none.
* **Ambiguity is proved locally.**  The plain-field counterfactual builds a
  throwaway ``ParseMatcher`` in the test and never registers it, so the real
  registry holds the same matchers afterwards as before - asserted against a
  snapshot taken in the test, not assumed.

Standing constraints honoured here
----------------------------------
* **The registry is loaded exactly once per process.**  Every test reaches it
  through :fixture:`step_registry` (session-scoped) or through
  ``conftest.load_step_registry()``, which caches.  A second load of a changed
  definition raises ``AmbiguousStep``, so "load it again to be safe" is the one
  thing that must not happen.
* **No browser, no socket, no import of the automation library, and no write
  into the repository's ``target/``.**  This module reads feature files and
  Python sources and asks the registry questions; that is all it does.
* **The step modules are never imported as Python modules.**  ``features/``
  carries no ``__init__.py`` by design, and ``load_step_modules`` *execs* the
  files rather than importing them, so ``import features.steps.x`` would create
  a second registration of every definition.  Modules are reached through the
  registry, or by reading their source text.
"""

from __future__ import annotations

import ast
import collections
from pathlib import Path
from typing import Any, Final, NamedTuple

import behave
import pytest
from behave.matchers import ParseMatcher
from behave.parser import parse_file

# ``tests/conftest.py`` owns the import path for the suite and owns the
# registry mechanism; this module reimplements neither.  It is imported as a
# top-level module because there is deliberately no ``tests/__init__.py``, so
# pytest's prepend import mode puts ``tests/`` itself on ``sys.path``.
from conftest import (
    REPO_ROOT,
    STEP_BUCKET,
    STEP_BUCKETS,
    StepMatch,
    find_all_step_matches,
    load_step_registry,
    step_modules_dir,
)

# behave is imported at this module's top level, which is a deliberate
# departure from ``conftest.py``'s rule of deferring it into
# ``load_step_registry``.  The reason that rule exists is so a test which never
# resolves a step phrase does not pay for the engine; this whole module is about
# the engine's registry and its parser, so the import is honest here and the
# module-level corpora below could not be built without it.  Both imports are
# side-effect free: neither parses a feature file nor registers anything.


# =========================================================================== #
# Locations
# =========================================================================== #

#: The Gherkin feature directory.  Derived from ``conftest.REPO_ROOT`` rather
#: than from the working directory, for the reason ``conftest.step_modules_dir``
#: states: a suite that resolved ``features/`` relative to the process's cwd
#: would pass or fail according to where pytest happened to be started.
FEATURES_DIR: Final[Path] = REPO_ROOT / "features"

#: The ten step modules' directory, taken from the same authority the loader
#: uses so that this module and the loader can never disagree about it.
STEPS_DIR: Final[Path] = step_modules_dir()

#: Suffix of a Gherkin feature file.
FEATURE_SUFFIX: Final[str] = ".feature"

#: Encoding of every feature file and every step module.  The suite is ASCII
#: apart from one French assertion string and a handful of typographic
#: characters in Gherkin comments, all of which are UTF-8.
SOURCE_ENCODING: Final[str] = "utf-8"


# =========================================================================== #
# The Java authority: the per-class census this port reproduces
#
# Measured from the annotation declarations of
# src/main/java/com/testinium/step_definitions/ at pinned revision
# 47e9d697e4a9a85da889f94a846fdf47af28a240, which AAP 0.2.1 holds REFERENCE and
# never modifies.  The table is written out here rather than read from that
# checkout on purpose: the reference tree is not part of this repository, so a
# test that opened it would fail on any machine that does not carry it.  It is
# an authority transcribed with citations, not a Python constant copied back to
# itself - the numbers on the right are what this file compares the *registry*
# against.
#
# Hooks.java declares no step at all; it became features/environment.py, which
# is why there is no eleventh row and no "environment" owner in the registry.
# =========================================================================== #


class StepClassCensus(NamedTuple):
    """One row of the Java-to-Python step-class census."""

    #: The Python step module's name without its extension - the value
    #: :attr:`conftest.StepMatch.module_name` reports.
    module: str

    #: The Java class it ports, by file name.
    java_class: str

    #: How many step definitions that class declares, and therefore how many
    #: the module must register.
    definitions: int


#: The ten step classes, in the alphabetical order of their Python modules.
JAVA_STEP_CENSUS: Final[tuple[StepClassCensus, ...]] = (
    StepClassCensus("calendar_steps", "Calendar.java", 13),
    StepClassCensus("contacts_steps", "Contacts.java", 14),
    StepClassCensus("crm_steps", "Crm.java", 12),
    StepClassCensus("employee_steps", "EmployeeStage.java", 12),
    StepClassCensus("inventory_steps", "Inventory.java", 9),
    StepClassCensus("login_steps", "LoginSD.java", 9),
    StepClassCensus("logout_steps", "LogOutSD.java", 3),
    StepClassCensus("notes_steps", "Notes.java", 11),
    StepClassCensus("sales_steps", "Sales.java", 7),
    StepClassCensus("session_steps", "Session.java", 1),
)

#: The ten module names, for parametrisation and for the directory check.
STEP_MODULE_NAMES: Final[tuple[str, ...]] = tuple(
    row.module for row in JAVA_STEP_CENSUS
)

#: 13 + 14 + 12 + 12 + 9 + 9 + 3 + 11 + 7 + 1.  The total the registry must
#: hold, and the count AAP 0.5.2 and the review findings both name.
EXPECTED_DEFINITION_COUNT: Final[int] = 91


# =========================================================================== #
# The Gherkin corpus: expected sizes
#
# Re-derived by every test that uses them; the constants are the expectation,
# not the measurement.
# =========================================================================== #

#: The ten feature files, in the order the engine discovers them (sorted by
#: path).  AAP 0.4.1 pairs each with its step module and page object; the
#: filenames are unchanged from the reference checkout, only the directory
#: moved (deviation 1).
FEATURE_FILENAMES: Final[tuple[str, ...]] = (
    "Calendar.feature",
    "Contact.feature",
    "Crm.feature",
    "EmployeeFc.feature",
    "Inventory.feature",
    "Login.feature",
    "Logout.feature",
    "Notes.feature",
    "Sales.feature",
    "Session.feature",
)

#: As-written usages per feature; they sum to
#: :data:`EXPECTED_AS_WRITTEN_USAGES`.
EXPECTED_USAGES_PER_FEATURE: Final[dict[str, int]] = {
    "Calendar.feature": 21,
    "Contact.feature": 21,
    "Crm.feature": 16,
    "EmployeeFc.feature": 14,
    "Inventory.feature": 24,
    "Login.feature": 18,
    "Logout.feature": 14,
    "Notes.feature": 16,
    "Sales.feature": 13,
    "Session.feature": 1,
}

#: Total authored step lines: each background once, plus every scenario step.
EXPECTED_AS_WRITTEN_USAGES: Final[int] = 158

#: Distinct phrases among those 158 usages, placeholders included.
EXPECTED_AS_WRITTEN_PHRASES: Final[int] = 93

#: Usages after ``Examples`` expansion, outline templates included and
#: backgrounds excluded.
EXPECTED_EXPANDED_USAGES: Final[int] = 443

#: Distinct phrases in the expanded corpus.  Larger than 93 because each
#: ``Examples`` row renders its own concrete phrase.
EXPECTED_EXPANDED_PHRASES: Final[int] = 143

#: As-written keyword census.  ``And`` dominates at 82 of 158, which is the
#: measured reason keyword-typed registration could not have expressed this
#: suite: the engine has no ``@and`` decorator at all.  ``But`` and ``*`` do not
#: occur, so neither appears here.
EXPECTED_KEYWORD_CENSUS: Final[dict[str, int]] = {
    "And": 82,
    "When": 34,
    "Then": 33,
    "Given": 9,
}

#: The keyword whose absence from the decorator surface forces ``@step``.
CONJUNCTION_KEYWORD: Final[str] = "And"

#: As-written effective-step-type census.  ``And`` inherits the type of the
#: step above it, which is how 82 ``And`` lines collapse into these three.
EXPECTED_STEP_TYPE_CENSUS: Final[dict[str, int]] = {
    "when": 116,
    "then": 33,
    "given": 9,
}


# =========================================================================== #
# The cross-keyword cases
#
# The finding's phrase "seven cross-keyword uses over two phrases" admits two
# readings.  Both are true of this tree and both are asserted below, each under
# its own name, because conflating them would leave one of them unproven:
#
#   (a) JAVA-DECLARATION vs GHERKIN-INVOCATION.  A definition whose Java
#       annotation keyword differs from the Gherkin keyword that invokes it.
#       Two phrases with eight invocation sites between them, seven of which
#       carry a keyword the Java annotation does not.  This is the reading
#       AAP 0.5.2 documents - for one of the two phrases.
#   (b) WITHIN THE GHERKIN CORPUS.  A phrase invoked under more than one
#       effective step type by the feature files themselves.  Three phrases,
#       fourteen as-written usages.
# =========================================================================== #


class InvocationSite(NamedTuple):
    """One Gherkin line that invokes a phrase, with the keyword it used."""

    #: Feature file name, without the ``features/`` prefix.
    feature: str

    #: 1-based line number of the step line.
    line: int

    #: The keyword as written: ``Given``, ``When``, ``Then`` or ``And``.
    keyword: str


class CrossKeywordDeclaration(NamedTuple):
    """A phrase whose Java annotation keyword its Gherkin uses contradict."""

    #: The step text, byte-identical in the Java annotation and in Gherkin.
    phrase: str

    #: ``<JavaFile>:<line>`` of the annotation.
    java_location: str

    #: The Java annotation keyword - ``When`` for both of these.
    java_keyword: str

    #: The Python module that ports it.
    module: str

    #: Every Gherkin line that invokes it, in feature-file order.
    sites: tuple[InvocationSite, ...]

    #: Whether AAP 0.5.2 documents this case by name.
    documented_in_aap: bool


#: The shared precondition.  ``Session.java:12`` declares it ``@When`` and six
#: features invoke it as a ``Given`` in their own ``Background``; only
#: ``Session.feature`` invokes it as a ``When``.  The committed baseline
#: ``target/cucumber.json`` records that step with ``keyword: "Given "`` and a
#: ``match.location`` pointing at ``Session.user_login_to_test_other_features``,
#: which is direct evidence that the JVM matched across types (AAP 0.5.2).
SESSION_PRECONDITION: Final[CrossKeywordDeclaration] = CrossKeywordDeclaration(
    phrase="User login to test other features",
    java_location="Session.java:12",
    java_keyword="When",
    module="session_steps",
    sites=(
        InvocationSite("Calendar.feature", 9, "Given"),
        InvocationSite("Contact.feature", 5, "Given"),
        InvocationSite("Crm.feature", 7, "Given"),
        InvocationSite("Inventory.feature", 9, "Given"),
        InvocationSite("Notes.feature", 8, "Given"),
        InvocationSite("Sales.feature", 10, "Given"),
        InvocationSite("Session.feature", 4, "When"),
    ),
    documented_in_aap=True,
)

#: The second, undocumented case.  ``Contacts.java:17`` declares the Contacts
#: dashboard step ``@When`` and ``Contact.feature:6`` invokes it as a ``Given``
#: in that feature's own ``Background``.  AAP 0.5.2 names only the Session case;
#: this one is measured here and asserted on its own, because bound to its
#: declaring keyword it would go undefined and every scenario in the file would
#: fail before reaching its first step.
CONTACTS_PRECONDITION: Final[CrossKeywordDeclaration] = CrossKeywordDeclaration(
    phrase="User is at Contact dashboard",
    java_location="Contacts.java:17",
    java_keyword="When",
    module="contacts_steps",
    sites=(InvocationSite("Contact.feature", 6, "Given"),),
    documented_in_aap=False,
)

#: Reading (a) in full: two phrases, 6 + 1 = seven invocation sites.
CROSS_KEYWORD_DECLARATIONS: Final[tuple[CrossKeywordDeclaration, ...]] = (
    SESSION_PRECONDITION,
    CONTACTS_PRECONDITION,
)

#: The seven the finding counts.
EXPECTED_CROSS_KEYWORD_SITES: Final[int] = 7

#: Reading (b): the phrases the Gherkin itself uses under more than one
#: effective step type, with those types.  Every one of the three is reachable
#: only because ``@step`` ignores the keyword.
EXPECTED_MULTI_TYPE_PHRASES: Final[dict[str, frozenset[str]]] = {
    "User login to test other features": frozenset({"given", "when"}),
    "User should see the dashboard": frozenset({"then", "when"}),
    "User should see the login dashboard": frozenset({"then", "when"}),
}

#: As-written usages carried by those three phrases: 7 + 5 + 2.
EXPECTED_MULTI_TYPE_USAGES: Final[int] = 14

#: The three Java classes that import ``io.cucumber.java.en.And``
#: (``Calendar.java``, ``Crm.java``, ``Sales.java``).  Recorded for the reader
#: and asserted only through its Python consequence - the engine publishes no
#: ``and`` decorator, so those annotations have no keyword-typed translation.
JAVA_AND_IMPORTERS: Final[tuple[str, ...]] = (
    "Calendar.java",
    "Crm.java",
    "Sales.java",
)


# =========================================================================== #
# Source-state facts preserved under AAP 0.8 "preserve, do not tidy"
# =========================================================================== #

#: The one definition of the 91 that no step line in any feature reaches.
#: ``EmployeeStage.java:16`` declares it and the port carries it; the Employee
#: feature's scenarios start from ``User is on the dashboard`` instead.  Note
#: the near-identical ``login_steps`` phrase ``User is on **the** upgenix login
#: page`` (``LoginSD.java:19``), which *is* exercised - they differ by one word
#: and are two distinct definitions in two modules, exactly as in Java.
#:
#: This is recorded as a measured fact, not reported as a defect: AAP 0.8
#: preserves the source's inconsistencies beyond the deviations AAP 0.1.3
#: inventories, and an unused glue method is one of them.
EXPECTED_UNEXERCISED_DEFINITIONS: Final[tuple[tuple[str, str], ...]] = (
    ("employee_steps", "User is on upgenix login page"),
)

#: ``Contacts.java:90`` is a **fully commented-out** annotation,
#: ``//    @When("User clicks and goes directly to the profile")``, matched by
#: the two commented-out step lines at ``Contact.feature:22-23``.  Contacts has
#: fourteen live methods and the port must not register a fifteenth.
COMMENTED_OUT_JAVA_PHRASE: Final[str] = "User clicks and goes directly to the profile"

#: Feature-level tags, by file.  Five features carry one each and five carry
#: none; AAP 0.2.2 declines to correct the latter.  ``@Smoke`` - the default tag
#: expression of the run (``CukesRunner.java:18``) - occurs exactly once in the
#: whole suite.
EXPECTED_FEATURE_TAGS: Final[dict[str, tuple[str, ...]]] = {
    "Calendar.feature": ("Calendar",),
    "Contact.feature": (),
    "Crm.feature": ("Smoke",),
    "EmployeeFc.feature": ("UPGN-344",),
    "Inventory.feature": (),
    "Login.feature": ("Login",),
    "Logout.feature": ("LogOut",),
    "Notes.feature": (),
    "Sales.feature": (),
    "Session.feature": (),
}

#: The tag the default run selects.
SMOKE_TAG: Final[str] = "Smoke"

#: Feature titles, preserved verbatim including three that are wrong:
#: ``Contact.feature`` is titled "Inventory feature", ``Notes.feature`` is
#: titled "login feature" (both mis-titled in the source and both preserved per
#: AAP 0.2.2), and ``Sales.feature`` opens with four literal dots where a name
#: belongs.  ``Session.feature`` is titled "Default".
EXPECTED_FEATURE_TITLES: Final[dict[str, str]] = {
    "Calendar.feature": "Testinium app Calendar Module",
    "Contact.feature": "Testinium app Inventory feature",
    "Crm.feature": "Testinium app CRM Module",
    "EmployeeFc.feature": "Testinium app Employees module",
    "Inventory.feature": "Testinium app Inventory feature",
    "Login.feature": "Testinium app login feature",
    "Logout.feature": "Testinium app logout feature",
    "Notes.feature": "Testinium app login feature",
    "Sales.feature": ".... app Sales feature",
    "Session.feature": "Default",
}

#: ``Login.feature:89``.  The browser-locale assertion AAP 0.8 singles out as
#: the one thing the plan cannot decide: the string is carried byte-for-byte and
#: no locale key is invented, so the scenario behaves exactly as it does today.
#: This module asserts the phrase is present and resolves; it asserts nothing
#: about a browser.
FRENCH_ASSERTION_PHRASE: Final[str] = (
    'User sees "Veuillez renseigner ce champ." message'
)

#: Where it is written.
FRENCH_ASSERTION_SITE: Final[InvocationSite] = InvocationSite(
    "Login.feature", 89, "Then"
)

#: ``Login.feature``'s five outlines, in line order, each carrying exactly one
#: Jira tag.  The ``@UPGN-288`` outline is the one holding the French string.
EXPECTED_LOGIN_OUTLINE_TAGS: Final[tuple[str, ...]] = (
    "UPGN-286",
    "UPGN-287",
    "UPGN-288",
    "UPGN-289",
    "UPGN-290",
)

#: The two ``Examples`` blocks of the valid-login outline (``@UPGN-286``), by
#: tag and data-row count.  Their asymmetry - 13 SalesManager rows against 15
#: PosManager rows - is source data and is not to be evened up.
EXPECTED_VALID_LOGIN_EXAMPLES: Final[tuple[tuple[str, int], ...]] = (
    ("SalesManager", 13),
    ("PosManager", 15),
)


# =========================================================================== #
# The Contacts quote-excluding field type
#
# The one place in the port where the naive translation is measurably broken.
# Cucumber's {string} compiles to "([^"\\]*(\\.[^"\\]*)*)", whose body cannot
# match a bare double quote.  A plain {field} in this engine's parse matcher
# renders as a non-greedy group that CAN span quotes and, because the match is
# anchored to the whole step text, backtracks across them - so the one-argument
# definition swallows a two-argument step use.
# =========================================================================== #

#: The field-type name ``features/steps/contacts_steps.py`` registers.
CUKE_STR_TYPE_NAME: Final[str] = "CukeStr"

#: The converter's regex, attached by ``parse.with_pattern``.  It differs from
#: Cucumber's in one respect only - it does not admit a backslash-escaped quote
#: *inside* an argument - and no step use in the suite contains one.
CUKE_STR_PATTERN: Final[str] = r'[^"]*'

#: The function that implements it, named for the assertion messages.
CUKE_STR_CONVERTER_NAME: Final[str] = "parse_cuke_str"

#: The behave call that installs it.
REGISTER_TYPE_CALLABLE: Final[str] = "register_type"


class CukeStrUsage(NamedTuple):
    """A concrete ``User enters ...`` step line and the definition it must reach."""

    #: The step line as a real feature row renders it.
    phrase: str

    #: The pattern of the definition that must match it, and only it.
    pattern: str

    #: The step function's name - the Java method name verbatim.
    func_name: str

    #: The arguments the match must capture, unquoted, by name.
    kwargs: dict[str, str]

    #: The Java declaration this definition ports.
    java_location: str


#: The three near-collisions, with the values the real ``Examples`` rows of
#: ``Contact.feature:17`` and ``:36`` supply.  Each must reach its *intended*
#: definition and capture its intended argument names and values; the
#: three-way separation is the whole purpose of the field type.
CUKE_STR_USAGES: Final[tuple[CukeStrUsage, ...]] = (
    CukeStrUsage(
        phrase='User enters name "&Dustin"',
        pattern='User enters name "{name:CukeStr}"',
        func_name="user_enters_name",
        kwargs={"name": "&Dustin"},
        java_location="Contacts.java:29",
    ),
    CukeStrUsage(
        phrase='User enters "Haussman"',
        pattern='User enters "{street_name:CukeStr}"',
        func_name="user_enters",
        kwargs={"street_name": "Haussman"},
        java_location="Contacts.java:36",
    ),
    CukeStrUsage(
        phrase='User enters "+99999999999" and "abcd@info.com"',
        pattern='User enters "{phone_no:CukeStr}" and "{e_mail:CukeStr}"',
        func_name="user_enters_and",
        kwargs={"phone_no": "+99999999999", "e_mail": "abcd@info.com"},
        java_location="Contacts.java:41",
    ),
)

#: The counterfactual's two patterns: the same definition written with a plain
#: field and with the registered field type.  Built as throwaway matchers inside
#: the test, never registered.
COUNTERFACTUAL_PLAIN_PATTERN: Final[str] = 'User enters "{street_name}"'
COUNTERFACTUAL_TYPED_PATTERN: Final[str] = 'User enters "{street_name:CukeStr}"'

#: The two-argument step line the plain field wrongly swallows.
COUNTERFACTUAL_TWO_ARGUMENT_TEXT: Final[str] = 'User enters "555-5555" and "a@b.com"'

#: What the plain field captures from it - the tell-tale run-on value.
COUNTERFACTUAL_SWALLOWED_VALUE: Final[str] = '555-5555" and "a@b.com'

#: The one-argument step line both patterns must still match.
COUNTERFACTUAL_ONE_ARGUMENT_TEXT: Final[str] = 'User enters "Street 123"'

#: And the value both must capture from it.
COUNTERFACTUAL_ONE_ARGUMENT_VALUE: Final[str] = "Street 123"


# =========================================================================== #
# The closed import boundary (AAP 0.4.2)
#
# The measured, complete import surface of the ten modules.  Each entry is
# "<module>:<name>" for a `from ... import ...` and "<module>" for a plain
# `import ...`.
#
# Asserted as EQUALITY, not as a subset, because the boundary is closed: the
# step modules' own docstrings call the list "complete and closed" and name
# this test as what checks it.  A parity fix that legitimately needs another
# helper therefore updates this table in the same change - which is the point.
# The table is visible, and widening the boundary is a visible act.
# =========================================================================== #

EXPECTED_IMPORT_SURFACE: Final[dict[str, frozenset[str]]] = {
    # Calendar is the one module that imports the standard-library module
    # rather than the function: its two 3-second delays are written
    # ``time.sleep(3)`` (``Calendar.java:52``, ``:109``).
    "calendar_steps": frozenset(
        {
            "time",
            "behave:step",
            "app.automation:wait_visible_element",
            "app.pages:CalendarPage",
        }
    ),
    # Contacts is the only module that takes anything besides ``step`` from the
    # engine, and the only one that imports ``parse``: both serve the
    # ``CukeStr`` registration.  It imports nothing from the interactions
    # helpers - ``Contacts.java`` is the one step class importing neither the
    # keyboard-key class nor the action builder, and its five ``sendKeys``
    # calls are plain sends on a located element.
    "contacts_steps": frozenset(
        {
            "time:sleep",
            "behave:register_type",
            "behave:step",
            "parse:with_pattern",
            "app.automation:wait_visible_element",
            "app.pages:ContactsPage",
        }
    ),
    "crm_steps": frozenset(
        {
            "time:sleep",
            "behave:step",
            "app.automation:action_chain",
            "app.automation:press_keys",
            "app.automation:wait_visible_element",
            "app.pages:CrmPage",
        }
    ),
    # The only consumer of ``url`` and ``EmplTitle``
    # (``EmployeeStage.java:24``, ``:31``) and the only user of the title wait.
    "employee_steps": frozenset(
        {
            "time:sleep",
            "behave:step",
            "app.automation:wait_title_is",
            "app.automation:wait_visible_element",
            "app.config:get_empl_title",
            "app.config:get_url",
            "app.config:get_web_table_url",
            "app.pages:EmployeePage",
        }
    ),
    "inventory_steps": frozenset(
        {
            "behave:step",
            "app.automation:wait_visible_element",
            "app.pages:InventoryPage",
        }
    ),
    # The only module importing the locator-strategy constant, for the port of
    # ``LoginSD.java:56`` - a direct ``By.NAME("login")`` lookup rather than a
    # page-object accessor.
    "login_steps": frozenset(
        {
            "behave:step",
            "app.automation:By",
            "app.automation:wait_visible_element",
            "app.config:get_web_table_url",
            "app.pages:LoginPage",
        }
    ),
    "logout_steps": frozenset(
        {
            "behave:step",
            "app.automation:wait_visible_element",
            "app.pages:LogOutPage",
        }
    ),
    # Notes reaches two page objects: ``Notes.java:76`` drags an element from
    # the New column to Today using a locator declared on ``InventoryP``.
    "notes_steps": frozenset(
        {
            "time:sleep",
            "behave:step",
            "app.automation:action_chain",
            "app.automation:press_keys",
            "app.automation:wait_visible_element",
            "app.pages:InventoryPage",
            "app.pages:NotesPage",
        }
    ),
    "sales_steps": frozenset(
        {
            "behave:step",
            "app.automation:press_keys",
            "app.automation:wait_visible_element",
            "app.pages:SalesPage",
        }
    ),
    # The shared precondition reads all three credential properties
    # (``Session.java:14-17``) and constructs no wait.
    "session_steps": frozenset(
        {
            "behave:step",
            "app.config:get_password",
            "app.config:get_username",
            "app.config:get_web_table_url",
            "app.pages:SessionPage",
        }
    ),
}

#: Every top-level package or module a step module may import from.  Anything
#: else - and the automation library in particular - is a boundary violation.
ALLOWED_IMPORT_ROOTS: Final[frozenset[str]] = frozenset(
    {"time", "behave", "parse", "app"}
)

#: The browser-automation library.  Named here once, as data, and imported
#: nowhere in this file: ``app/automation`` is the only package in the port
#: permitted to import it (AAP 0.4.2), and this module is what enforces that
#: over ``features/steps/``.
FORBIDDEN_IMPORT_ROOT: Final[str] = "selenium"

#: The keyword-bound decorators deviation 7 forbids.  Importing one is as much
#: a violation as applying one, because an import is the only way to apply it.
FORBIDDEN_DECORATOR_NAMES: Final[frozenset[str]] = frozenset(
    {"given", "when", "then"}
)

#: The only registration decorator permitted.
REQUIRED_DECORATOR_NAME: Final[str] = "step"

#: Every decorator name the ten modules are allowed to apply.
#: ``parse.with_pattern`` is the second, and it decorates the ``CukeStr``
#: converter rather than a step.
ALLOWED_DECORATOR_NAMES: Final[frozenset[str]] = frozenset(
    {REQUIRED_DECORATOR_NAME, "with_pattern"}
)

#: ``app.automation`` publishes the whole automation surface as a package; the
#: step modules take it from there and never from a submodule, so that the
#: library import stays confined to three files.  These are the submodules a
#: step module must not name.
FORBIDDEN_APP_AUTOMATION_SUBMODULES: Final[tuple[str, ...]] = (
    "app.automation.driver",
    "app.automation.waits",
    "app.automation.interactions",
)


# =========================================================================== #
# Corpus construction
#
# Built at module import time, which is what lets the resolution tests be
# parametrised one case per phrase: a failure then names the phrase in its test
# id rather than burying it in an aggregate.  Parsing ten small feature files
# is cheap and, unlike loading the step modules, has no process-global effect -
# ``parse_file`` returns a fresh model and registers nothing.
# =========================================================================== #


class StepUsage(NamedTuple):
    """One step line as a feature file writes it, or as an ``Examples`` row renders it."""

    #: Feature file name, without the ``features/`` prefix.
    feature: str

    #: 1-based line number of the step line, for the failure message.
    line: int

    #: The keyword as written: ``Given``, ``When``, ``Then``, ``And`` or ``But``.
    keyword: str

    #: The *effective* step type the engine resolves under - ``given``, ``when``
    #: or ``then``.  An ``And`` inherits the type of the step above it.
    step_type: str

    #: The step text with the keyword stripped: what a definition matches.
    phrase: str

    @property
    def site(self) -> str:
        """``features/<file>:<line>`` - a location a reader can open."""
        return f"features/{self.feature}:{self.line}"

    def describe(self) -> str:
        """One line naming the site, the keyword and the phrase."""
        return f"{self.site}: {self.keyword} {self.phrase!r}"


def _feature_paths() -> tuple[Path, ...]:
    """The ten feature files, sorted by name - the engine's own discovery order.

    :returns: Absolute paths, sorted.
    """
    return tuple(sorted(FEATURES_DIR.glob(f"*{FEATURE_SUFFIX}")))


def _usages_of(steps: Any, feature_name: str) -> list[StepUsage]:
    """Convert a sequence of parsed steps into :class:`StepUsage` records.

    :param steps: Parsed step models, each carrying ``line``, ``keyword``,
        ``step_type`` and ``name``.
    :param feature_name: The file name to record against each usage.
    :returns: One record per step, in source order.
    """
    return [
        StepUsage(
            feature=feature_name,
            line=step.line,
            keyword=step.keyword,
            step_type=step.step_type,
            phrase=step.name,
        )
        for step in steps
    ]


def as_written_usages() -> tuple[StepUsage, ...]:
    """Every authored step line: each ``Background`` once, plus every scenario step.

    The corpus of what the feature files *say*, placeholders included.  A
    ``Background`` is counted once per feature rather than once per scenario,
    because it is authored once; counting it per scenario would inflate the
    total without adding a phrase, and skipping it would lose all nine ``Given``
    usages in the suite along with two definitions reached only from one.

    :returns: 158 records, in file-then-line order.
    """
    usages: list[StepUsage] = []

    for path in _feature_paths():
        feature = parse_file(str(path))

        if feature.background is not None:
            usages.extend(_usages_of(feature.background.steps, path.name))

        for scenario in feature.scenarios:
            usages.extend(_usages_of(scenario.steps, path.name))

    return tuple(usages)


def background_phrases(feature_name: str) -> tuple[str, ...]:
    """The phrases of one feature's ``Background``, in source order.

    Two features have no usable background: ``Session.feature`` declares none at
    all, and ``EmployeeFc.feature`` declares one that carries only a
    description and no steps.  Both yield an empty tuple, which is the whole
    reason the return type is a tuple rather than an optional model object.

    :param feature_name: Feature file name, such as ``Contact.feature``.
    :returns: The background's step phrases, or an empty tuple.
    """
    feature = parse_file(str(FEATURES_DIR / feature_name))

    if feature.background is None:
        return ()

    return tuple(step.name for step in feature.background.steps)


def expanded_usages() -> tuple[StepUsage, ...]:
    """Every step line a run would execute from a scenario, ``Examples`` rendered.

    ``walk_scenarios(with_outlines=True)`` yields each outline *and* the
    concrete scenarios its ``Examples`` rows generate, so a phrase written once
    with a placeholder appears here once per row with the value substituted.
    It walks scenarios only, so **no background step appears** - which is why
    this corpus cannot produce the 158 count and why
    :func:`as_written_usages` exists alongside it.

    :returns: 443 records, in file-then-scenario order.
    """
    usages: list[StepUsage] = []

    for path in _feature_paths():
        feature = parse_file(str(path))

        for scenario in feature.walk_scenarios(with_outlines=True):
            usages.extend(_usages_of(scenario.steps, path.name))

    return tuple(usages)


#: The authored corpus, built once.
AS_WRITTEN_USAGES: Final[tuple[StepUsage, ...]] = as_written_usages()

#: Its distinct phrases, sorted so parametrisation ids are stable.
AS_WRITTEN_PHRASES: Final[tuple[str, ...]] = tuple(
    sorted({usage.phrase for usage in AS_WRITTEN_USAGES})
)

#: The expanded corpus, built once.
EXPANDED_USAGES: Final[tuple[StepUsage, ...]] = expanded_usages()

#: Its distinct phrases, sorted.
EXPANDED_PHRASES: Final[tuple[str, ...]] = tuple(
    sorted({usage.phrase for usage in EXPANDED_USAGES})
)

#: The placeholder-bearing phrases - an outline template's own step lines,
#: which survive into both corpora.  Each must still resolve, capturing the
#: placeholder token itself as the argument value, because the engine matches
#: the template text as readily as a rendered row.
PLACEHOLDER_PHRASES: Final[tuple[str, ...]] = tuple(
    phrase for phrase in AS_WRITTEN_PHRASES if "<" in phrase
)

#: How many of them there are.
EXPECTED_PLACEHOLDER_PHRASES: Final[int] = 10

#: How many of the 23 placeholder-bearing *usages* survive into the expanded
#: corpus - the outline templates themselves, which ``walk_scenarios`` yields
#: alongside the scenarios their rows generate.
EXPECTED_TEMPLATE_USAGES: Final[int] = 23

#: Phrases that exist only after substitution: present in the expanded corpus
#: and absent from the authored one.  45 of the 52 come from ``Login.feature``'s
#: five outlines.
EXPECTED_RENDERED_ONLY_PHRASES: Final[int] = 52

#: Six of those 52, drawn from five different features so that a substitution
#: failure confined to one file still fails the assertion.  Each is chosen
#: precisely because it cannot be authored literally anywhere - which the test
#: re-checks, so a future edit that writes one of them out by hand is caught
#: rather than quietly weakening the check.
RENDERED_ONLY_SAMPLES: Final[tuple[str, ...]] = (
    'User enters name "&Dustin"',
    'User enters "+99999999999" and "abcd@info.com"',
    'User creates new employees "Lionel Messi" in the Employees stage',
    "User can change any user's information like \"Test2\" , \"30\" and \"2\"",
    'User enters "Test test" in the box and clicks the create button',
    'User enters "SaLeSMaNaGeR" password',
)


# =========================================================================== #
# Source inspection helpers
#
# Every one of these takes source TEXT rather than a path, which is what lets
# the synthetic-source tests at the end of Phase 3 feed them deliberately bad
# modules and prove they reject each.  None of them imports, execs or otherwise
# runs the source it is given.
# =========================================================================== #


class ImportedName(NamedTuple):
    """One name a module imports, and where it imports it."""

    #: The module it comes from.  For a plain ``import x`` this is ``"x"`` and
    #: :attr:`name` is empty; for ``from m import n`` it is ``"m"``.  A relative
    #: import is rendered with its leading dots.
    module: str

    #: The imported name, or ``""`` for a plain ``import``.
    name: str

    #: The local binding, which differs from :attr:`name` under ``as``.
    binding: str

    #: 1-based line of the import statement.
    lineno: int

    #: ``True`` when the statement sits inside a function or class body rather
    #: than at module scope - a hiding place a boundary check must still see.
    nested: bool

    @property
    def label(self) -> str:
        """``module:name``, or just ``module`` for a plain ``import``."""
        return f"{self.module}:{self.name}" if self.name else self.module

    @property
    def root(self) -> str:
        """The top-level package name, for the allowlist and the ban."""
        return self.module.lstrip(".").split(".")[0]

    def describe(self, label: str) -> str:
        """One line naming the module, the line and what was imported."""
        where = " (nested)" if self.nested else ""
        return f"{label}:{self.lineno}{where}: {self.label}"


def imported_names(source: str) -> tuple[ImportedName, ...]:
    """Every name *source* imports, at module scope or inside a body.

    The whole tree is walked rather than only its top level, deliberately: an
    import hidden inside a step body is exactly as much of a boundary violation
    as one at the top of the file, and is harder to spot by eye.
    :attr:`ImportedName.nested` records which it was so a failure message can
    say so.

    :param source: Python source text.
    :returns: One record per imported name, in line order.
    :raises SyntaxError: If *source* does not parse.
    """
    tree = ast.parse(source)
    top_level = {id(node) for node in tree.body}
    found: list[ImportedName] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append(
                    ImportedName(
                        module=alias.name,
                        name="",
                        binding=alias.asname or alias.name.split(".")[0],
                        lineno=node.lineno,
                        nested=id(node) not in top_level,
                    )
                )
        elif isinstance(node, ast.ImportFrom):
            module = "." * node.level + (node.module or "")

            for alias in node.names:
                found.append(
                    ImportedName(
                        module=module,
                        name=alias.name,
                        binding=alias.asname or alias.name,
                        lineno=node.lineno,
                        nested=id(node) not in top_level,
                    )
                )

    return tuple(sorted(found, key=lambda entry: (entry.lineno, entry.label)))


def forbidden_library_imports(source: str) -> tuple[ImportedName, ...]:
    """Every import of the browser-automation library in *source*.

    Matches on the top-level package, so ``import selenium``,
    ``import selenium.webdriver`` and
    ``from selenium.webdriver.common.keys import Keys`` are all caught, wherever
    they sit.

    :param source: Python source text.
    :returns: The offending imports; empty is the only acceptable result for a
        module under ``features/steps/``.
    """
    return tuple(
        entry for entry in imported_names(source) if entry.root == FORBIDDEN_IMPORT_ROOT
    )


def keyword_decorator_imports(source: str) -> tuple[ImportedName, ...]:
    """Every import of a keyword-bound step decorator in *source*.

    ``given``, ``when`` and ``then`` from the engine.  Deviation 7 forbids all
    three: bound to a keyword, a definition stops matching a use under another
    keyword, and 82 of the suite's 158 authored step lines are ``And``.

    :param source: Python source text.
    :returns: The offending imports; empty is the only acceptable result.
    """
    return tuple(
        entry
        for entry in imported_names(source)
        if entry.root == "behave" and entry.name in FORBIDDEN_DECORATOR_NAMES
    )


def submodule_imports(
    source: str, prefixes: tuple[str, ...]
) -> tuple[ImportedName, ...]:
    """Every import in *source* that reaches one of *prefixes*.

    Used for the ``app.automation`` submodules: the package publishes the whole
    helper surface, and taking a helper from the package rather than from the
    file it lives in is what keeps the library import confined to three files.

    Both routes to a submodule are caught, because a check that closed only one
    of them would be trivially worked around:

    * ``import app.automation.driver`` and
      ``from app.automation.driver import get_driver``, where the submodule is
      the statement's module; and
    * ``from app.automation import driver``, where it is the imported *name* -
      the package re-exports its submodules as attributes, so this reaches the
      same file by a different spelling.

    :param source: Python source text.
    :param prefixes: Fully qualified module names to reject, along with anything
        beneath them.
    :returns: The offending imports.
    """
    nested_prefixes = tuple(f"{prefix}." for prefix in prefixes)
    offenders: list[ImportedName] = []

    for entry in imported_names(source):
        candidates = [entry.module]

        if entry.name:
            candidates.append(f"{entry.module}.{entry.name}")

        if any(
            candidate in prefixes or candidate.startswith(nested_prefixes)
            for candidate in candidates
        ):
            offenders.append(entry)

    return tuple(offenders)


class Decoration(NamedTuple):
    """One decorator applied to a module-level function."""

    #: The decorated function's name.
    func_name: str

    #: The decorator's callable, rendered as source - ``step``,
    #: ``with_pattern``, or whatever a violation used.
    decorator: str

    #: 1-based line of the decorator itself, which is the line the engine
    #: reports as a matcher's location.
    lineno: int

    #: The sole string argument, when the decorator was called with exactly
    #: one; ``None`` otherwise.
    pattern: str | None


def decorations(source: str) -> tuple[Decoration, ...]:
    """Every decorator applied to a module-level function in *source*.

    The line recorded is the **decorator's**, not the ``def``'s, because that
    is the line the engine stores on a matcher; comparing the two directly is
    how :func:`test_source_decorations_match_the_registry` proves the registry
    holds exactly what the file declares.

    :param source: Python source text.
    :returns: One record per decorator, in line order.
    :raises SyntaxError: If *source* does not parse.
    """
    tree = ast.parse(source)
    found: list[Decoration] = []

    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        for decorator in node.decorator_list:
            call = decorator if isinstance(decorator, ast.Call) else None
            target = call.func if call is not None else decorator
            pattern: str | None = None

            if call is not None and len(call.args) == 1 and not call.keywords:
                argument = call.args[0]

                if isinstance(argument, ast.Constant) and isinstance(
                    argument.value, str
                ):
                    pattern = argument.value

            found.append(
                Decoration(
                    func_name=node.name,
                    decorator=ast.unparse(target),
                    lineno=decorator.lineno,
                    pattern=pattern,
                )
            )

    return tuple(found)


def module_scope_production_calls(source: str) -> tuple[tuple[int, str], ...]:
    """Calls to an ``app`` collaborator evaluated at *source*'s module scope.

    The check behind AAP 0.4.2's one deliberately preserved semantic
    difference: the Java classes build their page object and their
    ``WebDriverWait`` as **fields, at glue construction**, and the port must not.
    A module-scope construction in Python would bind whichever worker process
    imported the module first, so every page object comes from the behave
    context per scenario instead.

    What counts as module scope: every top-level statement, plus the
    *decorator expressions* of top-level functions, since those are evaluated
    at import too.  A function's body is not module scope and is not inspected
    here.  What counts as a production collaborator: any name bound by an
    import whose root package is ``app``.

    Inert module-level data is not flagged, and that is correct rather than
    lenient - ``calendar_steps``' month-name mapping and the three ``__all__``
    lists construct nothing and touch no driver.

    :param source: Python source text.
    :returns: ``(line, rendered call)`` for each offending call, in line order.
    :raises SyntaxError: If *source* does not parse.
    """
    tree = ast.parse(source)
    app_bindings = {
        entry.binding for entry in imported_names(source) if entry.root == "app"
    }
    found: list[tuple[int, str]] = []

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            scanned: list[ast.AST] = list(node.decorator_list)
        else:
            scanned = [node]

        for root in scanned:
            for inner in ast.walk(root):
                if not isinstance(inner, ast.Call):
                    continue

                callee = inner.func
                base = callee

                while isinstance(base, ast.Attribute):
                    base = base.value

                if isinstance(base, ast.Name) and base.id in app_bindings:
                    found.append((inner.lineno, ast.unparse(inner)))

    return tuple(sorted(found))


def module_scope_statements(source: str) -> tuple[tuple[int, str], ...]:
    """Top-level statements of *source* that are neither docstring, import nor ``def``.

    The narrow question ``contacts_steps``' docstring answers about itself -
    "the ``CukeStr`` registration is the only thing that happens at module
    scope" - asked of every module.

    :param source: Python source text.
    :returns: ``(line, rendered statement)`` for each, in source order.
    :raises SyntaxError: If *source* does not parse.
    """
    tree = ast.parse(source)
    found: list[tuple[int, str]] = []

    for index, node in enumerate(tree.body):
        if isinstance(
            node, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue

        if (
            index == 0
            and isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            # The module docstring.
            continue

        found.append((node.lineno, ast.unparse(node)))

    return tuple(found)


def read_step_module_source(module: str) -> str:
    """The source text of one step module, read from disk.

    Reading text rather than importing is the whole technique: the ten modules
    must not be imported by the unit suite, because the engine's loader *execs*
    them and a second registration of the same definitions raises
    ``AmbiguousStep``.

    :param module: Module name without its extension, such as ``login_steps``.
    :returns: The file's text.
    :raises FileNotFoundError: If the module is absent.
    """
    return (STEPS_DIR / f"{module}.py").read_text(encoding=SOURCE_ENCODING)


# =========================================================================== #
# Synthetic sources for the mutation-sensitivity proofs
#
# These strings are PARSED and never executed: no test imports them, execs
# them, writes them to disk or registers anything from them.  The phrase inside
# them is asserted to be absent from the real registry, which is the direct
# proof that feeding them to the helpers cannot contaminate it.
#
# They exist because the alternative - temporarily breaking a real step module
# to watch a test go red - edits a file this working tree needs clean and
# leaves no permanent evidence.  A committed rejection test is permanent
# evidence.
# =========================================================================== #

#: A phrase that appears only in the synthetic sources below.  If it ever
#: resolved, a synthetic module would have reached the registry.
SYNTHETIC_PHRASE: Final[str] = "Synthetic user does something for the boundary tests"

#: A clean module: the positive control.  Every detector must accept it, so
#: that a detector which rejects everything cannot masquerade as a working one.
GOOD_SYNTHETIC_SOURCE: Final[str] = f'''"""A synthetic step module that honours every boundary."""

from time import sleep

from behave import step

from app.automation import wait_visible_element
from app.pages import LoginPage


def _page(context) -> LoginPage:
    """Bind a page object per scenario, never at module scope."""
    return LoginPage(context.driver)


@step("{SYNTHETIC_PHRASE}")
def synthetic_user_does_something(context) -> None:
    """A body that performs operations, none of them at import time."""
    page = _page(context)
    sleep(3)
    wait_visible_element(page.input_email, 3)
'''

#: Violation 1: a keyword-bound decorator, imported and applied.
BAD_KEYWORD_DECORATOR_SOURCE: Final[str] = f'''
"""A synthetic module that binds a definition to a keyword."""

from behave import given


@given("{SYNTHETIC_PHRASE}")
def synthetic_user_does_something(context) -> None:
    """Registered under ``given``, so a ``When`` use of it goes undefined."""
'''

#: Violation 2: the automation library imported outright.
BAD_LIBRARY_IMPORT_SOURCE: Final[str] = f'''
"""A synthetic module that imports the automation library directly."""

import selenium

from behave import step


@step("{SYNTHETIC_PHRASE}")
def synthetic_user_does_something(context) -> None:
    """The library must be reached only through ``app.automation``."""
    assert selenium is not None
'''

#: Violation 3: the same library reached through a submodule, which a check
#: matching only the exact name ``selenium`` would miss.
BAD_LIBRARY_SUBMODULE_SOURCE: Final[str] = f'''
"""A synthetic module that imports a submodule of the automation library."""

from behave import step
from selenium.webdriver.common.keys import Keys


@step("{SYNTHETIC_PHRASE}")
def synthetic_user_does_something(context) -> None:
    """``app.automation.press_keys`` is the only permitted route to the keys."""
    assert Keys.ENTER
'''

#: Violation 4: the same import hidden inside a body, which a check reading
#: only a module's top-level statements would miss.
BAD_NESTED_LIBRARY_IMPORT_SOURCE: Final[str] = f'''
"""A synthetic module that hides the library import inside a step body."""

from behave import step


@step("{SYNTHETIC_PHRASE}")
def synthetic_user_does_something(context) -> None:
    """A nested import is still an import."""
    from selenium.webdriver.common.by import By

    context.driver.find_element(By.ID, "anything")
'''

#: Violation 5: the automation helpers reached through a submodule instead of
#: through the package, by both available spellings.
BAD_AUTOMATION_SUBMODULE_SOURCE: Final[str] = f'''
"""A synthetic module that reaches past the app.automation package."""

from behave import step

from app.automation import driver
from app.automation.waits import wait_visible_element


@step("{SYNTHETIC_PHRASE}")
def synthetic_user_does_something(context) -> None:
    """Both imports bypass the package that owns the automation surface."""
    wait_visible_element(driver.get_driver(), 3)
'''

#: Violation 6: a page object constructed at module scope, which would bind
#: whichever worker imported the module first.
BAD_MODULE_SCOPE_PAGE_SOURCE: Final[str] = f'''
"""A synthetic module that constructs a page object at import time."""

from behave import step

from app.pages import LoginPage

PAGE = LoginPage(None)


@step("{SYNTHETIC_PHRASE}")
def synthetic_user_does_something(context) -> None:
    """Uses the module-scope page, which is the defect."""
    PAGE.input_email.click()
'''


# =========================================================================== #
# Fixtures
#
# All three are session-scoped, so the registry is inventoried once and the ten
# step modules are read from disk once for the whole session.  The first two
# depend on ``conftest``'s ``step_registry``, the session fixture that performs
# the single permitted load; the third reads text and needs no registry at all.
# =========================================================================== #


@pytest.fixture(scope="session")
def definitions(step_registry: Any) -> tuple[StepMatch, ...]:
    """Every registered definition, as a light record per matcher.

    Reuses :class:`conftest.StepMatch` so that this module and the ten parity
    modules describe a definition the same way.  The argument fields are empty
    here - a definition is being *inventoried*, not matched against a phrase -
    and :attr:`~conftest.StepMatch.phrase` therefore carries the pattern.

    :param step_registry: The loaded registry.
    :returns: One record per matcher across all four buckets, bucket by bucket.
    """
    found: list[StepMatch] = []

    for bucket in STEP_BUCKETS:
        for matcher in step_registry.steps.get(bucket, ()):
            location = matcher.location
            filename = str(getattr(location, "filename", "") or "")

            found.append(
                StepMatch(
                    phrase=str(matcher.pattern),
                    func=matcher.func,
                    args=(),
                    kwargs={},
                    pattern=str(matcher.pattern),
                    location=str(location),
                    module_name=Path(filename).stem,
                    bucket=bucket,
                )
            )

    return tuple(found)


@pytest.fixture(scope="session")
def definitions_by_module(
    definitions: tuple[StepMatch, ...],
) -> dict[str, tuple[StepMatch, ...]]:
    """The definitions grouped by the step module that declares them.

    Grouping is by ``location.filename``, because the loaded functions carry no
    usable ``__module__``: the engine's loader execs the files rather than
    importing them.

    :param definitions: Every registered definition.
    :returns: Module name to its definitions, in registration order.
    """
    grouped: dict[str, list[StepMatch]] = collections.defaultdict(list)

    for definition in definitions:
        grouped[definition.module_name].append(definition)

    return {module: tuple(items) for module, items in grouped.items()}


@pytest.fixture(scope="session")
def step_module_sources() -> dict[str, str]:
    """The source text of all ten step modules, read once.

    :returns: Module name to source text.
    """
    return {module: read_step_module_source(module) for module in STEP_MODULE_NAMES}


# =========================================================================== #
# PHASE 0 - The mechanism
#
# Before any contract can be asserted, the registry has to be the real one,
# loaded once, holding definitions that belong to the ten expected modules.
# =========================================================================== #


def test_registry_is_loaded_exactly_once_per_process(step_registry: Any) -> None:
    """A second load must return the same registry object, not a second load.

    This is not an optimisation.  behave's ``StepRegistry`` is process-wide and
    a second registration of a *changed* definition raises ``AmbiguousStep``,
    so the caching in ``conftest.load_step_registry`` is what makes eleven
    step-related test modules able to share one registry.
    """
    assert load_step_registry() is step_registry, (
        "conftest.load_step_registry() returned a different object from the "
        "session fixture: the step modules were loaded twice, which risks "
        "AmbiguousStep and doubles every count in this module"
    )


def test_registry_holds_the_ninety_one_java_definitions(
    definitions: tuple[StepMatch, ...],
) -> None:
    """The registry holds exactly 91 definitions - the Java per-class total.

    13 + 14 + 12 + 12 + 9 + 9 + 3 + 11 + 7 + 1, from the annotation
    declarations of the ten step classes at the pinned revision.
    """
    assert len(definitions) == EXPECTED_DEFINITION_COUNT, (
        f"expected {EXPECTED_DEFINITION_COUNT} step definitions across "
        f"{len(STEP_MODULE_NAMES)} modules, found {len(definitions)}: "
        + ", ".join(
            f"{definition.module_name} {definition.pattern!r}"
            for definition in definitions
        )
    )


def test_every_definition_registers_in_the_step_bucket(
    definitions: tuple[StepMatch, ...],
) -> None:
    """All 91 land in the keyword-agnostic bucket.

    The positive half of the ``@step``-only assertion.  A definition in any
    other bucket was registered with ``@given``, ``@when`` or ``@then`` and
    would stop matching a use under another keyword.
    """
    misplaced = tuple(
        definition for definition in definitions if definition.bucket != STEP_BUCKET
    )

    assert not misplaced, (
        "every definition must register with @step (AAP deviation 7); these "
        "did not: "
        + ", ".join(
            f"{definition.pattern!r} at {definition.location} "
            f"in bucket {definition.bucket!r}"
            for definition in misplaced
        )
    )


@pytest.mark.parametrize(
    "bucket", [bucket for bucket in STEP_BUCKETS if bucket != STEP_BUCKET]
)
def test_keyword_bucket_is_empty(step_registry: Any, bucket: str) -> None:
    """The ``given``, ``when`` and ``then`` buckets hold nothing at all.

    The strongest available form of the ``@step``-only assertion, and the one
    that fails loudest: a single ``@given`` anywhere under ``features/steps/``
    puts a matcher in one of these three and this test names it.
    """
    matchers = tuple(step_registry.steps.get(bucket, ()))

    assert not matchers, (
        f"the {bucket!r} bucket must be empty - AAP deviation 7 registers every "
        f"definition with @step - but it holds {len(matchers)}: "
        + ", ".join(f"{matcher.pattern!r} at {matcher.location}" for matcher in matchers)
    )


def test_no_bucket_outside_the_four_is_populated(step_registry: Any) -> None:
    """No fifth bucket carries definitions.

    ``conftest.find_all_step_matches`` scans exactly four buckets, so a
    definition registered anywhere else would be invisible to every resolution
    test in this file.  This closes that gap explicitly rather than trusting the
    engine's bucket set not to grow.
    """
    unexpected = {
        bucket: len(matchers)
        for bucket, matchers in step_registry.steps.items()
        if bucket not in STEP_BUCKETS and matchers
    }

    assert not unexpected, (
        f"definitions registered in buckets conftest does not scan: {unexpected}. "
        f"conftest.STEP_BUCKETS is {STEP_BUCKETS} and every resolution "
        f"assertion in this module is blind to anything outside it"
    )


def test_step_directory_holds_exactly_the_ten_modules() -> None:
    """``features/steps/`` holds the ten step modules and nothing else.

    No package marker, no ``conftest.py``, no shared helper module: the AAP
    0.3.1 target tree lists exactly ten files here.  The absence of
    ``__init__.py`` is load-bearing rather than incidental - the engine's loader
    execs these files, and making them importable as a package invites the
    double registration that raises ``AmbiguousStep``.
    """
    found = sorted(path.name for path in STEPS_DIR.glob("*.py"))
    expected = sorted(f"{module}.py" for module in STEP_MODULE_NAMES)

    assert found == expected, (
        f"{STEPS_DIR} must hold exactly the ten step modules; found {found}"
    )
    assert not (STEPS_DIR / "__init__.py").exists(), (
        "features/steps/__init__.py must not exist: the engine execs these "
        "files, and a package marker invites importing them a second time"
    )
    assert not (FEATURES_DIR / "__init__.py").exists(), (
        "features/__init__.py must not exist, for the same reason"
    )


def test_every_definition_belongs_to_one_of_the_ten_modules(
    definitions: tuple[StepMatch, ...],
) -> None:
    """No definition comes from a file outside the ten.

    ``features/environment.py`` in particular declares hooks and no steps: it
    ports ``Hooks.java``, which declares no annotation, and it is not under
    ``features/steps/`` so the loader never reaches it.
    """
    owners = {definition.module_name for definition in definitions}
    unexpected = sorted(owners - set(STEP_MODULE_NAMES))

    assert not unexpected, (
        f"definitions registered from unexpected files: {unexpected}. "
        f"Only {list(STEP_MODULE_NAMES)} may declare steps"
    )


@pytest.mark.parametrize("census", JAVA_STEP_CENSUS, ids=lambda row: row.module)
def test_definitions_per_module_match_the_java_class_census(
    definitions_by_module: dict[str, tuple[StepMatch, ...]],
    census: StepClassCensus,
) -> None:
    """Each module registers exactly as many definitions as its Java class declares.

    The per-class census the review findings name, asserted one class at a
    time so a failure says *which* class drifted.  ``Contacts.java`` is the one
    to watch: it declares fourteen live annotations plus a fifteenth that is
    commented out, so a port that read the comment would land on 15 here.
    """
    registered = definitions_by_module.get(census.module, ())

    assert len(registered) == census.definitions, (
        f"{census.module} must register {census.definitions} definitions - the "
        f"number {census.java_class} declares - but registers "
        f"{len(registered)}: "
        + ", ".join(f"{item.pattern!r} at {item.location}" for item in registered)
    )


def test_definition_patterns_are_unique(definitions: tuple[StepMatch, ...]) -> None:
    """No two definitions carry the same pattern.

    An identical pattern registered twice is the one duplication behave itself
    catches - ``same_step_definition`` tolerates only a byte-identical
    re-registration of the same function - so a duplicate here would mean two
    different functions claiming one phrase.
    """
    counts = collections.Counter(definition.pattern for definition in definitions)
    duplicated = {pattern: count for pattern, count in counts.items() if count > 1}

    assert not duplicated, f"patterns registered more than once: {duplicated}"


def test_definition_function_names_are_unique(
    definitions: tuple[StepMatch, ...],
) -> None:
    """Each of the 91 step functions has its own name.

    The names are the Java method names verbatim, which is also what
    ``target/cucumber.json`` reports as a step's ``match.location`` (AAP
    deviation 8).  Two definitions sharing a name would make that field
    ambiguous in the artifact a downstream publisher reads.
    """
    counts = collections.Counter(definition.func.__name__ for definition in definitions)
    duplicated = {name: count for name, count in counts.items() if count > 1}

    assert not duplicated, f"step function names used more than once: {duplicated}"


@pytest.mark.parametrize("module", STEP_MODULE_NAMES)
def test_source_decorations_match_the_registry(
    definitions_by_module: dict[str, tuple[StepMatch, ...]],
    step_module_sources: dict[str, str],
    module: str,
) -> None:
    """What the file declares and what the registry holds are the same set.

    Compared as ``(decorator line, pattern, function name)`` triples.  The
    engine records a matcher's location at the **decorator's** line rather than
    the ``def``'s, which is why :func:`decorations` reports the decorator's.

    This is the link that makes the source-inspection tests in Phase 3 binding
    on the *registry*: the file is the registry's only input, and here they are
    shown to agree exactly.
    """
    from_source = sorted(
        (decoration.lineno, decoration.pattern, decoration.func_name)
        for decoration in decorations(step_module_sources[module])
        if decoration.decorator == REQUIRED_DECORATOR_NAME
    )
    from_registry = sorted(
        (
            int(str(definition.location).rsplit(":", 1)[1]),
            definition.pattern,
            definition.func.__name__,
        )
        for definition in definitions_by_module.get(module, ())
    )

    assert from_source == from_registry, (
        f"{module}.py declares @step decorations that the registry does not "
        f"hold, or vice versa.\n  source:   {from_source}\n  registry: "
        f"{from_registry}"
    )


# =========================================================================== #
# PHASE 1 - DIRECTION 1
#
# Every step phrase across all ten feature files resolves to exactly one
# implementation.
# =========================================================================== #


def test_as_written_corpus_has_the_expected_size() -> None:
    """158 authored step lines carrying 93 distinct phrases.

    The corpus every DIRECTION 1 assertion below is measured over.  Both
    numbers are re-derived from the engine's parser on every run; a scenario
    added, removed or re-worded moves one of them.
    """
    assert len(AS_WRITTEN_USAGES) == EXPECTED_AS_WRITTEN_USAGES, (
        f"expected {EXPECTED_AS_WRITTEN_USAGES} as-written step usages "
        f"(each Background once, plus every scenario step), found "
        f"{len(AS_WRITTEN_USAGES)}"
    )
    assert len(AS_WRITTEN_PHRASES) == EXPECTED_AS_WRITTEN_PHRASES, (
        f"expected {EXPECTED_AS_WRITTEN_PHRASES} distinct as-written phrases, "
        f"found {len(AS_WRITTEN_PHRASES)}"
    )


def test_every_feature_file_is_present_and_parsed() -> None:
    """The ten feature files, by the names the reference checkout used.

    AAP deviation 1 moved the directory and nothing else: filenames, bytes,
    line endings, tags, descriptions, comments and ``Examples`` data are
    unchanged.
    """
    found = tuple(path.name for path in _feature_paths())

    assert found == FEATURE_FILENAMES, (
        f"features/ must hold exactly {list(FEATURE_FILENAMES)}; found "
        f"{list(found)}"
    )


@pytest.mark.parametrize("feature", FEATURE_FILENAMES)
def test_as_written_usages_per_feature(feature: str) -> None:
    """Each feature contributes its measured number of authored step lines.

    Per-feature rather than in aggregate so that a failure localises to one
    file.  ``Session.feature`` contributes one - it is three lines long and
    exists to declare the shared precondition.
    """
    found = sum(1 for usage in AS_WRITTEN_USAGES if usage.feature == feature)

    assert found == EXPECTED_USAGES_PER_FEATURE[feature], (
        f"features/{feature} must contribute "
        f"{EXPECTED_USAGES_PER_FEATURE[feature]} as-written step usages, "
        f"found {found}"
    )


def test_per_feature_usages_sum_to_the_total() -> None:
    """The per-feature table is complete and consistent with the total.

    Guards the table itself: a feature omitted from
    :data:`EXPECTED_USAGES_PER_FEATURE` would leave every other assertion
    passing while the total quietly stopped being checked.
    """
    assert sorted(EXPECTED_USAGES_PER_FEATURE) == sorted(FEATURE_FILENAMES)
    assert (
        sum(EXPECTED_USAGES_PER_FEATURE.values()) == EXPECTED_AS_WRITTEN_USAGES
    ), "the per-feature usage table must sum to 158"


@pytest.mark.parametrize("phrase", AS_WRITTEN_PHRASES)
def test_every_distinct_phrase_resolves_to_exactly_one_definition(
    step_registry: Any, phrase: str
) -> None:
    """Each of the 93 authored phrases reaches exactly one implementation.

    DIRECTION 1 and DIRECTION 2 together, one phrase per test case so that the
    failing phrase appears in the test id.  All four buckets are scanned, so a
    definition accidentally bound to a keyword is *found and named* rather than
    silently missed.
    """
    matches = find_all_step_matches(phrase)
    sites = [usage.site for usage in AS_WRITTEN_USAGES if usage.phrase == phrase]

    assert matches, (
        f"no step definition matches {phrase!r}, used at {', '.join(sites)}. "
        f"Every phrase in features/*.feature must resolve to exactly one "
        f"implementation under {STEPS_DIR}"
    )
    assert len(matches) == 1, (
        f"{len(matches)} definitions match {phrase!r}, used at "
        f"{', '.join(sites)}: "
        + ", ".join(f"{match.pattern!r} at {match.location}" for match in matches)
    )


def test_no_as_written_usage_is_undefined(step_registry: Any) -> None:
    """Aggregate over all 158 usages: nothing is undefined.

    The parametrised test above covers the same ground per phrase; this one
    reports **every** failing site in a single message, naming the feature file,
    the line and the phrase, which is what a fix needs when several break at
    once.
    """
    undefined: list[str] = []

    for usage in AS_WRITTEN_USAGES:
        try:
            matches = find_all_step_matches(usage.phrase)
        except AssertionError as error:  # a type converter failed on this phrase
            undefined.append(f"{usage.describe()} -> {error}")
            continue

        if not matches:
            undefined.append(usage.describe())

    assert not undefined, (
        f"{len(undefined)} of {len(AS_WRITTEN_USAGES)} authored step lines do "
        f"not resolve:\n  " + "\n  ".join(undefined)
    )


@pytest.mark.parametrize("phrase", PLACEHOLDER_PHRASES)
def test_outline_template_phrase_resolves_with_its_placeholder(
    step_registry: Any, phrase: str
) -> None:
    """An outline's own step line resolves, placeholder token and all.

    A ``Scenario Outline`` step is authored as ``"<username>"`` and the engine
    matches that text as readily as it matches a rendered row, capturing the
    token itself as the argument.  Asserting it matters because both the
    ``--dry-run`` artifact pass and the shard planner resolve template text.
    """
    match = find_all_step_matches(phrase)

    assert len(match) == 1, (
        f"outline template phrase {phrase!r} must resolve to exactly one "
        f"definition, got {len(match)}"
    )

    captured = tuple(match[0].kwargs.values())

    assert captured, (
        f"outline template phrase {phrase!r} resolved to "
        f"{match[0].pattern!r} but captured no argument, so the placeholder "
        f"was matched as literal text"
    )
    assert all(value.startswith("<") and value.endswith(">") for value in captured), (
        f"outline template phrase {phrase!r} captured {captured}, which should "
        f"be the placeholder tokens themselves"
    )


def test_the_placeholder_phrase_count_is_the_measured_ten() -> None:
    """Ten of the 93 authored phrases carry a placeholder.

    Pins the size of the parametrised set above, so that a phrase silently
    dropping its placeholder cannot shrink that set unnoticed.
    """
    assert len(PLACEHOLDER_PHRASES) == EXPECTED_PLACEHOLDER_PHRASES, (
        f"expected {EXPECTED_PLACEHOLDER_PHRASES} placeholder-bearing phrases, "
        f"found {len(PLACEHOLDER_PHRASES)}: {list(PLACEHOLDER_PHRASES)}"
    )


def test_expanded_corpus_has_the_expected_size() -> None:
    """443 usages and 143 distinct phrases once ``Examples`` rows are rendered.

    The outline-expansion checkbox.  Nearly three times the authored count,
    because ``Login.feature`` alone renders 203 step lines from five outlines -
    the valid-login outline's two blocks supply 13 and 15 rows.
    """
    assert len(EXPANDED_USAGES) == EXPECTED_EXPANDED_USAGES, (
        f"expected {EXPECTED_EXPANDED_USAGES} expanded step usages, found "
        f"{len(EXPANDED_USAGES)}"
    )
    assert len(EXPANDED_PHRASES) == EXPECTED_EXPANDED_PHRASES, (
        f"expected {EXPECTED_EXPANDED_PHRASES} distinct expanded phrases, "
        f"found {len(EXPANDED_PHRASES)}"
    )


def test_expanded_corpus_omits_backgrounds() -> None:
    """The expanded corpus carries no ``Given`` - because it carries no background.

    The measured reason the two corpora cannot be substituted for one another.
    All nine ``Given`` usages in the suite are background lines, so walking
    scenarios alone loses every one of them; a census taken from this corpus
    would also declare ``User is at Contact dashboard`` and ``User is on the
    upgenix login page`` unexercised, which they are not.
    """
    keywords = {usage.keyword for usage in EXPANDED_USAGES}

    assert "Given" not in keywords, (
        "walk_scenarios() unexpectedly yielded a Given usage; the 158-usage "
        "corpus and this one would then no longer differ in the documented way"
    )
    assert EXPECTED_KEYWORD_CENSUS["Given"] == sum(
        1 for usage in AS_WRITTEN_USAGES if usage.keyword == "Given"
    ), "all nine Given usages are authored in a Background"


def test_no_expanded_usage_is_undefined(step_registry: Any) -> None:
    """Every rendered ``Examples`` row resolves too.

    A row can break resolution where the template did not: the value it
    substitutes becomes part of the step text, so a value containing a quote or
    a comma changes what the pattern sees.  ``Login.feature``'s rows include
    ``posFkc@ma#$`` and ``SaLeSMaNaGeR``, and ``Contact.feature``'s include
    ``&Dustin``.
    """
    undefined: list[str] = []

    for usage in EXPANDED_USAGES:
        try:
            matches = find_all_step_matches(usage.phrase)
        except AssertionError as error:
            undefined.append(f"{usage.describe()} -> {error}")
            continue

        if not matches:
            undefined.append(usage.describe())

    assert not undefined, (
        f"{len(undefined)} of {len(EXPANDED_USAGES)} rendered step lines do not "
        f"resolve:\n  " + "\n  ".join(undefined)
    )


def test_ninety_of_the_ninety_one_definitions_are_exercised(
    step_registry: Any, definitions: tuple[StepMatch, ...]
) -> None:
    """Exactly one definition is never reached by any authored step line.

    Measured over the **authored** corpus, which is the one that includes
    backgrounds: ``User is at Contact dashboard`` and ``User is on the upgenix
    login page`` are reached only from a ``Background``, so an enumeration that
    skipped those would wrongly report three unexercised definitions instead of
    one.

    The one that is genuinely unreached is ``employee_steps``' port of
    ``EmployeeStage.java:16``.  It is source state preserved under AAP 0.8
    "preserve, do not tidy" - not a defect, and not to be deleted.
    """
    exercised: set[tuple[str, str]] = set()

    for phrase in AS_WRITTEN_PHRASES:
        for match in find_all_step_matches(phrase):
            exercised.add((match.module_name, match.pattern))

    declared = {
        (definition.module_name, definition.pattern) for definition in definitions
    }
    unexercised = tuple(sorted(declared - exercised))

    assert unexercised == EXPECTED_UNEXERCISED_DEFINITIONS, (
        f"expected exactly {len(EXPECTED_UNEXERCISED_DEFINITIONS)} unexercised "
        f"definition(s), {list(EXPECTED_UNEXERCISED_DEFINITIONS)}, found "
        f"{list(unexercised)}"
    )
    assert (
        len(declared) - len(unexercised) == EXPECTED_DEFINITION_COUNT - 1
    ), "90 of the 91 definitions must be reached by an authored step line"


# =========================================================================== #
# PHASE 2 - DIRECTION 2
#
# No phrase resolves to more than one implementation.  This is the risk @step
# introduces, and the port already carries one fix for it.
# =========================================================================== #


def test_no_authored_phrase_is_ambiguous(step_registry: Any) -> None:
    """Aggregate: not one of the 158 authored lines matches two definitions.

    The message lists **every** location of **every** ambiguous phrase, because
    an ambiguity is fixed by looking at the overlapping patterns together.
    """
    ambiguous: list[str] = []

    for usage in AS_WRITTEN_USAGES:
        matches = find_all_step_matches(usage.phrase)

        if len(matches) > 1:
            locations = ", ".join(
                f"{match.pattern!r} at {match.location}" for match in matches
            )
            ambiguous.append(f"{usage.describe()} -> {len(matches)}: {locations}")

    assert not ambiguous, (
        "@step reaches every keyword, so two overlapping patterns can both "
        f"match one phrase. {len(ambiguous)} authored step lines are "
        f"ambiguous:\n  " + "\n  ".join(ambiguous)
    )


def test_no_rendered_phrase_is_ambiguous(step_registry: Any) -> None:
    """Nor does any of the 443 rendered lines.

    The corpus that matters most for ambiguity: the Contacts collision only
    appears once a row supplies two quoted values, so a check over template
    text alone would have missed the defect the ``CukeStr`` type fixes.
    """
    ambiguous: list[str] = []

    for usage in EXPANDED_USAGES:
        matches = find_all_step_matches(usage.phrase)

        if len(matches) > 1:
            locations = ", ".join(
                f"{match.pattern!r} at {match.location}" for match in matches
            )
            ambiguous.append(f"{usage.describe()} -> {len(matches)}: {locations}")

    assert not ambiguous, (
        f"{len(ambiguous)} rendered step lines are ambiguous:\n  "
        + "\n  ".join(ambiguous)
    )


def test_commented_out_java_step_is_not_registered(step_registry: Any) -> None:
    """The commented-out ``Contacts.java:90`` annotation has no port.

    ``//    @When("User clicks and goes directly to the profile")`` with an
    empty body, matched by the two commented-out step lines at
    ``Contact.feature:22-23``.  AAP 0.2.2 preserves the source's
    inconsistencies rather than tidying them, so Contacts has fourteen
    definitions and no fifteenth.
    """
    matches = find_all_step_matches(COMMENTED_OUT_JAVA_PHRASE)

    assert matches == (), (
        f"{COMMENTED_OUT_JAVA_PHRASE!r} is commented out at Contacts.java:90 "
        f"and must not be registered, but resolves to: "
        + ", ".join(f"{match.pattern!r} at {match.location}" for match in matches)
    )


def test_cuke_str_field_type_is_registered(step_registry: Any) -> None:
    """The quote-excluding field type is installed, with its measured regex.

    ``register_type(CukeStr=parse_cuke_str)`` puts the converter in the parse
    matcher's type registry; ``parse.with_pattern`` is what attaches the regex
    the matcher then compiles into each pattern using it.
    """
    assert ParseMatcher.TYPE_REGISTRY.has_type(CUKE_STR_TYPE_NAME), (
        f"{CUKE_STR_TYPE_NAME!r} is not registered; the three Contacts "
        f"'User enters ...' patterns cannot compile without it"
    )

    converter = ParseMatcher.TYPE_REGISTRY[CUKE_STR_TYPE_NAME]

    assert converter.__name__ == CUKE_STR_CONVERTER_NAME
    assert getattr(converter, "pattern", None) == CUKE_STR_PATTERN, (
        f"the {CUKE_STR_TYPE_NAME} converter must carry the quote-excluding "
        f"pattern {CUKE_STR_PATTERN!r}; a pattern that admits a quote would "
        f"reintroduce the collision it exists to prevent"
    )
    assert converter('Street "1"') == 'Street "1"', (
        "the converter is the identity: Cucumber passes {string} through "
        "unconverted and so must this"
    )
    assert converter("") == "", "the empty string is accepted, as Cucumber's is"


def test_cuke_str_is_registered_at_module_scope_above_the_decorators(
    step_module_sources: dict[str, str],
) -> None:
    """The registration runs at import, before the first ``@step`` is evaluated.

    Load-bearing and easy to break by moving one line: the parse matcher
    compiles a pattern at **decoration** time, so a registration that ran later
    - in ``before_all``, say - would leave the three patterns already compiled
    with a plain field and change nothing at all.  Position is therefore part of
    the contract, and this asserts it textually: the call is a top-level
    statement, and its line precedes every ``@step`` line in the file.
    """
    source = step_module_sources["contacts_steps"]
    statements = module_scope_statements(source)
    registrations = tuple(
        (line, rendered)
        for line, rendered in statements
        if rendered.startswith(f"{REGISTER_TYPE_CALLABLE}(")
    )

    assert len(registrations) == 1, (
        f"contacts_steps.py must call {REGISTER_TYPE_CALLABLE} exactly once at "
        f"module scope; found {list(registrations)}"
    )
    assert CUKE_STR_TYPE_NAME in registrations[0][1], (
        f"the module-scope registration must install {CUKE_STR_TYPE_NAME}: "
        f"{registrations[0][1]!r}"
    )

    first_step_line = min(
        decoration.lineno
        for decoration in decorations(source)
        if decoration.decorator == REQUIRED_DECORATOR_NAME
    )

    assert registrations[0][0] < first_step_line, (
        f"{REGISTER_TYPE_CALLABLE} runs at line {registrations[0][0]} but the "
        f"first @step decorator is at line {first_step_line}; the parse matcher "
        f"compiles patterns at decoration time, so the registration must come "
        f"first"
    )


def test_contacts_module_scope_holds_only_that_registration(
    step_module_sources: dict[str, str],
) -> None:
    """Nothing else happens when ``contacts_steps`` is imported.

    The module's own docstring states it: "the ``CukeStr`` registration is the
    only thing that happens at module scope".  No page instance, no driver
    reference, no wait object.
    """
    statements = module_scope_statements(step_module_sources["contacts_steps"])

    assert len(statements) == 1, (
        f"contacts_steps.py must carry exactly one module-scope statement, the "
        f"{REGISTER_TYPE_CALLABLE} call; found {list(statements)}"
    )


@pytest.mark.parametrize("usage", CUKE_STR_USAGES, ids=lambda case: case.java_location)
def test_cuke_str_near_collision_resolves_to_the_intended_definition(
    step_registry: Any, usage: CukeStrUsage
) -> None:
    """Each concrete ``User enters ...`` line reaches its own definition.

    Three patterns, all beginning ``User enters``, two of them differing only in
    how many quoted fields follow.  Without the field type the one-argument
    pattern also matches the two-argument line - which is measured directly by
    :func:`test_plain_field_would_make_the_contacts_patterns_ambiguous`.

    The captured values are asserted too, unquoted and by name: the literal
    quotes live in the pattern and the converter is the identity, so a step body
    receives exactly what Cucumber passed it.
    """
    matches = find_all_step_matches(usage.phrase)

    assert len(matches) == 1, (
        f"{usage.phrase!r} (from {usage.java_location}) must resolve to exactly "
        f"one definition, got {len(matches)}: "
        + ", ".join(f"{match.pattern!r} at {match.location}" for match in matches)
    )

    match = matches[0]

    assert match.pattern == usage.pattern, (
        f"{usage.phrase!r} resolved to {match.pattern!r}, expected "
        f"{usage.pattern!r}"
    )
    assert match.func.__name__ == usage.func_name
    assert match.module_name == "contacts_steps"
    assert match.kwargs == usage.kwargs, (
        f"{usage.phrase!r} captured {match.kwargs}, expected {usage.kwargs}"
    )
    assert match.args == (), (
        "every parameter in this port is written {name} or {name:CukeStr}, so "
        "a match must carry named arguments only"
    )


def test_plain_field_would_make_the_contacts_patterns_ambiguous(
    step_registry: Any,
) -> None:
    """The counterfactual: why the field type is load-bearing, measured not asserted.

    Two throwaway matchers are built here - the street-name pattern written with
    a plain field, and the same pattern written with the registered type - and
    the plain one is shown to match a **two**-argument step line, swallowing
    ``555-5555" and "a@b.com`` into one field.  Two definitions matching one
    line is an ``AmbiguousStep`` error at runtime, or first-registered-wins
    according to module load order.

    Neither matcher is registered, and the closing assertion proves it against
    a count snapshotted at the top of the test.  Proving the ambiguity this way,
    rather than by registering a duplicate, keeps the process-global registry
    untouched for every other test in the session - and a duplicate would in
    any case be refused at load time, since the engine rejects a new pattern
    that an existing one already matches.
    """

    def _unused(*_args: Any, **_kwargs: Any) -> None:
        """The callable a throwaway matcher needs and never invokes."""

    # Snapshotted before the matchers are built and compared afterwards, so the
    # closing assertion is about *this* test's effect on the registry and
    # nothing else.
    registered_before = len(step_registry.steps[STEP_BUCKET])

    plain = ParseMatcher(_unused, COUNTERFACTUAL_PLAIN_PATTERN)
    typed = ParseMatcher(_unused, COUNTERFACTUAL_TYPED_PATTERN)

    swallowed = plain.match(COUNTERFACTUAL_TWO_ARGUMENT_TEXT)

    assert swallowed is not None, (
        f"the plain-field pattern {COUNTERFACTUAL_PLAIN_PATTERN!r} was expected "
        f"to match {COUNTERFACTUAL_TWO_ARGUMENT_TEXT!r} - that is the defect "
        f"the {CUKE_STR_TYPE_NAME} type exists to fix. If it no longer does, "
        f"the engine's matching changed and this module's reasoning needs "
        f"re-measuring"
    )
    assert swallowed.arguments[0].value == COUNTERFACTUAL_SWALLOWED_VALUE, (
        f"expected the plain field to capture "
        f"{COUNTERFACTUAL_SWALLOWED_VALUE!r}, got "
        f"{swallowed.arguments[0].value!r}"
    )

    assert typed.match(COUNTERFACTUAL_TWO_ARGUMENT_TEXT) is None, (
        f"the {CUKE_STR_TYPE_NAME} pattern "
        f"{COUNTERFACTUAL_TYPED_PATTERN!r} must not match the two-argument "
        f"line {COUNTERFACTUAL_TWO_ARGUMENT_TEXT!r}"
    )

    still_matches = typed.match(COUNTERFACTUAL_ONE_ARGUMENT_TEXT)

    assert still_matches is not None, (
        f"the {CUKE_STR_TYPE_NAME} pattern must still match its own "
        f"one-argument line {COUNTERFACTUAL_ONE_ARGUMENT_TEXT!r}"
    )
    assert still_matches.arguments[0].value == COUNTERFACTUAL_ONE_ARGUMENT_VALUE

    assert len(load_step_registry().steps[STEP_BUCKET]) == registered_before, (
        f"building a throwaway matcher must not register it; the real "
        f"registry held {registered_before} definitions before this test and "
        f"{len(load_step_registry().steps[STEP_BUCKET])} after"
    )


# =========================================================================== #
# PHASE 3 - DIRECTION 3
#
# Import hygiene across features/steps/, checked textually over every module,
# and each check paired with a synthetic bad module it must reject.
# =========================================================================== #


@pytest.mark.parametrize("module", STEP_MODULE_NAMES)
def test_step_module_does_not_import_the_automation_library(
    step_module_sources: dict[str, str], module: str
) -> None:
    """No module under ``features/steps/`` imports the browser library.

    AAP 0.4.2 names this module as the owner of the check: the library is
    imported only inside ``app/automation``, which is what lets the driver
    lifecycle, the nine explicit waits and the two interaction helpers have one
    owner each.  A step module that reached for it directly would bypass all
    three.
    """
    offenders = forbidden_library_imports(step_module_sources[module])

    assert not offenders, (
        f"{module}.py imports {FORBIDDEN_IMPORT_ROOT} directly; only "
        f"app/automation may: "
        + "; ".join(offender.describe(module) for offender in offenders)
    )


@pytest.mark.parametrize("module", STEP_MODULE_NAMES)
def test_step_module_does_not_import_a_keyword_decorator(
    step_module_sources: dict[str, str], module: str
) -> None:
    """No module imports ``given``, ``when`` or ``then``.

    The textual half of deviation 7.  The bucket assertions in Phase 0 catch a
    keyword-bound *registration*; this catches the import that would make one
    possible, which is the earlier and clearer signal.
    """
    offenders = keyword_decorator_imports(step_module_sources[module])

    assert not offenders, (
        f"{module}.py imports a keyword-bound decorator; every definition "
        f"registers with @{REQUIRED_DECORATOR_NAME} (AAP deviation 7): "
        + "; ".join(offender.describe(module) for offender in offenders)
    )


@pytest.mark.parametrize("module", STEP_MODULE_NAMES)
def test_step_module_applies_only_permitted_decorators(
    step_module_sources: dict[str, str], module: str
) -> None:
    """Only ``@step`` registers, and only ``@with_pattern`` joins it.

    An import is one way to reach a keyword decorator; a qualified use such as
    ``@behave.given(...)`` is another, and this catches that too by looking at
    what is actually applied.  Every ``@step`` is asserted to carry exactly one
    string literal, because a computed pattern would make the registry's
    contents unreadable from the source.
    """
    applied = decorations(step_module_sources[module])
    unexpected = tuple(
        decoration
        for decoration in applied
        if decoration.decorator not in ALLOWED_DECORATOR_NAMES
    )

    assert not unexpected, (
        f"{module}.py applies decorators outside "
        f"{sorted(ALLOWED_DECORATOR_NAMES)}: "
        + ", ".join(
            f"line {decoration.lineno} @{decoration.decorator} on "
            f"{decoration.func_name}"
            for decoration in unexpected
        )
    )

    unliteral = tuple(
        decoration
        for decoration in applied
        if decoration.decorator == REQUIRED_DECORATOR_NAME and decoration.pattern is None
    )

    assert not unliteral, (
        f"{module}.py has @{REQUIRED_DECORATOR_NAME} decorations whose pattern "
        f"is not a single string literal: "
        + ", ".join(
            f"line {decoration.lineno} on {decoration.func_name}"
            for decoration in unliteral
        )
    )


@pytest.mark.parametrize("module", STEP_MODULE_NAMES)
def test_step_module_import_surface_is_exactly_the_closed_list(
    step_module_sources: dict[str, str], module: str
) -> None:
    """Each module's imports are exactly the measured, closed set.

    The step modules' docstrings call their import list "complete and closed"
    and name this test as what keeps it.  Equality rather than a subset is the
    point: widening the boundary means editing
    :data:`EXPECTED_IMPORT_SURFACE`, which is a visible act in a review, where
    an unnoticed new dependency is not.

    The facts this pins, beyond the ban on the automation library:
    ``By`` is imported by ``login_steps`` alone (the port of
    ``LoginSD.java:56``'s direct lookup); ``wait_title_is`` by
    ``employee_steps`` alone; ``contacts_steps`` takes nothing from the
    interaction helpers; and only ``employee_steps`` and ``session_steps`` read
    configuration beyond the login URL.
    """
    found = frozenset(entry.label for entry in imported_names(step_module_sources[module]))
    expected = EXPECTED_IMPORT_SURFACE[module]

    assert found == expected, (
        f"{module}.py's import surface has drifted from the closed list.\n"
        f"  unexpected: {sorted(found - expected)}\n"
        f"  missing:    {sorted(expected - found)}"
    )


@pytest.mark.parametrize("module", STEP_MODULE_NAMES)
def test_step_module_imports_only_from_allowed_roots(
    step_module_sources: dict[str, str], module: str
) -> None:
    """Every import's top-level package is one of the four permitted.

    A coarser check than the exact surface above and deliberately kept
    alongside it: it is the one that still holds if
    :data:`EXPECTED_IMPORT_SURFACE` is ever updated, so the ban on a brand-new
    third-party dependency in the glue layer survives that edit.
    """
    offenders = tuple(
        entry
        for entry in imported_names(step_module_sources[module])
        if entry.root not in ALLOWED_IMPORT_ROOTS
    )

    assert not offenders, (
        f"{module}.py imports from outside {sorted(ALLOWED_IMPORT_ROOTS)}: "
        + "; ".join(offender.describe(module) for offender in offenders)
    )


@pytest.mark.parametrize("module", STEP_MODULE_NAMES)
def test_step_module_reaches_automation_through_the_package(
    step_module_sources: dict[str, str], module: str
) -> None:
    """No module imports an ``app.automation`` submodule directly.

    The package publishes the whole surface - the driver accessors, the nine
    waits, the two interaction helpers and the locator strategy - and taking a
    helper from the package rather than from the file it lives in is what keeps
    the library import confined to three files and lets that layer be
    reorganised without touching glue.
    """
    offenders = submodule_imports(
        step_module_sources[module], FORBIDDEN_APP_AUTOMATION_SUBMODULES
    )

    assert not offenders, (
        f"{module}.py imports an app.automation submodule; import from the "
        f"package instead: "
        + "; ".join(offender.describe(module) for offender in offenders)
    )


@pytest.mark.parametrize("module", STEP_MODULE_NAMES)
def test_step_module_constructs_no_collaborator_at_import_time(
    step_module_sources: dict[str, str], module: str
) -> None:
    """No page object, driver or wait is built when a step module is imported.

    AAP 0.4.2's one deliberately preserved semantic difference.  The Java
    classes build their page object and their ``WebDriverWait`` as fields at
    glue construction; in Python that would bind whichever worker process
    imported the module first, so both move inside the step bodies - the page
    from ``context.driver``, the timeout as an argument rather than an object.

    Binding a page inside a body costs nothing observable, which is what makes
    the move safe: construction stores the driver and locates nothing, so it
    does not displace a fixed delay that has to precede a click.
    """
    offenders = module_scope_production_calls(step_module_sources[module])

    assert not offenders, (
        f"{module}.py calls into the application at module scope, which would "
        f"bind one worker's session for every scenario: "
        + ", ".join(f"line {line}: {rendered}" for line, rendered in offenders)
    )


@pytest.mark.parametrize("module", STEP_MODULE_NAMES)
def test_step_module_declares_no_test_of_its_own(
    step_module_sources: dict[str, str], module: str
) -> None:
    """No step module declares a ``test_``-named function, and none imports pytest.

    ``pytest.ini`` keeps ``features/`` out of collection, and these modules are
    glue matched by phrase at scenario runtime.  A ``test_``-named function here
    would be dead code that looks like coverage, and importing ``pytest`` into
    the glue layer would put a test-only dependency on the runtime path - which
    also rules out a fixture, since declaring one needs that import.
    """
    tree = ast.parse(step_module_sources[module])
    test_functions = tuple(
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    )

    assert not test_functions, (
        f"{module}.py defines pytest-named functions {list(test_functions)}; "
        f"its verifying test is tests/test_{module}.py"
    )

    pytest_imports = tuple(
        entry
        for entry in imported_names(step_module_sources[module])
        if entry.root == "pytest"
    )

    assert not pytest_imports, f"{module}.py imports pytest"


def test_environment_module_declares_no_step_definition(
    step_registry: Any, definitions: tuple[StepMatch, ...]
) -> None:
    """``features/environment.py`` carries hooks and no step definitions.

    It ports ``Hooks.java``, which declares no annotation, and it sits beside
    ``features/steps/`` rather than inside it, so the loader never reaches it.
    Asserted from both sides: no definition claims it as an owner, and its
    source applies no registration decorator.
    """
    assert not any(
        definition.module_name == "environment" for definition in definitions
    ), "features/environment.py must register no step definition"

    source = (FEATURES_DIR / "environment.py").read_text(encoding=SOURCE_ENCODING)
    registrations = tuple(
        decoration
        for decoration in decorations(source)
        if decoration.decorator in ALLOWED_DECORATOR_NAMES
        or decoration.decorator in FORBIDDEN_DECORATOR_NAMES
    )

    assert not registrations, (
        "features/environment.py applies a step-registration decorator: "
        + ", ".join(
            f"line {decoration.lineno} @{decoration.decorator}"
            for decoration in registrations
        )
    )
    assert not forbidden_library_imports(source), (
        "features/environment.py imports the automation library directly; it "
        "reaches the driver through app.automation"
    )


# --------------------------------------------------------------------------- #
# The mutation-sensitivity proofs for Phase 3.
#
# Each detector above is now shown to reject a module that violates the rule it
# enforces, and to accept one that does not.  The synthetic sources are only
# ever parsed - never imported, exec'd or written to disk - and the first test
# below proves that by checking the phrase they all declare never reached the
# registry.
# --------------------------------------------------------------------------- #


def test_synthetic_sources_never_reach_the_registry(step_registry: Any) -> None:
    """The phrase the synthetic modules declare resolves to nothing.

    The guarantee that the rejection tests below cost nothing: they parse text,
    so none of these modules can have registered anything, and the real
    registry is untouched by them.
    """
    assert find_all_step_matches(SYNTHETIC_PHRASE) == (), (
        f"{SYNTHETIC_PHRASE!r} is declared only inside this module's synthetic "
        f"test sources and must never be registered; one of them was executed"
    )


def test_detectors_accept_a_clean_synthetic_module() -> None:
    """The positive control: a compliant module passes every detector.

    Without it, a detector that flagged everything would look like a working
    detector in all five tests below.
    """
    assert forbidden_library_imports(GOOD_SYNTHETIC_SOURCE) == ()
    assert keyword_decorator_imports(GOOD_SYNTHETIC_SOURCE) == ()
    assert module_scope_production_calls(GOOD_SYNTHETIC_SOURCE) == ()
    assert module_scope_statements(GOOD_SYNTHETIC_SOURCE) == ()
    assert (
        submodule_imports(GOOD_SYNTHETIC_SOURCE, FORBIDDEN_APP_AUTOMATION_SUBMODULES)
        == ()
    )
    assert {
        entry.root for entry in imported_names(GOOD_SYNTHETIC_SOURCE)
    } <= ALLOWED_IMPORT_ROOTS
    assert [
        decoration.decorator for decoration in decorations(GOOD_SYNTHETIC_SOURCE)
    ] == [REQUIRED_DECORATOR_NAME]


def test_keyword_decorator_detector_rejects_a_given_registration() -> None:
    """A module importing and applying ``@given`` is caught twice over.

    Once by the import detector, once by the decorator detector - the two
    routes to a keyword-bound registration, both closed.
    """
    offenders = keyword_decorator_imports(BAD_KEYWORD_DECORATOR_SOURCE)

    assert [entry.label for entry in offenders] == ["behave:given"], (
        f"the keyword-decorator detector missed a `from behave import given`: "
        f"{offenders}"
    )

    applied = {
        decoration.decorator for decoration in decorations(BAD_KEYWORD_DECORATOR_SOURCE)
    }

    assert not applied <= ALLOWED_DECORATOR_NAMES, (
        f"the decorator detector accepted {applied}, which contains a "
        f"keyword-bound registration"
    )


def test_library_detector_rejects_a_direct_import() -> None:
    """``import selenium`` at module scope is caught."""
    offenders = forbidden_library_imports(BAD_LIBRARY_IMPORT_SOURCE)

    assert [entry.label for entry in offenders] == [FORBIDDEN_IMPORT_ROOT], (
        f"the library detector missed a direct import: {offenders}"
    )
    assert offenders[0].nested is False


def test_library_detector_rejects_a_submodule_import() -> None:
    """An import naming a submodule of the library is caught too.

    The case a detector comparing against the exact string ``selenium`` would
    miss, which is why :func:`forbidden_library_imports` matches on the root
    package.
    """
    offenders = forbidden_library_imports(BAD_LIBRARY_SUBMODULE_SOURCE)

    assert [entry.label for entry in offenders] == [
        "selenium.webdriver.common.keys:Keys"
    ], f"the library detector missed a submodule import: {offenders}"
    assert offenders[0].root == FORBIDDEN_IMPORT_ROOT


def test_library_detector_rejects_an_import_hidden_in_a_step_body() -> None:
    """An import hidden inside a function body is caught too.

    The reason :func:`imported_names` walks the whole tree instead of reading
    only a module's top-level statements: a nested import is exactly as much of
    a boundary violation and considerably harder to see.
    """
    offenders = forbidden_library_imports(BAD_NESTED_LIBRARY_IMPORT_SOURCE)

    assert [entry.label for entry in offenders] == [
        "selenium.webdriver.common.by:By"
    ], f"the library detector missed a nested import: {offenders}"
    assert offenders[0].nested is True, (
        "a nested import must be reported as nested so the message can say where"
    )


def test_submodule_detector_rejects_both_routes_past_the_package() -> None:
    """Reaching an ``app.automation`` submodule is caught however it is spelled.

    ``from app.automation.waits import ...`` names the submodule as its module;
    ``from app.automation import driver`` names it as an imported *name*, since
    the package re-exports its submodules as attributes.  Both reach the same
    file, so both are rejected.
    """
    offenders = submodule_imports(
        BAD_AUTOMATION_SUBMODULE_SOURCE, FORBIDDEN_APP_AUTOMATION_SUBMODULES
    )

    assert sorted(entry.label for entry in offenders) == [
        "app.automation.waits:wait_visible_element",
        "app.automation:driver",
    ], f"the submodule detector missed a route past the package: {offenders}"


def test_module_scope_detector_rejects_a_page_built_at_import_time() -> None:
    """A page object constructed at module scope is caught.

    The defect AAP 0.4.2 describes: a module-scope construction binds whichever
    worker process imported the module first, so every scenario in every other
    worker would drive the wrong session.
    """
    offenders = module_scope_production_calls(BAD_MODULE_SCOPE_PAGE_SOURCE)

    assert [rendered for _line, rendered in offenders] == ["LoginPage(None)"], (
        f"the module-scope detector missed a page construction: {offenders}"
    )


def test_module_scope_detector_ignores_inert_module_level_data(
    step_module_sources: dict[str, str],
) -> None:
    """Inert module-level data is not flagged, and that is deliberate.

    ``calendar_steps`` declares a month-name mapping and three modules declare
    ``__all__``.  None constructs anything, touches a driver or performs a
    browser operation, so none is the defect the detector looks for - and a
    check that flagged them would have to be silenced, which is how a useful
    check becomes an ignored one.
    """
    inert = {
        module: module_scope_statements(source)
        for module, source in step_module_sources.items()
        if module_scope_statements(source)
    }

    assert set(inert) == {
        "calendar_steps",
        "contacts_steps",
        "crm_steps",
        "login_steps",
        "logout_steps",
    }, f"module-scope statements appeared in unexpected modules: {sorted(inert)}"

    for module, statements in inert.items():
        if module == "contacts_steps":
            # Asserted in full by its own test above.
            continue

        assert module_scope_production_calls(step_module_sources[module]) == (), (
            f"{module}.py's module-scope statements {statements} must construct "
            f"nothing"
        )


# =========================================================================== #
# PHASE 4 - The cross-keyword cases
#
# Both readings of "seven cross-keyword uses over two phrases", each asserted
# under its own name.
# =========================================================================== #


@pytest.mark.parametrize(
    "declaration", CROSS_KEYWORD_DECLARATIONS, ids=lambda case: case.java_location
)
def test_cross_keyword_phrase_resolves_from_every_invocation_site(
    step_registry: Any, declaration: CrossKeywordDeclaration
) -> None:
    """Reading (a): a ``@When`` in Java, reached by a ``Given`` in Gherkin.

    The phrase is asserted to resolve, to resolve into the keyword-agnostic
    bucket, and to be invoked at exactly the sites recorded for it - because the
    keyword is what would break it.  Bound to its declaring keyword, each of
    these phrases goes undefined at exactly the sites that use the other
    keyword, and for both of them those sites are ``Background`` lines, so every
    scenario in the file would fail before reaching its first step.
    """
    matches = find_all_step_matches(declaration.phrase)

    assert len(matches) == 1, (
        f"{declaration.phrase!r} ({declaration.java_location}, "
        f"@{declaration.java_keyword}) must resolve to exactly one definition, "
        f"got {len(matches)}"
    )
    assert matches[0].module_name == declaration.module
    assert matches[0].bucket == STEP_BUCKET, (
        f"{declaration.phrase!r} is registered in bucket "
        f"{matches[0].bucket!r}; it is invoked under "
        f"{sorted({site.keyword for site in declaration.sites})} and only "
        f"@{REQUIRED_DECORATOR_NAME} matches all of them"
    )

    found = tuple(
        InvocationSite(usage.feature, usage.line, usage.keyword)
        for usage in AS_WRITTEN_USAGES
        if usage.phrase == declaration.phrase
    )

    assert found == declaration.sites, (
        f"{declaration.phrase!r} is invoked at {list(found)}, expected "
        f"{list(declaration.sites)}"
    )

    mismatched = tuple(
        site for site in found if site.keyword != declaration.java_keyword
    )

    assert mismatched, (
        f"{declaration.phrase!r} is declared @{declaration.java_keyword} at "
        f"{declaration.java_location} and is expected to be invoked under a "
        f"different keyword somewhere; it no longer is, so this case is not "
        f"cross-keyword any more and the expectations here need re-measuring"
    )


def test_cross_keyword_sites_number_seven_over_two_phrases(step_registry: Any) -> None:
    """Reading (a) in aggregate: seven sites carry a keyword Java did not declare.

    Six ``Given`` uses of the shared precondition plus one ``When``, and one
    ``Given`` use of the Contacts dashboard step: 6 + 1 + 1 = 8 sites of which
    seven carry a keyword the Java annotation does not - the finding's "seven
    cross-keyword uses over two phrases".
    """
    mismatched = tuple(
        (declaration.phrase, site)
        for declaration in CROSS_KEYWORD_DECLARATIONS
        for site in declaration.sites
        if site.keyword != declaration.java_keyword
    )

    assert len(mismatched) == EXPECTED_CROSS_KEYWORD_SITES, (
        f"expected {EXPECTED_CROSS_KEYWORD_SITES} Gherkin sites whose keyword "
        f"differs from the Java annotation's, found {len(mismatched)}: "
        f"{list(mismatched)}"
    )
    assert len({phrase for phrase, _site in mismatched}) == len(
        CROSS_KEYWORD_DECLARATIONS
    ), "both phrases must contribute at least one mismatched site"


def test_session_precondition_is_the_case_aap_documents(step_registry: Any) -> None:
    """The documented case, named: ``Session.java:12``, six ``Given`` uses and one ``When``.

    AAP 0.5.2 cites this one directly, with the baseline artifact as evidence:
    ``target/cucumber.json`` records the step with ``keyword: "Given "`` and a
    ``match.location`` pointing at ``Session.user_login_to_test_other_features``.
    Registered as ``@when`` in Python it would go undefined at six of its seven
    sites.
    """
    assert SESSION_PRECONDITION.documented_in_aap is True

    given_sites = tuple(
        site for site in SESSION_PRECONDITION.sites if site.keyword == "Given"
    )
    when_sites = tuple(
        site for site in SESSION_PRECONDITION.sites if site.keyword == "When"
    )

    assert len(given_sites) == 6, f"expected six Given sites, got {list(given_sites)}"
    assert len(when_sites) == 1, f"expected one When site, got {list(when_sites)}"
    assert when_sites[0].feature == "Session.feature", (
        "the only When use is in the feature that declares the step"
    )

    match = find_all_step_matches(SESSION_PRECONDITION.phrase)

    assert len(match) == 1 and match[0].module_name == "session_steps"
    assert match[0].func.__name__ == "user_login_to_test_other_features", (
        "the function name is the Java method name verbatim, which is what "
        "target/cucumber.json reports as match.location (AAP deviation 8)"
    )


def test_contacts_precondition_is_a_second_undocumented_cross_keyword_case(
    step_registry: Any,
) -> None:
    """The case AAP 0.5.2 does **not** mention, measured here and named on its own.

    ``Contacts.java:17`` declares ``User is at Contact dashboard`` as ``@When``
    and ``Contact.feature:6`` invokes it as a ``Given`` inside that feature's
    own ``Background``.  It has the same consequence as the Session case and the
    same fix, and it is recorded separately so that a future reader does not
    conclude from AAP 0.5.2 that the Session step is the only one of its kind.

    It is also the reason the ``Background``-inclusive corpus is the one used
    for the coverage census: this phrase is reached from nowhere else.
    """
    assert CONTACTS_PRECONDITION.documented_in_aap is False

    match = find_all_step_matches(CONTACTS_PRECONDITION.phrase)

    assert len(match) == 1 and match[0].module_name == "contacts_steps"
    assert match[0].bucket == STEP_BUCKET

    sites = tuple(
        usage
        for usage in AS_WRITTEN_USAGES
        if usage.phrase == CONTACTS_PRECONDITION.phrase
    )

    assert len(sites) == 1, f"expected one invocation site, got {len(sites)}"
    assert (sites[0].feature, sites[0].line, sites[0].keyword) == (
        "Contact.feature",
        6,
        "Given",
    )
    assert CONTACTS_PRECONDITION.phrase in background_phrases("Contact.feature"), (
        "the phrase is authored in Contact.feature's Background, which is why "
        "a corpus that skipped backgrounds would report it unexercised"
    )


def test_three_phrases_are_used_under_more_than_one_step_type() -> None:
    """Reading (b): the Gherkin itself invokes three phrases under two types each.

    Measured over the authored corpus, by *effective* step type rather than by
    written keyword, so the 82 ``And`` lines are resolved to the type they
    inherit.  Together the three account for fourteen usages - 7 + 5 + 2 - and
    every one of them depends on ``@step``: a definition bound to ``then`` would
    not match the ``And`` that inherits ``when``.
    """
    types_by_phrase: dict[str, set[str]] = collections.defaultdict(set)

    for usage in AS_WRITTEN_USAGES:
        types_by_phrase[usage.phrase].add(usage.step_type)

    multi_type = {
        phrase: frozenset(types)
        for phrase, types in types_by_phrase.items()
        if len(types) > 1
    }

    assert multi_type == EXPECTED_MULTI_TYPE_PHRASES, (
        f"expected exactly {len(EXPECTED_MULTI_TYPE_PHRASES)} phrases used "
        f"under more than one effective step type.\n  found:    "
        f"{ {phrase: sorted(types) for phrase, types in multi_type.items()} }\n"
        f"  expected: "
        f"{ {phrase: sorted(types) for phrase, types in EXPECTED_MULTI_TYPE_PHRASES.items()} }"
    )

    usages = sum(
        1 for usage in AS_WRITTEN_USAGES if usage.phrase in EXPECTED_MULTI_TYPE_PHRASES
    )

    assert usages == EXPECTED_MULTI_TYPE_USAGES, (
        f"the three multi-type phrases must account for "
        f"{EXPECTED_MULTI_TYPE_USAGES} authored usages, found {usages}"
    )


@pytest.mark.parametrize("phrase", sorted(EXPECTED_MULTI_TYPE_PHRASES))
def test_multi_type_phrase_resolves_from_a_single_definition(
    step_registry: Any, phrase: str
) -> None:
    """Each multi-type phrase is served by one definition, not one per type.

    The failure mode this rules out is a port that "fixed" the keyword problem
    by registering the same phrase under two keywords.  That would resolve every
    site *and* make the phrase ambiguous, so it is only caught by asserting the
    definition count from the site side as well.
    """
    matches = find_all_step_matches(phrase)

    assert len(matches) == 1, (
        f"{phrase!r} is used under "
        f"{sorted(EXPECTED_MULTI_TYPE_PHRASES[phrase])} and must still be "
        f"served by exactly one definition, got "
        + ", ".join(f"{match.pattern!r} at {match.location}" for match in matches)
    )
    assert matches[0].bucket == STEP_BUCKET


def test_conjunction_keyword_dominates_the_corpus() -> None:
    """82 of the 158 authored step lines are ``And``.

    The measured scale of the problem ``@step`` solves.  The keyword census is
    asserted in full so that a re-worded scenario cannot shift the balance
    unnoticed, and ``But`` and ``*`` are asserted absent because the table would
    otherwise be silent about them.
    """
    census = collections.Counter(usage.keyword for usage in AS_WRITTEN_USAGES)

    assert dict(census) == EXPECTED_KEYWORD_CENSUS, (
        f"authored keyword census changed: {dict(census)}, expected "
        f"{EXPECTED_KEYWORD_CENSUS}"
    )
    assert census[CONJUNCTION_KEYWORD] == EXPECTED_KEYWORD_CENSUS[
        CONJUNCTION_KEYWORD
    ], "the And count is the reason keyword-typed registration was impossible"
    assert sum(census.values()) == EXPECTED_AS_WRITTEN_USAGES


def test_effective_step_types_resolve_the_conjunctions() -> None:
    """The 82 ``And`` lines collapse into three effective types.

    ``when`` 116, ``then`` 33, ``given`` 9.  This is the axis the engine
    actually resolves on, which is why a keyword-bound definition fails at a use
    it looks compatible with.
    """
    census = collections.Counter(usage.step_type for usage in AS_WRITTEN_USAGES)

    assert dict(census) == EXPECTED_STEP_TYPE_CENSUS, (
        f"effective step-type census changed: {dict(census)}, expected "
        f"{EXPECTED_STEP_TYPE_CENSUS}"
    )


def test_engine_publishes_no_conjunction_decorator(step_registry: Any) -> None:
    """There is no ``@and``, which is why three Java classes could not be translated literally.

    ``Calendar.java``, ``Crm.java`` and ``Sales.java`` import
    ``io.cucumber.java.en.And``.  The engine offers ``given``, ``when``, ``then``
    and ``step`` and nothing for the conjunction, so those annotations have no
    keyword-typed equivalent at all - ``@step`` is not a preference here, it is
    the only available translation.
    """
    assert len(JAVA_AND_IMPORTERS) == 3

    for name in ("and", "and_", "And"):
        assert not hasattr(behave, name), (
            f"behave unexpectedly publishes {name!r}; the reasoning behind "
            f"deviation 7 would need re-measuring"
        )

    assert hasattr(behave, REQUIRED_DECORATOR_NAME)

    for keyword in FORBIDDEN_DECORATOR_NAMES:
        assert hasattr(behave, keyword), (
            f"behave must still publish {keyword!r} - the import-hygiene tests "
            f"assert that the step modules do not use it, which means nothing "
            f"if it does not exist"
        )

    assert "and" not in step_registry.steps, (
        "the registry grew a conjunction bucket, which conftest does not scan"
    )


# =========================================================================== #
# PHASE 5 - Feature-file inventory cross-checks
#
# The corpus these assertions are measured over is only meaningful if the ten
# files are the ten files, unchanged.
# =========================================================================== #


@pytest.mark.parametrize("feature", FEATURE_FILENAMES)
def test_feature_level_tags_are_preserved(feature: str) -> None:
    """Each feature carries exactly the feature-level tags the source declared.

    Five carry one each - ``@Calendar``, ``@Smoke``, ``@UPGN-344``, ``@Login``,
    ``@LogOut`` - and five carry none.  AAP 0.2.2 declines to correct the
    latter: an untagged feature is simply not selected by the default run, and
    inventing a tag would change which scenarios execute.
    """
    parsed = parse_file(str(FEATURES_DIR / feature))

    assert tuple(parsed.tags) == EXPECTED_FEATURE_TAGS[feature], (
        f"features/{feature} feature-level tags are {list(parsed.tags)}, "
        f"expected {list(EXPECTED_FEATURE_TAGS[feature])}"
    )


def test_smoke_tag_occurs_exactly_once_in_the_suite() -> None:
    """``@Smoke`` is declared once, on ``Crm.feature``.

    The default tag expression of a run (``CukesRunner.java:18``), so this one
    tag decides what a plain invocation executes.  Counted over every tag
    scope - feature, scenario and ``Examples`` - so a second declaration
    anywhere would fail here rather than silently double the default run.

    The site reported is the **tag's own** line, which the engine's ``Tag``
    model carries: a feature-level tag sits on the line above the ``Feature:``
    keyword, so ``feature.line`` would name the wrong line by one.
    """
    holders: list[str] = []

    def _record(tags: Any, scope: str, feature_name: str) -> None:
        """Append a site for every occurrence of the tag in *tags*."""
        for tag in tags:
            if str(tag) == SMOKE_TAG:
                holders.append(f"{feature_name}:{tag.line} ({scope})")

    for path in _feature_paths():
        parsed = parse_file(str(path))
        _record(parsed.tags, "feature", path.name)

        for scenario in parsed.scenarios:
            _record(scenario.tags, "scenario", path.name)

            for examples in getattr(scenario, "examples", ()):
                _record(examples.tags, "examples", path.name)

    assert holders == ["Crm.feature:1 (feature)"], (
        f"@{SMOKE_TAG} must be declared exactly once, on Crm.feature; found "
        f"{holders}"
    )


@pytest.mark.parametrize("feature", FEATURE_FILENAMES)
def test_feature_title_is_preserved_verbatim(feature: str) -> None:
    """Titles are carried over exactly, mistakes included.

    Three are wrong and all three stay wrong: ``Contact.feature`` is titled
    "Testinium app Inventory feature", ``Notes.feature`` is titled "Testinium
    app login feature", and ``Sales.feature`` opens with four literal dots.
    AAP 0.2.2 names the first two explicitly as things this port does not
    correct, and two features sharing a title is also what makes two pairs
    share a JSON ``id`` - source behaviour the viewer keys around by index
    rather than disambiguating.
    """
    parsed = parse_file(str(FEATURES_DIR / feature))

    assert parsed.name == EXPECTED_FEATURE_TITLES[feature], (
        f"features/{feature} is titled {parsed.name!r}, expected "
        f"{EXPECTED_FEATURE_TITLES[feature]!r}"
    )


def test_two_pairs_of_features_share_a_title() -> None:
    """The mis-titles collide in pairs, which is the source of the ``id`` collision.

    ``Contact.feature`` shares "Testinium app Inventory feature" with
    ``Inventory.feature``, and ``Notes.feature`` shares "Testinium app login
    feature" with ``Login.feature``.  Asserted here so that the collision is
    recorded as expected state rather than discovered as a surprise by whoever
    reads the report artifacts.
    """
    counts = collections.Counter(EXPECTED_FEATURE_TITLES.values())
    shared = {title: count for title, count in counts.items() if count > 1}

    assert shared == {
        "Testinium app Inventory feature": 2,
        "Testinium app login feature": 2,
    }, f"expected exactly two shared titles, found {shared}"


def test_login_feature_carries_the_french_assertion_verbatim(
    step_registry: Any,
) -> None:
    """``Login.feature:89`` holds the French validation message, byte for byte.

    AAP 0.8 singles this out as the one thing the plan cannot decide: the
    assertion depends on a French-locale browser and neither repository shows
    how that locale is arranged, so the port carries the string verbatim and
    invents no locale key.  What is asserted here is textual and local - the
    phrase is present at that line and resolves to the login module's
    message definition.  Nothing here launches a browser or asserts a locale.
    """
    sites = tuple(
        usage for usage in AS_WRITTEN_USAGES if usage.phrase == FRENCH_ASSERTION_PHRASE
    )

    assert len(sites) == 1, (
        f"expected exactly one use of {FRENCH_ASSERTION_PHRASE!r}, found "
        f"{[usage.site for usage in sites]}"
    )
    assert (sites[0].feature, sites[0].line, sites[0].keyword) == FRENCH_ASSERTION_SITE

    matches = find_all_step_matches(FRENCH_ASSERTION_PHRASE)

    assert len(matches) == 1 and matches[0].module_name == "login_steps"
    assert matches[0].pattern == 'User sees "{alert_message}" message'
    assert matches[0].kwargs == {
        "alert_message": "Veuillez renseigner ce champ."
    }, (
        f"the French string must be captured unchanged, got {matches[0].kwargs}"
    )


def test_login_outlines_carry_their_jira_tags_and_examples_blocks() -> None:
    """``Login.feature``'s five outlines, their tags and the valid-login row counts.

    ``@UPGN-286`` through ``@UPGN-290`` in line order, each on its own outline,
    and the first one's two ``Examples`` blocks tagged ``@SalesManager`` and
    ``@PosManager`` with 13 and 15 data rows.  The asymmetry is source data:
    AAP 0.2.2 does not even it up, and it is a large part of why the expanded
    corpus is 443 usages against 158 authored ones.
    """
    parsed = parse_file(str(FEATURES_DIR / "Login.feature"))
    scenarios = tuple(parsed.scenarios)

    assert (
        tuple(tag for scenario in scenarios for tag in scenario.tags)
        == EXPECTED_LOGIN_OUTLINE_TAGS
    ), (
        "Login.feature's five outlines must carry exactly "
        f"{list(EXPECTED_LOGIN_OUTLINE_TAGS)}, in line order"
    )

    valid_login = scenarios[0]
    found = tuple(
        (tag, len(examples.table.rows))
        for examples in valid_login.examples
        for tag in examples.tags
    )

    assert found == EXPECTED_VALID_LOGIN_EXAMPLES, (
        f"the @{EXPECTED_LOGIN_OUTLINE_TAGS[0]} outline's Examples blocks are "
        f"{list(found)}, expected {list(EXPECTED_VALID_LOGIN_EXAMPLES)}"
    )


def test_expanded_corpus_renders_every_examples_row() -> None:
    """Every ``Examples`` row becomes a concrete step line, placeholders resolved.

    The substitution half of the outline-expansion checkbox: the 23
    placeholder-bearing lines that survive into the expanded corpus are the
    outline *templates*, and everything else has its values filled in.  The
    rendered values asserted below are drawn from five different features, so
    a substitution failure confined to one file still fails here.
    """
    templates = tuple(usage for usage in EXPANDED_USAGES if "<" in usage.phrase)

    assert len(templates) == EXPECTED_TEMPLATE_USAGES, (
        f"expected the {EXPECTED_TEMPLATE_USAGES} outline template lines to "
        f"survive expansion, found {len(templates)}"
    )
    assert {usage.phrase for usage in templates} == set(PLACEHOLDER_PHRASES), (
        "the surviving template lines must be exactly the placeholder-bearing "
        "phrases of the authored corpus"
    )

    rendered = set(EXPANDED_PHRASES)
    rendered_only = sorted(rendered - set(AS_WRITTEN_PHRASES))

    assert len(rendered_only) == EXPECTED_RENDERED_ONLY_PHRASES, (
        f"expected {EXPECTED_RENDERED_ONLY_PHRASES} phrases that exist only "
        f"after substitution, found {len(rendered_only)}"
    )

    for expected in RENDERED_ONLY_SAMPLES:
        assert expected in rendered, (
            f"{expected!r} is an Examples row rendered from an outline and must "
            f"appear in the expanded corpus"
        )
        assert expected not in AS_WRITTEN_PHRASES, (
            f"{expected!r} was chosen because substitution is the only way it "
            f"can appear; it is now authored literally, so it no longer tests "
            f"expansion and this sample needs replacing"
        )


def test_expanded_corpus_is_a_superset_of_the_authored_scenario_phrases() -> None:
    """Expansion adds phrases and removes none, apart from the backgrounds it omits.

    The containment relation between the two corpora, asserted so that neither
    can drift independently of the other: every authored phrase that is not a
    background line must still be present after expansion.
    """
    authored_background_phrases = {
        phrase
        for feature in FEATURE_FILENAMES
        for phrase in background_phrases(feature)
    }
    authored_scenario_phrases = set(AS_WRITTEN_PHRASES) - authored_background_phrases
    missing = sorted(authored_scenario_phrases - set(EXPANDED_PHRASES))

    assert not missing, (
        f"phrases authored in a scenario but absent from the expanded corpus: "
        f"{missing}"
    )
    assert len(EXPANDED_PHRASES) > len(AS_WRITTEN_PHRASES), (
        "expansion must add rendered phrases"
    )
