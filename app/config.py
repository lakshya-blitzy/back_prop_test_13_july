"""Resolved application configuration: every source default plus the five-rung chain.

What this module is
-------------------
This is the single place where a literal read out of the pre-migration Java/Maven
project becomes a Python value the Flask application can act on. The source system had
three configuration surfaces and no others: the Groovy pipeline ``Jenkins``, the Maven
``pom.xml`` and the prose of ``README.md``. Every threshold, URL, tag expression, boolean
and sort order in those files is carried across below as an explicit, typed default with
its source file and line cited in a comment immediately above it.

That discipline is AAP Rule T1 -- "configuration values are data, not decisions" -- and
this module is the densest concentration of it in the whole port: no value is rounded,
renamed, re-cased, "modernised" or quietly improved, and the four values that look like
mistakes are mistakes that are preserved on purpose (see *Preserved defects* below).

The five-rung precedence chain
------------------------------
Resolution order, highest wins::

    explicit constructor argument       # Config(TAG_EXPRESSION="Login")
      -> environment variable           # os.environ
        -> .env file                    # python-dotenv, repository root
          -> configuration.properties   # git-ignored [.gitignore:L3], normally ABSENT
            -> hard-coded default       # the source literal, declared in this module

The chain is written down rather than left to emerge, so parity is deterministic. Each
rung is *independently observable*: :meth:`Config.provenance_of` reports which rung
supplied any given setting, which is what makes the ordering testable instead of merely
asserted.

Two properties of the chain deserve emphasis:

* **Presence beats emptiness.** A key that is present with an empty value is a *decision*,
  not an omission, so resolution stops there. ``TAG_EXPRESSION=`` therefore runs the whole
  suite (it clears the preserved tag filter) and ``SELENIUM_REMOTE_URL=`` means "no remote
  grid". Only a genuinely absent key falls through to the next rung.
* **Values are taken verbatim.** Nothing is stripped, case-folded or normalised, because
  one of them -- the 29-byte French assertion literal of ``[README.md:L135]`` -- must
  survive byte for byte, trailing period included. The committed templates forbid trailing
  whitespace precisely so that verbatim reading is safe.

``.env`` is loaded here and **only** here, with ``python-dotenv==1.2.2``. ``wsgi.py``,
``run.py`` and ``gunicorn.conf.py`` deliberately do not load it, so there is exactly one
load point and the ordering cannot become ambiguous. The file is parsed with
:func:`dotenv.dotenv_values` into a private mapping consulted as rung three; the process
environment is never mutated. That reproduces python-dotenv's ``override=False`` semantics
by *ordering* -- a real environment variable still wins -- while keeping this module free
of global side effects, which matters because the test suite runs under ``pytest-xdist``
and builds many application instances in one process.

``configuration.properties`` is read exclusively through :mod:`app.utils.properties`,
never by opening the file here. That module parses the Java ``.properties`` format with
the standard library's :mod:`configparser` plus a synthesized section header, so the whole
contract of ``[.gitignore:L3]`` costs zero extra dependencies, and it never raises: a
missing, unreadable or malformed file degrades to an empty mapping. Because that ignore
line keeps the file untracked, **absence is the common path, not the edge case** -- a
fresh checkout, a CI job and a container all start life without it, and every setting
still resolves.

What this module deliberately does not do
-----------------------------------------
* It never builds a Flask application. ``create_app(config_name)`` in ``app/__init__.py``
  does that and consumes a class or instance from here via ``config.from_object``.
* It never configures logging. It obtains a module logger and emits records;
  ``app/logging_config.py`` owns handlers, levels and formats. It never prints.
* It never creates the ``target/`` tree. That belongs to ``app/utils/paths.py``, the
  ``Makefile`` ``test`` target and ``tests/conftest.py``, which guarantee it independently.
* It never evaluates a threshold, derives a build verdict or maps an exit code. The
  non-gating exit-code policy (pytest ``1`` non-failing, ``5`` a successful zero-scenario
  run) is ``app/services/test_runner_service.py``'s.
* It holds no setting the source system never had: no database, cache, broker or queue URL,
  no Jira client settings, no cross-browser matrix, no vault integration, and no invented
  address for the external application under test (AAP Rule T6 -- no fabrication).

Preserved defects (AAP Rule T4, "defects are behavior")
-------------------------------------------------------
Four preserved defects surface as defaults in this module, each flagged in a banner at its
declaration so that no future maintainer "fixes" one and breaks the acceptance criteria:

* **D2** -- :data:`DEFAULT_TAG_EXPRESSION` selects zero scenarios.
* **D3** -- :data:`DEFAULT_IGNORE_TEST_FAILURES` plus the six ``-1`` thresholds mean the
  build can never fail.
* **D5** -- :data:`DEFAULT_EXPECTED_EMPTY_FIELD_MESSAGE` is the French assertion, not the
  English comment beside it.
* **D7** -- :data:`DEFAULT_CLONE_URL` disagrees with the URL the README documents, which is
  retained as :data:`DOCUMENTED_CLONE_URL` rather than silently unified.

``docs/migration-parity.md`` is the authoritative register of defects D1 through D9 and
records the configuration switch that opts into each available fix. Every one of the four
above is overridable through the chain; none is corrected by default.

Layering
--------
AAP Rule T7 fixes the direction ``api -> services -> reporting -> utils``. This module sits
beside the factory and may import the standard library, ``python-dotenv``, ``flask``
itself (only :class:`flask.Flask`, and only for its ``default_config`` key names -- reading
that class attribute constructs nothing), ``app.utils.*`` and ``app.reporting.thresholds``
-- nothing else. It must never import
``app.api``, ``app.web``, ``app.services`` or the ``app`` package root (the factory imports
*this* module, so any of those would close a cycle at start-up), and never anything under
``tests/`` or ``scripts/``. It imports no harness distribution -- no ``pytest``,
``pytest_bdd``, ``selenium``, ``webdriver_manager`` or ``faker`` -- because the deployed
container installs ``requirements.txt`` alone and the chain ``wsgi.py`` ->
``app/__init__.py`` -> this module has to import cleanly there.

Usage
-----
::

    from app.config import get_config, load_config

    # In the application factory:
    #   app.config.from_object(load_config(config_name))
    settings = load_config("production")
    settings.TAG_EXPRESSION              # 'LogOut'  -- preserved defect D2
    settings.PUBLISHER_THRESHOLDS        # six -1 values, publisher's own key names
    settings.provenance_of("TAG_EXPRESSION")  # Provenance.DEFAULT

    get_config("testing")                # the class, for `from_object` on a class
"""

import logging
import os
from collections.abc import Iterable, Iterator, Mapping
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import ClassVar, Final

from dotenv import dotenv_values
from flask import Flask

# The publisher constants are IMPORTED, never restated: `app/reporting/thresholds.py` is
# the single module that declares the eight parameters of the report-publication call at
# [Jenkins:L15], and this module defaults from it. The `SOURCE_` prefix keeps each imported
# literal distinguishable from the configuration attribute that defaults from it, so no
# name is ever shadowed and the direction of the dependency stays obvious in every line.
from app.reporting.thresholds import PUBLISHER_THRESHOLD_KEYS
from app.reporting.thresholds import PUBLISHER_THRESHOLDS as SOURCE_PUBLISHER_THRESHOLDS
from app.reporting.thresholds import REPORT_FAILED_FEATURES_NUMBER as SOURCE_FAILED_FEATURES_NUMBER
from app.reporting.thresholds import (
    REPORT_FAILED_SCENARIOS_NUMBER as SOURCE_FAILED_SCENARIOS_NUMBER,
)
from app.reporting.thresholds import REPORT_FAILED_STEPS_NUMBER as SOURCE_FAILED_STEPS_NUMBER
from app.reporting.thresholds import REPORT_FILE_INCLUDE_PATTERN as SOURCE_FILE_INCLUDE_PATTERN
from app.reporting.thresholds import REPORT_PENDING_STEPS_NUMBER as SOURCE_PENDING_STEPS_NUMBER
from app.reporting.thresholds import REPORT_SKIPPED_STEPS_NUMBER as SOURCE_SKIPPED_STEPS_NUMBER
from app.reporting.thresholds import REPORT_SORTING_METHOD as SOURCE_SORTING_METHOD
from app.reporting.thresholds import REPORT_UNDEFINED_STEPS_NUMBER as SOURCE_UNDEFINED_STEPS_NUMBER
from app.utils.paths import (
    CUCUMBER_HTML_NAME,
    CUCUMBER_JSON_NAME,
    ERROR_SHOTS_DIR_NAME,
    PRETTY_REPORTS_DIR_NAME,
    RERUN_TXT_NAME,
    SCREENSHOTS_DIR_NAME,
    SUREFIRE_JUNIT_XML_NAME,
    SUREFIRE_REPORTS_DIR_NAME,
    to_posix,
)
from app.utils.paths import TARGET_DIR as DEFAULT_TARGET_DIR
from app.utils.properties import (
    clear_cache as clear_properties_cache,
)
from app.utils.properties import (
    default_properties_path,
    load_properties,
)

# The public surface, ordered as two ASCII-sorted groups: the module-level source-default
# constants first, then the classes and factory helpers. Names re-exported from
# `app.utils.paths`, `app.utils.properties` and `app.reporting.thresholds` are deliberately
# absent -- importers must take those from their owning module so the single-source-of-truth
# boundaries described in the module docstring stay visible at every call site. The one
# exception is `DEFAULT_TARGET_DIR`, which this module publishes because it is the base every
# artifact default is composed from and is therefore part of the configuration contract.
__all__ = [
    "BOOLEAN_FALSE_LITERALS",
    "BOOLEAN_TRUE_LITERALS",
    "CONFIG_MAP",
    "CONFIG_NAMES",
    "CONFIG_NAME_ENV_VAR",
    "DEFAULT_APP_HOST",
    "DEFAULT_APP_PORT",
    "DEFAULT_BROWSER",
    "DEFAULT_CLONE_BRANCH",
    "DEFAULT_CLONE_DIR",
    "DEFAULT_CLONE_INDEX",
    "DEFAULT_CLONE_TIMEOUT_SECONDS",
    "DEFAULT_CLONE_URL",
    "DEFAULT_CONFIGURATION_PROPERTIES_PATH",
    "DEFAULT_CONFIG_NAME",
    "DEFAULT_CORS_ORIGINS",
    "DEFAULT_CUCUMBER_HTML_PATH",
    "DEFAULT_CUCUMBER_JSON_PATH",
    "DEFAULT_DOTENV_FILENAME",
    "DEFAULT_DRIVER_MANAGER",
    "DEFAULT_ERROR_SHOTS_DIR",
    "DEFAULT_ERROR_SHOTS_ENABLED",
    "DEFAULT_EXPECTED_EMPTY_FIELD_MESSAGE",
    "DEFAULT_EXPLICIT_WAIT_SECONDS",
    "DEFAULT_GHERKIN_TERMINAL_REPORTER",
    "DEFAULT_HEADLESS",
    "DEFAULT_IGNORE_TEST_FAILURES",
    "DEFAULT_IMPLICIT_WAIT_SECONDS",
    "DEFAULT_LOG_FILE",
    "DEFAULT_LOG_LEVEL",
    "DEFAULT_PAGE_LOAD_TIMEOUT_SECONDS",
    "DEFAULT_PRETTY_REPORTS_DIR",
    "DEFAULT_PYTEST_WORKERS",
    "DEFAULT_RERUN_TXT_PATH",
    "DEFAULT_SCREENSHOTS_DIR",
    "DEFAULT_SCREENSHOTS_ENABLED",
    "DEFAULT_SUREFIRE_REPORTS_DIR",
    "DEFAULT_TAG_EXPRESSION",
    "DEFAULT_TARGET_DIR",
    "DEFAULT_TEST_TIMEOUT_SECONDS",
    "DEFAULT_THREAD_COUNT_ENABLED",
    "DEVELOPMENT_SECRET_KEY",
    "DISABLED_THREAD_COUNT",
    "DOCUMENTED_CLONE_URL",
    "FLASK_CONFIG_KEYS",
    "PRECEDENCE_CHAIN",
    "Config",
    "ConfigurationError",
    "DevelopmentConfig",
    "ProductionConfig",
    "Provenance",
    "TestingConfig",
    "clear_config_caches",
    "dotenv_path",
    "get_config",
    "load_config",
    "load_dotenv_values",
    "project_root",
    "resolve_config_name",
]

# Structured logging only. The logger is obtained, never configured: handler, level and
# format belong exclusively to app/logging_config.py, and this module never prints
# (AAP 0.7 baseline: "structured logging rather than print statements").
_LOGGER: Final[logging.Logger] = logging.getLogger(__name__)


# =============================================================================
# Rung bookkeeping.
#
# Each resolved setting records WHICH rung supplied its value. Without that
# record the precedence chain could only be described, never demonstrated; with
# it, `tests/unit/test_config.py` can assert the ordering rung by rung and the
# five-rung contract of AAP 0.3.1 becomes executable rather than aspirational.
# =============================================================================


class Provenance(StrEnum):
    """Which rung of the precedence chain supplied a resolved value.

    A :class:`enum.StrEnum` rather than a bare string so the vocabulary is closed and
    checkable, while every member still compares and serialises as its own lower-case
    name -- handy for log records and for a diagnostic HTTP payload.
    """

    OVERRIDE = "override"
    """Rung 1 -- an explicit keyword argument passed to the configuration class."""

    ENVIRONMENT = "environment"
    """Rung 2 -- a variable present in the process environment."""

    DOTENV = "dotenv"
    """Rung 3 -- an entry of the optional ``.env`` file."""

    PROPERTIES = "properties"
    """Rung 4 -- a key of the optional, git-ignored ``configuration.properties``."""

    DEFAULT = "default"
    """Rung 5 -- the hard-coded source default declared in this module."""


PRECEDENCE_CHAIN: Final[tuple[Provenance, ...]] = (
    Provenance.OVERRIDE,
    Provenance.ENVIRONMENT,
    Provenance.DOTENV,
    Provenance.PROPERTIES,
    Provenance.DEFAULT,
)
"""The five rungs in resolution order, highest precedence first (AAP 0.3.1).

``explicit constructor argument -> environment variable -> .env file ->
configuration.properties -> hard-coded default``. Published as data so a test can assert
the order instead of restating it.
"""


# The literals recognised when a text value has to become a boolean. A CLOSED set
# on purpose: Java's `Boolean.parseBoolean` treats every non-"true" string as
# false, which silently turns a typo into a decision, whereas anything outside
# the two sets below is reported and falls through to the next rung. The same
# vocabulary is used by the harness-side reader (tests/support/config_reader.py),
# so one spelling works in both trees. `[pom.xml:L25]`'s
# <testFailureIgnore>true</testFailureIgnore> and every boolean in the two
# committed templates are the lower-case spellings.
BOOLEAN_TRUE_LITERALS: Final[frozenset[str]] = frozenset({"true", "yes", "on", "1"})
"""Text values accepted as ``True``, matched case-insensitively."""

BOOLEAN_FALSE_LITERALS: Final[frozenset[str]] = frozenset({"false", "no", "off", "0"})
"""Text values accepted as ``False``, matched case-insensitively."""


# =============================================================================
# Locating the two optional files.
#
# Both are anchored on this module's own location rather than on the process
# working directory, so the same paths resolve whether the application was
# started by `flask run`, by gunicorn from a container WORKDIR, or by pytest from
# a subdirectory. app/config.py -> parents[0] is app/, parents[1] is the
# repository root.
# =============================================================================

DEFAULT_DOTENV_FILENAME: Final[str] = ".env"
"""Name of the optional environment file, git-ignored and normally absent."""

DEFAULT_CONFIGURATION_PROPERTIES_PATH: Final[Path] = default_properties_path()
"""Default location of the optional ``configuration.properties`` ``[.gitignore:L3]``.

Obtained from :mod:`app.utils.properties`, which owns the file name, so the location is
declared in exactly one place. The call is pure -- it composes a path from the module's own
location and touches no filesystem -- so importing this module remains side-effect free.
"""

_PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parents[1]

# Shared read-only empty mapping, handed back by every degradation path. One instance
# avoids allocating on the common absent-file path and, being read-only, it can never be
# polluted by a caller between reads.
_EMPTY_MAPPING: Final[Mapping[str, str]] = MappingProxyType({})


def project_root() -> Path:
    """Return the absolute repository root.

    Returns:
        The directory that contains ``app/``, ``pytest.ini`` and the two committed
        configuration templates. Derived from this module's location, never from the
        process working directory.
    """
    return _PROJECT_ROOT


def dotenv_path() -> Path:
    """Return the absolute path of the optional ``.env`` file.

    Returns:
        ``<repository root>/.env``. Existence is neither checked nor implied: the file is
        git-ignored, so a fresh checkout does not contain it, and only
        ``.env.example`` is tracked.
    """
    return _PROJECT_ROOT / DEFAULT_DOTENV_FILENAME


def load_dotenv_values(path: str | os.PathLike[str] | None = None) -> Mapping[str, str]:
    """Parse the optional ``.env`` file into a read-only mapping.

    This is the application's single ``.env`` load point (``wsgi.py``, ``run.py`` and
    ``gunicorn.conf.py`` deliberately do not load it). The process environment is **not**
    mutated: the parsed values are consulted as rung three of the chain, which reproduces
    python-dotenv's ``override=False`` behaviour by ordering -- a real environment variable
    still wins -- without the global side effect that would make two configuration
    instances in one process influence each other.

    Args:
        path: File to parse. Defaults to :func:`dotenv_path`.

    Returns:
        A read-only mapping of the file's entries, or an empty mapping when the file is
        absent or unreadable. ``KEY=`` yields ``""``, which the chain honours as a
        deliberate empty value; a bare ``KEY`` with no ``=`` carries no decision and is
        dropped. Never raises.
    """
    target = dotenv_path() if path is None else Path(path)
    try:
        # Explicit UTF-8 so values are encoding-stable on every platform; python-dotenv
        # returns {} for a missing file rather than raising.
        parsed = dotenv_values(target, encoding="utf-8")
    except OSError as exc:
        # An unreadable or otherwise hostile path is treated exactly like an absent file:
        # the environment file is optional, so it can never break start-up.
        _LOGGER.debug(
            "Optional environment file %s could not be read (%s: %s); continuing without it",
            to_posix(target),
            type(exc).__name__,
            exc,
        )
        return _EMPTY_MAPPING
    # python-dotenv models "KEY" with no "=" as None; the chain only understands text, and
    # a key with no value carries no decision, so those entries are dropped.
    values = {key: value for key, value in parsed.items() if value is not None}
    _LOGGER.debug("Loaded %d entries from %s", len(values), to_posix(target))
    return MappingProxyType(values) if values else _EMPTY_MAPPING


# =============================================================================
# Configuration profiles.
#
# `create_app(config_name)` is handed a profile NAME, so the mapping from name to
# class lives here. APP_CONFIG is an environment-only key: it selects the profile
# that decides how the rest of the configuration is read, so it cannot itself be
# read from `configuration.properties`, and the committed properties template
# records it under "DELIBERATELY NOT IN THIS FILE".
# =============================================================================

CONFIG_NAME_ENV_VAR: Final[str] = "APP_CONFIG"
"""Environment key naming the configuration profile, per ``.env.example`` section 1."""

DEFAULT_CONFIG_NAME: Final[str] = "development"
"""Profile used when none is given, matching ``APP_CONFIG=development`` in ``.env.example``.

The container overrides it: the ``Dockerfile`` and ``docker-compose.yml`` both select
``production``, so this default only ever applies to a working copy.
"""


# =============================================================================
# SECTION 1 -- Flask application. ADDITIVE.
#
# The source system exposed no HTTP surface at all: its orchestration was the
# Groovy scripted pipeline `node { ... }` [Jenkins:L1-L17]. The deliverable is
# defined as a Python 3 Flask application, so every default in this section is
# ADDITIVE -- required for the application to exist, ported from nothing.
# =============================================================================

FLASK_CONFIG_KEYS: Final[frozenset[str]] = frozenset(Flask.default_config)
"""Flask's own configuration keys, read from the framework rather than restated.

An override whose name is one of these is a deliberate Flask setting rather than a
misspelled setting of this module, so it is applied without a warning. Deriving the set from
``Flask.default_config`` -- a class attribute, so reading it constructs no application --
keeps it correct across Flask upgrades instead of drifting from a hand-maintained list.
"""

DEFAULT_APP_HOST: Final[str] = "0.0.0.0"
"""Listen address. ``0.0.0.0`` so a container or compose service is reachable from the host.

Matches ``APP_HOST`` in ``.env.example``; narrow it to ``127.0.0.1`` for a machine-local
deployment.
"""

DEFAULT_APP_PORT: Final[int] = 8000
"""Base listen port, matching ``APP_PORT`` in ``.env.example``.

The effective default is ``DEFAULT_APP_PORT + CLONE_INDEX``, which is exactly how the
``Makefile`` (``APP_PORT ?= $(shell echo $$((8000 + $(CLONE_INDEX))))``) and
``gunicorn.conf.py`` compute it, so parallel clones of the repository do not collide. An
explicit ``APP_PORT`` outranks the offset in all three places.
"""

DEVELOPMENT_SECRET_KEY: Final[str] = "change-me-in-production"
"""Obvious NON-SECRET placeholder for the Flask signing key.

This is not a credential and must never become one: it is the same visible placeholder the
committed ``.env.example`` and the ``docker-compose.yml`` fallback carry, so all three
agree. :class:`DevelopmentConfig` and :class:`TestingConfig` fall back to it;
:class:`ProductionConfig` requires a value supplied through the environment and warns
loudly when this placeholder is what it finds. Generate a real key with
``python -c "import secrets; print(secrets.token_hex(32))"`` and keep it out of git.
"""

DEFAULT_CLONE_INDEX: Final[int] = 0
"""Offset applied to host-global resources so parallel clones do not collide. ADDITIVE.

Nothing in the source system had to coexist with a second copy of itself -- a Jenkins
workspace was exclusive -- so this is ADDITIVE. The effective listen port becomes
``DEFAULT_APP_PORT + CLONE_INDEX``, matching the ``Makefile`` and ``gunicorn.conf.py``, and
the compose project name becomes ``testinium-qa-$CLONE_INDEX``. Environment-only by design,
which is why the committed properties template lists it under "DELIBERATELY NOT IN THIS
FILE".
"""

DEFAULT_CORS_ORIGINS: Final[tuple[str, ...]] = ("*",)
"""Cross-origin policy for the report-retrieval routes, matching ``CORS_ORIGINS=*``.

``*`` is flask-cors's own default and is appropriate here because those endpoints are
read-only, unauthenticated artifact downloads that carry no cookies. Resolved into a
``list[str]``: flask-cors reads ``CORS_ORIGINS`` from the application config natively but
does **not** split a comma-separated string -- it would treat ``a,b`` as one origin -- so
the splitting happens in this module.
"""


# =============================================================================
# SECTION 2 -- Stage 'Clone code'. Port of [Jenkins:L2-L4].
# =============================================================================

# -----------------------------------------------------------------------------
# PRESERVED DEFECT D7 -- TWO CONTRADICTORY REPOSITORY URLS.
#
# The pipeline clones
#
#     git 'https://github.com/BalamiRR/Upgenix-QA.git'        [Jenkins:L3]
#
# while the README's "Framework set up" section instructs cloning
#
#     git clone https://github.com/BalamiRR/Testinium-QA.git  [README.md:L59]
#
# Per AAP AMB-7 the PIPELINE value is the runtime default: executable
# configuration outranks prose when the two disagree, and the Jira key prefix
# `UPGN` [README.md:L115] agrees with the pipeline URL. Both strings are retained
# rather than silently unified, so no information is lost -- the discrepancy is
# documented, not resolved away. `docs/migration-parity.md` is the authoritative
# register of defects D1 through D9.
#
# To clone the repository the README documents, either set CLONE_URL to
# DOCUMENTED_CLONE_URL's value through the chain or uncomment the commented-out
# alternative that both committed templates already carry:
#
#     # CLONE_URL=https://github.com/BalamiRR/Testinium-QA.git   (.env.example)
#     #clone.url=https://github.com/BalamiRR/Testinium-QA.git    (properties)
# -----------------------------------------------------------------------------

DEFAULT_CLONE_URL: Final[str] = "https://github.com/BalamiRR/Upgenix-QA.git"
"""Repository the ported ``'Clone code'`` stage checks out. Source: ``[Jenkins:L3]``."""

DOCUMENTED_CLONE_URL: Final[str] = "https://github.com/BalamiRR/Testinium-QA.git"
"""The *other* URL the source names, kept machine-readable. Source: ``[README.md:L59]``.

Preserved defect **D7**. Reported alongside the URL in force by the configuration
endpoint, so the disagreement stays visible instead of being quietly reconciled.
"""

DEFAULT_CLONE_BRANCH: Final[str] = "main"
"""Branch to check out.

The Groovy ``git '<url>'`` step ``[Jenkins:L3]`` passes no explicit ref, so it takes the
remote's default branch; ``main`` is that branch for both repositories the source names,
and the README links ``.../archive/main.zip`` ``[README.md:L63]``.
"""

DEFAULT_CLONE_DIR: Final[str] = "workspace"
"""Working-copy destination, relative to the repository root. ADDITIVE.

The Groovy step wrote into the Jenkins workspace, which has no analogue in a plain Python
process, so the port names a directory of its own.
"""

DEFAULT_CLONE_TIMEOUT_SECONDS: Final[int] = 300
"""Timeout for the clone subprocess, in whole seconds. ADDITIVE but REQUIRED.

``app/services/clone_service.py`` always runs ``git`` under an explicit timeout: a clone
that blocked for ever would hang the whole ported pipeline.
"""


# =============================================================================
# SECTION 3 -- Stage 'Run tests'. Port of [Jenkins:L6-L11] and of the Maven
# Surefire configuration [pom.xml:L21-L29].
# =============================================================================

# -----------------------------------------------------------------------------
# PRESERVED DEFECT D2 -- THE DEFAULT TAG EXPRESSION SELECTS ZERO SCENARIOS.
#
# Faithful port of the documented runner's
#
#     tags = "@LogOut"                                        [README.md:L87]
#
# pytest-bdd turns every Gherkin tag into a pytest mark and STRIPS the leading
# `@`, which is why the value below carries none; `pytest.ini` spells the same
# selection as `-m "LogOut"`.
#
# NO SCENARIO CARRIES THAT TAG. The feature file's only tags are @Login,
# @UPGN-286, @UPGN-287, @UPGN-288, @SalesManager and @PosManager
# [README.md:L104-L148], so a default run reports "6 deselected, 0 selected" and
# pytest exits with code 5, "no tests ran".
#
# EXIT CODE 5 MUST BE REPORTED AS SUCCESS -- a successful zero-scenario run
# (validation criterion V6). The source pipeline went green in exactly this
# situation: Surefire simply found nothing matching the tag filter, and the
# publisher's all-`-1` thresholds gated nothing. Mapping 5 to failure would make
# the port fail where the original succeeded, which AAP 0.6 calls "the most
# consequential single-line decision in the port". That mapping lives in
# `app/services/test_runner_service.py`; this module only supplies the default
# that triggers it.
#
# Set the value to an EMPTY string -- `TAG_EXPRESSION=` in `.env`, or
# `tag.expression=` in `configuration.properties` -- to clear the filter and run
# the whole suite. An empty value is honoured as a decision, not read as absent.
# -----------------------------------------------------------------------------

DEFAULT_TAG_EXPRESSION: Final[str] = "LogOut"
"""Tag selector applied to the ported BDD run. Source: ``tags = "@LogOut"`` ``[README.md:L87]``.

Preserved defect **D2**: it matches no scenario, so the documented invocation runs nothing
and succeeds.
"""

# -----------------------------------------------------------------------------
# PRESERVED DEFECT D3 -- THE BUILD CAN NEVER FAIL.
#
# Direct port of
#
#     <testFailureIgnore>true</testFailureIgnore>             [pom.xml:L25]
#
# which made the source build swallow test failures outright. It compounds with
# the six publisher thresholds of [Jenkins:L15], every one of them `-1`, a value
# the Cucumber publisher reads as "no threshold" and which can therefore never be
# exceeded. The source system consequently had NO build-time quality gate at all,
# and neither does this port.
#
# While this setting is true the ported runner maps pytest's exit codes as:
#
#     0 -> success                    all selected scenarios passed
#     1 -> success, NON-GATING        scenarios ran and some failed
#     5 -> success, zero scenarios    everything deselected (defect D2 above)
#     2 -> hard failure               execution interrupted
#     3 -> hard failure               internal error
#     4 -> hard failure               usage / command-line error
#
# Report generation additionally runs even after a FAILED test stage, because
# `app/services/pipeline_service.py` invokes it from a finally-equivalent path
# (validation criterion V12).
#
# Set it to false to make failures gate the pipeline. That is a deliberate
# DEVIATION from source behaviour, not a fix; record it in
# `docs/migration-parity.md` if you take it.
# -----------------------------------------------------------------------------

DEFAULT_IGNORE_TEST_FAILURES: Final[bool] = True
"""Whether test failures are tolerated. Source: ``[pom.xml:L25]``. Preserved defect **D3**."""

DEFAULT_PYTEST_WORKERS: Final[str] = "logical"
"""pytest-xdist worker allocation, the ACTIVE parallelism setting.

Port of ``<parallel>methods</parallel>`` plus
``<useUnlimitedThreads>true</useUnlimitedThreads>`` ``[pom.xml:L22-L23]``: ``logical`` lets
xdist size the pool from the machine, the closest available analogue of "unlimited
threads". Parallelism does not compromise report fidelity -- a parallel run still emits a
complete ``target/cucumber.json`` containing every scenario.
"""

# -----------------------------------------------------------------------------
# PRESERVED-BUT-DISABLED TUNING DEFAULT -- the commented-out thread count.
#
# The source keeps its thread count commented out, exactly like this:
#
#     <!--                    <threadCount>4</threadCount>-->  [pom.xml:L24]
#
# AAP goal O7 requires it to be "preserved as a documented, disabled tuning
# default", so the value is recorded below together with the flag that says it is
# NOT in force. It is documentation with a value, not a knob that is on: the
# active parallelism setting stays DEFAULT_PYTEST_WORKERS ("logical").
#
# Both committed templates preserve the same disabled line -- `# PYTEST_WORKERS=4`
# in `.env.example` and `#pytest.workers=4` in `configuration.properties.example`
# -- so pinning four workers is a one-line, deliberate opt-in through the ordinary
# chain rather than a hidden default. There is therefore no configuration key of
# its own for the thread count, which is exactly why the value is spelled out
# here as a constant instead of being resolved.
#
# `app/api/schemas.py` declares the same literal as the default of its
# `ThreadCountSetting` response model. The two files cannot import each other --
# `api` sits above configuration in the dependency direction `api -> services ->
# reporting -> utils`, so an import either way would close a cycle -- so both read
# the value from [pom.xml:L24] independently and MUST agree. Change one only by
# changing the other.
# -----------------------------------------------------------------------------

DISABLED_THREAD_COUNT: Final[int] = 4
"""The thread count the source kept commented out at ``[pom.xml:L24]``, carried across.

Reported as inactive; see :data:`DEFAULT_THREAD_COUNT_ENABLED`.
"""

DEFAULT_THREAD_COUNT_ENABLED: Final[bool] = False
"""Whether :data:`DISABLED_THREAD_COUNT` is in force. ``False`` preserves the source state."""

DEFAULT_TEST_TIMEOUT_SECONDS: Final[int] = 1800
"""Timeout for the whole pytest invocation, in whole seconds. ADDITIVE guard rail.

Applies to the entire run rather than to a single scenario, so a hung browser session
cannot stall the ported ``'Run tests'`` stage for ever.
"""

DEFAULT_GHERKIN_TERMINAL_REPORTER: Final[bool] = False
"""Whether pytest-bdd's Gherkin terminal reporter is requested. OFF, and that is a HARD constraint.

The reporter cannot coexist with pytest-xdist -- it raises during configuration and aborts
the run with an internal error -- and the default invocation is parallel because
parallelism is itself a parity requirement ``[pom.xml:L22-L23]``. ``make test-pretty`` is
the serial escape hatch (validation criterion V8). The PrettyReports analogue of
``[README.md:L82]`` does not depend on this switch: it is produced by post-processing
``target/cucumber.json``, exactly as the Java reporting plugin ``[pom.xml:L66-L70]``
rendered its directory from the JSON.
"""


# =============================================================================
# SECTION 4 -- Screen shots and error shots. [README.md:L42-L43]:
#
#     "It also generate `screen shots` for your tests if you enable it and also
#      generate `error shots` for your failed test cases as well."
#
# The two defaults below follow that wording literally: shots of passing steps
# are OPT-IN ("if you enable it"), error shots for failed test cases are
# described unconditionally and are therefore ON.
# =============================================================================

DEFAULT_SCREENSHOTS_ENABLED: Final[bool] = False
"""Whether screen shots are captured for passing tests. Opt-in. Source: ``[README.md:L42]``."""

DEFAULT_ERROR_SHOTS_ENABLED: Final[bool] = True
"""Whether error shots are captured for failures. On. Source: ``[README.md:L43]``."""


# =============================================================================
# SECTION 5 -- Browser automation.
#
# The source project's prerequisite list asked for a browser driver with its
# class path already set:
#
#     "5. Browser driver (make sure you have your desired browser driver and
#      class path is set)"                                    [README.md:L53]
#
# The settings below replace that manual step with configuration. The harness
# reads them through `tests/support/config_reader.py`; they are declared here so
# there is one settings surface rather than two.
# =============================================================================

DEFAULT_BROWSER: Final[str] = "chrome"
"""Browser to drive. Deliberately SINGLE-VALUED: a cross-browser matrix is out of scope."""

DEFAULT_HEADLESS: Final[bool] = True
"""Whether the browser runs headless. ADDITIVE; on, because build environments have no display."""

DEFAULT_DRIVER_MANAGER: Final[str] = "selenium-manager"
"""Driver provisioning strategy: ``selenium-manager`` or ``webdriver-manager``.

Selenium 4 resolves drivers itself through Selenium Manager; the ``webdriver-manager``
distribution is retained as the one-to-one counterpart of
``io.github.bonigarcia:webdrivermanager`` ``[pom.xml:L42-L46]``.
"""

DEFAULT_IMPLICIT_WAIT_SECONDS: Final[int] = 10
"""Implicit wait budget, in seconds. ADDITIVE; kept only as a backstop."""

DEFAULT_EXPLICIT_WAIT_SECONDS: Final[int] = 30
"""Explicit wait budget, in seconds. ADDITIVE; the larger of the pair, and the one page objects use."""

DEFAULT_PAGE_LOAD_TIMEOUT_SECONDS: Final[int] = 60
"""Page-load timeout, in seconds. ADDITIVE."""


# =============================================================================
# SECTION 6 -- Behaviour-parity literal.
#
# PRESERVED DEFECT D5 -- THE LOCALISATION AND THE COMMENT DISAGREE.
#
# The feature file's explanatory comment says the expected text is English:
#
#     #3- "Please fill out this field" message should be displayed if the
#         password or username is empty                       [README.md:L130]
#
# while the ASSERTION five lines later expects French:
#
#     Then User sees "Veuillez renseigner ce champ." message   [README.md:L135]
#
# The assertion is the executable truth, so the French string is the value. It is
# 29 bytes and pure ASCII -- French-language, but not multi-byte. COMPARE IT
# EXACTLY: do not translate it to match the comment, do not drop the trailing
# period, and do not normalise, strip or case-fold it. That is why this module
# reads every value verbatim.
# =============================================================================

DEFAULT_EXPECTED_EMPTY_FIELD_MESSAGE: Final[str] = "Veuillez renseigner ce champ."
"""Message asserted for an empty login field. Source: ``[README.md:L135]``. Defect **D5**."""


# =============================================================================
# SECTION 7 -- Logging.
# =============================================================================

DEFAULT_LOG_LEVEL: Final[str] = "INFO"
"""Standard-library level name consumed by ``app/logging_config.py``.

``gunicorn.conf.py`` falls back to the same key for its own error log, so the HTTP server
tracks the application's verbosity instead of drifting away from it.
"""

DEFAULT_LOG_FILE: Final[Path] = Path("logs") / "testinium-qa.log"
"""Destination of the structured log.

The ``.log`` suffix is deliberate: the repository has ignored ``*.log`` since before the
migration ``[.gitignore:L6]``, so this path stays untracked with no new ignore rule. It
sits OUTSIDE the artifact root on purpose -- everything under ``target/`` is wiped by the
port of ``mvn clean`` before every run, which would take the log with it.
"""


# =============================================================================
# SECTION 8 -- Artifact layout.
#
# The four report artifacts are declared by the documented runner's plugin list:
#
#     "html:target/cucumber-reports.html",                    [README.md:L79]
#     "json:target/cucumber.json",                            [README.md:L80]
#     "rerun:target/rerun.txt",                               [README.md:L81]
#     "me.jvt.cucumber.report.PrettyReports:target/cucumber"   [README.md:L82]
#
# (AAP 0.4.1 cites this block as L78-L81; the lines above are the byte-verified
# positions in the file, and `app/utils/paths.py` cites them the same way.)
#
# THE ARTIFACT ROOT IS NEVER RENAMED. Keeping the Java-flavoured `target` name is
# precisely why the CI publisher's `fileIncludePattern: '**/*.json'` [Jenkins:L15]
# needs no edit at all -- the single largest saving of the whole migration.
#
# `app/utils/paths.py` owns the root name and every file and directory name
# beneath it, so NO PATH LITERAL IS WRITTEN IN THIS MODULE: the defaults below are
# composed from that module's constants. The same composition is used at
# resolution time, which is what lets an overridden TARGET_DIR re-base the entire
# layout instead of leaving the individual artifacts stranded under the old root.
# =============================================================================

DEFAULT_CUCUMBER_JSON_PATH: Final[Path] = DEFAULT_TARGET_DIR / CUCUMBER_JSON_NAME
"""``target/cucumber.json`` ``[README.md:L80]`` -- the artifact the publisher glob matches."""

DEFAULT_CUCUMBER_HTML_PATH: Final[Path] = DEFAULT_TARGET_DIR / CUCUMBER_HTML_NAME
"""``target/cucumber-reports.html`` ``[README.md:L79]``, served as a static artifact."""

DEFAULT_RERUN_TXT_PATH: Final[Path] = DEFAULT_TARGET_DIR / RERUN_TXT_NAME
"""``target/rerun.txt`` ``[README.md:L81]``, derived from the JSON report."""

DEFAULT_PRETTY_REPORTS_DIR: Final[Path] = DEFAULT_TARGET_DIR / PRETTY_REPORTS_DIR_NAME
"""``target/cucumber`` ``[README.md:L82]`` -- the PrettyReports output directory."""

DEFAULT_SCREENSHOTS_DIR: Final[Path] = DEFAULT_TARGET_DIR / SCREENSHOTS_DIR_NAME
"""``target/screenshots`` ``[README.md:L42]``."""

DEFAULT_ERROR_SHOTS_DIR: Final[Path] = DEFAULT_TARGET_DIR / ERROR_SHOTS_DIR_NAME
"""``target/error-shots`` ``[README.md:L43]``."""

DEFAULT_SUREFIRE_REPORTS_DIR: Final[Path] = DEFAULT_TARGET_DIR / SUREFIRE_REPORTS_DIR_NAME
"""``target/surefire-reports`` -- the name is retained for report-consumer parity.

Populated by the JUnit-XML output the runner requests, so anything downstream keyed on that
path keeps finding content.
"""


# =============================================================================
# Failures.
# =============================================================================


class ConfigurationError(RuntimeError):
    """Raised when the configuration cannot be resolved into a usable state.

    Deliberately rare. Almost every problem this module can meet degrades instead of
    raising -- an absent file, an unreadable file, a malformed value all fall through to
    the next rung with a logged warning -- because the two optional files are optional by
    design and a fresh checkout has neither. Only two situations are genuinely
    unrecoverable and raise:

    * a keyword argument that cannot be a configuration key at all (a non-upper-case name),
      which is a programming mistake rather than a deployment one, and
    * :class:`ProductionConfig` built with no signing key, where continuing would mean
      inventing a secret.
    """


# =============================================================================
# The precedence engine.
#
# One instance is built per configuration object. It holds the four *sources* --
# the explicit overrides, the process environment, the parsed `.env` and the
# parsed `configuration.properties` -- walks them in the fixed order of
# PRECEDENCE_CHAIN, coerces the first usable candidate to the requested type, and
# records which rung won so the ordering can be asserted afterwards.
#
# Coercion failure is a FALL-THROUGH, not an error: a malformed value at one rung
# is reported and the walk continues to the next, ending at the hard-coded source
# default. A typo can therefore never masquerade as a decision, and it can never
# stop the application from starting either.
# =============================================================================


class _Resolution(Mapping[str, Provenance]):
    """Read-only record of which rung supplied each setting, in declaration order."""

    def __init__(self, provenance: Mapping[str, Provenance]) -> None:
        self._provenance: Final[dict[str, Provenance]] = dict(provenance)

    def __getitem__(self, key: str) -> Provenance:
        return self._provenance[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._provenance)

    def __len__(self) -> int:
        return len(self._provenance)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._provenance!r})"


class _Resolver:
    """Walks the five rungs for one configuration instance.

    Not part of the public API: consumers see the resolved attributes on a :class:`Config`
    and the provenance record it publishes. Kept as a class rather than a pile of
    functions so that the four sources, the bookkeeping and the coercion rules stay in one
    auditable place.
    """

    def __init__(
        self,
        *,
        overrides: Mapping[str, object],
        environ: Mapping[str, str],
        dotenv: Mapping[str, str],
        properties: Mapping[str, str],
    ) -> None:
        self._overrides = overrides
        self._environ = environ
        self._dotenv = dotenv
        self._properties = properties
        # Insertion-ordered, so `env_keys()` reports the settings in the order this module
        # declares them -- which is the order of the source sections they came from.
        self._provenance: dict[str, Provenance] = {}
        self._property_keys: dict[str, str] = {}
        self._consumed_overrides: set[str] = set()

    # -- bookkeeping -------------------------------------------------------------

    @property
    def provenance(self) -> Mapping[str, Provenance]:
        """Rung that supplied each setting, keyed by environment key."""
        return _Resolution(self._provenance)

    @property
    def property_keys(self) -> tuple[str, ...]:
        """Every ``configuration.properties`` key consulted, in declaration order."""
        return tuple(self._property_keys.values())

    @property
    def unused_overrides(self) -> tuple[str, ...]:
        """Override keys that no setting claimed, sorted for a stable diagnostic."""
        return tuple(sorted(set(self._overrides) - self._consumed_overrides))

    def provenance_of(self, env_key: str) -> Provenance:
        """Return the rung that supplied *env_key*, which must already be resolved."""
        return self._provenance[env_key]

    def _record(self, env_key: str, property_key: str | None, provenance: Provenance) -> None:
        self._provenance[env_key] = provenance
        if property_key is not None:
            self._property_keys[env_key] = property_key
        if provenance is Provenance.OVERRIDE:
            self._consumed_overrides.add(env_key)

    def _reject(self, env_key: str, provenance: Provenance, value: object, expected: str) -> None:
        """Report a candidate that cannot be coerced, then let the walk continue.

        Only the key, the rung and the expected type are logged -- never the value, which
        could carry a credential an operator embedded in a URL.
        """
        _LOGGER.warning(
            "Configuration key %s from the %s rung is not a valid %s (got %s); "
            "falling through to the next rung",
            env_key,
            provenance.value,
            expected,
            type(value).__name__,
        )

    def _candidates(
        self, env_key: str, property_key: str | None
    ) -> Iterator[tuple[object, Provenance]]:
        """Yield the configured candidates for one setting, highest precedence first.

        Presence is what counts, not truthiness: a key present with an empty value is a
        deliberate decision -- ``TAG_EXPRESSION=`` clears the preserved tag filter -- so it
        is yielded and stops the walk. Only genuinely absent keys fall through.
        """
        if env_key in self._overrides:
            yield self._overrides[env_key], Provenance.OVERRIDE
        if env_key in self._environ:
            yield self._environ[env_key], Provenance.ENVIRONMENT
        if env_key in self._dotenv:
            yield self._dotenv[env_key], Provenance.DOTENV
        # Rung four is skipped entirely for the settings the committed properties template
        # deliberately does not carry (the process- and deployment-level ones).
        if property_key is not None and property_key in self._properties:
            yield self._properties[property_key], Provenance.PROPERTIES

    # -- typed accessors ---------------------------------------------------------

    def text(self, env_key: str, property_key: str | None, default: str) -> str:
        """Resolve a text setting. Values are taken VERBATIM -- never stripped or re-cased."""
        for value, provenance in self._candidates(env_key, property_key):
            self._record(env_key, property_key, provenance)
            return value if isinstance(value, str) else str(value)
        self._record(env_key, property_key, Provenance.DEFAULT)
        return default

    def optional_text(
        self, env_key: str, property_key: str | None, default: str | None = None
    ) -> str | None:
        """Resolve a text setting where an empty value means "not configured".

        Used for the two settings whose committed templates document them empty or
        commented out: the address of the external application under test and the address
        of an optional remote Selenium grid. An empty string is normalised to ``None`` here
        -- and only here -- because "" is not a usable URL, while the *presence* of the key
        still stops the walk.
        """
        for value, provenance in self._candidates(env_key, property_key):
            self._record(env_key, property_key, provenance)
            text = value if isinstance(value, str) else str(value)
            return text or None
        self._record(env_key, property_key, Provenance.DEFAULT)
        return default

    def override_flag(self, key: str, default: bool) -> bool:
        """Resolve a boolean from rung ONE only, without registering *key* as a setting.

        Used for the two keys that are Flask's own rather than this module's -- ``DEBUG`` and
        ``TESTING`` -- whose documented environment spellings are ``FLASK_DEBUG`` and the
        selected profile respectively. Consuming them here rather than letting them arrive as
        unclaimed overrides is what keeps the profile's debug policy authoritative: an
        unclaimed override is applied *after* resolution and would otherwise overwrite the
        gated value.

        The key is marked consumed but deliberately kept out of the provenance record,
        because :meth:`Config.env_keys` reports the environment keys documented in the
        committed ``.env.example`` and these two are not among them.

        Args:
            key: Override key to look for.
            default: Value to keep when the key is absent or uncoercible.

        Returns:
            The coerced override, or *default*.
        """
        if key not in self._overrides:
            return default
        value = self._overrides[key]
        coerced = _as_flag(value)
        self._consumed_overrides.add(key)
        if coerced is None:
            self._reject(key, Provenance.OVERRIDE, value, "boolean")
            return default
        return coerced

    def flag(self, env_key: str, property_key: str | None, default: bool) -> bool:
        """Resolve a boolean setting against the closed vocabulary of accepted literals."""
        for value, provenance in self._candidates(env_key, property_key):
            coerced = _as_flag(value)
            if coerced is None:
                self._reject(env_key, provenance, value, "boolean")
                continue
            self._record(env_key, property_key, provenance)
            return coerced
        self._record(env_key, property_key, Provenance.DEFAULT)
        return default

    def whole_number(
        self,
        env_key: str,
        property_key: str | None,
        default: int,
        *,
        minimum: int | None = None,
    ) -> int:
        """Resolve an integer setting.

        Args:
            env_key: Environment key documented in ``.env.example``.
            property_key: ``configuration.properties`` key, or ``None`` when the committed
                properties template deliberately does not carry this setting.
            default: The hard-coded source default.
            minimum: Smallest accepted value, when one applies. Deliberately ``None`` for
                the six publisher thresholds, whose source value is ``-1``.

        Returns:
            The first candidate that parses as an integer and satisfies *minimum*, or
            *default*.
        """
        for value, provenance in self._candidates(env_key, property_key):
            coerced = _as_whole_number(value)
            if coerced is None:
                self._reject(env_key, provenance, value, "integer")
                continue
            if minimum is not None and coerced < minimum:
                _LOGGER.warning(
                    "Configuration key %s from the %s rung is below the minimum of %d; "
                    "falling through to the next rung",
                    env_key,
                    provenance.value,
                    minimum,
                )
                continue
            self._record(env_key, property_key, provenance)
            return coerced
        self._record(env_key, property_key, Provenance.DEFAULT)
        return default

    def filesystem_path(self, env_key: str, property_key: str | None, default: Path) -> Path:
        """Resolve a filesystem path setting. Relative paths stay relative, by design.

        ``target/cucumber.json`` is exactly how ``pytest.ini``, the ``Makefile``, the CI
        publisher and both committed templates spell it, so resolving to an absolute path
        here would make the reported layout stop matching them.
        """
        for value, provenance in self._candidates(env_key, property_key):
            coerced = _as_path(value)
            if coerced is None:
                self._reject(env_key, provenance, value, "filesystem path")
                continue
            self._record(env_key, property_key, provenance)
            return coerced
        self._record(env_key, property_key, Provenance.DEFAULT)
        return default

    def origins(
        self, env_key: str, property_key: str | None, default: tuple[str, ...]
    ) -> list[str]:
        """Resolve a comma-separated origin list into the list flask-cors expects.

        flask-cors reads ``CORS_ORIGINS`` from the application config natively, but it does
        **not** split a comma-separated string: it would treat ``a,b`` as a single origin.
        The split therefore happens here, and ``*`` survives as a one-element list, which
        is what the library turns into its allow-all pattern.
        """
        for value, provenance in self._candidates(env_key, property_key):
            coerced = _as_origins(value)
            if coerced is None:
                self._reject(env_key, provenance, value, "origin list")
                continue
            self._record(env_key, property_key, provenance)
            return coerced
        self._record(env_key, property_key, Provenance.DEFAULT)
        return list(default)


def _as_flag(value: object) -> bool | None:
    """Coerce *value* to a boolean, or return ``None`` when it is not a recognised literal.

    Surrounding whitespace is tolerated and case is ignored on a throwaway copy; the stored
    value itself is never rewritten.
    """
    if isinstance(value, bool):
        return value
    text = (value if isinstance(value, str) else str(value)).strip().casefold()
    if text in BOOLEAN_TRUE_LITERALS:
        return True
    if text in BOOLEAN_FALSE_LITERALS:
        return False
    return None


def _as_whole_number(value: object) -> int | None:
    """Coerce *value* to an integer, or return ``None`` when it cannot be one.

    ``bool`` is rejected rather than silently read as ``0``/``1``: a boolean supplied for a
    count is a mistake worth reporting, and Python's ``bool`` is an ``int`` subclass, so
    the check has to come first.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = (value if isinstance(value, str) else str(value)).strip()
    try:
        return int(text)
    except ValueError:
        return None


def _as_path(value: object) -> Path | None:
    """Coerce *value* to a :class:`pathlib.Path`, or return ``None`` when it cannot be one.

    An empty or blank value yields ``None``: unlike a text setting, an empty path carries
    no decision -- it would silently relocate an artifact to the working directory.
    """
    if isinstance(value, Path):
        return value
    if isinstance(value, os.PathLike):
        return Path(value)
    text = (value if isinstance(value, str) else str(value)).strip()
    if not text:
        return None
    try:
        return Path(text)
    except ValueError:
        # `Path` rejects a string containing a NUL byte with ValueError. `text` is always a
        # string here, so TypeError cannot occur and is deliberately not caught.
        return None


def _owned(value: object) -> object:
    """Return a plain, caller-owned container for *value*, or *value* itself when scalar.

    Used only by :meth:`Config.as_dict`, whose contract is "a freshly built mapping the
    caller owns". :attr:`Config.PUBLISHER_THRESHOLDS` is a
    :class:`types.MappingProxyType` so the parity contract of ``[Jenkins:L15]`` cannot be
    mutated in-process, but a read-only proxy is not a ``dict`` subclass, so handing it
    straight to a diagnostic consumer is a trap: Flask's JSON provider raises on it, turning
    a configuration endpoint into a 500. Copying it here keeps the attribute immutable while
    making the diagnostic view ordinary.

    Scalars, :class:`pathlib.Path` values and ``str`` are returned untouched, because
    :meth:`Config.as_dict` documents that values keep their resolved Python types and that a
    JSON caller renders paths through :meth:`Config.artifact_paths_posix`.
    """
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str | bytes | Path):
        return value
    # Every other container is copied, not shared. `CORS_ORIGINS` is a real `list` on the
    # instance, so returning it as-is would let a caller that appended to the diagnostic
    # payload silently widen the application's cross-origin policy.
    if isinstance(value, list | tuple | frozenset | set):
        return list(value)
    return value


def _as_origins(value: object) -> list[str] | None:
    """Coerce *value* to a list of origins, or return ``None`` when nothing usable is left.

    Accepts either a comma-separated string (the form both the environment and a
    ``.properties`` file can express) or an already-iterable value, which is what an
    explicit constructor override is most likely to pass.
    """
    if isinstance(value, str):
        candidates: list[str] = value.split(",")
    elif isinstance(value, Iterable):
        candidates = [item if isinstance(item, str) else str(item) for item in value]
    else:
        return None
    origins = [origin.strip() for origin in candidates if origin.strip()]
    return origins or None


# =============================================================================
# The configuration classes.
#
# Flask's `config.from_object` copies every UPPER_CASE attribute it can see, so
# every setting below is UPPER_CASE and every diagnostic is not: the provenance
# record, the consulted keys and the profile policy stay off the application
# config, where they would only be noise.
#
# Values are resolved in __init__ -- on the INSTANCE -- rather than at class
# definition time. That is deliberate:
#
#   * `create_app(config_name)` builds a fresh application per call and
#     `tests/conftest.py` builds one per test, each needing the environment as it
#     stands at that moment. Class-level resolution would freeze the environment
#     as it stood at import time, and a test that sets an environment variable
#     could never be honoured.
#   * Nothing is read from the filesystem or the environment merely by importing
#     this module.
#
# The class body still declares every setting, typed, with its hard-coded source
# default, so `from_object(SomeConfig)` on the CLASS degrades to exactly the source
# defaults instead of yielding an empty configuration -- and so a reader sees the
# whole settings surface in one place. Those declarations reference the DEFAULT_*
# constants above; no literal is written twice.
# =============================================================================


class Config:
    """Every source default, resolved through the five-rung precedence chain.

    One instance is a complete, immutable-in-practice snapshot of the configuration in
    force: ``explicit constructor argument -> environment variable -> .env file ->
    configuration.properties -> hard-coded default`` (AAP 0.3.1). Hand it to Flask with
    ``app.config.from_object(instance)``.

    Overrides are spelled exactly like the environment keys the committed ``.env.example``
    documents, because those keys *are* the setting names::

        Config(TAG_EXPRESSION="", IGNORE_TEST_FAILURES=False)

    A key that is not one of this module's settings is still applied when it is upper case
    -- Flask configuration keys such as ``TESTING`` reach the application that way -- and a
    key that is not upper case raises :class:`ConfigurationError`, because it cannot be a
    configuration key at all.

    Attributes are documented at their declarations below; the constants they default from
    each carry the file and line of the source construct they were read from. Nothing here
    creates a Flask application, configures logging, creates the ``target/`` tree, or
    evaluates a threshold: those belong to ``app/__init__.py``, ``app/logging_config.py``,
    ``app/utils/paths.py`` and ``app/services/report_service.py`` respectively.

    The base class is directly usable and reports the profile name ``base``; the three
    deployable profiles are :class:`DevelopmentConfig`, :class:`TestingConfig` and
    :class:`ProductionConfig`, resolved by name through :func:`get_config`.
    """

    # -- profile policy -------------------------------------------------------
    # ClassVars, never assigned per instance: they describe the PROFILE, not a setting, and
    # only the three subclasses vary them. Every preserved parity constant is identical in
    # all profiles (validation criterion V4 asserts the constants regardless of
    # environment); only operational behaviour differs.

    _PROFILE_NAME: ClassVar[str] = "base"
    """Canonical name reported as :attr:`CONFIG_NAME`."""

    _TESTING: ClassVar[bool] = False
    """Whether the profile puts Flask into testing mode."""

    _ALLOW_DEBUG: ClassVar[bool] = True
    """Whether ``FLASK_DEBUG`` may switch the interactive debugger on for this profile."""

    _REQUIRE_SECRET_KEY: ClassVar[bool] = False
    """Whether the profile refuses to start without a supplied signing key."""

    # Overrides applied without a warning because they are ordinary Flask configuration
    # keys rather than typos: the factory and the test fixtures legitimately pass them.
    _FLASK_PASSTHROUGH_KEYS: ClassVar[frozenset[str]] = FLASK_CONFIG_KEYS

    # Never exposed by :meth:`as_dict`, so an introspection payload or a log record cannot
    # carry the signing key.
    _SECRET_ATTRIBUTES: ClassVar[frozenset[str]] = frozenset({"SECRET_KEY"})

    # -- Flask application. ADDITIVE: the source system had no HTTP surface. ---

    CONFIG_NAME: str = "base"
    """Name of the profile in force; the configuration endpoint reports it as ``environment``."""

    DEBUG: bool = False
    """Interactive debugger. OFF by default in every profile, and refused outright in production."""

    TESTING: bool = False
    """Flask testing mode. Only :class:`TestingConfig` turns it on."""

    SECRET_KEY: str = DEVELOPMENT_SECRET_KEY
    """Session-signing key. Read from the environment; never a real credential in source."""

    CORS_ORIGINS: list[str] = list(DEFAULT_CORS_ORIGINS)
    """Allowed origins, read natively by flask-cors from this key.

    A list rather than a string because flask-cors does not split a comma-separated value.
    The class-level value exists only for ``from_object`` on the class; every instance
    rebinds it, so the shared list is never mutated.
    """

    APP_HOST: str = DEFAULT_APP_HOST
    """Listen address of both the development server and gunicorn."""

    APP_PORT: int = DEFAULT_APP_PORT
    """Listen port. Defaults to ``DEFAULT_APP_PORT + CLONE_INDEX`` once resolved."""

    CLONE_INDEX: int = DEFAULT_CLONE_INDEX
    """Multi-instance offset for host-global resources. ADDITIVE, environment-only."""

    CONFIGURATION_PROPERTIES_PATH: Path = DEFAULT_CONFIGURATION_PROPERTIES_PATH
    """Location of rung four's optional file ``[.gitignore:L3]``. Environment-only override."""

    # -- Stage 'Clone code'. Port of [Jenkins:L2-L4]. -------------------------

    CLONE_URL: str = DEFAULT_CLONE_URL
    """Repository to clone. Source: ``[Jenkins:L3]``. Preserved defect **D7** -- see the banner."""

    CLONE_URL_DOCUMENTED: str = DOCUMENTED_CLONE_URL
    """The other URL the source names. Source: ``[README.md:L59]``. Preserved defect **D7**."""

    CLONE_BRANCH: str = DEFAULT_CLONE_BRANCH
    """Branch to check out."""

    CLONE_DIR: str = DEFAULT_CLONE_DIR
    """Working-copy destination, relative to the repository root. ADDITIVE."""

    CLONE_TIMEOUT_SECONDS: int = DEFAULT_CLONE_TIMEOUT_SECONDS
    """Timeout of the clone subprocess. ADDITIVE but required."""

    # -- Stage 'Run tests'. Port of [Jenkins:L6-L11] + [pom.xml:L21-L29]. -----

    TAG_EXPRESSION: str = DEFAULT_TAG_EXPRESSION
    """Tag selector. Source: ``[README.md:L87]``. Preserved defect **D2** -- selects nothing."""

    IGNORE_TEST_FAILURES: bool = DEFAULT_IGNORE_TEST_FAILURES
    """Failure tolerance. Source: ``[pom.xml:L25]``. Preserved defect **D3** -- never fails."""

    PYTEST_WORKERS: str = DEFAULT_PYTEST_WORKERS
    """Worker allocation, the ACTIVE parallelism setting. Source: ``[pom.xml:L22-L23]``."""

    THREAD_COUNT: int = DISABLED_THREAD_COUNT
    """The commented-out tuning value ``[pom.xml:L24]``, recorded but NOT in force."""

    THREAD_COUNT_ENABLED: bool = DEFAULT_THREAD_COUNT_ENABLED
    """Whether :attr:`THREAD_COUNT` applies. ``False`` preserves the source's disabled state."""

    TEST_TIMEOUT_SECONDS: int = DEFAULT_TEST_TIMEOUT_SECONDS
    """Timeout of the whole pytest invocation. ADDITIVE guard rail."""

    GHERKIN_TERMINAL_REPORTER: bool = DEFAULT_GHERKIN_TERMINAL_REPORTER
    """Whether the Gherkin terminal reporter is requested. OFF -- it cannot coexist with xdist."""

    # -- Stage 'Generate report'. Port of [Jenkins:L13-L15]. ------------------
    # The six thresholds, the sort order and the include pattern are IMPORTED from
    # `app/reporting/thresholds.py`, the single module that declares them. A literal here
    # would be a second declaration and a divergence waiting to happen; criterion V4
    # compares both against [Jenkins:L15].

    REPORT_FAILED_FEATURES_NUMBER: int = SOURCE_FAILED_FEATURES_NUMBER
    """Publisher limit on failed features. ``-1`` means "no threshold" ``[Jenkins:L15]``."""

    REPORT_FAILED_SCENARIOS_NUMBER: int = SOURCE_FAILED_SCENARIOS_NUMBER
    """Publisher limit on failed scenarios. ``-1`` means "no threshold" ``[Jenkins:L15]``."""

    REPORT_FAILED_STEPS_NUMBER: int = SOURCE_FAILED_STEPS_NUMBER
    """Publisher limit on failed steps. ``-1`` means "no threshold" ``[Jenkins:L15]``."""

    REPORT_PENDING_STEPS_NUMBER: int = SOURCE_PENDING_STEPS_NUMBER
    """Publisher limit on pending steps. ``-1`` means "no threshold" ``[Jenkins:L15]``."""

    REPORT_SKIPPED_STEPS_NUMBER: int = SOURCE_SKIPPED_STEPS_NUMBER
    """Publisher limit on skipped steps. ``-1`` means "no threshold" ``[Jenkins:L15]``."""

    REPORT_UNDEFINED_STEPS_NUMBER: int = SOURCE_UNDEFINED_STEPS_NUMBER
    """Publisher limit on undefined steps. ``-1`` means "no threshold" ``[Jenkins:L15]``."""

    PUBLISHER_THRESHOLDS: Mapping[str, int] = SOURCE_PUBLISHER_THRESHOLDS
    """The six thresholds keyed by the publisher's own camelCase parameter names.

    Rebuilt per instance from the resolved values, using the key tuple exported by
    ``app/reporting/thresholds.py`` so the six spellings and their ``[Jenkins:L15]`` order
    are never restated here. With nothing overridden it equals that module's frozen
    mapping exactly.
    """

    REPORT_SORTING_METHOD: str = SOURCE_SORTING_METHOD
    """Order features are sorted in before rendering. ``ALPHABETICAL`` ``[Jenkins:L15]``.

    Applying it is ``app/services/report_service.py``'s behaviour; this is only the request.
    """

    REPORT_FILE_INCLUDE_PATTERN: str = SOURCE_FILE_INCLUDE_PATTERN
    """The publisher's report-discovery glob ``[Jenkins:L15]``. PRESERVED VERBATIM AS DATA.

    Never expanded against the filesystem, compiled as a regular expression, normalised,
    split or rewritten -- here or anywhere else. Matching files is the CI publisher's job,
    and it still works unedited because the port writes its reports under the artifact root
    whose name ``app/utils/paths.py`` preserves.
    """

    # -- Artifact layout [README.md:L79-L82] + [README.md:L42-L43]. -----------

    TARGET_DIR: Path = DEFAULT_TARGET_DIR
    """The ephemeral artifact root, ``target``. NEVER rename it: ``[Jenkins:L15]`` depends on it."""

    CUCUMBER_JSON_PATH: Path = DEFAULT_CUCUMBER_JSON_PATH
    """``target/cucumber.json`` ``[README.md:L80]``."""

    CUCUMBER_HTML_PATH: Path = DEFAULT_CUCUMBER_HTML_PATH
    """``target/cucumber-reports.html`` ``[README.md:L79]``."""

    RERUN_TXT_PATH: Path = DEFAULT_RERUN_TXT_PATH
    """``target/rerun.txt`` ``[README.md:L81]``."""

    PRETTY_REPORTS_DIR: Path = DEFAULT_PRETTY_REPORTS_DIR
    """``target/cucumber`` ``[README.md:L82]``."""

    SCREENSHOTS_DIR: Path = DEFAULT_SCREENSHOTS_DIR
    """``target/screenshots`` ``[README.md:L42]``."""

    ERROR_SHOTS_DIR: Path = DEFAULT_ERROR_SHOTS_DIR
    """``target/error-shots`` ``[README.md:L43]``."""

    SUREFIRE_REPORTS_DIR: Path = DEFAULT_SUREFIRE_REPORTS_DIR
    """``target/surefire-reports`` -- name retained for report-consumer parity."""

    SUREFIRE_JUNIT_XML_PATH: Path = DEFAULT_SUREFIRE_REPORTS_DIR / SUREFIRE_JUNIT_XML_NAME
    """``target/surefire-reports/TEST-CukesRunner.xml``. DERIVED: it has no configuration key.

    The file name comes from ``app/utils/paths.py``, whose ``TEST-<class>.xml`` spelling is
    Surefire's own convention applied to the runner ``**/CukesRunner*.java`` ``[pom.xml:L27]``
    selected. It follows :attr:`SUREFIRE_REPORTS_DIR` automatically, so neither committed
    template needs a key for it.
    """

    # -- Screen shots and error shots [README.md:L42-L43]. --------------------

    SCREENSHOTS_ENABLED: bool = DEFAULT_SCREENSHOTS_ENABLED
    """Opt-in screen shots for passing tests, exactly as "if you enable it" describes."""

    ERROR_SHOTS_ENABLED: bool = DEFAULT_ERROR_SHOTS_ENABLED
    """Error shots for failed test cases, described unconditionally and therefore on."""

    # -- Browser automation [README.md:L53]. ----------------------------------

    BROWSER: str = DEFAULT_BROWSER
    """The single browser to drive. A cross-browser matrix is out of scope."""

    HEADLESS: bool = DEFAULT_HEADLESS
    """Whether the browser runs headless. ADDITIVE."""

    DRIVER_MANAGER: str = DEFAULT_DRIVER_MANAGER
    """Driver provisioning strategy: ``selenium-manager`` or ``webdriver-manager``."""

    IMPLICIT_WAIT_SECONDS: int = DEFAULT_IMPLICIT_WAIT_SECONDS
    """Implicit wait budget. ADDITIVE backstop."""

    EXPLICIT_WAIT_SECONDS: int = DEFAULT_EXPLICIT_WAIT_SECONDS
    """Explicit wait budget, the one the page objects use. ADDITIVE."""

    PAGE_LOAD_TIMEOUT_SECONDS: int = DEFAULT_PAGE_LOAD_TIMEOUT_SECONDS
    """Page-load timeout. ADDITIVE."""

    BASE_URL: str | None = None
    """Address of the EXTERNAL application under test. NO DEFAULT EXISTS, and none is invented.

    AAP Rule T6 forbids fabrication: no address for that application appears anywhere in the
    source project, the only two URLs in evidence are the two git remotes, and neither is
    it. Both committed templates therefore document the key COMMENTED OUT with an empty
    value. Supply your own environment's address locally to run the browser scenarios; the
    application itself is external, unmodifiable and out of scope, which is why the port's
    parity evidence is structural rather than end-to-end.
    """

    SELENIUM_REMOTE_URL: str | None = None
    """Address of a remote Selenium Grid, or ``None`` for a local browser. ADDITIVE.

    Selenium infrastructure, not the application under test: with the optional compose
    profile the in-network address is ``http://selenium:4444``.
    """

    # -- Behaviour-parity literal. Preserved defect D5. -----------------------

    EXPECTED_EMPTY_FIELD_MESSAGE: str = DEFAULT_EXPECTED_EMPTY_FIELD_MESSAGE
    """The French assertion literal ``[README.md:L135]``, compared exactly. Defect **D5**."""

    # -- Logging. -------------------------------------------------------------

    LOG_LEVEL: str = DEFAULT_LOG_LEVEL
    """Level name consumed by ``app/logging_config.py``; gunicorn falls back to it too."""

    LOG_FILE: Path = DEFAULT_LOG_FILE
    """Structured-log destination. Ends in ``.log`` ``[.gitignore:L6]``, and sits outside ``target/``."""

    def __init__(self, **overrides: object) -> None:
        """Resolve every setting for this profile.

        Args:
            **overrides: Rung-one values, keyed exactly like the environment keys the
                committed ``.env.example`` documents (for example ``TAG_EXPRESSION=""``).
                Upper-case keys that are not settings of this module are applied to the
                instance verbatim, so ordinary Flask keys such as ``TESTING`` still reach
                the application.

        Raises:
            ConfigurationError: If a keyword argument is not upper case, so it cannot be a
                configuration key; or if this profile requires a signing key and none was
                supplied.
        """
        checked = self._checked_overrides(overrides)

        # Rung 3, parsed once per instance. The process environment is never mutated: the
        # ordering below is what reproduces python-dotenv's `override=False` semantics.
        dotenv = load_dotenv_values()

        # Rung 4's CONTENT cannot be read before its LOCATION is known, and the location
        # cannot come from the file it locates. The mapping is therefore handed to the
        # resolver empty and filled in place immediately afterwards, which keeps ONE
        # resolver -- and therefore one provenance record -- for the whole instance.
        properties: dict[str, str] = {}
        resolver = _Resolver(
            overrides=checked,
            environ=os.environ,
            dotenv=dotenv,
            properties=properties,
        )

        # `property_key=None` skips rung 4 for this setting, exactly as the committed
        # properties template documents: "It LOCATES this file, so setting it inside this
        # file cannot work. It is an environment-only override."
        self.CONFIGURATION_PROPERTIES_PATH = resolver.filesystem_path(
            "CONFIGURATION_PROPERTIES_PATH", None, DEFAULT_CONFIGURATION_PROPERTIES_PATH
        )
        # `load_properties` never raises: an absent, unreadable or malformed file yields an
        # empty mapping, and absence is the COMMON case because `[.gitignore:L3]` keeps the
        # file untracked. Rung 4 then simply contributes nothing and every setting falls
        # through to its hard-coded source default.
        properties.update(load_properties(self.CONFIGURATION_PROPERTIES_PATH))

        self._resolve_application(resolver)
        self._resolve_clone_stage(resolver)
        self._resolve_test_stage(resolver)
        self._resolve_publisher(resolver)
        self._resolve_artifacts(resolver)
        self._resolve_shots(resolver)
        self._resolve_browser(resolver)
        self._resolve_parity_literals(resolver)
        self._resolve_logging(resolver)

        # Rung one wins even for keys this module does not own, so an unclaimed override is
        # applied last of all.
        self._apply_unclaimed_overrides(checked, resolver.unused_overrides)

        # Diagnostics, deliberately lower-case so Flask's `from_object` leaves them out of
        # the application config.
        self._provenance: Mapping[str, Provenance] = resolver.provenance
        self._property_keys: tuple[str, ...] = resolver.property_keys

        _LOGGER.debug(
            "Resolved %d settings for the %s profile (%d from %s, %d from the environment)",
            len(self._provenance),
            self.CONFIG_NAME,
            sum(1 for rung in self._provenance.values() if rung is Provenance.DEFAULT),
            Provenance.DEFAULT.value,
            sum(1 for rung in self._provenance.values() if rung is Provenance.ENVIRONMENT),
        )

    # -- override handling ----------------------------------------------------

    @staticmethod
    def _checked_overrides(overrides: Mapping[str, object]) -> Mapping[str, object]:
        """Reject keyword arguments that cannot be configuration keys at all.

        Every setting of this module is named exactly like the environment key the
        committed ``.env.example`` documents, and those keys are upper case. A lower-case
        or mixed-case keyword is therefore a programming mistake -- ``tag_expression``
        instead of ``TAG_EXPRESSION`` -- and silently ignoring it would let a caller believe
        an override took effect when it did not.

        Args:
            overrides: The keyword arguments as received.

        Returns:
            The same mapping, unchanged, once every key has been accepted.

        Raises:
            ConfigurationError: If any key is not upper case.
        """
        invalid = sorted(key for key in overrides if not key.isupper())
        if invalid:
            raise ConfigurationError(
                "Configuration overrides are spelled exactly like their environment keys, "
                "in upper case (for example TAG_EXPRESSION); rejected: " + ", ".join(invalid)
            )
        return overrides

    def _apply_unclaimed_overrides(
        self, overrides: Mapping[str, object], unclaimed: tuple[str, ...]
    ) -> None:
        """Apply upper-case overrides that are not settings of this module.

        Rung one outranks every other rung, including for keys this module does not own, so
        an unclaimed override is applied last of all and therefore wins. Ordinary Flask
        keys (``DEBUG``, ``TESTING``) travel this path routinely and are logged at debug
        level; anything else is logged as a warning, because it is far more likely to be a
        misspelled setting name than a deliberate Flask key.

        Args:
            overrides: The full override mapping.
            unclaimed: Keys that no setting claimed during resolution.
        """
        for key in unclaimed:
            setattr(self, key, overrides[key])
            if key in self._FLASK_PASSTHROUGH_KEYS:
                _LOGGER.debug("Applied Flask configuration override %s", key)
            else:
                _LOGGER.warning(
                    "Configuration override %s is not one of this module's settings; it has "
                    "been applied verbatim as an application configuration value",
                    key,
                )

    def _apply_secret_key_policy(self, provenance: Provenance) -> None:
        """Enforce this profile's rule for the Flask signing key.

        The key is never hard-coded as a real secret: :data:`DEVELOPMENT_SECRET_KEY` is a
        visible placeholder that the two development-oriented profiles accept so a working
        copy and the test suite need no setup. A profile that requires the key refuses to
        start without one, because the alternative -- inventing a key per process -- would
        silently break session signing across gunicorn workers while looking healthy.

        The placeholder is accepted with a warning rather than rejected even in production,
        because ``docker-compose.yml`` documents exactly that fallback
        (``SECRET_KEY: ${SECRET_KEY:-change-me-in-production}``) and refusing it would break
        the documented deployment. The warning is what makes the situation discoverable.

        Args:
            provenance: The rung that supplied :attr:`SECRET_KEY`.

        Raises:
            ConfigurationError: If this profile requires a key and none was supplied.
        """
        if not self._REQUIRE_SECRET_KEY:
            return
        if provenance is Provenance.DEFAULT or not self.SECRET_KEY:
            raise ConfigurationError(
                f"The {self._PROFILE_NAME} profile requires SECRET_KEY to be supplied through "
                "the environment (or an explicit override). Generate one with "
                'python -c "import secrets; print(secrets.token_hex(32))" and keep it out of '
                "version control."
            )
        if self.SECRET_KEY == DEVELOPMENT_SECRET_KEY:
            _LOGGER.warning(
                "SECRET_KEY is the committed development placeholder while the %s profile is "
                "in force; supply a real key through the environment before exposing this "
                "service",
                self._PROFILE_NAME,
            )

    # -- resolution steps, one per section of the committed templates ----------

    def _resolve_application(self, resolver: _Resolver) -> None:
        """Resolve the ADDITIVE Flask-application settings.

        The source system exposed no HTTP surface at all ``[Jenkins:L1-L17]``, so every
        setting here is additive: it exists because the deliverable is a Flask application.
        Every setting here is environment-only -- process- and deployment-level values that
        the committed properties template deliberately does not carry.
        """
        self.CONFIG_NAME = self._PROFILE_NAME

        # Flask's own key, and therefore NOT an environment key of this module: the profile
        # decides testing mode, and `.env.example` documents no TESTING key. It is still
        # honoured as an explicit rung-one override -- `tests/conftest.py` passes it -- and
        # coerced like every other flag, so `TESTING="1"` cannot land on the instance as a
        # string that Flask would read as truthy for the wrong reason.
        self.TESTING = resolver.override_flag("TESTING", self._TESTING)

        # Resolved before APP_PORT because it shifts that port's default, exactly as the
        # Makefile (APP_PORT ?= 8000 + CLONE_INDEX) and gunicorn.conf.py do.
        self.CLONE_INDEX = resolver.whole_number(
            "CLONE_INDEX", None, DEFAULT_CLONE_INDEX, minimum=0
        )
        self.APP_HOST = resolver.text("APP_HOST", None, DEFAULT_APP_HOST)
        self.APP_PORT = resolver.whole_number(
            "APP_PORT", None, DEFAULT_APP_PORT + self.CLONE_INDEX, minimum=1
        )
        self.CORS_ORIGINS = resolver.origins("CORS_ORIGINS", None, DEFAULT_CORS_ORIGINS)

        # Debug is OFF by default in every profile: Flask's debugger offers an interactive
        # console to anyone who can reach the port, and the committed template pins
        # FLASK_DEBUG=0 for the same reason.
        requested_debug = resolver.flag("FLASK_DEBUG", None, False)
        # `DEBUG` is Flask's own spelling of the same switch. `.env.example` documents only
        # FLASK_DEBUG, so DEBUG is accepted as an explicit rung-one override and nothing
        # else -- but it is consumed HERE rather than left to arrive as an unclaimed
        # override, because an unclaimed override is applied after resolution and would
        # otherwise sail straight past the profile gate below. Production can therefore
        # never be talked into serving the interactive debugger, whichever key asks.
        requested_debug = resolver.override_flag("DEBUG", requested_debug)
        if requested_debug and not self._ALLOW_DEBUG:
            _LOGGER.warning(
                "Debug mode was requested through DEBUG or FLASK_DEBUG, which the %s "
                "profile does not permit; debug stays off",
                self._PROFILE_NAME,
            )
            requested_debug = False
        self.DEBUG = requested_debug

        self.SECRET_KEY = resolver.text("SECRET_KEY", None, DEVELOPMENT_SECRET_KEY)
        self._apply_secret_key_policy(resolver.provenance_of("SECRET_KEY"))

    def _resolve_clone_stage(self, resolver: _Resolver) -> None:
        """Resolve stage ``'Clone code'`` ``[Jenkins:L2-L4]``.

        Both repository URLs are resolved, not just the one in force: preserved defect
        **D7** is two contradictory URLs, and reporting both is what keeps the discrepancy
        documented instead of silently unified.
        """
        self.CLONE_URL = resolver.text("CLONE_URL", "clone.url", DEFAULT_CLONE_URL)
        self.CLONE_URL_DOCUMENTED = resolver.text(
            "CLONE_URL_DOCUMENTED", "clone.url.documented", DOCUMENTED_CLONE_URL
        )
        self.CLONE_BRANCH = resolver.text("CLONE_BRANCH", "clone.branch", DEFAULT_CLONE_BRANCH)
        self.CLONE_DIR = resolver.text("CLONE_DIR", "clone.dir", DEFAULT_CLONE_DIR)
        self.CLONE_TIMEOUT_SECONDS = resolver.whole_number(
            "CLONE_TIMEOUT_SECONDS",
            "clone.timeout.seconds",
            DEFAULT_CLONE_TIMEOUT_SECONDS,
            minimum=1,
        )

    def _resolve_test_stage(self, resolver: _Resolver) -> None:
        """Resolve stage ``'Run tests'`` ``[Jenkins:L6-L11]`` and ``[pom.xml:L21-L29]``.

        Two preserved defects live here: **D2**, the tag expression that selects no
        scenario, and **D3**, the failure tolerance that makes the build unable to fail.
        Both are defaults, both are overridable, and neither is corrected.
        """
        self.TAG_EXPRESSION = resolver.text(
            "TAG_EXPRESSION", "tag.expression", DEFAULT_TAG_EXPRESSION
        )
        self.IGNORE_TEST_FAILURES = resolver.flag(
            "IGNORE_TEST_FAILURES", "ignore.test.failures", DEFAULT_IGNORE_TEST_FAILURES
        )
        self.PYTEST_WORKERS = resolver.text(
            "PYTEST_WORKERS", "pytest.workers", DEFAULT_PYTEST_WORKERS
        )
        self.TEST_TIMEOUT_SECONDS = resolver.whole_number(
            "TEST_TIMEOUT_SECONDS", "test.timeout.seconds", DEFAULT_TEST_TIMEOUT_SECONDS, minimum=1
        )
        # Environment-only: the reporter is a local convenience with a hard incompatibility
        # (see the constant's docstring), so the properties template carries no key for it.
        self.GHERKIN_TERMINAL_REPORTER = resolver.flag(
            "GHERKIN_TERMINAL_REPORTER", None, DEFAULT_GHERKIN_TERMINAL_REPORTER
        )

        # The commented-out tuning value of [pom.xml:L24] is RECORDED, never RESOLVED: it
        # has no configuration key precisely because it is not in force, and both committed
        # templates preserve it as a commented-out line. Pinning four workers is therefore a
        # deliberate one-line opt-in through PYTEST_WORKERS, never a default (AAP goal O7).
        self.THREAD_COUNT = DISABLED_THREAD_COUNT
        self.THREAD_COUNT_ENABLED = DEFAULT_THREAD_COUNT_ENABLED

    def _resolve_publisher(self, resolver: _Resolver) -> None:
        """Resolve stage ``'Generate report'`` ``[Jenkins:L13-L15]``.

        No ``minimum`` is imposed on the six thresholds: their source value is ``-1``, which
        the publisher reads as "no threshold", and a bound would reject the very value being
        preserved (defect **D3**).
        """
        self.REPORT_FAILED_FEATURES_NUMBER = resolver.whole_number(
            "REPORT_FAILED_FEATURES_NUMBER",
            "report.failed.features.number",
            SOURCE_FAILED_FEATURES_NUMBER,
        )
        self.REPORT_FAILED_SCENARIOS_NUMBER = resolver.whole_number(
            "REPORT_FAILED_SCENARIOS_NUMBER",
            "report.failed.scenarios.number",
            SOURCE_FAILED_SCENARIOS_NUMBER,
        )
        self.REPORT_FAILED_STEPS_NUMBER = resolver.whole_number(
            "REPORT_FAILED_STEPS_NUMBER", "report.failed.steps.number", SOURCE_FAILED_STEPS_NUMBER
        )
        self.REPORT_PENDING_STEPS_NUMBER = resolver.whole_number(
            "REPORT_PENDING_STEPS_NUMBER",
            "report.pending.steps.number",
            SOURCE_PENDING_STEPS_NUMBER,
        )
        self.REPORT_SKIPPED_STEPS_NUMBER = resolver.whole_number(
            "REPORT_SKIPPED_STEPS_NUMBER",
            "report.skipped.steps.number",
            SOURCE_SKIPPED_STEPS_NUMBER,
        )
        self.REPORT_UNDEFINED_STEPS_NUMBER = resolver.whole_number(
            "REPORT_UNDEFINED_STEPS_NUMBER",
            "report.undefined.steps.number",
            SOURCE_UNDEFINED_STEPS_NUMBER,
        )
        self.REPORT_SORTING_METHOD = resolver.text(
            "REPORT_SORTING_METHOD", "report.sorting.method", SOURCE_SORTING_METHOD
        )
        self.REPORT_FILE_INCLUDE_PATTERN = resolver.text(
            "REPORT_FILE_INCLUDE_PATTERN",
            "report.file.include.pattern",
            SOURCE_FILE_INCLUDE_PATTERN,
        )

        # The six resolved values in the order of [Jenkins:L15], paired with the key tuple
        # `app/reporting/thresholds.py` exports so the publisher's camelCase spellings are
        # never restated in this module. `strict=True` turns a length mismatch into an
        # immediate error instead of a silently truncated mapping, and MappingProxyType
        # makes the result genuinely read-only: this mapping is reachable from the
        # configuration endpoint, where a careless handler could otherwise mutate the parity
        # contract in-process for every later request.
        resolved_thresholds = (
            self.REPORT_FAILED_FEATURES_NUMBER,
            self.REPORT_FAILED_SCENARIOS_NUMBER,
            self.REPORT_FAILED_STEPS_NUMBER,
            self.REPORT_PENDING_STEPS_NUMBER,
            self.REPORT_SKIPPED_STEPS_NUMBER,
            self.REPORT_UNDEFINED_STEPS_NUMBER,
        )
        self.PUBLISHER_THRESHOLDS = MappingProxyType(
            dict(zip(PUBLISHER_THRESHOLD_KEYS, resolved_thresholds, strict=True))
        )

    def _resolve_artifacts(self, resolver: _Resolver) -> None:
        """Resolve the artifact layout ``[README.md:L79-L82]`` and ``[README.md:L42-L43]``.

        Each artifact defaults to a path *inside the resolved root*, so overriding
        ``TARGET_DIR`` re-bases the whole layout instead of stranding the individual
        artifacts under the old root, while a single artifact can still be relocated on its
        own. The file and directory names come from ``app/utils/paths.py``; no path literal
        is written in this module.
        """
        target_dir = resolver.filesystem_path("TARGET_DIR", "target.dir", DEFAULT_TARGET_DIR)
        self.TARGET_DIR = target_dir
        self.CUCUMBER_JSON_PATH = resolver.filesystem_path(
            "CUCUMBER_JSON_PATH", "cucumber.json.path", target_dir / CUCUMBER_JSON_NAME
        )
        self.CUCUMBER_HTML_PATH = resolver.filesystem_path(
            "CUCUMBER_HTML_PATH", "cucumber.html.path", target_dir / CUCUMBER_HTML_NAME
        )
        self.RERUN_TXT_PATH = resolver.filesystem_path(
            "RERUN_TXT_PATH", "rerun.txt.path", target_dir / RERUN_TXT_NAME
        )
        self.PRETTY_REPORTS_DIR = resolver.filesystem_path(
            "PRETTY_REPORTS_DIR", "pretty.reports.dir", target_dir / PRETTY_REPORTS_DIR_NAME
        )
        self.SCREENSHOTS_DIR = resolver.filesystem_path(
            "SCREENSHOTS_DIR", "screenshots.dir", target_dir / SCREENSHOTS_DIR_NAME
        )
        self.ERROR_SHOTS_DIR = resolver.filesystem_path(
            "ERROR_SHOTS_DIR", "error.shots.dir", target_dir / ERROR_SHOTS_DIR_NAME
        )
        self.SUREFIRE_REPORTS_DIR = resolver.filesystem_path(
            "SUREFIRE_REPORTS_DIR", "surefire.reports.dir", target_dir / SUREFIRE_REPORTS_DIR_NAME
        )
        # DERIVED, deliberately without a configuration key of its own: it follows the
        # resolved Surefire directory, so neither committed template needs a key for it.
        self.SUREFIRE_JUNIT_XML_PATH = self.SUREFIRE_REPORTS_DIR / SUREFIRE_JUNIT_XML_NAME

    def _resolve_shots(self, resolver: _Resolver) -> None:
        """Resolve the two shot switches ``[README.md:L42-L43]``.

        Two switches rather than one, because the source describes two different triggers:
        screen shots happen "if you enable it" and error shots happen "for your failed test
        cases".
        """
        self.SCREENSHOTS_ENABLED = resolver.flag(
            "SCREENSHOTS_ENABLED", "screenshots.enabled", DEFAULT_SCREENSHOTS_ENABLED
        )
        self.ERROR_SHOTS_ENABLED = resolver.flag(
            "ERROR_SHOTS_ENABLED", "error.shots.enabled", DEFAULT_ERROR_SHOTS_ENABLED
        )

    def _resolve_browser(self, resolver: _Resolver) -> None:
        """Resolve the browser-automation settings ``[README.md:L53]``.

        The implicit wait accepts ``0``, which disables it -- a legitimate choice, since the
        page objects use explicit waits and mixing the two aggressively is what the larger
        explicit budget guards against.
        """
        self.BROWSER = resolver.text("BROWSER", "browser", DEFAULT_BROWSER)
        self.HEADLESS = resolver.flag("HEADLESS", "headless", DEFAULT_HEADLESS)
        self.DRIVER_MANAGER = resolver.text(
            "DRIVER_MANAGER", "driver.manager", DEFAULT_DRIVER_MANAGER
        )
        self.IMPLICIT_WAIT_SECONDS = resolver.whole_number(
            "IMPLICIT_WAIT_SECONDS",
            "implicit.wait.seconds",
            DEFAULT_IMPLICIT_WAIT_SECONDS,
            minimum=0,
        )
        self.EXPLICIT_WAIT_SECONDS = resolver.whole_number(
            "EXPLICIT_WAIT_SECONDS",
            "explicit.wait.seconds",
            DEFAULT_EXPLICIT_WAIT_SECONDS,
            minimum=1,
        )
        self.PAGE_LOAD_TIMEOUT_SECONDS = resolver.whole_number(
            "PAGE_LOAD_TIMEOUT_SECONDS",
            "page.load.timeout.seconds",
            DEFAULT_PAGE_LOAD_TIMEOUT_SECONDS,
            minimum=1,
        )
        # No default exists for either address and none is invented (AAP Rule T6): both
        # committed templates document them empty or commented out.
        self.BASE_URL = resolver.optional_text("BASE_URL", "base.url")
        self.SELENIUM_REMOTE_URL = resolver.optional_text("SELENIUM_REMOTE_URL", None)

    def _resolve_parity_literals(self, resolver: _Resolver) -> None:
        """Resolve the behaviour-parity literal of preserved defect **D5**.

        Resolved through the chain like every other setting so a deployment can adapt it to
        a differently localised environment, but the DEFAULT stays the French assertion of
        ``[README.md:L135]`` -- byte for byte, trailing period included -- because the
        assertion, not the English comment beside it, is the executable truth.
        """
        self.EXPECTED_EMPTY_FIELD_MESSAGE = resolver.text(
            "EXPECTED_EMPTY_FIELD_MESSAGE",
            "expected.empty.field.message",
            DEFAULT_EXPECTED_EMPTY_FIELD_MESSAGE,
        )

    def _resolve_logging(self, resolver: _Resolver) -> None:
        """Resolve the logging settings consumed by ``app/logging_config.py``.

        This module resolves them and stops there: it installs no handler and sets no level,
        because logging configuration belongs to one module and this is not it.
        """
        self.LOG_LEVEL = resolver.text("LOG_LEVEL", "log.level", DEFAULT_LOG_LEVEL)
        self.LOG_FILE = resolver.filesystem_path("LOG_FILE", "log.file", DEFAULT_LOG_FILE)

    # -- introspection --------------------------------------------------------

    def provenance(self) -> Mapping[str, Provenance]:
        """Return which rung supplied each setting, keyed by environment key.

        Returns:
            A read-only mapping in declaration order. This is what makes the five-rung chain
            testable: ``settings.provenance()["TAG_EXPRESSION"]`` says whether the value came
            from an override, the environment, ``.env``, ``configuration.properties`` or the
            hard-coded source default.

        The mapping carries RUNGS, never values, so it cannot leak a credential. It does
        however name every setting -- ``SECRET_KEY`` among them -- and the entry
        ``SECRET_KEY: default`` tells a reader that the committed development placeholder is
        what is in force, which is as useful to an attacker as to an operator. Treat this as
        an INTERNAL diagnostic: log it and assert on it freely, but before putting it in a
        publicly reachable payload either omit it or drop the keys in
        :attr:`Config._SECRET_ATTRIBUTES` from it. :meth:`as_dict` already applies that
        filter to the values.
        """
        return self._provenance

    def provenance_of(self, env_key: str) -> Provenance:
        """Return the rung that supplied one setting.

        Args:
            env_key: Environment key of the setting, for example ``"TAG_EXPRESSION"``.

        Returns:
            The winning :class:`Provenance`.

        Raises:
            KeyError: If *env_key* is not one of this module's settings, which is a
                programming mistake rather than a configuration problem.
        """
        return self._provenance[env_key]

    def env_keys(self) -> tuple[str, ...]:
        """Return every environment key consulted, in declaration order.

        Every key returned here is documented in the committed ``.env.example``; a divergence
        between the two is a bug, and this accessor is what lets a test assert it.
        """
        return tuple(self._provenance)

    def property_keys(self) -> tuple[str, ...]:
        """Return every ``configuration.properties`` key consulted, in declaration order.

        Shorter than :meth:`env_keys` on purpose: the process- and deployment-level settings
        are environment-only, so the committed properties template deliberately carries no
        key for them.
        """
        return self._property_keys

    def artifact_paths(self) -> dict[str, Path]:
        """Return the resolved artifact layout, keyed as ``app/utils/paths.py`` keys it.

        The key names mirror :meth:`app.utils.paths.TargetLayout.as_dict`, so a caller can
        move between the module-level layout and the resolved one without a translation
        table.

        Returns:
            A freshly built mapping the caller owns; mutating it cannot affect this
            configuration.
        """
        return {
            "target_dir": self.TARGET_DIR,
            "cucumber_json_path": self.CUCUMBER_JSON_PATH,
            "cucumber_html_path": self.CUCUMBER_HTML_PATH,
            "rerun_txt_path": self.RERUN_TXT_PATH,
            "pretty_reports_dir": self.PRETTY_REPORTS_DIR,
            "screenshots_dir": self.SCREENSHOTS_DIR,
            "error_shots_dir": self.ERROR_SHOTS_DIR,
            "surefire_reports_dir": self.SUREFIRE_REPORTS_DIR,
            "surefire_junit_xml_path": self.SUREFIRE_JUNIT_XML_PATH,
        }

    def artifact_paths_posix(self) -> dict[str, str]:
        """Return :meth:`artifact_paths` rendered with forward slashes on every platform.

        Report consumers -- and above all the CI publisher's ``fileIncludePattern``
        ``[Jenkins:L15]`` -- are written in terms of forward slashes, so a Windows-flavoured
        ``target\\cucumber.json`` in a JSON payload would invalidate that glob. Rendering
        goes through ``app/utils/paths.py``, which owns the conversion.
        """
        return {key: to_posix(value) for key, value in self.artifact_paths().items()}

    def as_dict(self) -> dict[str, object]:
        """Return every resolved setting, for introspection and diagnostics.

        NO SECRET IS INCLUDED: :attr:`SECRET_KEY` is filtered out, so this mapping is safe to
        log or to serialise. Values are returned in their resolved Python types --
        :class:`pathlib.Path` stays a path -- so a JSON caller should render paths through
        :meth:`artifact_paths_posix`. Every container is a fresh copy, so a consumer that
        mutates the payload cannot reach back into this configuration.

        One caution for a publicly reachable payload: two entries describe the HOST rather
        than the application. :attr:`CONFIGURATION_PROPERTIES_PATH` and :attr:`LOG_FILE` are
        filesystem paths, and the first is absolute, so serving them unfiltered discloses the
        deployment's directory layout. Serve the curated view that
        ``app/api/schemas.py::ConfigResponse`` defines instead of this whole mapping when the
        consumer is untrusted.

        Returns:
            A freshly built, alphabetically ordered mapping of every upper-case setting.
        """
        return {
            name: _owned(getattr(self, name))
            for name in sorted(dir(self))
            if name.isupper() and not name.startswith("_") and name not in self._SECRET_ATTRIBUTES
        }

    def __repr__(self) -> str:
        """Return a diagnostic representation that can never leak the signing key."""
        return (
            f"<{type(self).__name__} config_name={self.CONFIG_NAME!r} "
            f"target_dir={to_posix(self.TARGET_DIR)!r} "
            f"tag_expression={self.TAG_EXPRESSION!r} "
            f"ignore_test_failures={self.IGNORE_TEST_FAILURES!r}>"
        )


# =============================================================================
# The three deployable profiles.
#
# They differ in OPERATIONAL behaviour only -- debug policy, signing-key policy
# and Flask's testing flag. Every preserved parity constant is inherited
# unchanged, so the six thresholds, the sort order, the include pattern, the
# failure tolerance and the tag expression are identical in all three, which is
# what validation criterion V4 asserts "regardless of environment".
# =============================================================================


class DevelopmentConfig(Config):
    """Profile for a working copy: the default when nothing selects otherwise.

    Debug mode may be switched on through ``FLASK_DEBUG``, but it stays OFF unless it is
    asked for -- the committed template pins ``FLASK_DEBUG=0`` because Flask's debugger
    serves an interactive console to anyone who can reach the port. The visible development
    placeholder is accepted as the signing key so a fresh checkout needs no setup at all.
    """

    _PROFILE_NAME: ClassVar[str] = "development"
    _TESTING: ClassVar[bool] = False
    _ALLOW_DEBUG: ClassVar[bool] = True
    _REQUIRE_SECRET_KEY: ClassVar[bool] = False

    CONFIG_NAME: str = "development"


class TestingConfig(Config):
    """Profile for automated tests: Flask's testing mode, and nothing else weakened.

    ``TESTING = True`` makes Flask propagate exceptions instead of converting them into
    ``500`` responses, which is what lets a failing view fail a test loudly. Nothing else is
    relaxed: the preserved parity constants are inherited verbatim, precisely because the
    parity suite asserts them through an application built with this profile.
    """

    # Not a test class, despite the name pytest's default `python_classes = Test*` pattern
    # matches. Every module that imports this name -- `tests/conftest.py` and
    # `tests/unit/test_config.py` among them -- would otherwise draw a
    # `PytestCollectionWarning` on every run ("cannot collect test class 'TestingConfig'
    # because it has a __init__ constructor"), and `pytest.ini` deliberately filters
    # warnings with `default` plus one narrowly targeted ignore so that nothing is swallowed
    # wholesale. Opting out at the source keeps the run's warning output meaningful instead
    # of teaching readers to ignore a line. This is pytest's own documented mechanism and
    # costs no import: it is an ordinary class attribute, invisible to Flask's
    # `from_object`, which copies UPPER_CASE names only.
    __test__ = False

    _PROFILE_NAME: ClassVar[str] = "testing"
    _TESTING: ClassVar[bool] = True
    _ALLOW_DEBUG: ClassVar[bool] = True
    _REQUIRE_SECRET_KEY: ClassVar[bool] = False

    CONFIG_NAME: str = "testing"
    TESTING: bool = True


class ProductionConfig(Config):
    """Profile for a deployment: debug refused outright, signing key required.

    Two rules distinguish it, and both exist because the alternative fails silently rather
    than loudly:

    * ``FLASK_DEBUG`` cannot switch the debugger on. A stray ``FLASK_DEBUG=1`` in a
      deployment environment would otherwise expose an interactive console; it is logged and
      ignored instead.
    * ``SECRET_KEY`` must be supplied. Falling back to the committed placeholder without
      saying so would look healthy while signing sessions with a public value, so start-up
      fails when no key is supplied and warns when the placeholder itself is what was
      supplied (which is exactly what ``docker-compose.yml`` documents as its fallback).
    """

    _PROFILE_NAME: ClassVar[str] = "production"
    _TESTING: ClassVar[bool] = False
    _ALLOW_DEBUG: ClassVar[bool] = False
    _REQUIRE_SECRET_KEY: ClassVar[bool] = True

    CONFIG_NAME: str = "production"


# =============================================================================
# Name -> class resolution.
#
# `create_app(config_name)` receives a profile NAME, so the mapping lives here and
# the factory stays free of configuration knowledge. Resolution is deliberately
# forgiving about spelling and NEVER fatal: an unrecognised name falls back to the
# documented default with a warning, because refusing to start over a typo in an
# orchestration variable would be a worse failure than starting with the
# documented default.
# =============================================================================

CONFIG_MAP: Final[Mapping[str, type[Config]]] = MappingProxyType(
    {
        "development": DevelopmentConfig,
        "testing": TestingConfig,
        "production": ProductionConfig,
    }
)
"""The three deployable profiles, keyed by the names ``APP_CONFIG`` accepts.

Read-only: assigning to a key raises :class:`TypeError`, so no caller can register a fourth
profile behind the application's back.
"""

CONFIG_NAMES: Final[tuple[str, ...]] = tuple(CONFIG_MAP)
"""The canonical profile names, derived from :data:`CONFIG_MAP` rather than restated."""

# Spellings that map onto a canonical name. `default` is offered because orchestration
# templates commonly write it, and the three short forms because they are what people type.
_CONFIG_ALIASES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "default": DEFAULT_CONFIG_NAME,
        "dev": "development",
        "develop": "development",
        "test": "testing",
        "tests": "testing",
        "prod": "production",
    }
)


def resolve_config_name(name: str | None = None) -> str:
    """Return the canonical profile name to build.

    Resolution mirrors the top of the configuration chain: an explicit argument wins, then
    the ``APP_CONFIG`` environment variable, then an ``APP_CONFIG`` entry in ``.env``, then
    :data:`DEFAULT_CONFIG_NAME`. ``configuration.properties`` is deliberately not consulted:
    the profile decides *how* the rest of the configuration is read, and the committed
    properties template records ``APP_CONFIG`` under "DELIBERATELY NOT IN THIS FILE".

    Args:
        name: Requested profile, in any case and with surrounding whitespace, or one of the
            documented short forms (``dev``, ``test``, ``prod``, ``default``). ``None`` or a
            blank string means "consult the environment".

    Returns:
        A key of :data:`CONFIG_MAP`. An unrecognised name yields
        :data:`DEFAULT_CONFIG_NAME` and logs a warning naming the accepted values, so a
        typo in an orchestration variable is visible without being fatal.
    """
    requested = name
    if requested is None or not requested.strip():
        # `os.environ` first, then the parsed `.env` -- the same ordering the settings
        # themselves use, so the profile cannot be selected by a rung that would lose to the
        # environment for everything else.
        requested = os.environ.get(CONFIG_NAME_ENV_VAR) or load_dotenv_values().get(
            CONFIG_NAME_ENV_VAR
        )
    if requested is None or not requested.strip():
        return DEFAULT_CONFIG_NAME

    candidate = requested.strip().casefold()
    candidate = _CONFIG_ALIASES.get(candidate, candidate)
    if candidate in CONFIG_MAP:
        return candidate

    _LOGGER.warning(
        "Unknown configuration profile %r; falling back to %r. Accepted names: %s",
        requested,
        DEFAULT_CONFIG_NAME,
        ", ".join(CONFIG_NAMES),
    )
    return DEFAULT_CONFIG_NAME


def get_config(name: str | None = None) -> type[Config]:
    """Return the configuration CLASS for *name*.

    Useful when the class itself is wanted -- to subclass it, to inspect it, or to hand it
    to ``config.from_object`` when only the hard-coded source defaults are desired.

    A CLASS carries the source defaults and reads nothing: pass :func:`load_config` to
    ``from_object`` instead whenever the environment, ``.env`` and
    ``configuration.properties`` should be honoured, which is what an application normally
    wants.

    Args:
        name: Profile name, resolved by :func:`resolve_config_name`.

    Returns:
        One of :class:`DevelopmentConfig`, :class:`TestingConfig` or
        :class:`ProductionConfig`.
    """
    return CONFIG_MAP[resolve_config_name(name)]


def load_config(name: str | None = None, **overrides: object) -> Config:
    """Build the fully resolved configuration for *name*.

    This is the function the application factory wants::

        app.config.from_object(load_config(config_name))

    Every setting is resolved through the five-rung chain at the moment of the call, so an
    environment change between two calls is honoured -- which is exactly what per-test
    application instances need.

    Args:
        name: Profile name, resolved by :func:`resolve_config_name`.
        **overrides: Rung-one values keyed like their environment keys, for example
            ``TAG_EXPRESSION=""`` to clear the preserved tag filter.

    Returns:
        A resolved configuration instance of the profile's class.

    Raises:
        ConfigurationError: If an override key is not upper case, or if the selected profile
            requires a signing key and none was supplied.
    """
    return get_config(name)(**overrides)


def clear_config_caches() -> None:
    """Discard cached configuration reads so the next resolution observes the files on disk.

    Only ``configuration.properties`` is cached, inside :mod:`app.utils.properties`, keyed by
    resolved path; ``.env`` is re-parsed on every resolution and therefore needs no
    invalidation. Call this after creating, editing or deleting a properties file while the
    process is running -- a test that writes a temporary file per case, or a long-lived
    service asked to reload.
    """
    clear_properties_cache()
    _LOGGER.debug("Cleared cached configuration reads")
