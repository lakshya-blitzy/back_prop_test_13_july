"""Frozen constants of the CI report-publication step.

This module is the single canonical Python home for the parameters of the
``cucumber`` report-publication call the CI pipeline issues, and it is nothing
else. It holds eight values, defines no function and no class, performs no I/O,
and has no side effect whatsoever on import: nothing is read, created or
inspected when it loads.

Provenance
----------
Every value below is quoted verbatim from a single line of the source pipeline,
``[Jenkins:L15]`` -- the sole statement of its ``'Generate report'`` stage::

    stage('Generate report'){
           cucumber failedFeaturesNumber: -1, failedScenariosNumber: -1,
           failedStepsNumber: -1, fileIncludePattern: '**/*.json',
           pendingStepsNumber: -1, skippedStepsNumber: -1,
           sortingMethod: 'ALPHABETICAL', undefinedStepsNumber: -1
    }

The unwrapped, byte-exact original of that statement is reproduced as a comment
immediately above the constants themselves, so the mapping between source and
port can be audited by inspection without opening ``Jenkins``. That is Rule T1
of the migration in practice: configuration values are data, not decisions, so
each one is carried across as an explicit constant with its source cited, and no
value is rounded, renamed or modernised.

The Groovy call passed its eight parameters *by name*, which is why the six
thresholds are also published here as a mapping keyed by those parameter names.
Those keys stay in camelCase, spelled exactly as the publisher spells them: they
are the publisher's own vocabulary carried across as data, not Python
identifiers, so they are never re-cased.

Why these constants live here, and only here
--------------------------------------------
Several artifacts have to agree on the same eight values: the pipeline
definition and its discoverable alias, the two committed configuration templates
(``.env.example`` and ``configuration.properties.example``), the application
configuration in ``app/config.py``, and the service that reproduces the
publication step. They agree by taking the values from this module instead of
restating them, so no value can drift in one place while staying correct in
another. Re-declaring any constant below elsewhere under ``app/`` is a defect.

Division of responsibility
--------------------------
* This module owns the **publisher constants**; ``app/utils/paths.py`` owns the
  **paths** -- the artifact-root layout and the location of every report file.
  The split is deliberate and neither module repeats the other: the include
  pattern below is intentionally absent from ``paths.py``, and no filesystem
  path is declared here.
* This module **stores** values and never evaluates them. Sorting features by
  name before rendering is ``app/services/report_service.py``'s behaviour, not
  this module's, even though the constant that requests it lives here.
* The non-gating exit-code policy of the test stage -- pytest exit code ``1``
  treated as non-failing and exit code ``5`` treated as a successful
  zero-scenario run -- belongs to ``app/services/test_runner_service.py``. It is
  deliberately not here, so do not look for it here.
* Resolving overrides is ``app/config.py``'s responsibility. Its precedence
  chain (constructor argument -> environment variable -> ``.env`` ->
  ``configuration.properties`` -> default) ends at the hard-coded default, and
  this module is that final rung: it reads no environment variable and parses no
  configuration file. It equally holds none of the *other* pipeline constants --
  the clone URL and the preserved tag expression belong to ``app/config.py``,
  because neither is a publisher setting.

Layering and dependencies
-------------------------
The application's dependency direction is fixed at ``api -> services ->
reporting -> utils``, and ``app/config.py`` imports this module. Anything
imported back from ``app`` here would therefore close a cycle that fails at
application start-up, so this module imports **only the standard library** --
three names, for immutability and typing -- and never ``app.config``,
``app.services``, ``app.api``, ``app.web``, the ``app`` package root,
``app.utils``, ``tests`` or ``scripts``. It touches neither Flask nor any test
harness distribution, which keeps it importable in a container built from
``requirements.txt`` alone, along the deployed chain ``wsgi.py`` ->
``app/__init__.py`` -> ``app/config.py`` -> this module. It configures no
logging and owns no logger; ``app/logging_config.py`` owns logging.

Preserved defect
----------------
All six thresholds are ``-1``, which the publisher reads as "no threshold".
That is defect **D3** of the migration, preserved on purpose rather than
corrected; the banner above the constants explains it in full, and
``docs/migration-parity.md`` is the authoritative register of defects D1 through
D9 and of the switch that opts into each available fix.

Usage
-----
::

    >>> from app.reporting.thresholds import PUBLISHER_THRESHOLDS
    >>> len(PUBLISHER_THRESHOLDS)
    6
    >>> PUBLISHER_THRESHOLDS["failedStepsNumber"]
    -1
    >>> from app.reporting.thresholds import REPORT_SORTING_METHOD
    >>> REPORT_SORTING_METHOD
    'ALPHABETICAL'
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

__all__ = [
    "PUBLISHER_THRESHOLDS",
    "PUBLISHER_THRESHOLD_KEYS",
    "REPORT_FAILED_FEATURES_NUMBER",
    "REPORT_FAILED_SCENARIOS_NUMBER",
    "REPORT_FAILED_STEPS_NUMBER",
    "REPORT_FILE_INCLUDE_PATTERN",
    "REPORT_PENDING_STEPS_NUMBER",
    "REPORT_SKIPPED_STEPS_NUMBER",
    "REPORT_SORTING_METHOD",
    "REPORT_UNDEFINED_STEPS_NUMBER",
]


# =============================================================================
# The source statement, reproduced unwrapped.
#
# `Jenkins` line 15 is the single statement of the `stage('Generate report')`
# block. In the original it is indented with eleven spaces and ends at its last
# `-1` with no trailing whitespace. It is the source of truth for every constant
# in this module, and it is copied here on one line, exactly as it reads there,
# so that the port can be checked against it by eye:
#
#            cucumber failedFeaturesNumber: -1, failedScenariosNumber: -1, failedStepsNumber: -1, fileIncludePattern: '**/*.json', pendingStepsNumber: -1, skippedStepsNumber: -1, sortingMethod: 'ALPHABETICAL', undefinedStepsNumber: -1
#
# The Groovy call lists its eight named parameters alphabetically, so
# `fileIncludePattern` and `sortingMethod` sit interleaved among the thresholds.
# The six thresholds keep their relative source order below; the other two
# parameters are declared afterwards, each in its own section.
# =============================================================================


# =============================================================================
# DEFECT D3 -- INTENTIONALLY PRESERVED: the never-failing build.
#
# All six thresholds below are `-1`, and the Jenkins Cucumber publisher reads
# `-1` as "no threshold". A limit of `-1` can never be exceeded, so no number of
# failed features, failed scenarios, failed steps, pending steps, skipped steps
# or undefined steps can mark the build unstable or failed. Combined with
#
#     <testFailureIgnore>true</testFailureIgnore>          [pom.xml:L25]
#
# -- which makes the source build swallow test failures outright -- the source
# system therefore has no build-time quality gate at all. The project's own
# Technical Specification records that conclusion independently.
#
# This is defect D3 of the migration register, and it is reproduced here on
# purpose under Rule T4, "Defects are behavior": the requirement is that the
# rewrite fully match the behaviour and logic of the current implementation, so
# these values are carried across unchanged rather than quietly corrected.
# `docs/migration-parity.md` is the authoritative register of defects D1 through
# D9 and records the configuration switch that opts into each available fix.
#
# Two consequences for anyone editing this file:
#
#   * Do NOT "fix" a `-1` to `0` or to any positive limit. Six `-1` values are
#     asserted by validation criterion V4 and by the thresholds parity test, and
#     changing one would make the ported pipeline fail where the original went
#     green.
#   * Do NOT add gating here. This module stores constants and never evaluates
#     them: there is deliberately no severity ranking, no limit-exceeded
#     calculation and no "must this fail the build" helper anywhere below.
#     Build-failure gating is out of scope for the whole migration and stays
#     switched off, which is exactly what preserves D3.
# =============================================================================

# [Jenkins:L15] failedFeaturesNumber: -1
REPORT_FAILED_FEATURES_NUMBER: Final[int] = -1
"""Publisher limit on failed features. ``-1`` means "no threshold"."""

# [Jenkins:L15] failedScenariosNumber: -1
REPORT_FAILED_SCENARIOS_NUMBER: Final[int] = -1
"""Publisher limit on failed scenarios. ``-1`` means "no threshold"."""

# [Jenkins:L15] failedStepsNumber: -1
REPORT_FAILED_STEPS_NUMBER: Final[int] = -1
"""Publisher limit on failed steps. ``-1`` means "no threshold"."""

# [Jenkins:L15] pendingStepsNumber: -1
REPORT_PENDING_STEPS_NUMBER: Final[int] = -1
"""Publisher limit on pending steps. ``-1`` means "no threshold"."""

# [Jenkins:L15] skippedStepsNumber: -1
REPORT_SKIPPED_STEPS_NUMBER: Final[int] = -1
"""Publisher limit on skipped steps. ``-1`` means "no threshold"."""

# [Jenkins:L15] undefinedStepsNumber: -1
REPORT_UNDEFINED_STEPS_NUMBER: Final[int] = -1
"""Publisher limit on undefined steps. ``-1`` means "no threshold"."""


# The publisher's six named threshold parameters, gathered into one read-only
# mapping. The keys are the Groovy parameter names, verbatim and in their source
# order; the values are the constants declared immediately above, so each number
# is written down exactly once in this codebase and the mapping cannot disagree
# with the individually importable constants.
#
# `MappingProxyType` is what makes this genuinely frozen, and it is not
# decoration: `Final` only forbids rebinding the name, so a plain dict annotated
# `Final` would still accept item assignment at run time. This mapping is
# reachable from the configuration-introspection endpoint, so a careless request
# handler could otherwise mutate the parity contract in-process for every later
# request. The dict is built inline and never bound to a module attribute, which
# leaves no mutable reference to it anywhere; item assignment on the proxy raises
# `TypeError`.
PUBLISHER_THRESHOLDS: Final[Mapping[str, int]] = MappingProxyType(
    {
        "failedFeaturesNumber": REPORT_FAILED_FEATURES_NUMBER,
        "failedScenariosNumber": REPORT_FAILED_SCENARIOS_NUMBER,
        "failedStepsNumber": REPORT_FAILED_STEPS_NUMBER,
        "pendingStepsNumber": REPORT_PENDING_STEPS_NUMBER,
        "skippedStepsNumber": REPORT_SKIPPED_STEPS_NUMBER,
        "undefinedStepsNumber": REPORT_UNDEFINED_STEPS_NUMBER,
    }
)
"""The six publisher thresholds, keyed by their Groovy parameter names.

This is the module's headline export and a fixed cross-module contract:
``app/services/report_service.py`` consumes it as ``from
app.reporting.thresholds import PUBLISHER_THRESHOLDS``. Neither the name, the
six keys, their camelCase spelling, their order nor the ``-1`` values may
change. The mapping is read-only: assigning to a key raises ``TypeError``.
"""

# Derived from the mapping rather than restated, so the six key strings exist in
# exactly one place. A tuple, not a list, because this is a constant.
PUBLISHER_THRESHOLD_KEYS: Final[tuple[str, ...]] = tuple(PUBLISHER_THRESHOLDS)
"""The six threshold parameter names, in their ``[Jenkins:L15]`` order."""


# =============================================================================
# The report sort order.
#
# [Jenkins:L15] sortingMethod: 'ALPHABETICAL'
#
# Carried across in upper case, exactly as the publisher spells it. Criterion V4
# compares it for exact string equality, so it is a plain string rather than an
# enumeration member whose rendering could drift from the literal.
#
# The *behaviour* this constant requests -- sorting features by name before they
# are rendered -- is implemented in `app/services/report_service.py`, not here.
# It has to be implemented somewhere even though it is a no-op while the suite
# holds a single feature: leaving it out would turn a preserved setting into a
# divergence that only appears once a second feature is authored.
# =============================================================================

REPORT_SORTING_METHOD: Final[str] = "ALPHABETICAL"
"""Order in which the publisher sorts features before rendering them."""


# =============================================================================
# The report include pattern. Handle with care.
#
# [Jenkins:L15] fileIncludePattern: '**/*.json'
#
# This is the one leading-wildcard string the migration allows to survive into
# the Python tree, and it survives strictly AS DATA: it is a value the pipeline
# hands to its report publisher, never an instruction this application acts on.
# So it is never rewritten, normalised, stripped, split, re-cased, turned into a
# path object, expanded against the filesystem or handed to a pattern-matching
# or regular-expression engine -- not here and not anywhere else in this
# codebase. Its only two jobs are to be reported (the configuration
# introspection endpoint echoes it back verbatim) and to be asserted (criterion
# V4 compares it for exact string equality).
#
# Selecting files is CI's business, not this application's: the publisher runs
# there and does the matching there. The pattern keeps working unedited because
# the port still writes its reports underneath the artifact root whose name
# `app/utils/paths.py` preserves -- which is the entire payoff of retaining that
# Java-flavoured name.
#
# The source declares a second leading-wildcard string, the Surefire include
# pattern that selected the runner class at [pom.xml:L27]. It is deliberately
# absent from this module: it maps to pytest's collection of the ported runner
# module, and it is not a publisher setting.
# =============================================================================

REPORT_FILE_INCLUDE_PATTERN: Final[str] = "**/*.json"
"""Pattern the publisher uses to find report files. Data only -- never expanded."""
